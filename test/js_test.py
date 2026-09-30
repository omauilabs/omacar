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

import base64
import collections
import hashlib
import http.client
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
TESTS = os.path.join(ROOT, "test", "js")

# The browser finder and the free port come from app_test.py, the suite that
# first ran this app in a browser -- one copy of how a browser is found.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser, free_port  # noqa: E402

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


# ------------------------------------------------------------------ WebSocket
# The DevTools protocol is JSON over a WebSocket (RFC 6455), and a WebSocket
# library is not something every machine this runs on has. The client half of
# the protocol is small enough to write here: one handshake, masked frames out,
# whole frames in. test/js_runner_test.py holds the frame code to the RFC's own
# examples.

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_accept(key):
    """The Sec-WebSocket-Accept a server must answer `key` with."""
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


def ws_encode(payload, opcode=0x1, mask=None):
    """One final, masked client frame. `mask` is pinned only by the tests."""
    n = len(payload)
    if n < 126:
        head = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
    elif n < 1 << 16:
        head = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
    mask = os.urandom(4) if mask is None else mask
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def ws_decode(buf):
    """(fin, opcode, payload, bytes used) for the first whole frame in `buf`,
    or None while `buf` holds only part of one."""
    if len(buf) < 2:
        return None
    fin, opcode = bool(buf[0] & 0x80), buf[0] & 0x0F
    n, at = buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, at = struct.unpack(">H", bytes(buf[2:4]))[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, at = struct.unpack(">Q", bytes(buf[2:10]))[0], 10
    mask = None
    if buf[1] & 0x80:                       # a server does not mask; be able to read one that does
        if len(buf) < at + 4:
            return None
        mask, at = bytes(buf[at:at + 4]), at + 4
    if len(buf) < at + n:
        return None
    payload = bytes(buf[at:at + n])
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload, at + n


class Devtools:
    """One page's DevTools connection. `call` sends a command and returns its
    result. What the page says on its own goes into `heard` (its last few
    exceptions and console errors, newest last) and `inflight` (the requests it
    has out), for the failure message; a crash raises."""

    def __init__(self, port, path, timeout=10):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout)
        self.buf = bytearray()
        self.partial = b""
        self.n = 0
        self.heard = collections.deque(maxlen=8)
        self.inflight = {}
        self.on_budget = None
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            more = self.sock.recv(4096)
            if not more:
                raise ConnectionError("Chromium closed the DevTools socket during the handshake")
            self.buf += more
        head, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        self.buf = bytearray(rest)
        lines = head.decode("latin-1").split("\r\n")
        got = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if " 101 " not in lines[0] or got.get("sec-websocket-accept") != ws_accept(key):
            raise ConnectionError(f"DevTools did not accept the WebSocket: {lines[0]}")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _frame(self, end):
        while True:
            got = ws_decode(self.buf)
            if got:
                del self.buf[:got[3]]
                return got[:3]
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("no answer from the page")
            self.sock.settimeout(left)
            try:
                more = self.sock.recv(65536)
            except socket.timeout:
                raise TimeoutError("no answer from the page") from None
            if not more:
                raise ConnectionError("Chromium closed the DevTools connection")
            self.buf += more

    def _message(self, end):
        while True:
            fin, opcode, payload = self._frame(end)
            if opcode == 0x8:
                raise ConnectionError("Chromium closed the DevTools connection")
            if opcode == 0x9:
                self.sock.sendall(ws_encode(payload, 0xA))
            elif opcode <= 0x2:
                self.partial += payload
                if fin:
                    text, self.partial = self.partial, b""
                    return json.loads(text)

    def _heard(self, msg):
        method, p = msg.get("method"), msg.get("params", {})
        if method == "Inspector.targetCrashed":
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
        self.sock.sendall(ws_encode(json.dumps(
            {"id": self.n, "method": method, "params": params or {}}).encode()))
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


# --------------------------------------------------------------------- Chromium
class Failed(Exception):
    """The runner page did not finish. `str()` says why; `notes` says what the
    page and Chromium had said on the way."""

    def __init__(self, why, notes=()):
        super().__init__(why)
        self.notes = list(notes)


def stop(proc):
    """Chromium and everything it started, gone. Asked first, then made to:
    the whole process group, because its renderer, GPU and utility processes
    are not its parent's to outlive."""
    def signal_group(sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    signal_group(signal.SIGTERM)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    signal_group(signal.SIGKILL)        # what ignored the request, and any straggler its parent outlived
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def tail(path, n=6):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip() for ln in f.read().splitlines()[-n:]]
    except OSError:
        return []


