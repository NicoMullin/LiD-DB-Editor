"""The Fighters page's Death Bag panel: what a fighter carries, wears and holds (savebag.py).

Like the Storage Box page, changes are kept as steps and played onto a fresh copy
of the save - once to show the bag as it will be, and again onto the copy the
editor writes. The steps are in one list with the Storage Box's (edit_steps.py),
so things can move between a bag and the box. What a fighter may wear is checked
against the class, grade and stats as they stand on the page, so raising a stat
there opens up gear here.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, savebag, savestorage
from .edit_steps import Steps
from .storage_page import AddForm

COLUMNS = ("Kind", "Name", "", "Worn")
EMPTY_HAND = savebag.QUICK               # the "Empty hand" button (Qt takes -1 to mean "pick an id")


class BagPanel(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savestorage.Catalog",
                 rules: "savebag.GearRules", fighters: dict, room_for, wearer_for,
                 steps: "Steps | None" = None, parent=None):
        super().__init__(parent)
        self.document, self.catalog, self.rules = document, catalog, rules
        self.fighters = fighters                    # cid -> savefighters.Fighter
        self.room_for, self.wearer_for = room_for, wearer_for
        self.work = steps if steps is not None else Steps(document)
        self.box_groups: list = []                  # what the Storage Box holds, as it will be
        self.cid: str | None = None
        self.bag: "savebag.Bag | None" = None
        self._loading = False

        self.lock = QLabel()
        self.lock.setWordWrap(True)
        self.used_label = QLabel()
        repair = QPushButton("Repair && refill all")
        repair.setToolTip("Every weapon and armor in this bag to full durability and full ammo")
        repair.clicked.connect(lambda: self._step(lambda bag: bag.repair_all(), "repair and refill all"))
        self.undo = QPushButton("Undo the last step")
        self.undo.clicked.connect(self.undo_last)
        top = QHBoxLayout()
        top.addWidget(self.used_label, 1)
        top.addWidget(repair)
        top.addWidget(self.undo)

        # -- wearing and holding --
        gear_box = QGroupBox("Wearing and holding")
        grid = QGridLayout(gear_box)
        self.quick: dict[tuple, QComboBox] = {}
        self.held: dict[str, QButtonGroup] = {}
        for column, hand in enumerate(savebag.HANDS):
            grid.addWidget(QLabel(f"<b>{savebag.HAND_LABELS[hand]}</b>"), 0, column * 2, 1, 2)
            group = QButtonGroup(self)
            group.setExclusive(True)
            for index in range(savebag.QUICK):
                radio = QRadioButton(f"{index + 1}")
                radio.setToolTip("The quick slot this hand holds")
                group.addButton(radio, index)
                combo = QComboBox()
                combo.setMinimumContentsLength(18)
                combo.currentIndexChanged.connect(lambda _i, h=hand, n=index: self._quick_picked(h, n))
                self.quick[(hand, index)] = combo
                grid.addWidget(radio, 1 + index, column * 2)
                grid.addWidget(combo, 1 + index, column * 2 + 1)
            bare = QRadioButton("Empty hand")
            group.addButton(bare, EMPTY_HAND)
            grid.addWidget(bare, 1 + savebag.QUICK, column * 2, 1, 2)
            group.idClicked.connect(lambda n, h=hand: self._held_picked(h, n))
            self.held[hand] = group
        self.armor: dict[str, QComboBox] = {}
        row = 2 + savebag.QUICK
        for site, label in (("EQSITE_HEAD", "Head:"), ("EQSITE_BODY", "Body:"), ("EQSITE_LEGS", "Legs:")):
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _i, s=site: self._armor_picked(s))
            self.armor[site] = combo
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(combo, row, 1, 1, 3)
            row += 1
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.take_off = QPushButton("Take off what they cannot wear")
        self.take_off.clicked.connect(self.take_off_problems)
        grid.addWidget(self.problems_label, row, 0, 1, 3)
        grid.addWidget(self.take_off, row, 3)
        grip_note = QLabel("Only gear this fighter can wear is offered (a greyed-out one says why). A two-handed "
                           "weapon leaves the other hand empty, as in the game.")
        grip_note.setObjectName("dim")
        grip_note.setWordWrap(True)
        grid.addWidget(grip_note, row + 1, 0, 1, 4)

        # -- what is in the bag --
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(160)
        take = QPushButton("Take out the picked rows")
        take.setToolTip("Out of the bag for good (worn or held pieces come off first)")
        take.clicked.connect(self.take_picked)
        self.to_box = QPushButton("Put them in the Storage Box")
        self.to_box.setToolTip("The picked rows go into the Storage Box's free slots (worn or held pieces come off "
                               "first)")
        self.to_box.clicked.connect(self.store_picked)
        take_row = QHBoxLayout()
        take_row.addStretch(1)
        take_row.addWidget(self.to_box)
        take_row.addWidget(take)

        self.from_box = QComboBox()
        self.from_box.setMinimumContentsLength(24)
        self.from_box_count = QSpinBox()
        self.from_box_count.setRange(1, 99_999)
        fetch = QPushButton("Take into the bag")
        fetch.setToolTip("That many of the picked thing out of the Storage Box into this bag (the best kept first)")
        fetch.clicked.connect(self.fetch_picked)
        self.box_row = QWidget()
        box_row = QHBoxLayout(self.box_row)
        box_row.setContentsMargins(0, 0, 0, 0)
        box_row.addWidget(QLabel("From the Storage Box:"))
        box_row.addWidget(self.from_box, 1)
        box_row.addWidget(QLabel("How many:"))
        box_row.addWidget(self.from_box_count)
        box_row.addWidget(fetch)

        self.form = AddForm(catalog)
        self.form.add.clicked.connect(self.add_picked)
        self.steps_label = QLabel()
        self.steps_label.setObjectName("dim")
        self.steps_label.setWordWrap(True)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(top)
        body.addWidget(gear_box)
        body.addWidget(QLabel("<b>In the bag</b>"))
        body.addWidget(self.table, 1)
        body.addLayout(take_row)
        body.addWidget(QLabel("<b>Put something in</b>"))
        body.addWidget(self.box_row)
        body.addWidget(self.form)
        body.addWidget(self.steps_label)
        layout = QVBoxLayout(self)
        layout.addWidget(self.lock)
        layout.addWidget(self.body, 1)

    # -- the steps --

    def _bag(self, save: "saveedit.SaveDocument", cid: str) -> "savebag.Bag":
        f = self.fighters[cid]
        return savebag.Bag(save, self.catalog, self.rules, cid, self.room_for(cid), f.name, f.chr_path)

    @property
    def steps(self) -> dict[str, list[str]]:
        """cid -> what was done to that fighter's bag, in words."""
        found: dict[str, list[str]] = {}
        for step in self.work.things:
            if isinstance(step.key, tuple) and step.key[0] == "bag":
                found.setdefault(step.key[1], []).append(step.description)
        return found

    def check_rooms(self, save: "saveedit.SaveDocument", check: set | None = None) -> None:
        """With every step played onto ``save``: no bag (of those changed, and of ``check``)
        may hold more than its room."""
        for cid in set(self.steps) | set(check or ()):
            try:
                bag = self._bag(save, cid)
            except saveedit.EditError:
                continue
            if bag.used() > bag.room:
                raise saveedit.EditError(f"{bag.label} holds {bag.used():,} things but has room for {bag.room:,}: "
                                         "take some out, or give it more Death Bag space")

    def problems(self, save: "saveedit.SaveDocument", cids) -> list[str]:
        """What the changed fighters would wear that the game would not allow, in words."""
        out = []
        for cid in cids:
            try:
                bag = self._bag(save, cid)
            except saveedit.EditError:
                continue
            for eid, where, why in bag.problems(self.wearer_for(cid)):
                out.append(f"{self.fighters[cid].name}: {bag.piece_label(eid)} ({where}) - {why}")
        return out

    def _step(self, step, description: str) -> None:
        if self.cid is None:
            return
        key = ("bag", self.cid)
        self.work.add(key, description, lambda save, s=step, c=self.cid: s(self._bag(save, c)))
        try:
            self.refresh()
        except saveedit.EditError as exc:
            self.work.undo(key)
            QMessageBox.warning(self, "Death Bag", str(exc))
            self.refresh()
            return
        self.changed.emit()

    def undo_last(self) -> None:
        if self.cid is not None and self.work.undo(("bag", self.cid)):
            self.refresh()
            self.changed.emit()

    @property
    def step_count(self) -> int:
        return sum(len(s) for s in self.steps.values())

    def fighter_changes(self, cid: str) -> int:
        return len(self.work.of(("bag", cid)))

    # -- showing a fighter's bag --

    def show_fighter(self, cid: str, reason: str) -> None:
        """Show this fighter's bag; ``reason`` (why it may not change) leaves it to look at only."""
        self.cid = cid
        self.lock.setText(f"<b>{html.escape(reason)}</b>" if reason else "")
        self.lock.setVisible(bool(reason))
        self.body.setEnabled(not reason)
        self.refresh()

    def refresh(self) -> None:
        if self.cid is None:
            return
        preview = self.work.preview()
        try:
            bag = self._bag(preview, self.cid)
        except saveedit.EditError as exc:
            self.bag = None
            self.used_label.setText(html.escape(str(exc)))
            self.table.setRowCount(0)
            self.body.setEnabled(False)
            return
        self.bag = bag
        self._show_used()
        self._fill_table(bag)
        self._fill_gear(bag)
        self._fill_box(preview)
        done = self.work.of(("bag", self.cid))
        self.undo.setEnabled(bool(done))
        self.steps_label.setText("Not saved yet: " + html.escape("; ".join(done)) if done else "")

    def check(self) -> None:
        """The stats changed on the page: what may be worn, and the room, may have too."""
        if self.bag is not None:
            self.bag.room = self.room_for(self.cid)
            self._show_used()
            self._fill_gear(self.bag)

    def _show_used(self) -> None:
        used, room = self.bag.used(), self.bag.room
        extra = self.rules.vip_extra(self.document)
        over = used > room
        text = f"{used:,} of {room:,} used" + (" - more than it has room for" if over else "")
        self.used_label.setText((f"<span style='color:#c0392b'>{text}</span>" if over else text)
                                + (f" <span style='color:gray'>(the Express Pass adds {extra})</span>" if extra
                                   else ""))

    def _fill_table(self, bag: "savebag.Bag") -> None:
        records = bag._records()
        gear = bag.gear()
        where = {}
        for arm, eid in gear.quick.items():
            hand = "right" if arm < savebag.QUICK else "left"
            where[eid] = f"{savebag.HAND_LABELS[hand]} {arm % savebag.QUICK + 1}"
        for hand in savebag.HANDS:
            if gear.held_eid(hand):
                where[gear.held_eid(hand)] = f"in the {savebag.HAND_LABELS[hand].lower()}"
        for site, eid in gear.armor.items():
            where[eid] = savebag.SITE_LABELS[site]
        rows = [(i, s) for i, s in enumerate(bag.slots) if savestorage._int(s.get("type"), -1) != -1]
        self.rows = [i for i, _ in rows]
        self.table.setRowCount(len(rows))
        for row, (index, slot) in enumerate(rows):
            number = savestorage._int(slot.get("type"), -1)
            group = bag._group_of(number, records.get(number, {}).get(slot.get("eid")), slot.get("eid"))
            detail = bag.piece_label(slot.get("eid"))[len(group.name) + 1:] if number == 0 else group.detail
            for column, text in enumerate((savestorage.KIND_LABELS[group.kind], group.name, detail,
                                           where.get(slot.get("eid"), ""))):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def _fill_box(self, preview: "saveedit.SaveDocument") -> None:
        """The Storage Box's things, to take into the bag."""
        try:
            box = savestorage.Box(preview, self.catalog)
        except saveedit.EditError:
            self.box_groups = []
            self.box_row.setVisible(False)
            self.to_box.setVisible(False)
            return
        self.box_groups = box.groups()
        self.box_row.setVisible(True)
        self.to_box.setVisible(True)
        picked = self.from_box.currentData()
        self.from_box.clear()
        for n, group in enumerate(self.box_groups):
            label = f"{group.name} {group.detail}".strip()
            self.from_box.addItem(f"{label} ({group.count:,} in the box)", n)
        if not self.box_groups:
            self.from_box.addItem("(the Storage Box is empty)", None)
        at = self.from_box.findData(picked) if picked is not None else 0
        self.from_box.setCurrentIndex(max(0, at))

    def _fill_gear(self, bag: "savebag.Bag") -> None:
        wearer = self.wearer_for(self.cid)
        gear = bag.gear()
        self._loading = True
        try:
            for (hand, index), combo in self.quick.items():
                site = savebag.HANDS[hand][0]
                self._fill_combo(combo, bag.wearable(site, wearer),
                                 gear.quick.get(savebag.HANDS[hand][1] + index), bag)
            for hand, group in self.held.items():
                held = gear.held.get(hand)
                group.button(EMPTY_HAND if held is None else held).setChecked(True)
            for site, combo in self.armor.items():
                self._fill_combo(combo, bag.wearable(site, wearer), gear.armor.get(site), bag)
        finally:
            self._loading = False
        problems = bag.problems(wearer)
        self.problems_label.setText(
            "<span style='color:#c0392b'>The game would not let them wear: "
            + html.escape("; ".join(f"{bag.piece_label(e)} ({w}) - {why}" for e, w, why in problems))
            + "</span>" if problems else "")
        self.problems_label.setVisible(bool(problems))
        self.take_off.setVisible(bool(problems))

    def _fill_combo(self, combo: QComboBox, choices: list, current: str | None, bag: "savebag.Bag") -> None:
        combo.clear()
        combo.addItem("(empty)", None)
        for eid, label, why in choices:
            combo.addItem(label + (f" - {why}" if why and eid != current else ""), eid)
            if why and eid != current:
                model = combo.model()
                if isinstance(model, QStandardItemModel):
                    model.item(combo.count() - 1).setEnabled(False)
        if current and combo.findData(current) < 0:
            combo.addItem(bag.piece_label(current), current)
        combo.setCurrentIndex(max(0, combo.findData(current)) if current else 0)

    # -- changing what is worn --

    def _quick_picked(self, hand: str, index: int) -> None:
        if self._loading or self.bag is None:
            return
        eid = self.quick[(hand, index)].currentData()
        wearer = self.wearer_for(self.cid)
        label = self.bag.piece_label(eid) if eid else "nothing"
        self._step(lambda bag, h=hand, n=index, e=eid, w=wearer: bag.set_quick(h, n, e, w),
                   f"{savebag.HAND_LABELS[hand].lower()} quick slot {index + 1}: {label}")

    def _held_picked(self, hand: str, index: int) -> None:
        if self._loading or self.bag is None:
            return
        held = None if index == EMPTY_HAND else index
        what = "nothing" if held is None else f"quick slot {held + 1}"
        self._step(lambda bag, h=hand, n=held: bag.set_held(h, n),
                   f"{savebag.HAND_LABELS[hand].lower()} holds {what}")

    def _armor_picked(self, site: str) -> None:
        if self._loading or self.bag is None:
            return
        eid = self.armor[site].currentData()
        wearer = self.wearer_for(self.cid)
        label = self.bag.piece_label(eid) if eid else "nothing"
        self._step(lambda bag, s=site, e=eid, w=wearer: bag.set_armor(s, e, w),
                   f"{savebag.SITE_LABELS[site].lower()}: {label}")

    def take_off_problems(self) -> None:
        if self.bag is None:
            return
        eids = {e for e, _, _ in self.bag.problems(self.wearer_for(self.cid))}
        if eids:
            self._step(lambda bag, gone=frozenset(eids): bag.take_off(set(gone)), "took off what they cannot wear")

    # -- taking out and putting in --

    def take_picked(self) -> None:
        if self.bag is None:
            return
        picked = sorted({self.rows[i.row()] for i in self.table.selectedIndexes()})
        if not picked:
            return
        self._step(lambda bag, slots=tuple(picked): bag.remove(list(slots), f"{len(slots)} thing(s)"),
                   f"take out {len(picked)} thing(s)")

    def store_picked(self) -> None:
        if self.bag is None:
            return
        picked = sorted({self.rows[i.row()] for i in self.table.selectedIndexes()})
        eids = tuple(self.bag.slots[i].get("eid") for i in picked)
        if not eids:
            return
        try:
            free = len(savestorage.Box(self.work.preview(), self.catalog).free_slots())
        except saveedit.EditError:
            return
        if free == 0:
            QMessageBox.information(self, "Death Bag", "The Storage Box is full: give it more slots on its page, or "
                                                       "take something out first.")
            return
        if len(eids) > free:
            QMessageBox.information(self, "Death Bag", f"Only {free:,} Storage Box slot(s) are free, so {free:,} go.")
        catalog = self.catalog
        self._step(lambda bag, e=eids: bag.move(list(e), savestorage.Box(bag.save, catalog), f"{len(e)} thing(s)"),
                   f"put {min(len(eids), free):,} thing(s) in the Storage Box")

    def fetch_picked(self) -> None:
        if self.bag is None or self.from_box.currentData() is None:
            return
        group = self.box_groups[self.from_box.currentData()]
        free = len(self.bag.free_slots())
        if free == 0:
            QMessageBox.information(self, "Death Bag", "The bag is full: take something out first, or give the "
                                                       "fighter more Death Bag space.")
            return
        wanted = min(self.from_box_count.value(), group.count)
        count = min(wanted, free)
        if count < wanted:
            QMessageBox.information(self, "Death Bag", f"Only {free:,} slot(s) are free, so {free:,} come in.")
        name = f"{group.name} {group.detail}".strip()
        catalog = self.catalog

        def fetch(bag, key=group.key, n=count, label=name):
            box = savestorage.Box(bag.save, catalog)
            box.move(box.picks(key, n), bag, label)

        self._step(fetch, f"take {count:,} x {name} from the Storage Box")

    def add_picked(self) -> None:
        if self.bag is None:
            return
        wanted = self.form.picked(self)
        if wanted is None:
            return
        free = len(self.bag.free_slots())
        if free == 0:
            QMessageBox.information(self, "Death Bag", "The bag is full: take something out first, or give the "
                                                       "fighter more Death Bag space.")
            return
        if wanted.count > free:
            QMessageBox.information(self, "Death Bag", f"Only {free:,} slot(s) are free, so {free:,} go in.")
        self._step(lambda bag, w=wanted: w.put(bag), f"add {min(wanted.count, free):,} x {wanted.name}")
