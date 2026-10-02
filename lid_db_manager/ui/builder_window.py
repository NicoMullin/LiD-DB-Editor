"""Making a mod by changing values, without writing a line of SQL.

Most people who want a mod want one specific thing: revives to cost 1 Kill
Coin, a weapon to be cheaper, the vending machine to sell something useful.
Doing that today means learning table names and writing SQL against a database
with 221 of them.

This is the other way in. Pick a heading, pick a thing, change the numbers,
press Save as mod. What comes out is an ordinary mod - it appears in the list
unticked, shows its own diff and plain-English summary, and can be switched off
again like any other.

Two ways to look at a table, because one does not fit both shapes:

* **As a list** - pick a weapon, see everything about it on a form. Right for
  the tables where each row is a *thing*, and for the wide ones: a weapon has
  90 columns, and no grid that wide is readable.
* **As a table** - a grid. Right for the ones where each row is a *number in a
  series*, like the bank's 99 levels, where you want to see the curve.

Clicking a heading shows what is under it; "Find a price" lists every column
measured in one currency, across all tables. Above each table sit the things
worth knowing before editing it - most of them learned from a mod that did
nothing, or crashed the game.

The window is deliberately thin: every edit goes through ``builder.ModBuilder``,
which owns the scratch database and the rules about what may be changed.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import builder_data
from ..builder import LINEUP_TABLE, PAGE, SCHEDULE_TABLE, BuildError, ModBuilder
from ..icons import IconSource
from .icon_cache import ThumbnailCache
from .theme import colors

TABLE_ROLE = Qt.ItemDataRole.UserRole
KEY_ROLE = Qt.ItemDataRole.UserRole + 1
# What a tree entry that is not a table stands for: a heading, or a currency.
PAGE_ROLE = Qt.ItemDataRole.UserRole + 2
COLUMN_ROLE = Qt.ItemDataRole.UserRole + 3

# Past this many editable columns a grid stops being readable, so such a table
# opens as a form unless you ask for the grid.
WIDE = 12

# The pages of the right-hand stack.
FORM_PAGE, GRID_PAGE, LINKS_PAGE = 0, 1, 2


class BuilderWindow(QDialog):
    def __init__(self, manager, dark: bool = True, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.dark = dark
        self.palette_ = colors(dark)
        self.builder: ModBuilder | None = None
        self.table: str | None = None
        self.offset = 0
        self._loading = False
        self._editable: list = []
        self._rows: list = []
        self._fields: dict[str, QLineEdit] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._wanted_column = ""
        # Artwork, if the player has pointed us at some. None ships with the
        # program - see lid_db_manager/icons.py.
        self.icons = IconSource(manager.state.settings.icon_folder or None)
        self.thumbnails = ThumbnailCache(manager.paths.root)

        self.setWindowTitle("Build a mod")
        self.resize(1240, 800)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(260)
        self.tree.currentItemChanged.connect(self._on_pick)

        self.title_label = QLabel()
        self.title_label.setObjectName("panelTitle")
        self.about_label = QLabel()
        self.about_label.setWordWrap(True)
        self.about_label.setObjectName("dim")
        self.tips_label = QLabel()
        self.tips_label.setWordWrap(True)
        self.tips_label.setTextFormat(Qt.TextFormat.PlainText)
        self.tips_label.setStyleSheet(
            f"QLabel {{ border-left: 3px solid {self.palette_['highlight']};"
            f" padding: 4px 8px; }}"
        )

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this list...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reload)

        self.mode = QComboBox()
        self.mode.addItem("Show as a list", "form")
        self.mode.addItem("Show as a table", "grid")
        self.mode.currentIndexChanged.connect(self._on_mode)

        self.copy_button = QPushButton("Copy this row...")
        self.copy_button.setToolTip(
            "Add a new row that starts as a copy of the one picked - a new "
            "grade, a new decal, a new month."
        )
        self.copy_button.clicked.connect(self._copy_row)
        self.stock_button = QPushButton("Add things to the machine...")
        self.stock_button.clicked.connect(self._stock_machine)
        self.rotate_button = QPushButton("Keep the machine rotating...")
        self.rotate_button.setToolTip(
            "The stock schedule ran out on 1 August 2026. This adds months so "
            "the machine has something to restock from."
        )
        self.rotate_button.clicked.connect(self._extend_schedule)

        # -- the list-and-form page
        self.row_list = QListWidget()
        self.row_list.setMinimumWidth(240)
        self.row_list.currentRowChanged.connect(self._show_one)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(self.form_host)
        detail = QSplitter()
        detail.addWidget(self.row_list)
        detail.addWidget(form_scroll)
        detail.setStretchFactor(1, 1)

        # -- the grid page
        self.grid = QTableWidget()
        self.grid.setAlternatingRowColors(True)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.itemChanged.connect(self._on_cell_changed)

        # -- the page of links a heading or a currency shows
        self.links = QListWidget()
        self.links.itemActivated.connect(self._follow_link)
        self.links.itemClicked.connect(self._follow_link)

        self.stack = QStackedWidget()
        self.stack.addWidget(detail)
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.links)

        self.count_label = QLabel()
        self.count_label.setObjectName("dim")
        self.prev_button = QPushButton("< Previous")
        self.next_button = QPushButton("Next >")
        self.prev_button.clicked.connect(lambda: self._page(-1))
        self.next_button.clicked.connect(lambda: self._page(1))

        self.bulk_column = QComboBox()
        self.bulk_column.setMinimumWidth(230)
        self.set_all = QPushButton("Set every one shown to...")
        self.set_all.clicked.connect(self._set_all)
        self.multiply = QPushButton("Multiply by...")
        self.multiply.clicked.connect(self._multiply)

        # The last edit's note, when it went past anything the stock game uses.
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setStyleSheet(f"color: {self.palette_['pending']};")
        self.warning_label.setVisible(False)

        self.changes_label = QLabel("No changes yet")
        self.changes_label.setWordWrap(True)
        self.save_button = QPushButton("Save as mod...")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        self.discard_button = QPushButton("Discard changes")
        self.discard_button.clicked.connect(self._discard)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        header = QHBoxLayout()
        header.addWidget(self.search, 1)
        header.addWidget(self.mode)
        header.addWidget(self.copy_button)
        header.addWidget(self.stock_button)
        header.addWidget(self.rotate_button)

        right = QVBoxLayout()
        right.addWidget(self.title_label)
        right.addWidget(self.about_label)
        right.addWidget(self.tips_label)
        right.addLayout(header)
        right.addWidget(self.stack, 1)

        self.paging = QWidget()
        paging = QHBoxLayout(self.paging)
        paging.setContentsMargins(0, 0, 0, 0)
        paging.addWidget(self.count_label, 1)
        paging.addWidget(self.prev_button)
        paging.addWidget(self.next_button)
        right.addWidget(self.paging)

        self.bulk = QWidget()
        bulk = QHBoxLayout(self.bulk)
        bulk.setContentsMargins(0, 0, 0, 0)
        bulk.addWidget(QLabel("Change all at once:"))
        bulk.addWidget(self.bulk_column)
        bulk.addWidget(self.set_all)
        bulk.addWidget(self.multiply)
        bulk.addStretch(1)
        right.addWidget(self.bulk)
        right.addWidget(self.warning_label)

        panel = QWidget()
        panel.setLayout(right)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(panel)
        split.setStretchFactor(1, 1)

        footer = QHBoxLayout()
        footer.addWidget(self.changes_label, 1)
        footer.addWidget(self.discard_button)
        footer.addWidget(close_button)
        footer.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(footer)

        self._start()

    # -- setting up --------------------------------------------------------

    def _start(self) -> None:
        try:
            self.builder = ModBuilder(self.manager.vanilla_path or "")
        except BuildError as exc:
            message = str(exc)
            # Tied to this window, so it cannot fire after the window is gone.
            QTimer.singleShot(0, self, lambda: self._fail(message))
            return
        for name, note, tables in self.builder.groups():
            parent = QTreeWidgetItem([name])
            parent.setToolTip(0, note)
            parent.setData(0, PAGE_ROLE, ("group", name, note, tables))
            for table in tables:
                child = QTreeWidgetItem([self.builder.title_of(table)])
                child.setData(0, TABLE_ROLE, table)
                child.setToolTip(0, table)
                parent.addChild(child)
                self._tree_items[table] = child
            self.tree.addTopLevelItem(parent)
            if name != builder_data.EVERYTHING_ELSE:
                parent.setExpanded(True)
        # "Find a price" goes second to last, just above everything else, so the
        # headings people come for are what they see first.
        prices = QTreeWidgetItem([builder_data.FIND_BY_CURRENCY])
        prices.setToolTip(0, builder_data.FIND_BY_CURRENCY_NOTE)
        prices.setData(0, PAGE_ROLE, ("group", builder_data.FIND_BY_CURRENCY,
                                      builder_data.FIND_BY_CURRENCY_NOTE, []))
        for name, unit, note in builder_data.CURRENCIES:
            child = QTreeWidgetItem([name])
            child.setToolTip(0, note)
            child.setData(0, PAGE_ROLE, ("currency", name, note, unit))
            prices.addChild(child)
        last = self.tree.topLevelItemCount()
        rest = self.tree.topLevelItem(last - 1)
        at = last - 1 if rest and rest.text(0) == builder_data.EVERYTHING_ELSE else last
        self.tree.insertTopLevelItem(at, prices)
        first = self.tree.topLevelItem(0)
        if first is not None and first.childCount():
            self.tree.setCurrentItem(first.child(0))
        self._refresh_changes()

    def _fail(self, message: str) -> None:
        QMessageBox.warning(self, "Cannot build a mod yet", message)
        self.reject()

    # -- moving around -----------------------------------------------------

    def _on_pick(self, current, _previous) -> None:
        if not current or not self.builder:
            return
        table = current.data(0, TABLE_ROLE)
        if not table:
            self._show_links(current.data(0, PAGE_ROLE))
            return
        self.table = table
        self.offset = 0
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        # A table of things reads better as a list; a table of numbers in a
        # series reads better as a grid, where you can see the curve.
        wide = len([c for c in self.builder.columns(table) if not c.is_key]) > WIDE
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(0 if wide else 1)
        self.mode.blockSignals(False)
        self.warning_label.setVisible(False)
        self._reload()

    def _set_table_controls(self, visible: bool) -> None:
        for widget in (self.search, self.mode, self.copy_button, self.paging, self.bulk):
            widget.setVisible(visible)
        table = self.table if visible else None
        self.stock_button.setVisible(table == LINEUP_TABLE)
        self.rotate_button.setVisible(table in (LINEUP_TABLE, SCHEDULE_TABLE))

    def _show_links(self, page) -> None:
        """A heading, or a currency: a page of links into the tables."""
        if not page:
            return
        self.table = None
        self._set_table_controls(False)
        self.warning_label.setVisible(False)
        self.stack.setCurrentIndex(LINKS_PAGE)
        self.links.clear()
        kind, name, note, what = page
        self.title_label.setText(name)
        self.about_label.setText(note)
        self.about_label.setVisible(bool(note))
        if kind == "currency":
            found = self.builder.priced_in(what)
            self.tips_label.setText(
                f"{len(found)} columns across "
                f"{len({t for t, _, _ in found})} tables. Click one to open it."
            )
            for table, column, meaning in found:
                entry = QListWidgetItem(f"{self.builder.title_of(table)}  -  {meaning}")
                entry.setData(TABLE_ROLE, table)
                entry.setData(COLUMN_ROLE, column)
                entry.setToolTip(f"{table}.{column}")
                self.links.addItem(entry)
        else:
            self.tips_label.setText("Click one to open it.")
            for table in what:
                entry = QListWidgetItem(self.builder.title_of(table))
                entry.setData(TABLE_ROLE, table)
                entry.setToolTip(table)
                about = self.builder.about_of(table)
                if about:
                    entry.setText(f"{self.builder.title_of(table)}  -  {about}")
                self.links.addItem(entry)
            if not what:
                self.tips_label.setText("Pick a currency below this heading.")
        self.tips_label.setVisible(True)

    def _follow_link(self, entry: QListWidgetItem) -> None:
        table = entry.data(TABLE_ROLE)
        target = self._tree_items.get(table)
        if target is None:
            return
        self._wanted_column = entry.data(COLUMN_ROLE) or ""
        self.tree.setCurrentItem(target)

    def _on_mode(self) -> None:
        self._reload()

    def _on_search(self) -> None:
        self.offset = 0
        self._search_timer.start()

    def _page(self, direction: int) -> None:
        self.offset = max(0, self.offset + direction * PAGE)
        self._reload()

    def _reload(self) -> None:
        if not self.builder or not self.table:
            return
        view = self.builder.view(
            self.table, search=self.search.text().strip(), offset=self.offset
        )
        self._set_table_controls(True)
        self.title_label.setText(view.title)
        self.about_label.setText(view.about)
        self.about_label.setVisible(bool(view.about))
        self.tips_label.setText("\n".join(f"• {tip}" for tip in view.tips))
        self.tips_label.setVisible(bool(view.tips))
        self._editable = view.editable
        self._rows = view.rows

        as_form = self.mode.currentData() == "form"
        self.stack.setCurrentIndex(FORM_PAGE if as_form else GRID_PAGE)
        if as_form:
            self._fill_list()
        else:
            self._fill_grid()

        wanted = self._wanted_column or self.bulk_column.currentData()
        self._wanted_column = ""
        self.bulk_column.clear()
        for column in self._editable:
            self.bulk_column.addItem(column.heading, column.name)
        index = self.bulk_column.findData(wanted)
        if index >= 0:
            self.bulk_column.setCurrentIndex(index)

        shown = len(view.rows)
        first = self.offset + 1 if shown else 0
        self.count_label.setText(
            f"Showing {first}-{self.offset + shown} of {view.total:,}"
        )
        self.prev_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + shown < view.total)
        self.copy_button.setEnabled(bool(view.rows))

    def _range_hint(self, column) -> str:
        """"The stock game uses 0 to 200,000 here", for a tooltip."""
        if not self.builder or not self.table or not column.is_number:
            return ""
        found = self.builder.stock_range(self.table, column.name)
        if not found:
            return ""
        low, high = found
        if low == high:
            return f"The stock game has {low:,} on every row."
        return f"The stock game uses {low:,} to {high:,} here."

    def _tooltip(self, column) -> str:
        lines = []
        if column.is_list:
            lines.append("A list, not a single number - keep the commas.")
        elif column.date == "epoch":
            lines.append("A date and time in UTC. Type it as 2027-01-31 or "
                         "2027-01-31 10:00. 0 or -1 means none.")
        elif column.date == "text":
            lines.append("A date. Type it as 2027-01-31.")
        if not column.described:
            lines.append(f"{column.name} - nobody has described this one yet")
        hint = self._range_hint(column)
        if hint:
            lines.append(hint)
        return "\n".join(lines)

    # -- as a list ---------------------------------------------------------

    def _fill_list(self) -> None:
        self._loading = True
        self.row_list.clear()
        show_art = self.icons.available
        for row in self._rows:
            entry = QListWidgetItem(row.label)
            entry.setToolTip(" / ".join(str(k) for k in row.key))
            if row.skipped:
                entry.setForeground(Qt.GlobalColor.gray)
            if show_art and row.key:
                picture = self.thumbnails.icon(
                    self.icons.for_id(str(row.key[0]), row.label)
                )
                if picture is not None:
                    entry.setIcon(picture)
            self.row_list.addItem(entry)
        self._loading = False
        if self._rows:
            self.row_list.setCurrentRow(0)
        else:
            self._clear_form()

    def _clear_form(self) -> None:
        while self.form.count():
            item = self.form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._fields = {}

    def _show_one(self, index: int) -> None:
        if self._loading or not (0 <= index < len(self._rows)):
            return
        self._clear_form()
        row = self._rows[index]
        if row.skipped:
            note = QLabel(f"{row.skipped}: the Steam game never loads this row, "
                          "so changing it does nothing.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {self.palette_['pending']};")
            self.form.addRow(note)
        for column in self._editable:
            value = row.values.get(column.name)
            field = QLineEdit(column.show(value))
            field.setProperty("column", column.name)
            field.setProperty("key", row.key)
            tip = self._tooltip(column)
            if tip:
                field.setToolTip(tip)
            field.editingFinished.connect(
                lambda f=field: self._on_field_changed(f)
            )
            label = QLabel(column.heading)
            label.setWordWrap(True)
            if not column.described:
                label.setObjectName("dim")
            self.form.addRow(label, field)
            self._fields[column.name] = field

    def _current_key(self) -> tuple | None:
        """The row picked in whichever view is showing."""
        if self.stack.currentIndex() == FORM_PAGE:
            index = self.row_list.currentRow()
            if 0 <= index < len(self._rows):
                return self._rows[index].key
            return None
        row = self.grid.currentRow()
        head = self.grid.item(row, 0) if row >= 0 else None
        return tuple(head.data(KEY_ROLE)) if head is not None else None

    def _on_field_changed(self, field: QLineEdit) -> None:
        if self._loading or not self.builder or not self.table:
            return
        column = field.property("column")
        key = tuple(field.property("key"))
        match = next((c for c in self._editable if c.name == column), None)
        # editingFinished also fires on leaving an untouched box. Writing the
        # same value back is harmless but a date would round-trip through text.
        if match is not None:
            try:
                if match.show(self.builder.value(self.table, key, column)) == field.text():
                    return
            except BuildError:
                pass
        try:
            self.builder.set_value(self.table, key, column, field.text())
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            self._reload()
            return
        if match is not None and match.date:
            # Show the date as it was understood, e.g. 2027-01-31 00:00.
            field.setText(match.show(self.builder.value(self.table, key, column)))
        self._note_range(key, column)
        self._refresh_changes()

    def _note_range(self, key: tuple, column: str) -> None:
        """Say so, without stopping anyone, when a value leaves the stock range."""
        if not self.builder or not self.table:
            return
        try:
            note = self.builder.range_note(
                self.table, column, self.builder.value(self.table, key, column)
            )
        except BuildError:
            note = ""
        self.warning_label.setText(
            f"Check this: {note}. Values far past the stock game's have crashed "
            "it before (a 1,000,000 decal draw). It is saved as you typed it."
            if note else ""
        )
        self.warning_label.setVisible(bool(note))

    # -- as a table --------------------------------------------------------

    def _fill_grid(self) -> None:
        self._loading = True
        self.grid.clear()
        self.grid.setColumnCount(len(self._editable) + 1)
        self.grid.setHorizontalHeaderLabels(
            ["Row"] + [c.heading for c in self._editable]
        )
        self.grid.setRowCount(len(self._rows))
        tips = [self._tooltip(c) for c in self._editable]
        for r, row in enumerate(self._rows):
            head = QTableWidgetItem(row.label)
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsEditable)
            head.setData(KEY_ROLE, row.key)
            head.setToolTip(" / ".join(str(k) for k in row.key))
            if row.skipped:
                head.setForeground(Qt.GlobalColor.gray)
            self.grid.setItem(r, 0, head)
            for i, column in enumerate(self._editable, start=1):
                cell = QTableWidgetItem(column.show(row.values.get(column.name)))
                if tips[i - 1]:
                    cell.setToolTip(tips[i - 1])
                self.grid.setItem(r, i, cell)
        self.grid.resizeColumnsToContents()
        self.grid.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self._loading = False

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or not self.builder or not self.table or item.column() == 0:
            return
        head = self.grid.item(item.row(), 0)
        if head is None:
            return
        column = self._editable[item.column() - 1]
        key = tuple(head.data(KEY_ROLE))
        try:
            self.builder.set_value(self.table, key, column.name, item.text())
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            self._reload()
            return
        if column.date:
            self._loading = True
            item.setText(column.show(self.builder.value(self.table, key, column.name)))
            self._loading = False
        self._note_range(key, column.name)
        self._refresh_changes()

    # -- bulk edits --------------------------------------------------------

    def _shown_keys(self) -> list[tuple]:
        return [row.key for row in self._rows]

    def _after_bulk(self, column: str, keys: list[tuple]) -> None:
        """One message for a bulk edit, not one per row."""
        self._reload()
        self._refresh_changes()
        if not self.builder or not self.table:
            return
        notes = []
        for key in keys:
            try:
                note = self.builder.range_note(
                    self.table, column, self.builder.value(self.table, key, column)
                )
            except BuildError:
                continue
            if note:
                notes.append(note)
        if notes:
            QMessageBox.warning(
                self, "Past the stock game's range",
                f"{len(notes)} of the {len(keys)} values are outside anything the "
                f"stock game uses in this column - for example:\n\n{notes[0]}\n\n"
                "They are kept. Going past the stock numbers is sometimes the point, "
                "but a 1,000,000 decal draw where nothing passed 200,000 crashed "
                "the game, so test it before sharing the mod."
            )

    def _set_all(self) -> None:
        if not self.builder or not self.table or not self.bulk_column.count():
            return
        column = self.bulk_column.currentData()
        keys = self._shown_keys()
        value, ok = QInputDialog.getText(
            self, "Set every one shown",
            f"Set {self.bulk_column.currentText()} to this, "
            f"for all {len(keys)} shown on this page:",
        )
        if not ok:
            return
        try:
            self.builder.set_many(self.table, keys, column, value)
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            self._reload()
            self._refresh_changes()
            return
        self._after_bulk(column, keys)

    def _multiply(self) -> None:
        if not self.builder or not self.table or not self.bulk_column.count():
            return
        column = self.bulk_column.currentData()
        keys = self._shown_keys()
        box = QInputDialog(self)
        box.setWindowTitle("Multiply")
        box.setInputMode(QInputDialog.InputMode.DoubleInput)
        box.setLabelText(
            f"Multiply {self.bulk_column.currentText()} by this, "
            f"for all {len(keys)} shown on this page:"
        )
        box.setDoubleDecimals(3)
        box.setDoubleRange(0.001, 100000.0)
        box.setDoubleValue(2.0)
        if not box.exec():
            return
        try:
            changed = self.builder.scale(self.table, keys, column, box.doubleValue())
        except BuildError as exc:
            QMessageBox.warning(self, "That cannot be multiplied", str(exc))
            return
        self._after_bulk(column, keys)
        if not changed:
            QMessageBox.information(
                self, "Nothing to multiply",
                "None of the rows shown hold a number in that column."
            )

    # -- adding rows -------------------------------------------------------

    def _copy_row(self) -> None:
        if not self.builder or not self.table:
            return
        key = self._current_key()
        if key is None:
            QMessageBox.information(self, "Copy a row", "Pick a row to copy first.")
            return
        keys = self.builder.keys_of(self.table)
        label = self.builder.label(self.table, key)
        new_key = None
        if keys != ["rowid"]:
            new_key = self._ask_for_key(keys, key, label)
            if new_key is None:
                return
        try:
            made = self.builder.copy_row(self.table, key, new_key)
        except BuildError as exc:
            QMessageBox.warning(self, "Could not copy it", str(exc))
            return
        if keys != ["rowid"]:
            # Show the copy, so the next thing to do - change it - is right there.
            self.search.setText(str(made[0]))
        else:
            self._reload()
        self._refresh_changes()

    def _ask_for_key(self, keys: list[str], key: tuple, label: str) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Copy this row")
        form = QFormLayout(dialog)
        intro = QLabel(
            f"A new row that starts as a copy of \"{label}\". Give it what "
            "identifies it - at least one of these has to be different."
        )
        intro.setWordWrap(True)
        form.addRow(intro)
        boxes = {}
        by_name = {c.name: c for c in self.builder.columns(self.table)}
        for name, value in zip(keys, key):
            box = QLineEdit("" if value is None else str(value))
            column = by_name.get(name)
            words = column.heading if column is not None and column.described else name
            form.addRow(QLabel(words), box)
            boxes[name] = box
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return None
        return {name: box.text() for name, box in boxes.items()}

    # -- the vending machine ----------------------------------------------

    def _stock_machine(self) -> None:
        if not self.builder:
            return
        from .vending_picker import VendingPicker

        current = ""
        key = self._current_key()
        if key is not None and self.table == LINEUP_TABLE:
            row = next((r for r in self._rows if r.key == key), None)
            if row is not None:
                current = str(row.values.get("lineup_id") or "")
        picker = VendingPicker(self.builder, tab=current, parent=self,
                               icons=self.icons, thumbnails=self.thumbnails)
        if picker.exec() and picker.added:
            self._reload()
            self._refresh_changes()
            room = (f"\n\nThe schedule now offers every item on that list "
                    f"({picker.room} value(s) raised)." if picker.room else "")
            QMessageBox.information(
                self, "Added",
                f"Put {picker.added} thing(s) on the machine. They show up in "
                "the list, and in the mod when you save. The game stores what is "
                "on offer in your save, so they appear at the machine's next "
                f"restock.{room}"
            )

    def _extend_schedule(self) -> None:
        if not self.builder:
            return
        end = self.builder.schedule_end() or "no date"
        this_year = datetime.now().year
        year, ok = QInputDialog.getInt(
            self, "Keep the machine rotating",
            f"The machine's schedule currently ends on {end}.\n\n"
            "Add a month for every month up to the end of this year, carrying on "
            "the Bloodnium list rotation (MON, TUE ... SUN, MON):",
            this_year + 5, this_year, this_year + 40,
        )
        if not ok:
            return
        try:
            added = self.builder.extend_schedule(year)
        except BuildError as exc:
            QMessageBox.warning(self, "Could not extend it", str(exc))
            return
        self._reload()
        self._refresh_changes()
        if not added:
            QMessageBox.information(
                self, "Nothing to add",
                f"The schedule already runs past the end of {year}."
            )
            return
        QMessageBox.information(
            self, "Schedule extended",
            f"Added {added} month(s), up to the end of {year}.\n\n"
            "Not yet tested in game: whether a save that is already past the old "
            "last month picks these up by itself. Try it before sharing the mod."
        )

    # -- saving ------------------------------------------------------------

    def _refresh_changes(self) -> None:
        if not self.builder:
            return
        count = self.builder.change_count()
        if not count:
            self.changes_label.setText("No changes yet")
        else:
            lines = self.builder.summary()
            where = "; ".join(lines[:3])
            more = "" if len(lines) <= 3 else "; and more"
            risky = len(self.builder.risks(labelled=False))
            flag = (f"  -  {risky} past the stock game's range" if risky else "")
            self.changes_label.setText(
                f"{count} change{'s' if count != 1 else ''} - {where}{more}{flag}"
            )
        self.save_button.setEnabled(bool(count))
        self.discard_button.setEnabled(bool(count))

    def _discard(self) -> None:
        if not self.builder or not self.builder.dirty:
            return
        confirmed = QMessageBox.question(
            self, "Discard changes",
            f"Throw away all {self.builder.change_count()} changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.builder.revert_all()
        self.warning_label.setVisible(False)
        self._reload()
        self._refresh_changes()

    def _save(self) -> None:
        if not self.builder or not self.builder.dirty:
            return
        dialog = SaveDialog(self.builder, self)
        if not dialog.exec():
            return
        count = self.builder.change_count()
        name = dialog.name.text().strip()
        try:
            mods = self.builder.save_as_mod(
                self.manager, name,
                description=dialog.description.toPlainText(),
                author=dialog.author.text(),
            )
        except Exception as exc:  # install errors are worth showing verbatim
            QMessageBox.warning(self, "Could not save the mod", str(exc))
            return
        QMessageBox.information(
            self, "Saved",
            f"Made \"{name}\" from {count} change(s), as "
            f"{len(mods)} switchable part(s).\n\n"
            "It is in your mod list, switched off. Look at its diff and its "
            "plain-English tab before you tick it."
        )
        self.accept()

    # -- tidying up --------------------------------------------------------

    def _shut_builder(self) -> None:
        if self.builder:
            self.builder.close()
            self.builder = None

    def closeEvent(self, event) -> None:
        self._shut_builder()
        super().closeEvent(event)

    def reject(self) -> None:
        if self.builder and self.builder.dirty:
            confirmed = QMessageBox.question(
                self, "Close without saving",
                f"{self.builder.change_count()} change(s) have not been saved "
                "as a mod. Close anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return
        self._shut_builder()
        super().reject()


class SaveDialog(QDialog):
    """Name, description and author, with what is about to be saved.

    Values past the stock game's range are listed here as well, because this is
    the last point at which they are cheap to fix.
    """

    def __init__(self, builder: ModBuilder, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save as mod")
        self.resize(560, 480)
        self.name = QLineEdit()
        self.name.setPlaceholderText("What the mod is called in the list")
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(
            "Optional - what it does. Left empty, it says how many values it changes."
        )
        self.description.setMaximumHeight(90)
        self.author = QLineEdit()
        self.author.setPlaceholderText("Optional")

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Author", self.author)

        changes = QLabel("It changes:\n" + "\n".join(
            f"• {line}" for line in builder.summary()[:12]
        ) + ("\n• ..." if len(builder.summary()) > 12 else ""))
        changes.setWordWrap(True)
        changes.setTextFormat(Qt.TextFormat.PlainText)

        risks = builder.risks()
        self.risk_label = QLabel()
        self.risk_label.setWordWrap(True)
        self.risk_label.setTextFormat(Qt.TextFormat.PlainText)
        if risks:
            shown = "\n".join(f"• {r}" for r in risks[:8])
            more = f"\n• and {len(risks) - 8} more" if len(risks) > 8 else ""
            self.risk_label.setText(
                f"{len(risks)} value(s) are outside anything the stock game uses:\n"
                f"{shown}{more}\n\nThat is allowed, but test the mod in game before "
                "sharing it - a price far past the stock range has crashed it before."
            )
            palette_ = colors(getattr(parent, "dark", True))
            self.risk_label.setStyleSheet(f"color: {palette_['pending']};")
        self.risk_label.setVisible(bool(risks))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.ok_button.setEnabled(False)
        self.name.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(changes)
        layout.addWidget(self.risk_label)
        layout.addStretch(1)
        layout.addWidget(buttons)
