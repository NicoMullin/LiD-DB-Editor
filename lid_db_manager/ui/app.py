"""Boot the GUI."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from .. import APP_NAME, __version__, self_update
from ..manager import Manager
from ..paths import AppPaths
from ..progress import Progress
from .main_window import MainWindow
from .theme import apply_theme


def run(paths: AppPaths | None = None, argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("lid-db-mod-manager")

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [self_update.FINISH_FLAG]:
        return finish_update(args[1:])

    manager = Manager(paths)
    apply_theme(app, manager.state.settings.dark_mode, manager.state.settings.text_scale)

    window = MainWindow(manager)
    window.show()
    if args[:1] == [self_update.DONE_FLAG]:
        window.updated_from(args[1] if len(args) > 1 else "")
    if getattr(sys, "frozen", False):
        # Whatever the last update left behind: the download, the unpacked copy
        # (once the copy that finished the update has closed) and the old files.
        finisher = 0
        if args[:1] == [self_update.DONE_FLAG] and len(args) > 2 and args[2].isdigit():
            finisher = int(args[2])
        folder = self_update.program_dir()
        threading.Thread(
            target=lambda: (self_update.wait_for_exit(finisher, 30),
                            self_update.clean_up(folder)),
            daemon=True,
        ).start()
    return app.exec()


def finish_update(args: list[str]) -> int:
    """Run by the NEW copy, from where it was unpacked: wait for the old one to
    close, put this copy's program files in its place, and start it."""
    if len(args) < 2 or not args[1].isdigit():
        return 2
    target, old_pid = Path(args[0]), int(args[1])
    from_version = args[2] if len(args) > 2 else ""
    here, exe = self_update.program_dir(), self_update.exe_name()

    dialog = QProgressDialog(f"Updating {APP_NAME} to {__version__}...", "", 0, 100)
    dialog.setWindowTitle(APP_NAME)
    dialog.setCancelButton(None)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.show()
    QCoreApplication.processEvents()

    while not self_update.wait_for_exit(old_pid, 30):
        again = QMessageBox.question(
            dialog, APP_NAME,
            f"The old version of {APP_NAME} is still open. Close it, then click Retry.",
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
        )
        if again != QMessageBox.StandardButton.Retry:
            return 1

    def report(text: str, value: int) -> None:
        dialog.setLabelText(text)
        dialog.setValue(value)
        QCoreApplication.processEvents()

    try:
        self_update.install(here, target, Progress(report))
    except self_update.UpdateError as exc:
        dialog.close()
        QMessageBox.critical(None, APP_NAME, str(exc))
        # Whatever is in the folder now - the old version, put back - still runs.
        if (target / exe).is_file():
            self_update.restart(target, exe, [])
        return 1
    dialog.close()
    self_update.restart(
        target, exe, [self_update.DONE_FLAG, from_version, str(QCoreApplication.applicationPid())]
    )
    return 0
