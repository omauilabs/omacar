#!/usr/bin/env python3
"""Hybrid coaching: a score for each drive, worked out after it is over.

    omacar coach status     what coach.json says now (the last drive, the trend)
    omacar coach update     score drives the log has not been scored yet
    omacar coach sim        write 20 SIMULATED drives (labelled Simulated, always)
    omacar coach clear      remove coach.json

WHAT THIS IS. The daemon already records, for every drive, a sample a second or
so: speed, throttle, rpm and the hybrid pack's charge (PID 0x5B), and a trips
row with the fuel figure. This reads those after the drive is parked and turns
them into one number from 0 to 100, three sub-scores, two facts and one hint.
Nothing here talks to the car. It reads telemetry.db read-only and writes one
state file of its own, coach.json, which can be deleted at any time.

WHAT IS MEASURED AND WHAT IS NOT. Every number carries `source`:

  measured   speed, throttle and rpm, straight from the car's PIDs, and what is
             worked out from them with no assumption in it (jerk, how long the
             foot was off the pedal, how long the engine was off at a stop)
  estimated  anything that rests on an assumption of ours: the energy that went
             back into the pack is the CHARGE READING (measured) times an
             assumed pack size (PACK_WH_PER_PERCENT), and the fuel figure is
             worked out from air flow at a fixed air-fuel ratio

There is no brake-pedal signal, no pack current and no motor torque read from
this car yet (doc/design/2026-10-10-hybrid-gauges.md), so "braked late" means
"slowed hard with the throttle closed", never "the brake pedal went down". See
doc/coach.md for the table.

A missing input never becomes a zero. A sub-score with too little data is None
with `why` saying what is missing, the headline is then PARTIAL (worked out
from the sub-scores there are), and with fewer than two it is None.

The scoring is pure (`score_drive`): lists in, a dict out, no clock, no files.
"""

import bisect
import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
STATE = os.path.join(STATE_DIR, "coach.json")

# ---- this app's own thresholds (none of them is a Honda figure) --------------

GAP_S = 10.0            # samples further apart than this are a hole, not a trend
MOVING_KPH = 3.0
MIN_MOVING_S = 120      # a drive shorter than this is not scored
MIN_KM = 0.8
CAR_KG = 1250.0         # the CR-Z with a driver in it; assumed
PACK_WH_PER_PERCENT = 5.8   # 1 point of the charge reading, in Wh; ASSUMED:
                        # the IMA pack is ~580 Wh nominal and we do not know how
                        # much of it the 0x5B percentage spans
SOC_LAG_S = 4.0         # the charge reading trails the energy by a few seconds
REGEN_FULL = 0.40       # recovering this share of the energy shed is a 100
MIN_SHED_WH = 15.0      # less kinetic energy than this shed: no regen verdict
THROTTLE_OFF = 5.0      # % pedal: a closed throttle
HARD_DECEL = 2.0        # m/s2, smoothed: a hard slowdown
HARD_ACCEL = 2.2        # m/s2, smoothed: a hard launch
HARD_MIN_KPH = 25.0     # a hard slowdown counts from at least this speed
JERK_BEST, JERK_WORST = 0.15, 0.9     # RMS jerk (m/s3) that scores 100 / 0
TSLEW_BEST, TSLEW_WORST = 5.0, 30.0   # RMS throttle change (%/s) that scores 100 / 0
GLIDE_FULL = 0.35       # this share of moving time with the foot off is a 100
GLIDE_MIN_KPH = 20.0
GLIDE_MAX_DECEL = 0.8   # m/s2: slowing harder than this is braking, not gliding
MIN_STOP_S = 20         # stopped for less than this: no auto-stop figure
SIMILAR = (0.5, 2.0)    # past drives within this ratio of the distance
MIN_SIMILAR = 3
WEIGHTS = {"regen": 0.40, "smooth": 0.35, "glide": 0.25}
KEEP = 60               # drives kept in coach.json
TREND = 20              # drives in the trend

LABEL = {
    "regen": "Regen capture",
    "smooth": "Smoothness",
    "glide": "Gliding",
}


# ---- small helpers ----------------------------------------------------------

