"""Optimisation tweaks, in the spirit of Chris Titus Tech's linutil / winutil.

Every tweak writes only MintCare-owned drop-in files (named *mintcare*) or
toggles a service, and records a marker in /var/lib/mintcare/applied so it can
be reverted cleanly. Scripts here run as root through pkexec.
"""

import getpass
import grp
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Callable, Optional

from . import STATE_DIR
from .util import (cpu_vendor, has_cmd, has_ssd, is_laptop, pkg_installed, q, read,
                   sysctl, unit_enabled, unit_active, out)

MARKERS = Path(STATE_DIR) / "applied"


@dataclass
class Tweak:
    id: str
    title: str
    category: str
    description: str
    probe: Callable[[], bool]           # is the effect currently active?
    apply: str                          # root shell script
    revert: Optional[str] = None        # root shell script, None = not revertible
    recommended: bool = False
    available: Callable[[], bool] = lambda: True
    conflicts: list = field(default_factory=list)

    def state(self):
        """'mintcare' (applied by us), 'system' (already active), or 'off'."""
        try:
            active = self.probe()
        except Exception:
            active = False
        if not active:
            return "off"
        return "mintcare" if (MARKERS / self.id).exists() else "system"

    def apply_script(self):
        # set -e is ignored inside "( ... ) && x", so test the status separately
        return (f"mkdir -p {MARKERS}\n( set -e\n{self.apply}\n)\nrc=$?\n"
                f"[ $rc -eq 0 ] && touch {MARKERS}/{self.id}\nexit $rc")

    def revert_script(self):
        return (f"( set -e\n{self.revert}\n)\nrc=$?\n"
                f"[ $rc -eq 0 ] && rm -f {MARKERS}/{self.id}\nexit $rc")


# --------------------------------------------------------------------------
# sysctl tweak builder: saves the previous values so revert restores them.

def _cmp(live, want, op):
    if op == "=":
        return live == want
    try:
        a, b = int(live.split()[0]), int(want.split()[0])
    except (ValueError, IndexError):
        return False
    return a >= b if op == ">=" else a <= b


def sysctl_tweak(id, title, category, description, settings, pre="", post_revert="", **kw):
    """settings: list of (key, value, op) where op is '=', '>=' or '<='."""
    conf = f"/etc/sysctl.d/99-mintcare-{id}.conf"
    bak = f"{STATE_DIR}/sysctl-{id}.bak"
    keys = " ".join(k for k, _, _ in settings)
    body = "\n".join(f"{k} = {v}" for k, v, _ in settings)
    apply = f"""{pre}
mkdir -p {STATE_DIR}
if [ ! -f {bak} ]; then
  for k in {keys}; do echo "$k = $(sysctl -n $k | tr '\\t' ' ')"; done > {bak}
fi
cat > {conf} <<'EOF'
# Managed by MintCare - remove via MintCare > Optimise to revert
{body}
EOF
sysctl -q -p {conf}
echo "Wrote {conf}"
"""
    revert = f"""rm -f {conf}
if [ -f {bak} ]; then sysctl -q -p {bak} || true; rm -f {bak}; fi
sysctl -q --system >/dev/null 2>&1 || true
{post_revert}
echo "Removed {conf}"
"""
    probe = lambda: all(_cmp(sysctl(k), v, op) for k, v, op in settings)
    return Tweak(id, title, category, description, probe, apply, revert, **kw)


def _bbr_available():
    return ("bbr" in sysctl("net.ipv4.tcp_available_congestion_control")
            or Path(f"/lib/modules/{out(['uname', '-r'])}/kernel/net/ipv4/tcp_bbr.ko.zst").exists()
            or out("modinfo -n tcp_bbr 2>/dev/null") != "")


def _microcode_pkg():
    return {"intel": "intel-microcode", "amd": "amd64-microcode"}.get(cpu_vendor())


def _ufw_enabled():
    return "ENABLED=yes" in read("/etc/ufw/ufw.conf").replace(" ", "")


def _journal_limited():
    return Path("/etc/systemd/journald.conf.d/99-mintcare.conf").exists()


