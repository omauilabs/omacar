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
tell(RI, "blue"); tell(RI, "green")
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
code, reply = utt("c-ok", said="in omacar fix it", secrets="8ABC123")
check("a good utterance", (code, reply.get("kind")), (200, "confirm"))
check("secrets that are not a list are ignored, never split into letters",
      any(e.get("text") == "in omacar fix it" for e in RH.events(0)), True)
n = len(RH.events(0)); code2, reply2 = utt("c-ok", said="in omacar fix it")
check("same client id over HTTP is one utterance", (reply2 == reply, len(RH.events(0))), (True, n))
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
QT = workcfg.Config(host="tablet", daily_cap=6, projects=CFG.projects, quiet=(0, 0))
pk = FakePark(); RN = mk("rn", QT, park_=pk, render=spy)
loop_ident = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, threading.get_ident()), RN.loop).result(3)
start_one(RN); start_one(RN)
tell(RN, "in omacar fix it"); tell(RN, "yes")
check("third is queued", len(RN.queue), 1)
del renders[:]
pk.parked = False                      # cached value only: no tick, so the queue is not cleared
first = list(RN.sessions.values())[0]
asyncio.run_coroutine_threadsafe(first.stop(), RN.loop)
wait_for(lambda: "parked_only" in [e["kind"] for e in RN.events(0)])
refusal = "Work starts sessions here only while parked."
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

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.stdout.flush()
os._exit(1 if fails else 0)
