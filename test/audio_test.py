#!/usr/bin/env python3
"""The Surface's volume, held at 100% where it was asked for and nowhere else:
reading wpctl and pactl, naming the port, and apply()/pin() deciding whether
and how to touch anything.

wpctl never runs for real in THIS PROCESS: every case below drives
lib/audio.py through a fake sink (audio._run and audio._sleep are replaced),
so nothing here can spend real wall-clock time or touch a real device. The
one exception is the "test servers" section near the bottom, which starts
real lib/serve.py subprocesses to prove app_test.py's config isolation --
those get a FAKE wpctl/pactl put first on PATH (see FAKEBIN below), so even
those separate processes never run the real tools either.

Fix round 2 rewrote pin() as a closed loop that re-reads the real sink on
every iteration rather than trusting a locally-tracked counter -- see
lib/audio.py's own header for why -- and the sections below test exactly
that: a failed wpctl call, the volume changing from outside mid-ramp, the
sink already parked at "Volume: 0.00", and two callers racing the same sink,
none of which may ever cost more than one small step.
"""

import json
import math
import os
import random
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
SCRATCH = tempfile.mkdtemp(prefix="omacar-audio-test-")
os.environ["XDG_CONFIG_HOME"] = SCRATCH
# pin()'s lock file (fix round 2) lives under XDG_RUNTIME_DIR, or the state
# dir if that is unset. Both are pointed at this test's own scratch area, so
# the lock this process takes is never the same file a real `omacar audio`
# on this machine would take, and cleanup is one rmtree at the bottom.
os.environ["XDG_RUNTIME_DIR"] = SCRATCH
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")

import audio  # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


head("reading wpctl")
check("a plain volume", audio.parse_volume("Volume: 0.25\n"), (0.25, False))
check("muted", audio.parse_volume("Volume: 1.00 [MUTED]\n"), (1.0, True))
check("nothing readable is nothing", audio.parse_volume("error"), (None, None))

head("naming the port")
SINK = "alsa_output.pci-0000_00_1f.3.analog-stereo"
SINKS = [{"name": SINK, "active_port": "analog-output-headphones",
          "ports": [{"name": "analog-output-speaker", "description": "Speakers",
                     "availability": "availability unknown"},
                    {"name": "analog-output-headphones", "description": "Headphones",
                     "availability": "available"}]}]
check("headphones are the AUX cable", audio.port_of(SINKS, SINK), ("aux", "Headphones"))
check("the speakers are the speakers",
      audio.port_of([dict(SINKS[0], active_port="analog-output-speaker")], SINK),
      ("speakers", "Speakers"))
check("so is a UCM-style port name",
      audio.port_of([{"name": "x", "active_port": "[Out] Headphones",
                      "ports": [{"name": "[Out] Headphones", "description": "Headphones"}]}], "x"),
      ("aux", "Headphones"))
check("an unknown default sink is unknown, never AUX", audio.port_of(SINKS, "other"), ("unknown", ""))


