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
            return Result(OK, "Internet connection is working")
        except OSError:
            continue
    return Result(FAIL, "No internet connection (could not reach 1.1.1.1, 9.9.9.9 or 8.8.8.8)",
                  Fix("root", "systemctl restart NetworkManager", "Restart networking"))


def c_dns():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        return Result(INFO, "Skipped: no internet connection")
    try:
        socket.getaddrinfo("linuxmint.com", 443)
        return Result(OK, "DNS name resolution is working")
    except OSError:
        return Result(FAIL, "Internet works but DNS lookups fail",
                      Fix("root", "resolvectl flush-caches 2>/dev/null || true\n"
                                  "systemctl restart systemd-resolved\nsystemctl restart NetworkManager",
                          "Repair DNS"))


def c_hostname():
    host = socket.gethostname()
    hosts = read("/etc/hosts")
    for line in hosts.splitlines():
        parts = line.split("#")[0].split()
        if host in parts[1:]:
            return Result(OK, f"Hostname '{host}' is in /etc/hosts")
    return Result(WARN, f"Hostname '{host}' missing from /etc/hosts (sudo can be slow)",
                  Fix("root", f"echo '127.0.1.1\t{host}' >> /etc/hosts", "Add entry"))


# ------------------------------------------------------------------ Packages

def c_dpkg_audit():
    o = out(["dpkg", "--audit"])
    if not o:
        return Result(OK, "No half-installed or unconfigured packages")
    return Result(FAIL, "Some packages were left half-installed (interrupted update?)",
                  Fix("root", "dpkg --configure -a\nDEBIAN_FRONTEND=noninteractive apt-get -f install -y",
                      "Repair packages"))


def c_apt_broken():
    rc, o = run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "check"])
    if rc == 0:
        return Result(OK, "No broken package dependencies")
    return Result(FAIL, "Broken package dependencies detected",
                  Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get -f install -y", "Repair"))


def c_updates():
    o = out(["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], timeout=60)
    n = sum(1 for line in o.splitlines() if line.startswith("Inst "))
    if n == 0:
        return Result(OK, "System is up to date (per the last package list refresh)")
    upd = "mintupdate-cli -r -y upgrade" if has_cmd("mintupdate-cli") else \
          "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade"
    return Result(WARN, f"{n} update{'s' if n != 1 else ''} available",
                  Fix("root", upd, "Install updates"))


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
                  Fix("page", "clean", "Open cleaner"))


def c_residual():
    o = out("dpkg -l | awk '/^rc/{print $2}'")
    n = len(o.split())
    if n == 0:
        return Result(OK, "No leftover config from removed packages")
    return Result(INFO, f"{n} removed package{'s' if n != 1 else ''} left config files behind",
                  Fix("page", "clean", "Open cleaner"))


# ---------------------------------------------------------------- Storage

def c_disk_root():
    du = shutil.disk_usage("/")
    pct = du.used * 100 // du.total
    msg = f"Root filesystem {pct}% full ({human(du.free)} free of {human(du.total)})"
    if pct >= 95:
        return Result(FAIL, msg, Fix("page", "clean", "Free space"))
    if pct >= 85:
        return Result(WARN, msg, Fix("page", "clean", "Free space"))
    return Result(OK, msg)


def c_disk_boot():
    if not os.path.ismount("/boot"):
        return Result(OK, "/boot is part of the root filesystem")
    du = shutil.disk_usage("/boot")
    pct = du.used * 100 // du.total
    msg = f"/boot is {pct}% full ({human(du.free)} free)"
    if pct >= 80:
        return Result(FAIL if pct >= 90 else WARN, msg + "; kernel updates may fail",
                      Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y",
                          "Remove old kernels"))
    return Result(OK, msg)


def c_trim():
    if not has_ssd():
        return Result(OK, "No SSD detected; TRIM not needed")
    if unit_enabled("fstrim.timer"):
        return Result(OK, "Weekly SSD TRIM is enabled")
    return Result(WARN, "SSD detected but periodic TRIM is disabled", tweak_fix("fstrim"))


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
        return Result(WARN, f"System logs use {human(size)}", tweak_fix("journal", "Cap logs"))
    return Result(OK, f"System logs use {human(size)}" if size else "Journal size looks fine")


def c_timeshift():
    cfg = read("/etc/timeshift/timeshift.json")
    if not cfg:
        return Result(WARN, "Timeshift is not set up; you have no system snapshots to roll back to",
                      Fix("launch", "timeshift-launcher", "Set up Timeshift"))
    import json
    try:
        j = json.loads(cfg)
    except ValueError:
        return Result(INFO, "Timeshift config could not be parsed")
    if not j.get("backup_device_uuid"):
        return Result(WARN, "Timeshift has no backup device selected",
                      Fix("launch", "timeshift-launcher", "Set up Timeshift"))
    sched = [k[len("schedule_"):] for k, v in j.items() if k.startswith("schedule_") and v == "true"]
    if not sched:
        return Result(WARN, "Timeshift is configured but no automatic schedule is set",
                      Fix("launch", "timeshift-launcher", "Open Timeshift"))
    return Result(OK, f"Timeshift snapshots scheduled ({', '.join(sched)})")


# ----------------------------------------------------------------- Memory

def c_swap():
    lines = read("/proc/swaps").splitlines()[1:]
    if not lines:
        return Result(WARN, "No swap configured; the system may freeze when RAM runs out",
                      tweak_fix("zram", "Enable zram"))
    kinds = ["zram" if "zram" in l else "disk" for l in lines]
    sw = sysctl("vm.swappiness")
    if "zram" in kinds:
        return Result(OK, f"zram compressed swap active (swappiness {sw})")
    return Result(INFO, f"Disk swap only (swappiness {sw}); zram is faster",
                  tweak_fix("zram", "Enable zram"))


def c_memory():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.split()[0]) * 1024
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    if not total:
        return Result(INFO, "Could not read memory info")
    used_pct = 100 - avail * 100 // total
    msg = f"{used_pct}% of {human(total)} RAM in use"
    return Result(WARN if used_pct >= 90 else OK, msg)


