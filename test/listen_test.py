#!/usr/bin/env python3
"""The detached drive capture, against an adapter that is not there.

WHY THIS EXISTS. The one capability that has to work unattended for hours is
the one nothing could test: the bench emulator answers diagnostic requests and
has no notion of monitor mode, so the entire listening path -- the part that
runs for a whole trip with nobody watching -- had never been executed by
anything but a car.

Three faults were found by running it by hand, and every one of them was a lie
told to somebody who had already driven away:

  it said "listening in the background" when the detached child had died
  it reported "nothing is listening" while a capture was running perfectly,
    because progress was only republished when a frame arrived
  it wrote a capture of zero frames from `unknown-car` for a session that
    never opened a port, which then sat in the list looking like evidence

So this stands a fake adapter in front of it: one that emits frames, or goes
quiet, or hears nothing at all, on demand. None of it needs a car and none of
it needs the emulator.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")

# A scratch state directory BEFORE anything imports records, because the module
# resolves its paths once at import and a real car's captures live there.
_TMP = tempfile.mkdtemp(prefix="omacar-listen-test-")
os.environ["XDG_STATE_HOME"] = os.path.join(_TMP, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)

sys.path.insert(0, LIB)

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def head(msg):
    print(f"\n  {msg}\n")


# --------------------------------------------------------------- the fake ELM
#
# It answers AT commands with "OK" and streams whatever frames the test asked
# for. `heard` decides which protocol settings hear anything, which is how the
# quiet-bus refusal is exercised without a quiet car.

class FakeElm:
    made = []

    def __init__(self, port, baudrate=None):
        self.port = port
        self.protocol = "7"
        self.closed = False
        self.setting = None
        FakeElm.made.append(self)

    def init(self):
        return True

    def raw(self, cmd):
        if str(cmd).upper().startswith("ATSP"):
            self.setting = str(cmd)[4:]
        return "OK"

    def close(self):
        self.closed = True

    def monitor(self, command="ATMA", seconds=8.0, on_line=None, limit=200000,
                should_stop=None):
        plan = FakeElm.plan
        n = 0
        deadline = time.time() + min(seconds, plan.get("cap_seconds", 30))
        # A probe run, told apart by its short duration. The threshold sits
        # below any real capture length this test asks for -- an earlier
        # version used 3.0 and a three-second capture took this branch, so the
        # test recorded the probe's frames and blamed the code.
        if seconds <= 2.5:
            for ln in plan["probe"].get(self.setting, []):
                if on_line:
                    on_line(ln)
                n += 1
            return n
        while time.time() < deadline:
            if should_stop and should_stop():
                break
            line = plan["frames"].pop(0) if plan["frames"] else None
            if line is None:
                time.sleep(0.05)          # a quiet bus, which is the point
                continue
            if on_line:
                on_line(line)
            n += 1
            time.sleep(plan.get("gap", 0.005))
        return n


FakeElm.plan = {"probe": {}, "frames": []}


def install_fakes():
    """Stand the fake in for the adapter, the port lease and the protocol table."""
    elm_mod = types.ModuleType("elm")
    elm_mod.Elm = FakeElm
    sys.modules["elm"] = elm_mod

    import connect
    connect.resolve = lambda: ("/dev/fake", "fake")
    connect.request_port = lambda port: True
    connect.release_port = lambda: True
    connect.detect_baud = lambda port: 38400

    import protocols
    protocols.describe = lambda p: {"header_digits": 3}


install_fakes()

import listen                                                  # noqa: E402
import records                                                 # noqa: E402


def reset():
    for path in (listen.RUNNING, listen.STOPFILE, listen.FAILFILE):
        try:
            os.remove(path)
        except OSError:
            pass
    caps = os.path.join(records.STATE, "captures")
    shutil.rmtree(caps, ignore_errors=True)
    FakeElm.made.clear()


def run_child(minutes=0.2, quiet=0.0, note="drive"):
    """The detached half, in this process, the way the child runs it.

    Its output is swallowed on purpose. The child writes to a log file and a
    failing one prints its traceback there deliberately, which is right for the
    thing being tested and unreadable in a test run.
    """
    import contextlib
    import io
    os.environ["OMACAR_LISTEN_CHILD"] = "1"
    args = types.SimpleNamespace(minutes=minutes, can_id=None, note=note,
                                 quiet_timeout=quiet)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return listen._drive_session(args)
    finally:
        os.environ.pop("OMACAR_LISTEN_CHILD", None)


def captures_written():
    d = os.path.join(records.STATE, "captures")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ------------------------------------------------------- an adapter that isn't
head("An adapter that is not plugged in")

reset()
import connect                                                 # noqa: E402
_resolve = connect.resolve
connect.resolve = lambda: (None, None)
rc = run_child(minutes=0.2)
connect.resolve = _resolve

check("the session reports failure rather than success", rc != 0)
check("it writes no capture at all", captures_written() == [])
check("nothing claims to be listening", listen.running() is None)
_f = listen.last_failure()
check("and the reason is kept for whoever asks later", bool(_f))
check("in words rather than as a traceback",
      bool(_f) and "adapter" in (_f.get("why") or "") and "Traceback" not in (_f.get("why") or ""))

# --------------------------------------------------------- a bus nobody hears
head("A bus that answers on no setting at all")

reset()
FakeElm.plan = {"probe": {}, "frames": [], "cap_seconds": 2}
rc = run_child(minutes=0.2, quiet=120)
check("the leg refuses rather than recording a quiet car", rc == 2)
check("and writes no capture", captures_written() == [])
_f = listen.last_failure()
check("the reason names the car being off, not a fault",
      bool(_f) and "off" in (_f.get("why") or ""))
# The probe leaves the adapter wherever it stopped; the daemon gets it back.
check("the adapter is put back on the protocol the daemon negotiated",
      bool(FakeElm.made) and FakeElm.made[-1].setting == "7")
check("and the port is closed", bool(FakeElm.made) and FakeElm.made[-1].closed)

# ------------------------------------------------------------ a bus that talks
head("A bus that talks")

reset()
FakeElm.plan = {
    # Only the 11-bit setting hears anything, which is the real car's shape.
    "probe": {"6": ["17C 01 02 03 04 05 06 07 08"] * 12},
    "frames": [f"17C 0{i % 8} 02 03 04 05 06 07 08" for i in range(200)],
    "gap": 0.001, "cap_seconds": 3,
}
rc = run_child(minutes=0.2, quiet=0)
check("the session succeeds", rc == 0)
names = captures_written()
check("exactly one capture is written", len(names) == 1)
if names:
    doc = json.load(open(os.path.join(records.STATE, "captures", names[0])))
    check(f"it recorded the frames it heard ({doc.get('frames')})",
          (doc.get("frames") or 0) > 50)
    check("it records the setting that actually heard them, not the diagnostic one",
          doc.get("monitor_protocol") == "6" or doc.get("protocol") == "6")
check("the adapter is handed back on the daemon's protocol",
      bool(FakeElm.made) and FakeElm.made[-1].setting == "7")

# ------------------------------------------------ a capture on a silent bus
head("A capture running perfectly on a bus that has gone quiet")

reset()
FakeElm.plan = {"probe": {"6": ["17C 01"] * 12}, "frames": [], "cap_seconds": 4}
seen = {"fresh": 0, "stale": 0}


def watch_running(stop):
    # `running()` calls a capture dead once its file is older than three flush
    # intervals. THIS IS THE TEST THAT MATTERS: a working capture on a quiet
    # bus must never look dead, because a quiet bus is a finding and it used to
    # be reported as "nothing is listening".
    while not stop.wait(0.25):
        if listen.running():
            seen["fresh"] += 1
        elif os.path.exists(listen.RUNNING):
            seen["stale"] += 1


_stop = threading.Event()
_w = threading.Thread(target=watch_running, args=(_stop,), daemon=True)
_orig = listen.FLUSH_EVERY
listen.FLUSH_EVERY = 0.4          # so three intervals is a second, not 90
_w.start()
rc = run_child(minutes=0.06, quiet=0)
_stop.set()
_w.join(timeout=2)
listen.FLUSH_EVERY = _orig

check("the capture completes", rc == 0)
check(f"it never once looked dead while it was running "
      f"({seen['fresh']} fresh, {seen['stale']} stale)",
      seen["fresh"] > 0 and seen["stale"] == 0)
check("and a capture is written even though nothing was heard",
      len(captures_written()) == 1)

# ------------------------------------------------------- the engine stopping
head("The engine stopping ends the leg")

reset()
listen.FLUSH_EVERY = 0.4
FakeElm.plan = {
    "probe": {"6": ["17C 01"] * 12},
    "frames": [f"17C 0{i % 8} 02" for i in range(20)],
    "gap": 0.005, "cap_seconds": 20,
}
began = time.time()
# Forty minutes asked for; the bus goes quiet after twenty frames.
rc = run_child(minutes=40, quiet=1.5)
took = time.time() - began
listen.FLUSH_EVERY = _orig

check(f"the leg ended on silence rather than running for 40 minutes "
      f"({took:.1f}s)", took < 20)
check("it ended cleanly rather than failing", rc == 0)
check("and kept what it heard", len(captures_written()) == 1)

shutil.rmtree(_TMP, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the drive capture tells the truth about itself\n")
