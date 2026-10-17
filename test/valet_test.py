#!/usr/bin/env python3
"""Valet mode (lib/valet.py): the PIN, the lockout, and the log's arithmetic.

No car and no clock: every function takes the time it is told, and the log is
fed a synthetic trace.
"""

import json
import os
import shutil
import stat
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


def raises(fn, status=None):
    import valet
    try:
        fn()
    except valet.ValetError as e:
        return e if status is None or e.status == status else None
    return None


def trace(points, hz=5):
    """[(seconds, km/h), ...] joined by straight lines, sampled at hz."""
    out, t = [], 0.0
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        n = int(round((t1 - t0) * hz))
        for i in range(n):
            out.append((t0 + (t1 - t0) * i / n, v0 + (v1 - v0) * i / n))
    out.append(points[-1])
    return out


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-valet-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import valet
    try:
        print("\n  the PIN")
        a, b = valet.hash_pin("1234"), valet.hash_pin("1234")
        check("the same PIN hashes differently under a new salt", a["hash"] != b["hash"] and a["salt"] != b["salt"])
        check("the right PIN verifies", valet.check_pin("1234", a))
        check("a wrong PIN does not", not valet.check_pin("1235", a))
        check("only four digits are a PIN",
              [valet.valid_pin(x) for x in ("1234", "123", "12345", "abcd", "12 4", None, 1234)]
              == [True, False, False, False, False, False, False])
        check("a malformed record verifies nothing", not valet.check_pin("1234", {}) and not valet.check_pin("1234", None))
        check("the hash is not the PIN", "1234" not in json.dumps(a))

        print("\n  turning on")
        now = 1_000_000.0
        check("nothing is on to begin with", valet.read_public(now)["on"] is False)
        e = raises(lambda: valet.turn_on(None, False, now), 409)
        check("the first time it needs a PIN or an explicit No PIN", e is not None and e.body.get("needs_setup"))
        check("a three-digit PIN is refused", raises(lambda: valet.turn_on("123", False, now), 400) is not None)
        check("still off after those", valet.read_public(now)["on"] is False)

        os.makedirs(valet.STATE_DIR, exist_ok=True)
        with open(valet.LIVE, "w") as f:
            json.dump({"connected": True, "t": now, "values": {"SPEED": 62.0}}, f)
        e = raises(lambda: valet.turn_on("4321", False, now), 409)
        check("moving: refused, server side", e is not None and e.body.get("moving"))
        check("and nothing was set by the refused attempt", not valet.config()["pin"])
        with open(valet.LIVE, "w") as f:
            json.dump({"connected": True, "t": now - 600, "values": {"SPEED": 62.0}}, f)
        check("a stale live.json is not 'moving'", valet.live_speed(now) is None)
        with open(valet.LIVE, "w") as f:
            json.dump({"connected": True, "t": now, "values": {"SPEED": 0.0}}, f)
        pub = valet.turn_on("4321", False, now)
        check("just turned on, the recorder is 'starting', not failed",
              pub["starting"] is True and pub["recording"] is False)
        check("after the grace it is honestly not recording",
              valet.read_public(now + valet.START_GRACE + 1)["starting"] is False)
        check("parked: on", pub["on"] is True and pub["since"] == now)
        check("the PIN is stored hashed, mode 600",
              stat.S_IMODE(os.stat(valet.CONFIG).st_mode) == 0o600
              and "4321" not in open(valet.CONFIG).read())
        check("the public view never carries the hash", "hash" not in json.dumps(pub) and "salt" not in json.dumps(pub))
        check("turning on twice is harmless", valet.turn_on(None, False, now + 1)["since"] == now)

        print("\n  turning off, and the lockout")
        e = raises(lambda: valet.turn_off("0000", now + 5), 403)
        check("a wrong PIN is refused and counts", e is not None and e.body["tries_left"] == 4)
        check("still on", valet.read_public(now + 5)["on"])
        check("no PIN at all is refused too", raises(lambda: valet.turn_off(None, now + 6), 403) is not None)
        for i in range(2):
            raises(lambda: valet.turn_off("0000", now + 7 + i))
        e = raises(lambda: valet.turn_off("0000", now + 10), 429)
        check("the fifth wrong PIN starts a five-minute cooldown", e is not None and e.body["retry_in"] == 300)
        check("the cooldown shows in the public state", valet.read_public(now + 11)["locked_for"] > 280)
        e = raises(lambda: valet.turn_off("4321", now + 60), 429)
        check("during the cooldown even the right PIN is refused", e is not None and valet.read_public(now + 60)["on"])
        pub = valet.turn_off("4321", now + 400)
        check("after it, the right PIN turns it off and the count is clear",
              pub["on"] is False and pub["tries_left"] == 5 and pub["locked_for"] == 0)
        check("the report says there was no log rather than inventing zeros",
              pub["last_report"]["tracked"] is False and "top_kmh" not in pub["last_report"])

        print("\n  No PIN, and changing the PIN")
        shutil.rmtree(valet.STATE_DIR); shutil.rmtree(valet.CONFIG_DIR)
        pub = valet.turn_on(None, True, now)
        check("No PIN is an explicit choice", pub["on"] and pub["no_pin"] and not pub["has_pin"])
        check("with no PIN, off needs none", valet.turn_off(None, now + 1)["on"] is False)
        check("Change PIN needs valet off", raises(lambda: (valet.turn_on(None, False, now), valet.set_pin(None, "1111"))[1], 409) is not None)
        valet.turn_off(None, now + 2)
        valet.set_pin(None, "2468")
        check("a PIN can be set later", valet.config()["pin"] is not None and not valet.config()["no_pin"])
        check("changing it needs the old one", raises(lambda: valet.set_pin("0000", "1111"), 403) is not None)
        valet.set_pin("2468", "1357")
        check("and then the new one works", valet.check_pin("1357", valet.config()["pin"]))
        pub = valet.ask({"action": "on"}, now + 3)
        check("ask() routes 'on'", pub["on"])
        check("ask() refuses an unknown action", raises(lambda: valet.ask({"action": "x"}), 400) is not None)

        print("\n  the log's arithmetic")
        # 0 -> 100 km/h in 20 s, cruise 60 s, ease to 0 over 20 s: a gentle drive.
        gentle = trace([(0, 0), (20, 100), (80, 100), (100, 0)])
        L = valet.Log(0)
        for t, v in gentle:
            L.feed(t, v)
        s = L.snapshot()
        check("top speed is the peak", s["top_kmh"] == 100.0)
        check("a gentle drive: no hard brakes", s["hard_brakes"] == 0)
        want = (100 * 20 / 2 + 100 * 60 + 100 * 20 / 2) / 3600
        check("distance is the speed's integral (%.3f km)" % want, abs(s["distance_km"] - want) < 0.005 and s["distance_basis"] == "speed")
        check("under 80 mph the time above it is zero", s["fast_s"] == 0)

        # Two hard stops from 100 km/h in 3 s (9.3 m/s2), separated by a re-acceleration.
        harsh = trace([(0, 0), (10, 100), (13, 20), (20, 100), (23, 0)])
        L = valet.Log(0)
        for t, v in harsh:
            L.feed(t, v)
        check("two separate hard stops count as two", L.brakes == 2)
        # One long firm stop is one event, not one per sample.
        L = valet.Log(0)
        for t, v in trace([(0, 100), (4, 0)]):
            L.feed(t, v)
        check("one firm stop (7 m/s2) is one event", L.brakes == 1)
        # Ordinary braking 100 -> 0 in 10 s is 2.8 m/s2: not hard.
        L = valet.Log(0)
        for t, v in trace([(0, 100), (10, 0)]):
            L.feed(t, v)
        check("2.8 m/s2 is not a hard brake", L.brakes == 0)
        # Whole-km/h jitter on a steady cruise must not look like braking.
        L = valet.Log(0)
        for i in range(300):
            L.feed(i * 0.2, 100 + (1 if i % 2 else -1))
        check("+-1 km/h jitter at 5 Hz is not braking", L.brakes == 0)
        # Braking from a crawl is not an event.
        L = valet.Log(0)
        for t, v in trace([(0, 12), (0.5, 0)]):
            L.feed(t, v)
        check("a stop from 12 km/h is not an event", L.brakes == 0)

        # Time above 80 mph: 140 km/h held for 30 s.
        L = valet.Log(0)
        for t, v in trace([(0, 0), (10, 140), (40, 140), (50, 0)]):
            L.feed(t, v)
        s = L.snapshot()
        check("time above 80 mph counts the held stretch (%ds)" % s["fast_s"], 29 <= s["fast_s"] <= 33)
        check("top speed shows in the car's unit", s["top_kmh"] == 140.0)

        # Odometer wins when it moved; speed is the fallback when it is frozen.
        L = valet.Log(0)
        for i, (t, v) in enumerate(trace([(0, 0), (10, 50), (20, 0)])):
            L.feed(t, v, 1000 + i * 0.01)
        km, basis = L.distance()
        check("an odometer that moved is the distance", basis == "odometer" and abs(km - (len(trace([(0, 0), (10, 50), (20, 0)])) - 1) * 0.01) < 1e-6)
        L = valet.Log(0)
        for t, v in trace([(0, 0), (10, 50), (20, 0)]):
            L.feed(t, v, 1000.0)
        check("a frozen odometer falls back to the speed integral", L.distance()[1] == "speed" and L.distance()[0] > 0.1)

        # A silence is a gap, not a drive at the last speed.
        L = valet.Log(0)
        L.feed(0, 100); L.feed(0.2, 100); L.feed(600, 100); L.feed(600.2, 100)
        check("a ten-minute silence adds no distance", L.distance()[0] < 0.02)
        # The same sample twice, and a sample out of order, change nothing.
        L = valet.Log(0)
        L.feed(1, 50); L.feed(1, 90); L.feed(0.5, 200)
        check("repeats and out-of-order samples are ignored", L.top == 50 and L.samples == 1)
        check("an unknown speed is not a zero", valet.Log(0).feed(1, None) is None and valet.Log(0).snapshot()["samples"] == 0)
        check("the snapshot holds no place, only numbers and times",
              set(valet.Log(0).snapshot()) == {"started", "ended", "top_kmh", "fast_s", "distance_km",
                                               "distance_basis", "hard_brakes", "samples", "simulated"})

        print("\n  the recorder against live.json")
        shutil.rmtree(valet.STATE_DIR); shutil.rmtree(valet.CONFIG_DIR)
        os.makedirs(valet.STATE_DIR)
        import time
        valet.turn_on(None, True)
        pub0 = valet.read_public()
        check("with the recorder never run, it says not recording", pub0["recording"] is False)
        t0 = time.time()
        s0 = valet.state()
        # Feed the recorder through its own loop, one pass per sample.
        # Samples stamped t0+dt, all inside the staleness window of the wall clock.
        for i, (dt, v) in enumerate(trace([(0, 0), (6, 150), (9, 20), (10, 0)])):
            with open(valet.LIVE, "w") as f:
                json.dump({"connected": True, "simulated": True, "t": t0 + dt,
                           "values": {"SPEED": v}, "odometer_km": 500 + i * 0.01}, f)
            valet.run(once=True)
        pub = valet.read_public()
        check("with samples flowing it is recording", pub["recording"] is True)
        check("the running log is visible to the owner", pub["so_far"]["top_kmh"] >= 0)
        check("a simulated trace says so", pub["so_far"]["simulated"] is True)
        rep = valet.turn_off(None)["last_report"]
        check("off files the report: tracked, simulated, with the times",
              rep["tracked"] and rep["simulated"] and rep["started"] and rep["ended"] >= rep["started"])
        check("the report holds no location", not any(k in rep for k in ("lat", "lon", "gps", "path", "route")))
        check("ack marks it seen without losing it", valet.ack()["last_report"]["seen"] is True)

        print("\n  the files")
        check("the log file holds no coordinates", all(f'"{k}"' not in open(valet.LOG).read() for k in ("lat", "lon", "gps", "path", "route")))

        print("\n  one lockout for every path that asks for the PIN")
        for f in (valet.CONFIG, valet.STATE):
            if os.path.exists(f):
                os.unlink(f)
        t0 = 5_000_000.0
        valet.set_pin(None, "2468", now=t0)
        codes = [getattr(raises(lambda: valet.set_pin("0000", "1111", now=t0 + i), None), "status", None) for i in range(5)]
        check(f"changing the PIN with a wrong old one counts towards the lockout ({codes})", codes == [403, 403, 403, 403, 429])
        check("and the lockout holds even with the right PIN",
              raises(lambda: valet.set_pin("2468", "1111", now=t0 + 10), 429) is not None)
        valet.turn_on(None, False, t0 + 400)
        check("turning valet off is locked by the same counter after it",
              raises(lambda: valet.turn_off("0000", now=t0 + 401), 403) is not None
              and valet.config()["fails"] == 1)
        check("after the cooldown the right PIN works", valet.turn_off("2468", now=t0 + 402)["on"] is False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print()
    print(f"  {'FAILED: %d' % fails if fails else 'every valet check holds'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
