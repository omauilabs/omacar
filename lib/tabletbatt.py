#!/usr/bin/env python3
"""The tablet's own battery, in a car that gets hot.

The Surface Pro 7+ lives on the dash and is charged by the car (lib/ignition.py
follows the charger). A lithium cell kept full and warm ages fastest, and a
parked car is both. This watches that cell and says so, honestly:

  * HEALTH. energy_full / energy_full_design (or charge_*) from
    /sys/class/power_supply/BAT*, one sample a day kept in a small history, and a
    trend with a projection that says "rough" when it is.
  * HEAT. Over 45 C while charging gets one calm sentence. The Surface's
    battery driver may not report a temperature at all (drivers/power/supply/
    surface_battery.c has no temp property, read 2026-10-17); then the screen says
    "not reported" and never draws a zero.
  * THE LIMIT. Linux CANNOT set a charge limit on this machine: no
    charge_control_end_threshold, no charge_behaviour, no write path. The
    limit is Surface UEFI's "Enable Battery Limit" (50 %). This only READS whether
    it looks active, from how high the charge went on days it was charging.
    See doc/tablet-battery.md for the sources.

Nothing here writes to the battery, the charger or the car. Read-only.

    omacar tabletbatt status    what the app is shown now
    omacar tabletbatt run       sample once a minute (omacar-tabletbatt.service)
    omacar tabletbatt sim [normal|hot|limited] [seconds]
                                a simulated battery; it says "Simulated"
"""

import datetime
import json
import math
import os
import sys
import time

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
STATE = os.path.join(STATE_DIR, "tabletbatt.json")
HISTORY = {"sysfs": os.path.join(STATE_DIR, "tabletbatt-history.json"),
           "sim": os.path.join(STATE_DIR, "tabletbatt-history-sim.json")}
SYSFS = "/sys/class/power_supply"

SAMPLE_EVERY = 60          # s between samples, so one sample is about a minute
STALE = 180                # s without a write: the sampler has stopped
KEEP_DAYS = 800
HOT_C = 45.0               # above this while charging: the heat note
LIMIT_PCT = 50             # Surface UEFI Battery Limit (Microsoft Learn)
LIMIT_SLACK = 55           # a cell "at 50 %" reads a little over, never to 70
EVIDENCE_MIN = 90          # charging samples (~minutes) that make a day count
EVIDENCE_DAYS = 3          # days like that before saying anything about the limit
REPLACE_AT = 80.0          # the usual "worn" line

HEAT_WORDS = "Hot: charging is hard on the battery right now"


# ---- reading sysfs (pure: parse() takes the files as strings) ---------------------

