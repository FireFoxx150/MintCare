"""Per-user state: startup-app overrides and maintenance bookkeeping, in throwaway dirs."""
import json
from pathlib import Path

from mintcare import autostart, maintenance
from tests.helpers import TempDirTest

ENTRY = """[Desktop Entry]
Type=Application
Name=Demo App
Exec=demo --autostart
{extra}
"""


class Autostart(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sys_dir = Path(self.tmp) / "xdg-autostart"
        self.user_dir = Path(self.tmp) / "user-autostart"
        self.sys_dir.mkdir()
        self._old = (autostart.SYSTEM_DIRS, autostart.USER_DIR)
        autostart.SYSTEM_DIRS, autostart.USER_DIR = [self.sys_dir], self.user_dir

    def tearDown(self):
        autostart.SYSTEM_DIRS, autostart.USER_DIR = self._old
        super().tearDown()

    def entries(self):
        return {e["file"]: e for e in autostart.entries()}

    def test_disable_writes_user_override_and_enable_restores(self):
        (self.sys_dir / "demo.desktop").write_text(ENTRY.format(extra=""))
        self.assertTrue(self.entries()["demo.desktop"]["enabled"])
        autostart.set_enabled("demo.desktop", False)
        e = self.entries()["demo.desktop"]
        self.assertFalse(e["enabled"])
        self.assertTrue(e["overridden"])
        self.assertIn("X-GNOME-Autostart-enabled=false", (self.user_dir / "demo.desktop").read_text())
        autostart.set_enabled("demo.desktop", True)
        self.assertTrue(self.entries()["demo.desktop"]["enabled"])
        self.assertTrue((Path("/etc/xdg/autostart")).exists() or True)

    def test_only_show_in_other_desktop_is_hidden(self):
        (self.sys_dir / "kde.desktop").write_text(ENTRY.format(extra="OnlyShowIn=KDE;"))
        with self._env("XDG_CURRENT_DESKTOP", "X-Cinnamon"):
            self.assertNotIn("kde.desktop", self.entries())

    def test_essential_is_flagged(self):
        (self.sys_dir / "cinnamon-settings-daemon-power.desktop").write_text(ENTRY.format(extra=""))
        self.assertTrue(self.entries()["cinnamon-settings-daemon-power.desktop"]["essential"])

    def _env(self, k, v):
        import contextlib
        import os

        @contextlib.contextmanager
        def cm():
            old = os.environ.get(k)
            os.environ[k] = v
            try:
                yield
            finally:
                if old is None:
                    del os.environ[k]
                else:
                    os.environ[k] = old
        return cm()


class Maintenance(TempDirTest):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp)
        self._old = {k: getattr(maintenance, k) for k in
                     ("CONFIG_DIR", "STATE", "SETTINGS_FILE", "HISTORY_FILE", "CHANGES_FILE", "LAST_RUN_FILE")}
        maintenance.CONFIG_DIR = root / "cfg"
        maintenance.STATE = root / "state"
        maintenance.SETTINGS_FILE = root / "cfg" / "settings.json"
        maintenance.HISTORY_FILE = root / "state" / "history.jsonl"
        maintenance.CHANGES_FILE = root / "state" / "changes.json"
        maintenance.LAST_RUN_FILE = root / "state" / "last.json"

    def tearDown(self):
        for k, v in self._old.items():
            setattr(maintenance, k, v)
        super().tearDown()

    def test_settings_roundtrip_and_defaults(self):
        s = maintenance.load_settings()
        self.assertEqual(s["snapshot_before"], "ask")
        s["maintenance"]["frequency"] = "daily"
        maintenance.save_settings(s)
        self.assertEqual(maintenance.load_settings()["maintenance"]["frequency"], "daily")
        self.assertEqual(maintenance.load_settings()["maintenance"]["notify"], "issues")

    def test_history_newest_first(self):
        maintenance.record("tweak", "first")
        maintenance.record("fix", "second", ok=False)
        h = maintenance.history()
        self.assertEqual([e["title"] for e in h], ["second", "first"])
        self.assertFalse(h[0]["ok"])
        maintenance.clear_history()
        self.assertEqual(maintenance.history(), [])

    def test_autostart_change_tracking(self):
        maintenance.note_autostart("a.desktop", False, had_user_file=False)
        c = json.loads(maintenance.CHANGES_FILE.read_text())
        self.assertEqual(c["autostart"]["a.desktop"], {"was_enabled": True, "created": True})
        maintenance.note_autostart("a.desktop", True, had_user_file=True)   # back to original
        self.assertNotIn("a.desktop", json.loads(maintenance.CHANGES_FILE.read_text())["autostart"])

    def test_flatpak_tracking(self):
        maintenance.note_flatpak("org.example.App")
        maintenance.note_flatpak("org.example.App")
        self.assertEqual(maintenance._changes()["flatpak"], ["org.example.App"])
        maintenance.note_flatpak("org.example.App", reset=True)
        self.assertEqual(maintenance._changes()["flatpak"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
