"""The save editor window, opened from the Saves tab with Edit...

One page per part of the save (Account, Fighters with their Death Bags and decals, Storage Box, Research,
Decals, Weapon Mastery, Mushroom Stew, Quests, Dates, Run; more to come). Every number
is held to what the game can store - the banks' caps follow their levels, a
fighter's stats follow its class, grade and limit break - and nothing is
written until Save changes, which lists every change first. The save is backed
up before it is written, and the game must be closed. See saveedit.py and
savefighters.py.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import (saveedit, savebag, savedates, savedecals, savefighters, saveheaven, savequests, saveresearch,
               saveroster, savestew, savestorage)
from ..savesummary import STATES, STATS
from .bag_panel import BagPanel
from .dates_page import DatesPage
from .decal_pages import DecalStockPage, MasteryPage, WornDecalsPanel
from .edit_steps import Steps
from .freezer import BuyDialog
from .research_page import ResearchPage
from .run_page import RunPage
from .stew_quest_pages import QuestsPage, StewPage
from .storage_page import StoragePage

ROLE_CID = Qt.ItemDataRole.UserRole


class SaveEditorDialog(QDialog):
    def __init__(self, manager, entry_id: str, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.entry = manager.library.get(entry_id)
        self.document = manager.open_save(entry_id)
        # The game's data as this save's own mods make it - their new items,
        # their raised limits - not whatever the game has applied right now.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.game_data = manager.save_game_data(entry_id)
            data_note = self.game_data.describe()
        except (saveedit.EditError, OSError) as exc:
            self.game_data = None
            data_note = f"the game's data as it is - this save's own mods could not be put together ({exc})"
        finally:
            QApplication.restoreOverrideCursor()
        db_path = self.game_data.db_path if self.game_data else None
        self.limits = manager.save_limits(db_path)
        self.rules = savefighters.Rules.read(db_path or manager.db_path)
        self.catalog = savestorage.Catalog.read(db_path or manager.db_path)
        self.gear_rules = savebag.GearRules.read(db_path or manager.db_path)
        self.decal_catalog = savedecals.DecalCatalog.read(db_path or manager.db_path)
        self.research_rules = saveresearch.ResearchRules.read(db_path or manager.db_path, self.catalog)
        self.stew_odds = savestew.StewOdds.read(db_path or manager.db_path)
        self.quest_catalog = savequests.QuestCatalog.read(db_path or manager.db_path)
        self.freezer = saveroster.Freezer.read(db_path or manager.db_path)
        self.work = Steps(self.document)    # the freezer's, Storage Box's and Death Bags' changes
        self.written = None                 # the entry, once saved
        self.setWindowTitle(f"Edit save - {self.entry.nickname}")
        self.resize(900, 680)

        _, _, in_game = manager._edit_target(entry_id)
        where = ("This is the save in the game: it is changed there, and in its slot here."
                 if in_game else "This save is in the library only; it is changed in its slot here.")
        intro = QLabel(f"<b>{html.escape(self.entry.nickname)}</b> - {where}<br>Nothing is written until "
                       "<b>Save changes</b>, which lists every change first and backs the save up. Close "
                       "the game before you save.<br><span style='color:gray'>Limits and lists come from "
                       f"{html.escape(data_note)}.</span>")
        intro.setWordWrap(True)

        self.pages = QTabWidget()
        self.pages.addTab(self._account_page(), "Account")
        self.pages.addTab(self._fighters_page(), "Fighters")
        self.storage = None
        try:
            self.storage = StoragePage(self.document, self.catalog, self.work)
            self.storage.changed.connect(self._update)
            self.storage.changed.connect(self.bag_panel.refresh)        # what can be taken into a bag
            self.bag_panel.changed.connect(self.storage.stale)          # what was put in the box
            self.pages.addTab(self.storage, "Storage Box")
        except saveedit.EditError as exc:
            missing = QLabel(f"This save's Storage Box cannot be edited: {html.escape(str(exc))}")
            missing.setWordWrap(True)
            self.pages.addTab(missing, "Storage Box")
        self.research = None
        try:
            self.research = ResearchPage(self.document, self.research_rules)
            self.research.changed.connect(self._update)
            self.pages.addTab(self.research, "Research")
        except saveedit.EditError as exc:
            missing = QLabel(f"This save's research cannot be edited: {html.escape(str(exc))}")
            missing.setWordWrap(True)
            self.pages.addTab(missing, "Research")
        self.decal_stock = DecalStockPage(self.document, self.decal_catalog)
        self.decal_stock.changed.connect(self._update)
        self.pages.addTab(self.decal_stock, "Decals")
        self.mastery = MasteryPage(self.document, self.decal_catalog)
        self.mastery.changed.connect(self._update)
        self.pages.addTab(self.mastery, "Weapon Mastery")
        self.stew = StewPage(self.document, self.stew_odds, self.decal_catalog)
        self.stew.changed.connect(self._update)
        self.pages.addTab(self.stew, "Mushroom Stew")
        self.quests = QuestsPage(self.document, self.quest_catalog)
        self.quests.changed.connect(self._update)
        self.pages.addTab(self.quests, "Quests")
        show_timers = savedates.timers_shown(self.game_data.mods if self.game_data else [], self.document)
        self.dates = DatesPage(self.document, self.catalog, show_timers)
        self.dates.changed.connect(self._update)
        self.pages.addTab(self.dates, "Dates")
        self.run = RunPage(self.document, self.limits,
                           saveheaven.Heaven.read(db_path or manager.db_path, self.catalog))
        self.run.changed.connect(self._update)
        self.pages.addTab(self.run, "Run")

        self.changes_label = QLabel()
        self.changes_label.setObjectName("dim")
        save = QPushButton("Save changes")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.changes_label, 1)
        bottom.addWidget(close)
        bottom.addWidget(save)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.pages, 1)
        layout.addLayout(bottom)
        self._update()

    # -- the Account page ---------------------------------------------------

    def _account_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.boxes: dict[str, QSpinBox] = {}
        self.caps: dict[str, QLabel] = {}
        bounds = saveedit.account_bounds(self.document, self.limits)
        for f in saveedit.ACCOUNT_FIELDS:
            box = QSpinBox()
            low, high = bounds[f.key]
            current = self.document.number(f.path)
            # opening the editor changes nothing: a value already past the limit is shown as it is
            box.setRange(min(low, current), max(high, current))
            box.setGroupSeparatorShown(True)
            box.setValue(current)
            box.setToolTip(f.help)
            box.valueChanged.connect(self._update)
            self.boxes[f.key] = box
            row = QHBoxLayout()
            row.addWidget(box, 1)
            if f.key in ("kill_coins", "splithium"):
                cap = QLabel()
                cap.setObjectName("dim")
                self.caps[f.key] = cap
                fill = QPushButton("Fill")
                fill.setToolTip("As much as the bank holds at its level")
                fill.clicked.connect(lambda _=False, k=f.key: self.boxes[k].setValue(self.boxes[k].maximum()))
                row.addWidget(cap)
                row.addWidget(fill)
            form.addRow(f"{f.label}:", row)
            if f.help:
                note = QLabel(f.help)
                note.setObjectName("dim")
                note.setWordWrap(True)
                form.addRow("", note)
        held = self.document.number(("soul", "free_money"))
        if held > bounds["kill_coins"][1]:
            warn = QLabel(f"This save holds {held:,} Kill Coins, more than its bank's level holds - the game "
                          "would cut it down. Raise the bank level, or the amount is set to the cap when saved.")
            warn.setWordWrap(True)
            form.addRow("", warn)
        paid = self.document.number(("user", "paid_medal"))
        if paid:
            form.addRow("", QLabel(f"Bought Death Metals, left as they are: {paid:,}"))
        return page

    def _values(self) -> dict:
        return {key: box.value() for key, box in self.boxes.items()}

    # -- the Fighters page ----------------------------------------------------

    def _fighters_page(self) -> QWidget:
        self.fighters = {f.cid: f for f in savefighters.fighters(self.document)}
        self.edits: dict[str, dict] = {}            # cid -> the page's values for that fighter
        self.shown_cid: str | None = None
        self._loading = False
        self._editable = False

        self.fighter_list = QListWidget()
        for f in self.fighters.values():
            item = QListWidgetItem(self._fighter_line(f))
            item.setData(ROLE_CID, f.cid)
            self.fighter_list.addItem(item)
        self.fighter_list.currentItemChanged.connect(lambda *_: self._show_fighter())
        max_all = QPushButton("Max all fighters")
        max_all.setToolTip("Every fighter that can be changed to the highest grade and limit break, with every "
                           "stat, decal slot, Death Bag space and rage bar at the top. Dead, kidnapped and "
                           "defending fighters, and one out on a run, are left as they are.")
        max_all.clicked.connect(self.max_all_fighters)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.fighter_list, 1)
        left_layout.addWidget(max_all)
        left_layout.addWidget(self._freezer_box())

        self.f_lock = QLabel()
        self.f_lock.setWordWrap(True)
        self.f_name = QLineEdit()
        self.f_name.setMaxLength(savefighters.NAME_MAX)
        self.f_class = QComboBox()
        for code in self.rules.classes():
            self.f_class.addItem(self.rules.names.class_name(code), code)
        self.f_grade = QSpinBox()
        self.f_lb = QLabel()
        self.f_body = QComboBox()
        for body, _ in self.rules.bodies:
            self.f_body.addItem(savefighters.body_label(body, self.rules), body)
        self.f_mask = QComboBox()
        self.f_level = QLabel()
        self.f_stats: dict[str, QSpinBox] = {}
        self.f_bonus: dict[str, QSpinBox] = {}
        self.f_slots: dict[str, QSpinBox] = {k: QSpinBox() for k in ("decal_slots", "death_bag", "rage")}
        for widget in (self.f_name,):
            widget.textChanged.connect(self._fighter_changed)
        for widget in (self.f_class, self.f_body, self.f_mask):
            widget.currentIndexChanged.connect(self._fighter_changed)
        self.f_grade.valueChanged.connect(self._fighter_changed)

        who = QFormLayout()
        who.addRow("Name:", self.f_name)
        who.addRow("Class:", self.f_class)
        who.addRow("Grade:", self.f_grade)
        who.addRow("Limit Break:", self.f_lb)
        who.addRow("Model:", self.f_body)
        who.addRow("Gas mask:", self.f_mask)
        who.addRow("Level:", self.f_level)

        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Stat</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Level</b>"), 0, 1)
        bonus_head = QLabel("<b>Bonus</b>")
        bonus_head.setToolTip("Rolled for each stat when the fighter is bought: 5, 10, 15 or 20% of the grade's "
                              "cap (or nothing), rounded down. That stat's cap is that much higher at limit break 0; "
                              "it no longer counts once the fighter is uncapped. Only the values the game can roll "
                              "are offered.")
        grid.addWidget(bonus_head, 0, 2)
        for row, key in enumerate(STATS, start=1):
            stat, bonus = QSpinBox(), QSpinBox()
            bonus.setPrefix("+")
            stat.valueChanged.connect(self._fighter_changed)
            bonus.valueChanged.connect(lambda _v, k=key: self._bonus_stepped(k))
            bonus.valueChanged.connect(self._fighter_changed)
            self.f_stats[key], self.f_bonus[key] = stat, bonus
            grid.addWidget(QLabel(savefighters.STAT_LABELS[key]), row, 0)
            grid.addWidget(stat, row, 1)
            grid.addWidget(bonus, row, 2)
        slots = QFormLayout()
        for key, label in (("decal_slots", "Decal slots:"), ("death_bag", "Death Bag space:"),
                           ("rage", "Rage bars:")):
            self.f_slots[key].valueChanged.connect(self._fighter_changed)
            slots.addRow(label, self.f_slots[key])
        self.f_ranges = QLabel()
        self.f_ranges.setObjectName("dim")
        self.f_ranges.setWordWrap(True)

        self.f_max = QPushButton("Max this fighter")
        self.f_max.setToolTip("The highest grade and limit break, with every stat and slot at the top")
        self.f_max.clicked.connect(self.max_fighter)
        self.f_undo = QPushButton("Undo this fighter's changes")
        self.f_undo.clicked.connect(self.undo_fighter)
        buttons = QHBoxLayout()
        buttons.addWidget(self.f_max)
        buttons.addWidget(self.f_undo)
        buttons.addStretch(1)

        form = QWidget()
        self.f_form = form
        form_layout = QVBoxLayout(form)
        form_layout.addWidget(self.f_lock)
        form_layout.addLayout(who)
        form_layout.addLayout(grid)
        form_layout.addLayout(slots)
        form_layout.addWidget(self.f_ranges)
        form_layout.addLayout(buttons)
        form_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        self.bag_panel = BagPanel(self.document, self.catalog, self.gear_rules, self.fighters, self._bag_room,
                                  self._wearer, self.work)
        self.bag_panel.changed.connect(self._bag_changed)
        bag_scroll = QScrollArea()
        bag_scroll.setWidgetResizable(True)
        bag_scroll.setWidget(self.bag_panel)
        self.worn_decals = WornDecalsPanel(self.document, self.decal_catalog, self.fighters,
                                           lambda cid: self._now(cid)["decal_slots"])
        self.worn_decals.changed.connect(self._decals_changed)
        decal_scroll = QScrollArea()
        decal_scroll.setWidgetResizable(True)
        decal_scroll.setWidget(self.worn_decals)
        self.fighter_tabs = QTabWidget()
        self.fighter_tabs.addTab(scroll, "Stats")
        self.fighter_tabs.addTab(bag_scroll, "Death Bag && gear")
        self.fighter_tabs.addTab(decal_scroll, "Decals")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.fighter_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 600])
        page = QWidget()
        page_layout = QVBoxLayout(page)
        note = QLabel("These follow the game's own rules: the limit break comes from the stats (raise them past a "
                      "cap and the fighter is uncapped), and the level is the six stat levels added up (less 5) "
                      "and what was bought on top. A new class or grade fits the rest to it; anything you do "
                      "not change is kept as it is.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        page_layout.addWidget(note)
        page_layout.addWidget(split, 1)
        if self.fighter_list.count():
            self.fighter_list.setCurrentRow(0)
        else:
            form.setEnabled(False)
            self.f_lock.setText("There are no fighters in this save.")
        return page

    def _fighter_line(self, f: "savefighters.Fighter") -> str:
        lb = f" LB{f.limit_break}" if f.limit_break else ""
        state = {"USE": "playing", "GUARD": "on defense", "ENEMY": "dead"}.get(f.state, "")
        if f.kidnapped:
            state = "kidnapped"
        mark = " *" if self._fighter_changes(f.cid) else ""
        return (f"{f.name or '?'} - {self.rules.names.class_name(f.kind)} G{f.grade}{lb}, Lv {f.level}"
                + (f" ({state})" if state else "") + mark)

    def _original(self, cid: str) -> dict:
        return self.fighters[cid].values(self.rules)

    def _form_values(self) -> dict:
        values = {"name": self.f_name.text(), "type": self.f_class.currentData(), "grade": self.f_grade.value(),
                  "body": self.f_body.currentData(), "gasmask": self.f_mask.currentData()}
        values.update({k: box.value() for k, box in self.f_stats.items()})
        values.update({f"{k}_bonus": box.value() for k, box in self.f_bonus.items()})
        values.update({k: box.value() for k, box in self.f_slots.items()})
        return values

    def _fighter_changes(self, cid: str) -> int:
        edits = self.edits.get(cid)
        bag = self.bag_panel.fighter_changes(cid) if getattr(self, "bag_panel", None) is not None else 0
        bag += self.worn_decals.fighter_changes(cid) if getattr(self, "worn_decals", None) is not None else 0
        if not edits:
            return bag
        original = self._original(cid)
        return bag + sum(1 for k, v in edits.items() if v != original.get(k))

    def _now(self, cid: str) -> dict:
        """A fighter's values as they will be saved."""
        return self.edits.get(cid) or self._original(cid)

    def _bag_room(self, cid: str) -> int:
        return self._now(cid)["death_bag"] + self.gear_rules.vip_extra(self.document)

    def _wearer(self, cid: str) -> "savebag.Wearer":
        values = self._now(cid)
        decals = (self.worn_decals.worn_ids(cid) if getattr(self, "worn_decals", None) is not None
                  else savebag.worn_decals(self.document, cid))
        return savebag.Wearer(values["name"], values["type"], values["grade"], {k: values[k] for k in STATS}, decals)

    def _decals_changed(self) -> None:
        self.bag_panel.check()                      # a discount decal changes what may be worn
        self._bag_changed()

    def _bag_changed(self) -> None:
        for row in range(self.fighter_list.count()):
            item = self.fighter_list.item(row)
            item.setText(self._fighter_line(self.fighters[item.data(ROLE_CID)]))
        self._update()

    def _show_fighter(self) -> None:
        item = self.fighter_list.currentItem()
        cid = item.data(ROLE_CID) if item is not None else None
        if cid is None:
            return
        self.shown_cid = cid
        f = self.fighters[cid]
        self._fill_form(self.edits.get(cid) or self._original(cid))
        reason = savefighters.lock_reason(f, self.document)
        if reason:
            self.f_lock.setText(f"<b>{html.escape(f.name or 'This fighter')} is {html.escape(reason)}</b>, so the "
                                "editor leaves them as they are.")
        elif savefighters.look_only(f):
            self.f_lock.setText(f"<b>{html.escape(f.name or 'This fighter')} is on defense.</b> The defense list "
                                "keeps its own copy of their class, grade and level, so only the name and look "
                                "change here - take them off defense in the game to change the rest.")
        else:
            state = STATES.get(f.state, f.state)
            self.f_lock.setText(f"<span style='color:gray'>{html.escape(state)}</span>")
        locked, look = bool(reason), savefighters.look_only(f)
        for widget in (self.f_name, self.f_body, self.f_mask):
            widget.setEnabled(not locked)
        for widget in ([self.f_class, self.f_grade, self.f_max] + list(self.f_stats.values())
                       + list(self.f_bonus.values()) + list(self.f_slots.values())):
            widget.setEnabled(not locked and not look)
        self.f_undo.setEnabled(not locked)
        self._editable = not locked and not look
        self._fit_ranges()
        name = html.escape(f.name or "This fighter")
        bag_reason = (f"{name} is {html.escape(reason)}, so their Death Bag is left as it is." if locked else
                      f"{name} is on defense: change their gear in the game." if look else "")
        self.bag_panel.show_fighter(cid, bag_reason)
        self.worn_decals.show_fighter(
            cid, f"{name} is {html.escape(reason)}, so their decals are left as they are." if locked else
            f"{name} is on defense: change their decals in the game." if look else "")

    def _fill_form(self, values: dict) -> None:
        self._loading = True
        try:
            self.f_name.setText(values["name"])
            self._select(self.f_class, values["type"], self.rules.names.class_name(values["type"]))
            self._select(self.f_body, values["body"], savefighters.body_label(values["body"], self.rules))
            self._fill_masks(values["body"], values["gasmask"])
            self.f_grade.setRange(1, max(self.rules.max_grade(values["type"]), values["grade"]))
            self.f_grade.setValue(values["grade"])
            for key in STATS:
                self.f_stats[key].setRange(0, 9999)
                self.f_stats[key].setValue(values[key])
                self.f_bonus[key].setRange(0, 9999)
                self.f_bonus[key].setValue(values[f"{key}_bonus"])
            for key, box in self.f_slots.items():
                box.setRange(0, 9999)
                box.setValue(values[key])
        finally:
            self._loading = False

    @staticmethod
    def _select(combo: QComboBox, data, label: str) -> None:
        index = combo.findData(data)
        if index < 0:                       # not in this game's data: shown as it is
            combo.addItem(label, data)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _fill_masks(self, body: str, mask: str) -> None:
        self.f_mask.blockSignals(True)
        self.f_mask.clear()
        for ident in self.rules.masks_for(body):
            self.f_mask.addItem(savefighters.mask_label(ident), ident)
        if self.f_mask.findData(mask) < 0:
            number = mask[-3:]
            match = next((i for i in range(self.f_mask.count()) if str(self.f_mask.itemData(i)).endswith(number)),
                         None)
            if match is None and self.f_mask.count() == 0:
                self.f_mask.addItem(savefighters.mask_label(mask), mask)
                match = 0
            self.f_mask.setCurrentIndex(match or 0)
        else:
            self.f_mask.setCurrentIndex(self.f_mask.findData(mask))
        self.f_mask.blockSignals(False)

    def _fit_ranges(self) -> None:
        """The boxes held to what the class and grade allow, at the limit break the stats give."""
        if self.shown_cid is None:
            return
        f = self.fighters[self.shown_cid]
        original = self._original(self.shown_cid)
        kind = self.f_class.currentData()
        self._loading = True
        try:
            same_class = kind == original["type"]
            self.f_grade.setMaximum(max(self.rules.max_grade(kind), original["grade"] if same_class else 1))
            grade = self.f_grade.value()
            refit = (kind, grade) != (original["type"], original["grade"])

            def span(key: str, low: int, high: int) -> tuple[int, int]:
                # the same class and grade: a value already outside the rules stays reachable
                return (low, high) if refit else (min(low, original[key]), max(high, original[key]))

            values = self._form_values()
            limits = self.rules.limits(kind, grade, {k: values[k] for k in STATS},
                                       {k: values[f"{k}_bonus"] for k in STATS})
            for key in STATS:
                self._bonus_choices(key, limits.bonus_choices, original[f"{key}_bonus"], refit)
                self.f_stats[key].setRange(*span(key, 1, limits.stat_top))
            values = self._form_values()
            bonus = {k: values[f"{k}_bonus"] for k in STATS}
            limits = self.rules.limits(kind, grade, {k: values[k] for k in STATS}, bonus)
            for what in savefighters.BOUGHT:
                key = savefighters.TOTALS[what]
                self.f_slots[key].setRange(*span(key, *limits.total_range(what)))
                self.f_slots[key].setEnabled(self._editable and limits.total_range(what)[0]
                                             != limits.total_range(what)[1])
            values = self._form_values()
            bought = {w: values[savefighters.TOTALS[w]] - limits.base[w] for w in savefighters.BOUGHT}
            stats = {k: values[k] for k in STATS}
            lb = self.rules.limit_break_of(kind, grade, stats, bonus, bought["skill"], bought["bag"], bought["rage"])
            level = savefighters.level_of(stats, bought["skill"], bought["bag"], bought["rage"])
            self.f_level.setText(f"<b>{level}</b>" + (f" (was {f.level})" if level != f.level else ""))
            lb_text = f"<b>{lb}</b> <span style='color:gray'>- worked out from the stats, as the game does</span>"
            if lb != f.limit_break and values == original:
                lb_text += (f"<br><span style='color:#c0392b'>The save says {f.limit_break}, but the game reads "
                            f"{lb} from these levels. Changing them sets it right.</span>")
            self.f_lb.setText(lb_text)
            top_lb = self.rules.max_limit_break(kind, grade)
            caps = ", ".join(str(self.rules.cap(kind, grade, n)) for n in range(top_lb + 1))
            ranges = [f"{label} {low} to {high}" for label, (low, high) in (
                ("decal slots", limits.total_range("skill")), ("Death Bag", limits.total_range("bag")),
                ("rage bars", limits.total_range("rage")))]
            self.f_ranges.setText(
                f"This class and grade caps stats at {caps} for limit breaks 0 to {top_lb}; raising a stat past "
                "a cap uncaps the fighter. Each stat's bonus (the game rolls "
                f"{', '.join(map(str, limits.bonus_choices))}) adds to its cap at limit break 0. At limit break "
                f"{limits.limit_break}: "
                + ", ".join(ranges) + ".")
        finally:
            self._loading = False

    def _bonus_choices(self, key: str, choices: tuple, original: int, refit: bool) -> None:
        """A stat's bonus box offers only what the purchase roll can give (and what the save has,
        while its class and grade are as they were)."""
        box = self.f_bonus[key]
        shown = sorted(set(choices) | ({original} if not refit else set()))
        current = box.value()
        if current not in shown:
            current = max([c for c in shown if c <= current] or [0])
        box.setRange(min(shown), max(shown))
        box.setValue(current)
        box.setProperty("choices", shown)

    def _bonus_stepped(self, key: str) -> None:
        """A spin of the bonus box moves to the next value the game can roll."""
        box = self.f_bonus[key]
        shown = box.property("choices") or []
        value = box.value()
        if shown and value not in shown:
            last = box.property("last") if box.property("last") is not None else min(shown)
            up = [c for c in shown if c > last]
            down = [c for c in shown if c < last]
            value = (min(up) if up else last) if value > last else (max(down) if down else last)
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        box.setProperty("last", value)

    def _fighter_changed(self, *_args) -> None:
        if self._loading or self.shown_cid is None:
            return
        if self.sender() is self.f_body:
            self._fill_masks(self.f_body.currentData(), self.f_mask.currentData() or "")
        self._fit_ranges()
        values = self._form_values()
        if values == self._original(self.shown_cid):
            self.edits.pop(self.shown_cid, None)
        else:
            self.edits[self.shown_cid] = values
        self.bag_panel.check()                      # the bag's room, and what may be worn, follow the stats
        self.worn_decals.check()                    # and so do the decal slots
        item = self.fighter_list.currentItem()
        if item is not None:
            item.setText(self._fighter_line(self.fighters[self.shown_cid]))
        self._update()

    def max_fighter(self) -> None:
        if self.shown_cid is None:
            return
        f = self.fighters[self.shown_cid]
        values = {**self._form_values(), **savefighters.max_values(f, self.rules, self.f_class.currentData())}
        self._fill_form(values)
        self._fighter_changed()

    def undo_fighter(self) -> None:
        if self.shown_cid is None:
            return
        self.edits.pop(self.shown_cid, None)
        self._show_fighter()
        self._fighter_changed()

    def max_all_fighters(self) -> None:
        skipped = []
        for cid, f in self.fighters.items():
            reason = savefighters.lock_reason(f, self.document) or ("on defense" if savefighters.look_only(f)
                                                                    else "")
            if reason:
                skipped.append(f"{f.name or '?'} ({reason})")
                continue
            values = {**(self.edits.get(cid) or self._original(cid)), **savefighters.max_values(f, self.rules)}
            if values == self._original(cid):
                self.edits.pop(cid, None)
            else:
                self.edits[cid] = values
        for row in range(self.fighter_list.count()):
            item = self.fighter_list.item(row)
            item.setText(self._fighter_line(self.fighters[item.data(ROLE_CID)]))
        self._show_fighter()
        self._update()
        if skipped:
            self.f_ranges.setText(self.f_ranges.text() + "<br>Left as they are: " + html.escape(", ".join(skipped)))

    # -- the Fighter Freezer: buying, deleting, bringing back, its size ---------------

    def _freezer_box(self) -> QWidget:
        box = QGroupBox("Fighter Freezer")
        self.fz_state = QLabel()
        self.fz_state.setWordWrap(True)
        self.fz_level = QComboBox()
        for level, count in sorted(self.freezer.sizes.items()):
            self.fz_level.addItem(f"Level {level} ({count} hangers)", level)
        self.fz_level.activated.connect(self._freezer_level_picked)
        self.fz_buy = QPushButton("Buy a fighter...")
        self.fz_buy.setToolTip("A new fighter in the first empty hanger, the way the Fighter Depot sells one: "
                               "level 1, the stat bonuses rolled as the game rolls them, and its gas mask on. "
                               "Not charged.")
        self.fz_buy.clicked.connect(self.buy_fighter)
        self.fz_delete = QPushButton("Delete")
        self.fz_delete.clicked.connect(self.delete_fighter)
        self.fz_back = QPushButton("Bring back")
        self.fz_back.clicked.connect(self.recover_fighter)
        self.fz_undo = QPushButton("Undo the last freezer change")
        self.fz_undo.clicked.connect(self.undo_freezer)
        self.fz_done = QLabel()
        self.fz_done.setObjectName("dim")
        self.fz_done.setWordWrap(True)
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("Size:"))
        level_row.addWidget(self.fz_level, 1)
        buttons = QGridLayout()
        buttons.addWidget(self.fz_buy, 0, 0, 1, 2)
        buttons.addWidget(self.fz_delete, 1, 0)
        buttons.addWidget(self.fz_back, 1, 1)
        buttons.addWidget(self.fz_undo, 2, 0, 1, 2)
        layout = QVBoxLayout(box)
        layout.addWidget(self.fz_state)
        layout.addLayout(level_row)
        layout.addLayout(buttons)
        layout.addWidget(self.fz_done)
        self._freezer_info(self.document)
        return box

    def _freezer_info(self, save: "saveedit.SaveDocument") -> None:
        """How many hangers, and how many empty, as the freezer's steps leave them (kept, so the
        count needs no fresh copy of the save each time it is shown)."""
        try:
            self._fz_info = (len(saveroster.hangers(save)), len(saveroster.free_hangers(save)))
        except saveedit.EditError as exc:
            self._fz_info = str(exc)

    def _shown_fighter(self) -> "savefighters.Fighter | None":
        return self.fighters.get(self.shown_cid) if self.shown_cid else None

    def _show_freezer(self) -> None:
        if not hasattr(self, "fz_state"):
            return
        if isinstance(self._fz_info, str):
            self.fz_state.setText(html.escape(self._fz_info))
            for widget in (self.fz_level, self.fz_buy, self.fz_delete, self.fz_back, self.fz_undo):
                widget.setEnabled(False)
            return
        count, free = self._fz_info
        level = self.freezer.level_of(count)
        self.fz_level.blockSignals(True)
        at = self.fz_level.findData(level)
        if at < 0:
            self.fz_level.addItem(f"{count} hangers (not a level the game has)", None)
            at = self.fz_level.count() - 1
        self.fz_level.setCurrentIndex(at)
        self.fz_level.blockSignals(False)
        self.fz_state.setText(f"{count} hangers, {free} empty")
        self.fz_buy.setEnabled(bool(free))
        self.fz_buy.setToolTip(self.fz_buy.toolTip() if free else "Every hanger holds a fighter: raise the size first")
        f = self._shown_fighter()
        why = saveroster.delete_reason(f) if f else "no fighter picked"
        self.fz_delete.setText(f"Delete {f.name}" if f and f.name else "Delete")
        self.fz_delete.setEnabled(not why)
        self.fz_delete.setToolTip(f"Can't: {f.name if f else 'they'} is {why}" if why and f else
                                  "Deletes them the way the game does: their Death Bag and all in it go, and "
                                  "Splithium comes back for their level")
        dead = bool(f and f.state in saveroster.DEAD)
        self.fz_back.setText(f"Bring {f.name} back" if dead and f.name else "Bring back")
        self.fz_back.setEnabled(dead)
        self.fz_back.setToolTip("Out of the Haters and back in the freezer with full HP, their Death Bag as it was, "
                                "the way the Fighter Depot does it (not charged)" if dead else
                                "Only a dead fighter can be brought back")
        done = [s.description for s in self.work.freezer]
        self.fz_undo.setEnabled(bool(done))
        self.fz_done.setText("Not saved yet: " + html.escape("; ".join(done)) if done else "")

    def _freezer_step(self, cid: str, description: str, run) -> bool:
        key = ("freezer", cid)
        self.work.add(key, description, run)
        try:
            self.work.base()
        except saveedit.EditError as exc:
            self.work.undo(key)
            QMessageBox.warning(self, "Fighter Freezer", str(exc))
            return False
        self._roster_changed(select=cid)
        return True

    def _roster_changed(self, select: str | None = None) -> None:
        """The freezer's steps changed: the fighters as they stand after them."""
        base = self.work.base()
        self._freezer_info(base)
        now = {f.cid: f for f in savefighters.fighters(base)}
        gone = set(self.fighters) - set(now)
        for cid in gone:                    # a fighter deleted here takes their unsaved changes along
            self.edits.pop(cid, None)
            self.worn_decals.layouts.pop(cid, None)
        self.work.drop(lambda key: isinstance(key, tuple) and key[0] == "bag" and key[1] not in now)
        self.fighters.clear()
        self.fighters.update(now)
        keep = select if select in now else self.shown_cid if self.shown_cid in now else None
        self.fighter_list.blockSignals(True)
        self.fighter_list.clear()
        for f in self.fighters.values():
            item = QListWidgetItem(self._fighter_line(f))
            item.setData(ROLE_CID, f.cid)
            self.fighter_list.addItem(item)
        self.fighter_list.blockSignals(False)
        rows = [r for r in range(self.fighter_list.count()) if self.fighter_list.item(r).data(ROLE_CID) == keep]
        self.shown_cid = None
        if self.fighter_list.count():
            self.fighter_list.setCurrentRow(rows[0] if rows else 0)
            self.f_form.setEnabled(True)
        if self.storage is not None:
            self.storage.stale()
        self._update()

    def buy_fighter(self) -> None:
        room_extra = self.gear_rules.vip_extra(self.document)
        dialog = BuyDialog(self.rules, self.catalog, room_extra, self)
        if not dialog.exec():
            return
        wanted = dialog.wanted()
        rules, catalog = self.rules, self.catalog

        def buy(save, w=wanted):
            saveroster.add_fighter(save, rules, catalog, name=w.name, kind=w.kind, grade=w.grade, body=w.body,
                                   gasmask=w.gasmask, bonus=w.bonus, room=w.room, cid=w.cid, eids=w.eids, now=w.now)

        self._freezer_step(wanted.cid, f"buy {wanted.name}", buy)

    def delete_fighter(self) -> None:
        f = self._shown_fighter()
        if f is None or saveroster.delete_reason(f):
            return
        base = self.work.preview()
        bag = base.get(("soul", "deathbag", base.uid, f.cid))
        carried = sum(1 for s in bag if isinstance(s, dict) and s.get("eid")) if isinstance(bag, list) else 0
        spl = saveroster.refund(self.rules, f)
        pending = self._fighter_changes(f.cid)
        answer = QMessageBox.question(
            self, "Fighter Freezer",
            f"Delete {f.name or 'this fighter'} when you save?\n\nThe {carried} thing(s) in their Death Bag go with "
            f"them, and the decals they wear come off (a premium one back to the stock). The game gives back "
            f"{spl:,} Splithium for their level, as far as the SPL tank holds."
            + (f"\n\nThe {pending} change(s) made to them here are dropped." if pending else ""))
        if answer != QMessageBox.StandardButton.Yes:
            return
        rules, decals, limits, cid = self.rules, self.decal_catalog, self.limits, f.cid
        self._freezer_step(cid, f"delete {f.name}",
                           lambda save: saveroster.delete_fighter(save, rules, decals, limits, cid))

    def recover_fighter(self) -> None:
        f = self._shown_fighter()
        if f is None or f.state not in saveroster.DEAD:
            return
        rules, cid = self.rules, f.cid
        self._freezer_step(cid, f"bring {f.name} back", lambda save: saveroster.recover_fighter(save, rules, cid))

    def _freezer_level_picked(self, _index: int) -> None:
        level = self.fz_level.currentData()
        if level is None:
            return
        if isinstance(self._fz_info, str) or self.freezer.sizes.get(level) == self._fz_info[0]:
            return
        freezer = self.freezer
        self._freezer_step("", f"freezer to level {level}",
                           lambda save, n=level: saveroster.set_freezer(save, freezer, n))

    def undo_freezer(self) -> None:
        if self.work.freezer:
            self.work.freezer.pop()
            self._roster_changed()

    # -- the change count -------------------------------------------------------

    def _update(self) -> None:
        values = self._values()
        bounds = saveedit.account_bounds(self.document, self.limits, values)
        for key, level_key in (("kill_coins", "bank_level"), ("splithium", "tank_level")):
            box = self.boxes[key]
            box.blockSignals(True)
            f = next(x for x in saveedit.ACCOUNT_FIELDS if x.key == key)
            box.setMaximum(max(bounds[key][1], self.document.number(f.path)))
            box.blockSignals(False)
            self.caps[key].setText(f"holds up to {bounds[key][1]:,} at level {values[level_key]}")
        count = sum(1 for f in saveedit.ACCOUNT_FIELDS if values[f.key] != self.document.number(f.path))
        count += len(self.work.freezer)
        count += sum(self._fighter_changes(cid) for cid in getattr(self, "edits", {}))
        if getattr(self, "bag_panel", None) is not None:
            count += sum(self.bag_panel.fighter_changes(cid) for cid in self.bag_panel.steps if cid not in self.edits)
            count += sum(self.worn_decals.fighter_changes(cid) for cid in self.worn_decals.layouts
                         if cid not in self.edits and cid not in self.bag_panel.steps)
        for page in ("research", "decal_stock", "mastery", "stew", "quests", "dates", "run"):
            if getattr(self, page, None) is not None:
                count += getattr(self, page).change_count
        if getattr(self, "storage", None) is not None:
            count += self.storage.step_count
        self._show_freezer()
        self.changes_label.setText("No changes yet" if not count else f"{count} value(s) changed, not saved yet")

    def _pending(self) -> "saveedit.SaveDocument":
        """A copy of the save with this window's values in it."""
        copy = saveedit.SaveDocument(self.document.raw, self.document.source)
        saveedit.set_account(copy, self._values(), self.limits)
        self.work.play_freezer(copy)            # after the account: a deleted fighter's Splithium adds on
        for cid, values in self.edits.items():
            savefighters.set_fighter(copy, self.rules, cid, values)
        self.worn_decals.apply(copy, check=set(self.edits))
        self.work.play_things(copy)             # the Storage Box's and Death Bags' steps, in their order
        self.bag_panel.check_rooms(copy, check=set(self.edits))
        if self.research is not None:
            self.research.apply(copy)
        self.decal_stock.apply(copy)
        self.mastery.apply(copy)
        self.stew.apply(copy)
        self.quests.apply(copy)
        self.run.apply(copy)
        self.dates.apply(copy)                  # last, so a fix sees the dates the other pages wrote
        return copy

    # -- saving ---------------------------------------------------------------

    def save(self) -> None:
        try:
            pending = self._pending()
        except saveedit.EditError as exc:
            QMessageBox.warning(self, "Save changes", f"Nothing was written: {exc}")
            return
        changes = pending.changes()
        if not changes:
            QMessageBox.information(self, "Save changes", "Nothing has been changed.")
            return
        shown = changes if len(changes) <= 40 else changes[:40]
        lines = "\n".join(f"  {c.label}: {_shown(c.before)} -> {_shown(c.after)}" for c in shown)
        if len(changes) > len(shown):
            lines += f"\n  ...and {len(changes) - len(shown)} more"
        problems = self.bag_panel.problems(pending, set(self.bag_panel.steps) | set(self.edits)
                                           | set(self.worn_decals.layouts))
        if problems:
            lines += "\n\nThe game would not let them wear these (see Death Bag & gear):\n" + "\n".join(
                f"  {p}" for p in problems)
        answer = QMessageBox.question(
            self, "Save changes",
            f"Write these changes to \"{self.entry.nickname}\"?\n\n{lines}\n\n"
            "The save is backed up first. Close the game before you go on.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.written = self.manager.write_edited_save(self.entry.id, pending)
        except (saveedit.EditError, OSError) as exc:
            QMessageBox.warning(self, "Save changes", f"Nothing was written: {exc}")
            return
        except Exception as exc:  # a library error after the write is still worth saying plainly
            QMessageBox.warning(self, "Save changes", f"The save was written, but: {exc}")
            return
        self.document = saveedit.SaveDocument.read(pending.source) if pending.source else pending
        QMessageBox.information(self, "Save changes", f"Saved {len(changes)} change(s) to \"{self.entry.nickname}\".")
        self.accept()


def _shown(value) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, str) and value.startswith("+"):
        return value                                # a "+N" part level stays one
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)
