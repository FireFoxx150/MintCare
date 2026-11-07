import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mintcare import maintenance, report


class SupportBundle(unittest.TestCase):
    def test_bundle_has_all_parts_and_no_personal_details(self):
        home = str(Path.home())
        events = [{"ts": 0, "kind": "clean", "title": f"Removed {home}/cache", "ok": True, "detail": ""}]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(report, "collect", return_value={}), \
                mock.patch.object(report, "render_html", return_value=f"<p>{home}/x</p>"), \
                mock.patch.object(report.hardware, "inventory", return_value=[("cpu", "Test CPU", "4 cores")]), \
                mock.patch.object(maintenance, "history", return_value=events):
            out = os.path.join(d, "bundle.zip")
            report.write_support_bundle(out)
            z = zipfile.ZipFile(out)
            self.assertEqual(sorted(z.namelist()),
                             ["MintCare-report.html", "README.txt", "activity.txt", "hardware.txt", "journal.txt"])
            everything = "".join(z.read(n).decode() for n in z.namelist())
            self.assertNotIn(home, everything)
            self.assertIn("Test CPU", z.read("hardware.txt").decode())
            self.assertIn("Removed ~/cache", z.read("activity.txt").decode())


if __name__ == "__main__":
    unittest.main()
