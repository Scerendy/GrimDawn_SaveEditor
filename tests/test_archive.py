"""存档级测试：解析、重建、修改、校验。

测试用自行构造的合成存档，不依赖任何真实的 player.gdc 文件。
"""
import contextlib
import builtins
import copy
import io
import os
import tempfile
import unittest
import unittest.mock

from gd_save import Archive, SaveFormatError, edit_file, semantic_view
from gd_save.archive import Block
from gd_save.editor import (apply_to_bytes, check_editable, check_roundtrip, diff_views,
                            list_backups, read_save, restore_from_backup, write_backup)
from gd_save.fields import format_clock, format_duration, parse_duration, parse_uint32


def block1_body():
    """主界面数据：难度记录、金币、竞技场等（block1 v5）。"""
    return {"version": 5, "inMainQuest": 1, "hasBeenInGame": 1, "difficulty": 2, "challenge": 0,
            "gameMode": 0, "greatestDifficulty": 2, "iron": 100, "greatestSurvival": 0,
            "tributes": 0, "compass": 4, "showSkillHelp": 1, "altWeaponSet": 0,
            "altWeaponSetEnabled": 0, "modelTexture": "", "lootFilter": ""}


def block2_body():
    """属性与点数：等级、经验、剩余点数、三维、生命/能量（block2 v8）。

    三个属性特意取不同位置，方便界面测试覆盖加减规则：力量正好在下限 50，
    敏捷在下限之上（减 8 会贴到下限），智力远高于下限。
    """
    return {"version": 8, "level": 42, "experience": 0, "attributePoints": 16, "skillPoints": 2,
            "devotionPoints": 3, "totalDevotionPoints": 4, "physique": 50.0, "cunning": 58.0,
            "spirit": 100.0, "health": 100.0, "energy": 50.0}


def block8_body(version=8):
    """技能与专精：空技能列表 + 专精数 + 洗回来的点数（block8）。"""
    return {"version": version, "skills": [], "masteriesAllowed": 2,
            "skillPointsReclaimed": 0, "devotionPointsReclaimed": 0,
            "itemSkills": [], "subskills": []}


def block13_body():
    """阵营声望（block13）：28 个阵营，其中“放逐者”设为友善档 5000。"""
    factions = [{"index": index + 1, "changed": 0, "unlocked": 0, "value": 0.0,
                 "positiveBoost": 0.0, "negativeBoost": 0.0} for index in range(28)]
    factions[10]["value"] = 5000.0    # 放逐者：0 起下标 10，记录里的 index 是 11
    return {"version": 5, "faction": 0, "factions": factions}


def mob_digest():
    """block16 里记录的“最强怪物”摘要，三条都是空的。"""
    return {"greatestName": "", "level": 0, "lifeMana": 0, "lastHitAt": "", "lastHitFrom": ""}


def block16_body():
    """战绩 / 成就计数（block16 v11，阿斯特卡恩之前的版本）。"""
    return {"version": 11, "secondsPlayed": 3600, "deaths": 3, "kills": 100,
            "experienceFromKills": 10, "healthPotionsUsed": 0, "energyPotionsUsed": 0,
            "maxLevelWithoutKills": 1, "hitsReceived": 0, "hitsInflicted": 0,
            "critsInflicted": 0, "critsReceived": 0, "maxDamageInflicted": 1.5,
            "mobDigests": [mob_digest() for _ in range(3)], "championKills": 0,
            "lastHitDefensiveAbility": 0.0, "lastHitOffensiveAbility": 0.0,
            "maxDamageReceived": 0, "heroKills": 0, "itemsCrafted": 0, "relicsCrafted": 0,
            "transcendentRelicsCrafted": 0, "mythicalRelicsCrafted": 0, "shrinesRestored": 0,
            "oneshotChestsOpened": 0, "loreNotesCollected": 0, "bossKills1": 0, "bossKills2": 0,
            "bossKills3": 0, "maxSurvivalWave": 0, "maxSurvivalScore": 0,
            "survivalDefenseBuilt": 0, "survivalPowerups": 0, "skills": [],
            "endlessSouls": 0, "endlessEssence": 0, "difficultySkip": 0,
            "unknown1": 0, "unknown2": 0}


