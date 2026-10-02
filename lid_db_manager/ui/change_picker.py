"""Choosing which of a modded database's changes to actually take.

A reworked `masters.db` usually arrives as one lump: dozens of tables retuned
at once, take it or leave it. This is the widget that breaks the lump up - a
table per top-level row, expandable to the individual edits underneath - so a
player can keep the shop changes and drop the enemy tuning.

Tables stay separate mods afterwards, so the choice is not a one-off: what is
picked here is what gets *written*, and each table's mod can still be switched
on and off in the list forever after.

Rows whose only change is an added carriage return get a group of their own,
left unticked. They are not edits: DB Browser for SQLite's Export to SQL file
and back puts one into every multi-line text row, and the game's own data has
none anywhere. Taking them would make the mod clash with every other mod that
touches those rows, and put old text back over newer. They are still offered,
not dropped, so taking them is one tick away.

The rows someone did edit went through the same round trip, so their new text
carries the carriage returns too. When a picker is made with ``offer_strip``,
a tick box - on by default, and only shown when there is something to take
out - removes them from the changes that are kept.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import dbdiff

# Past this many changed rows in one table, listing them individually stops
# being a help and starts being a wall. Such a table is offered whole or not
# at all, which is said plainly rather than silently truncating the list.
ROW_LIST_LIMIT = 2000

_TABLE_ROLE = Qt.ItemDataRole.UserRole
_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
_LINE_ENDINGS_ROLE = Qt.ItemDataRole.UserRole + 2

LINE_ENDINGS_TIP = (
    "These rows only gained a carriage return in text that already had line "
    "breaks. DB Browser for SQLite's Export to SQL file and back does that to "
    "every multi-line row - they are not real edits, and the game's own data "
    "has none. Left unticked so the mod does not clash with other mods over "
    "rows it never meant to change. Tick to take them anyway."
)

STRIP_TIP = (
    "The rows that were really edited went through DB Browser's export too, so "
    "their new text has the same invisible carriage return before each line "
    "break. The game's own text never has one. Ticked, they are taken out and "
    "the words stay exactly the same. Unticked, the text is kept exactly as it "
    "is in the file."
)


def _short(value: object, limit: int = 60) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    text = str(value).replace("\n", "\\n").replace("\r", "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _key_label(key: tuple) -> str:
    parts = [_short(v, 28) or "''" for v in key]
    return " / ".join(parts) if parts else "(row)"


class ChangePicker(QWidget):
    """A tree of tables and the edits inside them, each with a checkbox."""

    def __init__(self, delta, parent=None, *, offer_strip: bool = False):
        super().__init__(parent)
        self.delta = delta
        self._updating = False
        self._deltas = {t.table: t for t in delta.tables}
        # table -> keys of the rows whose only change is a carriage return
        self._line_endings = {t.table: dbdiff.line_ending_keys(t) for t in delta.tables}

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by table, row or value…")
        self.filter_edit.setClearButtonEnabled(True)

        all_button = QPushButton("All")
        none_button = QPushButton("None")
        for button in (all_button, none_button):
            button.setFixedWidth(60)

        top = QHBoxLayout()
        top.addWidget(self.filter_edit, 1)
        top.addWidget(all_button)
        top.addWidget(none_button)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Change", "Value"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setMinimumHeight(240)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        self.summary = QLabel()
        self.summary.setWordWrap(True)

        self.strip_box: QCheckBox | None = None
        if offer_strip and any(dbdiff.carries_carriage_returns(t, self._line_endings[t.table])
                               for t in delta.tables):
            self.strip_box = QCheckBox(
                "Remove DB Browser's line-ending marks from the changes kept (recommended)")
            self.strip_box.setChecked(True)
            self.strip_box.setToolTip(STRIP_TIP)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        if self.strip_box is not None:
            layout.addWidget(self.strip_box)
        layout.addWidget(self.summary)

        self._build()

        self.tree.itemChanged.connect(self._on_item_changed)
        self.filter_edit.textChanged.connect(self._apply_filter)
        all_button.clicked.connect(lambda: self._set_all(True))
        none_button.clicked.connect(lambda: self._set_all(False))
        self._update_summary()

    # -- building --------------------------------------------------------

    def _build(self) -> None:
        self._updating = True
        try:
            for table_delta in self.delta.tables:
                skip = self._line_endings[table_delta.table]
                if self._main_total(table_delta) or table_delta.is_new_table:
                    self._add_table(table_delta, skip)
                if skip:
                    group = QTreeWidgetItem(self.tree)
                    group.setData(0, _TABLE_ROLE, table_delta.table)
                    group.setData(0, _LINE_ENDINGS_ROLE, True)
                    group.setFlags(group.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    group.setCheckState(0, Qt.CheckState.Unchecked)
                    group.setText(0, f"{table_delta.table} - line endings only")
                    group.setText(1, f"{len(skip):,} changed")
                    group.setToolTip(0, LINE_ENDINGS_TIP)
        finally:
            self._updating = False

    def _add_table(self, table_delta, skip: set) -> None:
        parent = QTreeWidgetItem(self.tree)
        parent.setData(0, _TABLE_ROLE, table_delta.table)
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        parent.setCheckState(0, Qt.CheckState.Checked)
        parent.setText(0, table_delta.table)
        parent.setText(1, self._table_note(table_delta, skip))

        rows = self._row_entries(table_delta, skip)
        if rows is None:
            parent.setToolTip(
                0,
                "Too many changes to list one by one - this table can be "
                "taken whole or left out.",
            )
            return
        for key, label, value in rows:
            child = QTreeWidgetItem(parent)
            child.setData(0, _TABLE_ROLE, table_delta.table)
            child.setData(0, _KEY_ROLE, key)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Checked)
            child.setText(0, label)
            child.setText(1, value)

    def _main_total(self, table_delta) -> int:
        """Changes in the table's own row - everything but the line endings."""
        return (len(table_delta.updates) - len(self._line_endings[table_delta.table])
                + len(table_delta.inserts) + len(table_delta.deletes))

    def _main_keys(self, table_delta) -> set:
        skip = self._line_endings[table_delta.table]
        keys = {u.key for u in table_delta.updates if u.key not in skip}
        keys.update(dbdiff.insert_key(table_delta, v) for v in table_delta.inserts)
        keys.update(table_delta.deletes)
        return keys

    def _table_note(self, table_delta, skip: set) -> str:
        bits = []
        if table_delta.is_new_table:
            bits.append("new table")
        if len(table_delta.updates) > len(skip):
            bits.append(f"{len(table_delta.updates) - len(skip)} changed")
        if table_delta.inserts:
            bits.append(f"{len(table_delta.inserts)} added")
        if table_delta.deletes:
            bits.append(f"{len(table_delta.deletes)} removed")
        return ", ".join(bits)

    def _row_entries(self, table_delta, skip: set) -> list[tuple] | None:
        """(key, label, value) per edit, or None when the table is too big to list."""
        if self._main_total(table_delta) > ROW_LIST_LIMIT:
            return None
        if table_delta.keyed_by_rowid:
            # Rows here are matched by position, not identity - picking
            # individual ones is not something we can honestly stand behind.
            return None

        entries = []
        for update in table_delta.updates:
            if update.key in skip:
                continue
            changes = ", ".join(
                f"{column}: {_short(update.before.get(column))} → {_short(value)}"
                for column, value in update.changes.items()
            )
            entries.append((update.key, _key_label(update.key), changes))
        for values in table_delta.inserts:
            key = dbdiff.insert_key(table_delta, values)
            if key is None:
                return None
            entries.append((key, _key_label(key), "added"))
        for key in table_delta.deletes:
            entries.append((key, _key_label(key), "removed"))
        return entries

    # -- checking --------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        try:
            if item.parent() is None:
                state = item.checkState(0)
                if state != Qt.CheckState.PartiallyChecked:
                    for index in range(item.childCount()):
                        item.child(index).setCheckState(0, state)
            else:
                self._refresh_parent(item.parent())
        finally:
            self._updating = False
        self._update_summary()

    def _refresh_parent(self, parent: QTreeWidgetItem) -> None:
        checked = sum(
            1
            for index in range(parent.childCount())
            if parent.child(index).checkState(0) == Qt.CheckState.Checked
        )
        if checked == 0:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        elif checked == parent.childCount():
            parent.setCheckState(0, Qt.CheckState.Checked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._updating = True
        try:
            for index in range(self.tree.topLevelItemCount()):
                parent = self.tree.topLevelItem(index)
                if parent.isHidden():
                    continue
                parent.setCheckState(0, state)
                for child_index in range(parent.childCount()):
                    parent.child(child_index).setCheckState(0, state)
        finally:
            self._updating = False
        self._update_summary()

    # -- filtering -------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(index)
            if not needle:
                parent.setHidden(False)
                for child_index in range(parent.childCount()):
                    parent.child(child_index).setHidden(False)
                continue
            table_matches = needle in parent.text(0).lower()
            visible_children = 0
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                hit = table_matches or needle in (
                    child.text(0) + " " + child.text(1)
                ).lower()
                child.setHidden(not hit)
                visible_children += 1 if hit else 0
            parent.setHidden(not (table_matches or visible_children))
            if visible_children and not table_matches:
                parent.setExpanded(True)

    # -- results ---------------------------------------------------------

    def _picked(self) -> dict[str, tuple]:
        """table -> (all its own changes?, the rows picked, its line endings?)."""
        picked: dict[str, tuple] = {}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            table = item.data(0, _TABLE_ROLE)
            whole, keys, line_endings = picked.get(table, (False, set(), False))
            state = item.checkState(0)
            if state == Qt.CheckState.Unchecked:
                pass
            elif item.data(0, _LINE_ENDINGS_ROLE):
                line_endings = True
            elif item.childCount() == 0 or state == Qt.CheckState.Checked:
                whole = True
            else:
                keys = keys | {
                    item.child(i).data(0, _KEY_ROLE)
                    for i in range(item.childCount())
                    if item.child(i).checkState(0) == Qt.CheckState.Checked
                }
            picked[table] = (whole, keys, line_endings)
        return picked

    def selection(self) -> dict:
        """What to keep, in the shape ``DbDelta.filtered`` wants."""
        chosen: dict = {}
        for table, (whole, keys, line_endings) in self._picked().items():
            skip = self._line_endings[table]
            own = self._main_total(self._deltas[table]) > 0 or self._deltas[table].is_new_table
            if (whole or not own) and (line_endings or not skip):
                if whole or line_endings:
                    chosen[table] = dbdiff.ALL_ROWS
                continue
            if whole:
                keys = keys | self._main_keys(self._deltas[table])
            if line_endings:
                keys = keys | skip
            if keys:
                chosen[table] = keys
        return chosen

    def kept_counts(self) -> tuple[int, int, int]:
        """(changes kept, changes offered, tables kept)."""
        kept = tables = 0
        for table, (whole, keys, line_endings) in self._picked().items():
            count = self._main_total(self._deltas[table]) if whole else len(keys)
            count += len(self._line_endings[table]) if line_endings else 0
            kept += count
            if count or whole:
                tables += 1
        return kept, self.delta.change_count, tables

    def strip_carriage_returns(self) -> bool:
        """Whether the changes kept should have their carriage returns removed."""
        return self.strip_box is not None and self.strip_box.isChecked()

    def left_out_line_endings(self) -> int:
        """How many line-ending-only changes are unticked right now."""
        return sum(len(self._line_endings[table])
                   for table, (_whole, _keys, line_endings) in self._picked().items()
                   if not line_endings)

    def _update_summary(self) -> None:
        kept, offered, tables = self.kept_counts()
        if kept == offered:
            text = f"Taking all {offered:,} change(s), across {tables} table(s)."
        else:
            text = f"Taking {kept:,} of {offered:,} change(s), across {tables} table(s)."
        left_out = self.left_out_line_endings()
        if left_out:
            text += (f" {left_out:,} of them only add line endings and are left out -"
                     ' see the "line endings only" rows.')
        self.summary.setText(text)
