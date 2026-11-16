"""Disk-space page (folders, duplicates, old downloads, Timeshift snapshots), mixed into MintCareWindow."""

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import Gio, GLib, Gtk, Pango

from .i18n import _, ngettext
from . import maintenance, space
from .util import human
from .widgets import Treemap, describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import _thumbnail, card, css, hbox, icon, label, list_card, pill, scrolled, vbox


class SpacePage:
    def _build_space(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "drive-multidisk-symbolic", _("Disk space"),
            _("Find what's using your space: big folders and files, duplicate copies and old system "
            "snapshots. Files go to the Trash, so nothing is lost until you empty it.")), False, False, 0)
        self.space_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        sw = Gtk.StackSwitcher(stack=self.space_stack, halign=Gtk.Align.START)
        page.pack_start(sw, False, False, 0)
        self.space_stack.add_titled(self._build_folders_tab(), "folders", _("Folders & large files"))
        self.space_stack.add_titled(self._build_dups_tab(), "dups", _("Duplicates"))
        self.space_stack.add_titled(self._build_snaps_tab(), "snaps", _("Timeshift snapshots"))
        self.space_stack.add_titled(self._build_olddl_tab(), "olddl", _("Old files"))
        self.space_stack.connect("notify::visible-child-name",
                                 lambda st, _p: st.get_visible_child_name() == "olddl" and self.load_old_downloads())
        page.pack_start(self.space_stack, False, False, 0)
        return scrolled(page)

    # -- folders

    def _build_folders_tab(self):
        box = vbox(12)
        ctl = hbox(8)
        ctl.pack_start(self.action_button(_("Home folder"), lambda: self.scan_folders(space.HOME),
                                          icon_name="user-home-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button(_("Whole system"), lambda: self.scan_folders("/"),
                                          icon_name="drive-harddisk-symbolic"), False, False, 0)
        ctl.pack_start(self.action_button(_("Choose folder…"), self._choose_scan_folder,
                                          icon_name="folder-symbolic"), False, False, 0)
        self.space_spinner = Gtk.Spinner()
        ctl.pack_end(self.space_spinner, False, False, 0)
        self.space_status = label("", ("faint", "mono"), xalign=1.0, ellipsize=True)
        ctl.pack_end(self.space_status, True, True, 0)
        box.pack_start(ctl, False, False, 0)

        tc = card()
        nav = hbox(8)
        self.up_btn = self.action_button(_("Up"), self._tree_up, icon_name="go-up-symbolic")
        nav.pack_start(self.up_btn, False, False, 0)
        self.crumb = label("", ("crumb",), ellipsize=True)
        self.crumb.set_ellipsize(Pango.EllipsizeMode.START)
        nav.pack_start(self.crumb, True, True, 0)
        self.crumb_size = label("", ("stat-val",))
        nav.pack_end(self.crumb_size, False, False, 0)
        tc.pack_start(nav, False, False, 0)
        self.treemap = Treemap(self._treemap_activate, height=320)
        tc.pack_start(self.treemap, False, False, 10)
        tc.pack_start(label(_("Click a folder to look inside it. Hover for details."), ("faint",)), False, False, 0)
        box.pack_start(tc, False, False, 0)

        bigh = hbox(8)
        self.big_head = label(_("LARGEST FILES"), ("kicker",))
        bigh.pack_start(self.big_head, True, True, 0)
        self.big_type = Gtk.ComboBoxText()
        describe(self.big_type, _("File type"))
        for kind, text in (("all", _("All types")), ("video", _("Videos")), ("image", _("Pictures")),
                           ("audio", _("Music")), ("archive", _("Archives")), ("disk", _("Disk images")),
                           ("other", _("Other"))):
            self.big_type.append(kind, text)
        self.big_type.set_active_id("all")
        self.big_type.connect("changed", lambda *_x: self._render_big())
        bigh.pack_end(self.big_type, False, False, 0)
        box.pack_start(bigh, False, False, 0)
        self.big_card, self.big_list = list_card()
        box.pack_start(self.big_card, False, False, 0)
        self.space_tree = self.space_node = None
        self.space_big = []
        return box

    def _choose_scan_folder(self):
        d = Gtk.FileChooserDialog(title=_("Choose a folder to analyse"), transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Analyse"), Gtk.ResponseType.OK)
        d.set_current_folder(space.HOME)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path:
            self.scan_folders(path)

    def scan_folders(self, root):
        self._space_root = root
        self.space_spinner.start()
        self.space_status.set_text(_('Scanning {0} …').format(root))
        self.treemap.set_items([])

        def progress(n, path):
            GLib.idle_add(lambda: self.space_status.set_text(_('{0:,} files · {1}').format(n, path[-60:])) and False)

        def done(res):
            self.space_spinner.stop()
            if not res:
                self.space_status.set_text(_("Scan failed"))
                return
            tree, big = res
            self.space_tree, self.space_node, self.space_big = tree, tree, big
            note = _(" (unreadable system folders skipped)") if root == "/" else ""
            self.space_status.set_text(_('{0:,} files · {1}{2}').format(tree.files, human(tree.size), note))
            self._render_tree()
        self.bg(lambda: space.scan_tree(root, progress), done)

    def _render_tree(self):
        node = self.space_node
        if not node:
            return
        self.crumb.set_text(node.path)
        self.crumb_size.set_text(human(node.size))
        self.up_btn.set_sensitive(node is not self.space_tree)
        items, shown = [], 0
        for c in node.children[:28]:
            pct = 100 * c.size / node.size if node.size else 0
            kind = _('{0:,} files').format(c.files) if c.is_dir else _("file")
            items.append((c.size, c.name + ("/" if c.is_dir else ""), f"{human(c.size)} · {pct:.0f}% · {kind}", c))
            shown += c.size
        rest = node.size - shown
        if rest > node.size * 0.005:
            items.append((rest, _("(smaller items)"), human(rest), None))
        self.treemap.set_items(items)
        self._render_big()

    def _treemap_activate(self, node):
        if node is None:
            return
        if node.is_dir and node.children:
            self.space_node = node
            self._render_tree()
        elif not node.is_dir:
            self.show_in_folder(node.path)

    def _tree_up(self):
        if not self.space_tree or self.space_node is self.space_tree:
            return
        parent = os.path.dirname(self.space_node.path)
        self.space_node = space.find(self.space_tree, parent) or self.space_tree
        self._render_tree()

    def _render_big(self):
        self._action_buttons = [b for b in self._action_buttons if not getattr(b, "big_row", False)]
        for ch in self.big_list.get_children():
            ch.destroy()
        base = self.space_node.path.rstrip("/") + "/"
        want = self.big_type.get_active_id() or "all"
        rows = [b for b in self.space_big if b[1].startswith(base) and os.path.exists(b[1])
                and (want == "all" or space.file_kind(b[1]) == want)][:25]
        self.big_head.set_text(_('LARGEST FILES IN {0} ({1})').format(self.space_node.name.upper(), len(rows)))
        if not rows:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("No files over 50 MB here."), ("muted",)))
            self.big_list.add(r)
        for size, path, mtime in rows:
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            ctype = Gio.content_type_guess(path, None)[0]
            box.pack_start(css(Gtk.Image.new_from_gicon(Gio.content_type_get_symbolic_icon(ctype),
                                                         Gtk.IconSize.LARGE_TOOLBAR), "tile-ico"), False, False, 0)
            t = vbox(2)
            t.pack_start(label(os.path.basename(path), ("h3",), ellipsize=True), False, False, 0)
            pl = label(os.path.dirname(path), ("faint", "mono"), ellipsize=True)
            pl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            t.pack_start(pl, False, False, 0)
            t.pack_start(label(_('Modified {0} · {1}').format(time.strftime('%Y-%m-%d', time.localtime(mtime)), Gio.content_type_get_description(ctype)), ("muted",)), False, False, 0)
            box.pack_start(t, True, True, 0)
            b2 = self.action_button(_("Trash"), lambda p=path, sz=size: self._trash_big(p, sz),
                                    icon_name="user-trash-symbolic")
            b1 = self.action_button(_("Show"), lambda p=path: self.show_in_folder(p), icon_name="folder-symbolic")
            b1.big_row = b2.big_row = True
            box.pack_end(b2, False, False, 0)
            box.pack_end(b1, False, False, 0)
            box.pack_end(label(human(size), ("stat-val",)), False, False, 8)
            row.add(box)
            self.big_list.add(row)
        self.big_list.show_all()

    def _trash_big(self, path, size):
        if not self.confirm(_('Move to Trash?').format(),
                            _('<tt>{0}</tt>\n\n{1} — you can restore it from the Trash until you empty it.').format(GLib.markup_escape_text(path), human(size))):
            return
        try:
            space.trash(path)
        except GLib.Error as e:
            self.log(_('Could not trash {0}: {1}').format(path, e.message))
            self.set_status(_("Could not move to Trash"), bad=True)
            return
        self.log(_('Moved to Trash: {0} ({1})').format(path, human(size)))
        maintenance.record("trash", f"Moved {os.path.basename(path)} to the Trash ({human(size)})", True, path)
        self.set_status(_('Moved {0} to the Trash').format(os.path.basename(path)))
        self.space_big = [b for b in self.space_big if b[1] != path]
        n = self.space_tree
        while n:                                  # shrink the cached sizes along the path
            n.size -= size
            n.children = [c for c in n.children if c.path != path]
            n = next((c for c in n.children if c.is_dir and path.startswith(c.path + os.sep)), None)
        self._render_tree()
        self.run_clean_scan()

    def show_in_folder(self, path):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                          None, 0, 3000, None)
        except GLib.Error:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), None)

    # -- duplicates

    def _build_dups_tab(self):
        box = vbox(12)
        c = card()
        r1 = hbox(8)
        r1.pack_start(label(_("Search in:"), ("muted",)), False, False, 0)
        self.dup_roots = space.default_dup_roots()
        self.dup_roots_lbl = label("", ("crumb",), ellipsize=True, wrap=False)
        r1.pack_start(self.dup_roots_lbl, True, True, 0)
        r1.pack_end(self.action_button(_("Reset"), self._dup_reset_roots), False, False, 0)
        r1.pack_end(self.action_button(_("Add folder…"), self._dup_add_root, icon_name="list-add-symbolic"),
                    False, False, 0)
        c.pack_start(r1, False, False, 0)
        r2 = hbox(12)
        r2.pack_start(label(_("Ignore files smaller than"), ("muted",)), False, False, 0)
        self.dup_min = Gtk.ComboBoxText()
        describe(self.dup_min, _("Smallest file to compare"))
        for t in ("1 MB", "10 MB", "100 MB"):
            self.dup_min.append(t, t)
        self.dup_min.set_active(0)
        r2.pack_start(self.dup_min, False, False, 0)
        self.dup_hidden = Gtk.CheckButton(label=_("Include hidden files and folders"))
        r2.pack_start(self.dup_hidden, False, False, 12)
        c.pack_start(r2, False, False, 10)
        r2 = hbox(8)                      # buttons on their own row so narrow windows fit
        self.dup_cancel_btn = Gtk.Button(label=_("Cancel"))
        css(self.dup_cancel_btn, "ghost")
        self.dup_cancel_btn.connect("clicked", lambda *_x: self._space_cancel.set())
        self.dup_cancel_btn.set_sensitive(False)
        r2.pack_end(self.dup_cancel_btn, False, False, 0)
        # Not registered with action_button(): its state follows the scan, not the global busy flag
        self.dup_scan_btn = Gtk.Button()
        inner = hbox(6)
        inner.pack_start(icon("system-search-symbolic"), False, False, 0)
        inner.pack_start(Gtk.Label(label=_("Find duplicates")), False, False, 0)
        self.dup_scan_btn.add(inner)
        describe(self.dup_scan_btn, _("Find duplicates"))
        css(self.dup_scan_btn, "primary")
        self.dup_scan_btn.connect("clicked", lambda *_x: self.scan_dups())
        self.dup_running = False
        r2.pack_end(self.dup_scan_btn, False, False, 0)
        c.pack_start(r2, False, False, 0)
        self.dup_progress = Gtk.ProgressBar()
        self.dup_progress.set_no_show_all(True)
        c.pack_start(self.dup_progress, False, False, 0)
        self.dup_status = label(_("Duplicates are compared by content (BLAKE2 hash), not by name. "
                                "The oldest copy in each group is kept by default."), ("faint",), wrap=True)
        c.pack_start(self.dup_status, False, False, 6)
        box.pack_start(c, False, False, 0)

        self.dup_summary = hbox(10)
        self.dup_sel_lbl = label("", ("h2",))
        self.dup_summary.pack_start(self.dup_sel_lbl, True, True, 0)
        self.dup_trash_btn = self.action_button(_("Move selected to Trash"), self.trash_dups, primary=True,
                                                icon_name="user-trash-symbolic")
        self.dup_summary.pack_end(self.dup_trash_btn, False, False, 0)
        self.dup_summary.set_no_show_all(True)
        box.pack_start(self.dup_summary, False, False, 0)
        self.dup_card, self.dup_list = list_card()
        self.dup_card.set_no_show_all(True)
        box.pack_start(self.dup_card, False, False, 0)
        self.dup_groups, self.dup_sel = [], {}
        self._dup_show_roots()
        return box

    def _dup_show_roots(self):
        self.dup_roots_lbl.set_text("   ".join(r.replace(space.HOME, "~") for r in self.dup_roots))

    def _dup_reset_roots(self):
        self.dup_roots = space.default_dup_roots()
        self._dup_show_roots()

    def _dup_add_root(self):
        d = Gtk.FileChooserDialog(title=_("Add a folder to search"), transient_for=self,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        css(d, "mintcare")
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Add"), Gtk.ResponseType.OK)
        d.set_current_folder(space.HOME)
        resp, path = d.run(), d.get_filename()
        d.destroy()
        if resp == Gtk.ResponseType.OK and path and path not in self.dup_roots:
            self.dup_roots = [r for r in self.dup_roots if not r.startswith(path.rstrip("/") + "/")] + [path]
            self._dup_show_roots()

    def scan_dups(self):
        if self.dup_running:
            return
        self.dup_running = True
        self._space_cancel.clear()
        self.dup_scan_btn.set_sensitive(False)
        self.dup_cancel_btn.set_sensitive(True)
        self.dup_progress.show()
        self.dup_progress.set_fraction(0)
        self.dup_status.set_text(_("Listing files…"))
        min_size = {"1 MB": 1 << 20, "10 MB": 10 << 20, "100 MB": 100 << 20}[self.dup_min.get_active_id()]
        hidden = self.dup_hidden.get_active()
        roots = list(self.dup_roots)

        def progress(stage, done, total):
            def ui():
                if stage == "Listing":
                    self.dup_progress.pulse()
                    self.dup_status.set_text(_('Listing files… {0:,} candidates').format(done))
                else:
                    self.dup_progress.set_fraction(min(1.0, done / total))
                    self.dup_status.set_text(_('Comparing contents… {0} of up to {1}').format(human(done), human(total)))
                return False
            GLib.idle_add(ui)

        def done(groups):
            self.dup_running = False
            self.dup_scan_btn.set_sensitive(True)
            self.dup_cancel_btn.set_sensitive(False)
            self.dup_progress.hide()
            if self._space_cancel.is_set():
                self.dup_status.set_text(_("Cancelled."))
                return
            self.dup_groups = groups or []
            self.dup_sel = {p: i > 0 for g in self.dup_groups for i, (p, _m) in enumerate(g["files"])}
            wasted = sum(g["wasted"] for g in self.dup_groups)
            self.dup_status.set_text(_('Found {0:,} groups of identical files — {1} could be freed.').format(len(self.dup_groups), human(wasted)) if self.dup_groups
                                     else _("No duplicate files found."))
            self._render_dups()
        self.bg(lambda: space.duplicates(roots, min_size, hidden, progress, self._space_cancel.is_set), done)

    def _set_thumbnail(self, img, fut, gen):
        """Runs on the UI thread when a thumbnail is ready, unless the duplicate list was drawn again since."""
        if gen == self._dup_render and not fut.cancelled() and fut.exception() is None and fut.result() is not None:
            img.set_from_pixbuf(fut.result())
        return False

    def _render_dups(self):
        for f in getattr(self, "_thumb_futures", []):    # thumbnails of the previous list are not wanted now
            f.cancel()
        self._thumb_futures = []
        self._dup_render = getattr(self, "_dup_render", 0) + 1
        if getattr(self, "_thumbs", None) is None:
            self._thumbs = ThreadPoolExecutor(max_workers=1)     # one decoder at a time, off the UI thread
        for ch in self.dup_list.get_children():
            ch.destroy()
        show = self.dup_groups[:120]
        for g in show:
            head = Gtk.ListBoxRow(activatable=False)
            css(head, "dup-head")
            hb = hbox(10)
            hb.pack_start(label(_('{0} copies · {1} each').format(len(g['files']), human(g['size'])), ("h3",)), False, False, 0)
            hb.pack_start(pill(_('{0} wasted').format(human(g['wasted'])), "warn"), False, False, 0)
            name = os.path.basename(g["files"][0][0])
            hb.pack_start(label(name, ("faint", "mono"), ellipsize=True), True, True, 0)
            head.add(hb)
            self.dup_list.add(head)
            for i, (p, m) in enumerate(g["files"]):
                row = Gtk.ListBoxRow(activatable=False)
                rb = hbox(10)
                cb = Gtk.CheckButton(active=self.dup_sel.get(p, False), valign=Gtk.Align.CENTER)
                describe(cb, p)
                cb.connect("toggled", self._dup_toggle, p)
                rb.pack_start(cb, False, False, 0)
                if space.file_kind(p) == "image":           # thumbnail, so copies can be told apart
                    img = Gtk.Image(valign=Gtk.Align.CENTER)    # filled in later; an unreadable one stays empty
                    img.set_size_request(48, 48)
                    rb.pack_start(img, False, False, 0)
                    fut = self._thumbs.submit(_thumbnail, p)
                    fut.add_done_callback(lambda f, img=img, gen=self._dup_render:
                                          GLib.idle_add(self._set_thumbnail, img, f, gen))
                    self._thumb_futures.append(fut)
                pl = label(p.replace(space.HOME, "~"), ("mono", "muted"), ellipsize=True)
                pl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                rb.pack_start(pl, True, True, 0)
                if i == 0:
                    rb.pack_end(pill(_("oldest"), "ok"), False, False, 0)
                rb.pack_end(label(time.strftime("%Y-%m-%d", time.localtime(m)), ("faint", "mono")), False, False, 0)
                row.add(rb)
                self.dup_list.add(row)
        if len(self.dup_groups) > len(show):
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_('Showing the {0} groups that waste the most space (of {1:,}). Trash these, then scan again for the rest.').format(len(show), len(self.dup_groups)),
                        ("faint",), wrap=True))
            self.dup_list.add(r)
        has = bool(self.dup_groups)
        for w in (self.dup_card, self.dup_summary):
            w.set_no_show_all(False)      # no_show_all also blocks show_all() reaching the children
            w.show_all()
            w.set_visible(has)
            w.set_no_show_all(True)
        self._dup_update_sel()

    def _dup_toggle(self, cb, path):
        self.dup_sel[path] = cb.get_active()
        self._dup_update_sel()

    def _dup_update_sel(self):
        visible = {p for g in self.dup_groups[:120] for p, _x in g["files"]}
        n = sum(1 for p, v in self.dup_sel.items() if v and p in visible)
        size = sum(g["size"] for g in self.dup_groups[:120] for p, _x in g["files"] if self.dup_sel.get(p))
        self.dup_sel_lbl.set_text(_('{0:,} files selected · {1}').format(n, human(size)))

    def trash_dups(self):
        groups = self.dup_groups[:120]
        picked, skipped = [], 0
        for g in groups:
            sel = [p for p, _x in g["files"] if self.dup_sel.get(p)]
            if len(sel) == len(g["files"]):
                skipped += 1                          # never remove every copy
                continue
            picked += [(p, g["size"]) for p in sel]
        if not picked:
            self.set_status(_("Nothing selected") + (_(" (a group can't have every copy selected)") if skipped else ""))
            return
        total = sum(s_ for _x, s_ in picked)
        extra = _('\n\n{0} group(s) had every copy ticked and will be skipped.').format(skipped) if skipped else ""
        if not self.confirm(_('Move {0:,} duplicate file(s) to the Trash?').format(len(picked)),
                            _('Frees {0} once you empty the Trash. One copy of each file is kept.').format(human(total))
                            + extra):
            return

        def work():
            ok, failed = [], []
            for p, sz in picked:
                try:
                    space.trash(p)
                    ok.append((p, sz))
                except GLib.Error as e:
                    failed.append(f"{p}: {e.message}")
            return ok, failed

        def done(res):
            ok, failed = res or ([], [])
            for p, _x in ok:
                self.log(_('Moved duplicate to Trash: {0}').format(p))
            if ok:
                maintenance.record("trash", _('Moved {0:,} duplicate file(s) to the Trash ({1})').format(len(ok), human(sum(s_ for _x, s_ in ok))), not failed)
            for f in failed:
                self.log(_('Could not trash {0}').format(f))
            gone = {p for p, _x in ok}
            for g in self.dup_groups:
                g["files"] = [f for f in g["files"] if f[0] not in gone]
                g["wasted"] = g["size"] * max(0, len(g["files"]) - 1)
            self.dup_groups = [g for g in self.dup_groups if len(g["files"]) > 1]
            for p in gone:
                self.dup_sel.pop(p, None)
            self.set_status(_('Moved {0:,} duplicates ({1}) to the Trash').format(len(ok), human(sum(s_ for _x, s_ in ok))),
                            bad=bool(failed))
            self._render_dups()
            self.run_clean_scan()
        self.in_thread(_("Moving duplicates to the Trash…"), work, done)

    # -- snapshots

    def _build_olddl_tab(self):
        box = vbox(10)
        self.olddl_folder = space.downloads_dir()
        self.olddl_head = label("", ("muted",), wrap=True)
        box.pack_start(self.olddl_head, False, False, 0)
        fh = hbox(8)
        self.olddl_path = label("", ("crumb",), ellipsize=True)
        fh.pack_start(self.olddl_path, True, True, 0)
        fh.pack_end(self._btn(_("Choose folder…"), self._choose_old_folder, icon_name="folder-symbolic"), False, False, 0)
        box.pack_start(fh, False, False, 0)
        self.olddl_types = label("", ("muted",), wrap=True)
        box.pack_start(self.olddl_types, False, False, 0)
        c, self.olddl_list = list_card()
        box.pack_start(c, False, False, 0)
        return box

    def _choose_old_folder(self):
        path = self._pick_folder(_("Choose a folder to check"), self.olddl_folder)
        if path:
            self.olddl_folder = path
            self.load_old_downloads()

    def load_old_downloads(self):
        folder = self.olddl_folder
        self.olddl_path.set_text(folder.replace(str(Path.home()), "~"))
        self.bg(lambda: (space.old_downloads(folder), space.file_types(folder)), self._render_old_downloads)

    def _render_old_downloads(self, res):
        rows, types = res or ([], [])
        self._clear(self.olddl_list, "olddl_row")
        if not rows:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(_("Nothing old and big in this folder."), ("muted",)))
            self.olddl_list.add(r)
        total = sum(size for size, _p, _l in rows)
        self.olddl_head.set_text(
            _("Big files you haven't opened or changed for 3 months. They go to the Trash, so you can still get "
              "them back.") + ("  " + ngettext("{0} file, {1}.", "{0} files, {1}.",
                                                len(rows)).format(len(rows), human(total)) if rows else ""))
        names = {"video": _("Videos"), "image": _("Pictures"), "audio": _("Music"), "archive": _("Archives"),
                 "disk": _("Disk images"), "other": _("Other")}
        self.olddl_types.set_text(_("Space by type: ") + " · ".join(f"{names[k]} {human(n)}" for k, n in types)
                                  if types else "")
        for size, path, last in rows[:60]:
            when = time.strftime("%Y-%m-%d", time.localtime(last))
            btns = [self._btn(_("Show"), lambda p=path: self.show_in_folder(p), tag="olddl_row"),
                    self._btn(_("Trash"), lambda p=path, sz=size: self._trash_old(p, sz), tag="olddl_row")]
            self.olddl_list.add(self._row(os.path.basename(path), _('Last used {0}').format(when),
                                          os.path.dirname(path), "folder-download-symbolic", [], btns, human(size)))
        self.olddl_list.show_all()

    def _trash_old(self, path, size):
        if not self.confirm(_('Move to Trash?'), _('<tt>{0}</tt>\n\n{1} — you can restore it from the Trash until you '
                                                   'empty it.').format(GLib.markup_escape_text(path), human(size))):
            return
        try:
            space.trash(path)
        except GLib.Error as e:
            self.log(_('Could not trash {0}: {1}').format(path, e.message))
            self.set_status(_("Could not move to Trash"), bad=True)
            return
        maintenance.record("trash", _('Moved {0} to the Trash').format(os.path.basename(path)), True, path)
        self.set_status(_('Moved {0} to the Trash').format(os.path.basename(path)))
        self.load_old_downloads()

    def _build_snaps_tab(self):
        box = vbox(12)
        c = card("ecard")
        row = hbox(16)
        left = vbox(2)
        left.pack_start(label(_("SNAPSHOTS"), ("kicker",)), False, False, 0)
        self.snap_count = label("…", ("boot-big",))
        left.pack_start(self.snap_count, False, False, 0)
        row.pack_start(left, False, False, 0)
        self.snap_info = label("", ("muted",), wrap=True)
        self.snap_info.set_valign(Gtk.Align.CENTER)
        row.pack_start(self.snap_info, True, True, 0)
        row.pack_end(self.action_button(_("Delete selected"), self.delete_snapshots, primary=True,
                                        icon_name="user-trash-symbolic"), False, False, 0)
        row.pack_end(self.action_button(_("Open Timeshift"), lambda: subprocess.Popen(
            ["timeshift-launcher"], start_new_session=True), icon_name="document-open-recent-symbolic"),
            False, False, 0)
        c.pack_start(row, False, False, 0)
        box.pack_start(c, False, False, 0)
        box.pack_start(label(_("Snapshots share unchanged files, so deleting one frees only what changed "
                             "around it. The newest snapshot is always kept, and snapshots you gave a "
                             "comment are never pre-selected."), ("faint",), wrap=True), False, False, 0)
        self.snap_card, self.snap_list = list_card()
        box.pack_start(self.snap_card, False, False, 0)
        self.snap_sel = {}
        return box

    def load_snapshots(self):
        self.bg(lambda: (space.snapshots(), space.timeshift_summary()), self._render_snapshots)

    def _render_snapshots(self, res):
        if not res:
            return
        (snaps, note), summary = res
        self.snaps = snaps
        self.snap_count.set_text(str(len(snaps)))
        parts = []
        if summary["schedule"]:
            parts.append(_("Schedule: ") + ", ".join(_('{0} (keeps {1})').format(k, summary['keep'].get(k, '?'))
                                                  for k in summary["schedule"]))
        else:
            parts.append(_("No automatic schedule."))
        if summary["base_size"]:
            parts.append(_('A full system copy is about {0}; later snapshots store only changes.').format(
                human(summary['base_size'])))
        if note:
            parts.append(note)
        self.snap_info.set_text(" ".join(parts))
        self._clear(self.snap_list, "snap_row")
        self.snap_sel = {}
        if not snaps:
            r = Gtk.ListBoxRow(activatable=False)
            r.add(label(note or _("No snapshots."), ("muted",)))
            self.snap_list.add(r)
        for sn in snaps:
            row = Gtk.ListBoxRow(activatable=False)
            b = hbox(12)
            cb = Gtk.CheckButton(valign=Gtk.Align.CENTER, sensitive=not sn.get("latest"))
            cb.connect("toggled", lambda w, n=sn["name"]: self.snap_sel.__setitem__(n, w.get_active()))
            b.pack_start(cb, False, False, 0)
            t = vbox(2)
            th = hbox(8)
            when = time.strftime("%a %d %b %Y, %H:%M", time.localtime(sn["created"])) if sn["created"] else sn["name"]
            th.pack_start(label(when, ("h3",)), False, False, 0)
            for tag in sn["tags"]:
                th.pack_start(pill(tag, "dim"), False, False, 0)
            if sn.get("latest"):
                th.pack_start(pill(_("newest · kept"), "ok"), False, False, 0)
            t.pack_start(th, False, False, 0)
            if sn["comment"]:
                t.pack_start(label(f"“{sn['comment']}”", ("muted",)), False, False, 0)
            t.pack_start(label(_('{0} · {1:,} files').format(sn['name'], sn['files']), ("faint", "mono")), False, False, 0)
            b.pack_start(t, True, True, 0)
            b.pack_end(self._btn(_("Restore a file…"), lambda n=sn["name"]: self.restore_timeshift_file(n),
                                 tag="snap_row"), False, False, 0)
            row.add(b)
            self.snap_list.add(row)
        self.snap_list.show_all()

    def _pick_from(self, folder, title, everything=None):
        """File chooser rooted at `folder`. Returns a list of paths, [folder] for `everything`, or None."""
        d = Gtk.FileChooserDialog(title=title, transient_for=self, action=Gtk.FileChooserAction.OPEN,
                                  select_multiple=True)
        d.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        if everything:
            d.add_button(everything, 2)
        d.add_button(_("Restore selected"), Gtk.ResponseType.OK)
        d.set_current_folder(folder)
        resp = d.run()
        paths = d.get_filenames()
        d.destroy()
        if resp == 2:
            return [folder]
        return paths if resp == Gtk.ResponseType.OK and paths else None

    def restore_timeshift_file(self, name):
        root = space.snapshot_root(name)
        paths = self._pick_from(root, _('Restore from the {0} snapshot').format(name))
        if not paths:
            return
        try:
            script = space.restore_file_script(name, paths)
        except ValueError as e:
            self.set_status(str(e), bad=True)
            return
        dests = ["/" + os.path.relpath(os.path.realpath(p), os.path.realpath(root)) for p in paths]
        if not self.confirm(ngettext("Restore {0} file?", "Restore {0} files?", len(paths)).format(len(paths)),
                            "<tt>" + GLib.markup_escape_text("\n".join(dests[:12])) + "</tt>\n\n" +
                            _("Each file goes back to where it was. The current version is kept beside it "
                              "as NAME.~1~, so you can switch back.")):
            return
        self._root_job(_("Restoring…"), [("ts-restore", _("Restore files from Timeshift"), script)], "Timeshift",
                       lambda r: maintenance.record("snapshot", _('Restored {0} from snapshot {1}').format(
                           ", ".join(dests[:3]), name), r.get("ts-restore") == 0))

    def delete_snapshots(self):
        names = [n for n, v in self.snap_sel.items() if v]
        if not names:
            self.set_status(_("Tick the snapshots you want to delete first"))
            return
        info = {s_["name"]: s_ for s_ in getattr(self, "snaps", [])}
        lines = []
        for n in names:
            c = info.get(n, {}).get("comment")
            lines.append(f"• {n}" + (f" — <b>“{GLib.markup_escape_text(c)}”</b>" if c else ""))
        if not self.confirm(_('Delete {0} Timeshift snapshot(s)?').format(len(names)),
                            "\n".join(lines) + _("\n\nThis can't be undone; you won't be able to roll back to "
                            "these points.")):
            return
        steps = [(f"snap{i}", _('Delete snapshot {0}').format(n), space.delete_snapshot_script(n)) for i, n in enumerate(names)]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, _("Snapshot clean-up"))
            if freed > 0:
                self.log(_('Freed {0} on /').format(human(freed)))
                self.set_status(_('Deleted {0} snapshot(s), freed {1}').format(len(names), human(freed)))
            self.load_snapshots()
        self.in_thread(_("Deleting snapshots…"), lambda: self.execute(steps, [], "Timeshift"), done)
