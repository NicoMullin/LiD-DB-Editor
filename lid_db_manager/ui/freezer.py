"""The save editor's "Buy a fighter" window (saveroster.py does the work).

What the Fighter Depot sells: a name, a class and grade, a model and gas mask, and the
stat bonuses the game rolls for a bought fighter - shown, and rolled again on request.
The new fighter's ids and time are settled here, so every replay of the step makes the
same fighter (and its Death Bag can be filled in the same session).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import savefighters, saveroster, savestorage
from ..savesummary import STATS


@dataclass(frozen=True)
class Bought:
    name: str
    kind: str
    grade: int
    body: str
    gasmask: str
    bonus: dict
    room: int
    cid: str
    eids: tuple
    now: int


class BuyDialog(QDialog):
    def __init__(self, rules: "savefighters.Rules", catalog: "savestorage.Catalog", room_extra: int, parent=None):
        super().__init__(parent)
        self.rules, self.catalog, self.room_extra = rules, catalog, room_extra
        self.setWindowTitle("Buy a fighter")
        self.bonus: dict = {}

        self.name = QLineEdit("New Fighter")
        self.name.setMaxLength(savefighters.NAME_MAX)
        self.kind = QComboBox()
        for code in rules.classes():
            self.kind.addItem(rules.names.class_name(code), code)
        self.grade = QSpinBox()
        self.body = QComboBox()
        for body, _ in rules.bodies:
            self.body.addItem(savefighters.body_label(body, rules), body)
        self.mask = QComboBox()
        self.bonus_label = QLabel()
        self.bonus_label.setWordWrap(True)
        roll = QPushButton("Roll again")
        roll.setToolTip("Roll the six bonuses again, with the game's odds")
        roll.clicked.connect(self._roll)
        bonus_row = QHBoxLayout()
        bonus_row.addWidget(self.bonus_label, 1)
        bonus_row.addWidget(roll)
        self.gear = QLabel()
        self.gear.setObjectName("dim")
        self.gear.setWordWrap(True)

        self.kind.currentIndexChanged.connect(self._kind_picked)
        self.grade.valueChanged.connect(self._roll)
        self.body.currentIndexChanged.connect(self._fill_masks)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Class:", self.kind)
        form.addRow("Grade:", self.grade)
        form.addRow("Model:", self.body)
        form.addRow("Gas mask:", self.mask)
        form.addRow("Stat bonuses:", bonus_row)
        note = QLabel("They go into the first empty hanger at level 1, as the Fighter Depot sells them, with their "
                      "class's gas mask on. The price is not charged. Change their stats on the Stats tab once they "
                      "are bought.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Buy")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.gear)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self._kind_picked()
        self._fill_masks()

    def _kind_picked(self) -> None:
        kind = self.kind.currentData()
        self.grade.blockSignals(True)
        self.grade.setRange(1, self.rules.max_grade(kind))
        self.grade.blockSignals(False)
        self._roll()

    def _fill_masks(self) -> None:
        picked = self.mask.currentData()
        self.mask.clear()
        for ident in self.rules.masks_for(self.body.currentData() or ""):
            self.mask.addItem(savefighters.mask_label(ident), ident)
        self.mask.setCurrentIndex(max(0, self.mask.findData(picked)))

    def _roll(self) -> None:
        kind, grade = self.kind.currentData(), self.grade.value()
        self.bonus = saveroster.roll_bonus(self.rules, kind, grade)
        self.bonus_label.setText(", ".join(f"{savefighters.STAT_LABELS[k]} +{self.bonus[k]}" for k in STATS))
        gear = saveroster.starting_gear(self.rules, self.catalog, kind, grade)
        self.gear.setText("Comes with: " + (", ".join(self.catalog.part_name(p) for p in gear) or "nothing") + ". "
                          f"Death Bag: {self._room() - self.room_extra} slots"
                          + (f" (+{self.room_extra} while the Express Pass runs)." if self.room_extra else "."))

    def _room(self) -> int:
        return self.rules.capacity(self.kind.currentData(), self.grade.value(), 0, "bag") + self.room_extra

    def _accept(self) -> None:
        try:
            savefighters.check_name(self.name.text())
        except Exception as exc:
            self.name.setFocus()
            self.gear.setText(f"<span style='color:#c0392b'>{exc}</span>")
            return
        self.accept()

    def wanted(self) -> Bought:
        kind, grade = self.kind.currentData(), self.grade.value()
        gear = saveroster.starting_gear(self.rules, self.catalog, kind, grade)
        return Bought(self.name.text().strip(), kind, grade, self.body.currentData(), self.mask.currentData() or "",
                      dict(self.bonus), self._room(), str(uuid.uuid4()), tuple(str(uuid.uuid4()) for _ in gear),
                      int(time.time()))
