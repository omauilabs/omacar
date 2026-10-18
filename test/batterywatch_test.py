#!/usr/bin/env python3
"""The parked 12 V projection (lib/batterywatch.py): honest ranges, never one reading.

Every case builds a record from a decay this file chooses and checks what the
pure functions say about it -- no adapter, no clock but the one passed in.
  * a healthy battery is not alarmed, and says what it cannot see
  * a parasitic drain is projected to the floor, the true time inside the range
  * a cold night's step is not mistaken for a drain
  * too few readings (one, two, too close together, only settling hours) is
    "not enough readings", never a number
  * across several parks the rate is found; two parks, or short ones, are not enough
  * the alert fires once per park per level, critical under 11.9 V
"""
import json
import math
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    print(f"    {'ok' if cond else 'FAIL'}  {msg}")
    fails += 0 if cond else 1


H, D = 3600.0, 86400.0
NOW = 1_800_000_000.0


def q(v):
    return round(round(v / 0.1) * 0.1, 2)


def park_rows(t0, hours, fn, every_min=10, quant=True, start_v=13.0, end=None, pct0=90):
    """An off row, then an awake reading every `every_min`; fn(hours_since) -> volts."""
    rows = [{"t": t0, "kind": "off", "v": start_v, "rest": False, "pct": pct0, "src": "adapter"}]
    t = t0 + every_min * 60
    while t <= t0 + hours * H:
        v = fn((t - t0) / H)
        rows.append({"t": t, "kind": "parked", "v": q(v) if quant else round(v, 3),
                     "rest": True, "pct": int(pct0 - 0.5 * (t - t0) / H), "src": "adapter"})
        t += every_min * 60
    if end is not None:
        rows.append({"t": t0 + hours * H, "kind": "on", "v": end, "rest": True, "pct": 80, "src": "adapter"})
    return rows


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-bwatch-")
    os.environ["XDG_STATE_HOME"] = scratch
    os.environ["XDG_CONFIG_HOME"] = scratch
    import batterywatch as bw
    cfg = bw.config()
    os.makedirs(os.path.dirname(bw.CONFIG), exist_ok=True)
    try:
        # ---- config ------------------------------------------------------------
        check("the floor is the app's 12.2 V by default", cfg["floor_v"] == 12.2)
        with open(bw.CONFIG, "w") as f:
            json.dump({"floor_v": 11.0, "critical_v": 11.9, "sample_min": 1}, f)
        c2 = bw.config()
        check("an accepted lower floor is honoured, but never under the critical level",
              c2["floor_v"] >= c2["critical_v"] + 0.1 and c2["critical_v"] == 11.9)
        with open(bw.CONFIG, "w") as f:
            json.dump({"floor_v": 12.0}, f)
        check("a floor of 12.0 V is taken as asked", bw.config()["floor_v"] == 12.0)
        os.unlink(bw.CONFIG)

        # ---- a healthy battery -------------------------------------------------
        t0 = NOW - 12 * H
        rows = park_rows(t0, 12, lambda h: 12.65 + 0.2 * math.exp(-h / 1.4) - 0.01 * h / 24)
        p = bw.summary(rows, cfg, NOW, 70)["projection"]
        check("healthy: steady, with only a lower bound on the time to the floor",
              p["state"] == "steady" and p["eta_h"]["mid"] is None and p["eta_h"]["lo"] > 12)
        check("healthy: says what a few hours cannot show", "needs days to show" in p["text"] or p["state"] == "ok")
        s = bw.summary(rows, cfg, NOW, 70)
        check("healthy: Home's tone is ok", s["headline"]["tone"] == "ok")
        check("healthy: no alert", bw.alert_for(rows, bw.parks(rows), p, cfg, NOW) is None)

        # ---- a parasitic drain -------------------------------------------------
        rate = 0.40       # V a day: reaches 12.2 V in about 7 h more
        t0 = NOW - 20 * H
        rows = park_rows(t0, 20, lambda h: 12.62 + 0.2 * math.exp(-h / 1.4) - rate * h / 24)
        s = bw.summary(rows, cfg, NOW, 70)
        p = s["projection"]
        level = p["level"]
        true_h = (level - 12.2) / rate * 24
        check(f"drain: projected (state {p['state']})", p["state"] == "ok")
        e = p["eta_h"]
        check(f"drain: the true {true_h:.0f} h sits inside the range {e['lo']:.0f}..{e['hi']}",
              e["lo"] * 0.8 <= true_h <= ((e["hi"] or 1e9) * 1.25))
        check("drain: the text says how many readings it came from", "readings over" in p["text"])
        check("drain: Home warns", s["headline"]["tone"] == "warn")
        a = bw.alert_for(rows, bw.parks(rows), p, cfg, NOW)
        check("drain: an alert is raised at warn level, for this park", a and a[0] == "warn" and a[1] == t0)

        # ---- a cold night's step ----------------------------------------------
        rows = park_rows(NOW - 14 * H, 14, lambda h: 12.62 + 0.2 * math.exp(-h / 1.4) - (0.25 if h > 7 else 0.0))
        p = bw.summary(rows, cfg, NOW, 70)["projection"]
        check("cold step: not read as a drain (no 'falling about')", "Falling about" not in p["text"])
        check("cold step: the step is named", any("stepped down" in n for n in p["notes"]))

        # ---- too few readings -------------------------------------------------
        one = [{"t": NOW - H, "kind": "off", "v": 12.9, "rest": False, "pct": 80, "src": "adapter"}]
        s = bw.summary(one, cfg, NOW, 80)
        check("one reading: not enough readings, no eta", s["projection"]["state"] == "not-enough"
              and "eta_h" not in s["projection"])
        two = park_rows(NOW - 30 * H, 30, lambda h: 12.6, every_min=60 * 12)
        two = [r for r in two if r["kind"] != "parked"][:1] + two[-2:]
        check("two or three spread readings are still not enough to project",
              bw.summary(two[:2], cfg, NOW, 80)["projection"]["state"] == "not-enough")
        short = park_rows(NOW - 5 * H, 5, lambda h: 12.6 - 0.5 * h)
        check("five hours of readings: not enough (needs 6 h past the settling)",
              bw.summary(short, cfg, NOW, 80)["projection"]["state"] == "not-enough")
        settling = park_rows(NOW - 2 * H, 2, lambda h: 13.0 - 0.6 * h, every_min=5)
        sp = bw.summary(settling, cfg, NOW, 80)["projection"]
        check("readings only from the settling hours are never projected", sp["state"] == "not-enough")
        check("the not-enough text says what it needs", "needs 3" in sp["text"] or "at least" in sp["text"])
        check("empty record: nothing present", bw.summary([], cfg, NOW, 80).get("present") is False)

        # ---- parks and the pair ------------------------------------------------
        rows = []
        for ago, length, vend in ((20, 1.0, 12.58), (14, 3.0, 12.45), (9, 2.0, 12.51), (4, 5.0, 12.3)):
            rows += [{"t": NOW - ago * D, "kind": "off", "v": 13.0, "rest": False, "pct": 90, "src": "adapter"},
                     {"t": NOW - ago * D + length * D, "kind": "on", "v": vend, "rest": True, "pct": 85, "src": "adapter"}]
        ps = bw.parks(rows)
        check("four slept-through parks are four closed parks", len(ps) == 4 and not any(p["open"] for p in ps))
        check("a park keeps its start and end", ps[3]["off_v"] == 13.0 and ps[3]["on_v"] == 12.3)
        s = bw.summary(rows, cfg, NOW, 85)
        p = s["projection"]
        check("across parks: a rate is found", p["basis"] == "past parks" and p["state"] in ("ok", "steady"))
        check("across parks: the slope is about 0.05 V a day",
              p["state"] != "ok" or 0.02 < -p["slope_per_day"] < 0.1)
        check("across parks: said in parks, not readings", "parks" in p["text"])
        check("the pair is not a projection: two parks are not enough",
              bw.summary(rows[:4], cfg, NOW, 85)["projection"]["state"] == "not-enough")
        shortp = []
        for i in range(5):
            shortp += [{"t": NOW - (10 - i) * D, "kind": "off", "v": 13.0, "rest": False, "pct": 90, "src": "adapter"},
                       {"t": NOW - (10 - i) * D + 4 * H, "kind": "on", "v": 12.6, "rest": True, "pct": 88, "src": "adapter"}]
        check("parks shorter than 12 h do not count",
              bw.summary(shortp, cfg, NOW, 85)["projection"]["state"] == "not-enough")
        running = [dict(r) for r in rows[:2]]
        running[1]["rest"] = False
        check("a running voltage at ignition-on is never a resting value",
              bw.parks(running)[0]["on_v"] is None and bw.latest_rest(running) is None)
        implicit = [{"t": NOW - H, "kind": "parked", "v": 12.5, "rest": True, "pct": 60, "src": "adapter"}]
        check("a loop that started while parked opens an implicit park",
              bw.parks(implicit)[0]["implicit"] and bw.parks(implicit)[0]["open"])
        lost = [{"t": 1, "kind": "off", "v": 13, "rest": False, "src": "x"},
                {"t": 2 * D, "kind": "off", "v": 13, "rest": False, "src": "x"}]
        check("an off with no on before the next off closes the first", [p["open"] for p in bw.parks(lost)] == [False, True])

        # ---- the headline and the alerts -----------------------------------------
        low = [{"t": NOW - 3 * D, "kind": "off", "v": 13.0, "rest": False, "pct": 90, "src": "adapter"},
               {"t": NOW - 60, "kind": "on", "v": 12.1, "rest": True, "pct": 80, "src": "adapter"}]
        s = bw.summary(low, cfg, NOW, 80)
        check("under the floor: warn, and it says how long it stood",
              s["headline"]["tone"] == "warn" and "12.1 V" in s["headline"]["text"] and "3 days" in s["headline"]["text"])
        a = bw.alert_for(low, bw.parks(low), s["projection"], cfg, NOW)
        check("under the floor: a warn alert", a and a[0] == "warn")
        crit = [dict(r) for r in low]
        crit[1]["v"] = 11.8
        a = bw.alert_for(crit, bw.parks(crit), bw.summary(crit, cfg, NOW, 80)["projection"], cfg, NOW)
        check("under 11.9 V: critical", a and a[0] == "critical")
        check("critical on Home", bw.summary(crit, cfg, NOW, 80)["headline"]["tone"] == "bad")
        noisy = [dict(r) for r in low]
        noisy[1]["rest"] = False
        check("a running voltage raises no alert", bw.alert_for(noisy, bw.parks(noisy), {}, cfg, NOW) is None)
        offs = dict(cfg, offset_v=-0.3)
        okrow = [dict(r) for r in low]
        okrow[1]["v"] = 12.4
        check("a meter calibration offset moves what is judged",
              bw.alert_for(okrow, bw.parks(okrow), {}, cfg, NOW) is None
              and bw.alert_for(okrow, bw.parks(okrow), {}, offs, NOW)[0] == "warn")

        # once per park per level, through the real outbox
        import notify
        st = {}
        bw.tell(low, cfg, NOW, st)
        bw.tell(low, cfg, NOW + 3600, st)            # well past notify's 10 min dedupe
        sent = notify.since(0)
        check("a warn is sent once per park, even an hour later", len(sent) == 1 and sent[0]["urgency"] == "warn"
              and sent[0]["kind"] == "battery-low")
        bw.tell(crit, cfg, NOW + 7200, st)
        sent = notify.since(0)
        check("it escalates once to critical", len(sent) == 2 and sent[1]["urgency"] == "critical")
        bw.tell(crit, cfg, NOW + 9000, st)
        check("and not again", len(notify.since(0)) == 2)

        # ---- the tablet --------------------------------------------------------
        rows = park_rows(NOW - 10 * H, 10, lambda h: 12.6, every_min=60, pct0=80)
        t = bw.summary(rows, cfg, NOW, 70)["tablet"]
        check("tablet: a drain and a runtime from several readings", t["state"] == "ok"
              and 0.2 < t["drain_pct_per_h"] < 1.0 and t["hours_left"]["mid"] > 10)
        pk = bw.parks([{"t": NOW - 3 * D, "kind": "off", "v": 13, "rest": False, "pct": 92, "src": "x"},
                       {"t": NOW - 60, "kind": "on", "v": 12.5, "rest": True, "pct": 84, "src": "x"}])[0]
        tc = bw.tablet(pk, 84, NOW)
        check("tablet: after a park it says what the park cost, with no runtime (it is charging)",
              tc["state"] == "ok" and tc["hours_left"] is None and tc["text"].startswith("Lost 8 %"))
        t1 = bw.tablet({"open": True, "pct": [(NOW - 600, 80)]}, 79, NOW)
        check("tablet: ten minutes of readings give no runtime", t1["state"] == "not-enough" and t1["hours_left"] is None)
        check("tablet: no battery to read says so", bw.tablet(None, None, NOW)["state"] == "none")

        # ---- pure helpers ------------------------------------------------------
        check("a Theil-Sen slope ignores one wild reading",
              abs(bw.theil_sen([(i * 3600.0, 12.6 - 0.001 * i + (1.5 if i == 5 else 0)) for i in range(12)])["slope"]
                  + 0.001 / 3600.0) < 2e-7)
        check("spans are said the way a person would",
              [bw.fmt_span(x) for x in (0.5, 5, 40, 24 * 3.2, 24 * 90)] ==
              ["about an hour", "about 5 hours", "about 1.5 days", "about 3 days", "more than two months"])

        # ---- the loop, against a fake charger, car, adapter and clock ------------
        import ignition

        class Stop(Exception):
            pass

        def drive(script, volts, live=(13.8, 5.0)):
            """script: [(clock, powered, answering, pct)], one per loop tick."""
            for f in (bw.SAMPLES,):
                if os.path.exists(f):
                    os.unlink(f)
            tick = {"i": 0}
            clock = {"t": script[0][0]}

            class FakeTime:
                @staticmethod
                def time():
                    return clock["t"]

                @staticmethod
                def sleep(_s):
                    tick["i"] += 1
                    if tick["i"] >= len(script):
                        raise Stop()
                    clock["t"] = script[tick["i"]][0]

            def sup(root=None):
                _, pw, _, pct = script[tick["i"]]
                return {"powered": pw, "battery": pct, "seen": []}

            def car(path=None, now=None):
                _, _, ans, _ = script[tick["i"]]
                return {"answering": ans, "speed": 0.0 if ans else None, "age": 1.0 if ans else None}

            reads = list(volts)
            saved = (ignition.supplies, ignition.car, bw.adapter_volts, bw.live_volts, bw.time)
            ignition.supplies, ignition.car = sup, car
            bw.adapter_volts = lambda timeout=4.0: reads.pop(0) if reads else None
            bw.live_volts = lambda now=None: live
            bw.time = FakeTime
            try:
                bw.run(tick=0)
            except Stop:
                pass
            finally:
                ignition.supplies, ignition.car, bw.adapter_volts, bw.live_volts, bw.time = saved
            return bw.load()

        T = 1_900_000_000.0
        # Driving; the charger goes at +10 and the car goes quiet at +12; the first
        # reading lands at +17 (4 s of quiet, then it is taken); the tablet then
        # sleeps three days; the charger returns and the car is not yet answering.
        script = [(T, True, True, 90), (T + 10, False, True, 90), (T + 12, False, False, 90),
                  (T + 14, False, False, 90), (T + 17, False, False, 90), (T + 18, False, False, 90),
                  (T + 3 * D, True, False, 85), (T + 3 * D + 2, True, False, 85)]
        rows = drive(script, [12.9, 12.3])
        check("loop: an off reading inside the 30 s, an on reading when the charger returns",
              [r["kind"] for r in rows] == ["off", "on"])
        check("loop: the off is the post-drive voltage, never a resting one",
              rows[0]["v"] == 12.9 and rows[0]["rest"] is False and rows[0]["src"] == "adapter")
        check("loop: the on, with the car not yet answering, is a resting voltage, with the tablet's %",
              rows[1]["v"] == 12.3 and rows[1]["rest"] is True and rows[1]["pct"] == 85)
        # The car still answering when the charger returns: running, not resting.
        script2 = script[:6] + [(T + 3 * D, True, True, 85), (T + 3 * D + 2, True, True, 85)]
        rows = drive(script2, [12.9, 13.9])
        check("loop: an on reading with the car already answering is marked running",
              len(rows) == 2 and rows[1]["rest"] is False)
        # Slept before the first reading could be taken: the daemon's last voltage, labelled.
        script3 = [(T, True, True, 90), (T + 10, False, True, 90), (T + 3 * D, True, False, 80),
                   (T + 3 * D + 2, True, False, 80)]
        rows = drive(script3, [12.4], live=(13.8, 5.0))
        check("loop: slept before the first reading, the park starts from the daemon's last value, labelled live",
              len(rows) >= 1 and rows[0]["kind"] == "off" and rows[0]["src"] == "live"
              and rows[0]["rest"] is False and rows[0]["t"] == T + 10)
        # A cranking dip: the charger gone for 5 s and back is not a park.
        script4 = [(T, True, True, 90), (T + 10, False, True, 90), (T + 15, True, True, 90), (T + 16, True, True, 90)]
        check("loop: a cranking dip records nothing", drive(script4, [12.0]) == [])
        # Awake and parked: a reading every sample_min minutes, and only then.
        script5 = [(T, True, True, 90), (T + 10, False, True, 90)] + \
                  [(T + 12 + 120 * i, False, False, 90 - i) for i in range(0, 20)]
        rows = drive(script5, [12.9] + [12.6] * 10)
        kinds = [r["kind"] for r in rows]
        check("loop: awake and parked, one reading per ten minutes (an off, then parked)",
              kinds[0] == "off" and set(kinds[1:]) == {"parked"} and 3 <= len(rows) <= 6)
        # The adapter busy: no reading, no row, and no crash.
        check("loop: a busy adapter writes nothing and does not stop the loop",
              drive([(T, True, True, 90), (T + 10, False, True, 90), (T + 15, False, False, 90),
                     (T + 20, False, False, 90), (T + 25, False, False, 90)], []) == [])

        # ---- the simulator and what the app reads -------------------------------
        check("nothing recorded: not present", bw.read_public().get("present") is False)
        for sc, want in (("healthy", "ok"), ("drain", "warn"), ("step", "ok"), ("low", "warn")):
            rows = bw.sim_rows(time.time(), sc)
            h = bw.summary(rows, cfg, time.time(), 70)["headline"]["tone"]
            check(f"sim '{sc}' reads as {want}", h == want)
        os.makedirs(bw.STATE_DIR, exist_ok=True)
        with open(bw.SIM, "w") as f:
            for r in bw.sim_rows(time.time(), "drain"):
                f.write(json.dumps(r) + "\n")
        pub = bw.read_public()
        check("the simulator says sim while its file is fresh", pub["source"] == "sim" and pub["present"])
        os.utime(bw.SIM, (time.time() - 600, time.time() - 600))
        check("and stops being shown when nothing is writing it", bw.read_public().get("present") is False)
        check("api never carries an error out", "error" not in bw.read_public())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
