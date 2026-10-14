#!/usr/bin/env python3
"""The relay, end to end in one process: fake ears, fake model, fake sessions,
a fake park and a fake clock. Real HTTP for the identity and idempotency checks."""
import asyncio, base64, http.client, json, os, sys, tempfile, threading, time
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

def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond(): return True
        time.sleep(0.01)
    return bool(cond())

P = workcfg.Project
CFG = workcfg.Config(host="tablet", daily_cap=2, projects={
    "omacar": P("omacar", ("omacar",), "/p", "t")})
HEARD = {}
PROMPTS = []
def ears(path): return HEARD[os.path.basename(path).split(".")[0]], -0.2
def model(prompt):
    PROMPTS.append(prompt)
    if "fix" in prompt: return {"kind": "new_task", "project": "omacar", "instruction": "fix it"}
    if "Sentence: zzres" in prompt: return {"kind": "result", "project": "omacar"}
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
wait_for(lambda: "started" in kinds())
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
wait_for(lambda: FakeSession.made[0].state == "stopped")
check("running session stopped when the car moves", FakeSession.made[0].state, "stopped")
park.parked = True

print("\n  daily cap")
say("c7", "in omacar fix it"); say("c8", "yes"); wait_for(lambda: len(FakeSession.made) == 2)
wait_for(lambda: kinds().count("started") == 2)
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

print("\n  stop while a question is pending stops the session that asked")
R3 = workrelay.Relay(CFG, ears=ears, intent=intent, session_factory=FakeSession, park=park,
                     clock=lambda: now[0], state_dir=os.path.join(SCRATCH, "relay3"),
                     render=lambda t, o: False)
HEARD["q1"] = "in omacar fix it"; HEARD["q2"] = "yes"; HEARD["q3"] = "stop"
for cid in ("q1", "q2"):
    p = os.path.join(SCRATCH, cid + ".webm"); open(p, "wb").write(b"x"); R3.utterance(cid, p, [])
wait_for(lambda: R3.sessions)
asker = FakeSession.made[-1]
asyncio.run_coroutine_threadsafe(asker.ask("which one?"), R3.loop); wait_for(lambda: R3.questions)
check("question is queued", (len(R3.questions), R3.pending), (1, None))
p = os.path.join(SCRATCH, "q3.webm"); open(p, "wb").write(b"x"); R3.utterance("q3", p, [])
wait_for(lambda: asker.state == "stopped")
check("asking session stopped, pending cleared", (asker.state, R3.pending, len(R3.questions)), ("stopped", None, 0))

# ---- fix round 1 ---------------------------------------------------------------
class EndingSession(FakeSession):
    """Like FakeSession, but stop() really ends run(), so the relay's done-callback fires."""
    async def run(self):
        self.done = asyncio.Event()
        self.emit({"kind": "started", "session": self.id, "project": "omacar"})
        await self.done.wait()
    async def stop(self, reason="stopped"):
        self.state = reason
        self.done.set()

def mk(name, cfg=CFG, cls=EndingSession, park_=None, clk=None, render=lambda t, o: False, **kw):
    clk = clk if clk is not None else [1000.0]
    r = workrelay.Relay(cfg, ears=ears, intent=intent, session_factory=cls,
                        park=park_ or FakePark(), clock=lambda: clk[0],
                        state_dir=os.path.join(SCRATCH, name), render=render, **kw)
    r.clk = clk
    return r

_n = [0]
def tell(r, text, secrets=()):
    _n[0] += 1; cid = f"t{_n[0]}"
    HEARD[cid] = text
    p = os.path.join(SCRATCH, cid + ".webm"); open(p, "wb").write(b"x")
    return r.utterance(cid, p, list(secrets))

def start_one(r):
    n = len(r.sessions)
    tell(r, "in omacar fix it"); tell(r, "yes")
    wait_for(lambda: len(r.sessions) > n)
    wait_for(lambda: sum(e["kind"] == "started" for e in r.events(0)) > n)

BIG = workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects)

