#!/usr/bin/env python3
"""The demo's car: a drive built from a route, and the world that plays it.

The demo has no car in the room, so everything a car would say -- speed, revs,
the pack, where it is, what the next turn is -- comes from one scripted drive
played back in real time. Three things here would fail QUIETLY rather than
loudly, which is the only reason they are worth a test.

The first is pacing. The simulator's loop counts its own sleeps, so a slow
machine or a long sleep makes its clock run slow by a factor nobody notices
until a song and a turn banner disagree. The demo's clock is the elapsed real
time, and the check that matters is that ten seconds of it moves the drive
ten seconds however many steps it was cut into.

The second is the physics. A drive that looks right in the map but has the
car go from 0 to 100 km/h in three seconds is wrong in a way every person in
a room of car people will feel. So the built drive is held to real limits on
acceleration and braking, at every second, not just on average.

The third is the silo. The demo's car must never reach the adapter, the
recorder's cameras, the sound system or a service, and the surest guard is
that the module cannot: it has no code that would do any of it, and a test
reads its source to keep it that way.

Every state directory here is a scratch one, and no clock is real.
"""

import atexit
import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "test", "fixtures", "demo")

# The scratch state, set BEFORE anything from lib/ is imported: garage and
# records resolve their folders when they are imported, and importing them
# creates the garage folder.
TMP = tempfile.mkdtemp(prefix="omacar-demoworld-test-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
STATE = os.path.join(TMP, "state", "omacar")
os.environ["HOME"] = TMP
os.environ["XDG_STATE_HOME"] = os.path.join(TMP, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["OMACAR_STATE"] = STATE
os.makedirs(STATE, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "lib"))

PASS, FAIL = [], []


def ok(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   {'ok ' if cond else 'FAIL'}  {name}")


def head(name):
    print(f"\n  {name}\n")


def load_tool(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


demo_route = load_tool("demo_route")
import demoworld  # noqa: E402

with open(os.path.join(FIXTURES, "osrm-mini.json"), encoding="utf-8") as f:
    OSRM = json.load(f)
with open(os.path.join(FIXTURES, "drive-mini.json"), encoding="utf-8") as f:
    MINI = json.load(f)                    # the same route, a 300 s loop

LOOP = MINI["loop_secs"]
DRIVING = LOOP - 120
MS = 1 / 3.6

# ---- the drive builder --------------------------------------------------------
head("tools/demo_route.py: the drive")

built = demo_route.build(OSRM, loop_secs=300)
pts = built["points"]

ok("the committed mini drive is what the tool makes from the mini route",
   built == MINI)
ok("version, name and destination are as the contract says",
   built["version"] == 1
   and built["name"] == "Marina to San Francisco on Highway 1"
   and built["destination"] == {"label": "Omarchy Meetup, San Francisco",
                                "lat": 37.7793, "lon": -122.4193})
ok("points run at 1 Hz from t=0 to loop_secs, seven columns each",
   len(pts) == 301 and all(p[0] == i and len(p) == 7 for i, p in enumerate(pts)))
ok("the route is about three kilometres",
   2900 < built["route_total_m"] < 3100)
ok("speed never goes above 105 km/h", max(p[3] for p in pts) <= 105.0)

mps = [p[3] * MS for p in pts]
gains = [b - a for a, b in zip(mps, mps[1:])]
ok(f"acceleration never goes above 2.0 m/s^2 (peak {max(gains):.2f})",
   max(gains) <= 2.0)
ok(f"braking never goes above 2.5 m/s^2 (peak {-min(gains):.2f})",
   -min(gains) <= 2.5)
ok("the car does move, and gets past 40 km/h",
   max(p[3] for p in pts) > 40.0)

tail = pts[DRIVING:]
ok("the last 120 s are parked: stopped, and not moving an inch",
   all(p[3] == 0 for p in tail) and len({p[5] for p in tail}) == 1
   and len({(p[1], p[2]) for p in tail}) == 1)
ok("the hold is declared as a scene",
   built["scenes"] == [{"t": DRIVING, "kind": "parked", "secs": 120}])
ok("one scripted hard brake, a third of the way through the loop",
   built["events"] == [{"t": 100, "kind": "hard_brake"}])
ok("it leaves from rest, and route_m never goes backwards",
   pts[0][3] == 0 and pts[0][5] == 0
   and all(b[5] >= a[5] for a, b in zip(pts, pts[1:])))

# The first traffic-light-like manoeuvre is a stop of 25 s, a stop line short
# of the turn.
turn_m = next(m["route_m"] for m in built["maneuvers"] if m["type"] == "turn")
zero_runs, run = [], 0
for p in pts[:DRIVING + 1]:
    if p[3] == 0:
        run += 1
    else:
        if run:
            zero_runs.append(run)
        run = 0
zero_runs.append(run)
stop_i = next(i for i, p in enumerate(pts) if i > 5 and p[3] == 0)
ok("the car stops at the first turn for 25 s, and no longer",
   any(25 <= r <= 27 for r in zero_runs))
ok("and is stopped at no other time but the start and the loop's end",
   len(zero_runs) == 3 and zero_runs[0] == 4 and zero_runs[2] == 1)
ok("and it stops just short of the turn, not at the start",
   0 < turn_m - pts[stop_i][5] < 15)
ok("it eases in and out of that stop rather than dropping to it",
   pts[stop_i - 1][3] > 0 and pts[stop_i - 2][3] > pts[stop_i - 1][3])

# Manoeuvres.
mv = built["maneuvers"]
ok("depart is not a manoeuvre; the turn, the merge and the arrival are",
   [m["type"] for m in mv] == ["turn", "merge", "arrive"])
ok("the first manoeuvre has an instruction", mv[0]["instruction"].strip() != "")
ok("a turn reads as a turn, with the street's short name",
   mv[0]["instruction"] == "Turn left onto Imjin Pkwy"
   and mv[0]["modifier"] == "left" and mv[0]["street"] == "Imjin Pkwy")
ok("a merge onto a numbered road names it with a direction",
   mv[1]["instruction"] == "Merge onto CA-1 N" and mv[1]["street"] == "CA-1 N")
ok("the last one arrives at the destination's name",
   mv[2]["instruction"] == "Arrive at Omarchy Meetup"
   and abs(mv[2]["route_m"] - built["route_total_m"]) < 1.0)
ok("each manoeuvre is where its step is along the route",
   abs(mv[0]["route_m"] - 402.3) < 2.0 and abs(mv[1]["route_m"] - 1304.0) < 3.0)
ok("streets are where each road starts, the first from the depart's destination",
   built["streets"][0] == [0.0, "Reservation Rd"]
   and [s[1] for s in built["streets"]] == ["Reservation Rd", "Imjin Pkwy", "CA-1 N"])
ok("the route is the geometry simplified: the same ends, fewer points",
   built["route"][0] == [36.696, -121.806]
   and built["route"][-1] == [36.70776, -121.795917]
   and 2 < len(built["route"]) < 31)

# Headings and positions, from the route.
ok("it faces south to start, on a road that runs south",
   abs(pts[0][4] - 180.0) < 2.0)
north = pts[DRIVING - 5][4]
ok("and turns east onto the second road, and north onto the third",
   abs(pts[stop_i + 40][4] - 90.0) < 3.0 and min(north, 360.0 - north) < 3.0)
ok("remaining seconds are the route's own, less as it goes",
   abs(pts[0][6] - 169.2) < 1.0 and pts[DRIVING][6] < pts[0][6]
   and all(b[6] <= a[6] for a, b in zip(pts, pts[1:])))

# A longer loop gets to the end and arrives.
long_drive = demo_route.build(OSRM, loop_secs=600)
lp = long_drive["points"]
ok("given time enough, it reaches the destination and stops there",
   abs(lp[-1][5] - long_drive["route_total_m"]) < 1.0 and lp[-1][3] == 0
   and max(p[3] for p in lp) > 80.0)
ok("even then, speed and braking hold",
   max(b[3] - a[3] for a, b in zip(lp, lp[1:])) * MS <= 2.0
   and max(a[3] - b[3] for a, b in zip(lp, lp[1:])) * MS <= 2.5)

# The check the tool runs on what it made.
ok("the tool finds nothing wrong with the drives it makes",
   demo_route.problems(built) == [] and demo_route.problems(long_drive) == [])
fast = json.loads(json.dumps(built))
fast["points"][50][3] = 140.0
jerk = json.loads(json.dumps(built))
jerk["points"][100][3] = jerk["points"][99][3] + 20.0
lost = json.loads(json.dumps(built))
lost["points"] = lost["points"][:-5]
moving = json.loads(json.dumps(built))
moving["points"][-1][5] += 30.0
ok("and finds a speed over 105, a jerk, a missing second, and a car that will not park",
   all(demo_route.problems(d) for d in (fast, jerk, lost, moving)))

# Douglas-Peucker.
zig = [[0.0, 0.0]]
for i in range(1, 61):
    zig.append([i * 0.0001, (0.00018 if i % 10 == 5 else 0.0)])   # bumps of ~20 m
flat = [[i * 0.0001, (0.00001 if i % 2 else 0.0)] for i in range(61)]  # ~1 m
ok("simplifying keeps a 20 m bump and drops a 1 m wobble, at 5 m",
   len(demo_route.simplify(zig, 5.0)) > 10
   and len(demo_route.simplify(flat, 5.0)) == 2)
ok("and it keeps the ends",
   demo_route.simplify(zig, 5.0)[0] == zig[0]
   and demo_route.simplify(zig, 5.0)[-1] == zig[-1])
ok("a long dense line does not hit the recursion limit",
   len(demo_route.simplify([[i * 0.00001, 0.0] for i in range(20000)], 5.0)) == 2)

# The command line.
out = os.path.join(TMP, "drive-out.json")
cli = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "demo_route.py"),
     "--osrm", os.path.join(FIXTURES, "osrm-mini.json"), "--out", out,
     "--loop-secs", "300"], capture_output=True, text=True)
