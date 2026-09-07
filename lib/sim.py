"""A simulated 2015 Honda CR-Z, wired into the same files the real daemon writes.

The adapter is not always in the car, and a card that says "no telemetry yet"
teaches you nothing about whether the card works. So this is a whole car: a
year of driving, the faults it has picked up along the way, and a service book
— written into the same `telemetry.db` and `live.json` the daemon uses, so
everything downstream reads it without knowing the difference.

It is deliberately NOT a lie. Every row it writes carries `sim = 1`, the
vehicle record says `simulated`, and `omacar sim status` says so out loud. The
point is a faithful rehearsal, not a fake car.

    omacar sim seed     write a year of history, faults and service records
    omacar sim start    run the live loop — 1 Hz samples and live.json
    omacar sim stop

How the history is made
-----------------------
A year of one-second samples would be a hundred megabytes to say something a
daily rollup says in a line, so the two halves are generated differently and
made to agree:

  `days`    365 rollups, generated from a trip-level model — how far, what
            kind of driving, what the weather was doing to it
  `samples` the last fortnight at 1 Hz, synthesised from those same trips by
            driving them second by second, then scaled so each trip's fuel
            comes out at exactly the figure the rollup already claimed

So the sparkline and the odometer can never disagree, which is the failure
that would give the whole thing away.

Everything is seeded from the date, so re-seeding reproduces the same year
rather than inventing a new one every time you run it.
"""

import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garage   # noqa: E402
import records  # noqa: E402

STATE = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
LIVE = os.path.join(STATE, "live.json")
PIDFILE = os.path.join(STATE, "sim.pid")
# The bidirectional command channel. One small file the app writes and the
# simulator reads — a socket would be a second thing that can break, and there
# is exactly one writer and one reader, both on this machine.
COMMAND = os.path.join(STATE, "command.json")

# ---- the car ---------------------------------------------------------------

VEHICLE = {
    "year": 2015, "make": "Honda", "model": "CR-Z", "trim": "EX  ·  CVT",
    "vin": "JHMZF1D64FS004917",
    "engine": "1.5 L i-VTEC + IMA",
    "drivetrain": "FWD hybrid, CVT",
    "power_kw": 97.0,
    "mass_kg": 1250.0,
    "tank_l": 40.0,
    "displacement_l": 1.5,
    "redline": 6300,
    "protocol": "ISO 15765-4 (CAN 11/500)",
    "adapter": "OBDLink SX",
    "port": "/dev/ttyUSB0",
    "simulated": True,
}


def vehicle_from_profile(slug):
    """VEHICLE, but describing whatever car a profile names.

    THE REASON THIS EXISTS.

    The dictionary above is one invented car, and on 2026-09-03 its invented
    "6-speed manual" was read out of this file and stated as fact about a real
    CR-Z, to that car's owner, who had to correct it. The simulator is not
    supposed to be a source of truth about anybody's vehicle, and the surest
    way to stop it becoming one is to let it be driven by the same profile the
    real car uses.

    A slug with no profile, or a profile missing a field, keeps the built-in
    value -- so `omacar sim` with no arguments behaves exactly as it always
    has, and simulating somebody else's car is a matter of writing a profile
    rather than editing this file.
    """
    out = dict(VEHICLE)
    try:
        import profile as profilelib
        doc, _p = profilelib.load(slug)
        car = (doc or {}).get("car") or {}
    except Exception:
        return out
    if not car:
        return out
    for key in ("make", "model", "engine", "drivetrain", "trim", "protocol",
                "mass_kg", "power_kw", "tank_l", "displacement_l", "redline"):
        if car.get(key) not in (None, ""):
            out[key] = car[key]
    # `years` is deliberately NOT used to set the model year. It is a coverage
    # RANGE -- [2011, 2016] means "this profile describes CR-Zs from 2011 to
    # 2016" -- so reading years[0] turned a 2015 into a 2011. A profile that
    # spans six model years cannot say which one is being simulated, and
    # guessing the earliest is not better than keeping the built-in.
    years = car.get("years")
    if isinstance(years, list) and len(years) == 1:
        out["year"] = years[0]
    # The VIN is NOT taken from the profile: a profile carries a vin_prefix
    # describing a model, never a whole VIN, and a simulator that borrowed a
    # real one would be putting somebody's actual car number into fake data.
    out["simulated"] = True
    return out

# Odometer as of the seed date. A 2015 car in 2026 at a shade under 12,000
# km a year — the mileage decides every service interval below, so it is the
# one number everything else hangs off.
ODO_NOW = 137842.0

# What the car costs to feed. Only used for the cost line; change it here.
FUEL_PRICE = 1.62

# A CVT, expressed as the SPAN of road speed per 1000 rpm the ratio can cover,
# on a 195/55R16 rolling circumference.
#
# This used to be six fixed steps, and the car it is imitating does not have
# six of anything. The tell was in the owner's own recorded driving: 19,439
# moving samples produce ONE broad continuous peak in rpm-per-km/h with a
# smooth tail, where a six-speed would put six spikes. That histogram was read
# and explained away as highway driving smearing the lower gears, which is
# what happens when a model is trusted over a measurement.
#
# The tall end is anchored to that measurement: the real car's dominant cruise
# ratio is about 19.8 rpm per km/h, i.e. 1000/19.8 = 50.5 km/h per 1000 rpm.
CVT_TALL = 50.5          # economy cruise, the tallest it will pull
CVT_SHORT = 11.0         # pulling away from rest
CVT_TARGET_RPM = 1450    # what it holds at light throttle before it has to rev
IDLE_RPM = 780

# Aerodynamics and rolling resistance, for working out how hard the engine is
# having to push at any given moment.
CDA = 0.62          # m², drag coefficient × frontal area
CRR = 0.011         # rolling resistance coefficient
AIR = 1.20          # kg/m³

AFR = 14.7
FUEL_DENSITY_G_PER_L = 745.0

MOVING_KPH = 3.0
DAYS_OF_SAMPLES = 14
DAYS_OF_HISTORY = 365


# ---- the year --------------------------------------------------------------

def rng_for(d):
    """A generator that depends only on the date, so history is stable."""
    return random.Random(d.toordinal() * 7919 + 104729)


def season_factor(d):
    """What the weather does to economy. Winter is the expensive season.

    Cold air is denser, the engine spends longer warming up, and winter tyres
    and slush both cost. Summer costs less, but air conditioning is not free.
    """
    # Peaks in January, troughs in July (northern hemisphere).
    phase = math.cos((d.timetuple().tm_yday - 10) / 365.0 * 2 * math.pi)
    winter = max(0.0, phase)
    summer = max(0.0, -phase)
    return 1.0 + 0.14 * winter + 0.05 * summer


