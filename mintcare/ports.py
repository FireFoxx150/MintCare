"""Listening network services: what is reachable from other computers, and whether the
firewall is in front of it. Read with `ss` (no root); root only reveals extra process names.
"""

import re
from dataclasses import dataclass

from .i18n import _
from .util import out, read, run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"

# port: (what it is, systemd unit that provides it or None, risky if exposed)
KNOWN = {
    22: (_("SSH remote login"), "ssh.service", True),
    21: (_("FTP server"), "vsftpd.service", True),
    23: (_("Telnet (unencrypted remote login)"), None, True),
    53: (_("DNS resolver"), None, False),
    67: (_("DHCP server"), None, False),
    68: (_("DHCP client (gets your IP address)"), None, False),
    80: (_("Web server"), "apache2.service", False),
    111: (_("RPC portmapper (NFS)"), "rpcbind.service", True),
    139: (_("Windows file sharing (Samba)"), "smbd.service", True),
    443: (_("Web server (HTTPS)"), None, False),
    445: (_("Windows file sharing (Samba)"), "smbd.service", True),
    546: ("DHCPv6 client", None, False),
    631: ("Printing (CUPS)", "cups.service", False),
    1716: (_("KDE Connect (phone integration)"), None, False),
    1900: (_("UPnP / SSDP device discovery"), None, False),
    2049: (_("NFS file sharing"), "nfs-server.service", True),
    3306: (_("MySQL / MariaDB database"), "mysql.service", True),
    3389: (_("Remote desktop (RDP)"), "xrdp.service", True),
    5353: (_("mDNS / Avahi (finds printers and devices)"), "avahi-daemon.service", False),
    5355: (_("LLMNR name resolution"), None, False),
    5432: (_("PostgreSQL database"), "postgresql.service", True),
    5900: (_("VNC remote desktop"), None, True),
    6379: (_("Redis database"), "redis-server.service", True),
    8080: (_("Web server / proxy"), None, False),
    11434: (_("Ollama AI model server"), "ollama.service", True),
    27017: (_("MongoDB database"), "mongod.service", True),
    42000: (_("Warpinator file transfer"), None, False),
    42001: (_("Warpinator file transfer"), None, False),
}


@dataclass
class Listener:
    proto: str
    address: str
    port: int
    process: str = ""
    pid: int = 0

    @property
    def scope(self):
        a = self.address.strip("[]")
        if a.startswith("127.") or a in ("::1",) or "%lo" in self.address:
            return "local"
        if a.lower().startswith("fe80"):
            return "link"                 # IPv6 link-local: same network segment only
        return "network"

    @property
    def ephemeral(self):
        # Browsers, games and calls open temporary high UDP ports for outgoing traffic.
        return self.proto == "udp" and self.port >= 32768 and self.port not in KNOWN

    @property
    def info(self):
        return KNOWN.get(self.port, (None, None, False))


def parse_ss(text):
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        proto, local = f[0], f[4]
        addr, _x, port = local.rpartition(":")
        if not port.isdigit():
            continue
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        out.append(Listener(proto, addr, int(port), m.group(1) if m else "", int(m.group(2)) if m else 0))
    return out


def listeners():
    rc, o = run(["ss", "-tulnpH"])
    return parse_ss(o) if rc == 0 else []


def firewall_enabled():
    return "ENABLED=yes" in read("/etc/ufw/ufw.conf").replace(" ", "")


def owner(exe):
    """(package, summary) that installed this program, or (None, None)."""
    if not exe:
        return None, None
    rc, o = run(["dpkg", "-S", exe])
    if rc != 0:                      # /usr/bin vs /bin on merged-/usr systems
        alt = exe[4:] if exe.startswith("/usr/") else "/usr" + exe
        rc, o = run(["dpkg", "-S", alt])
    if rc != 0 or ":" not in o:
        return None, None
    pkg = o.split(":", 1)[0].split(",")[0].strip()
    return pkg, out(["dpkg-query", "-W", "-f", "${binary:Summary}", pkg])


