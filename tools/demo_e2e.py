#!/usr/bin/env python3
"""The meetup demo, end to end: the whole tour, headless and muted, at the
tablet's two shapes and the projector's (doc/design/2026-09-30-meetup-demo.md
§7, "The box, end to end").

    python3 tools/demo_e2e.py                        a scratch HOME, all three sizes
    python3 tools/demo_e2e.py --sizes 1368x912       one size
    python3 tools/demo_e2e.py --tablet --port 7580 --sizes 1368x912 --repeat 3
                                                     a demo that is already running

WHAT IT DOES, by default:
  1. makes a scratch HOME with a fake real state in it (a parked live.json, the
     volume pin off, a clip in ~/Videos/OmaCar, the bar's rollup) and PATH shims
     that write down every call to systemctl, wpctl, pactl, hyprctl, chromium
     and mpv, and hashes every file in it that is not the demo's;
  2. runs `bin/omacar demo on` there, muted (OMACAR_DEMO_MUTE=1), on a port of
     its own, with test-pattern clips and a dead proxy: no window opens (there
     is no Wayland socket in the scratch runtime folder) and nothing reaches the
     internet;
  3. for each size, opens /demo.html in a headless, muted Chromium over
     DevTools (test/js_test.py's pipe launcher; no port is opened), starts the
     tour from the top as `omacar demo tour` does, follows it to its end on
     Home, and screenshots every step: as it opens, after each of its actions,
     and just before it ends;
  4. fails on any console error or uncaught exception, a warning from the
     tour's own failure paths (an action, the reset or the radio), any failed
     request (an HTTP status of 400 or more, or a load that failed rather than
     being cancelled; the boot film's by-design 404 aside), a tour that skips,
     stalls or pauses by itself, or one that does not end on Home;
  5. runs `demo off`, and fails if anything that mentions the scratch folder is
     still running, the port is still held, a shim was called, or any file
     outside the demo's folder changed.

--tablet skips 1, 2 and 5 and runs against a demo that is already up on
--port (the controller's run on the tablet, beside the live app). Its silo
check hashes the real trees the demo must never write (spec §2.4): what the
live app itself changes while nothing is touring, over a --baseline window
before the first tour, is reported and not counted.

Not in test/all.sh: a tour is six and a quarter minutes of real time, per size.
The screenshots show the owner's private car picture and stay on this machine.
"""

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OMACAR = os.path.join(ROOT, "bin", "omacar")
sys.path.insert(0, os.path.join(ROOT, "test"))
import js_test  # noqa: E402  (the pipe launcher, and app_test's browser finder)

SIZES = ("1368x912", "912x1368", "1920x1080")
# The screen, seconds after a step opens: past the caption's 0.28 s fade and a
# map's first draw.
OPEN_SHOT = 2.5
# After an action: the agent types its answer, the scan starts, a projection
# slides in.
ACTION_SHOT = 3.5
# THE ONE FAILED REQUEST THAT IS NOT A FAULT. The boot screen asks, with a HEAD,
# whether the owner has put an intro film in the state folder, and a 404 is the
# server's answer when there is none: "Absent is the normal case and is not an
# error" (lib/serve.py, /boot-film). The live page asks the same. Anything else
# a test expects to fail is named with --allow.
EXPECTED = ("*/boot-film",)
# THE TOUR'S OWN FAULTS ARE WARNINGS. tour.js carries on past an action that
# throws, a reset that fails or a radio that cannot load, and says so with
# console.warn, so the room never sees a stack trace. Here they fail the run.
FAULTY_WARNINGS = ("demo tour", "demo reset", "Omarchy Radio")


def log(*a):
    print(*a, flush=True)


