import os
import unittest

from mintcare import space
from mintcare.widgets import _squarify
from tests.helpers import TempDirTest

MB = 1 << 20


class Duplicates(TempDirTest):
    def test_finds_identical_content_only(self):
        blob = os.urandom(2 * MB)
        a = self.write("a/one.bin", blob, mtime=1_000_000)
        b = self.write("b/two.bin", blob, mtime=2_000_000)
        self.write("c/same-size-different.bin", os.urandom(2 * MB))
        self.write("d/small.bin", b"x" * 100)               # below min size
        groups = space.duplicates([self.tmp], min_size=MB)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual([p for p, _m in g["files"]], [a, b])   # oldest first = kept
        self.assertEqual(g["wasted"], 2 * MB)

    def test_same_ends_different_middle(self):
        head, tail = os.urandom(70 * 1024), os.urandom(70 * 1024)
        self.write("x.bin", head + os.urandom(MB) + tail)
        self.write("y.bin", head + os.urandom(MB) + tail)
        self.assertEqual(space.duplicates([self.tmp], min_size=MB // 2), [])

    def test_hardlinks_are_not_duplicates(self):
        p = self.write("orig.bin", os.urandom(2 * MB))
        os.link(p, os.path.join(self.tmp, "link.bin"))
        self.assertEqual(space.duplicates([self.tmp], min_size=MB), [])

    def test_hidden_skipped_unless_asked(self):
        blob = os.urandom(2 * MB)
        self.write(".hidden/a.bin", blob)
        self.write("visible/b.bin", blob)
        self.assertEqual(space.duplicates([self.tmp], min_size=MB), [])
        self.assertEqual(len(space.duplicates([self.tmp], min_size=MB, include_hidden=True)), 1)

    def test_cancel(self):
        blob = os.urandom(2 * MB)
        self.write("a.bin", blob)
        self.write("b.bin", blob)
        self.assertEqual(space.duplicates([self.tmp], min_size=MB, cancel=lambda: True), [])


class Tree(TempDirTest):
    def test_sizes_and_find(self):
        self.write("big/file.bin", os.urandom(60 * MB))
        self.write("small/a.txt", b"hello")
        tree, big = space.scan_tree(self.tmp)
        self.assertEqual(tree.files, 2)
        self.assertGreaterEqual(tree.size, 60 * MB)
        self.assertEqual(tree.children[0].name, "big")
        self.assertEqual(len(big), 1)
        node = space.find(tree, os.path.join(self.tmp, "big"))
        self.assertIsNotNone(node)
        self.assertEqual(node.children[0].name, "file.bin")


class Treemap(unittest.TestCase):
    def test_squarify_fills_area_in_bounds(self):
        rects = _squarify([(v, i) for i, v in enumerate([50, 20, 10, 8, 5, 4, 2, 1])], 0, 0, 800, 300)
        self.assertEqual(len(rects), 8)
        self.assertAlmostEqual(sum(w * h for _x, _y, w, h, _p in rects), 800 * 300, delta=1)
        for x, y, w, h, _p in rects:
            self.assertGreaterEqual(x, -1e-6)
            self.assertGreaterEqual(y, -1e-6)
            self.assertLessEqual(x + w, 800 + 1e-6)
            self.assertLessEqual(y + h, 300 + 1e-6)

    def test_squarify_ignores_zero(self):
        self.assertEqual(_squarify([(0, "a")], 0, 0, 10, 10), [])


class OldDownloads(TempDirTest):
    def test_big_and_old_only(self):
        now = 1_000_000_000
        for name, size, age_days in (("old-big.iso", 20 << 20, 200), ("new-big.iso", 20 << 20, 5),
                                     ("old-small.txt", 1000, 200), (".hidden/old-big.bin", 20 << 20, 200)):
            p = os.path.join(self.tmp, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.truncate(size)                       # sparse: no real disk use
            t = now - age_days * 86400
            os.utime(p, (t, t))
        found = space.old_downloads(self.tmp, now=now)
        self.assertEqual([os.path.basename(p) for _s, p, _l in found], ["old-big.iso"])


class TimeshiftRestore(TempDirTest):
    def test_only_files_inside_the_snapshot(self):
        root = os.path.join(self.tmp, "snap1", "localhost")
        os.makedirs(os.path.join(root, "etc"))
        with open(os.path.join(root, "etc", "fstab"), "w") as f:
            f.write("x")
        os.symlink("/etc/passwd", os.path.join(root, "etc", "escape"))
        script = space.restore_file_script("snap1", [os.path.join(root, "etc", "fstab")], base=self.tmp)
        self.assertIn("cp -a --backup=numbered", script)
        self.assertIn(" /etc/fstab ", script)
        for bad in (os.path.join(root, "etc"), os.path.join(root, "etc", "escape"), "/etc/hosts"):
            with self.assertRaises(ValueError):
                space.restore_file_script("snap1", [bad], base=self.tmp)


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class ReducedMotion(unittest.TestCase):
    def test_gauge_jumps_when_animations_are_off(self):
        from gi.repository import Gtk
        from mintcare.widgets import RingGauge
        settings = Gtk.Settings.get_default()
        was = settings.props.gtk_enable_animations
        try:
            settings.props.gtk_enable_animations = False
            g = RingGauge()
            g.set_value(70)
            self.assertEqual(g.shown, 70)
            settings.props.gtk_enable_animations = True
            g.set_value(20)
            self.assertEqual(g.shown, 70)          # animating: still on its way down
        finally:
            settings.props.gtk_enable_animations = was


if __name__ == "__main__":
    unittest.main()


class FileKinds(unittest.TestCase):
    def test_kinds_from_extension(self):
        from mintcare import space
        self.assertEqual(space.file_kind("/home/x/Holiday.MP4"), "video")
        self.assertEqual(space.file_kind("/home/x/ubuntu.iso"), "disk")
        self.assertEqual(space.file_kind("/home/x/backup.tar.gz"), "archive")
        self.assertEqual(space.file_kind("/home/x/notes.bin"), "other")
