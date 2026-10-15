"""Startup applications (XDG autostart). Changes are per-user: no root needed.

System entries live in /etc/xdg/autostart; to switch one off we write a copy to
~/.config/autostart with X-GNOME-Autostart-enabled=false, which is exactly what
Cinnamon's own Startup Applications tool does.
"""

import os
from pathlib import Path

from gi.repository import GLib

SYSTEM_DIRS = [Path("/etc/xdg/autostart")]
USER_DIR = Path(GLib.get_user_config_dir()) / "autostart"
DE = "Desktop Entry"

# Desktop plumbing: switching these off breaks keyboard shortcuts, keyrings, password prompts…
ESSENTIAL_PREFIXES = (
    "cinnamon-", "at-spi", "gnome-keyring", "polkit", "xapp-sn-watcher", "user-dirs-update",
    "xdg-user-dirs", "im-launch", "org.gnome.SettingsDaemon", "mate-settings-daemon",
    "xfce4-", "xfsettingsd", "pulseaudio", "pipewire",
)


def _desktops():
    return [d for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]


def _get(kf, key, default=""):
    try:
        return kf.get_locale_string(DE, key, None)
    except GLib.Error:
        return default


def _bool(kf, key, default):
    try:
        return kf.get_boolean(DE, key)
    except GLib.Error:
        return default


def _list(kf, key):
    try:
        return kf.get_string_list(DE, key)
    except GLib.Error:
        return []


def _load(path):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(path), GLib.KeyFileFlags.KEEP_COMMENTS | GLib.KeyFileFlags.KEEP_TRANSLATIONS)
        return kf
    except GLib.Error:
        return None


def entries():
    """All autostart entries that apply to the current desktop, sorted by name."""
    desks = _desktops()
    files = {}
    for d in SYSTEM_DIRS:
        for p in sorted(d.glob("*.desktop")):
            files.setdefault(p.name, {})["system"] = p
    if USER_DIR.exists():
        for p in sorted(USER_DIR.glob("*.desktop")):
            files.setdefault(p.name, {})["user"] = p

    result = []
    for name, src in files.items():
        sys_kf = _load(src["system"]) if "system" in src else None
        usr_kf = _load(src["user"]) if "user" in src else None
        kf = usr_kf or sys_kf
        if kf is None:
            continue
        ref = sys_kf or kf  # OnlyShowIn etc. come from the original entry
        only, not_in = _list(ref, "OnlyShowIn"), _list(ref, "NotShowIn")
        if only and not any(d in only for d in desks):
            continue
        if any(d in not_in for d in desks):
            continue
        enabled = _bool(kf, "X-GNOME-Autostart-enabled", True) and not _bool(kf, "Hidden", False)
        delay = _get(kf, "X-GNOME-Autostart-Delay")
        result.append({
            "file": name,
            "name": _get(kf, "Name") or name[:-8],
            "comment": _get(kf, "Comment"),
            "exec": _get(kf, "Exec"),
            "icon": _get(kf, "Icon"),
            "delay": int(delay) if delay.isdigit() else 0,
            "enabled": enabled,
            "origin": "system" if sys_kf else "user",
            "overridden": bool(sys_kf and usr_kf),
            "essential": name.startswith(ESSENTIAL_PREFIXES),
        })
    result.sort(key=lambda e: (e["essential"], e["name"].lower()))
    return result


def _user_copy(file):
    """The user's override of an entry, starting from the system file if there is none yet."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    kf = _load(USER_DIR / file)
    if kf is None:
        sysf = next((d / file for d in SYSTEM_DIRS if (d / file).exists()), None)
        if sysf is None:
            raise FileNotFoundError(file)
        kf = _load(sysf)
    return kf


def set_delay(file, seconds):
    """Start an entry `seconds` after login (0 = straight away), for the current user only."""
    kf = _user_copy(file)
    if seconds:
        kf.set_string(DE, "X-GNOME-Autostart-Delay", str(int(seconds)))
    else:
        try:
            kf.remove_key(DE, "X-GNOME-Autostart-Delay")
        except GLib.Error:
            pass
    kf.save_to_file(str(USER_DIR / file))


def set_enabled(file, enabled):
    """Enable/disable an entry for the current user only."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    user = USER_DIR / file
    kf = _load(user)
    if kf is None:
        sysf = next((d / file for d in SYSTEM_DIRS if (d / file).exists()), None)
        if sysf is None:
            raise FileNotFoundError(file)
        if enabled:
            return  # no user override and the system entry is already enabled
        kf = _load(sysf)
    kf.set_boolean(DE, "X-GNOME-Autostart-enabled", enabled)
    if enabled:
        try:
            kf.remove_key(DE, "Hidden")
        except GLib.Error:
            pass
    kf.save_to_file(str(user))
