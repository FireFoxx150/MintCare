"""Flatpak app inventory and sandbox permission management (like Flatseal).

Permission changes are written as per-user overrides with `flatpak override --user`,
so they need no root and can be undone with "Reset".
"""

from pathlib import Path

from gi.repository import GLib

from .util import has_cmd, out, q, run

USER_OVERRIDES = Path(GLib.get_user_data_dir()) / "flatpak" / "overrides"
SYSTEM_OVERRIDES = Path("/var/lib/flatpak/overrides")
LIST_KEYS = ("shared", "sockets", "devices", "features", "filesystems")

# id: (title, explanation, (list-key, value), grant flag, revoke flag, risk weight)
PERMISSIONS = [
    ("network", "Network access", "Internet and local network",
     ("shared", "network"), "--share=network", "--unshare=network", 0),
    ("home", "Home folder", "Read and write all your personal files",
     ("filesystems", "home"), "--filesystem=home", "--nofilesystem=home", 2),
    ("host", "Entire filesystem", "Read and write almost every file on the computer",
     ("filesystems", "host"), "--filesystem=host", "--nofilesystem=host", 3),
    ("downloads", "Downloads folder", "Read and write ~/Downloads",
     ("filesystems", "xdg-download"), "--filesystem=xdg-download", "--nofilesystem=xdg-download", 0),
    ("devices", "All devices", "Webcams, game controllers, USB and GPU devices",
     ("devices", "all"), "--device=all", "--nodevice=all", 2),
    ("sound", "Sound & microphone", "PulseAudio / PipeWire audio",
     ("sockets", "pulseaudio"), "--socket=pulseaudio", "--nosocket=pulseaudio", 0),
    ("x11", "X11 windowing", "Legacy display access: can read other windows and keystrokes",
     ("sockets", "x11"), "--socket=x11", "--nosocket=x11", 1),
]
PERM_BY_ID = {p[0]: p for p in PERMISSIONS}


def available():
    return has_cmd("flatpak")


def apps():
    if not available():
        return []
    _rc, o = run(["flatpak", "list", "--app", "--columns=application,name,version,origin,installation,size"],
                 env_extra={"LC_ALL": "C.UTF-8"})
    o = o.replace("\xa0", " ")
    res = []
    for line in o.splitlines():
        f = line.split("\t")
        if len(f) < 6:
            continue
        app = {"id": f[0], "name": f[1] or f[0], "version": f[2], "origin": f[3],
               "install": f[4], "size": f[5]}
        app["perms"], app["overridden"] = effective_permissions(app["id"])
        app["risk"] = risk(app["perms"])
        res.append(app)
    res.sort(key=lambda a: a["name"].lower())
    return res


def _parse_context(text):
    ctx = {k: [] for k in LIST_KEYS}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[Context]" or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k in ctx:
            ctx[k] = [x for x in v.split(";") if x]
    return ctx


def _apply_override(eff, path):
    try:
        text = Path(path).read_text()
    except OSError:
        return False
    ov = _parse_context(text)
    for k, items in ov.items():
        for it in items:
            if it.startswith("!"):
                base = it[1:].split(":")[0]
                eff[k] = [x for x in eff[k] if x.split(":")[0] != base]
            elif it not in eff[k]:
                eff[k].append(it)
    return True


def effective_permissions(app_id):
    """App metadata permissions with system then user overrides applied."""
    meta = out(["flatpak", "info", "--show-permissions", app_id])
    eff = _parse_context(meta)
    overridden = False
    for base in (SYSTEM_OVERRIDES, USER_OVERRIDES):
        for name in ("global", app_id):
            if _apply_override(eff, base / name) and name == app_id:
                overridden = overridden or base == USER_OVERRIDES
    return eff, overridden


def has(perms, key):
    lk, val = PERM_BY_ID[key][3]
    items = [x.split(":")[0] for x in perms.get(lk, [])]
    if key == "home":
        return "home" in items or "~" in items or "host" in items
    if key == "host":
        return "host" in items or "host-os" in items
    if key == "x11":
        return "x11" in items or "fallback-x11" in items and "wayland" not in perms.get("sockets", [])
    return val in items


def risk(perms):
    """('strong'|'moderate'|'weak', score, [reasons])."""
    score, why = 0, []
    for pid, title, _d, _k, _g, _r, w in PERMISSIONS:
        if w and has(perms, pid):
            if pid == "home" and has(perms, "host"):
                continue
            score += w
            why.append(title.lower())
    if score <= 1:
        return "strong", score, why
    if score <= 3:
        return "moderate", score, why
    return "weak", score, why


def set_permission(app_id, key, grant):
    flag = PERM_BY_ID[key][4 if grant else 5]
    rc, o = run(["flatpak", "override", "--user", flag, app_id])
    if rc != 0:
        raise RuntimeError(o or f"flatpak override failed ({rc})")


def reset(app_id):
    rc, o = run(["flatpak", "override", "--user", "--reset", app_id])
    if rc != 0:
        raise RuntimeError(o or "reset failed")


def updates_available():
    """Number of pending app/runtime updates (needs network; may take a few seconds)."""
    n = 0
    for scope in ("--system", "--user"):
        rc, o = run(["flatpak", "remote-ls", "--updates", scope, "--columns=application"], timeout=60)
        if rc == 0:
            n += len([l for l in o.splitlines() if l.strip()])
    return n


def update_steps():
    return ([("fp-update-sys", "Update system Flatpaks",
              "flatpak update --system -y --noninteractive")],
            [("fp-update-user", "Update your Flatpaks",
              "flatpak update --user -y --noninteractive || true")])


def repair_steps():
    return ([("fp-repair-sys", "Repair system Flatpak installation",
              "flatpak repair --system\nflatpak uninstall --system --unused -y --noninteractive || true")],
            [("fp-repair-user", "Repair your Flatpak installation",
              "flatpak repair --user || true")])


def uninstall_steps(app):
    scope = "--user" if app["install"] == "user" else "--system"
    step = (f"fp-rm", f"Uninstall {app['name']}",
            f"flatpak uninstall {scope} -y --noninteractive {q(app['id'])}")
    return ([step], []) if scope == "--system" else ([], [step])
