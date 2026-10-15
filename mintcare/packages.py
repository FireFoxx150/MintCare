"""Package audit, apt history (with undo), kernels, mirror speed test and Mint upgrade check."""

import gzip
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .i18n import _
from .util import out, q, read, run

HISTORY = Path("/var/log/apt")
OFFICIAL_LIST = Path("/etc/apt/sources.list.d/official-package-repositories.list")
MINT_MIRRORS = Path("/usr/share/mint-mirrors/linuxmint.list")
UBUNTU_MIRRORS = Path("/usr/share/python-apt/templates/Ubuntu.mirrors")
MINT_DEFAULT = "http://packages.linuxmint.com"
UBUNTU_DEFAULT = "http://archive.ubuntu.com/ubuntu"


# ------------------------------------------------------------------ audit

def installed():
    """{name: {'size': bytes, 'version', 'section', 'priority', 'essential', 'desc'}}."""
    rc, o = run(["dpkg-query", "-W", "-f",
                 "${db:Status-Abbrev}\t${Package}\t${Installed-Size}\t${Version}\t${Section}\t${Priority}\t"
                 "${Essential}\t${binary:Summary}\n"], timeout=60)
    pk = {}
    for line in o.splitlines():
        f = line.split("\t")
        if len(f) < 8 or not f[0].startswith("ii"):
            continue
        pk[f[1]] = {"size": int(f[2] or 0) * 1024, "version": f[3], "section": f[4], "priority": f[5],
                    "essential": f[6] == "yes", "desc": f[7]}
    return pk


def manual():
    return set(out(["apt-mark", "showmanual"], timeout=30).split())


def held():
    return out(["apt-mark", "showhold"]).split()


def protected(name, info):
    return info.get("essential") or info.get("priority") in ("required", "important") or \
        name.startswith(("linux-image-", "linux-headers-", "linux-modules-", "linux-firmware", "mint", "cinnamon",
                         "nemo", "muffin", "lightdm", "slick-greeter", "systemd", "apt", "dpkg", "python3", "libc",
                         "libgtk", "libglib", "xserver", "xorg", "network-manager", "pipewire", "wireplumber",
                         "policykit", "pkexec", "sudo", "grub", "shim", "ubuntu-"))


def largest(pk=None, n=40):
    pk = pk or installed()
    return sorted(((v["size"], k, v) for k, v in pk.items()), reverse=True)[:n]


def history():
    """apt history entries, newest first: [{'date','cmd','by','install','upgrade','remove','purge'}]."""
    logs = sorted(HISTORY.glob("history.log*"), key=lambda p: (p.suffix != ".log", p.name), reverse=True)
    entries = []
    for f in logs:
        try:
            text = gzip.open(f, "rt").read() if f.suffix == ".gz" else f.read_text()
        except OSError:
            continue
        for block in text.split("\n\n"):
            e = {"date": "", "cmd": "", "by": "", "install": [], "upgrade": [], "remove": [], "purge": []}
            for line in block.strip().splitlines():
                k, _x, v = line.partition(": ")
                if k == "Start-Date":
                    e["date"] = " ".join(v.split())
                elif k == "Commandline":
                    e["cmd"] = v
                elif k == "Requested-By":
                    e["by"] = v.split(" (")[0]
                elif k.lower() in ("install", "upgrade", "remove", "purge"):
                    # "pkg:amd64 (ver), pkg2:amd64 (ver, automatic)"
                    for m in re.finditer(r"([^\s,()]+?)(?::\w+)? \(([^)]*)\)", v):
                        e[k.lower()].append({"pkg": m.group(1), "auto": "automatic" in m.group(2)})
            if e["date"]:
                entries.append(e)
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def user_installed(pk=None, hist=None):
    """Packages you installed (via apt/Software Manager), still installed and still manual."""
    pk = pk or installed()
    man = manual()
    hist = history() if hist is None else hist
    names = []
    for e in hist:
        if "unattended" in e["cmd"] or "mintupdate" in e["cmd"]:
            continue
        for p in e["install"]:
            if not p["auto"] and p["pkg"] in pk and p["pkg"] in man and p["pkg"] not in names:
                names.append(p["pkg"])
    return names


def undo_candidates(entry, pk=None):
    """Packages an 'undo' of a history entry would remove: its non-automatic installs still present."""
    pk = pk or installed()
    return [p["pkg"] for p in entry["install"] if not p["auto"] and p["pkg"] in pk and not protected(p["pkg"], pk[p["pkg"]])]


def simulate_remove(names):
    """What apt would remove (names) — shown before anything is done."""
    rc, o = run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "remove", *names], timeout=60)
    return [line.split()[1] for line in o.splitlines() if line.startswith("Remv ")], rc == 0


def remove_step(names, purge=False):
    verb = "purge" if purge else "remove"
    return (f"pkg-{verb}", f"{verb.capitalize()} {', '.join(names[:4])}{'…' if len(names) > 4 else ''}",
            f"DEBIAN_FRONTEND=noninteractive apt-get {verb} -y {' '.join(q(n) for n in names)}\n"
            "DEBIAN_FRONTEND=noninteractive apt-get autoremove -y")


