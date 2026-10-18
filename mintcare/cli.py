"""Command-line interface: scripting and headless use."""

import argparse
import sys

from . import __version__, checks, cleaner, tweaks
from .runner import run_steps
from .util import human

COLORS = {"ok": "\033[32m", "info": "\033[34m", "warn": "\033[33m", "fail": "\033[31m"}
SYMBOL = {"ok": "✔", "info": "i", "warn": "!", "fail": "✖"}
RESET = "\033[0m"


def c(status, text):
    return f"{COLORS[status]}{text}{RESET}" if sys.stdout.isatty() else text


def cmd_scan(args):
    results = []
    for chk in checks.CHECKS:
        r = chk.run()
        results.append((chk, r))
        fix = f"  [fix: {r.fix.label}]" if r.fix and r.status != "ok" else ""
        print(f" {c(r.status, SYMBOL[r.status])} {chk.title:<28} {r.summary}{fix}")
    bad = [(k, r) for k, r in results if r.status in ("warn", "fail")]
    print(f"\n{len(bad)} issue(s) found.")
    if args.fix:
        fixable = [(k, r) for k, r in bad if r.fix and r.fix.kind in ("root", "user", "tweak")]
        root = [(k.id, k.title, r.fix.payload) for k, r in fixable if r.fix.kind == "root"]
        tw = [(k, tweaks.BY_ID[r.fix.payload]) for k, r in fixable if r.fix.kind == "tweak"]
        root += [("t-" + t.id, k.title, t.apply_script()) for k, t in tw if not t.user]
        user = [(k.id, k.title, r.fix.payload) for k, r in fixable if r.fix.kind == "user"]
        user += [("t-" + t.id, k.title, t.apply_script()) for k, t in tw if t.user]
        run_steps(user, root=False)
        run_steps(root, root=True)
    return 1 if any(r.status == "fail" for _x, r in results) else 0


def cmd_tweaks(args):
    if not args.apply and not args.revert:
        for t in tweaks.TWEAKS:
            st = t.state() if t.available() else "n/a"
            rec = "*" if t.recommended else " "
            print(f" {rec} {t.id:<12} {st:<9} {t.title}")
        print("\n* = recommended.  Use --apply ID... / --apply recommended / --revert ID...")
        print("Profiles: " + ", ".join(f"profile:{p.id}" for p in tweaks.PROFILES) +
              "  (e.g. --apply profile:gaming)")
        return 0
    ids = args.apply or args.revert
    if ids == ["recommended"]:
        ids = [t.id for t in tweaks.TWEAKS if t.recommended and t.available() and t.state() == "off"]
    elif len(ids) == 1 and ids[0].startswith("profile:"):
        prof = tweaks.PROFILE_BY_ID.get(ids[0][8:])
        if not prof:
            sys.exit("Profiles: " + ", ".join(p.id for p in tweaks.PROFILES))
        ids = [i for i in prof.tweaks if tweaks.BY_ID[i].available() and tweaks.BY_ID[i].state() == "off"]
        if not ids:
            print(f"Everything in the {prof.title} profile is already active.")
            return 0
    unknown = [i for i in ids if i not in tweaks.BY_ID]
    if unknown:
        sys.exit(f"Unknown tweak(s): {', '.join(unknown)}")
    if args.apply:
        steps = [(tweaks.BY_ID[i], tweaks.BY_ID[i].apply_script()) for i in ids]
    else:
        steps = [(tweaks.BY_ID[i], tweaks.BY_ID[i].revert_script()) for i in ids if tweaks.BY_ID[i].revert]
    res = {}
    for as_root in (False, True):          # desktop settings as you, system tweaks with one password prompt
        batch = [(t.id, t.title, script) for t, script in steps if t.user != as_root]
        if batch:
            res.update(run_steps(batch, root=as_root))
    return 0 if all(rc == 0 for rc in res.values()) else 1


def cmd_clean(args):
    items = [i for i in cleaner.ITEMS if i.available()]
    for i in items:
        size, detail = i.scan()
        mark = "x" if i.default else " "
        print(f" [{mark}] {i.id:<11} {human(size) if size else '—':>9}  {i.title}: {detail}")
    if not args.run:
        print("\nRun with --run to clean the [x] items, or --run ID... for specific ones.")
        return 0
    chosen = [i for i in items if (i.id in args.run if args.run != ["default"] else i.default)]
    run_steps([(i.id, i.title, i.user_script) for i in chosen if i.user_script], root=False)
    run_steps([(i.id, i.title, i.root_script) for i in chosen if i.root_script], root=True)
    return 0


