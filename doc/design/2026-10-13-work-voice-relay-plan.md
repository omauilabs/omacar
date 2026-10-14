# Work by voice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speak a coding task in the car, have a Claude Code session do it on its own branch and open a draft PR, hear short spoken milestones, and answer the session's questions by voice.

**Architecture:** A relay process (`lib/workrelay.py`, `omacar work serve`) owns speech-to-text, intent parsing, sessions and speech rendering. It runs on the tablet first (parked only) and on the box later; the host is a setting. The page records and plays clips and talks only to its own server (`lib/api.py` → `lib/workroutes.py`), which keeps an on-disk queue and forwards to the relay. Every tool call a session makes passes a `PreToolUse` hook whose decisions come from one pure function, `workgates.judge()`.

**Tech Stack:** Python 3 stdlib + `claude-agent-sdk` + `faster-whisper` (both in the omacar venv), Piper (system program, never bundled), the `claude` CLI for one-shot intent parsing, ES modules in the page, the repo's own test runners (`test/*_test.py` scripts, `test/js/*.test.js` through `test/js_test.py`).

**Spec:** `doc/design/2026-10-13-work-voice-relay.md` (including its 2026-10-13 amendment: tablet now, box later).

## Global Constraints

- Sessions work on `work/<slug>` branches in worktrees under `~/Projects/.omacar-work/`; never on `main` or any default branch.
- A session can never: write outside its worktree; `git push` (the runner pushes, never the agent); `gh pr merge|ready|review|edit|create`; `ssh`/`scp`/`rsync` to any host; run `omacar`; open `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/rfcomm*`; touch `~/Projects/omacar` or `~/Projects/.omacar-wt/` or any `share/assets/private/`; use `sudo`, `systemctl`, a package manager, or `docker … prune`; add `ubuntu-*`, `macos-*`, `windows-*` or a bare `self-hosted` `runs-on`.
- The relay refuses `new_task` on host `tablet` unless parked, and stops a running session when the tablet's car leaves Park.
- "Parked" in this build is the app's current rule (`store.moving` in `share/js/core.js`: speed ≤ 3 km/h, connected, no hand-off, held ≥ 60 s; unknown counts as moving), mirrored in Python by `workpark.Park`. **The spec names the confirmed-Park rule (frame 0x191 = P, branch `safety/parked-confirm`), which is not on `main`.** When that branch merges, `workpark.Park` switches to it; nothing else changes.
- **Deviation from the spec, stated:** resuming a Claude session after a relay restart is not in this build. A session running at a restart is reported "interrupted" once and its worktree and branch are kept (Task 7). Resume is a follow-up.
- Spoken lines are at most two sentences. Diffs, file contents and test output are never spoken.
- Timeouts: recording stops after 1.2 s silence or 30 s; relay answer 5 s before "No signal — saved"; confirmation 20 s → "Not started."; question 30 min → parked in the PR; session silent 20 min → stuck.
- Sessions: model alias `sonnet`; at most 2 at once; a daily cap from `work.toml` (default 6). Intent: model alias `haiku`, through the `claude` CLI found by `ai.claude_bin()`.
- Quiet hours (default 21:00–07:00 while parked) hold lines instead of speaking them.
- Audio clips are deleted once transcribed; transcripts kept 7 days. Transcripts and lines are scrubbed by value of the car's `VEHICLE_SECRET` strings and any 17-character identifier before any model sees them or they are logged; the secrets are never written on the relay's host.
- In demo mode every `/api/work*` route is refused (`DEMO_ROUTES`: not listed → refused; add an explicit REFUSE entry and a test).
- CI stays `[self-hosted, Linux]`. Never call the real Claude, real whisper or real Piper in `test/all.sh`; box-only speech checks run behind `OMACAR_WORK_SPEECH=1`.
- Commit trailers name the model from the implementer's own context.

## Review Focus

1. **A transcript that names a project by a near-miss ("omakar", "the car thing")** → it must resolve through the allowlist or ask one either/or question, never start the wrong repo. Test in Task 3.
2. **Relay restart while a confirmation is pending** → a "yes" arriving after restart must not start anything (pending confirmations are in memory and die with the process). Test in Task 6.
3. **The same clip forwarded twice** (retry after a lost reply) → one utterance, not two tasks. Client ids are idempotent. Test in Task 6 and Task 7.
4. **An agent hiding a forbidden command in a compound shell line** (`cd x && git push`, `bash -c "ssh omacar-1"`, `$(...)`) → refused. Test in Task 2.
5. **Speech arriving while an alert is sounding, and the replay rule** → never overlaps, replays only if < 2 min old. Test in Task 9.

---

## File map

| File | Responsibility |
|---|---|
| `lib/workcfg.py` | Load `~/.config/omacar/work.toml`; projects, spoken names, host, caps, quiet hours |
| `lib/workgates.py` | `judge(tool, input, ctx)` — the one place a tool call is allowed or refused |
| `lib/workprivacy.py` | `secrets_from(vehicle)` and `scrub(text, secrets)` |
| `lib/workintent.py` | Transcript → one `Intent`, checked against the allowlist |
| `lib/workpark.py` | Python mirror of the app's parked rule, fed from `records.live()` |
| `lib/worksession.py` | One task: worktree, SDK client, hook, questions, gate, push, draft PR |
| `lib/workvoice.py` | Milestone → line; Piper rendering; quiet-hours hold |
| `lib/workears.py` | Clip → transcript + confidence (faster-whisper) |
| `lib/workrelay.py` | HTTP API, identity check, event log, orchestration |
| `lib/workroutes.py` | The app server's `/api/work/*` routes and the on-disk queue |
| `share/js/work/recorder.js` | Push-to-talk recording with silence stop |
| `share/js/work/speech.js` | The speech queue, yielding to alerts; when to repeat a question; offline once per drive |
| `share/js/views/work.js` | The Work tab |
| `share/systemd/omacar-work.service` | The relay as a user service |
| `bin/omacar` | `omacar work serve|status|say` |
| `test/work*_test.py`, `test/js/work*.test.js` | Tests, each listed in `test/all.sh` |

---

### Task 1: Settings — `work.toml`

**Files:**
- Create: `lib/workcfg.py`
- Create: `examples/work.toml`
- Test: `test/workcfg_test.py`
- Modify: `test/all.sh` (add the test after `srvwatch_test.py`)

**Interfaces:**
- Produces:
  - `workcfg.Project(name: str, names: tuple[str, ...], path: str, gate: str)`
  - `workcfg.Config(host: str, projects: dict[str, Project], max_sessions: int, daily_cap: int, quiet: tuple[int, int], relay_url: str, tablet_node: str)`
  - `workcfg.load(path: str | None = None) -> Config` (missing file → defaults with no projects)
  - `workcfg.PATH: str` = `$XDG_CONFIG_HOME/omacar/work.toml`

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""work.toml: the allowlist, the host, the caps. Scratch config only."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="omacar-workcfg-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workcfg  # noqa: E402

fails = 0
def check(msg, got, want=True):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

print("\n  work.toml")
c = workcfg.load(os.path.join(SCRATCH, "missing.toml"))
check("no file: no projects", c.projects, {})
check("no file: host is box", c.host, "box")
check("no file: two at once, six a day", (c.max_sessions, c.daily_cap), (2, 6))
check("no file: quiet 21-7", c.quiet, (21, 7))

p = os.path.join(SCRATCH, "w.toml")
with open(p, "w") as f:
    f.write('host = "tablet"\ndaily_cap = 3\nquiet = [22, 6]\n'
            '[project.omacar]\nnames = ["omacar", "Car App"]\n'
            'path = "~/Projects/omacar"\ngate = "test/all.sh"\n')
c = workcfg.load(p)
check("host read", c.host, "tablet")
check("cap read", c.daily_cap, 3)
check("names lower-cased, own name included",
      c.projects["omacar"].names, ("omacar", "car app"))
check("path expanded", c.projects["omacar"].path,
      os.path.expanduser("~/Projects/omacar"))

with open(p, "w") as f:
    f.write('host = "laptop"\n')
try:
    workcfg.load(p); check("bad host refused", False)
except ValueError as e:
    check("bad host refused", "host" in str(e))

with open(p, "w") as f:
    f.write('[project.x]\nnames=["x"]\npath="/tmp"\n')
try:
    workcfg.load(p); check("project without gate refused", False)
except ValueError as e:
    check("project without gate refused", "gate" in str(e))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 test/workcfg_test.py`
Expected: `ModuleNotFoundError: No module named 'workcfg'`

- [ ] **Step 3: Implement**

```python
"""Work's settings: which projects can be dispatched by voice, and where.

~/.config/omacar/work.toml, edited by the owner. A project that is not listed
here cannot be dispatched: the allowlist is the whole of Work's reach.
"""
import os
import tomllib
from dataclasses import dataclass, field

PATH = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omacar", "work.toml")
HOSTS = ("tablet", "box")


@dataclass(frozen=True)
class Project:
    name: str
    names: tuple
    path: str
    gate: str


@dataclass(frozen=True)
class Config:
    host: str = "box"
    projects: dict = field(default_factory=dict)
    max_sessions: int = 2
    daily_cap: int = 6
    quiet: tuple = (21, 7)
    relay_url: str = "http://127.0.0.1:7570"
    tablet_node: str = "omacar-1"


def load(path=None):
    path = path or PATH
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return Config()
    host = raw.get("host", "box")
    if host not in HOSTS:
        raise ValueError(f"work.toml: host must be one of {HOSTS}, not {host!r}")
    projects = {}
    for name, p in (raw.get("project") or {}).items():
        if not p.get("gate"):
            raise ValueError(f"work.toml: project {name} has no gate command")
        if not p.get("path"):
            raise ValueError(f"work.toml: project {name} has no path")
        names = [name.lower()] + [str(n).lower() for n in p.get("names", [])]
        projects[name] = Project(name, tuple(dict.fromkeys(names)),
                                 os.path.expanduser(p["path"]), str(p["gate"]))
    q = raw.get("quiet", [21, 7])
    return Config(host=host, projects=projects,
                  max_sessions=int(raw.get("max_sessions", 2)),
                  daily_cap=int(raw.get("daily_cap", 6)),
                  quiet=(int(q[0]), int(q[1])),
                  relay_url=str(raw.get("relay_url", Config.relay_url)),
                  tablet_node=str(raw.get("tablet_node", "omacar-1")))
```

Also write `examples/work.toml`:

```toml
# Work by voice: what the car may ask your machine to work on.
# Copy to ~/.config/omacar/work.toml on the machine that runs `omacar work serve`.
host = "tablet"          # "tablet" (parked only) or "box"
daily_cap = 6
quiet = [21, 7]          # hours when lines are shown, not spoken, while parked
# relay_url = "http://omarchy:7570"   # where the tablet's app finds the relay (host = "box")

[project.omacar]
names = ["omacar", "car", "the car app"]
path  = "~/Projects/omacar-work-src"   # never the live checkout ~/Projects/omacar
gate  = "test/all.sh"
```

Note: the name `omacar` folds in its own spelling (`"omacar"` above appears once after de-duplication).

- [ ] **Step 4: Run it to see it pass**

Run: `python3 test/workcfg_test.py`
Expected: `all passed`

- [ ] **Step 5: List it in `test/all.sh` and commit**

Add after the `srvwatch_test.py` line:

```bash
# Work by voice: settings, gates, intent, sessions, relay, routes (doc/design/2026-10-13-work-voice-relay.md).
for t in workcfg; do
  python3 "$ROOT/test/${t}_test.py" || fails=$((fails + 1))
