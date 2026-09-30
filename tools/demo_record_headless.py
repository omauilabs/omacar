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
     that outputs to nothing. The default sink, its volume and its mute are
     read before and after, and must not move. The module is ALWAYS unloaded
     at the end, however this ends, and only after the browser is gone: a sink
     removed from under a playing stream sends that stream to the default one.
  2. The demo, in a scratch HOME, muted, as tools/demo_e2e.py runs it.
  3. The one unmuted Chromium: headless, 1920x1080, with PULSE_SINK naming the
     null sink and no other way to a sound server (see demo_e2e.Browser). Its
     route is PROVEN before anything plays: a silent AudioContext must show up
     on the null sink. And a guard watches every stream of this browser's for
     as long as it lives; one anywhere else is cut (pactl kill-sink-input) and
     the browser killed.
  4. The tour, from the top; frames by screencast, and sound by `ffmpeg -f
     pulse` from omacar-demo-rec.monitor, on the same clock.
  5. Frames at their real times and the sound, muxed: H.264 and AAC at a
     constant 30 fps, written beside and renamed into place.
  6. Everything taken down; then the length, the size, five stills and
     ffmpeg's volumedetect, for the whole film and step by step.
"""

import argparse
import base64
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
SIZE = (1920, 1080)
FPS = 30
# The film starts this long before step 1 opens, and runs this long after the
# last step ends (the tour stays on Home).
LEAD, TAIL = 0.5, 2.0
# Five stills to look at: (step id, seconds into it, name).
STILLS = (("home", 30, "home-radio"), ("navigation", 20, "navigation"),
          ("drowsy", 12, "drowsy"), ("agent", 36, "agent-applied"),
          ("carplay", 16, "carplay-maps"))

log = e2e.log


class Refused(Exception):
    """A precondition that is not there: said, and nothing is played."""


class Proven(Exception):
    """--probe's end: the route is proven, and everything comes down."""


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        raise Refused("missing here: " + ", ".join(missing))


