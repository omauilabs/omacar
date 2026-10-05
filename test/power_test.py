#!/usr/bin/env python3
"""The screen goes dark on the Hyprland the tablet has, and on the one it had.

WHY THIS EXISTS. `omacar power screen` asked Hyprland for `dispatch dpms off`.
The tablet's Hyprland is 0.56 on a Lua config, and there `hyprctl dispatch` no
longer takes a dispatcher's name and its arguments: it takes a Lua expression
and evaluates `return hl.dispatch(<it>)`. So the old words come back as

    error: [string "return hl.dispatch(dpms off)"]:1: ')' expected near 'off'

and the screen stays on. The form that works there is
`hl.dsp.dpms({ action = "disable" })`: run on the tablet on 30 September, after
which `hyprctl monitors -j` showed dpmsStatus false. A Hyprland on a hyprlang
config answers that form with "Invalid dispatcher" and still wants `dpms off`.
So screen_off() asks the new way first and the old way second.

HOW A REFUSAL LOOKS, read from Hyprland's own source at v0.55.4 and v0.56.2
(hyprctl/src/main.cpp, src/debug/HyprCtl.cpp, src/config/lua/ConfigManager.cpp):

  a Lua config answers "ok", or "error: " and the Lua error
  a hyprlang config answers "ok", or "Invalid dispatcher" for a name it lacks
  hyprctl 0.56 exits 7 when the answer starts "error:"; 0.55 exits 0 whatever
    the answer, so the exit code alone would take a refusal for a success
  either way the answer is printed on stdout, never on stderr
  with no compositor to reach, hyprctl says so and exits nonzero

EVERY hyprctl HERE IS A STAND-IN, written into a scratch directory that is the
whole of PATH, so the real one cannot be reached even on a machine where
Hyprland is running: nothing in this file can turn a real screen off. Each
stand-in writes down how it was called and answers as one of those Hyprlands
would.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import power  # noqa: E402

# Resolved before any PATH is taken away.
PY = sys.executable

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


# How each stand-in is asked, as "$*" sees it.
LUA = 'dispatch hl.dsp.dpms({ action = "disable" })'
LEGACY = "dispatch dpms off"

# A Lua config handed the old words: the error the tablet printed, and the note
# HyprCtl.cpp adds when what it was given has no "(" in it.
LEGACY_ON_LUA = (
    "error: [string \"return hl.dispatch(dpms off)\"]:1: ')' expected near "
    "'off'\n\n → Note: dispatch in lua is a shorthand for hl.dispatch(...), "
    "your syntax might need to be updated.")
NOBODY = "HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)\n"

HYPRLANDS = {
    "tablet": {LUA: ("ok", 0), LEGACY: (LEGACY_ON_LUA, 7)},
    "hyprlang": {LUA: ("Invalid dispatcher", 0), LEGACY: ("ok", 0)},
    # Made up, to have a refusal that exits 0: hyprctl 0.55's client against
    # a Lua config whose Lua has no hl.dsp.dpms.
    "refuses": {
        LUA: ("error: [string \"return hl.dispatch(hl.dsp.dpms({ action = "
              "\"...\"]:1: attempt to call a nil value (field 'dpms')", 0),
        LEGACY: (LEGACY_ON_LUA, 0),
    },
    "nobody": {LUA: (NOBODY, 1), LEGACY: (NOBODY, 1)},
}


def stand_in(where, answers):
    """A hyprctl that writes down each call and answers from `answers`."""
    bindir = os.path.join(where, "bin")
    os.makedirs(bindir)
    calls = os.path.join(where, "calls")
    lines = ["#!/bin/sh",
             "# A stand-in hyprctl. It talks to no compositor.",
             f"printf '%s\\n' \"$*\" >> {shlex.quote(calls)}",
             'case "$*" in']
    for asked, (reply, code) in answers.items():
        lines.append(f"  {shlex.quote(asked)}) printf '%s\\n' "
                     f"{shlex.quote(reply)}; exit {code} ;;")
    lines += ["esac",
              "printf 'a stand-in hyprctl was asked something else: %s\\n' \"$*\"",
              "exit 99", ""]
    path = os.path.join(bindir, "hyprctl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(path, 0o755)
    return bindir, calls


def asked(calls):
    if not os.path.exists(calls):
        return []
    with open(calls, encoding="utf-8") as f:
        return f.read().splitlines()


def screen_off_under(bindir):
    """screen_off() with PATH holding nothing but `bindir`."""
    saved = dict(os.environ)
    os.environ["PATH"] = bindir
    os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    try:
        found = shutil.which("hyprctl")
        if found and os.path.dirname(found) != bindir:
            raise SystemExit(f"a real hyprctl is reachable at {found}; stopping")
        return power.screen_off()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-power-test-")
    try:
        runs = {}
        for name, answers in HYPRLANDS.items():
            bindir, calls = stand_in(os.path.join(scratch, name), answers)
            done, said = screen_off_under(bindir)
            runs[name] = (done, said, asked(calls), bindir)

        print("\n  The tablet's Hyprland: 0.56 on a Lua config\n")
        done, said, calls, _ = runs["tablet"]
        check("the screen goes off", done is True)
        check("and it says how to bring it back", "screen off" in said)
        check("it was asked the Lua way, once, and nothing else",
              calls == [LUA])

        print("\n  A Hyprland on a hyprlang config: the old words, second\n")
        done, said, calls, _ = runs["hyprlang"]
        check("the screen goes off", done is True)
        check("'Invalid dispatcher', exiting 0, was not taken for success",
              calls == [LUA, LEGACY])

        print("\n  A refusal that exits 0 is still a refusal\n")
        done, said, calls, _ = runs["refuses"]
        check("it does not say the screen went off",
              done is False and "screen off" not in said)
        check("both ways were tried, the new one first", calls == [LUA, LEGACY])
        check("the sentence has Hyprland's own words for both refusals",
              "attempt to call a nil value" in said
              and "')' expected near 'off'" in said)

        print("\n  No compositor to reach\n")
        done, said, calls, _ = runs["nobody"]
        check("it does not say the screen went off", done is False)
        check("it passes on hyprctl's reason, which is on stdout",
              "HYPRLAND_INSTANCE_SIGNATURE not set" in said)

        print("\n  No hyprctl at all\n")
        empty = os.path.join(scratch, "empty")
        os.makedirs(empty)
        done, said = screen_off_under(empty)
        check("it says there is nothing to ask",
              done is False and "hyprctl is not here" in said)

        print("\n  `omacar power screen` exits with the outcome\n")
        home = os.path.join(scratch, "home")
        os.makedirs(home)
        for name, want, says in (("tablet", 0, "screen off"),
                                 ("refuses", 1, "attempt to call a nil value")):
            r = subprocess.run(
                [PY, os.path.join(ROOT, "lib", "power.py"), "screen"],
                env={"PATH": runs[name][3], "HOME": home},
                capture_output=True, text=True, timeout=30)
            check(f"{name}: exits {want} and says why (got {r.returncode}:"
                  f" {r.stdout.strip()[:60]!r})",
                  r.returncode == want and says in r.stdout)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