print("\n  C1: the car leaves Park with a task queued: nothing starts, the queue is dropped")
pk = FakePark(); RC = mk("rc", BIG, park_=pk)
start_one(RC); start_one(RC)
tell(RC, "in omacar fix it"); tell(RC, "yes")
check("third task is queued", (len(RC.sessions), len(RC.queue), RC.events(0)[-1]["kind"]), (2, 1, "busy"))
made = len(FakeSession.made)
pk.parked = False; RC.tick()
wait_for(lambda: all(s.state == "stopped" for s in RC.sessions.values()))
time.sleep(0.3)
check("both stopped, queue empty, no third session",
      (sorted(s.state for s in RC.sessions.values()), RC.queue, len(FakeSession.made) - made),
      (["stopped", "stopped"], [], 0))
check("the dropped task was spoken", [e["kind"] for e in RC.events(0)].count("not_started"), 1)
check("no parked_only noise from the dying sessions", "parked_only" in [e["kind"] for e in RC.events(0)], False)

print("\n  C1: the cap counts running and queued work")
RK = mk("rk", workcfg.Config(host="box", daily_cap=2, projects=CFG.projects))
start_one(RK); start_one(RK)
tell(RK, "in omacar fix it")
check("two running, a third confirmed task is refused, not queued",
      (RK.events(0)[-1]["kind"], RK.queue, RK.pending), ("cap", [], None))
RQ = mk("rq", workcfg.Config(host="box", daily_cap=3, projects=CFG.projects))
start_one(RQ); start_one(RQ); tell(RQ, "in omacar fix it"); tell(RQ, "yes")
tell(RQ, "in omacar fix it")
check("a queued task counts toward the cap", (len(RQ.queue), RQ.events(0)[-1]["kind"]), (1, "cap"))

print("\n  I1: a yes is re-checked")
pk = FakePark(); RY = mk("ry", BIG, park_=pk)
tell(RY, "in omacar fix it"); pk.parked = False; RY.tick()
check("car leaves Park with a confirm pending", (RY.events(0)[-1]["kind"], RY.pending), ("not_started", None))
pk.parked = True
tell(RY, "in omacar fix it"); RY.clk[0] += 20.5
n = len(FakeSession.made); tell(RY, "yes")
check("a yes after the 20 s window starts nothing",
      (len(FakeSession.made) - n, RY.events(0)[-1]["kind"]), (0, "not_started"))
tell(RY, "in omacar fix it"); pk.parked = False
n = len(FakeSession.made); tell(RY, "yes")
check("a yes while moving, before any tick, starts nothing",
      (len(FakeSession.made) - n, RY.events(0)[-1]["kind"]), (0, "parked_only"))

print("\n  I2: questions queue in order and never overwrite a confirm")
RI = mk("ri", BIG)
start_one(RI); start_one(RI)
sa, sb = list(RI.sessions.values())
tell(RI, "in omacar fix it")                                   # confirm pending
fa = asyncio.run_coroutine_threadsafe(sa.ask("a?"), RI.loop); wait_for(lambda: len(RI.questions) == 1)
check("a question does not replace the confirm", RI.pending is not None and RI.pending[0], "confirm")
n = len(RI.sessions); tell(RI, "yes")
check("the yes reached the confirm (queued: two already running); the question still waits",
      (RI.events(0)[-1]["kind"], len(RI.questions)), ("busy", 1))
fb = asyncio.run_coroutine_threadsafe(sb.ask("b?"), RI.loop); wait_for(lambda: len(RI.questions) == 2)
check("F3: status names the oldest waiting question's session", RI.status()["asking"], sa.id)
tell(RI, "blue")
check("F3: after it is answered, the next one", RI.status()["asking"], sb.id)
tell(RI, "green")
check("F3: none waiting is null", RI.status()["asking"], None)
check("first answer to the first asker, second to the second",
      (fa.result(3), fb.result(3)), ("blue", "green"))

