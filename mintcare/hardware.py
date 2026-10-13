"""Disk SMART health (via UDisks2, no root needed) and temperature / throttling sensors."""

from glob import glob
from pathlib import Path

from .util import has_cmd, human, read, run

UD = "org.freedesktop.UDisks2"
OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
RANK = {OK: 0, INFO: 1, WARN: 2, FAIL: 3}

NVME_WARNINGS = {
    "spare": "spare capacity below threshold",
    "temperature": "temperature outside safe range",
    "degraded": "reliability degraded by media errors",
    "readonly": "drive switched to read-only mode",
    "volatile_mem": "volatile memory backup failed",
    "pmr_readonly": "persistent memory region read-only",
}


def _worst(*levels):
    return max(levels, key=RANK.get) if levels else OK


def _years(hours):
    return f"{hours / 8766:.1f} yrs" if hours >= 8766 else f"{hours / 730.5:.1f} mo" if hours >= 730 else f"{hours:.0f} h"


def _bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SYSTEM)


def _call(bus, path, iface, method):
    from gi.repository import GLib
    res = bus.call_sync(UD, path, iface, method, GLib.Variant("(a{sv})", ({},)), None, 0, 5000, None)
    return res.unpack()[0]


def drives():
    """Return one dict per physical drive with a health verdict and readable stats."""
    try:
        bus = _bus()
        res = bus.call_sync(UD, "/org/freedesktop/UDisks2", "org.freedesktop.DBus.ObjectManager",
                            "GetManagedObjects", None, None, 0, 5000, None)
        objs = res.unpack()[0]
    except Exception as e:
        return [{"error": f"UDisks2 unavailable: {e}"}]

    devs = {}
    for path, ifaces in objs.items():
        blk = ifaces.get("org.freedesktop.UDisks2.Block")
        if blk and blk.get("Drive", "/") != "/" and "org.freedesktop.UDisks2.Partition" not in ifaces:
            dev = bytes(blk.get("PreferredDevice") or blk.get("Device") or b"").rstrip(b"\0").decode()
            devs.setdefault(blk["Drive"], dev)

    out = []
    for path, ifaces in sorted(objs.items()):
        d = ifaces.get("org.freedesktop.UDisks2.Drive")
        if not d or not d.get("Size"):
            continue
        ata = ifaces.get("org.freedesktop.UDisks2.Drive.Ata")
        nvme = ifaces.get("org.freedesktop.UDisks2.NVMe.Controller")
        rot = d.get("RotationRate", -1)
        kind = "NVMe" if nvme else "SSD" if rot == 0 else "HDD" if rot and rot > 0 else "Disk"
        info = {
            "model": (d.get("Model") or d.get("Vendor") or "Unknown drive").strip(),
            "serial": d.get("Serial", ""), "size": d["Size"], "kind": kind,
            "rpm": rot if rot and rot > 0 else None,
            "device": devs.get(path, ""), "removable": d.get("Removable", False),
            "bus": d.get("ConnectionBus", ""), "temp": None, "hours": None,
            "stats": [], "notes": [], "health": OK,
        }
        try:
            if nvme:
                _nvme(bus, path, nvme, info)
            elif ata and ata.get("SmartSupported") and ata.get("SmartEnabled"):
                _ata(bus, path, ata, info)
            else:
                info["health"] = INFO
                info["notes"].append((INFO, "SMART is not available for this drive "
                                            "(common for USB enclosures and SD cards)"))
        except Exception as e:
            info["health"] = INFO
            info["notes"].append((INFO, f"Could not read SMART data: {e}"))
        out.append(info)
    order = {"NVMe": 0, "SSD": 1, "HDD": 2, "Disk": 3}
    out.sort(key=lambda x: (order[x["kind"]], x["device"]))
    return out


