"""The "Get mods" window: KSFA's mods, straight from GitHub.

Two tabs. **Mods** lists every mod in the mods repository (LiD-Mods): the
player gets or updates any one of them, or all that have an update, and
nothing else. **Mods with a plugin** lists the mods that need a plugin DLL,
each from its own repository's release.

Either way the download is checked against the SHA-256 GitHub (or the mods
list) gives for it, and - once the player has seen exactly what will be
written where and said yes - the mod goes in the mods folder, and a plugin in
the game's lid-plugins folder, with the Plugin Loader first if it is missing.
Nothing goes online until the player clicks. See get_mods.py for the rules.
"""

from __future__ import annotations

import html
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import get_mods as G
from .. import plugins
from .workers import TaskThread

COLUMNS = ("Mod", "You have", "Newest", "")
MOD, HAVE, NEWEST, ACTION = range(len(COLUMNS))
WORK_DIR = ("_update", "get-mods")
SHOW = ("All mods", "Ones you have", "Ones with an update", "Ones you do not have")
BLURB = 260                     # characters of a mod's description in its row


def _action_cell(button: QPushButton) -> QWidget:
    holder = QWidget()                  # so the button keeps its own height
    centred = QHBoxLayout(holder)
    centred.setContentsMargins(6, 0, 6, 0)
    centred.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
    return holder


