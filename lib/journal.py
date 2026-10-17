#!/usr/bin/env python3
"""The trip journal: one entry per drive, enriched from whatever else was running.

WHAT IT IS. The drive log (lib/records.py `trips`) already says when a drive
started and ended, how far it went and what it burned. The journal adds what
else the car and cabin noticed during those minutes -- stale air (lib/envnode.py),
a fridge that complained (lib/fridge.py), radar alerts (lib/v1.py) -- and, when
those modules exist, the hybrid coach's score (lib/coach.py), incident clips
(lib/clips.py) and fill-ups. A rule writes one line per drive from it:

    38 min, 24 mi, 51 mpg · stale air once · 2 K-band alerts

No AI, no guessing: every phrase is a rule over a number that was recorded.

THREE STATES FOR EVERY SOURCE, because they are three different claims:

    absent  the module or the route is not here at all. Nothing is said.
    gap     it is here but has nothing for THIS drive (the radar log keeps the
            last 50 encounters; the air history keeps a day). The journal says
            "not recorded", never "no alerts" -- a quiet drive and a drive
            nobody was listening to are not the same drive.
    none    it was listening and had nothing to report. Said as "nothing".
    found   it had something. That is what gets a phrase and a badge.

NO LOCATION. There is no GPS in the tablet. When lib/location.py exists, place
names for a drive's start and end are available, and they are OPT-IN: the
switch below ("places", in journal.json under the config dir) is off, and a
journal with it off never asks the location module anything.

SIMULATED DATA NEVER TOUCHES A REAL JOURNAL. A source whose own state says it
is a simulator is treated as absent when the journal is real, and the
journal's own simulator writes a separate file and labels every entry.

THE PURE PART is assemble() and render(): a trip row and the sources in, an
entry out. Everything that touches the disk is below them.
"""

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.join(os.path.expanduser(
    os.environ.get("XDG_STATE_HOME") or "~/.local/state"), "omacar")
CONFIG_DIR = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME") or "~/.config"), "omacar")
STATE = os.path.join(STATE_DIR, "journal.json")
SIM = os.path.join(STATE_DIR, "journal-sim.json")
EVENTS = os.path.join(STATE_DIR, "journal-events.jsonl")
CONFIG = os.path.join(CONFIG_DIR, "journal.json")

SETTLE = 600            # s after a drive ends before its entry is frozen
EVENTS_KEEP = 3 * 24 * 3600
NOTE_MAX = 280
SOURCES = ("env", "fridge", "v1", "coach", "clips", "fuel")

# The stale-air rule is envnode's own (PROMPT_*), restated only as a fallback
# so the assembler stays importable on its own.
try:
    import envnode as _env
    AIR_PPM, AIR_CLEAR, AIR_HOLD = _env.PROMPT_PPM, _env.PROMPT_CLEAR, _env.PROMPT_HOLD
except Exception:                                              # noqa: BLE001
    AIR_PPM, AIR_CLEAR, AIR_HOLD = 1500, 1400, 300
AIR_GAP = 150           # s between minute rows that breaks a run (a node that went quiet)

FRIDGE_WARM_C = 4.0     # above the set point by this, for FRIDGE_WARM_S, is "warm"
FRIDGE_WARM_S = 600

MPG_K = 235.214583
BAND_WORDS = {"K": "K-band", "Ka": "Ka-band", "X": "X-band", "Ku": "Ku-band", "Laser": "laser"}
BAND_ORDER = ("Laser", "Ka", "K", "X", "Ku")


# ---- the pure part ------------------------------------------------------------------------

def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def window(trip):
    """(t0, t1, duration_s) of a trip row. Any of them may be None."""
    t0 = _num(trip.get("t0"))
    t1 = _num(trip.get("t1"))
    dur = None
    if t0 is not None and t1 is not None and t1 >= t0:
        dur = t1 - t0
    else:
        parts = [_num(trip.get("moving_s")), _num(trip.get("idle_s"))]
        if any(p is not None for p in parts):
            dur = sum(p for p in parts if p is not None)
            if t0 is not None:
                t1 = t0 + dur
    return t0, t1, dur


