#!/usr/bin/env python3
"""Where did I park? What the car saves in the half minute before the tablet sleeps.

WHEN. The tablet's charger is the ignition signal (lib/ignition.py). When the
charger goes off with the car stopped, this has until the tablet sleeps (30 s)
to save one park, and it does it a few seconds in, once the car's computers
have gone quiet:

  * the last good GPS fix (lib/location.py), with its age -- or nothing. A
    park with no fix says "no fix"; a position is never made up;
  * one still from the rear camera and one from the front. Taken from the live
    picture the recorder already writes for the Cameras screen (cams.live_path):
    a file read, so the recorder is never stopped, slowed or asked for anything.
    If that picture is stale, the last picture in the newest clip (ffmpeg,
    read-only, a few seconds at most) -- and if that fails, no still;
  * the outdoor pressure (lib/envnode.py, when its node is live), set against
    the pressure the car had on the street a few minutes earlier, for an
    ESTIMATE of how many levels up or down it is parked in a garage.

The last ten parks are kept in the state dir (parks.jsonl, parks/<id>-*.jpg).
Older ones, and their photographs, are deleted. Locations stay on the tablet:
the phone's notification carries the time and "photo saved" and nothing else
(lib/notify.py), and a paired client asks /api/parking/last for the rest.

THE LEVEL, SAID HONESTLY. Pressure falls about 1.2 hPa per 10 m. The hypsometric
form  h = 44330 * (1 - (p / p_ref) ** 0.1903)  turns two pressures into a height
difference, and a level is taken as 3 m. A sensor is good to about +-0.1 hPa
between two readings minutes apart (1 m), and the weather moves too, so this is
"about", never "exactly": it is labelled an estimate wherever it is shown, it
claims a level only beyond 1.8 m, and with no street reference it says nothing.

    omacar parked status     the last park
    omacar parked list       the last ten
    omacar parked run        the loop (omacar-parkspot.service)
    omacar parked sim [n]    n simulated parks (fake fix, generated stills),
                             labelled Simulated everywhere they are shown
    omacar parked forget     delete every park and its photographs
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")

KEEP = 10                # parks kept
ROLES = ("front", "rear")
SETTLE_S = 4             # the charger has been off this long before we look
DEADLINE_S = 24          # past this the tablet is about to sleep: too late, skip
MIN_GAP_S = 300          # one park per five minutes at most
FIX_MAX_AGE_S = 120      # a fix older than this is not "where I parked"
FRESH_FRAME_S = 10       # a live picture older than this is not "now"
MOVING_CLEARS_S = 0      # speed seen after the charger went off
FLOOR_M = 3.0            # one level
GROUND_M = 1.8           # less than this above or below the street: street level
REF_KEEP_S = 25 * 60     # moving pressure samples kept
REF_SKIP_RECENT_S = 90   # the last minute and a half may already be the ramp
REF_MIN_SAMPLES = 3
REF_SAMPLE_EVERY_S = 10
REF_MIN_KPH = 15.0


def paths():
    return {"parks": os.path.join(STATE_DIR, "parks.jsonl"),
            "dir": os.path.join(STATE_DIR, "parks"),
            "ref": os.path.join(STATE_DIR, "parkspot-ref.json")}


# ---- deciding (pure) --------------------------------------------------------------

def fresh_state():
    return {"was_on": False, "off_since": None, "done": False, "last_capture": None,
            "last_moving": None}


def decide_capture(st, power, carnow, now):
    """(new state, action) -- action is None or "capture". Never touches anything.

    One park per time the charger goes off, taken SETTLE_S in, and:
      * never from a charger that was not seen ON first (a boot with the
        charger already off is not a parking);
      * never when the car was moving after the charger went off (a cable out
        on the road is a fault, not a park -- the same call lib/ignition.py makes);
      * never when the tablet is about to sleep (past DEADLINE_S);
      * never twice within MIN_GAP_S;
      * never from a charger this cannot read.
    """
    st = dict(st)
    if carnow.get("age") is not None and carnow["age"] < 15 and (carnow.get("speed") or 0) > 0:
        st["last_moving"] = max(st.get("last_moving") or 0, now - carnow["age"])
    if power.get("powered") is None:
        return st, None
    if power["powered"]:
        st.update(was_on=True, off_since=None, done=False)
        return st, None
    # The charger is off.
    if not st.get("was_on") or st.get("done"):
        return st, None
    if st.get("off_since") is None:
        st["off_since"] = now
    moved = st.get("last_moving") is not None and st["last_moving"] >= st["off_since"] - MOVING_CLEARS_S
    if moved:
        st["done"] = True            # a fault on the road: not this time round
        return st, None
    waited = now - st["off_since"]
    if waited < SETTLE_S:
        return st, None
    if waited > DEADLINE_S:
        st["done"] = True            # too late; the tablet is going to sleep
        return st, None
    last = st.get("last_capture")
    if last is not None and now - last < MIN_GAP_S:
        st["done"] = True
        return st, None
    st.update(done=True, last_capture=now)
    return st, "capture"


# ---- the level (pure) -----------------------------------------------------------------

def height_delta_m(p_ref, p):
    """Metres above (positive) or below the place where the pressure was p_ref.
    The hypsometric approximation; None for pressures that cannot be real."""
    try:
        p_ref, p = float(p_ref), float(p)
    except (TypeError, ValueError):
        return None
    if not (300.0 <= p_ref <= 1100.0 and 300.0 <= p <= 1100.0):
        return None
    return 44330.0 * (1.0 - (p / p_ref) ** 0.1903)


def level_estimate(p_ref, p, floor_m=FLOOR_M, ground_m=GROUND_M):
    """An estimate of the level, or None when either pressure is missing.

    {"estimate": True, "delta_m", "levels": int (signed), "label", "detail"}
    label is "street level" or "P3"; detail says up or down. Always an estimate.
    """
    d = height_delta_m(p_ref, p)
    if d is None:
        return None
    if abs(d) < ground_m:
        levels = 0
    else:
        levels = int(round(d / floor_m)) or (1 if d > 0 else -1)
    if levels == 0:
        label, detail = "street level", "about the same height as the street"
    else:
        n = abs(levels)
        label = f"P{n}"
        detail = f"about {n} level{'s' if n > 1 else ''} {'above' if levels > 0 else 'below'} the street"
    return {"estimate": True, "delta_m": round(d, 1), "levels": levels, "label": label,
            "detail": detail}


def street_reference(samples, now):
    """The pressure at street level: the median of the moving samples from
    REF_KEEP_S ago to REF_SKIP_RECENT_S ago (the last minute and a half may be
    the ramp into a garage). None with fewer than REF_MIN_SAMPLES."""
    pts = [p for t, p in samples
           if REF_SKIP_RECENT_S <= now - t <= REF_KEEP_S and 300.0 <= p <= 1100.0]
    if len(pts) < REF_MIN_SAMPLES:
        return None
    return statistics.median(pts)


# ---- the park's words (pure) -----------------------------------------------------------

def notify_text(at, has_photo):
    """What goes to the phone. The time and a fact about the photos: NO place, no
    coordinates, no address -- the phone fetches those from /api/parking/last."""
    when = time.strftime("%H:%M", time.localtime(at))
    return f"Parked at {when}" + (" · photo saved" if has_photo else "")


def pick_fix(loc, now):
    """The fix to save from lib/location.py's read_public(), or None.

    A fix right now, else the newest point in the short history if it is not
    older than FIX_MAX_AGE_S. {"lat","lon","accuracy_m","at","age_s","source"}.
    """
    if not isinstance(loc, dict):
        return None
    acc = loc.get("accuracy_m")
    acc = float(acc) if isinstance(acc, (int, float)) else None
    if loc.get("fix") and isinstance(loc.get("lat"), (int, float)) and isinstance(loc.get("lon"), (int, float)):
        return {"lat": round(loc["lat"], 6), "lon": round(loc["lon"], 6), "accuracy_m": acc,
                "at": round(now, 1), "age_s": 0, "source": loc.get("source")}
    rec = loc.get("recent") or []
    if rec:
        t, lat, lon = rec[-1][0], rec[-1][1], rec[-1][2]
        if 0 <= now - t <= FIX_MAX_AGE_S:
            return {"lat": round(lat, 6), "lon": round(lon, 6), "accuracy_m": acc,
                    "at": round(t, 1), "age_s": round(now - t), "source": loc.get("source")}
    return None


# ---- storing --------------------------------------------------------------------------

def _write_atomic(path, data, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        f.write(data)
    os.replace(tmp, path)


def load_parks():
    """Newest last, at most KEEP of them."""
    try:
        with open(paths()["parks"], encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), int)][-KEEP:]


def photo_file(pid, role):
    return os.path.join(paths()["dir"], f"{int(pid)}-{role}.jpg")


def save_park(rec):
    """Append one park, keep the last KEEP, delete the photographs of the rest."""
    rows = load_parks()
    rec = dict(rec, id=(rows[-1]["id"] + 1) if rows else 1)
    rows.append(rec)
    gone, rows = rows[:-KEEP], rows[-KEEP:]
    _write_atomic(paths()["parks"], "".join(json.dumps(r) + "\n" for r in rows))
    for g in gone:
        for role in ROLES:
            try:
                os.unlink(photo_file(g["id"], role))
            except OSError:
                pass
    return rec


def forget():
    for p in (paths()["parks"],):
        try:
            os.unlink(p)
        except OSError:
            pass
    shutil.rmtree(paths()["dir"], ignore_errors=True)


def view(rec):
    """A park as the API gives it: photo URLs for the photographs that exist."""
    out = dict(rec)
    out["photos"] = {r: f"/api/parking/photo/{rec['id']}/{r}" for r in ROLES
                     if rec.get(r) and os.path.isfile(photo_file(rec["id"], r))}
    return out


def read_public():
    """For the API: the last park and the history, newest first. Location stays
    on this tablet; this is served only to a paired, tokened client."""
    rows = load_parks()
    if not rows:
        return {"present": False, "last": None, "history": []}
    parks = [view(r) for r in reversed(rows)]
    return {"present": True, "last": parks[0], "history": parks}


def photo_path(pid, role):
    """The JPEG for /api/parking/photo/<id>/<role>, or None. Only an id this
    file has recorded and a known role ever become a path."""
    if role not in ROLES or not re.fullmatch(r"[0-9]{1,9}", str(pid)):
        return None
    if not any(r["id"] == int(pid) and r.get(role) for r in load_parks()):
        return None
    p = photo_file(pid, role)
    return p if os.path.isfile(p) and not os.path.islink(p) else None


# ---- the stills --------------------------------------------------------------------------

def _is_jpeg(b):
    return len(b) > 200 and b[:2] == b"\xff\xd8" and b[-2:] == b"\xff\xd9"


def still_from_live(role, dest, now, live_path=None):
    """Copy the recorder's own latest picture. A file read: nothing is asked of
    the recorder. {"ok", "how", "age_s"}."""
    if live_path is None:
        import cams
        live_path = cams.live_path(role)
    try:
        age = now - os.stat(live_path).st_mtime
        with open(live_path, "rb") as f:
            data = f.read()
    except OSError:
        return {"ok": False, "how": "no live picture"}
    if age > FRESH_FRAME_S:
        return {"ok": False, "how": f"live picture {round(age)} s old"}
    if not _is_jpeg(data):
        return {"ok": False, "how": "live picture unreadable"}
    _write_atomic(dest, data, "wb")
    return {"ok": True, "how": "live frame", "age_s": round(age, 1)}


def still_from_clip(role, dest, now, clips=None):
    """The last picture in the newest clip, read-only (ffmpeg -sseof on a file
    the recorder is writing is a read). Bounded to 8 s."""
    import camstore
    rows = [c for c in (clips if clips is not None else camstore.scan())
            if c["role"] == role and not c.get("locked")]
    if not rows:
        return {"ok": False, "how": "no clip"}
    clip = rows[-1]
    if now - clip["start"] > 15 * 60:
        return {"ok": False, "how": "newest clip is old"}
    tmp = dest + ".tmp.jpg"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                            "-y", "-sseof", "-2", "-i", clip["path"], "-frames:v", "1", "-q:v", "3", tmp],
                           capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "how": "ffmpeg failed"}
    try:
        with open(tmp, "rb") as f:
            data = f.read()
        os.unlink(tmp)
    except OSError:
        data = b""
    if r.returncode != 0 or not _is_jpeg(data):
        return {"ok": False, "how": "no picture in the clip"}
    _write_atomic(dest, data, "wb")
    return {"ok": True, "how": "clip's last frame", "age_s": None}


def take_still(role, dest, now):
    got = still_from_live(role, dest, now)
    if got["ok"]:
        return got
    return still_from_clip(role, dest, now)


# ---- reading the world ----------------------------------------------------------------

def outdoor_pressure(now=None):
    """(hPa, source) from the outdoor node while it is live, else (None, None)."""
    try:
        import envnode
        e = envnode.read_public(now)
    except Exception:                                      # noqa: BLE001
        return None, None
    side = (e or {}).get("outside") or {}
    v = (side.get("values") or {}).get("hpa")
    if side.get("state") == "live" and isinstance(v, (int, float)):
        return float(v), e.get("source")
    return None, None


def load_ref():
    try:
        with open(paths()["ref"], encoding="utf-8") as f:
            doc = json.load(f)
        return [(float(t), float(p)) for t, p in doc.get("samples", [])]
    except (OSError, ValueError, TypeError):
        return []


def note_ref(samples, now, hpa, kph):
    """Add a street-pressure sample while the car is moving. Pure on its inputs."""
    out = [(t, p) for t, p in samples if now - t <= REF_KEEP_S]
    if hpa is None or kph is None or kph < REF_MIN_KPH:
        return out
    if out and now - out[-1][0] < REF_SAMPLE_EVERY_S:
        return out
    out.append((round(now, 1), float(hpa)))
    return out


# ---- the capture ------------------------------------------------------------------------

def capture(now=None, *, send=None, location=None, pressure=None, stills=None, samples=None):
    """Save one park, tell the phone. Every outside reading is injectable for the
    tests. Returns the saved record.

    The phone's text has no place in it (notify_text). `send` is notify.send.
    """
    now = time.time() if now is None else now
    if location is None:
        import location as L
        location = L.read_public(now)
    fix = pick_fix(location, now)
    hpa, psrc = pressure if pressure is not None else outdoor_pressure(now)
    ref = street_reference(samples if samples is not None else load_ref(), now)
    level = level_estimate(ref, hpa) if (ref is not None and hpa is not None) else None
    pid = (load_parks()[-1]["id"] + 1) if load_parks() else 1
    os.makedirs(paths()["dir"], exist_ok=True)
    took, notes = {}, {}
    for role in ROLES:
        got = (stills or take_still)(role, photo_file(pid, role), now)
        took[role] = bool(got["ok"])
        notes[role] = got["how"]
    simulated = bool((fix and fix.get("source") == "sim") or psrc == "sim")
    rec = save_park({"at": round(now, 1), "fix": fix, "front": took["front"], "rear": took["rear"],
                     "stills": notes, "pressure_hpa": hpa, "street_hpa": round(ref, 2) if ref else None,
                     "level": level, "simulated": simulated})
    if send is None:
        import notify
        send = notify.send
    send("parked", "Parked", notify_text(now, took["front"] or took["rear"]), "info",
         key=f"parked-{rec['id']}", now=now)
    return rec


# ---- the loop ---------------------------------------------------------------------------

def run(tick=1.0):
    import ignition
    st = fresh_state()
    samples = load_ref()
    last_save = 0.0
    while True:
        now = time.time()
        pw, car = ignition.supplies(), ignition.car(now=now)
        # The street pressure, kept while the car is moving and the node is live.
        if car.get("answering") and (car.get("speed") or 0) >= REF_MIN_KPH and now - last_save >= REF_SAMPLE_EVERY_S:
            hpa, _ = outdoor_pressure(now)
            new = note_ref(samples, now, hpa, car.get("speed"))
            if new != samples:
                samples = new
                _write_atomic(paths()["ref"], json.dumps({"samples": samples}))
            last_save = now
        st, action = decide_capture(st, pw, car, now)
        if action == "capture":
            try:
                rec = capture(now, samples=samples)
                print(f"parked: saved #{rec['id']} at {time.strftime('%H:%M:%S')}", flush=True)
            except Exception as e:                             # noqa: BLE001
                print(f"parked: could not save a park: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        time.sleep(tick)


# ---- the simulator ------------------------------------------------------------------------

def _sim_still(dest, label, hue, w=640, h=360):
    """A generated picture. ffmpeg's test source when there is ffmpeg (the tablet
    always has it), else a small embedded JPEG."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x203040:c1=0x{hue}:x0=0:y0=0:x1={w}:y1={h}:d=1",
                            "-frames:v", "1", "-q:v", "4", dest],
                           capture_output=True, timeout=15)
        if r.returncode == 0 and os.path.getsize(dest) > 500:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def sim(n=3, base=None):
    """n simulated parks, oldest first, the newest a few minutes ago. A fake fix
    and generated pictures, marked simulated=True (the screens write the word)."""
    base = time.time() if base is None else base
    plan = [  # (minutes ago, lat, lon, levels or None, fix?)
        (60 * 26, 37.7955, -122.3937, None, True),
        (60 * 5, 37.7749, -122.4194, -3, True),
        (4, 37.7842, -122.4079, -3, True),
    ][-n:]
    last = None
    for mins, lat, lon, lv, has_fix in plan:
        at = base - mins * 60
        pid = (load_parks()[-1]["id"] + 1) if load_parks() else 1
        ok = {}
        for role, hue in (("front", "6a8fb0"), ("rear", "a07860")):
            ok[role] = _sim_still(photo_file(pid, role), role, hue)
        street = 1012.4
        hpa = round(street * ((1 - (lv * FLOOR_M) / 44330.0) ** (1 / 0.1903)), 2) if lv else None
        level = level_estimate(street, hpa) if hpa else None
        last = save_park({
            "at": round(at, 1),
            "fix": {"lat": lat, "lon": lon, "accuracy_m": 6.0, "at": round(at, 1), "age_s": 0,
                    "source": "sim"} if has_fix else None,
            "front": ok["front"], "rear": ok["rear"],
            "stills": {"front": "simulated", "rear": "simulated"},
            "pressure_hpa": hpa, "street_hpa": street if hpa else None, "level": level,
            "simulated": True})
    return last


# ---- the CLI ----------------------------------------------------------------------------

def _line(r):
    t = time.strftime("%a %H:%M", time.localtime(r["at"]))
    where = "no GPS fix" if not r.get("fix") else f"{r['fix']['lat']:.5f}, {r['fix']['lon']:.5f}"
    lv = r["level"]["label"] + " (estimate)" if r.get("level") else "level unknown"
    ph = "+".join(x for x in ROLES if r.get(x)) or "no photo"
    return f"  #{r['id']:<3} {t}  {where}  {lv}  {ph}{'  [Simulated]' if r.get('simulated') else ''}"


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    rows = load_parks()
    if what == "status":
        print(_line(rows[-1]) if rows else "  no park saved yet")
        return 0
    if what == "list":
        print("\n".join(_line(r) for r in reversed(rows)) or "  no park saved yet")
        return 0
    if what == "run":
        run()
        return 0
    if what == "sim":
        n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 3
        rec = sim(max(1, min(3, n)))
        print("  simulated parks written (labelled Simulated):")
        print(_line(rec))
        return 0
    if what == "forget":
        forget()
        print("  every park and its photographs deleted")
        return 0
    print("  omacar parked: status | list | run | sim [n] | forget", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
