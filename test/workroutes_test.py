#!/usr/bin/env python3
"""The app's side of Work: the queue survives a dead relay, sends oldest
first, never writes the secrets to disk, carries the relay's token, and the
demo refuses every route."""
import json, os, sys, tempfile, threading, urllib.error, urllib.request
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

print("\n  the secrets ride with the forward")
got = []
S2 = wr.Sender("http://relay", os.path.join(SCRATCH, "q2"),
               post=lambda u, p, t: got.append((u, p)) or {}, secrets=lambda: ["8ABC123"])
S2.enqueue({"client_id": "b1", "audio_b64": "eA==", "mime": "audio/webm"})
S2.drain_once()
check("posted to /utterance with secrets", (got[0][0], got[0][1]["secrets"]),
      ("http://relay/utterance", ["8ABC123"]))

print("\n  the relay's token")
class Resp:
    headers = {"Content-Type": "application/json"}
    def __init__(s, body): s.body = body
    def read(s): return s.body
    def __enter__(s): return s
    def __exit__(s, *a): pass
seen = []
def fake_urlopen(req, timeout=None):  # stands in for wr._open
    seen.append(req)
    return Resp(b'{"events": [], "ok": true}')
real_open = wr._open
wr._open = fake_urlopen
try:
    cfgdir = os.path.join(SCRATCH, "config", "omacar"); os.makedirs(cfgdir)
    toml = os.path.join(cfgdir, "work.toml")
    import workcfg
    workcfg.PATH = toml

    open(toml, "w").write('host = "box"\n')
    check("box, no token: relay reported offline, nothing sent",
          (wr.handle_get("/api/work/events", "after=0")[1]["online"], seen), (False, []))
    try: wr._post("http://relay/utterance", {}, 1); sent_ok = True
    except OSError: sent_ok = False
    check("box, no token: _post refuses to send", (sent_ok, seen), (False, []))

    open(toml, "w").write('host = "box"\nrelay_token = "box-tok"\n')
    wr._post("http://relay/utterance", {"a": 1}, 1)
    check("box: bearer from work.toml",
          (seen[-1].get_header("Authorization"), seen[-1].get_header("Content-type"),
           seen[-1].get_header("Origin")), ("Bearer box-tok", "application/json", None))
    raw, _ = wr._get("/health")
    check("box: GET carries the bearer too", seen[-1].get_header("Authorization"), "Bearer box-tok")

    import workrelay
    os.makedirs(workrelay.STATE, exist_ok=True)
    open(workrelay.token_path(), "w").write("tablet-tok\n")
    open(toml, "w").write('host = "tablet"\n')
    wr._post("http://relay/utterance", {"a": 1}, 1)
    check("tablet: bearer from the relay's token file",
          seen[-1].get_header("Authorization"), "Bearer tablet-tok")
finally:
    wr._open = real_open

print("\n  a clip is never lost, stalled, or sent unscrubbed")
def sender(name, post, secrets=lambda: ["8ABC123"]):
    return wr.Sender("http://relay", os.path.join(SCRATCH, name), post=post, secrets=secrets)
clip = lambda cid: {"client_id": cid, "audio_b64": "eA==", "mime": "audio/webm"}
def boom(): raise OSError("vehicle record unreadable")
got3 = []
S3 = sender("q3", lambda u, p, t: got3.append(p) or {}, secrets=boom)
S3.enqueue(clip("c1"))
check("secrets unreadable: nothing sent, clip kept", (S3.drain_once(), got3, len(S3.pending())), (0, [], 1))

S4 = sender("q4", lambda u, p, t: {})
ev, started = threading.Event(), threading.Event()
def slow(u, p, t): started.set(); ev.wait(5); return {}
S4.post = slow
S4.enqueue(clip("d1"))
th = threading.Thread(target=S4.drain_once); th.start(); started.wait(5)
done = []
t2 = threading.Thread(target=lambda: done.append(S4.enqueue(clip("d2")))); t2.start(); t2.join(1)
check("enqueue does not wait on a drain's network call", (done, "d2" in " ".join(os.listdir(S4.dir))), ([True], True))
check("a second drain does not run alongside", S4.drain_once(), 0)
ev.set(); th.join(5)

S5 = sender("q5", lambda u, p, t: {})
S5.enqueue(clip("e1")); S5.enqueue(clip("e2"))
open(os.path.join(S5.dir, "00000000000000000000000-zz.json"), "w").write("")   # garbage at the head
sent5 = []; S5.post = lambda u, p, t: sent5.append(p["client_id"]) or {}
check("garbage at the head: quarantined, the rest sent",
      (S5.drain_once(), sent5, os.listdir(S5.bad)), (2, ["e1", "e2"], ["00000000000000000000000-zz.json"]))

