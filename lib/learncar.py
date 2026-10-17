#!/usr/bin/env python3
"""Learn my car: a guided, parked, PASSIVE session that labels the CAN bus.

THE PROBLEM. `omacar listen` hears the bus, and the miners (lib/correlate.py,
tools/ima_mine.py) can rank bytes against a channel the car already reports.
For most of what the owner wants -- is the brake down, where is the wheel, is
the IMA ready, is it assisting -- there is no such channel, and nobody has said
which byte means what on this car. What is missing is a yardstick, and the
cheapest yardstick there is is a person who does one thing at a marked moment.

THE SESSION. A fixed script (STEPS): each step asks the owner to do ONE thing
-- hold the brake, turn the wheel, switch the lights -- with a countdown, a big
instruction and a live "listening" lamp, while a passive capture runs around
it. The moments are written down as labelled windows (labels.jsonl, beside the
captures), and lib/correlate.py ranks the bytes that sit still while nothing is
being done and move when it is. Results are candidates with a confidence like
"18/18 windows"; they are never findings, because only the person who watched
the car can promote one.

WHAT IT NEVER DOES. It never transmits. The only adapter operation is the
monitor listen.listen() already performs (ATMA), which does not even
acknowledge a frame. No functional test, no request, no write of any kind. It
runs only parked: it refuses to start, and refuses to take the next step, unless
the daemon's road speed is a fresh zero and nobody else holds the adapter.
Steps that need the engine running say so, and need the parking-brake tap
first. It is not a cockpit write (lib/serve.py COCKPIT_WRITES): it takes the
adapter, so only a full-control session may start it.

    omacar learncar status      what the app is showing now
    omacar learncar run [--sim] hold a session (the app starts this itself)
    omacar learncar mine [--sim]  rank everything labelled so far
    omacar learncar steps       the script

The simulator (`--sim`, or the app's Simulated source) drives the same
machine against a synthetic bus and writes under captures/sim/, labelled
simulated, so it can never rank beside a real car's frames.
"""

import json
import os
import random
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records  # noqa: E402

STATE_DIR = records.STATE
STATE = os.path.join(STATE_DIR, "learncar.json")
ASK = os.path.join(STATE_DIR, "learncar-ask.jsonl")
LIVE = os.path.join(STATE_DIR, "live.json")
CAPTURES = os.path.join(STATE_DIR, "captures")
SIM_CAPTURES = os.path.join(CAPTURES, "sim")

STALE = 8.0               # s without a write while "running": the runner is gone
LIVE_MAX_AGE = 90.0       # a road speed older than this says nothing about now
PARKED_KMH = 0.5          # at or above this the car is moving
LEAD_S = 5.0              # the countdown before a step starts listening
READY_TIMEOUT = 40.0      # s for the adapter to start listening before giving up
RESULT_TOP = 5            # candidates kept per label


# ---- the script (pure) --------------------------------------------------------
#
# Every step is cycles of [rest, act], plus a closing rest unless `tail` is
# False. A "rest" window is the thing NOT being done; an "act" window is the
# thing being done. The miner needs both: it ranks what is quiet in the first
# and moves in the second. `lag_s` is the human delay after a cue (engine
# cranking takes seconds) and is cut from the start of every window after the
# first, so a window holds only the state it is named for.

