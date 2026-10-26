"""嵌套结构：物品、背包、仓库、装备和快捷栏。

它们位于 block3、block4 和 block14 内部。背包和仓库都是“伪块”（kind 0）：它们
与顶层块携带相同的 ``kind | size | body | bare state`` 框架，因此可以用同样的
方式跳过。字段顺序已与 gdcx 参考实现
(https://codeberg.org/alex-ilchukov/gdcx) 交叉核对过。
"""
from __future__ import annotations

from .crypto import Decoder, Encoder


class SubBlockError(Exception):
    """一个嵌套伪块的布局与预期不符。
    """


# --------------------------------------------------------------------------
# 伪块框架（kind | size | body | bare state）
# --------------------------------------------------------------------------
def decode_pseudo_block(dec: Decoder, body_reader) -> dict:
    kind = dec.u32()
    if kind != 0:
        raise SubBlockError(f"期望 kind 为 0 的伪块，实际 kind 为 {kind}")
    size = dec.size_u32()
    end = dec.pos + size
    body = body_reader(dec)
    if dec.pos != end:
        raise SubBlockError(f"伪块 body 实际读取 {dec.pos - (end - size)} 字节，预期 {size} 字节")
    dec.pos = end
    dec.bare_state()
    return body


def encode_pseudo_block(enc: Encoder, body_writer, body: dict) -> None:
    enc.u32(0)
    state_at_size = enc.state
    size_offset = enc.reserve(4)
    start = len(enc.out)
    body_writer(enc, body)
    enc.patch_u32_at(size_offset, state_at_size, len(enc.out) - start)
    enc.seal_state()


# --------------------------------------------------------------------------
# 物品
# --------------------------------------------------------------------------
def decode_item(dec: Decoder, version: int) -> dict:
    item = {
        "name": dec.ascii_string(),
        "prefix": dec.ascii_string(),
        "suffix": dec.ascii_string(),
        "craftingBonus": dec.ascii_string(),
        "illusion": dec.ascii_string(),
        "seed": dec.u32(),
        "component": dec.ascii_string(),
        "relicBonus": dec.ascii_string(),
        "componentSeed": dec.u32(),
        "augment": dec.ascii_string(),
        "unknown1": dec.u32(),
        "augmentSeed": dec.u32(),
    }
    if version >= 11:
        item["ascended"] = dec.ascii_string()
        item["unknown2"] = dec.ascii_string()
    item["completion"] = dec.u32()
    item["amount"] = dec.u32()
    if version >= 11:
        item["seedRerolls"] = dec.u32()
        item["affixRerolls"] = dec.u32()
    return item


def encode_item(enc: Encoder, item: dict, version: int) -> None:
    for key in ("name", "prefix", "suffix", "craftingBonus", "illusion"):
        enc.ascii_string(item[key])
    enc.u32(item["seed"])
    enc.ascii_string(item["component"])
    enc.ascii_string(item["relicBonus"])
    enc.u32(item["componentSeed"])
    enc.ascii_string(item["augment"])
    enc.u32(item["unknown1"])
    enc.u32(item["augmentSeed"])
    if version >= 11:
        enc.ascii_string(item["ascended"])
        enc.ascii_string(item["unknown2"])
    enc.u32(item["completion"])
    enc.u32(item["amount"])
    if version >= 11:
        enc.u32(item["seedRerolls"])
        enc.u32(item["affixRerolls"])


def decode_sack_item(dec: Decoder, version: int) -> dict:
    item = decode_item(dec, version)
    item["x"] = dec.u32()
    item["y"] = dec.u32()
    return item


def encode_sack_item(enc: Encoder, item: dict, version: int) -> None:
    encode_item(enc, item, version)
    enc.u32(item["x"])
    enc.u32(item["y"])


def decode_stash_item(dec: Decoder, version: int) -> dict:
    item = decode_item(dec, version)
    item["x"] = dec.f32()
    item["y"] = dec.f32()
    return item


