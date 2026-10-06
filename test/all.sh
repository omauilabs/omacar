#!/bin/bash
#
# Every OmaCar test. The install round-trip runs against a scratch HOME; the
# prospector's logic runs against a scripted fake ECU. Neither needs a car.

set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omacar/venv"
fails=0

# ---- redesign/cameras ---------------------------------------------------------
# A running camera recorder holds Home's live picture open, and headless
# Chromium's virtual time never moves past an open request: app_test would hang
# rather than fail. So refuse, fast, and say why.
#
# SCOPED TO THIS CHECKOUT'S OWN cams.py, BY ABSOLUTE PATH, NOT A BARE
# SUBSTRING MATCH. Two things share this box: cams_test.py spawns its own
# fake recorder as a stand-in (`python -c '...' lib/cams.py run`, where
# "lib/cams.py" and "run" are just extra argv strings, not an executed
# script), and another mirror under ~/Projects/.omacar-test/ can have a real
# recorder of its own running for its own testing. A bare `pgrep -f
# "lib/cams.py (run|sim)"` matches either one's command line just as well as
# this checkout's real recorder, and would refuse to run here for a process
# that never touches this checkout's live picture or clips at all. Anchoring
# to "$ROOT/lib/cams.py" -- this checkout's own absolute path -- matches
# neither: the stand-in never carries an absolute path at all, and another
# mirror's real recorder carries *its own* root, not this one's.
if systemctl --user is-active --quiet omacar-cams.service 2>/dev/null \
   || pgrep -f "$ROOT/lib/cams.py (run|sim)" >/dev/null 2>&1; then
  echo "  omacar-cams is recording on this machine; stop it first: omacar cams off"
  exit 1
fi
# ---- end redesign/cameras -----------------------------------------------------

"$ROOT/test/smoke.sh" || fails=$((fails + 1))

if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" "$ROOT/test/prospect_test.py" || fails=$((fails + 1))
else
  echo "  (skipping prospector tests — run: omacar setup)"
fi

# The guards: every safety check that was written, looked right, and did not
# hold. Stubs pyserial itself, so it runs with or without a venv, always.
python3 "$ROOT/test/guards_test.py" || fails=$((fails + 1))

# `omacar power screen` on both Hyprlands: the Lua dispatch the tablet's 0.56
# needs, then the `dpms off` a hyprlang config needs, and a refusal that exits 0
# read as a refusal. Every hyprctl is a stand-in and is the whole of PATH, so no
# real screen can go dark. Listed here on the day it was written.
python3 "$ROOT/test/power_test.py" || fails=$((fails + 1))

python3 "$ROOT/test/calendar_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/shaders_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/app_test.py" || fails=$((fails + 1))

# The pure JavaScript, imported into a real browser: there is no node on the
# box or the tablet, and there is no build step to hang one off.
python3 "$ROOT/test/js_test.py" || fails=$((fails + 1))

# The runner above, held to what it promises: its WebSocket frames, its DevTools
# client, its wait on the real clock, and that it leaves no Chromium behind.
python3 "$ROOT/test/js_runner_test.py" || fails=$((fails + 1))

# The palette clears WCAG AA against itself, read out of app.css.
python3 "$ROOT/test/design_test.py" || fails=$((fails + 1))

# The phone screen's picture: framing, stream and decoder, end to end in a real
# browser against a recording. Listed here on the day it was written, which is
# the rule two suites above this one were added for.
python3 "$ROOT/test/phone_test.py" || fails=$((fails + 1))

# Road cameras: Caltrans' list, the proxied stills, the pins, and the one door
# out, against a trimmed copy of the list. Its own scratch HOME, and nothing
# reaches the internet -- the suite's last check is that nothing tried.
python3 "$ROOT/test/roadcams_test.py" || fails=$((fails + 1))

# The detached drive capture, against a fake adapter that can talk, go quiet,
# or not be there. The bench emulator has no monitor mode, so this is the only
# thing that ever runs the listening path without a car.
python3 "$ROOT/test/listen_test.py" || fails=$((fails + 1))

# tools/driveway_verdict.py's thresholds, against synthetic captures --
# pass/fail for both driveway-check.sh captures, the preset table, and the
# telemetry-share estimate. No car needed; see its own docstring.
python3 "$ROOT/test/driveway_test.py" || fails=$((fails + 1))

# tools/driveway-check.sh itself, run for real against a stand-in car that
# publishes the same hand-over snapshots the real daemon does (test/
# driveway_fake.py). Its systemctl is a stub, so it can never restart a real
# recorder unit. Needs the venv, and says so and skips without one.
python3 "$ROOT/test/driveway_e2e_test.py" || fails=$((fails + 1))

# The loopback server the kiosk's screen is served from: its output is kept,
# and kept small, where the next death can be read, and the kiosk brings it back
# on the same port when it goes. A signal that ends it says so. Real server,
# real shell; Chromium is a stand-in and every port and directory is a scratch
# one.
python3 "$ROOT/test/srvwatch_test.py" || fails=$((fails + 1))

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
#   drive_report
#            a day into drives, the figures on the page, and the same promise
#            for a page that is made to be shown to strangers
#   roadmap  the capability map and the claims: shipped means tracked by
#            git, a write names its gate, and regenerating the roadmap is
#            not counted as shipping. It never renders the whole block,
#            because rendering runs this script.
for suite in ima sitrep drive_report roadmap battery_health; do
  if [[ -x "$VENV/bin/python" ]]; then
    "$VENV/bin/python" "$ROOT/test/${suite}_test.py" || fails=$((fails + 1))
  else
    python3 "$ROOT/test/${suite}_test.py" || fails=$((fails + 1))
  fi
