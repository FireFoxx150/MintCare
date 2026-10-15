"""Junk cleaner. scan() runs unprivileged and estimates what each item frees."""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .i18n import _, ngettext
from .util import dir_size, has_cmd, out, q

HOME = Path.home()


@dataclass
class CleanItem:
    id: str
    title: str
    description: str
    scan: Callable[[], tuple]           # -> (bytes or None, detail)
    root_script: Optional[str] = None
    user_script: Optional[str] = None
    default: bool = True
    available: Callable[[], bool] = lambda: True


def _files_size(paths):
    total = 0
    for p in paths:
        try:
            total += p.lstat().st_blocks * 512
        except OSError:
            pass
    return total


def s_apt():
    debs = list(Path("/var/cache/apt/archives").glob("*.deb"))
    return _files_size(debs), ngettext("{0} downloaded package file", "{0} downloaded package files", len(debs)).format(len(debs))


def _autoremove_pkgs():
    o = out(["apt-get", "-s", "-o", "Debug::NoLocking=1", "autoremove", "--purge"], timeout=60)
    return [l.split()[1] for l in o.splitlines() if l.startswith("Purg ") or l.startswith("Remv ")]


def s_autoremove():
    pkgs = _autoremove_pkgs()
    if not pkgs:
        return 0, _("Nothing to remove")
    o = out(["dpkg-query", "-W", "-f=${Installed-Size}\n"] + pkgs)
    kb = sum(int(x) for x in o.split() if x.isdigit())
    kernels = sum(1 for p in pkgs if p.startswith("linux-image-"))
    detail = ngettext("{0} package", "{0} packages", len(pkgs)).format(len(pkgs))
    if kernels:
        detail += ngettext(", including {0} old kernel", ", including {0} old kernels", kernels).format(kernels)
    return kb * 1024, detail


def s_residual():
    pkgs = out("dpkg -l | awk '/^rc/{print $2}'").split()
    return None, ngettext("{0} removed package with leftover config", "{0} removed packages with leftover config",
                          len(pkgs)).format(len(pkgs))


def s_journal():
    o = out(["journalctl", "--disk-usage"])
    return None, o.replace("Archived and active journals take up ", "Journal uses ").rstrip(".") or "Unknown"


LOG_PATTERNS = ("*.gz", "*.[0-9]", "*.old", "*.xz")


def _old_logs():
    found = []
    for pat in LOG_PATTERNS:
        found += [p for p in Path("/var/log").rglob(pat) if p.is_file()]
    return found


def s_oldlogs():
    try:
        logs = _old_logs()
    except OSError:
        logs = []
    return _files_size(logs), ngettext("{0} rotated log file", "{0} rotated log files", len(logs)).format(len(logs))


def s_crash():
    files = [p for p in Path("/var/crash").glob("*") if p.is_file()]
    return _files_size(files), ngettext("{0} crash report", "{0} crash reports", len(files)).format(len(files))


def s_thumbs():
    return dir_size(HOME / ".cache/thumbnails"), _("Preview images regenerated on demand")


def _stale_cache_files(days=30):
    cutoff = time.time() - days * 86400
    root = HOME / ".cache"
    total = n = 0
    for dirpath, _d, files in os.walk(root, onerror=lambda e: None):
        for f in files:
            try:
                st = os.lstat(os.path.join(dirpath, f))
            except OSError:
                continue
            if max(st.st_atime, st.st_mtime) < cutoff:
                total += st.st_blocks * 512
                n += 1
    return total, n


def s_usercache():
    size, n = _stale_cache_files()
    return size, _('{0:,} files in ~/.cache unused for 30+ days').format(n)


DEV_CACHES = [HOME / ".cache/pip", HOME / ".npm/_cacache", HOME / ".cache/yarn",
              HOME / ".cache/go-build", HOME / ".cargo/registry/cache"]


def s_devcache():
    present = [p for p in DEV_CACHES if p.exists()]
    names = ", ".join(p.relative_to(HOME).as_posix() for p in present) or _("none found")
    return sum(dir_size(p) for p in present), names


def s_trash():
    t = HOME / ".local/share/Trash"
    n = len(list((t / "files").glob("*"))) if (t / "files").exists() else 0
    return dir_size(t), ngettext("{0} item in the Trash", "{0} items in the Trash", n).format(n)


