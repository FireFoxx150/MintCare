"""APT software-source health: parse .list and deb822 .sources files and spot common problems.

Problems found without root: sources that have never downloaded, PPAs built for another
Ubuntu release, duplicates, missing signing-key files. "Test sources" (admin) runs apt-get
update and maps its errors (404, NO_PUBKEY, no Release file) back to each source.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _
from .util import q, read

SOURCES_DIR = Path("/etc/apt/sources.list.d")
MAIN_LIST = Path("/etc/apt/sources.list")
LISTS_DIR = Path("/var/lib/apt/lists")
DISABLED_MARK = "# mintcare-disabled: "
OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
RANK = {OK: 0, INFO: 1, WARN: 2, FAIL: 3}


@dataclass
class Source:
    file: str
    line: int                  # 1-based line in a .list file, or paragraph start in .sources
    fmt: str                   # "list" | "deb822"
    type: str
    uri: str
    suite: str
    comps: list
    signed_by: str = ""
    enabled: bool = True
    mintcare_disabled: bool = False
    problems: list = field(default_factory=list)    # [(level, text)]

    @property
    def kind(self):
        u = self.uri
        if "linuxmint.com" in u:
            return "Linux Mint"
        if "ppa.launchpad" not in u and ("ubuntu.com" in u or re.search(r"/ubuntu/?$", u)):
            return "Ubuntu"                       # official archive or an Ubuntu mirror (…/ubuntu)
        if "ppa.launchpad" in u:
            return "PPA"
        return "Third-party"

    @property
    def label(self):
        m = re.search(r"ppa\.launchpad(?:content)?\.net/([^/]+)/([^/]+)", self.uri)
        if m:
            return f"ppa:{m.group(1)}/{m.group(2)}"
        host = re.sub(r"^\w+://", "", self.uri).split("/")[0]
        return host

    @property
    def health(self):
        if not self.enabled:
            return "disabled"
        return max([lvl for lvl, _t in self.problems] + [OK], key=RANK.get)

    def key(self):
        return (self.type, self.uri.rstrip("/"), self.suite)


def _parse_list(path):
    out = []
    for n, raw in enumerate(read(path).splitlines(), 1):
        line = raw.strip()
        mint_off = line.startswith(DISABLED_MARK)
        enabled = True
        if mint_off:
            line = line[len(DISABLED_MARK):]
            enabled = False
        elif line.startswith("#"):
            body = line.lstrip("#").strip()
            if re.match(r"deb(-src)?\s", body):
                line, enabled = body, False
            else:
                continue
        line = line.split("#", 1)[0].strip()
        if not re.match(r"deb(-src)?\s", line) or "cdrom:" in line:
            continue                              # install-media entries aren't real sources
        opts = ""
        m = re.match(r"(deb(?:-src)?)\s+\[([^\]]*)\]\s+(.*)", line)
        if m:
            typ, opts, rest = m.groups()
        else:
            typ, rest = line.split(None, 1)
        parts = rest.split()
        if len(parts) < 2:
            continue
        sb = re.search(r"signed-by=(\S+)", opts)
        out.append(Source(str(path), n, "list", typ, parts[0], parts[1], parts[2:],
                          sb.group(1) if sb else "", enabled, mint_off))
    return out


def _parse_deb822(path):
    out = []
    text = read(path)
    lines = text.splitlines()
    start = 1
    para = {}
    for n, raw in enumerate(lines + [""], 1):
        if not raw.strip():
            if para:
                types = para.get("types", "deb").split()
                uris = para.get("uris", "").split()
                suites = para.get("suites", "").split()
                comps = para.get("components", "").split()
                enabled = para.get("enabled", "yes").lower() not in ("no", "false", "0")
                mint_off = para.get("x-mintcare-disabled", "") == "yes"
                for t in types:
                    for u in uris:
                        for s_ in suites:
                            out.append(Source(str(path), start, "deb822", t, u, s_, comps,
                                              para.get("signed-by", "").split("\n")[0].strip(),
                                              enabled, mint_off))
            para, start = {}, n + 1
            continue
        if raw.startswith("#"):
            continue
        if raw[0].isspace() and para:
            continue                      # continuation lines (inline keys)
        k, _x, v = raw.partition(":")
        para[k.strip().lower()] = v.strip()
    return out


def sources():
    files = ([MAIN_LIST] if MAIN_LIST.exists() else []) + sorted(SOURCES_DIR.glob("*.list")) + \
        sorted(SOURCES_DIR.glob("*.sources"))
    out = []
    for f in files:
        out += _parse_deb822(f) if f.suffix == ".sources" else _parse_list(f)
    return out


def lists_name(uri, suite):
    """apt's on-disk name for a source's Release file prefix (see apt's URItoFileName)."""
    u = re.sub(r"^\w+://", "", uri).rstrip("/")
    u = re.sub(r"^[^@/]+@", "", u)                 # drop user:password@
    if suite.endswith("/"):
        base = f"{u}/{suite.rstrip('/')}" if suite != "/" else u
    else:
        base = f"{u}/dists/{suite}"
    return base.replace("/", "_")


def ubuntu_codename():
    for line in read("/etc/os-release").splitlines():
        if line.startswith("UBUNTU_CODENAME="):
            return line.split("=", 1)[1].strip('"')
    return ""


def _test_file():
    from . import maintenance
    return maintenance.STATE / "sources-test.json"


def last_test():
    try:
        return json.loads(_test_file().read_text())
    except (OSError, ValueError):
        return None


def save_test(output):
    """Parse apt-get update output into {'ts', 'errors': [{'uri','suite','text'}], 'warnings': [...]}."""
    errors, warns = [], []
    for line in output.splitlines():
        s = line.strip()
        m = re.match(r"Err:\d+\s+(\S+)\s+(\S+)", s)
        if m:
            errors.append({"uri": m.group(1), "suite": m.group(2), "text": ""})
            continue
        if errors and not errors[-1]["text"] and s and not s.startswith(("Hit:", "Get:", "Ign:", "Err:")):
            errors[-1]["text"] = s[:200]
        if s.startswith(("W:", "E:")):
            warns.append(s[:300])
    data = {"ts": time.time(), "errors": errors, "warnings": warns}
    try:
        f = _test_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data))
    except OSError:
        pass
    return data


