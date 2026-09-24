"""Applying and reverting whole game files - the file-level counterpart of
``backup.py`` + ``runner.py``.

Where the database side runs everything inside one SQLite transaction, file
copies have no transaction to lean on, so this module does the same guarantees
by hand:

    * the first time any enabled mod claims a game file, the file that is there
      is copied into ``backups/game_files/`` and never touched again - the
      file-level equivalent of ``masters.db.original``;
    * an apply is all-or-nothing: if one copy fails partway through, every file
      this run already changed is put back to its pre-run state;
    * revert restores the reverted mods' files to that saved copy (or deletes
      them, if the run created them fresh) and then re-applies whatever is still
      enabled, so a file another mod also claims comes straight back.

Nothing here parses the .upk format. A game file is an opaque blob that gets
copied, hash-checked and swapped into place atomically.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import configini, exe_check_off, exe_checksums, vetted
from .progress import Progress, ensure as ensure_progress
from .sqlutil import sha256_file

# Under paths.backups_dir. Holds the ".original" copies plus a manifest that
# maps each game-relative target to its backup filename (or null when the file
# did not exist before any mod touched it).
GAME_FILES_DIRNAME = "game_files"
MANIFEST_NAME = "manifest.json"
# Staging area for the current run's rollback copies. Emptied on success.
ROLLBACK_DIRNAME = ".rollback"

GAME_PROCESS = "BrgGame-Steam.exe"

# Files Windows runs rather than reads. A content pack is models, textures and
# sounds; it never needs one of these. They matter because the game loads DLLs
# from its own folder at startup (Binaries/Win64 already holds eight), so a
# "mod" that drops one there gets its code run, with the player's permissions,
# the next time they launch the game.
BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".dll", ".asi", ".sys", ".drv", ".ocx", ".cpl", ".scr", ".com",
    ".pif", ".msi", ".msp", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".jar",
})


def forbidden_target_reason(target: str) -> str:
    """Why no mod may write this game-relative path, or "" when it may.

    Applied to every file an asset patch would place, at load, at validation
    and again just before copying - so a file added to a mod folder after it
    was loaded is caught too.
    """
    text = str(target).replace("\\", "/")
    if ":" in text:
        # Never legitimate, and on NTFS "a.upk:b.exe" writes a hidden stream.
        return f"{target} contains ':', which is not allowed in a game file path"
    parts = [p for p in text.split("/") if p]
    if not parts:
        return ""
    # Windows silently drops trailing dots and spaces, so "version.dll." lands
    # on disk as "version.dll". Judge the name Windows will actually create.
    name = parts[-1].rstrip(". ").lower()
    suffix = Path(name).suffix
    if suffix in BLOCKED_EXTENSIONS:
        return (
            f"{target} is a program file ({suffix}), not game content - Windows "
            "would run it the next time the game starts. A content pack never needs one."
        )
    if name.startswith("masters.db"):
        # The database, its journals, and the manager's own backups of it -
        # replacing any of those bypasses every safety the database side has,
        # and overwriting masters.db.original destroys the way back to stock.
        return (
            f"{target} is the game database or one of its backups - mods change the "
            "database through database patches, never by replacing the file"
        )
    return ""


# --------------------------------------------------------------------------
# Locating the game folder
# --------------------------------------------------------------------------


def game_root_for(db_path: Path | str | None) -> Path | None:
    """Derive the game folder from masters.db's own location.

    LET IT DIE's layout is always ``<game>/BrgGame/Content/masters.db``, so the
    game root is three levels up. Returns None for anything that does not match
    (a test fixture, a loose copy on the desktop) so the caller can fall back to
    an explicit override rather than writing somewhere unexpected.
    """
    if not db_path:
        return None
    path = Path(db_path).resolve()
    parents = path.parents
    if len(parents) < 3:
        return None
    if parents[0].name.lower() != "content" or parents[1].name.lower() != "brggame":
        return None
    root = parents[2]
    if not (root / "BrgGame" / "CookedPCConsole").is_dir():
        return None
    return root


def game_lock_reason(db_path: Path | str | None = None) -> str:
    """Why game files must not be written right now, or "" when it is safe.

    The game holds its packages and its database open, so a running game means a
    copy will fail (a locked .upk) or corrupt (masters.db mid-write). A leftover
    -wal/-journal beside the database means it was not closed cleanly.
    """
    if _process_running(GAME_PROCESS):
        return "LET IT DIE is running. Close the game completely before applying or reverting mods."
    if db_path:
        base = Path(db_path)
        for suffix in ("-wal", "-journal"):
            if base.with_name(base.name + suffix).exists():
                return (
                    "The database has an open journal file - LET IT DIE may not have closed "
                    "cleanly. Start the game, let it reach the menu, quit, then try again."
                )
    return ""


def _process_running(exe_name: str) -> bool:
    """True if a process with this executable name is running. Windows only."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return False  # cannot tell - do not block on a guess
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        target = exe_name.lower()
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == target:
                return True
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return False


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@dataclass
class AssetApplyReport:
    ok: bool = True
    copied: list[str] = field(default_factory=list)   # targets written this run
    skipped: int = 0                                  # already in place, byte-identical
    restored: list[str] = field(default_factory=list)  # targets put back (revert / restore-all)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def did_something(self) -> bool:
        return bool(self.copied or self.restored)

    def summary_line(self) -> str:
        if not self.ok:
            return f"Game files: {self.error}"
        bits = []
        if self.copied:
            bits.append(f"{len(self.copied)} copied")
        if self.skipped:
            bits.append(f"{self.skipped} already in place")
        if self.restored:
            bits.append(f"{len(self.restored)} restored")
        return "Game files: " + (", ".join(bits) if bits else "nothing to do")


