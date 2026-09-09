"""The system settings a tablet in a car needs, and a laptop does not.

WHAT THIS IS FOR.

OmaCar runs on an ordinary Linux laptop, and almost everything it does should
leave that laptop alone. Three settings are the exception, because on a machine
bolted to a dashboard the defaults are actively dangerous:

  THE POWER BUTTON. On this machine it was bound to `systemctl
  suspend-then-hibernate`. On a dash mount the power button is the only control
  a driver can reach, so the one button available mid-drive was the one that
  killed the machine. It did exactly that on 8 September 2026, during a drive
  that was recording: the journal shows the session asking for
  suspend-then-hibernate at 15:57 and that boot never resuming. From the
  driver's seat it looked like a broken tablet — a screen that flashed at the
  button and would not come up, and no network for ten minutes.

  SUSPENDING AT ALL. There is no lid position on a dash mount that means "I am
  finished", and an idle timer cannot tell parked-at-work from waiting-at-a-
  light.

  THE SCREEN. Which is the one thing that SHOULD go dark when the car is
  parked, and the only one of the three the app can do for itself.

WHY THE BINDING IS EDITED RATHER THAN OVERRIDDEN. Hyprland has no "ignore this
binding" layer; the binding is in the user's own configuration file and the only
way to change it is to change that file. So this is careful instead: it backs
the file up first, it only touches a line that matches exactly what it expects,
it leaves a comment saying what it did and how to undo it, and it refuses
rather than guessing if the file has moved on.
"""

import os
import re
import shutil

BINDINGS = os.path.expanduser("~/.config/hypr/bindings.lua")
BACKUP = BINDINGS + ".omacar-backup"

# The exact binding that caused it. Matched rather than pattern-guessed: a
# rewrite that fires on anything mentioning the power key would also rewrite a
# binding somebody had already chosen deliberately.
SUSPEND = re.compile(
    r'^\s*o\.bind\(\s*"XF86PowerOff"\s*,\s*"[^"]*"\s*,\s*'
    r'"systemctl\s+(?:suspend|hibernate|suspend-then-hibernate)[^"]*"',
    re.M)

SAFE = re.compile(r'^\s*o\.bind\(\s*"XF86PowerOff"[^\n]*'
                  r'(?:omarchy-launch-screensaver|omacar-screen)', re.M)

NEW = '''-- Rewritten by `omacar tablet awake`. The original is in
-- bindings.lua.omacar-backup.
--
-- This was bound to suspend. On a tablet mounted in a car the power button is
-- the only control a driver can reach, and suspending mid-drive costs the
-- network, the recording, and — if the resume fails — the machine until
-- somebody holds the button for twenty seconds.
--
-- The screensaver rather than a raw screen blank, deliberately: a screensaver
-- exits on any input, so it cannot stand a driver behind a black screen with
-- no way back.
o.bind("XF86PowerOff", "Screen off", "omarchy-launch-screensaver", { locked = true })'''


def power_button():
    """What the power key does today: 'suspend', 'safe', 'other' or 'no file'."""
    try:
        with open(BINDINGS, encoding="utf-8") as f:
            s = f.read()
    except OSError:
        return "no file"
    if SUSPEND.search(s):
        return "suspend"
    if SAFE.search(s):
        return "safe"
    return "other"


def make_power_button_safe():
    """Rebind the power key away from suspend. Returns what it did."""
    state = power_button()
    if state != "suspend":
        return state
    with open(BINDINGS, encoding="utf-8") as f:
        s = f.read()
    if not os.path.exists(BACKUP):
        shutil.copy2(BINDINGS, BACKUP)
    line = SUSPEND.search(s).group(0)
    # Comment the original out rather than deleting it, so the file still shows
    # what was there before somebody came along and changed it.
    s = s.replace(line, NEW + "\n-- was: " + line.strip(), 1)
    tmp = BINDINGS + ".omacar-new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(s)
    os.replace(tmp, BINDINGS)
    return "rewritten"


def restore_power_button():
    if not os.path.exists(BACKUP):
        return False
    shutil.copy2(BACKUP, BINDINGS)
    return True


def main(argv):
    what = argv[0] if argv else "status"
    if what == "safe":
        print(make_power_button_safe())
        return 0
    if what == "restore":
        print("restored" if restore_power_button() else "no backup to restore")
        return 0
    print(power_button())
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
