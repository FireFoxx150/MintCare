"""The runner reports per-step exit codes, tweaks are only marked applied on success,
and root steps reach the helper as a sealed batch."""
import fcntl
import hashlib
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from mintcare import runner, selfupdate, tweaks
from mintcare.runner import run_steps
from tests.helpers import ROOT, TempDirTest

HELPER_SRC = os.path.join(ROOT, "packaging", "mintcare-root")


class Runner(TempDirTest):
    def test_codes_and_output(self):
        lines = []
        res = run_steps([("a", "ok", "echo hello"), ("b", "bad", "exit 3")], root=False, on_line=lines.append)
        self.assertEqual(res, {"a": 0, "b": 3})
        self.assertIn("hello", lines)

    def test_steps_cannot_read_stdin(self):
        res = run_steps([("a", "stdin", "read x && exit 9 || exit 0")], root=False, on_line=lambda _l: None)
        self.assertEqual(res["a"], 0)

    def test_marker_only_on_success(self):
        old = tweaks.MARKERS
        tweaks.MARKERS = Path(self.tmp) / "applied"
        try:
            bad = tweaks.Tweak("bad", "x", "c", "d", lambda: True, "false\necho SHOULD-NOT-RUN")
            good = tweaks.Tweak("good", "x", "c", "d", lambda: True, "true", revert="true")
            out = []
            res = run_steps([("bad", "bad", bad.apply_script()), ("good", "good", good.apply_script())],
                            root=False, on_line=out.append)
            self.assertNotEqual(res["bad"], 0)
            self.assertEqual(res["good"], 0)
            self.assertNotIn("SHOULD-NOT-RUN", out)        # set -e stops the failing step
            self.assertEqual(sorted(os.listdir(tweaks.MARKERS)), ["good"])
            self.assertEqual(good.state(), "mintcare")
            self.assertEqual(bad.state(), "system")
            run_steps([("r", "revert", good.revert_script())], root=False, on_line=lambda _l: None)
            self.assertFalse((tweaks.MARKERS / "good").exists())
        finally:
            tweaks.MARKERS = old


class SealedBatch(unittest.TestCase):
    def test_the_batch_is_read_only_and_reads_from_the_top(self):
        fd = runner._sealed("echo hi\n")
        try:
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE)
            self.assertEqual(os.read(fd, 100), b"echo hi\n")
            with self.assertRaises(PermissionError):
                os.write(fd, b"rm -rf /")
        finally:
            os.close(fd)

    @staticmethod
    def _recording_popen(seen):
        class FakePopen:
            def __init__(self, cmd, stdin, **_kw):
                seen["cmd"] = cmd
                seen["batch"] = os.pread(stdin, 1 << 16, 0)
                self.stdout = iter([])

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def wait(self):
                return 0
        return FakePopen

    def test_root_steps_send_the_sealed_batch_to_the_helper_through_pkexec(self):
        seen, steps = {}, [("a", "ok", "echo hi")]
        with mock.patch.object(runner.subprocess, "Popen", self._recording_popen(seen)), \
                mock.patch.object(runner.os, "geteuid", return_value=1000), \
                mock.patch.object(runner.os, "access", return_value=True):
            run_steps(steps, root=True, on_line=lambda _l: None)
        self.assertEqual(seen["cmd"], [runner.PKEXEC, runner.HELPER])
        self.assertEqual(seen["batch"].decode(), runner._script(steps))

    def test_without_the_installed_helper_the_same_logic_runs_under_pkexec_bash(self):
        seen = {}
        with mock.patch.object(runner.subprocess, "Popen", self._recording_popen(seen)), \
                mock.patch.object(runner.os, "geteuid", return_value=1000), \
                mock.patch.object(runner.os, "access", return_value=False):
            run_steps([("a", "ok", "echo hi")], root=True, on_line=lambda _l: None)
        self.assertEqual(seen["cmd"], [runner.PKEXEC, "/bin/bash", "-c", runner.ROOT_RUN])


