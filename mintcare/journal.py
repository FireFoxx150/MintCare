"""Errors since boot, grouped by program (one crash = one event) and explained in plain English."""

import json
import os
import re
import subprocess
import threading
import time

from .i18n import _
from .util import run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
MAX_GROUPS = 400        # kinds of error kept with a sample; further kinds share one heading
MAX_LINE = 16384        # an entry longer than this is read to its end and counted, not kept
BOOT_TIMEOUT = 30       # seconds; a boot that takes longer is reported as incomplete
RANK = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}

# (pattern on "identifier: message", level, explanation[, (button label, kind, payload)])
# kinds: "launch" (run a program), "page" (open a MintCare page), "check" (apply that check's fix),
# "tweak" (apply a tweak)
EXPLAIN = [
    (r"I/O error|blk_update_request|Buffer I/O error|critical medium error", FAIL,
     _("A disk reported read/write errors. Check Hardware › drives (SMART) and back up important files."),
     (_("Check drives"), "page", "hardware")),
    (r"dumped core", WARN,
     _("A program crashed. If it keeps happening, update or reinstall it; the program name is shown above."),
     (_("View crash reports"), "page", "troubleshoot")),
    (r"Out of memory|oom-kill|Killed process", WARN,
     _("The system ran out of memory and closed a program. Consider zram (Optimise) or closing heavy apps."),
     (_("Enable zram"), "tweak", "zram")),
    (r"hpmud.*usb_open|usb_open: Permission denied", OK,
     _("The HP printer/scanner driver probed your USB device without permission (colour-management "
     "service). Harmless — printing and scanning still work.")),
    (r"gkr-pam", INFO,
     _("Your keyring password doesn't match your login password, so it didn't unlock at login. Fix: open "
     "Passwords and Keys › right-click Login › Change Password, and set it to your login password."),
     (_("Open Passwords and Keys"), "launch", "seahorse")),
    (r"pam_ecryptfs", INFO,
     _("The ecryptfs login module is installed but unused. MintCare's scan offers to remove it."),
     (_("Remove ecryptfs"), "check", "ecryptfs")),
    (r"Sap driver|sap-server|sap_server", OK,
     _("Bluetooth's SIM Access plugin failed to start. Harmless; MintCare's scan can switch the plugin off."),
     (_("Switch the plugin off"), "check", "btsap")),
    (r"EVIOCSKEYCODE", OK,
     _("A keyboard hot-key mapping the kernel didn't accept. Harmless.")),
    (r"a password is required|incorrect password attempt|authentication failure", INFO,
     _("A command asked for an administrator password and didn't get one.")),
    (r"ACPI (BIOS )?Error|ACPI Error|AE_NOT_FOUND|AE_ALREADY_EXISTS", OK,
     _("Your motherboard's firmware tables contain a small bug. Usually harmless; a BIOS update can fix it.")),
    (r"NVRM|nvidia", INFO,
     _("A message from the NVIDIA driver. If you see glitches, check Driver Manager for a newer driver."),
     ("Open Driver Manager", "launch", "driver-manager")),
    (r"Bluetooth: hci\d", OK, _("The Bluetooth adapter reported a firmware quirk. Usually harmless.")),
    (r"device descriptor read|unable to enumerate USB device|over-current", WARN,
     _("A USB device failed to connect properly — try another port or cable, or an unpowered hub may be the cause.")),
    (r"Failed to start|failed with result", WARN,
     _("A system service failed to start. See the Failed services check for details."),
     ("Open Boot & Startup", "page", "startup")),
    (r"firmware: failed to load|Direct firmware load .* failed", INFO,
     _("A driver couldn't find an optional firmware file. Usually harmless unless that device misbehaves.")),
    (r"wireplumber|pipewire", INFO, _("A message from the sound system (PipeWire)."),
     (_("Open sound troubleshooter"), "page", "troubleshoot")),
]


def _text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        try:
            return bytes(v).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return ""
    return "" if v is None else str(v)


def _parse(line):
    """One journal entry as {'who', 'msg', 'ts'}, or None for a line that isn't one or has no text."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    who = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or "unknown"
    msg = _text(e.get("MESSAGE")).strip()
    if not msg or "a password is required" in msg:
        return None            # blank kernel lines, failed non-interactive sudo checks
    if who == "systemd-coredump":
        m = re.search(r"\((\S+)\) of user", msg)
        who = f"crash: {m.group(1) if m else 'program'}"
        cmd = _text(e.get("COREDUMP_CMDLINE") or e.get("COREDUMP_EXE"))
        cmd = re.sub(r"(\\n|\s)+", " ", cmd).strip()
        msg = msg.split("\n")[0] + (f" Command: {cmd[:200]}" if cmd else "")
    ts = int(e.get("__REALTIME_TIMESTAMP", "0") or 0) / 1e6
    return {"who": who, "msg": msg.split("\n")[0][:300], "ts": ts}


def _read(args):
    """(exit code, entries) from journalctl's JSON output, with blank and noisy lines left out."""
    rc, o = run(["journalctl", *args, "-q", "--no-pager", "-o", "json"], timeout=30)
    return rc, [e for e in (_parse(line) for line in o.splitlines()) if e]


def entries(boot="-b", priority="3", extra=()):
    return _read([*([boot] if boot else []), "-p", priority, *extra])[1]


