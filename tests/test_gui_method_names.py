"""Regression test: a second method with the same name in the window class silently replaces the first one,
so every name in MintCareWindow must be defined once. Reads gui.py as text; no display needed."""

import ast
import collections
import os
import unittest

GUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mintcare", "gui.py")


class WindowMethodNames(unittest.TestCase):
    def test_every_method_name_is_defined_once(self):
        tree = ast.parse(open(GUI, encoding="utf-8").read())
        window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MintCareWindow")
        names = [n.name for n in window.body if isinstance(n, ast.FunctionDef)]
        repeated = sorted(k for k, v in collections.Counter(names).items() if v > 1)
        self.assertEqual(repeated, [])


if __name__ == "__main__":
    unittest.main()
