"""The save editor's Research page (saveresearch.py does the work).

One line per part, its uncapped form folded in (+0 to +24 on one line), with the
level it is researched to. It keeps only the lines set and the blueprints given,
and writes them onto the save copy at Save changes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, saveresearch
from .decal_pages import _fixed, _table

NONE = -1                       # the level box's "not researched"
SLOT_FILTERS = {"Everything": "", "Weapons": "Weapon", "Head": "Head", "Body": "Body", "Legs": "Legs"}
SHOW_FILTERS = ("All", "Researched", "Not researched", "Blueprint known")
RAISE_TO = (("+4 (or the part's top)", 4), ("the top (+24 where it uncaps)", None))


def _shown_text(value: int) -> str:
    return "not researched" if value == NONE else f"+{value}"


class _LevelDelegate(QStyledItemDelegate):
    """The Level column: "+N", or "not researched" below +0, held to each line's range."""

    def __init__(self, page: "ResearchPage"):
        super().__init__(page.table)
        self.page = page

    def displayText(self, value, locale):
        try:
            return _shown_text(int(value))
        except (TypeError, ValueError):
            return str(value)

    def createEditor(self, parent, option, index):
        ptid = self.page.rows[index.row()].ptid
        low, high = self.page.rules.level_range(ptid)
        box = QSpinBox(parent)
        box.setPrefix("+")
        box.setRange(low if self.page.rules.initial(ptid) else NONE, high)
        box.setSpecialValueText("" if self.page.rules.initial(ptid) else "not researched")
        return box


class ResearchPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", rules: "saveresearch.ResearchRules", parent=None):
        super().__init__(parent)
        self.document, self.rules = document, rules
        self.rows = rules.rows(document)
        self.index = {r.ptid: i for i, r in enumerate(self.rows)}
        self.before = saveresearch.tops(document)
        self.known = saveresearch.known(document)
        self.wanted: dict[str, int | None] = {}     # line -> "+N" or None, in the order set
        self.blueprints: dict[str, None] = {}       # lines whose blueprint is given
        self.changes: dict[str, int] = {}
        self._loading = False

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts")
        self.search.textChanged.connect(self._filter)
        self.slot = QComboBox()
        self.slot.addItems(list(SLOT_FILTERS))
        self.slot.currentIndexChanged.connect(self._filter)
        self.show_only = QComboBox()
        self.show_only.addItems(SHOW_FILTERS)
        self.show_only.currentIndexChanged.connect(self._filter)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.slot)
        top.addWidget(self.show_only)

        self.table = _table(("Part", "Kind", "Level", "Blueprint"))
        self.table.setItemDelegateForColumn(2, _LevelDelegate(self))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.itemChanged.connect(self._edited)

        self.raise_to = QComboBox()
        for label, _ in RAISE_TO:
            self.raise_to.addItem(label)
        raise_shown = QPushButton("Research the shown ones")
        raise_shown.setToolTip("Every part the list shows now up to this level (none is lowered). The parts before "
                               "one in its upgrade chain go to their top, as the game needs.")
        raise_shown.clicked.connect(self.raise_shown)
        give = QPushButton("Give the shown blueprints")
        give.setToolTip("The blueprint of every first part of a chain the list shows, analysed and ready to "
                        "develop at Choku-Funsha (parts already researched keep their research)")
        give.clicked.connect(self.give_shown)
        clear = QPushButton("Take the shown research away")
        clear.setToolTip("Every part the list shows back to not researched (the starting weapons go to +0)")
        clear.clicked.connect(self.clear_shown)
        undo = QPushButton("Undo all")
        undo.clicked.connect(self.undo_all)
        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Up to:"))
        buttons.addWidget(self.raise_to)
        buttons.addWidget(raise_shown)
        buttons.addStretch(1)
        buttons.addWidget(give)
        buttons.addWidget(clear)
        buttons.addWidget(undo)

        self.summary = QLabel()
        self.summary.setObjectName("dim")
        self.summary.setWordWrap(True)
        self.stamp_label = QLabel()
        self.stamp_label.setWordWrap(True)

        note = QLabel("What the R&D lab has developed and how far. Double-click a level to change it. A part "
                      "that uncaps shows on one line, +0 to +24. Researching a part puts the parts before it in "
                      "its upgrade chain at their top, and taking one below its top takes away the research "
                      "after it, as the game needs. Each part's first level adds its Funshot once.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.summary)
        layout.addWidget(self.stamp_label)
        layout.addLayout(buttons)
        self._fill()

    # -- what each line is at --

    def levels(self) -> dict[str, int]:
        return {**self.before, **self.changes}

    def shown_now(self, ptid: str) -> int:
        shown = self.rules.shown(ptid, self.levels())
        return NONE if shown is None else shown

    def shown_before(self, ptid: str) -> int:
        shown = self.rules.shown(ptid, self.before)
        return NONE if shown is None else shown

    def _status(self, ptid: str, levels: dict) -> str:
        if self.rules.shown(ptid, levels) is not None:
            return "Armor Skin" if saveresearch.skin_unlocked(self.rules, ptid, levels) else ""
        if ptid in self.blueprints:
            return "given (ready to develop)"
        state = self.known.get(ptid)
        if state == saveresearch.MAP:
            return "known (ready to develop)"
        if state == saveresearch.UNREVEALED:
            return "picked up, not analysed"
        prev = self.rules.previous(ptid)
        if prev and self.rules.researchable(prev) and levels.get(prev, 0) >= self.rules.max_level(prev):
            return "ready to remodel"
        return ""

    def _fill(self) -> None:
        levels = self.levels()
        self._loading = True
        try:
            self.table.setRowCount(len(self.rows))
            for i, row in enumerate(self.rows):
                name = _fixed(row.name + (" - PlayStation only" if row.platform == 1 else ""))
                name.setToolTip(row.ptid)
                self.table.setItem(i, 0, name)
                self.table.setItem(i, 1, _fixed(row.slot))
                level = _fixed("")
                level.setFlags(level.flags() | Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, 2, level)
                self.table.setItem(i, 3, _fixed(""))
            self._show(levels)
        finally:
            self._loading = False
        self._filter()

    def _show(self, levels: dict | None = None) -> None:
        levels = self.levels() if levels is None else levels
        was = self._loading
        self._loading = True
        try:
            for i, row in enumerate(self.rows):
                shown = self.rules.shown(row.ptid, levels)
                value = NONE if shown is None else shown
                item = self.table.item(i, 2)
                item.setData(Qt.ItemDataRole.EditRole, value)
                font = QFont(item.font())
                font.setBold(value != self.shown_before(row.ptid))
                item.setFont(font)
                self.table.item(i, 3).setText(self._status(row.ptid, levels))
        finally:
            self._loading = was
        self._show_summary(levels)

    def _filter(self) -> None:
        words = self.search.text().lower().split()
        slot = list(SLOT_FILTERS.values())[self.slot.currentIndex()]
        which = SHOW_FILTERS[self.show_only.currentIndex()]
        levels = self.levels()
        for i, row in enumerate(self.rows):
            hide = not all(w in f"{row.name} {row.ptid}".lower() for w in words)
            hide = hide or bool(slot and row.slot != slot)
            researched = self.rules.shown(row.ptid, levels) is not None
            hide = hide or (which == "Researched" and not researched) or (which == "Not researched" and researched)
            hide = hide or (which == "Blueprint known" and (researched or not (
                self.known.get(row.ptid) == saveresearch.MAP or row.ptid in self.blueprints)))
            self.table.setRowHidden(i, hide)

    def _visible(self) -> list:
        return [row for i, row in enumerate(self.rows) if not self.table.isRowHidden(i)]

    # -- changing --

    def _edited(self, item) -> None:
        if self._loading or item.column() != 2:
            return
        value = int(item.data(Qt.ItemDataRole.EditRole))
        self.set_line(self.rows[item.row()].ptid, None if value == NONE else value)

    def set_line(self, ptid: str, shown: int | None) -> None:
        self.wanted.pop(ptid, None)
        self.wanted[ptid] = shown
        self._changed()

    def _changed(self) -> None:
        self.changes = saveresearch.resolve(self.document, self.rules, self.wanted)
        self._show()
        self._filter()
        self.changed.emit()

    def raise_shown(self) -> None:
        cap = RAISE_TO[self.raise_to.currentIndex()][1]
        for row in self._visible():
            if not row.offered:
                continue
            low, high = self.rules.level_range(row.ptid)
            target = high if cap is None else min(cap, high)
            now = self.rules.shown(row.ptid, self.levels())
            if now is None or now < target:
                self.wanted.pop(row.ptid, None)
                self.wanted[row.ptid] = target
        self._changed()

    def clear_shown(self) -> None:
        for row in self._visible():
            if self.rules.shown(row.ptid, self.levels()) is not None:
                self.wanted.pop(row.ptid, None)
                self.wanted[row.ptid] = None
        self._changed()

    def give_shown(self) -> None:
        for row in self._visible():
            if row.offered and self.rules.shown(row.ptid, self.levels()) is None \
                    and saveresearch.blueprint_wanted(self.document, self.rules, row.ptid):
                self.blueprints[row.ptid] = None
        self._show()
        self._filter()
        self.changed.emit()

    def undo_all(self) -> None:
        self.wanted.clear()
        self.blueprints.clear()
        self.changes = {}
        self._show()
        self._filter()
        self.changed.emit()

    # -- what it adds up to --

    def _gifts(self) -> list[str]:
        return [p for p in self.blueprints if self.changes.get(p, 0) == 0]

    @property
    def change_count(self) -> int:
        lines = {self.rules.row_of(p) for p in self.changes}
        moved = sum(1 for p in lines if self.rules.shown(p, self.before) != self.rules.shown(p, self.levels()))
        return moved + len(self._gifts())

    def _show_summary(self, levels: dict) -> None:
        researched = sum(1 for row in self.rows if self.rules.shown(row.ptid, levels) is not None)
        text = f"{researched:,} of {len(self.rows):,} parts researched"
        count = self.change_count
        if count:
            text += f" - {count:,} change(s)"
        lost = saveresearch.skins_lost(self.document, self.rules, self.changes)
        if lost:
            text += f". Armor Skins that would come off (below +{saveresearch.SKIN_LEVEL}): " + ", ".join(lost)
        self.summary.setText(text)
        have = saveresearch.stamps(self.document)
        after = saveresearch.stamps_after(self.document, self.rules, self.changes)
        parts = []
        for kind in saveresearch.STAMP_TYPES:
            old, new = have.get(kind, 0.0), after.get(kind, 0.0)
            label = saveresearch.STAMP_LABELS[kind]
            parts.append(f"{label} {old:.1f}" + (f" &rarr; <b>{new:.1f}</b>" if abs(old - new) > 1e-9 else ""))
        line = "Funshots: " + ", ".join(parts)
        if not saveresearch.stamp_flag(self.document):
            line += ("<br><span style='color:gray'>The game has not made its first Funshot count on this save "
                     "yet (it does at the first Choku-Funsha visit). Changing research sets them to the full "
                     "total and marks that count done, or the game would count this research twice.</span>")
        self.stamp_label.setText(line)

    def apply(self, save: "saveedit.SaveDocument") -> None:
        saveresearch.set_research(save, self.rules, self.wanted, self._gifts())