print("\n  I3: a question whose session is gone is not an answer target")
fc = asyncio.run_coroutine_threadsafe(sa.ask("c?"), RI.loop); wait_for(lambda: len(RI.questions) == 1)
fc.cancel(); wait_for(lambda: not RI.questions)
RI.queue.clear()
tell(RI, "in omacar fix it")
check("next utterance is a fresh intent", (RI.events(0)[-1]["kind"], RI.pending is not None), ("confirm", True))
RI.pending = None

print("\n  I4: a torn log line does not disable the relay")
RT = mk("rt", BIG)
RT.emit({"kind": "started", "session": "x1", "project": "omacar"})
with open(RT.log, "a") as f: f.write('{"id": 99, "ki')
RT.emit({"kind": "started", "session": "x2", "project": "omacar"})
ev = RT.events(0)
check("valid rows survive on both sides of the tear", [e.get("session") for e in ev], ["x1", "x2"])
check("cap still counts", RT._today(), 2)
RT2 = mk("rt", BIG)
check("ids continue after the max valid id", RT2.next_id, max(e["id"] for e in ev) + 1)

print("\n  I5: slow work does not hold the lock; a duplicate waits for the first")
calls = []; gate = threading.Event()
def slow_ears(path):
    calls.append(path); gate.wait(5); return "in omacar fix it", -0.2
slow = workrelay.Relay(BIG, ears=slow_ears, intent=intent, session_factory=EndingSession, park=FakePark(),
                       clock=lambda: 1000.0, state_dir=os.path.join(SCRATCH, "slow"), render=lambda t, o: False)
out = []
def go(): 
    p = os.path.join(SCRATCH, "dup.webm"); open(p, "wb").write(b"x"); out.append(slow.utterance("dup1", p, []))
t1 = threading.Thread(target=go); t1.start(); wait_for(lambda: calls)
t2 = threading.Thread(target=go); t2.start()
st = []; ts = threading.Thread(target=lambda: st.append(slow.status())); ts.start(); ts.join(1)
check("status answers while transcription is running", bool(st), True)
gate.set(); t1.join(5); t2.join(5)
check("one transcription, two identical replies", (len(calls), len(out), out[0] == out[1]), (1, 2, True))
rgate = threading.Event()
RR = mk("rr", BIG, render=lambda t, o: (rgate.wait(5), False)[1])
RR.cfg = workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0))
te = threading.Thread(target=lambda: RR.emit({"kind": "didnt_catch"})); te.start(); time.sleep(0.1)
st = []; ts = threading.Thread(target=lambda: st.append(RR.status())); ts.start(); ts.join(1)
check("status answers while Piper is rendering", bool(st), True)
rgate.set(); te.join(5)

print("\n  I6/I8: status reads the cached Park; rows carry session/project and a line")
class CountPark(FakePark):
    n = 0
    def update(self): CountPark.n += 1; return self.parked
RS = mk("rs", BIG, park_=CountPark())
start_one(RS); RS.status(); RS.status()
check("status never calls update()", CountPark.n, 0)
RS.tick()
check("tick does", CountPark.n, 1)
wait_for(lambda: RS.status()["sessions"][0]["line"])
check("status rows carry the last spoken line", RS.status()["sessions"][0]["line"], "omacar session started.")
check("every event has session and project", all("session" in e and "project" in e for e in RS.events(0)), True)
before = RS.last_prune; RS.clk[0] += 86400 + 1; RS.tick()
check("prune runs once a day from the ticker", RS.last_prune > before, True)

print("\n  M3/M6/M7")
workrelay.SEEN_MAX = 3
RM = mk("rm", BIG)
for k in range(5): tell(RM, "um")
check("seen is bounded", len(RM.seen), 3)
workrelay.SEEN_MAX = 500
RP = mk("rp", BIG); start_one(RP)
sp = list(RP.sessions.values())[0]; sp.pr = 7
tell(RP, "zzres")
check("result without a gate record claims no gate", RP.events(0)[-1]["line"], "omacar: draft PR 7.")
RP.emit({"kind": "gate_failed", "session": sp.id, "project": "omacar", "pr": 7})
tell(RP, "zzres")
check("result with a gate record", RP.events(0)[-1]["line"], "omacar: draft PR 7, gate failing.")

