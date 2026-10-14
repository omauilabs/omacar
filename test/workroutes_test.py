#!/usr/bin/env python3
"""The app's side of Work: the queue survives a dead relay, sends oldest
first, never writes the secrets to disk, carries the relay's token, and the
demo refuses every route."""
import json, os, sys, tempfile, urllib.request
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
def fake_urlopen(req, timeout=None):
    seen.append(req)
    return Resp(b'{"events": [], "ok": true}')
real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
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
    urllib.request.urlopen = real_urlopen

print("\n  routes")
check("unknown path falls through", wr.handle_get("/api/other", ""), None)
check("bad clip name refused", wr.handle_get("/api/work/clip/..%2fx", "")[0], 404)

sys.path.insert(0, os.path.join(ROOT, "lib"))
import serve  # noqa: E402
check("demo refuses /api/work/utterance", serve.demo_route("/api/work/utterance") == serve.ALLOW, False)
check("demo refuses /api/work/status", serve.demo_route("/api/work/status") == serve.ALLOW, False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
