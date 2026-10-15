"""Troubleshooters: sound, network, printers, Bluetooth and crash reports.

Reads are unprivileged. Fixes that touch only your session (volume, default output, restarting
PipeWire, per-user default printer) run as you; service restarts run as root.
"""

import json
import re
import socket
import subprocess
import time
from pathlib import Path

from .i18n import _
from .util import has_cmd, out, q, read, run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


# ------------------------------------------------------------------ sound

def audio():
    """{'server', 'default', 'sinks': [...], 'sources': [...], 'issues': [(level, text, action)]}."""
    res = {"server": "", "default": "", "sinks": [], "sources": [], "issues": []}
    if not has_cmd("pactl"):
        res["issues"].append((FAIL, _("pactl isn't installed, so sound can't be checked"), None))
        return res
    info = out(["pactl", "info"])
    m = re.search(r"Server Name: (.+)", info)
    res["server"] = m.group(1) if m else ""
    if not res["server"]:
        res["issues"].append((FAIL, _("The sound server isn't running"), "restart"))
        return res
    res["default"] = out(["pactl", "get-default-sink"])
    for kind in ("sinks", "sources"):
        rc, o = run(["pactl", "-f", "json", "list", kind])
        try:
            items = json.loads(o) if rc == 0 else []
        except ValueError:
            items = []
        for s in items:
            if kind == "sources" and s.get("monitor_source") == "" and ".monitor" in s.get("name", ""):
                continue
            if ".monitor" in s.get("name", ""):
                continue
            vols = [int(str(v.get("value_percent", "0")).rstrip("%")) for v in (s.get("volume") or {}).values()]
            res[kind].append({"name": s["name"], "desc": s.get("description", s["name"]), "mute": s.get("mute"),
                              "volume": max(vols) if vols else 0, "state": s.get("state", ""),
                              "ports": [p.get("description", "") for p in s.get("ports", [])],
                              "active_port": s.get("active_port", "")})
    sinks = res["sinks"]
    if not sinks:
        res["issues"].append((FAIL, _("No sound outputs found — the sound card or driver isn't detected"), "restart"))
    d = next((s for s in sinks if s["name"] == res["default"]), None)
    if d:
        if d["mute"]:
            res["issues"].append((WARN, _('Your default output “{0}” is muted').format(d['desc']), "unmute"))
        elif d["volume"] < 15:
            res["issues"].append((WARN, _('Volume is very low ({0}%)').format(d['volume']), "unmute"))
        elif d["volume"] > 100:
            res["issues"].append((INFO, _('Volume is above 100% ({0}%), which can distort sound').format(d['volume']), "normal"))
    elif sinks:
        res["issues"].append((WARN, _("No default output is set"), "default"))
    return res


def set_default(sink):
    return run(["pactl", "set-default-sink", sink])[0] == 0


def set_volume(sink, percent, unmute=True):
    run(["pactl", "set-sink-volume", sink, f"{int(percent)}%"])
    if unmute:
        run(["pactl", "set-sink-mute", sink, "0"])


