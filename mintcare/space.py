"""Disk-space analysis: folder tree + largest files, duplicate finder, Timeshift snapshots.

Everything here runs unprivileged; deleting files moves them to the Trash.
"""

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _
from .util import out, read

HOME = str(Path.home())
SKIP_SYSTEM = {"/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp", "/timeshift", "/snap", "/lost+found"}
MIN_NODE = 1 << 20          # files smaller than 1 MB are folded into "(small files)"


@dataclass
class Node:
    name: str
    path: str
    size: int = 0
    is_dir: bool = True
    children: list = field(default_factory=list)
    small: int = 0          # bytes in files < MIN_NODE directly inside this dir
    files: int = 0


def scan_tree(root, progress=None, cancel=lambda: False, one_fs=True):
    """Walk root and build a size tree. Returns (tree, largest_files)."""
    root = os.path.abspath(root)
    try:
        dev = os.stat(root).st_dev
    except OSError:
        return Node(os.path.basename(root) or root, root), []
    big = []                # (size, path, mtime)
    seen_inodes = set()
    count = [0]
    last = [time.monotonic()]

    def walk(path, name):
        node = Node(name, path)
        try:
            it = os.scandir(path)
        except OSError:
            return node
        with it:
            for e in it:
                if cancel():
                    break
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if (one_fs and st.st_dev != dev) or e.path in SKIP_SYSTEM:
                        continue
                    child = walk(e.path, e.name)
                    node.size += child.size
                    node.files += child.files
                    if child.size:
                        node.children.append(child)
                elif stat.S_ISREG(st.st_mode):
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen_inodes:
                            continue
                        seen_inodes.add(key)
                    sz = st.st_blocks * 512
                    node.size += sz
                    node.files += 1
                    count[0] += 1
                    if sz >= MIN_NODE:
                        node.children.append(Node(e.name, e.path, sz, False))
                        if sz >= 50 * MIN_NODE:
                            big.append((sz, e.path, st.st_mtime))
                    else:
                        node.small += sz
        node.children.sort(key=lambda n: -n.size)
        if progress and time.monotonic() - last[0] > 0.25:
            last[0] = time.monotonic()
            progress(count[0], path)
        return node

    tree = walk(root, os.path.basename(root) or root)
    big.sort(reverse=True)
    return tree, big[:200]


def find(tree, path):
    """Locate the node for path inside tree (or None)."""
    if tree.path == path:
        return tree
    for c in tree.children:
        if c.is_dir and (path == c.path or path.startswith(c.path + os.sep)):
            return find(c, path)
    return None


# ------------------------------------------------------------------ duplicates

def _hash(path, limit=None, chunk=1 << 20, cancel=lambda: False, tick=None):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        if limit:
            h.update(f.read(limit))
            try:
                f.seek(-limit, os.SEEK_END)
                h.update(f.read(limit))
            except OSError:
                pass
        else:
            while True:
                if cancel():
                    raise InterruptedError
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
                if tick:
                    tick(len(b))
    return h.hexdigest()


def duplicates(roots, min_size=1 << 20, include_hidden=False, progress=None, cancel=lambda: False):
    """Groups of identical files: [{'size': n, 'files': [(path, mtime)], 'wasted': n}]."""
    by_size, inodes = {}, set()
    scanned = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if cancel():
                return []
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not include_hidden and fn.startswith("."):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size < min_size:
                    continue
                key = (st.st_dev, st.st_ino)
                if key in inodes:          # hard link to a file we already have
                    continue
                inodes.add(key)
                by_size.setdefault(st.st_size, []).append((p, st.st_mtime))
                scanned += 1
                if progress and scanned % 500 == 0:
                    progress("Listing", scanned, 0)
    candidates = [(size, v) for size, v in by_size.items() if len(v) > 1]
    total = sum(size * len(v) for size, v in candidates) or 1
    done = [0]
    last = [0.0]

    def tick(n):
        done[0] += n
        if progress and time.monotonic() - last[0] > 0.2:
            last[0] = time.monotonic()
            progress("Comparing", done[0], total)
    groups = []
    try:
        for size, files in candidates:
            if cancel():
                return []
            partial = {}
            for p, m in files:
                try:
                    partial.setdefault(_hash(p, limit=64 * 1024), []).append((p, m))
                except OSError:
                    pass
            for same in partial.values():
                if len(same) < 2:
                    tick(size * len(same))      # ruled out without reading the whole file
                    continue
                full = {}
                for p, m in same:
                    try:
                        if size > 128 * 1024:
                            full.setdefault(_hash(p, cancel=cancel, tick=tick), []).append((p, m))
                        else:
                            full.setdefault(_hash(p), []).append((p, m))
                            tick(size)
                    except OSError:
                        tick(size)
                for grp in full.values():
                    if len(grp) > 1:
                        grp.sort(key=lambda x: (x[1], len(x[0])))   # oldest first = the one we keep
                        groups.append({"size": size, "files": grp, "wasted": size * (len(grp) - 1)})
    except InterruptedError:
        return []
    groups.sort(key=lambda g: -g["wasted"])
    return groups


