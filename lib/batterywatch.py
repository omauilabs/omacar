#!/usr/bin/env python3
"""The 12 V battery while the car is parked, and the tablet's own.

THE OWNER'S WORRY. A car left for days with a small drain on it comes back
flat, and the tablet that follows the ignition (lib/ignition.py) sleeps 30 s
after the car is parked -- so for most of a park nothing is awake to look.
This keeps the few readings there are, projects only what they can honestly
support, and says so when they cannot.

WHAT IS MEASURED, AND WHEN. Everything is the adapter's own `ATRV`: the
voltage at the OBD port, which is the car's permanent 12 V and is readable
with the ECUs off (the same reading lib/dtc.py already uses as its safety
floor). It is a question to the ADAPTER, not to the car: nothing is written
and no ECU is asked anything.

  * at ignition-off:  the charger goes (lib/ignition.py's signal), the car
    stops answering, and within the 30 s before the tablet sleeps, one ATRV.
    This is the post-drive voltage, still settling, so it is kept as the
    START of the park and never used as a resting value.
  * every 10 min while the tablet is awake and parked (a Stay on, or the
    ignition feature off): one ATRV.
  * at the next ignition-on: one ATRV as soon as the charger is back. If the
    car is not yet answering, that is the resting voltage after the whole
    park: the reading that matters most. If it already is, it is running
    voltage and is marked so, never used.
  * the tablet's own battery percentage, with each of those.

WHEN THE TABLET IS ASLEEP NOTHING IS MEASURED. A park of three days is two
readings: where it started and where it ended. That pair is shown, and a
trend is fitted across several PARKS (how far the rested voltage had fallen
against how long the car stood), never out of one pair.

HAND-OFF. The adapter belongs to the daemon; a reading goes through
connect.request_port() like every one-off command, with a short wait, and a
busy adapter simply means no reading this time. This never fights the
recorders for it.

    omacar battery-watch status     the projection, from the readings so far
    omacar battery-watch read       one ATRV now (through the hand-off)
    omacar battery-watch run        the loop (omacar-batterywatch.service)
    omacar battery-watch sim        a simulated multi-day park, labelled
                                    Simulated on screen, every time
    omacar battery-watch install    enable the service at login
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
SAMPLES = os.path.join(STATE_DIR, "battery-watch.jsonl")
SIM = os.path.join(STATE_DIR, "battery-watch-sim.jsonl")
STATE = os.path.join(STATE_DIR, "battery-watch.json")
CONFIG = os.path.join(CONFIG_DIR, "battery-watch.json")

KEEP = 4000                 # rows kept in the file
SIM_FRESH = 30              # s: the simulator file counts only while it is being written

DEFAULTS = {
    "enabled": True,
    "floor_v": 12.2,        # the app's own floor (lib/autodisc.py, lib/write.py)
    "critical_v": 11.9,     # the owner's number: below this it may not crank
    "settle_h": 2.0,        # open-circuit voltage relaxes after shutdown: ignore it
    "sample_min": 10,       # minutes between readings while awake and parked
    "offset_v": 0.0,        # add to every reading, to calibrate the adapter to a meter
    "alert_within_h": 24,   # warn when the projection reaches the floor inside this
}

VOLT_STEP = 0.1            # ATRV reads in tenths of a volt (ELM327 datasheet)
MIN_POINTS = 3             # readings before a within-park fit is offered
MIN_SPAN_H = 6.0           # ... spread over at least this long
MIN_PARKS = 3              # parks before an across-parks fit is offered
MIN_PARK_H = 12.0          # a park shorter than this tells nothing about drain
MIN_L_SPREAD_D = 1.0       # ... and the parks must differ in length by this much
MIN_PAIR_S = 1800          # slope pairs closer than this are noise
STEP_V = 0.15              # a single drop this big (over one 0.1 V reading step) may be a step, not a slope
STEP_MAX_H = 14.0          # ... if it happened inside one night
FAR_DAYS = 60              # beyond this, "more than two months"
QUIET_S = 4.0              # the car must have been silent this long before the first reading
OFF_WINDOW_S = 40.0        # the first reading must land inside this (the tablet sleeps at 30 s)
ON_WINDOW_S = 90.0         # how long the ignition-on reading is retried
HOURS_TO_FLOOR_PCT = 10    # the tablet is considered flat here (ignition.py's battery_floor)


# ---- config ------------------------------------------------------------------------

def config(path=None):
    cfg = dict(DEFAULTS)
    try:
        with open(path or CONFIG, encoding="utf-8") as f:
            mine = json.load(f)
        if isinstance(mine, dict):
            for k, dv in DEFAULTS.items():
                if k in mine and isinstance(mine[k], (int, float)) and not isinstance(mine[k], bool) \
                        and isinstance(dv, (int, float)) and not isinstance(dv, bool):
                    cfg[k] = float(mine[k]) if isinstance(dv, float) else int(mine[k])
            if isinstance(mine.get("enabled"), bool):
                cfg["enabled"] = mine["enabled"]
    except (OSError, ValueError):
        pass
    # The owner may accept a lower floor, but never one at or under the level
    # where the car may not crank, and never a sillier one than a charged battery.
    cfg["critical_v"] = max(10.5, min(12.0, cfg["critical_v"]))
    cfg["floor_v"] = max(cfg["critical_v"] + 0.1, min(12.8, cfg["floor_v"]))
    cfg["settle_h"] = max(0.0, min(24.0, cfg["settle_h"]))
    cfg["sample_min"] = max(2, min(120, cfg["sample_min"]))
    cfg["offset_v"] = max(-1.0, min(1.0, cfg["offset_v"]))
    cfg["alert_within_h"] = max(1, min(240, cfg["alert_within_h"]))
    return cfg


# ---- the record --------------------------------------------------------------------
# One JSON object per line:
#   t     seconds since the epoch
#   kind  "off"    ignition-off: a park begins (the voltage is still settling)
#         "parked" the tablet was awake and the car parked
#         "on"     ignition-on: the park ends
#   v     volts from ATRV, or null when it could not be read
#   rest  true when the car was not running (a resting value); false for a
#         post-drive or running voltage, which is never used for a trend
#   pct   the tablet's battery percent, or null
#   src   "adapter" (ATRV), "live" (the daemon's last CONTROL_MODULE_VOLTAGE,
#         when ATRV could not be had), "sim"

def load(path=None):
    rows = []
    try:
        with open(path or SAMPLES, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("t"), (int, float)) \
                        and r.get("kind") in ("off", "parked", "on"):
                    rows.append(r)
    except OSError:
        pass
    rows.sort(key=lambda r: r["t"])
    return rows


def append(row, path=None):
    path = path or SAMPLES
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = load(path)
    rows.append(row)
    rows = rows[-KEEP:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, path)


def parks(rows):
    """Group the rows into parks. A park opens at an "off" (or at the first
    awake reading when the loop started while already parked) and closes at an
    "on"; one that has not closed is open -- the car is still parked, or the
    tablet never saw it start.

    [{"t0", "t1" or None, "open", "implicit", "rows", "off_v", "on_v",
      "readings": [(t, v)], "pct": [(t, pct)]}]
    """
    out, cur = [], None
    for r in rows:
        k = r["kind"]
        if k == "off":
            if cur is not None:                   # an "on" was never seen for it
                cur["open"] = False
                out.append(cur)
            cur = {"t0": r["t"], "t1": None, "open": True, "implicit": False, "rows": [r]}
        elif k == "parked":
            if cur is None:
                cur = {"t0": r["t"], "t1": None, "open": True, "implicit": True, "rows": []}
            cur["rows"].append(r)
        elif cur is not None:                     # "on": the park ends here
            cur["rows"].append(r)
            cur["t1"], cur["open"] = r["t"], False
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    for p in out:
        rs = p["rows"]
        first = rs[0] if rs else None
        last = rs[-1] if rs else None
        p["off_v"] = first["v"] if first and first["kind"] == "off" else None
        p["on_v"] = last["v"] if last and last["kind"] == "on" and last.get("rest") else None
        p["readings"] = [(r["t"], r["v"]) for r in rs
                         if r.get("v") is not None and r["kind"] != "off" and r.get("rest")]
        p["pct"] = [(r["t"], r["pct"]) for r in rs if r.get("pct") is not None]
    return out


# ---- the arithmetic (pure) -----------------------------------------------------------

def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def theil_sen(points, min_dt=MIN_PAIR_S):
    """A robust slope through (t, y) points: the median of every pairwise slope.

    Returns {"slope", "q25", "q75", "pairs"} per SECOND, or None when no pair
    is far enough apart. One odd reading cannot move it; half of them would
    have to.
    """
    pts = sorted(points)
    slopes = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dt = pts[j][0] - pts[i][0]
            if dt >= min_dt:
                slopes.append((pts[j][1] - pts[i][1]) / dt)
    if not slopes:
        return None
    return {"slope": _quantile(slopes, 0.5), "q25": _quantile(slopes, 0.25),
            "q75": _quantile(slopes, 0.75), "pairs": len(slopes)}


def find_step(points):
    """The index after which a single overnight drop sits, or None.

    A cold night lowers the resting voltage in one go and does not repeat; fitted
    as part of a slope it looks like a drain. A step is one interval of at most
    STEP_MAX_H that holds at least 60% of the whole fall, of at least STEP_V,
    and falls at least four times faster than the rest of the record does.
    """
    pts = sorted(points)
    if len(pts) < 3:
        return None
    drops = [(pts[i][1] - pts[i + 1][1], pts[i + 1][0] - pts[i][0]) for i in range(len(pts) - 1)]
    total = sum(d for d, _ in drops)
    if total < STEP_V:
        return None
    i = max(range(len(drops)), key=lambda k: drops[k][0])
    d, dt = drops[i]
    if d < STEP_V or dt > STEP_MAX_H * 3600 or d < 0.6 * total:
        return None
    others = [(x / (t / 86400.0)) for k, (x, t) in enumerate(drops) if k != i and t > 0]
    rate = d / (dt / 86400.0)
    med = _quantile([abs(o) for o in others], 0.5) if others else 0.0
    return i if rate >= 4 * max(med, 0.01) else None


def fmt_span(hours):
    """"about 18 hours", "about 3 days": a span said the way a person would."""
    if hours is None:
        return ""
    if hours < 1.5:
        return "about an hour"
    if hours < 36:
        return f"about {round(hours)} hours"
    d = hours / 24.0
    if d < 10:
        n = round(d * 2) / 2
        return f"about {n:g} days"
    if d <= FAR_DAYS:
        return f"about {round(d)} days"
    return "more than two months"


def stood(hours):
    """How long a park stood, to a tenth of a day: "17 hours", "3.3 days"."""
    if hours < 36:
        return f"{round(hours)} hours"
    d = hours / 24.0
    return f"{d:.1f}".rstrip("0").rstrip(".") + " days" if d < 10 else f"{round(d)} days"


def ago(seconds):
    """"4 min ago", "3 hours ago", "2 days ago"."""
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    return fmt_span(seconds / 3600.0).replace("about ", "") + " ago"


def _eta(level, floor, rate_mid, rate_fast, rate_slow):
    """Hours to the floor from V/day rates (positive = falling). The range runs
    fast to slow; the slow end is open (None) when the gentlest plausible rate
    is zero or a rise."""
    rem = level - floor
    if rem <= 0:
        return {"mid": 0.0, "lo": 0.0, "hi": 0.0}
    h = lambda r: None if r is None or r <= 1e-6 else rem / r * 24.0  # noqa: E731
    return {"mid": h(rate_mid), "lo": h(rate_fast), "hi": h(rate_slow)}


def _range_text(eta):
    """"about 3 days (2 to 5 days)": the middle first, then how far it could be out."""
    mid, lo, hi = eta["mid"], eta["lo"], eta["hi"]
    if mid is None:
        return ""
    bare = lambda h: fmt_span(h).replace("about ", "")  # noqa: E731
    t = fmt_span(mid)
    if lo is not None and hi is None:
        t += f" (from {bare(lo)}, possibly much longer)"
    elif lo is not None and hi is not None and bare(lo) != bare(hi):
        t += f" ({bare(lo)} to {bare(hi)})"
    return t


def project_within(park, now, cfg):
    out = _project_within(park, now, cfg)
    if out.get("notes"):
        out["text"] += " (" + "; ".join(out["notes"]) + ".)"
    return out


def _project_within(park, now, cfg):
    """Fit THIS park's resting readings (awake and parked, past the settling hours)."""
    floor = cfg["floor_v"]
    t0 = park["t0"]
    pts = [(t, v + cfg["offset_v"]) for t, v in park["readings"]
           if t - t0 >= cfg["settle_h"] * 3600]
    notes = []
    step_i = find_step(pts)
    if step_i is not None:
        a, b = sorted(pts)[step_i], sorted(pts)[step_i + 1]
        notes.append(f"stepped down {a[1] - b[1]:.1f} V in one night; fitted from after it")
        pts = sorted(pts)[step_i + 1:]
    n = len(pts)
    span_h = (max(p[0] for p in pts) - min(p[0] for p in pts)) / 3600.0 if pts else 0.0
    base = {"basis": "this park", "n": n, "unit": "readings", "notes": notes, "floor": floor}
    if n < MIN_POINTS or span_h < MIN_SPAN_H:
        have = f"{n} reading{'s' if n != 1 else ''}"
        base.update(state="not-enough", level=pts[-1][1] if pts else None,
                    text=(f"Not enough readings to project: {have} since the first "
                          f"{cfg['settle_h']:g} h settled, over {span_h:.0f} h. It needs "
                          f"{MIN_POINTS} spread over at least {MIN_SPAN_H:g} h."))
        return base
    # Pairs further apart than a quarter of the span: tenth-of-a-volt steps
    # make slopes between near neighbours all zeros and spikes.
    ts = theil_sen(pts, min_dt=max(MIN_PAIR_S, span_h * 3600.0 / 4))
    if ts is None:
        base.update(state="not-enough", level=pts[-1][1], text="Readings too close together to tell a slope.")
        return base
    t_last = max(p[0] for p in pts)
    perday = 86400.0
    slope = ts["slope"] * perday
    level_last = _quantile([v - ts["slope"] * (t - t_last) for t, v in pts], 0.5)
    level_now = level_last + ts["slope"] * (now - t_last)
    span_d = span_h / 24.0
    base.update(level=round(level_now, 3), slope_per_day=round(slope, 4), span_h=round(span_h, 1))
    if level_now <= floor:
        base.update(state="below", eta_h={"mid": 0.0, "lo": 0.0, "hi": 0.0},
                    text=f"At {level_now:.1f} V it is already under the {floor:.1f} V floor.")
        return base
    if -slope * span_d < 2 * VOLT_STEP:
        # A fall of one or two reading steps cannot be told from rounding (a
        # true 0.01 V fall can read as a whole 0.1 V step, a true 0.19 V fall as
        # one). It does not mean no drain, it bounds it: the fastest hidden
        # drain is two steps over the span.
        bound = 2 * VOLT_STEP / span_d
        lo = (level_now - floor) / bound * 24.0
        base.update(state="steady", eta_h={"mid": None, "lo": lo, "hi": None},
                    text=(f"No fall the adapter can tell from rounding in {span_h:.0f} h (it reads in "
                          f"{VOLT_STEP:g} V steps). That rules out a drain faster than {bound:.2f} V a "
                          f"day, so the floor is at least {fmt_span(lo).replace('about ', '')} away. "
                          f"A slower drain needs days to show."))
        return base
    eta = _eta(level_now, floor, -slope, -ts["q25"] * perday, -ts["q75"] * perday)
    if eta["mid"] > FAR_DAYS * 24:
        base.update(state="steady", eta_h=eta, text="Falling very slowly: the floor is more than two months away.")
        return base
    base.update(state="ok", eta_h=eta,
                text=(f"Falling about {-slope:.2f} V a day: the {floor:.1f} V floor in "
                      f"{_range_text(eta)}, from {n} readings over {span_h:.0f} h."))
    return base