done
```

(Each later task appends its own name to this `for` list.)

```bash
git add lib/workcfg.py examples/work.toml test/workcfg_test.py test/all.sh
git commit -m "Work: work.toml settings — the allowlist, the host, the caps"
```

---

### Task 2: The gates — `workgates.judge()`

**Files:**
- Create: `lib/workgates.py`
- Test: `test/workgates_test.py`
- Modify: `test/all.sh` (`for t in workcfg workgates`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `workgates.Ctx(worktree: str, host: str, home: str)`
  - `workgates.judge(tool: str, inp: dict, ctx: Ctx) -> tuple[bool, str]` — `(True, "")` or `(False, reason)`. Reason is one short sentence fit to speak.

Rules (refuse when any match):
- **Write tools** (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`): resolved `file_path`/`notebook_path` not inside `ctx.worktree`; or inside it but the new content (`content`, `new_string`, each `edits[].new_string`) adds a forbidden `runs-on` (regex below) to a path under `.github/workflows/`.
- **Read tools** (`Read`, `Glob`, `Grep`): resolved path inside a protected place (below). Reading elsewhere is allowed.
- **Bash**: the command, split on `;`, `&&`, `||`, `|`, newlines, `$(`, backticks, and the argument of `bash -c`/`sh -c`, has any segment whose first word (after `env X=Y`, `sudo` counted as forbidden itself) is in the forbidden set or matches a forbidden pattern; or the whole command mentions a protected place or a device path.
- **Network/agents**: `WebFetch`/`WebSearch` allowed; `Task`/`Agent` allowed (subagents inherit the hook).
- Always allowed: `AskUserQuestion`, `TodoWrite`, `ExitPlanMode`, `EnterPlanMode`, `WebFetch`, `WebSearch`, `Task`, `Agent`, `BashOutput`, `KillShell`, `Skill`, `ToolSearch` (the user's settings load plugins and skills; refusing `Skill` would break sessions for no safety gain). `mcp__*` and every other unknown name: refused.

Protected places: `{home}/Projects/omacar` (exact dir and below — but not `{home}/Projects/omacar-*` siblings), `{home}/Projects/.omacar-wt`, any path containing `/share/assets/private`, `{home}/.ssh`, `{home}/.config/omacar`, `{home}/.local/state/omacar`; devices `/dev/ttyUSB`, `/dev/ttyACM`, `/dev/rfcomm`.

Forbidden first words: `sudo`, `su`, `doas`, `systemctl`, `ssh`, `scp`, `rsync`, `sftp`, `pacman`, `yay`, `paru`, `apt`, `apt-get`, `dnf`, `pip` with `--user`/global is allowed only inside a venv — keep it simple: `pip install` is refused, `uv pip install`/`npm install` inside the worktree are allowed; `omacar`, `shutdown`, `reboot`, `kill`, `pkill`, `killall`, `hyprctl`, `crontab`, `tailscale`.
Forbidden patterns (regex on each segment): `^git\s+push\b`, `^git\s+.*--force`, `^git\s+(reset\s+--hard\s+origin|filter-branch|update-ref)`, `^git\s+(checkout|switch)\s+(main|master)\b`, `^gh\s+pr\s+(merge|ready|review|edit|create|close)\b`, `^gh\s+api\b.*-X\s*(POST|PATCH|PUT|DELETE)`, `^gh\s+(repo\s+delete|release)\b`, `^docker\s+(system|image|container|volume)\s+prune\b`.
Forbidden `runs-on` in workflow content: `runs-on:\s*\[?\s*["']?(ubuntu|macos|windows)-` or `runs-on:\s*["']?self-hosted["']?\s*$` (multiline).

- [ ] **Step 1: Write the failing test** — the table is the core of this feature.

```python
#!/usr/bin/env python3
"""The gates: every tool call a Work session makes is judged here.

A refusal quietly becoming an allow is the bug that matters most in Work, so
this is a table, and both halves of it are checked.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workgates as g  # noqa: E402

H = "/home/omacar"
WT = H + "/Projects/.omacar-work/omacar-fix-timeout"
CTX = g.Ctx(worktree=WT, host="tablet", home=H)
fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

def bash(c): return ("Bash", {"command": c})

REFUSE = [
    ("write outside the worktree", ("Write", {"file_path": H + "/notes.txt", "content": "x"})),
    ("write via .. out of the worktree", ("Edit", {"file_path": WT + "/../omacar/lib/x.py", "old_string": "a", "new_string": "b"})),
    ("write the live checkout", ("Write", {"file_path": H + "/Projects/omacar/lib/x.py", "content": ""})),
    ("read private assets", ("Read", {"file_path": H + "/Projects/omacar/share/assets/private/vin.txt"})),
    ("read the app's state", ("Read", {"file_path": H + "/.local/state/omacar/live.json"})),
    ("git push", bash("git push origin work/fix")),
    ("git push hidden after &&", bash("cd lib && git push")),
    ("git push inside bash -c", bash('bash -c "git push -u origin HEAD"')),
    ("git push inside $()", bash('echo $(git push)')),
    ("force anything", bash("git commit --amend && git rebase --force")),
    ("checkout main", bash("git checkout main")),
    ("gh pr merge", bash("gh pr merge 41 --squash")),
    ("gh pr create (the runner does that)", bash("gh pr create --draft")),
    ("gh api write", bash("gh api -X PATCH repos/o/r/pulls/1")),
    ("ssh to the tablet", bash("ssh omacar@omacar-1 uptime")),
    ("ssh after a pipe", bash("echo hi | ssh box")),
    ("scp", bash("scp a b:")),
    ("sudo", bash("sudo true")),
    ("env then sudo", bash("env FOO=1 sudo true")),
    ("systemctl", bash("systemctl --user restart omacar-kiosk")),
    ("omacar cli", bash("omacar write --test fan_high")),
    ("serial device", bash("cat /dev/ttyUSB0")),
    ("rfcomm", bash("stty -F /dev/rfcomm0")),
    ("docker prune", bash("docker system prune -af")),
    ("pacman", bash("pacman -S foo")),
    ("pkill", bash("pkill -f serve.py")),
    ("kill", bash("kill 1234")),
    ("hosted runner added", ("Edit", {"file_path": WT + "/.github/workflows/ci.yml",
        "old_string": "runs-on: [self-hosted, Linux]", "new_string": "runs-on: ubuntu-latest"})),
    ("live checkout named in a command", bash("cat ~/Projects/omacar/lib/api.py")),
    ("live checkout via a newline", bash("true\ncd /home/omacar/Projects/omacar")),
    ("bare self-hosted added", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on: self-hosted\n"})),
    ("mcp tool", ("mcp__x__y", {})),
    ("unknown tool", ("Frobnicate", {})),
]
ALLOW = [
    ("write in the worktree", ("Write", {"file_path": WT + "/lib/x.py", "content": "x"})),
    ("edit in the worktree", ("Edit", {"file_path": WT + "/lib/x.py", "old_string": "a", "new_string": "b"})),
    ("read the worktree", ("Read", {"file_path": WT + "/README.md"})),
    ("read a sibling repo", ("Read", {"file_path": H + "/Projects/omacar-notes-public/a.md"})),
    ("run the gate", bash("test/all.sh")),
    ("git status / diff / commit", bash("git status && git diff && git commit -am 'x'")),
    ("pinned self-hosted kept", ("Edit", {"file_path": WT + "/.github/workflows/ci.yml",
        "old_string": "x", "new_string": "runs-on: [self-hosted, Linux]"})),
    ("grep", ("Grep", {"pattern": "push", "path": WT})),
    ("ask the driver", ("AskUserQuestion", {"questions": []})),
    ("todo list", ("TodoWrite", {"todos": []})),
    ("web fetch", ("WebFetch", {"url": "https://example.org", "prompt": "x"})),
    ("the word push in a string", bash("grep -rn 'git push' doc/")),
    ("a sibling folder named like the live one", bash("ls ~/Projects/omacar-work-src")),
    ("a skill", ("Skill", {"skill": "superpowers:test-driven-development"})),
]

print("\n  must be refused")
for name, (tool, inp) in REFUSE:
    allowed, why = g.judge(tool, inp, CTX)
    check(name, (allowed, bool(why)), (False, True))
print("\n  must be allowed")
for name, (tool, inp) in ALLOW:
    check(name, g.judge(tool, inp, CTX), (True, ""))
print("\n  reasons are speakable")
_, why = g.judge("Bash", {"command": "git push"}, CTX)
check("one sentence, no newline", ("\n" not in why, why.endswith(".")), (True, True))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

Note on the case "the word push in a string": `grep -rn 'git push' doc/` must be allowed — segments are split on shell operators only outside quotes. Use `shlex` with `punctuation_chars=True` to tokenise, then split the token list on operator tokens.

- [ ] **Step 2: Run it to see it fail**

Run: `python3 test/workgates_test.py`
Expected: `ModuleNotFoundError: No module named 'workgates'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run it to see it pass**

Run: `python3 test/workgates_test.py`
Expected: `all passed`. If one row fails, fix `judge()` — never the row.

- [ ] **Step 5: Commit**

```bash
git add lib/workgates.py test/workgates_test.py test/all.sh
git commit -m "Work: the gates — every tool call a session makes is judged in one function"
```

---

### Task 3: Privacy scrub and intent

**Files:**
- Create: `lib/workprivacy.py`
- Create: `lib/workintent.py`
- Test: `test/workintent_test.py`
- Modify: `test/all.sh` (`… workgates workintent`)

**Interfaces:**
- Consumes: `workcfg.Config`, `workcfg.Project`; `mcp.VEHICLE_SECRET`, `mcp.IDENTIFIER`, `mcp.WITHHELD`; `ai.claude_bin()`.
- Produces:
  - `workprivacy.secrets_from(vehicle: dict) -> list[str]`
  - `workprivacy.scrub(text: str, secrets: list[str]) -> str`
  - `workintent.Intent(kind: str, project: str | None = None, instruction: str = "", session: str | None = None, answer: str = "", choices: tuple = ())` — `kind` ∈ `new_task`, `status`, `answer`, `stop`, `result`, `yes`, `no`, `unclear`, `choose`
  - `workintent.parse(transcript: str, cfg: Config, *, pending: str | None, ask_model=None) -> Intent` — `pending` is `"confirm"`, `"question"` or `None`
  - `workintent.match_project(said: str, cfg: Config) -> list[str]`

Rules:
- With `pending == "confirm"`: only `yes`/`no` words decide, no model call. Yes words: `yes, yeah, yep, start it, go, do it, sure, ok, okay`. No words: `no, nope, cancel, stop, don't`. Anything else → `unclear`. Silence is never yes.
- With `pending == "question"`: the whole transcript is the answer (`kind="answer"`), unless it is exactly a stop phrase.
- Otherwise: `ask_model(prompt) -> dict` (default: `claude -p --model haiku --output-format json`, no tools, `--max-turns 1`) returns `{"kind", "project", "instruction", "session"}`; then Python checks: `project` must resolve through `match_project`; 0 matches → `unclear`; 2+ → `choose` with `choices`; `new_task` with empty instruction → `unclear`.
- `match_project`: exact spoken-name match first; else names within edit distance ≤ 1 (names of 6 letters or fewer) or ≤ 2 (longer) of any word n-gram (1–3 words) of `said`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Intent: a transcript becomes one checked thing to do, or a question back.
The model is a fake here; nothing reaches Claude."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workcfg, workintent as wi, workprivacy as wp  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

P = workcfg.Project
CFG = workcfg.Config(projects={
    "omacar": P("omacar", ("omacar", "car", "the car app"), "/p/omacar", "t"),
    "omalens": P("omalens", ("omalens", "lens"), "/p/omalens", "t"),
    "omasaber": P("omasaber", ("omasaber",), "/p/omasaber", "t")})

def fake(reply):
    calls = []
    def ask(prompt): calls.append(prompt); return reply
    ask.calls = calls
    return ask

print("\n  confirmation: only a spoken yes starts anything")
for said, kind in [("yes", "yes"), ("Yeah, start it.", "yes"), ("no", "no"),
                   ("cancel that", "no"), ("", "unclear"), ("um", "unclear"),
                   ("what about omalens", "unclear")]:
    m = fake({})
    check(f"{said!r} -> {kind}", wi.parse(said, CFG, pending="confirm", ask_model=m).kind, kind)
    check(f"{said!r} asked no model", m.calls, [])

print("\n  a pending question takes the words as the answer")
i = wi.parse("make it configurable", CFG, pending="question", ask_model=fake({}))
check("answer", (i.kind, i.answer), ("answer", "make it configurable"))

print("\n  new tasks are checked against the allowlist")
i = wi.parse("in omacar fix the timeout", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omacar", "instruction": "fix the timeout"}))
check("known project", (i.kind, i.project, i.instruction), ("new_task", "omacar", "fix the timeout"))
i = wi.parse("in omakar fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omakar", "instruction": "fix it"}))
check("near-miss resolves", (i.kind, i.project), ("new_task", "omacar"))
i = wi.parse("in the lab fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omalab", "instruction": "fix it"}))
check("unlisted project", i.kind, "unclear")
i = wi.parse("in oma fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "oma", "instruction": "fix it"}))
check("ambiguous asks either/or", (i.kind, len(i.choices) >= 2), ("choose", True))
i = wi.parse("in omacar", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omacar", "instruction": ""}))
check("no instruction", i.kind, "unclear")
i = wi.parse("x", CFG, pending=None, ask_model=fake({"kind": "launch_missiles"}))
check("unknown kind", i.kind, "unclear")
i = wi.parse("x", CFG, pending=None, ask_model=fake("not a dict"))
check("garbage from the model", i.kind, "unclear")

print("\n  scrub")
sec = wp.secrets_from({"vin": "JHMZF1D60FS000000", "plate": "8ABC123", "driver": "Jim"})
check("short names are not hunted", "Jim" in sec, False)
check("vin and plate hunted", ("JHMZF1D60FS000000" in sec, "8ABC123" in sec), (True, True))
out = wp.scrub("plate 8ABC123 and JHMZF1D60FS000000 and 1HGCM82633A004352", sec)
check("by value and by shape", ("8ABC123" in out, "JHMZF1D60FS000000" in out, "1HGCM82633A004352" in out),
      (False, False, False))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 test/workintent_test.py`
Expected: `ModuleNotFoundError: No module named 'workintent'`

- [ ] **Step 3: Implement `lib/workprivacy.py`**

```python
"""Work's privacy: nothing that names the car or its owner reaches a model or a log.

The same two nets as lib/mcp.py: the vehicle record's identifying strings by
value, and anything shaped like a VIN by shape.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp import IDENTIFIER, VEHICLE_SECRET, WITHHELD  # noqa: E402


def secrets_from(vehicle):
    out = []
    for key in VEHICLE_SECRET:
        s = str((vehicle or {}).get(key) or "").strip()
        if len(s) >= 4:          # mcp.py's floor: shorter strings hit ordinary words
            out.append(s)
    return out


def scrub(text, secrets):
    text = str(text or "")
    for s in secrets:
        text = text.replace(s, "this car")
    return IDENTIFIER.sub(WITHHELD, text)
```

- [ ] **Step 4: Implement `lib/workintent.py`**

```python
"""What the driver meant: one transcript, one checked intent.

The model proposes; Python decides. A project must be on the allowlist, a new
task must say what to do, and a confirmation is a yes only when somebody said
yes -- silence, a cough or an unrelated sentence is never one.
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KINDS = {"new_task", "status", "stop", "result"}
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "go", "start it", "do it", "go ahead"}
NO = {"no", "nope", "cancel", "stop", "don't", "dont", "cancel that", "never mind"}
PROMPT = """You turn one spoken sentence from a driver into JSON. Kinds:
new_task (start work: project + instruction), status (what's going on),
stop (stop a session: project), result (read me the result: project).
Projects: {projects}. Reply with only a JSON object:
{{"kind": ..., "project": ... or null, "instruction": ... or "", "session": null}}
Sentence: {said}"""


@dataclass(frozen=True)
class Intent:
    kind: str
    project: str | None = None
    instruction: str = ""
    session: str | None = None
    answer: str = ""
    choices: tuple = ()


def _words(s):
    return re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()


def _phrase(s):
    return " ".join(_words(s))


def _dist(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def match_project(said, cfg):
    said = _phrase(said)
    exact = [n for n, p in cfg.projects.items() if said in p.names]
    if exact:
        return exact
    w = said.split()
    grams = {" ".join(w[i:i + k]) for k in (1, 2, 3) for i in range(len(w) - k + 1)} or {said}
    # One slip for a short name, two for a long one: "omakar" is omacar, but
    # "omalab" is not (two slips in six letters is a different word).
    close = [n for n, p in cfg.projects.items()
             if any(_dist(g, name) <= (1 if len(name) <= 6 else 2)
                    for g in grams for name in p.names if len(name) >= 4)]
    return close or [n for n, p in cfg.projects.items()
                     if any(name.startswith(said) for name in p.names) and len(said) >= 2]


def _answer(words, vocab):
    """A yes (or a no) is the whole utterance, or a short one that starts with it:
    "yeah, start it" is a yes; "what about omalens" is neither."""
    return bool(words) and (" ".join(words) in vocab or (words[0] in vocab and len(words) <= 3))


def _claude(prompt):
    import ai
    proc = subprocess.run(
        [ai.claude_bin() or "claude", "-p", "--output-format", "json",
         "--model", "haiku", "--max-turns", "1",
         "--disallowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep",
         "WebSearch", "WebFetch", "Task", "Agent", "TodoWrite", "NotebookEdit"],
        input=prompt, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("intent: claude failed")
    text = json.loads(proc.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def parse(transcript, cfg, *, pending, ask_model=None):
    said = _phrase(transcript)
    if pending == "confirm":
        if _answer(said.split(), YES):
            return Intent("yes")
        if _answer(said.split(), NO):
            return Intent("no")
        return Intent("unclear")
    if pending == "question":
        if said in NO:
            return Intent("no")
        return Intent("answer", answer=transcript.strip())
    if not said:
        return Intent("unclear")
    try:
        got = (ask_model or _claude)(PROMPT.format(
            projects=", ".join(sorted(cfg.projects)), said=transcript))
    except Exception:                                    # noqa: BLE001
        return Intent("unclear")
    if not isinstance(got, dict) or got.get("kind") not in KINDS:
        return Intent("unclear")
    kind = got["kind"]
    if kind == "status":
        return Intent("status")
    names = match_project(str(got.get("project") or ""), cfg) if got.get("project") else []
    if len(names) > 1:
        return Intent("choose", choices=tuple(sorted(names)))
    if not names:
        return Intent("unclear")
    if kind == "new_task" and not str(got.get("instruction") or "").strip():
        return Intent("unclear")
    return Intent(kind, project=names[0], instruction=str(got.get("instruction") or "").strip())
```

- [ ] **Step 5: Run it to see it pass**

Run: `python3 test/workintent_test.py`
Expected: `all passed`

- [ ] **Step 6: Commit**

```bash
git add lib/workprivacy.py lib/workintent.py test/workintent_test.py test/all.sh
git commit -m "Work: intent — the model proposes, the allowlist decides; only a spoken yes confirms"
```

---

### Task 4: Parked, in Python — `workpark.Park`

**Files:**
- Create: `lib/workpark.py`
- Test: `test/workpark_test.py`
- Modify: `test/all.sh` (`… workintent workpark`)

**Interfaces:**
- Consumes: `records.live() -> dict` (keys used: `connected`, `handover`, `values.SPEED`, `status`).
- Produces:
  - `workpark.SETTLE_S = 60`
  - `workpark.Park(read=records.live, clock=time.time)` with `.update() -> bool` (call ~1 Hz; returns parked) and `.parked: bool`.

Mirror of `store.moving` (share/js/core.js): moving while connected and SPEED > 3, during any hand-off, and until stopped AND connected for 60 s without a break. Unknown (not connected, no sample, no SPEED) breaks the stillness clock. A Park never seen moving since start counts as parked **only if connected** (the tablet relay is stricter than the page here: an unknown car is not parked).

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Parked, as Work's relay sees it: the app's rule, stricter about not knowing."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workpark  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

t = [0.0]; s = [{}]
P = workpark.Park(read=lambda: s[0], clock=lambda: t[0])
def at(sec, sample): t[0] = sec; s[0] = sample; return P.update()
STOP = {"connected": True, "values": {"SPEED": 0}}
GO = {"connected": True, "values": {"SPEED": 40}}

check("no daemon: not parked", at(0, {"connected": False, "status": "no daemon"}), False)
check("connected and stopped from the start: parked", at(1, STOP), True)
check("moving: not parked", at(2, GO), False)
# The stop begins at t=32 (still_since=32).
check("stopped 0 s (a red light): not parked", at(32, STOP), False)
check("stopped 59 s: not parked", at(91, STOP), False)
check("stopped 60 s: parked", at(92, STOP), True)
check("hand-off: not parked", at(93, dict(STOP, handover=True)), False)
check("link drop resets the clock", (at(94, {"connected": False}), at(100, STOP), at(159, STOP), at(160, STOP)),
      (False, False, False, True))
check("no SPEED is not stopped", at(200, {"connected": True, "values": {}}), False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

Note on "connected and stopped from the start": a car never seen moving since start counts as parked once connected and stopped — matching the page, so the relay is usable in the driveway at power-on.

- [ ] **Step 2: Run it to see it fail**

Run: `python3 test/workpark_test.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Is the car parked? Work's relay asks this on the tablet, once a second.

The page's rule (store.moving in share/js/core.js) in Python: moving while the
speed is over 3 km/h, during any hand-off of the adapter, and until the car
has sat stopped and connected for SETTLE_S without a break. A car never seen
moving since start counts as parked once it is connected and stopped.

Stricter than the page in one way: not knowing is not parked. No daemon, no
link, no speed -- the relay treats each as moving.

INTERIM, like the page's: the safety/parked-confirm branch replaces this with
a Park confirmed from frame 0x191. When it merges, this class reads that.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SETTLE_S = 60.0


class Park:
    def __init__(self, read=None, clock=time.time):
        if read is None:
            import records
            read = records.live
        self.read, self.clock = read, clock
        self.ever_moved = False
        self.still_since = None
        self.parked = False

    def update(self):
        now = self.clock()
        try:
            s = self.read() or {}
        except Exception:                                   # noqa: BLE001
            s = {}
        speed = (s.get("values") or {}).get("SPEED")
        known_stop = bool(s.get("connected")) and not s.get("handover") \
            and speed is not None and speed <= 3
        if s.get("connected") and speed is not None and speed > 3:
            self.ever_moved = True
        if not known_stop:
            self.still_since = None
            self.parked = False
            return False
        if self.still_since is None:
            self.still_since = now
        self.parked = (not self.ever_moved) or (now - self.still_since >= SETTLE_S)
        return self.parked
```

- [ ] **Step 4: Run it to see it pass** — `python3 test/workpark_test.py` → `all passed`

- [ ] **Step 5: Commit**

```bash
git add lib/workpark.py test/workpark_test.py test/all.sh
git commit -m "Work: the relay's parked rule — the app's, and not knowing is not parked"
```

---

### Task 5: Spoken lines — `workvoice`

**Files:**
- Create: `lib/workvoice.py`
- Test: `test/workvoice_test.py`
- Modify: `test/all.sh` (`… workpark workvoice`)

**Interfaces:**
- Produces:
  - `workvoice.line(event: dict) -> str` — event kinds: `confirm`, `not_started`, `started`, `question`, `gate_failed`, `pr_opened`, `finished`, `stuck`, `stopped`, `interrupted`, `refused`, `didnt_catch`, `choose`, `status`, `limit`, `cap`, `offline`, `no_signal`, `parked_only`, `busy`
  - `workvoice.quiet_now(cfg_quiet: tuple[int, int], hour: int, parked: bool) -> bool`
  - `workvoice.render(text: str, out_path: str, run=subprocess.run) -> bool` — Piper; False when Piper is missing
  - `workvoice.OFFLINE_LINES: dict[str, str]` — `no_signal`, `offline`, `not_started` (pre-rendered for the tablet)

Lines (exact text; `{p}` = spoken project name, `{q}` = question, `{n}` = PR number, `{why}` = refusal reason, `{a}`/`{b}` = choices, `{t}` = time):
- confirm: `"{p}: {instruction}. Start it?"`
- not_started: `"Not started."`
- started: `"{p} session started."`
- question: `"{p} has a question: {q}"`
- gate_failed: `"{p}: gate still failing, draft PR {n} opened."`
- pr_opened: `"{p}: gate passed, draft PR {n} opened."`
- finished: `"{p} finished."` · stuck: `"{p} is stuck."` · stopped: `"{p} stopped."` · interrupted: `"{p} was interrupted."`
- refused: `"{p} tried something Work does not allow. {why}"`
- didnt_catch: `"Didn't catch that."` · choose: `"{a} or {b}?"`
- limit: `"Usage limit reached — sessions paused until {t}."` · cap: `"That's today's limit of sessions."`
- offline: `"Work is offline."` · no_signal: `"No signal — saved."`
- parked_only: `"Work starts sessions here only while parked."` · busy: `"Two sessions are running. It will start when one finishes."`

`line()` truncates to two sentences (split on `. `, `? `, `! `) and to 220 characters; the question text is cut at its first sentence end.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Spoken lines: short, fixed, and never a diff. Piper is a fake here."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workvoice as v  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

check("started", v.line({"kind": "started", "project": "omacar"}), "omacar session started.")
check("pr", v.line({"kind": "pr_opened", "project": "omacar", "pr": 41}),
      "omacar: gate passed, draft PR 41 opened.")
long_q = "Should it be 0.8 s? Or configurable? Here is the diff: --- a/x +++ b/x"
said = v.line({"kind": "question", "project": "omacar", "question": long_q})
check("question cut to its first sentence", said, "omacar has a question: Should it be 0.8 s?")
check("at most two sentences, ever",
      v.line({"kind": "refused", "project": "p", "why": "One. Two. Three."}).count(". ") <= 1, True)
check("unknown kind says nothing wrong", v.line({"kind": "nope"}), "")
check("quiet at 23:00 parked", v.quiet_now((21, 7), 23, True), True)
check("not quiet at 23:00 moving", v.quiet_now((21, 7), 23, False), False)
check("not quiet at noon", v.quiet_now((21, 7), 12, True), False)
check("quiet at 06:00", v.quiet_now((21, 7), 6, True), True)
ran = []
def fake_run(cmd, **kw):
    ran.append(cmd); open(cmd[cmd.index("--output_file") + 1], "wb").write(b"RIFF")
    class R: returncode = 0
    return R()
import tempfile; out = os.path.join(tempfile.mkdtemp(), "a.wav")
check("render calls piper with the line on stdin", (v.render("Hi.", out, run=fake_run), os.path.exists(out)), (True, True))
check("missing piper is False, not a crash", v.render("Hi.", out, run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())), False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail** — `python3 test/workvoice_test.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run it to see it pass** — `python3 test/workvoice_test.py` → `all passed`

- [ ] **Step 5: Commit**

```bash
git add lib/workvoice.py test/workvoice_test.py test/all.sh
git commit -m "Work: spoken lines — milestones only, two sentences at most, Piper or shown"
```

---

### Task 6: One session — `worksession`

**Files:**
- Create: `lib/worksession.py`
- Test: `test/worksession_test.py`
- Modify: `test/all.sh` (`… workvoice worksession`)

**Interfaces:**
- Consumes: `workgates.judge`, `workgates.Ctx`, `workcfg.Project`.
- Produces:
  - `worksession.slug(instruction: str) -> str` (lowercase, `[a-z0-9-]`, ≤ 40 chars, plus `-<4 hex>`)
  - `worksession.Session(project: Project, instruction: str, *, emit: Callable[[dict], None], ask: Callable[[str], Awaitable[str | None]], home: str, host: str, client_factory=None, git=None, timeouts=TIMEOUTS)`
    - `.id: str`, `.branch: str`, `.worktree: str`, `.state: str` (`starting|running|question|gate|pushing|done|failed|stuck|stopped|interrupted`), `.pr: int | None`, `.sdk_session: str | None`
    - `async .run() -> None`
    - `async .stop(reason: str = "stopped") -> None`
  - `worksession.TIMEOUTS = {"question": 1800, "silent": 1200}`
  - `worksession.Git` — the runner's own git/gh calls: `prepare(project, branch, worktree)`, `gate(worktree, cmd) -> (ok: bool, tail: str)`, `push(worktree, branch)`, `draft_pr(worktree, branch, title, body) -> int`, `has_commits(worktree, base) -> bool`

Events emitted (dicts with `kind`, `session`, `project` and kind-specific keys; Task 5 turns them into lines): `started`, `question` (`question`), `refused` (`why`), `gate_failed` (`pr`), `pr_opened` (`pr`), `limit` (`until`), `stuck`, `stopped`, `interrupted`, `finished`.

Flow of `run()`:
1. `git.prepare()`: `git -C <path> fetch origin` then `git -C <path> worktree add -b work/<slug> <worktree> origin/<default>` (default from `git -C <path> symbolic-ref refs/remotes/origin/HEAD`). Emit `started`.
2. Build options:
   ```python
   ClaudeAgentOptions(cwd=worktree, model="sonnet", permission_mode="default",
       setting_sources=["user", "project"], can_use_tool=self._can_use,
       hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre])]},
       system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPEND})
   ```
   `SYSTEM_APPEND` tells the agent: it is working by voice for a driver; work only in the current directory; commit as it goes; run the gate `<gate>` before finishing; never push or open a PR (the runner does); ask with AskUserQuestion only when truly blocked, one short question with 2–3 options.
3. `_pre(input, tool_use_id, ctx)` → `judge()`; on refuse, emit `refused` (once per session) and return the deny dict:
   ```python
   {"hookSpecificOutput": {"hookEventName": "PreToolUse",
     "permissionDecision": "deny", "permissionDecisionReason": why}}
   ```
   On allow return `{}`.
4. `_can_use(tool, inp, ctx)`: for `AskUserQuestion`, take the first question, set `state="question"`, emit `question`, `answer = await asyncio.wait_for(self.ask(qtext), TIMEOUTS["question"])`; on timeout record the question for the PR body and `return PermissionResultDeny(message="No answer from the driver; stop and summarise.", interrupt=True)`; else `PermissionResultAllow(updated_input={**inp, "answers": {qtext: answer}})`. Everything else: `PermissionResultAllow(updated_input=inp)` (the hook already judged it).
5. Consume `client.receive_response()` with a watchdog: if no message for `TIMEOUTS["silent"]` (not counting time in `question`), emit `stuck` and `await self.stop("stuck")`. Record `ResultMessage.session_id` into `.sdk_session`.
6. Gate: `git.gate()`. If it fails, up to 2 times: `await client.query("The gate failed. Last lines:\n<tail>\nFix it, commit, and run it again.")`, consume, re-run gate.
7. If `git.has_commits()` is false: emit `finished` and stop (nothing to push). Else `git.push()`; `pr = git.draft_pr(...)` with title `Work: <instruction>` and a body listing the transcript, gate result, any unanswered question, and "Opened by voice from the car; review when parked." Emit `pr_opened` or `gate_failed`.
8. `stop()`: `client.interrupt()` if connected, then `disconnect()`, set state, emit `stopped` (or `stuck`). The worktree and branch are kept.

`client_factory(options)` defaults to `ClaudeSDKClient(options=options)`; the test passes a fake. `git` defaults to `Git()`; the test passes a fake. Import `claude_agent_sdk` lazily inside the default factory and options builder, so the module imports without the SDK installed; tests build options through a `_options()` method that the fake path bypasses by stubbing `options_factory`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""A Work session against a fake SDK and a fake git: the events, the question,
the gate repairs, the timeouts. Nothing reaches Claude, git or GitHub."""
import asyncio, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import worksession as ws, workcfg  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

class Result:  # stands in for ResultMessage
    def __init__(self, sid, limited=False):
        self.session_id = sid; self.is_error = limited
        self.api_error_status = 429 if limited else None

class FakeClient:
    """Plays a script: each item is ('say',), ('tool', name, input), ('ask', q),
    ('silence', secs) or ('result',)."""
    def __init__(self, script, session):
        self.script, self.s, self.queries, self.interrupted = list(script), session, [], False
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def connect(self, prompt=None): pass
    async def disconnect(self): pass
    async def interrupt(self): self.interrupted = True
    async def query(self, p): self.queries.append(p)
    async def receive_response(self):
        while self.script:
            step = self.script.pop(0)
            if step[0] == "result": yield Result("sdk-1"); return
            if step[0] == "limit": yield Result("sdk-1", limited=True); return
            if step[0] == "silence": await asyncio.sleep(step[1]); continue
            if step[0] == "tool":
                d = await self.s._pre({"tool_name": step[1], "tool_input": step[2]}, "t1", None)
                self.denied = d
            if step[0] == "ask":
                r = await self.s._can_use("AskUserQuestion",
                    {"questions": [{"question": step[1], "header": "Q", "options": [], "multiSelect": False}]}, None)
                self.answer = r
            yield object()

class FakeGit:
    def __init__(self, gates): self.gates, self.pushed, self.prs = list(gates), [], []
    def prepare(self, project, branch, wt): os.makedirs(wt, exist_ok=True)
    def gate(self, wt, cmd): ok = self.gates.pop(0); return ok, "" if ok else "FAIL x"
    def has_commits(self, wt, base="origin/HEAD"): return True
    def push(self, wt, branch): self.pushed.append(branch)
    def draft_pr(self, wt, branch, title, body): self.prs.append((title, body)); return 41

HOME = tempfile.mkdtemp()
PROJ = workcfg.Project("omacar", ("omacar",), os.path.join(HOME, "src"), "test/all.sh")

def run(script, gates, answer="configurable", timeouts=None):
    events = []
    async def ask(q):
        if answer is None: await asyncio.sleep(10)
        return answer
    git = FakeGit(gates)
    s = ws.Session(PROJ, "fix the timeout", emit=events.append, ask=ask, home=HOME,
                   host="tablet", git=git, timeouts=timeouts or ws.TIMEOUTS,
                   client_factory=lambda opts: FakeClient(script, s), options_factory=lambda: None)
    asyncio.run(s.run())
    return s, events, git

print("\n  happy path")
s, ev, git = run([("say",), ("result",)], [True])
check("events", [e["kind"] for e in ev], ["started", "pr_opened"])
check("pushed its own branch", git.pushed, [s.branch])
check("branch shape", s.branch.startswith("work/fix-the-timeout-"), True)
check("worktree under .omacar-work", "/Projects/.omacar-work/" in s.worktree, True)
check("sdk session kept for resume", s.sdk_session, "sdk-1")

print("\n  a question is asked and answered")
s, ev, git = run([("ask", "0.8 s or configurable? It matters."), ("result",)], [True])
check("question event, first sentence intact", [e["kind"] for e in ev][:2], ["started", "question"])

print("\n  a refused tool is spoken once and denied")
s, ev, git = run([("tool", "Bash", {"command": "git push"}), ("tool", "Bash", {"command": "git push"}), ("result",)], [True])
check("refused once", [e["kind"] for e in ev].count("refused"), 1)

print("\n  the gate gets two repairs, then a failing draft PR")
s, ev, git = run([("result",), ("result",), ("result",)], [False, False, False])
check("ends gate_failed", ev[-1]["kind"], "gate_failed")
check("still pushed and opened", (len(git.pushed), len(git.prs)), (1, 1))
s, ev, git = run([("result",), ("result",)], [False, True])
check("repaired once, then passed", ev[-1]["kind"], "pr_opened")

print("\n  an unanswered question parks in the PR")
s, ev, git = run([("ask", "Which one?"), ("result",)], [True], answer=None,
                 timeouts={"question": 0.05, "silent": 5})
check("question in the PR body", "Which one?" in git.prs[0][1], True)

print("\n  a usage limit stops it, with nothing pushed")
s, ev, git = run([("limit",)], [True])
check("limit", (ev[-1]["kind"], git.pushed), ("limit", []))

print("\n  silence is stuck")
s, ev, git = run([("silence", 0.2), ("result",)], [True], timeouts={"question": 5, "silent": 0.05})
check("stuck", ev[-1]["kind"], "stuck")
check("nothing pushed", git.pushed, [])

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail** — `python3 test/worksession_test.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `lib/worksession.py`**

```python
"""One Work task: a worktree, a Claude Code session, the gate, a draft PR.

The agent edits and commits. The runner -- this file -- pushes and opens the
PR, so the agent never needs, and is never given, the right to do either.
Every tool call passes workgates.judge() from a PreToolUse hook first.
"""
import asyncio
import os
import re
import secrets
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workgates  # noqa: E402

TIMEOUTS = {"question": 1800, "silent": 1200}
REPAIRS = 2
SYSTEM_APPEND = """You are working for a driver who talks to you by voice from a car.
Work only inside the current directory. Commit as you go with clear messages.
Before you finish, run the project's gate: `{gate}`. Never push and never open
or edit a pull request: the runner does both. If you are truly blocked, ask one
short question with AskUserQuestion, two or three options; otherwise decide."""


def slug(instruction):
    s = re.sub(r"[^a-z0-9]+", "-", instruction.lower()).strip("-")[:40].strip("-")
    return f"{s or 'task'}-{secrets.token_hex(2)}"


class Git:
    def _run(self, *cmd, cwd=None, timeout=120):
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)

    def prepare(self, project, branch, worktree):
        self._run("git", "-C", project.path, "fetch", "origin")
        head = self._run("git", "-C", project.path, "symbolic-ref", "--short",
                         "refs/remotes/origin/HEAD").stdout.strip() or "origin/main"
        os.makedirs(os.path.dirname(worktree), exist_ok=True)
        r = self._run("git", "-C", project.path, "worktree", "add", "-b", branch, worktree, head)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:300])

    def gate(self, worktree, cmd):
        # The owner's own command from work.toml, split, never through a shell.
        r = subprocess.run(shlex.split(cmd), cwd=worktree, capture_output=True,
                           text=True, timeout=3600)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-30:])
        return r.returncode == 0, tail

    def has_commits(self, worktree, base="origin/HEAD"):
        r = self._run("git", "rev-list", "--count", f"{base}..HEAD", cwd=worktree)
        return r.returncode == 0 and r.stdout.strip() not in ("", "0")

    def push(self, worktree, branch):
        r = self._run("git", "push", "-u", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:300])

    def draft_pr(self, worktree, branch, title, body):
        r = self._run("gh", "pr", "create", "--draft", "--head", branch,
                      "--title", title, "--body", body, cwd=worktree)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:300])
        m = re.search(r"/pull/(\d+)", r.stdout)
        return int(m.group(1)) if m else 0


class Session:
    def __init__(self, project, instruction, *, emit, ask, home, host,
                 client_factory=None, options_factory=None, git=None, timeouts=TIMEOUTS):
        self.project, self.instruction = project, instruction
        self.emit_raw, self.ask = emit, ask
        self.home, self.host = home, host
        self.git = git or Git()
        self.timeouts = timeouts
        self.client_factory = client_factory or self._sdk_client
        self.options_factory = options_factory or self._options
        self.id = secrets.token_hex(4)
        name = slug(instruction)
        self.branch = f"work/{name}"
        self.worktree = os.path.join(home, "Projects", ".omacar-work", f"{project.name}-{name}")
        self.state, self.pr, self.sdk_session = "starting", None, None
        self.refused_once, self.unanswered, self.client = False, [], None
        self.limited = False
        self.last_message = time.monotonic()
        self.ctx = workgates.Ctx(worktree=self.worktree, host=host, home=home)

    def emit(self, kind, **kw):
        self.emit_raw(dict(kind=kind, session=self.id, project=self.project.name, **kw))

    # ---- the SDK's two callbacks ------------------------------------------
    async def _pre(self, inp, tool_use_id, context):
        ok, why = workgates.judge(inp.get("tool_name", ""), inp.get("tool_input") or {}, self.ctx)
        if ok:
            return {}
        if not self.refused_once:
            self.refused_once = True
            self.emit("refused", why=why)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}

    async def _can_use(self, tool, inp, context):
        allow, deny = self._results()
        if tool != "AskUserQuestion":
            return allow(updated_input=inp)
        qs = inp.get("questions") or [{}]
        qtext = str(qs[0].get("question", "")).strip()
        prev, self.state = self.state, "question"
        self.emit("question", question=qtext)
        try:
            answer = await asyncio.wait_for(self.ask(qtext), self.timeouts["question"])
        except asyncio.TimeoutError:
            answer = None
        self.state, self.last_message = prev, time.monotonic()
        if not answer:
            self.unanswered.append(qtext)
            return deny(message="No answer from the driver. Stop here and summarise.", interrupt=True)
        return allow(updated_input={**inp, "answers": {qtext: answer}})

    def _results(self):
        try:
            from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
            return PermissionResultAllow, PermissionResultDeny
        except ImportError:                       # the tests' world: plain records
            return (lambda **kw: dict(behavior="allow", **kw),
                    lambda **kw: dict(behavior="deny", **kw))

    # ---- the SDK itself -----------------------------------------------------
    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
        return ClaudeAgentOptions(
            cwd=self.worktree, model="sonnet", permission_mode="default",
            setting_sources=["user", "project"], can_use_tool=self._can_use,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre])]},
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": SYSTEM_APPEND.format(gate=self.project.gate)})

    def _sdk_client(self, options):
        from claude_agent_sdk import ClaudeSDKClient
        return ClaudeSDKClient(options=options)

    async def _drain(self):
        """Read one response, watching for silence. False when stuck."""
        it = self.client.receive_response().__aiter__()
        while True:
            budget = self.timeouts["silent"]
            try:
                msg = await asyncio.wait_for(it.__anext__(), budget)
            except StopAsyncIteration:
                return True
            except asyncio.TimeoutError:
                if self.state == "question":
                    continue
                return False
            self.last_message = time.monotonic()
            if type(msg).__name__ in ("ResultMessage", "Result"):
                self.sdk_session = getattr(msg, "session_id", None)
                if getattr(msg, "is_error", False) and getattr(msg, "api_error_status", None) == 429:
                    self.limited = True

    async def run(self):
        try:
            self.git.prepare(self.project, self.branch, self.worktree)
        except Exception as e:                                # noqa: BLE001
            self.state = "failed"
            self.emit("stuck", why=str(e)[:200])
            return
        self.emit("started")
        self.state = "running"
        self.client = self.client_factory(self.options_factory())
        async with self.client:
            await self.client.query(self.instruction)
            if not await self._drain():
                return await self.stop("stuck")
            if self.limited:
                self.state = "stopped"
                self.emit("limit", until="the limit resets")
                return
            ok, tail = self.git.gate(self.worktree, self.project.gate)
            tries = 0
            while not ok and tries < REPAIRS:
                tries += 1
                await self.client.query(
                    f"The gate failed. Last lines:\n{tail}\nFix it, commit, and run it again.")
                if not await self._drain():
                    return await self.stop("stuck")
                ok, tail = self.git.gate(self.worktree, self.project.gate)
        await self._finish(ok)

    async def _finish(self, gate_ok):
        if not self.git.has_commits(self.worktree):
            self.state = "done"
            self.emit("finished")
            return
        self.state = "pushing"
        body = "\n".join([
            f"Asked by voice from the car: \"{self.instruction}\"", "",
            f"Gate `{self.project.gate}`: {'passed' if gate_ok else 'still failing'}.",
            *([""] + [f"Unanswered question: {q}" for q in self.unanswered] if self.unanswered else []),
            "", "Opened by voice from the car; review when parked."])
        self.git.push(self.worktree, self.branch)
        self.pr = self.git.draft_pr(self.worktree, self.branch, f"Work: {self.instruction}", body)
        self.state = "done"
        self.emit("pr_opened" if gate_ok else "gate_failed", pr=self.pr)

    async def stop(self, reason="stopped"):
        if self.client is not None:
            try:
                await self.client.interrupt()
            except Exception:                                 # noqa: BLE001
                pass
        self.state = reason if reason in ("stuck", "interrupted") else "stopped"
        self.emit(self.state)
```

The fake `Result` class in the test is named `Result` so `_drain` recognises it without the SDK installed. A usage limit shows up as a `ResultMessage` with `is_error` and `api_error_status == 429`; the session then stops with a `limit` event and pushes nothing.

- [ ] **Step 4: Run it to see it pass** — `python3 test/worksession_test.py` → `all passed`. In the unanswered-question case the fake client's script continues to `result` after the deny; the session then gates, pushes and puts the question in the PR body.

- [ ] **Step 5: Commit**

```bash
git add lib/worksession.py test/worksession_test.py test/all.sh
git commit -m "Work: one session — worktree, gated SDK client, spoken questions, gate repairs, draft PR by the runner"
```

---

### Task 7: The relay — `workrelay` and `workears`

**Files:**
- Create: `lib/workears.py`
- Create: `lib/workrelay.py`
- Test: `test/workrelay_test.py`
- Modify: `test/all.sh` (`… worksession workrelay`)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `workears.transcribe(path: str) -> tuple[str, float]` (text, avg logprob; `("", -9.0)` when nothing heard). faster-whisper `WhisperModel("small.en", device="cpu", compute_type="int8")`, loaded once, `vad_filter=True`.
  - `workrelay.Relay(cfg, *, ears=workears.transcribe, intent=workintent.parse, session_factory=worksession.Session, park=None, clock=time.time, state_dir=..., render=workvoice.render)`
    - `.utterance(client_id: str, audio_path: str, secrets: list[str]) -> dict` — idempotent by `client_id`
    - `.events(after: int) -> list[dict]` — each `{"id", "at", "kind", "line", "clip", "session", "project"}`
    - `.status() -> dict` — `{"host", "parked", "sessions": [{"id","project","state","pr","line"}], "today": n, "cap": n}`
    - `.tick() -> None` — 1 Hz: park update, confirmation expiry (20 s), stop sessions on host tablet when not parked
  - `workrelay.serve(cfg, port=7570)` — `ThreadingHTTPServer`; routes `POST /utterance` (JSON `{client_id, audio_b64, mime, secrets}`), `GET /events?after=`, `GET /clip/<name>`, `GET /health`. Bind `127.0.0.1` on host `tablet`; on host `box` bind the tailnet address from `tailscale ip -4` and refuse any peer whose `tailscale whois --json <ip>` node name is not `cfg.tablet_node`.
  - Event log: `<state_dir>/events.jsonl`, append-only, ids increasing; transcripts kept 7 days (`prune()` at start and daily). Clips: `<state_dir>/clips/<id>.wav`. Audio uploads written to `<state_dir>/in/<client_id>.<ext>` and deleted after transcription.

Orchestration in `utterance()`:
1. Seen `client_id` → return the stored reply.
2. Transcribe; delete the audio. Scrub the text with the posted secrets (never stored). Empty or logprob < -1.0 → event `didnt_catch`.
3. Pending state: `self.pending` is `None`, `("confirm", Intent, expires)` or `("question", session_id, future)`.
4. `intent.parse(text, cfg, pending=...)`:
   - `yes` with a pending confirm → start it (checks below); `no` → `not_started`; `unclear` with pending confirm → `didnt_catch` and keep pending.
   - `answer` → resolve that session's future.
   - `new_task` → checks: host tablet and not parked → `parked_only`; today ≥ cap → `cap`; else set pending confirm (expires in 20 s) and emit `confirm`.
   - `status` → one `status` event whose `text` is up to three `"<project>: <state>."` sentences, newest first, or `"No sessions."`.
   - `stop` → `session.stop()`; `result` → `"<project>: draft PR <n>, <gate passed|gate failing>."`
   - `choose` → `choose`; `unclear` → `didnt_catch`.
5. Starting: if 2 running → queue it and emit `busy`. Sessions run on the relay's own asyncio loop (one thread); their `ask` callback sets `self.pending = ("question", id, future)`.
6. Every event gets `line = workvoice.line(event)`; if not quiet, `render(line, clip)`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""The relay, end to end in one process: fake ears, fake model, fake sessions,
a fake park and a fake clock. Real HTTP for the identity and idempotency checks."""
import asyncio, base64, json, os, sys, tempfile, threading, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="omacar-workrelay-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workcfg, workintent, workrelay  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

P = workcfg.Project
CFG = workcfg.Config(host="tablet", daily_cap=2, projects={
    "omacar": P("omacar", ("omacar",), "/p", "t")})
HEARD = {}
PROMPTS = []
def ears(path): return HEARD[os.path.basename(path).split(".")[0]], -0.2
def model(prompt):
    PROMPTS.append(prompt)
    if "fix" in prompt: return {"kind": "new_task", "project": "omacar", "instruction": "fix it"}
    if "going on" in prompt: return {"kind": "status"}
    return {}
def intent(text, cfg, *, pending): return workintent.parse(text, cfg, pending=pending, ask_model=model)

class FakeSession:
    made = []
    def __init__(self, project, instruction, *, emit, ask, home, host, **kw):
        self.project, self.instruction, self.emit, self.ask = project, instruction, emit, ask
        self.id, self.state, self.pr = f"s{len(FakeSession.made)}", "running", None
        FakeSession.made.append(self)
    async def run(self):
        self.emit({"kind": "started", "session": self.id, "project": "omacar"})
        await asyncio.sleep(3600)
    async def stop(self, reason="stopped"): self.state = reason

class FakePark:
    parked = True
    def update(self): return self.parked
park = FakePark()
now = [1000.0]
R = workrelay.Relay(CFG, ears=ears, intent=intent, session_factory=FakeSession,
                    park=park, clock=lambda: now[0], state_dir=os.path.join(SCRATCH, "relay"),
                    render=lambda text, out: False)

def say(cid, text):
    HEARD[cid] = text
    p = os.path.join(SCRATCH, cid + ".webm"); open(p, "wb").write(b"x")
    return R.utterance(cid, p, ["8ABC123"])

def kinds(): return [e["kind"] for e in R.events(0)]

print("\n  dispatch needs a spoken yes")
say("c1", "in omacar fix it")
check("confirm asked", kinds()[-1], "confirm")
check("line read back", R.events(0)[-1]["line"], "omacar: fix it. Start it?")
say("c2", "um")
check("mumble keeps it pending", (kinds()[-1], R.pending is not None), ("didnt_catch", True))
say("c3", "yes")
time.sleep(0.2)
check("started", "started" in kinds(), True)
check("one session", len(FakeSession.made), 1)

print("\n  idempotent by client id")
before = len(R.events(0))
say("c3", "yes")
check("same clip twice is one utterance", len(R.events(0)), before)

print("\n  confirmations expire, and a later yes starts nothing")
say("c4", "in omacar fix it"); now[0] += 21; R.tick()
check("expired", kinds()[-1], "not_started")
say("c5", "yes")
check("late yes", len(FakeSession.made), 1)

print("\n  parked only, on the tablet")
park.parked = False
say("c6", "in omacar fix it")
check("refused while moving", kinds()[-1], "parked_only")
R.tick()
time.sleep(0.2)
check("running session stopped when the car moves", FakeSession.made[0].state, "stopped")
park.parked = True

print("\n  daily cap")
say("c7", "in omacar fix it"); say("c8", "yes"); time.sleep(0.2)
say("c9", "in omacar fix it")
check("cap of 2", kinds()[-1], "cap")

print("\n  secrets are scrubbed and not stored")
say("c10", "the plate is 8ABC123 fix")
log = open(os.path.join(SCRATCH, "relay", "events.jsonl")).read()
check("transcript kept, scrubbed", ("this car" in log, "8ABC123" in log), (True, False))
check("the model never saw it", any("8ABC123" in p for p in PROMPTS), False)
check("audio deleted", os.listdir(os.path.join(SCRATCH, "relay", "in")), [])

print("\n  status names each session's repo, for the parked PR link")
check("repo field present", all("repo" in s for s in R.status()["sessions"]), True)

print("\n  a restart forgets a pending confirmation")
say("c11", "in omacar fix it")
R2 = workrelay.Relay(CFG, ears=ears, intent=intent, session_factory=FakeSession, park=park,
                     clock=lambda: now[0], state_dir=os.path.join(SCRATCH, "relay"),
                     render=lambda t, o: False)
n = len(FakeSession.made)
HEARD["c12"] = "yes"; p = os.path.join(SCRATCH, "c12.webm"); open(p, "wb").write(b"x")
R2.utterance("c12", p, [])
check("no start after restart", len(FakeSession.made), n)
check("event ids continue after restart", R2.next_id > max(e["id"] for e in R.events(0)), True)
check("a session running at the restart is reported interrupted",
      any(e["kind"] == "interrupted" for e in R2.events(0)), True)

print(f"\n  {fails} failed" if fails else "\n  all passed")
os._exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail** — `python3 test/workrelay_test.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `lib/workears.py`**

```python
"""Ears: one recorded clip in, the words and how sure, out.

faster-whisper on the CPU, small.en at int8, loaded once. It reads the
browser's webm/opus directly. Not installed -> ("", -9.0), which the relay
speaks as "Didn't catch that."
"""
_model = None


def transcribe(path):
    global _model
    try:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _info = _model.transcribe(path, vad_filter=True, beam_size=1)
        segs = list(segments)
    except Exception:                                          # noqa: BLE001
        return "", -9.0
    if not segs:
        return "", -9.0
    text = " ".join(s.text.strip() for s in segs).strip()
    return text, sum(s.avg_logprob for s in segs) / len(segs)
```

- [ ] **Step 4: Implement `lib/workrelay.py`**

```python
"""Work's relay: clips in, sessions run, spoken lines out.

`omacar work serve`. On the tablet it listens on 127.0.0.1 and starts sessions
only while parked; on the box it listens on the tailnet and answers only the
tablet's node. The page never talks to it: the app's server does
(lib/workroutes.py).
"""
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workcfg, workintent, workprivacy, workvoice  # noqa: E401,E402

STATE = os.path.join(os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state")), "omacar", "work")
CONFIRM_S = 20.0
KEEP_S = 7 * 86400
UNSURE = -1.0


class Relay:
    def __init__(self, cfg, *, ears=None, intent=workintent.parse, session_factory=None,
                 park=None, clock=time.time, state_dir=STATE, render=workvoice.render):
        if ears is None:
            import workears
            ears = workears.transcribe
        if session_factory is None:
            import worksession
            session_factory = worksession.Session
        if park is None and cfg.host == "tablet":
            import workpark
            park = workpark.Park()
        self.cfg, self.ears, self.intent, self.make = cfg, ears, intent, session_factory
        self.park, self.clock, self.render = park, clock, render
        self.dir = state_dir
        for d in ("", "in", "clips"):
            os.makedirs(os.path.join(self.dir, d), exist_ok=True)
        self.log = os.path.join(self.dir, "events.jsonl")
        self.lock = threading.RLock()
        self.seen, self.sessions, self.queue = {}, {}, []
        self.pending = None
        self.next_id = self._last_id() + 1
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.prune()
        self._recover()

    # ---- sessions that outlive the process ----------------------------------
    # Resuming a Claude session after a restart is not in this build: a
    # session that was running when the relay stopped is reported
    # "interrupted" once, and its worktree and branch are left for the owner.
    def _book(self):
        return os.path.join(self.dir, "sessions.json")

    def _save(self):
        rows = [{"id": s.id, "project": s.project.name, "state": s.state,
                 "pr": s.pr, "branch": getattr(s, "branch", "")}
                for s in self.sessions.values()]
        with open(self._book() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(self._book() + ".tmp", self._book())

    def _recover(self):
        try:
            rows = json.load(open(self._book(), encoding="utf-8"))
        except (OSError, ValueError):
            return
        for r in rows:
            if r.get("state") in ("starting", "running", "question", "gate", "pushing"):
                self.emit({"kind": "interrupted", "session": r["id"], "project": r["project"]})
        os.remove(self._book())

    # ---- the event log ------------------------------------------------------
    def _last_id(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                last = 0
                for row in f:
                    last = max(last, json.loads(row).get("id", 0))
                return last
        except (OSError, ValueError):
            return 0

    def prune(self):
        cutoff = self.clock() - KEEP_S
        try:
            with open(self.log, encoding="utf-8") as f:
                rows = [r for r in f if json.loads(r).get("at", 0) >= cutoff]
            with open(self.log + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(rows)
            os.replace(self.log + ".tmp", self.log)
        except (OSError, ValueError):
            pass

    def _parked(self):
        return True if self.park is None else bool(self.park.update())

    def emit(self, e):
        with self.lock:
            e = dict(e, id=self.next_id, at=self.clock())
            self.next_id += 1
            e["line"] = workvoice.line(e)
            hour = time.localtime(e["at"]).tm_hour
            e["clip"] = None
            if e["line"] and not workvoice.quiet_now(self.cfg.quiet, hour, self._parked()):
                name = f"{e['id']}.wav"
                if self.render(e["line"], os.path.join(self.dir, "clips", name)):
                    e["clip"] = name
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")
            if self.sessions:
                self._save()
            return e

    def events(self, after):
        try:
            with open(self.log, encoding="utf-8") as f:
                return [r for r in (json.loads(x) for x in f) if r["id"] > after]
        except (OSError, ValueError):
            return []

    # ---- utterances -----------------------------------------------------------
    def utterance(self, client_id, audio_path, secrets):
        with self.lock:
            if client_id in self.seen:
                try: os.remove(audio_path)
                except OSError: pass
                return self.seen[client_id]
            try:
                text, sure = self.ears(audio_path)
            finally:
                try: os.remove(audio_path)
                except OSError: pass
            text = workprivacy.scrub(text, secrets)
            if text.strip():
                # The transcript is the one thing kept (7 days); it has no line, so no clip.
                self.emit({"kind": "heard", "text": text})
            reply = self._handle(text, sure)
            self.seen[client_id] = reply
            return reply

    def _handle(self, text, sure):
        if not text.strip() or sure < UNSURE:
            return self.emit({"kind": "didnt_catch"})
        p = self.pending
        if p and p[0] == "question":
            i = self.intent(text, self.cfg, pending="question")
            if i.kind == "answer":
                self.pending = None
                self.loop.call_soon_threadsafe(p[2].set_result, i.answer)
                return {"ok": True}
        if p and p[0] == "confirm":
            i = self.intent(text, self.cfg, pending="confirm")
            if i.kind == "yes":
                self.pending = None
                return self._start(p[1])
            if i.kind == "no":
                self.pending = None
                return self.emit({"kind": "not_started"})
            return self.emit({"kind": "didnt_catch"})
        i = self.intent(text, self.cfg, pending=None)
        if i.kind == "new_task":
            if self.cfg.host == "tablet" and not self._parked():
                return self.emit({"kind": "parked_only"})
            if self._today() >= self.cfg.daily_cap:
                return self.emit({"kind": "cap"})
            self.pending = ("confirm", i, self.clock() + CONFIRM_S)
            return self.emit({"kind": "confirm", "project": i.project, "instruction": i.instruction})
        if i.kind == "status":
            return self.emit({"kind": "status", "text": self._status_text()})
        if i.kind == "stop" and i.project:
            for s in self.sessions.values():
                if s.project.name == i.project and s.state not in ("done", "stopped", "stuck"):
                    asyncio.run_coroutine_threadsafe(s.stop("stopped"), self.loop)
            return {"ok": True}
        if i.kind == "result" and i.project:
            done = [s for s in self.sessions.values() if s.project.name == i.project and s.pr]
            text = (f"{i.project}: draft PR {done[-1].pr}." if done else f"{i.project}: no pull request yet.")
            return self.emit({"kind": "status", "text": text})
        if i.kind == "choose":
            return self.emit({"kind": "choose", "choices": i.choices[:2]})
        return self.emit({"kind": "didnt_catch"})

    def _today(self):
        day = time.localtime(self.clock()).tm_yday
        return sum(1 for e in self.events(0)
                   if e["kind"] == "started" and time.localtime(e["at"]).tm_yday == day)

    def _running(self):
        return [s for s in self.sessions.values()
                if s.state in ("starting", "running", "question", "gate", "pushing")]

    def _start(self, i):
        project = self.cfg.projects[i.project]
        if len(self._running()) >= self.cfg.max_sessions:
            self.queue.append(i)
            return self.emit({"kind": "busy"})
        holder = {}

        async def ask(question):
            fut = self.loop.create_future()
            with self.lock:
                self.pending = ("question", holder["s"].id, fut)
            return await fut

        s = self.make(project, i.instruction, emit=self.emit, ask=ask,
                      home=os.path.expanduser("~"), host=self.cfg.host)
        holder["s"] = s
        self.sessions[s.id] = s
        fut = asyncio.run_coroutine_threadsafe(s.run(), self.loop)
        fut.add_done_callback(lambda _f: self._next())
        return {"ok": True, "session": s.id}

    def _next(self):
        with self.lock:
            if self.queue and len(self._running()) < self.cfg.max_sessions:
                self._start(self.queue.pop(0))

    def _status_text(self):
        rows = sorted(self.sessions.values(), key=lambda s: s.id, reverse=True)[:3]
        return " ".join(f"{s.project.name}: {s.state}." for s in rows) or "No sessions."

    def status(self):
        return {"host": self.cfg.host, "parked": self._parked(),
                "today": self._today(), "cap": self.cfg.daily_cap,
                "sessions": [{"id": s.id, "project": s.project.name, "state": s.state,
                              "pr": s.pr, "repo": self._repo(s.project)}
                             for s in self.sessions.values()]}

    def _repo(self, project):
        """owner/name of the project's GitHub remote, for the parked PR link."""
        try:
            r = subprocess.run(["git", "-C", project.path, "remote", "get-url", "origin"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        m = re.search(r"github\.com[:/]([^/]+/[^/.\s]+)", r.stdout)
        return m.group(1) if m else ""

    def tick(self):
        with self.lock:
            if self.pending and self.pending[0] == "confirm" and self.clock() > self.pending[2]:
                self.pending = None
                self.emit({"kind": "not_started"})
            if self.cfg.host == "tablet" and not self._parked():
                for s in self._running():
                    asyncio.run_coroutine_threadsafe(s.stop("stopped"), self.loop)


# ---- HTTP -------------------------------------------------------------------
MAX_AUDIO = 2 << 20


def _tailnet_ip():
    r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
    return r.stdout.split()[0]


def _node_of(ip):
    r = subprocess.run(["tailscale", "whois", "--json", ip], capture_output=True, text=True, timeout=5)
    try:
        return json.loads(r.stdout)["Node"]["ComputedName"]
    except (ValueError, KeyError):
        return ""


def serve(cfg, port=7570):
    relay = Relay(cfg)
    host = "127.0.0.1" if cfg.host == "tablet" else _tailnet_ip()

    def ticker():
        while True:
            time.sleep(1)
            relay.tick()
    threading.Thread(target=ticker, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def _ok_peer(self):
            if cfg.host == "tablet":
                return self.client_address[0] == "127.0.0.1"
            return _node_of(self.client_address[0]) == cfg.tablet_node

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._ok_peer():
                return self._json({"error": "not the tablet"}, 403)
            path, _, q = self.path.partition("?")
            if path == "/health":
                return self._json(dict(relay.status(), ok=True))
            if path == "/events":
                after = int((q.split("after=")[1:] or ["0"])[0].split("&")[0] or 0)
                return self._json({"events": relay.events(after)})
            if path.startswith("/clip/"):
                name = os.path.basename(path)
                p = os.path.join(relay.dir, "clips", name)
                if not name.endswith(".wav") or not os.path.exists(p):
                    return self._json({"error": "no such clip"}, 404)
                data = open(p, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return self._json({"error": "no such endpoint"}, 404)

        def do_POST(self):
            if not self._ok_peer():
                return self._json({"error": "not the tablet"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_AUDIO * 2:
                self.close_connection = True
                return self._json({"error": "too large"}, 413)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if self.path != "/utterance":
                return self._json({"error": "no such endpoint"}, 404)
            cid = str(data.get("client_id", ""))[:64]
            if not cid.replace("-", "").isalnum():
                return self._json({"error": "bad client id"}, 400)
            ext = "webm" if "webm" in str(data.get("mime", "")) else "ogg"
            path = os.path.join(relay.dir, "in", f"{cid}.{ext}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data.get("audio_b64", "")))
            return self._json(relay.utterance(cid, path, list(data.get("secrets") or [])))

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    serve(workcfg.load())
```

- [ ] **Step 5: Run it to see it pass** — `python3 test/workrelay_test.py` → `all passed`. Fix the relay, not the test, unless a row contradicts the spec.

- [ ] **Step 6: Commit**

```bash
git add lib/workears.py lib/workrelay.py test/workrelay_test.py test/all.sh
git commit -m "Work: the relay — transcribe, confirm, run, speak; parked-only on the tablet; idempotent clips"
```

---

### Task 8: The app's routes and queue — `workroutes`, CLI, service

**Files:**
- Create: `lib/workroutes.py`
- Modify: `lib/api.py` (overlay block at the end, after the cameras block)
- Modify: `lib/serve.py` (`DEMO_ROUTES`: `"/api/work": REFUSE, "/api/work/*": REFUSE`)
- Modify: `test/demoserve_test.py` (assert both refused)
- Modify: `bin/omacar` (`work` subcommand)
- Create: `share/systemd/omacar-work.service`
- Test: `test/workroutes_test.py`
- Modify: `test/all.sh` (`… workrelay workroutes`)

**Interfaces:**
- Consumes: `workcfg.load`, `workprivacy.secrets_from`, `records.connect`, `records.vehicle`.
- Produces (page-facing):
  - `POST /api/work/utterance` `{client_id, audio_b64, mime}` → `{queued: true}` immediately; the clip is written to `$STATE/work-queue/<client_id>.json` and a sender thread forwards oldest first.
  - `GET /api/work/events?after=<id>` → `{events: [...], online: bool, queued: n}` — proxied; `online=false` when the relay did not answer within 5 s.
  - `GET /api/work/clip/<name>` → proxied wav.
  - `GET /api/work/status` → proxied `/health` plus `{online, queued}`.
- `workroutes.handle_get(path, query)` / `workroutes.handle_post(path, body)` return `(status, payload)` or `None`, like `camroutes`.
- `workroutes.Sender(relay_url, queue_dir, post=_post, clock=time.time)` with `.drain_once() -> int` (sent count).

The secrets are read from the current vehicle record at forward time and posted with each clip; they are not written to the queue file.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""The app's side of Work: the queue survives a dead relay, sends oldest
first, never writes the secrets to disk, and the demo refuses every route."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="omacar-workroutes-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workroutes as wr  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

q = os.path.join(SCRATCH, "q")
sent, up = [], [False]
def post(url, payload, timeout):
    if not up[0]: raise OSError("no route")
    sent.append(payload["client_id"]); return {"ok": True}
S = wr.Sender("http://relay", q, post=post, secrets=lambda: ["8ABC123"])

for cid in ("a1", "a2", "a3"):
    S.enqueue({"client_id": cid, "audio_b64": "eA==", "mime": "audio/webm"})
check("queued on disk", len(os.listdir(q)), 3)
S.enqueue({"client_id": "a2", "audio_b64": "eA==", "mime": "audio/webm"})
check("a retried clip is queued once", len(os.listdir(q)), 3)
check("no secrets on disk", any("8ABC123" in open(os.path.join(q, f)).read() for f in os.listdir(q)), False)
check("relay down: nothing sent, nothing lost", (S.drain_once(), len(os.listdir(q))), (0, 3))
up[0] = True
check("relay back: oldest first", (S.drain_once(), sent), (3, ["a1", "a2", "a3"]))
check("queue empty", os.listdir(q), [])
S.enqueue({"client_id": "../evil", "audio_b64": "", "mime": ""})
check("bad client id refused", os.listdir(q), [])

sys.path.insert(0, os.path.join(ROOT, "lib"))
import serve  # noqa: E402
check("demo refuses /api/work/utterance", serve.demo_route("/api/work/utterance") == serve.ALLOW, False)
check("demo refuses /api/work/status", serve.demo_route("/api/work/status") == serve.ALLOW, False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run it to see it fail** — `python3 test/workroutes_test.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `lib/workroutes.py`**

```python
"""The app's side of Work: the page's clips, queued on disk, sent to the relay.

The page never talks to the relay. It posts here; the clip is written to the
queue at once and a sender forwards it oldest first, so a dead zone on the
Marina-Los Banos road costs a delay, not a clip. The car's identifying strings
ride along with each forward, read fresh from the vehicle record, and are never
written into the queue.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records, workcfg, workprivacy  # noqa: E401,E402

QUEUE = os.path.join(records.STATE, "work-queue")
ANSWER_S = 5.0
CID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _secrets():
    try:
        db = records.connect()
        return workprivacy.secrets_from(records.vehicle(db))
    except Exception:                                          # noqa: BLE001
        return []


class Sender:
    def __init__(self, relay_url, queue_dir=QUEUE, post=_post, secrets=_secrets):
        self.url, self.dir, self.post, self.secrets = relay_url, queue_dir, post, secrets
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.online = None

    def enqueue(self, item):
        cid = str(item.get("client_id", ""))
        if not CID.match(cid):
            return False
        if any(f.endswith(f"-{cid}.json") for f in os.listdir(self.dir)):
            return True                                    # the page retried; one clip
        rec = {k: item.get(k) for k in ("client_id", "audio_b64", "mime")}
        # Named by time, so the oldest is first by name: mtimes can tie.
        name = f"{time.time_ns():020d}-{cid}"
        tmp = os.path.join(self.dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, os.path.join(self.dir, name + ".json"))
        return True

    def pending(self):
        return sorted(f for f in os.listdir(self.dir) if f.endswith(".json"))

    def drain_once(self):
        sent = 0
        with self.lock:
            for name in self.pending():
                p = os.path.join(self.dir, name)
                try:
                    rec = json.load(open(p, encoding="utf-8"))
                    self.post(self.url + "/utterance", dict(rec, secrets=self.secrets()), ANSWER_S)
                except (OSError, ValueError):
                    self.online = False
                    break
                os.remove(p)
                self.online = True
                sent += 1
        return sent


_sender = None


def sender():
    global _sender
    if _sender is None:
        _sender = Sender(workcfg.load().relay_url)

        def loop():
            while True:
                _sender.drain_once()
                time.sleep(2)
        threading.Thread(target=loop, daemon=True).start()
    return _sender


def _get(path):
    try:
        with urllib.request.urlopen(sender().url + path, timeout=ANSWER_S) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except OSError:
        return None, ""


def handle_get(path, query):
    if path == "/api/work/status":
        raw, _ = _get("/health")
        base = json.loads(raw) if raw else {}
        return 200, dict(base, online=raw is not None, queued=len(sender().pending()))
    if path == "/api/work/events":
        after = 0
        m = re.search(r"(?:^|&)after=(\d+)", query or "")
        if m:
            after = int(m.group(1))
        raw, _ = _get(f"/events?after={after}")
        events = json.loads(raw)["events"] if raw else []
        return 200, {"events": events, "online": raw is not None,
                     "queued": len(sender().pending())}
    if path.startswith("/api/work/clip/"):
        name = os.path.basename(path)
        if not re.match(r"^\d+\.wav$", name):
            return 404, {"error": "no such clip"}
        raw, _ = _get(f"/clip/{name}")
        return (200, {"wav_b64": __import__("base64").b64encode(raw).decode()}) if raw \
            else (404, {"error": "no such clip"})
    return None


def handle_post(path, body):
    if path != "/api/work/utterance":
        return None
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return 400, {"error": "bad json"}
    if not sender().enqueue(data):
        return 400, {"error": "bad client id"}
    threading.Thread(target=sender().drain_once, daemon=True).start()
    return 200, {"queued": True}
```

Clips cross `lib/serve.py` as JSON (`wav_b64`) because the server's `_json()` is the only response path for `/api/*`. The page decodes it with `decodeAudioData`. A 2-sentence Piper line is ~100–200 KB of wav, under `MAX_BODY` concerns (that limit is on requests, not responses).

- [ ] **Step 4: Wire it in**

Append to `lib/api.py`, after the cameras block:

```python
# ---- Work by voice ------------------------------------------------------------
# /api/work/* route through lib/workroutes.py, tried before everything above.
# doc/design/2026-10-13-work-voice-relay.md.
_work_get, _work_post = handle_get, handle_post


def handle_get(path, query):  # noqa: F811
    if path.startswith("/api/work"):
        import workroutes
        out = workroutes.handle_get(path, query)
        if out is not None:
            return out
    return _work_get(path, query)


def handle_post(path, body):  # noqa: F811
    if path.startswith("/api/work"):
        import workroutes
        out = workroutes.handle_post(path, body)
        if out is not None:
            return out
    return _work_post(path, body)
# ---- end Work by voice ----------------------------------------------------------
```

In `lib/serve.py` `DEMO_ROUTES`, add a REFUSE group with its reason:

```python
    # --- REFUSE: Work by voice reaches a real machine's coding sessions; the
    # demo is siloed and has no business there.
    "/api/work": REFUSE,
    "/api/work/*": REFUSE,
```

In `test/demoserve_test.py`, add `/api/work/utterance` and `/api/work/status` to the list of routes that must never be let through (find the existing must-refuse list by searching for `"/api/clear"`).

In `bin/omacar`, next to `live)`:

```bash
  work)
    omacar_need_env; shift
    case "${1:-status}" in
      serve)  exec "$OMACAR_PY" "$ROOT/lib/workrelay.py" ;;
      status) exec curl -fsS "http://127.0.0.1:7570/health" ;;
      *) echo "usage: omacar work serve|status" >&2; exit 2 ;;
    esac ;;
```

`share/systemd/omacar-work.service`:

```ini
[Unit]
Description=OmaCar Work relay (voice to coding sessions)
After=network-online.target

[Service]
ExecStart=%h/Projects/omacar/bin/omacar work serve
Restart=on-failure
RestartSec=10
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin

[Install]
WantedBy=default.target
```

Not enabled by `install.sh`; the owner enables it by hand (`systemctl --user enable --now omacar-work`) at rollout step 2.

- [ ] **Step 5: Run the tests** — `python3 test/workroutes_test.py` → `all passed`; `python3 test/demoserve_test.py` → its usual pass count plus two.

- [ ] **Step 6: Commit**

```bash
git add lib/workroutes.py lib/api.py lib/serve.py test/demoserve_test.py bin/omacar \
        share/systemd/omacar-work.service test/workroutes_test.py test/all.sh
git commit -m "Work: the app's routes — an on-disk queue to the relay, secrets never on disk, refused in the demo"
```

---

### Task 9: The page — recorder, speech queue, Work tab

**Files:**
- Create: `share/js/work/recorder.js`
- Create: `share/js/work/speech.js`
- Modify: `share/js/views/work.js` (replace the placeholder)
- Modify: `share/js/core.js` (`api.work*` helpers)
- Create: `share/css/work.css` (and link it wherever the other view stylesheets are linked — search `index.html` for `roadcams.css`)
- Test: `test/js/workspeech.test.js`, `test/js/workview.test.js`

**Interfaces:**
- Consumes: `/api/work/*` (Task 8); `store.moving` (core.js); `alertPlayer().voices` and `audioContext()`, `voiceIn()` (audiobus.js; the `voice` bus is silent in the live app today, so Work's speech uses it); `schedule('music', …)` for the duck.
- Produces:
  - `recorder.js`: `export const SILENCE_S = 1.2, MAX_S = 30;` `export function silenceStop(levels: number[], dtS: number, threshold = 0.02) -> boolean` (pure: true when the trailing quiet run ≥ SILENCE_S after some sound); `export async function record({ getStream, onLevel, now }) -> Blob`.
  - `speech.js`: `export const REPLAY_S = 120;` `export function createSpeech({ play, alertBusy, now }) -> { say(item), tick(), get queue, get current }` where `item = { id, at, line, clip }`; pure scheduling: never two at once; if `alertBusy()` while playing → stop, requeue at front with its original `at`; on `tick()` with no alert and nothing playing → play the front item unless `now() - item.at > REPLAY_S` *and* it was interrupted (then drop it).
  - `views/work.js`: default export `workView(root)`.
- `core.js` additions to `api`:
  ```js
  workStatus: () => req("/api/work/status"),
  workEvents: (after) => req("/api/work/events?after=" + after),
  workClip: (name) => req("/api/work/clip/" + name),
  workSay: (body) => req("/api/work/utterance", { method: "POST", body: JSON.stringify(body) }),
  ```

The view:
- A large push-to-talk button (min 160 × 160 px, the screen's accent), the label "Talk to Work". Tap: short rising tone (two `OscillatorNode` notes on the voice bus, 0.15 s), duck music (`schedule('music', …)` −12 dB more, restored after), record until `silenceStop` or `MAX_S`, then `api.workSay({client_id: crypto.randomUUID(), audio_b64, mime})`.
- If `workSay` does not answer within 5 s, play the pre-rendered `share/assets/work/no_signal.wav` (installed in Task 10) once.
- Polls `api.workEvents(lastId)` every 2 s while the view is mounted **and** every 5 s from a background hook registered in `main.js`'s existing clock (so lines are heard on other tabs); each event with a `clip` goes to `speech.say()`.
- "Work is offline" plays once per drive when `online` turns false (a drive = since the app last saw `store.moving` become true).
- Session list: one row per session, `project · state · last line`, newest first.
- A pending question card shows the question in large text and repeats the push-to-talk button.
- **While `store.moving`:** nothing else is shown — no PR links, no text longer than one line per session.
- **While parked:** each row with a PR shows "Draft PR #n", a link to `https://github.com/<repo>/pull/n` built from the status row's `repo` (Task 7), the number alone when `repo` is empty.
- A question still waiting is said again the next time the car stops (`repeatQuestion`).
- Music ducks 12 dB further under each spoken line and glides back after it.

- [ ] **Step 1: Write the failing tests**

`test/js/workspeech.test.js`:

```js
import { eq, ok } from "./assert.js";
import { createSpeech, REPLAY_S, repeatQuestion, offlineOnce } from "../js/work/speech.js";
import { silenceStop, SILENCE_S } from "../js/work/recorder.js";

function rig() {
  const log = []; let t = 0; let alert = false; let playing = null;
  const s = createSpeech({
    play: (item, done) => { playing = { item, done }; log.push("play " + item.id); return () => { log.push("cut " + item.id); }; },
    alertBusy: () => alert, now: () => t,
  });
  return { s, log, set t(v) { t = v; }, get t() { return t; }, alert: (v) => { alert = v; }, finish: () => { const p = playing; playing = null; p.done(); } };
}

export default [
  ["one line plays", () => { const r = rig(); r.s.say({ id: 1, at: 0, clip: "1.wav" }); r.s.tick(); eq(r.log, ["play 1"]); }],
  ["two never overlap", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.say({ id: 2, at: 0 }); r.s.tick(); r.s.tick(); eq(r.log, ["play 1"]); r.finish(); r.s.tick(); eq(r.log, ["play 1", "play 2"]); }],
  ["an alert cuts speech off at once", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); eq(r.log, ["play 1", "cut 1"]); }],
  ["nothing starts during an alert", () => { const r = rig(); r.alert(true); r.s.say({ id: 1, at: 0 }); r.s.tick(); eq(r.log, []); }],
  ["the cut line replays after the alert", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); r.alert(false); r.t = 30; r.s.tick(); eq(r.log, ["play 1", "cut 1", "play 1"]); }],
  ["unless it is over two minutes old", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.alert(true); r.s.tick(); r.alert(false); r.t = REPLAY_S + 1; r.s.tick(); eq(r.log, ["play 1", "cut 1"]); }],
  ["the same event twice is said once", () => { const r = rig(); r.s.say({ id: 1, at: 0 }); r.s.say({ id: 1, at: 0 }); r.s.tick(); r.finish(); r.s.tick(); eq(r.log, ["play 1"]); }],
  ["a pending question is said again when the car next stops", () =>
    eq([repeatQuestion(40, 0, true, false), repeatQuestion(0, 0, true, false),
        repeatQuestion(40, 0, true, true), repeatQuestion(40, 0, false, false)],
       [true, false, false, false])],
  ["offline is said once per drive", () => eq(
    [offlineOnce({ online: false, said: false, newDrive: false }),
     offlineOnce({ online: false, said: true, newDrive: false }),
     offlineOnce({ online: false, said: true, newDrive: true })],
    [true, false, true])],
  ["silence stop needs sound first", () => { eq(silenceStop(Array(100).fill(0), 0.05), false); }],
  ["then 1.2 s of quiet stops it", () => { const lv = [...Array(10).fill(0.3), ...Array(Math.ceil(SILENCE_S / 0.05)).fill(0)]; eq(silenceStop(lv, 0.05), true); }],
  ["1.1 s of quiet does not", () => { const lv = [...Array(10).fill(0.3), ...Array(22).fill(0)]; eq(silenceStop(lv, 0.05), false); }],
];
```

`test/js/workview.test.js` — the view mounted for real against stubbed `api` and `store`, the way `test/js/roadcams.test.js` does it (`withScreen`):

```js
import { eq, ok } from "./assert.js";
import workView from "../js/views/work.js";
import { store, api } from "../js/core.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const SESSION = { id: "s1", project: "omacar", state: "done", pr: 41, repo: "omauilabs/omacar" };

async function withWork(opts, fn) {
  const was = { workStatus: api.workStatus, workEvents: api.workEvents, workClip: api.workClip };
  api.workStatus = async () => ({ online: true, sessions: opts.sessions || [SESSION] });
  api.workEvents = async () => ({ online: opts.online !== false, events: opts.events || [] });
  api.workClip = async () => ({ wav_b64: "" });
  const clock = { t: 1e12 };
  const wasClock = store.clock;
  store.clock = () => clock.t;
  store.motionLatched = false; store.stillSince = null; store.lastMoving = false;
  store.live = { connected: true, values: { SPEED: opts.speed || 0, RPM: 0 } };
  store.emit("live");
  const root = document.createElement("div");
  document.body.appendChild(root);
  const unmount = workView(root);
  try {
    for (let i = 0; i < 40 && !root.querySelector(".work-row"); i++) await wait(25);
    await fn(root);
  } finally {
    unmount(); root.remove(); Object.assign(api, was);
    store.clock = wasClock; store.live = null; store.lastMoving = false;
    store.motionLatched = false; store.stillSince = null;
  }
}

export default [
  ["moving: no pull request on screen", () => withWork({ speed: 60 }, (root) =>
    eq(root.querySelector(".work-pr"), null))],
  ["parked: the draft PR, linked", () => withWork({}, (root) => {
    const a = root.querySelector(".work-pr");
    ok(a && a.textContent.includes("41"), "PR 41 shown");
    eq(a.getAttribute("href"), "https://github.com/omauilabs/omacar/pull/41");
  })],
  ["a pending question is shown large", () => withWork({ events: [
    { id: 1, at: 1, kind: "question", session: "s1", line: "omacar has a question: 0.8 s?" }] },
    (root) => eq(root.querySelector(".work-question").textContent, "omacar has a question: 0.8 s?"))],
  ["the talk button is thumb-sized", () => withWork({}, (root) => {
    const r = root.querySelector(".work-talk").getBoundingClientRect();
    ok(r.width >= 160 && r.height >= 160, `talk button ${r.width}x${r.height}`);
  })],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 test/js_test.py`
Expected: FAIL in `workspeech.test.js` (module not found) and `workview.test.js`.

- [ ] **Step 3: Implement `share/js/work/speech.js`**

```js
// Work's speech queue: one line at a time, and alerts always win.
//
// An alert (drowsy or any other) cuts a line off at once. The line goes back
// to the front and plays again when the alert is over -- unless it is more
// than REPLAY_S old by then, because a status from three minutes ago is no
// longer news. Pure scheduling: `play` and `alertBusy` are given, so the
// tests run without an AudioContext.
export const REPLAY_S = 120;

// A question still waiting is said again the next time the car stops: speed
// fell from over 3 km/h to 3 or less, and it has not been repeated yet.
export function repeatQuestion(prevSpeed, speed, pending, repeated) {
  return pending && !repeated && prevSpeed > 3 && speed <= 3;
}

// "Work is offline" once per drive, not on every poll.
export function offlineOnce({ online, said, newDrive }) {
  return !online && (!said || newDrive);
}

export function createSpeech({ play, alertBusy, now }) {
  const queue = [];
  const seen = new Set();
  let current = null;   // { item, cut }
  return {
    say(item) {
      if (seen.has(item.id)) return;
      seen.add(item.id);
      queue.push({ ...item, interrupted: false });
    },
    tick() {
      if (current && alertBusy()) {
        current.cut();
        queue.unshift({ ...current.item, interrupted: true });
        current = null;
        return;
      }
      if (current || alertBusy() || !queue.length) return;
      let item = queue.shift();
      while (item && item.interrupted && now() - item.at > REPLAY_S) item = queue.shift();
      if (!item) return;
      const cut = play(item, () => { if (current && current.item === item) current = null; });
      current = { item, cut };
    },
    get queue() { return queue; },
    get current() { return current && current.item; },
  };
}
```

Note: event `at` is in seconds (relay clock), so `now` must also return seconds (`Date.now() / 1000`).

- [ ] **Step 4: Implement `share/js/work/recorder.js`**

```js
// Push-to-talk: record from the tablet's microphone until the driver stops
// talking (SILENCE_S of quiet after some sound) or MAX_S, whichever is first.
export const SILENCE_S = 1.2;
export const MAX_S = 30;

export function silenceStop(levels, dtS, threshold = 0.02) {
  const heard = levels.findIndex((l) => l > threshold);
  if (heard < 0) return false;
  let quiet = 0;
  for (let i = levels.length - 1; i > heard && levels[i] <= threshold; i--) quiet++;
  return quiet * dtS >= SILENCE_S;
}

export async function record({ getStream = () => navigator.mediaDevices.getUserMedia({ audio: true }),
                               ctx, onLevel = () => {} } = {}) {
  const stream = await getStream();
  const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const src = ctx.createMediaStreamSource(stream);
  const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
  const buf = new Float32Array(an.fftSize);
  const levels = []; const dt = 0.05; const started = performance.now();
  rec.start();
  await new Promise((resolve) => {
    const timer = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
      levels.push(rms); onLevel(rms);
      if (silenceStop(levels, dt) || (performance.now() - started) / 1000 >= MAX_S) {
        clearInterval(timer); resolve();
      }
    }, dt * 1000);
  });
  const done = new Promise((r) => { rec.onstop = r; });
  rec.stop(); await done;
  stream.getTracks().forEach((t) => t.stop());
  src.disconnect();
  return new Blob(chunks, { type: "audio/webm" });
}
```

- [ ] **Step 5: Implement `share/js/views/work.js`**

```js
// The Work tab: talk to your coding sessions from the car.
// Design: doc/design/2026-10-13-work-voice-relay.md. While moving it shows one
// button and one line per session; pull requests appear only while parked.
import { h, clear, store, api } from "../core.js";
import { audioContext, voiceIn, resume, schedule, currentDb, MUSIC_DB } from "../audiobus.js";
import { glidePlan } from "../ramps.js";
import { alertPlayer } from "../alertplayer.js";
import { record } from "../work/recorder.js";
import { createSpeech, repeatQuestion, offlineOnce } from "../work/speech.js";

const POLL_MS = 2000;
let speech = null;
let lastId = 0;
let offlineSaid = false;
let wasMoving = false;
let prevSpeed = 0;
let question = null;      // { event, repeated }

// Music ducks under speech as it does under alerts: 12 dB further down while
// a line plays, glided, and back after it.
const DUCK_DB = 12;
function duck(on) {
  const ctx = audioContext();
  const to = on ? MUSIC_DB - DUCK_DB : MUSIC_DB;
  schedule("music", glidePlan(currentDb("music"), to, 0.3), ctx.currentTime);
}

async function playWav(b64, done) {
  const ctx = audioContext();
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const buf = await ctx.decodeAudioData(bytes.buffer);
  const src = ctx.createBufferSource();
  src.buffer = buf; src.connect(voiceIn());
  duck(true);
  src.onended = () => { duck(false); done(); };
  src.start();
  return () => { try { src.stop(); } catch { /* already ended */ } };
}

function getSpeech() {
  if (speech) return speech;
  speech = createSpeech({
    alertBusy: () => alertPlayer().voices.some((v) => v.end > audioContext().currentTime),
    now: () => Date.now() / 1000,
    play: (item, done) => {
      let cut = () => {};
      api.workClip(item.clip).then((r) => playWav(r.wav_b64, done)).then((c) => { cut = c; })
        .catch(done);
      return () => cut();
    },
  });
  setInterval(() => speech.tick(), 200);
  return speech;
}

function tone() {
  const ctx = audioContext(); const t = ctx.currentTime;
  [[523.25, 0], [783.99, 0.08]].forEach(([hz, at]) => {
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.frequency.value = hz; g.gain.setValueAtTime(0.3, t + at); g.gain.linearRampToValueAtTime(0, t + at + 0.07);
    o.connect(g); g.connect(voiceIn()); o.start(t + at); o.stop(t + at + 0.08);
  });
}

async function talk() {
  await resume(); tone();
  const blob = await record({ ctx: audioContext() });
  const b64 = btoa(String.fromCharCode(...new Uint8Array(await blob.arrayBuffer())));
  const sent = api.workSay({ client_id: crypto.randomUUID(), audio_b64: b64, mime: blob.type });
  const slow = new Promise((r) => setTimeout(() => r("slow"), 5000));
  if ((await Promise.race([sent.then(() => "ok", () => "slow"), slow])) === "slow") {
    getSpeech().say({ id: "no_signal-" + Date.now(), at: Date.now() / 1000, local: "no_signal" });
  }
}

export async function pollWork() {
  const r = await api.workEvents(lastId).catch(() => null);
  if (!r) return null;
  const newDrive = store.moving && !wasMoving;
  wasMoving = store.moving;
  if (offlineOnce({ online: r.online, said: offlineSaid, newDrive })) {
    offlineSaid = true;
    getSpeech().say({ id: "offline-" + Date.now(), at: Date.now() / 1000, local: "offline" });
  }
  for (const e of r.events) {
    lastId = Math.max(lastId, e.id);
    if (e.kind === "question") question = { event: e, repeated: false };
    if (["pr_opened", "gate_failed", "stopped", "stuck", "finished", "limit"].includes(e.kind)
        && question && question.event.session === e.session) question = null;
    if (e.clip) getSpeech().say({ id: e.id, at: e.at, clip: e.clip, line: e.line });
  }
  const speed = ((store.live || {}).values || {}).SPEED || 0;
  if (question && question.event.clip && repeatQuestion(prevSpeed, speed, true, question.repeated)) {
    question.repeated = true;
    const e = question.event;
    getSpeech().say({ id: `${e.id}-again`, at: Date.now() / 1000, clip: e.clip, line: e.line });
  }
  prevSpeed = speed;
  return r;
}

export default function workView(root) {
  const list = h("div", { class: "work-list" });
  const qcard = h("div", { class: "work-question", hidden: true });
  const btn = h("button", { class: "work-talk", onclick: () => talk() }, "Talk to Work");
  clear(root);
  root.append(h("div", { class: "work" }, btn, qcard, list));
  const lines = new Map();
  async function refresh() {
    const r = await pollWork();
    if (r) for (const e of r.events) if (e.session) lines.set(e.session, e.line);
    const st = await api.workStatus().catch(() => null);
    clear(list);
    const pendingQ = r && [...r.events].reverse().find((e) => e.kind === "question");
    qcard.hidden = !pendingQ; if (pendingQ) qcard.textContent = pendingQ.line;
    for (const s of (st && st.sessions) || []) {
      const row = h("div", { class: "work-row" },
        h("span", { class: "work-proj" }, s.project), " · ", s.state, " · ",
        h("span", { class: "work-line" }, lines.get(s.id) || ""));
      if (!store.moving && s.pr) {
        row.append(s.repo
          ? h("a", { class: "work-pr", href: `https://github.com/${s.repo}/pull/${s.pr}` }, ` Draft PR #${s.pr}`)
          : h("span", { class: "work-pr" }, ` Draft PR #${s.pr}`));
      }
      list.append(row);
    }
  }
  refresh();
  const timer = setInterval(refresh, POLL_MS);
  return () => clearInterval(timer);
}
```

Local lines (`local: "no_signal"` / `"offline"`): in `getSpeech().play`, when `item.local` is set, fetch `/assets/work/<local>.wav` (the file `share/assets/work/<local>.wav`) (installed in Task 10) instead of `api.workClip`. Add that branch:

```js
    play: (item, done) => {
      let cut = () => {};
      const fetchB64 = item.local
        ? fetch(`/assets/work/${item.local}.wav`).then((r) => r.arrayBuffer())
            .then((ab) => btoa(String.fromCharCode(...new Uint8Array(ab))))
        : api.workClip(item.clip).then((r) => r.wav_b64);
      fetchB64.then((b64) => playWav(b64, done)).then((c) => { cut = c; }).catch(done);
      return () => cut();
    },
```

`share/` is the server's static root (it holds `app.html`, `js/`, `css/`), so `share/assets/work/no_signal.wav` is `/assets/work/no_signal.wav`.

Background polling: in `share/js/main.js`, where the app's slow clock runs (search for the existing `setInterval` that refreshes `api.live` or the drowsy loop), add `import { pollWork } from "./views/work.js";` and call `pollWork()` every 5 s. If the view returns its own cleanup (as above), make sure `main.js` honours it the same way other views' returns are honoured (search for how `mount` return values are used; if they are ignored, use a module-level `mounted` flag instead of returning the cleanup).

`share/css/work.css`:

```css
.work { display: grid; gap: 24px; justify-items: center; padding: 24px; }
.work-talk { min-width: 180px; min-height: 180px; border-radius: 50%; font-size: 1.4rem;
  background: var(--accent); color: var(--on-accent, #fff); border: none; }
.work-question { font-size: 1.6rem; max-width: 40ch; text-align: center; }
.work-list { width: min(720px, 100%); display: grid; gap: 12px; font-size: 1.2rem; }
.work-row { display: flex; gap: 6px; flex-wrap: wrap; }
.work-pr { font-weight: 600; }
```

Use the CSS custom property names the other views use for the accent (search `share/css` for `--accent`; if the name differs, use that).

- [ ] **Step 6: Run the JS tests** — `python3 test/js_test.py` → workspeech and workview cases pass, nothing else regresses.

- [ ] **Step 7: Commit**

```bash
git add share/js/work share/js/views/work.js share/js/core.js share/js/main.js share/css/work.css \
        test/js/workspeech.test.js test/js/workview.test.js
git add -u   # index.html stylesheet link
git commit -m "Work: the tab — push-to-talk, a speech queue that yields to alerts, PRs only while parked"
```

---

### Task 10: Install, offline lines, docs, roadmap

**Files:**
- Create: `tools/work_render_offline.py`
- Create: `share/assets/work/.gitignore` (`*.wav` — rendered on the machine; the Piper voice's dataset is CC BY-NC-SA, so its output is not committed)
- Modify: `install.sh` (render the offline lines when Piper is present; `pip install claude-agent-sdk faster-whisper` into the venv **only when** `OMACAR_WORK=1`)
- Create: `doc/work.md`
- Modify: `doc/capabilities.json` (`agent.work`: status `building`, evidence adds `lib/workrelay.py`, `lib/workgates.py`, `share/js/views/work.js`, `doc/work.md`)

**Interfaces:**
- Consumes: `workvoice.OFFLINE_LINES`, `workvoice.render`.
- Produces: `share/assets/work/{no_signal,offline,not_started}.wav` on the machine; `omacar work` documented.

- [ ] **Step 1: `tools/work_render_offline.py`**

```python
#!/usr/bin/env python3
"""Render the lines the tablet must be able to say with no relay.
Run where Piper is installed; writes share/assets/work/<name>.wav."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workvoice  # noqa: E402

out = os.path.join(ROOT, "share", "assets", "work")
os.makedirs(out, exist_ok=True)
bad = [n for n, text in workvoice.OFFLINE_LINES.items()
       if not workvoice.render(text, os.path.join(out, f"{n}.wav"))]
print("rendered" if not bad else f"Piper missing or failed for: {', '.join(bad)}")
sys.exit(1 if bad else 0)
```

- [ ] **Step 2: `install.sh`** — after the venv setup block (search for `venv/bin/pip`), add:

```bash
if [[ "${OMACAR_WORK:-0}" == 1 ]]; then
  "$VENV/bin/pip" install --quiet claude-agent-sdk faster-whisper
  command -v piper >/dev/null && "$VENV/bin/python" "$ROOT/tools/work_render_offline.py" \
    || echo "  Work: install Piper (system package) for spoken lines"
fi
```

- [ ] **Step 3: `doc/work.md`** — the owner's how-to, in the repo's plain style: what Work is; the safety rules (copy the Global Constraints list); `work.toml` example; the tablet setup (`OMACAR_WORK=1 ./install.sh`, copy `examples/work.toml`, point `path` at a **separate clone**, never `~/Projects/omacar`, `systemctl --user enable --now omacar-work`); the four rollout steps from the spec; the move to the box (install Claude Code there, `host = "box"`, set `relay_url` on the tablet to `http://<box tailnet name>:7570`); what you hear and when.

- [ ] **Step 4: Capability** — edit `doc/capabilities.json` `agent.work` as listed above; run `python3 test/roadmap_test.py` → passes (the new evidence files are tracked once committed; commit first if it complains, then re-run).

- [ ] **Step 5: The full gate**

Run: `test/all.sh` on the box mirror (`~/Projects/.omacar-test/work`, synced from this worktree) — it is the repo's gate and runs the Chromium-backed JS suite.
Expected: every suite passes, including the six new `work*` suites.

- [ ] **Step 6: Commit**

```bash
git add tools/work_render_offline.py share/assets/work/.gitignore install.sh doc/work.md \
        doc/capabilities.json
git commit -m "Work: install, offline lines, the owner's how-to, and agent.work to building"
```

---

### Task 11: Rollout step 2 — one real dispatch on the tablet (owner present)

Not code. Done with the owner, parked, after Tasks 1–10 are merged to a branch on the tablet's worktree (never the live checkout).

- [ ] On the tablet: `cd ~/Projects/.omacar-wt/work && OMACAR_WORK=1 ./install.sh`; `pacman -Q piper-tts` or install it (owner, sudo).
- [ ] Clone a scratch repo to `~/Projects/work-scratch` (owner creates `omauilabs/work-scratch`, private, with `test.sh` that runs `true`).
- [ ] `~/.config/omacar/work.toml`: `host = "tablet"`, one project `scratch` → that path, gate `./test.sh`.
- [ ] `systemctl --user start omacar-work` (not enable); `omacar work status` shows `parked: true` in the driveway.
- [ ] Owner taps Talk, says "in scratch add a hello file", hears the read-back, says "yes".
- [ ] Expect, in order: "scratch session started." → "scratch: gate passed, draft PR 1 opened." A draft PR on `work/add-a-hello-file-xxxx` exists; `main` untouched.
- [ ] Negative check: say "in scratch push straight to main"; expect the session's refusal line and no push to `main` (`git log origin/main` unchanged).
- [ ] `systemctl --user stop omacar-work`. Record results in `doc/work.md` under "Measured", with the commands.
