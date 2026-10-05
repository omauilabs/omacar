#!/usr/bin/env python3
"""The app's pure JavaScript, tested in the browser the app runs in.

There is no node on the tablet or on the box, and the house rule is no build
step, so a JavaScript unit test here is a module under test/js/ that a real
Chromium imports. Each module's default export is a list of [name, fn] pairs;
fn throws to fail. The runner page collects the results into document.title,
and this runner reads that title back over the DevTools protocol, in real time,
until the page says it is done.

Skipped loudly without a browser, like app_test.py: a test that cannot run is
not a failure, and a test that silently does nothing is.
"""

import collections
import fcntl
import http.server
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
TESTS = os.path.join(ROOT, "test", "js")

# The browser finder comes from app_test.py, the suite that first ran this app
# in a browser -- one copy of how a browser is found.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser  # noqa: E402

# REAL seconds the page has, from Chromium's launch to its RESULT. A healthy
# machine needs a few (the demo build's 557 tests take about four); this is the
# fence that turns a page that will never finish into a FAIL that says where it
# stopped.
LIMIT = 180

# Virtual time is granted in slices of this many virtual milliseconds, and a
# spent slice is granted again (see drive), so no run ever meets the end of it.
VIRTUAL_MS = 30000

# The page names the test it is in, in its title, as it goes: a page that never
# finishes then says where it stopped instead of only that it did.
RUNNER = """<!doctype html><meta charset="utf-8"><title>RUNNING</title>
<script type="module">
const files = %s;
const out = { passed: 0, failed: [] };
for (const f of files) {
  let mod;
  document.title = "RUNNING " + f;
  try { mod = await import("./_tests/" + f); }
  catch (e) { out.failed.push(f + " :: import :: " + ((e && e.message) || e)); continue; }
  for (const [name, fn] of mod.default) {
    document.title = "RUNNING " + f + " :: " + name;
    try { await fn(); out.passed++; }
    catch (e) { out.failed.push(f + " :: " + name + " :: " + ((e && e.message) || e)); }
  }
}
document.title = "RESULT " + JSON.stringify(out);
</script>
"""


class Failed(Exception):
    """The runner page did not finish. `str()` says why; `notes` says what the
    page and Chromium had said on the way."""

    def __init__(self, why, notes=()):
        super().__init__(why)
        self.notes = list(notes)


# --------------------------------------------------------------------- DevTools
class Devtools:
    """A DevTools session over Chromium's --remote-debugging-pipe: JSON
    messages, each ended by a NUL byte, down one pipe (`w`) and back up another
    (`r`). No port is open and nothing else can connect, and Chromium leaves when
    the pipe closes, however the runner dies. `call` sends a command and returns
    its result. What the page says on its own goes into `heard` (its last few
    exceptions and console errors, newest last) and `inflight` (the requests it
    has out), for the failure message; a crash raises."""

    def __init__(self, r, w):
        self.r, self.w = r, w
        self.buf = b""
        self.n = 0
        self.session = None
        self.heard = collections.deque(maxlen=8)
        self.inflight = {}
        self.on_budget = None

    def close(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass

    def _message(self, end):
        while True:
            head, nul, rest = self.buf.partition(b"\0")
            if nul:
                self.buf = rest
                return json.loads(head)
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.r], [], [], left)[0]:
                raise TimeoutError("no answer from the page")
            more = os.read(self.r, 65536)
            if not more:
                raise ConnectionError("Chromium closed the DevTools pipe")
            self.buf += more

    def _heard(self, msg):
        method, p = msg.get("method"), msg.get("params", {})
        if method in ("Inspector.targetCrashed", "Target.targetCrashed"):
            raise ConnectionError("the page crashed")
        if method == "Emulation.virtualTimeBudgetExpired" and self.on_budget:
            self.on_budget()
        elif method == "Network.requestWillBeSent":
            self.inflight[p["requestId"]] = p["request"]["url"]
        elif method in ("Network.loadingFinished", "Network.loadingFailed"):
            self.inflight.pop(p["requestId"], None)
        elif method == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.heard.append("exception: " + str((d.get("exception") or {}).get("description")
                                                  or d.get("text")).splitlines()[0])
        elif method == "Runtime.consoleAPICalled" and p.get("type") == "error":
            self.heard.append("console.error: " + " ".join(
                str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))

    def send(self, method, params=None):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if self.session:
            msg["sessionId"] = self.session
        data = json.dumps(msg).encode() + b"\0"
        while data:
            data = data[os.write(self.w, data):]
        return self.n

    def call(self, method, params=None, timeout=10):
        want = self.send(method, params)
        end = time.monotonic() + timeout
        while True:
            msg = self._message(end)
            if msg.get("id") != want:
                self._heard(msg)
                continue
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error'].get('message')}")
            return msg.get("result", {})

    def value(self, expression, timeout=10):
        """An expression's value in the page."""
        return self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                         timeout)["result"].get("value")

    def attach(self, end):
        """Attach to the browser's one page, once it has opened; every command
        after this goes to it."""
        while True:
            for t in self.call("Target.getTargets")["targetInfos"]:
                if t.get("type") == "page":
                    self.session = self.call("Target.attachToTarget",
                                             {"targetId": t["targetId"], "flatten": True})["sessionId"]
                    return
            if time.monotonic() > end:
                raise TimeoutError("Chromium opened no page")
            time.sleep(0.05)


