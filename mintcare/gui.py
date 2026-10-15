"""GTK 3 interface, styled after MintCare Companion (dark, emerald accent, live gauges)."""

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Gdk, Pango  # noqa: E402

from .i18n import N_, _, ngettext
from . import (__version__, assistant, autostart, backup, bench, boot, checks, cleaner, firmware,  # noqa: E402
               flatpaks, hardware, maintenance, packages, ports, processes, secaudit, selfupdate, sources,
               space, theme, troubleshoot, tweaks, widgets)
from .runner import run_steps  # noqa: E402
from .stats import Sampler  # noqa: E402
from .util import human, q, read, sysctl  # noqa: E402
from .widgets import BLUE, EMERALD, ORANGE, VIOLET, Bar, LineChart, RingGauge, StackedBar, Treemap, describe  # noqa: E402

STATUS_ICON = {
    checks.OK: ("emblem-ok-symbolic", "ok"),
    checks.INFO: ("dialog-information-symbolic", "info"),
    checks.WARN: ("dialog-warning-symbolic", "warn"),
    checks.FAIL: ("dialog-error-symbolic", "fail"),
}
# Job headings are English keys (see maintenance.KIND_FOR_HEADING); they are translated where shown.
_HEADINGS = (N_("Assistant"), N_("Boot services"), N_("Clean"), N_("Firmware"), N_("Flatpak"),
             N_("Flatpak repair"), N_("Flatpak update"), N_("Optimise"), N_("Packages"), N_("Processes"),
             N_("Repair"), N_("SMART report"), N_("Security"), N_("Snapshot"), N_("Sources"),
             N_("Timeshift"), N_("Troubleshoot"), N_("Undo"), N_("Update"))
STATUS_WORD = {checks.OK: _("OK"), checks.INFO: _("TIP"), checks.WARN: _("WARN"), checks.FAIL: _("FAIL")}

NAV = [
    ("dashboard", _("Dashboard"), "view-grid-symbolic"),
    ("diagnose", _("Diagnose & Fix"), "system-search-symbolic"),
    ("optimise", _("Optimise"), "power-profile-performance-symbolic"),
    ("clean", _("Clean"), "user-trash-symbolic"),
    ("space", _("Disk space"), "drive-multidisk-symbolic"),
    ("software", _("Software & updates"), "software-update-available-symbolic"),
    ("packages", _("Packages"), "package-x-generic-symbolic"),
    ("flatpak", _("Flatpak apps"), "system-software-install-symbolic"),
    ("processes", _("Processes"), "utilities-system-monitor-symbolic"),
    ("hardware", _("Hardware"), "drive-harddisk-symbolic"),
    ("security", _("Security"), "security-high-symbolic"),
    ("troubleshoot", _("Troubleshoot"), "dialog-question-symbolic"),
    ("startup", _("Boot & Startup"), "system-run-symbolic"),
    ("backup", _("Backup"), "document-save-symbolic"),
    ("maintenance", _("Maintenance"), "alarm-symbolic"),
    ("assistant", _("Assistant"), "user-available-symbolic"),
    ("log", _("Activity log"), "utilities-terminal-symbolic"),
]

