"""Leftover Flatpak data and unused Cinnamon add-ons are found, and nothing is guessed when lists can't be read."""
from pathlib import Path
from unittest import mock

from mintcare import cleaner
from tests.helpers import TempDirTest


class Leftovers(TempDirTest):
    def test_flatpak_data(self):
        root = Path(self.tmp) / "app"
        for a in ("org.kept.App", "org.gone.App"):
            (root / a).mkdir(parents=True)
        with mock.patch.object(cleaner, "VAR_APP", root):
            self.assertEqual([d.name for d in cleaner.leftover_flatpak_data(["org.kept.App"])], ["org.gone.App"])
            self.assertEqual(cleaner.leftover_flatpak_data([]), [])        # flatpak list failed: keep everything

    def test_spices(self):
        root = Path(self.tmp) / "cinnamon"
        for d in ("applets/weather@x", "applets/used@x", "desklets/clock@y"):
            (root / d).mkdir(parents=True)
        enabled = "['panel1:right:0:used@x:12'] @as [] @as []"
        with mock.patch.object(cleaner, "SPICES", root):
            self.assertEqual([d.name for d in cleaner.unused_spices(enabled)], ["weather@x", "clock@y"])
            self.assertEqual(cleaner.unused_spices("   "), [])             # gsettings failed: keep everything