@dataclass
class AssetBackupEntry:
    target: str          # game-root-relative path
    backup_path: Path | None  # None when the file was absent before any mod


# --------------------------------------------------------------------------
# Paths, manifest, low-level copy
# --------------------------------------------------------------------------


def _store_dir(backups_dir: Path) -> Path:
    return Path(backups_dir) / GAME_FILES_DIRNAME


def _manifest_path(backups_dir: Path) -> Path:
    return _store_dir(backups_dir) / MANIFEST_NAME


def _load_manifest(backups_dir: Path) -> dict[str, dict]:
    path = _manifest_path(backups_dir)
    if not path.is_file():
        return {}
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, dict] = {}
    for target, entry in data.items():
        if isinstance(entry, dict):
            clean[str(target)] = {"backup": entry.get("backup")}
    return clean


def _save_manifest(backups_dir: Path, manifest: dict[str, dict]) -> None:
    import json

    store = _store_dir(backups_dir)
    store.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(backups_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _flatten(target: str) -> str:
    return target.replace("/", "__")


def _free_backup_name(store: Path, target: str, taken: set[str]) -> str:
    base = _flatten(target) + ".original"
    name = base
    counter = 2
    while name in taken or (store / name).exists():
        name = f"{_flatten(target)}-{counter}.original"
        counter += 1
    return name


def _resolve_target(game_root: Path, target: str) -> Path:
    """game_root / target, refusing anything that escapes game_root."""
    root = game_root.resolve()
    dest = (root / target).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        raise OSError(f"target path escapes the game folder: {target}") from None
    return dest


def _verified_copy(source: Path, dest: Path) -> None:
    """Atomic, hash-checked replace: temp file in the same dir, then os.replace.

    Mirrors the crossover installer's verified_copy - copy to a sibling temp,
    confirm the copy's hash matches the source, then swap it into place so the
    destination is never a half-written file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    want = sha256_file(source)
    handle, tmp_name = tempfile.mkstemp(
        prefix="." + dest.name + ".lid-", suffix=".tmp", dir=dest.parent
    )
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(source, tmp)
        if sha256_file(tmp) != want:
            raise OSError(f"copy of {source.name} did not verify")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def _wanted_files(mods_in_load_order) -> dict[str, tuple[Path, str]]:
    """target -> (source file, mod id). Last mod in load order wins per file."""
    wanted: dict[str, tuple[Path, str]] = {}
    for mod in mods_in_load_order:
        for patch in mod.patches:
            pairs = getattr(patch, "pairs", None)
            if not callable(pairs):
                continue
            for source, target in pairs():
                wanted[_normalise(target)] = (source, mod.id)
    return wanted


def _normalise(target: str) -> str:
    return target.replace("\\", "/").strip("/")


def _wanted_transforms(mods_in_load_order) -> dict[str, tuple[object, str]]:
    """target -> (patch, mod id) for patches that rewrite a game file in place.

    A transform is not a copy: there is no file in the mod folder to put down,
    only a change to make to the file already in the game. Two kinds exist: one
    hash inside the executable (limited to recordings that ship with the
    manager - see vetted.py), and packages rebuilt by a TFC Installer mod, which
    rewrite many files and say which through ``transform_targets``.
    """
    wanted: dict[str, tuple[object, str]] = {}
    for mod in mods_in_load_order:
        for patch in mod.patches:
            many = getattr(patch, "transform_targets", None)
            if callable(many):
                targets = many()
            elif callable(getattr(patch, "transform", None)):
                targets = patch.asset_targets()
            else:
                continue
            for target in targets:
                wanted[_normalise(target)] = (patch, mod.id)
    return wanted


def _wanted_ini(mods_in_load_order) -> dict[str, list[tuple[str, str, str, str]]]:
    """target -> [(section, key, value, mod id)] for config files, in load order.

    Unlike a copy, two mods writing the same config file do not fight over it:
    the keys are collected from all of them and laid down in order, so each one
    gets the keys it asked for and only a key both of them set is decided by
    load order. That is the whole reason a config file is not just an asset.
    """
    wanted: dict[str, list[tuple[str, str, str, str]]] = {}
    for mod in mods_in_load_order:
        for patch in mod.patches:
            entries = getattr(patch, "ini_entries", None)
            if not callable(entries):
                continue
            target = _normalise(patch.target)
            for section, key, value in entries():
                wanted.setdefault(target, []).append((section, key, value, mod.id))
    return wanted


def _stage_ini(
    ini: dict[str, list[tuple[str, str, str, str]]],
    game_root: Path,
    store: Path,
    manifest: dict[str, dict],
    staging: Path,
) -> dict[str, tuple[Path, str]]:
    """Work out each config file's new contents, without writing to the game.

    Built from the *pristine* file - the copy kept the first time any mod
    claimed it, or nothing at all when the manager created it - rather than
    from what is on disk. Reading the current file back would keep keys an
    earlier run wrote and this one no longer sets, so unticking a part would
    leave its values behind in the game.

    Returns the same shape ``_wanted_files`` does, so the staged file goes
    through the ordinary backup, copy and rollback path from here on.
    """
    out: dict[str, tuple[Path, str]] = {}
    if ini:
        staging.mkdir(parents=True, exist_ok=True)
    for target, contributions in sorted(ini.items()):
        dest = _resolve_target(game_root, target)
        entry = manifest.get(target)
        if entry is not None and entry.get("backup"):
            kept = store / entry["backup"]
            if not kept.is_file():
                raise OSError(f"the saved copy of {target} is missing")
            base = kept.read_bytes()
        elif entry is not None:
            base = None  # the manager created it; the game shipped no such file
        else:
            base = dest.read_bytes() if dest.is_file() else None
        produced = configini.build(base, [(s, k, v) for s, k, v, _ in contributions])
        staged = staging / _flatten(target)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(produced)
        out[target] = (staged, contributions[-1][3])
    return out


TEXTURE_INDEX_FILE = "texture-index.json"


def texture_index_file(backups_dir: Path) -> Path:
    """Where the list of which package holds which texture is kept between runs."""
    return Path(backups_dir).parent / "cache" / TEXTURE_INDEX_FILE


def _bind_tfc_patches(mods_in_load_order, game_root: Path, backups_dir: Path, log) -> list[str]:
    """Tie every TFC Installer mod to this game folder, before anything is planned.

    Two things are only known with the game in front of us: which packages hold
    a copy of each texture a pack replaces, and which Texture2D_N.tfc numbers
    are free. A cache this manager installed itself last time is not "taken" -
    it is this run's to reuse - so the same mods in the same order land on the
    same numbers every time. Returns warnings to pass on.
    """
    bound = [(mod, patch) for mod in mods_in_load_order for patch in mod.patches
             if callable(getattr(patch, "bind", None))]
    if not bound:
        return []
    from .upk import texture_index, tfcmod

    cooked = game_root / tfcmod.COOKED
    if not cooked.is_dir():
        raise OSError(f"{cooked} is not there - is this the game folder?")
    manifest = _load_manifest(backups_dir)
    ours = set()
    for target, entry in manifest.items():
        name = Path(target).name
        if entry.get("backup") is None and name.startswith("Texture2D_") and name.endswith(".tfc"):
            suffix = name[len("Texture2D_"):-len(".tfc")]
            if suffix.isdigit():
                ours.add(int(suffix))
    taken = tfcmod.cache_numbers_present(cooked) - ours

    cache_file = texture_index_file(backups_dir)
    if log and not cache_file.is_file():
        log.info("finding which game packages hold each texture - the first time "
                 "takes a few minutes, and is remembered after that")
    index = texture_index.build(cooked, cache_file)

    # A cache already in the game with exactly the pack's bytes is used as it
    # is. Without this, a mod installed before - by TFC Installer, or by an
    # install of the manager whose records are gone - gets a second copy of the
    # same file under a new number every time. Ones TFC Installer does not
    # claim are tried first, so its own install is left as it was.
    tfc_owned = _tfc_installer_caches(game_root)
    reusable = sorted(taken, key=lambda n: (n in tfc_owned, n))

    warnings = []
    for mod, patch in bound:
        installed_as = {}
        for own_number in sorted(patch.tfc.caches):
            same = _identical_cache(patch.tfc.caches[own_number], cooked, reusable)
            if same is not None:
                reusable.remove(same)
                installed_as[own_number] = same
        rest = [i for i in sorted(patch.tfc.caches) if i not in installed_as]
        installed_as.update(zip(rest, tfcmod.allocate(len(rest), taken)))
        patch.bind(tfcmod.packages_changed(patch.tfc, index), installed_as)
        missing = tfcmod.missing_textures(patch.tfc, index)
        if missing:
            shown = ", ".join(Path(m.replace("\\", "/")).name for m in missing[:3])
            more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
            warnings.append(f"{mod.id}: {len(missing)} texture(s) in its pack are not in "
                            f"any package of this game ({shown}{more}) - left alone")
    return warnings


def bind_tfc_patches_for_validation(mods, game_root: Path) -> None:
    """Enough binding to know which packages each TFC Installer mod rebuilds.

    Validation has no backups folder in hand, so this uses the texture index only
    if it has already been built and leaves cache numbers alone - it is asking
    what will change, not deciding it.
    """
    bound = [patch for mod in mods for patch in mod.patches
             if callable(getattr(patch, "bind", None))]
    if not bound:
        return
    from .upk import texture_index, tfcmod
    cooked = Path(game_root) / tfcmod.COOKED
    if not cooked.is_dir():
        return
    from .paths import AppPaths
    cache_file = texture_index_file(AppPaths.default().backups_dir)
    if not cache_file.is_file():
        return          # the first save builds it; until then patched packages only
    index = texture_index.build(cooked, cache_file)
    for patch in bound:
        patch.bind(tfcmod.packages_changed(patch.tfc, index), {})


def bind_tfc_patches_for_revert(mods, game_root: Path, backups_dir: Path) -> None:
    """Tie TFC Installer mods to the game folder so switching them off is complete.

    A freshly loaded mod does not know which packages hold its textures, or
    which Texture2D_N.tfc it went in as, and without both, switching it off would
    leave those behind. The packages come from the same index as installing. The
    cache is found by what is in it - the installed file is the pack's own,
    byte for byte - so the answer does not depend on load order or on which
    other mods happen to be enabled right now.
    """
    bound = [patch for mod in mods for patch in mod.patches
             if callable(getattr(patch, "bind", None))]
    if not bound:
        return
    from .upk import texture_index, tfcmod

    cooked = Path(game_root) / tfcmod.COOKED
    if not cooked.is_dir():
        return
    manifest = _load_manifest(backups_dir)
    ours = {}
    for target, entry in manifest.items():
        name = Path(target).name
        if entry.get("backup") is None and name.startswith("Texture2D_") and name.endswith(".tfc"):
            path = cooked / name
            if path.is_file():
                ours[name] = path
    index = texture_index.build(cooked, texture_index_file(backups_dir))
    for patch in bound:
        installed_as = {}
        for pack_number, source in sorted(patch.tfc.caches.items()):
            size = source.stat().st_size
            wanted = None
            for name, path in ours.items():
                if path.stat().st_size == size and sha256_file(path) == sha256_file(source):
                    wanted = int(name[len("Texture2D_"):-len(".tfc")])
                    break
            if wanted is not None:
                installed_as[pack_number] = wanted
        patch.bind(tfcmod.packages_changed(patch.tfc, index), installed_as)


def _stage_transforms(
    transforms: dict[str, tuple[object, str]],
    game_root: Path,
    store: Path,
    manifest: dict[str, dict],
    staging: Path,
    progress: Progress | None = None,
) -> tuple[list[tuple[str, Path, Path]], dict[str, Path]]:
    """Work out each transformed file's new contents, without writing to the game.

    The change is always computed from the *pristine* file - the ".original"
    kept the first time any mod claimed it, when there is one - so running twice
    is not running the change twice, and so a transform never stacks on top of
    another mod's version of the same file.

    "Pristine" is asked of the patch rather than assumed of the file on disk. An
    executable someone already modified by hand is not stock, and keeping it as
    the way back would mean switching the mod off *restored* the modification.
    A patch that can undo its own change hands back the stock bytes, and those
    are what gets kept - see ``Patch.to_pristine``. Returns the plan plus, for
    each target, the stock copy to keep.
    """
    plan: list[tuple[str, Path, Path]] = []
    stock_files: dict[str, Path] = {}
    progress = ensure_progress(progress)
    staging.mkdir(parents=True, exist_ok=True)
    ordered = sorted(transforms.items())
    for number, (target, (patch, mod_id)) in enumerate(ordered):
        progress.step(number, len(ordered), f"Rebuilding {Path(target).name}...")
        dest = _resolve_target(game_root, target)
        if not dest.is_file():
            raise OSError(f"{mod_id}: {target} is not in the game folder")
        kept = manifest.get(target, {}).get("backup")
        if kept and _from_an_older_build(store / kept, dest):
            # Kept before a game update. Transforming it would refuse (or worse,
            # hand back an executable for the old build) - the one on disk, put
            # back to stock, is the real original now.
            (store / kept).unlink(missing_ok=True)
            manifest.pop(target, None)
            kept = None
        pristine = store / kept if kept else dest
        if not pristine.is_file():
            raise OSError(f"{mod_id}: the saved copy of {target} is missing")
        found = pristine.read_bytes()
        if not kept and callable(getattr(patch, "transform_target", None)):
            # The first time a package is rebuilt, the copy on disk becomes the
            # way back - so it had better be the game's own. A package another
            # tool already changed (TFC Installer, most likely) is refused rather
            # than kept, or switching the mod off would put that tool's version
            # back instead of the game's.
            stock_sha1 = stock_hash_for(game_root, dest.name)
            if stock_sha1 is not None and hashlib.sha1(found).hexdigest() != stock_sha1:
                # Changed already - by TFC Installer, or by an earlier install of
                # the manager whose backups are gone. Either way, an untouched
                # copy proven by the game's own hash is as good as the original.
                rescued = find_stock_copy(game_root, dest.name, stock_sha1)
                if rescued is None:
                    raise OSError(
                        f"{mod_id}: {dest.name} has already been changed - by an earlier "
                        "install of this mod, or by TFC Installer - and no untouched copy "
                        "of it could be found to rebuild from. Steam's \"Verify integrity "
                        "of game files\" puts the game's own copy back. It also resets "
                        "masters.db and the game .exe, so switch the file check back off "
                        "and save your mod list again afterwards.")
                found = rescued.read_bytes()
                stock_files[target] = rescued
        try:
            stock = patch.to_pristine(found)
            per_file = getattr(patch, "transform_target", None)
            produced = per_file(target, stock) if callable(per_file) else patch.transform(stock)
        except Exception as exc:  # the patch says why; the runner just refuses
            raise OSError(f"{mod_id}: {target} was not changed - {exc}") from exc
        staged = staging / _flatten(target)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(produced)
        plan.append((target, staged, dest))
        if stock != found and target not in stock_files:
            # The file on disk already carried the change. Keep the stock form
            # instead, so unticking the mod has something real to go back to.
            stock_path = staging / (_flatten(target) + ".stock")
            stock_path.write_bytes(stock)
            stock_files[target] = stock_path
    return plan, stock_files


def _keep_original(
    target: str, source: Path, store: Path, manifest: dict[str, dict], taken_names: set
) -> None:
    """Record the permanent ".original" for one target. Taken once, ever.

    ``source`` is usually the file as found. For a target whose patch could tell
    that what is on disk already carries its change, it is the stock form worked
    out from the recording instead.
    """
    name = _free_backup_name(store, target, taken_names)
    shutil.copy2(source, store / name)
    if sha256_file(store / name) != sha256_file(source):
        raise OSError(f"backup of {target} did not verify")
    manifest[target] = {"backup": name}
    taken_names.add(name)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_checksums(game_root: Path, transforms=None) -> dict[str, str]:
    """Package name (lower case) -> the SHA-1 the game will accept for it.

    Read from the table inside the game's executable, with any hash a vetted
    recording is about to write in this same run laid over it. Empty when there
    is no executable to read - a loose test folder, or a layout this does not
    know - in which case nothing is checked, as before.
    """
    exe = Path(game_root) / vetted.GAME_EXE
    if not exe.is_file():
        return {}
    try:
        entries = exe_checksums.read_entries(exe.read_bytes())
    except Exception:
        return {}
    expected = {name.lower(): entry.sha1.lower() for name, entry in entries.items()}
    for patch, _mod_id in (transforms or {}).values():
        recipe = getattr(patch, "recipe", None)
        if recipe is not None:
            expected[recipe.package.lower()] = recipe.checksum_after.lower()
    return expected


TFC_BACKUP_GLOB = "TFCInstallerBackups*"


def find_stock_copy(game_root: Path, file_name: str, stock_sha1: str) -> Path | None:
    """An untouched copy of a game file, for when the one in the game is changed.

    TFC Installer keeps the file it replaced as ``<name>Backup`` under
    TFCInstallerBackups in the game folder. Whatever is found there is only
    trusted if its SHA-1 is the one the game's executable lists for that file,
    so a backup from an older game build, or of an already-modded file, is
    passed over rather than used.
    """
    wanted = (file_name + "Backup").lower()
    for folder in sorted(Path(game_root).glob(TFC_BACKUP_GLOB)):
        if not folder.is_dir():
            continue
        for candidate in folder.rglob("*"):
            if candidate.name.lower() != wanted or not candidate.is_file():
                continue
            try:
                if _sha1_file(candidate) == stock_sha1.lower():
                    return candidate
            except OSError:
                continue
    return None


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tfc_installer_caches(game_root: Path) -> set[int]:
    """Texture2D_N.tfc numbers TFC Installer says it put into the game."""
    out = set()
    for folder in Path(game_root).glob(TFC_BACKUP_GLOB):
        if not folder.is_dir():
            continue
        for marker in folder.rglob("Texture2D_*.tfcInstalled"):
            suffix = marker.name[len("Texture2D_"):-len(".tfcInstalled")]
            if suffix.isdigit():
                out.add(int(suffix))
    return out


def _identical_cache(pack_file: Path, cooked: Path, candidates: list[int]) -> int | None:
    """A Texture2D_N.tfc already in the game with exactly this pack's bytes."""
    size = pack_file.stat().st_size
    wanted = None
    for number in candidates:
        existing = cooked / f"Texture2D_{number}.tfc"
        try:
            if existing.stat().st_size != size:
                continue
            wanted = wanted or sha256_file(pack_file)
            if sha256_file(existing) == wanted:
                return number
        except OSError:
            continue
    return None


def stock_hash_for(game_root: Path, file_name: str) -> str | None:
    """The SHA-1 the game shipped this file with, from its executable's list.

    Found even when the file check has been switched off: that only changes the
    last letter of each name - ``Cafe_KIS.upk`` becomes ``cafe_kis.upx`` - and
    leaves the hash beside it exactly as it was. None when the game does not
    list the file, or the executable cannot be read.
    """
    exe = Path(game_root) / vetted.GAME_EXE
    if not exe.is_file():
        return None
    try:
        entries = exe_checksums.read_entries(exe.read_bytes())
    except Exception:
        return None
    wanted = file_name.lower()
    entry = entries.get(wanted)
    if entry is not None:
        return entry.sha1
    for name, entry in entries.items():
        if len(name) == len(wanted) and name[:-1] == wanted[:-1] and name[-1] in "xy":
            return entry.sha1
    return None


def _is_game_exe(target: str) -> bool:
    return _normalise(target).lower() == vetted.GAME_EXE.lower()


def _code_fingerprint(path: Path) -> str:
    try:
        return exe_checksums.code_fingerprint(Path(path).read_bytes())
    except Exception:
        return ""


def _from_an_older_build(kept: Path, current: Path) -> bool:
    """A kept executable whose code is not the code of the one in the game.

    Only a game update changes the code: every change this manager makes to the
    executable is twenty bytes of data, checked to leave the code alone.
    """
    if not kept.is_file() or not current.is_file():
        return False
    kept_code, current_code = _code_fingerprint(kept), _code_fingerprint(current)
    return bool(kept_code and current_code and kept_code != current_code)


def stock_executable(raw: bytes) -> bytes:
    """``raw`` with every vetted change recorded for its build taken back out."""
    try:
        code = exe_checksums.code_fingerprint(raw)
    except Exception:
        return raw
    for recipe in vetted.known_exe_recipes().values():
        if recipe.code_fingerprint != code:
            continue
        try:
            raw = exe_checksums.restore_entry(
                raw, recipe.package, recipe.checksum_before, recipe.checksum_after
            )
        except Exception:
            continue
    return raw


def keep_stock_executable(game_root: Path, backups_dir: Path) -> Path | None:
    """Make sure the permanent copy of the stock executable exists.

    Taken once, ever, like every other ".original", and only from a file whose
    check is still on - so an executable that has already been switched off can
    never quietly become this machine's idea of stock, which is the trap the
    standalone tool fell into.

    Switching back on does not need this copy: it reads what each name was off
    the name itself. This is insurance, not the way back.
    """
    exe = game_root / vetted.GAME_EXE
    if not exe.is_file():
        return None
    manifest = _load_manifest(backups_dir)
    store = _store_dir(backups_dir)
    kept = manifest.get(vetted.GAME_EXE, {}).get("backup")
    if kept and (store / kept).is_file():
        return store / kept
    if exe_check_off.switched_off_names(exe.read_bytes()):
        return None  # not stock, so not worth keeping as if it were
    store.mkdir(parents=True, exist_ok=True)
    name = _free_backup_name(store, vetted.GAME_EXE, set())
    shutil.copy2(exe, store / name)
    if sha256_file(store / name) != sha256_file(exe):
        (store / name).unlink(missing_ok=True)
        raise OSError("backup of the game executable did not verify")
    manifest[vetted.GAME_EXE] = {"backup": name}
    _save_manifest(backups_dir, manifest)
    return store / name


def write_executable(game_root: Path, raw: bytes) -> None:
    """Put new bytes in the executable's place, atomically."""
    dest = game_root / vetted.GAME_EXE
    handle, tmp_name = tempfile.mkstemp(
        prefix="." + dest.name + ".lid-", suffix=".tmp", dir=dest.parent
    )
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def stock_checksums(game_root: Path) -> dict[str, str]:
    """What the game's *unmodified* executable expects for each package."""
    exe = Path(game_root) / vetted.GAME_EXE
    if not exe.is_file():
        return {}
    try:
        entries = exe_checksums.read_entries(stock_executable(exe.read_bytes()))
    except Exception:
        return {}
    return {name.lower(): entry.sha1.lower() for name, entry in entries.items()}


def _refresh_stale_copies(wanted, game_root, store, manifest, log) -> bool:
    """Replace kept copies of checked packages that belong to an older build.

    The copy kept as a file's way back is taken the first time a mod claims it.
    After a game update that copy may be the old build's file, and putting it
    back would stop the game. When the file on disk right now is exactly what
    the stock executable expects, it is the real original: keep that instead.
    """
    stock = None
    changed = False
    for target in wanted:
        entry = manifest.get(target)
        if not entry or not entry.get("backup"):
            continue
        backup = store / entry["backup"]
        if not backup.is_file():
            continue
        if stock is None:
            stock = stock_checksums(game_root)
            if not stock:
                return False
        want = stock.get(Path(target).name.lower())
        if want is None or _sha1(backup) == want:
            continue
        try:
            dest = _resolve_target(game_root, target)
        except OSError:
            continue
        if dest.is_file() and _sha1(dest) == want:
            _verified_copy(dest, backup)
            changed = True
            if log:
                log.info(
                    f"the kept copy of {target} was from an older game build; "
                    "replaced with this build's"
                )
    return changed


def _leave_out_unmatched(wanted, transforms, game_root, store, manifest, log):
    """Take out of ``wanted`` every file the game would refuse to load.

    Returns (what is still wanted, [(target, mod id)] left out, [(target, mod
    id)] left out whose game copy is wrong and could not be put right).

    A left-out file the manager put there on an earlier run - before a game
    update changed what the executable expects - is put back to the kept copy
    when that copy is the right one. When the game's own copy is already the
    right one (Steam replaced it), the kept copy is simply forgotten: it
    describes an older build, and putting it back later would break the game.
    """
    expected = expected_checksums(game_root, transforms)
    if not expected:
        return wanted, [], []
    kept_wanted = {}
    released: list[tuple[str, str]] = []
    stuck: list[tuple[str, str]] = []
    changed = False
    # Kept copies of files no enabled mod claims any more - a mod whose new
    # version dropped them - go through the same check, so a file the game now
    # refuses does not stay behind with an out-of-date copy as its way back.
    unclaimed = [
        target
        for target in list(manifest)
        if target not in wanted and target not in (transforms or {})
        and Path(target).name.lower() in expected
    ]
    candidates = [(t, src, m) for t, (src, m) in wanted.items()]
    candidates += [(target, None, None) for target in unclaimed]
    for target, source, mod_id in candidates:
        want = expected.get(Path(target).name.lower())
        if mod_id is not None:
            if want is None or not source.is_file() or _sha1(source) == want:
                kept_wanted[target] = (source, mod_id)
                continue
            released.append((target, mod_id))
        try:
            dest = _resolve_target(game_root, target)
        except OSError:
            continue
        entry = manifest.get(target)
        backup = store / entry["backup"] if entry and entry.get("backup") else None
        current = _sha1(dest) if dest.is_file() else None
        try:
            if current != want and backup is not None and backup.is_file() and _sha1(backup) == want:
                _verified_copy(backup, dest)
                current = want
                if log:
                    log.info(f"game file put back to the game's own copy: {target}")
        except OSError as exc:
            if log:
                log.warn(f"could not put back {target} ({exc})")
        if current == want or current is None:
            if entry is not None:
                if backup is not None:
                    backup.unlink(missing_ok=True)
                manifest.pop(target, None)
                changed = True
        else:
            stuck.append((target, mod_id or "A mod that no longer installs it"))
    try:
        changed = _refresh_stale_copies(kept_wanted, game_root, store, manifest, log) or changed
    except OSError as exc:
        if log:
            log.warn(f"could not refresh an out-of-date kept copy ({exc})")
    if changed:
        _save_manifest(store.parent, manifest)
    return kept_wanted, released, stuck


def _group_by_mod(pairs) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for target, mod_id in pairs:
        grouped.setdefault(mod_id, []).append(Path(target).name)
    return grouped


def _files_phrase(names: list[str]) -> tuple[str, bool]:
    """("UI_A.upk", False) or ("33 game files, UI_A.upk among them", True)."""
    names = sorted(names)
    if len(names) == 1:
        return names[0], False
    return f"{len(names)} game files, {names[0]} among them", True


def apply_asset_patches(
    mods_in_load_order,
    game_root: Path,
    backups_dir: Path,
    *,
    log=None,
    progress: Progress | None = None,
) -> AssetApplyReport:
    """Copy every enabled mod's game files into place, last-wins by load order."""
    progress = ensure_progress(progress)
    report = AssetApplyReport()
    game_root = Path(game_root)
    if not game_root.is_dir():
        report.ok = False
        report.error = f"game folder not found: {game_root}"
        return report

    try:
        for warning in _bind_tfc_patches(mods_in_load_order, game_root, backups_dir, log):
            report.warnings.append(warning)
            if log:
                log.warn(warning)
        wanted = _wanted_files(mods_in_load_order)
        transforms = _wanted_transforms(mods_in_load_order)
        ini = _wanted_ini(mods_in_load_order)
    except OSError as exc:
        report.ok = False
        report.error = str(exc)
        if log:
            log.error(report.error)
        return report
    if not wanted and not transforms and not ini:
        return report

    # Checked before a single byte is copied: a refused file fails the whole
    # run, so a pack is never left half-installed around the file it wanted.
    refused = [
        (target, mod_id, reason)
        for target, mod_id in sorted(
            [(t, m) for t, (_source, m) in wanted.items()]
            + [(t, c[-1][3]) for t, c in ini.items()]
        )
        if (reason := forbidden_target_reason(target))
    ]
    if refused:
        target, mod_id, reason = refused[0]
        more = f" (and {len(refused) - 1} more)" if len(refused) > 1 else ""
        report.ok = False
        report.error = f"{mod_id}: refused - {reason}{more}"
        if log:
            log.error(report.error)
        return report

    store = _store_dir(backups_dir)
    store.mkdir(parents=True, exist_ok=True)
    rollback_dir = store / ROLLBACK_DIRNAME
    _empty_dir(rollback_dir)
    manifest = _load_manifest(backups_dir)
    taken_names = {entry["backup"] for entry in manifest.values() if entry.get("backup")}

    # Config files are worked out here rather than copied from the mod folder,
    # then join the ordinary copy plan - so everything below treats them as any
    # other game file, including the backup and the all-or-nothing rollback.
    if ini:
        try:
            wanted.update(_stage_ini(ini, game_root, store, manifest, rollback_dir / "ini"))
        except (OSError, UnicodeError) as exc:
            report.ok = False
            report.error = f"a game config file could not be worked out: {exc}"
            if log:
                log.error(report.error)
            return report

    # Files the game checks against the table inside its executable. One that
    # does not match is not loaded - the game stops with an error naming it -
    # so it is left out, and the game's own copy is kept or put back.
    wanted, released, stuck = _leave_out_unmatched(
        wanted, transforms, game_root, store, manifest, log
    )
    for mod_id, names in _group_by_mod(stuck).items():
        files, several = _files_phrase(names)
        warning = (
            f"{mod_id}: {files} in the game folder {'are' if several else 'is'} not what "
            "this game build expects, and there is no correct copy to put back. The game "
            "will stop with an error when it loads "
            f"{'them' if several else 'it'}. Use Steam's Verify integrity of game files, "
            "then save again."
        )
        report.warnings.append(warning)
        if log:
            log.warn(warning)
    for mod_id, names in _group_by_mod(released).items():
        files, several = _files_phrase(names)
        warning = (
            f"{mod_id}: {files} {'were' if several else 'was'} made for a different game "
            f"build than this one, so {'they were' if several else 'it was'} left out and "
            "the game's own copy kept. Installing "
            f"{'them' if several else 'it'} would stop the game with an error. A release "
            "of the mod made for this build fixes that."
        )
        report.warnings.append(warning)
        if log:
            log.warn(warning)

    # Work out the changes first so a no-op run touches nothing.
    plan: list[tuple[str, Path, Path]] = []  # target, source, dest
    try:
        # Transforms first: they read the pristine file, so they must be worked
        # out before anything this run writes.
        # Rebuilding packages is the slow part; copying files is quick.
        staged_plan, stock_files = _stage_transforms(
            transforms, game_root, store, manifest, rollback_dir / "staged",
            progress.part(0.0, 0.8 if transforms else 0.0, "Rebuilding game packages..."),
        )
        copying = progress.part(0.8 if transforms else 0.0, 1.0, "Copying game files...")
        for target, source, dest in staged_plan:
            if dest.is_file() and sha256_file(dest) == sha256_file(source):
                # Already exactly what this mod wants: installed by hand, or
                # left in place while the manager's records went missing. There
                # is nothing to copy - but the way back still has to be written
                # down, or unticking the mod later would have nothing to put
                # back and the change would be stuck in the game for good.
                if target not in manifest and target in stock_files:
                    _keep_original(target, stock_files[target], store, manifest, taken_names)
                    _save_manifest(backups_dir, manifest)
                report.skipped += 1
                continue
            plan.append((target, source, dest))
        for target, (source, _mod_id) in sorted(wanted.items()):
            if not source.is_file():
                report.ok = False
                report.error = f"missing bundled file for {target}"
                return report
            dest = _resolve_target(game_root, target)
            current = sha256_file(dest) if dest.is_file() else None
            if current == sha256_file(source):
                report.skipped += 1
                continue
            plan.append((target, source, dest))
    except OSError as exc:
        # Why, not just that. Without this the manager reports only that game
        # files could not be applied, which tells nobody anything.
        report.ok = False
        report.error = str(exc)
        if log:
            log.error(report.error)
        return report

    if not plan:
        return report

    done: list[tuple[str, Path, bool]] = []  # target, dest, existed_before_this_run
    try:
        for number, (target, source, dest) in enumerate(plan):
            copying.step(number, len(plan), f"Copying {Path(target).name}...")
            existed = dest.is_file()
            # Rollback copy of this run's starting state.
            if existed:
                stash = rollback_dir / _flatten(target)
                stash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, stash)

            # Permanent ".original" - taken once per target, ever.
            if target not in manifest:
                if existed:
                    # Normally the file as found. For a target whose patch could
                    # tell that what is on disk already carries the change, it is
                    # the stock form worked out from the recording - otherwise
                    # the "way back" would put the modification back.
                    _keep_original(
                        target, stock_files.get(target, dest), store, manifest, taken_names
                    )
                else:
                    manifest[target] = {"backup": None}
                _save_manifest(backups_dir, manifest)

            _verified_copy(source, dest)
            done.append((target, dest, existed))
            report.copied.append(target)
            if log:
                log.info(f"game file {'replaced' if existed else 'added'}: {target}")
    except OSError as exc:
        # All-or-nothing: undo this run's changes.
        for target, dest, existed in reversed(done):
            try:
                if existed:
                    shutil.copy2(rollback_dir / _flatten(target), dest)
                elif dest.is_file():
                    dest.unlink()
                if manifest.get(target, {}).get("backup") is None and not existed:
                    manifest.pop(target, None)
            except OSError:
                if log:
                    log.warn(f"could not roll back {target} - check it by hand")
        _save_manifest(backups_dir, manifest)
        _empty_dir(rollback_dir)
        report.ok = False
        report.copied = []
        report.error = f"game file copy failed and was rolled back: {exc}"
        if log:
            log.error(report.error)
        return report

    _empty_dir(rollback_dir)
    if log:
        log.info(report.summary_line())
    return report