STEPS = [
    {"id": "ignition", "label": "ignition", "title": "Ignition to II, engine off",
     "kicker": "IMA readiness", "phrase": "the ignition is on (II)",
     "engine": False, "cycles": 1, "rest_s": 6, "act_s": 14, "tail": False, "lag_s": 1.5,
     "prep": "Foot OFF the brake. Press START once for accessory (ACC), then wait for the cue.",
     "first": "Accessory only — hold still", "act": ["Press START once more: ignition ON (II), engine OFF"],
     "back": "Back to accessory", "act_state": ["ON (II)"], "rest_state": "ACC",
     "teaches": "What wakes up when the ignition goes to II: the readiness the IMA waits on."},
    {"id": "brake", "label": "brake", "title": "Brake pedal",
     "kicker": "Brake switch", "phrase": "the brake is held",
     "engine": False, "cycles": 3, "rest_s": 4, "act_s": 5, "tail": True, "lag_s": 0.0,
     "prep": "Ignition ON (II), engine off. Right foot over the brake pedal.",
     "first": "Foot off the brake — hold still", "act": ["Press and HOLD the brake"],
     "back": "Release the brake", "act_state": ["held"], "rest_state": "released",
     "teaches": "The brake-light switch, as the bus sees it."},
    {"id": "steering", "label": "steering", "title": "Steering wheel",
     "kicker": "Steering angle", "phrase": "the wheel is turned",
     "engine": False, "cycles": 4, "rest_s": 4, "act_s": 5, "tail": True, "lag_s": 0.0,
     "prep": "Ignition ON (II), engine off. Both hands on the wheel; you will turn it lock to lock.",
     "first": "Wheel straight ahead — hold still",
     "act": ["Turn the wheel FULL LEFT and hold", "Turn the wheel FULL RIGHT and hold"],
     "back": "Bring the wheel back to centre", "act_state": ["full left", "full right"],
     "rest_state": "centre",
     "teaches": "Steering angle: an analog value that is quiet at centre."},
    {"id": "headlights", "label": "headlights", "title": "Headlights",
     "kicker": "Lighting", "phrase": "the headlights are on",
     "engine": False, "cycles": 3, "rest_s": 4, "act_s": 5, "tail": True, "lag_s": 0.0,
     "prep": "Ignition ON (II), engine off. Hand on the light stalk. Lights start OFF.",
     "first": "Headlights OFF — hold still", "act": ["Headlights ON"],
     "back": "Headlights OFF", "act_state": ["on"], "rest_state": "off",
     "teaches": "The light switch and the lamp state."},
    {"id": "hazards", "label": "hazards", "title": "Hazard lights",
     "kicker": "Lighting", "phrase": "the hazards are flashing",
     "engine": False, "cycles": 3, "rest_s": 4, "act_s": 6, "tail": True, "lag_s": 0.0,
     "prep": "Ignition ON (II), engine off. Finger on the hazard button. Hazards start OFF.",
     "first": "Hazards OFF — hold still", "act": ["Hazards ON"],
     "back": "Hazards OFF", "act_state": ["on"], "rest_state": "off",
     "teaches": "A switch that also flashes: both the button and the lamp output."},
    {"id": "clutch", "label": "clutch", "title": "Clutch pedal",
     "kicker": "Gearbox (manual)", "phrase": "the clutch is in", "trans": "manual",
     "engine": False, "cycles": 3, "rest_s": 4, "act_s": 5, "tail": True, "lag_s": 0.0,
     "prep": "Ignition ON (II), engine off, parking brake on. Gearbox in neutral. Left foot over the clutch.",
     "first": "Clutch out — hold still", "act": ["Clutch pedal IN, all the way, and hold"],
     "back": "Release the clutch", "act_state": ["in"], "rest_state": "out",
     "teaches": "The clutch switch."},
    {"id": "gear", "label": "gear", "title": "Select N, back to P",
     "kicker": "Gearbox (CVT)", "phrase": "the selector is in N", "trans": "cvt",
     "engine": False, "cycles": 2, "rest_s": 4, "act_s": 5, "tail": True, "lag_s": 0.8,
     "prep": "Ignition ON (II), engine off, parking brake on, foot on the brake. Selector in P.",
     "first": "Selector in P — hold still", "act": ["Select N and hold"],
     "back": "Back to P", "act_state": ["N"], "rest_state": "P",
     "teaches": "Selector position."},
    {"id": "engine", "label": "engine", "title": "Start the engine, idle 20 s",
     "kicker": "IMA assist and charge", "phrase": "the engine is running",
     "engine": True, "cycles": 2, "rest_s": 8, "act_s": 22, "tail": False, "lag_s": 3.0,
     "prep": "Parking brake ON. Gearbox in neutral (or P). Ignition ON (II); you will start the engine twice.",
     "first": "Engine OFF, ignition ON (II) — hold still",
     "act": ["START the engine and let it idle"],
     "back": "Switch the engine OFF (back to II)", "act_state": ["idling"],
     "rest_state": "engine off",
     "teaches": "Where the IMA's assist and charge transitions show up when the engine starts."},
    {"id": "rev", "label": "rev", "title": "Rev to 2,000 rpm and hold",
     "kicker": "Engine load", "phrase": "the engine is revved",
     "engine": True, "cycles": 3, "rest_s": 7, "act_s": 7, "tail": True, "lag_s": 2.0,
     "prep": "Engine running from the last step. Parking brake ON, gearbox in neutral (or P), area clear.",
     "first": "Let it idle — hold still",
     "act": ["Rev smoothly to 2,000 rpm and HOLD"],
     "back": "Let it fall back to idle", "act_state": ["2,000 rpm"], "rest_state": "idle",
     "teaches": "Throttle-driven signals and assist at load."},
]
STEP_BY_ID = {s["id"]: s for s in STEPS}


def script(transmission="manual"):
    """The steps this car runs: the clutch step for a manual, the selector step
    for a CVT, never both."""
    return [s for s in STEPS if s.get("trans") in (None, transmission)]


