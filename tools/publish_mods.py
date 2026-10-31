"""Build the files the LiD-Mods repository publishes: one zip per mod and the index.

Every mod that needs no plugin lives in the LiD-Mods repository, and its
release "mods" holds one zip per mod plus mods-index.json, which lists them.
The manager's Get mods window reads the index and gets or updates one mod at a
time, so a fix to one mod goes out without a new manager.

    py -3 tools/publish_mods.py --mods mods --out out [--live mods-index.json]

``--live`` is the index the release has now (missing on the first run). Each
mod is compared with it by its version and by a hash of its files:

- a new mod, or a newer version: its zip is built and listed for upload
- the same version and the same files: kept as it is, nothing to upload
- the same version but different files: an error. The manager only re-applies
  a mod whose version changed, so a player would keep the old one. Bump the
  version in its mod.json.
- an older version than the live one: an error too.

Writes into ``--out``: the zips to upload, mods-index.json, upload.txt (the
zips, one a line), stale.txt (release files no longer listed, to delete) and
changes.md (one line per new or updated mod, for the Discord post).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

INDEX = "mods-index.json"
PACKAGE_FILE = "lid-package.json"
FORMAT = 1
ZIP_TIME = (1980, 1, 1, 0, 0, 0)            # fixed, so a rebuild gives the same bytes
SKIP = {"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"}


class PublishError(Exception):
    pass


def numbers(version: str) -> tuple[int, ...]:
    found = re.search(r"\d+(?:\.\d+)*", version or "")
    return tuple(int(part) for part in found.group().split(".")) if found else ()


def compare(a: str, b: str) -> int:
    x, y = numbers(a), numbers(b)
    width = max(len(x), len(y))
    x, y = x + (0,) * (width - len(x)), y + (0,) * (width - len(y))
    return (x > y) - (x < y)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-.")


def mod_files(folder: Path) -> list[Path]:
    return sorted((p for p in folder.rglob("*")
                   if p.is_file() and not SKIP & set(p.relative_to(folder).parts)
                   and p.suffix != ".pyc"),
                  key=lambda p: p.relative_to(folder).as_posix())


def content_hash(folder: Path) -> str:
    """Of the files' names and bytes - not of a zip, whose bytes depend on zlib."""
    digest = hashlib.sha256()
    for path in mod_files(folder):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def read_mod(folder: Path) -> dict:
    try:
        data = json.loads((folder / "mod.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PublishError(f"{folder.name}: no mod.json") from exc
    except ValueError as exc:
        raise PublishError(f"{folder.name}: mod.json does not read: {exc}") from exc
    version = str(data.get("version") or "")
    if not numbers(version):
        raise PublishError(f"{folder.name}: mod.json has no version")
    return data


def build_zip(folder: Path, data: dict, target: Path) -> None:
    package = {"format": 1, "name": str(data.get("name") or folder.name),
               "version": str(data["version"]), "mods": [folder.name]}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        def put(name: str, payload: bytes) -> None:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
        put(PACKAGE_FILE, (json.dumps(package, indent=2) + "\n").encode("utf-8"))
        for path in mod_files(folder):
            put(f"mods/{folder.name}/{path.relative_to(folder).as_posix()}", path.read_bytes())


def publish(mods: Path, out: Path, live: dict | None, today: str | None = None) -> dict:
    today = today or datetime.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    before = {entry["folder"]: entry for entry in (live or {}).get("mods", [])}
    entries, upload, changes, problems = [], [], [], []
    files_seen: dict[str, str] = {}
    for folder in sorted((p for p in mods.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if folder.name.startswith(("_", ".")) or folder.name in SKIP:
            continue                    # the templates stay with the manager
        try:
            data = read_mod(folder)
        except PublishError as exc:
            problems.append(str(exc))
            continue
        version = str(data["version"])
        content = content_hash(folder)
        file = f"{slug(folder.name)}-{slug(version)}.zip"
        if file in files_seen:
            problems.append(f"{folder.name} and {files_seen[file]} would both be {file}")
            continue
        files_seen[file] = folder.name
        old = before.get(folder.name)
        if old is not None:
            order = compare(version, str(old.get("version", "")))
            if order < 0:
                problems.append(f"{folder.name}: {version} is older than the published {old['version']}")
                continue
            if order == 0 and old.get("content") != content:
                problems.append(f"{folder.name}: its files changed but its version is still {version} - "
                                "bump the version in its mod.json, or players keep the old one")
                continue
            if order == 0:
                entries.append(dict(old, **_listing(folder, data)))
                continue
        target = out / file
        build_zip(folder, data, target)
        entry = {
            "folder": folder.name, **_listing(folder, data), "version": version,
            "file": file, "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "content": content, "updated": today,
        }
        entries.append(entry)
        upload.append(file)
        what = f"{old['version']} -> {version}" if old else f"{version} (new)"
        changes.append(f"- **{entry['name']}** {what}")
    if problems:
        raise PublishError("\n".join(problems))
    listed = {entry["file"] for entry in entries}
    stale = sorted({entry.get("file", "") for entry in before.values()} - listed - {""})
    index = {"format": FORMAT, "mods": entries}
    (out / INDEX).write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "upload.txt").write_text("".join(f"{name}\n" for name in upload), encoding="utf-8")
    (out / "stale.txt").write_text("".join(f"{name}\n" for name in stale), encoding="utf-8")
    (out / "changes.md").write_text("".join(f"{line}\n" for line in changes), encoding="utf-8")
    return {"index": index, "upload": upload, "stale": stale, "changes": changes}


def _listing(folder: Path, data: dict) -> dict:
    """What the Get mods window shows - refreshed even when the mod is not rebuilt."""
    games = data.get("game_version")
    return {
        "name": str(data.get("name") or folder.name),
        "author": str(data.get("author") or ""),
        "description": str(data.get("description") or ""),
        "category": str(data.get("category") or ""),
        "min_manager_version": str(data.get("min_manager_version") or ""),
        "game_version": [str(g) for g in games] if isinstance(games, list)
        else [str(games)] if games else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mods", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", type=Path, help=f"the release's {INDEX} now, if it has one")
    args = parser.parse_args(argv)
    live = None
    if args.live and args.live.is_file():
        live = json.loads(args.live.read_text(encoding="utf-8"))
    try:
        result = publish(args.mods, args.out, live)
    except PublishError as exc:
        print(f"Nothing was published:\n{exc}", file=sys.stderr)
        return 1
    print(f"{len(result['index']['mods'])} mod(s) listed, {len(result['upload'])} to upload, "
          f"{len(result['stale'])} old file(s) to delete")
    for line in result["changes"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
