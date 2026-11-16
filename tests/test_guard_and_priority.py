"""Tests for the real-time guard (decoy files and download checks) and process priority.
Decoys live in temporary folders, scans are stubbed, and the only process whose priority changes is a
`sleep` started by the test itself. Nothing here runs as root, starts the user service or changes the system."""

import io
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from mintcare import guard, processes


class Decoys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "Documents")
        os.makedirs(self.folder)
        self.state = os.path.join(self.tmp.name, "guard.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_decoys_are_created_and_clean(self):
        guard.make_decoys(self.folder, self.state)
        for name in guard.DECOY_NAMES:
            self.assertTrue(os.path.isfile(os.path.join(self.folder, name)))
        self.assertEqual(guard.decoy_problems(self.state), [])

    def test_a_changed_or_missing_decoy_is_reported(self):
        guard.make_decoys(self.folder, self.state)
        changed, missing = (os.path.join(self.folder, n) for n in guard.DECOY_NAMES[:2])
        with open(changed, "a") as f:
            f.write("encrypted")
        os.remove(missing)
        found = dict(guard.decoy_problems(self.state))
        self.assertEqual(found, {guard.DECOY_NAMES[0]: "changed", guard.DECOY_NAMES[1]: "missing"})

    def test_existing_files_are_not_overwritten(self):
        original = os.path.join(self.folder, guard.DECOY_NAMES[0])
        with open(original, "w") as f:
            f.write("the user's own file")
        guard.make_decoys(self.folder, self.state)
        with open(original) as f:
            self.assertEqual(f.read(), "the user's own file")

    def test_no_problems_without_a_folder(self):
        self.assertEqual(guard.decoy_problems(self.state), [])


class Downloads(unittest.TestCase):
    def test_partial_and_hidden_files_are_skipped(self):
        self.assertTrue(guard.is_download("report.pdf"))
        for name in (".hidden", "video.mp4.part", "setup.crdownload", "file.download", "x.tmp"):
            self.assertFalse(guard.is_download(name), name)

    def test_clamscan_exit_codes(self):
        cases = {0: "clean", 1: "infected", 2: "error"}
        for rc, status in cases.items():
            with mock.patch.object(guard, "run", return_value=(rc, "/home/u/x: Test FOUND")):
                self.assertEqual(guard.scan_file("/home/u/x")[0], status)

    def test_an_infected_download_is_announced_by_name(self):
        announced = []
        with mock.patch.object(guard, "scan_file", return_value=("infected", "x FOUND")):
            guard.check_download("/home/u/Downloads/bad.exe", announced.append)
        self.assertEqual(len(announced), 1)
        self.assertIn("bad.exe", announced[0])

    def test_a_clean_download_is_silent(self):
        announced = []
        with mock.patch.object(guard, "scan_file", return_value=("clean", "")):
            guard.check_download("/home/u/Downloads/ok.pdf", announced.append)
        self.assertEqual(announced, [])

    def test_downloads_cannot_be_switched_on_without_clamscan(self):
        with mock.patch.object(guard, "clamscan_ready", return_value=False):
            with self.assertRaises(RuntimeError):
                guard.set_feature("downloads", True, path=None)

    def test_service_unit_restarts_on_failure(self):
        text = guard.unit_text()
        self.assertIn(" guard", text)
        self.assertIn("Restart=on-failure", text)

    def test_a_burst_of_downloads_is_checked_one_at_a_time(self):
        lock, running, peak, checked = threading.Lock(), [0], [0], []

        def slow_check(path, notify):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.01)
            with lock:
                running[0] -= 1
            checked.append(path)

        with mock.patch.object(guard, "check_download", slow_check):
            scans = guard.start_scanner(lambda text: None)
            for i in range(20):
                scans.put(f"/home/u/Downloads/file{i}.zip")
            scans.join()
        self.assertEqual(len(checked), 20)
        self.assertEqual(peak[0], 1)

    def test_a_failed_check_does_not_stop_the_rest(self):
        checked = []

        def check(path, notify):
            if path.endswith("bad.zip"):
                raise OSError("boom")
            checked.append(path)

        with mock.patch.object(guard, "check_download", check), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            scans = guard.start_scanner(lambda text: None)
            scans.put("/home/u/Downloads/bad.zip")
            scans.put("/home/u/Downloads/ok.zip")
            scans.join()
        self.assertEqual(checked, ["/home/u/Downloads/ok.zip"])


class Priority(unittest.TestCase):
    def test_own_process_reports_a_nice_value(self):
        self.assertIsInstance(processes.nice_of(os.getpid()), int)

    def test_lowering_then_checking_a_child_process(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            ok, message = processes.lower_priority(child.pid, "sleep")
            self.assertTrue(ok, message)
            self.assertEqual(processes.nice_of(child.pid), 10)
        finally:
            child.kill()
            child.wait()

    def test_a_pid_that_now_runs_another_program_is_left_alone(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            before = processes.nice_of(child.pid)
            ok, message = processes.lower_priority(child.pid, "firefox")
            self.assertFalse(ok)
            self.assertIn("firefox", message)
            self.assertEqual(processes.nice_of(child.pid), before)
        finally:
            child.kill()
            child.wait()

    def test_restoring_runs_as_root_with_numbers_only(self):
        step = processes.restore_step("4242", "firefox", nice=-3)
        self.assertEqual(step[0], "restore4242")
        self.assertIn("renice -n -3 -p 4242", step[2])


class ProcessInfo(unittest.TestCase):
    def test_the_package_lookup_runs_once_per_program(self):
        processes._owner.cache_clear()
        calls = []

        def fake_out(cmd, timeout=20):
            calls.append(cmd[0])
            return "coreutils: /usr/bin/ls" if cmd[0] == "dpkg" else "GNU ls"

        with mock.patch.object(processes, "out", side_effect=fake_out), \
                mock.patch.object(processes.os, "readlink", return_value="/usr/bin/ls"):
            for _ in range(3):
                info = processes.describe({"pid": 1234, "name": "ls", "kernel": False})
        self.assertEqual(calls.count("dpkg"), 1)
        self.assertEqual((info["package"], info["summary"]), ("coreutils", "GNU ls"))


if __name__ == "__main__":
    unittest.main()
