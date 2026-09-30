#!/usr/bin/env python3
"""The demo server: what `serve.py --demo` answers, and what it refuses.

The owner's constraint, verbatim: "the demo mode should not in any way shape or
form affect the live running app when in the car, or affect the real data. The
demo must be siloed data". This suite holds the server half of that
(doc/design/2026-09-30-meetup-demo.md §2.3 and §2.5):

  every /api/ route the demo has no business calling answers 403, on GET and
    POST, before any handler runs;
  the background calls the live page makes get inert JSON, so the page stays
    quiet without the handler running;
  demo code and media are served only by a server started with --demo, from
    inside their own folders, with HTTP Range for the media;
  the live server answers 404 for all of it;
  and the demo server will not start at all over anything but the demo's own
    folders.

Everything runs in a scratch HOME with a scratch share/ root. Nothing here
reads or writes the real HOME, and no route that could reach a car, a device
or a service is ever let through to its handler.
"""

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVE = os.path.join(ROOT, "lib", "serve.py")
DEMO_DIR = os.path.join(ROOT, "demo")
PY = sys.executable

SCRATCH = tempfile.mkdtemp(prefix="omacar-demoserve-")
HOME = os.path.join(SCRATCH, "home")
DEMO_ROOT = os.path.join(HOME, ".local", "state", "omacar-demo")
# The demo's environment, as `omacar demo on` sets it (bin/omacar demo_env).
ENV = dict(os.environ,
           HOME=HOME,
           XDG_STATE_HOME=os.path.join(DEMO_ROOT, "state"),
           XDG_CONFIG_HOME=os.path.join(DEMO_ROOT, "config"),
           XDG_RUNTIME_DIR=os.path.join(DEMO_ROOT, "run"),
           XDG_DATA_HOME=os.path.join(HOME, ".local", "share"),
           XDG_CACHE_HOME=os.path.join(HOME, ".cache"),
           OMACAR_STATE=os.path.join(DEMO_ROOT, "state", "omacar"),
           OMACAR_VIDEOS=os.path.join(DEMO_ROOT, "videos"),
           OMACAR_PORT=os.path.join(DEMO_ROOT, "no-adapter"))
for _d in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "OMACAR_STATE",
           "OMACAR_VIDEOS"):
    os.makedirs(ENV[_d], exist_ok=True)
os.environ.update(ENV)
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port, secs=20):
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# ---- a scratch share/ root, with a private tree like the box's --------------
#
# The real share/assets/private holds the owner's songs and pictures, and is
# not in the test mirrors at all. The server takes its share root on the
# command line and finds the private tree under it, so a scratch root with a
# scratch private tree is the same server on test data.

SHARE = os.path.join(SCRATCH, "share")
PRIVATE = os.path.join(SHARE, "assets", "private")
for _d in ("omarchy-radio", "demo/voice", "demo/clips", "demo/elsewhere"):
    os.makedirs(os.path.join(PRIVATE, _d))
with open(os.path.join(SHARE, "marker.txt"), "w", encoding="utf-8") as f:
    f.write("the live share root\n")