def listening(proc, prof, end, log):
    """The port Chromium's DevTools listens on. It writes it into its profile
    once it is up; `--remote-debugging-port=0` lets it choose a free one."""
    active = os.path.join(prof, "DevToolsActivePort")
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise Failed(f"Chromium exited (status {proc.returncode}) before it was listening", tail(log))
        try:
            with open(active) as f:
                lines = f.read().split("\n")
            if len(lines) > 1:
                return int(lines[0])
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise Failed("Chromium never started listening", tail(log))


def page_path(port, end):
    """The WebSocket path of the browser's one open page."""
    while time.monotonic() < end:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/json/list")
            for t in json.loads(conn.getresponse().read()):
                if t.get("type") == "page":
                    return "/" + t["webSocketDebuggerUrl"].split("/", 3)[3]
        finally:
            conn.close()
        time.sleep(0.05)
    raise Failed("Chromium opened no page")


def stalled(page, title, limit):
    """The failure for a page still at `title` when the real-time limit ran out,
    with what is worth knowing about why."""
    notes = list(page.heard)
    notes += [f"request still out: {u}" for u in list(page.inflight.values())[:5]]
    try:
        notes.append(f"the page's visibility is {page.value('document.visibilityState', timeout=3)!r}")
    except (OSError, RuntimeError):
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
# THE PAGE MUST BE VISIBLE AND FOCUSED. On the tablet, headless Chromium's tab is
# HIDDEN from the moment it navigates to the runner page (the box's stays
# visible, and focused, as the --dump-dom runs saw it). Chromium throttles a
# hidden page's timers: each one waits for the next whole second, and after five
# minutes hidden, for the next whole minute -- and virtual time reaches five
# minutes in an instant. A test's 5 ms wait then cost a virtual minute, the whole
# budget went in the first forty tests, and the page was dumped RUNNING. Nothing
# was wrong with those tests. Bringing the tab to the front ends that, and the
# anti-throttling flags are the second lock on the same door.
#
# It is not enough alone. With the tab in front and the page unfocused, the
# tablet finished, but a <video> pointed at a 404 often never fired its `error`
# (the box's fires in 3 ms): none of 10 runs passed all 557, cameras.test.js's
# real-404 test being the one that failed. With focus emulated as well, 20 of 20
# did. Both come after the navigation, since the navigation is what hides the tab.
def drive(exe, prof, url, log, limit=LIMIT):
    """Launch Chromium on the scratch profile `prof`, open `url`, and wait IN
    REAL TIME for the page's title to start with "RESULT ". Returns that title,
    or raises Failed. Chromium is gone, and `prof` is no longer in use, when
    this returns either way."""
    end = time.monotonic() + limit
    with open(log, "wb") as errs:
        # --mute-audio: the units build the page's real AudioContext (the
        # audio stage, the alert player), and nothing a test does may ever
        # sound on this machine's speakers. The sound tests render into
        # OfflineAudioContexts, which never reach an output at all; this is
        # the second lock on the same door.
        proc = subprocess.Popen(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--mute-audio",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
             "--disable-backgrounding-occluded-windows",
             f"--user-data-dir={prof}", "--remote-debugging-port=0", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errs,
            # TMPDIR inside the profile: Chromium keeps a directory of its own in
            # TMPDIR and removes it only on a clean exit, so a Chromium that is
            # signalled would leave one behind in /tmp on every run.
            env=dict(os.environ, TMPDIR=prof), start_new_session=True)
    page = None
    try:
        port = listening(proc, prof, end, log)
        page = Devtools(port, page_path(port, end))
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
    except (OSError, RuntimeError) as e:
        raise Failed(str(e), (page.heard if page else []) or tail(log)) from None
    finally:
        if page:
            page.close()
        stop(proc)


def run_units(exe, share, tests, files, limit=LIMIT):
    """Serve a copy of `share` with the modules `files` of `tests` beside it,
    run them in one page of `exe`, and return the page's
    {"passed": n, "failed": [...]}. Raises Failed. However it ends, what it
    started is stopped and what it wrote is removed."""
    # A COPY of share/, so nothing here can leave a test page in the repo.
    work = tempfile.mkdtemp()
    prof = tempfile.mkdtemp()
    srv = None
    try:
        copy = os.path.join(work, "share")
        shutil.copytree(share, copy)
        shutil.copytree(tests, os.path.join(copy, "_tests"))
        with open(os.path.join(copy, "_run.html"), "w", encoding="utf-8") as f:
            f.write(RUNNER % json.dumps(files))
        port = free_port()
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                "--bind", "127.0.0.1"], cwd=copy,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        title = drive(exe, prof, f"http://127.0.0.1:{port}/_run.html",
                      os.path.join(work, "chromium.log"), limit)
        try:
            return json.loads(title[len("RESULT "):])
        except ValueError:
            raise Failed(f"the page's result could not be read: {title[:200]!r}") from None
    finally:
        if srv:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()
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
    # A killed runner must still take Chromium down with it: the signal becomes
    # an exit, and the exit runs the finally blocks that stop it.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
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
