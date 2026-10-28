import unittest

from mintcare import packages

POLICY = """htop:
  Installed: 3.2.2-1build1
  Candidate: 3.3.0-1
  Version table:
     3.3.0-1 500
"""


class HoldStatus(unittest.TestCase):
    def test_reads_installed_and_candidate(self):
        self.assertEqual(packages.hold_status(POLICY), ("3.2.2-1build1", "3.3.0-1"))

    def test_missing_fields_are_empty(self):
        self.assertEqual(packages.hold_status("nothing here"), ("", ""))


if __name__ == "__main__":
    unittest.main()
