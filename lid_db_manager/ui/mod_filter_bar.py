"""The row above the mod list: search, category, tags and sort.

One line of controls rather than a sidebar, because the window is already three
panels wide and the list is the part worth the room.

Nothing here knows what a mod is. It reports a ``browse.Filter`` and a sort name
and leaves the deciding to browse.py, so what is on screen can be tested without
a window - see tests/test_browse.py.

Two deliberate choices about state:

* The sort is remembered between sessions; the search and the filters are not.
  A filter left on from last time hides mods, and somebody who has forgotten it
  is on has no reason to suspect the list rather than the mod.
* Typing is not applied on every keystroke. Each change rebuilds the whole tree,
  which is slow enough to feel at twenty mods, so the search waits until typing
  stops.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from ..browse import SORT_LABELS, SORT_ORDER, SORTS, Filter

# How long after the last keystroke the list is rebuilt.
TYPING_PAUSE_MS = 220

ALL_CATEGORIES = "All categories"


class ModFilterBar(QWidget):
    """Search, category, tags and sort, as one row of controls."""

    changed = Signal()  # the filter or the sort moved; rebuild the list

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tags: dict[str, int] = {}
        self._ticked: set[str] = set()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search mods - name, description, author, tag...")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Every word has to appear somewhere in the mod, in any order.\n"
            'So "coin limit" finds the Coin Locker mod without the exact phrase.'
        )
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(TYPING_PAUSE_MS)
        self._typing.timeout.connect(self.changed)
        self.search.textChanged.connect(lambda _text: self._typing.start())
        # Enter applies it now rather than waiting out the pause.
        self.search.returnPressed.connect(self._apply_now)

        self.category_box = QComboBox()
        self.category_box.setToolTip("Show only mods filed under one category")
        self.category_box.currentIndexChanged.connect(self._on_category_changed)

        self.tags_button = QToolButton()
        self.tags_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tags_button.setToolTip(
            "Tick tags to show the mods that carry any of them.\n"
            "Ticking a second tag shows more mods, not fewer."
        )
        self.tags_menu = QMenu(self)
        # Rebuilt as it opens rather than as a tag is ticked: ticking one while
        # the menu is up would otherwise delete the action being clicked.
        self.tags_menu.aboutToShow.connect(self._rebuild_tags_menu)
        self.tags_button.setMenu(self.tags_menu)

        self.sort_box = QComboBox()
        for name in SORTS:
            self.sort_box.addItem(SORT_LABELS[name], name)
        self.sort_box.setToolTip(
            "How the list is ordered on screen.\n\n"
            "Sorting never changes the load order - the number in the first "
            "column still says where a mod really applies. While you are sorted "
            "by anything else, that number cannot be edited."
        )
        self.sort_box.currentIndexChanged.connect(lambda _index: self.changed.emit())

        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Show every mod again")
        self.clear_button.clicked.connect(self.clear)

        self.count_label = QLabel()
        self.count_label.setObjectName("dim")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.search, 1)
        row.addWidget(self.category_box)
        row.addWidget(self.tags_button)
        row.addWidget(QLabel("Sort:"))
        row.addWidget(self.sort_box)
        row.addWidget(self.clear_button)
        row.addWidget(self.count_label)

        self.set_choices({}, {})
        self._refresh_tags_button()

    # -- what there is to choose from ---------------------------------------

    def set_choices(self, categories: dict[str, int], tags: dict[str, int]) -> None:
        """Rebuild the category and tag lists, keeping what was chosen.

        Called after every rescan, because a mod arriving can bring a category
        nobody had before. A chosen category that has just gone away falls back
        to "All categories" rather than showing an empty list.
        """
        chosen = self.current_category()
        self.category_box.blockSignals(True)
        self.category_box.clear()
        total = sum(categories.values())
        self.category_box.addItem(f"{ALL_CATEGORIES} ({total})", "")
        for name, count in categories.items():
            self.category_box.addItem(f"{name} ({count})", name)
        index = self.category_box.findData(chosen) if chosen else 0
        self.category_box.setCurrentIndex(max(index, 0))
        self.category_box.blockSignals(False)

        self._tags = dict(tags)
        self._ticked &= set(self._tags)
        self._rebuild_tags_menu()
        self._refresh_tags_button()

    def _rebuild_tags_menu(self) -> None:
        self.tags_menu.clear()
        if not self._tags:
            action = self.tags_menu.addAction("No tags yet")
            action.setEnabled(False)
            return
        for tag, count in self._tags.items():
            action = self.tags_menu.addAction(f"{tag} ({count})")
            action.setCheckable(True)
            action.setChecked(tag in self._ticked)
            action.toggled.connect(lambda on, tag=tag: self._on_tag_toggled(tag, on))
        self.tags_menu.addSeparator()
        clear = self.tags_menu.addAction("Clear tags")
        clear.setEnabled(bool(self._ticked))
        clear.triggered.connect(lambda: self.set_tags(()))

    def _refresh_tags_button(self) -> None:
        count = len(self._ticked)
        self.tags_button.setText(f"Tags ({count})" if count else "Tags")
        self.clear_button.setEnabled(self.current_filter().on)

    # -- reading it ---------------------------------------------------------

    def current_category(self) -> str:
        data = self.category_box.currentData()
        return str(data or "")

    def current_filter(self) -> Filter:
        return Filter(
            text=self.search.text(),
            category=self.current_category(),
            tags=frozenset(self._ticked),
        )

    def current_sort(self) -> str:
        return str(self.sort_box.currentData() or SORT_ORDER)

    # -- changing it --------------------------------------------------------

    def set_sort(self, sort: str) -> None:
        index = self.sort_box.findData(sort)
        if index >= 0 and index != self.sort_box.currentIndex():
            self.sort_box.setCurrentIndex(index)

    def set_category(self, category: str) -> None:
        index = self.category_box.findData(category)
        if index < 0:  # a category with no mods in it yet
            return
        if index != self.category_box.currentIndex():
            self.category_box.setCurrentIndex(index)

    def set_tags(self, tags) -> None:
        wanted = {tag for tag in tags if tag in self._tags}
        if wanted == self._ticked:
            return
        self._ticked = wanted
        self._rebuild_tags_menu()
        self._refresh_tags_button()
        self.changed.emit()

    def set_text(self, text: str) -> None:
        if text != self.search.text():
            self.search.setText(text)
            self._apply_now()

    def clear(self) -> None:
        """Back to showing everything. Leaves the sort alone - it is not a filter."""
        if not self.current_filter().on:
            return
        self._typing.stop()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.category_box.blockSignals(True)
        self.category_box.setCurrentIndex(0)
        self.category_box.blockSignals(False)
        self._ticked.clear()
        self._rebuild_tags_menu()
        self._refresh_tags_button()
        self.changed.emit()

    def set_counts(self, shown: int, total: int) -> None:
        """The "Showing 4 of 22" line. Says nothing when nothing is hidden."""
        self.count_label.setText(f"Showing {shown} of {total}" if shown != total else "")
        self._refresh_tags_button()

    # -- plumbing -----------------------------------------------------------

    def _apply_now(self) -> None:
        self._typing.stop()
        self.changed.emit()

    def _on_category_changed(self, _index: int) -> None:
        self._refresh_tags_button()
        self.changed.emit()

    def _on_tag_toggled(self, tag: str, on: bool) -> None:
        if on:
            self._ticked.add(tag)
        else:
            self._ticked.discard(tag)
        self._refresh_tags_button()
        self.changed.emit()