ok("the command line writes the same drive", cli.returncode == 0
   and json.load(open(out, encoding="utf-8")) == MINI)
ok("and says what it made", "max speed" in cli.stdout)
bad = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "demo_route.py"),
     "--osrm", os.path.join(TMP, "nope.json"), "--out", out],
    capture_output=True, text=True)
ok("a missing route is an error, not a stack trace",
   bad.returncode != 0 and "Traceback" not in bad.stderr)

# ---- state_at -----------------------------------------------------------------
head("demoworld.state_at: what the car says at one moment")

NOW = 1_780_000_000.0
s0 = demoworld.state_at(MINI, 0, {"now": NOW})
v0, d0 = s0["values"], s0["demo"]
ok("at the start it is stopped: 0 km/h, and 0 rpm, because it auto-stops",
   v0["SPEED"] == 0 and v0["RPM"] == 0)
ok("it is the same payload the simulator publishes, with the demo block",
   {"connected", "simulated", "port", "kind", "protocol", "supported",
    "values", "economy_lphk", "fuel_lph", "efficiency", "efficiency_basis",
    "odometer_km", "trip", "demo"} <= set(s0)
   and s0["connected"] is True and s0["simulated"] is True)
ok("and the pack is a reading the app will draw, not one it marks absent",
   "HYBRID_BATTERY_REMAINING" in s0["supported"]
   and v0["HYBRID_BATTERY_REMAINING"] == 58.0)
