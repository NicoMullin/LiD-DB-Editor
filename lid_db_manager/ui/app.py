"""Boot the GUI."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .. import APP_NAME, __version__
from ..manager import Manager
from ..paths import AppPaths
from .main_window import MainWindow
from .theme import apply_theme


def run(paths: AppPaths | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("lid-db-mod-manager")

    manager = Manager(paths)
    apply_theme(app, manager.state.settings.dark_mode)

    window = MainWindow(manager)
    window.show()
    return app.exec()
