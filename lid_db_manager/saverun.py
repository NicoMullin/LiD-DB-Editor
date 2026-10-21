"""The run a fighter is in: ending it, or going back to its last boss floor.

A save quit in the middle of a run keeps where the fighter is - soul.pause (the
way it was left: HEAVEN_PAUSE, or *_CRASH when the game closed without pausing),
soul.stgid/flrid/areaid/unitid and the position (pause_x/y/z, pause_yaw) - and
the run's floors under root.floor: rlg.user (the floors walked, the last the
current one), rlg.archive (each floor's layout), pop (spawns), trbox, item, dust,
mboss, bo, ffm, vm, zako; soul.hvntrinfo holds the boss floors' rewards ahead.
Starting the game puts the fighter back there.

Both tools are oberlinx's, confirmed in game:
- End the run does what the game does when a run ends: the run's position and
  floor data are cleared, things the run left on floors, in chests, on enemies
  and Jackals (owners FLOOR, TRBOX, ZAKO, ZOMBIE, MBOSS, JACKAL_*) go, the Kill
  Coins carried go to the Bank up to its limit for its level and the Bloodnium
  to the account (up to BLOODNIUM_POINT_MAX). The Death Bag stays. It also lets a
  save that crashes every time it loads mid-run load in the Waiting Room.
- Going back to the last boss floor is for a run that crashed on a normal
  Heaven floor (*_CRASH, on a floor with no boss): the fighter is put back on the
  last boss floor walked, paused at the arena's arrival point (known for regular
  Heaven's boss map and NEO map A), its boss beaten and its chests gone; the
  floors after it are dropped and played again. The Death Bag, Kill Coins and
  Bloodnium stay. (oberlinx also goes back past a NEO map B boss floor, rolling
  that floor's rewards; that is not offered here - end the run instead.)
Ported with the help of oberlinx's LET IT DIE save editor (used with permission).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .saveedit import Limits, SaveDocument

RUN_OWNERS = re.compile(r"^(FLOOR|TRBOX|ZAKO|ZOMBIE|MBOSS|JACKAL_.*)$")
ARRIVAL = {                     # where the game puts a fighter on a boss arena (from real paused saves)
    "HVN_AREA_017": (23.341, 8529.418, -902.85, 23846),
    "HVN_AREA_001": (60.8716, 8421.1299, -902.85, -4048),
}


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class HeavenFloors:
    """Heaven's floors (master_floor, stgid S_HVN): floor number and whether a boss is on it."""
    floors: dict = field(default_factory=dict)      # flrid -> (no, areaid, mbsmax)

    @classmethod
    def read(cls, db_path: Path | str | None) -> "HeavenFloors":
        found = cls()
        if not db_path or not Path(db_path).is_file():
            return found
        try:
            con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return found
        try:
            for flrid, no, areaid, mbsmax in con.execute(
                    "SELECT id, no, areaid, mbsmax FROM master_floor WHERE stgid = 'S_HVN'"):
                found.floors[str(flrid)] = (_int(no), str(areaid or ""), _int(mbsmax))
        except sqlite3.Error:
            pass
        finally:
            con.close()
        return found

    def label(self, flrid: str) -> str:
        row = self.floors.get(flrid)
        return f"{row[0]}F" if row and row[0] else (flrid or "?")

    def boss(self, flrid: str) -> bool:
        row = self.floors.get(flrid)
        return bool(row and row[2] > 0)


def _fighter(save: SaveDocument) -> dict | None:
    chrs = save.get(("soul", "chr", "chrs"))
    roster = chrs if isinstance(chrs, list) else (chrs or {}).get(save.uid) if isinstance(chrs, dict) else None
    return next((c for c in roster or [] if isinstance(c, dict) and c.get("state") == "USE"), None)


def _instances(save: SaveDocument) -> list[list]:
    """The item and weapon/armor record lists (each a plain list or one per player)."""
    found = []
    for section, key in (("item", "items"), ("part", "pts")):
        rows = (save.doc.get(section) or {}).get(key) if isinstance(save.doc.get(section), dict) else None
        if isinstance(rows, list):
            found.append(rows)
        elif isinstance(rows, dict) and isinstance(rows.get(save.uid), list):
            found.append(rows[save.uid])
    return found


