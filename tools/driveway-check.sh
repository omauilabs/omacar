#!/bin/bash
#
# omacar driveway-check — one command, the full-bus driveway test from
# doc/drive-day.md ("Then, if there's time: the full-bus driveway check" and
# "Telemetry versus capture"), automated.
#
# Run on the tablet, engine running, car in P:
#
#   ~/Projects/omacar/tools/driveway-check.sh
#
# It confirms before it touches anything, stands the recorder down for two
# short captures (never more than 20s each), decides which recorder preset
# gives the most telemetry today, and offers to install it. A trap always
# leaves the recorder the way it found it, on every exit path, including
# Ctrl-C, a terminal hang-up and a kill. Everything it prints is saved under
# ~/.local/state/omacar/driveway-checks/<timestamp>/, alongside a
# summary.json with the same facts in a form a script can read.
#
# WHAT THIS WILL NEVER DO, BY CONSTRUCTION. It sends nothing to the car of
# its own. `omacar listen capture` listens to the bus (ATMA, which does not
# even acknowledge the frames it hears); the adapter set-up around it is the
# usual one and does send the adapter's own set-up commands and one 0100
# request (ATZ, the baud raise, ATSP0, ATE0/L0/S0/H1 -- see Elm.init() in
# lib/elm.py), exactly as every other omacar command does. This script adds
# nothing to that: no `omacar write`, no clear, no prospect, no mcp, no
# ATCSM0, no AT command of its own at all. test/guards_test.py reads this
# file and fails if any other omacar command, any systemctl but the two
# below, or anything that opens the adapter would run.
#
# omarchy:summary=One-command driveway test for full-bus telemetry
# omarchy:group=car

set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
source "$ROOT/lib/env.sh"
export OMACAR_LIB="$ROOT/lib"

# The CLI this script drives. The tablet's `omacar` on PATH does resolve to
# this checkout's bin/omacar, but naming it from ROOT means this script
# always runs the drivelog.py/listen.py it was tested against, not whatever
# else might be earlier on PATH. Overridable so the tests can point it at a
# stand-in that only logs what it was asked to run.
OMACAR_BIN="${OMACAR_BIN:-$ROOT/bin/omacar}"
# The one systemctl this script uses, only ever as `--user daemon-reload` and
# `--user restart omacar-drivelog`. Overridable so the tests can never restart
# the real recorder unit -- on the tablet or anywhere else.
SYSTEMCTL="${OMACAR_SYSTEMCTL:-systemctl}"
VERDICT_PY="$ROOT/tools/driveway_verdict.py"

# Where the systemd drop-in goes. A real run never sets this and gets
# ~/.config/systemd/user/omacar-drivelog.service.d, exactly like the doc's
# own manual steps.
DROPIN_DIR="${OMACAR_DROPIN_DIR:-$HOME/.config/systemd/user/omacar-drivelog.service.d}"

# Seconds. Every one of these is overridable from the environment FOR THE
# TESTS ONLY (a test that waits 30 real seconds per scenario is a test that
# stops being run); a real run never sets them.
#   WAIT_SECS     how long the gauges get to come back after a capture
#   GRACE_SECS    extra, after a capture that hung: how long its port lease can
#                 outlive it (lib/connect.py's YIELD_GRACE, read from there)
#   PROMPT_SECS   how long the "Install it now?" question waits
#   CAPTURE_TIMEOUT  the doc's own `timeout 90`
#   VERIFY_SECS   how long to poll the recorder's status after a restart
WAIT_SECS="${OMACAR_DRIVEWAY_WAIT_SECS:-30}"
GRACE_SECS_OVERRIDE="${OMACAR_DRIVEWAY_GRACE_SECS:-}"
PROMPT_SECS="${OMACAR_DRIVEWAY_PROMPT_SECS:-60}"
CAPTURE_TIMEOUT="${OMACAR_DRIVEWAY_TIMEOUT_SECS:-90}"
VERIFY_SECS="${OMACAR_DRIVEWAY_VERIFY_SECS:-10}"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'

usage() {
  cat <<'EOF'

  omacar driveway-check — the full-bus driveway test, one command.

    tools/driveway-check.sh                run it, ask before installing anything
    tools/driveway-check.sh --apply        run it, install the winning preset
    tools/driveway-check.sh --parked       skip the "type parked" prompt (for a
                                            remote run after the owner already
                                            confirmed in chat)
    tools/driveway-check.sh --dry-run      print every command it would run;
                                            open no port; send nothing; write
                                            no drop-in
    tools/driveway-check.sh --apply-preset NAME
                                            install one preset by name, no test
                                            (telemetry-first-caf0, telemetry-first,
                                             or fallback)
    tools/driveway-check.sh --remove-preset
                                            undo: back to the recorder's defaults
    tools/driveway-check.sh -h|--help

  Have the engine running and the car in P before you start. It asks you to
  confirm before it sends anything. The recorder is paused while this runs
  and is given back when it ends, however it ends.
EOF
}

MODE="test"
PRESET_NAME=""
DRY_RUN=0
APPLY=0
PARKED=0