done

# ---- redesign/cameras ---------------------------------------------------------
# The cameras, the audio stage and drowsy mode. Scratch folders only: none of
# these touches ~/Videos, the real runtime directory or the speakers.
python3 "$ROOT/test/cams_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/camserve_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/audio_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/vendor_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/drowsy_test.py" || fails=$((fails + 1))
# ---- end redesign/cameras -----------------------------------------------------

# ---- demo/meetup: the map (Task 3) --------------------------------------------
# tools/demo_map.py against a hand-made map: the road classes, the ocean closed
# on the water's side, clipping, labels, and the same bytes every time. The
# renderer, the turn banner and the screens are test/js/demo-map.test.js, which
# js_test.py above already runs.
python3 "$ROOT/test/demo_map_test.py" || fails=$((fails + 1))
# ---- end demo/meetup: the map -------------------------------------------------
# ---- meetup demo: Agent, Work and the voice (Task 6) --------------------------
# tools/demo_voice.py against a stand-in Piper that writes silence: no voice
# model, no sound. The screens' own units are test/js/demo-agent.test.js, run
# by js_test.py above.
python3 "$ROOT/test/demo_voice_test.py" || fails=$((fails + 1))
# ---- end meetup demo: Task 6 --------------------------------------------------
# ---- demo/meetup --------------------------------------------------------------
# The demo's car: the drive built from a route, the world that plays it in real
# time, its cues, and that the module has no way to reach the adapter, the
# cameras or the sound. Scratch folders and held clocks only.
python3 "$ROOT/test/demoworld_test.py" || fails=$((fails + 1))
# ---- end demo/meetup ----------------------------------------------------------
# ---- demo/meetup: the silo ----------------------------------------------------
# The demo server's allowlist and its folders, then `omacar demo` itself in a
# scratch HOME with a fake real state, where systemctl, wpctl, pactl, hyprctl
# and chromium are shims that write down every call. Scratch ports only: never
# 7580, so a real demo on this machine is never met.
python3 "$ROOT/test/demoserve_test.py" || fails=$((fails + 1))
python3 "$ROOT/test/demo_silo_test.py" || fails=$((fails + 1))
# ---- end demo/meetup: the silo ------------------------------------------------
# ---- meetup demo: running the show (Task 8) -----------------------------------
# lib/democheck.py (`omacar demo check`) against a scratch tree, a scratch HOME
# and a stand-in pgrep: ready, and each thing that makes it not ready, named.
# The tour, its menu and keys, the top bar and the scripted scan are
# test/js/demo-tour.test.js, run by js_test.py above.
python3 "$ROOT/test/democheck_test.py" || fails=$((fails + 1))
# demoworld.py tidy: the seeded demo car with no active code, an all-clear last
# scan, readiness complete and nothing due, its history kept; and, in headless
# Chromium, Vehicle's "All systems normal", no tab badge, and Home's tyres not
# "Check". A scratch HOME inside an omacar-demo folder.
python3 "$ROOT/test/demotidy_test.py" || fails=$((fails + 1))
# ---- end meetup demo: Task 8 --------------------------------------------------
# ---- meetup demo: the backup video (Task 9) -----------------------------------
# tools/demo_record.sh, the tablet's recorder, dry-run against a stand-in
# gpu-screen-recorder and a stand-in demo: its flags, when it starts and stops,
# and what it refuses. The real recorder is never called and nothing plays.
# (`omacar demo video` is in workshop_test.py; tools/demo_e2e.py is a tool, run
# by hand: a tour is six minutes of real time per size.)
python3 "$ROOT/test/demo_record_test.py" || fails=$((fails + 1))
# tools/demo_record_headless.py's safety logic (the box's one unmuted browser)
# against a stand-in pactl: the guard on the browser's streams, the null sink
# never taken from under a stream, and one recording at a time. No sound
# server, browser or sink is touched.
python3 "$ROOT/test/demo_record_headless_test.py" || fails=$((fails + 1))
# ---- end meetup demo: Task 9 --------------------------------------------------
# ---- meetup demo: hardening D (no footage) ------------------------------------
# What tools/demo_e2e.py expects of a demo with no clips: the steps the tour must
# jump over, whether a folder or GET /api/cams says there is footage, and that its
# walk passes a tour that skips Cameras only when no footage is expected. No
# browser, no demo and no scratch HOME: the tour is a scripted page.
python3 "$ROOT/test/demo_e2e_test.py" || fails=$((fails + 1))
# ---- end meetup demo: hardening D ---------------------------------------------
# ---- battery health: the bus-capture miner (Task 2) ---------------------------
# tools/ima_mine.py against synthetic captures and a synthetic samples database:
# the planted charge byte ranks first, noise does not, no overlap is said
# plainly, and the source can reach neither the adapter nor the car.
python3 "$ROOT/test/ima_mine_test.py" || fails=$((fails + 1))
# ---- end battery health: Task 2 -----------------------------------------------

exit $((fails > 0))
