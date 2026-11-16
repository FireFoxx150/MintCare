"""Backup page: folders, schedule, restic, restore, verify and settings import/export.

Mixed into MintCareWindow. Methods are moved here unchanged from gui.py.
"""

import os
import threading
import time
from pathlib import Path
from gi.repository import GLib, Gtk
from . import backup, maintenance
from .i18n import _, ngettext
from .util import human
from .widgets import describe, show_or_hide
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import STATUS_ICON, card, css, hbox, icon, label, list_card, scrolled, vbox


class BackupPage:
    def verify_backup(self):
        """Read a sample of the newest snapshot; reports whether the copies can be read."""
        from . import backup as backup_mod
        b = backup_mod.config()
        snaps = backup_mod.snapshots(b["dest"]) if b.get("dest") else []
        if not snaps:
            self.set_status(_("No backup found yet"), bad=True)
            return

        def done(res):
            checked, bad = res
            if bad:
                self.set_status(_("{0} of {1} sampled files could not be read — back up again").format(
                    len(bad), checked), bad=True)
            else:
                self.set_status(_("Verified: {0} sampled files can be read").format(checked))
        self.in_thread(_("Verifying backup…"), lambda: backup_mod.verify(snaps[0]), done)

    def compare_backup(self):
        """Compare every file in the newest snapshot with its original, by checksum."""
        from . import backup as backup_mod
        b = backup_mod.config()
        snaps = backup_mod.snapshots(b["dest"]) if b.get("dest") else []
        if not snaps:
            self.set_status(_("No backup found yet"), bad=True)
            return
        folders = [f for f in b["folders"] if os.path.isdir(f)]

        def done(res):
            if res is None:
                return
            checked, differ = res
            for path in differ[:20]:
                self.log(path)
            if differ:
                self.set_status(_("{0} of {1} files differ from the originals (changed since the backup, or damaged)")
                                .format(len(differ), checked), bad=True)
            else:
                self.set_status(_("Verified: all {0} files match the originals").format(checked))
        self.in_thread(_("Comparing backup with your files…"),
                       lambda: backup_mod.compare_with_originals(snaps[0], folders,
                                                                 b.get("excludes", backup_mod.DEFAULT_EXCLUDES)), done)

    def _build_backup(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "document-save-symbolic", _("Backup"),
            _("Copies your personal folders to another drive. Each backup is a dated snapshot; files that didn't "
              "change are hard-linked, so extra snapshots cost almost no space. Timeshift covers the system; this "
              "covers your files."),
            [self.action_button(_("Verify latest backup"), self.verify_backup,
                                icon_name="emblem-ok-symbolic"),
             self.action_button(_("Check against originals"), self.compare_backup,
                                icon_name="emblem-ok-symbolic")]), False, False, 0)
        dc = card("ecard")
        h = hbox(8)
        h.pack_start(label(_("Destination"), ("h2",)), False, False, 0)
        self.bk_dest = label("", ("crumb",), ellipsize=True)
        h.pack_start(self.bk_dest, True, True, 0)
        h.pack_end(self._btn(_("Choose drive…"), self._bk_choose_dest, icon_name="drive-harddisk-symbolic"),
                   False, False, 0)
        dc.pack_start(h, False, False, 0)
        self.bk_dest_issues = vbox(2)
        dc.pack_start(self.bk_dest_issues, False, False, 6)
        dc.pack_start(Gtk.Separator(), False, False, 6)
        fh = hbox(8)
        fh.pack_start(label(_("Folders"), ("h2",)), False, False, 0)
        self.bk_size = label("", ("faint", "mono"))
        fh.pack_start(self.bk_size, True, True, 0)
        fh.pack_end(self._btn(_("Add folder…"), self._bk_add_folder, icon_name="list-add-symbolic"), False, False, 0)
        dc.pack_start(fh, False, False, 0)
        self.bk_folders = vbox(2)
        dc.pack_start(self.bk_folders, False, False, 4)
        dc.pack_start(Gtk.Separator(), False, False, 6)
        sh = hbox(10)
        sh.pack_start(label(_("Automatic backup"), ("h2",)), False, False, 0)
        self.bk_switch = describe(Gtk.Switch(valign=Gtk.Align.CENTER), _("Automatic backup"))
        self.bk_switch.connect("notify::active", self._bk_toggle)
        sh.pack_start(self.bk_switch, False, False, 0)
        self.bk_freq = Gtk.ComboBoxText()
        describe(self.bk_freq, _("How often to back up"))
        for k, t in (("daily", _("Every day")), ("weekly", _("Every Sunday")), ("monthly", _("Monthly"))):
            self.bk_freq.append(k, t)
        self.bk_freq.connect("changed", lambda *_x: self._bk_save())
        sh.pack_start(self.bk_freq, False, False, 0)
        sh.pack_start(label(_("Keep"), ("muted",)), False, False, 0)
        self.bk_keep = Gtk.SpinButton.new_with_range(2, 60, 1)
        describe(self.bk_keep, _("Number of backups to keep"))
        self.bk_keep.connect("value-changed", lambda *_x: self._bk_save())
        sh.pack_start(self.bk_keep, False, False, 0)
        sh.pack_start(label(_("snapshots"), ("muted",)), False, False, 0)
        dc.pack_start(sh, False, False, 0)
        gh = hbox(8)
        self.bk_gfs = Gtk.CheckButton(label=_("Keep daily, weekly and monthly copies (7 days, 4 weeks, 12 months) "
                                              "instead"))
        self.bk_gfs.connect("toggled", lambda *_x: self._bk_save())
        gh.pack_start(self.bk_gfs, False, False, 0)
        dc.pack_start(gh, False, False, 0)
        self.bk_status = label("", ("muted",), wrap=True)
        dc.pack_start(self.bk_status, False, False, 6)
        rh = hbox(8)
        self.bk_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.bk_progress.set_no_show_all(True)
        rh.pack_start(self.bk_progress, True, True, 0)
        self.bk_cancel = Gtk.Button(label=_("Cancel"))
        css(self.bk_cancel, "ghost")
        self.bk_cancel.set_no_show_all(True)
        self.bk_cancel.connect("clicked", lambda *_x: self._bk_cancel_ev.set())
        rh.pack_end(self.bk_cancel, False, False, 0)
        self.bk_run = Gtk.Button()
        inner = hbox(6)
        inner.pack_start(icon("document-save-symbolic"), False, False, 0)
        inner.pack_start(Gtk.Label(label=_("Back up now")), False, False, 0)
        self.bk_run.add(inner)
        describe(self.bk_run, _("Back up now"))
        css(self.bk_run, "primary")
        self.bk_run.connect("clicked", lambda *_x: self.run_backup())
        rh.pack_end(self.bk_run, False, False, 0)
        dc.pack_start(rh, False, False, 4)
        page.pack_start(dc, False, False, 0)
        self.bk_snap_head = label(_("SNAPSHOTS"), ("kicker",))
        page.pack_start(self.bk_snap_head, False, False, 0)
        self.bk_snap_card, self.bk_snap_list = list_card()
        page.pack_start(self.bk_snap_card, False, False, 0)

        page.pack_start(self._restic_card(), False, False, 0)
        ec = card()
        eh = hbox(8)
        eh.pack_start(icon("document-send-symbolic", Gtk.IconSize.LARGE_TOOLBAR, "tile-ico"), False, False, 0)
        et = vbox(2)
        et.pack_start(label(_("Move MintCare to another PC"), ("h2",)), False, False, 0)
        et.pack_start(label(_("Save your MintCare settings, the optimisations it applied and your startup-app "
                              "choices to a file, then import them on another computer."), ("muted",), wrap=True),
                      False, False, 0)
        eh.pack_start(et, True, True, 0)
        eh.pack_end(self._btn(_("Import…"), self._import_settings), False, False, 0)
        eh.pack_end(self._btn(_("Export…"), self._export_settings), False, False, 0)
        ec.pack_start(eh, False, False, 0)
        page.pack_start(ec, False, False, 0)
        self._bk_cancel_ev = threading.Event()
        self._bk_loading = False
        return scrolled(page)

    def _restic_card(self):
        c = card("ecard")
        h = hbox(8)
        t = vbox(2)
        t.pack_start(label(_("Encrypted copy (restic)"), ("h2",)), False, False, 0)
        t.pack_start(label(_("A second copy that only your password can open: a folder on another drive, a network "
                             "share, or an rclone remote. It runs with the automatic backup."), ("muted",), wrap=True),
                     False, False, 0)
        h.pack_start(t, True, True, 0)
        self.rs_install = self._btn(_("Install restic"), self._restic_install, primary=True)
        self.rs_install.set_no_show_all(True)
        h.pack_end(self.rs_install, False, False, 0)
        c.pack_start(h, False, False, 0)
        self.rs_repo = Gtk.Entry(placeholder_text=_("Folder, or rclone:remote:path"),
                                 text=backup.config().get("restic_repo", ""))
        self.rs_pass = Gtk.Entry(visibility=False, placeholder_text=_("Password"))
        self.rs_pass.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.rs_setup_btn = self._btn(_("Set up"), self._restic_setup)
        self.rs_now_btn = self._btn(_("Encrypted backup now"), self._restic_now, primary=True)
        row = hbox(8)
        row.pack_start(self.rs_repo, True, True, 0)
        row.pack_start(self.rs_pass, True, True, 0)
        row.pack_end(self.rs_now_btn, False, False, 0)
        row.pack_end(self.rs_setup_btn, False, False, 0)
        c.pack_start(row, False, False, 8)
        self.rs_status = label("", ("muted",), wrap=True)
        c.pack_start(self.rs_status, False, False, 0)
        self._restic_state()
        return c

    def _restic_state(self):
        ready = backup.restic_ready()
        show_or_hide(self.rs_install, not ready)
        for w in (self.rs_repo, self.rs_pass, self.rs_setup_btn, self.rs_now_btn):
            w.set_sensitive(ready)
        if not ready:
            self.rs_status.set_text(_("Install restic first. It's a small program from Ubuntu's repositories."))
        elif not backup.config().get("restic_repo"):
            self.rs_status.set_text(_("Choose a folder or repository and a password, then press Set up."))

    def _restic_refresh(self):
        if backup.restic_ready() and backup.config().get("restic_repo"):
            self.bg(backup.restic_latest, self._restic_show_latest)

    def _restic_show_latest(self, when):
        self.rs_status.set_text(_("Last encrypted copy: {0}").format(when[:16].replace("T", " ")) if when
                                else _("No encrypted copy yet."))

    def _restic_install(self):
        self._root_job(_("Installing restic…"),
                       [("restic", "Install restic", "DEBIAN_FRONTEND=noninteractive apt-get install -y restic")],
                       "Backup", lambda r: self._restic_state())

    def _restic_setup(self):
        repo, pw = self.rs_repo.get_text().strip(), self.rs_pass.get_text()
        if not repo or not pw:
            self.set_status(_("Enter a folder or repository, and a password"), bad=True)
            return
        self.rs_status.set_text(_("Creating the encrypted copy…"))

        def done(res):
            ok, text = res or (False, _("Couldn't create the encrypted copy"))
            if not ok:
                self.rs_status.set_text(_("Set-up failed: {0}").format(text))
                return
            b = backup.config()
            b["restic_repo"] = repo
            backup.save_config(b)
            self.rs_pass.set_text("")
            maintenance.record("backup", _("Encrypted copy set up"))
            self.set_status(_("Keep a copy of the password somewhere safe: without it the copy can't be opened."))
            self._restic_refresh()
        self.bg(lambda: backup.restic_init(repo, pw), done)

    def _restic_now(self):
        b = backup.config()
        self.rs_status.set_text(_("Making an encrypted copy…"))

        def done(res):
            ok, text = res or (False, _("Couldn't make the encrypted copy"))
            maintenance.record("backup", _("Encrypted copy made") if ok else _("Encrypted copy failed"), ok, text)
            if ok:
                self._restic_refresh()
            else:
                self.rs_status.set_text(_("Encrypted copy failed: {0}").format(text))
        self.bg(lambda: backup.restic_backup(b), done)

    def load_backup(self):
        self._bk_loading = True
        b = backup.config()
        self.bk_cfg = b
        self.bk_dest.set_text(b["dest"] or _("(not chosen)"))
        self.bk_freq.set_active_id(b["frequency"])
        self.bk_keep.set_value(b["keep"])
        self.bk_gfs.set_active(b.get("retention") == "gfs")
        self.bk_switch.set_active(backup.timer_next() is not None)
        self._bk_loading = False
        self._clear(self.bk_dest_issues)
        for lvl, text in backup.check_destination(b["dest"]):
            r = hbox(6)
            r.pack_start(icon(STATUS_ICON[lvl][0], Gtk.IconSize.MENU, STATUS_ICON[lvl][1]), False, False, 0)
            r.pack_start(label(text, ("muted",), wrap=True), True, True, 0)
            self.bk_dest_issues.pack_start(r, False, False, 0)
        self._clear(self.bk_folders, "bk_row")
        for f in b["folders"]:
            r = hbox(8)
            r.pack_start(icon("folder-symbolic", Gtk.IconSize.MENU, "ok"), False, False, 0)
            r.pack_start(label(f.replace(str(Path.home()), "~"), ("mono", "muted"), ellipsize=True), True, True, 0)
            r.pack_end(self._btn(_("Remove"), lambda f=f: self._bk_remove_folder(f), tag="bk_row"), False, False, 0)
            self.bk_folders.pack_start(r, False, False, 0)
        self.bk_dest_issues.show_all()
        self.bk_folders.show_all()
        self.bk_size.set_text(_("measuring…"))
        self.bg(lambda: backup.estimate(b["folders"], b["excludes"]), lambda n: self.bk_size.set_text(
            _('{0} to back up (first time)').format(human(n or 0))))
        last = backup.last_backup()
        nxt = backup.timer_next()
        self.bk_status.set_text(
            (_('Last backup {0}: {1} changed file(s). ').format(time.strftime('%a %d %b %H:%M', time.localtime(last['ts'])), last['files']) if last else _("No backup yet. ")) +
            (_('Next automatic backup: {0}.').format(nxt) if nxt else _("Automatic backup is off.")))
        self._clear(self.bk_snap_list, "bk_snap")
        snaps = backup.snapshots(b["dest"]) if b["dest"] else []
        self.bk_snap_head.set_text(_('SNAPSHOTS ({0})').format(len(snaps)))
        for sp in snaps:
            self.bk_snap_list.add(self._row(sp.name.replace("_", " "), "", str(sp), "folder-symbolic", (),
                                            [self._btn(_("Open"), lambda sp=sp: self._open_uri(sp.as_uri()),
                                                       tag="bk_snap"),
                                             self._btn(_("Restore…"), lambda sp=sp: self.restore_backup(sp),
                                                       tag="bk_snap")]))
        if not snaps:
            self.bk_snap_list.add(self._row(_("No snapshots yet"), _("Press Back up now to make the first one.")))
        self.bk_snap_list.show_all()

    def restore_backup(self, snapshot):
        paths = self._pick_from(str(snapshot), _('Restore from the backup of {0}').format(snapshot.name.replace("_", " ")),
                                everything=_("Restore everything"))
        if not paths:
            return
        what = _("everything in this backup") if paths == [str(snapshot)] else \
            ngettext("{0} item", "{0} items", len(paths)).format(len(paths))
        if not self.confirm(_('Restore {0}?').format(what),
                            _("Files go back to where they were backed up from. Anything they would replace is "
                              "kept beside it as NAME.before-restore, so nothing is lost.")):
            return

        def done(results):
            results = results or []
            bad = [p for p, ok, _m in results if not ok]
            for p, ok, msg in results:
                self.log((_('Restored {0}') if ok else _('Could not restore {0}: {1}')).format(p, msg))
                if ok and msg:
                    self.log(msg)                       # rsync's notes, e.g. a symlink it skipped
            maintenance.record("backup", _('Restored {0} from the backup of {1}').format(what, snapshot.name), not bad)
            self.set_status(_('Restore finished with {0} problem(s) — see the Activity log').format(len(bad)) if bad
                            else _("Restore finished"), bad=bool(bad))
        self.in_thread(_("Restoring…"), lambda: backup.restore(snapshot, paths), done)

    def _bk_save(self):
        if self._bk_loading:
            return
        b = backup.config()
        b["frequency"] = self.bk_freq.get_active_id() or "weekly"
        b["keep"] = int(self.bk_keep.get_value())
        b["retention"] = "gfs" if self.bk_gfs.get_active() else "last"
        backup.save_config(b)
        if self.bk_switch.get_active():
            try:
                backup.install_timer(b["frequency"])
            except Exception as e:
                self.log(_('Backup timer: {0}').format(e))

    def _bk_toggle(self, sw, _p):
        if self._bk_loading:
            return
        b = backup.config()
        if sw.get_active():
            if not b["dest"]:
                self.set_status(_("Choose a destination first"), bad=True)
                self._bk_loading = True
                sw.set_active(False)
                self._bk_loading = False
                return
            backup.install_timer(b["frequency"])
            maintenance.record("backup", _("Automatic backup turned on"))
        else:
            backup.remove_timer()
            maintenance.record("backup", _("Automatic backup turned off"))
        self.load_backup()

    def _pick_folder(self, title, start=None):
        d = Gtk.FileChooserDialog(title=title, transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Choose"), Gtk.ResponseType.OK)
        d.set_current_folder(start or "/mnt" if os.path.isdir("/mnt") else str(Path.home()))
        resp, path = d.run(), d.get_filename()
        d.destroy()
        return path if resp == Gtk.ResponseType.OK else None

    def _bk_choose_dest(self):
        path = self._pick_folder(_("Choose where to keep backups"), "/mnt")
        if path:
            b = backup.config()
            b["dest"] = path
            backup.save_config(b)
            self.load_backup()

    def _bk_add_folder(self):
        path = self._pick_folder(_("Add a folder to back up"), str(Path.home()))
        if path:
            b = backup.config()
            if path not in b["folders"]:
                b["folders"].append(path)
            backup.save_config(b)
            self.load_backup()

    def _bk_remove_folder(self, f):
        b = backup.config()
        b["folders"] = [x for x in b["folders"] if x != f]
        backup.save_config(b)
        self.load_backup()

    def run_backup(self):
        if getattr(self, "_bk_running", False):
            return
        self._bk_running = True
        self._bk_cancel_ev.clear()
        self.bk_run.set_sensitive(False)
        self.bk_progress.show()
        self.bk_cancel.show()

        def prog(pct, text):
            GLib.idle_add(lambda: (self.bk_progress.set_fraction(pct / 100), self.bk_progress.set_text(text),
                                   self.bk_progress.set_show_text(True), False)[-1])

        def done(r):
            self._bk_running = False
            self.bk_run.set_sensitive(True)
            self.bk_progress.hide()
            self.bk_cancel.hide()
            r = r or {"ok": False, "error": "failed"}
            text = (_('Backup done: {0} changed file(s)').format(r['files']) if r["ok"]
                    else _('Backup: {0}').format(r['error']))
            self.set_status(f"{text}. {r['warning']}" if r.get("warning") else text,
                            bad=not r["ok"] or bool(r.get("warning")))
            self.load_backup()
        self.bg(lambda: backup.run_backup(None, prog, self._bk_cancel_ev.is_set), done)

    def _export_settings(self):
        d = Gtk.FileChooserDialog(title=_("Export MintCare settings"), transient_for=self,
                                  action=Gtk.FileChooserAction.SAVE)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Export"), Gtk.ResponseType.OK)
        d.set_do_overwrite_confirmation(True)
        d.set_current_name("mintcare-settings.json")
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path:
            data = backup.export_settings(path)
            self.set_status(_('Exported settings and {0} optimisation(s)').format(len(data['tweaks'])))

    def _import_settings(self):
        d = Gtk.FileChooserDialog(title=_("Import MintCare settings"), transient_for=self,
                                  action=Gtk.FileChooserAction.OPEN)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Import"), Gtk.ResponseType.OK)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return
        try:
            ids = backup.import_settings(path)
        except (OSError, ValueError) as e:
            self.set_status(_('Import failed: {0}').format(e), bad=True)
            return
        self.prefs = maintenance.load_settings()
        self.apply_theme(self.prefs.get("theme", "dark"))
        self.apply_level(self.prefs.get("level", "intermediate"))
        for i in ids:
            sw = self.switches.get(i)
            if sw and sw.get_sensitive() and not sw.get_active():
                sw.set_active(True)
        self.goto("optimise")
        self.set_status(_('Imported settings; {0} optimisation(s) selected — press Apply changes').format(len(ids)))
