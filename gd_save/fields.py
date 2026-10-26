"""可编辑字段与取值解析器。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .archive import Archive

UINT32_MAX = 0xFFFFFFFF
FLOAT32_MAX = 3.4028235e38

#: block1（以及 ``greatestDifficulty``）使用的难度字节。
DIFFICULTIES = {"normal": 0, "elite": 1, "ultimate": 2, "普通": 0, "精英": 1, "终极": 2}

#: 挑战位（block1 合并字节的第 4-5 位）：难度的修饰符，0 无挑战 / 1 老兵（普通难度）/
#: 3 飞升（终极难度），没有 2。它与 ``greatestDifficulty``（最高主线难度）不是一回事。
CHALLENGES = {"none": 0, "veteran": 1, "ascended": 3, "无": 0, "老兵": 1, "飞升": 3}

#: block1 的 ``greatestSurvival`` 字节里存的竞技场（克鲁西布尔）难度。
SURVIVAL = {"aspirant": 0, "challenger": 1, "gladiator": 2,
            "新兵": 0, "挑战者": 1, "角斗士": 2}

_DURATION_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m)?\s*"
                          r"(?:(\d+(?:\.\d+)?)\s*s)?\s*$", re.IGNORECASE)

#: ``HH:MM:SS`` / ``MM:SS`` —— 游戏显示游戏时长用的格式。
_CLOCK_RE = re.compile(r"^\s*(\d+):(\d{1,2}):(\d{1,2})\s*$")


def parse_uint32(text: str) -> int:
    raw = str(text).strip().replace(",", "").replace("_", "")
    try:
        value = int(raw, 0)
    except (TypeError, ValueError):
        raise ValueError(f"{text!r} 不是整数") from None
    if not 0 <= value <= UINT32_MAX:
        raise ValueError(f"{text!r} 超出 uint32 的取值范围 0..{UINT32_MAX}")
    return value


def parse_float32(text: str) -> float:
    raw = str(text).strip().replace(",", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{text!r} 不是数字") from None
    if not -FLOAT32_MAX <= value <= FLOAT32_MAX:
        raise ValueError(f"{text!r} 超出 float32 的取值范围")
    return value


def parse_duration(text: str) -> int:
    """``03:08:00`` / ``80h`` / ``1h30m`` / ``90m`` / ``288000s`` / ``288000`` -> 秒数。

    ``HH:MM:SS`` 是游戏显示游戏时长的写法，也是界面上的显示形式，必须能读。
    """
    raw = str(text).strip().lower()
    if raw.isdigit():
        return int(raw)
    clock = _CLOCK_RE.match(raw)
    if clock is not None:
        hours, minutes, seconds = (int(part) for part in clock.groups())
        if minutes < 60 and seconds < 60:
            return hours * 3600 + minutes * 60 + seconds
        raise ValueError(f"无法解析时长 {text!r}：分和秒必须小于 60")
    match = _DURATION_RE.match(raw)
    if match is None or not any(match.groups()):
        raise ValueError(f"无法解析时长 {text!r} "
                         f"（可试 03:08:00、80h、1h30m、90m、288000s）")
    hours, minutes, seconds = (float(part) if part else 0.0 for part in match.groups())
    return int(round(hours * 3600 + minutes * 60 + seconds))


def format_duration(seconds: int | float) -> str:
    total = int(seconds)
    return f"{total // 3600}h{total % 3600 // 60:02d}m{total % 60:02d}s"


def format_clock(seconds: int | float) -> str:
    """``288000`` -> ``80:00:00``：游戏里显示游戏时长的写法。"""
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def format_whole(value) -> str:
    """``26940.302734375`` -> ``26940``：像游戏面板那样按整数显示（截断，不四舍五入）。

    block2 的属性 / 生命 / 能量和 block16 的一批计数在磁盘上是 f32，但界面上一律
    按整数呈现，不显示 ``10.0`` 这种小数尾巴。
    """
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


#: 界面上不能用 ``str(value)`` 直接表示的键。
DISPLAY_FORMATTERS = {
    "secondsPlayed": format_clock,
    # block2 的属性 / 生命 / 能量，以及 block16 的伤害与最近受击攻防：磁盘上是 f32，
    # 游戏面板按整数显示，界面保持一致（否则可编辑框里会写着 26940.302734375）。
    "physique": format_whole,
    "cunning": format_whole,
    "spirit": format_whole,
    "health": format_whole,
    "energy": format_whole,
    "maxDamageInflicted": format_whole,
    "maxDamageReceived": format_whole,
    "lastHitOffensiveAbility": format_whole,
    "lastHitDefensiveAbility": format_whole,
}


def field_text(key: str, value) -> str:
    """按界面上应该显示的样子渲染一个 body 取值。"""
    formatter = DISPLAY_FORMATTERS.get(key)
    return formatter(value) if formatter else str(value)


def parse_switch(text: str) -> int:
    raw = str(text).strip().lower()
    if raw in ("1", "true", "yes", "y", "on", "是", "开"):
        return 1
    if raw in ("0", "false", "no", "n", "off", "否", "关"):
        return 0
    raise ValueError(f"无法解析是/否取值 {text!r}")


@dataclass
class Change:
    """一处被修改的字段；``path`` 与校验器所用的路径一致。"""

    path: str
    old: object
    new: object
    note: str = ""

    def __str__(self) -> str:
        return f"{self.note or self.path}: {self.old} -> {self.new}"


def _block_path(kind: int, key: str) -> str:
    return f"blocks.block{kind}.{key}"


def _same(old, new) -> bool:
    """把 ``new`` 写进 ``old`` 时是否其实什么都没变。

    block16 里少数当整数用的字段实际是 f32，但编码器写进去的就是我们给的值，
    不存在额外的精度损失，所以 ``==`` 足够。
    """
    return old == new


def _set(archive: Archive, kind: int, key: str, value, note: str) -> Change | None:
    """写入一个 body 字段；值没变则返回 ``None``。

    返回 ``None`` 是有意义的：什么都没变的“改动”会在改动清单里打出 ``2 -> 2`` 这种假消息。
    """
    body = archive.require(kind)
    old = body[key]
    if _same(old, value):
        return None
    body[key] = value
    return Change(_block_path(kind, key), old, value, note)


def set_level(archive: Archive, value: int) -> list[Change]:
    """等级同时存在头部和 block2 里，两处都要写。"""
    old = archive.header["level"]
    archive.header["level"] = value
    inner = _set(archive, 2, "level", value, "等级(存档内)")
    return [Change("header.level", old, value, "等级(角色列表)")] + ([inner] if inner else [])


def set_name(archive: Archive, value: str) -> list[Change]:
    if not value.strip():
        raise ValueError("角色名不能为空")
    old = archive.header["name"]
    if _same(old, value):
        return []
    archive.header["name"] = value
    return [Change("header.name", old, value, "角色名")]


def set_hardcore(archive: Archive, value: int) -> list[Change]:
    old = archive.header["hardcore"]
    if _same(old, value):
        return []
    archive.header["hardcore"] = value
    return [Change("header.hardcore", old, value, "硬核模式")]


def set_male(archive: Archive, value: int) -> list[Change]:
    """角色性别：头部的 ``male`` 字节（1 = 男，0 = 女）。

    游戏在创建角色时就定死了性别，它自己的界面只做显示，所以这只能改存档；它只存在于
    头部，没有第二份需要同步。
    """
    old = archive.header["male"]
    if _same(old, value):
        return []
    archive.header["male"] = value
    return [Change("header.male", old, value, "性别")]


def _lookup(table: dict[str, int], value, what: str) -> int:
    key = str(value).strip().lower()
    if key not in table:
        raise ValueError(f"无法识别的{what} {value!r} "
                         f"（可用 {'/'.join(sorted({k for k in table if k.isascii()}))}）")
    return table[key]


def set_difficulty(archive: Archive, value) -> list[Change]:
    byte = _lookup(DIFFICULTIES, value, "难度")
    change = _set(archive, 1, "difficulty", byte, "当前难度")
    return [change] if change else []


def set_greatest_difficulty(archive: Archive, value) -> list[Change]:
    byte = _lookup(DIFFICULTIES, value, "难度")
    change = _set(archive, 1, "greatestDifficulty", byte, "最高主线难度")
    return [change] if change else []


def set_challenge(archive: Archive, value) -> list[Change]:
    """老兵 / 飞升等难度修饰符。

    它存放在 block1 里与难度共用的字节的第 4-5 位，写它只重新编码该字节，难度那 4 位
    不受影响。
    """
    byte = _lookup(CHALLENGES, value, "挑战")
    change = _set(archive, 1, "challenge", byte, "挑战难度")
    return [change] if change else []


def set_survival(archive: Archive, value) -> list[Change]:
    """角色已通关的竞技场（克鲁西布尔）难度（``greatestSurvival``）。"""
    byte = _lookup(SURVIVAL, value, "竞技场难度")
    change = _set(archive, 1, "greatestSurvival", byte, "竞技场最高难度")
    return [change] if change else []


#: 选项名 -> (块类型, body 键, 取值解析器, 标签)
#:
#: block16 那批是角色「成就」栏：``kills`` 击杀怪物、``championKills`` 击杀精英、
#: ``heroKills`` 击杀英雄；``bossKills1/2/3`` 是普通 / 精英 / 终极三个难度各自的
#: 复仇级（Nemesis）击杀数——三个独立计数器，没有总和字段，界面上不要合并。
#: ``championKillsAscendant`` 只有 block16 v12（阿斯特卡恩之牙）存档才有。
SIMPLE_FIELDS: dict[str, tuple] = {
    "iron": (1, "iron", parse_uint32, "铁币"),
    "tributes": (1, "tributes", parse_uint32, "贡品"),
    "experience": (2, "experience", parse_uint32, "经验值"),
    "attribute-points": (2, "attributePoints", parse_uint32, "剩余属性点"),
    "skill-points": (2, "skillPoints", parse_uint32, "剩余技能点"),
    "devotion-points": (2, "devotionPoints", parse_uint32, "剩余虔诚点"),
    "total-devotion-points": (2, "totalDevotionPoints", parse_uint32, "累计虔诚点"),
    # -- block8：洗点之后退回来的点数（界面上的“回收技能点 / 回收虔诚点”） --
    "skill-points-reclaimed": (8, "skillPointsReclaimed", parse_uint32, "回收技能点"),
    "devotion-points-reclaimed": (8, "devotionPointsReclaimed", parse_uint32, "回收虔诚点"),
    "physique": (2, "physique", parse_float32, "体格"),
    "cunning": (2, "cunning", parse_float32, "灵巧"),
    "spirit": (2, "spirit", parse_float32, "精神"),
    "health": (2, "health", parse_float32, "生命"),
    "energy": (2, "energy", parse_float32, "能量"),
    # -- 战绩 / 成就面板 (block16) ----------------------------------------
    # `playtime` 就是 block16 的 `secondsPlayed`；游戏（和本界面）按 HH:MM:SS
    # 显示，而 parse_duration 既收这种写法也收 `80h`。
    "playtime": (16, "secondsPlayed", parse_duration, "游戏时长"),
    "deaths": (16, "deaths", parse_uint32, "死亡次数"),
    "kills": (16, "kills", parse_uint32, "击杀怪物"),
    "max-survival-score": (16, "maxSurvivalScore", parse_uint32, "竞技场最高分"),
    "max-damage-inflicted": (16, "maxDamageInflicted", parse_float32, "造成的最大伤害"),
    # 看着像整数计数，但在 block16 里是单精度浮点（26940.302734375，游戏面板截断显示
    # 为 26940）；按 uint32 读会得到 1188198555 —— 那只是同样 4 个字节的另一种解释。
    "max-damage-received": (16, "maxDamageReceived", parse_float32, "受到的最大伤害"),
    "survival-defense-built": (16, "survivalDefenseBuilt", parse_uint32, "竞技场建造次数"),
    "survival-powerups": (16, "survivalPowerups", parse_uint32, "竞技场强化次数"),
    "hits-inflicted": (16, "hitsInflicted", parse_uint32, "命中次数"),
    "hits-received": (16, "hitsReceived", parse_uint32, "被命中次数"),
    "crits-inflicted": (16, "critsInflicted", parse_uint32, "暴击次数"),
    "crits-received": (16, "critsReceived", parse_uint32, "被暴击次数"),
    "champion-kills": (16, "championKills", parse_uint32, "击杀精英"),
    "hero-kills": (16, "heroKills", parse_uint32, "击杀英雄"),
    "items-crafted": (16, "itemsCrafted", parse_uint32, "制作物品"),
    "relics-crafted": (16, "relicsCrafted", parse_uint32, "制作遗物"),
    "transcendent-relics-crafted": (16, "transcendentRelicsCrafted", parse_uint32,
                                    "制作卓越遗物"),
    "mythical-relics-crafted": (16, "mythicalRelicsCrafted", parse_uint32, "制作神话遗物"),
    "boss-kills-1": (16, "bossKills1", parse_uint32, "击杀复仇（普通）"),
    "boss-kills-2": (16, "bossKills2", parse_uint32, "击杀复仇（精英）"),
    "boss-kills-3": (16, "bossKills3", parse_uint32, "击杀复仇（终极）"),
    # -- 只有 block16 v12 (阿斯特卡恩之牙) 才有的字段 ----------------------
    "champion-kills-ascendant": (16, "championKillsAscendant", parse_uint32, "击杀飞升精英"),
}

#: 只有 block16 v12 存档才有的字段；老存档里没有存放位置，界面会置灰并标注缺失。
BLOCK16_V12_FIELDS = ("champion-kills-ascendant",)


# --------------------------------------------------------------------------
# 阵营声望（block13）
# --------------------------------------------------------------------------
#: block13 里 0 起下标对应的阵营名（顺序即游戏内部阵营 ID，名字本身不存进存档）。
#:
#: 必须按 29 项完整保留：下标就是存档里的位置，删掉任何一项都会让后面的阵营整体错位。
FACTION_NAMES: list[str] = [
    "幸存者（隐藏）",   # 游戏面板不显示，但存档里有实值（24000）
    "恶魔十字",
    "虚灵",
    "冥煞",
    "克隆利帮",
    "野兽",
    "流浪者",
    "隐藏槽位 7",      # 见 HIDDEN_FACTION_INDEXES
    "霍姆斯特德",
    "隐藏槽位 9",      # 见 HIDDEN_FACTION_INDEXES
    "放逐者",
    "死亡守夜人教团",
    "安克维亚不死族",
    "黑色军团",
    "凯蒙的神选者",
    "巫登沼泽女巫团",
    "巴罗霍尔姆",
    "马尔茅斯反抗军",
    "虚灵先锋",
    "拜斯迈教团",
    "德里格教团",
    "索拉尔教团",
    "邪魔",
    "库恩",
    "血羁",
    "隐藏槽位 25",     # 见 HIDDEN_FACTION_INDEXES
    "隐藏槽位 26",     # 见 HIDDEN_FACTION_INDEXES
    "诺克图卡里",
    "隐藏槽位 28",     # 见 HIDDEN_FACTION_INDEXES
]

#: 游戏阵营面板从不显示、名字也无法验证的槽位下标。
#:
#: 真实存档里它们始终保持 ``unlocked=0`` / ``changed=0``，即使所有真阵营都满档也不会解锁；
#: 数值是哨兵值而不是声望档位（7 -> 0.0、9 -> -1000.0、25/26/28 -> -1.0，其中 -1.0 也是
#: “从未打过交道”阵营的初始值）。界面不为它们建行（见 gui._build_faction_tab），数据层照旧
#: 可读写；要临时查看全部槽位，把下面的集合清空即可。
HIDDEN_FACTION_INDEXES = {7, 9, 25, 26, 28}

#: 声望档位的门槛值与档位名，顺序即游戏内部的档位序。
#:
#: 两端有实证：游戏自己给满声望阵营存 25000.0（一个成品号上正好 14 个阵营是这个值）、
#: 给敌对阵营存 -20000.0。中间几档的门槛取自 GDStash ``GDCharFactionPane`` 的
#: ``minReputationLevels``（无法从存档反推），档位名对照它的 ``factionAll`` 与游戏文本
#: ``tagFactionState*``；社区对照表里憎恨 / 厌恶的高低顺序有写反的版本，以 GDStash 为准。
#: 注意 -1.0 同时也是“从未打过交道”阵营的哨兵值（见 HIDDEN_FACTION_INDEXES）。
REPUTATION_LEVELS: list[tuple[str, float]] = [
    ("复仇", -20000.0), ("厌恶", -8000.0), ("憎恨", -1500.0), ("敌对", -1.0),
    ("中立", 0.0), ("容忍", 1.0), ("友善", 1500.0), ("尊重", 5000.0),
    ("尊敬", 10000.0), ("崇敬", 25000.0)]

#: 下拉框选中某档时写入的数值：一律写该档门槛。崇敬写 25000，正是游戏给满声望阵营存的值。
REPUTATION_PRESETS: dict[str, float] = {
    name: threshold for name, threshold in REPUTATION_LEVELS}


def reputation_label(value) -> str:
    """声望数值 -> 档位名（25000.0 -> "崇敬"）。

    复仇是最低档，比它门槛还低的值也判为复仇；完全解析不出数值时返回空串，让界面整行置灰。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    label = REPUTATION_LEVELS[0][0]
    for name, threshold in REPUTATION_LEVELS:
        if number >= threshold:
            label = name
    return label


