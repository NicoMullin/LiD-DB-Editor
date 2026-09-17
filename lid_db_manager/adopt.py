"""Reading someone's database and saying which mods are already in it.

People arrive with a `masters.db` that has already been modded - by another
tool, by a pack's own installer, or by this manager before a reinstall. The
alternative to this module is telling them to start from a clean file and lose
what they have, which nobody does; they keep the modded file, the manager
treats it as "vanilla", and every undo from then on is wrong.

So: compare their database with the matching clean one, then work out how much
of that difference each installed mod accounts for. What is left over belongs to
nobody, and can be turned into an ordinary mod they can switch off like any
other.

Nothing here writes anything - it reports, and the caller decides.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import dbdiff
from .dbdiff import DbDelta, RowUpdate, TableDelta


@dataclass
class ModMatch:
    """How much of one mod is already in the database."""

    mod_id: str
    name: str
    cells: int = 0  # values the mod would write
    present: int = 0  # of those, the ones already there with the mod's value
    inserts: int = 0  # rows the mod would add
    inserts_present: int = 0
    files: int = 0  # game files the mod installs
    files_present: int = 0
    # For a mod with values: the values it was matched at, as the player reads
    # them ("Multiplier x7"), and where they came from - see VALUES_FROM_*.
    values: dict = field(default_factory=dict)
    values_text: str = ""
    values_from: str = ""
    # Every cell the mod writes has been changed, but no single set of values
    # gives exactly what is there: the mod, most likely, plus someone's edits.
    values_unknown: bool = False
    # The database's own note lists this mod (db_record).
    in_record: bool = False
    recorded_version: str = ""
    installed_version: str = ""

    @property
    def changes(self) -> int:
        return self.cells + self.inserts

    @property
    def found(self) -> int:
        return self.present + self.inserts_present

    @property
    def database_complete(self) -> bool:
        return self.changes > 0 and self.found == self.changes

    @property
    def files_complete(self) -> bool:
        return self.files == 0 or self.files_present == self.files

    @property
    def applied(self) -> bool:
        """Everything this mod does is already in place."""
        if self.changes == 0 and self.files == 0:
            return False
        return (self.changes == 0 or self.database_complete) and self.files_complete

    @property
    def partial(self) -> bool:
        """Some of it is there and some is not - worth saying out loud."""
        return (
            not self.applied
            and not self.values_unknown
            and (self.found > 0 or self.files_present > 0)
        )

    @property
    def version_changed(self) -> bool:
        """The database has a different version of this mod than is installed."""
        return bool(
            self.recorded_version
            and self.installed_version
            and self.recorded_version != self.installed_version
        )

    def summary(self) -> str:
        bits = []
        if self.values_unknown:
            bits.append("changed with values that could not be worked out")
        elif self.changes:
            bits.append(f"{self.found:,} of {self.changes:,} database change(s)")
        if self.files:
            bits.append(f"{self.files_present} of {self.files} game file(s)")
        if self.values_text and not self.values_unknown:
            bits.append(self.values_text)
        if self.version_changed:
            bits.append(
                f"saved with version {self.recorded_version}, "
                f"version {self.installed_version} installed"
            )
        return ", ".join(bits) or "nothing to look for"


VALUES_FROM_RECORD = "record"  # the database's own note, checked against the cells
VALUES_FROM_CHOSEN = "chosen"  # what the player has chosen in this manager
VALUES_FROM_WORKED_OUT = "worked out"  # read back from the cells themselves


@dataclass
class AdoptionReport:
    """What a database turned out to contain."""

    vanilla: Path | None = None
    total: DbDelta = field(default_factory=DbDelta)
    matches: list[ModMatch] = field(default_factory=list)
    leftover: DbDelta = field(default_factory=DbDelta)
    error: str = ""
    # Mods the database's note lists that are not installed here.
    not_installed: list = field(default_factory=list)
    record_note: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def recognised(self) -> list[ModMatch]:
        return [m for m in self.matches if m.applied]

    @property
    def partial(self) -> list[ModMatch]:
        return [m for m in self.matches if m.partial]

    @property
    def unknown_values(self) -> list[ModMatch]:
        return [m for m in self.matches if m.values_unknown]

    @property
    def has_leftover(self) -> bool:
        return not self.leftover.empty

    def summary(self) -> str:
        if self.error:
            return self.error
        if self.total.empty:
            return "this database matches vanilla - nothing has been modded"
        bits = [f"{self.total.summary()} in all"]
        if self.recognised:
            bits.append(f"{len(self.recognised)} mod(s) recognised")
        if self.unknown_values:
            bits.append(
                f"{len(self.unknown_values)} mod(s) with values that could not be worked out"
            )
        if self.not_installed:
            bits.append(f"{len(self.not_installed)} listed mod(s) not installed")
        if self.has_leftover:
            bits.append(f"{self.leftover.change_count:,} change(s) belonging to no mod")
        return "; ".join(bits)


def _index(delta: DbDelta) -> tuple[dict, dict, dict]:
    """Three ways to look a change up.

    Updates and most inserts are found by primary key. Some of the game's
    tables have no primary key at all - ``master_part_equipment`` is one - and
    a row added to one of those has nothing to be addressed by, so those are
    counted per table by their values instead. Without that they could never
    match anything, and a mod adding ten of them looked forever half-applied.
    """
    updates: dict[tuple[str, tuple], dict] = {}
    inserts: dict[tuple[str, tuple], tuple] = {}
    loose: dict[str, Counter] = {}
    for table_delta in delta.tables:
        for update in table_delta.updates:
            updates[(table_delta.table, update.key)] = update.changes
        for row in table_delta.inserts:
            key = dbdiff.insert_key(table_delta, row)
            if key is not None:
                inserts[(table_delta.table, key)] = row
            else:
                loose.setdefault(table_delta.table, Counter())[tuple(row)] += 1
    return updates, inserts, loose


def match_mod(mod_id: str, name: str, mod_delta: DbDelta, theirs: DbDelta) -> ModMatch:
    """How much of ``mod_delta`` the database already has, cell by cell.

    A mod's delta is measured against the same vanilla, so every cell in it
    genuinely differs from stock - which makes "the database has this exact
    value" a fair test of whether the mod is already applied. Matching on the
    values rather than on row counts means a mod that half-applied, or one whose
    rows another mod later overwrote, reads as partial instead of as present.
    """
    match = ModMatch(mod_id=mod_id, name=name)
    their_updates, their_inserts, their_loose = _index(theirs)
    for table_delta in mod_delta.tables:
        for update in table_delta.updates:
            theirs_here = their_updates.get((table_delta.table, update.key), {})
            for column, value in update.changes.items():
                match.cells += 1
                if column in theirs_here and theirs_here[column] == value:
                    match.present += 1
        for row in table_delta.inserts:
            key = dbdiff.insert_key(table_delta, row)
            match.inserts += 1
            if key is not None:
                if their_inserts.get((table_delta.table, key)) == row:
                    match.inserts_present += 1
                continue
            # No key to match on: found if the same row is there by value. The
            # count is spent as it is used, so a mod adding the same row twice
            # needs it there twice.
            available = their_loose.get(table_delta.table)
            if available and available[tuple(row)] > 0:
                available[tuple(row)] -= 1
                match.inserts_present += 1
    return match


def _explained(
    matches: list[ModMatch], deltas: dict[str, DbDelta]
) -> tuple[set, set, dict]:
    """Every cell and inserted row the recognised mods account for.

    Rows in keyless tables come back as a per-table count of row values, the
    same way they are matched - otherwise they would be recognised as part of a
    mod and *also* reported as belonging to nobody.
    """
    cells: set[tuple[str, tuple, str]] = set()
    rows: set[tuple[str, tuple]] = set()
    loose: dict[str, Counter] = {}
    for match in matches:
        delta = deltas.get(match.mod_id)
        if delta is None:
            continue
        for table_delta in delta.tables:
            for update in table_delta.updates:
                for column in update.changes:
                    cells.add((table_delta.table, update.key, column))
            for row in table_delta.inserts:
                key = dbdiff.insert_key(table_delta, row)
                if key is not None:
                    rows.add((table_delta.table, key))
                else:
                    loose.setdefault(table_delta.table, Counter())[tuple(row)] += 1
    return cells, rows, loose


def subtract(theirs: DbDelta, cells: set, rows: set, loose: dict | None = None) -> DbDelta:
    """``theirs`` with everything in ``cells``/``rows`` taken out.

    Per column rather than per row: one mod may own a weapon's durability while
    the player hand-edited its attack, and only the attack is left over.
    """
    left = DbDelta(warnings=list(theirs.warnings))
    for table_delta in theirs.tables:
        updates = []
        for update in table_delta.updates:
            keep = {
                column: value
                for column, value in update.changes.items()
                if (table_delta.table, update.key, column) not in cells
            }
            if keep:
                updates.append(
                    RowUpdate(
                        key=update.key,
                        changes=keep,
                        before={c: v for c, v in update.before.items() if c in keep},
                    )
                )
        spent = dict(loose or {}).get(table_delta.table)
        spent = Counter(spent) if spent else None
        inserts = []
        for row in table_delta.inserts:
            key = dbdiff.insert_key(table_delta, row)
            if key is not None:
                if (table_delta.table, key) not in rows:
                    inserts.append(row)
                continue
            if spent and spent[tuple(row)] > 0:
                spent[tuple(row)] -= 1  # a recognised mod put this one there
                continue
            inserts.append(row)
        trimmed = TableDelta(
            table_delta.table,
            list(table_delta.key_columns),
            list(table_delta.columns),
            updates=updates,
            inserts=inserts,
            deletes=list(table_delta.deletes),
            keyed_by_rowid=table_delta.keyed_by_rowid,
            create_sql=table_delta.create_sql,
            index_sql=list(table_delta.index_sql),
        )
        if not trimmed.empty:
            left.tables.append(trimmed)
    return left


def count_installed_files(mod, game_root: Path | None) -> tuple[int, int]:
    """(files the mod installs, how many are already in the game folder).

    Only whether the file is there, not whether it is the same file: a content
    pack is a couple of hundred packages and hashing them all to answer "is this
    pack installed" would cost more than the whole rest of the scan.
    """
    targets = list(mod.asset_targets())
    if not targets or game_root is None:
        return 0, 0
    present = 0
    for target in targets:
        if (Path(game_root) / str(target).replace("\\", "/")).exists():
            present += 1
    return len(targets), present


# Share of a mod's cells that must have been changed, whatever to, before it is
# worth working out which values would explain them.
FOOTPRINT_SHARE = 0.9


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _cells(delta: DbDelta) -> dict[tuple, tuple]:
    """(table, key, column) -> (stock value, the mod's value)."""
    out = {}
    for table_delta in delta.tables:
        for update in table_delta.updates:
            for column, value in update.changes.items():
                out[(table_delta.table, update.key, column)] = (update.before.get(column), value)
    return out


def _their_cells(theirs: DbDelta) -> dict[tuple, object]:
    return {
        (table_delta.table, update.key, column): value
        for table_delta in theirs.tables
        for update in table_delta.updates
        for column, value in update.changes.items()
    }


def footprint(mod_delta: DbDelta, their_cells: dict) -> float:
    """How much of what the mod writes has been changed in their database."""
    cells = _cells(mod_delta)
    if not cells:
        return 0.0
    return sum(1 for cell in cells if cell in their_cells) / len(cells)


def _snap(setting, x: float):
    """``x`` as a value the setting allows, or None if it cannot be one."""
    if not math.isfinite(x):
        return None
    # Not onto the step: a step is how far the arrows move the box, and a
    # typed-in 30,000 on a range starting at 1 with a step of 1,000 is allowed.
    if setting.kind == "integer":
        value = int(round(x))
    else:
        value = round(float(x), 10)
    if value < setting.minimum or value > setting.maximum:
        return None
    return value


def _second_value(setting, a):
    """A value to measure the setting's effect against, close to ``a``.

    Close rather than extreme: a mod may cap what it writes, and a cap reached
    at the far end of the range would read as no effect at all.
    """
    step = setting.step or 1
    for b in (a * 2, a + step * 10, a + step, a / 2, a - step):
        b = _snap(setting, b)
        if b is not None and b != a:
            return b
    return None


def infer_values(mod, delta_for, theirs: DbDelta, start: dict) -> dict | None:
    """The values that make ``mod`` write what is in ``theirs``, or None.

    Each setting's effect is measured by building the mod at two values and
    seeing how every number it writes moves; each cell then says which value
    would have produced what is there, and the most common answer is taken.
    That is exact for the usual multipliers and prices, and the caller checks
    the answer cell by cell anyway, so a formula this cannot see through comes
    back as "could not be worked out" rather than as a wrong guess.
    """
    their_cells = _their_cells(theirs)
    values = dict(start)
    for _ in range(1 if len(mod.settings) == 1 else 2):
        for setting in mod.settings:
            a = values[setting.id]
            b = _second_value(setting, a)
            if b is None:
                return None
            at_a = delta_for(mod.with_settings({**values, setting.id: a}))
            at_b = delta_for(mod.with_settings({**values, setting.id: b}))
            if at_a is None or at_b is None:
                return None
            cells_a, cells_b = _cells(at_a), _cells(at_b)
            votes: Counter = Counter()
            for cell in set(cells_a) | set(cells_b):
                stock = (cells_a.get(cell) or cells_b.get(cell))[0]
                value_a = cells_a[cell][1] if cell in cells_a else stock
                value_b = cells_b[cell][1] if cell in cells_b else stock
                there = their_cells.get(cell, stock)
                if not all(_is_number(v) for v in (value_a, value_b, there)):
                    continue
                if value_a == value_b:
                    continue
                guess = _snap(setting, a + (there - value_a) * (b - a) / (value_b - value_a))
                if guess is not None:
                    votes[guess] += 1
            if not votes:
                return None
            values[setting.id] = votes.most_common(1)[0][0]
    return values


def _values_text(mod, values: dict) -> str:
    """"x6" for a mod with one value; "Multiplier x6, Bonus 40 KC" for several."""
    shown = [setting for setting in mod.settings if setting.id in values]
    if len(shown) == 1:
        return shown[0].display(values[shown[0].id])
    return ", ".join(f"{setting.label} {setting.display(values[setting.id])}" for setting in shown)


def _match_values(mod, delta_for, theirs: DbDelta, tries: list[tuple[dict, str]]):
    """Look for ``mod`` at each set of values in turn.

    (match, delta) for the first set every cell agrees with, else for the set
    that got closest; (None, None) when the mod could not be built at all.
    """
    best = (None, None)
    seen = []
    for stored, source in tries:
        candidate = mod.with_settings(stored)
        if candidate.values in seen:
            continue
        seen.append(candidate.values)
        delta = delta_for(candidate)
        if delta is None:
            continue
        match = match_mod(mod.id, mod.name, delta, theirs)
        if mod.settings:
            match.values = dict(candidate.values)
            match.values_text = _values_text(mod, candidate.values)
            match.values_from = source
        if match.database_complete:
            return match, delta
        if best[0] is None or match.found > best[0].found:
            best = (match, delta)
    return best


def scan(
    vanilla: Path,
    db_path: Path,
    mods,
    delta_for,
    game_root: Path | None = None,
    *,
    record=None,
    chosen: dict | None = None,
) -> AdoptionReport:
    """Compare a database with vanilla and attribute the difference to mods.

    ``delta_for(mod)`` hands back what that mod changes, measured against the
    same vanilla - the manager already computes and caches exactly that.

    A mod with values is looked for at, in turn: the values the database's own
    note (``record``) gives, the values the player has ``chosen``, and failing
    both, the values its cells say it was applied with. Whichever it is, every
    cell has to agree before the mod counts as found.
    """
    report = AdoptionReport(vanilla=Path(vanilla))
    try:
        report.total = dbdiff.compare(Path(vanilla), Path(db_path))
    except Exception as exc:
        report.error = f"could not compare with the clean copy: {exc}"
        return report
    chosen = chosen or {}
    if record is not None:
        report.record_note = record.unreadable
        installed = {mod.id for mod in mods}
        report.not_installed = [e for e in record.mods if e.mod_id not in installed]
    their_cells = None

    deltas: dict[str, DbDelta] = {}
    for mod in mods:
        entry = record.get(mod.id) if record is not None else None
        tries = []
        if entry is not None:
            tries.append((entry.values, VALUES_FROM_RECORD))
        tries.append((chosen.get(mod.id), VALUES_FROM_CHOSEN))
        match, mod_delta = _match_values(mod, delta_for, report.total, tries)
        if match is None:
            continue

        if mod.settings and not match.database_complete:
            if their_cells is None:
                their_cells = _their_cells(report.total)
            if footprint(mod_delta, their_cells) >= FOOTPRINT_SHARE:
                match, mod_delta = _work_out(mod, delta_for, report.total, match, mod_delta)

        match.files, match.files_present = count_installed_files(mod, game_root)
        match.installed_version = mod.version
        if entry is not None:
            match.in_record = True
            match.recorded_version = entry.version
        deltas[mod.id] = mod_delta
        if match.changes or match.files:
            report.matches.append(match)

    report.matches.sort(key=lambda m: (not m.applied, m.values_unknown is False, m.name.lower()))
    # A mod whose values could not be pinned down does not get its cells: a
    # rebuild would apply it at values that are not the ones there, and those
    # cells left out of "belongs to no mod" could then not be kept at all.
    cells, rows, loose = _explained(report.recognised, deltas)
    report.leftover = subtract(report.total, cells, rows, loose)
    return report


def _work_out(mod, delta_for, theirs: DbDelta, match: ModMatch, mod_delta: DbDelta):
    """Every cell the mod writes is changed, just not to these values: find which."""
    worked_out = infer_values(mod, delta_for, theirs, match.values)
    if worked_out is not None:
        tries = [(worked_out, VALUES_FROM_WORKED_OUT)]
        if len(mod.settings) == 1:
            # One either side, for a value the game's own rounding nudged.
            setting = mod.settings[0]
            for nudge in (setting.step or 1, -(setting.step or 1)):
                near = _snap(setting, worked_out[setting.id] + nudge)
                if near is not None:
                    tries.append(({setting.id: near}, VALUES_FROM_WORKED_OUT))
        found, found_delta = _match_values(mod, delta_for, theirs, tries)
        if found is not None and found.database_complete:
            return found, found_delta
    match.values_unknown = True
    return match, mod_delta
