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
  `demo off` stops only the demo's own processes, found by what they are and
    never by a pid alone or a port;
  `demo cache` writes the demo's own panel rollup, never the real one;
  and none of it writes a byte outside the demo's folder, or runs systemctl,
    wpctl or pactl, which are shims here that write down every call.

Linux only: bin/omacar is bash on Linux (setsid, /proc, ss).
"""

import hashlib
import json
import os
import shutil
import signal
import socket
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


def write_live(speed, age=0.0):
    with open(REAL_LIVE, "w", encoding="utf-8") as f:
        json.dump({"connected": True, "t": time.time() - age,
                   "values": {"SPEED": speed, "RPM": 900}}, f)


def outside_demo():
    """sha256 of every file under HOME that is not in the demo's folder."""
    out = {}
    for d, dirs, files in os.walk(HOME):
        if os.path.realpath(d).startswith(os.path.realpath(DEMO_ROOT)):
            dirs[:] = []
            continue
        for n in files:
            p = os.path.join(d, n)
            if os.path.islink(p):
                out[os.path.relpath(p, HOME)] = "link:" + os.readlink(p)
                continue
            with open(p, "rb") as f:
                out[os.path.relpath(p, HOME)] = hashlib.sha256(f.read()).hexdigest()
    return out


# ---- PATH shims: each writes down that it was called ------------------------

for name in ("systemctl", "wpctl", "pactl", "hyprctl"):
    p = os.path.join(SHIMS, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\necho "{name} $*" >> "{SHIM_LOG}"\nexit 0\n')
    os.chmod(p, 0o755)
# Chromium stays up until it is told to go, as the real one does, and keeps its
# arguments on its command line, which is what `demo off` finds it by.
with open(os.path.join(SHIMS, "chromium"), "w", encoding="utf-8") as f:
    f.write(f"""#!{PY}
import sys, time
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
ENV.update(HOME=HOME, PATH=SHIMS + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
           XDG_RUNTIME_DIR=RUNTIME, WAYLAND_DISPLAY="wayland-test",
           OMACAR_DEMO_PORT=str(PORT), OMACAR_DEMO_MUTE="1")


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
    checkout's scripts started with this scratch HOME, or a browser on this
    demo's profile."""
    out = []
    scripts = [os.path.join(ROOT, "lib", n)
               for n in ("serve.py", "demoworld.py", "demoguard.py", "cams.py")]
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid():
            continue
        args = cmdline(p) or []
        if (any(a in scripts for a in args) and environ_has(p, HOME)) \
                or f"--user-data-dir={DEMO_ROOT}/browser" in args:
            out.append((int(p), " ".join(args)[:120]))
    return out


def spawn_decoy(*argv):
    return subprocess.Popen(list(argv), stdout=subprocess.DEVNULL,
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
    check("the demo gone (no ACTIVE): it goes too, having stopped nothing",
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

    head("demo off stops only what is the demo's")
    # Three things that look like demo processes and are not this demo's: a
    # server from another checkout, another HOME's demo browser, and a process
    # whose pid sits in this demo's pid file although it is not a server.
    other = os.path.join(SCRATCH, "other")
    os.makedirs(os.path.join(other, "lib"))
    with open(os.path.join(other, "lib", "serve.py"), "w", encoding="utf-8") as f:
        f.write("import time\ntime.sleep(600)\n")
    DECOYS.append(spawn_decoy(PY, os.path.join(other, "lib", "serve.py"), str(PORT),
                              "share", "--demo", "demo"))
    DECOYS.append(spawn_decoy(os.path.join(SHIMS, "chromium"),
                              f"--user-data-dir={other}/omacar-demo/browser"))
    DECOYS.append(spawn_decoy(os.path.join(SHIMS, "chromium"),
                              f"--user-data-dir={DEMO_ROOT}/browser-not"))
    DECOYS.append(spawn_decoy("sleep", "600"))
    os.makedirs(os.path.join(DEMO_ROOT, "pids"), exist_ok=True)
    for name in ("server", "world", "guard", "cams"):
        with open(os.path.join(DEMO_ROOT, "pids", f"{name}.pid"), "w") as f:
            f.write(str(DECOYS[-1].pid))
    time.sleep(0.5)
    open(SHIM_LOG, "w").close()
    rc, out = omacar("demo", "off")
    check("it runs cleanly with nothing of its own to stop", rc, 0)
    check("and says nothing was running", "nothing" in out, True)
    check("every decoy is still alive", [d.poll() for d in DECOYS], [None] * len(DECOYS))
    check("ACTIVE is gone", os.path.exists(ACTIVE), False)
    check("the stale pid files are gone",
          sorted(os.listdir(os.path.join(DEMO_ROOT, "pids"))), [])

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
    rc, out = omacar("help")
    check("help names on, off, check, tour and trash",
          "omacar demo on|off|check|tour|trash" in out, True)
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