def _num(x):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _ramp(x, worst, best):
    """100 at `best`, 0 at `worst`, straight between (either direction)."""
    return _clamp((x - worst) / (best - worst) * 100.0)


def _series(rows, key):
    pts = []
    for r in rows:
        t, v = _num(r.get("t")), _num(r.get(key))
        if t is not None and v is not None:
            pts.append((t, v))
    pts.sort()
    return pts


def _at(pts, ts, t, hold=False):
    """The series at time t: linear between neighbours, or the last value held.
    None when the neighbours are further apart than GAP_S (a hole)."""
    i = bisect.bisect_right(ts, t)
    if i == 0:
        return pts[0][1] if ts and abs(ts[0] - t) < 1e-6 else None
    a = pts[i - 1]
    if t - a[0] > GAP_S:
        return None
    if i == len(pts):
        return a[1] if t - a[0] < 1e-6 or hold else None
    b = pts[i]
    if b[0] - a[0] > GAP_S:
        return None
    if hold:
        return a[1]
    return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0])


def _grid(rows):
    """The drive on a one-second grid: t0, and parallel lists
    (kph, thr, rpm, soc), each None where the car did not say."""
    t_all = [t for t in (_num(r.get("t")) for r in rows) if t is not None]
    if len(t_all) < 2:
        return None
    t0, t1 = math.ceil(min(t_all)), math.floor(max(t_all))
    if t1 - t0 < 2:
        return None
    out = {}
    for key, hold in (("speed", False), ("throttle", False), ("rpm", True),
                      ("soc", False)):
        pts = _series(rows, key)
        ts = [p[0] for p in pts]
        out[key] = [(_at(pts, ts, t, hold) if pts else None)
                    for t in range(t0, t1 + 1)]
    return t0, out["speed"], out["throttle"], out["rpm"], out["soc"]


def _smooth3(v):
    out = [None] * len(v)
    for i in range(1, len(v) - 1):
        if v[i - 1] is not None and v[i] is not None and v[i + 1] is not None:
            out[i] = (v[i - 1] + v[i] + v[i + 1]) / 3.0
    return out


def _diff(v):
    """Central difference per second; None where a neighbour is missing."""
    out = [None] * len(v)
    for i in range(1, len(v) - 1):
        if v[i - 1] is not None and v[i + 1] is not None:
            out[i] = (v[i + 1] - v[i - 1]) / 2.0
    return out


def _runs(flags):
    """[(start, end)] of consecutive True, end inclusive."""
    out, s = [], None
    for i, f in enumerate(flags):
        if f and s is None:
            s = i
        elif not f and s is not None:
            out.append((s, i - 1))
            s = None
    if s is not None:
        out.append((s, len(flags) - 1))
    return out


# ---- the parts of a score ---------------------------------------------------

def _none_sub(why, source):
    return {"score": None, "value": None, "source": source, "why": why}


def _smoothness(a, thr, moving):
    """RMS jerk of the speed, and of the pedal when it was read. Measured.
    `a` is the smoothed acceleration in m/s2 on the one-second grid."""
    j = _diff(a)
    js = [j[i] for i in range(len(j)) if j[i] is not None and moving[i]]
    if len(js) < 60:
        return _none_sub("not enough data: speed was read for under a minute", "measured")
    rms = math.sqrt(sum(x * x for x in js) / len(js))
    parts = [(_ramp(rms, JERK_WORST, JERK_BEST), 0.7)]
    slew = None
    ts = [thr[i + 1] - thr[i] for i in range(len(thr) - 1)
          if thr[i] is not None and thr[i + 1] is not None and moving[i]]
    if len(ts) >= 60:
        slew = math.sqrt(sum(x * x for x in ts) / len(ts))
        parts.append((_ramp(slew, TSLEW_WORST, TSLEW_BEST), 0.3))
    wsum = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / wsum
    return {"score": round(score), "value": round(rms, 2), "unit": "m/s3 RMS jerk",
            "throttle_slew": None if slew is None else round(slew, 1),
            "source": "measured", "why": None}


