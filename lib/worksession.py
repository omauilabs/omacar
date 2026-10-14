"""One Work task: a worktree, a Claude Code session, the gate, a draft PR.

The agent edits and commits. The runner -- this file -- pushes and opens the
PR, so the agent never needs, and is never given, the right to do either.
Before the push the runner reads the branch's changed files itself
(Git.tree_problem): a workflow that puts a job on a hosted runner, or a file
named like a credential, means no push.
Every tool call passes workgates.judge() from a PreToolUse hook first.

The gate is a command from work.toml that lives in the worktree, which the
agent can edit, so the runner runs it inside bubblewrap: read-only filesystem
but the worktree, the credential places hidden, no network, a scrubbed
environment. A gate that needs the network therefore fails its gate; that is
the owner's call to revisit. Without bwrap the gate does not run (fails closed).
"""
import asyncio
import inspect
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workgates  # noqa: E402

LIB = os.path.dirname(os.path.abspath(__file__))

TIMEOUTS = {"question": 1800, "silent": 1200}
REPAIRS = 2
# Every way to a credential the parent process may hold. Blanked in the
# session's environment so the agent inherits none of them.
STRIP_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "SSH_AUTH_SOCK",
             "DBUS_SESSION_BUS_ADDRESS", "GIT_ASKPASS", "SSH_ASKPASS",
             "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")
SYSTEM_APPEND = """You are working for a driver who talks to you by voice from a car.
Work only inside the current directory. Commit as you go with clear messages.
Before you finish, run the project's gate: `{gate}`. Never push and never open
or edit a pull request: the runner does both. If you are truly blocked, ask one
short question with AskUserQuestion, two or three options; otherwise decide."""


def sandbox_settings(project, home=None):
    """Claude Code's own sandbox (bubblewrap on Linux): writes only in the
    worktree, no Unix sockets, no network but the project's own list, no
    reads of the credential and protected places, and no way for the model
    to step outside it. If it cannot start, nothing runs."""
    home = home or os.path.expanduser("~")
    return {"enabled": True, "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False, "failIfUnavailable": True,
            "network": {"allowedDomains": list(project.network)},
            "filesystem": {"denyRead": workgates.protected_places(home)}}


PRIVATE_RULE = "Read(//**/share/assets/private/**)"


def read_denials(home):
    """Claude Code permission deny rules for reading every protected place
    (workgates.protected_places: SECRET_PATHS, the live checkout, the app's
    worktrees) and any share/assets/private. Passed as disallowed_tools: a
    scoped disallowed rule denies matching calls in every permission mode.
    `//` starts an absolute path in permission rules; each place gets a rule
    for itself (a file) and for everything under it (a directory).

    Claude Code merges Read deny rules, like sandbox.filesystem.denyRead,
    into the sandbox's own configuration, so the OS also refuses those reads
    to Bash commands (code.claude.com/docs/en/sandboxing, "Permission
    rules": "Paths and domains from both sandbox settings and permission
    rules are merged into the final sandbox configuration"). sandbox_settings
    sets denyRead for the same places directly. Checked at rollout step 2:
    `cat ~/.claude/.credentials.json` from inside a session must fail."""
    rules = []
    for p in workgates.protected_places(home):
        rules += [f"Read(/{p})", f"Read(/{p}/**)"]
    return rules + [PRIVATE_RULE]


def session_env(base=None):
    """The session's environment: ours, minus every way to a credential.
    Values are blanked, not removed, so they override the parent's."""
    env = dict(os.environ if base is None else base)
    return {k: ("" if k in STRIP_ENV else v) for k, v in env.items()}


def _sdk():
    """Import claude_agent_sdk. OmaCar's lib/mcp.py would shadow the PyPI `mcp`
    package the SDK needs, so lib/ (and any module registered as `mcp` that is
    OmaCar's) is stood aside for the import, then put back."""
    saved = list(sys.path)
    here = os.path.abspath(LIB)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != here]
    mod = sys.modules.get("mcp")
    popped = sys.modules.pop("mcp") if mod is not None and hasattr(mod, "VEHICLE_SECRET") else None
    try:
        import claude_agent_sdk
        return claude_agent_sdk
    finally:
        sys.path[:] = saved
        if popped is not None and "mcp" not in sys.modules:
            sys.modules["mcp"] = popped


