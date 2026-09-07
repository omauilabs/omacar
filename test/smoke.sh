#!/bin/bash
#
# OmaCar smoke test.
#
# Installs into a scratch HOME, asserts every hook landed, uninstalls, then
# asserts the scratch HOME is back to how it started. Touches nothing real —
# safe to run on a live desktop with other sessions working.

fails=0
ok()   { printf '   ok  %s\n' "$1"; }
bad()  { printf ' FAIL  %s\n' "$1"; fails=$((fails + 1)); }
check(){ if [[ "$2" == "$3" ]]; then ok "$1"; else printf ' FAIL  %s (expected %q, got %q)\n' "$1" "$2" "$3"; fails=$((fails+1)); fi; }

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

echo
echo "  OmaCar smoke test (scratch HOME: $SCRATCH)"
echo

# The interpreter, resolved BEFORE the environment is mutated.
#
# `python3` on a mise-managed machine is a shim that resolves the real
# interpreter through XDG_DATA_HOME and XDG_CACHE_HOME. This test redirects both
# into the scratch, so the shim looked in an empty directory, failed, and every
# assertion that shelled out to python3 reported FAIL -- for a reason that had
# nothing to do with what was being tested. Capture the real path first and use
# it throughout.
PY="$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null || command -v python3)"

# Isolate XDG too. These are absolute paths that survive a HOME override, and
# an uninstall deletes its state directory — a test that only overrides HOME
# will happily delete the real one.
export XDG_CONFIG_HOME="$SCRATCH/.config" XDG_STATE_HOME="$SCRATCH/.local/state"
export XDG_DATA_HOME="$SCRATCH/.local/share" XDG_CACHE_HOME="$SCRATCH/.cache"

# Sentinel: prove afterwards that the real state directory was never touched.
REAL_STATE="$(getent passwd "$(id -u)" | cut -d: -f6)/.local/state/omacar"
mkdir -p "$REAL_STATE"
date +%s%N >"$REAL_STATE/.smoke-sentinel"
sentinel_before=$(cat "$REAL_STATE/.smoke-sentinel")

mkdir -p "$SCRATCH/.config/omarchy/extensions" "$SCRATCH/.config/hypr"
printf '{\n}\n' >"$SCRATCH/.config/omarchy/extensions/omarchy-menu.jsonc"
printf '{"plugins":[],"bar":{"layout":{}}}\n' >"$SCRATCH/.config/omarchy/shell.json"
printf -- '-- existing user config\n' >"$SCRATCH/.config/hypr/bindings.lua"
before_bindings=$(cat "$SCRATCH/.config/hypr/bindings.lua")

HOME="$SCRATCH" "$ROOT/install.sh" --bind >/dev/null 2>&1 || bad "install.sh exited non-zero"

[[ -L "$SCRATCH/.local/bin/omacar" ]] && ok "CLI symlinked onto PATH" || bad "CLI symlinked onto PATH"
[[ -f "$SCRATCH/.local/share/applications/omacar.desktop" ]] && ok "desktop entry written" || bad "desktop entry written"
grep -q '>>> omacar' "$SCRATCH/.config/omarchy/extensions/omarchy-menu.jsonc" && ok "menu block spliced" || bad "menu block spliced"
grep -q '>>> omacar' "$SCRATCH/.config/hypr/bindings.lua" && ok "keybinding block added" || bad "keybinding block added"
# The Omarchy menu is JSONC: line comments, and trailing commas before a
# closing brace are the existing convention there, not corruption.
"$PY" -c "
import json, re
raw = open('$SCRATCH/.config/omarchy/extensions/omarchy-menu.jsonc').read()
raw = re.sub(r'^\s*//.*$', '', raw, flags=re.M)
raw = re.sub(r',(\s*[}\]])', r'\1', raw)
d = json.loads(raw)
assert 'omacar.open' in d, 'our entry is missing'
" 2>/dev/null && ok "menu file is still valid JSONC and has our entry" || bad "menu file is still valid JSONC and has our entry"

# Idempotence: a second install must not duplicate anything.
HOME="$SCRATCH" "$ROOT/install.sh" --bind >/dev/null 2>&1
check "install is idempotent (one menu block)" "1" \
  "$(grep -c '>>> omacar' "$SCRATCH/.config/omarchy/extensions/omarchy-menu.jsonc")"
check "install is idempotent (one binding block)" "1" \
  "$(grep -c '>>> omacar' "$SCRATCH/.config/hypr/bindings.lua")"

HOME="$SCRATCH" "$ROOT/uninstall.sh" >/dev/null 2>&1 || bad "uninstall.sh exited non-zero"

[[ -e "$SCRATCH/.local/bin/omacar" ]] && bad "CLI removed" || ok "CLI removed"
grep -q '>>> omacar' "$SCRATCH/.config/omarchy/extensions/omarchy-menu.jsonc" && bad "menu block removed" || ok "menu block removed"
check "user's own config untouched" "$before_bindings" "$(cat "$SCRATCH/.config/hypr/bindings.lua")"
check "real state directory never touched" "$sentinel_before" "$(cat "$REAL_STATE/.smoke-sentinel" 2>/dev/null)"
rm -f "$REAL_STATE/.smoke-sentinel"

echo
if ((fails)); then echo "  $fails failed"; exit 1; else echo "  all good"; fi
echo
