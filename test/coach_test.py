#!/usr/bin/env python3
"""Hybrid coaching (lib/coach.py), on synthetic drives only.

What is worth a test: a smooth run must score well and a stop-and-go run with
late braking must score badly AND say why; missing data must give a partial
score that says "not enough data", never a zero; every number must say whether
it is measured or estimated; the simulator must say Simulated; and the module
must be unable to reach the car.
"""

import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


scratch = tempfile.mkdtemp(prefix="omacar-coach-")
os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
import coach  # noqa: E402


def sub(res, k):
    return res["subs"][k]


print("\n  a smooth highway run")
hw = coach.score_drive(coach.synth("highway", 1))
check("scores well", hw["headline"]["score"] >= 80)
check("is not partial", hw["headline"]["partial"] is False)
check("has three sub-scores, all numbers",
      all(isinstance(sub(hw, k)["score"], int) for k in ("regen", "smooth", "glide")))
check("smoothness is measured, regen is estimated",
      sub(hw, "smooth")["source"] == "measured" and sub(hw, "regen")["source"] == "estimated")
check("no hard slowdowns counted", hw["events"]["hard_brakes"] == 0)
check("it says so: a clean drive", hw["hint"]["id"] == "clean")

print("\n  a stop-and-go run with late braking")
ug = coach.score_drive(coach.synth("urban", 1))
check("scores worse than the highway run by a clear margin",
      ug["headline"]["score"] is not None
      and ug["headline"]["score"] <= hw["headline"]["score"] - 25)
check("counts the late brakes", ug["events"]["hard_brakes"] >= 6)
check("the hint is about braking, with the count in it",
      ug["hint"]["id"] == "late-braking"
      and str(ug["events"]["hard_brakes"]) in ug["hint"]["text"])
check("regen capture is lower than on the highway",
      sub(ug, "regen")["score"] < sub(hw, "regen")["score"])
check("auto-stop is measured from rpm at stops",
      ug["facts"]["autostop"]["source"] == "measured"
      and ug["facts"]["autostop"]["share"] is not None)

print("\n  missing data is partial, never zero")
nosoc = [{k: v for k, v in r.items() if k != "soc"} for r in coach.synth("mixed", 1)]
p = coach.score_drive(nosoc)
check("no charge readings: regen is None with a reason",
      sub(p, "regen")["score"] is None and "not enough data" in sub(p, "regen")["why"])
check("the headline is still a number, marked partial",
      isinstance(p["headline"]["score"], int) and p["headline"]["partial"] is True)
check("and its note says what it left out",
      "regen capture" in p["headline"]["note"] and "not enough data" in p["headline"]["note"])
check("no zero stands in for the missing sub-score",
      all(s["score"] != 0 or s["why"] is None for s in p["subs"].values()))
only_speed = coach.score_drive([{"t": i, "speed": 50 + (i % 7)} for i in range(400)])
check("speed alone: no headline, not a zero",
      only_speed["headline"]["score"] is None
      and "Not enough data" in only_speed["headline"]["note"])
check("empty and nonsense input do not raise",
      coach.score_drive([])["headline"]["score"] is None
      and coach.score_drive([{"t": "x", "speed": None}, {"t": 1}])["headline"]["score"] is None)
short = coach.score_drive([{"t": i, "speed": 30, "throttle": 10, "rpm": 1500, "soc": 60}
                           for i in range(60)])
check("a one-minute drive is too short to score",
      short["headline"]["score"] is None and "too short" in short["headline"]["note"])
gap = [r for r in coach.synth("highway", 2) if not (200 <= r["t"] - 1_000_000 <= 260)]
check("a minute of silence in the middle does not raise or invent numbers",
      coach.score_drive(gap)["headline"]["score"] is not None)

print("\n  the economy baseline")
past = [(10.0, 7.0), (12.0, 7.4), (9.0, 6.8), (80.0, 5.0)]
e = coach.economy_vs_baseline(6.3, 10.0, past)
check("median of similar-length drives only (the 80 km one is ignored)",
      e["baseline"] == 7.0 and e["n"] == 3)
check("less fuel than usual is a positive percentage", e["pct"] == 10)
check("and it is labelled estimated", e["source"] == "estimated")
e2 = coach.economy_vs_baseline(6.3, 10.0, past[:2])
check("two similar drives is not enough, and says so",
      e2["pct"] is None and "not enough data" in e2["why"])
check("no fuel figure is not enough data either",
      coach.economy_vs_baseline(None, 10, past)["pct"] is None)

print("\n  one hint, from rules")
ev = coach.choose_hint({"events": {"hard_brakes": 6}, "subs": {}, "facts": {},
                        "headline": {"score": 50}})
