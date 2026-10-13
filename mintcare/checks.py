"""System diagnostics. Each probe runs unprivileged and returns a Result.

A Result may carry a Fix:
  kind="root"   -> shell script run as root via pkexec
  kind="user"   -> shell script run as the current user
  kind="tweak"  -> apply the named optimisation tweak
  kind="launch" -> open a Mint tool (e.g. Timeshift, Driver Manager)
  kind="page"   -> jump to another page of the app (e.g. the cleaner)
"""

import os
import re
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .i18n import _
from . import tweaks
from .util import (cpu_vendor, has_cmd, has_ssd, human, is_laptop, out, pkg_installed,
                   read, run, sysctl, unit_enabled, unit_active)

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


@dataclass
class Fix:
    kind: str
    payload: str
    label: str = "Fix"


@dataclass
class Result:
    status: str
    summary: str
    fix: Optional[Fix] = None


@dataclass
class Check:
    id: str
    title: str
    category: str
    probe: Callable[[], Result]

    def run(self):
        try:
            return self.probe()
        except Exception as e:  # a broken probe must never break the scan
            return Result(INFO, f"Could not check: {e}")


def tweak_fix(tid, label="Enable"):
    return Fix("tweak", tid, label)


# ------------------------------------------------------------------ Network

def c_bbr():
    cc = sysctl("net.ipv4.tcp_congestion_control")
    qd = sysctl("net.core.default_qdisc")
    if cc == "bbr":
        if qd in ("fq", "fq_pie", "cake"):
            return Result(OK, f"BBR is enabled (qdisc {qd})")
        return Result(WARN, f"BBR is on but qdisc is '{qd}'; 'fq' gives proper pacing",
                      tweak_fix("bbr"))
    if not tweaks.BY_ID["bbr"].available():
        return Result(INFO, f"Using '{cc}'; this kernel has no BBR module")
    return Result(WARN, f"Using '{cc}' congestion control; BBR is faster on most links",
                  tweak_fix("bbr"))


