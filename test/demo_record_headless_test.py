#!/usr/bin/env python3
"""tools/demo_record_headless.py's safety logic, against a stand-in pactl.

The box recording is the one run with an unmuted browser, at night, near a
sleeping child. Its sound is safe only while the browser's streams are on the
null sink, and while that sink stays loaded under them. So these are held here,
with a stand-in pactl serving a scripted PipeWire state (no sound server, no
browser and no sink are touched):

  the guard ignores a stream that is not linked yet (PipeWire-Pulse gives its
    sink as SPA_ID_INVALID), counts one linked to the null sink as proven, and
    cuts one linked anywhere else: the browser's processes killed, then the
    stream (kill-sink-input), never a volume or the default sink;
  it knows the streams that were there before by object.serial, and a stream
    it cannot trace to a process by its name (Chromium, or omacar-demo-rec);
  it FAILS CLOSED: three failed looks in a row stop the run and kill the
    browser, and a guard that has stopped makes check() stop the run, the
    tour's loop included;
  the null sink is loaded with WirePlumber's restore off for it, and a
    leftover that something still uses is never taken away;
  it is unloaded only when nothing of the browser's is left, no process and no
    stream; otherwise it stays loaded, loudly, with its module; and when the
    streams cannot be listed but the browser is gone, the unload is tried;
  once the teardown starts, a second Ctrl+C, a TERM or a HUP cannot stop it;
  and a second recording cannot start while one holds the lock.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

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
    print("\n  demo_record_headless: Linux only (/proc); skipped\n")
    sys.exit(0)

SCRATCH = tempfile.mkdtemp(prefix="omacar-rec-safety-")
BIN = os.path.join(SCRATCH, "bin")
WORLD = os.path.join(SCRATCH, "world.json")
CALLS = os.path.join(SCRATCH, "calls.log")
os.makedirs(BIN)

# THE STAND-IN PACTL: the state in WORLD, every call written to CALLS.
# `fail_inputs: n` makes the next n listings of sink-inputs fail.
with open(os.path.join(BIN, "pactl"), "w", encoding="utf-8") as f:
    f.write(f"""#!{sys.executable}
import json, sys
W, C = {WORLD!r}, {CALLS!r}
a = sys.argv[1:]
with open(C, "a") as f:
    f.write(" ".join(a) + "\\n")
w = json.load(open(W))
def save():
    json.dump(w, open(W, "w"))
if a[:2] == ["-f", "json"] and a[2] == "list":
    if a[3] == "sink-inputs" and w.get("fail_inputs", 0) > 0:
        w["fail_inputs"] -= 1
        save()
        print("Connection failure: Connection refused", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(w[a[3]]))
elif a[0] == "get-default-sink":
    print(w["default"])
elif a[0] == "get-sink-volume":
    print(w["volume"])
elif a[0] == "get-sink-mute":
    print("Mute: no")
elif a[0] == "load-module":
    name = [x.split("=", 1)[1] for x in a if x.startswith("sink_name=")][0]
    w["sinks"].append({{"index": 900, "name": name, "owner_module": 77,
                       "monitor_source": name + ".monitor"}})
    w["sources"].append({{"index": 900, "name": name + ".monitor"}})
    if w.get("load_moves_default"):
        w["default"] = name
    save()
    print(77)
elif a[0] == "unload-module":
    mod = int(a[1])
    gone = [s for s in w["sinks"] if s["owner_module"] == mod]
    w["sinks"] = [s for s in w["sinks"] if s["owner_module"] != mod]
    names = {{s["name"] + ".monitor" for s in gone}}
    w["sources"] = [s for s in w["sources"] if s["name"] not in names]
    save()
elif a[0] == "kill-sink-input":
    w["sink-inputs"] = [i for i in w["sink-inputs"] if i["index"] != int(a[1])]
    save()
else:
    sys.exit(1)
