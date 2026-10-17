#!/usr/bin/env python3
"""The tablet sleeps only for a parked car, and never for a moving one.

lib/ignition.py's decide() is pure: given what the charger and the car said,
it returns the next state and whether to sleep. So every rule the owner was
promised is checked here against a clock this file controls, with no sysfs, no
logind and no car -- nothing in this file can put a real machine to sleep.

The rules (doc/ignition.md):
  * the charger off for the grace period, with the car quiet, sleeps;
  * the charger back before then cancels it, and a cranking dip is just that;
  * any speed in the last two minutes holds it awake (a charger fault on the
    road), and so does the car still answering (the ignition is still on);
  * after a hold lifts the countdown starts over, it does not resume;
  * Stay on holds until the charger returns, Sleep now skips the wait, and
    neither can override a moving or answering car;
  * a running child alert holds it, and Sleep now and a flat battery cannot
    end that;
  * a charger that cannot be read never starts a countdown;
  * the charger back after a sleep asks for the recorders to come back.
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import ignition  # noqa: E402

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


CFG = dict(ignition.DEFAULTS)
ON = {"powered": True, "battery": 80, "seen": []}
OFF = {"powered": False, "battery": 80, "seen": []}
UNKNOWN = {"powered": None, "battery": 80, "seen": []}
QUIET = {"answering": False, "speed": None, "age": None}


def drive(steps, st=None):
    """Run decide() over [(t, power, car, ask)], returning [(phase, action)]."""
    st = st or ignition.fresh_state()
    out = []
    for t, pw, car, ask in steps:
        st, action = ignition.decide(st, pw, car, t, CFG, ask)
        out.append((st["phase"], action, st.get("held")))
    return st, out


def main():
    print("\n  the countdown")
    _st, out = drive([(0, ON, QUIET, None), (100, OFF, QUIET, None),
                      (129, OFF, QUIET, None), (130, OFF, QUIET, None)])
    check(f"charger off starts it, and 30 s of a quiet car sleeps ({out})",
          out[1][0] == "counting" and out[2][1] is None and out[3] == ("asleep", "sleep", None))

    _st, out = drive([(0, OFF, QUIET, None), (12, ON, QUIET, None), (20, OFF, QUIET, None),
                      (45, OFF, QUIET, None), (50, OFF, QUIET, None)])
    check(f"the charger back cancels it, and the next one starts from zero ({out})",
          out[1][0] == "on" and out[3][1] is None and out[4][1] == "sleep")

    print("\n  the car can veto it")
    moving = {"answering": True, "speed": 48.0, "age": 0.2}
    st, out = drive([(0, ON, moving, None), (1, OFF, moving, None),
                      (60, OFF, QUIET, None), (120, OFF, QUIET, None)])
    check(f"a charger lost while moving holds, for the whole two minutes ({out})",
          all(o[0] == "held" and o[2] == "moving" and o[1] is None for o in out[1:]))
    st, out = drive([(122, OFF, QUIET, None), (151, OFF, QUIET, None), (152, OFF, QUIET, None)], st)
    check(f"and when the hold lifts the countdown starts over, not resumes ({out})",
          out[0][0] == "counting" and out[1][1] is None and out[2][1] == "sleep")

    idling = {"answering": True, "speed": 0.0, "age": 0.3}
    _st, out = drive([(0, OFF, idling, None), (40, OFF, idling, None)])
    check(f"the car still answering holds: the ignition is on ({out})",
          all(o[2] == "car answering" and o[1] is None for o in out))

    stale = {"answering": True, "speed": 90.0, "age": 600}
    _st, out = drive([(0, OFF, stale, None), (30, OFF, stale, None)])
    check(f"a sample from ten minutes ago vetoes nothing ({out})", out[1][1] == "sleep")

    print("\n  the screen's two taps")
    _st, out = drive([(0, OFF, QUIET, None), (5, OFF, QUIET, "stay"), (500, OFF, QUIET, None),
                      (501, ON, QUIET, None), (502, OFF, QUIET, None), (532, OFF, QUIET, None)])
    check(f"Stay on holds until the charger is back, then counts as usual ({out})",
          out[1][2] == "stay on" and out[2][2] == "stay on" and out[3][0] == "on"
          and out[5][1] == "sleep")

    low = {"powered": False, "battery": 9, "seen": []}
    _st, out = drive([(0, OFF, QUIET, "stay"), (10, low, QUIET, None), (40, low, QUIET, None)])
    check(f"a low battery ends a stay-on ({out})", out[1][0] == "counting" and out[2][1] == "sleep")

    _st, out = drive([(0, OFF, QUIET, None), (3, OFF, QUIET, "now")])
    check(f"Sleep now skips the wait ({out})", out[1][1] == "sleep")
    _st, out = drive([(0, OFF, QUIET, "stay"), (3, OFF, QUIET, "now")])
    check(f"and cancels an earlier Stay on ({out})", out[1][1] == "sleep")

    _st, out = drive([(0, OFF, moving, "now"), (1, OFF, moving, "now")])
    check(f"but neither tap can sleep a moving car ({out})",
          all(o[1] is None and o[2] == "moving" for o in out))
    low_moving = {"powered": False, "battery": 3, "seen": []}
    _st, out = drive([(0, low_moving, moving, None), (100, low_moving, QUIET, None)])
    check(f"nor can a flat battery ({out})", all(o[1] is None for o in out))

    print("\n  a child alert (lib/childalert.py)")

    def drive_child(steps, st=None):
        st = st or ignition.fresh_state()
        out = []
        for t, pw, car, ask, child in steps:
            st, action = ignition.decide(st, pw, car, t, CFG, ask, child)
            out.append((st["phase"], action, st.get("held")))
        return st, out

    _st, out = drive_child([(0, OFF, QUIET, None, "child alert"), (30, OFF, QUIET, None, "child alert"),
                            (3600, OFF, QUIET, None, "child alert")])
    check(f"a child alert holds the tablet awake, for as long as it runs ({out})",
          all(o[0] == "held" and o[2] == "child alert" and o[1] is None for o in out))
    _st, out = drive_child([(0, OFF, QUIET, None, "child alert"), (5, OFF, QUIET, "now", "child alert"),
                            (6, OFF, QUIET, "now", "child alert")])
    check(f"Sleep now cannot override it ({out})", all(o[1] is None for o in out))
    flat = {"powered": False, "battery": 2, "seen": []}
    _st, out = drive_child([(0, flat, QUIET, None, "child alert"), (60, flat, QUIET, None, "child alert")])
    check(f"nor can a flat battery, which ends a Stay on ({out})",
          all(o[1] is None and o[2] == "child alert" for o in out))
    _st, out = drive_child([(0, OFF, QUIET, None, "child alert"), (40, OFF, QUIET, None, None),
                            (69, OFF, QUIET, None, None), (70, OFF, QUIET, None, None)])
    check(f"once answered, the full countdown starts over ({out})",
          out[1][0] == "counting" and out[2][1] is None and out[3][1] == "sleep")
    _st, out = drive_child([(0, OFF, moving, None, "child alert")])
    check(f"a moving car still reads as moving, not as the alert ({out})", out[0][2] == "moving")

    print("\n  unknowns and waking")
    _st, out = drive([(0, UNKNOWN, QUIET, None), (100, UNKNOWN, QUIET, "now")])
    check(f"a charger it cannot read never starts a countdown ({out})",
          all(o[0] == "on" and o[1] is None for o in out))

    st, out = drive([(0, OFF, QUIET, None), (30, OFF, QUIET, None), (5000, ON, QUIET, None)])
    check(f"the charger back after a sleep asks for the recorders ({out})",
          out[1][1] == "sleep" and out[2] == ("on", "wake", None))
    st, out = drive([(0, OFF, QUIET, None), (30, OFF, QUIET, None),
                     (9000, OFF, QUIET, None), (9029, OFF, QUIET, None), (9030, OFF, QUIET, None)])
    check(f"a stray wakeup with no charger goes back to sleep after its grace ({out})",
          out[2][0] == "counting" and out[3][1] is None and out[4][1] == "sleep")

    print("\n  reading the machine")
    scratch = tempfile.mkdtemp(prefix="omacar-ign-")
    try:
        def supply(name, kind, **attrs):
            d = os.path.join(scratch, name)
            os.makedirs(d)
            for k, v in dict(type=kind, **attrs).items():
                with open(os.path.join(d, k), "w") as f:
                    f.write(str(v) + "\n")
        supply("ADP1", "Mains", online=0)
        supply("BAT1", "Battery", capacity=57)
        r = ignition.supplies(scratch)
        check(f"Surface Connect off reads as off, with the battery ({r})",
              r["powered"] is False and r["battery"] == 57)
        supply("ucsi-source-psy-USBC000:001", "USB", online=1)
        r = ignition.supplies(scratch)
        check(f"any online supply counts as power ({r['powered']})", r["powered"] is True)
        empty = os.path.join(scratch, "none")
        os.makedirs(empty)
        check("no supplies at all reads as unknown, not off",
              ignition.supplies(empty)["powered"] is None)

        live = os.path.join(scratch, "live.json")
        with open(live, "w") as f:
            json.dump({"connected": True, "t": 1000.0, "values": {"SPEED": 37}}, f)
        c = ignition.car(live, now=1002.0)
        check(f"live.json gives speed and age ({c})",
              c["answering"] and c["speed"] == 37.0 and abs(c["age"] - 2.0) < 1e-6)
        with open(live, "w") as f:
            json.dump({"connected": False, "status": "yielded", "handover": True,
                       "t": 1000.0, "values": {"SPEED": 64}}, f)
        c = ignition.car(live, now=1001.0)
        check(f"a hand-off (recorder borrowing the adapter) is the car answering ({c})",
              c["answering"] and c["speed"] == 64.0)
        _s, out = drive([(1001, OFF, c, None), (1200, OFF, ignition.car(live, now=1001.0), None)])
        check(f"so a charger lost during a capture leg holds as moving ({out})",
              all(o[1] is None and o[2] == "moving" for o in out))
        with open(live, "w") as f:
            json.dump({"connected": False, "status": "waiting for the car"}, f)
        check("a waiting daemon is not the car answering",
              ignition.car(live, now=1002.0)["answering"] is False)
        check("no live.json at all is a quiet car",
              ignition.car(os.path.join(scratch, "nope.json"))["answering"] is False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
