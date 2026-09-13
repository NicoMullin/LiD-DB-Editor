"""Stocking the vending machine by picking things off a list.

Adding an item to the machine by hand means knowing that `ITMP_ARM_WP001_002`
is the Battle Machete blueprint, that the day of the week lives in `lineup_id`,
and that the price is not in the vending table at all. This asks for none of
that: pick a day, tick the things you want, set a price if you want one.

Two facts about the game are baked in here, both checked against the database:

* **Blueprints have no names.** All 1,899 are called "RMAP" or "UNKNOWN_RMAP",
  so each is shown as the weapon or armour it makes, which is what a player
  would look for.
* **The machine carries no price of its own.** Every `pack_` and discount
  column is zero on all 315 vanilla rows, so what it charges is the item's own
  price - which is what setting a price here changes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..builder import SELLABLE_CATEGORIES, ModBuilder
from ..icons import IconSource
from .icon_cache import ThumbnailCache

ID_ROLE = Qt.ItemDataRole.UserRole


class VendingPicker(QDialog):
    """Pick items, pick a day, set a price."""

    def __init__(self, builder: ModBuilder, tab: str = "", parent=None,
                 icons: IconSource | None = None,
                 thumbnails: ThumbnailCache | None = None):
        super().__init__(parent)
        self.builder = builder
        self.added = 0
        self.icons = icons
        self.thumbnails = thumbnails
        self.setWindowTitle("Add things to the vending machine")
        self.resize(900, 640)

        self.category = QComboBox()
        self.category.addItem("Everything it can sell", "")
        for name in SELLABLE_CATEGORIES:
            self.category.addItem(name, name)
        self.category.currentIndexChanged.connect(self._reload)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, e.g. machete...")
        self.search.setClearButtonEnabled(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._reload)
        self.search.textChanged.connect(lambda: self._timer.start())

        self.list = QTableWidget()
        self.list.setColumnCount(6)
        self.list.setHorizontalHeaderLabels(
            ["What it is", "Kind", "Stars", "Kill Coins", "Recycle points", "Already sold on"]
        )
        self.list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.verticalHeader().setVisible(False)
        self.list.itemSelectionChanged.connect(self._refresh_footer)

        self.tab = QComboBox()
        for name in builder.tabs_in_use():
            self.tab.addItem(self._tab_label(name), name)
        if tab:
            index = self.tab.findData(tab)
            if index >= 0:
                self.tab.setCurrentIndex(index)

        self.set_price = QCheckBox("Set the price to")
        self.set_price.toggled.connect(lambda on: self.price.setEnabled(on))
        self.price = QSpinBox()
        self.price.setRange(0, 99_999_999)
        self.price.setValue(1)
        self.price.setEnabled(False)
        self.price_note = QLabel()
        self.price_note.setObjectName("dim")
        self.price_note.setWordWrap(True)

        self.chosen = QLabel("Nothing picked yet")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add to the machine")
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._add)
        buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.category)
        top.addWidget(self.search, 1)

        where = QHBoxLayout()
        where.addWidget(QLabel("Put it on:"))
        where.addWidget(self.tab)
        where.addSpacing(16)
        where.addWidget(self.set_price)
        where.addWidget(self.price)
        where.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addLayout(where)
        layout.addWidget(self.price_note)
        layout.addWidget(self.chosen)
        layout.addWidget(buttons)

        self.tab.currentIndexChanged.connect(self._refresh_footer)
        self._reload()
        self._refresh_footer()

    @staticmethod
    def _tab_label(name: str) -> str:
        days = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday",
                "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday",
                "SUN": "Sunday"}
        if name in days:
            return f"{days[name]} ({name})"
        if name == "COMMON":
            return "Always in stock (COMMON)"
        if name == "RE":
            return "Recycle tab (RE)"
        return name

    # -- the list ----------------------------------------------------------

    def _reload(self) -> None:
        found = self.builder.catalogue(
            category=self.category.currentData() or "",
            search=self.search.text(),
        )
        self.list.setRowCount(len(found))
        for r, item in enumerate(found):
            first = QTableWidgetItem(item.name)
            first.setData(ID_ROLE, item.item_id)
            first.setToolTip(item.item_id)
            if self.icons is not None and self.thumbnails is not None:
                picture = self.thumbnails.icon(
                    self.icons.for_id(item.item_id, item.name)
                )
                if picture is not None:
                    first.setIcon(picture)
            cells = [
                first,
                QTableWidgetItem(item.category),
                QTableWidgetItem("*" * item.rarity if item.rarity else ""),
                QTableWidgetItem(f"{item.money:,}" if item.money else ""),
                QTableWidgetItem(f"{item.recycle:,}" if item.recycle else ""),
                QTableWidgetItem(item.already.replace(",", ", ") if item.already else ""),
            ]
            for column, cell in enumerate(cells):
                self.list.setItem(r, column, cell)
        if self.icons is not None and self.icons.available:
            self.list.verticalHeader().setDefaultSectionSize(44)
        self.list.resizeColumnsToContents()
        self.list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._refresh_footer()

    def _picked(self) -> list[str]:
        rows = {i.row() for i in self.list.selectedIndexes()}
        out = []
        for r in sorted(rows):
            cell = self.list.item(r, 0)
            if cell is not None:
                out.append(cell.data(ID_ROLE))
        return out

    def _refresh_footer(self) -> None:
        picked = self._picked()
        tab = self.tab.currentData()
        self.chosen.setText(
            f"{len(picked)} picked" if picked else "Nothing picked yet"
        )
        self.ok_button.setEnabled(bool(picked))
        # Which price column a tab charges from is not written down anywhere,
        # only which one vanilla fills in, so say what will happen rather than
        # implying more certainty than there is.
        currency = self.builder.tab_defaults(tab).get("currency_type")
        column = {0: "Kill Coin", 3: "recycle-point"}.get(currency)
        if self.set_price.isChecked():
            if column:
                self.price_note.setText(
                    f"The machine has no price of its own, so this changes the "
                    f"item's {column} price - everywhere it is sold, not only here."
                )
            else:
                self.price_note.setText(
                    "This tab's currency setting is not one we have pinned down, "
                    "so the price is written to the item's Kill Coin price. "
                    "Check it in game before relying on it."
                )
        else:
            self.price_note.setText(
                "Leaving the price alone keeps whatever the item already costs."
            )

    # -- doing it ----------------------------------------------------------

    def _add(self) -> None:
        picked = self._picked()
        if not picked:
            return
        self.added = self.builder.stock_machine(
            picked,
            self.tab.currentData(),
            price=self.price.value() if self.set_price.isChecked() else None,
        )
        self.accept()
