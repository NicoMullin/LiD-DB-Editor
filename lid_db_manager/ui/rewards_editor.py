"""A list of rewards to draw from, edited in a window: which rewards, and how
often each one comes up.

The list is a "rewards" setting's value (see settings.py). Add rewards picks
more off everything in the player's masters.db (master_reward). Each row's
weight sets its chance: weight 3 comes up three times as often as weight 1,
and the Chance column shows what that works out to. A reward that makes more
than one present in the Rewards Box - "4 rare metals" is 4 presents - says so
in the Presents column. The rows are kept in the order "Add rewards" lists
them - by category, then name - since the order changes nothing about the
draw. Until the player changes anything the list is the mod's own, and leaving
it at that keeps the setting on the default (value "").
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..rewards import Reward, read_rewards
from ..settings import MOST_REWARDS, MOST_WEIGHT, ModSetting, RewardEntry
from .list_picker import ListPicker, PickRow

COLUMNS = ("Reward", "Category", "Presents", "Weight", "Chance")
REWARD, CATEGORY, PRESENTS, WEIGHT, CHANCE = range(len(COLUMNS))


def _presents(count: int) -> str:
    return f"{count:,} presents" if count != 1 else "1 present"


class RewardsChooser(ListPicker):
    """Tick rewards to add, off everything in the database."""

    def __init__(self, setting: ModSetting, rewards: list[Reward], parent=None):
        rows = [
            PickRow(
                reward.id,
                ((reward.name, reward.name.casefold()), (reward.category, reward.category.casefold()),
                 (str(reward.presents), reward.presents)),
                "<br>".join((
                    f"<b>{html.escape(reward.name)}</b> &nbsp; {html.escape(reward.category)}",
                    html.escape(reward.id),
                    f"Arrives as {_presents(reward.presents)} in the Rewards Box.",
                )),
            )
            for reward in rewards
        ]
        super().__init__(
            setting, "", rows, [],
            columns=("Reward", "Category", "Presents"), noun="reward",
            intro=("Every reward in your database: Kill Coins, SPLithium, Express Passes, items, "
                   "mushrooms, decals, Lost Bags and more. Tick the ones to add, then set how often "
                   "each comes up in the list. Presents is how many it puts in the Rewards Box: "
                   "the game makes one per item, so a reward of several items makes several."),
            placeholder="Search by name, category or id...",
            search_columns=(0, 1),
            parent=parent,
        )
        self.setWindowTitle(f"Add rewards - {setting.label}")
        self.ok_button.setText("Add these")

    def _count(self) -> None:
        if hasattr(self, "toggle_button"):
            self._label_toggle()
        picked = self.ticked()
        self.count.setText(f"{len(picked):,} ticked" if picked else "Tick the rewards to add.")
        self.ok_button.setEnabled(bool(picked))


class RewardsEditor(QDialog):
    """The whole list: each reward and its weight."""

    def __init__(self, setting: ModSetting, value: str, db_path: Path, parent=None):
        super().__init__(parent)
        self.setting = setting
        self.result_value = value
        self.catalogue = {reward.id: reward for reward in read_rewards(db_path)}
        self.order = {reward_id: n for n, reward_id in enumerate(self.catalogue)}
        self.default = self._sorted(setting.rewards(""))
        self.setWindowTitle(f"{setting.label} - {setting.noun or 'reward'}s")
        self.resize(820, 640)

        intro = QLabel(
            "The rewards to draw from. Weight is how often one comes up compared with the "
            "others: a reward with weight 3 comes up three times as often as one with weight 1. "
            "Chance is what that works out to for each draw."
        )
        intro.setWordWrap(True)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(REWARD, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        add = QPushButton("Add rewards...")
        add.clicked.connect(self.add_rewards)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_highlighted)
        clear = QPushButton("Remove all")
        clear.clicked.connect(lambda: self.fill([]))
        back = QPushButton("Back to the default")
        back.setToolTip(setting.default_label or "The mod's own list")
        back.clicked.connect(lambda: self.fill(self.default))

        self.count = QLabel()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Use this list")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        for button in (add, self.remove_button, clear, back):
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        footer = QHBoxLayout()
        footer.addWidget(self.count, 1)
        footer.addWidget(buttons)
        layout.addLayout(footer)

        self.fill(setting.rewards(value))

    # -- the rows --------------------------------------------------------------

    def _sorted(self, entries: list[RewardEntry]) -> list[RewardEntry]:
        """In the catalogue's order; rewards it does not have last."""
        return sorted(entries, key=lambda e: (self.order.get(e.id, len(self.order)), e.id))

    def fill(self, entries: list[RewardEntry]) -> None:
        self.table.setRowCount(0)
        for entry in self._sorted(entries):
            self._append(entry)
        self._count()

    def _append(self, entry: RewardEntry) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        reward = self.catalogue.get(entry.id)
        name = QTableWidgetItem(reward.name if reward else f"{entry.id} (not in your database)")
        name.setData(Qt.ItemDataRole.UserRole, entry.id)
        name.setToolTip(entry.id)
        self.table.setItem(row, REWARD, name)
        self.table.setItem(row, CATEGORY, QTableWidgetItem(reward.category if reward else "?"))
        presents = QTableWidgetItem(str(reward.presents) if reward else "?")
        presents.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if reward and reward.presents > 1:
            presents.setToolTip(f"Arrives as {_presents(reward.presents)} in the Rewards Box.")
        self.table.setItem(row, PRESENTS, presents)
        weight = QSpinBox()
        weight.setRange(1, MOST_WEIGHT)
        weight.setValue(entry.weight)
        weight.valueChanged.connect(lambda _v: self._count())
        self.table.setCellWidget(row, WEIGHT, weight)
        chance = QTableWidgetItem()
        chance.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, CHANCE, chance)

    def entry(self, row: int) -> RewardEntry:
        return RewardEntry(self.table.item(row, REWARD).data(Qt.ItemDataRole.UserRole),
                           self.table.cellWidget(row, WEIGHT).value())

    def entries(self) -> list[RewardEntry]:
        found: list[RewardEntry] = []
        seen: set[str] = set()
        for row in range(self.table.rowCount()):
            entry = self.entry(row)
            if entry.id not in seen:
                seen.add(entry.id)
                found.append(entry)
        return found

    def _highlighted_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    # -- the buttons -------------------------------------------------------------

    def add_rewards(self) -> None:
        chooser = RewardsChooser(self.setting, list(self.catalogue.values()), self)
        if not chooser.exec():
            return
        self.add(chooser.ticked())

    def add(self, reward_ids: list[str]) -> None:
        entries = self.entries()
        listed = {entry.id for entry in entries}
        for reward_id in reward_ids:
            if len(entries) >= MOST_REWARDS:
                break
            if reward_id not in listed:     # already on the list: its weight stays
                listed.add(reward_id)
                entries.append(RewardEntry(reward_id))
        self.fill(entries)

    def remove_highlighted(self) -> None:
        for row in reversed(self._highlighted_rows()):
            self.table.removeRow(row)
        self._count()

    def _count(self) -> None:
        count = self.table.rowCount()
        total = sum(self.table.cellWidget(row, WEIGHT).value() for row in range(count))
        for row in range(count):
            weight = self.table.cellWidget(row, WEIGHT).value()
            self.table.item(row, CHANCE).setText(f"{100 * weight / total:.1f}%" if total else "")
        if not count:
            self.count.setText("Nothing on the list - add at least one reward.")
            self.ok_button.setEnabled(False)
            return
        text = f"{count:,} reward{'s' if count != 1 else ''}"
        several = sum(1 for e in self.entries()
                      if (r := self.catalogue.get(e.id)) is not None and r.presents > 1)
        if several:
            text += f", {several:,} of them more than one present"
        if self.entries() == self.default:
            what = self.setting.default_label or "the mod's own list"
            text += f" - the default ({what})"
        elif count >= MOST_REWARDS:
            text += " - the most a list can hold"
        self.count.setText(text)
        self.ok_button.setEnabled(True)

    def value(self) -> str:
        """What the setting will hold: "" while the list is the default's."""
        entries = self.entries()
        if entries == self.default:
            return ""
        return ",".join(e.text() for e in entries)

    def _accept(self) -> None:
        if not self.table.rowCount():
            return
        self.result_value = self.value()
        self.accept()
