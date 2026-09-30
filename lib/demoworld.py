#!/usr/bin/env python3
"""The meetup demo's car: a scripted drive, played in real time.

There is no car in the room, so the demo's car is this. It plays one drive,
built by tools/demo_route.py from a real route, a second at a time; writes the
same live.json the daemon and the simulator write, so the app reads it without
knowing the difference; and takes cues from the presenter -- park, drive, get
drowsy, brake hard, start over -- and from `omacar demo off`, whose `quiet` it
only passes on to the page (demo.quiet_at), which fades its music out.

    python3 lib/demoworld.py run [--drive PATH]
    python3 lib/demoworld.py tidy        the seeded car, all systems normal

It writes `$OMACAR_STATE/live.json` at 5 Hz and `$OMACAR_STATE/sim.pid`, so
`omacar demo status` and the daemon's "the simulator is running" refusal work
as they do for the simulator. It takes its state folder from OMACAR_STATE, and
in the demo that is the same folder the rest of the app takes from
XDG_STATE_HOME. The presenter's cues arrive in `$OMACAR_STATE/demo-cue.json`,
as {"cue": ..., "at": epoch}; each `at` is acted on once.

It replaces the simulator in the demo, and differs from it on purpose:

  * PACED BY THE CLOCK. The simulator counts its own sleeps, so its drive runs
    at a multiple of real time that depends on the machine. This one measures
    the elapsed time and advances the drive by exactly that, so ten seconds of
    it is ten seconds of drive however it was cut into steps. DemoWorld takes
    its clocks as arguments so that a test can hold them.
  * CLOSED. It never opens the adapter, a camera, a service or the sound
    system. There is no code here that could, and test/demoworld_test.py
    reads this file's source to keep it so.
  * KEPT AWAY FROM THE REAL CAR. `run` refuses (exit 2) unless its state
    folder is inside an `omacar-demo` folder, and whenever the real daemon is
    running in it, because they write the same live.json.
    OMACAR_DEMO_ALLOW_ANY lifts the first, for the tests' scratch folders.

The drive is a loop of `loop_secs`. While nothing is cued the car simply is
where the drive says it is at the loop's time: state_at(drive, t) is that,
a pure function of the drive and the moment. A cue takes the car off the drive
and drives it itself -- braking, holding, pulling away -- until it is back on
the speed the drive has there, and the loop's time is then wherever on the
drive the car has got to. That is what makes a hold pause the loop, and a hard
brake cost the car a little distance.
"""

import argparse
import bisect
import json
import math
import os
import signal
import sqlite3
import sys
import time

LIB = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(LIB)
sys.path.insert(0, LIB)
import garage   # noqa: E402
import records  # noqa: E402
import sim      # noqa: E402

TICK = 0.2                 # live.json, 5 Hz
SUBSTEP = 0.1              # the longest a step of the world's own driving is integrated in

# ---- the cues ------------------------------------------------------------------
PARK_BRAKE = 2.5           # m/s^2, the park cue
HARD_BRAKE = 8.0           # m/s^2, for HARD_BRAKE_SECS
HARD_BRAKE_SECS = 2.0
EVENT_SECS = 10.0          # how long `demo.event` stays up
QUIET_SECS = 10.0          # how long `demo.quiet_at` stays up
DROWSY_SECS = 45.0
RECOVER_ACCEL = 2.0        # pulling away, and getting back to the drive's speed
RECOVER_BRAKE = 2.0
RECOVER_LIMIT = 40.0       # s. A car chasing the drive's speed is on it long before this;
                           # the limit is only so that it can never be left behind.
REJOIN_MPS = 0.02          # how near the drive's speed counts as on it

# ---- the car -------------------------------------------------------------------
# Revs by gear. Above 60 km/h it is in sixth: 750 rpm plus 28 for each km/h.
# Under that a lower gear, so the revs climb and drop as a car's do.
GEARS = ((60.0, 28.0), (45.0, 36.0), (30.0, 46.0), (18.0, 62.0), (8.0, 90.0),
         (0.0, 150.0))
IDLE_RPM = 750

ASSIST_MPS2 = 0.4          # harder than this is assist, and regen is the same the other way
SOC_START, SOC_MIN, SOC_MAX = 58.0, 40.0, 78.0     # % of the pack
SOC_PER_S = {"assist": -0.05, "charge": 0.06, "idle": 0.005, "stop": 0.0}
MOTOR_KW = 10.0            # what the IMA motor gives and takes at most

