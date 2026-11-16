"""Paths with spaces or odd characters reach the timers, the tray entry, the restart and the mirror rewrite
as one argument each. Timer files go to a temporary folder and every command runs on temporary files, so
nothing is installed, started or changed as root."""
import re
import shlex
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from mintcare import autostart, backup, maintenance, packages
from tests.helpers import TempDirTest

APP = ["/home/u/My Projects/mintcare.py"]


def exec_words(value):
    """The arguments an Exec= value stands for, read the way the Desktop Entry spec says: each double-quoted
    word, with the backslash before ", `, $ and \\ removed."""
    return [re.sub(r"\\(.)", r"\1", word) for word in re.findall(r'"((?:[^"\\]|\\.)*)"', value)]


class Timers(TempDirTest):
    def exec_start(self, unit):
        start = [line for line in unit.read_text().splitlines() if line.startswith("ExecStart=")][0]
        return shlex.split(start[len("ExecStart="):])

    def test_check_up_timer_runs_the_app_path_as_one_argument(self):
        units = Path(self.tmp) / "units"
        with mock.patch.object(maintenance, "launcher", return_value=APP), \
                mock.patch.object(maintenance, "UNIT_DIR", units), \
                mock.patch.object(maintenance, "run", return_value=(0, "")):
            maintenance.install_timer("weekly")
        self.assertEqual(self.exec_start(units / maintenance.SERVICE), APP + ["maintain"])

    def test_backup_timer_runs_the_app_path_as_one_argument(self):
        units = Path(self.tmp) / "units"
        with mock.patch.object(maintenance, "launcher", return_value=APP), \
                mock.patch.object(maintenance, "UNIT_DIR", units), \
                mock.patch.object(backup, "run", return_value=(0, "")):
            backup.install_timer("weekly")
        self.assertEqual(self.exec_start(units / backup.SERVICE), APP + ["backup"])


class TrayEntry(unittest.TestCase):
    def test_each_argument_comes_back_unchanged(self):
        argv = ['/home/u/My $Apps/mint"care.py', "--tray"]
        self.assertEqual(exec_words(autostart.desktop_exec(argv)), argv)

    def test_a_percent_sign_is_escaped_for_the_desktop_entry(self):
        self.assertEqual(autostart.desktop_exec(["/opt/100%/mintcare.py"]), '"/opt/100%%/mintcare.py"')


class Restart(TempDirTest):
    def test_the_restart_passes_the_app_path_as_one_argument(self):
        with mock.patch.object(maintenance, "launcher", return_value=["/bin/echo", "two words"]):
            done = subprocess.run(maintenance.relaunch_command(), capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout, "two words\n")


class MirrorRewrite(TempDirTest):
    def test_a_mirror_address_with_shell_characters_is_written_as_given(self):
        sources = Path(self.tmp) / "official-package-repositories.list"
        sources.write_text("deb http://old.example/mint noble main upstream\n")
        new = "https://m'i&r|r.example/mint"
        subprocess.run(["bash", "-c", packages._replace_in_file(sources, "http://old.example/mint", new)], check=True)
        self.assertEqual(sources.read_text(), f"deb {new} noble main upstream\n")


if __name__ == "__main__":
    unittest.main()
