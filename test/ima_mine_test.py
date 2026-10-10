#!/usr/bin/env python3
"""tools/ima_mine.py against synthetic captures and a synthetic samples database.

Nothing here touches the car, the real database or the real captures: every
file is made in a scratch directory, and the miner is pointed at it.
"""

import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "ima_mine.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ima_mine  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        fails += 1
        print(f"  FAIL {name} {detail}")


T0 = 1_700_000_000.0
random.seed(7)


def soc_at(t):
    return 60 + 15 * math.sin(t / 40.0) + t / 50.0


def make_db(path, start, seconds):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE samples(t REAL PRIMARY KEY, rpm, speed, load, throttle,"
               " coolant, intake, maf, stft, ltft, timing, lphk, eff, soc)")
    for s in range(seconds):
        t = start + s
        db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (t, 1500, 50, 30, 20 + 10 * math.sin(s / 7.0), 80, 30, 5, 0, 0,
                    10, 0, 0, soc_at(s)))
    db.commit()
    db.close()


def make_capture(path, started, seconds):
    raw = []
    for i in range(seconds * 10):
        t = i / 10.0
        b2 = max(0, min(255, round(soc_at(t) * 2)))
        hexb = lambda bs: "".join(f"{b:02X}" for b in bs)
        # 1A6: the charge in byte 2 at half-percent steps, its neighbours quiet
        raw.append({"t": t, "id": "1A6", "data": hexb([0, 0, b2, 0])})
        # two more honest followers, so the top five are all signal
        raw.append({"t": t, "id": "3C0", "data": hexb([round(soc_at(t))])})
        raw.append({"t": t, "id": "4D0", "data": hexb([round(soc_at(t) * 1.5)])})
        # 2B0: pure noise
        raw.append({"t": t, "id": "2B0", "data": hexb([random.randrange(256)])})
    with open(path, "w") as f:
        json.dump({"started": started, "raw": raw}, f)


def run(captures, db, out):
    return subprocess.run([sys.executable, TOOL, "--captures", captures, "--db", db,
                           "--out", out, "--top", "5"],
                          capture_output=True, text=True)