# --------------------------------------------------------------------- Chromium
def high(fd):
    """`fd` moved above 9, and the original closed, so that nothing about where
    the OS happened to number it can collide with descriptors 3 and 4."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
    os.close(fd)
    return moved


class Chromium:
    """Chromium, headless, on the scratch profile `prof`, with its DevTools on a
    pipe (`self.page`, a Devtools). `close()` leaves nothing of it running."""

    def __init__(self, exe, prof, log):
        cmd_r, cmd_w = map(high, os.pipe())         # our commands, its input
        ans_r, ans_w = map(high, os.pipe())         # its answers, our input
        self.log = log
        # --mute-audio: the units build the page's real AudioContext (the
        # audio stage, the alert player), and nothing a test does may ever
        # sound on this machine's speakers. The sound tests render into
        # OfflineAudioContexts, which never reach an output at all; this is
        # the second lock on the same door.
        #
        # --remote-debugging-pipe reads commands on descriptor 3 and answers on
        # 4, and exits when 3 closes -- which is the point: a runner that is
        # killed, hung up on or out of memory takes Chromium with it, where a
        # debugging port would outlive it. The shell only puts the pipes where
        # Chromium wants them, whatever numbers the OS gave them here, and
        # becomes Chromium by exec.
        try:
            with open(log, "wb") as errs:
                self.proc = subprocess.Popen(
                    # bash, not /bin/sh: the pipes' numbers are often above 9,
                    # and dash (Ubuntu's sh, the CI runner's) refuses a
                    # descriptor of two digits as "Bad fd number".
                    ["bash", "-c", f'exec "$@" 3<&{cmd_r} 4>&{ans_w}', "bash", exe,
                     "--headless=new", "--disable-gpu", "--no-sandbox", "--mute-audio",
                     "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                     "--disable-backgrounding-occluded-windows",
                     f"--user-data-dir={prof}", "--remote-debugging-pipe", "about:blank"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errs,
                    pass_fds=(cmd_r, ans_w),
                    # TMPDIR inside the profile: Chromium keeps a directory of
                    # its own in TMPDIR and removes it only on a clean exit, so a
                    # Chromium that is signalled would leave one behind in /tmp
                    # on every run.
                    env=dict(os.environ, TMPDIR=prof), start_new_session=True)
        except BaseException:
            for fd in (cmd_w, ans_r):
                os.close(fd)
            raise
        finally:
            os.close(cmd_r)
            os.close(ans_w)
        self.page = Devtools(ans_r, cmd_w)

    def exited(self, wait=3):
        """Chromium's exit status if it has exited (or does within `wait`
        seconds), else None."""
        try:
            return self.proc.wait(wait)
        except subprocess.TimeoutExpired:
            return None

    def close(self):
        """Chromium and everything it started, gone. The pipe closes first, and
        Chromium leaves by itself; then it is asked, then made to: the whole
        process group, because its renderer, GPU and utility processes are not
        its parent's to outlive."""
        self.page.close()
        self.exited(3)
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 10)):
            try:
                os.killpg(self.proc.pid, sig)     # what is still there, and any straggler its parent outlived
            except (ProcessLookupError, PermissionError):
                break
            self.exited(wait)