def project_across(done, now, cfg, current=None):
    """Fit how far the RESTING voltage had fallen when each park ended, against
    how long it had stood. Needs several parks of different lengths: a single
    pair says nothing about a rate (the start is still settling).

    done: closed parks. current: an open park, to say where it is now on the line.
    """
    floor = cfg["floor_v"]
    pts, used = [], []
    for p in done:
        if p["on_v"] is None or p["t1"] is None:
            continue
        L = (p["t1"] - p["t0"]) / 86400.0
        if L * 24 < MIN_PARK_H:
            continue
        pts.append((L, p["on_v"] + cfg["offset_v"]))
        used.append(p)
    spread = (max(x for x, _ in pts) - min(x for x, _ in pts)) if pts else 0.0
    base = {"basis": "past parks", "n": len(pts), "unit": "parks", "notes": [], "floor": floor}
    if len(pts) < MIN_PARKS or spread < MIN_L_SPREAD_D:
        base.update(state="not-enough", level=None,
                    text=(f"Not enough parks to project: {len(pts)} of at least "
                          f"{MIN_PARKS} stood {MIN_PARK_H:g} h or more"
                          + ("" if len(pts) < MIN_PARKS or spread >= MIN_L_SPREAD_D else
                             f", and their lengths differ by only {spread:.1f} days")
                          + ". Between a start and a finish nothing is measured while the tablet sleeps."))
        return base
    ts = theil_sen([(x * 86400.0, y) for x, y in pts], min_dt=3600)
    if ts is None:
        base.update(state="not-enough", level=None, text="Parks too alike in length to tell a rate.")
        return base
    slope = ts["slope"] * 86400.0                                 # V per day parked
    v0 = _quantile([y - ts["slope"] * x * 86400.0 for x, y in pts], 0.5)   # rested level at day 0
    base.update(slope_per_day=round(slope, 4), start_v=round(v0, 3), level=round(v0, 3))
    where = None
    if current is not None:
        L_now = (now - current["t0"]) / 86400.0
        where = v0 + slope * L_now
        base["level"] = round(where, 3)
        base["parked_days"] = round(L_now, 2)
    level = where if where is not None else v0
    if -slope * max(x for x, _ in pts) < 2 * VOLT_STEP:
        bound = 2 * VOLT_STEP / max(x for x, _ in pts)
        lo = max(0.0, level - floor) / bound * 24.0
        base.update(state="steady", eta_h={"mid": None, "lo": lo, "hi": None},
                    text=(f"Across {len(pts)} parks your 12 V showed no fall the adapter "
                          f"can tell from rounding, up to {max(x for x, _ in pts):.0f} days parked."))
        return base
    eta = _eta(level, floor, -slope, -ts["q25"] * 86400.0, -ts["q75"] * 86400.0)
    if level <= floor:
        base.update(state="below", eta_h={"mid": 0.0, "lo": 0.0, "hi": 0.0},
                    text=f"On the trend of {len(pts)} parks it is already under the {floor:.1f} V floor.")
        return base
    who = "from here" if where is not None else f"from a rested {v0:.1f} V"
    base.update(state="ok", eta_h=eta,
                text=(f"Across {len(pts)} parks your 12 V loses about {-slope:.2f} V a day "
                      f"parked; {who} that reaches {floor:.1f} V in {_range_text(eta)}."))
    return base


