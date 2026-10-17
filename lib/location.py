#!/usr/bin/env python3
"""Where the car is, when something can say so -- and "no fix" when nothing can.

THERE IS NO GPS ON THIS TABLET YET. A u-blox NEO-M9V behind gpsd is planned
(doc/design, the offline navigation plan). This file is the seam it plugs into,
written first so that the day gpsd appears, arrival and pickup pre-cooling
(lib/arrival.py) start working with no change anywhere else.

THREE RULES.
  * A position is only ever one that gpsd reported with a fix (TPV mode 2 or 3).
    No gpsd, no fix, a stale fix: the answer is "no fix", never a made-up point.
  * The simulator labels itself. `source` is "gpsd", "sim" or "none", the app
    shows it every time, and nothing in the simulator pretends to be gpsd.
  * Distances and ETAs are pure functions (haversine_m, eta_s) that are tested
    without a socket or a clock.

THE PLACES. ~/.config/omacar/places.json:

    {"home": {"lat": 37.77, "lon": -122.43, "radius_m": 150},
     "pickups": [{"name": "Target Marina", "lat": 37.80, "lon": -122.43}],
     "fridge_eco_c": 5.0,
     "auto_pickup": false,
     "house_command": ""}

`house_command` is the owner's own hook for the "House" key on the arrival card
(doc/arrival.md). It is read from this file only. Nothing in the app, the API or
the CLI can set it, so a page cannot make this machine run a command.

    omacar places show
    omacar places set home <lat> <lon> [radius_m]
    omacar places pickup add <name> <lat> <lon>
    omacar places pickup rm <name>
    omacar location status       what location.json says now
    omacar location run          follow gpsd (omacar-location.service)
    omacar location sim [home|<pickup name>] [seconds]
                                 a simulated drive toward a place, labelled Simulated
"""

import json
import math
import os
import socket
import sys
import time

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
STATE = os.path.join(STATE_DIR, "location.json")
PLACES = os.path.join(CONFIG_DIR, "places.json")

EARTH_M = 6371008.8
STALE = 10            # s: a fix older than this is not "where the car is"
RECENT_KEEP = 120     # s of position history kept, for "approaching"
DEFAULT_RADIUS_M = 150.0
DEFAULT_ECO_C = 5.0
MIN_MOVING_MS = 1.0   # m/s (3.6 km/h): below this there is no ETA, only "stopped"
GPSD = ("127.0.0.1", 2947)


# ---- pure geometry -------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, 0..360 clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def offset(lat, lon, bearing, metres):
    """The point `metres` away from (lat, lon) along `bearing`."""
    d, b = metres / EARTH_M, math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def eta_s(distance_m, speed_ms):
    """Seconds to cover `distance_m` at `speed_ms`, or None when there is no
    honest answer: no distance, no speed, or going too slowly to call it a trip."""
    if distance_m is None or speed_ms is None:
        return None
    if speed_ms < MIN_MOVING_MS:
        return None
    return distance_m / speed_ms


def approaching(recent, lat, lon, min_drop_m=15.0):
    """True when the car is getting closer to (lat, lon).

    `recent` is [[t, lat, lon], ...], oldest first. It is approaching when the
    newest point is at least `min_drop_m` nearer than the oldest one (GPS noise
    on a stopped car is a few metres, so a parked car is never "approaching").
    None when there is too little history to say.
    """
    if not recent or len(recent) < 2:
        return None
    first, last = recent[0], recent[-1]
    if last[0] - first[0] < 3:
        return None
    d0 = haversine_m(first[1], first[2], lat, lon)
    d1 = haversine_m(last[1], last[2], lat, lon)
    return d0 - d1 >= min_drop_m


# ---- places ----------------------------------------------------------------------------