ok("the start is stopped: ima says stop, and no kilowatts",
   d0["ima"] == {"state": "stop", "kw": 0.0})
ok("it is a drive scene, not parked, on the depart's road",
   d0["scene"] == "drive" and d0["parked"] is False
   and d0["street"] == "Reservation Rd" and d0["event"] is None)
ok("the next turn is the first manoeuvre ahead of it",
   d0["next"]["instruction"] == "Turn left onto Imjin Pkwy"
   and d0["next"]["type"] == "turn" and d0["next"]["modifier"] == "left"
   and d0["next"]["street"] == "Imjin Pkwy"
   and abs(d0["next"]["in_m"] - 402.3) < 2.0)
ok("its position is the route's start, facing south",
   abs(d0["lat"] - 36.696) < 1e-6 and abs(d0["lon"] + 121.806) < 1e-6
   and abs(d0["heading"] - 180.0) < 2.0 and d0["route_m"] == 0.0)
ok("it says how long the loop is, and how far there is to go",
   d0["loop_secs"] == 300 and d0["t"] == 0.0
   and abs(d0["remaining_m"] - MINI["route_total_m"]) < 1.0)
ok("the ETA is now plus the seconds left", abs(d0["eta"] - (NOW + 169.2)) < 1.0)
ok("state_at is a function: the same moment gives the same answer",
   demoworld.state_at(MINI, 77.3, {"now": NOW})
   == demoworld.state_at(MINI, 77.3, {"now": NOW}))
ok("time wraps at the loop's end",
   demoworld.state_at(MINI, 310.0, {"now": NOW})["demo"]["t"] == 10.0)

# A whole loop, second by second.
rows = [demoworld.state_at(MINI, float(t), {"now": NOW}) for t in range(LOOP)]
moving = [r for r in rows if r["values"]["SPEED"] > 5]
stopped = [r for r in rows if r["values"]["SPEED"] == 0]
ok("while it is moving the engine turns, and while stopped it does not",
   all(r["values"]["RPM"] > 0 for r in moving)
   and all(r["values"]["RPM"] == 0 for r in stopped))
ok("revs stay inside what this engine does",
   max(r["values"]["RPM"] for r in rows) < 6300)
ok("coolant sits between 88 and 91 C",
   all(88.0 <= r["values"]["COOLANT_TEMP"] <= 91.0 for r in rows))
ok("the system voltage is 14.1 to 14.3 driving and 12.5 stopped",
   all(14.1 <= r["values"]["CONTROL_MODULE_VOLTAGE"] <= 14.3 for r in moving)
   and all(r["values"]["CONTROL_MODULE_VOLTAGE"] == 12.5 for r in stopped))
ok("fuel starts at 62 and falls slowly",
   rows[0]["values"]["FUEL_LEVEL"] == 62.0
   and 55 < rows[-1]["values"]["FUEL_LEVEL"] <= 62.0)
ok("engine load and throttle are percentages",
   all(0 <= r["values"]["ENGINE_LOAD"] <= 100
       and 0 <= r["values"]["THROTTLE_POS"] <= 100 for r in rows))
push = max(rows, key=lambda r: r["values"]["ENGINE_LOAD"])
cruise = min((r for r in moving if r["demo"]["ima"]["state"] == "idle"),
             key=lambda r: abs(r["values"]["SPEED"] - 50))