def project(ps, now, cfg):
    """The best honest answer: this park's own readings when there are enough,
    else the trend across parks, else "not enough" with what is missing."""
    cur = ps[-1] if ps and ps[-1]["open"] else None
    done = [p for p in ps if not p["open"]]
    a = project_across(done, now, cfg, current=cur)
    if cur is not None:
        w = project_within(cur, now, cfg)
        if w["state"] == "steady" and a["state"] in ("ok", "steady") and a.get("eta_h", {}).get("mid"):
            # Nothing visible in a few awake hours; the parks say more.
            a["also"] = w["text"]
            return a
        if w["state"] != "not-enough":
            return w
    if a["state"] != "not-enough":
        return a
    if cur is not None:
        w = project_within(cur, now, cfg)
        w["text"] = w["text"] + " " + a["text"]
        return w
    return a


def tablet(park, pct_now, now, cfg=None):
    """The tablet's battery: where it is and, when two readings an hour or more
    apart show a fall, how long it lasts at that rate."""
    out = {"pct": pct_now, "state": "none", "text": "", "drain_pct_per_h": None, "hours_left": None}
    if pct_now is None:
        out["text"] = "No battery found to read."
        return out
    pts = list(park["pct"]) if park else []
    if park and park["open"]:
        pts.append((now, pct_now))
    if len(pts) < 2:
        out["state"] = "not-enough"
        out["text"] = "Not enough readings to say how fast it drains."
        return out
    span_h = (max(t for t, _ in pts) - min(t for t, _ in pts)) / 3600.0
    ts = theil_sen([(t, p) for t, p in pts], min_dt=3600)
    if ts is None or span_h < 2:
        out["state"] = "not-enough"
        out["text"] = f"Not enough readings to say how fast it drains: {len(pts)} over {span_h:.1f} h, it needs 2 hours apart."
        return out
    rate = -ts["slope"] * 3600.0                      # percent per hour, falling = positive
    out["n"] = len(pts)
    if park is not None and not park["open"]:
        # The car has started and the charger is back: what it did while parked,
        # in the past tense, and no runtime (it is charging now).
        drop = max(p for _, p in pts) - min(p for _, p in pts)
        out.update(state="ok" if rate > 0.02 else "steady", drain_pct_per_h=round(max(rate, 0.0), 2))
        out["text"] = (f"Lost {drop:g} % over the last {stood(span_h)} parked "
                       f"({max(rate, 0.0):.2f} % an hour)." if rate > 0.02 else
                       f"No fall over the last {stood(span_h)} parked.")
        return out
    if rate <= 0.02:
        out["state"] = "steady"
        out["text"] = f"No fall in {span_h:.0f} h ({len(pts)} readings)."
        return out
    fast, slow = -ts["q25"] * 3600.0, -ts["q75"] * 3600.0
    left = lambda r: None if r is None or r <= 0.02 else max(0.0, pct_now - HOURS_TO_FLOOR_PCT) / r  # noqa: E731
    out.update(state="ok", drain_pct_per_h=round(rate, 2),
               hours_left={"mid": left(rate), "lo": left(fast), "hi": left(slow)})
    mid = out["hours_left"]["mid"]
    out["text"] = (f"Losing about {rate:.1f} % an hour; {fmt_span(mid)} to "
                   f"{HOURS_TO_FLOOR_PCT} %, from {len(pts)} readings over {span_h:.0f} h.")
    return out


