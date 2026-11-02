import time
import unittest
from unittest import mock

from mintcare import backup, checks


class BackupCheck(unittest.TestCase):
    """The 'Personal file backup' check follows the automatic backup switch, as the Backup page does."""

    def check(self, timer_next, days_ago):
        last = None if days_ago is None else {"ts": time.time() - days_ago * 86400}
        with mock.patch.object(backup, "config", return_value={"dest": "/mnt/backup", "frequency": "weekly"}), \
                mock.patch.object(backup, "check_destination", return_value=[]), \
                mock.patch.object(backup, "last_backup", return_value=last), \
                mock.patch.object(backup, "timer_next", return_value=timer_next):
            return checks.c_backup()

    def test_automatic_backup_off_is_information_not_a_warning(self):
        self.assertEqual(self.check(timer_next=None, days_ago=40).status, checks.INFO)

    def test_an_old_backup_with_automatic_backup_on_is_still_a_warning(self):
        self.assertEqual(self.check(timer_next="Sun 2026-11-08", days_ago=10).status, checks.WARN)

    def test_a_recent_backup_with_automatic_backup_on_is_ok(self):
        self.assertEqual(self.check(timer_next="Sun 2026-11-08", days_ago=2).status, checks.OK)


if __name__ == "__main__":
    unittest.main()
