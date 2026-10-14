#!/usr/bin/env python3
"""A Work session against a fake SDK and a fake git: the events, the question,
the gate repairs, the timeouts. Nothing reaches Claude, git or GitHub."""
import asyncio, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import worksession as ws, workcfg  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

class Result:  # stands in for ResultMessage
    def __init__(self, sid, limited=False, subtype=None):
        self.session_id = sid; self.is_error = limited or subtype == "error_during_execution"
        self.subtype = subtype
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
            if step[0] == "sandbox": yield Result("sdk-1", subtype="error_during_execution"); return
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
    def tree_problem(self, branch): return ""
    def push(self, wt, branch): self.pushed.append(branch)
    def draft_pr(self, wt, branch, title, body): self.prs.append((title, body)); return 41

HOME = tempfile.mkdtemp()
PROJ = workcfg.Project("omacar", ("omacar",), os.path.join(HOME, "src"), "test/all.sh")

CLIENTS = []
def run(script, gates, answer="configurable", timeouts=None, ask_delay=0, stop_at=None, git=None):
    events = []
    async def ask(q):
        if answer is None: await asyncio.sleep(10)
        await asyncio.sleep(ask_delay)
        return answer
    git = git or FakeGit(gates)
    def factory(opts):
        c = FakeClient(script, s); CLIENTS.append(c); return c
    s = ws.Session(PROJ, "fix the timeout", emit=events.append, ask=ask, home=HOME,
                   host="tablet", git=git, timeouts=timeouts or ws.TIMEOUTS,
                   client_factory=factory, options_factory=lambda: None)
    async def main():
        task = asyncio.ensure_future(s.run())
        if stop_at is not None:
            await asyncio.sleep(stop_at); await s.stop(); await s.stop()
        await task
    asyncio.run(main())
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

print("\n  the sandbox is the wall")
env = ws.session_env({"XDG_RUNTIME_DIR": "/run/user/1", "WAYLAND_DISPLAY": "w", "HYPRLAND_INSTANCE_SIGNATURE": "h"})
check("desktop sockets' variables blanked", sorted(env.values()), ["", "", ""])
sb = ws.sandbox_settings(workcfg.Project("p", ("p",), "/x", "t"))
check("sandbox on, no way out, fails closed",
      (sb["enabled"], sb["allowUnsandboxedCommands"], sb["failIfUnavailable"]), (True, False, True))
check("no network by default", sb["network"]["allowedDomains"], [])
env = ws.session_env({"PATH": "/usr/bin", "GH_TOKEN": "t", "SSH_AUTH_SOCK": "/s",
                      "DBUS_SESSION_BUS_ADDRESS": "unix:x"})
check("credentials blanked, PATH kept",
      (env["PATH"], env["GH_TOKEN"], env["SSH_AUTH_SOCK"], env["DBUS_SESSION_BUS_ADDRESS"]),
      ("/usr/bin", "", "", ""))

print("\n  a usage limit stops it, with nothing pushed")
s, ev, git = run([("limit",)], [True])
check("limit", (ev[-1]["kind"], git.pushed), ("limit", []))

print("\n  a sandbox that cannot start is stuck, with nothing pushed")
s, ev, git = run([("sandbox",)], [True])
check("stuck, sandbox unavailable", (ev[-1]["kind"], ev[-1].get("why")), ("stuck", "sandbox unavailable"))
check("nothing pushed", (git.pushed, git.prs), ([], []))
s, ev, git = run([("tool", "Read", {"file_path": "x"}), ("sandbox",)], [True])
check("an error after a tool ran is stuck with its own why",
      (ev[-1]["kind"], ev[-1].get("why")), ("stuck", "the session failed"))
check("and pushes nothing", git.pushed, [])

print("\n  silence is stuck")
s, ev, git = run([("silence", 0.2), ("result",)], [True], timeouts={"question": 5, "silent": 0.05})
check("stuck", ev[-1]["kind"], "stuck")
check("nothing pushed", git.pushed, [])