# A fixture wav, made here rather than committed: a tenth of a second of silence
# at 8 kHz, which is a real RIFF file a <audio> would play.
WAV = os.path.join(PRIVATE, "demo", "voice", "fixture.wav")
with wave.open(WAV, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(bytes(range(256)) * 6 + b"\x00" * 64)
with open(WAV, "rb") as f:
    WAV_BYTES = f.read()
SONG = b"ID3" + bytes(range(200)) * 3
with open(os.path.join(PRIVATE, "omarchy-radio", "Ryan R Hughes - A Song.mp3"), "wb") as f:
    f.write(SONG)
with open(os.path.join(PRIVATE, "omacar-logo.png"), "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"logo" * 10)
with open(os.path.join(PRIVATE, "demo", "drive.json"), "w", encoding="utf-8") as f:
    json.dump({"version": 1}, f)
with open(os.path.join(PRIVATE, "demo", "clips", "front.mp4"), "wb") as f:
    f.write(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
# Something secret beside the private tree, and a link inside it pointing out.
with open(os.path.join(SCRATCH, "secret.txt"), "w", encoding="utf-8") as f:
    f.write("not for the demo\n")
os.symlink(os.path.join(SCRATCH, "secret.txt"),
           os.path.join(PRIVATE, "demo", "elsewhere", "link.txt"))


def start(port, *extra, env=ENV):
    """The server on `port`, its stderr in a file: a pipe nobody drains fills,
    and a server blocked writing a traceback into it looks like a hang."""
    err = open(os.path.join(SCRATCH, f"serve-{port}.err"), "wb")
    p = subprocess.Popen([PY, SERVE, str(port), SHARE, *extra], env=env,
                         stdout=subprocess.DEVNULL, stderr=err)
    err.close()
    p.errfile = err.name
    return p


def stderr_of(p):
    try:
        with open(p.errfile, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def req(port, method, path, body=None, headers=None):
    """(status, headers, body); status None when the server dropped the
    connection, which is what a handler that raised looks like from here."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    try:
        c.request(method, path, body=body, headers=hdrs)
        r = c.getresponse()
        data = r.read()
    except (OSError, http.client.HTTPException):
        return None, {}, b""
    finally:
        c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def as_json(data):
    try:
        return json.loads(data)
    except ValueError:
        return None


NOT_IN_DEMO = {"error": "not in the demo"}

# The routes that must NEVER reach their handler in the demo, named here as
# well as in the table: a later edit that lets one of these through fails this
# suite however the table is changed. Each can reach the car, the adapter, a
# device, a service, the real claude CLI, or the real recorder.
MUST_REFUSE = [
    "/api/begin",        # stops the real omacar-drivelog.service
    "/api/daemon",       # starts and stops the polling daemon
    "/api/adapter",      # goes looking for the OBD adapter
    "/api/scan", "/api/learn", "/api/clear", "/api/reset",
    "/api/write-mode", "/api/write-did", "/api/actuate",
    "/api/record", "/api/document", "/api/photo",
    "/api/vehicle", "/api/odometer", "/api/service", "/api/units",
    "/api/ai",           # runs the real claude CLI
    "/api/ai/anything",
    "/api/phone", "/api/phone/start", "/api/phone/stop", "/api/phone/input",
    "/api/phone/route", "/api/phone/video",
    "/api/no-such-route", "/api/begin/", "/api//begin",
]

PORT = free_port()
SRV = start(PORT, "--demo", DEMO_DIR)
LIVE_PORT = None
LIVE = None
try:
    if not wait_for_port(PORT):
        sys.exit(f"  FAIL  the demo server never came up {stderr_of(SRV).strip()}")

    import serve  # noqa: E402  (after the environment is the demo's)

    head("the allowlist is a table, and every entry has a verdict")
    verdicts = {v if isinstance(v, str) else "stub" for v in serve.DEMO_ROUTES.values()}
    check("every entry is allow, read, refuse or a stub",
          verdicts - {"allow", "read", "refuse", "stub"}, set())
    check("an unknown /api/ path is refused",
          serve.demo_route("/api/something-new"), "refuse")
    for p in MUST_REFUSE:
        if serve.demo_route(p) != "refuse":
            bad(f"{p} is not refused by the table ({serve.demo_route(p)!r})")
    ok("the routes that reach the car, a device or a service are all refused")

    head("every refused route answers 403 on GET and POST, and HEAD")
    refused = [k.replace("*", "x") for k, v in serve.DEMO_ROUTES.items() if v == "refuse"]
    wrong = []
    for p in sorted(set(refused + MUST_REFUSE)):
        for m in ("GET", "POST"):
            st, _, body = req(PORT, m, p, body="{}" if m == "POST" else None)
            if (st, as_json(body)) != (403, NOT_IN_DEMO):
                wrong.append((m, p, st, body[:80]))
        st, _, _ = req(PORT, "HEAD", p)
        if st != 403:
            wrong.append(("HEAD", p, st, b""))
    check(f"{len(set(refused + MUST_REFUSE))} routes, 403 {{'error': 'not in the demo'}}",
          wrong, [])

    head("a read-only route reads, and refuses a write")
    reads = [k for k, v in serve.DEMO_ROUTES.items() if v == "read"]
    wrong = []
    for p in reads:
        st, _, body = req(PORT, "POST", p, body='{"reason": "test"}')
        if (st, as_json(body)) != (403, NOT_IN_DEMO):
            wrong.append(("POST", p, st))
        st, _, _ = req(PORT, "GET", p)
        if st == 403:
            wrong.append(("GET", p, st))
    check(f"{len(reads)} read-only routes: GET answers, POST is 403", wrong, [])
    st, _, body = req(PORT, "GET", "/api/live")
    check("/api/live reads the demo's own state (there is no live.json yet)",
          (st, (as_json(body) or {}).get("connected")), (200, False))

    head("the background calls get inert answers, and nothing runs")
    for m in ("GET", "POST"):
        st, _, body = req(PORT, m, "/api/audio",
                          body='{"action": "apply"}' if m == "POST" else None)
        check(f"{m} /api/audio is the stub: the volume pin is not the demo's",
              (st, as_json(body)), (200, {"managed": False, "demo": True}))
    st, _, body = req(PORT, "GET", "/api/ai/available")
    check("/api/ai/available says no", (st, as_json(body)), (200, {"available": False}))
    st, _, body = req(PORT, "GET", "/api/ai/history")
    check("/api/ai/history is empty", (st, as_json(body)), (200, {"records": []}))
    st, _, body = req(PORT, "POST", "/api/assistant", body='{"action": "present"}')
    check("the voice assistant is not present, so its button stays hidden",
          (st, as_json(body)), (200, {"ok": False, "present": False}))

    head("the demo page, its code and its media")
    st, h, body = req(PORT, "GET", "/demo.html")
    check("/demo.html answers", st, 200)
    ok("and it loads demo/js/boot.js") if b"demo/js/boot.js" in body else \
        bad("/demo.html does not load demo/js/boot.js")
    check("as html", (h.get("content-type") or "").split(";")[0], "text/html")
    st, h, body = req(PORT, "GET", "/demo/js/boot.js")
    check("/demo/js/boot.js answers as JavaScript",
          (st, "javascript" in (h.get("content-type") or "")), (200, True))
    with open(os.path.join(DEMO_DIR, "js", "boot.js"), "rb") as f:
        check("with the file's bytes", body, f.read())
    st, h, _ = req(PORT, "HEAD", "/demo.html")
    check("HEAD /demo.html answers too", st, 200)
    st, _, body = req(PORT, "GET", "/marker.txt")
    check("the live share/ is still served beside it", (st, body),
          (200, b"the live share root\n"))

    st, h, body = req(PORT, "GET", "/demo-media/voice/fixture.wav")
    check("a voice clip, whole, as audio/wav with ranges welcome",
          (st, body == WAV_BYTES, h.get("content-type"), h.get("accept-ranges")),
          (200, True, "audio/wav", "bytes"))
    st, h, body = req(PORT, "GET", "/demo-media/voice/fixture.wav",
                      headers={"Range": "bytes=44-99"})
    check("a Range on it is 206 with exactly those bytes",
          (st, body, h.get("content-range")),
          (206, WAV_BYTES[44:100], f"bytes 44-99/{len(WAV_BYTES)}"))
    st, h, body = req(PORT, "GET", "/demo-media/radio/Ryan%20R%20Hughes%20-%20A%20Song.mp3",
                      headers={"Range": "bytes=-10"})
    check("a song, by its percent-encoded name, as audio/mpeg in ranges",
          (st, body, h.get("content-type")), (206, SONG[-10:], "audio/mpeg"))
    st, h, body = req(PORT, "GET", "/demo-media/logo.png")
    check("the logo", (st, h.get("content-type"), body[:4]), (200, "image/png", b"\x89PNG"))
    st, h, body = req(PORT, "GET", "/demo-media/drive.json")
    check("the drive, as JSON", (st, h.get("content-type"), as_json(body)),
          (200, "application/json", {"version": 1}))
    st, h, _ = req(PORT, "GET", "/demo-media/clips/front.mp4")
    check("a clip, as video/mp4", (st, h.get("content-type")), (200, "video/mp4"))

    head("nothing outside the mapped folders")
    for p in ("/demo-media/../../etc/passwd",
              "/demo-media/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
              "/demo-media/%2E%2E%2F%2E%2E%2Fsecret.txt",
              "/demo-media/radio/../../../../secret.txt",
              "/demo-media/radio/../demo/drive.json",     # another root's file
              "/demo-media/elsewhere/link.txt",            # a link pointing out
              "/demo-media//etc/passwd",
              "/demo-media/voice/fixture.wav%00.png",
              "/demo/../lib/serve.py",
              "/demo/%2e%2e/lib/serve.py",
              "/demo/", "/demo", "/demo-media/", "/demo-media/voice"):
        st, _, body = req(PORT, "GET", p)
        if st != 404 or b"not for the demo" in body or b"root:" in body:
            bad(f"{p} answered {st}")
    ok("traversal, encoded traversal, links out, other roots and folders are 404")

    head("POST /api/demo/cue")
    CUE = os.path.join(ENV["OMACAR_STATE"], "demo-cue.json")
    t0 = time.time()
    st, _, body = req(PORT, "POST", "/api/demo/cue", body='{"cue": "park"}')
    check("a park cue is taken", (st, as_json(body)), (200, {"ok": True}))
    try:
        with open(CUE, encoding="utf-8") as f:
            cue = json.load(f)
    except (OSError, ValueError):
        cue = {}
    check("and written for the demo world, in the demo's own state",
          (cue.get("cue"), t0 - 1 <= (cue.get("at") or 0) <= time.time() + 1),
          ("park", True))
    for c in ("drive", "drowsy", "hard_brake", "restart"):
        st, _, _ = req(PORT, "POST", "/api/demo/cue", body=json.dumps({"cue": c}))
        if st != 200:
            bad(f"the {c} cue answered {st}")
    ok("drive, drowsy, hard_brake and restart are taken too")
    st, _, _ = req(PORT, "POST", "/api/demo/cue", body='{"cue": "self_destruct"}')
    with open(CUE, encoding="utf-8") as f:
        last = json.load(f)
    check("a cue the world does not know is 400, and the file keeps the last good one",
          (st, last.get("cue")), (400, "restart"))
    st, _, _ = req(PORT, "POST", "/api/demo/cue", body='{"cue": "park"}',
                   headers={"Sec-Fetch-Site": "cross-site"})
    check("a cue from another site's page is refused", st, 403)

    head("the demo's routes, classified: the page's calls and the server's")
    # Every /api/ path the live page or the demo page can call has an entry of
    # its own, so a new call on the page gets a decision rather than a default.
    called = set()
    for top in (os.path.join(ROOT, "share", "js"), os.path.join(ROOT, "demo")):
        for d, _, files in os.walk(top):
            if "vendor" in d.split(os.sep):
                continue
            for n in files:
                if n.endswith(".js"):
                    with open(os.path.join(d, n), encoding="utf-8") as f:
                        for m in re.finditer(r"[\"'`](/api/[A-Za-z0-9_/\-]*)", f.read()):
                            called.add(m.group(1) + ("x" if m.group(1).endswith("/") else ""))
    check("the page calls a sensible number of routes", len(called) > 40, True)
    check("every one of them has its own entry in DEMO_ROUTES",
          sorted(p for p in called if serve.demo_entry(p) is None), [])
    served = set()
    for n in ("api.py", "camroutes.py", "serve.py"):
        with open(os.path.join(ROOT, "lib", n), encoding="utf-8") as f:
            src = f.read()
        served.update(re.findall(r'path == "(/api/[^"]*)"', src))
        for grp in re.findall(r'path in \(([^)]*)\)', src):
            served.update(re.findall(r'"(/api/[^"]*)"', grp))
        served.update(p + "x" for p in re.findall(r'startswith\("(/api/[^"]+/)"\)', src))
    check("every route the server answers has one too",
          sorted(p for p in served if serve.demo_entry(p) is None), [])

    head("without --demo, none of it exists")
    LIVE_PORT = free_port()
    LIVE = start(LIVE_PORT)
    if not wait_for_port(LIVE_PORT):
        bad("the live server never came up")
    else:
        for p in ("/demo.html", "/demo/js/boot.js", "/demo-media/logo.png",
                  "/demo-media/voice/fixture.wav", "/demo/demo.html"):
            st, _, _ = req(LIVE_PORT, "GET", p)
            if st != 404:
                bad(f"the live server answered {st} for {p}")
        ok("/demo.html, /demo/* and /demo-media/* are 404 on the live server")
        st, _, _ = req(LIVE_PORT, "POST", "/api/demo/cue", body='{"cue": "park"}')
        check("and it takes no cues", st, 404)
        # The assistant's "present" only looks for a binary on PATH, so it is
        # safe to ask for real; /api/audio's real answer would run wpctl.
        st, _, body = req(LIVE_PORT, "POST", "/api/assistant", body='{"action": "present"}')
        check("its own routes are its own (the real handler answers, not the stub)",
              (st, (as_json(body) or {}).get("action")), (200, "present"))

    head("the live page never loads demo code")
    with open(os.path.join(ROOT, "share", "app.html"), encoding="utf-8") as f:
        app = f.read()
    check("share/app.html mentions no demo/", "demo/" in app, False)

    head("the demo server will not serve the demo over real folders")
    real_ish = dict(ENV, XDG_STATE_HOME=os.path.join(HOME, ".local", "state"),
                    OMACAR_STATE=os.path.join(HOME, ".local", "state", "omacar"))
    no_videos = {k: v for k, v in ENV.items() if k != "OMACAR_VIDEOS"}
    port_exists = dict(ENV, OMACAR_PORT=WAV)
    for why, env in (("the real state folder", real_ish),
                     ("no OMACAR_VIDEOS, so the real ~/Videos/OmaCar", no_videos),
                     ("an OMACAR_PORT that exists", port_exists)):
        p = start(free_port(), "--demo", DEMO_DIR, env=env)
        try:
            rc = p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        err = stderr_of(p)
        check(f"refused with {why}", (rc not in (None, 0), "demo" in err), (True, True))
    p = start(free_port(), "--demo", DEMO_DIR, "--host", "0.0.0.0", "--token", "t")
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = None
    check("and never on the network", rc not in (None, 0), True)
finally:
    for proc in (SRV, LIVE):
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

print(f"\n  {'all passed' if not fails else f'{fails} failed'}\n")
sys.exit(1 if fails else 0)