FUEL_START = 62.0          # %
FUEL_FLOOR = 30.0
FUEL_PER_M = 0.00017       # % of the tank a metre costs: 0.17 %/km, about 6.8 L/100 km
AMBIENT_C = 17.0

SUPPORTED = ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "MAF",
             "COOLANT_TEMP", "INTAKE_TEMP", "FUEL_LEVEL", "RUN_TIME",
             "CONTROL_MODULE_VOLTAGE", "SHORT_FUEL_TRIM_1",
             "LONG_FUEL_TRIM_1", "TIMING_ADVANCE", "AMBIANT_AIR_TEMP",
             # Without this the app draws the hybrid tile as absent.
             "HYBRID_BATTERY_REMAINING"]


# ---- the drive -----------------------------------------------------------------

def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def drive_problem(drive):
    """The first thing wrong with a drive as the world will use it, in words, or
    None. The world indexes into these lists a hundred times a second, so a
    drive that is wrong is refused at the door and not deep in the loop."""
    if not isinstance(drive, dict):
        return "not a drive: it should be a JSON object"
    missing = [k for k in ("points", "loop_secs", "route_total_m", "maneuvers",
                           "destination") if k not in drive]
    if missing:
        return "not a drive: it has no " + ", ".join(f"`{k}`" for k in missing)
    loop, pts = drive["loop_secs"], drive["points"]
    if not isinstance(loop, int) or isinstance(loop, bool) or loop < 1:
        return "`loop_secs` should be a whole number of seconds"
    if not _number(drive["route_total_m"]) or drive["route_total_m"] <= 0:
        return "`route_total_m` should be a length in metres"
    if not isinstance(pts, list) or len(pts) != loop + 1:
        return f"`points` should be one a second, {loop + 1} of them for a {loop} s loop"
    for i, row in enumerate(pts):
        if not (isinstance(row, list) and len(row) == 7 and all(_number(x) for x in row)):
            return f"`points` row {i} should be [t, lat, lon, kph, heading, route_m, remaining_s]"
        if row[0] != i:
            return f"`points` row {i} is at t={row[0]}: they should be one a second from 0"
        if i and row[5] < pts[i - 1][5]:
            return f"`points` row {i} goes backwards along the route"
    moves = drive["maneuvers"]
    if not isinstance(moves, list):
        return "`maneuvers` should be a list"
    for i, m in enumerate(moves):
        if not (isinstance(m, dict) and _number(m.get("route_m"))
                and all(isinstance(m.get(k), str) for k in ("type", "street", "instruction"))):
            return f"`maneuvers` item {i} needs route_m, type, street and instruction"
        if i and m["route_m"] < moves[i - 1]["route_m"]:
            return f"`maneuvers` item {i} is out of order"
    dest = drive["destination"]
    if not (isinstance(dest, dict) and isinstance(dest.get("label"), str)
            and _number(dest.get("lat")) and _number(dest.get("lon"))):
        return "`destination` needs a label, lat and lon"
    for key, shape in (("streets", lambda x: isinstance(x, list) and len(x) == 2
                        and _number(x[0]) and isinstance(x[1], str)),
                       ("scenes", lambda x: isinstance(x, dict) and _number(x.get("t"))
                        and _number(x.get("secs")) and isinstance(x.get("kind"), str)),
                       ("events", lambda x: isinstance(x, dict) and _number(x.get("t"))
                        and isinstance(x.get("kind"), str))):
        items = drive.get(key)
        if items is not None and not (isinstance(items, list) and all(shape(x) for x in items)):
            return f"`{key}` is not in the shape the world reads"
        if key == "streets" and items and any(b[0] < a[0] for a, b in zip(items, items[1:])):
            return "`streets` are out of order"
    return None


def load_drive(path):
    """The drive in `path`, or a ValueError that says what is wrong with it."""
    with open(path, encoding="utf-8") as f:
        drive = json.load(f)
    problem = drive_problem(drive)
    if problem:
        raise ValueError(problem)
    return drive


