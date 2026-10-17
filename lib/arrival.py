#!/usr/bin/env python3
"""Coming home, and keeping the food cold for a pickup.

Two things, both built on lib/location.py (so both wait for a GPS fix, which
this tablet does not have yet -- doc/arrival.md says what works today).

ARRIVING HOME. Within 2 km of home and getting closer, the app offers one card
with three single-tap actions: open the garage, bring the fridge up to its eco
set point, run the owner's House command. NEVER AUTOMATIC: this file records
that you are nearly home and tells your phone once (lib/notify.py, one message
per trip); it does not open, cool, switch or run anything by itself. The
garage key is shown disabled until lib/garage_door.py exists (the garage is
design only: doc/design/2026-10-17-garage.md).

PICKUP PRE-COOL. Heading to a saved pickup place (you said so with one tap, or
you are within 15 minutes of one), the fridge is set 2 C colder 20 minutes
before you get there, bounded by the fridge's own range, and put back when you
leave the place or two hours after it started cooling, whichever comes first.
It puts back only if the set point is still what it set: if you changed it in
the meantime, that is your choice and it leaves it.

step() is pure: state, fix, places, fridge and the clock in; new state, the
fridge writes to make and the notifications to send out. Nothing in it touches
a file, a socket or the time, which is how test/arrival_test.py runs a whole
trip on a fake clock. tick() does the I/O; `omacar arrival run` calls it once a
second (omacar-arrival.service).

    omacar arrival status      what the app would be shown now
    omacar arrival run         the loop
    omacar arrival pickup <name> [eta_min]    heading to a saved pickup
    omacar arrival cancel      stop (and restore the fridge if it was cooled)
"""

import contextlib
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import location  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = location.STATE_DIR
STATE = os.path.join(STATE_DIR, "arrival.json")
LOCK = os.path.join(STATE_DIR, "arrival.lock")

NEAR_HOME_M = 2000.0        # the card appears inside this, when approaching
CLEAR_HOME_M = 3000.0       # ...and this far away ends the trip
PRECOOL_LEAD_S = 20 * 60    # start cooling this long before the ETA
AUTO_ETA_S = 15 * 60        # proximity: a saved pickup this close in time
COLDER_BY_C = 2.0
RESTORE_AFTER_S = 2 * 60 * 60
AT_PLACE_M = 150.0          # "at the pickup"
LEFT_PLACE_M = 400.0        # "has left it"
RESUPPRESS_S = 4 * 60 * 60  # after a pickup ends, don't auto-pick the same place
STALE_TICK_S = 15
FRIDGE_RANGE = (-22.0, 10.0)


def blank_state():
    return {"trip": None, "pickup": None, "suppressed": {}, "last_pickup": None, "tick_at": None}


# ---- arriving home (pure) ---------------------------------------------------------------

def home_view(fix, home, trip, now):
    """The arrival picture for this moment, and the next trip record.

    -> (view, trip). view["phase"] is one of: no_home, no_fix, away, approaching,
    arrived. `trip` is {id, notified, dismissed} while a trip is in progress.
    """
    if not home:
        return {"phase": "no_home", "dist_m": None, "eta_s": None}, None
    if not fix or fix.get("lat") is None:
        return {"phase": "no_fix", "dist_m": None, "eta_s": None}, trip
    dist = location.haversine_m(fix["lat"], fix["lon"], home["lat"], home["lon"])
    eta = location.eta_s(dist, fix.get("speed_ms"))
    if trip is not None and dist > CLEAR_HOME_M:
        trip = None                                   # left again: the next arrival is a new trip
    toward = location.approaching(fix.get("recent"), home["lat"], home["lon"])
    if toward is None and fix.get("track") is not None and dist > 1:
        b = location.bearing_deg(fix["lat"], fix["lon"], home["lat"], home["lon"])
        toward = abs((b - fix["track"] + 180) % 360 - 180) <= 60
    at_door = dist <= home["radius_m"]
    if trip is None and dist <= NEAR_HOME_M and (toward or at_door):
        trip = {"id": str(int(now)), "notified": False, "dismissed": False}
    view = {"dist_m": round(dist), "eta_s": None if eta is None else round(eta)}
    if trip is None:
        view["phase"] = "away"
    else:
        view["phase"] = "arrived" if at_door else "approaching"
    view["dismissed"] = bool(trip and trip["dismissed"])
    return view, trip


# ---- the pickup plan (pure) -----------------------------------------------------------------

def _place(places, name):
    return next((p for p in places["pickups"] if p["name"].lower() == str(name).lower()), None)