""")
os.chmod(os.path.join(BIN, "pactl"), 0o755)
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")

import demo_record_headless as R  # noqa: E402

REAL = {"index": 63, "name": "alsa_output.line", "owner_module": 1,
        "monitor_source": "alsa_output.line.monitor"}
OURS = {"index": 900, "name": R.SINK, "owner_module": 77, "monitor_source": R.SINK + ".monitor"}


def world(**over):
    w = {"default": "alsa_output.line", "volume": "Volume: front-left: 60%",
         "sinks": [dict(REAL)], "sources": [{"index": 63, "name": "alsa_output.line.monitor"}],
         "sink-inputs": [], "source-outputs": []}
    w.update(over)
    with open(WORLD, "w", encoding="utf-8") as f:
        json.dump(w, f)
    open(CALLS, "w").close()


def with_ours(**over):
    world(sinks=[dict(REAL), dict(OURS)],
          sources=[{"index": 63, "name": "alsa_output.line.monitor"},
                   {"index": 900, "name": R.SINK + ".monitor"}], **over)


def calls():
    with open(CALLS, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def changing(c=None):
    """The calls that change anything: the only ones allowed are the null
    sink's own load and unload, and kill-sink-input."""
    return [x for x in (calls() if c is None else c) if not x.startswith(("-f json list", "get-"))]


def stream(index, sink, pid=None, name="omacar-demo-rec"):
    props = {"application.name": name, "object.serial": str(5000 + index)}
    if pid is not None:
        props["application.process.id"] = str(pid)
    return {"index": index, "sink": sink, "properties": props}