def parse_reputation(text) -> float:
    """档位名（"崇敬"）或裸数值（"-500"）-> 写入存档的 float。"""
    key = str(text).strip()
    if key in REPUTATION_PRESETS:
        return REPUTATION_PRESETS[key]
    return parse_float32(key)


def faction_value_text(value) -> str:
    """25000.0 -> "25000"：界面上不显示 .0 这种小数尾巴。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _faction_entry(archive: Archive, index: int) -> dict:
    """按 0 起下标取出 block13 里对应的阵营条目。

    解码器给每条记录登记的 ``index`` 是 1 起的（decode_block13 里
    ``index + 1``），所以这里按下标匹配时要比对 ``index + 1``。
    """
    body = archive.require(13)
    for entry in body["factions"]:
        if entry.get("index") == index + 1:
            return entry
    raise ValueError(f"block13 里没有下标 {index} 的阵营（共 {len(body['factions'])} 条）")


def set_faction(archive: Archive, index: int, value) -> list[Change]:
    """修改一个阵营的声望值；``value`` 可以是档位名或裸数值。"""
    if not 0 <= index < len(FACTION_NAMES):
        raise ValueError(f"阵营下标 {index} 超出范围 0..{len(FACTION_NAMES) - 1}")
    entry = _faction_entry(archive, index)
    new = parse_reputation(value)
    old = entry["value"]
    if _same(old, new):
        return []
    entry["value"] = new
    return [Change(f"blocks.block13.factions[{index}].value", old, new,
                   f"声望·{FACTION_NAMES[index]}")]


def set_faction_unlocked(archive: Archive, index: int, value) -> list[Change]:
    """解锁 / 锁回一个阵营。

    真实存档里 ``changed`` 字节始终与 ``unlocked`` 一致（已解锁阵营全是 1，
    未解锁全是 0），所以解锁时两处一起写；只写 unlocked 可能留下游戏没见过的
    组合。
    """
    if not 0 <= index < len(FACTION_NAMES):
        raise ValueError(f"阵营下标 {index} 超出范围 0..{len(FACTION_NAMES) - 1}")
    entry = _faction_entry(archive, index)
    flag = 1 if value else 0
    if _same(entry["unlocked"], flag):
        return []
    name = FACTION_NAMES[index]
    changes = [Change(f"blocks.block13.factions[{index}].unlocked",
                      entry["unlocked"], flag, f"解锁·{name}")]
    entry["unlocked"] = flag
    if not _same(entry["changed"], flag):
        changes.append(Change(f"blocks.block13.factions[{index}].changed",
                              entry["changed"], flag, f"改动标记·{name}"))
        entry["changed"] = flag
    return changes


def available_fields(archive: Archive, kind: int | None = None) -> set[str]:
    """返回这个存档里确实存在对应 body 键的已登记选项名。

    body 里带哪些键是版本相关的，调用方据此判断某个字段在这个文件里到底存不存在。
    """
    present: set[str] = set()
    for option, (block_kind, key, _parser, _label) in SIMPLE_FIELDS.items():
        if kind is not None and block_kind != kind:
            continue
        block = archive.block(block_kind)
        if block is not None and block.parsed and key in block.body:
            present.add(option)
    return present


def apply_edits(archive: Archive, edits: dict) -> list[Change]:
    """应用所有请求的修改，并返回改动列表。

    ``edits`` 把选项名映射到它们的原始取值（可重复的选项用列表）。取值非法时抛出
    ``ValueError``；目标块缺失或其版本不受支持时抛出 ``SaveFormatError``。
    """
    changes: list[Change] = []
    if edits.get("name") is not None:
        changes += set_name(archive, str(edits["name"]))
    if edits.get("level") is not None:
        changes += set_level(archive, parse_uint32(edits["level"]))
    if edits.get("hardcore") is not None:
        changes += set_hardcore(archive, parse_switch(edits["hardcore"]))
    if edits.get("male") is not None:
        changes += set_male(archive, parse_switch(edits["male"]))
    if edits.get("difficulty") is not None:
        changes += set_difficulty(archive, edits["difficulty"])
    if edits.get("greatest-difficulty") is not None:
        changes += set_greatest_difficulty(archive, edits["greatest-difficulty"])
    if edits.get("challenge") is not None:
        changes += set_challenge(archive, edits["challenge"])
    if edits.get("survival") is not None:
        changes += set_survival(archive, edits["survival"])
    # playtime 不需要特殊分支：它是 block16 的 secondsPlayed，已按 parse_duration 登记。
    for option, (kind, key, parser, label) in SIMPLE_FIELDS.items():
        raw = edits.get(option)
        if raw is None:
            continue
        value = parser(raw) if isinstance(raw, str) else raw
        # 空改动返回 None 并被丢弃，这样改动清单里只会出现真正的修改。
        change = _set(archive, kind, key, value, label)
        if change is not None:
            changes.append(change)
    # 阵营声望（block13）：``faction-<i>`` 是声望值（档位名或裸数值），
    # ``faction-<i>-unlocked`` 是解锁开关。下标是 0 起的阵营下标，
    # 与 FACTION_NAMES 和界面上的行一一对应。
    for option, raw in edits.items():
        if raw is None or not isinstance(option, str):
            continue
        if not option.startswith("faction-"):
            continue
        head = option[len("faction-"):]
        if head.endswith("-unlocked"):
            index = int(head[:-len("-unlocked")])
            changes += set_faction_unlocked(archive, index, parse_switch(raw))
        else:
            index = int(head)
            changes += set_faction(archive, index, raw)
    return changes