# --------------------------------------------------------------------------
# Revert / restore
# --------------------------------------------------------------------------


def restore_targets(
    targets,
    game_root: Path,
    backups_dir: Path,
    *,
    log=None,
) -> AssetApplyReport:
    """Put specific game files back to their saved ".original" (or delete the
    ones a mod created), and forget them, so a later apply re-backs-up cleanly.
    """
    report = AssetApplyReport()
    game_root = Path(game_root)
    manifest = _load_manifest(backups_dir)
    store = _store_dir(backups_dir)
    changed = False
    stock = None
    # The executable first: which other files are right depends on it.
    ordered = sorted({_normalise(t) for t in targets}, key=lambda t: (not _is_game_exe(t), t))
    for target in ordered:
        entry = manifest.get(target)
        if entry is None:
            continue
        try:
            dest = _resolve_target(game_root, target)
        except OSError as exc:
            report.warnings.append(str(exc))
            continue
        backup_name = entry.get("backup")
        try:
            if backup_name and _is_game_exe(target) and _from_an_older_build(store / backup_name, dest):
                # The game was updated since this copy was kept. Putting it back
                # would install the old build's executable over the new game, so
                # this build's executable is taken back to stock instead.
                current = dest.read_bytes()
                cleaned = stock_executable(current)
                if cleaned != current:
                    staged = store / ROLLBACK_DIRNAME / "stock.exe"
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(cleaned)
                    _verified_copy(staged, dest)
                    staged.unlink(missing_ok=True)
                (store / backup_name).unlink(missing_ok=True)
                report.restored.append(target)
                manifest.pop(target, None)
                changed = True
                if log:
                    log.info(
                        f"{target}: the kept copy was from an older game build, so this "
                        "build's executable was put back to stock instead"
                    )
                continue
            if backup_name and not _is_game_exe(target):
                if stock is None:
                    stock = stock_checksums(game_root)
                want = stock.get(Path(target).name.lower())
                if want is not None and _sha1(store / backup_name) != want:
                    # Also from an older build: never put back.
                    (store / backup_name).unlink(missing_ok=True)
                    manifest.pop(target, None)
                    changed = True
                    if dest.is_file() and _sha1(dest) == want:
                        report.restored.append(target)
                    else:
                        warning = (
                            f"{target}: the kept copy was from an older game build and "
                            "was not put back. Use Steam's Verify integrity of game files "
                            "to get this build's file."
                        )
                        report.warnings.append(warning)
                        if log:
                            log.warn(warning)
                    continue
            if backup_name:
                _verified_copy(store / backup_name, dest)
                # The game file is now a verified copy of the backup, so the
                # backup is redundant. Keeping it is what used to leak: the next
                # apply takes a fresh one under a new name, and every on/off
                # cycle left another full copy of a file that can be 180 MB.
                # Taking it fresh each time is also what keeps it right after a
                # game update changes the vanilla file in between.
                (store / backup_name).unlink(missing_ok=True)
            elif dest.is_file():
                dest.unlink()
            report.restored.append(target)
        except OSError as exc:
            report.ok = False
            report.warnings.append(f"{target}: {exc}")
            continue
        manifest.pop(target, None)
        changed = True
        if log:
            log.info(f"game file restored: {target}")
    if changed:
        _save_manifest(backups_dir, manifest)
    return report


def restore_all_originals(game_root: Path, backups_dir: Path, *, log=None) -> AssetApplyReport:
    """Tools > Restore game files: walk the manifest and undo every tracked file."""
    manifest = _load_manifest(backups_dir)
    return restore_targets(list(manifest), game_root, backups_dir, log=log)


def list_asset_backups(backups_dir: Path) -> list[AssetBackupEntry]:
    manifest = _load_manifest(backups_dir)
    store = _store_dir(backups_dir)
    out: list[AssetBackupEntry] = []
    for target in sorted(manifest):
        name = manifest[target].get("backup")
        out.append(AssetBackupEntry(target, (store / name) if name else None))
    return out


def _empty_dir(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
