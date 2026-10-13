"""Listening network services: what is reachable from other computers, and whether the
firewall is in front of it. Read with `ss` (no root); root only reveals extra process names.
"""

import re
from dataclasses import dataclass

from .util import read, run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"

# port: (what it is, systemd unit that provides it or None, risky if exposed)
KNOWN = {
    22: ("SSH remote login", "ssh.service", True),
    21: ("FTP server", "vsftpd.service", True),
    23: ("Telnet (unencrypted remote login)", None, True),
    53: ("DNS resolver", None, False),
    67: ("DHCP server", None, False),
    68: ("DHCP client (gets your IP address)", None, False),
    80: ("Web server", "apache2.service", False),
    111: ("RPC portmapper (NFS)", "rpcbind.service", True),
    139: ("Windows file sharing (Samba)", "smbd.service", True),
    443: ("Web server (HTTPS)", None, False),
    445: ("Windows file sharing (Samba)", "smbd.service", True),
    546: ("DHCPv6 client", None, False),
    631: ("Printing (CUPS)", "cups.service", False),
    1716: ("KDE Connect (phone integration)", None, False),
    1900: ("UPnP / SSDP device discovery", None, False),
    2049: ("NFS file sharing", "nfs-server.service", True),
    3306: ("MySQL / MariaDB database", "mysql.service", True),
    3389: ("Remote desktop (RDP)", "xrdp.service", True),
    5353: ("mDNS / Avahi (finds printers and devices)", "avahi-daemon.service", False),
    5355: ("LLMNR name resolution", None, False),
    5432: ("PostgreSQL database", "postgresql.service", True),
    5900: ("VNC remote desktop", None, True),
    6379: ("Redis database", "redis-server.service", True),
    8080: ("Web server / proxy", None, False),
    11434: ("Ollama AI model server", "ollama.service", True),
    27017: ("MongoDB database", "mongod.service", True),
    42000: ("Warpinator file transfer", None, False),
    42001: ("Warpinator file transfer", None, False),
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


def services(items=None, root_names=None):
    """Group by (proto, port) and assess each: [{'port','proto','name','process','scope','level','advice','unit'}]."""
    items = listeners() if items is None else items
    root_names = root_names or {}
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
        label = name or (f"{g['process']}" if g["process"] else "Unknown service")
        if scope != "network":
            level, advice = OK, ("Only reachable from this computer." if scope == "local"
                                 else "Only reachable on your local network segment.")
        elif g.get("ephemeral"):
            level, advice = OK, "Temporary port used for outgoing traffic (calls, browsing, games)."
        elif fw:
            level = INFO if risky or (not name and not g["process"] and proto == "tcp") else OK
            advice = ("Open to the network, but the firewall blocks it unless you added an allow rule."
                      + (" Turn it off if you don't use it." if risky else "")
                      + (" Use Identify to see which program it is." if not name and not g["process"] else ""))
        else:
            level = WARN if risky or not name else INFO
            advice = "Reachable by other computers and the firewall is off."
        out.append({"proto": proto, "port": port, "name": label, "process": g["process"], "scope": scope,
                    "level": level, "advice": advice,
                    "unit": unit if scope == "network" and risky else None,   # never offer to kill e.g. Avahi
                    "addresses": sorted(g["addresses"]), "known": bool(name)})
    return out


def root_scan_step():
    return ("ports-root", "Read listening services and firewall rules",
            "echo '@@SS'; ss -tulnpH; echo '@@UFW'; ufw status verbose 2>&1 || true")


def parse_root_scan(lines):
    """From the admin scan output: ({(proto, port): process}, [ufw status lines])."""
    names, ufw, section = {}, [], None
    for line in lines:
        if line.strip() == "@@SS":
            section = "ss"
            continue
        if line.strip() == "@@UFW":
            section = "ufw"
            continue
        if section == "ss":
            for li in parse_ss(line):
                if li.process:
                    names[(li.proto, li.port)] = li.process
        elif section == "ufw" and line.strip():
            ufw.append(line.rstrip())
    return names, ufw
