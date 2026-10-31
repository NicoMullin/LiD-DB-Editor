"""Music files that are not .ogg yet, made into radio songs by the player's VLC.

The tag readers run on small files built here, one per kind. The conversion
itself needs VLC, so it runs only where VLC is installed: a 48 kHz mono WAV,
in a folder and under a name with the characters VLC's settings cannot hold,
must come back as a 44.1 kHz stereo song that keeps its own name and tags.
"""

from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from lid_db_manager import radio_maker as R
from lid_db_manager import song_convert as C

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOWER_SONG = PROJECT_ROOT / "mods" / "Tower Static Radio" / "assets" / "SN_BGM_Radio_Music_0902_SF.upk"
VLC = C.find_vlc()


def id3v2(version: int, frames: dict[str, str]) -> bytes:
    body = b""
    for name, text in frames.items():
        data = b"\x03" + text.encode("utf-8") if version == 4 else b"\x01" + text.encode("utf-16")
        size = len(data)
        size_bytes = (bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
                      if version == 4 else struct.pack(">I", size))
        body += name.encode() + size_bytes + b"\x00\x00" + data
    size = len(body)
    head = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3" + bytes([version, 0, 0]) + head + body + b"\xff\xfb" + b"\x00" * 200


def vorbis_comments(**tags: str) -> bytes:
    vendor = b"test"
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(tags))
    for key, value in tags.items():
        entry = f"{key}={value}".encode()
        out += struct.pack("<I", len(entry)) + entry
    return out


