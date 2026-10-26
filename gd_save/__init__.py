"""Grim Dawn（恐怖黎明）角色存档 player.gdc 的读取 / 修改库。

零依赖，只用标准库。``Archive.read`` / ``archive.to_bytes`` 负责整档解密与加密，
支持 block 1/2/3/4/5/6/7/8/10/12/13/14/15/16/17（含 block3/4 的嵌套物品与仓库伪块、
block14 的变长 hotslot）。写入只走 ``edit_file``：无修改往返必须逐字节一致，含无法
解析块的存档拒绝编辑，写入后再解析一次并逐字段比对。
"""
from .archive import Archive, Block, SaveFormatError
from .editor import (Backup, EditReport, RestoreReport, apply_to_bytes, diff_views,
                     edit_file, list_backups, read_save, restore_from_backup,
                     semantic_view)
from .fields import (Change, FACTION_NAMES, HIDDEN_FACTION_INDEXES,
                     REPUTATION_LEVELS, REPUTATION_PRESETS, SIMPLE_FIELDS,
                     apply_edits, available_fields, faction_value_text, field_text,
                     format_clock, format_duration, parse_duration, parse_reputation,
                     reputation_label, set_faction, set_faction_unlocked)

__version__ = "0.1.0"

__all__ = [
    "Archive", "Backup", "Block", "SaveFormatError", "Change", "EditReport",
    "RestoreReport", "SIMPLE_FIELDS",
    "FACTION_NAMES", "HIDDEN_FACTION_INDEXES", "REPUTATION_LEVELS",
    "REPUTATION_PRESETS", "apply_edits", "apply_to_bytes", "available_fields",
    "diff_views", "edit_file", "faction_value_text", "field_text", "format_clock",
    "format_duration", "list_backups", "parse_duration", "parse_reputation",
    "read_save", "reputation_label", "restore_from_backup", "semantic_view",
    "set_faction", "set_faction_unlocked", "__version__",
]
