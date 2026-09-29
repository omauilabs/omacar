#!/usr/bin/env python3
"""One drive, as a page somebody else can read.

    tools/drive-report.py --list                       every drive in the record
    tools/drive-report.py --day 2026-09-16 -o r.html   the drive on that day
    tools/drive-report.py --day 2026-09-29 --drive 2 -o back.html
    tools/drive-report.py --from "2026-09-16 16:40" --until "2026-09-16 18:31" -o r.html
    tools/drive-report.py --trip "2026-09-01 12:06" -o r.html

    --db PATH        read another record (a copy), never the live one by accident
    --units NAME     imperial or metric; the app's own setting when left out
    --label TEXT     a heading of the driver's own, shown as theirs
    --gap MINUTES    silence that ends a drive (default 10)

The output is ONE self-contained HTML file: the charts are inline SVG drawn
here, the only thing fetched is the typeface, and the page is complete with the
network unplugged -- it falls back to the system's sans and mono.

WHAT THIS PAGE IS FOR, AND THE RULE IT KEEPS.

It is the first thing OmaCar makes that is meant to be shown to strangers: a
drive, laid out for a room of people who have never seen the car. That makes
the app's two promises matter more here, not less.

  EVERY NUMBER SAYS WHERE IT CAME FROM. "Measured by the car over OBD-II" and
  "calculated by OmaCar from..." are different kinds of claim, and a page that
  prints them in the same voice has hidden which is which. Fuel is the one that
  bites: OBD-II does not report fuel burned on this car, so economy is mass air
  flow run through a stoichiometric ratio, and the page says so beside it.

  WHAT CANNOT BE COMPUTED HONESTLY IS LEFT OUT. A channel the car never
  answered is not drawn at zero, a gap in the readings is not drawn across, and
  the IMA section appears only when the hybrid pack's own reading backs it up.

It also says nothing about whose car this is. The car is year, make and model
through the same allowlist the sitrep uses, and the finished page is searched
for the VIN, the plate and the owner's name before it is written; if any of
them is there, nothing is written.

Read-only throughout: every read goes through lib/records.py, which opens the
database with mode=ro.
"""

import argparse
import bisect
import html
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import records  # noqa: E402
import sitrep  # noqa: E402

# ------------------------------------------------------------------ the rules

# THE CHANNELS THE CAR ACTUALLY ANSWERS. `lphk` and `eff` are in the same table
# but OmaCar computed them, and `eff` is written on every row whether the car
# answered or not -- the last half-minute of 16 September is rows holding
# nothing but eff=0.25. A row counts as a reading only if the car said
# something in it.
MEASURED = ("rpm", "speed", "load", "throttle", "coolant", "intake", "maf",
            "stft", "ltft", "timing", "soc")

# More than this many seconds between readings is a gap: no line is drawn
# across it and no distance or fuel is integrated over it. Ten seconds is the
# rule lib/records.py's daily figures already use, so a drive here and the same
# day in the app cannot disagree about what counts as missing. The daemon's
# routine fault-code hand-off (about three seconds) sits well inside it.
HOLE_S = 10.0

# Silence long enough to end one drive and start the next.
GAP_S = 600.0

# ...unless the engine was turning on both sides of it, and it is no longer
# than this. That is the capture recorder's signature, not a parked car: the
# drive-log supervisor only starts a leg with the engine running, and it hands
# the adapter back after LEG_MINUTES (20, lib/drivelog.py), plus the minute or
# so its probe takes. 16 September has exactly this: 10.7 minutes of silence at
# 16:52 with the engine at 759 rpm going in and 714 coming out, while a capture
# ran. Without the bridge that one drive reads as an eleven-minute warm-up and
# a separate trip. test/drive_report_test.py holds this above the leg length.
BRIDGE_S = 25 * 60.0

# The engine is running above this: the same test lib/drivelog.py and the
# watchdog use, so the three cannot disagree about whether the car is on.
RPM_RUNNING = 200

# Rolling economy is fuel over distance for the last minute of readings, never
# across a gap, and only once the car has covered enough ground in that minute
# for the ratio to mean anything.
ECON_WINDOW_S = 60.0
ECON_MIN_KM = 0.2

# THE IMA, INFERRED -- AND CHECKED BEFORE IT IS SHOWN.
#
# OBD-II gives this car no motor power, so assist and regeneration can only be
# read off the engine side: a throttle well open with the revs climbing, a
# closed throttle with the car slowing. That is an inference, and the page says
# so. What makes it worth printing is the one hybrid quantity the car does
# answer: if the pack reading does not fall across the "assist" spells and
# rise across the "regen" ones more often than it does across any stretch of
# the same length, the inference is not supported and the bands are not drawn.
#
# Throttle is judged against this drive's own closed-throttle reading rather
# than against nought: this car's closed throttle reads about 12.9%.
ASSIST_OPEN_PTS = 22.0      # at least this far above closed
CLOSED_PTS = 1.0            # within this of closed counts as closed
REGEN_MIN_KPH = 16.0        # about 10 mph: below it the car is stopping
SPELL_MIN_S = 4.0
CLOSE_S = 600.0             # the closer look: ten minutes
SPELL_JOIN_S = 4.0          # readings further apart than this end a spell
# The pack is re-read about every eight seconds, so a spell's effect shows in
# the first reading after it ends.
PACK_LAG_S = 10.0
PACK_MIN_SPELLS = 5
PACK_MIN_SHARE = 0.60
PACK_MIN_LIFT = 0.15        # over the same-length baseline

WHAT_READ = "measured by the car over OBD-II"
SRC = {
    "speed": f"{WHAT_READ} (PID 0x0D)",
    "rpm": f"{WHAT_READ} (PID 0x0C)",
    "throttle": f"{WHAT_READ} (PIDs 0x11 and 0x04)",
    "coolant": f"{WHAT_READ} (PID 0x05)",
    "soc": f"{WHAT_READ} (PID 0x5B)",
    "dist": "calculated by OmaCar from the car's speed readings",
    "trip": "from the trip record OmaCar filed during the drive",
    "clock": "timed by OmaCar from its reading timestamps",
    "econ": "calculated by OmaCar from the car's mass air flow (PID 0x10) and speed",
}
# What that calculation assumes, said once in full where the figure is drawn.
ECON_ASSUMES = ("OBD-II does not report fuel burned on this car, so fuel is mass air "
                "flow over 14.7 parts air to 1 of fuel, at 745 g per litre.")


# ------------------------------------------------------------------ reading

def answered(row):
    return any(row.get(c) is not None for c in MEASURED)


def load_rows(db, since=None, until=None):
    """Every reading in a span, through the app's own reader, unthinned."""
    out = records.samples(db, since=since, until=until, limit=10 ** 9)
    return [r for r in out if answered(r)]


def turning(row):
    return (row.get("rpm") or 0) > RPM_RUNNING


def split_drives(rows, gap_s=GAP_S, bridge_s=BRIDGE_S):
    """Readings into spans, at silences of `gap_s` or more.

    A silence is bridged -- the span carries on -- when the engine was turning
    at the last reading before it and the first after, and it is no longer
    than `bridge_s`. See BRIDGE_S for why that is a borrowed adapter and not a
    parked car.
    """
    spans, cur = [], []
    for r in rows:
        if cur:
            gap = r["t"] - cur[-1]["t"]
            if gap >= gap_s and not (gap <= bridge_s and turning(cur[-1])
                                     and turning(r)):
                spans.append(cur)
                cur = []
        cur.append(r)
    if cur:
        spans.append(cur)
    return spans