def unhold_step(name):
    return ("unhold", f"Release hold on {name}", f"apt-mark unhold {q(name)}")


# ------------------------------------------------------------------ kernels

def kernels(pk=None):
    pk = pk or installed()
    running = os.uname().release
    vers = sorted({re.sub(r"^linux-image-(unsigned-)?", "", n) for n in pk
                   if re.match(r"linux-image-(unsigned-)?\d", n)},
                  key=lambda v: [int(x) if x.isdigit() else x for x in re.split(r"[.-]", v)])
    out_ = []
    for i, v in enumerate(vers):
        related = [n for n in pk if n.endswith(v) and n.startswith(("linux-image-", "linux-headers-", "linux-modules-"))]
        size = sum(pk[n]["size"] for n in related)
        out_.append({"version": v, "running": v == running, "newest": i == len(vers) - 1,
                     "packages": related, "size": size})
    return list(reversed(out_))


def kernel_remove_step(k):
    names = " ".join(q(n) for n in k["packages"])
    return ("kernel-rm", _('Remove kernel {0}').format(k['version']),
            f"DEBIAN_FRONTEND=noninteractive apt-get purge -y {names}\nupdate-grub")


# ------------------------------------------------------------------ mirrors

def _country():
    tz = (read("/etc/timezone") or os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]).strip()
    for line in read("/usr/share/zoneinfo/zone.tab").splitlines():
        f = line.split("\t")
        if len(f) >= 3 and f[2] == tz:
            return f[0]
    m = re.search(r"_([A-Z]{2})", os.environ.get("LANG", ""))
    return m.group(1) if m else ""


def _mirror_list(path, country):
    cur, out_ = None, []
    for line in read(path).splitlines():
        if line.startswith("#LOC:"):
            cur = line[5:].strip()
        elif line.startswith("http") and cur == country:
            out_.append(line.split()[0].rstrip("/"))
    return out_


def current_mirrors():
    text = read(OFFICIAL_LIST)
    mint = re.search(r"^deb\s+(\S+)\s+\w+\s+main upstream", text, re.M)
    ubu = re.search(r"^deb\s+(\S+)\s+\w+-updates\s", text, re.M)
    return (mint.group(1).rstrip("/") if mint else MINT_DEFAULT, ubu.group(1).rstrip("/") if ubu else UBUNTU_DEFAULT)


def _codenames():
    info = read("/etc/linuxmint/info")
    m = re.search(r"^CODENAME=(\w+)", info, re.M)
    base = ""
    for line in read("/etc/os-release").splitlines():
        if line.startswith("UBUNTU_CODENAME="):
            base = line.split("=", 1)[1].strip('"')
    return (m.group(1) if m else ""), base


def _speed(url, timeout=6):
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MintCare mirror test"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(2 * 1024 * 1024)
    except (OSError, ValueError):
        return None, None
    dt = max(time.monotonic() - t0, 1e-3)
    date = re.search(rb"^Date: (.+)$", data, re.M)
    return len(data) / dt, (date.group(1).decode(errors="replace") if date else "")


def test_mirrors(progress=lambda done, total: None, limit=12):
    """Speed-test mirrors in your country (plus the defaults). Returns {'mint': [...], 'ubuntu': [...]}."""
    country = _country()
    codename, base = _codenames()
    cur_mint, cur_ubu = current_mirrors()
    sets = {
        "mint": ([MINT_DEFAULT, cur_mint] + _mirror_list(MINT_MIRRORS, country))[:limit + 2],
        "ubuntu": ([UBUNTU_DEFAULT, cur_ubu] + _mirror_list(UBUNTU_MIRRORS, country))[:limit + 2],
    }
    jobs = []
    for kind, urls in sets.items():
        suite = codename if kind == "mint" else f"{base}-updates"
        for u in dict.fromkeys(urls):
            jobs.append((kind, u, f"{u}/dists/{suite}"))
    results = {"mint": [], "ubuntu": [], "country": country}
    done = [0]

    def one(job):
        kind, base_url, dist = job
        _x, date = _speed(f"{dist}/Release")                  # freshness (Date: field)
        speed, _y = _speed(f"{dist}/main/binary-amd64/Packages.gz", timeout=10)   # throughput, ≤2 MB
        done[0] += 1
        progress(done[0], len(jobs))
        return kind, base_url, speed, date
    with ThreadPoolExecutor(max_workers=6) as ex:
        for kind, u, speed, date in ex.map(one, jobs):
            results[kind].append({"url": u, "speed": speed, "date": date,
                                  "current": u in (cur_mint, cur_ubu), "default": u in (MINT_DEFAULT, UBUNTU_DEFAULT)})
    for kind in ("mint", "ubuntu"):
        ref = next((r["date"] for r in results[kind] if r["default"] and r["date"]), "")
        for r in results[kind]:
            r["stale"] = bool(ref and r["date"] and r["date"] != ref)
        results[kind].sort(key=lambda r: (r["speed"] is None, r["stale"], -(r["speed"] or 0)))
    return results


