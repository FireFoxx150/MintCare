import os
import subprocess
import sys
import unittest

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_probe.py")


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "needs a display (CI runs the tests under xvfb)")
class EveryPageDraws(unittest.TestCase):
    """Smoke test, not a visual comparison: every page of the main window must open and draw without an error."""

    def test_every_page_opens_and_draws(self):
        p = subprocess.run([sys.executable, PROBE, "pages"], capture_output=True, text=True, timeout=300)
        line = next((l for l in p.stdout.splitlines() if l.startswith("PROBLEMS:") or l == "CLEAN"), "")
        self.assertEqual(p.returncode, 0, f"{line} {p.stderr[-300:]}")


if __name__ == "__main__":
    unittest.main()
