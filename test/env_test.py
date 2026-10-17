#!/usr/bin/env python3
"""The environment nodes (lib/envnode.py): what is believed, what is derived,
what is never opened. No hardware, no serial port, no network.

THE ONE THAT MATTERS MOST: the OBD adapter's port is never opened, and a node
is only ever read from.
"""

import json
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
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


class FakeSerial:
    def __init__(self, lines):
        self.lines, self.closed, self.written = list(lines), False, []

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def write(self, b):
        self.written.append(b)

    def close(self):
        self.closed = True


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-env-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import envnode as e
    try:
        print("\n  lines")
        ok = e.parse_line(b'{"node":"cabin","fw":1,"t_c":22.4,"rh":41.2,"co2":812,"pm25":4.0,"voc":102}\n')
        check("a cabin line parses", ok and ok[0] == "cabin" and ok[2]["co2"] == 812 and ok[1] == 1)
        check("the outdoor example parses",
              e.parse_line('{"node":"outdoor","fw":1,"t_c":17.9,"rh":63.0,"hpa":1012.6,"pm25":6.1,"pm10":9.4}')[0] == "outdoor")
        for bad, why in [('hello', "text"), ('[1,2]', "an array"), ('{"node":"toaster","t_c":20}', "an unknown node"),
                         ('{"node":"cabin"}', "no readings"), ('{"t_c":20}', "no node"),
                         ('{"node":"cabin","t_c":', "a torn line"), ('x' * 600, "a very long line")]:
            check(f"rejects {why}", e.parse_line(bad) is None)
        r = e.parse_line('{"node":"cabin","t_c":22,"co2":99999,"rh":140,"pm25":-3,"voc":true,"nox":"1","extra":5}')
        check("out-of-range, wrong-type and unknown values are dropped one by one, the rest kept",
              r and r[2] == {"t_c": 22.0})
        check("NaN and infinity are dropped",
              e.parse_line('{"node":"outdoor","t_c":NaN,"hpa":Infinity,"rh":50}')[2] == {"rh": 50.0})
        check("a zero CO2 is a fault, not a reading", e.clean_value("co2", 0) is None)
        check("pressure outside 300..1100 is dropped", e.clean_value("hpa", 1500) is None and e.clean_value("hpa", 1012.6) == 1012.6)
        check("temperature -40..85 only", e.clean_value("t_c", -41) is None and e.clean_value("t_c", 86) is None and e.clean_value("t_c", -40) == -40.0)

        print("\n  what the numbers mean")
        check("dew point of 20 C at 50 % is 9.3 C", e.dew_point_c(20, 50) == 9.3)
        check("absolute humidity of 20 C at 50 % is 8.6 g/m3", e.abs_humidity(20, 50) == 8.6)
        check("no humidity, no dew point", e.dew_point_c(20, None) is None)
        for c, idx, key in [(0, 0, "good"), (9.0, 50, "good"), (9.1, 51, "moderate"), (35.4, 100, "moderate"),
                            (35.5, 101, "usg"), (55.4, 150, "usg"), (55.5, 151, "unhealthy"),
                            (125.4, 200, "unhealthy"), (125.5, 201, "very"), (225.4, 300, "very"),
                            (225.5, 301, "hazardous"), (325.4, 500, "hazardous"), (900, 500, "hazardous")]:
            a = e.aqi_pm25(c)
            check(f"PM2.5 {c} is AQI {idx} {key} (2024 breakpoints)", a["index"] == idx and a["key"] == key)
        check("9.04 truncates to 9.0 and stays Good, as EPA does", e.aqi_pm25(9.04)["key"] == "good")
        for ppm, key in [(500, "good"), (799, "good"), (800, "ok"), (1199, "ok"), (1200, "stuffy"),
                         (2000, "stuffy"), (2001, "high")]:
            check(f"CO2 {ppm} ppm is {key}", e.co2_band(ppm)["key"] == key)

        print("\n  pressure trend: only while parked")
        now = 1_000_000.0
        rows = [{"t": now - 3 * 3600 + i * 60, "v": {"outdoor.hpa": 1010 + i * 0.01}, "mv": 0.0} for i in range(180)]
        tr = e.pressure_trend(rows, now)
        check(f"parked: +1.8 hPa over 3 h reads as rising ({tr.get('hpa3h')})", tr["available"] and tr["word"] == "rising")
        rows[90]["mv"] = 80.0
        tr = e.pressure_trend(rows, now)
        check("the car moved in the window: 'trend unavailable while driving'",
              not tr["available"] and tr["reason"] == "trend unavailable while driving")
        check("an hour of readings is not enough", not e.pressure_trend(rows[-60:], now)["available"])
        for r in rows:
            r.pop("mv")
        tr = e.pressure_trend(rows, now)
        check("no car link: shown, with the assumption said", tr["available"] and "assumed parked" in tr["note"])

        print("\n  the stale-air clock (5 min over 1500 ppm, moving)")
        rec = e.Record("sensor")

        def at(t, co2, speed):
            rec.ingest("cabin", 1, {"co2": co2}, t)
            rec.set_car(speed, None, t)
            rec.tick_prompt(t)
            return e.derive(rec.snapshot(t), t)["prompt"]

        t0 = 5000.0
        p = None
        for s in range(0, 290):
            p = at(t0 + s, 1640, 90)
        check("4 min 50 s: not yet", not p["due"])
        p = at(t0 + 300, 1640, 90)
        check("5 min: due, with the ppm", p["due"] and p["ppm"] == 1640)
        p = at(t0 + 301, 1300, 90)
        check("fresh air (1300, under the 1400 clear line) ends it", not p["due"])
        rec = e.Record("sensor")
        for s in range(0, 200):
            at(t0 + s, 1700, 90)
        for s in range(200, 260):
            at(t0 + s, 1700, 0)      # stopped for a minute: the clock starts over
        p = at(t0 + 330, 1700, 90)
        check("a stop longer than half a minute starts the clock over", not p["due"])
        rec = e.Record("sensor")
        for s in range(0, 400):
            p = at(t0 + s, 1700, 0)
        check("parked with 1700 ppm: never a prompt", not p["due"])
        rec = e.Record("sensor")
        for s in range(0, 400):
            rec.ingest("cabin", 1, {"co2": 1700}, t0 + s)
            rec.tick_prompt(t0 + s)
        check("no car link at all: never a prompt (moving is unknown)", not e.derive(rec.snapshot(t0 + 400), t0 + 400)["prompt"]["due"])

        print("\n  who said it")
        rec = e.Record("sensor")
        rec.ingest("outdoor", 1, {"t_c": 17.9, "rh": 63.0, "hpa": 1012.6, "pm25": 6.1}, now)
        rec.set_car(0, 19.5, now)
        d = e.derive(rec.snapshot(now), now)
        check("the outdoor node's temperature wins over the car's", d["outside"]["values"]["t_c"] == 17.9
              and "t_source" not in d["outside"] and d["outside"]["car_t_c"] == 19.5)
        check("a live node is 'sensor'", d["outside"]["source"] == "sensor" and d["outside"]["state"] == "live")
        check("outdoor PM2.5 6.1 is Good", d["outside"]["aqi"]["key"] == "good")
        check("cabin with no node is absent, not zero", d["cabin"]["state"] == "absent" and d["cabin"]["values"] == {})
        rec = e.Record("sensor")
        rec.set_car(0, 12.0, now)
        d = e.derive(rec.snapshot(now), now)
        check("no outdoor node: the car's thermometer, labelled the car's",
              d["outside"]["source"] == "car" and d["outside"]["t_source"] == "car" and d["outside"]["values"]["t_c"] == 12.0)
        check("and no dew point is invented from it", d["outside"]["dew_c"] is None and d["outside"]["aqi"] is None)
        rec = e.Record("sensor")
        rec.ingest("outdoor", 1, {"t_c": 17.9, "rh": 63.0}, now - 400)
        d = e.derive(rec.snapshot(now), now)
        check("a node silent for 400 s is stale, keeps its number, and says how old",
              d["outside"]["state"] == "stale" and d["outside"]["values"]["t_c"] == 17.9 and d["outside"]["age"] >= 399)
        rec.set_car(0, 12.0, now)
        check("a stale node with a car number: the node's last reading still shown (it is the better thermometer)",
              e.derive(rec.snapshot(now), now)["outside"]["values"]["t_c"] == 17.9)
        rec = e.Record("sim")
        rec.ingest("cabin", 1, {"t_c": 22}, now)
        check("the simulator is tagged sim", e.derive(rec.snapshot(now), now)["cabin"]["source"] == "sim")

        print("\n  the record")
        rec = e.Record("sensor")
        for s in range(0, 130):
            rec.ingest("cabin", 1, {"co2": 700 + s, "t_c": 21.0}, 60000 + s)
            rec.set_car(0 if s < 100 else 50, None, 60000 + s)
        # A minute roll: the first call arms, the next minute's call emits the row.
        rec2 = e.Record("sensor")
        rec2.roll_minute(60030.0)
        for s in range(30):
            rec2.ingest("cabin", 1, {"co2": 800}, 60030.0 + s)
        rec2.set_car(55.0, None, 60045.0)
        row = rec2.roll_minute(60090.0)
        check("a minute rolls into one averaged row with the max speed",
              row and row["v"]["cabin.co2"] == 800 and row["mv"] == 55.0)
        tr = [p for p in rec.snapshot(60130)["trail"]["cabin.co2"]]
        check(f"trails keep one point per 5 s ({len(tr)})", 24 <= len(tr) <= 27)
        e.append_row({"t": time.time() - 3600, "v": {"cabin.co2": 900}}, "sensor")
        e.append_row({"t": time.time() - 90000, "v": {"cabin.co2": 400}}, "sensor")
        with open(e.HIST, "a") as f:
            f.write('{"t": 1, "v"\n')
        check("history reads a day and survives a torn line", len(e.load_rows("sensor")) == 1)
        e.prune("sensor")
        check("prune drops what is older than a day", len(e.load_rows("sensor", keep=10**9)) == 1)

        print("\n  the file the app reads")
        check("nothing written: present False", e.read_public() == {"present": False})
        rec = e.Record("sensor")
        nowt = time.time()
        rec.ingest("cabin", 1, {"t_c": 22.4, "rh": 41.2, "co2": 812}, nowt)
        e.write(rec.snapshot(nowt))
        pub = e.read_public()
        check("a fresh write is present, live, and carries derived numbers",
              pub["present"] and pub["cabin"]["state"] == "live" and pub["cabin"]["co2_band"]["key"] == "ok"
              and pub["cabin"]["dew_c"] is not None)
        old = json.load(open(e.STATE))
        old["at"] -= 600
        for n in old["nodes"].values():
            n["last"] -= 600
        json.dump(old, open(e.STATE, "w"))
        pub = e.read_public()
        check("ten minutes stale is not live but keeps its number",
              pub["cabin"]["state"] == "stale" and pub["cabin"]["values"]["co2"] == 812)

        print("\n  finding the nodes: the adapter is never opened")
        dev, sysfs, byid = (os.path.join(scratch, d) for d in ("dev", "sys", "byid"))
        for d in (dev, byid):
            os.makedirs(d)
        for name, ids in [("ttyACM0", ("2e8a", "000a")), ("ttyACM1", ("303a", "1001")),
                          ("ttyUSB0", ("0403", "6015")), ("ttyACM2", ("1209", "6668")),
                          ("ttyACM3", ("2341", "0043")), ("ttyACM4", ("2e8a", "000a"))]:
            open(os.path.join(dev, name), "w").close()
            usbdev = os.path.join(sysfs, "devices", "usb-" + name)
            os.makedirs(os.path.join(usbdev, "if0"))
            for fn, val in (("idVendor", ids[0]), ("idProduct", ids[1])):
                open(os.path.join(usbdev, fn), "w").write(val + "\n")
            tty = os.path.join(sysfs, "class", "tty", name)
            os.makedirs(tty)
            os.symlink(os.path.join(usbdev, "if0"), os.path.join(tty, "device"))
        os.symlink(os.path.join(dev, "ttyACM0"), os.path.join(byid, "usb-Raspberry_Pi_Pico_E6614-if00"))
        os.symlink(os.path.join(dev, "ttyUSB0"), os.path.join(byid, "usb-FTDI_OBDLink_SX_X-if00-port0"))
        ok_, no_ = e.candidates(dev=dev, sysfs=sysfs, byid=byid, avoid={os.path.realpath(os.path.join(dev, "ttyACM4"))},
                                busy=lambda p: p.endswith("ttyACM3"))
        names = {os.path.basename(p) for p, _ in ok_}
        refused = dict(no_)
        check("a Pico and an ESP32-S3 are offered", {"ttyACM0", "ttyACM1"} <= names)
        check("the OBDLink (FTDI, and named like one) is refused",
              "ttyUSB0" not in names and any("OBDLink" in n for n, _ in no_))
        check("a lightsaber board (unknown vendor) is refused", "ttyACM2" not in names)
        check("the port the daemon holds is refused even though its vendor is a node's",
              "ttyACM4" not in names and refused.get("ttyACM4") == "the car link's port")
        check("a port another process has open is refused", "ttyACM3" not in names and refused.get("ttyACM3") == "open in another process")
        check("each device is listed once, by its by-id name", sum(1 for p, _ in ok_ if p.endswith("ttyACM0")) == 1)

        print("\n  probing: read-only")
        fs = FakeSerial([b"boot noise\n", b'{"node":"cabin","fw":2,"co2":700}\n', b'{"node":"cabin","fw":2,"co2":701}\n'])
        hit = e.probe("/dev/null", opener=lambda p: fs, secs=1.0)
        check("finds the node, its firmware and closes the port", hit == ("cabin", 2, 2) and fs.closed)
        check("never writes a byte to it", fs.written == [])
        check("a port that says nothing is None", e.probe("/dev/null", opener=lambda p: FakeSerial([]), secs=0.2) is None)
        def boom(p):
            raise OSError("busy")
        check("a port that will not open is None, not a crash", e.probe("/dev/null", opener=boom) is None)

        print("\n  the simulator")
        e.sim(seconds=1.2, seed=1, drive=True, co2=1700)
        s = e.read_public()
        check(f"says it is simulated ({s['source']}, {s['cabin']['source']})",
              s["source"] == "sim" and s["cabin"]["source"] == "sim" and s["outside"]["source"] == "sim")
        check("keeps its history in its own file, never the real one", os.path.exists(e.HIST_SIM) and len(e.load_rows("sim")) >= 1400)
        check("driving with 1700 ppm: the prompt is already due", s["prompt"]["due"] and s["moving"])
        check("a day of history is served", len(e.history(24)["series"]["cabin.co2"]) >= 1400)
        e.sim(seconds=1.2, seed=1, outdoor=False)
        s = e.read_public()
        check("--no-outdoor falls back to the car, and says so", s["outside"]["source"] == "car" and s["outside"]["t_source"] == "car")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
