#!/usr/bin/env python3
"""tools/demo_record.sh, the tablet's backup-video recorder, dry-run.

It is run once, at the owner's desk, with the tour playing out loud; there is
no second go. So its logic is held here against stand-ins: a stand-in
gpu-screen-recorder that writes down how it was started and stopped, a
stand-in demo server that takes the ask for the tour as the page does (by the
tour's reset reaching demo-cue.json), and stand-in demo windows. The real
recorder is never called (a shim of its name fails loudly), and nothing plays.

  it records eDP-1 and the default output, H.264 and AAC at a constant 30 fps;
  the recorder starts before the tour is asked for, and is stopped with SIGINT
    (which it can receive: a script's & job inherits SIGINT ignored) once the
    tour's steps have run from the page's reset;
  a page that never takes the ask, a TERM, or a recorder that dies mid-tour
    still ends with the recorder stopped;
  and it refuses, naming what is missing, without starting the recorder: no
    recorder, no demo answering, no window, a muted window, no such monitor.

Linux: the script is bash with pgrep, as on the tablet.
"""

import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "demo_record.sh")
PY = sys.executable

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


if not sys.platform.startswith("linux"):
    print("\n  demo_record.sh: Linux only (bash, pgrep, /proc); skipped\n")
    sys.exit(0)

SCRATCH = tempfile.mkdtemp(prefix="omacar-demo-record-")
HOME = os.path.join(SCRATCH, "home")
BIN = os.path.join(SCRATCH, "bin")
RUN = os.path.join(SCRATCH, "run")
DEMO_ROOT = os.path.join(HOME, ".local", "state", "omacar-demo")
CUE = os.path.join(DEMO_ROOT, "state", "omacar", "demo-cue.json")
FAKE = os.path.join(SCRATCH, "fake-gsr")
FAKE_LOG = os.path.join(SCRATCH, "gsr.jsonl")
SHIM_LOG = os.path.join(SCRATCH, "shims.log")
TOUR = os.path.join(SCRATCH, "tour.json")
FILM = os.path.join(SCRATCH, "film.mp4")
OUT = os.path.join(HOME, "Videos", "omacar-demo-backup-tablet.mp4")
for d in (BIN, RUN, os.path.dirname(CUE)):
    os.makedirs(d, exist_ok=True)
os.chmod(RUN, 0o700)

# A two-second tour: the script waits for its steps, the reset's 3 s and 2 s of
# the closing Home, so 7 s from the reset.
with open(TOUR, "w", encoding="utf-8") as f:
    json.dump({"steps": [{"id": "home", "secs": 1}, {"id": "end", "secs": 1}]}, f)
TOUR_SECS = 2 + 3 + 2

# A real, tiny film for the stand-in to leave behind, so the script's closing
# ffprobe has something to read. Without ffmpeg, bytes.
if shutil.which("ffmpeg"):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=64x48:rate=10", "-t", "1", "-pix_fmt", "yuv420p", FILM],
                   check=True, capture_output=True, timeout=60)
else:
    with open(FILM, "wb") as f:
        f.write(b"not a film")

# THE STAND-IN RECORDER. It lists two monitors; recording, it writes down its
# arguments, whether it was handed SIGINT ignored, and how it was stopped, and
# on SIGINT leaves the film at -o, as gpu-screen-recorder finishes its file.
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(f"""#!{PY}
import json, shutil, signal, sys, time
LOG = {FAKE_LOG!r}
def note(**k):
    with open(LOG, "a") as f:
        f.write(json.dumps(k) + "\\n")
if "--list-monitors" in sys.argv:
    print("eDP-1|2736x1824")
    print("HDMI-A-1|1920x1080")
    sys.exit(0)
note(event="start", argv=sys.argv[1:], at=time.time(),
     sigint_ignored=signal.getsignal(signal.SIGINT) == signal.SIG_IGN)
out = sys.argv[sys.argv.index("-o") + 1]
def stop(n, _):
    shutil.copy({FILM!r}, out)
    note(event="stop", signal=signal.Signals(n).name, at=time.time())
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
die_after = float(__import__("os").environ.get("FAKE_DIE_AFTER") or 0)
end = time.time() + (die_after or 120)
while time.time() < end:
    time.sleep(0.05)
note(event="died" if die_after else "timeout", at=time.time())
sys.exit(3)
""")
os.chmod(FAKE, 0o755)

