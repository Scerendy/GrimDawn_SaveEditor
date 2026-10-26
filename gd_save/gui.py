"""图形界面：恐怖黎明存档修改器（tkinter，随 Python 自带，无需第三方库）。

启动：``python main.py [player.gdc]``。窗口从上到下是工具按钮、存档选择下拉框、
标签页（人物数据 / 阵营势力 / 改动日志）与状态栏。

写入只经过 editor 的 ``edit_file`` / ``apply_to_bytes``，所以安全保护全部生效：
无修改往返必须逐字节一致，含无法解析块的存档拒绝修改，写入后再逐字段比对。
"""
from __future__ import annotations

import os
import sys
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .archive import Archive, SaveFormatError
from .editor import (Backup, apply_to_bytes, check_editable, check_roundtrip, edit_file,
                     list_backups, read_save, restore_from_backup)
from .fields import (FACTION_NAMES, HIDDEN_FACTION_INDEXES, REPUTATION_PRESETS,
                     SIMPLE_FIELDS, available_fields, faction_value_text,
                     field_text, reputation_label)

APP_TITLE = f"恐怖黎明 存档修改器 v{__version__}"

#: 标签与其输入框之间的最小间距（像素）
LABEL_GAP = 12
#: 分组框内左侧留白（像素）
LABEL_PAD = 8

#: 界面统一字体：小一号，控件不占多余地方
FONT = ("Microsoft YaHei UI", 9)

#: Grim Dawn 风格配色：照游戏内面板校准 —— 近黑的暖褐底、做旧古铜边框、亮金标题、
#: 米白数值文字、血红危险强调；属性标签用游戏物品栏那套彩色。
GD_BG = "#0d0a08"        #: 主背景：近黑的暖褐（游戏面板几乎纯黑）
GD_PANEL = "#161109"     #: 分组框 / 面板背景
GD_ENTRY = "#060504"     #: 输入框槽底（近纯黑）
GD_BORDER = "#7a6438"    #: 做旧古铜边框
GD_GOLD = "#d4b06a"      #: 亮金：标题、选中态
GD_TEXT = "#e6ddc8"      #: 米白正文（游戏里的数值文字近乎白）
GD_TEXT_DIM = "#9a8a68"  #: 暗淡文字（提示）
GD_RED = "#c8322a"       #: 血红（危险 / 复仇 / 飞升）
GD_GREEN = "#7a9a4e"     #: 暗绿（通过 / 崇敬）
GD_AMBER = "#c98a3a"     #: 琥珀（未打开存档）
GD_BTN = "#1c1610"       #: 按钮底色
GD_BTN_ACTIVE = "#2e2416"  #: 按钮悬停
GD_BTN_PRESS = "#3d3020"   #: 按钮按下
GD_ACCENT_BG = "#5c1a15"   #: 强调按钮底：暗红（游戏“接受”按钮的观感）
GD_ACCENT_FG = "#e0c078"   #: 强调按钮字：亮金
GD_LOG_BG = "#070605"    #: 日志底色
GD_LOG_FG = "#d8cfba"    #: 日志文字
#: 属性标签的彩色（对应 tags_ui.txt 里的文字颜色）
GD_ATTR_PHYSIQUE = "#d08a4a"  #: 体格 / 力量：橙
GD_ATTR_CUNNING = "#4ab8b8"   #: 灵巧 / 敏捷：青
GD_ATTR_SPIRIT = "#6aa8d8"    #: 精神：蔚蓝
GD_ATTR_HEALTH = "#d87a9a"    #: 生命：桃红
GD_ATTR_ENERGY = "#7ab8a0"    #: 能量：水鸭绿

#: 难度下拉框：显示文本与写入存档的取值按同一顺序对应
DIFFICULTY_CHOICES = ["普通", "精英", "终极"]
DIFFICULTY_TOKENS = ["normal", "elite", "ultimate"]

#: 最高炼狱（竞技场）难度下拉框
SURVIVAL_CHOICES = ["修习者", "挑战者", "角斗士"]
SURVIVAL_TOKENS = ["aspirant", "challenger", "gladiator"]

#: 性别下拉框（存档里 1 = 男，0 = 女）
SEX_CHOICES = ["男", "女"]

#: 属性「－ ＋」按钮的步长：每点一次属性 ±8。
ATTRIBUTE_STEP = 8
#: 属性下限：力量 / 敏捷 / 智力最低 50，减到 50 后减号不再生效。
ATTRIBUTE_MIN = 50


