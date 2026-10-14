"""The gates: whether a Work session may make this one tool call.

Every call a session makes is judged here, from the PreToolUse hook in
worksession.py, before the permission system sees it. The wall is Claude
Code's own OS sandbox (set up in worksession.py); this file is the first,
cheaper line: it refuses the common forms early and hands the car a sentence
to say. It does not try to stop every way a shell can write outside the
worktree, or a script the session wrote itself: that is the sandbox's job.
The spec's list (doc/design/2026-10-13-work-voice-relay.md, section 3 and the
amendment) is this file; a change to one is a change to the other.

Pushing and opening the PR are the runner's, not the agent's, so the agent is
refused every `git push` and every `gh pr` / `gh api` that changes anything.
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
DEVICE_RX = re.compile(r"/dev/(tty|serial|rfcomm)")
BANNED_WORDS = {"sudo", "su", "doas", "systemctl", "ssh", "scp", "rsync", "sftp",
                "pacman", "yay", "paru", "apt", "apt-get", "dnf", "omacar",
                "shutdown", "reboot", "kill", "pkill", "killall", "hyprctl",
                "crontab", "tailscale", "pipx", "systemd-run", "loginctl", "at"}
# Inline code ("python3 -c ...") is searched for these as whole words; `su` and
# `at` are left out because they are ordinary English inside a string.
INLINE_WORDS = BANNED_WORDS - {"su", "at"}
BANNED = [re.compile(p) for p in (
    r"^git\s+push\b", r"^git\b.*\s--force\b", r"^git\b.*\s-f\b.*\bpush\b",
    r"^git\s+(reset\s+--hard\s+origin|filter-branch|update-ref)\b",
    r"^gh\s+pr\s+(merge|ready|review|edit|create|close)\b",
    r"^gh\s+(repo\s+delete|release)\b",
    r"^docker\b.*\bprune\b")]
KEYWORDS = {"if", "then", "elif", "else", "fi", "while", "until", "do", "done",
            "esac", "{", "}", "!", "time"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Wrapper commands, and the options of each that take a separate argument.
WRAPPERS = {"command": (), "builtin": (), "exec": ("-a",), "nohup": (),
            "nice": ("-n",), "timeout": ("-s", "-k", "--signal", "--kill-after"),
            "env": ("-u", "-C", "--unset", "--chdir"),
            "xargs": ("-I", "-n", "-L", "-P", "-d", "-E", "-s", "-a"),
            "stdbuf": ("-i", "-o", "-e"), "setsid": (), "unbuffer": (),
            "ionice": ("-c", "-n", "-p")}
GIT_GLOBAL_ARGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
HOSTED_PREFIX = ("ubuntu-", "macos-", "windows-")
OPERATORS = {";", "&&", "||", "|", "&", "(", ")"}
UNREADABLE = "That command could not be read safely."
KEPT_OUT = "That reaches a place Work sessions are kept out of."
NO_DEVICE = "That would open the car's adapter, which Work never touches."
RUNNER_JOB = "Pushing, merging and pull requests are the runner's job, not the session's."


@dataclass(frozen=True)
class Ctx:
    worktree: str
    host: str
    home: str


# -- paths -------------------------------------------------------------------

def _expand(s, ctx):
    """Replace $HOME, ${HOME} and a word-leading ~ with the session's home."""
    s = re.sub(r"\$\{HOME\}|\$HOME(?!\w)", lambda m: ctx.home, s)
    return re.sub(r"(?<![^\s=:\"'(;|&<>])~(?=/|$|[\s\"'):;|&])", lambda m: ctx.home, s)


def _real(p, ctx):
    return os.path.realpath(os.path.join(ctx.worktree, _expand(p, ctx)))


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


def _root_reason(path, ctx):
    """Why a Read/Glob/Grep rooted at `path` is refused: a device, a protected
    place, or a directory that contains one (searching ~ would find it)."""
    if DEVICE_RX.match(path):
        return NO_DEVICE
    if _inside(path, ctx.worktree):
        return ""
    if _is_protected(path, ctx):
        return KEPT_OUT
    root = path.rstrip("/") + "/"
    if any(os.path.realpath(p).startswith(root) for p in _protected(ctx)):
        return KEPT_OUT
    return ""


def _glob_dir(root, pattern, ctx):
    """The literal directory a glob pattern starts from, resolved."""
    full = os.path.join(root, _expand(pattern, ctx))
    prefix = re.split(r"[*?\[{]", full, 1)[0]
    if not prefix.endswith("/"):
        prefix = os.path.dirname(prefix)
    return os.path.realpath(prefix or "/")


def _read_reason(tool, inp, ctx):
    raw = inp.get(READ_TOOLS[tool][0]) or ctx.worktree
    root = _real(raw, ctx)
    why = _root_reason(root, ctx)
    pattern = inp.get("pattern") if tool == "Glob" else inp.get("glob") if tool == "Grep" else None
    if not why and pattern:
        why = _root_reason(_glob_dir(root, pattern, ctx), ctx)
    return why


