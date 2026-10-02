"""One value a mod lets the player choose: a number box, an on/off, or a list."""

from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..settings import KIND_CHOICE, KIND_INTEGER, KIND_TOGGLE, ModSetting

# A list longer than this gets a box to type into, matching anywhere in a name:
# nobody scrolls through three hundred decals to find "Joker".
SEARCHABLE_FROM = 12


class SettingEditor(QWidget):
    """The input for one setting, with its limits, help and a Default button.

    A value is handed on when editing *finishes* - Enter, or clicking away -
    not on every keystroke: typing "1000" must not pass through 1, 10 and 100,
    rebuilding the panel and throwing the cursor out of the box each time. An
    on/off or a pick from the list is finished as soon as it is made.
    """

    committed = Signal(object)  # the new value
    finished = Signal()  # editing ended, whether or not anything changed

    def __init__(self, setting: ModSetting, value, dim_color: str = "", parent=None):
        super().__init__(parent)
        self.setting = setting
        self._committed_value = value
        self._dim = f"color: {dim_color};" if dim_color else ""
        self.spin = None  # the number box, for number settings
        self.combo: QComboBox | None = None
        self.check: QCheckBox | None = None
        self._option_help: QLabel | None = None

        # Bold through the stylesheet rather than <b>, and plain text
        # everywhere else. A label built by interpolating a mod's own words into
        # markup has two problems: Qt only treats a string as rich text when it
        # spots something that looks like a tag, so "&nbsp;" with no tag beside
        # it was printed literally; and a mod could otherwise put markup of its
        # own - including an <img> Qt would go and fetch - into the panel.
        name = QLabel(setting.label)
        name.setObjectName("settingName")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)

        if setting.kind == KIND_TOGGLE:
            self.input = self._build_toggle(value)
        elif setting.kind == KIND_CHOICE:
            self.input = self._build_choice(value)
        else:
            self.input = self._build_number(value)

        self.reset_button = QPushButton("Default")
        self.reset_button.setToolTip(f"Back to {setting.display(setting.default)}")
        self.reset_button.setEnabled(value != setting.default)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setHorizontalSpacing(8)
        row = 0
        if setting.kind != KIND_TOGGLE:
            # An on/off carries its own name beside the tick.
            layout.addWidget(name, row, 0, 1, 3)
            row += 1
        layout.addWidget(self.input, row, 0)
        layout.addWidget(self.reset_button, row, 1)
        layout.setColumnStretch(2, 1)
        row += 1
        if setting.is_number:
            limits = self._dim_label(
                f"{setting.display(setting.minimum)} to {setting.display(setting.maximum)}"
                f"  ·  default {setting.display(setting.default)}"
            )
            layout.addWidget(limits, row, 0, 1, 3)
            row += 1
        if self._option_help is not None:
            layout.addWidget(self._option_help, row, 0, 1, 3)
            row += 1
        if setting.help:
            layout.addWidget(self._dim_label(setting.help), row, 0, 1, 3)

        self.reset_button.clicked.connect(self._reset)

    # -- building ------------------------------------------------------------

    def _dim_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet(self._dim)
        return label

    def _build_number(self, value) -> QWidget:
        setting = self.setting
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
        spin.editingFinished.connect(self._commit)
        self.spin = spin
        return spin

    def _build_toggle(self, value) -> QWidget:
        check = QCheckBox(self.setting.label)
        check.setObjectName("settingName")
        check.setChecked(bool(value))
        check.toggled.connect(lambda _on: self._commit())
        self.check = check
        return check

    def _build_choice(self, value) -> QWidget:
        combo = QComboBox()
        combo.setMinimumWidth(260)
        combo.setMaxVisibleItems(20)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(28)
        model = combo.model()
        heading_font = QFont()
        heading_font.setBold(True)
        group = None
        for option in self.setting.options:
            if option.group and option.group != group:
                # A heading in the list: shown, never chosen.
                combo.addItem(option.group)
                heading = model.item(combo.count() - 1)
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                heading.setFont(heading_font)
            group = option.group or group
            combo.addItem(option.label, option.value)
        self.combo = combo
        combo.setCurrentIndex(self._index_of(value))

        if len(self.setting.options) > SEARCHABLE_FROM:
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            completer = QCompleter(QStringListModel([o.label for o in self.setting.options], combo), combo)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            combo.setCompleter(completer)
            completer.activated.connect(self._pick_label)
            combo.lineEdit().editingFinished.connect(self._typed)
            combo.lineEdit().setPlaceholderText("Type to search")

        self._option_help = self._dim_label("")
        combo.currentIndexChanged.connect(lambda _index: self._show_option_help())
        combo.activated.connect(lambda _index: self._commit())
        self._show_option_help()
        return combo

    # -- the list ------------------------------------------------------------

    def _index_of(self, value) -> int:
        index = self.combo.findData(value)
        return index if index >= 0 else self.combo.findData(self.setting.default)

    def _show_option_help(self) -> None:
        option = self.setting.option(self.combo.currentData())
        text = option.help if option is not None else ""
        self._option_help.setText(text)
        self._option_help.setVisible(bool(text))

    def _pick_label(self, label: str) -> None:
        index = self.combo.findText(label, Qt.MatchFlag.MatchFixedString)
        if index >= 0 and self.combo.itemData(index) is not None:
            self.combo.setCurrentIndex(index)
            self._commit()

    def _typed(self) -> None:
        """Enter or clicking away: take what was typed if it names an option,
        otherwise put back the one that was chosen."""
        text = self.combo.lineEdit().text().strip()
        index = self.combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if index >= 0 and self.combo.itemData(index) is not None:
            self.combo.setCurrentIndex(index)
        else:
            self.combo.setCurrentIndex(self._index_of(self._committed_value))
            self.combo.setEditText(self.setting.display(self._committed_value))
        self._commit()

    # -- what the panel asks of every editor ---------------------------------

    def value(self):
        if self.check is not None:
            return int(self.check.isChecked())
        if self.combo is not None:
            data = self.combo.currentData()
            return data if data is not None else self._committed_value
        number = self.spin.value()
        return int(number) if self.setting.kind == KIND_INTEGER else float(number)

    def set_value(self, value) -> None:
        if self.check is not None:
            self.check.setChecked(bool(value))
        elif self.combo is not None:
            self.combo.setCurrentIndex(self._index_of(value))
        else:
            self.spin.setValue(value)

    def show_value(self, value) -> None:
        """Show ``value`` as the one chosen, without it counting as a new choice."""
        self.input.blockSignals(True)
        try:
            self.set_value(value)
        finally:
            self.input.blockSignals(False)
        self._committed_value = value
        self.reset_button.setEnabled(value != self.setting.default)
        if self.combo is not None:
            if self.combo.isEditable():
                self.combo.setEditText(self.setting.display(value))
            self._show_option_help()

    def has_focus(self) -> bool:
        if self.combo is not None and self.combo.isEditable():
            return self.combo.hasFocus() or self.combo.lineEdit().hasFocus()
        return self.input.hasFocus()

    def focus(self) -> None:
        self.input.setFocus()
        if self.spin is not None:
            self.spin.selectAll()
        elif self.combo is not None and self.combo.isEditable():
            self.combo.lineEdit().selectAll()

    def block_signals(self) -> None:
        """Silence it for good - it is about to be thrown away."""
        self.blockSignals(True)
        self.input.blockSignals(True)
        if self.combo is not None and self.combo.isEditable():
            self.combo.lineEdit().blockSignals(True)
            self.combo.completer().blockSignals(True)

    def pending(self) -> bool:
        """Changed in the box but not yet handed on."""
        return self.value() != self._committed_value

    def mark_committed(self) -> None:
        self._committed_value = self.value()

    def reset_to_default(self) -> None:
        self._reset()

    def _commit(self) -> None:
        if self.pending():
            self._committed_value = self.value()
            # Here, not left to the panel: it does not touch a box that still
            # has the cursor in it, and a list keeps the cursor after a pick.
            self.reset_button.setEnabled(self._committed_value != self.setting.default)
            self.committed.emit(self._committed_value)
        self.finished.emit()

    def _reset(self) -> None:
        # Quietly for a tick box, so it commits once rather than twice.
        if self.check is not None:
            self.check.blockSignals(True)
        self.set_value(self.setting.default)
        if self.check is not None:
            self.check.blockSignals(False)
        self._commit()
