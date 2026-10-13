"""Scheduled check-ups, safety-snapshot preference, change tracking for "Undo all", and history.

Everything here is per-user: settings in ~/.config/mintcare, history and change records in
~/.local/state/mintcare, and the schedule is a systemd *user* timer (no root needed).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gi.repository import GLib

from . import STATE_DIR
from .util import human, out, run

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "mintcare"
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "mintcare"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = STATE / "history.jsonl"
CHANGES_FILE = STATE / "changes.json"
LAST_RUN_FILE = STATE / "last-maintenance.json"
UNIT_MARKERS = Path(STATE_DIR) / "units"
UNIT_DIR = Path(GLib.get_user_config_dir()) / "systemd" / "user"
TIMER = "mintcare-maintenance.timer"
SERVICE = "mintcare-maintenance.service"
SAFE_CLEAN = ["thumbs", "usercache", "browser"]          # user-level items allowed unattended
HISTORY_MAX = 3000

DEFAULTS = {
    "theme": "dark",                     # system | dark | light | contrast
    "level": "intermediate",             # beginner | intermediate | advanced
    "tray": False, "tray_autostart": False,
    "snapshot_before": "ask",            # ask | always | never
    "maintenance": {"frequency": "weekly", "clean": ["thumbs", "usercache"], "notify": "issues"},
}


# ------------------------------------------------------------------ settings

def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(SETTINGS_FILE.read_text())
        s.update({k: v for k, v in user.items() if k != "maintenance"})
        s["maintenance"].update(user.get("maintenance", {}))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(SETTINGS_FILE)


# ------------------------------------------------------------------ history

KIND_FOR_HEADING = {
    "Repair": "fix", "Optimise": "tweak", "Clean": "clean", "Boot services": "service",
    "Flatpak": "flatpak", "Flatpak update": "flatpak", "Flatpak repair": "flatpak",
    "Timeshift": "snapshot", "Undo": "undo", "Snapshot": "snapshot",
}


def record(kind, title, ok=True, detail=""):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with HISTORY_FILE.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "title": title, "ok": ok,
                                "detail": detail}) + "\n")
        _trim()
    except OSError:
        pass


def _trim():
    try:
        if HISTORY_FILE.stat().st_size < 600_000:
            return
        lines = HISTORY_FILE.read_text().splitlines()[-HISTORY_MAX:]
        HISTORY_FILE.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def history(limit=400):
    try:
        lines = HISTORY_FILE.read_text().splitlines()
    except OSError:
        return []
    events = []
    for line in reversed(lines[-limit * 2:]):
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
        if len(events) >= limit:
            break
    return events


def clear_history():
    try:
        HISTORY_FILE.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ change tracking (for Undo all)

def _changes():
    try:
        c = json.loads(CHANGES_FILE.read_text())
    except (OSError, ValueError):
        c = {}
    c.setdefault("autostart", {})
    c.setdefault("flatpak", [])
    return c


def _save_changes(c):
    STATE.mkdir(parents=True, exist_ok=True)
    CHANGES_FILE.write_text(json.dumps(c, indent=2))


def note_autostart(file, enabled, had_user_file):
    """Remember the first MintCare change to a startup entry so it can be undone."""
    c = _changes()
    if file not in c["autostart"]:
        c["autostart"][file] = {"was_enabled": not enabled, "created": not had_user_file}
    elif c["autostart"][file]["was_enabled"] == enabled:
        del c["autostart"][file]                 # back where it started: nothing to undo
    _save_changes(c)


def note_flatpak(app_id, reset=False):
    c = _changes()
    if reset:
        c["flatpak"] = [a for a in c["flatpak"] if a != app_id]
    elif app_id not in c["flatpak"]:
        c["flatpak"].append(app_id)
    _save_changes(c)


def undo_inventory():
    """Everything MintCare changed that can be reverted, grouped for the Undo dialog."""
    from . import tweaks
    items = []
    for t in tweaks.TWEAKS:
        if t.revert and t.available() and t.state() == "mintcare":
            items.append(("tweak", t.id, f"Optimisation: {t.title}"))
    if UNIT_MARKERS.is_dir():
        for m in sorted(UNIT_MARKERS.iterdir()):
            items.append(("unit", m.name, f"Re-enable boot service {m.name}"))
    c = _changes()
    for file, info in c["autostart"].items():
        verb = "Re-enable" if info["was_enabled"] else "Disable again"
        items.append(("autostart", file, f"{verb} startup app {file[:-8] if file.endswith('.desktop') else file}"))
    for app in c["flatpak"]:
        items.append(("flatpak", app, f"Reset Flatpak permissions of {app}"))
    return items


def undo_user_item(kind, key):
    """Undo one per-user change. Root items (tweaks, units) are scripted by the caller."""
    from . import autostart, flatpaks
    c = _changes()
    if kind == "autostart":
        info = c["autostart"].get(key)
        if info:
            user_file = autostart.USER_DIR / key
            if info["created"]:
                user_file.unlink(missing_ok=True)
            else:
                autostart.set_enabled(key, info["was_enabled"])
            del c["autostart"][key]
    elif kind == "flatpak":
        flatpaks.reset(key)
        c["flatpak"] = [a for a in c["flatpak"] if a != key]
    _save_changes(c)


# ------------------------------------------------------------------ safety snapshots

def timeshift_ready():
    from . import space
    if not shutil.which("timeshift"):
        return False, "Timeshift is not installed."
    cfg = space._ts_config()
    if not cfg.get("backup_device_uuid"):
        return False, "Timeshift has no backup drive set up yet."
    return True, ""


def recent_snapshot(minutes=30):
    from . import space
    snaps, _note = space.snapshots()
    return bool(snaps) and time.time() - snaps[0]["created"] < minutes * 60


def snapshot_step(reason):
    from .util import q
    comment = f"MintCare: before {reason}"[:60]
    return ("snapshot", f"Timeshift snapshot ({comment})",
            f"timeshift --create --comments {q(comment)} --tags O --scripted")


# ------------------------------------------------------------------ scheduled check-up

def launcher():
    """Command that starts MintCare (installed package or this source checkout)."""
    if Path("/usr/bin/mintcare").exists() and Path(__file__).resolve().is_relative_to("/usr/lib/mintcare"):
        return ["/usr/bin/mintcare"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "mintcare.py")]


CALENDAR = {"daily": "daily", "weekly": "Mon *-*-* 10:00", "monthly": "*-*-01 10:00"}


def install_timer(frequency):
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = " ".join(launcher() + ["maintain"])
    (UNIT_DIR / SERVICE).write_text(f"""# Managed by MintCare
