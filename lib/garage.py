"""More than one car.

A scan tool that only knows about the vehicle it saw last is a scan tool for
somebody with one vehicle. Plug into a different car and its codes, its
service book and its year of driving must not land on top of the last one's —
that is not a merge, it is a corruption, and the visible symptom is a driver
being shown trouble codes their car has never set.

So each vehicle gets its own database, keyed by VIN, and a pointer says which
one is current. Plugging in switches automatically: the VIN comes back in the
first survey, before anything is written, and the tool is looking at that car
from then on.

    ~/.local/state/omacar/vehicles/<key>.db     one car
    ~/.local/state/omacar/current-vehicle       which one is in front of us

`telemetry.db` from before any of this existed is adopted on first run rather
than orphaned.
"""

import json
import os
import re
import sqlite3
import sys
import time

STATE = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
GARAGE = os.path.join(STATE, "vehicles")
POINTER = os.path.join(STATE, "current-vehicle")
LEGACY = os.path.join(STATE, "telemetry.db")

# The simulated car lives alongside the real ones rather than fighting one of
# them for a file. It is a vehicle like any other; it just is not real.
SIM_KEY = "simulated"

# Filenames, and VIN keys, are alphanumeric. A VIN is alphanumeric by
# definition, so anything else in one is punctuation somebody typed — and
# keeping it would make two spellings of the same VIN into two cars.
SAFE = re.compile(r"[^A-Za-z0-9]")
PATH_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def key_for(vin):
    """A filename from a VIN. Anything unusable falls back to `unknown`."""
    k = SAFE.sub("", (vin or "").strip().upper())
    return k[:20] if len(k) >= 6 else "unknown"


def path_for(key):
    return os.path.join(GARAGE, f"{PATH_SAFE.sub('', key) or 'unknown'}.db")


def current():
    """The active vehicle key, adopting a pre-garage database if there is one."""
    os.makedirs(GARAGE, exist_ok=True)
    try:
        with open(POINTER) as f:
            key = f.read().strip()
        if key and os.path.exists(path_for(key)):
            return key
        if key:
            return key            # not created yet; the caller will make it
    except OSError:
        pass
    adopted = adopt_legacy()
    if adopted:
        return adopted
    return SIM_KEY


def set_current(key):
    os.makedirs(STATE, exist_ok=True)
    tmp = POINTER + ".tmp"
    with open(tmp, "w") as f:
        f.write(key)
    os.replace(tmp, POINTER)
    return key


def db_path():
    return path_for(current())


def adopt_legacy():
    """Move a pre-garage `telemetry.db` into the garage under its own VIN.

    Done by rename, so it is the same file and nothing is copied or lost. If
    the database says it is simulated it becomes the simulated car; otherwise
    its VIN names it.
    """
    if not os.path.exists(LEGACY):
        return None
    key = SIM_KEY
    try:
        db = sqlite3.connect(f"file:{LEGACY}?mode=ro", uri=True)
        row = db.execute(
            "SELECT k, v FROM vehicle WHERE k IN ('vin','simulated')").fetchall()
        db.close()
        found = {k: v for k, v in row}
        simulated = json.loads(found.get("simulated", "false") or "false")
        if not simulated and found.get("vin"):
            key = key_for(json.loads(found["vin"]))
    except (sqlite3.Error, ValueError, TypeError):
        pass
    os.makedirs(GARAGE, exist_ok=True)
    dest = path_for(key)
    if os.path.exists(dest):
        # Already adopted once, and something recreated the old name. Leave
        # both alone rather than choosing which to destroy.
        return None
    try:
        os.replace(LEGACY, dest)
    except OSError:
        return None
    for suffix in ("-wal", "-shm"):
        try:
            os.replace(LEGACY + suffix, dest + suffix)
        except OSError:
            pass
    set_current(key)
    return key


def describe(key):
    """What we know about one car in the garage, without opening it twice."""
    path = path_for(key)
    out = {"key": key, "path": path, "current": key == current(),
           "size": 0, "vin": None, "name": None, "simulated": False,
           "last_seen": None, "codes": 0,
           "driver": None, "plate": None, "notes": None}
    try:
        out["size"] = os.path.getsize(path)
    except OSError:
        return out
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        rows = db.execute("SELECT k, v FROM vehicle").fetchall()
        v = {}
        for k, raw in rows:
            try:
                v[k] = json.loads(raw)
            except (ValueError, TypeError):
                v[k] = raw
        out["vin"] = v.get("vin")
        out["simulated"] = bool(v.get("simulated"))
        out["name"] = v.get("name") or " ".join(
            str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x)
        for f in ("driver", "plate", "notes", "model_source"):
            out[f] = v.get(f)
        out["last_seen"] = v.get("surveyed_at") or v.get("seeded_at")
        out["codes"] = db.execute(
            "SELECT count(*) FROM faults WHERE status IN "
            "('stored','pending','permanent')").fetchone()[0]
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return out


