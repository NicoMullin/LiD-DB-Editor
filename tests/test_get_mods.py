"""Getting KSFA's plugin mods from GitHub (get_mods.py), with GitHub faked.

What matters: nothing is used that GitHub does not vouch for (the SHA-256 it
lists), nothing in a package lands outside where it belongs, a plugin never
goes in without the loader, and a dinput8.dll that is not the loader is never
replaced.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path

from lid_db_manager import get_mods as G

LOADER_BYTES = b"MZ loader " + "LiD Plugin Loader".encode("utf-16-le")
OLD_FIGHTER_BYTES = b"MZ old " + "LiD Fighter Mod".encode("utf-16-le")


def package_zip(name, version, *, mods=(), plugins=(), replaces=(), loader=False, extra=None,
                manifest=None, mod_plugin=None) -> bytes:
    data = manifest if manifest is not None else {
        "format": 1, "name": name, "version": version, "mods": list(mods),
        "plugins": list(plugins), "replaces": list(replaces), "loader": "dinput8.dll" if loader else "",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lid-package.json", json.dumps(data))
        for mod in mods:
            data = {"id": mod, "version": version}
            if mod_plugin is not None:
                data["plugin"] = mod_plugin
            archive.writestr(f"mods/{mod}/mod.json", json.dumps(data))
            archive.writestr(f"mods/{mod}/readme.md", f"{mod} {version}")
        for dll in plugins:
            archive.writestr(f"lid-plugins/{dll}", f"MZ {dll} {version}")
        if loader:
            archive.writestr("dinput8.dll", LOADER_BYTES)
        for path, body in (extra or {}).items():
            archive.writestr(path, body)
    return buffer.getvalue()


class FakeGitHub:
    """Answers releases/latest and the downloads for the repos it is given."""

    def __init__(self):
        self.repos: dict[str, tuple[str, bytes, str]] = {}   # repo -> (tag, zip, digest override)

    def publish(self, entry: G.Offer, tag: str, body: bytes, digest: str | None = None):
        self.repos[entry.repo] = (tag, body, digest or hashlib.sha256(body).hexdigest())

    def __call__(self, url: str):
        for repo, (tag, body, digest) in self.repos.items():
            api = G.API.format(owner=G.OWNER, repo=repo)
            download = G.DOWNLOADS.format(owner=G.OWNER, repo=repo) + f"{tag}/{repo}.zip"
            if url == api:
                return io.BytesIO(json.dumps({
                    "tag_name": tag, "html_url": f"https://github.com/{G.OWNER}/{repo}/releases/tag/{tag}",
                    "assets": [
                        {"name": f"{repo}.zip", "browser_download_url": download, "size": len(body),
                         "digest": f"sha256:{digest}"},
                        {"name": "SHA256SUMS.txt", "browser_download_url": download + ".txt", "size": 1,
                         "digest": "sha256:00"},
                    ],
                }).encode())
            if url == download:
                return io.BytesIO(body)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.mods = root / "mods"
        self.mods.mkdir()
        self.game = root / "LET IT DIE"
        G.win64(self.game).mkdir(parents=True)
        self.work = root / "work"
        self.github = FakeGitHub()

    def get(self, entry: G.Offer):
        release = G.latest(entry, opener=self.github)
        packed = G.download(release, self.work, opener=self.github)
        package = G.unpack(packed, self.work / entry.key)
        return package, G.install(package, self.mods, self.game)

    def loader(self) -> Path:
        return G.win64(self.game) / "dinput8.dll"

    def plugins(self) -> Path:
        return G.win64(self.game) / "lid-plugins"


def versioned(name: bytes, *numbers: int) -> bytes:
    """A stand-in DLL with a VS_FIXEDFILEINFO saying it is version `numbers`."""
    a, b, c, d = (list(numbers) + [0, 0, 0, 0])[:4]
    info = G.VERSION_SIGNATURE + (0x00010000).to_bytes(4, "little") + \
        ((a << 16) | b).to_bytes(4, "little") + ((c << 16) | d).to_bytes(4, "little")
    return b"MZ " + name + b" padding " + info + b" the rest"


class TheReleaseHere(Base):
    """What Have shows: the release a plugin came from, read from its own
    version - the same number as Newest, not the bundled mod's own."""

    def test_a_dlls_version(self):
        path = self.plugins()
        path.mkdir()
        dll = path / "x.dll"
        dll.write_bytes(versioned(b"x", 0, 12, 1))
        self.assertEqual(G.dll_version(dll), "0.12.1")
        dll.write_bytes(versioned(b"x", 1, 2, 3, 4))
        self.assertEqual(G.dll_version(dll), "1.2.3.4")
        dll.write_bytes(b"MZ no version resource")
        self.assertEqual(G.dll_version(dll), "")
        self.assertEqual(G.dll_version(path / "missing.dll"), "")

    def test_the_plugins_release_not_the_mods_version(self):
        self.plugins().mkdir()
        (self.mods / "Offline Fixes").mkdir()
        (self.mods / "Offline Fixes" / "mod.json").write_text('{"version": "0.1.0"}', encoding="utf-8")
        (self.plugins() / "lid-offlinefixes.dll").write_bytes(versioned(b"lid-offlinefixes.dll", 0, 1, 1))
        have = G.installed(G.offer("offlinefixes"), self.mods, self.game)
        self.assertEqual((have.release, have.mods), ("0.1.1", {"Offline Fixes": "0.1.0"}))

    def test_a_switched_off_plugin_and_the_loader(self):
        self.plugins().mkdir()
        (self.plugins() / "lid-timers.dll.off").write_bytes(versioned(b"lid-timers.dll", 0, 2, 2))
        self.assertEqual(G.installed(G.offer("timers"), self.mods, self.game).release, "0.2.2")
        self.loader().write_bytes(LOADER_BYTES + versioned(b"dinput8.dll", 1, 0, 0))
        self.assertEqual(G.installed(G.LOADER_OFFER, self.mods, self.game).release, "1.0.0")
        self.loader().write_bytes(OLD_FIGHTER_BYTES + versioned(b"dinput8.dll", 1, 4, 0))
        self.assertEqual(G.installed(G.LOADER_OFFER, self.mods, self.game).release, "")   # not ours

    def test_nothing_without_a_game_folder(self):
        have = G.installed(G.offer("timers"), self.mods, None)
        self.assertEqual((have.release, have.plugin), ("", False))


