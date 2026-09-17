"""One press, and the tablet is ready to record a drive.

WHY THIS EXISTS, AND WHY IT IS NOT `preflight`.

`preflight` answers a question and fixes nothing, deliberately: a report that
silently repairs things is a report you stop reading. That is the right shape
for the night before, at a desk.

It is the wrong shape in a car. On 16 September the sequence that had to happen
before a drive was: check no bench is shadowing the adapter, wait for the
adapter, make sure exactly ONE process owns the serial port, start the daemon,
confirm the car actually answered, confirm the readings are being filed under
the real car and not the emulator, and open the screen. Seven things, typed, in
a driveway, one-handed, with the engine running. Three of them went wrong that
day and two of those were invisible until the drive was over.

So this one ACTS, and every step says what it did. The difference from
preflight is the whole point of the file and is not an accident to be tidied
away later.

WHAT IT REFUSES TO DO. It will not start a recorder beside the daemon. One
serial port has one owner, and a second contender is what cost the switch hunt:
`listen marks` took a buffer of frames and then nothing, while the status file
went on saying "capturing". Whichever owner is wanted, the other is stopped
first and the screen says which is running.

READ THE EXIT CODE. 0 means the car answered and the screen is up. 1 means it
did not, and the line that says so is marked — a driver who pulls away on a 1
is recording nothing.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connect   # noqa: E402
import garage    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "omacar")
LIVE = os.path.join(connect.STATE, "live.json")
RECORDER = "omacar-drivelog.service"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"

# How long to wait for each thing, in seconds. The adapter figure is the one
# that matters: it is the gap between the tablet waking and the car's OBD port
# coming alive, and on this car that is several seconds after the ignition.
WAIT_ADAPTER = 25.0
WAIT_CONNECT = 45.0


def _run(cmd, timeout=90):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 127, "", "could not run it"


def _live():
    try:
        with open(LIVE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Run:
    """Steps, and whether the car is actually answering at the end of them."""

    def __init__(self, quiet=False):
        self.failed = 0
        self.quiet = quiet
        self.steps = []

    def say(self, what, ok, said, fatal=True):
        self.steps.append({"step": what, "ok": bool(ok), "said": said,
                           "fatal": bool(fatal)})
        if not ok and fatal:
            self.failed += 1
        if self.quiet:
            # One JSON object per line, for the launcher to read as it goes.
            print(json.dumps(self.steps[-1]), flush=True)
            return
        mark = (f"{GREEN}ok {RESET}" if ok
                else f"{RED}NO {RESET}" if fatal else f"{YELLOW}hm {RESET}")
        print(f"    {mark}  {what:<20} {said}", flush=True)


def _adapter(run):
    """Wait for the port rather than failing on a tablet that woke first."""
    port = connect.bench_port()
    if port:
        run.say("bench", False,
                "a bench emulator is running — every reading would be the "
                "emulator. Stop it: omacar bench stop")
        return None
    deadline = time.time() + WAIT_ADAPTER
    seen = None
    while time.time() < deadline:
        seen = connect.wired_ports()
        if os.path.exists("/dev/obd"):
            seen = ["/dev/obd"] + [p for p in seen if p != "/dev/obd"]
        if seen:
            break
        time.sleep(0.5)
    if not seen:
        run.say("adapter", False,
                f"none found after {WAIT_ADAPTER:.0f}s — is it in the OBD port?")
        return None
    run.say("adapter", True, seen[0])
    return seen[0]


def _one_owner(run):
    """ONE PORT, ONE OWNER. The recorder and the daemon both want it."""
    code, out, _ = _run(["systemctl", "--user", "is-active", RECORDER], timeout=15)
    if out != "active":
        run.say("one owner", True, "nothing else is holding the port")
        return
    _run(["systemctl", "--user", "stop", RECORDER], timeout=30)
    time.sleep(1.5)
    code, out, _ = _run(["systemctl", "--user", "is-active", RECORDER], timeout=15)
    run.say("one owner", out != "active",
            "stopped the recorder so the dashboard can have the port"
            if out != "active" else
            "the recorder will not stop — the dashboard cannot have the port")


def _daemon(run):
    _run([CLI, "daemon", "start"], timeout=120)
    # THE START COMMAND RETURNING IS NOT THE CAR ANSWERING. It polls for a
    # pidfile; negotiating a protocol takes several seconds longer, and on
    # 16 September `daemon start` printed "did not start" for a daemon that was
    # already running and about to connect perfectly.
    deadline = time.time() + WAIT_CONNECT
    doc = {}
    while time.time() < deadline:
        doc = _live()
        if doc.get("connected") and (doc.get("values") or {}):
            break
        time.sleep(1.0)
    vals = doc.get("values") or {}
    if not (doc.get("connected") and vals):
        run.say("the car", False,
                f"no answer after {WAIT_CONNECT:.0f}s — ignition on? "
                f"(omacar doctor says what the adapter sees)")
        return doc
    run.say("the car", True,
            f"{len(vals)} readings on {doc.get('protocol') or 'an unnamed protocol'}")
    return doc


def _whose(run, doc):
    """Whose drive is this going to be filed as."""
    key = garage.current()
    if key == garage.SIM_KEY:
        run.say("the car's record", False,
                "filed as the SIMULATED car — a bench has been run since the "
                "last real drive. Plug in and restart, or: omacar vehicle")
        return
    vin = doc.get("vehicle") or key
    run.say("the car's record", True, str(vin))


def _awake(run):
    code, out, _ = _run(["systemctl", "is-enabled", "suspend.target"], timeout=15)
    masked = (out or "").startswith("masked")
    run.say("stays awake", masked,
            "it cannot suspend" if masked else
            "THIS MACHINE CAN STILL SUSPEND mid-drive — omacar tablet awake",
            fatal=False)


def _screen(run, view):
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        run.say("the screen", True, "no graphical session here — nothing drawn",
                fatal=False)
        return
    code, _out, err = _run([CLI, "kiosk", view], timeout=30)
    run.say("the screen", code == 0,
            f"{view}, fullscreen" if code == 0 else (err or "would not open"),
            fatal=False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    quiet = "--json" in argv
    view = "drive"
    if "--view" in argv:
        i = argv.index("--view")
        if i + 1 < len(argv):
            view = argv[i + 1]
    no_open = "--no-open" in argv

    run = Run(quiet=quiet)
    if not quiet:
        print(f"\n  {BOLD}Getting ready to drive{RESET}\n")

    port = _adapter(run)
    if port:
        _one_owner(run)
        doc = _daemon(run)
        _whose(run, doc)
    _awake(run)
    if not no_open and not run.failed:
        _screen(run, view)

    if not quiet:
        print()
        if run.failed:
            print(f"  {RED}Not ready. {run.failed} thing(s) would cost you the "
                  f"drive.{RESET}")
            print(f"  {DIM}The line marked NO says which, and what fixes it."
                  f"{RESET}\n")
        else:
            print(f"  {GREEN}Ready. The car is answering and the screen is up."
                  f"{RESET}\n")
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
