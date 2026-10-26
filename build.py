"""打包脚本：用 PyInstaller 把恐怖黎明存档修改器冻结成 Windows 可执行文件。

在项目根目录运行（推荐用项目自带虚拟环境里的解释器）：

    python build.py                    # 默认：dist/GrimDawnSaveEditor/ 整个文件夹（启动快）
    python build.py --onefile          # dist/GrimDawnSaveEditor.exe 单个文件（方便发给别人）
    python build.py --out D:\tools     # 换输出目录（默认是项目里的 dist/）
    python build.py --console          # 保留黑色控制台窗口，出问题时报错看得见
    python build.py --icon app.ico     # 指定 exe 图标（必须是 .ico）
    python build.py --no-install       # 不自动装 PyInstaller，缺了就直接报错退出
    python build.py --keep-build       # 保留 build/ 里的中间产物，方便排查

设计说明：

* 本脚本自身只用标准库，所以用一个「干净」的 Python 也能跑起来去装打包工具；
* 程序是纯 tkinter 界面、没有任何数据文件，因此不需要 --add-data，
  PyInstaller 会自动带上 tkinter 的 Tcl/Tk 运行库；
* 产物默认是「文件夹」形态：免去每次启动都解压到临时目录的开销，界面秒开。
  需要单文件时加 --onefile 即可。
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 项目常量 --

# 项目根目录 = 本文件所在目录，保证在任意工作目录下执行都正确
ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "main.py"  # 程序唯一入口
APP_NAME = "GrimDawnSaveEditor"  # 产物名字（exe 与文件夹都叫这个）
BUILD_DIR = ROOT / "build"  # PyInstaller 中间产物
DIST_DIR = ROOT / "dist"  # 最终产物
REQUIREMENTS = ROOT / "requirements.txt"

# 只在开发／测试阶段用到、没必要打进 exe 的模块，排掉可以让体积小一圈
EXCLUDES = (
    "pytest",
    "pip",
    "setuptools",
    "pkg_resources",
    "wheel",
    "pydoc",
    "doctest",
    "lib2to3",
    "distutils",
    "unittest",
)


# ---------------------------------------------------------------- 小工具 ----

def log(message: str) -> None:
    """打印一行进度信息。"""
    print(f"[build] {message}", flush=True)


def fail(message: str) -> int:
    """打印错误并返回失败退出码。"""
    print(f"[build] 错误：{message}", file=sys.stderr, flush=True)
    return 1


def check_tkinter() -> bool:
    """PyInstaller 只能打包「当前解释器里装了的」东西，先确认 tkinter 在。"""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def pyinstaller_available() -> bool:
    """检查当前解释器能否 import PyInstaller。"""
    return importlib.util.find_spec("PyInstaller") is not None


def install_pyinstaller() -> bool:
    """用当前解释器把打包工具装上（优先按 requirements.txt 装）。"""
    if REQUIREMENTS.is_file():
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "pyinstaller>=6.6"]
    log("正在安装打包工具：" + " ".join(cmd))
    try:
        subprocess.check_call(cmd, cwd=str(ROOT))
    except (subprocess.CalledProcessError, OSError) as exc:
        fail(f"安装 PyInstaller 失败（{exc}）。可以手动执行 pip install -r requirements.txt 后重试。")
        return False
    return True


def output_dir(args: argparse.Namespace) -> Path:
    """最终产物放哪里：--out 优先，否则项目里的 dist/。"""
    if not args.out:
        return DIST_DIR
    return Path(args.out).expanduser().resolve()


def clean(out_dir: Path, keep_build: bool) -> None:
    """清掉上一次的产物，避免新旧文件混在一起。"""
    if out_dir.exists():
        log(f"清理旧产物 {out_dir}")
        shutil.rmtree(out_dir, ignore_errors=True)
    if not keep_build and BUILD_DIR.exists():
        log(f"清理中间产物 {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)


def integrity_label(path: Path) -> str:
    """读取 Windows 强制完整性标签，返回 "low" / "medium" / "unknown"。

    为什么要查这个：如果打包产物带着「低完整性 + 禁止向上写」标签（某些受管目录、
    沙箱工作区会给新建文件盖上这种标签），双击后 exe 会以低完整性进程运行，
    而 Windows 不允许低完整性进程写入中完整性的位置 —— 游戏存档所在的「文档」
    目录正是中完整性，表现就是保存时报“没有权限”。
    """
    if sys.platform != "win32":
        return "unknown"
    try:
        result = subprocess.run(["icacls", str(path)], capture_output=True, text=True)
    except OSError:
        return "unknown"
    text = (result.stdout or "").lower()
    if "mandatory label" not in text:
        return "medium"  # 没有显式标签就是中完整性
    return "low" if "low mandatory level" in text else "medium"


def fix_integrity(path: Path) -> bool:
    """尝试把产物的完整性标签提升为中（需要足够的文件权限，失败也不影响打包）。"""
    try:
        subprocess.run(["icacls", str(path), "/setintegritylevel", "Medium", "/T", "/C"],
                       capture_output=True, text=True)
    except OSError:
        return False
    return integrity_label(path) != "low"


def report_integrity(exe: Path, out_dir: Path) -> None:
    """产物带低完整性标签时给出明确提示和补救办法。"""
    if integrity_label(exe) != "low":
        return
    log("注意：产物带有 Windows「低完整性」标签（Low Mandatory Level）")
    log("  这会让 exe 双击后以低完整性进程运行，无法写入游戏存档所在的「文档」目录，")
    log("  表现就是保存时报「没有权限」。原因在输出目录本身（例如受管的沙箱工作区），")
    log("  与 PyInstaller、与本程序都无关。")
    if fix_integrity(out_dir):
        log("  已自动把标签修正为中完整性，可以直接运行了。")
        return
    log("  当前权限不足以就地改标签，请用下面任一办法：")
    log('    1) 把产物复制到普通目录后再运行（复制后标签会自动恢复）：')
    log(f'       Copy-Item "{out_dir}" "$env:USERPROFILE\\Desktop\\" -Recurse')
    log("    2) 直接打包到普通目录：")
    log('       python build.py --out "$env:USERPROFILE\\Desktop\\grimdawn-tool"')


def pyinstaller_command(args: argparse.Namespace, out_dir: Path) -> list[str]:
    """拼出 PyInstaller 的完整命令行。"""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",  # 覆盖输出目录时不交互询问
        "--clean",  # 每次都用全新的缓存，避免旧缓存导致的怪问题
        "--windowed" if not args.console else "--console",
        "--onefile" if args.onefile else "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(out_dir),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),  # 生成的 .spec 放进 build/，不污染项目根目录
        "--log-level",
        "INFO",
    ]
    if args.icon:
        icon = Path(args.icon)
        if not icon.is_absolute():
            icon = ROOT / icon
        if not icon.is_file():
            log(f"警告：找不到图标文件 {icon}，本次打包不使用自定义图标")
        else:
            cmd += ["--icon", str(icon)]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(ENTRY))
    return cmd


def describe_output(onefile: bool, out_dir: Path) -> None:
    """打印产物位置与分发提示，并检查有没有会挡住写存档的完整性标签。"""
    if onefile:
        target = out_dir / f"{APP_NAME}.exe"
    else:
        target = out_dir / APP_NAME / f"{APP_NAME}.exe"
    if not target.is_file():
        log(f"警告：没有找到预期的产物 {target}，请检查上面的 PyInstaller 日志")
        return
    size_mb = target.stat().st_size / (1024 * 1024)
    log(f"打包完成：{target}（主程序 {size_mb:.1f} MB）")
    if onefile:
        log("分发方式：把这一个 exe 拷给别人即可，对方电脑不需要装 Python")
    else:
        folder = out_dir / APP_NAME
        total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        log(f"整个文件夹约 {total / (1024 * 1024):.1f} MB")
        log(f"分发方式：把 {folder} 整个文件夹一起拷贝或用压缩包发给别人，别只拿文件夹里的 exe")
    report_integrity(target, out_dir)


# ---------------------------------------------------------------- 主流程 ----

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description="把恐怖黎明存档修改器打包成 Windows 可执行文件（PyInstaller）",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="打包成单个 exe（默认打包成文件夹，启动更快）",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="保留控制台窗口，方便看到报错（默认是纯窗口程序）",
    )
    parser.add_argument("--icon", metavar="FILE.ICO", help="exe 图标文件（.ico）")
    parser.add_argument(
        "--out",
        metavar="目录",
        help="产物输出目录（默认项目里的 dist/）；指到工作区之外的普通目录可避开受限标签问题",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="缺少 PyInstaller 时不自动安装，直接报错退出",
    )
    parser.add_argument(
        "--keep-build",
        action="store_true",
        help="保留 build/ 中间产物，便于排查打包问题",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log(f"项目目录：{ROOT}")
    log(f"解释器：{sys.executable}")

    if not ENTRY.is_file():
        return fail(f"找不到入口文件 {ENTRY}，请在项目根目录执行本脚本")
    if not check_tkinter():
        return fail("当前 Python 没有 tkinter，界面无法打包。请安装带 tkinter 的官方 Python 后重试")
    log("tkinter 检查通过")

    if not pyinstaller_available():
        if args.no_install:
            return fail("没有安装 PyInstaller。请先执行：pip install -r requirements.txt")
        if not install_pyinstaller():
            return 1
    else:
        log("PyInstaller 已安装")

    out_dir = output_dir(args)
    clean(out_dir, args.keep_build)

    cmd = pyinstaller_command(args, out_dir)
    log("开始打包：" + " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=str(ROOT))
    except OSError as exc:
        return fail(f"无法启动 PyInstaller：{exc}")
    if result.returncode != 0:
        return fail(f"PyInstaller 退出码 {result.returncode}，请看上面的日志定位原因")

    describe_output(args.onefile, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
