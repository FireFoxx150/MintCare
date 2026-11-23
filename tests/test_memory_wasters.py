import unittest
from unittest import mock

from mintcare import processes

MB = 2 ** 20
HOUR = 3600


def row(pid, name, rss_mb, age=2 * HOUR, cpu=0.0, cpu_time=1.0, mine=True, cmd=None, ppid=1000, swap_mb=0):
    return {"pid": pid, "ppid": ppid, "name": name, "user": "me", "uid": 1000, "cpu": cpu, "rss": rss_mb * MB,
            "swap": swap_mb * MB, "age": age, "cpu_time": cpu_time, "cmd": cmd or f"/usr/bin/{name}",
            "mine": mine, "kernel": False}


def pids(found):
    return [r["pid"] for r, _why in found]


class MemoryWasters(unittest.TestCase):
    """Which of your programs the scan offers to end, worked out from rows alone (no real processes)."""

    def test_big_idle_program_is_listed_and_busy_or_small_ones_are_not(self):
        rows = [row(1, "big-idle", 500), row(2, "small-idle", 50), row(3, "busy-now", 500, cpu=4.0),
                row(4, "busy-on-average", 500, cpu_time=0.05 * 2 * HOUR), row(5, "young", 500, age=600)]
        self.assertEqual(pids(processes.memory_wasters(rows)), [1])

    def test_swap_counts_towards_memory(self):
        self.assertEqual(pids(processes.memory_wasters([row(1, "app", 10, swap_mb=300)])), [1])

    def test_never_lists_other_users_session_kernel_or_protected(self):
        rows = [row(1, "theirs", 900, mine=False), row(2, "cinnamon", 900), row(3, "csd-power", 900),
                row(4, "Xorg", 900), row(5, "parent-shell", 900), row(6, "kthread", 900)]
        rows[-1]["kernel"] = True
        self.assertEqual(processes.memory_wasters(rows, protected={5}), [])

    def test_older_duplicate_is_listed_and_newest_copy_is_kept(self):
        rows = [row(1, "editor", 80, age=5 * HOUR), row(2, "editor", 80, age=HOUR)]
        self.assertEqual(pids(processes.memory_wasters(rows)), [1])

    def test_workers_started_by_the_same_program_are_not_duplicates(self):
        rows = [row(1, "browser", 80, age=5 * HOUR, cmd="/opt/b --type=renderer"),
                row(2, "browser", 80, age=3 * HOUR, cmd="/opt/b --type=renderer", ppid=9),
                row(9, "browser", 120, age=6 * HOUR, cmd="/opt/b", cpu=2.0)]
        rows[0]["ppid"] = 9
        self.assertEqual(processes.memory_wasters(rows), [])

    def test_most_memory_first(self):
        rows = [row(1, "a", 300), row(2, "b", 900), row(3, "c", 600)]
        self.assertEqual(pids(processes.memory_wasters(rows)), [2, 3, 1])


class Protected(unittest.TestCase):
    def test_walks_up_to_init(self):
        stats = {"/proc/50/stat": "50 (mintcare) S 40 0", "/proc/40/stat": "40 (bash) S 30 0",
                 "/proc/30/stat": "30 (gnome-terminal) S 1 0"}
        with mock.patch.object(processes, "read", side_effect=lambda p: stats.get(p, "")):
            self.assertEqual(processes.protected_pids(50), {50, 40, 30})


class Ending(unittest.TestCase):
    def test_ends_only_when_the_pid_still_has_the_same_name(self):
        with mock.patch.object(processes, "read", return_value="other"), \
                mock.patch.object(processes.os, "kill") as kill:
            self.assertEqual(processes.end_checked(123, "editor"), "gone")
            kill.assert_not_called()
        with mock.patch.object(processes, "read", return_value="editor"), \
                mock.patch.object(processes.os, "kill") as kill:
            self.assertEqual(processes.end_checked(123, "editor"), "ended")
            kill.assert_called_once_with(123, processes.signal.SIGTERM)

    def test_force_uses_sigkill_and_errors_are_reported(self):
        with mock.patch.object(processes, "read", return_value="editor"), \
                mock.patch.object(processes.os, "kill") as kill:
            processes.end_checked(5, "editor", force=True)
            kill.assert_called_once_with(5, processes.signal.SIGKILL)
        for exc, want in ((ProcessLookupError, "gone"), (PermissionError, "denied")):
            with mock.patch.object(processes, "read", return_value="editor"), \
                    mock.patch.object(processes.os, "kill", side_effect=exc):
                self.assertEqual(processes.end_checked(5, "editor"), want)

    def test_still_running_compares_names(self):
        names = {"/proc/1/comm": "editor", "/proc/2/comm": "", "/proc/3/comm": "reused"}
        with mock.patch.object(processes, "read", side_effect=lambda p: names.get(p, "")):
            self.assertEqual(processes.still_running([(1, "editor"), (2, "gone"), (3, "editor")]), [(1, "editor")])


if __name__ == "__main__":
    unittest.main()
