#!/usr/bin/env bash
# The backup video on the tablet: its own screen and its own sound, around one
# whole tour (doc/design/2026-09-30-meetup-demo.md §6, "The backup video is
# recorded from the frozen build").
#
#   tools/demo_record.sh [--out FILE] [--monitor eDP-1] [--port 7580]
#
# THE TOUR PLAYS OUT LOUD WHILE THIS RECORDS: it is for the owner's desk, with
# the screen on and his say-so for sound, never for the night. The box's silent
# recording is tools/demo_record_headless.py.
#
# It starts nothing of the demo's. Bring the demo up first, unmuted, and look
# at it (`omacar-demo on`); then this:
#   1. checks what it needs, and says which is missing rather than improvising:
#      gpu-screen-recorder, curl and python3; the demo answering on its port;
#      the demo window open, and not started muted; the monitor;
#   2. starts `gpu-screen-recorder -w eDP-1 -f 30 -a default_output` into
#      ~/Videos/omacar-demo-backup-tablet.mp4, as H.264 and AAC at a constant
#      30 fps (-k h264 -ac aac -fm cfr), which any phone plays;
#   3. asks the page for the tour as `omacar demo tour` does (POST /api/screen);
#   4. knows the tour has started when the page's reset reaches the demo world
#      (a `restart` in the demo's demo-cue.json, newer than the ask), and stops
#      the recorder when the tour's steps (demo/data/tour.json) have run, with
#      SIGINT, which is how gpu-screen-recorder is told to finish the file;
#   5. says how long the film is and how big.
# If the page never takes the ask, or anything else fails once the recorder is
# running, the recorder is still stopped. It never changes the default sink or
# any volume: set the level by hand first.
#
# For its tests only: OMACAR_GSR names a stand-in recorder, and
# OMACAR_TOUR_JSON another tour.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GSR="${OMACAR_GSR:-gpu-screen-recorder}"
TOUR_JSON="${OMACAR_TOUR_JSON:-$ROOT/demo/data/tour.json}"
OUT="$HOME/Videos/omacar-demo-backup-tablet.mp4"
MONITOR="eDP-1"
PORT="${OMACAR_DEMO_PORT:-7580}"
# tour.js's RESET_WAIT_MS: step 1 opens at most this long after the reset.
RESET_WAIT=3
# And this much of the closing Home after the last step, for a clean end.
TAIL=2

