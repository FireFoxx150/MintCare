import unittest

from mintcare import powerprof

LIST = """* performance:
    CpuDriver:\tamd_pstate
    Degraded:   no

  balanced:
    CpuDriver:\tamd_pstate
    PlatformDriver:\tplaceholder

  power-saver:
    CpuDriver:\tamd_pstate
    PlatformDriver:\tplaceholder
"""


class PowerProfiles(unittest.TestCase):
    """The profiles come from the daemon's own list; the detail lines under each profile are not profiles."""

    def test_profile_names_come_from_the_list(self):
        self.assertEqual(powerprof.parse_profiles(LIST), ["performance", "balanced", "power-saver"])

    def test_no_output_gives_no_profiles(self):
        self.assertEqual(powerprof.parse_profiles(""), [])


if __name__ == "__main__":
    unittest.main()