# ============================================================ DevTools, threaded
class Cdp:
    """A DevTools session on a Chromium's --remote-debugging-pipe, with a thread
    of its own reading the pipe, so the browser is never left blocked on a full
    pipe while this waits (a screencast sends several megabytes a second).
    `call` sends and waits; `send` does not wait; every event goes to each of
    `handlers`, on the reader thread."""

    def __init__(self, browser):
        self.r, self.w = browser.page.r, browser.page.w
        self.n = 0
        self.lock = threading.Lock()
        self.pending = {}
        self.handlers = []
        self.session = None
        self.dead = None
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        buf = bytearray()
        while not self.stopping:
            try:
                if not select.select([self.r], [], [], 0.2)[0]:
                    continue
                chunk = os.read(self.r, 1 << 20)
            except (OSError, ValueError) as e:
                self.dead = f"the DevTools pipe failed: {e}"
                break
            if not chunk:
                self.dead = "Chromium closed the DevTools pipe"
                break
            buf += chunk
            start = 0
            while True:
                i = buf.find(b"\0", start)
                if i < 0:
                    break
                self._dispatch(bytes(buf[start:i]))
                start = i + 1
            del buf[:start]
        with self.lock:
            waiting = list(self.pending.values())
            self.pending.clear()
        for slot in waiting:
            slot[0].set()

    def _dispatch(self, raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if "id" in msg:
            with self.lock:
                slot = self.pending.pop(msg["id"], None)
            if slot:
                slot[1] = msg
                slot[0].set()
            return
        for fn in list(self.handlers):
            try:
                fn(msg)
            except Exception as e:                          # noqa: BLE001
                log(f"  (an event handler failed: {e})")

    def send(self, method, params=None, browser=False, slot=None):
        with self.lock:
            if self.dead:
                raise ConnectionError(self.dead)
            self.n += 1
            n = self.n
            if slot is not None:
                self.pending[n] = slot
            msg = {"id": n, "method": method, "params": params or {}}
            if self.session and not browser:
                msg["sessionId"] = self.session
            data = json.dumps(msg).encode() + b"\0"
            while data:
                data = data[os.write(self.w, data):]
        return n

    def call(self, method, params=None, timeout=15, browser=False):
        slot = [threading.Event(), None]
        n = self.send(method, params, browser, slot)
        if not slot[0].wait(timeout):
            with self.lock:
                self.pending.pop(n, None)
            raise TimeoutError(f"{method}: no answer in {timeout} s")
        msg = slot[1]
        if msg is None:
            raise ConnectionError(self.dead or "the DevTools pipe closed")
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error'].get('message')}")
        return msg.get("result", {})

    def value(self, expression, timeout=15, wait=False):
        """An expression's value in the page (awaited when `wait`)."""
        res = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                             "awaitPromise": wait}, timeout)
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            raise RuntimeError(str((d.get("exception") or {}).get("description") or d.get("text")))
        return res.get("result", {}).get("value")

    def attach(self, timeout=30):
        end = time.monotonic() + timeout
        while True:
            for t in self.call("Target.getTargets", browser=True)["targetInfos"]:
                if t.get("type") == "page":
                    self.session = self.call("Target.attachToTarget",
                                             {"targetId": t["targetId"], "flatten": True},
                                             browser=True)["sessionId"]
                    return
            if time.monotonic() > end:
                raise TimeoutError("Chromium opened no page")
            time.sleep(0.05)

    def stop(self):
        self.stopping = True
        self.thread.join(3)


