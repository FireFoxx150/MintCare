"""Per-user desktop tweaks apply and revert against a private settings backend (your desktop is untouched)."""
import os
import subprocess
import unittest
from unittest import mock

from mintcare import tweaks
from tests.helpers import TempDirTest

HAVE_CINNAMON = "desktop-effects-workspace" in subprocess.run(
    ["gsettings", "list-keys", "org.cinnamon"], capture_output=True, text=True).stdout


@unittest.skipUnless(HAVE_CINNAMON, "Cinnamon settings schemas not installed")
class DesktopTweaks(TempDirTest):
    def test_effects_apply_and_revert(self):
        env = {"GSETTINGS_BACKEND": "keyfile", "XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg"),
               "XDG_STATE_HOME": os.path.join(self.tmp, "state")}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(tweaks, "USER_MARKERS", tweaks.Path(self.tmp) / "applied"):
            t = tweaks.gsettings_tweak("t1", "Test", "Desktop", "",
                                       [("org.cinnamon", "desktop-effects-workspace", "false")])
            sh = lambda script: subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(sh("gsettings set org.cinnamon desktop-effects-workspace true").returncode, 0)
            self.assertFalse(t.probe())
            self.assertEqual(sh(t.apply_script()).returncode, 0)
            self.assertTrue(t.probe())
            self.assertEqual(t.state(), "mintcare")
            self.assertEqual(sh(t.revert_script()).returncode, 0)
            self.assertEqual(t.state(), "off")          # back to the value it had before
            self.assertEqual(sh("gsettings get org.cinnamon desktop-effects-workspace").stdout.strip(), "true")

    def test_user_tweaks_are_marked(self):
        self.assertTrue(tweaks.BY_ID["effects"].user)
        self.assertFalse(tweaks.BY_ID["bbr"].user)
        self.assertIn("unredirect", next(p for p in tweaks.PROFILES if p.id == "gaming").tweaks)
