"""Colour themes: Dark (MintCare Companion style), Light, High contrast, or follow the system."""

from .i18n import _

PALETTES = {
    "dark": {
        "bg": "#05070a", "panel": "#080c12", "card": "#0c121a", "line": "rgba(255,255,255,0.05)",
        "eline": "rgba(6,78,59,0.35)", "ink": "#ffffff", "emerald": "#10b981", "emerald4": "#34d399",
        "slate1": "#f1f5f9", "slate2": "#e2e8f0", "slate3": "#cbd5e1", "slate4": "#94a3b8", "slate5": "#64748b",
        "amber": "#fbbf24", "blue": "#60a5fa", "red": "#f87171", "orange": "#fb923c", "chipval": "#d1fae5",
        "switchoff": "#1e293b", "slider": "#e2e8f0", "checkbg": "#0f172a", "checkborder": "#334155",
        "logtext": "#a7f3d0",
    },
    "light": {
        "bg": "#eef2f5", "panel": "#ffffff", "card": "#ffffff", "line": "rgba(15,23,42,0.09)",
        "eline": "rgba(5,150,105,0.28)", "ink": "#0f172a", "emerald": "#10b981", "emerald4": "#047857",
        "slate1": "#0f172a", "slate2": "#1e293b", "slate3": "#334155", "slate4": "#475569", "slate5": "#64748b",
        "amber": "#b45309", "blue": "#1d4ed8", "red": "#b91c1c", "orange": "#c2410c", "chipval": "#065f46",
        "switchoff": "#cbd5e1", "slider": "#ffffff", "checkbg": "#ffffff", "checkborder": "#94a3b8",
        "logtext": "#065f46",
    },
    "contrast": {
        "bg": "#000000", "panel": "#000000", "card": "#000000", "line": "rgba(255,255,255,0.75)",
        "eline": "rgba(255,255,255,0.85)", "ink": "#ffffff", "emerald": "#22c55e", "emerald4": "#4ade80",
        "slate1": "#ffffff", "slate2": "#ffffff", "slate3": "#ffffff", "slate4": "#f1f5f9", "slate5": "#e2e8f0",
        "amber": "#fde047", "blue": "#93c5fd", "red": "#fca5a5", "orange": "#fdba74", "chipval": "#ffffff",
        "switchoff": "#3f3f46", "slider": "#ffffff", "checkbg": "#000000", "checkborder": "#ffffff",
        "logtext": "#ffffff",
    },
}
# colour-blind friendly: the Okabe-Ito set (blue = fine, orange = careful, vermilion = problem); no red/green pairs
PALETTES["colourblind"] = dict(PALETTES["dark"], emerald="#56b4e9", emerald4="#8ccdf0", amber="#e69f00",
                               red="#d55e00", orange="#e69f00", chipval="#d4ecfb", logtext="#bfe3f7")
STATUS = {
    "dark": {"ok": "#10b981", "warn": "#f59e0b", "fail": "#ef4444"},
    "light": {"ok": "#10b981", "warn": "#b45309", "fail": "#b91c1c"},
    "contrast": {"ok": "#22c55e", "warn": "#fde047", "fail": "#fca5a5"},
    "colourblind": {"ok": "#56b4e9", "warn": "#e69f00", "fail": "#d55e00"},
}
DARK_BASE = {"dark": True, "light": False, "contrast": True, "colourblind": True}
NAMES = [("system", _("Follow system")), ("dark", _("Dark")), ("light", _("Light")), ("contrast", _("High contrast")),
         ("colourblind", _("Colour-blind friendly"))]

CONTRAST_EXTRA = """
.card, .sidebar, .stat-tile, .sensor-tile, .profile-tile, .pill { border-width: 2px; }
.mintcare button { border: 2px solid @slate1; }
.mintcare button.primary { color: #000; }
.faint, .muted, .kicker, .app-sub { color: @slate1; }
*:focus { outline: 2px solid @amber; outline-offset: 1px; }
"""

