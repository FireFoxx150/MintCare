"""Disk SMART health (via UDisks2, no root needed) and temperature / throttling sensors."""

import json
import os
import re
import shlex
import time
from glob import glob
from pathlib import Path

from .i18n import _
from .util import has_cmd, human, read, run

UD = "org.freedesktop.UDisks2"
OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
RANK = {OK: 0, INFO: 1, WARN: 2, FAIL: 3}

NVME_WARNINGS = {
    "spare": _("spare capacity below threshold"),
    "temperature": _("temperature outside safe range"),
    "degraded": _("reliability degraded by media errors"),
    "readonly": _("drive switched to read-only mode"),
    "volatile_mem": _("volatile memory backup failed"),
    "pmr_readonly": _("persistent memory region read-only"),
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
            "model": (d.get("Model") or d.get("Vendor") or _("Unknown drive")).strip(),
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
                info["notes"].append((INFO, _("SMART is not available for this drive "
                                            "(common for USB enclosures and SD cards)")))
        except Exception as e:
            info["health"] = INFO
            info["notes"].append((INFO, _('Could not read SMART data: {0}').format(e)))
        out.append(info)
    order = {"NVMe": 0, "SSD": 1, "HDD": 2, "Disk": 3}
    out.sort(key=lambda x: (order[x["kind"]], x["device"]))
    track(out)
    return out


# ------------------------------------------------------------------ SMART trend

# Counters that should never grow on a healthy drive; CRC errors point at the cable instead
GROWTH = {"realloc": (WARN, _("{0} more reallocated sector(s) since {1} — the drive is wearing out; back it up")),
          "pending": (FAIL, _("{0} more pending sector(s) since {1} — back up this drive now")),
          "offline_unc": (FAIL, _("{0} more uncorrectable sector(s) since {1} — back up this drive now")),
          "media": (WARN, _("{0} more media error(s) since {1} — back it up and watch it")),
          "crc": (WARN, _("{0} more interface CRC error(s) since {1} — the SATA cable or port is still faulty; "
                          "reseat or replace the cable"))}
WINDOW = 30 * 86400


def growth(samples, current, now=None):
    """[(level, text)] for counters that rose since the oldest sample in the last 30 days."""
    now = now or time.time()
    recent = [s for s in samples if now - s["ts"] <= WINDOW]
    if not recent:
        return []
    base = recent[0]
    when = time.strftime("%d %b", time.localtime(base["ts"]))
    notes = []
    for key, (level, text) in GROWTH.items():
        delta = current.get(key, 0) - base["c"].get(key, 0)
        if key in current and key in base["c"] and delta > 0:
            notes.append((level, text.format(delta, when)))
    return notes


def track(drives_, path=None, now=None):
    """Record each drive's error counters (once a day or when they change) and add growth notes."""
    from . import maintenance
    path = path or maintenance.STATE / "smart-history.json"
    now = now or time.time()
    try:
        hist = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        hist = {}
    for d in drives_:
        cur = d.get("counters")
        if not cur:
            continue
        key = d.get("serial") or f"{d['model']}|{d['device']}"
        samples = hist.setdefault(key, [])
        for note in growth(samples, cur, now):
            d["notes"].append(note)
            d["health"] = _worst(d["health"], note[0])
        if not samples or samples[-1]["c"] != cur or now - samples[-1]["ts"] > 86400:
            samples.append({"ts": now, "c": cur})
        del samples[:-90]                       # about three months of daily samples
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(hist))
    except OSError:
        pass


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
        info["notes"].append((FAIL, _("Critical warning: ") + NVME_WARNINGS.get(w, w)))
        levels.append(FAIL)
    if used >= 90:
        levels.append(FAIL)
        # xgettext:no-python-format
        info["notes"].append((FAIL, _('{0}% of rated endurance used — plan a replacement').format(used)))
    elif used >= 80:
        levels.append(WARN)
        # xgettext:no-python-format
        info["notes"].append((WARN, _('{0}% of rated endurance used').format(used)))
    if spare <= thresh:
        levels.append(FAIL)
        info["notes"].append((FAIL, _('Spare blocks at {0}% (threshold {1}%)').format(spare, thresh)))
    elif spare < thresh + 10:
        levels.append(WARN)
        info["notes"].append((WARN, _('Spare blocks low: {0}%').format(spare)))
    if media:
        levels.append(WARN)
        info["notes"].append((WARN, _('{0} media/data-integrity error(s) recorded').format(media)))
    if info["temp"] and warn_t > 0 and info["temp"] >= warn_t:
        levels.append(WARN)
        info["notes"].append((WARN, _('Running hot: {0:.0f}°C (warning at {1:.0f}°C)').format(info['temp'], warn_t)))
    if a.get("critical_temp_time"):
        info["notes"].append((INFO, _('Spent {0} min above critical temperature').format(a['critical_temp_time'])))
    elif a.get("warning_temp_time"):
        info["notes"].append((INFO, _('Spent {0} min above warning temperature in its lifetime').format(a['warning_temp_time'])))
    if unsafe > 100:
        info["notes"].append((INFO, _('{0} unsafe shutdowns (power loss / hard resets)').format(unsafe)))
    st = ctl.get("SmartSelftestStatus", "")
    if st and st not in ("success", "inprogress", "aborted", ""):
        levels.append(WARN)
        info["notes"].append((WARN, _('Last self-test result: {0}').format(st)))
    info["health"] = _worst(*levels)
    info["counters"] = {"media": media}
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
        info["notes"].append((FAIL, _("Drive reports SMART FAILING — back up now and replace it")))
    for name in below:
        levels.append(FAIL)
        info["notes"].append((FAIL, _("Attribute '{0}' has crossed its failure threshold").format(name)))
    pend, unc, rea, crc = raw.get("pending", 0), raw.get("offline_unc", 0), raw.get("realloc", 0), raw.get("crc", 0)
    if pend or unc:
        levels.append(FAIL)
        info["notes"].append((FAIL, _('{0} pending / {1} uncorrectable sector(s): data on them may be unreadable — back up this drive').format(pend, unc)))
    if rea:
        levels.append(WARN if rea < 100 else FAIL)
        info["notes"].append((WARN if rea < 100 else FAIL, _('{0} reallocated sector(s); watch whether this number grows').format(rea)))
    if crc:
        info["notes"].append((INFO, _('{0} interface CRC error(s): usually a loose or poor SATA cable, not the disk itself').format(crc)))
    limit = 55 if info["kind"] == "HDD" else 70
    if info["temp"] and info["temp"] >= limit:
        levels.append(WARN)
        info["notes"].append((WARN, _('Running hot: {0:.0f}°C').format(info['temp'])))
    if info["kind"] == "HDD" and info["hours"] > 50000:
        info["notes"].append((INFO, _('Aging drive: {0} powered on').format(_years(info['hours']))))
    st = ata.get("SmartSelftestStatus", "")
    if st and st not in ("success", "inprogress", "aborted", "interrupted", ""):
        levels.append(WARN)
        info["notes"].append((WARN, _('Last self-test result: {0}').format(st)))
    info["health"] = _worst(*levels)
    info["counters"] = {"realloc": rea, "pending": pend, "offline_unc": unc, "crc": crc}
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
        found.append({"label": _("CPU · hottest core"), "temp": max(seen_core), "high": None,
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


NV_REASONS = {0x4: _("power cap"), 0x8: _("hardware slowdown"), 0x20: "thermal (driver)",
              0x40: "thermal (hardware)", 0x80: _("power brake")}


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
        k, _x, v = line.partition(":")
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
        res.append(("CPU", WARN, _('Thermally throttled {0:,} times since boot').format(sum(counts))))
    elif cpu_t is not None and cpu_t >= 90:
        res.append(("CPU", WARN, _('At {0:.0f}°C — at its thermal limit and likely throttling').format(cpu_t)))
    elif cpu_t is not None:
        res.append(("CPU", OK, _('No thermal throttling ({0:.0f}°C)').format(cpu_t)))
    g = nvidia()
    if g:
        active = [txt for bit, txt in NV_REASONS.items() if g["reasons"] & bit]
        thermal = g["reasons"] & (0x8 | 0x20 | 0x40)
        if thermal:
            res.append(("GPU", WARN, "Throttling: " + ", ".join(active)))
        elif active:
            res.append(("GPU", INFO, _("Limited by ") + ", ".join(active) + " (normal under load)"))
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


# ------------------------------------------------------------------ battery

def batteries():
    """[{'name','model','health','cycles','status','capacity','design_wh','full_wh'}] for laptops."""
    out_ = []
    for bat in sorted(glob("/sys/class/power_supply/BAT*")):
        def val(n):
            v = read(f"{bat}/{n}")
            return int(v) if v.lstrip("-").isdigit() else None
        full, design = val("energy_full") or val("charge_full"), val("energy_full_design") or val("charge_full_design")
        unit_wh = 1e6 if val("energy_full") else None
        health = round(100 * full / design) if full and design else None
        out_.append({"name": Path(bat).name, "model": read(f"{bat}/model_name"),
                     "maker": read(f"{bat}/manufacturer"), "health": health, "cycles": val("cycle_count"),
                     "status": read(f"{bat}/status"), "capacity": val("capacity"),
                     "design_wh": design / unit_wh if unit_wh and design else None,
                     "full_wh": full / unit_wh if unit_wh and full else None})
    return out_


PLACEHOLDERS = ("system product name", "to be filled", "default string", "not applicable", "system manufacturer", "o.e.m")
PCI_KINDS = {"03": "gpu", "0200": "network", "0280": "network"}


def _pci_name(vendor, device):
    """'NVIDIA Corporation', 'GA106 [GeForce RTX 3060]' -> 'NVIDIA GeForce RTX 3060'."""
    strip = lambda s: re.sub(r"\s*\[[0-9a-f]{4}\]$", "", s)
    vendor, device = strip(vendor), strip(device)
    m = re.search(r"\[([^\]]+)\]", vendor)
    short = m.group(1).split("/")[0] if m else vendor.split()[0]
    m = re.search(r"\[([^\]]+)\]", device)
    return f"{short} {m.group(1) if m else device}"


def parse_lspci(text):
    """[(kind, pci slot, name)] for graphics and network adapters, from `lspci -Dmm -nn`."""
    found = []
    for line in text.splitlines():
        try:
            f = shlex.split(line)
        except ValueError:
            continue
        if len(f) < 4:
            continue
        cls = re.search(r"\[([0-9a-f]{4})\]$", f[1])
        kind = cls and (PCI_KINDS.get(cls.group(1)) or PCI_KINDS.get(cls.group(1)[:2]))
        if kind:
            found.append((kind, f[0], _pci_name(f[2], f[3])))
    return found


def _nvidia_cards():
    """{pci slot: (name, VRAM MiB, driver)} from nvidia-smi."""
    if not has_cmd("nvidia-smi"):
        return {}
    rc, o = run(["nvidia-smi", "--query-gpu=pci.bus_id,name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"], timeout=5)
    cards = {}
    for line in o.splitlines() if rc == 0 else ():
        f = [x.strip() for x in line.split(",")]
        if len(f) == 4:
            cards[f[0].lower()[-12:]] = (f[1], float(f[2]) if f[2].isdigit() else None, f[3])
    return cards


def _core_count(cpuinfo):
    """Distinct (physical id, core id) pairs in /proc/cpuinfo; 0 where the kernel does not report cores."""
    cores = set()
    for block in cpuinfo.split("\n\n"):
        core = re.search(r"^core id\s*:\s*(\d+)", block, re.M)
        if core:
            phys = re.search(r"^physical id\s*:\s*(\d+)", block, re.M)
            cores.add((phys.group(1) if phys else "0", core.group(1)))
    return len(cores)


def _usb_name(lsusb_text, vendor, product):
    """Product name for a USB ID from `lsusb` output, or "" when it is not listed."""
    wanted = f"ID {vendor.zfill(4)}:{product.zfill(4)} "
    for line in lsusb_text.splitlines():
        if wanted in line:
            return line.split(wanted, 1)[1].strip()
    return ""


def _gb(n_bytes):
    gb = n_bytes / 2 ** 30
    return f"{gb:.0f} GB" if gb >= 3 else f"{gb:.1f} GB"


_INVENTORY = {"at": 0.0, "items": []}


def inventory():
    """[(kind, name, detail)] describing the main parts of this PC, readable without a password.

    Cached for a minute: the Hardware page asks on every visit and the lookups run several commands.
    """
    if time.time() - _INVENTORY["at"] > 60:
        _INVENTORY["items"] = _read_inventory()
        _INVENTORY["at"] = time.time()
    return list(_INVENTORY["items"])


def _read_inventory():
    items = []
    dmi = lambda k: read(f"/sys/class/dmi/id/{k}").strip()
    usable = lambda s: s if s and not any(p in s.lower() for p in PLACEHOLDERS) else ""
    maker, model = usable(dmi("sys_vendor")), usable(dmi("product_name"))
    board = " ".join(x for x in (usable(dmi("board_vendor")), usable(dmi("board_name"))) if x)
    if model or board:
        items.append(("computer", f"{maker} {model}".strip() if model else board,
                      _("Motherboard: {0}").format(board) if model and board else ""))

    cpuinfo = read("/proc/cpuinfo")
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    if m:
        cores = _core_count(cpuinfo)
        threads = os.cpu_count() or 0
        mhz = read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq").strip()
        bits = [_("{0} cores, {1} threads").format(cores, threads) if cores else _("{0} threads").format(threads)]
        if mhz.isdigit():
            bits.append(_("up to {0:.1f} GHz").format(int(mhz) / 1e6))
        items.append(("cpu", re.sub(r"\s+", " ", m.group(1)).strip(), " · ".join(bits)))

    # Online memory blocks are close to what is installed; MemTotal leaves out what the kernel reserves
    block = read("/sys/devices/system/memory/block_size_bytes").strip()
    online = sum(read(f).strip() == "online" for f in glob("/sys/devices/system/memory/memory*/state"))
    m = re.search(r"^MemTotal:\s*(\d+) kB", read("/proc/meminfo"), re.M)
    total = int(block, 16) * online if block and online else int(m.group(1)) * 1024 if m else 0
    if total:
        items.append(("memory", _("{0} of memory").format(_gb(total)), ""))

    nv = _nvidia_cards()
    rc, o = run(["lspci", "-Dmm", "-nn"], timeout=5) if has_cmd("lspci") else (1, "")
    for kind, slot, name in parse_lspci(o if rc == 0 else ""):
        detail = ""
        if kind == "gpu":
            card = nv.get(slot.lower()[-12:])
            vram = read(f"/sys/bus/pci/devices/{slot}/mem_info_vram_total").strip()
            if card:
                name = card[0]
                detail = " · ".join(x for x in (card[1] and _("{0} VRAM").format(_gb(card[1] * 2 ** 20)),
                                                 _("driver {0}").format(card[2])) if x)
            elif vram.isdigit() and int(vram) > 0:
                detail = _("{0} VRAM").format(_gb(int(vram)))
        items.append((kind, name, detail))
    items += _sound_items() + _wireless_items() + _disk_items() + memory_detail_items()
    return items


def _sound_items():
    items = []
    for line in read("/proc/asound/cards").splitlines():
        m = re.match(r"\s*\d+ \[[^\]]*\]: (.*)", line)
        if m:
            driver, _x, longname = m.group(1).partition(" - ")
            items.append(("sound", longname or driver, ""))
    return items


def _wireless_items():
    items = []
    for ifc in sorted(glob("/sys/class/net/*/wireless")):
        iface = ifc.split("/")[4]
        ids = re.search(r"PRODUCT=([0-9a-f]+)/([0-9a-f]+)", read(f"/sys/class/net/{iface}/device/uevent"))
        name = _("Wireless adapter")
        if ids:
            name = _usb_name(run(["lsusb"], timeout=5)[1], ids.group(1), ids.group(2)) or name
        items.append(("network", name, iface))
    return items


def _disk_items():
    items = []
    for dev in sorted(glob("/sys/block/*")):
        if not re.match(r"(sd|hd|nvme|mmcblk)", dev.rsplit("/", 1)[1]):
            continue
        model = read(f"{dev}/device/model").strip()
        size = read(f"{dev}/size").strip()
        if model and size.isdigit():
            items.append(("drive", model, human(int(size) * 512)))
    return items


def gpu_load():
    """{name, util, vram_used, vram_total, temp} for the first GPU that reports load, or None.

    NVIDIA through nvidia-smi, AMD through the kernel's sysfs counters. Values are None when unknown.
    """
    if has_cmd("nvidia-smi"):
        rc, o = run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                     "--format=csv,noheader,nounits"], timeout=5)
        if rc == 0 and o:
            f = [x.strip() for x in o.splitlines()[0].split(",")]
            num = lambda x: float(x) if re.fullmatch(r"[0-9.]+", x) else None
            used, total = num(f[2]), num(f[3])
            return {"name": f[0].replace("NVIDIA GeForce ", ""), "util": num(f[1]),
                    "vram_used": used * 2 ** 20 if used is not None else None,
                    "vram_total": total * 2 ** 20 if total is not None else None, "temp": num(f[4])}
    for busy in sorted(glob("/sys/class/drm/card[0-9]*/device/gpu_busy_percent")):
        dev = Path(busy).parent
        util = read(busy).strip()
        if util.isdigit():
            used, total = (read(f"{dev}/mem_info_vram_{k}").strip() for k in ("used", "total"))
            return {"name": "AMD", "util": float(util),
                    "vram_used": int(used) if used.isdigit() else None,
                    "vram_total": int(total) if total.isdigit() else None, "temp": None}
    return None