# ============================================================ Chromium
class Browser(js_test.Chromium):
    """js_test's headless Chromium on a DevTools pipe, with the demo window's
    own rules (autoplay without a gesture, no permission prompts) at one window
    size, and an environment of its own: its HOME, caches and runtime folder
    are inside its scratch profile, so it writes nothing anywhere else.

    MUTED UNLESS TOLD OTHERWISE, and muted twice: --mute-audio, and no way to
    reach a sound server at all (PULSE_SERVER names a socket that does not
    exist, and the runtime folder PipeWire's ALSA plugin would look in is an
    empty one of its own). Only tools/demo_record_headless.py asks for sound,
    and it hands over its own PULSE_SERVER and a PULSE_SINK that goes nowhere.
    `close()` is js_test's: the pipe, then the whole process group."""

    def __init__(self, exe, prof, size, *, mute=True, env=None):
        w, h = size
        home = os.path.join(prof, "home")
        run = os.path.join(prof, "run")
        for d in (home, run):
            os.makedirs(d, exist_ok=True)
        os.chmod(run, 0o700)
        self.log = os.path.join(prof, "chromium.log")
        keep = {k: v for k, v in os.environ.items()
                if not k.startswith(("XDG_", "WAYLAND_", "PULSE_", "PIPEWIRE_", "DBUS_"))
                and k not in ("DISPLAY", "HOME", "TMPDIR")}
        keep.update(HOME=home, TMPDIR=prof, XDG_RUNTIME_DIR=run, PIPEWIRE_RUNTIME_DIR=run,
                    XDG_CACHE_HOME=os.path.join(prof, "cache"),
                    XDG_CONFIG_HOME=os.path.join(prof, "config"),
                    PULSE_SERVER="unix:" + os.path.join(run, "no-sound-server"))
        keep.update(env or {})
        flags = ["--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                 "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                 "--disable-backgrounding-occluded-windows",
                 "--autoplay-policy=no-user-gesture-required", "--deny-permission-prompts",
                 f"--window-size={w},{h}", f"--user-data-dir={prof}",
                 "--remote-debugging-pipe", "about:blank"]
        if mute:
            flags.insert(0, "--mute-audio")
        cmd_r, cmd_w = map(js_test.high, os.pipe())
        ans_r, ans_w = map(js_test.high, os.pipe())
        try:
            with open(self.log, "wb") as errs:
                self.proc = subprocess.Popen(
                    ["/bin/sh", "-c", f'exec "$@" 3<&{cmd_r} 4>&{ans_w}', "sh", exe, *flags],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errs,
                    pass_fds=(cmd_r, ans_w), env=keep, start_new_session=True)
        except BaseException:
            for fd in (cmd_w, ans_r):
                os.close(fd)
            raise
        finally:
            os.close(cmd_r)
            os.close(ans_w)
        self.page = js_test.Devtools(ans_r, cmd_w)


def parse_size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


# ============================================================ what the page says
class Watch:
    """Everything the page says that is a fault: uncaught exceptions,
    console.error, the browser's own error log, and requests that failed.
    Warnings are kept and reported. `where` is what the tour was doing, so each
    line says when it happened."""

    def __init__(self, allow=()):
        self.allow = list(EXPECTED) + list(allow)
        self.errors, self.warnings, self.failed, self.aborted = [], [], [], []
        self.urls = {}
        self.where = "boot"
        self.crashed = False

    def _url_ok(self, url):
        return any(fnmatch.fnmatch(url, p) for p in self.allow)

    def __call__(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m in ("Inspector.targetCrashed", "Target.targetCrashed"):
            self.crashed = True
            self.errors.append((self.where, "the page crashed"))
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            text = str((d.get("exception") or {}).get("description") or d.get("text"))
            self.errors.append((self.where, "exception: " + text.splitlines()[0]))
        elif m == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", "")))
                            for a in p.get("args", []))
            if p.get("type") in ("error", "assert"):
                self.errors.append((self.where, "console.error: " + text))
            elif p.get("type") == "warning":
                if text.startswith(FAULTY_WARNINGS):
                    self.errors.append((self.where, "console.warn: " + text))
                else:
                    self.warnings.append((self.where, "console.warn: " + text))
        elif m == "Log.entryAdded":
            e = p.get("entry", {})
            text = f"{e.get('source')}: {e.get('text')}" + (f" ({e['url']})" if e.get("url") else "")
            if e.get("level") == "error":
                if not (e.get("url") and self._url_ok(e["url"])):
                    self.errors.append((self.where, "log: " + text))
            elif e.get("level") == "warning":
                self.warnings.append((self.where, "log: " + text))
        elif m == "Network.requestWillBeSent":
            self.urls[p["requestId"]] = p["request"]["url"]
        elif m == "Network.responseReceived":
            r = p.get("response", {})
            if (r.get("status") or 0) >= 400 and not self._url_ok(r.get("url", "")):
                self.failed.append((self.where, f"{r.get('status')} {r.get('url')}"))
        elif m == "Network.loadingFailed":
            url = self.urls.get(p.get("requestId"), "?")
            what = f"{p.get('errorText')} {url}"
            # A load the page itself stopped (a song skipped, a clip switched)
            # is not a failure.
            if p.get("canceled") or p.get("errorText") == "net::ERR_ABORTED":
                self.aborted.append((self.where, what))
            elif not self._url_ok(url):
                self.failed.append((self.where, what))

    def faults(self):
        return self.errors + self.failed


