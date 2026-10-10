"""Picking decals for a decal setting, off the list of every decal there is.

Every decal in the masters.db the manager is pointed at is listed - the game's
own and any a mod added - with its English name, its stars and its category.
The window itself (search, sorting, ticks, highlighting, the panel) is the
shared ListPicker; this fills it with decals, and the panel says what the
highlighted decal does, so nobody needs the game or a wiki open. Until the
player changes anything it shows the mod's own default ticked, and leaving it
at that keeps the setting on the default (the mod's query, run afresh each time
it applies) rather than freezing today's answer into a list.
"""

from __future__ import annotations

import html
from pathlib import Path

from ..decals import Decal, default_ids, read_decals
from ..settings import ModSetting
from .list_picker import ListPicker, PickRow

COLUMNS = ("Decal", "Stars", "Category", "ID")
MISSING = "(not in this database)"


def stars_text(stars: int) -> str:
    return "★" * stars if stars > 0 else "-"


def describe(decal: Decal) -> str:
    """The panel's text for one decal, every part of it escaped."""
    parts = [f"<b>{html.escape(decal.name)}</b> &nbsp; {stars_text(decal.stars)}"
             + (f" &nbsp; {html.escape(decal.category)}" if decal.category else "")]
    if decal.description:
        parts.append(html.escape(decal.description))
    if decal.limit:
        parts.append(f"<i>{html.escape(decal.limit)}</i>")
    if decal.how_to_get:
        parts.append(f"How to get it: {html.escape(decal.how_to_get)}")
    if decal.name == MISSING:
        parts.append(f"{html.escape(decal.id)} is not in this masters.db - the mod that added it may be off.")
    return "<br>".join(parts)


def _row(decal: Decal) -> PickRow:
    return PickRow(
        decal.id,
        (
            (decal.name, decal.name.casefold()),
            (stars_text(decal.stars), decal.stars),
            (decal.category, decal.category.casefold()),
            (decal.id, decal.id.casefold()),
        ),
        describe(decal),
    )


class DecalPicker(ListPicker):
    """Tick the decals a decal setting holds."""

    def __init__(self, setting: ModSetting, value: str, db_path: Path, parent=None,
                 decals: list[Decal] | None = None):
        self.decals = decals if decals is not None else read_decals(db_path)
        known = {decal.id for decal in self.decals}
        # Picked once, since gone from the database (a mod that added it is off):
        # listed, so it can be seen and unticked, rather than kept unseen.
        missing = [Decal(i, MISSING, 0, "") for i in setting.decal_ids(value) if i not in known]
        super().__init__(
            setting, value, [_row(decal) for decal in self.decals],
            default_ids(db_path, setting.default_sql),
            columns=COLUMNS, noun="decal",
            intro=("Every decal in your masters.db, including ones a mod added. Click a "
                   "column's heading to sort by it. Drag, or hold Ctrl or Shift, to highlight "
                   "several, then tick them all at once."),
            placeholder="Search by name, category or id...",
            missing=[_row(decal) for decal in missing],
            search_columns=(0, 2, 3),
            parent=parent,
        )
