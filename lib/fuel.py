#!/usr/bin/env python3
"""The fuel log: notice a fill-up, ask how much, and work out what the car
really gets.

    omacar fuel status        what the app shows (JSON)
    omacar fuel list          the log, newest first
    omacar fuel add 9.4 3.79  log a fill-up by hand (gallons, $/gal; or
                              litres and $/L when the app is in metric).
                              --partial for a top-up that was not to full
    omacar fuel run           watch the tank (omacar-fuel.service)
    omacar fuel sim           ten simulated fill-ups over three months, for
                              seeing the screens; every one says Simulated
                              (--pending adds one waiting to be answered,
                              --clear removes them again)

WHY THE CAR CANNOT SIMPLY BE ASKED. OBD-II has a fuel LEVEL (mode 01 PID 0x2F,
a percentage of the tank) and nothing else about fuel that survives a fill-up:
no gallons dispensed, no price, no odometer. So the log has two halves that
meet in the middle:

  the car supplies WHEN. A level that jumps while the car is parked is a
    fill-up, and the watcher writes it down with the odometer and the two
    levels around it;
  the owner supplies HOW MUCH and AT WHAT PRICE, with two thumbs and no
    keyboard, on a card that only appears once the car is parked.

REAL ECONOMY is what comes out of the pump, not what the car estimates:
distance between two fill-ups to FULL, divided by the fuel it took to refill.
Nothing else is honest. A part-fill cannot end a measurement (the tank is at
an unknown level), but its gallons belong to the measurement it falls in.

THE CAR'S OWN ESTIMATE is the mass-air-flow figure the dashboard integrates.
It is shown next to the real one, labelled the car's estimate, because the gap
between the two is the useful number: it says how far to trust the instant
economy gauge on this car.

All figures are metric on disk (litres, km, L/100 km), like the rest of the
record; the app converts at the edge.
"""

import json
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
STATE = os.path.join(STATE_DIR, "fuel.json")        # the watcher's heartbeat
TRACE = os.path.join(STATE_DIR, "fuel-trace.json")  # its last readings, across a restart

LITRES_PER_GALLON = 3.785411784
KM_PER_MILE = 1.609344

# ---- what counts as a fill-up ---------------------------------------------------------

JUMP_PCT = 15.0         # percentage points of tank, between two settled levels
WINDOW_S = 30.0         # the median is of this many seconds of readings
MIN_READINGS = 2        # a median of one reading is a reading
GAP_S = 45.0            # no reading for this long: the ignition was cycled
PARKED_KPH = 3.0        # the same "still" the rest of the app uses
SAME_STOP_S = 15 * 60   # two jumps this close are one stop
STABLE_PCT = 3.0        # an open stretch is settled when its last window spans no more than this
RING = 1500             # readings kept by the watcher (~50 minutes at one per 2 s)
SAMPLE_S = 2.0
PENDING_SHOWN_S = 3 * 24 * 3600     # an unanswered question is dropped after three days


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _level(win):
    return median([x["fuel"] for x in win])


def _event(kind, at, before, after, odo, gap=None, tank_l=None):
    ev = {"kind": kind, "at": at, "level_before": round(before, 1),
          "level_after": round(after, 1), "odo_km": odo}
    if gap is not None:
        ev["gap_s"] = round(gap)
    if tank_l:
        ev["est_litres"] = round((after - before) / 100.0 * tank_l, 1)
    return ev


def _across(a, b, last, now, jump, tank_l):
    """A run of readings, a silence, another run: the ignition was cycled.

    The level before is the median of the last WINDOW_S seconds the car was
    heard; the level after is the median of the first WINDOW_S seconds it is
    heard again. Medians, because a tank slops on every hill and every corner
    and a single reading is a reading of the slop.
    """
    if a[-1].get("speed", 0) > PARKED_KPH or b[0].get("speed", 0) > PARKED_KPH:
        # A dropout in the adapter's link mid-drive is not a stop, and a tank
        # read while the car is moving is the least trustworthy tank there is.
        return None
    before = [x for x in a if x["t"] >= a[-1]["t"] - WINDOW_S]
    after = [x for x in b if x["t"] <= b[0]["t"] + WINDOW_S]
    if len(before) < MIN_READINGS or len(after) < MIN_READINGS:
        return None
    if last and now - b[0]["t"] < WINDOW_S:
        return None                      # still inside the window: not settled
    lo, hi = _level(before), _level(after)
    if hi - lo < jump:
        return None
    odo = b[0].get("odo") if b[0].get("odo") is not None else a[-1].get("odo")
    return _event("ignition", b[0]["t"], lo, hi, odo, b[0]["t"] - a[-1]["t"], tank_l)


