"""The vehicle book: the odometer, and the record of what has been done to it.

Neither of these can be read off the bus.

**The odometer is not an OBD-II PID.** There is a service 01 PID 0xA6 in the
later standards and almost nothing implements it; python-obd does not even
carry a command for it. So the mileage on the dashboard is unavailable to any
generic scan tool, including the expensive ones, and anything that shows you
one either asked a manufacturer protocol or did what this does: took a reading
from you once and integrated distance from the wheels ever since.

That is honest and it is also accurate — speed integrated at one hertz tracks a
trip meter to well inside a percent — but it drifts if the daemon is not
running while you drive. So it is easy to correct, and it says when it was
last set.

**The service record is not on the car either.** Maintenance Minder lives in
the instrument cluster behind a manufacturer protocol. What is on the car is
the intervals, which are in the owner's handbook, and what has been done, which
is in your head or a folder in the glovebox. This puts both somewhere the tool
can count down from.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

# A schedule that is right for most cars and wrong for none in a way that
# matters — the point is to have something counting down on day one rather
# than an empty page. Every figure is editable and the handbook wins.
STARTER = [
    ("Engine oil & filter", "A", 10000, 365, "check the handbook for your grade"),
    ("Tire rotation", "1", 10000, 365, "front to rear"),
    ("Air filter", "2", 30000, 730, ""),
    ("Cabin filter", "", 20000, 365, ""),
    ("Brake fluid", "7", 0, 1095, "hygroscopic — time, not mileage"),
    ("Coolant", "5", 100000, 1825, ""),
    ("Transmission fluid", "3", 60000, 1825, ""),
    ("Spark plugs", "4", 100000, 3650, ""),
    ("Brake pads", "", 40000, 1460, "inspect; replace on thickness"),
    ("Tires", "", 60000, 2190, "and on age — check the DOT date"),
    ("12 V battery", "", 0, 1825, ""),
]

# Miles in, kilometres stored. The record is metric because everything else is.
MI_PER_KM = 1 / 1.609344


def open_db():
    os.makedirs(records.STATE, exist_ok=True)
    db = sqlite3.connect(records.DB, timeout=10.0)
    # records.* reads rows by name, so a writable handle needs the same row
    # factory its read-only one uses or every helper here fails on the first
    # column lookup.
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS vehicle (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS service (
        item TEXT PRIMARY KEY, code TEXT, last_km REAL, last_at REAL,
        interval_km REAL, interval_days REAL, note TEXT)""")
    return db


def put(db, key, value):
    db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
               (key, json.dumps(value)))


# ---- the odometer -----------------------------------------------------------

def driven_since(db, since):
    """Kilometres covered since an instant. The implementation lives in
    records.py because the snapshot needs it too, and two copies of a distance
    integration is two chances to disagree about how far you have driven."""
    return records.driven_since(db, since)


def odometer(db=None):
    """The reading now: what you last told us, plus what has been driven since.

    Returns (km, when_set, driven) so a caller can say how it was arrived at
    rather than presenting a derived number as if it came off the bus.
    """
    own = db is None
    db = db or records.connect()
    if db is None:
        return None, None, 0.0
    try:
        v = records.vehicle(db)
        base = v.get("odometer_km")
        at = v.get("odometer_at")
        if base is None:
            return None, None, 0.0
        if at is None:
            # A seeded record with no timestamp — the simulator's — is a
            # figure that is already current.
            return float(base), None, 0.0
        driven = driven_since(db, at)
        return round(float(base) + driven, 1), at, driven
    finally:
        if own:
            db.close()


def set_odometer(km, when=None):
    db = open_db()
    try:
        put(db, "odometer_km", round(float(km), 1))
        put(db, "odometer_at", when or time.time())
        db.commit()
    finally:
        db.close()
    return odometer()


# ---- the service record -----------------------------------------------------

def ensure_schedule(db, verbose=False):
    """Give a car with no record something to count down from."""
    have = db.execute("SELECT count(*) FROM service").fetchone()[0]
    if have:
        return 0
    now = time.time()
    odo = (odometer(db)[0] or 0.0)
    for item, code, km, days, note in STARTER:
        # Everything starts due now rather than pretending it was just done:
        # a schedule that opens with eleven green ticks on a car nobody has
        # told us anything about is a schedule that is lying.
        db.execute("INSERT OR REPLACE INTO service VALUES (?,?,?,?,?,?,?)",
                   (item, code, odo, now - (days or 365) * 86400,
                    km / MI_PER_KM if km else 0.0, days, note))
    db.commit()
    if verbose:
        print(f"  started a schedule with {len(STARTER)} items, all showing as "
              f"due until you log what has been done")
    return len(STARTER)


