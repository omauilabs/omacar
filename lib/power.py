"""Ending the machine's day, and saying so out loud when it cannot.

WHY THIS IS A MODULE AND NOT TWO WORDS IN A BUTTON.

The board's Sleep and Shut down buttons ran `systemctl suspend` and
`systemctl poweroff` directly and looked at neither the exit code nor the
error. So when one of them did not work there was nothing to see: no message,
no log, no difference between "the tap missed" and "logind refused". The
report that came back was "shutdown and sleep buttons dont work", which is all
the information the design allowed anyone to have.

There are four separate reasons either can fail on this tablet, and three of
them are invisible:

  * polkit may want an administrator, which happens whenever a second session
    is logged in -- an ssh session from the desktop is a second session. The
    command then fails with "Interactive authentication required" on a screen
    nobody is reading.
  * `omacar tablet awake` MASKS sleep.target and suspend.target on purpose,
    because a tablet that suspends in a car comes home with no recording. With
    those masked, `systemctl suspend` cannot work and never will. Two features
    of this tool in direct conflict, and neither one said so.
  * an inhibitor lock can hold shutdown or sleep off.
  * `systemctl` may simply not be on the caller's PATH.

So: ask first, act second, and always return a sentence. `omacar power status`
answers all of it without touching anything, which means the preflight before a
drive is a command somebody can run rather than a thing I assert.
"""

import os
import re
import shutil
import subprocess
import sys
import time

# What logind calls it, what systemctl calls it, and the unit that has to not
# be masked for it to be possible at all.
WHAT = {
    "off":   ("CanPowerOff", "poweroff", "poweroff.target", "shut down"),
    "sleep": ("CanSuspend", "suspend", "suspend.target", "sleep"),
}


LOG = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                   "power.log")


def note(line):
    """A line on disk, because the screen may be about to go away.

    The one thing that cannot report its own outcome is a successful shutdown:
    by the time there is anything to say, there is nothing to say it to. So
    every attempt writes here first, and the next time the machine comes up the
    reason the last one did or did not work is sitting in a file.
    """
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as why:
        return 127, "", str(why)


def verdict(what):
    """What logind says it would do if asked. Nothing happens here.

    "yes" it would just do it, "challenge" it would ask for a password that no
    dashboard can answer, "no" it refuses, "na" the machine cannot.
    """
    method = WHAT[what][0]
    if not shutil.which("busctl"):
        return "unknown"
    code, out, _err = _run(["busctl", "--system", "call",
                            "org.freedesktop.login1", "/org/freedesktop/login1",
                            "org.freedesktop.login1.Manager", method])
    if code != 0:
        return "unknown"
    m = re.search(r'"([a-z]+)"', out)
    return m.group(1) if m else "unknown"


def masked(unit):
    code, out, _err = _run(["systemctl", "is-enabled", unit])
    return out == "masked"


def inhibitors(kind):
    """Who is holding this off, if anybody. Diagnostic only."""
    if not shutil.which("systemd-inhibit"):
        return []
    _code, out, _err = _run(["systemd-inhibit", "--list", "--no-legend",
                             "--no-pager"])
    held = []
    for line in out.splitlines():
        if kind in line and "block" in line:
            held.append(" ".join(line.split()[:3]))
    return held


def blocked(what):
    """The reason this cannot work, as a sentence, or None if it can."""
    _method, _verb, unit, name = WHAT[what]
    if not shutil.which("systemctl"):
        return "systemctl is not on this PATH, so nothing here can " + name
    if masked(unit):
        return (f"{unit} is masked, so this machine cannot {name} at all. "
                "`omacar tablet awake` does that on purpose, so a tablet in a "
                "car cannot sleep through a drive. Undo it with "
                "`omacar tablet sleep`.")
    v = verdict(what)
    if v == "challenge":
        return (f"logind would ask for an administrator password to {name}, "
                "which a dashboard cannot answer. This usually means a second "
                "session is logged in — an ssh session counts. Fix it once "
                "with `omacar power allow`.")
    if v == "no":
        return f"logind refuses to {name} on this machine."
    if v == "na":
        return f"this machine has no {name}."
    held = inhibitors("shutdown" if what == "off" else "sleep")
    if held:
        return f"held off by an inhibitor: {'; '.join(held)}"
    return None


def do(what):
    """Try it. Returns (ok, sentence) — and the sentence is never empty."""
    why = blocked(what)
    if why:
        note(f"{what}: refused before trying — {why}")
        return False, why
    verb = WHAT[what][1]
    note(f"{what}: logind says {verdict(what)}, running systemctl {verb}")
    code, out, err = _run(["systemctl", verb], timeout=30)
    if code == 0:
        note(f"{what}: systemctl {verb} accepted")
        return True, f"{verb} accepted"
    said = (err or out or f"systemctl {verb} exited {code}").splitlines()[0]
    note(f"{what}: FAILED — {said}")
    return False, said


