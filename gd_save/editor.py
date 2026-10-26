"""高层、带安全检查的编辑流程：读取、校验、修改、写入、再校验。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field

from .archive import Archive, SaveFormatError
from .fields import Change, apply_edits


def semantic_view(archive: Archive) -> dict:
    """本工具所理解的一切，都在这份便于 JSON 序列化、可直接一一比对的视图里。

    视图中的路径（``header.name``、``blocks.block2.iron``）与改动列表和校验器所用的
    路径完全一致。工具没有解析的块用其未改动字节的哈希来表示，因此校验器同时也能证明
    这些原样保留的块确实一字节未动。
    """
    blocks: dict[str, dict] = {}
    for block in archive.blocks:
        key = f"block{block.kind}"
        if key in blocks:
            suffix = 2
            while f"{key}#{suffix}" in blocks:
                suffix += 1
            key = f"{key}#{suffix}"
        if block.parsed:
            blocks[key] = block.body
        else:
            digest = hashlib.sha256(block.raw or b"").hexdigest()
            blocks[key] = {"preserved_sha256": digest}
    return {"header": dict(archive.header), "archiveVersion": archive.archive_version,
            "uuid": archive.uuid, "blocks": blocks}


def diff_views(old, new, prefix: str = "") -> list[tuple[str, object, object]]:
    out: list[tuple[str, object, object]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old:
                out.append((path, "<absent>", new[key]))
            elif key not in new:
                out.append((path, old[key], "<absent>"))
            else:
                out += diff_views(old[key], new[key], path)
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append((prefix + ".length", len(old), len(new)))
        else:
            for index, (left, right) in enumerate(zip(old, new)):
                out += diff_views(left, right, f"{prefix}[{index}]")
    elif old != new:
        out.append((prefix, old, new))
    return out


@dataclass
class EditReport:
    source: str
    target: str
    seed: int
    original_bytes: int
    changed_bytes: int
    changes: list[Change] = field(default_factory=list)
    differences: list[tuple[str, object, object]] = field(default_factory=list)
    unexpected: list[tuple[str, object, object]] = field(default_factory=list)
    backup: str | None = None

    @property
    def ok(self) -> bool:
        return not self.unexpected


def read_save(path: str) -> tuple[bytes, Archive]:
    with open(path, "rb") as handle:
        data = handle.read()
    return data, Archive.read(data)


def check_roundtrip(data: bytes, archive: Archive) -> None:
    """在动手编辑之前，先证明编码器能把输入逐字节地重新生成出来。

    如果这一步失败，说明文件里用了本工具看不懂的东西，重写它可能会损坏角色，因此我们
    拒绝继续。
    """
    rebuilt = archive.to_bytes()
    if rebuilt != data:
        first = next((index for index, (a, b) in enumerate(zip(data, rebuilt)) if a != b),
                     min(len(data), len(rebuilt)))
        raise SaveFormatError(
            f"往返校验失败：未改动的存档重新编码后无法逐字节复现原始字节"
            f"（首个差异位于 {first}）；拒绝修改")


def check_editable(archive: Archive) -> None:
    """拒绝编辑含有我们无法重新编码的块的存档。

    原样保留的块是逐字节拷贝的，只有当进入该块时的加密状态保持不变时才安全。编辑更
    靠前的块恰恰会改变这个状态，所以含有未解析块的存档，只有在工具学会了该块的布局
    之后才能编辑。
    """
    problems = []
    for block in archive.blocks:
        if not block.parsed:
            version = "版本未知" if block.error is None else block.error
            problems.append(f"块 {block.kind}（{version}）")
    if problems:
        raise SaveFormatError(
            "本存档含有本工具无法重新编码的块，编辑可能会损坏角色：" + "；".join(problems)
            + " —— 已拒绝编辑（可用 --force 自行承担风险强制编辑）")


def apply_to_bytes(data: bytes, edits: dict, *, force: bool = False) -> tuple[bytes, EditReport]:
    archive = Archive.read(data)
    if not force:
        check_roundtrip(data, archive)
        check_editable(archive)
    before = semantic_view(archive)
    changes = apply_edits(archive, edits)
    rebuilt = archive.to_bytes()
    # 重新读回我们生成的内容并比对语义，这样编码器里悄无声息的缺陷就无法溜进文件。
    after_view = semantic_view(Archive.read(rebuilt))
    differences = diff_views(before, after_view)
    expected = {change.path for change in changes}
    unexpected = [item for item in differences if item[0] not in expected]
    changed = sum(1 for a, b in zip(data, rebuilt) if a != b)
    report = EditReport(source="<memory>", target="<memory>", seed=archive.seed,
                        original_bytes=len(data), changed_bytes=changed,
                        changes=changes, differences=differences, unexpected=unexpected)
    return rebuilt, report


def edit_file(source: str, target: str, edits: dict, *, force: bool = False,
              backup: bool = True) -> EditReport:
    """修改一个存档并把结果写到 ``target``。

    写入顺序经过设计：备份失败和写入失败都不会损坏角色。

    1. 所有可能因为**取值**失败的事情（自检、改动、重编码、语义校验）先在内存里做完；
    2. 新字节先写进同目录的临时文件，再整档读回解析一次，这样被截断的写入在落盘前
       就会被发现；
    3. 替换目标文件；
    4. 备份放在**最后**，而且只是顺带做的事。备份建不出来（目录被 ACL、同步客户端或
       杀毒软件占住）以前会让整个保存在“已经无法回头”之后失败；现在它只作为警告报
       出来，保存本身照样成功 —— 因为到这一步用户的改动已经安全落盘了。
    """
    data, archive = read_save(source)
    if not force:
        check_roundtrip(data, archive)
        check_editable(archive)
    before = semantic_view(archive)
    changes = apply_edits(archive, edits)
    rebuilt = archive.to_bytes()
    after_view = semantic_view(Archive.read(rebuilt))
    differences = diff_views(before, after_view)
    expected = {change.path for change in changes}
    unexpected = [item for item in differences if item[0] not in expected]

    changed = sum(1 for a, b in zip(data, rebuilt) if a != b)
    report = EditReport(source=source, target=target, seed=archive.seed,
                        original_bytes=len(data), changed_bytes=changed,
                        changes=changes, differences=differences, unexpected=unexpected)

    # 旧字节已经在内存里了，留着它，这样写备份就不必再读一次或重命名目标文件。
    previous = data
    # 只有当目标文件确实会被覆盖时才备份：这既是命令行文档写的行为，也能避免一次
    # 全新的“另存为”在目录里留下一堆没用的备份。
    target_existed = os.path.exists(target)

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.dirname(os.path.abspath(target))
    # 临时文件名保持短而且定长：把目标文件名接在前面，会让本来就很长的存档路径
    # 超过 Windows 的 255 字符文件名上限。
    temp_path = os.path.join(folder, f".gd-save-{os.getpid()}-{stamp}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(rebuilt)
        # 在替换任何东西之前，先证明这个临时文件是一个完整、可解析的存档。
        with open(temp_path, "rb") as handle:
            Archive.read(handle.read())
        os.replace(temp_path, target)
    except OSError:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    if backup and target_existed:
        report.backup = write_backup(target, previous)
    return report


def write_backup(target: str, previous: bytes) -> str | None:
    """把改动前的存档留一份副本；出问题只报告，绝不抛异常。

    备份名为 ``<目标文件>.<时间戳>.bak``（例如 ``player.gdc.20261016-164100.bak``）：
    每次保存都生成一份新的带时间戳备份，不覆盖旧备份，可以回滚到任意一次改动前。
    同一秒内重复保存（或保存后立刻恢复）时追加 ``-1``、``-2`` 序号，同样不覆盖。
    """
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{target}.{stamp}.bak"
    sequence = 1
    while os.path.exists(backup_path):
        backup_path = f"{target}.{stamp}-{sequence}.bak"
        sequence += 1
    try:
        # 直接写，而不是重命名：某些杀毒软件和同步客户端允许新建文件但拒绝在游戏的
        # 存档上做重命名；而且直接写不会像重命名那样留下写了一半的备份。
        with open(backup_path, "wb") as handle:
            handle.write(previous)
    except OSError as exc:
        report = getattr(exc, "filename", None) or backup_path
        sys.stderr.write(
            f"警告：无法创建备份 {report}（{exc.strerror}）；"
            f"存档本身已成功写入\n")
        return None
    return backup_path


_BACKUP_NAME_RE = re.compile(r"^(?P<base>.+)\.(?P<stamp>\d{8}-\d{6})(?:-(?P<seq>\d+))?\.bak$")


@dataclass
class Backup:
    """``<存档>.<时间戳>.bak`` 备份文件。"""

    path: str
    stamp: str          # 文件名里的 YYYYMMDD-HHMMSS
    size: int

    @property
    def label(self) -> str:
        """``20261016-164100`` -> ``2026-10-16 16:41:00   53,619 字节``。"""
        date, _, clock = self.stamp.partition("-")
        return (f"{date[:4]}-{date[4:6]}-{date[6:8]} "
                f"{clock[:2]}:{clock[2:4]}:{clock[4:6]}   {self.size:,} 字节")


def list_backups(target: str) -> list[Backup]:
    """列出 ``target`` 旁边本工具生成的备份，最新的在前。"""
    folder = os.path.dirname(os.path.abspath(target))
    base = os.path.basename(os.path.abspath(target))
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found: list[tuple[str, int, Backup]] = []
    for name in names:
        match = _BACKUP_NAME_RE.match(name)
        if match is None or match.group("base") != base:
            continue
        path = os.path.join(folder, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        stamp = match.group("stamp")
        found.append((stamp, int(match.group("seq") or 0),
                      Backup(path=path, stamp=stamp, size=size)))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in found]


@dataclass
class RestoreReport:
    """一次「从备份恢复」的结果。"""

    target: str
    source_backup: str
    restored_bytes: int
    current_backup: str | None = None
    source_removed: bool = False
    warnings: list[str] = field(default_factory=list)


def restore_from_backup(target: str, backup: str, *, keep_backup: bool = False,
                        backup_current: bool = True) -> RestoreReport:
    """用 ``backup`` 的内容覆盖 ``target``，默认在成功后删掉这份备份。

    流程与 ``edit_file`` 一样保守：先解析备份证明它是完整存档，再把字节写进同目录临时
    文件并读回解析一遍，确认无误才替换目标文件；替换成功后先把当前内容另存为新的
    ``.bak``（所以恢复本身也能反悔），最后才删除用掉的那份备份，其余备份一律不动。

    备份不是完整存档时抛 ``SaveFormatError``，读写失败抛 ``OSError``；这两种情况下目标
    文件都不会被改动。
    """
    with open(backup, "rb") as handle:
        data = handle.read()
    Archive.read(data)

    previous = None
    if os.path.exists(target):
        with open(target, "rb") as handle:
            previous = handle.read()

    report = RestoreReport(target=target, source_backup=backup, restored_bytes=len(data))
    folder = os.path.dirname(os.path.abspath(target))
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    temp_path = os.path.join(folder, f".gd-restore-{os.getpid()}-{stamp}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
        with open(temp_path, "rb") as handle:
            Archive.read(handle.read())
        os.replace(temp_path, target)
    except OSError:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    if backup_current and previous is not None:
        report.current_backup = write_backup(target, previous)
        if report.current_backup is None:
            report.warnings.append("恢复前的当前内容没能备份成 .bak（恢复本身已成功）")

    if not keep_backup:
        try:
            os.remove(backup)
            report.source_removed = True
        except OSError as exc:
            report.warnings.append(f"没能删除用掉的备份（{exc.strerror}）；可以手动删除")
    return report