class AskingGitHub(Base):
    def test_the_newest_release_and_its_package(self):
        entry = G.offer("timers")
        self.github.publish(entry, "v0.2.0", package_zip("Timers", "0.2.0", mods=["Timers"],
                                                         plugins=["lid-timers.dll"]))
        release = G.latest(entry, opener=self.github)
        self.assertEqual((release.version, release.asset.name), ("0.2.0", "LiD-Timers.zip"))

    def test_no_release_yet_says_so(self):
        with self.assertRaises(G.GetModsError) as caught:
            G.latest(G.offer("timers"), opener=self.github)
        self.assertIn("no release", str(caught.exception))

    def test_a_package_without_a_listed_sha256_is_not_used(self):
        entry = G.offer("timers")
        body = package_zip("Timers", "0.2.0", plugins=["lid-timers.dll"])
        self.github.publish(entry, "v0.2.0", body)
        original = self.github.__call__

        def no_digest(url):
            answer = original(url)
            if url.startswith("https://api.github.com/"):
                data = json.loads(answer.read())
                for asset in data["assets"]:
                    asset.pop("digest")
                return io.BytesIO(json.dumps(data).encode())
            return answer
        release = G.latest(entry, opener=no_digest)
        self.assertIsNone(release.asset)
        with self.assertRaises(G.GetModsError):
            G.download(release, self.work, opener=no_digest)

    def test_a_download_that_does_not_match_its_sha256_is_thrown_away(self):
        entry = G.offer("timers")
        self.github.publish(entry, "v0.2.0", package_zip("Timers", "0.2.0"), digest="ab" * 32)
        with self.assertRaises(G.GetModsError) as caught:
            self.get(entry)
        self.assertIn("SHA-256", str(caught.exception))
        self.assertEqual(list(self.mods.iterdir()), [])


