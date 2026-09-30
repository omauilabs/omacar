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
# The world only runs in a folder of the demo's. These are scratch folders, so
# this lifts that, and the tests of the refusal take it away again.
os.environ["OMACAR_DEMO_ALLOW_ANY"] = "1"
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
# The mini drive scripts no hard brake, as the real one does not. The tests of
# the scripted event build it in, so they still run.
SCRIPTED = dict(MINI, events=[{"t": 100, "kind": "hard_brake"}])
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
ok("no scripted hard brake by default: the tour cues its own, and so does B",
   built["events"] == [])
scripted = demo_route.build(OSRM, loop_secs=300, hard_brake_at=100)
ok("hard_brake_at puts one back, at the second it is given, and changes nothing else",
   scripted["events"] == [{"t": 100, "kind": "hard_brake"}]
   and dict(scripted, events=[]) == built)
ok("it leaves from rest, and route_m never goes backwards",
   pts[0][3] == 0 and pts[0][5] == 0
   and all(b[5] >= a[5] for a, b in zip(pts, pts[1:])))

# The first traffic-light-like manoeuvre is a stop of 25 s, a stop line short
# of the turn.
turn_m = next(m["route_m"] for m in built["maneuvers"] if m["type"] == "end of road")
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
ok("neither the depart nor the exit from the roundabout is a manoeuvre",
   [m["type"] for m in mv] == ["roundabout", "end of road", "merge", "arrive"])
rotary = json.loads(json.dumps(OSRM))
rotary["routes"][0]["legs"][0]["steps"][2]["maneuver"]["type"] = "exit rotary"
ok("nor is an exit from a rotary",
   [m["type"] for m in demo_route.build(rotary, 300)["maneuvers"]] == [m["type"] for m in mv])
ok("the first manoeuvre has an instruction", mv[0]["instruction"].strip() != "")
ok("a roundabout is taken by its exit, on the road's name and not its county code",
   mv[0]["instruction"] == "At the roundabout, take the exit onto Reservation Rd"
   and mv[0]["street"] == "Reservation Rd" and mv[0]["modifier"] == "right")
ok("the end of a road reads as a turn, with the street's short name",
   mv[1]["instruction"] == "At the end of the road, turn left onto Imjin Pkwy"
   and mv[1]["modifier"] == "left" and mv[1]["street"] == "Imjin Pkwy")
ok("a merge onto a numbered road names it with a direction",
   mv[2]["instruction"] == "Merge onto CA-1 N" and mv[2]["street"] == "CA-1 N")
ok("the last one arrives at the destination's name",
   mv[3]["instruction"] == "Arrive at Omarchy Meetup"
   and abs(mv[3]["route_m"] - built["route_total_m"]) < 1.0)
ok("each manoeuvre is where its step is along the route",
   abs(mv[0]["route_m"] - 201.0) < 2.0 and abs(mv[1]["route_m"] - 402.3) < 2.0
   and abs(mv[2]["route_m"] - 1304.0) < 3.0)
ok("streets are where each road starts, the first from the depart's destination",
   built["streets"][0] == [0.0, "Reservation Rd"]
   and [s[1] for s in built["streets"]] == ["Reservation Rd", "Imjin Pkwy", "CA-1 N"])
ok("the route is the geometry simplified: the same ends, fewer points",
   built["route"][0] == [36.696, -121.806]
   and built["route"][-1] == [36.70776, -121.795917]
   and 2 < len(built["route"]) < 31)

# The words, on their own.
def step(kind, modifier=None, name="", ref=None, bearing=0, exit_n=None, dest=None):
    m = {"type": kind, "bearing_after": bearing, "location": [0, 0]}
    if modifier:
        m["modifier"] = modifier
    if exit_n:
        m["exit"] = exit_n
    out = {"maneuver": m, "name": name}
    if ref:
        out["ref"] = ref
    if dest:
        out["destinations"] = dest
    return out


