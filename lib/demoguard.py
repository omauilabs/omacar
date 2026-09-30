#!/usr/bin/env python3
"""The demo's guard: the demo closes itself the moment the real car moves.

    demoguard.py LIVE_JSON DEMO_ROOT    watch until the demo is gone
    demoguard.py --state LIVE_JSON      print "moving" or "parked", and exit 0

`omacar demo on` starts it with the REAL live.json (read before the demo's
environment moves OMACAR_STATE) and the demo's folder. Every 2 s it reads that
file, read-only. While the demo is on (DEMO_ROOT/ACTIVE exists) and the real
car is moving, it runs `omacar demo off --now`, and exits once that has
worked; one that fails is tried again (TRIES, LATER), and the guard never
leaves while the demo is still up. When ACTIVE goes, however that happened, it
runs `omacar demo off --now` once more and exits. --now, because neither is a
presenter's goodbye: the window comes down at once, without the music's fade.

MOVING MEANS A FRESH SAMPLE ABOVE 3 KM/H. Fresh is `t` within 5 s of now: the
daemon rewrites live.json several times a second while it is talking to the
car, so an older file is a daemon that has stopped, and a car nothing is
reading counts as parked. That is the venue: no adapter, no fresh live data.
Above 3 km/h, so a car creeping across a car park is not a car being driven,
and a speed that is not a number is not a speed.

`demo on` asks the same question with --state before it starts anything, so
the rule that keeps the demo off a moving car's screen is written once.

IT IS ALSO THE DEMO'S WATCHDOG (spec §6: "the server's watchdog restarts it";
until this, nothing did). On each look while the car is parked and the demo is
on, it checks that the demo server answers /.mark (2 s, and two misses in a
row before it counts as down), and that the world and the camera feed are
alive, each by the same identity checks `demo off` uses (its script, its words,
the demo's state folder in its environment). One that is down is started again
by `omacar demo mend <part>`, which starts only that part, exactly as `demo on`
did. Again at once, then no sooner than 2, 4, 8 and 16 s after the last try,
then at most once a minute; a part up for a minute starts from 2 s again. Every
try and what came of it is written to demo-guard.log (this process's stderr).
Nothing is ever started again while the real car is moving or once ACTIVE has
gone: both are asked again just before each start, and `demo mend` asks them a
third time itself. A camera feed this guard has never seen running (a build or
a machine without one) is left alone.
"""

import http.client
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FRESH_SECS = 5.0
MOVING_KPH = 3.0
# How often it looks. OMACAR_DEMO_GUARD_EVERY is for the tests.
EVERY = float(os.environ.get("OMACAR_DEMO_GUARD_EVERY") or 2.0)


def state(live_json, now=None):
    """"moving" or "parked", from the real live.json. Never raises: a file that
    is missing, unreadable, half-written or strange is a car nothing is
    reporting, which is parked."""
    now = time.time() if now is None else now
    try:
        with open(live_json, encoding="utf-8") as f:
            snap = json.load(f)
        t = float(snap.get("t"))
        speed = (snap.get("values") or {}).get("SPEED")
    except Exception:                                          # noqa: BLE001
        return "parked"
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return "parked"
    if abs(now - t) <= FRESH_SECS and speed > MOVING_KPH:
        return "moving"
    return "parked"


def omacar_env(root):
    """The environment to run `omacar demo ...` in, for the demo in `root`.

    The guard runs inside the demo's environment, where XDG_STATE_HOME is the
    demo's own state. bin/omacar finds the demo at $XDG_STATE_HOME/omacar-demo,
    so run as it is, `demo off` would look for a demo inside the demo and stop
    nothing. The real XDG_STATE_HOME is the folder the demo sits in. The rest
    is the environment `demo on` started this guard with, which is the one it
    started the demo's parts with too: so a part `demo mend` starts gets it."""
    env = dict(os.environ, XDG_STATE_HOME=os.path.dirname(os.path.abspath(root)))
    for name in ("XDG_CONFIG_HOME", "OMACAR_STATE", "OMACAR_VIDEOS", "OMACAR_PORT"):
        env.pop(name, None)
    return env


def stop_demo(root):
    """`omacar demo off --now`, for the demo in `root`.

    --now: the window goes at once, with no quiet cue and no wait for the
    music's fade. The guard stops the demo for a car that is moving, with the
    owner's dashboard under the window, or because ACTIVE went, when something
    else is already stopping it; neither is a presenter's goodbye."""
    return subprocess.run([os.path.join(ROOT, "bin", "omacar"), "demo", "off", "--now"],
                          env=omacar_env(root), stdin=subprocess.DEVNULL,
                          timeout=120).returncode