# A plain while-loop, not ima-session.sh's for-loop over "$@" — this script
# has one flag that takes a value (--apply-preset NAME), so it needs shift.
while (( "$#" )); do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --parked) PARKED=1; shift ;;
    --apply) APPLY=1; shift ;;
    --apply-preset)
      MODE="apply-preset"
      shift
      if [[ -z "${1:-}" ]]; then
        echo "omacar driveway-check: --apply-preset needs a preset name" >&2
        usage >&2
        exit 2
      fi
      PRESET_NAME="$1"
      shift
      ;;
    --remove-preset) MODE="remove-preset"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "omacar driveway-check: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$MODE" == "apply-preset" ]]; then
  case "$PRESET_NAME" in
    telemetry-first-caf0|telemetry-first|fallback) ;;
    *)
      echo "omacar driveway-check: --apply-preset takes telemetry-first-caf0," \
           "telemetry-first or fallback, not '$PRESET_NAME'" >&2
      exit 2
      ;;
  esac
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION_DIR="$OMACAR_STATE/driveway-checks/$STAMP"
mkdir -p "$SESSION_DIR"
LOG="$SESSION_DIR/transcript.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# -- state carried across the whole run, for the trap and the summary ------
STARTED=0          # did we stand the recorder down ourselves
PLAN_STOOD=0       # will (or did) this run stand it down -- for the --dry-run plan
WAS_ON=1           # was it running before we touched it (so cleanup knows
                   # whether to give it back)
STOOD_AT=""        # time.time() when `drive off` returned
APPLIED=0
CHOSEN_PRESET=""
CHOSEN_ENV=""
ABORTED_REASON=""
INSTALL_STATE=""   # installed | failed | (empty: not attempted)
INSTALL_VERIFIED=""
INSTALL_DETAIL=""
OTHER_CONFS=""
CHILD_PID=""       # a capture in flight, so a signal can wait for it
WL_OK=""; WL_SECS=""; WL_STATUS=""; WL_CONNECTED=""
CAP_A_PATH=""; CAP_A_EXIT=""; CAP_A_RESULT=""; CAP_A_PASSED=0; CAP_A_WHY=""
CAP_A_GEAR=""; CAP_A_FRAMES=""; CAP_A_SPAN=""; CAP_A_BACK=""
CAP_B_PATH=""; CAP_B_EXIT=""; CAP_B_RESULT=""; CAP_B_PASSED=0; CAP_B_WHY=""
CAP_B_GEAR=""; CAP_B_FRAMES=""; CAP_B_SPAN=""; CAP_B_BACK=""

abort() {
  # Recorded into summary.json too (see write_summary below), not just
  # printed — a session directory whose only trace of a refusal is a line in
  # transcript.log is harder to triage later than one whose summary.json
  # says so as well.
  ABORTED_REASON="$*"
  echo
  echo "  ${RED}omacar driveway-check:${RESET} $*" >&2
  echo
  exit 1
}

setv() { printf -v "$1" '%s' "$2"; }

# time.time(), with sub-second precision. GNU date has it; anything else
# (there is no such thing on the tablet, but the --dry-run tests run on a
# Mac) asks the helper.
now() {
  local t
  t="$(date +%s.%N 2>/dev/null)"
  if [[ "$t" =~ ^[0-9]+\.[0-9]+$ ]]; then printf '%s' "$t"; else "$OMACAR_PY" "$VERDICT_PY" now; fi
}

# The interpreter for a lookup that has no side effects at all, so that even
# --dry-run can show real values (the preset's Environment= line) on a
# machine with no venv.
helper_py() { if [[ -x "$OMACAR_PY" ]]; then printf '%s' "$OMACAR_PY"; else printf 'python3'; fi; }

# Every command that would touch the recorder's service goes through this, in
# both modes — so the plan --dry-run prints is never a second copy of what a
# real run does, only a report of the same calls with the last one held back.
# Unlike tools/ima-session.sh's run_step, this returns the command's real
# exit code: the driveway test branches on whether a step worked.
run_cmd() {
  local label="$1"; shift
  if (( DRY_RUN )); then
    echo "  [dry-run] $label"
    echo "            $*"
    return 0
  fi
  echo "  -> $label"
  echo "     $*"
  "$@"
  local rc=$?
  if (( rc == 0 )); then
    echo "     ${GREEN}ok${RESET}"
  else
    echo "     ${YELLOW}exit $rc${RESET}"
  fi
  return $rc
}

# The same, for a capture -- run in the background and waited for, so that a
# signal reaches this script at once (a foreground child would hold the trap
# back until the capture ended, and there would be nothing to say meanwhile).
# See on_signal below.
run_capture() {
  local label="$1"; shift
  if (( DRY_RUN )); then
    echo "  [dry-run] $label"
    echo "            $*"
    return 0
  fi
  echo "  -> $label"
  echo "     $*"
  "$@" &
  CHILD_PID=$!
  wait "$CHILD_PID"
  local rc=$?
  CHILD_PID=""
  if (( rc == 0 )); then
    echo "     ${GREEN}ok${RESET}"
  else
    echo "     ${YELLOW}exit $rc${RESET}"
  fi
  return $rc
}