def _stretches(run):
    out, cur = [], []
    for x in run:
        if x.get("speed", 0) <= PARKED_KPH:
            cur.append(x)
        else:
            if cur:
                out.append((cur, True))
            cur = []
    if cur:
        out.append((cur, False))
    return out


def _within(run, now, jump, tank_l):
    """A stretch with the car standing still and the ignition ON, in which the
    level steps up: somebody fuelled without switching off."""
    for st, ended in _stretches(run):
        t0 = st[0]["t"]
        if st[-1]["t"] - t0 < 2 * WINDOW_S:
            continue
        first = [x for x in st if x["t"] <= t0 + WINDOW_S]
        if len(first) < MIN_READINGS:
            continue
        base = _level(first)
        tail = [x for x in st if x["t"] >= st[-1]["t"] - WINDOW_S]
        if tail[0]["t"] < t0 + WINDOW_S or len(tail) < MIN_READINGS:
            continue
        levels = [x["fuel"] for x in tail]
        settled = ended or (max(levels) - min(levels) <= STABLE_PCT)
        if not settled:
            continue
        top = median(levels)
        if top - base < jump:
            continue
        cross = next((x for x in st if x["fuel"] >= base + jump / 2.0), tail[0])
        return _event("stationary", cross["t"], base, top, cross.get("odo"), None, tank_l)
    return None


def detect(samples, now=None, tank_l=None, jump=JUMP_PCT):
    """Fill-ups in a trace of readings, oldest first.

    `samples` are dicts {t, fuel, speed, odo?}: one per reading of the level
    while the car was connected, in seconds and percent of tank. PURE: the
    watcher calls it on its ring, the tests on synthetic traces, and it
    returns the same events for the same trace however often it is asked.

    A fill-up is a rise of at least `jump` percentage points between two
    levels that are each the median of WINDOW_S seconds, with the car stopped
    at both ends (or standing still throughout), and not yet settled events
    are held back until their window has closed. A fall is never one.
    """
    s = sorted((x for x in samples if x.get("fuel") is not None), key=lambda x: x["t"])
    if not s:
        return []
    if now is None:
        now = s[-1]["t"]
    runs, cur = [], []
    for x in s:
        if cur and x["t"] - cur[-1]["t"] > GAP_S:
            runs.append(cur)
            cur = []
        cur.append(x)
    runs.append(cur)
    out = []
    for i, run in enumerate(runs):
        if i:
            ev = _across(runs[i - 1], run, i == len(runs) - 1, now, jump, tank_l)
            if ev:
                out.append(ev)
        ev = _within(run, now, jump, tank_l)
        if ev:
            out.append(ev)
    out.sort(key=lambda e: e["at"])
    # Two events one stop apart are one stop.
    merged = []
    for e in out:
        if merged and e["at"] - merged[-1]["at"] < SAME_STOP_S:
            continue
        merged.append(e)
    return merged


# ---- the maths of economy -------------------------------------------------------------

MIN_INTERVAL_KM = 20.0              # shorter than this and a gallon's rounding is the answer
PLAUSIBLE_L100 = (2.0, 40.0)        # outside this the odometer or the pump is wrong
CAR_COVERAGE = 0.6                  # the car's estimate must cover this much of the distance


def to_mpg(l100):
    return None if not l100 or l100 <= 0 else records.MPG_CONSTANT / l100