def plan(step, scale=1.0):
    """[{"kind": "rest"|"act", "state", "say", "seconds"}] for one step.

    `scale` shrinks every duration (the simulator's speed-up); real sessions
    use 1.0.
    """
    out, k = [], 0
    for c in range(step["cycles"]):
        out.append({"kind": "rest", "state": step["rest_state"],
                    "say": step["first"] if c == 0 else step["back"],
                    "seconds": step["rest_s"] * scale})
        out.append({"kind": "act", "state": step["act_state"][c % len(step["act_state"])],
                    "say": step["act"][c % len(step["act"])],
                    "seconds": step["act_s"] * scale})
        k += 1
    if step.get("tail", True):
        out.append({"kind": "rest", "state": step["rest_state"], "say": step["back"],
                    "seconds": step["rest_s"] * scale})
    return out


def total_seconds(step, scale=1.0):
    return sum(s["seconds"] for s in plan(step, scale))


def check_parked(env, max_age=LIVE_MAX_AGE):
    """(ok, why). The refusal rule, as a pure function of what the daemon last
    reported. Anything unknown counts against listening, never for it: a
    session that cannot prove the car is stopped does not start."""
    if env is None:
        return False, "Can't tell whether the car is parked"
    if env.get("handover"):
        return False, "Another tool has the adapter (a scan or the drive recorder)"
    speed, age = env.get("speed"), env.get("age")
    if speed is None:
        return False, "Road speed unknown — can't confirm the car is parked"
    if age is None or age > max_age:
        return False, ("Road speed is %s old — can't confirm the car is parked"
                       % ("unknown" if age is None else "%d s" % int(age)))
    if speed >= PARKED_KMH:
        return False, "The car is moving (%d km/h)" % round(speed)
    return True, None


# ---- the state machine (pure: events in, effects out) --------------------------

