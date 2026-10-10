"""A shop list, edited in a window: what it sells, how many at once, in which
currency, for how much, and how many times a day.

The list is a "goods" setting's value (see settings.py). Its rows are the
goods in the order they will show; Add goods picks more off everything in the
player's masters.db. A price of 0 is the game's own price for that many, shown
beside it, so leaving it alone never gets a good sold for nothing by mistake.
Until the player changes anything the list is the mod's own, and leaving it at
that keeps the setting on the default (value "").
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..goods import Good, read_goods
from ..settings import LARGEST_ALLOWED, MOST_A_DAY, MOST_GOODS, MOST_IN_A_BUNDLE, GoodsEntry, ModSetting
from .list_picker import ListPicker, PickRow

CURRENCY_NAMES = {"k": "Kill Coins", "d": "Death Metals"}
SHORT = {"k": "KC", "d": "DM"}
COLUMNS = ("Good", "Bundle", "Pay with", "Price", "Game's price", "A day")
GOOD, BUNDLE, PAY, PRICE, GAME_PRICE, A_DAY = range(len(COLUMNS))


def _money(amount: int, currency: str) -> str:
    return f"{amount:,} {SHORT[currency]}"


class GoodsChooser(ListPicker):
    """Tick goods to add, off everything in the database."""

    def __init__(self, setting: ModSetting, goods: list[Good], parent=None):
        rows = [
            PickRow(
                good.id,
                ((good.name, good.name.casefold()), (good.category, good.category.casefold()),
                 (f"{good.money:,}", good.money), (f"{good.medal:,}", good.medal)),
                "<br>".join((
                    f"<b>{html.escape(good.name)}</b> &nbsp; {html.escape(good.category)}",
                    f"{html.escape(good.id)}",
                    f"The game's price for one: {good.money:,} KC or {good.medal:,} DM.",
                )),
            )
            for good in goods
        ]
        super().__init__(
            setting, "", rows, [],
            columns=("Good", "Category", "KC", "DM"), noun="good",
            intro=("Everything the machine can sell: items, materials, blueprints and mushrooms "
                   "(weapons and armour are left out). Tick the ones to add, then set each one's "
                   "bundle, currency, price and limit in the list."),
            placeholder="Search by name, category or id...",
            search_columns=(0, 1),
            parent=parent,
        )
        self.setWindowTitle(f"Add goods - {setting.label}")
        self.ok_button.setText("Add these")

    def _count(self) -> None:
        if hasattr(self, "toggle_button"):
            self._label_toggle()
        picked = self.ticked()
        self.count.setText(f"{len(picked):,} ticked" if picked else "Tick the goods to add.")
        self.ok_button.setEnabled(bool(picked))


class GoodsEditor(QDialog):
    """The whole list: each row's bundle, currency, price and limit."""

    def __init__(self, setting: ModSetting, value: str, db_path: Path, parent=None):
        super().__init__(parent)
        self.setting = setting
        self.result_value = value
        self.catalogue = {good.id: good for good in read_goods(db_path)}
        self.default = setting.goods("")
        self.setWindowTitle(f"{setting.label} - {setting.noun or 'good'}s")
        self.resize(900, 640)

        intro = QLabel(
            "What the machine sells, in the order it shows them. Bundle is how many one "
            "purchase gives. A price of 0 is the game's own price for that many. A day is how "
            "many times it can be bought before the next daily reset; more than 1 puts it on "
            "the machine that many times."
        )
        intro.setWordWrap(True)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(GOOD, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        add = QPushButton("Add goods...")
        add.clicked.connect(self.add_goods)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_highlighted)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self.move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self.move(1))
        clear = QPushButton("Remove all")
        clear.clicked.connect(lambda: self.fill([]))
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
        for button in (add, self.remove_button, up, down, clear, back):
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addWidget(self.count, 1)
        footer.addWidget(buttons)
        layout.addLayout(footer)

        self.fill(setting.goods(value))

    # -- the rows --------------------------------------------------------------

    def fill(self, entries: list[GoodsEntry]) -> None:
        self.table.setRowCount(0)
        for entry in entries:
            self._append(entry)
        self._count()

    def _append(self, entry: GoodsEntry) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        good = self.catalogue.get(entry.id)
        name = QTableWidgetItem(good.name if good else f"{entry.id} (not in your database)")
        name.setData(Qt.ItemDataRole.UserRole, entry.id)
        name.setToolTip(f"{entry.id}" + (f" - {good.category}" if good else ""))
        self.table.setItem(row, GOOD, name)

        bundle = QSpinBox()
        bundle.setRange(1, MOST_IN_A_BUNDLE)
        bundle.setValue(entry.bundle)
        pay = QComboBox()
        for code, words in CURRENCY_NAMES.items():
            pay.addItem(words, code)
        pay.setCurrentIndex(pay.findData(entry.currency))
        price = QSpinBox()
        price.setRange(0, LARGEST_ALLOWED)
        price.setGroupSeparatorShown(True)
        price.setSpecialValueText("Game's price")
        price.setValue(entry.price)
        a_day = QSpinBox()
        a_day.setRange(0, MOST_A_DAY)
        a_day.setSpecialValueText("No limit")
        a_day.setValue(entry.a_day)
        self.table.setCellWidget(row, BUNDLE, bundle)
        self.table.setCellWidget(row, PAY, pay)
        self.table.setCellWidget(row, PRICE, price)
        self.table.setCellWidget(row, A_DAY, a_day)
        self.table.setItem(row, GAME_PRICE, QTableWidgetItem())
        bundle.valueChanged.connect(lambda _v, r=name: self._game_price(r))
        pay.currentIndexChanged.connect(lambda _i, r=name: self._game_price(r))
        for box in (bundle, price, a_day):
            box.valueChanged.connect(lambda _v: self._count())
        pay.currentIndexChanged.connect(lambda _i: self._count())
        self._game_price(name)

    def _row_of(self, name_item: QTableWidgetItem) -> int:
        return self.table.row(name_item)

    def _game_price(self, name_item: QTableWidgetItem) -> None:
        """What the game would charge for this many, shown beside the price."""
        row = self._row_of(name_item)
        if row < 0:
            return
        entry = self.entry(row)
        good = self.catalogue.get(entry.id)
        each = (good.money if entry.currency == "k" else good.medal) if good else 0
        cell = self.table.item(row, GAME_PRICE)
        cell.setText(_money(each * entry.bundle, entry.currency) if good else "?")

    def entry(self, row: int) -> GoodsEntry:
        def widget(column):
            return self.table.cellWidget(row, column)

        return GoodsEntry(
            self.table.item(row, GOOD).data(Qt.ItemDataRole.UserRole),
            widget(BUNDLE).value(), widget(PAY).currentData(),
            widget(PRICE).value(), widget(A_DAY).value(),
        )

    def entries(self) -> list[GoodsEntry]:
        found: list[GoodsEntry] = []
        for row in range(self.table.rowCount()):
            entry = self.entry(row)
            if entry not in found:
                found.append(entry)
        return found

    def _highlighted_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    # -- the buttons -------------------------------------------------------------

    def add_goods(self) -> None:
        chooser = GoodsChooser(self.setting, list(self.catalogue.values()), self)
        if not chooser.exec():
            return
        self.add(chooser.ticked())

    def add(self, good_ids: list[str]) -> None:
        for good_id in good_ids:
            if self.table.rowCount() >= MOST_GOODS:
                break
            self._append(GoodsEntry(good_id))
        self._count()

    def remove_highlighted(self) -> None:
        for row in reversed(self._highlighted_rows()):
            self.table.removeRow(row)
        self._count()

    def move(self, step: int) -> None:
        """Move the highlighted rows up or down one place, together."""
        rows = self._highlighted_rows()
        if not rows:
            return
        entries = [self.entry(r) for r in range(self.table.rowCount())]
        if (step < 0 and rows[0] == 0) or (step > 0 and rows[-1] == len(entries) - 1):
            return
        moving = [entries[r] for r in rows]
        rest = [e for r, e in enumerate(entries) if r not in rows]
        at = rows[0] + step
        entries = rest[:at] + moving + rest[at:]
        self.fill(entries)
        selection = self.table.selectionModel()
        selection.clearSelection()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for r in range(at, at + len(moving)):
            selection.select(self.table.model().index(r, GOOD), flags)

    def _count(self) -> None:
        count = self.table.rowCount()
        same = self.entries() == self.default
        if not count:
            self.count.setText("Nothing on the list - add at least one good.")
            self.ok_button.setEnabled(False)
            return
        text = f"{count:,} good{'s' if count != 1 else ''}"
        if same:
            what = self.setting.default_label or "the mod's own list"
            text += f" - the default ({what})"
        elif count >= MOST_GOODS:
            text += " - the most a list can hold"
        self.count.setText(text)
        self.ok_button.setEnabled(True)

    def value(self) -> str:
        """What the setting will hold: "" while the list is the default's."""
        entries = self.entries()
        if entries == self.default:
            return ""
        return ",".join(e.text() for e in entries)

    def _accept(self) -> None:
        if not self.table.rowCount():
            return
        self.result_value = self.value()
        self.accept()
