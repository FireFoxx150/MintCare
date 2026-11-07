import unittest

from mintcare import checks, report
from mintcare.checks import FAIL, INFO, OK, WARN, Result


class HealthScore(unittest.TestCase):
    def test_tips_do_not_lower_the_score(self):
        self.assertEqual(report.score([Result(INFO, "")] * 5 + [Result(OK, "")])[0], 100)

    def test_problems_and_warnings_do(self):
        self.assertEqual(report.score([Result(FAIL, ""), Result(WARN, ""), Result(INFO, "")])[0], 80)

    def test_never_below_zero(self):
        self.assertEqual(report.score([Result(FAIL, "")] * 10)[0], 0)


if __name__ == "__main__":
    unittest.main()