def browser_stand_in(prof):
    """A process group like the browser's: a leader, and a child in its
    session, both carrying the profile on their command lines."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', sys.argv[1]]); "
         "time.sleep(60)", f"--user-data-dir={prof}"],
        start_new_session=True)


def children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def run_guard(sink, root, prof, before=(), secs=0.5):
    g = R.Guard(sink, root, prof, list(before))
    g.start()
    time.sleep(secs)
    g.stop()
    return g


def sink_at_900():
    s = R.NullSink("unix:/nowhere")
    s.index, s.module = 900, 77
    return s


prof = os.path.join(SCRATCH, "browser-profile")
b = None
try:
    head("the guard")
    b = browser_stand_in(prof)
    time.sleep(0.5)
    kid = (children(b.pid) or [None])[0]
    sink = sink_at_900()
    with_ours(**{"sink-inputs": [stream(5, 0xFFFFFFFF, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("a stream not linked yet is neither proof nor a fault", (g.ours, g.violation), (set(), None))
    check("and the browser is left alone", alive(b.pid), True)

    with_ours(**{"sink-inputs": [stream(5, 900, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("one of the browser's, linked to the null sink: proven", (g.ours, g.violation), ({5}, None))

    with_ours(**{"sink-inputs": [stream(6, 63, pid=os.getpid(), name="Firefox"),
                                 stream(7, 63, pid=1, name="Music")]})
    g = run_guard(sink, b.pid, prof)
    check("anybody else's streams on the real sink are not its business",
          (g.violation, changing()), (None, []))

    with_ours(**{"fail_inputs": 2, "sink-inputs": [stream(5, 900, pid=kid)]})
    g = run_guard(sink, b.pid, prof, secs=0.8)
    check("two failed looks in a row, then a good one: it carries on",
          (g.violation, g.errors, alive(b.pid)), (None, 2, True))

    with_ours(**{"sink-inputs": [stream(8, 63, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("one of the browser's linked to the real sink is a fault",
          bool(g.violation) and "#8" in g.violation, True)
    time.sleep(0.3)
    check("the browser's group is killed", (alive(b.pid), alive(kid) if kid else False), (False, False))
    check("then the stream is cut, and nothing else is changed", changing(), ["kill-sink-input 8"])
    b.wait(5)

    head("the guard fails closed")
    b = browser_stand_in(prof)
    time.sleep(0.5)
    with_ours(fail_inputs=999)
    g = R.Guard(sink, b.pid, prof, [])
    g.start()
    time.sleep(0.8)
    check(f"{R.BLIND} failed looks in a row are a fault",
          bool(g.violation) and "could not see" in g.violation, True)
    time.sleep(0.3)
    check("and the browser is killed for it", alive(b.pid), False)
    b.wait(5)
    try:
        g.check()
        got = None
    except R.GuardDown as e:
        got = str(e)
    check("and check() stops the run with it", got is not None and "could not see" in got, True)
    g.stop()
    check("nothing was changed but the browser", changing(), [])

    b = browser_stand_in(prof)
    time.sleep(0.5)
    with_ours()
    dead = R.Guard(sink, b.pid, prof, [])            # never started: not watching
    try:
        dead.check()
        got = None
    except R.GuardDown as e:
        got = str(e)
    check("a guard that is not watching stops the run", got is not None and "stopped watching" in got,
          True)
    time.sleep(0.3)
    check("and the browser is killed before anything else", alive(b.pid), False)
    b.wait(5)

    # THE TOUR'S LOOP ASKS IT. follow_tour calls check() at every look.
    import demo_e2e  # noqa: E402

    class FakePage:
        def __init__(self):
            self.looks = 0

        def value(self, expr, timeout=15, wait=False):
            if "tour.start()" in expr:
                return 0
            self.looks += 1
            return ["running", 0, 1.0, "#home", 1000]

    looks = {"n": 0}

    def guard_check():
        looks["n"] += 1
        if looks["n"] > 3:
            raise R.GuardDown("the guard stopped watching")

    page = FakePage()
    try:
        demo_e2e.follow_tour(page, [{"id": "home", "go": "#home", "secs": 40, "at": []}],
                             demo_e2e.Watch(), check=guard_check)
        got = None
    except R.GuardDown as e:
        got = str(e)
    check("the tour's loop stops at the first look after the guard is down",
          (got, page.looks), ("the guard stopped watching", 3))

    b = browser_stand_in(prof)
    time.sleep(0.5)
    with_ours(**{"sink-inputs": [stream(9, 63, pid=None, name="Chromium")]})
    g = run_guard(sink, b.pid, prof, secs=0.6)
    check("a Chromium stream with no process to trace counts as the browser's",
          (bool(g.violation), changing()), (True, ["kill-sink-input 9"]))
    b.wait(5)
    b = browser_stand_in(prof)
    time.sleep(0.5)
    with_ours(**{"sink-inputs": [stream(12, 63, pid=None, name=R.SINK)]})
    g = run_guard(sink, b.pid, prof, secs=0.6)
    check("and so does one named omacar-demo-rec", bool(g.violation), True)
    b.wait(5)
    with_ours(**{"sink-inputs": [stream(10, 63, pid=None, name="Chromium")]})
    g = run_guard(sink, b.pid, prof, before=[R.serial(stream(10, 63))])
    check("but not one that was there before the browser was, known by its object.serial",
          g.violation, None)
    check("an object.serial, not an index, is what is kept", R.serial(stream(10, 63)), "5010")

    head("loading the null sink")
    world()
    s = R.NullSink("unix:/nowhere")
    s.load()
    check("it loads module-null-sink with its name, and WirePlumber's restore off for it",
          changing(), [f"load-module module-null-sink sink_name={R.SINK} "
                       f"sink_properties=state.restore-props=false"])
    check("and knows its sink", (s.module, s.index), (77, 900))
    check("and nothing else moved", s.moved(), [])

    busy = {"index": 500, "name": R.SINK, "owner_module": 55, "monitor_source": R.SINK + ".monitor"}
    world(sinks=[dict(REAL), busy], sources=[{"index": 500, "name": R.SINK + ".monitor"}],
          **{"sink-inputs": [stream(3, 500, pid=1234)]})
    try:
        R.NullSink("unix:/nowhere").load()
        got = "loaded"
    except R.Refused as e:
        got = str(e)
    check("a leftover with a stream playing into it is not taken away",
          ("in use" in got, [c for c in changing() if "unload" in c]), (True, []))
    world(sinks=[dict(REAL), busy], sources=[{"index": 500, "name": R.SINK + ".monitor"}],
          **{"source-outputs": [{"index": 4, "source": 500}]})
    try:
        R.NullSink("unix:/nowhere").load()
        got = "loaded"
    except R.Refused as e:
        got = str(e)
    check("nor one that something is recording from",
          ("in use" in got, [c for c in changing() if "unload" in c]), (True, []))
    world(sinks=[dict(REAL), busy], sources=[{"index": 500, "name": R.SINK + ".monitor"}])
    R.NullSink("unix:/nowhere").load()
    check("an idle leftover is unloaded, then the sink loaded",
          changing()[:1], ["unload-module 55"])

    world(load_moves_default=True)
    s = R.NullSink("unix:/nowhere")
    try:
        s.load()
        got = None
    except RuntimeError as e:
        got = str(e)
    check("loading fails if it moved the default sink", got is not None and "moved" in got, True)
    s.unload(prof, wait=0.3)
    check("and the sink it loaded is unloaded", changing()[-1], "unload-module 77")

    head("unloading: only when nothing of the browser's is left")
    with_ours(**{"sink-inputs": [stream(11, 900, pid=1234)]})
    s = sink_at_900()
    problems = s.unload(prof, wait=1.0)
    check("a stream still on the sink: nothing is cut and the sink is NOT unloaded",
          (changing(), s.module), ([], 77))
    check("and it says so, loudly, with the module to unload later",
          any("LEFT LOADED (module 77)" in p and "pactl unload-module 77" in p for p in problems), True)

    b = browser_stand_in(prof)
    time.sleep(0.5)
    with_ours()
    s = sink_at_900()
    problems = s.unload(prof, wait=0.6)
    check("a process of the browser's still alive: not unloaded either",
          (changing(), s.module, any("browser process" in p for p in problems)), ([], 77, True))
    b.kill()
    b.wait(5)
    for k in children(b.pid):
        os.kill(k, signal.SIGKILL)
    time.sleep(0.3)
    for pid, _ in demo_e2e.mentions(prof):
        os.kill(pid, signal.SIGKILL)
    time.sleep(0.3)

    with_ours(fail_inputs=999)
    s = sink_at_900()
    problems = s.unload(prof, wait=0.3)
    check("the streams cannot be listed and the browser is gone: the unload is still tried",
          (changing(), s.module, any("could not be listed" in p for p in problems)),
          (["unload-module 77"], None, True))

    with_ours()
    s = sink_at_900()
    check("nothing left: unloaded, and nothing said", (s.unload(prof, wait=0.3), changing(), s.module),
          ([], ["unload-module 77"], None))

    head("signals during the teardown")
    # The real guarded() and take_down(), in a process of their own. The
    # teardown stops an ffmpeg stand-in that takes two seconds to finish, and
    # INT, TERM and HUP arrive meanwhile; it must still reach the unload.
    #   `first`: the body waits, and a first Ctrl+C stops it;
    #   `clean`: the body ends by itself, and the teardown is where the first
    #     signal lands.
    driver = os.path.join(SCRATCH, "driver.py")
    with open(driver, "w", encoding="utf-8") as f:
        f.write(f"""
