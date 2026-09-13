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

The window is deliberately thin: every edit goes through ``builder.ModBuilder``,
which owns the scratch database and the rules about what may be changed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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

from ..builder import LINEUP_TABLE, PAGE, BuildError, ModBuilder
from ..icons import IconSource
from .icon_cache import ThumbnailCache
from .theme import colors

TABLE_ROLE = Qt.ItemDataRole.UserRole
KEY_ROLE = Qt.ItemDataRole.UserRole + 1

# Past this many editable columns a grid stops being readable, so such a table
# opens as a form unless you ask for the grid.
WIDE = 12


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
        # Artwork, if the player has pointed us at some. None ships with the
        # program - see lid_db_manager/icons.py.
        self.icons = IconSource(manager.state.settings.icon_folder or None)
        self.thumbnails = ThumbnailCache(manager.paths.root)

        self.setWindowTitle("Build a mod")
        self.resize(1200, 760)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(250)
        self.tree.currentItemChanged.connect(self._on_pick)

        self.title_label = QLabel()
        self.title_label.setObjectName("heading")
        self.about_label = QLabel()
        self.about_label.setWordWrap(True)
        self.about_label.setObjectName("dim")

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

        self.stock_button = QPushButton("Add things to the machine...")
        self.stock_button.clicked.connect(self._stock_machine)
        self.stock_button.setVisible(False)

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

        self.stack = QStackedWidget()
        self.stack.addWidget(detail)
        self.stack.addWidget(self.grid)

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

        self.changes_label = QLabel("No changes yet")
        self.save_button = QPushButton("Save as mod")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        self.discard_button = QPushButton("Discard changes")
        self.discard_button.clicked.connect(self._discard)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        header = QHBoxLayout()
        header.addWidget(self.search, 1)
        header.addWidget(self.mode)
        header.addWidget(self.stock_button)

        right = QVBoxLayout()
        right.addWidget(self.title_label)
        right.addWidget(self.about_label)
        right.addLayout(header)
        right.addWidget(self.stack, 1)

        paging = QHBoxLayout()
        paging.addWidget(self.count_label, 1)
        paging.addWidget(self.prev_button)
        paging.addWidget(self.next_button)
        right.addLayout(paging)

        bulk = QHBoxLayout()
        bulk.addWidget(QLabel("Change all at once:"))
        bulk.addWidget(self.bulk_column)
        bulk.addWidget(self.set_all)
        bulk.addWidget(self.multiply)
        bulk.addStretch(1)
        right.addLayout(bulk)

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
            QTimer.singleShot(0, lambda: self._fail(str(exc)))
            return
        for name, note, tables in self.builder.groups():
            parent = QTreeWidgetItem([name])
            parent.setToolTip(0, note)
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for table in tables:
                child = QTreeWidgetItem([self.builder.title_of(table)])
                child.setData(0, TABLE_ROLE, table)
                child.setToolTip(0, table)
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)
            if name != "Everything else":
                parent.setExpanded(True)
        first = self.tree.topLevelItem(0)
        if first is not None and first.childCount():
            self.tree.setCurrentItem(first.child(0))
        self._refresh_changes()

    def _fail(self, message: str) -> None:
        QMessageBox.warning(self, "Cannot build a mod yet", message)
        self.reject()

    # -- moving around -----------------------------------------------------

    def _on_pick(self, current, _previous) -> None:
        table = current.data(0, TABLE_ROLE) if current else None
        if not table or not self.builder:
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
        self.stock_button.setVisible(table == LINEUP_TABLE)
        self._reload()

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
        self.title_label.setText(view.title)
        self.about_label.setText(view.about)
        self.about_label.setVisible(bool(view.about))
        self._editable = view.editable
        self._rows = view.rows

        as_form = self.mode.currentData() == "form"
        self.stack.setCurrentIndex(0 if as_form else 1)
        if as_form:
            self._fill_list()
        else:
            self._fill_grid()

        self.bulk_column.clear()
        for column in self._editable:
            self.bulk_column.addItem(column.heading, column.name)

        shown = len(view.rows)
        first = self.offset + 1 if shown else 0
        self.count_label.setText(
            f"Showing {first}-{self.offset + shown} of {view.total:,}"
        )
        self.prev_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + shown < view.total)

    # -- as a list ---------------------------------------------------------

    def _fill_list(self) -> None:
        self._loading = True
        self.row_list.clear()
        show_art = self.icons.available
        for row in self._rows:
            entry = QListWidgetItem(row.label)
            entry.setToolTip(" / ".join(str(k) for k in row.key))
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
        for column in self._editable:
            value = row.values.get(column.name)
            field = QLineEdit("" if value is None else str(value))
            field.setProperty("column", column.name)
            field.setProperty("key", row.key)
            if column.is_list:
                field.setToolTip("A list, not a single number - keep the commas.")
            elif not column.described:
                field.setToolTip(f"{column.name} - nobody has described this one yet")
            field.editingFinished.connect(
                lambda f=field: self._on_field_changed(f)
            )
            label = QLabel(column.heading)
            label.setWordWrap(True)
            if not column.described:
                label.setObjectName("dim")
            self.form.addRow(label, field)
            self._fields[column.name] = field

    def _on_field_changed(self, field: QLineEdit) -> None:
        if self._loading or not self.builder or not self.table:
            return
        column = field.property("column")
        key = tuple(field.property("key"))
        try:
            self.builder.set_value(self.table, key, column, field.text())
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            self._reload()
            return
        self._refresh_changes()

    # -- as a table --------------------------------------------------------

    def _fill_grid(self) -> None:
        self._loading = True
        self.grid.clear()
        self.grid.setColumnCount(len(self._editable) + 1)
        self.grid.setHorizontalHeaderLabels(
            ["Row"] + [c.heading for c in self._editable]
        )
        self.grid.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            head = QTableWidgetItem(row.label)
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsEditable)
            head.setData(KEY_ROLE, row.key)
            head.setToolTip(" / ".join(str(k) for k in row.key))
            self.grid.setItem(r, 0, head)
            for i, column in enumerate(self._editable, start=1):
                value = row.values.get(column.name)
                cell = QTableWidgetItem("" if value is None else str(value))
                if column.is_list:
                    cell.setToolTip("A list, not a single number - keep the commas.")
                elif not column.described:
                    cell.setToolTip(f"{column.name} - nobody has described this one yet")
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
        try:
            self.builder.set_value(
                self.table, tuple(head.data(KEY_ROLE)), column.name, item.text()
            )
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            self._reload()
            return
        self._refresh_changes()

    # -- bulk edits --------------------------------------------------------

    def _shown_keys(self) -> list[tuple]:
        return [row.key for row in self._rows]

    def _set_all(self) -> None:
        if not self.builder or not self.table or not self.bulk_column.count():
            return
        column = self.bulk_column.currentData()
        keys = self._shown_keys()
        value, ok = QInputDialog.getText(
            self, "Set every one shown",
            f"Set {self.bulk_column.currentText()} to this, "
            f"for all {len(keys)} shown:",
        )
        if not ok:
            return
        try:
            self.builder.set_many(self.table, keys, column, value)
        except BuildError as exc:
            QMessageBox.warning(self, "That will not go in there", str(exc))
            return
        self._reload()
        self._refresh_changes()

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
            f"for all {len(keys)} shown:"
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
        self._reload()
        self._refresh_changes()
        if not changed:
            QMessageBox.information(
                self, "Nothing to multiply",
                "None of the rows shown hold a number in that column."
            )

    # -- the vending machine ----------------------------------------------

    def _stock_machine(self) -> None:
        if not self.builder:
            return
        from .vending_picker import VendingPicker

        current = ""
        if self._rows:
            index = max(0, self.row_list.currentRow())
            if index < len(self._rows):
                values = self._rows[index].values
                current = str(values.get("lineup_id") or "")
        picker = VendingPicker(self.builder, tab=current, parent=self,
                               icons=self.icons, thumbnails=self.thumbnails)
        if picker.exec() and picker.added:
            self._reload()
            self._refresh_changes()
            QMessageBox.information(
                self, "Added",
                f"Put {picker.added} thing(s) on the machine. They show up in "
                "the list below, and in the mod when you save."
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
            self.changes_label.setText(
                f"{count} change{'s' if count != 1 else ''} - {where}{more}"
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
        self._reload()
        self._refresh_changes()

    def _save(self) -> None:
        if not self.builder or not self.builder.dirty:
            return
        name, ok = QInputDialog.getText(self, "Save as mod", "Call the mod:")
        if not ok or not name.strip():
            return
        count = self.builder.change_count()
        try:
            mods = self.builder.save_as_mod(self.manager, name)
        except Exception as exc:  # install errors are worth showing verbatim
            QMessageBox.warning(self, "Could not save the mod", str(exc))
            return
        QMessageBox.information(
            self, "Saved",
            f"Made \"{name.strip()}\" from {count} change(s), as "
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