# ---------------------------------------------------------------------------
# A fake sink that actually behaves like one: set-volume and set-mute change
# what the NEXT get-volume reports, exactly like PipeWire would. Everything
# is logged with a fake clock (advanced only by audio._sleep), so a step's
# dB size can be measured against the level that was ACTUALLY READ before
# it -- not against what pin() merely intended -- and the time between
# steps can be measured without this suite spending real seconds asleep.
class FakeSink:
    def __init__(self, vol=0.25, muted=False, fail_sets=None, get_fail_always=False,
                 external_at_sleep=None, lock=None):
        self.vol = vol
        self.muted = muted
        self.log = []                       # every call, in order, with a fake timestamp
        self.t = 0.0
        self.fail_sets = fail_sets or set()  # 1-based set-volume call numbers to fail
        self.set_count = 0
        self.get_fail_always = get_fail_always
        self.external_at_sleep = external_at_sleep or {}   # {sleep#: (vol, muted)}
        self.sleep_count = 0
        self._lock = lock or threading.Lock()

    def run(self, args, timeout=5):
        with self._lock:
            e = {"t": self.t, "args": list(args)}
            if args[:2] == ["wpctl", "get-volume"]:
                if self.get_fail_always:
                    e["kind"] = "get-fail"
                    self.log.append(e)
                    return 1, ""
                e["kind"], e["vol"], e["muted"] = "get", self.vol, self.muted
                self.log.append(e)
                return 0, f"Volume: {self.vol:.2f}{' [MUTED]' if self.muted else ''}\n"
            if args[:2] == ["wpctl", "set-volume"]:
                self.set_count += 1
                idx = self.set_count
                req = float(args[3])
                success = idx not in self.fail_sets
                e["kind"], e["req"], e["ok"] = "set", req, success
                if success:
                    self.vol = req
                self.log.append(e)
                return (0, "") if success else (1, "")
            if args[:2] == ["wpctl", "set-mute"]:
                self.muted = args[3] != "0"
                e["kind"] = "mute"
                self.log.append(e)
                return 0, ""
            if args[:2] == ["pactl", "get-default-sink"]:
                return 0, SINK + "\n"
            if args[:3] == ["pactl", "--format=json", "list"]:
                return 0, json.dumps(SINKS)
            return 0, ""

    def sleep(self, secs):
        with self._lock:
            self.sleep_count += 1
            self.t += secs
            if self.sleep_count in self.external_at_sleep:
                self.vol, self.muted = self.external_at_sleep[self.sleep_count]


def _db(v):
    """The exact formula pin() uses (mirrored, not imported, so a bug in one
    cannot cancel out a bug in the other)."""
    return -60.0 if v is None or v <= 0.001 else 60.0 * math.log10(v)


def audible_steps(sink):
    """Every SUCCESSFUL set-volume made while UNMUTED, paired with the level
    that was ACTUALLY READ right before it (never with what pin() merely
    intended). Each entry is a dict: t, before_db, after_db, delta_db,
    after_vol, below_floor. `below_floor` marks fix round 3's one exempt
    step: a single jump from below FLOOR_V straight to it, which the ruling
    says is not audible enough to need ramping at all. Floor-sets made WHILE
    STILL MUTED are excluded entirely -- inaudible by construction, and
    checked separately by mute_events()."""
    last_read = None
    out = []
    for e in sink.log:
        if e["kind"] == "get":
            last_read = e
        elif e["kind"] == "set" and e["ok"] and last_read is not None and not last_read["muted"]:
            before_v = last_read["vol"]
            before_db, after_db = _db(before_v), _db(e["req"])
            out.append({"t": e["t"], "before_db": before_db, "after_db": after_db,
                        "delta_db": after_db - before_db, "after_vol": e["req"],
                        "below_floor": before_v < audio.FLOOR_V - 1e-9})
    return out


def ramp_steps(sink):
    """audible_steps(), excluding the one floor-jump fix round 3 exempts
    from the 3 dB ceiling -- what the "every step stays <= 3.0 dB" checks
    below actually mean, once a below-floor start is allowed one big step."""
    return [s for s in audible_steps(sink) if not s["below_floor"]]


def floor_jumps(sink):
    return [s for s in audible_steps(sink) if s["below_floor"]]


def worst_step(steps):
    return max((s["delta_db"] for s in steps), default=0.0)


def steps_ok(steps, ceiling=3.0):
    return all(s["delta_db"] <= ceiling + 1e-6 for s in steps)


def honest_pin(result, target=1.0):
    """The ruling's core invariant (fix round 3): pin() may never claim
    `pinned: true` unless the level it actually confirmed by reading is
    genuinely within tolerance of the target. Nothing to check when it did
    not claim pinned at all."""
    if not result.get("pinned"):
        return True
    v = result.get("volume")
    return v is not None and abs(_db(v) - _db(target)) <= audio.TARGET_TOL_DB + 1e-9


def mute_events(sink):
    """Every unmute call, paired with the read immediately before it, so it
    can be checked that unmuting only ever happens once that read confirmed
    "muted, and at or below the floor"."""
    last_read = None
    out = []
    for e in sink.log:
        if e["kind"] == "get":
            last_read = e
        elif e["kind"] == "mute":
            out.append((e["t"], last_read))
    return out


