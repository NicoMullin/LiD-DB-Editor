"""The mod list: a checkbox, a status light, and what each mod touches."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from ..manager import Manager
from .theme import STATUS_GLYPH, STATUS_KEY, STATUS_TEXT, status_color

MOD_ID_ROLE = Qt.ItemDataRole.UserRole
PATCH_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def _affects(tables: list[str], asset_targets: list[str]) -> str:
    """The 'Affects' column: table names, then any game files as 'file: X.upk'."""
    bits = list(tables)
    bits += [f"file: {target.rsplit('/', 1)[-1]}" for target in asset_targets]
    return ", ".join(bits) or "-"


class ModListWidget(QTreeWidget):
    """Two rows per mod: the mod itself, and a child line with the details."""

    enabledChanged = Signal(str, bool)  # one per mod, deferred - see _on_item_changed
    partToggled = Signal(str, str, bool)  # mod id, patch key, on/off
    togglesApplied = Signal()  # once after a batch of enabledChanged
    selectionChangedTo = Signal(str)

    def __init__(self, manager: Manager, dark: bool = True, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.dark = dark
        self._loading = False
        self._pending_toggles: dict[str, bool] = {}
        self._pending_parts: dict[tuple[str, str], bool] = {}
        self._flush_scheduled = False

        # Column 0 carries the checkbox and the load-order number together.
        self.setColumnCount(4)
        self.setHeaderLabels(["#", "Mod", "Status", "Affects"])
        self.setRootIsDecorated(False)
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

        self.itemChanged.connect(self._on_item_changed)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    # -- population --------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild from the manager, keeping the current selection if we can."""
        selected = self.selected_mod_ids()
        self._loading = True
        self.clear()

        conflicts = self.manager.conflicts()
        validation = self.manager.last_validation

        # Enabled mods first, in load order; then everything else.
        for mod in self.manager.listed_mods():
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
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setText(1, f"{mod.id}  -  {mod.name}")
            item.setText(2, f"{STATUS_GLYPH[status]} {STATUS_TEXT[status]}")
            item.setText(3, _affects(sorted(mod.tables()), mod.asset_targets()))

            color = status_color(self.dark, STATUS_KEY[status])
            item.setForeground(2, QBrush(color))
            font = QFont()
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
            if self._problem_messages(mod.id, conflicts, validation):
                detail.setForeground(0, QBrush(status_color(self.dark, "pending")))

            self._add_parts(item, mod)
            item.setExpanded(True)

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
            on = self.manager.state.is_part_enabled(mod.id, patch.key)
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

    def _detail_text(self, mod, status: str, conflicts, validation) -> str:
        lines = [mod.description or "(no description)"]
        problems = self._problem_messages(mod.id, conflicts, validation)
        lines.extend(problems[:4])
        if len(problems) > 4:
            lines.append(f"... and {len(problems) - 4} more")
        applied = self.manager.state.applied.get(mod.id)
        if applied and status == "applied":
            lines.append(f"Applied {applied.applied_at} - {applied.rows_changed} row(s)")
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
        if not pending and not parts:
            return
        for mod_id, checked in pending.items():
            self.enabledChanged.emit(mod_id, checked)
        for (mod_id, patch_key), checked in parts.items():
            self.partToggled.emit(mod_id, patch_key, checked)
        self.togglesApplied.emit()

    def _on_selection_changed(self) -> None:
        selected = self.selected_mod_ids()
        self.selectionChangedTo.emit(selected[0] if selected else "")
