"""Optional AI assistant (Claude). Off until you install AI support and add an API key.

- Uses the official `anthropic` SDK, installed into MintCare's own virtual environment
  (~/.local/share/mintcare/ai-venv) because Mint doesn't package it and pip can't install
  into the system Python.
- Your key is stored in ~/.config/mintcare/anthropic-key (readable only by you), or picked up
  from ANTHROPIC_API_KEY / an `ant auth login` profile.
- Nothing about your system is sent unless you tick "Include my system summary"; that summary has
  your computer name, user name and serial numbers removed.
"""

import os
import sys
from pathlib import Path

from gi.repository import GLib

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
          "disabling security features as a fix. If a system summary is attached, use it, but say when "
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
        sys.path.append(str(sp))
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


def stream_reply(history, on_text, stop=lambda: False):
    """Send the conversation and stream the answer. history: [{'role','content'}].

    Returns {'text', 'stop_reason', 'model', 'error'}. on_text(chunk) is called per text delta.
    """
    anthropic = _load_sdk()
    if anthropic is None:
        return {"text": "", "error": "AI support isn't installed."}
    key = get_key()
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    params = dict(model=MODEL, max_tokens=16000, system=SYSTEM, messages=history,
                  betas=["server-side-fallback-2026-07-01"])
    text = []
    try:
        try:
            stream_cm = client.beta.messages.stream(fallbacks="default", **params)
        except TypeError:                              # older SDK without the fallbacks argument
            stream_cm = client.beta.messages.stream(extra_body={"fallbacks": "default"}, **params)
        with stream_cm as stream:
            for chunk in stream.text_stream:
                if stop():
                    break
                text.append(chunk)
                on_text(chunk)
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            return {"text": "".join(text), "stop_reason": "refusal", "model": final.model,
                    "error": "Claude declined to answer this one. Try rephrasing the question."}
        return {"text": "".join(text), "stop_reason": final.stop_reason, "model": final.model, "error": None}
    except anthropic.AuthenticationError:
        return {"text": "", "error": "The API key was rejected. Check it in the Assistant settings."}
    except anthropic.PermissionDeniedError:
        return {"text": "", "error": "This API key isn't allowed to use that model."}
    except anthropic.RateLimitError:
        return {"text": "".join(text), "error": "Rate limited by the API — wait a moment and try again."}
    except anthropic.APIStatusError as e:
        return {"text": "".join(text), "error": f"API error {e.status_code}: {getattr(e, 'message', e)}"}
    except anthropic.APIConnectionError:
        return {"text": "".join(text), "error": "Couldn't reach the Claude API — check your internet connection."}