# ---------------------------------------------------------------- Hardware

def c_microcode():
    pkg = {"intel": "intel-microcode", "amd": "amd64-microcode"}.get(cpu_vendor())
    if not pkg:
        return Result(OK, "No microcode package needed for this CPU")
    if pkg_installed(pkg):
        return Result(OK, f"{pkg} is installed")
    return Result(WARN, f"{pkg} is not installed (CPU security/bug fixes)", tweak_fix("microcode", "Install"))


def c_gpu_driver():
    lspci = out(["lspci", "-nn"]) if has_cmd("lspci") else ""
    gpus = [l for l in lspci.splitlines() if "VGA" in l or "3D controller" in l]
    if any("NVIDIA" in g for g in gpus):
        mods = read("/proc/modules")
        if "\nnvidia " in "\n" + mods:
            return Result(OK, "NVIDIA proprietary driver is loaded")
        if "\nnouveau " in "\n" + mods:
            return Result(WARN, "NVIDIA GPU is using the open 'nouveau' driver (slow, no CUDA)",
                          Fix("launch", "driver-manager", "Open Driver Manager"))
        return Result(WARN, "NVIDIA GPU detected but no driver is loaded",
                      Fix("launch", "driver-manager", "Open Driver Manager"))
    name = gpus[0].split(": ", 1)[-1].split(" [")[0] if gpus else "unknown GPU"
    return Result(OK, f"Graphics: {name} (open driver, no action needed)")


def c_power():
    if not is_laptop():
        return Result(OK, "Desktop PC; laptop power checks skipped")
    if pkg_installed("tlp") and pkg_installed("power-profiles-daemon"):
        return Result(WARN, "TLP and power-profiles-daemon are both installed and will fight",
                      tweak_fix("tlpconflict", "Resolve"))
    return Result(OK, "Laptop power management looks consistent")


# ------------------------------------------------------------- System health

def c_failed_units():
    o = out(["systemctl", "--failed", "--no-legend", "--plain"])
    units = [l.split()[0] for l in o.splitlines() if l.strip()]
    if not units:
        return Result(OK, "No failed system services")
    shown = ", ".join(units[:4]) + (" ..." if len(units) > 4 else "")
    return Result(WARN, f"{len(units)} failed service{'s' if len(units) != 1 else ''}: {shown}",
                  Fix("root", "\n".join(f"systemctl restart {u} || true" for u in units) +
                      "\nsystemctl reset-failed", "Restart them"))


def c_user_failed_units():
    o = out(["systemctl", "--user", "--failed", "--no-legend", "--plain"])
    units = [l.split()[0] for l in o.splitlines() if l.strip()]
    if not units:
        return Result(OK, "No failed user services")
    return Result(WARN, f"Failed user service{'s' if len(units) != 1 else ''}: {', '.join(units[:4])}",
                  Fix("user", "\n".join(f"systemctl --user restart {u} || true" for u in units) +
                      "\nsystemctl --user reset-failed", "Restart them"))


def c_boot_errors():
    # Ignore failed sudo password attempts (user activity, not a fault) and
    # collapse messages that differ only in PIDs, paths or numbers.
    rc, o = run("journalctl -b -p 3 -q --no-pager -o cat 2>/dev/null")
    if rc != 0:
        return Result(INFO, "Could not read the system log")
    msgs = {re.sub(r"\d+", "#", re.sub(r"PWD=[^;]*;", "", l)).strip()
            for l in o.splitlines()
            if l.strip() and "a password is required" not in l and "incorrect password" not in l}
    n = len(msgs)
    if n == 0:
        return Result(OK, "No errors logged since boot")
    return Result(INFO if n < 25 else WARN,
                  f"{n} distinct error message{'s' if n != 1 else ''} logged since boot "
                  "(run 'journalctl -b -p 3' to review)")