# Economy by the kind of driving, before weather and before the cold-start
# penalty. A CR-Z does mid-fives on a motorway and high-sevens around town.
BASE_LPHK = {"commute": 6.0, "errand": 7.2, "highway": 4.9, "long": 5.2}

TRIP_LABELS = {
    "commute": ["to work", "home from work"],
    "errand": ["shops", "school run", "errands", "into town", "the tip"],
    "highway": ["out of town", "back from out of town"],
    "long": ["road trip"],
}


def day_plan(d):
    """The trips taken on one day: (start hour, kind, km)."""
    r = rng_for(d)
    out = []
    weekend = d.weekday() >= 5

    # Roughly one day in eleven the car does not move at all.
    if r.random() < 0.09:
        return out

    if not weekend:
        if r.random() < 0.88:
            km = round(r.uniform(17.0, 27.0), 1)
            out.append((8.0 + r.uniform(-0.5, 0.6), "commute", km))
            out.append((17.4 + r.uniform(-0.4, 1.8), "commute",
                        round(km * r.uniform(0.95, 1.15), 1)))
        for _ in range(r.choice([0, 0, 1, 1, 2])):
            out.append((r.uniform(12.0, 21.0), "errand",
                        round(r.uniform(2.5, 14.0), 1)))
    else:
        for _ in range(r.choice([0, 1, 1, 2, 2, 3])):
            out.append((r.uniform(9.0, 20.0), "errand",
                        round(r.uniform(3.0, 22.0), 1)))
        if r.random() < 0.17:
            out.append((r.uniform(8.0, 11.0), "highway",
                        round(r.uniform(60.0, 180.0), 1)))

    # A few times a year the car goes properly somewhere.
    if r.random() < 0.012:
        out.append((r.uniform(7.0, 10.0), "long", round(r.uniform(220.0, 520.0), 1)))

    out.sort()
    return out


def trip_economy(d, kind, km, r):
    """L/100km for one trip, weather and cold start included."""
    lphk = BASE_LPHK[kind] * season_factor(d) * r.uniform(0.92, 1.09)
    # A cold engine is thirsty and a short trip is nearly all cold engine.
    if km < 8.0:
        lphk *= 1.0 + 0.55 * (1.0 - km / 8.0)
    return lphk


def year_of_trips(today):
    """Every trip in the window, oldest first, with its rollup figures."""
    trips = []
    for i in range(DAYS_OF_HISTORY - 1, -1, -1):
        d = today - timedelta(days=i)
        r = rng_for(d)
        for (hour, kind, km) in day_plan(d):
            lphk = trip_economy(d, kind, km, r)
            start = datetime.combine(d, datetime.min.time()) + timedelta(hours=hour)
            # Average speed follows the kind of road, and decides how long it
            # took — which the trip list shows and the engine-hours use.
            avg = {"commute": 38.0, "errand": 27.0,
                   "highway": 88.0, "long": 92.0}[kind] * r.uniform(0.9, 1.12)
            moving_s = int(km / avg * 3600)
            # Stationary time: town driving is mostly waiting.
            idle_s = int(moving_s * {"commute": 0.22, "errand": 0.34,
                                     "highway": 0.06, "long": 0.05}[kind])
            trips.append({
                "t0": start.timestamp(),
                "km": km, "kind": kind, "lphk": lphk,
                "litres": km * lphk / 100.0,
                "moving_s": moving_s, "idle_s": idle_s,
                "t1": start.timestamp() + moving_s + idle_s,
                "top_kph": {"commute": 96.0, "errand": 62.0,
                            "highway": 118.0, "long": 122.0}[kind]
                           * r.uniform(0.86, 1.06),
                "label": r.choice(TRIP_LABELS[kind]),
            })
    trips.sort(key=lambda x: x["t0"])

    # Today's schedule runs to the end of the day, but the day has not. A trip
    # that has not happened yet must not be in the record: it put samples in
    # the database with timestamps in the future, which made "the last hour"
    # of the data lab show this evening's commute at four in the morning.
    now = time.time()
    out = []
    for t in trips:
        if t["t0"] > now:
            continue
        if t["t1"] > now:
            # A drive in progress, kept but truncated to what has actually
            # been driven so far.
            done = max(0.0, (now - t["t0"]) / max(1.0, t["t1"] - t["t0"]))
            t = dict(t)
            t["t1"] = now
            t["km"] = round(t["km"] * done, 2)
            t["litres"] = t["litres"] * done
            t["moving_s"] = int(t["moving_s"] * done)
            t["idle_s"] = int(t["idle_s"] * done)
            if t["km"] < 0.2:
                continue
        out.append(t)
    return out


# ---- driving one trip, second by second ------------------------------------

def speed_trace(r, kind, km):
    """A one-second speed trace covering `km`, shaped like that kind of road.

    Built out of accelerate / cruise / brake / wait cycles rather than from a
    smooth curve, because the whole point of the fortnight of samples is that
    the engine load moves the way it does in traffic.
    """
    if kind in ("highway", "long"):
        cruise = (95.0, 118.0)
        cycle_s = (240, 900)
        stop_p = 0.05
    elif kind == "commute":
        cruise = (48.0, 92.0)
        cycle_s = (35, 150)
        stop_p = 0.45
    else:
        cruise = (32.0, 58.0)
        cycle_s = (18, 70)
        stop_p = 0.65

    speeds = []
    metres = 0.0
    target_m = km * 1000.0
    v = 0.0
    guard = 0
    while metres < target_m and guard < 60000:
        guard += 1
        want = r.uniform(*cruise)
        accel = r.uniform(1.0, 2.1)          # m/s²
        while v < want and metres < target_m:
            v = min(want, v + accel * 3.6)
            speeds.append(v)
            metres += v / 3.6
        for _ in range(r.randint(*cycle_s)):
            if metres >= target_m:
                break
            v = max(5.0, v + r.gauss(0, 1.6))
            speeds.append(v)
            metres += v / 3.6
        if metres >= target_m:
            break
        if r.random() < stop_p:
            decel = r.uniform(1.4, 3.0)
            while v > 0.5:
                v = max(0.0, v - decel * 3.6)
                speeds.append(v)
                metres += v / 3.6
            v = 0.0
            for _ in range(r.randint(4, 40)):
                speeds.append(0.0)
    # Come to a stop at the end rather than freezing mid-cruise.
    while v > 0.5:
        v = max(0.0, v - 2.2 * 3.6)
        speeds.append(v)
    speeds.append(0.0)

    # The cycles overshoot — the last one runs to its own end rather than to
    # the odometer's. Scale the whole trace back so the distance the samples
    # integrate to is the distance the rollup already claimed; otherwise the
    # sparkline and the odometer quietly disagree by a tenth.
    actual = sum(speeds) / 3.6
    if actual > 0:
        k = target_m / actual
        speeds = [v * k for v in speeds]
    return speeds


