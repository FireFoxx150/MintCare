"""Health report export: a self-contained HTML page (and PDF via WebKit in the GUI).

With private=True (the default) the hostname, user name, home path and drive serial
numbers are removed, so the report can be posted on a forum when asking for help.
"""

import getpass
import html
import os
import platform
import shutil
import socket
import time
from pathlib import Path

from . import __version__, autostart, boot, checks, flatpaks, hardware, maintenance, tweaks
from .util import human, read, sysctl

STATUS_TEXT = {"ok": "OK", "info": "Tip", "warn": "Warning", "fail": "Problem"}


def score(results):
    n = {s: sum(1 for r in results if r.status == s) for s in ("fail", "warn", "info")}
    return max(0, 100 - n["fail"] * 15 - n["warn"] * 5 - n["info"]), n


def collect(private=True, progress=lambda _s: None):
    progress("Running checks…")
    results = [(c, c.run()) for c in checks.CHECKS]
    sc, counts = score([r for _c, r in results])
    osr = dict(l.split("=", 1) for l in read("/etc/os-release").splitlines() if "=" in l)
    cpu = next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines()
                if l.startswith("model name")), platform.processor() or "unknown")
    mem = next((int(l.split()[1]) * 1024 for l in read("/proc/meminfo").splitlines()
                if l.startswith("MemTotal")), 0)
    du = shutil.disk_usage("/")
    up = float(read("/proc/uptime", "0").split()[0])
    progress("Reading drives and sensors…")
    drives = [d for d in hardware.drives() if "error" not in d]
    if private:
        for d in drives:
            d["serial"] = ""
    progress("Reading boot and startup…")
    bt = boot.times()
    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "version": __version__,
        "private": private,
        "system": [
            ("Distribution", osr.get("PRETTY_NAME", "Linux").strip('"')),
            ("Desktop", os.environ.get("XDG_CURRENT_DESKTOP", "unknown").replace("X-", "")),
            ("Kernel", platform.release()),
            ("Processor", f"{cpu} ({os.cpu_count()} threads)"),
            ("Memory", human(mem)),
            ("Root disk", f"{human(du.used)} used of {human(du.total)} ({du.used * 100 // du.total}%)"),
            ("TCP congestion control", f"{sysctl('net.ipv4.tcp_congestion_control')} / "
                                       f"{sysctl('net.core.default_qdisc')}"),
            ("Swappiness", sysctl("vm.swappiness")),
            ("Uptime", f"{int(up // 86400)}d {int(up % 86400 // 3600)}h {int(up % 3600 // 60)}m"),
        ],
        "score": sc, "counts": counts,
        "checks": [(c.category, c.title, r.status, r.summary) for c, r in results],
        "drives": drives,
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
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>MintCare health report"
           f"</title><meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style>"
           "</head><body><main>",
           "<header><div class='logo'>M</div><div><h1>System health report</h1>"
           f"<div class='sub'>Generated {e(d['generated'])} by MintCare {e(d['version'])}"
           + (" · personal details removed" if d["private"] else "") + "</div></div></header>"]
    c = d["counts"]
    lvl = "ok" if d["score"] >= 85 else "warn" if d["score"] >= 60 else "fail"
    out.append(f"<div class='card score'><div class='ring'>{d['score']}<span class='sub'>/100</span></div>"
               f"<div>{_pill(lvl, 'healthy' if lvl == 'ok' else 'needs care' if lvl == 'warn' else 'at risk')}"
               f"<div class='sub' style='margin-top:6px'>{c['fail']} problems · {c['warn']} warnings · "
               f"{c['info']} tips from {len(d['checks'])} checks</div></div></div>")

    out.append("<h2>System</h2><div class='card'><table>")
    out += [f"<tr><th style='width:30%'>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in d["system"]]
    out.append("</table></div>")

    order = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
    out.append("<h2>Checks</h2><div class='card'><table><tr><th>Area</th><th>Check</th><th>Result</th><th></th></tr>")
    for cat, title, st, summary in sorted(d["checks"], key=lambda x: (order[x[2]], x[0])):
        out.append(f"<tr><td>{e(cat)}</td><td>{e(title)}</td><td>{e(summary)}</td><td>{_pill(st)}</td></tr>")
    out.append("</table></div>")

    if d["drives"]:
        out.append("<h2>Drives</h2>")
        for dr in d["drives"]:
            hl = {"ok": "ok", "info": "ok", "warn": "warn", "fail": "fail"}[dr["health"]]
            word = {"ok": "healthy", "warn": "check", "fail": "failing"}[hl]
            stats = " · ".join(f"{k.lower()} {v}" for k, v in dr["stats"])
            notes = "".join(f"<div class='sub'>{_pill(l)} {e(t)}</div>" for l, t in dr["notes"])
            serial = f" · S/N {e(dr['serial'])}" if dr.get("serial") else ""
            out.append(f"<div class='card'><b>{e(dr['model'])}</b> {_pill(hl, word)} <span class='sub'>"
                       f"{e(dr['kind'])} · {e(dr['device'])} · {human(dr['size'])}{serial}</span>"
                       f"<div class='mono sub' style='margin:6px 0'>{e(stats)}</div>{notes}</div>")

    if d["sensors"]:
        out.append("<h2>Temperatures</h2><div class='card'><table>")
        out += [f"<tr><td>{e(lbl)}</td><td class='mono'>{t:.0f}°C</td><td>{_pill(lv)}</td></tr>"
                for lbl, t, lv in d["sensors"]]
        out += [f"<tr><td>{e(comp)} throttling</td><td colspan='2'>{_pill(lv)} {e(txt)}</td></tr>"
                for comp, lv, txt in d["throttling"]]
        out.append("</table></div>")

    if d["boot"].get("total"):
        b = d["boot"]
        parts = " + ".join(f"{k} {v:.1f} s" for k, v in b.items() if k != "total")
        out.append(f"<h2>Boot</h2><div class='card'><b>{b['total']:.1f} s</b> <span class='sub'>({e(parts)})</span>"
                   "<table style='margin-top:8px'><tr><th>Slowest units</th><th>Time</th><th></th></tr>")
        out += [f"<tr><td class='mono'>{e(u)}</td><td class='mono'>{s_:.2f} s</td><td>"
                f"{_pill('warn', 'blocks boot') if crit else ''}</td></tr>" for u, s_, crit in d["blame"]]
        out.append("</table></div>")

    if d["tweaks"]:
        out.append("<h2>Active optimisations</h2><div class='card'><table>")
        out += [f"<tr><td>{e(cat)}</td><td>{e(t)}</td><td>{_pill('ok' if st == 'mintcare' else 'dim', 'MintCare' if st == 'mintcare' else 'system')}</td></tr>"
                for cat, t, st in d["tweaks"]]
        out.append("</table></div>")

    if d["startup"] or d["flatpaks"]:
        out.append("<h2>Apps</h2><div class='card'>")
        if d["startup"]:
            out.append(f"<p><b>Starting at login ({len(d['startup'])}):</b> {e(', '.join(d['startup']))}</p>")
        if d["flatpaks"]:
            out.append("<table><tr><th>Flatpak</th><th>Sandbox</th><th>Broad access</th></tr>")
            for name, rating, why in d["flatpaks"]:
                lv = {"strong": "ok", "moderate": "warn", "weak": "fail"}[rating]
                out.append(f"<tr><td>{e(name)}</td><td>{_pill(lv, rating)}</td><td>{e(', '.join(why) or '—')}</td></tr>")
            out.append("</table>")
        out.append("</div>")

    if d["history"]:
        out.append("<h2>Recent MintCare changes</h2><div class='card'><table>")
        for ev in d["history"]:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ev["ts"]))
            out.append(f"<tr><td class='mono sub'>{when}</td><td>{e(ev['title'])}</td>"
                       f"<td>{_pill('ok' if ev.get('ok', True) else 'fail', ev.get('kind', ''))}</td></tr>")
        out.append("</table></div>")

    out.append("<footer>Created with MintCare — clean, optimise and repair Linux Mint.</footer></main></body></html>")
    return _scrub("".join(out), d["private"])


def write_html(path, private=True, progress=lambda _s: None):
    data = collect(private, progress)
    Path(path).write_text(render_html(data))
    return data