SAP_DROPIN = "/etc/systemd/system/bluetooth.service.d/99-mintcare-nosap.conf"


def c_bt_sap():
    if not Path("/usr/libexec/bluetooth/bluetoothd").exists():
        return Result(OK, "No Bluetooth daemon installed")
    if Path(SAP_DROPIN).exists():
        return Result(OK, "Bluetooth SIM Access plugin is disabled")
    log = out("journalctl -b -q --no-pager -o cat -u bluetooth.service 2>/dev/null")
    if "Sap driver initialization failed" not in log:
        return Result(OK, "Bluetooth started without plugin errors")
    return Result(INFO, "Bluetooth logs a SIM Access (SAP) plugin error every boot (harmless, noisy)",
                  Fix("root", f"mkdir -p {Path(SAP_DROPIN).parent}\n"
                              f"printf '[Service]\\nExecStart=\\nExecStart=/usr/libexec/bluetooth/bluetoothd "
                              f"--noplugin=sap\\n' > {SAP_DROPIN}\n"
                              "systemctl daemon-reload\nsystemctl restart bluetooth", "Disable SAP"))


def c_ecryptfs():
    if not pkg_installed("ecryptfs-utils"):
        return Result(OK, "ecryptfs is not installed")
    in_use = (Path.home() / ".ecryptfs").exists() or Path("/home/.ecryptfs").exists() \
        or "ecryptfs" in read("/proc/mounts")
    if in_use:
        return Result(OK, "ecryptfs is installed and in use")
    return Result(INFO, "ecryptfs is installed but unused; its login module logs "
                        "'seteuid error' on every unlock",
                  Fix("root", "DEBIAN_FRONTEND=noninteractive apt-get purge -y ecryptfs-utils libecryptfs1",
                      "Remove ecryptfs"))


def c_time():
    o = out(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "NTP"])
    if "NTPSynchronized=yes" in o:
        return Result(OK, "Clock is synchronised with internet time")
    return Result(WARN, "Clock is not synchronised (can break HTTPS and updates)",
                  Fix("root", "timedatectl set-ntp true\nsystemctl restart systemd-timesyncd", "Sync clock"))


def c_firewall():
    if tweaks.BY_ID["ufw"].probe():
        return Result(OK, "Firewall (UFW) is enabled")
    return Result(WARN, "Firewall is disabled", tweak_fix("ufw", "Enable firewall"))


def c_home_perms():
    home = Path.home()
    mode = home.stat().st_mode & 0o777
    if mode & 0o007:
        return Result(WARN, f"Home folder is readable by other users (mode {mode:o})",
                      Fix("user", f"chmod 750 {home}", "Make private"))
    return Result(OK, f"Home folder is private (mode {mode:o})")


def c_inotify():
    w = int(sysctl("fs.inotify.max_user_watches") or 0)
    if w >= 524288:
        return Result(OK, f"File watcher limit is {w:,}")
    return Result(INFO, f"File watcher limit is {w:,}; IDEs may complain on big projects",
                  tweak_fix("inotify", "Raise"))


# ------------------------------------------------------------ hardware health

def c_smart():
    from . import hardware
    ds = [d for d in hardware.drives() if "error" not in d]
    if not ds:
        return Result(INFO, "Could not read disk health (UDisks2 unavailable)")
    bad = [d for d in ds if d["health"] in (WARN, FAIL)]
    if bad:
        d = max(bad, key=lambda x: hardware.RANK[x["health"]])
        note = next((t for lvl, t in d["notes"] if lvl == d["health"]), "needs attention")
        return Result(d["health"], f"{d['model']} ({d['device']}): {note}",
                      Fix("page", "hardware", "View drives"))
    infos = [(d, t) for d in ds for lvl, t in d["notes"] if lvl == INFO and "CRC" in t]
    if infos:
        d, t = infos[0]
        return Result(INFO, f"All {len(ds)} drives healthy; {d['model']} ({d['device']}) has {t.split(':')[0]}",
                      Fix("page", "hardware", "View drives"))
    return Result(OK, f"All {len(ds)} drive{'s' if len(ds) != 1 else ''} report healthy SMART data")