def air_finding(rows, t0, t1):
    """Stale-air episodes and the peak, from the per-minute rows inside the drive.

    The rule is envnode's: over PROMPT_PPM for PROMPT_HOLD, and it has to fall
    below PROMPT_CLEAR before another counts. Returns (status, finding|None).
    """
    inside = sorted((r for r in rows or [] if t0 - 60 <= _num(r.get("t")) <= t1 + 60
                     and _num((r.get("v") or {}).get("cabin.co2")) is not None),
                    key=lambda r: r["t"])
    if not inside:
        return "gap", None
    episodes, start, last_hot, peak = 0, None, None, 0.0
    prev_t = None
    for r in inside:
        t, co2 = float(r["t"]), float(r["v"]["cabin.co2"])
        peak = max(peak, co2)
        if prev_t is not None and t - prev_t > AIR_GAP and start is not None:
            if last_hot - start + 60 >= AIR_HOLD:
                episodes += 1
            start = last_hot = None
        if co2 > AIR_PPM:
            if start is None:
                start = t
            last_hot = t
        elif start is not None and co2 < AIR_CLEAR:
            if last_hot - start + 60 >= AIR_HOLD:
                episodes += 1
            start = last_hot = None
        prev_t = t
    if start is not None and last_hot - start + 60 >= AIR_HOLD:
        episodes += 1
    return ("found" if episodes else "none"), {
        "kind": "air", "stale": episodes, "peak_ppm": int(round(peak)),
        "tone": "warn" if episodes else "ok"}


def radar_finding(log, t0, t1, covered):
    if not covered:
        return "gap", None
    hits = [e for e in log or [] if _num(e.get("at")) is not None and t0 <= e["at"] <= t1]
    if not hits:
        return "none", None
    bands = {}
    for e in hits:
        b = e.get("band") or "?"
        bands[b] = bands.get(b, 0) + 1
    return "found", {"kind": "radar", "total": len(hits), "bands": bands,
                     "laser": bands.get("Laser", 0),
                     "tone": "bad" if bands.get("Laser") else "warn"}


def fridge_finding(events, covered):
    if not covered:
        return "gap", None
    doors = sum(1 for e in events or [] if e.get("kind") == "door")
    warm = sum(1 for e in events or [] if e.get("kind") == "warm")
    if not doors and not warm:
        return "none", None
    return "found", {"kind": "fridge", "door": doors, "warm": warm, "tone": "warn"}


def assemble(trip, sources=None, now=None):
    """One drive's entry. `trip` is a row of the trips table; `sources` maps a
    source name to None (absent) or a dict:

        env     {"rows": [{"t", "v": {"cabin.co2": ppm, ...}}]}   per-minute air rows
        v1      {"log": [{"at", "band"}], "covered": bool}
        fridge  {"events": [{"t", "kind": "door"|"warm"}], "covered": bool}
        coach   {"drives": [{"t0", "score", "partial", "simulated"}]}
        clips   {"items": [{"t", "label", "id"}]}
        fuel    {"items": [{"t", "litres", "cost"}]}

    Missing numbers stay missing: nothing here turns an unknown into a zero.
    """
    sources = sources or {}
    t0, t1, dur = window(trip)
    km = _num(trip.get("km"))
    lphk = _num(trip.get("lphk"))
    moving = _num(trip.get("moving_s"))
    avg_kph = km / (moving / 3600.0) if km is not None and moving and moving > 60 else None
    entry = {
        "id": t0 if t0 is not None else None,
        "t0": t0, "t1": t1, "duration_s": dur, "km": km,
        "avg_kph": round(avg_kph, 1) if avg_kph is not None else None,
        "lphk": lphk if lphk and lphk > 0 else None,
        "litres": _num(trip.get("litres")),
        "top_kph": _num(trip.get("top_kph")),
        "label": trip.get("label"),
        "open": trip.get("t1") in (None, 0),
        "findings": [], "sources": {}, "simulated": False, "note": None,
    }
    st = entry["sources"]

    def settle(name, status, finding):
        st[name] = status
        if finding:
            entry["findings"].append(finding)

    for name in SOURCES:
        s = sources.get(name)
        if s is None or t0 is None or t1 is None:
            st[name] = "absent" if s is None else "gap"
            continue
        if name == "env":
            settle(name, *air_finding(s.get("rows"), t0, t1))
        elif name == "v1":
            settle(name, *radar_finding(s.get("log"), t0, t1, s.get("covered", True)))
        elif name == "fridge":
            settle(name, *fridge_finding(s.get("events"), s.get("covered", True)))
        elif name == "coach":
            mine = [d for d in s.get("drives") or []
                    if _num(d.get("t0")) is not None and abs(d["t0"] - t0) <= 5]
            if mine and _num(mine[0].get("score")) is not None:
                d = mine[0]
                settle(name, "found", {"kind": "coach", "score": int(round(d["score"])),
                                       "partial": bool(d.get("partial")), "tone": "info"})
            else:
                settle(name, "none", None)
        elif name == "clips":
            hits = [c for c in s.get("items") or []
                    if _num(c.get("t")) is not None and t0 - 30 <= c["t"] <= t1 + 30]
            if hits:
                settle(name, "found", {"kind": "clips", "count": len(hits), "tone": "bad",
                                       "items": [{"id": c.get("id"), "label": c.get("label")}
                                                 for c in hits]})
            else:
                settle(name, "none", None)
        elif name == "fuel":
            hits = [f for f in s.get("items") or []
                    if _num(f.get("t")) is not None and t0 <= f["t"] <= t1 + 1800]
            if hits:
                settle(name, "found", {"kind": "fuel", "tone": "info",
                                       "litres": sum(_num(f.get("litres")) or 0 for f in hits) or None,
                                       "count": len(hits)})
            else:
                settle(name, "none", None)
    entry["findings"].sort(key=lambda f: FINDING_ORDER.index(f["kind"]))
    return entry


