"""Mods > Make a radio station: the player's .ogg files as song packages, a channel each.

The songs of the bundled Tower Static Radio mod (S3er0i9ng's, made the same
way and proven in game) stand in for both the template and the player's music:
their .ogg data is pulled out, and building a package from one song plus
another's .ogg must give that other song's package, byte for byte, apart from
its GUID. The station's SQL runs on a copy of the real vanilla database.
"""

from __future__ import annotations

import shutil
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from lid_db_manager import radio_maker as R
from lid_db_manager.mod_loader import load_mod_folder
from lid_db_manager.upk import package as P
from lid_db_manager.upk.texture2d import _read_bulk, read_tags

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOWER = PROJECT_ROOT / "mods" / "Tower Static Radio" / "assets"
VANILLA_DB = PROJECT_ROOT / "LiD Vanilla DB" / "5.0.4.2" / "masters.db"


def package(path: Path) -> P.Package:
    summary, flat = P.decompressed(path.read_bytes())
    return P.Package(summary, flat, P.read_names(flat, summary), P.read_imports(flat, summary),
                     P.read_exports(flat, summary))


def ogg_and_label(path: Path) -> tuple[bytes, str]:
    pkg = package(path)
    data = pkg.export_data(5)
    tags, at, _ = read_tags(pkg, data, 4)
    marker, _, _ = read_tags(pkg, next(t for t in tags if t.name == "SoundMarkers").value, 4)
    label = next(t for t in marker if t.name == "Label").value[4:-1].decode()
    _, at = _read_bulk(data, at)
    pc, _ = _read_bulk(data, at)
    return pc.inline, label


def guid_spots(pkg: P.Package) -> set[int]:
    header = pkg.summary.offsets_at["thumbnail_table_offset"] + 4
    sf = pkg.exports[3]
    inner = sf.serial_offset_at + 12 + 4 * len(sf.net_objects)
    return set(range(header, header + 16)) | set(range(inner, inner + 16))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._run = tempfile.TemporaryDirectory()
        self.addCleanup(self._run.cleanup)
        self.root = Path(self._run.name)

    def song_file(self, number: int, name: str | None = None) -> Path:
        ogg, _ = ogg_and_label(TOWER / f"SN_BGM_Radio_Music_{number:04d}_SF.upk")
        path = self.root / (name or f"song {number}.ogg")
        path.write_bytes(ogg)
        return path


class TheSongPackage(Base):
    def test_it_rebuilds_a_proven_song_byte_for_byte(self) -> None:
        template = (TOWER / "SN_BGM_Radio_Music_0901_SF.upk").read_bytes()
        for number in (902, 903, 904):
            theirs_path = TOWER / f"SN_BGM_Radio_Music_{number:04d}_SF.upk"
            _, label = ogg_and_label(theirs_path)
            song = R.read_song(self.song_file(number))
            mine = R.build_song_package(template, number, song, label=label)
            theirs = theirs_path.read_bytes()
            self.assertEqual(len(mine), len(theirs))
            spots = guid_spots(package(theirs_path))
            differ = [i for i in range(len(mine)) if mine[i] != theirs[i] and i not in spots]
            self.assertEqual(differ, [], number)

    def test_the_header_and_package_object_share_a_new_guid(self) -> None:
        template_path = TOWER / "SN_BGM_Radio_Music_0901_SF.upk"
        built = self.root / "x.upk"
        built.write_bytes(R.build_song_package(template_path.read_bytes(), 234,
                                               R.read_song(self.song_file(902))))
        pkg = package(built)
        self.assertNotEqual(pkg.summary.guid, package(template_path).summary.guid)
        self.assertEqual(pkg.exports[3].package_guid, pkg.summary.guid)
        paths = {pkg.full_path(i + 1) for i in range(len(pkg.exports))}
        self.assertIn("SN_BGM_Radio_Music_0234_SF", paths)
        self.assertFalse(any("0901" in p for p in paths))

    def test_no_number_the_game_would_not_play(self) -> None:
        song = R.read_song(self.song_file(902))
        template = (TOWER / "SN_BGM_Radio_Music_0901_SF.upk").read_bytes()
        with self.assertRaises(R.RadioMakerError):
            R.build_song_package(template, 1000, song)
        self.assertLess(R.LAST_SONG_NUMBER, 900)        # 09xx is the community packs'

    def test_the_length_is_the_songs_own(self) -> None:
        song = R.read_song(self.song_file(903))
        self.assertEqual((song.channels, song.rate), (2, 44100))
        self.assertEqual(song.samples, 150 * 44100)
        built = self.root / "x.upk"
        built.write_bytes(R.build_song_package((TOWER / "SN_BGM_Radio_Music_0901_SF.upk").read_bytes(),
                                               200, song))
        pkg = package(built)
        tags, _, _ = read_tags(pkg, pkg.export_data(5), 4)
        values = {t.name: t.value for t in tags}
        self.assertEqual(struct.unpack("<f", values["Duration"])[0], 150.0)
        self.assertEqual(struct.unpack("<i", values["RawPCMDataSize"])[0], 150 * 44100 * 4)


