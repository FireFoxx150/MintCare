"""GTK 3 interface, styled after MintCare Companion (dark, emerald accent, live gauges)."""

import os
import re
import platform
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

from .i18n import N_, _
from . import (__version__, alerts, autostart, backup, checks, cleaner,  # noqa: E402
               maintenance, selfupdate,
               space, theme, tweaks, widgets)
from .runner import run_steps  # noqa: E402
from .stats import Sampler  # noqa: E402
from .util import human, read, sysctl  # noqa: E402
from .widgets import EMERALD, Bar, describe  # noqa: E402

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
from .gui_hardware import HardwarePage  # noqa: E402
from .gui_security import SecurityPage  # noqa: E402
from .gui_troubleshoot import TroubleshootPage  # noqa: E402
from .gui_startup import StartupPage  # noqa: E402
from .gui_packages import PackagesPage  # noqa: E402
from .gui_processes import ProcessesPage  # noqa: E402
from .gui_log import LogPage  # noqa: E402
from .gui_dashboard import DashboardPage  # noqa: E402
from .gui_diagnose import DiagnosePage  # noqa: E402
from .gui_optimise import OptimisePage  # noqa: E402
from .gui_software import SoftwarePage  # noqa: E402
from .gui_flatpak import FlatpakPage  # noqa: E402


class MintCareWindow(SpacePage, CleanPage, BackupPage, MaintenancePage, HardwarePage, SecurityPage,
                     TroubleshootPage, StartupPage, PackagesPage, ProcessesPage, FlatpakPage, SoftwarePage,
                     OptimisePage, DiagnosePage, DashboardPage, LogPage, Gtk.ApplicationWindow):
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
