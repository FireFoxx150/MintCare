"""Real-time guard, run as a user service (`mintcare guard`):

- Decoy files: three files named "!MintCare decoy …" in Documents. They sort first, so software that
  encrypts a folder usually reaches them early. If one changes, disappears or is renamed, you are warned.
- Downloads: each finished download is checked with ClamAV (clamscan). Nothing is deleted or moved.

Neither part needs root. Both are off until you turn them on on the Security page.
"""

import hashlib
import json
import os
import queue
import shlex
import shutil
import sys
import threading
from pathlib import Path

from .i18n import _
from . import maintenance
from .util import run

STATE_FILE = maintenance.STATE / "guard.json"
UNIT = "mintcare-guard.service"
DECOY_NAMES = [f"!MintCare decoy {n} - do not edit.txt" for n in (1, 2, 3)]
DECOY_TEXT = _("This is a MintCare decoy file. If it changes or disappears, MintCare warns you.\n"
               "Please leave it alone. Turning decoys off on the Security page removes it.\n")
PARTIAL = (".part", ".crdownload", ".download", ".partial", ".tmp")


def documents_dir():
    from gi.repository import GLib
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or str(Path.home() / "Documents")


def state(path=None):
    try:
        st = json.loads(Path(path or STATE_FILE).read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("decoys", False)
    st.setdefault("downloads", False)
    st.setdefault("folder", "")
    st.setdefault("hashes", {})
    return st


def _save(st, path=None):
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=2))


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ decoy files

def make_decoys(folder, path=None):
    """Create any decoy that is missing, and remember each one's hash so a change can be seen."""
    st = state(path)
    st["folder"] = folder
    for name in DECOY_NAMES:
        p = os.path.join(folder, name)
        if not os.path.exists(p):
            Path(p).write_text(DECOY_TEXT)
        if os.path.exists(p) and name not in st["hashes"]:
            st["hashes"][name] = _digest(p)
    _save(st, path)
    return st


def decoy_problems(path=None):
    """[(name, 'missing' or 'changed')] for each decoy that no longer matches what MintCare wrote."""
    st = state(path)
    if not st["folder"]:
        return []
    problems = []
    for name, digest in st["hashes"].items():
        p = os.path.join(st["folder"], name)
        if not os.path.exists(p):
            problems.append((name, "missing"))
        elif _digest(p) != digest:
            problems.append((name, "changed"))
    return problems


def remove_decoys(path=None):
    """Move every decoy to the Trash and forget them."""
    from gi.repository import Gio
    st = state(path)
    for name in (st["hashes"] if st["folder"] else {}):
        p = os.path.join(st["folder"], name)
        if os.path.exists(p):
            Gio.File.new_for_path(p).trash(None)
    st["hashes"] = {}
    _save(st, path)


# ------------------------------------------------------------------ downloads

def is_download(name):
    """True for a finished download: not hidden, and not a browser's partial file."""
    return not name.startswith(".") and not name.lower().endswith(PARTIAL)


def clamscan_ready():
    return shutil.which("clamscan") is not None


def scan_file(path):
    """('clean' | 'infected' | 'error', clamscan's output). Needs ClamAV's virus database; no root."""
    rc, o = run(["clamscan", "--no-summary", path], timeout=900)
    return {0: "clean", 1: "infected"}.get(rc, "error"), o.strip()


def check_download(path, notify):
    status, text = scan_file(path)
    if status == "infected":
        notify(_("Possible malware in Downloads: {0}. Keep it closed and check it on the Security page.")
               .format(os.path.basename(path)))
    elif status == "error":
        print(f"clamscan could not check {path}: {text}", file=sys.stderr, flush=True)


