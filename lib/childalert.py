#!/usr/bin/env python3
"""A child left in the car: when the car is switched off with the car seat
occupied, the owner is told -- on the tablet at once, then on the outbox for
the phone, then louder -- and the tablet is not allowed to sleep until the
owner answers.

WHAT DECIDES "OCCUPIED". A camera facing the child seat, watched in the page by
MediaPipe's Face Landmarker (share/js/childwatch.js), which reports here how
many faces it found and how bright the picture is. Children are often asleep,
heads turned, in a dark car, so absence of a face proves nothing. The rule is:

    occupied = seen_recently OR (seen_during_drive AND not cleared)

  * seen_recently      a face in the last SEEN_WINDOW seconds;
  * seen_during_drive  a face at ANY point since the last clear -- during the
                       drive, or since -- or the owner's "Child aboard" tap;
  * cleared            only the owner's tap, "I have my child" or "Car seat is
                       empty", after the last sighting. The camera seeing an
                       empty seat NEVER clears it: a face detector that cannot
                       see a sleeping child's face would also "see" an empty
                       seat (doc/child-alert.md, "Why the camera cannot clear").

A face seen in the first CLEAR_GRACE seconds after a clear is the child being
lifted out and does not re-arm it; one seen later does, and is a new alert.

A camera that is unplugged, dark, failing or not reporting is UNKNOWN, never
"empty": the latch from the drive stands and the alert proceeds on it.

THE LADDER, once the car is switched off (the charger off, and the car not
moving) with the seat occupied:

    t = 0     a full-screen card: "Is your child still in the car?",
              "I have my child" (clears) and "Remind me in 2 min";
    t = 30 s  notify.send("child-left", ..., "critical") to the owner's
              outbox, again every RESEND seconds until cleared;
    t = 60 s  a chime on the tablet, repeating, until answered -- THE ONE
              EXCEPTION TO OMACAR'S NO-SOUND RULE, by the owner's brief;
    heat      the cabin sensor (lib/envnode.py) at or above the threshold, or
              rising HEAT_RISE degrees in HEAT_WINDOW: a more urgent card and
              a fresh message, and another for every further 5 degrees;
    always    the ignition loop is held awake ("child alert"), and its Sleep
              now cannot override that.

It never fires while the car is on or moving, it fires once per switch-off
(again only on new evidence after a clear), and while a drive is under way
with the seat occupied the top bar shows a silent "Child seat: occupied" lamp.

    omacar child-alert status      what it sees now
    omacar child-alert run         the loop (omacar-child-alert.service)
    omacar child-alert sim [--drive] [--at S] [--hot] [--no-sensor]
                                   a simulated alert, labelled "Simulated" on
                                   the card; never sends a message
    omacar child-alert clear       "I have my child", from a shell
    omacar child-alert aboard      "Child aboard", from a shell
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
STATE = os.path.join(STATE_DIR, "child-alert.json")
CAM = os.path.join(STATE_DIR, "child-alert-cam.json")
ASK = os.path.join(STATE_DIR, "child-alert-ask.json")
CONFIG = os.path.join(CONFIG_DIR, "child-alert.json")

# The recorder's roles (lib/camstore.py ROLES). There is no fourth "toddler"
# role yet (the roadmap's safe.child-cam); until there is, the owner names the
# one that faces the seat.
CAMERAS = ("front", "rear", "cabin")

DEFAULTS = {
    "enabled": True,     # with no camera, it acts only on the "Child aboard" tap
    "chime": True,
    "heat_c": 30.0,      # cabin temperature that escalates
    "camera": None,      # which recorder role faces the child seat
}

SEEN_WINDOW = 120       # s: a face this recent is "seen recently"
CLEAR_GRACE = 20        # s after a clear: a face still in frame is the child leaving
CAM_FRESH = 10          # s: page reports older than this mean "camera unknown"
NOTIFY_AT = 30          # s after switch-off: the outbox
CHIME_AT = 60           # s after switch-off: the chime
SNOOZE = 120            # s: "Remind me in 2 min"
RESEND = 600            # s: the outbox again, while nobody has answered
HEAT_RISE = 3.0         # degrees C ...
HEAT_WINDOW = 600       # ... in this many seconds is "rising fast"
MOVING_WINDOW = 120     # s: any speed this recently, with the car answering, is moving
ANSWERING_WINDOW = 15   # s: the car's computers answered this recently
LOOP_STALE = 10         # s: the loop writes every second
ASK_STALE = 60          # s: a tap older than this is not a request
ASKS = ("clear", "snooze", "aboard")
CAM_STATES = ("watching", "dark", "no-picture", "no-engine", "error")


# ---- configuration -----------------------------------------------------------------

def config(path=None):
    cfg = dict(DEFAULTS)
    try:
        with open(path or CONFIG, encoding="utf-8") as f:
            mine = json.load(f)
    except (OSError, ValueError):
        mine = {}
    if not isinstance(mine, dict):
        mine = {}
    for k in ("enabled", "chime"):
        if isinstance(mine.get(k), bool):
            cfg[k] = mine[k]
    h = mine.get("heat_c")
    # Out of range is a typo, not a wish: the default, never the loosest value.
    if isinstance(h, (int, float)) and not isinstance(h, bool) and 25 <= h <= 40:
        cfg["heat_c"] = float(h)
    if mine.get("camera") in CAMERAS:
        cfg["camera"] = mine["camera"]
    return cfg


def set_config(changes, path=None):
    """Validated changes from the Cabin screen. Returns the new config."""
    if not isinstance(changes, dict):
        raise ValueError("expected an object")
    path = path or CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            mine = json.load(f)
        if not isinstance(mine, dict):
            mine = {}
    except (OSError, ValueError):
        mine = {}
    for k, v in changes.items():
        if k in ("enabled", "chime"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} is true or false")
            mine[k] = v
        elif k == "heat_c":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 25 <= v <= 40:
                raise ValueError("heat_c is between 25 and 40")
            mine[k] = float(v)
        elif k == "camera":
            if v is not None and v not in CAMERAS:
                raise ValueError(f"camera is one of {', '.join(CAMERAS)}, or null")
            mine[k] = v
        else:
            raise ValueError(f"no setting called {k!r}")
    _write_json(path, mine)
    return config(path)


# ---- deciding (pure) -----------------------------------------------------------------

def occupied(seen_recently, seen_during_drive, cleared):
    """THE RULE. Absence of a face is never evidence of an empty seat."""
    return bool(seen_recently or (seen_during_drive and not cleared))


def counts(seen_at, cleared_at):
    """Does a sighting at `seen_at` count, given the last clear?"""
    if seen_at is None:
        return False
    return cleared_at is None or seen_at > cleared_at + CLEAR_GRACE


def heat_of(temps, now, threshold):
    """{"t_c", "rise", "hot", "why"} from [[t, c], ...], or None with no reading.

    Hot is at or above the threshold (rising or not -- a hot car that has
    stopped warming is still a hot car), or a rise of HEAT_RISE in HEAT_WINDOW.
    """
    pts = [(t, c) for t, c in temps if now - t <= HEAT_WINDOW + 60]
    if not pts:
        return None
    t_now, c_now = pts[-1]
    window = [c for t, c in pts if now - t <= HEAT_WINDOW]
    rise = round(c_now - min(window), 1) if window else 0.0
    rising = len(pts) >= 2 and c_now > pts[0][1]
    hot_level = c_now >= threshold
    fast = rise >= HEAT_RISE
    why = None
    if hot_level and fast:
        why = "hot and rising fast"
    elif hot_level:
        why = "hot and rising" if rising else "hot"
    elif fast:
        why = "rising fast"
    return {"t_c": round(c_now, 1), "rise": rise, "rising": rising,
            "hot": bool(hot_level or fast), "why": why}


def fresh_state():
    return {"phase": "idle", "car": "unknown", "latched_at": None, "latched_by": None,
            "cleared_at": None, "last_seen": None, "off_at": None, "alert_at": None,
            "snooze_until": None, "last_moving": None, "last_answering": None,
            "sent": [], "temps": [],
            "alerts": 0}


def decide(st, power, carnow, cam, cabin, now, cfg, ask=None):
    """(new state, actions) from what is known now. Never touches anything.

    power   True (charger on: the car is on), False (off) or None (unreadable)
    carnow  lib/ignition.py car(): {"answering", "speed", "age"}
    cam     cam_now(): {"state", "last_seen", "age"}
    cabin   {"t_c": float or None, "state": "live"|"stale"|"absent"|None}
    ask     "clear", "snooze", "aboard" or None
    actions [{"notify": {kind, title, body, urgency, key}}]
    """
    st = dict(st)
    st["sent"] = list(st.get("sent") or [])
    st["temps"] = [list(p) for p in (st.get("temps") or [])]
    actions = []
    if not cfg.get("enabled", True):
        off = fresh_state()
        off["phase"] = "off"
        return off, actions

    # What the car said, remembered. MOVING is a speed in the last two minutes
    # WITH the car's computers still answering: a charger lost on the road (or
    # at a red light) is not a parking. Once they go quiet -- a second or two
    # after the ignition is switched off -- the car is parked, so the alert is
    # not held back by a speed from before the driver pulled in.
    if carnow and carnow.get("age") is not None and carnow["age"] < 15:
        if carnow.get("answering"):
            st["last_answering"] = max(st.get("last_answering") or 0, now - carnow["age"])
            if (carnow.get("speed") or 0) > 0:
                st["last_moving"] = max(st.get("last_moving") or 0, now - carnow["age"])

    # The owner's taps.
    if ask == "aboard":
        st["latched_at"], st["latched_by"] = now, "you"
    elif ask == "clear":
        st["cleared_at"] = now
        if st["phase"] in ("alert", "snoozed"):
            st["phase"] = "cleared"
        st["snooze_until"] = None
    elif ask == "snooze" and st["phase"] == "alert":
        st["phase"], st["snooze_until"] = "snoozed", now + SNOOZE

    # The camera's last sighting, if it counts, latches.
    seen = (cam or {}).get("last_seen")
    if counts(seen, st.get("cleared_at")):
        st["last_seen"] = max(st.get("last_seen") or 0, seen)
        if st.get("latched_at") is None or seen > st["latched_at"]:
            st["latched_at"], st["latched_by"] = seen, "camera"
    last = st.get("last_seen")
    seen_recently = counts(last, st.get("cleared_at")) and now - last <= SEEN_WINDOW
    latched = st.get("latched_at") is not None
    cleared = (st.get("cleared_at") is not None and latched
               and st["latched_at"] <= st["cleared_at"])
    occ = occupied(seen_recently, latched, cleared)

    answering = (st.get("last_answering") is not None
                 and now - st["last_answering"] < ANSWERING_WINDOW)
    moving = (answering and st.get("last_moving") is not None
              and now - st["last_moving"] < MOVING_WINDOW)
    if power is True:
        car = "on"
    elif power is False:
        car = "moving" if moving else "off"
    else:
        car = "unknown"
    st["car"] = car

    if car in ("on", "moving"):
        # NEVER WHILE THE CAR IS ON OR MOVING. Somebody is driving it; an alert
        # already showing ends, and the screen must not fill while it moves.
        st.update(off_at=None, alert_at=None, snooze_until=None, temps=[])
        st["phase"] = "armed" if occ else "idle"
        return st, actions
    if car == "unknown" and st["phase"] not in ("alert", "snoozed"):
        # The charger cannot be read, so a switch-off cannot be seen. An alert
        # already running keeps running (unknown is never "fine").
        st["phase"] = "armed" if occ else "idle"
        return st, actions

    # The car is off (or unreadable with an alert already running).
    if st.get("off_at") is None:
        st["off_at"] = now
    if st["phase"] not in ("alert", "snoozed"):
        if occ:
            st.update(phase="alert", alert_at=now, snooze_until=None, temps=[])
            st["alerts"] = (st.get("alerts") or 0) + 1
        else:
            st["phase"] = "cleared" if st["phase"] == "cleared" else "parked"
            return st, actions
    if st["phase"] == "snoozed" and now >= (st.get("snooze_until") or 0):
        st.update(phase="alert", snooze_until=None)

    # The cabin, remembered while the alert runs.
    if cabin and cabin.get("state") == "live" and isinstance(cabin.get("t_c"), (int, float)):
        st["temps"].append([now, float(cabin["t_c"])])
    st["temps"] = [p for p in st["temps"] if now - p[0] <= HEAT_WINDOW + 60][-720:]
    heat = heat_of(st["temps"], now, cfg["heat_c"])

    trip = f"{int(st['off_at'])}-{st['alerts']}"
    elapsed = now - st["alert_at"]
    why = _why(st, now, seen_recently)
    if elapsed >= NOTIFY_AT:
        n = int((elapsed - NOTIFY_AT) // RESEND)
        key = f"child-left-{trip}-{n}"
        if key not in st["sent"]:
            st["sent"].append(key)
            body = (f"The car was switched off {_span(now - st['off_at'])} ago and the car seat "
                    f"looks occupied ({why}).")
            if heat:
                body += f" Cabin {heat['t_c']:g} °C."
            actions.append({"notify": {"kind": "child-left", "urgency": "critical", "key": key,
                                       "title": "Is your child still in the car?", "body": body}})
    if heat and heat["hot"]:
        band = int(heat["t_c"] // 5 * 5)
        key = f"child-left-{trip}-heat-{band}"
        if key not in st["sent"]:
            st["sent"].append(key)
            actions.append({"notify": {
                "kind": "child-left", "urgency": "critical", "key": key,
                "title": "URGENT: the car is getting hot — is your child inside?",
                "body": (f"Cabin {heat['t_c']:g} °C ({heat['why']}), car off "
                         f"{_span(now - st['off_at'])}, car seat looks occupied ({why}).")}})
    st["sent"] = st["sent"][-60:]
    return st, actions


def _why(st, now, seen_recently):
    if seen_recently and st.get("last_seen") is not None:
        return f"a face seen {_ago(now - st['last_seen'])}"
    if st.get("latched_by") == "you":
        return "you said a child was aboard"
    return "a child was seen during the drive"


def _span(seconds):
    s = int(max(0, seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def _ago(seconds):
    return "just now" if seconds < 10 else _span(seconds) + " ago"


def public(st, cfg, cam, cabin, now, source="live"):
    """What the screen needs."""
    phase = st.get("phase", "idle")
    alerting = phase in ("alert", "snoozed")
    last = st.get("last_seen")
    seen_recently = counts(last, st.get("cleared_at")) and now - last <= SEEN_WINDOW
    latched = st.get("latched_at") is not None
    cleared = (st.get("cleared_at") is not None and latched
               and st["latched_at"] <= st["cleared_at"])
    occ = occupied(seen_recently, latched, cleared) if cfg.get("enabled", True) else False
    heat = heat_of(st.get("temps") or [], now, cfg["heat_c"]) if alerting else None
    sensor = bool(cabin) and cabin.get("state") in ("live", "stale")
    alert = None
    if alerting and st.get("alert_at") is not None:
        elapsed = now - st["alert_at"]
        alert = {"elapsed": round(elapsed), "off_for": round(now - (st.get("off_at") or now)),
                 "notified": elapsed >= NOTIFY_AT,
                 "notify_in": max(0, round(NOTIFY_AT - elapsed)),
                 "snoozed_left": (max(0, round(st["snooze_until"] - now))
                                  if phase == "snoozed" and st.get("snooze_until") else None)}
    return {
        "running": True, "at": now, "source": source, "enabled": cfg.get("enabled", True),
        "phase": phase, "car": st.get("car"), "occupied": occ,
        "why": _why(st, now, seen_recently) if occ else None,
        "latched_by": st.get("latched_by") if occ else None,
        "seen_ago": round(now - last) if last is not None else None,
        "camera": {"role": cfg.get("camera"), "state": (cam or {}).get("state", "none"),
                   "age": (cam or {}).get("age")},
        "alert": alert,
        "chime": bool(phase == "alert" and cfg.get("chime", True) and alert
                      and alert["elapsed"] >= CHIME_AT),
        "heat": heat, "cabin_sensor": sensor,
        "cabin_source": (cabin or {}).get("source"),
        "hold_sleep": alerting,
        "config": {k: cfg[k] for k in DEFAULTS},
        "cameras": list(CAMERAS),
    }


# ---- the files ---------------------------------------------------------------------

def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def report(data, now=None):
    """What the page's camera watcher saw (POST /api/child-alert/seen).

    {"faces": n, "state": "watching"|"dark"|..., "luma": 0-255, "camera": role}.
    The last sighting is kept across reports, so a face between two of the
    loop's reads is never lost.
    """
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        raise ValueError("expected an object")
    faces = data.get("faces")
    if isinstance(faces, bool) or not isinstance(faces, int) or not 0 <= faces <= 10:
        raise ValueError("faces is a count")
    state = data.get("state")
    if state not in CAM_STATES:
        raise ValueError(f"state is one of {', '.join(CAM_STATES)}")
    luma = data.get("luma")
    luma = round(float(luma), 1) if isinstance(luma, (int, float)) and not isinstance(luma, bool) else None
    old = _load(CAM) or {}
    rec = {"at": now, "faces": faces, "state": state, "luma": luma,
           "camera": data.get("camera") if data.get("camera") in CAMERAS else None,
           "last_seen": now if faces > 0 else old.get("last_seen")}
    _write_json(CAM, rec)
    return {"ok": True}


def cam_now(cfg, now=None):
    """The camera as the loop sees it: its state, and its last sighting."""
    now = time.time() if now is None else now
    if not cfg.get("camera"):
        rec = _load(CAM) or {}
        return {"state": "none", "last_seen": rec.get("last_seen"), "age": None}
    rec = _load(CAM)
    if not rec:
        return {"state": "no-reports", "last_seen": None, "age": None}
    age = max(0.0, now - float(rec.get("at") or 0))
    state = rec.get("state") if age <= CAM_FRESH else "no-reports"
    if rec.get("camera") and rec["camera"] != cfg["camera"]:
        state = "no-reports"     # the page is still watching the old one
    return {"state": state, "last_seen": rec.get("last_seen"), "age": round(age, 1)}


def cabin_now():
    """The cabin sensor's temperature, through lib/envnode.py's own staleness."""
    try:
        import envnode
        d = envnode.read_public()
    except Exception:            # noqa: BLE001 -- a broken sensor module is "no sensor"
        return {"t_c": None, "state": None, "source": None}
    if not d.get("present"):
        return {"t_c": None, "state": None, "source": None}
    cab = d.get("cabin") or {}
    t = (cab.get("values") or {}).get("t_c")
    return {"t_c": t if isinstance(t, (int, float)) else None,
            "state": cab.get("state"), "source": d.get("source")}


def take_ask():
    a = _load(ASK)
    try:
        os.unlink(ASK)
    except OSError:
        pass
    if not a or time.time() - float(a.get("at", 0)) > ASK_STALE:
        return None
    return a.get("ask") if a.get("ask") in ASKS else None


def ask(what):
    """What the API and the CLI call."""
    if what not in ASKS:
        raise ValueError(f"ask is one of {', '.join(ASKS)}")
    _write_json(ASK, {"ask": what, "at": time.time()})
    return {"asked": what}


def read_public(now=None):
    """For the API: the loop's last word, or that there is no loop."""
    now = time.time() if now is None else now
    doc = _load(STATE)
    if not doc or not isinstance(doc.get("pub"), dict):
        cfg = config()
        return {"running": False, "phase": "off", "enabled": cfg["enabled"],
                "config": cfg, "cameras": list(CAMERAS)}
    pub = doc["pub"]
    pub["running"] = now - float(pub.get("at", 0)) < LOOP_STALE
    if not pub["running"]:
        pub["stale_for"] = round(now - float(pub.get("at", 0)))
    pub["config"] = config()
    return pub


