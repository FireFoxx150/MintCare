import unittest

from mintcare import maintenance


class DriveTemps(unittest.TestCase):
    def setUp(self):
        maintenance.DRIVE_TEMPS_FILE.unlink(missing_ok=True)

    def test_range_over_recent_readings_and_one_reading_per_ten_minutes(self):
        now = 1_000_000
        maintenance.record_drive_temp("/dev/sda", 40.0, now=now)
        maintenance.record_drive_temp("/dev/sda", 41.0, now=now + 60)       # too soon, skipped
        maintenance.record_drive_temp("/dev/sda", 47.5, now=now + 700)
        old = maintenance.json.loads(maintenance.DRIVE_TEMPS_FILE.read_text())
        old["/dev/sda"].insert(0, [now - 8 * 86400, 33.0])                  # older than a week
        maintenance.DRIVE_TEMPS_FILE.write_text(maintenance.json.dumps(old))
        self.assertEqual(maintenance.drive_temp_range("/dev/sda", now=now + 800), (40.0, 47.5, 2))

    def test_unknown_drive_has_no_range(self):
        self.assertIsNone(maintenance.drive_temp_range("/dev/nothing"))


if __name__ == "__main__":
    unittest.main()
