"""One value a mod lets the player choose, as a labelled number box."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..settings import KIND_INTEGER, ModSetting


class SettingEditor(QWidget):
    """A number box for one setting, with its limits, help and a Default button.

    A value is handed on when editing *finishes* - Enter, or clicking away -
    not on every keystroke: typing "1000" must not pass through 1, 10 and 100,
    rebuilding the panel and throwing the cursor out of the box each time.
    """

    committed = Signal(object)  # the new value
    finished = Signal()  # editing ended, whether or not anything changed

    def __init__(self, setting: ModSetting, value, dim_color: str = "", parent=None):
        super().__init__(parent)
        self.setting = setting
        self._committed_value = value

        name = QLabel(f"<b>{setting.label}</b>")
        if setting.kind == KIND_INTEGER:
            spin = QSpinBox()
            spin.setRange(int(setting.minimum), int(setting.maximum))
            spin.setSingleStep(int(setting.step))
        else:
            spin = QDoubleSpinBox()
            decimals = len(str(setting.step).split(".")[1]) if "." in str(setting.step) else 2
            spin.setDecimals(min(decimals, 6))
            spin.setRange(float(setting.minimum), float(setting.maximum))
            spin.setSingleStep(float(setting.step))
        spin.setGroupSeparatorShown(True)
        unit = setting.unit.strip()
        if unit.lower() == "x":
            spin.setPrefix("x")
        elif unit == "%":
            spin.setSuffix("%")
        elif unit:
            spin.setSuffix(f" {unit}")
        spin.setValue(value)
        spin.setMinimumWidth(150)
        spin.setAccelerated(True)

        dim = f"color: {dim_color};" if dim_color else ""
        limits = QLabel(
            f"{setting.display(setting.minimum)} to {setting.display(setting.maximum)}"
            f" &nbsp;·&nbsp; default {setting.display(setting.default)}"
        )
        limits.setStyleSheet(dim)

        self.reset_button = QPushButton("Default")
        self.reset_button.setToolTip(f"Back to {setting.display(setting.default)}")
        self.reset_button.setEnabled(value != setting.default)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setHorizontalSpacing(8)
        layout.addWidget(name, 0, 0, 1, 3)
        layout.addWidget(spin, 1, 0)
        layout.addWidget(self.reset_button, 1, 1)
        layout.setColumnStretch(2, 1)
        layout.addWidget(limits, 2, 0, 1, 3)
        if setting.help:
            help_text = QLabel(setting.help)
            help_text.setWordWrap(True)
            help_text.setStyleSheet(dim)
            layout.addWidget(help_text, 3, 0, 1, 3)

        self.spin = spin
        spin.editingFinished.connect(self._commit)
        self.reset_button.clicked.connect(self._reset)

    def value(self):
        number = self.spin.value()
        return int(number) if self.setting.kind == KIND_INTEGER else float(number)

    def pending(self) -> bool:
        """Changed in the box but not yet handed on."""
        return self.value() != self._committed_value

    def mark_committed(self) -> None:
        self._committed_value = self.value()

    def _commit(self) -> None:
        if self.pending():
            self._committed_value = self.value()
            self.committed.emit(self._committed_value)
        self.finished.emit()

    def _reset(self) -> None:
        self.spin.setValue(self.setting.default)
        self._commit()
