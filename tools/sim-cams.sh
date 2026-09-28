#!/bin/bash
#
# Run a command beside a scratch camera recorder: `sim` test pictures for any
# role with no camera (on the box the C920 is the cabin), on scratch folders,
# so nothing touches ~/Videos, the real runtime directory, the real live.json
# or the real settings.
#
#   tools/sim-cams.sh python3 tools/shoot.py /tmp/shots 'cams=?still=1#cameras@1368,912'
#
# It waits, up to 60 s, until the recorder reports every role recording; runs
# the command with the same environment; then stops the recorder by its pid.
# The command's exit status is this script's.

set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
S="$(mktemp -d)"
mkdir -p "$S/videos" "$S/state" "$S/config"
mkdir -m 700 "$S/run"
export OMACAR_VIDEOS="$S/videos" XDG_RUNTIME_DIR="$S/run" XDG_STATE_HOME="$S/state" XDG_CONFIG_HOME="$S/config"
python3 "$ROOT/lib/cams.py" sim >"$S/cams.log" 2>&1 &
REC=$!
trap 'kill -INT "$REC" 2>/dev/null; wait "$REC" 2>/dev/null; rm -rf "$S"' EXIT
ready=0
for _ in $(seq 60); do
  if [[ "$(python3 "$ROOT/lib/cams.py" status | grep -c ' REC ')" == 3 ]]; then ready=1; break; fi
  sleep 1
done
if (( ! ready )); then
  echo "sim-cams: the recorder did not report three roles within 60 s:" >&2
  tail -5 "$S/cams.log" >&2
  exit 1
fi
"$@"
