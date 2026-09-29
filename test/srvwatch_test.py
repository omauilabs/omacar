#!/usr/bin/env python3
"""The screen does not depend on a process that nobody is watching.

WHY THIS EXISTS. On 29 September at 11:49, in the middle of a drive, the
kiosk's screen went blank. Chromium was up and still pointed at
http://127.0.0.1:7560/app.html; nothing was listening on 7560. lib/serve.py had
died. It is started once, detached, by app_base() in bin/omacar, nothing
supervised it, and its output went to /dev/null -- so the screen stayed blank
until somebody restarted it by hand, and the cause of the death is unknown.

Three things are asserted here, and all of them need a real server:

  the server's output is kept, in the state directory, and kept small, so the
    next death leaves something to read
  the kiosk restarts the server on the SAME port when it goes, which is what
    lets the page recover without a reload
  a signal that ends the server leaves a line saying which, because a Python
    process otherwise dies from one without a word

Chromium is never launched. The kiosk is run against a stand-in that sleeps
until told to stop, and every other tool it would touch (the idle switch, the
browser launcher) is a stand-in that only writes down that it was called.
Nothing here reads or writes the real HOME or any real XDG directory, and every
port is a scratch one: bin/omacar takes its port list from OMACAR_PORTS so that
a test never goes looking on 7560.

Linux only, because the thing under test is bash on Linux (setsid, ss).
"""

import http.client
import http.server
import os
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
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
            # Python's fault handler on from outside, so the log tests do not
            # lean on serve.py's own. test_says_so takes it away again to show
            # that serve.py turns it on for itself.
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


# ---- the watcher --------------------------------------------------------------

def start_server(s):
    """A server on the scratch port, as app_base() would have started it."""
    server = subprocess.Popen([PY, SERVE, str(s.port), SHARE], env=s.env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True)
    if wait_for(lambda: answers(s.port), 5) is None:
        raise RuntimeError("the scratch server never came up")
    return server