RULES = """
.mintcare popover, .mintcare popover.background { background-color: @card; border: 1px solid @eline;
  border-radius: 12px; }
.mintcare popover radiobutton, .mintcare popover checkbutton { padding: 3px 0; color: @slate3; }
.mintcare popover radio:checked { background-color: @emerald; border-color: @emerald; color: #000;
  background-image: none; }
.mintcare radio { background-image: none; background-color: @checkbg; border: 1px solid @checkborder; }
.level-pill { margin-top: 2px; }
flowboxchild { padding: 0; }
.detail-panel { background-color: @panel; border: 1px solid @line; border-radius: 8px; padding: 8px 10px;
  margin: 8px 0 2px 40px; }
image.faint { color: @slate5; }
.card list row:hover image.faint { color: @emerald4; }
.flash, .card list row.flash { background-color: alpha(@emerald, 0.22); }
.mintcare entry { background-color: @checkbg; color: @slate2; border: 1px solid @line; border-radius: 8px;
  background-image: none; box-shadow: none; }
.mintcare entry:focus { border-color: @emerald; }
/* keyboard focus is always drawn, in every theme (the high-contrast theme overrides the colour) */
*:focus { outline: 2px solid @emerald; outline-offset: 1px; }
.mintcare popover list { background: transparent; }
.mintcare popover list row { border-radius: 8px; }
.mintcare popover list row:selected { background-color: alpha(@emerald, 0.15); }.mintcare, .mintcare .main { background-color: @bg; color: @slate3;
  font-family: "Inter", "Ubuntu Sans", "Ubuntu", sans-serif; }

/* header */
.mintcare headerbar { background-color: @panel; background-image: none; box-shadow: none;
  border: none; border-bottom: 1px solid @eline; padding: 6px 14px; min-height: 46px; }
.logo { background-color: @emerald; color: #000; font-weight: 900; font-size: 15pt;
  border-radius: 9px; min-width: 36px; min-height: 36px;
  box-shadow: 0 0 15px rgba(16,185,129,0.35); }
.app-title { color: @emerald4; font-weight: bold; font-size: 12pt; }
.app-sub, .kicker { color: @slate5; font-family: "JetBrains Mono", "Ubuntu Mono", monospace;
  font-size: 7.5pt; font-weight: bold; }
.chip-val { color: @chipval; font-family: "JetBrains Mono", "Ubuntu Mono", monospace;
  font-weight: bold; font-size: 9pt; }
.vsep { background-color: @eline; min-width: 1px; }

/* sidebar */
.sidebar { background-color: @panel; border: 1px solid @eline; border-radius: 12px;
  padding: 8px; margin: 14px 0 14px 14px; }
.sidebar list { background: transparent; }
.sidebar row { border-radius: 8px; padding: 9px 10px; margin: 1px 0; color: @slate4;
  border: 1px solid transparent; }
.sidebar row:hover { background-color: alpha(@ink, 0.03); color: @slate3; }
.sidebar row:selected { background-color: rgba(16,185,129,0.10); color: @emerald4;
  border-color: rgba(16,185,129,0.25); }
.sidebar row:selected image, .sidebar row:selected label { color: @emerald4; }
.nav-label { font-weight: 600; font-size: 10pt; }
.nav-badge { background-color: rgba(245,158,11,0.15); color: @amber; border-radius: 99px;
  padding: 0 7px; font-size: 7.5pt; font-weight: bold;
  font-family: "JetBrains Mono", "Ubuntu Mono", monospace; }
.side-foot { border-top: 1px solid @eline; padding-top: 10px; margin-top: 6px; }

/* content */
.page { padding: 12px 16px 14px 16px; }
.card { background-color: @card; border: 1px solid @line; border-radius: 12px; padding: 14px; }
.card.tight { padding: 4px 0; }
.card.ecard { border-color: @eline; }
.h1 { color: @slate1; font-weight: bold; font-size: 13pt; }
.h2 { color: @slate3; font-weight: bold; font-size: 10.5pt; }
.h3 { color: @slate2; font-weight: 600; font-size: 10pt; }
.muted { color: @slate4; font-size: 9.5pt; }
.faint { color: @slate5; font-size: 9pt; }
.mono { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; }
.big { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 22pt;
  font-weight: bold; color: @emerald4; }
.tile-ico { background-color: rgba(16,185,129,0.10); color: @emerald4; border-radius: 9px;
  border: 1px solid rgba(16,185,129,0.20); padding: 8px; }
.stat-tile { background-color: alpha(@ink, 0.04); border: 1px solid @line;
  border-radius: 8px; padding: 6px; }
.stat-val { color: @slate3; font-weight: 600; font-size: 9.5pt;
  font-family: "JetBrains Mono", "Ubuntu Mono", monospace; }
.legend-dot { border-radius: 99px; min-width: 8px; min-height: 8px; }
.kv-key { color: @slate5; font-size: 9pt; }
.kv-val { color: @slate2; font-size: 9pt; font-family: "JetBrains Mono", "Ubuntu Mono", monospace; }

/* pills */
.pill { border-radius: 99px; padding: 1px 8px; font-size: 7.5pt; font-weight: bold;
  font-family: "JetBrains Mono", "Ubuntu Mono", monospace; border: 1px solid transparent; }
.pill.ok   { background-color: rgba(16,185,129,0.10); color: @emerald4; border-color: rgba(16,185,129,0.25); }
.pill.info { background-color: rgba(59,130,246,0.10); color: @blue; border-color: rgba(59,130,246,0.25); }
.pill.warn { background-color: rgba(245,158,11,0.10); color: @amber; border-color: rgba(245,158,11,0.25); }
.pill.fail { background-color: rgba(239,68,68,0.10);  color: @red; border-color: rgba(239,68,68,0.25); }
.pill.blue { background-color: rgba(59,130,246,0.10); color: @blue; border-color: rgba(59,130,246,0.25); }
.pill.orange { background-color: rgba(249,115,22,0.10); color: @orange; border-color: rgba(249,115,22,0.25); }
.pill.dim  { background-color: rgba(148,163,184,0.08); color: @slate4; border-color: rgba(148,163,184,0.15); }
image.ok { color: @emerald4; } image.info { color: @blue; }
image.warn { color: @amber; } image.fail { color: @red; }

/* buttons */
.mintcare button { background-image: none; box-shadow: none; text-shadow: none;
  border-radius: 8px; padding: 6px 12px; font-weight: 600; font-size: 9pt; }
.mintcare button.ghost { background-color: rgba(16,185,129,0.10); color: @emerald4;
  border: 1px solid rgba(16,185,129,0.30); }
.mintcare button.ghost:hover { background-color: rgba(16,185,129,0.20); }
.mintcare button.primary { background-color: @emerald; color: #000; font-weight: bold;
  border: 1px solid @emerald; box-shadow: 0 4px 14px rgba(16,185,129,0.20); }
.mintcare button.primary:hover { background-color: @emerald4; }
.mintcare button.primary label, .mintcare button.ghost label { color: inherit; }
.mintcare button:disabled { opacity: 0.45; }
.mintcare headerbar button.titlebutton { background: transparent; border: none; padding: 4px;
  color: @slate4; }
.mintcare headerbar button.titlebutton:hover { color: @slate1; background-color: alpha(@ink, 0.06); }

/* lists inside cards */
.card list { background: transparent; }
.card list row { padding: 10px 14px; background: transparent; }
.card list row:hover { background-color: alpha(@ink, 0.02); }
.card list separator { background-color: @line; min-height: 1px; }

/* switches & checks */
.mintcare switch { background-color: @switchoff; background-image: none; border: 1px solid alpha(@ink, 0.08);
  border-radius: 99px; color: transparent; }
.mintcare switch:checked { background-color: @emerald; border-color: @emerald; }
.mintcare switch slider { background-color: @slider; background-image: none; border: none;
  border-radius: 99px; box-shadow: 0 1px 2px rgba(0,0,0,0.4); }
.mintcare switch:disabled { opacity: 0.55; }
.mintcare check { border-radius: 4px; background-image: none; background-color: @checkbg;
  border: 1px solid @checkborder; color: transparent; min-width: 16px; min-height: 16px; }
.mintcare check:checked { background-color: @emerald; border-color: @emerald; color: #000;
  -gtk-icon-source: -gtk-icontheme("object-select-symbolic"); }
.mintcare check:hover { border-color: @emerald; }

/* hardware & boot */
.tval { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 15pt; font-weight: bold; color: @slate3; }
.tval.ok { color: @emerald4; } .tval.warn { color: @amber; } .tval.fail { color: @red; }
.tval.info { color: @blue; }
.sensor-tile { background-color: alpha(@ink, 0.03); border: 1px solid @line; border-radius: 10px;
  padding: 10px 12px; }
.drive-card.warn { border-color: rgba(245,158,11,0.35); }
.drive-card.fail { border-color: rgba(239,68,68,0.45); }
.boot-big { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 26pt; font-weight: bold;
  color: @emerald4; }
.time-val { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-weight: bold; color: @slate3;
  font-size: 10pt; min-width: 64px; }
.time-val.warn { color: @amber; }
.unit-name { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; color: @slate2; font-size: 9.5pt;
  font-weight: 600; }
expander title { color: @slate4; }
expander title:hover { color: @slate3; }

.profile-tile { background-color: alpha(@ink, 0.03); border: 1px solid @line; border-radius: 12px;
  padding: 12px; }
.profile-tile:hover { border-color: rgba(16,185,129,0.30); }
.perm-row { padding: 6px 0; }
.fp-perms { background-color: alpha(@ink, 0.02); border-top: 1px solid @line; padding: 10px 14px 6px 58px; }

.mintcare stackswitcher button { background-color: alpha(@ink, 0.03); color: @slate4;
  border: 1px solid @line; padding: 6px 16px; }
.mintcare stackswitcher button:checked { background-color: rgba(16,185,129,0.12); color: @emerald4;
  border-color: rgba(16,185,129,0.30); }
.mintcare progressbar trough { background-color: alpha(@ink, 0.05); min-height: 6px;
  border-radius: 99px; border: none; }
.mintcare progressbar progress { background-color: @emerald; min-height: 6px; border-radius: 99px;
  border: none; }
.crumb { color: @slate3; font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 9pt; }
.dup-head { background-color: alpha(@ink, 0.02); }

/* footer, log, scrollbars */
.footer { background-color: @panel; border-top: 1px solid @eline; padding: 5px 16px; }
.footer label { font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 8pt; color: @slate5; }
.footer .status { color: @emerald4; }
.footer .status.bad { color: @red; }
textview.log, textview.log text { background-color: @panel; color: @logtext;
  font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 9pt; }
.mintcare scrollbar { background-color: transparent; border: none; }
.mintcare scrollbar slider { background-color: rgba(16,185,129,0.30); min-width: 6px;
  min-height: 6px; border: none; border-radius: 4px; }
.mintcare scrollbar slider:hover { background-color: rgba(16,185,129,0.60); }
"""


def system_is_dark(settings):
    """Read once at startup, before MintCare sets gtk-application-prefer-dark-theme itself."""
    theme = (settings.get_property("gtk-theme-name") or "").lower()
    return "dark" in theme or bool(settings.get_property("gtk-application-prefer-dark-theme"))


def resolve(name, system_dark=True):
    """'system' -> 'dark' or 'light'; unknown names fall back to dark."""
    if name == "system":
        return "dark" if system_dark else "light"
    return name if name in PALETTES else "dark"


def css(name):
    pal = PALETTES[name]
    defs = "".join(f"@define-color {k} {v};\n" for k, v in pal.items())
    return (defs + RULES + (CONTRAST_EXTRA if name == "contrast" else "")).encode()
