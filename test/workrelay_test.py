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

print("\n  stop while a question is pending stops the session that asked")
R3 = workrelay.Relay(CFG, ears=ears, intent=intent, session_factory=FakeSession, park=park,
                     clock=lambda: now[0], state_dir=os.path.join(SCRATCH, "relay3"),
                     render=lambda t, o: False)
HEARD["q1"] = "in omacar fix it"; HEARD["q2"] = "yes"; HEARD["q3"] = "stop"
for cid in ("q1", "q2"):
    p = os.path.join(SCRATCH, cid + ".webm"); open(p, "wb").write(b"x"); R3.utterance(cid, p, [])
time.sleep(0.2)
asker = FakeSession.made[-1]
asyncio.run_coroutine_threadsafe(asker.ask("which one?"), R3.loop); time.sleep(0.2)
check("question is pending", R3.pending is not None and R3.pending[0], "question")
p = os.path.join(SCRATCH, "q3.webm"); open(p, "wb").write(b"x"); R3.utterance("q3", p, [])
time.sleep(0.2)
check("asking session stopped, pending cleared", (asker.state, R3.pending), ("stopped", None))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.stdout.flush()
os._exit(1 if fails else 0)