def pulse_server():
    """This user's PipeWire-Pulse socket. Over ssh there is no XDG_RUNTIME_DIR,
    so it is found where logind puts it."""
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    sock = os.path.join(run, "pulse", "native")
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
        r = subprocess.run(["pactl", *args], env=self.env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"pactl {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def sinks(self):
        return json.loads(self.pactl("-f", "json", "list", "sinks"))

    def inputs(self):
        return json.loads(self.pactl("-f", "json", "list", "sink-inputs"))

    def state(self):
        """The default sink, and its volume and mute: read only."""
        name = self.pactl("get-default-sink").strip()
        return {"default": name,
                "volume": self.pactl("get-sink-volume", name).strip(),
                "mute": self.pactl("get-sink-mute", name).strip()}

    def load(self):
        self.was = self.state()
        # A sink of this name left by a run that was killed outright: only
        # this tool makes one, so its module is this tool's to unload.
        for s in self.sinks():
            if s["name"] == SINK:
                log(f"  a {SINK} left by an earlier run (module {s['owner_module']}): unloaded")
                self.pactl("unload-module", str(s["owner_module"]))
        out = self.pactl("load-module", "module-null-sink", f"sink_name={SINK}")
        self.module = int(out.strip())
        for s in self.sinks():
            if s["name"] == SINK:
                self.index = s["index"]
        if self.index is None:
            raise RuntimeError(f"{SINK} did not appear after loading module {self.module}")
        moved = self.moved()
        if moved:
            raise RuntimeError("loading the null sink moved " + "; ".join(moved))
        log(f"  {SINK}: module {self.module}, sink #{self.index}; "
            f"the default is still {self.was['default']}, {self.was['volume'].splitlines()[0].strip()}")

    def moved(self):
        now = self.state()
        return [f"{k}: {self.was[k]!r} -> {now[k]!r}" for k in self.was if now[k] != self.was[k]]

    def on_sink(self):
        return [i for i in self.inputs() if i.get("sink") == self.index]

    def unload(self):
        """Nothing left playing into it, then gone. kill-sink-input ends a
        stream without touching any volume WirePlumber would remember."""
        if self.module is None:
            return []
        problems = []
        end = time.monotonic() + 8
        while self.on_sink() and time.monotonic() < end:
            time.sleep(0.25)
        for i in self.on_sink():
            problems.append(f"stream #{i['index']} ({i['properties'].get('application.name')}) "
                            f"was still on {SINK}: cut")
            self.pactl("kill-sink-input", str(i["index"]))
        self.pactl("unload-module", str(self.module))
        log(f"  {SINK} unloaded (module {self.module})")
        self.module = None
        if any(s["name"] == SINK for s in self.sinks()):
            problems.append(f"{SINK} is still there after unloading")
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


class Guard(threading.Thread):
    """Every tenth of a second: each stream of this browser's must be on the
    null sink. One that is not is cut, and the browser killed. A new Chromium
    stream that cannot be traced to a process counts as this browser's."""

    def __init__(self, sink, root, prof, before):
        super().__init__(daemon=True)
        self.sink, self.root, self.prof, self.before = sink, root, prof, set(before)
        self.ours = set()
        self.violation = None
        self.stopping = False
        self.errors = 0

    def run(self):
        while not self.stopping and not self.violation:
            try:
                for i in self.sink.inputs():
                    if i["index"] in self.before:
                        continue
                    props = i.get("properties", {})
                    pid = props.get("application.process.id")
                    name = str(props.get("application.name", ""))
                    mine = descends(int(pid), self.root) if str(pid or "").isdigit() else \
                        "chrom" in name.lower()
                    if not mine:
                        continue
                    self.ours.add(i["index"])
                    if i.get("sink") != self.sink.index:
                        self.violation = (f"stream #{i['index']} of the recording browser "
                                          f"({name}, pid {pid}) went to sink #{i.get('sink')}, "
                                          f"not {SINK} (#{self.sink.index})")
                        self.cut(i["index"])
                        break
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                self.errors += 1
            time.sleep(0.1)

    def cut(self, index):
        """The browser killed first, a signal and no process started, so it
        cannot open another stream; then the stream itself, in case it
        lingers."""
        try:
            os.killpg(self.root, signal.SIGKILL)
        except OSError:
            pass
        kill_browser(self.prof, wait=1.0)
        try:
            self.sink.pactl("kill-sink-input", str(index))
        except (RuntimeError, subprocess.TimeoutExpired):
            pass

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
    """A silent AudioContext in the page, which must show up on the null sink
    and nowhere else, before anything in the tour can play."""
    state = cdp.value(SILENCE_JS, wait=True, timeout=15)
    end = time.monotonic() + timeout
    while time.monotonic() < end and not guard.ours and not guard.violation:
        time.sleep(0.1)
    time.sleep(0.5)
    if guard.violation:
        raise Refused(guard.violation)
    if not guard.ours:
        raise Refused(f"the page's audio (AudioContext {state}) never reached PipeWire, "
                      f"so its route cannot be proven; nothing was played")
    cdp.value("globalThis.__recSilence && globalThis.__recSilence.close().then(() => true)",
              wait=True)
    return sorted(guard.ours)


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
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", ",".join(entries),
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
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", lst,
         "-ss", f"{offset:.3f}", "-i", audio,
         "-map", "0:v:0", "-map", "1:a:0",
         "-vf", f"fps={FPS},scale=out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-g", str(FPS * 2), "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-t", f"{v1 - v0:.3f}", "-movflags", "+faststart", tmp],
        check=True, timeout=1800, cwd=work)
    os.replace(tmp, out)
    return len(sel)


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
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda n, _: sys.exit(128 + n))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = tempfile.mkdtemp(prefix="omacar-demo-rec-", dir=a.work)
    stills_dir = a.stills or os.path.join(a.work, f"omacar-demo-backup-stills-{stamp}")
    report = {"out": a.out, "problems": []}
    problems = report["problems"]
    sink = sc = browser = cdp = guard = ff = frames = None
    prof = os.path.join(work, "browser")
    before = None
    entries = []
    v0 = v1 = a0 = None
    try:
        need("pactl", "ffmpeg", "ffprobe")
        exe = e2e.js_test.browser()
        if not exe:
            raise Refused("missing here: chromium")
        server = pulse_server()
        sink = NullSink(server)
        log(f"\n  the sound goes to a null sink on {server}")
        sink.load()
        existing = [i["index"] for i in sink.inputs()]

        if a.probe:
            url = "about:blank"
        else:
            sc = e2e.Scratch(a.work, a.clips, a.roadcams, a.roadcams_pins)
            url = sc.url
            before = sc.outside()
            rc, said = sc.demo("on")
            report["on"] = said.strip().splitlines()
            if rc != 0 or not e2e.wait_page(url):
                raise Refused(f"demo on did not bring up {url} (exit {rc}):\n{said}")
            log(f"  the demo, muted, in {sc.home}: {url}")

        browser = e2e.Browser(exe, prof, SIZE, mute=False,
                              env={"PULSE_SERVER": server, "PULSE_SINK": SINK})
        # Watching before the page exists: its first stream is checked from
        # its first tenth of a second.
        guard = Guard(sink, browser.proc.pid, prof, existing)
        guard.start()
        cdp = e2e.Cdp(browser)
        watch = e2e.Watch()
        if a.probe:
            cdp.attach()
            cdp.call("Runtime.enable")
        else:
            steps = e2e.open_demo(cdp, url, SIZE, watch)
        ours = prove_route(cdp, guard)
        log(f"  proven with silence: the browser's stream {ours} is on {SINK}, and nothing else of it")
        if a.probe:
            raise Proven()

        audio = os.path.join(work, "audio.mkv")
        ff = start_audio(sink, audio)
        frames = Frames(cdp, os.path.join(work, "frames"))
        cdp.handlers.append(frames)
        cdp.call("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": SIZE[0],
                                          "maxHeight": SIZE[1], "everyNthFrame": 1})
        time.sleep(1.5)
        if ff.poll() is not None:
            raise RuntimeError("ffmpeg stopped recording: " + ff.stderr.read().decode()[-400:])
        log("  recording: the tour, from the top")
        entries, tour_problems = e2e.follow_tour(cdp, steps, watch, out=None)
        problems += tour_problems
        time.sleep(TAIL + 0.5)
        cdp.call("Page.stopScreencast")
        stop_proc(ff)
        if guard.violation:
            problems.append(guard.violation)
        report["console"] = {"errors": watch.errors, "failed": watch.failed,
                             "warnings": watch.warnings}
        problems += [f"{w}: {e}" for w, e in watch.errors + watch.failed]
        if entries:
            v0 = entries[0][2] / 1000 - LEAD
            last = entries[-1]
            v1 = last[2] / 1000 + float(steps[last[0]]["secs"]) + TAIL
        a0 = float(probe_json(audio, "format=start_time").get("format", {}).get("start_time", "nan"))
    except Proven:
        pass
    except Refused as e:
        problems.append(f"refused: {e}")
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError,
            subprocess.CalledProcessError) as e:
        problems.append(f"stopped: {e!r}")
    finally:
        # THE ORDER MATTERS, and nothing may skip the last step. The browser,
        # every process of it, then the sound's recorder (on the sink's
        # monitor, which would move to the microphone), then the sink, which
        # is unloaded whatever went before. Then the demo, which plays nothing.
        try:
            if cdp:
                cdp.stop()
            if browser:
                left = kill_browser(prof, browser)
                if left:
                    problems.append("the recording browser outlived SIGKILL: "
                                    + "; ".join(f"{p} {c}" for p, c in left))
        except Exception as e:                                  # noqa: BLE001
            problems.append(f"taking the browser down: {e!r}")
        finally:
            try:
                stop_proc(ff)
            except Exception as e:                              # noqa: BLE001
                problems.append(f"stopping ffmpeg: {e!r}")
            finally:
                if guard:
                    guard.stop()
                if sink:
                    try:
                        problems += sink.unload()
                        moved = sink.moved() if sink.was else []
                        if moved:
                            problems.append("the default sink or its volume moved: "
                                            + "; ".join(moved))
                        elif sink.was:
                            log(f"  the default sink, its volume and mute: as they were "
                                f"({sink.was['default']})")
                    except Exception as e:                      # noqa: BLE001
                        problems.append(f"UNLOAD FAILED, run: pactl unload-module "
                                        f"{sink.module} ({e!r})")
        if sc:
            try:
                problems += e2e.teardown(sc, report)
                if before is not None:
                    report["silo"] = e2e.diff(before, sc.outside())
                    if report["silo"]:
                        problems.append("files outside the demo's folder changed: "
                                        + "; ".join(report["silo"]))
                calls = sc.shim_calls()
                if calls:
                    problems.append("shims were called: " + "; ".join(calls))
            except Exception as e:                              # noqa: BLE001
                problems.append(f"taking the demo down: {e!r} (scratch kept: {sc.base})")
            else:
                if not a.keep:
                    sc.remove()

    if not a.probe and v0 is not None and frames and frames.list and not math.isnan(a0):
        try:
            n = mux(frames.list, v0, v1, os.path.join(work, "audio.mkv"), a0, a.out, work)
            info = probe_json(a.out, "format=duration,size",
                              "stream=codec_name,width,height,r_frame_rate,avg_frame_rate")
            fmt = info.get("format", {})
            report.update(frames_captured=len(frames.list), frames_used=n,
                          capture_fps=round(n / (v1 - v0), 1),
                          duration=float(fmt.get("duration", 0)), size=int(fmt.get("size", 0)),
                          streams=info.get("streams"), audio_offset=round(v0 - a0, 3))
            report["volume"] = volume(a.out)
            per = []
            for idx, sid, at_ms, _h in entries:
                start = at_ms / 1000 - v0
                per.append({"step": idx + 1, "id": sid, "start": round(start, 1),
                            **volume(a.out, start, float(steps[idx]["secs"]))})
            report["volume_by_step"] = per
            os.makedirs(stills_dir, exist_ok=True)
            report["stills"] = []
            by_id = {sid: at_ms / 1000 - v0 for _i, sid, at_ms, _h in entries}
            for sid, secs, name in STILLS:
                if sid in by_id:
                    report["stills"].append(still(a.out, by_id[sid] + secs,
                                                  os.path.join(stills_dir, f"{name}.png")))
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as e:
            problems.append(f"the film could not be made: {e!r}")
    elif not a.probe and not problems:
        problems.append("nothing was recorded")

    if a.keep:
        report["work"] = work
    else:
        shutil.rmtree(work, ignore_errors=True)
    if not a.probe:
        os.makedirs(stills_dir, exist_ok=True)
        with open(os.path.join(stills_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    if "duration" in report:
        log(f"\n  {a.out}\n  {report['duration']:.1f} s, {report['size'] / 1e6:.1f} MB, "
            f"{report['frames_used']} frames at {report['capture_fps']} captured per second")
        log(f"  volume: mean {report['volume'].get('mean')} dB, max {report['volume'].get('max')} dB")
        for s in report["volume_by_step"]:
            log(f"    step {s['step']:2d} {s['id']:<12} from {s['start']:6.1f} s"
                f"  mean {s.get('mean')} dB  max {s.get('max')} dB")
        log(f"  stills and report.json in {stills_dir}")
    for p in problems:
        log(f"  FAIL  {p}")
    log("\n  " + ("FAILED" if problems else "done") + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