def cvt_ratio(kph, accel_kph_s):
    """Road speed per 1000 rpm at this instant. No steps, by construction.

    A CVT does not choose a gear, it chooses an engine speed and moves the
    ratio to hold it. At light throttle that is the tallest ratio it can pull
    while keeping the revs somewhere useful; asked to accelerate it shortens
    continuously and the engine flares ahead of the road speed, which is the
    "rubber band" the transmission is famous for.

    Deceleration is deliberately NOT modelled as a shortening: a CVT does hold
    revs on a trailing throttle, but tying that to negative acceleration here
    would make the engine scream every time the car slows, which is not what
    the real record shows.
    """
    if kph <= 0:
        return CVT_SHORT
    want = kph / (CVT_TARGET_RPM / 1000.0)
    if accel_kph_s > 0:
        want /= 1.0 + min(2.2, accel_kph_s * 0.55)
    return max(CVT_SHORT, min(CVT_TALL, want))


def sample_from(v_kph, prev_kph, coolant, ambient, r):
    """One second of the engine, given what the wheels are doing."""
    moving = v_kph > MOVING_KPH
    if moving:
        ratio = cvt_ratio(v_kph, v_kph - prev_kph)
        rpm = max(IDLE_RPM, v_kph / ratio * 1000)
    else:
        rpm = IDLE_RPM + r.gauss(0, 25)

    v = v_kph / 3.6
    a = (v_kph - prev_kph) / 3.6
    force = (VEHICLE["mass_kg"] * a
             + 0.5 * AIR * CDA * v * v
             + VEHICLE["mass_kg"] * 9.81 * CRR * (1 if moving else 0))
    power_w = max(0.0, force * v)
    # Power the engine can make at this speed — a torque curve flat enough to
    # be honest about a small four.
    avail = VEHICLE["power_kw"] * 1000 * min(1.0, 0.35 + 0.65 * rpm / 5800.0)
    load = 100.0 * power_w / avail if avail else 0.0
    load = max(3.0 if not moving else 14.0, min(96.0, load + r.gauss(0, 2.5)))
    throttle = max(11.0, min(92.0, load * 0.72 + 12.0 + r.gauss(0, 2.0)))

    # Volumetric efficiency rises with load; MAF follows from displacement.
    ve = 0.32 + 0.60 * (load / 100.0)
    maf = rpm / 120.0 * VEHICLE["displacement_l"] * AIR * ve
    # Deceleration fuel cut — a real ECU shuts the injectors off on overrun,
    # and leaving it out makes coasting look like it costs money.
    if a < -0.6 and rpm > 1400:
        maf *= 0.12

    return rpm, load, throttle, maf


def warm(coolant, ambient, load, seconds=1.0, bias=0.0):
    """Coolant creeping up to temperature, then the thermostat holding it.

    `bias` moves the target — which is what a cooling fan does. Commanding the
    fan on and watching the temperature actually fall is the difference between
    testing a fan and looking at a fan.
    """
    target = 88.0 + load * 0.10 + bias
    tau = 210.0 if coolant < 80 else 90.0
    return max(ambient, coolant + (target - coolant) * (seconds / tau))


# ---- faults and the service book -------------------------------------------

def faults_for(today):
    """The stored and pending trouble codes, with plain-English text.

    A real CR-Z at 138,000 km: an IMA pack that is starting to go, an oxygen
    sensor heater that only misbehaves on a cold morning, and two codes that
    were fixed and cleared but are still in the history.
    """
    def ts(y, m, d, hh=8):
        return datetime(y, m, d, hh).timestamp()

    return [
        {"code": "P1449", "system": "IMA",
         "desc": "Battery Module Deterioration",
         "detail": "One or more IMA cells falling behind the pack. Assist and "
                   "regen are being limited to protect it.",
         "first_seen": ts(2026, 6, 2), "last_seen": ts(2026, 8, 24, 18),
         "count": 11, "status": "stored", "severity": "warning",
         "freeze": json.dumps({"rpm": 2140, "speed": 54, "coolant": 89,
                               "load": 31, "soc": 22})},
        {"code": "P0135", "system": "Fuel & air",
         "desc": "O2 Sensor Heater Circuit (Bank 1, Sensor 1)",
         "detail": "Only ever on a cold start below about 40 °F. Heater element "
                   "resistance is drifting — the sensor is on its way out.",
         "first_seen": ts(2026, 8, 14, 7), "last_seen": ts(2026, 8, 25, 7),
         "count": 6, "status": "pending", "severity": "warning",
         "freeze": json.dumps({"rpm": 1180, "speed": 0, "coolant": 34,
                               "load": 22, "ltft": 8.4})},
        {"code": "P0420", "system": "Emissions",
         "desc": "Catalyst System Efficiency Below Threshold",
         "detail": "Cleared after the downstream sensor was replaced in March. "
                   "Has not come back.",
         "first_seen": ts(2026, 2, 19), "last_seen": ts(2026, 3, 11),
         "count": 4, "status": "cleared", "severity": "normal", "freeze": ""},
        {"code": "P0301", "system": "Ignition",
         "desc": "Cylinder 1 Misfire Detected",
         "detail": "Went away with a set of plugs at 60,090 miles.",
         "first_seen": ts(2025, 11, 3), "last_seen": ts(2025, 11, 9),
         "count": 2, "status": "cleared", "severity": "normal", "freeze": ""},
        {"code": "P0A7F", "system": "IMA",
         "desc": "Hybrid Battery Pack Deterioration",
         "detail": "The generic twin of P1449 — measured capacity is below "
                   "70% of a new pack.",
         "first_seen": ts(2026, 6, 2), "last_seen": ts(2026, 8, 24, 18),
         "count": 9, "status": "stored", "severity": "warning",
         "freeze": json.dumps({"rpm": 2140, "speed": 54, "coolant": 89,
                               "load": 31, "soc": 22})},
        {"code": "B1225", "system": "Body",
         "desc": "Blower Motor Circuit — Low Speed",
         "detail": "Speed 1 on the fan does nothing; 2 through 4 are fine. "
                   "Classic failed blower resistor.",
         "first_seen": ts(2026, 7, 19), "last_seen": ts(2026, 8, 22),
         "count": 14, "status": "stored", "severity": "normal", "freeze": ""},
        {"code": "C1B00", "system": "Chassis",
         "desc": "Deflation Warning System Needs Initialisation",
         "detail": "Set after the tyres were rotated. Not a fault — the "
                   "system just needs its calibration button held.",
         "first_seen": ts(2026, 8, 12), "last_seen": ts(2026, 8, 25, 9),
         "count": 22, "status": "stored", "severity": "normal", "freeze": ""},
    ]


