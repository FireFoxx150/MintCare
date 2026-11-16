"""Devices on your network that this PC has talked to recently, from the neighbour (ARP) table.

Nothing is scanned: a device appears once it has exchanged traffic with this PC and drops off after a
while. Devices you have not marked as known are flagged as new. Runs as you; no root needed.
"""

import json
import os
from pathlib import Path

from . import maintenance
from .util import run

KNOWN_FILE = maintenance.STATE / "known-devices.json"
DEAD_STATES = {"FAILED", "INCOMPLETE", "NONE"}


def parse_neigh(text):
    """[{'mac', 'ips', 'dev'}] from `ip -j neigh show`: one entry per device, broadcast and multicast skipped."""
    try:
        entries = json.loads(text or "[]")
    except ValueError:
        return []
    devices = {}
    for e in entries:
        mac = (e.get("lladdr") or "").lower()
        states = e.get("state") or []
        states = [states] if isinstance(states, str) else states
        try:
            group_bit = int(mac.split(":")[0], 16) & 1
        except ValueError:
            continue
        if not mac or group_bit or DEAD_STATES & set(states):
            continue
        dev = devices.setdefault(mac, {"mac": mac, "ips": [], "dev": e.get("dev", "")})
        if e.get("dst") and e["dst"] not in dev["ips"]:
            dev["ips"].append(e["dst"])
    return sorted(devices.values(), key=lambda d: d["ips"][0] if d["ips"] else "")


def physical(dev):
    """True for a real network adapter. Virtual bridges and tunnels (Docker, VPNs) have no hardware behind them."""
    return bool(dev) and "/" not in dev and os.path.exists(f"/sys/class/net/{dev}/device")


def neighbours():
    rc, o = run(["ip", "-j", "neigh", "show"], timeout=10)
    return [d for d in parse_neigh(o) if physical(d["dev"])] if rc == 0 else []


def known(path=None):
    """The MAC addresses you have marked as known."""
    try:
        return set(json.loads(Path(path or KNOWN_FILE).read_text()))
    except (OSError, ValueError):
        return set()


def mark_known(macs, path=None):
    path = Path(path or KNOWN_FILE)
    allowed = known(path) | {m.lower() for m in macs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(allowed)))