# -- workflow files ----------------------------------------------------------

def _items(value):
    value = re.sub(r"\s+#.*$", "", value).strip().strip("[]")
    return [x.strip().strip("\"'") for x in value.split(",") if x.strip()]


def _key_items(lines, i, indent, rest):
    """Values of a YAML key: inline (`k: [a, b]`) or a block list below it."""
    if rest.strip() and not rest.strip().startswith("#"):
        return _items(rest)
    out = []
    for line in lines[i + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)-\s+(.*)$", line)
        if not (m and len(m.group(1)) >= indent):
            break
        out += _items(m.group(2))
    return out


def _hosted(text):
    """Whether workflow text puts a job on a GitHub-hosted runner, or on a bare
    `self-hosted` (which would match the Macs and the Beelink alike)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:-\s+)?runs-on:\s*(.*)$", line)
        if not m:
            continue
        items = _key_items(lines, i, len(m.group(1)), m.group(2))
        labels = []
        for item in items:
            ref = re.search(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", item)
            if not ref:
                labels.append(item)
                continue
            for j, l2 in enumerate(lines):
                k = re.match(r"^(\s*)" + re.escape(ref.group(1)) + r":\s*(.*)$", l2)
                if k:
                    labels += _key_items(lines, j, len(k.group(1)), k.group(2))
        low = [x.lower() for x in labels]
        if any(x.startswith(HOSTED_PREFIX) for x in low) or low == ["self-hosted"]:
            return True
        if any(x == "self-hosted" for x in low) and any(
                re.search(r"\$\{\{\s*matrix", x) for x in items):
            return True
    return False


def _text_after(tool, inp, path):
    """What the file will contain once this write lands."""
    if tool == "Write":
        return inp.get("content", "")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        text = ""
    if tool == "MultiEdit":
        edits = [(e.get("old_string", ""), e.get("new_string", ""), e.get("replace_all"))
                 for e in inp.get("edits", [])]
    else:
        edits = [(inp.get("old_string", ""), inp.get("new_string", "") or inp.get("new_source", ""),
                  inp.get("replace_all"))]
    for old, new, every in edits:
        if old and old in text:
            text = text.replace(old, new) if every else text.replace(old, new, 1)
        else:
            text += "\n" + new      # cannot apply it: judge the new text on its own
    return text


# -- shell -------------------------------------------------------------------

def _tokens(command):
    """Shell tokens with operators split out, or None when quotes are unbalanced.
    `#` is not a comment here: judging a comment's words too is the safe side."""
    text = command.replace("\n", " ; ").replace("$(", " ( ").replace("`", " ( ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None


def _segments(tokens):
    """Split a token list on shell operators into simple commands (word lists)."""
    segs, cur = [], []
    for t in tokens:
        if t in OPERATORS or set(t) <= set(";&|()"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _skip_wrapper(words):
    """Drop a wrapper word, its options and (for timeout) its duration."""
    name = os.path.basename(words[0])
    argopts = WRAPPERS[name]
    i = 1
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        i += 2 if words[i] in argopts else 1
    if name == "timeout" and i < len(words):
        i += 1
    return words[i:]


def _inline_reason(base, words):
    """Refuse inline code (`python3 -c ...`) that names a banned thing."""
    if re.match(r"^python[\d.]*$", base):
        flag = re.compile(r"^-[a-zA-Z]*c$")
    elif base in ("node", "perl", "ruby"):
        flag = re.compile(r"^(-[a-zA-Z]*e|--eval)$|^-[pE]$")
    else:
        return ""
    for i, w in enumerate(words[1:], 1):
        if flag.match(w) and i + 1 < len(words):
            code = words[i + 1]
            if re.search(r"\b(" + "|".join(map(re.escape, INLINE_WORDS)) + r")\b|push|merge|/dev/", code):
                return "Inline code may not reach for that."
    return ""


def _gh_api_reason(rest):
    """`gh api` may read; any method but GET, any field or input, or graphql, may write."""
    for i, w in enumerate(rest):
        method = None
        if w in ("-X", "--method"):
            method = rest[i + 1] if i + 1 < len(rest) else ""
        elif w.startswith("--method="):
            method = w.split("=", 1)[1]
        elif w.startswith("-X") and len(w) > 2:
            method = w[2:]
        if method is not None and method.upper() != "GET":
            return RUNNER_JOB
        if (w.startswith(("-f", "-F")) and not w.startswith("--")) or \
                w.startswith(("--field", "--raw-field", "--input")):
            return RUNNER_JOB
        if w == "graphql" or w.endswith("/graphql"):
            return RUNNER_JOB
    return ""


def _git_reason(words):
    """Rules for git, after its global options (-C dir, -c k=v, ...) are dropped."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_GLOBAL_ARGS else 1
    rest = words[i:]
    seg = " ".join(["git"] + rest)
    if any(rx.search(seg) for rx in BANNED):
        return RUNNER_JOB
    if rest and rest[0] in ("checkout", "switch"):
        args = rest[1:]
        if "-b" in args or "-B" in args:
            k = max(args.index(f) for f in ("-b", "-B") if f in args)
            names = args[k + 1:k + 2]
        else:
            names = [a for a in args if not a.startswith("-")]
        if any(n in ("main", "master") for n in names):
            return "Work sessions stay off the main branch."
    return ""


def _package_reason(base, words):
    rest = words[1:]
    if base in ("pip", "pip3") and "install" in rest:
        return "Work sessions may not install packages system-wide."
    if base in ("npm", "pnpm", "yarn") and (
            {"-g", "--global", "--location=global"} & set(rest) or rest[:1] == ["global"]):
        return "Work sessions may not install packages system-wide."
    if base in ("gem", "cargo") and rest[:1] == ["install"]:
        return "Work sessions may not install packages system-wide."
    return ""


def _path_reason(words, ctx):
    """Refuse any word that resolves, from the worktree, into a protected place
    (`../../omacar/lib`), however it was spelled."""
    for t in words:
        if t.startswith("-") and "=" in t:
            t = t.split("=", 1)[1]
        if ("/" in t or t.startswith(".")) and _is_protected(_real(t, ctx), ctx):
            return KEPT_OUT
    return ""


def _judge_string(s, ctx, depth):
    tokens = _tokens(s)
    if tokens is None:
        return UNREADABLE
    for words in _segments(tokens):
        why = _judge_words(words, ctx, depth)
        if why:
            return why
    return ""


def _judge_words(words, ctx, depth):
    """Judge one simple command, peeling off keywords, assignments, wrappers,
    `sh -c`, `eval` and `find -exec` to reach the command that really runs."""
    if depth > 8:
        return UNREADABLE
    why = _path_reason(words, ctx)
    if why:
        return why
    words = list(words)
    while words:
        w, base = words[0], os.path.basename(words[0])
        if re.match(r"^\w+=", w) or w in KEYWORDS:
            words.pop(0)
        elif w in ("for", "case", "select"):
            return ""                   # the rest is a word list, not a command
        elif base in BANNED_WORDS:
            return f"Work sessions may not run {base}."
        elif base in WRAPPERS:
            words = _skip_wrapper(words)
        elif base in SHELLS:
            for i, x in enumerate(words[1:], 1):
                if x.startswith("-") and not x.startswith("--") and "c" in x and i + 1 < len(words):
                    return _judge_string(words[i + 1], ctx, depth + 1)
            return ""
        elif base == "eval":
            return _judge_string(" ".join(words[1:]), ctx, depth + 1)
        elif base == "find":
            for i, x in enumerate(words):
                if x in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = words[i + 1:]
                    end = next((k for k, y in enumerate(inner) if y in (";", "+")), len(inner))
                    why = _judge_words(inner[:end], ctx, depth + 1)
                    if why:
                        return why
            break
        else:
            break
    if not words:
        return ""
    base = os.path.basename(words[0])
    words[0] = base
    if re.match(r"^python[\d.]*$", base) and "-m" in words:
        k = words.index("-m")
        if words[k + 1:k + 2] in (["pip"], ["pip3"]):
            words = ["pip"] + words[k + 2:]
            base = "pip"
    why = _inline_reason(base, words)
    if why:
        return why
    if base == "git":
        return _git_reason(words)
    if base == "gh":
        if words[1:2] == ["api"]:
            why = _gh_api_reason(words[2:])
            if why:
                return why
        if any(rx.search(" ".join(words)) for rx in BANNED):
            return RUNNER_JOB
        return ""
    if any(rx.search(" ".join(words)) for rx in BANNED):
        return RUNNER_JOB
    return _package_reason(base, words)


def _bash(command, ctx):
    command = _expand(command, ctx)
    if DEVICE_RX.search(command):
        return NO_DEVICE
    if ".github/workflows" in command:
        return "Workflow files are changed with the edit tools, not the shell."
    for p in _protected(ctx) + ["/share/assets/private"]:
        # A boundary after the path: /Projects/omacar must not catch the
        # sibling /Projects/omacar-work-src.
        if re.search(re.escape(p) + r"(?![\w.-])", command):
            return KEPT_OUT
    return _judge_string(command, ctx, 0)


def judge(tool, inp, ctx):
    inp = inp or {}
    if tool in WRITE_TOOLS:
        path = _real(inp.get(WRITE_TOOLS[tool][0], ""), ctx)
        if not _inside(path, ctx.worktree):
            return False, "Work sessions write only inside their own worktree."
        if "/.github/workflows/" in path and _hosted(_text_after(tool, inp, path)):
            return False, "CI runs on our own runners only; that adds a hosted one."
        return True, ""
    if tool in READ_TOOLS:
        why = _read_reason(tool, inp, ctx)
        return (False, why) if why else (True, "")
    if tool == "Bash":
        why = _bash(str(inp.get("command", "")), ctx)
        return (False, why) if why else (True, "")
    if tool in FREE_TOOLS:
        return True, ""
    return False, f"Work sessions may not use {tool}."