ok("engine load follows acceleration: harder under it than cruising",
   push["values"]["ENGINE_LOAD"] > cruise["values"]["ENGINE_LOAD"] + 15
   and push["values"]["THROTTLE_POS"] > cruise["values"]["THROTTLE_POS"])
ok("the ima reads assist on the way up, charge on the way down, idle between",
   {"assist", "charge", "idle", "stop"} == {r["demo"]["ima"]["state"] for r in rows})
ok("assist and charge carry kilowatts, idle and stop do not",
   all(r["demo"]["ima"]["kw"] > 0 for r in rows
       if r["demo"]["ima"]["state"] in ("assist", "charge"))
   and all(r["demo"]["ima"]["kw"] == 0 for r in rows
           if r["demo"]["ima"]["state"] in ("idle", "stop")))
ok("assist is never more than the motor can give",
   max(r["demo"]["ima"]["kw"] for r in rows) <= 10.0)

# Manoeuvre distance.
falling = True
last = None
for r in rows:
    n = r["demo"]["next"]
    if n is None:
        last = None
        continue
    key = (n["instruction"], round(n["in_m"] + r["demo"]["route_m"]))
    if last and last[0] == key:
        falling = falling and n["in_m"] <= last[1] + 1e-6
    last = (key, n["in_m"])
ok("the distance to the next manoeuvre only falls until it is passed", falling)

# The loop parks at its end, short of the arrival on the short loop.
end = demoworld.state_at(MINI, 250.0, {"now": NOW})
ok("in the closing hold the scene is parked, and the pack says stop",
   end["demo"]["scene"] == "parked" and end["demo"]["parked"] is True
   and end["values"]["RPM"] == 0 and end["demo"]["ima"]["state"] == "stop")
ok("and the next thing is still the arrival, where it was",
   end["demo"]["next"]["type"] == "arrive"
   and abs(end["demo"]["next"]["in_m"] - (MINI["route_total_m"] - end["demo"]["route_m"])) < 0.2)

# The gears, on the drive that reaches the highway.
LONG = long_drive
arrived = demoworld.state_at(LONG, 590.0, {"now": NOW})["demo"]
ok("having arrived there is no next manoeuvre",
   arrived["next"] is None and arrived["remaining_m"] == 0.0)
hi = [demoworld.state_at(LONG, float(t), {"now": NOW}) for t in range(60, 480)]
top = [r for r in hi if r["values"]["SPEED"] > 60 and r["demo"]["ima"]["state"] == "idle"]
ok("above 60 km/h it is in sixth: 750 rpm plus 28 for every km/h",
   top and all(abs(r["values"]["RPM"] - (750 + r["values"]["SPEED"] * 28)) <= 1
               for r in top))
ok("under 60 it is in a lower gear, so the revs are higher for the speed",
   all(r["values"]["RPM"] > 750 + r["values"]["SPEED"] * 28 + 100
       for r in hi if 5 < r["values"]["SPEED"] < 55))

# A cue overrides the speed it is given, and nothing else moves.
over = demoworld.state_at(MINI, 120.0, {"now": NOW, "kph": 0.0, "accel": 0.0,
                                        "hold": True})
under = demoworld.state_at(MINI, 120.0, {"now": NOW})
ok("an overridden speed is the speed, and the position is still the loop's",
   over["values"]["SPEED"] == 0 and over["values"]["RPM"] == 0
   and over["demo"]["lat"] == under["demo"]["lat"]
   and over["demo"]["route_m"] == under["demo"]["route_m"])
ok("held at a stop, the scene is parked",
   over["demo"]["scene"] == "parked" and over["demo"]["parked"] is True)
dz = demoworld.state_at(MINI, 120.0, {"now": NOW, "drowsy": True})
ok("drowsy is a scene while it is moving, and parked outranks it",
   dz["demo"]["scene"] == "drowsy"
   and demoworld.state_at(MINI, 120.0, {"now": NOW, "drowsy": True, "kph": 0.0,
                                        "hold": True})["demo"]["scene"] == "parked")
ev = {"kind": "hard_brake", "at": NOW}
ok("an event in flight is reported as it was given",
   demoworld.state_at(MINI, 120.0, {"now": NOW, "event": ev})["demo"]["event"] == ev)


# ---- the world ----------------------------------------------------------------
head("demoworld.DemoWorld: the drive played in real time")


class Clock:
    """A clock that only moves when told to, and a wall clock that follows it."""

    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t

    def wall(self):
        return 1_780_000_000.0 + self.t


def make(drive=MINI, cue_path=None):
    c = Clock()
    return demoworld.DemoWorld(drive, clock=c, wall=c.wall, cue_path=cue_path), c


def go(world, clock, secs, dt=0.2, keep=False):
    """Advance `secs` in steps of `dt`; the last payload, or every one."""
    out, last = [], None
    for _ in range(int(round(secs / dt))):
        clock.t += dt
        last = world.step()
        if keep:
            out.append(last)
    return out if keep else last


