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
# Ctrl-C. Everything it prints is saved under
# ~/.local/state/omacar/driveway-checks/<timestamp>/, alongside a
# summary.json with the same facts in a form a script can read.
#
# WHAT THIS WILL NEVER DO, BY CONSTRUCTION. It sends nothing to the car
# beyond what `omacar listen capture` already does — ATMA, a passive monitor
# that does not even acknowledge the frames it hears. No `omacar write`, no
# clear, no prospect, no mcp, no ATCSM0, no AT command of its own at all.
# test/guards_test.py reads this file and fails if any of those would run.
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
# else might be earlier on PATH. Overridable so the tests below can point it
# at a script that only logs what it was asked to run, the same technique
# test/guards_test.py already uses for tools/ima-session.sh's --dry-run
# check.
OMACAR_BIN="${OMACAR_BIN:-$ROOT/bin/omacar}"
VERDICT_PY="$ROOT/tools/driveway_verdict.py"

# Where the systemd drop-in goes. A real run never sets this and gets
# ~/.config/systemd/user/omacar-drivelog.service.d, exactly like the doc's
# own manual steps. Overridable for the tests below, which have no systemd
# user session to write into.
DROPIN_DIR="${OMACAR_DROPIN_DIR:-$HOME/.config/systemd/user/omacar-drivelog.service.d}"

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
  confirm before it sends anything.
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
WAS_ON=1            # was it running before we touched it (so cleanup knows
                    # whether to give it back)
APPLIED=0
CHOSEN_PRESET=""
CHOSEN_ENV=""
ABORTED_REASON=""
CAP_A_PATH=""; CAP_A_FRAMES=""; CAP_A_SPAN=""
RECONNECT_A=""; RECONNECT_B=""
CAP_B_PATH=""

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

# Every command that would touch the wire or the recorder's service goes
# through this, in both modes — so the plan --dry-run prints is never a
# second copy of what a real run does, only a report of the same calls with
# the last one held back. Unlike tools/ima-session.sh's run_step, this
# returns the command's real exit code: the driveway test branches on
# whether a step passed (does capture B run at all?), it does not just log a
# failure and keep going.
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

# Runs one tools/driveway_verdict.py subcommand — the judgement helper — and
# leaves its `key=value` stdout lines in $VERDICT_OUT for kv() below to read.
# Narration goes straight to the terminal (and the transcript, via this
# script's own tee); only the python subprocess's own stdout is captured, so
# the narration lines never leak into $VERDICT_OUT.
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

# The one live-data read this script uses, read the same way lib/drivelog.py
# itself decides whether to capture — literally calling its own _live()
# rather than re-guessing the freshness window in a second place. Read-only:
# it opens live.json, never the adapter.
read_live() {
  "$OMACAR_PY" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["OMACAR_LIB"])
import drivelog
live = drivelog._live()
if live is None:
    print("fresh=0")
    raise SystemExit(0)
values = live.get("values") or {}
rpm = values.get("RPM")
print("fresh=1")
print(f"connected={1 if live.get('connected') else 0}")
print(f"rpm={'' if rpm is None else rpm}")
PY
}

# live.json's own "t" field, for the reconnect check below. Same file
# read_live() reads; split out because wait_reconnect() polls it in a loop
# and only needs the one field.
live_t() {
  "$OMACAR_PY" - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["OMACAR_LIB"])
import records
try:
    with open(records.LIVE, encoding="utf-8") as f:
        doc = json.load(f)
except (OSError, ValueError):
    print("")
    raise SystemExit(0)
print(doc.get("t") or "")
PY
}

# Polls live.json until its timestamp moves past `before`, or 30s pass.
# Prints the seconds it took and returns 0, or returns 1 on a timeout — an
# empty result IS the failure, the same shape ima-session.sh's PRECHECK_OUT
# parsing already uses elsewhere in this tree.
wait_reconnect() {
  local before="$1" start now cur
  start=$(date +%s)
  while true; do
    cur="$(live_t)"
    if [[ -n "$cur" && "$cur" != "$before" ]]; then
      echo $(( $(date +%s) - start ))
      return 0
    fi
    now=$(date +%s)
    (( now - start >= 30 )) && return 1
    sleep 1
  done
}

