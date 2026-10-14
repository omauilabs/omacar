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
        self.gates = {}          # session id -> "gate passed" | "gate failing", from its PR event
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
            if e.get("kind") in ("pr_opened", "gate_failed") and e.get("session"):
                self.gates[e["session"]] = "gate passed" if e["kind"] == "pr_opened" else "gate failing"
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
            if i.kind == "stop":
                # "stop" while a question is waiting stops the session that asked;
                # its own `stopped` event is what gets spoken.
                self.pending = None
                s = self.sessions.get(p[1])
                if s is not None:
                    asyncio.run_coroutine_threadsafe(s.stop("stopped"), self.loop)
                return {"ok": True}
            return self.emit({"kind": "didnt_catch"})
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
            text = (f"{i.project}: draft PR {done[-1].pr}, {self.gates.get(done[-1].id, 'gate failing')}."
                    if done else f"{i.project}: no pull request yet.")
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
        rows = list(self.sessions.values())[::-1][:3]      # newest first; ids are random hex
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