def _real_dir(p):
    return os.path.isdir(p) and not os.path.islink(p)


def gate_argv(cmd, worktree, home, hide, is_real_dir=_real_dir):
    """The full argv that runs the gate in bubblewrap. Pure, so it is tested
    without running anything."""
    # --unshare-all keeps the network off; the tmpfs over /run hides the
    # desktop's sockets (D-Bus, Hyprland, PipeWire, keyring) under /run/user.
    argv = ["bwrap", "--die-with-parent", "--unshare-all", "--cap-drop", "ALL",
            "--new-session", "--ro-bind", "/", "/", "--tmpfs", "/run"]
    # Where /var/run is a symlink to /run (Arch), bwrap cannot mount on it, and
    # the tmpfs over /run already hides what it points at.
    if is_real_dir("/var/run"):
        argv += ["--tmpfs", "/var/run"]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", worktree, worktree]
    for path in hide:
        if os.path.isdir(path):
            argv += ["--tmpfs", path]
        elif os.path.exists(path):
            argv += ["--ro-bind", "/dev/null", path]
    return argv + ["--chdir", worktree, "--"] + shlex.split(cmd)


CREDENTIAL_NAMES = {".git-credentials", ".netrc", "credentials.json",
                    ".credentials.json", "id_rsa", "id_ed25519"}
CREDENTIAL_WHY = "the branch adds a credential file"
SYMLINK_WHY = "the branch has a symlinked CI workflow"


def credential_name(path):
    """A path whose name says it holds a credential; such a file is never pushed."""
    parts = path.split("/")
    base = parts[-1]
    return (base in CREDENTIAL_NAMES or base.endswith((".pem", ".key"))
            or (base == "hosts.yml" and len(parts) > 1 and parts[-2] == "gh"))


def slug(instruction):
    s = re.sub(r"[^a-z0-9]+", "-", instruction.lower()).strip("-")[:40].strip("-")
    return f"{s or 'task'}-{secrets.token_hex(2)}"


