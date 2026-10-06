#!/usr/bin/env python3
"""The battery-health model, tested on synthetic samples only.

The things worth a test are the ones that would fail quietly: a drive split in
the wrong place, a recalibration counted three times because the pack stepped
three times in a row, a verdict that says "Healthy" before it has seen ten
drives, and a summary() that raises into the API. The module also promises it
never opens a serial port, so the last check reads its own source.
"""

import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

PASS, FAIL = [], []


def ok(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   {'ok ' if cond else 'FAIL'}  {name}")


def bad(name):
    ok(name, False)


def check(name, got, want):
    good = got == want
    ok(name if good else f"{name} (got {got!r}, want {want!r})", good)


tmp = tempfile.mkdtemp(prefix="omacar-bh-test-")
os.environ["XDG_STATE_HOME"] = tmp

import records  # noqa: E402
import battery_health as bh  # noqa: E402

SCHEMA = ("CREATE TABLE samples(t REAL PRIMARY KEY, rpm, speed, load, throttle,"
          " coolant, intake, maf, stft, ltft, timing, lphk, eff, soc)")
_n = [0]


def fresh():
    """A new empty database, with records pointed at it."""
    _n[0] += 1
    p = os.path.join(tmp, f"db{_n[0]}", "car.db")
    os.makedirs(os.path.dirname(p))
    db = sqlite3.connect(p)
    db.execute(SCHEMA)
    db.commit()
    records.DB = p
    return db


def put(db, rows):
    db.executemany("INSERT INTO samples(t, speed, soc) VALUES (?,?,?)", rows)
    db.commit()


def cycle(t0, seconds=600, lo=50.0, hi=70.0, speed=40.0):
    """1 Hz triangle wave lo -> hi -> lo, as (t, speed, soc) rows."""
    half = seconds / 2.0
    out = []
    for i in range(seconds + 1):
        x = i / half if i <= half else 2 - i / half
        out.append((t0 + i, speed, lo + (hi - lo) * x))
    return out


def reader():
    return records.connect()


# ---- drives ----------------------------------------------------------------
db = fresh()
put(db, cycle(1000, 400) + cycle(1000 + 400 + 1200, 400))
ds = bh.drives(reader())
check("two blocks 20 minutes apart are 2 drives", len(ds), 2)
check("a drive keeps its rows", len(ds[0]["rows"]), 401)

db = fresh()
put(db, cycle(1000, 240))
check("a 4-minute block is no drive", len(bh.drives(reader())), 0)

db = fresh()
put(db, cycle(1000, 400, speed=0.0))
check("a parked block with no speed is no drive", len(bh.drives(reader())), 0)

db = fresh()
rows = cycle(1000, 400)
db.executemany("INSERT INTO samples(t, speed, soc) VALUES (?,?,?)",
               [(r[0], r[1], r[2]) for r in rows])
db.execute("INSERT INTO samples(t, speed, soc) VALUES (1200.5, 40, NULL)")
db.commit()
check("rows without soc are left out", len(bh.drives(reader())[0]["rows"]), 401)

# ---- measure ---------------------------------------------------------------
db = fresh()
# The 5th-95th percentile of a triangle wave trims a tenth of its swing, so a
# 20-point window is a cycle of 49-71.
put(db, cycle(1000, 600, 49, 71))
m = bh.measure(bh.drives(reader())[0])
ok("a 50-70-50 cycle has a window about 20 wide", abs(m["window_width"] - 20) < 1)
ok("window_lo and window_hi bracket it",
   abs(m["window_lo"] - 50) < 1 and abs(m["window_hi"] - 70) < 1)
check("minutes is the span", round(m["minutes"], 1), 10.0)
check("a smooth cycle has no recalibrations", m["recals"], 0)


def steps(pairs, base=60.0, seconds=400):
    """A flat drive at `base`, with soc overwritten per {t_offset: soc}."""
    rows = []
    cur = base
    for i in range(seconds + 1):
        cur = pairs.get(i, cur)
        rows.append((5000 + i, 40.0, cur))
    return rows


def recals_of(rows):
    d = fresh()
    put(d, rows)
    return bh.measure(bh.drives(reader())[0])["recals"]


check("a 60->70 step in 2 s counts 1 recalibration",
      recals_of(steps({100: 70, 101: 70, 102: 70})), 1)
check("a 60->70 step over 2 s in thirds is too gentle per pair",
      recals_of(steps({100: 63, 101: 66, 102: 70})), 0)
check("three steps within 20 s count 1",
      recals_of(steps({100: 70, 110: 60, 120: 70})), 1)
check("two steps a minute apart count 2",
      recals_of(steps({100: 70, 200: 60})), 2)
slow = {100 + i: 60 + 10 * i / 60 for i in range(61)}
check("a slow 60->70 over 60 s counts 0", recals_of(steps(slow)), 0)

# ---- the verdict -----------------------------------------------------------


def hist(widths, recals=None, floor=0.0):
    recals = recals or [0] * len(widths)
    return [{"t0": i * 1000.0, "t1": i * 1000.0 + 600, "minutes": 10.0,
             "window_lo": 50.0, "window_hi": 50.0 + w, "window_width": w,
             "recals": r, "floor_share": floor}
            for i, (w, r) in enumerate(zip(widths, recals))]


v = bh.verdict(hist([20] * 9))
check("9 drives is Not enough data yet", v["verdict"], "Not enough data yet")
ok("and says 9 of 10", "9 of 10 drives so far" in v["reasons"])
check("with the drive count", v["drives"], 9)

v = bh.verdict(hist([20] * 40))
check("40 steady drives are Healthy", v["verdict"], "Healthy")
ok("with the steady-window reason",
   "no recalibrations and a steady window over the last 10 drives" in v["reasons"])

rc = [0] * 30 + [1, 0, 0, 1, 0, 0, 1, 0, 0, 0]
v = bh.verdict(hist([20] * 40, rc))
check("3 recalibrating drives in the last 10 is Watch it", v["verdict"], "Watch it")
ok("naming recalibrations", any("recalibrations" in r for r in v["reasons"]))
ok("and up from none", any("up from none" in r for r in v["reasons"]))

v = bh.verdict(hist([20] * 40, [0] * 30 + [1, 0] * 5))
check("1 recalibration in each of 5 drives stays Watch it", v["verdict"], "Watch it")

v = bh.verdict(hist([20] * 40, [0] * 30 + [1] * 6 + [0] * 4))
check("recalibrations in 6 of 10 drives are Failing", v["verdict"], "Failing")

v = bh.verdict(hist([20] * 30 + [12] * 10))
check("a window 40% narrower is Failing", v["verdict"], "Failing")
ok("naming narrower", any("narrower" in r for r in v["reasons"]))

v = bh.verdict(hist([20] * 30 + [17] * 10))
check("a window 15% narrower is Watch it", v["verdict"], "Watch it")

v = bh.verdict(hist([20] * 3 + [12] * 10))
check("a narrowing needs 5 base drives", v["verdict"], "Healthy")

v = bh.verdict(hist([20] * 10, [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]))
check("no base: 2 recalibrations compare against zero", v["verdict"], "Watch it")

v = bh.verdict(hist([20] * 40, [2] * 40))
check("recalibrating in every recent drive is Failing even if it always did",
      v["verdict"], "Failing")

# ---- rebuild and the table ---------------------------------------------------
db = fresh()
rows = []
for i in range(12):
    rows += cycle(10000 + i * 2000, 600)
put(db, rows)
n = bh.rebuild(records.connect_rw())
check("rebuild reports the drives", n, 12)
db2 = records.connect_rw()
a = [tuple(r) for r in db2.execute("SELECT * FROM battery_health ORDER BY t0")]
check("rebuild twice", bh.rebuild(records.connect_rw()), 12)
b = [tuple(r) for r in records.connect_rw().execute(
    "SELECT * FROM battery_health ORDER BY t0")]
check("gives an identical table", a, b)
check("history reads it back", len(bh.history(records.connect_rw())), 12)
check("the table has the 8 columns",
      [r[1] for r in records.connect_rw().execute("PRAGMA table_info(battery_health)")],
      ["t0", "t1", "minutes", "window_lo", "window_hi", "window_width",
       "recals", "floor_share"])

# floor_share: this drive's own window_lo is the floor when nothing came before
first = bh.history(records.connect_rw())[0]
ok("the first drive's floor share is a share", 0.0 <= first["floor_share"] <= 1.0)

# ---- summary ---------------------------------------------------------------
s = bh.summary()
check("summary on 12 drives", s["verdict"], "Healthy")
check("window is measured", s["measures"]["window"]["state"], "measured")
check("recals series has a point per drive", len(s["measures"]["recals"]["series"]), 12)
check("floor time is measured", s["measures"]["floor_share"]["state"], "measured")
for k in ("capacity", "resistance", "sag", "blocks"):
    check(f"{k} is undiscovered", s["measures"][k]["state"], "undiscovered")
    ok(f"{k} carries no number", "series" not in s["measures"][k]
       and "how" in s["measures"][k])
ok("thresholds carry a source",
   s["thresholds"] and all("source" in t for t in s["thresholds"].values()))
ok("recalibration meaning is owner-reported",
   s["thresholds"]["recalibration"]["source"]
   == "owner reports, no Honda source")

put(db, cycle(10000 + 12 * 2000, 600))
check("a newer sample triggers a rebuild", bh.summary()["drives"], 13)

records.DB = os.path.join(tmp, "nowhere", "missing.db")
s = bh.summary()
ok("summary on a missing database does not raise and says error", "error" in s)
check("and falls back to Not enough data yet", s["verdict"], "Not enough data yet")
ok("without creating the database", not os.path.exists(records.DB))

p = os.path.join(tmp, "junk.db")
open(p, "w").write("not a database")
records.DB = p
ok("summary on garbage does not raise", "error" in bh.summary())

# ---- read-only towards the car -----------------------------------------------
src = open(os.path.join(ROOT, "lib", "battery_health.py")).read()
for word in ("serial", "import connect", "import elm", "import daemon"):
    ok(f"the module source has no {word!r}", word not in src)

print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
