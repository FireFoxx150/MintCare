"""Threshold alerts: a desktop notification when a drive is nearly full, the battery is low, or a processor
or drive runs hot. A systemd user timer runs `mintcare alerts` every 15 minutes. Each problem is announced
once when it starts and again only after it has cleared. Runs as you; no root needed.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .i18n import _
from . import hardware, maintenance
from .util import out, run

TIMER = "mintcare-alerts.timer"
SERVICE = "mintcare-alerts.service"
DISK_FULL = 90                                # percent of a drive in use
BATTERY_LOW = 15                              # percent, while not charging
STATE_FILE = maintenance.STATE / "alerts.json"


def disk_points():
    """[(label, percent in use)] for the system drive and the home drive (one entry when they share a drive)."""
    seen, points = set(), []
    for path, label in (("/", _("System drive")), (str(Path.home()), _("Home drive"))):
        try:
            dev = os.stat(path).st_dev
            du = shutil.disk_usage(path)
        except OSError:
            continue
        usable = du.used + du.free                 # what df reports as its size, so the percentage matches df
        if dev in seen or not usable:
            continue
        seen.add(dev)
        points.append((label, round(100 * du.used / usable)))
    return points


def conditions(disks=None, bats=None, temps=None):
    """{key: text} for every threshold being crossed now. The arguments let tests pass readings in."""
    disks = disk_points() if disks is None else disks
    bats = hardware.batteries() if bats is None else bats
    temps = hardware.sensors(with_gpu=False) if temps is None else temps
    now = {}
    for label, pct in disks:
        if pct >= DISK_FULL:
            # xgettext:no-python-format
            now[f"disk:{label}"] = _("{0} is {1}% full").format(label, pct)
    for b in bats:
        if b.get("capacity") is not None and b["capacity"] <= BATTERY_LOW and b.get("status") == "Discharging":
            # xgettext:no-python-format
            now["battery"] = _("Battery is at {0}% and not charging").format(b["capacity"])
    for s in temps:
        if s["group"] in ("cpu", "gpu", "disk") and hardware.temp_level(s) in (hardware.WARN, hardware.FAIL):
            now[f"temp:{s['label']}"] = _("{0} is running hot: {1:.0f}°C").format(s["label"], s["temp"])
    return now


def changes(active, now):
    """(alerts to announce, keys still active). An alert is announced when it starts, not on every run."""
    return [(key, text) for key, text in now.items() if key not in active], set(now)


def notify(text):
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "MintCare", "-i", "mintcare", "MintCare", text], timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


def check_now():
    """One pass, as the timer runs it: record battery health, then announce problems that are new.
    Returns the texts announced."""
    bats = hardware.batteries()
    hardware.battery_track(bats)
    try:
        active = set(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        active = set()
    send, keep = changes(active, conditions(bats=bats))
    for _key, text in send:
        notify(text)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(sorted(keep)))
    except OSError:
        pass
    return [text for _key, text in send]


def unit_files():
    """The service and timer units as text. The timer runs five minutes after login, then every 15 minutes."""
    cmd = shlex.join(maintenance.launcher() + ["alerts"])
    service = (f"# Managed by MintCare\n[Unit]\nDescription=MintCare threshold alerts\n\n[Service]\n"
               f"Type=oneshot\nExecStart={cmd}\nNice=10\nIOSchedulingClass=idle\n")
    timer = ("# Managed by MintCare\n[Unit]\nDescription=MintCare threshold alerts (every 15 minutes)\n\n"
             "[Timer]\nOnBootSec=5min\nOnUnitActiveSec=15min\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def install_timer():
    service, timer = unit_files()
    maintenance.UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (maintenance.UNIT_DIR / SERVICE).write_text(service)
    (maintenance.UNIT_DIR / TIMER).write_text(timer)
    run(["systemctl", "--user", "daemon-reload"])
    rc, o = run(["systemctl", "--user", "enable", "--now", TIMER])
    if rc != 0:
        raise RuntimeError(o)


def remove_timer():
    run(["systemctl", "--user", "disable", "--now", TIMER])
    for f in (TIMER, SERVICE):
        (maintenance.UNIT_DIR / f).unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])


def timer_enabled():
    return out(["systemctl", "--user", "is-enabled", TIMER]) == "enabled"