# ============================================================ one page, one tour
def open_demo(cdp, url, size, watch, boot_timeout=60):
    """/demo.html at `size`, booted, with the tour's steps loaded."""
    w, h = size
    cdp.attach()
    cdp.handlers.append(watch)
    for d in ("Page", "Runtime", "Network", "Log", "Inspector"):
        cdp.call(d + ".enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
    nav = cdp.call("Page.navigate", {"url": url}, timeout=30)
    if nav.get("errorText"):
        raise RuntimeError(f"{url} did not load: {nav['errorText']}")
    # See test/js_test.py: on the tablet a headless tab is hidden, and a hidden
    # page's timers are throttled to the second or the minute.
    cdp.call("Page.bringToFront")
    cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
    end = time.monotonic() + boot_timeout
    while True:
        try:
            up = cdp.value("!!(globalThis.OMACAR_DEMO_TOUR && document.readyState === 'complete'"
                           " && document.getElementById('app').dataset.booting !== '1')")
        except RuntimeError:
            up = False
        if up:
            break
        if time.monotonic() > end:
            raise TimeoutError(f"the demo page did not boot in {boot_timeout} s")
        time.sleep(0.2)
    cdp.value("OMACAR_DEMO_TOUR.ready.then(() => true)", wait=True, timeout=30)
    steps = cdp.value("OMACAR_DEMO_TOUR.tour.steps")
    if not steps:
        raise RuntimeError("the page has no tour (demo/data/tour.json did not load)")
    return steps


def shot_plan(steps):
    """For each step, [(seconds in, tag)]: as it opens, after each action, and
    just before it ends."""
    plan = []
    for s in steps:
        secs = float(s["secs"])
        want = [(OPEN_SHOT, "open")]
        for a in s.get("at", []):
            t = float(a.get("t") or 0) + ACTION_SHOT
            tag = a.get("cue") or (a.get("go") or "").lstrip("#") or \
                "-".join(x for x in (a.get("do"), a.get("arg")) if x)
            if t < secs - 0.5:
                want.append((t, tag.replace(".", "-")))
        if secs >= 12:
            want.append((secs - 1.2, "late"))
        seen, out = set(), []
        for t, tag in sorted(want):
            if tag in seen:
                tag = f"{tag}-{int(t)}"
            seen.add(tag)
            out.append((t, tag))
        plan.append(out)
    return plan


def shoot(cdp, path):
    data = cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=40)["data"]
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))
    return path


STATE_JS = ("(() => { const t = OMACAR_DEMO_TOUR.tour; return [t.state, t.index,"
            " t.state === 'idle' ? 0 : t.elapsed(), location.hash, Date.now()]; })()")


