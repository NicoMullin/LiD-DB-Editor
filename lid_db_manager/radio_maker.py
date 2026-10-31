"""Make a radio station from the player's own music.

Each song becomes a package of its own, SN_BGM_Radio_Music_NNNN_SF.upk in
CookedPCConsole, the way the game ships its radio songs. A package holds six
objects: the redirector and referencer every song has, its two package
objects, a SoundCue that plays it, and a SoundNodeWave that is the song:

    SoundNodeWave
      properties    Duration, NumChannels, SampleRate, RawPCMDataSize, and one
                    SoundMarker whose SamplesToEndOfFile is the length in samples
      bulk data     RawData (empty), then CompressedPCData - the .ogg file
                    itself, byte for byte - then the other platforms' (empty)

Nothing of the game's ships with this: the package is built from the player's
own copy of the first stock song, SN_BGM_Radio_Music_0001_SF.upk, with its
four-digit number swapped for the new one (same length, so the name table and
header keep their size), its sound swapped for the player's, and a new GUID.
That is how S3er0i9ng's Tower Static songs were made, and they play.

Each song is then two rows: master_radio_music (id, title, artist) and
master_radio_channel (channel number, the song's id, the name the radio shows).
The game gives every one of its songs a channel of its own, named after the
artist - the table has room for five songs a channel, but no stock channel uses
more than one, and the shuffle stations only ever take a channel's first. So a
song is a channel here too. Shuffle group 1 (rand_grp) puts it in OBS Needle
Sounds, the station that plays that group in a random order. The result is an
ordinary mod folder - asset_file for the packages, raw_sql_file for the rows -
so it switches on, off and reverts like any other mod.

Only Ogg Vorbis at 44.1 kHz stereo is taken, which is what every song in the
game is. Other files are refused with a message rather than guessed at.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from .upk import apply as A
from .upk import package as P
from .upk.texture2d import _read_bulk, read_tags

TEMPLATE_NAME = "SN_BGM_Radio_Music_0001_SF.upk"
TEMPLATE_DIR = Path("BrgGame") / "CookedPCConsole"
SONG_PREFIX = "SN_BGM_Radio_Music_"
RATE = 44100
CHANNELS = 2
SHUFFLE_GROUP = 1              # rand_grp of the songs OBS Needle Sounds shuffles
# The game plays songs numbered below 1000 only: proven in game 2026-10-28, where
# 0951 played and 1000-1002 were listed but stayed silent. Stock songs are
# 0001-0144 and the community packs use 09xx, so made songs take 0200-0899.
FIRST_SONG_NUMBER = 200
LAST_SONG_NUMBER = 899
FIRST_CHANNEL = 600            # stock channels end at 500; the packs use 501-518
LAST_CHANNEL = 999
MOST_NAME = 24                 # characters of a channel name


class RadioMakerError(ValueError):
    """Something the player can fix: the message says what."""


# ---------------------------------------------------------------------------
# the song file
# ---------------------------------------------------------------------------

@dataclass
class Song:
    path: Path
    data: bytes
    channels: int
    rate: int
    samples: int
    title: str
    artist: str

    @property
    def seconds(self) -> float:
        return self.samples / self.rate


def _ogg_packets(data: bytes) -> tuple[list[bytes], int, set[int]]:
    """The first logical stream's first packets, its last granule, and every
    stream serial seen."""
    at, packets, partial, granule, serials, first = 0, [], b"", 0, set(), None
    while at < len(data):
        if data[at:at + 4] != b"OggS":
            raise RadioMakerError("the file is not a whole Ogg file - it breaks off part way")
        if at + 27 > len(data):
            raise RadioMakerError("the file is cut short")
        page_granule, serial = struct.unpack_from("<qI", data, at + 6)
        count = data[at + 26]
        lacing = data[at + 27:at + 27 + count]
        body = at + 27 + count
        serials.add(serial)
        if first is None:
            first = serial
        if serial == first:
            if page_granule >= 0:
                granule = max(granule, page_granule)
            pos = body
            for size in lacing:
                partial += data[pos:pos + size]
                pos += size
                if size < 255:
                    if len(packets) < 3:
                        packets.append(partial)
                    partial = b""
        at = body + sum(lacing)
    return packets, granule, serials


def _comments(packet: bytes, start: int = 7) -> dict[str, str]:
    """Vorbis comments (KEY=value), from ``start``: 7 in a Vorbis comment
    packet, 8 in an OpusTags one, 0 in a FLAC block."""
    found: dict[str, str] = {}
    try:
        (vendor,) = struct.unpack_from("<I", packet, start)
        at = start + 4 + vendor
        (count,) = struct.unpack_from("<I", packet, at)
        at += 4
        for _ in range(count):
            (size,) = struct.unpack_from("<I", packet, at)
            text = packet[at + 4:at + 4 + size].decode("utf-8", errors="replace")
            at += 4 + size
            key, _, value = text.partition("=")
            found.setdefault(key.upper(), value.strip())
    except struct.error:
        pass
    return found


# "[AUDIO]", "(Official Music Video)", "(Lyrics)": what downloaded songs carry in their names
_NAME_NOISE = re.compile(r"\s*[\[(][^\])]*\b(official|audio|video|lyrics?|visuali[sz]er|hd|hq|4k|mv)\b"
                         r"[^\])]*[\])]", re.IGNORECASE)


def guess_from_name(stem: str) -> tuple[str, str]:
    """(artist, title) from a file name like "Artist - Title [Official Video]",
    for a song with no tags; with no " - " the whole name is the title."""
    name = " ".join(_NAME_NOISE.sub("", stem).split()) or stem
    artist, dash, title = name.partition(" - ")
    if dash and artist.strip() and title.strip():
        return artist.strip(), title.strip()
    return "", name


def read_song(path: Path | str) -> Song:
    """An .ogg file, checked: Ogg Vorbis, 44.1 kHz, stereo."""
    path = Path(path)
    data = path.read_bytes()
    if not data.startswith(b"OggS"):
        raise RadioMakerError(f"{path.name} is not an Ogg file. Export it as Ogg Vorbis "
                              "(.ogg) first, for example with Audacity.")
    packets, granule, serials = _ogg_packets(data)
    if packets and packets[0].startswith(b"OpusHead"):
        raise RadioMakerError(f"{path.name} is Ogg Opus. The game plays Ogg Vorbis only - "
                              "export it again as Ogg Vorbis.")
    if not packets or not packets[0].startswith(b"\x01vorbis"):
        raise RadioMakerError(f"{path.name} is not Ogg Vorbis sound.")
    if len(serials) != 1:
        raise RadioMakerError(f"{path.name} holds more than one stream. Export it again as "
                              "a single song.")
    channels = packets[0][11]
    (rate,) = struct.unpack_from("<I", packets[0], 12)
    if (channels, rate) != (CHANNELS, RATE):
        raise RadioMakerError(
            f"{path.name} is {rate:,} Hz with {channels} channel(s). The game's radio songs "
            f"are all {RATE:,} Hz stereo - export it again at 44100 Hz, stereo.")
    if granule <= 0:
        raise RadioMakerError(f"{path.name} has no length - is it empty?")
    tags = _comments(packets[1]) if len(packets) > 1 else {}
    artist, title = guess_from_name(path.stem)
    return Song(path, data, channels, rate, granule,
                title=tags.get("TITLE") or title, artist=tags.get("ARTIST") or (artist if not tags.get("TITLE") else ""))


# ---------------------------------------------------------------------------
# the package
# ---------------------------------------------------------------------------

def _float_tag(tags, name: str, value: float) -> None:
    tag = next(t for t in tags if t.name == name)
    tag.value = struct.pack("<f", value)


def _int_tag(tags, name: str, value: int) -> None:
    tag = next(t for t in tags if t.name == name)
    tag.value = struct.pack("<i", value)


def _number_of(pkg: P.Package) -> str:
    for i in range(len(pkg.exports)):
        m = re.fullmatch(rf"{SONG_PREFIX}(\d{{4}})_SF", pkg.full_path(i + 1))
        if m:
            return m.group(1)
    raise RadioMakerError("the template is not a radio song package")


def build_song_package(template: bytes, number: int, song: Song, label: str | None = None) -> bytes:
    """A song package for ``song``, numbered ``number``, made from a stock one.

    ``label`` replaces the SoundMarker's text ("Tempo: 90.0" in the template);
    the game does nothing with it, and it is kept unless asked.
    """
    if not 1 <= number <= 999:
        raise RadioMakerError(f"song number {number} is not one the game plays (0001-0999)")
    summary, flat = P.decompressed(template)
    pkg = P.Package(summary, flat, P.read_names(flat, summary), P.read_imports(flat, summary),
                    P.read_exports(flat, summary))
    classes = [pkg.object_name(e.class_index) if e.class_index else "Class" for e in pkg.exports]
    if len(pkg.exports) != 6 or classes[4:] != ["SoundCue", "SoundNodeWave"]:
        raise RadioMakerError("the template is not laid out like the game's radio songs")
    old, new = _number_of(pkg), f"{number:04d}"
    out = bytearray(flat)

    # the number, in every name that carries it - the same length, so in place
    names_end = summary.import_offset
    region = bytes(out[summary.name_offset:names_end])
    carrying = sum(1 for n in pkg.names if old in n.text)
    if region.count(old.encode()) != carrying or carrying == 0:
        raise RadioMakerError("the template's names are not the ones expected")
    out[summary.name_offset:names_end] = region.replace(old.encode(), new.encode())

    # a GUID of its own, as every package has; the game's songs carry it twice,
    # in the header and on the _SF package object
    guid_at = summary.offsets_at["thumbnail_table_offset"] + 4
    guid = uuid.uuid4().bytes
    out[guid_at:guid_at + 16] = guid
    for i, entry in enumerate(pkg.exports):
        if pkg.full_path(i + 1) == f"{SONG_PREFIX}{old}_SF":
            at = entry.serial_offset_at + 4 + 4 + 4 + 4 * len(entry.net_objects)
            out[at:at + 16] = guid

    # the SoundCue: its length
    cue = pkg.exports[4]
    cue_data = pkg.export_data(4)
    tags, end, terminator = read_tags(pkg, cue_data, 4)
    _float_tag(tags, "Duration", song.seconds)
    new_cue = cue_data[:4] + b"".join(t.serialize() for t in tags) + terminator + cue_data[end:]
    if len(new_cue) != cue.serial_size:
        raise RadioMakerError("the template's SoundCue is not the one expected")
    out[cue.serial_offset:cue.serial_offset + cue.serial_size] = new_cue

    # the SoundNodeWave: its length, format, marker and sound
    wave = pkg.exports[5]
    if wave.serial_offset + wave.serial_size != len(flat):
        raise RadioMakerError("the template's sound is not the last thing in it")
    wave_data = pkg.export_data(5)
    tags, end, terminator = read_tags(pkg, wave_data, 4)
    _float_tag(tags, "Duration", song.seconds)
    _int_tag(tags, "NumChannels", song.channels)
    _int_tag(tags, "SampleRate", song.rate)
    _int_tag(tags, "RawPCMDataSize", song.samples * song.channels * 2)
    markers = next(t for t in tags if t.name == "SoundMarkers")
    (count,) = struct.unpack_from("<i", markers.value, 0)
    marker_tags, marker_end, marker_terminator = read_tags(pkg, markers.value, 4)
    if count != 1 or marker_end != len(markers.value):
        raise RadioMakerError("the template's sound marker is not the one expected")
    _int_tag(marker_tags, "SamplesToEndOfFile", song.samples)
    if label is not None:
        text = next(t for t in marker_tags if t.name == "Label")
        text.value = struct.pack("<i", len(label) + 1) + label.encode("latin-1") + b"\0"
    markers.value = struct.pack("<i", 1) + b"".join(t.serialize() for t in marker_tags) + marker_terminator

    blocks, at = [], end
    while at < len(wave_data):
        bulk, at = _read_bulk(wave_data, at)
        if not bulk.is_local:
            raise RadioMakerError("the template's sound is stored outside the package")
        blocks.append(bulk)
    if len(blocks) < 2 or blocks[0].size_on_disk or not blocks[1].size_on_disk:
        raise RadioMakerError("the template's sound data is not laid out as expected")

    body = bytearray(wave_data[:4] + b"".join(t.serialize() for t in tags) + terminator)
    for i, bulk in enumerate(blocks):
        inline = song.data if i == 1 else b""
        here = wave.serial_offset + len(body) + 24
        body += struct.pack("<Iiqq", bulk.flags, len(inline), len(inline), here) + inline
    del out[wave.serial_offset:]
    out += body
    A._place_entry(out, wave, wave.serial_offset, len(body))
    return bytes(out)


# ---------------------------------------------------------------------------
# the station mod
# ---------------------------------------------------------------------------

def template_path(game_root: Path | str) -> Path:
    return Path(game_root) / TEMPLATE_DIR / TEMPLATE_NAME


def _taken(mods_dir: Path, db_path: Path | None) -> tuple[set[int], set[int]]:
    """Song numbers and channels already used, by the game or by any mod."""
    songs: set[int] = set()
    channels: set[int] = set()
    if db_path and Path(db_path).is_file():
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            for (music_id,) in con.execute("SELECT music_id FROM master_radio_music"):
                m = re.fullmatch(rf"{SONG_PREFIX}(\d+)", music_id or "")
                if m:
                    songs.add(int(m.group(1)))
            for (channel,) in con.execute("SELECT channel FROM master_radio_channel"):
                if str(channel).isdigit():
                    channels.add(int(channel))
        finally:
            con.close()
    if mods_dir.is_dir():
        for path in mods_dir.rglob("*"):
            if path.suffix.lower() in (".upk",):
                m = re.fullmatch(rf"{SONG_PREFIX}(\d+)_SF", path.stem, re.IGNORECASE)
                if m:
                    songs.add(int(m.group(1)))
            elif path.suffix.lower() in (".sql", ".json"):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                songs.update(int(n) for n in re.findall(rf"{SONG_PREFIX}(\d{{4}})", text))
                channels.update(int(n) for n in re.findall(r"channel\s*=\s*'(\d+)'", text))
    return songs, channels


def _free(taken: set[int], first: int, last: int, count: int, what: str) -> list[int]:
    free = [n for n in range(first, last + 1) if n not in taken][:count]
    if len(free) < count:
        raise RadioMakerError(f"there are not {count} free {what} left")
    return free


def _q(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _folder_for(mods_dir: Path, name: str) -> Path:
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", f"Radio - {name}").strip(" .") or "Radio station"
    folder, n = mods_dir / base, 2
    while folder.exists():
        folder, n = mods_dir / f"{base} ({n})", n + 1
    return folder


def channel_name(song: Song, name: str) -> str:
    """What the radio shows for a song's channel: its artist, as the game's own
    channels do, or the name the player gave when it has none."""
    return (song.artist or name)[:MOST_NAME]


def make_station(mods_dir: Path | str, name: str, songs: list[Song], template: Path | str,
                 db_path: Path | str | None = None, shuffle: bool = False) -> Path:
    """Write the mod folder - one channel per song - and return it.

    ``shuffle`` also puts the songs in OBS Needle Sounds (channel 000), which
    plays every channel of shuffle group 1 in a random order.
    """
    mods_dir = Path(mods_dir)
    name = " ".join(name.split())
    if not name:
        raise RadioMakerError("give your music a name")
    if len(name) > MOST_NAME:
        raise RadioMakerError(f"the name is longer than {MOST_NAME} characters")
    if not songs:
        raise RadioMakerError("add at least one song")
    template = Path(template)
    if not template.is_file():
        raise RadioMakerError(f"{template.name} is not in the game's CookedPCConsole folder, "
                              "and it is what the songs are built from")
    template_bytes = template.read_bytes()

    taken_songs, taken_channels = _taken(mods_dir, Path(db_path) if db_path else None)
    numbers = _free(taken_songs, FIRST_SONG_NUMBER, LAST_SONG_NUMBER, len(songs), "song numbers")
    channels = _free(taken_channels, FIRST_CHANNEL, LAST_CHANNEL, len(songs), "radio channels")
    group = SHUFFLE_GROUP if shuffle else 0

    folder = _folder_for(mods_dir, name)
    staging = folder.with_name(folder.name + ".making")
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "assets").mkdir(parents=True)
    try:
        sql = [
            f"-- {name}: your own music on the radio, made with LID DB Mod Manager. Each song",
            "-- is a channel of its own, as the game's songs are. The two radio tables have no",
            "-- primary key, so every row is only added when it is not there yet - saving",
            "-- again never adds a second copy.",
            "",
        ]
        listing = []
        for number, channel, song in zip(numbers, channels, songs):
            music_id = f"{SONG_PREFIX}{number:04d}"
            (staging / "assets" / f"{music_id}_SF.upk").write_bytes(
                build_song_package(template_bytes, number, song))
            sql.append(
                f"INSERT INTO master_radio_music (music_id, title, artist) SELECT {_q(music_id)}, "
                f"{_q(song.title)}, {_q(song.artist)} WHERE NOT EXISTS "
                f"(SELECT 1 FROM master_radio_music WHERE music_id = {_q(music_id)});")
            shown = channel_name(song, name)
            sql.append(
                "INSERT INTO master_radio_channel (channel, music_id1, music_id2, music_id3, "
                "music_id4, music_id5, channel_name, rand_grp, platform) SELECT "
                + ", ".join(_q(v) for v in [f"{channel:03d}", music_id, "", "", "", "", shown])
                + f", {group}, 0 WHERE NOT EXISTS (SELECT 1 FROM master_radio_channel WHERE "
                  f"channel = {_q(f'{channel:03d}')});")
            listing.append((channel, shown, song))
        (staging / "radio.sql").write_text("\n".join(sql) + "\n", encoding="utf-8")

        count = len(songs)
        plural = "s" if count > 1 else ""
        where = (f"channel {channels[0]}" if count == 1
                 else f"channels {channels[0]}-{channels[-1]}" if channels[-1] - channels[0] == count - 1
                 else "channels " + ", ".join(str(c) for c in channels))
        mod = {
            "id": folder.name,
            "name": folder.name,
            "description": f"{count} song{plural} of your own on the radio, on {where}"
                           + (", and in the OBS Needle Sounds shuffle." if shuffle else "."),
            "version": "1.0.0",
            "author": "You",
            "category": "Audio",
            "tags": ["radio", "music", "game-files"],
            "requires": [],
            "conflicts_with": [],
            "patches": [
                {"type": "raw_sql_file", "path": "radio.sql",
                 "description": f"{count} song{plural} and {'their' if count > 1 else 'its'} "
                                f"channel{plural}"},
                {"type": "asset_file", "source": "assets", "target": "BrgGame/CookedPCConsole",
                 "description": f"{count} music package{plural}"},
            ],
        }
        (staging / "mod.json").write_text(json.dumps(mod, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
        readme = [f"# {name}", "", "Your own music on the in-game radio, one song per channel.", "",
                  "| Channel | Shows as | Song | Length |", "|---|---|---|---|"]
        for channel, shown, song in listing:
            minutes, seconds = divmod(round(song.seconds), 60)
            readme.append(f"| {channel} | {shown} | {song.title} | {minutes}:{seconds:02d} |")
        readme += ["", "Tick it and click **Save Mod List**, then pick the channel on the radio in "
                   "the game." + (" The songs also come up in OBS Needle Sounds (channel 000), "
                                  "which shuffles them in with the game's own." if shuffle else "")
                   + " Untick it and save to take it out again.", ""]
        (staging / "readme.md").write_text("\n".join(readme), encoding="utf-8")
        staging.rename(folder)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return folder
