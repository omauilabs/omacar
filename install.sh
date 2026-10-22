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
oa_context_install
oa_vortex_install
oa_bar_place

# Opt-in: Work by voice (doc/work.md). The Agent SDK and the speech-to-text
# engine are large and only the machine that runs the relay needs them.
# Piper is a separate system package (GPL-3.0, never bundled).
if [[ "${OMACAR_WORK:-0}" == 1 ]]; then
  VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omacar/venv"
  ROOT="$OA_ROOT"
  if [[ -x "$VENV/bin/pip" ]]; then
    "$VENV/bin/pip" install --quiet claude-agent-sdk faster-whisper
    if command -v piper >/dev/null; then
      "$VENV/bin/python" "$ROOT/tools/work_render_offline.py" \
        || echo "  Work: the offline lines did not render"
    else
      echo "  Work: install Piper (system package) for spoken lines"
    fi
  else
    echo "  Work: no Python environment yet; run 'omacar setup', then this again"
  fi
fi

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
