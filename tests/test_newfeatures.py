"""Software sources, open ports, self-update and the health trend."""
import hashlib
import os
import subprocess
import time
from pathlib import Path
from unittest import mock

from mintcare import maintenance, ports, selfupdate, sources
from tests.helpers import TempDirTest

LIST = """# deb cdrom:[Linux Mint 22.3 _Zena_ - Release amd64]/ zena contrib main
deb http://packages.linuxmint.com zena main upstream #id:linuxmint_main
deb [arch=amd64 signed-by=/nonexistent/key.gpg] https://example.com/apt stable main
deb http://ppa.launchpadcontent.net/someone/tool/ubuntu jammy main
# deb http://ppa.launchpadcontent.net/old/thing/ubuntu noble main
deb http://packages.linuxmint.com zena main upstream
"""
DEB822 = """Types: deb
URIs: https://brave-browser-apt-release.s3.brave.com
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/brave.gpg

Types: deb
URIs: https://off.example.org/repo
Suites: stable
Components: main
Enabled: no
"""


class Sources(TempDirTest):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp)
        (root / "sources.list.d").mkdir()
        (root / "lists").mkdir()
        (root / "sources.list.d" / "mixed.list").write_text(LIST)
        (root / "sources.list.d" / "brave.sources").write_text(DEB822)
        (root / "lists" / "packages.linuxmint.com_dists_zena_Release").write_text("x")
        self._old = (sources.SOURCES_DIR, sources.MAIN_LIST, sources.LISTS_DIR, maintenance.STATE)
        sources.SOURCES_DIR, sources.MAIN_LIST, sources.LISTS_DIR = root / "sources.list.d", root / "none", root / "lists"
        maintenance.STATE = root / "state"

    def tearDown(self):
        sources.SOURCES_DIR, sources.MAIN_LIST, sources.LISTS_DIR, maintenance.STATE = self._old
        super().tearDown()

    def by_label(self):
        out = {}
        for s_ in sources.analyse():
            out.setdefault(s_.label, []).append(s_)
        return out

    def test_parsing_both_formats(self):
        srcs = sources.sources()
        self.assertFalse(any("cdrom" in s_.uri for s_ in srcs))
        labels = [s_.label for s_ in srcs]
        self.assertIn("ppa:someone/tool", labels)
        self.assertIn("brave-browser-apt-release.s3.brave.com", labels)
        off = [s_ for s_ in srcs if not s_.enabled]
        self.assertEqual({s_.label for s_ in off}, {"ppa:old/thing", "off.example.org"})
        brave = next(s_ for s_ in srcs if "brave" in s_.uri)
        self.assertEqual((brave.fmt, brave.suite, brave.signed_by), ("deb822", "stable", "/usr/share/keyrings/brave.gpg"))

    def test_problems(self):
        with mock.patch.object(sources, "ubuntu_codename", return_value="noble"):
            b = self.by_label()
        mint = b["packages.linuxmint.com"]
        self.assertEqual(mint[0].health, "ok")                       # has a Release file
        self.assertTrue(any("Duplicate" in t for _l, t in mint[1].problems))
        ppa = b["ppa:someone/tool"][0]
        self.assertTrue(any("jammy" in t for _l, t in ppa.problems))
        third = b["example.com"][0]
        self.assertEqual(third.health, "fail")
        self.assertTrue(any("key file" in t for _l, t in third.problems))

    def test_lists_name(self):
        self.assertEqual(sources.lists_name("http://security.ubuntu.com/ubuntu/", "noble-security"),
                         "security.ubuntu.com_ubuntu_dists_noble-security")
        self.assertEqual(sources.lists_name("https://u:p@host/repo", "./"), "host_repo_.")

    def test_apt_update_errors_map_to_sources(self):
        out = ("Hit:1 http://packages.linuxmint.com zena InRelease\n"
               "Err:2 https://example.com/apt stable InRelease\n"
               "  404  Not Found [IP: 1.2.3.4 443]\n"
               "W: GPG error: https://example.com/apt stable InRelease: NO_PUBKEY ABCDEF\n")
        data = sources.save_test(out)
        self.assertEqual(data["errors"][0]["uri"], "https://example.com/apt")
        self.assertIn("404", data["errors"][0]["text"])
        third = self.by_label()["example.com"][0]
        self.assertTrue(any("last test" in t for _l, t in third.problems))
        self.assertTrue(any("NO_PUBKEY" in t for _l, t in third.problems))

    def test_toggle_script_round_trip(self):
        f = sources.SOURCES_DIR / "mixed.list"
        src = next(s_ for s_ in sources.sources() if s_.label == "ppa:someone/tool")
        script = sources.toggle_script(src, enable=False).replace("/var/lib/mintcare/", self.tmp + "/")
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        self.assertIn("# mintcare-disabled: deb http://ppa", f.read_text())
        again = next(s_ for s_ in sources.sources() if s_.label == "ppa:someone/tool")
        self.assertFalse(again.enabled)
        self.assertTrue(again.mintcare_disabled)
        subprocess.run(["bash", "-c", sources.toggle_script(again, enable=True).replace("/var/lib/mintcare/",
                                                                                       self.tmp + "/")],
                       check=True, capture_output=True)
        self.assertEqual(f.read_text(), LIST)

    def test_deb822_toggle(self):
        f = sources.SOURCES_DIR / "brave.sources"
        src = next(s_ for s_ in sources.sources() if "brave" in s_.uri)
        subprocess.run(["bash", "-c", sources.toggle_script(src, False).replace("/var/lib/mintcare/", self.tmp + "/")],
                       check=True, capture_output=True)
        self.assertFalse(next(s_ for s_ in sources.sources() if "brave" in s_.uri).enabled)
        src = next(s_ for s_ in sources.sources() if "brave" in s_.uri)
        subprocess.run(["bash", "-c", sources.toggle_script(src, True).replace("/var/lib/mintcare/", self.tmp + "/")],
                       check=True, capture_output=True)
        self.assertTrue(next(s_ for s_ in sources.sources() if "brave" in s_.uri).enabled)
        self.assertNotIn("X-MintCare", f.read_text())


