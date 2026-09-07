#!/bin/bash
#
# OmaCar installer. Idempotent: run it again after a git pull.
#
#   ./install.sh          CLI, launcher, icon, and Omarchy menu entries
#   ./install.sh --bind   also bind SUPER + SHIFT + C to open the app
#
# Everything derives from $HOME, so `HOME=/tmp/scratch ./install.sh` is a
# safe full-fidelity dry run — that is exactly what test/smoke.sh does.

set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/omarchy-app.sh"
oa_init omacar "OmaCar"

oa_bin_link
oa_icon_install
oa_desktop_entry "Live OBD-II diagnostics for your car" "$OA_CMD open" "Utility;" "omacar;" \
  "chrome-127.0.0.1__app.html-Default"
oa_menu_splice
oa_plugin_install
oa_unit_install
oa_bar_place

# Opt-in keybinding: this desktop is shared, so we never grab a chord uninvited.
if [[ "${1:-}" == "--bind" ]]; then
  oa_lua_block "$OA_BINDINGS_FILE" \
    "o.bind(\"SUPER + SHIFT + C\", \"OmaCar\", \"$OA_CMD open\")"
  oa_hypr_reload "SUPER + SHIFT + C opens the app"
fi

echo
echo "  OmaCar is in. Try:"
echo "    omacar             open the app"
echo "    omacar help        everything else"
echo