class Installing(Base):
    def publish_loader(self, version="1.0.0"):
        self.github.publish(G.LOADER_OFFER, f"v{version}", package_zip("Plugin Loader", version, loader=True))

    def test_a_plugin_needs_the_loader_first(self):
        entry = G.offer("timers")
        self.github.publish(entry, "v0.2.0", package_zip("Timers", "0.2.0", mods=["Timers"],
                                                         plugins=["lid-timers.dll"]))
        with self.assertRaises(G.GetModsError) as caught:
            self.get(entry)
        self.assertIn("Plugin Loader", str(caught.exception))
        self.assertFalse((self.mods / "Timers").exists())      # nothing written

    def test_loader_then_plugin_and_mod(self):
        self.publish_loader()
        _, done = self.get(G.LOADER_OFFER)
        self.assertEqual(self.loader().read_bytes(), LOADER_BYTES)
        self.assertTrue(self.plugins().is_dir())
        self.assertIn("put the loader", done[0])
        entry = G.offer("timers")
        self.github.publish(entry, "v0.2.0", package_zip("Timers", "0.2.0", mods=["Timers"],
                                                         plugins=["lid-timers.dll"]))
        _, done = self.get(entry)
        self.assertEqual((self.plugins() / "lid-timers.dll").read_bytes(), b"MZ lid-timers.dll 0.2.0")
        self.assertTrue((self.mods / "Timers" / "mod.json").is_file())
        state = G.installed(entry, self.mods, self.game)
        self.assertEqual((state.mods, state.plugin, state.loader), ({"Timers": "0.2.0"}, True, "ours"))

    def test_an_update_replaces_the_mod_and_the_plugin(self):
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        entry = G.offer("deathmetals")
        for version in ("0.5.0", "0.6.0"):
            self.github.publish(entry, f"v{version}", package_zip(
                "Death Metals", version, mods=["Death Metals"], plugins=["lid-deathmetals.dll"]))
            self.get(entry)
        self.assertEqual(G.installed(entry, self.mods, self.game).mods, {"Death Metals": "0.6.0"})
        self.assertEqual((self.plugins() / "lid-deathmetals.dll").read_bytes(),
                         b"MZ lid-deathmetals.dll 0.6.0")
        self.assertEqual([p.name for p in self.mods.iterdir()], ["Death Metals"])   # nothing left behind

    def install_timers(self, version, switched_on, mod_plugin="lid-timers.dll"):
        entry = G.offer("timers")
        self.github.publish(entry, f"v{version}", package_zip(
            "Timers", version, mods=["Timers"], plugins=["lid-timers.dll"], mod_plugin=mod_plugin))
        release = G.latest(entry, opener=self.github)
        package = G.unpack(G.download(release, self.work, opener=self.github), self.work / version)
        return G.install(package, self.mods, self.game, switched_on=switched_on)

    def test_a_new_plugin_waits_switched_off_while_its_mod_is_unticked(self):
        # a new mod arrives unticked, so its plugin must not load before a save
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        done = self.install_timers("0.2.0", switched_on=set())
        self.assertFalse((self.plugins() / "lid-timers.dll").exists())
        self.assertEqual((self.plugins() / "lid-timers.dll.off").read_bytes(), b"MZ lid-timers.dll 0.2.0")
        self.assertTrue(any("until its mod is ticked" in line for line in done))
        self.assertTrue(G.installed(G.offer("timers"), self.mods, self.game).plugin)

    def test_a_plugin_goes_in_on_when_a_ticked_mod_names_it(self):
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        (self.plugins() / "lid-timers.dll.off").write_bytes(b"MZ older")
        self.install_timers("0.2.0", switched_on={"lid-timers.dll"})
        self.assertEqual((self.plugins() / "lid-timers.dll").read_bytes(), b"MZ lid-timers.dll 0.2.0")
        self.assertFalse((self.plugins() / "lid-timers.dll.off").exists())

    def test_an_update_while_its_mod_is_unticked_switches_the_plugin_off(self):
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        (self.plugins() / "lid-timers.dll").write_bytes(b"MZ older")
        self.install_timers("0.2.0", switched_on=set())
        self.assertFalse((self.plugins() / "lid-timers.dll").exists())
        self.assertEqual((self.plugins() / "lid-timers.dll.off").read_bytes(), b"MZ lid-timers.dll 0.2.0")

    def test_a_plugin_no_mod_names_keeps_its_state(self):
        # nothing would ever switch it back on, so it is not switched off here
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        self.install_timers("0.2.0", switched_on=set(), mod_plugin=None)
        self.assertTrue((self.plugins() / "lid-timers.dll").is_file())

    def test_death_metals_brings_the_stew_plugin_but_not_its_mod(self):
        # the Death Metal stew runs on lid-stewpull.dll; the Stew Multi Pull mod is another matter
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        entry = G.offer("deathmetals")
        stew = G.offer("stewpull")
        self.assertEqual(G.plugins_needed(entry, self.game), [stew])
        self.github.publish(stew, "v0.5.2", package_zip(
            "Stew Multi Pull", "0.5.2", mods=["Stew Multi Pull"], plugins=["lid-stewpull.dll"],
            mod_plugin="lid-stewpull.dll"))
        release = G.latest(stew, opener=self.github)
        package = G.plugin_only(G.unpack(G.download(release, self.work, opener=self.github), self.work / "s"))
        self.assertEqual(G.what_it_writes(package, self.mods, self.game),
                         [f"lid-stewpull.dll into {self.plugins()}"])
        G.install(package, self.mods, self.game, switched_on={"lid-deathmetals.dll", "lid-stewpull.dll"})
        self.assertFalse((self.mods / "Stew Multi Pull").exists())
        self.assertTrue((self.plugins() / "lid-stewpull.dll").is_file())
        self.assertEqual(G.plugins_needed(entry, self.game), [])     # there now: not got again

    def test_the_vending_add_on_follows_the_mod_that_comes_with_the_manager(self):
        # its package has no mod; the Vending Machine mod already here names the plugin
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        entry = G.offer("vending")
        self.github.publish(entry, "v0.1.0", package_zip("Vending Machine add-on", "0.1.0",
                                                         plugins=["lid-vending.dll"]))
        release = G.latest(entry, opener=self.github)
        package = G.unpack(G.download(release, self.work, opener=self.github), self.work / "v")
        self.assertEqual(package.named, set())
        package.named |= {"lid-vending.dll"}        # as the window adds the names the mods here use
        G.install(package, self.mods, self.game, switched_on=set())
        self.assertTrue((self.plugins() / "lid-vending.dll.off").is_file())
        self.assertTrue(G.installed(entry, self.mods, self.game).plugin)

    def test_a_switched_off_stew_plugin_counts_as_there(self):
        self.plugins().mkdir()
        (self.plugins() / "lid-stewpull.dll.off").write_bytes(b"MZ")
        self.assertEqual(G.plugins_needed(G.offer("deathmetals"), self.game), [])
        self.assertEqual(G.plugins_needed(G.offer("deathmetals"), None), [])

    def test_the_old_fighter_name_is_removed(self):
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        (self.plugins() / "lid-fighter.dll").write_bytes(b"MZ old")
        entry = G.offer("classpassives")
        self.github.publish(entry, "v2.0.0", package_zip(
            "Fighter Class Passives", "2.0.0", mods=["fighter-passives"],
            plugins=["lid-classpassives.dll"], replaces=["lid-fighter.dll"]))
        package, done = self.get(entry)
        self.assertFalse((self.plugins() / "lid-fighter.dll").exists())
        self.assertTrue((self.plugins() / "lid-classpassives.dll").exists())
        self.assertTrue(any("old name" in line for line in done))

    def test_someone_elses_dinput8_is_never_replaced(self):
        self.loader().write_bytes(b"MZ ReShade or something")
        self.publish_loader()
        with self.assertRaises(G.GetModsError) as caught:
            self.get(G.LOADER_OFFER)
        self.assertIn("left alone", str(caught.exception))
        self.assertEqual(self.loader().read_bytes(), b"MZ ReShade or something")
        self.assertEqual(G.loader_state(self.game), "foreign")

    def test_the_old_fighter_mod_dinput8_is_replaced_by_the_loader(self):
        self.loader().write_bytes(OLD_FIGHTER_BYTES)
        self.assertEqual(G.loader_state(self.game), "old-fighter")
        self.publish_loader()
        _, done = self.get(G.LOADER_OFFER)
        self.assertEqual(self.loader().read_bytes(), LOADER_BYTES)
        self.assertIn("old Fighter Mod", done[0])

    def test_without_a_game_folder_a_plugin_is_refused(self):
        entry = G.offer("offlinefixes")
        self.github.publish(entry, "v0.1.0", package_zip("Offline Fixes", "0.1.0",
                                                         plugins=["lid-offlinefixes.dll"]))
        release = G.latest(entry, opener=self.github)
        package = G.unpack(G.download(release, self.work, opener=self.github), self.work / "x")
        with self.assertRaises(G.GetModsError) as caught:
            G.install(package, self.mods, None)
        self.assertIn("Set game folder", str(caught.exception))

    def test_it_says_what_it_will_write_first(self):
        self.publish_loader()
        self.get(G.LOADER_OFFER)
        (self.plugins() / "lid-fighter.dll").write_bytes(b"MZ old")
        entry = G.offer("classpassives")
        self.github.publish(entry, "v2.0.0", package_zip(
            "Fighter Class Passives", "2.0.0", mods=["fighter-passives"],
            plugins=["lid-classpassives.dll"], replaces=["lid-fighter.dll"]))
        release = G.latest(entry, opener=self.github)
        package = G.unpack(G.download(release, self.work, opener=self.github), self.work / "p")
        lines = G.what_it_writes(package, self.mods, self.game)
        self.assertEqual(len(lines), 3)
        self.assertIn("fighter-passives", lines[0])
        self.assertIn("lid-classpassives.dll", lines[1])
        self.assertIn("remove lid-fighter.dll", lines[2])
        self.assertFalse(G.needs_loader(package, self.game))


