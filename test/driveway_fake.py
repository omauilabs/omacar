#!/usr/bin/env python3
"""The stand-in car for test/driveway_e2e_test.py.

tools/driveway-check.sh only ever talks to three things: `omacar drive
off|on|status`, `omacar listen capture`, and `systemctl --user daemon-reload|
restart omacar-drivelog`. The e2e test points OMACAR_BIN and OMACAR_SYSTEMCTL
at two tiny shell stubs, and those stubs call this file. Nothing here opens a
port; nothing here could.

WHY THE FAKE PUBLISHES HAND-OVER SNAPSHOTS. The first end-to-end test always
wrote `connected: true` into live.json, which is exactly why the reconnect
check passing on any fresh `t` went unnoticed: the real daemon (lib/daemon.py)
publishes `connected: false, status: "yielded"` with a fresh `t` every 0.3s
for as long as it has lent the adapter out. So while a capture runs -- and
after it, until the daemon has taken the port back -- this fake does the same,
from a small background writer, and only then flips to a connected sample.

Everything is configured from the environment, set by the test:

  FAKE_OSTATE        the script's $OMACAR_STATE (live.json, drivelog.json, ...)
  FAKE_CALLS         file the omacar stub appends its arguments to
  FAKE_A / FAKE_B    what capture A / B does: pass | fail | thin | short |
                     missing | exit1 | hang | badraw
  FAKE_BACK          seconds after a capture returns until the daemon is
                     connected again ("never" = never: the S5 scenario)
  FAKE_CAPTURE_SECS  how long a capture takes before it saves (default 0)
  FAKE_HANG_BACK     for a hung capture: seconds after it STARTED until the
                     daemon takes the port back (lease expiry)
  FAKE_OFF_BACK      `drive off`: seconds until a mid-leg daemon is connected
                     again ("never" = never); only used with FAKE_START_YIELDED
  FAKE_RESTART_APPLY restart handler: ok (default) | stale (does not update)
"""

import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))

OSTATE = os.environ.get("FAKE_OSTATE", "")


def _write_json(path, doc):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except OSError:
        pass


def live_path():
    return os.path.join(OSTATE, "live.json")


def publish_yielded():
    _write_json(live_path(), {
        "connected": False, "status": "yielded", "handover": True,
        "t": time.time(), "note": "a command is using the adapter",
        "values": {"RPM": 850}})


def publish_connected():
    _write_json(live_path(), {
        "connected": True, "t": time.time(), "values": {"RPM": 850}})


# ------------------------------------------------------------- the finisher
#
# A detached writer standing in for the daemon: yielded snapshots (fresh `t`
# each time) until `back` seconds have passed, then connected ones.

FINISHER_PID = "finisher.pid"


def kill_finisher():
    path = os.path.join(OSTATE, FINISHER_PID)
    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass


def spawn_finisher(back, lifetime=25.0):
    kill_finisher()
    p = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "finisher", str(back), str(lifetime)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=os.environ.copy())
    try:
        with open(os.path.join(OSTATE, FINISHER_PID), "w", encoding="utf-8") as f:
            f.write(str(p.pid))
    except OSError:
        pass


def cmd_finisher(argv):
    back = None if argv[0] == "never" else float(argv[0])
    lifetime = float(argv[1])
    start = time.time()
    while time.time() - start < lifetime:
        if back is not None and time.time() - start >= back:
            publish_connected()
        else:
            publish_yielded()
        if not os.path.isdir(OSTATE):
            return 0
        time.sleep(0.3)
    return 0


# ------------------------------------------------------------ the captures

def _frames(n, span, ident, data):
    return [{"t": round(i * span / n, 4), "id": ident, "data": data} for i in range(n)]


