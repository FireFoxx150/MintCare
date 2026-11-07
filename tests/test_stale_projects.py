"""Unused project folders: a dependency or build folder is offered only when its project has been left alone."""
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from mintcare import cleaner
from tests.helpers import TempDirTest

OLD = time.time() - 200 * 86400


def touch(path, when=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    if when is not None:
        os.utime(path, (when, when))


class StaleProjects(TempDirTest):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp)
        touch(self.root / "stale_app/package.json", OLD)
        touch(self.root / "stale_app/node_modules/pkg/index.js")          # recent, but inside the folder itself
        touch(self.root / "fresh_app/package.json")                       # changed today: keep
        touch(self.root / "fresh_app/node_modules/pkg/index.js", OLD)
        touch(self.root / "rust_old/Cargo.toml", OLD)
        touch(self.root / "rust_old/target/debug/app")
        touch(self.root / "venv_old/app.py", OLD)
        touch(self.root / "venv_old/.venv/pyvenv.cfg")
        touch(self.root / "unmarked/node_modules/pkg/index.js", OLD)     # no package.json: not a project
        touch(self.root / ".cache/proj/package.json", OLD)                # cache folders are not searched
        touch(self.root / ".cache/proj/node_modules/x.js", OLD)

    def test_only_untouched_projects_are_listed(self):
        found = {os.path.relpath(p, self.root) for p, _n in cleaner.stale_folders(root=self.root)}
        self.assertEqual(found, {"stale_app/node_modules", "rust_old/target", "venv_old/.venv"})

    def test_the_scan_records_what_the_trash_step_moves(self):
        with mock.patch.object(cleaner, "STALE_ROOT", self.root):
            size, detail = cleaner.s_stale()
        self.assertGreater(size, 0)
        script = cleaner.BY_ID["stale"].user_script
        self.assertIn(f"gio trash {cleaner.q(str(self.root / 'stale_app/node_modules'))}", script)
        self.assertNotIn("fresh_app", script)
        self.assertIn("3 folders", detail)

    def test_nothing_stale_leaves_nothing_to_move(self):
        empty = self.root / "empty"
        empty.mkdir()
        with mock.patch.object(cleaner, "STALE_ROOT", empty):
            size, _detail = cleaner.s_stale()
        self.assertEqual(size, 0)
        self.assertIsNone(cleaner.BY_ID["stale"].user_script)


if __name__ == "__main__":
    unittest.main()