def c_internet():
    for host, port in (("1.1.1.1", 443), ("9.9.9.9", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            return Result(OK, _("Internet connection is working"))
        except OSError:
            continue
    return Result(FAIL, _("No internet connection (could not reach 1.1.1.1, 9.9.9.9 or 8.8.8.8)"),
                  Fix("root", "systemctl restart NetworkManager", _("Restart networking")))


def c_dns():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        return Result(INFO, _("Skipped: no internet connection"))
    try:
        socket.getaddrinfo("linuxmint.com", 443)
        return Result(OK, _("DNS name resolution is working"))
    except OSError:
        return Result(FAIL, _("Internet works but DNS lookups fail"),
                      Fix("root", "resolvectl flush-caches 2>/dev/null || true\n"
                                  "systemctl restart systemd-resolved\nsystemctl restart NetworkManager",
                          _("Repair DNS")))


def c_hostname():
    host = socket.gethostname()
    hosts = read("/etc/hosts")
    for line in hosts.splitlines():
        parts = line.split("#")[0].split()
        if host in parts[1:]:
            return Result(OK, f"Hostname '{host}' is in /etc/hosts")
    return Result(WARN, f"Hostname '{host}' missing from /etc/hosts (sudo can be slow)",
                  Fix("root", f"echo '127.0.1.1\t{host}' >> /etc/hosts", _("Add entry")))


# ------------------------------------------------------------------ Packages

def c_dpkg_audit():
    o = out(["dpkg", "--audit"])
    if not o:
        return Result(OK, _("No half-installed or unconfigured packages"))
    return Result(FAIL, _("Some packages were left half-installed (interrupted update?)"),
                  Fix("root", "dpkg --configure -a\nDEBIAN_FRONTEND=noninteractive apt-get -f install -y",
                      _("Repair packages")))


def c_apt_broken():
    rc, o = run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "check"])
    if rc == 0:
        return Result(OK, _("No broken package dependencies"))
    return Result(FAIL, _("Broken package dependencies detected"),
                  Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get -f install -y", _("Repair")))


def c_updates():
    o = out(["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], timeout=60)
    n = sum(1 for line in o.splitlines() if line.startswith("Inst "))
    if n == 0:
        return Result(OK, _("System is up to date (per the last package list refresh)"))
    upd = "mintupdate-cli -r -y upgrade" if has_cmd("mintupdate-cli") else \
          "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade"
    return Result(WARN, f"{n} update{'s' if n != 1 else ''} available",
                  Fix("root", upd, _("Install updates")))


def c_reboot():
    if Path("/run/reboot-required").exists():
        pkgs = read("/run/reboot-required.pkgs").split()
        extra = f" ({', '.join(sorted(set(pkgs))[:4])})" if pkgs else ""
        return Result(WARN, "A restart is required to finish updates" + extra)
    running = out(["uname", "-r"])
    newest = sorted((p.name[len("vmlinuz-"):] for p in Path("/boot").glob("vmlinuz-*")),
                    key=lambda v: [int(x) if x.isdigit() else x for x in v.replace("-", ".").split(".")])
    if newest and newest[-1] != running:
        return Result(WARN, f"Newer kernel {newest[-1]} installed; running {running}. Restart to use it")
    return Result(OK, f"Running the newest installed kernel ({running})")


def c_kernels():
    o = out("dpkg-query -W -f='${Status} ${Package}\\n' 'linux-image-[0-9]*' 2>/dev/null")
    kernels = [l.split()[-1] for l in o.splitlines() if l.startswith("install ok installed")]
    if len(kernels) <= 3:
        return Result(OK, f"{len(kernels)} kernel{'s' if len(kernels) != 1 else ''} installed")
    return Result(WARN, f"{len(kernels)} kernels installed; old ones waste space in /boot",
                  Fix("page", "clean", _("Open cleaner")))


def c_residual():
    o = out("dpkg -l | awk '/^rc/{print $2}'")
    n = len(o.split())
    if n == 0:
        return Result(OK, _("No leftover config from removed packages"))
    return Result(INFO, f"{n} removed package{'s' if n != 1 else ''} left config files behind",
                  Fix("page", "clean", _("Open cleaner")))


# ---------------------------------------------------------------- Storage

def c_disk_root():
    du = shutil.disk_usage("/")
    pct = du.used * 100 // du.total
    msg = f"Root filesystem {pct}% full ({human(du.free)} free of {human(du.total)})"
    if pct >= 95:
        return Result(FAIL, msg, Fix("page", "clean", _("Free space")))
    if pct >= 85:
        return Result(WARN, msg, Fix("page", "clean", _("Free space")))
    return Result(OK, msg)


def c_disk_boot():
    if not os.path.ismount("/boot"):
        return Result(OK, _("/boot is part of the root filesystem"))
    du = shutil.disk_usage("/boot")
    pct = du.used * 100 // du.total
    msg = f"/boot is {pct}% full ({human(du.free)} free)"
    if pct >= 80:
        return Result(FAIL if pct >= 90 else WARN, msg + "; kernel updates may fail",
                      Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y",
                          _("Remove old kernels")))
    return Result(OK, msg)


def c_trim():
    if not has_ssd():
        return Result(OK, _("No SSD detected; TRIM not needed"))
    if unit_enabled("fstrim.timer"):
        return Result(OK, _("Weekly SSD TRIM is enabled"))
    return Result(WARN, _("SSD detected but periodic TRIM is disabled"), tweak_fix("fstrim"))


def c_journal():
    o = out(["journalctl", "--disk-usage"])
    size = 0
    for tok in o.split():
        t = tok.rstrip(".")
        for suf, mul in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
            if t.endswith(suf):
                try:
                    size = float(t[:-1]) * mul
                except ValueError:
                    pass
    if size > 1 << 30:
        return Result(WARN, f"System logs use {human(size)}", tweak_fix("journal", _("Cap logs")))
    return Result(OK, f"System logs use {human(size)}" if size else "Journal size looks fine")


def c_timeshift():
    cfg = read("/etc/timeshift/timeshift.json")
    if not cfg:
        return Result(WARN, _("Timeshift is not set up; you have no system snapshots to roll back to"),
                      Fix("launch", "timeshift-launcher", _("Set up Timeshift")))
    import json
    try:
        j = json.loads(cfg)
    except ValueError:
        return Result(INFO, _("Timeshift config could not be parsed"))
    if not j.get("backup_device_uuid"):
        return Result(WARN, _("Timeshift has no backup device selected"),
                      Fix("launch", "timeshift-launcher", _("Set up Timeshift")))
    sched = [k[len("schedule_"):] for k, v in j.items() if k.startswith("schedule_") and v == "true"]
    if not sched:
        return Result(WARN, _("Timeshift is configured but no automatic schedule is set"),
                      Fix("launch", "timeshift-launcher", _("Open Timeshift")))
    return Result(OK, f"Timeshift snapshots scheduled ({', '.join(sched)})")


# ----------------------------------------------------------------- Memory

def c_swap():
    lines = read("/proc/swaps").splitlines()[1:]
    if not lines:
        return Result(WARN, _("No swap configured; the system may freeze when RAM runs out"),
                      tweak_fix("zram", _("Enable zram")))
    kinds = ["zram" if "zram" in l else "disk" for l in lines]
    sw = sysctl("vm.swappiness")
    if "zram" in kinds:
        return Result(OK, f"zram compressed swap active (swappiness {sw})")
    return Result(INFO, f"Disk swap only (swappiness {sw}); zram is faster",
                  tweak_fix("zram", _("Enable zram")))


def c_memory():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        k, _x, v = line.partition(":")
        info[k] = int(v.split()[0]) * 1024
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    if not total:
        return Result(INFO, _("Could not read memory info"))
    used_pct = 100 - avail * 100 // total
    msg = f"{used_pct}% of {human(total)} RAM in use"
    return Result(WARN if used_pct >= 90 else OK, msg)


# ---------------------------------------------------------------- Hardware

def c_microcode():
    pkg = {"intel": "intel-microcode", "amd": "amd64-microcode"}.get(cpu_vendor())
    if not pkg:
        return Result(OK, _("No microcode package needed for this CPU"))
    if pkg_installed(pkg):
        return Result(OK, f"{pkg} is installed")
    return Result(WARN, f"{pkg} is not installed (CPU security/bug fixes)", tweak_fix("microcode", _("Install")))


def c_gpu_driver():
    lspci = out(["lspci", "-nn"]) if has_cmd("lspci") else ""
    gpus = [l for l in lspci.splitlines() if "VGA" in l or "3D controller" in l]
    if any("NVIDIA" in g for g in gpus):
        mods = read("/proc/modules")
        if "\nnvidia " in "\n" + mods:
            return Result(OK, _("NVIDIA proprietary driver is loaded"))
        if "\nnouveau " in "\n" + mods:
            return Result(WARN, _("NVIDIA GPU is using the open 'nouveau' driver (slow, no CUDA)"),
                          Fix("launch", "driver-manager", _("Open Driver Manager")))
        return Result(WARN, _("NVIDIA GPU detected but no driver is loaded"),
                      Fix("launch", "driver-manager", _("Open Driver Manager")))
    name = gpus[0].split(": ", 1)[-1].split(" [")[0] if gpus else "unknown GPU"
    return Result(OK, f"Graphics: {name} (open driver, no action needed)")


def c_power():
    if not is_laptop():
        return Result(OK, _("Desktop PC; laptop power checks skipped"))
    if pkg_installed("tlp") and pkg_installed("power-profiles-daemon"):
        return Result(WARN, _("TLP and power-profiles-daemon are both installed and will fight"),
                      tweak_fix("tlpconflict", _("Resolve")))
    return Result(OK, _("Laptop power management looks consistent"))


# ------------------------------------------------------------- System health

def c_failed_units():
    o = out(["systemctl", "--failed", "--no-legend", "--plain"])
    units = [l.split()[0] for l in o.splitlines() if l.strip()]
    if not units:
        return Result(OK, _("No failed system services"))
    shown = ", ".join(units[:4]) + (" ..." if len(units) > 4 else "")
    return Result(WARN, f"{len(units)} failed service{'s' if len(units) != 1 else ''}: {shown}",
                  Fix("root", "\n".join(f"systemctl restart {u} || true" for u in units) +
                      "\nsystemctl reset-failed", _("Restart them")))


def c_user_failed_units():
    o = out(["systemctl", "--user", "--failed", "--no-legend", "--plain"])
    units = [l.split()[0] for l in o.splitlines() if l.strip()]
    if not units:
        return Result(OK, _("No failed user services"))
    return Result(WARN, f"Failed user service{'s' if len(units) != 1 else ''}: {', '.join(units[:4])}",
                  Fix("user", "\n".join(f"systemctl --user restart {u} || true" for u in units) +
                      "\nsystemctl --user reset-failed", _("Restart them")))


def c_boot_errors():
    from . import journal
    groups = journal.grouped()
    if not groups:
        return Result(OK, _("No errors logged since boot"))
    by = {lvl: [g for g in groups if g["level"] == lvl] for lvl in (FAIL, WARN, INFO, OK)}
    crashes = sum(g["count"] for g in groups if g["who"].startswith("crash:"))
    parts = []
    if crashes:
        parts.append(f"{crashes} program crash{'es' if crashes != 1 else ''}")
    if by[FAIL]:
        parts.append(f"{len(by[FAIL])} serious")
    look = [g for g in by[INFO] + by[WARN] if not g["who"].startswith("crash:")]
    if look:
        parts.append(f"{len(look)} to look at")
    if by[OK]:
        parts.append(f"{len(by[OK])} harmless")
    level = FAIL if by[FAIL] else WARN if by[WARN] else INFO if by[INFO] else OK
    return Result(level, f"{len(groups)} kind(s) of error since boot: " + ", ".join(parts) +
                  " — click for details and explanations")


SAP_DROPIN = "/etc/systemd/system/bluetooth.service.d/99-mintcare-nosap.conf"


def c_bt_sap():
    if not Path("/usr/libexec/bluetooth/bluetoothd").exists():
        return Result(OK, _("No Bluetooth daemon installed"))
    if Path(SAP_DROPIN).exists():
        return Result(OK, _("Bluetooth SIM Access plugin is disabled"))
    log = out("journalctl -b -q --no-pager -o cat -u bluetooth.service 2>/dev/null")
    if "Sap driver initialization failed" not in log:
        return Result(OK, _("Bluetooth started without plugin errors"))
    return Result(INFO, _("Bluetooth logs a SIM Access (SAP) plugin error every boot (harmless, noisy)"),
                  Fix("root", f"mkdir -p {Path(SAP_DROPIN).parent}\n"
                              f"printf '[Service]\\nExecStart=\\nExecStart=/usr/libexec/bluetooth/bluetoothd "
                              f"--noplugin=sap\\n' > {SAP_DROPIN}\n"
                              "systemctl daemon-reload\nsystemctl restart bluetooth", _("Disable SAP")))


def c_ecryptfs():
    if not pkg_installed("ecryptfs-utils"):
        return Result(OK, _("ecryptfs is not installed"))
    in_use = (Path.home() / ".ecryptfs").exists() or Path("/home/.ecryptfs").exists() \
        or "ecryptfs" in read("/proc/mounts")
    if in_use:
        return Result(OK, _("ecryptfs is installed and in use"))
    return Result(INFO, _("ecryptfs is installed but unused; its login module logs "
                        "'seteuid error' on every unlock"),
                  Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get purge -y ecryptfs-utils libecryptfs1",
                      _("Remove ecryptfs")))


def c_time():
    o = out(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "NTP"])
    if "NTPSynchronized=yes" in o:
        return Result(OK, _("Clock is synchronised with internet time"))
    return Result(WARN, _("Clock is not synchronised (can break HTTPS and updates)"),
                  Fix("root", "timedatectl set-ntp true\nsystemctl restart systemd-timesyncd", _("Sync clock")))


