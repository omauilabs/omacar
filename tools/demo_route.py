#!/usr/bin/env python3
"""Build the meetup demo's drive from an OSRM route.

There is no car in the room, so the demo's car drives a scripted loop: about
fifteen minutes of a real trip, replayed second by second. This turns OSRM's
answer for that trip into the file the demo plays, `drive.json`:

    python3 tools/demo_route.py --osrm map/raw/osrm-route.json \\
                                --out demo/drive.json [--loop-secs 900]

What it reads is one route, as OSRM's `route` service returns it with
`annotations=duration,distance,speed`, `geometries=geojson` and `steps=true`:
the geometry, each leg's steps (what to do, where, on which road) and each
leg's per-segment speeds.

What it makes
-------------
`points`, one per second: where the car is, how fast, which way it faces, how
far along the route, and how many seconds of the trip are left. It is built
by driving the route, not by drawing a line through it:

  * the speed on each stretch is OSRM's, smoothed over a couple of hundred
    metres and capped at 105 km/h, so it cruises rather than twitches;
  * it slows for every sharp turn, and stops for 25 s at the first turn, a
    stop line short of it, so the demo shows the engine's auto-stop;
  * it accelerates at no more than 2.0 m/s^2 (less at speed, as a car does)
    and brakes at no more than 2.5, and every check on that runs on the
    seconds the demo will play, not on the model that made them.

It drives for `loop_secs - 120` seconds, slows to a stop at exactly that
second, and holds for 120 s. That hold is the loop's `parked` scene.

The rest is what the screens need from the same trip: `maneuvers` (every
turn, with plain-English instructions), `streets` (which road it is on), the
route simplified to 5 m for the map to draw, and the scripted hard brake.
"""

import argparse
import bisect
import json
import math
import os
import re
import sys

NAME = "Marina to San Francisco on Highway 1"
DESTINATION = {"label": "Omarchy Meetup, San Francisco",
               "lat": 37.7793, "lon": -122.4193}

MAX_KPH = 105.0
MIN_KPH = 20.0             # the slowest a stretch of road is driven at
STOP_SECS = 25             # the first traffic light
STOP_LINE_M = 8.0          # ... and how short of the turn the car waits
START_HOLD = 3             # seconds at rest before it pulls away
PARK_SECS = 120            # the loop's closing hold
SMOOTH_M = 150.0           # half-width of the window the road speeds are averaged over
SIMPLIFY_M = 5.0

# What the drive is held to (problems() checks it), and what it plans on. The
# plan is under the limit -- braking at 1.8 and, in accel_limit(), pulling away
# at 1.9 -- so that a speed rounded to 0.01 km/h, and the centimetre a stop is
# closed by, can never take a second's change over it.
MAX_ACCEL, MAX_BRAKE = 2.0, 2.5
PLAN_BRAKE = 1.8           # ahead of a stop or a slower road
RAMP_BRAKE = 2.0           # the final stop, which is at a time and not at a place

SUBSTEPS = 20              # the drive is integrated at 1/20 s, and sampled at 1 Hz
HORIZON_M = 300.0          # how far ahead it looks: braking from 105 km/h at 1.8 takes 237 m

# Speed through a manoeuvre, km/h. Slight turns, forks and ramps are driven at
# the road's own speed.
TURN_KPH = {"uturn": 10.0, "sharp left": 15.0, "sharp right": 15.0,
            "left": 20.0, "right": 20.0}
ROUNDABOUT_KPH = 25.0

SUFFIXES = {"Road": "Rd", "Street": "St", "Avenue": "Ave", "Boulevard": "Blvd",
            "Drive": "Dr", "Parkway": "Pkwy", "Highway": "Hwy", "Lane": "Ln",
            "Court": "Ct", "Freeway": "Fwy", "Place": "Pl", "Terrace": "Ter",
            "Circle": "Cir", "Expressway": "Expy"}


# ---- geometry ------------------------------------------------------------------

