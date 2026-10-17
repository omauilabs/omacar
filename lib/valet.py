#!/usr/bin/env python3
"""Valet mode: hand the car over, keep what is yours, and learn how it was driven.

WHAT THE OWNER ASKED FOR (2026-10-17). One tap hides everything personal in the
app (Work, the advisor's history, the garage, drive history, documents, the
report, road-camera pins), locks every setting and every editor, and keeps a
small log of how the car was driven while it was out of sight: the top speed,
how long it spent above 80 mph, how far it went, how many times it was braked
hard, and when. NO LOCATION is recorded, here or anywhere it could be read
back. Turning it off needs a four-digit PIN, set the first time.

THREE FILES, ONE WRITER EACH, so nothing here ever merges two processes' writes:

  valet.json        (config dir, mode 600)  the PIN hash and the lockout. The
                    API process writes it.
  valet-state.json  (state dir)  on/off, when, and the last report. The API
                    process writes it.
  valet-log.json    (state dir)  the running log. The recorder loop (`omacar
                    valet run`, omacar-valet.service) writes it, once a second,
                    only while valet is on.

THE PIN IS A DETERRENT, NOT A VAULT. It stops a valet or a friend's child from
opening Settings; it does not stop someone with a shell on the tablet, who
already has everything. It is stored salted and slow-hashed (scrypt) so that a
copy of the file does not hand over four digits in a microsecond, which is the
most a four-digit secret can honestly be given. "No PIN" is offered, with a
warning, because a PIN the owner forgets locks them out of their own data.

THE LOG TELLS THE TRUTH ABOUT ITSELF. If the recorder was not running while
valet was on, the report says "no log" rather than printing zeros; a simulated
trace says Simulated on its face.

    omacar valet status    what the app sees
    omacar valet log       the running log, or the last report
    omacar valet on        turn it on (parked only; asks for a PIN the first time)
    omacar valet off       turn it off (asks for the PIN)
    omacar valet run       the recorder (what omacar-valet.service runs)
"""

import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
CONFIG = os.path.join(CONFIG_DIR, "valet.json")
STATE = os.path.join(STATE_DIR, "valet-state.json")
LOG = os.path.join(STATE_DIR, "valet-log.json")
LIVE = os.path.join(STATE_DIR, "live.json")

MAX_TRIES = 5
COOLDOWN = 300            # s of lockout after MAX_TRIES wrong PINs
PARKED_KMH = 3            # the app's own line for "moving" (store.state)
STALE = 15                # s: live.json older than this is not news
LOG_STALE = 10            # s: a log not written this recently is not recording
START_GRACE = 8           # s: the recorder is given this long to pick valet up

# ---- the log's arithmetic (pure: tested on synthetic traces) -------------------

FAST_KMH = 80 * 1.609344          # "above 80 mph", kept in the car's own unit
HARD_BRAKE = 3.5                  # m/s^2, about 0.36 g: a stop you feel in your chest
HARD_BRAKE_REARM = 1.5            # an event ends when braking eases below this
BRAKE_MIN_KMH = 15                # braking from a crawl is not an event
BRAKE_WINDOW = (0.6, 1.6)         # s: decel is measured over this much history,
                                  # because whole-km/h speed at 5 Hz is far too
                                  # noisy to difference sample to sample
MAX_GAP = 5.0                     # s: a longer silence is a gap, not driving