def c_firewall():
    if tweaks.BY_ID["ufw"].probe():
        return Result(OK, _("Firewall (UFW) is enabled"))
    return Result(WARN, _("Firewall is disabled"), tweak_fix("ufw", _("Enable firewall")))


def c_home_perms():
    home = Path.home()
    mode = home.stat().st_mode & 0o777
    if mode & 0o007:
        return Result(WARN, f"Home folder is readable by other users (mode {mode:o})",
                      Fix("user", f"chmod 750 {home}", _("Make private")))
    return Result(OK, f"Home folder is private (mode {mode:o})")


def c_inotify():
    w = int(sysctl("fs.inotify.max_user_watches") or 0)
    if w >= 524288:
        return Result(OK, f"File watcher limit is {w:,}")
    return Result(INFO, f"File watcher limit is {w:,}; IDEs may complain on big projects",
                  tweak_fix("inotify", _("Raise")))


# ------------------------------------------------------------ hardware health

def c_smart():
    from . import hardware
    ds = [d for d in hardware.drives() if "error" not in d]
    if not ds:
        return Result(INFO, _("Could not read disk health (UDisks2 unavailable)"))
    bad = [d for d in ds if d["health"] in (WARN, FAIL)]
    if bad:
        d = max(bad, key=lambda x: hardware.RANK[x["health"]])
        note = next((t for lvl, t in d["notes"] if lvl == d["health"]), "needs attention")
        return Result(d["health"], f"{d['model']} ({d['device']}): {note}",
                      Fix("page", "hardware", _("View drives")))
    infos = [(d, t) for d in ds for lvl, t in d["notes"] if lvl == INFO and "CRC" in t]
    if infos:
        d, t = infos[0]
        return Result(INFO, f"All {len(ds)} drives healthy; {d['model']} ({d['device']}) has {t.split(':')[0]}",
                      Fix("page", "hardware", _("View drives")))
    return Result(OK, f"All {len(ds)} drive{'s' if len(ds) != 1 else ''} report healthy SMART data")


