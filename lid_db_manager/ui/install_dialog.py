"""The little window that names a mod as it is installed.

Shown for anything dropped on the main window and for the Tools menu import, so
a mod arrives with a real name and description instead of turning up in the list
as "(unknown - .sql only)".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..install import InstallCandidate
from .change_picker import ChangePicker
from .theme import colors


@dataclass
class InstallDetails:
    name: str
    description: str
    author: str
    version: str
    selection: dict | None = None
    split_by_table: bool = False
    companion_of: str | None = None


class InstallDialog(QDialog):
    def __init__(
        self,
        candidate: InstallCandidate,
        dark: bool = True,
        parent=None,
        asset_mods: list[tuple[str, str]] | None = None,
    ):
        super().__init__(parent)
        self.candidate = candidate
        self.asset_mods = list(asset_mods or [])
        self.setWindowTitle("Add a mod")
        self.setMinimumWidth(520)
        palette = colors(dark)

        heading = QLabel(f"Adding <b>{candidate.source.name}</b>")
        heading.setWordWrap(True)

        note = QLabel(candidate.note or "")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {palette['dim']};")

        self.name_edit = QLineEdit(candidate.suggested_name)
        self.name_edit.selectAll()
        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText(
            "Optional - one line describing what it does, shown under the mod in the list."
        )
        self.description_edit.setFixedHeight(64)
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("Optional")
        self.version_edit = QLineEdit("1.0.0")

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Author", self.author_edit)
        form.addRow("Version", self.version_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Add mod")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(note)
        for warning in candidate.warnings:
            label = QLabel(warning)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {palette['pending']};")
            layout.addWidget(label)
        layout.addLayout(form)

        # A database import can be the other half of a game-files mod (a content
        # pack whose .upk were already added). Link them so enabling this one
        # without the files warns.
        self.companion_box: QComboBox | None = None
        if candidate.delta is not None and self.asset_mods:
            self.companion_box = QComboBox()
            self.companion_box.addItem("(none - stand-alone)", None)
            for mod_id, mod_name in self.asset_mods:
                self.companion_box.addItem(f"{mod_name}  ({mod_id})", mod_id)
            companion_form = QFormLayout()
            companion_form.addRow("Companion to game-files mod", self.companion_box)
            layout.addLayout(companion_form)

        # A database import is the one case with something to choose from: it
        # arrives as a lump of changes across many tables, and the player will
        # not want all of it.
        self.picker: ChangePicker | None = None
        self.split_box: QCheckBox | None = None
        if candidate.delta is not None and not candidate.delta.empty:
            self.setMinimumWidth(720)
            chooser_label = QLabel("<b>What to take from it</b>")
            layout.addWidget(chooser_label)
            self.picker = ChangePicker(candidate.delta, self)
            layout.addWidget(self.picker, 1)

            self.split_box = QCheckBox("Install each table as a separate mod")
            self.split_box.setChecked(False)
            self.split_box.setToolTip(
                "Off: one mod, with a switch for each table inside it - one name, one "
                "readme, still switchable part by part.\n"
                "On: a separate mod per table, which you can also reorder against "
                "other mods individually."
            )
            layout.addWidget(self.split_box)
            self.ok_button.setText("Add mods")

        self.footer = QLabel("It will be added switched off, so you can check the diff first.")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet(f"color: {palette['dim']};")
        layout.addWidget(self.footer)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._validate)
        if self.picker is not None:
            self.picker.tree.itemChanged.connect(lambda *_: self._validate())
        self._validate()

    def _validate(self) -> None:
        named = bool(self.name_edit.text().strip())
        # Nothing ticked means there is no mod to write, so say so with the
        # button rather than with an error after the fact.
        chose_something = self.picker is None or bool(self.picker.selection())
        self.ok_button.setEnabled(named and chose_something)

    def details(self) -> InstallDetails:
        return InstallDetails(
            name=self.name_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            author=self.author_edit.text().strip(),
            version=self.version_edit.text().strip() or "1.0.0",
            selection=self.picker.selection() if self.picker else None,
            split_by_table=bool(self.split_box.isChecked()) if self.split_box else True,
            companion_of=self.companion_box.currentData() if self.companion_box else None,
        )