def follow_tour(cdp, steps, watch, out=None, on_step=None, grace=90):
    """Start the tour from the top and follow it to its end. Screenshots into
    `out` when given. Returns (entries, problems): entries are
    [index, id, wall-clock ms at entry, hash at entry]."""
    plan = shot_plan(steps)
    total = sum(float(s["secs"]) for s in steps)
    problems = []
    entries = []
    done = set()
    # As `omacar demo tour` does, through the demo-tour screen's own call: the
    # reset (the drive from Marina, Home as it was, Work fresh, the radio
    # quiet), then step 1.
    started_ms = cdp.value("(OMACAR_DEMO_TOUR.tour.start(), Date.now())")
    t0 = time.monotonic()
    watch.where = "start"
    while True:
        state, index, el, hsh, now_ms = cdp.value(STATE_JS)
        if state == "running" and index is not None and index >= 0:
            if not entries or entries[-1][0] != index:
                s = steps[index]
                entries.append([index, s["id"], now_ms - el * 1000, hsh])
                watch.where = f"{index + 1:02d}-{s['id']}"
                log(f"    step {index + 1:2d} {s['id']:<12} {hsh:<18} at {(now_ms - started_ms) / 1000:6.1f} s")
                if s.get("go") and hsh != s["go"]:
                    problems.append(f"step {index + 1} ({s['id']}) opened {hsh}, not {s['go']}")
                if on_step:
                    on_step(index, s)
            if out:
                for t, tag in plan[index]:
                    if el >= t and (index, tag) not in done:
                        done.add((index, tag))
                        name = f"{index + 1:02d}-{steps[index]['id']}-{tag}.png"
                        try:
                            shoot(cdp, os.path.join(out, name))
                        except (TimeoutError, RuntimeError) as e:
                            problems.append(f"no screenshot {name}: {e}")
        elif state == "paused":
            problems.append(f"the tour paused by itself in step {index + 1}")
            cdp.value("OMACAR_DEMO_TOUR.tour.resume()")
        elif state == "idle" and entries:
            break
        if watch.crashed:
            problems.append("the page crashed")
            break
        if time.monotonic() - t0 > total + grace:
            problems.append(f"the tour had not ended {total + grace:.0f} s after it started "
                            f"(state {state}, step {index})")
            break
        time.sleep(0.2)
    watch.where = "end"
    # IN ORDER, EACH FOR ITS TIME.
    got = [e[0] for e in entries]
    if got != list(range(len(steps))):
        problems.append(f"the steps ran as {[g + 1 for g in got]}, not 1 to {len(steps)}")
    for a, b in zip(entries, entries[1:]):
        want = float(steps[a[0]]["secs"])
        took = (b[2] - a[2]) / 1000
        if abs(took - want) > 2.0:
            problems.append(f"step {a[0] + 1} ({a[1]}) ran {took:.1f} s, not {want:.0f}")
    time.sleep(1.0)
    hsh = cdp.value("location.hash")
    if hsh != "#home":
        problems.append(f"the tour ended on {hsh}, not #home")
    if out:
        shoot(cdp, os.path.join(out, f"{len(steps) + 1:02d}-after-end.png"))
    return entries, problems


def blank_shots(folder):
    """Screenshots that are one flat colour (a screen that never drew), when
    Pillow is here to look."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return None
    flat = []
    for n in sorted(os.listdir(folder)):
        if n.endswith(".png"):
            with Image.open(os.path.join(folder, n)) as im:
                if max(ImageStat.Stat(im.convert("L")).stddev) < 2.0:
                    flat.append(n)
    return flat


def tour_at(url, size, prof, out, allow, exe):
    """One whole tour at one size, in a Chromium of its own. Returns a result
    dict; raises nothing a tour can cause."""
    os.makedirs(out, exist_ok=True)
    watch = Watch(allow)
    res = {"size": f"{size[0]}x{size[1]}", "out": out, "problems": []}
    browser = Browser(exe, prof, size)
    cdp = Cdp(browser)
    try:
        steps = open_demo(cdp, url, size, watch)
        time.sleep(2.0)
        shoot(cdp, os.path.join(out, "00-booted.png"))
        entries, problems = follow_tour(cdp, steps, watch, out)
        res.update(steps=len(steps), entries=entries)
        res["problems"] += problems
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as e:
        res["problems"].append(f"the walk stopped: {e}")
    finally:
        cdp.stop()
        browser.close()
    flat = blank_shots(out)
    if flat:
        res["problems"].append(f"blank screenshots: {', '.join(flat)}")
    res.update(errors=watch.errors, failed=watch.failed, warnings=watch.warnings,
               aborted=watch.aborted, shots=len([n for n in os.listdir(out) if n.endswith(".png")]))
    return res


# ============================================================ the silo
def fingerprint(path):
    """A file's content hash; for a big one (a dashcam clip, over 8 MB), its
    size, its time and the hash of its two ends, which a write would change."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        return "link:" + os.readlink(path)
    if not stat.S_ISREG(st.st_mode):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if st.st_size <= 8 << 20:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        else:
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
            h.update(f.read(1 << 20))
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return h.hexdigest()