def c_temps():
    from . import hardware
    ss = hardware.sensors()
    if not ss:
        return Result(INFO, _("No temperature sensors found"))
    hot = [s for s in ss if hardware.temp_level(s) != OK]
    thr = [t for t in hardware.throttling() if t[1] == WARN]
    if thr:
        return Result(WARN, f"{thr[0][0]}: {thr[0][2]}", Fix("page", "hardware", _("View sensors")))
    if hot:
        s = max(hot, key=lambda x: x["temp"])
        return Result(WARN, f"{s['label']} is running hot at {s['temp']:.0f}°C",
                      Fix("page", "hardware", _("View sensors")))
    top = max(ss, key=lambda x: x["temp"])
    return Result(OK, f"Temperatures normal (hottest: {top['label']} {top['temp']:.0f}°C), no throttling")


def c_boot():
    from . import boot
    t = boot.times()
    if not t.get("total"):
        return Result(INFO, _("Boot timing not available"))
    chain = boot.critical_chain()
    slow = [(u, s) for u, s in chain.items() if u in boot.SAFE_TO_DISABLE and s >= 2]
    msg = f"Last boot took {t['total']:.1f} s"
    if slow:
        u, s = max(slow, key=lambda x: x[1])
        return Result(INFO, f"{msg}; {u} delays it by {s:.1f} s and can be disabled",
                      Fix("page", "startup", _("Review boot")))
    if t.get("firmware", 0) >= 15:
        return Result(INFO, f"{msg}, {t['firmware']:.1f} s of it in BIOS/UEFI firmware "
                            "(try Fast Boot in your UEFI setup)", Fix("page", "startup", _("Review boot")))
    return Result(OK, f"{msg} (userspace {t.get('userspace', 0):.1f} s)")