label = demo_route.road_label
ok("a highway is named by its number, with the direction it is signed",
   label(step("merge", ref="CA 1"), "N") == "CA-1 N"
   and label(step("merge", ref="CA 1"), "S") == "CA-1 S"
   and label(step("merge", ref="CA 92"), "E") == "CA-92 E"
   and label(step("merge", ref="CA 1")) == "CA-1")
ok("a ref that names a direction keeps it",
   label(step("merge", ref="CA 1 North"), "S") == "CA-1 N")
ok("the first of several refs is the one used, and the numbers keep their letters",
   label(step("merge", ref="I 280; CA 35"), "N") == "I-280 N"
   and label(step("off ramp", ref="US 101"), "S") == "US-101 S"
   and label(step("merge", ref="CA 1A"), "N") == "CA-1A N")
ok("a county road's code is not its name",
   label(step("depart", name="Reservation Road", ref="CR G17")) == "Reservation Rd"
   and label(step("depart", name="Reservation Road", ref="CR 12")) == "Reservation Rd"
   and label(step("depart", ref="CR G17")) == "")
ok("streets are written short: Road, Avenue, Parkway, Boulevard, Highway",
   [label(step("turn", name=n)) for n in ("Moss Landing Road", "1st Avenue",
        "Imjin Parkway", "Skyline Boulevard", "Cabrillo Highway")]
   == ["Moss Landing Rd", "1st Ave", "Imjin Pkwy", "Skyline Blvd", "Cabrillo Hwy"])

words = demo_route.instruction
ok("a roundabout: the first exit is 'the exit', the rest are counted",
   [words(step("roundabout", "right", "Reservation Road", exit_n=n), "Reservation Rd", "X")
    for n in (1, 2, 3, 4, 11)] == [
       "At the roundabout, take the exit onto Reservation Rd",
       "At the roundabout, take the 2nd exit onto Reservation Rd",
       "At the roundabout, take the 3rd exit onto Reservation Rd",
       "At the roundabout, take the 4th exit onto Reservation Rd",
       "At the roundabout, take the 11th exit onto Reservation Rd"])
ok("a rotary reads the same, and one with no exit number is entered",
   words(step("rotary", "right", "King Street", exit_n=2), "King St", "X")
   == "At the roundabout, take the 2nd exit onto King St"
   and words(step("roundabout", "left"), "", "X") == "Enter the roundabout"
   and words(step("roundabout turn", "left", "Main Street"), "Main St", "X")
   == "At the roundabout, turn left onto Main St")
ok("the rest of the vocabulary",
   [words(*a, "X") for a in [
       (step("turn", "right"), "1st Ave"), (step("turn", "slight left"), "1st Ave"),
       (step("turn", "uturn"), "Bass Way"), (step("end of road", "left"), "CA-1 N"),
       (step("merge", "slight left"), "CA-1 N"), (step("fork", "slight left"), "CA-1 N"),
       (step("on ramp", "right"), ""), (step("off ramp", "slight right", dest="Imjin Parkway"), ""),
       (step("new name", "straight"), "Cabrillo Hwy"), (step("arrive"), "")]]
   == ["Turn right onto 1st Ave", "Bear left onto 1st Ave", "Make a U-turn onto Bass Way",
       "At the end of the road, turn left onto CA-1 N", "Merge onto CA-1 N",
       "Keep left to stay on CA-1 N", "Take the ramp on the right",
       "Take the exit toward Imjin Pkwy", "Continue onto Cabrillo Hwy",
       "Arrive at X"])

# Which way a highway is signed, from where the route goes.
LAT0, LON0 = 36.6960, -121.8060
M_LAT, M_LON = 110540.0, 111320.0 * math.cos(math.radians(LAT0))


