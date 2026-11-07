import unittest

from mintcare import maintenance


class CacheTrend(unittest.TestCase):
    def setUp(self):
        maintenance.CACHE_SIZES_FILE.unlink(missing_ok=True)

    def test_growth_over_the_window(self):
        now = 1_000_000
        maintenance.record_cache_size(100, now=now - 10 * 86400)
        maintenance.record_cache_size(250, now=now - 86400)
        self.assertEqual(maintenance.cache_growth(now=now), (150, 9.0))

    def test_needs_two_readings(self):
        maintenance.record_cache_size(100, now=1_000_000)
        self.assertIsNone(maintenance.cache_growth(now=1_000_000))


if __name__ == "__main__":
    unittest.main()
