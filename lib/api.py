"""The loopback API behind the OmaCar workshop.

Everything the app knows comes through here, and everything here comes out of
`records.py`, so the browser, the CLI and the advisor can never disagree about
what the car said.

    GET  /api/snapshot          the whole car in one read
    GET  /api/live              the current sample
    GET  /api/adapter           what there is to connect to, and what is running
    POST /api/daemon            start or stop the process that reads the car
    GET  /api/history           samples over a span, decimated to fit a graph
    GET  /api/service-history   what has actually been done, newest first
    GET  /api/documents         the document library for this vehicle
    POST /api/document          add, update, remove or parse one
    GET  /api/trips             drives, newest first, for replay
    GET  /api/records           saved scans, recordings and advisor answers
    GET  /api/ai                poll an advisor job
    GET  /api/ai/history
    GET  /api/theme             the active palette, mapped to our roles
    GET  /api/themes            themes you have built, and which is active
    POST /api/themes            save, delete or select one
    GET  /api/fonts             the font stacks on offer, and which is worn
    POST /api/fonts             select one, or save your own
    GET  /api/concerns          what is trending somewhere it should not
    GET  /api/snapshots         states captured by hand or by the watchdog
    GET  /api/vehicles          the garage
    GET  /api/photos            photographs, filed against codes and concerns
    POST /api/photo             add, annotate or remove one
    POST /api/snapshot          freeze the state now
    POST /api/vehicle           switch to another car, or name one
    GET  /api/drive             the drive-mode layout
    POST /api/drive             change it
    POST /api/actuate           command an actuator, or stop one
    POST /api/units             switch between imperial and metric
    POST /api/odometer          set the reading (there is no odometer PID)
    POST /api/service           log, add or forget a maintenance item
    POST /api/scan              run a full-system scan and file the report
    POST /api/clear             clear codes in one module or all of them
    POST /api/record            save a stretch of samples as a recording
    POST /api/ai                start an advisor job

Advisor calls take a minute or so, which is far too long to hold a request
open while a spinner lies about progress. They are jobs: POST starts one and
returns an id, GET polls it. The worker is a plain thread — there is exactly
one user and they are on the same machine.
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import book     # noqa: E402
import concerns  # noqa: E402
import fonts    # noqa: E402
import photos   # noqa: E402
import garage   # noqa: E402
import records  # noqa: E402
import share    # noqa: E402
import theme    # noqa: E402
import themes   # noqa: E402

try:
    import ai
except Exception:                                   # pragma: no cover
    ai = None


# ---- advisor jobs -----------------------------------------------------------

_JOBS = {}
_JOBS_LOCK = threading.Lock()
# A job is finished work nobody has collected yet, so they are cheap to keep
# and pointless to keep forever.
JOB_TTL = 3600


def _sweep():
    now = time.time()
    with _JOBS_LOCK:
        for k in [k for k, v in _JOBS.items()
                  if v.get("done_at") and now - v["done_at"] > JOB_TTL]:
            _JOBS.pop(k, None)


def start_job(kind, question=None, code=None, refresh=False, span=None):
    if ai is None or not ai.available():
        raise RuntimeError(
            "the advisor needs the `claude` CLI, which is not installed. "
            "Everything else in OmaCar works without it.")
    _sweep()
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "question": question, "code": code,
           "span": span, "state": "running", "started": time.time(),
           "result": None, "error": None, "done_at": None}
    with _JOBS_LOCK:
        _JOBS[jid] = job

    def work():
        try:
            out = ai.ask(kind, question=question, code=code, refresh=refresh,
                         span=span)
            job["result"] = out
            job["state"] = "done"
        except Exception as e:                       # noqa: BLE001
            job["error"] = str(e)[:600]
            job["state"] = "error"
        finally:
            job["done_at"] = time.time()

    threading.Thread(target=work, daemon=True).start()
    return job


def job_state(jid):
    with _JOBS_LOCK:
        job = _JOBS.get(jid)
    if not job:
        return {"state": "unknown"}
    out = dict(job)
    out["elapsed"] = round(time.time() - job["started"], 1)
    return out


# ---- the scan ---------------------------------------------------------------

def surveyed_at():
    """Unix seconds of the last completed survey, or 0.

    Read straight from the vehicle table rather than from snapshot(), which
    does not carry it -- and the scan report needs it to say how old a stale
    result is.
    """
    try:
        db = records.connect()
        row = db.execute(
            "SELECT v FROM vehicle WHERE k='surveyed_at'").fetchone()
        return json.loads(row[0]) if row else 0
    except Exception:                                         # noqa: BLE001
        return 0


def request_survey(timeout=12.0):
    """Ask the running daemon to read the car NOW, and wait for it.

    Returns (fresh, why): fresh is True only if the car was actually re-read.

    The daemon holds the serial port, so this is a handoff rather than a read:
    drop the request file, wait for the daemon to claim it, then wait for the
    surveyed_at stamp to move. Both waits are bounded -- a scan button that
    hangs is no better than one that lies.
    """
    import connect as _connect
    req = os.path.join(_connect.STATE, "survey-now")
    pid = os.path.join(_connect.STATE, "daemon.pid")

    if not os.path.exists(pid):
        return False, "the daemon is not running, so nothing is reading the car"

    before = surveyed_at()
    try:
        with open(req, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError as e:
        return False, f"could not ask the daemon: {e}"

    deadline = time.time() + timeout
    claimed = False
    while time.time() < deadline:
        time.sleep(0.25)
        if not claimed and not os.path.exists(req):
            claimed = True          # daemon took the request; now it is working
        if claimed and surveyed_at() > before:
            return True, ""
    # Clean up an unclaimed request so it cannot fire later out of nowhere.
    if not claimed:
        try:
            os.remove(req)
        except OSError:
            pass
        return False, "the daemon did not pick up the request"
    return False, "the car did not answer in time"


def scan(live=True):
    """A full-system code scan, filed in the record book.

    On a real car this walks every module the adapter can address. Here it
    reads what the modules are holding — and the report marks which of them a
    plain OBD-II adapter could genuinely have reached, because a scan report
    that quietly implies it read the airbag module over a generic protocol is
    the sort of thing that gets people hurt.

    `live` asks the daemon for a fresh read first. The report carries `fresh`
    and `stale_reason` so the app can say which it is showing: a scan tool that
    hands back yesterday's answer without saying so is the same failure as one
    that invents an answer.
    """
    fresh, why = (request_survey() if live else (False, "not requested"))
    s = records.snapshot()
    by_module = {}
    for f in s["faults"]:
        mid = (f.get("module") or {}).get("id") or "PGM-FI"
        by_module.setdefault(mid, []).append(f)

    report_modules = []
    for m in s["modules"]:
        codes = by_module.get(m["id"], [])
        active = [c for c in codes if c["active"]]
        report_modules.append({
            "id": m["id"], "name": m["name"], "system": m["system"],
            "addr": m["addr"], "generic": m["generic"],
            "part": m.get("part"), "sw": m.get("sw"),
            "codes": codes,
            "active": len(active),
            "worst": ("critical" if any(c["severity"] == "critical" for c in active)
                      else "warning" if any(c["severity"] == "warning" for c in active)
                      else "normal" if active else "clean"),
        })

    report = {
        "at": int(time.time()),
        "vehicle": s["name"],
        "vin": s["vehicle"].get("vin"),
        "odometer": s["odometer"],
        "units": s["units"],
        "simulated": s["simulated"],
        "modules": report_modules,
        "totals": {
            "modules": len(report_modules),
            "with_codes": sum(1 for m in report_modules if m["active"]),
            "codes": sum(m["active"] for m in report_modules),
            "cleared": sum(1 for f in s["faults"] if not f["active"]),
        },
        "readiness": s["readiness"],
        "mode06": s["mode06"],
        "mode06_failed": [m["mid"] for m in s["mode06"] if m["pass"] is False],
        "service_due": (s["service"] or {}).get("due", 0),
        "service_next": (s["service"] or {}).get("next"),
        # Whether this report is a fresh read of the car or the last one on
        # file. The app shows the difference; a scan tool that hands back an
        # old answer without saying so is the same failure as one that invents
        # an answer.
        "fresh": fresh,
        "stale_reason": ("" if fresh else why),
        "surveyed_at": surveyed_at() or None,
    }
    rid = records.write_record(
        "scan",
        f"{report['totals']['codes']} code(s) across "
        f"{report['totals']['with_codes']} module(s)",
        {"totals": report["totals"],
         "modules": [{"id": m["id"], "active": m["active"], "worst": m["worst"]}
                     for m in report_modules],
         "ready": s["readiness"]["ready"]},
        odo=s["odometer"])
    report["record_id"] = rid
    return report


def record_clear_locally(module=None):
    """Bring our records into line AFTER the car has actually been cleared.

    THIS DOES NOT TALK TO THE CAR, AND ONCE PRETENDED IT DID.

    It was called `clear_codes`, its docstring said "Mode 04", and it was wired
    straight to the app's Clear button -- but every line of it writes to our own
    SQLite and nothing was ever sent over the wire. Pressing Clear made the
    codes disappear from the screen, returned a success payload, and left the
    fault set in the car with the lamp still lit. The codes then "came back" on
    the next survey, which reads as an intermittent fault rather than as a
    button that did nothing.

    That is the same failure as a progress bar over cached results, and it is
    worse, because a person can conclude their car is fixed.

    It is now only reachable from the /api/clear handler, and only after the
    module has confirmed the real clear. Clearing does not fix anything and it
    resets every readiness monitor, so the returned payload still says exactly
    what was lost.
    """
    db = sqlite3.connect(records.DB, timeout=5.0)
    try:
        db.row_factory = sqlite3.Row
        if not records.has(db, "faults"):
            return {"cleared": 0, "monitors_reset": 0}
        mods = {m["id"]: m for m in records.modules(db)}
        codes = []
        for r in db.execute("SELECT code, status FROM faults"):
            if r["status"] not in ("stored", "pending", "permanent"):
                continue
            if module:
                owner = next((m["id"] for m in mods.values()
                              if r["code"] in m["codes"]), None)
                if owner != module:
                    continue
            # Permanent codes are permanent on purpose: they exist so a car
            # cannot be presented for an emissions test with the codes wiped
            # on the way there. Mode 04 does not touch them and neither do we.
            if r["status"] == "permanent":
                continue
            codes.append(r["code"])
        now = time.time()
        for c in codes:
            db.execute("UPDATE faults SET status='cleared', last_seen=? "
                       "WHERE code=?", (now, c))
        reset = 0
        if records.has(db, "readiness"):
            cur = db.execute(
                "UPDATE readiness SET complete=0 WHERE kind='trip' AND supported=1")
            reset = cur.rowcount
        db.commit()
    finally:
        db.close()
    records.write_record("clear", f"cleared {len(codes)} code(s)",
                         {"codes": codes, "module": module,
                          "monitors_reset": reset})
    return {"cleared": len(codes), "codes": codes, "monitors_reset": reset}


# ---- the drive layout ------------------------------------------------------
#
# Kept on the server rather than in the browser's storage, so the layout you
# arranged on the workstation is the layout the tablet in the car shows. A
# cockpit display is read-only, which means it inherits the arrangement rather
# than being able to change it — which is the right way round: you set this up
# in the kitchen, not at seventy miles an hour.

# Config lives under XDG_CONFIG_HOME, exactly as state lives under
# XDG_STATE_HOME. Hardcoding ~/.config meant demo mode could redirect every
# byte of state and still overwrite the drive layout and themes of the car you
# actually drive.
DRIVE_CFG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")),
    "omarchy", "omacar-drive.json")

DEFAULT_DRIVE = {
    "hero": "speed",
    "tiles": ["econ_now", "coolant", "volts"],
    "columns": 3,
    "footer": "trip",
    # When the app should take itself to drive mode on its own. On a tablet
    # bolted to a dashboard this is the difference between a diagnostic tool
    # and an instrument cluster: you get in, the adapter wakes up, and the
    # screen is already the one you want without touching it.
    #   off      never
    #   connect  the moment the adapter answers
    #   moving   the moment the car is actually rolling
    "auto": "connect",
    # ...and back to the workshop when the link drops, so a car you have just
    # parked and unplugged leaves you looking at the codes rather than at a
    # dead gauge.
    "auto_return": True,
    # How each readout is drawn: {tile id: kind}. Absent means "digital", so a
    # layout written before gauges existed loads unchanged and looks the same.
    "kinds": {},
    "heroKind": "digital",
}
AUTO_MODES = ("off", "connect", "moving")

# Kept in step with KINDS in share/js/gauges.js. Validated here rather than
# trusted, for the same reason the tile count is: this file can be hand-edited,
# and an unknown kind would reach the browser and render nothing at all in a
# moving car. Anything unrecognised silently becomes a number, which is the one
# rendering every readout can wear.
GAUGE_KINDS = ("digital", "dial", "arc", "bar")


def _clean_kinds(value):
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items()
            if isinstance(k, str) and v in GAUGE_KINDS and v != "digital"}


def drive_layout():
    try:
        with open(DRIVE_CFG) as f:
            saved = json.load(f) or {}
    except (OSError, ValueError):
        saved = {}
    out = dict(DEFAULT_DRIVE)
    out.update({k: v for k, v in saved.items() if not k.startswith("_")})
    # Bound it here rather than trusting whatever wrote the file. A layout with
    # forty tiles is not a layout, and one with none is a blank screen in a
    # moving car.
    tiles = [t for t in (out.get("tiles") or []) if isinstance(t, str)][:8]
    out["tiles"] = tiles or list(DEFAULT_DRIVE["tiles"])
    try:
        out["columns"] = max(1, min(4, int(out.get("columns", 3))))
    except (TypeError, ValueError):
        out["columns"] = 3
    if out.get("auto") not in AUTO_MODES:
        out["auto"] = DEFAULT_DRIVE["auto"]
    out["auto_return"] = bool(out.get("auto_return", True))
    # A kind for a tile that is no longer on the screen is dead weight in the
    # file and would come back the moment the tile did, which is not what
    # removing it meant.
    out["kinds"] = {k: v for k, v in _clean_kinds(out.get("kinds")).items()
                    if k in out["tiles"]}
    if out.get("heroKind") not in GAUGE_KINDS:
        out["heroKind"] = "digital"
    return out


def save_drive_layout(data):
    cur = drive_layout()
    for key in ("hero", "footer"):
        if isinstance(data.get(key), str):
            cur[key] = data[key]
    if data.get("auto") in AUTO_MODES:
        cur["auto"] = data["auto"]
    if isinstance(data.get("auto_return"), bool):
        cur["auto_return"] = data["auto_return"]
    if isinstance(data.get("tiles"), list):
        cur["tiles"] = [t for t in data["tiles"] if isinstance(t, str)][:8]
    if data.get("columns") is not None:
        try:
            cur["columns"] = max(1, min(4, int(data["columns"])))
        except (TypeError, ValueError):
            pass
    if "kinds" in data:
        cur["kinds"] = _clean_kinds(data.get("kinds"))
    if data.get("heroKind") in GAUGE_KINDS:
        cur["heroKind"] = data["heroKind"]
    cur["_comment"] = ("Drive-mode layout. Edit here or in the app: OmaCar → "
                       "Drive → Customise. Tile ids are listed in "
                       "share/js/views/drive.js; `kinds` maps a tile id to "
                       "one of " + ", ".join(GAUGE_KINDS) + ".")
    os.makedirs(os.path.dirname(DRIVE_CFG), exist_ok=True)
    tmp = DRIVE_CFG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f, indent=2)
    os.replace(tmp, DRIVE_CFG)
    return drive_layout()


# ---- bidirectional --------------------------------------------------------

COMMAND = os.path.join(records.STATE, "command.json")

# What each test is allowed to do, and what it must not. The duration cap is
# the important column: a cooling fan commanded on and then forgotten is a flat
# battery, and an injector held off is a catalytic converter full of fuel.
LIMITS = {
    # Not an actuator: it holds the engine at idle so a test has a baseline
    # taken under the same conditions as the measurement. On a real car this
    # is a technician leaving it running; here the simulator has to be told.
    "hold_idle":     {"max": 180, "idle": True},
    "fan_low":       {"max": 60, "idle": False},
    "fan_high":      {"max": 60, "idle": False},
    "ac_clutch":     {"max": 30, "idle": True},
    "evap_purge":    {"max": 20, "idle": True},
    "egr_open":      {"max": 15, "idle": True},
    "fuel_pump":     {"max": 10, "idle": False},
    "injector_kill_1": {"max": 8, "idle": True},
    "injector_kill_2": {"max": 8, "idle": True},
    "injector_kill_3": {"max": 8, "idle": True},
    "injector_kill_4": {"max": 8, "idle": True},
}


def actuate(test, duration=None, stop=False):
    """Command an actuator, or stop whatever is running.

    Every test is capped in this process rather than trusted to the caller, and
    the command carries its own expiry so a crashed app cannot leave a fan on.
    """
    os.makedirs(records.STATE, exist_ok=True)
    if stop or not test:
        try:
            os.remove(COMMAND)
        except OSError:
            pass
        return {"stopped": True}
    lim = LIMITS.get(test)
    if not lim:
        raise ValueError(f"unknown test {test!r}")
    secs = min(lim["max"], max(1, int(duration or lim["max"])))
    cmd = {
        "id": uuid.uuid4().hex[:8],
        "test": test,
        "at": time.time(),
        "duration": secs,
        "idle": lim["idle"],
    }
    tmp = COMMAND + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cmd, f)
    os.replace(tmp, COMMAND)
    records.write_record("test", f"actuator: {test}", {"test": test, "seconds": secs})
    return cmd


def save_recording(label, t0, t1):
    db = records.connect()
    series = records.samples(db, since=t0, until=t1, limit=100000) if db else []
    st = records.stats(series) if series else {}
    if db:
        db.close()
    rid = records.write_record("movie", label or "Recording",
                               {"rows": len(series), "channels": st},
                               t0=t0, t1=t1)
    return {"id": rid, "rows": len(series), "channels": st}


# ---- the daemon -------------------------------------------------------------
#
# WHY THE APP IS ALLOWED TO START A PROCESS.
#
# Until this existed, an app that could not see a daemon told the person to
# open a terminal and type `omacar daemon start` — an instruction printed by a
# program perfectly capable of doing the thing itself. The bar plugin has had a
# start button for months; the workshop had a sentence. This is that button's
# other half.
#
# It shells `bin/omacar daemon start` rather than importing daemon.py and
# forking, because that script is where starting a daemon is DEFINED: it finds
# the venv, refuses a second one, redirects the log and detaches with setsid. A
# second implementation of all that in here would be a second thing to keep
# right, and they would drift.
#
# NOTHING FROM THE REQUEST REACHES THE COMMAND LINE. The argument vector is a
# fixed pair of literals; the body chooses between "start" and "stop" and
# contributes nothing else. There is deliberately no port, path or device
# argument: the daemon resolves the adapter itself through connect.resolve(),
# so such an argument would buy nothing and would put a caller's string into a
# subprocess.

def _omacar(*args, timeout=30):
    """Run the project's own CLI with a fixed argument vector."""
    import subprocess
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    return subprocess.run([os.path.join(root, "bin", "omacar"), *args],
                          timeout=timeout, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)


