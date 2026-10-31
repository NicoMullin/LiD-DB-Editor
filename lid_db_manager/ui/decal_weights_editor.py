"""A set of decals with weights, edited in a window: which decals are in, and
how often each one comes up - a stew's own odds list, say.

The list is a "decal_weights" setting's value (see settings.py). Each row has a
tick for whether the decal is in at all, and a weight: weight 3 comes up three
times as often as weight 1, and the Chance column shows what that works out to
among the decals ticked. Unticking keeps a decal on the list with its weight,
so it can be ticked back. Add decals picks more off every decal in the player's
masters.db, including ones another mod added. Until the player changes
anything the list is the mod's own query's, and leaving it at that keeps the
setting on the default (value ""), so it keeps up with the database.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..decals import Decal, default_weights, read_decals
from ..settings import MOST_DECAL_WEIGHT, MOST_DECALS, DecalWeight, ModSetting, parse_decal_weights
from .decal_picker import MISSING, _row, stars_text
from .list_picker import ListPicker

COLUMNS = ("In", "Decal", "Stars", "Category", "Weight", "Chance")
IN, DECAL, STARS, CATEGORY, WEIGHT, CHANCE = range(len(COLUMNS))


class DecalAdder(ListPicker):
    """Tick decals to add, off every decal not on the list yet."""

    def __init__(self, setting: ModSetting, decals: list[Decal], parent=None):
        super().__init__(
            setting, "", [_row(decal) for decal in decals], [],
            columns=("Decal", "Stars", "Category", "ID"), noun="decal",
            intro=("Every decal in your masters.db that is not on the list yet, including ones a "
                   "mod added. Tick the ones to add; each starts at weight 1."),
            placeholder="Search by name, category or id...",
            search_columns=(0, 2, 3),
            parent=parent,
        )
        self.setWindowTitle(f"Add decals - {setting.label}")
        self.ok_button.setText("Add these")

    def _count(self) -> None:
        if hasattr(self, "toggle_button"):
            self._label_toggle()
        picked = self.ticked()
        self.count.setText(f"{len(picked):,} ticked" if picked else "Tick the decals to add.")
        self.ok_button.setEnabled(bool(picked))


class DecalWeightsEditor(QDialog):
    """The whole list: each decal, whether it is in, and its weight."""

    def __init__(self, setting: ModSetting, value: str, db_path: Path, parent=None):
        super().__init__(parent)
        self.setting = setting
        self.result_value = value
        self.catalogue = {decal.id: decal for decal in read_decals(db_path)}
        self.default = [DecalWeight(decal, weight)
                        for decal, weight in default_weights(db_path, setting.default_sql)]
        self._filling = False
        self.setWindowTitle(f"{setting.label} - decals")
        self.resize(860, 680)

        intro = QLabel(
            "The decals in the list, and how often each comes up. Untick a decal to leave it out "
            "(it stays here, with its weight, to tick again). Weight is how often one comes up "
            "compared with the others: weight 3 comes up three times as often as weight 1. "
            "Chance is what that works out to for each pull."
        )
        intro.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, category or id...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._filter())

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(DECAL, QHeaderView.ResizeMode.Stretch)
        for column in (IN, STARS, CATEGORY, WEIGHT, CHANCE):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._ticked)

        add = QPushButton("Add decals...")
        add.clicked.connect(self.add_decals)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Take the highlighted decals off the list altogether.")
        self.remove_button.clicked.connect(self.remove_highlighted)
        all_on = QPushButton("All in")
        all_on.clicked.connect(lambda: self._tick_all(True))
        all_off = QPushButton("None in")
        all_off.clicked.connect(lambda: self._tick_all(False))
        back = QPushButton("Back to the default")
        back.setToolTip(setting.default_label or "The mod's own list")
        back.clicked.connect(lambda: self.fill(self.default))

        self.count = QLabel()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Use this list")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        for button in (add, self.remove_button, all_on, all_off, back):
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addWidget(self.count, 1)
        footer.addWidget(buttons)
        layout.addLayout(footer)

        entries = parse_decal_weights(value, setting.label) if str(value or "").strip() else self.default
        self.fill(entries)

    # -- the rows --------------------------------------------------------------

    def fill(self, entries: list[DecalWeight]) -> None:
        self._filling = True
        self.table.setRowCount(0)
        for entry in entries:
            self._append(entry)
        self._filling = False
        self._filter()
        self._count()

    def _append(self, entry: DecalWeight) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        decal = self.catalogue.get(entry.id)
        tick = QTableWidgetItem()
        tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        tick.setCheckState(Qt.CheckState.Checked if entry.on else Qt.CheckState.Unchecked)
        tick.setData(Qt.ItemDataRole.UserRole, entry.id)
        self.table.setItem(row, IN, tick)
        name = QTableWidgetItem(decal.name if decal else f"{entry.id} {MISSING}")
        name.setToolTip(entry.id)
        self.table.setItem(row, DECAL, name)
        stars = QTableWidgetItem(stars_text(decal.stars) if decal else "?")
        stars.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, STARS, stars)
        self.table.setItem(row, CATEGORY, QTableWidgetItem(decal.category if decal else "?"))
        weight = QSpinBox()
        weight.setRange(1, MOST_DECAL_WEIGHT)
        weight.setValue(entry.weight)
        weight.valueChanged.connect(lambda _v: self._count())
        self.table.setCellWidget(row, WEIGHT, weight)
        chance = QTableWidgetItem()
        chance.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, CHANCE, chance)

    def entry(self, row: int) -> DecalWeight:
        tick = self.table.item(row, IN)
        return DecalWeight(tick.data(Qt.ItemDataRole.UserRole), self.table.cellWidget(row, WEIGHT).value(),
                           tick.checkState() == Qt.CheckState.Checked)

    def entries(self) -> list[DecalWeight]:
        found: list[DecalWeight] = []
        seen: set[str] = set()
        for row in range(self.table.rowCount()):
            entry = self.entry(row)
            if entry.id not in seen:
                seen.add(entry.id)
                found.append(entry)
        return found

    def _highlighted_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _filter(self) -> None:
        words = self.search.text().casefold().split()
        for row in range(self.table.rowCount()):
            text = " ".join(
                (self.table.item(row, column).text() if self.table.item(row, column) else "")
                for column in (DECAL, CATEGORY)
            ) + " " + str(self.table.item(row, IN).data(Qt.ItemDataRole.UserRole))
            text = text.casefold()
            self.table.setRowHidden(row, not all(word in text for word in words))

    # -- the buttons -------------------------------------------------------------

    def _ticked(self, item: QTableWidgetItem) -> None:
        if not self._filling and item.column() == IN:
            self._count()

    def _tick_all(self, on: bool) -> None:
        """Every row the search shows, or every row when there is no search."""
        self._filling = True
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.item(row, IN).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._filling = False
        self._count()

    def add_decals(self) -> None:
        listed = {entry.id for entry in self.entries()}
        others = [decal for decal in self.catalogue.values() if decal.id not in listed]
        adder = DecalAdder(self.setting, others, self)
        if not adder.exec():
            return
        self.add(adder.ticked())

    def add(self, decal_ids: list[str]) -> None:
        entries = self.entries()
        listed = {entry.id for entry in entries}
        for decal_id in decal_ids:
            if len(entries) >= MOST_DECALS:
                break
            if decal_id not in listed:      # already on the list: as it is
                listed.add(decal_id)
                entries.append(DecalWeight(decal_id))
        self.fill(entries)

    def remove_highlighted(self) -> None:
        for row in reversed(self._highlighted_rows()):
            self.table.removeRow(row)
        self._count()

    def _count(self) -> None:
        if self._filling:
            return
        entries = self.entries()
        total = sum(e.weight for e in entries if e.on)
        for row, entry in enumerate(entries):
            text = f"{100 * entry.weight / total:.2f}%" if entry.on and total else ""
            self.table.item(row, CHANCE).setText(text)
        on = sum(1 for e in entries if e.on)
        if not on:
            self.count.setText("No decal is in - tick at least one.")
            self.ok_button.setEnabled(False)
            return
        text = f"{on:,} of {len(entries):,} decals in"
        if entries == self.default:
            what = self.setting.default_label or "the mod's own list"
            text += f" - the default ({what})"
        self.count.setText(text)
        self.ok_button.setEnabled(True)

    def value(self) -> str:
        """What the setting will hold: "" while the list is the default's."""
        entries = self.entries()
        if entries == self.default:
            return ""
        return ",".join(e.text() for e in entries)

    def _accept(self) -> None:
        if not any(e.on for e in self.entries()):
            return
        self.result_value = self.value()
        self.accept()