def service_for(today):
    """The maintenance book: what was done, when, and how often it is due.

    Honda's Maintenance Minder is a countdown rather than a mileage, so the
    percentage is what the car itself would show you — service soon at 15%,
    service now at 5%.
    """
    def ts(y, m, d):
        return datetime(y, m, d, 10).timestamp()

    return [
        {"item": "Engine oil & filter", "code": "A", "last_km": 129540.0,
         "last_at": ts(2025, 12, 14), "interval_km": 9600.0,
         "interval_days": 365, "note": "0W-20 full synthetic, 4.0 qt"},
        {"item": "Tire rotation", "code": "1", "last_km": 129540.0,
         "last_at": ts(2025, 12, 14), "interval_km": 12000.0,
         "interval_days": 365, "note": "front to rear, torque to 80 lb-ft"},
        {"item": "Air cleaner & pollen filter", "code": "2", "last_km": 118400.0,
         "last_at": ts(2025, 4, 8), "interval_km": 30000.0,
         "interval_days": 730, "note": "plus drive belt inspection"},
        {"item": "Brake fluid", "code": "7", "last_km": 118400.0,
         "last_at": ts(2025, 4, 8), "interval_km": 0.0,
         "interval_days": 1095, "note": "DOT 3, every 3 years regardless of mileage"},
        {"item": "Transmission fluid", "code": "3", "last_km": 96700.0,
         "last_at": ts(2023, 6, 17), "interval_km": 60000.0,
         "interval_days": 1825, "note": "Honda MTF-3, 1.6 qt"},
        {"item": "Spark plugs", "code": "4", "last_km": 96700.0,
         "last_at": ts(2023, 6, 17), "interval_km": 160000.0,
         "interval_days": 3650, "note": "NGK ILZKR7B-11S, and valve clearance"},
        {"item": "Engine coolant", "code": "5", "last_km": 84000.0,
         "last_at": ts(2022, 8, 11), "interval_km": 0.0,
         "interval_days": 1825, "note": "Honda Type 2, then every 5 years"},
        {"item": "IMA battery inspection", "code": "", "last_km": 131200.0,
         "last_at": ts(2026, 2, 15), "interval_km": 40000.0,
         "interval_days": 730, "note": "capacity test — P1449 is standing"},
        {"item": "Front brake pads", "code": "", "last_km": 121300.0,
         "last_at": ts(2025, 6, 20), "interval_km": 45000.0,
         "interval_days": 1460, "note": "4.5 mm left at the June inspection"},
        {"item": "12 V battery", "code": "", "last_km": 112800.0,
         "last_at": ts(2024, 9, 30), "interval_km": 0.0,
         "interval_days": 1825, "note": "group 51R — cranking is still strong"},
        {"item": "Tires", "code": "", "last_km": 121300.0,
         "last_at": ts(2025, 6, 20), "interval_km": 60000.0,
         "interval_days": 2190, "note": "195/55R16, DOT 2521 — age out before tread"},
    ]


# ---- the other control units ------------------------------------------------
#
# Generic OBD-II reaches exactly one module: the powertrain. Everything else in
# a modern car — the brakes, the airbags, the body, the hybrid pack — speaks a
# manufacturer protocol on the same bus and answers to nobody who has not paid
# for the licence. That is the whole reason a Snap-on tablet costs what it does.
#
# So the simulator presents the full module list, and each entry carries
# `generic: True` when a plain adapter could really have read it. The scan
# report shows the difference rather than quietly implying we read them all.

MODULES = [
    {"id": "PGM-FI", "name": "Engine (PGM-FI)", "addr": "0x7E0",
     "system": "Powertrain", "generic": True, "codes": ["P0135"],
     "part": "37820-RTW-A57", "sw": "RTW-A57 v3.11"},
    {"id": "IMA", "name": "IMA Motor & Battery", "addr": "0x7E2",
     "system": "Hybrid", "generic": False, "codes": ["P1449", "P0A7F"],
     "part": "1K000-RTW-A03", "sw": "RTW-A03 v2.04"},
    {"id": "VSA", "name": "VSA / ABS Modulator", "addr": "0x28",
     "system": "Chassis", "generic": False, "codes": [],
     "part": "57110-SZT-A03", "sw": "SZT-A03 v1.18"},
    {"id": "SRS", "name": "SRS Airbag Unit", "addr": "0x18",
     "system": "Restraints", "generic": False, "codes": [],
     "part": "77960-SZT-A02", "sw": "SZT-A02 v1.06"},
    {"id": "EPS", "name": "Electric Power Steering", "addr": "0x24",
     "system": "Chassis", "generic": False, "codes": [],
     "part": "39980-SZT-A01", "sw": "SZT-A01 v1.09"},
    {"id": "MICU", "name": "Body Control (MICU)", "addr": "0x40",
     "system": "Body", "generic": False, "codes": ["B1225"],
     "part": "38809-SZT-A02", "sw": "SZT-A02 v2.21"},
    {"id": "HVAC", "name": "Climate Control", "addr": "0x50",
     "system": "Body", "generic": False, "codes": [],
     "part": "79600-SZT-A41", "sw": "SZT-A41 v1.03"},
    {"id": "TPMS", "name": "Deflation Warning", "addr": "0x54",
     "system": "Chassis", "generic": False, "codes": ["C1B00"],
     "part": "39350-SZT-A01", "sw": "SZT-A01 v1.02"},
    {"id": "IMOES", "name": "Immobiliser / Keyless", "addr": "0x60",
     "system": "Security", "generic": False, "codes": [],
     "part": "39730-SZT-A01", "sw": "SZT-A01 v1.11"},
    {"id": "GAUGE", "name": "Gauge Control Module", "addr": "0x64",
     "system": "Body", "generic": False, "codes": [],
     "part": "78100-SZT-A11", "sw": "SZT-A11 v1.07"},
]


