"""Errors since boot, grouped by program (one crash = one event) and explained in plain English."""

import json
import re
import time

from .i18n import _
from .util import run

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"

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


def entries(boot="-b"):
    rc, o = run(["journalctl", boot, "-p", "3", "-q", "--no-pager", "-o", "json"], timeout=30)
    out = []
    for line in o.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        who = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or "unknown"
        msg = _text(e.get("MESSAGE")).strip()
        if not msg or "a password is required" in msg:
            continue               # blank kernel lines, failed non-interactive sudo checks
        if who == "systemd-coredump":
            m = re.search(r"\((\S+)\) of user", msg)
            who = f"crash: {m.group(1) if m else 'program'}"
            cmd = _text(e.get("COREDUMP_CMDLINE") or e.get("COREDUMP_EXE"))
            cmd = re.sub(r"(\\n|\s)+", " ", cmd).strip()
            msg = msg.split("\n")[0] + (f" Command: {cmd[:200]}" if cmd else "")
        ts = int(e.get("__REALTIME_TIMESTAMP", "0") or 0) / 1e6
        out.append({"who": who, "msg": msg.split("\n")[0][:300], "ts": ts})
    return out


def explain(who, msg):
    """(level, explanation, action or None)."""
    hay = f"{who}: {msg}"
    for entry in EXPLAIN:
        pat, level, text = entry[:3]
        if re.search(pat, hay, re.I):
            return level, text, (entry[3] if len(entry) > 3 else None)
    return INFO, "", None


def grouped(items=None):
    """[{'who','count','sample','last','level','explanation'}] sorted most serious first."""
    items = entries() if items is None else items
    groups = {}
    for e in items:
        key = (e["who"], re.sub(r"\d+", "#", e["msg"])[:120])
        g = groups.setdefault(key, {"who": e["who"], "count": 0, "sample": e["msg"], "last": 0})
        g["count"] += 1
        g["last"] = max(g["last"], e["ts"])
    out = []
    for g in groups.values():
        g["level"], g["explanation"], g["action"] = explain(g["who"], g["sample"])
        out.append(g)
    rank = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}
    out.sort(key=lambda g: (rank[g["level"]], -g["count"]))
    return out


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