def ignition_hold(now=None):
    """For lib/ignition.py: a reason to stay awake, or None.

    A loop that has stopped answering mid-alert is NOT a reason to sleep: its
    last word stands until somebody taps "I have my child" (the tap is a file
    the API writes, so it is seen even with the loop down). A simulation never
    holds the real tablet.
    """
    now = time.time() if now is None else now
    doc = _load(STATE)
    pub = (doc or {}).get("pub") or {}
    if not pub.get("hold_sleep") or pub.get("source") == "sim":
        return None
    if now - float(pub.get("at", 0)) < LOOP_STALE:
        return "child alert"
    a = _load(ASK) or {}
    if a.get("ask") == "clear" and float(a.get("at", 0)) > float(pub.get("at", 0)):
        return None
    return "child alert"


# ---- the loop --------------------------------------------------------------------------

def _send(actions, sim=False):
    out = []
    for a in actions:
        n = a.get("notify")
        if not n:
            continue
        if sim:
            out.append({"would_send": n["title"], "key": n["key"]})
            continue
        try:
            import notify
            notify.send(n["kind"], n["title"], n["body"], n["urgency"], key=n["key"])
        except Exception as why:   # noqa: BLE001 -- the card and the chime carry on
            _log(f"could not queue {n['key']}: {why}")
    return out


