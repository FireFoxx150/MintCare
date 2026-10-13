"""Quick benchmarks: sequential disk read/write per drive and a CPU hashing score, kept as history."""

import hashlib
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

from .util import out

MB = 1 << 20


def targets():
    """Writable mounted filesystems worth testing: [(mountpoint, device, fstype, free)]."""
    rows = out(["findmnt", "-rn", "-o", "TARGET,SOURCE,FSTYPE", "-t", "ext4,btrfs,xfs,f2fs,ntfs,ntfs3,fuseblk,exfat,vfat"])
    seen, res = set(), []
    for line in rows.splitlines():
        f = line.split()
        if len(f) < 3 or f[0].startswith(("/boot", "/snap", "/timeshift")) or f[1] in seen:
            continue
        mp = os.path.expanduser("~") if f[0] == "/" else f[0]
        if os.access(mp, os.W_OK):
            seen.add(f[1])
            res.append((mp, f[1], f[2], shutil.disk_usage(mp).free))
    return res


def disk(path, size_mb=256, progress=lambda text: None):
    """Sequential write then read of a temporary file, bypassing the page cache where possible."""
    fd, tmp = tempfile.mkstemp(prefix=".mintcare-bench-", dir=path)
    os.close(fd)
    block = os.urandom(4 * MB)
    try:
        progress("writing…")
        t0 = time.monotonic()
        with open(tmp, "wb", buffering=0) as f:
            for _x in range(size_mb // 4):
                f.write(block)
            os.fsync(f.fileno())
        write = size_mb * MB / (time.monotonic() - t0)
        try:
            fd2 = os.open(tmp, os.O_RDONLY)
            os.posix_fadvise(fd2, 0, 0, os.POSIX_FADV_DONTNEED)     # drop it from cache so we read the disk
            os.close(fd2)
        except (OSError, AttributeError):
            pass
        progress("reading…")
        t0 = time.monotonic()
        with open(tmp, "rb", buffering=0) as f:
            while f.read(4 * MB):
                pass
        read = size_mb * MB / (time.monotonic() - t0)
    finally:
        os.unlink(tmp)
    return {"write": write, "read": read}


def _hash_chunk(n):
    data = b"\x5a" * (8 * MB)
    t0 = time.monotonic()
    for _x in range(n):
        hashlib.sha256(data).digest()
    return n * 8 * MB / (time.monotonic() - t0)


def cpu(seconds=3):
    """Single-core and all-core SHA-256 throughput in MB/s."""
    single = _hash_chunk(24)
    cores = os.cpu_count() or 1
    t0 = time.monotonic()
    with ProcessPoolExecutor(max_workers=cores) as ex:
        list(ex.map(_hash_chunk, [24] * cores))
    total = cores * 24 * 8 * MB / (time.monotonic() - t0)
    return {"single": single / MB, "multi": total / MB, "cores": cores}


def _file():
    from . import maintenance
    return maintenance.STATE / "benchmarks.jsonl"


def save(kind, target, result):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "kind": kind, "target": target, **result}) + "\n")


def history(kind=None, target=None):
    try:
        rows = [json.loads(x) for x in _file().read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if (kind is None or r["kind"] == kind) and (target is None or r["target"] == target)]