def resolve_drive(path=None, root=None):
    """(path, warning). The real drive is built on this machine and private, so
    without it the demo still runs, on the short fixture, and says so."""
    if path:
        return path, None
    root = root or ROOT
    built = os.path.join(root, "share", "assets", "private", "demo", "drive.json")
    if os.path.exists(built):
        return built, None
    mini = os.path.join(root, "test", "fixtures", "demo", "drive-mini.json")
    return mini, (f"demoworld: no drive at {built}; playing the short test loop "
                  f"{mini}. Build the real one with tools/demo_route.py.")


def _lerp(a, b, f):
    return a + (b - a) * f


def _turn(a, b, f):
    """Between two compass headings, by the short way round."""
    return (a + ((b - a + 180.0) % 360.0 - 180.0) * f) % 360.0


def sample(drive, t):
    """The drive at loop time t (0..loop_secs): points are one a second, from 0."""
    pts = drive["points"]
    t = min(max(t, 0.0), float(len(pts) - 1))
    i = min(int(t), len(pts) - 2)
    f = t - i
    a, b = pts[i], pts[i + 1]
    return {"lat": _lerp(a[1], b[1], f), "lon": _lerp(a[2], b[2], f),
            "kph": _lerp(a[3], b[3], f), "heading": _turn(a[4], b[4], f),
            "route_m": _lerp(a[5], b[5], f), "remaining_s": _lerp(a[6], b[6], f)}


def base_accel(drive, t):
    """The drive's acceleration at t, m/s^2: the change in its speed a second
    either side."""
    hi = float(len(drive["points"]) - 1)
    a, b = max(0.0, t - 0.5), min(hi, t + 0.5)
    if b <= a:
        return 0.0
    return (sample(drive, b)["kph"] - sample(drive, a)["kph"]) / 3.6 / (b - a)


def t_of_s(drive, s):
    """When on the loop the drive is `s` metres along; where it stands still
    there, the first moment it does."""
    pts = drive["points"]
    i = bisect.bisect_left(pts, s, key=lambda p: p[5])
    if i <= 0:
        return 0.0
    if i >= len(pts):
        return float(pts[-1][0])
    a, b = pts[i - 1], pts[i]
    return a[0] + (s - a[5]) / (b[5] - a[5]) * (b[0] - a[0])


def kph_at_s(drive, s):
    """How fast the drive is going where it is `s` metres along."""
    return sample(drive, t_of_s(drive, s))["kph"]


def _next_maneuver(drive, route_m):
    moves = drive.get("maneuvers") or []
    i = bisect.bisect_right(moves, route_m, key=lambda m: m["route_m"])
    if i >= len(moves):
        return None
    m = moves[i]
    return {"type": m["type"], "modifier": m.get("modifier"), "street": m["street"],
            "instruction": m["instruction"], "in_m": round(m["route_m"] - route_m, 1)}


def _street(drive, route_m):
    streets = drive.get("streets") or []
    if not streets:
        return ""
    i = bisect.bisect_right(streets, route_m, key=lambda s: s[0]) - 1
    return streets[max(i, 0)][1]


def _rpm(speed):
    if speed <= 0:
        return 0
    for floor, per_kph in GEARS:
        if speed >= floor:
            return round(IDLE_RPM + speed * per_kph)