print("\n  G1/I7: the HTTP face")
def wav(t, o):
    open(o, "wb").write(b"RIFF"); return True
CT = workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0))
RH = mk("rh", CT, render=wav)
srv = workrelay.make_server(CT, 0, RH); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
TOK = RH.token
check("token file is 0600 and readable by the client",
      (oct(os.stat(workrelay.token_path(RH.dir)).st_mode & 0o777), workrelay.read_token(RH.dir) == TOK),
      ("0o600", True))

def call(method, path, body=None, headers=None, port=None, raw=None, auth=True, ctype=True):
    h = {"Host": f"127.0.0.1:{PORT}"}
    if auth: h["Authorization"] = "Bearer " + TOK
    if ctype and method == "POST": h["Content-Type"] = "application/json"
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", port or PORT, timeout=5)
    c.request(method, path, body=raw if raw is not None else (json.dumps(body).encode() if body is not None else None), headers=h)
    r = c.getresponse(); data = r.read(); c.close()
    try: return r.status, json.loads(data)
    except ValueError: return r.status, data

b64 = base64.b64encode(b"audio").decode()
def utt(cid, **kw):
    HEARD[cid] = kw.pop("said", "um")
    return call("POST", "/utterance", {"client_id": cid, "audio_b64": b64, "mime": "audio/webm", **kw})

check("health with the token", call("GET", "/health")[0], 200)
check("no token", call("GET", "/health", auth=False)[0], 403)
check("wrong token", call("GET", "/health", headers={"Authorization": "Bearer nope"})[0], 403)
check("a web page (Origin header) is refused even with the token",
      call("GET", "/health", headers={"Origin": "http://evil.example"})[0], 403)
check("a rebinding Host is refused on the tablet", call("GET", "/health", headers={"Host": "evil.example"})[0], 403)
check("localhost Host is fine", call("GET", "/health", headers={"Host": f"localhost:{PORT}"})[0], 200)
check("POST without the token", call("POST", "/utterance", {"client_id": "a"}, auth=False)[0], 403)
check("POST must be JSON", call("POST", "/utterance", raw=b"x=1", ctype=False)[0], 415)
check("bad client id", utt("bad id!")[0], 400)
check("non-string client id", call("POST", "/utterance", {"client_id": 5, "audio_b64": b64})[0], 400)
check("bad base64", utt("c-b64", audio_b64="%%%")[0], 400)
check("audio not a string", utt("c-nostr", audio_b64=7)[0], 400)
check("missing audio", call("POST", "/utterance", {"client_id": "c-none"})[0], 400)
check("non-numeric Content-Length", call("POST", "/utterance", raw=b"{}", headers={"Content-Length": "abc"})[0], 400)
check("negative Content-Length", call("POST", "/utterance", raw=b"{}", headers={"Content-Length": "-5"})[0], 400)
check("oversize body", call("POST", "/utterance", headers={"Content-Length": str(workrelay.MAX_BODY + 1)})[0], 413)
check("non-numeric after=", call("GET", "/events?after=abc")[0], 400)
check("clip path traversal, encoded", call("GET", "/clip/..%2Fevents.jsonl")[0], 404)
check("clip path traversal, dotted", call("GET", "/clip/../x.wav")[0], 404)
check("no such clip", call("GET", "/clip/999.wav")[0], 404)
code, ack = utt("c-ok", said="in omacar fix it", secrets="8ABC123")
check("a good utterance is acknowledged at once (E2)", (code, ack), (202, {"accepted": True}))
wait_for(lambda: any(e["kind"] == "confirm" for e in RH.events(0)))
reply = [e for e in RH.events(0) if e["kind"] == "confirm"][-1]
check("then processed: the confirm event", reply["kind"], "confirm")
check("secrets that are not a list are ignored, never split into letters",
      any(e.get("text") == "in omacar fix it" for e in RH.events(0)), True)
