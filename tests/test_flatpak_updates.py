import unittest
from unittest import mock

from mintcare import checks, flatpaks, maintenance


class FlatpakUpdates(unittest.TestCase):
    def setUp(self):
        checks._FLATPAK_CACHE.update(at=0.0, n=None)

    def run_check(self, count):
        with mock.patch.object(checks, "has_cmd", return_value=True), \
                mock.patch.object(maintenance, "load_settings", return_value={"check_updates": True}), \
                mock.patch.object(flatpaks, "updates_available", return_value=count) as ask:
            return checks.c_flatpak_updates(), ask

    def test_no_updates_is_ok(self):
        r, _ask = self.run_check(0)
        self.assertEqual(r.status, checks.OK)

    def test_updates_are_a_warning_that_opens_the_flatpak_page(self):
        r, _ask = self.run_check(3)
        self.assertEqual(r.status, checks.WARN)
        self.assertIn("3", r.summary)
        self.assertEqual((r.fix.kind, r.fix.payload), ("page", "flatpak"))

    def test_second_scan_uses_the_cached_count(self):
        self.run_check(2)
        r, ask = self.run_check(5)
        self.assertEqual(ask.call_count, 0)
        self.assertIn("2", r.summary)

    def test_a_rescan_asks_again_after_an_update(self):
        self.run_check(2)                                # the Flatpaks were updated after this count
        checks.forget_flatpak_count()                    # what a Rescan or a finished Flatpak update does
        r, ask = self.run_check(0)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(r.status, checks.OK)


if __name__ == "__main__":
    unittest.main()