def atom(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def write_wav(path: Path, rate: int, channels: int, seconds: float) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(rate * seconds)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 440 * 2 * math.pi / rate))) * channels
                               for i in range(frames)))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        run = tempfile.TemporaryDirectory()
        self.addCleanup(run.cleanup)
        self.root = Path(run.name)

    def file(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path


class TheTags(Base):
    def test_mp3_id3_versions(self) -> None:
        for version in (3, 4):
            path = self.file(f"v{version}.mp3", id3v2(version, {"TIT2": "Bloody Song", "TPE1": "Mr. Mañana"}))
            self.assertEqual(C.read_tags(path), {"TITLE": "Bloody Song", "ARTIST": "Mr. Mañana"}, version)

    def test_mp3_old_id3v1(self) -> None:
        tag = b"TAG" + b"Old One".ljust(30, b"\x00") + b"Old Band".ljust(30, b"\x00") + b"\x00" * 65
        path = self.file("old.mp3", b"\xff\xfb" + b"\x00" * 400 + tag)
        self.assertEqual(C.read_tags(path), {"TITLE": "Old One", "ARTIST": "Old Band"})

    def test_flac(self) -> None:
        info = bytes([0]) + (34).to_bytes(3, "big") + b"\x00" * 34
        comments = vorbis_comments(TITLE="Flac Song", ARTIST="Flac Band")
        last = bytes([0x80 | 4]) + len(comments).to_bytes(3, "big") + comments
        self.assertEqual(C.read_tags(self.file("a.flac", b"fLaC" + info + last)),
                         {"TITLE": "Flac Song", "ARTIST": "Flac Band"})

    def test_m4a(self) -> None:
        def item(kind: bytes, text: str) -> bytes:
            return atom(kind, atom(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + text.encode()))
        ilst = atom(b"ilst", item(b"\xa9nam", "Apple Song") + item(b"\xa9ART", "Apple Band"))
        moov = atom(b"moov", atom(b"udta", atom(b"meta", b"\x00\x00\x00\x00" + atom(b"hdlr", b"\x00" * 25) + ilst)))
        data = atom(b"ftyp", b"M4A \x00\x00\x00\x00") + atom(b"mdat", b"\x00" * 50) + moov
        self.assertEqual(C.read_tags(self.file("a.m4a", data)), {"TITLE": "Apple Song", "ARTIST": "Apple Band"})

    def test_wav_info(self) -> None:
        path = self.root / "a.wav"
        write_wav(path, 44100, 2, 0.1)
        info = b"INFO" + b"INAM" + struct.pack("<I", 9) + b"Wav Song\x00" + b"\x00" \
            + b"IART" + struct.pack("<I", 8) + b"Wav Band"
        data = bytearray(path.read_bytes() + b"LIST" + struct.pack("<I", len(info)) + info)
        struct.pack_into("<I", data, 4, len(data) - 8)
        path.write_bytes(bytes(data))
        self.assertEqual(C.read_tags(path), {"TITLE": "Wav Song", "ARTIST": "Wav Band"})

    def test_nothing_from_a_file_with_none(self) -> None:
        self.assertEqual(C.read_tags(self.file("junk.bin", b"\x00" * 300)), {})
        self.assertEqual(C.read_tags(self.root / "missing.mp3"), {})


class TheFileName(unittest.TestCase):
    def test_artist_and_title_from_a_downloaded_songs_name(self) -> None:
        cases = {
            "Bowling For Soup - 1985": ("Bowling For Soup", "1985"),
            "Bad City - Call Paul Stanley [AUDIO]": ("Bad City", "Call Paul Stanley"),
            "Olly Murs - Troublemaker ft. Flo Rida": ("Olly Murs", "Troublemaker ft. Flo Rida"),
            "Band - Song (Official Music Video) [HD]": ("Band", "Song"),
            "Frederic _oddloop": ("", "Frederic _oddloop"),
            "Song (Live in Tokyo)": ("", "Song (Live in Tokyo)"),
            "- Untitled": ("", "- Untitled"),
        }
        for stem, expected in cases.items():
            self.assertEqual(R.guess_from_name(stem), expected, stem)


class AddingASong(Base):
    def test_a_right_ogg_goes_in_without_vlc(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from test_radio_maker import ogg_and_label
        ogg, _ = ogg_and_label(TOWER_SONG)
        song = C.add_song(self.file("fine.ogg", ogg), None)
        self.assertEqual(song.rate, 44100)

    def test_without_vlc_it_says_where_to_get_it(self) -> None:
        path = self.root / "a.wav"
        write_wav(path, 44100, 2, 0.1)
        with self.assertRaises(R.RadioMakerError) as caught:
            C.add_song(path, None)
        self.assertIn("VLC", str(caught.exception))


@unittest.skipUnless(VLC, "needs VLC installed")
class WithVlc(Base):
    def test_a_48k_mono_wav_comes_out_as_a_radio_song(self) -> None:
        folder = self.root / "it's {odd}, folder"
        folder.mkdir()
        path = folder / "Don't Stop, {live}.wav"
        write_wav(path, 48000, 1, 3)
        song = C.add_song(path, VLC)
        self.assertEqual((song.channels, song.rate), (2, 44100))
        self.assertAlmostEqual(song.seconds, 3, delta=0.25)
        self.assertEqual(song.path, path)
        self.assertEqual(song.title, "Don't Stop, {live}")
        self.assertTrue(song.data.startswith(b"OggS"))

    def test_the_tags_come_from_the_original(self) -> None:
        path = self.root / "tagged.wav"
        write_wav(path, 22050, 2, 1)
        info = b"INFO" + b"INAM" + struct.pack("<I", 4) + b"Mine" + b"IART" + struct.pack("<I", 4) + b"Band"
        data = bytearray(path.read_bytes() + b"LIST" + struct.pack("<I", len(info)) + info)
        struct.pack_into("<I", data, 4, len(data) - 8)
        path.write_bytes(bytes(data))
        song = C.add_song(path, VLC)
        self.assertEqual((song.title, song.artist), ("Mine", "Band"))

    def test_a_file_vlc_cannot_play_is_refused(self) -> None:
        with self.assertRaises(R.RadioMakerError) as caught:
            C.add_song(self.file("noise.mp3", b"\x00" * 5000), VLC)
        self.assertIn("VLC could not convert", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
