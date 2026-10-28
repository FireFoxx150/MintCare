import os
import tempfile
import unittest
from pathlib import Path

from mintcare import backup


class VerifyBackup(unittest.TestCase):
    def test_readable_copies_pass_and_an_unreadable_one_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            snap = Path(d, "snap")
            (snap / "docs").mkdir(parents=True)
            (snap / "docs" / "a.txt").write_text("hello")
            (snap / "b.txt").write_text("world")
            checked, bad = backup.verify(snap)
            self.assertEqual((checked, bad), (2, []))
            os.chmod(snap / "b.txt", 0)
            try:
                if os.geteuid() == 0:
                    self.skipTest("root can read files without permission")
                _checked, bad = backup.verify(snap)
                self.assertEqual(bad, [str(snap / "b.txt")])
            finally:
                os.chmod(snap / "b.txt", 0o600)


if __name__ == "__main__":
    unittest.main()