def start_watcher(s, *extra):
    return subprocess.Popen([OMACAR, "server", "watch", str(s.port), *extra],
                            env=s.env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def stamped(port, what):
    """A log line that begins with a date and a time and says `what`."""
    return re.compile(rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d {what} on port {port}$")


def test_restart():
    print("\n  A server that dies comes back on the same port\n")
    every = 1
    port = free_port()
    s = Scratch(port, every=every)
    server = watcher = None
    try:
        server = start_server(s)
        watcher = start_watcher(s)
        time.sleep(every * 2.5)
        check("while the server is healthy, the watcher leaves it alone",
              watcher.poll() is None and server.poll() is None
              and server_pids(port) == [server.pid]
              and not read(s.path("serve-watch.log")))

        for n in (1, 2):
            pids = server_pids(port)
            if not pids:
                bad(f"kill {n}: there is no server left to kill")
                break
            pid = pids[0]
            os.kill(pid, signal.SIGKILL)
            if pid == server.pid:
                server.wait()
            wait_for(lambda: not alive(pid), 2)
            check(f"kill {n}: the port really is dead", not answers(port))
            t = wait_for(lambda: answers(port), every + 4)
            check(f"kill {n}: back on the same port within the interval"
                  f" ({'never' if t is None else f'{t:.1f}s'} against {every}s"
                  f" and a start)", t is not None and t <= every + 3)
            now = server_pids(port)
            check(f"kill {n}: as one new process", len(now) == 1 and now[0] != pid)

            def restarts():
                return [ln for ln in read(s.path("serve-watch.log")).splitlines()
                        if "restarted" in ln]

            # The watcher writes its line after it has seen the server answer,
            # which is a moment after this test has. Waiting for it also keeps
            # the next kill out of the watcher's own confirmation of this one.
            wait_for(lambda: len(restarts()) >= n, 3)
            said = restarts()
            check(f"kill {n}: the restart is in the log, with its time"
                  f" ({len(said)} line{'s' * (len(said) != 1)})",
                  len(said) == n and stamped(port, r"serve\.py restarted").match(said[-1]))
            check(f"kill {n}: and the new server's output has a log to go to",
                  read(s.path("serve.log")).count(
                      f"serve.py starting on port {port}") == n)
    finally:
        for proc in (watcher, server):
            if proc and proc.poll() is None:
                proc.kill()
        s.clean()


def test_unwritable_watch_log():
    print("\n  A watch log that cannot be written does not stop the restart\n")
    if os.geteuid() == 0:
        print("    (skipping: root can write a read-only file, so this cannot fail here)")
        return
    every = 1
    port = free_port()
    s = Scratch(port, every=every)
    server = watcher = None
    try:
        server = start_server(s)
        os.makedirs(s.state, exist_ok=True)
        log = s.path("serve-watch.log")
        open(log, "w").close()
        os.chmod(log, 0o444)
        watcher = start_watcher(s)
        time.sleep(every * 1.5)
        for n in (1, 2):
            pids = server_pids(port)
            if not pids:
                bad(f"kill {n}: there is no server left to kill")
                break
            os.kill(pids[0], signal.SIGKILL)
            if pids[0] == server.pid:
                server.wait()
            wait_for(lambda: not alive(pids[0]), 2)
            t = wait_for(lambda: answers(port), every + 4)
            check(f"kill {n}: the server is restarted although the log cannot be"
                  f" written ({'never' if t is None else f'{t:.1f}s'})",
                  t is not None)
            check(f"kill {n}: and the watcher is still watching", watcher.poll() is None)
            time.sleep(1)
        check("nothing was written to the log it could not write",
              read(log) == "")
    finally:
        for proc in (watcher, server):
            if proc and proc.poll() is None:
                proc.kill()
        s.clean()


def test_unwritable_serve_log():
    print("\n  A serve.log that cannot be opened does not stop the server starting\n")
    if os.geteuid() == 0:
        print("    (skipping: root can write a read-only file, so this cannot fail here)")
        return
    every = 1
    port = free_port()
    s = Scratch(port, every=every)
    watcher = None
    try:
        os.makedirs(s.state, exist_ok=True)
        log = s.path("serve.log")
        with open(log, "w") as f:
            f.write("earlier output\n")
        os.chmod(log, 0o444)

        subprocess.run([OMACAR, "open"], env=s.env, timeout=30, capture_output=True)
        check("`omacar open` still starts the server",
              wait_for(lambda: answers(port), 3) is not None)
        wait_for(lambda: s.mark("browser.calls"), 3)      # setsid -f: a moment later
        calls = s.mark("browser.calls")
        check("and opens the page at it, not at the file:// fallback",
              f"http://127.0.0.1:{port}/app.html" in calls and "file://" not in calls)
        check("the log it could not write is left as it was",
              read(log) == "earlier output\n")

        # The watcher starts it the same way, so it has to survive the same thing.
        watcher = start_watcher(s)
        time.sleep(every * 1.5)
        pids = server_pids(port)
        if not pids:
            bad("there is no server to kill")
        else:
            os.kill(pids[0], signal.SIGKILL)
            wait_for(lambda: not alive(pids[0]), 2)
            t = wait_for(lambda: answers(port), every + 4)
            check(f"a killed server is restarted with the log still unwritable"
                  f" ({'never' if t is None else f'{t:.1f}s'})", t is not None)
    finally:
        if watcher and watcher.poll() is None:
            watcher.kill()
        s.clean()


def test_foreign_port():
    print("\n  A port held by something that is not ours is left alone\n")
    every = 0.5
    port = free_port()
    s = Scratch(port, every=every)

    class NotOurs(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"some other program"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    other = http.server.ThreadingHTTPServer(("127.0.0.1", port), NotOurs)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    watcher = None
    try:
        watcher = start_watcher(s)
        time.sleep(every * 8)
        said = read(s.path("serve-watch.log"))
        # A second server could not bind, so it would die at once and leave no
        # process to find. What it would leave is its banner in serve.log.
        check("no second server was started, or even tried",
              not server_pids(port)
              and "serve.py starting" not in read(s.path("serve.log")))
        check("the watcher is still running, and still watching", watcher.poll() is None)
        check("it says so once, not once an interval",
              said.count("not the OmaCar server") == 1)
        check("and never claims to have restarted anything", "restarted" not in said)
    finally:
        if watcher and watcher.poll() is None:
            watcher.kill()
        other.shutdown()
        other.server_close()
        s.clean()


def test_parent_exit():
    print("\n  The watcher ends with the process that started it\n")
    every = 1
    port = free_port()
    s = Scratch(port, every=every)
    parent = server = watcher = None
    try:
        server = start_server(s)
        parent = subprocess.Popen(["sleep", "300"])
        watcher = start_watcher(s, str(parent.pid))
        time.sleep(every * 2.5)
        check("it carries on while that process is alive", watcher.poll() is None)
        # SIGKILL, so that no trap of the parent's gets to run: this is the
        # route a kiosk takes when something kills it outright.
        parent.kill()
        parent.wait()
        t = wait_for(lambda: watcher.poll() is not None, every + 3)
        check(f"it stops within an interval of the parent going"
              f" ({'never' if t is None else f'{t:.1f}s'})", t is not None)
        check("and nothing of it is left running",
              not pgrep(f"{OMACAR} server watch {port}"))
    finally:
        for proc in (watcher, parent, server):
            if proc and proc.poll() is None:
                proc.kill()
        s.clean()


def test_kiosk():
    print("\n  The kiosk starts it on its own port, and stops it on the way out\n")
    # An interval far longer than this test, so that the ONLY thing that can
    # have stopped the watcher early is the kiosk's own exit trap.
    every = 30
    port = free_port()
    s = Scratch(port, every=every)
    kiosk = None
    try:
        with open(os.path.join(s.dir, "kiosk.err"), "w") as err:
            kiosk = subprocess.Popen([OMACAR, "kiosk", "hub"], env=s.env,
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=err,
                                     start_new_session=True)
        up = wait_for(lambda: os.path.exists(os.path.join(s.marks, "chromium.pid")), 10)
        check("the kiosk got as far as starting the browser", up is not None)
        check("pointed at the server on the scratch port",
              f"--app=http://127.0.0.1:{port}/app.html#hub" in s.mark("chromium.args"))
        check("and that server is up", answers(port))
        mine = f"{OMACAR} server watch {port} {kiosk.pid}$"
        check("it started one watcher, for that port, tied to itself",
              len(pgrep(mine)) == 1)
        check("and held the screen awake", s.mark("idle.calls").split() == ["stay-awake"])

        open(os.path.join(s.marks, "stop"), "w").close()      # the browser exits
        try:
            kiosk.wait(10)
        except subprocess.TimeoutExpired:
            pass
        check("the kiosk ends when the browser does", kiosk.poll() is not None)
        t = wait_for(lambda: not pgrep(mine), 2)
        check(f"and the watcher goes with it, well inside its {every}s interval"
              f" ({'never' if t is None else f'{t:.1f}s'})", t is not None)
        check("in the same trap that puts the screen's idle switch back",
              s.mark("idle.calls").split() == ["stay-awake", "allow-idle"])
    finally:
        if kiosk and kiosk.poll() is None:
            os.killpg(kiosk.pid, signal.SIGKILL)
        s.clean()


# ---- a death that says so -----------------------------------------------------

def test_says_so():
    print("\n  A signal that ends the server leaves a line saying which\n")
    port = free_port()
    s = Scratch(port)
    # As the real one runs: nothing has turned Python's fault handler on.
    env = {k: v for k, v in s.env.items() if k != "PYTHONFAULTHANDLER"}
    try:
        for name, sig, status in (("SIGTERM", signal.SIGTERM, 143),
                                  ("SIGHUP", signal.SIGHUP, 129),
                                  ("SIGABRT", signal.SIGABRT, None)):
            errpath = os.path.join(s.dir, f"{name}.err")
            with open(errpath, "w") as err:
                server = subprocess.Popen([PY, SERVE, str(port), SHARE], env=env,
                                          stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=err,
                                          start_new_session=True)
            if wait_for(lambda: answers(port), 5) is None:
                bad(f"{name}: the scratch server never came up")
                server.kill()
                continue
            # A browser's idle keep-alive connection, held open across the
            # signal: the server must not wait for it to go away.
            held = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            held.request("GET", "/.mark")
            held.getresponse().read()
            server.send_signal(sig)
            try:
                code = server.wait(5)
            except subprocess.TimeoutExpired:
                server.kill()
                code = None
            held.close()
            text = read(errpath)
            if status is not None:
                check(f"{name}: it exits with the conventional status {status}"
                      f" (got {code})", code == status)
                check(f"{name}: and says which signal, with the time",
                      re.search(rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d "
                                rf"serve\.py: {name} received", text, re.M)
                      is not None)
            else:
                check("SIGABRT: a crash from inside leaves its stack trace",
                      "Fatal Python error: Aborted" in text)
    finally:
        s.clean()


def main():
    # A crash test that leaves a core file per run is not a kindness to the box.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if not sys.platform.startswith("linux"):
        print("    (skipping: this drives bash, setsid and ss, which are Linux)")
        return 0

    for test in (test_log, test_restart, test_unwritable_watch_log,
                 test_unwritable_serve_log,
                 test_foreign_port, test_parent_exit,
                 test_kiosk, test_says_so):
        try:
            test()
        except Exception as e:                       # noqa: BLE001
            bad(f"{test.__name__} stopped early: {e!r}")

    print()
    left = pgrep(f"{ROOT}/(bin/omacar|lib/serve.py)")
    check("nothing this test started is still running", not left)
    for pid in left:
        os.kill(pid, signal.SIGKILL)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
