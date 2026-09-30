#!/usr/bin/env python3
"""The demo's guard: the demo closes itself the moment the real car moves.

    demoguard.py LIVE_JSON DEMO_ROOT    watch until the demo is gone
    demoguard.py --state LIVE_JSON      print "moving" or "parked", and exit 0

`omacar demo on` starts it with the REAL live.json (read before the demo's
environment moves OMACAR_STATE) and the demo's folder. Every 2 s it reads that
file, read-only. While the demo is on (DEMO_ROOT/ACTIVE exists) and the real
car is moving, it runs `omacar demo off`, and exits once that has worked; one
that fails is tried again (TRIES, LATER), and the guard never leaves while
the demo is still up. When ACTIVE goes, however that happened, it runs
`omacar demo off` once more and exits.

MOVING MEANS A FRESH SAMPLE ABOVE 3 KM/H. Fresh is `t` within 5 s of now: the
daemon rewrites live.json several times a second while it is talking to the
car, so an older file is a daemon that has stopped, and a car nothing is
reading counts as parked. That is the venue: no adapter, no fresh live data.
Above 3 km/h, so a car creeping across a car park is not a car being driven,
and a speed that is not a number is not a speed.

`demo on` asks the same question with --state before it starts anything, so
the rule that keeps the demo off a moving car's screen is written once.
"""

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


def stop_demo(root):
    """`omacar demo off`, for the demo in `root`.

    The guard runs inside the demo's environment, where XDG_STATE_HOME is the
    demo's own state. bin/omacar finds the demo at $XDG_STATE_HOME/omacar-demo,
    so run as it is, `demo off` would look for a demo inside the demo and stop
    nothing. The real XDG_STATE_HOME is the folder the demo sits in."""
    env = dict(os.environ, XDG_STATE_HOME=os.path.dirname(os.path.abspath(root)))
    for name in ("XDG_CONFIG_HOME", "OMACAR_STATE", "OMACAR_VIDEOS", "OMACAR_PORT"):
        env.pop(name, None)
    return subprocess.run([os.path.join(ROOT, "bin", "omacar"), "demo", "off"],
                          env=env, stdin=subprocess.DEVNULL, timeout=120).returncode


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
          sleep=time.sleep, tries=TRIES, later=LATER):
    """Until the demo is gone: "gone" when ACTIVE went, "moving" when the real
    car moved and `demo off` stopped the demo for it. Returns only then."""
    active = os.path.join(root, "ACTIVE")
    failed, next_try = 0, 0.0
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
        if state(live_json, now) == "moving" and now >= next_try:
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
        elif state(live_json, now) != "moving":
            failed, next_try = 0, 0.0
        sleep(every)


def main(argv):
    if len(argv) == 2 and argv[0] == "--state":
        print(state(argv[1]))
        return 0
    if len(argv) == 2 and not argv[0].startswith("-"):
        watch(argv[0], argv[1])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