def _events(a, v, moving):
    """Hard slowdowns and hard launches, counted once per run."""
    brakes = accels = 0
    for s, e in _runs([x is not None and x <= -HARD_DECEL for x in a]):
        if v[s] is not None and v[s] >= HARD_MIN_KPH:
            brakes += 1
    for s, e in _runs([x is not None and x >= HARD_ACCEL for x in a]):
        if moving[s]:
            accels += 1
    return brakes, accels


def _regen(v, thr, soc, a):
    """Energy put back into the pack while slowing, against the kinetic energy
    shed. The charge is measured; its size in Wh is assumed. ESTIMATED."""
    n = len(v)
    mps = [None if x is None else x / 3.6 for x in v]
    decel = [a[i] is not None and a[i] <= -0.3 and mps[i] is not None and mps[i] > 2.0
             for i in range(n)]
    # Join slowdowns that a blip of 1-2 s separates, so one stop is one event.
    merged = []
    for s, e in _runs(decel):
        if merged and s - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    shed = rec = 0.0
    covered = total = 0
    for s, e in merged:
        s0, e0 = max(0, s - 1), min(n - 1, e + 1)
        if mps[s0] is None or mps[e0] is None or mps[e0] >= mps[s0]:
            continue
        total += 1
        lost = 0.5 * CAR_KG * (mps[s0] ** 2 - mps[e0] ** 2) / 3600.0   # Wh
        if lost < 1.0:
            continue
        end = min(n - 1, e0 + int(SOC_LAG_S))
        if soc[s0] is None or soc[end] is None:
            continue
        covered += 1
        shed += lost
        rec += max(0.0, (soc[end] - soc[s0]) * PACK_WH_PER_PERCENT)
    if total == 0 or covered == 0:
        has_soc = any(x is not None for x in soc)
        return _none_sub("not enough data: the charge reading was missing while slowing"
                         if not has_soc or total else
                         "not enough data: no real slowing down", "estimated")
    if shed < MIN_SHED_WH:
        return _none_sub(f"not enough data: only {shed:.0f} Wh of motion shed",
                         "estimated")
    ratio = _clamp(rec / shed, 0.0, 1.0)
    return {"score": round(_ramp(ratio, 0.0, REGEN_FULL)), "value": round(ratio * 100),
            "unit": "% of motion energy recovered", "shed_wh": round(shed),
            "recovered_wh": round(rec), "events": covered,
            "source": "estimated", "why": None}


def _glide(v, thr, a, moving):
    if not any(x is not None for x in thr):
        return _none_sub("not enough data: throttle was not read", "measured")
    mv = off = 0
    for i in range(len(v)):
        if not moving[i] or thr[i] is None or v[i] is None:
            continue
        mv += 1
        if (v[i] >= GLIDE_MIN_KPH and thr[i] < THROTTLE_OFF
                and (a[i] is None or a[i] > -GLIDE_MAX_DECEL)):
            off += 1
    if mv < 60:
        return _none_sub("not enough data: throttle was read for under a minute",
                         "measured")
    share = off / mv
    return {"score": round(_ramp(share, 0.0, GLIDE_FULL)), "value": round(share * 100),
            "unit": "% of moving time, foot off", "source": "measured", "why": None}


def _autostop(v, rpm):
    stopped = off = 0
    for i in range(len(v)):
        if v[i] is None or rpm[i] is None or v[i] >= MOVING_KPH:
            continue
        stopped += 1
        if rpm[i] < 100:
            off += 1
    if stopped < MIN_STOP_S:
        return {"share": None, "seconds": stopped, "source": "measured",
                "why": "not enough time stopped" if rpm and any(x is not None for x in rpm)
                else "not enough data: rpm was not read"}
    return {"share": round(off / stopped * 100), "seconds": stopped, "off_s": off,
            "source": "measured", "why": None}