def route_of(*legs):
    """A demo_route.Line through legs of (metres east, metres north), each in
    100 m pieces, from a start in Marina."""
    coords, x, y = [(LAT0, LON0)], 0.0, 0.0
    for dx, dy in legs:
        n = max(1, int(round(math.hypot(dx, dy) / 100.0)))
        for _ in range(n):
            x, y = x + dx / n, y + dy / n
            coords.append((LAT0 + y / M_LAT, LON0 + x / M_LON))
    return demo_route.Line(coords)


side = demo_route.travel_side
ok("north-south: north if the latitude goes up, south if not",
   side(route_of((0, 3000)), 0, "CA", "1") == "N"
   and side(route_of((0, -3000)), 0, "CA", "1") == "S"
   and side(route_of((0, 3000)), 0, "US", "101") == "N")
ok("a highway running west whose latitude goes up over the 5 km is north",
   side(route_of((-750, -270), (1500, 4200)), 0, "CA", "1") == "N"
   and side(route_of((-4000, 800)), 0, "CA", "1") == "N"
   and side(route_of((-4000, -800)), 0, "CA", "1") == "S")
ok("and I 280, which is north-south however it bends, is not made east-west",
   side(route_of((-3000, 200)), 0, "I", "280") == "N")
ok("east-west, in the known set: east if the longitude goes up, west if not",
   side(route_of((3000, 200)), 0, "CA", "92") == "E"
   and side(route_of((-3000, 200)), 0, "CA", "92") == "W"
   and all(side(route_of((-3000, 900)), 0, pre, num) == "W"
           for pre, num in (("CA", "152"), ("CA", "156"), ("CA", "17"), ("CA", "84"),
                            ("CA", "4"), ("CA", "24"), ("I", "580"), ("I", "80"),
                            ("I", "380"))))
# The 5 km, or the route's end if that is nearer.
far = route_of((0, 2000), (0, -6000))
ok("it looks 5 km ahead, and no further",
   side(far, 0, "CA", "1") == "S" and side(route_of((0, 2000), (0, 4000)), 0, "CA", "1") == "N"
   and side(route_of((0, -6000), (0, 9000)), 0, "CA", "1") == "S")
ok("or to the end of the route if that is nearer",
   side(route_of((0, 3000)), 1000, "CA", "1") == "N"
   and side(route_of((0, 3000), (0, -1000)), 2500, "CA", "1") == "S")
north_end, south_end = route_of((0, 3000)), route_of((0, -3000))
ok("a step at the very end is judged by the way it came",
   side(north_end, north_end.total, "US", "101") == "N"
   and side(south_end, south_end.total, "US", "101") == "S"
   and side(north_end, north_end.total - 20, "US", "101") == "N")
ok("no change in latitude is not an increase, and none in longitude is not either",
   side(route_of((3000, 0)), 0, "CA", "1") == "S"
   and side(route_of((0, 3000)), 0, "CA", "92") == "W")


def osrm_of(segments, steps):
    """A one-leg OSRM route through segments of (east m, north m, speed m/s),
    with steps given as (kind, modifier, name, ref, bearing_after, the segment
    it starts, or None for the end)."""
    line = [[LON0, LAT0]]
    starts, ann, x, y = [], {"distance": [], "duration": [], "speed": []}, 0.0, 0.0
    for dx, dy, speed in segments:
        starts.append(len(line) - 1)
        n = max(1, int(round(math.hypot(dx, dy) / 100.0)))
        for _ in range(n):
            x, y = x + dx / n, y + dy / n
            line.append([round(LON0 + x / M_LON, 6), round(LAT0 + y / M_LAT, 6)])
            d = math.hypot(dx, dy) / n
            ann["distance"].append(round(d, 1))
            ann["duration"].append(round(d / speed, 1))
            ann["speed"].append(speed)
    out = []
    for kind, modifier, name, ref, bearing, seg in steps:
        m = {"type": kind, "bearing_after": bearing,
             "location": line[-1 if seg is None else starts[seg]]}
        if modifier:
            m["modifier"] = modifier
        st = {"maneuver": m, "name": name, "mode": "driving", "distance": 0, "duration": 0}
        if ref:
            st["ref"] = ref
        out.append(st)
    total = sum(ann["duration"])
    return {"code": "Ok", "routes": [{
        "geometry": {"type": "LineString", "coordinates": line}, "duration": total,
        "distance": sum(ann["distance"]),
        "legs": [{"steps": out, "annotation": ann, "duration": total,
                  "distance": sum(ann["distance"])}]}]}