def _last_line(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _networked():
    """True when this API is answering something other than loopback.

    api.py cannot import serve.py — serve.py imports us — so the flag is read
    off the module object serve.py already keeps it on, which is __main__
    whenever serve.py is the script being run, and it always is.

    The fallback is the safe reading of "could not tell": loopback, which is
    what every caller that is not the server (the CLI, the tests) genuinely is.
    A cockpit sets that flag on itself before it answers its first request, so
    it can never be the thing this fails to recognise.
    """
    for name in ("serve", "__main__"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "LOOPBACK_ONLY"):
            return not mod.LOOPBACK_ONLY
    return False


def adapter():
    """What OmaCar can see to connect to, in one read.

    The point of this endpoint is the label. The app used to offer "not
    connected — plug in the adapter", which is what you write when you do not
    know what is there; naming the device that IS there turns a sentence into a
    button somebody can believe.
    """
    import connect as _connect
    import hotplug
    port, kind = _connect.resolve()
    # `name` is the noun the button says out loud and `label` is the whole
    # phrase, so a caller can either compose its own sentence or take ours.
    # Neither invents a make: this reports what resolve() found, and "FTDI" or
    # "OBDLink" would be a guess dressed as a reading.
    name = {"bench": "bench emulator", "override": "port override"}.get(
        kind, "adapter" if port else None)
    label = f"{name} on {port}" if port else "no adapter"
    return {
        "port": port, "kind": kind, "name": name, "label": label,
        # connect.serial_group_warning() is the one sentence that fixes the
        # commonest hard failure on Arch — the user not being in the serial
        # group — and until now it was only ever printed to a terminal that
        # nobody who needed it was looking at.
        "warning": _connect.serial_group_warning(port),
        "running": hotplug.daemon_running(),
        "simulator": hotplug.sim_running(),
    }


def _daemon_state(action, changed, note):
    out = adapter()
    out.update({"action": action, "changed": changed, "note": note})
    return out


def daemon_control(action):
    """Start or stop the gauge daemon. (status, payload), as the routes want.

    Every refusal names the one thing that would fix it, because this is the
    first button a new person presses and a "could not start" with no reason is
    exactly the experience the button exists to remove.
    """
    import hotplug
    if action not in ("start", "stop"):
        return 400, {"error": "action must be start or stop"}
    if _networked():
        # A cockpit is a screen for a car, not a console for the machine in the
        # workshop. It may look at the gauge; it may not start processes on
        # somebody else's laptop. (serve.py refuses every write from a cockpit
        # anyway unless it was started with --control, which is a promise about
        # commanding the CAR — this keeps that promise from stretching.)
        return 403, {"error": "the daemon is controlled from the machine it "
                              "runs on, not from a cockpit display"}

    if action == "stop":
        if not hotplug.daemon_running():
            return 200, _daemon_state("stop", False, "the daemon was not running")
        try:
            proc = _omacar("daemon", "stop")
        except Exception as e:                                # noqa: BLE001
            return 500, {"error": f"could not stop the daemon: {e}"}
        # Asked and answered separately: the CLI's exit code says it sent the
        # signal, and only the pidfile says the daemon acted on it.
        if hotplug.daemon_running():
            return 500, {"error": "could not stop the daemon: "
                                  + (_last_line(proc.stderr) or "it is still running")}
        return 200, _daemon_state("stop", True, "the daemon has stopped")

    if hotplug.daemon_running():
        # Not an error: two people, or two tabs, pressing Connect is a normal
        # thing to happen, and the honest answer is that it is already running.
        return 200, _daemon_state("start", False, "the daemon was already running")
    if hotplug.sim_running():
        # The simulator writes live.json five times a second and so does the
        # daemon. Two writers do not merge; the gauge flickers between two
        # cars. `omacar sim start` has always refused while the daemon is up,
        # and this is the other direction of the same rule.
        return 409, {"error": "the simulator is running — stop it first "
                              "(omacar sim stop)"}
    info = adapter()
    if not info["port"]:
        return 409, {"error": "no adapter — plug one in, "
                              "or run: omacar bench start"}
    if info["warning"]:
        return 409, {"error": info["warning"]}
    try:
        proc = _omacar("daemon", "start")
    except Exception as e:                                    # noqa: BLE001
        return 500, {"error": f"could not start the daemon: {e}"}
    if not hotplug.daemon_running():
        # bin/omacar tails the daemon's own log onto stderr when it gives up,
        # so the last line is the specific complaint rather than our guess.
        why = _last_line(proc.stderr)
        return 500, {"error": "the daemon did not start" + (f": {why}" if why else "")}
    # It is RUNNING, which is not the same as connected: it may be sitting in
    # front of a car whose ignition is off, waiting, which is now a state it
    # survives. Whoever pressed the button watches /api/live for that.
    return 200, _daemon_state(
        "start", True,
        "the daemon is running — the gauges come up as soon as the car answers")


# ---- routing ----------------------------------------------------------------

def qint(query, name, default, lo=None, hi=None):
    for part in query.split("&"):
        if part.startswith(name + "="):
            try:
                v = int(float(part[len(name) + 1:]))
            except ValueError:
                return default
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            return v
    return default


def qstr(query, name, default=None):
    from urllib.parse import unquote_plus
    for part in query.split("&"):
        if part.startswith(name + "="):
            return unquote_plus(part[len(name) + 1:])
    return default


def handle_get(path, query):
    """(status, payload) or None when it is not ours."""
    if path == "/api/snapshot":
        return 200, records.snapshot()
    if path == "/api/live":
        return 200, records.live()
    if path == "/api/history":
        db = records.connect()
        t0 = qstr(query, "from")
        t1 = qstr(query, "to")
        n = qint(query, "n", 900, 1, 20000)
        if t0:
            series = records.samples(db, since=float(t0),
                                     until=float(t1) if t1 else None, limit=n)
        else:
            mins = qint(query, "mins", 20, 1, 60 * 24 * 14)
            series = records.samples(db, since=time.time() - mins * 60, limit=n)
        if db:
            db.close()
        return 200, {"rows": series, "cols": records.SAMPLE_COLS}
    if path == "/api/trips":
        db = records.connect()
        out = records.trips(db, qint(query, "n", 20, 1, 200)) if db else []
        if db:
            db.close()
        return 200, {"trips": out}
    if path == "/api/records":
        db = records.connect()
        out = records.records(db, kind=qstr(query, "kind"),
                              n=qint(query, "n", 50, 1, 500)) if db else []
        if db:
            db.close()
        return 200, {"records": out}
    if path == "/api/ai":
        jid = qstr(query, "job")
        if not jid:
            return 400, {"error": "job id required"}
        return 200, job_state(jid)
    if path == "/api/ai/history":
        return 200, {"records": ai.history() if ai else []}
    if path == "/api/ai/available":
        return 200, {"available": bool(ai and ai.available())}
    if path == "/api/drive":
        return 200, drive_layout()
    if path == "/api/concerns":
        return 200, {"concerns": concerns.assess()}
    if path == "/api/snapshots":
        return 200, {"snapshots": concerns.snapshots(
            qint(query, "n", 40, 1, 200))}
    if path == "/api/photos":
        return 200, {"photos": photos.listing(
            subject=qstr(query, "subject"), subject_id=qstr(query, "id"))}
    if path == "/api/vehicles":
        return 200, {"vehicles": garage.vehicles(), "current": garage.current()}
    if path == "/api/plugins":
        import plugins
        # Projected rather than handed over whole. discover() carries each
        # plugin's absolute directory and its entire manifest, which is right
        # for the CLI that prints it and wrong here: the directory sits under
        # the user's home, so the reply names them and the shape of their
        # machine, and the manifest is the plugin author's file rather than
        # anything the app reads. What a plugin IS, not where it lives.
        keep = ("id", "ok", "problem", "name", "description", "version",
                "author", "provides")
        return 200, {"plugins": [{k: p.get(k) for k in keep}
                                 for p in plugins.discover()],
                     "views": plugins.views(),
                     "hooks": plugins.HOOKS}
    if path == "/api/service-history":
        import history
        return 200, {"timeline": history.timeline(), "items": history.book_items()}
    if path == "/api/documents":
        import docs
        return 200, {"documents": docs.listing(qstr(query, "kind") or None),
                     "kinds": docs.KINDS, "totals": docs.totals()}
    if path == "/api/procedures":
        # Owner procedures need no adapter, no arming and no car present --
        # they are instructions, not requests. So this endpoint deliberately
        # has none of the guards the write endpoints carry.
        import ops
        car = records.snapshot() or {}
        make = car.get("make") or (car.get("vehicle") or {}).get("make")
        return 200, {"procedures": ops.load_procedures(make=make), "make": make}
    if path == "/api/resets":
        import ops
        import write as writelib
        # The make comes from the VIN decode the survey already did. Filtering
        # matters: offering a Honda-specific routine on a BMW would send that
        # routine id to a module that does something else entirely with it.
        car = records.snapshot() or {}
        make = car.get("make") or (car.get("vehicle") or {}).get("make")
        defs = ops.load_resets()
        return 200, {"resets": ops.applicable(defs, make=make),
                     "write_armed": writelib.is_armed()}
    if path == "/api/learned":
        import discover
        import write as writelib
        return 200, {"car": discover.summary(),
                     "write_armed": writelib.is_armed(),
                     "write_until": writelib.armed_until()}
    if path == "/api/ima":
        # Imported here rather than at module scope for the same reason
        # discover is: this handler runs under the system interpreter, and
        # ima.py deliberately touches no serial library at all. summary() is
        # contracted never to raise -- it returns a summary carrying an "error"
        # key rather than letting a hand-edited drive log become a 500.
        import ima
        return 200, ima.summary()
    if path == "/api/adapter":
        return 200, adapter()
    if path == "/api/theme":
        # The mtime rides along so the app can re-apply on a theme change
        # without re-parsing anything it already has.
        #
        # One endpoint whichever kind of theme is active: the browser asks what
        # to wear and is told, and does not have to know that a theme can now
        # come from two places.
        src, stamp = themes.active()
        if src is not None:
            return 200, {"stamp": stamp, "custom": True,
                         "vars": theme.palette_of(src)}
        try:
            stamp = int(os.path.getmtime(theme.THEME))
        except OSError:
            stamp = 0
        return 200, {"stamp": stamp, "custom": False, "vars": theme.palette()}
    if path == "/api/themes":
        store = themes.load()
        # Each theme ships with the palette it derives to, so the manager can
        # show a true swatch of a theme it is not wearing. Deriving it in the
        # browser would mean a second copy of the contrast rules.
        out = []
        for tid, body in sorted(store["themes"].items(),
                                key=lambda kv: kv[1]["name"].lower()):
            out.append({"id": tid, **body, "palette": theme.palette_of(body)})
        return 200, {
            "active": store["active"],
            "desktop": themes.DESKTOP,
            "themes": out,
            "seed": themes.SEED,
            "colours": list(themes.COLOURS),
            # What the desktop's own theme derives to, for the swatch beside
            # the "follow Omarchy" option.
            "desktop_palette": theme.palette(),
        }
    if path == "/api/fonts":
        # Re-stamped on every read rather than only on a save. `omacar
        # panel-cache` rewrites the panel's rollup wholesale from
        # records.snapshot(), which drops the fonts key -- so this is what puts
        # it back, on the next occasion anything asks. It is a compare before a
        # write, so a page open on a timer is not rewriting the file.
        fonts.stamp_panel_cache()
        return 200, fonts.catalogue()
    return None



# ------------------------------------------------------------------- the mode
#
# Asked FIRST and answered on the server, so the tier is a boundary rather than
# a preference the browser is trusted to honour. Every check that already
# existed still runs after this one, so a bug here fails closed to the
# behaviour the tool had before modes existed.
def _mode_gate(action, ctx=None):
    """None if allowed; a (status, payload) tuple to return if not."""
    try:
        import modes
    except ImportError:                                       # pragma: no cover
        return None                                           # fail open to the old gates
    d = modes.decide(action, ctx=ctx)
    if d.ok:
        return None
    payload = d.asdict()
    payload["error"] = d.text()
    return 403, payload


def handle_post(path, body):
    try:
        data = json.loads(body or "{}")
    except ValueError:
        data = {}
    if path == "/api/daemon":
        return daemon_control(str(data.get("action") or "").strip().lower())
    if path == "/api/scan":
        return 200, scan()
    if path == "/api/record":
        try:
            return 200, save_recording(data.get("label"),
                                       float(data["from"]), float(data["to"]))
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "from and to (epoch seconds) required"}
    if path == "/api/drive":
        return 200, save_drive_layout(data)
    if path == "/api/themes":
        what = data.get("action")
        if what == "preview":
            # Derive without storing, so the editor can show a true palette
            # while you drag a colour picker. The alternative was deriving in
            # the browser, which means a second copy of every contrast rule --
            # and the first time one was tuned the preview would start lying.
            body = themes._clean("preview", data.get("theme") or {})
            if not body:
                return 400, {"error": "that is not a theme"}
            return 200, {"palette": theme.palette_of(body)}
        if what == "select":
            store, err = themes.select(str(data.get("id") or themes.DESKTOP))
        elif what == "delete":
            store, err = themes.remove(str(data.get("id") or ""))
        elif what == "save":
            store, err = themes.put(str(data.get("id") or ""), data.get("theme") or {})
        else:
            return 400, {"error": "action must be save, delete or select"}
        if err:
            return 400, {"error": err}
        out = [{"id": tid, **body, "palette": theme.palette_of(body)}
               for tid, body in sorted(store["themes"].items(),
                                       key=lambda kv: kv[1]["name"].lower())]
        return 200, {"active": store["active"], "desktop": themes.DESKTOP,
                     "themes": out, "seed": themes.SEED,
                     "colours": list(themes.COLOURS),
                     "desktop_palette": theme.palette()}
    if path == "/api/fonts":
        what = data.get("action")
        if what == "select":
            store, err = fonts.select(str(data.get("id") or fonts.DEFAULT))
        elif what == "custom":
            store, err = fonts.put_custom(data.get("stack") or {})
        else:
            return 400, {"error": "action must be select or custom"}
        if err:
            return 400, {"error": err}
        return 200, fonts.catalogue()
    if path == "/api/snapshot":
        return 200, concerns.capture(
            reason=data.get("reason") or "manual",
            label=data.get("label"), note=data.get("note"))
    if path == "/api/photo":
        act = data.get("action") or "add"
        if act == "remove":
            return 200, {"removed": photos.remove(data.get("id") or "")}
        if act == "annotate":
            return 200, {"ok": photos.annotate(
                data.get("id") or "", note=data.get("note"),
                subject=data.get("subject"), subject_id=data.get("subject_id"),
                tags=data.get("tags"))}
        try:
            return 200, photos.add(
                data.get("image") or "", subject=data.get("subject") or "general",
                subject_id=data.get("subject_id") or "",
                note=data.get("note") or "", tags=data.get("tags"))
        except ValueError as e:
            return 400, {"error": str(e)}
    if path == "/api/learn":
        refused = _mode_gate("learn")
        if refused:
            return refused
        # A learning pass takes tens of seconds and holds the port. Run it
        # inline rather than in a thread: two concurrent passes would fight
        # over the same adapter, and the lease would make the loser look like
        # a hardware fault.
        import discover
        try:
            discover.learn(deep=bool(data.get("deep")))
        except RuntimeError as e:
            return 409, {"error": str(e)}
        except Exception as e:                                    # noqa: BLE001
            return 500, {"error": f"{type(e).__name__}: {e}"}
        return 200, {"car": discover.summary()}
    if path == "/api/clear":
        refused = _mode_gate("clear_codes")
        if refused:
            return refused
        # Clearing needs the adapter to itself, so it takes the same lease
        # every other one-off command takes. The gauge pauses for a second or
        # two and resumes; it does not look like a disconnection.
        import connect
        import ops
        import elm as elmlib
        import write as writelib
        # Arming is checked before the port is touched. It is the cheapest
        # check and the most actionable message, and there is no reason to take
        # the lease off the gauge daemon for a request that cannot proceed.
        if not writelib.is_armed():
            return 409, {"error": "write mode is not armed — run: omacar write arm"}
        port, _kind = connect.resolve()
        if not port:
            return 409, {"error": "no adapter"}
        if not connect.request_port(port):
            return 409, {"error": "the daemon is holding the port"}
        try:
            el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
            el.init()
            try:
                ops.preflight(el)
                headers = data.get("headers") or []
                result = ops.clear_codes(el, headers)
            finally:
                el.close()
        except ops.Refused as e:
            return 409, {"error": str(e)}
        except Exception as e:                                    # noqa: BLE001
            return 500, {"error": f"{type(e).__name__}: {e}"}
        finally:
            connect.release_port()

        # Only now touch our own records, and only if the car agreed. A refusal
        # from the module must leave our view of the faults exactly as it was,
        # or the app goes back to claiming a clear that did not happen.
        gen = (result.get("generic") or {}).get("kind")
        mods = [m.get("kind") for m in (result.get("modules") or {}).values()]
        accepted = gen == "positive" or any(k == "positive" for k in mods)
        local = record_clear_locally(module=data.get("module")) if accepted else None

        try:
            request_survey()
        except Exception:                                         # noqa: BLE001
            pass
        return 200, {"sent": result, "accepted": accepted, "local": local}
    if path == "/api/document":
        import docs
        act = data.get("action") or "add"
        try:
            if act == "remove":
                return 200, {"removed": docs.remove(data.get("id"))}
            if act == "update":
                return 200, {"ok": docs.update(data.get("id"), **{
                    k: data.get(k) for k in
                    ("kind", "title", "vendor", "doc_date", "amount",
                     "odometer", "note", "tags") if k in data})}
            if act == "propose":
                import history
                d = docs.get(data.get("id"))
                if not d:
                    return 404, {"error": "no such document"}
                return 200, {"proposals": history.propose(d),
                             "logged": history.already_logged(d["id"])}
            if act == "log":
                import history
                return 200, {"written": history.apply(data.get("entries") or [])}
            if act == "withdraw":
                import history
                return 200, {"ok": history.withdraw(data.get("entry_id"))}
            if act == "parse":
                got, err = docs.parse(data.get("id"))
                if err:
                    return 409, {"error": err}
                return 200, {"extracted": got, "document": docs.get(data.get("id"))}
            # add: the file arrives as a data URL, same as photographs.
            raw = data.get("file") or ""
            if "," in raw and raw.startswith("data:"):
                import base64
                raw = base64.b64decode(raw.split(",", 1)[1])
            else:
                return 400, {"error": "a file is required"}
            return 200, docs.add(
                raw, kind=data.get("kind") or "other",
                title=data.get("title") or "", vendor=data.get("vendor") or "",
                doc_date=data.get("doc_date") or "",
                amount=data.get("amount"), odometer=data.get("odometer"),
                note=data.get("note") or "", tags=data.get("tags"),
                filename=data.get("filename") or "")
        except ValueError as e:
            return 400, {"error": str(e)}
        except Exception as e:                                # noqa: BLE001
            return 500, {"error": f"{type(e).__name__}: {e}"}
    if path == "/api/reset":
        refused = _mode_gate("routine")
        if refused:
            return refused
        import connect
        import ops
        import elm as elmlib
        import write as writelib
        if not writelib.is_armed():
            return 409, {"error": "write mode is not armed — run: omacar write arm"}
        defs = ops.load_resets()
        spec = defs.get(data.get("id") or "")
        if not spec:
            return 404, {"error": "no such reset definition"}
        if data.get("header"):
            spec = dict(spec, header=data["header"])
        port, _kind = connect.resolve()
        if not port:
            return 409, {"error": "no adapter"}
        if not connect.request_port(port):
            return 409, {"error": "the daemon is holding the port"}
        try:
            el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
            el.init()
            try:
                ops.preflight(el)
                steps = ops.run_reset(el, spec)
            finally:
                el.close()
        except ops.Refused as e:
            return 409, {"error": str(e)}
        except Exception as e:                                    # noqa: BLE001
            return 500, {"error": f"{type(e).__name__}: {e}"}
        finally:
            connect.release_port()
        return 200, {"steps": steps, "reset": spec.get("name")}
    if path == "/api/write-mode":
        import write as writelib
        if data.get("arm"):
            writelib.arm(float(data.get("minutes") or 15) * 60.0)
        else:
            writelib.disarm()
        return 200, {"write_armed": writelib.is_armed(),
                     "write_until": writelib.armed_until()}
    if path == "/api/vehicle":
        key = data.get("key")
        # Editing fields and switching cars are different intents and must not
        # be confused. An edit names a field; only a bare {key} switches. The
        # earlier version treated "no name given" as "switch to this car",
        # so clearing a car's name silently made it the active vehicle.
        edits = {f: data[f] for f in garage.META_FIELDS if f in data}
        if key and edits:
            for field, value in edits.items():
                garage.set_meta(key, field, value)
        elif key:
            garage.set_current(key)
            records.refresh_db()
        return 200, {"current": garage.current(),
                     "vehicles": garage.vehicles()}
    if path == "/api/odometer":
        try:
            km = float(data["km"])
        except (KeyError, TypeError, ValueError):
            return 400, {"error": "km required"}
        book.set_odometer(km)
        return 200, {"odometer": book.odometer()[0]}
    if path == "/api/service":
        act = data.get("action")
        if act == "log":
            done = book.log_service(data.get("item") or "")
            if not done:
                return 404, {"error": "no such item"}
            return 200, {"logged": done}
        if act == "add":
            return 200, {"added": book.add_item(
                data.get("item") or "Item",
                interval_km=float(data.get("interval_km") or 0),
                interval_days=int(data.get("interval_days") or 0),
                note=data.get("note") or "")}
        if act == "forget":
            return 200, {"removed": book.forget_item(data.get("item") or "")}
        if act == "start":
            db = book.open_db()
            try:
                n = book.ensure_schedule(db)
            finally:
                db.close()
            return 200, {"started": n}
        return 400, {"error": "action must be log, add, forget or start"}
    if path == "/api/units":
        want = str(data.get("system") or "").lower()
        if want not in records.UNITS:
            return 400, {"error": "units must be imperial or metric"}
        # One setting, in one file, read by the app, the CLI and the dock card,
        # so those three can never disagree about what a mile is.
        path_cfg = records.CONFIG
        try:
            with open(path_cfg) as f:
                cfg = json.load(f) or {}
        except (OSError, ValueError):
            cfg = {}
        cfg["units"] = want
        cfg.setdefault("_comment",
                       "OmaCar's record stays metric because OBD-II is metric "
                       "on the wire. This only decides how it is shown.")
        os.makedirs(os.path.dirname(path_cfg), exist_ok=True)
        tmp = path_cfg + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path_cfg)
        return 200, {"units": records.units_for()}
    if path == "/api/actuate":
        refused = _mode_gate("actuate")
        if refused:
            return refused
        try:
            return 200, actuate(data.get("test"), data.get("duration"),
                                bool(data.get("stop")))
        except ValueError as e:
            return 400, {"error": str(e)}
    if path == "/api/ai":
        try:
            span = data.get("span")
            if span and len(span) == 2:
                span = (float(span[0]), float(span[1]))
            else:
                span = None
            # A DTC has a known shape, and `code` arrives from the URL hash
            # (#advisor/code:...) straight into a prompt template. The advisor
            # has no tools and this is loopback-only, so the worst case is a
            # steered answer rather than an action -- but an unconstrained
            # string reaching a prompt is worth constraining when the value it
            # is meant to hold is five characters of hex.
            _code = (data.get("code") or "").strip().upper()
            if _code and not re.fullmatch(r"[PBCU][0-9A-F]{4}(-[0-9A-F]{2})?", _code):
                return 400, {"error": f"{_code!r} is not a diagnostic trouble code"}
            job = start_job(data.get("kind", "triage"),
                            question=data.get("question"),
                            code=_code or None,
                            refresh=bool(data.get("refresh")),
                            span=span)
        except Exception as e:                       # noqa: BLE001
            return 503, {"error": str(e)}
        return 200, {"id": job["id"], "state": job["state"]}
    return None
