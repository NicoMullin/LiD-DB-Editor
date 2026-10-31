"""Mods > Make a radio station...: the player's own .ogg files on the radio.

The window takes a name and a list of songs. Each song gets a channel of its
own, as the game's songs do, shown on the radio as its artist (or the name,
for a song without one). Each song's title and artist start as the file's own
tags (or its file name) and can be typed over.
Every file is checked as it is added - Ogg Vorbis, 44.1 kHz, stereo - and one
that is not is converted by the player's VLC (song_convert.py), off the window's
thread since that takes seconds a song, or, with no VLC, turned away with what
to do about it. Making the station writes
an ordinary mod folder (see radio_maker.py); it is ticked and saved like any
other mod.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..radio_maker import (
    FIRST_CHANNEL,
    MOST_NAME,
    RadioMakerError,
    Song,
    channel_name,
    make_station,
    template_path,
)
from ..song_convert import MUSIC_FILES, VLC_PAGE, add_song, find_vlc
from .workers import TaskThread

COLUMNS = ("Title", "Artist", "Length", "File")
TITLE, ARTIST, LENGTH, FILE = range(len(COLUMNS))


def _length(song: Song) -> str:
    minutes, seconds = divmod(round(song.seconds), 60)
    return f"{minutes}:{seconds:02d}"


class RadioMakerDialog(QDialog):
    def __init__(self, mods_dir: Path, game_root: Path | None, db_path: Path | None, parent=None):
        super().__init__(parent)
        self.mods_dir = mods_dir
        self.game_root = game_root
        self.db_path = db_path
        self.songs: list[Song] = []
        self.made: Path | None = None
        self.vlc = find_vlc()
        self.task: TaskThread | None = None
        self.setWindowTitle("Make a radio station")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Pick your own songs and give them a name. The manager turns them into a mod that "
            "puts them on the in-game radio: each song gets a channel of its own, like the "
            "game's songs, and the radio shows it as the song's artist. The game plays "
            "<b>Ogg Vorbis</b> (.ogg) at <b>44100 Hz, stereo</b> only.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if self.vlc is not None:
            found = ("VLC is installed, so any music file VLC plays (MP3, FLAC, WAV, M4A...) can be "
                     "added: the manager converts it.")
        else:
            found = (f"Only .ogg files that are already right can be added. Install VLC (free, "
                     f"<a href=\"{VLC_PAGE}\">videolan.org</a>) and the manager converts MP3, FLAC, "
                     "WAV and the rest itself.")
        converting = QLabel(found)
        converting.setWordWrap(True)
        converting.setOpenExternalLinks(True)
        layout.addWidget(converting)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(MOST_NAME)
        self.name.setPlaceholderText("The mod's name, and what the radio shows for a song with no artist")
        self.name.textChanged.connect(self._update)
        form.addRow("Name", self.name)
        layout.addLayout(form)
        self.shuffle = QCheckBox("Also play them in OBS Needle Sounds (channel 000), the station that "
                                 "shuffles the game's songs")
        layout.addWidget(self.shuffle)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(ARTIST, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(LENGTH, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(FILE, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(FILE, 200)
        self.table.itemChanged.connect(self._edited)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        for text, slot in (("Add songs...", self._add), ("Remove", self._remove),
                           ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(+1))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.make_button = box.addButton("Make the mod", QDialogButtonBox.ButtonRole.AcceptRole)
        box.accepted.connect(self._make)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._fill()

    # -- the list -----------------------------------------------------------

    def _fill(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.songs))
        for row, song in enumerate(self.songs):
            for column, text in ((TITLE, song.title), (ARTIST, song.artist),
                                 (LENGTH, _length(song)), (FILE, song.path.name)):
                item = QTableWidgetItem(text)
                if column in (LENGTH, FILE):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == FILE:
                    item.setToolTip(str(song.path))
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self._update()

    def _edited(self, item: QTableWidgetItem) -> None:
        song = self.songs[item.row()]
        text = " ".join(item.text().split())
        if item.column() == TITLE:
            song.title = text or song.path.stem
        elif item.column() == ARTIST:
            song.artist = text

    def _add(self) -> None:
        kinds = f"Music ({MUSIC_FILES})" if self.vlc is not None else "Ogg Vorbis (*.ogg)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Pick songs", "", f"{kinds};;All files (*)")
        if not paths or self.task is not None:
            return
        stop = threading.Event()
        vlc = self.vlc

        def work(progress):
            added, refused = [], []
            for done, path in enumerate(paths):
                if stop.is_set():
                    break
                progress.step(done, len(paths), f"Adding {Path(path).name} ({done + 1} of {len(paths)})...")
                try:
                    added.append(add_song(path, vlc))
                except (RadioMakerError, OSError) as exc:
                    refused.append(str(exc))
            return added, refused

        waiting = QProgressDialog("Adding songs...", "Stop", 0, 100, self)
        waiting.setWindowTitle("Make a radio station")
        waiting.setWindowModality(Qt.WindowModality.WindowModal)
        waiting.setMinimumDuration(0)
        waiting.setAutoClose(False)
        waiting.setAutoReset(False)
        waiting.setMinimumWidth(420)
        waiting.canceled.connect(stop.set)
        waiting.setValue(0)

        def report(text: str, value: int) -> None:
            waiting.setLabelText(text + ("\n\nA song that is not .ogg yet is converted by VLC."
                                         if vlc is not None else ""))
            waiting.setValue(value)

        def finished(result, trace: str = "") -> None:
            self.task = None
            waiting.close()
            waiting.deleteLater()
            if trace:
                QMessageBox.warning(self, "The songs were not added", trace.strip().splitlines()[-1])
                return
            added, refused = result
            self.songs.extend(added)
            self._fill()
            if refused:
                QMessageBox.warning(self, "Some songs were not added", "\n\n".join(refused))

        self.task = TaskThread(work, self, reports_progress=True)
        self.task.progressed.connect(report)
        self.task.succeeded.connect(finished)
        self.task.failed.connect(lambda trace: finished(None, trace))
        self.task.start()

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _remove(self) -> None:
        for row in reversed(self._selected_rows()):
            del self.songs[row]
        self._fill()

    def _move(self, step: int) -> None:
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        row, to = rows[0], rows[0] + step
        if 0 <= to < len(self.songs):
            self.songs[row], self.songs[to] = self.songs[to], self.songs[row]
            self._fill()
            self.table.selectRow(to)

    def _update(self) -> None:
        name = " ".join(self.name.text().split())
        count = len(self.songs)
        if not count:
            self.summary.setText("No songs yet - click Add songs....")
        else:
            shown = sorted({channel_name(song, name or "your name") for song in self.songs})
            self.summary.setText(
                f"{count} song(s), each on a channel of its own from {FIRST_CHANNEL} up (the next "
                f"free ones). The radio shows them as: {', '.join(shown)}.")
        self.make_button.setEnabled(bool(name) and bool(count))

    def reject(self) -> None:
        if self.task is None:        # not while songs are being added
            super().reject()

    # -- making it -----------------------------------------------------------

    def _make(self) -> None:
        if self.game_root is None:
            QMessageBox.warning(self, "Game folder not known",
                                "Choose your masters.db first, so the manager knows where the game is.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.made = make_station(self.mods_dir, self.name.text(), self.songs,
                                     template_path(self.game_root), self.db_path,
                                     shuffle=self.shuffle.isChecked())
        except (RadioMakerError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "The station was not made", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.accept()
