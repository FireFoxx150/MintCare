"""Small helpers shared by checks, tweaks and the cleaner (all run unprivileged)."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path


def run(cmd, timeout=20, env_extra=None):
    """Run a command, return (returncode, stdout). Never raises."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(
            cmd if isinstance(cmd, list) else ["bash", "-c", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, env=env,
        )
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def out(cmd, timeout=20):
    return run(cmd, timeout)[1]


def has_cmd(name):
    return shutil.which(name) is not None


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def sysctl(key):
    """Read a sysctl value straight from /proc/sys (no root needed)."""
    return " ".join(read("/proc/sys/" + key.replace(".", "/")).split())


def pkg_installed(name):
    rc, o = run(["dpkg-query", "-W", "-f=${Status}", name])
    return rc == 0 and "install ok installed" in o


def unit_enabled(unit):
    return out(["systemctl", "is-enabled", unit]) in ("enabled", "enabled-runtime", "static", "alias")


def unit_active(unit):
    return out(["systemctl", "is-active", unit]) == "active"


def dir_size(path):
    """Size in bytes of everything under path that we can read."""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                st = os.lstat(os.path.join(root, f))
                total += st.st_blocks * 512
            except OSError:
                pass
    return total


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def q(s):
    return shlex.quote(str(s))


def cpu_vendor():
    info = read("/proc/cpuinfo")
    if "GenuineIntel" in info:
        return "intel"
    if "AuthenticAMD" in info:
        return "amd"
    return "other"


def is_laptop():
    return any(Path("/sys/class/power_supply").glob("BAT*"))


def has_ssd():
    for rot in Path("/sys/block").glob("*/queue/rotational"):
        dev = rot.parent.parent.name
        if dev.startswith(("loop", "ram", "zram", "sr")):
            continue
        if read(rot) == "0":
            return True
    return False
