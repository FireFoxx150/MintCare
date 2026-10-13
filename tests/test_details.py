"""Clickable check details: every check has a read-only command, and boot errors are grouped/explained."""
import subprocess
import unittest

from mintcare import checks, journal
from tests.helpers import bash_syntax_ok

WRITES = ("rm ", "apt-get install", "apt install", "systemctl enable", "systemctl disable", "systemctl restart",
          " > /", "tee ", "dd ", "mkfs", "chmod", "chown", "sudo", "pkexec")


class DetailCommands(unittest.TestCase):
    def test_every_check_has_a_detail_command(self):
        self.assertEqual({c.id for c in checks.CHECKS}, set(checks.DETAIL_COMMANDS))

    def test_commands_parse_and_are_read_only(self):
        for cid, cmd in checks.DETAIL_COMMANDS.items():
            with self.subTest(check=cid):
                ok, err = bash_syntax_ok(cmd)
                self.assertTrue(ok, err)
                for w in WRITES:
                    self.assertNotIn(w, cmd, f"{cid} detail command looks like it changes something")

    def test_unknown_check_has_no_details(self):
        self.assertIsNone(checks.details("no-such-check"))


class BootErrors(unittest.TestCase):
    ITEMS = [
        {"who": "colord-sane", "msg": "io/hpmud/musb.c 2102: Invalid usb_open: Permission denied", "ts": 1},
        {"who": "colord-sane", "msg": "io/hpmud/musb.c 2102: Invalid usb_open: Permission denied", "ts": 5},
        {"who": "crash: python3", "msg": "Process 1 (python3) of user 1000 dumped core.", "ts": 3},
        {"who": "crash: python3", "msg": "Process 2 (python3) of user 1000 dumped core.", "ts": 4},
        {"who": "kernel", "msg": "blk_update_request: I/O error, dev sda, sector 1234", "ts": 2},
        {"who": "lightdm", "msg": "gkr-pam: couldn't unlock the login keyring.", "ts": 2},
        {"who": "weird", "msg": "something nobody has seen", "ts": 2},
    ]

    def test_grouping_counts_and_order(self):
        g = journal.grouped(self.ITEMS)
        self.assertEqual(g[0]["level"], journal.FAIL)                 # disk I/O error first
        crash = next(x for x in g if x["who"] == "crash: python3")
        self.assertEqual(crash["count"], 2)                          # digits ignored when grouping
        hp = next(x for x in g if x["who"] == "colord-sane")
        self.assertEqual((hp["count"], hp["last"], hp["level"]), (2, 5, journal.OK))
        self.assertEqual(next(x for x in g if x["who"] == "weird")["explanation"], "")

    def test_report_text(self):
        text = journal.report(journal.grouped(self.ITEMS))
        self.assertIn("[PROBLEM] kernel", text)
        self.assertIn("Passwords and Keys", text)
        self.assertEqual(journal.report([]), "No errors have been logged since this boot.")