def _nvme(bus, path, ctl, info):
    a = _call(bus, path, "org.freedesktop.UDisks2.NVMe.Controller", "SmartGetAttributes")
    temp = (ctl.get("SmartTemperature") or 0) - 273.15
    warn_t = (a.get("wctemp") or 0) - 273.15
    info["temp"] = temp if temp > -100 else None
    info["hours"] = ctl.get("SmartPowerOnHours")
    used, spare, thresh = a.get("percent_used", 0), a.get("avail_spare", 100), a.get("spare_thresh", 10)
    media, unsafe = a.get("media_errors", 0), a.get("unsafe_shutdowns", 0)
    levels = []
    crit = ctl.get("SmartCriticalWarning") or []
    for w in crit:
        info["notes"].append((FAIL, "Critical warning: " + NVME_WARNINGS.get(w, w)))
        levels.append(FAIL)
    if used >= 90:
        levels.append(FAIL)
        info["notes"].append((FAIL, f"{used}% of rated endurance used — plan a replacement"))
    elif used >= 80:
        levels.append(WARN)
        info["notes"].append((WARN, f"{used}% of rated endurance used"))
    if spare <= thresh:
        levels.append(FAIL)
        info["notes"].append((FAIL, f"Spare blocks at {spare}% (threshold {thresh}%)"))
    elif spare < thresh + 10:
        levels.append(WARN)
        info["notes"].append((WARN, f"Spare blocks low: {spare}%"))
    if media:
        levels.append(WARN)
        info["notes"].append((WARN, f"{media} media/data-integrity error(s) recorded"))
    if info["temp"] and warn_t > 0 and info["temp"] >= warn_t:
        levels.append(WARN)
        info["notes"].append((WARN, f"Running hot: {info['temp']:.0f}°C (warning at {warn_t:.0f}°C)"))
    if a.get("critical_temp_time"):
        info["notes"].append((INFO, f"Spent {a['critical_temp_time']} min above critical temperature"))
    elif a.get("warning_temp_time"):
        info["notes"].append((INFO, f"Spent {a['warning_temp_time']} min above warning temperature "
                                    "in its lifetime"))
    if unsafe > 100:
        info["notes"].append((INFO, f"{unsafe} unsafe shutdowns (power loss / hard resets)"))
    st = ctl.get("SmartSelftestStatus", "")
    if st and st not in ("success", "inprogress", "aborted", ""):
        levels.append(WARN)
        info["notes"].append((WARN, f"Last self-test result: {st}"))
    info["health"] = _worst(*levels)
    info["stats"] = [
        ("TEMP", f"{info['temp']:.0f}°C" if info["temp"] is not None else "–"),
        ("POWERED ON", _years(info["hours"] or 0)),
        ("WEAR", f"{used}%"),
        ("SPARE", f"{spare}%"),
        ("WRITTEN", human(a.get("total_data_written", 0))),
        ("ERRORS", str(media)),
    ]


ATA_KEYS = {5: "realloc", 9: "poh", 187: "uncorrectable_rep", 197: "pending", 198: "offline_unc", 199: "crc"}


