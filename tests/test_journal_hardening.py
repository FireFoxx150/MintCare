"""The journal's bounded reading (Claude Security finding F7): many kinds of error, long lines and a slow boot."""
import io
import json
import threading
import unittest
from unittest import mock

from mintcare import journal


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
        self.assertEqual([type(x) for x in got], [str, journal.LongLine, str])
        self.assertEqual(got[0], "short\n")
        self.assertEqual(got[2], "ok\n")

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


if __name__ == "__main__":
    unittest.main()