def _num(v, lo, hi, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is not a number")
    if not math.isfinite(f) or not lo <= f <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return f


def clean_name(name):
    n = " ".join(str(name or "").split())[:40]
    if not n:
        raise ValueError("a pickup needs a name")
    return n


def load_places():
    """places.json as the app uses it; a missing or broken file is no places."""
    out = {"home": None, "pickups": [], "fridge_eco_c": DEFAULT_ECO_C, "house_command": "",
           "auto_pickup": False}
    try:
        with open(PLACES, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return out
    except (OSError, ValueError):
        return out
    try:
        h = raw.get("home")
        if isinstance(h, dict):
            out["home"] = {"lat": _num(h["lat"], -90, 90, "latitude"),
                           "lon": _num(h["lon"], -180, 180, "longitude"),
                           "radius_m": _num(h.get("radius_m", DEFAULT_RADIUS_M), 20, 2000, "radius")}
    except (KeyError, ValueError):
        out["home"] = None
    for p in raw.get("pickups") or []:
        try:
            out["pickups"].append({"name": clean_name(p["name"]),
                                   "lat": _num(p["lat"], -90, 90, "latitude"),
                                   "lon": _num(p["lon"], -180, 180, "longitude")})
        except (KeyError, TypeError, ValueError):
            continue
    try:
        out["fridge_eco_c"] = _num(raw.get("fridge_eco_c", DEFAULT_ECO_C), -22, 10, "eco temperature")
    except ValueError:
        pass
    out["house_command"] = str(raw.get("house_command") or "")
    out["auto_pickup"] = bool(raw.get("auto_pickup", False))
    return out


def save_places(places):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = PLACES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(places, f, indent=2)
        f.write("\n")
    os.replace(tmp, PLACES)


def set_home(lat, lon, radius_m=DEFAULT_RADIUS_M):
    p = load_places()
    p["home"] = {"lat": _num(lat, -90, 90, "latitude"), "lon": _num(lon, -180, 180, "longitude"),
                 "radius_m": _num(radius_m, 20, 2000, "radius")}
    save_places(p)
    return p


def clear_home():
    p = load_places()
    p["home"] = None
    save_places(p)
    return p


def add_pickup(name, lat, lon):
    p = load_places()
    n = clean_name(name)
    rows = [r for r in p["pickups"] if r["name"].lower() != n.lower()]
    rows.append({"name": n, "lat": _num(lat, -90, 90, "latitude"),
                 "lon": _num(lon, -180, 180, "longitude")})
    if len(rows) > 12:
        raise ValueError("twelve pickup places is the limit")
    p["pickups"] = rows
    save_places(p)
    return p


def remove_pickup(name):
    p = load_places()
    p["pickups"] = [r for r in p["pickups"] if r["name"].lower() != str(name).lower()]
    save_places(p)
    return p


def public_places():
    """For the API: everything except the owner's private command text."""
    p = load_places()
    return {"home": p["home"], "pickups": p["pickups"], "fridge_eco_c": p["fridge_eco_c"],
            "auto_pickup": p["auto_pickup"], "house_set": bool(p["house_command"].strip())}


# ---- the file the app reads --------------------------------------------------------------

def blank(source):
    return {"source": source, "fix": False, "lat": None, "lon": None, "speed_ms": None,
            "track": None, "mode": 0, "recent": [], "note": None, "at": time.time()}


def write(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def read_public(now=None):
    """What the car's position is right now, or honestly isn't.

    `fix` is True only for a recent fix. A stale file keeps its last point (as
    `last`) so the screen can say "Last seen 3 min ago" but `lat`/`lon` are None:
    nothing downstream can act on a position that old.
    """
    now = time.time() if now is None else now
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"present": False, "source": "none", "fix": False, "lat": None, "lon": None,
                "speed_ms": None, "track": None, "recent": [], "age": None,
                "note": "No GPS receiver yet (gpsd is not running)"}
    st["present"] = True
    st["age"] = max(0.0, now - float(st.get("at", 0)))
    if st["age"] > STALE or not st.get("fix"):
        if st.get("lat") is not None:
            st["last"] = {"lat": st["lat"], "lon": st["lon"]}
        st.update(fix=False, lat=None, lon=None, speed_ms=None, track=None)
    return st


# ---- gpsd -------------------------------------------------------------------------------

def parse_tpv(line):
    """One gpsd JSON line -> {lat, lon, speed_ms, track, mode} for a TPV with a
    fix (mode 2 or 3), else None. Pure."""
    try:
        m = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("class") != "TPV":
        return None
    mode = m.get("mode")
    if mode not in (2, 3):
        return None
    lat, lon = m.get("lat"), m.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    sp, tr = m.get("speed"), m.get("track")
    return {"lat": float(lat), "lon": float(lon), "mode": int(mode),
            "speed_ms": float(sp) if isinstance(sp, (int, float)) else None,
            "track": float(tr) if isinstance(tr, (int, float)) else None}


def push_recent(st, now):
    """Append the current point to the short history and drop old ones."""
    if st.get("lat") is None:
        return
    r = st.setdefault("recent", [])
    r.append([round(now, 1), round(st["lat"], 6), round(st["lon"], 6)])
    while r and now - r[0][0] > RECENT_KEEP:
        r.pop(0)


def run(host=GPSD[0], port=GPSD[1]):
    """Follow gpsd. A missing gpsd is not an error: it is "no fix", every second,
    until it appears."""
    st = blank("none")
    st["note"] = "Waiting for gpsd"
    while True:
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(b'?WATCH={"enable":true,"json":true}\n')
                s.settimeout(5)
                buf = b""
                st["note"] = "gpsd connected, waiting for a fix"
                write(st)
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        chunk = b"\0"
                    if not chunk:
                        break
                    if chunk != b"\0":
                        buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        tpv = parse_tpv(line.decode("utf-8", "replace"))
                        now = time.time()
                        if tpv:
                            st.update(source="gpsd", fix=True, note=None, **tpv)
                            push_recent(st, now)
                            write(st)
                        elif b'"TPV"' in line:       # a TPV with no fix
                            st.update(source="gpsd", fix=False, lat=None, lon=None,
                                      speed_ms=None, track=None, mode=1,
                                      note="gpsd has no fix yet")
                            write(st)
        except OSError:
            st = blank("none")
            st["note"] = "No GPS receiver yet (gpsd is not running)"
            write(st)
        time.sleep(2)


# ---- the simulator ----------------------------------------------------------------------

def sim(target="home", seconds=None, speed_kph=48.0, start_m=6000.0, tick=1.0, bearing=215.0):
    """A drive toward a saved place, labelled sim, that slows and stops on arrival.

    It drives toward `target` ("home" or a pickup's name) from `start_m` away.
    With no such place set it writes "no fix" with a note saying so: a simulator
    that invented a home would be a simulator that invented a destination.
    """
    places = load_places()
    if target == "home":
        dest = places["home"]
    else:
        dest = next((p for p in places["pickups"] if p["name"].lower() == target.lower()), None)
    st = blank("sim")
    if not dest:
        st["note"] = f"Simulated: no place called {target!r} is saved"
        write(st)
        return 2
    lat0, lon0 = offset(dest["lat"], dest["lon"], bearing, start_m)
    heading = bearing_deg(lat0, lon0, dest["lat"], dest["lon"])
    covered, begin = 0.0, time.time()
    v_ms = speed_kph / 3.6
    while seconds is None or time.time() - begin < seconds:
        remaining = max(0.0, start_m - covered)
        # Slow for the last 300 m, stop at the door.
        v = v_ms if remaining > 300 else max(0.0, v_ms * remaining / 300.0)
        if remaining < 8:
            v = 0.0
        covered = min(start_m, covered + v * tick)
        lat, lon = offset(lat0, lon0, heading, covered)
        st.update(fix=True, lat=lat, lon=lon, speed_ms=round(v, 2), track=round(heading, 1),
                  mode=3, note="Simulated drive toward " + ("home" if target == "home" else dest["name"]))
        push_recent(st, time.time())
        write(st)
        time.sleep(tick)
    return 0


# ---- the CLI -----------------------------------------------------------------------------

def _places_cli(argv):
    what = argv[0] if argv else "show"
    try:
        if what == "show":
            print(json.dumps(public_places(), indent=2))
        elif what == "set" and len(argv) >= 4 and argv[1] == "home":
            set_home(argv[2], argv[3], argv[4] if len(argv) > 4 else DEFAULT_RADIUS_M)
            print(f"  home set to {argv[2]}, {argv[3]}")
        elif what == "clear" and argv[1:2] == ["home"]:
            clear_home()
            print("  home cleared")
        elif what == "pickup" and argv[1:2] == ["add"] and len(argv) == 5:
            add_pickup(argv[2], argv[3], argv[4])
            print(f"  pickup {argv[2]} saved")
        elif what == "pickup" and argv[1:2] == ["rm"] and len(argv) == 3:
            remove_pickup(argv[2])
            print(f"  pickup {argv[2]} removed")
        else:
            print("  omacar places: show | set home <lat> <lon> [radius_m] | clear home\n"
                  "                 pickup add <name> <lat> <lon> | pickup rm <name>", file=sys.stderr)
            return 2
    except ValueError as e:
        print(f"  {e}", file=sys.stderr)
        return 2
    return 0


def main(argv):
    if argv and argv[0] == "places":
        return _places_cli(argv[1:])
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "run":
        return run()
    if what == "sim":
        target = argv[1] if len(argv) > 1 else "home"
        secs = float(argv[2]) if len(argv) > 2 else None
        return sim(target, secs)
    print("  omacar location: status | run | sim [home|<pickup>] [seconds]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
