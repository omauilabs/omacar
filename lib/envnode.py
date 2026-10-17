#!/usr/bin/env python3
"""The weather in and around the car: a cabin node and an outdoor node.

Owner, 2026-10-17: "Internal cabin temperature, cabin air quality sensor,
outdoor temperature and outdoor air quality sensor with humidity, pressure,
basically a rolling weather/environment sensor." The hardware is not bought
yet (firmware/envnode/ is what to build). This is the half that runs on the
tablet and does not care which sensors are behind the microcontroller.

A node is a small board on USB that prints ONE JSON object per line, 1 Hz:

    {"node":"cabin","fw":1,"t_c":22.4,"rh":41.2,"co2":812,"pm1":3.1,
     "pm25":4.0,"pm10":5.2,"voc":102,"nox":1}
    {"node":"outdoor","fw":1,"t_c":17.9,"rh":63.0,"hpa":1012.6,"pm25":6.1}

A sensor that is not fitted, or not ready, is OMITTED, never zero.

    omacar env status    what env.json says now (with the derived numbers)
    omacar env scan      find the nodes on the USB ports, 3 s each
    omacar env run       hold them open and keep the record (omacar-env.service)
    omacar env sim       a simulated pair of nodes; says "Simulated", every time

THE OBD ADAPTER'S PORT IS NEVER OPENED. A port is probed only when ALL hold:
its USB vendor is on the node list (Raspberry Pi, Espressif, Adafruit,
Arduino, or one the owner put in env-nodes.json), it is not the port the
daemon resolved or published, no other process has it open, and its name is
not an OBD adapter's. The ELM327/OBDLink family (FTDI, CH340, CP210x) is not
on the list at all. This module only ever READS from a node: it never writes
a byte to one.

WHAT IS KEPT. env.json: the latest value of each metric and the last ten
minutes at 5 s (the card's trails). env-history.jsonl: one row per minute for
24 hours (the full view's charts). Values outside the physical range of the
quantity are dropped, not shown.
"""

import glob
import json
import math
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar"), "env-nodes.json")
STATE = os.path.join(STATE_DIR, "env.json")
HIST = os.path.join(STATE_DIR, "env-history.jsonl")
HIST_SIM = os.path.join(STATE_DIR, "env-history-sim.jsonl")

TRAIL_EVERY = 5           # s between trail points
TRAIL_KEEP = 10 * 60      # the card's trails: ten minutes
HIST_KEEP = 24 * 3600     # the full view: a day, a point a minute
LIVE_FOR = 10             # s: a node that spoke this recently is "live"
METRIC_FOR = 15           # s: a metric not repeated for this long is dropped from a live node
MOVING_KPH = 3.0          # the same line the rest of the app draws
CAR_FRESH = 20            # s: a car sample older than this says nothing
LINE_MAX = 512

# ---- what a node may say ------------------------------------------------------------
# (low, high, integer?) -- the physical range of the QUANTITY, not the sensor's
# datasheet: a reading outside it is a fault, and a fault is not shown.
RANGES = {
    "t_c": (-40.0, 85.0, False),
    "rh": (0.0, 100.0, False),
    "hpa": (300.0, 1100.0, False),
    "co2": (300, 10000, True),
    "pm1": (0.0, 1000.0, False),
    "pm25": (0.0, 1000.0, False),
    "pm4": (0.0, 1000.0, False),
    "pm10": (0.0, 1000.0, False),
    "voc": (0, 500, True),
    "nox": (0, 500, True),
}
NODES = {
    "cabin": ("t_c", "rh", "co2", "pm1", "pm25", "pm4", "pm10", "voc", "nox"),
    "outdoor": ("t_c", "rh", "hpa", "pm1", "pm25", "pm10"),
}
# The metrics that get a card trail and a 24 h chart.
TRACKED = {
    "cabin": ("t_c", "rh", "co2", "pm25", "voc"),
    "outdoor": ("t_c", "rh", "hpa", "pm25"),
}