[Unit]
Description=MintCare scheduled check-up

[Service]
Type=oneshot
ExecStart={cmd}
Nice=10
IOSchedulingClass=idle
""")
    (UNIT_DIR / TIMER).write_text(f"""# Managed by MintCare
[Unit]
Description=MintCare scheduled check-up ({frequency})

[Timer]
OnCalendar={CALENDAR.get(frequency, CALENDAR['weekly'])}
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
""")
    run(["systemctl", "--user", "daemon-reload"])
    rc, o = run(["systemctl", "--user", "enable", "--now", TIMER])
    if rc != 0:
        raise RuntimeError(o)


def remove_timer():
    run(["systemctl", "--user", "disable", "--now", TIMER])
    for f in (TIMER, SERVICE):
        (UNIT_DIR / f).unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])


def timer_status():
    enabled = out(["systemctl", "--user", "is-enabled", TIMER]) == "enabled"
    nxt = ""
    if enabled:
        nxt = out(["systemctl", "--user", "show", "-p", "NextElapseUSecRealtime", "--value", TIMER])
    return {"enabled": enabled, "next": nxt}


def last_run():
    try:
        return json.loads(LAST_RUN_FILE.read_text())
    except (OSError, ValueError):
        return None


def run_maintenance(notify=True, on_line=None):
    """Scan, do the unattended-safe cleaning chosen in settings, record and notify."""
    from . import checks, cleaner
    from .runner import run_steps
    log = on_line or (lambda s: None)
    s = load_settings()["maintenance"]
    started = time.time()

    log("Running checks…")
    results = [(c, c.run()) for c in checks.CHECKS]
    issues = [(c.title, r.summary) for c, r in results if r.status in (checks.WARN, checks.FAIL)]
    tips = sum(1 for _c, r in results if r.status == checks.INFO)

    wanted = set(s.get("clean", []))
    items = [i for i in cleaner.ITEMS if i.user_script and not i.root_script and i.available()
             and (i.id in wanted or ("browser" in wanted and i.id.startswith("browser-")))]
    before = shutil.disk_usage(str(Path.home())).free
    cleaned = []
    if items:
        log("Cleaning: " + ", ".join(i.title for i in items))
        res = run_steps([(i.id, i.title, i.user_script) for i in items], root=False, on_line=log)
        cleaned = [i.id for i in items if res.get(i.id) == 0]
    freed = max(0, shutil.disk_usage(str(Path.home())).free - before)

    report = {"ts": started, "issues": issues, "tips": tips, "checks": len(results),
              "cleaned": cleaned, "freed": freed}
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        LAST_RUN_FILE.write_text(json.dumps(report, indent=2))
    except OSError:
        pass
    summary = (f"{len(issues)} issue(s)" if issues else "no issues") + \
              (f", freed {human(freed)}" if freed > 1 << 20 else "")
    record("scheduled", f"Scheduled check-up: {summary}", True,
           "; ".join(t for t, _s in issues))
    log(f"Check-up finished: {summary}")

    mode = s.get("notify", "issues")
    if notify and shutil.which("notify-send") and (mode == "always" or issues):
        title = "MintCare: " + (f"{len(issues)} thing(s) need attention" if issues else "all good")
        body = "\n".join(f"• {t}" for t, _s in issues[:5]) or "Your system passed every check."
        if freed > 1 << 20:
            body += f"\nCleaned up {human(freed)}."
        try:
            p = subprocess.run(["notify-send", "-a", "MintCare", "-i", "mintcare",
                                "-A", "open=Open MintCare", title, body],
                               stdout=subprocess.PIPE, text=True, timeout=600)
            if p.stdout.strip() == "open":
                subprocess.Popen(launcher(), start_new_session=True,
                                 env=dict(os.environ, MINTCARE_PAGE="diagnose" if issues else "dashboard"))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return report