def state_at(drive, t, cue_state=None):
    """What the car says at loop time t: the payload live.json carries, less
    `t` and `uptime`, which are the world's.

    A function of the drive, the moment and `cue_state`, and nothing else --
    bar the real time, which is the ETA's default. `cue_state` is what the
    drive itself does not know, every key of it optional:

        kph, accel   the speed and acceleration when the world is driving
                     the car itself, instead of the drive
        hold         the world is holding the car at a stop
        drowsy       the drowsy moment is on
        event        {"kind", "at"}: a hard brake in progress
        quiet_at     epoch: when `demo off` asked the page for quiet
        soc          the pack, %                        (58)
        odo_m        metres driven so far this run      (how far along the route)
        run_time     seconds the world has been running (the loop's time)
        now          wall time, for the ETA            (the real one)
    """
    cue = cue_state or {}
    loop = drive["loop_secs"]
    tt = t % loop
    pos = sample(drive, tt)
    kph = pos["kph"] if cue.get("kph") is None else cue["kph"]
    accel = base_accel(drive, tt) if cue.get("accel") is None else cue["accel"]
    speed = round(kph, 1)
    stopped = speed <= 0
    v = speed / 3.6

    if stopped:
        rpm = 0
        load = throttle = maf = 0.0
    else:
        rpm = _rpm(speed)
        if accel < -ASSIST_MPS2:
            load = 6.0 + 0.05 * speed                 # coasting on the brakes
        else:
            load = 14.0 + 0.18 * speed + 26.0 * max(accel, 0.0)
        load = min(load, 92.0)
        throttle = max(0.0, 0.62 * load - 2.5)
        maf = rpm * load * 1.5e-4                     # g/s: 1.5 L, 1.2 g/L, at this load
    lphk = lph = None
    if maf > 0:
        lph = maf / sim.AFR * 3600.0 / sim.FUEL_DENSITY_G_PER_L
        if speed > sim.MOVING_KPH:
            lphk = lph / speed * 100.0

    # The pack moves by what the car is doing, by the same thresholds the
    # world moves it by.
    if stopped:
        ima, kw = "stop", 0.0
    elif accel > ASSIST_MPS2:
        ima = "assist"
        kw = min(MOTOR_KW, max(0.3, sim.VEHICLE["mass_kg"] * accel * v / 1000.0 * 0.3))
    elif accel < -ASSIST_MPS2:
        ima = "charge"
        kw = min(MOTOR_KW, max(0.3, sim.VEHICLE["mass_kg"] * -accel * v / 1000.0 * 0.5))
    else:
        ima, kw = "idle", 0.0

    parked_window = any(s["t"] <= tt < s["t"] + s["secs"] and s["kind"] == "parked"
                        for s in drive.get("scenes") or [])
    if parked_window or (cue.get("hold") and stopped):
        scene = "parked"
    elif cue.get("drowsy"):
        scene = "drowsy"
    else:
        scene = "drive"

    route_m = pos["route_m"]
    odo_m = cue.get("odo_m", route_m)
    now = cue.get("now") if cue.get("now") is not None else time.time()
    remaining_m = max(0.0, drive["route_total_m"] - route_m)
    values = {
        "RPM": rpm, "SPEED": speed,
        "ENGINE_LOAD": round(load, 1), "THROTTLE_POS": round(throttle, 1),
        "MAF": round(maf, 2),
        "COOLANT_TEMP": round(89.5 + 1.5 * math.sin(tt / loop * 6 * math.pi), 1),
        "INTAKE_TEMP": round(AMBIENT_C + 6 + load * 0.08, 1),
        "AMBIANT_AIR_TEMP": AMBIENT_C,
        "FUEL_LEVEL": round(max(FUEL_FLOOR, FUEL_START - FUEL_PER_M * odo_m), 1),
        "RUN_TIME": int(cue.get("run_time", tt)),
        "CONTROL_MODULE_VOLTAGE": 12.5 if stopped
        else round(14.2 + 0.09 * math.sin(tt * 0.9), 2),
        "SHORT_FUEL_TRIM_1": round(1.4 * math.sin(tt * 1.9), 1),
        "LONG_FUEL_TRIM_1": 2.3,
        "TIMING_ADVANCE": 0.0 if stopped
        else round(12.0 + load * 0.12 + 1.5 * math.sin(tt * 0.7), 1),
        "HYBRID_BATTERY_REMAINING": round(cue.get("soc", SOC_START), 3),
    }
    return {
        "connected": True, "simulated": True, "actuator": None,
        "port": sim.VEHICLE["port"], "kind": sim.VEHICLE["adapter"],
        "protocol": sim.VEHICLE["protocol"],
        "supported": list(SUPPORTED), "values": values,
        "economy_lphk": lphk, "fuel_lph": lph,
        "efficiency": sim.efficiency(lphk, speed, load, throttle),
        "efficiency_basis": "economy" if lphk else ("off" if stopped else "idle"),
        "odometer_km": round(sim.ODO_NOW + odo_m / 1000.0, 1),
        "trip": {"kind": "highway", "km": round(drive["route_total_m"] / 1000.0, 1),
                 "shakedown": False},
        "demo": {
            "t": round(tt, 3), "loop_secs": loop,
            "lat": round(pos["lat"], 6), "lon": round(pos["lon"], 6),
            "heading": round(pos["heading"], 1) % 360.0,
            "street": _street(drive, route_m),
            "route_m": round(route_m, 1), "remaining_m": round(remaining_m, 1),
            "eta": round(now + pos["remaining_s"], 1),
            "next": _next_maneuver(drive, route_m),
            "parked": scene == "parked", "scene": scene,
            "event": dict(cue["event"]) if cue.get("event") else None,
            "quiet_at": cue.get("quiet_at"),
            "ima": {"state": ima, "kw": round(kw, 1)},
        },
    }


