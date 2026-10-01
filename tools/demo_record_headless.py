#!/usr/bin/env python3
"""The backup video, recorded on the box, with the tour's sound, and nothing
anybody can hear (doc/design/2026-09-30-meetup-demo.md §6: "The backup video
plays full-screen with mpv from the menu").

    python3 tools/demo_record_headless.py                  ~/Videos/omacar-demo-backup.mp4
    python3 tools/demo_record_headless.py --probe          prove the sound's route with silence, and stop

The box has no wf-recorder, and the demo runs headless here, so the picture is
Chromium's own (DevTools' Page.startScreencast, each frame with its time) and
the sound is a PipeWire null sink's monitor:

  1. `pactl load-module module-null-sink sink_name=omacar-demo-rec`: a sink
     that outputs to nothing, and that WirePlumber neither restores nor saves
     (state.restore-props=false). The default sink, its volume and its mute
     are read before and after, and must not move. The module is unloaded at
     the end, however this ends, but ONLY once nothing of the browser's is
     left: a sink removed from under a playing stream sends that stream to the
     default one. If something is left, the sink stays loaded (it is silent),
     and this says so, with its module, and fails.
  2. The demo, in a scratch HOME, muted, as tools/demo_e2e.py runs it.
  3. The one unmuted Chromium: headless, 1920x1080, with PULSE_SINK naming the
     null sink and no other way to a sound server (see demo_e2e.Browser). Its
     streams are named omacar-demo-rec, not Chromium, and marked for
     WirePlumber never to restore or save them, never to fall back to another
     sink and never to be moved (STREAM_PROPS), so they can neither take nor
     leave a volume on the owner's own Chromium. Its route is PROVEN before
     anything plays: a silent AudioContext must show up on the null sink. And
     a guard watches every stream of this browser's for as long as it lives;
     one anywhere else is cut (pactl kill-sink-input) and the browser killed.
     A guard that cannot see three times running, or that stops, stops the
     run the same way.
  4. The tour, from the top; frames by screencast, and sound by `ffmpeg -f
     pulse` from omacar-demo-rec.monitor, on the same clock.
  5. Frames at their real times and the sound, muxed: H.264 and AAC at a
     constant 30 fps, written beside and renamed into place.
  6. Everything taken down; then the length, the size, five stills and
     ffmpeg's volumedetect, for the whole film and step by step.

INT, TERM and HUP stop the run; the first one counts, and from then on, as
from the moment the teardown starts, all three are ignored, so a second Ctrl+C
or a dropped ssh cannot cut the teardown short. pactl, ffmpeg and the browser
run in sessions of their own, where a terminal's Ctrl+C cannot reach them.
"""

import argparse
import base64
import fcntl
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import demo_e2e as e2e  # noqa: E402

SINK = "omacar-demo-rec"
# The null sink's own node: WirePlumber would otherwise remember its volume
# under its name, in the owner's ~/.local/state/wireplumber/stream-properties.
SINK_PROPS = "state.restore-props=false"
# The recording browser's streams, as PipeWire sees them (libpulse's
# PULSE_PROP_OVERRIDE, which wins over the name Chromium gives itself):
#   a name of their own, so WirePlumber's restore can never key on "Chromium",
#     the owner's browser;
#   state.restore-props/-target false: neither restored nor saved;
#   node.dont-fallback, dont-reconnect and dont-move: if omacar-demo-rec is not
#     there, or goes, the stream waits unlinked instead of going to the
#     default sink.
STREAM_PROPS = (f"application.name={SINK} application.id={SINK} "
                "state.restore-props=false state.restore-target=false "
                "node.dont-fallback=true node.dont-reconnect=true node.dont-move=true")
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
SIZE = (1920, 1080)
FPS = 30
# The film starts this long before step 1 opens, and runs this long after the
# last step ends (the tour stays on Home).
LEAD, TAIL = 0.5, 2.0
# Five stills to look at: (step id, seconds into it, name).
STILLS = (("home", 30, "home-radio"), ("navigation", 20, "navigation"),
          ("drowsy", 12, "drowsy"), ("agent", 36, "agent-applied"),
          ("carplay", 16, "carplay-maps"))


