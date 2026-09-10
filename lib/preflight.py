"""Is this machine ready to record a drive. One screen, one exit code.

WHY A COMMAND AND NOT A CHECKLIST.

doc/drive-day.md asks somebody to run six things and read the answers before
leaving. That is six chances to skip one, in a driveway, in the dark, with the
engine running -- and the one that gets skipped is always the one that mattered.
On 8 September a drive across three drive modes was recorded without its frame
bytes and answered nothing; on 9 September the drive recorder turned out never
to have been enabled on any machine, ever, while every status screen reported
the tablet completely ready.

Both were visible beforehand. Nobody looked, because looking meant knowing
which six things to look at.

WHAT IT WILL NOT DO.

It does not fix anything. Every line names the command that would, and stops
there: a preflight that silently repairs things is a preflight you stop reading,
and the whole value here is that somebody reads it.

It does not need the car. Every check is about this machine -- the recorder, the
port, the power, the disk. What the car says is `omacar doctor`, and the
difference is deliberate: this can be run indoors the night before.

READ THE EXIT CODE. 0 means go. 1 means something will cost you a leg, and the
line that says so is marked. Anything a script wraps this in should trust that
number rather than parsing the screen.
"""

import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records   # noqa: E402

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"

# A leg is ninety minutes. Raw frames from a busy bus run to tens of megabytes
# a minute, so four legs wants room measured in gigabytes, not megabytes.
DISK_FLOOR_GB = 4.0


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 127, "", "could not run it"


class Sheet:
    """Rows, and whether any of them is a reason not to leave."""

    def __init__(self):
        self.rows = []
        self.blockers = 0

    def row(self, what, ok, said, fix="", blocking=True):
        self.rows.append((what, ok, said, fix, blocking))
        if not ok and blocking:
            self.blockers += 1

    def draw(self):
        print()
        print(f"  {BOLD}Ready to record a drive{RESET}")
        print()
        for what, ok, said, fix, blocking in self.rows:
            if ok:
                mark = f"{GREEN}yes{RESET}"
            elif blocking:
                mark = f"{RED}NO {RESET}"
            else:
                # Worth knowing, not worth staying home for.
                mark = f"{YELLOW}hm {RESET}"
            print(f"    {mark}  {what:<22} {said}")
            if not ok and fix:
                print(f"         {DIM}{fix}{RESET}")
        print()
        if self.blockers:
            print(f"  {RED}{self.blockers} thing(s) will cost you a leg.{RESET}")
            print(f"  {DIM}Nothing here is fixed for you — the commands above "
                  f"are the fixes.{RESET}")
        else:
            print(f"  {GREEN}Nothing here will stop a recording.{RESET}")
            print(f"  {DIM}What the CAR says is a different question: "
                  f"omacar doctor{RESET}")
        print()
        return 1 if self.blockers else 0


def _recorder(sheet):
    """The one that was never on. It goes first because it always should have."""
    code, out, _ = _run(["systemctl", "--user", "is-enabled",
                         "omacar-drivelog.service"])
    enabled = out == "enabled"
    code, out, _ = _run(["systemctl", "--user", "is-active",
                         "omacar-drivelog.service"])
    active = out == "active"

    if enabled and active:
        said = "enabled and running"
    elif active:
        said = "running, but not enabled — it will not come back after a reboot"
    elif enabled:
        said = "enabled but NOT running"
    else:
        said = f"{out or 'not installed'} — nothing is recording anything"
    sheet.row("recording the bus", enabled and active, said,
              "systemctl --user enable --now omacar-drivelog.service")

    # A UNIT CAN BE ACTIVE AND THE PROGRAM STILL WEDGED, so ask the program.
    # ONLY WORTH ASKING IF SOMETHING IS SUPPOSED TO BE THERE. With the unit
    # inactive this would report the same problem a second time under a
    # different name, and a preflight that says one fault twice teaches you to
    # skim it.
    try:
        import drivelog
        doc = drivelog.status_doc() if active else None
    except Exception:                                         # noqa: BLE001
        doc = None
    if doc:
        age = time.time() - (doc.get("at") or 0)
        fresh = age < 120
        sheet.row("it is awake", fresh,
                  f"{doc.get('state', '?')} · {doc.get('detail') or ''}"
                  + ("" if fresh else f"  (silent for {age / 60:.0f} min)"),
                  "systemctl --user restart omacar-drivelog.service")
    elif active:
        sheet.row("it is awake", False, "no status file yet",
                  "give it fifteen seconds and run this again")

    stood_down = os.path.exists(os.path.join(records.STATE, "drivelog-off"))
    if stood_down:
        sheet.row("stood down", False, "somebody asked it not to record",
                  "omacar drive on")