class Log:
    """Accumulates one valet session from a stream of (time, km/h, odometer)."""

    def __init__(self, started, simulated=False):
        self.started = started
        self.simulated = bool(simulated)
        self.last_t = None
        self.last_v = None
        self.top = 0.0
        self.fast_s = 0.0
        self.integ_km = 0.0
        self.odo0 = None
        self.odo1 = None
        self.brakes = 0
        self.samples = 0
        self._win = collections.deque()
        self._braking = False

    def feed(self, t, speed_kmh, odo_km=None):
        if speed_kmh is None or t is None:
            return
        v = max(0.0, float(speed_kmh))
        if self.last_t is not None and t <= self.last_t:
            return                                  # a repeat of the same sample
        self.samples += 1
        self.top = max(self.top, v)
        if odo_km is not None:
            if self.odo0 is None:
                self.odo0 = float(odo_km)
            self.odo1 = float(odo_km)
        if self.last_t is not None:
            dt = t - self.last_t
            if dt <= MAX_GAP:
                self.integ_km += (v + self.last_v) / 2 * dt / 3600.0
                if v > FAST_KMH and self.last_v > FAST_KMH:
                    self.fast_s += dt
                elif v > FAST_KMH or self.last_v > FAST_KMH:
                    self.fast_s += dt / 2           # crossed the line inside this step
            else:
                self._win.clear()
                self._braking = False
        self.last_t, self.last_v = t, v
        self._brake(t, v)

    def _brake(self, t, v):
        win = self._win
        win.append((t, v))
        while win and t - win[0][0] > BRAKE_WINDOW[1]:
            win.popleft()
        # The newest sample at least BRAKE_WINDOW[0] back is the reference.
        ref = None
        for ts, vs in win:
            if t - ts >= BRAKE_WINDOW[0]:
                ref = (ts, vs)
        if ref is None:
            return
        decel = (ref[1] - v) / 3.6 / (t - ref[0])
        if self._braking:
            if decel < HARD_BRAKE_REARM:
                self._braking = False
        elif decel >= HARD_BRAKE and ref[1] >= BRAKE_MIN_KMH:
            self._braking = True
            self.brakes += 1

    def distance(self):
        """(km, basis). The odometer's change when it moved, else the speed's integral."""
        if self.odo0 is not None and self.odo1 is not None and self.odo1 - self.odo0 > 0:
            return self.odo1 - self.odo0, "odometer"
        return self.integ_km, "speed"

    def snapshot(self, now=None):
        km, basis = self.distance()
        end = self.last_t if now is None else now
        return {"started": self.started, "ended": end,
                "top_kmh": round(self.top, 1), "fast_s": round(self.fast_s),
                "distance_km": round(km, 2), "distance_basis": basis,
                "hard_brakes": self.brakes, "samples": self.samples,
                "simulated": self.simulated}


# ---- the PIN (pure apart from the file) -----------------------------------------

_PIN = re.compile(r"^\d{4}$")


def valid_pin(pin):
    return isinstance(pin, str) and bool(_PIN.match(pin))


def hash_pin(pin, salt=None):
    salt = salt if salt is not None else secrets.token_bytes(16)
    h = hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return {"algo": "scrypt-16384-8-1", "salt": salt.hex(), "hash": h.hex()}


def check_pin(pin, rec):
    if not valid_pin(pin) or not rec or rec.get("algo") != "scrypt-16384-8-1":
        return False
    want = hash_pin(pin, bytes.fromhex(rec["salt"]))["hash"]
    return hmac.compare_digest(want, rec["hash"])


class ValetError(Exception):
    """A refusal the API turns into a status: .status, .body."""
    def __init__(self, msg, status=400, **extra):
        super().__init__(msg)
        self.status = status
        self.body = dict({"error": msg}, **extra)


# ---- files ----------------------------------------------------------------------

def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def config():
    c = _read(CONFIG)
    return {"pin": c.get("pin"), "no_pin": bool(c.get("no_pin")),
            "fails": int(c.get("fails") or 0),
            "locked_until": float(c.get("locked_until") or 0)}


def _save_config(c):
    _write(CONFIG, c, 0o600)


def state():
    s = _read(STATE)
    return {"on": bool(s.get("on")), "since": s.get("since"), "token": s.get("token"),
            "last_report": s.get("last_report")}


def _save_state(s):
    _write(STATE, s)


def live_speed(now=None):
    """The car's speed in km/h from live.json, or None if nobody can say."""
    now = time.time() if now is None else now
    d = _read(LIVE)
    if not d.get("connected"):
        return None
    t = d.get("t")
    if not isinstance(t, (int, float)) or now - t > STALE:
        return None
    sp = (d.get("values") or {}).get("SPEED")
    return float(sp) if isinstance(sp, (int, float)) else None