# ---- the verdict on screen and in the outbox -------------------------------------------

def latest_rest(rows):
    for r in reversed(rows):
        if r.get("v") is not None and r.get("rest") and r["kind"] != "off":
            return r
    return None


def headline(rows, ps, proj, cfg, now):
    """{"tone": "ok"|"warn"|"bad"|"", "text", "sub"}: what Home says."""
    floor, crit = cfg["floor_v"], cfg["critical_v"]
    r = latest_rest(rows)
    if r is None:
        return {"tone": "", "text": "No resting reading yet",
                "sub": "One is taken when the car is parked and again when it starts."}
    v = r["v"] + cfg["offset_v"]
    when = "when you started" if r["kind"] == "on" else ago(now - r["t"])
    last = next((p for p in reversed(ps) if p["t1"] is not None), None)
    stood_s = ""
    if r["kind"] == "on" and last is not None and (last["t1"] - last["t0"]) >= 12 * 3600:
        stood_s = f" after {stood((last['t1'] - last['t0']) / 3600.0)} parked"
    if v < crit:
        return {"tone": "bad", "text": f"{v:.1f} V {when}{stood_s}: under {crit:.1f} V",
                "sub": "It may not start the car. Charge it or have a jump ready."}
    if v < floor:
        return {"tone": "warn", "text": f"Your 12 V was {v:.1f} V {when}{stood_s}",
                "sub": f"Under the {floor:.1f} V floor: consider a drive or a maintainer."}
    eta = (proj.get("eta_h") or {}).get("mid")
    if proj.get("state") == "ok" and eta is not None and eta < cfg["alert_within_h"]:
        return {"tone": "warn", "text": f"{v:.1f} V, falling: the floor in {fmt_span(eta).replace('about ', '')}",
                "sub": proj["text"]}
    return {"tone": "ok", "text": f"{v:.1f} V {when}{stood_s}",
            "sub": f"Above the {floor:.1f} V floor."}


