#!/bin/bash
#
# omacar ima-session — one command, parked, read-only: run every step from
# doc/../omacar-notes/2026-09-28-ima-findings.md section 4 in order, and
# nothing else.
#
# For the owner, alone with the car, with no connection for anyone to drive
# this remotely:
#
#   ~/Projects/omacar/tools/ima-session.sh
#
# It narrates each step in plain words as it runs, saves everything it
# prints under ~/.local/state/omacar/ima-sessions/<timestamp>/, and prints a
# short summary at the end. `--full` also runs the optional block sweep
# (section 4 step 5). `--dry-run` prints every command it would run and
# sends nothing — no port is opened.
#
# WHAT THIS WILL NEVER DO, BY CONSTRUCTION: `omacar write`, any clear, a
# session/security/reprogramming/routine/write service (0x10, 0x11, 0x14,
# 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x36, 0x37, 0x3E, 0x85), `ATCSM0`, or a
# header that is not 18DAxxF1/18DBxxF1. test/guards_test.py reads this file
# and fails if any of those would be sent.
#
# omarchy:summary=Parked, read-only IMA data hunt, one command
# omarchy:group=car

set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
source "$ROOT/lib/env.sh"
export OMACAR_LIB="$ROOT/lib"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'

BUDGET_SECS=$((25 * 60))     # a long key-on session once lit this car's ABS light

usage() {
  cat <<'EOF'

  omacar ima-session — parked, read-only, one command.

    tools/ima-session.sh              run it
    tools/ima-session.sh --full       also run the optional block sweep (step 5)
    tools/ima-session.sh --dry-run    print every command it would run; send nothing

  Have the engine running, the car in P, and the handbrake on before you
  start. It asks you to confirm before it sends anything.
EOF
}

DRY_RUN=0
FULL=0
for arg in "${@:-}"; do
  case "$arg" in
    "") ;;
    --dry-run) DRY_RUN=1 ;;
    --full) FULL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "omacar ima-session: unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION_DIR="$OMACAR_STATE/ima-sessions/$STAMP"
mkdir -p "$SESSION_DIR"
LOG="$SESSION_DIR/transcript.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

STEPS_RUN=0
STEPS_SKIPPED=0
STEPS_FAILED=0
STARTED=0
START_TS=0

abort() {
  echo
  echo "  ${RED}omacar ima-session:${RESET} $*" >&2
  echo
  exit 1
}

time_left() {
  local now; now=$(date +%s)
  echo $(( BUDGET_SECS - (now - START_TS) ))
}

# Every command that would touch the wire goes through this one function, in
# both modes -- so the plan --dry-run prints is never a second copy of what a
# real run does, only a report of the same calls with the last one held back.
run_step() {
  local label="$1"; shift
  if (( DRY_RUN )); then
    echo "  [dry-run] $label"
    echo "            $*"
    return 0
  fi
  if (( STARTED )); then
    local tl; tl=$(time_left)
    if (( tl <= 0 )); then
      echo "  ${YELLOW}[time-box]${RESET} skipping: $label — 25:00 budget used"
      STEPS_SKIPPED=$((STEPS_SKIPPED + 1))
      return 0
    fi
  fi
  echo "  -> $label"
  echo "     $*"
  if "$@"; then
    echo "     ${GREEN}ok${RESET}"
  else
    local rc=$?
    STEPS_RUN=$((STEPS_RUN + 1))
    stop_if_killed "$rc"
    echo "     ${YELLOW}failed (exit $rc) — continuing${RESET}"
    STEPS_FAILED=$((STEPS_FAILED + 1))
    return 0
  fi
  STEPS_RUN=$((STEPS_RUN + 1))
}

# A child killed by a signal (rc 128+n) is Ctrl-C or a kill, not an ordinary
# refusal, and must stop the whole session right here -- not be logged as one
# failed step while the loop goes on to send the next one. `exit` here runs
# the EXIT trap (cleanup: drive on, then the summary) exactly once.
stop_if_killed() {
  local rc="$1"
  if (( rc >= 128 )); then
    echo "  ${RED}interrupted (signal $((rc - 128)))${RESET} — stopping, sending nothing more."
    exit "$rc"
  fi
}