# Runs one tools/driveway_verdict.py subcommand — the judgement helper, and
# every read of live.json, drivelog.json and the captures folder — and leaves
# its `key=value` stdout lines in $VERDICT_OUT for kv() below. Narration goes
# straight to the terminal (and the transcript, via this script's own tee);
# only the python subprocess's own stdout is captured.
VERDICT_OUT=""
verdict_call() {
  local label="$1"; shift
  VERDICT_OUT=""
  if (( DRY_RUN )); then
    echo "  [dry-run] $label"
    echo "            $OMACAR_PY $VERDICT_PY $*"
    return 0
  fi
  echo "  -> $label"
  echo "     $OMACAR_PY $VERDICT_PY $*"
  VERDICT_OUT="$("$OMACAR_PY" "$VERDICT_PY" "$@")"
  local rc=$?
  (( rc != 0 )) && echo "     ${YELLOW}exit $rc${RESET}"
  return $rc
}

kv() {
  sed -n "s/^$1=//p" <<<"$VERDICT_OUT" | head -1
}

add_secs() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%g", a + b }'; }

# -- the recorder, the port and the gauges ---------------------------------

# Was the recorder already stood down before this run touched it? The marker
# file says so and nothing else does: `omacar drive status` also prints the
# supervisor's last published state NAME, and a supervisor that has only just
# been told `drive on` keeps saying "stood down" for up to its 15s poll --
# which is exactly the state this script's own trap leaves behind, so
# re-running it straight after a run would leave the recorder off for good.
# The same path as drivelog.OFFFILE; reading it is all that is done here.
recorder_marker() { [[ -e "$OMACAR_STATE/drivelog-off" ]]; }

# Stand the recorder down, remembering whether it was on. Used by every mode
# that is about to do something the recorder's own legs would collide with.
stand_down() {
  PLAN_STOOD=1
  if (( ! DRY_RUN )); then
    if recorder_marker; then
      WAS_ON=0
      echo "  the recorder was already stood down — this check will leave it that way."
    else
      WAS_ON=1
    fi
  fi
  run_cmd "stand the recorder down, so nothing else holds the port" "$OMACAR_BIN" drive off \
    || abort "could not stand the recorder down"
  if (( ! DRY_RUN )); then
    STARTED=1
    STOOD_AT="$(now)"
  fi
}

# wait_live connected|released SINCE TIMEOUT LABEL -> WL_OK, WL_SECS, WL_STATUS
#   connected: the gauges are alive (connected, and not mid hand-over)
#   released:  no recorder leg is holding the port (the car may be off)
# Always judged by a `t` LATER than SINCE: the daemon stamps a fresh `t` on
# every snapshot it publishes while it has lent the adapter out, so a `t` that
# has merely changed proves nothing.
wait_live() {
  local mode="$1" since="$2" timeout="$3" label="$4"
  verdict_call "$label" wait-live "$mode" "$since" "$timeout" \
    || abort "could not read the daemon's status (the helper failed)"
  WL_OK="$(kv ok)"; WL_SECS="$(kv seconds)"; WL_STATUS="$(kv status)"
  WL_CONNECTED="$(kv connected)"
}

# After `drive off`: wait for a recorder leg that was in progress to hand the
# adapter back, so capture A does not meet "the daemon is holding the port".
wait_port_free() {
  local mode="$1"
  if (( DRY_RUN )); then
    echo "  [dry-run] wait up to ${WAIT_SECS}s for the adapter to be free"
    return 0
  fi
  echo "  waiting for the adapter to be free…"
  wait_live "$mode" "$STOOD_AT" "$WAIT_SECS" "watching the daemon"
  if [[ "$WL_OK" == "1" ]]; then
    echo "  ${GREEN}the adapter is free${RESET} (${WL_SECS} s)"
    return 0
  fi
  if [[ "$WL_STATUS" == "yielded" ]]; then
    abort "the adapter is still being used by something else (a recorder leg or another command). Wait a minute and run this again."
  elif [[ "$mode" == "connected" ]]; then
    abort "the daemon says the adapter is not connected — check the OBDLink is plugged in and the ignition is on."
  fi
  abort "could not tell that the adapter was free within ${WAIT_SECS}s."
}

grace_secs() {
  if [[ -n "$GRACE_SECS_OVERRIDE" ]]; then
    printf '%s' "$GRACE_SECS_OVERRIDE"
  else
    "$OMACAR_PY" "$VERDICT_PY" yield-grace
  fi
}

# Names of any active drop-in that turns the fast link on.
fast_dropins() {
  local f names=""
  for f in "$DROPIN_DIR"/*.conf; do
    [[ -e "$f" ]] || continue
    grep -q 'OMACAR_FASTBAUD=1' "$f" 2>/dev/null && names="$names ${f##*/}"
  done
  printf '%s' "${names# }"
}

# The gauges did not come back: stop, loudly. Nothing is recommended or
# installed on the strength of a run that just showed the port is not coming
# back cleanly, and the setting that just failed is named.
gauges_failed() {
  local letter="$1" waited="$2" fast
  ABORTED_REASON="the gauges did not come back within ${waited}s after capture $letter (daemon status: ${WL_STATUS:-none})"
  echo
  echo "  ${RED}The gauges did not come back within ${waited}s after capture $letter.${RESET}"
  if [[ "$WL_STATUS" == "yielded" ]]; then
    echo "  The daemon is still waiting to get the adapter back."
  else
    echo "  The daemon is not reporting a connection."
  fi
  echo "  Stopping here — nothing was recommended and nothing was installed."
  fast="$(fast_dropins)"
  if [[ -n "$fast" ]]; then
    echo "  ${YELLOW}A fast-link drop-in is still installed: $fast${RESET}"
    echo "  It may be what is costing the gauges. Put the safe setting back with:"
  else
    echo "  If the gauges stay dead, put the safe recorder setting on with:"
  fi
  echo "    tools/driveway-check.sh --apply-preset fallback"
  exit 1
}