def action_times(sink):
    """The fake-clock timestamp of every call that could change what is
    HEARD: an unmute, or a set-volume made while already unmuted (whether it
    succeeded or not -- wpctl was still asked, and pin() still sleeps
    afterward). A set-volume made WHILE STILL MUTED is deliberately excluded:
    it is inaudible by construction, pin() re-reads to confirm it at once
    with no sleep in between, and the plan's "3 dB / 100 ms" is a rule about
    what a driver can hear."""
    last_read = None
    out = []
    for e in sink.log:
        if e["kind"] == "get":
            last_read = e
        elif e["kind"] == "mute":
            out.append(e["t"])
        elif e["kind"] == "set" and last_read is not None and not last_read["muted"]:
            out.append(e["t"])
    return out


def gaps_ok(sink, min_gap=0.095):
    times = action_times(sink)
    return all(b - a >= min_gap - 1e-9 for a, b in zip(times, times[1:]))


def drive(sink):
    audio._run = sink.run
    audio._sleep = sink.sleep
    return audio.pin()


head("apply() touches the volume only where it was asked to")
sink0 = FakeSink(vol=0.25, muted=False)
audio._run, audio._sleep = sink0.run, sink0.sleep
st0 = audio.apply()
check("not asked (unmanaged): nothing is set, nothing read moves",
      ([e for e in sink0.log if e["kind"] in ("set", "mute")], st0["managed"], st0["aux"], st0["volume"]),
      ([], False, True, 0.25))
audio.set_managed(True)
sink1 = FakeSink(vol=1.0, muted=False)
audio._run, audio._sleep = sink1.run, sink1.sleep
audio.apply()
check("already at 100%, unmuted: apply() never even calls pin()'s ramp",
      [e for e in sink1.log if e["kind"] in ("set", "mute")], [])
audio.set_managed(False)
check("and off again", audio.managed(), False)
audio.set_managed(True)      # left managed for the sections below


head("A: from 25% unmuted -- every step, including the first from the "
     "starting read, is <= 3.0 dB and >= 95 ms apart")
sinkA = FakeSink(vol=0.25, muted=False)
resultA = drive(sinkA)
stepsA = ramp_steps(sinkA)
check("A: pinned", resultA["pinned"], True)
check("A: and honestly -- the final volume really is within tolerance of the target",
      honest_pin(resultA), True)
check("A: reaches the target (got " + repr(resultA["volume"]) + ")",
      abs(resultA["volume"] - 1.0) < 0.02, True)
check(f"A: every step (n={len(stepsA)}) is <= 3.0 dB (worst {worst_step(stepsA):.3f})",
      steps_ok(stepsA), True)
check("A: no floor-jump -- it started above the floor", floor_jumps(sinkA), [])
check("A: no mute traffic at all -- it was never muted", mute_events(sinkA), [])
check("A: consecutive steps are >= 95 ms apart", gaps_ok(sinkA), True)


head("B: from MUTED at 100% -- floor before unmute, never heard at 0 dB "
     "(the exact defect fix round 1 left: Probe H)")
sinkB = FakeSink(vol=1.0, muted=True)
resultB = drive(sinkB)
check("B: pinned", resultB["pinned"], True)
check("B: and honestly -- the final volume really is within tolerance of the target",
      honest_pin(resultB), True)
mutesB = mute_events(sinkB)
check("B: unmutes exactly once", len(mutesB), 1)
_, priorB = mutesB[0] if mutesB else (None, None)
check("B: that unmute's own most-recent read confirmed muted AND at-or-below the floor",
      (priorB["muted"], priorB["vol"] <= audio.FLOOR_V + 1e-9) if priorB else None, (True, True))
check("B: no below-floor jump -- it arrived at the floor by unmuting there, "
      "not by ramping up to it", floor_jumps(sinkB), [])
stepsB = ramp_steps(sinkB)
check(f"B: every audible step after that (n={len(stepsB)}) is still <= 3.0 dB",
      steps_ok(stepsB), True)
check("B: consecutive steps are >= 95 ms apart", gaps_ok(sinkB), True)

head("B2: muted at 100%, and the FIRST floor call itself fails -- it must "
     "retry rather than unmuting at whatever the sink is really at")