def c_gamemode():
    if not pkg_installed("gamemode"):
        return Result(OK, _("GameMode not installed (optional, see Optimise > Gaming)"))
    if tweaks._in_group("gamemode"):
        return Result(OK, _("GameMode is installed and you can use all of its features"))
    return Result(WARN, _("GameMode is installed but you're not in the 'gamemode' group, so it "
                        "can't switch CPU governors"), tweak_fix("gamemode", _("Fix")))


def c_grub_recordfail():
    if not has_cmd("update-grub"):
        return Result(OK, _("Not using GRUB"))
    t, rf, _style = tweaks._grub_values()
    if rf > 10:
        return Result(INFO, f"After a crash or power cut GRUB waits {rf:.0f} s at its menu",
                      tweak_fix("grub", _("Shorten")))
    return Result(OK, f"GRUB menu timeout {t:.0f} s, {rf:.0f} s after a failed boot")


# ------------------------------------------------------------ updates, firmware, sources, ports

def c_mintcare_update():
    from . import __version__, maintenance, selfupdate
    if not maintenance.load_settings().get("check_updates", True):
        return Result(OK, _("Update checks for MintCare are turned off"))
    info = selfupdate.latest(timeout=5)
    if info is None:
        return Result(INFO, _("Couldn't reach GitHub to check for MintCare updates"))
    if selfupdate.is_newer(info["version"], __version__):
        return Result(WARN, f"MintCare {info['version']} is available (you have {__version__})",
                      Fix("page", "software", _("Update")))
    return Result(OK, f"MintCare {__version__} is the latest version")