# I/M readiness. Two monitors are deliberately incomplete, and for a reason the
# app can explain rather than just report: the pending O2 heater fault keeps
# aborting the heater monitor, and the catalyst monitor runs downstream of it.
# "You cannot pass an emissions test, and here is the one fault to fix" is the
# single most useful sentence a scan tool can say to somebody.
READINESS = [
    {"id": "misfire", "name": "Misfire", "kind": "continuous",
     "supported": True, "complete": True},
    {"id": "fuel", "name": "Fuel System", "kind": "continuous",
     "supported": True, "complete": True},
    {"id": "components", "name": "Comprehensive Components", "kind": "continuous",
     "supported": True, "complete": True},
    {"id": "catalyst", "name": "Catalyst", "kind": "trip",
     "supported": True, "complete": False,
     "why": "Runs downstream of the oxygen sensor monitor, which has not "
            "completed. Fix P0135 first."},
    {"id": "heated_catalyst", "name": "Heated Catalyst", "kind": "trip",
     "supported": False, "complete": False},
    {"id": "evap", "name": "Evaporative System", "kind": "trip",
     "supported": True, "complete": True},
    {"id": "secondary_air", "name": "Secondary Air System", "kind": "trip",
     "supported": False, "complete": False},
    {"id": "ac_refrigerant", "name": "A/C Refrigerant", "kind": "trip",
     "supported": False, "complete": False},
    {"id": "o2_sensor", "name": "Oxygen Sensor", "kind": "trip",
     "supported": True, "complete": True},
    {"id": "o2_heater", "name": "Oxygen Sensor Heater", "kind": "trip",
     "supported": True, "complete": False,
     "why": "Aborts on every cold start because the heater circuit fault "
            "(P0135) sets before the monitor can finish."},
    {"id": "egr", "name": "EGR System", "kind": "trip",
     "supported": True, "complete": True},
]


# Mode 06 — the on-board monitor test results. Every OBD-II car has had this
# since 1996 and almost nobody looks at it, which is a shame, because it is the
# only place the ECU shows its working: the actual measured value of each self
# test next to the limit it was judged against. A catalyst at 94% of its limit
# has passed and is also about to fail, and this is the one screen that says so.
MODE06 = [
    {"mid": "0x01", "name": "Misfire, cylinder 1", "component": "Cylinder 1",
     "value": 3.0, "min": None, "max": 40.0, "unit": "counts",
     "note": "Exponentially weighted misfire count over the last 1000 revolutions."},
    {"mid": "0x02", "name": "Misfire, cylinder 2", "component": "Cylinder 2",
     "value": 0.0, "min": None, "max": 40.0, "unit": "counts"},
    {"mid": "0x03", "name": "Misfire, cylinder 3", "component": "Cylinder 3",
     "value": 0.0, "min": None, "max": 40.0, "unit": "counts"},
    {"mid": "0x04", "name": "Misfire, cylinder 4", "component": "Cylinder 4",
     "value": 1.0, "min": None, "max": 40.0, "unit": "counts"},
    {"mid": "0x21", "name": "Catalyst switch ratio", "component": "Catalyst B1",
     "value": 0.712, "min": None, "max": 0.750, "unit": "ratio",
     "note": "Rear sensor switches divided by front. Rises as the catalyst "
             "loses its oxygen storage; at the limit it sets P0420."},
    {"mid": "0x39", "name": "O2 heater resistance, B1S1",
     "component": "Front O2 heater", "value": 8.9, "min": 1.0, "max": 10.0,
     "unit": "ohm",
     "note": "Drifting toward the top of the window — the element is on its "
             "way out. This is the measurement behind P0135."},
    {"mid": "0x3D", "name": "O2 heater resistance, B1S2",
     "component": "Rear O2 heater", "value": 4.1, "min": 1.0, "max": 10.0,
     "unit": "ohm"},
    {"mid": "0x41", "name": "EVAP 0.040\" leak check", "component": "EVAP system",
     "value": 0.021, "min": None, "max": 0.040, "unit": "in",
     "note": "Equivalent orifice size found during the leak test."},
    {"mid": "0x42", "name": "EVAP purge flow", "component": "Purge valve",
     "value": 0.83, "min": 0.30, "max": None, "unit": "ratio"},
    {"mid": "0x5B", "name": "IMA pack capacity", "component": "IMA battery",
     "value": 0.61, "min": 0.70, "max": None, "unit": "ratio",
     "note": "Measured usable capacity against a new pack. Below 0.70 the "
             "motor control unit limits assist — this is what sets P1449."},
    {"mid": "0x5C", "name": "IMA cell block spread", "component": "IMA battery",
     "value": 0.41, "min": None, "max": 0.30, "unit": "V",
     "note": "Voltage spread between the weakest and strongest cell blocks "
             "at the end of a discharge. Past the limit."},
]


# ---- writing it all down ----------------------------------------------------