def empty_item(version=11):
    """一件空物品；v11 才有升天词缀与两个重随字段。"""
    item = {"name": "", "prefix": "", "suffix": "", "craftingBonus": "", "illusion": "",
            "seed": 0, "component": "", "relicBonus": "", "componentSeed": 0, "augment": "",
            "unknown1": 0, "augmentSeed": 0, "completion": 0, "amount": 0}
    if version >= 11:
        item["ascended"] = ""
        item["unknown2"] = ""
        item["seedRerolls"] = 0
        item["affixRerolls"] = 0
    return item


def block3_body(version=11):
    """一个空背包、十二个空装备槽、两套武器配置（block3）。"""
    sack_item = dict(empty_item(version), x=1, y=2)
    equipment = dict(empty_item(version), attached=1)
    return {"version": version, "isPresent": 1, "focusedSack": 0, "selectedSack": 0,
            "sacks": [{"hasUnseen": 0, "items": [sack_item]}],
            "selectedWeaponSet": 0,
            "equipment": [dict(equipment) for _ in range(12)],
            "weaponSets": [{"index": 0, "right": dict(equipment), "left": dict(equipment)},
                           {"index": 1, "right": dict(equipment), "left": dict(equipment)}]}


def block4_body(version=11):
    """角色仓库：一个格子的仓库（block4）。"""
    stash_item = dict(empty_item(version), x=0.0, y=1.0)
    return {"version": version,
            "stashes": [{"width": 12, "height": 8, "items": [stash_item],
                         "iconBorder": 1, "iconBackgroundColor": 2, "icon": 3,
                         "iconForegroundColor": 4, "title": "仓库"}]}


def block14_body(version=7):
    """界面设置与快捷栏（block14），46 个空快捷键。"""
    return {"version": version, "unknown1": 0, "unknown2": 0, "unknown3": 0,
            "skillSets": [{"primary": "", "secondary": "", "active": 0} for _ in range(5)],
            "hotSlotArrays": [{"index": 0, "hotSlots": [{"kind": -1} for _ in range(46)]}],
            "cameraDistance": 30.0}


def sample_archive():
    """造一个结构完整的合成存档：带头部和六个块。"""
    header = {"version": 2, "name": "Tester", "male": 1, "tag": "tagSkillClassName0401",
              "level": 42, "hardcore": 1, "expansions": 3}
    blocks = [Block(kind=1, size=0, body=block1_body()),
              Block(kind=2, size=0, body=block2_body()),
              Block(kind=3, size=0, body=block3_body()),
              Block(kind=4, size=0, body=block4_body()),
              Block(kind=8, size=0, body=block8_body()),
              Block(kind=14, size=0, body=block14_body()),
              Block(kind=16, size=0, body=block16_body())]
    return Archive(seed=0x1234ABCD, header=header, archive_version=8, uuid="00" * 16,
                   blocks=blocks)


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.archive = sample_archive()
        self.data = self.archive.to_bytes()

    def test_read_write_roundtrip(self):
        """解密再加密必须与原文件逐字节一致，这是所有修改的前提。"""
        parsed = Archive.read(self.data)
        self.assertEqual(parsed.seed, self.archive.seed)
        self.assertEqual(parsed.header, self.archive.header)
        self.assertEqual(semantic_view(parsed), semantic_view(self.archive))
        self.assertEqual(parsed.to_bytes(), self.data)

    def test_state_after_equals_trailing_bare_state(self):
        """每个块都解析成功，没有一块退回“原样字节保留”。"""
        parsed = Archive.read(self.data)
        for block in parsed.blocks:
            self.assertTrue(block.parsed)
            self.assertNotEqual(block.raw, b"")

    def test_roundtrip_guard_rejects_a_broken_encoder(self):
        """编码器有缺陷时，往返自检必须拦下（模拟重编码结果不同）。"""
        parsed = Archive.read(self.data)
        parsed.to_bytes = lambda: b"\x00" * len(self.data)
        with self.assertRaises(SaveFormatError):
            check_roundtrip(self.data, parsed)

    def test_bad_magic_is_rejected(self):
        with self.assertRaises(SaveFormatError):
            Archive.read(b"\x00" * 64)

    def test_truncated_file_is_rejected(self):
        with self.assertRaises((SaveFormatError, EOFError, ValueError)):
            Archive.read(self.data[:20])


