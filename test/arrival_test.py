#!/usr/bin/env python3
"""Arriving home and the pickup pre-cool (lib/location.py, lib/arrival.py).

Pure geometry and the planner on a fake clock, then the files and the API's
actions in a scratch HOME. No socket to gpsd, no radio, no real clock.
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


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-arrival-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import location as L
    import arrival as A
    import fridge
    import notify
    try:
        print("\n  geometry")
        d = L.haversine_m(37.7749, -122.4194, 34.0522, -118.2437)
        check(f"San Francisco to Los Angeles is about 559 km ({d / 1000:.0f})", 555e3 < d < 563e3)
        check("same point is zero", L.haversine_m(10, 20, 10, 20) == 0)
        check("one degree of latitude is 111 km", abs(L.haversine_m(0, 0, 1, 0) - 111195) < 200)
        check("bearing due north is 0", abs(L.bearing_deg(0, 0, 1, 0)) < 0.01)
        check("bearing due east is 90", abs(L.bearing_deg(0, 0, 0, 1) - 90) < 0.01)
        lat, lon = L.offset(37.0, -122.0, 90, 1000)
        check("offset 1 km east is 1 km away", abs(L.haversine_m(37.0, -122.0, lat, lon) - 1000) < 1)
        check("ETA: 1 km at 10 m/s is 100 s", L.eta_s(1000, 10) == 100)
        check("ETA: stopped has no answer, never infinity", L.eta_s(1000, 0.2) is None)
        check("ETA: no speed has no answer", L.eta_s(1000, None) is None)

        print("\n  gpsd")
        tpv = '{"class":"TPV","mode":3,"lat":37.5,"lon":-122.1,"speed":12.5,"track":90.0}'
        check("a TPV with a 3D fix parses",
              L.parse_tpv(tpv) == {"lat": 37.5, "lon": -122.1, "mode": 3, "speed_ms": 12.5, "track": 90.0})
        check("mode 1 (no fix) is no position", L.parse_tpv('{"class":"TPV","mode":1}') is None)
        check("a TPV without lat/lon is no position", L.parse_tpv('{"class":"TPV","mode":2}') is None)
        check("an impossible latitude is refused",
              L.parse_tpv('{"class":"TPV","mode":3,"lat":95,"lon":0}') is None)
        check("other classes and junk are ignored",
              L.parse_tpv('{"class":"SKY"}') is None and L.parse_tpv("not json") is None)
        check("no location file means no fix, honestly",
              L.read_public()["fix"] is False and L.read_public()["source"] == "none")
        st = L.blank("gpsd")
        st.update(fix=True, lat=1.0, lon=2.0, speed_ms=3.0)
        L.write(st)
        check("a fresh fix is a fix", L.read_public()["fix"] is True)
        st = json.load(open(L.STATE))
        st["at"] -= 600
        json.dump(st, open(L.STATE, "w"))
        r = L.read_public()
        check("a stale fix is not a position, but is remembered as the last one",
              r["fix"] is False and r["lat"] is None and r["last"] == {"lat": 1.0, "lon": 2.0})

        print("\n  places")
        check("no file is no places", L.load_places()["home"] is None and L.load_places()["pickups"] == [])
        L.set_home(37.7, -122.4, 120)
        L.add_pickup("Target Marina", 37.8, -122.43)
        L.add_pickup("TARGET MARINA".title(), 37.81, -122.43)
        p = L.load_places()
        check("home and one pickup (same name replaces)",
              p["home"]["radius_m"] == 120 and len(p["pickups"]) == 1 and p["pickups"][0]["lat"] == 37.81)
        for bad in [(91, 0), (0, 181), ("x", 0), (float("nan"), 0)]:
            try:
                L.set_home(*bad)
                check(f"refuses home {bad}", False)
            except ValueError:
                check(f"refuses home {bad}", True)
        try:
            L.add_pickup("  ", 1, 1)
            check("refuses a nameless pickup", False)
        except ValueError:
            check("refuses a nameless pickup", True)
        pp = json.load(open(L.PLACES))
        pp["house_command"] = "true"
        json.dump(pp, open(L.PLACES, "w"))
        check("the House command is kept through an edit and never shown",
              L.public_places().get("house_set") is True and "house_command" not in L.public_places())
        L.add_pickup("Costco", 37.5, -122.3)
        check("an edit through the editor keeps the House command",
              json.load(open(L.PLACES))["house_command"] == "true")
        code, body = A.places_post({"house_command": "rm -rf /"})
        check("the API cannot set the House command",
              json.load(open(L.PLACES))["house_command"] == "true")
        L.remove_pickup("Costco")

        print("\n  arriving home, on a fake clock")
        home = {"lat": 37.7, "lon": -122.4, "radius_m": 150}
        places = {"home": home, "pickups": [], "fridge_eco_c": 5.0, "auto_pickup": True}
        fr = {"connected": True, "set": 3.0, "range": [-22.0, 10.0]}

        def fix_at(m, t, speed=12.0, bearing=215.0, back=None):
            lat, lon = L.offset(home["lat"], home["lon"], bearing, m)
            return {"lat": lat, "lon": lon, "speed_ms": speed,
                    "track": (bearing + 180) % 360, "recent": back or []}

        def trail(m_from, m_to, t_now, n=10):
            out = []
            for i in range(n + 1):
                m = m_from + (m_to - m_from) * i / n
                lat, lon = L.offset(home["lat"], home["lon"], 215.0, m)
                out.append([t_now - (n - i) * 2, lat, lon])
            return out

        v, trip = A.home_view(None, home, None, 0)
        check("no fix: no arrival card, not a guess", v["phase"] == "no_fix" and trip is None)
        v, trip = A.home_view(fix_at(5000, 0), None, None, 0)
        check("no home set: says so", v["phase"] == "no_home")
        v, trip = A.home_view(fix_at(5000, 0, back=trail(5100, 5000, 0)), home, None, 0)
        check("5 km out is away", v["phase"] == "away" and trip is None)
        v, trip = A.home_view(fix_at(1900, 0, back=trail(2000, 1900, 0)), home, None, 100)
        check("1.9 km and closing is approaching, with an ETA",
              v["phase"] == "approaching" and v["eta_s"] == round(1900 / 12) and trip["id"] == "100")
        v, _ = A.home_view(fix_at(1900, 0, back=trail(1800, 1900, 0)), home, None, 100)
        check("1.9 km and driving away is not approaching", v["phase"] == "away")
        v, _ = A.home_view(fix_at(1900, 0, speed=0.0, back=trail(1900, 1900, 0)), home, None, 100)
        check("1.9 km and parked is not approaching", v["phase"] == "away")
        f = fix_at(1900, 0, back=[])
        v, _ = A.home_view(f, home, None, 100)
        check("with no history, the course decides (heading toward home)", v["phase"] == "approaching")
        f["track"] = (f["track"] + 180) % 360
        v, _ = A.home_view(f, home, None, 100)
        check("...and heading away is away", v["phase"] == "away")
        v, trip = A.home_view(fix_at(100, 0, speed=0), home, None, 100)
        check("at the door is arrived", v["phase"] == "arrived")
        v, trip2 = A.home_view(fix_at(3500, 0), home, trip, 200)
        check("3.5 km away ends the trip", trip2 is None and v["phase"] == "away")

        s0 = A.blank_state()
        t = 1000.0
        s1, acts, ev = A.step(s0, fix_at(1900, t, back=trail(2000, 1900, t)), places, fr, t)
        check("approaching: one notification event, no fridge action, nothing automatic",
              len(ev) == 1 and ev[0]["kind"] == "arrival" and acts == [])
        s2, acts, ev = A.step(s1, fix_at(1500, t + 20, back=trail(1900, 1500, t + 20)), places, fr, t + 20)
        check("the next tick does not notify again (once per trip)", ev == [] and acts == [])
        s3, _, ev = A.step(s2, fix_at(80, t + 200, speed=0), places, fr, t + 200)
        check("arriving at the door is still the same trip, no second message", ev == [])
        s4, _, _ = A.step(s3, fix_at(4000, t + 900), places, fr, t + 900)
        s5, _, ev = A.step(s4, fix_at(1900, t + 1500, back=trail(2000, 1900, t + 1500)), places, fr, t + 1500)
        check("leaving and coming back is a new trip and one new message", len(ev) == 1)
        check("the trip id is stable and the dismiss flag lives on it",
              s1["trip"]["id"] == "1000" and s1["trip"]["dismissed"] is False)

        print("\n  the pickup plan, on a fake clock")
        place = {"name": "Target Marina", "lat": 37.8, "lon": -122.43}
        places2 = dict(places, pickups=[place])

        def at_place(m, bearing=10.0, speed=12.0, back=None):
            lat, lon = L.offset(place["lat"], place["lon"], bearing, m)
            return {"lat": lat, "lon": lon, "speed_ms": speed, "track": (bearing + 180) % 360,
                    "recent": back or []}

        t = 5000.0
        pk = A.new_pickup("Target Marina", t)
        # 30 min away at 12 m/s = 21.6 km
        pk1, acts, _ = A.plan_pickup(pk, at_place(21600), places2, fr, t)
        check("30 min out: nothing yet", acts == [] and pk1["cooled_to"] is None)
        pk2, acts, _ = A.plan_pickup(pk1, at_place(14000), places2, fr, t + 600)
        check("19 min out: 2 C colder (3 -> 1)", acts == [{"set": 1.0, "why": "cooling for the pickup"}]
              and pk2["base_set"] == 3.0 and pk2["cooled_to"] == 1.0)
        pk3, acts, _ = A.plan_pickup(pk2, at_place(10000), places2, fr, t + 900)
        check("and it asks only once", acts == [])
        fr_cold = dict(fr, set=1.0)
        pk4, acts, _ = A.plan_pickup(pk3, at_place(60, speed=0), places2, fr_cold, t + 1500)
        check("at the place: arrived, still cold, nothing to do", acts == [] and pk4["arrived_at"] == t + 1500)
        pk5, acts, ended = A.plan_pickup(pk4, at_place(300, speed=5), places2, fr_cold, t + 1800)
        check("300 m away is not 'left yet'", acts == [] and ended is None)
        pk6, acts, ended = A.plan_pickup(pk5, at_place(900, speed=10), places2, fr_cold, t + 1900)
        check("leaving the place restores 3 C and ends the plan",
              acts == [{"set": 3.0, "why": "pickup over (left)"}] and pk6 is None
              and ended["why"] == "left" and ended["restored_to"] == 3.0)
        # timeout path
        pkt, acts, _ = A.plan_pickup(A.new_pickup("Target Marina", t), at_place(14000), places2, fr, t)
        pku, acts, ended = A.plan_pickup(pkt, at_place(14000, speed=0), places2, fr_cold, t + 7199)
        check("one second short of two hours: still cooling", acts == [] and pku is not None)
        pku, acts, ended = A.plan_pickup(pku, at_place(14000, speed=0), places2, fr_cold, t + 7201)
        check("after two hours: restored", acts == [{"set": 3.0, "why": "pickup over (two hours)"}]
              and ended["why"] == "two hours")
        # the owner changed it meanwhile
        fr_own = dict(fr, set=6.0)
        pkv, acts, _ = A.plan_pickup(pkt, at_place(14000, speed=0), places2, fr_own, t + 7201)
        check("if the owner changed the set point since, it is left alone", acts == [] and pkv is None)
        # cancel
        pkc = dict(pkt, cancel=True)
        pkd, acts, ended = A.plan_pickup(pkc, at_place(14000), places2, fr_cold, t + 60)
        check("cancel restores what it cooled", acts == [{"set": 3.0, "why": "pickup over (cancelled)"}]
              and ended["why"] == "cancelled")
        pke, acts, ended = A.plan_pickup(dict(A.new_pickup("Target Marina", t), cancel=True),
                                         at_place(21600), places2, fr, t + 10)
        check("cancelling before any cooling does nothing to the fridge", acts == [] and ended is not None)
        # bounds
        lowfr = dict(fr, set=-21.0)
        pkl, acts, _ = A.plan_pickup(A.new_pickup("Target Marina", t, 5), None, places2, lowfr, t)
        check("bounded by the fridge's range (-21 -> -22, not -23)",
              acts == [{"set": -22.0, "why": "cooling for the pickup"}])
        floor = dict(fr, set=-22.0)
        pkl, acts, _ = A.plan_pickup(A.new_pickup("Target Marina", t, 5), None, places2, floor, t)
        check("already at the floor: no write, and it says so", acts == [] and "cold as" in pkl["note"])
        check("cold_target stays on half degrees", A.cold_target(3.3, [-22, 10]) == 1.5)
        # no fix, a typed ETA
        pkm = A.new_pickup("Target Marina", t, eta_min=40)
        pkm1, acts, _ = A.plan_pickup(pkm, None, places2, fr, t + 60)
        check("no GPS, owner said 40 min: waits", acts == [] and pkm1["cooled_to"] is None)
        pkm2, acts, _ = A.plan_pickup(pkm1, None, places2, fr, t + 60 * 21)
        check("19 min before the owner's own ETA: cools", len(acts) == 1)
        pkn, acts, _ = A.plan_pickup(A.new_pickup("Target Marina", t), None, places2, fr, t + 99999)
        check("no GPS and no ETA: never cools by guessing", acts == [] and pkn["cooled_to"] is None)
        # the fridge is away
        away = dict(fr, connected=False, set=None)
        pkf, acts, _ = A.plan_pickup(A.new_pickup("Target Marina", t, 5), None, places2, away, t)
        check("fridge not connected: no write, says why, tries again",
              acts == [] and pkf["cooled_to"] is None and "not connected" in pkf["note"])
        # the place vanished
        gone, acts, _ = A.plan_pickup(A.new_pickup("Nowhere", t, 5), None, places2, fr, t)
        check("a deleted place drops the plan", gone is None and acts == [])

        print("\n  proximity (auto) pickups")
        s = A.blank_state()
        far = at_place(21600, back=trail(0, 0, t))
        s1, acts, _ = A.step(s, dict(far, recent=[]), places2, fr, t)
        check("30 min away: nothing", s1["pickup"] is None)
        near = at_place(9000, back=[[t - 20, *L.offset(place["lat"], place["lon"], 10.0, 9240)],
                                    [t, *L.offset(place["lat"], place["lon"], 10.0, 9000)]])
        s2, acts, _ = A.step(s, near, places2, fr, t)
        check("12 min away and closing: auto pickup, cools now",
              s2["pickup"] and s2["pickup"]["mode"] == "auto" and acts and acts[0]["set"] == 1.0)
        hl, ho = L.offset(near["lat"], near["lon"], 190.0, 3000)
        homeward = dict(places2, home={"lat": hl, "lon": ho, "radius_m": 150})
        s2h, _, _ = A.step(s, near, homeward, fr, t)
        check("heading for it, but home is nearer: a drive home is not a pickup", s2h["pickup"] is None)
        sideways = dict(near, track=100.0)
        s2c, _, _ = A.step(s, sideways, places2, fr, t)
        check("a course that points away from it is not heading there", s2c["pickup"] is None)
        s3, acts, _ = A.step(s, near, dict(places2, auto_pickup=False), fr, t)
        check("auto can be switched off in places.json", s3["pickup"] is None)
        gone_by = at_place(9000, back=[[t - 20, *L.offset(place["lat"], place["lon"], 10.0, 8760)],
                                       [t, *L.offset(place["lat"], place["lon"], 10.0, 9000)]])
        s4, _, _ = A.step(s, gone_by, places2, fr, t)
        check("12 min away and driving away: nothing", s4["pickup"] is None)

        print("\n  the files and the API's taps")
        A.save(A.blank_state())
        code, body = A.do("garage", {})
        check("garage: disabled and says why when lib/garage_door.py does not exist",
              code == 409 and "not installed" in body["error"] and not A.garage_installed())
        code, body = A.do("house", {})
        check("house runs the owner's own command when one is set", code == 200 and body == {"ran": True})
        pp = json.load(open(L.PLACES))
        pp["house_command"] = ""
        json.dump(pp, open(L.PLACES, "w"))
        code, body = A.do("house", {})
        check("house with an empty command is refused, runs nothing", code == 409)
        code, body = A.do("pickup", {"name": "Nope"})
        check("an unknown pickup is refused", code == 400)
        code, body = A.do("pickup", {"name": "target marina", "eta_min": 45})
        check("heading to a pickup is stored, with the saved spelling",
              code == 200 and body["heading_to"] == "Target Marina" and A.load()["pickup"]["name"] == "Target Marina")
        code, body = A.do("pickup", {"name": "Target Marina", "eta_min": "soon"})
        check("a nonsense ETA is refused", code == 400)
        pub = A.read_public()
        check("the public view carries the plan, the source and 'not watching' with no loop",
              pub["pickup"]["name"] == "Target Marina" and pub["watching"] is False
              and pub["location"]["fix"] is False and pub["actions"]["garage"]["available"] is False
              and pub["actions"]["house"]["available"] is False)
        code, body = A.do("pickup_cancel", {})
        check("cancel marks the plan", A.load()["pickup"]["cancel"] is True)
        code, body = A.do("fridge_eco", {})
        with open(fridge.ASK) as f:
            ask = json.load(f)
        check("Fridge to eco asks the fridge for the configured eco set point",
              code == 200 and ask["name"] == "z0_set" and ask["value"] == 5.0)
        os.unlink(fridge.ASK)
        code, body = A.places_post({"fridge_eco_c": 40})
        check("an eco temperature outside the fridge's range is refused", code == 400)
        code, body = A.places_post({"home": {"lat": 200, "lon": 0}})
        check("an impossible home is refused", code == 400)
        code, body = A.places_post({"add_pickup": {"name": "Pier 39", "lat": 37.8, "lon": -122.4}})
        check("the editor adds a pickup", code == 200 and any(p["name"] == "Pier 39" for p in body["pickups"]))

        print("\n  tick (the I/O around step) and notifications")
        shutil.rmtree(os.environ["XDG_STATE_HOME"], ignore_errors=True)
        sstat = L.blank("sim")
        lat, lon = L.offset(37.7, -122.4, 215.0, 1500)
        sstat.update(fix=True, lat=lat, lon=lon, speed_ms=10.0, track=35.0)
        for i in range(8):
            lt, ln = L.offset(37.7, -122.4, 215.0, 1700 - i * 25)
            sstat["recent"].append([1000.0 - 20 + i * 2.5, lt, ln])
        import time as _t
        sstat["recent"] = [[_t.time() - 20 + i * 2.5, r[1], r[2]] for i, r in enumerate(sstat["recent"])]
        L.write(sstat)
        A.tick()
        A.tick()
        rows = notify._read()
        check("arriving sent exactly one 'arrival' message across two ticks",
              len([r for r in rows if r["kind"] == "arrival"]) == 1 and rows[-1]["urgency"] == "info")
        pub = A.read_public()
        check("the view says approaching, Simulated, and that the loop is watching",
              pub["arrival"]["phase"] == "approaching" and pub["location"]["source"] == "sim"
              and pub["watching"] is True)
        A.do("dismiss", {})
        check("dismissing hides it for this trip", A.read_public()["arrival"]["dismissed"] is True)

        print("\n  the simulator")
        import io
        L.sim("home", seconds=1.2, tick=0.4)
        s = L.read_public()
        check("the sim says Simulated in its source and note",
              s["source"] == "sim" and "Simulated" in s["note"] and s["fix"])
        d0 = L.haversine_m(s["lat"], s["lon"], 37.7, -122.4)
        check(f"and starts about 6 km out ({d0 / 1000:.1f} km)", 5500 < d0 < 6100)
        L.clear_home()
        check("with no home saved, the sim refuses rather than invent one",
              L.sim("home", seconds=0.1) == 2 and L.read_public()["fix"] is False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
