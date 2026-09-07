"""Everything OmaCar knows about the car, read out of telemetry.db.

One reader, used by the loopback API, by the AI layer and by the CLI, so those
three can never disagree about what the car said. Read-only and non-blocking
throughout: the daemon owns writing, and a diagnostic screen that can block on
a database lock is a diagnostic screen that hangs mid-scan.

The database is metric, because OBD-II is metric on the wire — every PID is
km/h, °C and grams per second. Display units are a separate question and are
settled once, at the edge, by `units_for()`.
"""

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garage  # noqa: E402

STATE = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
# One database per vehicle; see lib/garage.py for why. Resolved once at import
# and re-resolved by use(), so anything holding a reference to this module
# picks up a switch — and so the tests can still point it at a temp file.
DB = garage.db_path()
LIVE = os.path.join(STATE, "live.json")


def use(key):
    """Point every reader at another vehicle. Never call this while something
    has the database open — see survey.prepare()."""
    global DB
    garage.set_current(key)
    DB = garage.db_path()
    return DB


def refresh_db():
    """Re-read the pointer, for a process that did not switch it itself."""
    global DB
    DB = garage.db_path()
    return DB
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")),
    "omarchy", "liquid-glass-car.json")

AFR_GASOLINE = 14.7
FUEL_DENSITY_G_PER_L = 745.0
MOVING_KPH = 3.0
MPG_CONSTANT = 235.214583

# Honda's Maintenance Minder counts down rather than up: 15% is "book it",
# 5% is "now", nought is past due.
LIFE_SOON = 15
LIFE_NOW = 5

UNITS = {
    "imperial": {
        "system": "imperial", "dist": "mi", "speed": "mph", "econ": "mpg",
        "vol": "gal", "temp": "°F", "km": 1 / 1.609344,
        "litre": 1 / 3.785411784, "econ_better": "high",
    },
    "metric": {
        "system": "metric", "dist": "km", "speed": "km/h", "econ": "L/100km",
        "vol": "L", "temp": "°C", "km": 1.0, "litre": 1.0,
        "econ_better": "low",
    },
}


def units_for(name=None):
    if name is None:
        try:
            with open(CONFIG) as f:
                name = (json.load(f) or {}).get("units")
        except (OSError, ValueError):
            name = None
    return UNITS.get((name or "imperial").lower(), UNITS["imperial"])


# The record stays metric; these are the only place it becomes anything else.
# Distance and volume are scale factors. Economy is NOT — it is a reciprocal,
# so a lower consumption is a higher mpg, and treating it as a scale reports a
# thrifty car as a thirsty one. Temperature is an offset as well as a scale.

def to_dist(km, u):
    return None if km is None else km * u["km"]


def to_vol(litres, u):
    return None if litres is None else litres * u["litre"]


def to_econ(lphk, u):
    """L/100km into the display unit, or None where economy is undefined.

    A car burning nothing has infinite miles per gallon, so nought and missing
    both come back as None rather than as a very large number.
    """
    if lphk is None or lphk <= 0:
        return None
    return MPG_CONSTANT / lphk if u["system"] == "imperial" else lphk


def to_temp(c, u):
    if c is None:
        return None
    return c * 9.0 / 5.0 + 32.0 if u["system"] == "imperial" else c


def connect():
    if not os.path.exists(DB):
        return None
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2.0)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


# Columns added to `days` after the table was first shipped. SQLite has no
# "add if missing", and three copies of this loop in three modules is three
# chances for one of them to be forgotten — which is how a reseed ends up
# inserting fourteen values into an eleven-column table.
DAYS_COLUMNS = (("ltft_mean", "REAL"), ("coolant_max", "REAL"),
                ("rpm_max", "REAL"))


def migrate_days(db):
    """Bring an older `days` table up to date. Safe to call every time."""
    try:
        have = {r[1] for r in db.execute("PRAGMA table_info(days)")}
    except sqlite3.Error:
        return
    if not have:
        return
    for name, kind in DAYS_COLUMNS:
        if name not in have:
            try:
                db.execute(f"ALTER TABLE days ADD COLUMN {name} {kind}")
            except sqlite3.OperationalError:
                pass


def connect_rw():
    """A writable handle with the same row factory the read path uses. Every
    helper here reads columns by name and fails on a plain connection."""
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB, timeout=10.0)
    db.row_factory = sqlite3.Row
    return db


