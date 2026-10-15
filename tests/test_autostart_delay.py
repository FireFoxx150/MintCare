"""Startup delays are written as a per-user override and can be removed again."""
from pathlib import Path
from unittest import mock

from mintcare import autostart
from tests.helpers import TempDirTest

ENTRY = "[Desktop Entry]\nType=Application\nName=Chat\nExec=chat\n"


class Delay(TempDirTest):
    def test_set_and_clear(self):
        sysdir, userdir = Path(self.tmp) / "xdg", Path(self.tmp) / "user"
        sysdir.mkdir()
        (sysdir / "chat.desktop").write_text(ENTRY)
        with mock.patch.object(autostart, "SYSTEM_DIRS", [sysdir]), mock.patch.object(autostart, "USER_DIR", userdir):
            autostart.set_delay("chat.desktop", 30)
            self.assertIn("X-GNOME-Autostart-Delay=30", (userdir / "chat.desktop").read_text())
            self.assertEqual((sysdir / "chat.desktop").read_text(), ENTRY)        # system file untouched
            self.assertEqual(next(e for e in autostart.entries() if e["file"] == "chat.desktop")["delay"], 30)
            autostart.set_delay("chat.desktop", 0)
            self.assertNotIn("Delay", (userdir / "chat.desktop").read_text())