class BadPackages(Base):
    def unpack(self, body: bytes) -> G.Package:
        path = self.work / "p.zip"
        self.work.mkdir(exist_ok=True)
        path.write_bytes(body)
        return G.unpack(path, self.work / "p")

    def test_a_file_outside_the_package_is_refused(self):
        with self.assertRaises(G.GetModsError):
            self.unpack(package_zip("X", "1", extra={"../evil.dll": b"MZ"}))
        self.assertFalse((self.work / "evil.dll").exists())

    def test_a_mod_it_does_not_hold(self):
        body = package_zip("X", "1", manifest={"format": 1, "name": "X", "version": "1", "mods": ["Ghost"]})
        with self.assertRaises(G.GetModsError):
            self.unpack(body)

    def test_a_plugin_it_does_not_hold_or_a_strange_name(self):
        for plugins in (["lid-ghost.dll"], ["dinput8.dll"], ["../x.dll"]):
            with self.subTest(plugins=plugins):
                body = package_zip("X", "1", manifest={"format": 1, "name": "X", "version": "1",
                                                       "plugins": plugins})
                with self.assertRaises(G.GetModsError):
                    self.unpack(body)

    def test_an_unknown_format(self):
        with self.assertRaises(G.GetModsError) as caught:
            self.unpack(package_zip("X", "1", manifest={"format": 2, "name": "X", "version": "1"}))
        self.assertIn("update the manager", str(caught.exception))

    def test_not_a_package_at_all(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "hi")
        with self.assertRaises(G.GetModsError):
            self.unpack(buffer.getvalue())


