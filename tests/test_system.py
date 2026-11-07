"""Read-only smoke tests against the real machine: every probe returns something sane."""
import os
import subprocess
import sys
import unittest

from mintcare import checks, cleaner, hardware, tweaks
from tests.helpers import ROOT


@unittest.skipIf(os.environ.get("MINTCARE_SKIP_SYSTEM_TESTS"), "needs a real Mint desktop (CI/package build)")
class Probes(unittest.TestCase):
    def test_every_check_returns_a_result(self):
        for c in checks.CHECKS:
            with self.subTest(check=c.id):
                r = c.run()
                self.assertIn(r.status, (checks.OK, checks.INFO, checks.WARN, checks.FAIL))
                self.assertTrue(r.summary)
                self.assertNotIn("Could not check", r.summary)

    def test_every_tweak_state(self):
        for t in tweaks.TWEAKS:
            with self.subTest(tweak=t.id):
                if t.available():
                    self.assertIn(t.state(), ("mintcare", "system", "off"))

    def test_cleaner_scans(self):
        for i in cleaner.ITEMS:
            if i.available():
                with self.subTest(item=i.id):
                    size, detail = i.scan()
                    self.assertTrue(size is None or size >= 0)
                    self.assertIsInstance(detail, str)

    def test_hardware_reads(self):
        self.assertIsInstance(hardware.drives(), list)
        self.assertIsInstance(hardware.sensors(with_gpu=False), list)

    def test_cli_runs(self):
        for args in (["tweaks"], ["clean"], ["--version"]):
            with self.subTest(args=args):
                p = subprocess.run([sys.executable, f"{ROOT}/mintcare.py", *args], capture_output=True,
                                   text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
