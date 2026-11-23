"""Process explorer: top CPU/RAM users, what each one is, and ending them."""

import functools
import os
import signal
import time

from .i18n import _, ngettext
from .util import has_cmd, out, read, run

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
    """({pid: (utime+stime ticks, parent pid, start ticks after boot)}, wall time) for CPU % between two samples."""
    out_ = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        st = read(f"/proc/{d}/stat")
        if not st:
            continue
        f = st[st.rfind(")") + 2:].split()
        try:
            out_[int(d)] = (int(f[11]) + int(f[12]), int(f[1]), int(f[19]))
        except (IndexError, ValueError):
            pass
    return out_, time.monotonic()


def processes(prev=None):
    """List of dicts with pid, ppid, name, user, cpu (% of one core), rss and swap bytes, age and cpu_time in
    seconds, cmd, mine."""
    cur, now = snapshot()
    ticks = _clk()
    me = os.getuid()
    try:
        uptime = float(read("/proc/uptime").split()[0])
    except (IndexError, ValueError):
        uptime = 0.0
    rows = []
    users = {}
    for pid, (total, ppid, start) in cur.items():
        status = read(f"/proc/{pid}/status")
        if not status:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        name = fields.get("Name", "?").strip()
        uid = int(fields.get("Uid", "0").split()[0])
        rss = int(fields.get("VmRSS", "0 kB").split()[0]) * 1024 if "VmRSS" in fields else 0
        swap = int(fields.get("VmSwap", "0 kB").split()[0]) * 1024 if "VmSwap" in fields else 0
        cpu = 0.0
        if prev and pid in prev[0]:
            dt = max(now - prev[1], 1e-3)
            cpu = 100.0 * (total - prev[0][pid][0]) / ticks / dt
        if uid not in users:
            try:
                import pwd
                users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                users[uid] = str(uid)
        cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
        rows.append({"pid": pid, "ppid": ppid, "name": name, "user": users[uid], "uid": uid, "cpu": max(0.0, cpu),
                     "rss": rss, "swap": swap, "age": max(0.0, uptime - start / ticks), "cpu_time": total / ticks,
                     "cmd": cmd or f"[{name}]", "mine": uid == me, "kernel": not cmd})
    return rows, (cur, now)


@functools.lru_cache(maxsize=256)
def _owner(exe):
    """(package, its summary) that owns an executable. Looked up once per program, not on every refresh."""
    pkg = ""
    o = out(["dpkg", "-S", exe.replace(" (deleted)", "")], timeout=10)
    if ":" in o and _("no path found") not in o:
        pkg = o.split(":", 1)[0].split(",")[0].strip()
    summary = out(["dpkg-query", "-W", "-f=${binary:Summary}", pkg]) if pkg else ""
    return pkg, summary


def describe(p):
    """Plain-English description plus the owning package's summary, if any."""
    text = KNOWN.get(p["name"], "")
    exe = ""
    try:
        exe = os.readlink(f"/proc/{p['pid']}/exe")
    except OSError:
        pass
    pkg, summary = _owner(exe) if exe else ("", "")
    if p["kernel"]:
        text = text or _("A kernel thread — part of Linux itself.")
    return {"text": text, "exe": exe, "package": pkg, "summary": summary}


def end(pid, force=False):
    """End one of your own processes. Raises PermissionError for other users' processes."""
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


# Never offered by the memory-waster scan: the desktop session and the services it needs. Names are as
# /proc/<pid>/status shows them, cut to 15 characters.
SESSION = frozenset({
    "Xorg", "Xwayland", "systemd", "dbus-daemon", "dbus-broker", "pipewire", "pipewire-pulse", "wireplumber",
    "nemo-desktop", "gnome-keyring-d", "polkit-gnome-au", "polkit-mate-aut", "pulseaudio", "ibus-daemon",
    "xapp-sn-watcher", "blueman-applet", "nm-applet", "mintreport-tray", "mintUpdate", "mintcare",
})
SESSION_PREFIXES = ("cinnamon", "csd-", "xdg-", "gvfs", "at-spi", "ibus-", "evolution-", "gsd-", "dconf")

WASTE_MIN = 200 * 2 ** 20               # memory (resident + swap) an idle program must hold to be listed
DUP_MIN = 50 * 2 ** 20                  # memory an idle extra copy of a program must hold to be listed
IDLE_AGE = 30 * 60                      # seconds a program must have run before it can count as idle


def protected_pids(pid=None):
    """MintCare's own process and every process above it (the terminal or launcher that started it)."""
    pid = os.getpid() if pid is None else pid
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        st = read(f"/proc/{pid}/stat")
        try:
            pid = int(st[st.rfind(")") + 2:].split()[1])
        except (IndexError, ValueError):
            break
    return seen


def _session(name):
    return name in SESSION or name.startswith(SESSION_PREFIXES)


