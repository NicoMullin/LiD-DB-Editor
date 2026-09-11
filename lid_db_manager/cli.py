"""Command line front end.

The GUI is the main way in, but everything the GUI does is available here too -
handy for testing a mod, scripting, or running without PySide6 installed.

    python run.py list
    python run.py enable revive-cost-1kc body-prices-1kc
    python run.py validate
    python run.py apply
    python run.py revert revive-cost-1kc
    python run.py watch
    python run.py mods/revive-cost-1kc     # apply one folder, ad hoc
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from . import backup as backup_module
from .manager import MOD_APPLIED, MOD_FAILED, MOD_PENDING, Manager
from .mod_loader import load_mod_folder
from .paths import AppPaths
from .runner import apply_mods
from .session_log import SessionLog
from .watchdog import PollingWatcher

STATUS_MARK = {MOD_APPLIED: "[ok]", MOD_PENDING: "[..]", MOD_FAILED: "[!!]"}


def _manager() -> Manager:
    return Manager(echo_log=False)


def _require_db(manager: Manager) -> bool:
    if manager.db_path and manager.db_path.is_file():
        return True
    print(
        "No database selected. Run:  python run.py set-db <path to masters.db>",
        file=sys.stderr,
    )
    return False


def cmd_status(manager: Manager, args: argparse.Namespace) -> int:
    status = manager.db_status()
    print(f"{APP_NAME} {__version__}")
    print(f"  database : {manager.db_path or '(not set)'}")
    print(f"  status   : {status.label}")
    print(f"  last save: {manager.state.last_saved_at or '(never)'}")
    print(f"  mods     : {len(manager.mods)} installed, {len(manager.state.enabled_mods)} enabled")
    if manager.state.modpacks:
        print(f"  modpacks : {', '.join(sorted(manager.state.modpacks))}")
    deleted = manager.deleted_mods()
    if deleted:
        print(f"  deleted  : {', '.join(deleted)} (applied but no longer installed)")
    return 0


def cmd_list(manager: Manager, args: argparse.Namespace) -> int:
    if not manager.mods:
        print(f"No mods found in {manager.paths.mods_dir}")
    for mod in manager.mods:
        enabled = "x" if manager.state.is_enabled(mod.id) else " "
        mark = STATUS_MARK.get(manager.mod_status(mod.id), "    ")
        print(f"[{enabled}] {mark} {mod.id:<28} {mod.name}")
        print(f"           {mod.description}")
        print(f"           affects: {mod.affects_label()}")
    for failure in manager.scan.failures:
        print(f"[!] SKIPPED  {failure.folder}: {failure.reason}")
    return 0


def cmd_enable(manager: Manager, args: argparse.Namespace) -> int:
    installed = manager.installed_ids()
    exit_code = 0
    for mod_id in args.mod_ids:
        if mod_id not in installed:
            print(f"unknown mod: {mod_id}", file=sys.stderr)
            exit_code = 1
            continue
        manager.set_enabled(mod_id, args.enable)
        print(f"{'enabled' if args.enable else 'disabled'} {mod_id}")
    manager.state.save()
    print("Run 'apply' to write the changes to the database.")
    return exit_code


def _print_validation(report) -> None:
    print(report.summary_line())
    for result in report.results:
        for message in result.errors:
            print(f"  ERROR {result.mod_id}: {message}")
        for message in result.warnings:
            print(f"  warn  {result.mod_id}: {message}")
    for conflict in report.conflicts.conflicts:
        print(f"  warn  conflict: {conflict.message()}")
    for requirement in report.conflicts.missing_requirements:
        print(f"  warn  {requirement.message()}")
    for problem in report.conflicts.order_problems:
        print(f"  warn  {problem.message()}")


def cmd_validate(manager: Manager, args: argparse.Namespace) -> int:
    if not _require_db(manager):
        return 2
    report = manager.validate()
    _print_validation(report)
    return 0 if report.ok else 1


def cmd_apply(manager: Manager, args: argparse.Namespace) -> int:
    if not _require_db(manager):
        return 2
    if not manager.state.enabled_mods:
        print("No mods enabled. Use 'enable <mod-id>' first.")
        return 1
    report = manager.reapply_all() if args.no_backup else manager.save_mod_list()
    if report.validation is not None:
        _print_validation(report.validation)
    print(report.summary_line())
    for result in report.results:
        if result.ok:
            print(f"  {result.mod_id}: {result.rows_changed} row(s)")
    return 0 if report.ok else 1


def cmd_revert(manager: Manager, args: argparse.Namespace) -> int:
    if not _require_db(manager):
        return 2
    results = manager.revert(args.mod_ids)
    failed = False
    for result in results:
        if result.ok:
            print(f"reverted {result.mod_id} via {result.method}: {result.rows_restored} row(s)")
            for warning in result.warnings:
                print(f"  warn  {warning}")
        else:
            failed = True
            print(f"could not revert {result.mod_id}: {result.error}", file=sys.stderr)
    return 1 if failed else 0


def cmd_preview(manager: Manager, args: argparse.Namespace) -> int:
    if not _require_db(manager):
        return 2
    mods = [mod for mod in manager.mods if not args.mod_ids or mod.id in args.mod_ids]
    if not mods:
        print("no matching mods", file=sys.stderr)
        return 1
    for mod in mods:
        print(f"=== {mod.id} - {mod.name}")
        for preview in manager.previews([mod]).get(mod.id, []):
            print(f"  {preview.patch_summary}  ({preview.total_rows} row(s))")
            for row in preview.rows[: args.rows]:
                print(f"    {row.key}: {row.before}  ->  {row.after}")
            if preview.note:
                print(f"    {preview.note.splitlines()[0]}")
    return 0


def cmd_set_db(manager: Manager, args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 1
    first_time = not backup_module.original_backup_path(path).exists()
    manager.set_db_path(path)
    print(f"database set to {path}")
    if first_time:
        print()
        print("Point this at a CLEAN, unmodified masters.db.")
        print(
            f"  A copy has just been kept as {path.name}{backup_module.ORIGINAL_SUFFIX}, and it"
        )
        print("  is never overwritten - it is your permanent way back to vanilla.")
        print("  If the database had already been edited, so is that copy.")
        print("  Replace it now (delete it and verify the game files) rather than later.")
    return 0


def cmd_game_root(manager: Manager, args: argparse.Namespace) -> int:
    """Show, set or clear the game folder used by asset_file mods."""
    if args.clear:
        manager.set_game_root_override(None)
        print("game folder override cleared")
        return 0
    if args.path:
        path = Path(args.path).expanduser()
        if not (path / "BrgGame" / "CookedPCConsole").is_dir():
            print(f"not a game folder (no BrgGame/CookedPCConsole inside): {path}", file=sys.stderr)
            return 1
        manager.set_game_root_override(path)
        print(f"game folder set to {path}")
        return 0
    root = manager.asset_game_root
    source = "override" if manager.state.game_root_override else "derived from the database path"
    print(f"game folder: {root or '(not found - set one with: game-root <path>)'}")
    if root:
        print(f"  ({source})")
    return 0


def cmd_restore_game_files(manager: Manager, args: argparse.Namespace) -> int:
    entries = manager.asset_backups()
    if not entries:
        print("no game files have been changed by a mod")
        return 0
    try:
        report = manager.restore_all_asset_backups()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(report.summary_line())
    return 0 if report.ok else 1


def cmd_modpack(manager: Manager, args: argparse.Namespace) -> int:
    if args.action == "list":
        if not manager.state.modpacks:
            print("no modpacks saved")
        for name, mod_ids in sorted(manager.state.modpacks.items()):
            print(f"{name}: {', '.join(mod_ids) or '(empty)'}")
        return 0
    if not args.name:
        print("a modpack name is required", file=sys.stderr)
        return 1
    if args.action == "save":
        manager.save_modpack(args.name)
        print(f"saved modpack {args.name!r}")
        return 0
    if args.action == "load":
        try:
            mod_ids = manager.load_modpack(args.name)
        except KeyError:
            print(f"no such modpack: {args.name}", file=sys.stderr)
            return 1
        print(f"enabled: {', '.join(mod_ids) or '(none)'}")
        print("Run 'apply' to write the changes to the database.")
        return 0
    manager.delete_modpack(args.name)
    print(f"deleted modpack {args.name!r}")
    return 0


def cmd_backups(manager: Manager, args: argparse.Namespace) -> int:
    backups = manager.backups()
    if args.restore:
        if not _require_db(manager):
            return 2
        # Match by name first (that is what the listing prints, and the rolling
        # backup lives next to the DB rather than in backups/), then by path.
        chosen = next((entry for entry in backups if entry.name == args.restore), None)
        if chosen is not None:
            manager.restore_backup(chosen)
        else:
            candidate = Path(args.restore)
            if not candidate.is_file():
                candidate = manager.paths.backups_dir / args.restore
            if not candidate.is_file():
                print(f"no such backup: {args.restore}", file=sys.stderr)
                print("Run 'backups' with no arguments to see what there is.", file=sys.stderr)
                return 1
            manager.restore_backup(candidate)
        print(f"restored {args.restore}")
        return 0
    if not backups:
        print("no backups yet - they are taken on 'apply'")
        return 0
    for entry in backups:
        print(entry.label())
    return 0


def cmd_watch(manager: Manager, args: argparse.Namespace) -> int:
    if not _require_db(manager):
        return 2
    interval = args.interval or manager.state.settings.poll_seconds
    print(f"watching {manager.db_path} every {interval}s - Ctrl+C to stop")
    print(f"auto re-apply: {'on' if manager.state.settings.auto_reapply else 'off'}")

    def on_change(status) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {status.label}")
        if status.changed:
            report = manager.on_db_changed(status)
            if report is not None:
                print(f"  {report.summary_line()}")

    poller = PollingWatcher(manager.watcher, on_change, interval)
    poller.start()
    try:
        while poller.is_alive():
            poller.join(timeout=1.0)
    except KeyboardInterrupt:
        poller.stop()
        print("\nstopped")
    return 0


def cmd_apply_folder(args: argparse.Namespace) -> int:
    """Apply one mod folder directly, without touching the enabled list."""
    folder = Path(args.folder).expanduser().resolve()
    db_path = Path(args.db).expanduser() if args.db else None
    if db_path is None:
        manager = _manager()
        db_path = manager.db_path
    if db_path is None or not db_path.is_file():
        print("no database - pass --db <path to masters.db>", file=sys.stderr)
        return 2
    mod = load_mod_folder(folder)
    paths = AppPaths.default().ensure()
    log = SessionLog(paths.logs_dir, echo=True)
    report = apply_mods(db_path, [mod], snapshots_dir=paths.snapshots_dir, log=log)
    print(report.summary_line())
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description=f"{APP_NAME} (CLI)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="show the database and mod-list status")
    subparsers.add_parser("list", help="list installed mods")

    enable = subparsers.add_parser("enable", help="enable one or more mods")
    enable.add_argument("mod_ids", nargs="+")
    enable.set_defaults(enable=True)

    disable = subparsers.add_parser("disable", help="disable one or more mods")
    disable.add_argument("mod_ids", nargs="+")
    disable.set_defaults(enable=False)

    subparsers.add_parser("validate", help="validate the enabled mods without applying")

    apply_parser = subparsers.add_parser("apply", help="back up, validate and apply enabled mods")
    apply_parser.add_argument(
        "--no-backup", action="store_true", help="skip the backup (this is what Re-apply All does)"
    )

    revert = subparsers.add_parser("revert", help="undo mods from their snapshots")
    revert.add_argument("mod_ids", nargs="+")

    preview = subparsers.add_parser("preview", help="show what the mods would change")
    preview.add_argument("mod_ids", nargs="*")
    preview.add_argument("--rows", type=int, default=10, help="rows to show per patch")

    set_db = subparsers.add_parser("set-db", help="point the manager at masters.db")
    set_db.add_argument("path")

    game_root = subparsers.add_parser(
        "game-root", help="show/set the game folder for asset_file mods (holds BrgGame)"
    )
    game_root.add_argument("path", nargs="?", help="set the game folder to this path")
    game_root.add_argument("--clear", action="store_true", help="forget the override")

    subparsers.add_parser(
        "restore-game-files", help="put back every game file a mod has changed"
    )

    modpack = subparsers.add_parser("modpack", help="save, load, list or delete modpacks")
    modpack.add_argument("action", choices=["save", "load", "list", "delete"])
    modpack.add_argument("name", nargs="?")

    backups = subparsers.add_parser("backups", help="list or restore database backups")
    backups.add_argument("--restore", metavar="NAME", help="restore this backup over the DB")

    watch = subparsers.add_parser("watch", help="poll the DB and re-apply when it changes")
    watch.add_argument("--interval", type=int, default=0, help="seconds between polls")

    folder = subparsers.add_parser("apply-folder", help="apply a single mod folder, ad hoc")
    folder.add_argument("folder")
    folder.add_argument("--db", help="database to apply to (defaults to the saved one)")

    return parser


COMMANDS = {
    "status": cmd_status,
    "list": cmd_list,
    "enable": cmd_enable,
    "disable": cmd_enable,
    "validate": cmd_validate,
    "apply": cmd_apply,
    "revert": cmd_revert,
    "preview": cmd_preview,
    "set-db": cmd_set_db,
    "game-root": cmd_game_root,
    "restore-game-files": cmd_restore_game_files,
    "modpack": cmd_modpack,
    "backups": cmd_backups,
    "watch": cmd_watch,
}


def _make_console_unicode_safe() -> None:
    """Mod text is full of Japanese; a cp1252 console must not crash on it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _make_console_unicode_safe()
    argv = list(sys.argv[1:] if argv is None else argv)

    # `python run.py mods/revive-cost-1kc` - the shorthand from the spec.
    if argv and not argv[0].startswith("-") and argv[0] not in set(COMMANDS) | {"apply-folder"}:
        if Path(argv[0]).is_dir():
            argv = ["apply-folder"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "apply-folder":
        return cmd_apply_folder(args)
    return COMMANDS[args.command](_manager(), args)
