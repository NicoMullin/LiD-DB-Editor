"""The "rewards" setting: a list of master_reward rewards to draw from, each
with a weight, held as text that can never carry SQL - and the window that
edits it, which names every reward and says how many presents it makes."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from lid_db_manager.errors import ModLoadError
from lid_db_manager.rewards import read_rewards
from lid_db_manager.settings import (
    KIND_REWARDS,
    RewardEntry,
    is_setting_value,
    parse_rewards,
    parse_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"


def a_setting(**extra):
    entry = {"id": "rewards", "label": "Your list", "type": "rewards",
             "default": "RWD_MONEY_3000/1,RWD_ITEM_MAT_554/3", "default_label": "Two rewards"}
    entry.update(extra)
    [setting] = parse_settings([entry], "test")
    return setting


class TheValue(unittest.TestCase):
    def test_entries_read_back(self) -> None:
        self.assertEqual(parse_rewards("RWD_A/3, RWD_B/1", "x"), [RewardEntry("RWD_A", 3), RewardEntry("RWD_B", 1)])
        self.assertEqual(parse_rewards("", "x"), [])

    def test_a_reward_twice_is_kept_once_at_its_first_weight(self) -> None:
        self.assertEqual(parse_rewards("A/2,A/5,B/1", "x"), [RewardEntry("A", 2), RewardEntry("B", 1)])

    def test_anything_that_could_carry_sql_is_refused(self) -> None:
        for bad in ("A'/1", "A/1;DROP", "A/0", "A/101", "A/-1", "A B/1", "A/1,", "A", "A/1/2", "A/1000"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_rewards(bad, "x")

    def test_too_many(self) -> None:
        with self.assertRaises(ValueError):
            parse_rewards(",".join(f"R{n}/1" for n in range(1001)), "x")

    def test_state_json_keeps_it(self) -> None:
        self.assertTrue(is_setting_value("RWD_A/3,RWD_B/1"))
        self.assertFalse(is_setting_value("RWD_A/3;"))


class TheSetting(unittest.TestCase):
    def test_it_loads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.kind, KIND_REWARDS)
        self.assertTrue(setting.is_list)
        self.assertEqual(setting.default, "")
        self.assertEqual(setting.rewards(""), [RewardEntry("RWD_MONEY_3000", 1), RewardEntry("RWD_ITEM_MAT_554", 3)])

    def test_a_bad_default_is_refused_when_the_mod_loads(self) -> None:
        with self.assertRaises(ModLoadError):
            a_setting(default="RWD_A/1;")
        with self.assertRaises(ModLoadError):
            a_setting(noun="two words!")

    def test_what_it_writes_into_sql(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.sql(""), "('RWD_MONEY_3000', 1), ('RWD_ITEM_MAT_554', 3)")
        self.assertEqual(setting.sql("RWD_SKILL_011/7"), "('RWD_SKILL_011', 7)")
        self.assertEqual(a_setting(default="").sql(""), "(NULL, 0)")

    def test_how_it_reads(self) -> None:
        setting = a_setting()
        self.assertEqual(setting.display(""), "Two rewards")
        self.assertEqual(setting.display("RWD_A/1"), "1 reward")
        self.assertEqual(setting.display("RWD_A/1,RWD_B/4"), "2 rewards")

    def test_coerce_tidies_it(self) -> None:
        self.assertEqual(a_setting().coerce(" RWD_A/2 , RWD_A/9"), "RWD_A/2")


@unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
class EveryReward(unittest.TestCase):
    def test_each_is_named_from_what_it_gives(self) -> None:
        rewards = {reward.id: reward for reward in read_rewards(VANILLA_DB)}
        self.assertEqual(len(rewards), 880)
        expected = {
            "RWD_MONEY_3000": ("3,000 Kill Coins", "Kill Coins", 1),
            "RWD_MONEY_1": ("1 Kill Coin", "Kill Coins", 1),
            "RWD_SPIRIT_1000": ("1,000 SPLithium", "SPLithium", 1),
            "RWD_1VIP_3": ("Express one-day pass x3", "Express Pass", 1),
            "RWD_ITEM_MAT_554": ("D.O.D. ARMS Purple Metal x4", "Materials", 4),
            "RWD_LOSTBAG_017": ("Silver Lost Bag x3", "Lost Bags", 3),
            "RWD_BEAST_103": ("Golden Bird x3", "Beasts", 3),
            "RWD_MUSHROOM_111": ("Fun Fungus", "Mushrooms", 1),
            "RWD_SKILL_011": ("King Mantis decal", "Decals", 1),    # a booster: no stew copy
            "RWD_PART_ARM_001": ("Longsword RE", "Weapons and armour", 1),
            "RWD_BODY_001": ("Unlock fighter: All-rounder (G6)", "Fighters", 1),
        }
        for rwdid, (name, category, presents) in expected.items():
            with self.subTest(rwdid=rwdid):
                reward = rewards[rwdid]
                self.assertEqual((reward.name, reward.category, reward.presents), (name, category, presents))

    def test_nothing_is_left_unnamed(self) -> None:
        self.assertEqual([r.id for r in read_rewards(VANILLA_DB) if r.category == "Other"], [])


try:
    from PySide6.QtWidgets import QApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover - the laptop's Python has no PySide6
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT and VANILLA_DB.is_file(), "needs PySide6 and the vanilla database")
class TheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, value=""):
        from lid_db_manager.ui.rewards_editor import RewardsEditor

        return RewardsEditor(a_setting(), value, VANILLA_DB)

    def test_it_opens_on_the_list(self) -> None:
        editor = self.editor()
        self.assertEqual(editor.entries(), a_setting().rewards(""))
        self.assertEqual(editor.value(), "")                    # still the default
        self.assertEqual(editor.table.item(0, 0).text(), "3,000 Kill Coins")
        self.assertEqual(editor.table.item(1, 2).text(), "4")   # 4 metals: 4 presents
        self.assertEqual([editor.table.item(r, 4).text() for r in (0, 1)], ["25.0%", "75.0%"])
        self.assertIn("1 of them more than one present", editor.count.text())

    def test_weights_adding_and_removing(self) -> None:
        editor = self.editor()
        editor.table.cellWidget(0, 3).setValue(3)
        self.assertEqual([editor.table.item(r, 4).text() for r in (0, 1)], ["50.0%", "50.0%"])
        editor.add(["RWD_SPIRIT_1000", "RWD_MONEY_3000"])       # one already listed: kept as it is
        self.assertEqual(editor.value(), "RWD_MONEY_3000/3,RWD_ITEM_MAT_554/3,RWD_SPIRIT_1000/1")
        editor.table.selectRow(1)
        editor.remove_highlighted()
        self.assertEqual([e.id for e in editor.entries()], ["RWD_MONEY_3000", "RWD_SPIRIT_1000"])

    def test_back_to_the_default_is_the_default_again(self) -> None:
        editor = self.editor("RWD_SPIRIT_1000/2")
        editor.fill(editor.default)
        self.assertEqual(editor.value(), "")

    def test_an_empty_list_cannot_be_used(self) -> None:
        editor = self.editor()
        editor.fill([])
        self.assertFalse(editor.ok_button.isEnabled())

    def test_a_reward_no_longer_in_the_database_is_shown(self) -> None:
        editor = self.editor("NOT_A_REWARD/1")
        self.assertIn("not in your database", editor.table.item(0, 0).text())


if __name__ == "__main__":
    unittest.main()
