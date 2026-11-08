"""Check GitHub for a newer MintCare release, download it, check its signature and install it."""

import base64
import hashlib
import json
import os
import re
import subprocess
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
# The public half of the release key is installed with the package. Its private half is kept off
# GitHub, so someone who can change a release cannot also change the key that checks it.
RELEASE_KEY = Path("/usr/share/mintcare/release-signing.pub")


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
    """Query GitHub. Returns {'version', 'notes', 'url', 'asset', 'size', 'sha256', 'sums', 'sums_sig', ...}."""
    req = urllib.request.Request(API, headers={"User-Agent": f"MintCare/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    assets = data.get("assets", [])
    deb = next((a for a in assets if a.get("name", "").endswith(".deb")), None)
    sums = next((a for a in assets if a.get("name") == "SHA256SUMS"), None)
    sig = next((a for a in assets if a.get("name") == "SHA256SUMS.sig"), None)
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
        "sums": sums.get("browser_download_url") if sums else None,
        "sums_sig": sig.get("browser_download_url") if sig else None,
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


def latest_for(version, timeout=10):
    """A live answer from GitHub, but only while it still names the release the user agreed to.

    The cached answer only decides whether to offer the update. The file that gets installed
    comes from here, and signed_release then checks its checksum.
    """
    info = check(timeout)
    return info if info["version"] == version else None


def _fetch(url, timeout=30, limit=1 << 20):
    """A small release file, read whole. Refuses anything over a megabyte."""
    req = urllib.request.Request(url, headers={"User-Agent": f"MintCare/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("release file too large")
    return data


def _digests(sums_text):
    """(sha256, name) pairs from a sha256sum list, in text or binary form."""
    pairs = []
    for line in sums_text.splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def signed_release(info, key=RELEASE_KEY, timeout=30):
    """A copy of info whose sha256 comes from the release's signed SHA256SUMS.

    GitHub's digest for the asset arrives in the same answer as the asset's URL, so a release that
    someone has replaced passes that check too. SHA256SUMS is checked against the public key that
    ships in the package instead, and only the checksum it lists for this file is used.
    """
    unsigned = _("The release is not signed, or its signature doesn't check out — not installing it")
    if not info.get("sums") or not info.get("sums_sig"):
        raise RuntimeError(unsigned)
    if not Path(key).is_file():
        raise RuntimeError(_("The release signature can't be checked on this install — not installing it"))
    sums, sig = _fetch(info["sums"], timeout), _fetch(info["sums_sig"], timeout)
    with tempfile.TemporaryDirectory(prefix="mintcare-sig-") as d:
        sums_file, sig_file = os.path.join(d, "SHA256SUMS"), os.path.join(d, "SHA256SUMS.sig")
        Path(sums_file).write_bytes(sums)
        Path(sig_file).write_bytes(sig)
        try:
            verified = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
                                       "-rawin", "-in", sums_file, "-sigfile", sig_file],
                                      capture_output=True).returncode == 0
        except OSError:
            raise RuntimeError(_("The release signature can't be checked on this install — not installing it"))
    found = [sha for sha, name in _digests(sums.decode("utf-8", "replace")) if name == info.get("asset_name")]
    if not verified or len(found) != 1 or info.get("sha256") not in (None, found[0]):
        raise RuntimeError(unsigned)
    return dict(info, sha256=found[0])


def download(info, progress=lambda done, total: None):
    """Fetch the .deb into memory and check it against the release's SHA-256. Returns the bytes."""
    if not info.get("asset"):
        raise RuntimeError(_("This release has no .deb attached"))
    h = hashlib.sha256()
    chunks = []
    req = urllib.request.Request(info["asset"], headers={"User-Agent": f"MintCare/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    if info.get("sha256"):
        if h.hexdigest() != info["sha256"]:
            raise RuntimeError(_("Downloaded file does not match the release checksum — not installing it"))
    else:
        raise RuntimeError(_("The release has no checksum to verify against — not installing it"))
    return b"".join(chunks)


def install_step(data, sha256, version):
    """Root step: write the verified .deb into a folder root owns, check it there, install that copy.

    The package travels inside the sealed batch, so root never opens a path the user could change.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ValueError("a SHA-256 checksum is needed before installing")
    body = "\n".join([
        'd=$(mktemp -d /tmp/mintcare-update.XXXXXXXX) || exit 1',
        'trap \'rm -rf -- "$d"\' EXIT',
        'chmod 755 "$d"',  # apt's _apt user must read the copy; only root can write to the folder
        "base64 -d > \"$d/mintcare.deb\" <<'MINTCARE_DEB'",
        base64.encodebytes(data).decode().rstrip("\n"),
        "MINTCARE_DEB",
        f"printf '%s  %s\\n' {q(sha256)} \"$d/mintcare.deb\" | sha256sum -c - || exit 1",
        'DEBIAN_FRONTEND=noninteractive apt-get install -y "$d/mintcare.deb"',
    ])
    return ("selfupdate", f"Install MintCare {version}", body)