def alert_for(rows, ps, proj, cfg, now):
    """(level, park t0, title, body) when the outbox should hear about it, or None."""
    r = latest_rest(rows)
    cur = ps[-1] if ps else None
    if r is None or cur is None:
        return None
    t0 = cur["t0"]
    v = r["v"] + cfg["offset_v"]
    if v < cfg["critical_v"]:
        return ("critical", t0, "12 V battery critical",
                f"{v:.1f} V at the OBD port, under {cfg['critical_v']:.1f} V. The car may not start; "
                "charge the battery or have a jump ready.")
    if v < cfg["floor_v"]:
        return ("warn", t0, "12 V battery low",
                f"{v:.1f} V, under the {cfg['floor_v']:.1f} V floor. Consider a drive or a maintainer.")
    eta = (proj.get("eta_h") or {}).get("mid")
    if cur["open"] and proj.get("state") == "ok" and eta is not None and eta < cfg["alert_within_h"]:
        return ("warn", t0, "12 V battery running down",
                f"{v:.1f} V and falling; the {cfg['floor_v']:.1f} V floor in {fmt_span(eta).replace('about ', '')}. "
                + proj["text"])
    return None


def summary(rows, cfg=None, now=None, pct_now=None, source="adapter"):
    """Everything the screen needs, from the rows alone. Never raises."""
    cfg = cfg or config()
    now = time.time() if now is None else now
    ps = parks(rows)
    if not rows:
        return {"present": False, "source": source, "floor_v": cfg["floor_v"],
                "critical_v": cfg["critical_v"], "tablet": tablet(None, pct_now, now, cfg),
                "settle_h": cfg["settle_h"], "step_v": VOLT_STEP}
    proj = project(ps, now, cfg)
    cur = ps[-1] if ps and ps[-1]["open"] else None
    r = latest_rest(rows)
    last = rows[-1]
    hist = [{"t": x["t"], "v": round(x["v"] + cfg["offset_v"], 2), "kind": x["kind"],
             "rest": bool(x.get("rest"))}
            for x in rows[-600:] if x.get("v") is not None]
    return {
        "present": True, "source": source, "at": now,
        "floor_v": cfg["floor_v"], "critical_v": cfg["critical_v"], "settle_h": cfg["settle_h"],
        "step_v": VOLT_STEP, "offset_v": cfg["offset_v"],
        "parked": cur is not None,
        "park": None if cur is None else {"t0": cur["t0"], "hours": round((now - cur["t0"]) / 3600.0, 1),
                                          "implicit": cur["implicit"], "readings": len(cur["readings"])},
        "latest": None if r is None else {"t": r["t"], "v": round(r["v"] + cfg["offset_v"], 2),
                                          "kind": r["kind"], "age": max(0.0, now - r["t"])},
        "last_row": {"t": last["t"], "kind": last["kind"], "age": max(0.0, now - last["t"])},
        "projection": proj,
        "tablet": tablet(cur or (ps[-1] if ps else None), pct_now, now, cfg),
        "headline": headline(rows, ps, proj, cfg, now),
        "history": hist,
        "parks": [{"t0": p["t0"], "t1": p["t1"], "open": p["open"],
                   "hours": round(((p["t1"] or now) - p["t0"]) / 3600.0, 1),
                   "off_v": p["off_v"], "on_v": p["on_v"], "readings": len(p["readings"])}
                  for p in ps[-8:]],
    }


