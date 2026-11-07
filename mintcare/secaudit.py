"""Login security (failed logins, SSH settings, AppArmor, auto-login, screen lock) and malware scans."""

import glob
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from .i18n import _, ngettext
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


EFI_SECURE_BOOT = Path("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")
SETUID_DIRS = ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/libexec", "/bin", "/sbin")


def parse_secure_boot(data):
    """The SecureBoot EFI variable is 4 attribute bytes, then the value: 1 when Secure Boot is on."""
    return data[4] == 1 if len(data) >= 5 else None


def secure_boot(path=EFI_SECURE_BOOT):
    """True or False from the firmware; None without UEFI, or when the variable can't be read."""
    try:
        return parse_secure_boot(path.read_bytes())
    except OSError:
        return None


def setuid_programs(folders=SETUID_DIRS):
    """Program files (not links) that run with their owner's rights: set-user-ID or set-group-ID."""
    found = []
    for top in dict.fromkeys(os.path.realpath(d) for d in folders):     # /bin is a link to /usr/bin on Mint
        for root, _dirs, names in os.walk(top):
            for name in names:
                path = os.path.join(root, name)
                try:
                    mode = os.lstat(path).st_mode
                except OSError:
                    continue
                if stat.S_ISREG(mode) and mode & (stat.S_ISUID | stat.S_ISGID):
                    found.append(path)
    return sorted(found)


def system_items():
    """[(level, title, text)] for the system part of the Security page."""
    items = []
    sb = secure_boot()
    if sb is None:
        items.append((INFO, "Secure Boot", _("Unknown: the firmware doesn't report it (no UEFI, or the setting can't be read)")))
    elif sb:
        items.append((OK, "Secure Boot", _("On: the firmware starts only signed boot software")))
    else:
        items.append((INFO, "Secure Boot", _("Off: the firmware starts any boot software. Turn it on in the firmware "
                                             "settings if your PC supports it.")))
    progs = setuid_programs()
    if progs:
        names = ", ".join(os.path.basename(p) for p in progs[:12]) + (", …" if len(progs) > 12 else "")
        items.append((INFO, _("Programs with extra rights"), ngettext(
            "{0} program runs with its owner's rights, whoever starts it: {1}",
            "{0} programs run with their owner's rights, whoever starts them: {1}", len(progs)).format(len(progs), names)))
    else:
        items.append((OK, _("Programs with extra rights"), _("No programs with extra rights were found")))
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

LYNIS_SUMMARY = "/var/lib/mintcare/lynis-summary"   # the score and counts only; the Security page reads it


def scanners():
    return {"clamav": has_cmd("clamscan"), "rkhunter": has_cmd("rkhunter"), "lynis": has_cmd("lynis")}


def install_step(name):
    pkgs = {"clamav": "clamav clamav-freshclam", "rkhunter": "rkhunter", "lynis": "lynis"}[name]
    return (f"install-{name}", f"Install {name}",
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}\n"
            + ("systemctl stop clamav-freshclam 2>/dev/null; freshclam || true; "
               "systemctl start clamav-freshclam 2>/dev/null || true" if name == "clamav" else
               "rkhunter --propupd --nocolors || true" if name == "rkhunter" else "true"))


LYNIS_REPORT = "/var/log/lynis-report.dat"
LYNIS_ITEMS = "/var/lib/mintcare/lynis-items"       # each finding; readable only by the user who ran the audit
# Words that suggest acting on a finding could lock you out or break something. A guide, not a guarantee.
# "pam" alone also matches libpam-tmpdir, which is harmless, so match the PAM module names instead.
LYNIS_RISKY = ("ssh", "firewall", "ufw", "iptables", "nftables", "grub", "boot", "fstab", "noexec", "nodev",
               "nosuid", "pam_", "pam module", "password", "pwquality", "sudo", "shadow", "umask", "login.defs",
               "apparmor", "selinux", "ipv6", "sysctl", "compiler", "namespace", "unprivileged", "remove", "purge",
               "uninstall", "disable", "deactivate")


def lynis_risk(text):
    """True when a finding's wording suggests that acting on it could lock you out or break something."""
    low = text.lower()
    return any(word in low for word in LYNIS_RISKY)


def lynis_items_command(report=LYNIS_REPORT, out=LYNIS_ITEMS):
    """Shell command: each finding as kind, test id and description (the details column is left out), tab-separated."""
    program = '/^(warning|suggestion)\\[\\]=/ { split($1, a, /\\[\\]=/); printf "%s\\t%s\\t%s\\n", a[1], a[2], $2 }'
    return f"awk -F'|' {q(program)} {q(report)} > {q(out)}"


