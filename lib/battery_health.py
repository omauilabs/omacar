#!/usr/bin/env python3
"""Is the hybrid battery getting worse? Judged from what the car already said.

READ-ONLY TOWARDS THE CAR. Nothing here talks to the adapter: it imports no
connection, no emulator and no daemon. It reads the `samples` table the daemon
has been filling, and writes one derived table of its own, `battery_health`,
which can be thrown away and rebuilt at any time.

`soc` is PID 0x5B in percent: the pack's remaining life, which the app treats
as its charge figure. `speed` is km/h.

THE MEASURES. Each drive becomes one row: how wide a slice of the pack it used
(the window), how many times the figure jumped (recalibrations), and how long
it spent near the bottom of that window. Only a measured quantity feeds the
verdict; capacity, resistance, sag and blocks need the parked IMA session and
are reported as undiscovered, never as a number.

THE VERDICT is one of "Healthy", "Watch it", "Failing" or "Not enough data
yet", and always comes with reasons. It stays "Not enough data yet" until ten
drives have measures. Every threshold is this app's own and says so; the
meaning of a recalibration is the owner's report, with no Honda source.

    omacar battery rebuild    recompute the table, print the number of drives
    omacar battery report     the verdict, its reasons, a line per measure
"""

import os
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

GAP_S = 600           # consecutive samples further apart than this end a drive
MIN_SPAN_S = 300      # a drive spans at least this long
MOVING_KPH = 3        # driving rows are faster than this
RECAL_STEP = 8        # a jump of this many points ...
RECAL_DT = 10         # ... within this many seconds is a recalibration
RECAL_RUN_S = 30      # qualifying pairs this close together count once
FLOOR_MARGIN = 3      # "near the floor" is under floor + this
FLOOR_LOOKBACK = 30   # previous drives the floor is taken from
MIN_DRIVES = 10
RECENT = 10
BASE = 30
BASE_MIN_FOR_WIDTH = 5
FAIL_RECAL_DRIVES = 5       # recalibrations in more than this many recent drives
FAIL_NARROWING = 0.35
WATCH_RECAL_RATE = 2
WATCH_NARROWING = 0.15

APP = "this app's threshold"
OWNER = "owner reports, no Honda source"
THRESHOLDS = {
    "drive_gap_s": {"value": GAP_S, "source": APP},
    "drive_min_span_s": {"value": MIN_SPAN_S, "source": APP},
    "min_drives": {"value": MIN_DRIVES, "source": APP},
    "recalibration": {"value": f"a step of {RECAL_STEP}+ points within "
                      f"{RECAL_DT} s", "source": OWNER},
    "failing_recal_drives": {"value": FAIL_RECAL_DRIVES, "source": APP},
    "failing_narrowing": {"value": FAIL_NARROWING, "source": APP},
    "watch_recal_rate": {"value": WATCH_RECAL_RATE, "source": APP},
    "watch_narrowing": {"value": WATCH_NARROWING, "source": APP},
    "floor_margin": {"value": FLOOR_MARGIN, "source": APP},
}
HOW = "the parked IMA session: tools/ima-session.sh"

TABLE = ("CREATE TABLE IF NOT EXISTS battery_health(t0 REAL PRIMARY KEY, "
         "t1 REAL, minutes REAL, window_lo REAL, window_hi REAL, "
         "window_width REAL, recals INTEGER, floor_share REAL)")
COLUMNS = ("t0", "t1", "minutes", "window_lo", "window_hi", "window_width",
           "recals", "floor_share")


def _is_driving(row):
    return row["speed"] is not None and row["speed"] > MOVING_KPH


def _qualifies(rows):
    return (len(rows) >= 2 and rows[-1]["t"] - rows[0]["t"] >= MIN_SPAN_S
            and any(_is_driving(r) for r in rows))


def drives(db):
    """Runs of samples with soc, split at gaps; only real drives are kept."""
    out, run = [], []
    for row in db.execute("SELECT * FROM samples WHERE soc IS NOT NULL "
                          "ORDER BY t"):
        if run and row["t"] - run[-1]["t"] > GAP_S:
            if _qualifies(run):
                out.append(run)
            run = []
        run.append(row)
    if _qualifies(run):
        out.append(run)
    return [{"t0": r[0]["t"], "t1": r[-1]["t"], "rows": r} for r in out]


def _percentiles(values):
    if len(values) < 2:
        v = values[0] if values else 0.0
        return v, v
    cuts = statistics.quantiles(values, n=20)
    return cuts[0], cuts[-1]


def _recals(rows):
    count, last = 0, None
    for a, b in zip(rows, rows[1:]):
        dt = b["t"] - a["t"]
        if abs(b["soc"] - a["soc"]) >= RECAL_STEP and dt <= RECAL_DT:
            if last is None or b["t"] - last > RECAL_RUN_S:
                count += 1
            last = b["t"]
    return count


def measure(drive, floor=None):
    """One drive's measures. `floor` defaults to its own window_lo."""
    rows = drive["rows"]
    moving = [r["soc"] for r in rows if _is_driving(r)]
    lo, hi = _percentiles(moving)
    if floor is None:
        floor = lo
    near = sum(1 for s in moving if s < floor + FLOOR_MARGIN)
    return {
        "t0": drive["t0"], "t1": drive["t1"],
        "minutes": (drive["t1"] - drive["t0"]) / 60.0,
        "window_lo": lo, "window_hi": hi, "window_width": hi - lo,
        "recals": _recals(rows),
        "floor_share": near / len(moving) if moving else 0.0,
    }


