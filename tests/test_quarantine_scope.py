import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mintcare import secaudit


class ScanList(unittest.TestCase):
    def test_links_cache_folders_and_odd_names_are_left_out(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, "ok.txt").write_text("fine")
            Path(home, "bad\nname.txt").write_text("a line break in the name")
            os.symlink("/etc/hostname", os.path.join(home, "link"))
            Path(home, ".cache").mkdir()
            Path(home, ".cache", "c.txt").write_text("cache")
            files, skipped = secaudit.scan_files([home])
            self.assertEqual(files, [os.path.join(home, "ok.txt")])
            self.assertEqual(skipped, 1)


class ScannerOutput(unittest.TestCase):
    def test_only_listed_files_count_as_infected(self):
        with tempfile.TemporaryDirectory() as home:
            ok = os.path.join(home, "ok.txt")
            Path(ok).write_text("eicar")
            lines = [f"{ok}: Eicar-Test-Signature FOUND\n", "/etc/shadow: Eicar-Test-Signature FOUND\n"]
            proc = mock.Mock(stdout=iter(lines))
            proc.wait.return_value = 1
            with mock.patch.object(secaudit.subprocess, "Popen", return_value=proc) as popen:
                res = secaudit.clam_scan([home])
            self.assertEqual(res["infected"], [(ok, "Eicar-Test-Signature")])
            self.assertIn("--file-list=", " ".join(popen.call_args.args[0]))
            self.assertNotIn("-r", popen.call_args.args[0])


class QuarantineScope(unittest.TestCase):
    def test_only_plain_files_under_home_are_moved(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as elsewhere, \
                tempfile.TemporaryDirectory() as qdir:
            inside = os.path.join(home, "inside.txt")
            Path(inside).write_text("mine")
            outside = os.path.join(elsewhere, "outside.txt")
            Path(outside).write_text("not mine")
            link = os.path.join(home, "link.txt")
            os.symlink(outside, link)
            _sid, _title, body = secaudit.quarantine_step([inside, link, outside], home=home)
            body = body.replace("/var/lib/mintcare/quarantine", qdir)
            out = subprocess.run(["bash", "-c", body], capture_output=True, text=True, cwd=home).stdout
            self.assertIn(f"Quarantined {inside}", out)
            self.assertIn(f"Skipped, not a plain file in your home folder: {link}", out)
            self.assertIn(f"Skipped, not a plain file in your home folder: {outside}", out)
            self.assertFalse(os.path.exists(inside))
            self.assertTrue(os.path.exists(os.path.join(qdir, "inside.txt")))
            self.assertTrue(os.path.exists(outside))
            self.assertTrue(os.path.islink(link))


if __name__ == "__main__":
    unittest.main()