@dataclass
class Run:
    fighter: dict | None
    name: str
    pause: str
    flrid: str
    money: int                  # Kill Coins carried
    bloodnium: int              # Bloodnium carried
    cap: int                    # what the Bank holds at its level
    bank: int
    banked: int                 # how much of ``money`` fits
    floors: int                 # floors in the run's data
    made: int                   # things the run left on floors, in chests, on enemies

    @property
    def lost(self) -> int:
        return self.money - self.banked

    @property
    def crashed(self) -> bool:
        return self.pause.endswith("_CRASH")


def run(save: SaveDocument, limits: Limits) -> Run | None:
    """The run the save is in, or None when it is in the Waiting Room."""
    soul = save.doc["soul"]
    if not soul.get("pause"):
        return None
    fighter = _fighter(save)
    money = _int((fighter or {}).get("money"))
    cap = limits.cap(limits.bank, _int(soul.get("safe_level"), 1))
    bank = _int(soul.get("free_money"))
    rlg = (save.doc.get("floor") or {}).get("rlg") if isinstance(save.doc.get("floor"), dict) else None
    floors = len(rlg.get("user")) if isinstance(rlg, dict) and isinstance(rlg.get("user"), list) else 0
    made = sum(1 for rows in _instances(save) for r in rows if isinstance(r, dict) and RUN_OWNERS.match(str(r.get("owner") or "")))
    return Run(fighter, str((fighter or {}).get("name") or "the fighter"), str(soul.get("pause")), str(soul.get("flrid") or ""),
               money, _int((fighter or {}).get("bloodnium")), cap, bank, max(0, min(money, cap - bank)), floors, made)


def end_run(save: SaveDocument, limits: Limits, floors: HeavenFloors | None = None) -> bool:
    """Send the fighter back to the Waiting Room the way the game does when a run ends."""
    info = run(save, limits)
    if info is None:
        return False
    soul, doc = save.doc["soul"], save.doc
    where = floors.label(info.flrid) if floors else info.flrid
    for key in ("pause", "stgid", "flrid", "areaid", "unitid", "relief_point"):
        if key in soul:
            soul[key] = ""
    for key in ("replica_money", "replica_spirit", "replica_bloodnium_point"):
        if key in soul:
            soul[key] = -1
    if "hvntrinfo" in soul:
        soul["hvntrinfo"] = {}
    if "force_shutdown_counts" in doc:
        doc["force_shutdown_counts"] = {}
    save.note(f"Run: {info.name}", f"in a run, on {where}", "back in the Waiting Room")
    if info.fighter is not None:
        if info.money:
            soul["free_money"] = info.bank + info.banked
            save.note("Run: Kill Coins carried to the Bank", f"{info.money:,} carried",
                      f"{info.banked:,} banked" + (f", {info.lost:,} over the Bank's limit lost" if info.lost else ""))
        if info.bloodnium:
            had = _int(soul.get("bloodnium_point"))
            soul["bloodnium_point"] = max(had, min(limits.bloodnium, had + info.bloodnium))
            save.note("Run: Bloodnium carried", f"{had:,}", f"{soul['bloodnium_point']:,}")
        info.fighter["money"] = 0
        info.fighter["bloodnium"] = 0
        if "pause" in info.fighter:
            info.fighter["pause"] = ""
        info.fighter["bloodnium_result"] = json.dumps(
            {"enemy_count": 0, "bloodnium": 0, "elapsed_time": 0, "max_floor_id": ""}, separators=(",", ":"))
    floor = doc.get("floor")
    if isinstance(floor, dict):
        floor["rlg"] = {"user": {}, "archive": {}}
        if isinstance(floor.get("pop"), dict):
            for key in list(floor["pop"]):
                floor["pop"][key] = {}
        for key in ("trbox", "item", "dust"):
            if key in floor:
                floor[key] = {}
        for key, inner in (("bo", ("bos", "flrbos")), ("mboss", ("mbss", "mbszmbs", "flrmbss")),
                           ("ffm", ("ffms", "flrffms", "kill_areas")), ("vm", ("vms", "flrvms"))):
            if key in floor:
                floor[key] = {k: {} for k in inner}
    zombies = (doc.get("zombie") or {}).get("flrzmbs") if isinstance(doc.get("zombie"), dict) else None
    if isinstance(zombies, dict):
        for key in list(zombies):
            zombies[key] = {}
    for rows in _instances(save):
        rows[:] = [r for r in rows if not (isinstance(r, dict) and RUN_OWNERS.match(str(r.get("owner") or "")))]
    if info.made:
        save.note("Run: things left on floors, in chests and on enemies", f"{info.made:,}", "cleared")
    return True