def explain(g, fw, owner_of=owner):
    """(level, advice) for a network-facing listener MintCare doesn't know, from the admin scan's details."""
    exe, unit = g.get("exe", ""), g.get("unit", "")
    pkg, summary = owner_of(exe)
    started = _(" Started by {0}.").format(unit) if unit else ""
    if not pkg:
        return WARN, (_("{0} isn't from any installed package.").format(exe) + started +
                      _(" If you don't recognise it, find out what installed it before keeping it."))
    what = _("From the {0} package ({1}).").format(pkg, summary) if summary else _("From the {0} package.").format(pkg)
    keep = (_(" The firewall blocks it from other computers, so it's fine to keep.") if fw else
            _(" Other computers can reach it: turn on the firewall, or turn the service off if you don't use it."))
    return (OK if fw else WARN), what + started + keep


def services(items=None, root_names=None, root_details=None, owner_of=owner):
    """Group by (proto, port) and assess each: [{'port','proto','name','process','scope','level','advice','unit'}]."""
    items = listeners() if items is None else items
    root_names = root_names or {}
    root_details = root_details or {}
    fw = firewall_enabled()
    groups = {}
    for li in items:
        key = (li.proto, li.port)
        g = groups.setdefault(key, {"proto": li.proto, "port": li.port, "scopes": set(), "process": "",
                                    "addresses": set()})
        g["scopes"].add(li.scope)
        g["addresses"].add(li.address)
        g["process"] = g["process"] or li.process or root_names.get(key, "")
        g["ephemeral"] = li.ephemeral
    out = []
    for (proto, port), g in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        name, unit, risky = KNOWN.get(port, (None, None, False))
        scope = "network" if "network" in g["scopes"] else "link" if "link" in g["scopes"] else "local"
        label = name or (f"{g['process']}" if g["process"] else _("Unknown service"))
        if scope != "network":
            level, advice = OK, (_("Only reachable from this computer.") if scope == "local"
                                 else _("Only reachable on your local network segment."))
        elif g.get("ephemeral"):
            level, advice = OK, _("Temporary port used for outgoing traffic (calls, browsing, games).")
        elif fw:
            level = INFO if risky or (not name and not g["process"] and proto == "tcp") else OK
            advice = (_("Open to the network, but the firewall blocks it unless you added an allow rule.")
                      + (_(" Turn it off if you don't use it.") if risky else "")
                      + (_(" Use Identify to see which program it is.") if not name and not g["process"] else ""))
        else:
            level = WARN if risky or not name else INFO
            advice = _("Reachable by other computers and the firewall is off.")
        if scope == "network" and not name and not g.get("ephemeral") and (proto, port) in root_details:
            level, advice = explain(root_details[(proto, port)], fw, owner_of)
        out.append({"proto": proto, "port": port, "name": label, "process": g["process"], "scope": scope,
                    "level": level, "advice": advice,
                    "unit": unit if scope == "network" and risky else None,   # never offer to kill e.g. Avahi
                    "addresses": sorted(g["addresses"]), "known": bool(name)})
    return out


def root_scan_step():
    # For each listening process: its program and the systemd service that started it
    proc = ("for pid in $(ss -tulnpH | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do "
            "printf '%s\\t%s\\t%s\\n' \"$pid\" \"$(readlink -f /proc/$pid/exe 2>/dev/null)\" "
            "\"$(grep -o '[^/]*\\.service$' /proc/$pid/cgroup 2>/dev/null | head -1)\"; done")
    return ("ports-root", _("Read listening services and firewall rules"),
            f"echo '@@SS'; ss -tulnpH; echo '@@PROC'; {proc}; echo '@@UFW'; ufw status verbose 2>&1 || true")


def parse_root_scan(lines):
    """From the admin scan output: ({(proto, port): process}, {(proto, port): {'exe', 'unit'}}, [ufw lines])."""
    names, pids, procs, ufw, section = {}, {}, {}, [], None
    for line in lines:
        if line.strip() in ("@@SS", "@@PROC", "@@UFW"):
            section = line.strip()[2:].lower()
            continue
        if section == "ss":
            for li in parse_ss(line):
                if li.process:
                    names[(li.proto, li.port)] = li.process
                    pids[(li.proto, li.port)] = li.pid
        elif section == "proc":
            f = line.rstrip("\n").split("\t")
            if len(f) == 3 and f[0].isdigit():
                procs[int(f[0])] = {"exe": f[1], "unit": f[2]}
        elif section == "ufw" and line.strip():
            ufw.append(line.rstrip())
    details = {k: procs[p] for k, p in pids.items() if p in procs}
    return names, details, ufw
