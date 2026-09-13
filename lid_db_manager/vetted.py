"""Recordings the manager vouches for.

Some things a mod might want to do are safe enough to allow and too dangerous
to leave open. Changing one hash in the game executable's table is the case
this exists for: it is the only way to replace a package the game checks, and
it is also exactly what someone would want in order to slip a modified file
past that check.

So it is not a capability mods have. A mod cannot ask for a package name and a
pair of hashes; it can only name a recording that already ships here, and each
one has been recorded from a specific release, against a specific game build,
and looked at before it was added. An unknown name does not load.

Adding one is deliberately a code change to this repository - see
``tools/build_buttons_recipe.py``, and the README under "Vetted recordings".
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECIPES_DIRNAME = "recipes"
EXE_PREFIX = "exe-"

# The one file a recording of this kind is ever allowed to change. Not a
# setting: a recording naming anything else is refused when it is loaded.
GAME_EXE = "Binaries/Win64/BrgGame-Steam.exe"

# The patch type a recording is carried out by. Named here rather than imported
# so this module stays free of the patch machinery.
ExeChecksumType = "exe_checksum_entry"

# How a mod release of this kind describes itself.
MANIFEST_NAME = "manifest.json"


class VettedError(ValueError):
    """A recording is missing, malformed, or asks for something not allowed."""


def _recipes_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "lid_db_manager" / RECIPES_DIRNAME
    return Path(__file__).resolve().parent / RECIPES_DIRNAME


@dataclass(frozen=True)
class ExeRecipe:
    """One vetted change to one hash in the game executable's table."""

    name: str                 # the id a mod refers to, e.g. "exe-buttons-1.2"
    mod: str                  # what it is, for the mod list
    version: str
    package: str              # the package whose hash changes
    checksum_before: str      # what that hash must currently be
    checksum_after: str       # what it becomes - the modded package's SHA-1
    asset_sha256: str         # the modded package itself, so it can be checked
    code_fingerprint: str     # the game build this was recorded against
    steam_build: str = ""
    manifest_sha256: str = ""  # fingerprint of the mod release it came from
    recorded: str = ""
    target: str = GAME_EXE
    notes: str = ""

    @property
    def summary(self) -> str:
        return f"{self.mod} v{self.version}"

    @property
    def mod_json_path(self) -> Path:
        """The mod.json that ships beside this recording.

        Written out rather than assembled at install time, so what a player
        ends up running is a file in the repository that can be read and
        diffed - the same reason the recording itself is a file.
        """
        return _recipes_dir() / f"{self.name}.mod.json"

    def mod_json(self) -> dict:
        try:
            data = json.loads(self.mod_json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VettedError(
                f"{self.name}: its mod.json is missing or unreadable ({exc})"
            ) from exc
        names = [str(p.get("recipe", "")) for p in data.get("patches", [])
                 if p.get("type") == ExeChecksumType]
        if names != [self.name]:
            # The pair must agree, or the recording vouches for something other
            # than what would actually be installed.
            raise VettedError(
                f"{self.name}: its mod.json does not name this recording "
                f"(found {names or 'none'})"
            )
        return data


def _required(data: dict, key: str, where: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise VettedError(f"{where.name}: missing '{key}'")
    return value


def _load_one(path: Path) -> ExeRecipe:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VettedError(f"{path.name}: cannot be read ({exc})") from exc
    if not isinstance(data, dict):
        raise VettedError(f"{path.name}: not an object")

    target = str(data.get("target", GAME_EXE)).replace("\\", "/")
    if target != GAME_EXE:
        # A recording of this kind changes one hash in the game executable.
        # Anything else is out of scope and refused rather than carried out.
        raise VettedError(f"{path.name}: target must be {GAME_EXE}, not {target!r}")

    recipe = ExeRecipe(
        name=path.stem,
        mod=_required(data, "mod", path),
        version=str(data.get("version", "")),
        package=_required(data, "package", path),
        checksum_before=_required(data, "checksum_before", path).lower(),
        checksum_after=_required(data, "checksum_after", path).lower(),
        asset_sha256=_required(data, "asset_sha256", path).lower(),
        code_fingerprint=_required(data, "code_fingerprint", path).lower(),
        steam_build=str(data.get("steam_build", "")),
        manifest_sha256=str(data.get("manifest_sha256", "")).lower(),
        recorded=str(data.get("recorded", "")),
        target=target,
        notes=str(data.get("notes", "")),
    )
    for label, value, length in (
        ("checksum_before", recipe.checksum_before, 40),
        ("checksum_after", recipe.checksum_after, 40),
        ("asset_sha256", recipe.asset_sha256, 64),
        ("code_fingerprint", recipe.code_fingerprint, 64),
    ):
        if len(value) != length or any(c not in "0123456789abcdef" for c in value):
            raise VettedError(f"{path.name}: '{label}' is not a {length}-character hex hash")
    if recipe.checksum_before == recipe.checksum_after:
        raise VettedError(f"{path.name}: before and after hashes are the same")
    if not recipe.package.lower().endswith(".upk"):
        raise VettedError(f"{path.name}: 'package' is not a .upk")
    return recipe


def known_exe_recipes(directory: Path | None = None) -> dict[str, ExeRecipe]:
    """Every vetted executable recording, keyed by name.

    A malformed file here is skipped rather than fatal - one bad recording must
    not stop the manager starting - but it is never loaded half-read.
    """
    folder = Path(directory) if directory is not None else _recipes_dir()
    found: dict[str, ExeRecipe] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob(f"{EXE_PREFIX}*.json")):
        try:
            recipe = _load_one(path)
        except VettedError:
            continue
        found[recipe.name] = recipe
    return found


def exe_recipe(name: str, directory: Path | None = None) -> ExeRecipe:
    """One recording by name, or a refusal naming what is actually available."""
    available = known_exe_recipes(directory)
    found = available.get(name)
    if found is None:
        known = ", ".join(sorted(available)) or "none"
        raise VettedError(
            f"{name!r} is not a recording this manager ships, so it will not be "
            f"carried out. Changes to the game executable are limited to "
            f"recordings that ship with the manager (available: {known})."
        )
    return found


def sha256_of(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identify_mod_folder(folder: Path,
                        recipes: dict[str, ExeRecipe] | None = None) -> ExeRecipe | None:
    """The vetted recording this folder is the mod release for, if any.

    Matched on the release's own ``manifest.json``, hashed whole. That file
    names the package, the hash it had and the hash it becomes, so a folder
    whose manifest matches is the release the recording was made from - and one
    that differs by a byte is not, whatever its version says.

    The replacement package has to be there too, and be the one recorded, since
    that file's hash is what would be written into the executable.
    """
    folder = Path(folder)
    manifest = folder / MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        fingerprint = sha256_of(manifest)
    except OSError:
        return None
    for recipe in (known_exe_recipes() if recipes is None else recipes).values():
        if not recipe.manifest_sha256 or recipe.manifest_sha256 != fingerprint:
            continue
        package = folder / recipe.package
        if not package.is_file():
            return None
        try:
            if sha256_of(package) != recipe.asset_sha256:
                return None
        except OSError:
            return None
        return recipe
    return None
