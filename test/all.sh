#!/bin/bash
#
# Every OmaCar test. The install round-trip runs against a scratch HOME; the
# prospector's logic runs against a scripted fake ECU. Neither needs a car.

set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omacar/venv"
fails=0

"$ROOT/test/smoke.sh" || fails=$((fails + 1))

if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" "$ROOT/test/prospect_test.py" || fails=$((fails + 1))
else
  echo "  (skipping prospector tests — run: omacar setup)"
fi

# The guards: every safety check that was written, looked right, and did not
# hold. Stubs pyserial itself, so it runs with or without a venv, always.
python3 "$ROOT/test/guards_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/calendar_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/shaders_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/app_test.py" || fails=$((fails + 1))

# The phone screen's picture: framing, stream and decoder, end to end in a real
# browser against a recording. Listed here on the day it was written, which is
# the rule two suites above this one were added for.
python3 "$ROOT/test/phone_test.py" || fails=$((fails + 1))

# The workshop's own logic — units, the service countdown, Mode 06 verdicts,
# the advisor's evidence check, the theme derivation and the drive-mode gauges.
#
# Stdlib only, so system python runs nearly all of it. The venv is PREFERRED
# rather than required: a handful of checks import modules that reach pyserial
# (dtclog is one), and under system python those skip themselves with a note.
# They were silently skipping in every run until this line preferred the venv,
# which is a guard that exists and never fires -- the worst kind.
#
# RUN IN A SCRATCH HOME, BECAUSE IT WRITES.
#
# This suite exercises the real api and watchdog modules against the real path
# constants, so it wrote into whatever vehicle record was current: 57 rows
# saying {"test": "fan_high", "seconds": 60} had accumulated in this machine's
# database, one per run, and a trip dated 1970-01-01 sat in the trips table from
# a watchdog fixture that starts its clock at t=1000. On the simulator that is
# invisible under 779 real trips. On a fresh real-car record it is the first
# thing the drive log shows you, and it is not true.
#
# HOME and all four XDG roots, not just XDG_STATE_HOME: the drive layout and the
# saved themes live under XDG_CONFIG_HOME, and the panel's rollup is written
# under $HOME directly. Redirecting one of the three moves two of the writes.
#
# PY is resolved BEFORE the redirect. `python3` here may be a version-manager
# shim that finds the real interpreter through XDG_DATA_HOME, so a redirected
# environment breaks the shim rather than the test -- which is how a passing
# assertion came to report FAIL for a reason that had nothing to do with it.
PY="$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null || command -v python3)"
[[ -x "$VENV/bin/python" ]] && PY="$VENV/bin/python"
SCRATCH_HOME="$(mktemp -d)"
env HOME="$SCRATCH_HOME" \
    XDG_STATE_HOME="$SCRATCH_HOME/state" XDG_CONFIG_HOME="$SCRATCH_HOME/config" \
    XDG_DATA_HOME="$SCRATCH_HOME/data"   XDG_CACHE_HOME="$SCRATCH_HOME/cache" \
    "$PY" "$ROOT/test/workshop_test.py" || fails=$((fails + 1))
rm -rf "$SCRATCH_HOME"

# The suites that live in their own files. Both were written alongside a
# feature and neither was listed here, so both passed on demand and ran in no
# actual test run -- which is the same as not existing. Anything added to
# test/ from now on belongs in this list on the day it is written.
#
#   ima      what the hybrid modules answered, and the rule that an
#            undiscovered quantity never renders as a number
#   sitrep   redaction, which is the promise that nothing leaving this
#            machine says whose car it is
for suite in ima sitrep; do
  if [[ -x "$VENV/bin/python" ]]; then
    "$VENV/bin/python" "$ROOT/test/${suite}_test.py" || fails=$((fails + 1))
  else
    python3 "$ROOT/test/${suite}_test.py" || fails=$((fails + 1))
  fi
done

exit $((fails > 0))
