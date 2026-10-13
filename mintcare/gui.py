"""GTK 3 interface, styled after MintCare Companion (dark, emerald accent, live gauges)."""

import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Gdk, Pango  # noqa: E402

from . import (__version__, autostart, boot, checks, cleaner, flatpaks, hardware,  # noqa: E402
               maintenance, space, theme, tweaks, widgets)
from .runner import run_steps  # noqa: E402
from .stats import Sampler  # noqa: E402
from .util import human, q, read, sysctl  # noqa: E402
from .widgets import BLUE, EMERALD, ORANGE, VIOLET, Bar, LineChart, RingGauge, StackedBar, Treemap  # noqa: E402

STATUS_ICON = {
    checks.OK: ("emblem-ok-symbolic", "ok"),
    checks.INFO: ("dialog-information-symbolic", "info"),
    checks.WARN: ("dialog-warning-symbolic", "warn"),
    checks.FAIL: ("dialog-error-symbolic", "fail"),
}
STATUS_WORD = {checks.OK: "OK", checks.INFO: "TIP", checks.WARN: "WARN", checks.FAIL: "FAIL"}

NAV = [
    ("dashboard", "Dashboard", "view-grid-symbolic"),
    ("diagnose", "Diagnose & Fix", "system-search-symbolic"),
    ("optimise", "Optimise", "power-profile-performance-symbolic"),
    ("clean", "Clean", "user-trash-symbolic"),
    ("space", "Disk space", "drive-multidisk-symbolic"),
    ("flatpak", "Flatpak apps", "system-software-install-symbolic"),
    ("hardware", "Hardware", "drive-harddisk-symbolic"),
    ("startup", "Boot & Startup", "system-run-symbolic"),
    ("maintenance", "Maintenance", "alarm-symbolic"),
    ("log", "Activity log", "utilities-terminal-symbolic"),
]

# Pages and tweaks shown at each experience level (advanced shows everything).
LEVELS = [
    ("beginner", "Beginner", "Essentials only: health check, recommended tweaks, cleaning."),
    ("intermediate", "Intermediate", "Everything except the most technical tweaks."),
    ("advanced", "Advanced", "Every page and every tweak."),
]
BEGINNER_PAGES = {"dashboard", "diagnose", "optimise", "clean", "space", "maintenance", "log"}
ADVANCED_TWEAKS = {"dns", "swappiness", "dirty", "waitonline", "iosched", "aspm"}



# ---------------------------------------------------------------- helpers

def css(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


class SpacedLabel(Gtk.Label):
    """Letter-spaced caption. GTK 3 mis-measures letter-spaced text containing spaces
    (it reserves a second line), so spaces are swapped for non-breaking ones."""

    def set_text(self, text):
        super().set_text(text.replace(" ", "\u00a0"))


def label(text="", classes=(), xalign=0.0, wrap=False, markup=False, selectable=False, ellipsize=False):
    spaced = "kicker" in classes or "app-sub" in classes
    lb = (SpacedLabel if spaced else Gtk.Label)(xalign=xalign)
    lb.set_markup(text) if markup else lb.set_text(text)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    lb.set_selectable(selectable)
    if spaced:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(1 * Pango.SCALE))
        lb.set_attributes(attrs)
    return css(lb, *classes)


def pill(text, kind="dim"):
    lb = label(text.upper(), ("pill", kind), xalign=0.5)
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def set_pill(lb, text, kind):
    ctx = lb.get_style_context()
    for k in ("ok", "info", "warn", "fail", "dim", "blue", "orange"):
        ctx.remove_class(k)
    ctx.add_class(kind)
    lb.set_text(text.upper())


def vbox(spacing=0, *classes):
    return css(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing), *classes)


def hbox(spacing=0, *classes):
    return css(Gtk.Box(spacing=spacing), *classes)


def icon(name, size=Gtk.IconSize.BUTTON, *classes):
    return css(Gtk.Image.new_from_icon_name(name, size), *classes)


def card(*classes):
    return vbox(0, "card", *classes)


def list_card():
    c = card("tight")
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.set_header_func(lambda row, before: row.set_header(Gtk.Separator()) if before else None)
    c.pack_start(lb, False, False, 0)
    return c, lb


def scrolled(child):
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.add(child)
    return sw


def fmt_uptime(sec):
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


def rate(v):
    return human(v) + "/s"


# ---------------------------------------------------------------- window

class MintCareWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MintCare")
        css(self, "mintcare")
        self.set_default_size(1240, 820)
        self.set_size_request(720, 560)
        self._layout_mode = None
        self._responsive = []          # (grid, {mode: [(widget, left, top, width, height)]})
        self.set_icon_name("mintcare")
        self.busy = False
        self.check_results = {}
        self._action_buttons = []
        self._queue = []
        self._diag_buttons = []
        self._tweak_initial = {}
        self.sampler = Sampler()
        self._hw_loaded = self._boot_loaded = self._sensor_busy = self._fp_loaded = False
        self._fp_open = set()
        self._space_loaded = False
        self._space_cancel = threading.Event()

        self._gtk_settings = Gtk.Settings.get_default()
        self._system_dark = theme.system_is_dark(self._gtk_settings)
        self.css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.prefs = maintenance.load_settings()
        self.apply_theme(self.prefs.get("theme", "dark"))
        self._gtk_settings.connect("notify::gtk-theme-name", self._on_system_theme)

        self.set_titlebar(self._build_header())

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=160)
        self.stack.add_named(self._build_dashboard(), "dashboard")
        self.stack.add_named(self._build_diagnose(), "diagnose")
        self.stack.add_named(self._build_optimise(), "optimise")
        self.stack.add_named(self._build_clean(), "clean")
        self.stack.add_named(self._build_space(), "space")
        self.stack.add_named(self._build_flatpak(), "flatpak")
        self.stack.add_named(self._build_hardware(), "hardware")
        self.stack.add_named(self._build_startup(), "startup")
        self.stack.add_named(self._build_maintenance(), "maintenance")
        self.stack.add_named(self._build_log(), "log")

        body = hbox(0)
        body.pack_start(self._build_sidebar(), False, False, 0)
        body.pack_start(self.stack, True, True, 0)
        root = vbox(0, "main")
        root.pack_start(body, True, True, 0)
        root.pack_start(self._build_footer(), False, False, 0)
        self.add(root)

        self.show_all()
        self.connect("size-allocate", self._on_size)
        accel = Gtk.AccelGroup()
        accel.connect(Gdk.keyval_from_name("f"), Gdk.ModifierType.CONTROL_MASK, 0,
                      lambda *_: self.search.grab_focus() or True)
        self.add_accel_group(accel)
        self._apply_layout("wide")
        self.apply_level(self.prefs.get("level", "intermediate"))
        self.goto(os.environ.get("MINTCARE_PAGE", "dashboard"))
        self._tick()
        GLib.timeout_add(1000, self._tick)
        GLib.timeout_add(2000, self._poll_sensors)
        self.stack.connect("notify::visible-child-name", self._on_page)
        self._on_page(self.stack, None)
        GLib.idle_add(self.run_diagnose)
        GLib.idle_add(self.run_clean_scan)

    # ------------------------------------------------------------ chrome

    def _build_header(self):
        hb = Gtk.HeaderBar(show_close_button=True)
        brand = hbox(12)
        logo = label("M", ("logo",), xalign=0.5)
        logo.set_valign(Gtk.Align.CENTER)
        brand.pack_start(logo, False, False, 0)
        titles = vbox(1)
        titles.set_valign(Gtk.Align.CENTER)
        titles.pack_start(label("MintCare", ("app-title",)), False, False, 0)
        titles.pack_start(label("LINUX MINT CLEAN · OPTIMISE · REPAIR", ("app-sub",)), False, False, 0)
        brand.pack_start(titles, False, False, 0)
        hb.pack_start(brand)
        self.search = Gtk.SearchEntry(placeholder_text="Search  (Ctrl+F)", width_chars=24, valign=Gtk.Align.CENTER)
        self.search.set_margin_start(18)
        self.search.connect("search-changed", self._on_search)
        self.search.connect("activate", self._search_activate_first)
        self.search.connect("key-press-event", self._search_key)
        hb.pack_start(self.search)
        self.search_pop = Gtk.Popover(relative_to=self.search, position=Gtk.PositionType.BOTTOM)
        self.search_pop.set_modal(False)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, max_content_height=440,
                                propagate_natural_height=True)
        sw.set_size_request(460, -1)
        self.search_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.search_list.connect("row-activated", lambda _l, r: self._search_go(r.target))
        sw.add(self.search_list)
        self.search_pop.add(sw)
        sw.show_all()

        chips = hbox(16)
        self.chip_boxes = {}
        osr = dict(l.split("=", 1) for l in read("/etc/os-release").splitlines() if "=" in l)
        self.chip_vals = {}
        for i, (key, val) in enumerate([
                ("HOSTNAME", socket.gethostname()),
                ("SYSTEM", osr.get("PRETTY_NAME", "Linux").strip('"')),
                ("KERNEL", platform.release()),
                ("TCP", sysctl("net.ipv4.tcp_congestion_control").upper()),
                ("CPU TEMP", "–")]):
            if i:
                sep = css(Gtk.Box(), "vsep")
                sep.set_size_request(1, 24)
                sep.set_valign(Gtk.Align.CENTER)
                chips.pack_start(sep, False, False, 0)
                self.chip_boxes.setdefault(key + "-sep", sep)
            c = vbox(1)
            c.set_valign(Gtk.Align.CENTER)
            c.pack_start(label(key, ("kicker",)), False, False, 0)
            v = label(val, ("chip-val",))
            self.chip_vals[key] = v
            c.pack_start(v, False, False, 0)
            chips.pack_start(c, False, False, 0)
            self.chip_boxes[key] = c
        chip_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER)
        chip_scroller.set_propagate_natural_width(True)
        chip_scroller.set_propagate_natural_height(True)
        chip_scroller.add(chips)
        self.header_chips = chip_scroller
        menu_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        menu_btn.add(icon("open-menu-symbolic"))
        menu_btn.set_tooltip_text("Appearance, experience level, tray and report")
        css(menu_btn, "ghost")
        menu_btn.set_popover(self._build_app_menu(menu_btn))
        hb.pack_end(menu_btn)
        hb.pack_end(chip_scroller)
        return hb

    def _build_app_menu(self, relative):
        pop = Gtk.Popover(relative_to=relative)
        box = vbox(4)
        box.set_border_width(14)
        box.pack_start(label("APPEARANCE", ("kicker",)), False, False, 0)
        first = None
        self.theme_radios = {}
        for key, title in theme.NAMES:
            rb = Gtk.RadioButton.new_with_label_from_widget(first, title)
            first = first or rb
            rb.connect("toggled", lambda w, k=key: w.get_active() and self._set_pref("theme", k))
            self.theme_radios[key] = rb
            box.pack_start(rb, False, False, 0)
        box.pack_start(label("EXPERIENCE LEVEL", ("kicker",)), False, False, 8)
        first = None
        self.level_radios = {}
        for key, title, desc in LEVELS:
            rb = Gtk.RadioButton.new_from_widget(first)
            first = first or rb
            t = vbox(0)
            t.pack_start(label(title, ("h3",)), False, False, 0)
            t.pack_start(label(desc, ("faint",)), False, False, 0)
            rb.add(t)
            rb.connect("toggled", lambda w, k=key: w.get_active() and self._set_pref("level", k))
            self.level_radios[key] = rb
            box.pack_start(rb, False, False, 0)
        box.pack_start(label("SYSTEM TRAY", ("kicker",)), False, False, 8)
        self.tray_check = Gtk.CheckButton(label="Show MintCare in the system tray")
        self.tray_check.connect("toggled", lambda w: self._set_pref("tray", w.get_active()))
        box.pack_start(self.tray_check, False, False, 0)
        self.tray_login_check = Gtk.CheckButton(label="Start in the tray when I log in")
        self.tray_login_check.connect("toggled", lambda w: self._set_pref("tray_autostart", w.get_active()))
        box.pack_start(self.tray_login_check, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 8)
        exp = Gtk.ModelButton(text="Export health report…")
        exp.connect("clicked", lambda *_: self.export_report())
        box.pack_start(exp, False, False, 0)
        about = Gtk.ModelButton(text="About MintCare")
        about.connect("clicked", lambda *_: self.show_about())
        box.pack_start(about, False, False, 0)
        box.show_all()
        pop.add(box)
        self._menu_loading = True
        self.theme_radios.get(self.prefs.get("theme", "dark"), self.theme_radios["dark"]).set_active(True)
        self.level_radios.get(self.prefs.get("level", "intermediate"),
                              self.level_radios["intermediate"]).set_active(True)
        self.tray_check.set_active(self.prefs.get("tray", False))
        self.tray_login_check.set_active(self.prefs.get("tray_autostart", False))
        self.tray_login_check.set_sensitive(self.prefs.get("tray", False))
        self._menu_loading = False
        return pop

    def _set_pref(self, key, value):
        if getattr(self, "_menu_loading", False):
            return
        st = maintenance.load_settings()
        st[key] = value
        maintenance.save_settings(st)
        self.prefs = st
        if key == "theme":
            self.apply_theme(value)
        elif key == "level":
            self.apply_level(value)
            self.set_status(f"{value.capitalize()} mode")
        elif key == "tray":
            self.tray_login_check.set_sensitive(value)
            self.set_tray(value)
        elif key == "tray_autostart":
            self.set_tray_autostart(value)

    # ------------------------------------------------------------ search

    def _search_index(self):
        lvl = getattr(self, "level", "advanced")
        items = []
        for key, title, ico in NAV:
            if self.nav_rows[key].get_visible():
                items.append(("page", title, "Go to page", ico, (key, None), title))
        for c in checks.CHECKS:
            r = self.check_results.get(c.id)
            items.append(("check", c.title, r.summary if r else c.category, "system-search-symbolic",
                          ("diagnose", ("check", c.id)), f"{c.category} {c.id}"))
        for t in tweaks.TWEAKS:
            if self.tweak_rows[t.id].get_visible():
                items.append(("tweak", t.title, t.description, "power-profile-performance-symbolic",
                              ("optimise", ("tweak", t.id)), f"{t.category} {t.id}"))
        for p in tweaks.PROFILES:
            items.append(("profile", f"{p.title} profile", p.description, p.icon,
                          ("optimise", ("profile", p.id)), "profile preset"))
        for i in cleaner.ITEMS:
            if i.id in getattr(self, "clean_rows", {}):
                items.append(("clean", i.title, i.description, "user-trash-symbolic",
                              ("clean", ("clean", i.id)), "clean junk cache"))
        for key, title in theme.NAMES:
            items.append(("setting", f"{title} theme", "Change the appearance", "weather-clear-symbolic",
                          (None, ("theme", key)), "theme appearance colour color dark light contrast"))
        for key, title, desc in LEVELS:
            items.append(("setting", f"{title} mode", desc, "preferences-system-symbolic",
                          (None, ("level", key)), "level experience mode"))
        return items

    def _on_search(self, entry):
        q_ = entry.get_text().strip().lower()
        for ch in self.search_list.get_children():
            ch.destroy()
        if len(q_) < 2:
            self.search_pop.popdown()
            return
        words = q_.split()
        scored = []
        for kind, title, sub, ico, target, kw in self._search_index():
            hay = f"{title} {sub} {kw}".lower()
            if not all(w in hay for w in words):
                continue
            tl = title.lower()
            score = (0 if tl.startswith(q_) else 1 if q_ in tl else 2 if all(w in tl for w in words) else 3)
            scored.append((score, kind, title, sub, ico, target))
        scored.sort(key=lambda x: (x[0], x[2].lower()))
        if not scored:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(label(f"Nothing matches “{entry.get_text().strip()}”.", ("muted",)))
            row.target = None
            self.search_list.add(row)
        for _sc, kind, title, sub, ico, target in scored[:14]:
            row = Gtk.ListBoxRow()
            row.target = target
            b = hbox(10)
            b.set_border_width(6)
            b.pack_start(icon(ico, Gtk.IconSize.BUTTON, "ok"), False, False, 0)
            t = vbox(1)
            t.pack_start(label(title, ("h3",), ellipsize=True), False, False, 0)
            t.pack_start(label(sub, ("faint",), ellipsize=True), False, False, 0)
            b.pack_start(t, True, True, 0)
            b.pack_end(pill(kind, "dim"), False, False, 0)
            row.add(b)
            self.search_list.add(row)
        self.search_list.show_all()
        self.search_pop.popup()

    def _search_key(self, entry, ev):
        if ev.keyval == Gdk.KEY_Escape:
            entry.set_text("")
            self.search_pop.popdown()
            return True
        if ev.keyval == Gdk.KEY_Down and self.search_pop.get_visible():
            first = self.search_list.get_row_at_index(0)
            if first:
                self.search_list.select_row(first)
                first.grab_focus()
            return True
        return False

    def _search_activate_first(self, _entry):
        row = self.search_list.get_row_at_index(0)
        if row is not None and getattr(row, "target", None):
            self._search_go(row.target)

    def _search_go(self, target):
        if not target:
            return
        self.search_pop.popdown()
        page, what = target
        self.search.set_text("")
        if what and what[0] == "theme":
            self.theme_radios[what[1]].set_active(True)
            return
        if what and what[0] == "level":
            self.level_radios[what[1]].set_active(True)
            return
        if page:
            self.goto(page)
        if not what:
            return
        kind, key = what
        row = {"check": getattr(self, "check_rows", {}), "tweak": self.tweak_rows,
               "clean": getattr(self, "clean_rows", {})}.get(kind, {}).get(key)
        if kind == "profile":
            row = self.profile_tiles.get(key) if hasattr(self, "profile_tiles") else None
        if row is not None:
            GLib.timeout_add(220, self._scroll_to, row)

    def _scroll_to(self, widget):
        sw = widget.get_ancestor(Gtk.ScrolledWindow)
        if sw is not None and sw.get_child() is not None:
            content = sw.get_child().get_child() if isinstance(sw.get_child(), Gtk.Viewport) else sw.get_child()
            coords = widget.translate_coordinates(content, 0, 0)
            if coords:
                adj = sw.get_vadjustment()
                adj.set_value(max(0, min(coords[1] - 90, adj.get_upper() - adj.get_page_size())))
        css(widget, "flash")
        GLib.timeout_add(1600, lambda: widget.get_style_context().remove_class("flash") or False)
        return False

    # ------------------------------------------------------------ responsive layout

    def _on_size(self, _w, alloc):
        full_chips = alloc.width >= 1350
        if full_chips != getattr(self, "_full_chips", None):
            self._full_chips = full_chips
            GLib.idle_add(self._apply_chips)
        mode = "wide" if alloc.width >= 1180 else "medium" if alloc.width >= 900 else "narrow"
        if mode != self._layout_mode:
            self._layout_mode = mode
            GLib.idle_add(self._apply_layout, mode)

    def _apply_layout(self, mode):
        for grid, layouts in self._responsive:
            spec = layouts[mode]
            for wdg, *_pos in spec:
                if wdg.get_parent() is grid:
                    grid.remove(wdg)
            for wdg, left, top, w, h in spec:
                grid.attach(wdg, left, top, w, h)
            grid.set_column_homogeneous(True)
            grid.show_all()
        compact = mode == "narrow"
        for lbl in self.nav_labels:
            lbl.set_visible(not compact)
        self.nav_kicker.set_visible(not compact)
        self.side_foot.set_visible(not compact)
        self.sidebar.set_size_request(64 if compact else 220, -1)
        self._apply_chips()
        self.header_chips.set_visible(mode != "narrow")
        self.search.set_width_chars(14 if compact else 24)
        return False

    def _apply_chips(self):
        full = getattr(self, "_full_chips", True)
        for key in ("SYSTEM", "SYSTEM-sep", "KERNEL", "KERNEL-sep"):     # separators sit before their chip
            if key in self.chip_boxes:
                self.chip_boxes[key].set_visible(full)
        return False

    # ------------------------------------------------------------ theme & level

    def apply_theme(self, name):
        eff = theme.resolve(name, self._system_dark)
        self.css_provider.load_from_data(theme.css(eff))
        self._gtk_settings.set_property("gtk-application-prefer-dark-theme", theme.DARK_BASE[eff])
        pal = theme.PALETTES[eff]
        widgets.set_theme(pal["ink"], pal["slate1"], pal["slate5"])
        self._theme_name = name
        self.queue_draw()

    def _on_system_theme(self, settings, _pspec):
        name = (settings.get_property("gtk-theme-name") or "").lower()
        self._system_dark = "dark" in name
        if self._theme_name == "system":
            self.apply_theme("system")

    def apply_level(self, level):
        self.level = level
        for key, row in self.nav_rows.items():
            row.set_visible(level != "beginner" or key in BEGINNER_PAGES)

        def shown(t):
            if level == "beginner":
                return t.recommended
            if level == "intermediate":
                return t.id not in ADVANCED_TWEAKS
            return True
        for cat, kicker, cardw, ids in getattr(self, "opt_sections", []):
            vis = [i for i in ids if shown(tweaks.BY_ID[i])]
            for i in ids:
                self.tweak_rows[i].set_visible(i in vis)
            kicker.set_visible(bool(vis))
            cardw.set_visible(bool(vis))
        if hasattr(self, "level_pill"):
            set_pill(self.level_pill, f"{level} mode", "dim")
        cur = self.stack.get_visible_child_name()
        if not self.nav_rows[cur].get_visible():
            self.goto("dashboard")

    def _build_sidebar(self):
        side = vbox(4, "sidebar")
        side.set_size_request(220, -1)
        self.sidebar = side
        self.nav_kicker = label("NAVIGATION", ("kicker",))
        side.pack_start(self.nav_kicker, False, False, 6)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav_rows, self.nav_badges = {}, {}
        for key, title, icon_name in NAV:
            row = Gtk.ListBoxRow()
            row.page = key
            b = hbox(10)
            b.pack_start(icon(icon_name), False, False, 0)
            nl = label(title, ("nav-label",))
            self.nav_labels = getattr(self, "nav_labels", [])
            self.nav_labels.append(nl)
            row.set_tooltip_text(title)
            b.pack_start(nl, True, True, 0)
            badge = label("", ("nav-badge",))
            badge.set_no_show_all(True)
            self.nav_badges[key] = badge
            b.pack_end(badge, False, False, 0)
            row.add(b)
            self.nav.add(row)
            self.nav_rows[key] = row
        self.nav.connect("row-selected", lambda _lb, row: row and self.stack.set_visible_child_name(row.page))
        side.pack_start(self.nav, False, False, 0)

        foot = vbox(6, "side-foot")
        foot.pack_start(label("SYSTEM HEALTH", ("kicker",)), False, False, 0)
        row = hbox(8)
        self.side_score = label("–", ("big",))
        row.pack_start(self.side_score, False, False, 0)
        self.side_pill = pill("scanning", "dim")
        row.pack_end(self.side_pill, False, False, 0)
        foot.pack_start(row, False, False, 0)
        self.side_bar = Bar(EMERALD)
        foot.pack_start(self.side_bar, False, False, 0)
        live = hbox(6)
        live.pack_start(label("●", ("pill", "ok")), False, False, 0)
        live.pack_start(label("Live monitoring", ("faint",)), False, False, 0)
        foot.pack_start(live, False, False, 4)
        self.level_pill = pill("intermediate mode", "dim")
        self.level_pill.set_halign(Gtk.Align.START)
        css(self.level_pill, "level-pill")
        foot.pack_start(self.level_pill, False, False, 0)
        side.pack_end(foot, False, False, 0)
        self.side_foot = foot
        return side

    def _build_footer(self):
        f = hbox(10, "footer")
        f.pack_start(label(f"MintCare v{__version__}  •  {len(checks.CHECKS)} checks  •  "
                           f"{len(tweaks.TWEAKS)} tweaks  •  {len(cleaner.ITEMS)} cleaners"), False, False, 0)
        self.spinner = Gtk.Spinner()
        self.status_lbl = label("Ready", ("status",))
        f.pack_end(self.status_lbl, False, False, 0)
        f.pack_end(self.spinner, False, False, 0)
        return f

    def goto(self, page):
        row = self.nav_rows.get(page)
        if row:
            self.nav.select_row(row)

    # ------------------------------------------------------------ plumbing

    def action_button(self, text, cb, primary=False, icon_name=None):
        b = Gtk.Button()
        inner = hbox(6)
        if icon_name:
            inner.pack_start(icon(icon_name), False, False, 0)
        inner.pack_start(Gtk.Label(label=text), False, False, 0)
        b.add(inner)
        css(b, "primary" if primary else "ghost")
        b.set_valign(Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: cb())
        self._action_buttons.append(b)
        return b

    def set_status(self, text, bad=False):
        self.status_lbl.set_text(text)
        ctx = self.status_lbl.get_style_context()
        (ctx.add_class if bad else ctx.remove_class)("bad")

    def set_busy(self, busy, text=""):
        self.busy = busy
        self.set_status(text or ("Working…" if busy else "Ready"))
        (self.spinner.start if busy else self.spinner.stop)()
        for b in self._action_buttons:
            b.set_sensitive(not busy)

    def in_thread(self, text, work, done=None):
        """Run work() off the UI thread, then done(result) on it. Jobs queue up."""
        if self.busy:
            self._queue.append((text, work, done))
            return
        self.set_busy(True, text)

        def target():
            try:
                res = work()
            except Exception as e:  # surface, don't crash the UI
                self.log(f"Error: {e}")
                res = None
            GLib.idle_add(finish, res)

        def finish(res):
            self.set_busy(False)
            if done:
                done(res)
            if self._queue and not self.busy:
                self.in_thread(*self._queue.pop(0))
            return False

        threading.Thread(target=target, daemon=True).start()

    def bg(self, work, done):
        """Quiet background job (no busy state): for page loads and sensor polling."""
        def target():
            try:
                res = work()
            except Exception as e:
                self.log(f"Error: {e}")
                res = None
            GLib.idle_add(lambda: done(res) and False)
        threading.Thread(target=target, daemon=True).start()

    def _on_page(self, stack, _pspec):
        name = stack.get_visible_child_name()
        if name == "maintenance":
            self.refresh_maintenance()
        if name == "hardware" and not self._hw_loaded:
            self._hw_loaded = True
            self.load_drives()
            self._poll_sensors(force=True)
        elif name == "space" and not self._space_loaded:
            self._space_loaded = True
            self.scan_folders(space.HOME)
            self.load_snapshots()
        elif name == "flatpak" and not self._fp_loaded:
            self._fp_loaded = True
            self.load_flatpaks(check_updates=True)
        elif name == "startup" and not self._boot_loaded:
            self._boot_loaded = True
            self.load_boot()

    def log(self, text):
        def append():
            buf = self.log_buf
            buf.insert(buf.get_end_iter(), text + "\n")
            self.log_view.scroll_to_mark(buf.get_insert(), 0, False, 0, 0)
            buf.place_cursor(buf.get_end_iter())
            return False
        GLib.idle_add(append)

    def execute(self, root_steps, user_steps, heading, snapshot=()):
        """Run privileged + user steps in a worker, logging everything and recording history.

        snapshot: optional Timeshift step(s) from ask_snapshot(), run first as root.
        """
        self.log(f"\n━━━ {heading} ━━━")
        results = {}
        root_steps = list(snapshot) + list(root_steps)
        if user_steps:
            results.update(run_steps(user_steps, root=False, on_line=self.log))
        if root_steps:
            self.log("Requesting administrator access…")
            results.update(run_steps(root_steps, root=True, on_line=self.log))
        ok = sum(1 for rc in results.values() if rc == 0)
        self.log(f"Finished: {ok}/{len(results)} succeeded.")
        if heading != "SMART report":
            kind = maintenance.KIND_FOR_HEADING.get(heading, "other")
            for sid, title, _body in list(user_steps) + root_steps:
                if sid in results:
                    maintenance.record("snapshot" if sid == "snapshot" else kind, title, results[sid] == 0)
        GLib.idle_add(self._maint_if_visible)
        return results

    def ask_snapshot(self, reason):
        """Decide (on the UI thread) whether to take a Timeshift snapshot first.

        Returns a list of steps to pass as execute(snapshot=...), or None if the user cancelled.
        """
        pref = maintenance.load_settings().get("snapshot_before", "ask")
        if pref == "never":
            return []
        ready, _why = maintenance.timeshift_ready()
        if not ready:
            return []
        if maintenance.recent_snapshot(30):
            self.log("A Timeshift snapshot was taken in the last 30 minutes; not taking another.")
            return []
        if pref == "always":
            return [maintenance.snapshot_step(reason)]
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text="Take a Timeshift snapshot first?")
        css(d, "mintcare")
        d.format_secondary_text(f"You're about to apply {reason}. A snapshot lets you roll the whole "
                                "system back if anything goes wrong. It usually takes a minute or two.")
        remember = Gtk.CheckButton(label="Remember my choice")
        remember.set_margin_start(12)
        d.get_message_area().pack_start(remember, False, False, 6)
        remember.show()
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Continue without", Gtk.ResponseType.NO,
                      "Snapshot first", Gtk.ResponseType.YES)
        d.set_default_response(Gtk.ResponseType.YES)
        css(d.get_widget_for_response(Gtk.ResponseType.YES), "primary")
        resp = d.run()
        keep = remember.get_active()
        d.destroy()
        if resp not in (Gtk.ResponseType.YES, Gtk.ResponseType.NO):
            return None
        if keep:
            st = maintenance.load_settings()
            st["snapshot_before"] = "always" if resp == Gtk.ResponseType.YES else "never"
            maintenance.save_settings(st)
            self.refresh_maintenance()
        return [maintenance.snapshot_step(reason)] if resp == Gtk.ResponseType.YES else []

    def confirm(self, title, body):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=title)
        css(d, "mintcare")
        d.format_secondary_markup(body)
        resp = d.run()
        d.destroy()
        return resp == Gtk.ResponseType.OK

    def notify_done(self, results, what):
        failed = [k for k, rc in results.items() if rc != 0]
        if failed:
            self.set_status(f"{len(failed)} step(s) failed — see Activity log", bad=True)
        else:
            self.set_status(f"{what} complete")

    def page_header(self, icon_name, title, subtitle, buttons=()):
        c = card("ecard")
        row = hbox(12)
        row.pack_start(icon(icon_name, Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        t.set_valign(Gtk.Align.CENTER)
        t.pack_start(label(title, ("h1",)), False, False, 0)
        sub = label(subtitle, ("muted",), wrap=True)
        t.pack_start(sub, False, False, 0)
        row.pack_start(t, True, True, 0)
        for b in reversed(buttons):
            row.pack_end(b, False, False, 0)
        c.pack_start(row, False, False, 0)
        c.subtitle = sub
        return c

    # ------------------------------------------------------------ tray, report, about

    def set_tray(self, on):
        pass

    def set_tray_autostart(self, on):
        pass

    def export_report(self):
        d = Gtk.FileChooserDialog(title="Export health report", transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE)
        css(d, "mintcare")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Export", Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        d.set_current_folder(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or space.HOME)
        stamp = time.strftime("%Y-%m-%d")
        d.set_current_name(f"MintCare-report-{stamp}.pdf")
        extra = vbox(6)
        fmt = hbox(12)
        fmt.pack_start(label("Format:", ("muted",)), False, False, 0)
        pdf_rb = Gtk.RadioButton.new_with_label_from_widget(None, "PDF")
        html_rb = Gtk.RadioButton.new_with_label_from_widget(pdf_rb, "Web page (HTML)")

        def set_ext(_w):
            name = d.get_current_name() or f"MintCare-report-{stamp}"
            base = name.rsplit(".", 1)[0] if name.lower().endswith((".pdf", ".html")) else name
            d.set_current_name(base + (".pdf" if pdf_rb.get_active() else ".html"))
        pdf_rb.connect("toggled", set_ext)
        fmt.pack_start(pdf_rb, False, False, 0)
        fmt.pack_start(html_rb, False, False, 0)
        extra.pack_start(fmt, False, False, 0)
        priv = Gtk.CheckButton(label="Leave out personal details (computer name, user name, drive serial numbers)",
                               active=True)
        extra.pack_start(priv, False, False, 0)
        extra.show_all()
        d.set_extra_widget(extra)
        resp, path, as_pdf, private = d.run(), d.get_filename(), pdf_rb.get_active(), priv.get_active()
        d.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        if as_pdf and not path.lower().endswith(".pdf"):
            path += ".pdf"
        if not as_pdf and not path.lower().endswith((".html", ".htm")):
            path += ".html"
        from . import report

        def work():
            data = report.collect(private, progress=lambda m: GLib.idle_add(self.set_status, m))
            return report.render_html(data)

        def done(page):
            if not page:
                self.set_status("Could not build the report", bad=True)
                return
            if as_pdf:
                self._print_pdf(page, path)
            else:
                Path(path).write_text(page)
                self._report_saved(path)
        self.in_thread("Building health report…", work, done)

    def _print_pdf(self, page, path):
        try:
            gi.require_version("WebKit2", "4.1")
            from gi.repository import WebKit2
        except (ValueError, ImportError):
            alt = path[:-4] + ".html"
            Path(alt).write_text(page)
            self.log("WebKit is not available for PDF output; saved the report as HTML instead.")
            self._report_saved(alt)
            return
        win = Gtk.OffscreenWindow()
        view = WebKit2.WebView()
        win.add(view)
        win.set_default_size(980, 1300)
        win.show_all()
        self._pdf_keep = win               # keep alive until printing finishes

        def finished(*_a):
            self._pdf_keep = None
            win.destroy()
            self._report_saved(path)

        def failed(_op, err):
            self._pdf_keep = None
            win.destroy()
            self.log(f"PDF export failed: {err.message if hasattr(err, 'message') else err}")
            self.set_status("PDF export failed", bad=True)

        def loaded(v, ev):
            if ev != WebKit2.LoadEvent.FINISHED:
                return
            op = WebKit2.PrintOperation.new(v)
            ps = Gtk.PrintSettings()
            ps.set_printer("Print to File")
            ps.set(Gtk.PRINT_SETTINGS_OUTPUT_FILE_FORMAT, "pdf")
            ps.set(Gtk.PRINT_SETTINGS_OUTPUT_URI, Gio.File.new_for_path(path).get_uri())
            op.set_print_settings(ps)
            setup = Gtk.PageSetup()
            setup.set_paper_size(Gtk.PaperSize.new(Gtk.PAPER_NAME_A4))
            op.set_page_setup(setup)
            op.connect("finished", finished)
            op.connect("failed", failed)
            op.print_()
        view.connect("load-changed", loaded)
        view.load_html(page, "file:///")

    def _report_saved(self, path):
        maintenance.record("other", f"Exported health report ({os.path.basename(path)})")
        self.log(f"Health report saved: {path}")
        self.set_status(f"Report saved: {os.path.basename(path)}")
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    def show_about(self):
        d = Gtk.AboutDialog(transient_for=self, modal=True, program_name="MintCare", version=__version__,
                            comments="Clean, optimise and repair Linux Mint.", logo_icon_name="mintcare")
        css(d, "mintcare")
        d.run()
        d.destroy()

    # ------------------------------------------------------------ dashboard

    def _build_dashboard(self):
        page = vbox(12, "page")

        # upper bar
        top = card("ecard")
        row = hbox(12)
        row.pack_start(icon("computer-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        t.set_valign(Gtk.Align.CENTER)
        t.pack_start(label(socket.gethostname(), ("h1",)), False, False, 0)
        desk = os.environ.get("XDG_CURRENT_DESKTOP", "unknown").replace("X-", "")
        t.pack_start(label(f"● {platform.system()} ({platform.machine()}) • {desk} • Kernel {platform.release()}",
                           ("faint", "mono"), ellipsize=True), False, False, 0)
        row.pack_start(t, True, True, 0)
        row.pack_end(self.action_button("Refresh", self.run_diagnose, icon_name="view-refresh-symbolic"),
                     False, False, 0)
        row.pack_end(self.action_button("Health scan", self._health_scan, primary=True,
                                        icon_name="emblem-favorite-symbolic"), False, False, 0)
        up = vbox(1)
        up.set_valign(Gtk.Align.CENTER)
        up.pack_start(label("SYSTEM UPTIME", ("kicker",), xalign=1.0), False, False, 0)
        self.uptime_lbl = label("–", ("chip-val",), xalign=1.0)
        up.pack_start(self.uptime_lbl, False, False, 0)
        row.pack_end(up, False, False, 12)
        top.pack_start(row, False, False, 0)
        page.pack_start(top, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        page.pack_start(grid, False, False, 0)

        cpu_model = next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines()
                          if l.startswith("model name")), platform.processor() or "CPU")

        # gauge cards
        self.g_cpu, cpu_card, cpu_foot = self._gauge_card(
            "Processor Activity", "speedometer-symbolic", EMERALD, "CPU load", f"{os.cpu_count()} threads", "ok")
        cpu_foot.pack_start(label(cpu_model, ("faint", "mono"), xalign=0.5, ellipsize=True), False, False, 0)
        lr = hbox(0)
        lr.pack_start(label("Load average", ("faint", "mono")), False, False, 0)
        self.load_lbl = label("–", ("stat-val",), xalign=1.0)
        lr.pack_end(self.load_lbl, False, False, 0)
        cpu_foot.pack_start(lr, False, False, 0)

        self.g_mem, mem_card, mem_foot = self._gauge_card(
            "System Memory", "media-flash-symbolic", BLUE, "RAM used", human(self.sampler.sample()["mem_total"]),
            "blue")
        self.mem_used, self.mem_swap = self._tiles(mem_foot, "USED", "SWAP USED")

        self.g_disk, disk_card, disk_foot = self._gauge_card(
            "Root Disk", "drive-harddisk-symbolic", ORANGE, "Storage full", human(shutil.disk_usage("/").total),
            "orange")
        self.disk_used, self.disk_free = self._tiles(disk_foot, "OCCUPIED", "AVAILABLE")

        # performance chart (2 columns)
        perf = card()
        ph = hbox(8)
        ph.pack_start(icon("org.gnome.SystemMonitor-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        ph.pack_start(label("Live Performance", ("h2",)), False, False, 0)
        for name, col in (("RAM", BLUE), ("CPU", EMERALD)):
            ph.pack_end(self._legend(name, col), False, False, 4)
        perf.pack_start(ph, False, False, 0)
        self.perf_chart = LineChart([("CPU", EMERALD), ("RAM", BLUE)], points=90, height=150, fixed_max=100)
        perf.pack_start(self.perf_chart, True, True, 10)

        # health card
        hc = card("ecard")
        hh = hbox(8)
        hh.pack_start(icon("emblem-favorite-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        hh.pack_start(label("Health Score", ("h2",)), False, False, 0)
        self.health_pill = pill("scanning", "dim")
        hh.pack_end(self.health_pill, False, False, 0)
        hc.pack_start(hh, False, False, 0)
        self.g_health = RingGauge(EMERALD, "of 100", size=118)
        hc.pack_start(self.g_health, False, False, 6)
        counts = hbox(6)
        counts.set_halign(Gtk.Align.CENTER)
        self.cnt_fail, self.cnt_warn, self.cnt_info = pill("0 problems", "fail"), pill("0 warnings", "warn"), \
            pill("0 tips", "info")
        for p in (self.cnt_fail, self.cnt_warn, self.cnt_info):
            counts.pack_start(p, False, False, 0)
        hc.pack_start(counts, False, False, 4)
        self.issue_summary = label("Scanning…", ("faint",), xalign=0.5, wrap=True)
        self.issue_summary.set_justify(Gtk.Justification.CENTER)
        hc.pack_start(self.issue_summary, False, False, 6)
        acts = vbox(6)
        acts.pack_start(self.action_button("Fix all issues", self.fix_all, primary=True,
                                           icon_name="emblem-ok-symbolic"), False, False, 0)
        # stacked, not side by side: in an equal-width grid this card sets every column's minimum
        acts.pack_start(self.action_button("Recommended tweaks", self.apply_recommended), False, False, 0)
        acts.pack_start(self.action_button("Quick clean", self.quick_clean), False, False, 0)
        hc.pack_end(acts, False, False, 0)

        # network chart
        net = card()
        nh = hbox(8)
        nh.pack_start(icon("network-wired-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        nh.pack_start(label("Network Throughput", ("h2",)), False, False, 0)
        self.net_lbl = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        nh.pack_end(self.net_lbl, True, True, 0)
        net.pack_start(nh, False, False, 0)
        self.net_chart = LineChart([("Down", VIOLET), ("Up", ORANGE)], points=90, height=100, fmt=rate, log=True)
        net.pack_start(self.net_chart, True, True, 10)

        # system details
        det = card()
        dh = hbox(8)
        dh.pack_start(icon("dialog-information-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        dh.pack_start(label("System Details", ("h2",)), False, False, 0)
        det.pack_start(dh, False, False, 0)
        self.kv_grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        det.pack_start(self.kv_grid, False, False, 10)
        self._fill_details()
        self._responsive.append((grid, {
            "wide": [(cpu_card, 0, 0, 1, 1), (mem_card, 1, 0, 1, 1), (disk_card, 2, 0, 1, 1),
                     (perf, 0, 1, 2, 1), (hc, 2, 1, 1, 2), (net, 0, 2, 1, 1), (det, 1, 2, 1, 1)],
            "medium": [(cpu_card, 0, 0, 1, 1), (mem_card, 1, 0, 1, 1), (disk_card, 0, 1, 1, 1),
                       (hc, 1, 1, 1, 2), (det, 0, 2, 1, 1), (perf, 0, 3, 2, 1), (net, 0, 4, 2, 1)],
            "narrow": [(w, 0, i, 1, 1) for i, w in enumerate((hc, cpu_card, mem_card, disk_card, perf, net, det))],
        }))

        note = label("MintCare only writes its own drop-in files (named <tt>*mintcare*</tt>) and records what "
                     "it changed in <tt>/var/lib/mintcare</tt>, so every optimisation can be switched off again "
                     "from the Optimise page.", ("faint",), wrap=True, markup=True)
        page.pack_start(note, False, False, 0)
        return scrolled(page)

    def _gauge_card(self, title, icon_name, color, caption, badge, kind):
        c = card()
        h = hbox(8)
        h.pack_start(icon(icon_name, Gtk.IconSize.BUTTON, kind if kind in ("ok", "info") else "warn"),
                     False, False, 0)
        h.pack_start(label(title, ("h2",)), False, False, 0)
        h.pack_end(pill(badge, kind), False, False, 0)
        c.pack_start(h, False, False, 0)
        g = RingGauge(color, caption)
        c.pack_start(g, False, False, 4)
        foot = vbox(6)
        sep = Gtk.Separator()
        foot.pack_start(sep, False, False, 4)
        c.pack_start(foot, False, False, 0)
        return g, c, foot

    def _tiles(self, parent, a, b):
        row = hbox(8)
        row.set_homogeneous(True)
        vals = []
        for k in (a, b):
            t = vbox(1, "stat-tile")
            t.pack_start(label(k, ("kicker",), xalign=0.5), False, False, 0)
            v = label("–", ("stat-val",), xalign=0.5)
            t.pack_start(v, False, False, 0)
            row.pack_start(t, True, True, 0)
            vals.append(v)
        parent.pack_start(row, False, False, 0)
        return vals

    def _legend(self, name, color):
        b = hbox(5)
        dot = Gtk.DrawingArea()
        dot.set_size_request(8, 8)
        dot.set_valign(Gtk.Align.CENTER)

        def draw(_w, cr, col=color):
            from .widgets import rgb
            cr.set_source_rgba(*rgb(col))
            cr.arc(4, 4, 4, 0, 6.3)
            cr.fill()
        dot.connect("draw", draw)
        b.pack_start(dot, False, False, 0)
        b.pack_start(label(name, ("faint", "mono")), False, False, 0)
        return b

    def _fill_details(self):
        for ch in self.kv_grid.get_children():
            ch.destroy()
        swaps = read("/proc/swaps").splitlines()[1:]
        swap_kind = ", ".join(sorted({"zram" if "zram" in s else "disk" for s in swaps})) or "none"
        rows = [
            ("Desktop", os.environ.get("XDG_CURRENT_DESKTOP", "unknown").replace("X-", "")),
            ("TCP congestion", f"{sysctl('net.ipv4.tcp_congestion_control')} / {sysctl('net.core.default_qdisc')}"),
            ("Swap", f"{swap_kind} (swappiness {sysctl('vm.swappiness')})"),
            ("Firewall", "enabled" if tweaks.BY_ID["ufw"].probe() else "disabled"),
            ("Tweaks active", str(sum(1 for v in self._tweak_initial.values() if v)) if self._tweak_initial
             else "…"),
        ]
        for i, (k, v) in enumerate(rows):
            self.kv_grid.attach(label(k, ("kv-key",)), 0, i, 1, 1)
            self.kv_grid.attach(label(v, ("kv-val",), ellipsize=True), 1, i, 1, 1)
        self.kv_grid.show_all()
        self.chip_vals["TCP"].set_text(sysctl("net.ipv4.tcp_congestion_control").upper())

    def _tick(self):
        s = self.sampler.sample()
        self.g_cpu.set_value(s["cpu"])
        self.g_mem.set_value(s["mem_pct"])
        self.g_disk.set_value(s["disk_pct"])
        self.load_lbl.set_text(" • ".join(f"{v:.2f}" for v in s["load"]))
        self.mem_used.set_text(human(s["mem_used"]))
        self.mem_swap.set_text(human(s["swap_used"]) if s["swap_total"] else "no swap")
        self.disk_used.set_text(human(s["disk_used"]))
        self.disk_free.set_text(human(s["disk_free"]))
        self.uptime_lbl.set_text(fmt_uptime(s["uptime"]))
        t = hardware.cpu_temp()
        self.chip_vals["CPU TEMP"].set_text(f"{t:.0f}°C" if t is not None else "n/a")
        self.perf_chart.push(s["cpu"], s["mem_pct"])
        self.net_chart.push(s["rx_rate"], s["tx_rate"])
        self.net_lbl.set_text(f"↓ {rate(s['rx_rate'])}   ↑ {rate(s['tx_rate'])}")
        return True

    def _health_scan(self):
        self.goto("diagnose")
        self.run_diagnose()

    def _update_overview(self):
        res = self.check_results
        counts = {s: sum(1 for r in res.values() if r.status == s) for s in STATUS_ICON}
        score = max(0, 100 - counts[checks.FAIL] * 15 - counts[checks.WARN] * 5 - counts[checks.INFO])
        color, kind, word = ((EMERALD, "ok", "healthy") if score >= 85 else
                             ("#f59e0b", "warn", "needs care") if score >= 60 else ("#ef4444", "fail", "at risk"))
        self.g_health.color = color
        self.g_health.set_value(score, str(score))
        set_pill(self.health_pill, word, kind)
        set_pill(self.side_pill, word, kind)
        self.side_score.set_text(str(score))
        self.side_bar.color = color
        self.side_bar.set_value(score)
        self.cnt_fail.set_text(f"{counts[checks.FAIL]} problems")
        self.cnt_warn.set_text(f"{counts[checks.WARN]} warnings")
        self.cnt_info.set_text(f"{counts[checks.INFO]} tips")
        fixable = sum(1 for r in res.values() if r.fix and r.status in (checks.WARN, checks.FAIL)
                      and r.fix.kind in ("root", "user", "tweak"))
        self.issue_summary.set_text(
            f"{len(res)} checks run. " +
            (f"{fixable} issue(s) can be fixed automatically." if fixable else
             "Nothing needs fixing — tips have their own buttons on Diagnose & Fix."))
        issues = counts[checks.FAIL] + counts[checks.WARN]
        badge = self.nav_badges["diagnose"]
        badge.set_text(str(issues))
        badge.set_visible(issues > 0)
        self._fill_details()

    # ------------------------------------------------------------ diagnose

    def _build_diagnose(self):
        page = vbox(14, "page")
        self.diag_header = self.page_header(
            "system-search-symbolic", "Diagnose & Fix",
            "Checks networking (incl. TCP BBR), packages, disks, memory, drivers, security and services.",
            [self.action_button("Rescan", self.run_diagnose, icon_name="view-refresh-symbolic"),
             self.action_button("Fix all", self.fix_all, primary=True, icon_name="emblem-ok-symbolic")])
        page.pack_start(self.diag_header, False, False, 0)
        self.diag_box = vbox(10)
        page.pack_start(self.diag_box, False, False, 0)
        return scrolled(page)

    def run_diagnose(self):
        def work():
            return {c.id: c.run() for c in checks.CHECKS}

        def done(res):
            if res is None:
                return
            self.check_results = res
            self._render_diagnose()
            self._update_overview()
            n = sum(1 for r in res.values() if r.status in (checks.WARN, checks.FAIL))
            self.set_status(f"Scan complete: {n} issue(s)" if n else "Scan complete: all good")
        self.in_thread("Scanning system…", work, done)
        return False

    def _render_diagnose(self):
        self._action_buttons = [b for b in self._action_buttons if b not in self._diag_buttons]
        self._diag_buttons = []
        for ch in self.diag_box.get_children():
            ch.destroy()
        order = {checks.FAIL: 0, checks.WARN: 1, checks.INFO: 2, checks.OK: 3}
        cats = list(dict.fromkeys(c.category for c in checks.CHECKS))
        for cat in cats:
            items = [c for c in checks.CHECKS if c.category == cat and c.id in self.check_results]
            items.sort(key=lambda c: order[self.check_results[c.id].status])
            ok = sum(1 for c in items if self.check_results[c.id].status == checks.OK)
            head = hbox(8)
            head.pack_start(label(cat.upper(), ("kicker",)), False, False, 0)
            head.pack_start(label(f"{ok}/{len(items)} passing", ("faint", "mono")), False, False, 0)
            self.diag_box.pack_start(head, False, False, 2)
            c, lb = list_card()
            for chk in items:
                lb.add(self._check_row(chk, self.check_results[chk.id]))
            self.diag_box.pack_start(c, False, False, 0)
        self.diag_box.show_all()

    def _check_row(self, check, res):
        row = Gtk.ListBoxRow(activatable=False)
        self.check_rows = getattr(self, "check_rows", {})
        self.check_rows[check.id] = row
        box = hbox(12)
        icon_name, cls = STATUS_ICON[res.status]
        box.pack_start(icon(icon_name, Gtk.IconSize.LARGE_TOOLBAR, cls), False, False, 0)
        text = vbox(2)
        text.pack_start(label(check.title, ("h3",)), False, False, 0)
        text.pack_start(label(res.summary, ("muted",), wrap=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        if res.fix and res.status != checks.OK:
            b = self.action_button(res.fix.label, lambda c=check, r=res: self.apply_fixes([(c, r)]),
                                   primary=res.status in (checks.WARN, checks.FAIL))
            self._diag_buttons.append(b)
            box.pack_end(b, False, False, 0)
        box.pack_end(pill(STATUS_WORD[res.status], cls), False, False, 0)
        row.add(box)
        return row

    def _fix_steps(self, items):
        root, user = [], []
        for check, res in items:
            f = res.fix
            if f.kind == "root":
                root.append((check.id, check.title, f.payload))
            elif f.kind == "user":
                user.append((check.id, check.title, f.payload))
            elif f.kind == "tweak":
                t = tweaks.BY_ID[f.payload]
                if not any(s[0] == "tweak-" + t.id for s in root):
                    root.append(("tweak-" + t.id, t.title, t.apply_script()))
        return root, user

    def apply_fixes(self, items):
        if self.busy:
            return
        direct = [(c, r) for c, r in items if r.fix.kind in ("root", "user", "tweak")]
        for c, r in items:
            if r.fix.kind == "launch":
                self.log(f"Opening {r.fix.payload} for: {c.title}")
                try:
                    subprocess.Popen([r.fix.payload], start_new_session=True)
                except OSError as e:
                    self.log(f"Could not start {r.fix.payload}: {e}")
            elif r.fix.kind == "page" and len(items) == 1:
                self.goto(r.fix.payload)
        if not direct:
            return
        root, user = self._fix_steps(direct)
        snap = self.ask_snapshot("system repairs") if root else []
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, "Repair")
            self._refresh_tweaks()
            self.run_diagnose()
        self.in_thread("Fixing…", lambda: self.execute(root, user, "Repair", snap), done)

    def fix_all(self):
        items = [(c, self.check_results[c.id]) for c in checks.CHECKS
                 if c.id in self.check_results
                 and self.check_results[c.id].status in (checks.WARN, checks.FAIL)
                 and self.check_results[c.id].fix
                 and self.check_results[c.id].fix.kind in ("root", "user", "tweak")]
        if not items:
            self.set_status("Nothing to fix automatically")
            return
        listing = "\n".join(f"• {GLib.markup_escape_text(c.title)}: "
                            f"{GLib.markup_escape_text(r.fix.label)}" for c, r in items)
        if self.confirm(f"Apply {len(items)} fix(es)?", listing):
            self.apply_fixes(items)

    # ------------------------------------------------------------ optimise

    def _build_optimise(self):
        page = vbox(14, "page")
        self.opt_header = self.page_header(
            "power-profile-performance-symbolic", "Optimise",
            "Toggle tweaks, then press Apply. Switching a tweak off reverts it.",
            [self.action_button("Undo all…", self.undo_all, icon_name="edit-undo-symbolic"),
             self.action_button("Select recommended", self.select_recommended),
             self.action_button("Apply changes", self.apply_tweaks, primary=True,
                                icon_name="emblem-ok-symbolic")])
        self.pending_lbl = self.opt_header.subtitle
        page.pack_start(self.opt_header, False, False, 0)

        page.pack_start(label("PROFILES", ("kicker",)), False, False, 0)
        prow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=1,
                           max_children_per_line=4, column_spacing=12, row_spacing=12)
        self.profile_meters = {}
        for pr in tweaks.PROFILES:
            tile = vbox(6, "profile-tile")
            th = hbox(10)
            th.pack_start(icon(pr.icon, Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
            th.pack_start(label(pr.title, ("h3",)), True, True, 0)
            tile.pack_start(th, False, False, 0)
            d = label(pr.description, ("faint",), wrap=True)
            d.set_lines(3)
            tile.pack_start(d, True, True, 0)
            meter = Bar(EMERALD, 5)
            tile.pack_start(meter, False, False, 2)
            foot = hbox(8)
            cnt = label("", ("faint", "mono"))
            foot.pack_start(cnt, True, True, 0)
            foot.pack_end(self.action_button("Select", lambda pid=pr.id: self.select_profile(pid)), False, False, 0)
            tile.pack_start(foot, False, False, 0)
            self.profile_meters[pr.id] = (meter, cnt)
            self.profile_tiles = getattr(self, "profile_tiles", {})
            self.profile_tiles[pr.id] = tile
            tile.set_size_request(230, -1)
            prow.add(tile)
        page.pack_start(prow, False, False, 0)

        self.switches, self.tweak_state_lbls, self.tweak_rows, self.opt_sections = {}, {}, {}, []
        for cat in tweaks.CATEGORIES:
            kicker = label(cat.upper(), ("kicker",))
            page.pack_start(kicker, False, False, 0)
            c, lb = list_card()
            ids = []
            for t in (t for t in tweaks.TWEAKS if t.category == cat):
                row = self._tweak_row(t)
                self.tweak_rows[t.id] = row
                ids.append(t.id)
                lb.add(row)
            page.pack_start(c, False, False, 0)
            self.opt_sections.append((cat, kicker, c, ids))
        GLib.idle_add(self._refresh_tweaks)
        return scrolled(page)

    def _tweak_row(self, t):
        row = Gtk.ListBoxRow(activatable=False)
        box = hbox(12)
        text = vbox(3)
        title = hbox(8)
        title.pack_start(label(t.title, ("h3",)), False, False, 0)
        if t.recommended:
            title.pack_start(pill("recommended", "ok"), False, False, 0)
        state = pill("", "dim")
        state.set_no_show_all(True)
        self.tweak_state_lbls[t.id] = state
        title.pack_start(state, False, False, 0)
        text.pack_start(title, False, False, 0)
        text.pack_start(label(t.description, ("muted",), wrap=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.connect("notify::active", self._on_switch, t)
        self.switches[t.id] = sw
        box.pack_end(sw, False, False, 0)
        row.add(box)
        return row

    def _refresh_tweaks(self):
        """Probe tweak states off the UI thread (they shell out to dpkg/systemctl)."""
        if getattr(self, "_tweaks_refreshing", False):
            self._tweaks_again = True
            return False
        self._tweaks_refreshing = True

        def work():
            return {t.id: (t.available(), t.state() if t.available() else "off") for t in tweaks.TWEAKS}

        def done(states):
            self._tweaks_refreshing = False
            if states:
                self._apply_tweak_states(states)
            if getattr(self, "_tweaks_again", False):
                self._tweaks_again = False
                self._refresh_tweaks()
        self.bg(work, done)
        return False

    def _apply_tweak_states(self, states):
        self._tweak_initial = {}
        for t in tweaks.TWEAKS:
            sw, lbl = self.switches[t.id], self.tweak_state_lbls[t.id]
            avail, st = states[t.id]
            self._tweak_initial[t.id] = st != "off"
            sw.handler_block_by_func(self._on_switch)
            sw.set_active(st != "off")
            sw.handler_unblock_by_func(self._on_switch)
            lbl.show()
            if not avail:
                sw.set_sensitive(False)
                set_pill(lbl, "n/a on this PC", "dim")
            elif st == "system":
                sw.set_sensitive(False)
                set_pill(lbl, "already active", "info")
            elif st == "mintcare":
                sw.set_sensitive(t.revert is not None)
                set_pill(lbl, "applied", "ok")
            else:
                sw.set_sensitive(True)
                lbl.hide()
        self._update_pending()
        self._update_profiles()
        if hasattr(self, "kv_grid"):
            self._fill_details()

    def _update_profiles(self):
        for pr in tweaks.PROFILES:
            ids = [i for i in pr.tweaks if i in tweaks.BY_ID and tweaks.BY_ID[i].available()]
            on = sum(1 for i in ids if self._tweak_initial.get(i))
            meter, cnt = self.profile_meters[pr.id]
            meter.set_value(100 * on / len(ids) if ids else 0)
            cnt.set_text(f"{on}/{len(ids)} active")

    def select_profile(self, pid):
        pr = tweaks.PROFILE_BY_ID[pid]
        picked = []
        for i in pr.tweaks:
            sw = self.switches.get(i)
            if sw and sw.get_sensitive() and not sw.get_active():
                sw.set_active(True)
                picked.append(tweaks.BY_ID[i].title)
        self.goto("optimise")
        if picked:
            self.set_status(f"{pr.title}: {len(picked)} tweak(s) selected — press Apply changes")
            self.log(f"Profile '{pr.title}' selected: " + ", ".join(picked))
        else:
            self.set_status(f"Everything in the {pr.title} profile is already active")

    def _on_switch(self, sw, _pspec, t):
        if sw.get_active():
            for other in t.conflicts:
                o = self.switches.get(other)
                if o and o.get_active() and o.get_sensitive():
                    o.set_active(False)
        self._update_pending()

    def _pending(self):
        on, off = [], []
        for t in tweaks.TWEAKS:
            now = self.switches[t.id].get_active()
            if now != self._tweak_initial.get(t.id, now):
                (on if now else off).append(t)
        return on, off

    def _update_pending(self):
        on, off = self._pending()
        if on or off:
            self.pending_lbl.set_markup(f"<b>{len(on)}</b> to apply, <b>{len(off)}</b> to revert — "
                                        "press <b>Apply changes</b>.")
        else:
            self.pending_lbl.set_text("Toggle tweaks, then press Apply. Switching a tweak off reverts it.")

    def select_recommended(self):
        for t in tweaks.TWEAKS:
            sw = self.switches[t.id]
            if t.recommended and sw.get_sensitive() and not sw.get_active():
                sw.set_active(True)

    def apply_tweaks(self):
        on, off = self._pending()
        if not (on or off):
            self.set_status("No changes to apply")
            return
        steps = [("off-" + t.id, "Revert: " + t.title, t.revert_script()) for t in off]
        steps += [("on-" + t.id, "Apply: " + t.title, t.apply_script()) for t in on]
        snap = self.ask_snapshot(f"{len(steps)} optimisation change(s)")
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, "Optimisation")
            self._refresh_tweaks()
            self.run_diagnose()
        self.in_thread("Applying tweaks…", lambda: self.execute(steps, [], "Optimise", snap), done)

    def apply_recommended(self):
        self.select_recommended()
        on, off = self._pending()
        if not on and not off:
            self.set_status("All recommended tweaks are already active")
            return
        listing = "\n".join(f"• {GLib.markup_escape_text(t.title)}" for t in on)
        if self.confirm(f"Apply {len(on)} recommended tweak(s)?", listing):
            self.apply_tweaks()
        else:
            self._refresh_tweaks()

    # ------------------------------------------------------------ clean

    def _build_clean(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "user-trash-symbolic", "Clean",
            "Measures junk first, then removes only what you select.",
            [self.action_button("Rescan", self.run_clean_scan, icon_name="view-refresh-symbolic"),
             self.action_button("Clean selected", self.run_clean, primary=True,
                                icon_name="user-trash-symbolic")]), False, False, 0)

        hero = card("ecard")
        hr = hbox(16)
        left = vbox(2)
        left.pack_start(label("RECLAIMABLE", ("kicker",)), False, False, 0)
        self.clean_big = label("…", ("big",))
        left.pack_start(self.clean_big, False, False, 0)
        hr.pack_start(left, False, False, 0)
        right = vbox(6)
        right.set_valign(Gtk.Align.CENTER)
        self.clean_total = label("Scanning…", ("muted",), wrap=True)
        right.pack_start(self.clean_total, False, False, 0)
        self.clean_bar = Bar(EMERALD, 8)
        right.pack_start(self.clean_bar, False, False, 0)
        hr.pack_start(right, True, True, 0)
        hero.pack_start(hr, False, False, 0)
        page.pack_start(hero, False, False, 0)

        c, lb = list_card()
        self.clean_checks, self.clean_sizes, self.clean_details, self.clean_bytes = {}, {}, {}, {}
        for item in cleaner.ITEMS:
            if not item.available():
                continue
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            cb = Gtk.CheckButton(active=item.default, valign=Gtk.Align.CENTER)
            cb.connect("toggled", lambda *_: self._update_clean_total())
            self.clean_checks[item.id] = cb
            box.pack_start(cb, False, False, 0)
            text = vbox(2)
            th = hbox(8)
            th.pack_start(label(item.title, ("h3",)), False, False, 0)
            if item.root_script:
                th.pack_start(pill("admin", "dim"), False, False, 0)
            text.pack_start(th, False, False, 0)
            det = label(item.description, ("muted",), wrap=True)
            self.clean_details[item.id] = det
            text.pack_start(det, False, False, 0)
            box.pack_start(text, True, True, 0)
            size = label("…", ("stat-val",), xalign=1.0)
            self.clean_sizes[item.id] = size
            box.pack_end(size, False, False, 0)
            row.add(box)
            lb.add(row)
            self.clean_rows = getattr(self, "clean_rows", {})
            self.clean_rows[item.id] = row
        page.pack_start(c, False, False, 0)
        return scrolled(page)

    def run_clean_scan(self):
        def work():
            return {i.id: i.scan() for i in cleaner.ITEMS if i.id in self.clean_checks}

        def done(res):
            if not res:
                return
            for iid, (size, detail) in res.items():
                item = cleaner.BY_ID[iid]
                self.clean_bytes[iid] = size or 0
                self.clean_sizes[iid].set_text(human(size) if size else ("—" if size is None else "0 B"))
                self.clean_details[iid].set_text(f"{item.description} {detail}.")
            self._update_clean_total()
        self.in_thread("Measuring junk…", work, done)
        return False

    def _update_clean_total(self):
        total = sum(self.clean_bytes.get(i, 0) for i, cb in self.clean_checks.items() if cb.get_active())
        everything = sum(self.clean_bytes.values()) or 1
        n = sum(1 for cb in self.clean_checks.values() if cb.get_active())
        self.clean_big.set_text(human(total))
        self.clean_total.set_text(f"{n} of {len(self.clean_checks)} items selected. Items shown as “—” "
                                  "can't be measured in advance and may free more.")
        self.clean_bar.set_value(100 * total / everything)

    def run_clean(self):
        sel = [cleaner.BY_ID[i] for i, cb in self.clean_checks.items() if cb.get_active()]
        if not sel:
            return
        if any(i.id == "trash" for i in sel) and not self.confirm(
                "Empty the Trash?", "Files in the Trash will be permanently deleted."):
            return
        root = [(i.id, i.title, i.root_script) for i in sel if i.root_script]
        user = [(i.id + "-user", i.title, i.user_script) for i in sel if i.user_script]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, "Clean-up")
            if freed > 0:
                self.set_status(f"Freed {human(freed)}")
                self.log(f"Freed {human(freed)} on /")
            self.run_clean_scan()
            self.run_diagnose()
        self.in_thread("Cleaning…", lambda: self.execute(root, user, "Clean"), done)

    def quick_clean(self):
        self.goto("clean")
        self.run_clean()

    # ------------------------------------------------------------ disk space

    def _build_space(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "drive-multidisk-symbolic", "Disk space",
            "Find what's using your space: big folders and files, duplicate copies and old system "
            "snapshots. Files go to the Trash, so nothing is lost until you empty it."), False, False, 0)
        self.space_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        sw = Gtk.StackSwitcher(stack=self.space_stack, halign=Gtk.Align.START)
        page.pack_start(sw, False, False, 0)
        self.space_stack.add_titled(self._build_folders_tab(), "folders", "Folders & large files")
        self.space_stack.add_titled(self._build_dups_tab(), "dups", "Duplicates")
        self.space_stack.add_titled(self._build_snaps_tab(), "snaps", "Timeshift snapshots")
        page.pack_start(self.space_stack, False, False, 0)
        return scrolled(page)

    # -- folders

    def _build_folders_tab(self):
        box = vbox(12)
        ctl = hbox(8)
        ctl.pack_start(self.action_button("Home folder", lambda: self.scan_folders(space.HOME),
                                          icon_name="user-home-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button("Whole system", lambda: self.scan_folders("/"),
                                          icon_name="drive-harddisk-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button("Choose folder…", self._choose_scan_folder,
                                          icon_name="folder-symbolic"), False, False, 0)
        self.space_spinner = Gtk.Spinner()
        ctl.pack_end(self.space_spinner, False, False, 0)
        self.space_status = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        ctl.pack_end(self.space_status, True, True, 0)
        box.pack_start(ctl, False, False, 0)

        tc = card()
        nav = hbox(8)
        self.up_btn = self.action_button("Up", self._tree_up, icon_name="go-up-symbolic")
        nav.pack_start(self.up_btn, False, False, 0)
        self.crumb = label("", ("crumb",), ellipsize=True)
        self.crumb.set_ellipsize(Pango.EllipsizeMode.START)
        nav.pack_start(self.crumb, True, True, 0)
        self.crumb_size = label("", ("stat-val",))
        nav.pack_end(self.crumb_size, False, False, 0)
        tc.pack_start(nav, False, False, 0)
        self.treemap = Treemap(self._treemap_activate, height=320)
        tc.pack_start(self.treemap, False, False, 10)
        tc.pack_start(label("Click a folder to look inside it. Hover for details.", ("faint",)), False, False, 0)
        box.pack_start(tc, False, False, 0)

        self.big_head = label("LARGEST FILES", ("kicker",))
        box.pack_start(self.big_head, False, False, 0)
        self.big_card, self.big_list = list_card()
        box.pack_start(self.big_card, False, False, 0)
        self.space_tree = self.space_node = None
        self.space_big = []
        return box

    def _choose_scan_folder(self):
        d = Gtk.FileChooserDialog(title="Choose a folder to analyse", transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Analyse", Gtk.ResponseType.OK)
        d.set_current_folder(space.HOME)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path:
            self.scan_folders(path)

    def scan_folders(self, root):
        self.space_spinner.start()
        self.space_status.set_text(f"Scanning {root} …")
        self.treemap.set_items([])

        def progress(n, path):
            GLib.idle_add(lambda: self.space_status.set_text(f"{n:,} files · {path[-60:]}") and False)

        def done(res):
            self.space_spinner.stop()
            if not res:
                self.space_status.set_text("Scan failed")
                return
            tree, big = res
            self.space_tree, self.space_node, self.space_big = tree, tree, big
            note = " (unreadable system folders skipped)" if root == "/" else ""
            self.space_status.set_text(f"{tree.files:,} files · {human(tree.size)}{note}")
            self._render_tree()
        self.bg(lambda: space.scan_tree(root, progress), done)

    def _render_tree(self):
        node = self.space_node
        if not node:
            return
        self.crumb.set_text(node.path)
        self.crumb_size.set_text(human(node.size))
        self.up_btn.set_sensitive(node is not self.space_tree)
        items, shown = [], 0
        for c in node.children[:28]:
            pct = 100 * c.size / node.size if node.size else 0
            kind = f"{c.files:,} files" if c.is_dir else "file"
            items.append((c.size, c.name + ("/" if c.is_dir else ""), f"{human(c.size)} · {pct:.0f}% · {kind}", c))
            shown += c.size
        rest = node.size - shown
        if rest > node.size * 0.005:
            items.append((rest, "(smaller items)", human(rest), None))
        self.treemap.set_items(items)
        self._render_big()

    def _treemap_activate(self, node):
        if node is None:
            return
        if node.is_dir and node.children:
            self.space_node = node
            self._render_tree()
        elif not node.is_dir:
            self.show_in_folder(node.path)

    def _tree_up(self):
        if not self.space_tree or self.space_node is self.space_tree:
            return
        parent = os.path.dirname(self.space_node.path)
        self.space_node = space.find(self.space_tree, parent) or self.space_tree
        self._render_tree()

    def _render_big(self):
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "big_row", False)]
        for ch in self.big_list.get_children():
            ch.destroy()
        base = self.space_node.path.rstrip("/") + "/"
        rows = [b for b in self.space_big if b[1].startswith(base) and os.path.exists(b[1])][:25]
        self.big_head.set_text(f"LARGEST FILES IN {self.space_node.name.upper()} ({len(rows)})")
        if not rows:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label("No files over 50 MB here.", ("muted",)))
            self.big_list.add(r)
        for size, path, mtime in rows:
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            ctype = Gio.content_type_guess(path, None)[0]
            box.pack_start(css(Gtk.Image.new_from_gicon(Gio.content_type_get_symbolic_icon(ctype),
                                                         Gtk.IconSize.LARGE_TOOLBAR), "tile-ico"), False, False, 0)
            t = vbox(2)
            t.pack_start(label(os.path.basename(path), ("h3",), ellipsize=True), False, False, 0)
            pl = label(os.path.dirname(path), ("faint", "mono"), ellipsize=True)
            pl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            t.pack_start(pl, False, False, 0)
            t.pack_start(label(f"Modified {time.strftime('%Y-%m-%d', time.localtime(mtime))} · "
                               f"{Gio.content_type_get_description(ctype)}", ("muted",)), False, False, 0)
            box.pack_start(t, True, True, 0)
            b2 = self.action_button("Trash", lambda p=path, sz=size: self._trash_big(p, sz),
                                    icon_name="user-trash-symbolic")
            b1 = self.action_button("Show", lambda p=path: self.show_in_folder(p), icon_name="folder-symbolic")
            b1.big_row = b2.big_row = True
            box.pack_end(b2, False, False, 0)
            box.pack_end(b1, False, False, 0)
            box.pack_end(label(human(size), ("stat-val",)), False, False, 8)
            row.add(box)
            self.big_list.add(row)
        self.big_list.show_all()

    def _trash_big(self, path, size):
        if not self.confirm(f"Move to Trash?",
                            f"<tt>{GLib.markup_escape_text(path)}</tt>\n\n{human(size)} — you can restore it "
                            "from the Trash until you empty it."):
            return
        try:
            space.trash(path)
        except GLib.Error as e:
            self.log(f"Could not trash {path}: {e.message}")
            self.set_status("Could not move to Trash", bad=True)
            return
        self.log(f"Moved to Trash: {path} ({human(size)})")
        maintenance.record("trash", f"Moved {os.path.basename(path)} to the Trash ({human(size)})", True, path)
        self.set_status(f"Moved {os.path.basename(path)} to the Trash")
        self.space_big = [b for b in self.space_big if b[1] != path]
        n = self.space_tree
        while n:                                  # shrink the cached sizes along the path
            n.size -= size
            n.children = [c for c in n.children if c.path != path]
            n = next((c for c in n.children if c.is_dir and path.startswith(c.path + os.sep)), None)
        self._render_tree()
        self.run_clean_scan()

    def show_in_folder(self, path):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                          None, 0, 3000, None)
        except GLib.Error:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), None)

    # -- duplicates

    def _build_dups_tab(self):
        box = vbox(12)
        c = card()
        r1 = hbox(8)
        r1.pack_start(label("Search in:", ("muted",)), False, False, 0)
        self.dup_roots = space.default_dup_roots()
        self.dup_roots_lbl = label("", ("crumb",), ellipsize=True, wrap=False)
        r1.pack_start(self.dup_roots_lbl, True, True, 0)
        r1.pack_end(self.action_button("Reset", self._dup_reset_roots), False, False, 0)
        r1.pack_end(self.action_button("Add folder…", self._dup_add_root, icon_name="list-add-symbolic"),
                    False, False, 0)
        c.pack_start(r1, False, False, 0)
        r2 = hbox(12)
        r2.pack_start(label("Ignore files smaller than", ("muted",)), False, False, 0)
        self.dup_min = Gtk.ComboBoxText()
        for t in ("1 MB", "10 MB", "100 MB"):
            self.dup_min.append(t, t)
        self.dup_min.set_active(0)
        r2.pack_start(self.dup_min, False, False, 0)
        self.dup_hidden = Gtk.CheckButton(label="Include hidden files and folders")
        r2.pack_start(self.dup_hidden, False, False, 12)
        c.pack_start(r2, False, False, 10)
        r2 = hbox(8)                      # buttons on their own row so narrow windows fit
        self.dup_cancel_btn = Gtk.Button(label="Cancel")
        css(self.dup_cancel_btn, "ghost")
        self.dup_cancel_btn.connect("clicked", lambda *_: self._space_cancel.set())
        self.dup_cancel_btn.set_sensitive(False)
        r2.pack_end(self.dup_cancel_btn, False, False, 0)
        # Not registered with action_button(): its state follows the scan, not the global busy flag
        self.dup_scan_btn = Gtk.Button()
        inner = hbox(6)
        inner.pack_start(icon("system-search-symbolic"), False, False, 0)
        inner.pack_start(Gtk.Label(label="Find duplicates"), False, False, 0)
        self.dup_scan_btn.add(inner)
        css(self.dup_scan_btn, "primary")
        self.dup_scan_btn.connect("clicked", lambda *_: self.scan_dups())
        self.dup_running = False
        r2.pack_end(self.dup_scan_btn, False, False, 0)
        c.pack_start(r2, False, False, 0)
        self.dup_progress = Gtk.ProgressBar()
        self.dup_progress.set_no_show_all(True)
        c.pack_start(self.dup_progress, False, False, 0)
        self.dup_status = label("Duplicates are compared by content (BLAKE2 hash), not by name. "
                                "The oldest copy in each group is kept by default.", ("faint",), wrap=True)
        c.pack_start(self.dup_status, False, False, 6)
        box.pack_start(c, False, False, 0)

        self.dup_summary = hbox(10)
        self.dup_sel_lbl = label("", ("h2",))
        self.dup_summary.pack_start(self.dup_sel_lbl, True, True, 0)
        self.dup_trash_btn = self.action_button("Move selected to Trash", self.trash_dups, primary=True,
                                                icon_name="user-trash-symbolic")
        self.dup_summary.pack_end(self.dup_trash_btn, False, False, 0)
        self.dup_summary.set_no_show_all(True)
        box.pack_start(self.dup_summary, False, False, 0)
        self.dup_card, self.dup_list = list_card()
        self.dup_card.set_no_show_all(True)
        box.pack_start(self.dup_card, False, False, 0)
        self.dup_groups, self.dup_sel = [], {}
        self._dup_show_roots()
        return box

    def _dup_show_roots(self):
        self.dup_roots_lbl.set_text("   ".join(r.replace(space.HOME, "~") for r in self.dup_roots))

    def _dup_reset_roots(self):
        self.dup_roots = space.default_dup_roots()
        self._dup_show_roots()

    def _dup_add_root(self):
        d = Gtk.FileChooserDialog(title="Add a folder to search", transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        d.set_current_folder(space.HOME)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path and path not in self.dup_roots:
            self.dup_roots = [r for r in self.dup_roots if not r.startswith(path.rstrip("/") + "/")] + [path]
            self._dup_show_roots()

    def scan_dups(self):
        if self.dup_running:
            return
        self.dup_running = True
        self._space_cancel.clear()
        self.dup_scan_btn.set_sensitive(False)
        self.dup_cancel_btn.set_sensitive(True)
        self.dup_progress.show()
        self.dup_progress.set_fraction(0)
        self.dup_status.set_text("Listing files…")
        min_size = {"1 MB": 1 << 20, "10 MB": 10 << 20, "100 MB": 100 << 20}[self.dup_min.get_active_id()]
        hidden = self.dup_hidden.get_active()
        roots = list(self.dup_roots)

        def progress(stage, done, total):
            def ui():
                if stage == "Listing":
                    self.dup_progress.pulse()
                    self.dup_status.set_text(f"Listing files… {done:,} candidates")
                else:
                    self.dup_progress.set_fraction(min(1.0, done / total))
                    self.dup_status.set_text(f"Comparing contents… {human(done)} of up to {human(total)}")
                return False
            GLib.idle_add(ui)

        def done(groups):
            self.dup_running = False
            self.dup_scan_btn.set_sensitive(True)
            self.dup_cancel_btn.set_sensitive(False)
            self.dup_progress.hide()
            if self._space_cancel.is_set():
                self.dup_status.set_text("Cancelled.")
                return
            self.dup_groups = groups or []
            self.dup_sel = {p: i > 0 for g in self.dup_groups for i, (p, _m) in enumerate(g["files"])}
            wasted = sum(g["wasted"] for g in self.dup_groups)
            self.dup_status.set_text(f"Found {len(self.dup_groups):,} groups of identical files — "
                                     f"{human(wasted)} could be freed." if self.dup_groups
                                     else "No duplicate files found.")
            self._render_dups()
        self.bg(lambda: space.duplicates(roots, min_size, hidden, progress, self._space_cancel.is_set), done)

    def _render_dups(self):
        for ch in self.dup_list.get_children():
            ch.destroy()
        show = self.dup_groups[:120]
        for g in show:
            head = Gtk.ListBoxRow(activatable=False)
            css(head, "dup-head")
            hb = hbox(10)
            hb.pack_start(label(f"{len(g['files'])} copies · {human(g['size'])} each", ("h3",)), False, False, 0)
            hb.pack_start(pill(f"{human(g['wasted'])} wasted", "warn"), False, False, 0)
            name = os.path.basename(g["files"][0][0])
            hb.pack_start(label(name, ("faint", "mono"), ellipsize=True), True, True, 0)
            head.add(hb)
            self.dup_list.add(head)
            for i, (p, m) in enumerate(g["files"]):
                row = Gtk.ListBoxRow(activatable=False)
                rb = hbox(10)
                cb = Gtk.CheckButton(active=self.dup_sel.get(p, False), valign=Gtk.Align.CENTER)
                cb.connect("toggled", self._dup_toggle, p)
                rb.pack_start(cb, False, False, 0)
                pl = label(p.replace(space.HOME, "~"), ("mono", "muted"), ellipsize=True)
                pl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                rb.pack_start(pl, True, True, 0)
                if i == 0:
                    rb.pack_end(pill("oldest", "ok"), False, False, 0)
                rb.pack_end(label(time.strftime("%Y-%m-%d", time.localtime(m)), ("faint", "mono")), False, False, 0)
                row.add(rb)
                self.dup_list.add(row)
        if len(self.dup_groups) > len(show):
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(f"Showing the {len(show)} groups that waste the most space "
                        f"(of {len(self.dup_groups):,}). Trash these, then scan again for the rest.",
                        ("faint",), wrap=True))
            self.dup_list.add(r)
        has = bool(self.dup_groups)
        for w in (self.dup_card, self.dup_summary):
            w.set_no_show_all(False)      # no_show_all also blocks show_all() reaching the children
            w.show_all()
            w.set_visible(has)
            w.set_no_show_all(True)
        self._dup_update_sel()

    def _dup_toggle(self, cb, path):
        self.dup_sel[path] = cb.get_active()
        self._dup_update_sel()

    def _dup_update_sel(self):
        visible = {p for g in self.dup_groups[:120] for p, _ in g["files"]}
        n = sum(1 for p, v in self.dup_sel.items() if v and p in visible)
        size = sum(g["size"] for g in self.dup_groups[:120] for p, _ in g["files"] if self.dup_sel.get(p))
        self.dup_sel_lbl.set_text(f"{n:,} files selected · {human(size)}")

    def trash_dups(self):
        groups = self.dup_groups[:120]
        picked, skipped = [], 0
        for g in groups:
            sel = [p for p, _ in g["files"] if self.dup_sel.get(p)]
            if len(sel) == len(g["files"]):
                skipped += 1                          # never remove every copy
                continue
            picked += [(p, g["size"]) for p in sel]
        if not picked:
            self.set_status("Nothing selected" + (" (a group can't have every copy selected)" if skipped else ""))
            return
        total = sum(s_ for _, s_ in picked)
        extra = f"\n\n{skipped} group(s) had every copy ticked and will be skipped." if skipped else ""
        if not self.confirm(f"Move {len(picked):,} duplicate file(s) to the Trash?",
                            f"Frees {human(total)} once you empty the Trash. One copy of each file is kept."
                            + extra):
            return

        def work():
            ok, failed = [], []
            for p, sz in picked:
                try:
                    space.trash(p)
                    ok.append((p, sz))
                except GLib.Error as e:
                    failed.append(f"{p}: {e.message}")
            return ok, failed

        def done(res):
            ok, failed = res or ([], [])
            for p, _ in ok:
                self.log(f"Moved duplicate to Trash: {p}")
            if ok:
                maintenance.record("trash", f"Moved {len(ok):,} duplicate file(s) to the Trash "
                                            f"({human(sum(s_ for _, s_ in ok))})", not failed)
            for f in failed:
                self.log(f"Could not trash {f}")
            gone = {p for p, _ in ok}
            for g in self.dup_groups:
                g["files"] = [f for f in g["files"] if f[0] not in gone]
                g["wasted"] = g["size"] * max(0, len(g["files"]) - 1)
            self.dup_groups = [g for g in self.dup_groups if len(g["files"]) > 1]
            for p in gone:
                self.dup_sel.pop(p, None)
            self.set_status(f"Moved {len(ok):,} duplicates ({human(sum(s_ for _, s_ in ok))}) to the Trash",
                            bad=bool(failed))
            self._render_dups()
            self.run_clean_scan()
        self.in_thread("Moving duplicates to the Trash…", work, done)

    # -- snapshots

    def _build_snaps_tab(self):
        box = vbox(12)
        c = card("ecard")
        row = hbox(16)
        left = vbox(2)
        left.pack_start(label("SNAPSHOTS", ("kicker",)), False, False, 0)
        self.snap_count = label("…", ("boot-big",))
        left.pack_start(self.snap_count, False, False, 0)
        row.pack_start(left, False, False, 0)
        self.snap_info = label("", ("muted",), wrap=True)
        self.snap_info.set_valign(Gtk.Align.CENTER)
        row.pack_start(self.snap_info, True, True, 0)
        row.pack_end(self.action_button("Delete selected", self.delete_snapshots, primary=True,
                                        icon_name="user-trash-symbolic"), False, False, 0)
        row.pack_end(self.action_button("Open Timeshift", lambda: subprocess.Popen(
            ["timeshift-launcher"], start_new_session=True), icon_name="document-open-recent-symbolic"),
            False, False, 0)
        c.pack_start(row, False, False, 0)
        box.pack_start(c, False, False, 0)
        box.pack_start(label("Snapshots share unchanged files, so deleting one frees only what changed "
                             "around it. The newest snapshot is always kept, and snapshots you gave a "
                             "comment are never pre-selected.", ("faint",), wrap=True), False, False, 0)
        self.snap_card, self.snap_list = list_card()
        box.pack_start(self.snap_card, False, False, 0)
        self.snap_sel = {}
        return box

    def load_snapshots(self):
        self.bg(lambda: (space.snapshots(), space.timeshift_summary()), self._render_snapshots)

    def _render_snapshots(self, res):
        if not res:
            return
        (snaps, note), summary = res
        self.snaps = snaps
        self.snap_count.set_text(str(len(snaps)))
        parts = []
        if summary["schedule"]:
            parts.append("Schedule: " + ", ".join(f"{k} (keeps {summary['keep'].get(k, '?')})"
                                                  for k in summary["schedule"]))
        else:
            parts.append("No automatic schedule.")
        if summary["base_size"]:
            parts.append(f"A full system copy is about {human(summary['base_size'])}; later snapshots store "
                         "only changes.")
        if note:
            parts.append(note)
        self.snap_info.set_text(" ".join(parts))
        for ch in self.snap_list.get_children():
            ch.destroy()
        self.snap_sel = {}
        if not snaps:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(note or "No snapshots.", ("muted",)))
            self.snap_list.add(r)
        for sn in snaps:
            row = Gtk.ListBoxRow(activatable=False)
            b = hbox(12)
            cb = Gtk.CheckButton(valign=Gtk.Align.CENTER, sensitive=not sn.get("latest"))
            cb.connect("toggled", lambda w, n=sn["name"]: self.snap_sel.__setitem__(n, w.get_active()))
            b.pack_start(cb, False, False, 0)
            t = vbox(2)
            th = hbox(8)
            when = time.strftime("%a %d %b %Y, %H:%M", time.localtime(sn["created"])) if sn["created"] else sn["name"]
            th.pack_start(label(when, ("h3",)), False, False, 0)
            for tag in sn["tags"]:
                th.pack_start(pill(tag, "dim"), False, False, 0)
            if sn.get("latest"):
                th.pack_start(pill("newest · kept", "ok"), False, False, 0)
            t.pack_start(th, False, False, 0)
            if sn["comment"]:
                t.pack_start(label(f"“{sn['comment']}”", ("muted",)), False, False, 0)
            t.pack_start(label(f"{sn['name']} · {sn['files']:,} files", ("faint", "mono")), False, False, 0)
            b.pack_start(t, True, True, 0)
            row.add(b)
            self.snap_list.add(row)
        self.snap_list.show_all()

    def delete_snapshots(self):
        names = [n for n, v in self.snap_sel.items() if v]
        if not names:
            self.set_status("Tick the snapshots you want to delete first")
            return
        info = {s_["name"]: s_ for s_ in getattr(self, "snaps", [])}
        lines = []
        for n in names:
            c = info.get(n, {}).get("comment")
            lines.append(f"• {n}" + (f" — <b>“{GLib.markup_escape_text(c)}”</b>" if c else ""))
        if not self.confirm(f"Delete {len(names)} Timeshift snapshot(s)?",
                            "\n".join(lines) + "\n\nThis can't be undone; you won't be able to roll back to "
                            "these points."):
            return
        steps = [(f"snap{i}", f"Delete snapshot {n}", space.delete_snapshot_script(n)) for i, n in enumerate(names)]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, "Snapshot clean-up")
            if freed > 0:
                self.log(f"Freed {human(freed)} on /")
                self.set_status(f"Deleted {len(names)} snapshot(s), freed {human(freed)}")
            self.load_snapshots()
        self.in_thread("Deleting snapshots…", lambda: self.execute(steps, [], "Timeshift"), done)

    # ------------------------------------------------------------ flatpak

    def _build_flatpak(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "system-software-install-symbolic", "Flatpak apps",
            "Installed apps, how tightly each one is sandboxed, and switches to tighten or loosen "
            "permissions (your account only — like Flatseal).",
            [self.action_button("Refresh", lambda: self.load_flatpaks(check_updates=True),
                                icon_name="view-refresh-symbolic"),
             self.action_button("Repair", self.flatpak_repair, icon_name="emblem-synchronizing-symbolic"),
             self.action_button("Update all", self.flatpak_update, primary=True,
                                icon_name="software-update-available-symbolic")]), False, False, 0)
        sc = card("ecard")
        tiles = hbox(10)
        tiles.set_homogeneous(True)
        self.fp_stats = {}
        for key in ("APPS", "INSTALLED SIZE", "UPDATES", "LOOSE SANDBOXES"):
            t = vbox(2, "stat-tile")
            t.pack_start(label(key, ("kicker",), xalign=0.5), False, False, 0)
            v = label("…", ("tval",), xalign=0.5)
            t.pack_start(v, False, False, 0)
            tiles.pack_start(t, True, True, 0)
            self.fp_stats[key] = v
        sc.pack_start(tiles, False, False, 0)
        page.pack_start(sc, False, False, 0)
        page.pack_start(label("Sandbox ratings: STRONG = no broad file or device access; MODERATE = home "
                              "folder, all devices or X11; LOOSE = several of those or the whole filesystem. "
                              "Changes apply the next time the app starts.", ("faint",), wrap=True),
                        False, False, 0)
        self.fp_card, self.fp_list = list_card()
        page.pack_start(self.fp_card, False, False, 0)
        return scrolled(page)

    def load_flatpaks(self, check_updates=False):
        if not flatpaks.available():
            self._render_flatpaks([])
            return
        self.bg(flatpaks.apps, self._render_flatpaks)
        if check_updates:
            self.fp_stats["UPDATES"].set_text("…")
            self.bg(flatpaks.updates_available, self._render_fp_updates)

    def _render_fp_updates(self, n):
        v = self.fp_stats["UPDATES"]
        v.set_text("?" if n is None else str(n))
        ctx = v.get_style_context()
        for c in ("ok", "warn"):
            ctx.remove_class(c)
        ctx.add_class("warn" if n else "ok")

    def _render_flatpaks(self, apps):
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "fp_row", False)]
        for ch in self.fp_list.get_children():
            ch.destroy()
        apps = apps or []
        self.fp_apps = {a["id"]: a for a in apps}

        def to_bytes(sz):
            try:
                n, unit = sz.split()
                return float(n) * {"bytes": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(unit, 1)
            except ValueError:
                return 0
        self.fp_stats["APPS"].set_text(str(len(apps)))
        self.fp_stats["INSTALLED SIZE"].set_text(human(sum(to_bytes(a["size"]) for a in apps)))
        loose = sum(1 for a in apps if a["risk"][0] == "weak")
        lv = self.fp_stats["LOOSE SANDBOXES"]
        lv.set_text(str(loose))
        lctx = lv.get_style_context()
        for c in ("ok", "warn"):
            lctx.remove_class(c)
        lctx.add_class("warn" if loose else "ok")
        if not apps:
            row = Gtk.ListBoxRow(activatable=False)
            row.add(label("No Flatpak apps installed." if flatpaks.available() else "Flatpak is not installed.",
                          ("muted",)))
            self.fp_list.add(row)
        for a in apps:
            self.fp_list.add(self._fp_row(a))
        self.fp_list.show_all()

    def _fp_row(self, a):
        row = Gtk.ListBoxRow(activatable=False)
        outer = vbox(0)
        box = hbox(12)
        box.pack_start(self._app_icon(a["id"]), False, False, 0)
        text = vbox(2)
        th = hbox(8)
        th.pack_start(label(a["name"], ("h3",)), False, False, 0)
        rating, _score, why = a["risk"]
        kind = {"strong": "ok", "moderate": "warn", "weak": "fail"}[rating]
        th.pack_start(pill({"weak": "loose"}.get(rating, rating) + " sandbox", kind), False, False, 0)
        th.pack_start(pill(a["install"], "dim"), False, False, 0)
        if a["overridden"]:
            th.pack_start(pill("customised", "info"), False, False, 0)
        text.pack_start(th, False, False, 0)
        text.pack_start(label(f"{a['id']}  •  {a['version'] or '—'}  •  {a['origin']}  •  {a['size']}",
                              ("faint", "mono"), ellipsize=True), False, False, 0)
        if why:
            text.pack_start(label("Has access to: " + ", ".join(why), ("muted",), wrap=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        btn = self.action_button("Permissions", lambda: self._toggle_fp(a["id"], rev))
        btn.fp_row = True
        box.pack_end(btn, False, False, 0)
        outer.pack_start(box, False, False, 0)

        perms = vbox(2, "fp-perms")
        for pid, title, desc, _k, _g, _r, weight in flatpaks.PERMISSIONS:
            pr = hbox(10, "perm-row")
            t = vbox(1)
            tt = hbox(6)
            tt.pack_start(label(title, ("h3",)), False, False, 0)
            if weight >= 2:
                tt.pack_start(pill("broad", "warn"), False, False, 0)
            t.pack_start(tt, False, False, 0)
            t.pack_start(label(desc, ("faint",)), False, False, 0)
            pr.pack_start(t, True, True, 0)
            sw = Gtk.Switch(active=flatpaks.has(a["perms"], pid), valign=Gtk.Align.CENTER)
            sw.connect("notify::active", self._on_fp_perm, a, pid)
            pr.pack_end(sw, False, False, 0)
            perms.pack_start(pr, False, False, 0)
        acts = hbox(8)
        b1 = self.action_button("Reset to app defaults", lambda: self._fp_reset(a))
        b2 = self.action_button("Uninstall…", lambda: self._fp_uninstall(a), icon_name="user-trash-symbolic")
        b1.fp_row = b2.fp_row = True
        acts.pack_start(b1, False, False, 0)
        acts.pack_start(b2, False, False, 0)
        perms.pack_start(acts, False, False, 8)
        rev.add(perms)
        rev.set_reveal_child(a["id"] in self._fp_open)
        outer.pack_start(rev, False, False, 0)
        row.add(outer)
        return row

    def _toggle_fp(self, app_id, rev):
        show = not rev.get_reveal_child()
        rev.set_reveal_child(show)
        (self._fp_open.add if show else self._fp_open.discard)(app_id)

    def _on_fp_perm(self, sw, _pspec, a, pid):
        grant = sw.get_active()
        try:
            flatpaks.set_permission(a["id"], pid, grant)
        except Exception as e:
            self.log(f"Flatpak override failed for {a['id']}: {e}")
            self.set_status("Could not change permission", bad=True)
            sw.handler_block_by_func(self._on_fp_perm)
            sw.set_active(not grant)
            sw.handler_unblock_by_func(self._on_fp_perm)
            return
        title = flatpaks.PERM_BY_ID[pid][1]
        maintenance.note_flatpak(a["id"])
        maintenance.record("flatpak", f"{a['name']}: {title} {'allowed' if grant else 'blocked'}")
        self.log(f"Flatpak: {a['name']} — {title} {'allowed' if grant else 'blocked'} "
                 f"(flatpak override --user {flatpaks.PERM_BY_ID[pid][4 if grant else 5]} {a['id']})")
        self.set_status(f"{a['name']}: {title} {'allowed' if grant else 'blocked'} — restart the app to apply")
        self.load_flatpaks()

    def _fp_reset(self, a):
        try:
            flatpaks.reset(a["id"])
        except Exception as e:
            self.log(f"Reset failed for {a['id']}: {e}")
            self.set_status("Reset failed", bad=True)
            return
        maintenance.note_flatpak(a["id"], reset=True)
        maintenance.record("flatpak", f"{a['name']}: permissions reset to defaults")
        self.log(f"Flatpak: {a['name']} permissions reset to the app's defaults")
        self.set_status(f"{a['name']} permissions reset")
        self.load_flatpaks()

    def _fp_uninstall(self, a):
        if not self.confirm(f"Uninstall {a['name']}?",
                            f"<tt>{GLib.markup_escape_text(a['id'])}</tt> will be removed. Its data in "
                            "~/.var/app is kept."):
            return
        root, user = flatpaks.uninstall_steps(a)

        def done(results):
            self.notify_done(results or {}, f"Uninstall {a['name']}")
            self.load_flatpaks(check_updates=True)
            self.run_clean_scan()
        self.in_thread(f"Uninstalling {a['name']}…", lambda: self.execute(root, user, "Flatpak"), done)

    def flatpak_update(self):
        root, user = flatpaks.update_steps()

        def done(results):
            self.notify_done(results or {}, "Flatpak update")
            self.load_flatpaks(check_updates=True)
        self.in_thread("Updating Flatpaks…", lambda: self.execute(root, user, "Flatpak update"), done)

    def flatpak_repair(self):
        root, user = flatpaks.repair_steps()

        def done(results):
            self.notify_done(results or {}, "Flatpak repair")
            self.load_flatpaks()
            self.run_clean_scan()
        self.in_thread("Repairing Flatpak…", lambda: self.execute(root, user, "Flatpak repair"), done)

    # ------------------------------------------------------------ hardware

    def _build_hardware(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "drive-harddisk-symbolic", "Hardware Health",
            "SMART disk health and live temperatures, read through UDisks2 and kernel sensors — "
            "no password needed.",
            [self.action_button("Refresh", lambda: (self.load_drives(), self._poll_sensors(force=True)),
                                icon_name="view-refresh-symbolic"),
             self.action_button("Full SMART report", self.smart_report, icon_name="utilities-terminal-symbolic")]),
            False, False, 0)

        tc = card()
        th = hbox(8)
        th.pack_start(icon("weather-clear-symbolic", Gtk.IconSize.BUTTON, "warn"), False, False, 0)
        th.pack_start(label("Temperatures & Throttling", ("h2",)), False, False, 0)
        th.pack_end(label("updates every 2 s", ("faint", "mono")), False, False, 0)
        tc.pack_start(th, False, False, 0)
        self.throttle_box = vbox(4)
        tc.pack_start(self.throttle_box, False, False, 10)
        self.sensor_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                       min_children_per_line=2, max_children_per_line=5,
                                       column_spacing=10, row_spacing=10)
        tc.pack_start(self.sensor_flow, False, False, 0)
        self.sensor_tiles = {}
        page.pack_start(tc, False, False, 0)

        self.drives_head = label("DRIVES", ("kicker",))
        page.pack_start(self.drives_head, False, False, 0)
        self.drives_box = vbox(12)
        self.drives_box.pack_start(label("Reading SMART data…", ("faint",)), False, False, 0)
        page.pack_start(self.drives_box, False, False, 0)
        return scrolled(page)

    def load_drives(self):
        self.bg(hardware.drives, self._render_drives)

    def _render_drives(self, ds):
        for ch in self.drives_box.get_children():
            ch.destroy()
        ds = [d for d in (ds or []) if "error" not in d]
        self.drives = ds
        self.drives_head.set_text(f"DRIVES ({len(ds)})")
        if not ds:
            self.drives_box.pack_start(label("No drives found, or UDisks2 is not running.", ("faint",)),
                                       False, False, 0)
        word = {checks.OK: ("healthy", "ok"), checks.INFO: ("healthy", "ok"),
                checks.WARN: ("check", "warn"), checks.FAIL: ("failing", "fail")}
        for d in ds:
            c = card("drive-card", d["health"])
            h = hbox(12)
            ico = "drive-harddisk-solidstate-symbolic" if d["kind"] in ("NVMe", "SSD") else "drive-harddisk-symbolic"
            h.pack_start(icon(ico, Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
            t = vbox(2)
            t.set_valign(Gtk.Align.CENTER)
            t.pack_start(label(d["model"], ("h2",)), False, False, 0)
            meta = f"{d['device'] or '?'}  •  {human(d['size'])}"
            if d["rpm"]:
                meta += f"  •  {d['rpm']} rpm"
            if d["serial"]:
                meta += f"  •  S/N {d['serial']}"
            t.pack_start(label(meta, ("faint", "mono"), selectable=True), False, False, 0)
            h.pack_start(t, True, True, 0)
            w, k = word[d["health"]]
            h.pack_end(pill(w, k), False, False, 0)
            h.pack_end(pill(d["kind"], "blue" if d["kind"] == "NVMe" else "dim"), False, False, 0)
            c.pack_start(h, False, False, 0)
            if d["stats"]:
                tiles = hbox(8)
                tiles.set_homogeneous(True)
                for k2, v in d["stats"]:
                    tl = vbox(1, "stat-tile")
                    tl.pack_start(label(k2, ("kicker",), xalign=0.5), False, False, 0)
                    bad = (k2 in ("REALLOCATED", "PENDING", "UNCORRECTABLE", "ERRORS") and v not in ("0", "–"))
                    tl.pack_start(label(v, ("stat-val",) + (("tval", "warn") if bad else ()), xalign=0.5),
                                  False, False, 0)
                    tiles.pack_start(tl, True, True, 0)
                c.pack_start(tiles, False, False, 12)
            for lvl, text in d["notes"]:
                r = hbox(8)
                r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
                r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
                c.pack_start(r, False, False, 2)
            if not d["notes"] and d["stats"]:
                r = hbox(8)
                r.pack_start(icon("emblem-ok-symbolic", Gtk.IconSize.MENU, "ok"), False, False, 0)
                r.pack_start(label("No warning signs in SMART data", ("muted",)), True, True, 0)
                c.pack_start(r, False, False, 2)
            self.drives_box.pack_start(c, False, False, 0)
        self.drives_box.show_all()

    def _poll_sensors(self, force=False):
        visible = self.stack.get_visible_child_name() == "hardware"
        if (visible or force) and not self._sensor_busy:
            self._sensor_busy = True
            self.bg(lambda: (hardware.sensors(), hardware.throttling(), hardware.cpu_freq(), hardware.nvidia()),
                    self._render_sensors)
        return True

    def _render_sensors(self, res):
        self._sensor_busy = False
        if not res:
            return
        sens, thr, (cur, mx), gpu = res
        for ch in self.throttle_box.get_children():
            ch.destroy()
        for comp, lvl, text in thr:
            r = hbox(8)
            r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
            r.pack_start(label(comp, ("h3",)), False, False, 0)
            r.pack_start(label(text, ("muted",)), False, False, 0)
            self.throttle_box.pack_start(r, False, False, 0)
        self.throttle_box.show_all()

        tiles = []
        for s_ in sens:
            lvl = hardware.temp_level(s_)
            lim = []
            if s_["high"]:
                lim.append(f"high {s_['high']:.0f}°")
            if s_["crit"]:
                lim.append(f"crit {s_['crit']:.0f}°")
            pct = 100 * s_["temp"] / (s_["crit"] or 100)
            tiles.append((s_["label"], f"{s_['temp']:.0f}°C", lvl, " · ".join(lim) or "no limit reported", pct))
        if cur:
            tiles.append(("CPU · clock", f"{cur / 1000:.2f} GHz", checks.INFO,
                          f"max {mx / 1000:.2f} GHz" if mx else "", 100 * cur / mx if mx else 0))
        if gpu and gpu.get("util") is not None:
            extra = [f"{gpu['power']:.0f} W" if gpu.get("power") is not None else "",
                     f"fan {gpu['fan']:.0f}%" if gpu.get("fan") is not None else "",
                     f"{gpu['clock']:.0f} MHz" if gpu.get("clock") is not None else ""]
            tiles.append(("GPU · load", f"{gpu['util']:.0f}%", checks.INFO,
                          " · ".join(x for x in extra if x), gpu["util"]))
        keys = [t[0] for t in tiles]
        if keys != list(self.sensor_tiles):
            for ch in self.sensor_flow.get_children():
                ch.destroy()
            self.sensor_tiles = {}
            for key, *_ in tiles:
                box = vbox(4, "sensor-tile")
                box.pack_start(label(key.upper(), ("kicker",), ellipsize=True), False, False, 0)
                val = label("", ("tval",))
                box.pack_start(val, False, False, 0)
                bar = Bar(EMERALD, 5)
                box.pack_start(bar, False, False, 2)
                sub = label("", ("faint", "mono"), ellipsize=True)
                box.pack_start(sub, False, False, 0)
                self.sensor_flow.add(box)
                self.sensor_tiles[key] = (val, bar, sub)
            self.sensor_flow.show_all()
        colors = {checks.OK: EMERALD, checks.INFO: BLUE, checks.WARN: "#f59e0b", checks.FAIL: "#ef4444"}
        for key, value, lvl, sub, pct in tiles:
            val, bar, sublbl = self.sensor_tiles[key]
            val.set_text(value)
            ctx = val.get_style_context()
            for c in ("ok", "info", "warn", "fail"):
                ctx.remove_class(c)
            ctx.add_class(lvl)
            bar.color = colors[lvl]
            bar.set_value(pct)
            sublbl.set_text(sub)

    def smart_report(self):
        ds = [d for d in getattr(self, "drives", []) if d.get("device")]
        if not ds:
            self.set_status("No drives loaded yet")
            return
        steps = [(f"smart{i}", f"SMART report: {d['model']} ({d['device']})",
                  f"smartctl -x {q(d['device'])} || true") for i, d in enumerate(ds)]

        def done(results):
            self.notify_done(results or {}, "SMART report")
            self.goto("log")
        self.in_thread("Reading full SMART reports…", lambda: self.execute(steps, [], "SMART report"), done)

    # ------------------------------------------------------------ boot & startup

    def _build_startup(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "system-run-symbolic", "Boot & Startup",
            "How long boot takes, what slows it down, and which apps start when you log in.",
            [self.action_button("Refresh", self.load_boot, icon_name="view-refresh-symbolic")]), False, False, 0)

        bc = card("ecard")
        row = hbox(20)
        left = vbox(2)
        left.pack_start(label("LAST BOOT", ("kicker",)), False, False, 0)
        self.boot_total = label("…", ("boot-big",))
        left.pack_start(self.boot_total, False, False, 0)
        row.pack_start(left, False, False, 0)
        right = vbox(8)
        right.set_valign(Gtk.Align.CENTER)
        self.boot_bar = StackedBar(14)
        right.pack_start(self.boot_bar, False, False, 0)
        self.boot_legend = hbox(16)
        right.pack_start(self.boot_legend, False, False, 0)
        row.pack_start(right, True, True, 0)
        bc.pack_start(row, False, False, 0)
        self.boot_tip = label("", ("muted",), wrap=True)
        bc.pack_start(self.boot_tip, False, False, 10)
        page.pack_start(bc, False, False, 0)

        page.pack_start(label("SLOWEST TO START", ("kicker",)), False, False, 0)
        page.pack_start(label("Services start in parallel, so times overlap. Only those marked "
                              "BLOCKS BOOT actually delay reaching the desktop.", ("faint",), wrap=True),
                        False, False, 0)
        self.blame_card, self.blame_list = list_card()
        page.pack_start(self.blame_card, False, False, 0)

        self.auto_head = label("STARTUP APPLICATIONS", ("kicker",))
        page.pack_start(self.auto_head, False, False, 0)
        page.pack_start(label("Switching an app off only affects your account and takes effect at next login. "
                              "This is the same setting as Cinnamon's Startup Applications.", ("faint",), wrap=True),
                        False, False, 0)
        self.auto_card, self.auto_list = list_card()
        page.pack_start(self.auto_card, False, False, 0)
        self.ess_exp = Gtk.Expander(label="Desktop components (locked)")
        self.ess_card, self.ess_list = list_card()
        self.ess_exp.add(self.ess_card)
        page.pack_start(self.ess_exp, False, False, 0)
        return scrolled(page)

    def load_boot(self):
        self.bg(lambda: (boot.times(), boot.blame(), autostart.entries()), self._render_boot)

    def _render_boot(self, res):
        if not res:
            return
        times, rows, apps = res
        segs = [("firmware", "Firmware", VIOLET), ("loader", "Boot loader", BLUE),
                ("kernel", "Kernel", ORANGE), ("initrd", "initrd", "#f472b6"), ("userspace", "Services", EMERALD)]
        self.boot_total.set_text(f"{times['total']:.1f} s" if times.get("total") else "n/a")
        self.boot_bar.set_segments([(times.get(k, 0), col) for k, _, col in segs])
        for ch in self.boot_legend.get_children():
            ch.destroy()
        for k, name, col in segs:
            if times.get(k):
                b = self._legend(f"{name} {times[k]:.1f} s", col)
                self.boot_legend.pack_start(b, False, False, 0)
        self.boot_legend.show_all()
        tips = []
        if times.get("firmware", 0) >= 10:
            tips.append(f"{times['firmware']:.1f} s is spent in your BIOS/UEFI before Linux starts. Enabling "
                        "“Fast Boot”, disabling unused boot devices (network/PXE boot) and unused controllers "
                        "in the UEFI setup usually shortens this — MintCare can't change it from Linux.")
        crit = [r for r in rows if r["critical"] and r["can_toggle"] and r["enabled"]]
        if crit:
            tips.append(f"{crit[0]['unit']} is on the critical path and adds {crit[0]['secs']:.1f} s.")
        self.boot_tip.set_text(" ".join(tips) or "Boot looks lean — nothing obvious to trim.")

        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "boot_row", False)]
        for ch in self.blame_list.get_children():
            ch.destroy()
        for r in rows:
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            box.pack_start(label(f"{r['secs']:.2f}s", ("time-val",) + (("warn",) if r["critical"] else ()),
                                 xalign=1.0), False, False, 0)
            text = vbox(2)
            th = hbox(8)
            th.pack_start(label(r["unit"], ("unit-name",), ellipsize=True), False, False, 0)
            if r["critical"]:
                th.pack_start(pill("blocks boot", "warn"), False, False, 0)
            if r["enabled"] is False:
                th.pack_start(pill("disabled", "dim"), False, False, 0)
            text.pack_start(th, False, False, 0)
            if r["desc"] and r["desc"] != r["unit"]:
                text.pack_start(label(r["desc"], ("muted",), ellipsize=True), False, False, 0)
            if r["advice"]:
                text.pack_start(label(r["advice"], ("faint",), wrap=True), False, False, 0)
            box.pack_start(text, True, True, 0)
            if r["can_toggle"] and r["enabled"] is not None:
                b = self.action_button("Enable" if not r["enabled"] else "Disable",
                                       lambda rr=r: self.toggle_unit(rr))
                b.boot_row = True
                box.pack_end(b, False, False, 0)
            row.add(box)
            self.blame_list.add(row)
        self.blame_list.show_all()

        for lst in (self.auto_list, self.ess_list):
            for ch in lst.get_children():
                ch.destroy()
        normal = [a for a in apps if not a["essential"]]
        ess = [a for a in apps if a["essential"]]
        on = sum(1 for a in normal if a["enabled"])
        self.auto_head.set_text(f"STARTUP APPLICATIONS ({on} of {len(normal)} on)")
        self.ess_exp.set_label(f"Desktop components ({len(ess)}, locked)")
        for a in normal:
            self.auto_list.add(self._app_row(a, locked=False))
        for a in ess:
            self.ess_list.add(self._app_row(a, locked=True))
        self.auto_list.show_all()
        self.ess_list.show_all()

    def _app_icon(self, name):
        img = None
        if name and os.path.isabs(name):
            try:
                img = Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(name, 28, 28))
            except GLib.Error:
                img = None
        elif name and Gtk.IconTheme.get_default().has_icon(name):
            img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DND)
            img.set_pixel_size(28)
        if img is None:
            img = icon("application-x-executable-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico")
        return img

    def _app_row(self, a, locked):
        row = Gtk.ListBoxRow(activatable=False)
        box = hbox(12)
        box.pack_start(self._app_icon(a["icon"]), False, False, 0)
        text = vbox(2)
        th = hbox(8)
        th.pack_start(label(a["name"], ("h3",)), False, False, 0)
        if a["origin"] == "user":
            th.pack_start(pill("added by you", "info"), False, False, 0)
        elif a["overridden"]:
            th.pack_start(pill("customised", "dim"), False, False, 0)
        if a["delay"]:
            th.pack_start(pill(f"delay {a['delay']}s", "dim"), False, False, 0)
        text.pack_start(th, False, False, 0)
        if a["comment"]:
            text.pack_start(label(a["comment"], ("muted",), ellipsize=True), False, False, 0)
        text.pack_start(label(a["exec"], ("faint", "mono"), ellipsize=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        sw = Gtk.Switch(active=a["enabled"], valign=Gtk.Align.CENTER, sensitive=not locked)
        if locked:
            sw.set_tooltip_text("Part of the desktop; switching it off can break shortcuts, keyrings or logins")
        sw.connect("notify::active", self._on_app_toggle, a)
        box.pack_end(sw, False, False, 0)
        row.add(box)
        return row

    def _on_app_toggle(self, sw, _pspec, a):
        on = sw.get_active()
        had_user_file = (autostart.USER_DIR / a["file"]).exists()
        try:
            autostart.set_enabled(a["file"], on)
        except Exception as e:
            self.log(f"Could not change startup entry {a['file']}: {e}")
            self.set_status(f"Could not change {a['name']}", bad=True)
            sw.handler_block_by_func(self._on_app_toggle)
            sw.set_active(not on)
            sw.handler_unblock_by_func(self._on_app_toggle)
            return
        a["enabled"] = on
        maintenance.note_autostart(a["file"], on, had_user_file)
        maintenance.record("startup", f"Startup app {a['name']} {'enabled' if on else 'disabled'}")
        self.log(f"Startup: {a['name']} {'enabled' if on else 'disabled'} "
                 f"(~/.config/autostart/{a['file']})")
        self.set_status(f"{a['name']} will {'start' if on else 'no longer start'} at login")

    def toggle_unit(self, r):
        disabling = bool(r["enabled"])
        verb = "Disable" if disabling else "Enable"
        body = GLib.markup_escape_text(r["advice"] or r["desc"] or "")
        if not self.confirm(f"{verb} {r['unit']}?", body):
            return
        script = boot.disable_script(r["unit"]) if disabling else boot.enable_script(r["unit"])
        steps = [("unit", f"{verb} {r['unit']}", script)]
        snap = self.ask_snapshot(f"a change to {r['unit']}")
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, f"{verb} {r['unit']}")
            self.load_boot()
            self.run_diagnose()
        self.in_thread(f"{verb[:-1]}ing {r['unit']}…", lambda: self.execute(steps, [], "Boot services", snap), done)

    # ------------------------------------------------------------ maintenance

    def _build_maintenance(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "alarm-symbolic", "Maintenance",
            "Automatic check-ups, safety snapshots, one-click undo and a record of every change "
            "MintCare makes."), False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        page.pack_start(grid, False, False, 0)
        self._maint_loading = True

        # scheduled check-up
        sc = card("ecard")
        h = hbox(8)
        h.pack_start(icon("alarm-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        h.pack_start(label("Scheduled check-up", ("h2",)), True, True, 0)
        self.sched_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sched_switch.connect("notify::active", self._on_sched_toggle)
        h.pack_end(self.sched_switch, False, False, 0)
        sc.pack_start(h, False, False, 0)
        sc.pack_start(label("Runs every check in the background and tells you only when something needs "
                            "attention. Uses a systemd user timer — no password needed.", ("faint",), wrap=True),
                      False, False, 6)
        g = Gtk.Grid(column_spacing=12, row_spacing=8)
        g.attach(label("How often", ("muted",)), 0, 0, 1, 1)
        self.sched_freq = Gtk.ComboBoxText()
        for k, t in (("daily", "Every day"), ("weekly", "Every Monday"), ("monthly", "1st of each month")):
            self.sched_freq.append(k, t)
        self.sched_freq.connect("changed", lambda *_: self._save_maint_settings())
        g.attach(self.sched_freq, 1, 0, 1, 1)
        g.attach(label("Notify me", ("muted",)), 0, 1, 1, 1)
        self.sched_notify = Gtk.ComboBoxText()
        self.sched_notify.append("issues", "Only when something needs attention")
        self.sched_notify.append("always", "After every check-up")
        self.sched_notify.connect("changed", lambda *_: self._save_maint_settings())
        g.attach(self.sched_notify, 1, 1, 1, 1)
        g.attach(label("Also clean", ("muted",)), 0, 2, 1, 1)
        cl = vbox(2)
        self.sched_clean = {}
        for k, t in (("thumbs", "Thumbnail cache"), ("usercache", "Stale application cache (30+ days)"),
                     ("browser", "Browser caches (skipped while a browser is open)")):
            cb = Gtk.CheckButton(label=t)
            cb.connect("toggled", lambda *_: self._save_maint_settings())
            self.sched_clean[k] = cb
            cl.pack_start(cb, False, False, 0)
        g.attach(cl, 1, 2, 1, 1)
        sc.pack_start(g, False, False, 6)
        self.sched_status = label("", ("muted",), wrap=True)
        sc.pack_start(self.sched_status, False, False, 4)
        self.sched_last = label("", ("faint",), wrap=True)
        sc.pack_start(self.sched_last, False, False, 0)
        row = hbox(8)
        row.pack_end(self.action_button("Run check-up now", self.run_checkup_now, primary=True,
                                        icon_name="media-playback-start-symbolic"), False, False, 0)
        sc.pack_end(row, False, False, 0)

        # safety snapshots
        sf = card()
        h = hbox(8)
        h.pack_start(icon("document-open-recent-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        h.pack_start(label("Safety snapshots", ("h2",)), True, True, 0)
        sf.pack_start(h, False, False, 0)
        sf.pack_start(label("Before fixes, optimisations, boot-service changes and Undo, MintCare can take a "
                            "Timeshift snapshot so the whole system can be rolled back.", ("faint",), wrap=True),
                      False, False, 6)
        r = hbox(10)
        r.pack_start(label("Before big changes", ("muted",)), False, False, 0)
        self.snap_pref = Gtk.ComboBoxText()
        for k, t in (("ask", "Ask me"), ("always", "Always take a snapshot"), ("never", "Never")):
            self.snap_pref.append(k, t)
        self.snap_pref.connect("changed", lambda *_: self._save_maint_settings())
        r.pack_start(self.snap_pref, False, False, 0)
        sf.pack_start(r, False, False, 6)
        self.snap_ready = label("", ("muted",), wrap=True)
        sf.pack_start(self.snap_ready, False, False, 4)
        row = hbox(8)
        row.pack_end(self.action_button("Take snapshot now", self.snapshot_now,
                                        icon_name="document-save-symbolic"), False, False, 0)
        sf.pack_end(row, False, False, 0)

        # undo
        uc = card()
        h = hbox(10)
        h.pack_start(icon("edit-undo-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        t.pack_start(label("Undo MintCare changes", ("h2",)), False, False, 0)
        self.undo_summary = label("", ("muted",), wrap=True)
        t.pack_start(self.undo_summary, False, False, 0)
        h.pack_start(t, True, True, 0)
        h.pack_end(self.action_button("Undo…", self.undo_all, icon_name="edit-undo-symbolic"), False, False, 0)
        uc.pack_start(h, False, False, 0)
        two = [(sc, 0, 0, 1, 1), (sf, 1, 0, 1, 1), (uc, 0, 1, 2, 1)]
        stacked = [(sc, 0, 0, 1, 1), (sf, 0, 1, 1, 1), (uc, 0, 2, 1, 1)]
        self._responsive.append((grid, {"wide": two, "medium": stacked, "narrow": stacked}))

        # history
        hh = hbox(8)
        self.hist_head = label("HISTORY", ("kicker",))
        hh.pack_start(self.hist_head, True, True, 0)
        hh.pack_end(self.action_button("Clear history", self._clear_history), False, False, 0)
        page.pack_start(hh, False, False, 0)
        self.hist_card, self.hist_list = list_card()
        page.pack_start(self.hist_card, False, False, 0)
        self._maint_loading = False
        return scrolled(page)

    def _maint_if_visible(self):
        if self.stack.get_visible_child_name() == "maintenance":
            self.refresh_maintenance()
        return False

    def refresh_maintenance(self):
        def work():
            return (maintenance.timer_status(), maintenance.last_run(), maintenance.timeshift_ready(),
                    maintenance.undo_inventory(), maintenance.history(300))
        self.bg(work, self._render_maintenance)

    def _render_maintenance(self, res):
        if not res:
            return
        ts, lr, (ready, why), inv, events = res
        self._maint_loading = True
        st = maintenance.load_settings()
        m = st["maintenance"]
        self.sched_freq.set_active_id(m.get("frequency", "weekly"))
        self.sched_notify.set_active_id(m.get("notify", "issues"))
        for k, cb in self.sched_clean.items():
            cb.set_active(k in m.get("clean", []))
        self.snap_pref.set_active_id(st.get("snapshot_before", "ask"))
        self.sched_switch.set_active(ts["enabled"])
        self.sched_status.set_markup(
            f"<b>On.</b> Next check-up: {GLib.markup_escape_text(ts['next'] or 'soon')}" if ts["enabled"]
            else "Off. Turn it on to get a notification when your system needs attention.")
        if lr:
            when = time.strftime("%a %d %b %H:%M", time.localtime(lr["ts"]))
            issues = lr.get("issues", [])
            self.sched_last.set_text(f"Last check-up {when}: " +
                                     (f"{len(issues)} issue(s) — " + ", ".join(t for t, _ in issues[:3])
                                      if issues else "no issues") +
                                     (f"; freed {human(lr['freed'])}" if lr.get("freed", 0) > 1 << 20 else ""))
        else:
            self.sched_last.set_text("No check-up has run yet.")
        self.snap_ready.set_text("Timeshift is ready." if ready else why + " Snapshots will be skipped.")
        self._maint_loading = False

        counts = {}
        for kind, _k, _t in inv:
            counts[kind] = counts.get(kind, 0) + 1
        names = {"tweak": "optimisation", "unit": "boot service", "autostart": "startup app",
                 "flatpak": "Flatpak app"}
        self.undo_summary.set_text(
            ("MintCare has changed " + ", ".join(f"{n} {names[k]}{'s' if n != 1 else ''}"
                                                for k, n in counts.items()) +
             ". Choose what to put back the way it was.") if inv else
            "Nothing to undo — MintCare hasn't changed anything that's still active.")

        for ch in self.hist_list.get_children():
            ch.destroy()
        self.hist_head.set_text(f"HISTORY ({len(events)})")
        if not events:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label("Nothing recorded yet. Every fix, tweak, clean-up and undo will appear here.", ("muted",)))
            self.hist_list.add(r)
        kinds = {"fix": ("repair", "ok"), "tweak": ("tweak", "info"), "clean": ("clean", "ok"),
                 "service": ("service", "warn"), "flatpak": ("flatpak", "blue"), "snapshot": ("snapshot", "info"),
                 "undo": ("undo", "warn"), "startup": ("startup", "dim"), "trash": ("trash", "orange"),
                 "scheduled": ("check-up", "ok"), "other": ("other", "dim")}
        last_day = None
        for e in events:
            day = time.strftime("%A %d %B %Y", time.localtime(e["ts"]))
            if day != last_day:
                hr = Gtk.ListBoxRow(activatable=False)
                css(hr, "dup-head")
                hr.add(label(day.upper(), ("kicker",)))
                self.hist_list.add(hr)
                last_day = day
            r = Gtk.ListBoxRow(activatable=False)
            b = hbox(10)
            ok = e.get("ok", True)
            b.pack_start(icon("emblem-ok-symbolic" if ok else "dialog-error-symbolic", Gtk.IconSize.MENU,
                              "ok" if ok else "fail"), False, False, 0)
            b.pack_start(label(time.strftime("%H:%M", time.localtime(e["ts"])), ("faint", "mono")), False, False, 0)
            tl = label(e["title"], ("muted",), ellipsize=True)
            if e.get("detail"):
                tl.set_tooltip_text(e["detail"])
            b.pack_start(tl, True, True, 0)
            word, k = kinds.get(e.get("kind"), ("other", "dim"))
            b.pack_end(pill(word, k), False, False, 0)
            if not ok:
                b.pack_end(pill("failed", "fail"), False, False, 0)
            r.add(b)
            self.hist_list.add(r)
        self.hist_list.show_all()

    def _save_maint_settings(self):
        if self._maint_loading:
            return
        st = maintenance.load_settings()
        m = st["maintenance"]
        m["frequency"] = self.sched_freq.get_active_id() or "weekly"
        m["notify"] = self.sched_notify.get_active_id() or "issues"
        m["clean"] = [k for k, cb in self.sched_clean.items() if cb.get_active()]
        st["snapshot_before"] = self.snap_pref.get_active_id() or "ask"
        maintenance.save_settings(st)
        if self.sched_switch.get_active():
            try:
                maintenance.install_timer(m["frequency"])
            except Exception as e:
                self.log(f"Could not update the check-up timer: {e}")
        self.set_status("Maintenance settings saved")

    def _on_sched_toggle(self, sw, _pspec):
        if self._maint_loading:
            return
        try:
            if sw.get_active():
                self._save_maint_settings()
                maintenance.install_timer(maintenance.load_settings()["maintenance"]["frequency"])
                maintenance.record("scheduled", "Scheduled check-up turned on")
                self.set_status("Scheduled check-up turned on")
            else:
                maintenance.remove_timer()
                maintenance.record("scheduled", "Scheduled check-up turned off")
                self.set_status("Scheduled check-up turned off")
        except Exception as e:
            self.log(f"Scheduled check-up: {e}")
            self.set_status("Could not change the schedule", bad=True)
        self.refresh_maintenance()

    def run_checkup_now(self):
        def done(report):
            if report:
                n = len(report["issues"])
                self.set_status(f"Check-up done: {n} issue(s)" if n else "Check-up done: all good")
            self.refresh_maintenance()
            self.run_diagnose()
            self.run_clean_scan()
        self.in_thread("Running check-up…",
                       lambda: maintenance.run_maintenance(notify=False, on_line=self.log), done)

    def snapshot_now(self):
        ready, why = maintenance.timeshift_ready()
        if not ready:
            self.set_status(why, bad=True)
            return
        step = maintenance.snapshot_step("a manual MintCare snapshot")

        def done(results):
            self.notify_done(results or {}, "Snapshot")
            self.load_snapshots()
        self.in_thread("Taking a Timeshift snapshot…", lambda: self.execute([step], [], "Snapshot"), done)

    def _clear_history(self):
        if self.confirm("Clear the change history?", "This only clears the list; it doesn't undo anything."):
            maintenance.clear_history()
            self.refresh_maintenance()

    def undo_all(self):
        inv = maintenance.undo_inventory()
        if not inv:
            self.set_status("Nothing to undo")
            return
        d = Gtk.Dialog(title="Undo MintCare changes", transient_for=self, modal=True)
        css(d, "mintcare")
        d.set_default_size(560, 460)
        d.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Undo selected", Gtk.ResponseType.OK)
        css(d.get_widget_for_response(Gtk.ResponseType.OK), "primary")
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        box.pack_start(label("Untick anything you want to keep. Optimisations and boot services need your "
                             "password; startup apps and Flatpak permissions don't.", ("muted",), wrap=True),
                       False, False, 0)
        lst = vbox(4)
        checks_ = []
        groups = {"tweak": "Optimisations", "unit": "Boot services", "autostart": "Startup apps",
                  "flatpak": "Flatpak permissions"}
        for kind, title in groups.items():
            mine = [i for i in inv if i[0] == kind]
            if not mine:
                continue
            lst.pack_start(label(title.upper(), ("kicker",)), False, False, 6)
            for item in mine:
                text = item[2].split(": ", 1)[-1] if kind == "tweak" else item[2]
                cb = Gtk.CheckButton(label=text, active=True)
                lst.pack_start(cb, False, False, 0)
                checks_.append((cb, item))
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(lst)
        box.pack_start(sw, True, True, 0)
        d.show_all()
        resp = d.run()
        chosen = [item for cb, item in checks_ if cb.get_active()]
        d.destroy()
        if resp != Gtk.ResponseType.OK or not chosen:
            return
        root = []
        for kind, key, title in chosen:
            if kind == "tweak":
                t = tweaks.BY_ID[key]
                root.append(("undo-" + key, "Undo: " + t.title, t.revert_script()))
            elif kind == "unit":
                root.append(("undo-" + key, f"Undo: re-enable {key}", boot.enable_script(key)))
        user_items = [(k, key, t) for k, key, t in chosen if k in ("autostart", "flatpak")]
        snap = self.ask_snapshot("an undo of MintCare changes") if root else []
        if snap is None:
            return

        def work():
            for kind, key, title in user_items:
                try:
                    maintenance.undo_user_item(kind, key)
                    self.log(f"Undone: {title}")
                    maintenance.record("undo", title, True)
                except Exception as e:
                    self.log(f"Could not undo {title}: {e}")
                    maintenance.record("undo", title, False, str(e))
            return self.execute(root, [], "Undo", snap) if root else {}

        def done(results):
            self.notify_done(results or {}, "Undo")
            self._refresh_tweaks()
            self.refresh_maintenance()
            self.run_diagnose()
            if self._boot_loaded:
                self.load_boot()
            if self._fp_loaded:
                self.load_flatpaks()
        self.in_thread("Undoing changes…", work, done)

    # ------------------------------------------------------------ log

    def _build_log(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header("utilities-terminal-symbolic", "Activity log",
                                         "Full output of every command MintCare runs."), False, False, 0)
        c = card("tight")
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.log_view, "log")
        self.log_buf = self.log_view.get_buffer()
        self.log_buf.set_text(f"MintCare {__version__}\n")
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.log_view)
        c.pack_start(sw, True, True, 0)
        page.pack_start(c, True, True, 0)
        return page


class MintCareApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.mintcare.MintCare")

    def do_activate(self):
        win = self.get_active_window() or MintCareWindow(self)
        win.present()


def main():
    return MintCareApp().run(None)
