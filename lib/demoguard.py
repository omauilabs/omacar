#!/usr/bin/env python3
"""The demo's guard: the demo closes itself the moment the real car moves.

    demoguard.py LIVE_JSON DEMO_ROOT    watch until the demo is gone
    demoguard.py --state LIVE_JSON      print "moving" or "parked", and exit 0

`omacar demo on` starts it with the REAL live.json (read before the demo's
environment moves OMACAR_STATE) and the demo's folder. Every 2 s it reads that
file, read-only. While the demo is on (DEMO_ROOT/ACTIVE exists) and the real
car is moving, it runs `omacar demo off` and exits. It also exits the moment
ACTIVE is gone, however the demo ended.

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


def watch(live_json, root, every=EVERY, off=stop_demo, clock=time.time,
          sleep=time.sleep):
    """Until the demo is gone: "gone" when ACTIVE went, "moving" when the real
    car moved and the demo was stopped for it."""
    active = os.path.join(root, "ACTIVE")
    while True:
        if not os.path.exists(active):
            return "gone"
        if state(live_json, clock()) == "moving":
            print(f"{time.strftime('%F %T')} demoguard: the real car is moving "
                  f"({live_json}); stopping the demo", file=sys.stderr, flush=True)
            off(root)
            return "moving"
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