# The most recently saved capture whose `note` field matches, under
# listen.CAPTURES. Capture filenames are timestamps (see lib/listen.py's
# Capture.save()) so the note is the only way to find the one this run just
# made; sorted iteration keeps the last (most recent) match.
latest_capture_for_note() {
  "$OMACAR_PY" - "$1" <<'PY'
import glob, json, os, sys
sys.path.insert(0, os.environ["OMACAR_LIB"])
import listen
note = sys.argv[1]
best = ""
for path in sorted(glob.glob(os.path.join(listen.CAPTURES, "*.json"))):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        continue
    if doc.get("note") == note:
        best = path
print(best)
PY
}

# The numeric tokens worth grepping for in `omacar drive status`'s text,
# from a preset's `Environment=` line — drivelog.py's report() prints these
# with Python's :.0f, an integer with no trailing ".0", so "10" is what to
# look for, not "10.0".
preset_status_tokens() {
  local tok key val
  for tok in $1; do
    key="${tok%%=*}"; val="${tok#*=}"
    case "$key" in
      OMACAR_DRIVELOG_BETWEEN|OMACAR_DRIVELOG_LEG_LINES|OMACAR_DRIVELOG_QUIET)
        printf '%s\n' "${val%.*}"
        ;;
    esac
  done
}

# Writes the drop-in and restarts the recorder. Shared by the main test's
# apply step and --apply-preset, which is the same action without a test
# first.
apply_preset() {
  local name="$1" env_line="$2"
  echo
  echo "  Installing preset: $name"
  run_cmd "make sure the drop-in directory exists" mkdir -p "$DROPIN_DIR"

  # systemd reads every *.conf in this directory, so an older drop-in left
  # over from the doc's own manual steps would apply ALONGSIDE this one --
  # renamed to .off, never deleted, and each rename is printed.
  local legacy f
  for legacy in fullbus balanced telemetry-first; do
    f="$DROPIN_DIR/$legacy.conf"
    if [[ -f "$f" ]]; then
      run_cmd "disable the old $legacy.conf (renamed, never deleted)" mv "$f" "$f.off"
    fi
  done

  if (( DRY_RUN )); then
    echo "  [dry-run] write $DROPIN_DIR/driveway-preset.conf:"
    echo "            [Service]"
    echo "            Environment=$env_line"
  else
    { echo "[Service]"; echo "Environment=$env_line"; } > "$DROPIN_DIR/driveway-preset.conf"
    echo "  wrote $DROPIN_DIR/driveway-preset.conf"
  fi

  run_cmd "reload systemd user units" systemctl --user daemon-reload
  run_cmd "restart the recorder" systemctl --user restart omacar-drivelog

  if (( ! DRY_RUN )); then
    sleep 1
    local status_text all_found=1 tok
    status_text="$("$OMACAR_BIN" drive status || true)"
    echo "$status_text"
    while read -r tok; do
      [[ -z "$tok" ]] && continue
      grep -qE "(^|[^0-9.])${tok}([^0-9.]|$)" <<<"$status_text" || all_found=0
    done <<<"$(preset_status_tokens "$env_line")"
    if (( all_found )); then
      echo "  ${GREEN}pass${RESET} — the running supervisor reports this preset's numbers."
    else
      echo "  ${YELLOW}check by hand${RESET} — the running supervisor's numbers don't obviously" \
           "match. Run \`omacar drive status\` yourself."
    fi
  fi
}

