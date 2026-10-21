"""Which mods a save is played with, asked when none are noted for it.

Shown when a .sav comes in from elsewhere, and before the editor opens a save
with no mod list. The save is checked against the clean game first
(savecheck.py) and what it shows signs of is offered; the player can also say
it had no mods, the mods applied here now, or one of their mod packs. The
answer is noted for the save, so it is asked once - until Save Mod List with
the save in the game notes the real list over it.
"""

from __future__ import annotations

import copy
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from .. import savelink

CHOICE_FOUND, CHOICE_NONE, CHOICE_APPLIED, CHOICE_PACK = "found", "none", "applied", "pack"


class SaveModsDialog(QDialog):
    def __init__(self, manager, entry_id: str, parent=None, lead: str = ""):
        super().__init__(parent)
        self.manager = manager
        self.entry = manager.library.get(entry_id)
        self.noted = None                       # the entry, once a list is noted
        self.setWindowTitle(f"Which mods is \"{self.entry.nickname}\" played with?")
        self.resize(560, 0)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                self.found = manager.check_save(entry_id)
            except Exception as exc:  # an unreadable save is said, not a crash
                self.found, problem = None, str(exc)
            else:
                problem = ("" if self.found is not None
                           else "there is no clean copy of the game's data to check against")
        finally:
            QApplication.restoreOverrideCursor()

        intro = QLabel((f"{html.escape(lead)}<br><br>" if lead else "")
                       + f"No mod list is noted for <b>{html.escape(self.entry.nickname)}</b>. Only mods that "
                       "leave something in a save matter to it - new parts, decals, items or quests, or bigger "
                       "banks - so the save was checked against the clean game for them.")
        intro.setWordWrap(True)
        if self.found is None:
            findings = f"It could not be checked: {html.escape(problem)}."
        elif self.found.clean:
            findings = ("<b>Nothing in it needs a mod.</b> It may still have been played with mods that "
                        "left nothing in it, which changes nothing here.")
        else:
            findings = "<b>It shows signs of:</b><br>" + "<br>".join(
                f"&nbsp;&nbsp;- {html.escape(line)}" for line in self.found.lines())
            if self.found.unexplained:
                findings += ("<br><br>A mod this PC does not have explains some of that (or another save "
                             "editor did): its things cannot be given in the editor until it is installed.")
        found_label = QLabel(findings)
        found_label.setWordWrap(True)
        found_label.setTextFormat(Qt.TextFormat.RichText)

        self.group = QButtonGroup(self)
        self.options: dict[str, QRadioButton] = {}
        found_mods = self.found.found_mods if self.found is not None else []
        if found_mods:
            self._option(CHOICE_FOUND, f"What was found: {', '.join(found_mods)}")
        self._option(CHOICE_NONE, "No mods - the stock game")
        applied = manager.state.applied_choice.get("enabled_mods") if manager.state.applied_choice else None
        if applied:
            self._option(CHOICE_APPLIED, f"The mods applied here now ({len(applied)})")
        self.packs = manager.modpacks()
        self.pack_box = QComboBox()
        for info in self.packs:
            self.pack_box.addItem(info.name, info.name)
        pack_row = None
        if self.packs:
            pack_row = QHBoxLayout()
            pack_row.addWidget(self._option(CHOICE_PACK, "A mod pack:"))
            pack_row.addWidget(self.pack_box, 1)
            self.pack_box.currentIndexChanged.connect(lambda *_: self.options[CHOICE_PACK].setChecked(True))
        (self.options[CHOICE_FOUND] if found_mods else self.options[CHOICE_NONE]).setChecked(True)

        buttons = QDialogButtonBox()
        note = buttons.addButton("Note it", QDialogButtonBox.ButtonRole.AcceptRole)
        note.setDefault(True)
        later = buttons.addButton("Not now", QDialogButtonBox.ButtonRole.RejectRole)
        later.setToolTip("Nothing is noted; the editor uses the stock limits, which hold in every game.")
        buttons.accepted.connect(self.note)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(found_label)
        for key, button in self.options.items():
            if key != CHOICE_PACK:
                layout.addWidget(button)
        if pack_row is not None:
            layout.addLayout(pack_row)
        hint = QLabel("Noted for this save only. Put it in the game and Save Mod List with its mods ticked, "
                      "and the real list is noted over it.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _option(self, key: str, text: str) -> QRadioButton:
        button = QRadioButton(text)
        self.group.addButton(button)
        self.options[key] = button
        return button

    def picked(self) -> str:
        return next((key for key, button in self.options.items() if button.isChecked()), CHOICE_NONE)

    def choice(self) -> tuple[dict, str]:
        """(the mod list picked, how it came to be known)."""
        picked = self.picked()
        if picked == CHOICE_FOUND:
            return self.manager.found_mods_choice(self.found), "found"
        if picked == CHOICE_APPLIED:
            return copy.deepcopy(self.manager.state.applied_choice), "chosen"
        if picked == CHOICE_PACK:
            info = self.manager.modpack(self.pack_box.currentData())
            if info.packed:
                return copy.deepcopy(info.pack.choice), "chosen"
            return savelink.trim_choice({**self.manager.mod_choice(), "enabled_mods": info.mod_ids}), "chosen"
        return {"enabled_mods": []}, "chosen"

    def note(self) -> None:
        choice, how = self.choice()
        self.noted = self.manager.note_save_mods(self.entry.id, choice, how)
        self.accept()
