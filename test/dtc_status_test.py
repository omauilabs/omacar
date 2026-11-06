#!/usr/bin/env python3
"""A test that has not run is not a fault.

On 26 October the CR-Z's hybrid module returned seven codes for a status
mask of FF, every one 0x40, and `omacar dtc` printed "7 stored". These are
the bytes that came back, and the counts that should be said about them."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import dtc  # noqa: E402

fails = 0


def check(what, got, want):
    global fails
    if got == want:
        print(f"    ok  {what}")
    else:
        fails += 1
        print(f"  FAIL  {what}\n          got  {got!r}\n          want {want!r}")


print("\n  the hybrid module's own answer, 26 October")
# 18DA03F1, 19 02 FF: seven records, every status byte 0x40.
hybrid = dtc.parse_dtc_list(
    "59024E0DA80340158603400A2703400A9403400A7F034015C4034014470340", 2)
check("seven codes came back", len(hybrid), 7)
check("none of them is a fault", dtc.tally(hybrid), (0, 7))

print("\n  the engine's P0420 with the lamp lit, same day")
check("0xC8 (lamp, not run, confirmed) is a fault", dtc.is_fault(0xC8), True)
check("0x40 alone is not", dtc.is_fault(0x40), False)
check("0x10 alone is not", dtc.is_fault(0x10), False)
check("0x50, not run since clear and this cycle, is not", dtc.is_fault(0x50), False)
for bit, name in dtc.STATUS_BITS:
    check(f"{name} ({bit:#04x}) is a fault exactly when it says something failed",
          dtc.is_fault(bit), bit not in (0x10, 0x40))

print("\n  a mixed list")
check("faults and not-run tests are counted apart",
      dtc.tally([("P0420-00", 0xC8), ("P0A7F-03", 0x40),
                 ("P0700-02", 0x04), ("P0705-02", 0x50)]), (2, 2))
check("an empty list is no faults and nothing pending", dtc.tally([]), (0, 0))

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  a test that has not run is never called a fault\n")