def say(*a):
    """print, to a terminal that may have gone. An ssh that drops takes the
    recorder's stdout with it, and a print then raises (BrokenPipeError, EIO):
    one of those in the teardown would skip the unload that follows it. So a
    write that fails is dropped, and after the first, everything goes to
    /dev/null. Every message here goes through this."""
    try:
        print(*a, flush=True)
    except (OSError, ValueError):
        # The descriptor itself onto /dev/null: what is still buffered, and
        # anything started from here that inherits it, then write nowhere
        # instead of failing again.
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, sys.stdout.fileno())
            os.close(null)
        except (OSError, ValueError):
            try:
                sys.stdout = open(os.devnull, "w")
            except OSError:
                pass


class Refused(Exception):
    """A precondition that is not there: said, and nothing is played."""


class Proven(Exception):
    """--probe's end: the route is proven, and everything comes down."""


class Stop(BaseException):
    """INT, TERM or HUP: the run stops here, and the teardown runs."""


class GuardDown(RuntimeError):
    """The guard found a stream where it must not be, or stopped watching."""


class Signals:
    """The first INT, TERM or HUP stops the run (Stop); every one after it,
    and every one once the teardown has started, is ignored.

    `tearing` is set by the teardown's FIRST STATEMENT, an attribute store:
    CPython runs a signal handler only at a call or a backward jump, so none
    can land between the start of the `finally` and that store."""

    def __init__(self):
        self.got = None
        self.tearing = False

    def arm(self):
        for s in SIGNALS:
            signal.signal(s, self._on)

    def quiet(self):
        for s in SIGNALS:
            signal.signal(s, signal.SIG_IGN)

    def _on(self, n, _frame):
        self.quiet()
        if self.got is None:
            self.got = n
        if not self.tearing:
            raise Stop(n)


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        raise Refused("missing here: " + ", ".join(missing))


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def lock_path():
    """One per user, in the user's runtime folder: the same file whatever
    TMPDIR says. OMACAR_REC_LOCK is for the tests."""
    return os.environ.get("OMACAR_REC_LOCK") or os.path.join(runtime_dir(), f"{SINK}.lock")


def pulse_server():
    """This user's PipeWire-Pulse socket. Over ssh there is no XDG_RUNTIME_DIR,
    so it is found where logind puts it."""
    sock = os.path.join(runtime_dir(), "pulse", "native")
    if not os.path.exists(sock):
        raise Refused(f"no sound server at {sock}")
    return "unix:" + sock