print("\n  stopping really stops")
slow = [("silence", 1), ("result",)]
s, ev, git = run(slow, [True], stop_at=0.1)
check("stop during a drain: ends stopped, no push", ([e["kind"] for e in ev], git.pushed, git.prs), (["started", "stopped"], [], []))
s, ev, git = run([("ask", "Which?"), ("result",)], [True], answer=None, stop_at=0.1)
check("stop during a question: stopped, no push", ([e["kind"] for e in ev], git.pushed), (["started", "question", "stopped"], []))
check("state stays stopped", s.state, "stopped")
class StopGit(FakeGit):
    async def gate(self, wt, cmd): await self.s.stop(); return True, ""
g = StopGit([]); 
def run_stop_gate():
    global g
    events = []
    async def ask(q): return "x"
    s = ws.Session(PROJ, "fix the timeout", emit=events.append, ask=ask, home=HOME, host="tablet",
                   git=g, client_factory=lambda o: FakeClient([("result",)], s), options_factory=lambda: None)
    g.s = s
    asyncio.run(s.run()); return events
ev = run_stop_gate()
check("stop between gate and push: nothing pushed", ([e["kind"] for e in ev], g.pushed, g.prs), (["started", "stopped"], [], []))

print("\n  a quiet spell during a question does not end the stream")
CLIENTS.clear()
s, ev, git = run([("ask", "Which one?"), ("result",)], [True], ask_delay=0.3,
                 timeouts={"question": 5, "silent": 0.1})
check("continues to a PR", ev[-1]["kind"], "pr_opened")
check("answer delivered", CLIENTS[0].answer["updated_input"]["answers"]["Which one?"], "configurable")

print("\n  a stop landing during the import is never followed by a push")
class StopInImport(FakeGit):
    def has_commits(self, wt, base=None):
        asyncio.run_coroutine_threadsafe(self.s.stop(), self.loop).result(); return True
def run_with(git, script=(("result",),)):
    events = []
    async def ask(q): return "x"
    async def main():
        git.loop = asyncio.get_running_loop()
        s = ws.Session(PROJ, "fix the timeout", emit=events.append, ask=ask, home=HOME, host="tablet",
                       git=git, client_factory=lambda o: FakeClient(list(script), s), options_factory=lambda: None)
        git.s = s
        await s.run(); return s
    s = asyncio.run(main()); return s, events
g = StopInImport([True]); s, ev = run_with(g)
check("stop during has_commits: nothing pushed", ([e["kind"] for e in ev], g.pushed, g.prs), (["started", "stopped"], [], []))

print("\n  state and pr after a stop")
class StopInPR(FakeGit):
    def draft_pr(self, wt, b, t, body):
        asyncio.run_coroutine_threadsafe(self.s.stop(), self.loop).result(); return 41
g = StopInPR([True]); s, ev = run_with(g)
check("state stays stopped, pr recorded, one event", (s.state, s.pr, [e["kind"] for e in ev]), ("stopped", 41, ["started", "stopped"]))

print("\n  stop while connecting")
class SlowConnect(FakeClient):
    async def __aenter__(self): await self.s.stop(); return self
g = FakeGit([True]); events = []
async def _m():
    async def ask(q): return "x"
    s = ws.Session(PROJ, "fix the timeout", emit=events.append, ask=ask, home=HOME, host="tablet",
                   git=g, client_factory=lambda o: SlowConnect([("result",)], s), options_factory=lambda: None)
    await s.run(); return s
s = asyncio.run(_m())
check("no query, no push", (s.client.queries, g.pushed, [e["kind"] for e in events]), ([], [], ["started", "stopped"]))

print("\n  stop after the run is a no-op")
s, ev, git = run([("result",)], [True])
n = len(ev); asyncio.run(s.stop())
check("no second terminal event, state kept", (len(ev), s.state), (n, "done"))

