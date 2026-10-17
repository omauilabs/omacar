#!/usr/bin/env python3
"""The pre-trip check's stored half (lib/pretrip.py) and the maintenance gaps
(lib/book.py service_recorded). The verdict itself is JavaScript and is tested
in test/js/pretrip.test.js. Everything runs against a scratch state dir."""

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
scratch = tempfile.mkdtemp(prefix="omacar-pretrip-")
for k in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
    os.environ[k] = os.path.join(scratch, k.lower())
sys.path.insert(0, os.path.join(ROOT, "lib"))

import api  # noqa: E402
import book  # noqa: E402
import pretrip  # noqa: E402
import records  # noqa: E402

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


def raises(fn, *a):
    try:
        fn(*a)
    except (ValueError, TypeError):
        return True
    return False


def main():
    try:
        print("\n  the file")
        check("starts blank", pretrip.load() == pretrip.blank())
        pretrip.set_trip_km(402.3)
        pretrip.set_tick("oil", True)
        pretrip.set_tick("wipers", True)
        st = pretrip.load()
        check("keeps the trip distance", st["trip_km"] == 402.3)
        check("keeps the ticks", st["ticks"] == {"oil": True, "wipers": True})
        pretrip.set_tick("oil", False)
        check("an untick removes it", pretrip.load()["ticks"] == {"wipers": True})
        check("an unknown check id is refused", raises(pretrip.set_tick, "nope", True))
        check("a negative trip is refused", raises(pretrip.set_trip_km, -1))
        check("an absurd trip is refused, not clamped", raises(pretrip.set_trip_km, 1e9))
        check("a string trip is refused", raises(pretrip.set_trip_km, "far"))
        check("a bool trip is refused", raises(pretrip.set_trip_km, True))
        check("refusals changed nothing", pretrip.load()["trip_km"] == 402.3)
        pretrip.set_trip_km(None)
        check("None clears the distance", pretrip.load()["trip_km"] is None)
        pretrip.new_trip()
        st = pretrip.load()
        check("a new trip clears the ticks", st["ticks"] == {} and st["trip_km"] is None)
        check("and notes when it began", isinstance(st["trip_started"], float))
        with open(pretrip.STATE, "w") as f:
            f.write("{not json")
        check("a corrupt file reads as blank", pretrip.load() == pretrip.blank())
        with open(pretrip.STATE, "w") as f:
            f.write('{"trip_km": "x", "ticks": {"oil": "yes", "bogus": true, "spare": true}}')
        st = pretrip.load()
        check("hand-edited junk is dropped, not believed",
              st["trip_km"] is None and st["ticks"] == {"spare": True})

        print("\n  maintenance gaps")
        check("no database: no gaps, no crash", pretrip.gaps() == [])
        check("no database: read_public works",
              pretrip.read_public()["gaps"] == [] and pretrip.read_public()["tpms"] is None)
        db = book.open_db()
        book.ensure_schedule(db)
        n = db.execute("SELECT count(*) FROM service").fetchone()[0]
        db.close()
        g = pretrip.gaps()
        check(f"a fresh schedule has every item as a gap ({len(g)} of {n})", n > 5 and len(g) == n)
        done = book.log_service("oil")
        check("logging an item records it", done == "Engine oil & filter")
        names = [x["item"] for x in pretrip.gaps()]
        check("and it leaves the gaps", "Engine oil & filter" not in names and len(names) == n - 1)
        when = time.time() - 90 * 86400
        book.log_service("coolant", km=0.0, when=when)
        names = [x["item"] for x in pretrip.gaps()]
        check("a dated record leaves the gaps too", "Coolant" not in names)
        db = records.connect()
        row = records.rows(db, "SELECT last_km, last_at FROM service WHERE item='Coolant'")[0]
        db.close()
        check("an unknown-odometer date keeps the time and drops the distance",
              row["last_km"] == 0.0 and abs(row["last_at"] - when) < 2)
        svc = None
        db = records.connect()
        svc = records.service(db, 150000.0)
        db.close()
        cool = next(s for s in svc["items"] if s["item"] == "Coolant")
        check("the schedule then counts that item by time only",
              cool["km_left"] is None and cool["days_left"] is not None and cool["days_left"] < 1800)
        book.add_item("Timing belt", interval_km=100000, interval_days=0)
        check("an added item is a gap until it is done",
              "Timing belt" in [x["item"] for x in pretrip.gaps()])
        book.log_service("timing")
        check("and not after", "Timing belt" not in [x["item"] for x in pretrip.gaps()])
        book.add_item("Timing belt", interval_km=100000, interval_days=0)
        check("re-adding it makes it a gap again", "Timing belt" in [x["item"] for x in pretrip.gaps()])
        check("an item that does not exist cannot be recorded", book.log_service("flux capacitor") is None)

        print("\n  the API")
        st, out = api.handle_get("/api/pretrip", {})
        check("GET answers the stored half and the gaps",
              st == 200 and set(out) >= {"trip_km", "ticks", "gaps", "tpms", "checklist", "simulated"})
        st, out = api.handle_post("/api/pretrip", json.dumps({"trip_km": 201.2}))
        check("POST a trip distance", st == 200 and out["trip_km"] == 201.2)
        st, out = api.handle_post("/api/pretrip", json.dumps({"tick": "lights", "value": True}))
        check("POST a tick", st == 200 and out["ticks"].get("lights") is True)
        st, out = api.handle_post("/api/pretrip", json.dumps({"tick": "lights", "value": False}))
        check("POST an untick", st == 200 and "lights" not in out["ticks"])
        st, out = api.handle_post("/api/pretrip", json.dumps({"tick": "bogus", "value": True}))
        check("POST a bad check id is a 400", st == 400 and "error" in out)
        st, out = api.handle_post("/api/pretrip", json.dumps({"trip_km": -5}))
        check("POST a bad distance is a 400", st == 400)
        st, out = api.handle_post("/api/pretrip", json.dumps({}))
        check("POST nothing is a 400", st == 400)
        st, out = api.handle_post("/api/pretrip", json.dumps({"new_trip": True}))
        check("POST new_trip clears", st == 200 and out["trip_km"] is None and out["ticks"] == {})
        st, out = api.handle_post("/api/service", json.dumps({"action": "record", "item": "Spark plugs", "days_ago": 400}))
        check("service record with a date", st == 200 and out["logged"] == "Spark plugs")
        check("and it leaves the gaps",
              "Spark plugs" not in [x["item"] for x in api.handle_get("/api/pretrip", {})[1]["gaps"]])
        st, out = api.handle_post("/api/service", json.dumps({"action": "record", "item": "Spark plugs", "days_ago": -1}))
        check("a negative days_ago is a 400", st == 400)
        st, out = api.handle_post("/api/service", json.dumps({"action": "record", "item": "Spark plugs", "days_ago": "soon"}))
        check("a non-number days_ago is a 400", st == 400)
        st, out = api.handle_post("/api/service", json.dumps({"action": "record", "item": "nonesuch", "days_ago": 5}))
        check("an unknown item is a 404", st == 404)

        print("\n  tyres")
        check("with no lib/tpms.py the source is None, not a made-up pressure",
              pretrip.tpms() is None or isinstance(pretrip.tpms(), dict))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