# CA 1 runs west-south-west for a kilometre (bearing 250, and south of where it
# joined), then north: the way it does through Santa Cruz on its way to the city.
dip = osrm_of(
    [(0, -400, 11), (-1000, -360, 25), (0, 3500, 25)],
    [("depart", "right", "Reservation Road", "CR G17", 180, 0),
     ("merge", "slight left", "Cabrillo Highway", "CA 1", 250, 1),
     ("arrive", None, "Cabrillo Highway", "CA 1", 0, None)])
dm = demo_route.build(dip, 300)["maneuvers"]
ok("CA 1 heading west, whose latitude still rises over the 5 km, reads north",
   dip["routes"][0]["legs"][0]["steps"][1]["maneuver"]["bearing_after"] == 250
   and dm[0]["instruction"] == "Merge onto CA-1 N" and dm[0]["street"] == "CA-1 N"
   and dm[1]["street"] == "CA-1 N")
ok("and the streets say so too",
   [x[1] for x in demo_route.build(dip, 300)["streets"]] == ["Reservation Rd", "CA-1 N"])

# It is signed where it is joined, and keeps it while the route stays on it, even
# where it turns away: north for 1.5 km, then west-south-west and downhill.
turns_away = osrm_of(
    [(0, -400, 11), (0, 1500, 25), (-3000, -1200, 25)],
    [("depart", "right", "Reservation Road", "CR G17", 180, 0),
     ("merge", "slight left", "Cabrillo Highway", "CA 1", 0, 1),
     ("fork", "slight left", "Cabrillo Highway", "CA 1", 248, 2),
     ("arrive", None, "Cabrillo Highway", "CA 1", 0, None)])
line_of = demo_route.Line([(lat, lon) for lon, lat in
                           turns_away["routes"][0]["geometry"]["coordinates"]])
tm = demo_route.build(turns_away, 300)["maneuvers"]
ok("a step that stays on the highway keeps the way it was signed on joining it",
   [m["instruction"] for m in tm] == [
       "Merge onto CA-1 N", "Keep left to stay on CA-1 N", "Arrive at Omarchy Meetup"]
   and side(line_of, 1900, "CA", "1") == "S")
changed = json.loads(json.dumps(turns_away))
changed["routes"][0]["legs"][0]["steps"][2]["ref"] = "US 101"
ok("but a different highway is signed by where it goes",
   demo_route.build(changed, 300)["maneuvers"][1]["instruction"]
   == "Keep left to stay on US-101 S")

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
scripted_out = os.path.join(TMP, "drive-scripted.json")
cli = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "demo_route.py"),
     "--osrm", os.path.join(FIXTURES, "osrm-mini.json"), "--out", scripted_out,
     "--loop-secs", "300", "--hard-brake-at", "100"], capture_output=True, text=True)
ok("--hard-brake-at 100 writes the drive with its one scripted hard brake",
   cli.returncode == 0
   and json.load(open(scripted_out, encoding="utf-8")) == SCRIPTED)
for wrong_at in ("0", "180", "-5"):
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "demo_route.py"),
         "--osrm", os.path.join(FIXTURES, "osrm-mini.json"),
         "--out", os.path.join(TMP, "drive-wrong.json"),
         "--loop-secs", "300", "--hard-brake-at", wrong_at],
        capture_output=True, text=True)
    ok(f"--hard-brake-at {wrong_at} is outside the driving, so an error and no file",
       cli.returncode == 2 and "hard-brake-at" in cli.stderr
       and not os.path.exists(os.path.join(TMP, "drive-wrong.json")))
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
   d0["next"]["instruction"] == "At the roundabout, take the exit onto Reservation Rd"
   and d0["next"]["type"] == "roundabout" and d0["next"]["modifier"] == "right"
   and d0["next"]["street"] == "Reservation Rd"
   and abs(d0["next"]["in_m"] - 201.0) < 2.0)
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
# The same drive without a scripted hard brake, said outright, for the tests of
# the cues: they hold if the mini drive ever scripts one.
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

