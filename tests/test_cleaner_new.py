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

    def test_ai_model_downloads_are_sized(self):
        root = Path(self.tmp) / "models"
        (root / "hub").mkdir(parents=True)
        (root / "hub" / "model.bin").write_bytes(b"x" * 4096)
        with mock.patch.object(cleaner, "AI_MODELS", [root / "hub", root / "missing"]):
            size, names = cleaner.s_aimodels()
        self.assertGreaterEqual(size, 4096)
        self.assertIn("hub", names)

    def test_docker_summary_is_shown_as_text(self):
        with mock.patch.object(cleaner, "run", return_value=(0, "Images: 1.2GB\nBuild Cache: 300MB\n")):
            size, text = cleaner.s_docker()
        self.assertIsNone(size)
        self.assertEqual(text, "Images: 1.2GB; Build Cache: 300MB")
        with mock.patch.object(cleaner, "run", return_value=(1, "")):
            self.assertIsNone(cleaner.s_docker()[0])          # docker not reachable: no size, no guess
