"""Tests for the upkeep features: long SMART test, battery trend, Wi-Fi check, maintenance tasks,
recent-files and broken-link cleaners, checksum comparison, daily/weekly/monthly retention, file types and
threshold alerts. Nothing here runs as root or touches the real system: folders are temporary and every
command that could change something is only built, not run."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mintcare import alerts, backup, checks, cleaner, hardware, space, troubleshoot


BASE = 1.79e9                                    # a 2026 timestamp, so dates in the text read sensibly


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class SelfTestKinds(unittest.TestCase):
    def test_short_is_the_default(self):
        steps = hardware.selftest_steps([{"device": "/dev/sdb", "model": "M"}])
        self.assertEqual(steps[0][2], "smartctl -t short /dev/sdb")

    def test_long_test(self):
        steps = hardware.selftest_steps([{"device": "/dev/sda", "model": "M"}], "long")
        self.assertEqual(steps[0][2], "smartctl -t long /dev/sda")

    def test_device_names_are_quoted(self):
        steps = hardware.selftest_steps([{"device": "/dev/sd a"}], "long")
        self.assertEqual(steps[0][2], "smartctl -t long '/dev/sd a'")


class BatteryHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "battery-history.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def track(self, day, health, path=None):
        hardware.battery_track([{"name": "BAT0", "health": health}], path or self.path, now=BASE + day * 86400)

    def test_one_reading_per_day(self):
        self.track(0, 92)
        hardware.battery_track([{"name": "BAT0", "health": 91}], self.path, now=BASE + 100)
        self.track(1, 90)
        with open(self.path) as f:
            self.assertEqual(len(json.load(f)["BAT0"]), 2)

    def test_trend_after_a_week_with_a_drop(self):
        self.track(0, 92)
        self.track(8, 88)
        text = hardware.battery_trend("BAT0", self.path)
        self.assertIn("92", text)
        self.assertIn("88", text)

    def test_no_trend_when_the_history_is_recent(self):
        self.track(0, 92)
        self.track(3, 88)
        self.assertEqual(hardware.battery_trend("BAT0", self.path), "")

    def test_no_trend_when_the_health_is_flat(self):
        flat = os.path.join(self.dir, "flat.json")
        self.track(0, 92, flat)
        self.track(10, 92, flat)
        self.assertEqual(hardware.battery_trend("BAT0", flat), "")


class WifiSecurity(unittest.TestCase):
    def test_protected_networks_pass(self):
        self.assertEqual(checks.wifi_security("yes:WPA2 WPA3")[0], checks.OK)
        self.assertEqual(checks.wifi_security("yes:WPA1 WPA2")[0], checks.OK)

    def test_open_network_warns(self):
        self.assertEqual(checks.wifi_security("yes:")[0], checks.WARN)
        self.assertEqual(checks.wifi_security("yes:--")[0], checks.WARN)

    def test_wep_and_wpa1_warn(self):
        self.assertEqual(checks.wifi_security("yes:WEP")[0], checks.WARN)
        self.assertEqual(checks.wifi_security("yes:WPA1")[0], checks.WARN)

    def test_not_on_wifi_is_fine(self):
        self.assertEqual(checks.wifi_security("no:WEP\n")[0], checks.OK)


class Launchers(unittest.TestCase):
    def test_missing_program_is_broken(self):
        self.assertTrue(cleaner.desktop_missing("[Desktop Entry]\nExec=/no/such/program-xyz %U\n"))

    def test_program_on_path_is_fine(self):
        self.assertFalse(cleaner.desktop_missing("[Desktop Entry]\nExec=sh -c true\n"))

    def test_env_prefix_is_skipped(self):
        self.assertFalse(cleaner.desktop_missing("Exec=env FOO=1 sh\n"))

    def test_tryexec_is_checked_first(self):
        self.assertTrue(cleaner.desktop_missing("TryExec=/no/such/tool\nExec=sh\n"))

    def test_no_exec_is_not_broken(self):
        self.assertFalse(cleaner.desktop_missing("[Desktop Entry]\nName=x\n"))


class BrokenLinks(unittest.TestCase):
    def test_finds_dangling_links_only(self):
        with tempfile.TemporaryDirectory() as home:
            real = os.path.join(home, "real.txt")
            write(real)
            os.symlink(os.path.join(home, "gone"), os.path.join(home, "dead"))
            os.symlink(real, os.path.join(home, "alive"))
            os.makedirs(os.path.join(home, ".hidden"))
            os.symlink("/no/such/file", os.path.join(home, ".hidden", "skipped"))
            found = sorted(os.path.basename(p) for p in cleaner.broken_links(home))
            self.assertEqual(found, ["dead"])

    def test_search_stops_at_the_depth_limit(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "1", "2", "3"))
            os.symlink("/no/such/file", os.path.join(home, "1", "2", "shallow"))
            os.symlink("/no/such/file", os.path.join(home, "1", "2", "3", "too-deep"))
            found = [os.path.basename(p) for p in cleaner.broken_links(home)]
            self.assertEqual(found, ["shallow"])

    def test_broken_launcher_files(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "old.desktop"), b"[Desktop Entry]\nExec=/no/such/app\n")
            write(os.path.join(d, "fine.desktop"), b"[Desktop Entry]\nExec=sh\n")
            self.assertEqual([os.path.basename(p) for p in cleaner.broken_launchers([d])], ["old.desktop"])


class RecentList(unittest.TestCase):
    def test_counts_recent_entries(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "recently-used.xbel")
            write(f, b'<xbel><bookmark href="a"/><bookmark href="b"/></xbel>')
            with mock.patch.object(cleaner, "RECENT_FILE", Path(f)):
                size, detail = cleaner.s_recent()
            self.assertEqual(size, os.path.getsize(f))
            self.assertIn("2", detail)

    def test_nothing_to_clean(self):
        with mock.patch.object(cleaner, "RECENT_FILE", Path("/no/such/recently-used.xbel")):
            self.assertEqual(cleaner.s_recent()[0], 0)

    def test_new_items_are_off_by_default(self):
        self.assertFalse(cleaner.BY_ID["recent"].default)
        self.assertFalse(cleaner.BY_ID["broken"].default)


class UpkeepTasks(unittest.TestCase):
    def test_only_tasks_whose_tool_exists_are_offered(self):
        with mock.patch.object(troubleshoot, "has_cmd", lambda name: name == "fc-cache"):
            self.assertEqual([t[0] for t in troubleshoot.upkeep_tasks()], ["fonts"])

    def test_user_tasks_never_use_root(self):
        for tid, _title, _why, runs, command, _tool in troubleshoot.UPKEEP:
            if runs == "user":
                self.assertNotRegex(command, r"\b(sudo|pkexec|su)\b", tid)

    def test_index_update_is_a_root_task(self):
        self.assertEqual({t[0]: t[3] for t in troubleshoot.UPKEEP}["locate"], "root")


class RetentionPlan(unittest.TestCase):
    def test_keeps_newest_per_day_week_and_month(self):
        names = ["2026-11-15_10-30", "2026-11-15_09-00", "2026-11-14_10-00", "2026-11-08_10-00",
                 "2026-10-01_10-00", "2025-11-01_10-00"]
        keep = backup.keep_plan(names, daily=2, weekly=2, monthly=2)
        self.assertEqual(keep, {"2026-11-15_10-30", "2026-11-14_10-00", "2026-11-08_10-00", "2026-10-01_10-00"})

    def test_names_that_are_not_snapshots_are_not_kept(self):
        self.assertEqual(backup.keep_plan(["not-a-snapshot"]), set())

    def test_daily_limit_applies_to_days_with_backups(self):
        names = [f"2026-11-{d:02d}_10-00" for d in range(1, 10)]
        self.assertEqual(len(backup.keep_plan(names, daily=7, weekly=0, monthly=0)), 7)


class ChecksumComparison(unittest.TestCase):
    def test_differing_files_come_from_itemised_lines(self):
        listing = ">f+++++++++ Docs/new.txt\n>f.st...... Docs/changed.txt\ncd+++++++++ Docs/\n"
        self.assertEqual(backup.differing_files(listing), ["Docs/new.txt", "Docs/changed.txt"])

    @unittest.skipUnless(shutil.which("rsync"), "needs rsync")
    def test_changed_and_missing_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "Docs")
            copy_root = os.path.join(tmp, "snap")
            copy = os.path.join(copy_root, backup._key(src))
            write(os.path.join(src, "same.txt"), b"a")
            write(os.path.join(copy, "same.txt"), b"a")
            write(os.path.join(src, "changed.txt"), b"new")
            write(os.path.join(copy, "changed.txt"), b"old")
            write(os.path.join(src, "missing.txt"), b"m")
            checked, differ = backup.compare_with_originals(copy_root, [src])
            self.assertEqual(sorted(os.path.basename(p) for p in differ), ["changed.txt", "missing.txt"])
            self.assertGreaterEqual(checked, 3)


class FileTypes(unittest.TestCase):
    def test_totals_by_kind_skip_hidden_folders(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "clip.mp4"), b"x" * 1000)
            write(os.path.join(d, "photo.jpg"), b"x" * 300)
            write(os.path.join(d, "bundle.zip"), b"x" * 200)
            write(os.path.join(d, "notes.txt"), b"x" * 10)
            write(os.path.join(d, ".hidden", "skipped.mp4"), b"x" * 5000)
            totals = dict(space.file_types(d))
            self.assertEqual(totals, {"video": 1000, "image": 300, "archive": 200, "other": 10})


class ThresholdAlerts(unittest.TestCase):
    def test_full_disk(self):
        now = alerts.conditions(disks=[("System drive", 91), ("Home drive", 40)], bats=[], temps=[])
        self.assertEqual(list(now), ["disk:System drive"])

    def test_low_battery_only_while_discharging(self):
        low = {"capacity": 12, "status": "Discharging"}
        self.assertIn("battery", alerts.conditions(disks=[], bats=[low], temps=[]))
        charging = dict(low, status="Charging")
        self.assertEqual(alerts.conditions(disks=[], bats=[charging], temps=[]), {})

    def test_hot_processor(self):
        hot = [{"label": "CPU", "temp": 96.0, "high": 90.0, "crit": 100.0, "group": "cpu"}]
        cool = [dict(hot[0], temp=50.0)]
        self.assertIn("temp:CPU", alerts.conditions(disks=[], bats=[], temps=hot))
        self.assertEqual(alerts.conditions(disks=[], bats=[], temps=cool), {})

    def test_an_alert_is_announced_once_until_it_clears(self):
        send, keep = alerts.changes({"battery"}, {"battery": "low", "disk:/": "full"})
        self.assertEqual(send, [("disk:/", "full")])
        self.assertEqual(keep, {"battery", "disk:/"})
        send, keep = alerts.changes(keep, {})
        self.assertEqual((send, keep), ([], set()))

    def test_timer_runs_the_alerts_command(self):
        service, timer = alerts.unit_files()
        self.assertIn(" alerts", service)
        self.assertIn("OnUnitActiveSec=15min", timer)


if __name__ == "__main__":
    unittest.main()
