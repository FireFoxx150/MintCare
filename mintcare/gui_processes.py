"""Processes page (what is using CPU and memory, stuck apps), mixed into MintCareWindow."""

import os
import time

from gi.repository import GLib, Gtk

from .i18n import _, ngettext
from . import maintenance, processes
from .util import human
from .widgets import describe
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import css, hbox, label, list_card, scrolled, vbox


class ProcessesPage:
    def _build_processes(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header(
            "utilities-system-monitor-symbolic", _("Processes"),
            _("What's using your processor and memory right now, what each program is, and a way to close one "
              "that's stuck.")), False, False, 0)
        bar = hbox(8)
        self.proc_sort = Gtk.ComboBoxText()
        describe(self.proc_sort, _("Sort processes by"))
        self.proc_sort.append("cpu", _("Sort by CPU"))
        self.proc_sort.append("rss", _("Sort by memory"))
        self.proc_sort.set_active_id("cpu")
        bar.pack_start(self.proc_sort, False, False, 0)
        self.proc_filter = Gtk.SearchEntry(placeholder_text=_("Filter by name…"))
        bar.pack_start(self.proc_filter, True, True, 0)
        self.proc_all = Gtk.CheckButton(label=_("Include system processes"))
        bar.pack_start(self.proc_all, False, False, 0)
        self.proc_pause = Gtk.CheckButton(label=_("Pause"))
        bar.pack_end(self.proc_pause, False, False, 0)
        bar.pack_end(self.action_button(_("Find memory wasters"), self._find_wasters,
                                        icon_name="edit-find-symbolic"), False, False, 0)
        page.pack_start(bar, False, False, 0)
        self.proc_head = label("", ("faint", "mono"))
        page.pack_start(self.proc_head, False, False, 0)
        self.proc_gpu = label("", ("faint", "mono"), wrap=True)
        page.pack_start(self.proc_gpu, False, False, 0)
        self.proc_card, self.proc_list = list_card()
        page.pack_start(self.proc_card, False, False, 0)
        self._proc_prev = None
        self._proc_open = set()
        self._proc_infos = {}                 # pid -> processes.describe() for the rows that are open
        self._lowered = {}                    # pid -> (name, nice before MintCare lowered it)
        for w in (self.proc_sort, self.proc_all):
            w.connect("changed" if isinstance(w, Gtk.ComboBoxText) else "toggled", lambda *_x: self._poll_procs(True))
        self.proc_filter.connect("search-changed", lambda *_x: self._poll_procs(True))
        GLib.timeout_add(2500, self._poll_procs)
        return scrolled(page)

    def _poll_procs(self, force=False):
        if self.stack.get_visible_child_name() != "processes" or (self.proc_pause.get_active() and not force):
            return True
        if getattr(self, "_proc_busy", False):
            return True
        self._proc_busy = True
        open_pids = set(self._proc_open)

        def work():
            rows, prev = processes.processes(self._proc_prev)
            infos = {r["pid"]: processes.describe(r) for r in rows if r["pid"] in open_pids}
            return (rows, prev), processes.gpu_processes(), infos

        def done(res):
            self._proc_busy = False
            if res:
                (rows, self._proc_prev), gpu, self._proc_infos = res
                self._render_procs(rows)
                self._render_gpu_procs(gpu)
        self.bg(work, done)
        return True

    def _render_procs(self, rows):
        self._clear(self.proc_list, "proc_row")
        key = self.proc_sort.get_active_id()
        flt = self.proc_filter.get_text().strip().lower()
        shown = [r for r in rows if (self.proc_all.get_active() or r["mine"]) and not r["kernel"]
                 and (not flt or flt in r["name"].lower() or flt in r["cmd"].lower())]
        shown.sort(key=lambda r: -r[key])
        live = {r["pid"] for r in rows}
        self._lowered = {pid: v for pid, v in self._lowered.items() if pid in live}
        tot_cpu = sum(r["cpu"] for r in rows)
        # xgettext:no-python-format
        self.proc_head.set_text(_('{0} processes · {1:.0f}% of all CPU · showing {2}').format(len(rows), tot_cpu / (os.cpu_count() or 1), min(len(shown), 40)))
        for r in shown[:40]:
            pills = [] if r["mine"] else [(r["user"], "dim")]
            btns = [self._btn(_("What is this?"), lambda r=r: self._proc_info(r), tag="proc_row"),
                    self._btn(_("End"), lambda r=r: self._end_proc(r), tag="proc_row")]
            if r["mine"]:
                btns.insert(1, self._btn(_("Lower priority"), lambda r=r: self._lower_proc(r), tag="proc_row"))
            if r["pid"] in self._lowered:
                btns.append(self._btn(_("Restore priority"), lambda r=r: self._restore_proc(r), tag="proc_row"))
            row = self._row(r["name"], "", r["cmd"][:300], None, pills, btns,
                            f"{r['cpu']:4.0f}% · {human(r['rss'])}")
            if r["pid"] in self._proc_open and r["pid"] in self._proc_infos:
                info = self._proc_infos[r["pid"]]
                txt = info["text"] or _("No description for this one.")
                if info["package"]:
                    txt += f"\nPackage: {info['package']} — {info['summary']}"
                row.text_box.pack_start(label(txt, ("muted",), wrap=True), False, False, 0)
            self.proc_list.add(row)
        self.proc_list.show_all()

    def _render_gpu_procs(self, procs):
        if not procs:
            self.proc_gpu.set_text("")
            return
        self.proc_gpu.set_text(_("Graphics memory: {0}").format(" · ".join(
            f"{name} ({pid}) {human(mb * 2 ** 20)}" for pid, name, mb in procs[:8])))

    def _proc_info(self, r):
        (self._proc_open.discard if r["pid"] in self._proc_open else self._proc_open.add)(r["pid"])
        self._poll_procs(True)

    def _lower_proc(self, r):
        before = processes.nice_of(r["pid"])
        ok, msg = processes.lower_priority(r["pid"], r["name"])
        if ok and before is not None:
            self._lowered[r["pid"]] = (r["name"], before)
            self.set_status(_("{0} now runs at lower priority").format(r["name"]))
        else:
            self.set_status(msg or _("Could not change the priority"), bad=True)
        self._poll_procs(True)

    def _restore_proc(self, r):
        name, nice = self._lowered.get(r["pid"], (r["name"], 0))
        step = processes.restore_step(r["pid"], name, nice)

        def done(results):
            if results.get(step[0]) == 0:
                self._lowered.pop(r["pid"], None)
            self._poll_procs(True)
        self._root_job(_("Restoring priority…"), [step], _("Processes"), done_cb=done)

    def _end_proc(self, r):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text=_('End {0} ({1})?').format(r['name'], r['pid']))
        css(d, "mintcare")
        d.format_secondary_text(_("Unsaved work in that program will be lost. 'Force' stops a program that "
                                  "doesn't respond."))
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Force"), 2, _("End"), Gtk.ResponseType.OK)
        resp = d.run()
        d.destroy()
        if resp not in (Gtk.ResponseType.OK, 2):
            return
        force = resp == 2
        if r["mine"]:
            try:
                processes.end(r["pid"], force)
                maintenance.record("other", f"Ended {r['name']} ({r['pid']})")
                self.set_status(_('Ended {0}').format(r['name']))
            except ProcessLookupError:
                self.set_status(_("It had already closed"))
            except PermissionError:
                self.set_status(_("Not allowed to end that process"), bad=True)
            GLib.timeout_add(600, lambda: self._poll_procs(True) and False)
        else:
            self._root_job(_('Ending {0}…').format(r['name']), [processes.end_step(r["pid"], r["name"], force)], "Processes",
                           lambda res: self._poll_procs(True))

    def _find_wasters(self):
        def work():
            prev = processes.snapshot()
            time.sleep(2)                      # a CPU sample, so a program busy right now isn't called idle
            rows, _prev = processes.processes(prev)
            return processes.memory_wasters(rows, processes.protected_pids())
        self.in_thread(_("Looking for idle programs holding memory…"), work,
                       lambda found: self._show_wasters(found) if found is not None else None)

    def _show_wasters(self, found):
        if not found:
            self.set_status(_("No idle programs holding a lot of memory"))
            return
        d = Gtk.Dialog(title=_("Programs wasting memory"), transient_for=self, modal=True)
        css(d, "mintcare")
        d.set_default_size(620, 440)
        d.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("End selected"), Gtk.ResponseType.OK)
        css(d.get_widget_for_response(Gtk.ResponseType.OK), "primary")
        box = d.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        box.pack_start(label(_("These programs of yours have used almost no processor time for a while but still "
                               "hold memory. Ending one closes it, and unsaved work in it will be lost. Untick "
                               "anything you want to keep."), ("muted",), wrap=True), False, False, 0)
        lst = vbox(6)
        checks_ = []
        for r, why in found:
            cb = Gtk.CheckButton(active=True)
            text = vbox(2)
            text.pack_start(label(_("{0} ({1}) · {2}").format(r["name"], r["pid"], human(r["rss"] + r["swap"])),
                                  ("h3",)), False, False, 0)
            text.pack_start(label(why, ("muted",), wrap=True), False, False, 0)
            text.pack_start(label(r["cmd"][:200], ("faint", "mono"), wrap=True), False, False, 0)
            cb.add(text)
            describe(cb, r["name"])
            lst.pack_start(cb, False, False, 0)
            checks_.append((cb, r))
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(lst)
        box.pack_start(sw, True, True, 0)
        d.show_all()
        resp = d.run()
        chosen = [(r["pid"], r["name"]) for cb, r in checks_ if cb.get_active()]
        d.destroy()
        if resp == Gtk.ResponseType.OK and chosen:
            self._end_wasters(chosen, False)

    def _end_wasters(self, targets, force):
        ended = [(pid, name) for pid, name in targets if processes.end_checked(pid, name, force) == "ended"]
        for pid, name in ended:
            maintenance.record("other", f"Ended {name} ({pid})")
        self.set_status(ngettext("Ended {0} program", "Ended {0} programs", len(ended)).format(len(ended)))
        if force or not ended:
            GLib.timeout_add(600, lambda: self._poll_procs(True) and False)
            return

        def check():
            stuck = processes.still_running(ended)
            self._poll_procs(True)
            if stuck:
                self._offer_force(stuck)
            return False
        GLib.timeout_add(3000, check)

    def _offer_force(self, stuck):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              text=ngettext("{0} program is still running", "{0} programs are still running",
                                            len(stuck)).format(len(stuck)))
        css(d, "mintcare")
        d.format_secondary_text(_("They didn't close when asked: {0}. 'Force end' stops them straight away; "
                                  "unsaved work in them will be lost.").format(
            ", ".join("{0} ({1})".format(name, pid) for pid, name in stuck)))
        d.add_buttons(_("Leave them"), Gtk.ResponseType.CANCEL, _("Force end"), Gtk.ResponseType.OK)
        resp = d.run()
        d.destroy()
        if resp == Gtk.ResponseType.OK:
            self._end_wasters(stuck, True)
