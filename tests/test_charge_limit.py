"""The battery charge limit: the chosen level is stored, written into the tweak's script, and used to judge it applied."""
import unittest
from pathlib import Path
from unittest import mock

from mintcare import maintenance, tweaks
from tests.helpers import TempDirTest


class ChargeLimit(TempDirTest):
    def setUp(self):
        super().setUp()
        st = maintenance.load_settings()             # the settings file is shared by the tests in this process
        st.pop("charge_limit", None)
        maintenance.save_settings(st)

    def test_default_is_80_and_invalid_values_fall_back(self):
        self.assertEqual(tweaks.charge_limit(), 80)
        tweaks.set_charge_limit(60)
        self.assertEqual(tweaks.charge_limit(), 60)
        tweaks.set_charge_limit(42)                  # below 50 and not a step of 5: keep the default
        self.assertEqual(tweaks.charge_limit(), 80)

    def test_the_script_writes_the_chosen_level(self):
        tweaks.set_charge_limit(60)
        script = tweaks.BY_ID["chargelimit"].apply_script()
        self.assertIn('ATTR{charge_control_end_threshold}="60"', script)
        self.assertIn("echo 60 >", script)
        self.assertNotIn("@LIMIT@", script)

    def test_applied_means_every_battery_is_at_or_below_the_level(self):
        level_file = Path(self.tmp) / "charge_control_end_threshold"
        level_file.write_text("70")
        tweaks.set_charge_limit(60)
        probe = tweaks.BY_ID["chargelimit"].probe
        with mock.patch.object(tweaks, "_charge_files", return_value=[str(level_file)]):
            self.assertFalse(probe())                # 70 is above a 60 limit
            tweaks.set_charge_limit(75)
            self.assertTrue(probe())                 # 70 is within a 75 limit


if __name__ == "__main__":
    unittest.main()
