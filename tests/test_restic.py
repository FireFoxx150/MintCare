"""Encrypted copies with restic: the password is kept only once the repository exists, and arguments are never shell-parsed.

Module under test: mintcare/backup.py (restic_init, restic_backup, restic_latest). A fake `restic` on PATH records its
arguments, so no real repository or network is used.
"""
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from mintcare import backup
from tests.helpers import TempDirTest

FAKE_RESTIC = """#!/bin/sh
echo "$@ [repo=$RESTIC_REPOSITORY]" >> "$RESTIC_LOG"
case "$1" in
  init)
    case "$RESTIC_REPOSITORY" in *fail*) echo "Fatal: repository already exists" >&2; exit 1;; esac
    mkdir -p "$RESTIC_REPOSITORY"; echo "created restic repository"; exit 0;;
  backup) echo "Files: 2 new, 0 changed"; echo "snapshot 1a2b saved"; exit 0;;
  snapshots) echo '[{"time": "2026-11-04T08:00:00.123456789+00:00"}]'; exit 0;;
esac
exit 2
"""


class Restic(TempDirTest):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp)
        self.root = root
        bindir = root / "bin"
        bindir.mkdir()
        fake = bindir / "restic"
        fake.write_text(FAKE_RESTIC)
        fake.chmod(0o755)
        self.log = root / "restic.log"
        for patch in (mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                                                   "RESTIC_LOG": str(self.log)}),
                      mock.patch.object(backup, "RESTIC_PASSWORD", root / "config" / "restic-password")):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = root / "Documents"
        self.folder.mkdir()
        self.b = {"folders": [str(self.folder)], "excludes": ["*.tmp"], "restic_repo": str(root / "repo")}

    def test_restic_is_found_only_when_it_is_on_the_path(self):
        self.assertTrue(backup.restic_ready())
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "empty")}):
            self.assertFalse(backup.restic_ready())

    def test_password_is_kept_only_when_the_repository_is_created(self):
        ok, text = backup.restic_init(str(self.root / "repo"), "s3cret")
        self.assertTrue(ok, text)
        pw = backup.RESTIC_PASSWORD
        self.assertEqual(pw.read_text(), "s3cret")
        self.assertEqual(stat.S_IMODE(pw.stat().st_mode), 0o600)
        ok, _text = backup.restic_init(str(self.root / "fail-repo"), "other")
        self.assertFalse(ok)
        self.assertEqual(pw.read_text(), "s3cret")                 # the working password is not replaced
        self.assertFalse(pw.with_name(pw.name + ".new").exists())

    def test_an_empty_password_is_refused_before_restic_runs(self):
        ok, _text = backup.restic_init(str(self.root / "repo"), "")
        self.assertFalse(ok)
        self.assertFalse(self.log.exists())

    def test_folders_and_excludes_are_separate_arguments(self):
        ok, last = backup.restic_backup(self.b)
        self.assertTrue(ok)
        self.assertEqual(last, "snapshot 1a2b saved")
        logged = self.log.read_text().splitlines()[-1]
        self.assertTrue(logged.startswith(f"backup --exclude *.tmp {self.folder} --password-file "), logged)
        self.assertTrue(logged.endswith(f"[repo={self.b['restic_repo']}]"), logged)   # the repository comes from the environment

    def test_latest_snapshot_time_is_read_from_restic(self):
        self.assertEqual(backup.restic_latest(self.b), "2026-11-04T08:00:00.123456789+00:00")

    def test_a_repository_name_that_looks_like_an_option_is_refused(self):
        self.assertFalse(backup.restic_backup({**self.b, "restic_repo": "--help"})[0])
        self.assertIsNone(backup.restic_latest({**self.b, "restic_repo": ""}))


if __name__ == "__main__":
    unittest.main()