n = len(RH.events(0)); code2, ack2 = utt("c-ok", said="in omacar fix it")
time.sleep(0.2)
check("same client id over HTTP is one utterance, the same 202", ((code2, ack2), len(RH.events(0))), ((202, ack), n))
check("uploads are deleted", os.listdir(os.path.join(RH.dir, "in")), [])
cname = reply["clip"]
check("the clip is served", call("GET", "/clip/" + cname)[0] if cname else None, 200)
check("events over HTTP", len(call("GET", "/events?after=0")[1]["events"]) >= 2, True)
check("tablet refuses a non-loopback peer", (workrelay.peer_ok(CT, "10.0.0.5"), workrelay.peer_ok(CT, "127.0.0.1")), (False, True))

CB = workcfg.Config(host="box", daily_cap=6, projects=CFG.projects, quiet=(0, 0), tablet_node="omacar-1")
RB = mk("rb", CB, park_=None)
RB.park = None
srvb = workrelay.make_server(CB, 0, RB, host="127.0.0.1"); PORTB = srvb.server_address[1]
threading.Thread(target=srvb.serve_forever, daemon=True).start()
TOK_T, TOK = TOK, RB.token
real = workrelay._node_of
workrelay._nodes.clear(); workrelay._node_of = lambda ip: "omacar-1"
check("box answers the tablet's node", call("GET", "/health", port=PORTB, headers={"Host": "anything"})[0], 200)
workrelay._nodes.clear(); workrelay._node_of = lambda ip: "someone-else"
check("box refuses any other node", call("GET", "/health", port=PORTB, headers={"Host": "anything"})[0], 403)
workrelay._node_of = real; TOK = TOK_T
try:
    workrelay.serve(workcfg.Config(host="box", tablet_node=""))
    refused = False
except SystemExit:
    refused = True
check("box with no tablet_node refuses to start", refused, True)

print("\n  round 2: the token survives a restart; _next never renders on the loop; handler timeout")
R_a = mk("tok"); R_b = mk("tok")
check("two relays on one state dir share a token", R_a.token == R_b.token and bool(R_a.token), True)
os.chmod(workrelay.token_path(R_a.dir), 0o644)
R_c = mk("tok")
check("a looser file keeps its token and is fixed to 0600",
      (R_c.token == R_a.token, oct(os.stat(workrelay.token_path(R_a.dir)).st_mode & 0o777)), (True, "0o600"))
os.makedirs(os.path.join(SCRATCH, "tok2"), exist_ok=True)
with open(workrelay.token_path(os.path.join(SCRATCH, "tok2")), "w") as f: f.write("preset-token")
check("a pre-set token is used", mk("tok2").token, "preset-token")
with open(workrelay.token_path(os.path.join(SCRATCH, "tok2")), "w") as f: f.write("")
check("an empty file gets a new token", len(mk("tok2").token) > 20, True)

renders = []
def spy(text, out): renders.append((threading.get_ident(), text)); return False
# On the box (the tablet renders nothing while moving, F6): the queued task
# meets the daily cap when it leaves the queue.
QB = workcfg.Config(host="box", daily_cap=3, projects=CFG.projects, quiet=(0, 0))
RN = mk("rn", QB, render=spy); RN.park = None
loop_ident = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, threading.get_ident()), RN.loop).result(3)
start_one(RN); start_one(RN)
tell(RN, "in omacar fix it"); tell(RN, "yes")
check("third is queued", len(RN.queue), 1)
del renders[:]
RN.created["other"] = RN.clk[0]        # a third session today: the queued one now meets the cap
first = list(RN.sessions.values())[0]
asyncio.run_coroutine_threadsafe(first.stop(), RN.loop)
wait_for(lambda: "cap" in [e["kind"] for e in RN.events(0)])
refusal = "That's today's limit of sessions."
spoken = [r for r in renders if r[1] == refusal]
check("a refusal from _next is spoken, but not rendered on the loop thread",
      (bool(spoken), any(r[0] == loop_ident for r in renders)), (True, False))
check("handler has a socket timeout", srv.RequestHandlerClass.timeout, 15)

