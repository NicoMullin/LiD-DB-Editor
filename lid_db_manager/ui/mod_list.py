"""The mod list: a checkbox, a status light, and what each mod touches."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QSpinBox,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..browse import SORT_ORDER, Filter, arrange, select
from ..conflict import SERIOUS
from ..manager import Manager
from ..vetted import GAME_EXE
from .theme import STATUS_GLYPH, STATUS_KEY, STATUS_TEXT, status_color

MOD_ID_ROLE = Qt.ItemDataRole.UserRole
PATCH_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
# The load-order number as the list drew it. Column 0 shows the number and
# carries the checkbox, so a change there could be either - this is what tells
# a typed number apart from a tick.
ORDER_ROLE = Qt.ItemDataRole.UserRole + 2

SORTED_TOOLTIP = (
    "Load order. This is still where the mod applies, but the list is not in "
    "that order right now, so the number cannot be edited here.\n\n"
    "Set Sort back to “Load order” to move mods again."
)

ORDER_TOOLTIP = (
    "Load order. Type a number here to move this mod, or use the Move buttons.\n\n"
    "1 applies first; the last one applies last, so where two mods change the "
    "same value the higher number wins."
)

EXE_TOOLTIP = (
    "This mod changes the game executable.\n\n"
    "It replaces artwork the game keeps a checksum for, so that one checksum has "
    "to be updated or the game refuses the replacement. What changes is twenty "
    "bytes in a table of file checksums - no program code, checked before and "
    "after.\n\n"
    "The executable is backed up, and unticking this mod puts it back byte for "
    "byte. Only changes recorded in the manager's own recipes folder can do this."
)


def _typed_order(item: QTreeWidgetItem) -> int | None:
    """The number somebody typed into column 0, or None if nothing was typed.

    A change to column 0 is either a tick or a new load-order number, and the
    signal does not say which. The number the list drew is kept on the item, so
    a difference from it is the one thing a tick cannot cause.
    """
    drawn = item.data(0, ORDER_ROLE)
    if not drawn:
        return None  # a disabled mod has no place to change
    try:
        typed = int((item.text(0) or "").strip())
    except ValueError:
        return None
    return typed if typed != drawn else None


class OrderDelegate(QStyledItemDelegate):
    """A spin box for the load-order number.

    Typing straight into the cell would let any text through, and "" or "abc"
    is not a place in the list. A spin box can only offer a real one, and its
    arrows make the column usable without the keyboard at all.
    """

    def __init__(self, how_many, parent=None):
        super().__init__(parent)
        self._how_many = how_many  # called at edit time - the list changes size

    def createEditor(self, parent, option, index):
        editor = QSpinBox(parent)
        editor.setFrame(False)
        editor.setRange(1, max(1, self._how_many()))
        editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return editor

    def setEditorData(self, editor, index) -> None:
        try:
            editor.setValue(int(index.data() or 1))
        except (TypeError, ValueError):
            editor.setValue(1)

    def setModelData(self, editor, model, index) -> None:
        editor.interpretText()
        model.setData(index, str(editor.value()), Qt.ItemDataRole.EditRole)


def _affects(tables: list[str], asset_targets: list[str]) -> str:
    """The 'Affects' column: table names, then any game files as 'file: X.upk'."""
    bits = list(tables)
    bits += [f"file: {target.rsplit('/', 1)[-1]}" for target in asset_targets]
    return ", ".join(bits) or "-"


DOT_SIZE = 12

# What each dot means, for the tooltip - colour alone does not say it.
DOT_TOOLTIP = {
    "failed": (
        "Red dot: this mod failed, or another mod overwrites the very same values "
        "it changes, so some of its changes are lost."
    ),
    "pending": (
        "Yellow dot: this mod shares a table with another mod, or has a warning. "
        "Nothing is proven to be overwritten."
    ),
}


def _dot(color: QColor | None) -> QIcon:
    """A round dot for the mod's name, or an empty space the same size so the
    names without one still line up."""
    pixmap = QPixmap(DOT_SIZE, DOT_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    if color is not None:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(1, 1, DOT_SIZE - 2, DOT_SIZE - 2)
        painter.end()
    return QIcon(pixmap)


def _touches_executable(targets) -> bool:
    return GAME_EXE in {str(t).replace("\\", "/") for t in targets}


def _label(mod_id: str, name: str) -> str:
    """"id - name", unless they are the same thing said twice.

    A folder named after the mod gives both columns the same text, which reads
    as a stutter rather than as information.
    """
    tidy = lambda text: "".join(c for c in text.lower() if c.isalnum())  # noqa: E731
    return name if tidy(mod_id) == tidy(name) else f"{mod_id}  -  {name}"


class ModListWidget(QTreeWidget):
    """Two rows per mod: the mod itself, and a child line with the details."""

    enabledChanged = Signal(str, bool)  # one per mod, deferred - see _on_item_changed
    partToggled = Signal(str, str, bool)  # mod id, patch key, on/off
    togglesApplied = Signal()  # once after a batch of enabledChanged
    selectionChangedTo = Signal(str)
    orderTyped = Signal(str, int)  # mod id, the 1-based place typed into column 0

    def __init__(self, manager: Manager, dark: bool = True, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.dark = dark
        self._loading = False
        self._pending_toggles: dict[str, bool] = {}
        self._pending_parts: dict[tuple[str, str], bool] = {}
        self._pending_order: dict[str, int] = {}
        self._flush_scheduled = False
        # Which mods the user has opened. A mod's detail lines are worth having
        # but not worth showing twenty times over, so the list starts folded up
        # and remembers what was opened across the rebuilds a toggle triggers.
        self._expanded: set[str] = set()
        self._dots: dict[tuple[bool, str], QIcon] = {}  # (dark, level) -> dot
        # What is on screen, and in what order. Set from the filter bar above
        # the list; both are a way of looking at the list and neither changes
        # anything about the mods or the load order - see browse.py.
        self.filter: Filter = Filter()
        self.sort: str = SORT_ORDER
        self.shown_count = 0
        self.total_count = 0

        # Column 0 carries the checkbox and the load-order number together.
        self.setColumnCount(4)
        self.setHeaderLabels(["#", "Mod", "Status", "Affects"])
        # The arrow is the control for folding a mod open, so it has to be drawn.
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(False)
        self.setWordWrap(True)
        self.setExpandsOnDoubleClick(False)

        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        # Clicking the number of an already-selected mod starts editing it, which
        # is the shortest path from "this is in the wrong place" to fixing it.
        # Only column 0 of an enabled mod is ever editable (see refresh).
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.setItemDelegateForColumn(
            0, OrderDelegate(lambda: len(self.manager.state.enabled_mods), self)
        )

        self.itemChanged.connect(self._on_item_changed)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemExpanded.connect(self._on_expanded)
        self.itemCollapsed.connect(self._on_collapsed)

    # -- folding -----------------------------------------------------------

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if self._loading or item.parent() is not None:
            return
        mod_id = item.data(0, MOD_ID_ROLE)
        if mod_id:
            self._expanded.add(mod_id)

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        if self._loading or item.parent() is not None:
            return
        mod_id = item.data(0, MOD_ID_ROLE)
        if mod_id:
            self._expanded.discard(mod_id)

    def set_all_expanded(self, expanded: bool) -> None:
        """Open or fold every mod at once, and remember which it was."""
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item.data(0, MOD_ID_ROLE):
                item.setExpanded(expanded)

    # -- population --------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild from the manager, keeping selection, folding and scroll.

        Every toggle rebuilds this list, so anything not carried across here is
        lost the moment someone ticks a box. Losing the scroll position threw
        the user back to the top of the list on every single tick.
        """
        selected = self.selected_mod_ids()
        scroll = self.verticalScrollBar().value()
        self._loading = True
        self.clear()

        conflicts = self.manager.conflicts()
        validation = self.manager.last_validation

        # Enabled mods first, in load order; then everything else - narrowed to
        # what the filter bar asks for, and in the order it asks for.
        everything = self.manager.listed_mods()
        self.total_count = len(everything)
        showing = arrange(
            select(everything, self.filter),
            self.sort,
            status_of=lambda mod: self.manager.mod_status(mod.id),
        )
        self.shown_count = len(showing)
        for mod in showing:
            status = self.manager.mod_status(mod.id)
            order = self.manager.state.order_of(mod.id)
            item = QTreeWidgetItem(self)
            item.setData(0, MOD_ID_ROLE, mod.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if self.manager.state.is_enabled(mod.id)
                else Qt.CheckState.Unchecked,
            )
            item.setText(0, str(order) if order else "")
            item.setData(0, ORDER_ROLE, order)
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if order and self.sort == SORT_ORDER:
                # Only a mod that has a place can be moved to another one, and
                # only while the list is actually in load order - a number typed
                # into a list sorted by name would move a mod somewhere the rows
                # around it do not describe.
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(0, ORDER_TOOLTIP)
            elif order:
                item.setToolTip(0, SORTED_TOOLTIP)
            # A mod that changes the game executable says so wherever it is
            # seen, not only in the box that appeared once when it was added.
            # The check is on what the mod actually targets, so it cannot be
            # opted out of by a mod that would rather not mention it.
            marked = _touches_executable(mod.asset_targets())
            label = _label(mod.id, mod.name)
            if len(mod.settings) == 1:
                # A mod that comes down to one number shows it, so what it is
                # set to reads without opening anything. A mod with several
                # would only crowd its name; those live in the Configuration tab.
                setting = mod.settings[0]
                label += "   " + setting.display(mod.values.get(setting.id, setting.default))
            item.setText(1, label + ("   [changes the game .exe]" if marked else ""))
            item.setText(2, f"{STATUS_GLYPH[status]} {STATUS_TEXT[status]}")
            item.setText(3, _affects(sorted(mod.tables()), mod.asset_targets()))

            # A dot instead of opening the row: the warning is there to be
            # seen, and the arrow is there for whoever wants to read it.
            level = self._problem_level(mod.id, conflicts, validation)
            item.setIcon(1, self._dot_for(level))
            name_tips = [DOT_TOOLTIP[level] + " Open the row to read why."] if level else []

            if marked:
                item.setForeground(1, QBrush(status_color(self.dark, "pending")))
                name_tips.append(EXE_TOOLTIP)
                item.setToolTip(3, EXE_TOOLTIP)
            if name_tips:
                item.setToolTip(1, "\n\n".join(name_tips))

            color = status_color(self.dark, STATUS_KEY[status])
            item.setForeground(2, QBrush(color))
            # From the item's own font, not a fresh QFont(): a default-built
            # one carries the application's point size and would undo whatever
            # text size the person chose for this one column.
            font = item.font(2)
            font.setBold(status == "failed")
            item.setFont(2, font)

            applied = self.manager.state.applied.get(mod.id)
            tooltip = [mod.name, mod.description, f"Affects: {mod.affects_label()}"]
            if order:
                tooltip.insert(1, f"Load order {order} - later mods overwrite earlier ones")
            if mod.version:
                tooltip.append(f"Version {mod.version} by {mod.author}")
            if applied:
                tooltip.append(f"Last applied {applied.applied_at} ({applied.rows_changed} rows)")
            item.setToolTip(0, "\n".join(tooltip))

            detail = QTreeWidgetItem(item)
            detail.setFirstColumnSpanned(True)
            detail.setFlags(Qt.ItemFlag.ItemIsEnabled)
            detail.setData(0, MOD_ID_ROLE, mod.id)
            detail.setText(0, self._detail_text(mod, status, conflicts, validation))
            detail.setForeground(0, QBrush(status_color(self.dark, "dim")))
            if level:
                detail.setForeground(0, QBrush(status_color(self.dark, level)))

            self._add_parts(item, mod)
            # Folded unless the user opened it. A mod with a problem used to
            # open itself, which on a long list meant half of it was open; the
            # dot says there is something to read instead.
            item.setExpanded(mod.id in self._expanded)

        for failure in self.manager.scan.failures:
            item = QTreeWidgetItem(self)
            item.setText(1, f"{failure.folder}  (not loaded)")
            item.setText(2, "X Broken")
            item.setText(3, "-")
            item.setToolTip(1, failure.reason)
            item.setForeground(2, QBrush(status_color(self.dark, "failed")))
            child = QTreeWidgetItem(item)
            child.setFirstColumnSpanned(True)
            child.setFlags(Qt.ItemFlag.ItemIsEnabled)
            child.setText(0, failure.reason)
            child.setForeground(0, QBrush(status_color(self.dark, "failed")))
            item.setExpanded(True)

        if self.total_count and not self.shown_count:
            # An empty list looks exactly like "my mods are gone", so it has to
            # say that they are only hidden, and by what.
            hint = QTreeWidgetItem(self)
            hint.setFirstColumnSpanned(True)
            hint.setFlags(Qt.ItemFlag.ItemIsEnabled)
            hint.setText(
                0,
                f"None of your {self.total_count} mods match {self.filter.describe()}."
                "\n\nNothing has been removed - press Clear above the list to see "
                "them all again.",
            )
            hint.setForeground(0, QBrush(status_color(self.dark, "dim")))

        if not self.manager.mods and not self.manager.scan.failures:
            # The list itself answers "where do mod files go?".
            hint = QTreeWidgetItem(self)
            hint.setFirstColumnSpanned(True)
            hint.setFlags(Qt.ItemFlag.ItemIsEnabled)
            hint.setText(
                0,
                "No mods yet.\n\n"
                "Drag a .sql patch, a mod folder or a .zip onto this window to add one,\n"
                "or use Tools > Add a mod from a file.\n\n"
                f"They live in {self.manager.paths.mods_dir}",
            )
            hint.setForeground(0, QBrush(status_color(self.dark, "dim")))

        self._loading = False
        if selected:
            self.select_mods(selected)
        # After the rows exist, or the bar has nothing to scroll through yet.
        self.verticalScrollBar().setValue(scroll)

    def _add_parts(self, parent: QTreeWidgetItem, mod) -> None:
        """A switch per part, for a mod built out of several.

        A rework imported from someone's masters.db is one mod with a patch per
        table, and the point of keeping it one mod is being able to take the
        shop changes without the enemy tuning. A single-patch mod has nothing to
        choose between, so it gets no extra rows.
        """
        if len(mod.patches) < 2:
            return
        for patch in mod.patches:
            on = self.manager.state.is_part_on(mod.id, patch.key, patch.ships_on)
            child = QTreeWidgetItem(parent)
            child.setData(0, MOD_ID_ROLE, mod.id)
            child.setData(0, PATCH_KEY_ROLE, patch.key)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            child.setText(1, patch.part_label)
            child.setText(3, _affects(sorted(patch.tables()), sorted(patch.asset_targets())))
            if not on:
                child.setForeground(1, QBrush(status_color(self.dark, "dim")))
            child.setToolTip(
                1,
                f"{patch.summary()}\n\nUntick to leave this part out. Anything it "
                "already applied stays until you Revert the mod.",
            )

    def _problem_messages(self, mod_id: str, conflicts, validation) -> list[str]:
        messages = list(conflicts.for_mod(mod_id))
        if validation is not None:
            result = validation.for_mod(mod_id)
            if result is not None:
                messages = [f"FAILED: {e}" for e in result.errors] + messages + result.warnings
        if self.manager.last_apply is not None:
            applied = self.manager.last_apply.for_mod(mod_id)
            if applied is not None and applied.error:
                messages.insert(0, f"FAILED: {applied.error}")
        return messages

    def _problem_level(self, mod_id: str, conflicts, validation) -> str:
        """The theme colour for this mod's dot: "failed" (red), "pending"
        (yellow), or "" for no dot.

        Red when the mod failed, or another mod provably overwrites the same
        cells or file. Yellow for anything else worth a look - shared rows whose
        columns cannot be read, a warning, a missing requirement. Sharing a
        table is not one of them; see conflict.py.
        """
        messages = self._problem_messages(mod_id, conflicts, validation)
        if not messages:
            return ""
        if any(m.startswith("FAILED") for m in messages):
            return "failed"
        return "failed" if conflicts.severity_for(mod_id) == SERIOUS else "pending"

    def _dot_for(self, level: str) -> QIcon:
        key = (self.dark, level)
        if key not in self._dots:
            self._dots[key] = _dot(status_color(self.dark, level) if level else None)
        return self._dots[key]

    def _detail_text(self, mod, status: str, conflicts, validation) -> str:
        lines = [mod.description or "(no description)"]
        # Where the mod is filed, so a list sorted or filtered by category can
        # be read without going back to the boxes above it to remember what is
        # on. Only when there is something to say - an untagged mod in no
        # category gets a line saying so in neither.
        filing = []
        if mod.category:
            filing.append(mod.category)
        if mod.tags:
            filing.append(" ".join(f"#{tag}" for tag in mod.tags))
        if filing:
            lines.append("  -  ".join(filing))
        problems = self._problem_messages(mod.id, conflicts, validation)
        lines.extend(problems[:4])
        if len(problems) > 4:
            lines.append(f"... and {len(problems) - 4} more")
        applied = self.manager.state.applied.get(mod.id)
        if applied and status == "applied":
            lines.append(f"Applied {applied.applied_at} - {applied.rows_changed} row(s)")
        if applied and applied.version and mod.version and applied.version != mod.version:
            lines.append(
                f"Update available: version {applied.version} is in the database, "
                f"{mod.version} is installed - Save Mod List to apply it"
            )
        return "\n".join(lines)

    # -- selection ---------------------------------------------------------

    def selected_mod_ids(self) -> list[str]:
        ids = []
        for item in self.selectedItems():
            mod_id = item.data(0, MOD_ID_ROLE)
            if mod_id and mod_id not in ids:
                ids.append(mod_id)
        return ids

    def select_mods(self, mod_ids: list[str]) -> None:
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            item.setSelected(item.data(0, MOD_ID_ROLE) in mod_ids)

    def set_all_checked(self, checked: bool) -> None:
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            mod_id = item.data(0, MOD_ID_ROLE)
            if mod_id:
                item.setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    # -- signals -----------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Record the toggle and get out of Qt's way.

        This runs *inside* QTreeModel's data-changed emission, on the very item
        that changed. Whoever handles the toggle rebuilds this list, and calling
        clear() from here frees that item while Qt is still holding it - which
        segfaults as soon as the emission unwinds. So nothing is emitted
        synchronously: the mod id and its new state are copied out (never the
        item pointer, which may not survive) and handed on from a timer, once
        the stack is back under the event loop.
        """
        if self._loading or column != 0:
            return
        mod_id = item.data(0, MOD_ID_ROLE)
        if not mod_id:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        if item.parent() is not None:
            patch_key = item.data(0, PATCH_KEY_ROLE)
            if not patch_key:
                return  # the detail line, which carries no switch
            self._pending_parts[(mod_id, patch_key)] = checked
        else:
            typed = _typed_order(item)
            if typed is not None:
                self._pending_order[mod_id] = typed
            else:
                self._pending_toggles[mod_id] = checked
        if not self._flush_scheduled:
            self._flush_scheduled = True
            QTimer.singleShot(0, self._flush_toggles)

    def _flush_toggles(self) -> None:
        """Hand over every toggle collected this tick, then one refresh signal.

        Ticking 20 boxes at once (Tools > Enable all) is one rebuild, not 20.
        """
        self._flush_scheduled = False
        pending, self._pending_toggles = self._pending_toggles, {}
        parts, self._pending_parts = self._pending_parts, {}
        order, self._pending_order = self._pending_order, {}
        if not pending and not parts and not order:
            return
        for mod_id, position in order.items():
            self.orderTyped.emit(mod_id, position)
        for mod_id, checked in pending.items():
            self.enabledChanged.emit(mod_id, checked)
        for (mod_id, patch_key), checked in parts.items():
            self.partToggled.emit(mod_id, patch_key, checked)
        if pending or parts:
            self.togglesApplied.emit()

    def _on_selection_changed(self) -> None:
        selected = self.selected_mod_ids()
        self.selectionChangedTo.emit(selected[0] if selected else "")