def c_firmware():
    from . import firmware
    if not firmware.available():
        return Result(OK, _("fwupd is not installed; firmware updates are checked by your vendor's tools"))
    ups = firmware.updates()
    if ups:
        names = ", ".join(u["device"] for u in ups[:3])
        return Result(WARN, f"{len(ups)} firmware update(s) available: {names}",
                      Fix("page", "hardware", _("View firmware")))
    age = firmware.metadata_age()
    return Result(OK, "Firmware is up to date" + (f" (catalogue checked {age} ago)" if age else ""))


def c_sources():
    from . import sources
    srcs = [x for x in sources.analyse() if x.enabled]
    bad = [x for x in srcs if x.health in (sources.WARN, sources.FAIL)]
    if not bad:
        return Result(OK, f"All {len(srcs)} software sources look healthy")
    worst = max(bad, key=lambda x: sources.RANK[x.health])
    text = next(t for lvl, t in worst.problems if lvl == worst.health)
    return Result(worst.health, f"{len(bad)} software source(s) need attention — {worst.label}: {text}",
                  Fix("page", "software", _("Review sources")))


def c_ports():
    from . import ports
    svcs = [x for x in ports.services() if x["scope"] == "network"]
    warn = [x for x in svcs if x["level"] in (WARN, FAIL)]
    info = [x for x in svcs if x["level"] == INFO]
    if warn:
        names = ", ".join(f"{x['name']} ({x['port']})" for x in warn[:3])
        return Result(WARN, f"Reachable from the network with no firewall: {names}",
                      tweak_fix("ufw", _("Enable firewall")))
    if info:
        names = ", ".join(f"{x['name']} ({x['port']}/{x['proto']})" for x in info[:3])
        return Result(INFO, f"Open to the network behind the firewall: {names}",
                      Fix("page", "security", _("Review")))
    return Result(OK, f"{len(svcs)} network-facing port(s), all expected or blocked by the firewall"
                  if svcs else _("No services are reachable from the network"))


def c_backup():
    from . import backup
    b = backup.config()
    last = backup.last_backup()
    if not b["dest"]:
        return Result(INFO, _("Your personal files aren't backed up (Timeshift only covers system files)"),
                      Fix("page", "backup", _("Set up backup")))
    issues = [t for lvl, t in backup.check_destination(b["dest"]) if lvl == "fail"]
    if issues:
        return Result(WARN, issues[0], Fix("page", "backup", _("Open backup")))
    if not last:
        return Result(INFO, _("Backup is set up but hasn't run yet"), Fix("page", "backup", _("Back up now")))
    import time as _t
    days = (_t.time() - last["ts"]) / 86400
    limit = {"daily": 2, "weekly": 9, "monthly": 35}.get(b["frequency"], 9)
    if days > limit:
        return Result(WARN, f"Last backup was {days:.0f} days ago", Fix("page", "backup", _("Back up now")))
    return Result(OK, f"Personal files backed up {days:.0f} day(s) ago to {b['dest']}")


def c_battery():
    from . import hardware
    bats = hardware.batteries()
    if not bats:
        return Result(OK, _("No battery (desktop PC)"))
    b = min(bats, key=lambda x: x["health"] or 100)
    if b["health"] is None:
        return Result(OK, _("Battery health isn't reported by this laptop"))
    if b["health"] < 60:
        return Result(WARN, f"Battery holds only {b['health']}% of its original charge — consider replacing it",
                      Fix("page", "hardware", _("View battery")))
    return Result(OK, f"Battery holds {b['health']}% of its original charge"
                  + (f" after {b['cycles']} cycles" if b["cycles"] else ""))