print("\n  failures after prepare are spoken")
class BadPush(FakeGit):
    def push(self, wt, b): raise RuntimeError("push rejected")
s, ev, git = run([("result",)], [True], git=BadPush([True]))
check("failing push: stuck, no PR", (ev[-1]["kind"], ev[-1]["why"], git.prs, s.state), ("stuck", "push rejected", [], "failed"))
class BadPR(FakeGit):
    def draft_pr(self, wt, b, t, body): raise RuntimeError("gh broke")
s, ev, git = run([("result",)], [True], git=BadPR([True]))
check("failing draft_pr", (ev[-1]["kind"], ev[-1]["why"], git.pushed), ("stuck", "pushed, PR not opened", [s.branch]))

print("\n  a limit during a repair is a limit")
s, ev, git = run([("result",), ("limit",)], [False])
check("limit after repair", (ev[-1]["kind"], git.pushed), ("limit", []))

print("\n  OmaCar's mcp.py does not shadow the SDK's mcp")
import workprivacy  # noqa: E402,F401
check("mcp is not OmaCar's", hasattr(sys.modules.get("mcp"), "VEHICLE_SECRET"), False)
FAKE = tempfile.mkdtemp()
os.makedirs(FAKE + "/claude_agent_sdk"); os.makedirs(FAKE + "/mcp")
open(FAKE + "/claude_agent_sdk/__init__.py", "w").write("from mcp.types import X\n")
open(FAKE + "/mcp/__init__.py", "w").write("")
open(FAKE + "/mcp/types.py", "w").write("X = 1\n")
sys.path.append(FAKE)
sys.modules.pop("mcp", None)
try:
    check("_sdk() imports it despite lib/mcp.py", ws._sdk().X, 1)
except Exception as e:
    check("_sdk() imports it despite lib/mcp.py", repr(e), "ok")
for m in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.") or m.startswith("claude_agent_sdk")]:
    del sys.modules[m]
sys.path.remove(FAKE); shutil.rmtree(FAKE, ignore_errors=True)
check("lib stays on sys.path", os.path.join(ROOT, "lib") in sys.path, True)

print("\n  the gate runs in bubblewrap")
GH = tempfile.mkdtemp()
os.makedirs(GH + "/.config/gh"); open(GH + "/.git-credentials", "w").write("x")
argv = ws.gate_argv("test/all.sh --fast", "/wt", GH, [GH + "/.config/gh", GH + "/.git-credentials", GH + "/.aws"])
check("argv starts with bwrap, unshares all", (argv[0], "--unshare-all" in argv), ("bwrap", True))
binds = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
check("only the worktree is writable", binds, ["/wt"])
check("existing dir hidden by tmpfs", argv[argv.index(GH + "/.config/gh") - 1], "--tmpfs")
check("existing file hidden by /dev/null", argv[argv.index(GH + "/.git-credentials") - 2:argv.index(GH + "/.git-credentials") + 1], ["--ro-bind", "/dev/null", GH + "/.git-credentials"])
check("host IPC and caps closed", ("--unshare-all" in argv, "--share-net" in argv,
      argv[argv.index("--cap-drop") + 1]), (True, False, "ALL"))
argv_real = ws.gate_argv("c", "/wt", GH, [], is_real_dir=lambda p: True)
argv_link = ws.gate_argv("c", "/wt", GH, [], is_real_dir=lambda p: False)
check("real /var/run: tmpfs over /run and /var/run, after the root bind",
      [argv_real[argv_real.index(d) - 1] for d in ("/run", "/var/run")]
      + [argv_real.index("/run") > argv_real.index("--ro-bind")], ["--tmpfs", "--tmpfs", True])
check("symlinked /var/run: skipped, /run still hidden",
      ("/var/run" in argv_link, argv_link[argv_link.index("/run") - 1]), (False, "--tmpfs"))
