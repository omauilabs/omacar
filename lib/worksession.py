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
# Every way to a credential the parent process may hold. Blanked in the
# session's environment so the agent inherits none of them.
STRIP_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "SSH_AUTH_SOCK",
             "DBUS_SESSION_BUS_ADDRESS", "GIT_ASKPASS", "SSH_ASKPASS")
SYSTEM_APPEND = """You are working for a driver who talks to you by voice from a car.
Work only inside the current directory. Commit as you go with clear messages.
Before you finish, run the project's gate: `{gate}`. Never push and never open
or edit a pull request: the runner does both. If you are truly blocked, ask one
short question with AskUserQuestion, two or three options; otherwise decide."""


def sandbox_settings(project):
    """Claude Code's own sandbox (bubblewrap on Linux): writes only in the
    worktree, no Unix sockets, no network but the project's own list, and no
    way for the model to step outside it. If it cannot start, nothing runs."""
    return {"enabled": True, "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False, "failIfUnavailable": True,
            "network": {"allowedDomains": list(project.network)}}


def session_env(base=None):
    """The session's environment: ours, minus every way to a credential.
    Values are blanked, not removed, so they override the parent's."""
    env = dict(os.environ if base is None else base)
    return {k: ("" if k in STRIP_ENV else v) for k, v in env.items()}


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
        self.tool_calls = 0          # tool calls that reached the hook
        self.sandbox_failed = False  # error result before any tool ran
        self.last_message = time.monotonic()
        self.ctx = workgates.Ctx(worktree=self.worktree, host=host, home=home)

    def emit(self, kind, **kw):
        self.emit_raw(dict(kind=kind, session=self.id, project=self.project.name, **kw))

    # ---- the SDK's two callbacks ------------------------------------------
    async def _pre(self, inp, tool_use_id, context):
        self.tool_calls += 1
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
                           "append": SYSTEM_APPEND.format(gate=self.project.gate)},
            sandbox=sandbox_settings(self.project), env=session_env())

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
                # failIfUnavailable: a sandbox that cannot start surfaces as an
                # error result before the model has run a single tool.
                if (getattr(msg, "subtype", None) == "error_during_execution"
                        and self.tool_calls == 0):
                    self.sandbox_failed = True
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
            if self.sandbox_failed:
                return await self._unavailable()
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
                if self.sandbox_failed:
                    return await self._unavailable()
                ok, tail = self.git.gate(self.worktree, self.project.gate)
        await self._finish(ok)

    async def _unavailable(self):
        self.state = "stuck"
        self.emit("stuck", why="sandbox unavailable")

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
            try:
                await self.client.disconnect()
            except Exception:                                 # noqa: BLE001
                pass
        self.state = reason if reason in ("stuck", "interrupted") else "stopped"
        self.emit(self.state)