def _idle(r):
    """Has run IDLE_AGE or longer, used no processor in the last sample and under 1% of one core on average."""
    return r["age"] >= IDLE_AGE and r["cpu"] < 0.1 and r["cpu_time"] < 0.01 * r["age"]


def memory_wasters(rows, protected=()):
    """Your own programs that hold memory while doing nothing: [(row, reason)], most memory first.

    rows come from processes() with a CPU sample (prev). A program is listed when it is idle (see _idle) and
    either holds WASTE_MIN or more, or is an older copy of a program running the exact same command line and
    holds DUP_MIN or more; the newest copy is kept. Copies started by another copy (the worker processes of
    browsers and similar apps) are not counted as duplicates. Kernel threads, other users' processes, the
    desktop session (SESSION), MintCare and the processes above it are never listed."""
    cand = [r for r in rows if r["mine"] and not r["kernel"] and r["pid"] not in protected
            and not _session(r["name"])]
    by_pid = {r["pid"]: r for r in rows}
    groups = {}
    for r in cand:
        parent = by_pid.get(r["ppid"])
        if parent is None or parent["name"] != r["name"]:
            groups.setdefault(r["cmd"], []).append(r)
    older = set()
    for copies in groups.values():
        if len(copies) > 1:
            newest = min(copies, key=lambda r: r["age"])
            older.update(r["pid"] for r in copies if r is not newest)
    found = []
    for r in cand:
        mem = r["rss"] + r["swap"]
        if not _idle(r):
            continue
        hours = r["age"] / 3600
        if r["pid"] in older and mem >= DUP_MIN:
            found.append((r, _("Another copy of this program is running; this older copy has been idle")))
        elif mem >= WASTE_MIN:
            found.append((r, ngettext("Idle for most of the {0} hour it has been running",
                                      "Idle for most of the {0} hours it has been running",
                                      int(hours)).format(int(hours)) if hours >= 1 else
                          _("Idle since it started")))
    return sorted(found, key=lambda f: -(f[0]["rss"] + f[0]["swap"]))


def end_checked(pid, name, force=False):
    """End one of your own processes if it is still the same program. Returns "ended", "gone" or "denied".
    Checking the name first means a PID reused by another program since the scan is left alone."""
    if read(f"/proc/{int(pid)}/comm") != name[:15]:
        return "gone"
    try:
        end(int(pid), force)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "denied"
    return "ended"


def still_running(targets):
    """The (pid, name) pairs from targets that are still running as the same program."""
    return [(pid, name) for pid, name in targets if read(f"/proc/{int(pid)}/comm") == name[:15]]


def parse_gpu_processes(text):
    """[(pid, name, MB)] from `nvidia-smi --query-compute-apps=pid,process_name,used_memory`, largest first."""
    found = []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit() or not parts[-1].isdigit():
            continue                                  # "[N/A]" or a header: not a program
        found.append((int(parts[0]), ", ".join(parts[1:-1]), int(parts[-1])))
    return sorted(found, key=lambda f: -f[2])


def gpu_processes():
    """Programs holding NVIDIA graphics memory right now. Empty without nvidia-smi or without such a program."""
    if not has_cmd("nvidia-smi"):
        return []
    rc, o = run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                timeout=5)
    return parse_gpu_processes(o) if rc == 0 else []


def end_step(pid, name, force=False):
    sig = "KILL" if force else "TERM"
    return ("kill", f"End {name} ({pid})", f"kill -{sig} {int(pid)}")


def nice_of(pid):
    """Nice value of a process (-20 most urgent to 19 least), or None when it can't be read."""
    st = read(f"/proc/{pid}/stat")
    f = st[st.rfind(")") + 2:].split() if st else []
    try:
        return int(f[16])                 # field 19 of /proc/<pid>/stat, counted from the state field
    except (IndexError, ValueError):
        return None


def lower_priority(pid, name, nice=10):
    """Make one of your own programs less urgent: nice +10 for the processor, the idle class for disk.
    Needs no password. Returns (ok, message); refuses if that PID now belongs to a different program."""
    pid = int(pid)
    if read(f"/proc/{pid}/comm") != name[:15]:
        return False, _("{0} has already closed").format(name)
    rc, o = run(["renice", "-n", str(int(nice)), "-p", str(pid)], timeout=10)
    if rc != 0:
        return False, o or _("Could not change the priority")
    run(["ionice", "-c", "3", "-p", str(pid)], timeout=10)
    return True, ""


def restore_step(pid, name, nice=0):
    """Root step putting a program back to the priority it had before it was lowered. Raising priority needs
    administrator rights, which is why this is a root step."""
    p = int(pid)
    return (f"restore{p}", _("Restore normal priority: {0} ({1})").format(name, p),
            f"renice -n {int(nice)} -p {p}\nionice -c 2 -n 4 -p {p}")