# ============================================================ the null sink
class NullSink:
    """omacar-demo-rec, and what it must never change."""

    def __init__(self, server):
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PULSE_")}
        self.env["PULSE_SERVER"] = server
        self.module = None
        self.index = None
        self.was = None

    def pactl(self, *args, timeout=10):
        # A session of its own: a terminal's Ctrl+C reaches this process only.
        r = subprocess.run(["pactl", *args], env=self.env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, start_new_session=True)
        if r.returncode != 0:
            raise RuntimeError(f"pactl {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def sinks(self):
        return json.loads(self.pactl("-f", "json", "list", "sinks"))

    def inputs(self, timeout=10):
        return json.loads(self.pactl("-f", "json", "list", "sink-inputs", timeout=timeout))

    def state(self):
        """The default sink, and its volume and mute: read only."""
        name = self.pactl("get-default-sink").strip()
        return {"default": name,
                "volume": self.pactl("get-sink-volume", name).strip(),
                "mute": self.pactl("get-sink-mute", name).strip()}

    def load(self):
        self.was = self.state()
        # A sink of this name left by a run that was killed outright: only
        # this tool makes one, so its module is this tool's to unload. BUT
        # ONLY IF NOTHING IS USING IT. A sink unloaded from under a playing
        # stream sends that stream to the default sink, out loud.
        for s in self.sinks():
            if s["name"] == SINK:
                playing = [i for i in self.inputs() if i.get("sink") == s["index"]]
                monitor = {m["index"] for m in json.loads(self.pactl("-f", "json", "list", "sources"))
                           if m.get("name") == s.get("monitor_source")}
                listening = [o for o in json.loads(self.pactl("-f", "json", "list", "source-outputs"))
                             if o.get("source") in monitor]
                if playing or listening:
                    raise Refused(f"{SINK} is in use ({len(playing)} stream(s) in, "
                                  f"{len(listening)} recording): another recording is running")
                say(f"  a {SINK} left by an earlier run (module {s['owner_module']}): unloaded")
                self.pactl("unload-module", str(s["owner_module"]))
        out = self.pactl("load-module", "module-null-sink", f"sink_name={SINK}",
                         f"sink_properties={SINK_PROPS}")
        self.module = int(out.strip())
        for s in self.sinks():
            if s["name"] == SINK:
                self.index = s["index"]
        if self.index is None:
            raise RuntimeError(f"{SINK} did not appear after loading module {self.module}")
        moved = self.moved()
        if moved:
            raise RuntimeError("loading the null sink moved " + "; ".join(moved))
        say(f"  {SINK}: module {self.module}, sink #{self.index}; "
            f"the default is still {self.was['default']}, {self.was['volume'].splitlines()[0].strip()}")

    def moved(self):
        now = self.state()
        return [f"{k}: {self.was[k]!r} -> {now[k]!r}" for k in self.was if now[k] != self.was[k]]

    def on_sink(self):
        return [i for i in self.inputs() if i.get("sink") == self.index]

    def unload(self, prof=None, wait=8.0):
        """Gone, but ONLY when nothing can play into it any more: no process of
        the recording browser's (`prof`, found in /proc) and no stream on the
        sink. A sink removed from under a stream sends it to the default sink,
        out loud; a null sink left loaded outputs nothing. So when something
        is left after `wait` seconds, the sink stays, and the problem says so,
        with the command for later. A listing that fails is tried again until
        the deadline; if the last one failed too, and the browser is gone, the
        unload is still tried, and that is said."""
        if self.module is None:
            return []
        problems = []
        end = time.monotonic() + wait
        while True:
            alive = e2e.mentions(prof) if prof else []
            try:
                streams, listed = self.on_sink(), None
            except Exception as e:                              # noqa: BLE001
                streams, listed = None, e                       # unknown: looked at again
            if (not alive and streams == []) or time.monotonic() >= end:
                break
            time.sleep(0.25)
        streams = streams or []
        if alive or streams:
            what = [f"browser process {p}" for p, _ in alive] + \
                   [f"stream #{i['index']} ({i.get('properties', {}).get('application.name')})"
                    for i in streams]
            problems.append(f"{SINK} LEFT LOADED (module {self.module}): {'; '.join(what)} "
                            f"still there. It outputs nothing, so it is silent. Once they "
                            f"are gone: pactl unload-module {self.module}")
            say(f"  !!! {problems[-1]}")
            return problems
        if listed is not None:
            problems.append(f"the streams on {SINK} could not be listed ({listed!r}); "
                            f"the browser is gone, so it was unloaded anyway")
        try:
            self.pactl("unload-module", str(self.module))
        except Exception as e:                                  # noqa: BLE001
            problems.append(f"UNLOAD FAILED: pactl unload-module {self.module} ({e!r})")
            say(f"  !!! {problems[-1]}")
            return problems
        # The state first, then the words: nothing said can undo an unload.
        module, self.module = self.module, None
        say(f"  {SINK} unloaded (module {module})")
        try:
            if any(s["name"] == SINK for s in self.sinks()):
                problems.append(f"{SINK} is still there after unloading")
        except Exception:                                       # noqa: BLE001
            pass
        return problems


# ============================================================ the guard
def session_of(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            rest = f.read().rsplit(")", 1)[1].split()
        return int(rest[1]), int(rest[3]), int(rest[2])      # ppid, session, pgrp
    except (OSError, IndexError, ValueError):
        return None


def descends(pid, root):
    """Whether `pid` is `root`'s, by session, group or parentage."""
    seen = 0
    p = pid
    while p and p > 1 and seen < 64:
        if p == root:
            return True
        st = session_of(p)
        if not st:
            return False
        ppid, sid, pgrp = st
        if sid == root or pgrp == root:
            return True
        p, seen = ppid, seen + 1
    return False


def kill_browser(prof, browser=None, wait=5.0):
    """The recording browser, gone: its pipe and group (js_test's close), then
    every process that still carries its profile, killed and waited for. What
    is left after `wait` seconds is returned. A Chromium audio process that
    outlived the null sink would reconnect to the default one."""
    if browser:
        browser.close()
    end = time.monotonic() + wait
    while True:
        left = e2e.mentions(prof)
        if not left or time.monotonic() > end:
            return left
        for pid, _ in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.1)


# A stream that exists and is not linked to any sink yet: PipeWire-Pulse gives
# its sink as SPA_ID_INVALID. It plays nowhere, and is looked at again.
UNLINKED = (None, -1, 0xFFFFFFFF)
# BLIND IS NOT SAFE. The guard stops the run after BLIND failed looks in a
# row, or at the first failed look more than BLIND_SECS after its last good
# one; and a look that hangs is a failed look after GUARD_TIMEOUT seconds. So
# it is never blind for more than about two seconds.
BLIND = 3
BLIND_SECS = 1.0
GUARD_TIMEOUT = 2.0


def serial(i):
    """A stream's identity: PipeWire's object.serial, which is never reused,
    where an index can be."""
    return str(i.get("properties", {}).get("object.serial") or f"index:{i.get('index')}")


class Guard(threading.Thread):
    """Every tenth of a second: each stream of this browser's that is linked
    to a sink must be linked to the null sink. One that is linked anywhere else
    is cut, and the browser killed. `ours` holds the streams seen on the null
    sink. A new stream that cannot be traced to a process counts as this
    browser's if it says it is Chromium or omacar-demo-rec. `before` holds the
    object serials of the streams that were there before the browser.

    IT FAILS CLOSED. A look that fails (pactl, its JSON, anything) is counted,
    and BLIND of them in a row stop the run: the browser is killed, since a
    guard that cannot see is not guarding. Anything else that would end the
    thread does the same. The main thread asks `check()` between its steps,
    which raises GuardDown once the guard has found a fault or has stopped."""

    def __init__(self, sink, root, prof, before):
        super().__init__(daemon=True)
        self.sink, self.root, self.prof, self.before = sink, root, prof, set(before)
        self.ours = set()
        self.seen = {}
        self.violation = None
        self.stopping = False
        self.errors = 0

    def run(self):
        blind = 0
        last_good = time.monotonic()
        try:
            while not self.stopping and not self.violation:
                try:
                    self.look()
                    blind, last_good = 0, time.monotonic()
                except Exception as e:                          # noqa: BLE001
                    blind += 1
                    self.errors += 1
                    dark = time.monotonic() - last_good
                    if blind >= BLIND or dark > BLIND_SECS:
                        self.violation = (f"the guard could not see the streams ({blind} failed "
                                          f"look(s) in a row, {dark:.1f} s without a good one: "
                                          f"{e!r}), so the recording browser was stopped")
                        self.cut(None)
                        break
                time.sleep(0.1)
        except BaseException as e:                              # noqa: BLE001
            if not self.violation:
                self.violation = f"the guard stopped ({e!r}), so the recording browser was stopped"
            self.cut(None)

    def look(self):
        for i in self.sink.inputs(timeout=GUARD_TIMEOUT):
            if serial(i) in self.before:
                continue
            props = i.get("properties", {})
            pid = props.get("application.process.id")
            name = str(props.get("application.name", ""))
            mine = descends(int(pid), self.root) if str(pid or "").isdigit() else \
                ("chrom" in name.lower() or name == SINK)
            if not mine or i.get("sink") in UNLINKED:
                continue
            if i.get("sink") == self.sink.index:
                self.ours.add(i["index"])
                self.seen[i["index"]] = i
            else:
                self.violation = (f"stream #{i['index']} of the recording browser "
                                  f"({name}, pid {pid}) went to sink #{i.get('sink')}, "
                                  f"not {SINK} (#{self.sink.index})")
                self.cut(i["index"])
                return

    def cut(self, index):
        """The browser killed first, a signal and no process started, so it
        cannot open another stream; then the stream itself, in case it
        lingers."""
        try:
            os.killpg(self.root, signal.SIGKILL)
        except OSError:
            pass
        try:
            kill_browser(self.prof, wait=1.0)
        except Exception:                                       # noqa: BLE001
            pass
        if index is not None:
            try:
                self.sink.pactl("kill-sink-input", str(index))
            except Exception:                                   # noqa: BLE001
                pass

    def check(self):
        """For the main thread: a guard that has found a fault, or that is no
        longer watching, stops the run (the browser is already killed, or is
        killed here, before anything else is taken down)."""
        if self.violation:
            raise GuardDown(self.violation)
        if not self.is_alive() and not self.stopping:
            self.violation = "the guard stopped watching, so the recording browser was stopped"
            self.cut(None)
            raise GuardDown(self.violation)

    def stop(self):
        self.stopping = True
        self.join(3)


SILENCE_JS = """(async () => {
  const c = new AudioContext();
  const g = c.createGain(); g.gain.value = 0;
  const o = c.createOscillator(); o.connect(g); g.connect(c.destination); o.start();
  await c.resume();
  globalThis.__recSilence = c;
  return c.state;
})()"""


def prove_route(cdp, guard, timeout=12):
    """A silent AudioContext in the page, which must show up linked to the
    null sink and to nothing else, before anything in the tour can play.
    Returns what PipeWire says of those streams: their names, their flags and
    their volume."""
    state = cdp.value(SILENCE_JS, wait=True, timeout=15)
    end = time.monotonic() + timeout
    while time.monotonic() < end and not guard.ours:
        guard.check()
        time.sleep(0.1)
    time.sleep(0.5)
    guard.check()
    if not guard.ours:
        raise Refused(f"the page's audio (AudioContext {state}) never reached PipeWire, "
                      f"so its route cannot be proven; nothing was played")
    cdp.value("globalThis.__recSilence && globalThis.__recSilence.close().then(() => true)",
              wait=True)
    keys = ("application.name", "application.id", "state.restore-props", "state.restore-target",
            "node.dont-fallback", "node.dont-reconnect", "node.dont-move", "object.serial")
    said = []
    for index in sorted(guard.ours):
        i = guard.seen.get(index, {})
        props = i.get("properties", {})
        vol = [v.get("value_percent") for v in (i.get("volume") or {}).values()]
        said.append({"index": index, "volume": vol,
                     **{k: props.get(k) for k in keys}})
    return said


# ============================================================ recording
class Frames:
    """Page.screencastFrame, written as it comes, each with its own time."""

    def __init__(self, cdp, folder):
        self.cdp, self.folder = cdp, folder
        self.list = []
        self.lock = threading.Lock()
        os.makedirs(folder, exist_ok=True)

    def __call__(self, msg):
        if msg.get("method") != "Page.screencastFrame":
            return
        p = msg["params"]
        self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
        ts = (p.get("metadata") or {}).get("timestamp") or time.time()
        with self.lock:
            name = f"{len(self.list):06d}.jpg"
            self.list.append((float(ts), name))
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(base64.b64decode(p["data"]))


def start_audio(sink, path):
    """ffmpeg on the null sink's monitor. Its timestamps are the wall clock
    (the pulse input's `wallclock`, kept by -copyts), so the film can line the
    sound up with frames stamped by the same clock."""
    return subprocess.Popen(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "pulse", "-wallclock", "1", "-sample_rate", "48000", "-channels", "2",
         "-i", SINK + ".monitor", "-copyts", "-c:a", "flac", "-f", "matroska", path],
        env=sink.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, start_new_session=True)


