import unittest

from mintcare import report


class BundleLogs(unittest.TestCase):
    def test_addresses_are_removed_from_log_text(self):
        text = "IN=enp4s0 MAC=01:00:5e:00:00:01:60:8d:26:d9:d7:14:08:00 SRC=192.168.1.254 DST=224.0.0.1"
        clean = report._scrub_log(text)
        self.assertNotIn("192.168", clean)
        self.assertNotIn("01:00:5e", clean)
        self.assertIn("[ip]", clean)
        self.assertIn("[mac]", clean)


if __name__ == "__main__":
    unittest.main()
