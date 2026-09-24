"""Editing a mod's details without leaving the program.

Renaming a mod, fixing a typo in its description, writing its readme - all of it
otherwise means finding the folder and opening a text editor, which is a lot to
ask of someone who just wants their mod called something sensible.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..browse import SUGGESTED_CATEGORIES, clean_category, clean_tags
from ..mod import Mod
from ..modedit import safe_folder_name
from .theme import colors


@dataclass
class EditDetails:
    name: str
    description: str
    author: str
    version: str
    readme: str
    category: str
    tags: list[str]


class EditModDialog(QDialog):
    def __init__(self, mod: Mod, dark: bool = True, parent=None, known_categories=None):
        super().__init__(parent)
        self.mod = mod
        self.setWindowTitle(f"Edit {mod.name}")
        self.setMinimumWidth(560)
        palette = colors(dark)

        self.name_edit = QLineEdit(mod.name)
        self.description_edit = QPlainTextEdit(mod.description)
        self.description_edit.setFixedHeight(60)
        self.author_edit = QLineEdit(mod.author)
        self.version_edit = QLineEdit(mod.version)

        # Editable, because the suggestions are suggestions: a category nobody
        # here thought of is written through exactly as typed.
        self.category_box = QComboBox()
        self.category_box.setEditable(True)
        self.category_box.addItem("")
        for name in SUGGESTED_CATEGORIES:
            self.category_box.addItem(name)
        for name in sorted(known_categories or ()):
            if self.category_box.findText(name) < 0:
                self.category_box.addItem(name)
        self.category_box.setCurrentText(mod.category)
        self.category_box.lineEdit().setPlaceholderText(
            "Optional - leave empty to file it under Uncategorised"
        )

        self.tags_edit = QLineEdit(", ".join(mod.tags))
        self.tags_edit.setPlaceholderText("coins, storage, prices")
        self.tags_edit.setToolTip(
            "Separated by commas. Used by the search box and the Tags filter "
            "above the mod list.\n\n"
            "Tidied to one spelling as they are saved - “Coin Locker” "
            "becomes “coin-locker” - so the same tag on two mods is "
            "always the same tag."
        )
        self.tags_note = QLabel()
        self.tags_note.setObjectName("dim")
        self.tags_note.setStyleSheet(f"color: {palette['dim']};")
        self.tags_edit.textChanged.connect(self._show_tags)
        self._show_tags()

        readme = mod.readme
        self.readme_edit = QPlainTextEdit(
            readme.read_text(encoding="utf-8-sig") if readme else ""
        )
        self.readme_edit.setPlaceholderText(
            "Optional. Markdown, shown on the Readme tab beside the diff. "
            "Leave this empty to remove the file."
        )
        self.readme_edit.setMinimumHeight(160)

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Author", self.author_edit)
        form.addRow("Version", self.version_edit)
        form.addRow("Category", self.category_box)
        form.addRow("Tags", self.tags_edit)
        form.addRow("", self.tags_note)
        form.addRow("Readme", self.readme_edit)

        self.folder_note = QLabel()
        self.folder_note.setWordWrap(True)
        self.folder_note.setStyleSheet(f"color: {palette['dim']};")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save changes")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if mod.source != "mod.json":
            hint = QLabel(
                "This mod has no <code>mod.json</code> yet - saving will write one, "
                "so it stops showing as “(unknown - .sql only)”."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {palette['dim']};")
            layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.folder_note)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        name = self.name_edit.text().strip()
        self.ok_button.setEnabled(bool(name))
        new_id = safe_folder_name(name) if name else ""
        if not name:
            self.folder_note.setText("A mod needs a name.")
        elif new_id == self.mod.id:
            self.folder_note.setText(f"Folder: mods/{self.mod.id}")
        else:
            # Say it plainly - the folder moving is the surprising part, and it
            # takes the load-order slot and the revert snapshot with it.
            self.folder_note.setText(
                f"The folder will be renamed from “{self.mod.id}” to “{new_id}”. "
                "Its place in the load order and its undo history move with it."
            )

    def _show_tags(self) -> None:
        """Say what will be saved, but only when it is not what was typed.

        Repeating "coins, storage" back at somebody who typed "coins, storage"
        is noise; showing them that "Coin Locker" becomes "coin-locker" is the
        whole point.
        """
        tags = clean_tags(self.tags_edit.text())
        typed = [part.strip() for part in self.tags_edit.text().split(",") if part.strip()]
        self.tags_note.setText(
            "Saved as: " + ", ".join(tags) if tags and tags != typed else ""
        )

    def details(self) -> EditDetails:
        return EditDetails(
            name=self.name_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            author=self.author_edit.text().strip(),
            version=self.version_edit.text().strip(),
            readme=self.readme_edit.toPlainText(),
            category=clean_category(self.category_box.currentText()),
            tags=clean_tags(self.tags_edit.text()),
        )