def _zram_active():
    return "/dev/zram" in read("/proc/swaps")


USER = getpass.getuser()
IOSCHED_RULE = "/etc/udev/rules.d/60-mintcare-iosched.rules"
GRUB_DROPIN = "/etc/default/grub.d/99-mintcare-timeout.cfg"
GAMEMODE_INI = Path("/etc/gamemode.ini")
CHARGE_RULE = "/etc/udev/rules.d/99-mintcare-charge-limit.rules"
AUTOPOWER_RULE = "/etc/udev/rules.d/99-mintcare-power-profile.rules"
ASPM_POLICY = "/sys/module/pcie_aspm/parameters/policy"
ASPM_TMPFILE = "/etc/tmpfiles.d/mintcare-aspm.conf"


def _disks():
    """{name: (rotational, current_scheduler)} for real disks."""
    res = {}
    for qd in Path("/sys/block").glob("*/queue"):
        name = qd.parent.name
        if not name.startswith(("nvme", "sd", "mmcblk")):
            continue
        sched = read(qd / "scheduler")
        cur = sched[sched.find("[") + 1:sched.find("]")] if "[" in sched else sched
        res[name] = (read(qd / "rotational") == "1", cur)
    return res


def _iosched_ok():
    for name, (rot, cur) in _disks().items():
        want = "none" if name.startswith("nvme") else "bfq" if rot else "mq-deadline"
        if cur != want:
            return False
    return True


def _grub_values():
    o = out(["bash", "-c", 'unset GRUB_TIMEOUT GRUB_RECORDFAIL_TIMEOUT GRUB_TIMEOUT_STYLE; '
             '. /etc/default/grub 2>/dev/null; for f in /etc/default/grub.d/*.cfg; do '
             '[ -r "$f" ] && . "$f" 2>/dev/null; done; '
             'echo "${GRUB_TIMEOUT:-5}|${GRUB_RECORDFAIL_TIMEOUT:-30}|${GRUB_TIMEOUT_STYLE:-menu}"'])
    try:
        t, rf, style = o.strip().splitlines()[-1].split("|")
        return float(t), float(rf), style
    except (ValueError, IndexError):
        return 5.0, 30.0, "menu"


def _dual_boot():
    cfg = read("/boot/grub/grub.cfg")
    if "30_os-prober" not in cfg:
        return False
    block = cfg.split("### BEGIN /etc/grub.d/30_os-prober ###", 1)[-1].split("### END", 1)[0]
    return "menuentry" in block


def _grub_target():
    return 3 if _dual_boot() else 2


def _grub_fast():
    t, rf, _style = _grub_values()
    return t <= _grub_target() and rf <= 5


def _grub_apply_script():
    t, _rf, _style = _grub_values()
    lines = ["# Managed by MintCare", "GRUB_RECORDFAIL_TIMEOUT=5"]
    if t > _grub_target():
        lines.append(f"GRUB_TIMEOUT={_grub_target()}")
    body = "\n".join(lines)
    return f"""mkdir -p {STATE_DIR} /etc/default/grub.d
[ -f {STATE_DIR}/grub.cfg.bak ] || cp -a /boot/grub/grub.cfg {STATE_DIR}/grub.cfg.bak
cat > {GRUB_DROPIN} <<'EOF'
{body}
EOF
update-grub"""


def _in_group(group):
    try:
        return USER in grp.getgrnam(group).gr_mem
    except KeyError:
        return False


def _flatpak_has(app):
    return has_cmd("flatpak") and app in out(["flatpak", "list", "--app", "--columns=application"])


def _charge_files():
    return glob("/sys/class/power_supply/BAT*/charge_control_end_threshold")


def _gamemode_ini():
    gpu = ""
    if has_cmd("nvidia-smi"):
        gpu = ("\n[gpu]\napply_gpu_optimisations=accept-responsibility\ngpu_device=0\n"
               "nv_powermizer_mode=1\n")
    return ("# Managed by MintCare - remove via MintCare > Optimise to revert\n"
            "[general]\nrenice=10\nsoftrealtime=auto\nioprio=0\ninhibit_screensaver=1\n"
            "desiredgov=performance\n" + gpu)


