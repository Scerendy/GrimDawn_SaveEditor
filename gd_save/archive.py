"""解析并重建归档（也就是整个存档文件）。

布局（参见格式文档）：

    seed | magic "GDCX" | 头版本 | 头部字段 | 裸状态
    archive version | uuid | block | block | ...

每个块都是 ``kind | size | body | bare state``；size 只与状态异或、不推进状态，
末尾的裸状态则会把状态重置，这正是为什么一个版本无法识别的块可以被原样复制，
而不会破坏它后面的任何数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .crypto import MAGIC, Decoder, Encoder
from .items import (decode_block3, decode_block4, decode_block14, encode_block3,
                    encode_block4, encode_block14)


class SaveFormatError(Exception):
    """这个文件不是我们能解析的恐怖黎明角色存档。"""


# --------------------------------------------------------------------------
# 块解码器：Decoder -> body dict
# --------------------------------------------------------------------------
def _uuid_seq(dec: Decoder) -> list[str]:
    return [dec.uuid() for _ in range(dec.u32())]


def _ascii_seq(dec: Decoder) -> list[str]:
    return [dec.ascii_string() for _ in range(dec.u32())]


def _db_seq(dec: Decoder) -> list[str]:
    return [dec.ascii_string() for _ in range(dec.u32())]


def decode_block1(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 5:
        raise SaveFormatError(f"block1 版本 {version} 不受支持")
    body = {"version": version}
    body["inMainQuest"] = dec.byte()
    body["hasBeenInGame"] = dec.byte()
    combined = dec.byte()
    body["difficulty"] = combined & 0x0F
    body["challenge"] = (combined >> 4) & 0x03
    body["gameMode"] = (combined >> 6) & 0x03
    body["greatestDifficulty"] = dec.byte()
    body["iron"] = dec.u32()
    body["greatestSurvival"] = dec.byte()
    body["tributes"] = dec.u32()
    body["compass"] = dec.byte()
    body["showSkillHelp"] = dec.byte()
    body["altWeaponSet"] = dec.byte()
    body["altWeaponSetEnabled"] = dec.byte()
    body["modelTexture"] = dec.ascii_string()
    loot = dec.u32()
    body["lootFilter"] = dec.blob(loot).hex()
    return body


def decode_block2(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 8:
        raise SaveFormatError(f"block2 版本 {version} 不受支持")
    body = {"version": version, "level": dec.u32(), "experience": dec.u32(),
            "attributePoints": dec.u32(), "skillPoints": dec.u32(),
            "devotionPoints": dec.u32(), "totalDevotionPoints": dec.u32()}
    for key in ("physique", "cunning", "spirit", "health", "energy"):
        body[key] = dec.f32()
    return body


def _decode_skills(dec: Decoder, version: int) -> list[dict]:
    skills = []
    for _ in range(dec.u32()):
        skill = {"name": dec.ascii_string(), "level": dec.u32(), "enabled": dec.byte()}
        if version >= 8:
            skill["locked"] = dec.byte()
        skill["devotionLevel"] = dec.u32()
        skill["devotionExp"] = dec.u32()
        skill["sublevel"] = dec.u32()
        skill["aura"] = dec.byte()
        skill["unknown"] = dec.byte()
        skill["autocast"] = dec.ascii_string()
        skill["controller"] = dec.ascii_string()
        skills.append(skill)
    return skills


def decode_block8(dec: Decoder) -> dict:
    version = dec.u32()
    if version not in (5, 6, 8):
        raise SaveFormatError(f"block8 版本 {version} 不受支持")
    body = {"version": version, "skills": _decode_skills(dec, version)}
    body["masteriesAllowed"] = dec.u32()
    body["skillPointsReclaimed"] = dec.u32()
    body["devotionPointsReclaimed"] = dec.u32()
    item_skills = []
    for _ in range(dec.u32()):
        item_skills.append({"name": dec.ascii_string(), "autocast": dec.ascii_string(),
                            "controller": dec.ascii_string(), "itemLocation": dec.u32(),
                            "itemName": dec.ascii_string()})
    body["itemSkills"] = item_skills
    subskills = []
    if version >= 6:
        for _ in range(dec.u32()):
            subskills.append({"name": dec.ascii_string(), "autocast": dec.ascii_string(),
                              "controller": dec.ascii_string(), "parent": dec.ascii_string()})
    body["subskills"] = subskills
    return body


def decode_block5(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 1:
        raise SaveFormatError(f"block5 版本 {version} 不受支持")
    return {"version": version, "spawnNormal": _uuid_seq(dec), "spawnElite": _uuid_seq(dec),
            "spawnUltimate": _uuid_seq(dec), "lastNormal": dec.uuid(),
            "lastElite": dec.uuid(), "lastUltimate": dec.uuid()}


def decode_block6(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 1:
        raise SaveFormatError(f"block6 版本 {version} 不受支持")
    return {"version": version, "normal": _uuid_seq(dec), "elite": _uuid_seq(dec),
            "ultimate": _uuid_seq(dec)}


def decode_block7(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 1:
        raise SaveFormatError(f"block7 版本 {version} 不受支持")
    return {"version": version, "normal": _uuid_seq(dec), "elite": _uuid_seq(dec),
            "ultimate": _uuid_seq(dec)}


def decode_block17(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 2:
        raise SaveFormatError(f"block17 版本 {version} 不受支持")
    return {"version": version, "sequences": [_uuid_seq(dec) for _ in range(6)]}


def decode_block10(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 2:
        raise SaveFormatError(f"block10 版本 {version} 不受支持")
    return {"version": version, "tokens": [_ascii_seq(dec) for _ in range(3)]}


def decode_block12(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 1:
        raise SaveFormatError(f"block12 版本 {version} 不受支持")
    return {"version": version, "notes": _db_seq(dec)}


def decode_block13(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 5:
        raise SaveFormatError(f"block13 版本 {version} 不受支持")
    faction = dec.u32()
    factions = []
    for index in range(dec.u32()):
        factions.append({"index": index + 1, "changed": dec.byte(), "unlocked": dec.byte(),
                         "value": dec.f32(), "positiveBoost": dec.f32(), "negativeBoost": dec.f32()})
    return {"version": version, "faction": faction, "factions": factions}


def decode_block15(dec: Decoder) -> dict:
    version = dec.u32()
    if version != 1:
        raise SaveFormatError(f"block15 版本 {version} 不受支持")
    return {"version": version, "tutorials": [dec.u32() for _ in range(dec.u32())]}


def _decode_mob_digest(dec: Decoder) -> dict:
    return {"greatestName": dec.ascii_string(), "level": dec.u32(), "lifeMana": dec.u32(),
            "lastHitAt": dec.ascii_string(), "lastHitFrom": dec.ascii_string()}


def decode_block16(dec: Decoder) -> dict:
    version = dec.u32()
    if version not in (11, 12):
        raise SaveFormatError(f"block16 版本 {version} 不受支持")
    body = {"version": version}
    for key in ("secondsPlayed", "deaths", "kills", "experienceFromKills", "healthPotionsUsed",
                "energyPotionsUsed", "maxLevelWithoutKills", "hitsReceived", "hitsInflicted",
                "critsInflicted", "critsReceived"):
        body[key] = dec.u32()
    body["maxDamageInflicted"] = dec.f32()
    body["mobDigests"] = [_decode_mob_digest(dec) for _ in range(3)]
    body["championKills"] = dec.u32()
    body["lastHitDefensiveAbility"] = dec.f32()
    body["lastHitOffensiveAbility"] = dec.f32()
    # ``maxDamageReceived`` 在 block16 里是浮点，尽管游戏按整数显示它（这个角色面板把
    # 存下来的 26940.302734375 显示成 26940）。同样 4 个字节按 uint32 读会得到
    # 1188198555。
    body["maxDamageReceived"] = dec.f32()
    for key in ("heroKills", "itemsCrafted", "relicsCrafted",
                "transcendentRelicsCrafted", "mythicalRelicsCrafted", "shrinesRestored",
                "oneshotChestsOpened", "loreNotesCollected", "bossKills1", "bossKills2",
                "bossKills3", "maxSurvivalWave", "maxSurvivalScore", "survivalDefenseBuilt",
                "survivalPowerups"):
        body[key] = dec.u32()
    body["skills"] = [{"name": dec.ascii_string(), "level": dec.u32()} for _ in range(dec.u32())]
    body["endlessSouls"] = dec.u32()
    body["endlessEssence"] = dec.u32()
    body["difficultySkip"] = dec.byte()
    if version >= 12:
        body["championKillsAscendant"] = dec.u32()
        body["hiddenChestsOpened"] = dec.u32()
    body["unknown1"] = dec.u32()
    body["unknown2"] = dec.u32()
    return body


DECODERS: dict[int, Callable[[Decoder], dict]] = {
    1: decode_block1, 2: decode_block2, 3: decode_block3, 4: decode_block4,
    5: decode_block5, 6: decode_block6, 7: decode_block7, 8: decode_block8,
    10: decode_block10, 12: decode_block12, 13: decode_block13, 14: decode_block14,
    15: decode_block15, 16: decode_block16, 17: decode_block17,
}


# --------------------------------------------------------------------------
# 块编码器：Encoder, body -> None（kind/size/裸状态由外部处理）
# --------------------------------------------------------------------------
def _enc_uuid_seq(enc: Encoder, seq: list[str]) -> None:
    enc.u32(len(seq))
    for item in seq:
        enc.uuid(item)


def _enc_ascii_seq(enc: Encoder, seq: list[str]) -> None:
    enc.u32(len(seq))
    for item in seq:
        enc.ascii_string(item)


def encode_block1(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.byte(body["inMainQuest"])
    enc.byte(body["hasBeenInGame"])
    enc.byte((body["difficulty"] & 0x0F) | ((body["challenge"] & 0x03) << 4)
             | ((body["gameMode"] & 0x03) << 6))
    enc.byte(body["greatestDifficulty"])
    enc.u32(body["iron"])
    enc.byte(body["greatestSurvival"])
    enc.u32(body["tributes"])
    enc.byte(body["compass"])
    enc.byte(body["showSkillHelp"])
    enc.byte(body["altWeaponSet"])
    enc.byte(body["altWeaponSetEnabled"])
    enc.ascii_string(body["modelTexture"])
    loot = bytes.fromhex(body["lootFilter"]) if body["lootFilter"] else b""
    enc.u32(len(loot))
    enc.blob(loot)


def encode_block2(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.u32(body["level"])
    enc.u32(body["experience"])
    enc.u32(body["attributePoints"])
    enc.u32(body["skillPoints"])
    enc.u32(body["devotionPoints"])
    enc.u32(body["totalDevotionPoints"])
    for key in ("physique", "cunning", "spirit", "health", "energy"):
        enc.f32(body[key])


def _encode_skills(enc: Encoder, skills: list[dict], version: int) -> None:
    enc.u32(len(skills))
    for skill in skills:
        enc.ascii_string(skill["name"])
        enc.u32(skill["level"])
        enc.byte(skill["enabled"])
        if version >= 8:
            enc.byte(skill["locked"])
        enc.u32(skill["devotionLevel"])
        enc.u32(skill["devotionExp"])
        enc.u32(skill["sublevel"])
        enc.byte(skill["aura"])
        enc.byte(skill["unknown"])
        enc.ascii_string(skill["autocast"])
        enc.ascii_string(skill["controller"])


def encode_block8(enc: Encoder, body: dict) -> None:
    version = body["version"]
    enc.u32(version)
    _encode_skills(enc, body["skills"], version)
    enc.u32(body["masteriesAllowed"])
    enc.u32(body["skillPointsReclaimed"])
    enc.u32(body["devotionPointsReclaimed"])
    enc.u32(len(body["itemSkills"]))
    for skill in body["itemSkills"]:
        enc.ascii_string(skill["name"])
        enc.ascii_string(skill["autocast"])
        enc.ascii_string(skill["controller"])
        enc.u32(skill["itemLocation"])
        enc.ascii_string(skill["itemName"])
    if version >= 6:
        enc.u32(len(body["subskills"]))
        for skill in body["subskills"]:
            enc.ascii_string(skill["name"])
            enc.ascii_string(skill["autocast"])
            enc.ascii_string(skill["controller"])
            enc.ascii_string(skill["parent"])


def encode_block5(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    _enc_uuid_seq(enc, body["spawnNormal"])
    _enc_uuid_seq(enc, body["spawnElite"])
    _enc_uuid_seq(enc, body["spawnUltimate"])
    enc.uuid(body["lastNormal"])
    enc.uuid(body["lastElite"])
    enc.uuid(body["lastUltimate"])


def encode_block6(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    _enc_uuid_seq(enc, body["normal"])
    _enc_uuid_seq(enc, body["elite"])
    _enc_uuid_seq(enc, body["ultimate"])


def encode_block7(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    _enc_uuid_seq(enc, body["normal"])
    _enc_uuid_seq(enc, body["elite"])
    _enc_uuid_seq(enc, body["ultimate"])


def encode_block17(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    for seq in body["sequences"]:
        _enc_uuid_seq(enc, seq)


def encode_block10(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    for group in body["tokens"]:
        _enc_ascii_seq(enc, group)


def encode_block12(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.u32(len(body["notes"]))
    for note in body["notes"]:
        enc.ascii_string(note)


def encode_block13(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.u32(body["faction"])
    enc.u32(len(body["factions"]))
    for faction in body["factions"]:
        enc.byte(faction["changed"])
        enc.byte(faction["unlocked"])
        enc.f32(faction["value"])
        enc.f32(faction["positiveBoost"])
        enc.f32(faction["negativeBoost"])


def encode_block15(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.u32(len(body["tutorials"]))
    for item in body["tutorials"]:
        enc.u32(item)


def _encode_mob_digest(enc: Encoder, digest: dict) -> None:
    enc.ascii_string(digest["greatestName"])
    enc.u32(digest["level"])
    enc.u32(digest["lifeMana"])
    enc.ascii_string(digest["lastHitAt"])
    enc.ascii_string(digest["lastHitFrom"])


def encode_block16(enc: Encoder, body: dict) -> None:
    version = body["version"]
    enc.u32(version)
    for key in ("secondsPlayed", "deaths", "kills", "experienceFromKills", "healthPotionsUsed",
                "energyPotionsUsed", "maxLevelWithoutKills", "hitsReceived", "hitsInflicted",
                "critsInflicted", "critsReceived"):
        enc.u32(body[key])
    enc.f32(body["maxDamageInflicted"])
    for digest in body["mobDigests"]:
        _encode_mob_digest(enc, digest)
    enc.u32(body["championKills"])
    enc.f32(body["lastHitDefensiveAbility"])
    enc.f32(body["lastHitOffensiveAbility"])
    enc.f32(body["maxDamageReceived"])
    for key in ("heroKills", "itemsCrafted", "relicsCrafted",
                "transcendentRelicsCrafted", "mythicalRelicsCrafted", "shrinesRestored",
                "oneshotChestsOpened", "loreNotesCollected", "bossKills1", "bossKills2",
                "bossKills3", "maxSurvivalWave", "maxSurvivalScore", "survivalDefenseBuilt",
                "survivalPowerups"):
        enc.u32(body[key])
    enc.u32(len(body["skills"]))
    for skill in body["skills"]:
        enc.ascii_string(skill["name"])
        enc.u32(skill["level"])
    enc.u32(body["endlessSouls"])
    enc.u32(body["endlessEssence"])
    enc.byte(body["difficultySkip"])
    if version >= 12:
        enc.u32(body["championKillsAscendant"])
        enc.u32(body["hiddenChestsOpened"])
    enc.u32(body["unknown1"])
    enc.u32(body["unknown2"])


ENCODERS: dict[int, Callable[[Encoder, dict], None]] = {
    1: encode_block1, 2: encode_block2, 3: encode_block3, 4: encode_block4,
    5: encode_block5, 6: encode_block6, 7: encode_block7, 8: encode_block8,
    10: encode_block10, 12: encode_block12, 13: encode_block13, 14: encode_block14,
    15: encode_block15, 16: encode_block16, 17: encode_block17,
}


@dataclass
class Block:
    """一条 ``kind | size | body | bare state`` 记录。"""

    kind: int
    size: int
    body: dict | None = None
    raw: bytes | None = None
    state_after: int = 0
    kind_offset: int = 0
    end_offset: int = 0
    error: str | None = None
    offsets: dict = field(default_factory=dict)

    @property
    def parsed(self) -> bool:
        return self.body is not None

    @property
    def version(self) -> int | None:
        return None if self.body is None else self.body.get("version")


@dataclass
class Archive:
    """整个存档文件：文件头加上按顺序排列的每一个块。"""

    seed: int
    header: dict
    archive_version: int
    uuid: str
    blocks: list[Block]
    header_state: int = 0

    # -- 读取 ------------------------------------------------------------
    @classmethod
    def read(cls, data: bytes) -> "Archive":
        dec = Decoder(data)
        if dec.u32() != MAGIC:
            raise SaveFormatError("不是恐怖黎明角色存档（缺少 GDCX 魔数）")
        header_version = dec.u32()
        if header_version not in (1, 2):
            raise SaveFormatError(f"header 版本 {header_version} 不受支持")
        header = {"version": header_version, "name": dec.utf16_string(), "male": dec.byte(),
                  "tag": dec.ascii_string(), "level": dec.u32(), "hardcore": dec.byte()}
        if header_version >= 2:
            header["expansions"] = dec.byte()
        header_state = dec.bare_state()
        archive_version = dec.u32()
        uuid = dec.uuid()

        blocks: list[Block] = []
        while dec.pos + 4 <= len(data):
            kind_offset = dec.pos
            kind = dec.u32()
            size = dec.size_u32()
            body_start = dec.pos
            body_end = body_start + size
            if body_end + 4 > len(data):
                raise SaveFormatError(f"位于 {kind_offset} 的 block {kind} 声称其数据延伸到文件末尾之外 {size} 字节")
            state_before, pos_before = dec.state, dec.pos
            block = Block(kind=kind, size=size, kind_offset=kind_offset,
                          offsets={"body_start": body_start, "body_end": body_end})
            decode = DECODERS.get(kind)
            if decode is not None:
                try:
                    block.body = decode(dec)
                    if dec.pos != body_end:
                        raise SaveFormatError(f"block {kind} 解析了 {dec.pos - body_start} "
                                              f"/ {size} 字节")
                except Exception as exc:  # 未知布局：保持字节原样
                    block.body = None
                    block.error = str(exc)
                    dec.state, dec.pos = state_before, pos_before
            dec.pos = body_end
            block.state_after = dec.bare_state()
            block.end_offset = dec.pos
            if not block.parsed:
                block.raw = data[kind_offset:dec.pos]
            blocks.append(block)
        return cls(seed=dec.seed, header=header, archive_version=archive_version, uuid=uuid,
                   blocks=blocks, header_state=header_state)

    # -- 写入 ------------------------------------------------------------
    def to_bytes(self) -> bytes:
        enc = Encoder.for_archive(self.seed)
        enc.u32(MAGIC)
        header = self.header
        enc.u32(header["version"])
        enc.utf16_string(header["name"])
        enc.byte(header["male"])
        enc.ascii_string(header["tag"])
        enc.u32(header["level"])
        enc.byte(header["hardcore"])
        if header["version"] >= 2:
            enc.byte(header.get("expansions", 0))
        enc.seal_state()
        enc.u32(self.archive_version)
        enc.uuid(self.uuid)
        for block in self.blocks:
            if block.parsed:
                enc.u32(block.kind)
                state_at_size = enc.state
                size_offset = enc.reserve(4)
                start = len(enc.out)
                ENCODERS[block.kind](enc, block.body)
                enc.patch_u32_at(size_offset, state_at_size, len(enc.out) - start)
                enc.seal_state()
            else:
                assert block.raw is not None
                enc.out += block.raw
                enc.state = block.state_after
        return enc.data

    # -- 辅助 ------------------------------------------------------------
    def block(self, kind: int) -> Block | None:
        for block in self.blocks:
            if block.kind == kind:
                return block
        return None

    def require(self, kind: int) -> dict:
        block = self.block(kind)
        if block is None or block.body is None:
            raise SaveFormatError(f"此存档中没有 block {kind} "
                                  f"（未解析或缺失）")
        return block.body