def _capture_doc(note, scenario):
    raw = []
    if note == "fastbaud-test":
        if scenario == "fail":
            raw = _frames(120, 1.1, "161", "0102030405060708")
        elif scenario == "badraw":
            return {"started": time.time(), "note": note, "raw": [1, 2, 3]}
        else:
            raw = _frames(25413, 20.05, "161", "0102030405060708")
            raw += _frames(500, 20.0, "191", "01000000")
    else:
        n = 4000 if scenario != "thin" else 100
        len161 = 6 if scenario == "short" else 8
        len164 = 5 if scenario == "short" else 8
        ids = ["097", "1AA", "1CF", "374"]
        if scenario == "missing":
            ids = ["097", "1AA", "1CF"]
        for ident in ids:
            raw += _frames(n, 19.9, ident, "0011223344556677")
        raw += _frames(n, 19.9, "161", "00" * len161)
        raw += _frames(n, 19.9, "164", "00" * len164)
        raw += _frames(200, 19.9, "191", "01000000")
    return {"started": time.time(), "note": note, "raw": raw}


def cmd_capture(argv):
    note = argv[argv.index("--note") + 1]
    scenario = os.environ.get("FAKE_A" if note == "fastbaud-test" else "FAKE_B", "pass")
    open(os.path.join(OSTATE, "capture-running"), "w").write(str(os.getpid()))
    kill_finisher()
    publish_yielded()

    if scenario == "hang":
        # The daemon takes the port back when the lease runs out, whatever
        # the (killed) capture is doing.
        spawn_finisher(os.environ.get("FAKE_HANG_BACK", "never"))
        time.sleep(600)
        return 0

    secs = float(os.environ.get("FAKE_CAPTURE_SECS", "0"))
    end = time.time() + secs
    while time.time() < end:
        publish_yielded()
        time.sleep(0.3)

    if scenario == "exit1":
        print("the daemon is holding the port", file=sys.stderr)
        spawn_finisher(os.environ.get("FAKE_BACK", "1.0"))
        return 1

    doc = _capture_doc(note, scenario)
    caps = os.path.join(OSTATE, "captures")
    os.makedirs(caps, exist_ok=True)
    path = os.path.join(caps, time.strftime("%Y%m%d-%H%M%S") + f"-{note}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    print(f"  saved: {path}")
    spawn_finisher(os.environ.get("FAKE_BACK", "1.0"))
    return 0


def cmd_spawn(argv):
    """Start the stand-in daemon (used by the test's own set-up)."""
    spawn_finisher(argv[0], float(argv[1]))
    return 0


def cmd_drive_off(argv):
    """The recorder stops. If it was mid-leg the daemon has the adapter lent
    out and gets it back after FAKE_OFF_BACK seconds."""
    if os.environ.get("FAKE_START_YIELDED"):
        spawn_finisher(os.environ.get("FAKE_OFF_BACK", "1.0"))
    return 0


# ------------------------------------------------------------- the restart

def _conf_env(dropin):
    env = {}
    try:
        names = sorted(os.listdir(dropin))
    except OSError:
        return env
    for name in names:
        if not name.endswith(".conf"):
            continue
        with open(os.path.join(dropin, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Environment="):
                    for tok in line[len("Environment="):].split():
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            env[k] = v
    return env


def cmd_restart(argv):
    """`systemctl --user restart omacar-drivelog`: a new supervisor starts
    with whatever the drop-ins say, and publishes drivelog.json. Uses the
    real drivelog.read_overrides(), so the bounds are the real ones."""
    dropin = os.environ.get("OMACAR_DROPIN_DIR", "")
    for k, v in _conf_env(dropin).items():
        os.environ[k] = v
    import drivelog
    if os.environ.get("FAKE_RESTART_APPLY") == "stale":
        return 0            # the old supervisor's file is left exactly as it was
    ov = drivelog.read_overrides()
    doc = {"at": time.time(), "started": time.time(), "state": "waiting",
           "detail": "for the car", "legs": 0, "frames": 0,
           "between": ov["between"], "leg_lines": ov["leg_lines"],
           "quiet": ov["quiet"], "end_on_overflow": ov["end_on_overflow"]}
    _write_json(drivelog.STATE, doc)
    return 0


def main(argv):
    cmd = argv[0] if argv else ""
    fn = {"capture": cmd_capture, "finisher": cmd_finisher, "drive-off": cmd_drive_off, "spawn": cmd_spawn,
          "restart": cmd_restart}.get(cmd)
    if fn is None:
        print(f"driveway_fake.py: unknown command {cmd!r}", file=sys.stderr)
        return 2
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
