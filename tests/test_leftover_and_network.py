"""Tests for leftover app data (Clean page), devices on the network and connections per program.
Only temporary folders and sample command output are used; nothing here runs as root or changes the system."""

import json
import os
import tempfile
import unittest

from mintcare import cleaner, lan, ports


class LeftoverFolders(unittest.TestCase):
    def test_folders_named_after_removed_packages_are_found(self):
        with tempfile.TemporaryDirectory() as base:
            for name in ("GNOME Boxes", "gimp", "mintcare", ".hidden", "other"):
                os.makedirs(os.path.join(base, name))
            open(os.path.join(base, "gnome-boxes-file"), "w").close()
            found = cleaner.leftover_dirs({"gnome-boxes", "gimp", "mintcare"}, {"gimp"}, bases=[base])
            self.assertEqual([os.path.basename(p) for p in found], ["GNOME Boxes"])

    def test_nothing_found_when_no_folder_matches(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "unrelated"))
            self.assertEqual(cleaner.leftover_dirs({"gimp"}, set(), bases=[base]), [])

    def test_the_item_is_off_by_default(self):
        self.assertFalse(cleaner.BY_ID["leftovers"].default)


SAMPLE_NEIGH = json.dumps([
    {"dst": "192.168.1.1", "dev": "enp3s0", "lladdr": "aa:bb:cc:dd:ee:01", "state": ["REACHABLE"]},
    {"dst": "192.168.1.20", "dev": "enp3s0", "lladdr": "aa:bb:cc:dd:ee:02", "state": ["STALE"]},
    {"dst": "192.168.1.20", "dev": "enp3s0", "lladdr": "AA:BB:CC:DD:EE:02", "state": ["DELAY"]},
    {"dst": "192.168.1.99", "dev": "enp3s0", "state": ["FAILED"]},
    {"dst": "192.168.1.50", "dev": "enp3s0", "lladdr": "aa:bb:cc:dd:ee:03", "state": ["INCOMPLETE"]},
    {"dst": "224.0.0.251", "dev": "enp3s0", "lladdr": "01:00:5e:00:00:fb", "state": ["PERMANENT"]},
])


class Devices(unittest.TestCase):
    def test_one_entry_per_device_and_no_dead_or_multicast_entries(self):
        devices = lan.parse_neigh(SAMPLE_NEIGH)
        self.assertEqual([d["mac"] for d in devices], ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"])
        self.assertEqual(devices[1]["ips"], ["192.168.1.20"])

    def test_only_real_adapters_count(self):
        self.assertFalse(lan.physical(""))
        self.assertFalse(lan.physical("lo"))              # loopback has no hardware behind it
        self.assertFalse(lan.physical("../etc"))           # a name with a slash is never a device path

    def test_bad_output_gives_no_devices(self):
        self.assertEqual(lan.parse_neigh("not json"), [])
        self.assertEqual(lan.parse_neigh(""), [])

    def test_marked_devices_are_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "known.json")
            self.assertEqual(lan.known(path), set())
            lan.mark_known(["AA:BB:CC:DD:EE:01"], path)
            lan.mark_known(["aa:bb:cc:dd:ee:02"], path)
            self.assertEqual(lan.known(path), {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"})


SAMPLE_SS = """\
tcp   ESTAB 0      0      192.168.1.10:54322 142.250.80.46:443 users:(("firefox",pid=2134,fd=87))
tcp   ESTAB 0      0      192.168.1.10:54330 142.250.80.46:443 users:(("firefox",pid=2134,fd=90))
tcp   ESTAB 0      0      127.0.0.1:631      127.0.0.1:50000   users:(("cupsd",pid=900,fd=8))
tcp   ESTAB 0      0      [fe80::1]:5353     [2001:db8::5]:443 users:(("code",pid=77,fd=3))
tcp   ESTAB 0      0      192.168.1.10:22    203.0.113.9:51234
tcp   LISTEN 0      128    0.0.0.0:22         0.0.0.0:*          users:(("sshd",pid=1,fd=3))
"""


class Connections(unittest.TestCase):
    def test_remote_connections_only(self):
        conns = ports.parse_connections(SAMPLE_SS)
        self.assertEqual(len(conns), 4)                       # the loopback line is skipped
        code = next(c for c in conns if c.process == "code")
        self.assertEqual((code.remote, code.port, code.pid), ("2001:db8::5", 443, 77))

    def test_programs_are_grouped_and_remotes_listed_once(self):
        rows = ports.by_program(ports.parse_connections(SAMPLE_SS))
        name, pid, count, remotes = rows[0]
        self.assertEqual((name, pid, count), ("firefox", 2134, 2))
        self.assertEqual(remotes, ["142.250.80.46:443"])
        self.assertIn(("", 0, 1, ["203.0.113.9:51234"]), rows)   # another user's program: no name shown


if __name__ == "__main__":
    unittest.main()
