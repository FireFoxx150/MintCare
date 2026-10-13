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


if __name__ == "__main__":
    unittest.main()