print("\n  F9: lines and free text are scrubbed by shape before logging and speaking")
said9 = []
R9 = mk("f9", workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0)),
        render=lambda t, o: said9.append(t) or False)
R9.emit({"kind": "question", "session": "x", "project": "omacar",
         "question": "Is 1HGCM82633A004352 the right VIN?"})
raw9 = open(R9.log).read()
check("VIN not in the log", "1HGCM82633A004352" in raw9, False)
check("rendered, without the VIN", (len(said9), any("1HGCM82633A004352" in t for t in said9)), (1, False))
check("the line says withheld", "withheld" in R9.events(0)[-1]["line"], True)

print("\n  F8: the confirm window is judged by when the yes arrived")
def slow_yes(r, delay):
    """ears that take `delay` seconds of relay time, ticking meanwhile."""
    def ears8(path):
        text = HEARD[os.path.basename(path).split(".")[0]]
        if text == "yes":
            for _ in range(int(delay)):
                r.clk[0] += 1; r.tick()
        return text, -0.2
    return ears8
R8 = mk("f8", BIG); R8.ears = slow_yes(R8, 5)
tell(R8, "in omacar fix it")
check("the window starts when the clip exists", R8.pending[2], R8.clk[0] + workrelay.CONFIRM_S)
R8.clk[0] += 19; n = len(FakeSession.made)
tell(R8, "yes")
wait_for(lambda: len(FakeSession.made) > n)
check("a yes at 19 s that takes 5 s to transcribe starts the task",
      (len(FakeSession.made) - n, "not_started" in [e["kind"] for e in R8.events(0)]), (1, False))
R8b = mk("f8b", BIG); R8b.ears = slow_yes(R8b, 5)
tell(R8b, "in omacar fix it"); R8b.clk[0] += 21; n = len(FakeSession.made)
tell(R8b, "yes")
check("a yes arriving at 21 s starts nothing; the driver hears Not started",
      (len(FakeSession.made) - n, [e["kind"] for e in R8b.events(0)].count("not_started")), (0, 1))
slow_render = []
R8c = mk("f8c", workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0)))
def render8(t, o, r=R8c):
    r.clk[0] += 10; slow_render.append(t); return False         # Piper takes 10 s
R8c.render = render8
tell(R8c, "in omacar fix it")
check("a 10 s render does not eat the window", R8c.pending[2], R8c.clk[0] + workrelay.CONFIRM_S)

print("\n  F6: on the tablet, whisper and Piper run only while parked")
heard6, said6 = [], []
def ears6(path): heard6.append(path); return "um", -0.2
pk6 = FakePark()
R6 = mk("f6", workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0)),
        park_=pk6, render=lambda t, o: said6.append(t) or False)
R6.ears = ears6
srv6 = workrelay.make_server(R6.cfg, 0, R6); P6 = srv6.server_address[1]
threading.Thread(target=srv6.serve_forever, daemon=True).start()
TOK_SAVE, TOK = TOK, R6.token
pk6.parked = False
code6, body6 = call("POST", "/utterance", {"client_id": "m1", "audio_b64": b64, "mime": "audio/webm"},
                    port=P6, headers={"Host": f"127.0.0.1:{P6}"})
check("moving: 409 parked_only, ears never called, nothing written",
      (code6, body6, heard6, os.listdir(os.path.join(R6.dir, "in"))), (409, {"error": "parked_only"}, [], []))
R6.emit({"kind": "started", "session": "x", "project": "omacar"})
check("moving: an event is logged with its line, no clip, no render",
      (R6.events(0)[-1]["line"], R6.events(0)[-1]["clip"], said6), ("omacar session started.", None, []))
pk6.parked = True
code6, _ = call("POST", "/utterance", {"client_id": "m1", "audio_b64": b64, "mime": "audio/webm"},
                port=P6, headers={"Host": f"127.0.0.1:{P6}"})
wait_for(lambda: heard6)
check("parked: the same clip is taken and transcribed", (code6, len(heard6)), (202, 1))
TOK = TOK_SAVE
srv6.shutdown()

