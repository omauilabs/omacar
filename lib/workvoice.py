"""What the car says about Work, and the sound of it.

Milestones only, at most two sentences, never a diff, a file or test output.
Piper speaks; it is a system program run as its own process (GPL-3.0, never
bundled), and a machine without it shows the line instead of failing.
"""
import os
import re
import subprocess

VOICE = os.environ.get("OMACAR_PIPER_VOICE", "en_US-hfc_female-medium")
LINES = {
    "confirm": "{p}: {instruction}. Start it?",
    "not_started": "Not started.",
    "started": "{p} session started.",
    "question": "{p} has a question: {q}",
    "gate_failed": "{p}: gate still failing, draft PR {n} opened.",
    "pr_opened": "{p}: gate passed, draft PR {n} opened.",
    "finished": "{p} finished.", "stuck": "{p} is stuck.",
    "stopped": "{p} stopped.", "interrupted": "{p} was interrupted.",
    "refused": "{p} tried something Work does not allow. {why}",
    "didnt_catch": "Didn't catch that.", "choose": "{a} or {b}?",
    "status": "{text}",
    "limit": "Usage limit reached — sessions paused until {t}.",
    "cap": "That's today's limit of sessions.",
    "offline": "Work is offline.", "no_signal": "No signal — saved.",
    "parked_only": "Work starts sessions here only while parked.",
    "busy": "Two sessions are running. It will start when one finishes.",
}
OFFLINE_LINES = {k: LINES[k] for k in ("no_signal", "offline", "not_started")}


def _first_sentence(s):
    m = re.match(r"(.+?[.?!])(\s|$)", s.strip())
    return m.group(1) if m else s.strip()


def _two(s):
    parts = re.split(r"(?<=[.?!])\s+", s.strip())
    return " ".join(parts[:2])[:220]


def line(e):
    tmpl = LINES.get(e.get("kind"))
    if not tmpl:
        return ""
    ch = list(e.get("choices") or ("", ""))
    text = tmpl.format(p=e.get("project", ""), instruction=e.get("instruction", ""),
                       q=_first_sentence(str(e.get("question", ""))), n=e.get("pr", ""),
                       why=_first_sentence(str(e.get("why", ""))), a=ch[0],
                       b=ch[1] if len(ch) > 1 else "", t=e.get("until", ""),
                       text=e.get("text", ""))
    return _two(text)


def quiet_now(quiet, hour, parked):
    start, end = quiet
    inside = (start <= hour or hour < end) if start > end else (start <= hour < end)
    return parked and inside


def render(text, out_path, run=subprocess.run):
    try:
        r = run(["piper", "--model", VOICE, "--output_file", out_path],
                input=text, text=True, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and os.path.exists(out_path)