def moved(span):
    return any((r.get("speed") or 0) > records.MOVING_KPH for r in span)


def drives_in(rows, gap_s=GAP_S):
    """The spans in which the car went somewhere. Ten minutes of the key on in
    a driveway is a span; it is not a drive."""
    return [s for s in split_drives(rows, gap_s=gap_s) if moved(s)]


def holes(rows, hole_s=HOLE_S):
    return [(a["t"], b["t"]) for a, b in zip(rows, rows[1:])
            if b["t"] - a["t"] > hole_s]


# ------------------------------------------------------------------ the figures

def summarise(rows, trip=None):
    """The headline figures, with the same integration lib/records.py uses for
    a day: speed times time, fuel from mass air flow, and never across a gap --
    the reading after a gap stands for one second, as it does there."""
    km = litres = moving = idle = covered = 0.0
    top = None
    prev = None
    for r in rows:
        dt = 1.0 if prev is None else r["t"] - prev
        if dt > HOLE_S:
            dt = 1.0
        prev = r["t"]
        covered += dt
        s = r.get("speed")
        # A row with no speed says nothing about whether the car was moving,
        # so it counts toward neither. lib/records.py folds it into idle; that
        # is harmless for a daily total and wrong for a figure on this page.
        if s is not None:
            if s > records.MOVING_KPH:
                km += s * dt / 3600.0
                moving += dt
            else:
                idle += dt
            top = s if top is None else max(top, s)
        maf = r.get("maf")
        if maf and maf > 0:
            litres += (maf / records.AFR_GASOLINE) * dt / records.FUEL_DENSITY_G_PER_L
    t0, t1 = rows[0]["t"], rows[-1]["t"]
    span = max(1.0, t1 - t0)
    gaps = holes(rows)
    steps = [b["t"] - a["t"] for a, b in zip(rows, rows[1:]) if b["t"] - a["t"] <= HOLE_S]
    out = {
        "t0": t0, "t1": t1, "span_s": span, "n": len(rows),
        "covered_s": min(covered, span), "coverage": min(1.0, covered / span),
        "gaps": gaps, "cadence_s": statistics.median(steps) if steps else None,
        "km": km, "km_source": "dist", "litres": litres,
        "moving_s": moving, "idle_s": idle, "top_kph": top,
        "avg_kph": km / (moving / 3600.0) if moving > 0 else None,
        "has_maf": any(r.get("maf") is not None for r in rows),
    }
    if trip and trip.get("km"):
        out["km"] = float(trip["km"])
        out["km_source"] = "trip"
        if trip.get("litres"):
            out["litres"] = float(trip["litres"])
    # The same floor lib/records.py puts under a day: below half a kilometre a
    # fuel-over-distance figure is a rounding error with a unit on it.
    out["lphk"] = (out["litres"] / out["km"] * 100.0
                   if out["km"] > 0.5 and out["litres"] > 0 and out["has_maf"]
                   else None)
    return out


def rolling_economy(rows):
    """(t, L/100km) for each reading while moving: fuel over distance across
    the last minute of readings, restarted at every gap."""
    out, win = [], []
    wk = wl = 0.0
    prev = None
    for r in rows:
        t = r["t"]
        if prev is not None and t - prev > HOLE_S:
            win, wk, wl = [], 0.0, 0.0
        dt = 1.0 if prev is None or t - prev > HOLE_S else t - prev
        prev = t
        s, maf = r.get("speed"), r.get("maf")
        dk = s * dt / 3600.0 if s and s > records.MOVING_KPH else 0.0
        dl = ((maf / records.AFR_GASOLINE) * dt / records.FUEL_DENSITY_G_PER_L
              if maf and maf > 0 else 0.0)
        win.append((t, dk, dl))
        wk += dk
        wl += dl
        while win and win[0][0] < t - ECON_WINDOW_S:
            _, a, b = win.pop(0)
            wk -= a
            wl -= b
        ok = s and s > records.MOVING_KPH and maf is not None and wk >= ECON_MIN_KM and wl > 0
        out.append((t, wl / wk * 100.0 if ok else None))
    return out


# ------------------------------------------------------------------ the IMA

def _spells(rows, pred):
    out, start, last = [], None, None
    for a, b in zip(rows, rows[1:]):
        if b["t"] - a["t"] <= SPELL_JOIN_S and pred(a, b):
            if start is None:
                start = a["t"]
            last = b["t"]
        elif start is not None:
            if last - start >= SPELL_MIN_S:
                out.append((start, last))
            start = None
    if start is not None and last - start >= SPELL_MIN_S:
        out.append((start, last))
    return out


def _pack_at(ts, packs, t):
    """The pack reading in force at time t: the last one at or before it."""
    i = bisect.bisect_right(ts, t) - 1
    return packs[i] if i >= 0 else None


def ima(rows):
    """Assist and regen spells, and whether the pack reading supports each.

    Returns {"assist": {...}, "regen": {...}, "closed": pct} or a dict with
    only "why" when the drive cannot support the question at all.
    """
    running = sorted(r["throttle"] for r in rows
                     if r.get("throttle") is not None and turning(r))
    pack_rows = [(r["t"], r["soc"]) for r in rows if r.get("soc") is not None]
    if len(running) < 60:
        return {"why": "the car reported too little throttle and rpm to find assist or regen"}
    if len(pack_rows) < 60:
        return {"why": ("the car gave no Hybrid pack reading on this drive, so "
                        "there is nothing to check assist and regen against")}
    closed = running[int(len(running) * 0.02)]
    ts = [t for t, _ in pack_rows]
    packs = [v for _, v in pack_rows]

    def usable(a, b):
        return all(x.get(k) is not None for x in (a, b)
                   for k in ("throttle", "rpm", "speed"))

    def is_assist(a, b):
        return usable(a, b) and b["throttle"] >= closed + ASSIST_OPEN_PTS \
            and b["rpm"] > a["rpm"]

    def is_regen(a, b):
        return usable(a, b) and b["throttle"] <= closed + CLOSED_PTS \
            and b["speed"] < a["speed"] and b["speed"] > REGEN_MIN_KPH

    def delta(t_a, t_b):
        p0 = _pack_at(ts, packs, t_a)
        p1 = _pack_at(ts, packs, t_b + PACK_LAG_S)
        return None if p0 is None or p1 is None else p1 - p0

    # How the pack moves across ANY stretch of this drive of the typical spell
    # length: the bar a spell has to clear before its movement means anything.
    # Stretches that straddle a gap are skipped, as a spell cannot straddle one.
    row_ts = [r["t"] for r in rows]
    holes_upto = [0]
    for a, b in zip(rows, rows[1:]):
        holes_upto.append(holes_upto[-1] + (1 if b["t"] - a["t"] > HOLE_S else 0))

    def baseline(length):
        up = down = 0
        for i, t in enumerate(row_ts):
            j = bisect.bisect_left(row_ts, t + length)
            if j >= len(rows) or holes_upto[j] != holes_upto[i]:
                continue
            d = delta(t, row_ts[j])
            if d is None or d == 0:
                continue
            if d > 0:
                up += 1
            else:
                down += 1
        n = up + down
        return (up / n, down / n) if n else (None, None)

    out = {"closed": closed}
    for kind, pred, sign in (("assist", is_assist, -1), ("regen", is_regen, +1)):
        spells = _spells(rows, pred)
        moves = [delta(a, b) for a, b in spells]
        moves = [d for d in moves if d is not None]
        agree = sum(1 for d in moves if d * sign > 0)
        typical = statistics.median([b - a for a, b in spells]) if spells else SPELL_MIN_S
        up, down = baseline(typical) if spells else (None, None)
        base = down if sign < 0 else up
        share = agree / len(moves) if moves else None
        supported = bool(moves and len(moves) >= PACK_MIN_SPELLS and base is not None
                         and share >= PACK_MIN_SHARE and share >= base + PACK_MIN_LIFT)
        out[kind] = {"spells": spells, "checked": len(moves), "agree": agree,
                     "share": share, "baseline": base, "supported": supported,
                     "seconds": sum(b - a for a, b in spells)}
    return out