print("\n  F7: the box always renders; quiet hours are the page's, from /health")
said7 = []
R7 = mk("f7", workcfg.Config(host="box", daily_cap=6, projects=CFG.projects, quiet=(21, 7)),
        render=lambda t, o: said7.append(t) or False)
R7.park = None
real_lt = workrelay.time.localtime
workrelay.time.localtime = lambda t=None: time.struct_time((2026, 10, 13, 23, 0, 0, 1, 286, -1))
try:
    R7.emit({"kind": "started", "session": "x", "project": "omacar"})
    check("box relay at 23:00 renders", said7, ["omacar session started."])
    pk7 = FakePark()
    T7 = mk("f7t", workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(21, 7)),
            park_=pk7, render=lambda t, o: said7.append("tablet") or False)
    T7.emit({"kind": "started", "session": "x", "project": "omacar"})
    check("tablet relay at 23:00, parked: keeps its own quiet rule", said7.count("tablet"), 0)
finally:
    workrelay.time.localtime = real_lt
check("health carries the quiet hours", R7.status()["quiet"], [21, 7])

print("\n  N1: a bare stop")
TWO = workcfg.Config(host="box", daily_cap=9, projects={
    "omacar": P("omacar", ("omacar",), "/p", "t"), "omalens": P("omalens", ("omalens",), "/q", "t")})
N1 = mk("n1", TWO); N1.park = None
tell(N1, "stop")
check("no session running: said so", N1.events(0)[-1]["line"], "No sessions running.")
start_one(N1)
only = list(N1.sessions.values())[0]
tell(N1, "stop")
wait_for(lambda: only.state == "stopped")
check("one running: it stops", only.state, "stopped")
N1b = mk("n1b", TWO); N1b.park = None
start_one(N1b); start_one(N1b)
a, b = list(N1b.sessions.values())
b.project = TWO.projects["omalens"]
tell(N1b, "stop it")
time.sleep(0.1)
check("two running in two projects: asks which, stops nothing",
      (N1b.events(0)[-1]["line"], a.state, b.state), ("omacar or omalens?", "running", "running"))

print("\n  N2: after a usage limit, nothing starts for an hour")
N2 = mk("n2", BIG)
N2.emit({"kind": "limit", "session": "x", "project": "omacar", "until": "the limit resets"})
n = len(FakeSession.made)
tell(N2, "in omacar fix it")
check("refused with the limit line", (N2.events(0)[-1]["kind"], len(FakeSession.made) - n, N2.pending), ("limit", 0, None))
check("the line says until when", N2.events(0)[-1]["line"].startswith("Usage limit reached"), True)
N2.clk[0] += workrelay.LIMIT_S + 1
tell(N2, "in omacar fix it")
check("an hour later, the confirm is asked", N2.events(0)[-1]["kind"], "confirm")
N2.pending = None

print("\n  N3: repo names may hold dots")
class Out:
    def __init__(s, o): s.stdout = o
real_run = workrelay.subprocess.run
for url, want in (("git@github.com:o/omacar.github.io.git\n", "o/omacar.github.io"),
                  ("https://github.com/o/r.js\n", "o/r.js"), ("https://github.com/o/r\n", "o/r"),
                  ("https://github.com/o/r.git/\n", "o/r")):
    workrelay.subprocess.run = lambda *a, **k: Out(url)
    N2.repos.clear()
    check(f"repo of {url.strip()}", N2._repo(P("x", ("x",), "/r" + url, "t")), want)
workrelay.subprocess.run = real_run

print("\n  A: while_moving on, a moving tablet works")
WM = workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0), while_moving=True,
                    max_sessions=1)