FINDING_ORDER = ["clips", "radar", "air", "fridge", "coach", "fuel"]


# ---- the words (rules, not a model) -------------------------------------------------------

def _plural(n, one, many=None):
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def times(n):
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def fmt_duration(secs):
    if secs is None:
        return None
    m = max(1, int(round(secs / 60.0)))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def fmt_dist(km, imperial):
    if km is None:
        return None
    d = km * (0.621371 if imperial else 1.0)
    unit = "mi" if imperial else "km"
    return f"{d:.1f} {unit}" if d < 10 else f"{int(round(d))} {unit}"


def fmt_econ(lphk, imperial):
    if not lphk:
        return None
    return f"{MPG_K / lphk:.0f} mpg" if imperial else f"{lphk:.1f} L/100 km"


def fmt_volume(litres, imperial):
    if litres is None:
        return None
    return f"{litres * 0.264172:.1f} gal" if imperial else f"{litres:.0f} L"


def phrase(f, imperial=True):
    """The short clause a finding adds to the one-line summary, or None."""
    k = f["kind"]
    if k == "air":
        return f"stale air {times(f['stale'])}" if f["stale"] else None
    if k == "radar":
        parts = []
        for b in sorted(f["bands"], key=lambda b: BAND_ORDER.index(b) if b in BAND_ORDER else 9):
            n = f["bands"][b]
            parts.append(f"{n} laser alert{'s' if n != 1 else ''}" if b == "Laser"
                         else f"{n} {BAND_WORDS.get(b, b + '-band')} alert{'s' if n != 1 else ''}")
        return ", ".join(parts) or None
    if k == "fridge":
        bits = []
        if f["door"]:
            bits.append("fridge door alert" if f["door"] == 1 else f"{f['door']} fridge door alerts")
        if f["warm"]:
            bits.append("fridge ran warm")
        return ", ".join(bits) or None
    if k == "coach":
        return f"coach {f['score']}" + (" (partial)" if f.get("partial") else "")
    if k == "clips":
        return _plural(f["count"], "clip")
    if k == "fuel":
        v = fmt_volume(f.get("litres"), imperial)
        return f"fill-up {v}" if v else "fill-up"
    return None


def detail_lines(f, imperial=True):
    """The sentences the detail view shows for one finding."""
    k = f["kind"]
    if k == "air":
        s = f"Cabin CO₂ peaked at {f['peak_ppm']:,} ppm."
        if f["stale"]:
            s += f" Stale air {times(f['stale'])}: over {AIR_PPM:,} ppm for five minutes or more."
        return [s]
    if k == "radar":
        p = phrase(f, imperial)
        out = [p[0].upper() + p[1:] + "."]
        if f.get("laser"):
            out.append("A laser alert means the detector saw a laser pulse; it does not say who fired it.")
        return out
    if k == "fridge":
        out = []
        if f["door"]:
            out.append(f"The fridge reported its lid open {times(f['door'])}.")
        if f["warm"]:
            out.append(f"It ran more than {FRIDGE_WARM_C:.0f} °C above its set point for ten minutes or more.")
        return out
    if k == "coach":
        return [f"Hybrid coach score {f['score']} out of 100"
                + (", scored on part of the drive." if f.get("partial") else ".")]
    if k == "clips":
        return [f"{_plural(f['count'], 'incident clip')} saved during this drive."]
    if k == "fuel":
        v = fmt_volume(f.get("litres"), imperial)
        return [f"Fuel added after this drive: {v}." if v else "A fill-up was logged around this drive."]
    return []


