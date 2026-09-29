#!/usr/bin/env python3
"""The screen does not depend on a process that nobody is watching.

WHY THIS EXISTS. On 29 September at 11:49, in the middle of a drive, the
kiosk's screen went blank. Chromium was up and still pointed at
http://127.0.0.1:7560/app.html; nothing was listening on 7560. lib/serve.py had
died. It is started once, detached, by app_base() in bin/omacar, nothing
supervised it, and its output went to /dev/null -- so the screen stayed blank
until somebody restarted it by hand, and the cause of the death is unknown.

Two things are asserted here, and both need a real server and a real shell:

  the server's output is kept, in the state directory, and kept small, so the
    next death leaves something to read
  the kiosk restarts the server on the SAME port when it goes, which is what
    lets the page recover without a reload

Chromium is never launched. The kiosk is run against a stand-in that sleeps
until told to stop, and every other tool it would touch (the idle switch, the
browser launcher) is a stand-in that only writes down that it was called.
Nothing here reads or writes the real HOME or any real XDG directory, and every
port is a scratch one: bin/omacar takes its port list from OMACAR_PORTS so that
a test never goes looking on 7560.

Linux only, because the thing under test is bash on Linux (setsid, ss).
"""

import http.client
import os
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OMACAR = os.path.join(ROOT, "bin", "omacar")
SERVE = os.path.join(ROOT, "lib", "serve.py")
SHARE = os.path.join(ROOT, "share")

# Resolved BEFORE any environment is redirected. `python3` may be a
# version-manager shim that finds the real interpreter through XDG_DATA_HOME
# (smoke.sh has the story), and every test here redirects it.
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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def answers(port):
    """Is the OmaCar server what is answering on this port."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        c.request("GET", "/.mark")
        return b"omacar-server" in c.getresponse().read()
    except (OSError, http.client.HTTPException):
        return False


def wait_for(pred, limit, step=0.05):
    """Seconds until pred() is true, or None if it never was within limit."""
    t0 = time.monotonic()
    while True:
        if pred():
            return time.monotonic() - t0
        if time.monotonic() - t0 > limit:
            return None
        time.sleep(step)


def pgrep(pattern):
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


def server_pids(port):
    return pgrep(f"{SERVE} {port} ")


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not re.search(r"^State:\s+Z", read(f"/proc/{pid}/status"), re.M)


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


class Scratch:
    """A machine's worth of state that is not the real one.

    HOME and every XDG root are redirected, so the state directory bin/omacar
    derives (and the Chromium profile, and the venv it looks for) all land
    inside a directory this test deletes. PATH gets stand-ins in front: nothing
    real is ever the thing that answers.
    """

    def __init__(self, port, every=1):
        self.dir = tempfile.mkdtemp(prefix="omacar-srvwatch-")
        d = self.dir
        self.port = port
        self.state = os.path.join(d, "state", "omacar")
        self.bin = os.path.join(d, "bin")
        self.marks = os.path.join(d, "marks")
        run = os.path.join(d, "run")
        for p in (os.path.join(d, "home"), self.bin, self.marks, run):
            os.makedirs(p)
        os.chmod(run, 0o700)
        self.env = dict(
            os.environ,
            HOME=os.path.join(d, "home"),
            XDG_STATE_HOME=os.path.join(d, "state"),
            XDG_CONFIG_HOME=os.path.join(d, "config"),
            XDG_DATA_HOME=os.path.join(d, "data"),
            XDG_CACHE_HOME=os.path.join(d, "cache"),
            XDG_RUNTIME_DIR=run,
            PATH=self.bin + os.pathsep + os.environ.get("PATH", ""),
            OMACAR_PORTS=str(port),
            OMACAR_WATCH_EVERY=str(every),
            # So a server that is killed hard says why, in its log, in a form
            # this test can look for. (serve.py does not turn this on itself.)
            PYTHONFAULTHANDLER="1",
        )
        m = self.marks
        self.fake("python3", f"exec '{PY}' \"$@\"\n")
        self.fake("chromium",
                  f"echo $$ > '{m}/chromium.pid'\n"
                  f"printf '%s\\n' \"$@\" > '{m}/chromium.args'\n"
                  f"while [ ! -e '{m}/stop' ]; do sleep 0.2; done\n")
        self.fake("omarchy-toggle-idle", f"echo \"$@\" >> '{m}/idle.calls'\n")
        self.fake("omarchy-launch-browser", f"echo \"$@\" >> '{m}/browser.calls'\n")

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def mark(self, name):
        return read(os.path.join(self.marks, name))

    def path(self, name):
        return os.path.join(self.state, name)

    def clean(self):
        """Nothing this scratch started may outlive it."""
        # Matched on this checkout's own paths and this scratch port, so it
        # cannot reach a real server or a real kiosk on the same machine.
        for pattern in (f"{SERVE} {self.port} ",
                        f"{OMACAR} server watch {self.port}"):
            subprocess.run(["pkill", "-9", "-f", pattern])
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- the server's output is kept, and kept small ----------------------------

def test_log():
    print("\n  The server's output goes to a log, and the log stays small\n")
    port = free_port()
    s = Scratch(port)
    try:
        os.makedirs(s.state, exist_ok=True)
        log = s.path("serve.log")
        # A log that has been growing for a long time, ending in a line that a
        # person reading it afterwards would want.
        with open(log, "w") as f:
            for i in range(30000):
                f.write(f"old line {i:06d} {'x' * 80}\n")
            f.write("LAST LINE BEFORE THE START\n")
        before = os.path.getsize(log)

        subprocess.run([OMACAR, "open"], env=s.env, timeout=30,
                       capture_output=True)
        check("`omacar open` started the server on the scratch port",
              wait_for(lambda: answers(port), 3) is not None)
        check("and opened the page at that server's address",
              wait_for(lambda: f"http://127.0.0.1:{port}/app.html"
                       in s.mark("browser.calls"), 3) is not None)

        text = read(log)
        size = os.path.getsize(log) if os.path.exists(log) else 0
        check(f"an oversized log is cut to about a megabyte "
              f"({before} -> {size} bytes)", 0 < size <= (1 << 20) + 4096)
        check("and it is the END that is kept, not the beginning",
              "LAST LINE BEFORE THE START" in text
              and "old line 000000" not in text)
        check("each start is marked, with the time and the port",
              re.search(rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d "
                        rf"serve\.py starting on port {port}$", text, re.M)
              is not None)

        pids = server_pids(port)
        check("exactly one server is running", len(pids) == 1)
        if pids:
            os.kill(pids[0], signal.SIGABRT)
            wait_for(lambda: not alive(pids[0]), 3)
        check("what a crash prints, the log has",
              wait_for(lambda: "Fatal Python error: Aborted" in read(log), 2)
              is not None)

        subprocess.run([OMACAR, "open"], env=s.env, timeout=30,
                       capture_output=True)
        wait_for(lambda: answers(port), 3)
        text = read(log)
        check("a new start does not erase what killed the last one",
              "Fatal Python error: Aborted" in text)
        check("and marks itself underneath it",
              text.count(f"serve.py starting on port {port}") == 2)
    finally:
        s.clean()


def main():
    # A crash test that leaves a core file per run is not a kindness to the box.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if not sys.platform.startswith("linux"):
        print("    (skipping: this drives bash, setsid and ss, which are Linux)")
        return 0

    test_log()

    print()
    left = pgrep(f"{ROOT}/(bin/omacar|lib/serve.py)")
    check("nothing this test started is still running", not left)
    for pid in left:
        os.kill(pid, signal.SIGKILL)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