import json, os, subprocess, sys, time
sys.path.insert(0, {TOOLS!r})
import demo_record_headless as R
mode, marks = sys.argv[1], sys.argv[2]
class A:
    out = "/nowhere.mp4"; keep = True; probe = True
r = R.Run(A(), {SCRATCH!r})
r.prof = {os.path.join(SCRATCH, "no-browser")!r}
r.sink = R.NullSink("unix:/nowhere")
r.sink.index, r.sink.module = 900, 77
r.ff = subprocess.Popen([sys.executable, "-c",
    "import signal, sys, time\\n"
    "def slow(*a):\\n"
    "    open(sys.argv[1], 'a').write('ffmpeg asked to stop\\\\n')\\n"
    "    time.sleep(2)\\n"
    "    sys.exit(0)\\n"
    "signal.signal(signal.SIGINT, slow)\\n"
    "time.sleep(60)\\n", marks], start_new_session=True)
time.sleep(0.3)
sigs = R.Signals()
sigs.arm()
def body(r):
    print("BODY", flush=True)
    if mode == "first":
        time.sleep(30)
R.guarded(sigs, body, r)
print("DONE " + json.dumps(r.problems), flush=True)
""")

    def teardown_under_fire(mode):
        marks = os.path.join(SCRATCH, f"marks-{mode}")
        with_ours()
        d = subprocess.Popen([sys.executable, driver, mode, marks], stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"]))
        first = d.stdout.readline().strip()
        if mode == "first":
            time.sleep(0.3)
            d.send_signal(signal.SIGINT)
        for _ in range(50):
            if os.path.exists(marks):
                break
            time.sleep(0.1)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            d.send_signal(sig)
            time.sleep(0.1)
        # The rest from the same buffered stream readline() used (communicate
        # with a timeout reads the raw pipe, and loses what readline read
        # ahead); a timer stands in for the timeout.
        stop = threading.Timer(30, d.kill)
        stop.start()
        lines = [first] + d.stdout.read().splitlines()
        d.wait()
        stop.cancel()
        done = [ln for ln in lines if ln.startswith("DONE ")]
        return (first, os.path.exists(marks), d.returncode, len(done),
                "unload-module 77" in changing(), json.loads(done[0][5:]) if done else None, lines)

    first, reached, rc, done, unloaded, got, _ = teardown_under_fire("first")
    check("a first Ctrl+C stops the body", first, "BODY")
    check("and the teardown reaches ffmpeg", reached, True)
    check("a second INT, a TERM and a HUP do not stop it: it reaches the unload",
          (rc, done, unloaded), (0, 1, True))
    check("and only the first signal is recorded", [p for p in got or [] if "signal" in p],
          ["stopped by signal SIGINT"])

    first, reached, rc, done, unloaded, got, lines = teardown_under_fire("clean")
    check("a body that ends by itself: the teardown reaches ffmpeg", (first, reached), ("BODY", True))
    check("INT, TERM and HUP during that teardown do not stop it: it reaches the unload",
          (rc, done, unloaded), (0, 1, True))
    check("and it said, as the teardown began, that signals are ignored until it is done",
          any("ignored until it is done" in ln for ln in lines), True)

    head("one recording at a time")
    lock = os.path.join(SCRATCH, "rec.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time; f = open(sys.argv[1], 'w'); "
         "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)", lock],
        stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    world()
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "demo_record_headless.py"),
                        "--probe", "--work", SCRATCH], capture_output=True, text=True, timeout=30,
                       # No sound server to find, whatever the lock does.
                       env=dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"],
                                XDG_RUNTIME_DIR=SCRATCH, OMACAR_REC_LOCK=lock))
    holder.kill()
    holder.wait()
    check("a second recording will not start while one holds the lock",
          (r.returncode, "another recording is running" in r.stdout, calls()), (2, True, []))
    check("the lock is in the runtime folder, whatever TMPDIR says",
          R.lock_path() if "OMACAR_REC_LOCK" not in os.environ else "(overridden)",
          os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}",
                       "omacar-demo-rec.lock"))
finally:
    for p in subprocess.run(["pgrep", "-f", SCRATCH], capture_output=True, text=True).stdout.split():
        try:
            os.kill(int(p), signal.SIGKILL)
        except OSError:
            pass
    import shutil
    shutil.rmtree(SCRATCH, ignore_errors=True)

print()
print("  all passed" if not fails else f"  {fails} failed")
print()
sys.exit(1 if fails else 0)