RULE = "/etc/polkit-1/rules.d/49-omacar-power.rules"


def rule_text(user=None):
    """The polkit rule that lets the person at the screen end the day.

    WHY THIS IS NEEDED AND WHY IT IS NARROW. polkit lets an ordinary user power
    off their own machine right up until a second session exists — and an ssh
    session from another machine is a second session. The action becomes
    `power-off-multiple-sessions`, which asks for an administrator password.
    On a tablet bolted to a dashboard there is no keyboard and nobody to type
    it, so the button fails with a message nothing was displaying.

    The rule is limited to one named user and, deliberately, to `subject.local
    && subject.active`: whoever is physically at the screen. It grants nothing
    to the ssh session that caused the problem.
    """
    user = user or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return f"""// Written by `omacar power allow`. Remove with `omacar power deny`.
//
// A tablet on a dashboard has no keyboard, so a polkit prompt is a dead end.
// This lets the person actually sitting at the screen suspend or shut down
// even when another session (an ssh session, say) is logged in. It gives
// nothing to that other session: `local` and `active` mean the seat.
polkit.addRule(function(action, subject) {{
    var power = [
        "org.freedesktop.login1.power-off",
        "org.freedesktop.login1.power-off-multiple-sessions",
        "org.freedesktop.login1.suspend",
        "org.freedesktop.login1.suspend-multiple-sessions"
    ];
    if (power.indexOf(action.id) >= 0
        && subject.user == "{user}" && subject.local && subject.active) {{
        return polkit.Result.YES;
    }}
}});
"""


def allow():
    """Install it. One sudo, once."""
    import tempfile
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not user:
        print("  cannot tell who you are, so cannot write a rule for you",
              file=sys.stderr)
        return 1
    print(f"\n  Letting {user} suspend and shut down at the screen without a")
    print("  password prompt, even when another session is logged in.\n")
    print(f"  {RULE}\n")
    fd, tmp = tempfile.mkstemp(prefix="omacar-polkit-")
    with os.fdopen(fd, "w") as f:
        f.write(rule_text(user))
    try:
        code, _out, err = _run(["sudo", "install", "-m", "644", tmp, RULE],
                               timeout=120)
    finally:
        os.unlink(tmp)
    if code != 0:
        print(f"  could not install it: {err}", file=sys.stderr)
        return 1
    print("  done.\n")
    return status()


def deny():
    if not os.path.exists(RULE):
        print("  not installed")
        return 0
    code, _out, err = _run(["sudo", "rm", "-f", RULE], timeout=120)
    if code != 0:
        print(f"  could not remove it: {err}", file=sys.stderr)
        return 1
    print("  removed")
    return 0


def screen_off():
    """The other thing a parked car wants: the screen dark, the machine up.

    Not a lesser version of sleep. A tablet that is recording a drive must not
    suspend, and this is what "off" means for it — the difference between a
    dark screen and a dead one is the whole capture.
    """
    if not shutil.which("hyprctl"):
        return False, "hyprctl is not here, so nothing can turn the screen off"
    code, _out, err = _run(["hyprctl", "dispatch", "dpms", "off"])
    if code == 0:
        return True, "screen off — touch it or press a key to bring it back"
    return False, err or "hyprctl refused"


def status():
    print()
    for what in ("off", "sleep"):
        _m, _v, unit, name = WHAT[what]
        why = blocked(what)
        mark = "no" if why else "yes"
        print(f"  {name:9} {mark:4} logind says {verdict(what)}"
              f"{', ' + unit + ' is masked' if masked(unit) else ''}")
        if why:
            print(f"            {why}")
    can = "yes" if shutil.which("hyprctl") else "no"
    print(f"  {'screen':9} {can:4} omacar power screen")
    print(f"  {'rule':9} {'yes' if os.path.exists(RULE) else 'no':4} {RULE}")
    print()
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        return status()
    if what == "allow":
        return allow()
    if what == "deny":
        return deny()
    if what == "screen":
        ok, msg = screen_off()
        print(f"  {msg}")
        return 0 if ok else 1
    if what not in WHAT:
        print(f"  omacar power: no such thing as '{what}'.\n"
              "  off | sleep | screen | status | allow | deny", file=sys.stderr)
        return 2
    ok, msg = do(what)
    print(f"  {msg}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
