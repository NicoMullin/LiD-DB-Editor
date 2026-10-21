"""The save editor's Storage Box page (savestorage.py does the work).

What is changed here is kept as a list of steps - add these, take those out,
this many slots - and played onto a fresh copy of the save each time: once to
show the box as it will be, and once more when the editor saves, onto the copy
it writes. So the page never changes the save it was opened with. The list is
shared with the Death Bags (edit_steps.py), so things moved between a bag and
the box show here too.
"""

from __future__ import annotations

import html
import time
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, savestorage
from .edit_steps import Steps

ADD_KINDS = ("weapon", "armor", "blueprint", "item", "mushroom", "beast")
COLUMNS = ("Kind", "Name", "", "How many")


class StoragePage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savestorage.Catalog",
                 steps: "Steps | None" = None, parent=None):
        super().__init__(parent)
        self.document, self.catalog = document, catalog
        self.work = steps if steps is not None else Steps(document)
        self._stale = False

        self.size_box = QSpinBox()
        self.size_box.setSingleStep(catalog.capacity_step())
        self.size_box.setGroupSeparatorShown(True)
        self.size_box.setToolTip("How many slots the box has. The game expands it in steps of "
                                 f"{catalog.capacity_step()}, up to {catalog.capacity_max():,}; making it smaller "
                                 "only drops empty slots from the end.")
        size_set = QPushButton("Set")
        size_set.clicked.connect(lambda: self._step(lambda box, n=self.size_box.value(): box.set_capacity(n),
                                                    f"slots to {self.size_box.value():,}"))
        size_max = QPushButton("Max")
        size_max.clicked.connect(lambda: self._step(lambda box: box.set_capacity(catalog.capacity_max()),
                                                    "slots to the most"))
        self.used_label = QLabel()
        repair = QPushButton("Repair && refill all")
        repair.setToolTip("Every weapon and armor in the box to full durability and full ammo")
        repair.clicked.connect(lambda: self._step(lambda box: box.repair_all(), "repair and refill all"))
        top = QHBoxLayout()
        top.addWidget(QLabel("Slots:"))
        top.addWidget(self.size_box)
        top.addWidget(size_set)
        top.addWidget(size_max)
        top.addWidget(self.used_label, 1)
        top.addWidget(repair)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the box: name, +level, or weapon / armor / blueprint / mushroom ...")
        self.search.textChanged.connect(self._filter)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.take_count = QSpinBox()
        self.take_count.setRange(1, 99_999)
        take = QPushButton("Take out")
        take.setToolTip("Take that many of the picked row out of the box (the most worn first)")
        take.clicked.connect(self.take_picked)
        take_all = QPushButton("Take out all of it")
        take_all.clicked.connect(lambda: self.take_picked(everything=True))
        take_row = QHBoxLayout()
        take_row.addStretch(1)
        take_row.addWidget(self.take_count)
        take_row.addWidget(take)
        take_row.addWidget(take_all)

        self.form = AddForm(catalog)
        self.form.add.clicked.connect(self.add_picked)
        for name in ("add_kind", "add_thing", "add_level", "add_flag", "add_count"):
            setattr(self, name, getattr(self.form, name))

        note = QLabel("Everything offered comes from this save's own game data, so a mod's new gear and items can "
                      "go in. New weapons and armor arrive fully repaired and loaded. Nothing is written until "
                      "Save changes.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        self.undo = QPushButton("Undo the last step")
        self.undo.clicked.connect(self.undo_last)
        self.steps_label = QLabel()
        self.steps_label.setObjectName("dim")
        self.steps_label.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.steps_label, 1)
        bottom.addWidget(self.undo)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addLayout(take_row)
        layout.addWidget(QLabel("<b>Put something in</b>"))
        layout.addWidget(self.form)
        layout.addWidget(note)
        layout.addLayout(bottom)
        self.refresh()

    # -- the steps --

    @property
    def descriptions(self) -> list[str]:
        return self.work.of("box")

    def _step(self, step, description: str) -> None:
        self.work.add("box", description, lambda save, s=step: s(savestorage.Box(save, self.catalog)))
        try:
            self.refresh()
        except saveedit.EditError as exc:
            self.work.undo("box")
            QMessageBox.warning(self, "Storage Box", str(exc))
            self.refresh()
        self.changed.emit()

    def undo_last(self) -> None:
        if self.work.undo("box"):
            self.refresh()
            self.changed.emit()

    @property
    def step_count(self) -> int:
        return len(self.descriptions)

    def stale(self) -> None:
        """A Death Bag step moved things in or out: shown again when the page is next seen."""
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, event) -> None:
        if self._stale:
            self._stale = False
            self.refresh()
        super().showEvent(event)

    # -- showing the box --

    def refresh(self) -> None:
        box = savestorage.Box(self.work.preview(), self.catalog)
        self.box = box
        self.size_box.blockSignals(True)
        self.size_box.setRange(savestorage.MIN_SLOTS, max(self.catalog.capacity_max(), box.capacity))
        self.size_box.setValue(box.capacity)
        self.size_box.blockSignals(False)
        used = box.used()
        self.used_label.setText(f"{used:,} of {box.capacity:,} used" + (" - full" if used >= box.capacity else ""))
        self.groups = box.groups()
        self.table.setRowCount(len(self.groups))
        for row, group in enumerate(self.groups):
            for column, text in enumerate((savestorage.KIND_LABELS[group.kind], group.name, group.detail,
                                           f"{group.count:,}")):
                item = QTableWidgetItem(text)
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self._filter()
        self.undo.setEnabled(bool(self.descriptions))
        self.steps_label.setText("Not saved yet: " + html.escape("; ".join(self.descriptions))
                                 if self.descriptions else "")

    def _filter(self) -> None:
        words = self.search.text().lower().split()
        for row, group in enumerate(self.groups):
            text = f"{savestorage.KIND_LABELS[group.kind]} {group.kind} {group.name} {group.detail}".lower()
            self.table.setRowHidden(row, not all(w in text for w in words))

    # -- taking out --

    def _picked(self) -> "savestorage.Group | None":
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.groups[min(rows)] if rows else None

    def take_picked(self, everything: bool = False) -> None:
        group = self._picked()
        if group is None:
            return
        count = group.count if everything else min(self.take_count.value(), group.count)
        name = f"{group.name} {group.detail}".strip()
        if everything and count > 1:
            answer = QMessageBox.question(self, "Storage Box", f"Take all {count:,} of \"{name}\" out of the box?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._step(lambda box, key=group.key, n=count: box.take(key, n), f"take out {count:,} x {name}")

    # -- putting in --

    def add_picked(self) -> None:
        wanted = self.form.picked(self)
        if wanted is None:
            return
        free = len(self.box.free_slots())
        if free == 0:
            QMessageBox.information(self, "Storage Box", "The box is full: give it more slots, or take something "
                                                         "out first.")
            return
        if wanted.count > free:
            QMessageBox.information(self, "Storage Box", f"Only {free:,} slot(s) are free, so {free:,} go in.")
        self._step(lambda box, w=wanted: w.put(box), f"add {min(wanted.count, free):,} x {wanted.name}")


@dataclass(frozen=True)
class Wanted:
    """What the add form asks for. The new records' ids and time are settled when it is
    asked, so every replay of the step makes the same records (a later step can wear them)."""
    kind: str
    ident: str
    count: int
    level: int | None
    flag: bool
    name: str
    now: int = field(default_factory=lambda: int(time.time()))
    ids: tuple = ()

    def __post_init__(self) -> None:
        if not self.ids:
            object.__setattr__(self, "ids", tuple(str(uuid.uuid4()) for _ in range(2 * self.count)))

    def put(self, container) -> int:
        return container.add(self.kind, self.ident, self.count, level=self.level,
                             grilled=self.flag and self.kind in ("mushroom", "beast"),
                             unrevealed=self.flag and self.kind == "blueprint", now=self.now, ids=self.ids)


class AddForm(QWidget):
    """Pick something from the save's own game data to put in: kind, thing, +level,
    grilled or unrevealed, how many."""

    def __init__(self, catalog: "savestorage.Catalog", parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.add_kind = QComboBox()
        for kind in ADD_KINDS:
            self.add_kind.addItem(savestorage.KIND_LABELS[kind], kind)
        self.add_kind.currentIndexChanged.connect(self._fill_things)
        self.add_thing = QComboBox()
        self.add_thing.setEditable(True)
        self.add_thing.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.add_thing.currentIndexChanged.connect(self._thing_picked)
        self.add_level = QSpinBox()
        self.add_level.setPrefix("+")
        self.add_flag = QCheckBox()
        self.add_count = QSpinBox()
        self.add_count.setRange(1, 99_999)
        self.add = QPushButton("Add")
        self.add.setObjectName("primary")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.add_kind)
        row.addWidget(self.add_thing, 1)
        row.addWidget(self.add_level)
        row.addWidget(self.add_flag)
        row.addWidget(QLabel("How many:"))
        row.addWidget(self.add_count)
        row.addWidget(self.add)
        self._fill_things()

    def _fill_things(self) -> None:
        kind = self.add_kind.currentData()
        # PlayStation-only things are left out: a save whose mods make them PC things (the Crossover
        # pack) has them as such in its own game data, and without such a mod they do not work on PC
        things = [t for t in self.catalog.things(kind) if t.platform != 1]
        self.add_thing.blockSignals(True)
        self.add_thing.clear()
        for thing in things:
            self.add_thing.addItem(thing.name, thing.ident)
        completer = QCompleter([t.name for t in things], self.add_thing)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.add_thing.setCompleter(completer)
        self.add_thing.blockSignals(False)
        self.add_flag.setText({"blueprint": "Unrevealed", "mushroom": "Grilled", "beast": "Grilled"}.get(kind, ""))
        self.add_flag.setVisible(kind in ("blueprint", "mushroom", "beast"))
        self.add_flag.setChecked(False)
        self._thing_picked()

    def _thing_picked(self) -> None:
        kind, ident = self.add_kind.currentData(), self.add_thing.currentData()
        gear = kind in ("weapon", "armor") and ident is not None
        self.add_level.setVisible(gear)
        if gear:
            low, high = self.catalog.level_range(ident)
            self.add_level.setRange(low, high)
            self.add_level.setValue(high)
            capped = self.catalog.uncapped_form(ident) and self.catalog.level_range(self.catalog.uncapped_form(ident))
            self.add_level.setToolTip(f"The upgrade level, +{low} to +{high}" + (
                f" (+{capped[0]} and up go in as its uncapped form, as a Mingo Head makes it)" if capped else ""))
        if kind == "mushroom" and ident is not None:
            roastable = bool((self.catalog.mushrooms.get(ident) or {}).get("enable_roast"))
            self.add_flag.setEnabled(roastable)
            if not roastable:
                self.add_flag.setChecked(False)
        else:
            self.add_flag.setEnabled(True)

    def picked(self, parent) -> "Wanted | None":
        """What is asked for, or None (with a word to the user) when nothing is picked."""
        kind = self.add_kind.currentData()
        index = self.add_thing.currentIndex()               # the row picked, even when two share a name
        if index < 0 or self.add_thing.itemText(index) != self.add_thing.currentText():
            index = self.add_thing.findText(self.add_thing.currentText())
        ident = self.add_thing.itemData(index) if index >= 0 else None
        if ident is None:
            QMessageBox.information(parent, "Put something in", "Pick something from the list first.")
            return None
        level = None
        if kind in ("weapon", "armor"):
            ident, level = self.catalog.part_at(ident, self.add_level.value())
        name = self.add_thing.itemText(index) + (f" +{self.add_level.value()}" if level else "")
        return Wanted(kind, ident, self.add_count.value(), level, self.add_flag.isChecked(), name)
