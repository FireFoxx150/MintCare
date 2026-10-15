"""Login security (failed logins, SSH settings, AppArmor, auto-login, screen lock) and malware scans."""

import glob
import re
import subprocess
from pathlib import Path

from .i18n import _
from .util import has_cmd, out, pkg_installed, q, read, run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


def failed_logins(days=7):
    """{service: count} of failed password attempts in the last N days (from the journal)."""
    rc, o = run(["journalctl", "--since", f"-{days} days", "-q", "--no-pager", "-o", "cat"], timeout=40)
    counts = {}
    for line in o.splitlines():
        m = re.search(r"pam_unix\(([^:]+):auth\): authentication failure", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        elif "Failed password for" in line or "Invalid user" in line:
            counts["ssh"] = counts.get("ssh", 0) + 1
    return counts


def _sshd_settings():
    text = read("/etc/ssh/sshd_config")
    for f in sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf")):
        text = read(f) + "\n" + text          # drop-ins are read first; the first value wins
    vals = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.lower().startswith("match"):
            continue
        k, _x, v = line.partition(" ")
        vals.setdefault(k.lower(), v.strip())
    return vals


def audit():
    """[(level, title, text, action)] for the Security page."""
    items = []
    fails = failed_logins()
    total = sum(fails.values())
    where = ", ".join(f"{n}× {svc}" for svc, n in sorted(fails.items(), key=lambda kv: -kv[1]))
    if fails.get("ssh", 0) >= 10:
        items.append((WARN, _("Failed logins"), _('{0} failed SSH logins in 7 days — someone may be guessing passwords').format(fails['ssh']), None))
    elif total:
        items.append((INFO, _("Failed logins"), _('{0} wrong password(s) in 7 days ({1}). Usually just typos at the lock screen.').format(total, where), None))
    else:
        items.append((OK, _("Failed logins"), _("No failed password attempts in the last 7 days"), None))

    if pkg_installed("openssh-server"):
        s = _sshd_settings()
        root = s.get("permitrootlogin", "prohibit-password").lower()
        pw = s.get("passwordauthentication", "yes").lower()
        active = out(["systemctl", "is-active", "ssh"]) == "active" or out(["systemctl", "is-active", "ssh.socket"]) == "active"
        if root == "yes":
            items.append((WARN, _("SSH server"), _("Allows root to log in with a password"), "ssh-harden"))
        elif pw == "yes" and active:
            items.append((INFO, _("SSH server"), _("Running and accepts passwords. Keys are safer; "
                                              "make sure your password is strong."), "ssh-harden"))
        else:
            items.append((OK, _("SSH server"), _('{0}; root login {1}, passwords {2}').format(_('Running') if active else _('Installed but stopped'), root, pw), None))
    else:
        items.append((OK, _("SSH server"), _("Not installed, so nobody can log in remotely"), None))

    aa = read("/sys/module/apparmor/parameters/enabled") == "Y"
    items.append((OK if aa else WARN, "AppArmor", _("Enabled: apps with profiles are confined") if aa else
                  _("Disabled: apps aren't confined by AppArmor profiles"), None))

    auto = ""
    for f in glob.glob("/etc/lightdm/lightdm.conf") + glob.glob("/etc/lightdm/lightdm.conf.d/*.conf"):
        m = re.search(r"^\s*autologin-user\s*=\s*(\S+)", read(f), re.M)
        if m:
            auto = m.group(1)
    items.append((INFO if auto else OK, _("Automatic login"),
                  _('{0} is logged in automatically — anyone who switches the PC on gets your desktop').format(auto) if auto
                  else _("Off — a password is needed at start-up"), None))

    lock = out(["gsettings", "get", "org.cinnamon.desktop.screensaver", "lock-enabled"]) == "true"
    delay = out(["gsettings", "get", "org.cinnamon.desktop.session", "idle-delay"]).split()[-1:]
    mins = int(delay[0]) // 60 if delay and delay[0].isdigit() else 0
    if not lock:
        items.append((WARN, _("Screen lock"), _("The screen doesn't lock when you're away"), "lock-on"))
    elif mins == 0 or mins > 30:
        items.append((INFO, _("Screen lock"), _('Locks only after {0} idle').format(_('never') if mins == 0 else _('{0} min').format(mins)),
                      "lock-on"))
    else:
        items.append((OK, _("Screen lock"), _('Locks after {0} minutes idle').format(mins), None))

    admins = [u for u in out(["getent", "group", "sudo"]).split(":")[-1].split(",") if u]
    items.append((OK if len(admins) <= 2 else INFO, "Administrators", f"{len(admins)} account(s) can use sudo: "
                  + ", ".join(admins), None))
    return items


SSH_HARDEN = """mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-mintcare.conf <<'EOF'
# Managed by MintCare
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
EOF
systemctl reload ssh 2>/dev/null || systemctl restart ssh"""


def lock_screen_on():
    run(["gsettings", "set", "org.cinnamon.desktop.screensaver", "lock-enabled", "true"])
    d = out(["gsettings", "get", "org.cinnamon.desktop.session", "idle-delay"]).split()[-1:]
    if not d or not d[0].isdigit() or int(d[0]) == 0 or int(d[0]) > 1800:
        run(["gsettings", "set", "org.cinnamon.desktop.session", "idle-delay", "600"])


# ------------------------------------------------------------------ malware scanning

def scanners():
    return {"clamav": has_cmd("clamscan"), "rkhunter": has_cmd("rkhunter")}


def install_step(name):
    pkgs = {"clamav": "clamav clamav-freshclam", "rkhunter": "rkhunter"}[name]
    return (f"install-{name}", f"Install {name}",
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}\n"
            + ("systemctl stop clamav-freshclam 2>/dev/null; freshclam || true; "
               "systemctl start clamav-freshclam 2>/dev/null || true" if name == "clamav" else
               "rkhunter --propupd --nocolors || true"))


def clam_scan(paths, on_line=lambda line: None, cancel=lambda: False):
    """Run clamscan as you. Returns {'infected': [(path, virus)], 'scanned', 'rc'}."""
    args = ["clamscan", "-r", "-i", "--no-summary", "--exclude-dir=^/proc", "--exclude-dir=/\\.cache/", *paths]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    infected = []
    for line in proc.stdout:
        if cancel():
            proc.terminate()
            break
        line = line.rstrip()
        m = re.match(r"(.+): (.+) FOUND$", line)
        if m:
            infected.append((m.group(1), m.group(2)))
        on_line(line)
    rc = proc.wait()
    return {"infected": infected, "rc": rc, "cancelled": cancel()}


def rkhunter_step():
    return ("rkhunter", _("Rootkit check (rkhunter)"), "rkhunter --check --sk --rwo --nocolors 2>&1 || true")


def quarantine_step(paths):
    """Move infected files into /var/lib/mintcare/quarantine (root-only, not executable)."""
    lines = ["mkdir -p /var/lib/mintcare/quarantine", "chmod 700 /var/lib/mintcare/quarantine"]
    for p in paths:
        lines.append(f"mv -- {q(p)} /var/lib/mintcare/quarantine/ && echo 'Quarantined {p}'")
    lines.append("chmod -R a-x /var/lib/mintcare/quarantine/* 2>/dev/null || true")
    return ("quarantine", f"Quarantine {len(paths)} file(s)", "\n".join(lines))
