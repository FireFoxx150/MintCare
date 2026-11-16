"""Maintenance page: check-up schedule, alerts, reports, history and snapshots.

Mixed into MintCareWindow. Methods are moved here unchanged from gui.py.
"""

import time
from pathlib import Path
from gi.repository import GLib, Gtk
from . import alerts, maintenance
from .i18n import _, ngettext
from .util import human
from .widgets import describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, css, hbox, icon, label, list_card, pill, scrolled, vbox


class MaintenancePage:
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
        describe(self.sched_freq, _("How often to check up"))
        for k, t in (("daily", _("Every day")), ("weekly", _("Every Monday")), ("monthly", _("1st of each month"))):
            self.sched_freq.append(k, t)
        self.sched_freq.connect("changed", lambda *_x: self._save_maint_settings())
        g.attach(self.sched_freq, 1, 0, 1, 1)
        g.attach(label(_("Notify me"), ("muted",)), 0, 1, 1, 1)
        self.sched_notify = Gtk.ComboBoxText()
        describe(self.sched_notify, _("When to notify you"))
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
        describe(self.sched_report_fmt, _("Report format"))
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
        describe(self.snap_pref, _("Snapshot before repairs"))
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

        # threshold alerts: a timer, so they work while MintCare is closed
        ac = card()
        h = hbox(8)
        h.pack_start(icon("dialog-warning-symbolic", Gtk.IconSize.BUTTON, "warn"), False, False, 0)
        h.pack_start(label(_("Threshold alerts"), ("h2",)), True, True, 0)
        self.alert_switch = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Threshold alerts"))
        self.alert_switch.connect("notify::active", self._on_alert_toggle)
        h.pack_end(self.alert_switch, False, False, 0)
        ac.pack_start(h, False, False, 0)
        # xgettext:no-python-format
        ac.pack_start(label(_("Checks every 15 minutes, even when MintCare is closed. Tells you once when a drive is "
                              "{0}% full, the battery is at {1}% or less and not charging, or a processor or drive "
                              "runs hot.").format(alerts.DISK_FULL, alerts.BATTERY_LOW), ("faint",), wrap=True),
                      False, False, 6)

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
        two = [(sc, 0, 0, 1, 1), (sf, 1, 0, 1, 1), (ac, 0, 1, 1, 1), (uc, 1, 1, 1, 1)]
        stacked = [(sc, 0, 0, 1, 1), (sf, 0, 1, 1, 1), (ac, 0, 2, 1, 1), (uc, 0, 3, 1, 1)]
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
                    maintenance.undo_inventory(), maintenance.history(300), alerts.timer_enabled())
        self.bg(work, self._render_maintenance)

    def _render_maintenance(self, res):
        if not res:
            return
        ts, lr, (ready, why), inv, events, alerts_on = res
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
        self.alert_switch.set_active(alerts_on)
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

    def _on_alert_toggle(self, sw, _pspec):
        if self._maint_loading:
            return
        try:
            if sw.get_active():
                alerts.install_timer()
                self.set_status(_("Threshold alerts turned on"))
            else:
                alerts.remove_timer()
                self.set_status(_("Threshold alerts turned off"))
        except Exception as e:
            self.log(_('Threshold alerts: {0}').format(e))
            self.set_status(_("Could not change threshold alerts"), bad=True)
        self.refresh_maintenance()

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