def new_pickup(name, now, eta_min=None, mode="explicit"):
    return {"name": name, "mode": mode, "chosen_at": now,
            "manual_eta_at": None if eta_min is None else now + 60.0 * eta_min,
            "base_set": None, "cooled_to": None, "cooled_at": None,
            "arrived_at": None, "cancel": False, "note": None}


def cold_target(base, rng):
    """2 C colder than `base`, held inside the fridge's range, on its half-degree steps."""
    lo = rng[0] if rng else FRIDGE_RANGE[0]
    return max(lo, round((base - COLDER_BY_C) * 2) / 2)


def pickup_eta(pk, fix, place, now):
    """(dist_m or None, seconds to arrival or None). A fix beats a guess the
    owner typed in; with neither there is no ETA."""
    if fix and fix.get("lat") is not None:
        d = location.haversine_m(fix["lat"], fix["lon"], place["lat"], place["lon"])
        e = location.eta_s(d, fix.get("speed_ms"))
        if e is not None:
            return d, e
        if pk.get("manual_eta_at") is None:
            return d, None
    else:
        d = None
    if pk.get("manual_eta_at") is not None:
        return d, max(0.0, pk["manual_eta_at"] - now)
    return d, None


def plan_pickup(pk, fix, places, fridge, now):
    """-> (pickup or None, actions, ended). One tick of the pre-cool plan.

    actions: [{"set": celsius, "why": str}]. ended: a record of a finished
    pickup for the card to remember, or None.
    """
    actions = []
    place = _place(places, pk["name"])
    if place is None:
        return None, actions, None
    pk = dict(pk)
    fset = fridge.get("set") if fridge.get("connected") else None
    rng = fridge.get("range") or list(FRIDGE_RANGE)
    d, eta = pickup_eta(pk, fix, place, now)
    if d is not None and d <= AT_PLACE_M and pk["arrived_at"] is None:
        pk["arrived_at"] = now
    left = pk["arrived_at"] is not None and d is not None and d > LEFT_PLACE_M
    timed_out = pk["cooled_at"] is not None and now - pk["cooled_at"] >= RESTORE_AFTER_S
    if pk["cancel"] or left or timed_out:
        why = "cancelled" if pk["cancel"] else "left" if left else "two hours"
        restored = None
        if pk["cooled_to"] is not None and pk["base_set"] is not None:
            if fset is None:
                # Can't reach the fridge now: keep the plan, try again.
                pk["note"] = "Fridge not connected: will restore when it is"
                return pk, actions, None
            if abs(fset - pk["cooled_to"]) < 0.25 and abs(pk["cooled_to"] - pk["base_set"]) >= 0.25:
                actions.append({"set": pk["base_set"], "why": f"pickup over ({why})"})
                restored = pk["base_set"]
        return None, actions, {"name": pk["name"], "at": now, "why": why,
                               "restored_to": restored, "cooled_to": pk["cooled_to"]}
    if pk["cooled_to"] is None and (pk["arrived_at"] is not None
                                    or (eta is not None and eta <= PRECOOL_LEAD_S)):
        if fset is None:
            pk["note"] = "Fridge not connected: will cool when it is"
        else:
            tgt = cold_target(fset, rng)
            pk["base_set"], pk["cooled_to"], pk["cooled_at"], pk["note"] = fset, tgt, now, None
            if tgt < fset:
                actions.append({"set": tgt, "why": "cooling for the pickup"})
            else:
                pk["note"] = "Already as cold as the fridge goes"
    return pk, actions, None


def _heading_for(fix, place, home, dist):
    """Is the car plausibly going TO this place, not past it or home? Its course
    must point within 45 degrees of the place, and home must not be nearer: a
    drive home that happens to pass a pickup is not a pickup."""
    if home is not None:
        if location.haversine_m(fix["lat"], fix["lon"], home["lat"], home["lon"]) <= dist:
            return False
    if fix.get("track") is not None:
        b = location.bearing_deg(fix["lat"], fix["lon"], place["lat"], place["lon"])
        if abs((b - fix["track"] + 180) % 360 - 180) > 45:
            return False
    return True


