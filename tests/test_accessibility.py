import os
import unittest


def _text(w):
    from gi.repository import Gtk
    if isinstance(w, Gtk.Label) and w.get_text().strip():
        yield w.get_text()
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            yield from _text(c)


def _unnamed_buttons(w, out):
    """Buttons a screen reader would announce with no name: no accessible name, label, tooltip or text."""
    from gi.repository import Gtk
    if isinstance(w, Gtk.Button) and not (w.get_accessible().get_name() or w.get_label()
                                          or w.get_tooltip_text() or any(_text(w))):
        out.append(type(w).__name__)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            _unnamed_buttons(c, out)
    return out


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "needs a display (CI runs the tests under xvfb)")
class Accessibility(unittest.TestCase):
    def test_every_button_on_the_main_window_has_a_name(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        from mintcare import gui
        from unittest import mock
        from mintcare import hardware
        # no graphics card: the GPU card is left out of the dashboard, which must still lay out
        with mock.patch.object(hardware, "gpu_load", return_value=None):
            app = gui.MintCareApp()
            app.do_activate()
            try:
                unnamed = []
                for win in Gtk.Window.list_toplevels():
                    _unnamed_buttons(win, unnamed)
            finally:
                for win in Gtk.Window.list_toplevels():
                    win.destroy()
        self.assertEqual(unnamed, [], f"{len(unnamed)} button(s) have no name")


if __name__ == "__main__":
    unittest.main()
