import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mintcare import secaudit


class ScanList(unittest.TestCase):
    def test_links_cache_folders_and_odd_names_are_left_out(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, "ok.txt").write_text("fine")
            Path(home, "bad\nname.txt").write_text("a line break in the name")
            os.symlink("/etc/hostname", os.path.join(home, "link"))
            Path(home, ".cache").mkdir()
            Path(home, ".cache", "c.txt").write_text("cache")
            files, skipped = secaudit.scan_files([home])
            self.assertEqual(files, [os.path.join(home, "ok.txt")])
            self.assertEqual(skipped, 1)


class ScannerOutput(unittest.TestCase):
    def test_only_listed_files_count_as_infected(self):
        with tempfile.TemporaryDirectory() as home:
            ok = os.path.join(home, "ok.txt")
            Path(ok).write_text("eicar")
            lines = [f"{ok}: Eicar-Test-Signature FOUND\n", "/etc/shadow: Eicar-Test-Signature FOUND\n"]
            proc = mock.Mock(stdout=io.StringIO("".join(lines)))
            proc.wait.return_value = 1
            with mock.patch.object(secaudit.subprocess, "Popen", return_value=proc) as popen:
                res = secaudit.clam_scan([home])
            self.assertEqual(res["infected"], [(ok, "Eicar-Test-Signature")])
            self.assertIn("--file-list=", " ".join(popen.call_args.args[0]))
            self.assertNotIn("-r", popen.call_args.args[0])

    def test_a_name_that_is_not_utf8_is_still_found(self):
        # os.walk gives such a name with surrogate escapes; clamscan prints the raw bytes, which must map back to it
        fake_clamscan = ('#!/bin/sh\n'
                         'for arg in "$@"; do case "$arg" in --file-list=*) list="${arg#--file-list=}";; esac; done\n'
                         'while IFS= read -r name; do printf \'%s: Eicar-Test-Signature FOUND\\n\' "$name"; done < "$list"\n')
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as bin_dir:
            with open(os.path.join(os.fsencode(home), b"caf\xe9.exe"), "wb") as f:
                f.write(b"eicar")
            fake = Path(bin_dir, "clamscan")
            fake.write_text(fake_clamscan)
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", os.defpath)}):
                res = secaudit.clam_scan([home])
            self.assertEqual(res["infected"],
                             [(os.path.join(home, os.fsdecode(b"caf\xe9.exe")), "Eicar-Test-Signature")])