# Step 0b's single requests go through the MCP tool, on stdin, one JSON-RPC
# line per process -- the shape section 4 step 0b was written against.
run_mcp_request() {
  local header="$1" req="$2"
  local payload
  payload=$(printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"car_request","arguments":{"header":"%s","request":"%s"}}}' \
            "$header" "$req")
  if (( DRY_RUN )); then
    echo "  [dry-run] learn the no-answer wording: $header $req"
    echo "            printf '%s\\n' '$payload' | omacar mcp"
    return 0
  fi
  if (( STARTED )); then
    local tl; tl=$(time_left)
    if (( tl <= 0 )); then
      echo "  ${YELLOW}[time-box]${RESET} skipping: $header $req — 25:00 budget used"
      STEPS_SKIPPED=$((STEPS_SKIPPED + 1))
      return 0
    fi
  fi
  echo "  -> asking $header for $req, to learn what a refusal looks like"
  local reply rc
  reply=$(printf '%s\n' "$payload" | omacar mcp)
  rc=$?
  STEPS_RUN=$((STEPS_RUN + 1))
  stop_if_killed "$rc"
  echo "     $reply"
}

run_prospect() {
  local label="$1" service="$2" headers="$3" range="$4" rounds="$5"
  run_step "$label" \
    omacar prospect --service "$service" --headers "$headers" --range "$range" \
    --parked --rounds "$rounds"
}

run_candlog() {
  run_step "$1" omacar candlog --profile honda-crz-2015 --once
}

print_summary() {
  {
    echo
    echo "  ── summary ──────────────────────────────────────────────"
    echo "  session   $SESSION_DIR"
    if (( DRY_RUN )); then
      echo "  dry run — nothing was sent; the lines above are exactly what a"
      echo "  real run would send, in order."
    elif (( ! STARTED )); then
      echo "  nothing was sent — the session did not start."
    else
      local end_ts elapsed
      end_ts=$(date +%s); elapsed=$(( end_ts - START_TS ))
      echo "  ran        $STEPS_RUN step(s)"
      echo "  skipped    $STEPS_SKIPPED step(s) (time-box)"
      echo "  failed     $STEPS_FAILED step(s) — see $LOG"
      printf "  elapsed    %02d:%02d of 25:00\n" $((elapsed / 60)) $((elapsed % 60))
      echo
      echo "  Captures, prospect files and the draft profile are, as always,"
      echo "  under ~/.local/state/omacar/. Everything this session printed"
      echo "  is also under $SESSION_DIR."
    fi
    echo "  ────────────────────────────────────────────────────────"
    echo
  } | tee -a "$SESSION_DIR/summary.txt"
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if (( STARTED )) && (( ! DRY_RUN )); then
    echo
    echo "  letting the recorder run again (omacar drive on)…"
    omacar drive on || echo "  omacar drive on failed — run it by hand." >&2
  fi
  print_summary
  exit "$rc"
}
trap cleanup EXIT INT TERM

echo
echo "  ${BOLD}OmaCar IMA session${RESET}  ${DIM}$STAMP${RESET}"
echo "  Parked at Los Banos, read-only. Section 4 of the findings, steps 0-4"
if (( FULL )); then
  echo "  plus the optional step 5 block sweep (--full)."
fi
echo "  Time-boxed at 25 minutes of sending. Saving to $SESSION_DIR"
echo

if (( DRY_RUN )); then
  echo "  ${DIM}--dry-run: printing the plan; nothing is opened, nothing is sent.${RESET}"
else
  omacar_need_env

  echo "  checking for an adapter…"
  ADAPTER_INFO=$("$OMACAR_PY" - 2>&1 <<'PY'
import os, sys
sys.path.insert(0, os.environ["OMACAR_LIB"])
import connect
port, kind = connect.resolve()
if not port:
    print("no adapter and no bench emulator found", file=sys.stderr)
    sys.exit(1)
print(f"{port} ({kind})")
PY
) || abort "$ADAPTER_INFO — plug in the OBDLink and try again."
  echo "  adapter found: $ADAPTER_INFO"

  cat <<EOF

  Before anything is sent:

    - engine running
    - shifter in P
    - handbrake on