# -- one capture, start to judged ------------------------------------------
# capture_step LETTER NOTE DESCRIPTION [VAR=1 ...]
# Sets CAP_<LETTER>_{EXIT,RESULT,PATH,PASSED,WHY,GEAR,FRAMES,SPAN,BACK}.
# Any non-zero exit means that capture did not work; only a file this very
# run saved is ever judged; and the gauges have to come back before going on.
capture_step() {
  local L="$1" note="$2" desc="$3"; shift 3
  local start rc ret_at wait_t path vcmd
  echo
  echo "  Capture $L — $desc (20s)"
  if (( DRY_RUN )); then
    run_capture "capture $L: $desc" timeout "$CAPTURE_TIMEOUT" env "$@" \
      "$OMACAR_BIN" listen capture --seconds 20 --save --note "$note"
    return 0
  fi
  case "$L" in A) vcmd=verdict-a ;; *) vcmd=verdict-b ;; esac
  start="$(now)"
  run_capture "capture $L: $desc" timeout "$CAPTURE_TIMEOUT" env "$@" \
    "$OMACAR_BIN" listen capture --seconds 20 --save --note "$note"
  rc=$?
  ret_at="$(now)"
  setv "CAP_${L}_EXIT" "$rc"

  if (( rc == 124 )); then
    setv "CAP_${L}_RESULT" "hung — the ${CAPTURE_TIMEOUT}s timeout fired (exit 124)"
    echo "  ${RED}capture $L hung — the ${CAPTURE_TIMEOUT}s timeout fired.${RESET}"
  elif (( rc != 0 )); then
    setv "CAP_${L}_RESULT" "did not run (exit $rc)"
    echo "  ${RED}capture $L did not run (exit $rc) — nothing was saved to judge.${RESET}"
  else
    verdict_call "finding the file capture $L saved" latest-capture "$note" "$start" \
      || abort "could not look for capture $L's file"
    path="$(kv path)"
    if [[ -z "$path" ]]; then
      setv "CAP_${L}_RESULT" "ran, but saved no file"
      echo "  ${RED}capture $L ran but saved no file — nothing to judge.${RESET}"
    else
      setv "CAP_${L}_PATH" "$path"
      verdict_call "judging capture $L" "$vcmd" "$path" \
        || abort "could not judge capture $L"
      setv "CAP_${L}_FRAMES" "$(kv frames)"
      setv "CAP_${L}_SPAN" "$(kv span)"
      setv "CAP_${L}_WHY" "$(kv why)"
      setv "CAP_${L}_GEAR" "$(kv gear)"
      if [[ "$(kv passed)" == "1" ]]; then
        setv "CAP_${L}_PASSED" 1
        setv "CAP_${L}_RESULT" "passed"
        echo "  ${GREEN}capture $L passed${RESET} — $(kv why)"
      else
        setv "CAP_${L}_RESULT" "failed"
        echo "  ${YELLOW}capture $L failed${RESET} — $(kv why)"
      fi
    fi
  fi

  # The gauges. A capture that hung (or was killed) was cut off before it
  # could hand the port back; its lease outlives it, and the daemon only takes
  # the port back when that runs out.
  wait_t="$WAIT_SECS"
  if (( rc == 124 || rc >= 128 )); then
    wait_t="$(add_secs "$(grace_secs)" "$WAIT_SECS")"
  fi
  echo "  waiting for the gauges to come back…"
  wait_live connected "$ret_at" "$wait_t" "watching the daemon"
  if [[ "$WL_OK" == "1" ]]; then
    setv "CAP_${L}_BACK" "$WL_SECS"
    echo "  ${GREEN}gauges back in ${WL_SECS} s${RESET}"
  else
    gauges_failed "$L" "$wait_t"
  fi
}

# -- installing a preset ---------------------------------------------------

# A name for a file being switched off: <name>.off, or .off.1, .off.2 … --
# never one that already exists, so an earlier .off is never overwritten.
off_name() {
  local n="$1.off" i=1
  while [[ -e "$n" ]]; do n="$1.off.$i"; i=$((i + 1)); done
  printf '%s' "$n"
}

# systemd reads EVERY *.conf in the drop-in folder, so any active one other
# than ours can change the recorder's numbers underneath the preset. Only the
# three names the doc's own manual steps create are switched off (below);
# anything else is left alone and named here.
warn_other_confs() {
  local f base
  OTHER_CONFS=""
  for f in "$DROPIN_DIR"/*.conf; do
    [[ -e "$f" ]] || continue
    base="${f##*/}"
    [[ "$base" == "driveway-preset.conf" ]] && continue
    OTHER_CONFS="${OTHER_CONFS:+$OTHER_CONFS,}$base"
    echo "  ${YELLOW}another drop-in is active: $base${RESET} — it is left alone, and it may change the recorder's numbers."
  done
}

