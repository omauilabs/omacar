#!/usr/bin/env python3
"""The Surface's volume, held at 100% where it was asked for and nowhere else:
reading wpctl and pactl, naming the port, and apply()/pin() deciding whether
and how to touch anything. wpctl never runs for real here -- _run (and, for
pin()'s ramp, _sleep) are replaced -- so this suite cannot turn anybody's
speakers up, and cannot spend real wall-clock time doing it either.

Fix round 1 added the pin()/_sleep/isolation sections below: the review found
that pin() moved the sink from wherever it was to 100% in one wpctl call --
on wpctl's cubic scale, +36 dB from 25% -- repeated every 30 s by apply(),
which broke the plan's "nothing sets a level in one step" rule at the one
place it could actually startle a driver. See lib/audio.py's pin() docstring
and header comment for the fix.
"""

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
SCRATCH = tempfile.mkdtemp(prefix="omacar-audio-test-")
os.environ["XDG_CONFIG_HOME"] = SCRATCH

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


# A fake clock, the same idea as the fake wpctl below: audio._sleep is a
# swappable hook exactly like audio._run, so the ramp's pacing can be
# checked without this suite actually spending a second and a half asleep
# on every call to pin().
def _steps_db(volume_calls):
    """dB deltas between consecutive set-volume calls, on wpctl's own cubic
    scale (dB = 60*log10(v)) -- the scale the plan's "3 dB per 100 ms" is
    about, not the raw 0..1 number wpctl takes."""
    vols = [float(c[3]) for c in volume_calls]
    dbs = [60 * math.log10(v) for v in vols]
    return [dbs[i + 1] - dbs[i] for i in range(len(dbs) - 1)]


head("apply() touches the volume only where it was asked to")
VOL = ["Volume: 0.25\n"]
CALLS = []
SLEEPS = []


def fake(args, timeout=5):
    CALLS.append(list(args))
    if args[:2] == ["wpctl", "get-volume"]:
        return 0, VOL[0]
    if args[:2] == ["pactl", "get-default-sink"]:
        return 0, SINK + "\n"
    if args[:3] == ["pactl", "--format=json", "list"]:
        return 0, json.dumps(SINKS)
    return 0, ""


def fake_sleep(secs):
    SLEEPS.append(secs)


audio._run = fake
audio._sleep = fake_sleep
SETS = lambda: [c for c in CALLS if len(c) > 1 and c[1] in ("set-mute", "set-volume")]  # noqa: E731
_st = audio.apply()
check("not asked: nothing is set", (SETS(), _st["managed"], _st["aux"], _st["volume"]), ([], False, True, 0.25))
audio.set_managed(True)
CALLS.clear()
SLEEPS.clear()
audio.apply()
sets = [c for c in CALLS if len(c) > 1 and c[1] == "set-volume"]
mutes = [c for c in CALLS if len(c) > 1 and c[1] == "set-mute"]
steps = _steps_db(sets)
check("asked, from 25% and NOT muted: no set-mute call at all", mutes, [])
check("it climbs rather than jumping: every step is <= 3 dB (got "
      f"{[round(s, 3) for s in steps]})", all(s <= 3.0 + 1e-3 for s in steps), True)
check("...and every step really moves (no zero-length no-ops)", all(s > 0 for s in steps), True)
check("it reaches 100% (got " + repr(float(sets[-1][3])) + ")",
      abs(float(sets[-1][3]) - 1.0) < 1e-3, True)
check("a sleep separates every step from the next (got " + repr(sorted(set(SLEEPS))) + ")",
      len(SLEEPS) == len(sets) - 1 and all(s >= 0.1 - 1e-9 for s in SLEEPS), True)
VOL[0] = "Volume: 1.00\n"
CALLS.clear()
audio.apply()
check("already at 100%: nothing is set again", SETS(), [])
audio.set_managed(False)
check("and off again", audio.managed(), False)


head("pin() ramps from 25% MUTED to 100% -- never faster than 3 dB / 100 ms "
     "(the defect fix round 1 found)")
