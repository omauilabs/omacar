#!/bin/bash
#
# OmaCar uninstaller: removes every hook the installer placed.
#
#   ./uninstall.sh            remove the app, KEEP the car's records
#   ./uninstall.sh --purge    remove the app and delete the records too
#
# THE RECORDS ARE NOT THE APP, AND THIS USED TO DELETE THEM ANYWAY.
#
# `rm -rf "$OA_STATE_DIR"` ran unconditionally and said nothing first. That
# directory is every mile the tool has watched: one SQLite database per VIN with
# the samples, the trips, the fault history with its first-seen dates, the
# service book somebody typed in by hand, the photographs filed against a code,
# and the discovery sweeps that cost seventy minutes of a car's time each.
#
# None of that is reinstallable. A trouble code's first-seen date cannot be
# recovered by running the tool again -- it is a fact about a moment that has
# passed. Somebody uninstalling to try a newer checkout, or to move the app to
# another disk, would have lost eleven years of a car with one command and no
# warning, which is the sort of thing you only discover afterwards.
#
# So the default keeps it and says where it is. --purge is the way to mean it.

set -uo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/omarchy-app.sh"
set +e
oa_init omacar "OmaCar"

PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '3,7p' "$0"; exit 0 ;;
    *) oa_warn "unknown option: $a" ;;
  esac
done

"$OA_CMD" server stop >/dev/null 2>&1

oa_unit_remove
oa_context_remove
oa_remove

if ((PURGE)); then
  # Say what is going before it goes. A count is cheap and it is the difference
  # between a decision and a surprise.
  vehicles=0
  [[ -d "$OA_STATE_DIR/vehicles" ]] &&
    vehicles=$(find "$OA_STATE_DIR/vehicles" -name '*.db' 2>/dev/null | wc -l)
  rm -rf "$OA_STATE_DIR"
  echo
  echo "  OmaCar removed, and $vehicles vehicle record(s) deleted."
else
  if [[ -d "$OA_STATE_DIR" ]]; then
    size=$(du -sh "$OA_STATE_DIR" 2>/dev/null | cut -f1)
    echo
    echo "  OmaCar removed. Your car's records were kept:"
    echo
    echo "      $OA_STATE_DIR   ($size)"
    echo
    echo "  Reinstalling picks them straight back up. To delete them:"
    echo "      ./uninstall.sh --purge"
  else
    echo
    echo "  OmaCar removed."
  fi
fi