SOURCE_WORDS = {
    "env": "Cabin air", "fridge": "Fridge", "v1": "Radar detector",
    "coach": "Hybrid coach", "clips": "Incident clips", "fuel": "Fuel log",
}
STATUS_WORDS = {
    "gap": "not recorded for this drive",
    "none": "nothing to report",
    "found": "",
}


def render(entry, imperial=True):
    """The entry as the app shows it: the one-liner, the badges, the detail notes.

    Pure, and a function of the entry alone, so an entry can be stored without
    units and shown in whichever the owner has chosen this week.
    """
    head = [x for x in (fmt_duration(entry.get("duration_s")), fmt_dist(entry.get("km"), imperial),
                        fmt_econ(entry.get("lphk"), imperial)) if x]
    clauses = [p for p in (phrase(f, imperial) for f in entry.get("findings") or []) if p]
    line = ", ".join(head) if head else "Drive recorded"
    if clauses:
        line += " · " + " · ".join(clauses)
    badges = []
    for f in entry.get("findings") or []:
        p = phrase(f, imperial)
        if p:
            badges.append({"kind": f["kind"], "label": BADGE_WORD[f["kind"]](f), "tone": f.get("tone", "")})
    notes = []
    for f in entry.get("findings") or []:
        notes.extend({"kind": f["kind"], "tone": f.get("tone", ""), "text": t}
                     for t in detail_lines(f, imperial))
    checked = []
    for name in SOURCES:
        s = (entry.get("sources") or {}).get(name, "absent")
        if s == "absent":
            continue
        checked.append({"source": name, "name": SOURCE_WORDS[name], "state": s,
                        "text": STATUS_WORDS[s] if s != "found" else "reported"})
    return {"line": line, "badges": badges, "notes": notes, "checked": checked}


BADGE_WORD = {
    "air": lambda f: "Stale air" + (f" ×{f['stale']}" if f["stale"] > 1 else ""),
    "radar": lambda f: "Radar ×" + str(f["total"]) if f["total"] > 1 else "Radar",
    "fridge": lambda f: "Fridge",
    "coach": lambda f: f"Coach {f['score']}",
    "clips": lambda f: "Clip" if f["count"] == 1 else f"Clips ×{f['count']}",
    "fuel": lambda f: "Fill-up",
}


def public_entry(entry, imperial=True):
    """What the API sends: the stored entry plus its rendering."""
    out = dict(entry)
    out.update(render(entry, imperial))
    return out


# ---- gathering the sources ----------------------------------------------------------------

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


def _is_sim(state):
    return not isinstance(state, dict) or state.get("source") == "sim"


def gather(t0, t1, now=None):
    """The sources, for a REAL journal: each is None when it is not here or is a
    simulator. Every source is optional and may raise on any version of its
    module; none of them may take the journal down."""
    now = time.time() if now is None else now
    out = {name: None for name in SOURCES}

    try:                                    # cabin air
        import envnode
        st = _load_json(envnode.STATE)
        if st is not None and not _is_sim(st):
            out["env"] = {"rows": envnode.load_rows(st.get("source"), now, now - (t0 - 120))}
    except Exception:                                          # noqa: BLE001
        pass

    try:                                    # radar
        import v1
        st = v1.read_file()
        if st is not None and not _is_sim(st):
            log = st.get("log") or []
            oldest = min((e["at"] for e in log if _num(e.get("at")) is not None), default=None)
            full = len(log) >= v1.LOG_KEEP
            out["v1"] = {"log": log, "covered": not (full and oldest is not None and oldest > t0)}
    except Exception:                                          # noqa: BLE001
        pass

    try:                                    # fridge: transitions the journal's own loop saw
        import fridge
        st = _load_json(fridge.STATE)
        if st is not None and not _is_sim(st):
            ev = [e for e in read_events() if e.get("src") == "fridge" and t0 <= e["t"] <= t1]
            first = _first_event_time()
            out["fridge"] = {"events": ev, "covered": first is not None and first <= t0}
    except Exception:                                          # noqa: BLE001
        pass

    # Modules other builders add. Each is asked for what it already has.
    try:
        import coach
        pub = coach.read_public()
        if pub.get("present") and not pub.get("simulated"):
            out["coach"] = {"drives": [{"t0": d.get("t0_trip", d.get("t0")), "score": d.get("score"),
                                        "partial": d.get("partial")} for d in pub.get("drives") or []]}
    except Exception:                                          # noqa: BLE001
        pass
    try:
        import clips
        out["clips"] = {"items": [{"t": m.get("t"), "id": m.get("id"), "label": m.get("label")}
                                  for m in clips.list_incidents() if not m.get("sim")]}
    except Exception:                                          # noqa: BLE001
        pass
    try:
        import fuellog
        fn = getattr(fuellog, "journal_items", None)
        if fn:
            out["fuel"] = {"items": list(fn())}
    except Exception:                                          # noqa: BLE001
        pass
    return out