def encode_stash_item(enc: Encoder, item: dict, version: int) -> None:
    encode_item(enc, item, version)
    enc.f32(item["x"])
    enc.f32(item["y"])


def decode_equipment(dec: Decoder, version: int) -> dict:
    item = decode_item(dec, version)
    item["attached"] = dec.byte()
    return item


def encode_equipment(enc: Encoder, item: dict, version: int) -> None:
    encode_item(enc, item, version)
    enc.byte(item["attached"])


# --------------------------------------------------------------------------
# block3：背包和装备
# --------------------------------------------------------------------------
def decode_sack(dec: Decoder, version: int) -> dict:
    def body(decoder: Decoder) -> dict:
        sack = {"hasUnseen": decoder.byte(), "items": []}
        for _ in range(decoder.u32()):
            sack["items"].append(decode_sack_item(decoder, version))
        return sack

    return decode_pseudo_block(dec, body)


def encode_sack(enc: Encoder, sack: dict, version: int) -> None:
    def body(writer: Encoder, value: dict) -> None:
        writer.byte(value["hasUnseen"])
        writer.u32(len(value["items"]))
        for item in value["items"]:
            encode_sack_item(writer, item, version)

    encode_pseudo_block(enc, body, sack)


def decode_block3(dec: Decoder) -> dict:
    version = dec.u32()
    if version not in (4, 11):
        raise SubBlockError(f"不支持 block3 版本 {version}")
    body = {"version": version, "isPresent": dec.byte()}
    if not body["isPresent"]:
        return body
    count = dec.u32()
    body["focusedSack"] = dec.u32()
    body["selectedSack"] = dec.u32()
    body["sacks"] = [decode_sack(dec, version) for _ in range(count)]
    body["selectedWeaponSet"] = dec.byte()
    body["equipment"] = [decode_equipment(dec, version) for _ in range(12)]
    weapon_sets = []
    for _ in range(2):
        weapon_sets.append({"index": dec.byte(),
                            "right": decode_equipment(dec, version),
                            "left": decode_equipment(dec, version)})
    body["weaponSets"] = weapon_sets
    return body


def encode_block3(enc: Encoder, body: dict) -> None:
    version = body["version"]
    enc.u32(version)
    enc.byte(body["isPresent"])
    if not body["isPresent"]:
        return
    enc.u32(len(body["sacks"]))
    enc.u32(body["focusedSack"])
    enc.u32(body["selectedSack"])
    for sack in body["sacks"]:
        encode_sack(enc, sack, version)
    enc.byte(body["selectedWeaponSet"])
    for item in body["equipment"]:
        encode_equipment(enc, item, version)
    for weapon_set in body["weaponSets"]:
        enc.byte(weapon_set["index"])
        encode_equipment(enc, weapon_set["right"], version)
        encode_equipment(enc, weapon_set["left"], version)


# --------------------------------------------------------------------------
# block4：个人仓库
# --------------------------------------------------------------------------
def decode_stash(dec: Decoder, version: int) -> dict:
    def body(decoder: Decoder) -> dict:
        stash = {"width": decoder.u32(), "height": decoder.u32(), "items": []}
        for _ in range(decoder.u32()):
            stash["items"].append(decode_stash_item(decoder, version))
        if version >= 11:
            stash["iconBorder"] = decoder.u32()
            stash["iconBackgroundColor"] = decoder.u32()
            stash["icon"] = decoder.u32()
            stash["iconForegroundColor"] = decoder.u32()
            stash["title"] = decoder.utf16_string()
        return stash

    return decode_pseudo_block(dec, body)


def encode_stash(enc: Encoder, stash: dict, version: int) -> None:
    def body(writer: Encoder, value: dict) -> None:
        writer.u32(value["width"])
        writer.u32(value["height"])
        writer.u32(len(value["items"]))
        for item in value["items"]:
            encode_stash_item(writer, item, version)
        if version >= 11:
            writer.u32(value["iconBorder"])
            writer.u32(value["iconBackgroundColor"])
            writer.u32(value["icon"])
            writer.u32(value["iconForegroundColor"])
            writer.utf16_string(value["title"])

    encode_pseudo_block(enc, body, stash)