def step(state, fix, places, fridge, now):
    """-> (state, actions, events). Pure; see the module docstring."""
    st = json.loads(json.dumps(state or blank_state()))
    st["tick_at"] = now
    actions, events = [], []
    # Arrival
    f = None
    if fix and fix.get("lat") is not None:
        f = dict(fix)
    view, st["trip"] = home_view(f, places.get("home"), st.get("trip"), now)
    trip = st["trip"]
    if trip and view["phase"] in ("approaching", "arrived") and not trip["notified"]:
        trip["notified"] = True
        events.append({"kind": "arrival", "key": f"arrival-{trip['id']}",
                       "title": "Almost home" if view["phase"] == "approaching" else "Home",
                       "dist_m": view["dist_m"], "eta_s": view["eta_s"]})
    # Pickup
    st["suppressed"] = {k: v for k, v in (st.get("suppressed") or {}).items() if now - v < RESUPPRESS_S}
    if st.get("pickup"):
        pk, acts, ended = plan_pickup(st["pickup"], f, places, fridge, now)
        st["pickup"] = pk
        actions += acts
        if ended:
            st["last_pickup"] = ended
            st["suppressed"][ended["name"]] = now
    elif f and places.get("auto_pickup", True):
        for p in places["pickups"]:
            if p["name"] in st["suppressed"]:
                continue
            d = location.haversine_m(f["lat"], f["lon"], p["lat"], p["lon"])
            e = location.eta_s(d, f.get("speed_ms"))
            if (e is not None and e <= AUTO_ETA_S and d > AT_PLACE_M
                    and location.approaching(f.get("recent"), p["lat"], p["lon"])
                    and _heading_for(f, p, places.get("home"), d)):
                st["pickup"] = new_pickup(p["name"], now, mode="auto")
                pk, acts, _ = plan_pickup(st["pickup"], f, places, fridge, now)
                st["pickup"] = pk
                actions += acts
                break
    return st, actions, events


# ---- storage ------------------------------------------------------------------------------

@contextlib.contextmanager
def locked():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            return {**blank_state(), **st}
    except (OSError, ValueError):
        pass
    return blank_state()