def s_flatpak():
    o = out(["flatpak", "list", "--runtime", "--columns=application"])
    n = len(o.splitlines())
    return None, ngettext("{0} runtime installed; unused ones are removed", "{0} runtimes installed; unused ones are removed",
                    n).format(n)


SPICES = HOME / ".local/share/cinnamon"
SPICE_KEYS = ("enabled-applets", "enabled-desklets", "enabled-extensions")


def unused_spices(enabled=None):
    """Applets, desklets and extensions you downloaded but aren't using (never the built-in ones)."""
    if enabled is None:
        enabled = " ".join(out(["gsettings", "get", "org.cinnamon", k]) for k in SPICE_KEYS)
    if not enabled.strip():
        return []                           # couldn't read what's in use: report nothing rather than guess
    return [d for kind in ("applets", "desklets", "extensions") for d in sorted((SPICES / kind).glob("*"))
            if d.is_dir() and d.name not in enabled]


def s_spices():
    found = unused_spices()
    return sum(dir_size(d) for d in found), (", ".join(d.name for d in found[:4]) or _("none found"))


SPICES_SCRIPT = (
    "en=\"$(for k in " + " ".join(SPICE_KEYS) + "; do gsettings get org.cinnamon $k; done)\"\n"
    "[ -n \"$en\" ] || { echo 'Could not read the enabled add-ons; nothing removed'; exit 0; }\n"
    f"for d in {q(str(SPICES))}/applets/*/ {q(str(SPICES))}/desklets/*/ {q(str(SPICES))}/extensions/*/; do\n"
    "  [ -d \"$d\" ] || continue; u=\"$(basename \"$d\")\"\n"
    "  case \"$en\" in *\"$u\"*) ;; *) gio trash \"$d\" && echo \"Moved $u to the Trash\";; esac\n"
    "done")


VAR_APP = HOME / ".var/app"


def leftover_flatpak_data(installed=None):
    """~/.var/app folders of Flatpak apps that are no longer installed."""
    if installed is None:
        installed = out(["flatpak", "list", "--app", "--columns=application"]).split()
    if not installed:
        return []                           # flatpak list failed or nothing installed: don't guess
    return [d for d in sorted(VAR_APP.glob("*")) if d.is_dir() and d.name not in installed]


def s_flatpakdata():
    found = leftover_flatpak_data()
    return sum(dir_size(d) for d in found), (", ".join(d.name for d in found[:3]) or _("none found"))


FLATPAKDATA_SCRIPT = (
    "inst=\"$(flatpak list --app --columns=application)\"\n"
    "[ -n \"$inst\" ] || { echo 'Could not list installed Flatpaks; nothing removed'; exit 0; }\n"
    f"for d in {q(str(VAR_APP))}/*/; do\n"
    "  [ -d \"$d\" ] || continue; a=\"$(basename \"$d\")\"\n"
    "  printf '%s\\n' \"$inst\" | grep -qxF \"$a\" || { gio trash \"$d\" && echo \"Moved $a data to the Trash\"; }\n"
    "done")


# Browser caches: (id, name, cache dirs relative to HOME, process names)
BROWSERS = [
    ("firefox", "Firefox", [".cache/mozilla/firefox", ".var/app/org.mozilla.firefox/cache/mozilla"],
     ["firefox", "firefox-bin"]),
    ("brave", "Brave", [".cache/BraveSoftware", ".var/app/com.brave.Browser/cache"], ["brave"]),
    ("chrome", "Google Chrome", [".cache/google-chrome", ".var/app/com.google.Chrome/cache"], ["chrome"]),
    ("chromium", "Chromium", [".cache/chromium", ".var/app/org.chromium.Chromium/cache"], ["chromium"]),
    ("vivaldi", "Vivaldi", [".cache/vivaldi"], ["vivaldi-bin"]),
    ("edge", "Microsoft Edge", [".cache/microsoft-edge"], ["msedge"]),
    ("opera", "Opera", [".cache/opera"], ["opera"]),
    ("librewolf", "LibreWolf", [".cache/librewolf", ".var/app/io.gitlab.librewolf-community/cache"],
     ["librewolf"]),
]


def running(procs):
    names = set()
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            names.add(comm.read_text().strip())
        except OSError:
            pass
    return any(p in names or any(n.startswith(p) for n in names) for p in procs)