class ReadingAFile(Base):
    def test_title_falls_back_to_the_file_name(self) -> None:
        song = R.read_song(self.song_file(902, "My Song.ogg"))
        self.assertEqual(song.title, "My Song")

    def test_it_refuses_what_the_game_cannot_play(self) -> None:
        good = self.song_file(902).read_bytes()
        cases = {
            "not ogg.ogg": b"ID3" + good[3:],
            "opus.ogg": good.replace(b"\x01vorbis", b"OpusHead", 1),
        }
        at = good.find(b"\x01vorbis")
        rate48 = bytearray(good)
        struct.pack_into("<I", rate48, at + 12, 48000)
        cases["48k.ogg"] = bytes(rate48)
        mono = bytearray(good)
        mono[at + 11] = 1
        cases["mono.ogg"] = bytes(mono)
        for name, data in cases.items():
            path = self.root / name
            path.write_bytes(data)
            with self.assertRaises(R.RadioMakerError, msg=name):
                R.read_song(path)


class TheStation(Base):
    def make(self, count: int, name: str = "Nico's Mix", db: Path | None = None, **kw) -> Path:
        mods = self.root / "mods"
        mods.mkdir(exist_ok=True)
        songs = [R.read_song(self.song_file(901 + i % 4, f"song {i}.ogg")) for i in range(count)]
        return R.make_station(mods, name, songs, TOWER / "SN_BGM_Radio_Music_0901_SF.upk", db, **kw)

    def test_each_song_is_a_channel_of_its_own(self) -> None:
        folder = self.make(7)
        mod = load_mod_folder(folder)
        self.assertEqual(mod.id, "Radio - Nico's Mix")
        self.assertEqual(len(list((folder / "assets").glob("*.upk"))), 7)
        sql = (folder / "radio.sql").read_text(encoding="utf-8")
        for channel in range(600, 607):
            self.assertIn(f"WHERE channel = '{channel}'", sql)
        self.assertIn("SN_BGM_Radio_Music_0206", sql)
        self.assertNotIn("'Nico''s Mix 2'", sql)

    def test_a_second_mod_takes_the_next_numbers(self) -> None:
        self.make(2)
        second = self.make(1)
        self.assertTrue(second.name.endswith("(2)"))
        sql = (second / "radio.sql").read_text(encoding="utf-8")
        self.assertIn("SN_BGM_Radio_Music_0202", sql)
        self.assertIn("'602'", sql)

    def test_it_needs_a_name_and_songs(self) -> None:
        with self.assertRaises(R.RadioMakerError):
            self.make(1, name="   ")
        with self.assertRaises(R.RadioMakerError):
            self.make(0)
        self.assertEqual([p for p in (self.root / "mods").iterdir()], [])

    def test_a_channel_shows_its_artist_or_the_name(self) -> None:
        song = R.read_song(self.song_file(902))
        self.assertEqual(R.channel_name(song, "Mix"), "Mix")
        song.artist = "Tower Static"
        self.assertEqual(R.channel_name(song, "Mix"), "Tower Static")

    @unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
    def test_the_rows_go_in_once(self) -> None:
        db = self.root / "masters.db"
        shutil.copy2(VANILLA_DB, db)
        folder = self.make(3, db=db)
        con = sqlite3.connect(str(db))
        self.addCleanup(con.close)
        script = (folder / "radio.sql").read_text(encoding="utf-8")
        con.executescript(script)
        con.executescript(script)
        rows = con.execute("SELECT channel, music_id1, music_id2, channel_name, rand_grp FROM master_radio_channel "
                           "WHERE channel >= '600' ORDER BY channel").fetchall()
        self.assertEqual(rows, [("600", "SN_BGM_Radio_Music_0200", "", "Nico's Mix", 0),
                                ("601", "SN_BGM_Radio_Music_0201", "", "Nico's Mix", 0),
                                ("602", "SN_BGM_Radio_Music_0202", "", "Nico's Mix", 0)])
        music = con.execute("SELECT COUNT(*) FROM master_radio_music WHERE music_id >= "
                            "'SN_BGM_Radio_Music_0200'").fetchone()[0]
        self.assertEqual(music, 3)

    @unittest.skipUnless(VANILLA_DB.is_file(), "needs the vanilla database")
    def test_the_shuffle_puts_them_in_group_1(self) -> None:
        db = self.root / "masters.db"
        shutil.copy2(VANILLA_DB, db)
        folder = self.make(2, db=db, shuffle=True)
        con = sqlite3.connect(str(db))
        self.addCleanup(con.close)
        con.executescript((folder / "radio.sql").read_text(encoding="utf-8"))
        groups = {g for (g,) in con.execute("SELECT rand_grp FROM master_radio_channel WHERE channel >= '600'")}
        self.assertEqual(groups, {1})
        # the game's own shuffle stations are what reads the group
        self.assertEqual(con.execute("SELECT music_id1 FROM master_radio_channel WHERE channel = '000'").fetchone(),
                         ("OBS",))


if __name__ == "__main__":
    unittest.main()
