#!/usr/bin/env python3
"""Tyre pressures, heard on 433 MHz by an RTL-SDR running rtl_433.

The owner has aftermarket (Tymate TM2) tyre sensors. They are not wired to the
car, so nothing here touches the car: an RTL-SDR dongle listens, `rtl_433`
decodes what it hears and prints one JSON object per packet, and this reads
those lines. Nothing is ever transmitted.

    omacar tpms status                what tpms.json says now
    omacar tpms run                   hold rtl_433 open (omacar-tpms.service)
    omacar tpms run --stdin           read rtl_433-shaped JSON lines from stdin
                                      (a capture replayed, or `tpms sim --lines`)
    omacar tpms learn [seconds]       listen, then list every sensor id heard
                                      with its RSSI and pressure (default 60 s)
    omacar tpms assign <id> <FL|FR|RL|RR>
    omacar tpms unassign <FL|FR|RL|RR>
    omacar tpms sim [seconds]         a simulated car: 4 assigned sensors, 2
                                      strangers, one slow leak. It says
                                      "Simulated" on screen, every time.
    omacar tpms sim --lines           the same packets as rtl_433 JSON lines
    omacar tpms sim --rapid           ...and the front right goes flat 25 s in

WHICH SENSORS ARE THE OWNER'S. A car park on 433 MHz is full of other people's
tyres. Only ids the owner has assigned (~/.config/omacar/tpms.json,
"sensors": {"<id>": "FL"}) are ever shown as the car's. Everything else is
"heard": kept in memory-sized form for thirty minutes so the learn list can
show it, never charted, never stored with a history, never on Home.

THE FIELDS rtl_433 USES (src/devices/tpms_*.c, merbanan/rtl_433):
  id              string, hex, per decoder (a few print an integer)
  model, type     "type": "TPMS" on every tpms_* decoder
  pressure_kPa    most decoders (gauge)         pressure_PSI  Toyota, Ford, Kia,
                                                Honda, Schrader and a few more
  temperature_C   most decoders                 temperature_F  Schrader
  battery_ok      1 = good, 0 = low (PMV-107J, Truck, Porsche and others)
  battery_V / battery_pct   AirPuxem / AVE
  pressure_alert  Truck: the sensor's own alarm bit
  `-M time:unix` adds "time" in seconds; `-M level` adds "rssi", "snr", "noise".
A decoder this does not know about still works if it names the fields so; a
flex decoder (-X) prints raw integers, which the "flex" block of the config
turns into pressure and temperature (doc/tpms.md).
"""

import collections
import datetime
import json
import math
import os
import random
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
CONFIG = os.path.join(CONFIG_DIR, "tpms.json")
STATE = os.path.join(STATE_DIR, "tpms.json")
HEARD = os.path.join(STATE_DIR, "tpms-heard.json")
HISTORY = os.path.join(STATE_DIR, "tpms-history.json")

WHEELS = ("FL", "FR", "RL", "RR")
WHEEL_NAMES = {"FL": "Front left", "FR": "Front right",
               "RL": "Rear left", "RR": "Rear right"}
PSI = 6.894757            # kPa per psi
ATM = 101.325             # kPa; sensors report gauge pressure
DAY = 86400.0

DEFAULTS = {
    "sensors": {},                               # id (or "model:id") -> wheel
    "placard_psi": {"front": 36.0, "rear": 36.0},   # COLD pressure; per-wheel keys override
    "units": "psi",                              # for notifications: psi | bar | kPa
    "frequency": "433.92M",
    "extra_args": [],                            # appended to the rtl_433 command
    "command": None,                             # replaces the whole command (string)
    "warn_below": 0.10,                          # 10 % under placard
    "bad_below": 0.20,                           # 20 % under placard
    "high_above": 0.25,                          # 25 % over placard
    "rapid_loss": 0.25,                          # 25 % lost within rapid_window_s
    "rapid_window_s": 600,
    "leak_psi_per_day": 0.15,                    # a slope steeper than this is a leak
    "leak_window_days": 14,                      # ...measured over this many days
    "cold_gap_h": 3.0,                           # parked this long = the first reading is cold
    "stale_s": 1200,                             # a wheel unheard for this long is "last seen"
    "flex": None,                                # see parse_line
}

HEARD_KEEP_S = 30 * 60
HEARD_MAX = 40
HIST_EVERY = 600          # s between kept history points per wheel
HIST_KEEP = 45 * DAY
TREND_DAYS = 30
RECENT_KEEP = 900         # s of every packet, for rapid-loss detection
FLUSH_EVERY = 3.0
SERVICE_STALE = 120       # s without a state write: the service has stopped


