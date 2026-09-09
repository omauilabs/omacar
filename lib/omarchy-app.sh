# omarchy-app.sh — the shared install/uninstall primitives behind Omarchy apps.
#
# This file is VENDORED into each generated app (lib/omarchy-app.sh), not
# depended on at runtime. An app repo must stay self-contained: clone, run
# ./install.sh, done. Re-vendor with `omarchy-app-new --update .`.
#
# Everything derives from $HOME, which is what makes the whole installer
# testable: run it with HOME=/tmp/whatever and it touches nothing real.
#
#   source lib/omarchy-app.sh
#   oa_init omacar "Car Diagnostics"

set -euo pipefail

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

oa_init() { # slug "Display Name"
  OA_APP="$1"
  OA_NAME="${2:-$1}"
  OA_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[1]}")")" && pwd)"

  # Live desktop calls are no-ops when we're installing into a scratch HOME.
  OA_LIVE=1
  [[ "$HOME" == "$(getent passwd "$(id -u)" | cut -d: -f6)" ]] || OA_LIVE=0

  # A scratch HOME has to isolate *everything*, and XDG_* are absolute paths
  # that happily survive a HOME override — so a test that only overrides HOME
  # will still find, and an uninstall will still delete, the real state
  # directory. Re-point them inside the scratch HOME and export, so child
  # processes (python helpers, daemons) are isolated too.
  if ((OA_LIVE == 0)); then
    export XDG_CONFIG_HOME="$HOME/.config"
    export XDG_STATE_HOME="$HOME/.local/state"
    export XDG_DATA_HOME="$HOME/.local/share"
    export XDG_CACHE_HOME="$HOME/.cache"
  fi

  OA_BIN="$HOME/.local/bin"
  OA_CMD="$OA_BIN/$OA_APP"
  OA_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
  OA_PLUGIN_DIR="$OA_CONFIG/omarchy/plugins/$OA_APP"
  OA_MENU_FILE="$OA_CONFIG/omarchy/extensions/omarchy-menu.jsonc"
  OA_SHELL_JSON="$OA_CONFIG/omarchy/shell.json"
  OA_BINDINGS_FILE="$OA_CONFIG/hypr/bindings.lua"
  OA_AUTOSTART_FILE="$OA_CONFIG/hypr/autostart.lua"
  OA_ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
  OA_APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  OA_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$OA_APP"
  OA_TOGGLE_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/toggles/hypr/$OA_APP.lua"
}