# USB vendors whose boards are plausibly a node. A DENY-by-default list: the
# adapters (FTDI 0403, CH340 1a86, CP210x 10c4, PL2303 067b) are not here.
NODE_VENDORS = {
    "2e8a": "Raspberry Pi (RP2040)",
    "303a": "Espressif (ESP32-S3 native USB)",
    "239a": "Adafruit",
    "2341": "Arduino",
}
ADAPTER_NAME = re.compile(r"obdlink|elm3|stn\d|obd|ftdi|scantool", re.I)


def clean_value(key, v):
    """One value, or None: a real number inside the quantity's physical range."""
    spec = RANGES.get(key)
    if spec is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    lo, hi, integer = spec
    if v < lo or v > hi:
        return None
    return int(round(v)) if integer else round(float(v), 2)


def parse_line(line):
    """(node, fw, {metric: value}) from one line, or None when it is not a node's.

    A line that is not JSON, not an object, has no known "node", or carries no
    valid metric at all is not a reading. Unknown keys and bad values are
    dropped one by one; the rest of the line still counts.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line or len(line) > LINE_MAX or not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    node = obj.get("node")
    if node not in NODES:
        return None
    fw = obj.get("fw")
    fw = fw if isinstance(fw, int) and not isinstance(fw, bool) else None
    vals = {}
    for k in NODES[node]:
        if k in obj:
            v = clean_value(k, obj[k])
            if v is not None:
                vals[k] = v
    if not vals:
        return None
    return node, fw, vals


# ---- what the numbers mean ----------------------------------------------------------

def dew_point_c(t_c, rh):
    """Magnus formula, Sonntag 1990 constants (a=17.62, b=243.12 C), valid -45..60 C."""
    if t_c is None or rh is None or rh <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(rh / 100.0) + a * t_c / (b + t_c)
    return round(b * g / (a - g), 1)


def abs_humidity(t_c, rh):
    """g of water per m3 of air: 216.7 * e / T, e the actual vapour pressure in hPa."""
    if t_c is None or rh is None:
        return None
    es = 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))
    return round(216.7 * (rh / 100.0) * es / (273.15 + t_c), 1)


# US EPA Air Quality Index for PM2.5, the 2024 revision (final rule 2024-02-07,
# effective 2024-05-06; EPA fact sheet "2024 AQI for Fine Particle Pollution").
# (c_lo, c_hi, aqi_lo, aqi_hi, key, words). The top band's upper breakpoint
# (325.4 ug/m3 -> AQI 500) is 40 CFR 58 Appendix G's; the fact sheet gives
# only "225.5+" for Hazardous.
AQI_BANDS = (
    (0.0, 9.0, 0, 50, "good", "Good"),
    (9.1, 35.4, 51, 100, "moderate", "Moderate"),
    (35.5, 55.4, 101, 150, "usg", "Unhealthy for sensitive groups"),
    (55.5, 125.4, 151, 200, "unhealthy", "Unhealthy"),
    (125.5, 225.4, 201, 300, "very", "Very unhealthy"),
    (225.5, 325.4, 301, 500, "hazardous", "Hazardous"),
)


def aqi_pm25(ugm3):
    """{index, key, label} for a PM2.5 concentration (truncated to 0.1, as EPA does)."""
    if ugm3 is None:
        return None
    c = math.floor(max(0.0, ugm3) * 10) / 10.0
    for lo, hi, ilo, ihi, key, label in AQI_BANDS:
        if c <= hi or (key == "hazardous"):
            idx = ilo + (min(c, hi) - lo) * (ihi - ilo) / (hi - lo)
            return {"index": int(round(idx)), "key": key, "label": label}
    return None


def co2_band(ppm):
    """<800 good, 800-1200 ok, 1200-2000 stuffy, >2000 high."""
    if ppm is None:
        return None
    if ppm < 800:
        return {"key": "good", "label": "Fresh"}
    if ppm < 1200:
        return {"key": "ok", "label": "Fine"}
    if ppm <= 2000:
        return {"key": "stuffy", "label": "Stuffy"}
    return {"key": "high", "label": "High"}


TREND_WINDOW = 3 * 3600
TREND_RISE = 1.0     # hPa over 3 h: the WMO "steady" band is within +-1.6; 1 is a visible change


def pressure_trend(rows, now):
    """The 3 h pressure change in hPa, only while the car has been parked.

    Pressure tracks altitude (about 12 hPa per 100 m near sea level), so a
    drive over a hill is not weather. If the car moved anywhere in the window
    there is no trend, and the card says so. Rows are history rows:
    {t, v: {"outdoor.hpa": ..}, mv: max km/h in that minute or None}.
    """
    win = [r for r in rows if now - TREND_WINDOW <= r["t"] <= now]
    pts = [(r["t"], r["v"]["outdoor.hpa"]) for r in win if "outdoor.hpa" in r["v"]]
    if len(pts) < 20 or pts[-1][0] - pts[0][0] < 2 * 3600:
        return {"available": False, "reason": "needs a few hours of readings"}
    if any((r.get("mv") or 0) > MOVING_KPH for r in win):
        return {"available": False, "reason": "trend unavailable while driving"}
    # Mean of the first and last 10 minutes, so one noisy minute is not a trend.
    head = sum(v for _, v in pts[:10]) / len(pts[:10])
    tail = sum(v for _, v in pts[-10:]) / len(pts[-10:])
    d = round(tail - head, 1)
    word = "rising" if d >= TREND_RISE else "falling" if d <= -TREND_RISE else "steady"
    note = None
    if any(r.get("mv") is None for r in win):
        note = "no car link for part of this, assumed parked"
    return {"available": True, "hpa3h": d, "word": word, "note": note}


# ---- the record ---------------------------------------------------------------------

PROMPT_HOLD = 300      # s of CO2 over the line, moving, before it is worth a word
PROMPT_PPM = 1500
PROMPT_CLEAR = 1400    # and it has to fall below this (not just 1499) to start over
PROMPT_GAP = 30        # s stopped or fresh before the clock starts over


class Record:
    """Everything the service knows. Thread-safe enough: one lock, short holds."""

    def __init__(self, source="sensor"):
        self.lock = threading.Lock()
        self.source = source
        self.nodes = {}        # name -> {fw, port, last, vals: {k: [v, t]}}
        self.trail = {}        # "cabin.co2" -> [[t, v], ...]
        self.acc = {}          # minute accumulator: key -> [sum, n]
        self.minute = None
        self.mv = None         # max car speed this minute, or None for no car link
        self.car = {"speed": None, "ambient": None, "at": None}
        self.hot = {"since": None, "last_hot": None}

    # -- in
    def ingest(self, node, fw, vals, now, port=None):
        with self.lock:
            n = self.nodes.setdefault(node, {"vals": {}})
            n.update(fw=fw, last=now)
            if port:
                n["port"] = port
            for k, v in vals.items():
                n["vals"][k] = [v, now]
                key = f"{node}.{k}"
                if k in TRACKED.get(node, ()):
                    tr = self.trail.setdefault(key, [])
                    if not tr or now - tr[-1][0] >= TRAIL_EVERY:
                        tr.append([round(now, 1), v])
                    cut = now - TRAIL_KEEP
                    while tr and tr[0][0] < cut:
                        tr.pop(0)
                    a = self.acc.setdefault(key, [0.0, 0])
                    a[0] += v
                    a[1] += 1

    def set_car(self, speed, ambient, now):
        with self.lock:
            self.car = {"speed": speed, "ambient": ambient, "at": now}
            if speed is not None:
                self.mv = speed if self.mv is None else max(self.mv, speed)

    def tick_prompt(self, now):
        """The CO2 rule's clock. Call about once a second."""
        with self.lock:
            cab = self.nodes.get("cabin") or {}
            co2 = (cab.get("vals") or {}).get("co2")
            fresh = co2 is not None and now - co2[1] <= METRIC_FOR
            car_ok = self.car["at"] is not None and now - self.car["at"] <= CAR_FRESH
            moving = car_ok and (self.car["speed"] or 0) > MOVING_KPH
            h = self.hot
            if fresh and moving and co2[0] > PROMPT_PPM:
                h["last_hot"] = now
                if h["since"] is None:
                    h["since"] = now
            elif h["since"] is not None:
                # A stop at a light or a gap in the data is not a fresh start,
                # but fresh air (below PROMPT_CLEAR) or half a minute without
                # the conditions is.
                cleared = fresh and moving and co2[0] < PROMPT_CLEAR
                if cleared or now - (h["last_hot"] or now) > PROMPT_GAP:
                    h["since"] = h["last_hot"] = None

    # -- out: the minute row
    def roll_minute(self, now):
        """A history row when a minute has passed, else None."""
        with self.lock:
            m = int(now // 60)
            if self.minute is None:
                self.minute = m
                return None
            if m == self.minute:
                return None
            row = {"t": self.minute * 60 + 30,
                   "v": {k: round(s / n, 2) for k, (s, n) in self.acc.items() if n}}
            if self.mv is not None:
                row["mv"] = round(self.mv, 1)
            self.acc, self.mv, self.minute = {}, None, m
            return row if row["v"] else None

    def snapshot(self, now):
        with self.lock:
            return {
                "v": 1, "source": self.source, "at": now,
                "nodes": {n: {"fw": d.get("fw"), "port": d.get("port"), "last": d.get("last"),
                              "vals": {k: list(x) for k, x in d["vals"].items()}}
                          for n, d in self.nodes.items()},
                "trail": {k: list(v) for k, v in self.trail.items()},
                "car": dict(self.car),
                "hot": dict(self.hot),
            }

    def restore(self, st, now):
        """Pick up the trails after a restart; nothing else is believed."""
        if not isinstance(st, dict) or st.get("source") != self.source:
            return
        for k, tr in (st.get("trail") or {}).items():
            self.trail[k] = [p for p in tr if now - p[0] <= TRAIL_KEEP]
        for n, d in (st.get("nodes") or {}).items():
            if n in NODES:
                self.nodes[n] = {"fw": d.get("fw"), "port": d.get("port"),
                                 "last": d.get("last"), "vals": d.get("vals") or {}}


def write(snap):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f)
    os.replace(tmp, STATE)