# -- going back to the last boss floor ---------------------------------------------------------

@dataclass
class Rewind:
    index: int                  # the boss floor's place in rlg.user
    flrid: str
    areaid: str
    arrival: tuple | None       # None: the editor does not know where the game puts a fighter there
    dropped: int                # floors after it


def rewind_target(save: SaveDocument, floors: HeavenFloors) -> Rewind | None:
    """Where a run that crashed on a normal Heaven floor can go back to, or None."""
    soul = save.doc["soul"]
    if not str(soul.get("pause") or "").endswith("_CRASH") or soul.get("stgid") != "S_HVN":
        return None
    if str(soul.get("flrid")) not in floors.floors or floors.boss(str(soul.get("flrid"))):
        return None
    rlg = (save.doc.get("floor") or {}).get("rlg") if isinstance(save.doc.get("floor"), dict) else None
    walked = rlg.get("user") if isinstance(rlg, dict) and isinstance(rlg.get("user"), list) else []
    for i in range(len(walked) - 2, -1, -1):
        step = walked[i] if isinstance(walked[i], dict) else {}
        if floors.boss(str(step.get("flrid"))):
            areaid = str(step.get("areaid") or "")
            return Rewind(i, str(step.get("flrid")), areaid, ARRIVAL.get(re.sub(r"_R0\d$", "", areaid)),
                          len(walked) - 1 - i)
    return None


def rewind(save: SaveDocument, floors: HeavenFloors) -> bool:
    """Put the fighter back on the run's last boss floor, its boss beaten, the floors after it dropped."""
    target = rewind_target(save, floors)
    if target is None or target.arrival is None:
        return False
    soul, doc = save.doc["soul"], save.doc
    floor = doc.setdefault("floor", {})
    rlg = floor.get("rlg")
    walked = rlg["user"]
    kept = {(s.get("flrid"), s.get("areaid")) for s in walked[:target.index + 1] if isinstance(s, dict)}
    kept_ids = {f for f, _ in kept}
    before = floors.label(str(soul.get("flrid")))
    rlg["user"] = walked[:target.index + 1]
    if isinstance(rlg.get("archive"), list):
        rlg["archive"] = [a for a in rlg["archive"] if isinstance(a, dict) and (a.get("flrid"), a.get("areaid")) in kept]
    if isinstance(floor.get("pop"), dict):
        for key, value in floor["pop"].items():
            if isinstance(value, list):
                floor["pop"][key] = [e for e in value if not isinstance(e, dict) or not e.get("flrid")
                                     or e.get("flrid") in kept_ids]
            elif isinstance(value, dict):
                for inner in list(value):
                    ident = str(inner).split("-")[0]
                    if ident.startswith("HVN_FLR_") and ident not in kept_ids:
                        del value[inner]
    # the boss floor as the game leaves it once its boss is beaten: nothing waiting, the dust markers kept
    floor["trbox"], floor["item"] = {}, {}
    floor["mboss"] = {"mbss": {}, "mbszmbs": {}, "flrmbss": {}}
    floor["zako"] = {"zks": {}, "flrzks": {}, "trbox": {}}
    floor["bo"] = {"bos": {}, "flrbos": {}}
    if "vm" in floor:
        floor["vm"] = {"vms": {}, "flrvms": {}}
    if "ffm" in floor:
        floor["ffm"] = {"ffms": {}, "flrffms": {}, "kill_areas": {}}
    floor["dust"] = [{"flrid": target.flrid, "areaid": target.areaid, "unit": unit, "pntid": "HVN_DST_TGT_00",
                      "type": "CHARGE"} for unit in ("BOSS_START", "BOSS_GOAL")]
    zombies = (doc.get("zombie") or {}).get("flrzmbs") if isinstance(doc.get("zombie"), dict) else None
    if isinstance(zombies, dict):
        for key in list(zombies):
            zombies[key] = {}
    x, y, z, yaw = target.arrival
    soul.update(flrid=target.flrid, areaid=target.areaid, unitid="D0", relief_point="D0", pause="HEAVEN_PAUSE",
                pause_x=float(x), pause_y=float(y), pause_z=float(z), pause_yaw=float(yaw))
    for key in ("pause_pitch", "pause_roll"):
        if key in soul:
            soul[key] = 0
    save.note("Run: where the fighter is", f"{before} (closed without pausing)",
              f"{floors.label(target.flrid)}, its boss beaten ({target.dropped} floor(s) after it to play again)")
    return True
