#!/usr/bin/env python3
"""The fuel log (lib/fuel.py): fill-up detection, economy maths, the log on disk.

The two things that must not happen: a hill read as a fill-up (a made-up entry
in a record people trust), and a real fill-up read as sloshing (the question
never asked, and a month of economy lost). So the detector is run on traces
that behave like a tank: noisy, sloshing on hills, with a top-up that must not
count and a real fill that must.

No car, no clock, no network: every trace is synthetic and every time is an
argument. The log on disk runs against a scratch HOME.
"""

import math
import os
import random
import shutil
import sqlite3
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


# ---- synthetic traces -----------------------------------------------------------------

T0 = 1_000_000.0


def drive(t, secs, level, speed=60.0, slosh=0.0, rnd=None, odo=None, burn=0.0):
    """Readings every 2 s while moving, the level wandering by `slosh`."""
    out = []
    rnd = rnd or random.Random(1)
    k = 0
    while k * 2.0 < secs:
        tt = t + k * 2.0
        wobble = slosh * math.sin(k / 6.0) + rnd.gauss(0, slosh / 4.0 if slosh else 0.2)
        out.append({"t": tt, "fuel": level - burn * k * 2.0 / 60.0 + wobble,
                    "speed": speed, "odo": None if odo is None else odo + speed * k * 2.0 / 3600.0})
        k += 1
    return out


def park(t, secs, level, noise=0.2, rnd=None, odo=None):
    rnd = rnd or random.Random(2)
    return [{"t": t + k * 2.0, "fuel": level + rnd.gauss(0, noise), "speed": 0.0, "odo": odo}
            for k in range(int(secs / 2.0))]


def stop_and_fill(level_before, level_after, off_s=360.0, pre=40.0, post=50.0,
                  slosh=0.0, odo=None):
    """A drive that ends in a stop, ignition off for off_s, ignition back on."""
    tr = drive(T0, 600, level_before, slosh=slosh, odo=odo)
    t = tr[-1]["t"] + 2.0
    tr += park(t, pre, level_before, odo=odo)
    t = tr[-1]["t"] + off_s
    tr += park(t, post, level_after, odo=odo)
    return tr


