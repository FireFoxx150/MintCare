"""Run one GUI check in its own process (see test_accessibility and test_pages_render).

GTK does not survive several windows being built and destroyed in one test process, so each check
runs here instead and exits with os._exit() to skip the teardown. Usage: gui_probe.py a11y|pages
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402
from mintcare import gui, hardware  # noqa: E402


def _text(w):
    if isinstance(w, Gtk.Label) and w.get_text().strip():
        yield w.get_text()
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            yield from _text(c)


def _has_name(w):
    if w.get_accessible().get_name() or w.get_tooltip_text():
        return True
    if isinstance(w, Gtk.Button):
        return bool(w.get_label() or any(_text(w)))
    if isinstance(w, Gtk.Entry):
        return bool(w.get_placeholder_text())
    return False


KINDS = (Gtk.Button, Gtk.ComboBoxText, Gtk.SpinButton, Gtk.Entry, Gtk.SearchEntry, Gtk.Switch, Gtk.Scale)


def _unnamed(w, out):
    if isinstance(w, KINDS) and not isinstance(w, Gtk.CheckButton) and not _has_name(w):
        out.append(type(w).__name__)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            _unnamed(c, out)
    return out


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def main(mode):
    import cairo
    with mock.patch.object(hardware, "gpu_load", return_value=None):   # no graphics card must still lay out
        app = gui.MintCareApp()
        app.do_activate()
        win = Gtk.Window.list_toplevels()[0]
        win.show_all()
        problems = []
        for key, _title, _icon in gui.NAV:
            win.goto(key)
            pump()
            if mode == "a11y":
                for kind in _unnamed(win, []):
                    problems.append(f"{key}: unnamed {kind}")
            else:
                if win.stack.get_visible_child_name() != key:
                    problems.append(f"{key}: did not open")
                a = win.get_allocation()
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(a.width, 1), max(a.height, 1))
                win.draw(cairo.Context(surf))
        print("PROBLEMS:" + ";".join(problems) if problems else "CLEAN", flush=True)
    os._exit(1 if problems else 0)


if __name__ == "__main__":
    main(sys.argv[1])