def log_service(item, km=None, when=None, note=None):
    """Record that something was done. This is the whole point of the book."""
    db = open_db()
    try:
        ensure_schedule(db)
        row = db.execute(
            "SELECT item, interval_km, interval_days, note FROM service "
            "WHERE lower(item) = lower(?)", (item,)).fetchone()
        if row is None:
            # Match on a prefix so "oil" finds "Engine oil & filter".
            row = db.execute(
                "SELECT item, interval_km, interval_days, note FROM service "
                "WHERE lower(item) LIKE ?", (f"%{item.lower()}%",)).fetchone()
        if row is None:
            return None
        odo = km if km is not None else (odometer(db)[0] or 0.0)
        db.execute("UPDATE service SET last_km = ?, last_at = ?, note = ? "
                   "WHERE item = ?",
                   (odo, when or time.time(), note if note is not None else row[3],
                    row[0]))
        db.commit()
        return row[0]
    finally:
        db.close()


def add_item(item, code="", interval_km=0.0, interval_days=0, note=""):
    db = open_db()
    try:
        db.execute("INSERT OR REPLACE INTO service VALUES (?,?,?,?,?,?,?)",
                   (item, code, odometer(db)[0] or 0.0, time.time(),
                    interval_km, interval_days, note))
        db.commit()
    finally:
        db.close()
    return item


def forget_item(item):
    db = open_db()
    try:
        cur = db.execute("DELETE FROM service WHERE lower(item) = lower(?)", (item,))
        db.commit()
        return cur.rowcount
    finally:
        db.close()


# ---- terminal ---------------------------------------------------------------

def show():
    db = records.connect()
    u = records.units_for()
    km, at, driven = odometer(db)
    print()
    if km is None:
        print("  No odometer reading yet.")
        print("  Set it once from the dashboard and it keeps itself up to date:")
        print("      omacar odometer set 85700")
    else:
        print(f"  Odometer   {records.to_dist(km, u):,.0f} {u['dist']}")
        if at:
            print(f"             {records.to_dist(km - driven, u):,.0f} {u['dist']} "
                  f"set {datetime.fromtimestamp(at).strftime('%d %b %Y')}, "
                  f"{records.to_dist(driven, u):,.0f} {u['dist']} driven since")
            print(f"             OBD-II has no odometer PID, so this is integrated "
                  f"from road speed. Correct it any time.")
    svc = records.service(db, km) if db else None
    if svc:
        print()
        for s in svc["items"]:
            state = {"overdue": "OVERDUE", "now": "due now", "soon": "due soon",
                     "ok": ""}[s["state"]]
            bits = []
            if s.get("km_left") is not None:
                bits.append(f"{records.to_dist(abs(s['km_left']), u):,.0f} {u['dist']}"
                            + (" over" if s["km_left"] < 0 else " left"))
            if s.get("due_on"):
                bits.append(s["due_on"])
            print(f"  {max(0, s['life']):>3}%  {s['item']:<24}"
                  f"{'  '.join(bits):<28}{state}")
    if db:
        db.close()
    print()
    return 0


def main(argv):
    what = argv[0] if argv else "show"
    rest = argv[1:]
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "set" and rest:
        u = records.units_for()
        try:
            given = float(rest[0].replace(",", ""))
        except ValueError:
            print("omacar odometer set <reading>", file=sys.stderr)
            return 1
        # Whatever unit the display is in is the unit you are reading off the
        # dashboard, so that is the unit it is taken in.
        km = given / u["km"] if u["km"] else given
        set_odometer(km)
        print(f"  odometer set to {given:,.0f} {u['dist']}")
        return show()
    if what == "log" and rest:
        item = " ".join(rest)
        done = log_service(item)
        if not done:
            print(f"omacar service log: no item matching {item!r}", file=sys.stderr)
            return 1
        print(f"  logged: {done}, today, at the current odometer")
        return show()
    if what == "add" and rest:
        km = days = 0
        name = []
        i = 0
        while i < len(rest):
            if rest[i] == "--miles" and i + 1 < len(rest):
                km = float(rest[i + 1]) / MI_PER_KM
                i += 2
            elif rest[i] == "--days" and i + 1 < len(rest):
                days = int(rest[i + 1])
                i += 2
            else:
                name.append(rest[i])
                i += 1
        if not name:
            print("omacar service add <name> [--miles N] [--days N]", file=sys.stderr)
            return 1
        add_item(" ".join(name), interval_km=km, interval_days=days)
        return show()
    if what == "forget" and rest:
        n = forget_item(" ".join(rest))
        print(f"  removed {n} item(s)")
        return show()
    if what == "start":
        db = open_db()
        try:
            ensure_schedule(db, verbose=True)
        finally:
            db.close()
        return show()
    return show()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