def save(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def _fridge_now():
    import fridge
    f = fridge.read_public()
    z = (f.get("zones") or [{}])[0] if f.get("present") else {}
    return {"connected": bool(f.get("present") and f.get("connected") and f.get("power") is not False),
            "set": z.get("set"), "range": z.get("range")}


def _fix_now():
    loc = location.read_public()
    if not loc.get("fix"):
        return None
    return {"lat": loc["lat"], "lon": loc["lon"], "speed_ms": loc.get("speed_ms"),
            "track": loc.get("track"), "recent": loc.get("recent") or []}


def _km(m):
    if m is None:
        return ""
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{int(round(m, -1))} m"


def tick(now=None):
    """One pass with the real world: read, decide, write, then do what was decided."""
    import fridge
    import notify
    now = time.time() if now is None else now
    with locked():
        st = load()
        st, actions, events = step(st, _fix_now(), location.load_places(), _fridge_now(), now)
        save(st)
    for a in actions:
        try:
            fridge.ask("z0_set", a["set"])
        except ValueError:
            pass
    for e in events:
        body = f"{_km(e['dist_m'])} from home" + (f", about {max(1, round(e['eta_s'] / 60))} min" if e["eta_s"] else "")
        notify.send("arrival", e["title"], body, "info", key=e["key"], now=now)
    return actions, events


def run():
    while True:
        try:
            tick()
        except Exception as e:                       # noqa: BLE001 -- a loop that must not die
            print(f"  arrival: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(1.0)


# ---- what the API shows ---------------------------------------------------------------------

def garage_installed():
    return os.path.exists(os.path.join(ROOT, "lib", "garage_door.py"))


def read_public(now=None):
    now = time.time() if now is None else now
    loc = location.read_public(now)
    places = location.load_places()
    st = load()
    fix = _fix_now() if loc.get("fix") else None
    view, _ = home_view(fix, places.get("home"), st.get("trip"), now)
    fr = _fridge_now()
    pk = st.get("pickup")
    pview = None
    if pk:
        place = _place(places, pk["name"]) or {}
        d, eta = pickup_eta(pk, fix, place, now) if place else (None, None)
        base = pk["base_set"] if pk["base_set"] is not None else fr["set"]
        tgt = pk["cooled_to"] if pk["cooled_to"] is not None else (
            cold_target(base, fr["range"]) if base is not None else None)
        pview = {"name": pk["name"], "mode": pk["mode"], "dist_m": None if d is None else round(d),
                 "eta_s": None if eta is None else round(eta),
                 "by": None if eta is None else round(now + eta),
                 "cooling": pk["cooled_to"] is not None, "target_c": tgt, "base_c": pk["base_set"],
                 "starts_in_s": None if eta is None else max(0, round(eta - PRECOOL_LEAD_S)),
                 "arrived": pk["arrived_at"] is not None, "note": pk["note"],
                 "manual": pk.get("manual_eta_at") is not None and not (fix and fix.get("speed_ms"))}
    tick_age = None if st.get("tick_at") is None else max(0.0, now - st["tick_at"])
    return {
        "location": {"source": loc.get("source"), "fix": bool(loc.get("fix")), "age": loc.get("age"),
                     "note": loc.get("note"),
                     "here": {"lat": loc["lat"], "lon": loc["lon"]} if loc.get("fix") else None},
        "places": {"home": places["home"], "pickups": places["pickups"]},
        "arrival": view,
        "actions": {
            "garage": {"available": garage_installed(),
                       "why": None if garage_installed() else "Garage controller not installed"},
            "fridge": {"available": fr["connected"], "eco_c": places["fridge_eco_c"], "set_c": fr["set"],
                       "why": None if fr["connected"] else "Fridge not connected"},
            "house": {"available": bool(places["house_command"].strip()),
                      "why": None if places["house_command"].strip() else "No House command set"},
        },
        "pickup": pview,
        "last_pickup": st.get("last_pickup"),
        "fridge": {"connected": fr["connected"], "set_c": fr["set"], "range": fr["range"]},
        "watching": tick_age is not None and tick_age < STALE_TICK_S,
        "now": round(now),
    }


# ---- what the screen may ask ------------------------------------------------------------------

def do(action, data):
    """The API's POST. -> (http status, body). Every action is one tap's worth."""
    if action == "dismiss":
        with locked():
            st = load()
            if st.get("trip"):
                st["trip"]["dismissed"] = True
                save(st)
        return 200, {"ok": True}
    if action == "fridge_eco":
        import fridge
        eco = location.load_places()["fridge_eco_c"]
        try:
            return 200, fridge.ask("z0_set", eco)
        except ValueError as e:
            return 400, {"error": str(e)}
    if action == "house":
        cmd = location.load_places()["house_command"].strip()
        if not cmd:
            return 409, {"error": "No House command is set (places.json: house_command)"}
        try:
            subprocess.Popen(shlex.split(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, ValueError) as e:
            return 500, {"error": f"House command did not start: {e}"}
        return 200, {"ran": True}
    if action == "garage":
        if not garage_installed():
            return 409, {"error": "Garage controller not installed"}
        try:
            import garage_door
            opener = getattr(garage_door, "open_door")
        except (ImportError, AttributeError):
            return 501, {"error": "lib/garage_door.py has no open_door()"}
        try:
            opener()
        except Exception as e:                       # noqa: BLE001 -- the controller says many things
            return 502, {"error": f"Garage controller: {e}"}
        return 200, {"opened": True}
    if action == "pickup":
        name = str(data.get("name") or "")
        if _place(location.load_places(), name) is None:
            return 400, {"error": f"no saved pickup called {name!r}"}
        eta_min = data.get("eta_min")
        if eta_min is not None:
            try:
                eta_min = float(eta_min)
                if not 0 <= eta_min <= 600:
                    raise ValueError
            except (TypeError, ValueError):
                return 400, {"error": "eta_min is minutes, 0 to 600"}
        real = _place(location.load_places(), name)["name"]
        with locked():
            st = load()
            st["pickup"] = new_pickup(real, time.time(), eta_min)
            st["suppressed"].pop(real, None)
            save(st)
        return 200, {"heading_to": real}
    if action == "pickup_cancel":
        with locked():
            st = load()
            if st.get("pickup"):
                st["pickup"]["cancel"] = True
                save(st)
        return 200, {"ok": True}
    return 400, {"error": "unknown action"}


def places_post(data):
    """The parked-only editor's POST. Home and pickups only: never the House
    command, never the eco value's file keys beyond what is listed here."""
    try:
        if "home" in data:
            h = data["home"]
            if h is None:
                location.clear_home()
            else:
                location.set_home(h["lat"], h["lon"], h.get("radius_m", location.DEFAULT_RADIUS_M))
        if "add_pickup" in data:
            p = data["add_pickup"]
            location.add_pickup(p["name"], p["lat"], p["lon"])
        if "remove_pickup" in data:
            location.remove_pickup(str(data["remove_pickup"]))
        if "fridge_eco_c" in data:
            p = location.load_places()
            p["fridge_eco_c"] = location._num(data["fridge_eco_c"], -22, 10, "eco temperature")
            location.save_places(p)
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": str(e) if str(e) else "that is not a place"}
    return 200, location.public_places()


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "run":
        run()
        return 0
    if what == "pickup" and len(argv) >= 2:
        code, body = do("pickup", {"name": argv[1], "eta_min": argv[2] if len(argv) > 2 else None})
        print(json.dumps(body))
        return 0 if code == 200 else 2
    if what == "cancel":
        do("pickup_cancel", {})
        return 0
    print("  omacar arrival: status | run | pickup <name> [eta_min] | cancel", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
