"""The System journal view: scope and level go to journalctl, the text filter runs here, and a failed read is reported."""
import json
import unittest
from unittest import mock

from mintcare import journal

LINES = "\n".join([
    json.dumps({"SYSLOG_IDENTIFIER": "cron", "MESSAGE": "job ran", "__REALTIME_TIMESTAMP": "1700000000000000"}),
    json.dumps({"SYSLOG_IDENTIFIER": "kernel", "MESSAGE": [104, 105], "__REALTIME_TIMESTAMP": "1700000060000000"}),
    json.dumps({"SYSLOG_IDENTIFIER": "sshd", "MESSAGE": "Failed password for x", "__REALTIME_TIMESTAMP": "1700000120000000"}),
    "not json at all",
])


class Browse(unittest.TestCase):
    def test_filters_by_text_and_passes_scope_and_level_to_journalctl(self):
        with mock.patch.object(journal, "run", return_value=(0, LINES)) as run:
            rc, rows = journal.browse("day", "3", " SSHD ")
        args = run.call_args[0][0]
        self.assertIn("--since=-1 day", args)
        self.assertEqual(args[args.index("-p") + 1], "3")
        self.assertEqual(rc, 0)
        self.assertEqual([who for _t, who, _m in rows], ["sshd"])

    def test_this_boot_uses_the_boot_scope_and_binary_messages_are_decoded(self):
        with mock.patch.object(journal, "run", return_value=(0, LINES)) as run:
            rc, rows = journal.browse("boot", "7")
        self.assertIn("-b", run.call_args[0][0])
        self.assertIn(("kernel", "hi"), [(who, msg) for _t, who, msg in rows])
        self.assertEqual(len(rows), 3)                 # the line that is not JSON is skipped

    def test_a_failed_read_keeps_its_exit_code_and_shows_nothing(self):
        with mock.patch.object(journal, "run", return_value=(1, "Hint: You are not seeing messages")):
            rc, rows = journal.browse("week", "3")
        self.assertNotEqual(rc, 0)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