# ---- the fridge's transitions: the one source with no history of its own ----------------

def read_events():
    out = []
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and _num(e.get("t")) is not None:
                    out.append(e)
    except OSError:
        pass
    return out


def _first_event_time():
    """When the observer started: the earliest instant it can vouch for."""
    st = _load_json(STATE) or {}
    return _num(st.get("observing_since"))


def observe_fridge(state, prev, now):
    """Fold one reading of the fridge into events. Pure: returns (events, new_prev).

    A door event is the edge of the lid going from closed to open; a warm event
    is the edge of the zone having been more than FRIDGE_WARM_C over its set
    point for FRIDGE_WARM_S. `prev` is what the last call returned.
    """
    prev = dict(prev or {})
    events = []
    z = ((state or {}).get("zones") or [{}])[0] if state else {}
    door = bool(z.get("door")) or "Door open" in ((state or {}).get("alerts") or [])
    if door and not prev.get("door"):
        events.append({"t": now, "src": "fridge", "kind": "door"})
    prev["door"] = door
    temp, setp = _num(z.get("temp")), _num(z.get("set"))
    hot = temp is not None and setp is not None and temp - setp > FRIDGE_WARM_C
    if hot:
        since = prev.get("hot_since") or now
        prev["hot_since"] = since
        if now - since >= FRIDGE_WARM_S and not prev.get("warm"):
            events.append({"t": now, "src": "fridge", "kind": "warm"})
            prev["warm"] = True
    else:
        prev["hot_since"], prev["warm"] = None, False
    return events, prev


def append_events(events, now):
    if not events:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")


def prune_events(now):
    keep = [e for e in read_events() if now - e["t"] <= EVENTS_KEEP]
    tmp = EVENTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in keep:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    os.replace(tmp, EVENTS)


# ---- settings, notes, the store -----------------------------------------------------------

def location_available():
    """True when lib/location.py exists. It does not yet: no GPS in the tablet."""
    try:
        import location                                       # noqa: F401
        return True
    except Exception:                                          # noqa: BLE001
        return False


def settings():
    cfg = _load_json(CONFIG) or {}
    avail = location_available()
    return {"places": bool(cfg.get("places")) and avail, "places_available": avail}


def set_places(on):
    """The opt-in switch. Refuses to turn on what cannot work."""
    if on and not location_available():
        raise ValueError("there is no location source in this tablet yet")
    cfg = _load_json(CONFIG) or {}
    cfg["places"] = bool(on)
    _write_json(CONFIG, cfg)
    return settings()


def clean_note(text):
    t = " ".join(str(text or "").split())
    return t[:NOTE_MAX]


def _store():
    st = _load_json(STATE) or {}
    st.setdefault("frozen", {})
    st.setdefault("notes", {})
    return st


def set_note(t0, text):
    """The owner's own words about one drive. An empty note removes it."""
    key = str(int(float(t0)))
    st = _store()
    text = clean_note(text)
    if text:
        st["notes"][key] = text
    else:
        st["notes"].pop(key, None)
    _write_json(STATE, st)
    return {"t0": int(key), "note": text or None}


def places_for(entry):
    """Start and end place names, ONLY when the switch is on and a source exists."""
    if not settings()["places"]:
        return None
    try:
        import location
        return {"start": location.place_name(entry["t0"]), "end": location.place_name(entry["t1"])}
    except Exception:                                          # noqa: BLE001
        return None