sinkB2 = FakeSink(vol=1.0, muted=True, fail_sets={1})   # the floor-set fails once
resultB2 = drive(sinkB2)
check("B2: still pinned in the end", resultB2["pinned"], True)
check("B2: and honestly", honest_pin(resultB2), True)
mutesB2 = mute_events(sinkB2)
check("B2: still unmutes exactly once", len(mutesB2), 1)
_, priorB2 = mutesB2[0] if mutesB2 else (None, None)
check("B2: and only once a read (not a hope) confirmed the floor",
      (priorB2["muted"], priorB2["vol"] <= audio.FLOOR_V + 1e-9) if priorB2 else None, (True, True))
# The failed floor-set must never have been followed by an unmute while the
# sink was still actually at 100% -- the exact shape of Probe H.
bad_unmute = any(prior["vol"] > audio.FLOOR_V + 1e-9 for _, prior in mutesB2)
check("B2: never unmuted while the sink was still really at the old level", bad_unmute, False)


head("C: from Volume: 0.00, unmuted -- completes, and does not hang "
     "(New Breakage 1: 0.0 * anything stays 0.0 with an open-loop ramp)")
sinkC = FakeSink(vol=0.0, muted=False)
resultC = drive(sinkC)
check("C: pinned rather than hanging", resultC["pinned"], True)
check("C: and honestly", honest_pin(resultC), True)
check("C: within the hard iteration bound", resultC["iterations"] <= audio.MAX_ITERATIONS, True)
stepsC = ramp_steps(sinkC)
check(f"C: every step from the floor upward (n={len(stepsC)}) is <= 3.0 dB",
      steps_ok(stepsC), True)


head("D: random wpctl set failures (seeded) -- every SUCCESSFUL step is "
     "still <= 3.0 dB from wherever the sink actually was")
rng = random.Random(20260930)
fail_sets_D = {i for i in range(1, 30) if rng.random() < 0.4}
sinkD = FakeSink(vol=0.25, muted=False, fail_sets=fail_sets_D)
resultD = drive(sinkD)
stepsD = ramp_steps(sinkD)
check(f"D: {len(fail_sets_D)} of the first 29 set-volume calls were made to fail; "
      f"{len(stepsD)} succeeded", len(stepsD) > 0, True)
check(f"D: every one of those is still <= 3.0 dB (worst {worst_step(stepsD):.3f})",
      steps_ok(stepsD), True)
check("D: it still reaches the target despite the failures", resultD["pinned"], True)
check("D: and honestly", honest_pin(resultD), True)


head("E: the volume changed from outside mid-ramp, up and then down -- "
     "each step is still measured from a fresh read, so it absorbs both")
sinkE = FakeSink(vol=0.25, muted=False, external_at_sleep={1: (0.40, False), 3: (0.15, False)})
resultE = drive(sinkE)
stepsE = ramp_steps(sinkE)
check("E: still reaches the target", resultE["pinned"], True)
check("E: and honestly", honest_pin(resultE), True)
check(f"E: every step (n={len(stepsE)}), even the one right after an outside "
      f"change, is <= 3.0 dB (worst {worst_step(stepsE):.3f})",
      steps_ok(stepsE), True)


head("H: from 5% unmuted -- BELOW the floor. One jump straight to the "
     "floor (exempt from the 3 dB ceiling), then an ordinary ramp; ends "
     "pinned honestly at the target (the exact defect the re-review found "
     "in 86766dc: v=0.05, a 4.75 dB tick, used to report pinned at 5%)")
sinkH = FakeSink(vol=0.05, muted=False)
resultH = drive(sinkH)
check("H: pinned", resultH["pinned"], True)
check("H: and honestly -- the final volume really is at the target", honest_pin(resultH), True)
check("H: reaches the target (got " + repr(resultH["volume"]) + ")",
      abs(resultH["volume"] - 1.0) < 0.02, True)
jumpsH = floor_jumps(sinkH)
check("H: exactly one floor-jump, landing exactly at the floor",
      [round(s["after_vol"], 2) for s in jumpsH], [audio.FLOOR_V])