def tail(path, n=6):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip() for ln in f.read().splitlines()[-n:]]
    except OSError:
        return []


def stalled(page, title, limit):
    """The failure for a page still at `title` when the real-time limit ran out,
    with what is worth knowing about why."""
    notes = list(page.heard)
    notes += [f"request still out: {u}" for u in list(page.inflight.values())[:5]]
    try:
        visible, focused = page.value("[document.visibilityState, document.hasFocus()]", timeout=3)
        notes.append(f"the page's visibility is {visible!r}, and it is {'focused' if focused else 'not focused'}")
    except (OSError, RuntimeError, ValueError, TypeError):
        notes.append("the page did not answer a last question")
    return Failed(f"after {limit} s of real time it was still at {title!r}", notes)


# THE PAGE RUNS ON VIRTUAL TIME, AND THE RUNNER WAITS ON THE REAL CLOCK.
#
# The two jobs are different. Virtual time (Emulation.setVirtualTimePolicy, what
# --virtual-time-budget is made of) is what keeps a test that waits two seconds
# for a timer cheap: the demo build's 557 units take about 4 s on the box that
# way, about 19 s on the real clock, and on the real clock three of them fail,
# because they were written against the layout virtual time gives them (pictures
# still loading). Virtual time is not a way to know the page is DONE, though.
# --dump-dom answers when the budget is spent, and a budget is spent by idling:
# a page waiting on anything real (a network answer, a decoder, a device) has its
# whole budget fast-forwarded past while it waits, and is dumped still RUNNING.
#
# So the page keeps virtual time, granted in slices that are granted again as
# they are spent, and the runner asks the page, on the real clock, whether it has
# said RESULT yet. The real limit is then the only budget there is.
#
# THE PAGE MUST BE VISIBLE AND FOCUSED, and bringToFront and focus emulation are
# there for the tablet's hidden headless tab. Nothing else needs them: the box's
# tab is visible and focused without, and so is a Mac's. On the tablet, headless
# Chromium's tab is HIDDEN from the moment it navigates to the runner page.
# Chromium throttles a hidden page's timers: each one waits for the next whole
# second, and after five minutes hidden, for the next whole minute -- and virtual
# time reaches five minutes in an instant. A test's 5 ms wait then cost a virtual
# minute, the whole budget went in the first forty tests, and the page was dumped
# RUNNING. Nothing was wrong with those tests. Bringing the tab to the front ends
# that, and the anti-throttling flags are the second lock on the same door.
#
# It is not enough alone. With the tab in front and the page unfocused, the
# tablet finished, but a <video> pointed at a 404 often never fired its `error`
# (the box's fires in 3 ms): none of 10 runs passed all 557, cameras.test.js's
# real-404 test being the one that failed. With focus emulated as well, 20 of 20
# did. Both come after the navigation, since the navigation is what hides the tab.
def drive(chrome, url, limit=LIMIT):
    """Open `url` in `chrome`, and wait IN REAL TIME for the page's title to
    start with "RESULT ". Returns that title, or raises Failed."""
    end = time.monotonic() + limit
    page = chrome.page
    try:
        page.attach(end)
        for domain in ("Inspector", "Runtime", "Network"):
            page.call(domain + ".enable")
        # Virtual time waits, stopped, while the page loads: nothing of the
        # page's may be spent, or throttled, before it is visible.
        page.call("Emulation.setVirtualTimePolicy", {"policy": "pause"})
        nav = page.call("Page.navigate", {"url": url})
        if nav.get("errorText"):
            raise Failed(f"the runner page did not load: {nav['errorText']}")
        while page.value("location.href") == "about:blank":
            if time.monotonic() > end:
                raise Failed("the runner page never loaded")
            time.sleep(0.02)
        page.call("Page.bringToFront")
        page.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
        slice_ = {"policy": "pauseIfNetworkFetchesPending", "budget": VIRTUAL_MS}
        page.on_budget = lambda: page.send("Emulation.setVirtualTimePolicy", slice_)
        page.call("Emulation.setVirtualTimePolicy", slice_)
        title = ""
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise stalled(page, title, limit)
            try:
                title = page.value("document.title", timeout=left) or ""
            except TimeoutError:
                raise stalled(page, title, limit) from None
            if title.startswith("RESULT "):
                return title
            time.sleep(min(0.1, max(0, end - time.monotonic())))
    except (OSError, RuntimeError, ValueError, KeyError) as e:
        status = chrome.exited() if isinstance(e, ConnectionError) else None
        if status is not None:
            raise Failed(f"Chromium exited (status {status}) before the page finished",
                         tail(chrome.log)) from None
        raise Failed(str(e), list(page.heard) or tail(chrome.log)) from None


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, *args):              # a browser that hangs up on a request is not news
        pass