check("missing path skipped", GH + "/.aws" in argv, False)
check("ends with the split command", argv[-3:], ["--", "test/all.sh", "--fast"])
check("--chdir worktree before --", argv[argv.index("--chdir") + 1], "/wt")
shutil.rmtree(GH, ignore_errors=True)
orig = ws.shutil.which; ws.shutil.which = lambda n: None
try:
    r = asyncio.run(ws.Git().gate("/nonexistent", "touch /tmp/should-not-exist-xyz"))
    check("no bwrap: fails closed without running", (r[0], os.path.exists("/tmp/should-not-exist-xyz")), (False, False))
finally:
    ws.shutil.which = orig

print("\n  real git: the agent's clone, the runner's push")
T = tempfile.mkdtemp()
def sh(*a, cwd=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
    return subprocess.run(a, cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout
sh("git", "init", "-q", "--bare", "-b", "main", T + "/origin.git")
sh("git", "clone", "-q", T + "/origin.git", T + "/proj")
open(T + "/proj/a.txt", "w").write("a"); sh("git", "add", ".", cwd=T + "/proj"); sh("git", "commit", "-qm", "one", cwd=T + "/proj")
sh("git", "push", "-q", "origin", "HEAD:main", cwd=T + "/proj")
sh("git", "remote", "set-head", "origin", "main", cwd=T + "/proj")
sh("git", "checkout", "-q", "-b", "other", cwd=T + "/proj"); sh("git", "branch", "-D", "main", cwd=T + "/proj")
gp = workcfg.Project("p", ("p",), T + "/proj", "true")
real = ws.Git(); sess = T + "/Projects/.omacar-work/p-x"
real.prepare(gp, "work/x-1234", sess)
check("no local main needed; session starts at origin/main's tip",
      sh("git", "rev-parse", "HEAD", cwd=sess).strip(), sh("git", "rev-parse", "origin/main", cwd=T + "/proj").strip())
check("session folder is a clone on the work branch", sh("git", "branch", "--show-current", cwd=sess).strip(), "work/x-1234")
check("origin points at the real origin", sh("git", "remote", "get-url", "origin", cwd=sess).strip(), T + "/origin.git")
check("nothing to push yet", real.has_commits(sess), False)
open(sess + "/b.txt", "w").write("b"); sh("git", "add", ".", cwd=sess); sh("git", "commit", "-qm", "two", cwd=sess)
os.makedirs(sess + "/evil"); MARK = T + "/marker"
for hookdir in (sess + "/.git/hooks", sess + "/evil"):
    for name in ("pre-push", "reference-transaction"):
        open(hookdir + "/" + name, "w").write(f"#!/bin/sh\ntouch {MARK}\n")
        os.chmod(hookdir + "/" + name, 0o755)
sh("git", "config", "core.hooksPath", sess + "/evil", cwd=sess)
sh("git", "config", "core.sshCommand", f"touch {MARK}", cwd=sess)
check("commit found", real.has_commits(sess), True)
real.push(sess, "work/x-1234")
check("branch reached origin", "work/x-1234" in sh("git", "branch", "--list", "work/*", cwd=T + "/origin.git"), True)
check("no hook or config of the session ran", os.path.exists(MARK), False)
orig_run = ws.Git._run; seen = {}
def fake_run(self, *cmd, cwd=None, timeout=120):
    if cmd[0] == "gh": seen.update(cmd=cmd, cwd=cwd); return subprocess.CompletedProcess(cmd, 0, "https://github.com/o/r/pull/7\n", "")
    return orig_run(self, *cmd, cwd=cwd, timeout=timeout)
ws.Git._run = fake_run
sh("git", "remote", "set-url", "origin", "git@github.com:o/r.git", cwd=T + "/proj")
n = real.draft_pr(sess, "work/x-1234", "Work: x", "body")
ws.Git._run = orig_run
check("draft_pr: --repo, base, cwd is the project", ("--repo" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--repo") + 1] == "o/r",
      seen["cmd"][seen["cmd"].index("--base") + 1], seen["cwd"], n), (True, "main", T + "/proj", 7))
shutil.rmtree(T, ignore_errors=True)

print("\n  F5: the runner checks the pushed tree before GitHub sees it")
check("credential names", [ws.credential_name(n) for n in (
    ".netrc", "a/.git-credentials", "x/gh/hosts.yml", "hosts.yml", "k/id_ed25519", "c.pem",
    "tls.key", "credentials.json", "lib/keys.py", "README.md")],
    [True, True, True, False, True, True, True, True, False, False])
T = tempfile.mkdtemp()
sh("git", "init", "-q", "--bare", "-b", "main", T + "/origin.git")
sh("git", "clone", "-q", T + "/origin.git", T + "/proj")
os.makedirs(T + "/proj/.github/workflows")
open(T + "/proj/.github/workflows/ci.yml", "w").write("jobs:\n  a:\n    runs-on: [self-hosted, Linux]\n")
sh("git", "add", ".", cwd=T + "/proj"); sh("git", "commit", "-qm", "one", cwd=T + "/proj")
sh("git", "push", "-q", "origin", "HEAD:main", cwd=T + "/proj")
sh("git", "remote", "set-head", "origin", "main", cwd=T + "/proj")
gp5 = workcfg.Project("p", ("p",), T + "/proj", "true")

class AgentGit(ws.Git):
    """Real git; the 'agent' commits `files` in its clone right after prepare."""
    def __init__(self, files):
        super().__init__(); self.files = files; self.prs = []
    def prepare(self, project, branch, sd):
        super().prepare(project, branch, sd)
        for name, text in self.files.items():
            os.makedirs(os.path.dirname(os.path.join(sd, name)) or sd, exist_ok=True)
            open(os.path.join(sd, name), "w").write(text)
        sh("git", "add", "-A", cwd=sd); sh("git", "commit", "-qm", "agent", cwd=sd)
    async def gate(self, wt, cmd): return True, ""
    def draft_pr(self, sd, b, t, body): self.prs.append(b); return 9

def run_real(files):
    events = []
    async def ask(q): return "x"
    git = AgentGit(files)
    s = ws.Session(gp5, "fix the timeout", emit=events.append, ask=ask, home=T, host="tablet",
                   git=git, client_factory=lambda o: FakeClient([("result",)], s), options_factory=lambda: None)
    asyncio.run(s.run())
    pushed = sh("git", "branch", "--list", "work/*", cwd=T + "/origin.git").strip()
    return events, pushed, git

ev, pushed, git = run_real({".github/workflows/ci.yml": "jobs:\n  a:\n    runs-on: ubuntu-latest\n"})
check("a commit adding a hosted runner: stuck, nothing pushed, no PR",
      (ev[-1]["kind"], ev[-1].get("why"), pushed, git.prs), ("stuck", "the branch adds a hosted CI runner", "", []))
ev, pushed, git = run_real({".github/workflows/new.yaml": "jobs:\n  b:\n    runs-on: self-hosted\n"})
check("a new workflow with a bare self-hosted: stuck",
      (ev[-1]["kind"], ev[-1].get("why"), pushed), ("stuck", "the branch adds a hosted CI runner", ""))
ev, pushed, git = run_real({".netrc": "machine github.com password x\n"})
check("a commit adding .netrc: stuck, nothing pushed",
      (ev[-1]["kind"], ev[-1].get("why"), pushed), ("stuck", "the branch adds a credential file", ""))
ev, pushed, git = run_real({"lib/fix.py": "x = 1\n"})
check("a clean commit: pushed and opened", (ev[-1]["kind"], bool(pushed), len(git.prs)), ("pr_opened", True, 1))
shutil.rmtree(T, ignore_errors=True)


print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