# Always run from cleanup(), on every exit path — a session directory that
# has a transcript and no summary.json is exactly the failure item 11 of the
# brief this script was written against exists to prevent. Safe in
# --dry-run (no captures, so no --capture-a/--capture-b flags) and safe
# after an abort (ABORTED_REASON is just another string field).
write_summary() {
  local args=(write-summary --out "$SESSION_DIR" --stamp "$STAMP" --mode "$MODE"
              --dry-run "$DRY_RUN" --parked "$PARKED" --applied "$APPLIED")
  [[ -n "$ABORTED_REASON" ]] && args+=(--aborted "$ABORTED_REASON")
  [[ -n "$CAP_A_PATH" ]] && args+=(--capture-a "$CAP_A_PATH")
  [[ -n "$CAP_B_PATH" ]] && args+=(--capture-b "$CAP_B_PATH")
  [[ -n "$RECONNECT_A" ]] && args+=(--reconnect-a "$RECONNECT_A")
  [[ -n "$RECONNECT_B" ]] && args+=(--reconnect-b "$RECONNECT_B")
  [[ -n "$CHOSEN_PRESET" ]] && args+=(--preset "$CHOSEN_PRESET")
  [[ -n "$CHOSEN_ENV" ]] && args+=(--env "$CHOSEN_ENV")
  "$OMACAR_PY" "$VERDICT_PY" "${args[@]}" >/dev/null 2>>"$LOG" || \
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
      echo "  nothing was recommended and nothing was installed."
    elif [[ -n "$CHOSEN_PRESET" ]]; then
      echo "  preset    $CHOSEN_PRESET"
      if (( APPLIED )); then
        echo "  installed — the recorder is running with it now."
      else
        echo "  not installed. Install later with:"
        echo "    tools/driveway-check.sh --apply-preset $CHOSEN_PRESET"
      fi
    elif [[ "$MODE" != "test" ]]; then
      echo "  $MODE done."
    fi
    echo "  ────────────────────────────────────────────────────────"
    echo
  } | tee -a "$SESSION_DIR/summary.txt"
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  # MODE=test is the only mode that ever calls "drive off" (see step 3
  # below), so it is the only one whose dry-run plan should show this — an
  # --apply-preset or --remove-preset dry run never touched the recorder's
  # on/off state and must not claim it will turn it back on.
  if (( DRY_RUN )) && [[ "$MODE" == "test" ]]; then
    echo
    echo "  [dry-run] let the recorder run again"
    echo "            $OMACAR_BIN drive on"
  elif (( STARTED )) && (( ! DRY_RUN )); then
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
trap cleanup EXIT INT TERM

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
fi

# ------------------------------------------------------- --apply-preset
if [[ "$MODE" == "apply-preset" ]]; then
  verdict_call "looking up the $PRESET_NAME preset" preset-for "$PRESET_NAME"
  CHOSEN_PRESET="$(kv preset)"
  CHOSEN_ENV="$(kv env)"
  if (( ! DRY_RUN )) && [[ -z "$CHOSEN_ENV" ]]; then
    abort "could not look up preset '$PRESET_NAME'"
  fi
  apply_preset "${CHOSEN_PRESET:-$PRESET_NAME}" "$CHOSEN_ENV"
  (( ! DRY_RUN )) && APPLIED=1
  exit 0
fi

# ------------------------------------------------------- --remove-preset
if [[ "$MODE" == "remove-preset" ]]; then
  F="$DROPIN_DIR/driveway-preset.conf"
  if (( DRY_RUN )); then
    echo "  [dry-run] rename $F to $F.off, if it exists"
    echo "  [dry-run] systemctl --user daemon-reload"
    echo "  [dry-run] systemctl --user restart omacar-drivelog"
  else
    if [[ -f "$F" ]]; then
      run_cmd "disable driveway-preset.conf (renamed, never deleted)" mv "$F" "$F.off"
    else
      echo "  no driveway-preset.conf to remove — already at defaults."
    fi
    run_cmd "reload systemd user units" systemctl --user daemon-reload
    run_cmd "restart the recorder" systemctl --user restart omacar-drivelog
  fi
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

