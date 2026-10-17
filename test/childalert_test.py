#!/usr/bin/env python3
"""A child left in the car (lib/childalert.py): the latch, the ladder, the veto.

decide() is pure, like lib/ignition.py's: given what the charger, the car, the
camera and the cabin sensor said, it returns the next state and the messages
to queue. So every rule is checked here against a clock this file controls --
no sysfs, no camera, no outbox on the real machine. The dangerous failure is a
false negative, so most of these try to make it stay quiet and check it won't:

  * occupied = seen_recently OR (seen_during_drive AND not cleared);
  * a camera that is dark, unplugged or silent is unknown, never "empty";
  * only the owner's tap clears it; a face just after the tap is the child
    being lifted out, and one later re-arms it;
  * t=0 the card, t=30 s the outbox (again every 10 min), t=60 s the chime;
  * a hot or fast-warming cabin escalates with a fresh message per 5 degrees;
  * never while the car is on or moving; once per switch-off;
  * the ignition loop is held while it runs, even if this loop has died.
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


DRIVING = {"answering": True, "speed": 50.0, "age": 0.3}
STOPPED_ON = {"answering": True, "speed": 0.0, "age": 0.3}
QUIET = {"answering": False, "speed": None, "age": None}
NO_CAM = {"state": "none", "last_seen": None, "age": None}
NO_CABIN = {"t_c": None, "state": None}


def cam(seen, state="watching"):
    return {"state": state, "last_seen": seen, "age": 0.5}


def cabin(c):
    return {"t_c": c, "state": "live", "source": "sensor"}


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-child-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import childalert as ca
    CFG = dict(ca.DEFAULTS, camera="rear")

    def run(steps, st=None, cfg=CFG):
        """[(t, power, car, cam, cabin, ask)] -> state, [(phase, car, notify keys)]."""
        st = st or ca.fresh_state()
        out = []
        for t, pw, car, cm, cb, ask in steps:
            st, acts = ca.decide(st, pw, car, cm, cb, t, cfg, ask)
            out.append((st["phase"], st["car"], [a["notify"]["key"] for a in acts]))
        return st, out

    try:
        print("\n  the occupancy rule")
        check("seen recently is occupied", ca.occupied(True, False, False))
        check("seen during the drive and not cleared is occupied", ca.occupied(False, True, False))
        check("seen during the drive but cleared is not", not ca.occupied(False, True, True))
        check("never seen is not", not ca.occupied(False, False, False))
        check("seen just now outranks an older clear", ca.occupied(True, True, True))
        check("a sighting inside the grace after a clear does not count",
              not ca.counts(110, 100) and ca.counts(121, 100) and ca.counts(5, None))

        print("\n  the latch through a switch-off")
        st, out = run([(0, True, DRIVING, cam(None), NO_CABIN, None),
                       (10, True, DRIVING, cam(10), NO_CABIN, None),
                       (900, True, DRIVING, cam(10, "dark"), NO_CABIN, None),
                       (1000, False, QUIET, cam(10, "dark"), NO_CABIN, None)])
        check(f"a face during the drive arms it, silently, while driving ({out[1]})",
              out[1][:2] == ("armed", "on") and not out[1][2])
        check(f"15 minutes later, in the dark, it is still armed ({out[2][0]})", out[2][0] == "armed")
        check(f"switched off with nothing seen for 16 minutes: the alert fires ({out[3]})",
              out[3][:2] == ("alert", "off"))
        for state in ("no-picture", "no-engine", "error", "no-reports"):
            _st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                          (600, False, QUIET, cam(5, state), NO_CABIN, None)])
            check(f"a camera that is {state} at switch-off is not 'empty': alert", o[1][0] == "alert")
        _st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                      (600, False, QUIET, {"state": "no-reports", "last_seen": None, "age": None},
                       NO_CABIN, None)])
        check(f"even if the camera's file is gone, the drive's latch stands ({o[1][0]})", o[1][0] == "alert")
        _st, o = run([(0, True, DRIVING, NO_CAM, NO_CABIN, None),
                      (300, False, QUIET, NO_CAM, NO_CABIN, None)])
        check(f"nothing ever seen, nothing said: no alert ({o[1][0]})", o[1][0] == "parked")
        _st, o = run([(0, True, DRIVING, NO_CAM, NO_CABIN, "aboard"),
                      (300, False, QUIET, NO_CAM, NO_CABIN, None)])
        check(f"no camera, but the owner tapped Child aboard: alert ({o[1][0]})", o[1][0] == "alert")
        _st, o = run([(0, True, DRIVING, cam(None), NO_CABIN, None),
                      (100, False, QUIET, cam(None), NO_CABIN, None),
                      (160, False, QUIET, cam(150), NO_CABIN, None)])
        check(f"a face seen after the car was switched off starts it then ({o})",
              o[1][0] == "parked" and o[2][0] == "alert")

        print("\n  clearing")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (105, False, QUIET, cam(104), NO_CABIN, "clear"),
                     (115, False, QUIET, cam(114), NO_CABIN, None),
                     (300, False, QUIET, cam(114), NO_CABIN, None)])
        check(f"'I have my child' ends it, and the child's face on the way out does not re-arm it ({o})",
              o[2][0] == "cleared" and o[3][0] == "cleared" and o[4][0] == "cleared")
        st2, o = run([(400, False, QUIET, cam(400), NO_CABIN, None)], st)
        check(f"a face well after the clear is new evidence: a new alert ({o})", o[0][0] == "alert")
        check("and it is counted as a second alert, with keys of its own",
              st2["alerts"] == 2)
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (50, True, DRIVING, cam(5), NO_CABIN, "clear"),
                     (400, False, QUIET, cam(5), NO_CABIN, None)])
        check(f"cleared during the drive (seat empty) and nothing seen since: no alert ({o[2][0]})",
              o[2][0] == "parked")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (120, False, QUIET, cam(5), NO_CABIN, "clear"),
                     (5000, True, DRIVING, cam(5), NO_CABIN, None),
                     (6000, False, QUIET, cam(5), NO_CABIN, None)])
        check(f"after a clear, the next drive with no new face does not alert ({o[4][0]})",
              o[4][0] == "parked")

        print("\n  the ladder")
        steps = [(0, True, DRIVING, cam(5), NO_CABIN, None)]
        steps += [(t, False, QUIET, cam(5), NO_CABIN, None) for t in (100, 129, 130, 159, 160, 700, 730, 1330)]
        st, o = run(steps)
        pubs = {}
        st2 = ca.fresh_state()
        for t, pw, car, cm, cb, ask in steps:
            st2, _a = ca.decide(st2, pw, car, cm, cb, t, CFG, ask)
            pubs[t] = ca.public(st2, CFG, cm, cb, t)
        check(f"t=0 the card, no message yet ({o[1]})", o[1][0] == "alert" and not o[1][2])
        check(f"t=29 still no message ({o[2][2]})", not o[2][2])
        check(f"t=30 one critical message to the outbox ({o[3][2]})", len(o[3][2]) == 1)
        check("no chime before t=60", not pubs[159]["chime"] and pubs[160]["chime"])
        check(f"no second message on the next tick ({o[4][2]})", not o[4][2])
        check(f"and a repeat every ten minutes while nobody answers ({o[7][2]}, {o[8][2]})",
              o[6][2] == [] and len(o[7][2]) == 1 and len(o[8][2]) == 1 and o[7][2] != o[3][2])
        check("the screen is told to hold the tablet awake", pubs[100]["hold_sleep"])
        quiet_cfg = dict(CFG, chime=False)
        check("the chime is the owner's to switch off",
              not ca.public(st2, quiet_cfg, cam(5), NO_CABIN, 1330)["chime"])

        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (110, False, QUIET, cam(5), NO_CABIN, "snooze"),
                     (130, False, QUIET, cam(5), NO_CABIN, None),
                     (229, False, QUIET, cam(5), NO_CABIN, None),
                     (230, False, QUIET, cam(5), NO_CABIN, None)])
        check(f"Remind me in 2 min hides the card, but the outbox still goes at 30 s ({o[2]}, {o[3]})",
              o[2][0] == "snoozed" and o[3][0] == "snoozed" and len(o[3][2]) == 1)
        p = ca.public(st, CFG, cam(5), NO_CABIN, 230)
        check(f"and after two minutes the card is back, chiming at once ({o[5][0]}, chime {p['chime']})",
              o[4][0] == "snoozed" and o[5][0] == "alert" and p["chime"])
        st_s, _ = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                       (100, False, QUIET, cam(5), NO_CABIN, None),
                       (110, False, QUIET, cam(5), NO_CABIN, "snooze")])
        check("a snoozed alert still holds the tablet awake",
              ca.public(st_s, CFG, cam(5), NO_CABIN, 111)["hold_sleep"])

        print("\n  heat")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), cabin(26.0), None),
                     (400, False, QUIET, cam(5), cabin(27.5), None),
                     (700, False, QUIET, cam(5), cabin(29.2), None),
                     (760, False, QUIET, cam(5), cabin(30.1), None),
                     (900, False, QUIET, cam(5), cabin(33.0), None),
                     (960, False, QUIET, cam(5), cabin(35.2), None)])
        heat_keys = [k for row in o for k in row[2] if "-heat-" in k]
        check(f"rising 3.2 °C in ten minutes escalates before 30 °C ({o[3][2]})",
              any("-heat-" in k for k in o[3][2]))
        check(f"and every further 5 °C band is a fresh message ({heat_keys})",
              len(heat_keys) == 3 and len(set(heat_keys)) == 3)
        h = ca.heat_of([[0, 31.0], [600, 31.0]], 600, 30.0)
        check(f"a hot car that has stopped warming is still hot ({h})", h["hot"] and h["why"] == "hot")
        h = ca.heat_of([[0, 24.0], [300, 25.0]], 300, 30.0)
        check(f"a cool, slowly warming car is not ({h})", not h["hot"])
        check("no sensor: no heat, and the screen is told so",
              ca.heat_of([], 100, 30.0) is None
              and ca.public(st, CFG, cam(5), NO_CABIN, 960)["cabin_sensor"] is False)
        stale = {"t_c": 45.0, "state": "stale", "source": "sensor"}
        _st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                      (100, False, QUIET, cam(5), stale, None)])
        check("a stale reading is not used to escalate", not any("-heat-" in k for k in o[1][2]))

        print("\n  never while the car is on or moving")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, DRIVING, cam(5), NO_CABIN, None),
                     (130, False, STOPPED_ON, cam(5), NO_CABIN, None),
                     (300, False, STOPPED_ON, cam(5), NO_CABIN, None),
                     (301, False, QUIET, cam(5), NO_CABIN, None)])
        check(f"a charger lost on the road is not a switch-off ({o[1][:2]})", o[1][:2] == ("armed", "moving"))
        check(f"nor stopped at a light with the car still answering ({o[2][:2]})", o[2][:2] == ("armed", "moving"))
        check(f"but two minutes stopped with no speed is parked ({o[3][:2]})", o[3][:2] == ("alert", "off"))
        _st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                      (100, False, {"answering": False, "speed": None, "age": None}, cam(5), NO_CABIN, None)])
        check(f"the ignition off (computers quiet): the alert is not held back by the drive's speed ({o[1][0]})",
              o[1][0] == "alert")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (140, True, DRIVING, cam(5), NO_CABIN, None)])
        check(f"the car started again ends a running alert ({o[2][0]})", o[2][0] == "armed")
        st, o = run([(0, None, DRIVING, cam(5), NO_CABIN, None),
                     (100, None, QUIET, cam(5), NO_CABIN, None)])
        check(f"a charger that cannot be read never starts one ({o})", all(r[0] == "armed" for r in o))
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (130, None, QUIET, cam(5), NO_CABIN, None)])
        check(f"but one running keeps running if the charger becomes unreadable ({o[2][0]})",
              o[2][0] == "alert")
        st, o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                     (100, False, QUIET, cam(5), NO_CABIN, None),
                     (200, False, QUIET, cam(5), NO_CABIN, None)])
        keys = [k for r in o for k in r[2]]
        check(f"once per switch-off: one alert, one first message ({st['alerts']}, {keys})",
              st["alerts"] == 1 and len(keys) == 1)
        off, _o = run([(0, False, QUIET, cam(5), NO_CABIN, None)], cfg=dict(CFG, enabled=False))
        check("switched off in the settings, it does nothing and holds nothing",
              off["phase"] == "off" and not ca.public(off, dict(CFG, enabled=False), NO_CAM, NO_CABIN, 0)["hold_sleep"])

        print("\n  the files, and the ignition's view of them")
        r = ca.report({"faces": 1, "state": "watching", "luma": 80, "camera": "rear"}, now=1000)
        ca.report({"faces": 0, "state": "dark", "luma": 9, "camera": "rear"}, now=1002)
        c = ca.cam_now(CFG, now=1003)
        check(f"a sighting survives the next empty report ({c})",
              r["ok"] and c["last_seen"] == 1000 and c["state"] == "dark")
        check("reports stop: the camera is unknown, and the sighting is kept",
              ca.cam_now(CFG, now=1100) == {"state": "no-reports", "last_seen": 1000, "age": 98.0})
        check("a report for a camera the owner moved away from is not current",
              ca.cam_now(dict(CFG, camera="cabin"), now=1003)["state"] == "no-reports")
        bad = 0
        for junk in ({"faces": -1, "state": "watching"}, {"faces": 1, "state": "asleep"},
                     {"faces": True, "state": "watching"}, "nope"):
            try:
                ca.report(junk)
            except ValueError:
                bad += 1
        check("junk reports are refused", bad == 4)

        st, _o = run([(0, True, DRIVING, cam(5), NO_CABIN, None),
                      (100, False, QUIET, cam(5), NO_CABIN, None)])
        ca._write_json(ca.STATE, {"pub": ca.public(st, CFG, cam(5), NO_CABIN, 100), "st": st})
        check("a running alert holds the ignition", ca.ignition_hold(now=105) == "child alert")
        check("and still does after this loop stops answering", ca.ignition_hold(now=5000) == "child alert")
        ca._write_json(ca.ASK, {"ask": "clear", "at": 200})
        check("until somebody taps I have my child", ca.ignition_hold(now=5000) is None)
        os.unlink(ca.ASK)
        restored = ca.restore()
        check("the latch is restored from disk after a restart",
              restored["phase"] == "alert" and restored["latched_at"] == 5)
        sim = ca.public(st, CFG, cam(5), NO_CABIN, 100, source="sim")
        ca._write_json(ca.STATE, {"pub": sim, "st": st})
        check("a simulation never holds the real tablet", ca.ignition_hold(now=101) is None)
        check("nor is it restored as a real alert", ca.restore()["phase"] == "idle")
        os.unlink(ca.STATE)
        check("no file: nothing held", ca.ignition_hold() is None)
        check("no loop: the API says so", ca.read_public()["running"] is False)

        import ignition
        ca._write_json(ca.STATE, {"pub": ca.public(st, CFG, cam(5), NO_CABIN, 100), "st": st})
        ist = ignition.fresh_state()
        OFF = {"powered": False, "battery": 50, "seen": []}
        icfg = dict(ignition.DEFAULTS)
        for t, a in ((100, None), (140, "now"), (200, None)):
            ist, act = ignition.decide(ist, OFF, QUIET, t, icfg, a, ignition.child_hold(t))
        check(f"end to end: the ignition loop reads the hold and Sleep now cannot pass it ({ist['held']}, {act})",
              ist["held"] == "child alert" and act is None)

        print("\n  settings")
        cfg = ca.set_config({"chime": False, "heat_c": 32, "camera": "cabin"})
        check(f"settings are kept ({cfg})", cfg["chime"] is False and cfg["heat_c"] == 32.0
              and cfg["camera"] == "cabin" and cfg["enabled"] is True)
        bad = 0
        for junk in ({"heat_c": 80}, {"camera": "toddler-cam"}, {"enabled": "yes"}, {"volume": 11}):
            try:
                ca.set_config(junk)
            except ValueError:
                bad += 1
        check("and junk is refused", bad == 4)
        with open(ca.CONFIG, "w") as f:
            json.dump({"enabled": "maybe", "heat_c": 999, "camera": "toddler"}, f)
        cfg = ca.config()
        check(f"a hand-edited file with junk falls back to safe values ({cfg})",
              cfg["enabled"] is True and cfg["heat_c"] == 30.0 and cfg["camera"] is None)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
