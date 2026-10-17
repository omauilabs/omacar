#!/usr/bin/env python3
"""Ready for a long drive? The parts of the pre-trip check that need a disk.

Everything the check judges from the car (trouble codes, readiness monitors,
the service schedule, the 12 V battery, the IMA pack, fuel) is already in
/api/snapshot and /api/battery; the verdict is computed in the browser by
share/js/pretrip.js, a pure function with its own tests. What the browser
cannot hold is kept here, in pretrip.json under $XDG_STATE_HOME/omacar:

  * the trip distance the owner stepped to (kilometres), and
  * the manual checklist (fluids, wipers, lights ...) the owner ticked,
    PER TRIP: starting a new trip clears the ticks, because "washer fluid
    topped up" is only true for the drive you were standing next to.

And the MAINTENANCE GAPS, which are a fact about the service book: an item the
schedule knows but nobody has ever said was done. lib/book.py starts a new
schedule with every item "last done" at today minus its interval, which is a
placeholder so the countdown has somewhere to start. It must not be believed,
so lib/book.py log_service() writes a row to service_recorded and this module
lists every schedule item without one. (A book that existed before this table
shows every item once; one tap on "Done today" or on the date chips fixes it.)

    omacar pretrip status     the file, and the gaps
    omacar pretrip gaps       the items that have never been recorded

Nothing here writes to the car.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
STATE = os.path.join(STATE_DIR, "pretrip.json")

# The manual checklist. Ids are stable (they are stored); labels are the screen's.
CHECKLIST = [
    ("oil", "Engine oil level"),
    ("coolant", "Coolant level"),
    ("washer", "Washer fluid"),
    ("wipers", "Wipers"),
    ("lights", "Lights and indicators"),
    ("tyres_look", "Tyre condition"),
    ("spare", "Spare, jack or sealant"),
]
CHECK_IDS = [c[0] for c in CHECKLIST]

MAX_TRIP_KM = 8000.0        # a sanity cap on the stepper, not a feature


def blank():
    return {"trip_km": None, "ticks": {}, "trip_started": None, "updated": None}


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return blank()
    if not isinstance(d, dict):
        return blank()
    out = blank()
    km = d.get("trip_km")
    if isinstance(km, (int, float)) and not isinstance(km, bool) and 0 <= km <= MAX_TRIP_KM:
        out["trip_km"] = float(km)
    ticks = d.get("ticks")
    if isinstance(ticks, dict):
        out["ticks"] = {k: True for k in CHECK_IDS if ticks.get(k) is True}
    out["trip_started"] = d.get("trip_started")
    out["updated"] = d.get("updated")
    return out


def save(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["updated"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)
    return state


def set_trip_km(km):
    """None clears it. Anything out of range is refused, not clamped."""
    if km is not None:
        if isinstance(km, bool) or not isinstance(km, (int, float)):
            raise ValueError("trip_km must be a number")
        if not 0 <= km <= MAX_TRIP_KM:
            raise ValueError("trip_km out of range")
        km = float(km)
    st = load()
    st["trip_km"] = km
    return save(st)


def set_tick(name, value):
    if name not in CHECK_IDS:
        raise ValueError("no such check")
    st = load()
    if value:
        st["ticks"][name] = True
    else:
        st["ticks"].pop(name, None)
    return save(st)


def new_trip():
    """Start again: the ticks and the distance go, because they were about the
    last trip."""
    st = blank()
    st["trip_started"] = time.time()
    return save(st)


# ---- maintenance gaps ----------------------------------------------------------

def recorded_names(db):
    if db is None or not records.has(db, "service_recorded"):
        return set()
    return {r["item"].lower() for r in records.rows(
        db, "SELECT item FROM service_recorded", table="service_recorded")}


def gaps(db=None):
    """Schedule items nobody has ever said were done: [{item, interval_km,
    interval_days}]. A schedule with no items has no gaps (and no schedule)."""
    own = db is None
    db = db or records.connect()
    try:
        items = records.rows(db, "SELECT item, interval_km, interval_days FROM service",
                             table="service")
        seen = recorded_names(db)
        return [{"item": s["item"], "interval_km": s.get("interval_km"),
                 "interval_days": s.get("interval_days")}
                for s in items if s["item"].lower() not in seen]
    finally:
        if own and db is not None:
            db.close()


# ---- the tyre source, if another feature has landed one ----------------------------

def tpms():
    """lib/tpms.py's public reading when that module exists, else None. The
    check says "Not measured" for None; it never invents a pressure."""
    try:
        import tpms as mod  # noqa: PLC0415 -- optional, may not exist
    except ImportError:
        return None
    fn = getattr(mod, "read_public", None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001 -- a broken tyre source must not break the check
        return None


def read_public():
    st = load()
    db = records.connect()
    try:
        g = gaps(db)
        simulated = bool((records.vehicle(db) or {}).get("simulated")) if db else False
    finally:
        if db is not None:
            db.close()
    return {"trip_km": st["trip_km"], "ticks": st["ticks"],
            "trip_started": st["trip_started"],
            "checklist": [{"id": i, "label": l} for i, l in CHECKLIST],
            "gaps": g, "tpms": tpms(), "simulated": simulated}


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if cmd == "gaps":
        g = gaps()
        if not g:
            print("Every item on the schedule has been recorded at least once.")
        for s in g:
            print(f"  never recorded: {s['item']}")
        return 0
    print("usage: omacar pretrip status|gaps", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
