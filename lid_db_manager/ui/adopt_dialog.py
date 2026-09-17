"""What to do about a database that has already been modded.

Shown once, when the manager first looks at someone's `masters.db` and finds it
is not stock. It says what is in there - which installed mods it recognises, and
how much belongs to nobody - and offers to rebuild the file from the clean copy
plus whatever they tick here.

The rebuild is the honest part. Ticking a mod without it would leave the manager
calling a modded file "vanilla", and every undo from then on would put the wrong
values back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .change_picker import ChangePicker
from .theme import colors

MOD_ID_ROLE = Qt.ItemDataRole.UserRole

DEFAULT_CUSTOM_NAME = "My existing changes"


@dataclass
class AdoptChoice:
    """What the player decided."""

    mod_ids: list[str] = field(default_factory=list)
    make_custom: bool = False
    custom_name: str = DEFAULT_CUSTOM_NAME
    selection: dict | None = None
    # mod id -> the values it was found at, for the ticked mods that have any.
    values: dict = field(default_factory=dict)


class AdoptDialog(QDialog):
    def __init__(self, report, dark: bool = True, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Your database has been modded already")
        self.setMinimumWidth(720)
        palette = colors(dark)

        heading = QLabel("<b>This database is not stock.</b>")
        heading.setWordWrap(True)

        summary = QLabel(report.summary())
        summary.setWordWrap(True)
        summary.setStyleSheet(f"color: {palette['dim']};")

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(summary)

        # -- the mods it recognised ----------------------------------------
        self.tree: QTreeWidget | None = None
        if report.matches:
            layout.addWidget(QLabel("<b>Mods it found in there</b>"))
            self.tree = QTreeWidget()
            self.tree.setColumnCount(2)
            self.tree.setHeaderLabels(["Mod", "What was found"])
            self.tree.setRootIsDecorated(False)
            self.tree.setUniformRowHeights(True)
            self.tree.setMaximumHeight(170)
            for match in report.matches:
                item = QTreeWidgetItem(self.tree)
                item.setData(0, MOD_ID_ROLE, match.mod_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0, Qt.CheckState.Checked if match.applied else Qt.CheckState.Unchecked
                )
                item.setText(0, match.name)
                item.setText(1, match.summary())
                tips = []
                if match.values_unknown:
                    tips.append(
                        "Every value this mod changes has been changed, but not to "
                        "numbers any one setting of it gives - most likely the mod plus "
                        "some edits of your own. Ticking it applies it at the values in "
                        "its Configuration tab; the changes themselves are also listed "
                        "below, where they can be kept as they are."
                    )
                elif not match.applied:
                    tips.append(
                        "Only part of this mod is in the database. Ticking it applies "
                        "the whole thing; leaving it unticked keeps none of it."
                    )
                elif match.values_text:
                    tips.append(
                        f"Found at {match.values_text}, and rebuilt at those values."
                    )
                if match.version_changed:
                    tips.append(
                        f"Your database has version {match.recorded_version} of this "
                        f"mod; version {match.installed_version} is installed, and that "
                        "is the one a rebuild applies."
                    )
                if tips:
                    item.setToolTip(1, "\n\n".join(tips))
            self.tree.resizeColumnToContents(0)
            layout.addWidget(self.tree)
        else:
            none_found = QLabel("None of your installed mods account for any of it.")
            none_found.setStyleSheet(f"color: {palette['dim']};")
            layout.addWidget(none_found)

        if report.not_installed:
            names = ", ".join(entry.name or entry.mod_id for entry in report.not_installed)
            missing = QLabel(
                f"Your database also lists mods that are not installed here: {names}. "
                "Their changes are counted below as belonging to no mod."
            )
            missing.setWordWrap(True)
            missing.setStyleSheet(f"color: {palette['dim']};")
            layout.addWidget(missing)

        # -- everything nobody owns ----------------------------------------
        self.custom_box: QCheckBox | None = None
        self.name_edit: QLineEdit | None = None
        self.picker: ChangePicker | None = None
        if report.has_leftover:
            count = report.leftover.change_count
            layout.addWidget(QLabel("<b>Changes that match no mod</b>"))
            explain = QLabel(
                f"{count:,} change(s) in your database belong to no mod the manager "
                "knows. They can be kept as a mod of your own, which you can then "
                "switch off like any other - or left behind."
            )
            explain.setWordWrap(True)
            explain.setStyleSheet(f"color: {palette['dim']};")
            layout.addWidget(explain)

            self.custom_box = QCheckBox("Keep these as a mod I can switch off")
            self.custom_box.setChecked(True)
            layout.addWidget(self.custom_box)

            self.name_edit = QLineEdit(DEFAULT_CUSTOM_NAME)
            layout.addWidget(self.name_edit)

            self.picker = ChangePicker(report.leftover, self)
            layout.addWidget(self.picker, 1)
            self.custom_box.toggled.connect(self._on_custom_toggled)

        footer = QLabel(
            "Your database is rebuilt from the clean copy plus what you tick here, so "
            "every mod can be switched off again afterwards. The file you have now is "
            "backed up first."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color: {palette['dim']};")
        layout.addWidget(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Rebuild my database")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Leave it alone")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.name_edit is not None:
            self.name_edit.textChanged.connect(self._validate)
        self._validate()

    def _on_custom_toggled(self, on: bool) -> None:
        if self.name_edit is not None:
            self.name_edit.setEnabled(on)
        if self.picker is not None:
            self.picker.setEnabled(on)
        self._validate()

    def _validate(self) -> None:
        # A mod with no name cannot be written, so say so with the button.
        if self.custom_box is not None and self.custom_box.isChecked():
            named = bool(self.name_edit.text().strip()) if self.name_edit else False
            self.ok_button.setEnabled(named)
            return
        self.ok_button.setEnabled(True)

    def choice(self) -> AdoptChoice:
        mod_ids = []
        values = {}
        found = {match.mod_id: match for match in self.report.matches}
        if self.tree is not None:
            for index in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(index)
                if item.checkState(0) == Qt.CheckState.Checked:
                    mod_id = item.data(0, MOD_ID_ROLE)
                    mod_ids.append(mod_id)
                    match = found.get(mod_id)
                    if match is not None and match.values and not match.values_unknown:
                        values[mod_id] = dict(match.values)
        make_custom = bool(self.custom_box is not None and self.custom_box.isChecked())
        return AdoptChoice(
            mod_ids=mod_ids,
            make_custom=make_custom,
            custom_name=(self.name_edit.text().strip() if self.name_edit else ""),
            selection=self.picker.selection() if (self.picker and make_custom) else None,
            values=values,
        )