def stop_proc(p, sig=signal.SIGINT, wait=15):
    if p is None or p.poll() is not None:
        return
    try:
        p.send_signal(sig)
        p.wait(wait)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(5)


def probe_json(path, *entries):
    # Sections are separated by ':' (format=a,b:stream=c,d).
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", ":".join(entries),
                        "-of", "json", path], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "{}")


def mux(frames, v0, v1, audio, a0, out, work):
    """The frames between v0 and v1 (epoch seconds) at their own times, as a
    constant 30 fps, with the sound from `audio` (which starts at a0)."""
    sel = [f for f in frames if v0 <= f[0] <= v1]
    before = [f for f in frames if f[0] < v0]
    if before:
        sel.insert(0, (v0, before[-1][1]))
    if len(sel) < 2:
        raise RuntimeError(f"only {len(sel)} frames between the tour's start and end")
    lst = os.path.join(work, "frames.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for i, (ts, name) in enumerate(sel):
            nxt = sel[i + 1][0] if i + 1 < len(sel) else v1
            f.write(f"file 'frames/{name}'\nduration {max(0.001, nxt - ts):.6f}\n")
        f.write(f"file 'frames/{sel[-1][1]}'\n")
    offset = v0 - a0
    if offset < 0:
        raise RuntimeError(f"the sound started {-offset:.2f} s after the film does")
    tmp = out + ".part.mp4"
    try:
        _mux(lst, offset, audio, v1 - v0, tmp, work)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(sel)


def _mux(lst, offset, audio, secs, tmp, work):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", lst,
         "-ss", f"{offset:.3f}", "-i", audio,
         "-map", "0:v:0", "-map", "1:a:0",
         "-vf", f"fps={FPS},scale=out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-g", str(FPS * 2), "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-t", f"{secs:.3f}", "-movflags", "+faststart", tmp],
        check=True, timeout=1800, cwd=work)