def c_temps():
    from . import hardware
    ss = hardware.sensors()
    if not ss:
        return Result(INFO, "No temperature sensors found")
    hot = [s for s in ss if hardware.temp_level(s) != OK]
    thr = [t for t in hardware.throttling() if t[1] == WARN]
    if thr:
        return Result(WARN, f"{thr[0][0]}: {thr[0][2]}", Fix("page", "hardware", "View sensors"))
    if hot:
        s = max(hot, key=lambda x: x["temp"])
        return Result(WARN, f"{s['label']} is running hot at {s['temp']:.0f}°C",
                      Fix("page", "hardware", "View sensors"))
    top = max(ss, key=lambda x: x["temp"])
    return Result(OK, f"Temperatures normal (hottest: {top['label']} {top['temp']:.0f}°C), no throttling")


def c_boot():
    from . import boot
    t = boot.times()
    if not t.get("total"):
        return Result(INFO, "Boot timing not available")
    chain = boot.critical_chain()
    slow = [(u, s) for u, s in chain.items() if u in boot.SAFE_TO_DISABLE and s >= 2]
    msg = f"Last boot took {t['total']:.1f} s"
    if slow:
        u, s = max(slow, key=lambda x: x[1])
        return Result(INFO, f"{msg}; {u} delays it by {s:.1f} s and can be disabled",
                      Fix("page", "startup", "Review boot"))
    if t.get("firmware", 0) >= 15:
        return Result(INFO, f"{msg}, {t['firmware']:.1f} s of it in BIOS/UEFI firmware "
                            "(try Fast Boot in your UEFI setup)", Fix("page", "startup", "Review boot"))
    return Result(OK, f"{msg} (userspace {t.get('userspace', 0):.1f} s)")


def c_gamemode():
    if not pkg_installed("gamemode"):
        return Result(OK, "GameMode not installed (optional, see Optimise > Gaming)")
    if tweaks._in_group("gamemode"):
        return Result(OK, "GameMode is installed and you can use all of its features")
    return Result(WARN, "GameMode is installed but you're not in the 'gamemode' group, so it "
                        "can't switch CPU governors", tweak_fix("gamemode", "Fix"))


def c_grub_recordfail():
    if not has_cmd("update-grub"):
        return Result(OK, "Not using GRUB")
    t, rf, _style = tweaks._grub_values()
    if rf > 10:
        return Result(INFO, f"After a crash or power cut GRUB waits {rf:.0f} s at its menu",
                      tweak_fix("grub", "Shorten"))
    return Result(OK, f"GRUB menu timeout {t:.0f} s, {rf:.0f} s after a failed boot")


CHECKS = [
    Check("internet", "Internet connection", "Network", c_internet),
    Check("dns", "DNS resolution", "Network", c_dns),
    Check("bbr", "TCP BBR congestion control", "Network", c_bbr),
    Check("hostname", "Hostname resolution", "Network", c_hostname),
    Check("dpkg", "Interrupted package installs", "Packages", c_dpkg_audit),
    Check("aptbroken", "Broken dependencies", "Packages", c_apt_broken),
    Check("updates", "Pending updates", "Packages", c_updates),
    Check("reboot", "Restart needed", "Packages", c_reboot),
    Check("kernels", "Installed kernels", "Packages", c_kernels),
    Check("residual", "Leftover package config", "Packages", c_residual),
    Check("rootdisk", "Disk space", "Storage", c_disk_root),
    Check("bootdisk", "/boot space", "Storage", c_disk_boot),
    Check("trim", "SSD TRIM", "Storage", c_trim),
    Check("journal", "Log size", "Storage", c_journal),
    Check("timeshift", "System snapshots", "Storage", c_timeshift),
    Check("swap", "Swap / zram", "Memory", c_swap),
    Check("memory", "Memory usage", "Memory", c_memory),
    Check("microcode", "CPU microcode", "Hardware", c_microcode),
    Check("gpu", "Graphics driver", "Hardware", c_gpu_driver),
    Check("power", "Laptop power management", "Hardware", c_power),
    Check("smart", "Disk health (SMART)", "Hardware", c_smart),
    Check("temps", "Temperatures & throttling", "Hardware", c_temps),
    Check("firewall", "Firewall", "Security", c_firewall),
    Check("homeperms", "Home folder privacy", "Security", c_home_perms),
    Check("time", "Clock sync", "System", c_time),
    Check("failed", "Failed services", "System", c_failed_units),
    Check("userfailed", "Failed user services", "System", c_user_failed_units),
    Check("boottime", "Boot time", "System", c_boot),
    Check("grub", "Boot menu timeout", "System", c_grub_recordfail),
    Check("gamemode", "GameMode permissions", "System", c_gamemode),
    Check("booterrors", "Errors since boot", "System", c_boot_errors),
    Check("btsap", "Bluetooth plugin errors", "System", c_bt_sap),
    Check("ecryptfs", "Unused ecryptfs login module", "System", c_ecryptfs),
    Check("inotify", "File watcher limit", "System", c_inotify),
]
