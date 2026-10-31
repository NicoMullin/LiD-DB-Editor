"""The mods repository's build: one zip per mod, and the list the manager reads.

tools/publish_mods.py runs on every push to LiD-Mods. Only a mod whose version
went up is uploaded again, and a mod whose files changed without a new version
stops the run - the manager re-applies a mod only when its version changes.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from lid_db_manager import get_mods as G

TOOL = Path(__file__).resolve().parent.parent / "tools" / "publish_mods.py"
_spec = importlib.util.spec_from_file_location("publish_mods", TOOL)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def a_mod(mods: Path, folder: str, version: str, sql: str = "UPDATE x SET y = 1;", **extra) -> Path:
    path = mods / folder
    path.mkdir(parents=True, exist_ok=True)
    data = {"id": folder, "name": folder.title(), "version": version, "author": "KSFA",
            "description": f"{folder} does a thing", **extra}
    (path / "mod.json").write_text(json.dumps(data), encoding="utf-8")
    (path / "change.sql").write_text(sql, encoding="utf-8")
    return path


class Publishing(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods = self.root / "mods"
        a_mod(self.mods, "bag-size", "1.0.0", min_manager_version="0.12.0")
        a_mod(self.mods, "Vending Machine", "0.4.0", category="Shops")
        a_mod(self.mods, "_example", "0.1.0")         # a template: stays with the manager
        self.first = P.publish(self.mods, self.root / "out1", None, today="2026-10-31")

    def again(self, name: str = "out2") -> dict:
        return P.publish(self.mods, self.root / name, self.first["index"], today="2026-11-01")

    def test_the_first_run_lists_and_uploads_every_mod_but_the_templates(self) -> None:
        listed = {m["folder"]: m for m in self.first["index"]["mods"]}
        self.assertEqual(sorted(listed), ["Vending Machine", "bag-size"])
        self.assertEqual(sorted(self.first["upload"]), ["bag-size-1.0.0.zip", "vending-machine-0.4.0.zip"])
        self.assertEqual(listed["bag-size"]["min_manager_version"], "0.12.0")
        self.assertEqual(listed["Vending Machine"]["category"], "Shops")
        self.assertEqual(listed["bag-size"]["updated"], "2026-10-31")
        written = json.loads((self.root / "out1" / "mods-index.json").read_text(encoding="utf-8"))
        self.assertEqual(written, self.first["index"])

    def test_nothing_changed_nothing_to_upload(self) -> None:
        second = self.again()
        self.assertEqual(second["upload"], [])
        self.assertEqual(second["stale"], [])
        self.assertEqual(second["index"], self.first["index"])
        self.assertEqual(list((self.root / "out2").glob("*.zip")), [])

    def test_a_new_version_is_uploaded_and_its_old_zip_dropped(self) -> None:
        a_mod(self.mods, "Vending Machine", "0.4.1", sql="UPDATE x SET y = 2;", category="Shops")
        second = self.again()
        self.assertEqual(second["upload"], ["vending-machine-0.4.1.zip"])
        self.assertEqual(second["stale"], ["vending-machine-0.4.0.zip"])
        self.assertEqual(second["changes"], ["- **Vending Machine** 0.4.0 -> 0.4.1"])
        listed = {m["folder"]: m for m in second["index"]["mods"]}
        self.assertEqual(listed["Vending Machine"]["updated"], "2026-11-01")
        self.assertEqual(listed["bag-size"]["updated"], "2026-10-31")    # untouched

    def test_changed_files_without_a_new_version_stop_it(self) -> None:
        a_mod(self.mods, "Vending Machine", "0.4.0", sql="UPDATE x SET y = 2;", category="Shops")
        with self.assertRaisesRegex(P.PublishError, "Vending Machine: its files changed but its "
                                                    "version is still 0.4.0"):
            self.again()

    def test_an_older_version_stops_it(self) -> None:
        a_mod(self.mods, "bag-size", "0.9.0", min_manager_version="0.12.0")
        with self.assertRaisesRegex(P.PublishError, "older than the published 1.0.0"):
            self.again()

    def test_a_removed_mod_comes_off_the_list(self) -> None:
        import shutil
        shutil.rmtree(self.mods / "bag-size")
        second = self.again()
        self.assertEqual([m["folder"] for m in second["index"]["mods"]], ["Vending Machine"])
        self.assertEqual(second["stale"], ["bag-size-1.0.0.zip"])

    def test_a_mod_without_a_version_stops_it(self) -> None:
        (self.mods / "broken").mkdir()
        (self.mods / "broken" / "mod.json").write_text('{"id": "broken"}', encoding="utf-8")
        with self.assertRaisesRegex(P.PublishError, "broken: mod.json has no version"):
            self.again()

    def test_the_same_files_give_the_same_zip(self) -> None:
        again = P.publish(self.mods, self.root / "out3", None, today="2026-10-31")
        self.assertEqual(again["index"], self.first["index"])

    def test_its_zip_is_a_package_the_manager_installs(self) -> None:
        zip_path = self.root / "out1" / "vending-machine-0.4.0.zip"
        package = G.unpack(zip_path, self.root / "unpacked")
        self.assertEqual((package.name, package.version, package.mods), ("Vending Machine", "0.4.0",
                                                                         ["Vending Machine"]))
        listed = G.read_mods_index(json.dumps(self.first["index"]).encode())
        self.assertEqual([m.folder for m in listed], ["bag-size", "Vending Machine"])
        done = G.install(package, self.root / "manager-mods", None)
        self.assertEqual(done, ["added the mod Vending Machine"])
        self.assertEqual(G.installed_version(self.root / "manager-mods", "Vending Machine"), "0.4.0")


if __name__ == "__main__":
    unittest.main()