class Machine:
    """One session. Holds no clock and touches nothing: callers pass `now` and
    `env`, and carry out the effects it asks for."""

    def __init__(self, transmission="manual", scale=1.0):
        self.transmission = transmission
        self.scale = scale
        self.steps = script(transmission)
        self.status = "idle"           # idle running finished aborted refused
        self.reason = None
        self.idx = 0
        self.phase = None              # ready countdown starting listening saving review
        self.phase_at = 0.0
        self.seg = 0
        self.seg_at = 0.0
        self.confirmed = False
        self.blocked = None            # why "Start step" did nothing, last time
        self.done = {}                 # step id -> {"state", "frames", "windows", "why"}
        self.windows = []              # the current step's closed windows (absolute time)
        self._open = None
        self._effects = []
        self.frames = 0

    # -- helpers
    @property
    def step(self):
        return self.steps[self.idx] if 0 <= self.idx < len(self.steps) else None

    def _plan(self):
        return plan(self.step, self.scale)

    def pop_effects(self):
        out, self._effects = self._effects, []
        return out

    def _close_window(self, now):
        if self._open is not None:
            w = self._open
            w["t1"] = now
            self._open = None
            if w["t1"] - w["t0"] > 0.05:
                self.windows.append(w)

    def _open_window(self, seg, now, first):
        lag = 0.0 if first else (self.step.get("lag_s") or 0.0) * self.scale
        self._open = {"label": self.step["label"], "step": self.step["id"],
                      "state": seg["state"], "active": seg["kind"] == "act",
                      "t0": now + lag, "t1": None}

    def _stop(self, status, reason, now):
        if self.phase in ("starting", "listening"):
            self._effects.append(("listen_stop", None))
        self._open = None
        self.status, self.reason, self.phase = status, reason, None
        self.phase_at = now

    # -- events
    def start(self, now, env):
        ok, why = check_parked(env)
        if not ok:
            self.status, self.reason = "refused", why
            return False
        self.status, self.reason = "running", None
        self.idx, self.phase, self.phase_at = 0, "ready", now
        self.confirmed, self.blocked = False, None
        return True

    def confirm(self, now):
        if self.status == "running" and self.phase == "ready":
            self.confirmed = True
            self.blocked = None

    def go(self, now, env):
        """Begin the current step's countdown, if it may."""
        if self.status != "running" or self.phase != "ready":
            return False
        if self.step["engine"] and not self.confirmed:
            self.blocked = "Tap the parking-brake confirmation first"
            return False
        ok, why = check_parked(env)
        if not ok:
            self.blocked = why
            return False
        self.blocked = None
        self.phase, self.phase_at = "countdown", now
        self.windows, self._open, self.frames = [], None, 0
        return True

    def listening(self, now):
        """The adapter is on the bus: the first window opens now."""
        if self.status == "running" and self.phase == "starting":
            self.phase, self.seg, self.seg_at = "listening", 0, now
            self._open_window(self._plan()[0], now, first=True)

    def captured(self, now, frames, ok, why=None):
        if self.status != "running" or self.phase != "saving":
            return
        self.frames = frames
        self.done[self.step["id"]] = {
            "state": "done" if ok else "failed", "frames": frames,
            "windows": len(self.windows), "why": why}
        self.phase, self.phase_at = "review", now

    def fail(self, now, why):
        """The capture itself died (no traffic, the adapter dropped). The step
        is marked failed and writes no labels; the owner may redo it."""
        if self.status != "running" or self.phase not in ("starting", "listening", "saving"):
            return
        self._effects.append(("listen_stop", None))
        self._open, self.windows = None, []
        self.done[self.step["id"]] = {"state": "failed", "frames": self.frames,
                                      "windows": 0, "why": why}
        self.phase, self.phase_at = "review", now

    def next(self, now):
        if self.status != "running" or self.phase != "review":
            return
        self._advance(now)

    def skip(self, now):
        if self.status != "running" or self.phase not in ("ready", "review"):
            return
        self.done.setdefault(self.step["id"], {"state": "skipped", "frames": 0,
                                               "windows": 0, "why": None})
        self._advance(now)

    def retry(self, now):
        if self.status == "running" and self.phase == "review":
            self.phase, self.phase_at, self.confirmed = "ready", now, self.confirmed

    def _advance(self, now):
        self.idx += 1
        self.windows, self._open, self.frames = [], None, 0
        if self.idx >= len(self.steps):
            self.status, self.phase, self.phase_at = "finished", None, now
        else:
            self.phase, self.phase_at = "ready", now
            self.blocked = None
            self.confirmed = self.confirmed and self.step["engine"]

    def abort(self, now):
        if self.status in ("running", "idle"):
            self._stop("aborted", "Stopped by you", now)

    def tick(self, now, env=None):
        """Move time forward. `env` is the car's latest state when the runner
        has one (always in the simulator; in a real session only before the
        port is taken, because holding the port freezes the daemon's reading)."""
        if self.status != "running":
            return
        if self.phase == "countdown" and now - self.phase_at >= LEAD_S * self.scale:
            self.phase, self.phase_at = "starting", now
            self._effects.append(("listen_start", self.step["id"]))
        elif self.phase == "starting" and now - self.phase_at > READY_TIMEOUT:
            self._effects.append(("listen_stop", None))
            self.done[self.step["id"]] = {"state": "failed", "frames": 0, "windows": 0,
                                          "why": "The adapter never started listening"}
            self.phase, self.phase_at = "review", now
        elif self.phase == "listening":
            if env is not None:
                ok, why = check_parked(env)
                if not ok:
                    # It moved (or a hand-off began) mid-step: stop listening
                    # now and end the session. Not retried automatically.
                    self._stop("refused", "Stopped: " + (why or "not parked"), now)
                    return
            segs = self._plan()
            if now - self.seg_at >= segs[self.seg]["seconds"]:
                self._close_window(now)
                if self.seg + 1 < len(segs):
                    self.seg += 1
                    self.seg_at = now
                    self._open_window(segs[self.seg], now, first=False)
                else:
                    self.phase, self.phase_at = "saving", now
                    self._effects.append(("listen_stop", None))

    # -- what the app is shown
    def snapshot(self, now):
        s = self.step
        out = {"status": self.status, "reason": self.reason, "phase": self.phase,
               "idx": self.idx, "transmission": self.transmission,
               "steps": [{"id": t["id"], "title": t["title"], "kicker": t["kicker"],
                          "engine": t["engine"], "teaches": t["teaches"],
                          "seconds": round(total_seconds(t, self.scale) / self.scale),
                          "state": (self.done.get(t["id"]) or {}).get("state", "pending"),
                          "frames": (self.done.get(t["id"]) or {}).get("frames", 0),
                          "windows": (self.done.get(t["id"]) or {}).get("windows", 0),
                          "why": (self.done.get(t["id"]) or {}).get("why")}
                         for t in self.steps],
               "current": None, "blocked": self.blocked}
        if s and self.status == "running":
            cur = {"id": s["id"], "title": s["title"], "kicker": s["kicker"],
                   "prep": s["prep"], "engine": s["engine"], "confirmed": self.confirmed,
                   "n": self.idx + 1, "of": len(self.steps), "frames": self.frames,
                   "segments": [{"kind": g["kind"], "state": g["state"],
                                 "seconds": round(g["seconds"] / self.scale, 1)}
                                for g in self._plan()],
                   "countdown": None, "seg": None, "say": None, "kind": None,
                   "remaining": None, "windows": len(self.windows)}
            if self.phase == "countdown":
                cur["countdown"] = max(0.0, LEAD_S * self.scale - (now - self.phase_at)) / self.scale
            if self.phase == "listening":
                g = self._plan()[self.seg]
                cur.update(seg=self.seg, say=g["say"], kind=g["kind"], state=g["state"],
                           remaining=max(0.0, g["seconds"] - (now - self.seg_at)) / self.scale)
            out["current"] = cur
        return out


# ---- labels and results ---------------------------------------------------------

def captures_dir(sim):
    return SIM_CAPTURES if sim else CAPTURES