def run_units(exe, share, tests, files, limit=LIMIT):
    """Serve a copy of `share` with the modules `files` of `tests` beside it,
    run them in one page of `exe`, and return the page's
    {"passed": n, "failed": [...]}. Raises Failed. However it ends, what it
    started is stopped and what it wrote is removed."""
    # A COPY of share/, so nothing here can leave a test page in the repo.
    work = tempfile.mkdtemp()
    prof = tempfile.mkdtemp()
    server = thread = chrome = None
    try:
        copy = os.path.join(work, "share")
        # Not share/assets/private: the owner's pictures and the demo's songs
        # and footage, tens of megabytes no test reads (and CI never has).
        shutil.copytree(share, copy, ignore=lambda d, names:
                        ["private"] if os.path.basename(d) == "assets" else [])
        # The meetup demo's modules, beside share/ in the repo and at /demo/
        # on the demo server, so its tests import them as ../demo/js/<file>.
        demo = os.path.join(os.path.dirname(os.path.abspath(share)), "demo")
        if os.path.isdir(demo):
            shutil.copytree(demo, os.path.join(copy, "demo"))
        shutil.copytree(tests, os.path.join(copy, "_tests"))
        with open(os.path.join(copy, "_run.html"), "w", encoding="utf-8") as f:
            f.write(RUNNER % json.dumps(files))
        # In this process, not a child of it: a runner that dies any way at all
        # takes its server down with it.
        server = Server(("127.0.0.1", 0), lambda *a: Quiet(*a, directory=copy))
        thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        chrome = Chromium(exe, prof, os.path.join(work, "chromium.log"))
        title = drive(chrome, f"http://127.0.0.1:{server.server_address[1]}/_run.html", limit)
        try:
            return json.loads(title[len("RESULT "):])
        except ValueError:
            raise Failed(f"the page's result could not be read: {title[:200]!r}") from None
    finally:
        if chrome:
            chrome.close()
        if server:
            server.shutdown()
            server.server_close()
            thread.join(5)
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def main():
    print("\n  JavaScript units, in a real browser\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0
    files = sorted(f for f in os.listdir(TESTS) if f.endswith(".test.js"))
    if not files:
        print("    FAIL  test/js holds no *.test.js files\n")
        return 1
    # A runner that is told to stop, or hung up on (an ssh session that drops),
    # must still take Chromium down cleanly: the signal becomes an exit, and the
    # exit runs the finally blocks that stop it and remove what it wrote.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda n, _: sys.exit(128 + n))
    try:
        res = run_units(exe, SHARE, TESTS, files)
    except Failed as e:
        print(f"    FAIL  the runner page never finished: {e}")
        for note in e.notes:
            print(f"          {note}")
        print()
        return 1
    for line in res["failed"]:
        print(f"    FAIL  {line}")
    print(f"\n  {res['passed']} passed, {len(res['failed'])} failed\n")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