oa_say()  { printf '  \033[1m%s\033[0m %s\n' "${1:-}" "${2:-}"; }
oa_warn() { printf '  \033[1;33m!\033[0m %s\n' "$1" >&2; }
oa_die()  { printf '  \033[1;31merror\033[0m %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Commands on PATH
# ---------------------------------------------------------------------------

oa_bin_link() {
  mkdir -p "$OA_BIN"
  local tool
  for tool in "$OA_ROOT"/bin/*; do
    [[ -f "$tool" ]] || continue
    ln -sfn "$tool" "$OA_BIN/$(basename "$tool")"
  done
  oa_say "" "$OA_APP on your PATH"
}

# ---------------------------------------------------------------------------
# Icon + desktop launcher
# ---------------------------------------------------------------------------

oa_icon_install() {
  [[ -f "$OA_ROOT/share/icon.svg" ]] || return 0
  mkdir -p "$OA_ICON_DIR"
  if command -v rsvg-convert >/dev/null; then
    rsvg-convert -w 256 -h 256 "$OA_ROOT/share/icon.svg" -o "$OA_ICON_DIR/$OA_APP.png"
  elif command -v magick >/dev/null; then
    magick -background none "$OA_ROOT/share/icon.svg" -resize 256x256 "$OA_ICON_DIR/$OA_APP.png"
  fi
}

# StartupWMClass is what ties a Chromium web-app window back to this entry.
# Without it the window's own class (chrome-127.0.0.1__app.html-Default)
# matches nothing and the dock falls back to a generic icon.
oa_desktop_entry() { # "Comment" "Exec" "Categories" "Keywords" ["StartupWMClass"]
  mkdir -p "$OA_APPS_DIR"
  {
    echo "[Desktop Entry]"
    echo "Type=Application"
    echo "Name=$OA_NAME"
    echo "Comment=$1"
    echo "Exec=$2"
    echo "Icon=$OA_APP"
    echo "Terminal=false"
    [[ -n "${5:-}" ]] && echo "StartupWMClass=$5"
    echo "Categories=$3"
    echo "Keywords=$4"
  } >"$OA_APPS_DIR/$OA_APP.desktop"
  gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
  oa_say "" "app launcher entry installed (search: $OA_NAME)"
}

# ---------------------------------------------------------------------------
# Managed config blocks
# ---------------------------------------------------------------------------

# Replace (or append) a marker-delimited block in a Lua config file. Other
# sessions edit these files too, so we only ever touch our own block.
oa_lua_block() { # file content
  local file="$1" content="$2" tmp
  tmp=$(mktemp)
  mkdir -p "$(dirname "$file")"
  touch "$file"
  sed "/^-- >>> $OA_APP/,/^-- <<< $OA_APP/d" "$file" >"$tmp"
  # Trim trailing blank lines so repeated installs don't grow the file.
  sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp" 2>/dev/null || true
  printf '\n-- >>> %s (managed by %s/install.sh — edits inside are overwritten)\n%s\n-- <<< %s\n' \
    "$OA_APP" "$OA_APP" "$content" "$OA_APP" >>"$tmp"
  mv "$tmp" "$file"
}

# Splice menu/menu-entries.jsonc in just above the menu file's closing brace.
# __CMD__ in the entries file is replaced with the installed command path.
oa_menu_splice() {
  [[ -f "$OA_ROOT/menu/menu-entries.jsonc" ]] || return 0
  mkdir -p "$(dirname "$OA_MENU_FILE")"
  [[ -f "$OA_MENU_FILE" ]] || printf '{\n}\n' >"$OA_MENU_FILE"

  local tmp close_line
  tmp=$(mktemp)
  sed "/\/\/ >>> $OA_APP/,/\/\/ <<< $OA_APP/d" "$OA_MENU_FILE" >"$tmp"

  close_line=$(grep -n '^[[:space:]]*}[[:space:]]*$' "$tmp" | tail -1 | cut -d: -f1)
  if [[ -z "$close_line" ]]; then
    rm -f "$tmp"
    oa_die "$OA_MENU_FILE has no closing brace; fix it and rerun."
  fi
  {
    head -n "$((close_line - 1))" "$tmp"
    echo "  // >>> $OA_APP (managed by $OA_APP/install.sh)"
    sed "s|__CMD__|$OA_CMD|g; s/^/  /" "$OA_ROOT/menu/menu-entries.jsonc"
    echo "  // <<< $OA_APP"
    tail -n "+$close_line" "$tmp"
  } >"$OA_MENU_FILE"
  rm -f "$tmp"
  oa_say "" "menu entries added (Omarchy menu → $OA_NAME)"
}

# ---------------------------------------------------------------------------
# Bar plugin
# ---------------------------------------------------------------------------

# Copy, never symlink: the shell's hot-reload watches the real plugin
# directory and does not see writes through a symlink.
oa_plugin_install() {
  [[ -f "$OA_ROOT/plugin/manifest.json" ]] || return 0
  if [[ -e "$OA_PLUGIN_DIR" && ! -L "$OA_PLUGIN_DIR" && ! -f "$OA_PLUGIN_DIR/manifest.json" ]]; then
    oa_die "$OA_PLUGIN_DIR exists and isn't ours — move it aside first."
  fi
  [[ -L "$OA_PLUGIN_DIR" ]] && rm "$OA_PLUGIN_DIR"
  mkdir -p "$OA_PLUGIN_DIR"
  cp -f "$OA_ROOT"/plugin/* "$OA_PLUGIN_DIR/"

  # The overlay service mounts only for plugins listed in shell.json's
  # plugins[]; the bar layout alone does not load it.
  if [[ -f "$OA_SHELL_JSON" ]] && command -v jq >/dev/null &&
     ! jq -e --arg id "$OA_APP" '.plugins[]? | select(.id == $id)' "$OA_SHELL_JSON" >/dev/null 2>&1; then
    local tmp; tmp=$(mktemp)
    jq --arg id "$OA_APP" '.plugins = ((.plugins // []) + [{"id":$id}])' "$OA_SHELL_JSON" >"$tmp" &&
      mv "$tmp" "$OA_SHELL_JSON"
  fi

  ((OA_LIVE)) && omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  oa_say "" "bar plugin registered"
}

# Install the user units this app ships, with the paths of THIS checkout.
#
# WHY THEY ARE TEMPLATED RATHER THAN COPIED.
#
# Units carrying `%h/Projects/omacar` work for exactly one person: whoever put
# the checkout where the author did. On this machine the five units lived only
# in ~/.config/systemd/user, hand-placed, and the udev rule that ships in the
# repo names omacar-daemon.service -- a unit that existed nowhere in it. The
# auto-start half had nothing to start on any machine but one.
#
# __ROOT__ becomes this checkout, so a unit is correct wherever the repo was
# cloned. The interpreter is NOT substituted here: it is resolved when the unit
# starts, by sourcing lib/env.sh, because `omacar setup` builds the venv AFTER
# install.sh runs -- baking the path in at install time would freeze every unit
# on the system python that cannot import obd. It also avoids capturing a
# version-manager shim, which resolves through XDG_DATA_HOME and breaks the
# moment anything redirects it.
#
# Nothing is enabled here. A unit that starts a fullscreen gauge or a polling
# daemon at login is right on a tablet in a car and rude on a laptop; `omacar
# tablet setup` is where that choice is made, deliberately and reversibly.
# Tell every agent on this machine what is plugged in.
#
# Omarchy Vortex collects executables from ~/.config/omarchy/context.d/ and puts
# their `key: value` lines in front of every agent it launches, and in front of
# the voice assistant with every question. Dropping a file in is the whole
# integration -- there is nothing to register and no schema to extend, which is
# the point of that design.
#
# Absent Vortex, this costs nothing and does nothing: the directory simply is
# not read. OmaCar must work with no Vortex at all, because Vortex's own
# documentation says it is not installable by anybody else yet.
oa_context_install() {
  [[ -d "$OA_ROOT/share/context.d" ]] || return 0
  local dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/context.d"
  # Only where Omarchy is. Creating this on a machine that has no Vortex would
  # leave a directory nothing reads and imply an integration that is not there.
  [[ -d "${XDG_CONFIG_HOME:-$HOME/.config}/omarchy" ]] || return 0
  mkdir -p "$dir"
  local f n=0
  for f in "$OA_ROOT"/share/context.d/*; do
    [[ -f "$f" ]] || continue
    install -m 755 "$f" "$dir/$(basename "$f")"
    n=$((n + 1))
  done
  ((n)) && oa_say "" "car facts added to the agent context"
  return 0
}

oa_vortex_install() {
  # Lend the voice assistant the car's tools. Vortex reads `mcp` in
  # vortex.json and admits exactly the servers named there (see converse.py
  # in the Vortex tree). Only where Vortex is, only with jq, and idempotent:
  # the entry is keyed by app name and rewritten in place.
  local cfg="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/vortex.json"
  [[ -d "${XDG_CONFIG_HOME:-$HOME/.config}/omarchy" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  [[ -f "$cfg" ]] || echo '{}' >"$cfg"
  local tmp; tmp="$(mktemp "${cfg}.XXXXXX")"
  if jq --arg app "$OA_APP" --arg cmd "$OA_CMD" \
       '.mcp = ((.mcp // {}) + {($app): {"command": $cmd, "args": ["mcp"]}})' \
       "$cfg" >"$tmp" 2>/dev/null; then
    mv "$tmp" "$cfg"
    oa_say "" "car tools lent to the voice assistant (vortex.json → mcp.$OA_APP)"
  else
    rm -f "$tmp"
    oa_warn "could not update $cfg; the voice assistant will not see the car"
  fi
  return 0
}

oa_unit_install() {
  [[ -d "$OA_ROOT/share/systemd" ]] || return 0
  local dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$dir"
  local n=0 f name
  # TIMERS AS WELL AS SERVICES. A .service with no .timer beside it is a unit
  # that can only be started by hand, which for anything periodic means it is
  # never started at all -- and the timer file was sitting in the tree being
  # ignored by this loop.
  for f in "$OA_ROOT"/share/systemd/*.service "$OA_ROOT"/share/systemd/*.timer; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f")"
    sed -e "s|__ROOT__|$OA_ROOT|g" "$f" >"$dir/$name"
    n=$((n + 1))
  done
  ((n)) || return 0
  ((OA_LIVE)) && systemctl --user daemon-reload >/dev/null 2>&1 || true
  oa_say "" "$n user units installed (none enabled — see: $OA_APP tablet)"
}

# Is our widget actually on the bar? The one question that decides everything
# in oa_bar_place, asked of the file rather than of a command's exit code.
oa_bar_has() {
  jq -e --arg id "$OA_APP" \
    '.bar.layout | .. | objects | select(.id? == $id)' \
    "$OA_SHELL_JSON" >/dev/null 2>&1
}

oa_bar_place() { # [--before other.widget]
  [[ -f "$OA_ROOT/plugin/manifest.json" ]] || return 0
  ((OA_LIVE)) || return 0
  if oa_bar_has; then
    oa_say "" "widget already in the bar"
    return 0
  fi
  # ASK THE SHELL FIRST, THEN CHECK WHETHER IT DID IT.
  #
  # `omarchy bar put` hands the request to the running shell, which owns the
  # config it has in memory. That is the right way round and it is the path to
  # prefer -- but it answers "ok" and prints "<id> is on the bar" in cases
  # where nothing is written, and it exits 0 while doing so. Trusting the exit
  # code left this reporting a widget placed that was not, which is the exact
  # shape of lie the rest of this project spends its time refusing.
  #
  # It also cannot work at all from a non-graphical shell: OMARCHY_PATH comes
  # from the session, so over ssh its helper dies on an unbound variable. That
  # is how a perfectly good manifest came to look broken.
  #
  # So: ask, then VERIFY against the file, and only if the shell did not do it
  # write the entry ourselves -- the same one-line object the other third-party
  # widgets carry -- behind a backup and a JSON validity check.
  omarchy-bar put "$OA_APP" "$@" >/dev/null 2>&1 ||
    omarchy-bar put "$OA_APP" --section right >/dev/null 2>&1 || true
  if oa_bar_has; then
    oa_say "" "widget placed in the bar"
    return 0
  fi

  local tmp backup
  backup="$OA_SHELL_JSON.bak-$OA_APP-$(date +%Y%m%d-%H%M%S)"
  cp -f "$OA_SHELL_JSON" "$backup" 2>/dev/null || {
    oa_warn "couldn't back up $OA_SHELL_JSON — leaving the bar alone"
    return 0
  }
  tmp="$(mktemp)"
  if jq --arg id "$OA_APP" \
       '.bar.layout.right |= ((. // []) + [{"id": $id}])' \
       "$OA_SHELL_JSON" >"$tmp" 2>/dev/null && jq -e . "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$OA_SHELL_JSON"
    if oa_bar_has; then
      oa_say "" "widget placed in the bar"
      rm -f "$backup"
      return 0
    fi
  fi
  rm -f "$tmp"
  # Put back exactly what was there. A half-edited shell config is a desktop
  # that does not come up, and that is a far worse outcome than no widget.
  mv -f "$backup" "$OA_SHELL_JSON" 2>/dev/null || true
  oa_warn "couldn't place the bar widget — run: omarchy bar put $OA_APP"
}

# ---------------------------------------------------------------------------
# Hyprland
# ---------------------------------------------------------------------------

oa_hypr_reload() { # "success message"
  ((OA_LIVE)) || return 0
  hyprctl reload >/dev/null 2>&1 || true
  local errors
  errors=$(hyprctl configerrors 2>/dev/null || true)
  if [[ -n "$errors" && "$errors" != *"no errors"* ]]; then
    oa_warn "hyprctl configerrors reported:"
    echo "$errors" >&2
  else
    [[ -n "${1:-}" ]] && oa_say "" "$1"
  fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

# Reverses every hook the install placed. Deliberately not `set -e` sensitive:
# a partial install must still uninstall cleanly.
# Take the units back out. Stopped and disabled first: removing the file under a
# running unit leaves systemd holding a process it can no longer describe.
oa_context_remove() {
  local dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/context.d"
  [[ -d "$OA_ROOT/share/context.d" ]] || return 0
  local f
  for f in "$OA_ROOT"/share/context.d/*; do
    [[ -f "$f" ]] && rm -f "$dir/$(basename "$f")"
  done
  return 0
}

oa_vortex_remove() {
  local cfg="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/vortex.json"
  [[ -f "$cfg" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  local tmp; tmp="$(mktemp "${cfg}.XXXXXX")"
  if jq --arg app "$OA_APP" 'if .mcp then .mcp |= del(.[$app]) else . end
                             | if .mcp == {} then del(.mcp) else . end' \
       "$cfg" >"$tmp" 2>/dev/null; then
    mv "$tmp" "$cfg"
  else
    rm -f "$tmp"
  fi
  return 0
}

oa_unit_remove() {
  local dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  [[ -d "$OA_ROOT/share/systemd" ]] || return 0
  local f name n=0
  for f in "$OA_ROOT"/share/systemd/*.service; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f")"
    [[ -f "$dir/$name" ]] || continue
    ((OA_LIVE)) && systemctl --user disable --now "$name" >/dev/null 2>&1 || true
    rm -f "$dir/$name"
    n=$((n + 1))
  done
  ((n)) && { ((OA_LIVE)) && systemctl --user daemon-reload >/dev/null 2>&1 || true; \
             oa_say "" "$n user units removed"; }
  return 0
}

oa_remove() {
  local tool
  if [[ -f "$OA_SHELL_JSON" ]] && command -v jq >/dev/null; then
    local tmp; tmp=$(mktemp)
    jq --arg id "$OA_APP" '
      (.bar.layout // {}) |= with_entries(.value |= (if type == "array" then map(select(.id != $id)) else . end))
      | .plugins = ((.plugins // []) | map(select(.id != $id)))' \
      "$OA_SHELL_JSON" >"$tmp" 2>/dev/null && mv "$tmp" "$OA_SHELL_JSON" || rm -f "$tmp"
  fi

  sed -i "/^-- >>> $OA_APP/,/^-- <<< $OA_APP/d" "$OA_BINDINGS_FILE" 2>/dev/null || true
  sed -i "/^-- >>> $OA_APP/,/^-- <<< $OA_APP/d" "$OA_AUTOSTART_FILE" 2>/dev/null || true
  sed -i "/\/\/ >>> $OA_APP/,/\/\/ <<< $OA_APP/d" "$OA_MENU_FILE" 2>/dev/null || true

  for tool in "$OA_ROOT"/bin/*; do
    [[ -f "$tool" ]] && rm -f "$OA_BIN/$(basename "$tool")"
  done
  rm -f "$OA_TOGGLE_FILE" "$OA_APPS_DIR/$OA_APP.desktop" "$OA_ICON_DIR/$OA_APP.png"
  [[ -L "$OA_PLUGIN_DIR" ]] && rm "$OA_PLUGIN_DIR" || rm -rf "$OA_PLUGIN_DIR"

  if ((OA_LIVE)); then
    hyprctl reload >/dev/null 2>&1 || true
    omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  fi
}