class Git:
    """The runner's own git and gh. The agent works in a clone (its commits
    land in the clone's own .git, which the sandbox lets it write). The runner
    never runs git inside that folder -- hooks and config there are the
    agent's -- but fetches the branch into the trusted project repo, hooks off,
    and pushes from there."""

    def __init__(self):
        self.project = None
        self.default = "main"

    def _run(self, *cmd, cwd=None, timeout=120):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=timeout)

    def _git(self, *args, cwd=None):
        r = self._run("git", *args, cwd=cwd)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:300] or f"git {args[0]} failed")
        return r.stdout.strip()

    def prepare(self, project, branch, session_dir):
        self._session_branch = branch
        self.project, path = project, project.path
        self._git("-C", path, "fetch", "origin")
        head = self._run("git", "-C", path, "symbolic-ref", "--short",
                         "refs/remotes/origin/HEAD").stdout.strip() or "origin/main"
        self.default = head.removeprefix("origin/")
        url = self._git("-C", path, "remote", "get-url", "origin")
        os.makedirs(os.path.dirname(session_dir), exist_ok=True)
        self._git("clone", "--no-hardlinks", path, session_dir)
        self._git("-C", session_dir, "remote", "set-url", "origin", url)
        # Start from origin's tip; no local branch of the default's name needed.
        self._git("-C", session_dir, "fetch", "origin", self.default)
        self._git("-C", session_dir, "switch", "-c", branch, "FETCH_HEAD")

    def _import(self, session_dir, branch):
        """Bring the session's branch into the trusted repo. Nothing from the
        session folder runs: hooks off, only refs and objects are read."""
        self._git("-C", self.project.path, "-c", "core.hooksPath=/dev/null",
                  "-c", "protocol.file.allow=always", "fetch", "--no-tags",
                  session_dir, f"+refs/heads/{branch}:refs/heads/{branch}")

    _proc = None

    async def gate(self, worktree, cmd):
        # The owner's own command from work.toml, split, never through a shell,
        # and inside bubblewrap: the agent can edit what it runs.
        if not shutil.which("bwrap"):
            return False, "sandbox unavailable: bwrap missing"
        home = os.path.expanduser("~")
        argv = gate_argv(cmd, worktree, home, workgates.SECRET_PATHS(home))
        self._proc = proc = await asyncio.create_subprocess_exec(
            *argv, cwd=worktree, env=session_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 3600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "gate timed out"
        finally:
            self._proc = None
        tail = "\n".join(out.decode(errors="replace").strip().splitlines()[-30:])
        return proc.returncode == 0, tail

    async def cancel_gate(self):
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()

    def has_commits(self, session_dir, base=None, branch=None):
        branch = branch or self._session_branch
        self._import(session_dir, branch)
        r = self._run("git", "-C", self.project.path, "rev-list", "--count",
                      f"origin/{self.default}..{branch}")
        return r.returncode == 0 and r.stdout.strip() not in ("", "0")

    def tree_problem(self, branch):
        """The last gate before GitHub: what the branch would add that the
        session's own gates might have missed (a shell-written workflow, a
        credential file). Read from the trusted repo's imported refs, hooks
        off; nothing in the session folder runs. "" when clean."""
        path = self.project.path
        base = ["-C", path, "-c", "core.hooksPath=/dev/null"]
        # Every commit's names, not just the tree diff: a credential added and
        # then deleted is still in the pushed history.
        names = self._git(*base, "log", "--format=", "--name-only", "--no-renames", "-z",
                          f"origin/{self.default}..{branch}").split("\0")
        for name in filter(None, names):
            if credential_name(name):
                return CREDENTIAL_WHY
        # A symlinked .github or workflow would hide the real file from the read below.
        for entry in (self._git(*base, "ls-tree", "-z", branch, "--", ".github").split("\0")
                      + self._git(*base, "ls-tree", "-r", "-z", branch, "--",
                                  ".github/workflows").split("\0")):
            if entry.startswith("120000 "):
                return SYMLINK_WHY
        for name in sorted(set(filter(None, names))):
            if re.search(r"(^|/)\.github/workflows/.+\.ya?ml$", name, re.I):
                r = self._run("git", *base, "show", f"{branch}:{name}")
                if r.returncode != 0:
                    continue          # deleted again by the tip: nothing there to run
                why = workgates.hosted_runner_problem(r.stdout)
                if why:
                    return why
        return ""

    def push(self, session_dir, branch):
        # Pushes what has_commits imported; nothing is fetched here, so a stop
        # that lands between the two is never followed by a push.
        self._git("-C", self.project.path, "-c", "core.hooksPath=/dev/null", "push",
                  "origin", f"refs/heads/{branch}:refs/heads/{branch}")

    def _pr_argv(self, branch, title, body):
        url = self._git("-C", self.project.path, "remote", "get-url", "origin")
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
        if not m:
            raise RuntimeError("origin is not a GitHub remote")
        return ["gh", "pr", "create", "--draft", "--repo", m.group(1), "--head", branch,
                "--base", self.default, "--title", title, "--body", body]

    def draft_pr(self, session_dir, branch, title, body):
        r = self._run(*self._pr_argv(branch, title, body), cwd=self.project.path)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:300])
        m = re.search(r"/pull/(\d+)", r.stdout)
        if not m:
            # Pushed but no PR we can name: a failure, said as one (Session
            # reports "pushed, PR not opened"), never a PR 0.
            raise RuntimeError("gh printed no pull request")
        return int(m.group(1))


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
        self.problem = None          # "limit" | "sandbox" | "failed", from a result
        self.tool_calls = 0          # tool calls that reached the hook
        self.last_message = time.monotonic()
        self._stopping = False       # set by stop(); run() checks it everywhere
        self._pushed = False
        self._ask_task = self._reader = None
        self._done = False           # run() has returned; stop() is then a no-op
        self.ctx = workgates.Ctx(worktree=self.worktree, host=host, home=home)

    def _set_state(self, state):
        if not self._stopping:
            self.state = state

    def _emit(self, kind, **kw):
        self.emit_raw(dict(kind=kind, session=self.id, project=self.project.name, **kw))

    def emit(self, kind, **kw):
        # Once stop() has spoken its one terminal event, nothing else is said.
        if not self._stopping:
            self._emit(kind, **kw)

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
        stopped = lambda: deny(message="Stopped by the driver.", interrupt=True)  # noqa: E731
        if self._stopping:
            return stopped()
        qs = inp.get("questions") or [{}]
        qtext = str(qs[0].get("question", "")).strip()
        prev, self.state = self.state, "question"
        self.emit("question", question=qtext)
        self._ask_task = asyncio.ensure_future(self.ask(qtext))
        try:
            answer = await asyncio.wait_for(self._ask_task, self.timeouts["question"])
        except asyncio.TimeoutError:
            answer = None
        except asyncio.CancelledError:
            if self._stopping:
                return stopped()
            raise
        if self._stopping:
            return stopped()
        self.state, self.last_message = prev, time.monotonic()
        if not answer:
            self.unanswered.append(qtext)
            return deny(message="No answer from the driver. Stop here and summarise.", interrupt=True)
        return allow(updated_input={**inp, "answers": {qtext: answer}})

    def _results(self):
        try:
            sdk = _sdk()
            return sdk.PermissionResultAllow, sdk.PermissionResultDeny
        except ImportError:                       # the tests' world: plain records
            return (lambda **kw: dict(behavior="allow", **kw),
                    lambda **kw: dict(behavior="deny", **kw))

    # ---- the SDK itself -----------------------------------------------------
    def _options(self):
        sdk = _sdk()
        return sdk.ClaudeAgentOptions(
            cwd=self.worktree, model="sonnet", permission_mode="default",
            setting_sources=["user", "project"], can_use_tool=self._can_use,
            hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[self._pre])]},
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": SYSTEM_APPEND.format(gate=self.project.gate)},
            sandbox=sandbox_settings(self.project, self.home), env=session_env(),
            # Belt and braces with judge(): reads of protected places, and the
            # web tools (F4a), are denied by Claude Code itself too.
            disallowed_tools=read_denials(self.home) + sorted(workgates.WEB_TOOLS))

    def _sdk_client(self, options):
        return _sdk().ClaudeSDKClient(options=options)

    def _note_result(self, msg):
        self.sdk_session = getattr(msg, "session_id", None)
        status = getattr(msg, "api_error_status", None)
        if getattr(msg, "is_error", False) and status == 429:
            self.problem = "limit"
        elif getattr(msg, "subtype", None) == "error_during_execution":
            # failIfUnavailable: a sandbox that cannot start surfaces as an
            # error result before the model has run a single tool.
            self.problem = "sandbox" if status is None and self.tool_calls == 0 else "failed"

    async def _drain(self):
        """Read one response in its own task, so a quiet spell never cancels
        the stream. False when stuck (or stopped: check _stopping first)."""
        q = asyncio.Queue()

        async def read():
            try:
                async for msg in self.client.receive_response():
                    q.put_nowait(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:                            # noqa: BLE001
                q.put_nowait(e)
            finally:
                q.put_nowait(None)

        self._reader = task = asyncio.ensure_future(read())
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), self.timeouts["silent"])
                except asyncio.TimeoutError:
                    quiet = time.monotonic() - self.last_message
                    if self.state == "question" or quiet < self.timeouts["silent"]:
                        continue
                    return False
                if self._stopping:
                    return False
                if msg is None:
                    return True
                if isinstance(msg, Exception):
                    raise msg
                self.last_message = time.monotonic()
                if type(msg).__name__ in ("ResultMessage", "Result"):
                    self._note_result(msg)
        finally:
            task.cancel()

    async def _after_drain(self):
        """True when run() must end here: stopped, stuck, limited or failed."""
        ok = await self._drain()
        if self._stopping:
            return True
        if not ok:
            await self.stop("stuck")
            return True
        if self.problem:
            self._set_state("stopped" if self.problem == "limit" else "stuck")
            if self.problem == "limit":
                self.emit("limit", until="the limit resets")
            else:
                self.emit("stuck", why="sandbox unavailable" if self.problem == "sandbox"
                          else "the session failed")
            return True
        return False

    async def _gate(self):
        r = self.git.gate(self.worktree, self.project.gate)
        return await r if inspect.isawaitable(r) else r

    async def run(self):
        try:
            await self._guarded()
        finally:
            self._done = True

    async def _guarded(self):
        try:
            await self._run()
        except Exception as e:                                # noqa: BLE001
            if self._stopping:
                return
            self.state = "failed"
            why = "pushed, PR not opened" if self._pushed and self.pr is None else str(e)[:200]
            self.emit("stuck", why=why or type(e).__name__)

    async def _run(self):
        await asyncio.to_thread(self.git.prepare, self.project, self.branch, self.worktree)
        if self._stopping:
            return
        self.emit("started")
        self._set_state("running")
        self.client = self.client_factory(self.options_factory())
        async with self.client:
            if self._stopping:
                return
            await self.client.query(self.instruction)
            if await self._after_drain():
                return
            ok, tail = await self._gate()
            if self._stopping:
                return
            tries = 0
            while not ok and tries < REPAIRS:
                tries += 1
                await self.client.query(
                    f"The gate failed. Last lines:\n{tail}\nFix it, commit, and run it again.")
                if await self._after_drain():
                    return
                ok, tail = await self._gate()
                if self._stopping:
                    return
        await self._finish(ok)

    async def _finish(self, gate_ok):
        if self._stopping:
            return
        if not await asyncio.to_thread(self.git.has_commits, self.worktree):
            if not self._stopping:
                self._set_state("done")
                self.emit("finished")
            return
        self._set_state("pushing")
        body = "\n".join([
            f"Asked by voice from the car: \"{self.instruction}\"", "",
            f"Gate `{self.project.gate}`: {'passed' if gate_ok else 'still failing'}.",
            *([""] + [f"Unanswered question: {q}" for q in self.unanswered] if self.unanswered else []),
            "", "Opened by voice from the car; review when parked."])
        if self._stopping:
            return
        problem = await asyncio.to_thread(self.git.tree_problem, self.branch)
        if problem:
            # Nothing is pushed: the branch stays in the session folder and the
            # trusted repo for the owner to look at when parked.
            if not self._stopping:
                self._set_state("stuck")
                self.emit("stuck", why=problem)
            return
        if self._stopping:
            return
        await asyncio.to_thread(self.git.push, self.worktree, self.branch)
        self._pushed = True
        if self._stopping:
            return
        self.pr = await asyncio.to_thread(self.git.draft_pr, self.worktree, self.branch,
                                          f"Work: {self.instruction}", body)
        self._set_state("done")
        self.emit("pr_opened" if gate_ok else "gate_failed", pr=self.pr)

    async def stop(self, reason="stopped"):
        """Idempotent: one terminal event, and run() ends without pushing."""
        if self._stopping or self._done:
            return
        self._stopping = True
        self.state = reason if reason in ("stuck", "interrupted") else "stopped"
        self._emit(self.state)
        if self._ask_task is not None:
            self._ask_task.cancel()
        cancel = getattr(self.git, "cancel_gate", None)
        if cancel is not None:
            try:
                await cancel()
            except Exception:                                 # noqa: BLE001
                pass
        if self.client is not None:
            for call in (self.client.interrupt, self.client.disconnect):
                try:
                    await call()
                except Exception:                             # noqa: BLE001
                    pass
        if self._reader is not None:
            self._reader.cancel()