def closer_window(spells, gaps, t0, t1, length=None):
    """The gap-free stretch of `length` seconds holding the most spell time,
    as (start, end), or None. Over a two-hour axis a four-second spell is a
    hairline; over ten minutes it is a band you can see the pack move under."""
    length = length or CLOSE_S
    if t1 - t0 < length * 1.5 or not spells:
        return None
    best = None
    for a, _ in sorted(spells):
        w0 = max(t0, min(a - length * 0.1, t1 - length))
        w1 = w0 + length
        if any(gb > w0 and ga < w1 for ga, gb in gaps):
            continue
        score = sum(max(0.0, min(b, w1) - max(sa, w0)) for sa, b in spells)
        if best is None or score > best[0]:
            best = (score, w0, w1)
    return (best[1], best[2]) if best else None


# ------------------------------------------------------------------ choosing

def parse_when(text):
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"  cannot read {text!r} as a time: use YYYY-MM-DD HH:MM")


def day_bounds(day):
    try:
        start = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"  cannot read {day!r} as a day: use YYYY-MM-DD") from None
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp() - 1e-6


def trip_records(db):
    """The trip records, minus the watchdog fixture's 1970 trip that test runs
    once left in real databases (see test/all.sh)."""
    return sorted((t for t in records.trips(db, n=10 ** 6)
                   if t.get("t0") and t["t0"] > 1e9 and t.get("t1")),
                  key=lambda t: t["t0"])


def contained_trip(db, rows):
    """The trip record for this drive, when there is exactly one inside it."""
    t0, t1 = rows[0]["t"], rows[-1]["t"]
    inside = [t for t in trip_records(db) if t0 - 60 <= t["t0"] and t["t1"] <= t1 + 60]
    return inside[0] if len(inside) == 1 else None


def clock(t, fmt="%H:%M"):
    return datetime.fromtimestamp(t).strftime(fmt)


def list_drives(db, day=None, gap_s=GAP_S, units=None):
    u = units or records.units_for()
    since, until = day_bounds(day) if day else (None, None)
    rows = load_rows(db, since, until)
    by_day = {}
    for r in rows:
        by_day.setdefault(clock(r["t"], "%Y-%m-%d"), []).append(r)
    print()
    if not by_day:
        print("  No readings" + (f" on {day}." if day else " in this record."))
    for d, day_rows in sorted(by_day.items()):
        n = 0
        for span in split_drives(day_rows, gap_s=gap_s):
            s = summarise(span)
            mins = s["span_s"] / 60.0
            if moved(span):
                n += 1
                print(f"  {d}  drive {n}  {clock(s['t0'])}-{clock(s['t1'])}  "
                      f"{mins:5.1f} min  {records.to_dist(s['km'], u):6.1f} {u['dist']}  "
                      f"{s['coverage']:.0%} read")
            else:
                print(f"  {d}  --       {clock(s['t0'])}-{clock(s['t1'])}  "
                      f"{mins:5.1f} min  stationary, not a drive")
    trips = trip_records(db)
    if trips and not day:
        print("\n  Trip records:")
        for t in trips:
            print(f"    {clock(t['t0'], '%Y-%m-%d %H:%M')}  "
                  f"{records.to_dist(t['km'] or 0, u):.1f} {u['dist']}")
    print()


# ------------------------------------------------------------------ drawing

W, H = 1000.0, 100.0     # the plot's own coordinate space; the SVG stretches it
BUCKETS = 700            # min and max per bucket: extremes survive thinning


def nice_ticks(lo, hi, target=4, floor_zero=False):
    """Round tick values that contain [lo, hi], as (ticks, lo, hi)."""
    if floor_zero:
        lo = min(0.0, lo)
    if hi <= lo:
        hi = lo + 1.0
    # Of the round steps that give three to six intervals, the one that wastes
    # the least height: 0-4,320 rpm is drawn on 0-5,000, not 0-6,000.
    mag = 10 ** math.floor(math.log10((hi - lo) / target))
    best = None
    for m in (0.5, 1, 2, 2.5, 5, 10, 20):
        step = m * mag
        a = math.floor(lo / step) * step
        b = math.ceil(hi / step) * step
        n = round((b - a) / step)
        if 3 <= n <= 6 and (best is None or b - a < best[2] - best[1]):
            best = (step, a, b)
    step, a, b = best or (mag, math.floor(lo / mag) * mag, math.ceil(hi / mag) * mag)
    ticks, v = [], a
    while v <= b + step / 1000:
        ticks.append(round(v, 6))
        v += step
    return ticks, a, b


def fmt_num(v, step=None):
    if step is not None and step < 1:
        return f"{v:.1f}"
    return f"{v:,.0f}"


def path_for(points, t0, span, lo, hi):
    """An SVG path through (t, v) points, broken wherever the readings are.

    A None value or more than HOLE_S between readings starts a new subpath, so
    a gap is drawn as a gap. Thinned per bucket to the first, lowest, highest
    and last reading, in the order they happened, which keeps every peak.
    """
    def xy(t, v):
        x = (t - t0) / span * W
        y = H - (v - lo) / (hi - lo) * H
        return f"{x:.1f} {max(-1.0, min(H + 1, y)):.2f}"

    segs, cur, prev_t = [], [], None
    for t, v in points:
        if v is None or (prev_t is not None and t - prev_t > HOLE_S):
            if cur:
                segs.append(cur)
            cur = []
        if v is not None:
            cur.append((t, v))
        prev_t = t
    if cur:
        segs.append(cur)

    parts = []
    for seg in segs:
        keep, bucket, group = [], None, []

        def flush(group):
            if not group:
                return
            lo_p = min(group, key=lambda p: p[1])
            hi_p = max(group, key=lambda p: p[1])
            chosen = {id(p): p for p in (group[0], lo_p, hi_p, group[-1])}
            keep.extend(sorted(chosen.values(), key=lambda p: p[0]))

        for p in seg:
            b = int((p[0] - t0) / span * BUCKETS)
            if b != bucket:
                flush(group)
                group, bucket = [], b
            group.append(p)
        flush(group)
        if len(keep) == 1:
            parts.append(f"M{xy(*keep[0])}h0.01")
        else:
            parts.append("M" + " L".join(xy(t, v) for t, v in keep))
    return " ".join(parts)