# ---- files and the world ----------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _sim_live(now):
    try:
        return now - os.stat(SIM).st_mtime < SIM_FRESH
    except OSError:
        return False


def read_public():
    """For the API. {"present": False} until there is a reading; the simulator,
    while it is running, replaces the real record on screen and says Simulated."""
    try:
        now = time.time()
        cfg = config()
        if _sim_live(now):
            rows = load(SIM)
            pct = rows[-1].get("pct") if rows else None
            s = summary(rows, cfg, now, pct, source="sim")
            s["running"] = True
            return s
        pct = None
        try:
            import ignition
            pct = ignition.supplies()["battery"]
        except Exception:                                      # noqa: BLE001
            pass
        s = summary(load(), cfg, now, pct)
        hb = _read_json(STATE) or {}
        s["running"] = now - float(hb.get("at", 0)) < 60
        return s
    except Exception as e:                                      # noqa: BLE001 -- the API must not 500
        return {"present": False, "error": str(e)}


def adapter_volts(timeout=4.0):
    """One ATRV, through the same hand-off as every one-off command. None when
    there is no adapter, it is busy, or it will not answer. Changes nothing on
    the adapter and asks the car nothing."""
    try:
        import connect
        import elm as elmlib
        import dtc as dtclib
        port, kind = connect.resolve()
        if not port or kind not in ("wired", "override"):
            return None
        leased = False
        try:
            leased = connect.request_port(port, timeout=timeout)
            if not leased:
                return None
            el = elmlib.Elm(port, baudrate=connect.link_baud(port))
            try:
                return dtclib.battery_volts(el)
            finally:
                try:
                    el.close()
                except Exception:                               # noqa: BLE001
                    pass
        finally:
            if leased:
                connect.release_port()
    except Exception:                                           # noqa: BLE001
        return None


def live_volts(now=None):
    """The daemon's last CONTROL_MODULE_VOLTAGE and how old, or (None, None)."""
    now = time.time() if now is None else now
    try:
        import ignition
        snap = _read_json(ignition.LIVE) or {}
        v = (snap.get("values") or {}).get("CONTROL_MODULE_VOLTAGE")
        t = snap.get("t")
        if isinstance(v, (int, float)) and isinstance(t, (int, float)):
            return float(v), max(0.0, now - t)
    except Exception:                                           # noqa: BLE001
        pass
    return None, None


