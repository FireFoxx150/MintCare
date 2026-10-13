"""Home-folder backup with rsync snapshots (hard-linked, so unchanged files take no extra space),
plus export/import of MintCare settings. Runs as you; no root needed.

Layout: <destination>/MintCare-backups/<computer>/<YYYY-MM-DD_HH-MM>/<folders…>
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from gi.repository import GLib

from . import __version__
from .util import human, out, run

HOME = Path.home()
DEFAULT_EXCLUDES = [".cache", "node_modules", "__pycache__", ".venv", "venv", "*.tmp", ".Trash-*",
                    ".local/share/Trash", ".thumbnails"]
TIMER = "mintcare-backup.timer"
SERVICE = "mintcare-backup.service"
NO_HARDLINKS = {"vfat", "exfat", "msdos"}


def default_folders():
    dirs = []
    for d in (GLib.UserDirectory.DIRECTORY_DOCUMENTS, GLib.UserDirectory.DIRECTORY_PICTURES,
              GLib.UserDirectory.DIRECTORY_DESKTOP, GLib.UserDirectory.DIRECTORY_MUSIC,
              GLib.UserDirectory.DIRECTORY_VIDEOS):
        p = GLib.get_user_special_dir(d)
        if p and os.path.isdir(p) and p != str(HOME):
            dirs.append(p)
    return dirs


def config():
    from . import maintenance
    b = maintenance.load_settings().get("backup") or {}
    b.setdefault("dest", "")
    b.setdefault("folders", default_folders())
    b.setdefault("frequency", "weekly")
    b.setdefault("keep", 8)
    b.setdefault("excludes", DEFAULT_EXCLUDES)
    b.setdefault("enabled", False)
    return b


def save_config(b):
    from . import maintenance
    st = maintenance.load_settings()
    st["backup"] = b
    maintenance.save_settings(st)


def root(dest):
    return Path(dest) / "MintCare-backups" / socket.gethostname()


def snapshots(dest):
    r = root(dest)
    if not r.is_dir():
        return []
    snaps = sorted([p for p in r.iterdir() if re.match(r"\d{4}-\d\d-\d\d_\d\d-\d\d", p.name) and p.is_dir()],
                   reverse=True)
    return snaps


def fstype(path):
    return out(["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)])


def check_destination(dest):
    """[(level, text)] — problems with a chosen destination."""
    issues = []
    if not dest:
        return [("warn", "No backup destination chosen yet")]
    p = Path(dest)
    if not p.is_dir():
        return [("fail", f"{dest} isn't available — is the drive connected and mounted?")]
    if not os.access(p, os.W_OK):
        issues.append(("fail", f"You can't write to {dest}"))
    try:
        if os.stat(p).st_dev == os.stat(HOME).st_dev:
            issues.append(("warn", "This folder is on the same drive as your home folder — if that drive fails, "
                                   "the backup goes with it. Choose another drive."))
    except OSError:
        pass
    fs = fstype(p)
    if fs in NO_HARDLINKS:
        issues.append(("info", f"The drive uses {fs}, which can't hard-link files, so every snapshot is a full "
                               "copy. ext4 or NTFS saves a lot of space."))
    free = shutil.disk_usage(p).free if p.is_dir() else 0
    issues.append(("ok", f"{human(free)} free on the destination ({fs or 'unknown filesystem'})"))
    return issues


def estimate(folders, excludes=DEFAULT_EXCLUDES):
    from .util import dir_size
    return sum(dir_size(f) for f in folders if os.path.isdir(f))


def run_backup(b=None, progress=lambda pct, text: None, cancel=lambda: False):
    """Make one snapshot. Returns {'ok', 'snapshot', 'transferred', 'files', 'error'}."""
    b = b or config()
    problems = [t for lvl, t in check_destination(b["dest"]) if lvl == "fail"]
    if problems:
        return {"ok": False, "error": problems[0]}
    folders = [f for f in b["folders"] if os.path.isdir(f)]
    if not folders:
        return {"ok": False, "error": "None of the chosen folders exist"}
    r = root(b["dest"])
    r.mkdir(parents=True, exist_ok=True)
    prev = snapshots(b["dest"])
    name = time.strftime("%Y-%m-%d_%H-%M")
    target = r / name
    partial = r / (name + ".partial")
    fs = fstype(b["dest"])
    args = ["rsync", "-rlt" if fs in NO_HARDLINKS | {"ntfs", "ntfs3", "fuseblk"} else "-a", "--relative",
            "--delete", "--info=progress2", "--no-inc-recursive", "--stats", "--human-readable"]
    if prev and fs not in NO_HARDLINKS:
        args.append(f"--link-dest={prev[0]}")
    for ex in b.get("excludes", DEFAULT_EXCLUDES):
        args.append(f"--exclude={ex}")
    # "/home/you/./Documents" makes --relative keep just "Documents/…" inside the snapshot
    args += [f"{HOME}/./{os.path.relpath(f, HOME)}" if f.startswith(str(HOME)) else f for f in folders]
    args.append(str(partial) + "/")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    stats, tail = {}, []
    for line in proc.stdout:
        if cancel():
            proc.terminate()
            break
        m = re.search(r"(\d+)%\s+(\S+/s)", line)
        if m:
            progress(int(m.group(1)), f"{m.group(1)}% · {m.group(2)}")
        for key, label in (("files", "Number of regular files transferred"),
                           ("transferred", "Total transferred file size")):
            if line.startswith(label):
                stats[key] = line.split(":", 1)[1].strip().split(" bytes")[0]
        tail = (tail + [line.rstrip()])[-6:]
    rc = proc.wait()
    if cancel():
        shutil.rmtree(partial, ignore_errors=True)
        return {"ok": False, "error": "Cancelled"}
    if rc not in (0, 24):              # 24 = some files vanished during the copy (normal for live folders)
        return {"ok": False, "error": f"rsync failed (exit {rc}): " + " ".join(tail[-2:])}
    partial.rename(target)
    for old in snapshots(b["dest"])[int(b.get("keep", 8)):]:
        shutil.rmtree(old, ignore_errors=True)
    (r / "latest").unlink(missing_ok=True)
    try:
        (r / "latest").symlink_to(target.name)
    except OSError:
        pass
    result = {"ok": True, "snapshot": str(target), "files": stats.get("files", "?"),
              "transferred": stats.get("transferred", "?"), "ts": time.time()}
    from . import maintenance
    try:
        (maintenance.STATE / "last-backup.json").write_text(json.dumps(result))
    except OSError:
        pass
    maintenance.record("backup", f"Backed up {len(folders)} folder(s) to {b['dest']}", True,
                       f"{result['files']} files changed")
    return result


def last_backup():
    from . import maintenance
    try:
        return json.loads((maintenance.STATE / "last-backup.json").read_text())
    except (OSError, ValueError):
        return None


CALENDAR = {"daily": "daily", "weekly": "Sun *-*-* 12:00", "monthly": "*-*-01 12:00"}


def install_timer(frequency):
    from . import maintenance
    d = maintenance.UNIT_DIR
    d.mkdir(parents=True, exist_ok=True)
    cmd = " ".join(maintenance.launcher() + ["backup"])
    (d / SERVICE).write_text(f"# Managed by MintCare\n[Unit]\nDescription=MintCare home folder backup\n\n"
                             f"[Service]\nType=oneshot\nExecStart={cmd}\nNice=15\nIOSchedulingClass=idle\n")
    (d / TIMER).write_text(f"# Managed by MintCare\n[Unit]\nDescription=MintCare home folder backup ({frequency})\n\n"
                           f"[Timer]\nOnCalendar={CALENDAR.get(frequency, CALENDAR['weekly'])}\nPersistent=true\n"
                           "RandomizedDelaySec=20min\n\n[Install]\nWantedBy=timers.target\n")
    run(["systemctl", "--user", "daemon-reload"])
    rc, o = run(["systemctl", "--user", "enable", "--now", TIMER])
    if rc != 0:
        raise RuntimeError(o)


def remove_timer():
    from . import maintenance
    run(["systemctl", "--user", "disable", "--now", TIMER])
    for f in (TIMER, SERVICE):
        (maintenance.UNIT_DIR / f).unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])


def timer_next():
    if out(["systemctl", "--user", "is-enabled", TIMER]) != "enabled":
        return None
    return out(["systemctl", "--user", "show", "-p", "NextElapseUSecRealtime", "--value", TIMER])


# ------------------------------------------------------------------ export / import settings

def export_settings(path):
    from . import maintenance, tweaks
    data = {
        "mintcare": __version__,
        "exported": time.strftime("%Y-%m-%d %H:%M"),
        "settings": {k: v for k, v in maintenance.load_settings().items() if k != "backup"},
        "tweaks": [t.id for t in tweaks.TWEAKS if t.available() and t.state() == "mintcare"],
        "startup_disabled": [f for f, i in maintenance._changes()["autostart"].items() if i["was_enabled"]],
    }
    Path(path).write_text(json.dumps(data, indent=2))
    return data


def import_settings(path):
    """Apply saved settings; return the tweak ids to select (they still need Apply on Optimise)."""
    from . import autostart, maintenance, tweaks
    data = json.loads(Path(path).read_text())
    if "mintcare" not in data:
        raise ValueError("This isn't a MintCare settings file")
    st = maintenance.load_settings()
    for k, v in data.get("settings", {}).items():
        if k in st and k != "backup":
            st[k] = v
    maintenance.save_settings(st)
    for f in data.get("startup_disabled", []):
        try:
            had = (autostart.USER_DIR / f).exists()
            autostart.set_enabled(f, False)
            maintenance.note_autostart(f, False, had)
        except (OSError, FileNotFoundError):
            pass
    return [i for i in data.get("tweaks", []) if i in tweaks.BY_ID and tweaks.BY_ID[i].available()]
