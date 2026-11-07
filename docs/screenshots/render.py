#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README screenshots with the computer name replaced by "mint-pc".

Run from the project root, isolated from your own settings and any running MintCare:
  XDG_CONFIG_HOME=$(mktemp -d) XDG_STATE_HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d) LANGUAGE=en \\
    dbus-run-session -- python3 docs/screenshots/render.py docs/screenshots dashboard,diagnose,optimise,clean
Check every image for personal details before committing (the Hardware page shows drive serials)."""
import os, socket, sys
socket.gethostname = lambda: "mint-pc"          # before MintCare reads it
sys.path.insert(0, os.getcwd())
import gi; gi.require_version("Gtk", "3.0")
import cairo
from gi.repository import GLib
from mintcare import checks, gui
_read = checks.read
checks.read = lambda path, *a, **k: _read(path, *a, **k) + ("\n127.0.1.1 mint-pc\n" if path == "/etc/hosts" else "")
app = gui.MintCareApp()
pages = sys.argv[2].split(",")
def step(i=[0]):
    w = app.get_windows()[0]
    if i[0]:
        a = w.get_allocation(); surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, a.width, a.height)
        w.draw(cairo.Context(surf)); surf.write_to_png(f"{sys.argv[1]}/{pages[i[0]-1]}.png")
    if i[0] < len(pages):
        w.goto(pages[i[0]]); i[0] += 1; return True
    app.quit(); return False
GLib.timeout_add(9000, lambda: GLib.timeout_add(5000, step) and False)
app.run(None)