VOL = re.compile(r"(mean|max)_volume: (-?[\d.]+|-inf) dB")


def volume(path, start=None, secs=None):
    cmd = ["ffmpeg", "-nostdin", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", f"{start:.2f}", "-t", f"{secs:.2f}"]
    cmd += ["-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    got = dict(VOL.findall(r.stderr))
    return {k: (float(v) if v != "-inf" else float("-inf")) for k, v in got.items()}


def still(path, at, dest):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1", dest],
                   check=True, timeout=60)
    return dest


# ============================================================ the run
class Run:
    """What a recording has up, for take_down to bring down."""

    def __init__(self, a, work):
        self.a, self.work = a, work
        self.prof = os.path.join(work, "browser")
        self.problems = []
        self.report = {"out": a.out, "problems": self.problems}
        self.sink = self.sc = self.browser = self.cdp = self.guard = self.ff = self.frames = None
        self.before = self.steps = None
        self.entries = []
        self.v0 = self.v1 = self.a0 = None


def take_down(r):
    """Everything down, in the one order that is safe, each step on its own so
    that none can skip the next:
      1. the browser: its DevTools reader, its pipe and group, then every
         process that carries its profile, killed and waited for;
      2. ffmpeg, which records the sink's monitor (it would move to the
         microphone if the sink went first);
      3. the guard;
      4. the sink, unloaded only if nothing of the browser's is left
         (NullSink.unload), and the default sink's state checked;
      5. the demo, which plays nothing: `demo off`, and its silo."""
    p = r.problems
    try:
        if r.cdp:
            r.cdp.stop()
    except Exception as e:                                      # noqa: BLE001
        p.append(f"stopping the DevTools reader: {e!r}")
    try:
        left = kill_browser(r.prof, r.browser)
        if left:
            p.append("the recording browser outlived SIGKILL: "
                     + "; ".join(f"{pid} {c}" for pid, c in left))
    except Exception as e:                                      # noqa: BLE001
        p.append(f"taking the browser down: {e!r}")
    try:
        stop_proc(r.ff)
    except Exception as e:                                      # noqa: BLE001
        p.append(f"stopping ffmpeg: {e!r}")
    try:
        if r.guard:
            r.guard.stop()
            if r.guard.violation and r.guard.violation not in p:
                p.append(r.guard.violation)
    except Exception as e:                                      # noqa: BLE001
        p.append(f"stopping the guard: {e!r}")
    if r.sink:
        try:
            p += r.sink.unload(r.prof)
        except Exception as e:                                  # noqa: BLE001
            p.append(f"UNLOAD FAILED: pactl unload-module {r.sink.module} ({e!r})")
        try:
            moved = r.sink.moved() if r.sink.was else []
            if moved:
                p.append("the default sink or its volume moved: " + "; ".join(moved))
            elif r.sink.was:
                say(f"  the default sink, its volume and mute: as they were ({r.sink.was['default']})")
        except Exception as e:                                  # noqa: BLE001
            p.append(f"reading the default sink back: {e!r}")
    if r.sc:
        try:
            p += e2e.teardown(r.sc, r.report)
            if r.before is not None:
                r.report["silo"] = e2e.diff(r.before, r.sc.outside())
                if r.report["silo"]:
                    p.append("files outside the demo's folder changed: " + "; ".join(r.report["silo"]))
            calls = r.sc.shim_calls()
            if calls:
                p.append("shims were called: " + "; ".join(calls))
        except Exception as e:                                  # noqa: BLE001
            p.append(f"taking the demo down: {e!r} (scratch kept: {r.sc.base})")
        else:
            if not r.a.keep:
                r.sc.remove()


def guarded(sigs, body, r):
    """body(r), and then take_down(r), which nothing can stop: not an error in
    body, not the signal that ended it, and not a second signal during the
    teardown."""
    try:
        body(r)
    except Proven:
        pass
    except Stop as e:
        r.problems.append(f"stopped by signal {signal.Signals(e.args[0]).name}")
    except Refused as e:
        r.problems.append(f"refused: {e}")
    except Exception as e:                                      # noqa: BLE001
        r.problems.append(f"stopped: {e!r}")
        if r.browser:
            said = e2e.js_test.tail(r.browser.log, 8)
            if said:
                r.problems.append("Chromium said: " + " | ".join(said))
    finally:
        # FIRST, an attribute store and no call: see Signals.
        sigs.tearing = True
        sigs.quiet()
        say("  taking everything down; Ctrl+C, TERM and HUP are ignored until it is done")
        take_down(r)
    if r.sink and r.sink.module is not None:
        say(f"\n  !!! {SINK} IS STILL LOADED (module {r.sink.module}). It is silent. "
            f"When nothing plays into it: pactl unload-module {r.sink.module}\n")


def record(r):
    """The run itself: the sink, the demo, the browser, the proof, the tour."""
    a = r.a
    need("pactl", "ffmpeg", "ffprobe")
    exe = e2e.js_test.browser()
    if not exe:
        raise Refused("missing here: chromium")
    server = pulse_server()
    r.sink = NullSink(server)
    say(f"\n  the sound goes to a null sink on {server}")
    r.sink.load()
    existing = [serial(i) for i in r.sink.inputs()]

    if a.probe:
        url = "about:blank"
    else:
        r.sc = e2e.Scratch(a.work, a.clips, a.roadcams, a.roadcams_pins)
        url = r.sc.url
        r.before = r.sc.outside()
        rc, said = r.sc.demo("on")
        r.report["on"] = said.strip().splitlines()
        if rc != 0 or not e2e.wait_page(url):
            raise Refused(f"demo on did not bring up {url} (exit {rc}):\n{said}")
        say(f"  the demo, muted, in {r.sc.home}: {url}")

    r.browser = e2e.Browser(exe, r.prof, SIZE, mute=False,
                            env={"PULSE_SERVER": server, "PULSE_SINK": SINK,
                                 "PULSE_PROP": STREAM_PROPS, "PULSE_PROP_OVERRIDE": STREAM_PROPS})
    # Watching before the page exists: its first stream is checked from its
    # first tenth of a second.
    r.guard = Guard(r.sink, r.browser.proc.pid, r.prof, existing)
    r.guard.start()
    r.cdp = e2e.Cdp(r.browser)
    watch = e2e.Watch()
    if a.probe:
        r.cdp.attach()
        r.cdp.call("Runtime.enable")
    else:
        r.steps = e2e.open_demo(r.cdp, url, SIZE, watch)
    streams = prove_route(r.cdp, r.guard)
    r.report["streams_proven"] = streams
    say(f"  proven with silence: the browser's streams are on {SINK}, and nothing else of it:")
    for st in streams:
        say(f"    {st}")
    if a.probe:
        raise Proven()

    audio = os.path.join(r.work, "audio.mkv")
    r.ff = start_audio(r.sink, audio)
    r.frames = Frames(r.cdp, os.path.join(r.work, "frames"))
    r.cdp.handlers.append(r.frames)
    r.cdp.call("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": SIZE[0],
                                        "maxHeight": SIZE[1], "everyNthFrame": 1})
    time.sleep(1.5)
    r.guard.check()
    if r.ff.poll() is not None:
        raise RuntimeError("ffmpeg stopped recording: " + r.ff.stderr.read().decode()[-400:])
    say("  recording: the tour, from the top")
    # With no clips the tour jumps over its Cameras step (hardening D), and the video
    # is the tour without it: that is the tour this run is expected to show.
    r.entries, tour_problems = e2e.follow_tour(r.cdp, r.steps, watch, out=None,
                                               check=r.guard.check,
                                               skip=e2e.skipped_steps(r.steps, r.sc.footage))
    r.problems += tour_problems
    time.sleep(TAIL + 0.5)
    r.guard.check()
    r.cdp.call("Page.stopScreencast")
    stop_proc(r.ff)
    r.report["console"] = {"errors": watch.errors, "failed": watch.failed,
                           "warnings": watch.warnings}
    r.problems += [f"{w}: {e}" for w, e in watch.errors + watch.failed]
    if r.entries:
        r.v0 = r.entries[0][2] / 1000 - LEAD
        last = r.entries[-1]
        r.v1 = last[2] / 1000 + float(r.steps[last[0]]["secs"]) + TAIL
    r.a0 = float(probe_json(audio, "format=start_time").get("format", {}).get("start_time", "nan"))


