"""How far a long job has got, for whoever is showing it.

Saving the mod list can take a while - backing up a 60 MB database, applying
every mod to it, then rebuilding game packages that are tens of megabytes each.
The work reports what it is doing through a ``Progress``; the window turns that
into a moving bar. With nobody listening it does nothing, so the code doing the
work never has to ask whether there is a window.
"""

from __future__ import annotations

from typing import Callable

# (what is happening, percent done 0-100)
Reporter = Callable[[str, int], None]


class Progress:
    """Maps each step of a job onto its own slice of one 0-100 bar."""

    def __init__(self, report: Reporter | None = None) -> None:
        self._report = report
        self._low = 0
        self._high = 100
        self._text = ""

    def stage(self, text: str, low: int, high: int) -> None:
        """Start a step that fills the bar from ``low`` to ``high``."""
        self._low, self._high, self._text = low, high, text
        self._emit(text, low)

    def step(self, done: int, total: int, text: str = "") -> None:
        """``done`` of ``total`` parts of the current step are finished."""
        if total <= 0:
            return
        share = min(max(done / total, 0.0), 1.0)
        self._emit(text or self._text, round(self._low + (self._high - self._low) * share))

    def part(self, start: float, end: float, text: str = "") -> "Progress":
        """A Progress covering ``start``-``end`` (fractions) of the current step.

        For a step made of phases of different speeds - rebuilding a package
        takes seconds, copying it a moment - so each fills its own share.
        """
        span = self._high - self._low
        child = Progress(self._report)
        child.stage(text or self._text, round(self._low + span * start),
                    round(self._low + span * end))
        return child

    def _emit(self, text: str, value: int) -> None:
        if self._report is None:
            return
        try:
            self._report(text, value)
        except Exception:  # a broken window must never break a save
            pass


def ensure(progress: Progress | None) -> Progress:
    """The one given, or one that reports to nobody."""
    return progress if progress is not None else Progress()
