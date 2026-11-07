import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bash_syntax_ok(script):
    p = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    return p.returncode == 0, p.stderr


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mintcare-test-")
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, data=b"", mtime=None):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        if mtime:
            os.utime(path, (mtime, mtime))
        return path