def intervals(fills):
    """Real economy between fill-ups to FULL.

    `fills` are dicts: id, at, odo_km, litres, cost, full (bool), status
    ("logged" | "skipped" | "pending"), car_km, car_litres (the car's own
    integration over the distance since the fill-up before). Returns
    {"intervals": [...], "dropped": [...]} oldest first.

    THE RULE. A measurement runs from one full fill-up to the next. The fuel
    burned is the litres put in at the END plus any part-fills between (the
    first fill-up's litres only topped the tank up to where the measurement
    starts). If anything in between has an unknown quantity (a fill-up that
    was skipped or never answered), the measurement is dropped -- guessing a
    figure would put a confident number on a thing nobody measured -- and the
    next full fill-up starts a new one.
    """
    rows = sorted(fills, key=lambda f: (f["at"], f.get("id") or 0))
    out, dropped = [], []
    anchor = None
    acc = None

    def fresh():
        return {"litres": 0.0, "cost": 0.0, "cost_ok": True,
                "car_km": 0.0, "car_l": 0.0, "broken": None}

    for f in rows:
        known = f.get("status") == "logged" and f.get("litres") is not None
        if anchor is None:
            if known and f.get("full"):
                anchor, acc = f, fresh()
            continue
        if not known:
            acc["broken"] = acc["broken"] or "a fill-up in between has no quantity"
            continue
        acc["litres"] += f["litres"]
        if f.get("cost") is None:
            acc["cost_ok"] = False
        else:
            acc["cost"] += f["cost"]
        acc["car_km"] += f.get("car_km") or 0.0
        acc["car_l"] += f.get("car_litres") or 0.0
        if not f.get("full"):
            continue
        why = acc["broken"]
        km = None
        if not why:
            if anchor.get("odo_km") is None or f.get("odo_km") is None:
                why = "the odometer was not known"
            else:
                km = f["odo_km"] - anchor["odo_km"]
                if km < MIN_INTERVAL_KM:
                    why = "too short a distance"
        l100 = None
        if not why:
            l100 = acc["litres"] / km * 100.0 if acc["litres"] > 0 else None
            if l100 is None or not PLAUSIBLE_L100[0] <= l100 <= PLAUSIBLE_L100[1]:
                why = "the figures do not add up (check the odometer)"
        if why:
            dropped.append({"to_id": f.get("id"), "at": f["at"], "why": why})
        else:
            car = None
            if acc["car_km"] >= CAR_COVERAGE * km and acc["car_l"] > 0:
                car = acc["car_l"] / acc["car_km"] * 100.0
            cost = acc["cost"] if acc["cost_ok"] else None
            out.append({
                "from_id": anchor.get("id"), "to_id": f.get("id"),
                "from_at": anchor["at"], "at": f["at"], "km": round(km, 1),
                "litres": round(acc["litres"], 2), "cost": None if cost is None else round(cost, 2),
                "l100": round(l100, 2), "mpg": round(to_mpg(l100), 1),
                "cost_per_km": None if cost is None else round(cost / km, 4),
                "car_l100": None if car is None else round(car, 2),
                "car_mpg": None if car is None else round(to_mpg(car), 1),
                # Positive: the car flatters itself (its estimate is thriftier
                # than the pump found). In L/100 km terms, as a share of real.
                "car_error_pct": None if car is None else round((l100 - car) / l100 * 100.0, 1),
            })
        anchor, acc = f, fresh()
    return {"intervals": out, "dropped": dropped}


def month_key(at):
    return time.strftime("%Y-%m", time.localtime(at))


def months(fills, n=12):
    """Spend and fuel by calendar month, newest first. Every logged fill-up
    counts, full or not: the money left the wallet either way."""
    by = {}
    for f in fills:
        if f.get("status") != "logged":
            continue
        m = by.setdefault(month_key(f["at"]), {"month": month_key(f["at"]),
                                               "spend": 0.0, "litres": 0.0, "fills": 0,
                                               "unpriced": 0})
        m["fills"] += 1
        m["litres"] += f.get("litres") or 0.0
        if f.get("cost") is None:
            m["unpriced"] += 1
        else:
            m["spend"] += f["cost"]
    out = sorted(by.values(), key=lambda m: m["month"], reverse=True)[:n]
    for m in out:
        m["spend"] = round(m["spend"], 2)
        m["litres"] = round(m["litres"], 2)
    return out