class TheCatalog(unittest.TestCase):
    def test_every_offer_is_named_like_its_repository(self):
        keys = [entry.key for entry in G.CATALOG]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in G.CATALOG:
            with self.subTest(entry=entry.key):
                self.assertTrue(entry.repo.startswith("LiD-"))
                self.assertEqual(entry.package, f"{entry.repo}.zip")
                if not entry.is_loader:
                    self.assertRegex(entry.plugin, G.DLL_NAME)
        self.assertEqual(G.CATALOG[0], G.LOADER_OFFER)


class FakeModsRepo:
    """Answers mods-index.json and each mod's zip, as the LiD-Mods release would."""

    def __init__(self):
        self.mods: list[dict] = []
        self.files: dict[str, bytes] = {}

    def publish(self, folder: str, version: str, *, digest: str | None = None, **extra) -> dict:
        body = package_zip(folder, version, mods=[folder])
        file = f"{folder.lower().replace(' ', '-')}-{version}.zip"
        self.files[file] = body
        entry = {"folder": folder, "name": folder, "version": version, "file": file,
                 "size": len(body), "sha256": digest or hashlib.sha256(body).hexdigest(), **extra}
        self.mods = [m for m in self.mods if m["folder"] != folder] + [entry]
        return entry

    def __call__(self, url: str):
        if url == G.MODS_INDEX_URL:
            return io.BytesIO(json.dumps({"format": 1, "mods": self.mods}).encode())
        if url.startswith(G.MODS_DOWNLOADS) and url[len(G.MODS_DOWNLOADS):] in self.files:
            return io.BytesIO(self.files[url[len(G.MODS_DOWNLOADS):]])
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


class TheModsRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.mods = root / "mods"
        self.mods.mkdir()
        self.work = root / "work"
        self.repo = FakeModsRepo()

    def listed(self, folder: str) -> G.ListedMod:
        return next(m for m in G.mods_index(opener=self.repo) if m.folder == folder)

    def test_it_lists_what_the_index_says(self):
        self.repo.publish("Vending Machine", "0.4.1", author="KSFA", min_manager_version="0.13.4")
        mod = self.listed("Vending Machine")
        self.assertEqual((mod.version, mod.author, mod.min_manager_version), ("0.4.1", "KSFA", "0.13.4"))
        self.assertEqual(mod.asset.url, G.MODS_DOWNLOADS + "vending-machine-0.4.1.zip")

    def test_entries_that_do_not_check_out_are_left_out(self):
        good = self.repo.publish("Quest Limit", "0.1.1")
        for bad in ({"folder": "../escape"}, {"folder": "_example"}, {"file": "../x.zip"},
                    {"file": "Has Spaces.zip"}, {"sha256": "abc"}, {"size": 0}, {"size": "lots"}):
            self.repo.mods.append(dict(good, **bad))
        self.assertEqual([m.folder for m in G.mods_index(opener=self.repo)], ["Quest Limit"])

    def test_no_list_yet_or_a_strange_one_says_so(self):
        with self.assertRaisesRegex(G.GetModsError, "no mods published yet"):
            G.mods_index(opener=lambda url: (_ for _ in ()).throw(
                urllib.error.HTTPError(url, 404, "Not Found", None, None)))
        with self.assertRaisesRegex(G.GetModsError, "update the manager"):
            G.read_mods_index(b'{"format": 2, "mods": []}')

    def test_getting_one_brings_only_that_mod(self):
        self.repo.publish("Quest Limit", "0.1.1")
        self.repo.publish("Vending Machine", "0.4.1")
        package = G.download_mod(self.listed("Vending Machine"), self.work, opener=self.repo)
        self.assertEqual(G.install(package, self.mods, None), ["added the mod Vending Machine"])
        self.assertEqual(G.installed_version(self.mods, "Vending Machine"), "0.4.1")
        self.assertFalse((self.mods / "Quest Limit").exists())

    def test_a_download_that_does_not_match_its_hash_is_refused(self):
        self.repo.publish("Vending Machine", "0.4.1", digest="0" * 64)
        with self.assertRaisesRegex(G.GetModsError, "SHA-256"):
            G.download_mod(self.listed("Vending Machine"), self.work, opener=self.repo)

    def test_one_needing_a_newer_manager_is_not_downloaded(self):
        self.repo.publish("Vending Machine", "9.0.0", min_manager_version="99.0.0")
        mod = self.listed("Vending Machine")
        self.assertEqual(G.mod_action(mod, ""), "Needs manager 99.0.0")
        with self.assertRaisesRegex(G.GetModsError, "needs manager 99.0.0"):
            G.download_mod(mod, self.work, opener=lambda url: self.fail("it went online"))

    def test_the_button_says_what_it_will_do(self):
        mod = G.ListedMod("Vending Machine", "Vending Machine", "0.4.1", "v.zip", 1, "0" * 64)
        self.assertEqual(G.mod_action(mod, ""), "Get")
        self.assertEqual(G.mod_action(mod, "0.4.0"), "Update")
        self.assertEqual(G.mod_action(mod, "0.4.1"), "Reinstall")
        self.assertEqual(G.mod_action(mod, "0.5.0"), "Reinstall")       # never offered as an update
        self.assertEqual(G.mod_action(mod, "?"), "Update")

    def test_updating_replaces_the_old_copy_whole(self):
        old = self.mods / "Vending Machine"
        old.mkdir()
        (old / "mod.json").write_text('{"id": "Vending Machine", "version": "0.4.0"}', encoding="utf-8")
        (old / "gone.sql").write_text("old", encoding="utf-8")
        self.repo.publish("Vending Machine", "0.4.1")
        package = G.download_mod(self.listed("Vending Machine"), self.work, opener=self.repo)
        self.assertEqual(G.install(package, self.mods, None), ["updated the mod Vending Machine"])
        self.assertEqual(G.installed_version(self.mods, "Vending Machine"), "0.4.1")
        self.assertFalse((old / "gone.sql").exists())


if __name__ == "__main__":
    unittest.main()