def economy_vs_baseline(lphk, km, past):
    """This drive's fuel use against this car's own median on drives of similar
    length. `past` is [(km, lphk)] of earlier drives. Lower fuel is better, so
    `pct` is positive when this drive used LESS. ESTIMATED: the fuel figure is
    worked out from air flow at a fixed air-fuel ratio."""
    base = {"source": "estimated", "pct": None, "baseline": None, "n": 0}
    lphk, km = _num(lphk), _num(km)
    if lphk is None or lphk <= 0 or km is None or km <= 0:
        return {**base, "why": "not enough data: no fuel figure for this drive"}
    sim = [p[1] for p in (past or [])
           if _num(p[0]) and _num(p[1]) and p[1] > 0
           and SIMILAR[0] * km <= p[0] <= SIMILAR[1] * km]
    if len(sim) < MIN_SIMILAR:
        return {**base, "n": len(sim), "lphk": round(lphk, 1),
                "why": f"not enough data: {len(sim)} earlier "
                       f"{'drive' if len(sim) == 1 else 'drives'} of similar length, "
                       f"{MIN_SIMILAR} needed"}
    med = statistics.median(sim)
    return {**base, "pct": round((med - lphk) / med * 100), "baseline": round(med, 1),
            "lphk": round(lphk, 1), "n": len(sim), "why": None}


# ---- the hint ---------------------------------------------------------------

def choose_hint(res):
    """ONE hint for a scored drive, from rules; the most useful wins. Pure.
    Shown only when parked (the screens check), and kept in the record."""
    ev = res.get("events") or {}
    subs, facts = res.get("subs") or {}, res.get("facts") or {}
    cands = []
    nb = ev.get("hard_brakes") or 0
    if nb >= 3:
        cands.append((min(1.0, nb / 8) + 0.2, "late-braking",
                      f"You slowed hard {nb} times; easing off about 3 s earlier lets "
                      "the IMA recover more.", {"hard_brakes": nb}))
    na = ev.get("hard_accels") or 0
    if na >= 3:
        cands.append((min(1.0, na / 8), "quick-launches",
                      f"{na} quick launches. A steadier foot keeps the IMA's help "
                      "for the hills.", {"hard_accels": na}))
    g = subs.get("glide") or {}
    if g.get("score") is not None and g["value"] < 8 and (res.get("minutes") or 0) >= 5:
        cands.append((0.5, "no-gliding",
                      "Almost no time with your foot off. Lifting early for lights "
                      "and corners costs nothing and charges the pack.",
                      {"glide_pct": g["value"]}))
    a = facts.get("autostop") or {}
    if a.get("share") is not None and a["share"] < 30 and a["seconds"] >= 60:
        cands.append((0.55, "idling-at-stops",
                      "The engine kept idling at stops. Auto-stop needs a firm foot "
                      "on the brake (CVT), or neutral with the clutch up (6-speed), "
                      "once warm.", {"autostop_pct": a["share"]}))
    e = facts.get("economy") or {}
    if e.get("pct") is not None and e["pct"] <= -10:
        cands.append((0.4, "thirsty",
                      f"This drive used {abs(e['pct'])}% more fuel than your usual "
                      "drives this long. A steadier speed is the quickest way back.",
                      {"economy_pct": e["pct"]}))
    if cands:
        cands.sort(key=lambda c: -c[0])
        _, hid, text, evidence = cands[0]
        return {"id": hid, "text": text, "evidence": evidence}
    head = (res.get("headline") or {}).get("score")
    if head is not None and head >= 75:
        return {"id": "clean", "text": "A clean drive. Nothing to change.", "evidence": {}}
    if head is None:
        return {"id": "none", "text": "Not enough of this drive was recorded to coach it.",
                "evidence": {}}
    return {"id": "keep-going", "text": "Nothing stands out. Keep an eye on the "
            "sub-scores below.", "evidence": {}}


# ---- the score --------------------------------------------------------------