def interruptible(fn, r, what):
    """fn(r), after the teardown, with INT, TERM and HUP working again: the
    mux, volumedetect and the stills can be stopped as anything can. The first
    one stops fn and is recorded; any after it are ignored while the run
    tidies up."""
    sigs = Signals()
    sigs.arm()
    try:
        fn(r)
    except Stop as e:
        r.problems.append(f"stopped by signal {signal.Signals(e.args[0]).name} while {what}")


def finish(r, stills_dir):
    """The film from the frames and the sound, and what is said about it."""
    a, p = r.a, r.problems
    try:
        n = mux(r.frames.list, r.v0, r.v1, os.path.join(r.work, "audio.mkv"), r.a0, a.out, r.work)
        info = probe_json(a.out, "format=duration,size",
                          "stream=codec_name,width,height,r_frame_rate,avg_frame_rate")
        fmt = info.get("format", {})
        r.report.update(frames_captured=len(r.frames.list), frames_used=n,
                        capture_fps=round(n / (r.v1 - r.v0), 1),
                        duration=float(fmt.get("duration", 0)), size=int(fmt.get("size", 0)),
                        streams=info.get("streams"), audio_offset=round(r.v0 - r.a0, 3))
        r.report["volume"] = volume(a.out)
        per = []
        for idx, sid, at_ms, _h in r.entries:
            start = at_ms / 1000 - r.v0
            per.append({"step": idx + 1, "id": sid, "start": round(start, 1),
                        **volume(a.out, start, float(r.steps[idx]["secs"]))})
        r.report["volume_by_step"] = per
        os.makedirs(stills_dir, exist_ok=True)
        r.report["stills"] = []
        by_id = {sid: at_ms / 1000 - r.v0 for _i, sid, at_ms, _h in r.entries}
        for sid, secs, name in STILLS:
            if sid in by_id:
                r.report["stills"].append(still(a.out, by_id[sid] + secs,
                                                os.path.join(stills_dir, f"{name}.png")))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as e:
        p.append(f"the film could not be made: {e!r}")