def _log(line):
    try:
        import power
        power.note("child-alert: " + line)
    except Exception:              # noqa: BLE001
        print("child-alert:", line, file=sys.stderr)


def restore():
    """The latch survives a restart, a crash and a reboot: it is on disk."""
    doc = _load(STATE)
    if doc and doc.get("pub", {}).get("source") == "live" and isinstance(doc.get("st"), dict):
        st = fresh_state()
        st.update(doc["st"])
        return st
    return fresh_state()


def run(tick=1.0):
    import ignition
    st = restore()
    _log(f"loop started ({st['phase']})")
    while True:
        cfg = config()
        now = time.time()
        power = ignition.supplies()["powered"]
        carnow = ignition.car(now=now)
        cam = cam_now(cfg, now)
        cabin = cabin_now()
        was = st["phase"]
        st, actions = decide(st, power, carnow, cam, cabin, now, cfg, take_ask())
        if st["phase"] != was:
            _log(f"{was} -> {st['phase']} (car {st['car']})")
        _send(actions)
        _write_json(STATE, {"pub": public(st, cfg, cam, cabin, now), "st": st})
        time.sleep(tick)


def sim(argv):
    """A scripted world through the real decide(): a drive with a child seen,
    then the car switched off. Never sends a message, never holds the tablet."""
    doc = _load(STATE)
    if doc and doc.get("pub", {}).get("source") == "live" and \
            time.time() - float(doc["pub"].get("at", 0)) < LOOP_STALE:
        print("  the real child-alert loop is running here; stop it first", file=sys.stderr)
        return 1
    drive_only = "--drive" in argv
    hot = "--hot" in argv
    no_sensor = "--no-sensor" in argv
    at = float(argv[argv.index("--at") + 1]) if "--at" in argv else 0.0
    base = config()
    cfg = dict(base, enabled=True, camera=base["camera"] or "rear")
    drive_s = 20.0
    t0 = time.time() - drive_s - at       # the simulated drive started this long ago
    print("  SIMULATED: a child seen during a drive, then "
          + ("still driving" if drive_only else f"the car switched off {int(at)} s ago")
          + ". Nothing is sent; Ctrl-C ends it.")

    def world(v):
        on = drive_only or v < drive_s
        off_for = max(0.0, v - drive_s)
        carnow = {"answering": on, "speed": 40.0 if on else 0.0, "age": 0.5}
        cam = {"state": "watching", "last_seen": t0 + 5 if v >= 5 else None, "age": 0.5}
        start = 31.0 if hot else 27.0
        cabin = None if no_sensor else {
            "t_c": round(start + off_for / 60 * (0.9 if hot else 0.3), 1),
            "state": "live", "source": "sim"}
        return on, carnow, cam, cabin

    st, sent, v = fresh_state(), [], 0.0
    while True:
        live = t0 + v >= time.time() - 1
        on, carnow, cam, cabin = world(v)
        st, actions = decide(st, on, carnow, cam, cabin, t0 + v, cfg, take_ask() if live else None)
        sent += _send(actions, sim=True)
        if not live:
            v = min(v + 2.0, time.time() - t0)      # catching up to now
            continue
        pub = public(st, cfg, cam, cabin, t0 + v, source="sim")
        pub["sim_sent"] = sent[-5:]
        _write_json(STATE, {"pub": pub, "st": st})
        time.sleep(1.0)
        v = time.time() - t0