def build(db, n=60, now=None):
    """Entries for the last n drives of the real log, newest first. Freezes the
    ones that have settled so the sources' short memories do not lose them."""
    import records
    now = time.time() if now is None else now
    st = _store()
    out, dirty = [], False
    rows = records.trips(db, n) if db is not None else []
    for trip in rows:
        t0 = _num(trip.get("t0"))
        if t0 is None:
            continue
        key = str(int(t0))
        frozen = st["frozen"].get(key)
        if frozen and frozen.get("t1") == _num(trip.get("t1")):
            entry = dict(frozen)
        else:
            t0_, t1_, _ = window(trip)
            entry = assemble(trip, gather(t0_, t1_ if t1_ is not None else t0_, now), now)
            if entry["t1"] is not None and not entry["open"] and now - entry["t1"] >= SETTLE:
                st["frozen"][key] = dict(entry)
                dirty = True
        entry["note"] = st["notes"].get(key)
        entry["places"] = places_for(entry)
        out.append(entry)
    if dirty:
        keep = {k: v for k, v in st["frozen"].items()
                if now - (v.get("t0") or 0) <= 400 * 24 * 3600}
        st["frozen"] = keep
        _write_json(STATE, st)
    return out


def read_public(imperial=True, n=60, now=None):
    """For the API. Real drives when there are any; else the simulator's week,
    labelled; else {"present": False}."""
    now = time.time() if now is None else now
    real = []
    try:
        import records
        db = records.connect()
        try:
            real = build(db, n, now) if db is not None else []
        finally:
            if db is not None:
                db.close()
    except Exception:                                          # noqa: BLE001
        real = []
    base = {"settings": settings(), "present": False, "source": None, "simulated": False,
            "entries": [], "sources": list(SOURCES), "now": now}
    if real:
        base.update(present=True, source="real",
                    entries=[public_entry(e, imperial) for e in real])
        return base
    sim = _load_json(SIM)
    if sim and sim.get("entries"):
        notes = sim.get("notes") or {}
        ents = []
        for e in sim["entries"][:n]:
            e = dict(e, note=notes.get(str(int(e["t0"]))), simulated=True)
            ents.append(public_entry(e, imperial))
        base.update(present=True, source="sim", simulated=True, entries=ents)
    return base


def set_note_any(t0, text):
    """A note on a real or a simulated drive; the simulator keeps its own."""
    sim = _load_json(SIM)
    key = str(int(float(t0)))
    if sim and any(str(int(e["t0"])) == key for e in sim.get("entries") or []) \
            and not (_store()["frozen"].get(key)):
        sim.setdefault("notes", {})
        text = clean_note(text)
        if text:
            sim["notes"][key] = text
        else:
            sim["notes"].pop(key, None)
        _write_json(SIM, sim)
        return {"t0": int(key), "note": text or None}
    return set_note(t0, text)


# ---- the simulator: a week of drives, every one of them labelled --------------------------

SIM_KINDS = (
    # (label, km, minutes, mph-ish avg kph, lphk, air, radar bands, fridge)
    ("Commute", 24.0, 38, 38, 4.6),
    ("Errand", 6.5, 17, 23, 5.9),
    ("School run", 8.2, 21, 24, 5.4),
    ("Motorway", 96.0, 71, 81, 4.4),
    ("Weekend", 41.0, 64, 38, 5.0),
)