# ---- the watchdog ------------------------------------------------------------

# The server's mark, as lib/serve.py answers /.mark and bin/omacar's ours() reads it.
MARK = "omacar-server"
PARTS = ("server", "world", "cams")
WHAT = {"server": "the demo server", "world": "the demo world", "cams": "the camera feed"}
# How each is known, as bin/omacar's demo_holder knows it: the script its
# interpreter runs (argv[1]), the demo's own words after it.
IDENTITY = {"server": ("serve.py", ("--demo",)), "world": ("demoworld.py", ("run",)),
            "cams": ("cams.py", ("demo",))}
# `demo on` will not run without a world and a server, so those are always
# watched; the camera feed only once it has been seen running.
ALWAYS = ("server", "world")
SERVER_MISSES = 2                  # /.mark unanswered this many looks in a row: down
SERVER_TIMEOUT = 2.0
BACKOFF = (2.0, 4.0, 8.0, 16.0)    # seconds after the 1st, 2nd, 3rd and 4th try
BACKOFF_THEN = 60.0                # and after every one after that
STEADY = 60.0                      # up this long since its last try: its backoff starts again


def demo_port():
    """The demo's port, as bin/omacar takes it (OMACAR_DEMO_PORT is the tests')."""
    try:
        return int(os.environ.get("OMACAR_DEMO_PORT") or 7580)
    except ValueError:
        return 7580


def part_alive(root, name):
    """Whether DEMO_ROOT/pids/NAME.pid names a live process that is that part of
    this demo, by bin/omacar's demo_holder test: argv[1] is .../lib/<script>,
    its words follow, and it was started with this demo's state folder. From
    whichever checkout. A dead pid, or one reused by anything else, is not."""
    script, words = IDENTITY[name]
    try:
        with open(os.path.join(root, "pids", name + ".pid"), encoding="utf-8") as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        with open(f"/proc/{pid}/environ", "rb") as f:
            environ = f.read().split(b"\0")
    except (OSError, ValueError):
        return False
    if len(argv) < 2 or not argv[1].endswith("/lib/" + script):
        return False
    if any(w not in argv[2:] for w in words):
        return False
    state = "OMACAR_STATE=" + os.path.join(root, "state", "omacar")
    return state.encode() in environ


def server_answers(port, timeout=SERVER_TIMEOUT):
    """Whether something on the demo's port answers /.mark as an OmaCar server,
    within `timeout`. http.client, not urllib: no proxy may stand in for it."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", "/.mark")
        r = c.getresponse()
        return r.status == 200 and MARK in r.read(256).decode(errors="replace")
    except (OSError, http.client.HTTPException):
        return False
    finally:
        c.close()


def parts_up(root, port):
    """What is up this instant: {"server": answers, "world": alive, "cams": alive}."""
    return {"server": server_answers(port),
            "world": part_alive(root, "world"), "cams": part_alive(root, "cams")}


def mend_part(root, name):
    """`omacar demo mend NAME`, started and not waited for, so the guard goes
    on looking at the car while a part is starting. Its words go to this
    guard's own log."""
    return subprocess.Popen([os.path.join(ROOT, "bin", "omacar"), "demo", "mend", name],
                            env=omacar_env(root), stdin=subprocess.DEVNULL)


