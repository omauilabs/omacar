#!/usr/bin/env python3
"""The silo: `omacar demo` cannot touch the car, the live app or the real data.

The owner's constraint, verbatim: "the demo mode should not in any way shape or
form affect the live running app when in the car, or affect the real data. The
demo must be siloed data". This suite holds bin/omacar's half of it
(doc/design/2026-09-30-meetup-demo.md §2), in a scratch HOME seeded with a fake
real state:

  `demo on` refuses while the real live.json says the car is moving, and
    starts nothing;
  the guard closes the demo when the real car starts moving, and goes when
    the demo does;
  the guard is the demo's watchdog: a server, world or camera feed that dies
    is started again, as `demo on` started it, through `demo mend`, with a
    backoff, and never while the real car moves or once the demo is off;
  `demo off` asks the demo's own server for quiet and gives the music its
    fade before it closes the window;
  `demo off` stops only the demo's own processes, found by what they are and
    never by a pid alone or a port;
  another checkout's running demo is neither stopped nor forgotten from here,
    and `demo on` will not start a second one beside it;
  `demo cache` writes the demo's own panel rollup, never the real one;
  and none of it writes a byte outside the demo's folder or into the real
    runtime folder, or runs systemctl, wpctl or pactl, which are shims here
    that write down every call.

Linux only: bin/omacar is bash on Linux (setsid, /proc, ss).
"""

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OMACAR = os.path.join(ROOT, "bin", "omacar")
GUARD = os.path.join(ROOT, "lib", "demoguard.py")
# Resolved before any environment is redirected (see test/all.sh on why).
PY = sys.executable

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


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), 0.25):
            return True
    except OSError:
        return False


SCRATCH = tempfile.mkdtemp(prefix="omacar-silo-")
HOME = os.path.join(SCRATCH, "home")
SHIMS = os.path.join(SCRATCH, "bin")
SHIM_LOG = os.path.join(SCRATCH, "shims.log")
RUNTIME = os.path.join(SCRATCH, "run")
DEMO_ROOT = os.path.join(HOME, ".local", "state", "omacar-demo")
REAL_STATE = os.path.join(HOME, ".local", "state", "omacar")
REAL_LIVE = os.path.join(REAL_STATE, "live.json")
# A port of its own, so this suite never meets a real demo on 7580, or another
# run of itself (bin/omacar takes OMACAR_DEMO_PORT for exactly this).
PORT = free_port()

for d in (SHIMS, RUNTIME, REAL_STATE):
    os.makedirs(d, exist_ok=True)
os.chmod(RUNTIME, 0o700)

# ---- the fake real state ---------------------------------------------------

REAL_FILES = {
    ".local/state/omacar/live.json": None,            # written per case below
    ".config/omarchy/omacar-audio.json": json.dumps({"managed": True, "target": 1.0}),
    ".config/omarchy/omacar-roadcams.json": json.dumps({"pins": ["D4-1"]}),
    ".local/state/omacar/roadcams/list.json": json.dumps({"cameras": []}),
    "Videos/OmaCar/front/20260930-010000.mp4": "not really a clip",
    ".local/state/omarchy/liquid-glass-car.json": json.dumps({"name": "the real car"}),
}
for rel, body in REAL_FILES.items():
    p = os.path.join(HOME, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body or "{}")


# The REAL runtime folder, as this suite hands it to bin/omacar: what the live
# recorder keeps there (its status and each camera's latest picture). The demo's
# server and camera feed must get the demo's own on their command lines; one
# that inherited this one would read, or write, these.
for rel, body in (("omacar-cams/status.json", json.dumps({"t": 1, "pid": 1, "roles": {}})),
                  ("omacar-cams/front.jpg", "\xff\xd8 the real front camera")):
    p = os.path.join(RUNTIME, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)


def write_live(speed, age=0.0):
    with open(REAL_LIVE, "w", encoding="utf-8") as f:
        json.dump({"connected": True, "t": time.time() - age,
                   "values": {"SPEED": speed, "RPM": 900}}, f)


def window_told_to_go():
    """When the shim window last had its SIGTERM, or None."""
    try:
        with open(CHROMIUM_TERMS, encoding="utf-8") as f:
            return [float(ln) for ln in f if ln.strip()][-1]
    except (OSError, ValueError, IndexError):
        return None