def sim(now=None, seed=11, days=7):
    """Write journal-sim.json: `days` days of drives, assembled by the real
    assembler from synthetic sources, each entry flagged simulated."""
    now = time.time() if now is None else now
    rnd = random.Random(seed)
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    entries = []
    for d in range(days):
        day0 = midnight - d * 86400
        weekend = time.localtime(day0).tm_wday >= 5
        slots = [(10.5, 12.5)] if weekend else [(7.7, 8.4), (17.2, 18.6)]
        if rnd.random() < 0.45:
            slots.append((12.2, 13.4))
        for lo, hi in slots:
            kind = (SIM_KINDS[4] if weekend and lo < 11 else
                    SIM_KINDS[3] if rnd.random() < 0.12 else
                    SIM_KINDS[1] if lo < 13 and not weekend and rnd.random() < 0.5 else
                    SIM_KINDS[0] if not weekend else SIM_KINDS[2])
            label, km, mins, kph, lphk = kind
            t0 = float(int(day0 + rnd.uniform(lo, hi) * 3600) // 60 * 60)
            if t0 > now - 900:
                continue
            km *= rnd.uniform(0.85, 1.15)
            mins *= rnd.uniform(0.85, 1.2)
            t1 = t0 + mins * 60
            trip = {"t0": t0, "t1": t1, "km": round(km, 2), "lphk": round(lphk * rnd.uniform(.9, 1.15), 2),
                    "litres": round(km * lphk / 100, 2), "moving_s": int(mins * 60 * .9),
                    "idle_s": int(mins * 60 * .1), "top_kph": round(kph * 1.5), "kind": "drive",
                    "label": time.strftime("%H:%M", time.localtime(t0))}
            hot = rnd.random() < 0.4
            rows, t = [], t0
            while t <= t1:
                co2 = (1650 + 150 * rnd.random() if hot and t - t0 > 600 else 650 + 300 * rnd.random())
                rows.append({"t": t, "v": {"cabin.co2": co2}})
                t += 60
            bands = {}
            if rnd.random() < 0.35:
                bands["K"] = rnd.randint(1, 3)
            if rnd.random() < 0.15:
                bands["Ka"] = 1
            if rnd.random() < 0.05:
                bands["Laser"] = 1
            log = [{"at": t0 + 60 + 30 * i, "band": b}
                   for b, n in bands.items() for i in range(n)]
            doors = rnd.random() < 0.18
            fridge_ev = [{"t": t0 + 400, "kind": "door"}] if doors else []
            e = assemble(trip, {
                "env": {"rows": rows},
                "v1": {"log": log, "covered": True},
                "fridge": {"events": fridge_ev, "covered": True},
            }, now)
            e["simulated"] = True
            entries.append(e)
    entries.sort(key=lambda e: e["t0"], reverse=True)
    _write_json(SIM, {"source": "sim", "at": now, "entries": entries, "notes": {}})
    return len(entries)


# ---- the loop and the command line --------------------------------------------------------

def run(poll=15.0):
    """Watch the fridge's lid (it has no history of its own) and freeze settled
    drives so the sources' short memories cannot lose them."""
    prev, last_freeze, last_prune = {}, 0.0, 0.0
    st = _store()
    st.setdefault("observing_since", time.time())
    _write_json(STATE, st)
    print("  journal: watching", flush=True)
    while True:
        now = time.time()
        try:
            import fridge
            state = _load_json(fridge.STATE)
            if state is not None and not _is_sim(state) and now - float(state.get("at", 0)) < 60:
                ev, prev = observe_fridge(state, prev, now)
                append_events(ev, now)
        except Exception:                                      # noqa: BLE001
            pass
        if now - last_freeze > 300:
            try:
                read_public(True, 80, now)
            except Exception:                                  # noqa: BLE001
                pass
            last_freeze = now
        if now - last_prune > 86400:
            try:
                prune_events(now)
            except Exception:                                  # noqa: BLE001
                pass
            last_prune = now
        time.sleep(poll)


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        pub = read_public(True, int(argv[1]) if len(argv) > 1 else 10)
        print(f"  journal: {pub['source'] or 'no drives yet'}"
              f"{' (Simulated)' if pub['simulated'] else ''}, {len(pub['entries'])} shown")
        for e in pub["entries"]:
            print(f"  {time.strftime('%a %d %b %H:%M', time.localtime(e['t0']))}  {e['line']}"
                  + ("   [Simulated]" if e.get("simulated") else ""))
        return 0
    if what == "run":
        try:
            run()
        except KeyboardInterrupt:
            return 0
    if what == "sim":
        n = sim()
        print(f"  journal: wrote {n} simulated drives (every one labelled Simulated)")
        return 0
    if what == "places":
        if len(argv) < 2 or argv[1] not in ("on", "off"):
            print(json.dumps(settings()))
            return 0
        try:
            print(json.dumps(set_places(argv[1] == "on")))
        except ValueError as e:
            print(f"  journal: {e}", file=sys.stderr)
            return 1
        return 0
    if what == "note" and len(argv) >= 3:
        print(json.dumps(set_note_any(argv[1], " ".join(argv[2:]))))
        return 0
    print(f"  omacar journal: no such thing as '{what}'.\n"
          "  status | run | sim | places [on|off] | note <t0> <text>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