DUP_DEFAULT_DIRS = ["Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"]


def default_dup_roots():
    from gi.repository import GLib
    dirs = []
    for d in (GLib.UserDirectory.DIRECTORY_DESKTOP, GLib.UserDirectory.DIRECTORY_DOCUMENTS,
              GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_MUSIC,
              GLib.UserDirectory.DIRECTORY_PICTURES, GLib.UserDirectory.DIRECTORY_VIDEOS):
        p = GLib.get_user_special_dir(d)
        if p and os.path.isdir(p) and p != HOME and p not in dirs:
            dirs.append(p)
    return dirs or [HOME]


def trash(path):
    """Move to the Trash (reversible). Raises on failure."""
    from gi.repository import Gio
    Gio.File.new_for_path(path).trash(None)


# ------------------------------------------------------------------ timeshift

def _ts_config():
    try:
        return json.loads(read("/etc/timeshift/timeshift.json") or "{}")
    except ValueError:
        return {}


def snapshot_dir():
    cfg = _ts_config()
    uuid = cfg.get("backup_device_uuid", "")
    if not uuid:
        return None
    root_uuid = out(["findmnt", "-no", "UUID", "/"])
    if uuid == root_uuid:
        base = "/timeshift"
    else:
        mnt = out(["findmnt", "-rno", "TARGET", "-S", f"UUID={uuid}"]).splitlines()
        base = os.path.join(mnt[0], "timeshift") if mnt else None
    d = os.path.join(base, "snapshots") if base else None
    return d if d and os.path.isdir(d) else None


def snapshots():
    """List Timeshift rsync snapshots, newest first. Returns (list, note)."""
    cfg = _ts_config()
    if not cfg:
        return [], _("Timeshift is not set up.")
    if cfg.get("btrfs_mode") == "true":
        return [], _("BTRFS snapshots are managed inside Timeshift.")
    d = snapshot_dir()
    if not d:
        return [], _("The Timeshift backup drive isn't mounted, so snapshots can't be listed here.")
    res = []
    for name in sorted(os.listdir(d), reverse=True):
        info_p = os.path.join(d, name, "info.json")
        try:
            info = json.loads(Path(info_p).read_text())
        except (OSError, ValueError):
            continue
        res.append({
            "name": name,
            "created": float(info.get("created", 0) or 0),
            "tags": info.get("tags", "").split(),
            "comment": info.get("comments", ""),
            "files": int(info.get("file_count", 0) or 0),
        })
    if res:
        res[0]["latest"] = True
    return res, ""


def timeshift_summary():
    cfg = _ts_config()
    sched = [k[9:] for k, v in cfg.items() if k.startswith("schedule_") and v == "true"]
    keep = {k[6:]: cfg[k] for k in cfg if k.startswith("count_") and k[6:] in sched}
    return {"schedule": sched, "keep": keep, "base_size": int(cfg.get("snapshot_size", 0) or 0)}


def delete_snapshot_script(name):
    from .util import q
    return f"timeshift --delete --snapshot {q(name)} --scripted --yes"