class EditTest(unittest.TestCase):
    def setUp(self):
        self.archive = sample_archive()
        self.data = self.archive.to_bytes()

    def test_applies_only_the_requested_fields(self):
        rebuilt, report = apply_to_bytes(self.data, {"iron": "999999",
                                                     "playtime": "80h",
                                                     "cunning": "1234.5"})
        self.assertEqual(report.changed_bytes > 0, True)
        self.assertEqual(report.unexpected, [])
        after = Archive.read(rebuilt)
        self.assertEqual(after.require(1)["iron"], 999999)
        self.assertEqual(after.require(16)["secondsPlayed"], 80 * 3600)
        self.assertAlmostEqual(after.require(2)["cunning"], 1234.5, places=4)
        self.assertEqual(after.header["name"], "Tester")

    def test_unexpected_difference_is_reported(self):
        """如果一份改动清单谎报了自己改了什么，校验器必须发现这一点。"""
        first = semantic_view(Archive.read(self.data))
        second = copy.deepcopy(first)
        second["blocks"]["block2"]["physique"] = 999.0
        differences = diff_views(first, second)
        self.assertEqual([item[0] for item in differences], ["blocks.block2.physique"])

    def test_level_writes_header_and_block2(self):
        """等级同时写头部与 block2，两处都要变。"""
        rebuilt, report = apply_to_bytes(self.data, {"level": "100"})
        after = Archive.read(rebuilt)
        self.assertEqual(after.header["level"], 100)
        self.assertEqual(after.require(2)["level"], 100)
        self.assertEqual({change.path for change in report.changes},
                         {"header.level", "blocks.block2.level"})

    def test_reclaimed_skill_points_are_editable(self):
        """block8 里洗回来的技能点 / 虔诚点可以修改。"""
        rebuilt, report = apply_to_bytes(self.data, {"skill-points-reclaimed": "9",
                                                     "devotion-points-reclaimed": "7"})
        self.assertEqual(report.unexpected, [])
        after = Archive.read(rebuilt)
        self.assertEqual(after.require(8)["skillPointsReclaimed"], 9)
        self.assertEqual(after.require(8)["devotionPointsReclaimed"], 7)

    def test_edit_file_writes_and_backs_up(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "player.gdc")
            target = os.path.join(folder, "player.edited.gdc")
            with open(source, "wb") as handle:
                handle.write(self.data)
            with open(target, "wb") as handle:
                handle.write(b"old")
            report = edit_file(source, target, {"deaths": "0"})
            self.assertTrue(report.ok)
            self.assertIsNotNone(report.backup)
            self.assertTrue(os.path.exists(report.backup))
            _data, after = read_save(target)
            self.assertEqual(after.require(16)["deaths"], 0)

    def test_a_failed_backup_does_not_lose_the_edit(self):
        """目录拒绝创建 .bak 副本时，不能因此中断保存。

        这是一次真实报错的复现：工具什么都没写，用户只看到
        ``[Errno 13] Permission denied: <目标>.<时间戳>.bak``。备份只是顺带做的事，
        所以改动必须照常落盘，缺备份只作提示。
        """
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "player.gdc")
            target = os.path.join(folder, "player.edited.gdc")
            with open(source, "wb") as handle:
                handle.write(self.data)
            with open(target, "wb") as handle:
                handle.write(b"old")

            real_open = builtins.open

            def refuse_backups(path, *args, **kwargs):
                if str(path).endswith(".bak"):
                    raise PermissionError(13, "Permission denied", str(path))
                return real_open(path, *args, **kwargs)

            with contextlib.redirect_stderr(io.StringIO()) as err:
                with unittest.mock.patch("builtins.open", refuse_backups):
                    report = edit_file(source, target, {"deaths": "0"})

            self.assertIsNone(report.backup)
            self.assertTrue(report.ok)
            # 改动确实写进去了
            _data, after = read_save(target)
            self.assertEqual(after.require(16)["deaths"], 0)
            self.assertIn("无法创建备份", err.getvalue())

    def test_list_backups_is_newest_first(self):
        """备份按文件名里的时间戳倒序排列，界面把最新的放在最上面。"""
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "player.gdc")
            with open(target, "wb") as handle:
                handle.write(self.data)
            for stamp in ("20260101-000000", "20260301-000000", "20260201-000000"):
                with open(f"{target}.{stamp}.bak", "wb") as handle:
                    handle.write(self.data)
            # 名字里没有时间戳的文件不是本工具的备份，必须忽略
            with open(f"{target}.copy.bak", "wb") as handle:
                handle.write(b"x")
            names = [os.path.basename(item.path) for item in list_backups(target)]
            self.assertEqual(names, ["player.gdc.20260301-000000.bak",
                                     "player.gdc.20260201-000000.bak",
                                     "player.gdc.20260101-000000.bak"])

    def test_restore_from_backup_deletes_only_the_used_backup(self):
        """恢复后：用掉的那份删除，其他备份保留，恢复前的当前内容另存为新备份。"""
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "player.gdc")
            older = f"{target}.20260101-000000.bak"
            newer = f"{target}.20260202-000000.bak"
            changed = apply_to_bytes(self.data, {"iron": "4242"})[0]
            with open(target, "wb") as handle:
                handle.write(changed)
            with open(older, "wb") as handle:
                handle.write(self.data)      # 改之前的内容
            with open(newer, "wb") as handle:
                handle.write(changed)

            report = restore_from_backup(target, older)

            self.assertTrue(report.source_removed)
            self.assertFalse(os.path.exists(older))
            self.assertTrue(os.path.exists(newer))
            _data, after = read_save(target)
            self.assertEqual(after.require(1)["iron"], 100)      # self.data 里的原值
            # 恢复前的当前内容（4242）留在了新备份里，恢复本身也能反悔
            self.assertIsNotNone(report.current_backup)
            _data, previous = read_save(report.current_backup)
            self.assertEqual(previous.require(1)["iron"], 4242)

    def test_restore_refuses_a_file_that_is_not_a_save(self):
        """备份不是完整存档时拒绝恢复，目标文件和备份都保持原样。"""
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "player.gdc")
            bad = f"{target}.20260101-000000.bak"
            with open(target, "wb") as handle:
                handle.write(self.data)
            with open(bad, "wb") as handle:
                handle.write(b"not a save")
            with self.assertRaises(SaveFormatError):
                restore_from_backup(target, bad)
            self.assertTrue(os.path.exists(bad))
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), self.data)

    def test_same_second_backup_gets_a_sequence_number(self):
        """同一秒内的第二份备份加序号，不覆盖前一份。"""
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "player.gdc")
            first = write_backup(target, b"one")
            second = write_backup(target, b"two")
            self.assertNotEqual(first, second)
            self.assertTrue(second.endswith("-1.bak"), second)
            with open(first, "rb") as handle:
                self.assertEqual(handle.read(), b"one")

    def test_a_failed_write_leaves_no_junk_and_no_target(self):
        """目标写不进去时，不能留下临时文件，源文件也必须原封不动。"""
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "player.gdc")
            with open(source, "wb") as handle:
                handle.write(self.data)
            # 目标是个目录时，文件替换不上去
            target = os.path.join(folder, "target-dir")
            os.mkdir(target)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(OSError):
                    edit_file(source, target, {"deaths": "0"})
            leftovers = [name for name in os.listdir(folder) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [], leftovers)
            with open(source, "rb") as handle:
                self.assertEqual(handle.read(), self.data)


