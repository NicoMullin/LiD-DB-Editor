"""The save editor's decal and mastery pages (savedecals.py does the work).

- DecalStockPage: how many of each decal are held (a top tab).
- MasteryPage: each weapon type's mastery level (a top tab).
- WornDecalsPanel: the decals one fighter wears (a tab of the Fighters page).

Each keeps only what was changed, and writes it onto the save copy at Save changes.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, savedecals

ROLE_ID = Qt.ItemDataRole.UserRole


class _SpinDelegate(QStyledItemDelegate):
    """A number column edited with a spin box held to a range (the high end per row)."""

    def __init__(self, low: int, high, parent=None):
        super().__init__(parent)
        self.low, self.high = low, high             # high: an int, or a function of the row

    def createEditor(self, parent, option, index):
        box = QSpinBox(parent)
        box.setRange(self.low, self.high(index.row()) if callable(self.high) else self.high)
        return box


def _table(columns: tuple) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in range(1, len(columns)):
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    return table


def _number(value: int) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setData(Qt.ItemDataRole.EditRole, int(value))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _fixed(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


# -- the decal stock ---------------------------------------------------------------------

class DecalStockPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savedecals.DecalCatalog", parent=None):
        super().__init__(parent)
        self.document, self.catalog = document, catalog
        self.held = savedecals.stock(document)
        self.counts: dict[str, int] = {}            # sklid -> the count wanted, where changed
        self._loading = False

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: name, what it does, or Premium")
        self.search.textChanged.connect(self._filter)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)

        self.table = _table(("Decal", "Kind", "Held"))
        self.table.setItemDelegateForColumn(2, _SpinDelegate(0, savedecals.COUNT_MAX, self.table))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._edited)

        self.bulk = QSpinBox()
        self.bulk.setRange(0, savedecals.COUNT_MAX)
        self.bulk.setValue(savedecals.COUNT_MAX)
        set_shown = QPushButton("Set the shown ones")
        set_shown.setToolTip("Every decal the list shows now (after the search) to this many")
        set_shown.clicked.connect(self.set_shown)
        undo = QPushButton("Undo all")
        undo.clicked.connect(self.undo_all)
        self.summary = QLabel()
        self.summary.setObjectName("dim")
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(QLabel("Held:"))
        bottom.addWidget(self.bulk)
        bottom.addWidget(set_shown)
        bottom.addWidget(undo)

        note = QLabel(f"How many of each decal you hold, 0 to {savedecals.COUNT_MAX} (the game holds no more). "
                      "Double-click a count to change it. Putting a decal on a fighter or taking it off in this "
                      "editor (Fighters > Decals) leaves these counts as they are.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)
        self._fill()

    def _count(self, sklid: str) -> int:
        return self.counts.get(sklid, self.held.get(sklid, 0))

    def _fill(self) -> None:
        shown = self.catalog.offered()             # what this save's game data has for PC, and what is held
        extra = [savedecals.Decal(i, i, "", False, 0, "") for i in self.held if i not in self.catalog.decals]
        listed = {d.ident for d in shown}
        shown += [self.catalog.decals[i] for i in self.held if i in self.catalog.decals and i not in listed]
        self.rows = [d.ident for d in shown + extra]
        self._loading = True
        try:
            self.table.setRowCount(len(self.rows))
            for row, decal in enumerate(shown + extra):
                name = _fixed(decal.label + (" - PlayStation only" if decal.platform == 1 else ""))
                name.setToolTip(decal.description)
                self.table.setItem(row, 0, name)
                self.table.setItem(row, 1, _fixed(self.catalog.categories.get(decal.category, "")))
                self.table.setItem(row, 2, _number(self._count(decal.ident)))
        finally:
            self._loading = False
        self._filter()
        self._show_summary()

    def _filter(self) -> None:
        words = self.search.text().lower().split()
        for row, sklid in enumerate(self.rows):
            decal = self.catalog.decals.get(sklid)
            text = f"{decal.label} {decal.description}".lower() if decal else sklid.lower()
            self.table.setRowHidden(row, not all(w in text for w in words))

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 2:
            return
        self._set(self.rows[item.row()], int(item.data(Qt.ItemDataRole.EditRole) or 0))
        self._show_summary()
        self.changed.emit()

    def _set(self, sklid: str, count: int) -> None:
        count = max(0, min(savedecals.COUNT_MAX, count))
        if count == self.held.get(sklid, 0):
            self.counts.pop(sklid, None)
        else:
            self.counts[sklid] = count

    def set_shown(self) -> None:
        value = self.bulk.value()
        for row, sklid in enumerate(self.rows):
            if not self.table.isRowHidden(row):
                self._set(sklid, value)
        self._fill()
        self.changed.emit()

    def undo_all(self) -> None:
        self.counts.clear()
        self._fill()
        self.changed.emit()

    def _show_summary(self) -> None:
        kinds = sum(1 for i in set(self.held) | set(self.counts) if self._count(i) > 0)
        self.summary.setText(f"{kinds:,} kinds held, {sum(self._count(i) for i in set(self.held) | set(self.counts)):,}"
                             " decals in all" + (f" - {len(self.counts):,} changed" if self.counts else ""))

    @property
    def change_count(self) -> int:
        return len(self.counts)

    def apply(self, save: "saveedit.SaveDocument") -> None:
        savedecals.set_stock(save, self.catalog, self.counts)


# -- weapon mastery --------------------------------------------------------------------------

class MasteryPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savedecals.DecalCatalog", parent=None):
        super().__init__(parent)
        self.document, self.catalog = document, catalog
        have = savedecals.mastery(document)
        self.types = sorted((p for p in catalog.weapon_types if p in catalog.thresholds),
                            key=lambda p: catalog.weapon_types[p].lower())
        self.before = {p: savedecals._int((have.get(p) or {}).get("lvl"), 1) for p in self.types}
        self.points = {p: savedecals._int((have.get(p) or {}).get("abp"), -1) for p in self.types}
        self.levels: dict[str, int] = {}            # ptarmtp -> the level wanted, where changed
        self._loading = False

        self.table = _table(("Weapon type", "Level", "Points"))
        self.table.setItemDelegateForColumn(
            1, _SpinDelegate(1, lambda row: self.catalog.top_level(self.types[row]), self.table))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._edited)
        max_all = QPushButton("Max all")
        max_all.setToolTip("Every weapon type to its top level")
        max_all.clicked.connect(self.max_all)
        undo = QPushButton("Undo all")
        undo.clicked.connect(self.undo_all)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(max_all)
        bottom.addWidget(undo)
        note = QLabel("The game works a weapon type's level out from its points, so setting a level sets the "
                      "points to what that level needs. A lower level loses the points above it. Double-click a "
                      "level to change it.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)
        self._fill()

    def _level(self, ptarmtp: str) -> int:
        return self.levels.get(ptarmtp, self.before[ptarmtp])

    def _points(self, ptarmtp: str) -> int:
        if ptarmtp not in self.levels:
            return self.points[ptarmtp]
        level, points = self.levels[ptarmtp], self.points[ptarmtp]
        if self.catalog.level_of(ptarmtp, max(points, 0)) == level and not (level > 1 and points < 0):
            return points
        return points if level <= 1 and points < 0 else self.catalog.points_for(ptarmtp, level)

    def _fill(self) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(self.types))
            for row, ptarmtp in enumerate(self.types):
                self.table.setItem(row, 0, _fixed(self.catalog.weapon_types[ptarmtp]))
                self.table.setItem(row, 1, _number(self._level(ptarmtp)))
                points = self._points(ptarmtp)
                self.table.setItem(row, 2, _fixed("never used" if points < 0 else f"{points:,}"))
        finally:
            self._loading = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        self._set(self.types[item.row()], int(item.data(Qt.ItemDataRole.EditRole) or 1))
        self._fill()
        self.changed.emit()

    def _set(self, ptarmtp: str, level: int) -> None:
        level = max(1, min(level, self.catalog.top_level(ptarmtp)))
        if level == self.before[ptarmtp]:
            self.levels.pop(ptarmtp, None)
        else:
            self.levels[ptarmtp] = level

    def max_all(self) -> None:
        for ptarmtp in self.types:
            self._set(ptarmtp, self.catalog.top_level(ptarmtp))
        self._fill()
        self.changed.emit()

    def undo_all(self) -> None:
        self.levels.clear()
        self._fill()
        self.changed.emit()

    @property
    def change_count(self) -> int:
        return len(self.levels)

    def apply(self, save: "saveedit.SaveDocument") -> None:
        savedecals.set_mastery(save, self.catalog, self.levels)


# -- what a fighter wears ------------------------------------------------------------------

class WornDecalsPanel(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savedecals.DecalCatalog", fighters: dict,
                 slots_for, parent=None):
        super().__init__(parent)
        self.document, self.catalog, self.fighters, self.slots_for = document, catalog, fighters, slots_for
        self.layouts: dict[str, dict] = {}          # cid -> {slot: sklid}, where changed
        self.cid: str | None = None
        self._loading = False

        self.lock = QLabel()
        self.lock.setWordWrap(True)
        self.count_label = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.count_label, 1)
        self.boxes: list[QComboBox] = []
        form = QFormLayout()
        for slot in range(savedecals.WORN_MAX):
            box = QComboBox()
            box.setEditable(True)
            box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            box.activated.connect(lambda _i, s=slot: self._picked(s))
            box.lineEdit().editingFinished.connect(lambda s=slot: self._typed(s))
            self.boxes.append(box)
            form.addRow(f"Slot {slot + 1}:", box)
        self.description = QLabel()
        self.description.setObjectName("dim")
        self.description.setWordWrap(True)
        undo = QPushButton("Undo this fighter's decals")
        undo.clicked.connect(self.undo_fighter)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(undo)
        note = QLabel("A fighter wears up to their decal slots, and one of each decal (a decal and its Premium "
                      "version count as two). Putting one on here does not use one up from the decal stock, and "
                      "taking one off does not give it back - set the stock on the Decals tab.")
        note.setObjectName("dim")
        note.setWordWrap(True)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(top)
        body.addLayout(form)
        body.addWidget(self.description)
        body.addLayout(buttons)
        body.addWidget(note)
        body.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.lock)
        layout.addWidget(self.body, 1)
        self._refill()

    # -- what is worn --

    def layout_now(self, cid: str) -> dict[int, str]:
        return dict(self.layouts.get(cid, savedecals.worn(self.document, cid)))

    def fighter_changes(self, cid: str) -> int:
        if cid not in self.layouts:
            return 0
        before = savedecals.worn(self.document, cid)
        now = self.layouts[cid]
        return sum(1 for s in set(before) | set(now) if before.get(s) != now.get(s))

    @property
    def change_count(self) -> int:
        return sum(self.fighter_changes(cid) for cid in self.layouts)

    def apply(self, save: "saveedit.SaveDocument", check: set | None = None) -> None:
        """Every changed fighter's decals onto ``save``; those (and ``check``) held to their slots."""
        for cid in set(self.layouts) | set(check or ()):
            f = self.fighters.get(cid)
            if f is None:
                continue
            layout = self.layout_now(cid)
            why = savedecals.check_worn(layout, self.slots_for(cid), self.catalog, f.name)
            if why and (cid in self.layouts or len(layout) > self.slots_for(cid)):
                raise saveedit.EditError(why)
            if cid in self.layouts:
                savedecals.set_worn(save, self.catalog, cid, layout, self.slots_for(cid), f.name)

    def worn_ids(self, cid: str) -> list[str]:
        return [d for _, d in sorted(self.layout_now(cid).items()) if d]

    # -- showing a fighter --

    def show_fighter(self, cid: str, reason: str) -> None:
        self.cid = cid
        self.lock.setText(f"<b>{html.escape(reason)}</b>" if reason else "")
        self.lock.setVisible(bool(reason))
        self.body.setEnabled(not reason)
        self.check()

    def check(self) -> None:
        """Show the fighter's decals, with only their slots open."""
        if self.cid is None:
            return
        layout = self.layout_now(self.cid)
        slots = self.slots_for(self.cid)
        self._loading = True
        try:
            for slot, box in enumerate(self.boxes):
                current = layout.get(slot)
                index = box.findData(current) if current else 0
                if current and index < 0:
                    box.addItem(self.catalog.label(current), current)
                    index = box.count() - 1
                box.setCurrentIndex(index)
                box.setEnabled(slot < slots or bool(current))
        finally:
            self._loading = False
        used = len([d for d in layout.values() if d])
        why = savedecals.check_worn(layout, slots, self.catalog, self.fighters[self.cid].name)
        text = f"{used} of {slots} decal slot(s) used"
        self.count_label.setText(f"<span style='color:#c0392b'>{html.escape(text)} - {html.escape(why)}</span>"
                                 if why else text)
        worn = [self.catalog.decals.get(d) for _, d in sorted(layout.items())]
        self.description.setText("<br>".join(f"<b>{html.escape(d.label)}</b>: {html.escape(d.description)}"
                                             for d in worn if d))

    def _refill(self) -> None:
        decals = self.catalog.offered()
        names = [d.label for d in decals]
        for box in self.boxes:
            box.blockSignals(True)
            box.clear()
            box.addItem("(empty)", None)
            for decal in decals:
                box.addItem(decal.label, decal.ident)
                box.setItemData(box.count() - 1, decal.description, Qt.ItemDataRole.ToolTipRole)
            completer = QCompleter(["(empty)"] + names, box)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            box.setCompleter(completer)
            box.blockSignals(False)
        self.check()

    # -- changing --

    def _typed(self, slot: int) -> None:
        box = self.boxes[slot]
        index = box.findText(box.currentText())
        if index >= 0 and box.itemData(index) != self.layout_now(self.cid or "").get(slot):
            box.setCurrentIndex(index)
            self._picked(slot)
        elif index < 0:
            self.check()                            # not a decal: back to what was there

    def _picked(self, slot: int) -> None:
        if self._loading or self.cid is None:
            return
        sklid = self.boxes[slot].currentData()
        layout = self.layout_now(self.cid)
        if sklid and any(d == sklid for s, d in layout.items() if s != slot):
            QMessageBox.information(self, "Decals", f"{self.fighters[self.cid].name or 'This fighter'} already "
                                                    f"wears {self.catalog.label(sklid)} - the game allows one of each.")
            self.check()
            return
        if sklid:
            layout[slot] = sklid
        else:
            layout.pop(slot, None)
        if layout == savedecals.worn(self.document, self.cid):
            self.layouts.pop(self.cid, None)
        else:
            self.layouts[self.cid] = layout
        self.check()
        self.changed.emit()

    def undo_fighter(self) -> None:
        if self.cid in self.layouts:
            del self.layouts[self.cid]
            self.check()
            self.changed.emit()
