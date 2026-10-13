"""Cairo-drawn ring gauges and live line charts in the MintCare Companion style."""

import math
from collections import deque

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402

EMERALD = "#10b981"
BLUE = "#3b82f6"
ORANGE = "#f97316"
VIOLET = "#a78bfa"
# Theme-dependent colours for Cairo drawing; gui.apply_theme() updates these.
THEME = {"ink": (1.0, 1.0, 1.0), "text": "#f1f5f9", "faint": "#64748b"}


def set_theme(ink_hex, text_hex, faint_hex):
    THEME["ink"] = rgb(ink_hex)[:3]
    THEME["text"], THEME["faint"] = text_hex, faint_hex


def track(alpha=0.05):
    return THEME["ink"] + (alpha * (1.6 if THEME["ink"][0] < 0.5 else 1.0),)
MONO = "JetBrains Mono, Ubuntu Mono, monospace"


def rgb(hexstr, a=1.0):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (a,)


def describe(widget, name=None, description=None):
    """Set what screen readers announce for a widget (Cairo-drawn widgets have no text of their own)."""
    acc = widget.get_accessible()
    if acc is None:
        return widget
    if name is not None:
        acc.set_name(name)
    if description is not None:
        acc.set_description(description)
    return widget


def _text(cr, widget, text, font, color, cx, cy):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_text(text, -1)
    w, h = layout.get_pixel_size()
    cr.set_source_rgba(*color)
    cr.move_to(cx - w / 2, cy - h / 2)
    PangoCairo.show_layout(cr, layout)
    return h


class RingGauge(Gtk.DrawingArea):
    """Circular percentage gauge with a soft glow and an eased animation."""

    def __init__(self, color=EMERALD, caption="", size=124):
        super().__init__()
        self.color, self.caption = color, caption
        self.value = self.shown = 0.0
        self.text = "–"
        self.set_size_request(size, size)
        self.connect("draw", self._draw)
        describe(self, caption)

    def set_value(self, pct, text=None):
        self.value = max(0.0, min(100.0, pct))
        self.text = text if text is not None else f"{pct:.0f}%"
        describe(self, f"{self.caption}: {self.text}" if self.caption else self.text)
        self._animate()

    def _animate(self):
        from gi.repository import GLib
        if getattr(self, "_anim", None):
            return

        def step():
            d = self.value - self.shown
            if abs(d) < 0.3:
                self.shown = self.value
                self._anim = None
                self.queue_draw()
                return False
            self.shown += d * 0.18
            self.queue_draw()
            return True
        self._anim = GLib.timeout_add(16, step)

    def _draw(self, w, cr):
        a = w.get_allocation()
        cx, cy = a.width / 2, a.height / 2
        r = min(a.width, a.height) / 2 - 12
        lw = max(8, r * 0.17)
        start = -math.pi / 2
        end = start + 2 * math.pi * self.shown / 100
        cr.set_line_cap(1)  # round
        cr.set_line_width(lw)
        cr.set_source_rgba(*track())
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self.shown > 0.2:
            for width, alpha in ((lw + 10, 0.06), (lw + 5, 0.12)):  # glow
                cr.set_line_width(width)
                cr.set_source_rgba(*rgb(self.color, alpha))
                cr.arc(cx, cy, r, start, end)
                cr.stroke()
            cr.set_line_width(lw)
            cr.set_source_rgba(*rgb(self.color))
            cr.arc(cx, cy, r, start, end)
            cr.stroke()
        _text(cr, w, self.text, f"{MONO} Bold {max(13, int(r * 0.36))}px", rgb(self.color), cx, cy - 7)
        _text(cr, w, self.caption.upper(), "Ubuntu Sans Bold 8.5px",
              rgb(THEME["faint"]), cx, cy + r * 0.32)
        return False