def summarise(fills, now=None):
    """Everything the screens need from the log in one pass."""
    now = now if now is not None else time.time()
    ec = intervals(fills)
    iv = ec["intervals"]
    km = sum(i["km"] for i in iv)
    litres = sum(i["litres"] for i in iv)
    priced = [i for i in iv if i["cost"] is not None]
    pkm = sum(i["km"] for i in priced)
    car = [i for i in iv if i["car_l100"] is not None]
    car_km = sum(i["km"] for i in car)
    mths = months(fills)
    this = month_key(now)
    cur = next((m for m in mths if m["month"] == this), None)
    last = iv[-1] if iv else None
    return {
        "intervals": iv, "dropped": ec["dropped"],
        "last": last,
        "average_l100": round(litres / km * 100.0, 2) if km > 0 and litres > 0 else None,
        "average_mpg": round(to_mpg(litres / km * 100.0), 1) if km > 0 and litres > 0 else None,
        "cost_per_km": round(sum(i["cost"] for i in priced) / pkm, 4) if pkm > 0 else None,
        "car_average_l100": (round(sum(i["car_l100"] * i["km"] for i in car) / car_km, 2)
                             if car_km > 0 else None),
        "months": mths,
        "month": {"month": this, "spend": cur["spend"] if cur else 0.0,
                  "fills": cur["fills"] if cur else 0,
                  "unpriced": cur["unpriced"] if cur else 0},
        "measured_km": round(km, 1),
    }


def car_estimate(rows):
    """The car's own economy over rows of (speed km/h, L/100 km): distance-
    weighted, so a crawl does not outvote a motorway. Returns (km, litres)
    covered by rows where the car reported an instant economy."""
    km = litres = 0.0
    for speed, lphk in rows:
        if not speed or speed <= records.MOVING_KPH or not lphk or lphk <= 0:
            continue
        k = speed / 3600.0            # one second of driving
        km += k
        litres += lphk / 100.0 * k
    return km, litres


# ---- the log on disk ------------------------------------------------------------------

SCHEMA = """CREATE TABLE IF NOT EXISTS fuel_fills (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL, level_before REAL, level_after REAL, odo_km REAL,
    litres REAL, cost REAL, is_full INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0,
    simulated INTEGER NOT NULL DEFAULT 0,
    car_km REAL, car_litres REAL, est_litres REAL, added REAL)"""


def open_db():
    records.refresh_db()
    os.makedirs(os.path.dirname(records.DB), exist_ok=True)
    db = sqlite3.connect(records.DB, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    db.execute("CREATE INDEX IF NOT EXISTS fuel_fills_at ON fuel_fills(at)")
    db.commit()
    return db


def _row(r):
    d = dict(r)
    d["full"] = bool(d.pop("is_full"))
    d["manual"] = bool(d["manual"])
    d["simulated"] = bool(d["simulated"])
    return d


def all_fills(db=None):
    own = db is None
    db = db or open_db()
    try:
        return [_row(r) for r in db.execute("SELECT * FROM fuel_fills ORDER BY at, id")]
    finally:
        if own:
            db.close()


def _car_since(since, until):
    """What the car's own economy gauge says it burned between two instants,
    from the sample stream. (km, litres); (0, 0) where there is no stream."""
    if since is None:
        return 0.0, 0.0
    tdb = records.connect()
    try:
        if tdb is None or not records.has(tdb, "samples"):
            return 0.0, 0.0
        cols = records.table_columns(tdb, "samples")
        if "lphk" not in cols or "speed" not in cols:
            return 0.0, 0.0
        cur = tdb.execute("SELECT speed, lphk FROM samples WHERE t > ? AND t <= ?",
                          (since, until))
        return car_estimate(cur)
    except sqlite3.Error:
        return 0.0, 0.0
    finally:
        if tdb is not None:
            tdb.close()


def _odometer_now():
    try:
        import book
        return book.odometer()[0]
    except Exception:                                         # noqa: BLE001 -- no odometer yet
        return None


def record_detection(ev, tank_l=None, db=None):
    """A detected fill-up, written as a question waiting for an answer.
    Returns the row id, or None when this stop is already in the log."""
    own = db is None
    db = db or open_db()
    try:
        near = db.execute("SELECT id FROM fuel_fills WHERE simulated=0 AND ABS(at-?) < ?",
                          (ev["at"], SAME_STOP_S)).fetchone()
        if near:
            return None
        prev = db.execute("SELECT MAX(at) FROM fuel_fills WHERE simulated=0 AND at < ?",
                          (ev["at"],)).fetchone()[0]
        car_km, car_l = _car_since(prev, ev["at"])
        odo = ev.get("odo_km")
        if odo is None:
            odo = _odometer_now()
        cur = db.execute(
            "INSERT INTO fuel_fills (at, level_before, level_after, odo_km, status, is_full,"
            " car_km, car_litres, est_litres, added) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ev["at"], ev["level_before"], ev["level_after"], odo, "pending", 1,
             car_km, car_l, ev.get("est_litres"), time.time()))
        db.commit()
        return cur.lastrowid
    finally:
        if own:
            db.close()


