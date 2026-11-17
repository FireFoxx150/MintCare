"""GTK 3 interface, styled after MintCare Companion (dark, emerald accent, live gauges)."""

import os
import re
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
from . import (__version__, alerts, autostart, backup, bench, boot, checks, cleaner, firmware,  # noqa: E402
               flatpaks, guard, hardware, lan, maintenance, packages, ports, processes, secaudit, selfupdate, sources,
               space, theme, troubleshoot, tweaks, widgets)
from .runner import run_steps  # noqa: E402
from .stats import Sampler  # noqa: E402
from .util import human, q, read, sysctl  # noqa: E402
from .widgets import BLUE, EMERALD, ORANGE, VIOLET, Bar, LineChart, RingGauge, StackedBar, describe, show_or_hide  # noqa: E402

STATUS_ICON = {
    checks.OK: ("emblem-ok-symbolic", "ok"),
    checks.INFO: ("dialog-information-symbolic", "info"),
    checks.WARN: ("dialog-warning-symbolic", "warn"),
    checks.FAIL: ("dialog-error-symbolic", "fail"),
}
# Job headings are English keys (see maintenance.KIND_FOR_HEADING); they are translated where shown.
_HEADINGS = (N_("Backup"), N_("Boot services"), N_("Clean"), N_("Firmware"), N_("Flatpak"),
             N_("Flatpak repair"), N_("Flatpak update"), N_("Optimise"), N_("Packages"), N_("Processes"),
             N_("Repair"), N_("SMART report"), N_("Security"), N_("Snapshot"), N_("Sources"),
             N_("Timeshift"), N_("Troubleshoot"), N_("Undo"), N_("Update"))
STATUS_WORD = {checks.OK: _("OK"), checks.INFO: _("TIP"), checks.WARN: _("WARN"), checks.FAIL: _("FAIL")}

DASH_ORDER = ("cpu", "memory", "disk", "performance", "health", "network", "details", "gpu", "gpu2")
DASH_COLS = {"wide": 3, "medium": 2, "narrow": 1}
DASH_SPAN = {"wide": {"gpu": (2, 1), "performance": (2, 1), "health": (1, 2)},
             "medium": {"performance": (2, 1), "network": (2, 1), "health": (1, 2)}, "narrow": {}}
def dash_slots(keys, mode):
    """Place dashboard cards in order into a grid of DASH_COLS columns: [(key, column, row, width, height)]."""
    cols, spans, used, out = DASH_COLS[mode], DASH_SPAN[mode], set(), []
    for key in keys:
        w, h = spans.get(key, (1, 1))
        w = min(w, cols)
        r = 0
        while True:
            spot = next(((c, r) for c in range(cols - w + 1)
                         if not {(r + dr, c + dc) for dr in range(h) for dc in range(w)} & used), None)
            if spot:
                c, r = spot
                used |= {(r + dr, c + dc) for dr in range(h) for dc in range(w)}
                out.append((key, c, r, w, h))
                break
            r += 1
    return out


THUMB_MAX_PIXELS = 100_000_000   # no thumbnail for a picture this big: decoding it takes long and a lot of memory


def _thumbnail(path, size=48):
    """A thumbnail of at most `size` pixels for the picture at `path`, or None when it is unreadable or too large."""
    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)     # reads the header only
    if fmt is None or w * h > THUMB_MAX_PIXELS:
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except GLib.Error:
        return None


TIPS = {
    _("TCP congestion"): _("How the computer paces network traffic. BBR is a good, modern choice."),
    _("Swap"): _("Disk space used as extra memory when RAM is full. Some swap use is normal."),
    _("Firewall"): _("Blocks unwanted connections into your PC. Keep it enabled."),
    _("Load average"): _("How busy the processor has been over the last 1, 5 and 15 minutes. "
                         "Higher than the number of threads means it is overloaded."),
}
GPU_HOT_C, GPU_HOT_SAMPLES = 85, 30            # warn after 30 polls (about a minute) at 85°C or more

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
    ("log", _("Activity log"), "utilities-terminal-symbolic"),
]

# Pages and tweaks shown at each experience level (advanced shows everything).
LEVELS = [
    ("beginner", _("Beginner"), _("Essentials only: health check, recommended tweaks, cleaning.")),
    ("intermediate", _("Intermediate"), _("Everything except the most technical tweaks.")),
    ("advanced", _("Advanced"), _("Every page and every tweak.")),
]
BEGINNER_PAGES = {"dashboard", "diagnose", "optimise", "clean", "space", "security", "troubleshoot", "backup",
                  "maintenance", "log"}
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

from .gui_space import SpacePage  # noqa: E402  (after the helpers above, which gui_space imports)
from .gui_clean import CleanPage  # noqa: E402  (same order rule as gui_space)
from .gui_backup import BackupPage  # noqa: E402
from .gui_maintenance import MaintenancePage  # noqa: E402