def power_draw():
    """Watts being drawn right now, from the kernel's power sensors or the battery, or None."""
    readings = []
    for hw in glob("/sys/class/hwmon/hwmon*"):
        for f in glob(f"{hw}/power*_average") + glob(f"{hw}/power*_input"):
            v = read(f).strip()
            if v.isdigit() and int(v) > 0:
                readings.append(int(v) / 1e6)
                break
    if not readings:
        for b in glob("/sys/class/power_supply/BAT*/power_now"):
            v = read(b).strip()
            if v.isdigit() and int(v) > 0:
                readings.append(int(v) / 1e6)
    return max(readings) if readings else None


MEMORY_FILE = Path("/var/lib/mintcare/memory.txt")


def memory_modules(text):
    """[(type, speed)] for each installed module in `dmidecode -t memory` output."""
    modules = []
    for block in text.split("\n\n"):
        if "Memory Device" not in block:
            continue
        fields = dict(l.strip().split(":", 1) for l in block.splitlines() if ":" in l)
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        size = fields.get("Size", "")
        if not size or size.startswith("No Module"):
            continue
        speed = fields.get("Configured Memory Speed") or fields.get("Speed") or ""
        if speed.lower() in ("", "unknown"):
            speed = ""
        mtype = fields.get("Type", "")
        if mtype.lower() in ("", "unknown", "<out of spec>"):
            mtype = ""
        modules.append((mtype, speed))
    return modules


def memory_detail_items():
    """Inventory rows for RAM type and speed, from the file an admin read saved earlier."""
    try:
        mods = memory_modules(MEMORY_FILE.read_text())
    except OSError:
        return []
    if not mods:
        return []
    types = {t for t, _s in mods if t}
    speeds = {s for _t, s in mods if s}
    name = " ".join(filter(None, [f"{len(mods)} x", "/".join(sorted(types)) or "RAM", "/".join(sorted(speeds))]))
    return [("memory", name, "")]
