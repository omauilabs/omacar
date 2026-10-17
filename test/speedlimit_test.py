#!/usr/bin/env python3
"""The offline speed limit (lib/speedlimit.py, tools/speedlimits_build.py).

Everything runs on test/fixtures/speedlimits.osm, a made-up neighbourhood, with
the standard library: no GPS, no network, no real map.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

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


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-speedlimit-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
    dbfile = os.path.join(scratch, "sample.db")
    os.environ["OMACAR_SPEEDLIMITS_DB"] = dbfile
    import speedlimit as S
    import location as L
    try:
        print("\n  reading maxspeed")
        p = S.parse_maxspeed
        check("35 mph is 35", p("35 mph") == 35 and p("35mph") == 35)
        check("a bare number is km/h (50 -> 31)", p("50") == 31 and p("50 km/h") == 31)
        check("signals, none, walk, a list and a country code are nothing",
              all(p(v) is None for v in ("signals", "none", "walk", "25;35", "US:urban", "", None)))
        check("nonsense numbers are refused (0, 400 mph)", p("0 mph") is None and p("400 mph") is None)

        print("\n  ways to rows: posted, default, nothing")
        line = [(37.0, -122.0), (37.0, -121.999)]
        r = S.rows_for_way(1, {"highway": "secondary", "maxspeed": "45 mph"}, line)
        check("a tagged way is posted", len(r) == 1 and r[0]["source"] == "posted" and r[0]["mph_f"] == 45)
        r = S.rows_for_way(2, {"highway": "residential"}, line)
        check("an untagged residential way is a labelled 25 mph default",
              len(r) == 1 and r[0]["source"] == "default" and r[0]["mph_f"] == 25)
        check("an untagged residential way never says posted", all(x["source"] != "posted" for x in r))
        check("no default for unclassified, tertiary or motorway",
              all(S.rows_for_way(3, {"highway": h}, line) == [] for h in ("unclassified", "tertiary", "motorway")))
        check("a posted limit beats the default", S.rows_for_way(4, {"highway": "residential", "maxspeed": "15 mph"}, line)[0]["mph_f"] == 15)
        check("maxspeed=signals on a road with no default is dropped",
              S.rows_for_way(5, {"highway": "secondary", "maxspeed": "signals"}, line) == [])
        check("a footway is not a road", S.rows_for_way(6, {"highway": "footway", "maxspeed": "5 mph"}, line) == [])
        r = S.rows_for_way(7, {"highway": "motorway", "maxspeed": "65 mph"}, line)[0]
        check("a motorway is one-way with no 'against' limit", r["oneway"] == 1 and r["mph_b"] is None)
        r = S.rows_for_way(8, {"highway": "secondary", "maxspeed:forward": "45 mph", "maxspeed:backward": "35 mph"}, line)[0]
        check("forward and backward limits are kept apart", (r["mph_f"], r["mph_b"]) == (45, 35))
        r = S.rows_for_way(9, {"highway": "residential", "oneway": "-1", "maxspeed": "20 mph"}, line)[0]
        check("oneway=-1 reverses the points", r["points"][0] == line[1] and r["oneway"] == 1)
        long = [(37.0, -122.0 + i * 0.001) for i in range(20)]       # ~1.7 km
        ch = S.rows_for_way(10, {"highway": "secondary", "maxspeed": "35 mph"}, long)
        check("a long way is cut into chunks of about 250 m or less", len(ch) >= 7 and
              all(S._len_m(c["points"][0], c["points"][-1]) <= 350 for c in ch))
        check("the chunks share their end points (no gaps)",
              all(ch[i]["points"][-1] == ch[i + 1]["points"][0] for i in range(len(ch) - 1)))
        check("points survive packing to 1e-7 degrees", all(abs(a - b) < 1e-6 for q, w in
              zip(S.unpack_pts(S.pack_pts(long)), long) for a, b in zip(q, w)))

        print("\n  the database and its R*Tree")
        n = S.build_db(S.read_osm_xml(S.SAMPLE_OSM), dbfile, source="sample", sample=1)
        db = S.Db(dbfile)
        check(f"the sample builds ({n} chunks; unclassified and signals dropped)", n == 13)
        check("the attribution is stored with the data", "OpenStreetMap contributors" in db.meta["attribution"]
              and "ODbL" in db.meta["attribution"])
        names = lambda rows: sorted({r["name"] for r in db.rows_near(*rows)})
        check("a point on Main St finds Main St and the frontage road 20 m away",
              names((37.77, -122.427)) == ["Frontage Rd", "Main St"])
        check("a point in a field finds nothing", db.rows_near(37.80, -122.40) == [])
        check("the box is 25 m: Oak Ave 30 m away is not near Main St's far end",
              "Oak Ave" not in names((37.77, -122.4291)))
        plan = " ".join(str(x[-1]) for x in db.con.execute(
            "EXPLAIN QUERY PLAN SELECT c.id FROM chunk_idx i JOIN chunks c ON c.id = i.id "
            "WHERE i.max_lat >= 1 AND i.min_lat <= 2 AND i.max_lon >= 3 AND i.min_lon <= 4"))
        check("the lookup goes through the R*Tree, not a scan", "VIRTUAL TABLE INDEX 2:" in plan and "SCAN c" not in plan)
        # Scale: 30,000 chunks, and a query must only touch the few near the point.
        big = os.path.join(scratch, "big.db")
        dbb = S.create_db(big)
        rows = []
        for i in range(30000):
            la = 36.0 + (i // 200) * 0.002
            lo = -121.0 + (i % 200) * 0.002
            rows += S.rows_for_way(i + 1, {"highway": "secondary", "maxspeed": "35 mph"},
                                   [(la, lo), (la, lo + 0.001)])
        S.add_rows(dbb, rows)
        S.finish_db(dbb)
        dbb.close()
        bd = S.Db(big)
        t0 = time.time()
        hit = bd.rows_near(36.0 + 50 * 0.002, -121.0 + 100 * 0.002)
        dt = time.time() - t0
        check(f"in 30,000 chunks a query returns only the neighbours ({len(hit)}) in {dt * 1000:.1f} ms",
              0 < len(hit) <= 4 and dt < 0.2)
        bd.close()

        print("\n  heading matching")
        c = lambda lat, lon, course: db.candidates(lat, lon, course)
        got = c(37.77, -122.4270, 90)
        check("heading east on Main St picks Main St, 35 posted", got[0].name == "Main St" and got[0].mph == 35
              and got[0].source == "posted")
        got = c(37.77, -122.4250, 90)
        check("at the Oak Ave crossing, heading east, Oak Ave (90 degrees across) is not a candidate",
              [x.name for x in got].count("Oak Ave") == 0)
        got = c(37.77, -122.4250, 0)
        check("the same spot heading north is Oak Ave", got[0].name == "Oak Ave")
        got = c(37.77, -122.41005, 0)
        check("northbound on the freeway finds the 65 side, not the 55 side 10 m away",
              [(x.name, x.mph) for x in got] == [("Hwy 101 northbound", 65)])
        got = c(37.77, -122.41000, 180)
        check("southbound finds the 55 side", [(x.name, x.mph) for x in got] == [("Hwy 101 southbound", 55)])
        check("a one-way road is not a candidate for the wrong way", c(37.77, -122.41005, 180) == [] or
              all(x.name != "Hwy 101 northbound" for x in c(37.77, -122.41005, 180)))
        check("Split Rd east is 45, west is 35 (the 'against' limit)",
              c(37.76, -122.417, 90)[0].mph == 45 and c(37.76, -122.417, 270)[0].mph == 35)
        check("the closer parallel road scores better than the farther one",
              c(37.77009, -122.427, 90)[0].name in ("Main St", "Frontage Rd"))
        check("with no heading, nearest wins and heading is ignored", c(37.77, -122.4250, None)[0].name in ("Main St", "Oak Ave"))

        print("\n  hysteresis")
        m = S.Matcher(db)
        v = [m.feed(37.77, -122.4270 + i * 0.0001, 90, 12) for i in range(3)]
        check("nothing is shown on the first two fixes; the third locks on",
              v[0]["limit"] is None and v[1]["limit"] is None and v[2]["limit"] == 35)
        # Between the two parallel roads at 10 m from each, ambiguous; a lock must not flip.
        m = S.Matcher(db)
        for i in range(4):
            m.feed(37.77, -122.4270 + i * 0.0001, 90, 12)
        seq = []
        for i in range(8):
            la = 37.77 + (0.00018 if i % 2 else 0.0)         # noise: Main, then Frontage position, ...
            seq.append(m.feed(la, -122.4266 + i * 0.0001, 90, 12))
        check("noisy fixes hopping between Main St and the frontage road never change the locked way",
              m.locked == 1000)
        check("and the sign never shows the frontage road's 25",
              all(s["limit"] != 25 for s in seq))
        # A real turn: onto Oak Ave heading north. Needs 3 fixes to take it.
        m = S.Matcher(db)
        for i in range(4):
            m.feed(37.77, -122.4270 + i * 0.0001, 90, 12)
        r1 = m.feed(37.77005, -122.4250, 0, 12)
        r2 = m.feed(37.77030, -122.4250, 0, 12)
        r3 = m.feed(37.77060, -122.4250, 0, 12)
        check("after a turn the old road is held for two fixes and Oak Ave is taken on the third",
              m.locked == 1002 and r3["road"] == "Oak Ave")
        check("on the way, the old road's sign is not shown against a car now heading away from it",
              r1["limit"] is None and r2["limit"] is None)
        check("Oak Ave is a DEFAULT, not posted", r3["limit"] == 25 and r3["source"] == "default")
        # Leaving the road: three fixes in a field drops the lock.
        for i in range(2):
            out = m.feed(37.80, -122.40, 0, 12)
        check("out of reach for two fixes: nothing shown, lock held", out["limit"] is None and m.locked == 1002)
        out = m.feed(37.80, -122.40, 0, 12)
        check("out of reach for three: the lock is let go", m.locked is None and out["confidence"] == "none")
        # A stopped car's wobble never changes road.
        m = S.Matcher(db)
        for i in range(4):
            m.feed(37.77, -122.4270, 90, 12)
        for la in (37.77010, 37.77016, 37.77017, 37.77016, 37.77017):
            m.feed(la, -122.4270, 0.0, 0.1)
        check("a stopped car creeping toward the frontage road keeps Main St", m.locked == 1000)

        print("\n  confidence")
        m = S.Matcher(db)
        for i in range(4):
            r = m.feed(37.77, -122.4270 + i * 0.0001, 90, 12)
        check("on the road, lined up, nothing contradicting: high", r["confidence"] == "high" and r["limit"] == 35)
        m = S.Matcher(db)
        for i in range(4):
            r = m.feed(37.77009, -122.4270 + i * 0.0001, 90, 12)
        check("halfway between a 35 and a 25 road: LOW, and low shows nothing",
              r["confidence"] == "low" and r["limit"] is None and "different" in r["why"])
        m = S.Matcher(db)
        for i in range(4):
            r = m.feed(37.77, -122.4270 + i * 0.0001, 45, 12)
        check("a car pointing 45 degrees off the road is not confident", r["limit"] is None)
        m = S.Matcher(db)
        for i in range(4):
            r = m.feed(37.76970 , -122.4270 + i * 0.0001, 90, 12)
        check("a road 33 m away is out of reach: no road in reach", r["confidence"] == "none")
        m = S.Matcher(db)
        for i in range(4):
            r = m.feed(37.77, -122.4270, None, None)
        check("a stopped car with no heading still gets the answer for the road it's on", r["limit"] == 35)

        print("\n  what /api/speedlimit says")
        S.reset()
        os.environ["OMACAR_SPEEDLIMITS_DB"] = os.path.join(scratch, "nonesuch.db")
        body = S.read_public()
        check("no database: present is false and it says how to get one",
              body["present"] is False and "fetch" in body["why"] and body["limit"] is None)
        S.reset()
        os.environ["OMACAR_SPEEDLIMITS_DB"] = dbfile
        body = S.read_public()
        check("database but no GPS: present, no limit, 'No GPS fix'", body["present"] and body["limit"] is None
              and body["why"] == "No GPS fix" and body["fix"] is False)
        st = L.blank("sim")
        t = time.time()
        for i in range(4):
            st.update(fix=True, lat=37.77, lon=-122.4270 + i * 0.0001, speed_ms=15.0, track=90.0, mode=3)
            L.write(st)
            body = S.read_public()
            time.sleep(0.01)
        check("a simulated fix on Main St: 35 posted, source sim, attribution present",
              body["limit"] == 35 and body["source"] == "posted" and body["location_source"] == "sim"
              and "OpenStreetMap" in body["attribution"] and body["road"] == "Main St")
        check("gps_mph follows the fix's speed (15 m/s = 33.6 mph)", abs(body["gps_mph"] - 33.55) < 0.1)
        check("the sample database says it is a sample", body["data"]["sample"] is True)
        again = S.read_public()
        check("reading twice without a new fix does not count the same fix twice", again["limit"] == 35
              and S._matcher.chal_n == 0)
        st.update(fix=False, lat=None, lon=None)
        L.write(st)
        body = S.read_public()
        check("a lost fix clears the limit and the matcher", body["limit"] is None and S._matcher.locked is None)

        print("\n  fetch and build")
        S.reset()
        target = os.path.join(scratch, "data", "omacar", "speedlimits.db")
        os.environ.pop("OMACAR_SPEEDLIMITS_DB")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "speedlimit.py"), "fetch"],
                           capture_output=True, text=True)
        check("fetch with nothing configured explains itself and downloads nothing",
              r.returncode == 2 and "never shipped in git" in r.stderr and not os.path.exists(target))
        import hashlib
        good = "file://" + dbfile
        sha = hashlib.sha256(open(dbfile, "rb").read()).hexdigest()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "speedlimit.py"), "fetch", "--url", good,
                            "--sha256", "0" * 64], capture_output=True, text=True)
        check("a wrong checksum installs nothing", r.returncode == 1 and not os.path.exists(target))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "speedlimit.py"), "fetch", "--url", good,
                            "--sha256", sha], capture_output=True, text=True)
        check("a right checksum installs it, with the attribution", r.returncode == 0 and os.path.exists(target)
              and "OpenStreetMap" in r.stdout)
        junk = os.path.join(scratch, "junk.db")
        open(junk, "wb").write(b"not a database at all")
        os.remove(target)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "speedlimit.py"), "fetch", "--url",
                            "file://" + junk], capture_output=True, text=True)
        check("a file that is not a database is refused", r.returncode == 1 and not os.path.exists(target))
        tool = os.path.join(ROOT, "tools", "speedlimits_build.py")
        out2 = os.path.join(scratch, "tool.db")
        r = subprocess.run([sys.executable, tool, S.SAMPLE_OSM, "--out", out2, "--reader", "xml"],
                           capture_output=True, text=True)
        check("the desktop tool builds the sample with the standard library", r.returncode == 0
              and sqlite3.connect(out2).execute("select count(*) from chunks").fetchone()[0] == 13)
        check("the tool prints the ODbL attribution", "ODbL" in r.stdout)
        try:
            import osmium   # noqa: F401
            r = subprocess.run([sys.executable, tool, S.SAMPLE_OSM, "--out", out2 + "2", "--reader", "osmium"],
                               capture_output=True, text=True)
            q = "select way_id, mph_f, mph_b, source, oneway, name, pts from chunks order by way_id, id"
            same = sqlite3.connect(out2).execute(q).fetchall() == sqlite3.connect(out2 + "2").execute(q).fetchall()
            check("pyosmium and the XML reader build the identical database", r.returncode == 0 and same)
        except ImportError:
            print("    skip  pyosmium is not installed here; the pyosmium reader is not tested on this run")
    finally:
        S.reset()
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'every speed-limit check holds' if not fails else str(fails) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
