"""The save editor's Mushroom Stew and Quests pages (savestew.py and savequests.py do the work).

Each keeps only what was changed, and writes it onto the save copy at Save changes.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import saveedit, savedecals, savequests, savestew
from .decal_pages import _SpinDelegate, _fixed, _number, _table

COMING = 20


# -- the stew ------------------------------------------------------------------------------

class StewPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", odds: "savestew.StewOdds",
                 decals: "savedecals.DecalCatalog", parent=None):
        super().__init__(parent)
        self.document, self.odds, self.decals = document, odds, decals
        self.cleared = False

        note = QLabel("A normal Mushroom Stew does not roll its decal when you buy it: the game rolls a long list "
                      "ahead and keeps it in the save, so a decal a mod adds to the stew does not come until that "
                      "list runs out. Clearing the list puts the save back to how a new game has it, and the next "
                      "stew rolls a new list from the stew's odds as they are then.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.coming = QListWidget()
        clear = QPushButton("Clear the list")
        clear.setToolTip("The next stew rolls a new list, with every decal the stew gives now")
        clear.clicked.connect(self.clear)
        undo = QPushButton("Undo")
        undo.clicked.connect(self.undo)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(clear)
        buttons.addWidget(undo)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.state)
        layout.addWidget(QLabel(f"The next {COMING} pulls:"))
        layout.addWidget(self.coming, 1)
        layout.addLayout(buttons)
        self._show()

    def _show(self) -> None:
        rolled = savestew.queue(self.document)
        missing = savestew.missing(self.document, self.odds)
        if self.cleared:
            text = (f"<b>Cleared</b> ({len(rolled):,} decals were rolled ahead). The next stew rolls a new list "
                    f"from the {len(self.odds.odds):,} decals the stew gives now.")
        elif not rolled:
            text = "No list is rolled yet: the next stew rolls one from the stew's odds as they are then."
        else:
            text = f"{len(rolled):,} decals are rolled ahead."
            if missing:
                names = ", ".join(self.decals.label(s) for s in missing[:8]) + (", ..." if len(missing) > 8 else "")
                text += (f" <b>{len(missing):,} decal(s) the stew gives now are not in it</b> and will not come "
                         f"until it runs out: {html.escape(names)}.")
        self.state.setText(text)
        self.coming.clear()
        if not self.cleared:
            for sklid in savestew.coming(self.document, COMING):
                self.coming.addItem(self.decals.label(sklid))

    def clear(self) -> None:
        if savestew.queue(self.document) and not self.cleared:
            self.cleared = True
            self._show()
            self.changed.emit()

    def undo(self) -> None:
        if self.cleared:
            self.cleared = False
            self._show()
            self.changed.emit()

    @property
    def change_count(self) -> int:
        return 1 if self.cleared else 0

    def apply(self, save: "saveedit.SaveDocument") -> None:
        if self.cleared:
            savestew.clear(save)


# -- quests -----------------------------------------------------------------------------------

FILTERS = ("All", "Cleared", "Never cleared", "Taken now")


class QuestsPage(QWidget):
    changed = Signal()

    def __init__(self, document: "saveedit.SaveDocument", catalog: "savequests.QuestCatalog", parent=None):
        super().__init__(parent)
        self.document, self.catalog = document, catalog
        self.before = savequests.counts(document)
        self.held = savequests.taken(document)
        self.wanted: dict[str, tuple[int, int]] = {}    # qid -> (taken, cleared), where changed
        self.reset_done = False
        self.progress: dict[str, int] = {}
        self.dropped: set[str] = set()
        self._loading = False

        note = QLabel("Every quest's history: how many times it was taken and cleared. A quest never cleared pays "
                      "its first-clear reward, and opens the quests that need it once cleared. The four one-time "
                      "quests that unlock the Grade 6 fighters are marked; resetting them is safe (the game never "
                      "unlocks a fighter twice, and keeps unlocked fighters apart from quests) but gives nothing.")
        note.setObjectName("dim")
        note.setWordWrap(True)

        self.taken_table = _table(("Taken now", "Progress", "Goal"))
        self.taken_table.setItemDelegateForColumn(1, _SpinDelegate(0, lambda row: max(1, self._goal(row)),
                                                                   self.taken_table))
        self._edit_triggers(self.taken_table)
        self.taken_table.itemChanged.connect(self._progress_edited)
        self.taken_table.setMaximumHeight(140)
        complete = QPushButton("Complete the selected")
        complete.setToolTip("Progress to the goal; collect the reward in the game")
        complete.clicked.connect(self.complete_selected)
        drop = QPushButton("Drop the selected")
        drop.clicked.connect(self.drop_selected)
        taken_buttons = QHBoxLayout()
        taken_buttons.addStretch(1)
        taken_buttons.addWidget(complete)
        taken_buttons.addWidget(drop)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search quests")
        self.search.textChanged.connect(self._filter)
        self.show_only = QComboBox()
        self.show_only.addItems(FILTERS)
        self.show_only.currentIndexChanged.connect(self._filter)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.show_only)
        self.table = _table(("Quest", "Kind", "Taken", "Cleared"))
        for column in (2, 3):
            self.table.setItemDelegateForColumn(column, _SpinDelegate(0, savequests.COUNT_MAX, self.table))
        self._edit_triggers(self.table)
        self.table.itemChanged.connect(self._count_edited)

        self.one_time = QCheckBox("Reset the one-time fighter quests too")
        reset = QPushButton("Reset all")
        reset.setToolTip("Every quest back to never taken and never cleared (a quest taken now stays taken), so "
                         "first-clear rewards come again")
        reset.clicked.connect(self.reset_all)
        undo = QPushButton("Undo all")
        undo.clicked.connect(self.undo_all)
        self.summary = QLabel()
        self.summary.setObjectName("dim")
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.one_time)
        bottom.addWidget(reset)
        bottom.addWidget(undo)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.taken_table)
        layout.addLayout(taken_buttons)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)
        self.rows = sorted(self.before, key=lambda q: (_int((catalog.quests.get(q) or {}).get("no"), 10 ** 9), q))
        self._fill()

    @staticmethod
    def _edit_triggers(table) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                              | QAbstractItemView.EditTrigger.EditKeyPressed
                              | QAbstractItemView.EditTrigger.AnyKeyPressed)

    # -- what each quest is at --

    def counts_now(self, qid: str) -> tuple[int, int]:
        return self.wanted.get(qid, self.before.get(qid, (0, 0)))

    def _held_now(self) -> list[dict]:
        return [o for o in self.held if str(o.get("qid")) not in self.dropped]

    def _goal(self, row: int) -> int:
        held = self._held_now()
        return _int(held[row].get("prgmax")) if row < len(held) else 0

    def _fill(self) -> None:
        self._loading = True
        try:
            held = self._held_now()
            self.taken_table.setRowCount(len(held))
            for i, o in enumerate(held):
                qid = str(o.get("qid"))
                self.taken_table.setItem(i, 0, _fixed(self.catalog.name(qid)))
                self.taken_table.setItem(i, 1, _number(self.progress.get(qid, _int(o.get("prgnow")))))
                self.taken_table.setItem(i, 2, _fixed(str(_int(o.get("prgmax")))))
            self.table.setRowCount(len(self.rows))
            for i, qid in enumerate(self.rows):
                name = self.catalog.name(qid) + (" (one-time)" if self.catalog.one_time(qid) else "")
                item = _fixed(name)
                item.setToolTip(qid)
                self.table.setItem(i, 0, item)
                self.table.setItem(i, 1, _fixed(self.catalog.category(qid)))
                taken, cleared = self.counts_now(qid)
                self.table.setItem(i, 2, _number(taken))
                self.table.setItem(i, 3, _number(cleared))
        finally:
            self._loading = False
        self._filter()
        self._show_summary()

    def _filter(self) -> None:
        words = self.search.text().lower().split()
        which = FILTERS[self.show_only.currentIndex()]
        held = {str(o.get("qid")) for o in self._held_now()}
        for i, qid in enumerate(self.rows):
            taken, cleared = self.counts_now(qid)
            hide = not all(w in f"{self.catalog.name(qid)} {qid}".lower() for w in words)
            hide = hide or (which == "Cleared" and not cleared) or (which == "Never cleared" and cleared)
            hide = hide or (which == "Taken now" and qid not in held)
            self.table.setRowHidden(i, hide)

    def _show_summary(self) -> None:
        cleared = sum(1 for q in self.rows if self.counts_now(q)[1] > 0)
        text = f"{len(self.rows):,} quests, {cleared:,} cleared"
        if self.change_count:
            text += f" - {self.change_count:,} change(s)"
        self.summary.setText(text)

    # -- changing --

    def _set(self, qid: str, taken: int, cleared: int) -> None:
        cleared = max(0, min(savequests.COUNT_MAX, cleared))
        taken = max(cleared, min(savequests.COUNT_MAX, taken))
        if (taken, cleared) == self.before.get(qid):
            self.wanted.pop(qid, None)
        else:
            self.wanted[qid] = (taken, cleared)

    def _count_edited(self, item) -> None:
        if self._loading or item.column() not in (2, 3):
            return
        qid = self.rows[item.row()]
        taken, cleared = self.counts_now(qid)
        value = int(item.data(Qt.ItemDataRole.EditRole) or 0)
        if item.column() == 2:
            self._set(qid, value, min(cleared, value))
        else:
            self._set(qid, max(taken, value), value)
        self._fill()
        self.changed.emit()

    def _progress_edited(self, item) -> None:
        if self._loading or item.column() != 1:
            return
        o = self._held_now()[item.row()]
        qid = str(o.get("qid"))
        value = int(item.data(Qt.ItemDataRole.EditRole) or 0)
        if value == _int(o.get("prgnow")):
            self.progress.pop(qid, None)
        else:
            self.progress[qid] = value
        self._show_summary()
        self.changed.emit()

    def _selected_taken(self) -> list[str]:
        held = self._held_now()
        return [str(held[i.row()].get("qid")) for i in self.taken_table.selectionModel().selectedRows()
                if i.row() < len(held)]

    def complete_selected(self) -> None:
        for o in self._held_now():
            if str(o.get("qid")) in self._selected_taken():
                goal = _int(o.get("prgmax"))
                self.progress[str(o.get("qid"))] = goal if goal > 0 else 1
        self._fill()
        self.changed.emit()

    def drop_selected(self) -> None:
        for qid in self._selected_taken():
            self.dropped.add(qid)
            self.progress.pop(qid, None)
        self._fill()
        self.changed.emit()

    def reset_all(self) -> None:
        for qid, (taken, cleared) in savequests.reset_counts(self.document, self.catalog,
                                                             self.one_time.isChecked()).items():
            self._set(qid, taken, cleared)
        self.reset_done = bool(self.wanted)
        self._fill()
        self.changed.emit()

    def undo_all(self) -> None:
        self.wanted.clear()
        self.progress.clear()
        self.dropped.clear()
        self.reset_done = False
        self._fill()
        self.changed.emit()

    @property
    def change_count(self) -> int:
        return len(self.wanted) + len(self.progress) + len(self.dropped)

    def apply(self, save: "saveedit.SaveDocument") -> None:
        if self.wanted:
            savequests.set_counts(save, self.catalog, self.wanted,
                                  summary="Reset all" if self.reset_done else "")
        if self.progress:
            savequests.set_progress(save, self.catalog, self.progress)
        if self.dropped:
            savequests.drop(save, self.catalog, self.dropped)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
