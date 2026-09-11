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

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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


def apply_asset_patches(
    mods_in_load_order,
    game_root: Path,
    backups_dir: Path,
    *,
    log=None,
) -> AssetApplyReport:
    """Copy every enabled mod's game files into place, last-wins by load order."""
    report = AssetApplyReport()
    game_root = Path(game_root)
    if not game_root.is_dir():
        report.ok = False
        report.error = f"game folder not found: {game_root}"
        return report

    try:
        wanted = _wanted_files(mods_in_load_order)
    except OSError as exc:
        report.ok = False
        report.error = str(exc)
        return report
    if not wanted:
        return report

    # Checked before a single byte is copied: a refused file fails the whole
    # run, so a pack is never left half-installed around the file it wanted.
    refused = [
        (target, mod_id, reason)
        for target, (_source, mod_id) in sorted(wanted.items())
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

    # Work out the changes first so a no-op run touches nothing.
    plan: list[tuple[str, Path, Path]] = []  # target, source, dest
    try:
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
        report.ok = False
        report.error = str(exc)
        return report

    if not plan:
        return report

    done: list[tuple[str, Path, bool]] = []  # target, dest, existed_before_this_run
    try:
        for target, source, dest in plan:
            existed = dest.is_file()
            # Rollback copy of this run's starting state.
            if existed:
                stash = rollback_dir / _flatten(target)
                stash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, stash)

            # Permanent ".original" - taken once per target, ever.
            if target not in manifest:
                if existed:
                    name = _free_backup_name(store, target, taken_names)
                    shutil.copy2(dest, store / name)
                    if sha256_file(store / name) != sha256_file(dest):
                        raise OSError(f"backup of {target} did not verify")
                    manifest[target] = {"backup": name}
                    taken_names.add(name)
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
    for target in sorted({_normalise(t) for t in targets}):
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