def score_drive(rows, economy=None):
    """Score one drive. `rows` is [{t, speed (km/h), throttle (%), rpm, soc (%)}],
    any of them possibly None or absent; `economy` is {"lphk", "km", "past"}.
    Pure: nothing is read from the clock, a file or the car."""
    res = {"version": 1, "t0": None, "t1": None, "km": None, "minutes": None,
           "events": {}, "facts": {}}
    g = _grid(rows or [])
    if g is None:
        return _finish(res, {k: _none_sub("not enough data: no samples", "measured")
                             for k in WEIGHTS}, "no samples")
    t0, v, thr, rpm, soc = g
    res["t0"], res["t1"] = t0, t0 + len(v) - 1
    res["minutes"] = round(len(v) / 60.0, 1)
    moving = [x is not None and x > MOVING_KPH for x in v]
    mv_s = sum(moving)
    km = sum(x for x in v if x is not None and x > MOVING_KPH) / 3600.0
    res["km"] = round(km, 1)
    short = mv_s < MIN_MOVING_S or km < MIN_KM
    if short:
        subs = {k: _none_sub("too short to score", "measured") for k in WEIGHTS}
        subs["regen"]["source"] = "estimated"
        return _finish(res, subs, "too short to score (under 2 minutes moving)")

    a = _diff(_smooth3([None if x is None else x / 3.6 for x in v]))   # m/s2
    smooth = _smoothness(a, thr, moving)
    brakes, accels = _events(a, v, moving)
    res["events"] = {"hard_brakes": brakes, "hard_accels": accels}
    subs = {"regen": _regen(v, thr, soc, a), "smooth": smooth,
            "glide": _glide(v, thr, a, moving)}
    res["facts"]["autostop"] = _autostop(v, rpm)
    if economy:
        res["facts"]["economy"] = economy_vs_baseline(
            economy.get("lphk"), economy.get("km", km), economy.get("past"))
    return _finish(res, subs, None)


def _finish(res, subs, reason):
    res["subs"] = subs
    have = {k: s["score"] for k, s in subs.items() if s["score"] is not None}
    missing = [k for k in WEIGHTS if k not in have]
    if len(have) >= 2:
        wsum = sum(WEIGHTS[k] for k in have)
        score = round(sum(have[k] * WEIGHTS[k] for k in have) / wsum)
        res["headline"] = {
            "score": int(score), "partial": bool(missing), "basis": sorted(have),
            "missing": missing,
            "note": ("Worked out from " + " and ".join(LABEL[k].lower() for k in sorted(have))
                     + "; " + " and ".join(LABEL[k].lower() for k in missing)
                     + " not enough data.") if missing else None}
    else:
        res["headline"] = {"score": None, "partial": False, "basis": sorted(have),
                           "missing": missing,
                           "note": ("Not enough data: " + reason) if reason
                           else "Not enough data to score this drive."}
    res["hint"] = choose_hint(res)
    return res


# ---- reading the log, keeping the file --------------------------------------

def _past_economy(db, before):
    out = []
    try:
        for r in db.execute("SELECT t0, km, lphk FROM trips WHERE t0 < ? "
                            "AND lphk IS NOT NULL AND km > 0", (before,)):
            out.append((r[1], r[2]))
    except Exception:                                          # noqa: BLE001
        pass
    return out


def refresh(state=None, db=None):
    """Score trips the file has not seen. Returns the state dict (also saved).
    Never touches a simulated file."""
    import records
    st = state if state is not None else _load()
    if st.get("source") == "sim":
        return st
    close = False
    if db is None:
        db = records.connect()
        close = True
    if db is None or not records.has(db, "trips"):
        return st
    try:
        seen = {d["t0_trip"] for d in st.get("drives", []) if "t0_trip" in d}
        todo = [dict(r) for r in db.execute(
            "SELECT t0, t1, km, lphk FROM trips WHERE t1 IS NOT NULL "
            "ORDER BY t0 DESC LIMIT 25")]
        added = False
        for tr in sorted(todo, key=lambda r: r["t0"]):
            if tr["t0"] in seen:
                continue
            rows = records.samples(db, since=tr["t0"], until=tr["t1"], limit=20000)
            res = score_drive(rows, economy={"lphk": tr.get("lphk"), "km": tr.get("km"),
                                             "past": _past_economy(db, tr["t0"])})
            res["t0_trip"], res["source"] = tr["t0"], "real"
            res["t0"], res["t1"] = tr["t0"], tr["t1"]
            st.setdefault("drives", []).append(res)
            added = True
        st["drives"] = sorted(st.get("drives", []), key=lambda d: d["t0"])[-KEEP:]
        st["source"] = "real"
        if added or not os.path.exists(STATE):
            _save(st)
    finally:
        if close:
            db.close()
    return st


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {"drives": []}
    except (OSError, ValueError):
        return {"drives": []}