CHECKS = [
    Check("internet", _("Internet connection"), _("Network"), c_internet),
    Check("dns", _("DNS resolution"), _("Network"), c_dns),
    Check("bbr", _("TCP BBR congestion control"), _("Network"), c_bbr),
    Check("hostname", _("Hostname resolution"), _("Network"), c_hostname),
    Check("dpkg", _("Interrupted package installs"), _("Packages"), c_dpkg_audit),
    Check("aptbroken", _("Broken dependencies"), _("Packages"), c_apt_broken),
    Check("updates", _("Pending updates"), _("Packages"), c_updates),
    Check("reboot", _("Restart needed"), _("Packages"), c_reboot),
    Check("kernels", _("Installed kernels"), _("Packages"), c_kernels),
    Check("residual", _("Leftover package config"), _("Packages"), c_residual),
    Check("sources", _("Software sources"), _("Packages"), c_sources),
    Check("selfupdate", _("MintCare updates"), _("Packages"), c_mintcare_update),
    Check("rootdisk", _("Disk space"), _("Storage"), c_disk_root),
    Check("bootdisk", _("/boot space"), _("Storage"), c_disk_boot),
    Check("trim", _("SSD TRIM"), _("Storage"), c_trim),
    Check("journal", _("Log size"), _("Storage"), c_journal),
    Check("timeshift", _("System snapshots"), _("Storage"), c_timeshift),
    Check("backup", _("Personal file backup"), _("Storage"), c_backup),
    Check("swap", _("Swap / zram"), _("Memory"), c_swap),
    Check("memory", _("Memory usage"), _("Memory"), c_memory),
    Check("microcode", _("CPU microcode"), _("Hardware"), c_microcode),
    Check("gpu", _("Graphics driver"), _("Hardware"), c_gpu_driver),
    Check("power", _("Laptop power management"), _("Hardware"), c_power),
    Check("smart", _("Disk health (SMART)"), _("Hardware"), c_smart),
    Check("temps", _("Temperatures & throttling"), _("Hardware"), c_temps),
    Check("firmware", _("Firmware updates"), _("Hardware"), c_firmware),
    Check("battery", _("Battery health"), _("Hardware"), c_battery),
    Check("firewall", _("Firewall"), _("Security"), c_firewall),
    Check("homeperms", _("Home folder privacy"), _("Security"), c_home_perms),
    Check("ports", _("Open network ports"), _("Security"), c_ports),
    Check("time", _("Clock sync"), _("System"), c_time),
    Check("failed", _("Failed services"), _("System"), c_failed_units),
    Check("userfailed", _("Failed user services"), _("System"), c_user_failed_units),
    Check("boottime", _("Boot time"), _("System"), c_boot),
    Check("grub", _("Boot menu timeout"), _("System"), c_grub_recordfail),
    Check("gamemode", _("GameMode permissions"), _("System"), c_gamemode),
    Check("booterrors", _("Errors since boot"), _("System"), c_boot_errors),
    Check("btsap", _("Bluetooth plugin errors"), _("System"), c_bt_sap),
    Check("ecryptfs", _("Unused ecryptfs login module"), _("System"), c_ecryptfs),
    Check("inotify", _("File watcher limit"), _("System"), c_inotify),
]


# ------------------------------------------------------------ details shown when a check is clicked

