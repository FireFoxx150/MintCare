"""The fixes for Claude Security findings F7, F8, F10 and F11."""
import io
import json
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

from mintcare import backup, journal, maintenance, secaudit, space
from tests.helpers import TempDirTest


def _word(i):
    """A distinct word for each i, so message shapes differ (digits are blanked out when grouping)."""
    return chr(97 + i // 26) + chr(97 + i % 26)


class BoundedBootErrors(unittest.TestCase):  # F7
    def test_kinds_past_the_cap_share_one_heading_that_keeps_the_worst_level(self):
        groups = journal._Groups()
        for i in range(journal.MAX_GROUPS + 5):
            groups.add("app", f"problem {_word(i)} happened", 1.0)
        groups.add("disk", "I/O error on sda", 2.0)          # a serious kind, past the cap
        out = groups.finish()
        self.assertEqual(len(out), journal.MAX_GROUPS + 1)
        self.assertEqual(sum(g["count"] for g in out), journal.MAX_GROUPS + 6)   # nothing is dropped from the count
        self.assertTrue(any(g["count"] == 6 and g["level"] == journal.FAIL for g in out))

    def test_a_line_past_the_limit_is_counted_not_kept(self):
        with mock.patch.object(journal, "MAX_LINE", 10):
            got = list(journal._lines(io.StringIO("short\n" + "x" * 25 + "\nok\n")))
        self.assertEqual(got, ["short\n", None, "ok\n"])

    def test_a_boot_that_takes_too_long_says_so(self):
        class Stream:
            def __init__(self, stop):
                self.stop, self.sent = stop, False

            def readline(self, _n=-1):
                if not self.sent:
                    self.sent = True
                    return json.dumps({"SYSLOG_IDENTIFIER": "app", "MESSAGE": "hello",
                                       "__REALTIME_TIMESTAMP": "1000000"}) + "\n"
                self.stop.wait(5)
                return ""

            def close(self):
                pass

        class Proc:
            def __init__(self, *args, **kwargs):
                self.stop = threading.Event()
                self.stdout = Stream(self.stop)

            def kill(self):
                self.stop.set()

            def wait(self):
                return 0

        with mock.patch.object(journal, "BOOT_TIMEOUT", 0.05), mock.patch.object(journal.subprocess, "Popen", Proc):
            result = journal.grouped()
        self.assertTrue(any(g["who"] == "MintCare" for g in result))


class StreamedLoginCounts(unittest.TestCase):  # F8
    def _counts(self, stdout):
        class Proc:
            def __init__(self, *args, **kwargs):
                self.stdout = stdout

            def kill(self):
                pass

            def wait(self):
                return 0

        with mock.patch.object(secaudit.subprocess, "Popen", Proc):
            return secaudit.failed_logins()

    def test_failures_are_counted_by_service(self):
        lines = io.StringIO("pam_unix(sudo:auth): authentication failure\n"
                            "Failed password for root from 10.0.0.1\n"
                            "unrelated\n")
        self.assertEqual(self._counts(lines), {"sudo": 1, "ssh": 1})

    def test_the_number_of_services_kept_is_capped(self):
        lines = io.StringIO("".join(f"pam_unix(svc{_word(i)}:auth): authentication failure\n"
                                    for i in range(secaudit.FAILED_LOGIN_SERVICES + 5)))
        counts = self._counts(lines)
        self.assertEqual(len(counts), secaudit.FAILED_LOGIN_SERVICES + 1)
        self.assertEqual(counts["other"], 5)
        self.assertEqual(sum(counts.values()), secaudit.FAILED_LOGIN_SERVICES + 5)

    def test_a_read_that_runs_past_the_limit_gives_no_result(self):
        class Stream:
            def __init__(self, stop):
                self.stop = stop

            def __iter__(self):
                yield "pam_unix(sudo:auth): authentication failure\n"
                self.stop.wait(5)

            def close(self):
                pass

        class Proc:
            def __init__(self, *args, **kwargs):
                self.stop = threading.Event()
                self.stdout = Stream(self.stop)

            def kill(self):
                self.stop.set()

            def wait(self):
                return 0

        with mock.patch.object(secaudit, "LOG_TIMEOUT", 0.05), mock.patch.object(secaudit.subprocess, "Popen", Proc):
            self.assertEqual(secaudit.failed_logins(), {})


class IterativeWalk(TempDirTest):  # F10
    def test_the_walk_finds_what_os_walk_finds(self):
        for rel in ("a/b/c", "a/d", "e"):
            self.write(f"{rel}/file.txt", b"x")
        mine = sorted((p, sorted(d), sorted(f)) for p, d, f in space._walk(self.tmp))
        theirs = sorted((p, sorted(d), sorted(f)) for p, d, f in os.walk(self.tmp))
        self.assertEqual(mine, theirs)

    def test_a_tree_nested_past_the_limit_is_refused_not_undercounted(self):
        os.makedirs(os.path.join(self.tmp, *["d"] * 5))
        with mock.patch.object(space, "MAX_DEPTH", 2), self.assertRaises(OSError):
            space.duplicates([self.tmp], min_size=0)


class RepositoryOutOfTheCommandLine(unittest.TestCase):  # F11
    def test_restic_gets_the_repository_from_its_environment(self):
        repo = "sftp:user:secret@host:/backup"
        with mock.patch.object(backup, "run", return_value=(0, "")) as run_mock:
            backup._restic(["snapshots"], repo)
        cmd = run_mock.call_args.args[0]
        self.assertNotIn("--repo", cmd)
        self.assertNotIn(repo, cmd)
        self.assertEqual(run_mock.call_args.kwargs["env_extra"], {"RESTIC_REPOSITORY": repo})


class PrivateSettings(TempDirTest):  # F11
    def setUp(self):
        super().setUp()
        self.patches = [mock.patch.object(maintenance, "CONFIG_DIR", Path(self.tmp)),
                        mock.patch.object(maintenance, "SETTINGS_FILE", Path(self.tmp) / "settings.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def test_settings_are_saved_readable_by_their_owner_only(self):
        maintenance.save_settings({"x": 1})
        self.assertEqual(os.stat(maintenance.SETTINGS_FILE).st_mode & 0o777, 0o600)

    def test_a_file_written_before_is_made_private_when_loaded(self):
        maintenance.SETTINGS_FILE.write_text('{"x": 1}')
        os.chmod(maintenance.SETTINGS_FILE, 0o644)
        maintenance.load_settings()
        self.assertEqual(os.stat(maintenance.SETTINGS_FILE).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