def lynis_step():
    """Root step: a read-only Lynis audit. The hardening index and counts go to LYNIS_SUMMARY (readable by everyone);
    the findings go to LYNIS_ITEMS, which belongs to the user who ran the audit."""
    tmp = LYNIS_ITEMS + ".tmp"
    return ("lynis", _("Hardening audit (Lynis)"),
            f"rm -f {LYNIS_SUMMARY} {LYNIS_ITEMS} {tmp}\n"
            "lynis audit system --quick --no-colors >/dev/null 2>&1 || true\n"
            "install -d -m 755 /var/lib/mintcare\n"
            f"idx=$(grep -m1 '^hardening_index=' {LYNIS_REPORT} 2>/dev/null | cut -d= -f2)\n"
            f"warn=$(grep -c -F 'warning[]=' {LYNIS_REPORT} 2>/dev/null || true)\n"
            f"sugg=$(grep -c -F 'suggestion[]=' {LYNIS_REPORT} 2>/dev/null || true)\n"
            f"[ -n \"$idx\" ] && printf 'hardening_index=%s\\nwarnings=%s\\nsuggestions=%s\\n' "
            f"\"$idx\" \"$warn\" \"$sugg\" > {LYNIS_SUMMARY} && chmod 644 {LYNIS_SUMMARY}\n"
            f"if [ -r {LYNIS_REPORT} ]; then\n"
            f"{lynis_items_command(LYNIS_REPORT, tmp)} && "
            "{ [ -z \"$PKEXEC_UID\" ] || chown \"$PKEXEC_UID\" " + tmp + "; } && "
            f"chmod 600 {tmp} && mv -f {tmp} {LYNIS_ITEMS}\n"
            "fi\n")


def read_lynis_items(path=LYNIS_ITEMS):
    """[(kind, test id, description, may break the system)] from the last audit: [] if it found nothing,
    None if there is no list or it can't be read."""
    try:
        rows = []
        for line in Path(path).read_text().splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                rows.append((parts[0], parts[1], parts[2], lynis_risk(parts[2])))
        return rows
    except OSError:
        return None


def read_lynis_summary(path=LYNIS_SUMMARY):
    """(hardening index, warnings, suggestions) from the last Lynis audit, or None if there has been none."""
    try:
        data = dict(line.split("=", 1) for line in Path(path).read_text().splitlines() if "=" in line)
        return int(data["hardening_index"]), int(data["warnings"]), int(data["suggestions"])
    except (OSError, KeyError, ValueError):
        return None


def scan_files(paths):
    """Regular files under the folders in `paths` that clamscan may scan, and how many names were left out.

    Links and .cache folders are skipped. A name with a control character (a line break, say) is left out
    too: clamscan prints one line per file, so such a name could make the output show another path.
    """
    files, skipped = [], 0
    for top in paths:
        for root, dirs, names in os.walk(top):
            dirs[:] = [d for d in dirs if d != ".cache"]
            for name in names:
                full = os.path.join(root, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                if any(ord(c) < 32 or ord(c) == 127 for c in full):
                    skipped += 1
                    continue
                files.append(full)
    return files, skipped


def clam_scan(paths, on_line=lambda line: None, cancel=lambda: False):
    """Run clamscan as you over the folders in `paths`.

    Returns {'infected': [(path, virus)], 'scanned', 'skipped', 'rc', 'cancelled'}. clamscan gets an explicit
    file list (see scan_files), and a line counts as an infection only when its path is one of those files.
    """
    files, skipped = scan_files(paths)
    if not files:
        return {"infected": [], "scanned": 0, "skipped": skipped, "rc": 0, "cancelled": cancel()}
    fd, listing = tempfile.mkstemp(prefix="mintcare-scan-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(b"\n".join(os.fsencode(p) for p in files) + b"\n")
        proc = subprocess.Popen(["clamscan", "-i", "--no-summary", f"--file-list={listing}"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                bufsize=1)
        known, infected = set(files), []
        for line in proc.stdout:
            if cancel():
                proc.terminate()
                break
            line = line.rstrip()
            m = re.match(r"(.+): (.+) FOUND$", line)
            if m and m.group(1) in known:
                infected.append((m.group(1), m.group(2)))
            on_line(line)
        rc = proc.wait()
    finally:
        os.remove(listing)
    return {"infected": infected, "scanned": len(files), "skipped": skipped, "rc": rc, "cancelled": cancel()}


def rkhunter_step():
    return ("rkhunter", _("Rootkit check (rkhunter)"), "rkhunter --check --sk --rwo --nocolors 2>&1 || true")


def quarantine_step(paths, home=None):
    """Move infected files into /var/lib/mintcare/quarantine (root-only, not executable).

    The root step checks each path again after the password prompt: only a plain file under your home
    folder is moved, so a folder swapped for a link in the meantime cannot send the move elsewhere.
    """
    home = os.path.realpath(home or os.path.expanduser("~"))
    lines = ["mkdir -p /var/lib/mintcare/quarantine", "chmod 700 /var/lib/mintcare/quarantine"]
    for p in paths:
        plain = (f"[ -f {q(p)} ] && [ ! -L {q(p)} ] && "
                 f'case "$(realpath -e -- {q(p)})" in {q(home)}/*) true;; *) false;; esac')
        lines.append(f"if {plain}; then mv -- {q(p)} /var/lib/mintcare/quarantine/ && echo {q('Quarantined ' + p)}; "
                     f"else echo {q('Skipped, not a plain file in your home folder: ' + p)}; fi")
    lines.append("chmod -R a-x /var/lib/mintcare/quarantine/* 2>/dev/null || true")
    return ("quarantine", f"Quarantine {len(paths)} file(s)", "\n".join(lines))