def _num(files, name):
    raw = files.get(name)
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def parse(files, ac_online=None):
    """One battery from its sysfs files, {name: text}. Any file may be missing;
    what is missing is None, never 0. Energy is micro-Wh, charge micro-Ah, `temp`
    tenths of a degree C -- the power_supply ABI."""
    e_full, e_des = _num(files, "energy_full"), _num(files, "energy_full_design")
    c_full, c_des = _num(files, "charge_full"), _num(files, "charge_full_design")
    if e_full is not None and e_des:
        unit, full, design = "Wh", e_full / 1e6, e_des / 1e6
    elif c_full is not None and c_des:
        unit, full, design = "Ah", c_full / 1e6, c_des / 1e6
    else:
        unit, full, design = None, None, None
    health = None
    if unit and design > 0 and full is not None and full > 0:
        health = round(100.0 * full / design, 1)
    cap = _num(files, "capacity")
    cycles = _num(files, "cycle_count")
    temp = _num(files, "temp")
    status = (files.get("status") or "").strip() or None
    charging = (status in ("Charging", "Full", "Not charging")) or bool(ac_online)
    return {
        "capacity": None if cap is None else max(0, min(100, int(cap))),
        "status": status,
        "charging": charging if (status or ac_online is not None) else None,
        "unit": unit,
        "full": None if full is None else round(full, 3),
        "design": None if design is None else round(design, 3),
        "health": health,
        "cycles": None if cycles is None or cycles < 0 else int(cycles),
        "temp_c": None if temp is None else round(temp / 10.0, 1),
        "model": (files.get("model_name") or "").strip() or None,
    }


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def read_sysfs(root=SYSFS):
    """The first real battery under `root` as parse() gives it, or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    ac = None
    for n in names:
        kind = (_read(os.path.join(root, n, "type")) or "").strip()
        if kind in ("Mains", "USB"):
            on = (_read(os.path.join(root, n, "online")) or "").strip()
            if on in ("0", "1"):
                ac = bool(ac) or on == "1"
    for n in names:
        if not n.startswith("BAT"):
            continue
        d = os.path.join(root, n)
        kind = (_read(os.path.join(d, "type")) or "Battery").strip()
        if kind != "Battery":
            continue
        files = {}
        for f in ("status", "capacity", "cycle_count", "temp", "model_name",
                  "energy_full", "energy_full_design", "charge_full",
                  "charge_full_design"):
            v = _read(os.path.join(d, f))
            if v is not None:
                files[f] = v
        r = parse(files, ac)
        r["name"] = n
        return r
    return None


# ---- the daily history (pure) -----------------------------------------------------

def fold(days, reading, day):
    """Fold one sample into the list of day records; returns a new list.
    A day: {day, health, full, design, cycles, peak_cap, charge_samples,
    samples, max_temp, hot_samples}. peak_cap is the highest charge seen WHILE
    on the charger -- the figure the limit heuristic reads."""
    days = [dict(d) for d in days]
    if not days or days[-1]["day"] != day:
        days.append({"day": day, "health": None, "full": None, "design": None,
                     "cycles": None, "peak_cap": None, "charge_samples": 0,
                     "samples": 0, "max_temp": None, "hot_samples": 0})
    d = days[-1]
    d["samples"] += 1
    for k in ("health", "full", "design", "cycles"):
        if reading.get(k) is not None:
            d[k] = reading[k]
    t = reading.get("temp_c")
    if t is not None:
        d["max_temp"] = t if d["max_temp"] is None else max(d["max_temp"], t)
    if reading.get("charging"):
        d["charge_samples"] += 1
        c = reading.get("capacity")
        if c is not None:
            d["peak_cap"] = c if d["peak_cap"] is None else max(d["peak_cap"], c)
        if t is not None and t > HOT_C:
            d["hot_samples"] += 1
    return days[-KEEP_DAYS:]


def heat_note(temp_c, charging):
    """The calm word, or None. Not reported (None) is never "fine"."""
    if temp_c is not None and charging and temp_c > HOT_C:
        return HEAT_WORDS
    return None


def limit_status(days, today=None):
    """Does the UEFI Battery Limit look active? Judged only from how high the
    charge went on days the tablet was charging a long time.

    Evidence days: charging for >= EVIDENCE_MIN samples, within the last 14 days.
    Need EVIDENCE_DAYS of them. Peak <= LIMIT_SLACK on all -> likely on; any
    peak >= 70 -> off (a limited cell cannot get there); else unclear. It is
    inference from behaviour, and says so. state: on|off|unclear|unknown."""
    cutoff = None
    if today:
        cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=14)).isoformat()
    ev = [d for d in days
          if d.get("peak_cap") is not None and d.get("charge_samples", 0) >= EVIDENCE_MIN
          and (cutoff is None or d["day"] >= cutoff)]
    if len(ev) < EVIDENCE_DAYS:
        have = len(ev)
        return {"state": "unknown", "evidence_days": have,
                "why": f"Needs {EVIDENCE_DAYS} days with a long charge to tell "
                       f"({have} so far)."}
    peak = max(d["peak_cap"] for d in ev)
    if peak <= LIMIT_SLACK:
        return {"state": "on", "evidence_days": len(ev), "peak": peak,
                "why": f"Charge never passed {peak} % over {len(ev)} charging days."}
    if peak >= 70:
        return {"state": "off", "evidence_days": len(ev), "peak": peak,
                "why": f"Charge reached {peak} % while charging."}
    return {"state": "unclear", "evidence_days": len(ev), "peak": peak,
            "why": f"Highest charge was {peak} %: neither clearly capped nor not."}


def trend(days):
    """[[day_ordinal, health], ...] for days that have a health figure."""
    out = []
    for d in days:
        if d.get("health") is not None:
            out.append([datetime.date.fromisoformat(d["day"]).toordinal(), d["health"]])
    return out


def project(days):
    """An honest projection of when health reaches REPLACE_AT.

    A least-squares line through the daily health. Refused (state "learning")
    under 5 points or 21 days: capacity estimates wander by a point or two
    from the pack re-learning itself, so a short line is noise. Under 90 days
    the answer is flagged rough. state: learning|flat|declining|below."""
    pts = trend(days)
    if len(pts) < 5 or pts[-1][0] - pts[0][0] < 21:
        return {"state": "learning", "text": "Needs about three weeks of readings "
                "before it can say which way health is going."}
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx   # % per day
    per_month = slope * 30.4
    now_fit = my + slope * (pts[-1][0] - mx)
    rough = (pts[-1][0] - pts[0][0]) < 90
    if now_fit <= REPLACE_AT:
        return {"state": "below", "per_month": round(per_month, 2),
                "text": f"At or under {REPLACE_AT:.0f} %, the usual line for a worn battery."}
    if per_month > -0.05:
        return {"state": "flat", "per_month": round(per_month, 2), "rough": rough,
                "text": "No measurable decline yet."}
    months = (now_fit - REPLACE_AT) / -per_month
    if months > 120:
        return {"state": "flat", "per_month": round(per_month, 2), "rough": rough,
                "text": "Declining so slowly it would take over ten years to reach "
                        f"{REPLACE_AT:.0f} %."}
    word = "about " + (f"{months / 12:.1f} years" if months >= 24 else f"{months:.0f} months")
    return {"state": "declining", "per_month": round(per_month, 2), "months_to_80": round(months, 1),
            "rough": rough,
            "text": f"Losing about {-per_month:.1f} points a month; {REPLACE_AT:.0f} % in {word}"
                    + (" (rough: under three months of readings)." if rough else ".")}


# ---- files ------------------------------------------------------------------------

def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def write_state(reading, source, now=None):
    now = time.time() if now is None else now
    _save(STATE, {"source": source, "reading": reading, "at": now})


def view(state, days, now=None, today=None):
    """What the API returns, from a state dict and day records. Pure."""
    now = time.time() if now is None else now
    if not state or not state.get("reading"):
        return {"present": False}
    r = state["reading"]
    age = max(0.0, now - float(state.get("at", 0)))
    stale = age > STALE
    today = today or datetime.date.fromtimestamp(now).isoformat()
    warn = None if stale else heat_note(r.get("temp_c"), r.get("charging"))
    return {
        "present": True, "source": state.get("source"), "age": age, "stale": stale,
        "reading": r, "warning": warn,
        "limit": limit_status(days, today),
        "projection": project(days),
        "days": [{k: d.get(k) for k in ("day", "health", "peak_cap", "max_temp", "charge_samples")}
                 for d in days[-400:]],
        "limit_pct": LIMIT_PCT,
    }


def read_public():
    st = _load(STATE, None)
    if not st:
        return {"present": False}
    src = st.get("source") if st.get("source") in HISTORY else "sysfs"
    hist = _load(HISTORY[src], {"days": []})
    return view(st, hist.get("days") or [])


# ---- the sampler ------------------------------------------------------------------

def sample_once(root=SYSFS, now=None):
    now = time.time() if now is None else now
    r = read_sysfs(root)
    if r is None:
        return None
    day = datetime.date.fromtimestamp(now).isoformat()
    hist = _load(HISTORY["sysfs"], {"days": []})
    hist["days"] = fold(hist.get("days") or [], r, day)
    _save(HISTORY["sysfs"], hist)
    write_state(r, "sysfs", now)
    return r


def run():
    while True:
        try:
            sample_once()
        except Exception as e:                     # noqa: BLE001 -- keep sampling
            print(f"  tabletbatt: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(SAMPLE_EVERY)


# ---- the simulator ----------------------------------------------------------------

SCENARIOS = ("normal", "hot", "limited")


def _sim_history(scenario, today):
    """Ninety days. normal: slow wear, charged to the top. limited: the UEFI
    cap on, peaks at 50 for the last month. Deterministic."""
    days = []
    start = datetime.date.fromisoformat(today) - datetime.timedelta(days=119)
    for i in range(120):
        d = (start + datetime.timedelta(days=i)).isoformat()
        wobble = 0.5 * math.sin(i / 3.1) + 0.3 * math.sin(i / 1.3)
        health = round(100.0 - i * 0.045 + wobble, 1) if scenario != "limited" \
            else round(97.5 - i * 0.02 + wobble, 1)
        capped = scenario == "limited" and i >= 80
        days.append({"day": d, "health": min(100.0, health),
                     "full": round(min(100.0, health) * 0.4301, 3), "design": 43.01,
                     "cycles": 40 + i // 2, "peak_cap": 50 if capped else (100 if i % 9 else 96),
                     "charge_samples": 200, "samples": 600,
                     "max_temp": round(31 + 9 * abs(math.sin(i / 11)), 1),
                     "hot_samples": 0})
    return days


def sim(scenario="normal", seconds=None):
    """Writes a believable battery every 2 s, always source "sim". Its history
    goes to its own file so it can never leak into the real one."""
    today = datetime.date.today().isoformat()
    days = _sim_history(scenario, today)
    _save(HISTORY["sim"], {"days": days})
    start, cap = time.time(), 62.0 if scenario != "limited" else 49.0
    while seconds is None or time.time() - start < seconds:
        t = time.time() - start
        if scenario != "limited":
            cap = min(100.0, cap + 0.05)
        temp = 38.0 + 9.5 * (1 - math.exp(-t / 60.0)) if scenario == "hot" \
            else 31.0 + 2.0 * math.sin(t / 40.0)
        last = days[-1]
        r = {"capacity": int(cap), "status": "Not charging" if scenario == "limited" else "Charging",
             "charging": True, "unit": "Wh", "full": last["full"], "design": 43.01,
             "health": last["health"], "cycles": last["cycles"], "temp_c": round(temp, 1),
             "model": "Simulated battery", "name": "BAT1"}
        write_state(r, "sim")
        time.sleep(2)


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "run":
        run()
        return 0
    if what == "sim":
        rest = list(argv[1:])
        sc = rest.pop(0) if rest and rest[0] in SCENARIOS else "normal"
        sim(sc, float(rest[0]) if rest else None)
        return 0
    print(f"  omacar tabletbatt: no such thing as '{what}'.\n"
          "  status | run | sim [normal|hot|limited] [seconds]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
