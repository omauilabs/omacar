#!/usr/bin/env python3
"""The Surface's volume, held at 100% where it was asked for and nowhere else:
reading wpctl and pactl, naming the port, and apply()/pin() deciding whether
and how to touch anything.

wpctl never runs for real in THIS PROCESS: every case below drives
lib/audio.py through a fake sink (audio._run, audio._sleep and audio._now
are replaced), so nothing here can spend real wall-clock time or touch a real
device. The one exception is the "test servers" section near the bottom,
which starts real lib/serve.py subprocesses to prove app_test.py's config
isolation -- those get a FAKE wpctl/pactl put first on PATH (see FAKEBIN
below), so even those separate processes never run the real tools either.

Every judgement is made against the fake sink's REAL state -- its level at
full precision, and whether it is muted -- never against what pin() read or
meant to set: a step is measured from the level the sink was really at when
the set landed, and `pinned: true` is checked against the level the sink is
really at afterwards. The fake's set-volume is wpctl's: a relative `VOL+`
is added to the level the sink is at when the set lands, `-l` stops it at
the limit, and argv wpctl would refuse is refused. The numbers the rulings
fixed (the 0.13 floor, 3 dB, 100 ms, 40 iterations, 7 s, 8 s, the 1 s
timeouts, 13 s for apply(), the largest step of 0.09) are written out here,
not read from lib/audio.py, so changing one there fails a test rather than
moving the goalposts with it.
"""

import json
import math
import os
import random
import re
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
# pin()'s lock file lives under XDG_RUNTIME_DIR, or the state dir if that is
# unset. Both are pointed at this test's own scratch area, so the lock this
# process takes is never the same file a real `omacar audio` on this machine
# would take, and cleanup is one rmtree at the bottom.
os.environ["XDG_RUNTIME_DIR"] = SCRATCH
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")

import audio  # noqa: E402

# The rulings' numbers.
FLOOR = 0.13                 # -53.2 dB
MAX_STEP_DB = 3.0
MIN_GAP = 0.1
ITERATIONS = 40
LAST_START = 7.0
MAX_SECS = 8.0
TOOL_TIMEOUT = 1.0
APPLY_WORST = 13.0           # 3 s status() + 9 s pin() + 1 s read after
PAGE_MARGIN = 2.0            # what the page's abort must leave over that
# The largest relative step: 2.5 dB above the lowest real level a read
# allows, rounded down to 0.01, and no more than it takes to reach 1.00 from
# there. That is 0.09, from reads of 0.90, 0.91 and 0.92. So an outside drop
# to the floor just before a step lands at most 0.13 + 0.09 = 0.22.
DELTA_MAX = 0.09
GAP_WORST_DB = 60.0 * math.log10((FLOOR + DELTA_MAX) / FLOOR)     # 13.71 dB

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
# A fake sink that behaves like a real one. `vol` is its REAL level, at any
# precision; get-volume prints it the way wpctl does, to two decimals, so an
# off-grid level reads the way it would on the tablet. set-volume and
# set-mute change what the next get-volume reports. Every call is logged with
# the fake clock at its start, the timeout it was given, and (for a set or a
# mute) the sink's REAL state just before it landed. The clock moves only by
# audio._sleep and by the time each call takes (`call_secs`); a call that
# would take its whole timeout or longer fails at the timeout, the way _run
# reports a TimeoutExpired.
#
# Outside changes, the things pin() has to survive: `external_at_sleep`
# ({sleep number: (vol, muted), or a function of (vol, muted)}) lands during
# one of pin()'s own sleeps; `after_get` (a function of the sink and the read
# it just served) lands right after a read, before pin()'s next call;
# `before_move` (a function of the sink and the argv) lands inside a
# set-volume or set-mute call, just before it applies -- the gap between
# pin()'s last read and wpctl acting, which pin() cannot see.
#
# set-volume is wpctl 0.5.17's, as its `set-volume --help` gives it and its
# source (src/tools/wpctl.c) does it:
#   - the command is argv[1] (wpctl finds it there, so an option before it
#     is refused);
#   - the command's options, `-l/--limit VALUE`, may sit after it;
#   - what is left must be ID and VOL[%][-/+];
#   - `VOL+` / `VOL-` step up/down from the level the sink is at when the
#     set lands, `VOL` sets it, a result below 0 is 0, and with -l a result
#     above the limit is the limit.
# (wpctl reads VOL with strtof, single precision; this reads it as a double.
# The difference is under 1e-8, below anything measured here.) `decimals` is
# how many places get-volume prints: 2, as wpctl does, unless a case needs
# to tell apart levels wpctl itself would print alike.
WPCTL_COMMANDS = ("status", "list", "get-volume", "inspect", "set-default", "set-volume",
                  "set-mute", "set-profile", "set-route", "clear-default", "settings",
                  "set-log-level", "reset")
WPCTL_VOL = re.compile(r"^(\d*\.?\d*)(%?)([-+]?)$")


def wpctl_set_volume_args(args):
    """(relative, value, limit) from a set-volume argv, parsed the way
    wpctl 0.5.17 parses it, or None where wpctl would refuse it."""
    rest, limit = [], 0.0
    it = iter(args[2:])
    for a in it:
        if a in ("-l", "--limit"):
            try:
                limit = float(next(it))
            except (StopIteration, ValueError):
                return None
        elif a.startswith("-") and len(a) > 1:
            return None                  # an option wpctl does not have (or -p, never used)
        else:
            rest.append(a)
    m = WPCTL_VOL.match(rest[1]) if len(rest) == 2 else None
    if not m or not m.group(1):
        return None
    value = float(m.group(1)) / (100 if m.group(2) else 1)
    return m.group(3) != "", (-value if m.group(3) == "-" else value), limit