def hash_tree(roots, skip=()):
    """{label:relpath: fingerprint} of every file under each (label, root), a
    root that is a file included, never descending into `skip`."""
    skip = [os.path.realpath(s) for s in skip]
    out = {}
    for label, top in roots:
        if os.path.isfile(top) or os.path.islink(top):
            fp = fingerprint(top)
            if fp:
                out[label] = fp
            continue
        for d, dirs, files in os.walk(top):
            if any(os.path.realpath(d) == s or os.path.realpath(d).startswith(s + os.sep) for s in skip):
                dirs[:] = []
                continue
            for n in files:
                p = os.path.join(d, n)
                try:
                    fp = fingerprint(p)
                except OSError:
                    continue
                if fp:
                    out[f"{label}:{os.path.relpath(p, top)}"] = fp
    return out


def diff(before, after):
    return sorted([f"changed {k}" for k in before if k in after and before[k] != after[k]]
                  + [f"removed {k}" for k in before if k not in after]
                  + [f"added {k}" for k in after if k not in before])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), 0.3):
            return True
    except OSError:
        return False


def mentions(needle, exclude=()):
    """Processes whose command line or environment names `needle`."""
    found = []
    me = {os.getpid(), *exclude}
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) in me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read()
        except OSError:
            continue
        if needle in cmd or needle.encode() in env:
            found.append((int(pid), cmd[:160]))
    return found


# ============================================================ the scratch demo
SHIMMED = ("systemctl", "wpctl", "pactl", "hyprctl", "chromium", "mpv")


