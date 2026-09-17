"""The Mod dataclass and the JSON / SQL constructors that build one."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ModLoadError
from .patch import Patch, RawSqlFilePatch
from .settings import (
    ModSetting,
    check_placeholders,
    parse_settings,
    render,
    render_text,
    resolve_values,
)

REQUIRED_FIELDS = ("id", "name", "description", "version", "author")
STRING_LIST_FIELDS = ("requires", "conflicts_with", "raw_sql_files_do_not_touch")

# Source layouts, per the detection rule in the spec.
SOURCE_JSON = "mod.json"
SOURCE_SQL = "mod.sql"
SOURCE_SQL_PAIR = "mod.sql + inverse.sql"

# How a mod reaches the database.
APPLY_DIRECT = "direct"  # run its SQL against your database (the default)
APPLY_DIFF = "diff"      # run it against vanilla, write only what differs
APPLY_MODES = (APPLY_DIRECT, APPLY_DIFF)


@dataclass
class Mod:
    """One mod folder, parsed and ready to validate or apply."""

    id: str
    name: str
    description: str
    version: str
    author: str
    folder: Path
    patches: list[Patch] = field(default_factory=list)
    homepage: str = ""
    # The game build this mod was built against, as master_const_str's
    # TITLE_VERSION reads it - "5.0.3.0.0 - 1.87". Optional, and only ever used
    # to warn: a mod made on one build usually still fits the next, but when it
    # does not, the failure is silent, so the mismatch is worth saying out loud.
    game_version: str = ""
    # Every build it has been checked against, oldest first; ``game_version``
    # is the newest of them. mod.json may give one string or a list.
    game_versions: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    raw_sql_files_do_not_touch: list[str] = field(default_factory=list)
    source: str = SOURCE_JSON
    apply_mode: str = APPLY_DIRECT
    inverse_sql: Path | None = None
    load_warnings: list[str] = field(default_factory=list)
    # Numbers the player may choose - see settings.py. ``patch_data`` and
    # ``description_template`` are the mod as written, placeholders and all, so
    # it can be filled in again with different values; ``patches`` and
    # ``description`` are always filled in, with ``values``.
    settings: list[ModSetting] = field(default_factory=list)
    patch_data: list = field(default_factory=list)
    description_template: str = ""
    values: dict = field(default_factory=dict)

    # -- settings --------------------------------------------------------

    def setting(self, setting_id: str) -> ModSetting | None:
        for setting in self.settings:
            if setting.id == setting_id:
                return setting
        return None

    def with_settings(self, stored: dict | None) -> "Mod":
        """This mod with the player's chosen values filled in.

        Rebuilt from the mod as written, never from an already-filled copy, so
        choosing x5 after x2 gives x5 - not x2 filled in a second time.
        """
        if not self.settings:
            return self
        values, _problems = resolve_values(self.settings, stored)
        if values == self.values:
            return self
        return _filled_in(self, values)

    # -- convenience -----------------------------------------------------

    @property
    def readme(self) -> Path | None:
        for name in ("readme.md", "README.md", "readme.txt"):
            candidate = self.folder / name
            if candidate.is_file():
                return candidate
        return None

    @property
    def screenshot(self) -> Path | None:
        for name in ("screenshot.png", "screenshot.jpg", "screenshot.jpeg"):
            candidate = self.folder / name
            if candidate.is_file():
                return candidate
        return None

    @property
    def has_inverse_sql(self) -> bool:
        return self.inverse_sql is not None and self.inverse_sql.is_file()

    def tables(self) -> set[str]:
        tables: set[str] = set()
        for patch in self.patches:
            tables |= patch.tables()
        return tables

    def targets(self) -> set[tuple[str, str]]:
        """(table, column) pairs this mod writes, minus declared exclusions."""
        excluded = set(self.raw_sql_files_do_not_touch)
        pairs: set[tuple[str, str]] = set()
        for patch in self.patches:
            for table, column in patch.targets():
                if table in excluded:
                    continue
                pairs.add((table, column))
        return pairs

    def asset_targets(self) -> list[str]:
        """Game files this mod copies, as forward-slash paths under the game root."""
        found: set[str] = set()
        for patch in self.patches:
            found |= patch.asset_targets()
        return sorted(found)

    def affects_label(self) -> str:
        """Short "Affects: ..." string for the mod list."""
        by_table: dict[str, list[str]] = {}
        for table, column in sorted(self.targets()):
            by_table.setdefault(table, []).append(column)
        parts = []
        for table, columns in by_table.items():
            if columns == ["*"]:
                parts.append(f"{table} (whole table)")
            else:
                parts.append(f"{table} ({', '.join(columns)})")
        assets = self.asset_targets()
        if assets:
            names = [a.rsplit("/", 1)[-1] for a in assets]
            shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
            parts.append(f"game file(s): {shown}")
        return "; ".join(parts) or "(nothing detected)"


def _build_patches(patch_data: list, mod_dir: Path, values: dict) -> list[Patch]:
    patches = [
        Patch.from_dict(render(entry, values) if values else entry, mod_dir, index)
        for index, entry in enumerate(patch_data)
    ]
    for patch in patches:
        patch.setting_values = dict(values)
    return patches


def _filled_in(mod: Mod, values: dict) -> Mod:
    return replace(
        mod,
        patches=_build_patches(mod.patch_data, mod.folder, values),
        description=render_text(mod.description_template, values),
        values=dict(values),
    )


def _as_string_list(value: Any, mod_ref: str, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ModLoadError(mod_ref, f"'{field_name}' must be an array of strings")
    return list(value)


def load_mod_json(mod_dir: Path) -> Mod:
    """Build a Mod from ``<mod_dir>/mod.json``."""
    path = mod_dir / "mod.json"
    mod_ref = mod_dir.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ModLoadError(mod_ref, f"could not read mod.json: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModLoadError(
            mod_ref, f"mod.json is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ModLoadError(mod_ref, "mod.json must contain a JSON object at the top level")

    missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, "") or "").strip()]
    if missing:
        raise ModLoadError(mod_ref, f"mod.json is missing required field(s): {', '.join(missing)}")

    patch_data = data.get("patches")
    if not isinstance(patch_data, list) or not patch_data:
        raise ModLoadError(mod_ref, "mod.json needs a non-empty 'patches' array")

    warnings: list[str] = []
    mod_id = str(data["id"]).strip()
    if mod_id != mod_dir.name:
        warnings.append(
            f"mod id {mod_id!r} does not match its folder name {mod_dir.name!r}; "
            "the folder name is used as the identity"
        )

    # Settings are only looked for in a mod that declares some. A mod without
    # them is read exactly as before, braces and all, so nothing that already
    # loads can start failing because its text happens to contain "{{".
    settings = parse_settings(data.get("settings"), mod_ref)
    description_template = str(data["description"]).strip()
    defaults = {setting.id: setting.default for setting in settings}
    if settings:
        used = check_placeholders(patch_data, settings, mod_ref, "a patch")
        used |= check_placeholders(description_template, settings, mod_ref, "the description")
    patches = _build_patches(patch_data, mod_dir, defaults)
    if settings:
        for patch in patches:
            if isinstance(patch, RawSqlFilePatch) and patch.path.is_file():
                used |= check_placeholders(
                    patch.path.read_text(encoding="utf-8-sig"), settings, mod_ref, patch.rel_path
                )
        for setting in settings:
            if setting.id not in used:
                warnings.append(
                    f"setting {setting.id!r} is never used, so changing it does nothing"
                )

    apply_mode = str(data.get("apply", APPLY_DIRECT)).strip().lower() or APPLY_DIRECT
    if apply_mode not in APPLY_MODES:
        raise ModLoadError(
            mod_ref,
            f"'apply' must be one of {', '.join(APPLY_MODES)}, not {apply_mode!r}",
        )

    inverse = mod_dir / "inverse.sql"
    raw_versions = data.get("game_version")
    if isinstance(raw_versions, list):
        game_versions = [str(v).strip() for v in raw_versions if str(v or "").strip()]
    else:
        game_versions = [str(raw_versions or "").strip()] if str(raw_versions or "").strip() else []
    game_versions = list(dict.fromkeys(game_versions))

    return Mod(
        id=mod_dir.name,
        name=str(data["name"]).strip(),
        description=render_text(description_template, defaults) if settings else description_template,
        version=str(data["version"]).strip(),
        author=str(data["author"]).strip(),
        folder=mod_dir,
        patches=patches,
        homepage=str(data.get("homepage", "") or ""),
        game_version=(game_versions[-1] if game_versions else ""),
        game_versions=game_versions,
        requires=_as_string_list(data.get("requires"), mod_ref, "requires"),
        conflicts_with=_as_string_list(data.get("conflicts_with"), mod_ref, "conflicts_with"),
        raw_sql_files_do_not_touch=_as_string_list(
            data.get("raw_sql_files_do_not_touch"), mod_ref, "raw_sql_files_do_not_touch"
        ),
        source=SOURCE_JSON,
        apply_mode=apply_mode,
        inverse_sql=inverse if inverse.is_file() else None,
        load_warnings=warnings,
        settings=settings,
        patch_data=list(patch_data),
        description_template=description_template,
        values=dict(defaults),
    )


def _title_from_folder(name: str) -> str:
    return re.sub(r"[-_]+", " ", name).strip().title()


def load_mod_sql(mod_dir: Path, sql_file: Path) -> Mod:
    """Build a Mod from a bare ``.sql`` file, with metadata derived from the folder."""
    inverse = mod_dir / "inverse.sql"
    paired = sql_file.name.lower() == "mod.sql" and inverse.is_file()
    patch = Patch.from_dict(
        {
            "type": "raw_sql_file",
            "path": sql_file.name,
            "description": f"Raw SQL from {sql_file.name}",
        },
        mod_dir,
        0,
    )
    revert_note = (
        "Revert runs inverse.sql." if paired else "Revert uses the automatic pre-apply snapshot."
    )
    return Mod(
        id=mod_dir.name,
        name=_title_from_folder(mod_dir.name),
        description=f"Raw SQL mod (no metadata). {revert_note}",
        version="0.0.0",
        author="(unknown - .sql only)",
        folder=mod_dir,
        patches=[patch],
        source=SOURCE_SQL_PAIR if paired else SOURCE_SQL,
        inverse_sql=inverse if paired else None,
    )