def _save(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    st["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def summary(d):
    """One drive, short, for the trend."""
    h = d.get("headline") or {}
    return {"t0": d.get("t0"), "km": d.get("km"), "score": h.get("score"),
            "partial": bool(h.get("partial")), "kind": d.get("kind"),
            "simulated": d.get("source") == "sim"}


def read_public(refresh_now=False):
    """For the API: the last drive in full, the last TREND as a trend, the
    source. {"present": False} when nothing has been scored."""
    st = _load()
    if refresh_now and st.get("source") != "sim":
        # Cheap when nothing is new; capped so a poll never re-reads the log.
        if time.time() - float(st.get("checked", 0)) > 60:
            try:
                st = refresh(st)
            except Exception:                                  # noqa: BLE001
                pass
            st["checked"] = time.time()
            try:
                _save(st)
            except OSError:
                pass
    drives = st.get("drives") or []
    if not drives:
        return {"present": False, "source": st.get("source")}
    sim = st.get("source") == "sim"
    return {"present": True, "source": "sim" if sim else "real",
            "simulated": sim, "last": drives[-1],
            "drives": [{**d, **summary(d)} for d in drives[-TREND:]],
            "age": max(0.0, time.time() - float(st.get("at", 0)))}


# ---- synthetic drives (the simulator, and the tests) ------------------------

def synth(kind, seed=0, harsh=1.0):
    """A believable drive as sample rows, one a second. kind: "highway",
    "urban" (stop-and-go, braking late), "mixed". Deterministic per seed. `harsh` (0..1) is how late the driver brakes
    and how hard the driver launches: 1 is the worst of it, 0 a careful one."""
    rnd = random.Random(f"{kind}-{seed}")
    speed, thr, rpm, soc, t = [], [], [], [], 0
    state = {"v": 0.0, "soc": 62.0, "off": 0.0}

    def step(target, accel, brake, wot=False):
        v0 = state["v"]
        a = accel if target > v0 else -brake
        v1 = v0 + a if abs(target - v0) > abs(a) else target
        v1 = max(0.0, v1)
        state["v"] = v1
        x = (v1 - v0)
        if x > 0.05:
            tp = min(100.0, 12 + x * (22 if not wot else 40) + v0 * 0.3)
        elif x < -0.05:
            tp = 0.0
        else:
            tp = 11 + v0 * 0.12
        # the pack: energy back from a slowdown, capped at the motor's 12 kW
        if x < 0:
            kin_w = CAR_KG * ((v0 + v1) / 2) * abs(x)
            state["soc"] += 0.4 * min(kin_w, 12000.0) / 3600.0 / PACK_WH_PER_PERCENT
        elif x > 0.4:
            state["soc"] -= 0.012
        speed.append(round(v1 * 3.6))
        thr.append(round(tp + rnd.uniform(-0.5, 0.5), 1))
        # Auto-stop engages for most stops once warm.
        stopped = v1 * 3.6 < 3
        if stopped:
            state["off"] += 1
            off = state["off"] > 3 and rnd.random() > 0.15
        else:
            state["off"] = 0
            off = False
        rpm.append(0 if off else round(800 + v1 * 3.6 * 28))
        soc.append(round(state["soc"]))

    def drive_to(v_kph, accel=0.8, brake=0.8, wot=False):
        target = v_kph / 3.6
        guard = 0
        while abs(state["v"] - target) > 1e-6 and guard < 600:
            step(target, accel, brake, wot)
            guard += 1

    def hold(sec, v_kph, wobble=0.0, accel=0.5):
        base = v_kph / 3.6
        for k in range(int(sec)):
            target = base + wobble * math.sin(k / 23.0 + rnd.random() * 0.01)
            step(target, accel, accel)

    def glide(sec, drop=0.35):
        for _ in range(int(sec)):
            v0 = state["v"]
            v1 = max(0.0, v0 - drop)
            state["v"] = v1
            speed.append(round(v1 * 3.6))
            thr.append(0.0)
            rpm.append(round(800 + v1 * 3.6 * 28))
            soc.append(round(state["soc"]))
            state["soc"] += 0.4 * min(CAR_KG * v0 * drop, 12000.0) / 3600.0 / PACK_WH_PER_PERCENT

    def stop(sec):
        for _ in range(int(sec)):
            step(0.0, 1.0, 1.0)

    if kind == "highway":
        stop(8)
        drive_to(55, 0.9)
        hold(40, 55, 0.3)
        drive_to(105, 0.7)
        for _ in range(4):
            hold(110 + rnd.randint(0, 30), 105, 0.6 + 2.5 * harsh)
            glide(int(14 * (1.2 - harsh)), 0.3)
            drive_to(105, 0.6)
        hold(60, 105, 1.0)
        glide(30, 0.45)
        drive_to(0, 0.8, 0.55)
        stop(15)
    elif kind == "urban":
        stop(10)
        for _ in range(8):
            drive_to(46 + rnd.randint(-4, 6), 0.9 + 1.0 * harsh, 1.0, wot=harsh > 0.5)
            hold(rnd.randint(6, 14), 46, 0.0, 1.5)
            drive_to(0, 1.9, 1.0 + 2.3 * harsh)    # braked late, when harsh
            stop(rnd.randint(14, 30))
    else:  # mixed: a town, a road, a town; a foot that comes off early
        stop(8)
        for _ in range(3):
            drive_to(40, 1.1, 0.9)
            glide(8, 0.4)
            drive_to(0, 1.0, 1.2 + 2.0 * harsh)
            stop(rnd.randint(12, 22))
        drive_to(80, 0.9)
        hold(150, 80, 0.8 + 3.0 * harsh)
        glide(20, 0.4)
        drive_to(0, 0.9, 0.9)
        stop(15)
    return [{"t": 1_000_000 + i, "speed": speed[i], "throttle": thr[i], "rpm": rpm[i],
             "soc": soc[i]} for i in range(len(speed))]


def sim(now=None, seed=7):
    """Twenty simulated drives over twenty days, scored by the same code as
    real ones, and labelled Simulated in the file and on every screen."""
    now = time.time() if now is None else now
    rnd = random.Random(seed)
    kinds = ["urban", "highway", "mixed", "urban", "mixed", "highway", "urban"]
    base = {"urban": 8.4, "highway": 5.9, "mixed": 6.8}
    drives, past = [], []
    for i in range(20):
        kind = rnd.choice(kinds)
        rows = synth(kind, seed * 100 + i, harsh=rnd.random())
        km = sum(r["speed"] for r in rows) / 3600.0
        lphk = base[kind] * rnd.uniform(0.88, 1.18)
        t1 = now - (19 - i) * 86400 + rnd.randint(-3600, 3600)
        t0 = t1 - len(rows)
        for r in rows:
            r["t"] = t0 + (r["t"] - 1_000_000)
        res = score_drive(rows, economy={"lphk": lphk, "km": km, "past": list(past)})
        past.append((km, lphk))
        res["t0"], res["t1"], res["kind"], res["source"] = t0, t1, kind, "sim"
        res["t0_trip"] = t0
        drives.append(res)
    return {"source": "sim", "drives": drives, "at": now}


# ---- command line -----------------------------------------------------------

def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        pub = read_public()
        if not pub["present"]:
            print("  no drive has been scored yet (omacar coach update, or omacar coach sim)")
            return 0
        last = pub["last"]
        h = last["headline"]
        print(f"  {'SIMULATED  ' if pub['simulated'] else ''}last drive: "
              f"{h['score'] if h['score'] is not None else 'not enough data'}"
              f"{' (partial)' if h['partial'] else ''}")
        for k, s in last["subs"].items():
            val = "not enough data" if s["score"] is None else f"{s['score']} ({s['source']})"
            print(f"    {LABEL[k]:<14}{val}")
        print(f"  hint: {last['hint']['text']}")
        return 0
    if what == "update":
        st = refresh()
        print(f"  {len(st.get('drives', []))} drives scored")
        return 0
    if what == "sim":
        st = sim()
        _save(st)
        print(f"  wrote {len(st['drives'])} SIMULATED drives to {STATE}")
        return 0
    if what == "clear":
        try:
            os.unlink(STATE)
        except OSError:
            pass
        print("  coach.json removed")
        return 0
    print(f"  omacar coach: no such thing as '{what}'.\n"
          "  status | update | sim | clear", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
