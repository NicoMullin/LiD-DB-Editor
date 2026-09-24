"""The Hash Patcher: switching the game's own file check off, from in here.

The game refuses a replaced package at startup unless the hash it carries for
that package still matches. The manager already says so when a mod is blocked -
this is what lets somebody do something about it without leaving for another
program.

Every file the game checks is listed, so any of them can be found and switched
off; the ones an enabled mod needs are put at the top and ticked already.
Switching the whole check off is a separate button with its own warning,
because it is a far larger thing to do to somebody's game.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

NAME_ROLE = Qt.ItemDataRole.UserRole  # the package name, without any label

# How long after the last keystroke the list is filtered.
TYPING_PAUSE_MS = 220


class FileCheckDialog(QDialog):
    """Read what the game checks, and switch it off for chosen packages."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        # Read once per refresh and kept, because reading it is not cheap.
        # Nothing reads these before refresh() has run, at the end of __init__.
        self.status = None
        self._ticked_names: set[str] = set()
        self.setWindowTitle("Hash Patcher")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)

        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        explain = QLabel(
            "The game keeps a list of files with the checksum it expects each "
            "one to have, and refuses a replacement at startup. A file that is "
            "not on the list is never checked, so taking a file off it makes "
            "that file freely replaceable.\n\n"
            "This changes one byte per file, inside that list. No program code "
            "is touched, and it can be put back at any time."
        )
        explain.setWordWrap(True)
        explain.setObjectName("dim")
        layout.addWidget(explain)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search for a file...")
        self.search.setClearButtonEnabled(True)
        # Each keystroke re-hides 8,000 rows, so it waits for typing to stop -
        # the same pause the mod list's filter bar uses.
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(TYPING_PAUSE_MS)
        self._typing.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _text: self._typing.start())
        self.search.returnPressed.connect(self._apply_now)
        layout.addWidget(self.search)

        self.list = QListWidget()
        # Every row is one line of text with a box, so they are all the same
        # height. Saying so means the view stops measuring 8,000 rows to work
        # out where the scrollbar goes - which it did on every build and every
        # time a search was cleared.
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list, 1)

        self.shown = QLabel()
        self.shown.setObjectName("dim")
        layout.addWidget(self.shown)

        buttons = QHBoxLayout()
        self.off_button = QPushButton("Switch off for the ticked files")
        self.off_button.clicked.connect(self._switch_off)
        buttons.addWidget(self.off_button)
        self.all_button = QPushButton("Switch off for everything...")
        self.all_button.clicked.connect(self._switch_off_everything)
        buttons.addWidget(self.all_button)
        self.on_button = QPushButton("Put it all back")
        self.on_button.clicked.connect(self._switch_on)
        buttons.addWidget(self.on_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.refresh()

    # -- reading ------------------------------------------------------------

    def refresh(self) -> None:
        """Read the executable again and rebuild the list from it.

        The one place that reads it. Asking the manager for the status means
        reading and parsing the whole 45 MB executable - about 26 ms - so the
        answer is kept on ``self.status`` and everything below reads that. It
        used to be asked again on every tick and every keystroke.
        """
        from ..exe_check_off import original_name

        self.status = status = self.manager.file_check_status()
        self.list.blockSignals(True)
        self.list.clear()
        if not status.readable:
            self.summary.setText(f"Nothing can be said about it: {status.reason}.")
            self.shown.setText("")
            self.list.blockSignals(False)
            self._refresh_buttons(readable=False)
            return

        self._ticked_names.clear()
        off = {original_name(name) for name in status.switched_off}
        blocked = {name.lower() for name in self.manager.blocked_packages()}
        checked = set(status.checked)

        self.summary.setText(
            f"The game checks {len(checked)} file(s). "
            + (
                f"{len(off)} have been switched off."
                if off
                else "None have been switched off."
            )
        )

        # Whatever a mod is waiting on first, then what has already been done,
        # then the rest - so the reason for opening this is never scrolled to.
        for name in sorted(blocked & checked):
            self._add(name, "a mod you have enabled replaces this", ticked=True)
        for name in sorted(off):
            self._add(name, "already switched off", ticked=False, checkable=False)
        for name in sorted(checked - blocked):
            self._add(name, "", ticked=False)
        self.list.blockSignals(False)
        self._apply_filter()

    def _add(self, name: str, why: str, *, ticked: bool, checkable: bool = True) -> None:
        item = QListWidgetItem(f"{name}   - {why}" if why else name)
        item.setData(NAME_ROLE, name)
        if checkable:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked
            )
            if ticked:
                # Signals are blocked while the list fills, so itemChanged will
                # not fire for this and the set has to be told here.
                self._ticked_names.add(name)
        else:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.list.addItem(item)

    def _apply_filter(self) -> None:
        """Hide what does not match, rather than rebuilding.

        A tick is a choice, and rebuilding would throw away any made before the
        search was typed - including ones scrolled out of sight.
        """
        wanted = self.search.text().strip().lower()
        visible = 0
        for row in range(self.list.count()):
            item = self.list.item(row)
            name = item.data(NAME_ROLE) or ""
            hide = bool(wanted) and wanted not in name
            item.setHidden(hide)
            visible += not hide
        total = self.list.count()
        self.shown.setText(
            f"Showing {visible} of {total}" if wanted else f"{total} file(s)"
        )
        self._refresh_buttons()

    def _apply_now(self) -> None:
        """Filter without waiting out the typing pause - Enter means now."""
        self._typing.stop()
        self._apply_filter()

    def _refresh_buttons(self, readable: bool = True) -> None:
        """Nothing here reads the executable or walks the list - see refresh."""
        if not readable or self.status is None:
            for button in (self.off_button, self.all_button, self.on_button):
                button.setEnabled(False)
            self.off_button.setText("Switch off for the ticked files")
            return
        count = len(self._ticked_names)
        self.off_button.setEnabled(bool(count))
        self.off_button.setText(
            f"Switch off for the ticked files ({count})"
            if count
            else "Switch off for the ticked files"
        )
        self.on_button.setEnabled(bool(self.status.switched_off))
        self.all_button.setEnabled(bool(self.status.checked))

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Keep the ticked set in step with one box, and nothing else.

        Recounting meant walking every row - 8,000 of them on a real game, about
        19 ms - for a change that names the one row that moved.
        """
        name = item.data(NAME_ROLE)
        if not name:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._ticked_names.add(name)
        else:
            self._ticked_names.discard(name)
        self._refresh_buttons()

    def _ticked(self) -> list[str]:
        """Every ticked file, whether the search is hiding it or not."""
        return sorted(self._ticked_names)

    # -- writing ------------------------------------------------------------

    def apply_change(self, what) -> tuple[list[str], str]:
        """Carry the change out and read the list again.

        Returns what changed and why it did not, if it did not. Kept apart from
        the message boxes so the writing can be driven - and tested - without a
        window manager to click them.
        """
        try:
            done = what()
        except (RuntimeError, OSError, ValueError) as exc:
            return [], str(exc)
        self.refresh()
        return done, ""

    def _run(self, what, describe: str) -> None:
        done, problem = self.apply_change(what)
        if problem:
            QMessageBox.warning(self, "Hash Patcher", problem)
            return
        QMessageBox.information(
            self,
            "Hash Patcher",
            f"{describe} {len(done)} file(s)." if done else "Nothing needed changing.",
        )

    def _switch_off(self) -> None:
        names = self._ticked()
        if not names:
            return
        self._run(
            lambda: self.manager.switch_file_check_off(names),
            "The game no longer checks",
        )

    def _switch_off_everything(self) -> None:
        status = self.status  # the read refresh() already paid for
        if status is None or not status.readable:
            return
        confirmed = QMessageBox.question(
            self,
            "Switch the check off for everything?",
            f"This takes all {len(status.checked)} files off the list, not just "
            "the ones your mods need.\n\n"
            "Every package in the game becomes replaceable by anything, with "
            "nothing left to tell you when a file is not what the game expects. "
            "A copy of the executable is kept first, and 'Put it all back' "
            "undoes this exactly.\n\n"
            "Switch the check off for everything?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._run(
            lambda: self.manager.switch_file_check_off(status.checked),
            "The game no longer checks",
        )

    def _switch_on(self) -> None:
        self._run(lambda: self.manager.switch_file_check_on(), "The game checks")
