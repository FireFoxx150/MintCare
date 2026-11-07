"""The safe additions: the LUKS and auditd checks, the Lynis audit and its summary, and the SMART self-test steps."""
import shlex
import subprocess
import unittest
from unittest import mock

from mintcare import checks, hardware, secaudit
from tests.helpers import TempDirTest, bash_syntax_ok


class LuksState(unittest.TestCase):
    def test_a_system_disk_on_luks_is_root(self):
        self.assertEqual(checks.luks_state("crypt\npart\ndisk", "crypt\npart\ndisk"), "root")

    def test_an_encrypted_data_disk_is_other(self):
        self.assertEqual(checks.luks_state("part\ndisk", "part\ndisk\ncrypt\npart\ndisk"), "other")

    def test_no_luks_is_none(self):
        self.assertEqual(checks.luks_state("part\ndisk", "part\ndisk"), "none")


class EncryptionCheck(unittest.TestCase):
    def test_an_encrypted_system_disk_is_ok(self):
        with mock.patch.object(checks, "run", side_effect=[(0, "/dev/mapper/cryptroot\n"),
                                                           (0, "crypt\npart\ndisk\n"), (0, "crypt\npart\ndisk\n")]):
            self.assertEqual(checks.c_luks().status, checks.OK)

    def test_no_encryption_is_information_not_a_warning(self):
        with mock.patch.object(checks, "run", side_effect=[(0, "/dev/sda2\n"),
                                                           (0, "part\ndisk\n"), (0, "part\ndisk\n")]):
            self.assertEqual(checks.c_luks().status, checks.INFO)

    def test_a_btrfs_subvolume_is_looked_up_without_the_subvolume(self):
        with mock.patch.object(checks, "run", side_effect=[(0, "/dev/sda2[/@]\n"),
                                                           (0, "part\ndisk\n"), (0, "part\ndisk\n")]) as run:
            checks.c_luks()
        self.assertIn("/dev/sda2", run.call_args_list[1].args[0])

    def test_an_unreadable_device_list_is_information(self):
        with mock.patch.object(checks, "run", side_effect=[(0, "/dev/sda2\n"), (1, ""), (1, "")]):
            self.assertEqual(checks.c_luks().status, checks.INFO)


class AuditdCheck(unittest.TestCase):
    def test_not_installed_is_optional_information(self):
        with mock.patch.object(checks, "pkg_installed", return_value=False):
            self.assertEqual(checks.c_auditd().status, checks.INFO)

    def test_running_is_ok(self):
        with mock.patch.object(checks, "pkg_installed", return_value=True), \
                mock.patch.object(checks, "run", return_value=(0, "active\n")):
            self.assertEqual(checks.c_auditd().status, checks.OK)

    def test_installed_but_stopped_is_information(self):
        with mock.patch.object(checks, "pkg_installed", return_value=True), \
                mock.patch.object(checks, "run", return_value=(3, "inactive\n")):
            self.assertEqual(checks.c_auditd().status, checks.INFO)


class LynisAudit(TempDirTest):
    def test_the_summary_reads_back_as_three_numbers(self):
        path = self.write("lynis-summary", b"hardening_index=61\nwarnings=7\nsuggestions=22\n")
        self.assertEqual(secaudit.read_lynis_summary(path), (61, 7, 22))

    def test_no_audit_yet_is_none(self):
        self.assertIsNone(secaudit.read_lynis_summary(f"{self.tmp}/missing"))

    def test_a_damaged_summary_is_none(self):
        path = self.write("lynis-summary", b"hardening_index=oops\n")
        self.assertIsNone(secaudit.read_lynis_summary(path))

    def test_the_step_runs_a_quick_read_only_audit_and_keeps_only_the_summary(self):
        _id, _title, script = secaudit.lynis_step()
        self.assertIn("lynis audit system --quick --no-colors", script)
        self.assertIn("/var/log/lynis-report.dat", script)
        self.assertIn(secaudit.LYNIS_SUMMARY, script)
        ok, err = bash_syntax_ok(script)
        self.assertTrue(ok, err)

    def test_installing_lynis_does_not_run_rkhunter_setup(self):
        _id, _title, script = secaudit.install_step("lynis")
        self.assertIn("apt-get install -y lynis", script)
        self.assertNotIn("rkhunter", script)


class SelfTestSteps(unittest.TestCase):
    def test_each_drive_gets_a_short_test_with_its_device_quoted(self):
        steps = hardware.selftest_steps([
            {"device": "/dev/sda", "model": "Disk A"},
            {"device": "/dev/sd b", "model": "Odd name"},
            {"model": "No device, so skipped"},
        ])
        self.assertEqual([s[2] for s in steps],
                         ["smartctl -t short /dev/sda", "smartctl -t short " + shlex.quote("/dev/sd b")])


class LynisFindings(TempDirTest):
    def test_risky_wording_is_flagged_and_plain_wording_is_not(self):
        self.assertTrue(secaudit.lynis_risk("Consider hardening SSH configuration"))
        self.assertTrue(secaudit.lynis_risk("Enable AppArmor"))
        self.assertTrue(secaudit.lynis_risk("Disable IPv6 if it is not used"))
        self.assertTrue(secaudit.lynis_risk("One or more sysctl values differ from the scan profile and could be tweaked"))
        self.assertTrue(secaudit.lynis_risk("Harden compilers like restricting access to root user only"))
        self.assertTrue(secaudit.lynis_risk("Install a PAM module such as pam_cracklib"))
        self.assertFalse(secaudit.lynis_risk("Install libpam-tmpdir to set $TMP and $TMPDIR for PAM sessions"))
        self.assertFalse(secaudit.lynis_risk("Consider installing a file integrity tool"))

    def test_the_list_reads_back_with_the_risk_flag(self):
        path = self.write("lynis-items", b"warning\tAUTH-9286\tSet a password on GRUB\n"
                                          b"suggestion\tFILE-6310\tInstall a file integrity tool\n")
        self.assertEqual(secaudit.read_lynis_items(path),
                         [("warning", "AUTH-9286", "Set a password on GRUB", True),
                          ("suggestion", "FILE-6310", "Install a file integrity tool", False)])

    def test_no_list_is_none_and_an_empty_list_is_empty(self):
        self.assertIsNone(secaudit.read_lynis_items(f"{self.tmp}/missing"))
        self.assertEqual(secaudit.read_lynis_items(self.write("empty-items", b"")), [])

    def test_the_command_keeps_only_the_kind_id_and_description(self):
        report = self.write("report.dat", b"hardening_index=61\n"
                                           b"warning[]=AUTH-9286|Set a password on GRUB|detail with /home/someone\n"
                                           b"suggestion[]=SSH-7408|Consider hardening SSH configuration|more detail\n")
        out = f"{self.tmp}/items"
        subprocess.run(["bash", "-c", secaudit.lynis_items_command(report, out)], check=True)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "warning\tAUTH-9286\tSet a password on GRUB\n"
                                        "suggestion\tSSH-7408\tConsider hardening SSH configuration\n")

    def test_the_step_keeps_the_findings_private_to_the_user_who_ran_the_audit(self):
        _id, _title, script = secaudit.lynis_step()
        ok, err = bash_syntax_ok(script)
        self.assertTrue(ok, err)
        self.assertIn(secaudit.LYNIS_ITEMS, script)
        self.assertIn("chmod 600", script)
        self.assertIn("PKEXEC_UID", script)


if __name__ == "__main__":
    unittest.main()