def analyse(srcs=None):
    """Attach problems to every source. Returns the list."""
    srcs = sources() if srcs is None else srcs
    codename = ubuntu_codename()
    seen = {}
    test = last_test()
    for s_ in srcs:
        if not s_.enabled:
            continue
        name = lists_name(s_.uri, s_.suite)
        if s_.type == "deb" and not any((LISTS_DIR / f"{name}_{f}").exists() for f in ("InRelease", "Release")):
            s_.problems.append((FAIL, _("Has never downloaded successfully — apt can't reach it or it no longer exists")))
        if s_.kind == "PPA" and codename and s_.suite.split("-")[0] not in (codename,):
            s_.problems.append((WARN, _("Built for Ubuntu '{0}', but Mint uses '{1}' packages").format(s_.suite, codename)))
        if s_.signed_by and s_.signed_by.startswith("/") and not Path(s_.signed_by).exists():
            s_.problems.append((FAIL, _('Its signing key file {0} is missing').format(s_.signed_by)))
        k = s_.key()
        if k in seen:
            s_.problems.append((WARN, _('Duplicate of an entry in {0}').format(Path(seen[k]).name)))
        else:
            seen[k] = s_.file
        if test:
            for e in test["errors"]:
                if e["uri"].rstrip("/") == s_.uri.rstrip("/") and e["suite"] in (s_.suite, s_.suite + "/"):
                    s_.problems.append((FAIL, _("Failed in the last test: ") + (e["text"] or _("download error"))))
            for w in test["warnings"]:
                if "NO_PUBKEY" in w and s_.uri.rstrip("/") in w:
                    s_.problems.append((FAIL, _("Signing key is missing (NO_PUBKEY) — open Software Sources › Maintenance")))
    return srcs


# ------------------------------------------------------------------ root scripts

_PY_TOGGLE = r'''
import re, sys
path, line, fmt, enable = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4] == "1"
MARK = "# mintcare-disabled: "
lines = open(path).read().split("\n")
if fmt == "list":
    cur = lines[line - 1]
    if enable and cur.startswith(MARK):
        lines[line - 1] = cur[len(MARK):]
    elif not enable and not cur.lstrip().startswith("#"):
        lines[line - 1] = MARK + cur
else:
    i = line - 1
    end = i
    while end < len(lines) and lines[end].strip():
        end += 1
    para = [l for l in lines[i:end] if not re.match(r"(?i)(enabled|x-mintcare-disabled):", l)]
    if not enable:
        para += ["Enabled: no", "X-MintCare-Disabled: yes"]
    lines[i:end] = para
open(path, "w").write("\n".join(lines))
print(("Enabled" if enable else "Disabled") + " source in " + path)
'''


def toggle_script(src, enable):
    return (f"cp -a {q(src.file)} /var/lib/mintcare/ 2>/dev/null || true\n"
            f"python3 -c {q(_PY_TOGGLE)} {q(src.file)} {src.line} {src.fmt} {'1' if enable else '0'}")


def test_step():
    return ("sources-test", _("Test all software sources (apt-get update)"),
            "apt-get update 2>&1 || true")
