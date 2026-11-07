"""Restoring from a MintCare backup puts files back and keeps whatever they would overwrite."""
import os
from pathlib import Path
from unittest import mock

from mintcare import backup
from tests.helpers import TempDirTest


class Restore(TempDirTest):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "home"
        self.outside = Path(self.tmp) / "data" / "photos"
        self.snap = Path(self.tmp) / "snap"
        (self.snap / "Documents").mkdir(parents=True)
        (self.snap / "Documents" / "a.txt").write_text("from backup")
        (self.snap / self.outside.relative_to("/") / "p.jpg").parent.mkdir(parents=True)
        (self.snap / self.outside.relative_to("/") / "p.jpg").write_text("photo")
        (self.home / "Documents").mkdir(parents=True)
        (self.home / "Documents" / "a.txt").write_text("changed since")
        self.folders = [str(self.home / "Documents"), str(self.outside)]

    def restore(self, paths):
        with mock.patch.object(backup, "HOME", self.home):
            return backup.restore(self.snap, paths, self.folders)

    def test_single_file_keeps_the_current_copy(self):
        res = self.restore([self.snap / "Documents" / "a.txt"])
        self.assertTrue(res[0][1], res)
        self.assertEqual((self.home / "Documents" / "a.txt").read_text(), "from backup")
        self.assertEqual((self.home / "Documents" / "a.txt.before-restore").read_text(), "changed since")

    def test_whole_snapshot_and_outside_home(self):
        res = self.restore([self.snap])
        self.assertTrue(all(ok for _p, ok, _m in res), res)
        self.assertEqual((self.outside / "p.jpg").read_text(), "photo")

    def test_refuses_paths_outside_the_snapshot(self):
        res = self.restore([Path(self.tmp) / "home" / "Documents" / "a.txt"])
        self.assertFalse(res[0][1])
        self.assertFalse(os.path.exists(self.home / "Documents" / "a.txt.before-restore"))
