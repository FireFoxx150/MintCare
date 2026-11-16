"""Clean page: junk measured and cleaned, plus the quick clean.

Mixed into MintCareWindow. Methods are moved here unchanged from gui.py.
"""

import shutil
from gi.repository import Gtk
from . import cleaner, maintenance
from .i18n import _
from .util import human
from .widgets import Bar, EMERALD, describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, hbox, label, list_card, pill, scrolled, vbox


class CleanPage:
    def _build_clean(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "user-trash-symbolic", _("Clean"),
            _("Measures junk first, then removes only what you select."),
            [self.action_button(_("Rescan"), self.run_clean_scan, icon_name="view-refresh-symbolic"),
             self.action_button(_("Clean selected"), self.run_clean, primary=True,
                                icon_name="user-trash-symbolic")]), False, False, 0)

        hero = card("ecard")
        hr = hbox(16)
        left = vbox(2)
        left.pack_start(label(_("RECLAIMABLE"), ("kicker",)), False, False, 0)
        self.clean_big = label("…", ("big",))
        left.pack_start(self.clean_big, False, False, 0)
        hr.pack_start(left, False, False, 0)
        right = vbox(6)
        right.set_valign(Gtk.Align.CENTER)
        self.clean_total = label(_("Scanning…"), ("muted",), wrap=True)
        right.pack_start(self.clean_total, False, False, 0)
        self.clean_bar = Bar(EMERALD, 8)
        right.pack_start(self.clean_bar, False, False, 0)
        hr.pack_start(right, True, True, 0)
        hero.pack_start(hr, False, False, 0)
        page.pack_start(hero, False, False, 0)

        c, lb = list_card()
        self.clean_checks, self.clean_sizes, self.clean_details, self.clean_bytes = {}, {}, {}, {}
        self.clean_scripts = {}                 # what Clean runs for each item, as its latest measure found it
        for item in cleaner.ITEMS:
            if not item.available():
                continue
            row = Gtk.ListBoxRow(activatable=False)
            box = hbox(12)
            cb = Gtk.CheckButton(active=item.default, valign=Gtk.Align.CENTER)
            describe(cb, item.title)
            cb.connect("toggled", lambda *_x: self._update_clean_total())
            self.clean_checks[item.id] = cb
            box.pack_start(cb, False, False, 0)
            text = vbox(2)
            th = hbox(8)
            th.pack_start(label(item.title, ("h3",)), False, False, 0)
            if item.root_script:
                th.pack_start(pill(_("admin"), "dim"), False, False, 0)
            text.pack_start(th, False, False, 0)
            det = label(item.description, ("muted",), wrap=True)
            self.clean_details[item.id] = det
            text.pack_start(det, False, False, 0)
            box.pack_start(text, True, True, 0)
            size = label("…", ("stat-val",), xalign=1.0)
            self.clean_sizes[item.id] = size
            box.pack_end(size, False, False, 0)
            row.add(box)
            lb.add(row)
            self.clean_rows = getattr(self, "clean_rows", {})
            self.clean_rows[item.id] = row
        page.pack_start(c, False, False, 0)
        return scrolled(page)

    def run_clean_scan(self):
        def work():
            return {i.id: cleaner.measure(i) for i in cleaner.ITEMS if i.id in self.clean_checks}

        def done(res):
            if not res:
                return
            for iid, (size, detail, script) in res.items():
                item = cleaner.BY_ID[iid]
                self.clean_scripts[iid] = script
                self.clean_bytes[iid] = size or 0
                self.clean_sizes[iid].set_text(human(size) if size else ("—" if size is None else "0 B"))
                self.clean_details[iid].set_text(f"{item.description} {detail}.")
            self._update_clean_total()
            maintenance.record_cache_size(sum(self.clean_bytes.values()))
            grown = maintenance.cache_growth()
            if grown and grown[0] > 1 << 20:
                self.clean_total.set_text(self.clean_total.get_text() + "\n" + _(
                    "Junk has grown by {0} in the last {1:.0f} days.").format(human(grown[0]), grown[1]))
        self.in_thread(_("Measuring junk…"), work, done)
        return False

    def _update_clean_total(self):
        total = sum(self.clean_bytes.get(i, 0) for i, cb in self.clean_checks.items() if cb.get_active())
        everything = sum(self.clean_bytes.values()) or 1
        n = sum(1 for cb in self.clean_checks.values() if cb.get_active())
        self.clean_big.set_text(human(total))
        self.clean_total.set_text(_("{0} of {1} items selected. Items shown as “—” can't be measured in advance and may free more.").format(n, len(self.clean_checks)))
        self.clean_bar.set_value(100 * total / everything)

    def run_clean(self):
        sel = [cleaner.BY_ID[i] for i, cb in self.clean_checks.items() if cb.get_active()]
        if not sel:
            return
        if any(i.id == "trash" for i in sel) and not self.confirm(
                _("Empty the Trash?"), _("Files in the Trash will be permanently deleted.")):
            return
        root = [(i.id, i.title, i.root_script) for i in sel if i.root_script]
        scripts = {i.id: self.clean_scripts.get(i.id, i.user_script) for i in sel}
        user = [(i.id + "-user", i.title, scripts[i.id]) for i in sel if scripts[i.id]]
        before = shutil.disk_usage("/").free

        def done(results):
            freed = shutil.disk_usage("/").free - before
            self.notify_done(results or {}, _("Clean-up"))
            if freed > 0:
                self.set_status(_('Freed {0}').format(human(freed)))
                self.log(_('Freed {0} on /').format(human(freed)))
            self.run_clean_scan()
            self.run_diagnose()
        self.in_thread(_("Cleaning…"), lambda: self.execute(root, user, "Clean"), done)

    def quick_clean(self):
        self.goto("clean")
        self.run_clean()
