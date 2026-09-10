"""The little window that names a mod as it is installed.

Shown for anything dropped on the main window and for the Tools menu import, so
a mod arrives with a real name and description instead of turning up in the list
as "(unknown - .sql only)".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..install import InstallCandidate
from .theme import colors


@dataclass
class InstallDetails:
    name: str
    description: str
    author: str
    version: str


class InstallDialog(QDialog):
    def __init__(self, candidate: InstallCandidate, dark: bool = True, parent=None):
        super().__init__(parent)
        self.candidate = candidate
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

        self.footer = QLabel("It will be added switched off, so you can check the diff first.")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet(f"color: {palette['dim']};")
        layout.addWidget(self.footer)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        self.ok_button.setEnabled(bool(self.name_edit.text().strip()))

    def details(self) -> InstallDetails:
        return InstallDetails(
            name=self.name_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            author=self.author_edit.text().strip(),
            version=self.version_edit.text().strip() or "1.0.0",
        )