ESSENTIALS = ["build-essential", "curl", "git", "htop", "btop", "unzip", "fastfetch"]


TWEAKS = [
    # ---------------------------------------------------------------- Network
    sysctl_tweak(
        "bbr", "TCP BBR congestion control", "Network",
        "Google's BBR algorithm with the fq qdisc. Higher throughput and lower "
        "latency on most connections, especially lossy Wi-Fi.",
        [("net.core.default_qdisc", "fq", "="),
         ("net.ipv4.tcp_congestion_control", "bbr", "=")],
        pre="modprobe tcp_bbr 2>/dev/null || true\n"
            "echo tcp_bbr > /etc/modules-load.d/mintcare-bbr.conf",
        post_revert="rm -f /etc/modules-load.d/mintcare-bbr.conf",
        recommended=True, available=_bbr_available),
    sysctl_tweak(
        "network", "Network stack tuning", "Network",
        "TCP Fast Open, MTU probing and larger socket buffers (16 MB) for fast links.",
        [("net.ipv4.tcp_fastopen", "3", "="),
         ("net.ipv4.tcp_mtu_probing", "1", "="),
         ("net.core.rmem_max", "16777216", ">="),
         ("net.core.wmem_max", "16777216", ">="),
         ("net.ipv4.tcp_rmem", "4096 87380 16777216", "="),
         ("net.ipv4.tcp_wmem", "4096 65536 16777216", "="),
         ("net.core.netdev_max_backlog", "16384", ">=")],
        recommended=True),
    Tweak(
        "dns", "Encrypted DNS (Cloudflare + Quad9)", "Network",
        "Use 1.1.1.1 and 9.9.9.9 with opportunistic DNS-over-TLS through "
        "systemd-resolved. Faster lookups and more privacy than many ISP resolvers.",
        probe=lambda: Path("/etc/systemd/resolved.conf.d/99-mintcare-dns.conf").exists(),
        apply="""mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/99-mintcare-dns.conf <<'EOF'
# Managed by MintCare
[Resolve]
DNS=1.1.1.1#cloudflare-dns.com 9.9.9.9#dns.quad9.net 2606:4700:4700::1111#cloudflare-dns.com
FallbackDNS=1.0.0.1#cloudflare-dns.com 149.112.112.112#dns.quad9.net
DNSOverTLS=opportunistic
EOF
systemctl restart systemd-resolved""",
        revert="rm -f /etc/systemd/resolved.conf.d/99-mintcare-dns.conf\nsystemctl restart systemd-resolved",
        available=lambda: unit_active("systemd-resolved")),

    # ----------------------------------------------------------------- Memory
    Tweak(
        "zram", "Compressed RAM swap (zram)", "Memory",
        "Adds a zstd-compressed swap device in RAM (50% of memory). Keeps the "
        "desktop responsive under memory pressure and reduces SSD writes.",
        probe=_zram_active,
        apply="""DEBIAN_FRONTEND=noninteractive apt-get install -y zram-tools
cat > /etc/default/zramswap <<'EOF'
# Managed by MintCare
ALGO=zstd
PERCENT=50
PRIORITY=100
EOF
systemctl enable zramswap.service
systemctl restart zramswap.service""",
        revert="systemctl disable --now zramswap.service || true\n"
               "DEBIAN_FRONTEND=noninteractive apt-get purge -y zram-tools",
        recommended=True, conflicts=["swappiness"]),
    sysctl_tweak(
        "swappiness", "Low swappiness (disk swap only)", "Memory",
        "Prefer RAM over a swap file/partition (swappiness 10, cache pressure 50). "
        "Use this instead of zram if you keep plain disk swap.",
        [("vm.swappiness", "10", "<="), ("vm.vfs_cache_pressure", "50", "<=")],
        conflicts=["zram"]),
    sysctl_tweak(
        "dirty", "Smoother disk writeback", "Memory",
        "Flush dirty pages earlier (64 MB background / 256 MB max) so large copies "
        "to USB sticks don't stall the desktop.",
        [("vm.dirty_background_bytes", "67108864", "="),
         ("vm.dirty_bytes", "268435456", "=")]),

    # ---------------------------------------------------------------- Storage
    Tweak(
        "fstrim", "Weekly SSD TRIM", "Storage",
        "Enable fstrim.timer so SSDs are trimmed weekly, keeping write speeds up.",
        probe=lambda: unit_enabled("fstrim.timer"),
        apply="systemctl enable --now fstrim.timer",
        revert="systemctl disable --now fstrim.timer",
        recommended=True, available=has_ssd),
    Tweak(
        "journal", "Cap system logs at 200 MB", "Storage",
        "Limit the systemd journal to 200 MB and 1 month so logs never fill the disk.",
        probe=_journal_limited,
        apply="""mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-mintcare.conf <<'EOF'
# Managed by MintCare
[Journal]
SystemMaxUse=200M
MaxRetentionSec=1month
EOF
systemctl restart systemd-journald
journalctl --vacuum-size=200M""",
        revert="rm -f /etc/systemd/journald.conf.d/99-mintcare.conf\nsystemctl restart systemd-journald",
        recommended=True),
    Tweak(
        "autoremove", "Automatic old-kernel/orphan removal", "Storage",
        "Turn on Mint Update's weekly automation that removes obsolete kernels "
        "and orphaned packages.",
        probe=lambda: Path("/var/lib/linuxmint/mintupdate-automatic-removals-enabled").exists(),
        apply="/usr/bin/mintupdate-automation autoremove enable",
        revert="/usr/bin/mintupdate-automation autoremove disable",
        recommended=True, available=lambda: has_cmd("mintupdate-automation")),

    # ------------------------------------------------------------------- Boot
    Tweak(
        "shutdown", "Faster shutdown", "Boot & Services",
        "Wait at most 10 s (instead of 90 s) for hung services when shutting down.",
        probe=lambda: Path("/etc/systemd/system.conf.d/99-mintcare-timeout.conf").exists(),
        apply="""mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/99-mintcare-timeout.conf <<'EOF'
# Managed by MintCare
[Manager]
DefaultTimeoutStopSec=10s
EOF
systemctl daemon-reload""",
        revert="rm -f /etc/systemd/system.conf.d/99-mintcare-timeout.conf\nsystemctl daemon-reload",
        recommended=True),
    Tweak(
        "waitonline", "Skip 'wait for network' at boot", "Boot & Services",
        "Disable NetworkManager-wait-online, which can add several seconds to boot. "
        "Leave this off if you mount network shares (NFS/SMB) in /etc/fstab.",
        probe=lambda: not unit_enabled("NetworkManager-wait-online.service"),
        apply="systemctl disable NetworkManager-wait-online.service",
        revert="systemctl enable NetworkManager-wait-online.service"),

    # --------------------------------------------------------------- Security
    Tweak(
        "ufw", "Firewall (UFW)", "Security",
        "Enable the firewall: block all incoming connections, allow outgoing.",
        probe=_ufw_enabled,
        apply="""DEBIAN_FRONTEND=noninteractive apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw --force enable""",
        revert="ufw --force disable",
        recommended=True),
    Tweak(
        "autoupdate", "Automatic updates", "Security",
        "Let Mint Update install updates automatically in the background "
        "(Timeshift snapshot recommended).",
        probe=lambda: Path("/var/lib/linuxmint/mintupdate-automatic-upgrades-enabled").exists(),
        apply="/usr/bin/mintupdate-automation upgrade enable",
        revert="/usr/bin/mintupdate-automation upgrade disable",
        available=lambda: has_cmd("mintupdate-automation")),
    Tweak(
        "microcode", "CPU microcode updates", "Security",
        "Install the vendor microcode package: fixes CPU bugs and security issues.",
        probe=lambda: bool(_microcode_pkg()) and pkg_installed(_microcode_pkg()),
        apply=f"DEBIAN_FRONTEND=noninteractive apt-get install -y {_microcode_pkg() or 'true'}",
        recommended=True, available=lambda: _microcode_pkg() is not None),

    # ------------------------------------------------------------- Storage+
    Tweak(
        "iosched", "Best I/O scheduler per drive", "Storage",
        "NVMe: none · SATA SSD: mq-deadline · hard drives: BFQ. BFQ keeps the desktop "
        "responsive while a hard drive is busy copying or indexing.",
        probe=_iosched_ok,
        apply=f"""modprobe bfq
echo bfq > /etc/modules-load.d/mintcare-bfq.conf
cat > {IOSCHED_RULE} <<'EOF'
# Managed by MintCare
ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", ATTR{{queue/scheduler}}="none"
ACTION=="add|change", KERNEL=="sd[a-z]*|mmcblk[0-9]*", ATTR{{queue/rotational}}=="0", ATTR{{queue/scheduler}}="mq-deadline"
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{{queue/rotational}}=="1", ATTR{{queue/scheduler}}="bfq"
EOF
udevadm control --reload
udevadm trigger --subsystem-match=block --action=change
udevadm settle
for q in /sys/block/*/queue; do echo "$(basename $(dirname $q)): $(cat $q/scheduler)"; done | grep -v loop""",
        revert=f"""rm -f {IOSCHED_RULE} /etc/modules-load.d/mintcare-bfq.conf
udevadm control --reload
for q in /sys/block/sd*/queue; do [ "$(cat $q/rotational)" = 1 ] && echo mq-deadline > $q/scheduler || true; done""",
        available=lambda: bool(_disks())),

    # ----------------------------------------------------------- Boot menu
    Tweak(
        "grub", "Faster boot menu (GRUB)", "Boot & Services",
        "After a crash or power cut Ubuntu-based systems show the GRUB menu for 30 s. "
        "This cuts that to 5 s and keeps the normal menu short (2 s, or 3 s when "
        "dual-booting). Uses a drop-in file; /etc/default/grub is not edited and "
        "grub.cfg is backed up first.",
        probe=_grub_fast,
        apply=_grub_apply_script(),
        revert=f"rm -f {GRUB_DROPIN}\nupdate-grub",
        recommended=True, available=lambda: has_cmd("update-grub")),

    # ----------------------------------------------------------------- Gaming
    sysctl_tweak(
        "mapcount", "Raise vm.max_map_count", "Gaming",
        "Raise the memory-map limit to SteamOS's value. Prevents crashes in some "
        "Proton/Steam games (e.g. CS2, Hogwarts Legacy).",
        [("vm.max_map_count", "2147483642", ">=")]),
    Tweak(
        "gamemode", "Feral GameMode", "Gaming",
        "Temporarily boosts CPU/GPU performance while a game runs. Also adds you to the "
        "'gamemode' group, which GameMode needs to switch CPU governors "
        "(log out and back in afterwards). Launch games with: gamemoderun %command%",
        probe=lambda: pkg_installed("gamemode") and _in_group("gamemode"),
        apply=f"DEBIAN_FRONTEND=noninteractive apt-get install -y gamemode\n"
              f"getent group gamemode >/dev/null || groupadd -r gamemode\n"
              f"usermod -aG gamemode {q(USER)}",
        revert=f"gpasswd -d {q(USER)} gamemode || true"),
    Tweak(
        "gamemodecfg", "GameMode performance profile", "Gaming",
        "While gaming: performance CPU governor, higher process and I/O priority, "
        "screensaver blocked" + (", NVIDIA 'prefer maximum performance'" if has_cmd("nvidia-smi") else "")
        + ". Writes /etc/gamemode.ini (any existing file is backed up).",
        probe=lambda: GAMEMODE_INI.exists(),
        apply=f"""mkdir -p {STATE_DIR}
if [ -f {GAMEMODE_INI} ] && ! grep -q 'Managed by MintCare' {GAMEMODE_INI}; then
  cp -a {GAMEMODE_INI} {STATE_DIR}/gamemode.ini.bak
fi
cat > {GAMEMODE_INI} <<'EOF'
{_gamemode_ini()}EOF
echo "Wrote {GAMEMODE_INI}" """,
        revert=f"""if [ -f {STATE_DIR}/gamemode.ini.bak ]; then mv -f {STATE_DIR}/gamemode.ini.bak {GAMEMODE_INI}
else rm -f {GAMEMODE_INI}; fi""",
        available=lambda: pkg_installed("gamemode")),
    Tweak(
        "mangohud", "MangoHud + GOverlay", "Gaming",
        "In-game overlay for FPS, frame times, temperatures and load, plus GOverlay to "
        "configure it. Launch games with: mangohud %command%",
        probe=lambda: pkg_installed("mangohud"),
        apply="DEBIAN_FRONTEND=noninteractive apt-get install -y mangohud goverlay",
        revert="DEBIAN_FRONTEND=noninteractive apt-get purge -y mangohud goverlay"),
    Tweak(
        "steam", "Steam (with Proton)", "Gaming",
        "Installs Steam from Flathub plus controller udev rules (steam-devices). Proton "
        "lets Steam run most Windows games; Steam asks you to accept its licence on first "
        "launch.",
        probe=lambda: has_cmd("steam") or _flatpak_has("com.valvesoftware.Steam"),
        apply="DEBIAN_FRONTEND=noninteractive apt-get install -y steam-devices\n"
              "flatpak install --system -y --noninteractive flathub com.valvesoftware.Steam",
        revert="flatpak uninstall --system -y --noninteractive com.valvesoftware.Steam",
        available=lambda: has_cmd("flatpak")),
    Tweak(
        "protonup", "ProtonUp-Qt (Proton-GE manager)", "Gaming",
        "Installs and updates community Proton builds such as Proton-GE, which fix "
        "videos and compatibility in many games.",
        probe=lambda: _flatpak_has("net.davidotek.pupgui2"),
        apply="flatpak install --system -y --noninteractive flathub net.davidotek.pupgui2",
        revert="flatpak uninstall --system -y --noninteractive net.davidotek.pupgui2",
        available=lambda: has_cmd("flatpak")),

    # -------------------------------------------------------------- Developer
    sysctl_tweak(
        "inotify", "More file watchers", "Developer",
        "Raise inotify limits so VS Code, IDEs and dev servers can watch large projects.",
        [("fs.inotify.max_user_watches", "524288", ">="),
         ("fs.inotify.max_user_instances", "1024", ">=")]),
    Tweak(
        "nala", "Install Nala (faster apt front-end)", "Developer",
        "Nala downloads packages in parallel and shows clearer output. Use 'nala' "
        "anywhere you would type 'apt'.",
        probe=lambda: has_cmd("nala"),
        apply="DEBIAN_FRONTEND=noninteractive apt-get install -y nala",
        revert="DEBIAN_FRONTEND=noninteractive apt-get purge -y nala"),
    Tweak(
        "essentials", "Install essential tools", "Developer",
        "build-essential, curl, git, htop, btop, unzip and fastfetch (where available).",
        probe=lambda: all(pkg_installed(p) for p in ESSENTIALS if p != "fastfetch"),
        apply="pkgs='" + " ".join(ESSENTIALS) + "'\n"
              "avail=''; for p in $pkgs; do apt-cache show $p >/dev/null 2>&1 && avail=\"$avail $p\"; done\n"
              "DEBIAN_FRONTEND=noninteractive apt-get install -y $avail"),

    # ----------------------------------------------------------------- Laptop
    Tweak(
        "tlpconflict", "Remove TLP / power-profiles conflict", "Laptop",
        "Having both TLP and power-profiles-daemon running causes erratic power "
        "management. This removes TLP and keeps Mint's power-profiles-daemon.",
        probe=lambda: not (pkg_installed("tlp") and pkg_installed("power-profiles-daemon")),
        apply="DEBIAN_FRONTEND=noninteractive apt-get purge -y tlp tlp-rdw\n"
              "systemctl enable --now power-profiles-daemon",
        available=is_laptop),
    Tweak(
        "chargelimit", "Battery charge limit (80%)", "Laptop",
        "Stop charging at 80% to roughly double battery lifespan when the laptop lives on "
        "the charger. Only shown when the battery firmware supports it.",
        probe=lambda: bool(_charge_files()) and all(read(f, "100").isdigit() and int(read(f, "100")) <= 80
                                                    for f in _charge_files()),
        apply=f"""cat > {CHARGE_RULE} <<'EOF'
# Managed by MintCare
SUBSYSTEM=="power_supply", KERNEL=="BAT*", ATTR{{charge_control_end_threshold}}="80"
EOF
for f in /sys/class/power_supply/BAT*/charge_control_end_threshold; do echo 80 > "$f"; done
udevadm control --reload""",
        revert=f"""rm -f {CHARGE_RULE}
for f in /sys/class/power_supply/BAT*/charge_control_end_threshold; do echo 100 > "$f" || true; done
udevadm control --reload""",
        available=lambda: is_laptop() and bool(_charge_files())),
    Tweak(
        "autopower", "Power saver on battery", "Laptop",
        "Switch to the power-saver profile when you unplug and back to balanced when you "
        "plug in (power-profiles-daemon).",
        probe=lambda: Path(AUTOPOWER_RULE).exists(),
        apply=f"""cat > {AUTOPOWER_RULE} <<'EOF'
# Managed by MintCare
SUBSYSTEM=="power_supply", ATTR{{type}}=="Mains", ATTR{{online}}=="0", RUN+="/usr/bin/powerprofilesctl set power-saver"
SUBSYSTEM=="power_supply", ATTR{{type}}=="Mains", ATTR{{online}}=="1", RUN+="/usr/bin/powerprofilesctl set balanced"
EOF
udevadm control --reload""",
        revert=f"rm -f {AUTOPOWER_RULE}\nudevadm control --reload",
        available=lambda: is_laptop() and has_cmd("powerprofilesctl")),
    Tweak(
        "aspm", "PCIe power saving (ASPM)", "Laptop",
        "Let PCIe devices (Wi-Fi, NVMe, GPU links) drop into low-power states when idle. "
        "Typically saves 0.5–2 W. If a device misbehaves, switch this off.",
        probe=lambda: any(f"[{p}]" in read(ASPM_POLICY) for p in ("powersave", "powersupersave")),
        apply=f"""cat > {ASPM_TMPFILE} <<'EOF'
# Managed by MintCare
w {ASPM_POLICY} - - - - powersupersave
EOF
echo powersupersave > {ASPM_POLICY} 2>/dev/null || echo powersave > {ASPM_POLICY}""",
        revert=f"rm -f {ASPM_TMPFILE}\necho default > {ASPM_POLICY}",
        available=lambda: is_laptop() and Path(ASPM_POLICY).exists()),
]

