"""Secure Boot is read from its EFI variable, and programs with the set-user-ID or set-group-ID bit are found."""
import os
from pathlib import Path

from mintcare import secaudit
from tests.helpers import TempDirTest


class SecureBoot(TempDirTest):
    def test_value_is_the_byte_after_the_attributes(self):
        self.assertTrue(secaudit.parse_secure_boot(b"\x06\x00\x00\x00\x01"))
        self.assertFalse(secaudit.parse_secure_boot(b"\x06\x00\x00\x00\x00"))

    def test_unknown_when_the_variable_is_short_or_missing(self):
        self.assertIsNone(secaudit.parse_secure_boot(b"\x06\x00"))
        self.assertIsNone(secaudit.secure_boot(Path(self.tmp) / "missing"))

    def test_reads_the_variable_file(self):
        var = Path(self.tmp) / "SecureBoot"
        var.write_bytes(b"\x06\x00\x00\x00\x01")
        self.assertTrue(secaudit.secure_boot(var))


class SetuidPrograms(TempDirTest):
    def test_only_program_files_with_the_bits_are_listed(self):
        bindir = Path(self.tmp) / "bin"
        bindir.mkdir()
        for name, mode in (("sudo", 0o4755), ("wall", 0o2755), ("ls", 0o755)):
            (bindir / name).write_bytes(b"x")
            os.chmod(bindir / name, mode)
        os.symlink(bindir / "sudo", bindir / "su")          # a link to a set-user-ID file is not one itself
        real = os.path.realpath(bindir)
        self.assertEqual(secaudit.setuid_programs([str(bindir)]),
                         [os.path.join(real, "sudo"), os.path.join(real, "wall")])

    def test_missing_folder_gives_nothing(self):
        self.assertEqual(secaudit.setuid_programs([str(Path(self.tmp) / "nope")]), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