SS = """udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*
udp UNCONN 0 0 *:56095 *:* users:(("firefox-bin",pid=14655,fd=224))
tcp LISTEN 0 4096 127.0.0.1:631 0.0.0.0:*
tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=900,fd=3))
tcp LISTEN 0 50 *:60000 *:*
udp UNCONN 0 0 [fe80::1]%enp4s0:546 [::]:*
"""


class Ports(TempDirTest):
    def services(self, fw):
        with mock.patch.object(ports, "firewall_enabled", return_value=fw):
            return {(x["proto"], x["port"]): x for x in ports.services(ports.parse_ss(SS))}

    def test_scopes_and_levels_with_firewall(self):
        s_ = self.services(True)
        self.assertEqual(s_[("tcp", 631)]["scope"], "local")
        self.assertEqual(s_[("udp", 546)]["scope"], "link")
        self.assertEqual(s_[("tcp", 22)]["level"], "info")            # risky but firewalled
        self.assertEqual(s_[("tcp", 22)]["unit"], "ssh.service")
        self.assertEqual(s_[("udp", 56095)]["level"], "ok")           # browser's temporary port
        self.assertEqual(s_[("tcp", 60000)]["level"], "info")         # unknown -> identify
        self.assertIsNone(s_[("udp", 5353)]["unit"])                  # never offer to stop Avahi

    def test_no_firewall_warns(self):
        s_ = self.services(False)
        self.assertEqual(s_[("tcp", 22)]["level"], "warn")
        self.assertEqual(s_[("tcp", 60000)]["level"], "warn")

    def test_root_scan_parse(self):
        names, ufw = ports.parse_root_scan(["@@SS", 'tcp LISTEN 0 50 *:60000 *:* users:(("rustdesk",pid=5,fd=9))',
                                            "@@UFW", "Status: active", "Default: deny (incoming)"])
        self.assertEqual(names[("tcp", 60000)], "rustdesk")
        self.assertEqual(ufw, ["Status: active", "Default: deny (incoming)"])


class SelfUpdate(TempDirTest):
    def test_versions(self):
        self.assertTrue(selfupdate.is_newer("1.7.0", "1.6.1"))
        self.assertTrue(selfupdate.is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(selfupdate.is_newer("1.6.1", "1.6.1"))
        self.assertFalse(selfupdate.is_newer("1.6", "1.6.1"))

    def _info(self, data, digest):
        src = Path(self.tmp) / "pkg.deb"
        src.write_bytes(data)
        return {"asset": src.as_uri(), "asset_name": "mintcare_9.9.9_all.deb", "size": len(data), "sha256": digest}

    def test_download_verifies_checksum(self):
        data = os.urandom(5000)
        good = self._info(data, hashlib.sha256(data).hexdigest())
        path = selfupdate.download(good)
        self.assertEqual(Path(path).read_bytes(), data)
        self.assertEqual(oct(os.stat(os.path.dirname(path)).st_mode & 0o777), "0o755")
        bad = self._info(data, "0" * 64)
        with self.assertRaises(RuntimeError):
            selfupdate.download(bad)
        with self.assertRaises(RuntimeError):
            selfupdate.download(self._info(data, None))


class Trend(TempDirTest):
    def test_close_points_replace_previous(self):
        old = (maintenance.STATE, maintenance.SCORES_FILE)
        maintenance.STATE, maintenance.SCORES_FILE = Path(self.tmp), Path(self.tmp) / "scores.jsonl"
        try:
            maintenance.record_score(80, {"warn": 2})
            maintenance.record_score(85, {"warn": 1})            # within 30 min: replaces
            self.assertEqual([p["score"] for p in maintenance.scores()], [85])
            with mock.patch.object(time, "time", return_value=time.time() + 3600):
                maintenance.record_score(90, {})
            self.assertEqual([p["score"] for p in maintenance.scores()], [85, 90])
        finally:
            maintenance.STATE, maintenance.SCORES_FILE = old