# ---- the world -----------------------------------------------------------------

def _crossed(t_event, before, after, wrapped):
    """Did the loop's clock pass t_event going from `before` to `after`? The
    clock only goes backwards by wrapping at the loop's end, and `wrapped` says
    whether it did: a smaller `after` on its own is not a wrap."""
    if wrapped:
        return t_event > before or t_event <= after
    return before < t_event <= after


class DemoWorld:
    """The drive, played. step() advances it by the time elapsed since the last
    step and returns what live.json should say.

    Two ways the car can be. Following, it is where the drive has it at
    `tau`, the loop's time, which runs at the clock's pace. Driving itself
    (own), after a cue, it keeps its own place `s` on the route and its own
    speed `v`, and `tau` is where on the loop that place is, never less than it
    was; it goes back to following when its speed is the drive's again.
    """

    def __init__(self, drive, clock=time.monotonic, wall=time.time, cue_path=None):
        self.drive = drive
        self.clock, self.wall = clock, wall
        self.cue_path = cue_path
        self.loop = float(drive["loop_secs"])
        self.route_end = drive["points"][-1][5]
        self.started = self.last = clock()
        # A cue written before the world existed is somebody else's.
        self.honoured = wall()

        self.tau = 0.0
        self.own = False
        self.s = self.v = self.a = 0.0
        self.holding = False
        self.brake_left = 0.0
        self.recovering = 0.0

        self.drowsy_until = 0.0
        self.event = None
        self.event_until = 0.0
        self.quiet_at = None
        self.quiet_until = 0.0
        self.soc = SOC_START
        self.odo_m = 0.0

    # -- cues

    def cue(self, kind, at=None):
        """Act on a cue now: `at` is when it was asked for (the cue file's).
        False for one it does not know."""
        if kind == "park":
            self._own()
            self.holding = True
        elif kind == "drive":
            self.holding = False
            self.recovering = 0.0
            # The loop's own closing stop has nothing to pull away from.
            if any(s["kind"] == "parked" and s["t"] <= self.tau < s["t"] + s["secs"]
                   for s in self.drive.get("scenes") or []):
                self._restart()
        elif kind == "drowsy":
            self.drowsy_until = self.clock() + DROWSY_SECS
        elif kind == "hard_brake":
            self._own()
            self.brake_left = HARD_BRAKE_SECS
            self.recovering = 0.0
            self.event = {"kind": "hard_brake", "at": self.wall()}
            self.event_until = self.clock() + EVENT_SECS
        elif kind == "restart":
            self._restart()
        elif kind == "quiet":
            # Only said: the page fades its music on seeing it. The car goes on.
            self.quiet_at = self.wall() if at is None else at
            self.quiet_until = self.clock() + QUIET_SECS
        else:
            return False
        return True

    def _read_cue_file(self):
        if not self.cue_path:
            return
        try:
            with open(self.cue_path, encoding="utf-8") as f:
                doc = json.load(f)
            kind, at = doc["cue"], float(doc["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if at > self.honoured:
            self.honoured = at
            self.cue(kind, at)

    def _own(self):
        """Take the car off the drive, at its place and speed now."""
        if self.own:
            return
        here = sample(self.drive, self.tau)
        self.own = True
        self.s = here["route_m"]
        self.v = here["kph"] / 3.6
        self.a = base_accel(self.drive, self.tau)
        self.recovering = 0.0

    def _restart(self):
        """Back to the start of the loop, and everything that counts with it."""
        self.tau = 0.0
        self.own = False
        self.holding = False
        self.brake_left = 0.0
        self._start_over()

    def _start_over(self):
        """What a loop begins with. Left to run, the pack gains a few per cent
        a loop and an evening of loops would pin it at the top, where it could
        never be seen charging; the fuel and the odometer would wander off as
        far, and the demo would not be the same on the tenth loop as the first."""
        self.soc = SOC_START
        self.odo_m = 0.0

    # -- time

    def step(self):
        now = self.clock()
        elapsed, self.last = max(0.0, now - self.last), now
        self._read_cue_file()
        n = math.ceil(elapsed / SUBSTEP) if elapsed > 0 else 0
        for _ in range(n):
            self._tick(elapsed / n)
        return self.payload()

    def _tick(self, h):
        before, wrapped = self.tau, False
        if self.own:
            self._drive_self(h)
            v, a = self.v, self.a
        else:
            self.tau += h
            if self.tau >= self.loop:
                self.tau %= self.loop
                wrapped = True
                self._start_over()
            v = sample(self.drive, self.tau)["kph"] / 3.6
            a = base_accel(self.drive, self.tau)
        for e in self.drive.get("events") or []:
            if _crossed(e["t"], before, self.tau, wrapped):
                self.cue(e["kind"])

        if v < 0.05 / 3.6:
            state = "stop"
        elif a > ASSIST_MPS2:
            state = "assist"
        elif a < -ASSIST_MPS2:
            state = "charge"
        else:
            state = "idle"
        self.soc = min(SOC_MAX, max(SOC_MIN, self.soc + SOC_PER_S[state] * h))
        self.odo_m += v * h

    def _drive_self(self, h):
        """One step of the car driving itself."""
        drive = self.drive
        if self.brake_left > 0:
            accel = -HARD_BRAKE
            self.brake_left = max(0.0, self.brake_left - h)
        elif self.holding:
            accel = -PARK_BRAKE if self.v > 0 else 0.0
        else:
            accel = max(-RECOVER_BRAKE, min(RECOVER_ACCEL, (self._target() - self.v) / h))
            self.recovering += h
        v_new = max(0.0, self.v + accel * h)
        self.a = (v_new - self.v) / h
        self.s = min(self.route_end, self.s + (self.v + v_new) / 2 * h)
        self.v = v_new
        # The loop clock is where on the loop the car has got to, and it never
        # goes back. Where the drive stands still, t_of_s is when the stop began,
        # and a car cued part way through it has already had that much of it.
        self.tau = max(self.tau, t_of_s(drive, self.s))

        if not self.holding and self.brake_left == 0:
            if self.v >= self._target() - REJOIN_MPS or self.recovering > RECOVER_LIMIT:
                self.own = False

    def _target(self):
        """How fast the drive is going where the car is, m/s. If the drive has
        this place at the loop's time now, that is its speed now: where it
        stands still, the first time it was here was the moment it was still
        rolling in, and the car would be sent off with that."""
        here = sample(self.drive, self.tau)
        if abs(here["route_m"] - self.s) < 0.05:
            return here["kph"] / 3.6
        return kph_at_s(self.drive, self.s) / 3.6

    # -- what it says

    def payload(self):
        now = self.clock()
        if self.event and now >= self.event_until:
            self.event = None
        cue = {"now": self.wall(), "soc": self.soc, "odo_m": self.odo_m,
               "run_time": int(now - self.started), "hold": self.holding,
               "drowsy": now < self.drowsy_until, "event": self.event,
               "quiet_at": self.quiet_at if now < self.quiet_until else None}
        if self.own:
            cue["kph"], cue["accel"] = self.v * 3.6, self.a
        p = state_at(self.drive, self.tau, cue)
        p["t"] = self.wall()
        p["uptime"] = now - self.started
        return p


# ---- the process ---------------------------------------------------------------

def state_dir():
    return os.environ.get("OMACAR_STATE") or sim.STATE


def _alive(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True                     # it is there, and it is not ours
    except OSError:
        return False
    return True


def refusal(state):
    """Why the world may not run against this state folder, or None.

    The demo's car writes live.json and the vehicle pointer, and the real
    daemon writes the same files, so two things stop it from ever doing that
    to the real car: it only runs in a folder of the demo's, and never beside
    a daemon that is running. OMACAR_DEMO_ALLOW_ANY lifts the first, for the
    tests' scratch folders."""
    if not os.environ.get("OMACAR_DEMO_ALLOW_ANY") \
            and "omacar-demo" not in os.path.abspath(state).split(os.sep):
        return (f"{state} is not inside an omacar-demo folder, so it may be the "
                "real car's; `omacar demo on` sets the state folder up")
    pid_file = os.path.join(state, "daemon.pid")
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if pid > 0 and _alive(pid):
        return (f"the daemon is running (pid {pid}, from {pid_file}) and would be "
                "writing the same live.json")
    return None


def run(drive):
    """Publish live.json at 5 Hz until told to stop. 2 if it may not."""
    state = state_dir()
    why = refusal(state)
    if why:
        print(f"demoworld: not running: {why}", file=sys.stderr)
        return 2
    os.makedirs(state, exist_ok=True)
    # sim.publish writes to sim's own paths. In the demo they are the same
    # folder as OMACAR_STATE; if somebody made them differ, this goes where it
    # was told.
    sim.STATE = state
    sim.LIVE = os.path.join(state, "live.json")
    sim.PIDFILE = os.path.join(state, "sim.pid")
    with open(sim.PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    # The app reads live.json only when it is about the current vehicle, and
    # the simulated car is the demo's. That pointer lives in the garage, so it
    # is only moved if the garage is in the folder this is writing to.
    if os.path.abspath(garage.STATE) == os.path.abspath(state):
        records.use(garage.SIM_KEY)
    else:
        print(f"demoworld: OMACAR_STATE is {state}, not {garage.STATE}; "
              "leaving the current-vehicle pointer alone", file=sys.stderr)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    world = DemoWorld(drive, cue_path=os.path.join(state, "demo-cue.json"))
    due = time.monotonic()
    try:
        while True:
            sim.publish(world.step())
            due += TICK
            behind = time.monotonic() - due
            if behind > 1.0:            # a stall: carry on from now, not in a rush
                due += behind
            time.sleep(max(0.0, due - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        sim.publish({"connected": False, "simulated": True, "status": "stopped",
                     "port": sim.VEHICLE["port"]})
        try:
            os.remove(sim.PIDFILE)
        except OSError:
            pass
    return 0


# ---- the demo car, tidied -------------------------------------------------------
#
# `sim.py seed` writes a year of a real CR-Z's life, and a real CR-Z at 138,000
# km has codes: an IMA pack on its way out, an O2 heater, a blower resistor and
# the deflation warning after a rotation, and the readiness monitors those hold
# back. In front of a room that is a broken car -- Vehicle said "4 systems
# need attention", its tab carried a red 5 and Home's car said "Tyres Check",
# a minute before the demo's own scan said all normal. The demo's car is the
# same car on a good day (doc/design/2026-09-30-meetup-demo.md §4: "All systems
# normal ... no trouble codes"). Tidying it:
#
#   * every stored, pending or permanent code is cleared; the rows stay, so the
#     history still says what the car has had;
#   * the last full scan found nothing in any module, and it was run earlier
#     today (9:41, the mockups' clock, or half an hour ago before that);
#   * the readiness monitors are complete, and every Mode 06 result sits well
#     inside its limits;
#   * nothing in the service book is due, over the loop's own miles;
#   * the year of trips, days and samples is not touched.
#
# It writes only the demo car's own database, under the same rules as `run`.
# `omacar demo on` runs it every time, after the seed, and a second run
# changes nothing.

TIDY_SCAN_AT = (9, 41)
TIDY_MARGIN_KM = 250.0          # further than a loop of the drive ever takes the odometer
TIDY_LIFE = 0.75                # a service item tidied is three quarters of its interval away
# Mode 06 notes that describe the fault the seed gave the car, and what they
# say once the result is healthy.
TIDY_NOTES = {
    "0x39": "Heater resistance, measured as the engine starts.",
    "0x5B": "Measured usable capacity against a new pack. Below 0.70 the motor "
            "control unit limits assist.",
    "0x5C": "Voltage spread between the weakest and strongest cell blocks at the "
            "end of a discharge.",
}
TIDY_SERVICE_NOTES = {"IMA battery inspection": "capacity test, pack balanced"}


def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (table,)).fetchone() is not None


def _healthy(value, lo, hi):
    """A Mode 06 result comfortably inside its limits: at most 60% of a
    ceiling, 25% over a floor, and half way between the two when it has both.
    A result that already is stays as it is."""
    if value is None:
        return None
    if lo is not None and hi is not None:
        return value if lo + 0.2 * (hi - lo) <= value <= hi - 0.2 * (hi - lo) \
            else round((lo + hi) / 2, 3)
    if hi is not None:
        return value if value <= 0.6 * hi else round(0.5 * hi, 3)
    if lo is not None:
        return value if value >= 1.2 * lo else round(1.25 * lo, 3)
    return value


def _earlier_today(now):
    lt = time.localtime(now)
    at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, *TIDY_SCAN_AT, 0, 0, 0, -1))
    if at <= now - 600:
        return int(at)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int(max(midnight, now - 1800))


def tidy(state=None, now=None):
    """Make the seeded car the demo's: see above. 0, or 2 if it may not."""
    state = state or state_dir()
    why = refusal(state)
    if why:
        print(f"demoworld: not tidying: {why}", file=sys.stderr)
        return 2
    if os.path.abspath(garage.STATE) != os.path.abspath(state):
        print(f"demoworld: not tidying: the garage is in {garage.STATE}, not {state}",
              file=sys.stderr)
        return 2
    path = garage.path_for(garage.SIM_KEY)
    if not os.path.exists(path):
        print(f"demoworld: no demo car to tidy at {path} (sim.py seed makes one)",
              file=sys.stderr)
        return 2
    now = time.time() if now is None else now
    db = sqlite3.connect(path, timeout=10.0)
    try:
        with db:
            cleared = 0
            if _has(db, "faults"):
                cleared = db.execute(
                    "UPDATE faults SET status = 'cleared' "
                    "WHERE status IN ('stored', 'pending', 'permanent')").rowcount
            if _has(db, "modules"):
                db.execute("UPDATE modules SET codes = '[]' WHERE codes IS NOT '[]'")
            if _has(db, "readiness"):
                db.execute("UPDATE readiness SET complete = 1, why = '' "
                           "WHERE supported = 1 AND (complete = 0 OR why IS NOT '')")
            if _has(db, "mode06"):
                for mid, value, lo, hi in db.execute(
                        "SELECT mid, value, lo, hi FROM mode06").fetchall():
                    good = _healthy(value, lo, hi)
                    if good != value:
                        db.execute("UPDATE mode06 SET value = ? WHERE mid = ?", (good, mid))
                    if mid in TIDY_NOTES:
                        db.execute("UPDATE mode06 SET note = ? WHERE mid = ?",
                                   (TIDY_NOTES[mid], mid))
            if _has(db, "service"):
                odo = sim.ODO_NOW + TIDY_MARGIN_KM
                for item, last_km, last_at, ikm, idays in db.execute(
                        "SELECT item, last_km, last_at, interval_km, interval_days "
                        "FROM service").fetchall():
                    used = [((odo - last_km) / ikm) if ikm and last_km else 0.0,
                            ((now + 2 * 86400 - last_at) / 86400.0 / idays)
                            if idays and last_at else 0.0]
                    if (1.0 - max(used)) * 100 <= records.LIFE_SOON + 5:
                        db.execute(
                            "UPDATE service SET last_km = ?, last_at = ? WHERE item = ?",
                            (round(sim.ODO_NOW - (1 - TIDY_LIFE) * ikm, 1) if ikm else last_km,
                             round(now - (1 - TIDY_LIFE) * idays * 86400) if idays else last_at,
                             item))
                for item, note in TIDY_SERVICE_NOTES.items():
                    db.execute("UPDATE service SET note = ? WHERE item = ?", (note, item))
            if _has(db, "vehicle"):
                row = db.execute("SELECT v FROM vehicle WHERE k = 'surveyed_at'").fetchone()
                had = None
                try:
                    had = json.loads(row[0]) if row else None
                except (TypeError, ValueError):
                    pass
                # Earlier today, once: a second tidy the same day keeps the stamp.
                if not (isinstance(had, (int, float))
                        and time.localtime(had)[:3] == time.localtime(now)[:3] and had <= now):
                    db.execute("INSERT OR REPLACE INTO vehicle VALUES ('surveyed_at', ?)",
                               (json.dumps(_earlier_today(now)),))
    finally:
        db.close()
    print(f"demoworld: the demo car is tidy: {cleared} code(s) cleared, "
          "all systems normal", file=sys.stderr)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "tidy":
        return tidy()
    if not argv or argv[0] != "run":
        print("usage: demoworld.py run [--drive PATH] | demoworld.py tidy", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="demoworld.py run")
    ap.add_argument("--drive", help="the drive to play (default: the built one)")
    args = ap.parse_args(argv[1:])
    path, warning = resolve_drive(args.drive)
    if warning:
        print(warning, file=sys.stderr)
    try:
        drive = load_drive(path)
    except (OSError, ValueError) as e:
        print(f"demoworld: cannot play {path}: {e}", file=sys.stderr)
        return 2
    return run(drive)


if __name__ == "__main__":
    sys.exit(main())