# daemon-reload && restart: the restart is not attempted if the reload failed.
reload_restart() {
  run_cmd "reload systemd user units" "$SYSTEMCTL" --user daemon-reload || return 1
  run_cmd "restart the recorder" "$SYSTEMCTL" --user restart omacar-drivelog
}

# Restart the recorder and check, from the supervisor's own status file, that
# the NEW supervisor is the one running with the preset's numbers. Sets
# INSTALL_STATE (installed | failed), INSTALL_VERIFIED (1 | 0) and APPLIED.
# What it cannot check: the supervisor never publishes OMACAR_FASTBAUD or
# OMACAR_CAF0, so those two are said to be unverifiable, not assumed.
restart_and_verify() {
  local env_line="$1" restart_at
  if (( DRY_RUN )); then
    reload_restart
    echo "  [dry-run] check the recorder's own status file (up to ${VERIFY_SECS}s): restarted, with the preset's numbers"
    return 0
  fi
  restart_at="$(now)"
  if ! reload_restart; then
    INSTALL_STATE="failed"
    echo
    echo "  ${RED}NOT installed${RESET} — systemd would not restart the recorder, so it is still running"
    echo "  its old numbers. To take the drop-in back out:  tools/driveway-check.sh --remove-preset"
    return 1
  fi
  INSTALL_STATE="installed"
  APPLIED=1
  echo "  restarted — checking the recorder came back with these numbers…"
  verdict_call "reading the recorder's own status file" verify-running "$env_line" "$restart_at" "$VERIFY_SECS" \
    || abort "could not read the recorder's status file (the helper failed)"
  if [[ "$(kv ok)" == "1" ]]; then
    INSTALL_VERIFIED=1
    echo "  ${GREEN}pass${RESET} — the recorder restarted with: between legs $(kv between)s," \
         "leg cap $(kv leg_lines) lines, quiet $(kv quiet)s$([[ "$(kv end_on_overflow)" == "1" ]] && echo ', ends on overflow')."
  else
    INSTALL_VERIFIED=0
    INSTALL_DETAIL="$(kv detail)"
    echo "  ${YELLOW}could not confirm${RESET} the recorder is running with these numbers ($INSTALL_DETAIL)."
    echo "  Check it by hand:  omacar drive status"
  fi
  echo "  ${DIM}(The fast link and CAF0 cannot be seen from status; the recorder's first leg will show them.)${RESET}"
  return 0
}

# Writes the drop-in and restarts the recorder. Shared by the main test's
# apply step and --apply-preset, which is the same action without a test
# first. The caller has already stood the recorder down.
apply_preset() {
  local name="$1" env_line="$2" legacy f target
  INSTALL_STATE=""; INSTALL_VERIFIED=""; INSTALL_DETAIL=""
  echo
  echo "  Installing preset: $name"
  run_cmd "make sure the drop-in folder exists" mkdir -p "$DROPIN_DIR"

  # An older drop-in left over from the doc's own manual steps would apply
  # ALONGSIDE this one -- renamed to .off, never deleted, and each rename is
  # printed.
  for legacy in fullbus balanced telemetry-first; do
    f="$DROPIN_DIR/$legacy.conf"
    if [[ -f "$f" ]]; then
      target="$(off_name "$f")"
      run_cmd "switch off the old $legacy.conf (renamed to ${target##*/}, never deleted)" mv "$f" "$target"
    fi
  done
  f="$DROPIN_DIR/driveway-preset.conf"
  if [[ -f "$f" ]]; then
    echo "  replacing the earlier driveway-preset.conf ($(grep '^Environment=' "$f" | head -1))"
  fi
  warn_other_confs

  if (( DRY_RUN )); then
    echo "  [dry-run] write $f:"
    echo "            [Service]"
    echo "            Environment=$env_line"
  else
    { echo "[Service]"; echo "Environment=$env_line"; } > "$f"
    echo "  wrote $f"
  fi
  restart_and_verify "$env_line"
}

remove_preset() {
  local f="$DROPIN_DIR/driveway-preset.conf" target
  INSTALL_STATE=""; INSTALL_VERIFIED=""; INSTALL_DETAIL=""
  target="$(off_name "$f")"
  run_cmd "switch off driveway-preset.conf (renamed to ${target##*/}, never deleted)" mv "$f" "$target"
  warn_other_confs
  restart_and_verify ""
}

# -- the summary, and giving the recorder back -----------------------------