EOF
  read -r -p "  Type parked and press enter to continue (anything else cancels): " REPLY
  REPLY="$(printf '%s' "$REPLY" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ "$REPLY" == "parked" ]] || abort "not confirmed — nothing was sent."

  echo "  checking road speed and battery voltage…"
  PRECHECK_OUT=$("$OMACAR_PY" - 2>&1 <<'PY'
import os, sys
sys.path.insert(0, os.environ["OMACAR_LIB"])
import connect
import elm as elmlib
import dtc as dtclib

port, kind = connect.resolve()
if not port:
    print("no adapter and no bench emulator found", file=sys.stderr)
    sys.exit(1)
warn = connect.serial_group_warning(port)
if warn:
    print(warn, file=sys.stderr)
    sys.exit(1)
if not connect.request_port(port):
    print(f"the daemon is holding {port} and did not let go", file=sys.stderr)
    sys.exit(1)

el = None
try:
    el = elmlib.Elm(port, baudrate=connect.link_baud(port))
    el.init()
    if kind == "bench":
        speed = "bench"
    else:
        import prospect
        mv = prospect.moving(el)
        speed = "moving" if mv else ("zero" if mv is False else "unknown")
    volts = dtclib.battery_volts(el)
    floor = dtclib.LOW_VOLTS
    volts_ok = (volts is None) or (volts >= floor)
    volts_s = f"{volts:.1f}" if volts is not None else "unknown"
    print(f"speed={speed} volts={volts_s} floor={floor} volts_ok={volts_ok}")
finally:
    if el is not None:
        try:
            el.close()
        except Exception:
            pass
    try:
        connect.release_port()
    except Exception:
        pass
PY
) || abort "could not read speed/voltage: $PRECHECK_OUT"

  SPEED=$(sed -n 's/.*speed=\([a-zA-Z0-9.]*\).*/\1/p' <<<"$PRECHECK_OUT")
  VOLTS=$(sed -n 's/.*volts=\([a-zA-Z0-9.]*\).*/\1/p' <<<"$PRECHECK_OUT")
  FLOOR=$(sed -n 's/.*floor=\([a-zA-Z0-9.]*\).*/\1/p' <<<"$PRECHECK_OUT")
  VOLTS_OK=$(sed -n 's/.*volts_ok=\([a-zA-Z]*\).*/\1/p' <<<"$PRECHECK_OUT")

  [[ "$SPEED" == "moving" ]] && abort "the car reports road speed — refusing. Park it and try again."
  [[ "$VOLTS_OK" == "True" ]] || abort "battery is ${VOLTS} V, below the ${FLOOR} V floor — start the engine or charge the battery."
  echo "  speed: $SPEED (parked)   battery: ${VOLTS} V (floor ${FLOOR} V)"
fi

# ------------------------------------------------------------------ begin
run_step "stand the recorder down, so nothing else holds the port" omacar drive off
if (( ! DRY_RUN )); then
  STARTED=1
  START_TS=$(date +%s)
fi

echo
echo "  Step 0 — passive probe for the community IMA-CAN ids (receive-only; nothing sent to the car)"
for id in 231 307 115 17D; do
  run_step "listen for 0x$id (15s)" omacar listen capture --id "$id" --seconds 15 --save --note "probe-$id"
done

echo
echo "  Step 0b — learn each module's refusal wording (six single requests, read-only)"
for pair in "18DA0EF1:222660" "18DA10F1:222610" "18DA03F1:222001" \
            "18DA03F1:22202A" "18DA03F1:2101" "18DA04F1:2101"; do
  header="${pair%%:*}"; req="${pair##*:}"
  run_mcp_request "$header" "$req"
done

echo
echo "  Step 1 — positive control: Honda's 0x2xxx DIDs exist on this car"
run_prospect "Step 1a: engine/PCM, 2610-2616" 0x22 "18DA0EF1,18DA10F1" "2610-2616" 4
run_prospect "Step 1b: engine/PCM, 2660-2666" 0x22 "18DA0EF1,18DA10F1" "2660-2666" 4
run_prospect "Step 1c: PCM, 2240-2240" 0x22 "18DA0EF1" "2240-2240" 2
run_candlog "Step 1: read back full records for anything that answered"

echo
echo "  Step 2 — Honda HV-battery DIDs on the hybrid modules"
run_prospect "Step 2a: HV battery/motor, 2001-2012" 0x22 "18DA03F1,18DA04F1" "2001-2012" 8
run_prospect "Step 2b: HV battery/motor, 2021-202C" 0x22 "18DA03F1,18DA04F1" "2021-202C" 8
run_prospect "Step 2c: HV battery/motor, 2222-2222" 0x22 "18DA03F1,18DA04F1" "2222-2222" 8
run_candlog "Step 2: read back full records for anything that answered"

echo
echo "  Step 3 — service 0x21 across the hybrid modules, 00-FF"
run_prospect "Step 3: HV battery/motor, service 0x21, 00-FF" 0x21 "18DA03F1,18DA04F1" "00-FF" 8
run_candlog "Step 3: read back full records for anything that answered"

if (( FULL )); then
  echo
  echo "  Step 5 (--full) — resumable block sweep of 0x2000-0x2FFF, one 45s pass"
  run_step "Step 5: block sweep" \
    omacar discover --headers "18DA03F1,18DA04F1" --service 0x22 --range 2000-2FFF --budget 45 --once
  run_step "Step 5: sweep status" omacar discover status
else
  echo
  echo "  Step 5 (optional block sweep) skipped — run with --full to include it."
fi

if (( DRY_RUN )); then
  echo
  echo "  [dry-run] let the recorder run again"
  echo "            omacar drive on"
fi

exit 0
