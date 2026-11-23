"""Home-folder backup with rsync snapshots (hard-linked, so unchanged files take no extra space),
plus export/import of MintCare settings. Runs as you; no root needed.

Layout: <destination>/MintCare-backups/<computer>/<YYYY-MM-DD_HH-MM>/<folders…>
"""

import fnmatch
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path

from gi.repository import GLib

from .i18n import _, ngettext
from . import __version__
from .util import has_cmd, human, out, run

HOME = Path.home()
DEFAULT_EXCLUDES = [".cache", "node_modules", "__pycache__", ".venv", "venv", "*.tmp", ".Trash-*",
                    ".local/share/Trash", ".thumbnails"]
TIMER = "mintcare-backup.timer"
SERVICE = "mintcare-backup.service"
NO_HARDLINKS = {"vfat", "exfat", "msdos"}
DATED = re.compile(r"\d{4}-\d\d-\d\d_\d\d-\d\d")      # a snapshot folder's name: 2026-11-15_10-30


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
    b.setdefault("retention", "last")                # "last" keeps the newest `keep`; "gfs" keeps daily/weekly/monthly
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
    snaps = sorted([p for p in r.iterdir() if DATED.fullmatch(p.name) and p.is_dir()], reverse=True)
    return snaps


def fstype(path):
    return out(["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)])


def check_destination(dest):
    """[(level, text)] — problems with a chosen destination."""
    issues = []
    if not dest:
        return [("warn", _("No backup destination chosen yet"))]
    p = Path(dest)
    if not p.is_dir():
        return [("fail", _("{0} isn't available — is the drive connected and mounted?").format(dest))]
    if not os.access(p, os.W_OK):
        issues.append(("fail", _("You can't write to {0}").format(dest)))
    try:
        if os.stat(p).st_dev == os.stat(HOME).st_dev:
            issues.append(("warn", _("This folder is on the same drive as your home folder — if that drive fails, "
                                   "the backup goes with it. Choose another drive.")))
    except OSError:
        pass
    fs = fstype(p)
    if fs in NO_HARDLINKS:
        issues.append(("info", _("The drive uses {0}, which can't hard-link files, so every snapshot is a full copy. ext4 or NTFS saves a lot of space.").format(fs)))
    free = shutil.disk_usage(p).free if p.is_dir() else 0
    issues.append(("ok", _('{0} free on the destination ({1})').format(human(free), fs or _('unknown filesystem'))))
    return issues


def estimate(folders, excludes=DEFAULT_EXCLUDES):
    """Bytes a backup of these folders would copy, leaving out what the excludes skip (close to what rsync does)."""
    from .util import dir_size
    return sum(dir_size(f, lambda p, f=f: _skipped(p, f, excludes)) for f in folders if os.path.isdir(f))


def _skipped(path, folder, excludes):
    """Whether an exclude pattern leaves this path out. A pattern with a slash is matched against the path inside
    the folder being backed up; one without a slash is matched against each name, as rsync matches it."""
    rel, name = os.path.relpath(path, folder), os.path.basename(path)
    for pat in excludes:
        if "/" in pat:
            if rel == pat or rel.endswith("/" + pat):
                return True
        elif fnmatch.fnmatchcase(name, pat):
            return True
    return False


def run_backup(b=None, progress=lambda pct, text: None, cancel=lambda: False):
    """Make one snapshot. Returns {'ok', 'snapshot', 'transferred', 'files', 'error'}, and 'skipped' and 'warning'
    when rsync could not copy some files."""
    b = b or config()
    problems = [t for lvl, t in check_destination(b["dest"]) if lvl == "fail"]
    if problems:
        return {"ok": False, "error": problems[0]}
    folders = [f for f in b["folders"] if os.path.isdir(f)]
    if not folders:
        return {"ok": False, "error": _("None of the chosen folders exist")}
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
    stats, tail, skipped = {}, [], []
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
        if line.startswith("rsync: ") and "failed" in line:      # a file rsync could not copy
            skipped.append(line.split('"')[1] if '"' in line else line.strip())
        tail = (tail + [line.rstrip()])[-6:]
    rc = proc.wait()
    proc.stdout.close()                # the pipe stays open otherwise, and Python warns about it
    if cancel():
        shutil.rmtree(partial, ignore_errors=True)
        return {"ok": False, "error": "Cancelled"}
    if rc not in (0, 23, 24):          # 23: some files could not be copied; 24: some vanished (normal for live folders)
        shutil.rmtree(partial, ignore_errors=True)       # a failed copy is not a snapshot
        return {"ok": False, "error": _('rsync failed (exit {0}): ').format(rc) + " ".join(tail[-2:])}
    partial.rename(target)
    old = snapshots(b["dest"])
    if b.get("retention") == "gfs":
        keep = keep_plan([p.name for p in old])
        drop = [p for p in old if DATED.fullmatch(p.name) and p.name not in keep]
    else:
        drop = old[int(b.get("keep", 8)):]
    for p in drop:
        shutil.rmtree(p, ignore_errors=True)
    (r / "latest").unlink(missing_ok=True)
    try:
        (r / "latest").symlink_to(target.name)
    except OSError:
        pass
    result = {"ok": True, "snapshot": str(target), "files": stats.get("files", "?"),
              "transferred": stats.get("transferred", "?"), "ts": time.time()}
    if rc == 23:                       # the snapshot is kept, and the user is told what is missing from it
        result["skipped"] = skipped
        result["warning"] = ngettext("{0} file could not be copied, so it is not in this backup",
                                     "{0} files could not be copied, so they are not in this backup",
                                     len(skipped)).format(len(skipped))
    from . import maintenance
    stored = {k: v for k, v in result.items() if k != "skipped"}      # file names stay out of the state file
    try:
        (maintenance.STATE / "last-backup.json").write_text(json.dumps(stored))
    except OSError:
        pass
    maintenance.record("backup", _('Backed up {0} folder(s) to {1}').format(len(folders), b['dest']), True,
                       result.get("warning") or _('{0} files changed').format(result['files']))
    return result


def _key(folder):
    """How a backed-up folder is stored inside a snapshot: relative to home, or its full path without
    the leading slash for folders elsewhere."""
    return os.path.relpath(folder, HOME) if folder.startswith(str(HOME) + os.sep) else folder.lstrip("/")


def _origin(rel, folders):
    """Where a path inside a snapshot came from."""
    for f in sorted(folders, key=len, reverse=True):
        key = _key(f)
        if rel == key or rel.startswith(key + os.sep):
            return str(HOME / rel) if f.startswith(str(HOME) + os.sep) else "/" + rel
    return str(HOME / rel)


def _via_symlink(dest):
    """True if dest is under HOME and any part of its path below HOME is a symlink."""
    try:
        parts = Path(dest).relative_to(HOME).parts
    except ValueError:
        return False
    cur = HOME
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def restore(snapshot, paths, folders=None):
    """Copy files or folders from a snapshot back to where they came from. Files that would be
    overwritten are kept beside them as NAME.before-restore. Returns [(path, ok, message)]."""
    snapshot = os.path.realpath(snapshot)
    folders = folders if folders is not None else config()["folders"]
    results = []
    for p in paths:
        src = os.path.realpath(p)
        if src == snapshot:                                  # the whole snapshot: each backed-up folder in it
            inside = [os.path.join(snapshot, _key(f)) for f in folders]
            results += restore(snapshot, [d for d in inside if os.path.exists(d)], folders)
            continue
        if not src.startswith(snapshot + os.sep):
            results.append((p, False, "not inside this snapshot"))
            continue
        dest = _origin(os.path.relpath(src, snapshot), folders)
        if _via_symlink(dest):
            results.append((dest, False, _("the path goes through a symlink, so nothing was written")))
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # --no-links: a symlink on the snapshot media is skipped, never recreated under HOME
        args = ["rsync", "-a", "--no-links", "--backup", "--suffix=.before-restore"]
        args += [src + "/", dest + "/"] if os.path.isdir(src) else [src, dest]
        rc, o = run(args, timeout=3600)
        results.append((dest, rc == 0, o.strip()[-200:]))
    return results


def last_backup():
    from . import maintenance
    try:
        return json.loads((maintenance.STATE / "last-backup.json").read_text())
    except (OSError, ValueError):
        return None


RESTIC_PASSWORD = Path(GLib.get_user_config_dir()) / "mintcare" / "restic-password"


def restic_ready():
    return has_cmd("restic")


def _restic(args, repo, password_file=None, timeout=600):
    """Run restic on `repo`. The arguments are a list, so nothing in them is read by a shell."""
    if not repo or repo.startswith("-"):
        return 2, _("Choose a folder or repository first")
    # The repository goes in the environment: a command line can be read by every user on the PC.
    return run(["restic", *args, "--password-file", str(password_file or RESTIC_PASSWORD)],
               timeout=timeout, env_extra={"RESTIC_REPOSITORY": repo})


def restic_init(repo, password):
    """Create the encrypted repository. The password is kept only once restic has accepted it."""
    if not password:
        return False, _("Type a password first")
    RESTIC_PASSWORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESTIC_PASSWORD.with_name(RESTIC_PASSWORD.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password)
    rc, text = _restic(["init"], repo, tmp, timeout=120)
    if rc == 0:
        os.replace(tmp, RESTIC_PASSWORD)
    else:
        tmp.unlink(missing_ok=True)
    lines = text.strip().splitlines()
    return rc == 0, lines[-1] if lines else ""


def restic_backup(b=None, timeout=24 * 3600):
    """One encrypted snapshot of the chosen folders. Returns (ok, last line of restic's output)."""
    b = b or config()
    folders = [f for f in b["folders"] if os.path.isdir(f)]
    if not folders:
        return False, _("None of the chosen folders exist")
    args = ["backup"]
    for ex in b.get("excludes", DEFAULT_EXCLUDES):
        args += ["--exclude", ex]
    rc, text = _restic(args + folders, b.get("restic_repo", ""), timeout=timeout)
    lines = text.strip().splitlines()
    return rc == 0, lines[-1] if lines else ""


def restic_latest(b=None):
    """Time of the newest encrypted snapshot, as restic writes it, or None."""
    b = b or config()
    rc, text = _restic(["snapshots", "--json"], b.get("restic_repo", ""), timeout=120)
    try:
        times = [s["time"] for s in json.loads(text) if s.get("time")] if rc == 0 else []
    except (ValueError, TypeError):
        return None
    return max(times) if times else None


CALENDAR = {"daily": "daily", "weekly": "Sun *-*-* 12:00", "monthly": "*-*-01 12:00"}


def install_timer(frequency):
    from . import maintenance
    d = maintenance.UNIT_DIR
    d.mkdir(parents=True, exist_ok=True)
    cmd = shlex.join(maintenance.launcher() + ["backup"])
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
        raise ValueError(_("This isn't a MintCare settings file"))
    st = maintenance.load_settings()
    report_folder = st.get("maintenance", {}).get("report_folder", "")    # a folder on this computer: never taken from the file
    for k, v in data.get("settings", {}).items():
        if k in st and k != "backup":
            st[k] = v
    if isinstance(st.get("maintenance"), dict):
        st["maintenance"]["report_folder"] = report_folder
    maintenance.save_settings(st)
    for f in data.get("startup_disabled", []):
        if not autostart.is_plain_name(f):
            continue
        try:
            had = (autostart.USER_DIR / f).exists()
            autostart.set_enabled(f, False)
            maintenance.note_autostart(f, False, had)
        except (OSError, FileNotFoundError):
            pass
    return [i for i in data.get("tweaks", []) if i in tweaks.BY_ID and tweaks.BY_ID[i].available()]


def verify(snapshot, sample=300):
    """Read a random sample of files in a snapshot. Returns (files read, unreadable paths).

    Checks that the copies can be read, not that they match the originals: a full comparison would
    read everything again. shortcut: a damaged file outside the sample is missed; raise `sample` or compare fully.
    """
    import random
    files = [p for p in Path(snapshot).rglob("*") if p.is_file() and not p.is_symlink()]
    picked = random.sample(files, min(sample, len(files)))
    bad = []
    for f in picked:
        try:
            with open(f, "rb") as fh:
                fh.read(1 << 16)
        except OSError:
            bad.append(str(f))
    return len(picked), bad


def compare_with_originals(snapshot, folders, excludes=DEFAULT_EXCLUDES):
    """Compare every file in a snapshot with its original, by checksum. Returns (files checked, paths that differ).

    A file changed since the backup counts as different. Every file is read twice, so this can take a long time.
    """
    checked, differ = 0, []
    for folder in folders:
        copy = Path(snapshot) / _key(folder)
        if not copy.is_dir():
            differ.append(folder)
            continue
        args = ["rsync", "-rcn", "--stats", "--out-format=%i %n"] + [f"--exclude={ex}" for ex in excludes]
        _rc, text = run(args + [folder.rstrip("/") + "/", str(copy) + "/"], timeout=24 * 3600)
        m = re.search(r"Number of files: ([\d,]+)", text)
        checked += int(m.group(1).replace(",", "")) if m else 0
        differ += [os.path.join(folder, rel) for rel in differing_files(text)]
    return checked, differ


def differing_files(listing):
    """Files rsync's dry run would copy (new, missing or different), from a listing made with --out-format='%i %n'."""
    return [line[12:] for line in listing.splitlines() if line.startswith(">f")]


def keep_plan(names, daily=7, weekly=4, monthly=12):
    """Snapshot names to keep: the newest copy from each of the last `daily` days, `weekly` weeks and `monthly`
    months that have one. Names look like 2026-11-15_10-30."""
    from datetime import date
    dated = []
    for name in names:
        try:
            dated.append((time.strptime(name, "%Y-%m-%d_%H-%M"), name))
        except ValueError:
            continue
    dated.sort(reverse=True)                  # newest first: the first copy seen in a period is its newest
    keep = set()
    periods = ((daily, lambda d: d), (weekly, lambda d: d.isocalendar()[:2]), (monthly, lambda d: (d.year, d.month)))
    for limit, period in periods:
        seen = set()
        for t, name in dated:
            key = period(date(t.tm_year, t.tm_mon, t.tm_mday))
            if key in seen:
                continue
            if len(seen) == limit:
                break
            seen.add(key)
            keep.add(name)
    return keep