# Each loop starts over with the pack, the fuel and the odometer where they
# began, so none of them walks off.
def dials(p):
    return (p["values"]["HYBRID_BATTERY_REMAINING"], p["values"]["FUEL_LEVEL"],
            p["odometer_km"])


w, c = make()
p = w.step()
starts, swing, burnt, top, last_t = [dials(p)], [], [], 0.0, 0.0
while len(starts) < 10 and c.t < 5000.0 + 20 * LOOP:
    c.t += 0.2
    p = w.step()
    if loop_t(p) < last_t:                       # the loop wrapped
        burnt.append(starts[0][1] - low)
        starts.append(dials(p))
        swing.append(top)
        top = 0.0
        low = 100.0
    if len(starts) == 1 and not burnt and last_t == 0.0:
        low = 100.0
    low = min(low, p["values"]["FUEL_LEVEL"])
    top = max(top, abs(dials(p)[0] - starts[0][0]))
    last_t = loop_t(p)
ok(f"ten loops: the pack at the start of the tenth is where it was at the first "
   f"({starts[9][0]} vs {starts[0][0]})",
   len(starts) == 10 and starts[9][0] == starts[0][0] == 58.0)
ok("and at the start of each one in between",
   all(x[0] == 58.0 for x in starts))
ok(f"having moved within each loop, so this is not the pack standing still "
   f"(by up to {min(swing):.2f} to {max(swing):.2f} %)", min(swing) > 0.3)
ok(f"the fuel and the odometer too: {starts[0][1]} % and {starts[0][2]} km at the "
   f"start of every loop", all(x[1:] == starts[0][1:] for x in starts)
   and starts[0][1:] == (62.0, 137842.0))
ok(f"having been spent within each loop ({min(burnt):.2f} % at the least)",
   len(burnt) == 9 and min(burnt) > 0.2)

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
w, c = make(SCRIPTED)
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
w, c = make(SCRIPTED)
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

# The loop clock only goes back by wrapping.
ok("a clock that went back is a wrap only if the loop wrapped",
   demoworld._crossed(295, 290.0, 10.0, True) and demoworld._crossed(5, 290.0, 10.0, True)
   and not demoworld._crossed(100, 290.0, 10.0, True)
   and not demoworld._crossed(100, 195.0, 180.0, False)
   and not demoworld._crossed(100, 58.0, 45.0, False)
   and demoworld._crossed(100, 99.0, 101.0, False)
   and not demoworld._crossed(100, 101.0, 102.0, False))

# A cue at rest. Where the drive stands still, the loop time it was at is the
# moment the stop began, and a car cued part way through the stop must not be
# put back to it: the loop clock would go back, and read as the loop starting
# over, and fire the scripted hard brake.
light = stop_i + 12                       # in the middle of the 25 s light
AT_REST = {"at 1.5 s, before it has pulled away": 1.5,
           "in the middle of the light": float(light),
           "in the closing hold": 200.0}
for where, when in AT_REST.items():
    for cue_name in ("park", "hard_brake"):
        w, c, p = at_loop_time(SCRIPTED, when)
        t_cued, wall_cued = loop_t(p), c.wall()
        w.cue(cue_name)
        trail = go(w, c, 30.0, dt=0.2, keep=True)
        clocks = [t_cued] + [loop_t(x) for x in trail]
        flags = [x["demo"]["event"] for x in trail]
        rising = sum(1 for a, b in zip([None] + flags, flags) if b and not a)
        ok(f"{cue_name} {where}: the loop clock never goes back "
           f"({t_cued:.1f} to {clocks[-1]:.1f})",
           all(b >= a for a, b in zip(clocks, clocks[1:])))
        if cue_name == "park":
            ok(f"park {where}: no event fires", all(e is None for e in flags))
        else:
            ok(f"hard_brake {where}: one event, the one cued, for 10 s and no more",
               rising == 1 and flags[0]["at"] == wall_cued
               and all(e for e in flags[:49]) and all(e is None for e in flags[52:]))

