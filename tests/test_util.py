import sys
import unittest

from mintcare import util
from tests.helpers import TempDirTest

REPL = "\N{REPLACEMENT CHARACTER}"


class NonUtf8Bytes(TempDirTest):
    """/proc files and process output can hold bytes that are not UTF-8; one such process must not break a read."""

    def test_read_replaces_invalid_utf8(self):
        path = self.write("cmdline", b"python3\x00\xff\xfe")
        self.assertEqual(util.read(path), "python3\x00" + REPL + REPL)

    def test_run_replaces_invalid_utf8(self):
        rc, out = util.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok\\xff')"])
        self.assertEqual((rc, out), (0, "ok" + REPL))


if __name__ == "__main__":
    unittest.main()
