#!/usr/bin/env python3
"""The trip journal (lib/journal.py): the assembler with every source present,
some absent, and a drive with missing data; the rules that write the one-liner;
and the pieces that keep a real journal honest (simulators, the places switch).
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


T0 = 1_760_000_000.0
TRIP = {"t0": T0, "t1": T0 + 38 * 60, "km": 38.6, "litres": 1.8, "lphk": 4.6,
        "moving_s": 2100, "idle_s": 180, "top_kph": 112, "kind": "drive", "label": "07:42"}


def air_rows(peak_run=True):
    rows = []
    for i in range(39):
        t = T0 + i * 60
        co2 = 1700 if (peak_run and 10 <= i <= 20) else 700
        rows.append({"t": t, "v": {"cabin.co2": co2}})
    return rows


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-journal-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import journal
    try:
        print("\n  every source present")
        full = {
            "env": {"rows": air_rows()},
            "v1": {"log": [{"at": T0 + 100, "band": "K"}, {"at": T0 + 400, "band": "K"},
                           {"at": T0 + 90000, "band": "Ka"}], "covered": True},
            "fridge": {"events": [{"t": T0 + 500, "kind": "door"}], "covered": True},
            "coach": {"drives": [{"t0": T0 + 2, "score": 82.4, "partial": False}]},
            "clips": {"items": [{"t": T0 + 900, "id": "c1", "label": "Hard brake"}]},
            "fuel": {"items": [{"t": T0 + 2400, "litres": 30.0}]},
        }
        e = journal.assemble(TRIP, full)
        r = journal.render(e, True)
        check("the base: 38 min, 24 mi, 51 mpg", r["line"].startswith("38 min, 24 mi, 51 mpg"))
        check("stale air once (one run over 1500 ppm for 11 minutes)", "stale air once" in r["line"])
        check("two K-band alerts; the Ka alert a day later is not this drive", "2 K-band alerts" in r["line"]
              and "Ka-band" not in r["line"])
        check("a fridge door alert", "fridge door alert" in r["line"])
        check("coach, clip and fill-up appear when their modules exist",
              "coach 82" in r["line"] and "1 clip" in r["line"] and "fill-up 7.9 gal" in r["line"])
        check("the line is the rules' order: clips, radar, air, fridge, coach, fuel",
              [f["kind"] for f in e["findings"]] == ["clips", "radar", "air", "fridge", "coach", "fuel"])
        check("every source says found", set(e["sources"].values()) == {"found"})
        check("metric units on request", journal.render(e, False)["line"].startswith("38 min, 39 km, 4.6 L/100 km"))
        check("no location in an entry", "places" not in e and not any(k in e for k in ("lat", "lon", "lng", "latitude")))
        check("badges carry a tone", all(b["tone"] for b in r["badges"]))

        print("\n  some sources absent")
        part = journal.assemble(TRIP, {"env": {"rows": air_rows()}, "v1": None})
        check("absent sources are 'absent', not 'none'",
              part["sources"]["v1"] == "absent" and part["sources"]["coach"] == "absent")
        check("only what exists is said", journal.render(part)["line"] == "38 min, 24 mi, 51 mpg · stale air once")
        check("checked list omits absent sources", [c["source"] for c in journal.render(part)["checked"]] == ["env"])
        none = journal.assemble(TRIP, {})
        check("no sources at all: just the drive", journal.render(none)["line"] == "38 min, 24 mi, 51 mpg"
              and journal.render(none)["badges"] == [])

        print("\n  a source here but silent is not the same as a quiet drive")
        quiet = journal.assemble(TRIP, {"env": {"rows": air_rows(False)},
                                        "v1": {"log": [], "covered": True},
                                        "fridge": {"events": [], "covered": False}})
        check("listening, nothing found: none", quiet["sources"]["env"] == "none" and quiet["sources"]["v1"] == "none")
        check("not covering this drive: gap", quiet["sources"]["fridge"] == "gap")
        words = {c["source"]: c["text"] for c in journal.render(quiet)["checked"]}
        check("gap is worded 'not recorded', never 'nothing'", words["fridge"] == "not recorded for this drive"
              and words["env"] == "nothing to report")
        old = journal.assemble(TRIP, {"env": {"rows": [{"t": T0 - 90000, "v": {"cabin.co2": 2500}}]}})
        check("air rows from another day are a gap", old["sources"]["env"] == "gap" and not old["findings"])
        brief = journal.assemble(TRIP, {"env": {"rows": [{"t": T0 + 600 + 60 * i, "v": {"cabin.co2": 1900}}
                                                         for i in range(3)]}})
        check("three minutes over the line is not stale air", brief["findings"][0]["stale"] == 0
              and "stale" not in journal.render(brief)["line"])
        dip = [{"t": T0 + 60 * i, "v": {"cabin.co2": c}} for i, c in
               enumerate([1700] * 4 + [1450] * 2 + [1700] * 4 + [800] * 3)]
        check("a dip to 1450 does not restart the run (hysteresis)",
              journal.air_finding(dip, T0, T0 + 900)[1]["stale"] == 1)
        check("laser is worded as laser, and tone is bad",
              journal.phrase(journal.radar_finding([{"at": T0 + 5, "band": "Laser"}], T0, T0 + 99, True)[1]) == "1 laser alert"
              and journal.radar_finding([{"at": T0 + 5, "band": "Laser"}], T0, T0 + 99, True)[1]["tone"] == "bad")
        full_log = journal.radar_finding([{"at": T0 + 99999, "band": "K"}], T0, T0 + 99, False)
        check("a radar log that has rolled past the drive is a gap", full_log[0] == "gap")

        print("\n  a drive with missing data")
        thin = journal.assemble({"t0": T0, "t1": None, "km": None, "lphk": None, "moving_s": 600, "idle_s": 60},
                                {"env": {"rows": air_rows()}})
        check("still open: flagged, duration from moving+idle", thin["open"] and thin["duration_s"] == 660)
        check("unknown distance and economy are left out, not zero",
              journal.render(thin)["line"].split(" · ")[0] == "11 min")
        bare = journal.assemble({"t0": None})
        check("a row with nothing in it does not raise", bare["id"] is None and bare["findings"] == []
              and journal.render(bare)["line"] == "Drive recorded")
        zero = journal.assemble(dict(TRIP, lphk=0, km=0.0))
        check("zero economy is unknown", zero["lphk"] is None and "mpg" not in journal.render(zero)["line"])
        check("avg speed is over moving time", abs(e["avg_kph"] - 38.6 / (2100 / 3600)) < 0.1)

        print("\n  the words")
        check("once, twice, n times", [journal.times(n) for n in (1, 2, 3)] == ["once", "twice", "3 times"])
        check("an hour and a bit", journal.fmt_duration(4320) == "1 h 12 min")
        check("short distances keep a decimal", journal.fmt_dist(4.0, True) == "2.5 mi")

        print("\n  the fridge observer")
        st = {"zones": [{"temp": 3.0, "set": 3.0, "door": False}], "alerts": []}
        ev, prev = journal.observe_fridge(st, None, 100)
        check("a closed lid is no event", ev == [])
        st["zones"][0]["door"] = True
        ev, prev = journal.observe_fridge(st, prev, 110)
        check("lid opening is one event", [x["kind"] for x in ev] == ["door"])
        ev, prev = journal.observe_fridge(st, prev, 120)
        check("lid staying open is not another", ev == [])
        st["zones"][0].update(door=False, temp=9.0)
        ev, prev = journal.observe_fridge(st, prev, 130)
        ev, prev = journal.observe_fridge(st, prev, 130 + journal.FRIDGE_WARM_S + 1)
        check("ten minutes warm is one warm event", [x["kind"] for x in ev] == ["warm"])
        ev, prev = journal.observe_fridge(st, prev, 130 + journal.FRIDGE_WARM_S + 60)
        check("and only one", ev == [])

        print("\n  honesty and switches")
        check("the places switch is off by default", journal.settings()["places"] is False)
        # Both cases, explicitly: lib/location.py may or may not be in this
        # tree (it arrived with the arrivals feature).
        real_avail = journal.location_available
        journal.location_available = lambda: False
        try:
            journal.set_places(True)
            check("turning places on without a location source is refused", False)
        except ValueError:
            check("turning places on without a location source is refused", True)
        finally:
            journal.location_available = real_avail
        if real_avail():
            journal.set_places(True)
            check("with a location module here, places can be turned on", journal.settings()["places"] is True)
            journal.set_places(False)
        check("nothing asks for place names while it is off", journal.places_for(e) is None)
        check("a note is trimmed and keyed to the drive",
              journal.set_note(T0, "  Dropped   the kids\noff ")["note"] == "Dropped the kids off")
        check("an empty note removes it", journal.set_note(T0, "")["note"] is None)
        check("notes are capped", len(journal.clean_note("x" * 999)) == journal.NOTE_MAX)

        print("\n  the simulated week")
        n = journal.sim(now=T0 + 8 * 86400, seed=3)
        check("a week of drives", 8 <= n <= 25)
        pub = journal.read_public(True, 60, now=T0 + 8 * 86400)
        check("labelled simulated, every entry", pub["simulated"] and pub["source"] == "sim"
              and all(x["simulated"] for x in pub["entries"]))
        check("simulated entries have lines", all(x["line"] for x in pub["entries"]))
        check("newest first", [x["t0"] for x in pub["entries"]] == sorted((x["t0"] for x in pub["entries"]), reverse=True))
        check("same seed, same week", journal.sim(now=T0 + 8 * 86400, seed=3) == n)
        sim_t0 = pub["entries"][0]["t0"]
        journal.set_note_any(sim_t0, "Rain all the way")
        check("a note on a simulated drive stays in the simulator",
              journal.read_public(True, 60, now=T0 + 8 * 86400)["entries"][0]["note"] == "Rain all the way"
              and not (journal._load_json(journal.STATE) or {}).get("notes", {}).get(str(int(sim_t0))))
        print("\n  the real log, freezing and notes")
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE trips (t0 REAL PRIMARY KEY, t1 REAL, km REAL, litres REAL, lphk REAL,
                      moving_s INTEGER, idle_s INTEGER, top_kph REAL, kind TEXT, label TEXT)""")
        db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (T0, T0 + 2280, 38.6, 1.8, 4.6, 2100, 180, 112, "drive", "07:42"))
        db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (T0 + 9000, None, None, None, None, 60, 0, 20, "drive", "09:30"))
        got = journal.build(db, 10, now=T0 + 2280 + journal.SETTLE + 5)
        check("newest first, open drive included", [g["t0"] for g in got] == [T0 + 9000, T0])
        check("the settled drive is frozen, the open one is not",
              list((journal._load_json(journal.STATE) or {}).get("frozen", {})) == [str(int(T0))])
        journal.set_note(T0, "Rain")
        got = journal.build(db, 10, now=T0 + 99999)
        check("a note rides on the entry and is not baked into the frozen copy",
              got[1]["note"] == "Rain"
              and journal._load_json(journal.STATE)["frozen"][str(int(T0))].get("note") is None)
        db.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all journal checks pass' if not fails else str(fails) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
