"""Check GitHub for a newer MintCare release, download it, verify its SHA-256 and install it."""

import hashlib
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

from .i18n import _
from . import __version__
from .util import q

REPO = "FireFoxx150/MintCare"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def _cache_file():
    from . import maintenance
    return maintenance.STATE / "update-check.json"


def parse_version(v):
    out = []
    for part in v.lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(latest, current=__version__):
    return parse_version(latest) > parse_version(current)


def installed_from_package():
    return Path(__file__).resolve().is_relative_to("/usr/lib/mintcare")


def check(timeout=8):
    """Query GitHub. Returns {'version', 'notes', 'url', 'asset', 'size', 'sha256', 'published'}."""
    req = urllib.request.Request(API, headers={"User-Agent": f"MintCare/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    deb = next((a for a in data.get("assets", []) if a.get("name", "").endswith(".deb")), None)
    digest = (deb or {}).get("digest") or ""
    info = {
        "version": data.get("tag_name", "").lstrip("v"),
        "notes": data.get("body") or "",
        "url": data.get("html_url") or RELEASES_PAGE,
        "published": data.get("published_at", ""),
        "asset": deb.get("browser_download_url") if deb else None,
        "asset_name": deb.get("name") if deb else None,
        "size": deb.get("size", 0) if deb else 0,
        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
        "checked": time.time(),
    }
    try:
        f = _cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(info))
    except OSError:
        pass
    return info


def cached(max_age=6 * 3600):
    try:
        info = json.loads(_cache_file().read_text())
        if time.time() - info.get("checked", 0) < max_age:
            return info
    except (OSError, ValueError):
        pass
    return None


def latest(max_age=6 * 3600, timeout=8):
    """Cached result if fresh, otherwise ask GitHub. Returns None when offline."""
    info = cached(max_age)
    if info:
        return info
    try:
        return check(timeout)
    except (OSError, ValueError):
        return None


def download(info, progress=lambda done, total: None):
    """Fetch the .deb into a fresh /tmp folder (readable by apt's _apt user) and verify it."""
    if not info.get("asset"):
        raise RuntimeError(_("This release has no .deb attached"))
    folder = tempfile.mkdtemp(prefix="mintcare-update-", dir="/tmp")
    os.chmod(folder, 0o755)
    path = os.path.join(folder, info["asset_name"])
    h = hashlib.sha256()
    req = urllib.request.Request(info["asset"], headers={"User-Agent": f"MintCare/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    os.chmod(path, 0o644)
    if info.get("sha256"):
        if h.hexdigest() != info["sha256"]:
            os.unlink(path)
            raise RuntimeError(_("Downloaded file does not match the release checksum — not installing it"))
    else:
        raise RuntimeError(_("The release has no checksum to verify against — not installing it"))
    return path


def install_step(path, version):
    return ("selfupdate", f"Install MintCare {version}",
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {q(path)}")