class Scratch:
    """A demo of this checkout in a scratch HOME, muted, on a port of its own,
    with nothing on the real machine reachable from it but the checkout."""

    def __init__(self, work, clips=None, roadcams=None, roadcams_pins=None):
        self.base = tempfile.mkdtemp(prefix="omacar-demo-e2e-", dir=work)
        self.home = os.path.join(self.base, "home")
        self.run = os.path.join(self.base, "run")
        self.shims = os.path.join(self.base, "bin")
        self.shim_log = os.path.join(self.base, "shims.log")
        self.demo_root = os.path.join(self.home, ".local", "state", "omacar-demo")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/demo.html"
        for d in (self.home, self.run, self.shims):
            os.makedirs(d, exist_ok=True)
        os.chmod(self.run, 0o700)
        open(self.shim_log, "w").close()
        self._fake_real_state(roadcams, roadcams_pins)
        for name in SHIMMED:
            p = os.path.join(self.shims, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(f'#!/bin/sh\necho "{name} $*" >> "{self.shim_log}"\nexit 0\n')
            os.chmod(p, 0o755)
        self.clips = clips or self._make_clips()
        dead = "http://127.0.0.1:9"
        self.env = {
            "PATH": self.shims + ":/usr/local/bin:/usr/bin:/bin", "HOME": self.home,
            "XDG_RUNTIME_DIR": self.run, "LANG": "C.UTF-8", "TERM": "dumb",
            "OMACAR_DEMO_MUTE": "1", "OMACAR_DEMO_PORT": str(self.port),
            "OMACAR_DEMO_CLIPS": self.clips,
            "http_proxy": dead, "https_proxy": dead, "HTTP_PROXY": dead, "HTTPS_PROXY": dead,
            "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost",
        }

    def _fake_real_state(self, roadcams, roadcams_pins):
        live = {"connected": False, "t": time.time() - 3600, "values": {"SPEED": 0}}
        files = {
            ".local/state/omacar/live.json": json.dumps(live),
            ".config/omarchy/omacar-audio.json": json.dumps({"managed": False}),
            "Videos/OmaCar/front/20260930-010000.mp4": "not really a clip",
            ".local/state/omarchy/liquid-glass-car.json": json.dumps({"name": "the real car"}),
        }
        for rel, body in files.items():
            p = os.path.join(self.home, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        # The live recorder's runtime files: the demo's feed has its own folder.
        p = os.path.join(self.run, "omacar-cams", "status.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": 1, "pid": 1, "roles": {}}))
        # Saved road-camera stills, as `demo on` copies them from a real
        # state: a copy of somebody's cache, when given one.
        if roadcams:
            shutil.copytree(roadcams, os.path.join(self.home, ".local/state/omacar/roadcams"))
        if roadcams_pins:
            shutil.copy2(roadcams_pins, os.path.join(self.home, ".config/omarchy/omacar-roadcams.json"))

    def _make_clips(self):
        """Twenty seconds of test pattern per camera (the private clips are
        the controller's). ffmpeg's own generators: nothing is downloaded."""
        d = os.path.join(self.base, "clips")
        os.makedirs(d)
        for role, src in (("front", "testsrc2=size=640x360:rate=25"),
                          ("rear", "testsrc2=size=640x360:rate=25"),
                          ("cabin", "testsrc2=size=480x360:rate=25"),
                          ("cabin-drowsy", "smptebars=size=480x360:rate=25")):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", src, "-t", "20", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "30", "-g", "25", "-pix_fmt", "yuv420p",
                            os.path.join(d, role + ".mp4")],
                           check=True, capture_output=True, timeout=120)
        return d

    def demo(self, *args, timeout=180):
        r = subprocess.run([OMACAR, "demo", *args], env=self.env, capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr)

    def outside(self):
        """Everything of the scratch HOME's that is not the demo's, and the
        scratch runtime folder."""
        return hash_tree([("home", self.home), ("run", self.run)], skip=[self.demo_root])

    def shim_calls(self):
        with open(self.shim_log, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def remove(self):
        shutil.rmtree(self.base, ignore_errors=True)


def wait_page(url, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def teardown(sc, report, exclude=()):
    """`demo off`, then what is left: processes naming the scratch folder, the
    port, and the shims' log."""
    rc, out = sc.demo("off", timeout=120)
    report["off"] = out.strip().splitlines()
    problems = []
    if rc != 0:
        problems.append(f"demo off exited {rc}")
    time.sleep(1.0)
    left = mentions(sc.base, exclude)
    if left:
        problems.append("still running after demo off: " + "; ".join(f"{p} {c}" for p, c in left))
        for pid, _ in left:           # nothing of this run's is left on the machine
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    if listening(sc.port):
        problems.append(f"port {sc.port} is still held after demo off")
    return problems


# ============================================================ main
def args_of(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", default=",".join(SIZES), help="WxH,... (default: all three)")
    ap.add_argument("--repeat", type=int, default=1, help="run the sizes this many times")
    ap.add_argument("--out", help="screenshots here (default: a folder beside the scratch one)")
    ap.add_argument("--work", default="/var/tmp", help="where the scratch folder goes")
    ap.add_argument("--clips", help="camera clips to hand the demo (default: made here)")
    ap.add_argument("--roadcams", help="a saved road-cameras cache to seed the fake real state with")
    ap.add_argument("--roadcams-pins", help="and its pins file")
    ap.add_argument("--allow", action="append", default=[],
                    help="a URL pattern whose failure is expected (fnmatch); repeatable")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    ap.add_argument("--tablet", action="store_true",
                    help="no scratch demo: tour the demo already running on --port")
    ap.add_argument("--port", type=int, default=7580)
    ap.add_argument("--baseline", type=float, default=15.0,
                    help="--tablet: seconds to watch the real trees change on their own first")
    ap.add_argument("--silo-root", action="append", default=[],
                    help="--tablet: another real file or folder to hold byte-identical")
    ap.add_argument("--home", default=os.path.expanduser("~"),
                    help="--tablet: the HOME whose real trees are held (default: yours)")
    return ap.parse_args(argv)


def real_roots(home, extra):
    roots = [("state", os.path.join(home, ".local/state/omacar")),
             ("config", os.path.join(home, ".config/omarchy")),
             ("videos", os.path.join(home, "Videos/OmaCar")),
             ("rollup", os.path.join(home, ".local/state/omarchy/liquid-glass-car.json"))]
    roots += [(p, p) for p in extra]
    return [(label, p) for label, p in roots if os.path.exists(p)]


def main(argv=None):
    a = args_of(argv if argv is not None else sys.argv[1:])
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda n, _: sys.exit(128 + n))
    exe = js_test.browser()
    if not exe:
        log("demo_e2e: no chromium here")
        return 2
    sizes = [parse_size(s) for s in a.sizes.split(",") if s.strip()] * max(1, a.repeat)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = a.out or os.path.join(a.work, f"omacar-demo-e2e-shots-{stamp}")
    os.makedirs(out, exist_ok=True)
    report = {"mode": "tablet" if a.tablet else "scratch", "results": [], "problems": []}
    problems = report["problems"]
    sc = None
    before, noisy = None, set()
    profs = tempfile.mkdtemp(prefix="omacar-demo-e2e-browser-", dir=a.work)
    try:
        if a.tablet:
            url = f"http://127.0.0.1:{a.port}/demo.html"
            if not wait_page(url, 5):
                log(f"demo_e2e: nothing answers {url}; start the demo first (omacar demo on)")
                return 2
            roots = real_roots(a.home, a.silo_root)
            log(f"\n  the real trees, watched for {a.baseline:.0f} s before touring")
            first = hash_tree(roots)
            time.sleep(a.baseline)
            before = hash_tree(roots)
            noisy = {k for k in set(first) | set(before) if first.get(k) != before.get(k)}
        else:
            sc = Scratch(a.work, a.clips, a.roadcams, a.roadcams_pins)
            url = sc.url
            log(f"\n  scratch HOME  {sc.home}\n  demo          {url}")
            before = sc.outside()
            rc, said = sc.demo("on")
            report["on"] = said.strip().splitlines()
            log("  demo on:\n    " + "\n    ".join(report["on"]))
            if rc != 0 or not wait_page(url):
                problems.append(f"demo on did not bring up {url} (exit {rc})")
                raise SystemExit
            rc, said = sc.demo("check", timeout=60)
            report["check"] = said.strip().splitlines()
            log("  demo check (the kiosk line is expected to say no in a scratch HOME):\n    "
                + "\n    ".join(report["check"]))
        for i, size in enumerate(sizes):
            tag = f"{size[0]}x{size[1]}" + (f"-{i // len(set(sizes)) + 1}" if a.repeat > 1 else "")
            log(f"\n  the tour at {tag}")
            res = tour_at(url, size, os.path.join(profs, f"p{i}"), os.path.join(out, tag),
                          a.allow, exe)
            report["results"].append(res)
            for p in res["problems"] + [f"{w}: {e}" for w, e in res["errors"] + res["failed"]]:
                log(f"    FAIL  {p}")
            log(f"    {res['shots']} screenshots, {len(res['errors'])} console errors, "
                f"{len(res['failed'])} failed requests, {len(res['warnings'])} warnings, "
                f"{len(res['aborted'])} loads cancelled by the page")
    except SystemExit:
        pass
    finally:
        if sc:
            try:
                problems += teardown(sc, report)
                report["silo"] = diff(before, sc.outside())
                calls = sc.shim_calls()
                if calls:
                    problems.append("shims were called: " + "; ".join(calls))
            except Exception as e:                              # noqa: BLE001
                problems.append(f"taking the demo down: {e!r} (scratch kept: {sc.base})")
                a.keep = True
        elif a.tablet and before is not None:
            after = hash_tree(real_roots(a.home, a.silo_root))
            changed = diff(before, after)
            report["silo"] = [c for c in changed if c.split(" ", 1)[1] not in noisy]
            report["silo_by_itself"] = sorted(noisy)
        if report.get("silo"):
            problems.append("files outside the demo's folder changed: " + "; ".join(report["silo"]))
        shutil.rmtree(profs, ignore_errors=True)
        if sc and not a.keep:
            sc.remove()
        with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    failed = bool(problems) or any(r["problems"] or r["errors"] or r["failed"]
                                   for r in report["results"])
    if len(report["results"]) < len(sizes):
        failed = True
    log("\n  the silo: " + ("byte-identical outside the demo's folder" if "silo" in report
                           and not report["silo"] else "see above"))
    if report.get("silo_by_itself"):
        log("  (changed on their own before the tour, not counted: "
            + ", ".join(report["silo_by_itself"][:8]) + ")")
    for p in problems:
        log(f"  FAIL  {p}")
    log(f"\n  screenshots and report.json in {out}")
    log("\n  " + ("FAILED" if failed else "all passed") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