# ---- the loop (impure; the decisions are the functions above) ---------------------------

def _log(line):
    try:
        import power
        power.note("battery-watch: " + line)
    except Exception:                                           # noqa: BLE001
        pass


def tell(rows, cfg, now, st):
    """Put a warning in the outbox once per park per level, and remember it."""
    ps = parks(rows)
    proj = project(ps, now, cfg)
    a = alert_for(rows, ps, proj, cfg, now)
    if not a:
        return
    level, t0, title, body = a
    rank = {"warn": 1, "critical": 2}
    told = st.setdefault("told", {})
    if rank[level] <= told.get(str(int(t0)), 0):
        return
    import notify
    notify.send("battery-low", title, body, level, key=f"battery-low-{int(t0)}-{level}", now=now)
    told[str(int(t0))] = rank[level]
    for k in sorted(told)[:-20]:
        told.pop(k, None)


def run(tick=1.0):
    import ignition
    st = _read_json(STATE) or {}
    st.pop("at", None)
    prev = None                  # was the charger on at the last tick: True/False/None
    pending_off = None           # when the charger went away, until its first reading
    pending_on = None            # when it came back, until its first reading
    quiet_since = None
    off_live = (None, None)      # the daemon's last voltage when the charger went
    last_sample = 0.0
    _log("loop started")
    first = True
    while True:
        cfg = config()
        now = time.time()
        pw = ignition.supplies()
        car = ignition.car(now=now)
        powered = pw["powered"]
        pct = pw["battery"]

        def add(kind, v, rest, src, t=None):
            row = {"t": round(t or time.time(), 1), "kind": kind,
                   "v": None if v is None else round(float(v), 2),
                   "rest": bool(rest), "pct": pct, "src": src}
            append(row)
            tell(load(), cfg, row["t"], st)
            _log(f"{kind}: {row['v']} V ({src}), tablet {pct}%")

        if not cfg["enabled"]:
            _write_json(STATE, dict(st, at=now))
            time.sleep(tick)
            continue

        answering = car["answering"] and (car["age"] is not None and car["age"] < 15)
        newest = (load() or [None])[-1] if (first or prev is False or pending_off is not None) else None
        if powered is True:
            quiet_since = None
            if pending_off is not None:
                if now - pending_off > OFF_WINDOW_S:
                    # It slept before the first reading (or the loop was frozen
                    # by suspend): the park's start is the daemon's last voltage,
                    # labelled as that, never a resting value.
                    add("off", off_live[0], False, "live", t=pending_off)
                    newest = (load() or [None])[-1]
                pending_off = None
            if (first or prev is False) and newest is not None and newest["kind"] in ("off", "parked"):
                pending_on = now
        elif powered is False:
            if prev is True:
                pending_off = now
                off_live = live_volts(now)
            quiet_since = (quiet_since or now) if not answering else None
        first = False
        prev = powered if powered is not None else prev

        # The first reading after ignition-off: the car must have gone quiet (the
        # ignition loop vetoes a sleep while it answers, and a reading taken
        # while it still answers is the running voltage), and it must land
        # before the tablet sleeps.
        if pending_off is not None:
            waited = now - pending_off
            if waited > OFF_WINDOW_S:
                # Too late (the tablet slept through it, or it never went quiet):
                # keep the daemon's last voltage as the park's start, labelled.
                v, age = off_live
                add("off", v, False, "live", t=pending_off)
                pending_off = None
                last_sample = now
            elif quiet_since is not None and now - quiet_since >= QUIET_S:
                v = adapter_volts()
                if v is not None:
                    add("off", v, False, "adapter", t=pending_off)
                    pending_off = None
                    last_sample = now        # the next one is sample_min from here

        # The first reading after ignition-on, as soon as the charger is back.
        if pending_on is not None:
            v = adapter_volts()
            if v is not None or now - pending_on > ON_WINDOW_S:
                resting = not answering
                add("on", v, resting and v is not None, "adapter", t=now)
                pending_on = None
                last_sample = now

        # Awake and parked: a reading every sample_min minutes.
        awake_parked = (powered is False and pending_off is None and quiet_since is not None
                        and now - quiet_since >= QUIET_S)
        if awake_parked and now - last_sample >= cfg["sample_min"] * 60:
            last_sample = now
            v = adapter_volts()
            if v is not None:
                add("parked", v, True, "adapter")

        _write_json(STATE, dict(st, at=time.time()))
        time.sleep(tick)


# ---- the simulator -----------------------------------------------------------------------