# ... and a car parked at a light, driving on, has the rest of the light to wait.
light_go = next(i for i in range(stop_i, len(pts)) if pts[i][3] > 0) - 1   # last second at rest
for where, when, hold_secs in (("at a light", light, 10.0), ("at the start", 1.5, 6.0)):
    w, c, p = at_loop_time(MINI, when)
    left = (light_go if when == light else 3) - loop_t(p)
    w.cue("park")
    go(w, c, hold_secs)
    w.cue("drive")
    waited = 0.0
    while speed(go(w, c, 0.2)) == 0 and waited < 40.0:
        waited += 0.2
    ok(f"parked {where} with {left:.1f} s of it left, and driven on after {hold_secs:.0f} s: "
       f"it goes after {waited:.1f} s, not after a fresh wait",
       left - 1.0 <= waited <= left + 1.0)

# Restart.
w, c, p = at_loop_time(QUIET, CRUISE)
pack_before = p["values"]["HYBRID_BATTERY_REMAINING"]
dials_before = dials(p)
w.cue("restart")
c.t += 0.2
p = w.step()
ok("restart: the loop clock is back at the start, and so is the car",
   loop_t(p) < 0.5 and p["demo"]["route_m"] < 5.0)
ok(f"and the pack is back to 58 ({pack_before} before)",
   pack_before != 58.0 and p["values"]["HYBRID_BATTERY_REMAINING"] == 58.0)
ok("and the fuel and the odometer are back to where they began",
   p["values"]["FUEL_LEVEL"] == 62.0 and p["odometer_km"] == 137842.0
   and dials_before[1:] != (62.0, 137842.0))
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

# The quiet cue (hardening B). `omacar demo off` sends it first, and the page,
# seeing a fresh demo.quiet_at, fades its music out before the window goes.
# The world only says when, for 10 s, and changes nothing else about the car.
w, c, p = cruising_with_file()
twin, tc = make(QUIET)                      # the same drive, never told
twin.step()
go(twin, tc, CRUISE, dt=0.5)
ok("with no quiet cue there is no quiet_at", p["demo"].get("quiet_at", "missing") is None)
at = c.wall() + 0.1
write_cue("quiet", at)


def both(secs):
    c.t += secs
    tc.t += secs
    return w.step(), twin.step()


def but_quiet(x):
    d = json.loads(json.dumps(x))
    d["demo"].pop("quiet_at", None)
    return d


p, q = both(0.5)
ok("a quiet cue puts its own `at` in live.json as demo.quiet_at",
   p["demo"].get("quiet_at") == at)
ok("and nothing else about the car changes: the same payload as a world never told",
   but_quiet(p) == but_quiet(q) and speed(p) > 0 and p["demo"]["scene"] == "drive")
for _ in range(18):
    p, q = both(0.5)
ok("it is still there 9.5 s later, and the drive still has not changed",
   p["demo"].get("quiet_at") == at and but_quiet(p) == but_quiet(q))
p, q = both(1.0)
ok("and gone after 10 s", p["demo"].get("quiet_at", "missing") is None and but_quiet(p) == but_quiet(q))
later_at = c.wall() + 0.1
write_cue("quiet", later_at)
p, q = both(0.5)
ok("a later quiet is another one", p["demo"].get("quiet_at") == later_at)
w.cue("quiet")
ok("told directly, it is now", w.step()["demo"]["quiet_at"] == c.wall())

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

