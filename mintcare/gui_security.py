"""Security page (network connections, LAN devices, download guard, login and system audit, malware scan, Lynis), mixed into MintCareWindow."""

import os
import threading
from pathlib import Path

from gi.repository import GLib, Gtk, Pango

from .i18n import _, ngettext
from . import boot, checks, guard, lan, maintenance, ports, secaudit, tweaks
from .runner import display_text, run_steps
from .widgets import describe, show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import STATUS_ICON, STATUS_WORD, card, hbox, icon, label, list_card, pill, scrolled, set_pill, vbox


class SecurityPage:
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
                GLib.idle_add(self.mal_progress.set_text, display_text(text[-160:]))

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
                self.mal_results.pack_start(self._row(display_text(os.path.basename(path)), virus, display_text(path),
                                                      "dialog-error-symbolic"), False, False, 0)
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