class NestedBlockTest(unittest.TestCase):
    """块 3/4/14 里含有嵌套的伪块；它们必须在编辑后保持不变。"""

    def setUp(self):
        self.data = sample_archive().to_bytes()

    def test_roundtrip_with_nested_blocks(self):
        parsed = Archive.read(self.data)
        for kind in (3, 4, 14):
            block = parsed.block(kind)
            self.assertIsNotNone(block)
            self.assertTrue(block.parsed, f"块 {kind} 应该能解析")
        self.assertEqual(parsed.to_bytes(), self.data)
        self.assertEqual(parsed.require(3)["sacks"][0]["items"][0]["x"], 1)
        self.assertEqual(parsed.require(4)["stashes"][0]["title"], "仓库")
        self.assertEqual(len(parsed.require(14)["hotSlotArrays"][0]["hotSlots"]), 46)

    def test_edit_before_them_keeps_their_content(self):
        """改前面的字段时，这些块的内容必须一字不差地保留。"""
        rebuilt, report = apply_to_bytes(self.data, {"iron": "777"})
        self.assertEqual(report.unexpected, [])
        before, after = Archive.read(self.data), Archive.read(rebuilt)
        self.assertEqual(after.require(1)["iron"], 777)
        for kind in (3, 4, 14):
            self.assertEqual(before.require(kind), after.require(kind))
        # 密文确实会变动
        self.assertNotEqual(rebuilt, self.data)

    def test_editing_is_refused_when_a_block_is_unknown(self):
        archive = sample_archive()
        archive.blocks.append(Block(kind=99, size=0, body=None, raw=b"\x00" * 12,
                                    state_after=0))
        with self.assertRaises(SaveFormatError):
            check_editable(archive)

    def test_preserved_blocks_are_hashed_in_the_view(self):
        archive = sample_archive()
        archive.blocks.append(Block(kind=99, size=0, body=None, raw=b"abc", state_after=0))
        view = semantic_view(archive)
        self.assertIn("preserved_sha256", view["blocks"]["block99"])