def test_sound():
    for cmd in (["canberra-gtk-play", "-i", "bell"],
                ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"]):
        if has_cmd(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


AUDIO_RESTART = "systemctl --user restart wireplumber pipewire pipewire-pulse"
AUDIO_RESET = ("systemctl --user stop wireplumber pipewire pipewire-pulse; "
               "mkdir -p ~/.local/state/wireplumber.mintcare-bak; "
               "cp -a ~/.local/state/wireplumber/. ~/.local/state/wireplumber.mintcare-bak/ 2>/dev/null; "
               "rm -rf ~/.local/state/wireplumber/*; "
               "systemctl --user start pipewire pipewire-pulse wireplumber")


# ------------------------------------------------------------------ network

def _ping(host, count=3):
    rc, o = run(["ping", "-c", str(count), "-W", "2", "-q", host], timeout=15)
    m = re.search(r"(\d+)% packet loss.*?(?:= [\d.]+/([\d.]+)/)?", o, re.S)
    loss = int(m.group(1)) if m else 100
    avg = re.search(r"= [\d.]+/([\d.]+)/", o)
    return loss, float(avg.group(1)) if avg else None


SPEED_HOST = "https://speed.cloudflare.com"


def speed_test(host=SPEED_HOST, seconds=8, cancel=lambda: False):
    """Latency, download and upload against Cloudflare's speed-test endpoints.

    Returns {'ping_ms', 'down_mbps', 'up_mbps'} (None for a part that failed) or raises OSError if
    the server can't be reached at all.
    """
    import http.client
    import urllib.request
    from urllib.parse import urlparse
    ua = {"User-Agent": "MintCare speed test"}

    def get(nbytes, deadline=None):
        t0 = time.monotonic()
        got = 0
        with urllib.request.urlopen(urllib.request.Request(f"{host}/__down?bytes={nbytes}", headers=ua),
                                    timeout=10) as r:
            while not cancel():
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                if deadline and time.monotonic() > deadline:
                    break
        return got, time.monotonic() - t0

    def put(nbytes):
        t0 = time.monotonic()
        req = urllib.request.Request(f"{host}/__up", data=bytes(nbytes), headers=ua, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return nbytes, time.monotonic() - t0

    # Latency over one kept-open connection, so it's a single round trip rather than TCP + TLS setup
    u = urlparse(host)
    conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=10)
    pings = []
    try:
        for _i in range(4):
            t0 = time.monotonic()
            conn.request("GET", "/__down?bytes=0", headers=ua)
            conn.getresponse().read()
            pings.append(time.monotonic() - t0)
    finally:
        conn.close()
    res = {"ping_ms": min(pings[1:]) * 1000, "down_mbps": None, "up_mbps": None}
    try:
        deadline, got, t0 = time.monotonic() + seconds, 0, time.monotonic()
        while time.monotonic() < deadline and got < 100_000_000 and not cancel():   # at most ~100 MB
            got += get(25_000_000, deadline)[0]        # the server refuses single requests of 100 MB or more
        res["down_mbps"] = got * 8 / max(time.monotonic() - t0, 1e-6) / 1e6
    except OSError:
        pass
    try:
        if not cancel():
            n, secs = put(2_000_000)
            if secs < 1.5 and not cancel():                 # fast line: a bigger sample is more accurate
                n, secs = put(20_000_000)
            res["up_mbps"] = n * 8 / max(secs, 1e-6) / 1e6
    except OSError:
        pass
    return res


def network():
    """Interfaces, Wi-Fi details, latency and DNS timing, with issues."""
    res = {"devices": [], "wifi": None, "gateway": "", "latency": {}, "dns_ms": None, "issues": [],
           "nearby": []}
    for line in out(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]).splitlines():
        f = line.split(":")
        if len(f) >= 4 and f[1] in ("ethernet", "wifi"):
            dev = {"name": f[0], "type": f[1], "state": f[2], "connection": f[3]}
            if f[1] == "ethernet":
                sp = read(f"/sys/class/net/{f[0]}/speed")
                dev["speed"] = int(sp) if sp.lstrip("-").isdigit() and int(sp) > 0 else None
            res["devices"].append(dev)
    m = re.search(r"default via (\S+)", out(["ip", "-4", "route", "show", "default"]))
    res["gateway"] = m.group(1) if m else ""
    wifi = next((d for d in res["devices"] if d["type"] == "wifi" and d["state"] == "connected"), None)
    if wifi:
        rows = out(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,CHAN,FREQ,SECURITY", "device", "wifi", "list"], timeout=20)
        nets = []
        for line in rows.splitlines():
            f = re.split(r"(?<!\\):", line)
            if len(f) >= 6:
                nets.append({"active": f[0] == "*", "ssid": f[1].replace("\\:", ":"), "signal": int(f[2] or 0),
                             "chan": int(f[3] or 0), "freq": f[4], "security": f[5]})
        res["nearby"] = nets
        cur = next((n for n in nets if n["active"]), None)
        if cur:
            same = sum(1 for n in nets if not n["active"] and abs(n["chan"] - cur["chan"]) < 3 and n["chan"] <= 14)
            res["wifi"] = dict(cur, crowd=same)
            if cur["signal"] < 40:
                res["issues"].append((WARN, _('Weak Wi-Fi signal ({0}%) — move closer or use 5 GHz').format(cur['signal']), None))
            if cur["chan"] <= 14 and same >= 4:
                res["issues"].append((INFO, _('{0} other networks share channel {1} — a 5 GHz band or another channel on your router may be faster').format(same, cur['chan']), None))
    for d in res["devices"]:
        if d["type"] == "ethernet" and d["state"] == "connected" and d.get("speed") and d["speed"] < 1000:
            res["issues"].append((INFO, _('{0} links at {1} Mb/s — a faulty cable or port can limit gigabit to 100 Mb/s').format(d['name'], d['speed']), None))
    for name, host in (("router", res["gateway"]), ("internet", "1.1.1.1")):
        if host:
            res["latency"][name] = _ping(host)
    r = res["latency"].get("router")
    if r and r[0] > 0:
        res["issues"].append((WARN, _('{0}% packet loss to your router — check Wi-Fi signal or the cable').format(r[0]),
                              "reset"))
    i = res["latency"].get("internet")
    if i and i[0] == 100:
        res["issues"].append((FAIL, _("No internet connection (1.1.1.1 unreachable)"), "reset"))
    t0 = time.monotonic()
    try:
        socket.getaddrinfo(f"mintcare-{int(t0)}.example.com", 80)
    except OSError:
        pass
    res["dns_ms"] = (time.monotonic() - t0) * 1000
    if res["dns_ms"] > 800:
        res["issues"].append((WARN, _('DNS lookups are slow ({0:.0f} ms) — try Optimise › Encrypted DNS').format(res['dns_ms']),
                              "flushdns"))
    return res


NET_RESET = "nmcli networking off; sleep 2; nmcli networking on"
DNS_FLUSH = "resolvectl flush-caches && systemctl restart systemd-resolved"


# ------------------------------------------------------------------ printers

def printers():
    res = {"printers": [], "default": "", "jobs": [], "issues": [], "cups": True}
    rc, o = run(["lpstat", "-p", "-d"])
    if rc != 0 and "not running" in o.lower() + out(["lpstat", "-r"]).lower():
        res["cups"] = False
        res["issues"].append((FAIL, _("The printing service (CUPS) isn't running"), "restart-cups"))
        return res
    for line in o.splitlines():
        m = re.match(r"printer (\S+) (?:is )?(.+?)\.", line)
        if m:
            state = m.group(2)
            res["printers"].append({"name": m.group(1), "state": state,
                                    "disabled": "disabled" in line})
        d = re.match(r"system default destination: (\S+)", line)
        if d:
            res["default"] = d.group(1)
    user_default = re.search(r"^Default (\S+)", read(str(Path.home() / ".cups/lpoptions")), re.M)
    if user_default:
        res["default"] = res["default"] or user_default.group(1)
    for line in out(["lpstat", "-o"]).splitlines():
        f = line.split()
        if f:
            res["jobs"].append({"id": f[0], "user": f[1] if len(f) > 1 else "", "line": line})
    if res["printers"] and not res["default"]:
        res["issues"].append((INFO, _("No default printer is set, so apps may ask every time or pick the wrong one"),
                              "default"))
    for p in res["printers"]:
        if p["disabled"]:
            res["issues"].append((WARN, f"{p['name']} is paused/disabled", f"enable:{p['name']}"))
    old = [j for j in res["jobs"]]
    if old:
        res["issues"].append((WARN, _('{0} print job(s) waiting — a stuck job blocks everything after it').format(len(old)),
                              "cancel"))
    names = [p["name"] for p in res["printers"]]
    dup = [n for n in names if n.endswith("_USB") and n[:-4] in names]
    if dup:
        res["issues"].append((INFO, _('The same printer is set up twice ({0} over the network and USB) — pick one as the default').format(dup[0][:-4]), "default"))
    return res


def set_default_printer(name):
    return run(["lpoptions", "-d", name])[0] == 0


CUPS_RESTART = "systemctl restart cups"
CANCEL_ALL = "cancel -a -x 2>/dev/null || cancel -a"


def enable_printer_step(name):
    return ("cups-enable", _('Resume printer {0}').format(name), f"cupsenable {q(name)} && cupsaccept {q(name)}")


# ------------------------------------------------------------------ bluetooth

def bluetooth():
    res = {"available": has_cmd("bluetoothctl"), "powered": False, "name": "", "devices": [], "issues": []}
    if not res["available"]:
        return res
    show = out(["bluetoothctl", "show"], timeout=10)
    if not show or "No default controller" in show:
        res["issues"].append((INFO, _("No Bluetooth adapter found"), None))
        res["available"] = False
        return res
    res["powered"] = "Powered: yes" in show
    m = re.search(r"Name: (.+)", show)
    res["name"] = m.group(1).strip() if m else ""
    for line in out(["bluetoothctl", "devices", "Paired"], timeout=10).splitlines():
        m = re.match(r"Device (\S+) (.+)", line)
        if not m:
            continue
        info = out(["bluetoothctl", "info", m.group(1)], timeout=10)
        bat = re.search(r"Battery Percentage: \S+ \((\d+)\)", info)
        res["devices"].append({"mac": m.group(1), "name": m.group(2), "connected": "Connected: yes" in info,
                               "trusted": "Trusted: yes" in info, "battery": int(bat.group(1)) if bat else None})
    if not res["powered"]:
        res["issues"].append((INFO, _("Bluetooth is switched off"), "power-on"))
    for d in res["devices"]:
        if d["battery"] is not None and d["battery"] < 20:
            res["issues"].append((WARN, _('{0} battery is low ({1}%)').format(d['name'], d['battery']), None))
    return res


def bt_power(on):
    return run(["bluetoothctl", "power", "on" if on else "off"], timeout=10)[0] == 0


def bt_remove(mac):
    return run(["bluetoothctl", "remove", mac], timeout=10)[0] == 0


def bt_connect(mac):
    return run(["bluetoothctl", "connect", mac], timeout=20)


BT_RESTART = "systemctl restart bluetooth"


# ------------------------------------------------------------------ crash reports

def crashes(limit=50):
    """Recent core dumps: [{'time','pid','exe','name','signal','size','present'}] newest first."""
    rc, o = run(["coredumpctl", "--json=short", "list", "--no-pager"], timeout=30)
    try:
        items = json.loads(o) if rc == 0 else []
    except ValueError:
        items = []
    sigs = {6: "aborted", 11: "segmentation fault", 7: "bus error", 8: "arithmetic error", 4: "illegal instruction",
            5: "trap"}
    out_ = []
    for d in items[-limit:]:
        exe = d.get("exe", "")
        out_.append({"time": d.get("time", 0) / 1e6, "pid": d.get("pid"), "exe": exe,
                     "name": Path(exe).name or "?", "signal": sigs.get(d.get("sig"), f"signal {d.get('sig')}"),
                     "size": d.get("size", 0) or 0, "present": d.get("corefile") == "present",
                     "uid": d.get("uid")})
    out_.sort(key=lambda d: -d["time"])
    return out_


def crash_info(pid):
    return out(["coredumpctl", "info", str(pid), "--no-pager"], timeout=30)


CLEAR_DUMPS = "rm -f /var/lib/systemd/coredump/core.* && echo 'Removed stored crash dumps'"
