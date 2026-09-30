#!/usr/bin/env python3
"""The meetup demo's car: a scripted drive, played in real time.

There is no car in the room, so the demo's car is this. It plays one drive,
built by tools/demo_route.py from a real route, a second at a time; writes the
same live.json the daemon and the simulator write, so the app reads it without
knowing the difference; and takes cues from the presenter -- park, drive, get
drowsy, brake hard, start over.

    python3 lib/demoworld.py run [--drive PATH]

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

def load_drive(path):
    with open(path, encoding="utf-8") as f:
        drive = json.load(f)
    if not isinstance(drive.get("points"), list) or len(drive["points"]) < 2 \
            or not drive.get("loop_secs"):
        raise ValueError("not a drive: it needs `points` and `loop_secs`")
    return drive


def resolve_drive(path=None, root=ROOT):
    """(path, warning). The real drive is built on this machine and private, so
    without it the demo still runs, on the short fixture, and says so."""
    if path:
        return path, None
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
            "ima": {"state": ima, "kw": round(kw, 1)},
        },
    }


# ---- the world -----------------------------------------------------------------

def _crossed(t_event, before, after):
    """Did the loop's clock pass t_event going from `before` to `after`, the
    clock wrapping at the loop's end if `after` is the smaller?"""
    if after >= before:
        return before < t_event <= after
    return t_event > before or t_event <= after


class DemoWorld:
    """The drive, played. step() advances it by the time elapsed since the last
    step and returns what live.json should say.

    Two ways the car can be. Following, it is where the drive has it at
    `tau`, the loop's time, which runs at the clock's pace. Driving itself
    (own), after a cue, it keeps its own place `s` on the route and its own
    speed `v`, and `tau` is where on the loop that place is; it goes back to
    following when its speed is the drive's again.
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
        self.soc = SOC_START
        self.odo_m = 0.0

    # -- cues

    def cue(self, kind):
        """Act on a cue now. False for one it does not know."""
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
            self.cue(kind)

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
        """Back to the start of the loop, and the pack with it."""
        self.tau = 0.0
        self.own = False
        self.holding = False
        self.brake_left = 0.0
        self.soc = SOC_START

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
        before = self.tau
        if self.own:
            self._drive_self(h)
            v, a = self.v, self.a
        else:
            self.tau += h
            if self.tau >= self.loop:
                # The loop starts over, and so does the pack. Left to itself it
                # gains a few per cent a loop, and an evening of loops would pin
                # it at the top, where it could never be seen charging.
                self.tau %= self.loop
                self.soc = SOC_START
            v = sample(self.drive, self.tau)["kph"] / 3.6
            a = base_accel(self.drive, self.tau)
        for e in self.drive.get("events") or []:
            if _crossed(e["t"], before, self.tau):
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
            target = kph_at_s(drive, self.s) / 3.6
            accel = max(-RECOVER_BRAKE, min(RECOVER_ACCEL, (target - self.v) / h))
            self.recovering += h
        v_new = max(0.0, self.v + accel * h)
        self.a = (v_new - self.v) / h
        self.s = min(self.route_end, self.s + (self.v + v_new) / 2 * h)
        self.v = v_new
        self.tau = t_of_s(drive, self.s)

        if not self.holding and self.brake_left == 0:
            target = kph_at_s(drive, self.s) / 3.6
            if self.v >= target - REJOIN_MPS or self.recovering > RECOVER_LIMIT:
                self.own = False

    # -- what it says

    def payload(self):
        now = self.clock()
        if self.event and now >= self.event_until:
            self.event = None
        cue = {"now": self.wall(), "soc": self.soc, "odo_m": self.odo_m,
               "run_time": int(now - self.started), "hold": self.holding,
               "drowsy": now < self.drowsy_until, "event": self.event}
        if self.own:
            cue["kph"], cue["accel"] = self.v * 3.6, self.a
        p = state_at(self.drive, self.tau, cue)
        p["t"] = self.wall()
        p["uptime"] = now - self.started
        return p


# ---- the process ---------------------------------------------------------------

def state_dir():
    return os.environ.get("OMACAR_STATE") or sim.STATE


def run(drive):
    """Publish live.json at 5 Hz until told to stop."""
    state = state_dir()
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "run":
        print("usage: demoworld.py run [--drive PATH]", file=sys.stderr)
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