while (( $# )); do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --monitor) MONITOR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h | --help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "demo_record: unknown option $1 (try --help)" >&2; exit 2 ;;
  esac
done

DEMO_ROOT="$(realpath -m -- "${XDG_STATE_HOME:-$HOME/.local/state}/omacar-demo")"
CUE="$DEMO_ROOT/state/omacar/demo-cue.json"
URL="http://127.0.0.1:$PORT"

say() { printf '  %s\n' "$*"; }
die() { printf 'demo_record: %s\n' "$*" >&2; exit 1; }

# ---- 1. what it needs ---------------------------------------------------------
missing=()
for t in "$GSR" curl python3; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
(( ${#missing[@]} == 0 )) || die "missing here: ${missing[*]}"
[[ -f "$TOUR_JSON" ]] || die "no tour at $TOUR_JSON"
curl -fsS -o /dev/null --max-time 3 "$URL/demo.html" \
  || die "no demo answers on $URL: bring it up first (omacar-demo on)"
[[ -f "$DEMO_ROOT/ACTIVE" ]] || die "the demo on $URL is not the one in $DEMO_ROOT (no ACTIVE there)"
# The window: `omacar demo on` opens it on the demo's own profile, and passes
# --mute-audio only when OMACAR_DEMO_MUTE=1 (the nights). A muted window would
# make a silent film.
window=$(pgrep -af -- "--user-data-dir=$DEMO_ROOT/browser" | grep -v -- "--type=" | head -1 || true)
[[ -n "$window" ]] || die "the demo window is not open (omacar-demo on, from the desktop or with a Wayland session)"
if [[ " $window " == *" --mute-audio "* ]]; then
  die "the demo window was started muted (OMACAR_DEMO_MUTE=1), so the film would be silent: omacar-demo off, then omacar-demo on without it"
fi
# gpu-screen-recorder needs the session's Wayland and sound, which an ssh shell
# does not carry: found where `omacar demo on` finds them.
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
  for w in "$XDG_RUNTIME_DIR"/wayland-[0-9]*; do
    [[ -S "$w" ]] || continue
    WAYLAND_DISPLAY="$(basename "$w")"
    export WAYLAND_DISPLAY
    break
  done
fi
monitors=$("$GSR" --list-monitors 2>/dev/null | cut -d'|' -f1 || true)
grep -qx -- "$MONITOR" <<<"$monitors" \
  || die "no monitor $MONITOR to record (gpu-screen-recorder sees: $(echo $monitors))"
secs=$(python3 -c 'import json, sys
print(round(sum(float(s["secs"]) for s in json.load(open(sys.argv[1]))["steps"])))' "$TOUR_JSON")

# ---- 2. the recorder ----------------------------------------------------------
mkdir -p "$(dirname "$OUT")"
rec=""
stop_recorder() {
  [[ -n "$rec" ]] || return 0
  if kill -0 "$rec" 2>/dev/null; then
    kill -INT "$rec" 2>/dev/null || true
    local i
    for i in $(seq 1 100); do kill -0 "$rec" 2>/dev/null || break; sleep 0.1; done
    if kill -0 "$rec" 2>/dev/null; then
      echo "demo_record: the recorder did not finish in 10 s; killed, and $OUT may be unplayable" >&2
      kill -KILL "$rec" 2>/dev/null || true
    fi
  fi
  wait "$rec" 2>/dev/null || true
  rec=""
}
trap stop_recorder EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# SIGINT BACK TO ITS DEFAULT FIRST. A job started with & from a script (no job
# control) inherits SIGINT ignored, and SIGINT is the only way to tell
# gpu-screen-recorder to finish its file; exec keeps the pid.
python3 -c 'import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])' \
  "$GSR" -w "$MONITOR" -f 30 -a default_output -k h264 -ac aac -fm cfr -o "$OUT" \
  </dev/null >"$OUT.log" 2>&1 &
rec=$!
sleep 2
kill -0 "$rec" 2>/dev/null || die "the recorder stopped at once: $(tail -3 "$OUT.log" 2>/dev/null)"
say "recording $MONITOR and the default output into $OUT"

# ---- 3. the tour --------------------------------------------------------------
asked=$(python3 -c 'import time; print(time.time())')
curl -fsS --max-time 5 -H "Content-Type: application/json" \
  -d '{"view": "demo-tour", "who": "OmaCar"}' "$URL/api/screen" >/dev/null \
  || die "the demo server did not take the ask for the tour"

# ---- 4. its start, and its end --------------------------------------------------
# The page looks for asks every 1.5 s, and a tour from the top begins with its
# reset, whose first act is the `restart` cue.
started=""
for _ in $(seq 1 100); do
  started=$(python3 - "$CUE" "$asked" <<'PY' 2>/dev/null || true
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)
if doc.get("cue") == "restart" and float(doc.get("at", 0)) > float(sys.argv[2]):
    print(doc["at"])
PY
)
  [[ -n "$started" ]] && break
  sleep 0.2
done
[[ -n "$started" ]] || die "the page did not start the tour within 20 s (is the demo window showing the demo?)"
say "the tour started; it runs ${secs} s"
end=$(python3 -c 'import sys; print(float(sys.argv[1]) + float(sys.argv[2]))' \
      "$started" "$((secs + RESET_WAIT + TAIL))")
while :; do
  left=$(python3 -c 'import sys, time; print(max(0.0, float(sys.argv[1]) - time.time()))' "$end")
  [[ "$left" == "0.0" ]] && break
  kill -0 "$rec" 2>/dev/null || die "the recorder stopped during the tour: $(tail -3 "$OUT.log" 2>/dev/null)"
  sleep "$(python3 -c 'import sys; print(min(1.0, float(sys.argv[1])))' "$left")"
done

# ---- 5. done --------------------------------------------------------------------
stop_recorder
[[ -s "$OUT" ]] || die "the recorder wrote nothing to $OUT (see $OUT.log)"
rm -f "$OUT.log"
info=$(ffprobe -v error -show_entries format=duration,size -of default=nw=1:nk=1 "$OUT" 2>/dev/null \
       | paste -sd' ' || true)
if [[ "$info" =~ ^([0-9.]+)\ ([0-9]+)$ ]]; then
  say "$OUT: ${BASH_REMATCH[1]} s, $(( BASH_REMATCH[2] / 1000000 )) MB"
else
  say "$OUT: $(du -h "$OUT" | cut -f1) (ffprobe could not read its length)"
fi
say "play it with: omacar-demo video"