check("six late brakes gives the example hint",
      "6 times" in ev["text"] and "3 s earlier" in ev["text"])
quick = coach.choose_hint({"events": {"hard_accels": 5, "hard_brakes": 3}, "subs": {},
                           "facts": {}, "headline": {"score": 50}})
check("two rules fire, one hint comes back", isinstance(quick["text"], str)
      and quick["id"] in ("late-braking", "quick-launches"))
nodata = coach.choose_hint({"events": {}, "subs": {}, "facts": {}, "headline": {"score": None}})
check("an unscored drive says it was not recorded enough", nodata["id"] == "none")
idle = coach.choose_hint({"events": {}, "subs": {}, "headline": {"score": 70},
                          "facts": {"autostop": {"share": 5, "seconds": 120}}})
check("an engine idling at stops gets the auto-stop hint", idle["id"] == "idling-at-stops")

print("\n  the simulator")
s = coach.sim(now=1_800_000_000)
check("twenty drives", len(s["drives"]) == 20)
check("labelled Simulated in the file and on every drive",
      s["source"] == "sim" and all(d["source"] == "sim" for d in s["drives"]))
check("scored by the real scorer: every drive has a headline",
      all(d["headline"]["score"] is not None for d in s["drives"]))
check("the same seed gives the same drives",
      [d["headline"]["score"] for d in s["drives"]]
      == [d["headline"]["score"] for d in coach.sim(now=1_800_000_000)["drives"]])
check("a spread of scores, not twenty of one",
      len({d["headline"]["score"] for d in s["drives"]}) > 5)
check("economy gets a baseline once there are earlier drives",
      any(d["facts"]["economy"]["pct"] is not None for d in s["drives"]))

print("\n  the file and the API view")
check("nothing scored yet: not present", coach.read_public() == {"present": False, "source": None})
coach._save(s)
pub = coach.read_public()
check("present, simulated, last twenty",
      pub["present"] and pub["simulated"] and len(pub["drives"]) == 20)
check("a simulated file is never refreshed from the log",
      coach.refresh(coach._load())["source"] == "sim")
check("the trend carries scores and the Simulated flag",
      all("score" in d and d["simulated"] for d in pub["drives"]))

print("\n  scoring a real log read-only")
sdir = os.path.join(scratch, "real")
os.makedirs(sdir)
coach.STATE_DIR, coach.STATE = sdir, os.path.join(sdir, "coach.json")
import records  # noqa: E402
dbp = os.path.join(sdir, "t.db")
db = sqlite3.connect(dbp)
db.execute("CREATE TABLE samples(t REAL PRIMARY KEY, rpm, speed, load, throttle, coolant, intake,"
           " maf, stft, ltft, timing, lphk, eff, soc)")
db.execute("CREATE TABLE trips(t0 REAL PRIMARY KEY, t1 REAL, km REAL, litres REAL, lphk REAL,"
           " moving_s INTEGER, idle_s INTEGER, top_kph REAL, kind TEXT, label TEXT)")
for n, kind in enumerate(["urban", "highway", "mixed", "highway", "urban"]):
    rows = coach.synth(kind, n)
    t0 = 5_000_000 + n * 10_000
    for r in rows:
        db.execute("INSERT INTO samples(t, rpm, speed, throttle, soc) VALUES (?,?,?,?,?)",
                   (t0 + r["t"] - 1_000_000, r["rpm"], r["speed"], r["throttle"], r["soc"]))
    km = sum(r["speed"] for r in rows) / 3600
    db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)",
               (t0, t0 + len(rows), km, km * 0.07, 7.0 + n * 0.1, 0, 0, 100, "drive", "x"))
db.commit()
db.close()
records.DB = dbp
st = coach.refresh({"drives": []})
check("every trip scored, labelled real", len(st["drives"]) == 5
      and all(d["source"] == "real" for d in st["drives"]))
check("the first (urban) drive scored from the log is the urban one: braking counted",
      st["drives"][0]["events"]["hard_brakes"] >= 6)
check("scoring again adds nothing", len(coach.refresh(st)["drives"]) == 5)
check("the database was not changed (read-only)",
      sqlite3.connect(dbp).execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 5)

print("\n  it cannot reach the car")
src = open(os.path.join(ROOT, "lib", "coach.py"), encoding="utf-8").read()
check("imports no serial, adapter, daemon or elm module",
      not any(f"import {m}" in src for m in ("serial", "elm", "connect", "daemon", "actuate")))

print()
if fails:
    print(f"  {fails} FAILED")
    sys.exit(1)
print("  coach: every check holds")
