import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from mintcare import gui  # noqa: E402


class DuplicateThumbnails(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.picture = os.path.join(self._dir.name, "picture.png")
        GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 40, 30).savev(self.picture, "png", [], [])

    def test_a_picture_gets_a_thumbnail_no_bigger_than_48_pixels(self):
        thumb = gui._thumbnail(self.picture)
        self.assertIsNotNone(thumb)
        self.assertLessEqual(max(thumb.get_width(), thumb.get_height()), 48)

    def test_an_icon_gets_a_picture_no_bigger_than_its_size(self):
        icon = gui._thumbnail(self.picture, 28)
        self.assertLessEqual(max(icon.get_width(), icon.get_height()), 28)

    def test_a_picture_over_the_pixel_limit_gets_none(self):
        with mock.patch.object(gui, "THUMB_MAX_PIXELS", 100):    # the picture is 40 x 30 = 1200 pixels
            self.assertIsNone(gui._thumbnail(self.picture))

    def test_a_file_that_is_not_a_picture_gets_none(self):
        text = Path(self._dir.name, "notes.txt")
        text.write_text("not an image")
        self.assertIsNone(gui._thumbnail(str(text)))


if __name__ == "__main__":
    unittest.main()