class LineChart(Gtk.DrawingArea):
    """Scrolling multi-series area chart. Series values are 0..max."""

    def __init__(self, series, points=60, height=150, fixed_max=None, fmt=lambda v: f"{v:.0f}%", log=False,
                 axis=True):
        super().__init__()
        self.series = [(name, color, deque([0.0] * points, maxlen=points)) for name, color in series]
        self.fixed_max, self.fmt, self.log, self.axis = fixed_max, fmt, log, axis
        self._started = False
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)
        describe(self, ", ".join(name for name, _c, _b in self.series))

    def push(self, *values):
        for (_x, _x, buf), v in zip(self.series, values):
            if not self._started:  # fill history with the first sample, not a flat zero line
                buf.extend([float(v)] * buf.maxlen)
            buf.append(float(v))
        self._started = True
        self._describe_latest()
        self.queue_draw()

    def _describe_latest(self):
        describe(self, description=", ".join(f"{name} {self.fmt(buf[-1])}" for name, _c, buf in self.series if buf))

    def _y(self, v, top):
        """Fraction 0..1 of the plot height for value v (log scale keeps small values visible)."""
        v = max(0.0, min(v, top))
        if self.log:
            return math.log10(1 + v) / math.log10(1 + top) if top > 0 else 0
        return v / top if top else 0

    def _label_value(self, frac, top):
        return (10 ** (frac * math.log10(1 + top)) - 1) if self.log else top * frac

    def _draw(self, w, cr):
        import cairo
        a = w.get_allocation()
        pad_l, pad_r, pad_t, pad_b = (52 if self.log else 44, 8, 8, 18) if self.axis else (4, 6, 4, 4)
        W, H = a.width - pad_l - pad_r, a.height - pad_t - pad_b
        top = self.fixed_max or max([max(b) for _x, _x, b in self.series] + [1.0]) * 1.15
        cr.set_line_width(1)
        for i in range(5):
            y = pad_t + H * i / 4
            cr.set_source_rgba(*track(0.045))
            cr.move_to(pad_l, int(y) + 0.5)
            cr.line_to(pad_l + W, int(y) + 0.5)
            cr.stroke()
            if not self.axis:
                continue
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription.from_string(f"{MONO} 8px"))
            layout.set_text(self.fmt(self._label_value((4 - i) / 4, top)), -1)
            tw, th = layout.get_pixel_size()
            cr.set_source_rgba(*rgb(THEME["faint"]))
            cr.move_to(pad_l - tw - 6, y - th / 2)
            PangoCairo.show_layout(cr, layout)
        paths = []
        for name, color, buf in self.series:
            n = len(buf)
            pts = [(pad_l + W * i / (n - 1), pad_t + H * (1 - self._y(v, top))) for i, v in enumerate(buf)]
            paths.append((color, pts, sum(buf) / n))
        # all fills first, then lines, smallest series last so it is never hidden behind a bigger one
        for color, pts, _avg in paths:
            grad = cairo.LinearGradient(0, pad_t, 0, pad_t + H)
            grad.add_color_stop_rgba(0, *rgb(color, 0.22))
            grad.add_color_stop_rgba(1, *rgb(color, 0.0))
            cr.move_to(pts[0][0], pad_t + H)
            for x, y in pts:
                cr.line_to(x, y)
            cr.line_to(pts[-1][0], pad_t + H)
            cr.close_path()
            cr.set_source(grad)
            cr.fill()
        for color, pts, _avg in sorted(paths, key=lambda p: -p[2]):
            cr.set_line_width(2)
            cr.set_line_join(1)
            cr.set_source_rgba(*rgb(color))
            cr.move_to(*pts[0])
            for x, y in pts[1:]:
                cr.line_to(x, y)
            cr.stroke()
            cr.arc(pts[-1][0], pts[-1][1], 3.2, 0, 2 * math.pi)
            cr.fill()
        return False


