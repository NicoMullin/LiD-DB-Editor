"""Any music file as a radio song, converted by the player's own VLC.

The radio plays Ogg Vorbis at 44.1 kHz stereo only (see radio_maker.py). A
file that is anything else - MP3, FLAC, WAV, M4A, a YouTube Opus, a 48 kHz or
mono .ogg - is run through VLC, which many players already have, and which
nothing here ships: the manager only looks for it where it installs itself.
VLC is driven from its command line, the same as Media > Convert / Save:

    vlc -I dummy <file> --sout=#transcode{acodec=vorb,...}:std{mux=ogg,dst=...}

Two things about VLC shape the rest. Its settings string cannot hold every
file name (a ' , or } breaks it), so it writes to a plain name in a folder of
its own, given as the working folder, and the song keeps the name of the file
it came from. And it drops the title and artist tags, so those are read here
from the original: ID3 (MP3), Vorbis comments (FLAC, Ogg, Opus), MP4 atoms
(M4A) and RIFF INFO (WAV). Downloaded songs often have none, only a name like
"Artist - Title [AUDIO]", which is then read instead. The converted song is checked like any other .ogg,
held in memory, and its temporary file deleted.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from .radio_maker import RadioMakerError, Song, _comments, _ogg_packets, guess_from_name, read_song

VLC_PAGE = "https://www.videolan.org/vlc/"
SOUT = ("#transcode{vcodec=none,scodec=none,acodec=vorb,ab=192,channels=2,samplerate=44100}"
        ":std{access=file,mux=ogg,dst=song.ogg}")
MOST_SECONDS = 900          # per song; VLC converts a song in a few seconds
MUSIC_FILES = "*.ogg *.mp3 *.flac *.wav *.m4a *.aac *.opus *.webm *.wma *.mp4 *.aiff"


# ---------------------------------------------------------------------------
# finding VLC
# ---------------------------------------------------------------------------

def _from_registry() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for key in (r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                exe, _ = winreg.QueryValueEx(k, "")
                found.append(Path(exe))
                folder, _ = winreg.QueryValueEx(k, "InstallDir")
                found.append(Path(folder) / "vlc.exe")
        except OSError:
            continue
    return found


def find_vlc() -> Path | None:
    """The player's VLC, or None."""
    if sys.platform == "win32":
        places = _from_registry() + [
            Path(os.environ.get(var, default)) / "VideoLAN" / "VLC" / "vlc.exe"
            for var, default in (("ProgramFiles", r"C:\Program Files"),
                                 ("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
        return next((p for p in places if p.is_file()), None)
    # cvlc is VLC without its window, on Linux
    found = shutil.which("cvlc") or shutil.which("vlc")
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# the original file's tags
# ---------------------------------------------------------------------------

def _text(data: bytes, encoding: int) -> str:
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(encoding, "latin-1")
    return data.decode(codec, errors="replace").split("\x00")[0].strip()


def _id3v2(data: bytes) -> dict[str, str]:
    version, flags = data[3], data[5]
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    end, at = min(10 + size, len(data)), 10
    if flags & 0x40 and version >= 3:                       # an extended header
        (skip,) = struct.unpack_from(">I", data, at)
        at += skip if version == 3 else ((skip >> 24) << 21 | (skip >> 16 & 0x7F) << 14
                                         | (skip >> 8 & 0x7F) << 7 | skip & 0x7F)
    wanted = {"TIT2": "TITLE", "TPE1": "ARTIST", "TT2": "TITLE", "TP1": "ARTIST"}
    found: dict[str, str] = {}
    while at + (6 if version == 2 else 10) <= end:
        if version == 2:
            frame, size, head = data[at:at + 3].decode("latin-1"), int.from_bytes(data[at + 3:at + 6], "big"), 6
        else:
            frame, head = data[at:at + 4].decode("latin-1"), 10
            raw = data[at + 4:at + 8]
            size = ((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) if version == 4 \
                else int.from_bytes(raw, "big")
        if not frame.strip("\x00") or size <= 0:
            break
        body = data[at + head:at + head + size]
        if frame in wanted and body:
            found.setdefault(wanted[frame], _text(body[1:], body[0]))
        at += head + size
    return found


def _id3v1(data: bytes) -> dict[str, str]:
    tag = data[-128:]
    return {"TITLE": _text(tag[3:33], 0), "ARTIST": _text(tag[33:63], 0)}


def _flac(data: bytes) -> dict[str, str]:
    at = 4
    while at + 4 <= len(data):
        kind, size = data[at] & 0x7F, int.from_bytes(data[at + 1:at + 4], "big")
        if kind == 4:
            return _comments(data[at + 4:at + 4 + size], 0)
        if data[at] & 0x80:
            break
        at += 4 + size
    return {}


def _ogg(data: bytes) -> dict[str, str]:
    packets, _, _ = _ogg_packets(data)
    if len(packets) > 1 and packets[1].startswith(b"\x03vorbis"):
        return _comments(packets[1], 7)
    if len(packets) > 1 and packets[1].startswith(b"OpusTags"):
        return _comments(packets[1], 8)
    return {}


def _atoms(data: bytes, at: int, end: int):
    while at + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, at)
        head = 8
        if size == 1:
            (size,) = struct.unpack_from(">Q", data, at + 8)
            head = 16
        elif size == 0:
            size = end - at
        if size < head:
            return
        yield kind, at + head, min(at + size, end)
        at += size


def _mp4(data: bytes) -> dict[str, str]:
    def inside(at, end, *path):
        for kind, start, stop in _atoms(data, at, end):
            if kind == path[0]:
                if kind == b"meta":
                    start += 4                                  # version and flags
                return (start, stop) if len(path) == 1 else inside(start, stop, *path[1:])
        return None

    ilst = inside(0, len(data), b"moov", b"udta", b"meta", b"ilst")
    if ilst is None:
        return {}
    found: dict[str, str] = {}
    for kind, start, stop in _atoms(data, *ilst):
        key = {b"\xa9nam": "TITLE", b"\xa9ART": "ARTIST"}.get(kind)
        for inner, s, e in _atoms(data, start, stop):
            if key and inner == b"data":
                found.setdefault(key, data[s + 8:e].decode("utf-8", errors="replace").strip())
    return found


def _wav(data: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    at = 12
    while at + 8 <= len(data):
        kind, size = data[at:at + 4], struct.unpack_from("<I", data, at + 4)[0]
        body = data[at + 8:at + 8 + size]
        if kind == b"LIST" and body[:4] == b"INFO":
            inner = 4
            while inner + 8 <= len(body):
                key, length = body[inner:inner + 4], struct.unpack_from("<I", body, inner + 4)[0]
                name = {b"INAM": "TITLE", b"IART": "ARTIST"}.get(key)
                if name:
                    found.setdefault(name, _text(body[inner + 8:inner + 8 + length], 3))
                inner += 8 + length + (length & 1)
        elif kind.lower() == b"id3 " and body.startswith(b"ID3"):
            for key, value in _id3v2(body).items():
                found.setdefault(key, value)
        at += 8 + size + (size & 1)
    return found


def read_tags(path: Path | str) -> dict[str, str]:
    """TITLE and ARTIST from a music file's own tags, where it has them."""
    try:
        data = Path(path).read_bytes()
        if data.startswith(b"ID3"):
            found = _id3v2(data)
        elif data.startswith(b"fLaC"):
            found = _flac(data)
        elif data.startswith(b"OggS"):
            found = _ogg(data)
        elif data[4:8] == b"ftyp":
            found = _mp4(data)
        elif data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            found = _wav(data)
        else:
            found = {}
        if not found and len(data) >= 128 and data[-128:-125] == b"TAG":
            found = _id3v1(data)
    except (OSError, struct.error, IndexError, RadioMakerError):
        return {}
    return {key: value for key, value in found.items() if value}


# ---------------------------------------------------------------------------
# converting
# ---------------------------------------------------------------------------

def convert(path: Path | str, vlc: Path) -> Song:
    """``path`` as a radio song, by way of VLC. The Song keeps the original's
    path and its title and artist."""
    path = Path(path)
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    with tempfile.TemporaryDirectory(prefix="lid-song-") as work:
        command = [str(vlc), "-I", "dummy", "--dummy-quiet", "--no-repeat", "--no-loop",
                   "--no-sout-video", "--no-sout-spu", str(path), f"--sout={SOUT}", "vlc://quit"]
        try:
            subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                           stderr=subprocess.DEVNULL, timeout=MOST_SECONDS, check=False, **hidden)
        except subprocess.TimeoutExpired:
            raise RadioMakerError(f"VLC took too long converting {path.name}.") from None
        except OSError as exc:
            raise RadioMakerError(f"VLC could not be started: {exc}") from None
        made = Path(work) / "song.ogg"
        if not made.is_file() or made.stat().st_size == 0:
            raise RadioMakerError(f"VLC could not convert {path.name} - can VLC play it?")
        song = read_song(made)
    tags = read_tags(path)
    artist, title = guess_from_name(path.stem)
    song.path = path
    song.title = tags.get("TITLE") or title
    song.artist = tags.get("ARTIST") or (artist if not tags.get("TITLE") else "")
    return song


def add_song(path: Path | str, vlc: Path | None) -> Song:
    """A file as a radio song: as it is when the game can play it, otherwise
    converted - or refused, saying how to fix it."""
    try:
        return read_song(path)
    except RadioMakerError as refused:
        if vlc is None:
            raise RadioMakerError(f"{refused} Or install VLC (free, {VLC_PAGE}) and the manager "
                                  "converts it for you.") from None
    return convert(path, vlc)
