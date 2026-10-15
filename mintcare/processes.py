"""Process explorer: top CPU/RAM users, what each one is, and ending them."""

import os
import signal
import time

from .i18n import _
from .util import out, read

KNOWN = {
    "Xorg": _("The display server that draws your screen."),
    "cinnamon": _("The Cinnamon desktop (panel, menu, windows)."),
    "cinnamon-session": _("Starts and supervises your desktop session."),
    "nemo-desktop": _("Draws the desktop icons."),
    "nemo": _("The Files app."),
    "pipewire": _("The sound and screen-sharing server."),
    "pipewire-pulse": _("PulseAudio compatibility for PipeWire (most apps' sound goes through it)."),
    "wireplumber": _("Chooses audio devices for PipeWire."),
    "Xwayland": _("Runs X11 apps on Wayland."),
    "systemd": _("The system and service manager."),
    "systemd-journald": _("Collects the system log."),
    "NetworkManager": _("Manages network connections."),
    "firefox": _("Firefox web browser (one process per tab group)."),
    "firefox-bin": _("Firefox web browser (one process per tab group)."),
    "Isolated Web Co": _("A Firefox tab's content process."),
    "Web Content": _("A Firefox tab's content process."),
    "brave": _("Brave web browser (several processes per window)."),
    "chrome": _("Google Chrome (several processes per window)."),
    "code": "Visual Studio Code.",
    "Discord": _("Discord chat."),
    "steam": _("Steam client."),
    "steamwebhelper": "Steam's built-in browser.",
    "ollama": _("Ollama AI model server — uses a lot of RAM while a model is loaded."),
    "kdeconnectd": _("KDE Connect phone integration."),
    "csd-media-keys": _("Cinnamon settings daemon: media keys."),
    "csd-power": _("Cinnamon settings daemon: power management."),
    "mintupdate": "Linux Mint Update Manager.",
    "mintreport": "Linux Mint System Reports.",
    "timeshift": _("Timeshift system snapshots."),
    "tracker-miner-f": _("Indexes files for search."),
    "baloo_file": _("Indexes files for search (KDE)."),
    "gnome-keyring-d": _("Stores passwords and keys."),
    "polkitd": _("Handles administrator password prompts."),
    "udisksd": _("Manages disks and USB drives."),
    "upowerd": _("Battery and power information."),
    "cupsd": _("The printing service."),
    "avahi-daemon": _("Finds printers and devices on the network."),
    "smartd": _("Monitors disk health in the background."),
    "fwupd": _("Firmware update service."),
    "packagekitd": _("Software install/update service."),
    "python3": _("A Python program — the command line shows which one."),
    "java": _("A Java program — the command line shows which one."),
    "node": _("A Node.js program — the command line shows which one."),
}


def _clk():
    return os.sysconf("SC_CLK_TCK")


def snapshot():
    """{pid: (utime+stime ticks, wall time)} for CPU % between two samples."""
    out_ = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        st = read(f"/proc/{d}/stat")
        if not st:
            continue
        f = st[st.rfind(")") + 2:].split()
        try:
            out_[int(d)] = int(f[11]) + int(f[12])
        except (IndexError, ValueError):
            pass
    return out_, time.monotonic()


def processes(prev=None):
    """List of dicts with pid, name, user, cpu (% of one core), rss bytes, cmd, mine."""
    cur, now = snapshot()
    ticks = _clk()
    me = os.getuid()
    rows = []
    users = {}
    for pid, total in cur.items():
        status = read(f"/proc/{pid}/status")
        if not status:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        name = fields.get("Name", "?").strip()
        uid = int(fields.get("Uid", "0").split()[0])
        rss = int(fields.get("VmRSS", "0 kB").split()[0]) * 1024 if "VmRSS" in fields else 0
        cpu = 0.0
        if prev and pid in prev[0]:
            dt = max(now - prev[1], 1e-3)
            cpu = 100.0 * (total - prev[0][pid]) / ticks / dt
        if uid not in users:
            try:
                import pwd
                users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                users[uid] = str(uid)
        cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
        rows.append({"pid": pid, "name": name, "user": users[uid], "uid": uid, "cpu": max(0.0, cpu),
                     "rss": rss, "cmd": cmd or f"[{name}]", "mine": uid == me, "kernel": not cmd})
    return rows, (cur, now)


def describe(p):
    """Plain-English description plus the owning package's summary, if any."""
    text = KNOWN.get(p["name"], "")
    exe = ""
    try:
        exe = os.readlink(f"/proc/{p['pid']}/exe")
    except OSError:
        pass
    pkg = ""
    if exe:
        o = out(["dpkg", "-S", exe.replace(" (deleted)", "")], timeout=10)
        if ":" in o and _("no path found") not in o:
            pkg = o.split(":", 1)[0].split(",")[0].strip()
    summary = out(["dpkg-query", "-W", "-f=${binary:Summary}", pkg]) if pkg else ""
    if p["kernel"]:
        text = text or _("A kernel thread — part of Linux itself.")
    return {"text": text, "exe": exe, "package": pkg, "summary": summary}


def end(pid, force=False):
    """End one of your own processes. Raises PermissionError for other users' processes."""
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def end_step(pid, name, force=False):
    sig = "KILL" if force else "TERM"
    return ("kill", f"End {name} ({pid})", f"kill -{sig} {int(pid)}")
