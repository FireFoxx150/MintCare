"""The runner reports per-step exit codes and tweaks are only marked applied on success."""
import os
import unittest
from pathlib import Path

from mintcare import tweaks
from mintcare.runner import run_steps
from tests.helpers import TempDirTest


class Runner(TempDirTest):
    def test_codes_and_output(self):
        lines = []
        res = run_steps([("a", "ok", "echo hello"), ("b", "bad", "exit 3")], root=False, on_line=lines.append)
        self.assertEqual(res, {"a": 0, "b": 3})
        self.assertIn("hello", lines)

    def test_steps_cannot_read_stdin(self):
        res = run_steps([("a", "stdin", "read x && exit 9 || exit 0")], root=False, on_line=lambda _l: None)
        self.assertEqual(res["a"], 0)

    def test_marker_only_on_success(self):
        old = tweaks.MARKERS
        tweaks.MARKERS = Path(self.tmp) / "applied"
        try:
            bad = tweaks.Tweak("bad", "x", "c", "d", lambda: True, "false\necho SHOULD-NOT-RUN")
            good = tweaks.Tweak("good", "x", "c", "d", lambda: True, "true", revert="true")
            out = []
            res = run_steps([("bad", "bad", bad.apply_script()), ("good", "good", good.apply_script())],
                            root=False, on_line=out.append)
            self.assertNotEqual(res["bad"], 0)
            self.assertEqual(res["good"], 0)
            self.assertNotIn("SHOULD-NOT-RUN", out)        # set -e stops the failing step
            self.assertEqual(sorted(os.listdir(tweaks.MARKERS)), ["good"])
            self.assertEqual(good.state(), "mintcare")
            self.assertEqual(bad.state(), "system")
            run_steps([("r", "revert", good.revert_script())], root=False, on_line=lambda _l: None)
            self.assertFalse((tweaks.MARKERS / "good").exists())
        finally:
            tweaks.MARKERS = old


class SysctlCompare(unittest.TestCase):
    def test_cmp(self):
        c = tweaks._cmp
        self.assertTrue(c("bbr", "bbr", "="))
        self.assertFalse(c("cubic", "bbr", "="))
        self.assertTrue(c("1048576", "524288", ">="))
        self.assertFalse(c("8192", "524288", ">="))
        self.assertTrue(c("10", "10", "<="))
        self.assertFalse(c("60", "10", "<="))
        self.assertFalse(c("abc", "10", ">="))

    def test_sysctl_tweak_backs_up_and_restores(self):
        t = tweaks.BY_ID["bbr"]
        self.assertIn("/var/lib/mintcare/sysctl-bbr.bak", t.apply)
        self.assertIn("/etc/sysctl.d/99-mintcare-bbr.conf", t.apply)
        self.assertIn("sysctl -q -p /var/lib/mintcare/sysctl-bbr.bak", t.revert)


if __name__ == "__main__":
    unittest.main()