def append_labels(capture, windows, sim, note=""):
    """One line per window, in absolute time, in labels.jsonl beside the
    captures. Appended, never rewritten."""
    d = captures_dir(sim)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "labels.jsonl"), "a", encoding="utf-8") as f:
        for w in windows:
            f.write(json.dumps({
                "v": 1, "capture": capture, "label": w["label"], "step": w.get("step"),
                "state": w["state"], "active": bool(w["active"]),
                "t0": round(w["t0"], 3), "t1": round(w["t1"], 3),
                "simulated": bool(sim), "note": note}) + "\n")


def describe(step, c):
    """The sentence for one candidate: what the bytes did, in the words a
    person would use, and how many windows it held in."""
    where = ("byte %d" % c["byte"] if not c.get("span") or c["span"][0] == c["span"][1]
             else "bytes %d–%d" % tuple(c["span"]))
    frac = "%d/%d window%s" % (c["hits"], c["windows"], "" if c["windows"] == 1 else "s")
    verb = "changes only while" if c["kind"] == "switch" else "moves only while"
    if not c["strong"]:
        verb = "changed while" if c["kind"] == "switch" else "moved while"
    s = "0x%s %s %s %s — %s" % (c["id"], where, verb, step["phrase"], frac)
    if not c["strong"]:
        s += (" (a hint: repeat the session to confirm)"
              if c["windows"] < 3 else "")
    if c["also_in"]:
        s += " · also moves during " + ", ".join(c["also_in"])
    return s


def analyse(sim):
    """Rank everything labelled so far for this source. Pure over the files."""
    import correlate
    runs = correlate.load_labelled(captures_dir(sim), simulated=sim)
    ranked = correlate.rank_labelled(runs)
    out = {}
    for s in STEPS:
        lab = s["label"]
        n_act = sum(1 for r in runs for w in r["windows"]
                    if w["label"] == lab and w["active"])
        n_rest = sum(1 for r in runs for w in r["windows"]
                     if w["label"] == lab and not w["active"])
        if not (n_act or n_rest):
            continue
        out[lab] = {
            "label": lab, "title": s["title"], "kicker": s["kicker"],
            "phrase": s["phrase"], "windows": n_act,
            "candidates": [dict(c, text=describe(s, c))
                           for c in ranked.get(lab, [])[:RESULT_TOP]],
        }
    return {"source": "sim" if sim else "car", "at": time.time(),
            "captures": len(runs), "labels": out}


def write_results(res):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".results.tmp"
    path = os.path.join(STATE_DIR, "learncar-results-%s.json" % res["source"])
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(tmp, path)


def read_results(sim):
    path = os.path.join(STATE_DIR, "learncar-results-%s.json" % ("sim" if sim else "car"))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---- the car, as far as this needs to know ---------------------------------------

def read_env(now=None):
    """What the daemon last said about road speed, from live.json. Passive."""
    now = time.time() if now is None else now
    try:
        with open(LIVE, encoding="utf-8") as f:
            snap = json.load(f)
        st = os.stat(LIVE)
    except (OSError, ValueError):
        return {"speed": None, "age": None, "handover": False, "source": "live"}
    t = snap.get("t") if isinstance(snap.get("t"), (int, float)) else st.st_mtime
    speed = (snap.get("values") or {}).get("SPEED")
    return {"speed": float(speed) if isinstance(speed, (int, float)) else None,
            "age": max(0.0, now - t),
            "handover": bool(snap.get("handover")) or snap.get("status") == "yielded",
            "source": "live"}


def sim_env():
    """The simulator's car is parked, unless OMACAR_LEARNCAR_SIM_SPEED says
    otherwise (a way to watch the refusal)."""
    try:
        speed = float(os.environ.get("OMACAR_LEARNCAR_SIM_SPEED", "0"))
    except ValueError:
        speed = 0.0
    return {"speed": speed, "age": 0.0, "handover": False, "source": "sim"}


# ---- the synthetic bus ----------------------------------------------------------