# check id -> shell command (read-only, runs as you). Shown in the panel and runnable in a terminal.
DETAIL_COMMANDS = {
    "internet": "ip -brief address; echo; ip route | head -5; echo; ping -c 3 -W 2 1.1.1.1",
    "dns": "resolvectl status | head -30; echo; getent ahosts linuxmint.com | head -3",
    "bbr": "sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc net.ipv4.tcp_available_congestion_control",
    "hostname": "hostname; echo; cat /etc/hosts",
    "dpkg": "dpkg --audit || true",
    "aptbroken": "apt-get -s -o Debug::NoLocking=1 check",
    "updates": "apt list --upgradable 2>/dev/null",
    "reboot": "uname -r; echo; ls -1 /boot/vmlinuz-*; echo; cat /run/reboot-required.pkgs 2>/dev/null || true",
    "kernels": "dpkg -l 'linux-image-[0-9]*' | awk '/^ii/{print $2, $3}'; echo; echo \"Running: $(uname -r)\"",
    "residual": "dpkg -l | awk '/^rc/{print $2, $3}'",
    "sources": "grep -rhv '^\\s*#' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null | grep -v '^\\s*$'",
    "selfupdate": "dpkg-query -W -f='${Version}\\n' mintcare 2>/dev/null || echo 'not installed as a package'",
    "rootdisk": "df -h -x tmpfs -x devtmpfs -x squashfs -x efivarfs",
    "bootdisk": "df -h /boot /boot/efi 2>/dev/null; echo; ls -lh /boot",
    "trim": "systemctl status fstrim.timer --no-pager | head -6; echo; lsblk -d -o NAME,ROTA,DISC-GRAN,MODEL",
    "journal": "journalctl --disk-usage; echo; cat /etc/systemd/journald.conf.d/*.conf 2>/dev/null",
    "timeshift": "cat /etc/timeshift/timeshift.json 2>/dev/null; echo; ls /timeshift/snapshots 2>/dev/null",
    "swap": "swapon --show; echo; free -h; echo; sysctl vm.swappiness",
    "memory": "free -h; echo; ps -eo pid,comm,%mem,rss --sort=-%mem | head -12",
    "microcode": "grep -m1 microcode /proc/cpuinfo; dpkg -l '*microcode*' | awk '/^ii/{print $2, $3}'",
    "gpu": "lspci -k | grep -A3 -E 'VGA|3D'; echo; nvidia-smi 2>/dev/null | head -15",
    "power": "powerprofilesctl 2>/dev/null; dpkg -l tlp power-profiles-daemon 2>/dev/null | awk '/^ii/{print $2, $3}'",
    "smart": "udisksctl status",
    "temps": "sensors 2>/dev/null || cat /sys/class/hwmon/hwmon*/temp*_input",
    "firmware": "fwupdmgr get-devices 2>/dev/null | head -60",
    "firewall": "cat /etc/ufw/ufw.conf; echo; systemctl status ufw --no-pager | head -5",
    "homeperms": "ls -ld ~",
    "ports": "ss -tulnp",
    "time": "timedatectl",
    "failed": "systemctl --failed --no-pager",
    "userfailed": "systemctl --user --failed --no-pager",
    "boottime": "systemd-analyze; echo; systemd-analyze blame --no-pager | head -15; echo; systemd-analyze critical-chain --no-pager",
    "booterrors": "journalctl -b -p 3 --no-pager",
    "grub": "grep -h -E '^GRUB_(TIMEOUT|RECORDFAIL|TIMEOUT_STYLE)' /etc/default/grub /etc/default/grub.d/*.cfg 2>/dev/null",
    "gamemode": "getent group gamemode; id -nG",
    "btsap": "systemctl cat bluetooth --no-pager | grep -E 'ExecStart|^#'",
    "ecryptfs": "dpkg -l ecryptfs-utils 2>/dev/null | awk '/^ii/{print $2, $3}'; grep -l ecryptfs /etc/pam.d/* 2>/dev/null",
    "inotify": "sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances",
    "backup": "ls -la ~/.config/systemd/user/mintcare-backup.* 2>/dev/null; cat ~/.local/state/mintcare/last-backup.json 2>/dev/null || echo 'No backup has run yet'",
    "battery": "upower -i $(upower -e | grep -m1 BAT) 2>/dev/null || echo 'No battery'",
}


def details(check_id):
    """(command shown to the user, output text, [(button label, kind, payload)]) or None."""
    cmd = DETAIL_COMMANDS.get(check_id)
    if not cmd:
        return None
    if check_id == "booterrors":
        from . import journal
        groups = journal.grouped()
        return cmd, journal.report(groups), journal.actions(groups)
    rc, o = run(["bash", "-c", cmd], timeout=30)
    return cmd, o or _("(no output)"), DETAIL_ACTIONS.get(check_id, [])


# extra buttons in a check's details panel
DETAIL_ACTIONS = {
    "updates": [("Open Update Manager", "launch", "mintupdate")],
    "kernels": [("Manage kernels", "page", "packages")],
    "residual": [("Open cleaner", "page", "clean")],
    "sources": [("Open Software & updates", "page", "software"), ("Software Sources", "launch", "mintsources")],
    "rootdisk": [("Find big files", "page", "space")],
    "backup": [("Open Backup", "page", "backup")],
    "timeshift": [("Open Timeshift", "launch", "timeshift-launcher")],
    "memory": [("Open process explorer", "page", "processes")],
    "gpu": [("Open Driver Manager", "launch", "driver-manager")],
    "smart": [("Open drive details", "page", "hardware")],
    "temps": [("Open sensors", "page", "hardware")],
    "firmware": [("Open firmware", "page", "hardware")],
    "ports": [("Open Security page", "page", "security")],
    "failed": [("Open Boot & Startup", "page", "startup")],
    "boottime": [("Open Boot & Startup", "page", "startup")],
    "internet": [("Network troubleshooter", "page", "troubleshoot")],
    "dns": [("Network troubleshooter", "page", "troubleshoot")],
}