# ---- what the API says ------------------------------------------------------------

def read_public(now=None):
    now = time.time() if now is None else now
    s, c = state(), config()
    log = _read(LOG)
    recording = bool(s["on"] and log.get("token") == s["token"]
                     and now - float(log.get("at") or 0) < LOG_STALE)
    locked_for = max(0, int(round(c["locked_until"] - now)))
    out = {"on": s["on"], "since": s["since"],
           "has_pin": bool(c["pin"]), "no_pin": c["no_pin"] and not c["pin"],
           "configured": bool(c["pin"]) or c["no_pin"],
           "locked_for": locked_for,
           "tries_left": max(0, MAX_TRIES - c["fails"]),
           "recording": recording,
           # The recorder notices valet within a second or two; until then
           # "not recording" would be an alarm about nothing.
           "starting": bool(s["on"] and not recording and s["since"] and now - s["since"] < START_GRACE),
           "last_report": s["last_report"]}
    if s["on"] and log.get("token") == s["token"]:
        out["so_far"] = {k: log.get(k) for k in
                         ("top_kmh", "fast_s", "distance_km", "hard_brakes", "simulated")}
    return out


def turn_on(pin=None, no_pin=False, now=None):
    """Turn valet on. Parked only. The first time, the PIN (or "No PIN") is set."""
    now = time.time() if now is None else now
    s, c = state(), config()
    if s["on"]:
        return read_public(now)
    sp = live_speed(now)
    if sp is not None and sp > PARKED_KMH:
        raise ValetError("Valet mode turns on only while the car is parked.", 409, moving=True)
    if not (c["pin"] or c["no_pin"]):
        if pin is not None:
            if not valid_pin(pin):
                raise ValetError("The PIN is four digits.", 400)
            c["pin"], c["no_pin"] = hash_pin(pin), False
        elif no_pin:
            c["pin"], c["no_pin"] = None, True
        else:
            raise ValetError("Set a PIN first, or choose No PIN.", 409, needs_setup=True)
        c["fails"], c["locked_until"] = 0, 0
        _save_config(c)
    _save_state({"on": True, "since": now, "token": secrets.token_hex(8),
                 "last_report": s["last_report"]})
    return read_public(now)


def _report(s, now):
    """The finished report: the recorder's last word if it was writing, else an
    honest 'no log' that still has the times."""
    log = _read(LOG)
    base = {"started": s["since"], "ended": now, "seen": False}
    if log.get("token") == s["token"] and log.get("samples"):
        base.update({k: log.get(k) for k in
                     ("top_kmh", "fast_s", "distance_km", "distance_basis",
                      "hard_brakes", "samples", "simulated")})
        base["tracked"] = True
    else:
        base["tracked"] = False
    return base


def turn_off(pin=None, now=None):
    now = time.time() if now is None else now
    s, c = state(), config()
    if not s["on"]:
        return read_public(now)
    if c["pin"]:
        if c["locked_until"] > now:
            raise ValetError("Too many tries. Wait, then try again.", 429,
                             retry_in=int(round(c["locked_until"] - now)))
        if not check_pin(pin, c["pin"]):
            c["fails"] += 1
            left = MAX_TRIES - c["fails"]
            if left <= 0:
                c["fails"], c["locked_until"] = 0, now + COOLDOWN
                _save_config(c)
                raise ValetError("Too many tries. Wait five minutes.", 429, retry_in=COOLDOWN)
            _save_config(c)
            raise ValetError("That is not the PIN.", 403, tries_left=left)
        if c["fails"]:
            c["fails"] = 0
            _save_config(c)
    rep = _report(s, now)
    _save_state({"on": False, "since": None, "token": None, "last_report": rep})
    return read_public(now)


def ack():
    """The owner has seen the report; it stays on file but stops being news."""
    s = state()
    if s["last_report"]:
        s["last_report"]["seen"] = True
        _save_state(s)
    return read_public()