class FakeSink:
    def __init__(self, vol=0.25, muted=False, *, fail_sets=(), fail_set_if=None,
                 fail_mute_if=None, get_fail=(), get_fail_always=False, fail_get_if=None,
                 external_at_sleep=None, after_get=None, before_move=None,
                 call_secs=0.0, decimals=2, lock=None):
        self.vol = vol
        self.muted = muted
        self.log = []
        self.t = 0.0
        self.fail_sets = set(fail_sets)        # 1-based set-volume numbers that fail
        self.fail_set_if = fail_set_if         # or a function of (sink, level it would land at)
        self.fail_mute_if = fail_mute_if       # a function of (sink, "0"|"1")
        self.get_fail = set(get_fail)          # 1-based get-volume numbers that fail
        self.get_fail_always = get_fail_always
        self.fail_get_if = fail_get_if         # or a function of the sink
        self.external_at_sleep = external_at_sleep or {}
        self.after_get = after_get
        self.before_move = before_move
        self.call_secs = call_secs
        self.decimals = decimals
        self.sets = self.gets = self.sleeps = 0
        self._lock = lock or threading.Lock()

    def now(self):
        return self.t

    def show(self):
        """The level as get-volume prints it."""
        return f"{self.vol:.{self.decimals}f}"

    def outside(self, vol, muted):
        self.vol, self.muted = vol, muted
        self.log.append({"kind": "outside", "t": self.t, "vol": vol, "muted": muted})

    def run(self, args, timeout=None):
        with self._lock:
            e = {"t": self.t, "args": list(args), "timeout": timeout}
            limit = 60.0 if timeout is None else timeout
            hung = self.call_secs >= limit
            self.t += limit if hung else self.call_secs
            if args[0] == "wpctl" and (len(args) < 2 or args[1] not in WPCTL_COMMANDS):
                e["kind"] = "refused"
                self.log.append(e)
                return 1, ""
            if args[:2] == ["wpctl", "get-volume"]:
                self.gets += 1
                if (hung or self.get_fail_always or self.gets in self.get_fail
                        or (self.fail_get_if and self.fail_get_if(self))):
                    e["kind"] = "get-fail"
                    self.log.append(e)
                    return (127 if hung else 1), ""
                shown = self.show()
                out = f"Volume: {shown}{' [MUTED]' if self.muted else ''}\n"
                e.update(kind="get", vol=self.vol, muted=self.muted, shown=float(shown))
                self.log.append(e)
                if self.after_get:
                    self.after_get(self, e)      # lands after this read, before the next call
                return 0, out
            if args[:2] == ["wpctl", "set-volume"]:
                parsed = wpctl_set_volume_args(args)
                if parsed is None:
                    e["kind"] = "refused"
                    self.log.append(e)
                    return 1, ""
                self.sets += 1
                rel, value, cap = parsed
                if self.before_move:
                    self.before_move(self, args)  # lands in the gap, before the set applies
                land = max(0.0, self.vol + value if rel else value)
                if cap > 0 and land > cap:
                    land = cap
                good = (not hung and self.sets not in self.fail_sets
                        and not (self.fail_set_if and self.fail_set_if(self, land)))
                e.update(kind="set", rel=rel, value=value, cap=cap or None, req=land, ok=good,
                         before=self.vol, before_muted=self.muted)
                if good:
                    self.vol = land
                self.log.append(e)
                return (0, "") if good else ((127 if hung else 1), "")
            if args[:2] == ["wpctl", "set-mute"]:
                if len(args) != 4 or args[3] not in ("0", "1", "toggle"):
                    e["kind"] = "refused"
                    self.log.append(e)
                    return 1, ""
                if self.before_move:
                    self.before_move(self, args)
                good = not hung and not (self.fail_mute_if and self.fail_mute_if(self, args[3]))
                e.update(kind="mute", ok=good, before=self.vol, before_muted=self.muted)
                if good:
                    self.muted = (not self.muted) if args[3] == "toggle" else args[3] == "1"
                self.log.append(e)
                return (0, "") if good else ((127 if hung else 1), "")
            e["kind"] = "other"
            self.log.append(e)
            if hung:
                return 127, ""
            if args[:2] == ["pactl", "get-default-sink"]:
                return 0, SINK + "\n"
            if args[:3] == ["pactl", "--format=json", "list"]:
                return 0, json.dumps(SINKS)
            return 0, ""

    def sleep(self, secs):
        with self._lock:
            self.sleeps += 1
            self.log.append({"kind": "sleep", "t": self.t, "secs": secs})
            self.t += secs
            change = self.external_at_sleep.get(self.sleeps)
            if change is not None:
                self.outside(*(change(self.vol, self.muted) if callable(change) else change))


def use(sink):
    audio._run, audio._sleep, audio._now = sink.run, sink.sleep, sink.now


def drive(sink):
    use(sink)
    return audio.pin()


def _db(v):
    """dB on wpctl's cubic scale, mirrored rather than imported, so a bug in
    lib/audio.py cannot cancel out a bug here. Silence is -inf."""
    return float("-inf") if v <= 0 else 60.0 * math.log10(v)


def calls(sink):
    return [e for e in sink.log if e["kind"] != "outside"]


def reads(sink):
    return [e for e in sink.log if e["kind"] == "get"]


def audible_steps(sink):
    """Every set-volume that landed while the sink was really unmuted,
    measured from the sink's REAL level just before it -- not from what
    pin() read, and not from what it meant to set. `floor_jump` marks the
    one step the rulings exempt from the 3 dB ceiling: an absolute set from
    below the floor to exactly the floor, where nothing is loud enough to
    startle anybody."""
    return [{"t": e["t"], "before": e["before"], "after": e["req"], "rel": e["rel"],
             "delta_db": _db(e["req"]) - _db(e["before"]),
             "floor_jump": not e["rel"] and e["req"] == FLOOR and e["before"] < FLOOR}
            for e in sink.log if e["kind"] == "set" and e["ok"] and not e["before_muted"]]


