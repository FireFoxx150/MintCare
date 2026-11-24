"""Packages page (updates and removals, install history, kernels, mirrors, Mint upgrade), mixed into MintCareWindow."""

import shutil
from pathlib import Path

from gi.repository import GLib, Gtk

from .i18n import _
from . import packages
from .util import human
from .widgets import describe, show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import STATUS_ICON, card, hbox, icon, label, list_card, scrolled, vbox


class PackagesPage:
    def _build_packages(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "package-x-generic-symbolic", _("Packages"),
            _("What's installed and how much space it takes, what you changed and when (with undo), "
              "kernels, the fastest download mirrors and Mint upgrades.")), False, False, 0)
        # installed
        inst = vbox(10)
        bar = hbox(8)
        self.pkg_mode = Gtk.ComboBoxText()
        describe(self.pkg_mode, _("Which packages to show"))
        for k, t in (("largest", _("Largest packages")), ("mine", _("Installed by you")), ("held", _("Held back"))):
            self.pkg_mode.append(k, t)
        self.pkg_mode.set_active_id("largest")
        self.pkg_mode.connect("changed", lambda *_x: self._render_packages())
        bar.pack_start(self.pkg_mode, False, False, 0)
        self.pkg_filter = Gtk.SearchEntry(placeholder_text=_("Filter…"))
        self.pkg_filter.connect("search-changed", lambda *_x: self._render_packages())
        bar.pack_start(self.pkg_filter, True, True, 0)
        self.pkg_summary = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        bar.pack_end(self.pkg_summary, False, False, 0)
        inst.pack_start(bar, False, False, 0)
        self.pkg_card, self.pkg_list = list_card()
        inst.pack_start(self.pkg_card, False, False, 0)
        # history
        hist = vbox(10)
        hist.pack_start(label(_("Undo removes the packages that change installed (dependencies that are no longer "
                                "needed go too). You'll see exactly what will be removed before anything happens."),
                              ("faint",), wrap=True), False, False, 0)
        self.hist_pkg_card, self.hist_pkg_list = list_card()
        hist.pack_start(self.hist_pkg_card, False, False, 0)
        # kernels
        kern = vbox(10)
        kern.pack_start(label(_("Keep the running kernel and the newest one; older kernels are only a fallback. "
                                "Removing one frees space in /boot."), ("faint",), wrap=True), False, False, 0)
        self.kern_card, self.kern_list = list_card()
        kern.pack_start(self.kern_card, False, False, 0)
        # mirrors
        mir = vbox(10)
        mc = card("ecard")
        mh = hbox(8)
        self.mir_status = label(_("Find the fastest Linux Mint and Ubuntu mirrors near you."), ("muted",), wrap=True)
        mh.pack_start(self.mir_status, True, True, 0)
        self.mir_restore_btn = self._btn(_("Restore original"), self.restore_mirrors)
        self.mir_restore_btn.set_no_show_all(True)
        mh.pack_end(self.mir_restore_btn, False, False, 0)
        self.mir_use_btn = self._btn(_("Use the fastest"), self.use_fastest_mirrors, primary=True)
        self.mir_use_btn.set_no_show_all(True)
        mh.pack_end(self.mir_use_btn, False, False, 0)
        mh.pack_end(self._btn(_("Test mirrors"), self.test_mirrors, icon_name="network-transmit-receive-symbolic"),
                    False, False, 0)
        mc.pack_start(mh, False, False, 0)
        self.mir_progress = Gtk.ProgressBar()
        self.mir_progress.set_no_show_all(True)
        mc.pack_start(self.mir_progress, False, False, 6)
        mir.pack_start(mc, False, False, 0)
        self.mir_card, self.mir_list = list_card()
        mir.pack_start(self.mir_card, False, False, 0)
        # upgrade
        up = vbox(10)
        uc = card("ecard")
        uh = hbox(8)
        rel, code = packages.mint_release()
        self.upg_lbl = label(_("You're on Linux Mint {0} “{1}”.").format(rel, code.capitalize()), ("h2",), wrap=True)
        uh.pack_start(self.upg_lbl, True, True, 0)
        uh.pack_end(self._btn(_("Check for a new release"), self.check_mint_upgrade,
                              icon_name="software-update-available-symbolic"), False, False, 0)
        uc.pack_start(uh, False, False, 0)
        self.upg_sub = label(_("Point releases (22.x) arrive through Update Manager › Edit › Upgrade; a new major "
                               "version uses Mint's upgrade tool."), ("muted",), wrap=True)
        uc.pack_start(self.upg_sub, False, False, 6)
        self.upg_box = vbox(4)
        uc.pack_start(self.upg_box, False, False, 0)
        up.pack_start(uc, False, False, 0)
        self.pkg_tabs = self._tabs(page, [("installed", _("Installed"), inst), ("history", _("History"), hist),
                                          ("kernels", _("Kernels"), kern), ("mirrors", _("Mirrors"), mir),
                                          ("upgrade", _("Mint upgrade"), up)])
        self.pkg_data = None
        return scrolled(page)

    def load_packages(self):
        def work():
            pk = packages.installed()
            h = packages.history()
            held = packages.held()
            return {"pk": pk, "hist": h, "mine": packages.user_installed(pk, h), "held": held,
                    "held_why": {n: packages.hold_explanation(n) for n in held[:80]},
                    "kernels": packages.kernels(pk)}

        def done(d):
            if not d:
                return
            self.pkg_data = d
            self._render_packages()
            self._render_pkg_history()
            self._render_kernels()
        self.bg(work, done)

    def _render_packages(self):
        d = self.pkg_data
        if not d:
            return
        self._clear(self.pkg_list, "pkg_row")
        mode = self.pkg_mode.get_active_id()
        flt = self.pkg_filter.get_text().strip().lower()
        pk = d["pk"]
        if mode == "largest":
            names = [n for _s, n, _v in packages.largest(pk, 400)]
        elif mode == "mine":
            names = sorted(d["mine"], key=lambda n: -pk[n]["size"])
        else:
            names = [n for n in d["held"] if n in pk]
        if flt:
            names = [n for n in names if flt in n.lower() or flt in pk[n]["desc"].lower()]
        total = sum(pk[n]["size"] for n in names)
        self.pkg_summary.set_text(_('{0} packages · {1}  (of {2:,} installed)').format(len(names), human(total), len(pk)))
        for n in names[:80]:
            v = pk[n]
            prot = packages.protected(n, v)
            btns = []
            if mode == "held":
                btns.append(self._btn(_("Release hold"), lambda n=n: self._root_job(
                    _("Releasing hold…"), [packages.unhold_step(n)], "Packages", lambda r: self.load_packages()),
                    tag="pkg_row"))
            elif not prot:
                btns.append(self._btn(_("Remove…"), lambda n=n: self.remove_packages([n]), tag="pkg_row"))
            pills = [(_("system"), "dim")] if prot else []
            desc = self.pkg_data.get("held_why", {}).get(n, v["desc"]) if mode == "held" else v["desc"]
            self.pkg_list.add(self._row(n, desc, f"{v['version']} · {v['section']}", None, pills, btns,
                                        human(v["size"])))
        if not names:
            self.pkg_list.add(self._row(_("Nothing to show"), _("No packages match.")))
        self.pkg_list.show_all()

    def remove_packages(self, names, heading=_("Remove packages")):
        removed, ok = packages.simulate_remove(names)
        if not ok or not removed:
            self.set_status(_("apt can't remove that cleanly — see the Activity log"), bad=True)
            self.log(_('apt-get -s remove {0} failed or would remove nothing').format(' '.join(names)))
            return
        extra = [r for r in removed if r not in names]
        body = (_("This removes:") + "\n<tt>" + GLib.markup_escape_text(", ".join(removed[:40])) +
                ("…" if len(removed) > 40 else "") + "</tt>")
        if extra:
            body += "\n\n<b>" + _('{0} other package(s) depend on it and go too.').format(len(extra)) + "</b>"
        if not self.confirm(_('Remove {0} package(s)?').format(len(removed)), body):
            return
        self._root_job(_("Removing packages…"), [packages.remove_step(names)], "Packages",
                       lambda r: (self.load_packages(), self.run_clean_scan()), snapshot_reason=_("removing packages"))

    def _render_pkg_history(self):
        d = self.pkg_data
        self._clear(self.hist_pkg_list, "hist_row")
        for e in d["hist"][:80]:
            parts = [f"{len(e[k])} {k}" for k in ("install", "upgrade", "remove", "purge") if e[k]]
            who = "Software Manager" if "aptkit" in e["cmd"] or "packagekit" in e["cmd"] else \
                  ("Update Manager" if "mintupdate" in e["cmd"] or "unattended" in e["cmd"] else "apt")
            cand = packages.undo_candidates(e, d["pk"])
            btns = [self._btn(_("Undo…"), lambda c=cand: self.remove_packages(c), tag="hist_row")] if cand else []
            names = ", ".join(p["pkg"] for p in (e["install"] or e["remove"] or e["upgrade"])[:6])
            self.hist_pkg_list.add(self._row(f"{e['date']} — {', '.join(parts) or _('no changes')}", names,
                                             e["cmd"][:200], None, [(who, "dim")], btns))
        self.hist_pkg_list.show_all()

    def _render_kernels(self):
        self._clear(self.kern_list, "kern_row")
        for k in self.pkg_data["kernels"]:
            pills = [(_("running"), "ok")] if k["running"] else []
            if k["newest"]:
                pills.append((_("newest"), "info"))
            btns = [] if k["running"] or k["newest"] else [
                self._btn(_("Remove"), lambda k=k: self._remove_kernel(k), tag="kern_row")]
            self.kern_list.add(self._row(_('Linux {0}').format(k['version']), ", ".join(k["packages"][:4]), "", None, pills,
                                         btns, human(k["size"])))
        self.kern_list.show_all()

    def _remove_kernel(self, k):
        if self.confirm(_('Remove kernel {0}?').format(k['version']),
                        _("You keep the running and newest kernels to boot from. The boot menu is updated.")):
            self._root_job(_("Removing kernel…"), [packages.kernel_remove_step(k)], "Packages",
                           lambda r: self.load_packages())

    def test_mirrors(self):
        self.mir_progress.show()
        self.mir_status.set_text(_("Testing mirrors…"))

        def prog(done, total):
            GLib.idle_add(lambda: self.mir_progress.set_fraction(done / max(total, 1)) or False)

        def done(r):
            self.mir_progress.hide()
            self.mirror_results = r
            self._clear(self.mir_list)
            if not r:
                return
            cur_m, cur_u = packages.current_mirrors()
            for kind, title in (("mint", "Linux Mint"), ("ubuntu", "Ubuntu")):
                self.mir_list.add(self._row(_('{0} mirrors').format(title), "", "", None))
                for m in r[kind][:6]:
                    sp = f"{m['speed'] / 1e6:.1f} MB/s" if m["speed"] else _("no answer")
                    pills = []
                    if m["url"] in (cur_m, cur_u):
                        pills.append((_("current"), "info"))
                    if m["stale"]:
                        pills.append((_("out of date"), "warn"))
                    self.mir_list.add(self._row(m["url"], "", "", None, pills, (), sp))
            best = [next((m for m in r[k] if m["speed"] and not m["stale"]), None) for k in ("mint", "ubuntu")]
            gain = [b for b, cur in zip(best, (cur_m, cur_u)) if b and b["url"] != cur]
            self.mir_status.set_text(_('Tested mirrors in {0}. ').format(r['country'] or _('your area')) +
                                     (_("A faster mirror is available.") if gain else _("You're already on the fastest.")))
            show_or_hide(self.mir_use_btn, bool(gain))
            self.mir_list.show_all()
        self.bg(lambda: packages.test_mirrors(prog), done)
        show_or_hide(self.mir_restore_btn, Path("/var/lib/mintcare/official-package-repositories.list.orig").exists())

    def use_fastest_mirrors(self):
        r = getattr(self, "mirror_results", None)
        if not r:
            return
        pick = [next((m["url"] for m in r[k] if m["speed"] and not m["stale"]), None) for k in ("mint", "ubuntu")]
        if not self.confirm(_("Switch to the fastest mirrors?"),
                            _('Linux Mint: <tt>{0}</tt>\nUbuntu: <tt>{1}</tt>\n\nThe original list is backed up and can be restored here.').format(GLib.markup_escape_text(pick[0] or '-'), GLib.markup_escape_text(pick[1] or '-'))):
            return
        self._root_job(_("Switching mirrors…"), [packages.switch_mirrors_step(*pick)], "Sources",
                       lambda res: (self.test_mirrors(), self.load_software() if self._sw_loaded else None))

    def restore_mirrors(self):
        self._root_job(_("Restoring mirrors…"), [packages.restore_mirrors_step()], "Sources",
                       lambda res: self.test_mirrors())

    def check_mint_upgrade(self):
        self.upg_sub.set_text(_("Checking…"))

        def done(res):
            new, ready = res or (None, [])
            self._clear(self.upg_box, "upg_row")
            if new:
                self.upg_sub.set_markup(_('<b>Linux Mint {0} “{1}” is available.</b> Check the list below, then start the upgrade tool.').format(GLib.markup_escape_text(new[1]), GLib.markup_escape_text(new[0].capitalize())))
            else:
                self.upg_sub.set_text(_("No newer Linux Mint release yet. Point releases also appear in Update "
                                        "Manager › Edit › Upgrade."))
            for lvl, text in ready:
                r = hbox(6)
                r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
                r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
                self.upg_box.pack_start(r, False, False, 0)
            if new:
                b = self._btn(_("Start the upgrade tool"), self._start_mintupgrade, primary=True, tag="upg_row")
                self.upg_box.pack_start(b, False, False, 6)
            self.upg_box.show_all()
        self.bg(lambda: (packages.upgrade_available(), packages.upgrade_readiness()), done)

    def _start_mintupgrade(self):
        if shutil.which("mintupgrade"):
            self._launch("mintupgrade")
            return
        self._root_job(_("Installing Mint's upgrade tool…"),
                       [("mintupgrade", _("Install mintupgrade"), "DEBIAN_FRONTEND=noninteractive apt-get install -y mintupgrade")],
                       "Packages", lambda r: self._launch("mintupgrade") if r.get("mintupgrade") == 0 else None)
