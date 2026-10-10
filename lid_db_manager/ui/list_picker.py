"""A window for ticking any number of things off a list.

The decal picker and the "pick" setting's picker are this window with
different rows. The list sorts by any column, a search narrows it, and a tick
decides what is in - one at a time, or every highlighted row at once (drag,
Ctrl or Shift to highlight, then the button or Space). A panel under the list
says what the highlighted row is. Until the player changes anything it shows
the mod's own default ticked, and leaving it at that keeps the setting on the
default (value "") rather than freezing today's answer into a list.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from ..settings import ModSetting

SORT_ROLE = Qt.ItemDataRole.UserRole
ID_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True)
class PickRow:
    """One line of the list: its id, and each column's text and sort key."""

    id: str
    cells: tuple[tuple[str, object], ...]
    about: str = ""          # the panel's text for it, already escaped HTML


class _SortItem(QTableWidgetItem):
    """A cell that sorts by its own key (stars as numbers, names without case)."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine, theirs = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if mine is None or theirs is None:
            return super().__lt__(other)
        return mine < theirs


class ListPicker(QDialog):
    """Tick the rows a setting holds.

    ``rows`` are the list, ``default`` the ids the mod means until the player
    picks, ``missing`` rows for picked ids no longer in the list (shown so they
    can be seen and unticked). ``noun`` names one row ("decal", "quest").
    """

    def __init__(self, setting: ModSetting, value: str, rows: list[PickRow], default: list[str],
                 *, columns: tuple[str, ...], noun: str, intro: str, placeholder: str = "",
                 missing: list[PickRow] | None = None, search_columns: tuple[int, ...] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setting = setting
        self.noun = noun
        self.rows = list(rows)
        self.missing = list(missing or [])
        self.by_id = {row.id: row for row in self.rows + self.missing}
        known = {row.id for row in self.rows}
        self.default = [i for i in default if i in known]
        picked = setting.decal_ids(value)
        start = set(picked) if picked else set(self.default)
        self.result_value = value
        self._search_columns = search_columns if search_columns is not None else tuple(range(len(columns)))
        self.setWindowTitle(f"Choose {self._plural(2)} - {setting.label}")
        self.resize(820, 640)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder or "Search...")
        self.search.setClearButtonEnabled(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._filter)
        self.search.textChanged.connect(lambda: self._timer.start())

        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(columns)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._fill(start)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.itemChanged.connect(lambda _item: self._count())
        self.table.itemSelectionChanged.connect(self._show_highlighted)

        self.about = QTextBrowser()
        self.about.setOpenLinks(False)
        self.about.setMaximumHeight(140)
        self.about.setPlaceholderText(f"Click a {noun} to read about it.")

        self.toggle_button = QPushButton("Tick highlighted")
        self.toggle_button.setToolTip(f"Tick every highlighted {noun}, or untick them if they "
                                      "are all ticked already (Space does the same)")
        self.toggle_button.clicked.connect(self.toggle_highlighted)
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self.table)
        space.setContext(Qt.ShortcutContext.WidgetShortcut)
        space.activated.connect(self.toggle_highlighted)

        tick = QPushButton("Tick all shown")
        tick.clicked.connect(lambda: self._tick_shown(True))
        untick = QPushButton("Untick all shown")
        untick.clicked.connect(lambda: self._tick_shown(False))
        back = QPushButton("Back to the default")
        back.setToolTip(setting.default_label or "The mod's own choice")
        back.clicked.connect(self._back_to_default)

        self.count = QLabel()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Use these")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        label = QLabel(intro)
        label.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.toggle_button)
        row.addWidget(tick)
        row.addWidget(untick)
        row.addWidget(back)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.about)
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addWidget(self.count, 1)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self._count()

    def _plural(self, count: int) -> str:
        return self.noun if count == 1 else f"{self.noun}s"

    # -- the list --------------------------------------------------------------

    def _fill(self, ticked: set[str]) -> None:
        everything = self.rows + self.missing
        self.table.setRowCount(len(everything))
        for row, entry in enumerate(everything):
            for column, (text, key) in enumerate(entry.cells):
                item = _SortItem(text)
                item.setData(SORT_ROLE, key)
                if column == 0:
                    item.setData(ID_ROLE, entry.id)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if entry.id in ticked else Qt.CheckState.Unchecked)
                self.table.setItem(row, column, item)

    def _rows(self):
        for row in range(self.table.rowCount()):
            yield row, self.table.item(row, 0)

    def ticked(self) -> list[str]:
        """The ticked ids, in the list's own order (then any missing)."""
        on = {item.data(ID_ROLE) for _, item in self._rows()
              if item.checkState() == Qt.CheckState.Checked}
        return [entry.id for entry in self.rows + self.missing if entry.id in on]

    def highlighted(self) -> list[QTableWidgetItem]:
        """The tick cells of the highlighted rows still shown by the search."""
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        return [self.table.item(row, 0) for row in sorted(rows) if not self.table.isRowHidden(row)]

    def toggle_highlighted(self) -> None:
        """Tick every highlighted row - or, when they are all ticked, untick them."""
        items = self.highlighted()
        if not items:
            return
        on = not all(item.checkState() == Qt.CheckState.Checked for item in items)
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        try:
            for item in items:
                item.setCheckState(state)
        finally:
            self.table.blockSignals(False)
        self._count()

    def _show_highlighted(self) -> None:
        current = self.table.currentRow()
        item = self.table.item(current, 0) if current >= 0 else None
        entry = self.by_id.get(item.data(ID_ROLE)) if item is not None else None
        self.about.setHtml(entry.about if entry is not None else "")
        self._label_toggle()

    def _label_toggle(self) -> None:
        items = self.highlighted()
        self.toggle_button.setEnabled(bool(items))
        if not items:
            self.toggle_button.setText("Tick highlighted")
        elif all(item.checkState() == Qt.CheckState.Checked for item in items):
            self.toggle_button.setText(f"Untick highlighted ({len(items):,})")
        else:
            self.toggle_button.setText(f"Tick highlighted ({len(items):,})")

    def _filter(self) -> None:
        words = self.search.text().casefold().split()
        for row, _ in self._rows():
            text = " ".join(
                self.table.item(row, column).text() for column in self._search_columns
            ).casefold()
            self.table.setRowHidden(row, not all(word in text for word in words))
        self._label_toggle()

    def _tick_shown(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        try:
            for row, item in self._rows():
                if not self.table.isRowHidden(row):
                    item.setCheckState(state)
        finally:
            self.table.blockSignals(False)
        self._count()

    def _back_to_default(self) -> None:
        wanted = set(self.default)
        self.table.blockSignals(True)
        try:
            for _, item in self._rows():
                item.setCheckState(
                    Qt.CheckState.Checked if item.data(ID_ROLE) in wanted else Qt.CheckState.Unchecked
                )
        finally:
            self.table.blockSignals(False)
        self._count()

    def _count(self) -> None:
        if hasattr(self, "toggle_button"):
            self._label_toggle()
        picked = self.ticked()
        total = f"{len(self.rows):,} {self._plural(len(self.rows))}"
        if not picked:
            self.count.setText(f"Nothing ticked - pick at least one {self.noun}.")
        elif set(picked) == set(self.default):
            what = self.setting.default_label or "the mod's own choice"
            self.count.setText(f"{len(picked):,} of {total} ticked - the default ({what})")
        else:
            self.count.setText(f"{len(picked):,} of {total} ticked")
        self.ok_button.setEnabled(bool(picked))

    def value(self) -> str:
        """What the setting will hold: "" while the ticks are the default's, so
        the default keeps standing for itself; otherwise the ticked ids."""
        picked = self.ticked()
        if set(picked) == set(self.default):
            return ""
        return ",".join(picked)

    def _accept(self) -> None:
        if not self.ticked():
            return
        self.result_value = self.value()
        self.accept()


class OptionPicker(ListPicker):
    """Tick the entries a "pick" setting holds, off the mod's own list."""

    def __init__(self, setting: ModSetting, value: str, parent=None):
        noun = setting.noun or "item"
        rows = [
            PickRow(
                option.value,
                ((option.label, option.label.casefold()), (option.group, option.group.casefold())),
                "<br>".join(part for part in (
                    f"<b>{html.escape(option.label)}</b>"
                    + (f" &nbsp; {html.escape(option.group)}" if option.group else ""),
                    html.escape(option.help).replace("\n", "<br>"),
                ) if part),
            )
            for option in setting.options
        ]
        super().__init__(
            setting, value, rows, list(setting.default_picks),
            columns=(noun[:1].upper() + noun[1:], "Group"), noun=noun,
            intro=(f"Every {noun} this mod offers. Click a column's heading to sort by it. "
                   "Drag, or hold Ctrl or Shift, to highlight several, then tick them all at once."),
            placeholder="Search...",
            parent=parent,
        )