SINCE = {"day": "-1 day", "week": "-7 days"}


def browse(period, priority, text=""):
    """(exit code, [(time, program, message)]) for the System journal view: the newest 500 lines matching `text`."""
    scope = ["-b"] if period == "boot" else [f"--since={SINCE[period]}"]
    rc, found = _read([*scope, "-p", priority, "-n", "1000"])
    needle = text.strip().lower()
    rows = [(time.strftime("%d %b %H:%M:%S", time.localtime(e["ts"])), e["who"], e["msg"]) for e in found
            if needle in f"{e['who']} {e['msg']}".lower()]
    return rc, rows[-500:]


def explain(who, msg):
    """(level, explanation, action or None)."""
    hay = f"{who}: {msg}"
    for entry in EXPLAIN:
        pat, level, text = entry[:3]
        if re.search(pat, hay, re.I):
            return level, text, (entry[3] if len(entry) > 3 else None)
    return INFO, "", None


class _Groups:
    """Errors counted by program and message shape. Memory stays bounded: once MAX_GROUPS kinds are kept, new
    kinds share one heading that keeps their count and the most serious level among them."""

    def __init__(self):
        self.groups, self.rest, self.long = {}, None, None

    @staticmethod
    def _count(g, ts, level=None):
        g["count"] += 1
        g["last"] = max(g["last"], ts)
        if level and RANK[level] < RANK[g["level"]]:
            g["level"] = level

    def add(self, who, msg, ts):
        key = (who, re.sub(r"\d+", "#", msg)[:120])
        if key in self.groups:
            return self._count(self.groups[key], ts)
        level, text, action = explain(who, msg)
        if len(self.groups) < MAX_GROUPS:
            self.groups[key] = {"who": who, "count": 0, "sample": msg, "last": 0, "level": level,
                                "explanation": text, "action": action}
            return self._count(self.groups[key], ts)
        if self.rest is None:
            self.rest = {"who": _("(other kinds)"), "count": 0, "sample": "", "last": 0, "level": level,
                         "explanation": _("More kinds of error than MintCare lists one by one. "
                                          "Open the System journal to see them."), "action": None}
        self._count(self.rest, ts, level)

    def add_long(self, ts):
        if self.long is None:
            self.long = {"who": _("(long entries)"), "count": 0, "sample": "", "last": 0, "level": WARN,
                         "explanation": _("Journal entries too long to read were skipped, and are counted here. "
                                          "Open the System journal to see them."), "action": None}
        self._count(self.long, ts)

    def finish(self, extra=()):
        out = list(self.groups.values()) + [g for g in (self.rest, self.long, *extra) if g]
        out.sort(key=lambda g: (RANK[g["level"]], -g["count"]))
        return out


def _lines(stream):
    """The lines of a text stream, one at a time. A line longer than MAX_LINE is read to its end and yielded as None."""
    while True:
        line = stream.readline(MAX_LINE)
        if not line:
            return
        if line.endswith("\n"):
            yield line
            continue
        while True:
            more = stream.readline(MAX_LINE)
            if not more or more.endswith("\n"):
                break
        yield None


def _grouped_stream():
    """Errors since boot, grouped as journalctl prints them, so the journal is never held in memory whole."""
    try:
        p = subprocess.Popen(["journalctl", "-b", "-p", "3", "-q", "--no-pager", "-o", "json"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace",
                             env=dict(os.environ, LC_ALL="C", LANG="C"))
    except OSError:
        return []
    groups, late = _Groups(), [False]

    def stop():
        late[0] = True
        p.kill()

    timer = threading.Timer(BOOT_TIMEOUT, stop)
    timer.start()
    try:
        for line in _lines(p.stdout):
            if line is None:
                groups.add_long(0)
                continue
            e = _parse(line)
            if e:
                groups.add(e["who"], e["msg"], e["ts"])
        p.wait()
    finally:
        timer.cancel()
        p.stdout.close()
    note = [{"who": "MintCare", "count": 0, "sample": "", "last": 0, "level": WARN, "action": None,
             "explanation": _("The journal was too long to read in time, so this list may be incomplete.")}]
    return groups.finish(note if late[0] else ())


def grouped(items=None):
    """[{'who','count','sample','last','level','explanation','action'}] sorted most serious first."""
    if items is None:
        return _grouped_stream()
    groups = _Groups()
    for e in items:
        groups.add(e["who"], e["msg"], e["ts"])
    return groups.finish()


def report(groups=None):
    """Readable text for the details panel."""
    groups = grouped() if groups is None else groups
    if not groups:
        return _("No errors have been logged since this boot.")
    words = {FAIL: "PROBLEM", WARN: "WARNING", INFO: "NOTE", OK: "HARMLESS"}
    lines = []
    for g in groups:
        when = time.strftime("%H:%M", time.localtime(g["last"])) if g["last"] else "?"
        lines.append(f"[{words[g['level']]}] {g['who']} — {g['count']}× (last at {when})")
        lines.append(f"    {g['sample']}")
        if g["explanation"]:
            lines.append(f"    → {g['explanation']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def actions(groups=None):
    """Unique fix buttons for the details panel: [(label, kind, payload)]."""
    groups = grouped() if groups is None else groups
    seen, out_ = set(), []
    for g in groups:
        a = g.get("action")
        if a and a not in seen:
            seen.add(a)
            out_.append(a)
    return out_
