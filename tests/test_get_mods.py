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
                manifest=None) -> bytes:
    data = manifest if manifest is not None else {
        "format": 1, "name": name, "version": version, "mods": list(mods),
        "plugins": list(plugins), "replaces": list(replaces), "loader": "dinput8.dll" if loader else "",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lid-package.json", json.dumps(data))
        for mod in mods:
            archive.writestr(f"mods/{mod}/mod.json", json.dumps({"id": mod, "version": version}))
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


if __name__ == "__main__":
    unittest.main()