class MintCareWindow(SpacePage, CleanPage, BackupPage, MaintenancePage, Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MintCare")
        css(self, "mintcare")
        self.set_default_size(1240, 820)
        self.set_size_request(720, 560)
        self._layout_mode = None
        self._theme_eff = "dark"                    # set by apply_theme; read by the status colours
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
        if getattr(self, "g_gpu", None) is not None:
            GLib.timeout_add(2000, self._poll_gpu)
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

    def _dash_names(self):
        names = {"cpu": _("Processor"), "memory": _("Memory"), "disk": _("Disk"), "gpu": _("Graphics"),
                 "performance": _("Performance"), "health": _("Health"), "network": _("Network"),
                 "details": _("Details"), "gpu2": _("Second graphics card")}
        names.update({key: name for key, _g, _t, _c, name in getattr(self, "_gpu_more", [])})
        return names

    def _dash_order(self):
        """Card keys in the user's order, with any new or missing cards placed in the default order."""
        saved = [k for k in maintenance.load_settings().get("dash_order", []) if k in self._dash_cards]
        default = list(DASH_ORDER) + [k for k in self._dash_cards if k not in DASH_ORDER]
        return saved + [k for k in default if k in self._dash_cards and k not in saved]

    def _fill_dash_switches(self):
        for ch in self._dash_switches.get_children():
            ch.destroy()
        self._dash_switches.add(label(_("Show on the dashboard:"), ("faint",)))
        hidden = maintenance.load_settings().get("dash_hidden", [])
        names = self._dash_names()
        order = self._dash_order()
        for i, key in enumerate(order):
            box = hbox(2)
            sw = Gtk.CheckButton(label=names[key], active=key not in hidden)
            sw.connect("toggled", self._toggle_dash_card, key)
            box.pack_start(sw, False, False, 0)
            for step, icon_name, word in ((-1, "go-up-symbolic", _("Move {0} earlier")),
                                          (1, "go-down-symbolic", _("Move {0} later"))):
                if (step < 0 and i == 0) or (step > 0 and i == len(order) - 1):
                    continue
                b = self.action_button("", lambda k=key, st=step: self._move_dash_card(k, st), icon_name=icon_name)
                b.set_tooltip_text(word.format(names[key]))
                describe(b, word.format(names[key]))
                box.pack_start(b, False, False, 0)
            self._dash_switches.add(box)
        for ch in self._dash_switches.get_children():
            ch.show_all()

    def _move_dash_card(self, key, step):
        order = self._dash_order()
        i = order.index(key)
        order[i], order[i + step] = order[i + step], order[i]
        st = maintenance.load_settings()
        st["dash_order"] = order
        maintenance.save_settings(st)
        self._fill_dash_switches()
        self._apply_layout(self._layout_mode or "wide")

    def _layout_dashboard(self, mode):
        """Pack the visible cards in the user's order into the grid, honouring each card's size."""
        grid = self._dash_grid
        hidden = set(maintenance.load_settings().get("dash_hidden", []))
        for wdg in self._dash_cards.values():
            if wdg.get_parent() is grid:
                grid.remove(wdg)
        for key, c, r, w, h in dash_slots([k for k in self._dash_order() if k not in hidden], mode):
            grid.attach(self._dash_cards[key], c, r, w, h)
        grid.set_column_homogeneous(True)
        grid.show_all()

    def _toggle_dash_card(self, sw, key):
        st = maintenance.load_settings()
        hidden = [k for k in st.get("dash_hidden", []) if k != key]
        if not sw.get_active():
            hidden.append(key)
        st["dash_hidden"] = hidden
        maintenance.save_settings(st)
        self._apply_layout(self._layout_mode or "wide")

    def _apply_layout(self, mode):
        if getattr(self, "_dash_grid", None) is not None:
            self._layout_dashboard(mode)
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
        self._theme_eff = eff
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

    def _dismiss_start(self):
        st = maintenance.load_settings()
        st["start_dismissed"] = True
        maintenance.save_settings(st)
        self.start_card.set_visible(False)

    def apply_level(self, level):
        self.level = level
        for key, row in self.nav_rows.items():
            row.set_visible(level != "beginner" or key in BEGINNER_PAGES)
        if getattr(self, "start_card", None) is not None:
            on = level == "beginner" and not maintenance.load_settings().get("start_dismissed")
            if on:                                  # show_all() skips a widget marked no_show_all, so show its contents
                for ch in self.start_card.get_children():
                    ch.show_all()
            self.start_card.set_visible(on)
        # 4. Beginner: the card-order and show/hide row is an advanced control, so it is left out
        if getattr(self, "_dash_switches", None) is not None:
            self._dash_switches.set_no_show_all(True)
            self._dash_switches.set_visible(level != "beginner")

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
        nav_scroll.set_overlay_scrolling(False)       # the scrollbar stays visible: it shows the list continues
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
            "dashboard": self.run_diagnose, "diagnose": self.run_diagnose, "optimise": lambda: (self._refresh_tweaks(), self._refresh_power_profile()),
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
        if name == "optimise":
            self._refresh_power_profile()          # the profile can be changed outside MintCare
        if name == "log":
            self.load_journal()
        if name == "backup":
            self._restic_refresh()
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
        # the icon itself shows when something needs attention, not just the tooltip
        fail = any(r.status == checks.FAIL for r in issues)
        self.tray.set_icon_name("dialog-error" if fail else "dialog-warning" if issues else
                                ("mintcare" if Gtk.IconTheme.get_default().has_icon("mintcare") else "applications-system"))

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
            cmd = autostart.desktop_exec(maintenance.launcher() + ["--tray"])
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

    def export_support_bundle(self):
        """Zip with the health report, hardware list and recent activity, for asking for help."""
        d = Gtk.FileChooserDialog(title=_("Save support bundle"), transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Save"), Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        d.set_current_folder(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or space.HOME)
        d.set_current_name(f"MintCare-support-{time.strftime('%Y-%m-%d')}.zip")
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        from . import report

        def work():
            return report.write_support_bundle(path, progress=lambda m: GLib.idle_add(self.set_status, m))

        def done(saved):
            if saved:
                self.set_status(_("Support bundle saved to {0}").format(saved))
                self.log(_("Support bundle saved to {0}").format(saved))
            else:
                self.set_status(_("Could not write the support bundle"), bad=True)
        self.in_thread(_("Building support bundle…"), work, done)

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

        # "backup overdue" banner, shown while the backup check is at warning level
        self.backup_banner = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        bb = card("ecard")
        br = hbox(12)
        br.pack_start(icon("document-save-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        bt = vbox(2)
        bt.set_valign(Gtk.Align.CENTER)
        bt.pack_start(label(_("Your personal files need a backup"), ("h2",)), False, False, 0)
        self.backup_sub = label("", ("muted",), ellipsize=True)
        bt.pack_start(self.backup_sub, False, False, 0)
        br.pack_start(bt, True, True, 0)
        br.pack_end(self.action_button(_("Back up now"), lambda: self.goto("backup"), primary=True,
                                       icon_name="document-save-symbolic"), False, False, 0)
        bb.pack_start(br, False, False, 0)
        self.backup_banner.add(bb)
        page.pack_start(self.backup_banner, False, False, 0)

        # Start here: three steps for new users; Beginner level only, until dismissed
        self.start_card = card("ecard")
        self.start_card.set_no_show_all(True)
        sc = hbox(10)
        st_text = vbox(2)
        st_text.pack_start(label(_("Start here"), ("h2",)), False, False, 0)
        st_text.pack_start(label(_("Three steps to a healthy PC. You can do them in any order."), ("muted",),
                                 wrap=True), False, False, 0)
        sc.pack_start(st_text, True, True, 0)
        sc.pack_end(self.action_button(_("Got it"), self._dismiss_start), False, False, 0)
        sc.pack_end(self.action_button(_("Free up space"), self.quick_clean), False, False, 0)
        sc.pack_end(self.action_button(_("Fix what it finds"), self.fix_all), False, False, 0)
        sc.pack_end(self.action_button(_("Scan your PC"), self._health_scan, primary=True), False, False, 0)
        self.start_card.pack_start(sc, False, False, 0)
        page.pack_start(self.start_card, False, False, 0)

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
        row.pack_end(self.action_button(_("Refresh"), lambda: self.run_diagnose(fresh=True), icon_name="view-refresh-symbolic"),
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

        week = card("ecard")
        wh = hbox(10)
        wh.pack_start(icon("x-office-calendar-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        wh.pack_start(label(_("This week"), ("h3",)), False, False, 0)
        self.week_lbl = label("", ("faint",), wrap=True)
        wh.pack_start(self.week_lbl, True, True, 0)
        week.pack_start(wh, False, False, 0)
        page.pack_start(week, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        page.pack_start(grid, False, False, 0)

        cpu_model = next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines()
                          if l.startswith("model name")), platform.processor() or "CPU")

        # gauge cards
        self.g_cpu, cpu_card, cpu_foot = self._gauge_card(
            _("Processor Activity"), "speedometer-symbolic", EMERALD, _("CPU load"), _('{0} threads').format(os.cpu_count()), "ok")
        cpu_foot.pack_start(label(cpu_model, ("faint", "mono"), xalign=0.5, ellipsize=True), False, False, 0)
        lr = hbox(0)
        load_name = label(_("Load average"), ("faint", "mono"))
        load_name.set_tooltip_text(TIPS[_("Load average")])
        lr.pack_start(load_name, False, False, 0)
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

        # Graphics cards that report their load (NVIDIA via nvidia-smi, AMD via sysfs), largest VRAM first.
        # The first gets the full card with a load and VRAM history; a second one gets a simpler card.
        gpus = hardware.gpu_load()
        gpu_card = gpu2_card = None
        self._gpu_busy = False
        if gpus:
            self.g_gpu, gpu_card, gpu_foot = self._gauge_card(
                _("Graphics Activity"), "video-display-symbolic", VIOLET, _("GPU load"), gpus[0]["name"], "info")
            self.gpu_vram, self.gpu_temp = self._tiles(gpu_foot, _("VRAM USED"), _("TEMPERATURE"))
            legend = hbox(12)
            legend.set_halign(Gtk.Align.CENTER)
            legend.pack_start(self._legend("GPU", VIOLET), False, False, 0)
            legend.pack_start(self._legend("VRAM", BLUE), False, False, 0)
            self.gpu_history = LineChart([("GPU", VIOLET), ("VRAM", BLUE)], points=60, height=44, fixed_max=100,
                                         axis=False)
            gpu_foot.pack_start(legend, False, False, 2)
            gpu_foot.pack_start(self.gpu_history, False, False, 2)
            self.gpu_chart = LineChart([(_("Temperature"), ORANGE)], points=60, height=44, fixed_max=100, axis=False,
                                       fmt=lambda v: f"{v:.0f}°C")
            gpu_foot.pack_start(self.gpu_chart, False, False, 2)
            self._gpu_hot = []                          # recent temperatures, for the sustained-heat warning
            self._gpu_alerted = False
            self._gpu_temps = maintenance.load_gpu_temps()     # kept across restarts
            for t in self._gpu_temps:
                self.gpu_chart.push(t)
        if len(gpus) > 1:
            self.g_gpu2, gpu2_card, gpu2_foot = self._gauge_card(
                _("Graphics Activity"), "video-display-symbolic", VIOLET, _("GPU load"), gpus[1]["name"], "info")
            (self.gpu2_temp,) = self._tiles(gpu2_foot, _("TEMPERATURE"))
        self._gpu_more = []                 # (key, gauge, temperature label, card, name) for each card after the second
        for i, g in enumerate(gpus[2:], start=3):
            gauge, more_card, more_foot = self._gauge_card(
                _("Graphics Activity"), "video-display-symbolic", VIOLET, _("GPU load"), g["name"], "info")
            (temp,) = self._tiles(more_foot, _("TEMPERATURE"))
            self._gpu_more.append((f"gpu{i}", gauge, temp, more_card, g["name"]))

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
        self._dash_cards = {k: w for k, w in {"cpu": cpu_card, "memory": mem_card, "disk": disk_card,
                                              "gpu": gpu_card, "gpu2": gpu2_card, "performance": perf,
                                              "health": hc, "network": net, "details": det}.items() if w is not None}
        self._dash_cards.update({key: card for key, _g, _t, card, _n in self._gpu_more})
        self._dash_switches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=4)
        page.pack_start(self._dash_switches, False, False, 0)
        page.reorder_child(self._dash_switches, page.get_children().index(grid))
        self._fill_dash_switches()

        self._dash_grid = grid

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

    def _tiles(self, parent, *names):
        row = hbox(8)
        row.set_homogeneous(True)
        vals = []
        for k in names:
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
            key_lbl = label(k, ("kv-key",))
            if k in TIPS:
                key_lbl.set_tooltip_text(TIPS[k])
            self.kv_grid.attach(key_lbl, 0, i, 1, 1)
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

    def _update_weekly(self):
        events, points = maintenance.history(800), maintenance.scores(500)

        def line(days):
            s_ = maintenance.weekly_summary(events, points, days=days)
            parts = [_("{0} cleaning step(s)").format(s_["cleaned"]), _("{0} repair(s)").format(s_["repairs"]),
                     _("{0} scheduled check-up(s)").format(s_["checkups"])]
            text = ", ".join(parts) + "."
            if s_["score_now"] is not None and s_["score_then"] is not None:
                text += _(" Health score {0}, was {1}.").format(s_["score_now"], s_["score_then"])
            return text
        self.week_lbl.set_text(_("Week: ") + line(7) + "\n" + _("Month: ") + line(30))

    def _update_overview(self):
        res = self.check_results
        bk = res.get("backup")
        self.backup_sub.set_text(bk.summary if bk else "")
        self.backup_banner.set_reveal_child(bool(bk) and bk.status == checks.WARN)
        self._update_weekly()
        counts = {s: sum(1 for r in res.values() if r.status == s) for s in STATUS_ICON}
        hidden = set(maintenance.hidden_tips())
        counts[checks.INFO] = sum(1 for cid, r in res.items() if r.status == checks.INFO and cid not in hidden)
        score = max(0, 100 - counts[checks.FAIL] * 15 - counts[checks.WARN] * 5)  # tips do not count
        st = theme.STATUS[self._theme_eff]
        color, kind, word = ((st["ok"], "ok", _("healthy")) if score >= 85 else
                             (st["warn"], "warn", _("needs care")) if score >= 60 else (st["fail"], "fail", _("at risk")))
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
            # The cached answer only offered the update. Install from a live GitHub answer for the same release.
            try:
                fresh = selfupdate.latest_for(info["version"])
            except (OSError, ValueError):
                raise RuntimeError(_("Couldn't reach GitHub to check for updates"))
            if fresh is None:
                raise RuntimeError(_("The latest MintCare release has changed. Check for updates again."))
            try:
                # The checksum comes from SHA256SUMS, which must verify against the key shipped in the package.
                fresh = selfupdate.signed_release(fresh)
            except (OSError, ValueError):
                raise RuntimeError(_("Couldn't reach GitHub to check for updates"))
            data = selfupdate.download(fresh, lambda d, t: GLib.idle_add(
                self.set_status, f"Downloading… {human(d)} of {human(t)}") if t else None)
            self.log(_('Downloaded and verified {0} (sha256 {1}…)').format(fresh['asset_name'], fresh['sha256'][:16]))
            return self.execute([selfupdate.install_step(data, fresh["sha256"], info["version"])], [], "Update")

        def done(results):
            if not results or results.get("selfupdate") != 0:
                self.set_status(_("The update didn't install — see Activity log"), bad=True)
                return
            maintenance.record("update", _('Updated MintCare to {0}').format(info['version']))
            if self.confirm(_('MintCare {0} is installed').format(info['version']), _("Restart MintCare now to use it?")):
                subprocess.Popen(maintenance.relaunch_command(), start_new_session=True)
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
        show_or_hide(self.sw_update_btn, newer and selfupdate.installed_from_package())

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

        hc = hbox(8)
        hc.pack_start(label(_("CONNECTIONS RIGHT NOW"), ("kicker",)), True, True, 0)
        hc.pack_end(self._btn(_("Refresh"), self.refresh_network, icon_name="view-refresh-symbolic"), False, False, 0)
        page.pack_start(hc, False, False, 0)
        self.conn_card, self.conn_list = list_card()
        page.pack_start(self.conn_card, False, False, 0)

        hd = hbox(8)
        hd.pack_start(label(_("DEVICES ON YOUR NETWORK"), ("kicker",)), True, True, 0)
        hd.pack_end(self._btn(_("Mark all as known"), self._mark_all_devices, icon_name="emblem-ok-symbolic"),
                    False, False, 0)
        page.pack_start(hd, False, False, 0)
        self.dev_card, self.dev_list = list_card()
        page.pack_start(self.dev_card, False, False, 0)

        page.pack_start(label(_("LOGIN SECURITY"), ("kicker",)), False, False, 0)
        self.login_card, self.login_list = list_card()
        page.pack_start(self.login_card, False, False, 0)

        page.pack_start(label(_("SYSTEM SECURITY"), ("kicker",)), False, False, 0)
        self.system_card, self.system_list = list_card()
        page.pack_start(self.system_card, False, False, 0)

        page.pack_start(label(_("REAL-TIME PROTECTION"), ("kicker",)), False, False, 0)
        rt = card()
        self._rt_loading = False
        self.rt_decoys = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Decoy files"))
        self.rt_downloads = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Scan downloads with ClamAV"))
        for sw, title, text, name in (
                (self.rt_decoys, _("Decoy files"),
                 _("Three files named !MintCare decoy in Documents. Software that encrypts a folder often reaches "
                   "them early, so you are warned at once if one changes, disappears or is renamed."), "decoys"),
                (self.rt_downloads, _("Scan downloads with ClamAV"),
                 _("Checks each file when its download finishes. Nothing is deleted; you are told about anything "
                   "found."), "downloads")):
            row = hbox(12)
            t = vbox(2)
            t.pack_start(label(title, ("h2",)), False, False, 0)
            t.pack_start(label(text, ("muted",), wrap=True), False, False, 0)
            row.pack_start(t, True, True, 0)
            row.pack_end(sw, False, False, 0)
            rt.pack_start(row, False, False, 6)
            sw.connect("notify::active", lambda s, _p, n=name: self._on_guard_toggle(n, s))
        self.rt_note = label("", ("muted",), wrap=True)
        rt.pack_start(self.rt_note, False, False, 0)
        page.pack_start(rt, False, False, 0)

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

        page.pack_start(label(_("HARDENING AUDIT"), ("kicker",)), False, False, 0)
        lc = card()
        lh = hbox(8)
        self.lynis_status = label("", ("muted",), wrap=True)
        lh.pack_start(self.lynis_status, True, True, 0)
        self.lynis_btns = hbox(8)
        lh.pack_end(self.lynis_btns, False, False, 0)
        lc.pack_start(lh, False, False, 0)
        page.pack_start(lc, False, False, 0)
        self._mal_cancel = threading.Event()
        return scrolled(page)

    def load_security(self):
        self.bg(lambda: (ports.firewall_enabled(), ports.services(root_names=self.root_port_names,
                                                                root_details=getattr(self, 'root_port_details', {}))),
                self._render_security)
        self.bg(secaudit.audit, self._render_login)
        self.bg(secaudit.system_items, self._render_system)
        self._render_malware()
        self._render_lynis()
        self._render_realtime()
        self.refresh_network()

    def refresh_network(self):
        self.bg(ports.connections, self._render_connections)
        self.bg(lan.neighbours, self._render_lan_devices)

    def _render_connections(self, conns):
        self._clear(self.conn_list, "conn_row")
        rows = ports.by_program(conns or [])
        if not rows:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("No programs are connected to other computers right now."), ("muted",)))
            self.conn_list.add(r)
        for name, pid, count, remotes in rows[:30]:
            more = _(" and {0} more").format(len(remotes) - 3) if len(remotes) > 3 else ""
            self.conn_list.add(self._row(
                name or _("Another user's program"), ", ".join(remotes[:3]) + more,
                f"PID {pid}" if pid else "", None,
                [(ngettext("{0} connection", "{0} connections", count).format(count), "dim")], []))
        self.conn_list.show_all()

    def _render_lan_devices(self, devices):
        self._clear(self.dev_list, "dev_row")
        devices = devices or []
        self._devices_now = [d["mac"] for d in devices]
        if not devices:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("No other devices have talked to this PC recently."), ("muted",)))
            self.dev_list.add(r)
        known = lan.known()
        for d in devices[:40]:
            new = d["mac"] not in known
            btns = [self._btn(_("Mark as known"), lambda m=d["mac"]: self._mark_devices([m]), tag="dev_row")] \
                if new else []
            pills = [(_("New"), "warn")] if new else [(_("Known"), "ok")]
            meta = d["mac"] + (f"  ·  {d['dev']}" if d["dev"] else "")
            self.dev_list.add(self._row(", ".join(d["ips"]) or d["mac"], meta, "", None, pills, btns))
        self.dev_list.show_all()

    def _mark_devices(self, macs):
        lan.mark_known(macs)
        self.refresh_network()

    def _mark_all_devices(self):
        lan.mark_known(getattr(self, "_devices_now", []))
        self.refresh_network()

    def _render_realtime(self):
        st = guard.state()
        ready = guard.clamscan_ready()
        self._rt_loading = True
        self.rt_decoys.set_active(bool(st["decoys"]))
        self.rt_downloads.set_active(bool(st["downloads"]) and ready)
        self.rt_downloads.set_sensitive(ready)
        self.rt_note.set_text("" if ready else _("ClamAV isn't installed (clamscan not found). Install the clamav "
                                                  "package to use this."))
        self._rt_loading = False

    def _on_guard_toggle(self, name, sw):
        if self._rt_loading:
            return
        try:
            guard.set_feature(name, sw.get_active(), folder=guard.documents_dir() if name == "decoys" else None)
            self.set_status(_("Real-time protection updated"))
        except Exception as e:
            self.log(_('Real-time protection: {0}').format(e))
            self.set_status(_("Could not change real-time protection"), bad=True)
        self._render_realtime()

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

    def _render_system(self, items):
        self._clear(self.system_list)
        for lvl, title, text in items or []:
            self.system_list.add(self._row(title, text, "", STATUS_ICON[lvl][0]))
        self.system_list.show_all()

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

    def _render_lynis(self):
        self._clear(self.lynis_btns, "lynis_row")
        last = secaudit.read_lynis_summary()
        if last:
            idx, warn, sugg = last
            self.lynis_status.set_text(_("Last audit: hardening index {0}/100, {1} warning(s), {2} suggestion(s). "
                                         "The audit is read-only and changes nothing.").format(idx, warn, sugg))
        else:
            self.lynis_status.set_text(_("Lynis checks how hardened the system is and lists what to tighten. "
                                         "It is read-only and changes nothing."))
        if secaudit.scanners()["lynis"]:
            self.lynis_btns.pack_start(self._btn(_("Run Lynis audit"), self.run_lynis, primary=True,
                                                 tag="lynis_row"), False, False, 0)
            if secaudit.read_lynis_items() is not None:
                self.lynis_btns.pack_start(self._btn(_("View results"), self.show_lynis_results,
                                                     tag="lynis_row"), False, False, 0)
        else:
            self.lynis_btns.pack_start(self._btn(_("Install Lynis"), lambda: self._root_job(
                _("Installing Lynis…"), [secaudit.install_step("lynis")], "Security", lambda r: self._render_lynis()),
                tag="lynis_row"), False, False, 0)
        self.lynis_btns.show_all()

    def run_lynis(self):
        """Run the read-only audit on its own (not in the Activity log), then show its results."""
        def work():
            return run_steps([secaudit.lynis_step()], root=True, on_line=lambda _line: None)

        def done(res):
            self.notify_done(res or {}, _("Hardening audit (Lynis)"))
            self._render_lynis()
            self.show_lynis_results()
        self.in_thread(_("Running Lynis audit…"), work, done)

    def show_lynis_results(self):
        """The findings from the last audit in their own window. Nothing is changed from here: you decide."""
        items = secaudit.read_lynis_items()
        if items is None:
            self.set_status(_("No Lynis results yet. Run the audit first"))
            return
        d = Gtk.Dialog(title=_("Lynis results"), transient_for=self, modal=True)
        d.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        d.set_default_size(680, 560)
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        last = secaudit.read_lynis_summary()
        if last:
            idx, warn, sugg = last
            box.pack_start(label(_("Last audit: hardening index {0}/100, {1} warning(s), {2} suggestion(s). "
                                   "The audit is read-only and changes nothing.").format(idx, warn, sugg),
                                 ("h3",), wrap=True), False, False, 0)
        box.pack_start(label(_("MintCare changes nothing here. Decide what to act on. WARN items may break "
                               "the system if you change them."), ("muted",), wrap=True), False, False, 0)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for kind, test_id, text, risky in items:
            level = checks.WARN if risky else checks.INFO
            where = _("Lynis warning") if kind == "warning" else _("Lynis suggestion")
            sub = text + ("\n" + _("May break the system if changed") if risky else "")
            lst.add(self._row(test_id, sub, where, STATUS_ICON[level][0],
                              pills=((STATUS_WORD[level], STATUS_ICON[level][1]),)))
        box.pack_start(scrolled(lst), True, True, 0)
        d.show_all()
        d.run()
        d.destroy()

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
            skipped = res.get("skipped", 0)
            note = ngettext("{0} file name was not scanned because it contains a line break.",
                            "{0} file names were not scanned because they contain line breaks.", skipped).format(skipped)
            self.mal_progress.set_text((_("Cancelled.") if res.get("cancelled") else
                                        (_('Scan finished: {0} infected file(s).').format(len(found)) if res["rc"] in (0, 1)
                                         else _("The scanner reported an error — see the Activity log.")))
                                       + (" " + note if skipped else ""))
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
        show_or_hide(self.fw_btn, not fw)
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
            names, details, ufw = ports.parse_root_scan(lines)
            self.root_port_names, self.root_port_details = names, details
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
            [self.action_button(_("Rescan"), lambda: self.run_diagnose(fresh=True), icon_name="view-refresh-symbolic"),
             self.action_button(_("Fix all"), self.fix_all, primary=True, icon_name="emblem-ok-symbolic")])
        page.pack_start(self.diag_header, False, False, 0)
        self.diag_box = vbox(10)
        page.pack_start(self.diag_box, False, False, 0)
        return scrolled(page)

    def run_diagnose(self, on_done=None, fresh=False):
        def work():
            if fresh:
                checks.forget_flatpak_count()        # a user's Rescan asks for the Flatpak count again
            return {c.id: c.run() for c in checks.CHECKS}

        def done(res):
            if res is None:
                return
            self.check_results = res
            self._render_diagnose()
            self._update_overview()
            n = sum(1 for r in res.values() if r.status in (checks.WARN, checks.FAIL))
            self.set_status(_('Scan complete: {0} issue(s)').format(n) if n else _("Scan complete: all good"))
            if on_done:
                on_done(res)
        self.in_thread(_("Scanning system…"), work, done)
        return False

    def _render_diagnose(self):
        self._action_buttons = [b for b in self._action_buttons if b not in self._diag_buttons]
        self._diag_buttons = []
        for ch in self.diag_box.get_children():
            ch.destroy()
        order = {checks.FAIL: 0, checks.WARN: 1, checks.INFO: 2, checks.OK: 3}
        cats = list(dict.fromkeys(c.category for c in checks.CHECKS))
        hidden = set(maintenance.hidden_tips())
        for cat in cats:
            items = [c for c in checks.CHECKS if c.category == cat and c.id in self.check_results]
            if not getattr(self, "_show_hidden", False):
                items = [c for c in items if c.id not in hidden or self.check_results[c.id].status != checks.INFO]
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
        n_hidden = sum(1 for cid in hidden if cid in self.check_results and self.check_results[cid].status == checks.INFO)
        if n_hidden:
            text = _("Hide hidden tips") if getattr(self, "_show_hidden", False) else \
                ngettext("Show {0} hidden tip", "Show {0} hidden tips", n_hidden).format(n_hidden)
            tb = self.action_button(text, self._toggle_hidden_tips, icon_name="view-reveal-symbolic")
            self._diag_buttons.append(tb)
            self.diag_box.pack_start(tb, False, False, 0)
        self.diag_box.show_all()
        for cid in list(getattr(self, "_open_details", set())):
            row = self.check_rows.get(cid)
            if row is not None and hasattr(row, "toggle_details"):
                row.toggle_details(force=True)

    def _toggle_hidden_tips(self):
        self._show_hidden = not getattr(self, "_show_hidden", False)
        self._render_diagnose()

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
        if res.status == checks.INFO:                  # tips can be hidden; warnings and problems never are
            hiding = check.id not in set(maintenance.hidden_tips())
            tb = self.action_button(_("Hide this tip") if hiding else _("Show again"),
                                    lambda cid=check.id, h=hiding: (maintenance.set_tip_hidden(cid, h),
                                                                    self._render_diagnose()), icon_name=None)
            self._diag_buttons.append(tb)
            box.pack_end(tb, False, False, 0)
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
                for ch in acts.get_children() + dismissals.get_children():
                    ch.destroy()
                for lbl, kind, payload in (res[2] if res and len(res) > 2 else []):
                    b = Gtk.Button(label=lbl)
                    css(b, "primary" if kind in ("check", "tweak") else "ghost")
                    b.connect("clicked", lambda *_x, k=kind, p=payload: self.run_action(k, p))
                    (dismissals if kind == "dismiss" else acts).pack_start(b, False, False, 0)
                acts.show_all()
                dismissals.show_all()
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
        panel.pack_start(top, False, False, 0)
        panel.pack_start(sw, False, False, 0)
        acts = hbox(8)
        panel.pack_start(acts, False, False, 2)
        dismissals = vbox(4)
        panel.pack_start(dismissals, False, False, 2)
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
                dest = user if t.user else root
                if not any(s[0] == "tweak-" + t.id for s in dest):
                    dest.append(("tweak-" + t.id, t.title, t.apply_script()))
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

        before = dict(self.check_results)

        def done(results):
            self.notify_done(results or {}, _("Repair"))
            self._refresh_tweaks()
            self.run_diagnose(on_done=lambda after: self._show_before_after(before, after))
        self.in_thread(_("Fixing…"), lambda: self.execute(root, user, "Repair", snap), done)

    def _show_before_after(self, before, after):
        if not before:                                  # no scan before the repair, nothing to compare
            return
        fixed, still, new = checks.compare(before, after, {c.id: c.title for c in checks.CHECKS})
        summary = _("Before and after: {0} fixed, {1} still open, {2} new").format(len(fixed), len(still), len(new))
        self.log(summary)
        for title in fixed:
            self.log(_("  Fixed: {0}").format(title))
        for title in still:
            self.log(_("  Still open: {0}").format(title))
        for title in new:
            self.log(_("  New: {0}").format(title))
        self.set_status(summary)
        maintenance.record("repair", summary, True, "; ".join(fixed))

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

    def _power_profile_row(self):
        row = hbox(10)
        row.set_no_show_all(True)                  # shown only when power-profiles-daemon is installed
        row.pack_start(label(_("Power profile"), ("h3",)), False, False, 0)
        self.power_btns = {}
        for name, text in (("performance", _("Performance")), ("balanced", _("Balanced")),
                           ("power-saver", _("Power saver"))):
            b = self.action_button(text, lambda n=name: self._set_power_profile(n))
            b.set_no_show_all(True)                # hidden unless the daemon offers this profile
            self.power_btns[name] = b
            row.pack_start(b, False, False, 0)
        return row

    def _refresh_power_profile(self):
        from . import powerprof

        def work():
            names = powerprof.profiles() if powerprof.available() else []
            return names, (powerprof.active() if names else "")

        def done(res):
            names, now = res
            show_or_hide(self.power_row, bool(names))
            for name, b in self.power_btns.items():
                show_or_hide(b, name in names)
                ctx = b.get_style_context()
                ctx.remove_class("primary")
                ctx.remove_class("ghost")
                css(b, "primary" if name == now else "ghost")
        self.bg(work, done)

    def _set_power_profile(self, name):
        from . import powerprof

        def done(ok):
            if not ok:
                self.set_status(_("Couldn't switch the power profile"), bad=True)
            self._refresh_power_profile()
        self.bg(lambda: powerprof.set_profile(name), done)

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

        self.power_row = self._power_profile_row()
        page.pack_start(self.power_row, False, False, 0)
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
        self.limit_spins = {}
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
        if t.limit:                                    # the level the tweak applies when switched on
            spin = describe(Gtk.SpinButton.new_with_range(50, 100, 5), _("Charge limit"),
                            _("The battery stops charging at this level"))
            spin.set_value(t.limit())
            spin.set_valign(Gtk.Align.CENTER)
            spin.set_sensitive(False)                  # enabled once the probe says this PC has a battery
            self.limit_spins[t.id] = spin
            spin.connect("value-changed", lambda s: tweaks.set_charge_limit(int(s.get_value())))
            box.pack_end(spin, False, False, 0)
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
            if t.id in self.limit_spins:
                self.limit_spins[t.id].set_sensitive(avail)
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
        steps = [("off-" + t.id, "Revert: " + t.title, t.revert_script(), t.user) for t in off]
        steps += [("on-" + t.id, "Apply: " + t.title, t.apply_script(), t.user) for t in on]
        root = [s_[:3] for s_ in steps if not s_[3]]
        user = [s_[:3] for s_ in steps if s_[3]]
        snap = self.ask_snapshot(_('{0} optimisation change(s)').format(len(steps))) if root else []
        if snap is None:
            return

        def done(results):
            self.notify_done(results or {}, _("Optimisation"))
            self._refresh_tweaks()
            self.run_diagnose()
        self.in_thread(_("Applying tweaks…"), lambda: self.execute(root, user, "Optimise", snap), done)

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
        elif kind == "dismiss":
            from . import journal
            journal.set_dismissed(*payload)
            self.run_diagnose()
        elif kind == "restore":
            from . import journal
            journal.clear_dismissed()
            self.run_diagnose()
        elif kind == "tweak":
            t = tweaks.BY_ID.get(payload)
            if t and t.state() == "off":
                steps = [("on-" + t.id, "Apply: " + t.title, t.apply_script())]
                self.in_thread(_("Applying…"), lambda: self.execute([] if t.user else steps, steps if t.user else [],
                                                                    "Optimise"),
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
        describe(self.pkg_mode, _("Which packages to show"))
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
            held = packages.held()
            return {"pk": pk, "hist": h, "mine": packages.user_installed(pk, h), "held": held,
                    "held_why": {n: packages.hold_explanation(n) for n in held[:80]},
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
            desc = self.pkg_data.get("held_why", {}).get(n, v["desc"]) if mode == "held" else v["desc"]
            self.pkg_list.add(self._row(n, desc, f"{v['version']} · {v['section']}", None, pills, btns,
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
            show_or_hide(self.mir_use_btn, bool(gain))
            self.mir_list.show_all()
        self.bg(lambda: packages.test_mirrors(prog), done)
        show_or_hide(self.mir_restore_btn, Path("/var/lib/mintcare/official-package-repositories.list.orig").exists())

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
        describe(self.proc_sort, _("Sort processes by"))
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
        self.proc_gpu = label("", ("faint", "mono"), wrap=True)
        page.pack_start(self.proc_gpu, False, False, 0)
        self.proc_card, self.proc_list = list_card()
        page.pack_start(self.proc_card, False, False, 0)
        self._proc_prev = None
        self._proc_open = set()
        self._proc_infos = {}                 # pid -> processes.describe() for the rows that are open
        self._lowered = {}                    # pid -> (name, nice before MintCare lowered it)
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
        open_pids = set(self._proc_open)

        def work():
            rows, prev = processes.processes(self._proc_prev)
            infos = {r["pid"]: processes.describe(r) for r in rows if r["pid"] in open_pids}
            return (rows, prev), processes.gpu_processes(), infos

        def done(res):
            self._proc_busy = False
            if res:
                (rows, self._proc_prev), gpu, self._proc_infos = res
                self._render_procs(rows)
                self._render_gpu_procs(gpu)
        self.bg(work, done)
        return True

    def _render_procs(self, rows):
        self._clear(self.proc_list, "proc_row")
        key = self.proc_sort.get_active_id()
        flt = self.proc_filter.get_text().strip().lower()
        shown = [r for r in rows if (self.proc_all.get_active() or r["mine"]) and not r["kernel"]
                 and (not flt or flt in r["name"].lower() or flt in r["cmd"].lower())]
        shown.sort(key=lambda r: -r[key])
        live = {r["pid"] for r in rows}
        self._lowered = {pid: v for pid, v in self._lowered.items() if pid in live}
        tot_cpu = sum(r["cpu"] for r in rows)
        # xgettext:no-python-format
        self.proc_head.set_text(_('{0} processes · {1:.0f}% of all CPU · showing {2}').format(len(rows), tot_cpu / (os.cpu_count() or 1), min(len(shown), 40)))
        for r in shown[:40]:
            pills = [] if r["mine"] else [(r["user"], "dim")]
            btns = [self._btn(_("What is this?"), lambda r=r: self._proc_info(r), tag="proc_row"),
                    self._btn(_("End"), lambda r=r: self._end_proc(r), tag="proc_row")]
            if r["mine"]:
                btns.insert(1, self._btn(_("Lower priority"), lambda r=r: self._lower_proc(r), tag="proc_row"))
            if r["pid"] in self._lowered:
                btns.append(self._btn(_("Restore priority"), lambda r=r: self._restore_proc(r), tag="proc_row"))
            row = self._row(r["name"], "", r["cmd"][:300], None, pills, btns,
                            f"{r['cpu']:4.0f}% · {human(r['rss'])}")
            if r["pid"] in self._proc_open and r["pid"] in self._proc_infos:
                info = self._proc_infos[r["pid"]]
                txt = info["text"] or _("No description for this one.")
                if info["package"]:
                    txt += f"\nPackage: {info['package']} — {info['summary']}"
                row.text_box.pack_start(label(txt, ("muted",), wrap=True), False, False, 0)
            self.proc_list.add(row)
        self.proc_list.show_all()

    def _render_gpu_procs(self, procs):
        if not procs:
            self.proc_gpu.set_text("")
            return
        self.proc_gpu.set_text(_("Graphics memory: {0}").format(" · ".join(
            f"{name} ({pid}) {human(mb * 2 ** 20)}" for pid, name, mb in procs[:8])))

    def _proc_info(self, r):
        (self._proc_open.discard if r["pid"] in self._proc_open else self._proc_open.add)(r["pid"])
        self._poll_procs(True)

    def _lower_proc(self, r):
        before = processes.nice_of(r["pid"])
        ok, msg = processes.lower_priority(r["pid"], r["name"])
        if ok and before is not None:
            self._lowered[r["pid"]] = (r["name"], before)
            self.set_status(_("{0} now runs at lower priority").format(r["name"]))
        else:
            self.set_status(msg or _("Could not change the priority"), bad=True)
        self._poll_procs(True)

    def _restore_proc(self, r):
        name, nice = self._lowered.get(r["pid"], (r["name"], 0))
        step = processes.restore_step(r["pid"], name, nice)

        def done(results):
            if results.get(step[0]) == 0:
                self._lowered.pop(r["pid"], None)
            self._poll_procs(True)
        self._root_job(_("Restoring priority…"), [step], _("Processes"), done_cb=done)

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
              "programs."),
            [self.action_button(_("Support bundle…"), self.export_support_bundle, icon_name="document-save-symbolic")]),
            False, False, 0)
        self.ts_boxes = {}
        tabs = []
        for key, title in (("sound", _("Sound")), ("network", _("Network")), ("devices", _("Printers & Bluetooth")),
                           ("crashes", _("Crash reports")), ("upkeep", _("Maintenance"))):
            b = vbox(10)
            self.ts_boxes[key] = b
            tabs.append((key, title, b))
        self.ts_tabs = self._tabs(page, tabs)
        self.ts_tabs.connect("notify::visible-child-name", lambda *_x: self.load_troubleshoot())
        return scrolled(page)

    def _render_upkeep(self, tasks):
        box = self.ts_boxes["upkeep"]
        self._clear(box, "ts_upkeep")
        c, lst = list_card()
        if not tasks:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("None of the maintenance tools are installed on this PC."), ("muted",)))
            lst.add(r)
        for tid, title, why, runs, command, _tool in tasks:
            btn = self._btn(_("Run"), lambda t=(tid, title, runs, command): self._run_upkeep(*t), primary=True,
                            icon_name="media-playback-start-symbolic", tag="ts_upkeep")
            meta = _("Asks for your password") if runs == "root" else ""
            lst.add(self._row(title, why, meta, None, [], [btn]))
        box.pack_start(c, False, False, 0)
        box.show_all()

    def _run_upkeep(self, tid, title, runs, command):
        step = [(tid, title, command)]
        work = (lambda: self.execute(step, [], title)) if runs == "root" else (lambda: self.execute([], step, title))
        self.in_thread(title, work, lambda res: self.notify_done(res or {}, title))

    def load_troubleshoot(self):
        key = self.ts_tabs.get_visible_child_name()
        box = self.ts_boxes[key]
        self._clear(box, "ts_" + key)
        box.pack_start(label(_("Checking…"), ("muted",)), False, False, 0)
        box.show_all()
        work = {"sound": troubleshoot.audio, "network": troubleshoot.network,
                "devices": lambda: (troubleshoot.printers(), troubleshoot.bluetooth()),
                "crashes": troubleshoot.crashes, "upkeep": troubleshoot.upkeep_tasks}[key]
        render = {"sound": self._render_sound, "network": self._render_network, "devices": self._render_devices,
                  "crashes": self._render_crashes, "upkeep": self._render_upkeep}[key]
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

    def speed_test(self):
        def done(r):
            if not r:
                self._speed_text = _("Couldn't reach speed.cloudflare.com — check your internet connection.")
            else:
                fmt = lambda v: _('{0:.1f} Mb/s').format(v) if v is not None else "–"
                self._speed_text = _('Download {0} · Upload {1} · Latency {2:.0f} ms').format(
                    fmt(r["down_mbps"]), fmt(r["up_mbps"]), r["ping_ms"])
                maintenance.record("other", _('Speed test: {0}').format(self._speed_text))
            self.speed_lbl.set_text(self._speed_text)
            self.set_status(self._speed_text)

        def work():
            try:
                return troubleshoot.speed_test()
            except OSError:
                return None
        self.in_thread(_("Testing internet speed…"), work, done)

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
        sc = card()
        sr = hbox(12)
        st = vbox(2)
        st.pack_start(label(_("Internet speed"), ("h3",)), False, False, 0)
        self.speed_lbl = label(getattr(self, "_speed_text", None) or
                               _("Measures download and upload speed against speed.cloudflare.com (uses up to about 120 MB of data)."),
                               ("muted",), wrap=True)
        st.pack_start(self.speed_lbl, False, False, 0)
        sr.pack_start(st, True, True, 0)
        sr.pack_end(self._btn(_("Test speed"), self.speed_test, primary=True, tag="ts_network"), False, False, 0)
        sc.pack_start(sr, False, False, 0)
        box.pack_start(sc, False, False, 0)
        dc = card()
        dr = hbox(12)
        dt = vbox(2)
        dt.pack_start(label(_("DNS servers"), ("h3",)), False, False, 0)
        self.dns_lbl = label(getattr(self, "_dns_text", None) or
                             _("Times how quickly four public DNS servers answer, fastest first."), ("muted",), wrap=True)
        dt.pack_start(self.dns_lbl, False, False, 0)
        dr.pack_start(dt, True, True, 0)
        dr.pack_end(self._btn(_("Compare DNS servers"), self.dns_benchmark, tag="ts_network"), False, False, 0)
        dc.pack_start(dr, False, False, 0)
        box.pack_start(dc, False, False, 0)
        if n["nearby"]:
            box.pack_start(label(_("NEARBY WI-FI"), ("kicker",)), False, False, 0)
            nc, nl = list_card()
            for w in sorted(n["nearby"], key=lambda w: -w["signal"])[:12]:
                nl.add(self._row(w["ssid"] or _("(hidden)"), "", _('channel {0} · {1} · {2}').format(w['chan'], w['freq'], w['security']),
                                 None, [(_("connected"), "ok")] if w["active"] else [], (), f"{w['signal']}%"))
            box.pack_start(nc, False, False, 0)
        box.show_all()

    def dns_benchmark(self):
        from . import dnsbench
        self.dns_lbl.set_text(_("Testing DNS servers…"))
        self.bg(dnsbench.benchmark, self._render_dns)

    def _render_dns(self, rows):
        if rows is None:
            text = _("The DNS test failed. See the Activity log for details.")
        else:
            text = " · ".join(f"{name}: " + (f"{ms:.0f} ms" if ms is not None else _("no answer")) for name, ms in rows)
        self._dns_text = text
        self.dns_lbl.set_text(text)

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
            self.run_diagnose(fresh=True)            # the update changed the count the Diagnose page shows
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
             self.action_button(_("Full SMART report"), self.smart_report, icon_name="utilities-terminal-symbolic"),
             self.action_button(_("Short self-test"), lambda: self.smart_selftest("short"),
                                icon_name="media-playback-start-symbolic"),
             self.action_button(_("Long self-test"), lambda: self.smart_selftest("long"),
                                icon_name="media-playback-start-symbolic")]),
            False, False, 0)

        ic = card()
        ih = hbox(8)
        ih.pack_start(icon("computer-symbolic", Gtk.IconSize.BUTTON, "info"), False, False, 0)
        ih.pack_start(label(_("Your hardware"), ("h2",)), False, False, 0)
        ih.pack_end(self.action_button(_("Read RAM details…"), self.read_ram_details,
                                       icon_name="dialog-password-symbolic"), False, False, 0)
        ic.pack_start(ih, False, False, 0)
        self.inv_box = vbox(8)
        self.inv_box.pack_start(label(_("Detecting hardware…"), ("faint",)), False, False, 0)
        ic.pack_start(self.inv_box, False, False, 10)
        page.pack_start(ic, False, False, 0)

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
        # power history only where the machine reports power (see hardware.power_draw)
        self.power_chart = None
        if hardware.power_draw() is not None:
            tc.pack_start(label(_("POWER DRAW, LAST 4 MINUTES"), ("kicker",)), False, False, 0)
            self.power_chart = LineChart([(_("Power"), ORANGE)], points=120, height=70, axis=False,
                                         fmt=lambda v: f"{v:.1f} W")
            tc.pack_start(self.power_chart, False, False, 6)
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
        describe(self.bench_target, _("Folder to test"))
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
        self.bg(hardware.inventory, self._render_inventory)
        self.load_firmware()
        self._render_battery()
        self._render_bench()

    INV_ICONS = {"computer": "computer-symbolic", "cpu": "system-run-symbolic", "memory": "media-flash-symbolic",
                 "gpu": "video-display-symbolic", "network": "network-wired-symbolic",
                 "sound": "audio-card-symbolic", "drive": "drive-harddisk-symbolic"}

    def read_ram_details(self):
        """Admin read of RAM type and speed (dmidecode needs root); the result is kept for the hardware list."""
        step = ("ram-detail", _("Read RAM type and speed"),
                f"install -d -m 755 /var/lib/mintcare && dmidecode -t memory > {hardware.MEMORY_FILE} && "
                f"chmod 644 {hardware.MEMORY_FILE}")
        self._root_job(_("Reading RAM details…"), [step], "Hardware",
                       lambda r: (hardware._INVENTORY.update(at=0), self.bg(hardware.inventory, self._render_inventory)))

    def _render_inventory(self, items):
        self._clear(self.inv_box)
        if not items:
            self.inv_box.pack_start(label(_("No hardware details could be read."), ("faint",)), False, False, 0)
        for kind, name, detail in items:
            row = hbox(10)
            row.pack_start(icon(self.INV_ICONS.get(kind, "dialog-information-symbolic")), False, False, 0)
            t = vbox(1)
            t.pack_start(label(name, wrap=True, selectable=True), False, False, 0)
            if detail:
                t.pack_start(label(detail, ("muted",), wrap=True), False, False, 0)
            row.pack_start(t, True, True, 0)
            self.inv_box.pack_start(row, False, False, 0)
        self.inv_box.show_all()

    def _render_battery(self):
        self._clear(self.bat_box)
        bats = hardware.batteries()
        hardware.battery_track(bats)
        for b in bats:
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
            trend = hardware.battery_trend(b["name"])
            if trend:
                t.pack_start(label(trend, ("muted",), wrap=True), False, False, 0)
            h.pack_start(t, True, True, 0)
            if hl is not None:
                h.pack_end(pill(_("good") if hl >= 80 else _("worn") if hl >= 60 else _("replace soon"),
                                "ok" if hl >= 80 else "warn" if hl >= 60 else "fail"), False, False, 0)
            c.pack_start(h, False, False, 0)
            if hl is not None:
                st = theme.STATUS[self._theme_eff]
                bar = Bar(st["ok"] if hl >= 80 else st["warn"] if hl >= 60 else st["fail"], 8)
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
        show_or_hide(self.fw_install_btn, bool(ups))
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
            if d.get("temp") is not None and d["device"]:
                maintenance.record_drive_temp(d["device"], d["temp"])
                rng = maintenance.drive_temp_range(d["device"])
                if rng:
                    t.pack_start(label(_("Temperature over the last week: {0:.0f}–{1:.0f} °C ({2} readings)")
                                       .format(rng[0], rng[1], rng[2]), ("faint",)), False, False, 0)
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

    def _poll_gpu(self):
        if self.stack.get_visible_child_name() == "dashboard" and not self._gpu_busy:
            self._gpu_busy = True
            self.bg(hardware.gpu_load, self._render_gpu)
        return True

    def _render_gpu(self, gpus):
        self._gpu_busy = False
        if not gpus:
            return
        g = gpus[0]
        self.g_gpu.set_value(g["util"] or 0)
        used, total = g["vram_used"], g["vram_total"]
        self.gpu_vram.set_text(f"{human(used)} / {human(total)}" if used is not None and total else "n/a")
        self.gpu_temp.set_text(f"{g['temp']:.0f}°C" if g["temp"] is not None else "n/a")
        self.gpu_history.push(g["util"] or 0, used / total * 100 if used is not None and total else 0)
        if len(gpus) > 1 and getattr(self, "g_gpu2", None) is not None:
            g2 = gpus[1]
            self.g_gpu2.set_value(g2["util"] or 0)
            self.gpu2_temp.set_text(f"{g2['temp']:.0f}°C" if g2["temp"] is not None else "n/a")
        for (_k, gauge, temp, _c, _n), more in zip(getattr(self, "_gpu_more", []), gpus[2:]):
            gauge.set_value(more["util"] or 0)
            temp.set_text(f"{more['temp']:.0f}°C" if more["temp"] is not None else "n/a")
        if g["temp"] is not None:
            self.gpu_chart.push(g["temp"])
            self._gpu_temps = (self._gpu_temps + [g["temp"]])[-60:]
            self._gpu_polls = getattr(self, "_gpu_polls", 0) + 1
            if self._gpu_polls % 15 == 0:               # save about every 30 seconds, not every poll
                maintenance.save_gpu_temps(self._gpu_temps)
            self._gpu_hot = (self._gpu_hot + [g["temp"]])[-GPU_HOT_SAMPLES:]
            hot = len(self._gpu_hot) == GPU_HOT_SAMPLES and all(t >= GPU_HOT_C for t in self._gpu_hot)
            if hot and not self._gpu_alerted:
                self._gpu_alerted = True
                self.set_status(_("The graphics card has been above {0}°C for a minute — check its cooling")
                                .format(GPU_HOT_C), bad=True)
            elif not hot:
                self._gpu_alerted = False

    def _poll_sensors(self, force=False):
        visible = self.stack.get_visible_child_name() == "hardware"
        if (visible or force) and not self._sensor_busy:
            self._sensor_busy = True
            self.bg(lambda: (hardware.sensors(), hardware.throttling(), hardware.cpu_freq(), hardware.nvidia(),
                             hardware.power_draw()), self._render_sensors)
        return True

    def _render_sensors(self, res):
        self._sensor_busy = False
        if not res:
            return
        sens, thr, (cur, mx), gpu, watts = res
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
        if watts:
            tiles.append(("POWER DRAW", f"{watts:.1f} W", checks.INFO, _("whole system, right now"), 0))
            if self.power_chart is not None:
                self.power_chart.push(watts)
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
        st = theme.STATUS[self._theme_eff]
        colors = {checks.OK: st["ok"], checks.INFO: BLUE, checks.WARN: st["warn"], checks.FAIL: st["fail"]}
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

    def smart_selftest(self, kind="short"):
        ds = [d for d in getattr(self, "drives", []) if d.get("device")]
        if not ds:
            self.set_status(_("No drives loaded yet"))
            return

        def done(results):
            self.notify_done(results or {}, _("SMART self-test"))
            self.load_drives()
        self.in_thread(_("Starting SMART self-tests…"),
                       lambda: self.execute(hardware.selftest_steps(ds, kind), [], _("SMART self-test")), done)

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
        trend = (_("slower than average") if last > avg * 1.15 else _("faster than average") if last < avg * 0.85
                 else _("about average"))
        self.boot_hist_lbl.set_text(
            _('Last {0} boots: this one took {1:.1f} s, {2} ({3:.1f} s). Slowest was {4:.1f} s on {5}.').format(len(h), last, trend, avg, worst['total'], time.strftime('%d %b', time.localtime(worst['ts']))))

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
            pixbuf = _thumbnail(name, 28)              # the same size limit as the duplicate list's thumbnails
            img = Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf is not None else None
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
        if not locked:
            delay = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
            choices = [0, 10, 30, 60] + ([a["delay"]] if a["delay"] not in (0, 10, 30, 60) else [])
            for secs in choices:
                delay.append(str(secs), _("Start straight away") if not secs else _('Start {0} s after login').format(secs))
            delay.set_active_id(str(a["delay"]))
            describe(delay, _('When {0} starts').format(a["name"]))
            delay.set_tooltip_text(_("Delaying apps that aren't needed straight away makes the desktop ready sooner."))
            delay.connect("changed", self._on_app_delay, a)
            box.pack_end(delay, False, False, 0)
        row.add(box)
        return row

    def _on_app_delay(self, combo, a):
        secs = int(combo.get_active_id() or 0)
        try:
            autostart.set_delay(a["file"], secs)
        except Exception as e:
            self.log(_('Could not change startup entry {0}: {1}').format(a['file'], e))
            self.set_status(_('Could not change {0}').format(a['name']), bad=True)
            return
        a["delay"] = secs
        maintenance.record("startup", _('Startup app {0}: starts {1} s after login').format(a['name'], secs))
        self.set_status(_('{0} will start {1} s after login').format(a['name'], secs) if secs else
                        _('{0} will start straight away').format(a['name']))

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
        root, user_steps = [], []
        for kind, key, title in chosen:
            if kind == "tweak":
                t = tweaks.BY_ID[key]
                (user_steps if t.user else root).append(("undo-" + key, "Undo: " + t.title, t.revert_script()))
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
            return self.execute(root, user_steps, "Undo", snap) if root or user_steps else {}

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
        switch = Gtk.StackSwitcher(halign=Gtk.Align.START)
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        switch.set_stack(stack)
        stack.add_titled(c, "app", _("MintCare"))
        stack.add_titled(self._journal_view(), "journal", _("System journal"))
        page.pack_start(switch, False, False, 0)
        page.pack_start(stack, True, True, 0)
        return page

    def _journal_view(self):
        box = vbox(10)
        bar = hbox(8)
        self.jr_scope = Gtk.ComboBoxText()
        for key, text in (("boot", _("This boot")), ("day", _("Last 24 hours")), ("week", _("Last 7 days"))):
            self.jr_scope.append(key, text)
        self.jr_scope.set_active_id("boot")
        self.jr_level = Gtk.ComboBoxText()
        for key, text in (("3", _("Errors")), ("4", _("Warnings and errors")), ("7", _("Everything"))):
            self.jr_level.append(key, text)
        self.jr_level.set_active_id("3")
        self.jr_find = Gtk.SearchEntry(placeholder_text=_("Filter by program or text…"))
        self.jr_scope.connect("changed", lambda *_x: self.load_journal())
        self.jr_level.connect("changed", lambda *_x: self.load_journal())
        self.jr_find.connect("activate", lambda *_x: self.load_journal())
        bar.pack_start(self.jr_scope, False, False, 0)
        bar.pack_start(self.jr_level, False, False, 0)
        bar.pack_start(self.jr_find, True, True, 0)
        bar.pack_end(self._btn(_("Refresh"), self.load_journal, icon_name="view-refresh-symbolic"), False, False, 0)
        box.pack_start(bar, False, False, 0)
        self.jr_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.jr_view, "log")
        self.jr_buf = self.jr_view.get_buffer()
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.jr_view)
        c = card("tight")
        c.pack_start(sw, True, True, 0)
        box.pack_start(c, True, True, 0)
        return box

    def load_journal(self):
        from . import journal
        scope, level, text = self.jr_scope.get_active_id(), self.jr_level.get_active_id(), self.jr_find.get_text()

        def done(res):
            rc, rows = res or (1, [])
            if rc != 0 and not rows:
                msg = _("Couldn't read the system journal. Your account may need to be in the adm group.")
            elif not rows:
                msg = _("Nothing matches these filters.")
            else:
                msg = "\n".join(f"{t}  {who}: {m}" for t, who, m in rows)
            self.jr_buf.set_text(msg)
        self.bg(lambda: journal.browse(scope, level, text), done)


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