class QuarantineScope(unittest.TestCase):
    def test_only_plain_files_under_home_are_moved(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as elsewhere, \
                tempfile.TemporaryDirectory() as qdir:
            inside = os.path.join(home, "inside.txt")
            Path(inside).write_text("mine")
            outside = os.path.join(elsewhere, "outside.txt")
            Path(outside).write_text("not mine")
            link = os.path.join(home, "link.txt")
            os.symlink(outside, link)
            _sid, _title, body = secaudit.quarantine_step([inside, link, outside], home=home)
            body = body.replace("/var/lib/mintcare/quarantine", qdir)
            out = subprocess.run(["bash", "-c", body], capture_output=True, text=True, cwd=home).stdout
            self.assertIn(f"Quarantined {inside}", out)
            self.assertIn(f"Skipped, not a plain file in your home folder: {link}", out)
            self.assertIn(f"Skipped, not a plain file in your home folder: {outside}", out)
            self.assertFalse(os.path.exists(inside))
            self.assertTrue(os.path.exists(os.path.join(qdir, "inside.txt")))
            self.assertTrue(os.path.exists(outside))
            self.assertTrue(os.path.islink(link))


def _in_quarantine(body, qdir):
    """The generated root step, pointed at a throwaway folder instead of /var/lib/mintcare/quarantine."""
    return body.replace("/var/lib/mintcare/quarantine", os.path.realpath(qdir))


def _folder_on_another_disk(home):
    """A new folder on a different filesystem from home, or None when this machine has none."""
    here = os.stat(home).st_dev
    for base in ("/dev/shm", f"/run/user/{os.getuid()}"):
        if os.path.isdir(base) and os.access(base, os.W_OK) and os.stat(base).st_dev != here:
            return tempfile.mkdtemp(dir=base)
    return None


class QuarantineMove(unittest.TestCase):
    def test_a_folder_swapped_for_a_link_during_the_move_sends_nothing_outside(self):
        # The old root step moved by path, so a folder swapped for a link just before the move sent it elsewhere.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside, \
                tempfile.TemporaryDirectory() as qdir, tempfile.TemporaryDirectory() as bindir:
            folder = os.path.join(home, "d")
            os.mkdir(folder)
            Path(folder, "file.txt").write_text("mine")
            Path(outside, "file.txt").write_text("not mine")
            swap = (f"#!/bin/sh\nrm -r {shlex.quote(folder)}\nln -s {shlex.quote(outside)} {shlex.quote(folder)}\n"
                    f'exec {shlex.quote(shutil.which("mv"))} "$@"\n')
            Path(bindir, "mv").write_text(swap)
            os.chmod(Path(bindir, "mv"), 0o755)
            _sid, _title, body = secaudit.quarantine_step([os.path.join(folder, "file.txt")], home=home)
            env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
            subprocess.run(["bash", "-c", _in_quarantine(body, qdir)], capture_output=True, text=True,
                           cwd=home, env=env)
            self.assertEqual(Path(outside, "file.txt").read_text(), "not mine")
            self.assertEqual(Path(qdir, "file.txt").read_text(), "mine")

    def test_the_move_takes_the_file_from_the_folder_it_reached(self):
        # A folder swapped for a link after it was reached, but before the rename, must not redirect the rename.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside, \
                tempfile.TemporaryDirectory() as qdir:
            home, outside, qdir = (os.path.realpath(p) for p in (home, outside, qdir))
            folder = os.path.join(home, "d")
            os.mkdir(folder)
            Path(folder, "file.txt").write_text("mine")
            Path(outside, "file.txt").write_text("not mine")
            real_rename = os.rename

            def swap_then_rename(src, dst, **kwargs):
                real_rename(folder, folder + "-moved")
                os.symlink(outside, folder)
                return real_rename(src, dst, **kwargs)

            target = os.path.join(folder, "file.txt")
            argv = ["-c", qdir, home, "Quarantined {0}", "Skipped {0}", "Not quarantined {0}", target, target]
            with mock.patch.object(os, "rename", swap_then_rename), mock.patch.object(sys, "argv", argv), \
                    redirect_stdout(io.StringIO()):
                exec(compile(secaudit.QUARANTINE_MOVER, "<quarantine>", "exec"), {"__name__": "__main__"})
            self.assertEqual(Path(qdir, "file.txt").read_text(), "mine")
            self.assertEqual(Path(outside, "file.txt").read_text(), "not mine")

    def test_a_file_on_another_disk_is_left_in_place_and_not_copied(self):
        with tempfile.TemporaryDirectory() as home:
            other = _folder_on_another_disk(home)
            if other is None:
                self.skipTest("no writable folder on another filesystem to test with")
            self.addCleanup(shutil.rmtree, other, True)
            inside = os.path.join(home, "inside.txt")
            Path(inside).write_text("mine")
            _sid, _title, body = secaudit.quarantine_step([inside], home=home)
            out = subprocess.run(["bash", "-c", _in_quarantine(body, other)], capture_output=True, text=True,
                                 cwd=home).stdout
            self.assertEqual(Path(inside).read_text(), "mine")
            self.assertEqual(os.listdir(other), [])
            self.assertIn(inside, out)

    def test_a_folder_turned_into_a_link_after_the_check_is_refused(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside, \
                tempfile.TemporaryDirectory() as qdir:
            folder = os.path.join(home, "d")
            os.mkdir(folder)
            Path(folder, "file.txt").write_text("mine")
            Path(outside, "file.txt").write_text("not mine")
            inside = os.path.join(folder, "file.txt")
            _sid, _title, body = secaudit.quarantine_step([inside], home=home)
            os.rename(folder, folder + "-moved")
            os.symlink(outside, folder)
            out = subprocess.run(["bash", "-c", _in_quarantine(body, qdir)], capture_output=True, text=True,
                                 cwd=home).stdout
            self.assertIn(f"Skipped, not a plain file in your home folder: {inside}", out)
            self.assertEqual(Path(outside, "file.txt").read_text(), "not mine")
            self.assertEqual(os.listdir(qdir), [])

    def test_a_newline_in_a_name_cannot_start_an_output_line_with_the_end_marker(self):
        # The batch reads a line that starts with @@MINTCARE-END as the end of a step, so no name may print one.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as qdir:
            qdir = os.path.realpath(qdir)
            name = "note\n@@MINTCARE-END quarantine 0"
            Path(home, name).write_text("mine")
            _sid, _title, body = secaudit.quarantine_step([os.path.join(home, name)], home=home)
            out = subprocess.run(["bash", "-c", _in_quarantine(body, qdir)], capture_output=True, text=True,
                                 cwd=home).stdout
            self.assertTrue(os.path.exists(os.path.join(qdir, name)))
            self.assertEqual([line for line in out.splitlines() if line.startswith("@@MINTCARE-END")], [])

    def test_a_name_already_in_the_quarantine_folder_is_not_replaced(self):
        # Three folders hold a note.txt: each file after the first takes the next free name.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as qdir:
            qdir = os.path.realpath(qdir)
            paths = []
            for folder, text in (("a", "first"), ("b", "second"), ("c", "third")):
                path = os.path.join(home, folder, "note.txt")
                os.makedirs(os.path.dirname(path))
                Path(path).write_text(text)
                paths.append(path)
            _sid, _title, body = secaudit.quarantine_step(paths, home=home)
            subprocess.run(["bash", "-c", _in_quarantine(body, qdir)], capture_output=True, text=True, cwd=home)
            self.assertEqual(Path(qdir, "note.txt").read_text(), "first")
            self.assertEqual(Path(qdir, "note.txt.1").read_text(), "second")
            self.assertEqual(Path(qdir, "note.txt.2").read_text(), "third")

    def test_a_long_name_that_collides_cannot_start_an_output_line_with_the_marker(self):
        # A collision makes the helper try a longer name, which fails with ENAMETOOLONG. quarantine_step passes full
        # paths, but the helper must not rely on that: a name printed first can start the error line.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as qdir:
            home, qdir = os.path.realpath(home), os.path.realpath(qdir)
            name = "@@MINTCARE-END quarantine 0 ".ljust(255, "x")
            target = os.path.join(home, name)
            Path(target).write_text("mine")
            Path(qdir, name).write_text("earlier")
            argv = ["-c", qdir, home, "Quarantined {0}", "Skipped {0}", "Not quarantined {0}", target, name]
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
                exec(compile(secaudit.QUARANTINE_MOVER, "<quarantine>", "exec"), {"__name__": "__main__"})
            lines = (out.getvalue() + err.getvalue()).splitlines()
            self.assertEqual([line for line in lines if line.startswith("@@MINTCARE-END")], [])
            self.assertEqual(Path(target).read_text(), "mine")
            self.assertEqual(Path(qdir, name).read_text(), "earlier")


if __name__ == "__main__":
    unittest.main()