def status():
    pub = read_public()
    cfg = config()
    print()
    print(f"  loop       {'running' if pub.get('running') else 'not running'}"
          + (" (SIMULATED)" if pub.get("source") == "sim" else ""))
    print(f"  enabled    {'yes' if cfg['enabled'] else 'no'}, chime "
          f"{'on' if cfg['chime'] else 'off'}, heat at {cfg['heat_c']:g} °C")
    print(f"  camera     {cfg['camera'] or 'none chosen (only the Child aboard tap arms it)'}"
          + (f", {pub['camera']['state']}" if pub.get("camera") else ""))
    print(f"  phase      {pub.get('phase')}  car {pub.get('car') or 'unknown'}")
    print(f"  seat       {'OCCUPIED — ' + pub['why'] if pub.get('occupied') else 'nothing seen since the last clear'}")
    if pub.get("alert"):
        a = pub["alert"]
        print(f"  alert      {a['elapsed']} s, outbox {'sent' if a['notified'] else 'in ' + str(a['notify_in']) + ' s'}")
    print(f"  sleep      {'held awake' if pub.get('hold_sleep') else 'not held'}")
    print()
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        return status()
    if what == "run":
        run()
        return 0
    if what == "sim":
        try:
            return sim(argv[1:])
        except KeyboardInterrupt:
            return 0
    if what in ("clear", "aboard", "snooze"):
        ask(what)
        print(f"  asked: {what}")
        return 0
    print(f"  omacar child-alert: no such thing as '{what}'.\n"
          "  status | run | sim | clear | aboard | snooze", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
