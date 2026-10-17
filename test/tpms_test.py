#!/usr/bin/env python3
"""Tyre pressures (lib/tpms.py): rtl_433's lines in, four honest wheels out.

Field names are from merbanan/rtl_433 src/devices/tpms_*.c (cited in tpms.py).
The fixtures below are shaped like `rtl_433 -F json -M time:unix -M level`
output; none was recorded off the owner's sensors (they have not been heard
yet, doc/ROADMAP.md), and the test says so by using made-up ids.
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


def near(a, b, tol=0.05):
    return a is not None and abs(a - b) <= tol


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-tpms-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import tpms
    P = tpms.PSI
    try:
        print("\n  parsing the decoders' field variants")
        L = lambda **d: json.dumps(d)  # noqa: E731
        jansite = tpms.parse_line(L(time=1760000000, model="Jansite", type="TPMS", id="a1b2c3",
                                    flags=1, pressure_kPa=240.0, temperature_C=21.0, code="deadbe",
                                    mic="CRC", rssi=-9.5, snr=20.1), 5)
        check("Jansite: kPa, C, rssi, time:unix", jansite and jansite["kpa"] == 240.0 and
              jansite["temp_c"] == 21.0 and jansite["rssi"] == -9.5 and jansite["at"] == 1760000000)
        toyota = tpms.parse_line(L(model="Toyota", type="TPMS", id="0001ABCD", status=128,
                                   pressure_PSI=35.5, temperature_C=19, mic="CRC"), 77)
        check("Toyota: pressure_PSI becomes kPa, the id is lower-cased", toyota and
              near(toyota["kpa"], 35.5 * P, 0.01) and toyota["id"] == "0001abcd")
        check("no 'time' means 'now'", toyota["at"] == 77)
        pmv = tpms.parse_line(L(model="PMV-107J", type="TPMS", id="55aa55", status=0, battery_ok=0,
                                pressure_kPa=250.2, temperature_C=22.5), 1)
        check("PMV-107J: battery_ok 0 is low", pmv["battery_ok"] is False and tpms.battery_state(pmv) == "low")
        check("battery_ok 1 is ok", tpms.battery_state(dict(pmv, battery_ok=True)) == "ok")
        schr = tpms.parse_line(L(model="Schrader", type="TPMS", id="9f8e7d", pressure_kPa=220,
                                 temperature_F=77.0), 1)
        check("Schrader: temperature_F is converted", near(schr["temp_c"], 25.0, 0.01))
        air = tpms.parse_line(L(model="AirPuxem", type="TPMS", id="12", pressure_kPa=230, temperature_C=20,
                                battery_V=2.1), 1)
        check("battery_V under 2.4 V is low, over is ok", tpms.battery_state(air) == "low" and
              tpms.battery_state(dict(air, battery_v=2.9)) == "ok")
        check("no battery field says None, not 'ok'", tpms.battery_state(dict(jansite, battery_ok=None)) is None)
        trk = tpms.parse_line(L(model="Truck", type="TPMS", id="77", wheel=3, pressure_kPa=700,
                                temperature_C=30, pressure_alert=1, battery_ok=1), 1)
        check("an integer-ish id is kept as text, the sensor's alarm bit is kept",
              trk["id"] == "77" and trk["alert"] is True)
        check("an integer id (a %u decoder) is text", tpms.parse_line(L(model="X", type="TPMS", id=4242,
              pressure_kPa=230), 1)["id"] == "4242")
        check("ISO time is read", tpms.parse_line(L(time="2026-10-17 08:00:00", model="J", type="TPMS",
              id="a", pressure_kPa=230), 1)["at"] > 1.7e9)

        print("\n  what is not a tyre, or not believable")
        check("a weather station on 433 MHz is ignored", tpms.parse_line(
            L(model="Acurite-Tower", id=1234, channel="A", temperature_C=12.1, humidity=55), 1) is None)
        check("rtl_433's own chatter is ignored", tpms.parse_line("Registered 213 out of 297 device decoding protocols", 1) is None)
        check("broken JSON is ignored", tpms.parse_line('{"model": "Toyota", "id"', 1) is None)
        check("an array is ignored", tpms.parse_line("[1,2]", 1) is None)
        check("no id is ignored", tpms.parse_line(L(type="TPMS", pressure_kPa=230), 1) is None)
        check("a TPMS line with no pressure is ignored (we never draw a zero)",
              tpms.parse_line(L(type="TPMS", id="a", temperature_C=20), 1) is None)
        check("a bit-flipped 4000 kPa is dropped", tpms.parse_line(L(type="TPMS", id="a", pressure_kPa=4000), 1) is None)
        check("an impossible temperature is dropped but the pressure is kept",
              tpms.parse_line(L(type="TPMS", id="a", pressure_kPa=230, temperature_C=999), 1)["temp_c"] is None)

        print("\n  a flex decoder (-X) prints raw integers; the config says what they mean")
        flex = {"model": "tymate-test", "id": "id",
                "pressure": {"key": "p", "scale": 1.6, "offset": 0, "unit": "kPa"},
                "temperature": {"key": "t", "scale": 1, "offset": -50}}
        f = tpms.parse_line(L(time=1760000100, model="tymate-test", count=1, id=90210, p=150, t=71), 1, flex)
        check("scaled pressure and offset temperature", f and near(f["kpa"], 240.0, 0.01) and f["temp_c"] == 21.0)
        check("another model goes through the normal path, not the flex spec",
              tpms.parse_line(L(model="other", id=1, p=150), 1, flex) is None)
        psi = dict(flex, pressure={"key": "p", "scale": 0.2, "unit": "psi"})
        check("a flex spec in psi", near(tpms.parse_line(L(model="tymate-test", id=1, p=175, t=70), 1, psi)["kpa"], 35 * P, 0.01))

        print("\n  temperature normalisation (ideal gas, absolute pressure)")
        check("at 20 C nothing changes", near(tpms.normalise_kpa(240.0, 20.0), 240.0, 0.001))
        hot = tpms.normalise_kpa(255.0, 40.0)
        check("a 40 C reading of 255 kPa is a lower cold number", 225 < hot < 240)
        check("the ratio is on ABSOLUTE pressure", near(hot, (255.0 + 101.325) * 293.15 / 313.15 - 101.325, 0.001))
        check("cold tyres read up: 230 kPa at 0 C", tpms.normalise_kpa(230.0, 0.0) > 253)
        check("a round trip", near(tpms.normalise_kpa(tpms.normalise_kpa(240, 35), 20, to_c=35), 240, 0.001))
        check("no temperature: the reading as it is", tpms.normalise_kpa(240.0, None) == 240.0)
        check("no pressure: None", tpms.normalise_kpa(None, 20) is None)

        print("\n  thresholds against the placard (36 psi cold)")
        pl = 36 * P
        L_ = lambda psi: tpms.level_of(psi * P, pl)  # noqa: E731
        check("36 is ok", L_(36) == "ok")
        check("33 (-8 %) is ok", L_(33) == "ok")
        check("32 (-11 %) is warn", L_(32) == "warn")
        check("exactly -10 % is warn", tpms.level_of(0.9 * pl, pl) == "warn")
        check("29 (-19 %) is warn", L_(29) == "warn")
        check("28.8 (-20 %) is bad", L_(28.8) == "bad")
        check("20 is bad", L_(20) == "bad")
        check("46 (+28 %) is warn: too high", L_(46) == "warn")
        check("unknown is None", tpms.level_of(None, pl) is None and tpms.level_of(200, None) is None)
        cfg = tpms.load_config(os.path.join(scratch, "none.json"))
        check("the placard defaults to 36 psi front and rear",
              near(tpms.placard_kpa(cfg, "FL"), 36 * P, 0.01) and near(tpms.placard_kpa(cfg, "RR"), 36 * P, 0.01))
        cpath = os.path.join(scratch, "config", "omacar", "tpms.json")
        os.makedirs(os.path.dirname(cpath))
        with open(cpath, "w") as fh:
            json.dump({"placard_psi": {"front": 33, "rear": 32, "RR": 30}, "sensors": {"A1": "fl", "b2": "XX"}}, fh)
        c2 = tpms.load_config()
        check("the placard is the owner's: front, rear, and a per-wheel override",
              near(tpms.placard_kpa(c2, "FR"), 33 * P, 0.01) and near(tpms.placard_kpa(c2, "RL"), 32 * P, 0.01)
              and near(tpms.placard_kpa(c2, "RR"), 30 * P, 0.01))
        check("sensors: ids lower-cased, wheels upper-cased, a bad wheel dropped", c2["sensors"] == {"a1": "FL"})
        os.unlink(cpath)

        print("\n  rapid loss")
        T = 1_000_000.0
        flat = [(T - 600 + i * 30, 250.0) for i in range(20)]
        check("steady is not a loss", tpms.rapid_loss(flat, T) is None)
        drop = flat[:-2] + [(T - 20, 180.0), (T - 5, 170.0)]
        r = tpms.rapid_loss(drop, T)
        check("-32 % in ten minutes, twice in a row, is rapid", r and r["frac"] > 0.3)
        one = flat[:-1] + [(T - 5, 150.0)]
        check("ONE bad reading is not", tpms.rapid_loss(one, T) is None)
        slow = [(T - 600 + i * 30, 250.0 - i * 1.0) for i in range(20)]
        check("19 kPa over ten minutes (8 %) is not", tpms.rapid_loss(slow, T) is None)
        check("an old fall is not now", tpms.rapid_loss(drop, T + 1000) is None)
        check("too few readings is not", tpms.rapid_loss(drop[-2:], T) is None)

        print("\n  slow leak: robust slope over cold readings")
        day = tpms.DAY
        # Two sessions a day, a hot one in between that must NOT count.
        def series(slope_psi_day, days=14, noise=0.0, start=36.0, rng=None):
            out = []
            for d in range(days):
                t0 = T - (days - d) * day
                p = (start + slope_psi_day * d + (rng.uniform(-noise, noise) if rng else 0)) * P
                out += [(t0, p), (t0 + 600, p * 1.12), (t0 + 1200, p * 1.12)]
            return out
        import random
        rng = random.Random(4)
        s_leak = series(-0.30, noise=0.2, rng=rng)
        cold = tpms.cold_points(s_leak)
        check("only the first reading after a long park is cold", len(cold) == 13)
        check("none of the hot readings leaked in", all(abs(p - cold[0][1]) < 80 for _, p in cold))
        lt = tpms.leak_trend(cold)
        check("0.3 psi/day through noise is a leak", lt["leak"] and near(lt["slope_kpa_day"] / P, -0.30, 0.08))
        s_ok = series(-0.02, noise=0.3, rng=random.Random(9))
        check("normal seepage with noise is not", not tpms.leak_trend(tpms.cold_points(s_ok))["leak"])
        s_flat = series(0.0, noise=0.4, rng=random.Random(2))
        check("a noisy flat line is not", not tpms.leak_trend(tpms.cold_points(s_flat))["leak"])
        wild = list(cold)
        wild[6] = (wild[6][0], wild[6][1] + 120.0)
        check("one wild point does not break the slope (Theil-Sen)",
              tpms.leak_trend(wild)["leak"])
        wild2 = list(tpms.cold_points(series(0.0, rng=None)))
        wild2[5] = (wild2[5][0], wild2[5][1] - 200.0)
        check("...and one wild point does not make a leak out of a flat tyre",
              not tpms.leak_trend(wild2)["leak"])
        short = tpms.cold_points(series(-0.5, days=3))
        check("three days is not a verdict", tpms.leak_trend(short)["slope_kpa_day"] is None and not tpms.leak_trend(short)["leak"])
        # inflation resets the window
        up = [(t, p) for t, p in tpms.cold_points(series(-0.3, days=10))]
        up = up[:5] + [(t, 38.0 * P) for t, p in up[5:]]
        check("topping up the tyre starts the slope over", tpms.after_inflation(up) == up[5:])
        check("...so the old fall does not count against the new air", not tpms.leak_trend(up)["leak"])

        print("\n  the tracker: only the owner's sensors, ever")
        cfg = tpms.load_config()
        sensors = {"aa01": "FL", "aa02": "FR", "aa03": "RL", "aa04": "RR"}
        tr = tpms.Tracker(cfg, sensors=sensors)
        t0 = T
        mk = lambda i, kpa, t, **k: tpms.parse_line(  # noqa: E731
            L(time=t, model="Jansite", type="TPMS", id=i, pressure_kPa=kpa, temperature_C=20.0,
              battery_ok=1, rssi=-10.0, **k), t)
        got = [tr.ingest(mk("aa01", 245, t0)), tr.ingest(mk("zz99", 250, t0 + 1)), tr.ingest(mk("zz98", 100, t0 + 2))]
        check("an assigned id lands on its wheel; strangers land nowhere", got == ["FL", None, None])
        snap = tr.snapshot(t0 + 3)
        names = json.dumps(snap)
        check("strangers never appear in the wheels' picture", "zz99" not in names and "zz98" not in names)
        check("an unheard wheel is None, never zero",
              [w["kpa"] for w in snap["wheels"] if w["wheel"] == "FR"] == [None])
        fl = snap["wheels"][0]
        check("the heard wheel carries pressure, level, battery, rssi",
              fl["kpa"] == 245 and fl["level"] == "ok" and fl["battery"] == "ok" and fl["rssi"] == -10.0)
        hl = tr.heard_list(t0 + 3)
        check("the learn list has all three, with RSSI", {h["id"] for h in hl} == {"aa01", "zz99", "zz98"})
        check("...and marks which are assigned", {h["id"]: h["wheel"] for h in hl} == {"aa01": "FL", "zz99": None, "zz98": None})
        tr.ingest(mk("zz99", 250, t0 + 4000))
        check("the learn list forgets a stranger after half an hour",
              "zz98" not in {h["id"] for h in tr.heard_list(t0 + 4000)})
        by_model = tpms.Tracker(cfg, sensors={"jansite:bb01": "RR"})
        check("an assignment can be 'model:id' (ids are only unique within a decoder)",
              by_model.ingest(mk("bb01", 240, t0)) == "RR" and
              by_model.ingest(tpms.parse_line(L(time=t0, model="Toyota", type="TPMS", id="bb01", pressure_kPa=240), t0)) is None)

        print("\n  a wheel that fell under the placard")
        low = tpms.Tracker(cfg, sensors=sensors)
        low.ingest(mk("aa03", 36 * P * 0.85, t0))
        low.ingest(mk("aa04", 36 * P * 0.70, t0))
        w = {x["wheel"]: x for x in low.snapshot(t0)["wheels"]}
        check("-15 % warns, -30 % is bad", w["RL"]["level"] == "warn" and w["RR"]["level"] == "bad")
        hot_t = tpms.Tracker(cfg, sensors=sensors)
        hot_t.ingest(tpms.parse_line(L(time=t0, model="J", type="TPMS", id="aa01", pressure_kPa=265.0,
                                       temperature_C=45.0), t0))
        h1 = hot_t.snapshot(t0)["wheels"][0]
        check("a warm tyre at 38 psi is judged cold (normalised), not flagged high",
              h1["level"] == "ok" and h1["p20_kpa"] < 245)

        print("\n  the tracker keeps a month, and finds the leak")
        sim_now = 1_800_000_000.0
        h = tpms.sim_history(sim_now, random.Random(3))
        tk = tpms.Tracker(cfg, h, source="sim", sensors=tpms.SIM_SENSORS)
        s = tk.snapshot(sim_now)
        wl = {x["wheel"]: x for x in s["wheels"]}
        check("the sim's rear left is a slow leak", wl["RL"]["leak"] and not wl["FL"]["leak"]
              and not wl["FR"]["leak"] and not wl["RR"]["leak"])
        check("and the alert says so, for that wheel only",
              [(a["wheel"], a["kind"]) for a in s["alerts"]] == [("RL", "leak")])
        check("the trend is cold points only, and a sparkline's worth", len(wl["RL"]["trend"]) > 10)

        print("\n  notifications: once per wheel per day")
        outbox = []
        class Fake:
            @staticmethod
            def send(kind, title, body, urgency, key=None, now=None):
                outbox.append((kind, key, urgency, body))
        tk.source = "rtl_433"
        tpms.send_due(tk, sim_now, Fake)
        check("a leak is one warn message with a per-wheel, per-day key",
              len(outbox) == 1 and outbox[0][0] == "tyre-leak" and outbox[0][2] == "warn"
              and outbox[0][1].startswith("tyre-leak-RL-"))
        check("it names the wheel and the loss in psi", "psi a day" in outbox[0][3])
        tpms.send_due(tk, sim_now + 3600, Fake)
        check("an hour later the same day: no second message", len(outbox) == 1)
        tpms.send_due(tk, sim_now + 2 * 86400, Fake)
        check("two days later: again", len(outbox) == 2)
        check("history is written for next time", os.path.exists(tpms.HISTORY))
        tk.source = "sim"
        n = len(outbox)
        tk.notified.clear()
        tpms.send_due(tk, sim_now, Fake)
        check("a simulator never writes the owner's outbox", len(outbox) == n)

        print("\n  rapid loss raises a critical, per wheel")
        rp = tpms.Tracker(cfg, sensors=sensors)
        for i in range(10):
            rp.ingest(mk("aa02", 245.0, t0 + i * 30))
        rp.ingest(mk("aa02", 150.0, t0 + 320))
        rp.ingest(mk("aa02", 130.0, t0 + 335))
        due = rp.due_notifications(t0 + 340)
        check("one critical, for the front right", len(due) == 1 and due[0][3] == "critical" and "Front right" in due[0][1])
        sn = rp.snapshot(t0 + 340)
        check("the alert is 'rapid' and bad", sn["alerts"][0]["kind"] == "rapid" and sn["alerts"][0]["tone"] == "bad")

        print("\n  the file the app reads")
        tpms.write_state(tk, sim_now, radio="ok")
        pub = tpms.read_public(sim_now + 5)
        check("present, with four wheels", pub["present"] and len(pub["wheels"]) == 4)
        check("a wheel's age is reported and it is not 'fresh' when unheard",
              all(w["age"] is None or w["age"] >= 0 for w in pub["wheels"]))
        late = tpms.read_public(sim_now + 3600)
        check("the service stopped writing: no alerts survive", late["alerts"] == [] and late["running"] is False)
        check("...but the last readings stay, with their age",
              all(w["age"] is None or w["age"] > 3000 for w in late["wheels"]))
        hr = tpms.read_heard(sim_now + 1)
        check("the learn route has a list", hr["present"] and isinstance(hr["heard"], list))

        print("\n  assigning from a shell or the app")
        a = tpms.assign("A1B2C3", "rl")
        check("assign lower-cases the id and upper-cases the wheel", a == {"assigned": "a1b2c3", "wheel": "RL"})
        tpms.assign("ffee01", "RL")
        check("one wheel, one sensor: the old one is released",
              tpms.load_config()["sensors"] == {"ffee01": "RL"})
        tpms.assign("ffee01", "FL")
        check("one sensor, one wheel: it moves", tpms.load_config()["sensors"] == {"ffee01": "FL"})
        tpms.assign("ffee02", "FR")
        tpms.unassign("fl")
        check("unassign frees a wheel and leaves the others", tpms.load_config()["sensors"] == {"ffee02": "FR"})
        for bad in (("x y", "FL"), ("ab", "XX"), ("a" * 41, "FL")):
            try:
                tpms.assign(*bad)
                check(f"refuses {bad}", False)
            except ValueError:
                check(f"refuses {bad}", True)
        with open(tpms.CONFIG) as fh:
            raw = json.load(fh)
        raw["placard_psi"] = {"front": 33, "rear": 33}
        with open(tpms.CONFIG, "w") as fh:
            json.dump(raw, fh)
        tpms.assign("ffee03", "RR")
        check("assigning keeps the owner's other settings", tpms.load_config()["placard_psi"]["front"] == 33)

        print("\n  the command and the radio's complaints")
        check("default command", tpms.build_command(tpms.load_config()) ==
              ["rtl_433", "-f", "433.92M", "-F", "json", "-M", "time:unix", "-M", "level"])
        check("a configured command replaces it", tpms.build_command({"command": "rtl_433 -R 1 -F json", "frequency": "x"}) ==
              ["rtl_433", "-R", "1", "-F", "json"])
        check("no dongle", "RTL-SDR" in tpms.radio_note(["No supported devices found."]))
        check("busy dongle", "could not be opened" in tpms.radio_note(["usb_claim_interface error -6"]))

        print("\n  the simulator speaks rtl_433")
        lines = []
        import itertools
        for ln in itertools.islice(tpms.sim_lines(5, None, pace=0), 12):
            lines.append(ln)
        pk = [tpms.parse_line(x, 1) for x in lines]
        check("every simulated line parses as a tyre packet", all(pk))
        check("six distinct sensors: four assigned, two strangers",
              len({p["id"] for p in pk}) == 6 and len({p["id"] for p in pk} & set(tpms.SIM_SENSORS)) == 4)
        check("the model says it is simulated", all(p["model"] == tpms.SIM_MODEL for p in pk))
        # Through the whole pipe: the sim's own lines to a tracker, no service.
        pipe = tpms.Tracker(cfg, sensors=tpms.SIM_SENSORS, source="sim")
        for p in pk:
            pipe.ingest(p)
        check("through a Tracker, the strangers stay out of the car",
              sum(1 for w in pipe.snapshot(pk[0]["at"])["wheels"] if w["kpa"] is not None) == 4
              and len(pipe.heard) == 6)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'every tyre check holds' if not fails else str(fails) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
