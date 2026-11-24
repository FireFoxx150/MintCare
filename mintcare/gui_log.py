"""Activity log page and the system journal viewer, mixed into MintCareWindow."""

from gi.repository import Gtk

from .i18n import _
from . import __version__
# gui imports this module after defining these helpers, so they exist here (import gui, not this module, first)
from .gui import card, css, hbox, vbox


class LogPage:
    def _build_log(self):
        page = vbox(14, "page")
        page.pack_start(self.page_header("utilities-terminal-symbolic", _("Activity log"),
                                         _("Full output of every command MintCare runs.")), False, False, 0)
        c = card("tight")
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.log_view, "log")
        self.log_buf = self.log_view.get_buffer()
        self.log_buf.set_text(_('MintCare {0}\n').format(__version__))
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.log_view)
        c.pack_start(sw, True, True, 0)
        switch = Gtk.StackSwitcher(halign=Gtk.Align.START)
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        switch.set_stack(stack)
        stack.add_titled(c, "app", _("MintCare"))
        stack.add_titled(self._journal_view(), "journal", _("System journal"))
        page.pack_start(switch, False, False, 0)
        page.pack_start(stack, True, True, 0)
        return page

    def _journal_view(self):
        box = vbox(10)
        bar = hbox(8)
        self.jr_scope = Gtk.ComboBoxText()
        for key, text in (("boot", _("This boot")), ("day", _("Last 24 hours")), ("week", _("Last 7 days"))):
            self.jr_scope.append(key, text)
        self.jr_scope.set_active_id("boot")
        self.jr_level = Gtk.ComboBoxText()
        for key, text in (("3", _("Errors")), ("4", _("Warnings and errors")), ("7", _("Everything"))):
            self.jr_level.append(key, text)
        self.jr_level.set_active_id("3")
        self.jr_find = Gtk.SearchEntry(placeholder_text=_("Filter by program or text…"))
        self.jr_scope.connect("changed", lambda *_x: self.load_journal())
        self.jr_level.connect("changed", lambda *_x: self.load_journal())
        self.jr_find.connect("activate", lambda *_x: self.load_journal())
        bar.pack_start(self.jr_scope, False, False, 0)
        bar.pack_start(self.jr_level, False, False, 0)
        bar.pack_start(self.jr_find, True, True, 0)
        bar.pack_end(self._btn(_("Refresh"), self.load_journal, icon_name="view-refresh-symbolic"), False, False, 0)
        box.pack_start(bar, False, False, 0)
        self.jr_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    left_margin=12, right_margin=12, top_margin=10, bottom_margin=10)
        css(self.jr_view, "log")
        self.jr_buf = self.jr_view.get_buffer()
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.jr_view)
        c = card("tight")
        c.pack_start(sw, True, True, 0)
        box.pack_start(c, True, True, 0)
        return box

    def load_journal(self):
        from . import journal
        scope, level, text = self.jr_scope.get_active_id(), self.jr_level.get_active_id(), self.jr_find.get_text()

        def done(res):
            rc, rows = res or (1, [])
            if rc != 0 and not rows:
                msg = _("Couldn't read the system journal. Your account may need to be in the adm group.")
            elif not rows:
                msg = _("Nothing matches these filters.")
            else:
                msg = "\n".join(f"{t}  {who}: {m}" for t, who, m in rows)
            self.jr_buf.set_text(msg)
        self.bg(lambda: journal.browse(scope, level, text), done)