def switch_mirrors_step(mint_url, ubuntu_url):
    """Rewrite the official sources to the chosen mirrors (backup kept in /var/lib/mintcare)."""
    cur_mint, cur_ubu = current_mirrors()
    f = q(str(OFFICIAL_LIST))
    script = (f"mkdir -p /var/lib/mintcare\n[ -f /var/lib/mintcare/official-package-repositories.list.orig ] || "
              f"cp -a {f} /var/lib/mintcare/official-package-repositories.list.orig\n")
    if mint_url and mint_url != cur_mint:
        script += f"sed -i 's|{cur_mint}|{mint_url}|g' {f}\n"
    if ubuntu_url and ubuntu_url != cur_ubu:
        script += f"sed -i 's|{cur_ubu}|{ubuntu_url}|g' {f}\n"
    script += f"cat {f}\napt-get update 2>&1 | tail -3"
    return ("mirrors", _("Switch to the fastest mirrors"), script)


def restore_mirrors_step():
    return ("mirrors-restore", _("Restore the original mirrors"),
            f"cp -a /var/lib/mintcare/official-package-repositories.list.orig {q(str(OFFICIAL_LIST))}\n"
            "apt-get update 2>&1 | tail -3")


# ------------------------------------------------------------------ Mint upgrade

def mint_release():
    info = read("/etc/linuxmint/info")
    rel = re.search(r"^RELEASE=(\S+)", info, re.M)
    code = re.search(r"^CODENAME=(\w+)", info, re.M)
    return (rel.group(1) if rel else "?"), (code.group(1) if code else "?")


def _release_info(name, timeout):
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"{MINT_DEFAULT}/dists/{name}/Release", headers={"User-Agent": "MintCare"}), timeout=timeout) as r:
            head = r.read(4096).decode(errors="replace")
    except (OSError, ValueError):
        return None
    ver = re.search(r"^Version: (\S+)", head, re.M)
    desc = re.search(r"^Description: (.+)$", head, re.M)
    return (ver.group(1) if ver else ""), (desc.group(1) if desc else "")


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:2]) or (0,)


def upgrade_available(timeout=8):
    """A newer mainline Linux Mint release on packages.linuxmint.com: (codename, version) or None.

    Codenames don't sort by release, so each unknown suite's Release file is read for its Version and
    Description ("Linux Mint 23 …" vs "LMDE 7 …").
    """
    try:
        with urllib.request.urlopen(urllib.request.Request(
                MINT_DEFAULT + "/dists/", headers={"User-Agent": "MintCare"}), timeout=timeout) as r:
            listing = r.read().decode(errors="replace")
    except (OSError, ValueError):
        return None
    rel, _code = mint_release()
    known = {p.name for p in Path("/usr/share/mintsources").iterdir()} if Path("/usr/share/mintsources").is_dir() else set()
    best = None
    for name in re.findall(r'href="([a-z]+)/"', listing):
        if name in known or name in ("debian", "romeo"):
            continue
        info = _release_info(name, timeout)
        if not info:
            continue
        ver, desc = info
        cur, new = _vtuple(rel), _vtuple(ver)
        # only the next point release (22.x) or the next major (23): the archive also holds internal
        # and test suites with odd version numbers that aren't real upgrades
        plausible = new[0] == cur[0] or new[0] == cur[0] + 1
        if desc.startswith("Linux Mint") and plausible and new > cur and (not best or new > _vtuple(best[1])):
            best = (name, ver)
    return best


def upgrade_readiness():
    """[(level, text)] checklist before a Mint upgrade."""
    import shutil
    from . import space
    items = []
    free = shutil.disk_usage("/").free
    items.append(("ok" if free > 15 << 30 else "fail", _('{0} GB free on / (need about 15 GB)').format(free >> 30)))
    snaps, _note = space.snapshots()
    recent = snaps and time.time() - snaps[0]["created"] < 3 * 86400
    items.append(("ok" if recent else "warn", _("Recent Timeshift snapshot") if recent else
                  _("No Timeshift snapshot in the last 3 days — take one first")))
    n_up = sum(1 for line in out(["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"], timeout=60).splitlines()
               if line.startswith("Inst "))
    items.append(("ok" if n_up == 0 else "warn", _("All updates installed") if n_up == 0 else
                  _('{0} updates pending — install them first').format(n_up)))
    from . import sources
    third = [s_ for s_ in sources.sources() if s_.enabled and s_.kind in ("PPA", "Third-party")]
    items.append(("ok" if not third else "info", _("No PPAs or third-party sources") if not third else
                  _('{0} PPA/third-party source(s) — the upgrade tool disables them temporarily').format(len(third))))
    return items