def _table(rows: int) -> QTableWidget:
    table = QTableWidget(rows, len(COLUMNS))
    table.setHorizontalHeaderLabels(COLUMNS)
    table.verticalHeader().setVisible(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(MOD, QHeaderView.ResizeMode.Stretch)
    for column in (HAVE, NEWEST, ACTION):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    return table


class GetModsDialog(QDialog):
    def __init__(self, manager, parent=None, opener=None):
        super().__init__(parent)
        self.manager = manager
        self.opener = opener or G.self_update._open
        self.releases: dict[str, G.Release | G.GetModsError] = {}
        self.listed: list[G.ListedMod] | G.GetModsError | None = None   # the mods list, once asked
        self.mod_buttons: dict[str, QPushButton] = {}
        self.task: TaskThread | None = None
        self.changed = False            # a mod was added: the main window rescans
        self.setWindowTitle("Get mods")
        self.resize(940, 700)

        mods_intro = QLabel(
            "Every mod that needs no plugin, from the "
            f"<a href='{G.MODS_PAGE}'>mods repository</a>. Get only the ones you want; "
            "<b>Update</b> brings one mod's newest version without a new manager. Click "
            "<b>Check GitHub for the newest</b> to see the list. A mod you update keeps its "
            "settings, and the next <b>Save Mod List</b> applies the new version."
        )
        mods_intro.setWordWrap(True)
        mods_intro.setTextFormat(Qt.TextFormat.RichText)
        mods_intro.setOpenExternalLinks(True)
        self.mods_table = _table(0)
        self.show_box = QComboBox()
        self.show_box.addItems(SHOW)
        self.show_box.currentIndexChanged.connect(lambda _index: self.refresh())
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.update_all)
        self.mods_note = QLabel("Not checked yet.")
        self.mods_note.setWordWrap(True)
        mods_bar = QHBoxLayout()
        mods_bar.addWidget(QLabel("Show:"))
        mods_bar.addWidget(self.show_box)
        mods_bar.addWidget(self.mods_note, 1)
        mods_bar.addWidget(self.update_all_button)
        mods_page = QWidget()
        mods_layout = QVBoxLayout(mods_page)
        mods_layout.addWidget(mods_intro)
        mods_layout.addLayout(mods_bar)
        mods_layout.addWidget(self.mods_table, 1)

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

        self.table = _table(len(G.CATALOG))
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
            self.table.setCellWidget(row, ACTION, _action_cell(button))
        plugin_page = QWidget()
        plugin_layout = QVBoxLayout(plugin_page)
        plugin_layout.addWidget(intro)
        plugin_layout.addWidget(self.where)
        plugin_layout.addWidget(self.table, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(mods_page, "Mods")
        self.tabs.addTab(plugin_page, "Mods with a plugin")

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
        layout.addWidget(self.tabs, 1)
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
            # the release it came from, the same number Newest shows (the mod
            # inside has a version of its own, which only confused)
            if have.release:
                text = have.release
            elif have.plugin:
                text = "installed"
            elif any(have.mods.values()):
                text = "mod only"
            else:
                text = "-"
            details = [f"{folder} mod {v}" for folder, v in have.mods.items() if v]
            if entry.plugin and have.plugin:
                details.append(f"{entry.plugin} {have.release or '(no version in it)'}")
            self.table.item(row, HAVE).setText(text)
            self.table.item(row, HAVE).setToolTip("\n".join(details))
            release = self.releases.get(entry.key)
            if isinstance(release, G.Release):
                self.table.item(row, NEWEST).setText(release.version)
                self.table.item(row, NEWEST).setToolTip("")
            elif isinstance(release, G.GetModsError):
                self.table.item(row, NEWEST).setText("?")
                self.table.item(row, NEWEST).setToolTip(str(release))
            if not have.anything:
                action = "Get"
            elif isinstance(release, G.Release) and have.release == release.version:
                action = "Reinstall"
            else:
                action = "Update"
            self.buttons[entry.key].setText(action)
        self.table.resizeRowsToContents()
        self._refresh_mods()

    def _mod_rows(self) -> list[tuple[G.ListedMod, str, str]]:
        """(mod, the version here, the button's word) for each listed mod, by name."""
        if not isinstance(self.listed, list):
            return []
        rows = []
        for mod in sorted(self.listed, key=lambda m: m.name.lower()):
            have = G.installed_version(self.mods_dir, mod.folder)
            rows.append((mod, have, G.mod_action(mod, have)))
        return rows

    def updatable(self) -> list[G.ListedMod]:
        return [mod for mod, have, action in self._mod_rows() if have and action == "Update"]

    def _refresh_mods(self) -> None:
        show = self.show_box.currentIndex()
        rows = [(mod, have, action) for mod, have, action in self._mod_rows()
                if show == 0 or (show == 1 and have) or (show == 2 and have and action == "Update")
                or (show == 3 and not have)]
        table = self.mods_table
        table.setRowCount(len(rows))
        self.mod_buttons = {}
        for row, (mod, have, action) in enumerate(rows):
            blurb = mod.description if len(mod.description) <= BLURB \
                else mod.description[:BLURB].rsplit(" ", 1)[0] + "..."
            by = f" by {html.escape(mod.author)}" if mod.author else ""
            label = QLabel(f"<b>{html.escape(mod.name)}</b>{by} - {html.escape(blurb)}")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setContentsMargins(4, 4, 4, 4)
            label.setToolTip(mod.description)
            table.setCellWidget(row, MOD, label)
            table.setItem(row, HAVE, QTableWidgetItem(have or "-"))
            newest = QTableWidgetItem(mod.version)
            newest.setToolTip(f"Published {mod.updated}" if mod.updated else "")
            table.setItem(row, NEWEST, newest)
            button = QPushButton(action)
            button.setEnabled(self.task is None and not mod.needs_newer_manager())
            button.clicked.connect(lambda _checked=False, folder=mod.folder: self.get_mod(folder))
            self.mod_buttons[mod.folder] = button
            table.setCellWidget(row, ACTION, _action_cell(button))
        table.resizeRowsToContents()
        updates = len(self.updatable())
        self.update_all_button.setText(f"Update all ({updates})" if updates else "Update all")
        self.update_all_button.setEnabled(self.task is None and updates > 0)
        if isinstance(self.listed, G.GetModsError):
            self.mods_note.setText(f"Could not get the list: {self.listed}")
        elif isinstance(self.listed, list):
            here = sum(1 for _, have, _ in self._mod_rows() if have)
            self.mods_note.setText(f"{len(self.listed)} mods, {here} of them here"
                                   + (f", {updates} with an update." if updates else "."))

    # -- asking GitHub -------------------------------------------------------------

    def _busy(self, busy: bool, message: str = "") -> None:
        self.check_button.setEnabled(not busy)
        for button in self.buttons.values():
            button.setEnabled(not busy)
        for button in self.mod_buttons.values():
            button.setEnabled(not busy)
        self.update_all_button.setEnabled(not busy and bool(self.updatable()))
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
            try:
                listed = G.mods_index(opener=self.opener)
            except G.GetModsError as exc:
                listed = exc
            found = {}
            for entry in G.CATALOG:
                try:
                    found[entry.key] = G.latest(entry, opener=self.opener)
                except G.GetModsError as exc:
                    found[entry.key] = exc
            return listed, found

        def done(result):
            self.listed, found = result
            self.releases.update(found)
            problems = [r for r in found.values() if isinstance(r, G.GetModsError)]
            if isinstance(self.listed, G.GetModsError):
                problems.append(self.listed)
            self.status.setText(f"{len(problems)} could not be checked - hover over the ?, or see "
                                "the Mods tab" if problems else "Checked.")

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
            plugin_only = G.plugins_needed(entry, game)     # their plugin, not their mod
            packages = []
            for item in wanted + plugin_only:
                release = self.releases.get(item.key)
                if not isinstance(release, G.Release):
                    release = G.latest(item, opener=self.opener)
                    self.releases[item.key] = release
                packed = G.download(release, work, opener=self.opener)
                package = G.unpack(packed, work / item.key)
                packages.append(G.plugin_only(package) if item in plugin_only else package)
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

        # a plugin goes in on only when a ticked mod names it, as a save would
        # leave it - a new mod arrives unticked, so its plugin waits switched off
        switched_on = plugins.named_by(self.manager.scan.mods, self.manager.state.enabled_mods)
        # a plugin for a mod already here (the Vending Machine add-on) follows that mod too
        named_here = plugins.named_by(self.manager.scan.mods)

        def put():
            done = []
            for package in packages:
                package.named |= named_here
                done += G.install(package, self.mods_dir, game, switched_on=switched_on)
            return done

        def finished(done):
            for line in done:
                self.manager.log.info(f"Get mods: {line}")
            self.changed = self.changed or bool(entry.mods)
            shutil.rmtree(self._work_dir(), ignore_errors=True)
            QMessageBox.information(
                self, f"{entry.title} installed",
                "Done:\n- " + "\n- ".join(done)
                + ("\n\nTick it in the mod list and click Save Mod List."
                   + (" Its plugin is switched on and off with it." if entry.plugin else "")
                   if entry.mods else "")
                + ("\n\nTick Vending Machine, turn its Rotation on and click Save Mod List."
                   if entry.key == "vending" else "")
            )
            self.status.setText(f"{entry.title} installed.")

        self._start(put, finished, f"Installing {entry.title}...")

    # -- the mods repository ---------------------------------------------------------

    def _listed(self, folder: str) -> G.ListedMod | None:
        if isinstance(self.listed, list):
            for mod in self.listed:
                if mod.folder == folder:
                    return mod
        return None

    def get_mod(self, folder: str) -> None:
        mod = self._listed(folder)
        if mod is not None:
            self._get_mods([mod], mod.name)

    def update_all(self) -> None:
        mods = self.updatable()
        if mods:
            self._get_mods(mods, f"{len(mods)} mod update(s)")

    def _get_mods(self, mods: list[G.ListedMod], what: str) -> None:
        work = self._work_dir()

        def fetch():
            shutil.rmtree(work, ignore_errors=True)
            return [(mod, G.download_mod(mod, work, opener=self.opener)) for mod in mods]

        self._start(fetch, lambda got: self._confirm_mods(what, got), f"Downloading {what}...")

    def _confirm_mods(self, what: str, got: list[tuple[G.ListedMod, G.Package]]) -> None:
        lines = []
        for mod, _package in got:
            have = G.installed_version(self.mods_dir, mod.folder)
            change = f"{have} -> {mod.version}" if have else f"{mod.version}, new"
            lines.append(f"{mod.name} ({change}) into {self.mods_dir / mod.folder}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(f"Install {what}?")
        box.setText(f"<b>{html.escape(what)}</b> downloaded and checked against the SHA-256 the mods "
                    "list gives for it.")
        box.setInformativeText(
            "It will write:<ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"
            "A mod already here is replaced by the new copy; its settings are kept, and the next "
            "<b>Save Mod List</b> applies the new version."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Install")
        if box.exec() != QMessageBox.StandardButton.Yes:
            shutil.rmtree(self._work_dir(), ignore_errors=True)
            self.status.setText("Nothing was installed.")
            return

        def put():
            done = []
            for _mod, package in got:
                done += G.install(package, self.mods_dir, None)
            return done

        def finished(done):
            for line in done:
                self.manager.log.info(f"Get mods: {line}")
            self.changed = True
            shutil.rmtree(self._work_dir(), ignore_errors=True)
            ticked = set(self.manager.state.enabled_mods)
            note = ("\n\nClick Save Mod List to apply the new version."
                    if any(mod.folder in ticked for mod, _ in got)
                    else "\n\nTick it in the mod list and click Save Mod List.")
            QMessageBox.information(self, "Installed", "Done:\n- " + "\n- ".join(done) + note)
            self.status.setText(f"{what} installed.")

        self._start(put, finished, f"Installing {what}...")

    def done(self, result: int) -> None:
        if self.task is not None and self.task.isRunning():
            return              # let the download or install finish first
        shutil.rmtree(self._work_dir(), ignore_errors=True)
        super().done(result)
