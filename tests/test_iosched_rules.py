"""The I/O scheduler rules must reach whole disks only: a partition has no queue of its own, so writing one fails.

Module under test: mintcare/tweaks.py (the "iosched" tweak writes these udev rules). No other tests cover them.
"""
import fnmatch
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
