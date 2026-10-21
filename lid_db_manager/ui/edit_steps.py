"""The save editor's changes to things kept in records: the freezer's (fighters bought,
deleted, brought back, the freezer's size) and the Storage Box's and Death Bags' (things put
in, taken out, worn, moved from one to the other).

Each is a step - a callable that makes the change on a save - kept in the order it was made
and played onto a fresh copy of the save: to show the box or a bag as it will be, and once
more onto the copy the editor writes. The freezer's steps come first, so a fighter bought
here has a Death Bag for the steps after; the box's and bags' share one list, so moving a
sword from a bag into the box and then out again plays back in that order.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import saveedit


@dataclass
class Step:
    key: object                     # "box", ("bag", cid) or ("freezer", cid)
    description: str
    run: object                     # callable(save)


class Steps:
    def __init__(self, document: "saveedit.SaveDocument") -> None:
        self.document = document
        self.freezer: list[Step] = []
        self.things: list[Step] = []
        self._kept: tuple | None = None         # (the freezer's steps, the save after them as bytes)

    # -- adding and taking back --

    def add(self, key, description: str, run) -> None:
        (self.freezer if self._freezer(key) else self.things).append(Step(key, description, run))

    def undo(self, key) -> Step | None:
        """Take back the last step made with ``key``."""
        steps = self.freezer if self._freezer(key) else self.things
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].key == key:
                return steps.pop(i)
        return None

    def drop(self, test) -> None:
        """Forget the box and bag steps whose key passes ``test`` (a deleted fighter's)."""
        self.things = [s for s in self.things if not test(s.key)]

    def of(self, key) -> list[str]:
        return [s.description for s in self.freezer + self.things if s.key == key]

    @staticmethod
    def _freezer(key) -> bool:
        return isinstance(key, tuple) and key[0] == "freezer"

    # -- playing them --

    def play_freezer(self, save: "saveedit.SaveDocument") -> None:
        for step in self.freezer:
            step.run(save)

    def play_things(self, save: "saveedit.SaveDocument") -> None:
        for step in self.things:
            step.run(save)

    def base(self) -> "saveedit.SaveDocument":
        """A fresh copy of the save with the freezer's steps on it (to look at: the freezer's
        result is kept as bytes, so its changes are not listed on this copy)."""
        if not self.freezer:
            return saveedit.SaveDocument(self.document.raw, self.document.source)
        if self._kept is None or self._kept[0] != self.freezer:
            copy = saveedit.SaveDocument(self.document.raw, self.document.source)
            self.play_freezer(copy)
            self._kept = (list(self.freezer), copy.to_bytes())
        return saveedit.SaveDocument(self._kept[1], self.document.source)

    def preview(self) -> "saveedit.SaveDocument":
        """A fresh copy with every step on it."""
        copy = self.base()
        self.play_things(copy)
        return copy