# ============================================================ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.expanduser("~/Videos/omacar-demo-backup.mp4"))
    ap.add_argument("--stills", help="where the five stills go (default: beside the work folder)")
    ap.add_argument("--work", default="/var/tmp")
    ap.add_argument("--clips", help="camera clips to hand the demo (default: test patterns)")
    ap.add_argument("--roadcams", help="a saved road-cameras cache for the fake real state")
    ap.add_argument("--roadcams-pins")
    ap.add_argument("--keep", action="store_true", help="keep the frames and the scratch HOME")
    ap.add_argument("--probe", action="store_true",
                    help="load the sink, prove the route with silence, unload, and stop")
    a = ap.parse_args(argv)
    sigs = Signals()
    sigs.arm()

    # ONE RECORDING AT A TIME. A second would find the first's sink and take
    # it for a leftover.
    try:
        lock = open(lock_path(), "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        say(f"demo_record_headless: another recording is running, or the lock "
            f"{lock_path()} cannot be taken ({e}); not starting")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    r = Run(a, tempfile.mkdtemp(prefix="omacar-demo-rec-", dir=a.work))
    stills_dir = a.stills or os.path.join(a.work, f"omacar-demo-backup-stills-{stamp}")
    guarded(sigs, record, r)
    report, problems = r.report, r.problems

    if not a.probe and r.v0 is not None and r.frames and r.frames.list and not math.isnan(r.a0):
        interruptible(lambda run: finish(run, stills_dir), r, "making the film")
    elif not a.probe and not problems:
        problems.append("nothing was recorded")

    if a.keep:
        report["work"] = r.work
    else:
        shutil.rmtree(r.work, ignore_errors=True)
    if not a.probe:
        os.makedirs(stills_dir, exist_ok=True)
        with open(os.path.join(stills_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    if "duration" in report:
        say(f"\n  {a.out}\n  {report['duration']:.1f} s, {report['size'] / 1e6:.1f} MB, "
            f"{report['frames_used']} frames at {report['capture_fps']} captured per second")
        say(f"  volume: mean {report['volume'].get('mean')} dB, max {report['volume'].get('max')} dB")
        for st in report["volume_by_step"]:
            say(f"    step {st['step']:2d} {st['id']:<12} from {st['start']:6.1f} s"
                f"  mean {st.get('mean')} dB  max {st.get('max')} dB")
        say(f"  stills and report.json in {stills_dir}")
    for p in problems:
        say(f"  FAIL  {p}")
    say("\n  " + ("FAILED" if problems else "done") + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