def _check_amounts(litres, cost):
    litres = float(litres)
    if not 0.5 <= litres <= 200.0 or litres != litres:
        raise ValueError("that is not a fill-up quantity")
    if cost is not None:
        cost = float(cost)
        if not 0.0 <= cost <= 5000.0 or cost != cost:
            raise ValueError("that is not a price")
        cost = round(cost, 2)
    return round(litres, 2), cost


def answer(fill_id, litres, cost, full=True):
    litres, cost = _check_amounts(litres, cost)
    db = open_db()
    try:
        r = db.execute("SELECT id FROM fuel_fills WHERE id=? AND status='pending'",
                       (int(fill_id),)).fetchone()
        if not r:
            raise ValueError("nothing is waiting to be answered")
        db.execute("UPDATE fuel_fills SET litres=?, cost=?, is_full=?, status='logged'"
                   " WHERE id=?", (litres, cost, 1 if full else 0, int(fill_id)))
        db.commit()
    finally:
        db.close()


def skip(fill_id):
    db = open_db()
    try:
        db.execute("UPDATE fuel_fills SET status='skipped' WHERE id=? AND status='pending'",
                   (int(fill_id),))
        db.commit()
    finally:
        db.close()


def add(litres, cost, full=True, at=None, odo_km=None):
    """A fill-up the watcher did not see (the tablet was off; the sender is
    wrong). Same table, marked manual."""
    litres, cost = _check_amounts(litres, cost)
    at = float(at) if at else time.time()
    if odo_km is None:
        odo_km = _odometer_now()
    db = open_db()
    try:
        prev = db.execute("SELECT MAX(at) FROM fuel_fills WHERE simulated=0 AND at < ?",
                          (at,)).fetchone()[0]
        car_km, car_l = _car_since(prev, at)
        cur = db.execute(
            "INSERT INTO fuel_fills (at, odo_km, litres, cost, is_full, status, manual,"
            " car_km, car_litres, added) VALUES (?,?,?,?,?,'logged',1,?,?,?)",
            (at, odo_km, litres, cost, 1 if full else 0, car_km, car_l, time.time()))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def remove(fill_id):
    db = open_db()
    try:
        db.execute("DELETE FROM fuel_fills WHERE id=?", (int(fill_id),))
        db.commit()
    finally:
        db.close()


# ---- what the app reads ---------------------------------------------------------------

def _tank_l():
    db = records.connect()
    try:
        return (records.vehicle(db) or {}).get("tank_l")
    finally:
        if db is not None:
            db.close()


def _watcher():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"watching": False, "age": None}
    age = max(0.0, time.time() - float(st.get("at", 0)))
    return {"watching": age < 30.0, "age": round(age), "level": st.get("level")}