def default_save_dir() -> str:
    """游戏默认的角色存档目录（找不到就退回用户主目录）。"""
    candidates = []
    if os.environ.get("USERPROFILE"):
        candidates.append(os.path.join(os.environ["USERPROFILE"], "Documents", "My Games",
                                       "Grim Dawn", "save", "main"))
    for drive in ("C:", "D:", "E:", "F:"):
        candidates.append(os.path.join(drive, "\\", "Program Files (x86)", "Steam", "userdata"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~")


def find_saves() -> list[str]:
    """在默认存档目录里找出所有 player.gdc（含各角色子目录）。"""
    base = default_save_dir()
    out: list[str] = []
    direct = os.path.join(base, "player.gdc")
    if os.path.isfile(direct):
        out.append(direct)
    try:
        for name in sorted(os.listdir(base)):
            for sub in ("", "_"):
                path = os.path.join(base, name, sub, "player.gdc")
                if os.path.isfile(path):
                    out.append(path)
    except OSError:
        pass
    return out


def archive_field_strings(archive: Archive) -> dict[str, str]:
    """把存档里的可编辑字段换成界面上的初始文本。"""
    out: dict[str, str] = {}
    for option, (kind, key, _parser, _label) in SIMPLE_FIELDS.items():
        block = archive.block(kind)
        if block is not None and block.parsed and key in block.body:
            out[option] = field_text(key, block.body[key])
    return out


class SaveEditorApp(tk.Tk):
    """主窗口：工具按钮 + 存档选择 + 标签页 + 状态栏。"""

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x600")
        self.minsize(900, 520)
        self.configure(background=GD_BG)

        self.path: str | None = None
        self.archive: Archive | None = None
        #: 输入控件：字段名 -> Entry / Combobox / BooleanVar
        self.vars: dict[str, object] = {}
        #: 字段在界面上的初始文本（存档当前值），用于判断是否被改过
        self.loaded: dict[str, str] = {}
        self.last_edits: dict = {}
        self._safe = False
        #: 存档下拉框：显示名 -> 完整路径
        self._save_paths: dict[str, str] = {}

        self._build_style()
        self._build_toolbar()
        self._build_selector()
        self._build_notebook()
        self._build_status()

        self.refresh_save_list()
        if path:
            self.after(80, lambda: self.open_file(path))
        else:
            self.set_status(f"未打开存档。默认目录：{default_save_dir()}")

    # ----------------------------------------------------------------- 外壳
    def _build_style(self) -> None:
        """Grim Dawn 暗黑风格：暗褐底、暗金边框、羊皮纸文字。

        vista 等系统主题的外观由操作系统绘制、拒绝深度定制，所以这里强制切到
        clam 主题，它允许逐项配置颜色、边框与悬停态，才能上这套游戏配色。
        """
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # 全局默认：深色底 + 羊皮纸文字 + 暗金选中
        style.configure(".", background=GD_BG, foreground=GD_TEXT, font=FONT,
                         fieldbackground=GD_ENTRY, bordercolor=GD_BORDER,
                         lightcolor=GD_BORDER, darkcolor=GD_BORDER,
                         troughcolor=GD_ENTRY, insertcolor=GD_TEXT,
                         selectbackground=GD_GOLD, selectforeground=GD_ENTRY)

        style.configure("TFrame", background=GD_BG)
        style.configure("TLabel", background=GD_BG, foreground=GD_TEXT)

        # 分组框：稍浅的面板色 + 暗金细边，标题用金色加粗
        style.configure("TLabelframe", background=GD_PANEL, bordercolor=GD_BORDER,
                         relief="groove")
        style.configure("TLabelframe.Label", background=GD_PANEL, foreground=GD_GOLD,
                         font=(FONT[0], 9, "bold"))

        # 按钮：皮革底 + 暗金边，悬停 / 按下时泛金
        style.configure("TButton", background=GD_BTN, foreground=GD_TEXT,
                         bordercolor=GD_BORDER, focuscolor=GD_BTN, padding=(10, 3),
                         relief="raised")
        style.map("TButton",
                  background=[("active", GD_BTN_ACTIVE), ("pressed", GD_BTN_PRESS)],
                  foreground=[("active", GD_GOLD)],
                  bordercolor=[("active", GD_GOLD)])
        # 强调按钮（保存）：暗红底 + 亮金字，仿游戏里“接受”按钮的观感
        style.configure("Accent.TButton", background=GD_ACCENT_BG,
                         foreground=GD_ACCENT_FG, bordercolor=GD_GOLD,
                         focuscolor=GD_ACCENT_BG, padding=(10, 3), relief="raised")
        style.map("Accent.TButton",
                  background=[("active", "#742420"), ("pressed", "#8a2c26")],
                  foreground=[("active", "#f0d890")])

        # 输入框 / 下拉框：最深的槽底，聚焦时描金边
        style.configure("TEntry", fieldbackground=GD_ENTRY, foreground=GD_TEXT,
                         bordercolor=GD_BORDER, insertcolor=GD_TEXT)
        style.map("TEntry", bordercolor=[("focus", GD_GOLD)])
        style.configure("TCombobox", fieldbackground=GD_ENTRY, foreground=GD_TEXT,
                         background=GD_BTN, arrowcolor=GD_GOLD, bordercolor=GD_BORDER)
        style.map("TCombobox",
                  fieldbackground=[("readonly", GD_ENTRY)],
                  foreground=[("readonly", GD_TEXT)],
                  selectbackground=[("readonly", GD_ENTRY)],
                  selectforeground=[("readonly", GD_TEXT)],
                  bordercolor=[("focus", GD_GOLD)])

        # 下拉弹层是 Tk 的 Listbox，ttk 主题管不到它，只能用 option database 单独上色，
        # 否则它会是系统默认的白底 + 蓝选中。
        for pattern, value in (
                ("*TCombobox*Listbox.background", GD_ENTRY),
                ("*TCombobox*Listbox.foreground", GD_TEXT),
                ("*TCombobox*Listbox.selectBackground", GD_GOLD),
                ("*TCombobox*Listbox.selectForeground", GD_ENTRY),
                ("*TCombobox*Listbox.font", FONT),
                ("*TCombobox*Listbox.borderWidth", 1),
                ("*TCombobox*Listbox.relief", "flat"),
                ("*TCombobox*Listbox.highlightThickness", 1),
                ("*TCombobox*Listbox.highlightBackground", GD_BORDER),
                ("*TCombobox*Listbox.highlightColor", GD_BORDER)):
            self.option_add(pattern, value)

        # 复选框：未选深槽，选中变金色块
        style.configure("TCheckbutton", background=GD_PANEL, foreground=GD_TEXT,
                         indicatorcolor=GD_ENTRY)
        style.map("TCheckbutton",
                  background=[("active", GD_PANEL)],
                  indicatorcolor=[("selected", GD_GOLD), ("active", GD_BORDER)])

        # 标签页：暗色页签，选中页金色文字并与内容区连成一体
        style.configure("TNotebook", background=GD_BG, bordercolor=GD_BORDER,
                         tabmargins=(4, 4, 0, 0))
        style.configure("TNotebook.Tab", background=GD_BTN, foreground=GD_TEXT_DIM,
                         padding=(16, 5), bordercolor=GD_BORDER)
        style.map("TNotebook.Tab",
                  background=[("selected", GD_PANEL), ("active", GD_BTN_ACTIVE)],
                  foreground=[("selected", GD_GOLD), ("active", GD_TEXT)],
                  expand=[("selected", (0, 0, 0, 2))])

        # 滚动条：与背景融为一体，悬停泛金
        style.configure("TScrollbar", background=GD_BTN, troughcolor=GD_BG,
                         bordercolor=GD_BG, arrowcolor=GD_TEXT_DIM, borderwidth=0)
        style.map("TScrollbar",
                  background=[("active", GD_BORDER), ("pressed", GD_BTN_ACTIVE)],
                  arrowcolor=[("active", GD_GOLD)])

        # 语义文字（都用在状态栏，背景与主背景一致）
        style.configure("Hint.TLabel", background=GD_BG, foreground=GD_TEXT_DIM)
        style.configure("Ok.TLabel", background=GD_BG, foreground=GD_GREEN)
        style.configure("Bad.TLabel", background=GD_BG, foreground=GD_RED)
        style.configure("Warn.TLabel", background=GD_BG, foreground=GD_AMBER)

        # 属性标签彩色（用在“属性”分组框里，背景与面板一致）：游戏物品栏里这几项
        # 本来就是彩色的（体格橙 / 灵巧蓝 / 精神青 / 生命粉 / 能量绿）
        style.configure("Physique.TLabel", background=GD_PANEL,
                         foreground=GD_ATTR_PHYSIQUE)
        style.configure("Cunning.TLabel", background=GD_PANEL,
                         foreground=GD_ATTR_CUNNING)
        style.configure("Spirit.TLabel", background=GD_PANEL,
                         foreground=GD_ATTR_SPIRIT)
        style.configure("Health.TLabel", background=GD_PANEL,
                         foreground=GD_ATTR_HEALTH)
        style.configure("Energy.TLabel", background=GD_PANEL,
                         foreground=GD_ATTR_ENERGY)

    def _build_toolbar(self) -> None:
        self.toolbar = ttk.Frame(self, padding=(LABEL_PAD, 4, LABEL_PAD, 2))
        self.toolbar.pack(fill="x")
        buttons = [("打开存档", self.open_file, "TButton"),
                   ("重新加载", self.reload, "TButton"),
                   ("保存人物档", self.save_in_place, "Accent.TButton"),
                   ("另存为", self.save_as, "TButton"),
                   ("恢复存档", self.restore, "TButton"),
                   ("预览改动", self.preview, "TButton"),
                   ("无损自检", self.selftest, "TButton"),
                   ("帮助", self.show_about, "TButton")]
        for text, command, style_name in buttons:
            ttk.Button(self.toolbar, text=text, command=command,
                       style=style_name).pack(side="left", padx=(0, 4))

    def _build_selector(self) -> None:
        row = ttk.Frame(self, padding=(LABEL_PAD, 0, LABEL_PAD, 4))
        row.pack(fill="x")
        self.save_var = tk.StringVar()
        self.save_combo = ttk.Combobox(row, textvariable=self.save_var, state="readonly",
                                       height=12)
        self.save_combo.pack(side="left", fill="x", expand=True)
        self.save_combo.bind("<<ComboboxSelected>>", lambda _e: self.open_selected_save())

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=LABEL_PAD, pady=(0, 2))
        self._build_character_tab()
        self._build_faction_tab()
        self._build_log_tab()

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(LABEL_PAD, 1, LABEL_PAD, 4))
        bar.pack(fill="x", side="bottom")
        self.safety_label = ttk.Label(bar, text="未打开存档", style="Warn.TLabel")
        self.safety_label.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, style="Hint.TLabel").pack(side="right")

    def _tab(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.notebook, padding=LABEL_PAD)
        self.notebook.add(frame, text=title)
        return frame

    # ------------------------------------------------------------- 人物数据页
    def _build_character_tab(self) -> None:
        """人物数据页：三个并排分组 + 底部整行的「成就」分组。

        每个分组都是「第 0 列标签、其后各列控件」的网格：同列标签共用列宽所以左边缘
        对齐，控件也从固定列开始；标签列宽度取该组最长的标签，再加 LABEL_GAP 的右边距。
        """
        frame = self._tab("人物数据")
        for column in (0, 1, 2):
            frame.columnconfigure(column, weight=1)
        frame.rowconfigure(1, weight=1)

        info = ttk.LabelFrame(frame, text="人物数据", padding=(6, 2, 8, 6))
        info.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        attrs = ttk.LabelFrame(frame, text="属性", padding=(6, 2, 8, 6))
        attrs.grid(row=0, column=1, sticky="nsew", padx=(0, 6), pady=(0, 6))
        points = ttk.LabelFrame(frame, text="点数", padding=(6, 2, 8, 6))
        points.grid(row=0, column=2, sticky="nsew", pady=(0, 6))
        achievements = ttk.LabelFrame(frame, text="成就", padding=(6, 2, 8, 6))
        achievements.grid(row=1, column=0, columnspan=3, sticky="nsew")

        self._build_info_group(info)
        self._build_attribute_group(attrs)
        self._build_points_group(points)
        self._build_achievement_group(achievements)

    def _field(self, parent, row: int, label: str, key: str, *,
               column: int = 0, width: int = 12, values: list[str] | None = None,
               widget=None) -> None:
        """放一行“标签 + 输入控件”，并把控件登记到 self.vars。

        ``widget`` 可以直接指定已建好的控件（例如属性页的加减行）；
        否则按 ``values`` 建下拉框，没给就建文本框。
        """
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w",
                                           padx=(LABEL_PAD, LABEL_GAP), pady=1)
        if widget is None:
            if values is None:
                widget = ttk.Entry(parent, width=width)
            else:
                widget = ttk.Combobox(parent, values=values, width=width, state="readonly")
        widget.grid(row=row, column=column + 1, sticky="w", pady=1)
        self.vars[key] = widget

    def _build_info_group(self, box: ttk.LabelFrame) -> None:
        rows = [("人物名称", "name", 14, None),
                ("性别", "male", 6, SEX_CHOICES),
                ("专家模式", "hardcore", 0, None),
                ("死亡次数", "deaths", 8, None),
                ("最高主线难度", "greatest-difficulty", 8, DIFFICULTY_CHOICES),
                ("最高炼狱难度", "survival", 8, SURVIVAL_CHOICES),
                ("经验", "experience", 12, None),
                ("等级", "level", 8, None),
                ("铁币", "iron", 12, None)]
        for row, (label, key, width, values) in enumerate(rows):
            if key == "hardcore":
                # 专家模式用复选框：勾选 = 硬核
                ttk.Label(box, text=label).grid(row=row, column=0, sticky="w",
                                                padx=(LABEL_PAD, LABEL_GAP), pady=1)
                var = tk.BooleanVar(value=False)
                ttk.Checkbutton(box, variable=var).grid(row=row, column=1, sticky="w", pady=1)
                self.vars[key] = var
            else:
                self._field(box, row, label, key, width=width, values=values)

    def _build_attribute_group(self, box: ttk.LabelFrame) -> None:
        """力量 / 敏捷 / 智力带「－」「＋」（联动属性点，见 ``_step``）。

        生命 / 能量没有加减按钮，但数值框放在同一列，所以五个框和标签都对齐。
        """
        rows = [("力量", "physique", "Physique.TLabel"),
                ("敏捷", "cunning", "Cunning.TLabel"),
                ("智力", "spirit", "Spirit.TLabel")]
        for row, (label, key, style_name) in enumerate(rows):
            ttk.Label(box, text=label, style=style_name).grid(
                row=row, column=0, sticky="w",
                padx=(LABEL_PAD, LABEL_GAP), pady=1)
            ttk.Button(box, text="－", width=2,
                       command=lambda k=key: self._step(k, -1)).grid(
                row=row, column=1, sticky="w", padx=(0, 2), pady=1)
            entry = ttk.Entry(box, width=9)
            entry.grid(row=row, column=2, sticky="w", pady=1)
            self.vars[key] = entry
            ttk.Button(box, text="＋", width=2,
                       command=lambda k=key: self._step(k, +1)).grid(
                row=row, column=3, sticky="w", padx=(2, 0), pady=1)
        # 生命 / 能量没有加减按钮，但数值框与上面的数值框放在同一列，
        # 所以五个框的左边缘对齐，标签也照旧与其它标签对齐
        for offset, (label, key, style_name) in enumerate(
                (("生命", "health", "Health.TLabel"),
                 ("能量", "energy", "Energy.TLabel")), start=3):
            ttk.Label(box, text=label, style=style_name).grid(
                row=offset, column=0, sticky="w",
                padx=(LABEL_PAD, LABEL_GAP), pady=1)
            entry = ttk.Entry(box, width=9)
            entry.grid(row=offset, column=2, sticky="w", pady=1)
            self.vars[key] = entry

    def _build_points_group(self, box: ttk.LabelFrame) -> None:
        rows = [("属性点", "attribute-points", 9),
                ("技能点", "skill-points", 9),
                ("虔诚点", "devotion-points", 9),
                ("累计虔诚点", "total-devotion-points", 9),
                ("贡品", "tributes", 9),
                ("回收技能点", "skill-points-reclaimed", 9),
                ("回收虔诚点", "devotion-points-reclaimed", 9)]
        for row, (label, key, width) in enumerate(rows):
            self._field(box, row, label, key, width=width)

    def _build_achievement_group(self, box: ttk.LabelFrame) -> None:
        """成就分组：三列「标签 + 输入框」，第 2 / 5 列吸收剩余宽度让三列间距均匀。"""
        columns = [
            [("kills", "击杀怪物"),
             ("champion-kills", "击杀精英"),
             ("hero-kills", "击杀英雄"),
             ("boss-kills-1", "击杀复仇（普通）"),
             ("boss-kills-2", "击杀复仇（精英）"),
             ("boss-kills-3", "击杀复仇（终极）"),
             ("champion-kills-ascendant", "击杀飞升精英")],
            [("max-damage-inflicted", "造成的最大伤害"),
             ("max-damage-received", "受到的最大伤害"),
             ("playtime", "游戏时长"),
             ("items-crafted", "制作物品"),
             ("relics-crafted", "制作遗物"),
             ("transcendent-relics-crafted", "制作卓越遗物"),
             ("mythical-relics-crafted", "制作神话遗物")],
            [("max-survival-score", "竞技场最高分"),
             ("survival-defense-built", "竞技场建造次数"),
             ("survival-powerups", "竞技场强化次数"),
             ("hits-inflicted", "命中次数"),
             ("hits-received", "被命中次数"),
             ("crits-inflicted", "暴击次数"),
             ("crits-received", "被暴击次数")],
        ]
        box.columnconfigure(2, weight=1)
        box.columnconfigure(5, weight=1)
        for index, entries in enumerate(columns):
            column = index * 3
            for row, (key, label) in enumerate(entries):
                ttk.Label(box, text=label).grid(row=row, column=column, sticky="w",
                                                padx=(LABEL_PAD if index == 0 else 0,
                                                      LABEL_GAP), pady=1)
                entry = ttk.Entry(box, width=9)
                entry.grid(row=row, column=column + 1, sticky="w", pady=1)
                self.vars[key] = entry

    # -------------------------------------------------------------- 阵营势力页
    def _build_faction_tab(self) -> None:
        """阵营势力页：每个阵营一行「名称 + 状态下拉 + 数值框 + 解锁勾选」。

        下拉框只是把档位数值填进数值框的快捷方式，数值框才是唯一写入来源，
        所以两个控件不会各写各的。可见槽位分两列摆放，一页装得下，不需要滚动条。
        """
        frame = self._tab("阵营势力")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        #: 下标 -> (数值框, 状态下拉, 解锁勾选变量)
        self._faction_rows: dict[int, tuple] = {}
        # 游戏面板从不显示的槽位（fields.HIDDEN_FACTION_INDEXES）不建行；
        # 要临时查看全部槽位，把下面这行换成 range(len(FACTION_NAMES))。
        visible = [index for index in range(len(FACTION_NAMES))
                   if index not in HIDDEN_FACTION_INDEXES]
        half = (len(visible) + 1) // 2
        spans = (visible[:half], visible[half:])
        for column, indices in enumerate(spans):
            box = ttk.LabelFrame(frame, text="阵营" if column == 0 else "阵营（续）",
                                 padding=(6, 2, 8, 6))
            box.grid(row=0, column=column, sticky="nsew",
                     padx=(0, 6) if column == 0 else (0, 0))
            for row, index in enumerate(indices):
                self._build_faction_row(box, row, index)

        note = ttk.Label(
            frame,
            text="「状态」只是快捷方式：选中即把对应数值填进数值框，真正写入存档的是数值框；"
                 "存档里下标 29-46 是预留槽位、另有几个游戏不显示的隐藏槽位，本工具都不展示也不修改。",
            style="Hint.TLabel")
        note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_faction_row(self, box, row: int, index: int) -> None:
        ttk.Label(box, text=FACTION_NAMES[index]).grid(
            row=row, column=0, sticky="w", padx=(LABEL_PAD, LABEL_GAP), pady=1)
        levels = list(REPUTATION_PRESETS)
        level = ttk.Combobox(box, values=levels, width=6, state="readonly")
        level.grid(row=row, column=1, sticky="w", pady=1)
        entry = ttk.Entry(box, width=8)
        entry.grid(row=row, column=2, sticky="w", padx=(2, 4), pady=1)
        var = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="解锁", variable=var).grid(
            row=row, column=3, sticky="w", pady=1)
        level.bind("<<ComboboxSelected>>", lambda _e, k=index: self._apply_faction_level(k))
        self.vars[f"faction-{index}"] = entry
        self.vars[f"faction-{index}-level"] = level
        self.vars[f"faction-{index}-unlocked"] = var
        self._faction_rows[index] = (entry, level, var)

    def _apply_faction_level(self, index: int) -> None:
        """状态下拉选中某档：把该档的数值填进同一行的数值框。"""
        row = self._faction_rows.get(index)
        if row is None:
            return
        value_box, level_box, _var = row
        name = level_box.get()
        if name in REPUTATION_PRESETS and str(value_box.cget("state")) != "disabled":
            value_box.delete(0, "end")
            value_box.insert(0, faction_value_text(REPUTATION_PRESETS[name]))

    def _populate_factions(self, archive: Archive) -> None:
        """把 block13 里的阵营数据填进阵营页；这个存档没有的条目整行置灰。"""
        block = archive.block(13)
        records: dict[int, dict] = {}
        if block is not None and block.parsed:
            for record in block.body["factions"]:
                records[record.get("index", 0) - 1] = record
        for index, (value_box, level_box, unlocked_var) in self._faction_rows.items():
            record = records.get(index)
            if record is None:
                value_box.delete(0, "end")
                value_box.insert(0, "此存档没有该阵营")
                value_box.configure(state="disabled")
                level_box.set("")
                unlocked_var.set(False)
                self.loaded[f"faction-{index}"] = "此存档没有该阵营"
                self.loaded[f"faction-{index}-unlocked"] = "0"
                continue
            if str(value_box.cget("state")) == "disabled":
                value_box.configure(state="normal")
            text = faction_value_text(record["value"])
            value_box.delete(0, "end")
            value_box.insert(0, text)
            level_box.set(reputation_label(record["value"]))
            unlocked_var.set(bool(record["unlocked"]))
            self.loaded[f"faction-{index}"] = text
            self.loaded[f"faction-{index}-unlocked"] = "1" if record["unlocked"] else "0"

    # ---------------------------------------------------------------- 日志页
    def _build_log_tab(self) -> None:
        frame = self._tab("改动日志")
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True)
        self.log_text = tk.Text(box, wrap="word", relief="solid", borderwidth=1,
                                font=("Consolas", 9),
                                background=GD_LOG_BG, foreground=GD_LOG_FG,
                                insertbackground=GD_GOLD,
                                selectbackground=GD_BORDER, selectforeground=GD_TEXT)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------ 控件小工具
    def _set_field(self, key: str, value) -> None:
        """把值写进复选框 / 下拉框 / 输入框（三者的 set 语义各不相同）。"""
        widget = self.vars.get(key)
        if widget is None:
            return
        if isinstance(widget, tk.Variable):
            widget.set(bool(value))
        elif isinstance(widget, ttk.Combobox):
            values = list(widget.cget("values"))
            widget.set(value if value in values else "")
        else:
            widget.delete(0, "end")
            widget.insert(0, value)

    def _points_entry(self):
        """“属性点”输入框；这个存档里没有该字段（被置灰）时返回 None。"""
        widget = self.vars.get("attribute-points")
        if isinstance(widget, ttk.Entry) and str(widget.cget("state")) != "disabled":
            return widget
        return None

    @staticmethod
    def _entry_int(entry) -> int:
        """输入框里的整数；空着或不是数字一律按 0 算。"""
        try:
            return int(str(entry.get()).replace(",", "") or 0)
        except ValueError:
            return 0

    @staticmethod
    def _set_entry_int(entry, value: int) -> None:
        entry.delete(0, "end")
        entry.insert(0, str(max(0, value)))

    def _step(self, key: str, delta: int) -> None:
        """属性框旁的加减按钮：属性 ±8，同时属性点 ∓1。

        与游戏规则一致：＋ 花 1 个属性点，点数不足时不生效；－ 退 1 点，属性到
        ATTRIBUTE_MIN（50）后不生效。直接输入数字不受这些限制；老存档没有属性点
        字段时只改属性本身。
        """
        widget = self.vars.get(key)
        if widget is None or isinstance(widget, tk.Variable):
            return
        try:
            value = float(str(widget.get()).replace(",", "") or 0)
        except ValueError:
            return
        points = self._points_entry()
        if delta > 0:
            if points is not None and self._entry_int(points) < 1:
                self.set_status("属性点不足：请先在“点数”分组里增加属性点，再用 ＋ 加属性")
                self.bell()
                return
            new_value = value + ATTRIBUTE_STEP
        else:
            if value <= ATTRIBUTE_MIN:
                return              # 已经在下限：减号不再生效
            new_value = max(float(ATTRIBUTE_MIN), value - ATTRIBUTE_STEP)
        # 属性一律按整数显示/写回（游戏面板就是整数）
        widget.delete(0, "end")
        widget.insert(0, str(int(new_value)))
        if points is not None:
            # ＋ 花 1 点，－ 退 1 点；_set_entry_int 里钳在 0，不会低于 0
            self._set_entry_int(points, self._entry_int(points) - (1 if delta > 0 else -1))

    # ------------------------------------------------------------ 状态与日志
    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def log(self, text: str = "") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def show_error(self, title: str, exc: BaseException) -> None:
        messagebox.showerror(title, str(exc))
        self.log(f"[错误] {title}: {exc}")
        self.set_status(f"{title}失败：{exc}")

    # -------------------------------------------------------------- 打开存档
    def refresh_save_list(self) -> None:
        """扫描默认目录填充顶部下拉框。

        当前文件即使不在默认目录里（手动选的、或另存出来的副本）也要列进去并保持选中，
        这样下拉框始终反映“正在改哪一个”。
        """
        saves = find_saves()
        if self.path and self.path not in saves:
            saves = [self.path] + saves
        self._save_paths.clear()
        labels = []
        for path in saves:
            base = os.path.basename(os.path.dirname(path)) or os.path.basename(path)
            # 保证任何情况下标签唯一，避免同名时后者退化成整条路径。
            label = base
            while label in self._save_paths:
                label = "· " + label
            self._save_paths[label] = path
            labels.append(label)
        self.save_combo.configure(values=labels)
        if self.path:
            for label, path in self._save_paths.items():
                if path == self.path:
                    self.save_var.set(label)
                    break

    def open_selected_save(self) -> None:
        path = self._save_paths.get(self.save_var.get())
        if path:
            self.open_file(path)

    def open_file(self, path: str | None = None) -> None:
        if path is None:
            path = filedialog.askopenfilename(
                title="选择恐怖黎明角色存档 (player.gdc)",
                initialdir=os.path.dirname(self.path) if self.path else default_save_dir(),
                filetypes=[("Grim Dawn 存档", "*.gdc"), ("所有文件", "*.*")])
            if not path:
                return
        if isinstance(path, str) and path.startswith("{"):
            path = path.strip("{}")
        source = os.path.abspath(path)
        try:
            data, archive = read_save(source)
        except (OSError, SaveFormatError) as exc:
            self.show_error("打开存档", exc)
            return

        # 直接在读取的文件上编辑；保存前会自动在同目录生成带时间戳的备份。
        self.path, self.archive = source, archive
        self.clear_log()
        self.log(f"打开 {source}（{len(data):,} 字节，加密种子 0x{archive.seed:08x}）")
        self.log("保存时会直接写这个文件，写入前先在同目录生成带时间戳的备份。")
        self.log("解析到的块：" + ", ".join(
            f"block{b.kind}{'' if b.parsed else '(未解析)'}" for b in archive.blocks))

        self._safe = False
        try:
            check_roundtrip(data, archive)
            check_editable(archive)
        except SaveFormatError as exc:
            self.safety_label.configure(text="自检未通过，只读：" + str(exc)[:60],
                                        style="Bad.TLabel")
            self.log(f"[警告] {exc}")
        else:
            self._safe = True
            self.safety_label.configure(text="自检通过：可以安全修改", style="Ok.TLabel")

        self._populate(archive)
        self.refresh_save_list()
        self.set_status(f"已打开 {os.path.basename(source)}"
                        + ("，可以安全修改" if self._safe else "，禁止修改"))

    def open_default(self) -> None:
        saves = find_saves()
        if saves:
            self.open_file(saves[0])
        else:
            messagebox.showinfo("未找到存档",
                                f"默认目录里没有 player.gdc：\n{default_save_dir()}\n\n"
                                "请用“打开存档”手动选择。")

    def reload(self) -> None:
        if self.path:
            self.open_file(self.path)
        else:
            self.open_default()

    # -------------------------------------------------------------- 填充界面
    def _populate(self, archive: Archive) -> None:
        header = archive.header
        # 打开（或重新载入）时把“存档里的原值”快照下来：判断哪个字段被改过，
        # 一律以它为基准，所以点开界面什么都不动就保存，不会写入任何东西。
        self.loaded = archive_field_strings(archive)
        self.last_edits = {}

        self._set_field("name", str(header.get("name", "")))
        self._set_field("level", str(header.get("level", "")))
        self._set_field("hardcore", bool(header.get("hardcore")))
        # 头部 male 字节：1 是男，0 是女
        self._set_field("male", "男" if header.get("male") else "女")
        self.loaded["name"] = str(header.get("name", ""))
        self.loaded["level"] = str(header.get("level", ""))
        self.loaded["male"] = "男" if header.get("male") else "女"
        self.loaded["hardcore"] = "1" if header.get("hardcore") else "0"

        block1 = archive.block(1)
        for key, body_key, choices in (("greatest-difficulty", "greatestDifficulty",
                                        DIFFICULTY_CHOICES),
                                       ("survival", "greatestSurvival", SURVIVAL_CHOICES)):
            if block1 is not None and block1.parsed and body_key in block1.body:
                index = int(block1.body[body_key])
                text = choices[index] if 0 <= index < len(choices) else ""
                self._set_field(key, text)
                self.loaded[key] = text
            else:
                self.vars[key].set("")

        self._disable_unavailable_fields(archive)
        # 把存档里每个可编辑字段填进对应输入框；只处理文本框与下拉框，
        # 复选框（专家模式）与性别已在上面按各自语义赋过值，这里不能覆盖
        for key, text in self.loaded.items():
            widget = self.vars.get(key)
            if isinstance(widget, (ttk.Entry, ttk.Combobox)):
                self._set_field(key, text)

        block2 = archive.block(2)
        if block2 and block2.parsed and header.get("level") != block2.body.get("level"):
            self.log(f"[提示] 头部等级 {header.get('level')} 与存档内等级 "
                     f"{block2.body.get('level')} 不一致；改等级会同时写入两处。")

        # 阵营势力页单独填充：它按 block13 的下标逐行填值并记基线
        self._populate_factions(archive)

    def _disable_unavailable_fields(self, archive: Archive) -> None:
        """把“这个存档里根本没有该字段”的输入框置灰并标注缺失。

        保持可编辑等于承诺一次做不到的写入，所以老存档（例如 block16 v11 之前没有
        v12 专属计数器）一律禁用。
        """
        present = available_fields(archive)
        for option in SIMPLE_FIELDS:
            widget = self.vars.get(option)
            if not isinstance(widget, ttk.Entry) or isinstance(widget, ttk.Combobox):
                continue
            if option in present:
                if str(widget.cget("state")) == "disabled":
                    widget.configure(state="normal")
                continue
            widget.delete(0, "end")
            widget.insert(0, "此存档没有该字段")
            widget.configure(state="disabled")
            self.loaded[option] = "此存档没有该字段"

    # ------------------------------------------------------------ 收集修改
    def collect_edits(self) -> dict:
        """把界面上的输入翻译成 ``apply_edits`` 认识的改动字典。

        只有与存档当前值不同、并且非空的字段才会进返回值，所以
        “点开界面什么都不动就保存”不会写坏任何东西。
        """
        edits: dict[str, object] = {}
        self.last_edits = {}
        if self.archive is None:
            return edits

        name = self.vars["name"].get().strip()
        if name and name != str(self.archive.header.get("name", "")):
            edits["name"] = name

        raw_level = self.vars["level"].get().strip()
        if raw_level and raw_level != str(self.archive.header.get("level", "")):
            edits["level"] = raw_level

        # 专家模式（硬核）：复选框勾选 = 硬核
        want_hardcore = 1 if self.vars["hardcore"].get() else 0
        if want_hardcore != int(self.archive.header.get("hardcore", 0) or 0):
            edits["hardcore"] = str(want_hardcore)

        # 性别：1 = 男，0 = 女
        want_male = 1 if self.vars["male"].get() == "男" else 0
        if want_male != int(self.archive.header.get("male", 1) or 0):
            edits["male"] = str(want_male)

        # 两条“最高难度”记录：下拉框的序号就等于存档里的字节
        block1 = self.archive.block(1)
        if block1 is not None and block1.parsed:
            for key, tokens, body_key in (("greatest-difficulty", DIFFICULTY_TOKENS,
                                           "greatestDifficulty"),
                                          ("survival", SURVIVAL_TOKENS, "greatestSurvival")):
                widget = self.vars[key]
                if str(widget.cget("state")) == "disabled":
                    continue
                text = widget.get()
                if text and text != self._baseline(key):
                    edits[key] = tokens[list(widget.cget("values")).index(text)]

        # 文本输入框：所有已登记字段（游戏时长也在内，接受 80h 这类写法）
        for option in SIMPLE_FIELDS:
            widget = self.vars.get(option)
            if not isinstance(widget, ttk.Entry) or isinstance(widget, ttk.Combobox):
                continue
            if str(widget.cget("state")) == "disabled":
                continue          # 这个存档没有该字段
            text = widget.get().strip()
            if not text or text == self._baseline(option):
                continue
            edits[option] = text

        # 阵营势力页：数值框与解锁勾选；整行置灰（此存档没有该阵营）的行跳过
        for index, (value_box, _level_box, unlocked_var) in self._faction_rows.items():
            if str(value_box.cget("state")) == "disabled":
                continue
            text = value_box.get().strip()
            if text and text != self._baseline(f"faction-{index}"):
                edits[f"faction-{index}"] = text
            want_unlocked = "1" if unlocked_var.get() else "0"
            if want_unlocked != self._baseline(f"faction-{index}-unlocked"):
                edits[f"faction-{index}-unlocked"] = want_unlocked

        self.last_edits = {key: value for key, value in edits.items()
                           if isinstance(value, str)}
        return edits

    def _baseline(self, option: str) -> str | None:
        """字段在界面上的初始文本（即存档当前值），用于判断有没有被改过。"""
        return self.loaded.get(option)

    # ------------------------------------------------------------------ 操作
    def _require_save(self) -> bool:
        if self.archive is None or self.path is None:
            messagebox.showinfo("还没有存档", "请先打开一个 player.gdc 存档。")
            return False
        return True

    def preview(self) -> None:
        if not self._require_save():
            return
        assert self.path is not None
        edits = self.collect_edits()
        if not edits:
            self.log("预览：没有任何改动。")
            self.set_status("预览：没有改动")
            messagebox.showinfo("没有改动", "界面上的值和存档里的一样，没有需要修改的内容。")
            return
        try:
            data, _archive = read_save(self.path)
            _rebuilt, report = apply_to_bytes(data, edits, force=not self._safe)
        except (SaveFormatError, ValueError) as exc:
            self.show_error("预览失败", exc)
            return

        self.log("")
        self.log(f"=== 预览（源文件 {self.path}，未写入任何文件）===")
        self._log_requested_edits()
        for change in report.changes:
            self.log(f"  · {change}")
        self.log(f"共 {len(report.changes)} 项改动，影响 {report.changed_bytes} 字节"
                 f"（占 {len(data)} 字节的 "
                 f"{report.changed_bytes / max(len(data), 1) * 100:.2f}%）")
        if report.unexpected:
            self.log("  [注意] 出现未预期的语义差异（可能是编码缺陷），不建议写入：")
            for path, old, new in report.unexpected:
                self.log(f"     {path}: {old} -> {new}")
            self.set_status("预览发现问题：存在未预期的差异")
        else:
            self.log("  [OK] 除上面列出的字段外，其余数据完全未变。")
            self.set_status(f"预览完成：{len(report.changes)} 项改动，可以保存")
        self.notebook.select(self.notebook.tabs()[-1])

    def save_in_place(self) -> None:
        if not self._require_save():
            return
        assert self.path is not None
        if not self._safe:
            messagebox.showerror("禁止修改", "自检未通过：这个存档含有本工具无法重写的块，"
                                            "出于安全考虑不允许写入。")
            return
        target = self.path
        if not messagebox.askyesno(
                "确认写入",
                f"将修改写回：\n{target}\n\n"
                "写入前会自动把当前内容备份为带时间戳的 .bak 文件（同目录），\n"
                "例如 player.gdc.20261016-164100.bak，不会覆盖旧备份。\n\n"
                "继续写入？"):
            return
        self._write(target)

    def save_as(self) -> None:
        if not self._require_save():
            return
        assert self.path is not None
        if not self._safe:
            messagebox.showerror("禁止修改", "自检未通过：这个存档含有本工具无法重写的块，"
                                            "出于安全考虑不允许写入。")
            return
        root, ext = os.path.splitext(self.path)
        target = filedialog.asksaveasfilename(
            title="另存为", defaultextension=ext or ".gdc",
            initialfile=os.path.basename(f"{root}.edited{ext or '.gdc'}"),
            initialdir=os.path.dirname(self.path) or default_save_dir(),
            filetypes=[("Grim Dawn 存档", "*.gdc"), ("所有文件", "*.*")])
        if not target:
            return
        self._write(target)

    def _write(self, target: str) -> None:
        assert self.path is not None
        edits = self.collect_edits()
        if not edits:
            messagebox.showinfo("没有改动", "界面上的值和存档里的一样，没有需要写入的内容。")
            return
        try:
            report = edit_file(self.path, target, edits, force=not self._safe, backup=True)
        except (SaveFormatError, ValueError, OSError) as exc:
            self.show_error("保存失败", exc)
            self.log("[提示] 写入失败时不会留下半成品文件，原文件也没有被改动。")
            if isinstance(exc, PermissionError):
                self.log("[排查建议] 这是 Windows 拒绝了写入权限，常见原因：")
                self.log("  1. 游戏还在运行，占着这个 player.gdc —— 完全退出游戏再试；")
                self.log("  2. Steam 云同步 / OneDrive 正在同步这个目录 —— 暂停同步再试；")
                self.log("  3. 杀毒软件的“文件夹限制访问”拦住了这个目录 —— 在 Windows "
                         "安全中心放行 python.exe；")
                self.log("  4. 改不动就先用“另存为”存到桌面，再手动复制回去。")
            return

        self.log("")
        self.log(f"=== 已写入 {target} ===")
        self._log_requested_edits()
        if report.backup:
            self.log(f"  原文件备份：{report.backup}")
        else:
            self.log("  [注意] 备份文件没能创建，但这次的修改已经成功写入。")
        for change in report.changes:
            self.log(f"  · {change}")
        self.log(f"  改动 {len(report.changes)} 项，影响 {report.changed_bytes} 字节")
        if report.unexpected:
            self.log("  [注意] 写入后校验发现未预期的语义差异：")
            for path, old, new in report.unexpected:
                self.log(f"     {path}: {old} -> {new}")
            messagebox.showwarning(
                "校验未通过",
                "写入后发现未预期的差异，请不要使用这个文件：\n"
                + "\n".join(f"{p}: {o} -> {n}" for p, o, n in report.unexpected[:10]))
            self.set_status("写入完成，但校验未通过")
        else:
            self.log("  [OK] 写入后重新解析并逐字段比对：除改动清单外没有任何变化。")
            self.set_status(f"已写入 {os.path.basename(target)}，校验通过")
        self.notebook.select(self.notebook.tabs()[-1])

        if messagebox.askyesno("写入完成",
                               f"已写出：\n{target}\n\n改动 {len(report.changes)} 项。"
                               "现在重新载入这个文件以刷新界面吗？"):
            self.open_file(target)
        else:
            self.path = target

    # ------------------------------------------------------------ 从备份恢复
    def restore(self) -> None:
        """挑一份时间戳备份覆盖当前存档；用掉的那份随后删除，其余保留。"""
        if not self._require_save():
            return
        assert self.path is not None
        backups = list_backups(self.path)
        if not backups:
            messagebox.showinfo(
                "没有可用的备份",
                f"这个存档旁边还没有备份文件：\n{self.path}\n\n"
                "每次保存都会自动生成一份 <文件名>.<时间戳>.bak，可以从中恢复。")
            return
        chosen = self._choose_backup(backups)
        if chosen is not None:
            self._restore_backup(chosen)

    def _choose_backup(self, backups: list[Backup]) -> Backup | None:
        """弹一个暗色小窗让用户挑备份；取消返回 ``None``。"""
        dialog = tk.Toplevel(self)
        dialog.title("从备份恢复")
        dialog.configure(background=GD_BG)
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text=f"用哪一份备份覆盖：\n{self.path or ''}", style="Hint.TLabel",
                  justify="left").pack(anchor="w", padx=LABEL_PAD, pady=(LABEL_PAD, 4))
        listbox = tk.Listbox(dialog, height=min(len(backups), 10), width=56,
                             background=GD_ENTRY, foreground=GD_TEXT,
                             selectbackground=GD_GOLD, selectforeground=GD_ENTRY,
                             font=("Consolas", 9), relief="flat", borderwidth=1,
                             highlightthickness=1, highlightbackground=GD_BORDER,
                             highlightcolor=GD_GOLD, activestyle="none")
        for item in backups:
            listbox.insert("end", item.label)
        listbox.selection_set(0)
        holder = ttk.Frame(dialog)
        holder.pack(fill="both", expand=True, padx=LABEL_PAD)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        ttk.Label(dialog,
                  text="恢复会覆盖当前文件；用掉的这份备份随后删除（其他备份保留），"
                       "恢复前的当前内容会另存为一份新的 .bak。",
                  style="Hint.TLabel", justify="left", wraplength=430).pack(
            anchor="w", padx=LABEL_PAD, pady=(4, 6))

        picked: list[Backup | None] = [None]

        def accept(_event=None) -> None:
            selection = listbox.curselection()
            if selection:
                picked[0] = backups[selection[0]]
                dialog.destroy()

        bar = ttk.Frame(dialog)
        bar.pack(fill="x", padx=LABEL_PAD, pady=(0, LABEL_PAD))
        ttk.Button(bar, text="恢复", command=accept,
                   style="Accent.TButton").pack(side="left")
        ttk.Button(bar, text="取消", command=dialog.destroy).pack(side="left", padx=(4, 0))
        listbox.bind("<Double-Button-1>", accept)
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

        dialog.update_idletasks()
        left = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        top = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(left, 0)}+{max(top, 0)}")
        dialog.grab_set()
        dialog.wait_window()
        return picked[0]

    def _restore_backup(self, chosen: Backup) -> None:
        """确认后用 ``chosen`` 覆盖当前存档，再把界面刷成恢复后的样子。"""
        assert self.path is not None
        target = self.path
        if not messagebox.askyesno(
                "确认恢复",
                f"用这份备份覆盖当前存档？\n\n备份：{chosen.path}\n目标：{target}\n\n"
                "用掉的这份备份随后会被删除（其他备份保留），\n"
                "恢复前的当前内容会先另存为一份新的 .bak。"):
            return
        try:
            report = restore_from_backup(target, chosen.path)
        except (OSError, SaveFormatError) as exc:
            self.show_error("恢复失败", exc)
            return

        self.open_file(target)
        self.log("")
        self.log(f"=== 已从备份恢复 {os.path.basename(target)} ===")
        self.log(f"  使用的备份：{report.source_backup}（{report.restored_bytes:,} 字节）")
        if report.current_backup:
            self.log(f"  恢复前的当前内容已备份：{report.current_backup}")
        if report.source_removed:
            self.log("  这份备份已删除，其他备份保留。")
        for warning in report.warnings:
            self.log(f"  [注意] {warning}")
        self.set_status("已从备份恢复，可以安全修改" if self._safe
                        else "已从备份恢复，但自检未通过（只读）")

    def selftest(self) -> None:
        if not self._require_save():
            return
        assert self.path is not None
        try:
            data, archive = read_save(self.path)
            rebuilt = archive.to_bytes()
            check_editable(archive)
        except (OSError, SaveFormatError) as exc:
            self.show_error("自检失败", exc)
            self.safety_label.configure(text="自检失败", style="Bad.TLabel")
            self._safe = False
            return
        parsed = [b.kind for b in archive.blocks if b.parsed]
        unparsed = [b.kind for b in archive.blocks if not b.parsed]
        self.log("")
        self.log("=== 无损自检 ===")
        self.log(f"  文件     : {self.path} ({len(data):,} 字节)")
        self.log(f"  已解析块 : {parsed}")
        self.log(f"  原样保留 : {unparsed or '无'}")
        if rebuilt == data:
            self._safe = True
            self.log("  重编码一致: 是（逐字节相同）—— 可以安全修改。")
            self.safety_label.configure(text="自检通过：可以安全修改", style="Ok.TLabel")
            messagebox.showinfo("自检通过", "重编码结果与原文件逐字节一致，可以安全修改。")
            self.set_status("自检通过")
        else:
            self._safe = False
            index = next((i for i, (a, b) in enumerate(zip(data, rebuilt)) if a != b), None)
            self.log(f"  重编码一致: 否（首个不一致字节 {index}）—— 不要修改此文件。")
            self.safety_label.configure(text="重编码不一致，禁止修改此文件", style="Bad.TLabel")
            messagebox.showerror("自检未通过",
                                 f"重编码结果与原文件不同（首个不一致字节 {index}）。\n"
                                 "这个文件可能来自本工具不支持的版本，请勿修改。")
            self.set_status("自检未通过")
        self.notebook.select(self.notebook.tabs()[-1])

    def _log_requested_edits(self) -> None:
        """把界面上请求的改动列进日志（与真正写入的内容同一份快照）。"""
        if not self.last_edits:
            return
        self.log("  界面请求的改动：")
        labels = {option: label for option, (_k, _key, _p, label) in SIMPLE_FIELDS.items()}
        labels.update({"name": "人物名称", "level": "等级", "hardcore": "专家模式",
                       "male": "性别", "greatest-difficulty": "最高主线难度",
                       "survival": "最高炼狱难度"})
        for key, value in self.last_edits.items():
            label = labels.get(key) or self._edit_label(key)
            self.log(f"    {label}: {value}")

    def _edit_label(self, key: str) -> str:
        """阵营改动的可读标签：``faction-3`` -> ``虚灵·声望``。"""
        if not key.startswith("faction-"):
            return key
        head = key[len("faction-"):]
        if head.endswith("-unlocked"):
            head = head[:-len("-unlocked")]
            suffix = "解锁"
        else:
            suffix = "声望"
        try:
            index = int(head)
            name = FACTION_NAMES[index] if 0 <= index < len(FACTION_NAMES) else f"#{index}"
        except ValueError:
            return key
        return f"{name}·{suffix}"

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE}\n\n"
            "读取 / 修改恐怖黎明角色存档 player.gdc 的图形工具。\n"
            "安全流程：写入前要求无损往返自检通过，写入后再解析一次逐字段比对。\n\n"
            "改档前请完全退出游戏，并注意 Steam 云同步可能覆盖改动。\n"
            "本工具只处理你自己的存档，请勿用于联机作弊。\n\nMIT License")


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """界面里未捕获的异常也要让人看见，而不是静默崩掉。"""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    sys.stderr.write(text)
    try:
        messagebox.showerror("程序异常", text[-1500:])
    except Exception:  # 弹窗失败就只剩 stderr
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = next((item for item in argv if item and not item.startswith("-")), None)
    sys.excepthook = _excepthook
    try:
        app = SaveEditorApp(path)
    except tk.TclError as exc:  # 没有图形环境（例如纯 SSH）
        print(f"无法启动图形界面：{exc}", file=sys.stderr)
        print("请在有桌面环境的 Windows 上运行本程序。", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
