#!/usr/bin/env python3
"""The Surface's volume, held at 100% where it was asked for and nowhere else:
reading wpctl and pactl, naming the port, and apply() deciding whether to
touch anything. wpctl never runs for real here -- _run is replaced -- so this
suite cannot turn anybody's speakers up."""

import json
import os
import shutil
import sys
import tempfile

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

head("apply() touches the volume only where it was asked to")
VOL = ["Volume: 0.25\n"]
CALLS = []


def fake(args, timeout=5):
    CALLS.append(args)
    if args[:2] == ["wpctl", "get-volume"]:
        return 0, VOL[0]
    if args[:2] == ["pactl", "get-default-sink"]:
        return 0, SINK + "\n"
    if args[:3] == ["pactl", "--format=json", "list"]:
        return 0, json.dumps(SINKS)
    return 0, ""


audio._run = fake
SETS = lambda: [c for c in CALLS if len(c) > 1 and c[1] in ("set-mute", "set-volume")]  # noqa: E731
_st = audio.apply()
check("not asked: nothing is set", (SETS(), _st["managed"], _st["aux"], _st["volume"]), ([], False, True, 0.25))
audio.set_managed(True)
CALLS.clear()
audio.apply()
check("asked: unmuted and set to 100%", SETS(),
      [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
       ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"]])
VOL[0] = "Volume: 1.00\n"
CALLS.clear()
audio.apply()
check("already at 100%: nothing is set again", SETS(), [])
audio.set_managed(False)
check("and off again", audio.managed(), False)

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the audio path holds\n")
