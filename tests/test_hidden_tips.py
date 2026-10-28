import unittest

from mintcare import maintenance


class HiddenTips(unittest.TestCase):
    def setUp(self):
        maintenance.SETTINGS_FILE.unlink(missing_ok=True)

    def test_hide_and_show_again(self):
        maintenance.set_tip_hidden("swap", True)
        maintenance.set_tip_hidden("swap", True)            # hiding twice keeps one entry
        maintenance.set_tip_hidden("firewall", True)
        self.assertEqual(maintenance.hidden_tips(), ["swap", "firewall"])
        maintenance.set_tip_hidden("swap", False)
        self.assertEqual(maintenance.hidden_tips(), ["firewall"])


if __name__ == "__main__":
    unittest.main()
