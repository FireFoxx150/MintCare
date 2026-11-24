"""Optimise page (tweaks, one-click profiles, undo), mixed into MintCareWindow."""

from gi.repository import GLib, Gtk

from .i18n import _
from . import tweaks
from .widgets import Bar, describe, EMERALD, show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import css, hbox, icon, label, list_card, pill, scrolled, set_pill, vbox


class OptimisePage:
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