class Watchdog:
    """Starts the demo's server, world or camera feed again when one is down.

    tick(now, may) is called on each of the guard's looks while the car is
    parked and the demo is on; may() says, just before anything is started,
    whether that is still so. probe() -> {part: up}; mend(part) -> an exit
    status, or something still running with a poll() (a Popen)."""

    def __init__(self, root, port=None, probe=None, mend=None):
        port = port or demo_port()
        self.probe = probe or (lambda: parts_up(root, port))
        self.mend = mend or (lambda name: mend_part(root, name))
        self.seen = set(ALWAYS)
        self.misses = 0
        self.tries = {}                # part -> [tries so far, t of the last]
        self.pending = None            # (part, a mend still under way)

    def _gap(self, n):
        return BACKOFF[n - 1] if n - 1 < len(BACKOFF) else BACKOFF_THEN

    def _done(self, name, rc):
        n = self.tries.get(name, [0, 0.0])[0]
        if rc == 0:
            _log(f"{WHAT[name]} is back (try {n})")
        else:
            _log(f"`omacar demo mend {name}` failed ({rc}), try {n}; the next is "
                 f"{self._gap(n):g} s after it at the soonest")

    def tick(self, now, may):
        # ONE START AT A TIME. A mend under way is waited for, a look at a
        # time, and nothing else is started beside it.
        if self.pending is not None:
            name, proc = self.pending
            rc = proc.poll()
            if rc is None:
                return
            self.pending = None
            self._done(name, rc)
        up = self.probe()
        self.misses = 0 if up.get("server") else self.misses + 1
        for name in PARTS:
            if name == "server":
                down = self.misses >= SERVER_MISSES
            else:
                down = not up.get(name)
            n, last = self.tries.get(name, [0, 0.0])
            if not down:
                if up.get(name):
                    self.seen.add(name)
                if n and now - last >= STEADY:
                    del self.tries[name]
                continue
            if name not in self.seen:
                continue
            if n and now - last < self._gap(n):
                continue
            # ASKED AGAIN, NOW: a car that has started moving, or a demo that
            # has been switched off, since this look began starts nothing.
            if not may():
                return
            self.tries[name] = [n + 1, now]
            why = ("has not answered /.mark twice in a row" if name == "server"
                   else "is not running")
            _log(f"{WHAT[name]} {why}; starting it again (try {n + 1})")
            try:
                r = self.mend(name)
            except Exception as e:                             # noqa: BLE001
                r = f"{type(e).__name__}: {e}"
            if hasattr(r, "poll"):
                self.pending = (name, r)
                return
            self._done(name, r)


# A `demo off` that fails is tried again every EVERY seconds, TRIES times in
# a row. After that the guard says so and keeps watching, trying again every
# LATER seconds while the car is still moving. It never leaves while the demo
# is still standing: a guard that gave up would leave a window on the screen
# of a moving car with nothing watching it.
TRIES = 5
LATER = 30.0


def _log(msg):
    try:
        print(f"{time.strftime('%F %T')} demoguard: {msg}", file=sys.stderr, flush=True)
    except Exception:                                          # noqa: BLE001
        pass


def watch(live_json, root, every=EVERY, off=stop_demo, clock=time.time,
          sleep=time.sleep, tries=TRIES, later=LATER, parts=None):
    """Until the demo is gone: "gone" when ACTIVE went, "moving" when the real
    car moved and `demo off` stopped the demo for it. Returns only then.
    `parts`, a Watchdog, keeps the demo's parts up meanwhile."""
    active = os.path.join(root, "ACTIVE")
    failed, next_try = 0, 0.0

    def may():
        return os.path.exists(active) and state(live_json, clock()) != "moving"

    while True:
        if not os.path.exists(active):
            # THE MARKER WENT, BUT DID THE DEMO? `demo off` removes it first and
            # stops this guard straight after, so then this changes nothing.
            # But something else may have removed it on its own -- the old bar
            # widget's "Demo Off" does exactly that, and leaves the window up
            # -- so once, before leaving, the demo is taken down properly.
            try:
                rc = off(root)
            except Exception as e:                             # noqa: BLE001
                rc = f"{type(e).__name__}: {e}"
            if rc != 0:
                _log(f"the demo's marker went, and `omacar demo off` failed ({rc})")
            return "gone"
        now = clock()
        moving = state(live_json, now) == "moving"
        if moving and now >= next_try:
            if not failed:
                _log(f"the real car is moving ({live_json}); stopping the demo")
            try:
                rc = off(root)
            except Exception as e:                             # noqa: BLE001
                rc = f"{type(e).__name__}: {e}"
            if rc == 0:
                return "moving"
            failed += 1
            _log(f"`omacar demo off` failed ({rc}), try {failed}")
            if failed >= tries:
                _log(f"the demo is still up after {failed} tries; watching, and trying "
                     f"again every {later:g} s while the car moves")
                next_try = now + later
        elif not moving:
            failed, next_try = 0, 0.0
            if parts is not None:
                parts.tick(now, may)
        sleep(every)


def main(argv):
    if len(argv) == 2 and argv[0] == "--state":
        print(state(argv[1]))
        return 0
    if len(argv) == 2 and not argv[0].startswith("-"):
        watch(argv[0], argv[1], parts=Watchdog(argv[1]) if os.path.isdir("/proc/self") else None)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
