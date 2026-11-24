"""Startup page (boot time, boot history, startup applications, undo all), mixed into MintCareWindow."""

import os
import time

from gi.repository import GLib, Gtk

from .i18n import _
from . import autostart, boot, maintenance, tweaks
from .widgets import BLUE, EMERALD, ORANGE, VIOLET, LineChart, StackedBar, describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import _thumbnail, card, css, hbox, icon, label, list_card, pill, scrolled, vbox


class StartupPage:
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