# Step 2 — preflight: the daemon running, live data fresh. Refuse plainly if
# not; print RPM if it is known.
if (( ! DRY_RUN )); then
  echo "  checking the recorder can see a live car…"
  LIVE_OUT="$(read_live)"
  FRESH="$(sed -n 's/^fresh=\(.*\)$/\1/p' <<<"$LIVE_OUT")"
  [[ "$FRESH" == "1" ]] || abort "no live data from the daemon — is it running? Check \`omacar daemon status\`."
  CONNECTED="$(sed -n 's/^connected=\(.*\)$/\1/p' <<<"$LIVE_OUT")"
  [[ "$CONNECTED" == "1" ]] || abort "live data says the adapter is not connected — plug in the OBDLink and try again."
  RPM="$(sed -n 's/^rpm=\(.*\)$/\1/p' <<<"$LIVE_OUT")"
  if [[ -n "$RPM" ]]; then
    echo "  live data is fresh — ${RPM} rpm"
  else
    echo "  live data is fresh — rpm not reported"
  fi
fi

# Step 3 — stop the recorder, remembering whether it was on. `omacar drive
# status` prints "stood down" (and its own report() returns rc=1) when the
# owner had already stood it down by hand; this check is what lets the trap
# below leave it that way instead of turning it back on behind their back.
if (( ! DRY_RUN )); then
  STATUS_BEFORE="$("$OMACAR_BIN" drive status || true)"
  if grep -q "stood down" <<<"$STATUS_BEFORE"; then
    WAS_ON=0
    echo "  the recorder was already stood down — this check will leave it that way."
  else
    WAS_ON=1
  fi
fi

run_cmd "stand the recorder down, so nothing else holds the port" "$OMACAR_BIN" drive off
(( ! DRY_RUN )) && STARTED=1

# Step 4 — capture A: the fast link alone. Exactly doc/drive-day.md's
# command. `timeout 90` turns a hang into exit 124, which is checked before
# anything is asked of the file it may or may not have saved.
echo
echo "  Capture A — the fast link alone (20s)"
CAP_A_NOTE="fastbaud-test"
(( ! DRY_RUN )) && BEFORE_T_A="$(live_t)"
run_cmd "capture A: fast link" timeout 90 env OMACAR_FASTBAUD=1 \
  "$OMACAR_BIN" listen capture --seconds 20 --save --note "$CAP_A_NOTE"
CAP_A_RC=$?

A_PASSED=0
CAP_A_WHY=""
CAP_A_GEAR=""
if (( ! DRY_RUN )); then
  if (( CAP_A_RC == 124 )); then
    echo "  ${RED}capture A hung — the 90s timeout fired.${RESET}"
    CAP_A_WHY="timed out (hang)"
  else
    CAP_A_PATH="$(latest_capture_for_note "$CAP_A_NOTE")"
    if [[ -z "$CAP_A_PATH" ]]; then
      echo "  ${RED}capture A did not save a file — cannot judge it.${RESET}"
      CAP_A_WHY="no capture file found"
    else
      verdict_call "judging capture A" verdict-a "$CAP_A_PATH"
      CAP_A_FRAMES="$(kv frames)"; CAP_A_SPAN="$(kv span)"
      CAP_A_WHY="$(kv why)"; CAP_A_GEAR="$(kv gear)"
      [[ "$(kv passed)" == "1" ]] && A_PASSED=1
      if (( A_PASSED )); then
        echo "  ${GREEN}capture A passed${RESET} — $CAP_A_WHY"
      else
        echo "  ${YELLOW}capture A failed${RESET} — $CAP_A_WHY"
      fi
    fi
  fi

  # Step 5 — the daemon reconnects. A failure here is reported loudly and
  # the rest is skipped: it means the port did not come back cleanly, which
  # makes every capture and every preset recommendation from this run
  # suspect, not just the one just taken. Safer to stop and say so than to
  # install anything on the strength of a run that just proved the port
  # itself is not behaving.
  echo "  waiting for the daemon to reconnect…"
  if RECONNECT_A="$(wait_reconnect "$BEFORE_T_A")"; then
    echo "  ${GREEN}reconnected${RESET} in ${RECONNECT_A}s"
  else
    echo
    echo "  ${RED}the daemon did not reconnect within 30s after capture A.${RESET}"
    ABORTED_REASON="the daemon did not reconnect within 30s after capture A"
  fi