class SimBus:
    """A bus that reacts to what the machine is asking the owner to do.

    Brake toggles byte 3 of 0x17C; the wheel moves the word at 0x1A6; the lamps
    live in 0x305; the clutch and selector in 0x191; the ignition and the IMA in
    0x1D0 and 0x1E4; the engine at 0x1DC. Alongside them: a rolling counter and
    its checksum (they change with everything, and a good miner must not offer
    them), wheel-speed jitter, a constant frame, and one byte at 0x3E8 that
    moves during ANY action (so the "also moves during" note has something to
    say). Every frame is made up. Nothing here has been measured on a car.
    """

    #                id       Hz
    RATES = (("17C", 50), ("1A6", 50), ("305", 20), ("191", 20), ("1D0", 25),
             ("1E4", 20), ("1DC", 50), ("156", 50), ("2D0", 10), ("3E8", 20))

    def __init__(self, seed=1, rate_mult=1.0):
        # rate_mult keeps the frames per window constant when the session is
        # sped up (scale < 1): the same bus, played faster.
        self.rate_mult = rate_mult
        self.rnd = random.Random(seed)
        self.last = None
        self.n = {k: 0 for k, _ in self.RATES}
        self.ctl = {}

    def controls(self, step, seg):
        """What the owner is doing, from the machine's current segment."""
        ctl = {"ign": 0, "engine": 0, "rev": 0, "brake": 0, "wheel": 0, "lights": 0,
               "hazard": 0, "clutch": 0, "gear": 1, "busy": 0}
        if step is None:
            return ctl
        sid, act = step["id"], bool(seg and seg["kind"] == "act")
        ctl["ign"] = 1 if sid != "ignition" or act else 0
        if sid == "ignition":
            ctl["ign"] = 1 if act else 0
        if sid in ("engine", "rev"):
            ctl["ign"] = 1
            ctl["engine"] = 1 if (sid == "rev" or act) else 0
        if act:
            ctl["busy"] = 1
            if sid == "brake":
                ctl["brake"] = 1
            elif sid == "steering":
                ctl["wheel"] = 1 if seg["state"] == "full left" else -1
            elif sid == "headlights":
                ctl["lights"] = 1
            elif sid == "hazards":
                ctl["hazard"] = 1
            elif sid == "clutch":
                ctl["clutch"] = 1
            elif sid == "gear":
                ctl["gear"] = 4
            elif sid == "rev":
                ctl["rev"] = 1
        return ctl

    def frames(self, now, ctl):
        """Frames due between the last call and `now`: [(t, id, bytes)]."""
        if self.last is None:
            self.last = now
            return []
        out = []
        for fid, hz in self.RATES:
            period = 1.0 / (hz * self.rate_mult)
            k0 = int(self.last / period) + 1
            k1 = int(now / period)
            for k in range(k0, k1 + 1):
                ft = k * period
                if ft <= self.last or ft > now:
                    continue
                out.append((ft, fid, self._frame(fid, ft, ctl)))
        self.last = now
        out.sort(key=lambda f: f[0])
        return out

    def _frame(self, fid, t, ctl):
        r = self.rnd
        self.n[fid] += 1
        cnt = self.n[fid] & 0x0F
        if fid == "17C":
            b = [0, 0, 0, 0x40 if ctl["brake"] else 0, 0, 0, cnt, 0]
            b[7] = (sum(b[:7]) * 7 + 0x35) & 0xFF
        elif fid == "1A6":
            ang = ctl["wheel"] * 0x0480 + (r.choice((0, 0, 0, 1, -1)) if not ctl["wheel"] else r.randint(-6, 6))
            ang &= 0xFFFF
            b = [ang >> 8, ang & 0xFF, 0, 0, 0, 0, cnt, 0]
            b[7] = (b[0] + b[1] + cnt * 3) & 0xFF
        elif fid == "305":
            flash = 1 if (ctl["hazard"] and int(t * 2) % 2 == 0) else 0
            b = [0, 0x01 if ctl["lights"] else 0, 0x02 if ctl["hazard"] else 0, flash, 0, 0, 0, 0]
        elif fid == "191":
            b = [0x20 if ctl["clutch"] else 0, 0, ctl["gear"], 0, 0, 0, 0, 0]
        elif fid == "1D0":
            b = [ctl["ign"], 0, 0, 0, 0x80 if ctl["ign"] else 0, 0, 0, 0]
        elif fid == "1E4":
            assist = 0 if not ctl["engine"] else (5 if ctl["rev"] else 2)
            b = [0, 0, assist, 0, 0, 0, 0, 0]
        elif fid == "1DC":
            rpm = 0 if not ctl["engine"] else (2050 if ctl["rev"] else 780) + r.randint(-14, 14)
            b = [rpm >> 8, rpm & 0xFF, 0, 0, 0, 0, 0, 0]
        elif fid == "156":
            b = [r.randint(0, 255) for _ in range(4)] + [0, 0, 0, 0]
        elif fid == "3E8":
            b = [0, 1 if ctl["busy"] else 0, 0, 0, 0, 0, 0, 0]
        else:
            b = [0x5A, 0, 0, 0xFF, 0, 0, 0, 0]
        return bytes(b)


# ---- publishing -----------------------------------------------------------------