def haversine(a, b):
    """Metres between two (lat, lon) points."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def bearing(a, b):
    """Compass degrees from a to b, 0 north, clockwise."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def cardinal(deg):
    return "NESW"[int(((deg % 360.0) + 45.0) // 90.0) % 4]


def simplify(points, tolerance_m):
    """Douglas-Peucker on [lat, lon] pairs: what is left is within tolerance_m
    of the line it was taken from. Iterative, because a route has thousands of
    points and a bend at each end would otherwise hit the recursion limit."""
    n = len(points)
    if n < 3:
        return [list(p) for p in points]
    lat0 = points[0][0]
    kx = 111320.0 * math.cos(math.radians(lat0))
    xy = [((p[1]) * kx, p[0] * 110540.0) for p in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        far, at = -1.0, -1
        for k in range(i + 1, j):
            x, y = xy[k]
            if length == 0:
                d = math.hypot(x - x1, y - y1)
            else:
                d = abs(dy * (x - x1) - dx * (y - y1)) / length
            if d > far:
                far, at = d, k
        if far > tolerance_m:
            keep[at] = True
            stack.append((i, at))
            stack.append((at, j))
    return [list(p) for p, k in zip(points, keep) if k]


def cumulative(coords):
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine(a, b))
    return cum


class Line:
    """Where a distance along the route is, and which way the road runs there."""

    def __init__(self, coords):
        self.coords = coords
        self.cum = cumulative(coords)
        self.total = self.cum[-1]

    def at(self, s):
        s = min(max(s, 0.0), self.total)
        i = min(bisect.bisect_right(self.cum, s) - 1, len(self.coords) - 2)
        span = self.cum[i + 1] - self.cum[i]
        f = (s - self.cum[i]) / span if span else 0.0
        a, b = self.coords[i], self.coords[i + 1]
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

    def heading(self, s, look=15.0):
        """The road's direction over the next `look` metres (the last ones, at the end)."""
        a = min(max(s, 0.0), max(self.total - look, 0.0))
        return bearing(self.at(a), self.at(min(a + look, self.total)))


# ---- steps and their words -----------------------------------------------------

def short_name(name):
    words = name.split()
    if words:
        words[-1] = SUFFIXES.get(words[-1], words[-1])
    return " ".join(words)


def road_label(step):
    """A step's road as a person says it: 'CA-1 N' for a numbered one, else its name."""
    ref = (step.get("ref") or "").split(";")[0].strip()
    if ref:
        ref = re.sub(r"^([A-Za-z]+)\s+(\d+)", r"\1-\2", ref)
        return f"{ref} {cardinal(step['maneuver'].get('bearing_after', 0))}"
    return short_name((step.get("name") or "").strip())


def destination_label(step):
    """Where a ramp or a fork says it goes: 'Imjin Parkway' -> 'Imjin Pkwy'."""
    text = (step.get("destinations") or "").split(":")[-1].split(",")[0].strip()
    return short_name(text)


def side_of(modifier):
    if modifier and "left" in modifier:
        return "left"
    if modifier and "right" in modifier:
        return "right"
    return None


def instruction(step, street, destination):
    m = step["maneuver"]
    kind, mod = m["type"], m.get("modifier")
    onto = f" onto {street}" if street else ""
    toward = destination_label(step)
    side = side_of(mod)
    if kind == "arrive":
        return f"Arrive at {destination}"
    if kind == "turn" or (kind == "continue" and mod == "uturn"):
        if mod == "uturn":
            return f"Make a U-turn{onto}"
        if mod == "straight":
            return f"Continue straight{onto}"
        if mod and mod.startswith("slight"):
            return f"Bear {side}{onto}"
        return f"Turn {mod}{onto}" if mod else f"Turn{onto}"
    if kind == "end of road":
        return f"At the end of the road, turn {side}{onto}" if side else f"Turn{onto}"
    if kind == "merge":
        return f"Merge{onto}"
    if kind == "on ramp":
        if street:
            return f"Take the ramp{onto}"
        if toward:
            return f"Take the ramp toward {toward}"
        return f"Take the ramp on the {side}" if side else "Take the ramp"
    if kind == "off ramp":
        if toward:
            return f"Take the exit toward {toward}"
        return f"Take the exit on the {side}" if side else "Take the exit"
    if kind == "fork":
        keep = f"Keep {side}" if side else "Keep straight"
        if street:
            return f"{keep} to stay on {street}"
        return f"{keep} toward {toward}" if toward else keep
    if kind in ("roundabout", "rotary", "roundabout turn"):
        if m.get("exit"):
            return f"At the roundabout, take exit {m['exit']}{onto}"
        if kind == "roundabout turn" and mod:
            return f"At the roundabout, turn {mod}{onto}"
        return "Enter the roundabout"
    if kind in ("exit roundabout", "exit rotary"):
        return f"Exit the roundabout{onto}"
    return f"Continue{onto}"


def place_steps(line, steps):
    """route_m of each step's location. Steps follow the route in order, and a
    U-turn or a waypoint brings it back to a place it has been, so each is
    looked for from the previous one on."""
    coords, out, j = line.coords, [], 0
    for step in steps:
        lon, lat = step["maneuver"]["location"]
        found = None
        for k in range(j, len(coords)):
            if abs(coords[k][0] - lat) < 2e-6 and abs(coords[k][1] - lon) < 2e-6:
                found = k
                break
        if found is None:
            found = min(range(j, len(coords)),
                        key=lambda i: (coords[i][0] - lat) ** 2 + (coords[i][1] - lon) ** 2)
        j = found
        out.append(line.cum[found])
    return out


def turn_cap_kph(step):
    m = step["maneuver"]
    kind, mod = m["type"], m.get("modifier")
    if kind in ("turn", "end of road") or (kind == "continue" and mod == "uturn"):
        return TURN_KPH.get(mod)
    if kind in ("roundabout", "rotary", "roundabout turn"):
        return ROUNDABOUT_KPH
    return None


# ---- the speed profile ---------------------------------------------------------

def road_speeds(line, speeds):
    """OSRM's speed for each segment, averaged over a window of the road and
    held between MIN_KPH and MAX_KPH, in m/s. OSRM's own are per segment and
    jitter by a few km/h from one to the next."""
    cum = line.cum
    prefix = [0.0]
    for i, v in enumerate(speeds):
        prefix.append(prefix[-1] + v * (cum[i + 1] - cum[i]))

    def integral(x):
        i = min(max(bisect.bisect_right(cum, x) - 1, 0), len(speeds) - 1)
        return prefix[i] + speeds[i] * (x - cum[i])

    out = []
    for i in range(len(speeds)):
        mid = (cum[i] + cum[i + 1]) / 2
        a, b = max(0.0, mid - SMOOTH_M), min(line.total, mid + SMOOTH_M)
        avg = (integral(b) - integral(a)) / (b - a) if b > a else speeds[i]
        out.append(min(MAX_KPH, max(MIN_KPH, avg * 3.6)) / 3.6)
    return out


def accel_limit(v):
    """What the car can give at speed v (m/s): 1.9 from rest, 0.65 at 105 km/h."""
    return max(0.6, 1.9 - 0.043 * v)


def simulate(line, limits, caps, stop_s, loop_secs):
    """Drive the route and sample it every second: [(t, route_m, m/s), ...].

    The allowed speed at a place is the road's, and lower ahead of anything
    the car has to slow for: a slower road, a sharp turn, the stop, the end.
    It is the speed from which it could still brake, at a comfortable rate, to
    that thing's own speed by the time it gets there. The car takes the
    highest speed it is allowed and can reach, at the acceleration it has.
    """
    seg_start = line.cum[:-1]
    n = len(limits)
    stop = (stop_s, 0.0) if stop_s is not None else None
    # Places it must be at a given speed, and slower than it is on either side.
    musts = sorted([(line.total, 0.0)] + [(s, v / 3.6) for s, v in caps]
                   + ([stop] if stop else []))
    must_s = [m[0] for m in musts]
    t_end = loop_secs - PARK_SECS
    h = 1.0 / SUBSTEPS

    def envelope(s):
        i = min(max(bisect.bisect_right(seg_start, s) - 1, 0), n - 1)
        v = limits[i]
        for j in range(i + 1, n):
            d = seg_start[j] - s
            if d > HORIZON_M:
                break
            v = min(v, math.sqrt(limits[j] ** 2 + 2 * PLAN_BRAKE * d))
        for k in range(bisect.bisect_left(must_s, s), len(musts)):
            d = must_s[k] - s
            if d > HORIZON_M:
                break
            v = min(v, math.sqrt(musts[k][1] ** 2 + 2 * PLAN_BRAKE * d))
        return v

    out = []
    s = v = 0.0
    holding_until = float(START_HOLD)
    waiting_at_stop = stop is not None
    for sec in range(loop_secs + 1):
        out.append((sec, s, v))
        if sec == loop_secs:
            break
        for k in range(SUBSTEPS):
            now = sec + (k + 1) * h
            if now <= holding_until + 1e-9:
                v = 0.0
                continue
            target = min(envelope(s), max(0.0, RAMP_BRAKE * (t_end - now)))
            if v < target:
                v_new = min(target, v + accel_limit(v) * h)
            else:
                v_new = max(target, v - (MAX_BRAKE - 0.1) * h)
            s += (v + v_new) / 2 * h
            v = v_new
            # Arrived. The envelope brings it to rest at the stop line and at
            # the end within a centimetre or two; this closes the gap.
            if waiting_at_stop and s >= stop_s - 0.02:
                s, v = stop_s, 0.0
                holding_until = now + STOP_SECS
                waiting_at_stop = False
                musts.remove(stop)
                must_s = [m[0] for m in musts]
            elif s >= line.total - 0.02:
                s, v = line.total, 0.0
    return out


# ---- the drive -----------------------------------------------------------------

def build(osrm, loop_secs=900):
    """The drive.json document for one OSRM response."""
    if loop_secs < PARK_SECS + 60:
        raise ValueError(f"--loop-secs {loop_secs} leaves no time to drive")
    route = osrm["routes"][0]
    coords = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
    line = Line(coords)
    speeds = [v for leg in route["legs"] for v in leg["annotation"]["speed"]]
    if len(speeds) != len(coords) - 1:
        raise ValueError(
            f"the route has {len(coords)} points but {len(speeds)} segment "
            "speeds: it needs annotations=speed, and geometries=geojson")
    duration = float(route.get("duration")
                     or sum(leg["duration"] for leg in route["legs"]))
    dest_name = DESTINATION["label"].split(",")[0]

    raw = [st for leg in route["legs"] for st in leg["steps"]]
    places = place_steps(line, raw)
    last = len(raw) - 1

    maneuvers, streets = [], []
    for i, (st, s) in enumerate(zip(raw, places)):
        kind = st["maneuver"]["type"]
        street = road_label(st)
        if kind == "depart" and not street:
            street = destination_label(st)
        if kind != "arrive" and street and (not streets or streets[-1][1] != street):
            streets.append([round(s, 1), street])
        # A waypoint's arrive and the depart after it are not turns; the last
        # arrive is the destination.
        if kind == "depart" or (kind == "arrive" and i != last):
            continue
        maneuvers.append({
            "route_m": round(s, 1), "type": kind,
            "modifier": st["maneuver"].get("modifier"), "street": road_label(st),
            "instruction": instruction(st, road_label(st), dest_name)})

    caps = [(s, cap) for st, s in zip(raw, places)
            if (cap := turn_cap_kph(st)) is not None]
    first_turn = next((s for st, s in zip(raw, places)
                       if st["maneuver"]["type"] in ("turn", "end of road")), None)
    stop_s = max(0.0, first_turn - STOP_LINE_M) if first_turn is not None else None

    samples = simulate(line, road_speeds(line, speeds), caps, stop_s, loop_secs)
    points = []
    for sec, s, v in samples:
        lat, lon = line.at(s)
        points.append([sec, round(lat, 6), round(lon, 6), round(v * 3.6, 2),
                       round(line.heading(s), 1), round(s, 1),
                       round(duration * (line.total - s) / line.total, 1)])

    return {
        "version": 1,
        "name": NAME,
        "destination": dict(DESTINATION),
        "route_total_m": round(line.total, 1),
        "loop_secs": loop_secs,
        "route": [[round(a, 6), round(b, 6)]
                  for a, b in simplify([list(c) for c in coords], SIMPLIFY_M)],
        "points": points,
        "maneuvers": maneuvers,
        "streets": streets,
        "scenes": [{"t": loop_secs - PARK_SECS, "kind": "parked", "secs": PARK_SECS}],
        "events": [{"t": loop_secs // 3, "kind": "hard_brake"}],
    }


def problems(doc):
    """What is wrong with a drive, in words; nothing if it is fit to play. Run
    on the seconds that will be played, not on the model that made them."""
    pts, loop = doc["points"], doc["loop_secs"]
    out = []
    if [p[0] for p in pts] != list(range(loop + 1)):
        out.append(f"points are not one a second from 0 to {loop}")
        return out
    ms = [p[3] / 3.6 for p in pts]
    if max(p[3] for p in pts) > MAX_KPH:
        out.append(f"speed goes above {MAX_KPH:g} km/h")
    if max(b - a for a, b in zip(ms, ms[1:])) > MAX_ACCEL:
        out.append(f"acceleration goes above {MAX_ACCEL} m/s^2")
    if max(a - b for a, b in zip(ms, ms[1:])) > MAX_BRAKE:
        out.append(f"braking goes above {MAX_BRAKE} m/s^2")
    if any(b[5] < a[5] for a, b in zip(pts, pts[1:])):
        out.append("route_m goes backwards")
    for scene in doc["scenes"]:
        held = pts[scene["t"]:scene["t"] + scene["secs"] + 1]
        if any(p[3] != 0 or p[5] != held[0][5] for p in held):
            out.append(f"the {scene['kind']} scene at t={scene['t']} is not a stop")
    return out


def write(drive_doc, path):
    """One row of `points`, `route` and the lists to a line, so a diff is readable."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{\n")
        keys = list(drive_doc)
        for n, key in enumerate(keys):
            value = drive_doc[key]
            if isinstance(value, list) and value and isinstance(value[0], (list, dict)):
                rows = ",\n".join(json.dumps(r, separators=(",", ":")) for r in value)
                body = "[\n" + rows + "\n]"
            else:
                body = json.dumps(value, separators=(",", ":"))
            f.write(f"{json.dumps(key)}:{body}{',' if n < len(keys) - 1 else ''}\n")
        f.write("}\n")
    os.replace(tmp, path)


def summary(drive_doc):
    pts = drive_doc["points"]
    moved = next((i for i, p in enumerate(pts) if p[3] > 0), None)
    stop = next((p[0] for p in pts[moved:] if p[3] == 0), None) if moved is not None else None
    return [
        f"route          {drive_doc['route_total_m'] / 1000:.1f} km, "
        f"{len(drive_doc['maneuvers'])} manoeuvres, "
        f"{len(drive_doc['route'])} route points",
        f"loop           {drive_doc['loop_secs']} s, {len(pts)} points, "
        f"the last {PARK_SECS} s parked at {pts[-1][5] / 1000:.1f} km",
        f"max speed      {max(p[3] for p in pts):.1f} km/h",
        f"first stop     t={stop} s" if stop is not None else "first stop     none",
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the demo's drive.json from an OSRM route.")
    ap.add_argument("--osrm", required=True, help="the OSRM route response")
    ap.add_argument("--out", required=True, help="where to write drive.json")
    ap.add_argument("--loop-secs", type=int, default=900)
    args = ap.parse_args(argv)
    try:
        with open(args.osrm, encoding="utf-8") as f:
            osrm = json.load(f)
        doc = build(osrm, args.loop_secs)
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(f"demo_route: {args.osrm}: {e}", file=sys.stderr)
        return 2
    wrong = problems(doc)
    if wrong:
        print("demo_route: the drive is not fit to play, so it was not written:",
              file=sys.stderr)
        for w in wrong:
            print(f"  - {w}", file=sys.stderr)
        return 1
    write(doc, args.out)
    print(f"demo_route: wrote {args.out}")
    for line in summary(doc):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