def set_pin(old=None, new=None, no_pin=False, now=None):
    """Change the PIN while valet is off. The old one is needed if there is one."""
    now = time.time() if now is None else now
    s, c = state(), config()
    if s["on"]:
        raise ValetError("Turn Valet mode off first.", 409)
    if c["pin"]:
        if c["locked_until"] > now:
            raise ValetError("Too many tries. Wait, then try again.", 429,
                             retry_in=int(round(c["locked_until"] - now)))
        if not check_pin(old, c["pin"]):
            raise ValetError("That is not the current PIN.", 403)
    if new is not None:
        if not valid_pin(new):
            raise ValetError("The PIN is four digits.", 400)
        c["pin"], c["no_pin"] = hash_pin(new), False
    elif no_pin:
        c["pin"], c["no_pin"] = None, True
    else:
        raise ValetError("Give a new PIN, or choose No PIN.", 400)
    c["fails"], c["locked_until"] = 0, 0
    _save_config(c)
    return read_public(now)


def ask(data, now=None):
    """The one entry point POST /api/valet uses: {action, pin, no_pin, old, new}."""
    action = str(data.get("action") or "")
    pin = data.get("pin")
    pin = pin if isinstance(pin, str) else None
    if action == "on":
        return turn_on(pin, bool(data.get("no_pin")), now)
    if action == "off":
        return turn_off(pin, now)
    if action == "ack":
        return ack()
    if action == "set_pin":
        old, new = data.get("old"), data.get("new")
        return set_pin(old if isinstance(old, str) else None,
                       new if isinstance(new, str) else None,
                       bool(data.get("no_pin")), now)
    raise ValetError("action is on, off, ack or set_pin", 400)


# ---- the recorder -------------------------------------------------------------------

def run(tick_on=0.25, tick_off=1.0, once=False):
    """Reads live.json while valet is on and keeps valet-log.json current.
    Writes nothing at all while valet is off."""
    log, token, last_write, last_sample = None, None, 0.0, None
    while True:
        now = time.time()
        s = state()
        if not s["on"]:
            log, token = None, None
        else:
            if s["token"] != token:
                log, token, last_write, last_sample = None, s["token"], 0.0, None
            d = _read(LIVE)
            t = d.get("t")
            fresh = (d.get("connected") and isinstance(t, (int, float)) and now - t <= STALE)
            if log is None:
                log = Log(s["since"] or now, simulated=bool(d.get("simulated")))
            if fresh and t != last_sample:
                last_sample = t
                sp = (d.get("values") or {}).get("SPEED")
                if isinstance(sp, (int, float)):
                    log.feed(t, sp, d.get("odometer_km"))
                if d.get("simulated"):
                    log.simulated = True
            if now - last_write >= 1.0:
                last_write = now
                snap = log.snapshot()
                snap.update({"token": token, "at": now})
                _write(LOG, snap)
        if once:
            return
        time.sleep(tick_on if s["on"] else tick_off)


# ---- command line ---------------------------------------------------------------------

def main(argv):
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "run":
            run()
        elif cmd == "status":
            print(json.dumps(read_public(), indent=2))
        elif cmd == "log":
            s = state()
            print(json.dumps(_read(LOG) if s["on"] else (s["last_report"] or {}), indent=2))
        elif cmd == "on":
            import getpass
            c = config()
            pin, no_pin = None, False
            if not (c["pin"] or c["no_pin"]):
                pin = getpass.getpass("  Choose a four-digit PIN (empty for No PIN): ").strip()
                if not pin:
                    print("  No PIN: anyone at the tablet can turn Valet mode off.")
                    pin, no_pin = None, True
            print(json.dumps(turn_on(pin, no_pin), indent=2))
        elif cmd == "off":
            import getpass
            c = config()
            pin = getpass.getpass("  PIN: ") if c["pin"] else None
            print(json.dumps(turn_off(pin), indent=2))
        else:
            print(__doc__)
            return 2
    except ValetError as e:
        print("  " + str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