def _ata(bus, path, ata, info):
    attrs = _call(bus, path, "org.freedesktop.UDisks2.Drive.Ata", "SmartGetAttributes")
    raw, below = {}, []
    for aid, name, flags, value, worst, thresh, pretty, unit, _exp in attrs:
        if aid in ATA_KEYS:
            raw[ATA_KEYS[aid]] = pretty
        if thresh and 0 < value <= thresh and flags & 1:  # pre-fail attribute at/below threshold
            below.append(name)
    temp = (ata.get("SmartTemperature") or 0) - 273.15
    info["temp"] = temp if temp > -100 else None
    info["hours"] = (ata.get("SmartPowerOnSeconds") or 0) / 3600
    levels = []
    if ata.get("SmartFailing"):
        levels.append(FAIL)
        info["notes"].append((FAIL, "Drive reports SMART FAILING — back up now and replace it"))
    for name in below:
        levels.append(FAIL)
        info["notes"].append((FAIL, f"Attribute '{name}' has crossed its failure threshold"))
    pend, unc, rea, crc = raw.get("pending", 0), raw.get("offline_unc", 0), raw.get("realloc", 0), raw.get("crc", 0)
    if pend or unc:
        levels.append(FAIL)
        info["notes"].append((FAIL, f"{pend} pending / {unc} uncorrectable sector(s): data on them may "
                                    "be unreadable — back up this drive"))
    if rea:
        levels.append(WARN if rea < 100 else FAIL)
        info["notes"].append((WARN if rea < 100 else FAIL, f"{rea} reallocated sector(s); watch whether "
                                                            "this number grows"))
    if crc:
        info["notes"].append((INFO, f"{crc} interface CRC error(s): usually a loose or poor SATA cable, "
                                    "not the disk itself"))
    limit = 55 if info["kind"] == "HDD" else 70
    if info["temp"] and info["temp"] >= limit:
        levels.append(WARN)
        info["notes"].append((WARN, f"Running hot: {info['temp']:.0f}°C"))
    if info["kind"] == "HDD" and info["hours"] > 50000:
        info["notes"].append((INFO, f"Aging drive: {_years(info['hours'])} powered on"))
    st = ata.get("SmartSelftestStatus", "")
    if st and st not in ("success", "inprogress", "aborted", "interrupted", ""):
        levels.append(WARN)
        info["notes"].append((WARN, f"Last self-test result: {st}"))
    info["health"] = _worst(*levels)
    info["stats"] = [
        ("TEMP", f"{info['temp']:.0f}°C" if info["temp"] is not None else "–"),
        ("POWERED ON", _years(info["hours"])),
        ("REALLOCATED", str(rea)),
        ("PENDING", str(pend)),
        ("UNCORRECTABLE", str(unc)),
        ("CRC ERRORS", str(crc)),
    ]


# ------------------------------------------------------------------ sensors

def _label(chip, lbl, idx):
    nice = {"k10temp": "CPU", "coretemp": "CPU", "zenpower": "CPU", "amdgpu": "GPU",
            "nvme": "NVMe", "acpitz": "Motherboard", "iwlwifi_1": "Wi-Fi", "drivetemp": "Disk"}
    base = nice.get(chip, chip)
    return f"{base} · {lbl}" if lbl else f"{base} · temp{idx}"


def sensors(with_gpu=True):
    """List of dicts: label, temp, high, crit, group. Cheap except for nvidia-smi."""
    found, seen_core = [], []
    for hw in sorted(glob("/sys/class/hwmon/hwmon*")):
        chip = read(f"{hw}/name")
        for inp in sorted(glob(f"{hw}/temp*_input"), key=lambda p: int(Path(p).name[4:-6] or 0)):
            stem = inp[:-6]
            try:
                t = int(read(inp, "0")) / 1000
            except ValueError:
                continue
            if t <= -40 or t > 150:
                continue
            lbl = read(stem + "_label")
            if chip == "coretemp" and lbl.startswith("Core"):
                seen_core.append(t)
                continue
            high = read(stem + "_max") or read(stem + "_high")
            crit = read(stem + "_crit")
            high = int(high) / 1000 if high.lstrip("-").isdigit() and int(high) > 0 else None
            crit = int(crit) / 1000 if crit.lstrip("-").isdigit() and int(crit) > 0 else None
            if chip in ("k10temp", "zenpower") and not crit:
                crit = 95.0  # Ryzen Tjmax
                high = high or 90.0
            if high and high > 150:
                high = None
            if crit and crit > 150:
                crit = None
            group = "cpu" if chip in ("k10temp", "coretemp", "zenpower") else \
                    "gpu" if chip in ("amdgpu", "nouveau", "radeon") else \
                    "disk" if chip in ("nvme", "drivetemp") else "other"
            found.append({"label": _label(chip, lbl, Path(stem).name[4:]), "temp": t,
                          "high": high, "crit": crit, "group": group})
    if seen_core:
        found.append({"label": "CPU · hottest core", "temp": max(seen_core), "high": None,
                      "crit": None, "group": "cpu"})
    if with_gpu:
        gpu = nvidia()
        if gpu and gpu.get("temp") is not None:
            found.append({"label": f"GPU · {gpu['name']}", "temp": gpu["temp"],
                          "high": gpu.get("slowdown"), "crit": gpu.get("shutdown"), "group": "gpu"})
    return found


