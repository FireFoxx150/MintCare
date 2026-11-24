"""Hardware page (inventory, drives and SMART, sensors, GPU, battery, benchmarks, firmware), mixed into MintCareWindow."""

import time
from pathlib import Path

from gi.repository import GLib, Gtk

from .i18n import _
from . import bench, checks, firmware, hardware, maintenance, theme
from .util import human, q
from .widgets import BLUE, EMERALD, ORANGE, Bar, LineChart, describe, show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import GPU_HOT_C, GPU_HOT_SAMPLES, STATUS_ICON, card, hbox, icon, label, pill, scrolled, vbox


class HardwarePage:
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