def decode_block4(dec: Decoder) -> dict:
    version = dec.u32()
    if version not in (6, 11):
        raise SubBlockError(f"不支持 block4 版本 {version}")
    count = dec.u32()
    return {"version": version, "stashes": [decode_stash(dec, version) for _ in range(count)]}


def encode_block4(enc: Encoder, body: dict) -> None:
    enc.u32(body["version"])
    enc.u32(len(body["stashes"]))
    for stash in body["stashes"]:
        encode_stash(enc, stash, body["version"])


# --------------------------------------------------------------------------
# block14：界面设置和快捷栏
# --------------------------------------------------------------------------
HOT_SLOT_COUNTS = {5: (1, 46), 6: (1, 47)}


def decode_skill_set(dec: Decoder) -> dict:
    return {"primary": dec.ascii_string(), "secondary": dec.ascii_string(), "active": dec.byte()}


def encode_skill_set(enc: Encoder, skill_set: dict) -> None:
    enc.ascii_string(skill_set["primary"])
    enc.ascii_string(skill_set["secondary"])
    enc.byte(skill_set["active"])


def decode_hot_slot(dec: Decoder) -> dict:
    kind = dec.i32()
    slot = {"kind": kind}
    if kind == 0:
        slot["skillName"] = dec.ascii_string()
        slot["isItemSkill"] = dec.byte()
        slot["itemName"] = dec.ascii_string()
        slot["itemLocation"] = dec.u32()
    elif kind == 4:
        slot["itemName"] = dec.ascii_string()
        slot["up"] = dec.ascii_string()
        slot["down"] = dec.ascii_string()
        slot["text"] = dec.utf16_string()
    return slot


def encode_hot_slot(enc: Encoder, slot: dict) -> None:
    enc.i32(slot["kind"])
    if slot["kind"] == 0:
        enc.ascii_string(slot["skillName"])
        enc.byte(slot["isItemSkill"])
        enc.ascii_string(slot["itemName"])
        enc.u32(slot["itemLocation"])
    elif slot["kind"] == 4:
        enc.ascii_string(slot["itemName"])
        enc.ascii_string(slot["up"])
        enc.ascii_string(slot["down"])
        enc.utf16_string(slot["text"])


def decode_block14(dec: Decoder) -> dict:
    version = dec.u32()
    if version not in (5, 6, 7):
        raise SubBlockError(f"不支持 block14 版本 {version}")
    body = {"version": version, "unknown1": dec.byte(), "unknown2": dec.u32(),
            "unknown3": dec.byte(), "skillSets": [decode_skill_set(dec) for _ in range(5)]}
    arrays = []
    if version in HOT_SLOT_COUNTS:
        count, slots = HOT_SLOT_COUNTS[version]
        for _ in range(count):
            arrays.append({"index": 0, "hotSlots": [decode_hot_slot(dec) for _ in range(slots)]})
    else:
        array_count = dec.u32()
        slot_count = dec.u32()
        for _ in range(array_count):
            index = dec.u32()
            arrays.append({"index": index,
                           "hotSlots": [decode_hot_slot(dec) for _ in range(slot_count)]})
    body["hotSlotArrays"] = arrays
    body["cameraDistance"] = dec.f32()
    return body


def encode_block14(enc: Encoder, body: dict) -> None:
    version = body["version"]
    enc.u32(version)
    enc.byte(body["unknown1"])
    enc.u32(body["unknown2"])
    enc.byte(body["unknown3"])
    for skill_set in body["skillSets"]:
        encode_skill_set(enc, skill_set)
    arrays = body["hotSlotArrays"]
    if version >= 7:
        enc.u32(len(arrays))
        enc.u32(len(arrays[0]["hotSlots"]) if arrays else 0)
    for array in arrays:
        if version >= 7:
            enc.u32(array["index"])
        for slot in array["hotSlots"]:
            encode_hot_slot(enc, slot)
    enc.f32(body["cameraDistance"])