def cmd_hardware(args):
    from . import hardware, boot
    for kind, name, detail in hardware.inventory():
        print(f"{kind:9} {name}" + (f"  ({detail})" if detail else ""))
    print()
    print("Drives:")
    for d in hardware.drives():
        if "error" in d:
            print("  " + d["error"])
            continue
        st = "  ".join(f"{k.lower()} {v}" for k, v in d["stats"])
        print(f" {c(d['health'], SYMBOL[d['health']])} {d['device']:<13} {d['kind']:<4} {d['model']}  {st}")
        for lvl, t in d["notes"]:
            print(f"      {c(lvl, SYMBOL[lvl])} {t}")
    print("\nTemperatures:")
    for s_ in hardware.sensors():
        lvl = hardware.temp_level(s_)
        print(f" {c(lvl, SYMBOL[lvl])} {s_['label']:<26} {s_['temp']:5.1f}°C")
    for comp, lvl, t in hardware.throttling():
        print(f" {c(lvl, SYMBOL[lvl])} {comp}: {t}")
    t = boot.times()
    if t.get("total"):
        print(f"\nBoot: {t['total']:.1f} s  (" +
              ", ".join(f"{k} {v:.1f}s" for k, v in t.items() if k != "total") + ")")
        for r in boot.blame(8):
            flag = " [blocks boot]" if r["critical"] else ""
            print(f"   {r['secs']:6.2f}s  {r['unit']}{flag}")
    return 0


def cmd_space(args):
    from . import space
    root = args.path or space.HOME
    tree, big = space.scan_tree(root)
    print(f"{root}: {human(tree.size)} in {tree.files:,} files\n")
    for c in tree.children[:15]:
        pct = 100 * c.size / tree.size if tree.size else 0
        print(f" {human(c.size):>9} {pct:5.1f}%  {c.name}{'/' if c.is_dir else ''}")
    print("\nLargest files:")
    for size, path, _m in big[:15]:
        print(f" {human(size):>9}  {path}")
    snaps, note = space.snapshots()
    print(f"\nTimeshift: {len(snaps)} snapshot(s)" + (f" — {note}" if note else ""))
    return 0


def cmd_maintain(args):
    from . import maintenance
    rep = maintenance.run_maintenance(notify=not args.no_notify, on_line=print)
    return 1 if rep["issues"] else 0


def cmd_report(args):
    from . import report
    out = args.output or f"MintCare-report-{__import__('time').strftime('%Y-%m-%d')}.html"
    report.write_html(out, private=not args.personal, progress=lambda m: print(m))
    print(f"Saved {out}")
    return 0


def cmd_backup(args):
    from . import backup
    r = backup.run_backup(progress=lambda pct, text: print(f"\r{text}", end="", flush=True))
    print()
    print(f"Backup: {r['files']} changed file(s) -> {r['snapshot']}" if r["ok"] else f"Backup failed: {r['error']}")
    return 0 if r["ok"] else 1


def main(argv):
    ap = argparse.ArgumentParser(prog="mintcare", description="Clean, optimise and repair Linux Mint.")
    ap.add_argument("--version", action="version", version=f"MintCare {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="diagnose the system")
    s.add_argument("--fix", action="store_true", help="automatically fix what can be fixed")
    t = sub.add_parser("tweaks", help="list, apply or revert optimisations")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--apply", nargs="+", metavar="ID")
    g.add_argument("--revert", nargs="+", metavar="ID")
    sub.add_parser("hardware", help="disk SMART health, temperatures and boot time")
    sub.add_parser("backup", help="back up your personal folders now (used by the backup timer)")
    rp = sub.add_parser("report", help="export an HTML health report")
    rp.add_argument("-o", "--output", help="file to write (default: MintCare-report-DATE.html)")
    rp.add_argument("--personal", action="store_true", help="keep hostname, user name and serial numbers")
    mt = sub.add_parser("maintain", help="scheduled check-up: scan, safe clean, notify")
    mt.add_argument("--no-notify", action="store_true")
    sp = sub.add_parser("space", help="what's using disk space")
    sp.add_argument("path", nargs="?", help="folder to analyse (default: your home)")
    cl = sub.add_parser("clean", help="show or remove junk")
    cl.add_argument("--run", nargs="*", metavar="ID",
                    help="clean the default items (no IDs) or the listed ones")
    a = ap.parse_args(argv)
    if a.cmd == "clean" and a.run == []:
        a.run = ["default"]
    return {"scan": cmd_scan, "tweaks": cmd_tweaks, "clean": cmd_clean, "hardware": cmd_hardware, "space": cmd_space,
            "maintain": cmd_maintain, "report": cmd_report, "backup": cmd_backup}[a.cmd](a)