CALLS.clear()
SLEEPS.clear()
VOL[0] = "Volume: 0.25 [MUTED]\n"
_st2 = audio.pin()
sets2 = [c for c in CALLS if len(c) > 1 and c[1] == "set-volume"]
mutes2 = [c for c in CALLS if len(c) > 1 and c[1] == "set-mute"]
steps2 = _steps_db(sets2)
check("it unmutes exactly once", mutes2, [["wpctl", "set-mute", audio.SINK, "0"]])
check("the FIRST set-volume call -- made while still muted, so it is inaudible --"
      " is at the quiet floor, not at the old level or at 100%",
      round(60 * math.log10(float(sets2[0][3])), 1) if sets2 else None, audio.FLOOR_DB)
check("set-mute happens right after that floor call, and only there",
      CALLS.index(mutes2[0]) if mutes2 else -1,
      CALLS.index(sets2[0]) + 1 if sets2 else -2)
check("every later step, all the way to 100%, is <= 3 dB (got "
      f"{[round(s, 3) for s in steps2]})", all(s <= 3.0 + 1e-3 for s in steps2), True)
check("it actually reaches 100% (got " + repr(float(sets2[-1][3])) + ")",
      abs(float(sets2[-1][3]) - 1.0) < 1e-3, True)
check("a sleep -- unmute included -- separates every consecutive set-volume call "
      f"(got {len(SLEEPS)} sleeps for {len(sets2)} set-volume calls, all >= 100 ms)",
      len(SLEEPS) == len(sets2) - 1 and all(s >= 0.1 - 1e-9 for s in SLEEPS), True)
check("apply()'s own guard still means nothing new starts once it is there",
      _st2["volume"], 0.25)  # the fake sink's reported volume never actually moves


head("pin() reports a failure truthfully (it used to always say error: None)")
FAIL_CALLS = []


def fake_fails_to_set(args, timeout=5):
    FAIL_CALLS.append(list(args))
    if args[:2] == ["wpctl", "get-volume"]:
        return 0, "Volume: 0.25\n"
    if args[:2] == ["wpctl", "set-volume"]:
        return 1, ""            # wpctl itself refuses
    if args[:2] == ["pactl", "get-default-sink"]:
        return 0, SINK + "\n"
    if args[:3] == ["pactl", "--format=json", "list"]:
        return 0, json.dumps(SINKS)
    return 0, ""


audio._run = fake_fails_to_set
audio._sleep = lambda secs: None
st3 = audio.pin()
check("a wpctl that refuses every set-volume is reported, not silently swallowed",
      st3.get("error") is not None, True)


head("test servers can never see the real audio config (a managed tablet "
     "must come out of a full test run exactly as it went in)")
# Task 4's alertness.js POSTs /api/audio on every page load, which
# lib/audio.py answers by reading the managed flag out of
# $XDG_CONFIG_HOME/omarchy/omacar-audio.json. Every server test/app_test.py
# starts now passes an isolated XDG_CONFIG_HOME (see app_test._isolated_env,
# fix round 1) instead of inheriting the box's real one -- on a managed
# machine (the tablet), the old code would have run a real wpctl ramp on
# every single headless probe.
#
# This proves the fix rather than re-describing it: a "simulated real"
# config (never the box's actual one) is given to a server the OLD way, to
# show the flag mechanism really can leak if nothing isolates it, and then
# to a server started app_test.py's way, to show it cannot.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import _isolated_env  # noqa: E402  -- the fix under test

SHARE = os.path.join(ROOT, "share")


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


# A stand-in for "the real config" -- never the box's actual ~/.config.
SIMROOT = tempfile.mkdtemp(prefix="omacar-simulated-real-config-")
os.makedirs(os.path.join(SIMROOT, "omarchy"), exist_ok=True)
with open(os.path.join(SIMROOT, "omarchy", "omacar-audio.json"), "w", encoding="utf-8") as f:
    json.dump({"pin": True}, f)

leak_env = dict(os.environ)
leak_env["XDG_CONFIG_HOME"] = SIMROOT
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
fixed_srv = _serve(_isolated_env(), fixed_port)
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

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the audio path holds\n")