pkw = FakePark(); pkw.parked = False
said_wm = []
RW = mk("wm", WM, park_=pkw, render=lambda t, o: said_wm.append(t) or False)
tell(RW, "in omacar fix it")
check("new_task while moving: a confirm, not parked_only", RW.events(0)[-1]["kind"], "confirm")
check("the read-back was rendered while moving", said_wm[-1], "omacar: fix it. Start it?")
tell(RW, "yes")
wait_for(lambda: len(RW.sessions) == 1)
wait_for(lambda: "started" in [e["kind"] for e in RW.events(0)])
check("yes while moving starts the session", len(RW.sessions), 1)
tell(RW, "in omacar fix it"); tell(RW, "yes")
check("a second task queues (one at a time)", (len(RW.queue), RW.events(0)[-1]["kind"]), (1, "busy"))
tell(RW, "in omacar fix it")
check("a third task is awaiting its yes", RW.pending is not None, True)
RW.tick(); RW.tick()
time.sleep(0.2)
check("tick while moving: session kept, queue kept, confirm kept",
      (list(RW.sessions.values())[0].state, len(RW.queue), RW.pending is not None), ("running", 1, True))
check("no not_started or parked_only from the tick",
      [e["kind"] for e in RW.events(0)].count("not_started") + [e["kind"] for e in RW.events(0)].count("parked_only"), 0)
RW.pending = None
srvw = workrelay.make_server(WM, 0, RW); PW = srvw.server_address[1]
threading.Thread(target=srvw.serve_forever, daemon=True).start()
TOK_SAVE, TOK = TOK, RW.token
HEARD["wm1"] = "um"
codew, _ = call("POST", "/utterance", {"client_id": "wm1", "audio_b64": b64, "mime": "audio/webm"},
                port=PW, headers={"Host": f"127.0.0.1:{PW}"})
check("POST /utterance while moving is not 409", codew != 409, True)
hw = call("GET", "/health", port=PW, headers={"Host": f"127.0.0.1:{PW}"})[1]
check("/health exposes while_moving", (hw["while_moving"], hw["parked"]), (True, False))
TOK = TOK_SAVE
check("/health of a default tablet says false", RH.status()["while_moving"], False)
srvw.shutdown()

print("\n  E2: accepted clips are processed in arrival order, once")
order = []
def ears_e2(path):
    cid = os.path.basename(path).split(".")[0]; order.append(cid); time.sleep(0.2 if cid == "o1" else 0)
    return HEARD[cid], -0.2
RO = mk("e2", CT, park_=FakePark()); RO.ears = ears_e2
RO.clk[0] = 5000.0
def drop(cid, text):
    HEARD[cid] = text
    p = os.path.join(SCRATCH, cid + ".webm"); open(p, "wb").write(b"x")
    return RO.accept(cid, p, [])
a1 = drop("o1", "um"); a2 = drop("o2", "um"); a3 = drop("o1", "um")
check("all three acknowledged at once, the duplicate with the same ack", (a1, a2, a3), ({"accepted": True},) * 3)
wait_for(lambda: len(order) == 2 and not RO.arrived)
time.sleep(0.2)
check("arrival order, the duplicate processed once", order, ["o1", "o2"])
check("both clips were decided", [e["kind"] for e in RO.events(0)].count("status"), 2)
RO.ears = lambda p: ("in omacar fix it", -0.2)
RO.clk[0] = 6000.0
HEARD["o4"] = "in omacar fix it"
p4 = os.path.join(SCRATCH, "o4.webm"); open(p4, "wb").write(b"x"); RO.accept("o4", p4, [])
wait_for(lambda: RO.pending is not None)
RO.clk[0] += 19
dry = threading.Event()
def ears_yes(path): dry.wait(2); return "yes", -0.2
RO.ears = ears_yes
HEARD["o5"] = "yes"
p5 = os.path.join(SCRATCH, "o5.webm"); open(p5, "wb").write(b"x"); RO.accept("o5", p5, [])
time.sleep(0.1); RO.clk[0] += 3                    # the window ends while the yes waits to be heard
dry.set()
wait_for(lambda: {"started", "not_started"} & {e["kind"] for e in RO.events(0)})
check("a yes received at 19 s counts though it was decided at 22 s (receipt time)",
      ("started" in [e["kind"] for e in RO.events(0)], "not_started" in [e["kind"] for e in RO.events(0)]), (True, False))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.stdout.flush()
os._exit(1 if fails else 0)
