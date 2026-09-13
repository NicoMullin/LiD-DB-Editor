"""Record a vetted change to one hash in the game executable's table.

A maintenance tool. Players never run this, and adding a recording is meant to
be a deliberate act by whoever maintains this repository - that is the whole
point of the vetted list.

It reads a mod folder that supplies a replacement package plus a manifest
saying which package it replaces and what its hash becomes, checks all of that
against a real game executable, performs the edit in memory to prove it works
and is reversible, and only then writes the recording.

Nothing is written to the game. The executable is opened read-only.

Usage::

    py -3 tools/build_buttons_recipe.py \
        --mod "C:/.../lid cross/buttons" \
        --game "E:/SteamLibrary/steamapps/common/LET IT DIE"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECIPES = REPO / "lid_db_manager" / "recipes"
EXE_REL = Path("Binaries/Win64/BrgGame-Steam.exe")


def sha1_of(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha1").hexdigest()


def sha256_of(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod", type=Path, required=True,
                        help="the mod folder: a .upk plus its manifest.json")
    parser.add_argument("--game", type=Path, required=True,
                        help="your LET IT DIE folder (read-only)")
    parser.add_argument("--name", default="",
                        help="recording id; defaults to exe-<folder>-<version>")
    parser.add_argument("--author", default="S3er0i9ng",
                        help="who made the mod being recorded")
    parser.add_argument("--source", default="https://letitdiemods.pages.dev/",
                        help="where players should download it from")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))
    from lid_db_manager import exe_checksums as X  # noqa: PLC0415

    mod = args.mod.resolve()
    manifest_path = mod / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"{mod} has no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    package = manifest.get("file")
    if not package:
        raise SystemExit("manifest.json does not say which package it replaces ('file')")
    asset = mod / package
    if not asset.is_file():
        raise SystemExit(f"{mod} does not contain {package}")

    exe = args.game / EXE_REL
    if not exe.is_file():
        raise SystemExit(f"no game executable at {exe}")
    raw = exe.read_bytes()                     # read-only; never written back

    asset_sha1, asset_sha256 = sha1_of(asset), sha256_of(asset)
    before = str(manifest.get("original_sha1", "")).lower()
    after = str(manifest.get("patched_sha1", "")).lower()
    print(f"mod            : {manifest.get('version', '?')}  {package}")
    print(f"replacement    : {asset.stat().st_size:,} bytes, sha1 {asset_sha1}")

    if after != asset_sha1:
        raise SystemExit(
            f"the manifest says the replacement hashes to {after}, but the file "
            f"in the folder hashes to {asset_sha1}"
        )

    entry = X.read_entries(raw).get(package.lower())
    if entry is None:
        raise SystemExit(f"this game build carries no hash for {package}; nothing to record")
    if entry.at < 0:
        raise SystemExit(f"{package} appears more than once in the table; refusing to record")
    print(f"in the exe     : {entry.sha1}")
    if entry.sha1 != before:
        raise SystemExit(
            f"the manifest expects {before} but this build holds {entry.sha1}. "
            "Either the mod is for a different game build, or this executable is "
            "already modified. Nothing recorded."
        )

    fingerprint = X.code_fingerprint(raw)
    print(f"code (.text)   : {fingerprint}")

    # Prove it in memory, both ways, before writing anything.
    changed = X.apply_entry(raw, package, before, after)
    if X.code_fingerprint(changed) != fingerprint:
        raise SystemExit("the edit changed the executable's code; refusing to record")
    if X.read_entries(changed)[package.lower()].sha1 != asset_sha1:
        raise SystemExit("the edit did not produce the replacement's hash")
    if X.apply_entry(changed, package, after, before) != raw:
        raise SystemExit("the edit is not reversible; refusing to record")
    differing = sum(1 for a, b in zip(raw, changed) if a != b)
    print(f"verified       : {differing} byte(s) change, code identical, reversible")

    name = args.name or f"exe-{mod.name.lower()}-{manifest.get('version', '1.0')}"
    recipe = {
        "mod": manifest.get("name") or mod.name.replace("_", " ").title(),
        "version": str(manifest.get("version", "")),
        "target": "Binaries/Win64/BrgGame-Steam.exe",
        "package": package,
        "checksum_before": before,
        "checksum_after": after,
        "asset_sha256": asset_sha256,
        "code_fingerprint": fingerprint,
        "steam_build": str(manifest.get("steam_build", "")),
        "manifest_sha256": sha256_of(manifest_path),
        "recorded": date.today().isoformat(),
        "author": args.author,
        "source": args.source,
        "notes": "One hash in the executable's file table. No code is changed.",
    }
    RECIPES.mkdir(parents=True, exist_ok=True)
    out = RECIPES / f"{name}.json"
    out.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")

    # The mod.json that will be used, written out rather than assembled at
    # install time: what a player ends up running is then a file in this
    # repository that can be read, reviewed and diffed like any other.
    title = f"{recipe['mod']} v{recipe['version']}".strip()
    mod_json = {
        "id": name.removeprefix("exe-"),
        "name": title,
        "description": (
            f"Replaces {package} and updates the one hash the game keeps for it, "
            "so the game accepts the replacement. No game code is changed."
        ),
        "version": recipe["version"] or "1.0.0",
        "author": args.author,
        "requires": [],
        "conflicts_with": [],
        "patches": [
            {
                "type": "asset_file",
                "source": "assets",
                "target": "BrgGame/CookedPCConsole",
                "description": package,
            },
            {
                "type": "exe_checksum_entry",
                "recipe": name,
                "description": title,
            },
        ],
    }
    beside = RECIPES / f"{name}.mod.json"
    beside.write_text(json.dumps(mod_json, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {beside}")
    print("\nLook both over and commit them deliberately - that is what makes them vetted.")


if __name__ == "__main__":
    main()
