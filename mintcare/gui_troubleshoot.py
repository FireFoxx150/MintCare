"""Troubleshoot page (upkeep, sound, network and DNS, devices and printers, crashes), mixed into MintCareWindow."""

import time

from gi.repository import GLib, Gtk

from .i18n import _
from . import maintenance, troubleshoot
from .util import human
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import STATUS_ICON, card, css, hbox, icon, label, list_card, scrolled, vbox


class TroubleshootPage:
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