rampH = ramp_steps(sinkH)
check(f"H: every step at or above the floor (n={len(rampH)}) stays <= 3.0 dB "
      f"(worst {worst_step(rampH):.3f})", steps_ok(rampH), True)


head("I: from 1% unmuted -- the re-review's second worked example (an "
     "18 dB tick at v=0.01)")
sinkI = FakeSink(vol=0.01, muted=False)
resultI = drive(sinkI)
check("I: pinned", resultI["pinned"], True)
check("I: and honestly", honest_pin(resultI), True)
check("I: reaches the target (got " + repr(resultI["volume"]) + ")",
      abs(resultI["volume"] - 1.0) < 0.02, True)
jumpsI = floor_jumps(sinkI)
check("I: exactly one floor-jump, landing exactly at the floor",
      [round(s["after_vol"], 2) for s in jumpsI], [audio.FLOOR_V])
rampI = ramp_steps(sinkI)
check(f"I: every step at or above the floor (n={len(rampI)}) stays <= 3.0 dB "
      f"(worst {worst_step(rampI):.3f})", steps_ok(rampI), True)


head("J: from 0.2% unmuted -- the coordinator's own boundary case, right "
     "above FLOOR_READ_V")
sinkJ = FakeSink(vol=0.002, muted=False)
resultJ = drive(sinkJ)
check("J: pinned", resultJ["pinned"], True)
check("J: and honestly", honest_pin(resultJ), True)
check("J: reaches the target (got " + repr(resultJ["volume"]) + ")",
      abs(resultJ["volume"] - 1.0) < 0.02, True)
jumpsJ = floor_jumps(sinkJ)
check("J: exactly one floor-jump, landing exactly at the floor",
      [round(s["after_vol"], 2) for s in jumpsJ], [audio.FLOOR_V])
rampJ = ramp_steps(sinkJ)
check(f"J: every step at or above the floor (n={len(rampJ)}) stays <= 3.0 dB "
      f"(worst {worst_step(rampJ):.3f})", steps_ok(rampJ), True)


head("F: two pin() calls at once -- the second returns busy and changes "
     "nothing, and the combined sequence still obeys the limit")
sinkF = FakeSink(vol=1.0, muted=True)             # the longest ramp (floor, then all the way up):
                                                   # a wide real-time window for B to contend the lock
started = threading.Event()
real_run = sinkF.run


def gated_run(args, timeout=5):
    r = real_run(args, timeout=timeout)
    started.set()
    return r


def slow_sleep(secs):
    # A little REAL wall time per step (not just the fake clock), so thread
    # A's ramp takes long enough in practice for B to genuinely contend the
    # lock rather than finding it already released.
    sinkF.sleep(secs)
    time.sleep(0.02)


resultsF = {}


def run_A():
    audio._run, audio._sleep = gated_run, slow_sleep
    resultsF["A"] = audio.pin()


def run_B():
    started.wait(timeout=5)     # A has made its first real wpctl call: it holds the lock
    audio._run, audio._sleep = gated_run, slow_sleep
    resultsF["B"] = audio.pin()


tA = threading.Thread(target=run_A)
tB = threading.Thread(target=run_B)
tA.start()
tB.start()
tA.join(timeout=20)
tB.join(timeout=20)
check("F: A actually ran the ramp and pinned it", resultsF.get("A", {}).get("pinned"), True)
check("F: and honestly", honest_pin(resultsF.get("A", {})), True)
check("F: B found the lock held and changed nothing",
      (resultsF.get("B", {}).get("pinned"), resultsF.get("B", {}).get("busy")), (False, True))
check("F: B's own result shows it never ran a single iteration -- it "
      "returned at the lock, before ever touching the sink",
      resultsF.get("B", {}).get("iterations"), 0)
stepsF = ramp_steps(sinkF)
check(f"F: even with two callers in the picture, every recorded step "
      f"(n={len(stepsF)}) still obeys <= 3.0 dB",
      steps_ok(stepsF), True)


head("G: a read that fails every time -- gives up within the bounds and "
     "reports an error, rather than looping forever")