def vehicles():
    os.makedirs(GARAGE, exist_ok=True)
    keys = sorted(f[:-3] for f in os.listdir(GARAGE) if f.endswith(".db"))
    return [describe(k) for k in keys]


def switch_to(vin, simulated=False):
    """Point at the car with this VIN, making room for it if it is new.

    Returns (key, is_new). Never touches an open database: the caller does
    this before anything opens one, because renaming or swapping a file under
    a live SQLite handle fails silently and then refuses the next write.
    """
    key = SIM_KEY if simulated else key_for(vin)
    was = current()
    if key == was:
        return key, False
    path = path_for(key)
    new = not os.path.exists(path)
    os.makedirs(GARAGE, exist_ok=True)
    if new:
        # Create the record here rather than waiting for the first write.
        # Otherwise "have we met this car" depends on whether anything has
        # opened it yet, and coming back to a car you switched away from
        # before it was written to reports it as new all over again.
        sqlite3.connect(path).close()
    set_current(key)
    return key, new


# Fields a person can set on a car. A whitelist rather than free-form keys,
# because these share the `vehicle` table with values the survey writes -- VIN,
# odometer, protocol -- and an unrestricted setter would let the UI overwrite
# measurements with typing.
META_FIELDS = {
    "name":   "what to call it, since OBD-II cannot tell us the model",
    "driver": "who normally drives it",
    "plate":  "registration, for telling two identical cars apart",
    "notes":  "anything else worth remembering about this car",
}


def set_meta(key, field, value):
    """Set one human-supplied field on a car.

    `driver` is why this exists. A household garage is not a list of cars, it
    is a list of people's cars: the codes that matter are the ones on the car a
    teenager drives, and "2014 Civic" does not tell you that at a glance while
    "Emma — 2014 Civic" does.
    """
    if field not in META_FIELDS:
        return False
    path = path_for(key)
    if not os.path.exists(path):
        return False
    db = sqlite3.connect(path, timeout=5.0)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS vehicle (k TEXT PRIMARY KEY, v TEXT)")
        text = str(value).strip()
        if text:
            db.execute("INSERT OR REPLACE INTO vehicle VALUES (?, ?)",
                       (field, json.dumps(text)))
        else:
            # Clearing a field removes it, so `describe` reports None rather
            # than an empty string that renders as a blank line in the UI.
            db.execute("DELETE FROM vehicle WHERE k = ?", (field,))
        db.commit()
    finally:
        db.close()
    return True


def name_vehicle(key, name):
    """Give a car a name. Kept as its own entry point: the CLI and the HTTP API
    both call it by this name, and renaming a car is common enough to deserve
    one."""
    return set_meta(key, "name", name)


def forget(key):
    path = path_for(key)
    if not os.path.exists(path):
        return False
    aside = path + ".removed"
    try:
        os.replace(path, aside)
    except OSError:
        return False
    if current() == key:
        remaining = [v["key"] for v in vehicles()]
        set_current(remaining[0] if remaining else SIM_KEY)
    return True


# ---- terminal ---------------------------------------------------------------

def main(argv):
    what = argv[0] if argv else "list"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "use" and len(argv) > 1:
        key, new = switch_to(argv[1])
        print(f"  now looking at {key}" + ("  (new)" if new else ""))
        return main(["list"])
    if what == "name" and len(argv) > 2:
        ok = name_vehicle(argv[1], " ".join(argv[2:]))
        print("  named" if ok else "  no such vehicle")
        return main(["list"])
    if what == "forget" and len(argv) > 1:
        print("  removed" if forget(argv[1]) else "  no such vehicle")
        return main(["list"])

    cars = vehicles()
    print()
    if not cars:
        print("  No vehicles yet. Plug in an adapter, or: omacar sim seed")
        print()
        return 0
    for c in cars:
        mark = "→" if c["current"] else " "
        label = c["name"] or c["vin"] or c["key"]
        bits = []
        if c["vin"]:
            bits.append(c["vin"])
        if c["simulated"]:
            bits.append("simulated")
        if c["codes"]:
            bits.append(f"{c['codes']} code(s)")
        bits.append(f"{c['size'] / 1e6:.1f} MB")
        if c["last_seen"]:
            bits.append(time.strftime("%d %b %Y", time.localtime(c["last_seen"])))
        print(f"  {mark} {label:<26}{'  ·  '.join(bits)}")
    print()
    print("  omacar vehicle use <vin>     look at another car")
    print("  omacar vehicle name <key> …  give it a name OBD-II cannot")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