class Bar(Gtk.DrawingArea):
    """Thin rounded progress bar."""

    def __init__(self, color=EMERALD, height=6):
        super().__init__()
        self.color, self.value = color, 0.0
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def set_value(self, pct):
        self.value = max(0.0, min(100.0, pct))
        describe(self, description=f"{self.value:.0f}%")
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        r = a.height / 2

        def pill(x, wd):
            cr.new_sub_path()
            cr.arc(x + r, r, r, math.pi / 2, 3 * math.pi / 2)
            cr.arc(x + max(wd - r, r), r, r, -math.pi / 2, math.pi / 2)
            cr.close_path()
        cr.set_source_rgba(*track())
        pill(0, a.width)
        cr.fill()
        if self.value > 0:
            cr.set_source_rgba(*rgb(self.color))
            pill(0, max(a.height, a.width * self.value / 100))
            cr.fill()
        return False


class StackedBar(Gtk.DrawingArea):
    """Horizontal bar split into coloured segments: [(value, color), ...]."""

    def __init__(self, height=14):
        super().__init__()
        self.segments = []
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def set_segments(self, segments):
        self.segments = [(v, c) for v, c in segments if v > 0]
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        r = a.height / 2
        total = sum(v for v, _x in self.segments) or 1
        cr.new_sub_path()
        cr.arc(r, r, r, math.pi / 2, 3 * math.pi / 2)
        cr.arc(a.width - r, r, r, -math.pi / 2, math.pi / 2)
        cr.close_path()
        cr.clip()
        cr.set_source_rgba(*track())
        cr.paint()
        x = 0.0
        for i, (v, color) in enumerate(self.segments):
            wd = a.width * v / total
            cr.set_source_rgba(*rgb(color))
            cr.rectangle(x, 0, wd - (2 if i < len(self.segments) - 1 else 0), a.height)
            cr.fill()
            x += wd
        return False


TREEMAP_COLORS = [EMERALD, BLUE, ORANGE, VIOLET, "#f472b6", "#22d3ee", "#facc15", "#a3e635", "#fb7185",
                  "#818cf8"]


def _squarify(items, x, y, w, h):
    """items: [(value, payload)] sorted desc. Returns [(x, y, w, h, payload)]."""
    out = []
    items = [it for it in items if it[0] > 0]
    total = sum(v for v, _x in items)
    if not items or w <= 0 or h <= 0 or total <= 0:
        return out
    scale = w * h / total
    rest = [(v * scale, p) for v, p in items]

    def worst(row, side):
        s = sum(a for a, _x in row)
        mx, mn = max(a for a, _x in row), min(a for a, _x in row)
        return max(side * side * mx / (s * s), (s * s) / (side * side * mn))

    while rest:
        side = min(w, h)
        row = [rest.pop(0)]
        while rest and worst(row + [rest[0]], side) <= worst(row, side):
            row.append(rest.pop(0))
        s = sum(a for a, _x in row)
        if w >= h:                       # lay the row out as a column on the left
            cw = s / h
            cy = y
            for a, p in row:
                ch = a / cw
                out.append((x, cy, cw, ch, p))
                cy += ch
            x, w = x + cw, w - cw
        else:                            # lay the row out along the top
            rh = s / w
            cx = x
            for a, p in row:
                rw = a / rh
                out.append((cx, y, rw, rh, p))
                cx += rw
            y, h = y + rh, h - rh
    return out