def at_loop_time(drive, target):
    """A fresh world, stepped to `target` s into the loop."""
    world, clock = make(drive)
    world.step()
    return world, clock, go(world, clock, target, dt=0.5)


def speed(p):
    return p["values"]["SPEED"]


def per_second(kphs, dt):
    """Changes in speed over one second, m/s^2, from speeds `dt` apart. The
    payload rounds speed to 0.1 km/h, which over a fifth of a second is worth
    0.3 m/s^2 and over a second is worth 0.06, so it is read a second at a time."""
    k = int(round(1.0 / dt))
    return [(b - a) * MS for a, b in zip(kphs, kphs[k:])]


def loop_t(p):
    return p["demo"]["t"]


# The mini drive's own timetable, read off it, so the cues are tried while the
# car is on the second road and not in the middle of a stop.
STOP_END = stop_i + 26
CRUISE = float(STOP_END + 15)
# The same drive without its scripted hard brake, for the tests of the cues.
QUIET = dict(MINI, events=[])

# Pacing.
w, c = make()
first = w.step()
for _ in range(50):
    c.t += 0.2
    p = w.step()
ok(f"ten seconds in fifty steps moves the drive ten seconds "
   f"({loop_t(p) - loop_t(first):.3f})",
   abs(loop_t(p) - loop_t(first) - 10.0) <= 0.05)
w, c = make()
w.step()
c.t += 5.0
w.step()
c.t += 5.0
ok("and in two steps of five it is the same: it is time, not a step count",
   abs(loop_t(w.step()) - 10.0) <= 0.05)
w, c = make(QUIET)
w.step()
c.t += 200.0
ok("a stall of 200 s does not lose the time either",
   abs(loop_t(w.step()) - 200.0) <= 0.05)
w, c = make(QUIET)
w.step()
c.t += 299.0
a = w.step()
c.t += 2.0
b = w.step()
ok("wrapping is seamless: 299 s, then 301 s, is 1 s into the next loop",
   abs(loop_t(a) - 299.0) <= 0.05 and abs(loop_t(b) - 1.0) <= 0.05)

# The payload the world publishes.
w, c = make()
p = w.step()
ok("the world's payload is state_at's, plus the wall time and the uptime",
   p["t"] == c.wall() and p["uptime"] == 0.0
   and p["values"]["SPEED"] == 0 and "demo" in p)
c.t += 30.0
p = w.step()
ok("uptime and run time are elapsed real time",
   abs(p["uptime"] - 30.0) < 1e-6 and p["values"]["RUN_TIME"] == 30)
ok("the ETA is the wall clock plus the seconds left",
   abs(p["demo"]["eta"] - (c.wall() + MINI["points"][30][6])) < 3.0)

# The pack.
w, c = make(QUIET)
packs = [w.step()["values"]["HYBRID_BATTERY_REMAINING"]]
for _ in range(int(LOOP * 2 / 0.2)):
    c.t += 0.2
    packs.append(w.step()["values"]["HYBRID_BATTERY_REMAINING"])
ok("the pack starts at 58", packs[0] == 58.0)
ok(f"and stays within 40 to 78 over two whole loops at 0.2 s "
   f"({min(packs):.1f}..{max(packs):.1f})", all(40.0 <= x <= 78.0 for x in packs))
ok("it falls under assist and rises under regen and cruising",
   min(packs) < 58.0 and max(packs) > 58.0)

w, c = make(QUIET)
w.step()
seen = {}
prev = 58.0
for _ in range(int(LOOP / 0.2)):
    c.t += 0.2
    p = w.step()
    now_soc = p["values"]["HYBRID_BATTERY_REMAINING"]
    seen.setdefault(p["demo"]["ima"]["state"], []).append((now_soc - prev) / 0.2)
    prev = now_soc
avg = {k: sum(v) / len(v) for k, v in seen.items()}
ok(f"assist takes 0.05 %/s ({avg['assist']:+.3f})", abs(avg["assist"] + 0.05) < 0.005)
ok(f"charge gives 0.06 %/s ({avg['charge']:+.3f})", abs(avg["charge"] - 0.06) < 0.005)
ok(f"cruising gives 0.005 %/s ({avg['idle']:+.4f})", abs(avg["idle"] - 0.005) < 0.001)
ok(f"and standing still gives nothing ({avg['stop']:+.4f})", abs(avg["stop"]) < 0.001)

# The odometer and the fuel move with the miles.
w, c = make(QUIET)
a = w.step()
b = go(w, c, 100.0)
ok("the odometer counts the distance it has driven, and the tank the fuel",
   b["odometer_km"] > a["odometer_km"]
   and b["values"]["FUEL_LEVEL"] < a["values"]["FUEL_LEVEL"])

