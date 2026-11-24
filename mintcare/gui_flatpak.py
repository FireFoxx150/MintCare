"""Flatpak page (installed Flatpak apps, updates, removals), mixed into MintCareWindow."""

from gi.repository import GLib, Gtk

from .i18n import N_, _
from . import flatpaks, maintenance
from .util import human
from .widgets import describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, hbox, label, list_card, pill, scrolled, vbox


class FlatpakPage:
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