def has(db, table):
    if db is None:
        return False
    try:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
    except sqlite3.Error:
        return False


def rows(db, sql, args=(), table=None):
    if db is None or (table and not has(db, table)):
        return []
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


# How old the last published sample may be before it stops counting as live.
# The daemon publishes five times a second and the simulator matches it, so
# anything past a few seconds means whoever was writing has stopped.
LIVE_STALE = 15


def live():
    """The current sample — or an honest nothing when it has gone stale.

    A file on disk does not know that the process writing it has died. Without
    this check the app happily shows last Tuesday's road speed as the current
    one, which on a dashboard is not a cosmetic problem: the whole contract of
    that screen is that the number is true right now.
    """
    try:
        with open(LIVE, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return {"connected": False, "status": "no daemon"}

    # WHOSE SAMPLE IS THIS?
    #
    # The daemon and the simulator write the same file -- documented, and only
    # one is meant to run at a time -- but nothing enforced the consequence of
    # breaking that rule, and the consequence is the worst kind this tool has.
    # Leave the simulator running, point the garage at a real car, and the real
    # car's screen took the SIMULATED odometer: 137,847 km on a vehicle with
    # 306,246 on it. Found on the launch tablet, with the real car's rescued
    # record open.
    #
    # So a publisher stamps the vehicle its sample is about, and a sample about
    # a different car is not this car's news. An unstamped sample is accepted
    # unchanged, because an older daemon writing one is not wrong about
    # anything -- it simply predates the question.
    stamped = snap.get("vehicle")
    if stamped:
        try:
            mine = garage.current()
        except Exception:                                     # noqa: BLE001
            mine = None
        if mine and stamped != mine:
            return {
                "connected": False,
                "status": "another vehicle",
                "note": (f"the live sample is about {stamped}, and this is "
                         f"{mine}. The daemon and the simulator share this "
                         f"file and only one may run at a time."),
                "values": {},
            }

    age = time.time() - (snap.get("t") or 0)
    if snap.get("connected") and age > LIVE_STALE:
        return {
            "connected": False,
            "status": "no daemon",
            "stale_for": int(age),
            # The port and protocol are still the last thing that answered,
            # which is worth showing on a connection screen. Everything that
            # moves is dropped rather than frozen.
            "port": snap.get("port"),
            "kind": snap.get("kind"),
            "protocol": snap.get("protocol"),
            "values": {},
        }
    # A HAND-OFF IS NOT A DISCONNECTION.
    #
    # The daemon publishes connected=False, status="yielded" for the few
    # seconds it lends the serial port to a command -- the DTC sweep, a scan, a
    # reset. The cable is in, the car is answering, and nobody has lost
    # anything; the daemon has simply stepped out of the way.
    #
    # Every consumer read that as the adapter dying, so a drive spent five
    # minutes connected and a few seconds "disconnected", forever. `handover`
    # lets a caller tell the two apart without having to know the daemon's
    # status vocabulary.
    snap["handover"] = snap.get("status") == "yielded"
    return snap


def status(snap):
    if not snap.get("connected"):
        return "offline"
    v = snap.get("values") or {}
    if (v.get("SPEED") or 0) > MOVING_KPH:
        return "driving"
    if (v.get("RPM") or 0) > 200:
        return "idling"
    return "parked"


# ---- the car ----------------------------------------------------------------

def possessive(name):
    """James -> James', Alex -> Alex's.

    The trailing-s rule rather than always "'s", because the one name this was
    written for is James and "James's car" is not what its owner writes.
    """
    n = (name or "").strip()
    if not n:
        return ""
    return n + ("'" if n[-1].lower() == "s" else "'s")


def vehicle(db):
    out = {}
    for r in rows(db, "SELECT k, v FROM vehicle", table="vehicle"):
        try:
            out[r["k"]] = json.loads(r["v"])
        except (ValueError, TypeError):
            out[r["k"]] = r["v"]
    if not out:
        return out

    # `name` is the human's own label, and the garage screen documents it as
    # "what to call it, since OBD-II cannot tell us the model". On this car it
    # holds "CR-Z".
    #
    # THIS USED TO DESTROY THE MODEL.
    #
    # The old line overwrote `name` with f"{year} {make} {model}" -- and no car
    # in the garage has ever had a `model` key, because OBD-II does not report
    # one and nothing writes one. So the single field that carried the model
    # was overwritten to build a string that could not contain a model, and
    # every screen in the app said "2015 Honda".
    #
    # garage.describe() had it right all along: prefer what the human typed.
    label = str(out.get("name") or "").strip()
    model = str(out.get("model") or "").strip() or label
    if model:
        out["model"] = model
    out["label"] = label

    # What the car is. Missing parts are skipped rather than left as a double
    # space or a trailing gap where a model should be.
    out["name"] = " ".join(
        str(x) for x in (out.get("year"), out.get("make"), model) if x).strip() or label

    # Whose it is. Two Hondas in one household is the ordinary case, and
    # "James' 2015 Honda CR-Z" tells them apart at a glance where "2015 Honda"
    # tells them apart from nothing.
    out["owner"] = str(out.get("driver") or "").strip()
    out["title"] = (f"{possessive(out['owner'])} {out['name']}".strip()
                    if out["owner"] and out["name"] else out["name"])
    return out


def modules(db):
    out = rows(db, "SELECT * FROM modules ORDER BY pos", table="modules")
    for m in out:
        try:
            m["codes"] = json.loads(m.get("codes") or "[]")
        except (ValueError, TypeError):
            m["codes"] = []
        m["generic"] = bool(m.get("generic"))
    return out


def ago(stamp):
    return None if not stamp else max(0, int(time.time() - stamp))


def faults(db):
    out = rows(db, "SELECT * FROM faults", table="faults")
    rank = {"stored": 0, "permanent": 1, "pending": 2, "cleared": 3}
    for f in out:
        f["ago"] = ago(f.get("last_seen"))
        f["since"] = ago(f.get("first_seen"))
        f["active"] = f.get("status") in ("stored", "pending", "permanent")
        try:
            f["freeze"] = json.loads(f["freeze"]) if f.get("freeze") else None
        except (ValueError, TypeError):
            f["freeze"] = None
    out.sort(key=lambda f: (rank.get(f.get("status"), 4),
                            -(f.get("last_seen") or 0)))

    # Attach the module each code lives in, so a scan report can group by
    # control unit the way the car actually does.
    where = {}
    for m in modules(db):
        for c in m["codes"]:
            where[c] = {"id": m["id"], "name": m["name"]}
    for f in out:
        f["module"] = where.get(f["code"])
    return out


def readiness(db):
    out = rows(db, "SELECT * FROM readiness ORDER BY pos", table="readiness")
    for r in out:
        r["supported"] = bool(r.get("supported"))
        r["complete"] = bool(r.get("complete"))
    supported = [r for r in out if r["supported"]]
    incomplete = [r for r in supported if not r["complete"]]
    return {
        "monitors": out,
        "supported": len(supported),
        "incomplete": len(incomplete),
        # Most states allow one incomplete non-continuous monitor on an
        # OBD-II car; two is a fail everywhere. Reported as the rule rather
        # than as a verdict for one jurisdiction we cannot know.
        "ready": len(incomplete) == 0,
        "marginal": len(incomplete) == 1,
        "blocking": [r["id"] for r in incomplete],
    }


def mode06(db):
    out = rows(db, "SELECT * FROM mode06 ORDER BY pos", table="mode06")
    for m in out:
        lo, hi, v = m.get("lo"), m.get("hi"), m.get("value")
        m["pass"] = True
        m["headroom"] = None
        if v is None:
            m["pass"] = None
        else:
            if hi is not None and v > hi:
                m["pass"] = False
            if lo is not None and v < lo:
                m["pass"] = False
            # How close to the limit, as a fraction. This is the number that
            # turns Mode 06 from a pass/fail list into a prediction: a test at
            # 95% of its limit has passed and is also about to stop passing.
            if hi is not None and hi > 0:
                m["headroom"] = max(0.0, min(1.5, v / hi))
            elif lo is not None and lo > 0:
                m["headroom"] = max(0.0, min(1.5, lo / v)) if v else None
    return out


SHORT_NAMES = [
    ("oil", "OIL"), ("coolant", "COOLANT"), ("tire", "TIRES"), ("tyre", "TIRES"),
    ("brake fluid", "BRAKE FL"), ("brake pad", "PADS"), ("spark", "PLUGS"),
    ("transmission", "TRANS"), ("air cleaner", "AIR"), ("ima", "IMA"),
    ("12 v", "12V"), ("battery", "BATTERY"),
]


def short_name(item):
    low = item.lower()
    for needle, name in SHORT_NAMES:
        if needle in low:
            return name
    return item.split(" ")[0].upper()


def service(db, odometer):
    items = rows(db, "SELECT * FROM service", table="service")
    if not items:
        return None
    now = time.time()
    for s in items:
        used_km = (odometer - s["last_km"]) if odometer and s["last_km"] else None
        used_days = (now - s["last_at"]) / 86400.0 if s["last_at"] else None
        frac_km = (used_km / s["interval_km"]
                   if s.get("interval_km") and used_km is not None else None)
        frac_days = (used_days / s["interval_days"]
                     if s.get("interval_days") and used_days is not None else None)
        frac = max([f for f in (frac_km, frac_days) if f is not None] or [0.0])
        s["life"] = int(round((1.0 - frac) * 100))
        s["by"] = "distance" if frac_km is not None and frac == frac_km else "time"
        s["km_left"] = round(s["interval_km"] - used_km) if frac_km is not None else None
        s["days_left"] = (int(round(s["interval_days"] - used_days))
                          if frac_days is not None else None)
        s["due_at_km"] = (round(s["last_km"] + s["interval_km"])
                          if s.get("interval_km") else None)
        s["due_on"] = (datetime.fromtimestamp(
            s["last_at"] + s["interval_days"] * 86400).strftime("%Y-%m-%d")
            if s.get("interval_days") and s.get("last_at") else None)
        s["last_on"] = (datetime.fromtimestamp(s["last_at"]).strftime("%Y-%m-%d")
                        if s.get("last_at") else None)
        s["state"] = ("overdue" if s["life"] <= 0 else
                      "now" if s["life"] <= LIFE_NOW else
                      "soon" if s["life"] <= LIFE_SOON else "ok")
        s["short"] = short_name(s["item"])
    items.sort(key=lambda s: s["life"])
    return {
        "items": items,
        "next": items[0],
        "oil": next((s for s in items if s["item"].lower().startswith("engine oil")), None),
        "overdue": sum(1 for s in items if s["state"] == "overdue"),
        "due": sum(1 for s in items if s["state"] in ("overdue", "now", "soon")),
    }


# ---- how it has been driven -------------------------------------------------

def days(db, live_window=60):
    """Daily figures — the stored rollups, plus today and any recent day the
    compactor has not reached yet, computed from the raw samples on the fly.

    Without this the whole year view is empty until something has run a
    compaction, which on a car driven for the first time this morning means
    the app has nothing to show about a drive that just happened. The stored
    row always wins where one exists; this only fills the gap at the live end.
    """
    stored = rows(db, "SELECT * FROM days ORDER BY day ASC", table="days")
    have = {d["day"] for d in stored}
    fresh = live_days(db, since=time.time() - live_window * 86400)
    merged = stored + [d for d in fresh if d["day"] not in have]
    merged.sort(key=lambda d: d["day"])
    return merged


def live_days(db, since):
    """Roll raw samples into daily figures, with the same maths as everywhere
    else — speed integrated, fuel from mass air flow, and never across a gap."""
    if db is None:
        return []
    try:
        raw = db.execute(
            "SELECT t, speed, maf, ltft, coolant, rpm, "
            "       CONTROL_VOLTS AS volts FROM samples WHERE t >= ? ORDER BY t ASC",
            (since,)).fetchall()
    except sqlite3.Error:
        try:
            raw = db.execute(
                "SELECT t, speed, maf, ltft, coolant, rpm FROM samples "
                "WHERE t >= ? ORDER BY t ASC", (since,)).fetchall()
        except sqlite3.Error:
            return []
    if not raw:
        return []

    acc, prev_t = {}, None
    for r in raw:
        t, speed, maf = r["t"], r["speed"], r["maf"]
        dt = 1.0 if prev_t is None else t - prev_t
        gap = dt > 10
        if gap:
            dt = 1.0
        key = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
        d = acc.setdefault(key, {"km": 0.0, "litres": 0.0, "moving_s": 0.0,
                                 "engine_s": 0.0, "idle_s": 0.0,
                                 "top_kph": 0.0, "trips": 0, "open": True,
                                 # The health figures. These are the ones the
                                 # trend engine reads, and they have to
                                 # survive compaction — a drift you can only
                                 # see over months is exactly the drift worth
                                 # seeing, and raw samples are gone by then.
                                 "ltft_sum": 0.0, "ltft_n": 0,
                                 "coolant_max": None, "rpm_max": 0.0})
        if gap:
            d["open"] = True
        if speed and speed > MOVING_KPH:
            if d["open"]:
                d["trips"] += 1
                d["open"] = False
            d["km"] += speed * dt / 3600.0
            d["moving_s"] += dt
            d["top_kph"] = max(d["top_kph"], speed)
        else:
            d["idle_s"] += dt
        d["engine_s"] += dt
        if maf and maf > 0:
            d["litres"] += (maf / AFR_GASOLINE) * dt / FUEL_DENSITY_G_PER_L
        ltft = r["ltft"] if "ltft" in r.keys() else None
        if ltft is not None:
            d["ltft_sum"] += ltft
            d["ltft_n"] += 1
        coolant = r["coolant"] if "coolant" in r.keys() else None
        if coolant is not None:
            d["coolant_max"] = (coolant if d["coolant_max"] is None
                                else max(d["coolant_max"], coolant))
        rpm = r["rpm"] if "rpm" in r.keys() else None
        if rpm:
            d["rpm_max"] = max(d["rpm_max"], rpm)
        prev_t = t

    out = []
    for day, d in sorted(acc.items()):
        out.append({
            "day": day, "km": round(d["km"], 2),
            "litres": round(d["litres"], 3),
            "lphk": (round(d["litres"] / d["km"] * 100.0, 2)
                     if d["km"] > 0.5 and d["litres"] > 0 else None),
            "moving_s": int(d["moving_s"]), "engine_s": int(d["engine_s"]),
            "idle_s": int(d["idle_s"]), "top_kph": round(d["top_kph"], 1),
            "trips": d["trips"], "cost": None, "odo": None,
            "ltft_mean": (round(d["ltft_sum"] / d["ltft_n"], 2)
                          if d["ltft_n"] > 60 else None),
            "coolant_max": (round(d["coolant_max"], 1)
                            if d["coolant_max"] is not None else None),
            "rpm_max": round(d["rpm_max"]) if d["rpm_max"] else None,
        })
    return out


def window_of(series, first_day=None, n=None):
    chosen = series[-n:] if n is not None else [d for d in series if d["day"] >= first_day]
    km = sum(d["km"] or 0 for d in chosen)
    litres = sum(d["litres"] or 0 for d in chosen)
    return {
        "km": round(km, 1),
        "litres": round(litres, 2),
        # Fuel over distance for the whole window, never the mean of the daily
        # figures — a two-mile errand would otherwise weigh as much as a
        # three-hundred-mile run.
        "lphk": round(litres / km * 100.0, 2) if km > 0.5 else None,
        "cost": round(sum(d["cost"] or 0 for d in chosen), 2),
        "moving_s": int(sum(d["moving_s"] or 0 for d in chosen)),
        "engine_s": int(sum(d["engine_s"] or 0 for d in chosen)),
        "trips": int(sum(d["trips"] or 0 for d in chosen)),
        "top_kph": round(max([d["top_kph"] or 0 for d in chosen] or [0])),
        "days": len([d for d in chosen if (d["km"] or 0) > 0.5]),
        "span": len(chosen),
    }


def performance(series):
    if not series:
        return None
    today = datetime.now().date()

    def pair(first, prev_first=None, prev_end=None):
        w = window_of(series, first_day=first)
        if prev_first is not None:
            prev = [d for d in series if prev_first <= d["day"] <= prev_end]
            pkm = sum(d["km"] or 0 for d in prev)
            pl = sum(d["litres"] or 0 for d in prev)
            w["prev"] = {
                "km": round(pkm, 1),
                "lphk": round(pl / pkm * 100.0, 2) if pkm > 0.5 else None,
                "cost": round(sum(d["cost"] or 0 for d in prev), 2),
            }
        return w

    iso = today.isoformat()
    yday = (today - timedelta(days=1)).isoformat()
    month_first = today.replace(day=1).isoformat()
    lm_end = today.replace(day=1) - timedelta(days=1)
    out = {
        "day": pair(iso, yday, yday),
        "week": pair((today - timedelta(days=6)).isoformat(),
                     (today - timedelta(days=13)).isoformat(),
                     (today - timedelta(days=7)).isoformat()),
        "month": pair(month_first, lm_end.replace(day=1).isoformat(), lm_end.isoformat()),
        "year": pair(today.replace(month=1, day=1).isoformat()),
        "rolling_year": window_of(series, n=365),
    }

    months = {}
    for d in series:
        m = months.setdefault(d["day"][:7], {"km": 0.0, "litres": 0.0})
        m["km"] += d["km"] or 0
        m["litres"] += d["litres"] or 0
    out["months"] = [
        {"month": k, "km": round(v["km"], 1),
         "lphk": round(v["litres"] / v["km"] * 100.0, 2) if v["km"] > 0.5 else None}
        for k, v in sorted(months.items())][-12:]
    out["odometer"] = series[-1].get("odo")
    out["since"] = series[0]["day"]
    out["days"] = series[-60:]
    return out


def driven_since(db, since):
    """Kilometres covered since an instant. Lives here rather than in book.py
    because the snapshot needs it and nothing should import the CLI."""
    if db is None or since is None:
        return 0.0
    total = 0.0
    day = datetime.fromtimestamp(since).strftime("%Y-%m-%d")
    for d in days(db):
        if d["day"] < day:
            continue
        if d["day"] == day:
            total += _partial_day(db, since)
        else:
            total += d.get("km") or 0
    return round(total, 2)


def _partial_day(db, since):
    """The part of one day that came after an instant, from the raw samples."""
    try:
        raw = db.execute(
            "SELECT t, speed FROM samples WHERE t >= ? AND t < ? ORDER BY t",
            (since, since + 86400)).fetchall()
    except sqlite3.Error:
        return 0.0
    km, prev = 0.0, None
    for r in raw:
        t, speed = r["t"], r["speed"]
        dt = 1.0 if prev is None else min(10.0, t - prev)
        prev = t
        if speed and speed > MOVING_KPH:
            km += speed * dt / 3600.0
    return km


def trips(db, n=20):
    return rows(db, "SELECT * FROM trips ORDER BY t0 DESC LIMIT ?", (n,),
                table="trips")


# ---- the sample stream ------------------------------------------------------

SAMPLE_COLS = ["t", "rpm", "speed", "load", "throttle", "coolant", "intake",
               "maf", "stft", "ltft", "timing", "lphk", "eff"]


def samples(db, since=None, until=None, limit=4000, step=1):
    """Raw rows over a span, thinned so a long span still fits in a graph."""
    if db is None:
        return []
    where, args = [], []
    if since is not None:
        where.append("t >= ?")
        args.append(since)
    if until is not None:
        where.append("t <= ?")
        args.append(until)
    sql = "SELECT " + ",".join(SAMPLE_COLS) + " FROM samples"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t ASC"
    out = rows(db, sql, tuple(args))
    if step > 1:
        out = out[::step]
    if len(out) > limit:
        # Decimate rather than truncate: the shape of the whole span is what a
        # graph is for, and the last N rows of a drive are not the drive.
        k = math.ceil(len(out) / limit)
        out = out[::k]
    return out


def stats(series, cols=None):
    """Min / max / mean / last per channel, which is what an AI can reason over.

    Handing a language model ten thousand raw rows is expensive and it reads
    them badly. Handing it the shape of each channel — and the few excursions
    that matter — is cheap and it reads that well.
    """
    cols = cols or [c for c in SAMPLE_COLS if c != "t"]
    out = {}
    for c in cols:
        vals = [r[c] for r in series if r.get(c) is not None]
        if not vals:
            continue
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        out[c] = {
            "n": n,
            "min": round(vals_sorted[0], 2),
            "max": round(vals_sorted[-1], 2),
            "mean": round(sum(vals) / n, 2),
            "p05": round(vals_sorted[int(n * 0.05)], 2),
            "p50": round(vals_sorted[n // 2], 2),
            "p95": round(vals_sorted[min(n - 1, int(n * 0.95))], 2),
            "last": round(vals[-1], 2),
        }
    return out


# ---- saved work -------------------------------------------------------------

def records(db, kind=None, n=50):
    sql = "SELECT * FROM records"
    args = ()
    if kind:
        sql += " WHERE kind = ?"
        args = (kind,)
    sql += " ORDER BY at DESC LIMIT ?"
    args = args + (n,)
    out = rows(db, sql, args, table="records")
    for r in out:
        try:
            r["payload"] = json.loads(r["payload"]) if r.get("payload") else None
        except (ValueError, TypeError):
            r["payload"] = None
    return out


def write_record(kind, label, payload, t0=None, t1=None, odo=None):
    """Append to the record book. The only write in this module, and it opens
    its own connection so the read path stays read-only."""
    os.makedirs(STATE, exist_ok=True)
    db = sqlite3.connect(DB, timeout=5.0)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, at REAL,
            odo REAL, label TEXT, t0 REAL, t1 REAL, payload TEXT)""")
        cur = db.execute(
            "INSERT INTO records (kind, at, odo, label, t0, t1, payload) "
            "VALUES (?,?,?,?,?,?,?)",
            (kind, time.time(), odo, label, t0, t1,
             json.dumps(payload) if payload is not None else None))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


# ---- one call for the whole car ---------------------------------------------

def _signal_catalogue(vehicle):
    """What lib/signals.py would drive, for the app to draw. Never raises: a
    malformed profile is a missing catalogue, not a missing snapshot."""
    try:
        import profile as profilelib
        import signals
        # THE GARAGE KEY FIRST, THE VEHICLE TABLE SECOND. The daemon picks
        # its learned readings from the profile that matches garage.current(),
        # because that key is what chose the database it is writing into. The
        # vehicle table's `vin` is re-read every survey and on a flaky link can
        # differ from the key for a tick (the bench emulator, which cycles
        # three VINs, is how this was found). Using the same source as the
        # daemon means the tile the app draws and the number the daemon polls
        # cannot come from two different profiles.
        import garage
        key = garage.current()
        vin = "" if key in (garage.SIM_KEY, "unknown") else key
        vin = vin or (vehicle or {}).get("vin") or ""
        slug = profilelib.for_vin(vin) if vin else None
        if not slug:
            return []
        doc, _ = profilelib.load(slug)
        return signals.catalogue(doc or {})
    except Exception:                                         # noqa: BLE001
        return []


def _actuator_reach(vehicle):
    """Which functional tests have a validated identifier on THIS car, for
    the Tests screen to say per button. Never raises; see _signal_catalogue."""
    try:
        import actuate as actlib
        doc = actlib.profile_doc()
        return actlib.reach(doc) if doc else {}
    except Exception:                                         # noqa: BLE001
        return {}


def snapshot(include_samples=False):
    """Everything, in one read. The API and the AI layer both start here."""
    db = connect()
    snap = live()
    v = vehicle(db)
    series = days(db)
    perf = performance(series)
    # A live sample that carries one wins (the simulator publishes it); then
    # the baseline-plus-driven figure the book keeps; then the daily rollups'
    # running total. OBD-II has no odometer PID, so none of these came off the
    # bus and lib/book.py says so wherever it is shown.
    odo = (snap.get("odometer_km") if snap.get("connected") else None)
    if odo is None and v.get("odometer_at") is not None:
        base = v.get("odometer_km")
        if base is not None:
            odo = float(base) + driven_since(db, v["odometer_at"])
    if odo is None:
        odo = (perf or {}).get("odometer") or v.get("odometer_km")
    f = faults(db)
    out = {
        "checked": int(time.time()),
        "units": units_for(),
        "connected": bool(snap.get("connected")),
        "simulated": bool((snap.get("simulated") if snap.get("connected") else False)
                          or v.get("simulated")),
        "status": status(snap),
        "stale": (max(0, int(time.time() - snap["t"])) if snap.get("t") else None),
        "vehicle": v,
        # The validated identifiers this car can read as live values: id, name
        # and unit, nothing else. The browser draws numbers; it does not send
        # requests, so it gets no header and no formula. Empty for a car with
        # no validated entries, which today is every car -- the point is that
        # the first one to arrive lands on a screen instead of in a file.
        "signals": _signal_catalogue(v),
        # Per test: does a button on the Tests screen reach this car, and on
        # whose word. Empty on every car nobody has mapped an actuator for,
        # and the screen says so next to the button rather than offering one
        # that does nothing.
        "actuators": _actuator_reach(v),
        "name": v.get("name", ""),
        "title": v.get("title", "") or v.get("name", ""),
        "odometer": round(odo, 1) if odo else None,
        "live": snap,
        "modules": modules(db),
        "faults": f,
        "active_faults": [x for x in f if x["active"]],
        "readiness": readiness(db),
        "mode06": mode06(db),
        "service": service(db, odo),
        "perf": perf,
        "trips": trips(db, 12),
        "have_history": bool(series),
    }
    if include_samples:
        out["samples"] = samples(db, since=time.time() - 3600, limit=1200)
    if db is not None:
        db.close()
    return out
