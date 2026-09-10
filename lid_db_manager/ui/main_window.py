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
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, DB_FILENAME, DEFAULT_DB_HINT, __version__
from .. import backup as backup_module
from ..backup import BACKUP_SUFFIX, ORIGINAL_SUFFIX
from ..install import InstallError
from ..manager import Manager
from ..watchdog import STATUS_OK, STATUS_STALE, DbStatus
from .diff_view import DiffView
from .install_dialog import InstallDialog
from .log_panel import LogPanel
from .mod_list import ModListWidget
from .theme import apply_theme, colors
from .workers import TaskThread, WatchThread


class MainWindow(QMainWindow):
    def __init__(self, manager: Manager):
        super().__init__()
        self.manager = manager
        self.dark = manager.state.settings.dark_mode
        self.task: TaskThread | None = None
        self.unsaved = False

        self.setWindowTitle(APP_NAME)
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
        QTimer.singleShot(0, self._first_run_checks)

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
            f"Poll {DB_FILENAME} and notice when the game replaces it."
        )
        self.watchdog_box.setChecked(self.manager.state.settings.watchdog_enabled)
        self.watchdog_box.toggled.connect(self._on_watchdog_toggled)

        self.auto_box = QCheckBox("Auto re-apply")
        self.auto_box.setToolTip("Re-apply the enabled mods as soon as a change is detected.")
        self.auto_box.setChecked(self.manager.state.settings.auto_reapply)
        self.auto_box.toggled.connect(self._on_auto_toggled)

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
        bottom.addWidget(self.reapply_button)

        self.header = QWidget()
        layout = QVBoxLayout(self.header)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addLayout(top)
        layout.addLayout(bottom)

    def _build_body(self) -> None:
        self.mod_list = ModListWidget(self.manager, self.dark)
        self.mod_list.enabledChanged.connect(self._on_mod_toggled)
        self.mod_list.togglesApplied.connect(self._after_mods_toggled)
        self.mod_list.selectionChangedTo.connect(self._on_mod_selected)

        self.diff_view = DiffView(self.manager, self.dark)
        self.log_panel = LogPanel(self.manager.log, self.dark)

        # The list plus its load-order controls, as one panel.
        self.move_up_button = QPushButton("Move up")
        self.move_up_button.setToolTip("Apply this mod earlier, so later mods can overwrite it")
        self.move_up_button.clicked.connect(lambda: self.move_selected(-1))
        self.move_down_button = QPushButton("Move down")
        self.move_down_button.setToolTip("Apply this mod later, so it overwrites the ones above")
        self.move_down_button.clicked.connect(lambda: self.move_selected(+1))

        order_hint = QLabel("Load order: top applies first, bottom wins")
        order_hint.setObjectName("dim")

        order_row = QHBoxLayout()
        order_row.addWidget(self.move_up_button)
        order_row.addWidget(self.move_down_button)
        order_row.addWidget(order_hint, 1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
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

        tools = self.menuBar().addMenu("&Tools")
        self._add_action(tools, "Validate enabled mods", self.validate, "Ctrl+T")
        self._add_action(tools, "Save Mod List (apply)", self.save_mod_list, "Ctrl+S")
        self._add_action(tools, "Re-apply All", self.reapply_all, "Ctrl+R")
        tools.addSeparator()
        self._add_action(tools, "Enable all mods", lambda: self.mod_list.set_all_checked(True))
        self._add_action(tools, "Disable all mods", lambda: self.mod_list.set_all_checked(False))
        tools.addSeparator()
        self._add_action(tools, "Move up the load order", lambda: self.move_selected(-1), "Ctrl+Up")
        self._add_action(tools, "Move down the load order", lambda: self.move_selected(+1), "Ctrl+Down")
        tools.addSeparator()
        self._add_action(
            tools, "Create a mod from a modded masters.db...", self.import_database
        )
        self._add_action(tools, "Add a mod from a file...", self.add_mod_from_file)
        tools.addSeparator()
        self._add_action(tools, "Restore a backup...", self.restore_backup)

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
        self.mod_list.refresh()
        self.diff_view.refresh()
        self._refresh_header()
        self._refresh_modpacks()

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
        self.setWindowTitle(f"{APP_NAME} - {enabled} mod(s) enabled{suffix}")

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

    def choose_database(self) -> None:
        start = str(self.manager.db_path or DEFAULT_DB_HINT)
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

    def _after_mods_toggled(self) -> None:
        self.manager.state.save()
        self.mod_list.refresh()
        self.diff_view.refresh()
        self._refresh_header()

    def _on_mod_selected(self, mod_id: str) -> None:
        self.diff_view.show_mod(mod_id)

    def move_selected(self, delta: int) -> None:
        """Shift the selected mods up or down the load order."""
        selected = self.mod_list.selected_mod_ids()
        enabled = [m for m in selected if self.manager.state.is_enabled(m)]
        if not enabled:
            self.statusBar().showMessage(
                "Select an enabled mod to move - the load order only covers enabled mods.", 6000
            )
            return
        # Moving down means starting from the bottom, or the mods trip over
        # each other on the way.
        order = self.manager.state.enabled_mods
        enabled.sort(key=order.index, reverse=delta > 0)
        if not any(self.manager.move_mod(mod_id, delta) for mod_id in enabled):
            return
        self.unsaved = True
        self.mod_list.refresh()
        self.mod_list.select_mods(selected)
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

    def _run(self, work, on_done) -> None:
        if self.task is not None and self.task.isRunning():
            return
        self._busy(True)
        self.task = TaskThread(work, self)
        self.task.succeeded.connect(lambda result: self._finish(on_done, result, ""))
        self.task.failed.connect(lambda trace: self._finish(on_done, None, trace))
        self.task.start()

    def _finish(self, on_done, result, trace: str) -> None:
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
        if not self._check_database():
            return
        self._run(self.manager.save_mod_list, self._after_apply)

    def reapply_all(self) -> None:
        if not self._check_database():
            return
        self._run(self.manager.reapply_all, self._after_apply)

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
        QMessageBox.warning(self, "Nothing was applied", message)

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

    def _drain_install_queue(self) -> None:
        if not self._install_queue or (self.task is not None and self.task.isRunning()):
            return
        source = self._install_queue.pop(0)
        # Inspecting a database means diffing it, which takes a second or two.
        self._run(lambda: self.manager.inspect_install(source), self._after_inspect)

    def _after_inspect(self, candidate) -> None:
        if candidate is None:
            self._install_queue.clear()
            return
        dialog = InstallDialog(candidate, self.dark, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            QTimer.singleShot(0, self._drain_install_queue)
            return
        details = dialog.details()
        try:
            mod = self.manager.install(
                candidate,
                details.name,
                description=details.description,
                author=details.author,
                version=details.version,
            )
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
                    mod = self.manager.install(
                        candidate,
                        details.name,
                        description=details.description,
                        author=details.author,
                        version=details.version,
                        overwrite=True,
                    )
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

        self.refresh()
        if mod is not None:
            self.mod_list.select_mods([mod.id])
            self.diff_view.show_mod(mod.id)
            self.statusBar().showMessage(
                f"Added {mod.name} - it is switched off; tick it when you have "
                "looked at the diff.",
                10000,
            )
        QTimer.singleShot(0, self._drain_install_queue)

    # -- settings ----------------------------------------------------------

    def _on_dark_toggled(self, dark: bool) -> None:
        self.dark = dark
        self.manager.state.settings.dark_mode = dark
        self.manager.state.save()
        apply_theme(QApplication.instance(), dark)
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
                f"It is usually under:<br><code>{DEFAULT_DB_HINT}</code>"
            )
            box.exec()
            self.choose_database()
        self._offer_to_revert_deleted_mods()
        self._validate_quietly()

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
