import os
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "needs a display (CI runs the tests under xvfb)")
class EveryPageDraws(unittest.TestCase):
    """Smoke test, not a visual comparison: every page of the main window must build and draw without an error."""

    def test_every_page_opens_and_draws(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        try:
            import cairo
        except ImportError:
            self.skipTest("pycairo is not installed")
        from mintcare import gui
        app = gui.MintCareApp()
        app.do_activate()
        try:
            win = Gtk.Window.list_toplevels()[0]
            win.show_all()
            for key, _title, _icon in gui.NAV:
                with self.subTest(page=key):
                    win.goto(key)
                    while Gtk.events_pending():
                        Gtk.main_iteration_do(False)
                    self.assertEqual(win.stack.get_visible_child_name(), key)
                    a = win.get_allocation()
                    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(a.width, 1), max(a.height, 1))
                    win.draw(cairo.Context(surf))
        finally:
            for w in Gtk.Window.list_toplevels():
                w.destroy()


if __name__ == "__main__":
    unittest.main()
