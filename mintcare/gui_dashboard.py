"""Dashboard page (live gauges, tiles, tips), mixed into MintCareWindow."""

import os
import platform
import shutil
import socket
import time

from gi.repository import GLib, Gtk

from .i18n import _, ngettext
from . import checks, hardware, maintenance, theme, tweaks
from .util import human, read, sysctl
from .widgets import BLUE, describe, EMERALD, LineChart, ORANGE, RingGauge, VIOLET
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, fmt_uptime, hbox, icon, label, pill, rate, scrolled, set_pill, STATUS_ICON, TIPS, vbox


class DashboardPage:
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
