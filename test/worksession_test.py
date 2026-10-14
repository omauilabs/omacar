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

print("\n  the sandbox is the wall")
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
check("a failure after a tool ran is not blamed on the sandbox", ev[-1].get("why") != "sandbox unavailable", True)

print("\n  silence is stuck")
s, ev, git = run([("silence", 0.2), ("result",)], [True], timeouts={"question": 5, "silent": 0.05})
check("stuck", ev[-1]["kind"], "stuck")
check("nothing pushed", git.pushed, [])

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)