import sys
import tempfile
import unittest
from pathlib import Path

from mintcare import assistant


class SdkPath(unittest.TestCase):
    def test_venv_packages_win_over_system_ones(self):
        # Mint's own typing_extensions/httpx are older than the SDK needs; the venv must be searched first
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d, "lib", "python3.12", "site-packages")
            sp.mkdir(parents=True)
            (sp / "anthropic.py").write_text("__version__ = 'stub'\n")
            old_venv, old_path = assistant.VENV, list(sys.path)
            assistant.VENV = Path(d)
            sys.modules.pop("anthropic", None)
            try:
                self.assertEqual(assistant._load_sdk().__version__, "stub")
                self.assertEqual(sys.path[0], str(sp))
            finally:
                assistant.VENV = old_venv
                sys.path[:] = old_path
                sys.modules.pop("anthropic", None)


if __name__ == "__main__":
    unittest.main()
