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
  a stream it cannot trace to a process counts as the browser's if it says it
    is Chromium, and anybody else's streams are left alone;
  loading refuses to take away a leftover omacar-demo-rec that something is
    still playing into or recording from, and unloads one that is idle;
  loading fails, and the sink goes, if the default sink or its volume moved;
  unloading cuts what is still on the sink before the sink goes;
  and a second recording cannot start while one holds the lock.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

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


def world(**over):
    w = {"default": "alsa_output.line", "volume": "Volume: front-left: 60%",
         "sinks": [dict(REAL)], "sources": [{"index": 63, "name": "alsa_output.line.monitor"}],
         "sink-inputs": [], "source-outputs": []}
    w.update(over)
    with open(WORLD, "w", encoding="utf-8") as f:
        json.dump(w, f)
    open(CALLS, "w").close()


def calls():
    with open(CALLS, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def changing(c):
    """The calls that change anything: the only ones allowed are the null
    sink's own load and unload, and kill-sink-input."""
    return [x for x in c if not x.startswith(("-f json list", "get-"))]


def stream(index, sink, pid=None, name="Chromium"):
    props = {"application.name": name}
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
        os.kill(pid, 0)
    except OSError:
        return False
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


try:
    head("the guard")
    prof = os.path.join(SCRATCH, "browser-profile")
    b = browser_stand_in(prof)
    time.sleep(0.5)
    kid = (children(b.pid) or [None])[0]
    sink = R.NullSink("unix:/nowhere")
    sink.index = 900
    world(sinks=[dict(REAL), {"index": 900, "name": R.SINK, "owner_module": 77,
                              "monitor_source": R.SINK + ".monitor"}])
    world(**{**json.load(open(WORLD)), "sink-inputs": [stream(5, 0xFFFFFFFF, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("a stream not linked yet is neither proof nor a fault", (g.ours, g.violation), (set(), None))
    check("and the browser is left alone", alive(b.pid), True)

    world(**{**json.load(open(WORLD)), "sink-inputs": [stream(5, 900, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("one of the browser's, linked to the null sink: proven", (g.ours, g.violation), ({5}, None))

    world(**{**json.load(open(WORLD)),
             "sink-inputs": [stream(6, 63, pid=os.getpid(), name="Firefox"),
                             stream(7, 63, pid=1, name="Music")]})
    g = run_guard(sink, b.pid, prof)
    check("anybody else's streams on the real sink are not its business",
          (g.violation, changing(calls())), (None, []))

    world(**{**json.load(open(WORLD)), "sink-inputs": [stream(8, 63, pid=kid)]})
    g = run_guard(sink, b.pid, prof)
    check("one of the browser's linked to the real sink is a fault",
          bool(g.violation) and "#8" in g.violation, True)
    time.sleep(0.3)
    check("the browser's group is killed", (alive(b.pid), alive(kid) if kid else False), (False, False))
    check("then the stream is cut, and nothing else is changed", changing(calls()), ["kill-sink-input 8"])
    b.wait(5)

    b = browser_stand_in(prof)
    time.sleep(0.5)
    world(**{**json.load(open(WORLD)), "sink-inputs": [stream(9, 63, pid=None, name="Chromium")]})
    g = run_guard(sink, b.pid, prof, secs=0.6)
    check("a Chromium stream with no process to trace counts as the browser's",
          (bool(g.violation), changing(calls())), (True, ["kill-sink-input 9"]))
    time.sleep(0.3)
    check("and the browser is killed for it", alive(b.pid), False)
    b.wait(5)
    world(**{**json.load(open(WORLD)), "sink-inputs": [stream(10, 63, pid=None, name="Chromium")]})
    g = run_guard(sink, b.pid, prof, before=[10])
    check("but not one that was there before the browser was", g.violation, None)

    head("loading the null sink")
    world()
    s = R.NullSink("unix:/nowhere")
    s.load()
    check("it loads module-null-sink with its name", changing(calls()),
          [f"load-module module-null-sink sink_name={R.SINK}"])
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
          ("in use" in got, [c for c in changing(calls()) if "unload" in c]), (True, []))
    world(sinks=[dict(REAL), busy], sources=[{"index": 500, "name": R.SINK + ".monitor"}],
          **{"source-outputs": [{"index": 4, "source": 500}]})
    try:
        R.NullSink("unix:/nowhere").load()
        got = "loaded"
    except R.Refused as e:
        got = str(e)
    check("nor one that something is recording from",
          ("in use" in got, [c for c in changing(calls()) if "unload" in c]), (True, []))
    world(sinks=[dict(REAL), busy], sources=[{"index": 500, "name": R.SINK + ".monitor"}])
    R.NullSink("unix:/nowhere").load()
    check("an idle leftover is unloaded, then the sink loaded",
          changing(calls()), ["unload-module 55", f"load-module module-null-sink sink_name={R.SINK}"])

    world(load_moves_default=True)
    s = R.NullSink("unix:/nowhere")
    try:
        s.load()
        got = None
    except RuntimeError as e:
        got = str(e)
    check("loading fails if it moved the default sink", got is not None and "moved" in got, True)
    s.unload()
    check("and the sink it loaded is unloaded", changing(calls())[-1], "unload-module 77")

    head("unloading")
    world()
    s = R.NullSink("unix:/nowhere")
    s.load()
    w = json.load(open(WORLD))
    w["sink-inputs"] = [stream(11, 900, pid=1234)]
    json.dump(w, open(WORLD, "w"))
    open(CALLS, "w").close()
    t = time.monotonic()
    problems = s.unload()
    check("a stream still on the sink is cut before the sink goes",
          changing(calls()), ["kill-sink-input 11", "unload-module 77"])
    check("and it says so", any("still on" in p for p in problems), True)
    check("after waiting for it to leave by itself first", time.monotonic() - t >= 7.5, True)

    head("one recording at a time")
    lock = os.path.join(tempfile.gettempdir(), f"omacar-demo-rec-{os.getuid()}.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time; f = open(sys.argv[1], 'w'); "
         "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)", lock],
        stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    world()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "demo_record_headless.py"),
                        "--probe", "--work", SCRATCH], capture_output=True, text=True, timeout=30,
                       # No sound server to find, whatever the lock does.
                       env=dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"],
                                XDG_RUNTIME_DIR=SCRATCH))
    holder.kill()
    holder.wait()
    check("a second recording will not start while one holds the lock",
          (r.returncode, "another recording is running" in r.stdout, calls()), (2, True, []))
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
