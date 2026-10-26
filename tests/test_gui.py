"""界面测试：窗口必须能载入存档、正确填值、只请求用户真正改过的字段。

这些测试以无头方式运行（Windows 上 Tk 不需要显示器）；在打不开 Tk 的机器上
会自动跳过，而不是失败。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gd_save.archive import Block
from gd_save.editor import list_backups, read_save
from gd_save.fields import BLOCK16_V12_FIELDS

from tests.test_archive import (block1_body, block2_body, block8_body, block13_body,
                                block16_body, sample_archive)

try:
    import tkinter as tk
    from tkinter import messagebox, ttk

    import gd_save.gui as gui_module
    from gd_save.gui import SaveEditorApp
    _root = tk.Tk()
    _root.destroy()
    TK_AVAILABLE = True
    TK_ERROR = ""
except Exception as exc:  # 机器上没有可用的 Tk
    TK_AVAILABLE = False
    TK_ERROR = str(exc)


def gui_save() -> bytes:
    """造一个带 block1/2/8/13/16 的合成存档，覆盖界面上的全部控件。"""
    archive = sample_archive()
    archive.blocks = [Block(kind=1, size=0, body=block1_body()),
                      Block(kind=2, size=0, body=block2_body()),
                      Block(kind=8, size=0, body=block8_body()),
                      Block(kind=13, size=0, body=block13_body()),
                      Block(kind=16, size=0, body=block16_body())]
    return archive.to_bytes()


@unittest.skipUnless(TK_AVAILABLE, f"这里打不开 tkinter：{TK_ERROR}")
class GuiTest(unittest.TestCase):
    def setUp(self):
        # 让弹窗不再阻塞测试
        self.dialogs = []
        self._saved = {name: getattr(messagebox, name)
                       for name in ("askyesno", "showinfo", "showwarning", "showerror")}
        messagebox.askyesno = lambda *a, **k: True
        for name in ("showinfo", "showwarning", "showerror"):
            setattr(messagebox, name, lambda *a, **k: self.dialogs.append(a))

        self.folder = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.folder.name, "_Reaver", "player.gdc")
        os.makedirs(os.path.dirname(self.source), exist_ok=True)
        with open(self.source, "wb") as handle:
            handle.write(gui_save())
        self.app = SaveEditorApp()
        self.app.open_file(self.source)
        self.app.update()

    def tearDown(self):
        for name, function in self._saved.items():
            setattr(messagebox, name, function)
        # 先冲掉挂起的事件，避免销毁窗口时 Tk 往 stderr 报一行无害提示
        try:
            self.app.update()
        except tk.TclError:
            pass
        self.app.destroy()
        self.folder.cleanup()

    def test_opens_and_passes_the_safety_check(self):
        self.assertTrue(self.app._safe, self.app.safety_label.cget("text"))
        self.assertEqual(self.app.vars["name"].get(), "Tester")
        self.assertEqual(self.app.vars["iron"].get(), "100")
        self.assertEqual(self.app.vars["playtime"].get(), "01:00:00")
        self.assertEqual(self.app.vars["level"].get(), "42")

    def test_untouched_window_requests_no_edits(self):
        """点开界面什么都不动，不能产生任何改动。"""
        self.assertEqual(self.app.collect_edits(), {})

    def test_only_changed_fields_are_requested(self):
        self.app._set_field("iron", "999")
        self.app._set_field("cunning", "2000")
        self.app._set_field("playtime", "80h")
        edits = self.app.collect_edits()
        self.assertEqual(set(edits), {"iron", "cunning", "playtime"}, edits)
        self.assertEqual(edits["playtime"], "80h")

    def test_character_group_is_filled(self):
        """人物数据分组：名称、等级、性别、专家模式都按存档填好。"""
        self.assertEqual(self.app.vars["name"].get(), "Tester")
        self.assertEqual(self.app.vars["level"].get(), "42")
        self.assertEqual(self.app.vars["male"].get(), "男")
        # 合成存档头部 hardcore=1，所以专家模式应当被勾上
        self.assertTrue(self.app.vars["hardcore"].get())

    def test_sex_field_reflects_the_header_byte(self):
        """头部 male 字节（1 男 / 0 女）必须可改。"""
        self.assertEqual(self.app.archive.header["male"], 1)
        self.app._set_field("male", "女")
        self.assertEqual(self.app.collect_edits().get("male"), "0")
        # 改回去就不应产生改动
        self.app._set_field("male", "男")
        self.assertNotIn("male", self.app.collect_edits())

    def test_hardcore_checkbox_is_optional(self):
        """合成存档是硬核，取消勾选应当产生 hardcore=0 的改动。"""
        self.app.vars["hardcore"].set(False)
        self.assertEqual(self.app.collect_edits().get("hardcore"), "0")

    def test_two_difficulty_records_are_editable(self):
        """两条“最高难度”记录按下拉框序号写回存档字节。"""
        self.app._set_field("greatest-difficulty", "精英")
        self.app._set_field("survival", "角斗士")
        edits = self.app.collect_edits()
        self.assertEqual(edits.get("greatest-difficulty"), "elite")
        self.assertEqual(edits.get("survival"), "gladiator")

    def test_difficulty_dropdowns_stay_chinese_only(self):
        """下拉框只提供中文选项，不夹带英文。"""
        from gd_save.gui import DIFFICULTY_CHOICES, SURVIVAL_CHOICES
        self.assertEqual(DIFFICULTY_CHOICES, ["普通", "精英", "终极"])
        self.assertEqual(SURVIVAL_CHOICES, ["修习者", "挑战者", "角斗士"])

    def test_changing_the_level_back_gives_no_edit(self):
        self.app._set_field("level", "99")
        self.assertIn("level", self.app.collect_edits())
        self.app._set_field("level", "42")
        self.assertNotIn("level", self.app.collect_edits())

    def test_points_group_exposes_reclaimed_points(self):
        """点数分组要带上 block8 的两个回收点数字段。"""
        for key in ("attribute-points", "skill-points", "devotion-points",
                    "total-devotion-points", "tributes", "skill-points-reclaimed",
                    "devotion-points-reclaimed"):
            self.assertIn(key, self.app.vars, key)
        self.assertEqual(self.app.vars["skill-points-reclaimed"].get(), "0")

    def test_attribute_step_buttons(self):
        """加减按钮：属性 ±8，属性点每次只 ∓1。"""
        # 所有数值按整数显示，没有 100.0 这种小数尾巴
        self.assertEqual(self.app.vars["spirit"].get(), "100")
        self.assertEqual(self.app.vars["attribute-points"].get(), "16")
        self.app._step("spirit", +1)
        self.assertEqual(self.app.vars["spirit"].get(), "108")
        self.assertEqual(self.app.vars["attribute-points"].get(), "15")
        self.app._step("spirit", -1)
        self.app._step("spirit", -1)
        self.assertEqual(self.app.vars["spirit"].get(), "92")
        self.assertEqual(self.app.vars["attribute-points"].get(), "17")

    def test_attribute_step_stops_at_the_minimum(self):
        """属性最低 50：减到 50 后减号不再生效，也不再退属性点。"""
        # 敏捷 58，减一次贴到下限
        self.app._step("cunning", -1)
        self.assertEqual(self.app.vars["cunning"].get(), "50")
        self.assertEqual(self.app.vars["attribute-points"].get(), "17")
        # 力量正好在下限，减号完全不动、也不退点
        self.app._step("physique", -1)
        self.assertEqual(self.app.vars["physique"].get(), "50")
        self.assertEqual(self.app.vars["attribute-points"].get(), "17")

    def test_plus_requires_one_attribute_point(self):
        """属性点为 0 时 ＋ 不生效：属性和点数都保持不动。"""
        self.app._set_field("attribute-points", "0")
        self.app._step("spirit", +1)
        self.assertEqual(self.app.vars["spirit"].get(), "100")
        self.assertEqual(self.app.vars["attribute-points"].get(), "0")

    def test_stepping_attribute_requests_both_edits(self):
        """加 8 点敏捷、花 1 个属性点后，两处都要进改动清单。"""
        self.app._step("cunning", +1)
        edits = self.app.collect_edits()
        self.assertEqual(edits.get("cunning"), "66")
        self.assertEqual(edits.get("attribute-points"), "15")

    def test_attribute_boxes_share_one_column(self):
        """属性组五个数值框必须落在同一列，才能左右对齐。"""
        columns = {self.app.vars[key].grid_info()["column"]
                   for key in ("physique", "cunning", "spirit", "health", "energy")}
        self.assertEqual(len(columns), 1, columns)

    def test_achievement_group_has_no_reset_buttons(self):
        """伤害计数右侧的 × 清零按钮已移除，成就分组里不应再有任何按钮。"""
        achievement_box = self.app.vars["max-damage-inflicted"].master
        buttons = [child for child in achievement_box.winfo_children()
                   if isinstance(child, ttk.Button)]
        self.assertEqual(buttons, [], buttons)

    def test_achievement_group_exposes_every_block16_statistic(self):
        """成就分组要带上存档里全部战绩计数。"""
        expected = {
            "max-damage-inflicted", "max-damage-received", "kills", "hero-kills",
            "champion-kills", "boss-kills-1", "boss-kills-2", "boss-kills-3",
            "champion-kills-ascendant",
            "playtime", "items-crafted", "relics-crafted",
            "transcendent-relics-crafted", "mythical-relics-crafted",
            "hits-inflicted", "hits-received", "crits-inflicted", "crits-received",
            "max-survival-score", "survival-defense-built",
            "survival-powerups",
        }
        missing = sorted(key for key in expected if key not in self.app.vars)
        self.assertEqual(missing, [], f"界面缺少这些字段: {missing}")
        # 每一项都要和存档里的值对得上，而不是留空
        self.assertEqual(self.app.vars["kills"].get(), "100")
        self.assertEqual(self.app.vars["deaths"].get(), "3")
        self.assertEqual(self.app.vars["items-crafted"].get(), "0")

    def test_potion_counters_are_not_exposed(self):
        """生命/能量药水这两个计数只原样保留，不给输入框。"""
        for gone in ("health-potions-used", "energy-potions-used"):
            self.assertNotIn(gone, self.app.vars)
        self.assertIn("healthPotionsUsed", self.app.archive.require(16))
        self.assertIn("energyPotionsUsed", self.app.archive.require(16))

    # ------------------------------------------------------------ 阵营势力页
    def test_faction_tab_lists_only_visible_slots(self):
        """阵营页只为游戏面板会显示的槽位建行，隐藏槽位一个都不出现。"""
        from gd_save.fields import FACTION_NAMES, HIDDEN_FACTION_INDEXES
        rows = self.app._faction_rows
        for index in range(len(FACTION_NAMES)):
            if index in HIDDEN_FACTION_INDEXES:
                self.assertNotIn(index, rows, f"隐藏槽位 {index} 不该建行")
                self.assertNotIn(f"faction-{index}", self.app.vars)
                continue
            self.assertIn(index, rows, f"阵营「{FACTION_NAMES[index]}」缺少行")
            self.assertIn(f"faction-{index}-level", self.app.vars)
            self.assertIn(f"faction-{index}-unlocked", self.app.vars)

    def test_faction_rows_follow_block13(self):
        """合成存档把下标 10（放逐者）设为 5000：数值框、档位下拉、解锁勾选都要对上。"""
        self.assertEqual(self.app.vars["faction-10"].get(), "5000")
        self.assertEqual(self.app.vars["faction-10-level"].get(), "尊重")
        self.assertFalse(self.app.vars["faction-10-unlocked"].get())
        # 其余阵营是 0.0：显示成没有小数尾巴的 0，档位是「中立」
        self.assertEqual(self.app.vars["faction-2"].get(), "0")
        self.assertEqual(self.app.vars["faction-2-level"].get(), "中立")

    def test_faction_level_dropdown_only_fills_the_value_box(self):
        """状态下拉只是快捷方式：它把数值填进数值框，本身不产生改动。"""
        self.app.vars["faction-2-level"].set("崇敬")
        self.assertEqual(self.app.collect_edits(), {})
        self.app._apply_faction_level(2)
        self.assertEqual(self.app.collect_edits().get("faction-2"), "25000")

    def test_faction_unlock_is_requested(self):
        """勾选解锁要产生 faction-<i>-unlocked=1 的改动。"""
        self.app.vars["faction-10-unlocked"].set(True)
        self.assertEqual(self.app.collect_edits().get("faction-10-unlocked"), "1")

    def test_faction_edits_are_written_and_validated(self):
        """阵营改动走完整写入流程，写回后能从 block13 读回同样的值。"""
        self.app.vars["faction-2-level"].set("崇敬")
        self.app._apply_faction_level(2)
        self.app.vars["faction-10-unlocked"].set(True)
        self.app.save_in_place()
        self.app.update()
        _data, after = read_save(self.app.path)
        entries = {entry["index"]: entry for entry in after.require(13)["factions"]}
        self.assertEqual(entries[3]["value"], 25000.0)   # 虚灵：下标 2 -> 记录下标 3
        self.assertEqual(entries[11]["unlocked"], 1)    # 放逐者：0 起下标 10 -> 记录下标 11
        self.assertEqual(entries[11]["changed"], 1)     # changed 必须跟着一起写

    def test_save_in_place_writes_and_validates(self):
        self.app._set_field("iron", "4242")
        self.app.save_in_place()
        self.app.update()
        # 直接写回读取的那个文件
        copy = self.app.path
        self.assertEqual(os.path.abspath(copy), os.path.abspath(self.source))
        _data, after = read_save(copy)
        self.assertEqual(after.require(1)["iron"], 4242)
        # 保存前的内容备份成 <文件名>.<时间戳>.bak
        backups = [name for name in os.listdir(os.path.dirname(copy))
                   if name.startswith("player.gdc.") and name.endswith(".bak")]
        self.assertEqual(len(backups), 1, backups)
        _data, original = read_save(os.path.join(os.path.dirname(copy), backups[0]))
        self.assertEqual(original.require(1)["iron"], 100)

    def test_saving_twice_keeps_both_timestamped_backups(self):
        """每次保存都生成新的时间戳备份，不覆盖旧备份。"""
        self.app._set_field("iron", "4242")
        self.app.save_in_place()
        self.app.update()
        # 备份名的时间戳精确到秒；跨过一秒再保存，保证两次备份名不同
        time.sleep(1.1)
        self.app._set_field("iron", "8484")
        self.app.save_in_place()
        self.app.update()
        folder = os.path.dirname(self.app.path)
        backups = [name for name in os.listdir(folder)
                   if name.startswith("player.gdc.") and name.endswith(".bak")]
        self.assertEqual(len(backups), 2, backups)

    def test_opening_edits_the_file_in_place(self):
        """打开的文件就是之后读写的文件，不再复制工作副本。"""
        self.assertEqual(os.path.abspath(self.app.path),
                         os.path.abspath(self.source))

    # ------------------------------------------------------------ 备份与恢复
    def test_combobox_popdown_uses_the_theme_colours(self):
        """下拉弹层是 Tk 的 Listbox，必须用 option database 上色，否则是系统白底蓝选中。"""
        self.app.update()
        popdown = self.app.tk.call("ttk::combobox::PopdownWindow", str(self.app.save_combo))
        listbox = f"{popdown}.f.l"
        self.assertEqual(self.app.tk.call(listbox, "cget", "-background"),
                         gui_module.GD_ENTRY)
        self.assertEqual(self.app.tk.call(listbox, "cget", "-foreground"),
                         gui_module.GD_TEXT)
        self.assertEqual(self.app.tk.call(listbox, "cget", "-selectbackground"),
                         gui_module.GD_GOLD)
        self.assertEqual(self.app.tk.call(listbox, "cget", "-selectforeground"),
                         gui_module.GD_ENTRY)

    def test_restore_button_sits_right_of_save_as(self):
        texts = [widget.cget("text") for widget in self.app.toolbar.winfo_children()]
        self.assertIn("恢复存档", texts)
        self.assertEqual(texts[texts.index("另存为") + 1], "恢复存档")

    def test_restore_puts_the_backup_content_back(self):
        """恢复后文件回到备份时的内容；用掉的备份删除，恢复前的内容另存为新备份。"""
        self.app._set_field("iron", "4242")
        self.app.save_in_place()
        self.app.update()
        backups = list_backups(self.app.path)
        self.assertEqual(len(backups), 1, [item.path for item in backups])
        used = backups[0]
        self.app._choose_backup = lambda _items: used      # 跳过选择弹窗
        self.app.restore()
        self.app.update()
        _data, after = read_save(self.app.path)
        self.assertEqual(after.require(1)["iron"], 100)    # 备份里的原值
        remaining = [item.path for item in list_backups(self.app.path)]
        self.assertNotIn(used.path, remaining)
        self.assertEqual(len(remaining), 1, remaining)
        _data, previous = read_save(remaining[0])
        self.assertEqual(previous.require(1)["iron"], 4242)

    def test_backup_dialog_lists_backups_and_can_be_cancelled(self):
        self.app._set_field("iron", "4242")
        self.app.save_in_place()
        self.app.update()
        backups = list_backups(self.app.path)
        self.assertTrue(backups)

        def cancel() -> None:
            for child in self.app.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()

        self.app.after(120, cancel)
        self.assertIsNone(self.app._choose_backup(backups))

    def test_save_as_defaults_to_the_source_directory(self):
        """另存为的默认位置是 player.gdc 的同级目录（源文件所在目录）。"""
        captured = {}

        def fake_asksaveasfilename(**kwargs):
            captured.update(kwargs)
            return ""

        real = gui_module.filedialog.asksaveasfilename
        gui_module.filedialog.asksaveasfilename = fake_asksaveasfilename
        try:
            self.app.save_as()
        finally:
            gui_module.filedialog.asksaveasfilename = real
        self.assertEqual(os.path.abspath(captured["initialdir"]),
                         os.path.abspath(os.path.dirname(self.app.path)))

    def test_broken_file_is_rejected_without_crashing(self):
        broken = os.path.join(self.folder.name, "broken.gdc")
        with open(broken, "wb") as handle:
            handle.write(b"not a save at all" * 4)
        count = len(self.dialogs)
        self.app.open_file(broken)
        self.assertGreater(len(self.dialogs), count)


class OldSaveTest(unittest.TestCase):
    """阿斯特卡恩之前的存档没有那两个 v12 专属计数器的位置。"""

    def setUp(self):
        self._saved = {name: getattr(messagebox, name)
                       for name in ("askyesno", "showinfo", "showwarning", "showerror")}
        messagebox.askyesno = lambda *a, **k: True
        for name in ("showinfo", "showwarning", "showerror"):
            setattr(messagebox, name, lambda *a, **k: None)

        self.folder = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.folder.name, "player.gdc")
        archive = sample_archive()
        # block16 v11（阿斯特卡恩之前）
        archive.blocks = [Block(kind=1, size=0, body=block1_body()),
                          Block(kind=2, size=0, body=block2_body()),
                          Block(kind=8, size=0, body=block8_body()),
                          Block(kind=16, size=0, body=block16_body())]
        with open(self.source, "wb") as handle:
            handle.write(archive.to_bytes())
        self.app = SaveEditorApp()
        self.app.open_file(self.source)
        self.app.update()

    def tearDown(self):
        for name, function in self._saved.items():
            setattr(messagebox, name, function)
        # 先冲掉挂起的事件，避免销毁窗口时 Tk 往 stderr 报一行无害提示
        try:
            self.app.update()
        except tk.TclError:
            pass
        self.app.destroy()
        self.folder.cleanup()

    def test_v12_only_fields_are_disabled_and_never_edited(self):
        for option in BLOCK16_V12_FIELDS:
            widget = self.app.vars[option]
            self.assertEqual(str(widget.cget("state")), "disabled", option)
            self.assertNotIn("championKillsAscendant", self.app.archive.require(16))
        self.assertEqual(self.app.collect_edits(), {})

    def test_v11_fields_still_work(self):
        self.app._set_field("boss-kills-1", "198")
        self.assertEqual(self.app.collect_edits().get("boss-kills-1"), "198")


@unittest.skipUnless(TK_AVAILABLE, f"这里打不开 tkinter：{TK_ERROR}")
class MissingFactionTest(unittest.TestCase):
    """存档里只有部分阵营（block13 被截短）时，缺的那些行整行置灰、不参与改动。"""

    def setUp(self):
        self._saved = {name: getattr(messagebox, name)
                       for name in ("askyesno", "showinfo", "showwarning", "showerror")}
        messagebox.askyesno = lambda *a, **k: True
        for name in ("showinfo", "showwarning", "showerror"):
            setattr(messagebox, name, lambda *a, **k: None)

        self.folder = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.folder.name, "player.gdc")
        archive = sample_archive()
        body = block13_body()
        body["factions"] = body["factions"][:5]      # 只保留下标 0..4 五个阵营
        archive.blocks = [Block(kind=1, size=0, body=block1_body()),
                          Block(kind=2, size=0, body=block2_body()),
                          Block(kind=8, size=0, body=block8_body()),
                          Block(kind=13, size=0, body=body),
                          Block(kind=16, size=0, body=block16_body())]
        with open(self.source, "wb") as handle:
            handle.write(archive.to_bytes())
        self.app = SaveEditorApp()
        self.app.open_file(self.source)
        self.app.update()

    def tearDown(self):
        for name, function in self._saved.items():
            setattr(messagebox, name, function)
        try:
            self.app.update()
        except tk.TclError:
            pass
        self.app.destroy()
        self.folder.cleanup()

    def test_present_factions_are_filled_and_missing_ones_are_disabled(self):
        self.assertEqual(self.app.vars["faction-1"].get(), "0")
        self.assertEqual(str(self.app.vars["faction-4"].cget("state")), "normal")
        missing = self.app.vars["faction-10"]
        self.assertEqual(str(missing.cget("state")), "disabled")
        self.assertEqual(missing.get(), "此存档没有该阵营")

    def test_disabled_rows_never_request_edits(self):
        """缺阵营的行即使被勾了解锁，也不会产生任何改动。"""
        self.app.vars["faction-10-unlocked"].set(True)
        self.assertEqual(self.app.collect_edits(), {})


if __name__ == "__main__":
    unittest.main()