# Pages and tweaks shown at each experience level (advanced shows everything).
LEVELS = [
    ("beginner", _("Beginner"), _("Essentials only: health check, recommended tweaks, cleaning.")),
    ("intermediate", _("Intermediate"), _("Everything except the most technical tweaks.")),
    ("advanced", _("Advanced"), _("Every page and every tweak.")),
]
BEGINNER_PAGES = {"dashboard", "diagnose", "optimise", "clean", "space", "security", "troubleshoot", "backup",
                  "maintenance", "assistant", "log"}
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
        self._sw_loaded = self._sec_loaded = False
        self.update_info = None
        self.root_port_names = {}

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
        self.stack.add_named(self._build_software(), "software")
        self.stack.add_named(self._build_packages(), "packages")
        self.stack.add_named(self._build_flatpak(), "flatpak")
        self.stack.add_named(self._build_processes(), "processes")
        self.stack.add_named(self._build_hardware(), "hardware")
        self.stack.add_named(self._build_security(), "security")
        self.stack.add_named(self._build_troubleshoot(), "troubleshoot")
        self.stack.add_named(self._build_startup(), "startup")
        self.stack.add_named(self._build_backup(), "backup")
        self.stack.add_named(self._build_maintenance(), "maintenance")
        self.stack.add_named(self._build_assistant(), "assistant")
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
        self.connect("delete-event", self._on_delete)
        if self.prefs.get("tray") or getattr(app, "start_in_tray", False):
            self.set_tray(True)
        self._install_shortcuts()
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
        GLib.timeout_add_seconds(4, self.check_mintcare_update)

    # ------------------------------------------------------------ chrome

    def _build_header(self):
        hb = Gtk.HeaderBar(show_close_button=True)
        brand = hbox(12)
        logo = label("M", ("logo",), xalign=0.5)
        logo.set_valign(Gtk.Align.CENTER)
        brand.pack_start(logo, False, False, 0)
        titles = vbox(1)
        titles.set_valign(Gtk.Align.CENTER)
        titles.pack_start(label(_("MintCare"), ("app-title",)), False, False, 0)
        titles.pack_start(label(_("LINUX MINT CLEAN · OPTIMISE · REPAIR"), ("app-sub",)), False, False, 0)
        brand.pack_start(titles, False, False, 0)
        hb.pack_start(brand)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search  (Ctrl+F)"), width_chars=24, valign=Gtk.Align.CENTER)
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
                (N_("HOSTNAME"), socket.gethostname()),
                (N_("SYSTEM"), osr.get("PRETTY_NAME", "Linux").strip('"')),
                (N_("KERNEL"), platform.release()),
                ("TCP", sysctl("net.ipv4.tcp_congestion_control").upper()),
                (N_("CPU TEMP"), "–")]):
            if i:
                sep = css(Gtk.Box(), "vsep")
                sep.set_size_request(1, 24)
                sep.set_valign(Gtk.Align.CENTER)
                chips.pack_start(sep, False, False, 0)
                self.chip_boxes.setdefault(key + "-sep", sep)
            c = vbox(1)
            c.set_valign(Gtk.Align.CENTER)
            c.pack_start(label(_(key), ("kicker",)), False, False, 0)
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
        menu_btn.set_tooltip_text(_("Appearance, experience level, tray and report"))
        describe(menu_btn, _("Main menu"))
        css(menu_btn, "ghost")
        menu_btn.set_popover(self._build_app_menu(menu_btn))
        hb.pack_end(menu_btn)
        hb.pack_end(chip_scroller)
        return hb

    def _build_app_menu(self, relative):
        pop = Gtk.Popover(relative_to=relative)
        box = vbox(4)
        box.set_border_width(14)
        box.pack_start(label(_("APPEARANCE"), ("kicker",)), False, False, 0)
        first = None
        self.theme_radios = {}
        for key, title in theme.NAMES:
            rb = Gtk.RadioButton.new_with_label_from_widget(first, title)
            first = first or rb
            rb.connect("toggled", lambda w, k=key: w.get_active() and self._set_pref("theme", k))
            self.theme_radios[key] = rb
            box.pack_start(rb, False, False, 0)
        box.pack_start(label(_("EXPERIENCE LEVEL"), ("kicker",)), False, False, 8)
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
        box.pack_start(label(_("SYSTEM TRAY"), ("kicker",)), False, False, 8)
        self.tray_check = Gtk.CheckButton(label=_("Show MintCare in the system tray"))
        self.tray_check.connect("toggled", lambda w: self._set_pref("tray", w.get_active()))
        box.pack_start(self.tray_check, False, False, 0)
        self.tray_login_check = Gtk.CheckButton(label=_("Start in the tray when I log in"))
        self.tray_login_check.connect("toggled", lambda w: self._set_pref("tray_autostart", w.get_active()))
        box.pack_start(self.tray_login_check, False, False, 0)
        box.pack_start(label(_("UPDATES"), ("kicker",)), False, False, 8)
        self.updates_check = Gtk.CheckButton(label=_("Check GitHub for new MintCare versions"))
        self.updates_check.connect("toggled", lambda w: self._set_pref("check_updates", w.get_active()))
        box.pack_start(self.updates_check, False, False, 0)
        chk = Gtk.ModelButton(text=_("Check for MintCare updates now"))
        chk.connect("clicked", lambda *_x: self.check_mintcare_update(force=True))
        box.pack_start(chk, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 8)
        exp = Gtk.ModelButton(text=_("Export health report…"))
        exp.connect("clicked", lambda *_x: self.export_report())
        box.pack_start(exp, False, False, 0)
        keys = Gtk.ModelButton(text=_("Keyboard shortcuts"))
        keys.connect("clicked", lambda *_x: self.show_shortcuts())
        box.pack_start(keys, False, False, 0)
        about = Gtk.ModelButton(text=_("About MintCare"))
        about.connect("clicked", lambda *_x: self.show_about())
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
        self.updates_check.set_active(self.prefs.get("check_updates", True))
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
            self.set_status(_('{0} mode').format(value.capitalize()))
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
                items.append(("page", title, _("Go to page"), ico, (key, None), title))
        for c in checks.CHECKS:
            r = self.check_results.get(c.id)
            items.append(("check", c.title, r.summary if r else c.category, "system-search-symbolic",
                          ("diagnose", ("check", c.id)), f"{c.category} {c.id}"))
        for t in tweaks.TWEAKS:
            if self.tweak_rows[t.id].get_visible():
                items.append(("tweak", t.title, t.description, "power-profile-performance-symbolic",
                              ("optimise", ("tweak", t.id)), f"{t.category} {t.id}"))
        for p in tweaks.PROFILES:
            items.append(("profile", _('{0} profile').format(p.title), p.description, p.icon,
                          ("optimise", ("profile", p.id)), "profile preset"))
        for i in cleaner.ITEMS:
            if i.id in getattr(self, "clean_rows", {}):
                items.append(("clean", i.title, i.description, "user-trash-symbolic",
                              ("clean", ("clean", i.id)), "clean junk cache"))
        for key, title in theme.NAMES:
            items.append(("setting", _('{0} theme').format(title), _("Change the appearance"), "weather-clear-symbolic",
                          (None, ("theme", key)), "theme appearance colour color dark light contrast"))
        for key, title, desc in LEVELS:
            items.append(("setting", _('{0} mode').format(title), desc, "preferences-system-symbolic",
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
            row.add(label(_('Nothing matches “{0}”.').format(entry.get_text().strip()), ("muted",)))
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
            set_pill(self.level_pill, _('{0} mode').format(next((t for k, t, _d in LEVELS if k == level), level)), "dim")
        cur = self.stack.get_visible_child_name()
        if not self.nav_rows[cur].get_visible():
            self.goto("dashboard")

    def _build_sidebar(self):
        side = vbox(4, "sidebar")
        side.set_size_request(220, -1)
        self.sidebar = side
        self.nav_kicker = label(_("NAVIGATION"), ("kicker",))
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
            describe(row, title)                      # the label is hidden in the narrow layout
            b.pack_start(nl, True, True, 0)
            badge = label("", ("nav-badge",))
            badge.set_no_show_all(True)
            self.nav_badges[key] = badge
            b.pack_end(badge, False, False, 0)
            row.add(b)
            self.nav.add(row)
            self.nav_rows[key] = row
        self.nav.connect("row-selected", lambda _lb, row: row and self.stack.set_visible_child_name(row.page))
        nav_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        nav_scroll.add(self.nav)                      # 17 pages don't fit a short window
        side.pack_start(nav_scroll, True, True, 0)

        foot = vbox(6, "side-foot")
        foot.pack_start(label(_("SYSTEM HEALTH"), ("kicker",)), False, False, 0)
        row = hbox(8)
        self.side_score = label("–", ("big",))
        row.pack_start(self.side_score, False, False, 0)
        self.side_pill = pill(_("scanning"), "dim")
        row.pack_end(self.side_pill, False, False, 0)
        foot.pack_start(row, False, False, 0)
        self.side_bar = Bar(EMERALD)
        foot.pack_start(self.side_bar, False, False, 0)
        live = hbox(6)
        live.pack_start(label("●", ("pill", "ok")), False, False, 0)
        live.pack_start(label(_("Live monitoring"), ("faint",)), False, False, 0)
        foot.pack_start(live, False, False, 4)
        self.level_pill = pill(_("intermediate mode"), "dim")
        self.level_pill.set_halign(Gtk.Align.START)
        css(self.level_pill, "level-pill")
        foot.pack_start(self.level_pill, False, False, 0)
        side.pack_end(foot, False, False, 0)
        self.side_foot = foot
        return side

    def _build_footer(self):
        f = hbox(10, "footer")
        f.pack_start(label(_('MintCare v{0}  •  {1} checks  •  {2} tweaks  •  {3} cleaners').format(__version__, len(checks.CHECKS), len(tweaks.TWEAKS), len(cleaner.ITEMS))), False, False, 0)
        self.spinner = Gtk.Spinner()
        self.status_lbl = label(_("Ready"), ("status",))
        f.pack_end(self.status_lbl, False, False, 0)
        f.pack_end(self.spinner, False, False, 0)
        return f

    def goto(self, page):
        row = self.nav_rows.get(page)
        if row:
            self.nav.select_row(row)

    # ------------------------------------------------------------ keyboard

    SHORTCUTS = [("<Primary>f", N_("Search")), ("<Primary>1", N_("Go to the 1st … 9th page in the sidebar")),
                 ("<Primary>Page_Down", N_("Next page")), ("<Primary>Page_Up", N_("Previous page")),
                 ("F5", N_("Refresh this page")), ("<Primary>e", N_("Export health report")),
                 ("<Primary>question", N_("Keyboard shortcuts"))]

    def _install_shortcuts(self):
        accel = Gtk.AccelGroup()

        def add(accelerator, fn):
            key, mods = Gtk.accelerator_parse(accelerator)
            accel.connect(key, mods, 0, lambda *_x: fn() or True)
        add("<Primary>f", self.search.grab_focus)
        for n in range(1, 10):
            add(f"<Primary>{n}", lambda n=n: self._goto_nth(n - 1))
        add("<Primary>Page_Down", lambda: self._goto_step(1))
        add("<Primary>Page_Up", lambda: self._goto_step(-1))
        add("F5", self.refresh_page)
        add("<Primary>r", self.refresh_page)
        add("<Primary>e", self.export_report)
        add("<Primary>question", self.show_shortcuts)
        add("<Primary><Shift>question", self.show_shortcuts)
        add("F1", self.show_shortcuts)
        self.add_accel_group(accel)

    def _visible_pages(self):
        return [key for key, _t, _i in NAV if self.nav_rows[key].get_visible()]

    def _goto_nth(self, n):
        pages = self._visible_pages()
        if n < len(pages):
            self.goto(pages[n])
            self.nav_rows[pages[n]].grab_focus()

    def _goto_step(self, d):
        pages = self._visible_pages()
        cur = self.stack.get_visible_child_name()
        i = pages.index(cur) if cur in pages else 0
        self._goto_nth((i + d) % len(pages))

    def refresh_page(self):
        if self.busy:
            return
        loaders = {
            "dashboard": self.run_diagnose, "diagnose": self.run_diagnose, "optimise": self._refresh_tweaks,
            "clean": self.run_clean_scan, "software": self.load_software, "packages": self.load_packages,
            "flatpak": lambda: self.load_flatpaks(check_updates=True), "processes": lambda: self._poll_procs(True),
            "hardware": self.load_drives, "security": self.load_security, "troubleshoot": self.load_troubleshoot,
            "startup": self.load_boot, "backup": self.load_backup, "maintenance": self.refresh_maintenance,
            "space": lambda: (self.scan_folders(getattr(self, "_space_root", space.HOME)), self.load_snapshots()),
        }
        fn = loaders.get(self.stack.get_visible_child_name())
        if fn:
            fn()

    def show_shortcuts(self):
        d = Gtk.Dialog(title=_("Keyboard shortcuts"), transient_for=self, modal=True)
        d.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        g = Gtk.Grid(column_spacing=24, row_spacing=10, margin=18)
        for i, (accelerator, text) in enumerate(self.SHORTCUTS):
            key, mods = Gtk.accelerator_parse(accelerator)
            keys = Gtk.accelerator_get_label(key, mods)
            if accelerator == "<Primary>1":
                keys += " … " + Gtk.accelerator_get_label(*Gtk.accelerator_parse("<Primary>9"))
            g.attach(label(keys, ("mono",)), 0, i, 1, 1)
            g.attach(label(_(text)), 1, i, 1, 1)
        extra = [_("Tab / Shift+Tab — move between controls; Space or Enter — press a button or switch"),
                 _("Disk space map — arrow keys move between folders, Enter opens one")]
        for j, text in enumerate(extra, len(self.SHORTCUTS)):
            g.attach(label(text, ("muted",), wrap=True), 0, j, 2, 1)
        d.get_content_area().add(g)
        d.show_all()
        d.run()
        d.destroy()

    # ------------------------------------------------------------ plumbing

    def action_button(self, text, cb, primary=False, icon_name=None):
        b = Gtk.Button()
        inner = hbox(6)
        if icon_name:
            inner.pack_start(icon(icon_name), False, False, 0)
        inner.pack_start(Gtk.Label(label=text), False, False, 0)
        b.add(inner)
        describe(b, text)
        css(b, "primary" if primary else "ghost")
        b.set_valign(Gtk.Align.CENTER)
        b.connect("clicked", lambda *_x: cb())
        self._action_buttons.append(b)
        return b

    def set_status(self, text, bad=False):
        self.status_lbl.set_text(text)
        ctx = self.status_lbl.get_style_context()
        (ctx.add_class if bad else ctx.remove_class)("bad")

    def set_busy(self, busy, text=""):
        self.busy = busy
        self.set_status(text or (_("Working…") if busy else _("Ready")))
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
                self.log(_('Error: {0}').format(e))
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
                self.log(_('Error: {0}').format(e))
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
        elif name == "software" and not self._sw_loaded:
            self._sw_loaded = True
            self.load_software()
        elif name == "security" and not self._sec_loaded:
            self._sec_loaded = True
            self.load_security()
        elif name == "packages" and getattr(self, "pkg_data", None) is None and not getattr(self, "_pkg_loading", False):
            self._pkg_loading = True
            self.load_packages()
        elif name == "backup":
            self.load_backup()
        elif name == "assistant":
            self.load_assistant()
        elif name == "processes":
            self._poll_procs(True)
        elif name == "troubleshoot":
            self.load_troubleshoot()
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
            self.log(_("Requesting administrator access…"))
            results.update(run_steps(root_steps, root=True, on_line=self.log))
        ok = sum(1 for rc in results.values() if rc == 0)
        self.log(_('Finished: {0}/{1} succeeded.').format(ok, len(results)))
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
            self.log(_("A Timeshift snapshot was taken in the last 30 minutes; not taking another."))
            return []
        if pref == "always":
            return [maintenance.snapshot_step(reason)]
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text=_("Take a Timeshift snapshot first?"))
        css(d, "mintcare")
        d.format_secondary_text(_("You're about to apply {0}. A snapshot lets you roll the whole system back if anything goes wrong. It usually takes a minute or two.").format(reason))
        remember = Gtk.CheckButton(label=_("Remember my choice"))
        remember.set_margin_start(12)
        d.get_message_area().pack_start(remember, False, False, 6)
        remember.show()
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Continue without"), Gtk.ResponseType.NO,
                      _("Snapshot first"), Gtk.ResponseType.YES)
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
            self.set_status(_('{0} step(s) failed — see Activity log').format(len(failed)), bad=True)
        else:
            self.set_status(_('{0} complete').format(_(what)))

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
        """Show/hide the XApp status icon (Cinnamon/MATE/Xfce panels)."""
        app = self.get_application()
        if on and getattr(self, "tray", None) is None:
            try:
                gi.require_version("XApp", "1.0")
                from gi.repository import XApp
            except (ValueError, ImportError):
                self.log(_("System tray icon needs the XApp library (gir1.2-xapp-1.0)."))
                self.set_status(_("Tray icon not available on this desktop"), bad=True)
                return
            self.tray = XApp.StatusIcon()
            self.tray.set_name("mintcare")
            theme_ = Gtk.IconTheme.get_default()
            self.tray.set_icon_name("mintcare" if theme_.has_icon("mintcare") else "applications-system")
            self.tray.connect("activate", lambda *_x: self.toggle_visible())
            menu = Gtk.Menu()
            for text, cb in ((_("Open MintCare"), self.show_from_tray), (_("Run check-up now"), self.run_checkup_now),
                             (_("Quick clean"), lambda: (self.show_from_tray(), self.quick_clean())), (None, None),
                             ("Quit MintCare", self.quit_app)):
                item = Gtk.SeparatorMenuItem() if text is None else Gtk.MenuItem(label=text)
                if cb:
                    item.connect("activate", lambda *_a, f=cb: f())
                menu.append(item)
            menu.show_all()
            self.tray.set_secondary_menu(menu)
            self._update_tray()
            app.hold()
        elif not on and getattr(self, "tray", None) is not None:
            self.tray.set_visible(False)
            self.tray = None
            app.release()
            self.show_from_tray()

    def _update_tray(self):
        if getattr(self, "tray", None) is None:
            return
        res = self.check_results
        if not res:
            self.tray.set_tooltip_text(_("MintCare — scanning…"))
            return
        issues = [r for r in res.values() if r.status in (checks.WARN, checks.FAIL)]
        sc = int(self.side_score.get_text() or 0) if self.side_score.get_text().isdigit() else 0
        self.tray.set_tooltip_text(_('MintCare — health {0}/100').format(sc) +
                                   (_(', {0} issue(s) to look at').format(len(issues)) if issues else _(", all good")))

    def toggle_visible(self):
        if self.get_visible() and self.is_active():
            self.hide()
        else:
            self.show_from_tray()

    def show_from_tray(self):
        self.show()
        self.present()

    def quit_app(self):
        if getattr(self, "tray", None) is not None:
            self.tray.set_visible(False)
            self.tray = None
            self.get_application().release()
        self.get_application().quit()

    def _on_delete(self, *_a):
        if getattr(self, "tray", None) is not None:
            self.hide()               # keep running in the tray
            return True
        return False

    TRAY_DESKTOP = Path(GLib.get_user_config_dir()) / "autostart" / "mintcare-tray.desktop"

    def set_tray_autostart(self, on):
        f = self.TRAY_DESKTOP
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            cmd = " ".join(maintenance.launcher() + ["--tray"])
            f.write_text("[Desktop Entry]\nType=Application\nName=MintCare (tray)\n"
                         "Comment=Keep an eye on system health from the panel\n"
                         f"Exec={cmd}\nIcon=mintcare\nX-GNOME-Autostart-enabled=true\n"
                         "X-GNOME-Autostart-Delay=20\n# Managed by MintCare\n")
            self.set_status(_("MintCare will start in the tray when you log in"))
        else:
            f.unlink(missing_ok=True)
            self.set_status(_("MintCare won't start at login"))
        maintenance.record("startup", (_('Start in tray at login: on') if on else _('Start in tray at login: off')))

    def export_report(self):
        d = Gtk.FileChooserDialog(title=_("Export health report"), transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Export"), Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        d.set_current_folder(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or space.HOME)
        stamp = time.strftime("%Y-%m-%d")
        d.set_current_name(f"MintCare-report-{stamp}.pdf")
        extra = vbox(6)
        fmt = hbox(12)
        fmt.pack_start(label(_("Format:"), ("muted",)), False, False, 0)
        pdf_rb = Gtk.RadioButton.new_with_label_from_widget(None, _("PDF"))
        html_rb = Gtk.RadioButton.new_with_label_from_widget(pdf_rb, _("Web page (HTML)"))

        def set_ext(_w):
            name = d.get_current_name() or f"MintCare-report-{stamp}"
            base = name.rsplit(".", 1)[0] if name.lower().endswith((".pdf", ".html")) else name
            d.set_current_name(base + (".pdf" if pdf_rb.get_active() else ".html"))
        pdf_rb.connect("toggled", set_ext)
        fmt.pack_start(pdf_rb, False, False, 0)
        fmt.pack_start(html_rb, False, False, 0)
        extra.pack_start(fmt, False, False, 0)
        priv = Gtk.CheckButton(label=_("Leave out personal details (computer name, user name, drive serial numbers)"),
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
                self.set_status(_("Could not build the report"), bad=True)
                return
            if as_pdf:
                self._print_pdf(page, path)
            else:
                Path(path).write_text(page)
                self._report_saved(path)
        self.in_thread(_("Building health report…"), work, done)

    def _print_pdf(self, page, path):
        try:
            gi.require_version("WebKit2", "4.1")
            from gi.repository import WebKit2
        except (ValueError, ImportError):
            alt = path[:-4] + ".html"
            Path(alt).write_text(page)
            self.log(_("WebKit is not available for PDF output; saved the report as HTML instead."))
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
            self.log(_('PDF export failed: {0}').format(err.message if hasattr(err, 'message') else err))
            self.set_status(_("PDF export failed"), bad=True)

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
        maintenance.record("other", _('Exported health report ({0})').format(os.path.basename(path)))
        self.log(_('Health report saved: {0}').format(path))
        self.set_status(_('Report saved: {0}').format(os.path.basename(path)))
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    def show_about(self):
        d = Gtk.AboutDialog(transient_for=self, modal=True, program_name="MintCare", version=__version__,
                            comments=_("Clean, optimise and repair Linux Mint."), logo_icon_name="mintcare",
                            copyright="© 2026 Matthew Allwood", license_type=Gtk.License.GPL_3_0)
        css(d, "mintcare")
        d.run()
        d.destroy()

    # ------------------------------------------------------------ dashboard

    def _build_dashboard(self):
        page = vbox(12, "page")

        # "new version" banner, revealed when GitHub has a newer release
        self.update_banner = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        ub = card("ecard")
        ur = hbox(12)
        ur.pack_start(icon("software-update-available-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"),
                      False, False, 0)
        ut = vbox(2)
        ut.set_valign(Gtk.Align.CENTER)
        self.update_title = label("", ("h2",))
        ut.pack_start(self.update_title, False, False, 0)
        self.update_sub = label("", ("muted",), ellipsize=True)
        ut.pack_start(self.update_sub, False, False, 0)
        ur.pack_start(ut, True, True, 0)
        ur.pack_end(self.action_button(_("Update now"), lambda: self.install_mintcare_update(), primary=True,
                                       icon_name="software-update-available-symbolic"), False, False, 0)
        ur.pack_end(self.action_button(_("What's new"), lambda: self.goto("software")), False, False, 0)
        ub.pack_start(ur, False, False, 0)
        self.update_banner.add(ub)
        page.pack_start(self.update_banner, False, False, 0)

        # upper bar
        top = card("ecard")
        row = hbox(12)
        row.pack_start(icon("computer-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        t.set_valign(Gtk.Align.CENTER)
        t.pack_start(label(socket.gethostname(), ("h1",)), False, False, 0)
        desk = os.environ.get("XDG_CURRENT_DESKTOP", "unknown").replace("X-", "")
        t.pack_start(label(_('● {0} ({1}) • {2} • Kernel {3}').format(platform.system(), platform.machine(), desk, platform.release()),
                           ("faint", "mono"), ellipsize=True), False, False, 0)
        row.pack_start(t, True, True, 0)
        row.pack_end(self.action_button(_("Refresh"), self.run_diagnose, icon_name="view-refresh-symbolic"),
                     False, False, 0)
        row.pack_end(self.action_button(_("Health scan"), self._health_scan, primary=True,
                                        icon_name="emblem-favorite-symbolic"), False, False, 0)
        up = vbox(1)
        up.set_valign(Gtk.Align.CENTER)
        up.pack_start(label(_("SYSTEM UPTIME"), ("kicker",), xalign=1.0), False, False, 0)
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
            _("Processor Activity"), "speedometer-symbolic", EMERALD, _("CPU load"), _('{0} threads').format(os.cpu_count()), "ok")
        cpu_foot.pack_start(label(cpu_model, ("faint", "mono"), xalign=0.5, ellipsize=True), False, False, 0)
        lr = hbox(0)
        lr.pack_start(label(_("Load average"), ("faint", "mono")), False, False, 0)
        self.load_lbl = label("–", ("stat-val",), xalign=1.0)
        lr.pack_end(self.load_lbl, False, False, 0)
        cpu_foot.pack_start(lr, False, False, 0)

        self.g_mem, mem_card, mem_foot = self._gauge_card(
            _("System Memory"), "media-flash-symbolic", BLUE, _("RAM used"), human(self.sampler.sample()["mem_total"]),
            "blue")
        self.mem_used, self.mem_swap = self._tiles(mem_foot, _("USED"), _("SWAP USED"))

        self.g_disk, disk_card, disk_foot = self._gauge_card(
            _("Root Disk"), "drive-harddisk-symbolic", ORANGE, _("Storage full"), human(shutil.disk_usage("/").total),
            "orange")
        self.disk_used, self.disk_free = self._tiles(disk_foot, _("OCCUPIED"), _("AVAILABLE"))

        # performance chart (2 columns)
        perf = card()
        ph = hbox(8)
        ph.pack_start(icon("org.gnome.SystemMonitor-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        ph.pack_start(label(_("Live Performance"), ("h2",)), False, False, 0)
        for name, col in (("RAM", BLUE), ("CPU", EMERALD)):
            ph.pack_end(self._legend(name, col), False, False, 4)
        perf.pack_start(ph, False, False, 0)
        self.perf_chart = LineChart([("CPU", EMERALD), ("RAM", BLUE)], points=90, height=150, fixed_max=100)
        perf.pack_start(self.perf_chart, True, True, 10)

        # health card
        hc = card("ecard")
        hh = hbox(8)
        hh.pack_start(icon("emblem-favorite-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        hh.pack_start(label(_("Health Score"), ("h2",)), False, False, 0)
        self.health_pill = pill(_("scanning"), "dim")
        hh.pack_end(self.health_pill, False, False, 0)
        hc.pack_start(hh, False, False, 0)
        self.g_health = RingGauge(EMERALD, _("of 100"), size=118)
        hc.pack_start(self.g_health, False, False, 6)
        counts = hbox(6)
        counts.set_halign(Gtk.Align.CENTER)
        self.cnt_fail, self.cnt_warn, self.cnt_info = pill(_("0 problems"), "fail"), pill(_("0 warnings"), "warn"), \
            pill(_("0 tips"), "info")
        for p in (self.cnt_fail, self.cnt_warn, self.cnt_info):
            counts.pack_start(p, False, False, 0)
        hc.pack_start(counts, False, False, 4)
        self.trend_chart = LineChart([(_("Score"), EMERALD)], points=30, height=48, fixed_max=100, axis=False)
        hc.pack_start(self.trend_chart, False, False, 2)
        self.trend_lbl = label("", ("faint",), xalign=0.5, wrap=True)
        self.trend_lbl.set_justify(Gtk.Justification.CENTER)
        hc.pack_start(self.trend_lbl, False, False, 0)
        self.issue_summary = label(_("Scanning…"), ("faint",), xalign=0.5, wrap=True)
        self.issue_summary.set_justify(Gtk.Justification.CENTER)
        hc.pack_start(self.issue_summary, False, False, 6)
        acts = vbox(6)
        acts.pack_start(self.action_button(_("Fix all issues"), self.fix_all, primary=True,
                                           icon_name="emblem-ok-symbolic"), False, False, 0)
        # stacked, not side by side: in an equal-width grid this card sets every column's minimum
        acts.pack_start(self.action_button(_("Recommended tweaks"), self.apply_recommended), False, False, 0)
        acts.pack_start(self.action_button(_("Quick clean"), self.quick_clean), False, False, 0)
        hc.pack_end(acts, False, False, 0)

        # network chart
        net = card()
        nh = hbox(8)
        nh.pack_start(icon("network-wired-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        nh.pack_start(label(_("Network Throughput"), ("h2",)), False, False, 0)
        self.net_lbl = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        nh.pack_end(self.net_lbl, True, True, 0)
        net.pack_start(nh, False, False, 0)
        self.net_chart = LineChart([(_("Down"), VIOLET), (_("Up"), ORANGE)], points=90, height=100, fmt=rate, log=True)
        net.pack_start(self.net_chart, True, True, 10)

        # system details
        det = card()
        dh = hbox(8)
        dh.pack_start(icon("dialog-information-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        dh.pack_start(label(_("System Details"), ("h2",)), False, False, 0)
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

        note = label(_("MintCare only writes its own drop-in files (named <tt>*mintcare*</tt>) and records what "
                     "it changed in <tt>/var/lib/mintcare</tt>, so every optimisation can be switched off again "
                     "from the Optimise page."), ("faint",), wrap=True, markup=True)
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
            (_("Desktop"), os.environ.get("XDG_CURRENT_DESKTOP", _("unknown")).replace("X-", "")),
            (_("TCP congestion"), f"{sysctl('net.ipv4.tcp_congestion_control')} / {sysctl('net.core.default_qdisc')}"),
            (_("Swap"), f"{swap_kind} (swappiness {sysctl('vm.swappiness')})"),
            (_("Firewall"), _("enabled") if tweaks.BY_ID["ufw"].probe() else _("disabled")),
            (_("Tweaks active"), str(sum(1 for v in self._tweak_initial.values() if v)) if self._tweak_initial
             else "…"),
        ]
        for i, (k, v) in enumerate(rows):
            self.kv_grid.attach(label(k, ("kv-key",)), 0, i, 1, 1)
            self.kv_grid.attach(label(v, ("kv-val",), ellipsize=True), 1, i, 1, 1)
        self.kv_grid.show_all()
        self.chip_vals["TCP"].set_text(sysctl("net.ipv4.tcp_congestion_control").upper())

    def _tick(self):
        if not self.get_visible():
            return True                       # hidden in the tray: no need to redraw charts
        s = self.sampler.sample()
        self.g_cpu.set_value(s["cpu"])
        self.g_mem.set_value(s["mem_pct"])
        self.g_disk.set_value(s["disk_pct"])
        self.load_lbl.set_text(" • ".join(f"{v:.2f}" for v in s["load"]))
        self.mem_used.set_text(human(s["mem_used"]))
        self.mem_swap.set_text(human(s["swap_used"]) if s["swap_total"] else _("no swap"))
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
        color, kind, word = ((EMERALD, "ok", _("healthy")) if score >= 85 else
                             ("#f59e0b", "warn", _("needs care")) if score >= 60 else ("#ef4444", "fail", _("at risk")))
        self.g_health.color = color
        self.g_health.set_value(score, str(score))
        describe(self.g_health, _('Health score: {0} of 100, {1}').format(score, word))
        set_pill(self.health_pill, word, kind)
        set_pill(self.side_pill, word, kind)
        self.side_score.set_text(str(score))
        self.side_bar.color = color
        self.side_bar.set_value(score)
        self.cnt_fail.set_text(ngettext('{0} problem', '{0} problems', counts[checks.FAIL]).format(counts[checks.FAIL]))
        self.cnt_warn.set_text(ngettext('{0} warning', '{0} warnings', counts[checks.WARN]).format(counts[checks.WARN]))
        self.cnt_info.set_text(ngettext('{0} tip', '{0} tips', counts[checks.INFO]).format(counts[checks.INFO]))
        fixable = sum(1 for r in res.values() if r.fix and r.status in (checks.WARN, checks.FAIL)
                      and r.fix.kind in ("root", "user", "tweak"))
        self.issue_summary.set_text(
            _('{0} checks run. ').format(len(res)) +
            (_('{0} issue(s) can be fixed automatically.').format(fixable) if fixable else
             _("Nothing needs fixing — tips have their own buttons on Diagnose & Fix.")))
        issues = counts[checks.FAIL] + counts[checks.WARN]
        maintenance.record_score(score, {"fail": counts[checks.FAIL], "warn": counts[checks.WARN],
                                         "info": counts[checks.INFO]})
        self._render_trend()
        GLib.idle_add(self._update_tray)
        badge = self.nav_badges["diagnose"]
        badge.set_text(str(issues))
        badge.set_visible(issues > 0)
        self._fill_details()

    def _render_trend(self):
        pts = maintenance.scores(30)
        if len(pts) < 2:
            self.trend_chart.set_values([p["score"] for p in pts] or [0])
            self.trend_lbl.set_text(_("The health trend appears after a few scans."))
            return
        self.trend_chart.set_values([p["score"] for p in pts])
        first, last = pts[0], pts[-1]
        delta = last["score"] - first["score"]
        since = time.strftime("%d %b", time.localtime(first["ts"]))
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        self.trend_lbl.set_text(_('Health over time: {0} {1} since {2} ({3} scans)').format(arrow, abs(delta), since, len(pts)))

    # ------------------------------------------------------------ MintCare updates

    def check_mintcare_update(self, force=False):
        if not force and not self.prefs.get("check_updates", True):
            return False

        def work():
            if force:
                try:
                    return selfupdate.check(timeout=10)
                except (OSError, ValueError):
                    return None
            return selfupdate.latest(timeout=8)

        def done(info):
            self.update_info = info
            newer = bool(info) and selfupdate.is_newer(info["version"])
            if newer:
                self.update_title.set_text(_('MintCare {0} is available').format(info['version']))
                self.update_sub.set_text(_('You have {0}. ').format(__version__) + (
                    _("Update installs the verified package from GitHub.") if selfupdate.installed_from_package()
                    else _("You're running from source — update with 'git pull'.")))
            self.update_banner.set_reveal_child(newer)
            if force:
                self.set_status(_('MintCare {0} is available').format(info['version']) if newer else
                                (_('MintCare {0} is up to date').format(__version__) if info else
                                 _("Couldn't reach GitHub to check for updates")), bad=info is None)
            if hasattr(self, "sw_update_lbl"):
                self._render_mintcare_card()
        self.bg(work, done)
        return False

    def install_mintcare_update(self):
        info = self.update_info
        if not info or not selfupdate.is_newer(info["version"]):
            self.set_status(_("No newer MintCare version found"))
            return
        if not selfupdate.installed_from_package():
            self.set_status(_("Running from source: update with 'git pull' in the project folder"))
            return
        if not self.confirm(_('Update MintCare to {0}?').format(info['version']),
                            _('Downloads <tt>{0}</tt> ({1}) from GitHub, checks its SHA-256 checksum, then installs it. MintCare restarts afterwards.').format(GLib.markup_escape_text(info.get('asset_name') or ''), human(info.get('size', 0)))):
            return

        def work():
            self.log(_('\n━━━ Updating MintCare to {0} ━━━').format(info['version']))
            path = selfupdate.download(info, lambda d, t: GLib.idle_add(
                self.set_status, f"Downloading… {human(d)} of {human(t)}") if t else None)
            self.log(_('Downloaded and verified {0} (sha256 {1}…)').format(path, info['sha256'][:16]))
            return self.execute([selfupdate.install_step(path, info["version"])], [], "Update")

        def done(results):
            if not results or results.get("selfupdate") != 0:
                self.set_status(_("The update didn't install — see Activity log"), bad=True)
                return
            maintenance.record("update", _('Updated MintCare to {0}').format(info['version']))
            if self.confirm(_('MintCare {0} is installed').format(info['version']), _("Restart MintCare now to use it?")):
                cmd = " ".join(maintenance.launcher())
                subprocess.Popen(["bash", "-c", f"sleep 1; exec {cmd}"], start_new_session=True)
                self.quit_app()
        self.in_thread(_("Updating MintCare…"), work, done)

    # ------------------------------------------------------------ software & sources

    def _build_software(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "software-update-available-symbolic", _("Software & updates"),
            _("MintCare's own updates and the health of the software sources apt installs from."),
            [self.action_button(_("Open Software Sources"), lambda: self._launch("mintsources"),
                                icon_name="preferences-system-symbolic"),
             self.action_button(_("Test sources"), self.test_sources, primary=True,
                                icon_name="emblem-synchronizing-symbolic")]), False, False, 0)
        mc = card("ecard")
        h = hbox(10)
        h.pack_start(icon("software-update-available-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        h.pack_start(label(_("MintCare updates"), ("h2",)), True, True, 0)
        self.sw_update_btn = self.action_button(_("Update now"), self.install_mintcare_update, primary=True)
        self.sw_update_btn.set_no_show_all(True)
        h.pack_end(self.sw_update_btn, False, False, 0)
        h.pack_end(self.action_button(_("Release notes"), lambda: self._open_uri(
            (self.update_info or {}).get("url") or selfupdate.RELEASES_PAGE)), False, False, 0)
        h.pack_end(self.action_button(_("Check now"), lambda: self.check_mintcare_update(force=True),
                                      icon_name="view-refresh-symbolic"), False, False, 0)
        mc.pack_start(h, False, False, 0)
        self.sw_update_lbl = label("", ("muted",), wrap=True)
        mc.pack_start(self.sw_update_lbl, False, False, 8)
        self.sw_notes = label("", ("faint",), wrap=True)
        mc.pack_start(self.sw_notes, False, False, 0)
        page.pack_start(mc, False, False, 0)

        self.src_head = label(_("SOFTWARE SOURCES"), ("kicker",))
        page.pack_start(self.src_head, False, False, 0)
        self.src_note = label(_("Official Mint and Ubuntu sources can't be switched off here — use Software "
                                "Sources for mirrors. Test sources runs apt-get update and records any errors."),
                              ("faint",), wrap=True)
        page.pack_start(self.src_note, False, False, 0)
        self.src_card, self.src_list = list_card()
        page.pack_start(self.src_card, False, False, 0)
        return scrolled(page)

    def _render_mintcare_card(self):
        info = self.update_info
        if not info:
            self.sw_update_lbl.set_text(_('You have MintCare {0}. ').format(__version__) +
                                        _("Press Check now to look for a newer version on GitHub."))
            self.sw_notes.set_text("")
            self.sw_update_btn.hide()
            return
        newer = selfupdate.is_newer(info["version"])
        when = (info.get("published") or "")[:10]
        checked = time.strftime("%H:%M", time.localtime(info.get("checked", time.time())))
        self.sw_update_lbl.set_markup(
            (_('<b>MintCare {0} is available</b> (released {1}). You have {2}.').format(GLib.markup_escape_text(info['version']), when, __version__) if newer else
             _('You have the latest version, <b>{0}</b>.').format(__version__)) + _('  <small>Checked at {0}.</small>').format(checked))
        notes = info.get("notes", "").strip()
        self.sw_notes.set_text(notes[:600] if newer else "")
        self.sw_update_btn.set_visible(newer and selfupdate.installed_from_package())

    def load_software(self):
        self._render_mintcare_card()
        if self.update_info is None:
            self.check_mintcare_update()
        self.bg(sources.analyse, self._render_sources)

    def _render_sources(self, srcs):
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "src_row", False)]
        for ch in self.src_list.get_children():
            ch.destroy()
        srcs = srcs or []
        test = sources.last_test()
        bad = sum(1 for x in srcs if x.enabled and x.health in (sources.WARN, sources.FAIL))
        tested = (_(" · last tested {0}").format(time.strftime("%d %b %H:%M", time.localtime(test["ts"])))) if test else ""
        self.src_head.set_text(_('SOFTWARE SOURCES ({0}, {1} with problems){2}').format(len(srcs), bad, tested))
        icons = {"Linux Mint": "computer-symbolic", "Ubuntu": "system-software-install-symbolic",
                 "PPA": "package-x-generic-symbolic", "Third-party": "network-server-symbolic"}
        order = {"Linux Mint": 0, "Ubuntu": 1, "PPA": 2, "Third-party": 3}
        for x in sorted(srcs, key=lambda x: (not x.enabled, order[x.kind], x.label)):
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            box.pack_start(icon(icons[x.kind], Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
            t = vbox(2)
            th = hbox(8)
            th.pack_start(label(x.label, ("h3",), ellipsize=True), False, False, 0)
            th.pack_start(pill(x.kind, "blue" if x.kind in ("Linux Mint", "Ubuntu") else "dim"), False, False, 0)
            th.pack_start(pill(x.suite, "dim"), False, False, 0)
            hl = x.health
            word = {"ok": "working", "info": "note", "warn": "check", "fail": "broken", "disabled": "disabled"}[hl]
            th.pack_start(pill(word, {"disabled": "dim"}.get(hl, hl)), False, False, 0)
            t.pack_start(th, False, False, 0)
            sub = f"{x.type} {x.uri} {x.suite} {' '.join(x.comps)}  ·  {os.path.basename(x.file)}"
            sl = label(sub, ("faint", "mono"), ellipsize=True)
            sl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            t.pack_start(sl, False, False, 0)
            for lvl, text in x.problems:
                r = hbox(6)
                r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
                r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
                t.pack_start(r, False, False, 0)
            box.pack_start(t, True, True, 0)
            if x.kind in ("PPA", "Third-party") and (x.enabled or x.mintcare_disabled):
                b = self.action_button(_("Disable") if x.enabled else _("Enable"),
                                       lambda src=x: self.toggle_source(src))
                b.src_row = True
                box.pack_end(b, False, False, 0)
            row.add(box)
            self.src_list.add(row)
        self.src_list.show_all()

    def toggle_source(self, src):
        enable = not src.enabled
        if not self.confirm((_('Enable {0}?') if enable else _('Disable {0}?')).format(src.label),
                            (_("Packages from this source will get updates again.") if enable else
                             _("apt will stop installing and updating packages from this source. Installed "
                             "packages stay installed. You can enable it again here.")) +
                            _('\n\n<tt>{0}</tt> is backed up to /var/lib/mintcare first.').format(GLib.markup_escape_text(os.path.basename(src.file)))):
            return
        steps = [("source", f"{'Enable' if enable else 'Disable'} source {src.label}",
                  sources.toggle_script(src, enable))]

        def done(results):
            self.notify_done(results or {}, _("Software source"))
            self.load_software()
            self.run_diagnose()
        self.in_thread(_("Updating software sources…"), lambda: self.execute(steps, [], "Sources"), done)

    def test_sources(self):
        def work():
            self.log(_("\n━━━ Testing software sources (apt-get update) ━━━"))
            lines = []

            def collect(line):
                lines.append(line)
                self.log(line)
            res = run_steps([sources.test_step()], root=True, on_line=collect)
            data = sources.save_test("\n".join(lines)) if res.get("sources-test") == 0 else None
            return res, data

        def done(out):
            res, data = out or ({}, None)
            if data is None:
                self.set_status(_("Source test was cancelled or failed"), bad=True)
                return
            n = len(data["errors"])
            self.set_status(_('Tested sources: {0} error(s)').format(n) if n else _("All software sources downloaded fine"),
                            bad=bool(n))
            maintenance.record("other", _('Tested software sources: {0} error(s)').format(n), n == 0)
            self.load_software()
            self.run_diagnose()
        self.in_thread(_("Testing software sources…"), work, done)

    def _launch(self, cmd):
        try:
            subprocess.Popen([cmd], start_new_session=True)
        except OSError as e:
            self.set_status(_('Could not start {0}: {1}').format(cmd, e), bad=True)

    def _open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as e:
            self.log(_('Could not open {0}: {1}').format(uri, e.message))

    # ------------------------------------------------------------ security

    def _build_security(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "security-high-symbolic", _("Security"),
            _("What other computers can reach on this PC, and whether the firewall is in front of it."),
            [self.action_button(_("Identify programs"), self.identify_ports,
                                icon_name="system-search-symbolic")]), False, False, 0)
        fc = card("ecard")
        h = hbox(10)
        self.fw_icon = icon("security-high-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico")
        h.pack_start(self.fw_icon, False, False, 0)
        t = vbox(2)
        t.set_valign(Gtk.Align.CENTER)
        th = hbox(8)
        th.pack_start(label(_("Firewall (UFW)"), ("h2",)), False, False, 0)
        self.fw_pill = pill("…", "dim")
        th.pack_start(self.fw_pill, False, False, 0)
        t.pack_start(th, False, False, 0)
        self.fw_text = label("", ("muted",), wrap=True)
        t.pack_start(self.fw_text, False, False, 0)
        h.pack_start(t, True, True, 0)
        self.fw_btn = self.action_button(_("Enable firewall"), self._enable_firewall, primary=True)
        self.fw_btn.set_no_show_all(True)
        h.pack_end(self.fw_btn, False, False, 0)
        fc.pack_start(h, False, False, 0)
        self.fw_rules = label("", ("faint", "mono"), wrap=True, selectable=True)
        self.fw_rules.set_no_show_all(True)
        fc.pack_start(self.fw_rules, False, False, 8)
        page.pack_start(fc, False, False, 0)

        hh = hbox(8)
        self.port_head = label(_("LISTENING SERVICES"), ("kicker",))
        hh.pack_start(self.port_head, True, True, 0)
        self.show_local = Gtk.CheckButton(label=_("Show local-only services"))
        self.show_local.connect("toggled", lambda *_x: self.load_security())
        hh.pack_end(self.show_local, False, False, 0)
        page.pack_start(hh, False, False, 0)
        self.port_card, self.port_list = list_card()
        page.pack_start(self.port_card, False, False, 0)

        page.pack_start(label(_("LOGIN SECURITY"), ("kicker",)), False, False, 0)
        self.login_card, self.login_list = list_card()
        page.pack_start(self.login_card, False, False, 0)

        page.pack_start(label(_("MALWARE SCAN"), ("kicker",)), False, False, 0)
        mc = card()
        mh = hbox(8)
        self.mal_status = label("", ("muted",), wrap=True)
        mh.pack_start(self.mal_status, True, True, 0)
        self.mal_btns = hbox(8)
        mh.pack_end(self.mal_btns, False, False, 0)
        mc.pack_start(mh, False, False, 0)
        self.mal_progress = label("", ("faint", "mono"), ellipsize=True)
        self.mal_progress.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        mc.pack_start(self.mal_progress, False, False, 4)
        self.mal_results = vbox(4)
        mc.pack_start(self.mal_results, False, False, 0)
        page.pack_start(mc, False, False, 0)
        self._mal_cancel = threading.Event()
        return scrolled(page)

    def load_security(self):
        self.bg(lambda: (ports.firewall_enabled(), ports.services(root_names=self.root_port_names)),
                self._render_security)
        self.bg(secaudit.audit, self._render_login)
        self._render_malware()

    def _render_login(self, items):
        self._clear(self.login_list, "login_row")
        for lvl, title, text, action in items or []:
            btns = []
            if action == "ssh-harden":
                btns.append(self._btn(_("Harden SSH"), lambda: self.confirm(
                    _("Harden the SSH server?"), _("Turns off root login, limits password attempts to 3 and shortens "
                                                   "the login window. Your own logins keep working.")) and
                    self._root_job(_("Hardening SSH…"), [("ssh", "Harden SSH", secaudit.SSH_HARDEN)], "Security",
                                   lambda r: self.load_security()), primary=True, tag="login_row"))
            elif action == "lock-on":
                btns.append(self._btn(_("Turn on screen lock"), lambda: (secaudit.lock_screen_on(),
                                      maintenance.record("other", _("Screen lock turned on")), self.load_security()),
                                      primary=True, tag="login_row"))
            self.login_list.add(self._row(title, text, "", STATUS_ICON[lvl][0], [], btns))
        self.login_list.show_all()

    def _render_malware(self):
        sc = secaudit.scanners()
        self._clear(self.mal_btns, "mal_row")
        if sc["clamav"]:
            self.mal_status.set_text(_("ClamAV is installed. Scanning your home folder takes a while (minutes to "
                                       "hours, depending on how many files you have)."))
            self.mal_btns.pack_start(self._btn(_("Scan home folder"), self.scan_malware, primary=True,
                                               tag="mal_row"), False, False, 0)
        else:
            self.mal_status.set_text(_("Linux malware is rare, but a scan can catch Windows malware in downloads "
                                       "you might pass on. ClamAV isn't installed."))
            self.mal_btns.pack_start(self._btn(_("Install ClamAV"), lambda: self._root_job(
                _("Installing ClamAV…"), [secaudit.install_step("clamav")], "Security", lambda r: self._render_malware()),
                tag="mal_row"), False, False, 0)
        if sc["rkhunter"]:
            self.mal_btns.pack_start(self._btn(_("Rootkit check"), lambda: self._root_job(
                _("Checking for rootkits…"), [secaudit.rkhunter_step()], "Security",
                lambda r: (self.set_status(_("Rootkit check done — warnings (if any) are in the Activity log")),
                           self.goto("log"))), tag="mal_row"), False, False, 0)
        else:
            self.mal_btns.pack_start(self._btn(_("Install rkhunter"), lambda: self._root_job(
                _("Installing rkhunter…"), [secaudit.install_step("rkhunter")], "Security",
                lambda r: self._render_malware()), tag="mal_row"), False, False, 0)
        self.mal_btns.show_all()

    def scan_malware(self):
        if getattr(self, "_mal_running", False):
            self._mal_cancel.set()
            return
        self._mal_running = True
        self._mal_cancel.clear()
        self._clear(self.mal_results, "mal_res")
        counter = {"n": 0}

        def line(text):
            counter["n"] += 1
            if counter["n"] % 25 == 0 or "FOUND" in text:
                GLib.idle_add(self.mal_progress.set_text, text[-160:])

        def done(res):
            self._mal_running = False
            res = res or {"infected": [], "rc": 2}
            found = res["infected"]
            self.mal_progress.set_text(_("Cancelled.") if res.get("cancelled") else
                                       (_('Scan finished: {0} infected file(s).').format(len(found)) if res["rc"] in (0, 1)
                                        else _("The scanner reported an error — see the Activity log.")))
            for path, virus in found:
                self.mal_results.pack_start(self._row(os.path.basename(path), virus, path, "dialog-error-symbolic"),
                                            False, False, 0)
            if found:
                self.mal_results.pack_start(self._btn(_("Quarantine these files"), lambda: self._root_job(
                    _("Quarantining…"), [secaudit.quarantine_step([p for p, _v in found])], "Security"),
                    primary=True, tag="mal_res"), False, False, 0)
            maintenance.record("other", _('Malware scan: {0} infected file(s)').format(len(found)), not found)
            self.mal_results.show_all()
        self.mal_progress.set_text(_("Scanning… press the button again to cancel"))
        self.bg(lambda: secaudit.clam_scan([str(Path.home())], line, self._mal_cancel.is_set), done)

    def _render_security(self, res):
        if not res:
            return
        fw, svcs = res
        set_pill(self.fw_pill, _("on") if fw else _("off"), "ok" if fw else "fail")
        self.fw_text.set_text(_("Incoming connections are blocked unless you allowed them. Outgoing traffic is "
                                "unaffected.") if fw else
                              _("Anything listening on the network can be reached by other computers."))
        self.fw_btn.set_visible(not fw)
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "port_row", False)]
        for ch in self.port_list.get_children():
            ch.destroy()
        net = [x for x in svcs if x["scope"] != "local"]
        shown = svcs if self.show_local.get_active() else net
        self.port_head.set_text(_('LISTENING SERVICES ({0} open to the network, {1} local only)').format(len(net), len(svcs) - len(net)))
        if not shown:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("Nothing is listening for connections from other computers."), ("muted",)))
            self.port_list.add(r)
        order = {"warn": 0, "fail": 0, "info": 1, "ok": 2}
        scope_word = {"network": (_("network"), "warn"), "link": (_("local network"), "dim"), "local": (_("this PC only"), "ok")}
        for x in sorted(shown, key=lambda x: (order[x["level"]], x["scope"] != "network", x["port"])):
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            box.pack_start(icon(STATUS_ICON[x["level"]][0], Gtk.IconSize.LARGE_TOOLBAR, STATUS_ICON[x["level"]][1]),
                           False, False, 0)
            t = vbox(2)
            th = hbox(8)
            th.pack_start(label(x["name"], ("h3",), ellipsize=True), False, False, 0)
            th.pack_start(pill(f"{x['port']}/{x['proto']}", "dim"), False, False, 0)
            w, k = scope_word[x["scope"]]
            th.pack_start(pill(w, k if x["scope"] != "network" else ("ok" if fw else "warn")), False, False, 0)
            if x["scope"] == "network" and fw:
                th.pack_start(pill(_("firewalled"), "ok"), False, False, 0)
            t.pack_start(th, False, False, 0)
            who = x["process"] or (_("(owned by a system service — use Identify programs)") if not x["known"] else "")
            meta = "  ·  ".join(p for p in (who, ", ".join(x["addresses"])) if p)
            t.pack_start(label(meta, ("faint", "mono"), ellipsize=True), False, False, 0)
            t.pack_start(label(x["advice"], ("muted",), wrap=True), False, False, 0)
            box.pack_start(t, True, True, 0)
            if x["unit"] and boot.unit_enabled(x["unit"]):
                b = self.action_button(_("Turn off"), lambda u=x["unit"], n=x["name"]: self._turn_off_service(u, n))
                b.port_row = True
                box.pack_end(b, False, False, 0)
            row.add(box)
            self.port_list.add(row)
        self.port_list.show_all()

    def identify_ports(self):
        def work():
            lines = []
            res = run_steps([ports.root_scan_step()], root=True, on_line=lines.append)
            return res, lines

        def done(out):
            res, lines = out or ({}, [])
            if res.get("ports-root") != 0:
                self.set_status(_("Identify was cancelled or failed"), bad=True)
                return
            names, ufw = ports.parse_root_scan(lines)
            self.root_port_names = names
            self.fw_rules.set_text("\n".join(ufw) or _("No firewall rules reported."))
            self.fw_rules.show()
            self.set_status(_('Identified {0} listening program(s)').format(len(names)))
            self.load_security()
        self.in_thread(_("Reading services and firewall rules…"), work, done)

    def _enable_firewall(self):
        t = tweaks.BY_ID["ufw"]
        steps = [("on-ufw", "Apply: " + t.title, t.apply_script())]

        def done(results):
            self.notify_done(results or {}, _("Firewall"))
            self._refresh_tweaks()
            self.load_security()
            self.run_diagnose()
        self.in_thread(_("Enabling the firewall…"), lambda: self.execute(steps, [], "Optimise"), done)

    def _turn_off_service(self, unit, name):
        if not self.confirm(_('Turn off {0}?').format(name),
                            _('Stops <tt>{0}</tt> now and at boot. Undo it from Maintenance › Undo, or with Enable on the Boot &amp; Startup page.').format(GLib.markup_escape_text(unit))):
            return
        steps = [("unit", f"Disable {unit}", boot.disable_script(unit))]

        def done(results):
            self.notify_done(results or {}, _('Turn off {0}').format(name))
            self.load_security()
            self.run_diagnose()
        self.in_thread(_('Turning off {0}…').format(name), lambda: self.execute(steps, [], "Boot services"), done)

    # ------------------------------------------------------------ diagnose

    def _build_diagnose(self):
        page = vbox(14, "page")
        self.diag_header = self.page_header(
            "system-search-symbolic", _("Diagnose & Fix"),
            _("Checks networking (incl. TCP BBR), packages, disks, memory, drivers, security and services."),
            [self.action_button(_("Rescan"), self.run_diagnose, icon_name="view-refresh-symbolic"),
             self.action_button(_("Fix all"), self.fix_all, primary=True, icon_name="emblem-ok-symbolic")])
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
            self.set_status(_('Scan complete: {0} issue(s)').format(n) if n else _("Scan complete: all good"))
        self.in_thread(_("Scanning system…"), work, done)
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
            head.pack_start(label(_('{0}/{1} passing').format(ok, len(items)), ("faint", "mono")), False, False, 0)
            self.diag_box.pack_start(head, False, False, 2)
            c, lb = list_card()
            lb.connect("row-activated", lambda _lb, row: getattr(row, "toggle_details", lambda: None)())
            for chk in items:
                lb.add(self._check_row(chk, self.check_results[chk.id]))
            self.diag_box.pack_start(c, False, False, 0)
        self.diag_box.show_all()
        for cid in list(getattr(self, "_open_details", set())):
            row = self.check_rows.get(cid)
            if row is not None and hasattr(row, "toggle_details"):
                row.toggle_details(force=True)

    def _check_row(self, check, res):
        has_details = check.id in checks.DETAIL_COMMANDS
        row = Gtk.ListBoxRow(activatable=has_details)
        self.check_rows = getattr(self, "check_rows", {})
        self.check_rows[check.id] = row
        outer = vbox(0)
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
        outer.pack_start(box, False, False, 0)
        if has_details:
            chev = icon("pan-end-symbolic", Gtk.IconSize.MENU, "faint")
            box.pack_end(chev, False, False, 0)
            row.set_tooltip_text(_("Click to see what MintCare checked, and the command behind it"))
            # A plain show/hide holder: a Gtk.Revealer mis-sizes when the output arrives after it opens
            holder = vbox(0)
            holder.set_no_show_all(True)
            outer.pack_start(holder, False, False, 0)
            state = {"panel": None}

            def toggle(force=False):
                self._open_details = getattr(self, "_open_details", set())
                show = force or not holder.get_visible()
                if show and state["panel"] is None:
                    state["panel"] = self._detail_panel(check)
                    holder.pack_start(state["panel"], False, False, 0)
                    state["panel"].show_all()
                elif show and not force:
                    state["panel"].refresh()
                holder.set_visible(show)
                chev.set_from_icon_name("pan-down-symbolic" if show else "pan-end-symbolic", Gtk.IconSize.MENU)
                (self._open_details.add if show else self._open_details.discard)(check.id)
            row.toggle_details = toggle
        row.add(outer)
        return row

    def _detail_panel(self, check):
        """Expandable panel: the command behind a check, its live output, and copy / terminal / refresh."""
        panel = vbox(6, "detail-panel")
        top = hbox(6)
        cmd_lbl = label("$ " + checks.DETAIL_COMMANDS[check.id], ("faint", "mono"), ellipsize=True, selectable=True)
        cmd_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        top.pack_start(cmd_lbl, True, True, 0)
        spin = Gtk.Spinner()
        top.pack_start(spin, False, False, 0)
        out = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False,
                           left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        css(out, "log")
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(out)

        def fit(text):
            # size to the output (wrapped lines estimated from width), scrolling beyond ~20 lines
            width_chars = max(40, (panel.get_allocated_width() or 900) // 8)
            rows = sum(1 + len(line) // width_chars for line in text.split("\n"))
            sw.set_min_content_height(min(rows, 20) * 17 + 14)

        def refresh():
            spin.start()
            out.get_buffer().set_text(_("Running…"))
            fit(_("Running…"))

            def done(res):
                spin.stop()
                text = (res or ("", _("(could not run the command)"), []))[1]
                for ch in acts.get_children():
                    ch.destroy()
                for lbl, kind, payload in (res[2] if res and len(res) > 2 else []):
                    b = Gtk.Button(label=lbl)
                    css(b, "primary" if kind in ("check", "tweak") else "ghost")
                    b.connect("clicked", lambda *_x, k=kind, p=payload: self.run_action(k, p))
                    acts.pack_start(b, False, False, 0)
                acts.show_all()
                out.get_buffer().set_text(text)
                fit(text)
            self.bg(lambda: checks.details(check.id), done)

        def small(text, cb, icon_name):
            b = Gtk.Button()
            inner = hbox(4)
            inner.pack_start(icon(icon_name), False, False, 0)
            inner.pack_start(Gtk.Label(label=text), False, False, 0)
            b.add(inner)
            describe(b, text)
            css(b, "ghost")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda *_x: cb())
            return b
        cmd = checks.DETAIL_COMMANDS[check.id]
        top.pack_end(small(_("Refresh"), refresh, "view-refresh-symbolic"), False, False, 0)
        top.pack_end(small(_("Open in terminal"), lambda: self.run_in_terminal(cmd), "utilities-terminal-symbolic"),
                     False, False, 0)
        top.pack_end(small(_("Copy"), lambda: self._copy(cmd), "edit-copy-symbolic"), False, False, 0)
        top.pack_end(small(_("Ask the assistant"), lambda: self.ask_assistant(
            _("MintCare's check “{0}” says: {1}. What does this mean on my PC, and what should I do?").format(check.title, self.check_results[check.id].summary if check.id in self.check_results else '')), "user-available-symbolic"), False, False, 0)
        panel.pack_start(top, False, False, 0)
        panel.pack_start(sw, False, False, 0)
        acts = hbox(8)
        panel.pack_start(acts, False, False, 2)
        panel.refresh = refresh
        refresh()
        return panel

    def _copy(self, text):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.set_status(_("Command copied to the clipboard"))

    def run_in_terminal(self, cmd):
        script = f"{cmd}; echo; read -rp 'Press Enter to close… '"
        for argv in (["gnome-terminal", "--", "bash", "-c", script],
                     ["x-terminal-emulator", "-e", "bash", "-c", script]):
            if shutil.which(argv[0]):
                try:
                    subprocess.Popen(argv, start_new_session=True)
                    return
                except OSError:
                    continue
        self.set_status(_("No terminal app found"), bad=True)

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
                self.log(_('Opening {0} for: {1}').format(r.fix.payload, c.title))
                try:
                    subprocess.Popen([r.fix.payload], start_new_session=True)
                except OSError as e:
                    self.log(_('Could not start {0}: {1}').format(r.fix.payload, e))
            elif r.fix.kind == "page" and len(items) == 1:
                self.goto(r.fix.payload)
        if not direct:
            return
        root, user = self._fix_steps(direct)
        snap = self.ask_snapshot(_("system repairs")) if root else []
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, _("Repair"))
            self._refresh_tweaks()
            self.run_diagnose()
        self.in_thread(_("Fixing…"), lambda: self.execute(root, user, "Repair", snap), done)

    def fix_all(self):
        items = [(c, self.check_results[c.id]) for c in checks.CHECKS
                 if c.id in self.check_results
                 and self.check_results[c.id].status in (checks.WARN, checks.FAIL)
                 and self.check_results[c.id].fix
                 and self.check_results[c.id].fix.kind in ("root", "user", "tweak")]
        if not items:
            self.set_status(_("Nothing to fix automatically"))
            return
        listing = "\n".join(f"• {GLib.markup_escape_text(c.title)}: "
                            f"{GLib.markup_escape_text(r.fix.label)}" for c, r in items)
        if self.confirm(_('Apply {0} fix(es)?').format(len(items)), listing):
            self.apply_fixes(items)

    # ------------------------------------------------------------ optimise

    def _build_optimise(self):
        page = vbox(14, "page")
        self.opt_header = self.page_header(
            "power-profile-performance-symbolic", _("Optimise"),
            _("Toggle tweaks, then press Apply. Switching a tweak off reverts it."),
            [self.action_button(_("Undo all…"), self.undo_all, icon_name="edit-undo-symbolic"),
             self.action_button(_("Select recommended"), self.select_recommended),
             self.action_button(_("Apply changes"), self.apply_tweaks, primary=True,
                                icon_name="emblem-ok-symbolic")])
        self.pending_lbl = self.opt_header.subtitle
        page.pack_start(self.opt_header, False, False, 0)

        page.pack_start(label(_("PROFILES"), ("kicker",)), False, False, 0)
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
            foot.pack_end(self.action_button(_("Select"), lambda pid=pr.id: self.select_profile(pid)), False, False, 0)
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
            title.pack_start(pill(_("recommended"), "ok"), False, False, 0)
        state = pill("", "dim")
        state.set_no_show_all(True)
        self.tweak_state_lbls[t.id] = state
        title.pack_start(state, False, False, 0)
        text.pack_start(title, False, False, 0)
        text.pack_start(label(t.description, ("muted",), wrap=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        sw = describe(Gtk.Switch(valign=Gtk.Align.CENTER), t.title, t.description)
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
                set_pill(lbl, _("n/a on this PC"), "dim")
            elif st == "system":
                sw.set_sensitive(False)
                set_pill(lbl, _("already active"), "info")
            elif st == "mintcare":
                sw.set_sensitive(t.revert is not None)
                set_pill(lbl, _("applied"), "ok")
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
            cnt.set_text(_('{0}/{1} active').format(on, len(ids)))

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
            self.set_status(_('{0}: {1} tweak(s) selected — press Apply changes').format(pr.title, len(picked)))
            self.log(_("Profile '{0}' selected: ").format(pr.title) + ", ".join(picked))
        else:
            self.set_status(_('Everything in the {0} profile is already active').format(pr.title))

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
            self.pending_lbl.set_markup(_('<b>{0}</b> to apply, <b>{1}</b> to revert — press <b>Apply changes</b>.').format(len(on), len(off)))
        else:
            self.pending_lbl.set_text(_("Toggle tweaks, then press Apply. Switching a tweak off reverts it."))

    def select_recommended(self):
        for t in tweaks.TWEAKS:
            sw = self.switches[t.id]
            if t.recommended and sw.get_sensitive() and not sw.get_active():
                sw.set_active(True)

    def apply_tweaks(self):
        on, off = self._pending()
        if not (on or off):
            self.set_status(_("No changes to apply"))
            return
        steps = [("off-" + t.id, "Revert: " + t.title, t.revert_script()) for t in off]
        steps += [("on-" + t.id, "Apply: " + t.title, t.apply_script()) for t in on]
        snap = self.ask_snapshot(_('{0} optimisation change(s)').format(len(steps)))
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, _("Optimisation"))
            self._refresh_tweaks()
            self.run_diagnose()
        self.in_thread(_("Applying tweaks…"), lambda: self.execute(steps, [], "Optimise", snap), done)

    def apply_recommended(self):
        self.select_recommended()
        on, off = self._pending()
        if not on and not off:
            self.set_status(_("All recommended tweaks are already active"))
            return
        listing = "\n".join(f"• {GLib.markup_escape_text(t.title)}" for t in on)
        if self.confirm(_('Apply {0} recommended tweak(s)?').format(len(on)), listing):
            self.apply_tweaks()
        else:
            self._refresh_tweaks()

    # ------------------------------------------------------------ clean

    def _build_clean(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "user-trash-symbolic", _("Clean"),
            _("Measures junk first, then removes only what you select."),
            [self.action_button(_("Rescan"), self.run_clean_scan, icon_name="view-refresh-symbolic"),
             self.action_button(_("Clean selected"), self.run_clean, primary=True,
                                icon_name="user-trash-symbolic")]), False, False, 0)

        hero = card("ecard")
        hr = hbox(16)
        left = vbox(2)
        left.pack_start(label(_("RECLAIMABLE"), ("kicker",)), False, False, 0)
        self.clean_big = label("…", ("big",))
        left.pack_start(self.clean_big, False, False, 0)
        hr.pack_start(left, False, False, 0)
        right = vbox(6)
        right.set_valign(Gtk.Align.CENTER)
        self.clean_total = label(_("Scanning…"), ("muted",), wrap=True)
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
            cb.connect("toggled", lambda *_x: self._update_clean_total())
            self.clean_checks[item.id] = cb
            box.pack_start(cb, False, False, 0)
            text = vbox(2)
            th = hbox(8)
            th.pack_start(label(item.title, ("h3",)), False, False, 0)
            if item.root_script:
                th.pack_start(pill(_("admin"), "dim"), False, False, 0)
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
        self.in_thread(_("Measuring junk…"), work, done)
        return False

    def _update_clean_total(self):
        total = sum(self.clean_bytes.get(i, 0) for i, cb in self.clean_checks.items() if cb.get_active())
        everything = sum(self.clean_bytes.values()) or 1
        n = sum(1 for cb in self.clean_checks.values() if cb.get_active())
        self.clean_big.set_text(human(total))
        self.clean_total.set_text(_("{0} of {1} items selected. Items shown as “—” can't be measured in advance and may free more.").format(n, len(self.clean_checks)))
        self.clean_bar.set_value(100 * total / everything)

    def run_clean(self):
        sel = [cleaner.BY_ID[i] for i, cb in self.clean_checks.items() if cb.get_active()]
        if not sel:
            return
        if any(i.id == "trash" for i in sel) and not self.confirm(
                _("Empty the Trash?"), _("Files in the Trash will be permanently deleted.")):
            return
        root = [(i.id, i.title, i.root_script) for i in sel if i.root_script]
        user = [(i.id + "-user", i.title, i.user_script) for i in sel if i.user_script]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, _("Clean-up"))
            if freed > 0:
                self.set_status(_('Freed {0}').format(human(freed)))
                self.log(_('Freed {0} on /').format(human(freed)))
            self.run_clean_scan()
            self.run_diagnose()
        self.in_thread(_("Cleaning…"), lambda: self.execute(root, user, "Clean"), done)

    def quick_clean(self):
        self.goto("clean")
        self.run_clean()

    # ------------------------------------------------------------ disk space

    def _build_space(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "drive-multidisk-symbolic", _("Disk space"),
            _("Find what's using your space: big folders and files, duplicate copies and old system "
            "snapshots. Files go to the Trash, so nothing is lost until you empty it.")), False, False, 0)
        self.space_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        sw = Gtk.StackSwitcher(stack=self.space_stack, halign=Gtk.Align.START)
        page.pack_start(sw, False, False, 0)
        self.space_stack.add_titled(self._build_folders_tab(), "folders", _("Folders & large files"))
        self.space_stack.add_titled(self._build_dups_tab(), "dups", _("Duplicates"))
        self.space_stack.add_titled(self._build_snaps_tab(), "snaps", _("Timeshift snapshots"))
        page.pack_start(self.space_stack, False, False, 0)
        return scrolled(page)

    # -- folders

    def _build_folders_tab(self):
        box = vbox(12)
        ctl = hbox(8)
        ctl.pack_start(self.action_button(_("Home folder"), lambda: self.scan_folders(space.HOME),
                                          icon_name="user-home-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button(_("Whole system"), lambda: self.scan_folders("/"),
                                          icon_name="drive-harddisk-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button(_("Choose folder…"), self._choose_scan_folder,
                                          icon_name="folder-symbolic"), False, False, 0)
        self.space_spinner = Gtk.Spinner()
        ctl.pack_end(self.space_spinner, False, False, 0)
        self.space_status = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        ctl.pack_end(self.space_status, True, True, 0)
        box.pack_start(ctl, False, False, 0)

        tc = card()
        nav = hbox(8)
        self.up_btn = self.action_button(_("Up"), self._tree_up, icon_name="go-up-symbolic")
        nav.pack_start(self.up_btn, False, False, 0)
        self.crumb = label("", ("crumb",), ellipsize=True)
        self.crumb.set_ellipsize(Pango.EllipsizeMode.START)
        nav.pack_start(self.crumb, True, True, 0)
        self.crumb_size = label("", ("stat-val",))
        nav.pack_end(self.crumb_size, False, False, 0)
        tc.pack_start(nav, False, False, 0)
        self.treemap = Treemap(self._treemap_activate, height=320)
        tc.pack_start(self.treemap, False, False, 10)
        tc.pack_start(label(_("Click a folder to look inside it. Hover for details."), ("faint",)), False, False, 0)
        box.pack_start(tc, False, False, 0)

        self.big_head = label(_("LARGEST FILES"), ("kicker",))
        box.pack_start(self.big_head, False, False, 0)
        self.big_card, self.big_list = list_card()
        box.pack_start(self.big_card, False, False, 0)
        self.space_tree = self.space_node = None
        self.space_big = []
        return box

    def _choose_scan_folder(self):
        d = Gtk.FileChooserDialog(title=_("Choose a folder to analyse"), transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Analyse"), Gtk.ResponseType.OK)
        d.set_current_folder(space.HOME)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path:
            self.scan_folders(path)

    def scan_folders(self, root):
        self._space_root = root
        self.space_spinner.start()
        self.space_status.set_text(_('Scanning {0} …').format(root))
        self.treemap.set_items([])

        def progress(n, path):
            GLib.idle_add(lambda: self.space_status.set_text(_('{0:,} files · {1}').format(n, path[-60:])) and False)

        def done(res):
            self.space_spinner.stop()
            if not res:
                self.space_status.set_text(_("Scan failed"))
                return
            tree, big = res
            self.space_tree, self.space_node, self.space_big = tree, tree, big
            note = _(" (unreadable system folders skipped)") if root == "/" else ""
            self.space_status.set_text(_('{0:,} files · {1}{2}').format(tree.files, human(tree.size), note))
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
            kind = _('{0:,} files').format(c.files) if c.is_dir else _("file")
            items.append((c.size, c.name + ("/" if c.is_dir else ""), f"{human(c.size)} · {pct:.0f}% · {kind}", c))
            shown += c.size
        rest = node.size - shown
        if rest > node.size * 0.005:
            items.append((rest, _("(smaller items)"), human(rest), None))
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
        self.big_head.set_text(_('LARGEST FILES IN {0} ({1})').format(self.space_node.name.upper(), len(rows)))
        if not rows:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("No files over 50 MB here."), ("muted",)))
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
            t.pack_start(label(_('Modified {0} · {1}').format(time.strftime('%Y-%m-%d', time.localtime(mtime)), Gio.content_type_get_description(ctype)), ("muted",)), False, False, 0)
            box.pack_start(t, True, True, 0)
            b2 = self.action_button(_("Trash"), lambda p=path, sz=size: self._trash_big(p, sz),
                                    icon_name="user-trash-symbolic")
            b1 = self.action_button(_("Show"), lambda p=path: self.show_in_folder(p), icon_name="folder-symbolic")
            b1.big_row = b2.big_row = True
            box.pack_end(b2, False, False, 0)
            box.pack_end(b1, False, False, 0)
            box.pack_end(label(human(size), ("stat-val",)), False, False, 8)
            row.add(box)
            self.big_list.add(row)
        self.big_list.show_all()

    def _trash_big(self, path, size):
        if not self.confirm(_('Move to Trash?').format(),
                            _('<tt>{0}</tt>\n\n{1} — you can restore it from the Trash until you empty it.').format(GLib.markup_escape_text(path), human(size))):
            return
        try:
            space.trash(path)
        except GLib.Error as e:
            self.log(_('Could not trash {0}: {1}').format(path, e.message))
            self.set_status(_("Could not move to Trash"), bad=True)
            return
        self.log(_('Moved to Trash: {0} ({1})').format(path, human(size)))
        maintenance.record("trash", f"Moved {os.path.basename(path)} to the Trash ({human(size)})", True, path)
        self.set_status(_('Moved {0} to the Trash').format(os.path.basename(path)))
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
        r1.pack_start(label(_("Search in:"), ("muted",)), False, False, 0)
        self.dup_roots = space.default_dup_roots()
        self.dup_roots_lbl = label("", ("crumb",), ellipsize=True, wrap=False)
        r1.pack_start(self.dup_roots_lbl, True, True, 0)
        r1.pack_end(self.action_button(_("Reset"), self._dup_reset_roots), False, False, 0)
        r1.pack_end(self.action_button(_("Add folder…"), self._dup_add_root, icon_name="list-add-symbolic"),
                    False, False, 0)
        c.pack_start(r1, False, False, 0)
        r2 = hbox(12)
        r2.pack_start(label(_("Ignore files smaller than"), ("muted",)), False, False, 0)
        self.dup_min = Gtk.ComboBoxText()
        for t in ("1 MB", "10 MB", "100 MB"):
            self.dup_min.append(t, t)
        self.dup_min.set_active(0)
        r2.pack_start(self.dup_min, False, False, 0)
        self.dup_hidden = Gtk.CheckButton(label=_("Include hidden files and folders"))
        r2.pack_start(self.dup_hidden, False, False, 12)
        c.pack_start(r2, False, False, 10)
        r2 = hbox(8)                      # buttons on their own row so narrow windows fit
        self.dup_cancel_btn = Gtk.Button(label=_("Cancel"))
        css(self.dup_cancel_btn, "ghost")
        self.dup_cancel_btn.connect("clicked", lambda *_x: self._space_cancel.set())
        self.dup_cancel_btn.set_sensitive(False)
        r2.pack_end(self.dup_cancel_btn, False, False, 0)
        # Not registered with action_button(): its state follows the scan, not the global busy flag
        self.dup_scan_btn = Gtk.Button()
        inner = hbox(6)
        inner.pack_start(icon("system-search-symbolic"), False, False, 0)
        inner.pack_start(Gtk.Label(label=_("Find duplicates")), False, False, 0)
        self.dup_scan_btn.add(inner)
        describe(self.dup_scan_btn, _("Find duplicates"))
        css(self.dup_scan_btn, "primary")
        self.dup_scan_btn.connect("clicked", lambda *_x: self.scan_dups())
        self.dup_running = False
        r2.pack_end(self.dup_scan_btn, False, False, 0)
        c.pack_start(r2, False, False, 0)
        self.dup_progress = Gtk.ProgressBar()
        self.dup_progress.set_no_show_all(True)
        c.pack_start(self.dup_progress, False, False, 0)
        self.dup_status = label(_("Duplicates are compared by content (BLAKE2 hash), not by name. "
                                "The oldest copy in each group is kept by default."), ("faint",), wrap=True)
        c.pack_start(self.dup_status, False, False, 6)
        box.pack_start(c, False, False, 0)

        self.dup_summary = hbox(10)
        self.dup_sel_lbl = label("", ("h2",))
        self.dup_summary.pack_start(self.dup_sel_lbl, True, True, 0)
        self.dup_trash_btn = self.action_button(_("Move selected to Trash"), self.trash_dups, primary=True,
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
        d = Gtk.FileChooserDialog(title=_("Add a folder to search"), transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Add"), Gtk.ResponseType.OK)
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
        self.dup_status.set_text(_("Listing files…"))
        min_size = {"1 MB": 1 << 20, "10 MB": 10 << 20, "100 MB": 100 << 20}[self.dup_min.get_active_id()]
        hidden = self.dup_hidden.get_active()
        roots = list(self.dup_roots)

        def progress(stage, done, total):
            def ui():
                if stage == "Listing":
                    self.dup_progress.pulse()
                    self.dup_status.set_text(_('Listing files… {0:,} candidates').format(done))
                else:
                    self.dup_progress.set_fraction(min(1.0, done / total))
                    self.dup_status.set_text(_('Comparing contents… {0} of up to {1}').format(human(done), human(total)))
                return False
            GLib.idle_add(ui)

        def done(groups):
            self.dup_running = False
            self.dup_scan_btn.set_sensitive(True)
            self.dup_cancel_btn.set_sensitive(False)
            self.dup_progress.hide()
            if self._space_cancel.is_set():
                self.dup_status.set_text(_("Cancelled."))
                return
            self.dup_groups = groups or []
            self.dup_sel = {p: i > 0 for g in self.dup_groups for i, (p, _m) in enumerate(g["files"])}
            wasted = sum(g["wasted"] for g in self.dup_groups)
            self.dup_status.set_text(_('Found {0:,} groups of identical files — {1} could be freed.').format(len(self.dup_groups), human(wasted)) if self.dup_groups
                                     else _("No duplicate files found."))
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
            hb.pack_start(label(_('{0} copies · {1} each').format(len(g['files']), human(g['size'])), ("h3",)), False, False, 0)
            hb.pack_start(pill(_('{0} wasted').format(human(g['wasted'])), "warn"), False, False, 0)
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
                    rb.pack_end(pill(_("oldest"), "ok"), False, False, 0)
                rb.pack_end(label(time.strftime("%Y-%m-%d", time.localtime(m)), ("faint", "mono")), False, False, 0)
                row.add(rb)
                self.dup_list.add(row)
        if len(self.dup_groups) > len(show):
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_('Showing the {0} groups that waste the most space (of {1:,}). Trash these, then scan again for the rest.').format(len(show), len(self.dup_groups)),
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
        visible = {p for g in self.dup_groups[:120] for p, _x in g["files"]}
        n = sum(1 for p, v in self.dup_sel.items() if v and p in visible)
        size = sum(g["size"] for g in self.dup_groups[:120] for p, _x in g["files"] if self.dup_sel.get(p))
        self.dup_sel_lbl.set_text(_('{0:,} files selected · {1}').format(n, human(size)))

    def trash_dups(self):
        groups = self.dup_groups[:120]
        picked, skipped = [], 0
        for g in groups:
            sel = [p for p, _x in g["files"] if self.dup_sel.get(p)]
            if len(sel) == len(g["files"]):
                skipped += 1                          # never remove every copy
                continue
            picked += [(p, g["size"]) for p in sel]
        if not picked:
            self.set_status(_("Nothing selected") + (_(" (a group can't have every copy selected)") if skipped else ""))
            return
        total = sum(s_ for _x, s_ in picked)
        extra = _('\n\n{0} group(s) had every copy ticked and will be skipped.').format(skipped) if skipped else ""
        if not self.confirm(_('Move {0:,} duplicate file(s) to the Trash?').format(len(picked)),
                            _('Frees {0} once you empty the Trash. One copy of each file is kept.').format(human(total))
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
            for p, _x in ok:
                self.log(_('Moved duplicate to Trash: {0}').format(p))
            if ok:
                maintenance.record("trash", _('Moved {0:,} duplicate file(s) to the Trash ({1})').format(len(ok), human(sum(s_ for _x, s_ in ok))), not failed)
            for f in failed:
                self.log(_('Could not trash {0}').format(f))
            gone = {p for p, _x in ok}
            for g in self.dup_groups:
                g["files"] = [f for f in g["files"] if f[0] not in gone]
                g["wasted"] = g["size"] * max(0, len(g["files"]) - 1)
            self.dup_groups = [g for g in self.dup_groups if len(g["files"]) > 1]
            for p in gone:
                self.dup_sel.pop(p, None)
            self.set_status(_('Moved {0:,} duplicates ({1}) to the Trash').format(len(ok), human(sum(s_ for _x, s_ in ok))),
                            bad=bool(failed))
            self._render_dups()
            self.run_clean_scan()
        self.in_thread(_("Moving duplicates to the Trash…"), work, done)

    # -- snapshots

    def _build_snaps_tab(self):
        box = vbox(12)
        c = card("ecard")
        row = hbox(16)
        left = vbox(2)
        left.pack_start(label(_("SNAPSHOTS"), ("kicker",)), False, False, 0)
        self.snap_count = label("…", ("boot-big",))
        left.pack_start(self.snap_count, False, False, 0)
        row.pack_start(left, False, False, 0)
        self.snap_info = label("", ("muted",), wrap=True)
        self.snap_info.set_valign(Gtk.Align.CENTER)
        row.pack_start(self.snap_info, True, True, 0)
        row.pack_end(self.action_button(_("Delete selected"), self.delete_snapshots, primary=True,
                                        icon_name="user-trash-symbolic"), False, False, 0)
        row.pack_end(self.action_button(_("Open Timeshift"), lambda: subprocess.Popen(
            ["timeshift-launcher"], start_new_session=True), icon_name="document-open-recent-symbolic"),
            False, False, 0)
        c.pack_start(row, False, False, 0)
        box.pack_start(c, False, False, 0)
        box.pack_start(label(_("Snapshots share unchanged files, so deleting one frees only what changed "
                             "around it. The newest snapshot is always kept, and snapshots you gave a "
                             "comment are never pre-selected."), ("faint",), wrap=True), False, False, 0)
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
            parts.append(_("Schedule: ") + ", ".join(_('{0} (keeps {1})').format(k, summary['keep'].get(k, '?'))
                                                  for k in summary["schedule"]))
        else:
            parts.append(_("No automatic schedule."))
        if summary["base_size"]:
            parts.append(_('A full system copy is about {0}; later snapshots store only changes.').format(
                human(summary['base_size'])))
        if note:
            parts.append(note)
        self.snap_info.set_text(" ".join(parts))
        for ch in self.snap_list.get_children():
            ch.destroy()
        self.snap_sel = {}
        if not snaps:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(note or _("No snapshots."), ("muted",)))
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
                th.pack_start(pill(_("newest · kept"), "ok"), False, False, 0)
            t.pack_start(th, False, False, 0)
            if sn["comment"]:
                t.pack_start(label(f"“{sn['comment']}”", ("muted",)), False, False, 0)
            t.pack_start(label(_('{0} · {1:,} files').format(sn['name'], sn['files']), ("faint", "mono")), False, False, 0)
            b.pack_start(t, True, True, 0)
            row.add(b)
            self.snap_list.add(row)
        self.snap_list.show_all()

    def delete_snapshots(self):
        names = [n for n, v in self.snap_sel.items() if v]
        if not names:
            self.set_status(_("Tick the snapshots you want to delete first"))
            return
        info = {s_["name"]: s_ for s_ in getattr(self, "snaps", [])}
        lines = []
        for n in names:
            c = info.get(n, {}).get("comment")
            lines.append(f"• {n}" + (f" — <b>“{GLib.markup_escape_text(c)}”</b>" if c else ""))
        if not self.confirm(_('Delete {0} Timeshift snapshot(s)?').format(len(names)),
                            "\n".join(lines) + _("\n\nThis can't be undone; you won't be able to roll back to "
                            "these points.")):
            return
        steps = [(f"snap{i}", _('Delete snapshot {0}').format(n), space.delete_snapshot_script(n)) for i, n in enumerate(names)]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, _("Snapshot clean-up"))
            if freed > 0:
                self.log(_('Freed {0} on /').format(human(freed)))
                self.set_status(_('Deleted {0} snapshot(s), freed {1}').format(len(names), human(freed)))
            self.load_snapshots()
        self.in_thread(_("Deleting snapshots…"), lambda: self.execute(steps, [], "Timeshift"), done)

    # ------------------------------------------------------------ generic actions

    def run_action(self, kind, payload):
        """Buttons from details panels: launch a program, open a page, apply a check's fix or a tweak."""
        if kind == "launch":
            self._launch(payload)
        elif kind == "page":
            self.goto(payload)
        elif kind == "check":
            chk = next((c for c in checks.CHECKS if c.id == payload), None)
            res = self.check_results.get(payload)
            if chk and res and res.fix:
                self.apply_fixes([(chk, res)])
            else:
                self.set_status(_("Nothing to fix there right now"))
        elif kind == "tweak":
            t = tweaks.BY_ID.get(payload)
            if t and t.state() == "off":
                steps = [("on-" + t.id, "Apply: " + t.title, t.apply_script())]
                self.in_thread(_("Applying…"), lambda: self.execute(steps, [], "Optimise"),
                               lambda r: (self.notify_done(r or {}, t.title), self._refresh_tweaks(),
                                          self.run_diagnose()))

    def _tabs(self, page, tabs):
        """Add a StackSwitcher + Stack to page. tabs: [(name, title, widget)]. Returns the stack."""
        st = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        for name, title, w in tabs:
            st.add_titled(w, name, title)
        page.pack_start(Gtk.StackSwitcher(stack=st, halign=Gtk.Align.START), False, False, 0)
        page.pack_start(st, False, False, 0)
        return st

    def _row(self, title, sub="", meta="", ico=None, pills=(), buttons=(), right=None):
        """A standard list row: icon, title + pills, subtitle, mono meta line, right value, buttons."""
        row = Gtk.ListBoxRow(activatable=False)
        box = hbox(12)
        if ico:
            box.pack_start(icon(ico, Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        th = hbox(8)
        th.pack_start(label(title, ("h3",), ellipsize=True), False, False, 0)
        for text, kind in pills:
            th.pack_start(pill(text, kind), False, False, 0)
        t.pack_start(th, False, False, 0)
        if sub:
            t.pack_start(label(sub, ("muted",), wrap=True), False, False, 0)
        if meta:
            ml = label(meta, ("faint", "mono"), ellipsize=True)
            ml.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            t.pack_start(ml, False, False, 0)
        box.pack_start(t, True, True, 0)
        for b in reversed(list(buttons)):
            box.pack_end(b, False, False, 0)
        if right:
            box.pack_end(label(right, ("stat-val",)), False, False, 6)
        body = vbox(0)
        body.pack_start(box, False, False, 0)
        row.add(body)
        row.body, row.text_box = body, t       # for details added under the row / inside its text column
        return row

    def _btn(self, text, cb, primary=False, icon_name=None, tag=None):
        b = self.action_button(text, cb, primary=primary, icon_name=icon_name)
        if tag:
            setattr(b, tag, True)
        return b

    def _clear(self, container, tag=None):
        if tag:
            self._action_buttons = [b for b in self._action_buttons if not getattr(b, tag, False)]
        for ch in container.get_children():
            ch.destroy()

    def _root_job(self, status, steps, heading, done_cb=None, user_steps=(), snapshot_reason=None):
        snap = self.ask_snapshot(snapshot_reason) if snapshot_reason else []
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, heading)
            if done_cb:
                done_cb(results or {})
        self.in_thread(status, lambda: self.execute(steps, list(user_steps), heading, snap), done)

    # ------------------------------------------------------------ packages

    def _build_packages(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "package-x-generic-symbolic", _("Packages"),
            _("What's installed and how much space it takes, what you changed and when (with undo), "
              "kernels, the fastest download mirrors and Mint upgrades.")), False, False, 0)
        # installed
        inst = vbox(10)
        bar = hbox(8)
        self.pkg_mode = Gtk.ComboBoxText()
        for k, t in (("largest", _("Largest packages")), ("mine", _("Installed by you")), ("held", _("Held back"))):
            self.pkg_mode.append(k, t)
        self.pkg_mode.set_active_id("largest")
        self.pkg_mode.connect("changed", lambda *_x: self._render_packages())
        bar.pack_start(self.pkg_mode, False, False, 0)
        self.pkg_filter = Gtk.SearchEntry(placeholder_text=_("Filter…"))
        self.pkg_filter.connect("search-changed", lambda *_x: self._render_packages())
        bar.pack_start(self.pkg_filter, True, True, 0)
        self.pkg_summary = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        bar.pack_end(self.pkg_summary, False, False, 0)
        inst.pack_start(bar, False, False, 0)
        self.pkg_card, self.pkg_list = list_card()
        inst.pack_start(self.pkg_card, False, False, 0)
        # history
        hist = vbox(10)
        hist.pack_start(label(_("Undo removes the packages that change installed (dependencies that are no longer "
                                "needed go too). You'll see exactly what will be removed before anything happens."),
                              ("faint",), wrap=True), False, False, 0)
        self.hist_pkg_card, self.hist_pkg_list = list_card()
        hist.pack_start(self.hist_pkg_card, False, False, 0)
        # kernels
        kern = vbox(10)
        kern.pack_start(label(_("Keep the running kernel and the newest one; older kernels are only a fallback. "
                                "Removing one frees space in /boot."), ("faint",), wrap=True), False, False, 0)
        self.kern_card, self.kern_list = list_card()
        kern.pack_start(self.kern_card, False, False, 0)
        # mirrors
        mir = vbox(10)
        mc = card("ecard")
        mh = hbox(8)
        self.mir_status = label(_("Find the fastest Linux Mint and Ubuntu mirrors near you."), ("muted",), wrap=True)
        mh.pack_start(self.mir_status, True, True, 0)
        self.mir_restore_btn = self._btn(_("Restore original"), self.restore_mirrors)
        self.mir_restore_btn.set_no_show_all(True)
        mh.pack_end(self.mir_restore_btn, False, False, 0)
        self.mir_use_btn = self._btn(_("Use the fastest"), self.use_fastest_mirrors, primary=True)
        self.mir_use_btn.set_no_show_all(True)
        mh.pack_end(self.mir_use_btn, False, False, 0)
        mh.pack_end(self._btn(_("Test mirrors"), self.test_mirrors, icon_name="network-transmit-receive-symbolic"),
                    False, False, 0)
        mc.pack_start(mh, False, False, 0)
        self.mir_progress = Gtk.ProgressBar()
        self.mir_progress.set_no_show_all(True)
        mc.pack_start(self.mir_progress, False, False, 6)
        mir.pack_start(mc, False, False, 0)
        self.mir_card, self.mir_list = list_card()
        mir.pack_start(self.mir_card, False, False, 0)
        # upgrade
        up = vbox(10)
        uc = card("ecard")
        uh = hbox(8)
        rel, code = packages.mint_release()
        self.upg_lbl = label(_("You're on Linux Mint {0} “{1}”.").format(rel, code.capitalize()), ("h2",), wrap=True)
        uh.pack_start(self.upg_lbl, True, True, 0)
        uh.pack_end(self._btn(_("Check for a new release"), self.check_mint_upgrade,
                              icon_name="software-update-available-symbolic"), False, False, 0)
        uc.pack_start(uh, False, False, 0)
        self.upg_sub = label(_("Point releases (22.x) arrive through Update Manager › Edit › Upgrade; a new major "
                               "version uses Mint's upgrade tool."), ("muted",), wrap=True)
        uc.pack_start(self.upg_sub, False, False, 6)
        self.upg_box = vbox(4)
        uc.pack_start(self.upg_box, False, False, 0)
        up.pack_start(uc, False, False, 0)
        self.pkg_tabs = self._tabs(page, [("installed", _("Installed"), inst), ("history", _("History"), hist),
                                          ("kernels", _("Kernels"), kern), ("mirrors", _("Mirrors"), mir),
                                          ("upgrade", _("Mint upgrade"), up)])
        self.pkg_data = None
        return scrolled(page)

    def load_packages(self):
        def work():
            pk = packages.installed()
            h = packages.history()
            return {"pk": pk, "hist": h, "mine": packages.user_installed(pk, h), "held": packages.held(),
                    "kernels": packages.kernels(pk)}

        def done(d):
            if not d:
                return
            self.pkg_data = d
            self._render_packages()
            self._render_pkg_history()
            self._render_kernels()
        self.bg(work, done)

    def _render_packages(self):
        d = self.pkg_data
        if not d:
            return
        self._clear(self.pkg_list, "pkg_row")
        mode = self.pkg_mode.get_active_id()
        flt = self.pkg_filter.get_text().strip().lower()
        pk = d["pk"]
        if mode == "largest":
            names = [n for _s, n, _v in packages.largest(pk, 400)]
        elif mode == "mine":
            names = sorted(d["mine"], key=lambda n: -pk[n]["size"])
        else:
            names = [n for n in d["held"] if n in pk]
        if flt:
            names = [n for n in names if flt in n.lower() or flt in pk[n]["desc"].lower()]
        total = sum(pk[n]["size"] for n in names)
        self.pkg_summary.set_text(_('{0} packages · {1}  (of {2:,} installed)').format(len(names), human(total), len(pk)))
        for n in names[:80]:
            v = pk[n]
            prot = packages.protected(n, v)
            btns = []
            if mode == "held":
                btns.append(self._btn(_("Release hold"), lambda n=n: self._root_job(
                    _("Releasing hold…"), [packages.unhold_step(n)], "Packages", lambda r: self.load_packages()),
                    tag="pkg_row"))
            elif not prot:
                btns.append(self._btn(_("Remove…"), lambda n=n: self.remove_packages([n]), tag="pkg_row"))
            pills = [(_("system"), "dim")] if prot else []
            self.pkg_list.add(self._row(n, v["desc"], f"{v['version']} · {v['section']}", None, pills, btns,
                                        human(v["size"])))
        if not names:
            self.pkg_list.add(self._row(_("Nothing to show"), _("No packages match.")))
        self.pkg_list.show_all()

    def remove_packages(self, names, heading=_("Remove packages")):
        removed, ok = packages.simulate_remove(names)
        if not ok or not removed:
            self.set_status(_("apt can't remove that cleanly — see the Activity log"), bad=True)
            self.log(_('apt-get -s remove {0} failed or would remove nothing').format(' '.join(names)))
            return
        extra = [r for r in removed if r not in names]
        body = (_("This removes:") + "\n<tt>" + GLib.markup_escape_text(", ".join(removed[:40])) +
                ("…" if len(removed) > 40 else "") + "</tt>")
        if extra:
            body += "\n\n<b>" + _('{0} other package(s) depend on it and go too.').format(len(extra)) + "</b>"
        if not self.confirm(_('Remove {0} package(s)?').format(len(removed)), body):
            return
        self._root_job(_("Removing packages…"), [packages.remove_step(names)], "Packages",
                       lambda r: (self.load_packages(), self.run_clean_scan()), snapshot_reason=_("removing packages"))

    def _render_pkg_history(self):
        d = self.pkg_data
        self._clear(self.hist_pkg_list, "hist_row")
        for e in d["hist"][:80]:
            parts = [f"{len(e[k])} {k}" for k in ("install", "upgrade", "remove", "purge") if e[k]]
            who = "Software Manager" if "aptkit" in e["cmd"] or "packagekit" in e["cmd"] else \
                  ("Update Manager" if "mintupdate" in e["cmd"] or "unattended" in e["cmd"] else "apt")
            cand = packages.undo_candidates(e, d["pk"])
            btns = [self._btn(_("Undo…"), lambda c=cand: self.remove_packages(c), tag="hist_row")] if cand else []
            names = ", ".join(p["pkg"] for p in (e["install"] or e["remove"] or e["upgrade"])[:6])
            self.hist_pkg_list.add(self._row(f"{e['date']} — {', '.join(parts) or _('no changes')}", names,
                                             e["cmd"][:200], None, [(who, "dim")], btns))
        self.hist_pkg_list.show_all()

    def _render_kernels(self):
        self._clear(self.kern_list, "kern_row")
        for k in self.pkg_data["kernels"]:
            pills = [(_("running"), "ok")] if k["running"] else []
            if k["newest"]:
                pills.append((_("newest"), "info"))
            btns = [] if k["running"] or k["newest"] else [
                self._btn(_("Remove"), lambda k=k: self._remove_kernel(k), tag="kern_row")]
            self.kern_list.add(self._row(_('Linux {0}').format(k['version']), ", ".join(k["packages"][:4]), "", None, pills,
                                         btns, human(k["size"])))
        self.kern_list.show_all()

    def _remove_kernel(self, k):
        if self.confirm(_('Remove kernel {0}?').format(k['version']),
                        _("You keep the running and newest kernels to boot from. The boot menu is updated.")):
            self._root_job(_("Removing kernel…"), [packages.kernel_remove_step(k)], "Packages",
                           lambda r: self.load_packages())

    def test_mirrors(self):
        self.mir_progress.show()
        self.mir_status.set_text(_("Testing mirrors…"))

        def prog(done, total):
            GLib.idle_add(lambda: self.mir_progress.set_fraction(done / max(total, 1)) or False)

        def done(r):
            self.mir_progress.hide()
            self.mirror_results = r
            self._clear(self.mir_list)
            if not r:
                return
            cur_m, cur_u = packages.current_mirrors()
            for kind, title in (("mint", "Linux Mint"), ("ubuntu", "Ubuntu")):
                self.mir_list.add(self._row(_('{0} mirrors').format(title), "", "", None))
                for m in r[kind][:6]:
                    sp = f"{m['speed'] / 1e6:.1f} MB/s" if m["speed"] else _("no answer")
                    pills = []
                    if m["url"] in (cur_m, cur_u):
                        pills.append((_("current"), "info"))
                    if m["stale"]:
                        pills.append((_("out of date"), "warn"))
                    self.mir_list.add(self._row(m["url"], "", "", None, pills, (), sp))
            best = [next((m for m in r[k] if m["speed"] and not m["stale"]), None) for k in ("mint", "ubuntu")]
            gain = [b for b, cur in zip(best, (cur_m, cur_u)) if b and b["url"] != cur]
            self.mir_status.set_text(_('Tested mirrors in {0}. ').format(r['country'] or _('your area')) +
                                     (_("A faster mirror is available.") if gain else _("You're already on the fastest.")))
            self.mir_use_btn.set_visible(bool(gain))
            self.mir_list.show_all()
        self.bg(lambda: packages.test_mirrors(prog), done)
        self.mir_restore_btn.set_visible(Path("/var/lib/mintcare/official-package-repositories.list.orig").exists())

    def use_fastest_mirrors(self):
        r = getattr(self, "mirror_results", None)
        if not r:
            return
        pick = [next((m["url"] for m in r[k] if m["speed"] and not m["stale"]), None) for k in ("mint", "ubuntu")]
        if not self.confirm(_("Switch to the fastest mirrors?"),
                            _('Linux Mint: <tt>{0}</tt>\nUbuntu: <tt>{1}</tt>\n\nThe original list is backed up and can be restored here.').format(GLib.markup_escape_text(pick[0] or '-'), GLib.markup_escape_text(pick[1] or '-'))):
            return
        self._root_job(_("Switching mirrors…"), [packages.switch_mirrors_step(*pick)], "Sources",
                       lambda res: (self.test_mirrors(), self.load_software() if self._sw_loaded else None))

    def restore_mirrors(self):
        self._root_job(_("Restoring mirrors…"), [packages.restore_mirrors_step()], "Sources",
                       lambda res: self.test_mirrors())

    def check_mint_upgrade(self):
        self.upg_sub.set_text(_("Checking…"))

        def done(res):
            new, ready = res or (None, [])
            self._clear(self.upg_box, "upg_row")
            if new:
                self.upg_sub.set_markup(_('<b>Linux Mint {0} “{1}” is available.</b> Check the list below, then start the upgrade tool.').format(GLib.markup_escape_text(new[1]), GLib.markup_escape_text(new[0].capitalize())))
            else:
                self.upg_sub.set_text(_("No newer Linux Mint release yet. Point releases also appear in Update "
                                        "Manager › Edit › Upgrade."))
            for lvl, text in ready:
                r = hbox(6)
                r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
                r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
                self.upg_box.pack_start(r, False, False, 0)
            if new:
                b = self._btn(_("Start the upgrade tool"), self._start_mintupgrade, primary=True, tag="upg_row")
                self.upg_box.pack_start(b, False, False, 6)
            self.upg_box.show_all()
        self.bg(lambda: (packages.upgrade_available(), packages.upgrade_readiness()), done)

    def _start_mintupgrade(self):
        if shutil.which("mintupgrade"):
            self._launch("mintupgrade")
            return
        self._root_job(_("Installing Mint's upgrade tool…"),
                       [("mintupgrade", _("Install mintupgrade"), "DEBIAN_FRONTEND=noninteractive apt-get install -y mintupgrade")],
                       "Packages", lambda r: self._launch("mintupgrade") if r.get("mintupgrade") == 0 else None)

    # ------------------------------------------------------------ processes

    def _build_processes(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "utilities-system-monitor-symbolic", _("Processes"),
            _("What's using your processor and memory right now, what each program is, and a way to close one "
              "that's stuck.")), False, False, 0)
        bar = hbox(8)
        self.proc_sort = Gtk.ComboBoxText()
        self.proc_sort.append("cpu", _("Sort by CPU"))
        self.proc_sort.append("rss", _("Sort by memory"))
        self.proc_sort.set_active_id("cpu")
        bar.pack_start(self.proc_sort, False, False, 0)
        self.proc_filter = Gtk.SearchEntry(placeholder_text=_("Filter by name…"))
        bar.pack_start(self.proc_filter, True, True, 0)
        self.proc_all = Gtk.CheckButton(label=_("Include system processes"))
        bar.pack_start(self.proc_all, False, False, 0)
        self.proc_pause = Gtk.CheckButton(label=_("Pause"))
        bar.pack_end(self.proc_pause, False, False, 0)
        page.pack_start(bar, False, False, 0)
        self.proc_head = label("", ("faint", "mono"))
        page.pack_start(self.proc_head, False, False, 0)
        self.proc_card, self.proc_list = list_card()
        page.pack_start(self.proc_card, False, False, 0)
        self._proc_prev = None
        self._proc_open = set()
        for w in (self.proc_sort, self.proc_all):
            w.connect("changed" if isinstance(w, Gtk.ComboBoxText) else "toggled", lambda *_x: self._poll_procs(True))
        self.proc_filter.connect("search-changed", lambda *_x: self._poll_procs(True))
        GLib.timeout_add(2500, self._poll_procs)
        return scrolled(page)

    def _poll_procs(self, force=False):
        if self.stack.get_visible_child_name() != "processes" or (self.proc_pause.get_active() and not force):
            return True
        if getattr(self, "_proc_busy", False):
            return True
        self._proc_busy = True

        def done(res):
            self._proc_busy = False
            if res:
                rows, self._proc_prev = res
                self._render_procs(rows)
        self.bg(lambda: processes.processes(self._proc_prev), done)
        return True

    def _render_procs(self, rows):
        self._clear(self.proc_list, "proc_row")
        key = self.proc_sort.get_active_id()
        flt = self.proc_filter.get_text().strip().lower()
        shown = [r for r in rows if (self.proc_all.get_active() or r["mine"]) and not r["kernel"]
                 and (not flt or flt in r["name"].lower() or flt in r["cmd"].lower())]
        shown.sort(key=lambda r: -r[key])
        tot_cpu = sum(r["cpu"] for r in rows)
        # xgettext:no-python-format
        self.proc_head.set_text(_('{0} processes · {1:.0f}% of all CPU · showing {2}').format(len(rows), tot_cpu / (os.cpu_count() or 1), min(len(shown), 40)))
        for r in shown[:40]:
            pills = [] if r["mine"] else [(r["user"], "dim")]
            btns = [self._btn(_("What is this?"), lambda r=r: self._proc_info(r), tag="proc_row"),
                    self._btn(_("End"), lambda r=r: self._end_proc(r), tag="proc_row")]
            row = self._row(r["name"], "", r["cmd"][:300], None, pills, btns,
                            f"{r['cpu']:4.0f}% · {human(r['rss'])}")
            if r["pid"] in self._proc_open:
                info = processes.describe(r)
                txt = info["text"] or _("No description for this one.")
                if info["package"]:
                    txt += f"\nPackage: {info['package']} — {info['summary']}"
                row.text_box.pack_start(label(txt, ("muted",), wrap=True), False, False, 0)
            self.proc_list.add(row)
        self.proc_list.show_all()

    def _proc_info(self, r):
        (self._proc_open.discard if r["pid"] in self._proc_open else self._proc_open.add)(r["pid"])
        self._poll_procs(True)

    def _end_proc(self, r):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text=_('End {0} ({1})?').format(r['name'], r['pid']))
        css(d, "mintcare")
        d.format_secondary_text(_("Unsaved work in that program will be lost. 'Force' stops a program that "
                                  "doesn't respond."))
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Force"), 2, _("End"), Gtk.ResponseType.OK)
        resp = d.run()
        d.destroy()
        if resp not in (Gtk.ResponseType.OK, 2):
            return
        force = resp == 2
        if r["mine"]:
            try:
                processes.end(r["pid"], force)
                maintenance.record("other", f"Ended {r['name']} ({r['pid']})")
                self.set_status(_('Ended {0}').format(r['name']))
            except ProcessLookupError:
                self.set_status(_("It had already closed"))
            except PermissionError:
                self.set_status(_("Not allowed to end that process"), bad=True)
            GLib.timeout_add(600, lambda: self._poll_procs(True) and False)
        else:
            self._root_job(_('Ending {0}…').format(r['name']), [processes.end_step(r["pid"], r["name"], force)], "Processes",
                           lambda res: self._poll_procs(True))

    # ------------------------------------------------------------ troubleshoot

    def _build_troubleshoot(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "dialog-question-symbolic", _("Troubleshoot"),
            _("Step-by-step checks and one-click fixes for sound, network, printers, Bluetooth and crashing "
              "programs.")), False, False, 0)
        self.ts_boxes = {}
        tabs = []
        for key, title in (("sound", _("Sound")), ("network", _("Network")), ("devices", _("Printers & Bluetooth")),
                           ("crashes", _("Crash reports"))):
            b = vbox(10)
            self.ts_boxes[key] = b
            tabs.append((key, title, b))
        self.ts_tabs = self._tabs(page, tabs)
        self.ts_tabs.connect("notify::visible-child-name", lambda *_x: self.load_troubleshoot())
        return scrolled(page)

    def load_troubleshoot(self):
        key = self.ts_tabs.get_visible_child_name()
        box = self.ts_boxes[key]
        self._clear(box, "ts_" + key)
        box.pack_start(label(_("Checking…"), ("muted",)), False, False, 0)
        box.show_all()
        work = {"sound": troubleshoot.audio, "network": troubleshoot.network,
                "devices": lambda: (troubleshoot.printers(), troubleshoot.bluetooth()),
                "crashes": troubleshoot.crashes}[key]
        render = {"sound": self._render_sound, "network": self._render_network, "devices": self._render_devices,
                  "crashes": self._render_crashes}[key]
        self.bg(work, lambda res: render(res) if res is not None else None)

    def _issues_card(self, box, issues, handler, tag):
        c = card("ecard")
        if not issues:
            r = hbox(8)
            r.pack_start(icon("emblem-ok-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "ok"), False, False, 0)
            r.pack_start(label(_("No problems found."), ("h3",)), True, True, 0)
            c.pack_start(r, False, False, 0)
        for lvl, text, action in issues:
            r = hbox(8)
            r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.LARGE_TOOLBAR, STATUS_ICON[lvl][1]), False, False, 0)
            r.pack_start(label(text, ("h3",), wrap=True), True, True, 0)
            if action:
                r.pack_end(self._btn(_("Fix"), lambda a=action: handler(a), primary=True, tag=tag), False, False, 0)
            c.pack_start(r, False, False, 4)
        box.pack_start(c, False, False, 0)

    def _render_sound(self, a):
        box = self.ts_boxes["sound"]
        self._clear(box, "ts_sound")
        self._issues_card(box, a["issues"], self._sound_fix, "ts_sound")
        bar = hbox(8)
        bar.pack_start(label(_('Sound server: {0}').format(a['server'] or _('not running')), ("faint", "mono")), True, True, 0)
        for text, cb in ((_("Play test sound"), troubleshoot.test_sound),
                         (_("Restart sound system"), lambda: self._user_fix(troubleshoot.AUDIO_RESTART, "sound")),
                         (_("Reset sound settings"), self._reset_sound)):
            bar.pack_end(self._btn(text, cb, tag="ts_sound"), False, False, 0)
        box.pack_start(bar, False, False, 0)
        c, lb = list_card()
        for sname in a["sinks"]:
            is_def = sname["name"] == a["default"]
            pills = [(_("default"), "ok")] if is_def else []
            if sname["mute"]:
                pills.append((_("muted"), "warn"))
            btns = [] if is_def else [self._btn(_("Use this"), lambda n=sname["name"]: (
                troubleshoot.set_default(n), self.load_troubleshoot()), tag="ts_sound")]
            btns.append(self._btn(_("Set to 70%"), lambda n=sname["name"]: (
                troubleshoot.set_volume(n, 70), self.load_troubleshoot()), tag="ts_sound"))
            lb.add(self._row(sname["desc"], ", ".join(p for p in sname["ports"] if p), sname["name"],
                             "audio-speakers-symbolic", pills, btns, f"{sname['volume']}%"))
        box.pack_start(label(_("OUTPUTS"), ("kicker",)), False, False, 0)
        box.pack_start(c, False, False, 0)
        box.show_all()

    def _sound_fix(self, action):
        a = troubleshoot.audio()
        sink = a["default"] or (a["sinks"][0]["name"] if a["sinks"] else "")
        if action == "restart":
            self._user_fix(troubleshoot.AUDIO_RESTART, "sound")
            return
        if action == "default" and sink:
            troubleshoot.set_default(sink)
        elif action in ("unmute", "normal") and sink:
            troubleshoot.set_volume(sink, 70 if action == "unmute" or a["sinks"] else 70)
        maintenance.record("other", _("Sound: fixed volume/default output"))
        troubleshoot.test_sound()
        self.load_troubleshoot()

    def _reset_sound(self):
        if self.confirm(_("Reset sound settings?"), _("Forgets saved volumes and device choices (a copy is kept in "
                                                      "~/.local/state/wireplumber.mintcare-bak), then restarts the "
                                                      "sound system.")):
            self._user_fix(troubleshoot.AUDIO_RESET, "sound")

    def _user_fix(self, script, tab):
        def done(res):
            self.notify_done(res or {}, _("Fix"))
            GLib.timeout_add(1500, lambda: self.load_troubleshoot() and False)
        self.in_thread(_("Working…"), lambda: self.execute([], [("fix", script.split(";")[0][:60], script)],
                                                           "Troubleshoot"), done)

    def _render_network(self, n):
        box = self.ts_boxes["network"]
        self._clear(box, "ts_network")
        self._issues_card(box, n["issues"], self._net_fix, "ts_network")
        c = card()
        g = Gtk.Grid(column_spacing=16, row_spacing=6)
        rows = []
        for d in n["devices"]:
            sp = f" · {d['speed']} Mb/s" if d.get("speed") else ""
            rows.append((d["name"], f"{d['type']} · {d['state']} · {d['connection']}{sp}"))
        if n["wifi"]:
            w = n["wifi"]
            rows.append(("Wi-Fi", _('{0} · signal {1}% · channel {2} ({3}) · {4} networks nearby on the same channel').format(
                w['ssid'], w['signal'], w['chan'], w['freq'], w['crowd'])))
        for name, (loss, avg) in n["latency"].items():
            rows.append((_('Ping {0}').format(name),
                         # xgettext:no-python-format
                         _('{0:.1f} ms, {1}% loss').format(avg, loss) if avg is not None
                         # xgettext:no-python-format
                         else _('{0}% loss').format(loss)))
        rows.append((_("DNS lookup"), f"{n['dns_ms']:.0f} ms" if n["dns_ms"] is not None else "?"))
        rows.append((_("Router"), n["gateway"] or _("none")))
        for i, (k, v) in enumerate(rows):
            g.attach(label(k, ("kv-key",)), 0, i, 1, 1)
            g.attach(label(v, ("kv-val",), wrap=True), 1, i, 1, 1)
        c.pack_start(g, False, False, 0)
        bar = hbox(8)
        bar.pack_end(self._btn(_("Flush DNS cache"), lambda: self._net_fix("flushdns"), tag="ts_network"), False, False, 0)
        bar.pack_end(self._btn(_("Reset networking"), lambda: self._net_fix("reset"), tag="ts_network"), False, False, 0)
        bar.pack_end(self._btn(_("Test again"), self.load_troubleshoot, icon_name="view-refresh-symbolic",
                               tag="ts_network"), False, False, 0)
        c.pack_start(bar, False, False, 10)
        box.pack_start(c, False, False, 0)
        if n["nearby"]:
            box.pack_start(label(_("NEARBY WI-FI"), ("kicker",)), False, False, 0)
            nc, nl = list_card()
            for w in sorted(n["nearby"], key=lambda w: -w["signal"])[:12]:
                nl.add(self._row(w["ssid"] or _("(hidden)"), "", f"channel {w['chan']} · {w['freq']} · {w['security']}",
                                 None, [(_("connected"), "ok")] if w["active"] else [], (), f"{w['signal']}%"))
            box.pack_start(nc, False, False, 0)
        box.show_all()

    def _net_fix(self, action):
        if action == "reset":
            if self.confirm(_("Reset networking?"), _("Disconnects for a few seconds, then reconnects.")):
                self._user_fix(troubleshoot.NET_RESET, "network")
        elif action == "flushdns":
            self._root_job(_("Flushing DNS…"), [("dns", _("Flush DNS cache"), troubleshoot.DNS_FLUSH)], "Troubleshoot",
                           lambda r: self.load_troubleshoot())

    def _render_devices(self, res):
        pr, bt = res
        box = self.ts_boxes["devices"]
        self._clear(box, "ts_devices")
        box.pack_start(label(_("PRINTERS"), ("kicker",)), False, False, 0)
        self._issues_card(box, pr["issues"], self._printer_fix, "ts_devices")
        c, lb = list_card()
        for p_ in pr["printers"]:
            is_def = p_["name"] == pr["default"]
            pills = [(_("default"), "ok")] if is_def else []
            if p_["disabled"]:
                pills.append((_("paused"), "warn"))
            btns = [] if is_def else [self._btn(_("Make default"), lambda n=p_["name"]: (
                troubleshoot.set_default_printer(n), maintenance.record("other", _('Default printer: {0}').format(n)),
                self.load_troubleshoot()), tag="ts_devices")]
            lb.add(self._row(p_["name"].replace("_", " "), p_["state"], "", "printer-symbolic", pills, btns))
        for j in pr["jobs"]:
            lb.add(self._row(_('Job {0}').format(j['id']), j["line"], "", "document-print-symbolic", [(_("waiting"), "warn")]))
        if not pr["printers"]:
            lb.add(self._row(_("No printers set up"), _("Add one in Menu › Printers.")))
        box.pack_start(c, False, False, 0)
        bar = hbox(8)
        bar.pack_end(self._btn(_("Restart printing service"), lambda: self._printer_fix("restart-cups"),
                               tag="ts_devices"), False, False, 0)
        if pr["jobs"]:
            bar.pack_end(self._btn(_("Cancel all jobs"), lambda: self._printer_fix("cancel"), tag="ts_devices"),
                         False, False, 0)
        bar.pack_end(self._btn(_("Printer settings"), lambda: self._launch("system-config-printer"), tag="ts_devices"),
                     False, False, 0)
        box.pack_start(bar, False, False, 0)

        box.pack_start(label(_("BLUETOOTH"), ("kicker",)), False, False, 8)
        if not bt["available"]:
            box.pack_start(label(_("No Bluetooth adapter found."), ("muted",)), False, False, 0)
        else:
            self._issues_card(box, bt["issues"], lambda a: (troubleshoot.bt_power(True), self.load_troubleshoot()),
                              "ts_devices")
            c2, lb2 = list_card()
            for d in bt["devices"]:
                pills = [(_("connected"), "ok")] if d["connected"] else []
                if d["battery"] is not None:
                    pills.append((_('battery {0}%').format(d['battery']), "warn" if d["battery"] < 20 else "dim"))
                btns = [self._btn(_("Forget"), lambda m=d["mac"], n=d["name"]: self._bt_forget(m, n), tag="ts_devices")]
                if not d["connected"]:
                    btns.insert(0, self._btn(_("Connect"), lambda m=d["mac"]: self.bg(
                        lambda: troubleshoot.bt_connect(m), lambda r: self.load_troubleshoot()), tag="ts_devices"))
                lb2.add(self._row(d["name"], "", d["mac"], "bluetooth-active-symbolic", pills, btns))
            if not bt["devices"]:
                lb2.add(self._row(_("No paired devices"), ""))
            box.pack_start(c2, False, False, 0)
            bar2 = hbox(8)
            bar2.pack_end(self._btn(_("Restart Bluetooth"), lambda: self._root_job(
                _("Restarting Bluetooth…"), [("bt", "Restart Bluetooth", troubleshoot.BT_RESTART)], "Troubleshoot",
                lambda r: self.load_troubleshoot()), tag="ts_devices"), False, False, 0)
            bar2.pack_end(self._btn(_("Pair a device"), lambda: self._launch("blueman-manager"), tag="ts_devices"),
                          False, False, 0)
            box.pack_start(bar2, False, False, 0)
        box.show_all()

    def _printer_fix(self, action):
        if action == "default":
            pr = troubleshoot.printers()
            names = [p_["name"] for p_ in pr["printers"]]
            best = next((n for n in names if not n.endswith("_USB")), names[0] if names else None)
            if best:
                troubleshoot.set_default_printer(best)
                self.set_status(_('{0} is now your default printer').format(best))
                maintenance.record("other", _('Default printer: {0}').format(best))
            self.load_troubleshoot()
        elif action == "cancel":
            if self.confirm(_("Cancel all print jobs?"), _("Everything waiting to print is removed.")):
                self._root_job(_("Cancelling jobs…"), [("cancel", _("Cancel all print jobs"), troubleshoot.CANCEL_ALL)],
                               "Troubleshoot", lambda r: self.load_troubleshoot())
        elif action == "restart-cups":
            self._root_job(_("Restarting printing…"), [("cups", "Restart CUPS", troubleshoot.CUPS_RESTART)],
                           "Troubleshoot", lambda r: self.load_troubleshoot())
        elif action.startswith("enable:"):
            self._root_job(_("Resuming printer…"), [troubleshoot.enable_printer_step(action[7:])], "Troubleshoot",
                           lambda r: self.load_troubleshoot())

    def _bt_forget(self, mac, name):
        if self.confirm(_('Forget {0}?').format(name), _("You'll need to pair it again to use it.")):
            troubleshoot.bt_remove(mac)
            self.load_troubleshoot()

    def _render_crashes(self, items):
        box = self.ts_boxes["crashes"]
        self._clear(box, "ts_crashes")
        box.pack_start(label(_("Programs that crashed recently. The same program crashing again and again usually "
                               "means it needs updating or reinstalling."), ("faint",), wrap=True), False, False, 0)
        c, lb = list_card()
        lb.connect("row-activated", lambda _l, row: getattr(row, "toggle_details", lambda: None)())
        for it in items[:40]:
            when = time.strftime("%a %d %b %H:%M", time.localtime(it["time"]))
            row = self._row(it["name"], f"{it['signal']} · {when}", it["exe"], "dialog-error-symbolic",
                            [(_("dump kept"), "dim")] if it["present"] else [], (), human(it["size"]) if it["size"] else "")
            row.set_activatable(True)
            row.set_tooltip_text(_("Click for the full crash report"))
            holder = vbox(0)
            row.body.pack_start(holder, False, False, 0)

            def toggle(h=holder, pid=it["pid"]):
                if h.get_children():
                    for ch in h.get_children():
                        ch.destroy()
                    return
                tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
                css(tv, "log")
                sw = Gtk.ScrolledWindow(min_content_height=260)
                sw.add(tv)
                h.pack_start(sw, True, True, 0)
                h.show_all()
                self.bg(lambda: troubleshoot.crash_info(pid), lambda t: tv.get_buffer().set_text(t or ""))
            row.toggle_details = toggle
            lb.add(row)
        if not items:
            lb.add(self._row(_("No crash reports"), _("Nothing has crashed recently.")))
        box.pack_start(c, False, False, 0)
        if items:
            bar = hbox(8)
            bar.pack_end(self._btn(_("Delete stored crash dumps"), lambda: self.confirm(
                _("Delete stored crash dumps?"), _("Frees the space they use; the list of crashes stays in the log."))
                and self._root_job(_("Deleting…"), [("dumps", _("Delete crash dumps"), troubleshoot.CLEAR_DUMPS)],
                                   "Troubleshoot", lambda r: self.load_troubleshoot()), tag="ts_crashes"), False, False, 0)
            box.pack_start(bar, False, False, 0)
        box.show_all()

    # ------------------------------------------------------------ backup

    def _build_backup(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "document-save-symbolic", _("Backup"),
            _("Copies your personal folders to another drive. Each backup is a dated snapshot; files that didn't "
              "change are hard-linked, so extra snapshots cost almost no space. Timeshift covers the system; this "
              "covers your files.")), False, False, 0)
        dc = card("ecard")
        h = hbox(8)
        h.pack_start(label(_("Destination"), ("h2",)), False, False, 0)
        self.bk_dest = label("", ("crumb",), ellipsize=True)
        h.pack_start(self.bk_dest, True, True, 0)
        h.pack_end(self._btn(_("Choose drive…"), self._bk_choose_dest, icon_name="drive-harddisk-symbolic"),
                   False, False, 0)
        dc.pack_start(h, False, False, 0)
        self.bk_dest_issues = vbox(2)
        dc.pack_start(self.bk_dest_issues, False, False, 6)
        dc.pack_start(Gtk.Separator(), False, False, 6)
        fh = hbox(8)
        fh.pack_start(label(_("Folders"), ("h2",)), False, False, 0)
        self.bk_size = label("", ("faint", "mono"))
        fh.pack_start(self.bk_size, True, True, 0)
        fh.pack_end(self._btn(_("Add folder…"), self._bk_add_folder, icon_name="list-add-symbolic"), False, False, 0)
        dc.pack_start(fh, False, False, 0)
        self.bk_folders = vbox(2)
        dc.pack_start(self.bk_folders, False, False, 4)
        dc.pack_start(Gtk.Separator(), False, False, 6)
        sh = hbox(10)
        sh.pack_start(label(_("Automatic backup"), ("h2",)), False, False, 0)
        self.bk_switch = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Automatic backup"))
        self.bk_switch.connect("notify::active", self._bk_toggle)
        sh.pack_start(self.bk_switch, False, False, 0)
        self.bk_freq = Gtk.ComboBoxText()
        for k, t in (("daily", _("Every day")), ("weekly", _("Every Sunday")), ("monthly", _("Monthly"))):
            self.bk_freq.append(k, t)
        self.bk_freq.connect("changed", lambda *_x: self._bk_save())
        sh.pack_start(self.bk_freq, False, False, 0)
        sh.pack_start(label(_("Keep"), ("muted",)), False, False, 0)
        self.bk_keep = Gtk.SpinButton.new_with_range(2, 60, 1)
        self.bk_keep.connect("value-changed", lambda *_x: self._bk_save())
        sh.pack_start(self.bk_keep, False, False, 0)
        sh.pack_start(label(_("snapshots"), ("muted",)), False, False, 0)
        dc.pack_start(sh, False, False, 0)
        self.bk_status = label("", ("muted",), wrap=True)
        dc.pack_start(self.bk_status, False, False, 6)
        rh = hbox(8)
        self.bk_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.bk_progress.set_no_show_all(True)
        rh.pack_start(self.bk_progress, True, True, 0)
        self.bk_cancel = Gtk.Button(label=_("Cancel"))
        css(self.bk_cancel, "ghost")
        self.bk_cancel.set_no_show_all(True)
        self.bk_cancel.connect("clicked", lambda *_x: self._bk_cancel_ev.set())
        rh.pack_end(self.bk_cancel, False, False, 0)
        self.bk_run = Gtk.Button()
        inner = hbox(6)
        inner.pack_start(icon("document-save-symbolic"), False, False, 0)
        inner.pack_start(Gtk.Label(label=_("Back up now")), False, False, 0)
        self.bk_run.add(inner)
        describe(self.bk_run, _("Back up now"))
        css(self.bk_run, "primary")
        self.bk_run.connect("clicked", lambda *_x: self.run_backup())
        rh.pack_end(self.bk_run, False, False, 0)
        dc.pack_start(rh, False, False, 4)
        page.pack_start(dc, False, False, 0)
        self.bk_snap_head = label(_("SNAPSHOTS"), ("kicker",))
        page.pack_start(self.bk_snap_head, False, False, 0)
        self.bk_snap_card, self.bk_snap_list = list_card()
        page.pack_start(self.bk_snap_card, False, False, 0)

        ec = card()
        eh = hbox(8)
        eh.pack_start(icon("document-send-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        et = vbox(2)
        et.pack_start(label(_("Move MintCare to another PC"), ("h2",)), False, False, 0)
        et.pack_start(label(_("Save your MintCare settings, the optimisations it applied and your startup-app "
                              "choices to a file, then import them on another computer."), ("muted",), wrap=True),
                      False, False, 0)
        eh.pack_start(et, True, True, 0)
        eh.pack_end(self._btn(_("Import…"), self._import_settings), False, False, 0)
        eh.pack_end(self._btn(_("Export…"), self._export_settings), False, False, 0)
        ec.pack_start(eh, False, False, 0)
        page.pack_start(ec, False, False, 0)
        self._bk_cancel_ev = threading.Event()
        self._bk_loading = False
        return scrolled(page)

    def load_backup(self):
        self._bk_loading = True
        b = backup.config()
        self.bk_cfg = b
        self.bk_dest.set_text(b["dest"] or _("(not chosen)"))
        self.bk_freq.set_active_id(b["frequency"])
        self.bk_keep.set_value(b["keep"])
        self.bk_switch.set_active(backup.timer_next() is not None)
        self._bk_loading = False
        self._clear(self.bk_dest_issues)
        for lvl, text in backup.check_destination(b["dest"]):
            r = hbox(6)
            r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
            r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
            self.bk_dest_issues.pack_start(r, False, False, 0)
        self._clear(self.bk_folders, "bk_row")
        for f in b["folders"]:
            r = hbox(8)
            r.pack_start(icon("folder-symbolic", Gtk.IconSize.MENU, "ok"), False, False, 0)
            r.pack_start(label(f.replace(str(Path.home()), "~"), ("mono", "muted"), ellipsize=True), True, True, 0)
            r.pack_end(self._btn(_("Remove"), lambda f=f: self._bk_remove_folder(f), tag="bk_row"), False, False, 0)
            self.bk_folders.pack_start(r, False, False, 0)
        self.bk_dest_issues.show_all()
        self.bk_folders.show_all()
        self.bk_size.set_text(_("measuring…"))
        self.bg(lambda: backup.estimate(b["folders"]), lambda n: self.bk_size.set_text(
            _('{0} to back up (first time)').format(human(n or 0))))
        last = backup.last_backup()
        nxt = backup.timer_next()
        self.bk_status.set_text(
            (_('Last backup {0}: {1} changed file(s). ').format(time.strftime('%a %d %b %H:%M', time.localtime(last['ts'])), last['files']) if last else _("No backup yet. ")) +
            (_('Next automatic backup: {0}.').format(nxt) if nxt else _("Automatic backup is off.")))
        self._clear(self.bk_snap_list, "bk_snap")
        snaps = backup.snapshots(b["dest"]) if b["dest"] else []
        self.bk_snap_head.set_text(_('SNAPSHOTS ({0})').format(len(snaps)))
        for sp in snaps:
            self.bk_snap_list.add(self._row(sp.name.replace("_", " "), "", str(sp), "folder-symbolic", (),
                                            [self._btn(_("Open"), lambda sp=sp: self._open_uri(sp.as_uri()),
                                                       tag="bk_snap")]))
        if not snaps:
            self.bk_snap_list.add(self._row(_("No snapshots yet"), _("Press Back up now to make the first one.")))
        self.bk_snap_list.show_all()

    def _bk_save(self):
        if self._bk_loading:
            return
        b = backup.config()
        b["frequency"] = self.bk_freq.get_active_id() or "weekly"
        b["keep"] = int(self.bk_keep.get_value())
        backup.save_config(b)
        if self.bk_switch.get_active():
            try:
                backup.install_timer(b["frequency"])
            except Exception as e:
                self.log(_('Backup timer: {0}').format(e))

    def _bk_toggle(self, sw, _p):
        if self._bk_loading:
            return
        b = backup.config()
        if sw.get_active():
            if not b["dest"]:
                self.set_status(_("Choose a destination first"), bad=True)
                self._bk_loading = True
                sw.set_active(False)
                self._bk_loading = False
                return
            backup.install_timer(b["frequency"])
            maintenance.record("backup", _("Automatic backup turned on"))
        else:
            backup.remove_timer()
            maintenance.record("backup", _("Automatic backup turned off"))
        self.load_backup()

    def _pick_folder(self, title, start=None):
        d = Gtk.FileChooserDialog(title=title, transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Choose"), Gtk.ResponseType.OK)
        d.set_current_folder(start or "/mnt" if os.path.isdir("/mnt") else str(Path.home()))
        resp, path = d.run(), d.get_filename()
        d.destroy()
        return path if resp == Gtk.ResponseType.OK else None

    def _bk_choose_dest(self):
        path = self._pick_folder(_("Choose where to keep backups"), "/mnt")
        if path:
            b = backup.config()
            b["dest"] = path
            backup.save_config(b)
            self.load_backup()

    def _bk_add_folder(self):
        path = self._pick_folder(_("Add a folder to back up"), str(Path.home()))
        if path:
            b = backup.config()
            if path not in b["folders"]:
                b["folders"].append(path)
            backup.save_config(b)
            self.load_backup()

    def _bk_remove_folder(self, f):
        b = backup.config()
        b["folders"] = [x for x in b["folders"] if x != f]
        backup.save_config(b)
        self.load_backup()

    def run_backup(self):
        if getattr(self, "_bk_running", False):
            return
        self._bk_running = True
        self._bk_cancel_ev.clear()
        self.bk_run.set_sensitive(False)
        self.bk_progress.show()
        self.bk_cancel.show()

        def prog(pct, text):
            GLib.idle_add(lambda: (self.bk_progress.set_fraction(pct / 100), self.bk_progress.set_text(text),
                                   self.bk_progress.set_show_text(True), False)[-1])

        def done(r):
            self._bk_running = False
            self.bk_run.set_sensitive(True)
            self.bk_progress.hide()
            self.bk_cancel.hide()
            r = r or {"ok": False, "error": "failed"}
            self.set_status(_('Backup done: {0} changed file(s)').format(r['files']) if r["ok"] else _('Backup: {0}').format(r['error']),
                            bad=not r["ok"])
            self.load_backup()
        self.bg(lambda: backup.run_backup(None, prog, self._bk_cancel_ev.is_set), done)

    def _export_settings(self):
        d = Gtk.FileChooserDialog(title=_("Export MintCare settings"), transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Export"), Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        d.set_current_name("mintcare-settings.json")
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path:
            data = backup.export_settings(path)
            self.set_status(_('Exported settings and {0} optimisation(s)').format(len(data['tweaks'])))

    def _import_settings(self):
        d = Gtk.FileChooserDialog(title=_("Import MintCare settings"), transient_for=self,
                                  action=Gtk.FileChooserAction.OPEN)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Import"), Gtk.ResponseType.OK)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        try:
            ids = backup.import_settings(path)
        except (OSError, ValueError) as e:
            self.set_status(_('Import failed: {0}').format(e), bad=True)
            return
        self.prefs = maintenance.load_settings()
        self.apply_theme(self.prefs.get("theme", "dark"))
        self.apply_level(self.prefs.get("level", "intermediate"))
        for i in ids:
            sw = self.switches.get(i)
            if sw and sw.get_sensitive() and not sw.get_active():
                sw.set_active(True)
        self.goto("optimise")
        self.set_status(_('Imported settings; {0} optimisation(s) selected — press Apply changes').format(len(ids)))

    # ------------------------------------------------------------ assistant

    def _build_assistant(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "user-available-symbolic", _("Assistant"),
            _("Ask about your computer in plain English. Uses Claude by Anthropic — optional, off until you set "
              "it up, and nothing is sent unless you ask.")), False, False, 0)
        self.ai_setup = card("ecard")
        page.pack_start(self.ai_setup, False, False, 0)
        chat = card()
        ch = hbox(8)
        ch.pack_start(label(_('Model: {0}').format(assistant.MODEL), ("faint", "mono")), True, True, 0)
        self.ai_include = Gtk.CheckButton(label=_("Include my system summary (no names or serial numbers)"))
        ch.pack_end(self.ai_include, False, False, 0)
        chat.pack_start(ch, False, False, 0)
        self.ai_view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False,
                                    left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.ai_view, "log")
        sw = Gtk.ScrolledWindow(min_content_height=360)
        sw.add(self.ai_view)
        chat.pack_start(sw, True, True, 8)
        ih = hbox(8)
        self.ai_entry = Gtk.Entry(placeholder_text=_("Ask anything about your PC…"))
        self.ai_entry.connect("activate", lambda *_x: self.ai_send())
        ih.pack_start(self.ai_entry, True, True, 0)
        self.ai_send_btn = Gtk.Button(label=_("Send"))
        css(self.ai_send_btn, "primary")
        self.ai_send_btn.connect("clicked", lambda *_x: self.ai_send())
        ih.pack_end(self.ai_send_btn, False, False, 0)
        b = Gtk.Button(label=_("Clear"))
        css(b, "ghost")
        b.connect("clicked", lambda *_x: self._ai_clear())
        ih.pack_end(b, False, False, 0)
        chat.pack_start(ih, False, False, 0)
        self.ai_chat = chat
        page.pack_start(chat, True, True, 0)
        self.ai_history = []
        self._ai_stop = threading.Event()
        return scrolled(page)

    def load_assistant(self):
        c = self.ai_setup
        self._clear(c, "ai_row")
        ready_sdk = assistant.sdk_available()
        has_key = assistant.has_credentials()
        h = hbox(10)
        h.pack_start(icon("emblem-ok-symbolic" if ready_sdk and has_key else "dialog-information-symbolic",
                          Gtk.IconSize.LARGE_TOOLBAR, "ok" if ready_sdk and has_key else "info"), False, False, 0)
        t = vbox(2)
        if not ready_sdk:
            t.pack_start(label(_("AI support isn't installed"), ("h2",)), False, False, 0)
            t.pack_start(label(_("Installs Python's venv module (admin) and the official Anthropic SDK into "
                                 "MintCare's own folder (~/.local/share/mintcare/ai-venv). About 30 MB."), ("muted",),
                               wrap=True), False, False, 0)
            h.pack_end(self._btn(_("Install AI support"), self._ai_install, primary=True, tag="ai_row"), False, False, 0)
        elif not has_key:
            t.pack_start(label(_("Add your Anthropic API key"), ("h2",)), False, False, 0)
            t.pack_start(label(_("Create one at console.anthropic.com (usage is billed to your account). It's "
                                 "stored in ~/.config/mintcare/anthropic-key, readable only by you."), ("muted",),
                               wrap=True), False, False, 0)
        else:
            t.pack_start(label(_("Assistant ready"), ("h2",)), False, False, 0)
            t.pack_start(label(_("Requests go to Anthropic's API using your key. If Claude declines a request, "
                                 "it's retried automatically on Anthropic's recommended fallback model."), ("muted",),
                               wrap=True), False, False, 0)
            h.pack_end(self._btn(_("Remove key"), lambda: (assistant.clear_key(), self.load_assistant()), tag="ai_row"),
                       False, False, 0)
        h.pack_start(t, True, True, 0)
        c.pack_start(h, False, False, 0)
        if ready_sdk and not has_key:
            kh = hbox(8)
            self.ai_key = Gtk.Entry(visibility=False, placeholder_text="sk-ant-…")
            self.ai_key.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            kh.pack_start(self.ai_key, True, True, 0)
            kh.pack_end(self._btn(_("Save key"), self._ai_save_key, primary=True, tag="ai_row"), False, False, 0)
            c.pack_start(kh, False, False, 8)
        c.show_all()
        ok = ready_sdk and has_key
        for w in (self.ai_entry, self.ai_send_btn, self.ai_include):
            w.set_sensitive(ok)

    def _ai_install(self):
        root, user = assistant.install_steps()
        if Path(f"/usr/lib/python3.{sys.version_info.minor}/ensurepip").exists():
            root = []                           # venv support already present: no admin step needed
        self._root_job(_("Installing AI support…"), root, "Assistant", lambda r: self.load_assistant(), user_steps=user)

    def _ai_save_key(self):
        k = self.ai_key.get_text().strip()
        if not k.startswith("sk-ant-"):
            self.set_status(_("That doesn't look like an Anthropic API key (sk-ant-…)"), bad=True)
            return
        assistant.set_key(k)
        maintenance.record("other", _("Assistant: API key saved"))
        self.load_assistant()

    def _ai_clear(self):
        self.ai_history = []
        self.ai_view.get_buffer().set_text("")

    def _ai_append(self, text, bold=False):
        buf = self.ai_view.get_buffer()
        it = buf.get_end_iter()
        if bold:
            tag = buf.get_tag_table().lookup("b") or buf.create_tag("b", weight=Pango.Weight.BOLD)
            buf.insert_with_tags(it, text, tag)
        else:
            buf.insert(it, text)
        self.ai_view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 1)
        return False

    def ask_assistant(self, question):
        """Prefill a question (e.g. from a check's details); the user still presses Send."""
        self.goto("assistant")
        self.ai_entry.set_text(question)
        self.ai_entry.grab_focus()

    def ai_send(self):
        q_ = self.ai_entry.get_text().strip()
        if not q_ or getattr(self, "_ai_busy", False):
            return
        self._ai_busy = True
        self.ai_entry.set_text("")
        self.ai_send_btn.set_sensitive(False)
        self._ai_append(f"\nYou: {q_}\n", bold=True)
        include = self.ai_include.get_active()

        def work():
            content = q_
            if include and not self.ai_history:
                content = f"My system summary:\n{assistant.system_summary()}\n\nQuestion: {q_}"
            history = self.ai_history + [{"role": "user", "content": content}]
            GLib.idle_add(self._ai_append, "\n" + _("MintCare assistant:") + " ", True)
            res = assistant.stream_reply(history, lambda t: GLib.idle_add(self._ai_append, t))
            return content, res

        def done(out):
            self._ai_busy = False
            self.ai_send_btn.set_sensitive(True)
            if not out:
                return
            content, res = out
            if res.get("error"):
                self._ai_append(f"\n[{res['error']}]\n")
            if res.get("text") and not res.get("error"):
                self.ai_history += [{"role": "user", "content": content},
                                    {"role": "assistant", "content": res["text"]}]
            self._ai_append("\n")
        self.bg(work, done)

    # ------------------------------------------------------------ flatpak

    def _build_flatpak(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "system-software-install-symbolic", _("Flatpak apps"),
            _("Installed apps, how tightly each one is sandboxed, and switches to tighten or loosen "
            "permissions (your account only — like Flatseal)."),
            [self.action_button(_("Refresh"), lambda: self.load_flatpaks(check_updates=True),
                                icon_name="view-refresh-symbolic"),
             self.action_button(_("Repair"), self.flatpak_repair, icon_name="emblem-synchronizing-symbolic"),
             self.action_button(_("Update all"), self.flatpak_update, primary=True,
                                icon_name="software-update-available-symbolic")]), False, False, 0)
        sc = card("ecard")
        tiles = hbox(10)
        tiles.set_homogeneous(True)
        self.fp_stats = {}
        for key in (N_("APPS"), N_("INSTALLED SIZE"), N_("UPDATES"), N_("LOOSE SANDBOXES")):
            t = vbox(2, "stat-tile")
            t.pack_start(label(_(key), ("kicker",), xalign=0.5), False, False, 0)
            v = label("…", ("tval",), xalign=0.5)
            t.pack_start(v, False, False, 0)
            tiles.pack_start(t, True, True, 0)
            self.fp_stats[key] = v
        sc.pack_start(tiles, False, False, 0)
        page.pack_start(sc, False, False, 0)
        page.pack_start(label(_("Sandbox ratings: STRONG = no broad file or device access; MODERATE = home "
                              "folder, all devices or X11; LOOSE = several of those or the whole filesystem. "
                              "Changes apply the next time the app starts."), ("faint",), wrap=True),
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
            row.add(label(_("No Flatpak apps installed.") if flatpaks.available() else _("Flatpak is not installed."),
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
        th.pack_start(pill({"weak": "loose"}.get(rating, rating) + _(" sandbox"), kind), False, False, 0)
        th.pack_start(pill(a["install"], "dim"), False, False, 0)
        if a["overridden"]:
            th.pack_start(pill(_("customised"), "info"), False, False, 0)
        text.pack_start(th, False, False, 0)
        text.pack_start(label(f"{a['id']}  •  {a['version'] or '—'}  •  {a['origin']}  •  {a['size']}",
                              ("faint", "mono"), ellipsize=True), False, False, 0)
        if why:
            text.pack_start(label(_("Has access to: ") + ", ".join(why), ("muted",), wrap=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        btn = self.action_button(_("Permissions"), lambda: self._toggle_fp(a["id"], rev))
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
                tt.pack_start(pill(_("broad"), "warn"), False, False, 0)
            t.pack_start(tt, False, False, 0)
            t.pack_start(label(desc, ("faint",)), False, False, 0)
            pr.pack_start(t, True, True, 0)
            sw = describe(Gtk.Switch(active=flatpaks.has(a["perms"], pid), valign=Gtk.Align.CENTER),
                          f"{a['name']}: {title}", desc)
            sw.connect("notify::active", self._on_fp_perm, a, pid)
            pr.pack_end(sw, False, False, 0)
            perms.pack_start(pr, False, False, 0)
        acts = hbox(8)
        b1 = self.action_button(_("Reset to app defaults"), lambda: self._fp_reset(a))
        b2 = self.action_button(_("Uninstall…"), lambda: self._fp_uninstall(a), icon_name="user-trash-symbolic")
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
            self.log(_('Flatpak override failed for {0}: {1}').format(a['id'], e))
            self.set_status(_("Could not change permission"), bad=True)
            sw.handler_block_by_func(self._on_fp_perm)
            sw.set_active(not grant)
            sw.handler_unblock_by_func(self._on_fp_perm)
            return
        title = flatpaks.PERM_BY_ID[pid][1]
        maintenance.note_flatpak(a["id"])
        maintenance.record("flatpak", f"{a['name']}: {title} {'allowed' if grant else 'blocked'}")
        self.log(_('Flatpak: {0} — {1} {2} (flatpak override --user {3} {4})').format(a['name'], title, 'allowed' if grant else 'blocked', flatpaks.PERM_BY_ID[pid][4 if grant else 5], a['id']))
        self.set_status(_('{0}: {1} {2} — restart the app to apply').format(a['name'], title, 'allowed' if grant else 'blocked'))
        self.load_flatpaks()

    def _fp_reset(self, a):
        try:
            flatpaks.reset(a["id"])
        except Exception as e:
            self.log(_('Reset failed for {0}: {1}').format(a['id'], e))
            self.set_status(_("Reset failed"), bad=True)
            return
        maintenance.note_flatpak(a["id"], reset=True)
        maintenance.record("flatpak", _('{0}: permissions reset to defaults').format(a['name']))
        self.log(_("Flatpak: {0} permissions reset to the app's defaults").format(a['name']))
        self.set_status(_('{0} permissions reset').format(a['name']))
        self.load_flatpaks()

    def _fp_uninstall(self, a):
        if not self.confirm(_('Uninstall {0}?').format(a['name']),
                            _('<tt>{0}</tt> will be removed. Its data in ~/.var/app is kept.').format(GLib.markup_escape_text(a['id']))):
            return
        root, user = flatpaks.uninstall_steps(a)

        def done(results):
            self.notify_done(results or {}, _('Uninstall {0}').format(a['name']))
            self.load_flatpaks(check_updates=True)
            self.run_clean_scan()
        self.in_thread(_('Uninstalling {0}…').format(a['name']), lambda: self.execute(root, user, "Flatpak"), done)

    def flatpak_update(self):
        root, user = flatpaks.update_steps()

        def done(results):
            self.notify_done(results or {}, "Flatpak update")
            self.load_flatpaks(check_updates=True)
        self.in_thread(_("Updating Flatpaks…"), lambda: self.execute(root, user, "Flatpak update"), done)

    def flatpak_repair(self):
        root, user = flatpaks.repair_steps()

        def done(results):
            self.notify_done(results or {}, "Flatpak repair")
            self.load_flatpaks()
            self.run_clean_scan()
        self.in_thread(_("Repairing Flatpak…"), lambda: self.execute(root, user, "Flatpak repair"), done)

    # ------------------------------------------------------------ hardware

    def _build_hardware(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "drive-harddisk-symbolic", _("Hardware Health"),
            _("SMART disk health and live temperatures, read through UDisks2 and kernel sensors — "
            "no password needed."),
            [self.action_button(_("Refresh"), lambda: (self.load_drives(), self._poll_sensors(force=True)),
                                icon_name="view-refresh-symbolic"),
             self.action_button(_("Full SMART report"), self.smart_report, icon_name="utilities-terminal-symbolic")]),
            False, False, 0)

        tc = card()
        th = hbox(8)
        th.pack_start(icon("weather-clear-symbolic", Gtk.IconSize.BUTTON, "warn"), False, False, 0)
        th.pack_start(label(_("Temperatures & Throttling"), ("h2",)), False, False, 0)
        th.pack_end(label(_("updates every 2 s"), ("faint", "mono")), False, False, 0)
        tc.pack_start(th, False, False, 0)
        self.throttle_box = vbox(4)
        tc.pack_start(self.throttle_box, False, False, 10)
        self.sensor_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                       min_children_per_line=2, max_children_per_line=5,
                                       column_spacing=10, row_spacing=10)
        tc.pack_start(self.sensor_flow, False, False, 0)
        self.sensor_tiles = {}
        page.pack_start(tc, False, False, 0)

        fwc = card()
        fh = hbox(8)
        fh.pack_start(icon("drive-harddisk-solidstate-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        fh.pack_start(label(_("Firmware"), ("h2",)), True, True, 0)
        self.fw_install_btn = self.action_button(_("Install updates"), self.install_firmware, primary=True)
        self.fw_install_btn.set_no_show_all(True)
        fh.pack_end(self.fw_install_btn, False, False, 0)
        fh.pack_end(self.action_button(_("Check online"), self.refresh_firmware, icon_name="view-refresh-symbolic"),
                    False, False, 0)
        fwc.pack_start(fh, False, False, 0)
        self.fwup_lbl = label(_("Reading firmware information…"), ("muted",), wrap=True)
        fwc.pack_start(self.fwup_lbl, False, False, 8)
        self.fwup_box = vbox(4)
        fwc.pack_start(self.fwup_box, False, False, 0)
        self.fwdev_exp = Gtk.Expander(label=_("Devices"))
        self.fwdev_box = vbox(2)
        self.fwdev_exp.add(self.fwdev_box)
        fwc.pack_start(self.fwdev_exp, False, False, 4)
        page.pack_start(fwc, False, False, 0)

        self.bat_box = vbox(8)
        page.pack_start(self.bat_box, False, False, 0)

        bc = card()
        bh = hbox(8)
        bh.pack_start(icon("power-profile-performance-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        bh.pack_start(label(_("Benchmarks"), ("h2",)), True, True, 0)
        self.bench_target = Gtk.ComboBoxText()
        for mp, dev, fs, free in bench.targets():
            self.bench_target.append(mp, f"{mp.replace(str(Path.home()), '~')} ({fs}, {human(free)} free)")
        self.bench_target.set_active(0)
        bh.pack_end(self._btn(_("Test CPU"), self.run_cpu_bench), False, False, 0)
        bh.pack_end(self._btn(_("Test disk"), self.run_disk_bench, primary=True), False, False, 0)
        bh.pack_end(self.bench_target, False, False, 0)
        bc.pack_start(bh, False, False, 0)
        bc.pack_start(label(_("Disk: writes and reads a 256 MB temporary file (deleted afterwards). CPU: hashes data "
                              "on one core and on all cores. Results are kept so you can compare before and after "
                              "a tweak."), ("faint",), wrap=True), False, False, 6)
        self.bench_box = vbox(4)
        bc.pack_start(self.bench_box, False, False, 0)
        page.pack_start(bc, False, False, 0)

        self.drives_head = label(_("DRIVES"), ("kicker",))
        page.pack_start(self.drives_head, False, False, 0)
        self.drives_box = vbox(12)
        self.drives_box.pack_start(label(_("Reading SMART data…"), ("faint",)), False, False, 0)
        page.pack_start(self.drives_box, False, False, 0)
        return scrolled(page)

    def load_drives(self):
        self.bg(hardware.drives, self._render_drives)
        self.load_firmware()
        self._render_battery()
        self._render_bench()

    def _render_battery(self):
        self._clear(self.bat_box)
        for b in hardware.batteries():
            c = card()
            h = hbox(10)
            h.pack_start(icon("battery-good-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
            t = vbox(2)
            t.pack_start(label(f"{b['maker']} {b['model']}".strip() or b["name"], ("h2",)), False, False, 0)
            hl = b["health"]
            # xgettext:no-python-format
            t.pack_start(label((_('Holds {0}% of its original charge').format(hl) if hl is not None else _("Health unknown")) +
                               (_(' · {0} charge cycles').format(b['cycles']) if b["cycles"] else "") +
                               (_(' · {0:.1f} of {1:.1f} Wh').format(b['full_wh'], b['design_wh']) if b["full_wh"] and b["design_wh"]
                                else "") + f" · {b['status']}, {b['capacity']}%", ("muted",), wrap=True),
                         False, False, 0)
            h.pack_start(t, True, True, 0)
            if hl is not None:
                h.pack_end(pill(_("good") if hl >= 80 else _("worn") if hl >= 60 else _("replace soon"),
                                "ok" if hl >= 80 else "warn" if hl >= 60 else "fail"), False, False, 0)
            c.pack_start(h, False, False, 0)
            if hl is not None:
                bar = Bar(EMERALD if hl >= 80 else "#f59e0b" if hl >= 60 else "#ef4444", 8)
                bar.set_value(hl)
                c.pack_start(bar, False, False, 8)
            self.bat_box.pack_start(c, False, False, 0)
        self.bat_box.show_all()

    def _render_bench(self):
        self._clear(self.bench_box)
        target = self.bench_target.get_active_id()
        for kind, rows in (("disk", bench.history("disk", target)), ("cpu", bench.history("cpu"))):
            if not rows:
                continue
            last = rows[-1]
            prev = rows[-2] if len(rows) > 1 else None
            if kind == "disk":
                text = _('Disk {0}: write {1:,.0f} MB/s · read {2:,.0f} MB/s').format(
                    target.replace(str(Path.home()), '~'), last['write'] / 2**20, last['read'] / 2**20)
                if prev:
                    text += _('  (before: {0:,.0f} / {1:,.0f} MB/s)').format(prev['write'] / 2**20, prev['read'] / 2**20)
            else:
                text = _('CPU: {0:,.0f} MB/s on one core · {1:,.0f} MB/s on {2} threads').format(
                    last['single'], last['multi'], last['cores'])
                if prev:
                    text += _('  (before: {0:,.0f} / {1:,.0f})').format(prev['single'], prev['multi'])
            when = time.strftime("%d %b %H:%M", time.localtime(last["ts"]))
            self.bench_box.pack_start(self._row(text, _('Measured {0}').format(when), "", None), False, False, 0)
        self.bench_box.show_all()

    def run_disk_bench(self):
        target = self.bench_target.get_active_id()
        if not target:
            return

        def work():
            r = bench.disk(target, 256, lambda t: GLib.idle_add(self.set_status, _('Disk test {0}').format(t)))
            bench.save("disk", target, r)
            return r
        self.in_thread(_("Testing disk speed…"), work, lambda r: (self._render_bench(),
                                                                  self.set_status(_("Disk test finished"))))

    def run_cpu_bench(self):
        def work():
            r = bench.cpu()
            bench.save("cpu", "cpu", r)
            return r
        self.in_thread(_("Testing CPU…"), work, lambda r: (self._render_bench(), self.set_status(_("CPU test finished"))))

    def load_firmware(self):
        if not firmware.available():
            self.fwup_lbl.set_text(_("fwupd isn't installed, so firmware can't be checked here."))
            return
        self.bg(lambda: (firmware.updates(), firmware.devices(), firmware.metadata_age()), self._render_firmware)

    def _render_firmware(self, res):
        if not res:
            return
        ups, devs, age = res
        self.fw_updates = ups
        for box in (self.fwup_box, self.fwdev_box):
            for ch in box.get_children():
                ch.destroy()
        updatable = [d for d in devs if d["updatable"]]
        if ups:
            self.fwup_lbl.set_markup(_("<b>{0} firmware update(s) available.</b> Keep the computer plugged in and don't switch it off while updating.").format(len(ups)))
            for u in ups:
                r = hbox(8)
                r.pack_start(icon("software-update-available-symbolic", Gtk.IconSize.MENU, "warn"), False, False, 0)
                r.pack_start(label(f"{u['device']}: {u['current']} → {u['new']}", ("h3",), ellipsize=True),
                             False, False, 0)
                if u["reboot"]:
                    r.pack_start(pill(_("needs restart"), "warn"), False, False, 0)
                if u["urgency"] in ("high", "critical"):
                    r.pack_start(pill(u["urgency"], "fail"), False, False, 0)
                self.fwup_box.pack_start(r, False, False, 0)
                if u["summary"]:
                    self.fwup_box.pack_start(label(u["summary"], ("faint",), wrap=True), False, False, 0)
        else:
            self.fwup_lbl.set_text(_('Firmware is up to date for {0} updatable device(s).').format(len(updatable))
                                   + (_(' The firmware catalogue was refreshed {0} ago.').format(age) if age else ""))
        self.fw_install_btn.set_visible(bool(ups))
        self.fwdev_exp.set_label(_('Devices ({0}, {1} support updates)').format(len(devs), len(updatable)))
        for d in devs:
            r = hbox(8)
            r.pack_start(label(d["name"], ("muted",), ellipsize=True), True, True, 0)
            r.pack_end(pill(_("updatable") if d["updatable"] else _("fixed"), "info" if d["updatable"] else "dim"),
                       False, False, 0)
            r.pack_end(label(d["version"] or "—", ("faint", "mono")), False, False, 0)
            self.fwdev_box.pack_start(r, False, False, 0)
        self.fwup_box.show_all()
        self.fwdev_box.show_all()

    def refresh_firmware(self):
        def done(results):
            self.notify_done(results or {}, _("Firmware check"))
            self.load_firmware()
        self.in_thread(_("Checking for firmware updates…"),
                       lambda: self.execute([firmware.refresh_step()], [], "Firmware"), done)

    def install_firmware(self):
        ups = getattr(self, "fw_updates", [])
        if not ups:
            return
        listing = "\n".join(f"• {GLib.markup_escape_text(u['device'])}: {GLib.markup_escape_text(u['current'])} → "
                            f"{GLib.markup_escape_text(u['new'])}" for u in ups)
        reboot = any(u["reboot"] for u in ups)
        if not self.confirm(_('Install {0} firmware update(s)?').format(len(ups)),
                            listing + _("\n\nKeep the computer plugged in and don't turn it off.") +
                            (_("\n<b>Some updates finish during the next restart.</b>") if reboot else "")):
            return
        snap = self.ask_snapshot(_("firmware updates"))
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, _("Firmware update"))
            self.load_firmware()
            if reboot and results and all(rc == 0 for rc in results.values()):
                self.set_status(_("Firmware staged — restart the computer to finish installing it"))
        self.in_thread(_("Installing firmware updates…"),
                       lambda: self.execute([firmware.update_step()], [], "Firmware", snap), done)

    def _render_drives(self, ds):
        for ch in self.drives_box.get_children():
            ch.destroy()
        ds = [d for d in (ds or []) if "error" not in d]
        self.drives = ds
        self.drives_head.set_text(_('DRIVES ({0})').format(len(ds)))
        if not ds:
            self.drives_box.pack_start(label(_("No drives found, or UDisks2 is not running."), ("faint",)),
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
                r.pack_start(label(_("No warning signs in SMART data"), ("muted",)), True, True, 0)
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
            tiles.append((s_["label"], f"{s_['temp']:.0f}°C", lvl, " · ".join(lim) or _("no limit reported"), pct))
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
            for key, *_x in tiles:
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
            self.set_status(_("No drives loaded yet"))
            return
        steps = [(f"smart{i}", _('SMART report: {0} ({1})').format(d['model'], d['device']),
                  f"smartctl -x {q(d['device'])} || true") for i, d in enumerate(ds)]

        def done(results):
            self.notify_done(results or {}, _("SMART report"))
            self.goto("log")
        self.in_thread(_("Reading full SMART reports…"), lambda: self.execute(steps, [], "SMART report"), done)

    # ------------------------------------------------------------ boot & startup

    def _build_startup(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "system-run-symbolic", _("Boot & Startup"),
            _("How long boot takes, what slows it down, and which apps start when you log in."),
            [self.action_button(_("Refresh"), self.load_boot, icon_name="view-refresh-symbolic")]), False, False, 0)

        bc = card("ecard")
        row = hbox(20)
        left = vbox(2)
        left.pack_start(label(_("LAST BOOT"), ("kicker",)), False, False, 0)
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

        hc = card()
        hh = hbox(8)
        hh.pack_start(icon("org.gnome.SystemMonitor-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        hh.pack_start(label(_("Boot time history"), ("h2",)), False, False, 0)
        for name, col in ((_("Services"), EMERALD), (_("Total"), VIOLET)):
            hh.pack_end(self._legend(name, col), False, False, 4)
        hc.pack_start(hh, False, False, 0)
        self.boot_hist = LineChart([(_("Total"), VIOLET), (_("Services"), EMERALD)], points=20, height=140,
                                   fmt=lambda v: f"{v:.0f}s")
        hc.pack_start(self.boot_hist, False, False, 8)
        self.boot_hist_lbl = label("", ("faint",), wrap=True)
        hc.pack_start(self.boot_hist_lbl, False, False, 0)
        page.pack_start(hc, False, False, 0)

        page.pack_start(label(_("SLOWEST TO START"), ("kicker",)), False, False, 0)
        page.pack_start(label(_("Services start in parallel, so times overlap. Only those marked "
                              "BLOCKS BOOT actually delay reaching the desktop."), ("faint",), wrap=True),
                        False, False, 0)
        self.blame_card, self.blame_list = list_card()
        page.pack_start(self.blame_card, False, False, 0)

        self.auto_head = label(_("STARTUP APPLICATIONS"), ("kicker",))
        page.pack_start(self.auto_head, False, False, 0)
        page.pack_start(label(_("Switching an app off only affects your account and takes effect at next login. "
                              "This is the same setting as Cinnamon's Startup Applications."), ("faint",), wrap=True),
                        False, False, 0)
        self.auto_card, self.auto_list = list_card()
        page.pack_start(self.auto_card, False, False, 0)
        self.ess_exp = Gtk.Expander(label=_("Desktop components (locked)"))
        self.ess_card, self.ess_list = list_card()
        self.ess_exp.add(self.ess_card)
        page.pack_start(self.ess_exp, False, False, 0)
        return scrolled(page)

    def load_boot(self):
        self.bg(lambda: (boot.times(), boot.blame(), autostart.entries()), self._render_boot)
        self.bg(lambda: boot.history(20), self._render_boot_history)

    def _render_boot_history(self, h):
        h = h or []
        if len(h) < 2:
            self.boot_hist_lbl.set_text(_("The history fills in as you restart the computer."))
            return
        self.boot_hist.set_values([b["total"] for b in h], [b.get("userspace", 0) for b in h])
        avg = sum(b["total"] for b in h[:-1]) / (len(h) - 1)
        last = h[-1]["total"]
        worst = max(h, key=lambda b: b["total"])
        trend = _("slower") if last > avg * 1.15 else _("faster") if last < avg * 0.85 else _("about the same")
        self.boot_hist_lbl.set_text(
            _('Last {0} boots: this one took {1:.1f} s, {2} than the average of {3:.1f} s. Slowest was {4:.1f} s on {5}.').format(len(h), last, trend, avg, worst['total'], time.strftime('%d %b', time.localtime(worst['ts']))))

    def _render_boot(self, res):
        if not res:
            return
        times, rows, apps = res
        segs = [("firmware", _("Firmware"), VIOLET), ("loader", _("Boot loader"), BLUE),
                ("kernel", _("Kernel"), ORANGE), ("initrd", "initrd", "#f472b6"), ("userspace", _("Services"), EMERALD)]
        self.boot_total.set_text(f"{times['total']:.1f} s" if times.get("total") else "n/a")
        self.boot_bar.set_segments([(times.get(k, 0), col) for k, _x, col in segs])
        describe(self.boot_bar, _("Last boot"), ", ".join(f"{name} {times[k]:.1f} s" for k, name, _c in segs if times.get(k)))
        for ch in self.boot_legend.get_children():
            ch.destroy()
        for k, name, col in segs:
            if times.get(k):
                b = self._legend(f"{name} {times[k]:.1f} s", col)
                self.boot_legend.pack_start(b, False, False, 0)
        self.boot_legend.show_all()
        tips = []
        if times.get("firmware", 0) >= 10:
            tips.append(_('{0:.1f} s is spent in your BIOS/UEFI before Linux starts. Enabling “Fast Boot”, disabling '
                          'unused boot devices (network/PXE boot) and unused controllers in the UEFI setup usually '
                          'shortens this — MintCare can\'t change it from Linux.').format(times['firmware']))
        crit = [r for r in rows if r["critical"] and r["can_toggle"] and r["enabled"]]
        if crit:
            tips.append(_('{0} is on the critical path and adds {1:.1f} s.').format(crit[0]['unit'], crit[0]['secs']))
        self.boot_tip.set_text(" ".join(tips) or _("Boot looks lean — nothing obvious to trim."))

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
                th.pack_start(pill(_("blocks boot"), "warn"), False, False, 0)
            if r["enabled"] is False:
                th.pack_start(pill(_("disabled"), "dim"), False, False, 0)
            text.pack_start(th, False, False, 0)
            if r["desc"] and r["desc"] != r["unit"]:
                text.pack_start(label(r["desc"], ("muted",), ellipsize=True), False, False, 0)
            if r["advice"]:
                text.pack_start(label(r["advice"], ("faint",), wrap=True), False, False, 0)
            box.pack_start(text, True, True, 0)
            if r["can_toggle"] and r["enabled"] is not None:
                b = self.action_button(_("Enable") if not r["enabled"] else _("Disable"),
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
        self.auto_head.set_text(_('STARTUP APPLICATIONS ({0} of {1} on)').format(on, len(normal)))
        self.ess_exp.set_label(_('Desktop components ({0}, locked)').format(len(ess)))
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
            th.pack_start(pill(_("added by you"), "info"), False, False, 0)
        elif a["overridden"]:
            th.pack_start(pill(_("customised"), "dim"), False, False, 0)
        if a["delay"]:
            th.pack_start(pill(_('delay {0}s').format(a['delay']), "dim"), False, False, 0)
        text.pack_start(th, False, False, 0)
        if a["comment"]:
            text.pack_start(label(a["comment"], ("muted",), ellipsize=True), False, False, 0)
        text.pack_start(label(a["exec"], ("faint", "mono"), ellipsize=True), False, False, 0)
        box.pack_start(text, True, True, 0)
        sw = describe(Gtk.Switch(active=a["enabled"], valign=Gtk.Align.CENTER, sensitive=not locked),
                      _('Start {0} at login').format(a["name"]))
        if locked:
            sw.set_tooltip_text(_("Part of the desktop; switching it off can break shortcuts, keyrings or logins"))
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
            self.log(_('Could not change startup entry {0}: {1}').format(a['file'], e))
            self.set_status(_('Could not change {0}').format(a['name']), bad=True)
            sw.handler_block_by_func(self._on_app_toggle)
            sw.set_active(not on)
            sw.handler_unblock_by_func(self._on_app_toggle)
            return
        a["enabled"] = on
        maintenance.note_autostart(a["file"], on, had_user_file)
        maintenance.record("startup", (_('Startup app {0} enabled') if on else _('Startup app {0} disabled')).format(a['name']))
        self.log(_('Startup: {0} {1} (~/.config/autostart/{2})').format(a['name'], 'enabled' if on else 'disabled', a['file']))
        self.set_status((_('{0} will start at login') if on else _('{0} will no longer start at login')).format(a['name']))

    def toggle_unit(self, r):
        disabling = bool(r["enabled"])
        verb = "Disable" if disabling else "Enable"
        body = GLib.markup_escape_text(r["advice"] or r["desc"] or "")
        q_ = _('Disable {0}?') if disabling else _('Enable {0}?')
        if not self.confirm(q_.format(r['unit']), body):
            return
        script = boot.disable_script(r["unit"]) if disabling else boot.enable_script(r["unit"])
        steps = [("unit", f"{verb} {r['unit']}", script)]
        snap = self.ask_snapshot(_('a change to {0}').format(r['unit']))
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, f"{verb} {r['unit']}")
            self.load_boot()
            self.run_diagnose()
        self.in_thread((_('Disabling {0}…') if disabling else _('Enabling {0}…')).format(r['unit']), lambda: self.execute(steps, [], "Boot services", snap), done)

    # ------------------------------------------------------------ maintenance

    def _build_maintenance(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "alarm-symbolic", _("Maintenance"),
            _("Automatic check-ups, safety snapshots, one-click undo and a record of every change "
            "MintCare makes.")), False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        page.pack_start(grid, False, False, 0)
        self._maint_loading = True

        # scheduled check-up
        sc = card("ecard")
        h = hbox(8)
        h.pack_start(icon("alarm-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        h.pack_start(label(_("Scheduled check-up"), ("h2",)), True, True, 0)
        self.sched_switch = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Scheduled check-up"))
        self.sched_switch.connect("notify::active", self._on_sched_toggle)
        h.pack_end(self.sched_switch, False, False, 0)
        sc.pack_start(h, False, False, 0)
        sc.pack_start(label(_("Runs every check in the background and tells you only when something needs "
                            "attention. Uses a systemd user timer — no password needed."), ("faint",), wrap=True),
                      False, False, 6)
        g = Gtk.Grid(column_spacing=12, row_spacing=8)
        g.attach(label(_("How often"), ("muted",)), 0, 0, 1, 1)
        self.sched_freq = Gtk.ComboBoxText()
        for k, t in (("daily", _("Every day")), ("weekly", _("Every Monday")), ("monthly", _("1st of each month"))):
            self.sched_freq.append(k, t)
        self.sched_freq.connect("changed", lambda *_x: self._save_maint_settings())
        g.attach(self.sched_freq, 1, 0, 1, 1)
        g.attach(label(_("Notify me"), ("muted",)), 0, 1, 1, 1)
        self.sched_notify = Gtk.ComboBoxText()
        self.sched_notify.append("issues", _("Only when something needs attention"))
        self.sched_notify.append("always", _("After every check-up"))
        self.sched_notify.connect("changed", lambda *_x: self._save_maint_settings())
        g.attach(self.sched_notify, 1, 1, 1, 1)
        g.attach(label(_("Also clean"), ("muted",)), 0, 2, 1, 1)
        cl = vbox(2)
        self.sched_clean = {}
        for k, t in (("thumbs", _("Thumbnail cache")), ("usercache", _("Stale application cache (30+ days)")),
                     ("browser", _("Browser caches (skipped while a browser is open)"))):
            cb = Gtk.CheckButton(label=t)
            cb.connect("toggled", lambda *_x: self._save_maint_settings())
            self.sched_clean[k] = cb
            cl.pack_start(cb, False, False, 0)
        g.attach(cl, 1, 2, 1, 1)
        sc.pack_start(g, False, False, 6)
        rh = hbox(8)
        self.sched_report = Gtk.CheckButton(label=_("Save a health report after each check-up"))
        self.sched_report.connect("toggled", lambda *_x: self._save_maint_settings())
        rh.pack_start(self.sched_report, False, False, 0)
        self.sched_report_fmt = Gtk.ComboBoxText()
        self.sched_report_fmt.append("pdf", _("PDF"))
        self.sched_report_fmt.append("html", _("HTML"))
        self.sched_report_fmt.connect("changed", lambda *_x: self._save_maint_settings())
        rh.pack_start(self.sched_report_fmt, False, False, 0)
        rh.pack_end(self._btn(_("Open reports folder"), lambda: self._open_reports()), False, False, 0)
        sc.pack_start(rh, False, False, 4)
        self.sched_status = label("", ("muted",), wrap=True)
        sc.pack_start(self.sched_status, False, False, 4)
        self.sched_last = label("", ("faint",), wrap=True)
        sc.pack_start(self.sched_last, False, False, 0)
        row = hbox(8)
        row.pack_end(self.action_button(_("Run check-up now"), self.run_checkup_now, primary=True,
                                        icon_name="media-playback-start-symbolic"), False, False, 0)
        sc.pack_end(row, False, False, 0)

        # safety snapshots
        sf = card()
        h = hbox(8)
        h.pack_start(icon("document-open-recent-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        h.pack_start(label(_("Safety snapshots"), ("h2",)), True, True, 0)
        sf.pack_start(h, False, False, 0)
        sf.pack_start(label(_("Before fixes, optimisations, boot-service changes and Undo, MintCare can take a "
                            "Timeshift snapshot so the whole system can be rolled back."), ("faint",), wrap=True),
                      False, False, 6)
        r = hbox(10)
        r.pack_start(label(_("Before big changes"), ("muted",)), False, False, 0)
        self.snap_pref = Gtk.ComboBoxText()
        for k, t in (("ask", _("Ask me")), ("always", _("Always take a snapshot")), ("never", _("Never"))):
            self.snap_pref.append(k, t)
        self.snap_pref.connect("changed", lambda *_x: self._save_maint_settings())
        r.pack_start(self.snap_pref, False, False, 0)
        sf.pack_start(r, False, False, 6)
        self.snap_ready = label("", ("muted",), wrap=True)
        sf.pack_start(self.snap_ready, False, False, 4)
        row = hbox(8)
        row.pack_end(self.action_button(_("Take snapshot now"), self.snapshot_now,
                                        icon_name="document-save-symbolic"), False, False, 0)
        sf.pack_end(row, False, False, 0)

        # undo
        uc = card()
        h = hbox(10)
        h.pack_start(icon("edit-undo-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        t = vbox(2)
        t.pack_start(label(_("Undo MintCare changes"), ("h2",)), False, False, 0)
        self.undo_summary = label("", ("muted",), wrap=True)
        t.pack_start(self.undo_summary, False, False, 0)
        h.pack_start(t, True, True, 0)
        h.pack_end(self.action_button(_("Undo…"), self.undo_all, icon_name="edit-undo-symbolic"), False, False, 0)
        uc.pack_start(h, False, False, 0)
        two = [(sc, 0, 0, 1, 1), (sf, 1, 0, 1, 1), (uc, 0, 1, 2, 1)]
        stacked = [(sc, 0, 0, 1, 1), (sf, 0, 1, 1, 1), (uc, 0, 2, 1, 1)]
        self._responsive.append((grid, {"wide": two, "medium": stacked, "narrow": stacked}))

        # history
        hh = hbox(8)
        self.hist_head = label(_("HISTORY"), ("kicker",))
        hh.pack_start(self.hist_head, True, True, 0)
        hh.pack_end(self.action_button(_("Clear history"), self._clear_history), False, False, 0)
        page.pack_start(hh, False, False, 0)
        self.hist_card, self.hist_list = list_card()
        page.pack_start(self.hist_card, False, False, 0)
        self._maint_loading = False
        return scrolled(page)

    def _open_reports(self):
        from . import report
        folder = Path(maintenance.load_settings()["maintenance"].get("report_folder") or report.default_folder())
        folder.mkdir(parents=True, exist_ok=True)
        self._open_uri(folder.as_uri())

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
        self.sched_report.set_active(m.get("report", False))
        self.sched_report_fmt.set_active_id(m.get("report_format", "pdf"))
        self.snap_pref.set_active_id(st.get("snapshot_before", "ask"))
        self.sched_switch.set_active(ts["enabled"])
        self.sched_status.set_markup(
            _('<b>On.</b> Next check-up: {0}').format(GLib.markup_escape_text(ts['next'] or 'soon')) if ts["enabled"]
            else _("Off. Turn it on to get a notification when your system needs attention."))
        if lr:
            when = time.strftime("%a %d %b %H:%M", time.localtime(lr["ts"]))
            issues = lr.get("issues", [])
            self.sched_last.set_text(_('Last check-up {0}: ').format(when) +
                                     (_('{0} issue(s) — ').format(len(issues)) + ", ".join(t for t, _x in issues[:3])
                                      if issues else _("no issues")) +
                                     (_('; freed {0}').format(human(lr['freed'])) if lr.get("freed", 0) > 1 << 20 else ""))
        else:
            self.sched_last.set_text(_("No check-up has run yet."))
        self.snap_ready.set_text(_("Timeshift is ready.") if ready else why + _(" Snapshots will be skipped."))
        self._maint_loading = False

        counts = {}
        for kind, _k, _t in inv:
            counts[kind] = counts.get(kind, 0) + 1
        names = {"tweak": lambda n: ngettext("{0} optimisation", "{0} optimisations", n),
                 "unit": lambda n: ngettext("{0} boot service", "{0} boot services", n),
                 "autostart": lambda n: ngettext("{0} startup app", "{0} startup apps", n),
                 "flatpak": lambda n: ngettext("{0} Flatpak app", "{0} Flatpak apps", n)}
        self.undo_summary.set_text(
            (_("MintCare has changed ") + ", ".join(names[k](n).format(n) for k, n in counts.items()) +
             _(". Choose what to put back the way it was.")) if inv else
            _("Nothing to undo — MintCare hasn't changed anything that's still active."))

        for ch in self.hist_list.get_children():
            ch.destroy()
        self.hist_head.set_text(_('HISTORY ({0})').format(len(events)))
        if not events:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("Nothing recorded yet. Every fix, tweak, clean-up and undo will appear here."), ("muted",)))
            self.hist_list.add(r)
        kinds = {"fix": ("repair", "ok"), "tweak": ("tweak", "info"), "clean": ("clean", "ok"),
                 "service": ("service", "warn"), "flatpak": ("flatpak", "blue"), "snapshot": ("snapshot", "info"),
                 "undo": ("undo", "warn"), "startup": ("startup", "dim"), "trash": ("trash", "orange"),
                 "scheduled": ("check-up", "ok"), "update": ("update", "ok"), "firmware": ("firmware", "info"),
                 "sources": ("sources", "warn"), "other": ("other", "dim")}
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
                b.pack_end(pill(_("failed"), "fail"), False, False, 0)
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
        m["report"] = self.sched_report.get_active()
        m["report_format"] = self.sched_report_fmt.get_active_id() or "pdf"
        st["snapshot_before"] = self.snap_pref.get_active_id() or "ask"
        maintenance.save_settings(st)
        if self.sched_switch.get_active():
            try:
                maintenance.install_timer(m["frequency"])
            except Exception as e:
                self.log(_('Could not update the check-up timer: {0}').format(e))
        self.set_status(_("Maintenance settings saved"))

    def _on_sched_toggle(self, sw, _pspec):
        if self._maint_loading:
            return
        try:
            if sw.get_active():
                self._save_maint_settings()
                maintenance.install_timer(maintenance.load_settings()["maintenance"]["frequency"])
                maintenance.record("scheduled", _("Scheduled check-up turned on"))
                self.set_status(_("Scheduled check-up turned on"))
            else:
                maintenance.remove_timer()
                maintenance.record("scheduled", _("Scheduled check-up turned off"))
                self.set_status(_("Scheduled check-up turned off"))
        except Exception as e:
            self.log(_('Scheduled check-up: {0}').format(e))
            self.set_status(_("Could not change the schedule"), bad=True)
        self.refresh_maintenance()

    def run_checkup_now(self):
        def done(report):
            if report:
                n = len(report["issues"])
                self.set_status(_('Check-up done: {0} issue(s)').format(n) if n else _("Check-up done: all good"))
            self.refresh_maintenance()
            self.run_diagnose()
            self.run_clean_scan()
        self.in_thread(_("Running check-up…"),
                       lambda: maintenance.run_maintenance(notify=False, on_line=self.log), done)

    def snapshot_now(self):
        ready, why = maintenance.timeshift_ready()
        if not ready:
            self.set_status(why, bad=True)
            return
        step = maintenance.snapshot_step(_("a manual MintCare snapshot"))

        def done(results):
            self.notify_done(results or {}, _("Snapshot"))
            self.load_snapshots()
        self.in_thread(_("Taking a Timeshift snapshot…"), lambda: self.execute([step], [], "Snapshot"), done)

    def _clear_history(self):
        if self.confirm(_("Clear the change history?"), _("This only clears the list; it doesn't undo anything.")):
            maintenance.clear_history()
            self.refresh_maintenance()

    def undo_all(self):
        inv = maintenance.undo_inventory()
        if not inv:
            self.set_status(_("Nothing to undo"))
            return
        d = Gtk.Dialog(title=_("Undo MintCare changes"), transient_for=self, modal=True)
        css(d, "mintcare")
        d.set_default_size(560, 460)
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Undo selected"), Gtk.ResponseType.OK)
        css(d.get_widget_for_response(Gtk.ResponseType.OK), "primary")
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        box.pack_start(label(_("Untick anything you want to keep. Optimisations and boot services need your "
                             "password; startup apps and Flatpak permissions don't."), ("muted",), wrap=True),
                       False, False, 0)
        lst = vbox(4)
        checks_ = []
        groups = {"tweak": _("Optimisations"), "unit": _("Boot services"), "autostart": _("Startup apps"),
                  "flatpak": _("Flatpak permissions")}
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
        snap = self.ask_snapshot(_("an undo of MintCare changes")) if root else []
        if snap is None:
            return

        def work():
            for kind, key, title in user_items:
                try:
                    maintenance.undo_user_item(kind, key)
                    self.log(_('Undone: {0}').format(title))
                    maintenance.record("undo", title, True)
                except Exception as e:
                    self.log(_('Could not undo {0}: {1}').format(title, e))
                    maintenance.record("undo", title, False, str(e))
            return self.execute(root, [], "Undo", snap) if root else {}

        def done(results):
            self.notify_done(results or {}, _("Undo"))
            self._refresh_tweaks()
            self.refresh_maintenance()
            self.run_diagnose()
            if self._boot_loaded:
                self.load_boot()
            if self._fp_loaded:
                self.load_flatpaks()
        self.in_thread(_("Undoing changes…"), work, done)

    # ------------------------------------------------------------ log

    def _build_log(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header("utilities-terminal-symbolic", _("Activity log"),
                                         _("Full output of every command MintCare runs.")), False, False, 0)
        c = card("tight")
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.log_view, "log")
        self.log_buf = self.log_view.get_buffer()
        self.log_buf.set_text(_('MintCare {0}\n').format(__version__))
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.log_view)
        c.pack_start(sw, True, True, 0)
        page.pack_start(c, True, True, 0)
        return page


class MintCareApp(Gtk.Application):
    def __init__(self, start_in_tray=False):
        super().__init__(application_id="org.mintcare.MintCare")
        self.start_in_tray = start_in_tray

    def do_activate(self):
        wins = self.get_windows()
        if wins:
            wins[0].show_from_tray()          # second launch: bring the existing window back
            return
        win = MintCareWindow(self)
        if self.start_in_tray:
            win.hide()
        else:
            win.present()


def main(start_in_tray=False):
    return MintCareApp(start_in_tray).run(None)