# Always run from cleanup(), on every exit path — a session directory that
# has a transcript and no summary.json is exactly the failure the brief this
# script was written against exists to prevent. Safe in --dry-run (no
# captures) and after an abort (ABORTED_REASON is just another field).
write_summary() {
  local args=(write-summary --out "$SESSION_DIR" --stamp "$STAMP" --mode "$MODE"
              --dry-run "$DRY_RUN" --parked "$PARKED" --applied "$APPLIED")
  [[ -n "$ABORTED_REASON" ]] && args+=(--aborted "$ABORTED_REASON")
  [[ -n "$CAP_A_PATH" ]] && args+=(--capture-a "$CAP_A_PATH")
  [[ -n "$CAP_B_PATH" ]] && args+=(--capture-b "$CAP_B_PATH")
  [[ -n "$CAP_A_EXIT" ]] && args+=(--exit-a "$CAP_A_EXIT")
  [[ -n "$CAP_B_EXIT" ]] && args+=(--exit-b "$CAP_B_EXIT")
  [[ -n "$CAP_A_RESULT" ]] && args+=(--result-a "$CAP_A_RESULT")
  [[ -n "$CAP_B_RESULT" ]] && args+=(--result-b "$CAP_B_RESULT")
  [[ -n "$CAP_A_BACK" ]] && args+=(--reconnect-a "$CAP_A_BACK")
  [[ -n "$CAP_B_BACK" ]] && args+=(--reconnect-b "$CAP_B_BACK")
  [[ -n "$CHOSEN_PRESET" ]] && args+=(--preset "$CHOSEN_PRESET")
  [[ -n "$CHOSEN_ENV" ]] && args+=(--env "$CHOSEN_ENV")
  [[ -n "$INSTALL_STATE" ]] && args+=(--install-state "$INSTALL_STATE"
                                      --install-verified "$INSTALL_VERIFIED"
                                      --install-detail "$INSTALL_DETAIL")
  [[ -n "$OTHER_CONFS" ]] && args+=(--other-confs "$OTHER_CONFS")
  "$(helper_py)" "$VERDICT_PY" "${args[@]}" >/dev/null 2>>"$LOG" || \
    echo "  ${YELLOW}could not write summary.json — see $LOG${RESET}" >&2
}

print_summary() {
  {
    echo
    echo "  ── summary ──────────────────────────────────────────────"
    echo "  session   $SESSION_DIR"
    if (( DRY_RUN )); then
      echo "  dry run — nothing was sent; the lines above are exactly what a"
      echo "  real run would send, in order."
    elif [[ -n "$ABORTED_REASON" ]]; then
      echo "  ${RED}stopped early${RESET} — $ABORTED_REASON"
      if [[ "$INSTALL_STATE" == "installed" ]]; then
        echo "  the preset had already been installed."
      else
        echo "  nothing was recommended and nothing was installed."
      fi
    elif [[ "$INSTALL_STATE" == "installed" && "$MODE" == "remove-preset" ]]; then
      if [[ "$INSTALL_VERIFIED" == "1" ]]; then
        echo "  removed — the recorder restarted at its own defaults."
      else
        echo "  removed and restarted, but I could not confirm the recorder is at its"
        echo "  own defaults — check it by hand."
      fi
    elif [[ "$INSTALL_STATE" == "installed" ]]; then
      [[ -n "$CHOSEN_PRESET" ]] && echo "  preset    $CHOSEN_PRESET"
      if [[ "$INSTALL_VERIFIED" == "1" ]]; then
        echo "  installed — the recorder restarted with the numbers it asked for."
      else
        echo "  installed, but I could not confirm the recorder restarted with the"
        echo "  numbers it asked for — check it by hand."
      fi
    elif [[ "$INSTALL_STATE" == "failed" ]]; then
      echo "  ${RED}NOT installed${RESET} — systemd would not restart the recorder."
    elif [[ -n "$CHOSEN_PRESET" ]]; then
      echo "  preset    $CHOSEN_PRESET"
      echo "  not installed. Install later with:"
      echo "    tools/driveway-check.sh --apply-preset $CHOSEN_PRESET"
    elif [[ "$MODE" != "test" ]]; then
      echo "  $MODE done."
    fi
    echo "  ────────────────────────────────────────────────────────"
    echo
  } | tee -a "$SESSION_DIR/summary.txt"
}

# Whatever ends this script, the recorder goes back the way it was found. A
# second Ctrl-C during the hand-back is ignored: it must finish.
cleanup() {
  local rc=$?
  trap '' INT TERM HUP
  trap - EXIT
  # Only the modes that stand the recorder down have anything to give back.
  if (( DRY_RUN )); then
    if (( PLAN_STOOD )); then
      echo
      echo "  [dry-run] let the recorder run again"
      echo "            $OMACAR_BIN drive on"
    fi
  elif (( STARTED )); then
    if (( WAS_ON )); then
      echo
      echo "  letting the recorder run again (omacar drive on)…"
      "$OMACAR_BIN" drive on || echo "  omacar drive on failed — run it by hand." >&2
    else
      echo
      echo "  the recorder was already stood down before this check — leaving it that way."
    fi
  fi
  write_summary
  print_summary
  exit "$rc"
}

# A signal (Ctrl-C, a terminal hang-up, a kill) ends the run -- but never
# with a capture still holding the adapter: `timeout` puts the capture in its
# own process group, so it does not get the signal, and it is waited for
# here before the recorder is given back.
on_signal() {
  trap '' INT TERM HUP
  ABORTED_REASON="interrupted (SIG$1)"
  echo
  if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    echo "  finishing the capture, then giving the recorder back…"
    wait "$CHILD_PID" 2>/dev/null
  else
    echo "  stopping — giving the recorder back…"
  fi
  exit 130
}
trap cleanup EXIT
# A terminal that hangs up takes the log's `tee` with it; without this the
# next line this script prints would kill it with SIGPIPE, before the
# recorder was given back. A write to a dead pipe just fails quietly instead.
trap '' PIPE
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

# One run at a time: the owner in the car and one over SSH must not overlap.
take_lock() {
  (( DRY_RUN )) && return 0
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$OMACAR_STATE/driveway-check.lock"
    flock -n 9 || abort "another driveway check is already running — wait for it to finish."
  else
    echo "  (no flock on this machine — nothing stops a second run at the same time)"
  fi
}

