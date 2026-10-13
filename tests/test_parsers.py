"""Pure parsing/scoring logic: boot times, Flatpak permissions, hardware levels."""
import unittest
from pathlib import Path
from unittest import mock

from mintcare import boot, flatpaks, hardware
from tests.helpers import TempDirTest


class BootParsing(unittest.TestCase):
    def test_secs(self):
        self.assertAlmostEqual(boot._secs("6.454s"), 6.454)
        self.assertAlmostEqual(boot._secs("450ms"), 0.45)
        self.assertAlmostEqual(boot._secs("1min 2.5s"), 62.5)

    def test_times(self):
        line = ("Startup finished in 24.888s (firmware) + 2.415s (loader) + 4.211s (kernel) + "
                "9.014s (userspace) = 40.529s \ngraphical.target reached after 8.992s in userspace.")
        with mock.patch.object(boot, "out", return_value=line):
            t = boot.times()
        self.assertEqual(t["firmware"], 24.888)
        self.assertEqual(t["userspace"], 9.014)
        self.assertEqual(t["total"], 40.529)

    def test_critical_chain_ascii_and_unicode(self):
        sample = ("graphical.target @8.992s\n"
                  "`-multi-user.target @8.991s\n"
                  "  `-smartmontools.service @2.536s +6.454s\n"
                  "    └─basic.target @2.5s\n"
                  "      └─systemd-timesyncd.service @0.8s +1.687s\n")
        with mock.patch.object(boot, "out", return_value=sample):
            chain = boot.critical_chain()
        self.assertEqual(chain, {"smartmontools.service": 6.454, "systemd-timesyncd.service": 1.687})


class FlatpakPerms(TempDirTest):
    META = """[Context]
shared=network;ipc;
sockets=x11;wayland;pulseaudio;
devices=all;
filesystems=xdg-download;home;
"""

    def test_parse_and_risk(self):
        ctx = flatpaks._parse_context(self.META)
        self.assertTrue(flatpaks.has(ctx, "network"))
        self.assertTrue(flatpaks.has(ctx, "home"))
        self.assertTrue(flatpaks.has(ctx, "devices"))
        rating, score, why = flatpaks.risk(ctx)
        self.assertEqual(rating, "weak")                    # home 2 + devices 2 + x11 1
        self.assertEqual(score, 5)

    def test_override_removes_and_adds(self):
        ctx = flatpaks._parse_context(self.META)
        ov = Path(self.tmp) / "app"
        ov.write_text("[Context]\nfilesystems=!home;\ndevices=!all;\nshared=!network;\nsockets=!x11;\n")
        self.assertTrue(flatpaks._apply_override(ctx, ov))
        self.assertFalse(flatpaks.has(ctx, "home"))
        self.assertFalse(flatpaks.has(ctx, "network"))
        self.assertEqual(flatpaks.risk(ctx)[0], "strong")

    def test_x11_fallback_only_counts_without_wayland(self):
        ctx = {"sockets": ["wayland", "fallback-x11"]}
        self.assertFalse(flatpaks.has(ctx, "x11"))
        self.assertTrue(flatpaks.has({"sockets": ["fallback-x11"]}, "x11"))


class HardwareLevels(unittest.TestCase):
    def test_temp_level(self):
        lvl = hardware.temp_level
        self.assertEqual(lvl({"temp": 45, "high": 90, "crit": 95}), "ok")
        self.assertEqual(lvl({"temp": 91, "high": 90, "crit": 100}), "warn")
        self.assertEqual(lvl({"temp": 92, "high": 90, "crit": 95}), "fail")
        self.assertEqual(lvl({"temp": 88, "high": None, "crit": None}), "warn")

    def test_years(self):
        self.assertEqual(hardware._years(10), "10 h")
        self.assertEqual(hardware._years(9278), "1.1 yrs")



class ReportPrivacy(unittest.TestCase):
    def test_scrub_removes_identity(self):
        import getpass
        import socket
        from mintcare import report
        home, user, host = str(Path.home()), getpass.getuser(), socket.gethostname()
        text = f"{host} {home}/Downloads/{user}.txt /home/{user}/x"
        out = report._scrub(text, private=True)
        self.assertNotIn(host, out)
        self.assertNotIn(home, out)
        if len(user) > 2:
            self.assertNotIn(user, out)
        self.assertEqual(report._scrub(text, private=False), text)

    def test_score(self):
        from types import SimpleNamespace as R
        from mintcare import report
        sc, n = report.score([R(status="fail"), R(status="warn"), R(status="info"), R(status="ok")])
        self.assertEqual(sc, 100 - 15 - 5 - 1)
        self.assertEqual(n, {"fail": 1, "warn": 1, "info": 1})


if __name__ == "__main__":
    unittest.main()
