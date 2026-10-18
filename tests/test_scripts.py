"""Every shell script MintCare can run must at least parse, and ids must be consistent."""
import unittest
from pathlib import Path

from mintcare import checks, cleaner, tweaks
from mintcare.runner import _script
from tests.helpers import bash_syntax_ok


class ScriptSyntax(unittest.TestCase):
    def test_tweak_scripts_parse(self):
        for t in tweaks.TWEAKS:
            with self.subTest(tweak=t.id, kind="apply"):
                ok, err = bash_syntax_ok(t.apply_script())
                self.assertTrue(ok, err)
            if t.revert:
                with self.subTest(tweak=t.id, kind="revert"):
                    ok, err = bash_syntax_ok(t.revert_script())
                    self.assertTrue(ok, err)

    def test_cleaner_scripts_parse(self):
        for i in cleaner.ITEMS:
            for body in (i.root_script, i.user_script):
                if body:
                    with self.subTest(item=i.id):
                        ok, err = bash_syntax_ok(body)
                        self.assertTrue(ok, err)

    def test_combined_batch_parses(self):
        steps = [(t.id, t.title, t.apply_script()) for t in tweaks.TWEAKS]
        ok, err = bash_syntax_ok(_script(steps))
        self.assertTrue(ok, err)

    def test_check_fix_scripts_parse(self):
        for c in checks.CHECKS:
            r = c.run()
            if r.fix and r.fix.kind in ("root", "user"):
                with self.subTest(check=c.id):
                    ok, err = bash_syntax_ok(r.fix.payload)
                    self.assertTrue(ok, err)


class Consistency(unittest.TestCase):
    def test_unique_ids(self):
        for name, ids in (("tweaks", [t.id for t in tweaks.TWEAKS]),
                          ("checks", [c.id for c in checks.CHECKS]),
                          ("cleaner", [i.id for i in cleaner.ITEMS])):
            with self.subTest(kind=name):
                self.assertEqual(len(ids), len(set(ids)))

    def test_profiles_and_conflicts_reference_real_tweaks(self):
        for p in tweaks.PROFILES:
            for tid in p.tweaks:
                self.assertIn(tid, tweaks.BY_ID, f"profile {p.id} -> {tid}")
        for t in tweaks.TWEAKS:
            for other in t.conflicts:
                self.assertIn(other, tweaks.BY_ID, f"{t.id} conflicts with unknown {other}")

    def test_tweak_fixes_point_at_real_tweaks(self):
        for c in checks.CHECKS:
            r = c.run()
            if r.fix and r.fix.kind == "tweak":
                self.assertIn(r.fix.payload, tweaks.BY_ID, c.id)

    def test_managed_files_are_marked(self):
        # MintCare promises to only write files it owns; config bodies carry a marker
        for t in tweaks.TWEAKS:
            if "cat >" in t.apply and "<<'EOF'" in t.apply:
                self.assertIn("MintCare", t.apply, t.id)


if __name__ == "__main__":
    unittest.main()


class TestUntrustedText(unittest.TestCase):
    """Names from outside MintCare (process names, file names, Flatpak names) must stay text in root scripts."""

    def test_title_and_quarantine_path(self):
        import os, subprocess, tempfile
        from mintcare import processes, secaudit
        with tempfile.TemporaryDirectory() as d:
            mark = os.path.join(d, "pwned")
            name = "x';touch pwned;'.exe"
            Path(d, name).touch()
            sid, title, body = secaudit.quarantine_step([os.path.join(d, name)])
            body = body.replace("/var/lib/mintcare/quarantine", os.path.join(d, "q"))
            kill = processes.end_step(999999, f"$(touch {mark})`touch {mark}`")
            out = subprocess.run(["bash", "-c", _script([(sid, title, body), kill])],
                                 capture_output=True, text=True, cwd=d).stdout
            self.assertFalse(os.path.exists(mark), out)
            self.assertIn(f"==> End $(touch {mark})", out)
            self.assertIn(f"Quarantined {d}/{name}", out)

    def test_source_credentials_hidden(self):
        from mintcare import report, sources
        s = sources.Source("f", 1, "list", "deb", "https://TOKEN:secret@packagecloud.io/org/repo/ubuntu", "noble", [])
        self.assertEqual(s.label, "packagecloud.io")
        self.assertNotIn("secret", report._scrub("deb https://TOKEN:secret@example.com/x noble main", True))
