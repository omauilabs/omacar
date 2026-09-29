#!/usr/bin/env python3
"""The drive report: how a day becomes drives, the figures on the page, and
the promise that the page does not say whose car it is.

Run directly. Touches no real state: XDG_STATE_HOME and XDG_CONFIG_HOME are
pointed at a temporary directory BEFORE lib/records.py is imported, because it
resolves the garage at import time. Every database here is a fixture built by
this file; the owner's record is never opened.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="omacar-report-test-")
os.environ["XDG_STATE_HOME"] = os.path.join(_TMP, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "drive_report", os.path.join(ROOT, "tools", "drive-report.py"))
dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dr)
records = dr.records

PASS = FAIL = 0


def head(title):
    print(f"\n  {title}\n")


def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"   ok   {label}")
    else:
        FAIL += 1
        print(f"   FAIL  {label}")


def near(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


# The car this project runs on, so a leak in a test looks like a leak.
VIN = "JHMZF1D44FS001835"
PLATE = "JRMYERS"
DRIVER = "James"
DAY = "2026-09-29"
T0 = datetime(2026, 9, 29, 9, 0, 0).timestamp()


def row(t, speed=None, rpm=None, throttle=None, load=None, coolant=None,
        maf=None, soc=None):
    return {"t": t, "speed": speed, "rpm": rpm, "throttle": throttle, "load": load,
            "coolant": coolant, "intake": None, "maf": maf, "stft": None,
            "ltft": None, "timing": None, "lphk": None, "eff": 0.25, "soc": soc}


def cruise(t0, seconds, speed=72.0, rpm=2000.0, maf=10.0, step=1.0, **kw):
    out, t = [], t0
    while t < t0 + seconds:
        out.append(row(t, speed=speed, rpm=rpm, maf=maf, throttle=kw.get("throttle", 20.0),
                       load=40.0, coolant=kw.get("coolant", 88.0), soc=kw.get("soc")))
        t += step
    return out


def make_db(path, rows, with_soc=True, trips=()):
    db = sqlite3.connect(path)
    soc = ", soc REAL" if with_soc else ""
    db.execute("CREATE TABLE samples (t REAL PRIMARY KEY, rpm REAL, speed REAL, "
               "load REAL, throttle REAL, coolant REAL, intake REAL, maf REAL, "
               f"stft REAL, ltft REAL, timing REAL, lphk REAL, eff REAL{soc})")
    cols = list(records.SAMPLE_COLS if with_soc else records.SAMPLE_COLS[:-1])
    db.executemany(f"INSERT INTO samples ({','.join(cols)}) VALUES "
                   f"({','.join('?' * len(cols))})",
                   [tuple(r.get(c) for c in cols) for r in rows])
    db.execute("CREATE TABLE vehicle (k TEXT PRIMARY KEY, v TEXT)")
    for k, v in (("vin", VIN), ("plate", PLATE), ("driver", DRIVER), ("name", "CR-Z"),
                 ("year", 2015), ("make", "Honda"), ("model", "CR-Z")):
        db.execute("INSERT INTO vehicle VALUES (?, ?)", (k, json.dumps(v)))
    db.execute("CREATE TABLE trips (t0 REAL PRIMARY KEY, t1 REAL, km REAL, litres REAL, "
               "lphk REAL, moving_s INTEGER, idle_s INTEGER, top_kph REAL, kind TEXT, "
               "label TEXT)")
    for t in trips:
        db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)", t)
    db.commit()
    db.close()
    return path


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = dr.main(argv)
    return code, out.getvalue() + err.getvalue()


def ima_drive(pack_follows=True):
    """Thirty minutes of one-second readings that cycle every minute through
    assist (throttle open, revs climbing), cruise, regen (throttle closed,
    slowing) and cruise. The pack is re-read every eight seconds, as the
    daemon does, and either follows the IMA or climbs regardless of it."""
    out, soc, last_read = [], 60.0, None
    speed, rpm = 80.0, 2000.0
    for i in range(1800):
        t = T0 + i
        phase = i % 60
        if phase < 10:                                  # assist
            throttle, rpm, speed = 50.0, rpm + 60, speed + 1.0
            drift = -0.25
        elif phase < 30:
            throttle, rpm = 25.0, 2000.0
            drift = 0.0
        elif phase < 40:                                # regen
            throttle, speed = 13.0, speed - 1.0
            drift = +0.25
        else:
            throttle, rpm = 25.0, 2000.0
            drift = 0.0
        soc += drift if pack_follows else 0.05
        if last_read is None or t - last_read >= 8:
            reading, last_read = round(soc, 1), t
        out.append(row(t, speed=speed, rpm=rpm, throttle=throttle, load=40.0,
                       coolant=88.0, maf=8.0, soc=reading))
    return out


# ============================================================ splitting

head("A day becomes drives")

a = cruise(T0, 300)
b = cruise(T0 + 300 + 720, 300)                      # 12 min of silence
ok("a 12-minute silence with the engine turning on both sides is one drive",
   len(dr.split_drives(a + b)) == 1)

a_off = a[:-1] + [row(a[-1]["t"], speed=0.0, rpm=0.0, coolant=88.0)]
ok("the same silence after the engine stopped ends the drive",
   len(dr.split_drives(a_off + b)) == 2)

c = cruise(T0 + 300 + 1800, 300)                     # 30 min: longer than any leg
ok("30 minutes of silence ends it even with the engine turning",
   len(dr.split_drives(a + c)) == 2)

d = cruise(T0 + 300 + 540, 300)                      # 9 min: under the gap
ok("a 9-minute silence with the engine off does not end a drive",
   len(dr.split_drives(a_off + d)) == 1)

ok("--gap moves the line: 5 minutes splits that 9-minute silence",
   len(dr.split_drives(a_off + d, gap_s=300)) == 2)

parked = [row(T0 + 7200 + i, speed=0.0, rpm=0.0, coolant=40.0) for i in range(300)]
ok("ten minutes with the key on and the car never moving is not a drive",
   len(dr.drives_in(a_off + c + parked)) == 2 and len(dr.split_drives(a_off + c + parked)) == 3)

ok("a row holding only OmaCar's own eff score is not a reading",
   not dr.answered(row(T0)) and dr.answered(row(T0, soc=50.0)))

leg = re.search(r"^LEG_MINUTES\s*=\s*([\d.]+)",
                open(os.path.join(ROOT, "lib", "drivelog.py"), encoding="utf-8").read(), re.M)
ok("the bridge outlasts one capture leg (lib/drivelog.py LEG_MINUTES)",
   leg is not None and dr.BRIDGE_S >= float(leg.group(1)) * 60 + 120)

# ============================================================ figures

head("The figures")

s = dr.summarise(cruise(T0, 600))
ok("ten minutes at 72 km/h is 12 km", near(s["km"], 12.0, 0.03))
ok("and all of it moving", near(s["moving_s"], 600, 1.5) and s["idle_s"] == 0)
ok("top and average speed", s["top_kph"] == 72.0 and near(s["avg_kph"], 72.0, 0.2))
litres = 10.0 / records.AFR_GASOLINE * 600 / records.FUEL_DENSITY_G_PER_L
ok("fuel is mass air flow over 14.7, at 745 g per litre", near(s["litres"], litres, 0.002))
ok("economy is fuel over distance", near(s["lphk"], litres / s["km"] * 100, 0.01))
ok("every second read is full coverage", s["coverage"] > 0.99 and s["gaps"] == [])

stop = [row(T0 + 600 + i, speed=0.0, rpm=800.0, maf=2.0, coolant=88.0) for i in range(1, 121)]
s = dr.summarise(cruise(T0, 600) + stop)
ok("two minutes stopped is idle, not moving", near(s["idle_s"], 120, 1.5)
   and near(s["moving_s"], 600, 1.5))

blind = cruise(T0, 300) + [row(T0 + 300 + i, rpm=2000.0, coolant=88.0) for i in range(60)]
s = dr.summarise(blind)
ok("a row with no speed counts as neither moving nor idle",
   near(s["moving_s"], 300, 1.5) and s["idle_s"] == 0)

holed = cruise(T0, 300) + cruise(T0 + 300 + 120, 300)     # two unread minutes
s = dr.summarise(holed)
ok("distance is not integrated across a gap", near(s["km"], 12.0, 0.05))
ok("the gap is found and reported", len(s["gaps"]) == 1
   and near(s["gaps"][0][1] - s["gaps"][0][0], 121, 1.5))
ok("coverage counts only what was read", near(s["coverage"], 600 / 719, 0.01))

s = dr.summarise(cruise(T0, 600), trip={"km": 11.5, "litres": 0.6})
ok("a trip record's distance wins, and says so",
   s["km"] == 11.5 and s["km_source"] == "trip" and near(s["lphk"], 0.6 / 11.5 * 100, 0.01))

econ = dr.rolling_economy(holed)
after = [v for t, v in econ if T0 + 420 <= t < T0 + 425]
ok("rolling economy restarts after a gap rather than reaching across it",
   after and all(v is None for v in after))
ok("and reads normally once enough ground is covered",
   near(econ[-1][1], 10.0 / 14.7 / 745 * 3600 / 72 * 100, 0.05))

# ============================================================ drawing

head("The drawing")

pts = [(r["t"], r["speed"]) for r in holed]
path = dr.path_for(pts, T0, 720, 0, 100)
ok("a gap is drawn as a gap: the line lifts across it", path.count("M") == 2)
pts_none = [(T0 + i, None if 100 <= i < 110 else 50.0) for i in range(200)]
ok("a channel that did not answer is a break, not a zero",
   dr.path_for(pts_none, T0, 200, 0, 100).count("M") == 2
   and " 100.00" not in dr.path_for(pts_none, T0, 200, 0, 100))
ticks, lo, hi = dr.nice_ticks(0, 4320, floor_zero=True)
ok("0-4,320 rpm is drawn on 0-5,000", lo == 0 and hi == 5000)
peaks = [(T0 + i, 50.0 + (40.0 if i == 1234 else 0.0)) for i in range(4000)]
ok("thinning keeps a one-reading peak",
   " 10.00" in dr.path_for(peaks, T0, 4000, 0, 100))

# ============================================================ the IMA

head("IMA at work")

im = dr.ima(ima_drive(pack_follows=True))
ok("assist spells are found", len(im["assist"]["spells"]) >= 25)
ok("regen spells are found", len(im["regen"]["spells"]) >= 25)
ok("both are shown when the pack moves the way the IMA would move it",
   im["assist"]["supported"] and im["regen"]["supported"])

im = dr.ima(ima_drive(pack_follows=False))
ok("neither is shown when the pack climbs whatever the car does",
   not im["assist"]["supported"] and not im["regen"]["supported"])

no_pack = [dict(r, soc=None) for r in ima_drive()]
ok("without a pack reading there is nothing to check against, and it says so",
   "why" in dr.ima(no_pack) and "Hybrid pack" in dr.ima(no_pack)["why"])

# ============================================================ end to end

head("The page, from a fixture database")

drive1 = cruise(T0, 600, soc=62.0) + [row(T0 + 600 + i, speed=0.0, rpm=0.0, coolant=88.0,
                                          soc=62.0) for i in range(1, 30)]
drive2 = cruise(T0 + 3 * 3600, 600, speed=36.0, soc=58.0)
fixture = make_db(os.path.join(_TMP, "car.db"), drive1 + drive2)
before = hashlib.sha256(open(fixture, "rb").read()).hexdigest()

code, said = run(["--db", fixture, "--day", DAY, "-o", os.path.join(_TMP, "x.html")])
ok("two drives on the day and no --drive: refuses and lists them",
   code == 1 and "drive 2" in said and not os.path.exists(os.path.join(_TMP, "x.html")))

page_path = os.path.join(_TMP, "out", "one.html")
code, said = run(["--db", fixture, "--day", DAY, "--drive", "1", "--units", "imperial",
                  "-o", page_path])
page = open(page_path, encoding="utf-8").read() if os.path.exists(page_path) else ""
ok("--drive 1 writes the page", code == 0 and len(page) > 5000)
ok("the database is untouched",
   hashlib.sha256(open(fixture, "rb").read()).hexdigest() == before)

low = page.lower()
ok("no VIN on the page, in any case", VIN.lower() not in low)
ok("no plate", PLATE.lower() not in low)
ok("no owner's name", DRIVER.lower() not in low)
ok('never the words "state of charge"', "state of charge" not in low)
ok("the car is named by year, make and model", "2015 Honda CR-Z" in page)
ok('the pack is labelled "Hybrid pack" with its PID', "Hybrid pack" in page and "0x5B" in page)
ok("the distance is right, in miles", re.search(r">7\.\d<small> mi<", page) is not None)
ok("every headline figure carries a source",
   page.count('class="stat-src"') == page.count('class="stat"'))

head("The page keeps the artifact contract")

ok("a <title> in the first 8 KB", re.search(r"<title>[^<]{4,}</title>", page[:8192]) is not None)
ok("a viewport meta for phones", 'name="viewport"' in page and "viewport-fit=cover" in page)
css = page[page.find("<style>"):page.find("</style>")]
ok("light tokens on bare :root, dark under the media query and the toggle",
   re.search(r"^:root \{[^}]*--ground:", css, re.M) is not None
   and '@media (prefers-color-scheme: dark)' in css
   and ':root:not([data-theme="light"])' in css and ':root[data-theme="dark"]' in css
   and css.count("color-scheme: dark;") == 2)
ok("the body paints its own background", re.search(r"body \{[^}]*background: var\(--ground\)",
                                                      css) is not None)
hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', page))
ok("nothing loads from outside Google Fonts",
   hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"})
ok("no external script at all", re.search(r"<script[^>]+src=", page) is None)

head("It will not write a page that names the car's owner")

vehicle = {"vin": VIN, "plate": PLATE, "driver": DRIVER, "owner": DRIVER,
           "title": f"{DRIVER}' 2015 Honda CR-Z", "year": 2015, "make": "Honda",
           "model": "CR-Z", "name": "2015 Honda CR-Z", "label": "CR-Z"}
ok("the VIN in lower case is caught", "vin" in dr.leaks(f"<p>{VIN.lower()}</p>", vehicle))
ok('"State of Charge" in any case is caught',
   "state of charge" in dr.leaks("<h3>State of Charge</h3>", vehicle))
ok("a clean page passes", dr.leaks(page, vehicle) == [])
leak_path = os.path.join(_TMP, "leak.html")
code, said = run(["--db", fixture, "--day", DAY, "--drive", "1",
                  "--label", f"{DRIVER} drives to Los Banos", "-o", leak_path])
ok("a label carrying the owner's name stops the write",
   code == 3 and not os.path.exists(leak_path) and DRIVER not in said)

head("An older record, and the other units")

old = make_db(os.path.join(_TMP, "old.db"), [dict(r, soc=None) for r in drive2],
              with_soc=False)
old_path = os.path.join(_TMP, "old.html")
code, _ = run(["--db", old, "--day", DAY, "--units", "metric", "-o", old_path])
page = open(old_path, encoding="utf-8").read() if code == 0 else ""
ok("a record with no pack column still makes a page", code == 0 and page)
ok("with no Hybrid pack chart and no assist or regen bands",
   'id="c-pack"' not in page and 'class="band ' not in page)
ok("and says why IMA at work is left out", "Left out of this report" in page)
ok("metric units throughout", "km/h" in page and "L/100km" in page and ">6.0<small> km<" in page)

trip_db = make_db(os.path.join(_TMP, "trip.db"), drive1,
                  trips=[(T0, T0 + 600, 11.8, 0.7, 5.9, 600, 0, 72.0, "drive", "09:00"),
                         (1000.0, 1118.0, 3.0, 0.0, None, 120, 180, 90.0, "drive", "16:16")])
trip_path = os.path.join(_TMP, "trip.html")
code, said = run(["--db", trip_db, "--trip", "2026-09-29 09:00", "--units", "metric",
                  "-o", trip_path])
page = open(trip_path, encoding="utf-8").read() if code == 0 else ""
ok("--trip picks the trip record and uses its distance",
   code == 0 and ">11.8<small> km<" in page and "trip record" in page)
code, said = run(["--db", trip_db, "--list"])
ok("the 1970 fixture trip is not listed as a real one", "1970" not in said and "09:00" in said)

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n  {PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
