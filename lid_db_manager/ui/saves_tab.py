"""The Saves tab: the save library - every save kept, with a nickname, what is in it, and its mods.

The list is the library; the panel beside it shows the picked save: its
nickname, the numbers a player knows (Kill Coins, Splithium, Bloodnium, time
played, highest floor, blueprints...), every fighter with their stats, and the
mods it was last played with. Switching puts that save in the game - the one
there goes back into its own entry first - and then offers to make its mod
list the one ticked, which Save Mod List applies as usual. See savelibrary.py
for the rules about the game's files.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import modpack, newsave, savegame, savelink
from ..savelibrary import CLOUD_BUDGET, CLOUD_QUOTA, MB, SLOTS, LibraryError
from ..savesummary import STAT_NAMES, STATS, SaveSummary, read_summary

FIGHTER_COLUMNS = ("Name", "Class", "Grade", "Level", "Where") + tuple(STAT_NAMES[s] for s in STATS)
ROLE_ID = Qt.ItemDataRole.UserRole


class SavesTab(QWidget):
    # A save's mod list was made the ticked one: the main window rescans and
    # offers Save Mod List. Carries the mods it named that are not installed.
    modListChosen = Signal(list)
    # A save's mod list is already the one in the game and the ticks were put back
    # to it: the main window shows them, with nothing left to save.
    modListRestored = Signal()
    # a .lidpack picked in Import a .sav...: the window keeps it with the mod packs
    packChosen = Signal(object)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._summaries: dict[tuple[str, str], SaveSummary | str] = {}
        self._names = None

        intro = QLabel(
            "Every save you keep, each with a nickname of your own and the mods it is played "
            "with. <b>Switch to this save</b> puts it in the game - the save there now is kept "
            "in its own place here first, with everything played since - and then offers its "
            "mod list. The game's file is never renamed. Close the game before switching."
        )
        intro.setWordWrap(True)

        self.cloud_found = QLabel()
        self.cloud_found.setWordWrap(True)
        self.cloud_found.setTextFormat(Qt.TextFormat.RichText)
        self.restore_button = QPushButton("Bring them in")
        self.restore_button.clicked.connect(self.restore_cloud)
        found_row = QHBoxLayout()
        found_row.addWidget(self.cloud_found, 1)
        found_row.addWidget(self.restore_button)
        self.found_box = QWidget()
        self.found_box.setLayout(found_row)
        self.found_box.setVisible(False)
        self._found = []

        # Other saves sitting in the game's save folder (old backups, other tools').
        self.stray_label = QLabel()
        self.stray_label.setWordWrap(True)
        self.stray_label.setTextFormat(Qt.TextFormat.RichText)
        self.stray_button = QPushButton("Give them slots")
        self.stray_button.setToolTip("Keep each of them in the library, in the first empty slots. The files in "
                                     "the game's save folder are left where they are.")
        self.stray_button.clicked.connect(self.keep_strays)
        stray_row = QHBoxLayout()
        stray_row.addWidget(self.stray_label, 1)
        stray_row.addWidget(self.stray_button)
        self.stray_box = QWidget()
        self.stray_box.setLayout(stray_row)
        self.stray_box.setVisible(False)
        self._strays = []

        self.cloud_meter = QLabel()
        self.cloud_meter.setObjectName("dim")

        # What the note beside the save in the game says its mods are.
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.TextFormat.RichText)
        self.note_button = QPushButton("Use the save's mod list")
        self.note_button.setToolTip("Tick the mods the save in the game says it is played with "
                                    "(Save Mod List applies them).")
        self.note_button.clicked.connect(self.use_note_mods)
        note_row = QHBoxLayout()
        note_row.addWidget(self.note_label, 1)
        note_row.addWidget(self.note_button)
        self._checks = []

        self.live_label = QLabel()
        self.live_label.setWordWrap(True)
        self.live_label.setTextFormat(Qt.TextFormat.RichText)

        self.keep_button = QPushButton("Keep the game's save...")
        self.keep_button.setToolTip("Add the save that is in the game now to the library, with a nickname.")
        self.keep_button.clicked.connect(self.keep_live)
        self.update_button = QPushButton("Update from the game")
        self.update_button.setToolTip("Copy what the game has saved since into this save's place in the library.")
        self.update_button.clicked.connect(self.update_live)
        import_button = QPushButton("Import a .sav...")
        import_button.clicked.connect(self.import_file)
        self.new_button = QPushButton("Start a new save...")
        self.new_button.setToolTip("A brand-new game, from the tutorial or just after it, in place of the save "
                                   "in the game (which stays here in the library).")
        self.new_button.clicked.connect(self.new_save)
        # For the save picked in the list. New saves start with it on.
        self.cloud_button = QPushButton("Steam Cloud backup: on")
        self.cloud_button.setCheckable(True)
        self.cloud_button.setToolTip(
            "Keep one more copy of the picked save beside the game's own, where Steam Cloud carries it to "
            "your other PCs. On for every save added to the library, while it fits: the manager keeps "
            f"these copies under {CLOUD_BUDGET // MB} MB of the {CLOUD_QUOTA // MB} MB Steam gives the "
            "game, so your own save always has room.")
        self.cloud_button.clicked.connect(self.toggle_cloud)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        top = QHBoxLayout()
        top.addWidget(self.keep_button)
        top.addWidget(self.update_button)
        top.addWidget(import_button)
        top.addWidget(self.new_button)
        top.addWidget(self.cloud_button)
        top.addStretch(1)
        top.addWidget(self.cloud_meter)
        top.addWidget(refresh_button)

        self.slots_label = QLabel()
        self.slots_label.setObjectName("dim")
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._show_selected())

        # -- details -------------------------------------------------------
        self.nickname = QLineEdit()
        self.nickname.setPlaceholderText("A name for this save")
        self.nickname.returnPressed.connect(self.rename)
        rename = QPushButton("Rename")
        rename.clicked.connect(self.rename)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nickname:"))
        name_row.addWidget(self.nickname, 1)
        name_row.addWidget(rename)

        self.facts = QFormLayout()
        facts_box = QWidget()
        facts_box.setLayout(self.facts)

        self.fighters = QTableWidget(0, len(FIGHTER_COLUMNS))
        self.fighters.setHorizontalHeaderLabels(FIGHTER_COLUMNS)
        self.fighters.verticalHeader().setVisible(False)
        self.fighters.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fighters.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.fighters.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # As tall as its rows, with no scroll bar of its own: the details panel
        # scrolls as one page (user, 2026-10-20).
        self.fighters.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fighters.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.mods_label = QLabel()
        self.mods_label.setWordWrap(True)
        self.mods_label.setTextFormat(Qt.TextFormat.RichText)

        self.switch_button = QPushButton("Switch to this save")
        self.switch_button.setObjectName("primary")
        self.switch_button.clicked.connect(self.switch)
        self.use_mods_button = QPushButton("Use its mod list")
        self.use_mods_button.setToolTip("Tick the mods this save was played with (Save Mod List applies them).")
        self.use_mods_button.clicked.connect(lambda: self.use_mods())
        export = QPushButton("Export...")
        export.clicked.connect(self.export)
        self.share_button = QPushButton("Share with its mods...")
        self.share_button.setToolTip("The save and the mods it is played with - their settings and plugins "
                                     "too - in one mod pack file to hand to someone.")
        self.share_button.clicked.connect(self.share)
        self.which_button = QPushButton("Which mods?...")
        self.which_button.setToolTip("Check the save for signs of mods, and note which mods it is played with.")
        self.which_button.clicked.connect(lambda: self.which_mods())
        move = QPushButton("Move to slot...")
        move.clicked.connect(self.move_slot)
        self.edit_button = QPushButton("Edit...")
        self.edit_button.setToolTip("Change what is in this save: Kill Coins, Splithium, Bloodnium and more.")
        self.edit_button.clicked.connect(self.edit)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete)
        actions = QHBoxLayout()
        actions.addWidget(self.delete_button)
        actions.addWidget(export)
        actions.addWidget(self.share_button)
        actions.addWidget(self.which_button)
        actions.addWidget(move)
        actions.addWidget(self.edit_button)
        actions.addStretch(1)
        actions.addWidget(self.use_mods_button)
        actions.addWidget(self.switch_button)

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(6, 0, 0, 0)
        # The buttons first, above the save's details (user's choice, 2026-10-20).
        detail_layout.addLayout(actions)
        detail_layout.addLayout(name_row)
        detail_layout.addWidget(facts_box)
        detail_layout.addWidget(QLabel("<b>Fighters</b>"))
        detail_layout.addWidget(self.fighters)
        detail_layout.addWidget(self.mods_label)
        detail_layout.addStretch(1)
        self.details = details

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(details)

        list_box = QWidget()
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self.slots_label)
        list_layout.addWidget(self.list, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(list_box)
        split.addWidget(scroll)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 4)
        layout.addWidget(intro)
        layout.addWidget(self.live_label)
        layout.addLayout(note_row)
        layout.addWidget(self.found_box)
        layout.addWidget(self.stray_box)
        layout.addLayout(top)
        layout.addWidget(split, 1)

    # -- reading -----------------------------------------------------------

    def _summary(self, entry) -> SaveSummary | str:
        """The entry's summary, or why it could not be read; kept until its file changes."""
        key = (entry.id, entry.sha256)
        if key not in self._summaries:
            try:
                self._summaries[key] = read_summary(entry.save, self._names)
            except savegame.SaveError as exc:
                self._summaries[key] = str(exc)
        return self._summaries[key]

    def _live(self):
        try:
            return self.manager.library.live(self.manager.asset_game_root)
        except OSError:
            return []

    def refresh(self) -> None:
        self._names = self.manager.save_names()
        live = self._live()
        in_game = {s.entry.id: s for s in live if s.known}
        lines = []
        for s in live:
            if s.foreign:
                lines.append(f"The save in the game (account {s.steam_id}) is <b>not "
                             f"\"{html.escape(s.entry.nickname)}\" any more</b> - it is another save, put there "
                             "by hand perhaps. <b>Keep the game's save</b> to give it a slot of its own; "
                             f"\"{html.escape(s.entry.nickname)}\" stays as it was.")
            elif s.entry is None:
                lines.append(f"The save in the game (account {s.steam_id}) is <b>not in the library yet</b> - "
                             "<b>Keep the game's save</b> so it can never be lost when you switch.")
            elif s.in_sync:
                lines.append(f"In the game: <b>{html.escape(s.entry.nickname)}</b>, up to date here.")
            else:
                lines.append(f"In the game: <b>{html.escape(s.entry.nickname)}</b>, played since it was "
                             "last kept - it is brought up to date when the game closes, when the manager "
                             "starts, and before any switch.")
        if not live:
            lines.append("No save in the game was found. The manager looks under the game folder's "
                         "Savedata - set the game folder with Tools > Set game folder... if it is wrong.")
        self.live_label.setText("<br>".join(lines))
        self.keep_button.setEnabled(any(not s.known for s in live))
        self._show_notes()
        accounts = sorted({s.steam_id for s in live})
        used = sum(self.manager.library.cloud_usage(a) for a in accounts)
        if accounts:
            room = max(0, CLOUD_BUDGET - used)
            biggest = max([e.save.stat().st_size for e in self.manager.library.entries()]
                          + [s.path.stat().st_size for s in live] + [1])
            more = room // biggest
            self.cloud_meter.setText(f"Steam Cloud: {used / MB:.1f} of {CLOUD_QUOTA // MB} MB - room for "
                                     f"about {more} more backed-up save{'s' if more != 1 else ''}")
            self.cloud_meter.setToolTip(
                f"The manager keeps its Steam Cloud backups under {CLOUD_BUDGET // MB} MB, so the game's own "
                "save always fits. \"About\" because saves grow as they are played: the count assumes each "
                f"new one is as big as your biggest ({biggest / MB:.1f} MB).")
        else:
            self.cloud_meter.setText("")
        try:
            self._found = self.manager.library.cloud_saves()
        except OSError:
            self._found = []
        self.found_box.setVisible(bool(self._found))
        if self._found:
            names = ", ".join(f"<b>{html.escape(c.nickname)}</b>" for c in self._found[:5])
            more = f" and {len(self._found) - 5} more" if len(self._found) > 5 else ""
            self.cloud_found.setText(f"Backed up to Steam Cloud but not in this library: {names}{more}.")
        self.update_button.setEnabled(any(s.known and not s.in_sync for s in live))
        try:
            self._strays = self.manager.library.stray_saves()
        except OSError:
            self._strays = []
        self.stray_box.setVisible(bool(self._strays))
        if self._strays:
            names = ", ".join(html.escape(p.name) for p in self._strays[:4])
            more = f" and {len(self._strays) - 4} more" if len(self._strays) > 4 else ""
            self.stray_label.setText(f"Other saves in the game's save folder, not in the library: {names}{more}.")

        current = self._selected_id()
        self.list.blockSignals(True)
        self.list.clear()
        slots = self.manager.library.by_slot()
        self.slots_label.setText(f"Save slots: {len(slots)} of {SLOTS} used")
        first = None
        for number in range(1, SLOTS + 1):
            entry = slots.get(number)
            if entry is None:
                item = QListWidgetItem(f"Slot {number:>2}   - empty -")
                item.setData(ROLE_ID, None)
                item.setToolTip("An empty slot. Keep the game's save, Import a .sav or Start a new save "
                                "fills the first empty one.")
                self.list.addItem(item)
                continue
            summary = self._summary(entry)
            mark = "▶ " if entry.id in in_game else ""
            cloud = "  ☁" if entry.cloud else ""
            if isinstance(summary, SaveSummary):
                floor = f" · {summary.highest_floor}F" if summary.highest_floor else ""
                second = f"{summary.play_time}{floor} · kept {entry.updated[:16]}"
            else:
                second = "could not be read"
            item = QListWidgetItem(f"Slot {number:>2}   {mark}{entry.nickname}{cloud}\n            {second}")
            item.setData(ROLE_ID, entry.id)
            tips = ["This is the save in the game."] if entry.id in in_game else []
            if entry.cloud:
                tips.append("Backed up to Steam Cloud.")
            item.setToolTip(" ".join(tips))
            self.list.addItem(item)
            first = first or item
            if entry.id == current:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and first is not None:
            self.list.setCurrentItem(first)
        self.list.blockSignals(False)
        self._show_selected()

    def _show_notes(self) -> None:
        try:
            self._checks = self.manager.save_mod_checks()
        except OSError:
            self._checks = []
        lines = []
        for check in self._checks:
            if check.state == "matches":
                lines.append("Its mods: the same as this install has applied.")
            elif check.state == "differs":
                text = (f"<b>Its own note says it is played with a different mod list</b> - "
                        f"{len(check.link.enabled)} mod(s), noted {html.escape(check.link.written)} by "
                        f"{html.escape(check.link.by)}, on this or another PC.")
                if check.missing:
                    text += " Not installed here: " + html.escape(", ".join(check.missing)) + "."
                if check.versions:
                    text += " Other versions here: " + html.escape(", ".join(
                        f"{m} {here} (the save had {theirs})" for m, theirs, here in check.versions)) + "."
                lines.append(text)
            elif check.state == "other":
                lines.append("The mod note beside it belongs to a different save, so it is not used. "
                             "Save Mod List writes this one's.")
            else:
                lines.append("No mod list is noted beside it yet - Save Mod List notes it, and the note "
                             "goes with the save wherever Steam Cloud or a copy takes it.")
        self.note_label.setText("<br>".join(lines))
        self.note_label.setVisible(bool(lines))
        self.note_button.setVisible(any(c.state == "differs" for c in self._checks))

    def use_note_mods(self) -> None:
        check = next((c for c in self._checks if c.state == "differs"), None)
        if check is None:
            return
        if not self._already_in_place(check.link.mods, "Use the save's mod list"):
            missing = self.manager.use_mod_choice(check.link.mods)
            self.modListChosen.emit(missing)

    def _already_in_place(self, mods: dict, title: str, lead: str = "") -> bool:
        """Say so, and do nothing more, when a save's mod list is already set up.

        True when nothing further is needed: the game already has these mods with
        these settings (the ticks are put back to match if they moved since), or
        they are ticked already and only Save Mod List is left, which is offered.
        """
        where = self.manager.mods_in_place(mods)
        if where == "applied":
            moved = not savelink.same_choice(mods, self.manager.mod_choice())
            if moved:
                self.manager.use_mod_choice(mods)
                self.modListRestored.emit()
            QMessageBox.information(
                self, title,
                f"{lead}The game already has this save's mods, with the same settings"
                + (" - the ticks in the Mods tab were put back to match them" if moved else "")
                + ". Nothing needs saving.")
            return True
        if where == "ticked":
            if lead:
                QMessageBox.information(self, title, lead.strip())
            self.modListChosen.emit([])     # ticked already: only Save Mod List is offered
            return True
        return False

    def _selected_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(ROLE_ID) if item is not None else None

    def _selected(self):
        entry_id = self._selected_id()
        if not entry_id:
            return None
        try:
            return self.manager.library.get(entry_id)
        except LibraryError:
            return None

    def _show_selected(self) -> None:
        entry = self._selected()
        self.details.setEnabled(entry is not None)
        self._show_cloud(entry)
        while self.facts.rowCount():
            self.facts.removeRow(0)
        self.fighters.setRowCount(0)
        self._fit_fighters()
        if entry is None:
            self.nickname.clear()
            picked = self.list.currentItem() is not None
            self.mods_label.setText(("This slot is empty. " if picked else "No save picked. ")
                                    + "<b>Keep the game's save</b>, <b>Import a .sav</b> or <b>Start a new "
                                    "save</b> fills the first empty slot.")
            return
        self.nickname.setText(entry.nickname)
        in_game = any(s.known and s.entry.id == entry.id for s in self._live())
        self.switch_button.setEnabled(not in_game)
        self.switch_button.setText("This is the save in the game" if in_game else "Switch to this save")
        self.delete_button.setEnabled(not in_game)
        summary = self._summary(entry)
        if isinstance(summary, str):
            self.facts.addRow("Problem:", QLabel(html.escape(summary)))
        else:
            for label, value in summary.lines():
                self.facts.addRow(f"{label}:", QLabel(value))
            self._fill_fighters(summary)
        self.facts.addRow("Steam account:", QLabel(entry.steam_id or "-"))
        self.facts.addRow("Kept:", QLabel(f"{entry.updated} (added {entry.added})"))
        self.mods_label.setText(self._mods_text(entry))
        self.use_mods_button.setEnabled(bool(entry.mods))
        self.share_button.setEnabled(bool(entry.mods))
        # a list Save Mod List noted is the real one; anything else can be looked at again
        self.which_button.setVisible(not entry.mods or bool(entry.mods.get("noted")))
        self._show_cloud(entry)

    def _fill_fighters(self, summary: SaveSummary) -> None:
        self.fighters.setRowCount(len(summary.fighters))
        for row, f in enumerate(summary.fighters):
            grade = f"G{f.grade}" + (f" +{f.limit_break}" if f.limit_break else "")
            cells = [f.name, f.class_name, grade, str(f.level), f.state_label]
            cells += [f"{f.points.get(s, 0)}" + (f"+{f.bonus[s]}" if f.bonus.get(s) else "") for s in STATS]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.fighters.setItem(row, column, item)
        self.fighters.setToolTip("Stats are the points put into each; +N is the stat bonus the fighter "
                                 "was bought with.")
        self._fit_fighters()

    def _fit_fighters(self) -> None:
        """The fighter table sized to show every row and column, so it never scrolls itself."""
        table = self.fighters
        table.resizeColumnsToContents()
        frame = 2 * table.frameWidth()
        height = table.horizontalHeader().sizeHint().height() + frame
        height += sum(table.rowHeight(row) for row in range(table.rowCount()))
        table.setFixedHeight(height)
        table.setMinimumWidth(table.horizontalHeader().length() + frame)

    def _mods_text(self, entry) -> str:
        if not entry.mods:
            return ("<b>Mods:</b> none noted yet - the list is noted each time you click Save Mod "
                    "List while this is the save in the game.")
        names = self.manager.scan.by_id
        mods = entry.mods.get("enabled_mods", [])
        how = {"found": " <i>- found by checking the save; mods that leave nothing in a save may be "
                        "missing</i>",
               "chosen": " <i>- as you chose for it</i>"}.get(entry.mods.get("noted"), "")
        if not mods:
            return "<b>Mods:</b> played with no mods." + how
        shown = [html.escape(m) + ("" if m in names else " <i>(not installed)</i>") for m in mods]
        return f"<b>Mods ({len(mods)}):</b> " + ", ".join(shown) + how

    # -- actions -------------------------------------------------------------

    def _fail(self, title: str, exc: Exception) -> None:
        QMessageBox.warning(self, title, f"Nothing was changed: {exc}")

    def _account_to_use(self, title: str) -> str | None:
        accounts = sorted({s.steam_id for s in self._live()})
        if len(accounts) == 1:
            return accounts[0]
        if not accounts:
            text, ok = QInputDialog.getText(self, title, "The Steam account (the number in Savedata) this save "
                                                         "belongs to:")
            return text.strip() if ok and text.strip().isdigit() else None
        choice, ok = QInputDialog.getItem(self, title, "Which Steam account is it for?", accounts, 0, False)
        return choice if ok else None

    def keep_live(self) -> None:
        title = "Keep the game's save"
        unknown = [s for s in self._live() if not s.known]
        if not unknown:
            return
        live = unknown[0]
        if len(unknown) > 1:
            choice, ok = QInputDialog.getItem(self, title, "Which account's save?",
                                              [s.steam_id for s in unknown], 0, False)
            if not ok:
                return
            live = next(s for s in unknown if s.steam_id == choice)
        try:
            player = read_summary(live.path).player
        except savegame.SaveError:
            player = ""
        name, ok = QInputDialog.getText(self, title, "A nickname for this save (only the library's name for "
                                                     "it - the game's file keeps its own):",
                                        text=f"{player}'s save" if player else "My save")
        if not ok:
            return
        try:
            entry = self.manager.add_live_save(live.steam_id, name)
        except (LibraryError, OSError) as exc:
            self._fail(title, exc)
            return
        self._select_after_refresh(entry.id)
        self._tell_cloud_problem()

    def update_live(self) -> None:
        title = "Update from the game"
        for live in self._live():
            if live.known and not live.in_sync:
                try:
                    self.manager.store_live_save(live.steam_id)
                except (LibraryError, OSError) as exc:
                    self._fail(title, exc)
                    return
        self.refresh()

    def import_file(self) -> None:
        title = "Import a .sav"
        path, _ = QFileDialog.getOpenFileName(
            self, title, "", f"LET IT DIE saves (*.sav);;Mod packs with a save (*{modpack.SUFFIX});;All files (*)")
        if not path:
            return
        if Path(path).suffix.lower() == modpack.SUFFIX:
            self.packChosen.emit(Path(path))
            return
        steam_id = self._account_to_use(title)
        if not steam_id:
            return
        name, ok = QInputDialog.getText(self, title, "A nickname for this save:", text=Path(path).stem)
        if not ok:
            return
        try:
            entry = self.manager.import_save(Path(path), steam_id, name)
        except (LibraryError, OSError) as exc:
            self._fail(title, exc)
            return
        self._select_after_refresh(entry.id)
        self._tell_cloud_problem()
        self.which_mods(entry, lead=f"\"{entry.nickname}\" is kept in slot {entry.slot}.")

    def new_save(self) -> None:
        title = "Start a new save"
        live = self._live()
        if any(not s.known for s in live):
            QMessageBox.information(self, title, "The save in the game is not in the library yet. "
                                                 "Keep the game's save first, so it is not lost.")
            return
        steam_id = self._account_to_use(title)
        if not steam_id:
            return
        starts = list(newsave.START_LABELS)
        label, ok = QInputDialog.getItem(self, title, "Where should the new game begin?",
                                         [newsave.START_LABELS[s] for s in starts], 0, False)
        if not ok:
            return
        start = starts[[newsave.START_LABELS[s] for s in starts].index(label)]
        name, ok = QInputDialog.getText(self, title, "The profile name for the new game (the name the game "
                                                     "asks for at the start, up to 16 letters):")
        if not ok or not name.strip():
            return
        nickname, ok = QInputDialog.getText(self, title, "A nickname for it in the library:",
                                            text=f"New game - {name.strip()}")
        if not ok:
            return
        now = next((s for s in live if s.steam_id == steam_id), None)
        keeping = (f"\"{now.entry.nickname}\" goes back into its place in the library first, with "
                   "everything played since, and is backed up.\n\n") if now is not None else ""
        answer = QMessageBox.question(
            self, title,
            f"Start a brand-new game, \"{name.strip()}\", in place of the save in the game?\n\n{keeping}"
            + ("The game starts it from the tutorial." if start == newsave.TUTORIAL else
               "The game carries on from the end of the tutorial: two films, then choosing a fighter.")
            + " Close the game before you go on.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            entry = self.manager.start_new_save(steam_id, name, nickname, start)
        except (LibraryError, savegame.SaveError, OSError) as exc:
            self._fail(title, exc)
            return
        self._select_after_refresh(entry.id)
        self._tell_cloud_problem()
        QMessageBox.information(self, title, f"\"{entry.nickname}\" is now the save in the game. Your mods "
                                             "are as they were - untick any you do not want for it, and "
                                             "Save Mod List.")

    def _tell_cloud_problem(self) -> None:
        """A save just added is kept either way; say so when it could not go to Steam Cloud too."""
        problem = getattr(self.manager, "cloud_default_problem", None)
        if problem:
            QMessageBox.information(self, "Steam Cloud backup",
                                    f"The save is kept in the library, but not backed up to Steam Cloud: {problem}")

    def _show_cloud(self, entry) -> None:
        self.cloud_button.setEnabled(entry is not None)
        on = bool(entry is not None and entry.cloud)
        self.cloud_button.setChecked(on)
        self.cloud_button.setText(f"Steam Cloud backup: {'on' if on else 'off'}")

    def toggle_cloud(self, on: bool) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            self.manager.set_save_cloud(entry.id, on)
        except (LibraryError, OSError) as exc:
            self._show_cloud(entry)
            self._fail("Steam Cloud backup", exc)
            return
        self.refresh()

    def keep_strays(self) -> None:
        if not self._strays:
            return
        try:
            kept = self.manager.import_strays(self._strays)
        except (LibraryError, OSError) as exc:
            self._fail("Give them slots", exc)
            self.refresh()
            return
        if kept:
            self._select_after_refresh(kept[0].id)

    def restore_cloud(self) -> None:
        if not self._found:
            return
        try:
            restored = self.manager.restore_cloud_saves(self._found)
        except (LibraryError, OSError) as exc:
            self._fail("Bring in saves from Steam Cloud", exc)
            self.refresh()
            return
        if restored:
            self._select_after_refresh(restored[0].id)

    def edit(self) -> None:
        from .save_editor import SaveEditorDialog

        entry = self._selected()
        if entry is None:
            return
        if self.manager.save_mods(entry.id) is None:
            # the editor's limits and lists follow the save's own mods: settle them first
            self.which_mods(entry, lead="Before it is edited:")
        try:
            dialog = SaveEditorDialog(self.manager, entry.id, self)
        except Exception as exc:
            self._fail("Edit save", exc)
            return
        if dialog.exec() and dialog.written is not None:
            self._summaries.clear()
            self._select_after_refresh(entry.id)

    def which_mods(self, entry=None, lead: str = "") -> bool:
        """Ask which mods a save is played with (SaveModsDialog). True once one is noted."""
        from .save_mods_dialog import SaveModsDialog

        entry = entry or self._selected()
        if entry is None:
            return False
        try:
            dialog = SaveModsDialog(self.manager, entry.id, self, lead=lead)
        except Exception as exc:
            self._fail("Which mods", exc)
            return False
        if dialog.exec() and dialog.noted is not None:
            self._select_after_refresh(entry.id)
            return True
        return False

    def move_slot(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        slot, ok = QInputDialog.getInt(self, "Move to slot", f"Move \"{entry.nickname}\" to slot (1 to {SLOTS}) - "
                                       "a save already there swaps places with it:", entry.slot or 1, 1, SLOTS)
        if not ok or slot == entry.slot:
            return
        try:
            self.manager.library.move_to_slot(entry.id, slot)
        except (LibraryError, OSError) as exc:
            self._fail("Move to slot", exc)
            return
        self._select_after_refresh(entry.id)

    def rename(self) -> None:
        entry = self._selected()
        if entry is None or not self.nickname.text().strip() or self.nickname.text().strip() == entry.nickname:
            return
        try:
            self.manager.library.rename(entry.id, self.nickname.text())
        except (LibraryError, OSError) as exc:
            self._fail("Rename", exc)
            return
        self.refresh()

    def switch(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        title = "Switch saves"
        now = next((s for s in self._live() if s.steam_id == entry.steam_id), None)
        leaving = (f"\"{now.entry.nickname}\" goes back into its place in the library first, with "
                   "everything played since, and is backed up.\n\n") if now is not None and now.entry else ""
        answer = QMessageBox.question(
            self, title,
            f"Put \"{entry.nickname}\" in the game?\n\n{leaving}"
            "Close the game before you go on: it writes its save when it quits.\n\n"
            "Steam Cloud keeps whichever save is in the game.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            entry = self.manager.switch_save(entry.id)
        except (LibraryError, OSError) as exc:
            self._fail(title, exc)
            return
        self.refresh()
        if entry.mods:
            self.use_mods(asked_after_switch=True)
        else:
            QMessageBox.information(self, title, f"\"{entry.nickname}\" is now the save in the game.")

    def use_mods(self, asked_after_switch: bool = False) -> None:
        entry = self._selected()
        if entry is None or not entry.mods:
            return
        title = "Use this save's mod list"
        count = len(entry.mods.get("enabled_mods", []))
        lead = f"\"{entry.nickname}\" is now the save in the game.\n\n" if asked_after_switch else ""
        if self._already_in_place(entry.mods, title, lead):
            return
        answer = QMessageBox.question(
            self, title,
            f"{lead}It was played with {count} mod(s). Tick exactly those, with their settings, "
            "in place of the list you have now?\n\n"
            "Nothing is written to the game until Save Mod List, which you are offered next.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        missing = self.manager.use_mod_choice(entry.mods)
        self.modListChosen.emit(missing)

    def export(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export a save", f"{entry.nickname}.sav",
                                                "LET IT DIE saves (*.sav)")
        if not target:
            return
        try:
            self.manager.library.export(entry.id, Path(target))
        except (LibraryError, OSError) as exc:
            self._fail("Export a save", exc)
            return
        QMessageBox.information(self, "Export a save", f"Saved a copy to:\n  {target}")

    def share(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        title = "Share a save with its mods"
        target, _ = QFileDialog.getSaveFileName(self, title, modpack.file_name(entry.nickname),
                                                f"Mod packs (*{modpack.SUFFIX})")
        if not target:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pack = self.manager.pack_save(entry.id, Path(target))
        except (modpack.PackError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            self._fail(title, exc)
            return
        QApplication.restoreOverrideCursor()
        note = (f"\n\nThese plugins were not in the game folder, so they are not in it: "
                f"{', '.join(pack.missing_plugins)}." if pack.missing_plugins else "")
        QMessageBox.information(
            self, title, f"Packed \"{entry.nickname}\" with its {len(pack.mods)} mod(s) to:\n  {target}\n\n"
            "Whoever gets it imports it (Mods tab, Import..., or drop it on the window): the mods go in "
            f"with their settings, and the save comes in noted as played with them.{note}")

    def keep_pack_save(self, pack) -> None:
        """The save in a mod pack (``pack``, already kept with the mod packs) in a slot of its own."""
        title = "Keep the save in a mod pack"
        steam_id = self._account_to_use(title)
        if not steam_id:
            return
        name, ok = QInputDialog.getText(self, title, "A nickname for this save:",
                                        text=(pack.save or {}).get("nickname") or pack.name)
        if not ok:
            return
        try:
            entry = self.manager.keep_pack_save(pack.name, steam_id, name)
        except (modpack.PackError, LibraryError, OSError) as exc:
            self._fail(title, exc)
            return
        self._select_after_refresh(entry.id)
        self._tell_cloud_problem()

    def delete(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        answer = QMessageBox.question(
            self, "Delete a save",
            f"Delete \"{entry.nickname}\" from the library for good?\n\nIt is not the save in the game, "
            "so the game is not affected - but this copy cannot be brought back. Export it first if "
            "you might want it.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.manager.library.delete(entry.id)
        except (LibraryError, OSError) as exc:
            self._fail("Delete a save", exc)
            return
        self.refresh()

    def _select_after_refresh(self, entry_id: str) -> None:
        self.refresh()
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_ID) == entry_id:
                self.list.setCurrentRow(row)
                break
