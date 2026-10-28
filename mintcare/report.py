"""Health report export: a self-contained HTML page (and PDF via WebKit in the GUI).

With private=True (the default) the hostname, user name, home path and drive serial
numbers are removed, so the report can be posted on a forum when asking for help.
"""

import getpass
import html
import os
import platform
import re
import shutil
import socket
import time
from pathlib import Path

from .i18n import _, current_language
from . import __version__, autostart, boot, checks, flatpaks, hardware, maintenance, tweaks
from .util import human, out, read, sysctl

STATUS_TEXT = {"ok": "OK", "info": _("Tip"), "warn": _("Warning"), "fail": _("Problem")}


def score(results):
    n = {s: sum(1 for r in results if r.status == s) for s in ("fail", "warn", "info")}
    return max(0, 100 - n["fail"] * 15 - n["warn"] * 5 - n["info"]), n


def collect(private=True, progress=lambda _s: None):
    progress(_("Running checks…"))
    results = [(c, c.run()) for c in checks.CHECKS]
    sc, counts = score([r for _c, r in results])
    osr = dict(l.split("=", 1) for l in read("/etc/os-release").splitlines() if "=" in l)
    cpu = next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines()
                if l.startswith("model name")), platform.processor() or "unknown")
    mem = next((int(l.split()[1]) * 1024 for l in read("/proc/meminfo").splitlines()
                if l.startswith("MemTotal")), 0)
    du = shutil.disk_usage("/")
    up = float(read("/proc/uptime", "0").split()[0])
    progress(_("Reading drives and sensors…"))
    drives = [d for d in hardware.drives() if "error" not in d]
    if private:
        for d in drives:
            d["serial"] = ""
    progress(_("Reading boot and startup…"))
    bt = boot.times()
    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "version": __version__,
        "private": private,
        "system": [
            (_("Distribution"), osr.get("PRETTY_NAME", "Linux").strip('"')),
            (_("Desktop"), os.environ.get("XDG_CURRENT_DESKTOP", "unknown").replace("X-", "")),
            (_("Kernel"), platform.release()),
            (_("Processor"), _("{0} ({1} threads)").format(cpu, os.cpu_count())),
            (_("Memory"), human(mem)),
            (_("Root disk"), _("{0} used of {1} ({2}%)").format(human(du.used), human(du.total), du.used * 100 // du.total)),
            (_("TCP congestion control"), f"{sysctl('net.ipv4.tcp_congestion_control')} / "
                                       f"{sysctl('net.core.default_qdisc')}"),
            (_("Swappiness"), sysctl("vm.swappiness")),
            (_("Uptime"), _("{0}d {1}h {2}m").format(int(up // 86400), int(up % 86400 // 3600), int(up % 3600 // 60))),
        ],
        "score": sc, "counts": counts,
        "checks": [(c.category, c.title, r.status, r.summary) for c, r in results],
        "drives": drives,
        "hardware": [(n, dt) for k, n, dt in hardware.inventory() if k != "drive"],
        "sensors": [(s["label"], s["temp"], hardware.temp_level(s)) for s in hardware.sensors()],
        "throttling": hardware.throttling(),
        "boot": bt,
        "blame": [(r["unit"], r["secs"], r["critical"]) for r in boot.blame(8)] if bt else [],
        "tweaks": [(t.category, t.title, t.state()) for t in tweaks.TWEAKS if t.available() and t.state() != "off"],
        "startup": [e["name"] for e in autostart.entries() if e["enabled"] and not e["essential"]],
        "flatpaks": [(a["name"], a["risk"][0], a["risk"][2]) for a in flatpaks.apps()] if flatpaks.available() else [],
        "history": [e for e in maintenance.history(25) if not (private and e.get("kind") == "trash")],
    }
    return data


def _scrub(text, private):
    if not private:
        return text
    text = re.sub(r"(\w+://)[^/@\s]+@", r"\1", text)   # credentials in URLs (apt sources)
    home, user, host = str(Path.home()), getpass.getuser(), socket.gethostname()
    for old, new in ((home, "~"), (f"/home/{user}", "~"), (host, "this-pc")):
        if old:
            text = text.replace(old, new)
    if len(user) > 2:
        text = text.replace(user, "user")
    return text


CSS = """
body{font-family:"Ubuntu Sans","Inter",system-ui,sans-serif;color:#0f172a;margin:0;background:#f3f6f8}
main{max-width:980px;margin:0 auto;padding:28px}
header{display:flex;align-items:center;gap:16px;margin-bottom:18px}
.logo{width:44px;height:44px;border-radius:10px;background:#10b981;color:#000;font-weight:900;
 font-size:24px;display:flex;align-items:center;justify-content:center}
h1{margin:0;font-size:22px} h2{font-size:15px;margin:22px 0 8px;color:#047857;text-transform:uppercase;
 letter-spacing:.06em}
.sub{color:#64748b;font-size:13px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px;margin-bottom:10px}
.score{display:flex;gap:24px;align-items:center}
.ring{font:700 34px "JetBrains Mono","Ubuntu Mono",monospace;color:#047857}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;border-bottom:1px solid #eef2f5;text-align:left;vertical-align:top}
th{color:#64748b;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font:700 10.5px "JetBrains Mono",monospace}
.ok{background:#d1fae5;color:#065f46}.info{background:#dbeafe;color:#1e40af}
.warn{background:#fef3c7;color:#92400e}.fail{background:#fee2e2;color:#991b1b}
.dim{background:#f1f5f9;color:#475569}
.mono{font-family:"JetBrains Mono","Ubuntu Mono",monospace}
footer{color:#94a3b8;font-size:11px;margin-top:24px}
@media print{body{background:#fff}main{padding:0}.card{break-inside:avoid}}
"""


def _pill(level, text=None):
    return f'<span class="pill {level}">{html.escape(text or STATUS_TEXT.get(level, level))}</span>'


def render_html(d):
    e = html.escape
    out = [f"<!doctype html><html lang='{html.escape(current_language())}'><head><meta charset='utf-8'><title>{_('MintCare health report')}"
           f"</title><meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style>"
           "</head><body><main>",
           f"<header><div class='logo'>M</div><div><h1>{_('System health report')}</h1>"
           f"<div class='sub'>{_('Generated {0} by MintCare {1}').format(e(d['generated']), e(d['version']))}"
           + (_(" · personal details removed") if d["private"] else "") + "</div></div></header>"]
    c = d["counts"]
    lvl = "ok" if d["score"] >= 85 else "warn" if d["score"] >= 60 else "fail"
    out.append(f"<div class='card score'><div class='ring'>{d['score']}<span class='sub'>/100</span></div>"
               f"<div>{_pill(lvl, _('healthy') if lvl == 'ok' else _('needs care') if lvl == 'warn' else _('at risk'))}"
               f"<div class='sub' style='margin-top:6px'>{_('{0} problems · {1} warnings · {2} tips from {3} checks').format(c['fail'], c['warn'], c['info'], len(d['checks']))}</div></div></div>")

    out.append(f"<h2>{_('System')}</h2><div class='card'><table>")
    out += [f"<tr><th style='width:30%'>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in d["system"]]
    out.append("</table></div>")

    if d["hardware"]:
        out.append(f"<h2>{_('Hardware')}</h2><div class='card'><table>")
        out += [f"<tr><td>{e(n)}</td><td class='sub'>{e(dt)}</td></tr>" for n, dt in d["hardware"]]
        out.append("</table></div>")

    order = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
    out.append(f"<h2>{_('Checks')}</h2><div class='card'><table><tr><th>{_('Area')}</th><th>{_('Check')}</th><th>{_('Result')}</th><th></th></tr>")
    for cat, title, st, summary in sorted(d["checks"], key=lambda x: (order[x[2]], x[0])):
        out.append(f"<tr><td>{e(cat)}</td><td>{e(title)}</td><td>{e(summary)}</td><td>{_pill(st)}</td></tr>")
    out.append("</table></div>")

    if d["drives"]:
        out.append(f"<h2>{_('Drives')}</h2>")
        for dr in d["drives"]:
            hl = {"ok": "ok", "info": "ok", "warn": "warn", "fail": "fail"}[dr["health"]]
            word = {"ok": _("healthy"), "warn": _("check"), "fail": _("failing")}[hl]
            stats = " · ".join(f"{k.lower()} {v}" for k, v in dr["stats"])
            notes = "".join(f"<div class='sub'>{_pill(l)} {e(t)}</div>" for l, t in dr["notes"])
            serial = f" · {_('S/N')} {e(dr['serial'])}" if dr.get("serial") else ""
            out.append(f"<div class='card'><b>{e(dr['model'])}</b> {_pill(hl, word)} <span class='sub'>"
                       f"{e(dr['kind'])} · {e(dr['device'])} · {human(dr['size'])}{serial}</span>"
                       f"<div class='mono sub' style='margin:6px 0'>{e(stats)}</div>{notes}</div>")

    if d["sensors"]:
        out.append(f"<h2>{_('Temperatures')}</h2><div class='card'><table>")
        out += [f"<tr><td>{e(lbl)}</td><td class='mono'>{t:.0f}°C</td><td>{_pill(lv)}</td></tr>"
                for lbl, t, lv in d["sensors"]]
        out += [f"<tr><td>{_('{0} throttling').format(e(comp))}</td><td colspan='2'>{_pill(lv)} {e(txt)}</td></tr>"
                for comp, lv, txt in d["throttling"]]
        out.append("</table></div>")

    if d["boot"].get("total"):
        b = d["boot"]
        parts = " + ".join(f"{k} {v:.1f} s" for k, v in b.items() if k != "total")
        out.append(f"<h2>{_('Boot')}</h2><div class='card'><b>{b['total']:.1f} s</b> <span class='sub'>({e(parts)})</span>"
                   f"<table style='margin-top:8px'><tr><th>{_('Slowest units')}</th><th>{_('Time')}</th><th></th></tr>")
        out += [f"<tr><td class='mono'>{e(u)}</td><td class='mono'>{s_:.2f} s</td><td>"
                f"{_pill('warn', _('blocks boot')) if crit else ''}</td></tr>" for u, s_, crit in d["blame"]]
        out.append("</table></div>")

    if d["tweaks"]:
        out.append(f"<h2>{_('Active optimisations')}</h2><div class='card'><table>")
        out += [f"<tr><td>{e(cat)}</td><td>{e(t)}</td><td>{_pill('ok' if st == 'mintcare' else 'dim', 'MintCare' if st == 'mintcare' else _('system'))}</td></tr>"
                for cat, t, st in d["tweaks"]]
        out.append("</table></div>")

    if d["startup"] or d["flatpaks"]:
        out.append(f"<h2>{_('Apps')}</h2><div class='card'>")
        if d["startup"]:
            out.append(f"<p><b>{_('Starting at login ({0}):').format(len(d['startup']))}</b> {e(', '.join(d['startup']))}</p>")
        if d["flatpaks"]:
            out.append(f"<table><tr><th>Flatpak</th><th>{_('Sandbox')}</th><th>{_('Broad access')}</th></tr>")
            for name, rating, why in d["flatpaks"]:
                lv = {"strong": "ok", "moderate": "warn", "weak": "fail"}[rating]
                out.append(f"<tr><td>{e(name)}</td><td>{_pill(lv, _(rating))}</td><td>{e(', '.join(why) or '—')}</td></tr>")
            out.append("</table>")
        out.append("</div>")

    if d["history"]:
        out.append(f"<h2>{_('Recent MintCare changes')}</h2><div class='card'><table>")
        for ev in d["history"]:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ev["ts"]))
            out.append(f"<tr><td class='mono sub'>{when}</td><td>{e(ev['title'])}</td>"
                       f"<td>{_pill('ok' if ev.get('ok', True) else 'fail', ev.get('kind', ''))}</td></tr>")
        out.append("</table></div>")

    out.append(f"<footer>{_('Created with MintCare — clean, optimise and repair Linux Mint.')}</footer></main></body></html>")
    return _scrub("".join(out), d["private"])


def write_html(path, private=True, progress=lambda _s: None):
    data = collect(private, progress)
    Path(path).write_text(render_html(data))
    return data


def write_pdf(page, path, timeout=60):
    """Render HTML to PDF with WebKit (for the scheduled check-up, which has no window).

    Runs its own GTK main loop, so only call it outside the GUI. Returns True on success.
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import GLib, Gtk, WebKit2
    except (ValueError, ImportError):
        return False
    if not Gtk.init_check()[0]:
        return False
    state = {"ok": False}
    win = Gtk.OffscreenWindow()
    view = WebKit2.WebView()
    win.add(view)
    win.set_default_size(980, 1300)
    win.show_all()
    loop = GLib.MainLoop()

    def loaded(v, ev):
        if ev != WebKit2.LoadEvent.FINISHED:
            return
        op = WebKit2.PrintOperation.new(v)
        ps = Gtk.PrintSettings()
        ps.set_printer("Print to File")
        ps.set(Gtk.PRINT_SETTINGS_OUTPUT_FILE_FORMAT, "pdf")
        ps.set(Gtk.PRINT_SETTINGS_OUTPUT_URI, Path(path).absolute().as_uri())
        op.set_print_settings(ps)
        setup = Gtk.PageSetup()
        setup.set_paper_size(Gtk.PaperSize.new(Gtk.PAPER_NAME_A4))
        op.set_page_setup(setup)
        op.connect("finished", lambda *_x: (state.update(ok=True), loop.quit()))
        op.connect("failed", lambda *_x: loop.quit())
        op.print_()
    view.connect("load-changed", loaded)
    view.load_html(page, "file:///")
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()
    win.destroy()
    return state["ok"] and Path(path).exists()


def default_folder():
    from gi.repository import GLib
    docs = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or str(Path.home())
    return str(Path(docs) / "MintCare reports")


def save_scheduled(fmt="pdf", folder=None, keep=10, private=True):
    """Write a dated report into the reports folder and prune old ones. Returns the path or None."""
    folder = Path(folder or default_folder())
    folder.mkdir(parents=True, exist_ok=True)
    page = render_html(collect(private))
    stamp = time.strftime("%Y-%m-%d_%H-%M")
    path = None
    if fmt == "pdf":
        cand = folder / f"MintCare-report-{stamp}.pdf"
        if write_pdf(page, cand):
            path = cand
    if path is None:
        path = folder / f"MintCare-report-{stamp}.html"
        path.write_text(page)
    reports = sorted(folder.glob("MintCare-report-*"), reverse=True)
    for old in reports[keep:]:
        old.unlink(missing_ok=True)
    return str(path)


def _scrub_log(text):
    """Log text with names removed, plus IP and hardware (MAC) addresses."""
    text = _scrub(text, True)
    text = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "[ip]", text)
    return re.sub(r"\b[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\b", "[mac]", text)


def write_support_bundle(path, progress=lambda _s: None):
    """A zip for asking for help: the health report, hardware list and recent activity, all with personal details removed."""
    import zipfile
    from . import maintenance
    page = render_html(collect(True, progress))
    hw = "\n".join(f"{kind:9} {name}" + (f"  ({detail})" if detail else "")
                   for kind, name, detail in hardware.inventory())
    activity = "\n".join(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ev.get('ts', 0)))}  {ev.get('kind', '')}  "
                         f"{'ok' if ev.get('ok', True) else 'failed'}  {ev.get('title', '')}"
                         for ev in maintenance.history(limit=300)) or "No activity recorded yet."
    journal = _scrub_log(out(["journalctl", "-b", "-p", "warning", "--no-pager", "-n", "300"], timeout=30))
    readme = ("MintCare support bundle\n"
              "Send this file when asking for help. Computer name, user name, home folder paths and drive\n"
              "serial numbers are removed.\n\n"
              "MintCare-report.html   health report (open in a web browser)\n"
              "hardware.txt           the parts MintCare can see\n"
              "activity.txt           recent cleaning, tweaks and maintenance, newest first\n"
              "journal.txt            warnings and errors from this start-up (last 300), addresses removed\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("MintCare-report.html", _scrub(page, True))
        z.writestr("hardware.txt", _scrub(hw, True))
        z.writestr("activity.txt", _scrub(activity, True))
        z.writestr("journal.txt", journal or "No warnings or errors recorded in this start-up.")
        z.writestr("README.txt", readme)
    return path
