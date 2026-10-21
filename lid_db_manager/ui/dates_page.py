"""The save editor's Dates page (savedates.py does the work).

The changes asked for are kept as steps (fix, set, a new game day, finish timers)
and run in order on the save copy at Save changes, after every other page, so a
fix sees the dates the other pages wrote. What the page shows is those steps run
on a copy of the save.
"""

from __future__ import annotations

import html
import time

from PySide6.QtCore import QDateTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateTimeEdit,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, savedates
from .decal_pages import _fixed, _table

KIND_TEXT = {"future": "in the future", "post2038": "past the 2038 limit", "wrapped": "already wrapped round"}


def _picker(value: int) -> QDateTimeEdit:
    box = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(value)))
    box.setCalendarPopup(True)
    box.setDisplayFormat("yyyy-MM-dd HH:mm")
    return box


class DatesPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog, show_timers: bool, parent=None):
        super().__init__(parent)
        self.document, self.catalog, self.show_timers = document, catalog, show_timers
        self.steps: list[tuple] = []
        self.count = 0

        note = QLabel("Every date in the save. Moving the PC clock forward and back leaves dates in the future, and "
                      "going past 2038 makes them overflow; either can crash the game. Fixing sets the wrong ones "
                      "to the reference time, removes login-bonus days that have not come yet, and changes an "
                      "expiry only if it overflowed.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        self.ref = _picker(time.time())
        self.ref.dateTimeChanged.connect(self._show)
        now = QPushButton("This PC's clock")
        now.clicked.connect(lambda: self.ref.setDateTime(QDateTime.currentDateTime()))
        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("Reference time:"))
        ref_row.addWidget(self.ref)
        ref_row.addWidget(now)
        ref_row.addStretch(1)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.fix_all_button = QPushButton("Fix all problem dates")
        self.fix_all_button.clicked.connect(self.fix_all)
        banner_row = QHBoxLayout()
        banner_row.addWidget(self.banner, 1)
        banner_row.addWidget(self.fix_all_button)

        self.table = _table(("What", "How many", "Earliest", "Latest", "Problems"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selected)
        fix_one = QPushButton("Fix the selected kind")
        fix_one.clicked.connect(self.fix_selected)
        self.set_to = _picker(time.time())
        self.set_button = QPushButton("Set the selected date")
        self.set_button.setToolTip("For a date the save keeps once, such as when the Express Pass ends")
        self.set_button.clicked.connect(self.set_selected)
        row_buttons = QHBoxLayout()
        row_buttons.addWidget(fix_one)
        row_buttons.addStretch(1)
        row_buttons.addWidget(self.set_to)
        row_buttons.addWidget(self.set_button)

        self.new_day = QCheckBox("Make the next start a new game day (free continues and daily things come round "
                                 "again)")
        self.new_day.toggled.connect(self._new_day_toggled)

        self.timer_box = QGroupBox("Timers (the Timers mod)")
        self.timer_table = _table(("Timer", "Ends"))
        self.timer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timer_table.setMaximumHeight(160)
        finish = QPushButton("Finish the selected now")
        finish.clicked.connect(self.finish_selected)
        finish_all = QPushButton("Finish all now")
        finish_all.clicked.connect(self.finish_all)
        timer_buttons = QHBoxLayout()
        timer_buttons.addStretch(1)
        timer_buttons.addWidget(finish)
        timer_buttons.addWidget(finish_all)
        timer_layout = QVBoxLayout(self.timer_box)
        timer_layout.addWidget(self.timer_table)
        timer_layout.addLayout(timer_buttons)
        self.timer_box.setVisible(show_timers)

        self.summary = QLabel()
        self.summary.setObjectName("dim")
        undo = QPushButton("Undo all")
        undo.clicked.connect(self.undo_all)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(undo)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(ref_row)
        layout.addLayout(banner_row)
        layout.addWidget(self.table, 1)
        layout.addLayout(row_buttons)
        layout.addWidget(self.new_day)
        layout.addWidget(self.timer_box)
        layout.addLayout(bottom)
        self.groups: list = []
        self._show()

    # -- the steps --

    def _ref(self) -> int:
        return int(self.ref.dateTime().toSecsSinceEpoch())

    def _run(self, save: "saveedit.SaveDocument") -> int:
        count = 0
        for step in self.steps:
            if step[0] == "fix":
                count += savedates.fix(save, step[2], step[1])
            elif step[0] == "set":
                count += savedates.set_one(save, step[1], step[2])
            elif step[0] == "new day":
                count += savedates.new_game_day(save, step[1])
            elif step[0] == "finish":
                count += savedates.finish_timers(save, step[1], step[2], self.catalog)
        return count

    def _add(self, step: tuple) -> None:
        self.steps.append(step)
        self._show()
        self.changed.emit()

    def _preview(self) -> "saveedit.SaveDocument":
        if not self.steps:
            self.count = 0
            return self.document
        copy = saveedit.SaveDocument(self.document.raw, self.document.source)
        self.count = self._run(copy)
        return copy

    # -- showing --

    def _show(self) -> None:
        save = self._preview()
        ref = self._ref()
        self.groups = savedates.scan(save.doc)
        counts = savedates.problems(self.groups, ref)

        def bad(g):
            return [e for e in g.entries if savedates.problem(g, e.value, ref)]

        self.groups.sort(key=lambda g: (not bad(g), -max(abs(e.value) for e in g.entries)))
        total = sum(counts.values())
        if total:
            parts = ", ".join(f"{n:,} {KIND_TEXT[k]}" for k, n in counts.items() if n)
            self.banner.setText(f"<span style='color:#c0392b'><b>{total:,} problem date(s)</b>: "
                                f"{html.escape(parts)}.</span>")
        else:
            self.banner.setText(f"No problem dates against {savedates.when(ref)}.")
        self.fix_all_button.setEnabled(bool(total))
        self.table.setRowCount(len(self.groups))
        for row, g in enumerate(self.groups):
            values = [e.value for e in g.entries]
            wrong = bad(g)
            what = _fixed(g.label + (" (an expiry)" if g.deadline else ""))
            what.setToolTip(g.pattern)
            self.table.setItem(row, 0, what)
            self.table.setItem(row, 1, _fixed(f"{len(values):,}"))
            self.table.setItem(row, 2, _fixed("" if len(values) == 1 else savedates.when(min(values))))
            self.table.setItem(row, 3, _fixed(savedates.when(max(values))))
            self.table.setItem(row, 4, _fixed(f"{len(wrong):,} wrong" if wrong else "ok"))
        self._show_timers(save)
        self._selected()
        steps = len(self.steps)
        self.summary.setText(f"{sum(len(g.entries) for g in self.groups):,} dates of {len(self.groups)} kinds"
                             + (f" - {self.count:,} change(s) from {steps} step(s)" if steps else ""))

    def _show_timers(self, save) -> None:
        if not self.show_timers:
            return
        rows = savedates.timers(save)
        now = time.time()
        self.timer_rows = rows
        self.timer_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.timer_table.setItem(i, 0, _fixed(savedates.timer_label(r, self.catalog)))
            period = r.get("period")
            ends = savedates.when(period) if isinstance(period, int) else "?"
            if isinstance(period, int) and period <= now:
                ends += " (ready to collect)"
            self.timer_table.setItem(i, 1, _fixed(ends))

    def _selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        single = len(rows) == 1 and len(self.groups[next(iter(rows))].entries) == 1
        self.set_button.setEnabled(single)
        if single:
            value = self.groups[next(iter(rows))].entries[0].value
            if value > 0:
                self.set_to.setDateTime(QDateTime.fromSecsSinceEpoch(min(value, savedates.INT32_MAX)))

    # -- changing --

    def fix_all(self) -> None:
        self._add(("fix", "*", self._ref()))

    def fix_selected(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}):
            self.steps.append(("fix", self.groups[row].pattern, self._ref()))
        self._show()
        self.changed.emit()

    def set_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) == 1:
            self._add(("set", self.groups[next(iter(rows))].pattern, int(self.set_to.dateTime().toSecsSinceEpoch())))

    def _new_day_toggled(self, on: bool) -> None:
        self.steps = [s for s in self.steps if s[0] != "new day"]
        if on:
            self.steps.append(("new day", time.time()))
        self._show()
        self.changed.emit()

    def finish_selected(self) -> None:
        rows = sorted({i.row() for i in self.timer_table.selectedIndexes()})
        keys = [savedates.timer_key(self.timer_rows[r]) for r in rows if r < len(self.timer_rows)]
        if keys:
            self._add(("finish", keys, time.time()))

    def finish_all(self) -> None:
        keys = [savedates.timer_key(r) for r in getattr(self, "timer_rows", [])]
        if keys:
            self._add(("finish", keys, time.time()))

    def undo_all(self) -> None:
        self.steps.clear()
        self.new_day.blockSignals(True)
        self.new_day.setChecked(False)
        self.new_day.blockSignals(False)
        self._show()
        self.changed.emit()

    @property
    def change_count(self) -> int:
        return self.count

    def apply(self, save: "saveedit.SaveDocument") -> None:
        self._run(save)
