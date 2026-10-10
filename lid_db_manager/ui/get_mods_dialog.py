"""The "Get mods" window: KSFA's plugin mods, straight from their GitHub releases.

Mods that need a plugin DLL do not come with the manager. This lists them,
says which ones are here already, and gets one with a click: the release
package is downloaded from that mod's GitHub page, checked against the
SHA-256 GitHub lists for it, and - once the player has seen exactly what will
be written where and said yes - the mod goes in the mods folder and the plugin
in the game's lid-plugins folder, with the Plugin Loader first if it is
missing. See get_mods.py for the rules.
"""

from __future__ import annotations

import html
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import get_mods as G
from .workers import TaskThread

COLUMNS = ("Mod", "You have", "Newest", "")
MOD, HAVE, NEWEST, ACTION = range(len(COLUMNS))
WORK_DIR = ("_update", "get-mods")


class GetModsDialog(QDialog):
    def __init__(self, manager, parent=None, opener=None):
        super().__init__(parent)
        self.manager = manager
        self.opener = opener or G.self_update._open
        self.releases: dict[str, G.Release | G.GetModsError] = {}
        self.task: TaskThread | None = None
        self.changed = False            # a mod was added: the main window rescans
        self.setWindowTitle("Get mods")
        self.resize(940, 700)

        intro = QLabel(
            "These mods need a plugin - a DLL the game loads through the LiD Plugin Loader - so "
            "they do not come with the manager. Each is built and tested on its own GitHub page.<br>"
            "<b>Get</b> downloads its newest release, checks it against the SHA-256 GitHub lists "
            "for it, and after you say yes puts the mod here and the plugin in the game's "
            "<code>Binaries\\Win64\\lid-plugins</code> folder (with the Plugin Loader, if you do "
            "not have it yet). Close the game first."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        self.where = QLabel()
        self.where.setWordWrap(True)

        self.table = QTableWidget(len(G.CATALOG), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(MOD, QHeaderView.ResizeMode.Stretch)
        for column in (HAVE, NEWEST, ACTION):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.buttons: dict[str, QPushButton] = {}
        for row, entry in enumerate(G.CATALOG):
            label = QLabel(f"<b>{html.escape(entry.title)}</b> - {html.escape(entry.blurb)}<br>"
                           f"<a href='{entry.page}'>its GitHub page</a>")
            label.setWordWrap(True)
            label.setOpenExternalLinks(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setContentsMargins(4, 4, 4, 4)
            self.table.setCellWidget(row, MOD, label)
            self.table.setItem(row, HAVE, QTableWidgetItem())
            self.table.setItem(row, NEWEST, QTableWidgetItem("-"))
            button = QPushButton("Get")
            button.clicked.connect(lambda _checked=False, key=entry.key: self.get(key))
            self.buttons[entry.key] = button
            holder = QWidget()             # so the button keeps its own height
            centred = QHBoxLayout(holder)
            centred.setContentsMargins(6, 0, 6, 0)
            centred.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.table.setCellWidget(row, ACTION, holder)

        self.check_button = QPushButton("Check GitHub for the newest")
        self.check_button.clicked.connect(self.check)
        self.status = QLabel()
        self.status.setWordWrap(True)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(self.check_button)
        footer.addWidget(self.status, 1)
        footer.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.where)
        layout.addWidget(self.table, 1)
        layout.addLayout(footer)
        self.refresh()

    # -- what is here ----------------------------------------------------------

    @property
    def game_root(self):
        return self.manager.asset_game_root

    @property
    def mods_dir(self):
        return self.manager.paths.mods_dir

    def refresh(self) -> None:
        game = self.game_root
        if game is None:
            self.where.setText("<b>The game folder is not set</b> - mods without a plugin can still "
                               "be got; for the plugins set it first with Tools > Set game folder.")
        else:
            state = G.loader_state(game)
            loader = {"ours": "the Plugin Loader is installed",
                      "missing": "no Plugin Loader yet - it comes with the first plugin you get",
                      "old-fighter": "the old Fighter Mod dinput8.dll is there; the Plugin Loader "
                                     "replaces it with the first plugin you get",
                      "foreign": "a dinput8.dll that is not the Plugin Loader is there, so plugins "
                                 "cannot be added until it is moved out of the way"}[state]
            self.where.setText(f"Game folder: {html.escape(str(game))} - {loader}.")
        for row, entry in enumerate(G.CATALOG):
            have = G.installed(entry, self.mods_dir, game)
            parts = []
            if entry.is_loader:
                parts.append("installed" if have.plugin else "-")
            else:
                parts += [f"mod {v}" for v in have.mods.values() if v]
                if entry.plugin:
                    parts.append("plugin" if have.plugin else "")
            text = ", ".join(p for p in parts if p) or "-"
            self.table.item(row, HAVE).setText(text)
            release = self.releases.get(entry.key)
            if isinstance(release, G.Release):
                self.table.item(row, NEWEST).setText(release.version)
                self.table.item(row, NEWEST).setToolTip("")
            elif isinstance(release, G.GetModsError):
                self.table.item(row, NEWEST).setText("?")
                self.table.item(row, NEWEST).setToolTip(str(release))
            self.buttons[entry.key].setText("Update" if have.anything else "Get")
        self.table.resizeRowsToContents()

    # -- asking GitHub -------------------------------------------------------------

    def _busy(self, busy: bool, message: str = "") -> None:
        self.check_button.setEnabled(not busy)
        for button in self.buttons.values():
            button.setEnabled(not busy)
        self.status.setText(message)

    def _start(self, work, on_done, message: str) -> None:
        if self.task is not None and self.task.isRunning():
            return
        self._busy(True, message)
        self.task = TaskThread(work, self)
        self.task.succeeded.connect(lambda result: self._done(on_done, result, ""))
        self.task.failed.connect(lambda trace: self._done(on_done, None, trace))
        self.task.start()

    def _done(self, on_done, result, trace: str) -> None:
        self.task = None
        self._busy(False)
        if trace:
            last = trace.strip().splitlines()[-1]
            self.manager.log.error(f"Get mods: {last}")
            message = last.split(": ", 1)[-1] if "GetModsError" in last else trace
            QMessageBox.warning(self, "Get mods", message)
            self.refresh()
            return
        on_done(result)
        self.refresh()

    def check(self) -> None:
        def ask():
            found = {}
            for entry in G.CATALOG:
                try:
                    found[entry.key] = G.latest(entry, opener=self.opener)
                except G.GetModsError as exc:
                    found[entry.key] = exc
            return found

        def done(found):
            self.releases.update(found)
            problems = [r for r in found.values() if isinstance(r, G.GetModsError)]
            self.status.setText(f"{len(problems)} could not be checked - hover over the ?"
                                if problems else "Checked.")

        self._start(ask, done, "Asking GitHub...")

    # -- getting one -----------------------------------------------------------------

    def _work_dir(self):
        return self.manager.paths.root.joinpath(*WORK_DIR)

    def get(self, key: str) -> None:
        entry = G.offer(key)
        game = self.game_root
        if entry.plugin and game is None:
            QMessageBox.information(self, "Get mods",
                                    f"{entry.title} puts a plugin in the game folder. Set the game "
                                    "folder first, with Tools > Set game folder.")
            return
        if game is not None and entry.plugin and G.loader_state(game) == "foreign":
            QMessageBox.warning(self, "Get mods",
                                f"There is a dinput8.dll in {G.win64(game)} that is not the LiD Plugin "
                                "Loader, so plugins cannot be added. Move it out of the way first.")
            return
        work = self._work_dir()

        def fetch():
            shutil.rmtree(work, ignore_errors=True)
            wanted = [entry]
            if entry.plugin and G.loader_state(game) != "ours":
                wanted.insert(0, G.LOADER_OFFER)
            packages = []
            for item in wanted:
                release = self.releases.get(item.key)
                if not isinstance(release, G.Release):
                    release = G.latest(item, opener=self.opener)
                    self.releases[item.key] = release
                packed = G.download(release, work, opener=self.opener)
                packages.append(G.unpack(packed, work / item.key))
            return packages

        self._start(fetch, lambda packages: self._confirm(entry, packages),
                    f"Downloading {entry.title}...")

    def _confirm(self, entry: G.Offer, packages: list[G.Package]) -> None:
        game = self.game_root
        lines = []
        for package in packages:
            lines += G.what_it_writes(package, self.mods_dir, game)
        names = " and ".join(f"{p.name} {p.version}" for p in packages)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(f"Install {entry.title}?")
        box.setText(f"<b>{html.escape(names)}</b> downloaded and checked against GitHub's SHA-256.")
        box.setInformativeText(
            "It will write:<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"
            "Close the game first. A mod already here is replaced by the new copy; its settings "
            "are kept, and the next <b>Save Mod List</b> applies the new version."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Install")
        if box.exec() != QMessageBox.StandardButton.Yes:
            self.status.setText("Nothing was installed.")
            return

        def put():
            done = []
            for package in packages:
                done += G.install(package, self.mods_dir, game)
            return done

        def finished(done):
            for line in done:
                self.manager.log.info(f"Get mods: {line}")
            self.changed = self.changed or bool(entry.mods)
            shutil.rmtree(self._work_dir(), ignore_errors=True)
            QMessageBox.information(
                self, f"{entry.title} installed",
                "Done:\n- " + "\n- ".join(done)
                + ("\n\nTick it in the mod list and click Save Mod List." if entry.mods else "")
            )
            self.status.setText(f"{entry.title} installed.")

        self._start(put, finished, f"Installing {entry.title}...")

    def done(self, result: int) -> None:
        if self.task is not None and self.task.isRunning():
            return              # let the download or install finish first
        shutil.rmtree(self._work_dir(), ignore_errors=True)
        super().done(result)
