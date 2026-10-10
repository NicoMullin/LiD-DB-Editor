"""The Events and Seasons mod: the online game's events, without the calendar.

Every part is plain SQL on masters.db, so the checks run it through the
manager on a copy of the real vanilla database - each setting, then the mod
switched off again, which has to leave every table it touched as it was.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from lid_db_manager.manager import Manager
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.paths import AppPaths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOD = PROJECT_ROOT / "mods" / "Events and Seasons"
MOD_ID = "Events and Seasons"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.0" / "masters.db"
TABLES = ("master_event_schedule", "master_skillgacha", "master_skillgacha_odds", "master_shop_product",
          "master_shop_product_price", "master_text", "master_mushroom_gen", "master_mushroom_gen_odds",
          "master_model_schedule", "master_model_group", "master_quest", "master_quest_param_str",
          "master_quest_param_int", "master_quest_param_text", "master_quest_param_desc_text")
QUEST_TABLES = ("master_quest", "master_quest_param_str", "master_quest_param_int", "master_quest_param_text",
                "master_quest_param_desc_text")
SEASONS = {
    "MUSHROOM_SPRING": {"MSR_200", "MSR_201", "MSR_202"},
    "MUSHROOM_SUMMER": {"MSR_306", "MSR_307", "MSR_308"},
    "MUSHROOM_AUTUMN": {"MSR_309", "MSR_310", "MSR_311"},
    "MUSHROOM_WINTER": {"MSR_312", "MSR_313", "MSR_314"},
}
SEASONAL = set().union(*SEASONS.values())
TRAVIS_TO_WARDOGS = {
    "MODEL_09": "MODEL_07", "MODEL_091": "MODEL_071", "MODEL_10": "MODEL_072", "MODEL_101": "MODEL_073",
    "MODEL_11": "MODEL_08", "MODEL_111": "MODEL_081", "MODEL_12": "MODEL_082", "MODEL_121": "MODEL_083",
}


class TheMod(unittest.TestCase):
    def test_free_continues_are_back_and_off_by_default(self) -> None:
        # Taken out in 0.1.2 for a crash on arriving on a floor with a free
        # continue waiting. That crash was Enemies Left 0.2.0 reading the HUD
        # font as the wrong kind of array (fixed in 0.2.1), so they are back.
        mod = load_mod_folder(MOD)
        setting = {s.id: s for s in mod.settings}["free_continues"]
        self.assertEqual((setting.default, setting.minimum, setting.maximum), (0, 0, 10))
        self.assertIn("'FREE_CONTINUE'", "\n".join(p.sql for p in mod.patches))

    def test_it_loads_and_is_sql_only(self) -> None:
        mod = load_mod_folder(MOD)
        self.assertEqual({p.type for p in mod.patches}, {"raw_sql"})
        self.assertEqual(mod.requires_check_off, [])

    def test_the_stew_draws_from_its_own_list(self) -> None:
        # lid-stewpull.dll gives a fresh pick from its own list only to
        # stews whose odds list is named SKLGACH_RANDOM_*, and the server fills
        # Check Lineup from the odds rows named by the stew's id - so the two
        # are the same name
        sql = "\n".join(p.sql for p in load_mod_folder(MOD).patches)
        self.assertIn("'SKLGACH_RANDOM_FEST', 'SKLGACH_RANDOM_FEST', 'NORMAL'", sql)


@unittest.skipUnless(VANILLA_DB.is_file(), f"no vanilla database at {VANILLA_DB}")
class OnTheVanillaDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.stock_db = Path(cls._tmp.name) / "stock.db"
        shutil.copy2(VANILLA_DB, cls.stock_db)
        cls.stock = cls.read(cls.stock_db)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        root = Path(self._run.name)
        self.paths = AppPaths(root).ensure()
        shutil.copytree(MOD, self.paths.mods_dir / MOD.name)
        self.db = root / "game" / "masters.db"
        self.db.parent.mkdir()
        shutil.copy2(self.stock_db, self.db)
        self.manager = Manager(self.paths)
        self.manager.set_db_path(self.db)
        self.manager.set_enabled(MOD_ID, True)

    def tearDown(self) -> None:
        self._run.cleanup()

    @staticmethod
    def read(db: Path) -> dict:
        con = sqlite3.connect(str(db))
        try:
            return {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in TABLES}
        finally:
            con.close()

    def save(self, **settings) -> None:
        for name, value in settings.items():
            self.manager.set_mod_setting(MOD_ID, name, value)
        report = self.manager.save_mod_list()
        self.assertTrue(report.ok, report)

    def rows(self, sql: str, *args) -> list:
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def events(self) -> dict:
        return {i: (t, v, s, e) for i, t, v, s, e in self.rows(
            "SELECT id, type, val0, start, end FROM master_event_schedule WHERE id LIKE 'LID_%'")}

    def test_by_default_the_hub_festivals_and_the_stew(self) -> None:
        self.save()
        forever = (0, 2147483647)
        self.assertEqual(self.events(), {
            "LID_EVENT_MUSHROOM_FES": ("MUSHROOM_FES", "", *forever),
            "LID_EVENT_CHOKU_FES": ("CHOKU_FES", "", *forever),
            "LID_EVENT_TDM_FES": ("TDM_FES", "", *forever),
        })
        [stew] = self.rows("SELECT odds_id, is_fest, fest_text, fest_detail_text, banner, menu_text_id, "
                           "confirm_text_id, icon_type, product_id FROM master_skillgacha WHERE id = 'SKLGACH_RANDOM_FEST'")
        self.assertEqual(stew, ("SKLGACH_RANDOM_FEST", 1, "MSHR_KAIKAN.TEXT_MUSHROOM_TITLE_1",
                                "MSHR_KAIKAN.TXT_LID_FEST_BOOST", "lid_super_stew_festival",
                                "MSHR_KAIKAN.TXT_LID_GACHA_FEST", "MSHR_KAIKAN.TXT_MS_GACHA", "FEST",
                                "PRD_SKILL_GACHA_FEST"))
        self.assertEqual(self.rows("SELECT price, spirit, medal FROM master_shop_product_price "
                                   "WHERE id = 'PRD_SKILL_GACHA_FEST'"), [(100000, 0, 0)])
        self.assertEqual(len(self.rows("SELECT * FROM master_shop_product WHERE id = 'PRD_SKILL_GACHA_FEST'")), 1)
        # every three-star and up decal of the normal stew, four and five stars at 3x
        odds = dict(self.rows("SELECT sklid, odds FROM master_skillgacha_odds WHERE id = 'SKLGACH_RANDOM_FEST'"))
        normal = {sklid: (o, stars) for sklid, o, stars in self.rows(
            "SELECT o.sklid, o.odds, s.rarity FROM master_skillgacha_odds o JOIN master_skill s ON s.id = o.sklid "
            "WHERE o.id = 'SKLGACH_NORMAL_OFFLINE' AND o.odds > 0 AND s.rarity >= 3")}
        self.assertEqual(set(odds), set(normal))
        for sklid, (o, stars) in normal.items():
            self.assertEqual(odds[sklid], o * (3 if stars >= 4 else 1), sklid)
        # the words in every language the game has
        langs = {lang for (lang,) in self.rows("SELECT DISTINCT lang FROM master_text")}
        for tid in ("TXT_LID_GACHA_FEST", "TXT_LID_FEST_BOOST"):
            words = dict(self.rows("SELECT lang, txt FROM master_text WHERE sct = 'MSHR_KAIKAN' AND id = ?", tid))
            self.assertEqual(set(words), langs, tid)
        self.assertEqual(self.rows("SELECT txt FROM master_text WHERE id = 'TXT_LID_FEST_BOOST' AND lang = 'int'"),
                         [("3x Boosted Rate for ★★★★ and Up!",)])
        # nothing else
        after = self.read(self.db)
        for table in ("master_mushroom_gen", "master_mushroom_gen_odds", "master_model_schedule",
                      "master_model_group"):
            self.assertEqual(after[table], self.stock[table], table)

    def test_the_boost_never_compounds(self) -> None:
        def boosted() -> set:
            return set(self.rows(
                "SELECT f.sklid, f.odds FROM master_skillgacha_odds f JOIN master_skill s ON s.id = f.sklid "
                "WHERE f.id = 'SKLGACH_RANDOM_FEST' AND s.rarity >= 4"))

        self.save(fest_boost=3)
        first = boosted()
        self.save(fest_boost=5)
        self.assertNotEqual(boosted(), first)
        self.save(fest_boost=3)
        self.assertEqual(boosted(), first)

    def test_paying_with_death_metals(self) -> None:
        self.save(fest_currency="dm", fest_price_dm=25, fest_boost=1, fest_banner=False)
        self.assertEqual(self.rows("SELECT price, spirit, medal FROM master_shop_product_price "
                                   "WHERE id = 'PRD_SKILL_GACHA_FEST'"), [(0, 0, 25)])
        self.assertEqual(self.rows("SELECT confirm_text_id, fest_detail_text, banner FROM master_skillgacha "
                                   "WHERE id = 'SKLGACH_RANDOM_FEST'"), [("MSHR_KAIKAN.TXT_MS_GACHA_DM", "", "")])

    def test_the_bonuses(self) -> None:
        self.save(mushroom_fes=False, choku_fes=False, tdm_fes=False, fest_stew=False,
                  free_continues=3, recovery_discount=50, part_sale=True)
        self.assertEqual({i: (t, v) for i, (t, v, _, _) in self.events().items()}, {
            "LID_EVENT_FREE_CONTINUE": ("FREE_CONTINUE", "3"),
            "LID_EVENT_RECOVERY_DISCOUNT": ("RECOVERY_DISCOUNT", "50"),
            "LID_EVENT_PART_SALE_01": ("PART_SALE", "PT_ARM_WP001_0B1"),
            "LID_EVENT_PART_SALE_02": ("PART_SALE", "PT_ARM_WP011_0B1"),
        })
        self.assertEqual(self.rows("SELECT * FROM master_skillgacha WHERE id = 'SKLGACH_RANDOM_FEST'"), [])

    def mushrooms(self, odds_id: str | None = None) -> dict:
        if odds_id is None:
            return {(i, m): f for i, m, f in self.rows("SELECT id, msrid, freq FROM master_mushroom_gen")}
        return {(i, m): f for i, m, f in self.rows(
            "SELECT id, msrid, freq FROM master_mushroom_gen_odds WHERE odds_id = ?", odds_id)}

    def stock_mushrooms(self, odds_id: str | None = None) -> dict:
        if odds_id is None:
            return {(i, m): f for i, m, f in self.stock["master_mushroom_gen"]}
        return {(i, m): f for o, i, m, f in self.stock["master_mushroom_gen_odds"] if o == odds_id}

    def test_one_seasons_mushrooms_all_year(self) -> None:
        self.save(seasons="spring")
        spring = {k: f for k, f in self.stock_mushrooms("MUSHROOM_SPRING").items() if k[1] in SEASONS["MUSHROOM_SPRING"]}
        self.assertTrue(spring)
        # the months between seasons
        self.assertEqual(self.mushrooms(), {**self.stock_mushrooms(), **spring})
        # spring's own months, untouched
        self.assertEqual(self.mushrooms("MUSHROOM_SPRING"), self.stock_mushrooms("MUSHROOM_SPRING"))
        # the other seasons' months: spring's mushrooms, theirs never picked
        for odds_id in ("MUSHROOM_SUMMER", "MUSHROOM_AUTUMN", "MUSHROOM_WINTER"):
            got = self.mushrooms(odds_id)
            own = {k: 0 for k in self.stock_mushrooms(odds_id) if k[1] in SEASONS[odds_id]}
            self.assertTrue(own)
            self.assertEqual(got, {**self.stock_mushrooms(odds_id), **own, **spring}, odds_id)

    def test_all_four_seasons_all_year(self) -> None:
        self.save(seasons="all")
        quarter = {}
        for odds_id, mine in SEASONS.items():
            quarter.update({k: max(1, f // 4) for k, f in self.stock_mushrooms(odds_id).items() if k[1] in mine})
        self.assertEqual({m for _, m in quarter}, SEASONAL)
        self.assertEqual(self.mushrooms(), {**self.stock_mushrooms(), **quarter})
        for odds_id in SEASONS:
            self.assertEqual(self.mushrooms(odds_id), {**self.stock_mushrooms(odds_id), **quarter}, odds_id)

    def groups(self) -> list:
        return sorted(self.rows("SELECT group_id, model_hater_id, freq FROM master_model_group"))

    def test_travis_imposters(self) -> None:
        self.save(collab="travis")
        self.assertEqual(self.rows("SELECT pattern FROM master_model_schedule"), [("D",)])
        self.assertEqual(self.groups(), sorted(self.stock["master_model_group"]))

    def event_groups(self, rows) -> dict:
        return {(g, m): f for g, m, f in rows if g[-1] in "DEF" and g.startswith("MH_0")}

    def test_wardogs_take_the_travis_imposters_places(self) -> None:
        self.save(collab="wardogs")
        self.assertEqual(self.rows("SELECT pattern FROM master_model_schedule"), [("D",)])
        stock = self.event_groups(self.stock["master_model_group"])
        want = dict(stock)
        for (g, m), f in stock.items():
            if m in TRAVIS_TO_WARDOGS:
                want[(g, m)] = 0
                want[(g, TRAVIS_TO_WARDOGS[m])] = f
        self.assertEqual(self.event_groups(self.groups()), want)
        self.assertEqual(len(want) - len(stock), 24)        # 8 tiers in 3 patterns

    def test_both_share_the_travis_imposters_weight(self) -> None:
        self.save(collab="both")
        stock = self.event_groups(self.stock["master_model_group"])
        got = self.event_groups(self.groups())
        for (g, m), f in stock.items():
            if m in TRAVIS_TO_WARDOGS:
                self.assertEqual(got[(g, m)] + got[(g, TRAVIS_TO_WARDOGS[m])], f, g)
                self.assertGreater(got[(g, m)], 0)
                self.assertGreater(got[(g, TRAVIS_TO_WARDOGS[m])], 0)

    def quests(self, event_type: str) -> list:
        return self.rows("SELECT qid, start_date, end_date, old_flg FROM master_quest WHERE event_type = ? "
                         "ORDER BY start_date, qid", event_type)

    def test_the_weekly_and_season_quests_run_on_to_2033(self) -> None:
        stock = {}
        con = sqlite3.connect(str(self.stock_db))
        for event_type in ("QUESTEVENT_WEEK", "QUESTEVENT_SEASON"):
            stock[event_type] = con.execute("SELECT qid, start_date, end_date FROM master_quest "
                                            "WHERE event_type = ?", (event_type,)).fetchall()
        con.close()
        self.save()
        week = 7 * 86400
        for event_type, rows in stock.items():
            got = {q: (s, e, old) for q, s, e, old in self.quests(event_type)}
            self.assertEqual(len(got), len(rows))
            for qid, start, end in rows:
                self.assertEqual(got[qid], (start + 365 * week, end + 365 * week, 0), qid)
            # one unbroken calendar from where the shipped one stopped
            windows = sorted({(s, e) for s, e, _ in got.values()})
            self.assertEqual(windows[0][0], 1767607200)               # 2026-01-05 10:00 UTC
            self.assertEqual(windows[-1][1], 1767607200 + 365 * week)  # 2033-01-03 10:00 UTC
            self.assertTrue(all(a[1] == b[0] for a, b in zip(windows, windows[1:])), event_type)
        # the week of 2026-10-08 is the week that ran from 2019-10-07
        now = 1791417600                                              # 2026-10-08 12:00 UTC
        live = [q for q, s, e, _ in self.quests("QUESTEVENT_WEEK") if s <= now < e]
        self.assertTrue(live)
        self.assertTrue(all(q.startswith("SEASON_20191007_") for q in live), live)
        # every other quest the game ships is left alone
        self.assertEqual(self.rows("SELECT COUNT(*) FROM master_quest WHERE old_flg = 1"), [(0,)])

    def test_a_second_save_does_not_move_the_quests_again(self) -> None:
        self.save()
        first = self.quests("QUESTEVENT_WEEK") + self.quests("QUESTEVENT_SEASON")
        self.save(collab="travis", seasons="all", quest_days=3)
        self.assertEqual(self.quests("QUESTEVENT_WEEK") + self.quests("QUESTEVENT_SEASON"), first)

    def stock_rows(self, sql: str) -> list:
        con = sqlite3.connect(str(self.stock_db))
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_the_quests_can_be_left_off(self) -> None:
        self.save(quest_mode="off")
        for table in QUEST_TABLES:
            self.assertEqual(self.read(self.db)[table], self.stock[table], table)

    def test_the_pick_holds_the_43_quests_behind_the_calendar(self) -> None:
        pick = {s.id: s for s in load_mod_folder(MOD).settings}["quest_pick"]
        self.assertEqual(len(pick.options), 43)
        self.assertEqual(pick.default_picks, tuple(o.value for o in pick.options))
        groups = [o.group for o in pick.options]
        self.assertEqual((groups.count("Weekly"), groups.count("Season")), (37, 6))

    def test_as_online_with_some_quests_unticked(self) -> None:
        keep = ["SEASON_20190107_7", "SEASON_20190107_1"]       # Screamers x50 weekly, x1000 season
        self.save(quest_pick=",".join(keep))
        live = self.rows("SELECT qid, name, \"desc\", prgmax, event_type FROM master_quest WHERE old_flg = 0 "
                         "AND event_type IN ('QUESTEVENT_WEEK', 'QUESTEVENT_SEASON')")
        self.assertEqual(len(live), 365 + 28)
        self.assertEqual({(n, d, p, e) for _, n, d, p, e in live}, set(self.stock_rows(
            "SELECT name, \"desc\", prgmax, event_type FROM master_quest WHERE qid IN ('%s')" % "','".join(keep))))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM master_quest WHERE old_flg = 1"), [(5983 - 393,)])

    def copies(self, prefix: str) -> list:
        return self.rows("SELECT qid, start_date, end_date, old_flg FROM master_quest WHERE qid LIKE ? "
                         "ORDER BY start_date, qid", prefix + "%")

    def test_a_new_draw_every_few_days(self) -> None:
        picked = [f"SEASON_20190107_{n}" for n in range(1, 11)]
        self.save(quest_mode="rotate", quest_days=3, quest_count=4, quest_pick=",".join(picked))
        copies = self.copies("LID_Q_")
        period = 3 * 86400
        now = int(time.time())
        first = (now - 1767607200) // period
        periods = {}
        for qid, start, end, old in copies:
            _, _, g, template = qid.split("_", 3)
            self.assertEqual((start, end, old), (1767607200 + int(g) * period, 1767607200 + (int(g) + 1) * period, 0))
            periods.setdefault(int(g), []).append(template)
        # from the period the save falls in to a year ahead, each with 4 different picked quests
        self.assertEqual(sorted(periods), list(range(first, first + (365 + 2) // 3 + 1)))
        for g, templates in periods.items():
            self.assertEqual(len(set(templates)), 4, g)
            self.assertLessEqual(set(templates), set(picked))
        # drawn, not the same four every time: every picked quest comes up, none hogs it
        counts = {q: sum(q in t for t in periods.values()) for q in picked}
        self.assertTrue(all(len(periods) * 4 / 10 * 0.5 < n < len(periods) * 4 / 10 * 1.5 for n in counts.values()),
                        counts)
        self.assertGreater(len({tuple(sorted(t)) for t in periods.values()}), len(periods) // 2)
        # each copy brings its quest's targets and texts
        for table in QUEST_TABLES[1:]:
            got = dict(((q.split("_", 3)[3], n), v) for q, n, v in self.rows(
                f"SELECT qid, no, val FROM {table} WHERE qid LIKE 'LID_Q_%'"))
            want = {(q, n): v for q, n, v in self.stock_rows(
                f"SELECT qid, no, val FROM {table} WHERE qid IN ('%s')" % "','".join(picked))}
            self.assertLessEqual(set(got.items()), set(want.items()), table)
        # a later save writes the same draw
        self.save(collab="travis")
        self.assertEqual(self.copies("LID_Q_"), copies)

    def test_all_of_them_all_the_time(self) -> None:
        picked = ["SEASON_20190107_9", "SEASON_20190107_2"]
        self.save(quest_mode="all", quest_pick=",".join(picked))
        self.assertEqual(self.copies("LID_Q_"), [(f"LID_Q_ALL_{q}", -1, -1, 0) for q in sorted(picked)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM master_quest WHERE old_flg = 0 "
                                   "AND event_type IN ('QUESTEVENT_WEEK', 'QUESTEVENT_SEASON')"), [(2,)])
        for table in QUEST_TABLES[1:]:
            self.assertEqual(
                sorted((q[len("LID_Q_ALL_"):], n, v) for q, n, v in self.rows(
                    f"SELECT qid, no, val FROM {table} WHERE qid LIKE 'LID_Q_ALL_%'")),
                sorted(self.stock_rows(f"SELECT qid, no, val FROM {table} WHERE qid IN ('%s')"
                                       % "','".join(picked))), table)

    def test_saving_again_with_nothing_changed(self) -> None:
        # The mod runs again over what it wrote, not over a stock table: a save
        # with no setting changed (or only another mod's) takes nothing off first.
        for mode in ("rotate", "all"):
            with self.subTest(mode=mode):
                self.save(quest_mode=mode, quest_days=1, quest_count=15)
                before = self.read(self.db)
                self.save()
                self.assertEqual(self.read(self.db), before)

    def test_a_later_days_save_leaves_no_old_periods_behind(self) -> None:
        self.save(quest_mode="rotate", quest_days=1, quest_count=2)
        con = sqlite3.connect(str(self.db))
        try:     # a copy from a period long gone, as an earlier day's save would have left
            cols = [c for _, c, *_ in con.execute("PRAGMA table_info(master_quest)") if c != "qid"]
            quoted = ", ".join(f'"{c}"' for c in cols)
            con.execute(f"INSERT INTO master_quest (qid, {quoted}) SELECT 'LID_Q_1_OLD', {quoted} "
                        "FROM master_quest WHERE qid LIKE 'LID_Q_%' LIMIT 1")
            con.commit()
        finally:
            con.close()
        self.save()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM master_quest WHERE qid = 'LID_Q_1_OLD'"), [(0,)])
        self.assertTrue(self.copies("LID_Q_"))

    def test_switching_it_off_puts_every_table_back(self) -> None:
        self.save(fest_currency="dm", recovery_discount=30, part_sale=True,
                  seasons="all", collab="both")
        self.assertNotEqual(self.read(self.db), self.stock)
        self.save(seasons="winter", collab="wardogs", fest_currency="kc", quest_mode="rotate", quest_days=2)
        self.manager.set_enabled(MOD_ID, False)
        self.assertTrue(self.manager.save_mod_list().ok)
        self.assertEqual(self.read(self.db), self.stock)


if __name__ == "__main__":
    unittest.main()