def cpu_temp():
    for s in sensors(with_gpu=False):
        if s["group"] == "cpu":
            return s["temp"]
    return None


NV_REASONS = {0x4: "power cap", 0x8: "hardware slowdown", 0x20: "thermal (driver)",
              0x40: "thermal (hardware)", 0x80: "power brake"}


def nvidia():
    if not has_cmd("nvidia-smi"):
        return None
    rc, o = run(["nvidia-smi", "--query-gpu=name,temperature.gpu,clocks_throttle_reasons.active,"
                 "clocks.sm,clocks.max.sm,utilization.gpu,power.draw,fan.speed,"
                 "temperature.gpu.tlimit", "--format=csv,noheader,nounits"], timeout=5)
    if rc != 0 or not o:
        return None
    f = [x.strip() for x in o.splitlines()[0].split(",")]

    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    try:
        reasons = int(f[2], 16)
    except (ValueError, IndexError):
        reasons = 0
    g = {"name": f[0].replace("NVIDIA GeForce ", ""), "temp": num(f[1]), "reasons": reasons,
         "clock": num(f[3]), "max_clock": num(f[4]), "util": num(f[5]), "power": num(f[6]),
         "fan": num(f[7]), "slowdown": None, "shutdown": None}
    rc2, o2 = run("nvidia-smi -q -d TEMPERATURE", timeout=5)
    for line in o2.splitlines():
        k, _, v = line.partition(":")
        v = num(v.strip().split()[0]) if v.strip() else None
        if "Slowdown Temp" in k and "GPU" in k:
            g["slowdown"] = v
        elif "Shutdown Temp" in k and "GPU" in k:
            g["shutdown"] = v
    return g


def throttling():
    """[(component, level, text)] describing current throttling state."""
    res = []
    counts = []
    for p in glob("/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count"):
        try:
            counts.append(int(read(p, "0")))
        except ValueError:
            pass
    cpu_t = cpu_temp()
    if counts and sum(counts):
        res.append(("CPU", WARN, f"Thermally throttled {sum(counts):,} times since boot"))
    elif cpu_t is not None and cpu_t >= 90:
        res.append(("CPU", WARN, f"At {cpu_t:.0f}°C — at its thermal limit and likely throttling"))
    elif cpu_t is not None:
        res.append(("CPU", OK, f"No thermal throttling ({cpu_t:.0f}°C)"))
    g = nvidia()
    if g:
        active = [txt for bit, txt in NV_REASONS.items() if g["reasons"] & bit]
        thermal = g["reasons"] & (0x8 | 0x20 | 0x40)
        if thermal:
            res.append(("GPU", WARN, "Throttling: " + ", ".join(active)))
        elif active:
            res.append(("GPU", INFO, "Limited by " + ", ".join(active) + " (normal under load)"))
        else:
            res.append(("GPU", OK, f"No throttling ({g['temp']:.0f}°C, {g['clock']:.0f} MHz)"))
    return res


def cpu_freq():
    cur = []
    for p in glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"):
        try:
            cur.append(int(read(p, "0")))
        except ValueError:
            pass
    mx = read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    return (sum(cur) / len(cur) / 1000 if cur else None, int(mx) / 1000 if mx.isdigit() else None)


def temp_level(s):
    t, high, crit = s["temp"], s["high"], s["crit"]
    if crit and t >= crit - 5:
        return FAIL
    if high and t >= high:
        return WARN
    if not high and not crit:
        return WARN if t >= 85 else OK
    return OK