def cue_now():
    """The demo's last cue, as the server wrote it: (cue, at), or None."""
    try:
        with open(os.path.join(DEMO_ROOT, "state", "omacar", "demo-cue.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return d.get("cue"), d.get("at")
    except (OSError, ValueError):
        return None


def outside_demo():
    """sha256 of every file under HOME that is not in the demo's folder, and of
    every file in the real runtime folder (sockets aside: they have no bytes)."""
    out = {}
    for top, tag in ((HOME, "home"), (RUNTIME, "run")):
        for d, dirs, files in os.walk(top):
            if os.path.realpath(d).startswith(os.path.realpath(DEMO_ROOT)):
                dirs[:] = []
                continue
            for n in files:
                p = os.path.join(d, n)
                key = tag + ":" + os.path.relpath(p, top)
                if os.path.islink(p):
                    out[key] = "link:" + os.readlink(p)
                    continue
                if stat.S_ISSOCK(os.lstat(p).st_mode):
                    continue
                with open(p, "rb") as f:
                    out[key] = hashlib.sha256(f.read()).hexdigest()
    return out


# ---- PATH shims: each writes down that it was called ------------------------

for name in ("systemctl", "wpctl", "pactl", "hyprctl"):
    p = os.path.join(SHIMS, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\necho "{name} $*" >> "{SHIM_LOG}"\nexit 0\n')
    os.chmod(p, 0o755)
# Chromium stays up until it is told to go, as the real one does, and keeps its
# arguments on its command line, which is what `demo off` finds it by.
# It also writes down the cache folder it was given (the demo's own, on its
# command line only), and when it was told to go.
CHROMIUM_ENV = os.path.join(SCRATCH, "chromium.env")
CHROMIUM_TERMS = os.path.join(SCRATCH, "chromium.terms")
with open(os.path.join(SHIMS, "chromium"), "w", encoding="utf-8") as f:
    f.write(f"""#!{PY}
import os, signal, sys, time
def term(*_):
    with open({CHROMIUM_TERMS!r}, "a") as f:
        f.write(str(time.time()) + "\\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
with open({CHROMIUM_ENV!r}, "w") as f:
    f.write(os.environ.get("XDG_CACHE_HOME", ""))
with open({SHIM_LOG!r}, "a") as f:
    f.write("chromium " + " ".join(sys.argv[1:]) + "\\n")
time.sleep(600)
""")
os.chmod(os.path.join(SHIMS, "chromium"), 0o755)
# The venv's interpreter, as the tablet has one: the system's, by another name.
VENV_PY = os.path.join(HOME, ".local", "share", "omacar", "venv", "bin", "python")
os.makedirs(os.path.dirname(VENV_PY))
with open(VENV_PY, "w", encoding="utf-8") as f:
    f.write(f'#!/bin/sh\nexec "{PY}" "$@"\n')
os.chmod(VENV_PY, 0o755)

ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(("XDG_", "OMACAR_", "WAYLAND_"))}
# The demo's footage: three-second test patterns made below, handed to the
# camera feed as --from by  (OMACAR_DEMO_CLIPS), because the private
# clips are not in a test mirror.
CLIPS = os.path.join(SCRATCH, "clips")
ENV.update(HOME=HOME, PATH=SHIMS + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
           XDG_RUNTIME_DIR=RUNTIME, WAYLAND_DISPLAY="wayland-test",
           OMACAR_DEMO_PORT=str(PORT), OMACAR_DEMO_MUTE="1", OMACAR_DEMO_CLIPS=CLIPS)
# The environment every demo process is started with carries the demo's own
# state folder; a look-alike given it is told apart by what else it is.
DEMO_STATE_ENV = dict(os.environ, OMACAR_STATE=os.path.join(DEMO_ROOT, "state", "omacar"))


def omacar(*args, timeout=90, env=None):
    r = subprocess.run([OMACAR, *args], env=env or ENV, capture_output=True,
                       text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def shim_calls():
    try:
        with open(SHIM_LOG, encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return None


def environ_has(pid, text):
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            return text.encode() in f.read()
    except OSError:
        return False


def ours_running():
    """This run's demo processes, and its shim browser, still alive: this
    checkout's scripts started with this scratch HOME, a browser on this demo's
    profile, or an ffmpeg of the camera feed's writing into the demo."""
    out = []
    scripts = [os.path.join(ROOT, "lib", n)
               for n in ("serve.py", "demoworld.py", "demoguard.py", "cams.py")]
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid():
            continue
        args = cmdline(p) or []
        if (any(a in scripts for a in args) and environ_has(p, HOME)) \
                or f"--user-data-dir={DEMO_ROOT}/browser" in args \
                or (args[:1] and os.path.basename(args[0]) == "ffmpeg"
                    and any(DEMO_ROOT in a for a in args)):
            out.append((int(p), " ".join(args)[:120]))
    return out


def spawn_decoy(*argv, env=None):
    return subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, env=env,
                            stderr=subprocess.DEVNULL, start_new_session=True)


DECOYS = []
try:
    BEFORE = None

    head("the guard's one question: is the real car moving?")

    def state_of(doc, raw=None):
        p = os.path.join(SCRATCH, "probe-live.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(doc))
        r = subprocess.run([PY, GUARD, "--state", p], capture_output=True, text=True,
                           timeout=20)
        return r.stdout.strip()

    now = time.time()
    check("fresh, at 50 km/h: moving", state_of({"t": now, "values": {"SPEED": 50}}), "moving")
    check("fresh, at 3.5 km/h: moving", state_of({"t": now, "values": {"SPEED": 3.5}}), "moving")
    check("fresh, at 3 km/h: parked (creeping in a car park is not driving)",
          state_of({"t": now, "values": {"SPEED": 3}}), "parked")
    check("4 s old at 50 km/h: still moving",
          state_of({"t": now - 4, "values": {"SPEED": 50}}), "moving")
    check("a minute old at 50 km/h: parked (nothing is answering for the car)",
          state_of({"t": now - 60, "values": {"SPEED": 50}}), "parked")
    check("no speed at all: parked", state_of({"t": now, "values": {}}), "parked")
    check("a speed that is not a number: parked",
          state_of({"t": now, "values": {"SPEED": "fast"}}), "parked")
    check("not JSON: parked", state_of(None, raw="{half"), "parked")
    r = subprocess.run([PY, GUARD, "--state", os.path.join(SCRATCH, "nope.json")],
                       capture_output=True, text=True, timeout=20)
    check("no live.json at all, as at the venue: parked", r.stdout.strip(), "parked")

    head("demo on refuses while the real car is moving, and starts nothing")
    write_live(50)
    BEFORE = outside_demo()
    rc, out = omacar("demo", "on")
    check("it exits non-zero", rc != 0, True)
    check("and says why", "moving" in out, True)
    check("nothing was run: no browser, no systemctl, no wpctl, no pactl", shim_calls(), [])
    check("the demo's folder was not even made", os.path.exists(DEMO_ROOT), False)
    check("nothing is listening on the demo's port", listening(PORT), False)
    check("no demo process is running", ours_running(), [])
    check("every real file is byte-identical", outside_demo(), BEFORE)
    rc, out = omacar("demo", "tour")
    check("demo tour refuses the same way", (rc != 0, "moving" in out, shim_calls()),
          (True, True, []))
    rc, out = omacar("demo", "start")
    check("and so does start, the old name for on", (rc != 0, "moving" in out), (True, True))

    head("demo on, a cue, demo off: the whole demo, and not a byte of yours")
    # Three seconds of test pattern per camera, for the real `cams.py demo`.
    os.makedirs(CLIPS)
    for role, src in (("front", "testsrc2=size=192x108:rate=25"),
                      ("rear", "testsrc2=size=192x108:rate=25"),
                      ("cabin", "testsrc2=size=128x96:rate=25"),
                      ("cabin-drowsy", "smptebars=size=128x96:rate=25")):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", src, "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "40", "-g", "25", "-pix_fmt", "yuv420p",
                        os.path.join(CLIPS, role + ".mp4")],
                       check=True, capture_output=True, timeout=60)
    write_live(0)                      # the real car, parked and answering
    open(SHIM_LOG, "w").close()
    BEFORE = outside_demo()
    URL = f"http://127.0.0.1:{PORT}/demo.html"
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on starts", rc, 0)
    if rc != 0:
        print(out)
    check("and says where the demo is", URL in out, True)
    check("it started the world, the camera feed, the server, the guard and the window",
          [w for w in ("the demo world", "the camera feed", "the demo server", "the guard",
                       "the demo window") if w not in out.split("started", 1)[-1]], [])

    # setsid -f: the window starts on its own time, so wait for it to say so.
    for _ in range(50):
        opened = [c.split()[1:] for c in shim_calls() if c.startswith("chromium ")]
        if opened:
            break
        time.sleep(0.2)
    check("the window was opened once", len(opened), 1)
    argv = opened[0] if opened else []
    check("on the demo's own profile",
          f"--user-data-dir={DEMO_ROOT}/browser" in argv, True)
    check("at the demo page", f"--app={URL}" in argv, True)
    # The live kiosk's flags, read from kiosk() itself, so the two windows stay
    # in step: everything it passes but its profile and its page.
    with open(OMACAR, encoding="utf-8") as f:
        _kiosk = f.read().split("kiosk() {", 1)[1].split("\n}", 1)[0]
    KIOSK_FLAGS = [ln.strip().rstrip("\\").strip() for ln in _kiosk.splitlines()
                   if ln.strip().startswith("--")
                   and not ln.strip().startswith(("--user-data-dir", "--app"))]
    check("with every flag the live kiosk has", (len(KIOSK_FLAGS) >= 10,
          [f for f in KIOSK_FLAGS if f not in argv]), (True, []))
    check("muted under OMACAR_DEMO_MUTE=1, and no camera or microphone prompt, ever",
          ("--mute-audio" in argv, "--deny-permission-prompts" in argv), (True, True))
    try:
        with open(CHROMIUM_ENV, encoding="utf-8") as f:
            cache = f.read()
    except OSError:
        cache = None
    check("with the demo's own cache folder, given on its command line",
          cache, os.path.join(DEMO_ROOT, "cache"))

    import http.client

    def hreq(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
        try:
            c.request(method, path, body=body,
                      headers={"Content-Type": "application/json"} if body else {})
            r = c.getresponse()
            return r.status, r.read()
        except (OSError, http.client.HTTPException):
            return None, b""
        finally:
            c.close()

    st, body = hreq("GET", "/demo.html")
    check("the demo server serves the demo page", (st, b"demo/js/boot.js" in body), (200, True))
    st, _ = hreq("POST", "/api/begin", "{}")
    check("and refuses the route that stops the real recorder", st, 403)

    demo_live = os.path.join(DEMO_ROOT, "state", "omacar", "live.json")
    fresh = None
    for _ in range(50):
        try:
            with open(demo_live, encoding="utf-8") as f:
                fresh = json.load(f)
            if time.time() - fresh.get("t", 0) < 3:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    check("the demo world is driving, in the demo's own live.json",
          bool(fresh) and time.time() - fresh.get("t", 0) < 3 and "demo" in fresh, True)
    run_cams = os.path.join(DEMO_ROOT, "run", "omacar-cams")
    front_clips = os.path.join(DEMO_ROOT, "videos", "front")

    def _clips():
        return len(os.listdir(front_clips)) if os.path.isdir(front_clips) else 0
    # The pictures come first; the 50 minutes of clips are laid down beside them.
    for _ in range(150):
        if all(os.path.exists(os.path.join(run_cams, r + ".jpg")) for r in ("front", "rear", "cabin")) \
                and _clips() >= 50:
            break
        time.sleep(0.2)
    check("the real camera feed draws each camera's live picture in the demo's runtime folder",
          [r for r in ("front", "rear", "cabin")
           if not os.path.exists(os.path.join(run_cams, r + ".jpg"))], [])
    check("with its status, and the last 50 minutes of clips in the demo's videos",
          (os.path.exists(os.path.join(run_cams, "status.json")), _clips()), (True, 50))
    rc, out = omacar("demo", "status")
    check("status: the world, the server, the guard and the window",
          [w for w in ("demo world     running", URL, "watching", "demo window    open")
           if w not in out], [])
    check("ACTIVE is there for the bar widget",
          os.path.exists(os.path.join(DEMO_ROOT, "ACTIVE")), True)
    rc, out = omacar("demo", "status",
                     env=dict(ENV, XDG_STATE_HOME=os.path.join(HOME, ".local", "state") + "//"))
    check("an XDG_STATE_HOME spelled with a trailing // names the same demo",
          ("demo world     running" in out, "demo server    http" in out), (True, True))
    check("the road cameras' saved list and pins were copied in, for no signal",
          (os.path.exists(os.path.join(DEMO_ROOT, "state", "omacar", "roadcams", "list.json")),
           os.path.exists(os.path.join(DEMO_ROOT, "config", "omarchy", "omacar-roadcams.json"))),
          (True, True))

    st, body = hreq("POST", "/api/demo/cue", '{"cue": "park"}')
    check("a park cue is taken", st, 200)
    time.sleep(3)
    try:
        with open(os.path.join(DEMO_ROOT, "state", "omacar", "demo-cue.json"),
                  encoding="utf-8") as f:
            cue = json.load(f).get("cue")
    except (OSError, ValueError):
        cue = None
    check("and waits for the world in the demo's state", cue, "park")

    asked = time.time()
    rc, out = omacar("demo", "off", timeout=120)
    check("demo off", rc, 0)
    # A PRESENTER'S demo off fades: the real server takes the quiet cue and
    # writes it for the world, and the window goes 2.5 s after the answer.
    cue, went = cue_now(), window_told_to_go()
    gap = round(went - cue[1], 2) if cue and cue[1] and went else None
    check("it asked for quiet through the real cue path: the server wrote the quiet cue",
          (bool(cue) and cue[0] == "quiet" and (cue[1] or 0) >= asked,
           "asked the demo page for quiet" in out), (True, True))
    check(f"and the window was told to go 2.5 s after it, not much later (took {gap} s)",
          gap is not None and 2.5 <= gap < 3.3, True)
    check("and says what it stopped",
          [w for w in ("the demo window", "the demo server", "the demo world", "the guard",
                       "the camera feed") if f"stopped {w}" not in out], [])
    check("nothing holds the demo's port", listening(PORT), False)
    check("nothing of the demo's is left running, not one of the feed's ffmpegs", ours_running(), [])
    check("ACTIVE is gone", os.path.exists(os.path.join(DEMO_ROOT, "ACTIVE")), False)
    check("every file outside the demo's folder is byte-identical, the real runtime "
          "folder's camera pictures included", outside_demo(), BEFORE)
    check("no systemctl, wpctl or pactl, and no hyprctl either",
          [c for c in shim_calls() if c.split()[0] != "chromium"], [])
    # Each section after this one counts its own calls from zero.
    open(SHIM_LOG, "w").close()

    head("a road-camera copy that fails leaves nothing half made, and is tried again")
    shutil.rmtree(os.path.join(DEMO_ROOT, "state", "omacar", "roadcams"), ignore_errors=True)
    os.remove(os.path.join(DEMO_ROOT, "config", "omarchy", "omacar-roadcams.json"))
    locked = os.path.join(REAL_STATE, "roadcams", "list.json")
    os.chmod(locked, 0)
    try:
        rc, out = omacar("demo", "on", timeout=180)
    finally:
        os.chmod(locked, 0o644)
    check("demo on still starts", rc, 0)
    check("and says the stills could not be copied",
          "the saved stills could not be copied" in out, True)
    state_dir = os.path.join(DEMO_ROOT, "state", "omacar")
    check("the pins are copied all the same",
          os.path.exists(os.path.join(DEMO_ROOT, "config", "omarchy", "omacar-roadcams.json")), True)
    check("with no half copy left behind, under either name",
          (os.path.exists(os.path.join(state_dir, "roadcams")),
           [n for n in os.listdir(state_dir) if n.startswith(".roadcams.copy")]), (False, []))
    rc, _ = omacar("demo", "off", timeout=120)
    rc, out = omacar("demo", "on", timeout=180)
    check("the next demo on copies them",
          (rc, os.path.exists(os.path.join(state_dir, "roadcams", "list.json")),
           "could not be copied" in out), (0, True, False))
    rc, _ = omacar("demo", "off", timeout=120)
    check("and goes cleanly", (rc, ours_running(), listening(PORT)), (0, [], False))
    open(SHIM_LOG, "w").close()

    def still_up():
        """What is left of the demo: its processes, its window, its ffmpegs
        (ours_running), and ACTIVE."""
        up = [p[1][:60] for p in ours_running()]
        if os.path.exists(os.path.join(DEMO_ROOT, "ACTIVE")):
            up.append("ACTIVE")
        return up

    def gone_within(secs):
        t0 = time.time()
        while time.time() - t0 < secs and still_up():
            time.sleep(0.2)
        return still_up(), round(time.time() - t0, 1)

    PIDS = os.path.join(DEMO_ROOT, "pids")
    GUARD_LOG = os.path.join(DEMO_ROOT, "state", "omacar", "demo-guard.log")

    def pid_in(name):
        try:
            with open(os.path.join(PIDS, name + ".pid"), encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def started_as(pid):
        """What a process was started with: its command line, and its
        environment less what every shell changes on its own (_, SHLVL)."""
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = dict(e.decode(errors="replace").split("=", 1)
                           for e in f.read().split(b"\0") if b"=" in e)
        except OSError:
            return None
        for k in ("_", "SHLVL", "OLDPWD"):
            env.pop(k, None)
        return cmdline(pid), env

    def answers():
        st, body = hreq("GET", "/.mark")
        return st == 200 and b"omacar-server" in body

    def guard_says():
        try:
            with open(GUARD_LOG, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    head("the real car moves: the guard takes the whole demo down")
    write_live(0)
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on", rc, 0)
    try:
        with open(os.path.join(DEMO_ROOT, "pids", "guard.pid"), encoding="utf-8") as f:
            guard_pid = int(f.read().strip())
    except (OSError, ValueError):
        guard_pid = None
    check("the guard is running, from guard.pid",
          bool(guard_pid) and cmdline(guard_pid) is not None
          and os.path.join(ROOT, "lib", "demoguard.py") in cmdline(guard_pid), True)
    check("with the window, world, server and camera feed up",
          all(any(w in p[1] for p in ours_running())
              for w in ("demoworld.py", "serve.py", "cams.py", "--user-data-dir=")), True)
    said_before = guard_says()
    moved = time.time()
    write_live(42)                      # the real car, fresh, at 42 km/h
    left, took = gone_within(10)
    check(f"within 10 s the window, world, server, camera feed, guard and ACTIVE are all gone "
          f"(took {took} s)", left, [])
    check("and nothing holds the demo's port", listening(PORT), False)
    # THE WINDOW AT ONCE: the owner's dashboard is under it. The guard looks
    # every 2 s, and its `demo off --now` sends no quiet cue and waits for no
    # fade, so the window has gone within 2.5 s of the moving sample.
    went = window_told_to_go()
    gap = round(went - moved, 2) if went else None
    check(f"the window was told to go within 2.5 s of the moving sample (took {gap} s)",
          gap is not None and 0 <= gap <= 2.5, True)
    cue = cue_now()
    check("and no quiet cue was sent: no fade on a moving car",
          (bool(cue) and cue[0] == "quiet" and (cue[1] or 0) >= moved,
           "asked the demo page for quiet" in guard_says()[len(said_before):]), (False, False))
    write_live(0)

    head("the marker removed by something else: the guard takes the rest down")
    # The old bar widget's "Demo Off" removes ACTIVE and knows nothing of the
    # window, the feed or the guard. The guard sees the marker go, and runs
    # `demo off` once before it leaves.
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on", rc, 0)
    said_before = guard_says()
    os.remove(os.path.join(DEMO_ROOT, "ACTIVE"))
    left, took = gone_within(10)
    check(f"within 10 s the window, world, server, camera feed and guard are all gone "
          f"(took {took} s)", left, [])
    check("and nothing holds the demo's port", listening(PORT), False)
    check("without a quiet cue: something else is already stopping it",
          "asked the demo page for quiet" in guard_says()[len(said_before):], False)
    check("no systemctl, wpctl or pactl", [c for c in shim_calls()
                                           if c.split()[0] in ("systemctl", "wpctl", "pactl")], [])
    open(SHIM_LOG, "w").close()

    head("the watchdog: a part that dies is back within 10 s, started as demo on started it")
    write_live(0)
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on", rc, 0)
    said_before = guard_says()
    for name, what in (("server", "the demo server"), ("world", "the demo world"),
                       ("cams", "the camera feed")):
        old = pid_in(name)
        before = started_as(old) if old else None
        if not before:
            bad(f"{what} was not running to be killed (pid file: {old})")
            continue
        try:
            os.kill(old, signal.SIGKILL)
        except OSError:
            pass
        t0, new = time.time(), None
        while time.time() - t0 < 12:
            p = pid_in(name)
            argv = cmdline(p) if p else None
            # Up means exec'd into its script (not the shell before it), and
            # for the server, answering.
            if p and p != old and argv and argv[1:2] == before[0][1:2] \
                    and (name != "server" or answers()):
                new = p
                break
            time.sleep(0.2)
        took = round(time.time() - t0, 1)
        check(f"{what}, killed, is back within 10 s (took {took} s)",
              bool(new) and took <= 10, True)
        check("with the command line and environment demo on gave it",
              started_as(new) if new else None, before)
    # A server that is there and says nothing (stopped, as a hung one is): the
    # guard counts it down after two unanswered looks, and `demo mend` stops
    # it, by what it is, before it starts another.
    old = pid_in("server")
    try:
        if old:
            os.kill(old, signal.SIGSTOP)
    except OSError:
        old = None
    t0, new = time.time(), None
    while old and time.time() - t0 < 25:
        p = pid_in("server")
        if p and p != old and answers():
            new = p
            break
        time.sleep(0.5)
    took = round(time.time() - t0, 1)
    check(f"a server that stops answering is stopped and started again within 20 s "
          f"(took {took} s)", (bool(new) and took <= 20, cmdline(old) if old else None),
          (True, None))
    said = guard_says()[len(said_before):]
    check("each restart is written in demo-guard.log",
          [w for w in ("the demo server", "the demo world", "the camera feed")
           if f"{w} " not in said or "starting it again" not in said], [])
    check("the demo is whole again: the window, the guard and ACTIVE are as they were",
          (bool(pid_in("guard")) and cmdline(pid_in("guard")) is not None,
           any("--user-data-dir=" in p[1] for p in ours_running()),
           os.path.exists(os.path.join(DEMO_ROOT, "ACTIVE"))), (True, True, True))

    head("demo mend: one part of a demo that is on, only when it is down")
    rc, out = omacar("demo", "mend", "world")
    check("it will not start a part that is running, and says so",
          (rc != 0, "running" in out), (True, True))
    rc, out = omacar("demo", "mend", "window")
    check("nor one it does not know", (rc, "server|world|cams" in out), (2, True))

    head("a part down while the real car moves: nothing is started again, and the demo goes")
    said_before = guard_says()
    write_live(42)                      # the real car, fresh, at 42 km/h
    world_pid = pid_in("world")
    try:
        if world_pid:
            os.kill(world_pid, signal.SIGKILL)
    except OSError:
        pass
    left, took = gone_within(10)
    check(f"within 10 s the whole demo is gone (took {took} s)", left, [])
    check("and the guard started nothing again",
          "starting it again" in guard_says()[len(said_before):], False)
    check("nothing holds the demo's port", listening(PORT), False)

    write_live(42)                      # fresh again: a sample 5 s old is a car nothing reads
    rc, out = omacar("demo", "mend", "world")
    check("demo mend with the car moving: refused, and nothing is started",
          (rc != 0, "moving" in out, ours_running()), (True, True, []))
    write_live(0)
    rc, out = omacar("demo", "mend", "world")
    check("with the demo off: refused, and nothing is started",
          (rc != 0, "not on" in out, ours_running()), (True, True, []))
    with open(os.path.join(DEMO_ROOT, "ACTIVE"), "w", encoding="utf-8"):
        pass
    rc, out = omacar("demo", "mend", "world", timeout=60)
    running_now = [p[1] for p in ours_running()]
    check("with ACTIVE there and the car parked it starts the world, and only the world",
          (rc, [r for r in running_now if "demoworld.py" not in r], len(running_now)),
          (0, [], 1))
    rc, out = omacar("demo", "off", timeout=120)
    check("and demo off takes it down", (rc, ours_running()), (0, []))
    open(SHIM_LOG, "w").close()

    head("the start lock: one start at a time, a server still starting is left alone, "
         "and the car is asked again under it")
    import fcntl
    START_LOCK = os.path.join(DEMO_ROOT, "run", "demo-start.lock")
    write_live(0)
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on", rc, 0)

    def ours_named(script):
        return [p for p in ours_running() if f"/lib/{script}" in p[1]]

    def kill_part(name):
        pid = pid_in(name)
        try:
            if pid:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        for _ in range(50):
            if not pid or cmdline(pid) is None:
                break
            time.sleep(0.1)

    def hold_lock():
        f = open(START_LOCK, "a")
        fcntl.flock(f, fcntl.LOCK_EX)
        return f

    def let_go(f):
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

    kill_part("guard")                  # nothing mends behind the test's back from here

    # A SERVER THAT IS STILL STARTING: this demo's server, run as demo on runs
    # it, but not listening for its first 3 s (a sitecustomize that sleeps), as
    # one another start has just launched. mend must not take it for a hung one.
    srv_argv, srv_env = started_as(pid_in("server")) or ([], {})
    kill_part("server")
    SLOW_SITE = os.path.join(SCRATCH, "slow-site")
    os.makedirs(SLOW_SITE, exist_ok=True)
    with open(os.path.join(SLOW_SITE, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write("import os, time\nif os.environ.get('SILO_LISTEN_AFTER'):\n"
                "    time.sleep(float(os.environ['SILO_LISTEN_AFTER']))\n")
    slow_srv = subprocess.Popen(srv_argv, env=dict(srv_env, PYTHONPATH=SLOW_SITE,
                                                   SILO_LISTEN_AFTER="3"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    DECOYS.append(slow_srv)
    with open(os.path.join(PIDS, "server.pid"), "w") as f:
        f.write(str(slow_srv.pid))
    time.sleep(0.2)
    rc, out = omacar("demo", "mend", "server", timeout=60)
    check("a server not listening yet is not taken for a hung one: mend leaves it alone",
          (rc != 0, "less than 10 s" in out, slow_srv.poll()), (True, True, None))
    up = False
    for _ in range(80):
        if answers():
            up = True
            break
        time.sleep(0.1)
    check("and it answers once it has started, still the server in the pid file",
          (up, pid_in("server") == slow_srv.pid, slow_srv.poll()), (True, True, None))

    # THE CAR, ASKED AGAIN UNDER THE LOCK: mend asks it first (parked), waits
    # for a start that holds the lock, and by then the car is moving.
    kill_part("world")
    held = hold_lock()
    mender = subprocess.Popen([OMACAR, "demo", "mend", "world"], env=ENV, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
    time.sleep(1.5)
    check("mend waits for a start that holds the lock", mender.poll(), None)
    write_live(42)
    let_go(held)
    out, _ = mender.communicate(timeout=60)
    check("the car moved while it waited: asked again under the lock, it starts nothing",
          (mender.returncode != 0, "moving" in out, ours_named("demoworld.py")), (True, True, []))
    write_live(0)

    # RACES. Three mends of a dead world at once, three times over; then demo on,
    # a second demo on and two mends at once, with the world and the guard dead.
    rounds = []
    for _ in range(3):
        kill_part("world")
        racers = [subprocess.Popen([OMACAR, "demo", "mend", "world"], env=ENV,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL) for _ in range(3)]
        rcs = sorted(p.wait(timeout=90) for p in racers)
        rounds.append((rcs, len(ours_named("demoworld.py"))))
    check("three mends at once, three times: one starts the world, and one world runs",
          rounds, [([0, 1, 1], 1)] * 3)
    kill_part("world")
    racers = [subprocess.Popen([OMACAR, "demo", *verb], env=ENV, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
              for verb in (("on",), ("on",), ("mend", "world"), ("mend", "world"))]
    for p in racers:
        p.wait(timeout=180)
    check("demo on twice and two mends at once: one world, and one guard",
          (len(ours_named("demoworld.py")), len(ours_named("demoguard.py"))), (1, 1))

    # A START THAT HANGS HOLDING THE LOCK against `demo off --now`: the window
    # goes at once; only the parts wait for the lock.
    held = hold_lock()
    t0 = time.time()
    stopper = subprocess.Popen([OMACAR, "demo", "off", "--now"], env=ENV,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL)
    went = None
    for _ in range(50):
        went = window_told_to_go()
        if went and went >= t0:
            break
        time.sleep(0.1)
    gap = round(went - t0, 2) if went and went >= t0 else None
    check(f"a hung start holding the lock: demo off --now closes the window at once "
          f"({gap} s), while it waits to stop the rest",
          (gap is not None and gap <= 1.0, stopper.poll()), (True, None))
    let_go(held)
    stopper.wait(timeout=90)
    check("and once the lock is let go, demo off stops the rest",
          (stopper.returncode, ours_running(), listening(PORT)), (0, [], False))
    if slow_srv.poll() is None:
        slow_srv.kill()
    slow_srv.wait()
    DECOYS.remove(slow_srv)
    open(SHIM_LOG, "w").close()

    head("a server that hangs does not hold up the car")
    write_live(0)
    rc, out = omacar("demo", "on", timeout=180)
    check("demo on", rc, 0)
    hung = pid_in("server")
    try:
        if hung:
            os.kill(hung, signal.SIGSTOP)
    except OSError:
        pass
    time.sleep(2.5)                     # the guard is in a look that waits 2 s on it
    said_before = guard_says()
    moved = time.time()
    write_live(42)
    went = None
    for _ in range(60):
        went = window_told_to_go()
        if went and went >= moved:
            break
        time.sleep(0.1)
    gap = round(went - moved, 2) if went and went >= moved else None
    check(f"the car moving while the guard waits on a hung server: the window goes within "
          f"2.5 s ({gap} s)", gap is not None and gap <= 2.5, True)
    cue = cue_now()
    check("with no quiet cue",
          (bool(cue) and cue[0] == "quiet" and (cue[1] or 0) >= moved,
           "asked the demo page for quiet" in guard_says()[len(said_before):]), (False, False))
    left, took = gone_within(15)
    check(f"and the whole demo goes, the stopped server too (took {took} s)", left, [])
    write_live(0)
    open(SHIM_LOG, "w").close()

    head("the guard closes the demo when the real car moves, and goes with it")
    write_live(0)
    os.makedirs(DEMO_ROOT, exist_ok=True)
    ACTIVE = os.path.join(DEMO_ROOT, "ACTIVE")
    with open(ACTIVE, "w", encoding="utf-8"):
        pass
    # Started as `demo on` starts it: inside the demo's environment, where
    # XDG_STATE_HOME is the demo's own state. A `demo off` run from there that
    # did not put the real one back would look for a demo inside the demo.
    genv = dict(ENV, OMACAR_DEMO_GUARD_EVERY="0.2",
                XDG_STATE_HOME=os.path.join(DEMO_ROOT, "state"),
                XDG_CONFIG_HOME=os.path.join(DEMO_ROOT, "config"),
                OMACAR_STATE=os.path.join(DEMO_ROOT, "state", "omacar"))
    g = subprocess.Popen([PY, GUARD, REAL_LIVE, DEMO_ROOT], env=genv,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(1.5)
    check("parked: it keeps watching", g.poll(), None)
    os.remove(ACTIVE)
    try:
        g.wait(timeout=10)
    except subprocess.TimeoutExpired:
        g.kill()
    check("the demo gone (no ACTIVE): it runs demo off once and goes, stopping nothing",
          (g.returncode, shim_calls()), (0, []))
    with open(ACTIVE, "w", encoding="utf-8"):
        pass
    g = subprocess.Popen([PY, GUARD, REAL_LIVE, DEMO_ROOT], env=genv,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(1.0)
    write_live(42)
    try:
        g.wait(timeout=60)
    except subprocess.TimeoutExpired:
        g.kill()
    err = g.stderr.read().decode(errors="replace")
    check("moving: it runs `omacar demo off` for this demo, and ACTIVE is gone",
          (g.returncode, os.path.exists(ACTIVE)), (0, False))
    check("and says so", "moving" in err, True)
    check("the demo off it ran was this HOME's, not one nested inside the demo",
          os.path.exists(os.path.join(DEMO_ROOT, "state", "omacar-demo")), False)
    check("still no systemctl, wpctl or pactl", shim_calls(), [])
    write_live(0)

    head("a guard whose demo off fails tries again, and never leaves the demo up")
    import contextlib
    import io
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import demoguard  # noqa: E402
    groot = os.path.join(SCRATCH, "guard-unit", "omacar-demo")
    os.makedirs(groot)
    glive = os.path.join(SCRATCH, "guard-unit", "live.json")
    gactive = os.path.join(groot, "ACTIVE")

    def moving_at(t):
        with open(glive, "w", encoding="utf-8") as f:
            json.dump({"t": t, "values": {"SPEED": 60}}, f)

    clock = [1000.0]
    moving_at(clock[0])
    open(gactive, "w").close()
    calls = []

    def off_fails_once(_root):
        calls.append(1)
        return 1 if len(calls) == 1 else 0

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        got = demoguard.watch(glive, groot, every=2, off=off_fails_once,
                              clock=lambda: clock[0], sleep=lambda s: None)
    check("its first demo off fails, the second works, and only then is it done",
          (got, len(calls)), ("moving", 2))
    check("and it says the first failed", "failed (1), try 1" in err.getvalue(), True)

    # A demo off that never works: five tries two seconds apart, then one every
    # 30 s while the car moves, and it leaves only when the demo has gone.
    calls.clear()
    ticks = [0]

    def always_fails(_root):
        calls.append(clock[0])
        return 1

    def a_tick(secs):
        ticks[0] += 1
        clock[0] += secs
        moving_at(clock[0])                    # the car keeps moving
        if ticks[0] == 40:                     # 80 s in, the demo goes some other way
            os.remove(gactive)

    clock[0] = 1000.0
    moving_at(clock[0])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        got = demoguard.watch(glive, groot, every=2, off=always_fails,
                              clock=lambda: clock[0], sleep=a_tick)
    check("it never gives up and leaves while the demo stands: it goes when ACTIVE does",
          (got, ticks[0]), ("gone", 40))
    check("five tries two seconds apart, then one every 30 s, and one more as it goes",
          [round(t - 1000) for t in calls], [0, 2, 4, 6, 8, 38, 68, 80])
    check("and it says it is still watching",
          "still up after 5 tries" in err.getvalue(), True)

    calls.clear()
    open(gactive, "w").close()
    os.remove(gactive)
    got = demoguard.watch(glive, groot, every=2, off=lambda r: calls.append(r) or 0,
                          clock=lambda: clock[0], sleep=lambda s: None)
    check("ACTIVE gone some other way: it runs demo off once, then goes",
          (got, calls), ("gone", [groot]))

    head("the watchdog in the guard: what it starts again, when, and when never")
    wroot = os.path.join(SCRATCH, "watchdog-unit", "omacar-demo")
    os.makedirs(wroot)
    wlive = os.path.join(SCRATCH, "watchdog-unit", "live.json")
    wactive = os.path.join(wroot, "ACTIVE")
    ALL_UP = {"server": True, "world": True, "cams": True}
    DOG_T = [0]                           # the fake clock's t, for fakes that need it

    def dog_run(secs, up, moving=lambda t: False, mend_rc=lambda t, name: 0):
        """The guard on a fake clock, a tick every 2 s from t=0 until t=secs,
        when ACTIVE goes. up(t) is what the watchdog finds at t; a part's mend
        answers mend_rc(t, name): an exit status, or something still running
        (with a poll(), as a Popen has). Returns what watch returned, each mend
        as (t, part), each demo off's t, and what the guard wrote."""
        clock = [1000.0]
        mends, offs = [], []
        open(wactive, "w").close()

        def at():
            return round(clock[0] - 1000)

        def write():
            DOG_T[0] = at()
            with open(wlive, "w", encoding="utf-8") as f:
                json.dump({"t": clock[0], "values": {"SPEED": 60 if moving(at()) else 0}}, f)

        def tick(s):
            clock[0] += s
            write()
            if at() >= secs and os.path.exists(wactive):
                os.remove(wactive)

        def mend(name):
            mends.append((at(), name))
            return mend_rc(at(), name)

        write()
        dog = demoguard.Watchdog(wroot, probe=lambda: dict(up(at())), mend=mend)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = demoguard.watch(wlive, wroot, every=2, off=lambda r: offs.append(at()) or 0,
                                  clock=lambda: clock[0], sleep=tick, parts=dog)
        return got, mends, offs, err.getvalue()

    got, mends, offs, said = dog_run(60, lambda t: ALL_UP)
    check("everything up: it starts nothing, and goes when ACTIVE does",
          (got, mends, offs), ("gone", [], [60]))

    got, mends, offs, said = dog_run(240, lambda t: dict(ALL_UP, world=t < 10))
    check("a world that stays down: started again at once, then 2, 4, 8 and 16 s apart, "
          "then once a minute", [t for t, _ in mends], [10, 12, 16, 24, 40, 100, 160, 220])
    check("only the world", {n for _, n in mends}, {"world"})
    check("each one written down",
          (said.count("the demo world is not running; starting it again"), "is back" in said),
          (8, True))

    got, mends, offs, said = dog_run(60, lambda t: dict(ALL_UP, server=t != 10))
    check("a server that misses one /.mark is not started again", mends, [])
    got, mends, offs, said = dog_run(60, lambda t: dict(ALL_UP, server=t not in (20, 22)))
    check("one that misses two in a row is", mends, [(22, "server")])

    got, mends, offs, said = dog_run(60, lambda t: dict(ALL_UP, world=t < 10),
                                     moving=lambda t: t >= 10)
    check("the world down while the real car moves: nothing started, and demo off",
          (got, mends, offs), ("moving", [], [10]))

    got, mends, offs, said = dog_run(10, lambda t: dict(ALL_UP, world=t < 10))
    check("the world down as ACTIVE goes: nothing started", (got, mends), ("gone", []))

    def gone_mid_tick(t):
        if t == 10 and os.path.exists(wactive):
            os.remove(wactive)            # `demo off`, between the look and the start
        return dict(ALL_UP, world=t < 10)
    got, mends, offs, said = dog_run(60, gone_mid_tick)
    check("ACTIVE gone between finding a part down and starting it: nothing started",
          (got, mends), ("gone", []))

    got, mends, offs, said = dog_run(60, lambda t: dict(ALL_UP, cams=False))
    check("a camera feed never seen running (a build without one) is left alone", mends, [])
    got, mends, offs, said = dog_run(60, lambda t: dict(ALL_UP, cams=t < 10 or t >= 12))
    check("one that was running and died is started again", mends, [(10, "cams")])

    got, mends, offs, said = dog_run(
        140, lambda t: dict(ALL_UP, world=not (10 <= t <= 40 or t >= 120)))
    check("a minute up and its backoff starts again from 2 s",
          [t for t, _ in mends], [10, 12, 16, 24, 40, 120, 122, 126, 134])

    class Starting:
        """A `demo mend` still under way until the fake clock reaches `until`."""
        def __init__(self, until):
            self.until = until

        def poll(self):
            return 0 if DOG_T[0] >= self.until else None

    got, mends, offs, said = dog_run(30, lambda t: dict(ALL_UP, world=t < 10),
                                     mend_rc=lambda t, name: Starting(20) if t == 10 else 0)
    check("while one start is under way nothing else is started",
          [t for t, _ in mends][:2], [10, 20])

    def broken_at_4(t):
        if t == 4:
            raise RuntimeError("the look itself broke")
        return dict(ALL_UP, world=t < 10)
    got, mends, offs, said = dog_run(30, broken_at_4)
    check("a look at the parts that raises is written down, and the guard watches on",
          (got, "the watchdog's look failed (RuntimeError: the look itself broke)" in said,
           mends[:1]), ("gone", True, [(10, "world")]))

    # A LOOK THAT TAKES 1.5 s (a server that hangs holds it for 2): the car is
    # asked again straight after it, and the guard sleeps only what is left of
    # its 2 s. The car starts moving during the look that ends at t=7.5.
    slow = [1000.0]
    slow_sleeps, slow_offs = [], []

    def slow_live():
        with open(wlive, "w", encoding="utf-8") as f:
            json.dump({"t": slow[0], "values": {"SPEED": 60 if slow[0] - 1000 >= 7 else 0}}, f)

    def slow_probe():
        slow[0] += 1.5
        slow_live()
        return dict(ALL_UP)

    def slow_sleep(secs):
        slow_sleeps.append(round(secs, 2))
        slow[0] += secs
        slow_live()

    open(wactive, "w").close()
    slow_live()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        got = demoguard.watch(wlive, wroot, every=2, clock=lambda: slow[0], sleep=slow_sleep,
                              off=lambda r: slow_offs.append(round(slow[0] - 1000, 2)) or 0,
                              parts=demoguard.Watchdog(wroot, probe=slow_probe,
                                                       mend=lambda n: 0))
    check("a slow look does not hold up the car: demo off the moment it ends, and each "
          "sleep is only what is left of 2 s",
          (got, slow_offs, slow_sleeps), ("moving", [7.5], [0.5, 0.5, 0.5]))
    if os.path.exists(wactive):
        os.remove(wactive)

    head("demo off stops only what is the demo's")
    # Look-alikes in the demo's pid files, each started with the demo's own
    # state folder in its environment, so what else they are is all that tells
    # them apart. From this checkout: its own script paths and the demo's
    # words, but the script is not what their interpreter runs. A check that
    # looked for the path anywhere on the command line would stop them.
    sleeper = "import time; time.sleep(600)"
    lib = os.path.join(ROOT, "lib")
    same_checkout = {
        "server": [PY, "-c", sleeper, os.path.join(lib, "serve.py"), "7560", "share", "--demo"],
        "cams": [PY, "-c", sleeper, os.path.join(lib, "cams.py"), "run", "demo"],
        "world": [PY, "-c", sleeper, os.path.join(lib, "demoworld.py"), "run"],
        "guard": [PY, "-c", sleeper, os.path.join(lib, "demoguard.py"), REAL_LIVE, DEMO_ROOT],
    }
    os.makedirs(os.path.join(DEMO_ROOT, "pids"), exist_ok=True)
    for name, argv in same_checkout.items():
        proc = spawn_decoy(*argv, env=DEMO_STATE_ENV)
        DECOYS.append(proc)
        with open(os.path.join(DEMO_ROOT, "pids", f"{name}.pid"), "w") as f:
            f.write(str(proc.pid))
    # And two windows that are not this demo's: another HOME's, and a profile
    # whose name only starts like this one's.
    DECOYS.append(spawn_decoy(os.path.join(SHIMS, "chromium"),
                              f"--user-data-dir={SCRATCH}/else/omacar-demo/browser"))
    DECOYS.append(spawn_decoy(os.path.join(SHIMS, "chromium"),
                              f"--user-data-dir={DEMO_ROOT}/browser-not"))
    time.sleep(0.5)
    open(SHIM_LOG, "w").close()
    check("the watchdog counts none of them as a running world or camera feed",
          [n for n in ("world", "cams") if demoguard.part_alive(DEMO_ROOT, n)], [])
    with open(ACTIVE, "w", encoding="utf-8"):
        pass
    rc, out = omacar("demo", "off")
    check("it runs cleanly with nothing of its own to stop", rc, 0)
    check("and says nothing was running", "nothing" in out, True)
    check("every look-alike is still alive", [d.poll() for d in DECOYS], [None] * len(DECOYS))
    check("ACTIVE is gone", os.path.exists(ACTIVE), False)
    check("the pid files that named nothing of the demo's are gone",
          sorted(os.listdir(os.path.join(DEMO_ROOT, "pids"))), [])
    for d in DECOYS:
        d.kill()
        d.wait()
    DECOYS.clear()

    # A REAL server from this checkout, running its script, in server.pid,
    # started with the demo's state folder: the live server, without --demo.
    # Only the demo's words tell it apart. And another checkout's world
    # running its script with the word, but not with the demo's state folder.
    live_share = os.path.join(SCRATCH, "live-share")
    os.makedirs(live_share)
    live_port = free_port()
    live_srv = spawn_decoy(PY, os.path.join(ROOT, "lib", "serve.py"), str(live_port), live_share,
                           env=dict(ENV, OMACAR_STATE=os.path.join(DEMO_ROOT, "state", "omacar")))
    DECOYS.append(live_srv)
    stranger = os.path.join(SCRATCH, "stranger checkout")
    os.makedirs(os.path.join(stranger, "lib"))
    with open(os.path.join(stranger, "lib", "demoworld.py"), "w", encoding="utf-8") as f:
        f.write("import time\ntime.sleep(600)\n")
    no_env = spawn_decoy(PY, os.path.join(stranger, "lib", "demoworld.py"), "run")
    DECOYS.append(no_env)
    for _ in range(100):
        if listening(live_port):
            break
        time.sleep(0.1)
    for name, proc in (("server", live_srv), ("world", no_env)):
        with open(os.path.join(DEMO_ROOT, "pids", f"{name}.pid"), "w") as f:
            f.write(str(proc.pid))
    rc, out = omacar("demo", "status")
    check("status counts neither as the demo's",
          ("demo world     not running" in out, "demo server    not running" in out), (True, True))
    check("nor does the watchdog count that world", demoguard.part_alive(DEMO_ROOT, "world"), False)
    rc, out = omacar("demo", "off")
    check("demo off leaves the live server running, and answering",
          (rc, live_srv.poll(), listening(live_port)), (0, None, True))
    check("and the other checkout's world that is not this demo's",
          (no_env.poll(), "stranger" in out), (None, False))
    for d in DECOYS:
        d.kill()
        d.wait()
    DECOYS.clear()

    head("another checkout's demo is this demo: demo off stops it, and says where it ran")
    # The tablet's case: the demo runs from a worktree while the `omacar` on
    # PATH is the live checkout, and the guard runs `demo off` from its own.
    # Here "the other checkout" is a folder whose lib/demoworld.py and
    # lib/serve.py only sleep, run as the demo runs them, with the demo's state
    # folder in their environment and their pids in this demo's pid files.
    other = os.path.join(SCRATCH, "other checkout")
    os.makedirs(os.path.join(other, "lib"))
    for n in ("demoworld.py", "serve.py"):
        with open(os.path.join(other, "lib", n), "w", encoding="utf-8") as f:
            f.write("import time\ntime.sleep(600)\n")
    # Its world shrugs off SIGTERM, so only the SIGKILL after five seconds ends
    # it: the "stopped" must be true of another checkout's process too.
    with open(os.path.join(other, "lib", "demoworld.py"), "w", encoding="utf-8") as f:
        f.write("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(600)\n")
    OTHER = {
        "world": spawn_decoy(PY, os.path.join(other, "lib", "demoworld.py"), "run",
                             env=DEMO_STATE_ENV),
        "server": spawn_decoy(PY, os.path.join(other, "lib", "serve.py"), str(PORT),
                              os.path.join(other, "share"), "--demo",
                              os.path.join(other, "demo"), env=DEMO_STATE_ENV),
    }
    DECOYS.extend(OTHER.values())
    for name, proc in OTHER.items():
        with open(os.path.join(DEMO_ROOT, "pids", f"{name}.pid"), "w") as f:
            f.write(str(proc.pid))
    # Its window, on this demo's own profile.
    window = spawn_decoy(os.path.join(SHIMS, "chromium"), f"--user-data-dir={DEMO_ROOT}/browser")
    DECOYS.append(window)
    with open(ACTIVE, "w", encoding="utf-8"):
        pass
    time.sleep(0.5)
    open(SHIM_LOG, "w").close()

    check("the watchdog counts that checkout's world as the demo's, as demo off does",
          demoguard.part_alive(DEMO_ROOT, "world"), True)
    rc, out = omacar("demo", "status")
    check("status says where it runs",
          (f"running from {other} (pid {OTHER['world'].pid})" in out,
           f"running from {other} (pid {OTHER['server'].pid})" in out), (True, True))
    write_live(0)
    rc, out = omacar("demo", "on")
    check("demo on will not start a second demo beside it, and says where it runs",
          (rc != 0, "another checkout" in out, f"from {other}" in out), (True, True, True))
    check("and how to stop it: this checkout's own demo off, by its full path",
          f"stop it first:  {ROOT}/bin/omacar demo off" in out, True)
    check("it started nothing: no second window, no systemctl, wpctl or pactl",
          [c for c in shim_calls() if not c.startswith("chromium --user-data-dir=")], [])
    check("no demo process of this checkout's is running",
          [p for p in ours_running() if p[0] != window.pid], [])
    check("the other one's are untouched", [p.poll() for p in OTHER.values()], [None, None])

    rc, out = omacar("demo", "off")
    for proc in OTHER.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    try:
        window.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    check("demo off stops it, its world (which ignores SIGTERM) with a SIGKILL",
          (rc, [p.poll() for p in OTHER.values()]), (0, [-9, -15]))
    check("and names the checkout it was started from",
          (f"stopped the demo world, run from {other}" in out,
           f"stopped the demo server, run from {other}" in out,
           f"the demo was started from {other}" in out), (True, True, True))
    check("the window and ACTIVE go too, as they always do",
          (window.poll() is not None, "stopped the demo window" in out, os.path.exists(ACTIVE)),
          (True, True, False))
    check("its dead pid files are cleared", sorted(os.listdir(os.path.join(DEMO_ROOT, "pids"))), [])
    check("no systemctl, wpctl or pactl throughout",
          [c for c in shim_calls() if c.split()[0] in ("systemctl", "wpctl", "pactl")], [])
    for d in DECOYS:                   # whatever demo off failed to stop
        if d.poll() is None:
            d.kill()
            d.wait()
    DECOYS.clear()

    head("demo off asks the page for quiet before it closes the window, and gives it 2.5 s")
    # A demo server that writes down every POST and its time (and answers 400,
    # as the real one does to a cue it does not take), from a checkout of its
    # own, run as the demo runs its server; and a window that writes down when
    # it was told to go.
    QUIET = os.path.join(SCRATCH, "quiet checkout")
    os.makedirs(os.path.join(QUIET, "lib"))
    POSTS = os.path.join(SCRATCH, "quiet-posts.log")
    TERMS = os.path.join(SCRATCH, "quiet-window.log")
    with open(os.path.join(QUIET, "lib", "serve.py"), "w", encoding="utf-8") as f:
        f.write("""import http.server, json, os, sys, time
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "13"); self.end_headers()
        self.wfile.write(b"omacar-server")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        with open(os.environ["POSTS"], "a") as f:
            f.write(json.dumps({"t": time.time(), "path": self.path, "body": body}) + "\\n")
        if os.environ.get("HANG"):
            time.sleep(5)
        out = b'{"error": "not that cue"}'
        self.send_response(400); self.send_header("Content-Length", str(len(out)))
        self.end_headers(); self.wfile.write(out)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
""")
    WINDOW_REC = os.path.join(SCRATCH, "window-rec.py")
    with open(WINDOW_REC, "w", encoding="utf-8") as f:
        f.write(f"""import signal, sys, time
def term(*_):
    with open({TERMS!r}, "a") as f:
        f.write(str(time.time()) + "\\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
time.sleep(600)
""")

    QUIET_MOVED = [None]

    def quiet_run(server_env=None, in_pid_file=True, during=None):
        """demo off over the recording server and window; what each wrote down.
        during(), if given, is called the moment the server has the quiet cue,
        while demo off waits for the fade; its time goes in QUIET_MOVED."""
        for p in (POSTS, TERMS):
            if os.path.exists(p):
                os.remove(p)
        srv = spawn_decoy(PY, os.path.join(QUIET, "lib", "serve.py"), str(PORT),
                          os.path.join(QUIET, "share"), "--demo", os.path.join(QUIET, "demo"),
                          env=server_env)
        win = spawn_decoy(PY, WINDOW_REC, f"--user-data-dir={DEMO_ROOT}/browser")
        DECOYS.extend([srv, win])
        if in_pid_file:
            with open(os.path.join(DEMO_ROOT, "pids", "server.pid"), "w") as f:
                f.write(str(srv.pid))
        with open(ACTIVE, "w", encoding="utf-8"):
            pass
        for _ in range(100):
            if listening(PORT):
                break
            time.sleep(0.1)
        time.sleep(0.3)
        QUIET_MOVED[0] = None
        if during is None:
            rc, out = omacar("demo", "off")
        else:
            proc = subprocess.Popen([OMACAR, "demo", "off"], env=ENV, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
            for _ in range(100):
                if os.path.exists(POSTS) and os.path.getsize(POSTS):
                    break
                time.sleep(0.05)
            QUIET_MOVED[0] = time.time()
            during()
            out, _ = proc.communicate(timeout=60)
            rc = proc.returncode
        try:
            with open(POSTS, encoding="utf-8") as f:
                posts = [json.loads(ln) for ln in f if ln.strip()]
        except OSError:
            posts = []
        try:
            with open(TERMS, encoding="utf-8") as f:
                terms = [float(ln) for ln in f if ln.strip()]
        except OSError:
            terms = []
        return rc, out, srv, win, posts, terms

    def ended(proc):
        try:
            return proc.wait(timeout=10) is not None
        except subprocess.TimeoutExpired:
            return False

    rc, out, srv, win, posts, terms = quiet_run(dict(DEMO_STATE_ENV, POSTS=POSTS))
    check("demo off sends the demo's server one quiet cue",
          [(p["path"], json.loads(p["body"] or "null")) for p in posts],
          [("/api/demo/cue", {"cue": "quiet"})])
    gap = round(terms[0] - posts[0]["t"], 2) if posts and terms else None
    # 2.5 s, pinned from both sides: the page starts its fade 0.30-0.45 s after
    # the ask and pauses the radio 1.4-1.7 s after it (measured), so less cuts
    # the music, and more only keeps a closing demo on the screen.
    check(f"and closes the window 2.5 s after it was answered, not sooner and not much "
          f"later (took {gap} s)", gap is not None and 2.5 <= gap < 3.3, True)
    check("then stops the rest as ever",
          (rc, ended(win), ended(srv)), (0, True, True))

    rc, out, srv, win, posts, terms = quiet_run(dict(DEMO_STATE_ENV, POSTS=POSTS, HANG="1"))
    gap = round(terms[0] - posts[0]["t"], 2) if posts and terms else None
    check(f"a server that does not answer within 1 s: it asks, and goes on at once "
          f"(window closed {gap} s after asking)", gap is not None and gap < 2.0, True)
    check("and still stops everything", (rc, ended(win), ended(srv)), (0, True, True))

    rc, out, srv, win, posts, terms = quiet_run(dict(ENV, POSTS=POSTS), in_pid_file=False)
    check("a server on the port that is not the demo's is asked nothing, and left running",
          (rc, posts, ended(win), srv.poll()), (0, [], True, None))
    srv.kill()
    srv.wait()

    # NEVER A FADE ON A MOVING CAR. A presenter's demo off stops the guard
    # before it asks for quiet, so it asks the real car itself: before the ask,
    # and every 0.25 s of the wait.
    write_live(42)
    rc, out, srv, win, posts, terms = quiet_run(dict(DEMO_STATE_ENV, POSTS=POSTS))
    check("the car moving before demo off asks: no quiet cue, and the window goes at once",
          (rc, posts, bool(terms), "with no fade" in out, ended(srv)), (0, [], True, True, True))
    write_live(0)
    rc, out, srv, win, posts, terms = quiet_run(dict(DEMO_STATE_ENV, POSTS=POSTS),
                                                during=lambda: write_live(42))
    moved = QUIET_MOVED[0]
    after_move = round(terms[0] - moved, 2) if terms and moved else None
    after_ask = round(terms[0] - posts[0]["t"], 2) if terms and posts else None
    check(f"the car moving during the fade: the window goes within 0.8 s of it "
          f"({after_move} s), not 2.5 s after the ask ({after_ask} s)",
          (after_move is not None and after_move <= 0.8,
           after_ask is not None and after_ask < 2.0), (True, True))
    check("and demo off says why, and still stops everything",
          (rc, "before the fade was done" in out, ended(win), ended(srv)), (0, True, True, True))
    write_live(0)
    for d in DECOYS:
        if d.poll() is None:
            d.kill()
            d.wait()
    DECOYS.clear()
    for n in os.listdir(os.path.join(DEMO_ROOT, "pids")):
        os.remove(os.path.join(DEMO_ROOT, "pids", n))

    head("demo cache writes the demo's own panel rollup, never the real one")
    BEFORE = outside_demo()
    rc, out = omacar("demo", "cache", timeout=120)
    demo_card = os.path.join(DEMO_ROOT, "state", "omarchy", "liquid-glass-car.json")
    check("it writes the demo's", (rc, os.path.exists(demo_card)), (0, True))
    check("and every real file is byte-identical, the real rollup included",
          outside_demo(), BEFORE)
    probe = subprocess.run(
        [PY, "-c", "import sys; sys.path.insert(0, %r); import card; print(card.CACHE)"
         % os.path.join(ROOT, "lib")],
        env=dict(ENV, XDG_STATE_HOME=os.path.join(SCRATCH, "elsewhere")),
        capture_output=True, text=True, timeout=30)
    check("card.py follows XDG_STATE_HOME", probe.stdout.strip(),
          os.path.join(SCRATCH, "elsewhere", "omarchy", "liquid-glass-car.json"))
    probe = subprocess.run(
        [PY, "-c", "import sys; sys.path.insert(0, %r); import card; print(card.CACHE)"
         % os.path.join(ROOT, "lib")], env=ENV, capture_output=True, text=True, timeout=30)
    check("and without it, it is where the panel has always read it",
          probe.stdout.strip(),
          os.path.join(HOME, ".local", "state", "omarchy", "liquid-glass-car.json"))

    head("the other verbs")
    rc, out = omacar("demo", "status")
    check("status answers, and says the server is not running",
          (rc, "not running" in out), (0, True))
    if not os.path.exists(os.path.join(ROOT, "lib", "democheck.py")):
        rc, out = omacar("demo", "check")
        check("check says it has not arrived yet, and is not a failure",
              (rc, "the check arrives with Task 8" in out), (0, True))
    rc, out = omacar("demo", "off", "--soon")
    check("demo off takes --now and nothing else", (rc, "demo off [--now]" in out), (2, True))
    rc, out = omacar("help")
    check("help names on, off, check, tour, video, mend and trash",
          "omacar demo on|off|check|tour|video|mend|trash" in out, True)
    check("no systemctl, wpctl or pactl, in the whole run",
          [c for c in shim_calls() if c.split()[0] in ("systemctl", "wpctl", "pactl")], [])
finally:
    for d in DECOYS:
        if d.poll() is None:
            d.kill()
            d.wait()
    # Anything of ours left behind is a failure, and is stopped here either way.
    left = ours_running() if os.path.isdir("/proc") else []
    for pid, _ in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if left:
        bad(f"left running: {left}")
    shutil.rmtree(SCRATCH, ignore_errors=True)

print(f"\n  {'all passed' if not fails else f'{fails} failed'}\n")
sys.exit(1 if fails else 0)