echo
echo "  ${BOLD}OmaCar driveway check${RESET}  ${DIM}$STAMP${RESET}"
case "$MODE" in
  test) echo "  The full-bus driveway test from doc/drive-day.md, one command." ;;
  apply-preset) echo "  Installing the '$PRESET_NAME' preset, no test." ;;
  remove-preset) echo "  Removing the driveway-check preset — back to the recorder's defaults." ;;
esac
echo "  Saving to $SESSION_DIR"
echo

if (( DRY_RUN )); then
  echo "  ${DIM}--dry-run: printing the plan; nothing is opened, nothing is sent.${RESET}"
else
  omacar_need_env
  take_lock
fi

# ------------------------------------------------------- --apply-preset
# Same care as the test: stand the recorder down, wait for a leg in progress
# to hand the port back, THEN restart it -- restarting a supervisor that is
# mid-leg kills the leg, and neither drivelog.py nor listen.py handles the
# signal, leaving a stale port lease and an adapter still on the fast link.
if [[ "$MODE" == "apply-preset" ]]; then
  # A pure lookup: it runs even in --dry-run, so the plan shows the real line.
  VERDICT_OUT="$("$(helper_py)" "$VERDICT_PY" preset-for "$PRESET_NAME")" \
    || abort "could not look up the '$PRESET_NAME' preset"
  CHOSEN_PRESET="$(kv preset)"
  CHOSEN_ENV="$(kv env)"
  stand_down
  wait_port_free released
  apply_preset "$CHOSEN_PRESET" "$CHOSEN_ENV" || exit 1
  exit 0
fi

# ------------------------------------------------------- --remove-preset
if [[ "$MODE" == "remove-preset" ]]; then
  if [[ ! -f "$DROPIN_DIR/driveway-preset.conf" ]]; then
    echo "  nothing to remove — there is no driveway-preset.conf in $DROPIN_DIR,"
    echo "  so there is nothing of this script's to undo. The recorder is untouched."
    exit 0
  fi
  stand_down
  wait_port_free released
  remove_preset || exit 1
  exit 0
fi

# ------------------------------------------------------------------ test
# Everything below is MODE=test: the driveway procedure itself.

# Step 1 — confirm, the same way tools/ima-session.sh does.
if (( ! DRY_RUN )) && (( ! PARKED )); then
  cat <<EOF

  Before anything is sent:

    - engine running
    - shifter in P

EOF
  read -r -p "  Type parked and press enter to continue (anything else cancels): " REPLY || true
  REPLY="$(printf '%s' "${REPLY:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ "$REPLY" == "parked" ]] || abort "not confirmed — nothing was sent."
fi

# Step 2 — preflight: the daemon is running and its live data is fresh, read
# the way the recorder itself reads it. A snapshot that says "yielded" counts:
# the daemon is alive and has only lent the adapter to a recorder leg.
if (( ! DRY_RUN )); then
  echo "  checking the daemon is publishing live data…"
  verdict_call "reading live.json" live-state || abort "could not read live.json (the helper failed)"
  [[ "$(kv alive)" == "1" ]] \
    || abort "no live data from the daemon — nothing fresh in the last 90 s. Is the engine running and the daemon up?"
  RPM="$(kv rpm)"
  if [[ -n "$RPM" ]]; then echo "  live data is fresh — ${RPM} rpm"; else echo "  live data is fresh — rpm not reported"; fi
  if [[ "$(kv status)" == "yielded" ]]; then
    echo "  the recorder is capturing right now — it is stood down first, then waited for."
  fi
fi

# Step 3 — stand the recorder down (remembering whether it was on), then wait
# for a leg in progress to hand the adapter back.
stand_down
wait_port_free connected

# Steps 4-6 — the captures. B only if A passed: the doc says "do not add
# OMACAR_CAF0 on top of a link that has not proven itself." In --dry-run
# there is no real A to have passed, so the plan always shows both.
capture_step A fastbaud-test "the fast link alone" OMACAR_FASTBAUD=1
RUN_B=1
(( ! DRY_RUN )) && RUN_B=$CAP_A_PASSED
if (( RUN_B )); then
  capture_step B caf0-test "fast link plus ATCAF0" OMACAR_FASTBAUD=1 OMACAR_CAF0=1
else
  echo
  echo "  ${DIM}capture A did not pass — skipping capture B (the doc: never add" \
       "ATCAF0 on top of a link that has not proven itself).${RESET}"
fi

# Step 7 — gear position heard in each capture, information only. A free
# cross-check that the car was in P throughout; never a pass/fail gate.
if (( ! DRY_RUN )); then
  echo
  if [[ -n "$CAP_A_GEAR$CAP_B_GEAR" && "$CAP_A_GEAR$CAP_B_GEAR" != "none" && "$CAP_A_GEAR$CAP_B_GEAR" != "nonenone" ]]; then
    echo "  Gear position heard (0x191 byte 0; 0x01 = P) — information only:"
    [[ -n "$CAP_A_GEAR" && "$CAP_A_GEAR" != "none" ]] && echo "    capture A: ${CAP_A_GEAR//;/, }"
    [[ -n "$CAP_B_GEAR" && "$CAP_B_GEAR" != "none" ]] && echo "    capture B: ${CAP_B_GEAR//;/, }"
  else
    echo "  Gear position: no 0x191 frames were heard in the captures."
  fi