def time_ticks(t0, t1):
    """Round clock times across the drive, as (t, label, odd). A phone shows
    only the even ones, which on a quarter-hour axis are the half hours. Ticks
    hard against either end are dropped so their labels cannot overhang."""
    span = max(1.0, t1 - t0)
    step = (2 if span <= 900 else 5 if span <= 1800 else 15 if span <= 5400
            else 30 if span <= 14400 else 60)
    cur = datetime.fromtimestamp(t0).replace(second=0, microsecond=0)
    cur += timedelta(minutes=(-cur.minute) % step)
    out = []
    while cur.timestamp() <= t1:
        frac = (cur.timestamp() - t0) / span
        if 0.03 <= frac <= 0.97:
            odd = ((cur.hour * 60 + cur.minute) // step) % 2 == 1
            out.append((cur.timestamp(), cur.strftime("%H:%M"), odd))
        cur += timedelta(minutes=step)
    return out


def esc(s):
    return html.escape(str(s), quote=True)


BAND_NAMES = {"assist": "Assist", "regen": "Regen"}
# A four-second spell on a two-hour axis is a fraction of a pixel. Drawn at
# least this wide (of the plot's 1,000) so a spell is visible where it is.
BAND_MIN_W = 1.6


def chart(cid, title, unit, source, series, ctx, floor_zero=False,
          bands=(), note=None, digits=0):
    """One figure: a title row with its source, HTML axis labels, and an SVG
    plot stretched to the width. Axis text lives in HTML so a phone does not
    shrink it to nothing along with the drawing.

    `series` is [("key:Name", [(t, v)], css_class)]; `key` names the channel
    the hover readout reports. `bands` is [(kind, [(t_a, t_b)])], shaded
    behind the line and given a legend of their own."""
    vals = [v for _, pts, _ in series for _, v in pts if v is not None]
    if not vals:
        return ""
    ticks, lo, hi = nice_ticks(min(vals), max(vals), floor_zero=floor_zero)
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    t0, t1 = ctx["t0"], ctx["t1"]
    span = max(1.0, t1 - t0)
    x = lambda t: (t - t0) / span * W  # noqa: E731

    svg = [f'<svg class="plot-svg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'preserveAspectRatio="none" aria-hidden="true">']
    for a, b in ctx["gaps"]:
        svg.append(f'<rect class="gap" x="{x(a):.1f}" y="0" '
                   f'width="{max(0.6, x(b) - x(a)):.1f}" height="{H:.0f}"/>')
    for cls, spells in bands:
        for a, b in spells:
            svg.append(f'<rect class="band {cls}" x="{x(a):.1f}" y="0" '
                       f'width="{max(BAND_MIN_W, x(b) - x(a)):.1f}" height="{H:.0f}"/>')
    for v in ticks:
        y = H - (v - lo) / (hi - lo) * H
        svg.append(f'<line class="grid" x1="0" x2="{W:.0f}" y1="{y:.2f}" y2="{y:.2f}"/>')
    # Drawn last-listed first, so the series the legend names first is on top.
    for key, pts, cls in reversed(series):
        svg.append(f'<path class="line {cls}" d="{path_for(pts, t0, span, lo, hi)}"/>')
    svg.append("</svg>")

    ylab = "".join(
        f'<span style="top:{(1 - (v - lo) / (hi - lo)) * 100:.2f}%">{fmt_num(v, step)}</span>'
        for v in ticks)
    xlab = "".join(
        f'<span class="{"odd" if odd else ""}" style="left:{x(t) / W * 100:.2f}%">{lab}</span>'
        for t, lab, odd in ctx["xticks"])
    keys = [s[0].split(":", 1) for s in series]
    chips = []
    if len(series) > 1:
        chips += [f'<span class="key {s[2]}">{esc(name)}</span>'
                  for (_, name), s in zip(keys, series)]
    chips += [f'<span class="key band-{kind}">{BAND_NAMES[kind]}</span>'
              for kind, _ in bands]
    legend = f'<div class="legend">{"".join(chips)}</div>' if chips else ""
    read_keys = ",".join(k for k, _ in keys)
    return f"""
<figure class="chart" id="{cid}" data-keys="{read_keys}" data-digits="{digits}"
  data-from="{t0 - ctx.get('base', t0):.1f}" data-span="{span:.1f}">
  <figcaption>
    <div class="chart-head"><h3>{esc(title)}</h3><span class="unit">{esc(unit)}</span>
      <output class="readout" aria-live="off"></output></div>
    <p class="source">{esc(source)}</p>
    {legend}
  </figcaption>
  <div class="plot">
    <div class="ylab" aria-hidden="true">{ylab}</div>
    <div class="area">{''.join(svg)}<div class="cross" hidden></div></div>
    <div class="xlab" aria-hidden="true">{xlab}</div>
  </div>
  {f'<p class="note">{esc(note)}</p>' if note else ''}
</figure>"""


# ------------------------------------------------------------------ the page

def hm(seconds):
    m = int(round(seconds / 60.0))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def hm_parts(seconds):
    m = int(round(seconds / 60.0))
    return [(str(m // 60), " h "), (f"{m % 60:02d}", " min")] if m >= 60 else [(str(m), " min")]


def tile(label, value, unit, source, sub=None):
    """A headline figure. `value` may be a list of (number, unit) pairs, which
    is how a duration stays on one line: 1 h 48 min, set as figures."""
    if isinstance(value, list):
        shown = "".join(f"{esc(n)}<small>{esc(w)}</small>" for n, w in value)
    else:
        shown = f"{esc(value)}<small>{esc(unit)}</small>"
    return f"""<div class="stat">
  <div class="stat-label">{esc(label)}</div>
  <div class="stat-value">{shown}</div>
  {f'<div class="stat-sub">{esc(sub)}</div>' if sub else ''}
  <div class="stat-src">{esc(source)}</div>
</div>"""


def car_line(vehicle):
    """Year, make and model, through the sitrep's allowlist and nothing else."""
    return " ".join(str(vehicle[k]) for k in sitrep.CAR_ALLOWED if vehicle.get(k))


def secrets_of(vehicle):
    """Every string that would say whose car this is, with the field it is."""
    out = []
    for k in ("vin", "plate", "driver", "owner", "title", "label"):
        v = str(vehicle.get(k) or "").strip()
        # `title` is "James' 2015 Honda CR-Z" when there is a driver and just
        # the car's name when there is not; only the first is identifying.
        if k in ("title", "label") and v and v in (car_line(vehicle), vehicle.get("model")):
            continue
        if len(v) >= 3:
            out.append((k, v))
    return out


FORBIDDEN_WORDS = ("state of charge",)


def leaks(page, vehicle):
    """What the finished page says that it must not. Case-insensitive, because
    "JHMZF1D44FS001835" in lower case is still the VIN."""
    low = page.lower()
    found = [k for k, v in secrets_of(vehicle) if v.lower() in low]
    found += [w for w in FORBIDDEN_WORDS if w in low]
    return found


def build(rows, vehicle, units, trip=None, label=None, generated=None):
    u = units
    s = summarise(rows, trip=trip)
    t0, t1 = s["t0"], s["t1"]
    ctx = {"t0": t0, "t1": t1, "base": t0, "gaps": s["gaps"], "xticks": time_ticks(t0, t1)}
    car = car_line(vehicle) or "The car"
    model = str(vehicle.get("model") or "").strip()
    start = datetime.fromtimestamp(t0)
    tz = time.strftime("%Z", time.localtime(t0))
    date_long = f"{start:%A} {start.day} {start:%B %Y}"
    page_title = f"{model + ' drive' if model else 'Drive'}, {start.day} {start:%b} {start:%H:%M}"

    def spd(v):
        return None if v is None else records.to_dist(v, u)

    # ---- the headline
    tiles = []
    km_src = SRC["trip"] if s["km_source"] == "trip" else SRC["dist"]
    part = (f"over the {hm(s['covered_s'])} the car was read"
            if s["coverage"] < 0.99 and s["km_source"] == "dist" else None)
    tiles.append(tile("Distance", f"{records.to_dist(s['km'], u):.1f}", f" {u['dist']}",
                      km_src, part))
    tiles.append(tile("Duration", hm_parts(s["span_s"]), "",
                      SRC["clock"], f"{clock(t0)} to {clock(t1)} {tz}"))
    tiles.append(tile("Moving", hm_parts(s["moving_s"]), "", SRC["clock"] + " and the car's speed",
                      f"idle {hm(s['idle_s'])}, at or under "
                      f"{records.to_dist(records.MOVING_KPH, u):.0f} {u['speed']}"))
    if s["top_kph"] is not None:
        tiles.append(tile("Top speed", f"{spd(s['top_kph']):.0f}", f" {u['speed']}",
                          SRC["speed"]))
    if s["avg_kph"] is not None:
        tiles.append(tile("Average speed", f"{spd(s['avg_kph']):.0f}", f" {u['speed']}",
                          "calculated by OmaCar: distance over moving time"))
    econ = records.to_econ(s["lphk"], u)
    if econ is not None:
        tiles.append(tile("Average economy", f"{econ:.1f}", f" {u['econ']}", SRC["econ"],
                          f"{records.to_vol(s['litres'], u):.2f} {u['vol']} "
                          f"of fuel, estimated"))

    # ---- coverage
    gap_min = sum(b - a for a, b in s["gaps"]) / 60.0
    listed = sorted(s["gaps"], key=lambda g: g[1] - g[0], reverse=True)[:6]
    gap_items = "".join(
        f"<li><span>{clock(a, '%H:%M:%S')} to {clock(b, '%H:%M:%S')}</span>"
        f"<b>{(b - a) / 60.0:.1f} min</b></li>"
        for a, b in sorted(listed))
    more = len(s["gaps"]) - len(listed)
    strip = "".join(
        f'<rect class="gap" x="{(a - t0) / max(1, t1 - t0) * W:.1f}" y="0" '
        f'width="{max(1.5, (b - a) / max(1, t1 - t0) * W):.1f}" height="10"/>'
        for a, b in s["gaps"])
    coverage = f"""
<section class="coverage" aria-labelledby="cov-h">
  <h2 id="cov-h">How much of the drive was read</h2>
  <p class="lede"><strong>{s['coverage']:.0%}</strong> of this drive has readings:
  {s['covered_s'] / 60.0:.1f} of {s['span_s'] / 60.0:.1f} minutes, from {s['n']:,} samples{
  f" about {s['cadence_s']:.1f} s apart" if s['cadence_s'] else ""}.</p>
  <div class="strip" role="img" aria-label="Readings across the drive, with {len(s['gaps'])} gaps">
    <svg viewBox="0 0 {W:.0f} 10" preserveAspectRatio="none" aria-hidden="true">
      <rect class="have" x="0" y="0" width="{W:.0f}" height="10"/>{strip}</svg>
    <div class="strip-ends"><span>{clock(t0)}</span><span>{clock(t1)} {tz}</span></div>
  </div>
  <p>A gap is more than {HOLE_S:.0f} seconds without a reading, and nothing is drawn
  across one. OmaCar has one OBD-II adapter, and the CAN capture recorder borrows it
  to listen to the car's bus; while it has it, the gauges are not read. Distance,
  fuel and time here count only what was read.</p>
  {f'<ul class="gaps">{gap_items}</ul>' if gap_items else ''}
  {f'<p class="fine">and {more} shorter gap{"s" if more != 1 else ""}; {gap_min:.1f} min in all.</p>'
   if more > 0 else ''}
</section>"""

    # ---- the series, in display units
    def pts(key, conv=lambda v: v):
        return [(r["t"], None if r.get(key) is None else conv(r[key])) for r in rows]

    speed_pts = pts("speed", lambda v: records.to_dist(v, u))
    econ_raw = rolling_economy(rows) if s["has_maf"] else []
    econ_pts = [(t, records.to_econ(v, u)) for t, v in econ_raw]

    # ---- the IMA
    im = ima(rows)
    shown = [k for k in ("assist", "regen") if im.get(k, {}).get("supported")]
    bands = [(k, im[k]["spells"]) for k in shown]
    speed_note = None
    if shown:
        speed_note = ("Shaded spells are " + " and ".join(BAND_NAMES[k].lower() for k in shown)
                      + ", inferred from throttle, rpm and speed; see IMA at work below.")
    speed_chart = chart("c-speed", "Speed", u["speed"], SRC["speed"],
                        [("speed:Speed", speed_pts, "s1")], ctx,
                        floor_zero=True, bands=bands, note=speed_note)

    ima_html = ima_section(im, shown, u)

    closer = ""
    win = closer_window([sp for k in shown for sp in im[k]["spells"]], s["gaps"], t0, t1)
    if win:
        w0, w1 = win
        wctx = {"t0": w0, "t1": w1, "base": t0, "gaps": [], "xticks": time_ticks(w0, w1)}
        inside = [(t, v) for t, v in speed_pts if w0 <= t <= w1]
        wbands = [(k, [(max(a, w0), min(b, w1)) for a, b in im[k]["spells"] if b > w0 and a < w1])
                  for k in shown]
        zoom = [chart("z-speed", "Speed", u["speed"], SRC["speed"],
                      [("speed:Speed", inside, "s1")], wctx, floor_zero=True, bands=wbands)]
        if any(r.get("soc") is not None for r in rows):
            zoom.append(chart("z-pack", "Hybrid pack", "%", SRC["soc"],
                              [("soc:Hybrid pack", [(r["t"], r.get("soc")) for r in rows
                                                    if w0 <= r["t"] <= w1], "s1")],
                              wctx, bands=wbands, digits=1))
        closer = f"""<div class="closer">
      <h3>A closer look, {clock(w0)} to {clock(w1)}</h3>
      <p class="fine">The ten gap-free minutes with the most assist and regen, at a scale
      where each spell can be seen.</p>
      <div class="charts">{''.join(zoom)}</div>
    </div>"""

    pack_chart = ""
    if any(r.get("soc") is not None for r in rows):
        pack_chart = chart(
            "c-pack", "Hybrid pack, the whole drive", "%", SRC["soc"],
            [("soc:Hybrid pack", pts("soc"), "s1")], ctx, digits=1,
            note=("PID 0x5B is the generic OBD-II hybrid battery pack remaining life "
                  "reading, not the manufacturer's own battery gauge."))

    engine = [
        chart("c-rpm", "Engine speed", "rpm", SRC["rpm"],
              [("rpm:Engine speed", pts("rpm"), "s1")], ctx, floor_zero=True),
        chart("c-pedal", "Throttle and load", "%", SRC["throttle"],
              [("throttle:Throttle", pts("throttle"), "s1"),
               ("load:Load", pts("load"), "s2")], ctx, floor_zero=True),
        chart("c-cool", "Coolant", u["temp"], SRC["coolant"],
              [("coolant:Coolant", pts("coolant", lambda v: records.to_temp(v, u)), "s1")],
              ctx),
        chart("c-econ", "Economy, rolling one minute", u["econ"], SRC["econ"],
              [("econ:Economy", econ_pts, "s1")], ctx, floor_zero=True, digits=1,
              note=("Fuel over distance for the minute before each reading, while "
                    "moving, restarting after every gap. " + ECON_ASSUMES)),
    ]
    engine = [c for c in engine if c]

    # ---- hover data: every reading, in display units, relative seconds
    def r1(v, d=1):
        return None if v is None else round(v, d)
    econ_by_t = dict(econ_pts)
    data = {
        "t0": t0, "span": max(1.0, t1 - t0), "hole": HOLE_S,
        "t": [round(r["t"] - t0, 1) for r in rows],
        "speed": [r1(v, 0) for _, v in speed_pts],
        "rpm": [r1(r.get("rpm"), 0) for r in rows],
        "throttle": [r1(r.get("throttle"), 0) for r in rows],
        "load": [r1(r.get("load"), 0) for r in rows],
        "coolant": [r1(None if r.get("coolant") is None else records.to_temp(r["coolant"], u), 0)
                    for r in rows],
        "econ": [r1(econ_by_t.get(r["t"])) for r in rows],
        "soc": [r1(r.get("soc")) for r in rows],
        "units": {"speed": u["speed"], "rpm": "rpm", "throttle": "%", "load": "%",
                  "coolant": u["temp"], "econ": u["econ"], "soc": "%"},
    }
    data_json = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")

    heading = esc(label) if label else esc(date_long)
    # Separate pieces rather than one string with dots in it, so a phone wraps
    # between them instead of stranding a separator at the start of a line.
    sub = "".join(f"<span>{p}</span>" for p in (
        [f'<b>{esc(car)}</b>'] + ([esc(date_long)] if label else [])
        + [f"{clock(t0)} to {clock(t1)} {tz}"]))
    label_note = ('<p class="fine">The heading is the driver\'s own label for this drive; '
                  'nothing on the car reports a route.</p>') if label else ""
    made = generated or datetime.now().strftime("%Y-%m-%d %H:%M")

    sources = [
        ("Speed, engine speed, throttle, load, coolant", "OBD-II mode 01 PIDs 0x0D, 0x0C, "
         "0x11, 0x04, 0x05", "Measured by the car"),
        ("Hybrid pack", "OBD-II mode 01 PID 0x5B", "Measured by the car"),
        ("Mass air flow", "OBD-II mode 01 PID 0x10", "Measured by the car"),
        ("Distance", "Speed × time between readings, not across gaps"
         if s["km_source"] == "dist" else "The trip record OmaCar filed during the drive",
         "Calculated by OmaCar"),
        ("Fuel and economy", "Mass air flow ÷ 14.7 ÷ 745 g/L, × time", "Calculated by OmaCar"),
        ("Moving, idle, duration", f"Reading timestamps; moving means over "
         f"{records.to_dist(records.MOVING_KPH, u):.0f} {u['speed']}", "Timed by OmaCar"),
    ]
    if shown:
        sources.append(("Assist and regen bands", "Throttle, rpm and speed, checked "
                        "against the Hybrid pack reading", "Inferred by OmaCar"))
    source_rows = "".join(f"<tr><th scope=\"row\">{esc(a)}</th><td>{esc(b)}</td>"
                          f"<td>{esc(c)}</td></tr>" for a, b, c in sources)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(page_title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{esc(car)}: a drive on {esc(date_long)}, from OBD-II readings recorded by OmaCar.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap">
<style>{CSS}</style>
</head>
<body>
<main class="page">
  <header class="masthead">
    <p class="eyebrow"><span class="mark" aria-hidden="true"></span>OmaCar drive report</p>
    <h1>{heading}</h1>
    <p class="when">{sub}</p>
    {label_note}
  </header>

  <section class="headline" aria-label="The drive in numbers">
    {''.join(tiles)}
  </section>

  {coverage}

  <section class="road" aria-labelledby="road-h">
    <h2 id="road-h" class="section-h">On the road</h2>
    {speed_chart}
  </section>

  <section class="hybrid" aria-labelledby="hy-h">
    <h2 id="hy-h" class="section-h">The hybrid system</h2>
    {ima_html}
    {pack_chart}
    {closer}
  </section>

  <section class="engine" aria-labelledby="en-h">
    <h2 id="en-h" class="section-h">The engine</h2>
    <div class="charts">{''.join(engine)}</div>
  </section>

  <section class="method" aria-labelledby="me-h">
    <h2 id="me-h" class="section-h">Where each number comes from</h2>
    <div class="table-wrap"><table>
      <thead><tr><th scope="col">Figure</th><th scope="col">Source</th><th scope="col">Kind</th></tr></thead>
      <tbody>{source_rows}</tbody>
    </table></div>
    <p class="fine">Recorded by OmaCar on the car's tablet from the diagnostic port, about
    one reading a second. Nothing on this page is filled in between readings. Made
    {esc(made)} by OmaCar's drive report. It names the car by year, make and model only.</p>
  </section>
</main>
<script type="application/json" id="drive-data">{data_json}</script>
<script>{JS}</script>
</body>
</html>
"""


def ima_section(im, shown, u):
    if "why" in im:
        return (f'<div class="ima"><h3>IMA at work</h3><p class="fine">Left out of this '
                f'report: {esc(im["why"])}.</p></div>')
    closed = im["closed"]
    parts = []
    for k in ("assist", "regen"):
        d = im[k]
        n = len(d["spells"])
        if k == "assist":
            rule = (f"throttle at least {closed + ASSIST_OPEN_PTS:.0f}% with the revs "
                    f"climbing, for {SPELL_MIN_S:.0f} s or more")
            want, base_word = "fell", "falls"
        else:
            rule = (f"throttle closed and the car slowing from above "
                    f"{records.to_dist(REGEN_MIN_KPH, u):.0f} {u['speed']}, for "
                    f"{SPELL_MIN_S:.0f} s or more")
            want, base_word = "rose", "rises"
        name = "Assist" if k == "assist" else "Regen"
        if k in shown:
            parts.append(
                f'<div class="ima-kind band-{k}"><div class="ima-top"><span class="key band-{k}">'
                f'{name}</span><b>{n}</b> spells, {hm(d["seconds"])} in all</div>'
                f'<p>{esc(rule[0].upper() + rule[1:])}. The Hybrid pack reading {want} across '
                f'<b>{d["agree"]} of the {d["checked"]}</b>. Over every stretch of this drive '
                f'of the same length, it {base_word} in {d["baseline"]:.0%} of those where it '
                f'moved.</p></div>')
        else:
            why = (f"too few spells to check ({d['checked']})" if d["checked"] < PACK_MIN_SPELLS
                   else f"the Hybrid pack reading {want} across only {d['agree']} of "
                        f"{d['checked']}, which is not clearly more than chance on this drive")
            parts.append(f'<div class="ima-kind off"><div class="ima-top">{name}: not shown'
                         f'</div><p>{esc(why)}.</p></div>')
    lead = ("The car does not report motor power over OBD-II, so these are read from "
            "throttle, rpm and speed, which it does report, and kept only where the "
            "Hybrid pack reading moves the way assist and regen would move it.")
    return (f'<div class="ima"><h3>IMA at work</h3><p class="lede">{lead}</p>'
            f'<div class="ima-kinds">{"".join(parts)}</div></div>')


CSS = r"""
:root {
  color-scheme: light;
  --ground: #F2F4F3; --panel: #FFFFFF; --raise: #E8EDEB;
  --edge: #CFD8D5; --hair: #DFE6E4;
  --ink: #0B1214; --ink-2: #33424B; --dim: #3C4B4E; --faint: #61706F;
  --accent: #00708A; --accent-bg: rgba(0, 112, 138, .10);
  --s1: #0082A3; --s2: #5B4BC4;
  --ok: #1B7A4B;
  --band-assist: rgba(0, 130, 163, .26); --band-regen: rgba(27, 122, 75, .28);
  --gap: rgba(11, 18, 20, .07); --have: rgba(0, 112, 138, .55);
  --sans: "Inter", "Adwaita Sans", "Noto Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground: #0A1314; --panel: #0E1519; --raise: #16222A;
    --edge: #1E2A30; --hair: #272E32;
    --ink: #F6FCFF; --ink-2: #CAD4DA; --dim: #BDC7D1; --faint: #7C8B94;
    --accent: #22CDEC; --accent-bg: rgba(34, 205, 236, .12);
    --s1: #22CDEC; --s2: #8B7CF0;
    --ok: #9BDE68;
    --band-assist: rgba(34, 205, 236, .26); --band-regen: rgba(155, 222, 104, .28);
    --gap: rgba(246, 252, 255, .05); --have: rgba(34, 205, 236, .55);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground: #0A1314; --panel: #0E1519; --raise: #16222A;
  --edge: #1E2A30; --hair: #272E32;
  --ink: #F6FCFF; --ink-2: #CAD4DA; --dim: #BDC7D1; --faint: #7C8B94;
  --accent: #22CDEC; --accent-bg: rgba(34, 205, 236, .12);
  --s1: #22CDEC; --s2: #8B7CF0;
  --ok: #9BDE68;
  --band-assist: rgba(34, 205, 236, .26); --band-regen: rgba(155, 222, 104, .28);
  --gap: rgba(246, 252, 255, .05); --have: rgba(34, 205, 236, .55);
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
img { max-width: 100%; }
html { background: var(--ground); }
body {
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: var(--sans); font-size: 16px; line-height: 1.55;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
h1, h2, h3, p, ul, figure { margin: 0; }
ul { padding: 0; list-style: none; }
.page {
  max-width: 1120px; margin: 0 auto;
  padding-inline: 16px; padding-block: 28px 56px;
  display: flex; flex-direction: column; gap: 40px;
}
@media (min-width: 720px) { .page { padding-inline: 32px; padding-block: 44px 72px; gap: 52px; } }

.masthead { display: flex; flex-direction: column; gap: 10px; }
.eyebrow {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--mono); font-size: .78rem; letter-spacing: .14em;
  text-transform: uppercase; color: var(--accent);
}
.mark { width: 10px; height: 10px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-bg); flex: none; }
h1 { font-size: clamp(2rem, 6vw, 3.4rem); font-weight: 600; line-height: 1.05;
  letter-spacing: -.02em; text-wrap: balance; }
.when { display: flex; flex-wrap: wrap; gap: 2px 18px; font-family: var(--mono);
  color: var(--dim); font-size: .95rem; font-variant-numeric: tabular-nums; }
.when b { font-weight: 500; color: var(--ink); }

.headline {
  display: grid; gap: 1px; background: var(--hair);
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  border: 1px solid var(--hair); border-radius: 16px; overflow: hidden;
}
@media (max-width: 480px) { .headline { grid-template-columns: 1fr 1fr; } }
.stat { background: var(--panel); padding: 16px 16px 14px; display: flex;
  flex-direction: column; gap: 4px; min-width: 0; }
.stat-label { font-family: var(--mono); font-size: .72rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim); }
.stat-value { font-size: clamp(1.6rem, 4.4vw, 2.3rem); font-weight: 600; line-height: 1.1;
  font-variant-numeric: tabular-nums; letter-spacing: -.01em; overflow-wrap: anywhere; }
.stat-value small { font-size: .5em; font-weight: 500; color: var(--ink-2); letter-spacing: 0; }
.stat-sub { font-size: .85rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.stat-src { margin-top: auto; padding-top: 6px; font-size: .74rem; line-height: 1.4;
  color: var(--faint); }

h2, .section-h { font-size: 1.35rem; font-weight: 600; letter-spacing: -.01em;
  text-wrap: balance; }
section { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
section > p, .ima p, .coverage p { max-width: 68ch; color: var(--ink-2); }
.page .lede { color: var(--ink); }
.lede strong { font-size: 1.15em; font-variant-numeric: tabular-nums; }
.page .fine { font-size: .82rem; color: var(--faint); }

.strip { display: flex; flex-direction: column; gap: 6px; }
.strip svg { width: 100%; height: 14px; display: block; border-radius: 4px; }
.strip .have { fill: var(--have); }
.strip .gap { fill: var(--ground); }
.strip-ends { display: flex; justify-content: space-between; font-family: var(--mono);
  font-size: .75rem; color: var(--faint); font-variant-numeric: tabular-nums; }
.gaps { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 6px 20px; font-family: var(--mono); font-size: .82rem;
  font-variant-numeric: tabular-nums; color: var(--ink-2); }
.gaps li { display: flex; justify-content: space-between; gap: 12px;
  border-bottom: 1px solid var(--hair); padding-block: 4px; }
.gaps b { font-weight: 500; color: var(--ink); }

.chart { background: var(--panel); border: 1px solid var(--hair); border-radius: 14px;
  padding: 14px 14px 12px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.chart figcaption { display: flex; flex-direction: column; gap: 4px; }
.chart-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.chart h3 { font-size: 1rem; font-weight: 600; }
.unit { font-family: var(--mono); font-size: .78rem; color: var(--dim); }
.readout { margin-left: auto; font-family: var(--mono); font-size: .8rem; color: var(--ink);
  font-variant-numeric: tabular-nums; min-height: 1.2em; }
.source { font-size: .74rem; color: var(--faint); line-height: 1.4; }
.note { font-size: .8rem; color: var(--ink-2); max-width: 70ch; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: .78rem; color: var(--ink-2); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.key::before { content: ""; width: 14px; height: 3px; border-radius: 2px; background: var(--s1); }
.key.s2::before { background: var(--s2); }
.key.band-assist::before, .key.band-regen::before { height: 12px; width: 12px; border-radius: 3px; }
.key.band-assist::before { background: var(--band-assist); box-shadow: inset 0 0 0 1px var(--s1); }
.key.band-regen::before { background: var(--band-regen); box-shadow: inset 0 0 0 1px var(--ok); }

.plot { display: grid; grid-template-columns: 44px minmax(0, 1fr); grid-template-rows: auto auto; }
.ylab { position: relative; grid-row: 1; grid-column: 1; }
.ylab span { position: absolute; right: 8px; transform: translateY(-50%);
  font-family: var(--mono); font-size: .7rem; color: var(--faint);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.area { position: relative; grid-row: 1; grid-column: 2; height: 150px;
  touch-action: pan-y; cursor: crosshair; }
.road .area { height: 210px; }
.plot-svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.plot-svg .grid { stroke: var(--hair); stroke-width: 1; vector-effect: non-scaling-stroke; }
.plot-svg .gap { fill: var(--gap); }
.plot-svg .band.assist { fill: var(--band-assist); }
.plot-svg .band.regen { fill: var(--band-regen); }
.plot-svg .line { fill: none; stroke-width: 1.75; vector-effect: non-scaling-stroke;
  stroke-linejoin: round; stroke-linecap: round; }
.plot-svg .line.s1 { stroke: var(--s1); }
.plot-svg .line.s2 { stroke: var(--s2); }
.cross { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--ink);
  opacity: .45; pointer-events: none; }
.xlab { position: relative; grid-row: 2; grid-column: 2; height: 22px; }
.xlab span { position: absolute; top: 6px; transform: translateX(-50%);
  font-family: var(--mono); font-size: .7rem; color: var(--faint);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
@media (max-width: 560px) { .xlab span.odd { display: none; } }

.charts { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 880px) { .charts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

.ima { display: flex; flex-direction: column; gap: 12px; }
.ima h3, .closer h3 { font-size: 1.05rem; font-weight: 600; }
.closer { display: flex; flex-direction: column; gap: 10px; }
.ima-kinds { display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 720px) { .ima-kinds { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.ima-kind { border: 1px solid var(--hair); border-radius: 12px; padding: 12px 14px;
  background: var(--panel); display: flex; flex-direction: column; gap: 6px; }
.ima-kind p { font-size: .9rem; }
.ima-top { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  font-variant-numeric: tabular-nums; color: var(--ink-2); }
.ima-top .key { font-weight: 600; color: var(--ink); margin-right: 4px; }
.ima-top b { font-size: 1.2rem; color: var(--ink); }
.ima-kind.off { color: var(--faint); }

.table-wrap { overflow-x: auto; border: 1px solid var(--hair); border-radius: 12px;
  background: var(--panel); }
table { border-collapse: collapse; width: 100%; font-size: .86rem; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--hair);
  vertical-align: top; }
thead th { font-family: var(--mono); font-size: .7rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim); font-weight: 500; }
tbody th { font-weight: 500; }
td { color: var(--ink-2); }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
@media (max-width: 560px) { th, td { padding: 8px 10px; } }
"""

# The hover layer: one crosshair across every chart, and each chart's reading
# at that moment. It reads the nearest real reading and says so when the
# pointer is over a gap, rather than interpolating one.
JS = r"""
(function () {
  var el = document.getElementById("drive-data");
  if (!el) return;
  var d = JSON.parse(el.textContent);
  var charts = Array.prototype.slice.call(document.querySelectorAll(".chart"));
  function nearest(sec) {
    var lo = 0, hi = d.t.length - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (d.t[mid] < sec) lo = mid; else hi = mid; }
    return Math.abs(d.t[lo] - sec) <= Math.abs(d.t[hi] - sec) ? lo : hi;
  }
  function at(sec) {
    var date = new Date((d.t0 + sec) * 1000);
    return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0")
      + ":" + String(date.getSeconds()).padStart(2, "0");
  }
  function win(c) { return [+c.getAttribute("data-from") || 0, +c.getAttribute("data-span") || d.span]; }
  function show(sec) {
    var i = nearest(sec), off = Math.abs(d.t[i] - sec) > d.hole / 2;
    charts.forEach(function (c) {
      var cross = c.querySelector(".cross"), out = c.querySelector(".readout");
      var w = win(c), frac = (sec - w[0]) / w[1];
      if (frac < 0 || frac > 1) {
        if (cross) cross.hidden = true;
        if (out) out.textContent = "";
        return;
      }
      if (cross) { cross.hidden = false; cross.style.left = (frac * 100) + "%"; }
      if (!out) return;
      var keys = (c.getAttribute("data-keys") || "").split(","), digits = +c.getAttribute("data-digits") || 0;
      var bits = keys.map(function (k) {
        var v = off ? null : (d[k] || [])[i];
        return v === null || v === undefined ? "no reading" : v.toFixed(digits) + " " + d.units[k];
      });
      out.textContent = at(sec) + "  " + bits.join(" · ");
    });
  }
  function hide() {
    charts.forEach(function (c) {
      var cross = c.querySelector(".cross"), out = c.querySelector(".readout");
      if (cross) cross.hidden = true;
      if (out) out.textContent = "";
    });
  }
  charts.forEach(function (c) {
    var area = c.querySelector(".area");
    if (!area) return;
    area.addEventListener("pointermove", function (e) {
      var r = area.getBoundingClientRect(), w = win(c);
      show(w[0] + w[1] * Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
    });
    area.addEventListener("pointerleave", hide);
  });
})();
"""


# ------------------------------------------------------------------ the command

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="drive-report",
        description="One drive from the record, as a self-contained HTML page.")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--day", help="YYYY-MM-DD, local time")
    pick.add_argument("--from", dest="since", help="start, 'YYYY-MM-DD HH:MM' local")
    pick.add_argument("--trip", help="a trip record, by its start 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--until", help="end, with --from")
    ap.add_argument("--drive", type=int, help="which drive of the day, from --list")
    ap.add_argument("--list", action="store_true", help="list drives and stop")
    ap.add_argument("-o", "--out", help="the HTML file to write")
    ap.add_argument("--db", help="a database file to read instead of the current car's")
    ap.add_argument("--units", choices=("imperial", "metric"))
    ap.add_argument("--label", help="a heading of your own, shown as yours")
    ap.add_argument("--gap", type=float, default=GAP_S / 60.0,
                    help="minutes of silence that end a drive (default 10)")
    a = ap.parse_args(argv)

    if a.db:
        if not os.path.exists(a.db):
            print(f"  no such database: {a.db}", file=sys.stderr)
            return 2
        records.DB = os.path.abspath(a.db)
    db = records.connect()
    if db is None:
        print(f"  cannot open {records.DB}", file=sys.stderr)
        return 2
    units = records.units_for(a.units)
    gap_s = a.gap * 60.0
    try:
        if a.list:
            list_drives(db, day=a.day, gap_s=gap_s, units=units)
            return 0
        if not a.out:
            ap.error("say where to write the page: -o FILE")

        trip = None
        if a.trip:
            want = parse_when(a.trip)
            trip = next((t for t in trip_records(db) if abs(t["t0"] - want) < 60), None)
            if trip is None:
                print(f"  no trip record starts at {a.trip}; see --list", file=sys.stderr)
                return 1
            rows = load_rows(db, trip["t0"], trip["t1"])
        elif a.since:
            if not a.until:
                ap.error("--from needs --until")
            rows = load_rows(db, parse_when(a.since), parse_when(a.until))
        elif a.day:
            drives = drives_in(load_rows(db, *day_bounds(a.day)), gap_s=gap_s)
            if not drives:
                print(f"  no drive on {a.day}; see --list", file=sys.stderr)
                return 1
            if a.drive is None and len(drives) > 1:
                print(f"  {len(drives)} drives on {a.day}: pick one with --drive N",
                      file=sys.stderr)
                list_drives(db, day=a.day, gap_s=gap_s, units=units)
                return 1
            n = a.drive or 1
            if not 1 <= n <= len(drives):
                print(f"  there is no drive {n} on {a.day}; see --list", file=sys.stderr)
                return 1
            rows = drives[n - 1]
        else:
            ap.error("choose a drive: --day, --from/--until or --trip (or --list)")

        if not rows:
            print("  the car gave no readings in that span", file=sys.stderr)
            return 1
        if trip is None and not a.since:
            trip = contained_trip(db, rows)
        vehicle = records.vehicle(db)
    finally:
        db.close()

    page = build(rows, vehicle, units, trip=trip, label=a.label)
    bad = leaks(page, vehicle)
    if bad:
        # Name the field, not the value: the terminal may be the thing being
        # filmed.
        print("  not written: the page would name " + ", ".join(bad), file=sys.stderr)
        return 3
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, out)
    s = summarise(rows, trip=trip)
    print(f"  wrote {out}  ({os.path.getsize(out) / 1024:.0f} KB)  "
          f"{clock(s['t0'], '%Y-%m-%d %H:%M')}-{clock(s['t1'])}, "
          f"{records.to_dist(s['km'], units):.1f} {units['dist']}, "
          f"{s['coverage']:.0%} read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