def main():
    import fuel

    print("\n  detection: a real fill, noisy")
    tr = stop_and_fill(24.0, 91.0, slosh=5.0, odo=137_412.5)
    ev = fuel.detect(tr, tank_l=40.0)
    check("one fill-up is found", len(ev) == 1)
    e = ev[0] if ev else {}
    check("levels are the medians either side (24 -> 91, to a point)",
          abs(e.get("level_before", 0) - 24) < 1.5 and abs(e.get("level_after", 0) - 91) < 1.5)
    check("the odometer rides along", e.get("odo_km") == 137_412.5)
    check("the gauge estimate of litres is 40 L x the rise",
          abs(e.get("est_litres", 0) - 40 * 0.67) < 1.0)
    check("it is timed at the first reading after the stop",
          e.get("at") == tr[-int(50 / 2)]["t"])
    check("the same trace gives the same answer (pure)", fuel.detect(tr, tank_l=40.0) == ev)

    print("\n  detection: things that are not fill-ups")
    tr = stop_and_fill(60.0, 70.0)
    check("a 10 point top-up is not detected", fuel.detect(tr) == [])
    tr = stop_and_fill(60.0, 74.9)
    check("14.9 points is not", fuel.detect(tr) == [])
    tr = stop_and_fill(60.0, 75.2)
    check("15.2 points is", len(fuel.detect(tr)) == 1)
    tr = stop_and_fill(80.0, 30.0)
    check("a fall is never a fill-up", fuel.detect(tr) == [])

    # Sloshing: a long hilly drive, the level swinging 8 points either way, then
    # parked on a slope where the sender reads 6 points high.
    rnd = random.Random(7)
    tr = drive(T0, 1800, 55.0, slosh=8.0, rnd=rnd)
    t = tr[-1]["t"] + 2
    tr += park(t, 40, 55.0, noise=1.0, rnd=rnd)
    tr += park(tr[-1]["t"] + 300, 50, 61.0, noise=1.0, rnd=rnd)
    check("slosh on hills plus a slope is not a fill-up", fuel.detect(tr) == [])

    # A bump in the road: a few wild readings in the window, median ignores them.
    tr = stop_and_fill(50.0, 52.0)
    for i in range(3):
        tr[-5 - i]["fuel"] = 90.0
    for i in range(3):
        tr[len(tr) - 40 + i]["fuel"] = 5.0
    check("a few wild readings in a window do not make one", fuel.detect(tr) == [])

    # Dropout mid-drive: the link dies for two minutes at 60 km/h; the level
    # on the far side is 20 points up (a glitch, or a different car).
    tr = drive(T0, 300, 40.0)
    tr += drive(tr[-1]["t"] + 120, 300, 62.0)
    check("a dropout while moving is not a stop", fuel.detect(tr) == [])

    # Stopped at one end only.
    tr = drive(T0, 300, 30.0)
    tr += drive(tr[-1]["t"] + 400, 300, 95.0)
    check("moving at both ends: not a fill-up", fuel.detect(tr) == [])

    print("\n  detection: timing and edge cases")
    tr = stop_and_fill(24.0, 91.0, post=20.0)
    check("held back until 30 s of readings after the stop have been heard",
          fuel.detect(tr) == [])
    check("... and found as soon as the clock says the window has closed",
          len(fuel.detect(tr, now=tr[-1]["t"] + 5.0)) == 0
          and len(fuel.detect(tr, now=tr[-1]["t"] + 40.0)) == 1)
    tr = stop_and_fill(24.0, 91.0, post=20.0) + drive(T0 + 2000, 60, 91.0)
    check("a car that pulls away before 30 s still counts once a later run exists",
          len(fuel.detect(tr)) == 1)
    check("nothing in, nothing out", fuel.detect([]) == [])
    check("one reading either side is not a median",
          fuel.detect([{"t": 0, "fuel": 20, "speed": 0}, {"t": 500, "fuel": 90, "speed": 0}]) == [])
    check("readings with no level are ignored",
          fuel.detect([{"t": 0, "fuel": None, "speed": 0}]) == [])

    # Fuelled with the ignition on: the car never stops reporting.
    tr = park(T0, 60, 22.0) + park(T0 + 60, 120, 88.0)
    ev = fuel.detect(tr, tank_l=40.0)
    check("fuelled with the engine on is found, as stationary", len(ev) == 1 and ev[0]["kind"] == "stationary")
    tr = park(T0, 400, 50.0, noise=0.4)
    check("idling at a light for six minutes is not", fuel.detect(tr) == [])

    # Two readings of one stop are one stop.
    tr = stop_and_fill(24.0, 91.0)
    tr2 = tr + park(tr[-1]["t"] + 200, 40, 24.0) + park(tr[-1]["t"] + 600, 50, 95.0)
    check("two jumps ten minutes apart are one stop", len(fuel.detect(tr2)) == 1)

    print("\n  economy: full to full")
    G = fuel.LITRES_PER_GALLON

    def fill(i, at, odo, gal=None, cost=None, full=True, status="logged", car_km=None, car_gal=None):
        return {"id": i, "at": at, "odo_km": odo * 1.609344 if odo is not None else None,
                "litres": None if gal is None else gal * G, "cost": cost, "full": full,
                "status": status,
                "car_km": car_km, "car_litres": None if car_gal is None else car_gal * G}

    day = 86400.0
    # 300 miles on 10 gallons = 30 mpg.
    f = [fill(1, T0, 10000, 9.0, 31.0),
         fill(2, T0 + 7 * day, 10300, 10.0, 35.0)]
    r = fuel.intervals(f)
    check("two full fills make one interval", len(r["intervals"]) == 1)
    i = r["intervals"][0]
    check("300 mi on 10 gal is 30 mpg", abs(i["mpg"] - 30.0) < 0.1)
    check("... which is 7.84 L/100km", abs(i["l100"] - 7.84) < 0.02)
    check("only the second fill's gallons count: the first filled the start",
          abs(i["litres"] - 10 * G) < 0.01)
    check("cost per mile = $35 / 300 mi", abs(i["cost_per_km"] * 1.609344 - 35 / 300) < 0.0005)

    # A part-fill in between: its gallons belong to the measurement.
    f = [fill(1, T0, 10000, 9.0, 30.0),
         fill(2, T0 + 3 * day, 10120, 4.0, 14.0, full=False),
         fill(3, T0 + 7 * day, 10300, 6.0, 21.0)]
    i = fuel.intervals(f)["intervals"][0]
    check("a part-fill in the middle adds its gallons (4 + 6 = 10 gal, 30 mpg)",
          abs(i["mpg"] - 30.0) < 0.1 and abs(i["cost"] - 35.0) < 0.001)
    check("one interval, not two: a part-fill cannot end a measurement",
          len(fuel.intervals(f)["intervals"]) == 1)

    # A part-fill that opens the log is no anchor.
    f = [fill(1, T0, 10000, 3.0, 10.0, full=False),
         fill(2, T0 + 2 * day, 10100, 8.0, 28.0)]
    check("a part-fill at the start gives nothing to measure from",
          fuel.intervals(f)["intervals"] == [])
    f.append(fill(3, T0 + 6 * day, 10400, 10.0, 35.0))
    check("... the next full fill starts it", len(fuel.intervals(f)["intervals"]) == 1)

    # A skipped fill-up between two fulls: unknown fuel, no number.
    f = [fill(1, T0, 10000, 9.0, 30.0),
         fill(2, T0 + 3 * day, 10150, None, None, status="skipped"),
         fill(3, T0 + 7 * day, 10300, 10.0, 35.0),
         fill(4, T0 + 14 * day, 10600, 10.0, 36.0)]
    r = fuel.intervals(f)
    check("a skipped fill-up in the middle drops that measurement and says why",
          len(r["dropped"]) == 1 and "no quantity" in r["dropped"][0]["why"])
    check("the next full fill-up starts a new one",
          len(r["intervals"]) == 1 and r["intervals"][0]["to_id"] == 4)
    f = [fill(1, T0, 10000, 9.0, 30.0), fill(2, T0 + day, 10300, 10.0, 35.0, status="pending")]
    check("a pending fill-up has no quantity yet and measures nothing",
          fuel.intervals(f)["intervals"] == [])

    # Odometer problems.
    f = [fill(1, T0, 10000, 9.0, 30.0), fill(2, T0 + day, None, 10.0, 35.0)]
    check("an unknown odometer drops the interval",
          fuel.intervals(f)["intervals"] == [] and "odometer" in fuel.intervals(f)["dropped"][0]["why"])
    f = [fill(1, T0, 10000, 9.0, 30.0), fill(2, T0 + day, 10005, 0.3, 1.0)]
    check("five miles is too short to mean anything",
          fuel.intervals(f)["intervals"] == [])
    f = [fill(1, T0, 10000, 9.0, 30.0), fill(2, T0 + day, 10300, 1.0, 3.0)]
    check("300 miles on a gallon is a typo, not an economy",
          fuel.intervals(f)["intervals"] == [] and "check the odometer" in fuel.intervals(f)["dropped"][0]["why"])
    f = [fill(2, T0 + 7 * day, 10300, 10.0, 35.0), fill(1, T0, 10000, 9.0, 30.0)]
    check("the order in the list does not matter, time does",
          len(fuel.intervals(f)["intervals"]) == 1)

    # Cost unknown on one fill: the economy stands, the cost does not.
    f = [fill(1, T0, 10000, 9.0, 30.0),
         fill(2, T0 + 3 * day, 10120, 4.0, None, full=False),
         fill(3, T0 + 7 * day, 10300, 6.0, 21.0)]
    i = fuel.intervals(f)["intervals"][0]
    check("an unpriced part-fill keeps the mpg but not the cost",
          abs(i["mpg"] - 30.0) < 0.1 and i["cost"] is None and i["cost_per_km"] is None)

    print("\n  economy: against the car's own estimate")
    # Pump says 30 mpg (7.84). The car integrates 9.4 gal over 285 of the 300 miles.
    f = [fill(1, T0, 10000, 9.0, 30.0),
         fill(2, T0 + 7 * day, 10300, 10.0, 35.0, car_km=285 * 1.609344, car_gal=8.55)]
    i = fuel.intervals(f)["intervals"][0]
    check("the car's estimate is its litres over its km (30.0 mpg real, 33.3 est)",
          i["car_mpg"] is not None and abs(i["car_mpg"] - 285 / 8.55) < 0.1)
    check("the gauge flatters by 10 per cent here", abs(i["car_error_pct"] - 10.0) < 0.3)
    f[1]["car_km"] = 100 * 1.609344
    check("an estimate covering a third of the distance is not offered",
          fuel.intervals(f)["intervals"][0]["car_l100"] is None)
    km, l = fuel.car_estimate([(60.0, 6.0)] * 3600 + [(2.0, 99.0)] * 100 + [(60.0, None)] * 10)
    check("car_estimate: an hour at 60 km/h and 6 L/100 is 60 km and 3.6 L; crawls and gaps ignored",
          abs(km - 60.0) < 0.01 and abs(l - 3.6) < 0.01)

    print("\n  the month, and the summary")
    jan = time.mktime((2026, 10, 3, 12, 0, 0, 0, 0, -1))
    f = [fill(1, jan - 40 * day, 10000, 9.0, 30.0),
         fill(2, jan - 20 * day, 10300, 10.0, 35.0),
         fill(3, jan + 2 * day, 10620, 10.5, 38.0),
         fill(4, jan + 3 * day, 10700, 2.0, 7.5, full=False),
         fill(5, jan + 4 * day, 10800, None, None, status="skipped")]
    now = jan + 5 * day
    s = fuel.summarise(f, now)
    check("this month sums every logged fill-up, part or full, and not the skipped",
          s["month"]["month"] == "2026-10" and abs(s["month"]["spend"] - 45.5) < 0.001
          and s["month"]["fills"] == 2)
    check("months come newest first", [m["month"] for m in s["months"]][0] == "2026-10")
    check("two measured intervals", len(s["intervals"]) == 2)
    check("the average is distance-weighted: 620 mi on 20.5 gal",
          abs(s["average_mpg"] - 620 / 20.5) < 0.1)
    check("cost per mile overall = $73 / 620 mi", abs(s["cost_per_km"] * 1.609344 - 73 / 620) < 0.0005)
    check("last is the newer interval", s["last"]["to_id"] == 3)
    check("nothing logged: nothing invented",
          fuel.summarise([], now)["average_mpg"] is None and fuel.summarise([], now)["last"] is None)

    print("\n  the log on disk")
    scratch = tempfile.mkdtemp(prefix="omacar-fuel-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import importlib
    import garage
    import records
    importlib.reload(garage)
    importlib.reload(records)
    importlib.reload(fuel)
    try:
        check("nothing logged yet reads as none",
              fuel.read_public()["source"] == "none" and fuel.read_public()["present"] is False)

        # A trace of the car's own samples, so the car estimate has something to read.
        B = time.time() - 3 * 3600
        os.makedirs(os.path.dirname(records.DB), exist_ok=True)
        tdb = sqlite3.connect(records.DB)
        tdb.execute("CREATE TABLE samples (t REAL PRIMARY KEY, rpm REAL, speed REAL, load REAL,"
                    " throttle REAL, coolant REAL, intake REAL, maf REAL, stft REAL, ltft REAL,"
                    " timing REAL, lphk REAL, eff REAL, soc REAL)")
        for k in range(2000):
            tdb.execute("INSERT INTO samples (t, speed, lphk) VALUES (?,?,?)",
                        (B + k, 72.0, 6.0))
        tdb.commit()
        tdb.close()

        ev = {"at": B + 600, "level_before": 22.0, "level_after": 90.0, "odo_km": 50_000.0,
              "est_litres": 27.2}
        first = fuel.record_detection(ev)
        check("a detection is stored as a question", first is not None
              and (fuel.read_public()["pending"] or {}).get("id") == first)
        check("the same stop again is not stored twice",
              fuel.record_detection(dict(ev, at=ev["at"] + 120)) is None)
        pub = fuel.read_public()
        check("pending: no quantity, and says so", pub["pending"]["litres"] is None
              and pub["pending"]["status"] == "pending")
        check("a source of real, never sim", pub["source"] == "real" and pub["simulated"] is False)

        for bad in ((0.1, 1.0), (500, 1.0), (10, -3), (float("nan"), 1)):
            try:
                fuel.answer(first, bad[0], bad[1])
                check(f"answer {bad} is refused", False)
            except ValueError:
                check(f"answer {bad} is refused", True)
        fuel.answer(first, 9.9, 36.5, True)
        check("answered: logged with litres and cost",
              fuel.read_public()["pending"] is None
              and fuel.read_public()["fills"][0]["litres"] == 9.9)
        try:
            fuel.answer(first, 9.9, 36.5)
            check("an answered question cannot be answered again", False)
        except ValueError:
            check("an answered question cannot be answered again", True)

        ev2 = {"at": B + 1700, "level_before": 20.0, "level_after": 92.0, "odo_km": 50_500.0}
        second = fuel.record_detection(ev2)
        row2 = [f for f in fuel.all_fills() if f["id"] == second][0]
        check("the car's own integral since the last fill-up is stored with it "
              "(1100 s at 72 km/h = 22 km, 1.32 L)",
              abs(row2["car_km"] - 22.0) < 0.1 and abs(row2["car_litres"] - 1.32) < 0.01)
        fuel.skip(second)
        check("skip leaves it in the log as skipped, with no quantity",
              [(f["status"], f["litres"]) for f in fuel.all_fills()] == [("logged", 9.9), ("skipped", None)])
        manual = fuel.add(9.0, 33.0, full=False, at=B + 1800, odo_km=50_550.0)
        check("a manual fill-up is marked manual and logged",
              fuel.all_fills()[-1]["manual"] is True and fuel.all_fills()[-1]["status"] == "logged")
        fuel.remove(manual)
        check("remove deletes it", len(fuel.all_fills()) == 2)

        km, l = fuel._car_since(B, B + 600)
        check("the sample stream integrates: 600 s at 72 km/h, 6 L/100 = 12 km, 0.72 L",
              abs(km - 12.0) < 0.1 and abs(l - 0.72) < 0.01)
        check("no previous fill-up: no estimate", fuel._car_since(None, B) == (0.0, 0.0))

        print("\n  the simulator")
        n = fuel.sim(pending=True)
        pub = fuel.read_public()
        check("real entries win: the simulator's rows are not mixed in",
              pub["source"] == "real" and all(not f["simulated"] for f in pub["fills"]))
        for f in fuel.all_fills():
            if not f["simulated"]:
                fuel.remove(f["id"])
        pub = fuel.read_public()
        check("with only simulated rows the source is sim, and the flag is set",
              pub["source"] == "sim" and pub["simulated"] is True)
        check("ten fill-ups and one waiting question", n == 11 and pub["pending"] is not None
              and len([f for f in pub["fills"] if f["status"] != "pending"]) == 10)
        check("all of them are marked simulated", all(f["simulated"] for f in pub["fills"]))
        check("the simulator yields measured intervals and one dropped for the skipped fill-up",
              len(pub["intervals"]) == 6 and len(pub["dropped"]) == 1)
        mpgs = [i["mpg"] for i in pub["intervals"]]
        check("believable economy for a CR-Z (28-42 mpg)", all(28 <= m <= 42 for m in mpgs))
        check("the car's own estimate flatters the pump (positive error)",
              all(i["car_error_pct"] is not None and i["car_error_pct"] > 0 for i in pub["intervals"]))
        check("prices are plausible per gallon", all(2.5 < i["cost"] / (i["litres"] / G) < 5.5
                                                      for i in pub["intervals"]))
        sim_ids = [f["id"] for f in pub["fills"]]
        fuel.sim()
        check("running the simulator twice does not pile up",
              len([f for f in fuel.all_fills() if f["simulated"]]) == 10 and sim_ids != [])
        check("--clear removes only simulated rows", fuel.clear_sim() == 10 and fuel.all_fills() == [])
        check("take_reading: a hand-off is not a reading",
              fuel.take_reading({"connected": True, "handover": True,
                                 "values": {"FUEL_LEVEL": 50}, "t": 5}, None) is None)
        rd = fuel.take_reading({"connected": True, "t": 5, "odometer_km": 7.0,
                                "values": {"FUEL_LEVEL": 50, "SPEED": 12}}, None)
        check("take_reading reads level, speed, odometer",
              rd == {"t": 5.0, "fuel": 50.0, "speed": 12.0, "odo": 7.0})
        check("... and never the same instant twice",
              fuel.take_reading({"connected": True, "t": 5, "values": {"FUEL_LEVEL": 50}}, 5.0) is None)
        check("a car that is not connected gives no reading",
              fuel.take_reading({"connected": False, "values": {}}, None) is None)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if fails:
        print(f"  {fails} FAILED")
        return 1
    print("  fuel: all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
