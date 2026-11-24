"""Software and updates page (MintCare updates, package sources), mixed into MintCareWindow."""

import os
import subprocess
import time

from gi.repository import Gio, GLib, Gtk, Pango

from .i18n import _
from . import maintenance, selfupdate, sources, __version__
from .runner import run_steps
from .widgets import show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, hbox, icon, label, list_card, pill, scrolled, STATUS_ICON, vbox


class SoftwarePage:
    def _build_software(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "software-update-available-symbolic", _("Software & updates"),
            _("MintCare's own updates and the health of the software sources apt installs from."),
            [self.action_button(_("Open Software Sources"), lambda: self._launch("mintsources"),
                                icon_name="preferences-system-symbolic"),
             self.action_button(_("Test sources"), self.test_sources, primary=True,
                                icon_name="emblem-synchronizing-symbolic")]), False, False, 0)
        mc = card("ecard")
        h = hbox(10)
        h.pack_start(icon("software-update-available-symbolic", Gtk.IconSize.BUTTON, "ok"), False, False, 0)
        h.pack_start(label(_("MintCare updates"), ("h2",)), True, True, 0)
        self.sw_update_btn = self.action_button(_("Update now"), self.install_mintcare_update, primary=True)
        self.sw_update_btn.set_no_show_all(True)
        h.pack_end(self.sw_update_btn, False, False, 0)
        h.pack_end(self.action_button(_("Release notes"), lambda: self._open_uri(
            (self.update_info or {}).get("url") or selfupdate.RELEASES_PAGE)), False, False, 0)
        h.pack_end(self.action_button(_("Check now"), lambda: self.check_mintcare_update(force=True),
                                      icon_name="view-refresh-symbolic"), False, False, 0)
        mc.pack_start(h, False, False, 0)
        self.sw_update_lbl = label("", ("muted",), wrap=True)
        mc.pack_start(self.sw_update_lbl, False, False, 8)
        self.sw_notes = label("", ("faint",), wrap=True)
        mc.pack_start(self.sw_notes, False, False, 0)
        page.pack_start(mc, False, False, 0)

        self.src_head = label(_("SOFTWARE SOURCES"), ("kicker",))
        page.pack_start(self.src_head, False, False, 0)
        self.src_note = label(_("Official Mint and Ubuntu sources can't be switched off here — use Software "
                                "Sources for mirrors. Test sources runs apt-get update and records any errors."),
                              ("faint",), wrap=True)
        page.pack_start(self.src_note, False, False, 0)
        self.src_card, self.src_list = list_card()
        page.pack_start(self.src_card, False, False, 0)
        return scrolled(page)

    def _render_mintcare_card(self):
        info = self.update_info
        if not info:
            self.sw_update_lbl.set_text(_('You have MintCare {0}. ').format(__version__) +
                                        _("Press Check now to look for a newer version on GitHub."))
            self.sw_notes.set_text("")
            self.sw_update_btn.hide()
            return
        newer = selfupdate.is_newer(info["version"])
        when = (info.get("published") or "")[:10]
        checked = time.strftime("%H:%M", time.localtime(info.get("checked", time.time())))
        self.sw_update_lbl.set_markup(
            (_('<b>MintCare {0} is available</b> (released {1}). You have {2}.').format(GLib.markup_escape_text(info['version']), when, __version__) if newer else
             _('You have the latest version, <b>{0}</b>.').format(__version__)) + _('  <small>Checked at {0}.</small>').format(checked))
        notes = info.get("notes", "").strip()
        self.sw_notes.set_text(notes[:600] if newer else "")
        show_or_hide(self.sw_update_btn, newer and selfupdate.installed_from_package())

    def load_software(self):
        self._render_mintcare_card()
        if self.update_info is None:
            self.check_mintcare_update()
        self.bg(sources.analyse, self._render_sources)

    def _render_sources(self, srcs):
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "src_row", False)]
        for ch in self.src_list.get_children():
            ch.destroy()
        srcs = srcs or []
        test = sources.last_test()
        bad = sum(1 for x in srcs if x.enabled and x.health in (sources.WARN, sources.FAIL))
        tested = (_(" · last tested {0}").format(time.strftime("%d %b %H:%M", time.localtime(test["ts"])))) if test else ""
        self.src_head.set_text(_('SOFTWARE SOURCES ({0}, {1} with problems){2}').format(len(srcs), bad, tested))
        icons = {"Linux Mint": "computer-symbolic", "Ubuntu": "system-software-install-symbolic",
                 "PPA": "package-x-generic-symbolic", "Third-party": "network-server-symbolic"}
        order = {"Linux Mint": 0, "Ubuntu": 1, "PPA": 2, "Third-party": 3}
        for x in sorted(srcs, key=lambda x: (not x.enabled, order[x.kind], x.label)):
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            box.pack_start(icon(icons[x.kind], Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
            t = vbox(2)
            th = hbox(8)
            th.pack_start(label(x.label, ("h3",), ellipsize=True), False, False, 0)
            th.pack_start(pill(x.kind, "blue" if x.kind in ("Linux Mint", "Ubuntu") else "dim"), False, False, 0)
            th.pack_start(pill(x.suite, "dim"), False, False, 0)
            hl = x.health
            word = {"ok": "working", "info": "note", "warn": "check", "fail": "broken", "disabled": "disabled"}[hl]
            th.pack_start(pill(word, {"disabled": "dim"}.get(hl, hl)), False, False, 0)
            t.pack_start(th, False, False, 0)
            sub = f"{x.type} {x.uri} {x.suite} {' '.join(x.comps)}  ·  {os.path.basename(x.file)}"
            sl = label(sub, ("faint", "mono"), ellipsize=True)
            sl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            t.pack_start(sl, False, False, 0)
            for lvl, text in x.problems:
                r = hbox(6)
                r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
                r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
                t.pack_start(r, False, False, 0)
            box.pack_start(t, True, True, 0)
            if x.kind in ("PPA", "Third-party") and (x.enabled or x.mintcare_disabled):
                b = self.action_button(_("Disable") if x.enabled else _("Enable"),
                                       lambda src=x: self.toggle_source(src))
                b.src_row = True
                box.pack_end(b, False, False, 0)
            row.add(box)
            self.src_list.add(row)
        self.src_list.show_all()

    def toggle_source(self, src):
        enable = not src.enabled
        if not self.confirm((_('Enable {0}?') if enable else _('Disable {0}?')).format(src.label),
                            (_("Packages from this source will get updates again.") if enable else
                             _("apt will stop installing and updating packages from this source. Installed "
                             "packages stay installed. You can enable it again here.")) +
                            _('\n\n<tt>{0}</tt> is backed up to /var/lib/mintcare first.').format(GLib.markup_escape_text(os.path.basename(src.file)))):
            return
        steps = [("source", f"{'Enable' if enable else 'Disable'} source {src.label}",
                  sources.toggle_script(src, enable))]

        def done(results):
            self.notify_done(results or {}, _("Software source"))
            self.load_software()
            self.run_diagnose()
        self.in_thread(_("Updating software sources…"), lambda: self.execute(steps, [], "Sources"), done)

    def test_sources(self):
        def work():
            self.log(_("\n━━━ Testing software sources (apt-get update) ━━━"))
            lines = []

            def collect(line):
                lines.append(line)
                self.log(line)
            res = run_steps([sources.test_step()], root=True, on_line=collect)
            data = sources.save_test("\n".join(lines)) if res.get("sources-test") == 0 else None
            return res, data

        def done(out):
            res, data = out or ({}, None)
            if data is None:
                self.set_status(_("Source test was cancelled or failed"), bad=True)
                return
            n = len(data["errors"])
            self.set_status(_('Tested sources: {0} error(s)').format(n) if n else _("All software sources downloaded fine"),
                            bad=bool(n))
            maintenance.record("other", _('Tested software sources: {0} error(s)').format(n), n == 0)
            self.load_software()
            self.run_diagnose()
        self.in_thread(_("Testing software sources…"), work, done)

    def _launch(self, cmd):
        try:
            subprocess.Popen([cmd], start_new_session=True)
        except OSError as e:
            self.set_status(_('Could not start {0}: {1}').format(cmd, e), bad=True)

    def _open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as e:
            self.log(_('Could not open {0}: {1}').format(uri, e.message))