def start_scanner(notify):
    """Start the one thread that checks downloads, a file at a time. Returns the queue to put paths on.
    One thread, so a burst of new files cannot start a ClamAV scan (and its memory) for every file."""
    scans = queue.Queue()

    def work():
        while True:
            path = scans.get()
            try:
                check_download(path, notify)
            except Exception as err:            # one bad file must not stop the checks for the rest
                print(f"clamscan could not check {path}: {err}", file=sys.stderr, flush=True)
            finally:
                scans.task_done()

    threading.Thread(target=work, daemon=True).start()
    return scans


# ------------------------------------------------------------------ the service

def _watch_folder(folder, handler):
    from gi.repository import Gio
    mon = Gio.File.new_for_path(folder).monitor_directory(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", lambda _m, f, _o, event: handler(f, event))
    return mon


def watch():
    """Watch the decoys and the Downloads folder until the session ends. Returns at once if both are off."""
    from gi.repository import Gio, GLib
    from . import alerts, space
    notify = alerts.notify
    E = Gio.FileMonitorEvent
    st = state()
    monitors = []
    if st["decoys"] and st["folder"] and os.path.isdir(st["folder"]):
        names = set(st["hashes"])
        changed_events = {E.CHANGES_DONE_HINT, E.DELETED, E.RENAMED, E.MOVED_OUT}

        def on_decoy(f, event):
            name = f.get_basename()
            if name not in names or event not in changed_events:
                return
            for n, kind in decoy_problems():
                if n == name and kind == "missing":
                    notify(_("A decoy file in Documents was deleted or renamed: {0}. Something may be changing "
                             "your files.").format(n))
                elif n == name:
                    notify(_("A decoy file in Documents was changed: {0}. Something may be changing your "
                             "files.").format(n))
        monitors.append(_watch_folder(st["folder"], on_decoy))
    if st["downloads"] and clamscan_ready() and os.path.isdir(space.downloads_dir()):
        seen = set()
        new_events = {E.CREATED, E.CHANGES_DONE_HINT, E.MOVED_IN, E.RENAMED}
        scans = start_scanner(notify)

        def on_download(f, event):
            path, name = f.get_path(), f.get_basename()
            if event not in new_events or not path or not is_download(name) or not os.path.isfile(path):
                return
            try:
                key = (path, os.path.getsize(path), os.path.getmtime(path))
            except OSError:
                return
            if key not in seen:               # a download is checked once, however many events it causes
                seen.add(key)
                scans.put(path)
        monitors.append(_watch_folder(space.downloads_dir(), on_download))
    if monitors:
        GLib.MainLoop().run()


# ------------------------------------------------------------------ turning it on and off

def unit_text():
    cmd = shlex.join(maintenance.launcher() + ["guard"])
    return ("# Managed by MintCare\n[Unit]\nDescription=MintCare guard: decoy files and download checks\n\n"
            f"[Service]\nExecStart={cmd}\nRestart=on-failure\nNice=10\n\n[Install]\nWantedBy=default.target\n")


def sync_unit(st):
    """Run the user service while either part is on; stop it and remove the unit otherwise."""
    if st["decoys"] or st["downloads"]:
        maintenance.UNIT_DIR.mkdir(parents=True, exist_ok=True)
        (maintenance.UNIT_DIR / UNIT).write_text(unit_text())
        run(["systemctl", "--user", "daemon-reload"])
        rc, o = run(["systemctl", "--user", "enable", "--now", UNIT])
        if rc != 0:
            raise RuntimeError(o)
        run(["systemctl", "--user", "restart", UNIT])          # pick up the new settings
    else:
        run(["systemctl", "--user", "disable", "--now", UNIT])
        (maintenance.UNIT_DIR / UNIT).unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])


def set_feature(name, on, folder=None, path=None):
    """Turn "decoys" or "downloads" on or off, then start or stop the service to match."""
    if name == "downloads" and on and not clamscan_ready():
        raise RuntimeError(_("ClamAV isn't installed (clamscan not found)"))
    if name == "decoys":
        if on:
            make_decoys(folder, path)
        else:
            remove_decoys(path)
    st = state(path)
    st[name] = bool(on)
    _save(st, path)
    sync_unit(st)