def ramp_steps(sink):
    return [s for s in audible_steps(sink) if not s["floor_jump"]]


def floor_jumps(sink):
    return [s for s in audible_steps(sink) if s["floor_jump"]]


def worst_step(steps):
    return max((s["delta_db"] for s in steps), default=0.0)


def steps_ok(steps):
    return all(s["delta_db"] <= MAX_STEP_DB + 1e-6 for s in steps)


def is_unmute(e):
    return e["kind"] == "mute" and e["args"][3] == "0"


def is_up_move(e):
    """A call that can make the sink louder: any set-volume, or an unmute.
    A mute only ever silences it, so pin() may mute on less than two reads."""
    return e["kind"] == "set" or is_unmute(e)


def unmutes(sink):
    """The sink's REAL level at every unmute."""
    return [e["before"] for e in sink.log if is_unmute(e)]


def gaps_ok(sink):
    """Every call that can make it louder -- an unmute, or a set-volume while
    the sink is really unmuted, landed or not -- comes at least 100 ms after
    the last one. A set made while muted is inaudible, and a mute is quieter."""
    times = [e["t"] for e in sink.log
             if is_unmute(e) or (e["kind"] == "set" and not e["before_muted"])]
    return all(b - a >= MIN_GAP - 1e-9 for a, b in zip(times, times[1:]))


def unread_moves(sink):
    """Every set-volume or unmute NOT made on the strength of two successful
    reads in a row, showing the same thing, the second one immediately
    before it. Empty is right: nothing is ever set after a failed read, on a
    remembered level, or on a read something else has since overtaken."""
    cs = calls(sink)
    bad_at = []
    for i, e in enumerate(cs):
        if is_up_move(e):
            a, b = (cs[i - 2], cs[i - 1]) if i >= 2 else (None, None)
            if not (a and a["kind"] == "get" and b["kind"] == "get"
                    and (a["shown"], a["muted"]) == (b["shown"], b["muted"])):
                bad_at.append((i, e["kind"], e.get("req")))
    return bad_at


def moves_after_failed_read(sink):
    cs = calls(sink)
    return [(i, e["kind"]) for i, e in enumerate(cs)
            if is_up_move(e) and i > 0 and cs[i - 1]["kind"] == "get-fail"]


def refused(sink):
    """Calls wpctl itself would have refused: a wrong argv."""
    return [e["args"] for e in sink.log if e["kind"] == "refused"]


def steps_not_relative(sink):
    """Every set-volume made on an unmuted sink from at or above the floor
    must be wpctl's relative step under -l 1.00; only the floor itself (from
    below it, or while muted) is an absolute set."""
    return [(e["args"][2:], e["before"]) for e in sink.log
            if e["kind"] == "set" and not e["before_muted"] and e["before"] >= FLOOR
            and not (e["rel"] and e["value"] >= 0 and e["cap"] == 1.0)
            and not (not e["rel"] and e["req"] == FLOOR)]


def honest(result, sink, target=1.0):
    """pinned: true is allowed only when the sink, read right now, shows the
    target, unmuted -- and the result reports what the sink shows. Checked
    against the fake sink's own state, never against the result's claim."""
    if not result.get("pinned"):
        return True
    shown = float(sink.show())
    return (not sink.muted and abs(_db(shown) - _db(target)) <= 0.1 + 1e-9
            and result.get("volume") == shown)


def moves(sink, after=-1):
    """(kind, level) of every set and mute logged after sink.log[after]."""
    return [(e["kind"], e.get("req")) for e in sink.log[after + 1:] if e["kind"] in ("set", "mute")]


def few(items):
    """A list as (how many, the first three), so a failure says how bad
    without printing all of it."""
    return len(items), items[:3]


def ramp_checks(name, sink, result, *, pinned=True):
    """What every ordinary case must show."""
    check(f"{name}: pinned" if pinned else f"{name}: not pinned", result["pinned"], pinned)
    check(f"{name}: and honestly -- the sink itself agrees", honest(result, sink), True)
    steps = ramp_steps(sink)
    check(f"{name}: every step from the sink's real level (n={len(steps)}) is <= 3.0 dB "
          f"(worst {worst_step(steps):.3f})", steps_ok(steps), True)
    check(f"{name}: audible moves are >= 100 ms apart", gaps_ok(sink), True)
    check(f"{name}: every move was made on two matching reads, the second "
          "immediately before it", few(unread_moves(sink)), (0, []))
    check(f"{name}: every step from the floor up is wpctl's relative step under -l 1.00, "
          "and wpctl would have taken every call", (few(steps_not_relative(sink)), refused(sink)),
          ((0, []), []))


head("the floor is the ruled 0.13 (-53.2 dB)")
check("FLOOR_V", audio.FLOOR_V, FLOOR)


