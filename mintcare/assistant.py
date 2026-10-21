"""Optional AI assistant (Claude). Off until you install AI support and add an API key.

- Uses the official `anthropic` SDK, installed into MintCare's own virtual environment
  (~/.local/share/mintcare/ai-venv) because Mint doesn't package it and pip can't install
  into the system Python.
- Your key is stored in ~/.config/mintcare/anthropic-key (readable only by you), or picked up
  from ANTHROPIC_API_KEY / an `ant auth login` profile.
- Nothing about your system is sent unless you tick "Include my system summary"; that summary has
  your computer name, user name and serial numbers removed.
"""

import json
import os
import sys
from pathlib import Path

from gi.repository import GLib

from .i18n import _
from .util import q

DATA = Path(GLib.get_user_data_dir()) / "mintcare"
VENV = DATA / "ai-venv"
KEY_FILE = Path(GLib.get_user_config_dir()) / "mintcare" / "anthropic-key"
MODEL = "claude-opus-5-5"
SYSTEM = ("You are MintCare's built-in helper for Linux Mint 22 (Ubuntu 24.04 'noble' base, Cinnamon desktop). "
          "Answer questions about the user's computer clearly and briefly, for a non-expert. Prefer Mint's own "
          "graphical tools and MintCare pages when they solve the problem (MintCare has pages for Diagnose & Fix, "
          "Optimise, Clean, Disk space, Software & updates, Packages, Flatpak apps, Processes, Hardware, Security, "
          "Troubleshoot, Boot & Startup, Backup and Maintenance). When a terminal command is needed, give it in a "
          "code block, say what it does, and flag anything that deletes data or needs sudo. Never suggest "
          "disabling security features as a fix. You can run MintCare's read-only checks with the "
          "list_checks and run_check tools when the answer depends on this PC's state; if the user hasn't "
          "allowed that, answer without them. If a system summary is attached, use it, but say when "
          "something can't be known from it.")


def _site_packages():
    lib = VENV / "lib"
    if not lib.is_dir():
        return None
    for p in lib.glob("python3*/site-packages"):
        return p
    return None


def _load_sdk():
    sp = _site_packages()
    if sp and str(sp) not in sys.path:
        sys.path.insert(0, str(sp))         # before the system packages: Mint's copies are too old
    try:
        import anthropic  # noqa: F401
        return anthropic
    except ImportError:
        return None


def sdk_available():
    return _load_sdk() is not None


def install_steps():
    """(root steps, user steps): python3-venv as root, then the venv + SDK as you."""
    root = [("ai-venv-pkg", "Install Python's venv module", "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv")]
    user = [("ai-sdk", "Install the Anthropic SDK for MintCare",
             f"mkdir -p {q(str(DATA))} && python3 -m venv {q(str(VENV))} && "
             f"{q(str(VENV / 'bin' / 'pip'))} install --quiet --upgrade anthropic && echo 'AI support installed'")]
    return root, user


def remove_step():
    return ("ai-remove", "Remove MintCare's AI support", f"rm -rf {q(str(VENV))}; rm -f {q(str(KEY_FILE))}")


def get_key():
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def set_key(key):
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip())
    os.chmod(KEY_FILE, 0o600)


def clear_key():
    KEY_FILE.unlink(missing_ok=True)


def has_credentials():
    return bool(get_key() or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").is_dir())


def system_summary():
    """A short, privacy-scrubbed description of this PC and its current problems."""
    from . import checks, report
    data = report.collect(private=True)
    lines = ["System: " + "; ".join(f"{k}: {v}" for k, v in data["system"])]
    lines.append(f"Health score {data['score']}/100.")
    for cat, title, status, summary in data["checks"]:
        if status != checks.OK:
            lines.append(f"- [{status}] {title}: {summary}")
    return report._scrub("\n".join(lines), True)


# Read-only tools. The list never changes during a conversation (a different tools array would
# invalidate earlier thinking blocks), so when checks aren't allowed the tool says so instead.
TOOLS = [
    {"name": "list_checks", "eager_input_streaming": True,
     "description": "List MintCare's health checks on this PC (id, title, area). Use it to find which check to run.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "run_check", "eager_input_streaming": True,
     "description": ("Run one of MintCare's read-only health checks on this PC and return its verdict, the "
                     "diagnostic command it uses and that command's output. It never changes anything."),
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["check_id"],
                      "properties": {"check_id": {"type": "string",
                                                  "description": "A check id from list_checks, e.g. bbr or smart."}}}},
]
MAX_ROUNDS = 8
# $ per million tokens: input, output, cache read, cache write (5-minute)
# shortcut: only the default model is priced; a fallback model's turns are shown as approximate
PRICES = {MODEL: (4.00, 20.00, 0.20, 5.00)}


def cost(usage, model=MODEL):
    """Dollar cost of one response's usage, or None when the model isn't priced here."""
    p = PRICES.get(model)
    if p is None:
        return None
    n = lambda attr: getattr(usage, attr, 0) or 0
    return (n("input_tokens") * p[0] + n("output_tokens") * p[1] + n("cache_read_input_tokens") * p[2]
            + n("cache_creation_input_tokens") * p[3]) / 1e6