def read_public(now=None):
    """For the API. `source` is "real" when the log holds anything the owner
    or the watcher wrote, "sim" when it holds only `omacar fuel sim`'s rows
    (the screens then say Simulated), "none" when it is empty."""
    now = now if now is not None else time.time()
    try:
        every = all_fills()
    except sqlite3.Error:
        every = []
    real = [f for f in every if not f["simulated"]]
    fills = real if real else every
    source = "real" if real else ("sim" if every else "none")
    pending = [f for f in fills if f["status"] == "pending"
               and now - f["at"] < PENDING_SHOWN_S]
    pend = pending[-1] if pending else None
    s = summarise(fills, now)
    priced = [f for f in fills if f["status"] == "logged" and f.get("cost") and f.get("litres")]
    last_price = priced[-1]["cost"] / priced[-1]["litres"] if priced else None
    out = {
        "present": bool(every), "source": source, "simulated": source == "sim",
        "units": records.units_for(), "tank_l": _tank_l(),
        "watcher": _watcher(),
        "pending": pend,
        "fills": list(reversed(fills))[:60],
        "last_price_per_l": None if last_price is None else round(last_price, 4),
        "at": now,
    }
    out.update(s)
    return out


# ---- the watcher ----------------------------------------------------------------------

def _write_json(path, doc):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


def _load_trace():
    try:
        with open(TRACE, encoding="utf-8") as f:
            return [x for x in json.load(f).get("samples", []) if "t" in x and "fuel" in x]
    except (OSError, ValueError):
        return []


def take_reading(snap, last_t):
    """One reading from records.live(), or None. Nothing is read from a
    sample that is not live, is a hand-off, or has no level."""
    if not snap or not snap.get("connected") or snap.get("handover"):
        return None
    v = snap.get("values") or {}
    fuel = v.get("FUEL_LEVEL")
    t = snap.get("t")
    if fuel is None or t is None or (last_t is not None and t <= last_t):
        return None
    return {"t": float(t), "fuel": float(fuel), "speed": float(v.get("SPEED") or 0.0),
            "odo": snap.get("odometer_km")}