fi

# Step 6 — capture B, only if A passed. In --dry-run there is no real A to
# have passed, so the plan always shows both captures; a real run gates on
# A_PASSED, matching the doc: "do not add OMACAR_CAF0 on top of a link that
# has not proven itself."
RUN_B=1
(( ! DRY_RUN )) && RUN_B=$A_PASSED
B_PASSED=0
CAP_B_WHY=""
CAP_B_GEAR=""

if [[ -z "$ABORTED_REASON" ]]; then
  if (( RUN_B )); then
    echo
    echo "  Capture B — fast link plus ATCAF0 (20s)"
    CAP_B_NOTE="caf0-test"
    (( ! DRY_RUN )) && BEFORE_T_B="$(live_t)"
    run_cmd "capture B: fast link + ATCAF0" timeout 90 env OMACAR_FASTBAUD=1 OMACAR_CAF0=1 \
      "$OMACAR_BIN" listen capture --seconds 20 --save --note "$CAP_B_NOTE"
    CAP_B_RC=$?

    if (( ! DRY_RUN )); then
      if (( CAP_B_RC == 124 )); then
        echo "  ${RED}capture B hung — the 90s timeout fired.${RESET}"
        CAP_B_WHY="timed out (hang)"
      else
        CAP_B_PATH="$(latest_capture_for_note "$CAP_B_NOTE")"
        if [[ -z "$CAP_B_PATH" ]]; then
          echo "  ${RED}capture B did not save a file — cannot judge it.${RESET}"
          CAP_B_WHY="no capture file found"
        else
          verdict_call "judging capture B" verdict-b "$CAP_B_PATH"
          CAP_B_WHY="$(kv why)"; CAP_B_GEAR="$(kv gear)"
          [[ "$(kv passed)" == "1" ]] && B_PASSED=1
          if (( B_PASSED )); then
            echo "  ${GREEN}capture B passed${RESET} — $CAP_B_WHY"
          else
            echo "  ${YELLOW}capture B failed${RESET} — $CAP_B_WHY"
          fi
        fi
      fi

      # Same reconnect check, same reasoning: a failure here stops the run
      # just as hard as one after capture A.
      echo "  waiting for the daemon to reconnect…"
      if RECONNECT_B="$(wait_reconnect "$BEFORE_T_B")"; then
        echo "  ${GREEN}reconnected${RESET} in ${RECONNECT_B}s"
      else
        echo
        echo "  ${RED}the daemon did not reconnect within 30s after capture B.${RESET}"
        ABORTED_REASON="the daemon did not reconnect within 30s after capture B"
      fi
    fi
  else
    echo
    echo "  ${DIM}capture A did not pass — skipping capture B (the doc: never add" \
         "ATCAF0 on top of a link that has not proven itself).${RESET}"
  fi
fi

# Step 7 — gear position heard in each capture, information only. A free
# cross-check that the car was in P throughout; never a pass/fail gate.
if [[ -n "$CAP_A_GEAR" || -n "$CAP_B_GEAR" ]]; then
  echo
  echo "  Gear position heard (0x191 byte 0; 0x01 = P) — information only:"
  [[ -n "$CAP_A_GEAR" && "$CAP_A_GEAR" != "none" ]] && echo "    capture A: ${CAP_A_GEAR//;/, }"
  [[ -n "$CAP_B_GEAR" && "$CAP_B_GEAR" != "none" ]] && echo "    capture B: ${CAP_B_GEAR//;/, }"
fi