# ---- the pure parts: a line in, a number out ---------------------------------------

def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def pressure_kpa(d):
    """Gauge kPa from whichever pressure field this decoder uses, or None."""
    for key, k in (("pressure_kPa", 1.0), ("pressure_PSI", PSI), ("pressure_bar", 100.0)):
        v = _num(d.get(key))
        if v is not None:
            return v * k
    return None


def temperature_c(d):
    v = _num(d.get("temperature_C"))
    if v is not None:
        return v
    f = _num(d.get("temperature_F"))
    return None if f is None else (f - 32.0) * 5.0 / 9.0


def _when(d, now):
    t = d.get("time")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 1e9:
        return float(t)
    if isinstance(t, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.datetime.strptime(t[:19], fmt).timestamp()
            except ValueError:
                pass
    return now


def _flex(d, spec):
    """A flex decoder prints raw getter integers; the config says what they mean:
    {"model": "tymate", "id": "id",
     "pressure": {"key": "p", "scale": 1.6, "offset": 0, "unit": "kPa"},
     "temperature": {"key": "t", "scale": 1, "offset": -50}}.
    Returns (kpa, temp_c, id) or None when this line is not that decoder."""
    if not isinstance(spec, dict) or str(d.get("model")) != str(spec.get("model")):
        return None
    p, t = spec.get("pressure") or {}, spec.get("temperature") or {}
    raw = _num(d.get(p.get("key")))
    if raw is None:
        return None
    kpa = raw * float(p.get("scale", 1)) + float(p.get("offset", 0))
    unit = str(p.get("unit", "kPa")).lower()
    kpa *= PSI if unit == "psi" else 100.0 if unit == "bar" else 1.0
    tr = _num(d.get(t.get("key"))) if t else None
    temp = None if tr is None else tr * float(t.get("scale", 1)) + float(t.get("offset", 0))
    return kpa, temp, d.get(spec.get("id", "id"))


def parse_line(line, now=None, flex=None):
    """One line of rtl_433 -F json -> a packet dict, or None for anything that is
    not a tyre sensor (other 433 MHz devices, rtl_433's own chatter, junk).

    {"id", "model", "at", "kpa", "temp_c", "battery_ok", "battery_v",
     "battery_pct", "rssi", "alert"}"""
    now = time.time() if now is None else now
    line = (line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    got = _flex(d, flex) if flex else None
    if got:
        kpa, temp, ident = got
    else:
        kpa, temp, ident = pressure_kpa(d), temperature_c(d), d.get("id")
        # A weather station on the same band has an id and a temperature and is
        # not a tyre. A tyre says type TPMS or carries a pressure.
        if str(d.get("type", "")).upper() != "TPMS" and kpa is None:
            return None
    if ident is None or isinstance(ident, (dict, list, bool)):
        return None
    ident = str(ident).strip().lower()
    if not ident or kpa is None:
        return None
    # A bit-flip can decode to 400 psi. A tyre is between a flat and a truck's.
    if not 0.0 <= kpa <= 1500.0:
        return None
    if temp is not None and not -60.0 <= temp <= 180.0:
        temp = None
    ok = d.get("battery_ok")
    return {
        "id": ident, "model": str(d.get("model") or "?"), "at": _when(d, now),
        "kpa": round(kpa, 2), "temp_c": None if temp is None else round(temp, 1),
        "battery_ok": None if ok is None else bool(ok),
        "battery_v": _num(d.get("battery_V")), "battery_pct": _num(d.get("battery_pct")),
        "rssi": _num(d.get("rssi")), "alert": bool(d.get("pressure_alert")),
    }


def battery_state(pkt):
    """"ok" | "low" | None. The coin cell is 3 V new."""
    if pkt.get("battery_ok") is False:
        return "low"
    v, pct = pkt.get("battery_v"), pkt.get("battery_pct")
    if v is not None and v < 2.4:
        return "low"
    if pct is not None and pct < 20:
        return "low"
    if pkt.get("battery_ok") or v is not None or pct is not None:
        return "ok"
    return None


def normalise_kpa(gauge_kpa, temp_c, to_c=20.0):
    """The pressure this tyre would show at `to_c`, by the ideal-gas ratio on
    ABSOLUTE pressure (gauge + 101.325 kPa). A tyre is a fixed volume of air:
    p1/T1 = p2/T2. A hot tyre reads high and a cold one low; this takes the
    weather out so a leak is the only thing that moves the number. None for no
    temperature returns the reading as it is (and the caller says so)."""
    if gauge_kpa is None:
        return None
    if temp_c is None:
        return gauge_kpa
    return (gauge_kpa + ATM) * (to_c + 273.15) / (temp_c + 273.15) - ATM


def level_of(p20_kpa, placard_kpa, warn=0.10, bad=0.20, high=0.25):
    """"ok" | "warn" | "bad" | None. Against the placard, which is a COLD
    pressure, so p20 (not the hot reading) is what is compared."""
    if p20_kpa is None or not placard_kpa:
        return None
    short = 1.0 - p20_kpa / placard_kpa
    eps = 1e-9                      # exactly -10 % is -10 %, not 9.99999 %
    if short >= bad - eps:
        return "bad"
    if short >= warn - eps or -short >= high - eps:
        return "warn"
    return "ok"


def rapid_loss(samples, now, window_s=600, frac=0.25, fresh_s=180):
    """A fast fall: the newest TWO readings are both `frac` below the highest in
    the last `window_s`. Two, because one bad decode must not shout. `samples`
    is [(t, p20_kpa)] oldest first. Returns {"from", "to", "frac", "at"} or None."""
    recent = [(t, p) for t, p in samples if now - window_s <= t <= now + 5]
    if len(recent) < 3 or now - recent[-1][0] > fresh_s:
        return None
    peak = max(p for _, p in recent[:-2])
    if peak <= 0:
        return None
    last_two = recent[-2:]
    if all((peak - p) / peak >= frac for _, p in last_two):
        return {"from": round(peak, 1), "to": round(last_two[-1][1], 1),
                "frac": round((peak - last_two[-1][1]) / peak, 3), "at": last_two[-1][0]}
    return None


def cold_points(samples, gap_s=3 * 3600):
    """[(t, p20)] -> the first reading after each stretch of at least `gap_s`
    with no readings: the car sat, so the tyre is at ambient and the number is
    a cold one. (A tyre read mid-drive is warm and would blur a slow leak.)"""
    out, prev = [], None
    for t, p in samples:
        if prev is not None and t - prev >= gap_s:
            out.append((t, p))
        prev = t
    return out


def after_inflation(points, jump_kpa=14.0):
    """Drop everything up to the last jump of +2 psi between cold points: air
    was added, and a slope across it would be nonsense."""
    cut = 0
    for i in range(1, len(points)):
        if points[i][1] - points[i - 1][1] >= jump_kpa:
            cut = i
    return points[cut:]


def theil_sen(points):
    """Median of the slopes between every pair of points >= half a day apart,
    in units per DAY. Robust: one wild reading moves it by almost nothing.
    None with no such pair."""
    slopes = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dt = (points[j][0] - points[i][0]) / DAY
            if dt >= 0.5:
                slopes.append((points[j][1] - points[i][1]) / dt)
    if not slopes:
        return None
    slopes.sort()
    m = len(slopes) // 2
    return slopes[m] if len(slopes) % 2 else (slopes[m - 1] + slopes[m]) / 2


def leak_trend(points, leak_psi_day=0.15, min_points=4, min_span_days=3.0, min_drop_kpa=3.5):
    """The slow-leak verdict over cold, 20 C-normalised points [(t, kPa)].
    -> {"slope_kpa_day", "n", "span_days", "leak", "why"}. A leak needs a
    slope steeper than `leak_psi_day`, over at least four mornings across three
    days, AND to have cost half a psi: a noisy flat line is not a leak, and a
    steep line through two points is not a verdict."""
    pts = after_inflation(sorted(points))
    out = {"slope_kpa_day": None, "n": len(pts), "span_days": 0.0, "leak": False, "why": ""}
    if len(pts) >= 2:
        out["span_days"] = round((pts[-1][0] - pts[0][0]) / DAY, 2)
    if len(pts) < min_points or out["span_days"] < min_span_days:
        out["why"] = "Needs a few more mornings"
        return out
    slope = theil_sen(pts)
    out["slope_kpa_day"] = None if slope is None else round(slope, 3)
    if slope is None:
        out["why"] = "Needs a few more mornings"
        return out
    lost = -slope * out["span_days"]
    if slope <= -leak_psi_day * PSI and lost >= min_drop_kpa:
        out["leak"] = True
        out["why"] = "Losing air"
    else:
        out["why"] = "Holding"
    return out


def radio_note(tail):
    """What rtl_433's last lines mean for a person. `tail` is its non-JSON output."""
    text = " ".join(tail).lower()
    if "no supported devices" in text or "no device" in text:
        return "No RTL-SDR found: is it plugged in?"
    if "usb_claim_interface" in text or "usb_open" in text or "permission" in text:
        return "The RTL-SDR could not be opened (another program has it, or a udev rule is missing)"
    return (tail[-1][:120] if tail else "rtl_433 stopped")


def build_command(cfg):
    if cfg.get("command"):
        return shlex.split(str(cfg["command"]))
    cmd = ["rtl_433", "-f", str(cfg["frequency"]), "-F", "json", "-M", "time:unix", "-M", "level"]
    return cmd + [str(a) for a in (cfg.get("extra_args") or [])]


# ---- the owner's config ----------------------------------------------------------------

_ID = re.compile(r"^[0-9a-z:_.\-]{1,40}$")


def load_config(path=None):
    path = path or CONFIG
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path, encoding="utf-8") as f:
            mine = json.load(f)
    except (OSError, ValueError):
        mine = {}
    if isinstance(mine, dict):
        for k, v in mine.items():
            if k == "placard_psi" and isinstance(v, dict):
                cfg[k].update({a: float(b) for a, b in v.items() if _num(b) and float(b) > 0})
            elif k == "sensors" and isinstance(v, dict):
                cfg[k] = {str(i).strip().lower(): str(w).strip().upper() for i, w in v.items()
                          if str(w).strip().upper() in WHEELS and _ID.match(str(i).strip().lower())}
            elif k in cfg and k != "sensors":
                cfg[k] = v
    return cfg


def placard_kpa(cfg, wheel):
    p = cfg["placard_psi"]
    psi = p.get(wheel) or p.get("front" if wheel.startswith("F") else "rear") or 36.0
    return float(psi) * PSI


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _config_raw(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def assign(ident, wheel, path=None):
    """Bind one sensor id to one wheel. One id, one wheel; one wheel, one id."""
    path = path or CONFIG
    ident, wheel = str(ident).strip().lower(), str(wheel).strip().upper()
    if wheel not in WHEELS:
        raise ValueError(f"a wheel is one of {', '.join(WHEELS)}")
    if not _ID.match(ident):
        raise ValueError("that is not a sensor id")
    raw = _config_raw(path)
    sensors = {str(i).lower(): str(w).upper() for i, w in (raw.get("sensors") or {}).items()}
    sensors = {i: w for i, w in sensors.items() if w != wheel and i != ident}
    sensors[ident] = wheel
    raw["sensors"] = sensors
    _write_json(path, raw)
    return {"assigned": ident, "wheel": wheel}


def unassign(wheel, path=None):
    path = path or CONFIG
    wheel = str(wheel).strip().upper()
    if wheel not in WHEELS:
        raise ValueError(f"a wheel is one of {', '.join(WHEELS)}")
    raw = _config_raw(path)
    raw["sensors"] = {i: w for i, w in (raw.get("sensors") or {}).items()
                      if str(w).upper() != wheel}
    _write_json(path, raw)
    return {"unassigned": wheel}


# ---- the tracker: packets in, a picture of four wheels out ---------------------------

class Tracker:
    """Everything the screens show, from the packets heard. Takes its clock as
    an argument so a test can run a month in a millisecond."""

    def __init__(self, cfg, history=None, source="rtl_433", sensors=None):
        self.cfg = cfg
        self.source = source
        self.sensors = dict(cfg["sensors"] if sensors is None else sensors)
        h = history or {}
        self.hist = {w: [tuple(p) for p in (h.get("wheels") or {}).get(w, [])] for w in WHEELS}
        self.notified = dict(h.get("notified") or {})
        self.last = {w: None for w in WHEELS}          # newest packet per wheel
        self.recent = {w: collections.deque() for w in WHEELS}   # (t, p20), every packet
        self.heard = {}                                 # id -> what the learn list shows
        self.rapid_at = {}                              # wheel -> {event}

    def wheel_of(self, pkt):
        return self.sensors.get(f"{pkt['model'].lower()}:{pkt['id']}") or self.sensors.get(pkt["id"])

    def ingest(self, pkt):
        """-> the wheel it was for, or None (a stranger)."""
        wheel = self.wheel_of(pkt)
        h = self.heard.setdefault(pkt["id"], {"id": pkt["id"], "first": pkt["at"], "n": 0})
        h.update(model=pkt["model"], rssi=pkt["rssi"], kpa=pkt["kpa"], temp_c=pkt["temp_c"],
                 at=pkt["at"], wheel=wheel)
        h["n"] += 1
        self._prune_heard(pkt["at"])
        if wheel is None:
            return None
        p20 = normalise_kpa(pkt["kpa"], pkt["temp_c"])
        pkt = dict(pkt, p20=p20)
        prev = self.last[wheel]
        if prev is None or pkt["at"] >= prev["at"]:
            self.last[wheel] = pkt
        rec = self.recent[wheel]
        rec.append((pkt["at"], p20))
        while rec and pkt["at"] - rec[0][0] > RECENT_KEEP:
            rec.popleft()
        hist = self.hist[wheel]
        if not hist or pkt["at"] - hist[-1][0] >= HIST_EVERY:
            hist.append((pkt["at"], round(p20, 2)))
        while hist and pkt["at"] - hist[0][0] > HIST_KEEP:
            hist.pop(0)
        return wheel

    def _prune_heard(self, now):
        for i in [i for i, v in self.heard.items() if now - v["at"] > HEARD_KEEP_S]:
            del self.heard[i]
        if len(self.heard) > HEARD_MAX:
            for i in sorted(self.heard, key=lambda k: self.heard[k]["at"])[:len(self.heard) - HEARD_MAX]:
                del self.heard[i]

    def wheel_state(self, wheel, now):
        cfg = self.cfg
        ident = next((i for i, w in self.sensors.items() if w == wheel), None)
        pk = self.last[wheel]
        placard = placard_kpa(cfg, wheel)
        cold = [(t, p) for t, p in cold_points(self.hist[wheel], cfg["cold_gap_h"] * 3600)
                if now - t <= TREND_DAYS * DAY]
        recent_cold = [(t, p) for t, p in cold if now - t <= cfg["leak_window_days"] * DAY]
        trend = leak_trend(recent_cold, cfg["leak_psi_per_day"])
        rapid = rapid_loss(list(self.recent[wheel]), now, cfg["rapid_window_s"], cfg["rapid_loss"])
        st = {
            "wheel": wheel, "name": WHEEL_NAMES[wheel], "id": ident, "assigned": ident is not None,
            "kpa": None, "temp_c": None, "p20_kpa": None, "placard_kpa": round(placard, 1),
            "level": None, "battery": None, "battery_v": None, "rssi": None, "model": None,
            "at": None, "normalised": None,
            "trend": [[round(t, 1), round(p, 1)] for t, p in cold],
            "slope_kpa_day": trend["slope_kpa_day"], "leak": trend["leak"],
            "trend_note": trend["why"], "trend_n": trend["n"], "rapid": rapid,
        }
        if pk:
            lvl = level_of(pk["p20"], placard, cfg["warn_below"], cfg["bad_below"], cfg["high_above"])
            st.update(kpa=pk["kpa"], temp_c=pk["temp_c"], p20_kpa=round(pk["p20"], 1),
                      level=lvl, battery=battery_state(pk), battery_v=pk["battery_v"],
                      rssi=pk["rssi"], model=pk["model"], at=pk["at"],
                      normalised=pk["temp_c"] is not None)
        return st

    def snapshot(self, now):
        wheels = [self.wheel_state(w, now) for w in WHEELS]
        alerts = []
        for w in wheels:
            if w["rapid"]:
                alerts.append({"wheel": w["wheel"], "kind": "rapid", "tone": "bad",
                               "at": w["rapid"]["at"], "frac": w["rapid"]["frac"],
                               "text": f"{w['name']} tire is losing pressure fast"})
            elif w["leak"]:
                alerts.append({"wheel": w["wheel"], "kind": "leak", "tone": "warn", "at": now,
                               "text": f"{w['name']} tire is leaking slowly"})
        return {"source": self.source, "wheels": wheels, "alerts": alerts,
                "assigned": sum(1 for w in wheels if w["assigned"]),
                "units": self.cfg.get("units", "psi")}

    def heard_list(self, now):
        rows = sorted(self.heard.values(), key=lambda r: (-(r["rssi"] if r["rssi"] is not None else -999)))
        return [dict(r, age=round(max(0.0, now - r["at"]), 1)) for r in rows]

    def due_notifications(self, now):
        """[(kind, title, body, urgency, key)], each wheel/kind at most once a
        day (the outbox's own dedupe is ten minutes, too short for this)."""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        out = []
        snap = self.snapshot(now)
        for a in snap["alerts"]:
            slot = f"{a['kind']}:{a['wheel']}"
            if self.notified.get(slot) == day:
                continue
            w = next(x for x in snap["wheels"] if x["wheel"] == a["wheel"])
            now_txt = fmt_pressure(w["p20_kpa"], self.cfg.get("units", "psi"))
            if a["kind"] == "rapid":
                body = (f"{now_txt} and falling quickly ({round(a['frac'] * 100)} % in the last "
                        f"{round(self.cfg['rapid_window_s'] / 60)} minutes). Stop somewhere safe and look.")
                out.append(("tyre-rapid", f"{w['name']} tire losing air fast", body, "critical",
                            f"tyre-rapid-{a['wheel']}-{day}"))
            else:
                per_day = fmt_pressure(abs(w["slope_kpa_day"] or 0), self.cfg.get("units", "psi"))
                body = (f"Down about {per_day} a day over {w['trend_n']} cold readings; "
                        f"{now_txt} now (as at 20 °C).")
                out.append(("tyre-leak", f"{w['name']} tire is leaking slowly", body, "warn",
                            f"tyre-leak-{a['wheel']}-{day}"))
            self.notified[slot] = day
        return out

    def history_doc(self):
        return {"wheels": {w: [[round(t, 1), p] for t, p in self.hist[w]] for w in WHEELS},
                "notified": self.notified}


def fmt_pressure(kpa, units="psi", unit=True):
    if kpa is None:
        return "—"
    if units == "bar":
        s, u = f"{kpa / 100:.2f}", " bar"
    elif units == "kPa":
        s, u = f"{kpa:.0f}", " kPa"
    else:
        s, u = f"{kpa / PSI:.1f}", " psi"
    return s + (u if unit else "")


# ---- the file the app reads -----------------------------------------------------------

def write_state(tracker, now, radio=None, note=None, running=True):
    doc = tracker.snapshot(now)
    doc.update(running=running, radio=radio, note=note, at=now,
               placard_psi=tracker.cfg["placard_psi"])
    _write_json(STATE, doc)
    _write_json(HEARD, {"at": now, "source": tracker.source, "heard": tracker.heard_list(now)})
    return doc


def read_public(now=None):
    """For the API. {"present": False} when nothing was ever heard or simulated.
    A wheel keeps its last reading, with its age; it is `fresh` only if it is
    young AND the service is still writing. A stale file shows no alert."""
    now = time.time() if now is None else now
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"present": False}
    st["present"] = True
    st["age"] = max(0.0, now - float(st.get("at", 0)))
    alive = st["age"] <= SERVICE_STALE
    st["running"] = bool(st.get("running")) and alive
    stale_s = load_config()["stale_s"] if st.get("source") != "sim" else 1e9
    for w in st.get("wheels", []):
        w["age"] = None if w.get("at") is None else max(0.0, now - w["at"])
        w["fresh"] = bool(alive and w["age"] is not None and w["age"] <= stale_s)
    if not alive:
        st["alerts"] = []
        for w in st.get("wheels", []):
            w["rapid"] = None
    return st


def read_heard(now=None):
    """The learn list: every id heard in the last half hour, loudest first. Only
    the learn screen asks for this; it is the one place strangers appear."""
    now = time.time() if now is None else now
    try:
        with open(HEARD, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {"present": False, "heard": []}
    age = max(0.0, now - float(d.get("at", 0)))
    rows = [dict(r, age=round(max(0.0, now - r["at"]), 1)) for r in d.get("heard", [])
            if now - r["at"] <= HEARD_KEEP_S]
    return {"present": True, "source": d.get("source"), "running": age <= SERVICE_STALE,
            "heard": rows, "sensors": load_config()["sensors"]}


def load_history():
    try:
        with open(HISTORY, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---- the service -------------------------------------------------------------------------

def _feed(tracker, lines, flush_state, notify_mod=None, now=time.time):
    """The loop body, shared by the live process and --stdin."""
    last_flush = 0.0
    for line in lines:
        t = now()
        pkt = parse_line(line, t, tracker.cfg.get("flex"))
        if pkt:
            tracker.ingest(pkt)
        if t - last_flush >= FLUSH_EVERY:
            last_flush = t
            flush_state(t)
            send_due(tracker, t, notify_mod)


def send_due(tracker, now, notify_mod=None):
    if tracker.source == "sim":
        return                                     # a simulator never writes the owner's outbox
    if notify_mod is None:
        import notify as notify_mod
    for kind, title, body, urgency, key in tracker.due_notifications(now):
        notify_mod.send(kind, title, body, urgency, key=key, now=now)
    _write_json(HISTORY, tracker.history_doc())


def run(stdin=False):
    cfg = load_config()
    tracker = Tracker(cfg, load_history())
    tail = collections.deque(maxlen=8)

    def flush(t, radio="ok", note=None, running=True):
        write_state(tracker, t, radio=radio, note=note, running=running)

    if stdin:
        try:
            _feed(tracker, sys.stdin, flush)
        finally:
            flush(time.time(), running=False)
            _write_json(HISTORY, tracker.history_doc())
        return 0

    wait = 5
    while True:
        cfg = load_config()
        tracker.cfg, tracker.sensors = cfg, dict(cfg["sensors"])
        cmd = build_command(cfg)
        if shutil.which(cmd[0]) is None:
            flush(time.time(), radio="missing", note=f"{cmd[0]} is not installed (pacman -S rtl_433)")
            time.sleep(60)
            continue
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
        except OSError as e:
            flush(time.time(), radio="missing", note=str(e))
            time.sleep(60)
            continue
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        mtime, last_flush, last_cfg = 0.0, 0.0, time.time()
        flush(time.time(), radio="starting")
        while proc.poll() is None:
            ready = sel.select(timeout=1.0)
            t = time.time()
            for _key, _ in ready:
                line = proc.stdout.readline()
                if not line:
                    continue
                pkt = parse_line(line, t, cfg.get("flex"))
                if pkt:
                    tracker.ingest(pkt)
                    wait = 5
                elif not line.lstrip().startswith("{"):
                    tail.append(line.strip())
            if t - last_cfg > 5:                    # an assignment made in the app, picked up
                last_cfg = t
                cfg = load_config()
                tracker.cfg, tracker.sensors = cfg, dict(cfg["sensors"])
            if t - last_flush >= FLUSH_EVERY:
                last_flush = t
                flush(t)
                send_due(tracker, t)
        code = proc.returncode
        note = radio_note(list(tail)) if code else "rtl_433 stopped"
        flush(time.time(), radio="error", note=note, running=False)
        _write_json(HISTORY, tracker.history_doc())
        time.sleep(wait)
        wait = min(120, wait * 2)


# ---- the learn list, from a shell ----------------------------------------------------------

def learn(seconds=60):
    cfg = load_config()
    cmd = build_command(cfg)
    if shutil.which(cmd[0]) is None:
        print(f"  {cmd[0]} is not installed: pacman -S rtl_433", file=sys.stderr)
        return 1
    print(f"  listening for {seconds:.0f} s on {cfg['frequency']} ... "
          "(to find your own: let ~3 psi out of one tyre while this runs)")
    tracker = Tracker(cfg)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    first, end = {}, time.time() + seconds
    tail = []
    try:
        while time.time() < end and proc.poll() is None:
            if sel.select(timeout=0.5):
                line = proc.stdout.readline()
                pkt = parse_line(line, time.time(), cfg.get("flex"))
                if pkt:
                    first.setdefault(pkt["id"], pkt["kpa"])
                    tracker.ingest(pkt)
                elif line.strip():
                    tail.append(line.strip())
    finally:
        proc.terminate()
    rows = tracker.heard_list(time.time())
    if not rows:
        print("  nothing heard. " + (radio_note(tail) if tail else
              "Sensors in a parked car may only speak every few minutes; try driving a block."))
        return 1
    print(f"\n  {'id':<14}{'model':<16}{'RSSI':>7}{'psi':>8}{'change':>9}  wheel")
    for r in rows:
        d = (r["kpa"] - first.get(r["id"], r["kpa"])) / PSI
        rssi = "—" if r["rssi"] is None else f"{r['rssi']:.1f}"
        print(f"  {r['id']:<14}{r['model'][:15]:<16}{rssi:>7}{r['kpa'] / PSI:>8.1f}{d:>+9.1f}  "
              f"{r['wheel'] or '(not assigned)'}")
    print("\n  Assign one:  omacar tpms assign <id> <FL|FR|RL|RR>")
    return 0


# ---- the simulator ---------------------------------------------------------------------------

SIM_SENSORS = {"a1b2c1": "FL", "a1b2c2": "FR", "a1b2c3": "RL", "a1b2c4": "RR"}
SIM_STRANGERS = ("7e91f0", "30cc42")
SIM_MODEL = "Simulated-TPMS"


def _sim_p20(wheel, day_ago):
    """Cold 20 C pressure in psi, `day_ago` days back. Three tyres hold their
    air (a tenth of a psi a week); the rear left has leaked since twelve days ago."""
    if wheel == "RL":
        return 36.4 - 0.42 * max(0.0, 12 - day_ago)
    return 36.0 - 0.02 * (30 - day_ago)


def sim_history(now, rnd):
    """Thirty days of commutes: each morning and each afternoon, the car has sat
    for hours, so the first reading is cold, and the next one is warm."""
    wheels = {w: [] for w in WHEELS}
    for d in range(30, -1, -1):
        for hours_ago in (9.0, 1.0) if d else (9.0,):
            t = now - d * DAY - hours_ago * 3600
            ambient = 13.0 + rnd.uniform(-3, 3)
            for w in WHEELS:
                p = (_sim_p20(w, d) + rnd.uniform(-0.2, 0.2)) * PSI
                cold = (p + ATM) * (ambient + 273.15) / 293.15 - ATM
                warm = (p + ATM) * (ambient + 15 + 273.15) / 293.15 - ATM
                wheels[w].append((round(t, 1), round(normalise_kpa(cold, ambient), 2)))
                wheels[w].append((round(t + 900, 1), round(normalise_kpa(warm, ambient + 15), 2)))
    for w in WHEELS:
        wheels[w].sort()
    return {"wheels": wheels}


def sim_packet(sensor_id, kpa, temp_c, rssi, t):
    """The line rtl_433 -F json -M time:unix -M level prints for a Jansite-style
    decoder (src/devices/tpms_jansite.c fields), under a name that says it is not."""
    return json.dumps({"time": round(t, 3), "model": SIM_MODEL, "type": "TPMS", "id": sensor_id,
                       "flags": 0, "pressure_kPa": round(kpa, 1), "temperature_C": round(temp_c, 1),
                       "battery_ok": 1, "mic": "CRC", "mod": "FSK", "freq": 433.92,
                       "rssi": round(rssi, 1), "snr": 18.2, "noise": -26.0})


def sim_lines(seed=None, seconds=None, pace=1.0, clock=time.time, rapid=False):
    """An endless (or `seconds` long) walk of packets: the four sensors and two
    strangers, one after another, a drive's warm tyres."""
    rnd = random.Random(seed)
    ids = list(SIM_SENSORS) + list(SIM_STRANGERS)
    rssi = {i: rnd.uniform(-14, -6) if i in SIM_SENSORS else rnd.uniform(-22, -16) for i in ids}
    start = clock()
    n = 0
    while seconds is None or clock() - start < seconds:
        i = ids[n % len(ids)]
        n += 1
        t = clock()
        warm = min(1.0, (t - start) / 600.0)
        temp = 18.0 + 22.0 * warm + rnd.uniform(-0.6, 0.6)
        wheel = SIM_SENSORS.get(i)
        cold_psi = _sim_p20(wheel, 0) if wheel else rnd.uniform(33, 38)
        if rapid and wheel == "FR" and t - start > 25:      # a puncture, 2 %/s from 25 s in
            cold_psi *= max(0.5, 1.0 - 0.02 * (t - start - 25))
        kpa = (cold_psi * PSI + ATM) * (temp + 273.15) / 293.15 - ATM
        yield sim_packet(i, kpa, temp, rssi[i] + rnd.uniform(-1.5, 1.5), t)
        if pace:
            time.sleep(pace)


def sim(seconds=None, seed=None, rapid=False):
    cfg = load_config()
    rnd = random.Random(seed)
    now = time.time()
    tracker = Tracker(cfg, sim_history(now, rnd), source="sim", sensors=SIM_SENSORS)
    lines = sim_lines(seed, seconds, pace=1.0, rapid=rapid)
    try:
        _feed(tracker, lines, lambda t: write_state(tracker, t, radio="sim", note="Simulated"))
    except KeyboardInterrupt:
        pass
    write_state(tracker, time.time(), radio="sim", note="Simulated", running=False)
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "run":
        return run(stdin="--stdin" in argv)
    if what == "learn":
        return learn(float(argv[1]) if len(argv) > 1 else 60.0)
    if what == "assign" and len(argv) == 3:
        try:
            print("  " + json.dumps(assign(argv[1], argv[2])))
            return 0
        except ValueError as e:
            print(f"  {e}", file=sys.stderr)
            return 2
    if what == "unassign" and len(argv) == 2:
        try:
            print("  " + json.dumps(unassign(argv[1])))
            return 0
        except ValueError as e:
            print(f"  {e}", file=sys.stderr)
            return 2
    if what == "sim":
        if "--lines" in argv:
            try:
                for line in sim_lines():
                    print(line, flush=True)
            except (KeyboardInterrupt, BrokenPipeError):
                pass
            return 0
        rest = [a for a in argv[1:] if not a.startswith("-")]
        return sim(float(rest[0]) if rest else None, rapid="--rapid" in argv)
    print(f"  omacar tpms: no such thing as '{what}'.\n"
          "  status | run | learn | assign | unassign | sim", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