class Treemap(Gtk.DrawingArea):
    """Clickable treemap. set_items([(size, label, sublabel, payload)])."""

    def __init__(self, on_activate=None, height=300):
        super().__init__()
        self.items, self.rects, self.hover = [], [], None
        self.on_activate = on_activate
        self.set_size_request(-1, height)
        self.set_has_tooltip(True)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._draw)
        self.connect("motion-notify-event", self._motion)
        self.connect("leave-notify-event", lambda *_x: self._set_hover(None))
        self.connect("button-press-event", self._click)
        self.connect("query-tooltip", self._tooltip)
        # Keyboard: Tab in, arrow keys move between tiles, Enter opens the highlighted folder
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK)
        self.connect("key-press-event", self._key)
        self.connect("focus-in-event", lambda *_x: self.hover is None and self.rects and self._set_hover(0))

    def set_items(self, items):
        self.items = list(items)
        self.hover = None
        describe(self, description="; ".join(f"{it[1]} {it[2]}" for it in self.items[:8]))
        self.queue_draw()

    def _key(self, _w, ev):
        if not self.rects:
            return False
        i = self.hover if self.hover is not None else -1
        if ev.keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self._set_hover((i + 1) % len(self.rects))
        elif ev.keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self._set_hover((i - 1) % len(self.rects))
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and i >= 0:
            if self.on_activate:
                self.on_activate(self.rects[i][4][3])
        else:
            return False
        return True

    def _hit(self, x, y):
        for i, (rx, ry, rw, rh, _p) in enumerate(self.rects):
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return i
        return None

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            if i is not None and i < len(self.rects):
                _s, lbl, sub, _p = self.rects[i][4]
                describe(self, description=f"{lbl}, {sub}")
            self.queue_draw()
        return False

    def _motion(self, _w, ev):
        self._set_hover(self._hit(ev.x, ev.y))

    def _click(self, _w, ev):
        i = self._hit(ev.x, ev.y)
        if i is not None and self.on_activate:
            self.on_activate(self.rects[i][4][3])

    def _tooltip(self, _w, x, y, _kb, tip):
        i = self._hit(x, y)
        if i is None:
            return False
        _s, lbl, sub, _p = self.rects[i][4]
        tip.set_text(f"{lbl}\n{sub}")
        return True

    def _draw(self, w, cr):
        a = w.get_allocation()
        self.rects = [(x, y, rw, rh, p[1]) for x, y, rw, rh, p in
                      _squarify([(it[0], (i, it)) for i, it in enumerate(self.items)], 0, 0, a.width, a.height)]
        for idx, (x, y, rw, rh, item) in enumerate(self.rects):
            color = TREEMAP_COLORS[idx % len(TREEMAP_COLORS)]
            gap = 2
            x0, y0, x1, y1 = x + gap / 2, y + gap / 2, max(x + gap / 2, x + rw - gap / 2), max(y + gap / 2, y + rh - gap / 2)
            r = min(6, (x1 - x0) / 3, (y1 - y0) / 3)
            cr.new_sub_path()
            cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
            cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
            cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
            cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.set_source_rgba(*rgb(color, 0.42 if idx == self.hover else 0.22))
            cr.fill_preserve()
            cr.set_source_rgba(*rgb(color, 0.9 if idx == self.hover else 0.55))
            cr.set_line_width(2 if idx == self.hover and self.has_focus() else 1)
            cr.stroke()
            if x1 - x0 > 64 and y1 - y0 > 30:
                cr.save()
                cr.rectangle(x0 + 4, y0, x1 - x0 - 8, y1 - y0)
                cr.clip()
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(Pango.FontDescription.from_string("Ubuntu Sans Bold 9"))
                layout.set_text(item[1], -1)
                cr.set_source_rgba(*rgb(THEME["text"]))
                cr.move_to(x0 + 8, y0 + 6)
                PangoCairo.show_layout(cr, layout)
                layout2 = PangoCairo.create_layout(cr)
                layout2.set_font_description(Pango.FontDescription.from_string(f"{MONO} 8"))
                layout2.set_text(item[2], -1)
                cr.set_source_rgba(*rgb(color))
                cr.move_to(x0 + 8, y0 + 22)
                PangoCairo.show_layout(cr, layout2)
                cr.restore()
        return False


def _set_values(self, *value_lists):
    """Replace a LineChart's history (e.g. stored health scores) instead of streaming."""
    for (_name, _color, buf), vals in zip(self.series, value_lists):
        vals = [float(v) for v in vals][-buf.maxlen:]
        buf.clear()
        if vals:
            buf.extend([vals[0]] * (buf.maxlen - len(vals)) + vals)
    self._started = True
    self._describe_latest()
    self.queue_draw()


LineChart.set_values = _set_values