# park, then drive.
w, c, p = at_loop_time(QUIET, CRUISE)
before = speed(p)
ok("on the second road it is moving", before > 30)
w.cue("park")
trail = go(w, c, 20.0, keep=True)
speeds = [before] + [speed(x) for x in trail]
ok("park: it is at 0 within 20 s", speed(trail[-1]) == 0)
ok("and the scene says parked",
   trail[-1]["demo"]["scene"] == "parked" and trail[-1]["demo"]["parked"] is True)
worst = -min(per_second(speeds, 0.2))
ok(f"it brakes at no more than 2.5 m/s^2 ({worst:.2f}), and does slow down",
   2.2 < worst <= 2.5 + 0.06)
ok("it is not called parked while it is still rolling",
   all(x["demo"]["scene"] != "parked" for x in trail if speed(x) > 0))
held_at = loop_t(trail[-1])
held = go(w, c, 30.0, dt=0.5, keep=True)
ok("held, it stays stopped and parked, and the loop clock waits",
   all(speed(x) == 0 and x["demo"]["scene"] == "parked" for x in held)
   and all(abs(loop_t(x) - held_at) < 0.01 for x in held))
ok("a hold does not move the car, or the turn banner",
   len({x["demo"]["route_m"] for x in held}) == 1
   and len({x["demo"]["next"]["in_m"] for x in held}) == 1)
w.cue("drive")
after = go(w, c, 3.0, keep=True)
climb = [speed(x) for x in after]
ok("drive: it pulls away from where it stopped, and the scene is drive again",
   climb[-1] > 10 and after[-1]["demo"]["scene"] == "drive"
   and after[-1]["demo"]["route_m"] > held[-1]["demo"]["route_m"])
ok("at no more than 2.0 m/s^2, and no less than most of it",
   1.7 <= max(per_second([0.0] + climb, 0.2)) <= 2.0 + 0.06)
later = go(w, c, 30.0, keep=True)
base_now = speed(demoworld.state_at(QUIET, loop_t(later[-1]), {"now": NOW}))
ok(f"and it is back on the drive's own speed within 30 s "
   f"({speed(later[-1]):.1f} vs {base_now:.1f})",
   abs(speed(later[-1]) - base_now) < 1.0)
ok("the loop clock carried on from the point it was paused at",
   loop_t(later[-1]) > held_at + 5.0)
ok("with no jump in speed where it rejoins",
   max(abs(x) for x in per_second([speed(x) for x in later], 0.2)) <= 2.0 + 0.06)

# Parked at the loop's natural end, a `drive` cue leaves early.
w, c, p = at_loop_time(QUIET, 200.0)
ok("in the closing hold the scene is parked", p["demo"]["scene"] == "parked")
w.cue("drive")
c.t += 0.5
p = w.step()
ok("a drive cue there starts the loop over, not a standing hold",
   loop_t(p) < 1.0 and p["demo"]["scene"] == "drive")
w.cue("park")
p = go(w, c, 3.0)
ok("and a park cue there holds where it is",
   p["demo"]["scene"] == "parked" and speed(p) == 0 and p["demo"]["route_m"] < 5.0)

# Drowsy.
w, c, p = at_loop_time(QUIET, CRUISE)
w.cue("drowsy")
scenes = []
for i in range(100):
    c.t += 0.5
    p = w.step()
    scenes.append((0.5 * (i + 1), p["demo"]["scene"], speed(p)))
ok("drowsy: the scene is drowsy for 45 s",
   all(s == "drowsy" for t, s, v in scenes if t < 44.9))
ok("and back to driving after it",
   all(s == "drive" for t, s, v in scenes if t > 45.1))
ok("with the car moving all the way through",
   all(v > 0 for t, s, v in scenes if t <= 45.0))

# Hard braking.
w, c, p = at_loop_time(QUIET, CRUISE)
v_before = speed(p)
cued_at = c.wall()
w.cue("hard_brake")
trail = go(w, c, 15.0, dt=0.1, keep=True)
speeds = [v_before] + [speed(x) for x in trail]
worst = -min(per_second(speeds, 0.1))
ok(f"hard brake: at 8 m/s^2 ({worst:.2f})", 7.5 <= worst <= 8.0 + 0.06)
ok("so in two seconds it has shed all it had, or 16 m/s",
   speed(trail[19]) <= max(0.0, v_before - 16 / MS + 1.0))
flags = [x["demo"]["event"] for x in trail]
ok("it sets the event for 10 s, stamped with when it happened",
   all(e and e["kind"] == "hard_brake" and e["at"] == cued_at for e in flags[:99])
   and all(e is None for e in flags[100:]))
rises = per_second(speeds[25:], 0.1)
ok("it recovers, at no more than 2.0 m/s^2, and not slowly",
   1.5 <= max(rises) <= 2.0 + 0.06)
base_now = speed(demoworld.state_at(QUIET, loop_t(trail[-1]), {"now": NOW}))
ok(f"and is back on the drive's speed within 15 s "
   f"({speed(trail[-1]):.1f} vs {base_now:.1f})", abs(speed(trail[-1]) - base_now) < 1.0)