def write_state(doc):
    os.makedirs(STATE_DIR, exist_ok=True)
    doc["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, STATE)


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_public():
    """For the API. Always a document: {"present": False} before anything ran."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    if st is None:
        # Nothing has run yet, but the script and any earlier results exist.
        m = Machine()
        st = {"present": False, "status": "idle", "source": None,
              "steps": m.snapshot(time.time())["steps"], "current": None, "reason": None,
              "transmission": "manual", "phase": None, "idx": 0, "blocked": None}
        st["results"] = read_results(False)
        st["results_sim"] = read_results(True)
        st["at"] = None
        st["age"] = None
        st["now"] = time.time()
        return st
    st["present"] = True
    st["age"] = max(0.0, time.time() - float(st.get("at", 0)))
    st["now"] = time.time()
    if st.get("status") == "running" and (st["age"] > STALE or not _pid_alive(st.get("pid"))):
        st["status"] = "stalled"
        st["reason"] = "The session stopped responding"
        st["current"] = None
    st["results"] = read_results(False)
    st["results_sim"] = read_results(True)
    return st


def _runner_alive():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return False
    return (st.get("status") == "running" and _pid_alive(st.get("pid"))
            and time.time() - float(st.get("at", 0)) <= STALE)


ACTIONS = ("start", "go", "confirm", "next", "retry", "skip", "abort")


def ask(action, sim=False, transmission="manual"):
    """What the API calls. Validated here; carried out by the runner.

    `start` is refused outright, with the reason, if the car is not provably
    parked -- before any process is spawned or any adapter touched.
    """
    if action not in ACTIONS:
        raise ValueError("unknown action %r" % (action,))
    if transmission not in ("manual", "cvt"):
        raise ValueError("transmission must be manual or cvt")
    if action == "start":
        if _runner_alive():
            raise PermissionError("A session is already running")
        ok, why = check_parked(sim_env() if sim else read_env())
        if not ok:
            raise PermissionError(why)
        try:
            os.unlink(ASK)
        except OSError:
            pass
        cmd = [sys.executable, os.path.abspath(__file__), "run",
               "--transmission", transmission]
        if sim:
            cmd.append("--sim")
        os.makedirs(STATE_DIR, exist_ok=True)
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {"asked": "start", "source": "sim" if sim else "car"}
    if not _runner_alive():
        raise PermissionError("No session is running")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ASK, "a", encoding="utf-8") as f:
        f.write(json.dumps({"action": action, "at": time.time()}) + "\n")
    return {"asked": action}


def _take_asks():
    taking = ASK + ".taking"
    try:
        os.replace(ASK, taking)
    except OSError:
        return []
    out = []
    try:
        with open(taking, encoding="utf-8") as f:
            for line in f:
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if time.time() - float(a.get("at", 0)) < 60:
                    out.append(a.get("action"))
        os.unlink(taking)
    except OSError:
        pass
    return out


# ---- the runner -----------------------------------------------------------------

class RealListener:
    """One passive capture on the real adapter, in a thread. Hears; never sends."""

    def __init__(self, step, scale, events):
        import listen
        self.listen = listen
        self.step, self.events = step, events
        self.stop = threading.Event()
        self.cap = listen.Capture(note="learncar:" + step["id"])
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.seconds = total_seconds(step, scale) + 30.0
        self.thread.start()

    def _run(self):
        try:
            self.listen.listen(seconds=self.seconds, cap=self.cap, probe=2.0,
                               should_stop=self.stop.is_set, require_traffic=True,
                               on_ready=lambda c: self.events.append("ready"))
        except self.listen.Quiet as e:
            self.error = str(e)
        except Exception as e:                                # noqa: BLE001
            self.error = "%s: %s" % (type(e).__name__, e)

    def frames(self):
        return len(self.cap.frames)

    def finished(self):
        return not self.thread.is_alive()

    def finish(self, windows):
        """Mark the capture with the windows and keep it, raw."""
        for w in windows:
            self.cap.marks.append((w["t0"], "%s:%s" % (w["label"], w["state"])))
        name = "learncar-%s-%s" % (self.step["id"], time.strftime(
            "%Y%m%d-%H%M%S", time.localtime(self.cap.started)))
        self.cap.save(name=name, raw=True)
        return name, len(self.cap.frames)


class SimListener:
    """The same shape against the synthetic bus, saved under captures/sim/."""

    def __init__(self, step, scale, machine, bus):
        self.step, self.m, self.bus = step, machine, bus
        self.frames_list, self.started = [], time.time()
        self.error = None
        self.bus.last = None

    def pump(self, now):
        seg = None
        if self.m.phase == "listening":
            seg = self.m._plan()[self.m.seg]
        for f in self.bus.frames(now, self.bus.controls(self.step, seg)):
            self.frames_list.append(f)

    def frames(self):
        return len(self.frames_list)

    def finish(self, windows):
        os.makedirs(SIM_CAPTURES, exist_ok=True)
        name = "learncar-%s-%s" % (self.step["id"], time.strftime(
            "%Y%m%d-%H%M%S", time.localtime(self.started)))
        doc = {"started": self.started, "note": "learncar:%s (Simulated)" % self.step["id"],
               "simulated": True, "frames": len(self.frames_list),
               "marks": [{"at": w["t0"], "label": "%s:%s" % (w["label"], w["state"])}
                         for w in windows],
               "raw": [{"t": round(t - self.started, 4), "id": i, "data": d.hex().upper()}
                       for t, i, d in self.frames_list]}
        with open(os.path.join(SIM_CAPTURES, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return name, len(self.frames_list)


def run(sim=False, transmission="manual", scale=None, tick=None):
    """Hold one session until it ends. Returns the final status."""
    if scale is None:
        try:
            scale = 1.0 / max(1.0, float(os.environ.get("OMACAR_LEARNCAR_SPEED", "1")))
        except ValueError:
            scale = 1.0
    if not sim:
        scale = 1.0                      # a real session is never sped up
    if tick is None:
        tick = min(0.2, max(0.02, 0.4 * scale))
    m = Machine(transmission, scale)
    bus = SimBus(rate_mult=1.0 / scale) if sim else None
    events, listener = [], None
    source = "sim" if sim else "car"

    def publish(env):
        doc = m.snapshot(time.time())
        doc.update(source=source, pid=os.getpid(), scale=scale,
                   speedup=round(1.0 / scale, 2), parked=check_parked(env)[0],
                   parked_why=check_parked(env)[1], env=env)
        write_state(doc)

    env = sim_env() if sim else read_env()
    m.start(time.time(), env)
    publish(env)
    if m.status != "running":
        return m.status
    while m.status == "running":
        now = time.time()
        # The car: live from the daemon before the port is taken; the
        # simulator's is always on hand. Mid-step a real session cannot see it.
        holding = m.phase in ("starting", "listening", "saving")
        env = sim_env() if sim else (read_env(now) if not holding else None)
        for a in _take_asks():
            if a == "start":
                continue
            if a == "go":
                m.go(now, env if env is not None else read_env(now))
            elif a == "confirm":
                m.confirm(now)
            elif a == "next":
                m.next(now)
            elif a == "retry":
                m.retry(now)
            elif a == "skip":
                m.skip(now)
            elif a == "abort":
                m.abort(now)
        while events:
            if events.pop(0) == "ready":
                m.listening(now)
        m.tick(now, env)
        if sim and listener is not None:
            listener.pump(now)
        for eff, arg in m.pop_effects():
            if eff == "listen_start":
                step = STEP_BY_ID[arg]
                if sim:
                    listener = SimListener(step, scale, m, bus)
                    events.append("ready")
                else:
                    listener = RealListener(step, scale, events)
            elif eff == "listen_stop":
                if listener is not None and not sim:
                    listener.stop.set()
        if listener is not None and not sim and listener.finished() \
                and m.phase in ("starting", "listening"):
            m.fail(now, listener.error or "The adapter stopped listening")
            listener = None
        if listener is not None:
            if m.phase in ("listening", "saving"):
                m.frames = listener.frames()
            if m.phase == "saving" and (sim or listener.finished()):
                ok, why = True, None
                name, n = None, listener.frames()
                if listener.error or n == 0:
                    ok, why = False, listener.error or "The bus was silent"
                else:
                    name, n = listener.finish(m.windows)
                    append_labels(name, m.windows, sim)
                    try:
                        write_results(analyse(sim))
                    except Exception:                         # noqa: BLE001
                        pass
                m.captured(time.time(), n, ok, why)
                listener = None
            elif m.phase in ("review", None) and listener is not None and not sim \
                    and listener.finished():
                listener = None
            elif m.status != "running":
                listener = None
        publish(env if env is not None else (sim_env() if sim else read_env(now)))
        time.sleep(tick)
    if listener is not None and not sim:
        listener.stop.set()
    publish(sim_env() if sim else read_env())
    return m.status


# ---- the CLI --------------------------------------------------------------------

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar learncar")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=("status", "run", "mine", "steps"))
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--transmission", default="manual", choices=("manual", "cvt"))
    args = ap.parse_args(argv)
    if args.cmd == "run":
        status = run(sim=args.sim, transmission=args.transmission)
        print("  session %s" % status)
        return 0 if status == "finished" else 1
    if args.cmd == "steps":
        for i, s in enumerate(script(args.transmission), 1):
            print("  %d. %-30s %3.0f s%s" % (i, s["title"], total_seconds(s),
                                             "  (engine running)" if s["engine"] else ""))
        return 0
    if args.cmd == "mine":
        res = analyse(args.sim)
        write_results(res)
        print("  %d labelled capture(s), %s" % (res["captures"],
                                                "Simulated" if args.sim else "car"))
        for lab, r in res["labels"].items():
            print("\n  %s — %d active window(s)" % (r["title"], r["windows"]))
            for c in r["candidates"] or []:
                print("    " + c["text"])
            if not r["candidates"]:
                print("    nothing stood out")
        return 0
    st = read_public()
    print(json.dumps({k: st.get(k) for k in ("status", "source", "idx", "phase", "reason")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