# Shims that must never be reached: the real recorder by its name, and the
# sound tools the script has no business calling.
for name in ("gpu-screen-recorder", "pactl", "wpctl"):
    p = os.path.join(BIN, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\necho "{name} $*" >> "{SHIM_LOG}"\nexit 1\n')
    os.chmod(p, 0o755)


# THE STAND-IN DEMO. The page takes an ask for demo-tour within 1.5 s, and the
# tour's reset sends `restart` to the world first thing.
class Demo(http.server.ThreadingHTTPServer):
    take_asks = True
    asks = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"ok"):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._reply(200 if self.path == "/demo.html" else 404)

    do_HEAD = do_GET

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != "/api/screen":
            return self._reply(404)
        ask = json.loads(body or b"{}")
        self.server.asks.append((time.time(), ask))
        if self.server.take_asks and ask.get("view") == "demo-tour":
            def reset():
                with open(CUE + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({"cue": "restart", "at": time.time()}, f)
                os.replace(CUE + ".tmp", CUE)
            threading.Timer(0.8, reset).start()
        self._reply(200, b'{"ok": true}')


def serve():
    s = Demo(("127.0.0.1", 0), Handler)
    threading.Thread(target=s.serve_forever, args=(0.05,), daemon=True).start()
    return s


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def window(muted=False):
    """A stand-in demo window: a process with the demo profile's argument."""
    args = [f"--user-data-dir={DEMO_ROOT}/browser", "--app=http://127.0.0.1/demo.html", "--kiosk"]
    if muted:
        args.append("--mute-audio")
    return subprocess.Popen([PY, "-c", "import time; time.sleep(600)", *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def events():
    try:
        with open(FAKE_LOG, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return []


def reset():
    for p in (FAKE_LOG, SHIM_LOG, CUE, OUT, OUT + ".log"):
        try:
            os.remove(p)
        except OSError:
            pass


ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(("XDG_", "OMACAR_", "WAYLAND_", "PULSE_"))}
ENV.update(HOME=HOME, PATH=BIN + ":/usr/local/bin:/usr/bin:/bin", XDG_RUNTIME_DIR=RUN,
           WAYLAND_DISPLAY="wayland-test", OMACAR_GSR=FAKE, OMACAR_TOUR_JSON=TOUR)


def record(port, *args, env=None, timeout=90):
    e = dict(ENV, OMACAR_DEMO_PORT=str(port), **(env or {}))
    t = time.time()
    r = subprocess.run(["bash", SCRIPT, *args], env=e, capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr, time.time() - t


def stand_ins_left():
    out = subprocess.run(["pgrep", "-f", FAKE], capture_output=True, text=True).stdout.split()
    return [p for p in out if p.strip()]


server = serve()
PORT = server.server_address[1]
win = None
try:
    head("a whole tour: the recorder around it, stopped at its end")
    open(os.path.join(DEMO_ROOT, "ACTIVE"), "w").close()
    win = window()
    reset()
    rc, out, took = record(PORT)
    check("it succeeds", rc, 0)
    if rc:
        print(out)
    ev = events()
    starts = [e for e in ev if e["event"] == "start"]
    stops = [e for e in ev if e["event"] == "stop"]
    check("the recorder ran once", len(starts), 1)
    if starts:
        check("recording eDP-1 and the default output, H.264 and AAC at a constant 30 fps",
              starts[0]["argv"], ["-w", "eDP-1", "-f", "30", "-a", "default_output",
                                  "-k", "h264", "-ac", "aac", "-fm", "cfr", "-o", OUT])
        check("and it can hear SIGINT (a script's & job inherits it ignored)",
              starts[0]["sigint_ignored"], False)
        check("it was recording before the tour was asked for",
              bool(server.asks) and starts[0]["at"] < server.asks[0][0], True)
    check("the page was asked for the tour, as `omacar demo tour` asks",
          [a[1].get("view") for a in server.asks], ["demo-tour"])
    check("it was stopped with SIGINT, which finishes the file",
          [s["signal"] for s in stops], ["SIGINT"])
    try:
        with open(CUE, encoding="utf-8") as f:
            began = json.load(f)["at"]
    except (OSError, ValueError, KeyError):
        began = None
    if stops and began:
        after = stops[0]["at"] - began
        check(f"once the tour's {TOUR_SECS - 5} s, the reset's 3 s and 2 s of Home had run "
              f"from the reset (took {after:.1f} s)",
              TOUR_SECS - 0.3 <= after <= TOUR_SECS + 2.5, True)
    check("the film is where it was asked for",
          os.path.exists(OUT) and os.path.getsize(OUT) > 0, True)
    check("the recorder's log is not left beside it", os.path.exists(OUT + ".log"), False)
    check("and it says how long the film is",
          "play it with: omacar-demo video" in out and OUT in out, True)
    check("no stand-in recorder is left running", stand_ins_left(), [])

    head("a page that never takes the ask")
    reset()
    server.asks.clear()
    server.take_asks = False
    rc, out, took = record(PORT)
    server.take_asks = True
    check("it fails, and says why", (rc != 0, "did not start the tour" in out), (True, True))
    check("and the recorder is still stopped, with SIGINT",
          [e.get("signal") for e in events() if e["event"] == "stop"], ["SIGINT"])
    check("no stand-in recorder is left running", stand_ins_left(), [])

    head("told to stop mid-tour (TERM, or an ssh that drops)")
    reset()
    e = dict(ENV, OMACAR_DEMO_PORT=str(PORT))
    p = subprocess.Popen(["bash", SCRIPT], env=e, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if os.path.exists(CUE):
            break
        time.sleep(0.1)
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    out = p.communicate(timeout=30)[0]
    check("it exits for the TERM", p.returncode, 143)
    check("and the recorder is stopped with SIGINT, so the film is whole",
          [e.get("signal") for e in events() if e["event"] == "stop"], ["SIGINT"])
    check("no stand-in recorder is left running", stand_ins_left(), [])

    head("a recorder that dies during the tour")
    reset()
    rc, out, took = record(PORT, env={"FAKE_DIE_AFTER": "4"})
    check("it fails, and says so", (rc != 0, "the recorder stopped during the tour" in out),
          (True, True))

    head("what it refuses, before the recorder starts")
    reset()
    rc, out, _ = record(PORT, env={"OMACAR_GSR": os.path.join(SCRATCH, "no-such-recorder")})
    check("no recorder: named", (rc != 0, "missing here:" in out and "no-such-recorder" in out),
          (True, True))
    rc, out, _ = record(free_port())
    check("no demo answering: says to bring it up", (rc != 0, "omacar-demo on" in out), (True, True))
    rc, out, _ = record(PORT, "--monitor", "DP-9")
    check("no such monitor: named, with the ones there are",
          (rc != 0, "no monitor DP-9" in out and "eDP-1" in out), (True, True))
    win.kill()
    win.wait()
    rc, out, _ = record(PORT)
    check("no demo window: says so", (rc != 0, "window is not open" in out), (True, True))
    win = window(muted=True)
    rc, out, _ = record(PORT)
    check("a muted window: says the film would be silent",
          (rc != 0, "started muted" in out and "silent" in out), (True, True))
    win.kill()
    win.wait()
    win = window()
    os.remove(os.path.join(DEMO_ROOT, "ACTIVE"))
    rc, out, _ = record(PORT)
    check("a demo that is not this HOME's: says so", (rc != 0, "no ACTIVE" in out), (True, True))
    check("in none of those did the recorder start",
          [e for e in events() if e["event"] == "start"], [])
    check("the real recorder, pactl and wpctl were never called",
          os.path.exists(SHIM_LOG), False)
finally:
    if win:
        win.kill()
        win.wait()
    for pid in stand_ins_left():
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
    server.shutdown()
    server.server_close()
    shutil.rmtree(SCRATCH, ignore_errors=True)

print()
print("  all passed" if not fails else f"  {fails} failed")
print()
sys.exit(1 if fails else 0)
