"""The firewall keeps SSH reachable when an SSH server runs, so turning it on cannot cut off remote login.
The commands are stubbed on PATH: no firewall rule changes and nothing runs as root."""
import os
import subprocess
import unittest
from pathlib import Path

from mintcare import tweaks
from tests.helpers import TempDirTest

# "systemctl is-active --quiet <unit>" succeeds only for SSH units, and only when SSH_RUNNING=1
SYSTEMCTL = '#!/bin/sh\n[ "$SSH_RUNNING" = 1 ] && case "$3" in ssh*) exit 0;; esac\nexit 3\n'
UFW = '#!/bin/sh\necho "ufw $*" >> "$UFW_LOG"\n'


class FirewallRules(TempDirTest):
    def run_rules(self, ssh_running):
        bin_dir = Path(self.tmp) / "bin"
        bin_dir.mkdir()
        for name, text in (("systemctl", SYSTEMCTL), ("ufw", UFW)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        log = Path(self.tmp) / "ufw.log"
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", UFW_LOG=str(log),
                   SSH_RUNNING="1" if ssh_running else "0")
        done = subprocess.run(["bash", "-c", tweaks.UFW_RULES], env=env, capture_output=True, text=True, check=True)
        return log.read_text().splitlines(), done.stdout

    def test_ssh_is_allowed_before_the_firewall_turns_on(self):
        rules, shown = self.run_rules(ssh_running=True)
        self.assertEqual(rules, ["ufw default deny incoming", "ufw default allow outgoing",
                                 "ufw allow ssh", "ufw --force enable"])
        self.assertIn("SSH is running", shown)

    def test_without_an_ssh_server_no_port_is_opened(self):
        rules, shown = self.run_rules(ssh_running=False)
        self.assertEqual(rules, ["ufw default deny incoming", "ufw default allow outgoing", "ufw --force enable"])
        self.assertNotIn("SSH is running", shown)


if __name__ == "__main__":
    unittest.main()