class RootHelper(TempDirTest):
    """packaging/mintcare-root, run with a stub `id` so its root check passes without root."""

    def setUp(self):
        super().setUp()
        stub = os.path.join(self.tmp, "bin")
        os.mkdir(stub)
        with open(os.path.join(stub, "id"), "w") as f:
            f.write("#!/bin/sh\necho 0\n")
        os.chmod(os.path.join(stub, "id"), 0o755)
        self.env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")

    def _helper(self, args, stdin):
        return subprocess.run(["bash", HELPER_SRC, *args], input=stdin, text=True,
                              capture_output=True, env=self.env, timeout=60)

    def test_runs_the_batch_from_stdin_then_removes_its_folder(self):
        p = self._helper([], 'echo "ran from $0"\n')
        self.assertEqual(p.returncode, 0, p.stderr)
        batch = p.stdout.split("ran from ", 1)[1].strip()
        self.assertTrue(batch.startswith("/tmp/mintcare-batch."), batch)
        self.assertFalse(os.path.exists(os.path.dirname(batch)))

    def test_a_path_argument_is_refused_and_nothing_runs(self):
        p = self._helper([os.path.join(self.tmp, "x.sh")], "echo should-not-run\n")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("should-not-run", p.stdout)

    def test_an_empty_batch_is_refused(self):
        self.assertNotEqual(self._helper([], "").returncode, 0)

    @unittest.skipIf(os.geteuid() == 0, "checks the refusal for a user who is not root")
    def test_without_root_it_refuses(self):
        p = subprocess.run(["bash", HELPER_SRC], input="echo x\n", text=True, capture_output=True)
        self.assertEqual(p.returncode, 126)


class PkexecPath(unittest.TestCase):
    def test_pkexec_is_called_by_its_absolute_path(self):
        self.assertEqual(runner.PKEXEC, "/usr/bin/pkexec")


class ChainThroughHelper(TempDirTest):
    """run_steps -> pkexec -> packaging/mintcare-root -> the batch, with stubs for pkexec, id and apt-get."""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        for name, body in (("id", "echo 0"), ("pkexec", 'exec "$@"'), ("apt-get", 'cp "$3" "$RECORD"')):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(f"#!/bin/sh\n{body}\n")
            os.chmod(path, 0o755)

    def _run_as_root(self, steps, **env):
        env = dict(env, PATH=f"{self.bin}:{os.environ['PATH']}")
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(runner, "HELPER", HELPER_SRC), \
                mock.patch.object(runner, "PKEXEC", os.path.join(self.bin, "pkexec")), \
                mock.patch.object(runner.os, "geteuid", return_value=1000):
            return run_steps(steps, root=True, on_line=lambda _l: None)

    def test_a_root_step_runs_the_batch_the_helper_read_from_stdin(self):
        marker = os.path.join(self.tmp, "marker")
        self.assertEqual(self._run_as_root([("a", "write", 'echo done > "$MARKER"')], MARKER=marker), {"a": 0})
        with open(marker) as f:
            self.assertEqual(f.read(), "done\n")

    def test_an_update_reaches_apt_as_the_copy_that_was_checked(self):
        data = os.urandom(5000)
        record = os.path.join(self.tmp, "installed.deb")
        step = selfupdate.install_step(data, hashlib.sha256(data).hexdigest(), "9.9.9")
        self.assertEqual(self._run_as_root([step], RECORD=record), {"selfupdate": 0})
        with open(record, "rb") as f:
            self.assertEqual(f.read(), data)


class SysctlCompare(unittest.TestCase):
    def test_cmp(self):
        c = tweaks._cmp
        self.assertTrue(c("bbr", "bbr", "="))
        self.assertFalse(c("cubic", "bbr", "="))
        self.assertTrue(c("1048576", "524288", ">="))
        self.assertFalse(c("8192", "524288", ">="))
        self.assertTrue(c("10", "10", "<="))
        self.assertFalse(c("60", "10", "<="))
        self.assertFalse(c("abc", "10", ">="))

    def test_sysctl_tweak_backs_up_and_restores(self):
        t = tweaks.BY_ID["bbr"]
        self.assertIn("/var/lib/mintcare/sysctl-bbr.bak", t.apply)
        self.assertIn("/etc/sysctl.d/99-mintcare-bbr.conf", t.apply)
        self.assertIn("sysctl -q -p /var/lib/mintcare/sysctl-bbr.bak", t.revert)


if __name__ == "__main__":
    unittest.main()
