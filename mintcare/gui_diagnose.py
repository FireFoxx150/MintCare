"""Diagnose page (health checks and the report), mixed into MintCareWindow."""

import shutil
import subprocess

from gi.repository import GLib, Gtk, Gdk, Pango

from .i18n import _, ngettext
from . import checks, maintenance, tweaks
from .widgets import describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import css, hbox, icon, label, list_card, pill, scrolled, STATUS_ICON, STATUS_WORD, vbox


class DiagnosePage:
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