def hist_path(source):
    return HIST_SIM if source == "sim" else HIST


def append_row(row, source):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(hist_path(source), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_rows(source, now=None, keep=HIST_KEEP):
    now = time.time() if now is None else now
    rows = []
    try:
        with open(hist_path(source), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue            # a torn last line after a power cut
                if isinstance(r, dict) and now - r.get("t", 0) <= keep:
                    rows.append(r)
    except OSError:
        pass
    return rows


def prune(source, now=None):
    """Rewrite the history without rows older than a day (at start, and daily)."""
    rows = load_rows(source, now)
    tmp = hist_path(source) + ".tmp"
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, hist_path(source))


# ---- what the API says --------------------------------------------------------------

def _side(snap, name, now):
    """One node, as the card needs it: values (live ones only while live), age, trails."""
    n = (snap.get("nodes") or {}).get(name)
    if not n or n.get("last") is None:
        return {"state": "absent", "values": {}}
    age = max(0.0, now - n["last"])
    live = age <= LIVE_FOR
    vals = {}
    for k, (v, t) in (n.get("vals") or {}).items():
        if not live or now - t <= METRIC_FOR:
            vals[k] = v
    return {"state": "live" if live else "stale", "age": round(age, 1), "values": vals,
            "fw": n.get("fw"), "port": n.get("port")}


def _trail(snap, name, k):
    return [p for p in (snap.get("trail") or {}).get(f"{name}.{k}", [])]


def _mean_recent(snap, key, now, secs=600):
    pts = [v for t, v in (snap.get("trail") or {}).get(key, []) if now - t <= secs]
    return sum(pts) / len(pts) if pts else None


def derive(snap, now=None, rows=None):
    """The public view of a state snapshot. Pure given its arguments."""
    now = time.time() if now is None else now
    sim = snap.get("source") == "sim"
    tag = "sim" if sim else "sensor"
    car = snap.get("car") or {}
    car_ok = car.get("at") is not None and now - car["at"] <= CAR_FRESH
    car_c = car.get("ambient") if car_ok else None
    moving = bool(car_ok and (car.get("speed") or 0) > MOVING_KPH)

    cab = _side(snap, "cabin", now)
    out = _side(snap, "outdoor", now)

    cv = cab["values"]
    cabin = {"state": cab["state"], "source": tag if cab["state"] != "absent" else None,
             "age": cab.get("age"), "values": cv,
             "dew_c": dew_point_c(cv.get("t_c"), cv.get("rh")),
             "ah": abs_humidity(cv.get("t_c"), cv.get("rh")),
             "co2_band": co2_band(cv.get("co2")),
             "trail": {k: _trail(snap, "cabin", k) for k in TRACKED["cabin"]}}

    ov = out["values"]
    outside = {"state": out["state"], "source": tag if out["state"] != "absent" else None,
               "age": out.get("age"), "values": ov, "car_t_c": car_c,
               "trail": {k: _trail(snap, "outdoor", k) for k in TRACKED["outdoor"]}}
    # The car's own thermometer stands in for the outdoor node's temperature
    # only when the node cannot give one. Always labelled as the car's.
    if "t_c" not in ov and car_c is not None:
        outside["values"] = dict(ov, t_c=round(car_c, 1))
        outside["t_source"] = "car"
        if out["state"] == "absent":
            outside["state"], outside["source"] = "live", "car"
    pm = _mean_recent(snap, "outdoor.pm25", now)
    pm = pm if pm is not None else ov.get("pm25")
    ov2 = outside["values"]
    outside.update(
        dew_c=dew_point_c(ov2.get("t_c"), ov2.get("rh")) if outside.get("t_source") != "car" else None,
        ah=abs_humidity(ov2.get("t_c"), ov2.get("rh")) if outside.get("t_source") != "car" else None,
        aqi=aqi_pm25(pm) if "pm25" in ov else None,
        pm25_mean=round(pm, 1) if pm is not None and "pm25" in ov else None)
    if rows is not None:
        outside["trend"] = pressure_trend(rows, now)

    hot = snap.get("hot") or {}
    since = hot.get("since")
    held = (now - since) if since is not None else 0
    cabin_co2 = cv.get("co2")
    prompt = {"due": bool(since is not None and held >= PROMPT_HOLD and moving
                          and cabin_co2 is not None and cabin_co2 > PROMPT_CLEAR),
              "ppm": cabin_co2, "held": int(held)}
    return {"present": True, "source": snap.get("source"), "age": round(max(0.0, now - snap.get("at", now)), 1),
            "moving": moving, "cabin": cabin, "outside": outside, "prompt": prompt}


def read_public(now=None):
    """For the API. {"present": False} when no node was ever seen and no car number exists."""
    now = time.time() if now is None else now
    try:
        with open(STATE, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return {"present": False}
    if not isinstance(snap, dict):
        return {"present": False}
    rows = load_rows(snap.get("source"), now, TREND_WINDOW + 600)
    d = derive(snap, now, rows)
    if d["age"] > 30:
        # Whoever was writing has stopped: nothing is live, and no prompt is due.
        for s in (d["cabin"], d["outside"]):
            if s["state"] == "live":
                s["state"] = "stale"
        d["prompt"]["due"] = False
        d["moving"] = False
    return d


def history(hours=24, now=None):
    """The full view's series, a point a minute: {"series": {"cabin.co2": [[t, v]]}}."""
    now = time.time() if now is None else now
    try:
        with open(STATE, encoding="utf-8") as f:
            source = (json.load(f) or {}).get("source")
    except (OSError, ValueError):
        return {"present": False, "series": {}}
    rows = load_rows(source, now, min(HIST_KEEP, max(1, hours) * 3600))
    series = {}
    for r in rows:
        v = r.get("v") or {}
        for k, x in v.items():
            series.setdefault(k, []).append([r["t"], x])
        for side in ("cabin", "outdoor"):
            d = dew_point_c(v.get(f"{side}.t_c"), v.get(f"{side}.rh"))
            if d is not None:
                series.setdefault(f"{side}.dew_c", []).append([r["t"], d])
        if "outdoor.pm25" in v:
            a = aqi_pm25(v["outdoor.pm25"])
            series.setdefault("outdoor.aqi", []).append([r["t"], a["index"]])
    moved = [r["t"] for r in rows if (r.get("mv") or 0) > MOVING_KPH]
    return {"present": True, "source": source, "t0": now - hours * 3600, "t1": now,
            "series": series, "moved": moved}


# ---- finding the nodes --------------------------------------------------------------

def _open_elsewhere(real, proc="/proc", me=None):
    """Does another process hold this device open? Best effort, own processes only."""
    me = str(os.getpid()) if me is None else me
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit() and p != me]
    except OSError:
        return False
    for p in pids:
        try:
            for fd in os.listdir(os.path.join(proc, p, "fd")):
                try:
                    if os.path.realpath(os.path.join(proc, p, "fd", fd)) == real:
                        return True
                except OSError:
                    continue
        except OSError:
            continue
    return False


def _config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def adapter_ports():
    """Ports that belong to the car link: what the daemon resolved or published."""
    ports = set()
    try:
        import connect
        p, _kind = connect.resolve()
        if p:
            ports.add(os.path.realpath(p))
        with open(connect.LIVE_JSON, encoding="utf-8") as f:
            lp = (json.load(f) or {}).get("port")
        if lp:
            ports.add(os.path.realpath(lp))
    except Exception:                                    # noqa: BLE001 -- best effort, and the other checks still stand
        pass
    return ports


def candidates(dev="/dev", sysfs="/sys", byid="/dev/serial/by-id", proc="/proc",
               avoid=None, busy=None):
    """[(real_path, label)] worth probing, and [(path, why)] for those refused.

    This is the whole safety argument for not touching the OBD adapter, so each
    refusal is named, and `scan` prints them.
    """
    import connect
    cfg = _config()
    extra = {str(x).lower() for x in cfg.get("allow", []) if isinstance(x, str)}
    pinned = [str(p) for p in cfg.get("ports", []) if isinstance(p, str)]
    avoid = adapter_ports() if avoid is None else avoid
    busy = _open_elsewhere if busy is None else busy
    seen, ok, no = {}, [], []
    for link in (sorted(glob.glob(os.path.join(byid, "*")))
                 + sorted(glob.glob(os.path.join(dev, "ttyACM*"))) + pinned):
        real = os.path.realpath(link)
        # The by-id name is the better label, and also what ADAPTER_NAME reads.
        if real not in seen or link.startswith(byid):
            seen[real] = os.path.basename(link)
    for real, label in seen.items():
        name = label
        if not os.path.basename(real).startswith(("ttyACM", "ttyUSB")) and real not in pinned:
            no.append((name, "not a USB serial port"))
            continue
        if real in avoid:
            no.append((name, "the car link's port"))
            continue
        if ADAPTER_NAME.search(name):
            no.append((name, "named like an OBD adapter"))
            continue
        ids = connect.usb_ids(os.path.basename(real), sysfs)
        if real not in pinned and (ids is None or (ids[0] not in NODE_VENDORS
                                                  and f"{ids[0]}:{ids[1]}" not in extra)):
            no.append((name, "not a known node board (" + (":".join(ids) if ids else "unknown ids") + ")"))
            continue
        if busy(real):
            no.append((name, "open in another process"))
            continue
        ok.append((real, name))
    return ok, no


def probe(port, opener=None, secs=3.0, clock=time.time):
    """Open `port` read-only for `secs`; the (node, fw, lines) it announces, or None."""
    if opener is None:
        import serial
        opener = lambda p: serial.Serial(p, 115200, timeout=0.5)     # noqa: E731
    try:
        s = opener(port)
    except (OSError, ValueError, Exception):             # noqa: BLE001 -- serial's own errors are OSError subclasses
        return None
    end = clock() + secs
    found, n = None, 0
    try:
        while clock() < end:
            r = parse_line(s.readline())
            if r:
                found = found or (r[0], r[1])
                n += 1
                if n >= 2:
                    break
    finally:
        try:
            s.close()
        except Exception:                                # noqa: BLE001
            pass
    return (found[0], found[1], n) if found else None


# ---- the service --------------------------------------------------------------------

def car_now():
    """(speed_kph, ambient_c) from the daemon's published sample, or (None, None)."""
    try:
        import records
        s = records.live()
        if not s.get("connected"):
            return None, None
        v = s.get("values") or {}
        sp, am = v.get("SPEED"), v.get("AMBIANT_AIR_TEMP")
        sp = float(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) else None
        am = clean_value("t_c", am) if am is not None else None
        return sp, am
    except Exception:                                    # noqa: BLE001 -- the car being absent is normal
        return None, None


def _reader(rec, port, label, stop, opener=None):
    if opener is None:
        import serial
        opener = lambda p: serial.Serial(p, 115200, timeout=1.0)     # noqa: E731
    try:
        s = opener(port)
    except Exception:                                    # noqa: BLE001
        return
    try:
        while not stop.is_set():
            r = parse_line(s.readline())
            if r:
                rec.ingest(r[0], r[1], r[2], time.time(), port=label)
    except Exception:                                    # noqa: BLE001 -- unplugged: the thread ends, the scan finds it again
        pass
    finally:
        try:
            s.close()
        except Exception:                                # noqa: BLE001
            pass


def run():
    try:
        import serial                                    # noqa: F401
    except ImportError:
        print("  pyserial is not installed in this interpreter: omacar setup", file=sys.stderr)
        return 1
    rec = Record("sensor")
    rec.restore(_load_state(), time.time())
    prune("sensor")
    held = {}                                            # real path -> (thread, stop)
    last_scan = last_prune = 0.0
    while True:
        now = time.time()
        if now - last_scan > 10:
            last_scan = now
            for p in [p for p, (t, _s) in held.items() if not t.is_alive()]:
                held.pop(p)
            ok, _no = candidates(avoid=adapter_ports() | set(held))
            for real, label in ok:
                if real in held:
                    continue
                hit = probe(real)
                if hit:
                    stop = threading.Event()
                    t = threading.Thread(target=_reader, args=(rec, real, label, stop), daemon=True)
                    t.start()
                    held[real] = (t, stop)
        sp, am = car_now()
        rec.set_car(sp, am, now)
        rec.tick_prompt(now)
        row = rec.roll_minute(now)
        if row:
            append_row(row, "sensor")
        if now - last_prune > 6 * 3600:
            last_prune = now
            prune("sensor")
        write(rec.snapshot(now))
        time.sleep(1.0)


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scan():
    ok, no = candidates()
    for name, why in no:
        print(f"  skipped   {name}: {why}")
    if not ok:
        print("  no candidate ports. Plug a node in (USB), then try again.")
        return 1
    try:
        import serial                                    # noqa: F401
    except ImportError:
        print("  pyserial is not installed in this interpreter: omacar setup", file=sys.stderr)
        return 1
    found = 0
    for real, name in ok:
        hit = probe(real)
        if hit:
            found += 1
            print(f"  found     {name}: node '{hit[0]}', firmware {hit[1]}, {hit[2]} lines")
        else:
            print(f"  silent    {name}: nothing that looks like a node in 3 s")
    return 0 if found else 1


# ---- the simulator ------------------------------------------------------------------

def _bump(x, lo, hi):
    """0 outside [lo, hi], a smooth hill inside (x, lo, hi in the same unit)."""
    if x <= lo or x >= hi:
        return 0.0
    return math.sin(math.pi * (x - lo) / (hi - lo)) ** 2


def sim_values(t, seed_co2=None):
    """A believable pair of nodes at wall time t: a day's rhythm, two commutes
    that load the cabin with breath, and a little noise on top."""
    d = (t % 86400) / 3600.0                                # hour of the (UTC) day
    trip = max(_bump(d, 7.5, 8.4), _bump(d, 17.2, 18.3))
    w = math.sin(t / 37.0)
    outdoor_t = 14 + 6 * math.sin(2 * math.pi * (d / 24 - 0.3))
    cabin = {"t_c": round(21.0 + 1.8 * math.sin(2 * math.pi * (d / 24 - 0.35)) + 2.2 * trip + 0.05 * w, 2),
             "rh": round(44 + 5 * math.sin(2 * math.pi * d / 24) - 4 * trip + 0.4 * w, 1),
             "co2": int(seed_co2 if seed_co2 is not None else 470 + 980 * trip + 35 * w),
             "pm1": round(2.0 + 0.8 * math.sin(2 * math.pi * d / 12) + 0.1 * w, 1),
             "pm25": round(3.2 + 1.4 * math.sin(2 * math.pi * d / 12) + 0.2 * w, 1),
             "pm4": round(3.8 + 1.5 * math.sin(2 * math.pi * d / 12) + 0.2 * w, 1),
             "pm10": round(4.4 + 1.6 * math.sin(2 * math.pi * d / 12) + 0.2 * w, 1),
             "voc": int(90 + 60 * trip + 5 * w), "nox": int(1 + 2 * trip)}
    outdoor = {"t_c": round(outdoor_t + 0.05 * w, 2),
               "rh": round(max(20, min(95, 63 - 2.2 * (outdoor_t - 14))) + 0.3 * w, 1),
               "hpa": round(1013.2 - 4.0 * math.sin(2 * math.pi * (t / (86400 * 2.5))) + 0.05 * w, 2),
               "pm1": round(2.0 + 2.0 * _bump(d, 15, 20) + 0.2 * w, 1),
               "pm25": round(3.5 + 6.0 * _bump(d, 15, 20) + 0.4 * w, 1),
               "pm10": round(6.0 + 7.0 * _bump(d, 15, 20) + 0.5 * w, 1)}
    return cabin, outdoor


def sim(seconds=None, seed=None, drive=False, co2=None, outdoor=True, speed=None):
    """Write a simulated pair of nodes. It says "sim" in the state, and the card says Simulated.

    drive: the car is moving (100 km/h) and has an ambient thermometer; with
    co2 over 1500 the "stale cabin air" clock starts already run down, so the
    prompt can be seen without waiting five minutes.
    outdoor=False: no outdoor node, so the card falls back to the car's number.
    """
    import random
    rnd = random.Random(seed)
    t0 = time.time()
    rec = Record("sim")
    # A day behind us, a point a minute, so the charts are not empty. Its own file.
    prune_path = hist_path("sim")
    if os.path.exists(prune_path):
        os.remove(prune_path)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(prune_path, "w", encoding="utf-8") as f:
        for i in range(24 * 60, 0, -1):
            tt = t0 - i * 60
            c, o = sim_values(tt)
            v = {f"cabin.{k}": c[k] for k in TRACKED["cabin"]}
            if outdoor:
                v.update({f"outdoor.{k}": o[k] for k in TRACKED["outdoor"]})
            row = {"t": int(tt // 60) * 60 + 30, "v": v, "mv": 0.0}
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    c, o = sim_values(t0, co2)
    for k in range(120):                                  # ten minutes of trail
        tt = t0 - (120 - k) * TRAIL_EVERY
        cc, oo = sim_values(tt, co2)
        rec.ingest("cabin", 1, cc, tt, port="simulated")
        if outdoor:
            rec.ingest("outdoor", 1, oo, tt, port="simulated")
    if drive and (co2 or 0) > PROMPT_PPM:
        rec.hot = {"since": t0 - PROMPT_HOLD - 10, "last_hot": t0}
    end = None if seconds is None else time.time() + seconds
    while True:
        now = time.time()
        c, o = sim_values(now, co2)
        c["t_c"] = round(c["t_c"] + rnd.uniform(-0.05, 0.05), 2)
        rec.ingest("cabin", 1, c, now, port="simulated")
        if outdoor:
            rec.ingest("outdoor", 1, o, now, port="simulated")
        rec.set_car(speed if speed is not None else (100.0 if drive else 0.0),
                    round(o["t_c"] + 2.0, 1) if drive or not outdoor else None, now)
        rec.tick_prompt(now)
        row = rec.roll_minute(now)
        if row:
            append_row(row, "sim")
        write(rec.snapshot(now))
        if end is not None and time.time() >= end:
            return rec
        time.sleep(1.0)


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "scan":
        return scan()
    if what == "run":
        return run()
    if what == "sim":
        opts = argv[1:]
        co2 = None
        if "--co2" in opts:
            co2 = int(opts[opts.index("--co2") + 1])
        sim(drive="--drive" in opts, co2=co2, outdoor="--no-outdoor" not in opts)
        return 0
    print(f"  omacar env: no such thing as '{what}'.\n"
          "  status | scan | run | sim [--drive] [--co2 N] [--no-outdoor]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
