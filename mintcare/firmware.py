"""Firmware updates through fwupd / LVFS (the same service GNOME Software uses)."""

import json
import re

from .util import has_cmd, run


def available():
    return has_cmd("fwupdmgr")


def _json(args, timeout=40):
    rc, o = run(["fwupdmgr", *args, "--json"], timeout=timeout)
    start = o.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(o[start:])
    except ValueError:
        return {}


def devices():
    """Devices fwupd knows about: [{'name','vendor','version','updatable'}]."""
    out = []
    for d in _json(["get-devices"]).get("Devices", []):
        if not d.get("Name"):
            continue
        out.append({
            "name": d["Name"], "vendor": d.get("Vendor", ""), "version": d.get("Version", ""),
            "updatable": "updatable" in d.get("Flags", []),
            "id": d.get("DeviceId", ""),
        })
    out.sort(key=lambda d: (not d["updatable"], d["name"]))
    return out


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def updates():
    """Pending firmware updates: [{'device','current','new','summary','urgency','reboot'}]."""
    out = []
    for d in _json(["get-updates"]).get("Devices", []):
        rels = d.get("Releases") or []
        if not rels:
            continue
        r = rels[0]
        flags = set(d.get("Flags", [])) | set(r.get("Flags", []))
        out.append({
            "device": d.get("Name", "Device"),
            "current": d.get("Version", "?"),
            "new": r.get("Version", "?"),
            "summary": _clean(r.get("Summary") or r.get("Description", ""))[:240],
            "urgency": r.get("Urgency", ""),
            "reboot": bool(flags & {"needs-reboot", "needs-shutdown"}),
        })
    return out


def metadata_age():
    """Human text such as '14 hours' for the LVFS catalogue, or ''."""
    rc, o = run(["fwupdmgr", "get-remotes"], timeout=20)
    m = re.findall(r"Age:\s+(.+)", o)
    return m[0].strip() if m else ""


def refresh_step():
    return ("fw-refresh", "Download the latest firmware catalogue", "fwupdmgr refresh --force")


def update_step():
    return ("fw-update", "Install firmware updates", "fwupdmgr update --assume-yes --no-reboot-check")