BY_ID = {t.id: t for t in TWEAKS}
CATEGORIES = list(dict.fromkeys(t.category for t in TWEAKS))


# ---------------------------------------------------------------- profiles

@dataclass
class Profile:
    id: str
    title: str
    icon: str
    description: str
    tweaks: list


_BASE = ["bbr", "network", "zram", "fstrim", "journal", "autoremove", "shutdown", "ufw",
         "microcode", "grub", "iosched"]

PROFILES = [
    Profile("desktop", "Everyday desktop", "computer-symbolic",
            "Faster networking, zram, SSD care, firewall, tidy logs and a quick boot menu.", _BASE),
    Profile("gaming", "Gaming", "input-gaming-symbolic",
            "Everyday tweaks plus GameMode, MangoHud, Steam with Proton and the "
            "SteamOS memory-map limit.",
            _BASE + ["mapcount", "gamemode", "gamemodecfg", "mangohud", "steam", "protonup"]),
    Profile("laptop", "Laptop battery", "battery-good-symbolic",
            "Everyday tweaks plus charge limiting, power saver on battery and PCIe power saving.",
            _BASE + ["tlpconflict", "chargelimit", "autopower", "aspm", "dirty"]),
    Profile("developer", "Developer", "utilities-terminal-symbolic",
            "Everyday tweaks plus large file-watcher limits, Nala and build tools.",
            _BASE + ["inotify", "nala", "essentials"]),
]
PROFILE_BY_ID = {p.id: p for p in PROFILES}