head("the fake's set-volume is wpctl 0.5.17's: relative steps, -l, and its argv")
# One sink, the calls in turn: each starts from where the one before left it.
_w = FakeSink(vol=0.50)
for argv, want in (
        (["wpctl", "set-volume", "-l", "1.00", "@DEFAULT_AUDIO_SINK@", "0.09+"], 0.59),
        (["wpctl", "set-volume", "-l", "1.00", "@DEFAULT_AUDIO_SINK@", "0.60+"], 1.0),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.60+"], 1.6),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.20-"], 1.4),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"], 1.35),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "2-"], 0.0),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.40"], 0.40),
        (["wpctl", "set-volume", "-l", "0.30", "@DEFAULT_AUDIO_SINK@", "0.40"], 0.30),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.30+", "-l", "0.45"], 0.45),
        (["wpctl", "-l", "1.00", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.10+"], "refused"),
        (["wpctl", "set-volume", "-l", "1.00", "0.10+", "@DEFAULT_AUDIO_SINK@"], "refused"),
        (["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@"], "refused")):
    _rc, _ = _w.run(argv, 1)
    got = "refused" if _w.log[-1]["kind"] == "refused" else round(_w.vol, 4)
    check(f"{' '.join(argv[1:])}: {want}", (got, _rc == 0), (want, want != "refused"))
_cap = []
_c = FakeSink(vol=0.91, before_move=lambda s, a: _cap.append(list(a)))
use(_c)
audio._step(audio._step_up(0.91, 1.0), 1.0)
check("pin()'s step is sent in that order: the command, -l and the target, the sink, then Δ+",
      _cap, [["wpctl", "set-volume", "-l", "1.00", "@DEFAULT_AUDIO_SINK@", "0.09+"]])


head("apply() touches the volume only where it was asked to")
sink0 = FakeSink(vol=0.25)
use(sink0)
st0 = audio.apply()
check("not asked (unmanaged): nothing is set",
      (moves(sink0), st0["managed"], st0["aux"], st0["volume"]),
      ([], False, True, 0.25))
audio.set_managed(True)
sink1 = FakeSink(vol=1.0)
use(sink1)
audio.apply()
check("already at 100%, unmuted: nothing is set", moves(sink1), [])
sink2 = FakeSink(vol=0.25)
use(sink2)
st2 = audio.apply()
check("from 25%: apply() ramps, and reports the level read after the ramp, "
      "with the port it read before it",
      (st2["volume"], st2["muted"], st2["aux"], st2["error"], round(sink2.vol, 2)),
      (1.0, False, True, None, 1.0))
audio.set_managed(False)
check("and off again", audio.managed(), False)
audio.set_managed(True)      # left managed for the sections below


head("A: from 25% unmuted")
sinkA = FakeSink(vol=0.25)
resultA = drive(sinkA)
ramp_checks("A", sinkA, resultA)
check("A: no floor-jump -- it started above the floor", floor_jumps(sinkA), [])
check("A: no mute traffic -- it was never muted", unmutes(sinkA), [])


head("B: from MUTED at 100% -- floor before unmute, never heard at 0 dB")
sinkB = FakeSink(vol=1.0, muted=True)
resultB = drive(sinkB)
ramp_checks("B", sinkB, resultB)
check("B: unmutes exactly once, with the sink really at the floor",
      unmutes(sinkB), [FLOOR])
check("B: no below-floor jump -- it was unmuted at the floor", floor_jumps(sinkB), [])

head("B2: muted at 100%, and the floor call itself fails once")
sinkB2 = FakeSink(vol=1.0, muted=True, fail_sets={1})
resultB2 = drive(sinkB2)
ramp_checks("B2", sinkB2, resultB2)
check("B2: still unmutes exactly once, with the sink really at the floor",
      unmutes(sinkB2), [FLOOR])


head("C: from Volume: 0.00, unmuted -- completes, and does not hang")
sinkC = FakeSink(vol=0.0)
resultC = drive(sinkC)
ramp_checks("C", sinkC, resultC)
check("C: within the 40-iteration bound", resultC["iterations"] <= ITERATIONS, True)
check("C: one jump, from nothing to the floor",
      [(s["before"], s["after"]) for s in floor_jumps(sinkC)], [(0.0, FLOOR)])


head("D: random set-volume failures (seeded) -- a failure costs a step, never a jump")
rng = random.Random(20260930)
fail_sets_D = {i for i in range(1, 30) if rng.random() < 0.4}
sinkD = FakeSink(vol=0.25, fail_sets=fail_sets_D)
resultD = drive(sinkD)
failedD = [e for e in sinkD.log if e["kind"] == "set" and not e["ok"]]
check(f"D: {len(failedD)} set-volume calls really failed on the way",
      len(failedD) > 0, True)
ramp_checks("D", sinkD, resultD)


head("E: outside changes that are not a rise over 3 dB -- a drop, and a one-tick "
     "rise -- are absorbed; each step is sized from a fresh read")
sinkE = FakeSink(vol=0.25, external_at_sleep={
    3: (0.15, False),
    7: lambda v, m: (round(v + 0.01, 2), m)})
resultE = drive(sinkE)
ramp_checks("E", sinkE, resultE)
check("E: both outside changes happened",
      len([e for e in sinkE.log if e["kind"] == "outside"]), 2)


head("H, I, J, J0: from below the floor -- one jump to the floor, then an ordinary ramp")
# J0 starts at exactly 0.002. wpctl prints that as 0.00, the same text as C,
# so J0's fake prints three decimals: its read shows 0.002, and pin() has to
# act on a level that is not zero.
readsJ0 = None
for name, start, first_read, places in (("H", 0.05, 0.05, 2), ("I", 0.01, 0.01, 2),
                                        ("J", 0.0051, 0.01, 2), ("J0", 0.002, 0.002, 3)):
    sinkX = FakeSink(vol=start, decimals=places)
    resultX = drive(sinkX)
    ramp_checks(name, sinkX, resultX)
    check(f"{name}: exactly one floor-jump, from the real {start} to the floor",
          [(s["before"], s["after"]) for s in floor_jumps(sinkX)], [(start, FLOOR)])
    check(f"{name}: its first read shows {first_read}", reads(sinkX)[0]["shown"], first_read)
    if name == "J0":
        readsJ0 = reads(sinkX)
check("J is not C: C's first read shows 0.00, J's 0.01 -- 0.0051 is the quietest "
      "level wpctl prints as non-zero",
      (reads(sinkC)[0]["shown"], FakeSink(vol=0.0051).run(["wpctl", "get-volume", "x"], 1)[1]),
      (0.0, "Volume: 0.01\n"))
check("J0 is not C either: J0's first read shows 0.002, not 0.00",
      (readsJ0[0]["shown"] if readsJ0 else None, reads(sinkC)[0]["shown"]), (0.002, 0.0))


head("K: off the 0.01 grid -- a read can hide up to 0.005 below it, so steps are "
     "sized from read - 0.005 (the re-review's worst starts, then every start "
     "from 0.0950 to 1.0000 in steps of 0.0001)")
for start in (0.1051, 0.1150, 0.1950, 0.0950):
    sinkK = FakeSink(vol=start)
    resultK = drive(sinkK)
    stepsK = audible_steps(sinkK)
    firstK = stepsK[0] if stepsK else {"floor_jump": None, "delta_db": float("nan")}
    problems = [what for what, good in (
        ("the first move is not the expected kind", firstK["floor_jump"] == (start < FLOOR)),
        ("a step over 3.0 dB", steps_ok(ramp_steps(sinkK))),
        ("not pinned", resultK["pinned"]),
        ("pinned dishonestly", honest(resultK, sinkK))) if not good]
    check(f"K {start} ({_db(start):.1f} dB, reads {reads(sinkK)[0]['shown']}): "
          f"its first move is {'a floor-jump' if start < FLOOR else 'a ramp step'} "
          f"of {firstK['delta_db']:.2f} dB, then every step <= 3.0 dB "
          f"(worst {worst_step(ramp_steps(sinkK)):.3f}), pinned honestly", problems, [])
sweep_bad = []
sweep_worst = (0.0, None)
for i in range(950, 10001):
    start = i / 10000
    sinkK = FakeSink(vol=start)
    resultK = drive(sinkK)
    stepsK = ramp_steps(sinkK)
    w = worst_step(stepsK)
    if w > sweep_worst[0]:
        sweep_worst = (w, start)
    if not (resultK["pinned"] and honest(resultK, sinkK) and steps_ok(stepsK)
            and not unread_moves(sinkK) and len(floor_jumps(sinkK)) <= 1):
        sweep_bad.append(start)
check(f"K: all 9051 starts pinned honestly, every step <= 3.0 dB from the real "
      f"level (worst {sweep_worst[0]:.3f} dB, from {sweep_worst[1]})",
      few(sweep_bad), (0, []))


head("L: an outside DROP between the read a step is decided from and its set "
     "(the re-review's P2: a read at 0.80 or above, then the route restores 0.25) "
     "-- no set")
dropped = []


def drop_after_decision(sink, e):
    if not dropped and not e["muted"] and e["shown"] >= 0.80:
        dropped.append(len(sink.log) - 1)
        sink.outside(0.25, False)


sinkL = FakeSink(vol=0.25, after_get=drop_after_decision)
resultL = drive(sinkL)
check("L: the drop happened", len(dropped), 1)


def reads_before(sink, entry):
    """What the two calls just before `entry` read."""
    cs = calls(sink)
    i = next(k for k, c in enumerate(cs) if c is entry)
    return [cs[k].get("shown") for k in (i - 2, i - 1)] if i >= 2 else None


setsL = [e for e in sinkL.log[dropped[0] + 1:] if e["kind"] == "set"] if dropped else []
check("L: no set was made on that read: the next set came only after two "
      "reads showing 0.25", reads_before(sinkL, setsL[0]) if setsL else None, [0.25, 0.25])
ramp_checks("L", sinkL, resultL)


head("L2: a ONE-TICK drop between those reads also stops the set -- the step "
     "was sized for the higher read, so it is decided again from the lower one")
ticked = []


def tick_down(sink, e):
    if not ticked and e["shown"] == 0.20:
        ticked.append(1)
        sink.outside(0.1851, False)


sinkL2 = FakeSink(vol=0.20, after_get=tick_down)
resultL2 = drive(sinkL2)
setsL2 = [e for e in sinkL2.log if e["kind"] == "set" and e["before"] == 0.1851]
check("L2: the one step taken from the real 0.1851 was made on two reads showing 0.19, "
      "and was 0.01, landing at 0.1951",
      ([reads_before(sinkL2, e) for e in setsL2], [(e["value"], round(e["req"], 4)) for e in setsL2]),
      ([[0.19, 0.19]], [(0.01, 0.1951)]))
ramp_checks("L2", sinkL2, resultL2)


head("M: an outside RISE while muted, between the read that confirms the floor "
     "and the unmute (the re-review's P3) -- no unmute at 0 dB")
raised = []


def raise_while_muted(sink, e):
    if not raised and e["muted"] and e["shown"] <= FLOOR:
        raised.append(1)
        sink.outside(1.0, True)


sinkM = FakeSink(vol=1.0, muted=True, after_get=raise_while_muted)
resultM = drive(sinkM)
check("M: the rise happened", len(raised), 1)
check("M: it unmuted exactly once, with the sink really at the floor",
      unmutes(sinkM), [FLOOR])
ramp_checks("M", sinkM, resultM)


head("W: an outside DROP inside the gap no read can see -- after pin()'s last read, "
     "just before wpctl applies the step. The step is added to the dropped level: "
     f"at most {DELTA_MAX} above it, never above 1.00 (a drop to the floor: at most "
     f"+{GAP_WORST_DB:.2f} dB)")


def gap_run(start, to=None, rise=None):
    """pin() from `start`, with one outside change landing inside the first
    step's own call -- the first set-volume on the unmuted sink from the
    floor up, however it is written -- before it applies: a drop to `to`, or
    a rise to `rise`. Returns the sink, the result, and that step's log
    entry (None if no such step was made)."""
    hit = []

    def hook(sink, args):
        if not hit and args[1] == "set-volume" and not sink.muted and sink.vol >= FLOOR - 1e-9:
            hit.append(len(sink.log))
            sink.outside(to if rise is None else rise, False)

    sink = FakeSink(vol=start, before_move=hook)
    result = drive(sink)
    step = next((e for e in sink.log[hit[0]:] if e["kind"] == "set"), None) if hit else None
    return sink, result, step


gap_bad = []
gap_worst = {}                  # drop level -> (worst landing - drop, worst dB, from start)
biggest = 0.0
for i in range(13, 100):
    start = i / 100
    for to in (FLOOR, 0.25, 0.05, 0.0):
        if to >= start:
            continue            # not a drop
        sinkP, resultP, stepP = gap_run(start, to)
        if stepP is None:
            gap_bad.append((start, to, "no step in the gap"))
            continue
        rise = stepP["req"] - to
        rise_db = _db(stepP["req"]) - _db(to) if to > 0 else float("inf")
        biggest = max(biggest, stepP["value"] if stepP["rel"] else 0.0)
        if rise > gap_worst.get(to, (-1,))[0]:
            gap_worst[to] = (rise, rise_db, start)
        others = [{"delta_db": _db(e["req"]) - _db(e["before"])} for e in sinkP.log
                  if e is not stepP and e["kind"] == "set" and e["ok"] and not e["before_muted"]
                  and not (not e["rel"] and e["req"] == FLOOR and e["before"] < FLOOR)]
        problems = [what for what, good in (
            (f"more than {DELTA_MAX} above the dropped level", rise <= DELTA_MAX + 1e-9),
            ("above 1.00", stepP["req"] <= 1.0 + 1e-12),
            (f"more than {GAP_WORST_DB:.2f} dB over a drop to the floor or above",
             to < FLOOR or rise_db <= GAP_WORST_DB + 1e-9),
            ("not pinned afterwards, or not honestly",
             resultP["pinned"] and honest(resultP, sinkP)),
            ("another step over 3.0 dB", steps_ok(others)),
            ("a move not made on two matching reads", not unread_moves(sinkP)),
            ("a call wpctl would refuse", not refused(sinkP))) if not good]
        if problems:
            gap_bad.append((start, to, problems))
check(f"W: from every read 0.13-0.99, a drop to 0.13, 0.25, 0.05 or 0.00 just before the "
      f"step: it lands at most {DELTA_MAX} above the drop and never above 1.00, then pins "
      "honestly", few(gap_bad), (0, []))
check(f"W: and the largest step any read makes is exactly {DELTA_MAX}, so that bound is "
      "reached, not just respected", biggest, DELTA_MAX)
worst_floor = gap_worst.get(FLOOR, (0, 0, None))
check(f"W: the worst drop to the floor: 0.13 -> {FLOOR + worst_floor[0]:.2f}, "
      f"+{worst_floor[1]:.2f} dB (from a read of {worst_floor[2]}), which is the computed "
      f"+{GAP_WORST_DB:.2f} dB", round(worst_floor[1], 6), round(GAP_WORST_DB, 6))
sinkP1, resultP1, stepP1 = gap_run(0.91, 0.25)
check("W1: the re-review's probe -- a read of 0.91, then a drop to 0.25 in the gap: "
      "0.25 -> 0.34, +8.0 dB (a plain set to 1.00 there was +36.1 dB)",
      (round(stepP1["req"], 4), round(_db(stepP1["req"]) - _db(0.25), 1)) if stepP1 else None,
      (0.34, 8.0))
sinkP3 = FakeSink(vol=1.20)
resultP3 = drive(sinkP3)
check("W2: from 120%, above the target: one step of 0.00+ under -l 1.00 brings it to "
      "exactly 1.00, and pins",
      ([(e["args"][2:], e["req"]) for e in sinkP3.log if e["kind"] == "set"], resultP3["pinned"],
       honest(resultP3, sinkP3)),
      ([(["-l", "1.00", "@DEFAULT_AUDIO_SINK@", "0.00+"], 1.0)], True, True))
sinkP4, resultP4, stepP4 = gap_run(1.20, 0.25)
check("W3: and a drop to 0.25 in that step's gap stays at 0.25 -- nothing is added -- "
      "then it ramps from there and pins honestly",
      (stepP4 and (stepP4["before"], stepP4["req"]), resultP4["pinned"], honest(resultP4, sinkP4),
       steps_ok(ramp_steps(sinkP4))),
      ((0.25, 0.25), True, True, True))
sinkP2, resultP2, stepP2 = gap_run(0.50, rise=0.98)
check("W4: a RISE to 0.98 in the same gap: the step stops at 1.00, not 1.03, and pin() "
      "then stops, not pinned, naming the outside change",
      (stepP2 and stepP2["req"], resultP2["pinned"], "outside change" in (resultP2["error"] or "")),
      (1.0, False, True))


head("S: an outside RISE while muted, inside the gap before the unmute -- pin() reads "
     "straight after unmuting, mutes again, and starts over from the floor")
BOX_CALL = 0.009                 # the box's median wpctl call


def rise_before_first_unmute(to):
    done = []

    def hook(sink, args):
        if not done and args[1] == "set-mute" and args[3] == "0":
            done.append(1)
            sink.outside(to, True)
    return hook


def after_first_unmute(sink):
    """The calls straight after the first unmute, and the time from that
    unmute to the first mute after it."""
    cs = calls(sink)
    i = next((k for k, e in enumerate(cs) if is_unmute(e)), len(cs))
    j = next((k for k in range(i + 1, len(cs))
              if cs[k]["kind"] == "mute" and cs[k]["args"][3] == "1"), None)
    return i, cs, (None if j is None else cs[j]["t"] - cs[i]["t"])


sinkS = FakeSink(vol=1.0, muted=True, before_move=rise_before_first_unmute(1.0),
                 call_secs=BOX_CALL)
resultS = drive(sinkS)
iS, csS, exposedS = after_first_unmute(sinkS)
check("S: the first unmute exposed the outside 1.00; the second came with the sink at the floor",
      unmutes(sinkS), [1.0, FLOOR])
check("S: straight after that unmute: one read (showing 1.00 unmuted), then the mute",
      [(c["kind"], c.get("shown"), c.get("muted"), c["args"][3] if c["kind"] == "mute" else None)
       for c in csS[iS + 1:iS + 3]],
      [("get", 1.0, False, None), ("mute", None, None, "1")])
check(f"S: heard for two wpctl calls -- the read, then the mute's own start-up -- "
      f"{2 * BOX_CALL * 1000:.0f} ms at the box's median", round(exposedS or -1, 9),
      round(2 * BOX_CALL, 9))
ramp_checks("S", sinkS, resultS)

sinkS2 = FakeSink(vol=1.0, muted=True, before_move=rise_before_first_unmute(1.0),
                  fail_mute_if=lambda s, v: v == "1")
resultS2 = drive(sinkS2)
iS2, csS2, _ = after_first_unmute(sinkS2)
check("S2: the mute after it fails: not pinned, says so, reports what the sink shows, "
      "and moves nothing after",
      (resultS2["pinned"], "muting again failed" in (resultS2["error"] or ""),
       resultS2["volume"], sinkS2.muted, [c["kind"] for c in csS2[iS2 + 1:]]),
      (False, True, 1.0, False, ["get", "mute"]))

failed_after = []


def fail_read_after_unmute(sink):
    last = next((e for e in reversed(sink.log) if e["kind"] != "outside"), None)
    if not failed_after and last is not None and is_unmute(last):
        failed_after.append(1)
        return True
    return False


sinkS3 = FakeSink(vol=1.0, muted=True, fail_get_if=fail_read_after_unmute)
resultS3 = drive(sinkS3)
iS3, csS3, _ = after_first_unmute(sinkS3)
check("S3: the read straight after the unmute fails: nothing shows the level is safe, so it "
      "mutes again, then unmutes at the floor once more",
      ([c["kind"] for c in csS3[iS3 + 1:iS3 + 3]], unmutes(sinkS3)),
      (["get-fail", "mute"], [FLOOR, FLOOR]))
ramp_checks("S3", sinkS3, resultS3)


head("U: MUTED part-way up (0.60, 0.14) -- the floor comes before the unmute from "
     "any level, not only from 100%")
for start in (0.60, 0.14):
    sinkU = FakeSink(vol=start, muted=True)
    resultU = drive(sinkU)
    check(f"U {start}: unmuted exactly once, with the sink really at the floor (0.13) -- "
          "never above it", unmutes(sinkU), [FLOOR])
    ramp_checks(f"U {start}", sinkU, resultU)


head("N: an outside RISE of more than 3 dB -- pin() takes no further step, and "
     "says an outside change interfered")
sinkN = FakeSink(vol=0.25, external_at_sleep={3: (0.60, False)})
resultN = drive(sinkN)
riseN = next(i for i, e in enumerate(sinkN.log) if e["kind"] == "outside")
check("N: not pinned, and the error says why",
      (resultN["pinned"], "outside change" in (resultN["error"] or "")), (False, True))
check("N: nothing set or unmuted after the rise", few(moves(sinkN, riseN)), (0, []))
check("N: it reports the level it found", resultN["volume"], 0.60)
check("N: and the sink is where the outside change left it", sinkN.vol, 0.60)
ramp_checks("N", sinkN, resultN, pinned=False)

sinkN2 = FakeSink(vol=0.40, after_get=lambda s, e: (
    s.outside(1.0, False) if s.gets == 1 else None))
resultN2 = drive(sinkN2)
check("N2: a rise to 100% between the decision read and the set: no set at all, "
      "and not pinned",
      (few(moves(sinkN2)), resultN2["pinned"], "outside change" in (resultN2["error"] or "")),
      ((0, []), False, True))

sinkN3 = FakeSink(vol=1.0, muted=True, after_get=lambda s, e: (
    s.outside(1.0, False) if s.gets == 1 else None))
resultN3 = drive(sinkN3)
check("N3: an outside unmute at 100% is a rise from silence: nothing set, not pinned",
      (few(moves(sinkN3)), resultN3["pinned"], "outside change" in (resultN3["error"] or "")),
      ((0, []), False, True))


head("O: read failures, intermittent, with an outside drop landing among them "
     "-- nothing is ever set on a failed read or a remembered level")
rngO = random.Random(20260928)
sinkO = FakeSink(vol=0.80, get_fail={g for g in range(1, 400) if rngO.random() < 0.3},
                 external_at_sleep={2: (0.25, False)})
resultO = drive(sinkO)
check("O: some reads failed", sum(e["kind"] == "get-fail" for e in sinkO.log) > 10, True)
check("O: no set or mute directly after a failed read", few(moves_after_failed_read(sinkO)), (0, []))
check("O: and honestly", honest(resultO, sinkO), True)
stepsO = ramp_steps(sinkO)
check(f"O: every step from the sink's real level (n={len(stepsO)}) is <= 3.0 dB "
      f"(worst {worst_step(stepsO):.3f})", steps_ok(stepsO), True)
check("O: every move was made on two matching reads", few(unread_moves(sinkO)), (0, []))


head("G: a read that fails every time -- nothing is set, and it gives up at "
     "exactly 40 iterations")
sinkG = FakeSink(vol=0.25, get_fail_always=True)
resultG = drive(sinkG)
check("G: not pinned, with an error",
      (resultG["pinned"], resultG["error"] is not None), (False, True))
check("G: nothing set or unmuted, ever", few(moves(sinkG)), (0, []))
check("G: exactly 40 iterations", resultG["iterations"], ITERATIONS)


head("Q: the final set to 100% fails once -- pinned only once a read shows it landed")
failed_final = []


def fail_first_full(sink, req):
    if req >= 1.0 and not failed_final:
        failed_final.append(1)
        return True
    return False


sinkQ = FakeSink(vol=0.90, fail_set_if=fail_first_full)
resultQ = drive(sinkQ)
check("Q: that set really failed", len(failed_final), 1)
ramp_checks("Q", sinkQ, resultQ)

head("R: every set to 100% fails -- never pinned, and says so")
sinkR = FakeSink(vol=0.90, fail_set_if=lambda s, req: req >= 1.0)
resultR = drive(sinkR)
check("R: not pinned, with an error", (resultR["pinned"], resultR["error"] is not None),
      (False, True))
check("R: and honestly: the sink really is short of 100%",
      (honest(resultR, sinkR), float(f"{sinkR.vol:.2f}") < 1.0), (True, True))
check("R: the result reports what the sink shows",
      resultR["volume"], float(f"{sinkR.vol:.2f}"))


head("T: the time bounds -- with slow or hung wpctl, no iteration starts after "
     "7 s, no call starts after 8 s, and pin() ends within 8 s + one 1 s timeout")


def decision_starts(sink):
    """When each iteration's first call began: a read that follows a sleep,
    a set, a mute, or nothing -- but not the read straight after an unmute,
    which belongs to the unmute's own iteration."""
    cs = calls(sink)
    return [e["t"] for i, e in enumerate(cs)
            if e["kind"] in ("get", "get-fail")
            and (i == 0 or (cs[i - 1]["kind"] in ("sleep", "set", "mute")
                            and not is_unmute(cs[i - 1])))]


# The last case puts the unmute's check read at 7.13 s: an unmute then would
# start past 7 s, and the read and the mute it can bring would start past 8 s.
T_CASES = [(secs, vol, muted, ()) for secs in (float("inf"), 0.99, 0.7, 0.5, 0.3)
           for vol, muted in ((0.25, False), (1.0, True))] + [(0.99, 1.0, True, (1, 2))]
for secs, start_vol, start_muted, get_fail in T_CASES:
    sinkT = FakeSink(vol=start_vol, muted=start_muted, call_secs=secs, get_fail=get_fail)
    resultT = drive(sinkT)
    tool_calls = [e for e in calls(sinkT) if e["kind"] != "sleep"]
    label = f"T: {'hung' if secs == float('inf') else f'{secs} s'} wpctl, from " \
            f"{start_vol}{' muted' if start_muted else ''}" \
            f"{', the first two reads failing' if get_fail else ''}"
    problems = [what for what, good in (
        ("ended after 9 s", sinkT.t <= MAX_SECS + TOOL_TIMEOUT + 1e-9),
        ("an iteration started at 7 s or later",
         all(t < LAST_START for t in decision_starts(sinkT))),
        ("a call started at 8 s or later", all(e["t"] < MAX_SECS for e in tool_calls)),
        ("a call without a 1 s timeout",
         all(e["timeout"] is not None and e["timeout"] <= TOOL_TIMEOUT for e in tool_calls)),
        ("claimed pinned", not resultT["pinned"]),
        ("a step over 3.0 dB", steps_ok(ramp_steps(sinkT)))) if not good]
    check(f"{label}: took {sinkT.t:.2f} s -- ends by 9 s, no iteration starts at 7 s "
          f"or later, no call at 8 s or later, every call has a 1 s timeout, not "
          f"pinned", problems, [])


head("V: apply() fits inside the page's abort, with slow or hung tools")
m = re.search(r"APPLY_TIMEOUT_MS\s*=\s*(\d+)",
              open(os.path.join(ROOT, "share", "js", "audiostate.js"), encoding="utf-8").read())
page_secs = int(m.group(1)) / 1000 if m else None
check(f"V: the page's abort ({page_secs} s) leaves at least {PAGE_MARGIN:g} s over "
      f"apply()'s {APPLY_WORST:g} s worst case",
      page_secs is not None and page_secs >= APPLY_WORST + PAGE_MARGIN, True)
for secs in (float("inf"), 0.99, 0.7, 0.3):
    sinkV = FakeSink(vol=0.25, call_secs=secs)
    use(sinkV)
    stV = audio.apply()
    tool_calls = [e for e in calls(sinkV) if e["kind"] != "sleep"]
    problems = [what for what, good in (
        (f"took over {APPLY_WORST:g} s", sinkV.t <= APPLY_WORST + 1e-9),
        ("a call without a 1 s timeout",
         all(e["timeout"] is not None and e["timeout"] <= TOOL_TIMEOUT for e in tool_calls)))
        if not good]
    check(f"V: {'hung' if secs == float('inf') else f'{secs} s'} tools: apply() took "
          f"{sinkV.t:.2f} s -- within {APPLY_WORST:g} s, every call with a 1 s timeout",
          problems, [])


head("F: two pin() calls at once -- the second returns busy and changes "
     "nothing, and the combined sequence still obeys the limit")
sinkF = FakeSink(vol=1.0, muted=True)             # the longest ramp (floor, then all the way up):
                                                   # a wide real-time window for B to contend the lock
started = threading.Event()
real_run = sinkF.run


def gated_run(args, timeout=None):
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
    audio._run, audio._sleep, audio._now = gated_run, slow_sleep, sinkF.now
    resultsF["A"] = audio.pin()


def run_B():
    started.wait(timeout=5)     # A has made its first real wpctl call: it holds the lock
    audio._run, audio._sleep, audio._now = gated_run, slow_sleep, sinkF.now
    resultsF["B"] = audio.pin()


tA = threading.Thread(target=run_A)
tB = threading.Thread(target=run_B)
tA.start()
tB.start()
tA.join(timeout=20)
tB.join(timeout=20)
check("F: A actually ran the ramp and pinned it", resultsF.get("A", {}).get("pinned"), True)
check("F: and honestly", honest(resultsF.get("A", {}), sinkF), True)
check("F: B found the lock held and changed nothing",
      (resultsF.get("B", {}).get("pinned"), resultsF.get("B", {}).get("busy")), (False, True))
check("F: B's own result shows it never ran a single iteration -- it "
      "returned at the lock, before ever touching the sink",
      resultsF.get("B", {}).get("iterations"), 0)
stepsF = ramp_steps(sinkF)
check(f"F: even with two callers in the picture, every step "
      f"(n={len(stepsF)}) still obeys <= 3.0 dB", steps_ok(stepsF), True)


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
