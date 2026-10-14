"""The gates: whether a Work session may make this one tool call.

Every call a session makes is judged here, from the PreToolUse hook in
worksession.py, before the permission system sees it. Nothing here asks the
model to behave: it reads the call and says yes or no. The spec's list
(doc/design/2026-10-13-work-voice-relay.md, section 3 and the amendment) is
this file; a change to one is a change to the other.

Pushing and opening the PR are the runner's, not the agent's, so the agent is
refused every `git push` and every `gh pr` that changes anything.
"""
import os
import re
import shlex
from dataclasses import dataclass

WRITE_TOOLS = {"Write": ("file_path",), "Edit": ("file_path",),
               "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path",)}
READ_TOOLS = {"Read": ("file_path",), "Glob": ("path",), "Grep": ("path",)}
FREE_TOOLS = {"AskUserQuestion", "TodoWrite", "ExitPlanMode", "EnterPlanMode",
              "WebFetch", "WebSearch", "Task", "Agent", "BashOutput", "KillShell",
              "Skill", "ToolSearch"}
DEVICES = ("/dev/ttyUSB", "/dev/ttyACM", "/dev/rfcomm")
BANNED_WORDS = {"sudo", "su", "doas", "systemctl", "ssh", "scp", "rsync", "sftp",
                "pacman", "yay", "paru", "apt", "apt-get", "dnf", "omacar",
                "shutdown", "reboot", "kill", "pkill", "killall", "hyprctl",
                "crontab", "tailscale"}
BANNED = [re.compile(p) for p in (
    r"^git\s+push\b", r"^git\b.*\s--force\b", r"^git\b.*\s-f\b.*\bpush\b",
    r"^git\s+(reset\s+--hard\s+origin|filter-branch|update-ref)\b",
    r"^git\s+(checkout|switch)\s+(main|master)\b",
    r"^gh\s+pr\s+(merge|ready|review|edit|create|close)\b",
    r"^gh\s+api\b.*-X\s*(POST|PATCH|PUT|DELETE)\b",
    r"^gh\s+(repo\s+delete|release)\b",
    r"^docker\s+(system|image|container|volume)\s+prune\b",
    r"^pip3?\s+install\b")]
HOSTED = re.compile(
    r"runs-on:\s*\[?\s*[\"']?(ubuntu|macos|windows)-"
    r"|runs-on:\s*[\"']?self-hosted[\"']?\s*$", re.M)
OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}


@dataclass(frozen=True)
class Ctx:
    worktree: str
    host: str
    home: str


def _real(p, ctx):
    return os.path.realpath(os.path.join(ctx.worktree, os.path.expanduser(p)))


def _inside(path, root):
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def _protected(ctx):
    h = ctx.home
    return [os.path.join(h, "Projects", "omacar"),
            os.path.join(h, "Projects", ".omacar-wt"),
            os.path.join(h, ".ssh"), os.path.join(h, ".config", "omacar"),
            os.path.join(h, ".local", "state", "omacar")]


def _is_protected(path, ctx):
    if "/share/assets/private" in path:
        return True
    return any(_inside(path, p) for p in _protected(ctx))


def _segments(command):
    """Split a shell command into simple commands, outside quotes, and follow
    `bash -c "..."`, `sh -c` and `$(...)` / backticks into their contents."""
    text = command.replace("\n", " ; ").replace("$(", " ( ").replace("`", " ( ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return [command.strip()]          # unbalanced quotes: judge it whole
    segs, cur = [], []
    for t in tokens:
        if t in OPERATORS or set(t) <= set(";&|()"):
            if cur: segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur: segs.append(cur)
    out = []
    for words in segs:
        while words and (words[0] == "env" or re.match(r"^\w+=", words[0])):
            words = words[1:]
        if len(words) >= 3 and words[0] in ("bash", "sh", "zsh") and words[1] == "-c":
            out.extend(_segments(words[2]))
            continue
        if words:
            out.append(" ".join(words))
    return out


def _bash(command, ctx):
    for d in DEVICES:
        if d in command:
            return "That would open the car's adapter, which Work never touches."
    for p in _protected(ctx) + ["/share/assets/private"]:
        for form in {p, p.replace(ctx.home, "~", 1)}:
            # A boundary after the path: ~/Projects/omacar must not catch the
            # sibling ~/Projects/omacar-work-src.
            if re.search(re.escape(form) + r"(?![\w.-])", command):
                return "That reaches a place Work sessions are kept out of."
    for seg in _segments(command):
        first = seg.split()[0] if seg.split() else ""
        if os.path.basename(first) in BANNED_WORDS:
            return f"Work sessions may not run {os.path.basename(first)}."
        for rx in BANNED:
            if rx.search(seg):
                return "Pushing, merging and pull requests are the runner's job, not the session's."
    return ""


def _new_text(tool, inp):
    if tool == "Write":
        return inp.get("content", "")
    if tool == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in inp.get("edits", []))
    return inp.get("new_string", "") or inp.get("new_source", "")


def judge(tool, inp, ctx):
    inp = inp or {}
    if tool in WRITE_TOOLS:
        path = _real(inp.get(WRITE_TOOLS[tool][0], ""), ctx)
        if not _inside(path, ctx.worktree):
            return False, "Work sessions write only inside their own worktree."
        if "/.github/workflows/" in path and HOSTED.search(_new_text(tool, inp)):
            return False, "CI runs on our own runners only; that adds a hosted one."
        return True, ""
    if tool in READ_TOOLS:
        raw = inp.get(READ_TOOLS[tool][0]) or ctx.worktree
        if _is_protected(_real(raw, ctx), ctx):
            return False, "That reaches a place Work sessions are kept out of."
        return True, ""
    if tool == "Bash":
        why = _bash(str(inp.get("command", "")), ctx)
        return (False, why) if why else (True, "")
    if tool in FREE_TOOLS:
        return True, ""
    return False, f"Work sessions may not use {tool}."