fi

# Step 8 — verdict and preset. In --dry-run there are no real verdicts, so
# this only prints the shape of the call.
if (( DRY_RUN )); then
  echo
  echo "  [dry-run] pick a preset from today's two verdicts and estimate the telemetry share:"
  echo "            $OMACAR_PY $VERDICT_PY preset <A_PASSED> <B_PASSED>"
  echo "            $OMACAR_PY $VERDICT_PY telemetry <preset> <env> <frame-rate> <gauges-back-seconds>"
  CHOSEN_PRESET="<the chosen preset>"
  CHOSEN_ENV="<the chosen preset's Environment= line>"
else
  B_ARG=0
  (( RUN_B )) && B_ARG=$CAP_B_PASSED
  verdict_call "picking today's preset" preset "$CAP_A_PASSED" "$B_ARG" \
    || abort "could not pick a preset (the helper failed)"
  CHOSEN_PRESET="$(kv preset)"
  CHOSEN_ENV="$(kv env)"

  RATE_ARG="none"
  if (( CAP_A_PASSED )) && [[ -n "$CAP_A_FRAMES" && -n "$CAP_A_SPAN" ]]; then
    RATE_ARG="$(awk -v n="$CAP_A_FRAMES" -v s="$CAP_A_SPAN" 'BEGIN { if (s > 0) printf "%g", n / s; else print "none" }')"
  fi
  # The gauges' time away after the fast-link capture this preset is judged
  # on: B's for the CAF0 row, A's for the other. (Never for the fallback.)
  BACK_ARG="none"
  if [[ "$CHOSEN_PRESET" == "telemetry-first-caf0" ]]; then BACK_ARG="${CAP_B_BACK:-none}"
  elif [[ "$CHOSEN_PRESET" == "telemetry-first" ]]; then BACK_ARG="${CAP_A_BACK:-none}"; fi
  verdict_call "estimating today's telemetry share" telemetry "$CHOSEN_PRESET" "$CHOSEN_ENV" "$RATE_ARG" "$BACK_ARG" \
    || abort "could not estimate the telemetry share (the helper failed)"

  echo
  echo "  ${BOLD}Verdict${RESET}"
  if (( CAP_A_PASSED )); then echo "    capture A: pass — $CAP_A_WHY"
  else echo "    capture A: fail — ${CAP_A_WHY:-$CAP_A_RESULT}"; fi
  if (( RUN_B )); then
    if (( CAP_B_PASSED )); then echo "    capture B: pass — $CAP_B_WHY"
    else echo "    capture B: fail — ${CAP_B_WHY:-$CAP_B_RESULT}"; fi
  else
    echo "    capture B: not run"
  fi
  echo "    preset:    $CHOSEN_PRESET"
  echo "    would set: $CHOSEN_ENV"
  if [[ "$(kv rate_measured)" == "1" ]]; then
    echo "    expected telemetry: about $(kv pct)% (estimated from today's frame rate:" \
         "a leg holds the port ~$(kv leg_hold)s of a ~$(kv cycle)s cycle)"
  else
    echo "    expected telemetry: about $(kv pct)% (estimated from doc/drive-day.md's own" \
         "numbers, not from today's capture: a leg holds the port ~$(kv leg_hold)s of a ~$(kv cycle)s cycle)"
  fi
  if [[ "$(kv reconnect_counted)" == "1" ]]; then
    echo "    ${DIM}includes the $BACK_ARG s of hand-over the gauges took to come back after today's capture.${RESET}"
  else
    echo "    ${DIM}this does not count the few seconds of hand-over each leg costs.${RESET}"
  fi
fi

# Step 9 — install, only with --apply, or if the person types "yes". The
# recorder stays stood down until this run ends, so it is installed while
# nothing is running to be killed by the restart, and only then given back.
# A question nobody answers must not keep the recorder paused: it waits a
# minute, and no answer is no.
DO_APPLY=0
if (( APPLY )); then
  DO_APPLY=1
elif (( ! DRY_RUN )); then
  echo
  echo "  the recorder is paused until you answer (no answer in ${PROMPT_SECS}s means no)."
  INSTALL_REPLY=""
  read -r -t "$PROMPT_SECS" -p "  Install it now? [yes/N] " INSTALL_REPLY
  READ_RC=$?
  echo
  case "$(printf '%s' "$INSTALL_REPLY" | tr '[:upper:]' '[:lower:]')" in
    y|yes) DO_APPLY=1 ;;
    *)
      if (( READ_RC > 128 )); then echo "  no answer — not installing."; else echo "  not installing."; fi
      ;;
  esac
fi

if (( DRY_RUN )); then
  echo
  echo "  [dry-run] install the chosen preset, if asked (--apply, or 'yes' at the prompt):"
  apply_preset "$CHOSEN_PRESET" "$CHOSEN_ENV"
  CHOSEN_PRESET=""; CHOSEN_ENV=""
elif (( DO_APPLY )); then
  apply_preset "$CHOSEN_PRESET" "$CHOSEN_ENV" || exit 1
fi

exit 0