ok("having lost some distance: its place in the loop is behind the clock",
   loop_t(trail[-1]) < CRUISE + 15.0 - 1.0)

# Cues act on a car that is doing something else already.
w, c, p = at_loop_time(QUIET, CRUISE)
w.cue("park")
go(w, c, 1.0)
w.cue("hard_brake")
p = go(w, c, 2.0)
ok("a hard brake while braking to park is still a stop",
   p["demo"]["event"] is not None and speed(p) < 40)
w.cue("drive")
p = go(w, c, 30.0)
ok("and drive gets it going again", speed(p) > 30 and p["demo"]["parked"] is False)

# Getting back onto the drive is certain, not just likely.
demoworld.RECOVER_LIMIT, kept = 1.0, demoworld.RECOVER_LIMIT
try:
    w, c, p = at_loop_time(QUIET, CRUISE)
    w.cue("park")
    go(w, c, 10.0)
    w.cue("drive")
    p = go(w, c, 0.6)
    still_own = w.own
    p = go(w, c, 1.5)
finally:
    demoworld.RECOVER_LIMIT = kept
ok("a car that has not caught the drive in RECOVER_LIMIT seconds is put back on it",
   still_own and not w.own and abs(speed(p) - speed(
       demoworld.state_at(QUIET, loop_t(p), {"now": NOW}))) < 0.2)

# The scripted event is the same thing, at its own time.
w, c = make()
w.step()
first_flag = None
dip = None
for _ in range(int(130 / 0.2)):
    c.t += 0.2
    p = w.step()
    if p["demo"]["event"] and first_flag is None:
        first_flag = loop_t(p)
        v_at = speed(p)
    if first_flag and dip is None and speed(p) < v_at - 20:
        dip = speed(p)
ok("the scripted hard brake fires at t=100, with nobody's cue",
   first_flag is not None and 99.5 <= first_flag <= 101.0)
ok("and does what the cue does", dip is not None)
w, c = make()
w.step()
fired = 0
was = False
for _ in range(int(2 * LOOP / 0.2)):
    c.t += 0.2
    p = w.step()
    now_flag = p["demo"]["event"] is not None
    fired += now_flag and not was
    was = now_flag
ok("and again on the next loop, once each time", fired == 2)

# Restart.
w, c, p = at_loop_time(QUIET, CRUISE)
w.cue("restart")
c.t += 0.2
p = w.step()
ok("restart: the loop clock is back at the start, and so is the car",
   loop_t(p) < 0.5 and p["demo"]["route_m"] < 5.0)
w.cue("park")
go(w, c, 1.0)
w.cue("restart")
p = go(w, c, 4.0)
ok("restart releases a hold as well", p["demo"]["parked"] is False and loop_t(p) > 3.0)

# The cue file: each `at` acted on once.
cue_path = os.path.join(TMP, "demo-cue.json")


def write_cue(cue, at):
    with open(cue_path, "w", encoding="utf-8") as f:
        json.dump({"cue": cue, "at": at}, f)


def cruising_with_file():
    """A world reading an empty cue_path, at CRUISE. (The file outlives a
    world, so each starts by clearing it.)"""
    if os.path.exists(cue_path):
        os.remove(cue_path)
    world, clock = make(QUIET, cue_path=cue_path)
    world.step()
    return world, clock, go(world, clock, CRUISE, dt=0.5)


if os.path.exists(cue_path):
    os.remove(cue_path)
write_cue("drowsy", 1_780_000_000.0 + 5000.0 - 10.0)      # older than the world
w, c = make(QUIET, cue_path=cue_path)
w.step()
c.t += 0.5
ok("a cue written before the world started is not acted on",
   w.step()["demo"]["scene"] == "drive")
w, c, p = cruising_with_file()
write_cue("drowsy", c.wall() + 0.1)
c.t += 0.5
ok("a new cue is acted on", w.step()["demo"]["scene"] == "drowsy")
p = go(w, c, 46.0, dt=0.5)
ok("and only once: the file is still there, and the moment is over",
   p["demo"]["scene"] == "drive")
write_cue("hard_brake", c.wall() + 0.1)
c.t += 0.5
ok("a later cue in the same file is acted on too",
   w.step()["demo"]["event"] is not None)

w, c, p = cruising_with_file()
with open(cue_path, "w", encoding="utf-8") as f:
    f.write('{"cue": "drow')
c.t += 0.5
p = w.step()
ok("a half-written cue file is ignored, not fatal",
   p["demo"]["scene"] == "drive" and speed(p) > 0)
write_cue("self_destruct", c.wall() + 0.1)
c.t += 0.5
p = w.step()
ok("an unknown cue is ignored", p["demo"]["scene"] == "drive" and speed(p) > 0)
write_cue("park", c.wall() + 0.1)
p = go(w, c, 10.0, dt=0.5)
ok("and it takes the park cue from the file, as the server writes it",
   speed(p) == 0 and p["demo"]["parked"] is True)