def open_db():
    os.makedirs(os.path.dirname(records.DB), exist_ok=True)
    db = sqlite3.connect(records.DB)
    db.execute("""CREATE TABLE IF NOT EXISTS samples (
        t REAL PRIMARY KEY, rpm REAL, speed REAL, load REAL, throttle REAL,
        coolant REAL, intake REAL, maf REAL, stft REAL, ltft REAL,
        timing REAL, lphk REAL, eff REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS samples_t ON samples(t)")
    db.execute("""CREATE TABLE IF NOT EXISTS days (
        day TEXT PRIMARY KEY, km REAL, litres REAL, lphk REAL,
        moving_s INTEGER, engine_s INTEGER, idle_s INTEGER,
        top_kph REAL, trips INTEGER, cost REAL, odo REAL,
        ltft_mean REAL, coolant_max REAL, rpm_max REAL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS trips (
        t0 REAL PRIMARY KEY, t1 REAL, km REAL, litres REAL, lphk REAL,
        moving_s INTEGER, idle_s INTEGER, top_kph REAL,
        kind TEXT, label TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS faults (
        code TEXT PRIMARY KEY, system TEXT, descr TEXT, detail TEXT,
        first_seen REAL, last_seen REAL, count INTEGER,
        status TEXT, severity TEXT, freeze TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS service (
        item TEXT PRIMARY KEY, code TEXT, last_km REAL, last_at REAL,
        interval_km REAL, interval_days REAL, note TEXT)""")
    db.execute("CREATE TABLE IF NOT EXISTS vehicle (k TEXT PRIMARY KEY, v TEXT)")
    records.migrate_days(db)
    db.execute("""CREATE TABLE IF NOT EXISTS modules (
        id TEXT PRIMARY KEY, name TEXT, addr TEXT, system TEXT,
        generic INTEGER, part TEXT, sw TEXT, codes TEXT, pos INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS readiness (
        id TEXT PRIMARY KEY, name TEXT, kind TEXT, supported INTEGER,
        complete INTEGER, why TEXT, pos INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mode06 (
        mid TEXT PRIMARY KEY, name TEXT, component TEXT, value REAL,
        lo REAL, hi REAL, unit TEXT, note TEXT, pos INTEGER)""")
    # Saved work: a full-system scan, or a stretch of recorded samples. Both
    # are just a stamp and a span — the samples themselves stay where they are.
    db.execute("""CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, at REAL,
        odo REAL, label TEXT, t0 REAL, t1 REAL, payload TEXT)""")
    db.commit()
    return db


def seed(verbose=True):
    # The simulated car has its own record. It used to share `telemetry.db`
    # with whatever real vehicle had been plugged in, which is how a driver
    # ends up looking at a fictional car's trouble codes.
    records.use(garage.SIM_KEY)
    today = date.today()
    trips = year_of_trips(today)
    db = open_db()

    # Odometer runs backwards from today's figure, so every service interval
    # below lands where the book says it should.
    total_km = sum(t["km"] for t in trips)
    odo = ODO_NOW - total_km

    days = {}
    for t in trips:
        key = datetime.fromtimestamp(t["t0"]).strftime("%Y-%m-%d")
        d = days.setdefault(key, {"km": 0.0, "litres": 0.0, "moving_s": 0,
                                  "idle_s": 0, "top_kph": 0.0, "trips": 0})
        d["km"] += t["km"]
        d["litres"] += t["litres"]
        d["moving_s"] += t["moving_s"]
        d["idle_s"] += t["idle_s"]
        d["top_kph"] = max(d["top_kph"], t["top_kph"])
        d["trips"] += 1

    db.execute("DELETE FROM days")
    db.execute("DELETE FROM trips")
    for i in range(DAYS_OF_HISTORY - 1, -1, -1):
        key = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        d = days.get(key)
        km = d["km"] if d else 0.0
        odo += km
        litres = d["litres"] if d else 0.0
        # A year of health figures, not just driving. The fuel trim creeps up
        # as the front oxygen sensor ages — which is a real thing that happens
        # to a real car, is the cause behind two of the codes this car is
        # holding, and is exactly the kind of slow drift a trend is for.
        age = (DAYS_OF_HISTORY - i) / DAYS_OF_HISTORY
        ltft = round(2.9 + 5.1 * age ** 1.6 + rng_for(
            today - timedelta(days=i)).gauss(0, 0.35), 2) if km > 0.5 else None
        coolant_max = round(88 + 6 * season_factor(today - timedelta(days=i))
                            + rng_for(today - timedelta(days=i)).uniform(-2, 4), 1) \
            if km > 0.5 else None
        rpm_max = round(3200 + rng_for(today - timedelta(days=i)).uniform(0, 2600)) \
            if km > 0.5 else None
        db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (key, round(km, 2), round(litres, 3),
                    round(litres / km * 100.0, 2) if km > 0.5 else None,
                    d["moving_s"] if d else 0,
                    (d["moving_s"] + d["idle_s"]) if d else 0,
                    d["idle_s"] if d else 0,
                    round(d["top_kph"], 1) if d else 0.0,
                    d["trips"] if d else 0,
                    round(litres * FUEL_PRICE, 2), round(odo, 1),
                    ltft, coolant_max, rpm_max))

    for t in trips:
        db.execute("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (t["t0"], t["t1"], round(t["km"], 2), round(t["litres"], 3),
                    round(t["lphk"], 2), t["moving_s"], t["idle_s"],
                    round(t["top_kph"], 1), t["kind"], t["label"]))

    db.execute("DELETE FROM faults")
    for f in faults_for(today):
        db.execute("INSERT OR REPLACE INTO faults VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (f["code"], f["system"], f["desc"], f["detail"],
                    f["first_seen"], f["last_seen"], f["count"],
                    f["status"], f["severity"], f["freeze"]))

    db.execute("DELETE FROM service")
    for s in service_for(today):
        db.execute("INSERT OR REPLACE INTO service VALUES (?,?,?,?,?,?,?)",
                   (s["item"], s["code"], s["last_km"], s["last_at"],
                    s["interval_km"], s["interval_days"], s["note"]))

    db.execute("DELETE FROM modules")
    for i, m in enumerate(MODULES):
        db.execute("INSERT OR REPLACE INTO modules VALUES (?,?,?,?,?,?,?,?,?)",
                   (m["id"], m["name"], m["addr"], m["system"],
                    1 if m["generic"] else 0, m["part"], m["sw"],
                    json.dumps(m["codes"]), i))

    db.execute("DELETE FROM readiness")
    for i, r in enumerate(READINESS):
        db.execute("INSERT OR REPLACE INTO readiness VALUES (?,?,?,?,?,?,?)",
                   (r["id"], r["name"], r["kind"],
                    1 if r["supported"] else 0, 1 if r["complete"] else 0,
                    r.get("why", ""), i))

    db.execute("DELETE FROM mode06")
    for i, m in enumerate(MODE06):
        db.execute("INSERT OR REPLACE INTO mode06 VALUES (?,?,?,?,?,?,?,?,?)",
                   (m["mid"], m["name"], m["component"], m["value"],
                    m.get("min"), m.get("max"), m["unit"], m.get("note", ""), i))

    meta = dict(VEHICLE)
    meta["odometer_km"] = round(ODO_NOW, 1)
    meta["fuel_price"] = FUEL_PRICE
    meta["seeded_at"] = int(time.time())
    for k, v in meta.items():
        db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
                   (k, json.dumps(v)))
    db.commit()

    # The fortnight at 1 Hz. Only the trips inside the window, driven properly.
    cutoff = time.time() - DAYS_OF_SAMPLES * 86400
    db.execute("DELETE FROM samples WHERE t >= ?", (cutoff,))
    rows = 0
    for t in trips:
        if t["t1"] < cutoff:
            continue
        rows += write_trip_samples(db, t)
    db.commit()
    db.close()

    if verbose:
        print(f"seeded  {len(trips)} trips over {DAYS_OF_HISTORY} days  "
              f"({total_km:,.0f} km, odometer {ODO_NOW:,.0f} km)")
        print(f"        {rows:,} one-second samples over the last "
              f"{DAYS_OF_SAMPLES} days")
        print(f"        {len(faults_for(today))} trouble codes across "
              f"{len(MODULES)} modules, {len(service_for(today))} service records")
        ready = sum(1 for r in READINESS if r["supported"] and not r["complete"])
        print(f"        {len(MODE06)} on-board test results, "
              f"{ready} readiness monitor(s) incomplete")
    return 0


def write_trip_samples(db, trip):
    """Drive one trip second by second and store it.

    The trace is generated freely and then the fuel is scaled to the figure
    the rollup already published, so the fortnight of samples and the year of
    rollups are guaranteed to tell the same story.
    """
    r = random.Random(int(trip["t0"]))
    speeds = speed_trace(r, trip["kind"], trip["km"])
    ambient = 9.0 + 12.0 * math.sin(
        (datetime.fromtimestamp(trip["t0"]).timetuple().tm_yday - 100)
        / 365.0 * 2 * math.pi) + r.uniform(-3, 3)

    coolant = ambient + r.uniform(0, 6)
    prev = 0.0
    raw = []
    for v in speeds:
        rpm, load, throttle, maf = sample_from(v, prev, coolant, ambient, r)
        coolant = warm(coolant, ambient, load)
        raw.append((v, rpm, load, throttle, maf, coolant))
        prev = v

    # Scale so the trip burns exactly what the rollup says it burned.
    litres_raw = sum(x[4] for x in raw) / AFR / FUEL_DENSITY_G_PER_L
    scale = (trip["litres"] / litres_raw) if litres_raw > 0 else 1.0

    t = trip["t0"]
    ltft = r.uniform(5.5, 9.0)          # the standing lean trim, see faults
    n = 0
    for (v, rpm, load, throttle, maf, coolant) in raw:
        maf *= scale
        lphk = None
        if v > MOVING_KPH and maf > 0:
            lphk = maf / AFR * 3600.0 / FUEL_DENSITY_G_PER_L / v * 100.0
        eff = efficiency(lphk, v, load, throttle)
        db.execute("INSERT OR REPLACE INTO samples "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (t, round(rpm), round(v, 1), round(load, 1),
                    round(throttle, 1), round(coolant, 1),
                    round(ambient + 6 + load * 0.08, 1), round(maf, 2),
                    round(r.gauss(0, 3.0), 1), round(ltft, 1),
                    round(14.0 + r.gauss(0, 3), 1),
                    round(lphk, 2) if lphk else None, round(eff, 3)))
        t += 1.0
        n += 1
    return n


def efficiency(lphk, speed, load, throttle):
    """The 0..1 the ambient ring reads, on the same band OmaCar uses."""
    if lphk is not None:
        return max(0.0, min(1.0, 1.0 - (lphk - 4.5) / 5.0))
    if speed < MOVING_KPH:
        return 0.25
    return max(0.0, min(1.0, 1.0 - (load + throttle) / 200.0))


# ---- bidirectional: actuator tests ------------------------------------------
#
# The feature that separates a scan tool from a reader is being able to command
# the car rather than only ask it. A fan you can switch on is a fan you can
# prove, and a cylinder you can silence is a compression test you did not have
# to do.
#
# Generic OBD-II barely has this: Mode 08 exists in the standard and almost
# nobody implements it, so on a real car every test below needs the
# manufacturer's own protocol. The catalogue is honest about that per test; the
# simulator implements them properly, because a simulated car that cannot be
# poked is not a rehearsal for anything.
#
# Each effect is a modifier applied to the sample the loop was going to publish
# anyway, so a test changes what the engine does rather than what the display
# says — everything downstream, including the graph and the advisor, sees the
# real consequence.

# Cylinder 1 is deliberately the weak one. The car's own history has a P0301
# that was "fixed" with a set of plugs, and a balance test that quietly agrees
# with the code history is the whole point of running one.
CYLINDER_STRENGTH = {1: 0.62, 2: 1.0, 3: 0.97, 4: 0.93}

# Nominal idle drop when a healthy cylinder stops firing, in rpm.
BALANCE_DROP = 195.0


def read_command():
    try:
        with open(COMMAND, encoding="utf-8") as f:
            cmd = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cmd, dict) or not cmd.get("test"):
        return None
    if time.time() > cmd.get("at", 0) + cmd.get("duration", 0):
        return None
    return cmd


def apply_actuator(cmd, values, coolant_target, r):
    """Bend the sample the way the commanded actuator would.

    Returns (values, coolant_bias, note). Nothing here fakes a reading: the
    modifiers are applied to the engine model and the published sample follows.
    """
    test = cmd["test"]
    bias = 0.0

    if test == "hold_idle":
        # Nothing is actuated; the loop's `holding` flag has already parked the
        # car and left the engine running, which is the whole effect.
        pass
    elif test == "fan_high":
        bias = -14.0
    elif test == "fan_low":
        bias = -7.0
    elif test == "ac_clutch":
        values["RPM"] = values["RPM"] + 90
        values["ENGINE_LOAD"] = min(96.0, values["ENGINE_LOAD"] + 9)
        bias = +4.0
    elif test == "evap_purge":
        # Purging draws stored vapour into the intake: the mixture goes rich,
        # short trim pulls fuel out to compensate, and the idle wobbles.
        values["SHORT_FUEL_TRIM_1"] = round(values["SHORT_FUEL_TRIM_1"] - 11.0
                                            + r.gauss(0, 1.5), 1)
        values["RPM"] = values["RPM"] - 45 + r.gauss(0, 12)
    elif test == "egr_open":
        # Inert exhaust displacing intake charge. A blocked passage would show
        # nothing at all, which is exactly how the test finds one.
        values["RPM"] = values["RPM"] - 135 + r.gauss(0, 15)
        values["ENGINE_LOAD"] = min(96.0, values["ENGINE_LOAD"] + 6)
        values["SHORT_FUEL_TRIM_1"] = round(values["SHORT_FUEL_TRIM_1"] + 4.5, 1)
    elif test == "fuel_pump":
        # Priming with the engine off. Nothing on the bus changes; the proof is
        # audible, which the catalogue says out loud.
        pass
    elif test.startswith("injector_kill_"):
        try:
            cyl = int(test.rsplit("_", 1)[1])
        except ValueError:
            cyl = 1
        strength = CYLINDER_STRENGTH.get(cyl, 1.0)
        values["RPM"] = max(430.0, values["RPM"] - BALANCE_DROP * strength
                            + r.gauss(0, 8))
        values["ENGINE_LOAD"] = min(96.0, values["ENGINE_LOAD"] + 14)
        # The ECU notices, and says so the way it would on the bus.
        values["SHORT_FUEL_TRIM_1"] = round(values["SHORT_FUEL_TRIM_1"] + 6.0, 1)

    return values, bias


# ---- the live loop ----------------------------------------------------------

def current_trip(now):
    """The trip in progress right now, if the schedule has one."""
    d = datetime.fromtimestamp(now).date()
    for day in (d - timedelta(days=1), d):
        r = rng_for(day)
        for (hour, kind, km) in day_plan(day):
            lphk = trip_economy(day, kind, km, r)
            start = (datetime.combine(day, datetime.min.time())
                     + timedelta(hours=hour)).timestamp()
            avg = {"commute": 38.0, "errand": 27.0,
                   "highway": 88.0, "long": 92.0}[kind]
            dur = km / avg * 3600 * 1.3
            if start <= now <= start + dur:
                return {"t0": start, "km": km, "kind": kind, "lphk": lphk,
                        "litres": km * lphk / 100.0}
    return None


def shakedown(now):
    """A short drive so the cluster has something to show on a quiet evening.

    The schedule is honest — this car is parked most of the day, like every
    car. But a dock card you have just installed showing nothing at all is
    indistinguishable from one that is broken, so starting the simulator when
    nothing is scheduled takes it round the block.
    """
    return {"t0": now, "km": 11.0, "kind": "errand", "lphk": 7.2,
            "litres": 11.0 * 7.2 / 100.0, "shakedown": True}


def publish(payload):
    # ALWAYS THE SIMULATED CAR, never garage.current(). What this process
    # publishes is invented, whatever the garage happens to be pointing at, so
    # the stamp is a statement about the sample and not about the pointer --
    # which is exactly what makes it useful to a reader deciding whether the
    # sample is their car's. See records.live().
    os.makedirs(STATE, exist_ok=True)
    payload = dict(payload, vehicle=garage.SIM_KEY)
    tmp = LIVE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, LIVE)


def run():
    """Publish live.json at 5 Hz and a sample a second, exactly as the daemon does."""
    records.use(garage.SIM_KEY)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    db = open_db()
    supported = ["RPM", "SPEED", "ENGINE_LOAD", "THROTTLE_POS", "MAF",
                 "COOLANT_TEMP", "INTAKE_TEMP", "FUEL_LEVEL", "RUN_TIME",
                 "CONTROL_MODULE_VOLTAGE", "SHORT_FUEL_TRIM_1",
                 "LONG_FUEL_TRIM_1", "TIMING_ADVANCE", "AMBIANT_AIR_TEMP"]

    started = time.time()
    trip = current_trip(started) or shakedown(started)
    r = random.Random(int(started))
    speeds = speed_trace(r, trip["kind"], trip["km"])
    idx = 0

    ambient = 9.0 + 12.0 * math.sin(
        (datetime.now().timetuple().tm_yday - 100) / 365.0 * 2 * math.pi)
    coolant = ambient + 2.0
    fuel_pct = 63.0
    prev = 0.0
    last_row = 0.0
    ltft = 7.8
    odo = ODO_NOW
    actuator_bias = 0.0

    try:
        while True:
            now = time.time()
            cmd = read_command()
            # A functional test needs the engine running and the car still —
            # which is a technician with it idling in the bay. When a test asks
            # for that, the simulator holds idle for as long as the test lasts
            # rather than pretending the engine is off.
            holding = bool(cmd and cmd.get("idle"))

            if holding:
                v = 0.0
            elif idx < len(speeds):
                v = speeds[idx]
                idx += 1
            else:
                # Trip over — park until the schedule starts another one.
                nxt = current_trip(now)
                if nxt and nxt.get("t0") != trip.get("t0"):
                    trip = nxt
                    r = random.Random(int(now))
                    speeds = speed_trace(r, trip["kind"], trip["km"])
                    idx = 0
                    continue
                v = 0.0

            parked = (not holding) and idx >= len(speeds) and v <= 0.5
            rpm, load, throttle, maf = sample_from(v, prev, coolant, ambient, r)
            if parked:
                rpm, load, throttle, maf = 0.0, 0.0, 0.0, 0.0
            else:
                coolant = warm(coolant, ambient, load, seconds=1.0,
                               bias=actuator_bias)
                # An engine held at idle for a test warms toward temperature
                # like any idling engine.
                if holding and coolant < 88:
                    coolant = warm(coolant, ambient, 18.0, seconds=1.0)
            prev = v

            lphk = None
            if v > MOVING_KPH and maf > 0:
                lphk = maf / AFR * 3600.0 / FUEL_DENSITY_G_PER_L / v * 100.0
            lph = maf / AFR * 3600.0 / FUEL_DENSITY_G_PER_L if maf > 0 else None
            eff = efficiency(lphk, v, load, throttle)
            if lph:
                fuel_pct = max(4.0, fuel_pct - lph / 3600.0 / VEHICLE["tank_l"] * 100)
            odo += v / 3600.0

            values = {
                "RPM": round(rpm), "SPEED": round(v, 1),
                "ENGINE_LOAD": round(load, 1), "THROTTLE_POS": round(throttle, 1),
                "MAF": round(maf, 2), "COOLANT_TEMP": round(coolant, 1),
                "INTAKE_TEMP": round(ambient + 6 + load * 0.08, 1),
                "AMBIANT_AIR_TEMP": round(ambient, 1),
                "FUEL_LEVEL": round(fuel_pct, 1),
                "RUN_TIME": int(now - started),
                "CONTROL_MODULE_VOLTAGE": round(
                    (12.4 if parked else 14.25) + r.gauss(0, 0.06), 2),
                "SHORT_FUEL_TRIM_1": round(r.gauss(0, 3.0), 1),
                "LONG_FUEL_TRIM_1": round(ltft, 1),
                "TIMING_ADVANCE": round(14.0 + r.gauss(0, 3), 1),
            }

            actuator_bias = 0.0
            actuator = None
            if cmd:
                values, actuator_bias = apply_actuator(cmd, values, coolant, r)
                # Re-round what the modifiers touched, so the published sample
                # looks like every other sample rather than like a calculation.
                values["RPM"] = round(values["RPM"])
                values["ENGINE_LOAD"] = round(values["ENGINE_LOAD"], 1)
                actuator = {
                    "id": cmd.get("id"), "test": cmd["test"],
                    "started": cmd.get("at"),
                    "ends": cmd.get("at", 0) + cmd.get("duration", 0),
                    "idle": bool(cmd.get("idle")),
                }

            publish({
                "connected": True, "simulated": True,
                "actuator": actuator,
                "port": VEHICLE["port"], "kind": VEHICLE["adapter"],
                "t": now, "uptime": now - started,
                "protocol": VEHICLE["protocol"],
                "supported": supported, "values": values,
                "economy_lphk": lphk, "fuel_lph": lph,
                "efficiency": eff, "efficiency_basis": "economy" if lphk else
                               ("idle" if not parked else "off"),
                "odometer_km": round(odo, 1),
                "trip": {"kind": trip["kind"], "km": trip["km"],
                         "shakedown": bool(trip.get("shakedown"))},
            })

            if now - last_row >= 1.0 and not parked:
                db.execute("INSERT OR REPLACE INTO samples "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (now, values["RPM"], values["SPEED"],
                            values["ENGINE_LOAD"], values["THROTTLE_POS"],
                            values["COOLANT_TEMP"], values["INTAKE_TEMP"],
                            values["MAF"], values["SHORT_FUEL_TRIM_1"],
                            values["LONG_FUEL_TRIM_1"],
                            values["TIMING_ADVANCE"],
                            round(lphk, 2) if lphk else None, round(eff, 3)))
                db.commit()
                last_row = now

            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        publish({"connected": False, "simulated": True, "status": "stopped",
                 "port": VEHICLE["port"]})
        db.close()
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
    return 0


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if what == "seed":
        return seed()
    if what == "run":
        return run()
    print(f"sim: unknown command {what!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