# Step 8 — verdict and preset. Skipped if step 5 or 6 aborted; in --dry-run
# there are no real verdicts, so this only prints the shape of the call.
if [[ -z "$ABORTED_REASON" ]]; then
  if (( DRY_RUN )); then
    echo
    echo "  [dry-run] pick a preset from today's two verdicts and estimate the telemetry share:"
    echo "            $OMACAR_PY $VERDICT_PY preset <A_PASSED> <B_PASSED>"
    echo "            $OMACAR_PY $VERDICT_PY telemetry <preset> <env> <measured-rate-or-none>"
  else
    B_ARG=0
    (( RUN_B )) && B_ARG=$B_PASSED
    verdict_call "picking today's preset" preset "$A_PASSED" "$B_ARG"
    CHOSEN_PRESET="$(kv preset)"
    CHOSEN_ENV="$(kv env)"

    RATE_ARG="none"
    if (( A_PASSED )) && [[ -n "$CAP_A_FRAMES" && -n "$CAP_A_SPAN" ]]; then
      RATE_ARG="$("$OMACAR_PY" -c "print($CAP_A_FRAMES/$CAP_A_SPAN)" 2>/dev/null || echo none)"
    fi
    verdict_call "estimating today's telemetry share" telemetry "$CHOSEN_PRESET" "$CHOSEN_ENV" "$RATE_ARG"
    TELEMETRY_PCT="$(kv pct)"
    TELE_MEASURED="$(kv measured)"
    TELE_LEG_HOLD="$(kv leg_hold)"
    TELE_CYCLE="$(kv cycle)"

    echo
    echo "  ${BOLD}Verdict${RESET}"
    if (( A_PASSED )); then echo "    capture A: pass — $CAP_A_WHY"
    else echo "    capture A: fail — $CAP_A_WHY"; fi
    if (( RUN_B )); then
      if (( B_PASSED )); then echo "    capture B: pass — $CAP_B_WHY"
      else echo "    capture B: fail — $CAP_B_WHY"; fi
    else
      echo "    capture B: not run"
    fi
    echo "    preset:    $CHOSEN_PRESET"
    echo "    would set: $CHOSEN_ENV"
    if [[ "$TELE_MEASURED" == "1" ]]; then
      echo "    expected telemetry: about ${TELEMETRY_PCT}% (measured today: leg holds the" \
           "port ~${TELE_LEG_HOLD}s, cycle ~${TELE_CYCLE}s)"
    else
      echo "    expected telemetry: about ${TELEMETRY_PCT}% (estimated from" \
           "doc/drive-day.md's own numbers, not measured this session:" \
           "leg ~${TELE_LEG_HOLD}s, cycle ~${TELE_CYCLE}s)"
    fi
  fi
fi

# Step 9 — apply, only with --apply, or interactively if the person types
# "yes" to "Install it now?".
DO_APPLY=0
if [[ -z "$ABORTED_REASON" && -n "$CHOSEN_PRESET" ]]; then
  if (( APPLY )); then
    DO_APPLY=1
  elif (( ! DRY_RUN )); then
    read -r -p "  Install it now? [yes/N] " INSTALL_REPLY || true
    [[ "${INSTALL_REPLY:-}" == "yes" ]] && DO_APPLY=1
  fi
fi

if (( DRY_RUN )); then
  echo
  echo "  [dry-run] install the chosen preset, if asked (--apply, or 'yes' at the prompt):"
  echo "            mkdir -p $DROPIN_DIR"
  echo "            write $DROPIN_DIR/driveway-preset.conf"
  echo "            systemctl --user daemon-reload"
  echo "            systemctl --user restart omacar-drivelog"
  echo "            $OMACAR_BIN drive status"
elif (( DO_APPLY )); then
  apply_preset "$CHOSEN_PRESET" "$CHOSEN_ENV"
  APPLIED=1
elif [[ -n "$CHOSEN_PRESET" ]]; then
  echo
  echo "  not installed. Install later with:"
  echo "    tools/driveway-check.sh --apply-preset $CHOSEN_PRESET"
fi

exit 0
