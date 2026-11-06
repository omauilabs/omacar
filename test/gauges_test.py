#!/usr/bin/env python3
"""The gauge daemon comes back after a crash, never spins on an empty port,
and the pre-drive sheet says how its last run ended.

On 28 October the daemon crashed 38 seconds into a drive, stayed dead because
the unit was Restart=no, and `omacar preflight` still said nothing would stop
a recording."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import preflight  # noqa: E402

fails = 0


def check(what, got, want):
    global fails
    if got == want:
        print(f"    ok  {what}")
    else:
        fails += 1
        print(f"  FAIL  {what}\n          got  {got!r}\n          want {want!r}")


print("\n  the unit and the daemon agree on what 'no car' looks like")
with open(os.path.join(ROOT, "lib", "daemon.py"), encoding="utf-8") as f:
    daemon_src = f.read()
with open(os.path.join(ROOT, "share", "systemd", "omacar-daemon.service"),
          encoding="utf-8") as f:
    unit = f.read()
code = re.search(r"^EXIT_NO_CAR = (\d+)$", daemon_src, re.M)
check("daemon.py names its no-car status", bool(code), True)
code = code.group(1) if code else "?"
check("the unit restarts on failure", "Restart=on-failure" in unit, True)
check("but never for no car", f"RestartPreventExitStatus={code}" in unit, True)
check("and no car is not shown as failed", f"SuccessExitStatus={code}" in unit, True)
check("a crash loop is still capped", "StartLimitBurst=3" in unit, True)
check("both startup exits use it, not a bare sys.exit(str)",
      len(re.findall(r'^\s+sys\.exit\("omacar daemon:', daemon_src, re.M)), 0)
check("connect()'s own no-adapter exit is caught, not re-raised",
      "except SystemExit:\n            raise" in daemon_src, False)

print("\n  the pre-drive sheet")
preflight._unit_present = lambda name: True
CASES = [
    ("running clean", "active", "success", "0", "0", True, False),
    ("running, restarted once", "active", "success", "0", "1", False, False),
    ("gave up after a crash loop", "failed", "start-limit-hit", "1", "3", False, True),
    ("crashed once and stopped", "failed", "exit-code", "1", "0", False, False),
    ("found no car", "inactive", "success", "3", "0", True, False),
]
for name, state, result, status, restarts, want_ok, want_block in CASES:
    out = (f"ActiveState={state}\nResult={result}\nExecMainStatus={status}\n"
           f"NRestarts={restarts}\nExecMainExitTimestamp=")
    preflight._run = lambda cmd, timeout=10, out=out: (0, out, "")
    sheet = preflight.Sheet()
    preflight._gauges(sheet)
    _what, ok, _said, _fix, blocking = sheet.rows[0]
    check(f"{name}: {'fine' if want_ok else 'flagged'}"
          + (", and it blocks" if want_block else ""),
          (ok, (not ok) and blocking), (want_ok, want_block))

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the gauges come back, and the sheet says when they did not\n")
