#!/usr/bin/env python3
"""Drowsy mode's settings, and where its events and measures are written.

    GET  /api/drowsy          share/data/drowsy.json with the owner's
                              ~/.config/omarchy/omacar-drowsy.json laid over it
    POST /api/drowsy          what the app may change: on or off, sensitivity,
                              the name the voice uses, which sounds rotate
    POST /api/drowsy/event    one alert, into the records book as kind=drowsy
    POST /api/drowsy/log      per-second measures, appended to
                              $XDG_STATE_HOME/omacar/drowsy/YYYY-MM-DD.jsonl

Thresholds are not changed from the app. They are changed by hand, in the
file, which is where Wednesday's tuning in the Los Banos office happens: the
measures log beside the recorded cabin clips.
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = os.path.join(ROOT, "share", "data", "drowsy.json")
SENSITIVITIES = ("standard", "sensitive")
# The names the voice can say are the ones there are clips for
# (tools/render_voice.py). "" is the clips with no name.
NAMES = ("James", "")
SOUNDS = ("bark", "voice", "alarm")
# A speed an event may carry, in km/h: anything else is a bug, not a car.
MAX_KPH = 400


def user_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-drowsy.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _overlay(base, over):
    """`over` laid on `base` key by key, keeping only keys `base` has and values
    of the same kind, so a hand-edited typo cannot turn a threshold into a
    string."""
    out = {}
    for k, v in base.items():
        o = over.get(k, v)
        if k.startswith("_"):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = _overlay(v, o if isinstance(o, dict) else {})
        elif isinstance(v, bool):
            out[k] = o if isinstance(o, bool) else v
        elif isinstance(v, (int, float)):
            out[k] = o if isinstance(o, (int, float)) and not isinstance(o, bool) else v
        elif isinstance(v, str):
            out[k] = o if isinstance(o, str) else v
        elif isinstance(v, list):
            out[k] = o if isinstance(o, list) else v
        else:
            out[k] = v
    return out


def load():
    defaults = _read(DEFAULTS)
    out = _overlay(defaults, _read(user_path()))
    # The rotation, from a hand-edited file, keeps only sounds that exist
    # (Task 9 fix round 1): an unknown name is dropped with a warning, and a
    # list with none left is the spec's own.
    if "sounds" in out:
        raw = out["sounds"]
        kept = list(dict.fromkeys(x for x in raw if isinstance(x, str) and x in SOUNDS))
        unknown = [x for x in raw if not (isinstance(x, str) and x in SOUNDS)]
        if unknown:
            print(f"drowsycfg: {user_path()}: unknown sounds {unknown!r} dropped; the sounds are {list(SOUNDS)}",
                  file=sys.stderr)
        out["sounds"] = kept or list(defaults.get("sounds") or SOUNDS)
    return out


def _read_for_save():
    """The owner's file, to lay the app's changes over. Unlike _read, which
    shrugs a broken file off as {} for reading (the spec's numbers are used),
    a save must not: writing the app's keys over {} would silently delete
    every hand-tuned threshold in a file that has, say, a trailing comma. So
    a file that exists and is not a JSON object stops the save, and the
    message names the file and where it broke (Task 9 fix round 1, I2)."""
    path = user_path()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{path} could not be read ({e}), so nothing was saved; fix or remove it by hand")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON (line {e.lineno}, column {e.colno}: {e.msg}), "
                         "so nothing was saved; fix it by hand")
    if not isinstance(doc, dict):
        raise ValueError(f"{path} must hold a JSON object, so nothing was saved; fix it by hand")
    return doc


def save(changes):
    if not isinstance(changes, dict):
        raise ValueError("the settings must be an object")
    user = _read_for_save()
    for k, v in changes.items():
        if k == "enabled" and isinstance(v, bool):
            user[k] = v
        elif k == "sensitivity" and isinstance(v, str) and v in SENSITIVITIES:
            user[k] = v
        elif k == "name" and isinstance(v, str) and v in NAMES:
            user[k] = v
        elif k == "sounds" and isinstance(v, list) and v and all(isinstance(s, str) and s in SOUNDS for s in v):
            user[k] = list(dict.fromkeys(v))
        else:
            raise ValueError(f"{k!r} cannot be set to {v!r} from the app")
    os.makedirs(os.path.dirname(user_path()), exist_ok=True)
    tmp = user_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(user, f, indent=2)
    os.replace(tmp, user_path())
    return load()


def log_event(data):
    import records
    level, trigger = data.get("level"), data.get("trigger")
    # isinstance(True, int) holds in Python, and True == 1, so a bool is
    # refused by name rather than read as Level 1.
    if isinstance(level, bool) or level not in (1, 2, 3) or not isinstance(trigger, str):
        raise ValueError("an event needs a level of 1, 2 or 3 and a trigger")
    t = data.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not t:
        t = time.time()
    # The speed is unknown (None) or a real one; the measures an object
    # (Task 9 fix round 1). Anything else is refused, not written as-is.
    kph = data.get("speed_kph")
    if kph is not None and (isinstance(kph, bool) or not isinstance(kph, (int, float))
                            or not math.isfinite(kph) or not 0 <= kph <= MAX_KPH):
        raise ValueError(f"speed_kph must be a speed from 0 to {MAX_KPH} km/h, or null")
    measures = data.get("measures")
    if measures is None:
        measures = {}
    if not isinstance(measures, dict):
        raise ValueError("measures must be an object")
    payload = {"t": t, "level": level, "trigger": trigger, "speed_kph": kph, "measures": measures}
    return {"id": records.write_record("drowsy", f"Drowsy · Level {level} · {trigger}", payload)}


def log_dir():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_STATE_HOME", "~/.local/state")),
                        "omacar", "drowsy")


def log_measures(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    os.makedirs(log_dir(), exist_ok=True)
    path = os.path.join(log_dir(), time.strftime("%Y-%m-%d") + ".jsonl")
    kept = [r for r in rows[:600] if isinstance(r, dict)]
    with open(path, "a", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return {"written": len(kept), "file": path}