sinkG = FakeSink(vol=0.25, muted=False, get_fail_always=True)
resultG = drive(sinkG)
check("G: never claims to have pinned it", resultG["pinned"], False)
check("G: and honestly reports it did not (trivially true when pinned is "
      "already false, but checked the same way as every other case)",
      honest_pin(resultG), True)
check("G: reports an error rather than staying silent", resultG["error"] is not None, True)
check("G: hits exactly the iteration cap (it never gives up early on a "
      "read failure, and never loops past it)", resultG["iterations"], audio.MAX_ITERATIONS)


head("test servers can never see the real audio config, and never run a "
     "real wpctl or pactl either")
# Task 4's alertness.js POSTs /api/audio on every page load, which
# lib/audio.py answers by reading the managed flag out of
# $XDG_CONFIG_HOME/omarchy/omacar-audio.json. Every server test/app_test.py
# starts passes an isolated XDG_CONFIG_HOME (app_test._isolated_env, fix
# round 1). This proves that fix rather than re-describing it: a "simulated
# real" config (never the box's actual one) is given to a server the OLD
# way, to show the flag mechanism really can leak if nothing isolates it,
# and then to a server started app_test.py's way, to show it cannot.
#
# NEITHER SERVER GETS THE REAL wpctl OR pactl. The re-review found that both
# of fix round 1's servers, while never able to see the real CONFIG, could
# still run the real audio TOOLS against the box's real PipeWire (safe here
# only because this suite only ever GETs /api/audio, never POSTs apply). A
# fake wpctl/pactl on PATH, ahead of the real ones, closes that off too --
# chosen over just correcting the docstring, since a docstring is a promise
# that only holds until the next person adds a POST here.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import _isolated_env  # noqa: E402  -- the fix under test

SHARE = os.path.join(ROOT, "share")

FAKEBIN = tempfile.mkdtemp(prefix="omacar-fake-wpctl-")
for name, body in (
    ("wpctl", '#!/bin/sh\nif [ "$1" = "get-volume" ]; then echo "Volume: 0.50"; fi\nexit 0\n'),
    ("pactl", '#!/bin/sh\n'
     'if [ "$1" = "get-default-sink" ]; then echo "fake-sink"; exit 0; fi\n'
     'if [ "$1" = "--format=json" ]; then echo "[]"; exit 0; fi\n'
     'exit 0\n'),
):
    p = os.path.join(FAKEBIN, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_path_env(env):
    env = dict(env)
    env["PATH"] = FAKEBIN + os.pathsep + env.get("PATH", "")
    return env


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, secs=15):
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _audio_via(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/audio", timeout=5) as r:
        return json.loads(r.read())


def _serve(env, port):
    return subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "lib", "serve.py"), str(port), SHARE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def _stop(srv):
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except subprocess.TimeoutExpired:
        srv.kill()


SIMROOT = tempfile.mkdtemp(prefix="omacar-simulated-real-config-")
os.makedirs(os.path.join(SIMROOT, "omarchy"), exist_ok=True)
with open(os.path.join(SIMROOT, "omarchy", "omacar-audio.json"), "w", encoding="utf-8") as f:
    json.dump({"pin": True}, f)

leak_env = dict(os.environ)
leak_env["XDG_CONFIG_HOME"] = SIMROOT
leak_env = _fake_path_env(leak_env)
leak_port = _free_port()
leak_srv = _serve(leak_env, leak_port)
try:
    if _wait_port(leak_port):
        got = _audio_via(leak_port)
        check("sanity: a server actually pointed at the simulated real config sees the flag",
              got.get("managed"), True)
    else:
        bad("the sanity server never came up")
finally:
    _stop(leak_srv)

fixed_port = _free_port()
fixed_srv = _serve(_fake_path_env(_isolated_env()), fixed_port)
try:
    if _wait_port(fixed_port):
        got = _audio_via(fixed_port)
        check("app_test.py's fix: a server started the way it starts one never sees it",
              got.get("managed"), False)
    else:
        bad("the isolated server never came up")
finally:
    _stop(fixed_srv)

shutil.rmtree(SIMROOT, ignore_errors=True)
shutil.rmtree(FAKEBIN, ignore_errors=True)

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the audio path holds\n")
