"""A button made with set_no_show_all(True) must show its label when it is shown. show_all() skips such a widget and
everything inside it, so set_visible(True) leaves the label hidden and the button looks empty. Needs a display, so it is
skipped without one (CI runs the tests under xvfb)."""
import os
import unittest

from mintcare.widgets import show_or_hide


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "needs a display (CI runs the tests under xvfb)")
class ShowOrHide(unittest.TestCase):
    def build(self):
        from gi.repository import Gtk
        parent = Gtk.Box()
        button = Gtk.Button()
        inner = Gtk.Box()
        text = Gtk.Label(label="Update now")
        inner.pack_start(text, False, False, 0)
        button.add(inner)
        button.set_no_show_all(True)
        parent.pack_start(button, False, False, 0)
        parent.show_all()
        self.parent = parent            # keep the box alive: once it is freed, GTK destroys the button's child
        return button, text

    def test_set_visible_shows_the_button_but_not_its_label(self):
        button, text = self.build()
        button.set_visible(True)
        self.assertTrue(button.get_visible())
        self.assertFalse(text.get_visible())

    def test_show_or_hide_shows_the_label_and_hides_again(self):
        button, text = self.build()
        show_or_hide(button, True)
        self.assertTrue(button.get_visible())
        self.assertTrue(text.get_visible())
        show_or_hide(button, False)
        self.assertFalse(button.get_visible())


if __name__ == "__main__":
    unittest.main()
