"""The main window: everything from the UI sketch, wired to the Manager."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, DB_FILENAME, DEFAULT_DB_HINT, __version__, steam_locate
from .. import browse
from ..browse import SORT_LABELS, SORT_ORDER
from ..textsize import DEFAULT_SCALE, SCALE_PRESETS, clamp_scale, stepped
from .. import backup as backup_module
from ..backup import BACKUP_SUFFIX, ORIGINAL_SUFFIX
from ..install import (
    KIND_ASSET_FOLDER,
    KIND_CONTENT_PACK,
    KIND_DATABASE,
    KIND_VETTED_MOD,
    InstallCandidate,
    InstallError,
)
from ..errors import ModManagerError
from ..modedit import ModEditError
from .. import vanilla_library
from ..manager import Manager
from ..watchdog import STATUS_OK, STATUS_STALE, DbStatus
from .adopt_dialog import AdoptDialog
from .diff_view import DiffView
from .edit_dialog import EditModDialog
from .install_dialog import InstallDialog
from .log_panel import LogPanel
from .mod_filter_bar import ModFilterBar
from .mod_list import MOD_ID_ROLE, ModListWidget
from .theme import apply_theme, colors
from .workers import TaskThread, WatchThread

ORDER_HINT = "Load order: top applies first, bottom wins"


class MainWindow(QMainWindow):
    def __init__(self, manager: Manager):
        super().__init__()
        self.manager = manager
        self.dark = manager.state.settings.dark_mode
        self.task: TaskThread | None = None
        self.unsaved = False

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1180, 780)
        # Dropping a mod on the window is how most people will install one.
        self.setAcceptDrops(True)
        self._install_queue: list[Path] = []

        self._build_header()
        self._build_body()
        self._build_footer()
        self._build_menu()

        self.watch = WatchThread(manager.state.settings.poll_seconds)
        self.watch.statusChanged.connect(self._on_db_status)
        self._sync_watcher()
        if manager.state.settings.watchdog_enabled:
            self.watch.start()

        self.refresh(rescan=False)
        # Parented to the window on purpose. QTimer.singleShot with a bound
        # method keeps no receiver, so it still fires after the window has been
        # destroyed and then builds a QMessageBox on a dead C++ object. Qt owns
        # this one and takes it down with the window.
        self._first_run = QTimer(self)
        self._first_run.setSingleShot(True)
        self._first_run.timeout.connect(self._first_run_checks)
        self._first_run.start(0)

    # -- construction ------------------------------------------------------

    def _build_header(self) -> None:
        self.db_label = QLabel()
        self.db_label.setObjectName("path")
        self.db_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        browse = QPushButton("Browse...")
        browse.clicked.connect(self.choose_database)

        self.status_label = QLabel()
        self.last_save_label = QLabel()
        self.last_save_label.setObjectName("dim")

        self.watchdog_box = QCheckBox("Watchdog")
        self.watchdog_box.setToolTip(
            f"Poll {DB_FILENAME} every few seconds and notice when the game "
            "replaces it, without needing to click around first. Off by default. "
            "Only ever shows a \"stale, click Re-apply All\" notice by itself - "
            "see Auto re-apply for the (also off by default) setting that acts on it."
        )
        self.watchdog_box.setChecked(self.manager.state.settings.watchdog_enabled)
        self.watchdog_box.toggled.connect(self._on_watchdog_toggled)

        self.auto_box = QCheckBox("Auto re-apply")
        self.auto_box.setToolTip(
            "Re-apply the enabled mods the moment a change is detected, with no "
            "confirmation - including while Steam is still verifying game files, "
            "which can race it. Off by default; leave it off unless you want that "
            "convenience and accept the risk. With it off, a change still shows a "
            "\"click Re-apply All\" notice - nothing is written until you do."
        )
        self.auto_box.setChecked(self.manager.state.settings.auto_reapply)
        self.auto_box.toggled.connect(self._on_auto_toggled)

        self.auto_check_off_box = QCheckBox("Auto hash patch")
        self.auto_check_off_box.setToolTip(
            "When a mod is blocked because the game still checks a file it "
            "replaces, take that file off the game's list without asking, then "
            "carry on saving.\n\n"
            "On by default. It is the only setting here that changes the "
            "game's executable, but the change is one byte per file and no "
            "program code, your executable is copied before it is touched, and "
            "Tools > Hash Patcher puts it back whenever you want.\n\n"
            "Untick it to be asked each time instead. Either way it is written "
            "to the log and said in the status bar - it never happens quietly."
        )
        self.auto_check_off_box.setChecked(
            self.manager.state.settings.auto_switch_file_check_off
        )
        self.auto_check_off_box.toggled.connect(self._on_auto_check_off_toggled)

        self.reapply_button = QPushButton("Re-apply All")
        self.reapply_button.clicked.connect(self.reapply_all)

        top = QHBoxLayout()
        top.addWidget(QLabel("Database:"))
        top.addWidget(self.db_label, 1)
        top.addWidget(browse)

        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.last_save_label)
        bottom.addWidget(self.watchdog_box)
        bottom.addWidget(self.auto_box)
        bottom.addWidget(self.auto_check_off_box)
        bottom.addWidget(self.reapply_button)

        self.header = QWidget()
        layout = QVBoxLayout(self.header)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addLayout(top)
        layout.addLayout(bottom)

    def _build_body(self) -> None:
        self.mod_list = ModListWidget(self.manager, self.dark)
        self.mod_list.enabledChanged.connect(self._on_mod_toggled)
        self.mod_list.partToggled.connect(self._on_part_toggled)
        self.mod_list.togglesApplied.connect(self._after_mods_toggled)
        self.mod_list.selectionChangedTo.connect(self._on_mod_selected)
        self.mod_list.orderTyped.connect(self.set_mod_position)
        # Right-clicking a mod is where people look for what they can do to it.
        self.mod_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mod_list.customContextMenuRequested.connect(self._mod_context_menu)

        self.diff_view = DiffView(
            self.manager, self.dark, text_scale=self.manager.state.settings.text_scale
        )
        self.diff_view.settingChanged.connect(self._on_setting_changed)
        self.log_panel = LogPanel(self.manager.log, self.dark)

        # The list plus its load-order controls, as one panel.
        self.move_up_button = QPushButton("Move up")
        self.move_up_button.setToolTip("Apply this mod earlier, so later mods can overwrite it")
        self.move_up_button.clicked.connect(lambda: self.move_selected(-1))
        self.move_down_button = QPushButton("Move down")
        self.move_down_button.setToolTip("Apply this mod later, so it overwrites the ones above")
        self.move_down_button.clicked.connect(lambda: self.move_selected(+1))

        self.order_hint = QLabel(ORDER_HINT)
        self.order_hint.setObjectName("dim")

        order_row = QHBoxLayout()
        order_row.addWidget(self.move_up_button)
        order_row.addWidget(self.move_down_button)
        order_row.addWidget(self.order_hint, 1)

        # Search, category, tags and sort. Above the list because it decides
        # what is in the list; the load-order controls stay below it because
        # they act on what is selected.
        self.filter_bar = ModFilterBar()
        self.filter_bar.set_sort(self.manager.state.settings.mod_sort)
        self.filter_bar.changed.connect(self._on_filter_changed)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self.filter_bar)
        left_layout.addWidget(self.mod_list, 1)
        left_layout.addLayout(order_row)

        self.center_split = QSplitter(Qt.Orientation.Horizontal)
        self.center_split.addWidget(left_panel)
        self.center_split.addWidget(self.diff_view)
        self.center_split.setStretchFactor(0, 3)
        self.center_split.setStretchFactor(1, 2)

        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split.addWidget(self.center_split)
        self.main_split.addWidget(self.log_panel)
        self.main_split.setStretchFactor(0, 4)
        self.main_split.setStretchFactor(1, 1)

    def _build_footer(self) -> None:
        self.modpack_box = QComboBox()
        self.modpack_box.setMinimumWidth(180)
        save_pack = QPushButton("Save as...")
        save_pack.clicked.connect(self.save_modpack)
        load_pack = QPushButton("Load")
        load_pack.clicked.connect(self.load_modpack)
        delete_pack = QPushButton("Delete")
        delete_pack.clicked.connect(self.delete_modpack)

        self.dark_box = QCheckBox("Dark mode")
        self.dark_box.setChecked(self.dark)
        self.dark_box.toggled.connect(self._on_dark_toggled)

        packs = QHBoxLayout()
        packs.addWidget(QLabel("Modpacks:"))
        packs.addWidget(self.modpack_box)
        packs.addWidget(save_pack)
        packs.addWidget(load_pack)
        packs.addWidget(delete_pack)
        packs.addStretch(1)
        packs.addWidget(self.dark_box)

        validate = QPushButton("Validate")
        validate.clicked.connect(self.validate)
        self.revert_button = QPushButton("Revert Selected")
        self.revert_button.clicked.connect(self.revert_selected)
        self.save_button = QPushButton("Save Mod List")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("Back up the database, then validate and apply every enabled mod.")
        self.save_button.clicked.connect(self.save_mod_list)

        actions = QHBoxLayout()
        actions.addWidget(validate)
        actions.addStretch(1)
        actions.addWidget(self.revert_button)
        actions.addWidget(self.save_button)

        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.addLayout(packs)
        layout.addLayout(actions)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.header)
        central_layout.addWidget(line)
        central_layout.addWidget(self.main_split, 1)
        central_layout.addWidget(footer)
        self.setCentralWidget(central)

        self.buttons = [self.save_button, self.reapply_button, self.revert_button, validate]

        version_label = QLabel(f"v{__version__}")
        version_label.setObjectName("dim")
        self.statusBar().addPermanentWidget(version_label)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._add_action(file_menu, "Choose database...", self.choose_database, "Ctrl+O")
        self._add_action(file_menu, "Rescan mods folder", lambda: self.refresh(rescan=True), "F5")
        self._add_action(
            file_menu, "Open mods folder", lambda: _open_folder(self.manager.paths.mods_dir)
        )
        self._add_action(
            file_menu, "Open logs folder", lambda: _open_folder(self.manager.paths.logs_dir)
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Quit", self.close, "Ctrl+Q")

        # Two menus, split by what they act on: Mods is the list itself -
        # which mods, in what order, and making new ones. Tools is everything
        # that acts on the database or the game folder.
        mods_menu = self.menuBar().addMenu("&Mods")
        mods_menu.addSection("The mod list")
        self._add_action(mods_menu, "Enable all mods", lambda: self.mod_list.set_all_checked(True))
        self._add_action(mods_menu, "Disable all mods", lambda: self.mod_list.set_all_checked(False))
        self._add_action(mods_menu, "Expand all mods", lambda: self.mod_list.set_all_expanded(True))
        self._add_action(
            mods_menu, "Collapse all mods", lambda: self.mod_list.set_all_expanded(False)
        )

        mods_menu.addSection("Load order")
        self._add_action(
            mods_menu, "Move up the load order", lambda: self.move_selected(-1), "Ctrl+Up"
        )
        self._add_action(
            mods_menu, "Move down the load order", lambda: self.move_selected(+1), "Ctrl+Down"
        )
        self._add_action(
            mods_menu, "Send to the top of the load order",
            lambda: self.send_selected_to("top"), "Ctrl+Home",
        )
        self._add_action(
            mods_menu, "Send to the bottom of the load order",
            lambda: self.send_selected_to("bottom"), "Ctrl+End",
        )

        mods_menu.addSection("Make and change mods")
        self._add_action(mods_menu, "Build a mod...", self.build_mod, "Ctrl+B")
        self._add_action(
            mods_menu, "Create a mod from a modded masters.db...", self.import_database
        )
        self._add_action(mods_menu, "Add a mod from a file...", self.add_mod_from_file)
        self._add_action(mods_menu, "Add a mod from a folder...", self.add_mod_from_folder)
        self._add_action(mods_menu, "Edit mod details...", self.edit_selected_mod, "F2")
        self._add_action(mods_menu, "Delete mod...", self.delete_selected_mod)

        tools = self.menuBar().addMenu("&Tools")
        tools.addSection("Your database")
        self._add_action(tools, "Validate enabled mods", self.validate, "Ctrl+T")
        self._add_action(tools, "Save Mod List (apply)", self.save_mod_list, "Ctrl+S")
        self._add_action(tools, "Re-apply All", self.reapply_all, "Ctrl+R")
        self._add_action(
            tools, "Scan my database for mods already in it...", self.scan_for_existing_mods
        )
        self._add_action(tools, "Clean database to compare against...", self.choose_vanilla)

        tools.addSection("Your game folder")
        self._add_action(tools, "Set item artwork folder...", self.set_icon_folder)
        self._add_action(tools, "Set game folder...", self.set_game_folder)
        self._add_action(tools, "Hash Patcher...", self.open_file_check)

        tools.addSection("Putting things back")
        self._add_action(tools, "Restore game files...", self.restore_game_files)
        self._add_action(tools, "Restore a backup...", self.restore_backup)

        view = self.menuBar().addMenu("&View")
        view.addSection("Text size")
        # Ctrl+= as well as Ctrl++, because + is a shifted key on most layouts
        # and Ctrl++ needs three fingers to reach.
        self._add_action(view, "Bigger text", lambda: self.step_text_size(up=True), "Ctrl+=")
        bigger_plus = QAction("Bigger text", self)
        bigger_plus.triggered.connect(lambda: self.step_text_size(up=True))
        bigger_plus.setShortcut("Ctrl++")
        bigger_plus.setVisible(False)
        self.addAction(bigger_plus)
        self._add_action(view, "Smaller text", lambda: self.step_text_size(up=False), "Ctrl+-")
        self._add_action(view, "Normal size", self.reset_text_size, "Ctrl+0")
        view.addSeparator()
        self.text_size_actions: list[QAction] = []
        for percent in SCALE_PRESETS:
            action = QAction(f"{percent}%", self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, percent=percent: self.set_text_size(percent)
            )
            view.addAction(action)
            action.setData(percent)
            self.text_size_actions.append(action)
        self._refresh_text_size_menu()

        help_menu = self.menuBar().addMenu("&Help")
        self._add_action(help_menu, "About", self.about)

    def _add_action(self, menu, text: str, slot, shortcut: str = "") -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # -- refresh -----------------------------------------------------------

    def refresh(self, *, rescan: bool = False) -> None:
        if rescan:
            self.manager.rescan()
        self._sync_watcher()
        # The choices come from the mods themselves, so a mod that has just
        # arrived brings its category and tags into the boxes with it.
        self.filter_bar.set_choices(self.manager.categories(), self.manager.tags())
        self.mod_list.filter = self.filter_bar.current_filter()
        self.mod_list.sort = self.filter_bar.current_sort()
        self.mod_list.refresh()
        self.filter_bar.set_counts(self.mod_list.shown_count, self.mod_list.total_count)
        self._refresh_order_controls()
        self.diff_view.refresh()
        self._refresh_header()
        self._refresh_modpacks()

    def _on_filter_changed(self) -> None:
        """The search, a category, a tag or the sort moved."""
        sort = self.filter_bar.current_sort()
        if sort != self.manager.state.settings.mod_sort:
            # Remembered, unlike the filters - see mod_filter_bar.py.
            self.manager.state.settings.mod_sort = sort
            self.manager.state.save()
        self.mod_list.filter = self.filter_bar.current_filter()
        self.mod_list.sort = sort
        self.mod_list.refresh()
        self.filter_bar.set_counts(self.mod_list.shown_count, self.mod_list.total_count)
        self._refresh_order_controls()

    def _refresh_order_controls(self) -> None:
        """Turn the load-order controls off where they would mislead.

        Move up and Move down are relative - "swap with the row next to this
        one" - so they only mean what they look like when every row is on screen
        in load order. Typing a number is absolute, so it survives a filter; it
        is only sorting that takes it away, because the rows around the number
        would no longer be the ones the number is measured against.
        """
        sorted_away = self.filter_bar.current_sort() != SORT_ORDER
        filtered = self.mod_list.filter.on
        self.move_up_button.setEnabled(not sorted_away and not filtered)
        self.move_down_button.setEnabled(not sorted_away and not filtered)
        if sorted_away:
            label = SORT_LABELS.get(self.filter_bar.current_sort(), "something else")
            self.order_hint.setText(
                f"Sorted by {label} - switch Sort back to “Load order” to move mods"
            )
        elif filtered:
            self.order_hint.setText(
                "Some mods are hidden - you can still type a number, but Clear the "
                "filter to move mods one at a time"
            )
        else:
            self.order_hint.setText(ORDER_HINT)

    def _refresh_header(self) -> None:
        db_path = self.manager.db_path
        self.db_label.setText(str(db_path) if db_path else "(none selected - click Browse)")
        status = self.manager.db_status()
        self._set_status(status)
        self.last_save_label.setText(
            f"Last save: {self.manager.state.last_saved_at or 'never'}"
        )
        enabled = len(self.manager.state.enabled_mods)
        suffix = " - unsaved changes" if self.unsaved else ""
        self.setWindowTitle(f"{APP_NAME} {__version__} - {enabled} mod(s) enabled{suffix}")

    def _set_status(self, status: DbStatus) -> None:
        palette = colors(self.dark)
        color = {
            STATUS_OK: palette["ok"],
            STATUS_STALE: palette["pending"],
        }.get(status.state, palette["failed"])
        self.status_label.setText(status.label)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        if status.state != STATUS_STALE:
            # Drop the "click Re-apply All" nag once the DB is back in step.
            self.statusBar().clearMessage()

    def _refresh_modpacks(self) -> None:
        current = self.modpack_box.currentText()
        self.modpack_box.blockSignals(True)
        self.modpack_box.clear()
        self.modpack_box.addItems(sorted(self.manager.state.modpacks))
        if current:
            index = self.modpack_box.findText(current)
            if index >= 0:
                self.modpack_box.setCurrentIndex(index)
        self.modpack_box.blockSignals(False)

    def _sync_watcher(self) -> None:
        """Hand the watchdog thread the fingerprint the mods are known to be on.

        Re-configuring drops the thread's cached hash, so this only fires when
        something actually moved - otherwise every checkbox tick would make the
        watchdog re-hash the whole database.
        """
        fingerprint = (
            str(self.manager.db_path or ""),
            self.manager.state.db_sha256_at_last_save,
            self.manager.state.db_mtime_at_last_save,
        )
        if fingerprint == getattr(self, "_watcher_fingerprint", None):
            return
        self._watcher_fingerprint = fingerprint
        self.watch.configure(self.manager.db_path, fingerprint[1], fingerprint[2])

    # -- database ----------------------------------------------------------

    def _database_hint(self) -> tuple[str, bool]:
        """Where masters.db probably is, and whether it was actually found there.

        Looked up once: Steam can put the game in a library on any drive, and
        the picker should open in the right one rather than on C: regardless.
        """
        if not hasattr(self, "_found_database"):
            try:
                self._found_database = steam_locate.find_game_database()
            except Exception:
                self._found_database = None
        if self._found_database is not None:
            return str(self._found_database), True
        return DEFAULT_DB_HINT, False

    def choose_database(self) -> None:
        start = str(self.manager.db_path or self._database_hint()[0])
        path, _ = QFileDialog.getOpenFileName(
            self, f"Locate {DB_FILENAME}", start, f"{DB_FILENAME} (*.db);;All files (*)"
        )
        if not path:
            return
        self.manager.set_db_path(path)
        self._sync_watcher()
        self.refresh()
        kept = backup_module.original_backup_path(Path(path))
        if kept.is_file():
            self.statusBar().showMessage(
                f"Keeping {kept.name} as your way back to stock - it is never overwritten.",
                12000,
            )

    # -- mod list ----------------------------------------------------------

    def _on_mod_toggled(self, mod_id: str, enabled: bool) -> None:
        # State only. Rebuilding the list happens once, in _after_mods_toggled.
        self.manager.set_enabled(mod_id, enabled)
        self.unsaved = True

    def _on_part_toggled(self, mod_id: str, patch_key: str, enabled: bool) -> None:
        # State only, same as a whole mod - the rebuild happens once afterwards.
        self.manager.set_part_enabled(mod_id, patch_key, enabled)
        self.unsaved = True

    def _after_mods_toggled(self) -> None:
        self.manager.state.save()
        self.mod_list.refresh()
        self.diff_view.refresh()
        self._refresh_header()

    def _on_mod_selected(self, mod_id: str) -> None:
        self.diff_view.show_mod(mod_id)

    def _on_setting_changed(self, mod_id: str, setting_id: str, value) -> None:
        """A value was chosen in a mod's row. Takes effect on the next save."""
        try:
            number = self.manager.set_mod_setting(mod_id, setting_id, value)
        except (ValueError, ModManagerError) as exc:
            QMessageBox.warning(self, "That value is not allowed", str(exc))
            self.mod_list.refresh()
            return
        self.unsaved = True
        self.mod_list.refresh()
        self.diff_view.refresh()
        self._refresh_header()
        mod = self.manager.configured_mod(mod_id)
        setting = mod.setting(setting_id) if mod is not None else None
        if mod is None or setting is None:
            return
        if self.manager.state.is_enabled(mod_id):
            tail = " - click Save Mod List to apply it."
        else:
            tail = " - tick the mod and click Save Mod List to apply it."
        self.statusBar().showMessage(
            f"{mod.name}: {setting.label} is now {setting.display(number)}{tail}", 12000
        )

    def _open_configuration(self, mod_id: str) -> None:
        """Show a mod's Configuration tab, ready to type in."""
        self.mod_list.select_mods([mod_id])
        self.diff_view.show_mod(mod_id)
        self.diff_view.show_configuration()

    def _commit_setting_edits(self) -> None:
        """Take any value still being typed as it stands, before a save."""
        for mod_id, setting_id, value in self.diff_view.pending_setting_edits():
            try:
                self.manager.set_mod_setting(mod_id, setting_id, value)
            except (ValueError, ModManagerError) as exc:
                self.manager.log.warn(str(exc))

    # -- right-click menu --------------------------------------------------

    def _mod_context_menu(self, point) -> None:
        item = self.mod_list.itemAt(point)
        menu = self._build_mod_menu(item)
        menu.exec(self.mod_list.viewport().mapToGlobal(point))

    def _build_mod_menu(self, item) -> QMenu:
        """The menu for a right-clicked row. Built apart from showing it so a
        test can read what it offers without a window manager involved."""
        menu = QMenu(self)
        mod_id = item.data(0, MOD_ID_ROLE) if item is not None else None
        if mod_id:
            # Act on what was right-clicked, not on some older selection.
            if mod_id not in self.mod_list.selected_mod_ids():
                self.mod_list.select_mods([mod_id])
            enabled = self.manager.state.is_enabled(mod_id)
            menu.addAction(
                "Disable" if enabled else "Enable",
                lambda: self._set_enabled_from_menu(mod_id, not enabled),
            )
            menu.addSeparator()
            menu.addAction("Edit details...", self.edit_selected_mod)
            mod = self.manager.scan.get(mod_id)
            if mod is not None and mod.settings:
                menu.addAction("Change values...", lambda: self._open_configuration(mod_id))
            if mod is not None:
                menu.addAction("Open mod folder", lambda: _open_folder(mod.folder))
            menu.addSeparator()
            menu.addAction("Move up the load order", lambda: self.move_selected(-1))
            menu.addAction("Move down the load order", lambda: self.move_selected(+1))
            menu.addAction("Send to the top", lambda: self.send_selected_to("top"))
            menu.addAction("Send to the bottom", lambda: self.send_selected_to("bottom"))
            menu.addSeparator()
            if mod is not None:
                self._add_filing_menu(menu, mod)
            menu.addAction("Revert - put its rows back", self.revert_selected)
            menu.addAction("Delete mod...", self.delete_selected_mod)
            menu.addSeparator()
        else:
            menu.addAction("Rescan mods folder", lambda: self.refresh(rescan=True))
            menu.addSeparator()
        menu.addAction("Expand all", lambda: self.mod_list.set_all_expanded(True))
        menu.addAction("Collapse all", lambda: self.mod_list.set_all_expanded(False))
        return menu

    def _add_filing_menu(self, menu: QMenu, mod) -> None:
        """"Show only..." entries, so a mod is its own way into its category.

        Finding the other coin mods starts from a coin mod far more often than
        from the Category box, and this saves reading the box to work out what
        this one is filed under.
        """
        category = browse.category_of(mod)
        if category != browse.UNCATEGORISED:
            menu.addAction(
                f"Show only {category}",
                lambda: self._filter_to(category=category),
            )
        if mod.tags:
            tags = menu.addMenu("Show only mods tagged")
            for tag in mod.tags:
                tags.addAction(tag, lambda checked=False, tag=tag: self._filter_to(tags=[tag]))
        menu.addSeparator()

    def _filter_to(self, *, category: str | None = None, tags=None) -> None:
        """Narrow the list to one category or tag, from wherever it was."""
        self.filter_bar.clear()
        if category is not None:
            self.filter_bar.set_category(category)
        if tags is not None:
            self.filter_bar.set_tags(tags)

    # -- text size ---------------------------------------------------------

    def _refresh_text_size_menu(self) -> None:
        """Tick the preset that matches, or none when the size is between two."""
        current = self.manager.state.settings.text_scale
        for action in self.text_size_actions:
            action.setChecked(action.data() == current)

    def set_text_size(self, percent) -> None:
        """Make every bit of text in the window this size, and remember it."""
        wanted = clamp_scale(percent)
        if wanted == self.manager.state.settings.text_scale:
            self._refresh_text_size_menu()
            return
        self.manager.state.settings.text_scale = wanted
        self.manager.state.save()
        self.apply_text_size()
        self.statusBar().showMessage(
            f"Text size {wanted}%"
            + ("" if wanted != DEFAULT_SCALE else " (normal)"),
            4000,
        )

    def apply_text_size(self) -> None:
        """Re-theme at the current size and let everything lay out again."""
        scale = self.manager.state.settings.text_scale
        apply_theme(QApplication.instance(), self.dark, scale)
        # The details panel builds its own HTML, so it has to be told; the rest
        # picks the new size up from the stylesheet.
        self.diff_view.set_text_scale(scale)
        self._refresh_text_size_menu()
        self.refresh()

    def step_text_size(self, *, up: bool) -> None:
        self.set_text_size(stepped(self.manager.state.settings.text_scale, up=up))

    def reset_text_size(self) -> None:
        self.set_text_size(DEFAULT_SCALE)

    def _set_enabled_from_menu(self, mod_id: str, enabled: bool) -> None:
        self.manager.set_enabled(mod_id, enabled)
        self.unsaved = True
        self._after_mods_toggled()

    def delete_selected_mod(self) -> None:
        """Remove a mod's folder. Offers to put its rows back first."""
        mods = [
            mod for mod in (self.manager.scan.get(m) for m in self.mod_list.selected_mod_ids())
            if mod is not None
        ]
        if not mods:
            QMessageBox.information(self, "Delete mod", "Pick a mod in the list first.")
            return
        names = "\n  ".join(mod.name for mod in mods)
        ids = [mod.id for mod in mods]
        applied = [mod_id for mod_id in ids if mod_id in self.manager.state.applied]
        if applied:
            choice = QMessageBox.question(
                self,
                "Delete mod",
                f"Delete these mods?\n\n  {names}\n\n"
                "Their changes are still in your database. Put those rows back first?\n\n"
                "Yes - undo the changes, then delete\n"
                "No - delete anyway and leave the changes in place",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self._pending_delete = ids
                self._run(lambda: self.manager.revert(applied), self._after_revert_then_delete)
                return
        else:
            confirm = QMessageBox.question(
                self,
                "Delete mod",
                f"Delete these mod folders?\n\n  {names}\n\nThis removes them from disk.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self._delete_mods(ids)

    def _after_revert_then_delete(self, results) -> None:
        self._after_revert(results)
        ids = getattr(self, "_pending_delete", [])
        self._pending_delete = []
        if ids:
            self._delete_mods(ids)

    def _delete_mods(self, mod_ids: list[str]) -> None:
        failed = []
        for mod_id in mod_ids:
            try:
                self.manager.delete_mod(mod_id)
            except Exception as exc:  # a locked folder, a vanished mod
                failed.append(f"{mod_id}: {exc}")
        self.refresh()
        if failed:
            QMessageBox.warning(self, "Could not delete", "\n".join(failed))
        else:
            self.statusBar().showMessage(
                f"Deleted {len(mod_ids)} mod(s)" if len(mod_ids) > 1 else f"Deleted {mod_ids[0]}",
                6000,
            )

    def _to_move(self, what: str) -> list[str]:
        """The selected mods that have a load order, or [] with a word said."""
        selected = [
            m for m in self.mod_list.selected_mod_ids() if self.manager.state.is_enabled(m)
        ]
        if not selected:
            self.statusBar().showMessage(
                f"Select an enabled mod to {what} - the load order only covers "
                "enabled mods.", 6000
            )
        return selected

    def _after_reorder(self, moved: list[bool], selected: list[str]) -> None:
        if not any(moved):
            return
        self.unsaved = True
        self.mod_list.refresh()
        self.mod_list.select_mods(selected)
        self._refresh_header()

    def move_selected(self, delta: int) -> None:
        """Shift the selected mods up or down the load order."""
        enabled = self._to_move("move")
        if not enabled:
            return
        # Moving down means starting from the bottom, or the mods trip over
        # each other on the way.
        order = self.manager.state.enabled_mods
        enabled.sort(key=order.index, reverse=delta > 0)
        # A list, not any(): any() stops at the first mod that moved, which
        # with several selected left the rest of them where they were.
        moved = [self.manager.move_mod(mod_id, delta) for mod_id in enabled]
        self._after_reorder(moved, enabled)

    def send_selected_to(self, edge: str) -> None:
        """Send the selected mods to the top or the bottom of the load order."""
        enabled = self._to_move("send")
        if not enabled:
            return
        order = self.manager.state.enabled_mods
        # Each mod is placed in turn, so the pass runs in whichever direction
        # leaves the selection in the order it was already in.
        enabled.sort(key=order.index, reverse=(edge == "top"))
        place = 1 if edge == "top" else len(order)
        moved = [self.manager.set_mod_position(mod_id, place) for mod_id in enabled]
        self._after_reorder(moved, enabled)

    def set_mod_position(self, mod_id: str, position: int) -> None:
        """Put one mod at the place that was typed into its number."""
        if not self.manager.set_mod_position(mod_id, position):
            self.mod_list.refresh()  # put the number that was typed over back
            return
        self.unsaved = True
        self.mod_list.refresh()
        self.mod_list.select_mods([mod_id])
        self._refresh_header()

    # -- long-running actions ----------------------------------------------

    def _check_database(self) -> bool:
        if self.manager.db_path and self.manager.db_path.is_file():
            return True
        QMessageBox.warning(
            self,
            "No database",
            f"Point the manager at your {DB_FILENAME} first (File > Choose database).",
        )
        return False

    def _busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.setEnabled(not busy)
        if busy:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        else:
            QGuiApplication.restoreOverrideCursor()

    def _show_progress(self, message: str, determinate: bool = False) -> None:
        """A small window naming the slow thing that is happening.

        Reading a 60 MB database, or rebuilding one and applying a content pack
        to it, takes seconds. With the buttons greyed out and nothing moving,
        that reads as a hang - and the natural response is to kill the program
        half way through writing a database.
        """
        self._close_progress()
        if not message:
            return
        # 0..0 is a bar that just moves, for work that cannot say how far it is.
        dialog = QProgressDialog(message, "", 0, 100 if determinate else 0, self)
        dialog.setWindowTitle(APP_NAME)
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(460)
        if determinate:
            dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()  # paint it before the work starts
        self._progress = dialog

    def _on_progress(self, text: str, value: int) -> None:
        progress = getattr(self, "_progress", None)
        if progress is None:
            return
        progress.setLabelText(
            f"{text}\n\nPlease don't close the manager or start the game until this "
            "finishes."
        )
        progress.setValue(value)

    def _close_progress(self) -> None:
        progress = getattr(self, "_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
        self._progress = None

    def _run(self, work, on_done, message: str = "", progress: bool = False) -> None:
        """Run ``work`` off the GUI thread. With ``progress`` it is handed a
        Progress and the window shows a bar that fills as it reports."""
        if self.task is not None and self.task.isRunning():
            return
        self._busy(True)
        self._show_progress(message, determinate=progress)
        self.task = TaskThread(work, self, reports_progress=progress)
        self.task.progressed.connect(self._on_progress)
        self.task.succeeded.connect(lambda result: self._finish(on_done, result, ""))
        self.task.failed.connect(lambda trace: self._finish(on_done, None, trace))
        self.task.start()

    def _finish(self, on_done, result, trace: str) -> None:
        self._close_progress()
        self._busy(False)
        self.task = None
        if trace:
            last = trace.strip().splitlines()[-1]
            self.manager.log.error(last)
            if "InstallError" in trace or "IncompatibleDatabase" in trace:
                QMessageBox.warning(self, "Cannot use that file", last.split(": ", 1)[-1])
            else:
                QMessageBox.critical(self, "Something went wrong", trace)
            self.refresh()
            return
        on_done(result)
        self._sync_watcher()
        self.refresh()

    def save_mod_list(self) -> None:
        self._commit_setting_edits()
        if not self._check_database():
            return
        self._run(
            self.manager.save_mod_list,
            self._after_apply,
            message="Applying your mods...",
            progress=True,
        )

    def reapply_all(self) -> None:
        if not self._check_database():
            return
        self._run(
            self.manager.reapply_all,
            self._after_apply,
            message="Re-applying your mods...",
            progress=True,
        )

    def _after_apply(self, report) -> None:
        self.unsaved = False
        if report is None:
            return
        if report.ok:
            self.statusBar().showMessage(report.summary_line(), 8000)
            return
        message = report.error or "Apply failed."
        if report.validation is not None and report.validation.failed:
            details = "\n\n".join(
                f"{result.mod_id}:\n  " + "\n  ".join(result.errors)
                for result in report.validation.failed
            )
            message = f"{message}\n\n{details}"
        # The one failure with a fix the manager can carry out itself. Offering
        # it here, where it went wrong, saves hunting through a menu for a panel
        # whose name means nothing until you have read the error.
        if self._offer_file_check(report):
            return
        QMessageBox.warning(self, "Nothing was applied", message)

    def _offer_file_check(self, report) -> bool:
        """Offer to switch the game's file check off, if that is what blocked it.

        Returns True when it has been dealt with - carried out and saved again,
        the panel opened, or the offer declined. In each of those the person has
        already been told what happened and does not need a second box about it.
        """
        blocked = self.manager.blocked_packages()
        if not blocked or report.validation is None:
            return False
        if not any(
            "still checks" in error
            for result in report.validation.failed
            for error in result.errors
        ):
            return False
        one = len(blocked) == 1
        it = "it" if one else "them"
        files = ", ".join(blocked)
        if not self.manager.state.settings.auto_switch_file_check_off:
            answer = QMessageBox.question(
                self,
                "The game still checks these files",
                f"Nothing was applied. The game keeps a checksum for {files}, "
                f"and a mod you have enabled replaces {it} - so the game would "
                "refuse the replacement at startup, before the intro.\n\n"
                f"The Hash Patcher can take {it} off the game's list, which is "
                "one byte per file and no program code. Your executable is "
                "copied first, and it can be put back at any time.\n\n"
                "Switch the check off and save again?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Open,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Open:
                self.open_file_check()  # let them look before deciding
                return True
            if answer != QMessageBox.StandardButton.Yes:
                return True
        try:
            done = self.manager.switch_file_check_off(blocked)
        except (RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Hash Patcher", str(exc))
            return True
        if not done:
            return False  # nothing changed, so what blocked it was something else
        self.statusBar().showMessage(
            f"The game no longer checks {len(done)} file(s) - saving again.", 8000
        )
        self.save_mod_list()
        return True

    def validate(self) -> None:
        if not self._check_database():
            return
        self._run(self.manager.validate, self._after_validate)

    def _after_validate(self, report) -> None:
        if report is None:
            return
        self.manager.log.info(report.summary_line())
        lines = [report.summary_line()]
        for result in report.results:
            lines += [f"FAILED {result.mod_id}: {message}" for message in result.errors]
            lines += [f"warn   {result.mod_id}: {message}" for message in result.warnings]
        lines += [f"warn   {c.message()}" for c in report.conflicts.conflicts]
        lines += [f"warn   {r.message()}" for r in report.conflicts.missing_requirements]

        box = QMessageBox(self)
        box.setWindowTitle("Validation")
        box.setIcon(QMessageBox.Icon.Warning if not report.ok else QMessageBox.Icon.Information)
        box.setText(report.summary_line())
        if len(lines) > 1:
            box.setDetailedText("\n".join(lines[1:]))
        box.exec()

    def revert_selected(self) -> None:
        if not self._check_database():
            return
        mod_ids = self.mod_list.selected_mod_ids()
        if not mod_ids:
            QMessageBox.information(self, "Revert", "Select one or more mods in the list first.")
            return
        confirmed = QMessageBox.question(
            self,
            "Revert mods",
            "Put the original rows back for:\n\n  " + "\n  ".join(mod_ids) + "\n\n"
            "The mods will also be disabled.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._run(lambda: self.manager.revert(mod_ids), self._after_revert)

    def _after_revert(self, results) -> None:
        if not results:
            return
        failures = [r for r in results if not r.ok]
        if failures:
            QMessageBox.warning(
                self,
                "Revert",
                "\n".join(f"{r.mod_id}: {r.error}" for r in failures),
            )
        else:
            self.statusBar().showMessage(
                f"Reverted {len(results)} mod(s)", 8000
            )

    # -- modpacks ----------------------------------------------------------

    def save_modpack(self) -> None:
        name, ok = QInputDialog.getText(self, "Save modpack", "Name:")
        if not ok or not name.strip():
            return
        self.manager.save_modpack(name.strip())
        self._refresh_modpacks()
        self.modpack_box.setCurrentText(name.strip())

    def load_modpack(self) -> None:
        name = self.modpack_box.currentText()
        if not name:
            return
        self.manager.load_modpack(name)
        self.unsaved = True
        self.refresh()

    def delete_modpack(self) -> None:
        name = self.modpack_box.currentText()
        if not name:
            return
        self.manager.delete_modpack(name)
        self._refresh_modpacks()

    # -- backups -----------------------------------------------------------

    def restore_backup(self) -> None:
        if not self._check_database():
            return
        backups = self.manager.backups()
        if not backups:
            QMessageBox.information(
                self,
                "Restore a backup",
                "No backups yet - one is taken on every Save Mod List.\n\n"
                f"Looked in:\n  {self.manager.paths.backups_dir}\n"
                f"  {self.manager.db_path.parent if self.manager.db_path else ''} "
                f"(for {DB_FILENAME}{BACKUP_SUFFIX})",
            )
            return
        labels = [entry.label() for entry in backups]
        label, ok = QInputDialog.getItem(
            self, "Restore a backup", "Overwrite the database with:", labels, 0, False
        )
        if not ok:
            return
        entry = backups[labels.index(label)]
        confirmed = QMessageBox.question(
            self,
            "Restore a backup",
            f"Replace\n  {self.manager.db_path}\nwith\n  {entry.path}?\n\n"
            "Every applied mod will be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.manager.restore_backup(entry)
        self._sync_watcher()
        self.refresh()

    def set_game_folder(self) -> None:
        """Point asset_file mods at the folder that holds BrgGame."""
        current = self.manager.asset_game_root
        start = str(current or (self.manager.db_path.parent if self.manager.db_path else ""))
        path = QFileDialog.getExistingDirectory(
            self, "Choose the LET IT DIE folder (the one containing BrgGame)", start
        )
        if not path:
            return
        if not (Path(path) / "BrgGame" / "CookedPCConsole").is_dir():
            QMessageBox.warning(
                self,
                "Set game folder",
                f"{path}\n\ndoes not look like the game folder - it has no "
                "BrgGame\\CookedPCConsole inside it.",
            )
            return
        self.manager.set_game_root_override(path)
        self.refresh()
        QMessageBox.information(self, "Set game folder", f"Game folder set to:\n  {path}")

    def open_file_check(self) -> None:
        """Switch the game's own file check off for chosen packages."""
        from .file_check_dialog import FileCheckDialog

        FileCheckDialog(self.manager, self).exec()
        self.refresh()

    def restore_game_files(self) -> None:
        entries = self.manager.asset_backups()
        if not entries:
            QMessageBox.information(
                self,
                "Restore game files",
                "No game files have been changed by a mod, so there is nothing to restore.",
            )
            return
        confirmed = QMessageBox.question(
            self,
            "Restore game files",
            f"Put back {len(entries)} game file(s) that mods have changed?\n\n"
            "Files a mod added will be deleted; files a mod replaced will be restored "
            "from the copy taken before any mod touched them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            report = self.manager.restore_all_asset_backups()
        except RuntimeError as exc:
            QMessageBox.warning(self, "Restore game files", str(exc))
            return
        self.refresh()
        self.statusBar().showMessage(report.summary_line(), 8000)

    # -- installing mods ---------------------------------------------------

    ACCEPTED_SUFFIXES = (".sql", ".zip", ".db", ".sqlite", ".sqlite3")

    def _droppable(self, path: Path) -> bool:
        return path.is_dir() or path.suffix.lower() in self.ACCEPTED_SUFFIXES

    def dragEnterEvent(self, event) -> None:
        data = event.mimeData()
        if not data.hasUrls():
            return
        paths = [Path(u.toLocalFile()) for u in data.urls() if u.isLocalFile()]
        if any(self._droppable(p) for p in paths):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        event.acceptProposedAction()
        # Get out of the drop handler before opening a modal dialog - Qt is
        # still holding the drag when this returns.
        self._install_queue.extend(p for p in paths if self._droppable(p))
        QTimer.singleShot(0, self._drain_install_queue)

    def add_mod_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add a mod",
            "",
            "Mods (*.sql *.zip *.db *.sqlite *.sqlite3);;All files (*)",
        )
        if path:
            self._install_queue.append(Path(path))
            self._drain_install_queue()

    def add_mod_from_folder(self) -> None:
        """A mod folder, or a folder of game files (.upk) / a content pack."""
        path = QFileDialog.getExistingDirectory(self, "Choose a mod or content-pack folder", "")
        if path:
            self._install_queue.append(Path(path))
            self._drain_install_queue()

    def build_mod(self) -> None:
        """Tools entry: make a mod by changing values, with no SQL involved."""
        if not self.manager.vanilla_path:
            QMessageBox.information(
                self,
                "No vanilla copy yet",
                "Building a mod means changing values in a clean copy of the "
                "database, and there is not one yet.\n\n"
                f"Click Save Mod List once and the manager keeps {DB_FILENAME}"
                f"{ORIGINAL_SUFFIX} next to your database - that is what the "
                "editor starts from.",
            )
            return
        from .builder_window import BuilderWindow

        window = BuilderWindow(self.manager, dark=self.dark, parent=self)
        if window.exec():
            self.refresh(rescan=True)

    def set_icon_folder(self) -> None:
        """Point the builder at item artwork the player already has.

        None ships with the program: the game's artwork belongs to its owners,
        not to us, so it is never redistributed here. A player who has a copy
        can point at it and the builder will use it.
        """
        from ..icons import IconSource

        current = self.manager.state.settings.icon_folder
        path = QFileDialog.getExistingDirectory(
            self, "Pick a folder of item artwork", current or ""
        )
        if not path:
            return
        source = IconSource(path)
        if not source.available:
            QMessageBox.warning(
                self, "Nothing usable in there",
                "That folder holds no pictures this can match to the game's "
                "items.\n\nIt expects either an icon_map.json keyed by the "
                "game's own ids, or files named after what they show - "
                "battle_machete.png, or pt_arm_wp001_002.png.",
            )
            return
        self.manager.state.settings.icon_folder = path
        self.manager.state.save()
        QMessageBox.information(
            self, "Artwork found",
            f"Using the artwork in {path}.\n\nIt is read from there and never "
            "copied into your mods or this program.",
        )

    def import_database(self) -> None:
        """Tools entry: turn somebody else's modded masters.db into a mod."""
        if not self.manager.vanilla_path:
            QMessageBox.information(
                self,
                "No vanilla copy yet",
                "Comparing a modded database needs an untouched copy to compare "
                "against.\n\n"
                f"Click Save Mod List once and the manager keeps {DB_FILENAME}"
                f"{ORIGINAL_SUFFIX} next to your database - that becomes the reference.",
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the modded masters.db", "", f"{DB_FILENAME} (*.db);;All files (*)"
        )
        if path:
            self._install_queue.append(Path(path))
            self._drain_install_queue()

    def edit_selected_mod(self) -> None:
        """Rename a mod, fix its description, write its readme - all in here."""
        selected = self.mod_list.selected_mod_ids()
        if not selected:
            QMessageBox.information(
                self, "Edit mod details", "Pick a mod in the list first."
            )
            return
        mod = self.manager.scan.get(selected[0])
        if mod is None:
            return

        dialog = EditModDialog(
            mod, self.dark, self, known_categories=self.manager.categories()
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        details = dialog.details()
        try:
            updated = self.manager.edit_mod(
                mod.id,
                details.name,
                description=details.description,
                author=details.author,
                version=details.version,
                readme=details.readme,
                category=details.category,
                tags=details.tags,
            )
        except ModEditError as exc:
            QMessageBox.warning(self, "Could not save the changes", str(exc))
            return
        except Exception as exc:  # pragma: no cover - unexpected, still must not crash
            QMessageBox.critical(self, "Could not save the changes", str(exc))
            return

        self.refresh()
        if updated is not None:
            self.mod_list.select_mods([updated.id])
            self.diff_view.show_mod(updated.id)
            self.statusBar().showMessage(f"Saved {updated.name}", 6000)

    def _drain_install_queue(self) -> None:
        if not self._install_queue or (self.task is not None and self.task.isRunning()):
            return
        source = self._install_queue.pop(0)
        # Inspecting a database means diffing it, and a large .sql file has to
        # be read through - either one takes long enough that with nothing on
        # screen it looks as though the window has died.
        self._run(
            lambda: self.manager.inspect_install(source),
            self._after_inspect,
            message=f"Looking at {source.name}...",
        )

    def _after_inspect(self, candidate) -> None:
        if candidate is None:
            self._install_queue.clear()
            return
        asset_mods = [(m.id, m.name) for m in self.manager.mods if m.asset_targets()]
        dialog = InstallDialog(candidate, self.dark, self, asset_mods=asset_mods)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            QTimer.singleShot(0, self._drain_install_queue)
            return
        details = dialog.details()

        def do_install(overwrite: bool) -> list:
            if candidate.delta is not None:
                return self.manager.install_database(
                    candidate,
                    details.name,
                    description=details.description,
                    author=details.author,
                    version=details.version,
                    overwrite=overwrite,
                    selection=details.selection,
                    split_by_table=details.split_by_table,
                    requires=[details.companion_of] if details.companion_of else None,
                )
            mod = self.manager.install(
                candidate,
                details.name,
                description=details.description,
                author=details.author,
                version=details.version,
                overwrite=overwrite,
            )
            return [mod] if mod is not None else []

        try:
            mods = do_install(False)
        except InstallError as exc:
            if "already exists" in str(exc):
                replace = QMessageBox.question(
                    self,
                    "Already installed",
                    f"{exc}\n\nReplace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if replace == QMessageBox.StandardButton.Yes:
                    mods = do_install(True)
                else:
                    QTimer.singleShot(0, self._drain_install_queue)
                    return
            else:
                QMessageBox.warning(self, "Could not add the mod", str(exc))
                QTimer.singleShot(0, self._drain_install_queue)
                return
        except Exception as exc:  # pragma: no cover - unexpected, still must not crash
            QMessageBox.critical(self, "Could not add the mod", str(exc))
            QTimer.singleShot(0, self._drain_install_queue)
            return

        # Everything after this reads the mod that was just added, and a
        # shared dump of whole tables is tens of megabytes of SQL to split and
        # scan - long enough that the window stops painting and Windows greys
        # it out, which reads as a crash. The reading happens on the worker
        # thread, behind a progress window; what it works out is kept on the
        # patch, so the refresh that follows is instant.
        self._run(
            lambda: [mod.targets() for mod in mods],
            lambda _warmed: self._after_install(mods, candidate),
            message=f"Reading {candidate.source.name}...",
        )

    def _after_install(self, mods, candidate) -> None:
        """Show what was added. Runs once the mod has been read through."""
        self.refresh()
        if mods:
            self.mod_list.select_mods([mod.id for mod in mods])
            self.diff_view.show_mod(mods[0].id)
            if len(mods) == 1:
                message = (
                    f"Added {mods[0].name} - it is switched off; tick it when you "
                    "have looked at the diff."
                )
            else:
                message = (
                    f"Added {len(mods)} mods from {candidate.source.name}, one per "
                    "table - all switched off, so you can enable just the parts "
                    "you want."
                )
            self.statusBar().showMessage(message, 10000)

        if candidate.kind == KIND_VETTED_MOD and candidate.recipe is not None:
            recipe = candidate.recipe
            QMessageBox.information(
                self,
                "This mod changes the game executable",
                f"{recipe.summary} went in as one mod.\n\n"
                f"It replaces {recipe.package}, and the game keeps a checksum for "
                "that file inside BrgGame-Steam.exe - so the replacement is refused "
                "unless that one value is updated too. This mod does both.\n\n"
                "What changes in the executable is twenty bytes in a table of file "
                "checksums. No program code is changed, and the manager checks that "
                "before and after: the code section has to hash to the same value "
                "or nothing is written.\n\n"
                "The manager will not do this for any mod that asks. It only carries "
                "out changes recorded in its own recipes folder, which are added by "
                "hand. Yours is:\n"
                f"    {recipe.name}\n\n"
                "The executable is backed up first, like any game file, and unticking "
                "this mod puts it back byte for byte.",
            )
        elif candidate.kind == KIND_CONTENT_PACK and candidate.pack is not None:
            recipe = candidate.pack.recipe
            QMessageBox.information(
                self,
                "Content pack added",
                f"{candidate.pack.suggested_name} went in as one mod, holding both "
                "halves: the artwork, and the database changes that make it "
                "reachable in game.\n\n"
                f"That is {recipe.summary}, and "
                f"{candidate.pack.asset_count} artwork packages.\n\n"
                "It is switched off. Tick it and Save Mod List when you are ready - "
                "it layers with your other mods and unticking it puts everything "
                "back.\n\n"
                "You do not need to run the pack's own installer as well. Doing "
                "both would not break anything, but the manager rebuilds "
                "masters.db from your mod list, so its changes would be replaced "
                "by these next time you save.",
            )
        elif candidate.kind == KIND_ASSET_FOLDER and (
            (candidate.source / "catalog.json").is_file()
            or (candidate.source / "installer.py").is_file()
        ):
            # A content pack we could not match to a recorded recipe, or someone
            # else's pack entirely. The artwork is in; the database half still
            # has to go the long way round.
            why = candidate.warnings[0] if candidate.warnings else (
                "This pack also changes masters.db (items, quests, drop pools). "
                "That part is done by the pack's own installer, which this "
                "manager does not have a recorded copy of."
            )
            QMessageBox.information(
                self,
                "This pack also changes the database",
                f"The game files have been added as a mod.\n\n{why}\n\n"
                "First: tick the files mod you just added and Save Mod List - "
                "before running the pack's installer, so the manager keeps track "
                "of the model files.\n\n"
                "Then, with the game closed:\n"
                "  1. Untick your other database mods and save, so masters.db is vanilla.\n"
                "  2. Close this manager and copy masters.db somewhere safe.\n"
                "  3. Run the pack's installer on your game folder.\n"
                "  4. Copy the edited masters.db out, then put the vanilla one back.\n"
                "  5. Reopen the manager and drag the edited copy onto this window.\n"
                "     Pick this files mod as its companion.\n\n"
                "The README has the full step-by-step under \"The Crossover "
                "Content pack\".",
            )
        QTimer.singleShot(0, self._drain_install_queue)


    # -- settings ----------------------------------------------------------

    def _on_dark_toggled(self, dark: bool) -> None:
        self.dark = dark
        self.manager.state.settings.dark_mode = dark
        self.manager.state.save()
        apply_theme(QApplication.instance(), dark, self.manager.state.settings.text_scale)
        self.mod_list.dark = dark
        self.diff_view.dark = dark
        self.log_panel.set_dark(dark)
        self.refresh()

    def _on_watchdog_toggled(self, enabled: bool) -> None:
        self.manager.state.settings.watchdog_enabled = enabled
        self.manager.state.save()
        if enabled and not self.watch.isRunning():
            self._sync_watcher()
            self.watch.start()
        elif not enabled and self.watch.isRunning():
            self.watch.stop()
        self.manager.log.info(f"Watchdog {'on' if enabled else 'off'}")

    def _on_auto_toggled(self, enabled: bool) -> None:
        self.manager.state.settings.auto_reapply = enabled
        self.manager.state.save()

    def _on_auto_check_off_toggled(self, enabled: bool) -> None:
        self.manager.state.settings.auto_switch_file_check_off = enabled
        self.manager.state.save()
        self.manager.log.info(
            f"Auto hash patch {'on' if enabled else 'off'}"
        )

    # -- watchdog ----------------------------------------------------------

    def _on_db_status(self, status: DbStatus) -> None:
        self._set_status(status)
        if not status.changed:
            self.mod_list.refresh()
            return
        self.manager.log.warn(status.label)
        self.mod_list.refresh()
        if not self.manager.state.settings.auto_reapply:
            self.statusBar().showMessage(
                "The database changed - click Re-apply All to put your mods back.", 0
            )
            return
        if not self.manager.state.enabled_mods:
            return
        if self.task is not None and self.task.isRunning():
            return
        self.manager.log.info("Auto re-apply triggered by the watchdog")
        self.reapply_all()

    # -- first run ---------------------------------------------------------

    def _first_run_checks(self) -> None:
        if not self.manager.db_path or not self.manager.db_path.is_file():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle(f"Point me at your {DB_FILENAME}")
            box.setText(f"{APP_NAME} needs to know where {DB_FILENAME} lives.")
            hint, found = self._database_hint()
            where = "Found your game here:" if found else "It is usually under:"
            builds = self.manager.vanilla_builds()
            if builds:
                # Clean copies ship with the manager, so the file being picked
                # no longer has to be a clean one - that is the whole point of
                # the scan that follows.
                box.setInformativeText(
                    "<b>Any copy will do — modded or not.</b><br><br>"
                    "This build carries clean copies of the game's database "
                    f"({len(builds)} of them), so yours is compared against the one "
                    "matching your game build. If it turns out to have mods in it "
                    "already, you are shown what they are and asked what to keep.<br><br>"
                    f"{where}<br><code>{hint}</code>"
                )
            else:
                box.setInformativeText(
                    "<b>Use a clean, unmodified copy.</b><br><br>"
                    "The moment you pick it, this tool keeps a copy as "
                    f"<code>{DB_FILENAME}{ORIGINAL_SUFFIX}</code> and never overwrites it "
                    "again — that is your permanent way back to vanilla. If the file "
                    "has already been edited by hand or by another tool, that "
                    "\"original\" is a copy of the edited version, and no amount of "
                    "reverting will get you back to stock.<br><br>"
                    "If you are not sure yours is clean, replace it <i>before</i> pointing "
                    "this tool at it: delete it and let Steam re-download it "
                    "(Properties &gt; Installed Files &gt; Verify integrity of game files). "
                    "Doing that afterwards means downloading it all over again.<br><br>"
                    f"{where}<br><code>{hint}</code>"
                )
            box.exec()
            self.choose_database()
        self._offer_to_revert_deleted_mods()
        self._validate_quietly()
        self._offer_clean_copy()
        self._offer_adoption()

    def _offer_clean_copy(self) -> None:
        """No clean copy for this build: ask whether this file is one.

        After a game update the manager normally keeps the game's own database
        as the clean copy for the new build by itself, because Steam had just
        written it and nothing had touched it since. When it cannot tell, the
        player can: they know whether they have modded this file yet. Asked
        once per build - saying no is remembered.
        """
        manager = self.manager
        if not manager.db_path or not manager.db_path.is_file():
            return
        if manager.chosen_vanilla() is not None:
            return
        version = vanilla_library.database_version(manager.db_path)
        if not version or version in manager.state.clean_copy_asked:
            return
        result = manager.capture_clean_copy()
        if result.ok:
            self.statusBar().showMessage(
                f"Kept your masters.db as the clean copy for build {result.label}", 8000
            )
            self.refresh()
            return

        manager.state.clean_copy_asked.append(version)
        manager.state.save()
        detail = f"\n\nWhat it found: {result.reason}." if result.reason else ""
        answer = QMessageBox.question(
            self,
            "A game build I have no clean copy of",
            f"Your database says it is game build {version}, and no clean copy of that "
            "build ships with the manager. Without one, nothing can be compared: which "
            "mods are already in your file, and what a mod actually changes, both need "
            f"an untouched copy of the same build.{detail}\n\n"
            "Is this file a fresh, unmodded masters.db — the one the game update just "
            "put there, with no mods applied to it yet?\n\n"
            "Yes  - keep it as the clean copy for this build.\n"
            "No   - leave it alone. Nothing is compared until a clean copy turns up.\n\n"
            "If you are not sure, say No. Steam's Verify integrity of game files puts "
            "an untouched copy back, and then this can be answered with Yes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        kept = manager.keep_as_clean_copy()
        if not kept.ok:
            QMessageBox.warning(self, "Not kept", kept.reason)
            return
        QMessageBox.information(
            self,
            "Kept",
            f"Kept as the clean copy for build {kept.label}, in\n{kept.kept.parent}\n\n"
            "If it turns out to have mods in it, delete that folder and everything goes "
            "back to how it was.",
        )
        self.refresh()

    def _offer_adoption(self) -> None:
        """First run: if their database is already modded, say so and offer to
        take it over. Asked once - after that it lives in the Tools menu."""
        if self.manager.state.adoption_offered:
            return
        if not self.manager.db_path or not self.manager.db_path.is_file():
            return
        if self.manager.state.applied:
            # Someone already using the manager: their database is this tool's
            # own work, and offering to "take it over" would be asking them
            # about mods they applied themselves five minutes ago.
            self.manager.state.adoption_offered = True
            self.manager.state.save()
            return
        if self.manager.chosen_vanilla() is None:
            # Nothing safe to compare against; leave them to the old way rather
            # than nagging about a build we do not ship.
            return
        self.scan_for_existing_mods()

    # -- adopting a database that was modded before the manager saw it -----

    def choose_vanilla(self) -> None:
        """Pick which clean database everything is measured against."""
        builds = self.manager.vanilla_builds()
        if not builds:
            QMessageBox.information(
                self,
                "No clean database",
                "No clean copy of masters.db ships with this build, and none was found "
                f"in {self.manager.paths.vanilla_dir}.\n\n"
                "Put one in a folder named after its game build, like\n"
                f"{self.manager.paths.vanilla_dir / '5.0.3.0' / DB_FILENAME}",
            )
            return
        automatic = "Whichever matches my database"
        labels = [automatic] + [build.description for build in builds]
        current = self.manager.state.settings.vanilla_choice
        index = 0
        for position, build in enumerate(builds, start=1):
            if current and current in (build.version, build.label):
                index = position
        picked, ok = QInputDialog.getItem(
            self, "Clean database", "Compare my database against:", labels, index, False
        )
        if not ok:
            return
        if picked == automatic:
            self.manager.set_vanilla_choice("")
        else:
            build = builds[labels.index(picked) - 1]
            self.manager.set_vanilla_choice(build.version or build.label)
        self.refresh()

    def scan_for_existing_mods(self) -> None:
        """Read the chosen database and say which mods are already in it."""
        if not self._check_database():
            return
        if self.manager.chosen_vanilla() is None:
            QMessageBox.information(
                self,
                "Nothing to compare against",
                "Working out which mods are already in your database means comparing it "
                "with a clean copy of the same game build, and none of the copies "
                "available matches it.\n\n"
                "Tools > Clean database to compare against... lists what there is.",
            )
            return
        self._run(
            self.manager.adopt_scan,
            self._after_adopt_scan,
            message=(
                "Reading your database and comparing it with the clean copy, to see "
                "which mods are already in it.\n\n"
                "This takes a few seconds on a real masters.db. The program has not "
                "frozen - nothing is being written yet."
            ),
        )

    def _after_adopt_scan(self, report) -> None:
        if report is None:
            return
        self.manager.state.adoption_offered = True
        self.manager.state.save()
        if not report.ok:
            QMessageBox.warning(self, "Could not read your database", report.error)
            return
        if report.total.empty:
            QMessageBox.information(
                self,
                "Nothing has been modded",
                "Your database matches the clean copy exactly, so there is nothing to "
                "take over - tick the mods you want and click Save Mod List.",
            )
            return

        dialog = AdoptDialog(report, self.dark, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(
                "Left your database alone. Tools > Scan my database for mods "
                "already in it... runs this again.",
                12000,
            )
            return
        choice = dialog.choice()

        mod_ids = list(choice.mod_ids)
        if choice.make_custom and report.has_leftover:
            try:
                mod_ids += self._keep_leftover_as_mod(report, choice)
            except InstallError as exc:
                QMessageBox.warning(self, "Could not keep those changes", str(exc))
                return
            except Exception as exc:  # pragma: no cover - unexpected, must not crash
                QMessageBox.critical(self, "Could not keep those changes", str(exc))
                return

        self._run(
            lambda: self.manager.adopt_rebuild(mod_ids, choice.values),
            self._after_apply,
            message=(
                "Rebuilding your database from the clean copy and applying the mods "
                "you kept, including any game files they install.\n\n"
                "This can take a minute. The program has not frozen - please do not "
                "close it until it finishes."
            ),
        )

    def _keep_leftover_as_mod(self, report, choice) -> list[str]:
        """Turn the changes no mod accounts for into an ordinary mod.

        Straight through the same path as a dropped modded database, so what
        comes out is a normal mod folder with a switch per table.
        """
        candidate = InstallCandidate(
            source=self.manager.db_path,
            kind=KIND_DATABASE,
            suggested_name=choice.custom_name,
            note=report.leftover.summary(),
            delta=report.leftover,
        )
        mods = self.manager.install_database(
            candidate,
            choice.custom_name,
            description=(
                "Changes found in your own masters.db that matched no installed mod, "
                "kept so they can be switched off like any other."
            ),
            author="",
            version="1.0.0",
            selection=choice.selection,
            split_by_table=False,
        )
        return [mod.id for mod in mods]

    def _validate_quietly(self) -> None:
        """Populate per-mod warnings on startup, without a dialog.

        Conflicts are worked out from the mod files alone, so they show up
        immediately - but anything that needs the database (missing columns,
        row-count drift, absent text) only appears once validation has run. Do
        it up front so the list is honest before the user touches anything.
        """
        if not self.manager.state.enabled_mods:
            return
        if not self.manager.db_path or not self.manager.db_path.is_file():
            return
        self.manager.validate()
        self.mod_list.refresh()
        self.diff_view.refresh()

    def _offer_to_revert_deleted_mods(self) -> None:
        deleted = self.manager.deleted_mods()
        if not deleted or not self.manager.db_path:
            return
        confirmed = QMessageBox.question(
            self,
            "Mods were removed",
            "These mods were applied but their folders are gone:\n\n  "
            + "\n  ".join(deleted)
            + "\n\nPut the rows they changed back?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirmed == QMessageBox.StandardButton.Yes:
            self._run(lambda: self.manager.revert(deleted), self._after_revert)
        else:
            self.manager.forget(deleted)
            self.refresh()

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            f"Applies JSON- and SQL-defined mods to {DB_FILENAME}, snapshots the "
            "rows it changes so anything can be undone, and re-applies your list "
            "when the game replaces the database.<br><br>"
            f"Mods: <code>{self.manager.paths.mods_dir}</code><br>"
            f"Logs: <code>{self.manager.paths.logs_dir}</code><br>"
            f"Backups: <code>{self.manager.paths.backups_dir}</code><br><br>"
            "Offline tool. It makes no network calls.",
        )

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.watch.stop()
        self.watch.wait(2000)
        if self.task is not None and self.task.isRunning():
            self.task.wait(5000)
        self.manager.state.save()
        super().closeEvent(event)


def _open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - opening a local folder in Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