# A drive is checked at the door.
def drive_file(doc, name="d.json"):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def refused(doc):
    """What load_drive says about a drive, or None if it loads."""
    try:
        demoworld.load_drive(drive_file(doc))
    except ValueError as e:
        return str(e)
    return None


def without(key, **changes):
    d = json.loads(json.dumps(MINI))
    d.pop(key, None)
    d.update(changes)
    return d


ok("the drives it will play load: the fixture, and the real one where there is one",
   refused(MINI) is None
   and all(refused(json.load(open(x))) is None for x in
           [os.path.join(ROOT, "share", "assets", "private", "demo", "drive.json")]
           if os.path.exists(x)))
for key in ("points", "loop_secs", "route_total_m", "maneuvers", "destination"):
    ok(f"a drive with no `{key}` is refused, by name, at load",
       f"`{key}`" in (refused(without(key)) or ""))
ok("and one that is not JSON, or not an object",
   "" != (refused("{ nope") or "") and "object" in (refused("[1, 2]") or ""))
short = json.loads(json.dumps(MINI))
short["points"] = short["points"][:-1]
gappy = json.loads(json.dumps(MINI))
del gappy["points"][40]
late = json.loads(json.dumps(MINI))
late["points"][5][0] = 5.5
back = json.loads(json.dumps(MINI))
back["points"][50][5] = back["points"][49][5] - 1
narrow = json.loads(json.dumps(MINI))
narrow["points"][7] = narrow["points"][7][:6]
ok("points that are not one a second are refused: too few, a gap, off the beat",
   all(x and "points" in x for x in (refused(short), refused(gappy), refused(late))))
ok("and rows that are the wrong shape, or that go backwards",
   "row 7" in (refused(narrow) or "") and "backwards" in (refused(back) or ""))
ok("a loop length that is not a whole number of seconds is refused",
   "loop_secs" in (refused(without("loop_secs", loop_secs=300.5)) or "")
   and "loop_secs" in (refused(without("loop_secs", loop_secs=0)) or ""))
ok("manoeuvres it would fall over on are refused: no words, out of order",
   "maneuvers" in (refused(without("maneuvers", maneuvers=[{"route_m": 5}])) or "")
   and "out of order" in (refused(without(
       "maneuvers", maneuvers=list(reversed(MINI["maneuvers"])))) or ""))
ok("and a destination with no place, scenes with no length",
   "destination" in (refused(without("destination", destination={"label": "x"})) or "")
   and "scenes" in (refused(without("scenes", scenes=[{"t": 3}])) or ""))
ok("a drive without streets, scenes or events is fine: the world does without",
   refused(without("streets")) is None and refused(without("scenes")) is None
   and refused(without("events")) is None)

# Through main(): no drive named, nothing built here.
import contextlib  # noqa: E402
import io  # noqa: E402

played = []
kept_root, kept_run = demoworld.ROOT, demoworld.run
demoworld.ROOT = os.path.join(TMP, "root-no-private")
os.makedirs(os.path.join(demoworld.ROOT, "test", "fixtures", "demo"))
shutil.copy(os.path.join(FIXTURES, "drive-mini.json"),
            os.path.join(demoworld.ROOT, "test", "fixtures", "demo", "drive-mini.json"))
demoworld.run = lambda d: played.append(d) or 0
try:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = demoworld.main(["run"])
    with contextlib.redirect_stderr(io.StringIO()) as bad_err:
        bad_code = demoworld.main(["run", "--drive", drive_file(without("points"))])
finally:
    demoworld.ROOT, demoworld.run = kept_root, kept_run
ok("main() with no drive named and none built plays the fixture",
   code == 0 and len(played) == 1 and played[0]["loop_secs"] == 300)
ok("and says so on stderr, once, naming what it is playing instead",
   err.getvalue().count("no drive at") == 1 and "drive-mini.json" in err.getvalue()
   and "tools/demo_route.py" in err.getvalue())
