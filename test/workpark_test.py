#!/usr/bin/env python3
"""Parked, as Work's relay sees it: the app's rule, stricter about not knowing."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workpark  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

t = [0.0]; s = [{}]
P = workpark.Park(read=lambda: s[0], clock=lambda: t[0])
def at(sec, sample): t[0] = sec; s[0] = sample; return P.update()
STOP = {"connected": True, "values": {"SPEED": 0}}
GO = {"connected": True, "values": {"SPEED": 40}}

check("no daemon: not parked", at(0, {"connected": False, "status": "no daemon"}), False)
check("fresh Park, connected and stopped: not parked yet", at(1, STOP), False)
check("fresh Park, stopped 59 s: not parked", at(60, STOP), False)
check("fresh Park, stopped 60 s: parked", at(61, STOP), True)
check("moving: not parked", at(2, GO), False)
# The stop begins at t=32 (still_since=32).
check("stopped 0 s (a red light): not parked", at(32, STOP), False)
check("stopped 59 s: not parked", at(91, STOP), False)
check("stopped 60 s: parked", at(92, STOP), True)
check("hand-off: not parked", at(93, dict(STOP, handover=True)), False)
check("link drop resets the clock", (at(94, {"connected": False}), at(100, STOP), at(159, STOP), at(160, STOP)),
      (False, False, False, True))
check("no SPEED is not stopped", at(200, {"connected": True, "values": {}}), False)

# A hand-off resets the settle: parked, then a hand-off needs a full settle again.
t = [0.0]; s = [{}]
P = workpark.Park(read=lambda: s[0], clock=lambda: t[0])
check("fresh car settles: parked at 60 s", (at(0, STOP), at(60, STOP)), (False, True))
check("hand-off: not parked", at(61, dict(STOP, handover=True)), False)
check("after hand-off, stopped 0 s: not parked", at(62, STOP), False)
check("after hand-off, stopped 59 s: not parked", at(121, STOP), False)
check("after hand-off, stopped 60 s: parked", at(122, STOP), True)
check("non-numeric SPEED is unknown, no raise", at(123, {"connected": True, "values": {"SPEED": "--"}}), False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