class ParserTest(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(parse_duration("80h"), 288000)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("288000s"), 288000)
        self.assertEqual(parse_duration("288000"), 288000)
        self.assertEqual(format_duration(288000), "80h00m00s")
        with self.assertRaises(ValueError):
            parse_duration("abc")

    def test_clock_durations(self):
        """游戏和界面使用的 HH:MM:SS 写法必须能读。"""
        self.assertEqual(parse_duration("03:08:00"), 3 * 3600 + 8 * 60)
        self.assertEqual(parse_duration("80:00:00"), 288000)
        self.assertEqual(parse_duration("1:30:00"), 5400)
        self.assertEqual(parse_duration("0:00:05"), 5)
        self.assertEqual(parse_duration(" 03:08:00 "), 3 * 3600 + 8 * 60)
        with self.assertRaises(ValueError):
            parse_duration("03:70:00")          # 分钟必须小于 60
        with self.assertRaises(ValueError):
            parse_duration("1:2:3:4")

    def test_format_clock_round_trips(self):
        for seconds in (0, 5, 5400, 11280, 288000):
            self.assertEqual(parse_duration(format_clock(seconds)), seconds)

    def test_uint32_bounds(self):
        self.assertEqual(parse_uint32("4,294,967,295"), 4294967295)
        self.assertEqual(parse_uint32("0x10"), 16)
        with self.assertRaises(ValueError):
            parse_uint32("4294967296")
        with self.assertRaises(ValueError):
            parse_uint32("-1")


if __name__ == "__main__":
    unittest.main()