def http(code):
    def post(u, p, t): raise urllib.error.HTTPError(u, code, "x", {}, None)
    return post
S6 = sender("q6", http(400)); S6.enqueue(clip("f1")); S6.enqueue(clip("f2"))
check("relay 400: each clip quarantined, drain continues", (S6.drain_once(), S6.pending(), len(os.listdir(S6.bad))), (0, [], 2))
for code in (401, 403, 503):
    S7 = sender(f"q7{code}", http(code)); S7.enqueue(clip("g1")); S7.enqueue(clip("g2"))
    check(f"relay {code}: kept, nothing after it sent", (S7.drain_once(), len(S7.pending()), os.path.isdir(S7.bad)), (0, 2, False))

S8 = sender("q8", lambda u, p, t: {}); S8.enqueue(clip("utt-1")); S8.enqueue(clip("1"))
check("ids 1 and utt-1 are two clips", len(S8.pending()), 2)
S8.enqueue(clip("1"))
check("and a retry of 1 is still one", len(S8.pending()), 2)

open(os.path.join(S8.dir, "123-x.tmp"), "w").write("{")
S9 = wr.Sender("http://relay", S8.dir, post=lambda u, p, t: {}, secrets=list)
check("leftover .tmp cleaned at start", [f for f in os.listdir(S8.dir) if f.endswith(".tmp")], [])

class R302(urllib.request.BaseHandler): pass
check("relay calls do not follow redirects",
      wr._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://evil/"), None)
check("the opener uses that handler", any(isinstance(h, wr._NoRedirect) for h in wr._OPENER.handlers), True)

print("\n  F6: a relay that takes no clips while moving (409) keeps the queue")
S10 = sender("q10", http(409)); S10.enqueue(clip("h1")); S10.enqueue(clip("h2"))
check("409: kept, nothing after it sent, not quarantined, relay counted online",
      (S10.drain_once(), len(S10.pending()), os.path.isdir(S10.bad), S10.online, S10.held), (0, 2, False, True, True))
S10.post = lambda u, p, t: {}
check("once parked, both go, oldest first", (S10.drain_once(), S10.pending(), S10.held), (2, [], False))

print("\n  F2: the page learns whether its clip reached the relay")
up2 = [False]
def post2(u, p, t):
    if not up2[0]: raise OSError("no route")
    return {"ok": True}
wr._sender = sender("q11", post2)
body = lambda cid: json.dumps(clip(cid))
check("relay down: queued, not sent", wr.handle_post("/api/work/utterance", body("k1")),
      (200, {"queued": True, "sent": False, "held": False}))
up2[0] = True
check("relay up: sent", wr.handle_post("/api/work/utterance", body("k2")),
      (200, {"queued": True, "sent": True, "held": False}))
check("the older clip went first, nothing left", wr._sender.pending(), [])
wr._sender = sender("q12", http(409))
check("tablet, moving: saved and held, not sent", wr.handle_post("/api/work/utterance", body("k3")),
      (200, {"queued": True, "sent": False, "held": True}))
busy = sender("q13", lambda u, p, t: {})
busy.drain_lock.acquire(); wr._sender = busy
threading.Timer(0.3, busy.drain_lock.release).start()
check("a drain already running is waited for, then this clip goes",
      wr.handle_post("/api/work/utterance", body("k4"))[1]["sent"], True)
import io, contextlib
err = io.StringIO()
with contextlib.redirect_stderr(err):
    S14 = sender("q14", http(400)); S14.enqueue(clip("z1")); S14.drain_once()
check("a quarantine is logged with its status code, no clip or token",
      ("(400)" in err.getvalue(), "eA==" in err.getvalue(), "Bearer" in err.getvalue()), (True, False, False))
wr._sender = None

print("\n  N5: a malformed work.toml is offline, not a 500")
open(toml, "w").write('host = "box"\nrelay_token = \n')
check("token is empty", wr._token(), "")
check("status answers, offline", wr.handle_get("/api/work/status", "")[1]["online"], False)
open(toml, "w").write('host = "tablet"\n')

print("\n  routes")
check("unknown path falls through", wr.handle_get("/api/other", ""), None)
check("bad clip name refused", wr.handle_get("/api/work/clip/..%2fx", "")[0], 404)

sys.path.insert(0, os.path.join(ROOT, "lib"))
import serve  # noqa: E402
check("demo refuses /api/work/utterance", serve.demo_route("/api/work/utterance") == serve.ALLOW, False)
check("demo refuses /api/work/events", serve.demo_route("/api/work/events") == serve.ALLOW, False)
check("demo refuses /api/work/clip/1.wav", serve.demo_route("/api/work/clip/1.wav") == serve.ALLOW, False)
check("demo refuses /api/work/status", serve.demo_route("/api/work/status") == serve.ALLOW, False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
