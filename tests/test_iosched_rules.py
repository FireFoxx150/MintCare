"""The I/O scheduler rules must reach whole disks only: a partition has no queue of its own, so writing one fails.

Module under test: mintcare/tweaks.py (the "iosched" tweak writes these udev rules). No other tests cover them.
"""
import fnmatch
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from mintcare import tweaks

DEVICES = [("nvme0n1", "disk"), ("nvme0n1p1", "partition"), ("nvme0n1p2", "partition"),
           ("sda", "disk"), ("sda1", "partition"), ("mmcblk0", "disk"), ("mmcblk0p1", "partition")]
PAIR = re.compile(r'([A-Z]+(?:\{[^}]*\})?)\s*(==|=)\s*"([^"]*)"')


def rules():
    """The udev rule lines the iosched tweak writes into its rules file."""
    return [line for line in tweaks.BY_ID["iosched"].apply_script().splitlines() if line.startswith("ACTION==")]


def matches(rule, name, devtype):
    """Whether udev applies this rule's name and device-type conditions to the device.

    Attribute conditions are left out on purpose: a partition has no queue attributes, so the name and
    the device type decide which devices a rule reaches.
    """
    for key, op, value in PAIR.findall(rule):
        if op != "==":
            continue
        if key == "KERNEL" and not any(fnmatch.fnmatchcase(name, pat) for pat in value.split("|")):
            return False
        if key == "ENV{DEVTYPE}" and value != devtype:
            return False
    return True


class IoschedRules(unittest.TestCase):
    def test_partitions_are_never_reached_by_a_rule_that_sets_a_scheduler(self):
        writers = rules()
        self.assertEqual(len(writers), 3)
        for name, devtype in DEVICES:
            if devtype == "partition":
                self.assertFalse(any(matches(r, name, devtype) for r in writers), name)

    def test_every_whole_disk_is_still_reached(self):
        for name, devtype in DEVICES:
            if devtype == "disk":
                self.assertTrue(any(matches(r, name, devtype) for r in rules()), name)


class SchedulerRestore(unittest.TestCase):
    """The revert puts back the scheduler each disk had before the tweak, run against a fake /sys/block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "block"
        self.saved = Path(self.tmp.name) / "schedulers.bak"
        for name, sched, rot in (("sda", "[mq-deadline] kyber bfq none", "1"), ("nvme0n1", "[none] mq-deadline", "0")):
            queue = self.root / name / "queue"
            queue.mkdir(parents=True)
            (queue / "scheduler").write_text(sched + "\n")
            (queue / "rotational").write_text(rot + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def set_scheduler(self, name, text):
        (self.root / name / "queue" / "scheduler").write_text(text + "\n")

    def scheduler(self, name):
        return (self.root / name / "queue" / "scheduler").read_text().strip()

    def run_sh(self, script):
        subprocess.run(["bash", "-c", script], check=True)

    def test_each_disk_gets_back_the_scheduler_it_had(self):
        self.run_sh(tweaks._iosched_save(self.root, self.saved))
        self.set_scheduler("sda", "[bfq] mq-deadline none")        # what the tweak sets
        self.set_scheduler("nvme0n1", "[mq-deadline] none")
        self.run_sh(tweaks._iosched_restore(self.root, self.saved))
        self.assertEqual(self.scheduler("sda"), "mq-deadline")
        self.assertEqual(self.scheduler("nvme0n1"), "none")
        self.assertFalse(self.saved.exists())

    def test_applying_twice_keeps_the_first_list(self):
        self.run_sh(tweaks._iosched_save(self.root, self.saved))
        self.set_scheduler("sda", "[bfq] mq-deadline none")
        self.run_sh(tweaks._iosched_save(self.root, self.saved))  # a second apply must not save the tweaked value
        self.run_sh(tweaks._iosched_restore(self.root, self.saved))
        self.assertEqual(self.scheduler("sda"), "mq-deadline")

    def test_without_a_saved_list_hard_drives_go_back_to_mq_deadline(self):
        self.run_sh(tweaks._iosched_restore(self.root, self.saved))
        self.assertEqual(self.scheduler("sda"), "mq-deadline")
        self.assertEqual(self.scheduler("nvme0n1"), "[none] mq-deadline")  # not a hard drive: left alone


if __name__ == "__main__":
    unittest.main()