with tempfile.TemporaryDirectory() as tmp:
    caps = os.path.join(tmp, "caps")
    os.makedirs(caps)
    db = os.path.join(tmp, "car.db")
    out = os.path.join(tmp, "out")
    make_db(db, T0, 300)
    make_capture(os.path.join(caps, "a.json"), T0, 300)
    # a capture with no raw frames, which must be counted but not mined
    with open(os.path.join(caps, "b.json"), "w") as f:
        json.dump({"started": T0}, f)

    p = run(caps, db, out)
    check("overlapping run exits 0", p.returncode == 0, p.stderr[-300:])
    cands = json.load(open(os.path.join(out, "candidates.json")))
    charge = [c for c in cands if c["target"] == "charge"]
    check("charge candidates exist", len(charge) > 0)
    top = charge[0] if charge else {}
    check("1A6 byte 2 ranks first for charge",
          top.get("id") == "1A6" and top.get("bytes") == [2], str(top))
    check("its r is above 0.95", top.get("r", 0) > 0.95, str(top.get("r")))
    check("2B0 byte 0 is not in the top 5 for charge",
          not any(c["id"] == "2B0" and c["bytes"] == [0] for c in charge[:5]))
    check("every candidate is state candidate",
          all(c["state"] == "candidate" for c in cands) and len(cands) > 0)
    check("every candidate has the contract's keys",
          all(set(("id", "bytes", "target", "r", "bins", "bracketed", "state")) <= set(c)
              for c in cands))
    check("overlap bins are not counted as bracketed",
          all(c["bracketed"] == 0 for c in cands), str(cands[:1]))
    check("targets are only charge, current, voltage",
          {c["target"] for c in cands} <= {"charge", "current", "voltage"})
    check("at most 5 per target", all(sum(1 for c in cands if c["target"] == t) <= 5
                                      for t in ("charge", "current", "voltage")))
    check("summary counts the captures", "2 captures" in p.stdout and "1 had raw" in p.stdout,
          p.stdout)

    # No overlap: the samples are a day away from the capture.
    db2 = os.path.join(tmp, "far.db")
    make_db(db2, T0 + 86400, 300)
    out2 = os.path.join(tmp, "out2")
    p = run(caps, db2, out2)
    check("no overlap exits 0", p.returncode == 0, p.stderr[-300:])
    check("no overlap writes an empty list",
          json.load(open(os.path.join(out2, "candidates.json"))) == [])
    check("no overlap says so", "no overlap" in p.stdout.lower(), p.stdout)
    check("no overlap says how much raw data exists", "frames" in p.stdout, p.stdout)

    # No samples database at all is the same honest outcome, not a crash.
    out3 = os.path.join(tmp, "out3")
    p = run(caps, os.path.join(tmp, "missing.db"), out3)
    check("no database says no overlap",
          p.returncode == 0 and "no overlap" in p.stdout.lower(), p.stdout + p.stderr)

    # Too few bins: a 30 s overlap is below the 60-bin floor.
    caps4 = os.path.join(tmp, "caps4")
    os.makedirs(caps4)
    make_capture(os.path.join(caps4, "s.json"), T0, 30)
    p = run(caps4, db, os.path.join(tmp, "out4"))
    check("under 60 bins yields nothing",
          json.load(open(os.path.join(tmp, "out4", "candidates.json"))) == [], p.stdout)

    # Garbage capture files never raise.
    caps5 = os.path.join(tmp, "caps5")
    os.makedirs(caps5)
    open(os.path.join(caps5, "bad.json"), "w").write("{nope")
    make_capture(os.path.join(caps5, "good.json"), T0, 300)
    p = run(caps5, db, os.path.join(tmp, "out5"))
    check("an unreadable capture is skipped", p.returncode == 0, p.stderr[-300:])

    # Bracketed: 30 short legs, each in a gap between charge readings, like the
    # recorder's real legs (it cannot poll while it listens to the bus).
    def make_legs(dirpath, dbpath, gap, legs=30, secs=4):
        os.makedirs(dirpath)
        db = sqlite3.connect(dbpath)
        db.execute("CREATE TABLE samples(t REAL PRIMARY KEY, rpm, speed, load, throttle,"
                   " coolant, intake, maf, stft, ltft, timing, lphk, eff, soc)")
        for k in range(legs):
            o = 100 * k
            for ts in (o - gap, o + secs + gap):
                db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (T0 + ts, 1500, 50, 30, 20, 80, 30, 5, 0, 0, 10, 0, 0,
                            soc_at(ts)))
            raw = []
            for i in range(secs * 10):
                t = i / 10.0
                raw.append({"t": t, "id": "1A6",
                            "data": f"0000{round(soc_at(o + t)):02X}00"})
                raw.append({"t": t, "id": "2B0", "data": f"{random.randrange(256):02X}"})
            with open(os.path.join(dirpath, f"leg{k}.json"), "w") as f:
                json.dump({"started": T0 + o, "raw": raw}, f)
        db.commit()
        db.close()

    caps6, db6 = os.path.join(tmp, "caps6"), os.path.join(tmp, "legs.db")
    make_legs(caps6, db6, 3)
    p = run(caps6, db6, os.path.join(tmp, "out6"))
    c6 = json.load(open(os.path.join(tmp, "out6", "candidates.json")))
    check("bracketed legs exit 0", p.returncode == 0, p.stderr[-300:])
    check("bracketed legs rank 1A6 byte 2 first for charge",
          bool(c6) and c6[0]["id"] == "1A6" and c6[0]["bytes"] == [2]
          and c6[0]["target"] == "charge" and c6[0]["r"] > 0.95, str(c6[:1]))
    check("bracketed legs give charge only", {c["target"] for c in c6} == {"charge"},
          str({c["target"] for c in c6}))
    check("every bracketed bin is counted as bracketed",
          all(c["bracketed"] == c["bins"] for c in c6), str(c6[:1]))
    check("the summary reports the bracketed legs", "30 are bracketed" in p.stdout,
          p.stdout)
    check("a candidate says how many captures it rests on",
          bool(c6) and c6[0].get("captures") == 30, str(c6[:1]))
    check("1A6 byte 2 moves inside a capture", bool(c6) and c6[0].get("moves_within") is True,
          str(c6[:1]))

    # Twelve longer legs: over 60 bins, but bracketing makes each leg ONE point
    # against charge, and twelve points is not a lead.
    caps8, db8 = os.path.join(tmp, "caps8"), os.path.join(tmp, "few-legs.db")
    make_legs(caps8, db8, 3, legs=12, secs=7)
    p = run(caps8, db8, os.path.join(tmp, "out8"))
    check("fewer than 20 bracketed captures give no candidate, however many bins",
          json.load(open(os.path.join(tmp, "out8", "candidates.json"))) == []
          and "12 are bracketed" in p.stdout, p.stdout)

    caps7, db7 = os.path.join(tmp, "caps7"), os.path.join(tmp, "far-legs.db")
    make_legs(caps7, db7, 45)
    p = run(caps7, db7, os.path.join(tmp, "out7"))
    check("readings beyond 30 s do not bracket",
          json.load(open(os.path.join(tmp, "out7", "candidates.json"))) == []
          and "no overlap" in p.stdout.lower(), p.stdout)

# Pure helpers
check("pearson of a line is 1", abs(ima_mine.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1) < 1e-9)
socs = [(0.0, 50.0), (10.0, 60.0)]
check("bracket interpolates in a straight line",
      ima_mine.bracket_bins(socs, 2.0, 5.0) == {2: {"charge": 52.5}, 3: {"charge": 53.5},
                                                4: {"charge": 54.5}})
check("a reading inside the capture is not a bracket",
      ima_mine.bracket_bins(socs + [(20.0, 70.0)], 5.0, 15.0) == {})
check("a capture before the first reading is not bracketed",
      ima_mine.bracket_bins(socs, -5.0, -1.0) == {})
check("pearson of a constant is None", ima_mine.pearson([1, 1, 1], [1, 2, 3]) is None)

src = open(TOOL).read()
check("source has no serial", "serial" not in src.lower())
check("source does not import connect", "import connect" not in src)
check("source does not import elm", "import elm" not in src)
check("source does not import daemon", "import daemon" not in src)

print("FAIL" if fails else "PASS")
sys.exit(1 if fails else 0)