def _private(text):
    """Remove names, drive serials and MAC addresses from tool output before it leaves the PC."""
    import re
    from . import hardware, report
    text = report._scrub(text, True)
    for d in hardware.drives():
        if d.get("serial"):
            text = text.replace(d["serial"], "[serial]")
    return re.sub(r"\b[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\b", "[mac]", text)


def run_tool(name, args, allowed):
    """(content, is_error) for one tool call. Inputs come from the model, so they are validated here."""
    from . import checks
    if not allowed:
        return ("The user hasn't allowed the assistant to run checks on this PC. Answer from what you know, "
                "or tell them they can tick 'Let Claude run MintCare's checks'."), True
    by_id = {c.id: c for c in checks.CHECKS}
    if name == "list_checks":
        return "\n".join(f"{c.id}\t{c.title}\t{c.category}" for c in checks.CHECKS), False
    if name == "run_check":
        cid = args.get("check_id") if isinstance(args, dict) else None
        if not isinstance(cid, str) or cid not in by_id:
            return json.dumps({"INVALID_INPUT": args, "valid_ids": sorted(by_id)}), True
        r = by_id[cid].run()
        parts = [f"Check: {by_id[cid].title}", f"Result: {r.status.upper()} - {r.summary}"]
        det = checks.details(cid)
        if det:
            cmd, out_, _acts = det
            parts += [f"Command: {cmd}", "Output:", out_[:6000] + ("\n[output shortened]" if len(out_) > 6000 else "")]
        return _private("\n".join(parts)), False
    return f"Unknown tool {name!r}", True


def echo_blocks(content):
    """The blocks to send back next time. After a mid-output fallback, only text survives from the
    declined part (before the last fallback block); everything after it is kept as returned."""
    cut = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    return [b for i, b in enumerate(content) if i >= cut or b.type == "text"]


def stream_reply(history, on_text, stop=lambda: False, allow_checks=False, effort="medium",
                 on_status=lambda text: None):
    """Send the conversation, stream the answer and run any checks Claude asks for.

    history: the conversation so far, ending with the new user turn; it is never edited.
    Returns {'messages', 'text', 'stop_reason', 'model', 'cost', 'approx', 'error'}, where
    'messages' are the turns to append to history (assistant turns as returned, tool results).
    """
    anthropic = _load_sdk()
    if anthropic is None:
        return {"text": "", "messages": [], "error": "AI support isn't installed."}
    key = get_key()
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    params = dict(model=MODEL, max_tokens=64000, system=SYSTEM, tools=TOOLS,
                  thinking={"type": "adaptive"}, output_config={"effort": effort},
                  betas=["server-side-fallback-2026-07-01"])
    new, text, total, approx = [], [], 0.0, False
    res = {"text": "", "messages": new, "stop_reason": None, "model": MODEL, "cost": 0.0, "approx": False,
           "error": None}
    on_status(_("Thinking…"))
    try:
        for _round in range(MAX_ROUNDS):
            messages = history + new
            try:
                stream_cm = client.beta.messages.stream(fallbacks="default", messages=messages, **params)
            except TypeError:                          # older SDK without the fallbacks argument
                stream_cm = client.beta.messages.stream(extra_body={"fallbacks": "default"}, messages=messages,
                                                        **params)
            try:
                with stream_cm as stream:
                    for chunk in stream.text_stream:
                        if stop():
                            break
                        text.append(chunk)
                        on_text(chunk)
                    final = stream.get_final_message()
            except ValueError:                          # tool input the SDK couldn't parse at all
                res["error"] = "Claude sent a malformed request to run a check. Please ask again."
                break
            c = cost(final.usage, final.model)
            approx = approx or c is None
            total += c or 0.0
            blocks = echo_blocks(final.content)
            new.append({"role": "assistant", "content": blocks})
            res.update(stop_reason=final.stop_reason, model=final.model)
            if final.stop_reason == "refusal":
                res["error"] = "Claude declined to answer this one. Try rephrasing the question."
                break
            calls = [b for b in blocks if b.type == "tool_use"]
            if final.stop_reason == "max_tokens" and calls:
                res["error"] = "The answer was cut off before a check could run."
                break
            if final.stop_reason != "tool_use" or not calls or stop():
                break
            results = []
            for b in calls:                             # all results go back in one user turn
                on_status(_('Running check: {0}').format((b.input or {}).get('check_id', b.name)))
                content, is_err = run_tool(b.name, b.input, allow_checks)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": content,
                                **({"is_error": True} if is_err else {})})
            new.append({"role": "user", "content": results})
            text.append("\n")
            on_text("\n")
        else:
            res["error"] = "Stopped after several checks without a final answer."
    except anthropic.AuthenticationError:
        res["error"] = "The API key was rejected. Check it in the Assistant settings."
    except anthropic.PermissionDeniedError:
        res["error"] = "This API key isn't allowed to use that model."
    except anthropic.RateLimitError:
        res["error"] = "Rate limited by the API — wait a moment and try again."
    except anthropic.APIStatusError as e:
        res["error"] = f"API error {e.status_code}: {getattr(e, 'message', e)}"
    except anthropic.APIConnectionError:
        res["error"] = "Couldn't reach the Claude API — check your internet connection."
    res.update(text="".join(text), cost=total, approx=approx)
    return res