def _browser_scan(dirs, procs, name):
    def scan():
        present = [HOME / d for d in dirs if (HOME / d).exists()]
        size = sum(dir_size(p) for p in present)
        if running(procs):
            return size, _('{0} is open — close it first or this item is skipped').format(name)
        return size, _('Cached pages and images; {0} rebuilds them as you browse').format(name)
    return scan


def _browser_script(dirs, procs, name):
    paths = " ".join(q(str(HOME / d)) for d in dirs)
    pat = "|".join(procs)
    return (f"if pgrep -x -u \"$(id -u)\" '({pat})' >/dev/null; then echo 'Skipped: {name} is running'; "
            f"else for d in {paths}; do [ -d \"$d\" ] && find \"$d\" -mindepth 1 -delete; done; "
            f"echo 'Cleared {name} cache'; fi")


def _browser_items():
    items = []
    for bid, name, dirs, procs in BROWSERS:
        if not any((HOME / d).exists() for d in dirs):
            continue
        items.append(CleanItem(f"browser-{bid}", f"{name} cache", _("{0}'s disk cache.").format(name),
                               _browser_scan(dirs, procs, name),
                               user_script=_browser_script(dirs, procs, name)))
    return items


ITEMS = [
    CleanItem("apt", _("Package download cache"), _("Downloaded .deb files that are already installed."),
              s_apt, root_script="apt-get clean"),
    CleanItem("autoremove", _("Old kernels & orphaned packages"),
              _("Dependencies no longer needed and kernels older than the current fallback."),
              s_autoremove, root_script="DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y"),
    CleanItem("residual", _("Leftover package configs"), _("Config files of packages you already removed."),
              s_residual, root_script="dpkg -l | awk '/^rc/{print $2}' | xargs -r dpkg --purge"),
    CleanItem("journal", _("System journal older than 7 days"), _("Old systemd log entries."),
              s_journal, root_script="journalctl --vacuum-time=7d"),
    CleanItem("oldlogs", _("Rotated log files"), _("Compressed and rotated logs in /var/log."),
              s_oldlogs,
              root_script="find /var/log -type f \\( " +
                          " -o ".join(f"-name {q(p)}" for p in LOG_PATTERNS) + " \\) -delete"),
    CleanItem("crash", _("Crash reports"), _("Saved crash dumps in /var/crash."),
              s_crash, root_script="rm -f /var/crash/*"),
    CleanItem("flatpak", _("Unused Flatpak runtimes"), _("Runtimes left behind after apps were removed."),
              s_flatpak,
              root_script="flatpak uninstall --system --unused -y --noninteractive || true",
              user_script="flatpak uninstall --user --unused -y --noninteractive || true",
              available=lambda: has_cmd("flatpak")),
    CleanItem("thumbs", _("Thumbnail cache"), _("Cached file previews."), s_thumbs,
              user_script=f"rm -rf {q(HOME / '.cache/thumbnails')}/*"),
    CleanItem("usercache", _("Stale application cache"), _("Cache files no app has touched in 30 days."),
              s_usercache,
              user_script=f"find {q(HOME / '.cache')} -type f -atime +30 -mtime +30 -delete; "
                          f"find {q(HOME / '.cache')} -mindepth 1 -type d -empty -delete"),
    *_browser_items(),
    CleanItem("flatpakdata", _("Data of removed Flatpak apps"),
              _("Settings and files that uninstalled Flatpak apps left in ~/.var/app. Moved to the Trash."),
              s_flatpakdata, user_script=FLATPAKDATA_SCRIPT, default=False,
              available=lambda: has_cmd("flatpak") and VAR_APP.is_dir()),
    CleanItem("spices", _("Unused Cinnamon add-ons"),
              _("Applets, desklets and extensions you downloaded but don't use. Moved to the Trash."),
              s_spices, user_script=SPICES_SCRIPT, default=False,
              available=lambda: SPICES.is_dir() and has_cmd("gsettings")),
    CleanItem("devcache", _("Developer caches"), _("pip, npm, yarn, Go and Cargo download caches."),
              s_devcache, default=False,
              user_script="\n".join(f"rm -rf {q(p)}" for p in DEV_CACHES)),
    CleanItem("trash", _("Empty Trash"), _("Permanently deletes everything in your Trash."),
              s_trash, default=False, user_script="gio trash --empty"),
]

BY_ID = {i.id: i for i in ITEMS}