def _power(sheet):
    """Both the buttons and the thing that ends a drive by itself."""
    try:
        import power
    except Exception:                                         # noqa: BLE001
        return
    for what, label in (("off", "can shut down"), ("sleep", "can sleep")):
        why = power.blocked(what)
        # NOT BLOCKING. A tablet that cannot suspend records perfectly well;
        # this is here because the buttons on the reference screen are the only
        # controls a driver has, and finding out they do nothing at the end of
        # a drive is worse than knowing now.
        sheet.row(label, why is None, why or "logind says yes",
                  "omacar power allow", blocking=False)

    # THE ONE THAT ACTUALLY ENDS DRIVES. A tablet that suspends mid-leg does
    # not report an error; it stops answering and comes home with nothing.
    code, out, _ = _run(["systemctl", "is-enabled", "suspend.target"])
    masked = out == "masked"
    sheet.row("cannot suspend itself", masked,
              "suspend.target is masked" if masked
              else "IT CAN SUSPEND — a tablet in a car should not",
              "omacar tablet awake", blocking=False)


def _port(sheet):
    """One adapter, and everything wants it."""
    try:
        import connect
        port, kind = connect.resolve()
    except Exception as why:                                  # noqa: BLE001
        sheet.row("adapter", False, f"could not look: {why}", "", blocking=False)
        return
    sheet.row("adapter", bool(port), f"{port} ({kind})" if port
              else "none found — plug it in, or this is an indoor check",
              "", blocking=False)
    if port:
        try:
            warn = connect.serial_group_warning(port)
        except Exception:                                     # noqa: BLE001
            warn = None
        if warn:
            sheet.row("permission", False, warn.splitlines()[0], "")


def _disk(sheet):
    """Four legs of raw frames is gigabytes, and the failure is silent."""
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
    except OSError:
        return
    free_gb = usage.free / (1024 ** 3)
    sheet.row("room to record", free_gb >= DISK_FLOOR_GB,
              f"{free_gb:.1f} GB free"
              + ("" if free_gb >= DISK_FLOOR_GB
                 else f" — four legs of frames wants {DISK_FLOOR_GB:.0f} GB"),
              "omacar listen list, then remove what you have already read")


def _captures(sheet):
    """The frames, which is the whole reason for the drive.

    A capture without them can be read once and never compared with another,
    which is exactly how the 8 September drive answered nothing. Saving keeps
    them now -- this checks that the file on THIS machine agrees.
    """
    try:
        import listen
        names = listen.captures()
    except Exception:                                         # noqa: BLE001
        return
    if not names:
        sheet.row("captures so far", True, "none yet", "", blocking=False)
        return
    doc = listen.load(names[-1]) or {}
    kept = bool(doc.get("raw"))
    sheet.row("captures so far", True,
              f"{len(names)}, newest {names[-1]}", "", blocking=False)
    sheet.row("the newest kept frames", kept,
              "yes" if kept else "NO — a census only, which cannot be compared",
              "captures save frames by default now; --no-raw is the opt-out",
              blocking=False)


def _code(sheet):
    """Is this machine running what was fixed."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code, head, _ = _run(["git", "-C", root, "rev-parse", "--short", "HEAD"])
    if code != 0:
        return
    _run(["git", "-C", root, "fetch", "--quiet", "origin"], timeout=30)
    code, behind, _ = _run(
        ["git", "-C", root, "rev-list", "--count", "HEAD..@{u}"])
    n = int(behind) if behind.isdigit() else 0
    code, dirty, _ = _run(["git", "-C", root, "status", "--porcelain"])
    sheet.row("running the fixes", n == 0,
              f"at {head}" + (f", {n} commit(s) behind" if n else "")
              + (", with local changes" if dirty else ""),
              "git pull && ./install.sh", blocking=n > 0)


def main(argv=None):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    sheet = Sheet()
    _code(sheet)
    _recorder(sheet)
    _port(sheet)
    _power(sheet)
    _disk(sheet)
    _captures(sheet)
    return sheet.draw()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