# Where the drive comes from.
fake_root = os.path.join(TMP, "root")
os.makedirs(os.path.join(fake_root, "test", "fixtures", "demo"))
with open(os.path.join(fake_root, "test", "fixtures", "demo", "drive-mini.json"),
          "w") as f:
    json.dump(MINI, f)
path, warn = demoworld.resolve_drive(None, root=fake_root)
ok("with no built drive it falls back to the fixture, and says so",
   path.endswith("drive-mini.json") and warn and "drive-mini.json" in warn)
os.makedirs(os.path.join(fake_root, "share", "assets", "private", "demo"))
with open(os.path.join(fake_root, "share", "assets", "private", "demo",
                       "drive.json"), "w") as f:
    json.dump(MINI, f)
path, warn = demoworld.resolve_drive(None, root=fake_root)
ok("with one it plays that, without a warning",
   path.endswith(os.path.join("private", "demo", "drive.json")) and warn is None)
path, warn = demoworld.resolve_drive("/some/where.json", root=fake_root)
ok("a drive named on the command line is used as it is",
   path == "/some/where.json" and warn is None)

# ---- the process --------------------------------------------------------------
head("demoworld.py run: the process")

env = dict(os.environ)
env["OMACAR_STATE"] = STATE
proc = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "lib", "demoworld.py"), "run",
     "--drive", os.path.join(FIXTURES, "drive-mini.json")],
    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
live_path = os.path.join(STATE, "live.json")
live = None
deadline = time.time() + 15
while time.time() < deadline:
    try:
        with open(live_path, encoding="utf-8") as f:
            live = json.load(f)
        break
    except (OSError, ValueError):
        time.sleep(0.1)
ok("it writes live.json", live is not None)
ok("as the simulated car, with the demo block and the pack",
   live and live["simulated"] is True and live["connected"] is True
   and live["vehicle"] == "simulated" and "demo" in live
   and "HYBRID_BATTERY_REMAINING" in live["values"])
pidfile = os.path.join(STATE, "sim.pid")
ok("and sim.pid, so `omacar demo status` and the daemon's refusal still work",
   os.path.exists(pidfile) and open(pidfile).read().strip() == str(proc.pid))
import records  # noqa: E402  (the app's own reader, on the same scratch folder)
seen = records.live()
ok("the app's own reader accepts the sample, demo block and all",
   seen.get("connected") is True and "demo" in seen
   and records.status(seen) in ("driving", "parked"))
t1 = live["demo"]["t"] if live else 0
time.sleep(1.2)
with open(live_path, encoding="utf-8") as f:
    live2 = json.load(f)
ok(f"it publishes at about 5 Hz, and the loop clock is real time "
   f"({live2['demo']['t'] - t1:.2f} s in 1.2 s)",
   0.8 <= live2["demo"]["t"] - t1 <= 1.9)
with open(os.path.join(STATE, "demo-cue.json"), "w", encoding="utf-8") as f:
    json.dump({"cue": "drowsy", "at": time.time() + 0.2}, f)
time.sleep(1.5)
with open(live_path, encoding="utf-8") as f:
    live3 = json.load(f)
ok("it takes a cue from $OMACAR_STATE/demo-cue.json", live3["demo"]["scene"] == "drowsy")
proc.send_signal(signal.SIGTERM)
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
ok("SIGTERM ends it cleanly", proc.returncode in (0, -signal.SIGTERM))
with open(live_path, encoding="utf-8") as f:
    gone = json.load(f)
ok("it says it has stopped, and removes sim.pid",
   gone.get("connected") is False and gone.get("status") == "stopped"
   and not os.path.exists(pidfile))
ok("it wrote nothing outside its state folder",
   sorted(os.listdir(os.path.dirname(STATE))) == ["omacar"]
   and not os.path.exists(os.path.join(TMP, "config")))

nope = subprocess.run(
    [sys.executable, os.path.join(ROOT, "lib", "demoworld.py"), "run",
     "--drive", os.path.join(TMP, "nope.json")],
    env=env, capture_output=True, text=True, timeout=20)
ok("a drive that is not there is an error, said plainly",
   nope.returncode == 2 and "nope.json" in nope.stderr
   and "Traceback" not in nope.stderr)

# ---- the silo -----------------------------------------------------------------
head("the silo: the module has no way to reach the car")

with open(os.path.join(ROOT, "lib", "demoworld.py"), encoding="utf-8") as f:
    src = f.read()
for word in ("serial", "import connect", "import elm", "import daemon",
             "systemctl", "wpctl", "pactl", "/dev/"):
    ok(f"lib/demoworld.py never mentions {word!r}", word not in src)

ok("importing it did not load the adapter's modules",
   all(m not in sys.modules for m in ("connect", "elm", "daemon", "serial")))

print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
if FAIL:
    print("  failed:")
    for n in FAIL:
        print(f"    - {n}")
    print()
sys.exit(1 if FAIL else 0)
