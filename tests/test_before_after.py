import unittest

from mintcare import checks
from mintcare.checks import FAIL, INFO, OK, WARN, Result


class BeforeAfter(unittest.TestCase):
    def test_fixed_still_open_and_new(self):
        before = {"a": Result(WARN, ""), "b": Result(FAIL, ""), "c": Result(OK, ""), "d": Result(WARN, "")}
        after = {"a": Result(OK, ""), "b": Result(WARN, ""), "c": Result(FAIL, ""), "d": Result(INFO, "")}
        titles = {"a": "Alpha", "b": "Bravo", "c": "Charlie", "d": "Delta"}
        fixed, still, new = checks.compare(before, after, titles)
        self.assertEqual(fixed, ["Alpha", "Delta"])
        self.assertEqual(still, ["Bravo"])
        self.assertEqual(new, ["Charlie"])

    def test_checks_missing_after_are_ignored(self):
        before = {"a": Result(WARN, "")}
        self.assertEqual(checks.compare(before, {}, {"a": "Alpha"}), ([], [], []))


if __name__ == "__main__":
    unittest.main()