def run():
    ring = _load_trace()[-RING:]
    last_t = ring[-1]["t"] if ring else None
    saved = 0.0
    while True:
        now = time.time()
        try:
            records.refresh_db()
            rd = take_reading(records.live(), last_t)
            if rd:
                ring.append(rd)
                last_t = rd["t"]
                del ring[:-RING]
            for ev in detect(ring, now=now, tank_l=_tank_l()):
                if now - ev["at"] < 6 * 3600:
                    record_detection(ev)
            if now - saved >= 10.0:
                saved = now
                _write_json(TRACE, {"samples": ring})
                _write_json(STATE, {"at": now, "level": ring[-1]["fuel"] if ring else None,
                                    "readings": len(ring)})
        except Exception as e:                                # noqa: BLE001 -- keep watching
            print(f"  fuel: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(SAMPLE_S)


# ---- the simulator --------------------------------------------------------------------

def sim(pending=False, seed=1017, tank_l=40.0):
    """Ten fill-ups over three months, believable for a CR-Z hybrid: about 35
    mpg at the pump, the car's own gauge a few per cent kinder, one top-up that
    was not to full, one nobody answered (skipped), a thirstier fortnight.
    Every row says simulated, so the screens say so.

    Built from a tank, not from a table: each stretch burns fuel, and what a
    fill-up takes is what the tank is missing, so the maths in intervals() has
    the same physics to find that a real car would give it.
    """
    rnd = random.Random(seed)
    day = 24 * 3600
    now = time.time()
    t = now - 92 * day
    odo = 137_000.0                      # km: about 85,100 miles
    price = 3.62                         # $ per gallon
    missing = 0.0                        # litres the tank is short of full
    db = open_db()
    try:
        db.execute("DELETE FROM fuel_fills WHERE simulated=1")
        for i in range(10):
            t += rnd.uniform(6.5, 11.0) * day
            km = rnd.uniform(330.0, 470.0)
            l100 = rnd.gauss(6.7, 0.5) + (1.1 if i in (3, 4) else 0.0)
            burned = km * l100 / 100.0
            missing += burned
            odo += km
            price = max(3.25, min(4.15, price + rnd.uniform(-0.12, 0.14)))
            full, status = 1, "logged"
            litres = missing                       # a full tank takes what is missing
            if i == 5:
                full, litres = 0, missing * 0.5    # a top-up on the way home
            missing -= litres
            before = rnd.uniform(14.0, 26.0)
            after = min(98.0, before + litres / tank_l * 100.0)
            cost = round(litres * price / LITRES_PER_GALLON, 2)
            shown_l, shown_c = round(litres, 2), cost
            if i == 7:
                status, shown_l, shown_c = "skipped", None, None
            car_km = km * 0.97
            car_l = car_km * (l100 * rnd.uniform(0.93, 0.97)) / 100.0
            db.execute(
                "INSERT INTO fuel_fills (at, level_before, level_after, odo_km, litres, cost,"
                " is_full, status, manual, simulated, car_km, car_litres, est_litres, added)"
                " VALUES (?,?,?,?,?,?,?,?,0,1,?,?,?,?)",
                (t, round(before, 1), round(after, 1), round(odo, 1), shown_l, shown_c,
                 full, status, car_km, car_l, round(litres, 1), now))
        if pending:
            db.execute(
                "INSERT INTO fuel_fills (at, level_before, level_after, odo_km, status, is_full,"
                " manual, simulated, car_km, car_litres, est_litres, added)"
                " VALUES (?,?,?,?,'pending',1,0,1,0,0,?,?)",
                (now - 90, 21.0, 96.0, round(odo + 410.0, 1), round(0.75 * tank_l, 1), now))
        db.commit()
    finally:
        db.close()
    return 10 + (1 if pending else 0)


def clear_sim():
    db = open_db()
    try:
        n = db.execute("DELETE FROM fuel_fills WHERE simulated=1").rowcount
        db.commit()
        return n
    finally:
        db.close()


# ---- the command line -----------------------------------------------------------------

def _ago(at):
    return time.strftime("%d %b %Y %H:%M", time.localtime(at))


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "list":
        u = records.units_for()
        pub = read_public()
        if pub["source"] == "sim":
            print("  (Simulated: omacar fuel sim --clear removes these)")
        if not pub["fills"]:
            print("  no fill-ups yet: omacar fuel run, or omacar fuel add <amount> <price>")
            return 0
        for f in pub["fills"]:
            amt = "—" if f["litres"] is None else f"{f['litres'] * u['litre']:.1f} {u['vol']}"
            cost = "—" if f["cost"] is None else f"${f['cost']:.2f}"
            odo = "—" if f["odo_km"] is None else f"{f['odo_km'] * u['km']:,.0f} {u['dist']}"
            print(f"  {_ago(f['at'])}  {amt:>9}  {cost:>8}  {odo:>11}  "
                  f"{'full' if f['full'] else 'part'}  {f['status']}")
        s = pub.get("last")
        if s:
            e = s["mpg"] if u["system"] == "imperial" else s["l100"]
            print(f"\n  last measured: {e} {u['econ']} over {s['km'] * u['km']:,.0f} {u['dist']}")
        return 0
    if what == "add":
        rest = [a for a in argv[1:] if not a.startswith("--")]
        if len(rest) != 2:
            print("  omacar fuel add <quantity> <price per unit> [--partial]", file=sys.stderr)
            return 2
        u = records.units_for()
        try:
            qty, price = float(rest[0]), float(rest[1])
            litres = qty / u["litre"]
            cost = qty * price
            i = add(litres, cost, full="--partial" not in argv)
        except ValueError as e:
            print(f"  {e}", file=sys.stderr)
            return 2
        print(f"  logged #{i}: {qty:g} {u['vol']} for ${cost:.2f}")
        return 0
    if what == "run":
        return run() or 0
    if what == "sim":
        if "--clear" in argv:
            print(f"  removed {clear_sim()} simulated fill-up(s)")
            return 0
        n = sim(pending="--pending" in argv)
        print(f"  wrote {n} simulated fill-ups. They say Simulated on every screen;"
              f" omacar fuel sim --clear removes them.")
        return 0
    print(f"  omacar fuel: no such thing as '{what}'.\n"
          "  status | list | add | run | sim", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