ok("a drive that will not load is exit 2 with the reason, and nothing is played",
   bad_code == 2 and "`points`" in bad_err.getvalue() and len(played) == 1)

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
quiet_at = time.time() + 0.2
with open(os.path.join(STATE, "demo-cue.json"), "w", encoding="utf-8") as f:
    json.dump({"cue": "quiet", "at": quiet_at}, f)
time.sleep(1.5)
with open(live_path, encoding="utf-8") as f:
    live4 = json.load(f)
ok("and a quiet cue shows in live.json as demo.quiet_at",
   live4["demo"].get("quiet_at") == quiet_at and live4["demo"]["scene"] == "drowsy")
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

# It will not run beside the real daemon, or outside a folder of the demo's.
def try_run(state, extra_env=None, drop=(), secs=1.5):
    """demoworld.py run against a state folder; (exit code or None, stderr, live.json or None)."""
    e = dict(os.environ)
    e["OMACAR_STATE"] = state
    e["XDG_STATE_HOME"] = os.path.dirname(state)
    e.update(extra_env or {})
    for k in drop:
        e.pop(k, None)
    os.makedirs(state, exist_ok=True)
    pr = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "lib", "demoworld.py"), "run",
         "--drive", os.path.join(FIXTURES, "drive-mini.json")],
        env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + secs
    while pr.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    code = pr.poll()
    if code is None:
        pr.send_signal(signal.SIGTERM)
    _, err = pr.communicate(timeout=10)
    return code, err, os.path.join(state, "sim.pid")


sleeper = subprocess.Popen(["sleep", "60"])
gone = subprocess.Popen(["true"])
gone.wait()
try:
    state = os.path.join(TMP, "with-a-daemon", "omacar-demo", "state", "omacar")
    os.makedirs(state)
    with open(os.path.join(state, "daemon.pid"), "w") as f:
        f.write(str(sleeper.pid))
    code, err, pidf = try_run(state)
    ok("a live daemon.pid in the state folder: exit 2, and it says why",
       code == 2 and "daemon" in err and str(sleeper.pid) in err
       and "Traceback" not in err)
    ok("and it wrote nothing: no sim.pid, no live.json",
       not os.path.exists(pidf) and not os.path.exists(os.path.join(state, "live.json")))
    with open(os.path.join(state, "daemon.pid"), "w") as f:
        f.write(str(gone.pid))
    code, err, pidf = try_run(state)
    ok("a daemon.pid left behind by a daemon that has gone does not stop it",
       code is None and os.path.exists(os.path.join(state, "live.json")))
    with open(os.path.join(state, "daemon.pid"), "w") as f:
        f.write("not a pid")
    code, err, pidf = try_run(state)
    ok("nor does one that is not a pid", code is None)
finally:
    sleeper.kill()
    sleeper.wait()

plain = os.path.join(TMP, "the-real-car", "state", "omacar")
code, err, pidf = try_run(plain, drop=("OMACAR_DEMO_ALLOW_ANY",))
ok("a state folder that is not inside an omacar-demo folder: exit 2, and it says why",
   code == 2 and "omacar-demo" in err and "Traceback" not in err)
ok("and it wrote nothing: no sim.pid, no live.json",
   not os.path.exists(pidf) and not os.path.exists(os.path.join(plain, "live.json")))
code, err, pidf = try_run(os.path.join(TMP, "the-demo", "omacar-demo", "state", "omacar"),
                          drop=("OMACAR_DEMO_ALLOW_ANY",))
ok("inside one it runs, with nothing lifted", code is None)
code, err, pidf = try_run(os.path.join(TMP, "not-omacar-demo-either", "state", "omacar"),
                          drop=("OMACAR_DEMO_ALLOW_ANY",))
ok("a folder only named something like it is not enough", code == 2)
code, err, pidf = try_run(plain)
ok("OMACAR_DEMO_ALLOW_ANY lifts the folder rule, for scratch folders", code is None)

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
