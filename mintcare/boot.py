"""Boot-time analysis via systemd-analyze, with advice for services that are safe to disable."""

import re

from .i18n import _
from .util import out, run, unit_enabled

# Services MintCare may offer to disable, with what you lose by doing so.
SAFE_TO_DISABLE = {
    "NetworkManager-wait-online.service":
        "Holds up boot until the network is up. Only needed for network shares mounted from /etc/fstab.",
    "systemd-networkd-wait-online.service":
        _("Holds up boot until the network is up. Only needed for network shares mounted at boot."),
    "ModemManager.service": _("Mobile-broadband (3G/4G/5G) modem support. Not needed without a cellular modem."),
    "cups-browsed.service": _("Auto-discovers network printers. Printers you have already added keep working."),
    "smartmontools.service":
        _("smartd background disk monitor. MintCare's Hardware page and scan read SMART data on demand, "
        "but you lose smartd's continuous logging of early failure signs."),
    "ollama.service": "Local AI model server. Start it when needed with: systemctl start ollama",
    "docker.service": _("Docker engine. If docker.socket stays enabled it still starts on first use."),
    "containerd.service": _("Container runtime used by Docker; started on demand by Docker."),
    "kerneloops.service": _("Sends kernel crash reports to kerneloops.org."),
    "teamviewerd.service": _("TeamViewer remote-access daemon. TeamViewer can start it when launched."),
    "anydesk.service": _("AnyDesk remote-access daemon."),
    "e2scrub_reap.service": _("Cleans up after ext4 online checks. Harmless to disable."),
    "libvirtd.service": _("Virtual-machine manager. Socket activation starts it when virt-manager opens."),
    "apache2.service": _("Apache web server, often pulled in as a dependency."),
    "nginx.service": _("nginx web server."),
    "mysql.service": _("MySQL database server."),
    "mariadb.service": _("MariaDB database server."),
    "postgresql.service": _("PostgreSQL database server."),
    "postfix.service": _("Postfix mail server, rarely needed on a desktop."),
    "lvm2-monitor.service": _("LVM monitoring. Only needed if you use LVM volumes."),
}

MOUNT_ADVICE = ("A drive mounted from /etc/fstab. Adding the options 'nofail,x-systemd.automount' "
                "mounts it on first access instead of during boot.")


def _secs(tok):
    """'1min 2.5s' / '450ms' / '6.454s' -> seconds."""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)\s*(min|ms|us|s|h)", tok):
        total += float(num) * {"h": 3600, "min": 60, "s": 1, "ms": 1e-3, "us": 1e-6}[unit]
    return total


def times():
    """{'firmware': s, 'loader': s, 'kernel': s, 'initrd': s, 'userspace': s, 'total': s} or {}."""
    o = out(["systemd-analyze", "--no-pager"])
    line = o.splitlines()[0] if o else ""
    res = {}
    for chunk in line.split("=")[0].replace("Startup finished in", "").split("+"):
        m = re.match(r"\s*(.+?)\s*\((\w+)\)", chunk)
        if m:
            res[m.group(2)] = _secs(m.group(1))
    if "=" in line:
        res["total"] = _secs(line.split("=")[1])
    return res


def critical_chain():
    """Units on the boot critical path with their own start time: {unit: seconds}."""
    o = out(["systemd-analyze", "critical-chain", "--no-pager"], timeout=20)
    chain = {}
    for line in o.splitlines():
        m = re.search(r"([\w@.\\-]+\.(?:service|mount|target|socket|device))\s+@[^+]+\+(.+)$", line.strip(" └─│├`|-"))
        if m:
            chain[m.group(1)] = _secs(m.group(2))
    return chain


def online_wanters():
    """Units that pull in network-online.target (which re-enables wait-online)."""
    o = out(["systemctl", "list-dependencies", "--reverse", "--plain", "--no-pager", "network-online.target"])
    return [l.strip() for l in o.splitlines()[1:] if l.strip() and not l.strip().endswith(".target")]


def description(unit):
    return out(["systemctl", "show", "-p", "Description", "--value", unit]) or ""


def blame(limit=14):
    rc, o = run(["systemd-analyze", "blame", "--no-pager"], timeout=20)
    if rc != 0:
        return []
    chain = critical_chain()
    rows = []
    for line in o.splitlines():
        parts = line.strip().rsplit(None, 1)
        if len(parts) != 2:
            continue
        t, unit = parts
        rows.append(unit_row(unit, _secs(t), unit in chain))
        if len(rows) >= limit:
            break
    return rows


def unit_row(unit, secs, critical):
    advice = SAFE_TO_DISABLE.get(unit)
    if unit.endswith(".mount"):
        advice = advice or MOUNT_ADVICE
    enabled = unit_enabled(unit) if unit in SAFE_TO_DISABLE else None
    if enabled is False and unit.endswith("wait-online.service"):
        who = online_wanters()
        advice = (_("Already disabled, but still started because ") +
                  (", ".join(who) if who else _("another service")) +
                  (" wait" if len(who) > 1 else " waits") + " for network-online.target.")
    return {
        "unit": unit, "secs": secs, "critical": critical,
        "desc": description(unit), "advice": advice,
        "can_toggle": unit in SAFE_TO_DISABLE,
        "enabled": enabled,
    }


UNIT_MARKERS = "/var/lib/mintcare/units"


def disable_script(unit):
    # the marker lets "Undo all" re-enable exactly the services MintCare turned off
    return f"systemctl disable --now {unit} && mkdir -p {UNIT_MARKERS} && touch {UNIT_MARKERS}/{unit}"


def enable_script(unit):
    return f"systemctl enable --now {unit} && rm -f {UNIT_MARKERS}/{unit}"


STARTUP_MSG = "b07a249cd024414a82dd00cd181378ff"      # systemd's "Startup finished" message id


def history(limit=30):
    """Past boots from the journal: [{'ts','kernel','initrd','userspace','firmware','loader','total'}]."""
    import json
    rc, o = run(["journalctl", f"MESSAGE_ID={STARTUP_MSG}", "_PID=1", "-o", "json", "--no-pager", "-q"], timeout=30)
    boots = []
    for line in o.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE", "")
        if not isinstance(msg, str) or "Startup finished" not in msg:
            continue
        parts = {m.group(2): _secs(m.group(1)) for m in re.finditer(r"([\d.]+\s*(?:min\s*)?[\d.]*m?s)\s+\((\w+)\)", msg)}
        tot = re.search(r"=\s*(.+?)\s*\.?$", msg)
        ts = int(e.get("__REALTIME_TIMESTAMP", "0")) / 1e6
        boots.append({"ts": ts, **parts, "total": _secs(tot.group(1)) if tot else sum(parts.values())})
    return boots[-limit:]