def sim_rows(now, scenario="drain", seed=7):
    """A believable record: five earlier parks that were slept through (an off
    and an on each, nothing between), then the current park kept awake for
    eighteen hours by Stay on, a reading every ten minutes. Volts in the
    adapter's tenths. `scenario`: "healthy" (barely falls anywhere), "drain"
    (earlier parks fine; this one is falling fast, as if something were left
    on) "step" (a cold night's drop half way, then steady) or "low" (the car has just
    started after a park that took it under the floor)."""
    import random
    rnd = random.Random(seed)
    q = lambda v: round(round(v / VOLT_STEP) * VOLT_STEP, 2)   # noqa: E731
    past, now_rate = {"healthy": (0.05, 0.012), "drain": (0.07, 0.5),
                      "step": (0.012, 0.012), "low": (0.06, 0.0)}[scenario]
    day = 86400.0
    rest = lambda el, rate: 12.66 + 0.2 * math.exp(-el * 24 / 1.4) - rate * el  # noqa: E731
    rows = []
    # (days ago the park began, how long it stood in days)
    for ago, length in ((24.0, 0.7), (19.5, 3.1), (13.0, 1.4), (9.0, 5.2), (3.0, 0.6)):
        t0 = now - ago * day
        rows.append({"t": round(t0, 1), "kind": "off", "v": q(13.0 + rnd.uniform(-0.1, 0.1)),
                     "rest": False, "pct": 94 - int(rnd.uniform(0, 8)), "src": "sim"})
        rows.append({"t": round(t0 + length * day, 1), "kind": "on",
                     "v": q(rest(length, past) + rnd.uniform(-0.03, 0.03)),
                     "rest": True, "pct": 88 - int(rnd.uniform(0, 6)), "src": "sim"})
    if scenario == "low":
        # The car has just started after a long park that took it under the floor:
        # the last park is 3.2 days, ending at 12.1 V, a minute ago.
        rows = rows[:-2]                      # drop the "3 days ago" park
        t0 = now - 3.3 * day
        rows += [{"t": round(t0, 1), "kind": "off", "v": 13.0, "rest": False, "pct": 92, "src": "sim"},
                 {"t": round(now - 90, 1), "kind": "on", "v": 12.1, "rest": True, "pct": 84, "src": "sim"}]
        return rows
    held = 18.0
    t0 = now - held * 3600.0
    rows.append({"t": round(t0, 1), "kind": "off", "v": q(13.0), "rest": False,
                 "pct": 91, "src": "sim"})
    t = t0 + 600.0
    while t < now:
        el = (t - t0) / day
        v = rest(el, now_rate)
        if scenario == "step" and t - t0 > 7 * 3600:
            v -= 0.2
        pct = 91.0 - 0.6 * (t - t0) / 3600.0
        rows.append({"t": round(t, 1), "kind": "parked", "v": q(v + rnd.uniform(-0.02, 0.02)),
                     "rest": True, "pct": int(round(pct)), "src": "sim"})
        t += 600.0
    return rows


def sim(seconds=None, scenario="drain"):
    """Write a simulated record to its own file (the real one is never touched)
    and keep it fresh; the app shows it, labelled Simulated, while this runs."""
    start = time.time()
    rows = sim_rows(start, scenario)
    try:
        while seconds is None or time.time() - start < seconds:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = SIM + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            os.replace(tmp, SIM)
            time.sleep(5.0)
            # Its clock keeps moving: a reading every ten minutes of sim time is
            # not worth faking in real time, so the file is rewritten as-is and
            # only its age is kept fresh.
    finally:
        try:
            os.unlink(SIM)
        except OSError:
            pass


# ---- telling ------------------------------------------------------------------------------

def show_status():
    s = read_public()
    if not s.get("present"):
        print("\n  no readings yet -- one is taken when the car is parked and again when it starts.\n")
        return 0
    print()
    h = s["headline"]
    print(f"  {h['text']}   [{h['tone'] or 'no verdict'}]\n  {h['sub']}")
    p = s["projection"]
    print(f"  projection  {p['state']}: {p['text']}")
    t = s["tablet"]
    print(f"  tablet      {t['pct']}%  {t['text']}")
    print(f"  floor       {s['floor_v']:.1f} V (critical {s['critical_v']:.1f} V)   source: {s['source']}")
    for q in s["parks"]:
        a = time.strftime("%m-%d %H:%M", time.localtime(q["t0"]))
        print(f"  park {a}  {q['hours']:>6.1f} h  start {q['off_v']}  end {q['on_v']}  "
              f"{q['readings']} resting readings{'  (open)' if q['open'] else ''}")
    print()
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        return show_status()
    if what == "read":
        v = adapter_volts(timeout=12.0)
        print(f"  {v:.1f} V at the OBD port" if v is not None
              else "  no reading: no adapter, or it is busy")
        return 0 if v is not None else 1
    if what == "run":
        run()
        return 0
    if what == "sim":
        scenario = next((a for a in argv[1:] if a in ("healthy", "drain", "step", "low")), "drain")
        secs = next((float(a) for a in argv[1:] if a.replace(".", "", 1).isdigit()), None)
        print(f"  simulating ({scenario}); the app says Simulated while this runs. Ctrl-C stops it.")
        try:
            sim(secs, scenario)
        except KeyboardInterrupt:
            pass
        return 0
    print(f"  omacar battery-watch: no such thing as '{what}'.\n"
          "  status | read | run | sim [healthy|drain|step|low] [seconds] | install", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