def rebuild(db):
    """Recompute every row in one transaction. Returns the rows written."""
    ds = drives(db)
    out, los = [], []
    for d in ds:
        floor = statistics.median(los[-FLOOR_LOOKBACK:]) if los else None
        m = measure(d, floor)
        los.append(m["window_lo"])
        out.append(m)
    db.execute(TABLE)
    with db:
        db.execute("DELETE FROM battery_health")
        db.executemany(
            f"INSERT INTO battery_health({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' * len(COLUMNS))})",
            [tuple(m[c] for c in COLUMNS) for m in out])
    return len(out)


def history(db):
    db.execute(TABLE)
    return [dict(zip(COLUMNS, tuple(r))) for r in db.execute(
        f"SELECT {','.join(COLUMNS)} FROM battery_health ORDER BY t0")]


def verdict(hist):
    n = len(hist)
    if n < MIN_DRIVES:
        return {"verdict": "Not enough data yet",
                "reasons": [f"{n} of {MIN_DRIVES} drives so far"],
                "drives": n, "measures": {}}
    recent, base = hist[-RECENT:], hist[-(RECENT + BASE):-RECENT]
    recent_rate = sum(d["recals"] for d in recent)
    recal_drives = sum(1 for d in recent if d["recals"] > 0)
    base_rate = (sum(d["recals"] for d in base) * RECENT / len(base)
                 if base else 0.0)
    narrowing = None
    if len(base) >= BASE_MIN_FOR_WIDTH:
        bw = statistics.median(d["window_width"] for d in base)
        if bw > 0:
            narrowing = 1 - statistics.median(
                d["window_width"] for d in recent) / bw

    failing, watch = [], []
    if recal_drives > FAIL_RECAL_DRIVES:
        failing.append(f"recalibrations in {recal_drives} of the last "
                       f"{RECENT} drives")
    if narrowing is not None and narrowing >= FAIL_NARROWING:
        failing.append(f"usable window {narrowing:.0%} narrower than the "
                       f"{len(base)} drives before")
    if recent_rate >= WATCH_RECAL_RATE and recent_rate - base_rate >= WATCH_RECAL_RATE:
        was = ("none" if base_rate == 0
               else f"{base_rate:.1f} per {RECENT} drives")
        watch.append(f"{recent_rate} recalibrations in the last {RECENT} "
                     f"drives, up from {was}")
    if (narrowing is not None and WATCH_NARROWING <= narrowing < FAIL_NARROWING):
        watch.append(f"usable window {narrowing:.0%} narrower than the "
                     f"{len(base)} drives before")

    if failing:
        label, reasons = "Failing", failing + watch
    elif watch:
        label, reasons = "Watch it", watch
    else:
        label = "Healthy"
        reasons = [f"no recalibrations and a steady window over the last "
                   f"{RECENT} drives"]
    return {"verdict": label, "reasons": reasons, "drives": n,
            "measures": {
                "recent_recals": recent_rate,
                "recent_recal_drives": recal_drives,
                "base_recals_per_10": round(base_rate, 2),
                "narrowing": None if narrowing is None else round(narrowing, 3),
                "recent_width": statistics.median(
                    d["window_width"] for d in recent)}}


def _series(hist, keys):
    return [dict({"t": h["t0"]}, **{k: h[k] for k in keys}) for h in hist]


def summary():
    """What /api/battery returns. Never raises."""
    try:
        if not os.path.exists(records.DB):
            raise FileNotFoundError("no database yet")
        db = records.connect_rw()
        try:
            db.execute(TABLE)
            # Rebuild once per finished drive, not on every call: only when a
            # driving sample is newer than the table and that drive has ended
            # (no driving for GAP_S seconds). Parked samples and a drive
            # still under way leave the table alone.
            newest = db.execute("SELECT MAX(t) FROM samples WHERE speed > 3 "
                                "AND soc IS NOT NULL").fetchone()[0]
            built = db.execute("SELECT MAX(t1) FROM battery_health").fetchone()[0]
            if newest is not None and (built is None or newest > built) \
                    and time.time() - newest > GAP_S:
                rebuild(db)
            hist = history(db)
        finally:
            db.close()
        out = verdict(hist)
        out["measures"] = dict(out.get("measures") or {}, **{
            "window": {"state": "measured",
                       "series": _series(hist, ("window_lo", "window_hi",
                                                "window_width"))},
            "recals": {"state": "measured",
                       "series": _series(hist, ("recals",))},
            "floor_share": {"state": "measured",
                            "series": _series(hist, ("floor_share",))},
            **{k: {"state": "undiscovered", "how": HOW}
               for k in ("capacity", "resistance", "sag", "blocks")}})
        out["thresholds"] = THRESHOLDS
        return out
    except Exception as e:  # noqa: BLE001 -- the API must not 500 on this
        return {"error": str(e), "verdict": "Not enough data yet",
                "reasons": []}


def report():
    s = summary()
    if "error" in s:
        print(f"battery: {s['error']}")
    print(f"Verdict: {s['verdict']}")
    for r in s["reasons"]:
        print(f"  - {r}")
    for k, m in (s.get("measures") or {}).items():
        if isinstance(m, dict) and "state" in m:
            if m["state"] == "measured":
                last = m["series"][-1] if m["series"] else None
                tail = ("no drives" if last is None else ", ".join(
                    f"{a}={b:.2f}" for a, b in last.items() if a != "t"))
                print(f"{k:12} measured      {len(m['series'])} drives; latest {tail}")
            else:
                print(f"{k:12} {m['state']:13} {m['how']}")
    return 0


def main(argv):
    cmd = argv[0] if argv else "report"
    if cmd == "rebuild":
        db = records.connect_rw()
        try:
            print(rebuild(db))
        finally:
            db.close()
        return 0
    if cmd == "report":
        return report()
    print("usage: omacar battery [rebuild|report]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
