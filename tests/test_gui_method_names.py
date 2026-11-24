"""Regression test: a second method with the same name in the window class (or one of its page mixins)
silently replaces the first one, so every name must be defined once across them. Reads the files as text;
no display needed."""

import ast
import collections
import os
import pathlib
import unittest

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mintcare")
CLASSES = {"gui.py": "MintCareWindow", "gui_space.py": "SpacePage", "gui_clean.py": "CleanPage",
           "gui_backup.py": "BackupPage", "gui_maintenance.py": "MaintenancePage",
           "gui_hardware.py": "HardwarePage", "gui_security.py": "SecurityPage",
           "gui_troubleshoot.py": "TroubleshootPage", "gui_startup.py": "StartupPage",
           "gui_packages.py": "PackagesPage", "gui_processes.py": "ProcessesPage",
           "gui_flatpak.py": "FlatpakPage",
           "gui_software.py": "SoftwarePage"}


class WindowMethodNames(unittest.TestCase):
    def test_every_method_name_is_defined_once(self):
        names = []
        for file, cls in CLASSES.items():
            tree = ast.parse(pathlib.Path(PKG, file).read_text(encoding="utf-8"))
            node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
            names += [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
        repeated = sorted(k for k, v in collections.Counter(names).items() if v > 1)
        self.assertEqual(repeated, [])


if __name__ == "__main__":
    unittest.main()
