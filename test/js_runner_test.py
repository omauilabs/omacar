#!/usr/bin/env python3
"""The JavaScript runner's own machinery, held to what it promises.

test/js_test.py reads its result out of a real Chromium over the DevTools
protocol, with a WebSocket client small enough to live in that file. That
client, and the waiting around it, are code that can be wrong in ways the 557
tests it carries would only ever show as "the runner page never finished" --
on the one machine where it happened, a car tablet, which is the worst place to
find out. So:

  the WebSocket frames        against the RFC's own worked examples
  the DevTools client         against a scripted server: pings, a message in
                              two fragments, events before the answer, an
                              error, a crash
  a page that answers late    a Worker, on the real clock, after the page's
                              virtual time has fast-forwarded past a budget
                              that --dump-dom would have been dumped at
  a page that never answers   is a FAIL that names the test it is stuck in,
                              inside the limit
  what it leaves behind       no Chromium, no scratch directory -- after a
                              timeout, a browser that dies at once, and a
                              runner that is killed

SKIPPED LOUDLY without chromium (the frames and the client need none), like the
suite it tests.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import js_test as js  # noqa: E402

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


# ------------------------------------------------------------------ the frames
def frames():
    hello = bytes([0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58])       # RFC 6455, 5.7
    check("the accept key is the RFC's own example",
          js.ws_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
    check("a masked \"Hello\" is the RFC's bytes",
          js.ws_encode(b"Hello", mask=hello[:4]) == bytes([0x81, 0x85]) + hello)
    check("and reads back as what it hid",
          js.ws_decode(bytes([0x81, 0x85]) + hello) == (True, 1, b"Hello", 11))
    check("an unmasked server \"Hello\" reads",
          js.ws_decode(bytes([0x81, 0x05]) + b"Hello") == (True, 1, b"Hello", 7))
    check("a message in two fragments reads as two frames",
          [js.ws_decode(bytes([0x01, 0x03]) + b"Hel"), js.ws_decode(bytes([0x80, 0x02]) + b"lo")]
          == [(False, 1, b"Hel", 5), (True, 0, b"lo", 4)])
    check("a ping is opcode 9", js.ws_decode(bytes([0x89, 0x05]) + b"Hello")[1] == 9)
    check("a 256-byte payload uses the 16-bit length",
          js.ws_decode(bytes([0x82, 0x7E, 0x01, 0x00]) + b"x" * 256) == (True, 2, b"x" * 256, 260))
    big = bytes([0x82, 0x7F]) + (65536).to_bytes(8, "big") + b"y" * 65536
    check("a 64 KiB payload uses the 64-bit length", js.ws_decode(big) == (True, 2, b"y" * 65536, len(big)))
    check("every length survives encode and decode",
          all(js.ws_decode(js.ws_encode(b"z" * n))[2] == b"z" * n for n in (0, 1, 125, 126, 65535, 65536)))
    part = bytes([0x82, 0x7E, 0x01, 0x00]) + b"x" * 256
    check("half a frame is not a frame, at any cut", all(js.ws_decode(part[:i]) is None for i in range(len(part))))
    check("and two in one buffer read one at a time",
          js.ws_decode(bytes([0x81, 0x01]) + b"a" + bytes([0x81, 0x01]) + b"b") == (True, 1, b"a", 3))


# ----------------------------------------------------------------- the client
def server(script):
    """A DevTools server on a free port that speaks the WebSocket handshake and
    then calls script(conn, message) for each command. Returns (port, thread,
    frames the client sent that were not commands)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    other = []

    def out(conn, payload, opcode=1, fin=True):
        conn.sendall(bytes([(0x80 if fin else 0) | opcode, len(payload)]) + payload)

    def serve():
        conn, _ = srv.accept()
        with conn, srv:
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += conn.recv(4096)
            key = re.search(rb"Sec-WebSocket-Key: (\S+)", buf).group(1).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Accept: {js.ws_accept(key)}\r\n\r\n").encode())
            buf = bytearray(buf.partition(b"\r\n\r\n")[2])
            while True:
                got = js.ws_decode(buf)
                while not got:
                    more = conn.recv(4096)
                    if not more:
                        return
                    buf += more
                    got = js.ws_decode(buf)
                del buf[:got[3]]
                if got[1] != 1:
                    other.append(got[1:3])
                    continue
                script(lambda *a, **k: out(conn, *a, **k), json.loads(got[2]))

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], t, other


def client():
    def script(out, msg):
        method = msg["method"]
        if method == "Boom":
            out(json.dumps({"id": msg["id"], "error": {"message": "no such thing"}}).encode())
        elif method == "Crash":
            out(json.dumps({"method": "Inspector.targetCrashed", "params": {}}).encode())
        else:
            out(b"hi", opcode=0x9)                                     # a ping, first
            event = json.dumps({"method": "Runtime.consoleAPICalled",
                                "params": {"type": "error", "args": [{"value": "oops"}]}}).encode()
            half = len(event) // 2
            out(event[:half], opcode=1, fin=False)                     # an event, in two fragments
            out(event[half:], opcode=0, fin=True)
            out(json.dumps({"id": msg["id"], "result": {"echo": msg["params"]}}).encode())

    port, t, other = server(script)
    page = js.Devtools(port, "/devtools/page/x")
    try:
        check("a command's answer comes back through a ping and a fragmented event",
              page.call("Echo", {"a": 1}) == {"echo": {"a": 1}})
        check("the event on the way is heard", list(page.heard) == ["console.error: oops"])
        for _ in range(100):                                         # the server reads it after it has sent
            if (0xA, b"hi") in other:
                break
            time.sleep(0.02)
        check("and the ping was answered with a pong of its own body", (0xA, b"hi") in other)
        try:
            page.call("Boom")
            bad("a protocol error raises")
        except RuntimeError as e:
            check("a protocol error raises, with its message", "no such thing" in str(e))
        try:
            page.call("Crash")
            bad("a crashed page raises")
        except ConnectionError as e:
            check("a crashed page raises at once, not at the limit", "crashed" in str(e))
        try:
            page._message(time.monotonic() + 0.2)
            bad("silence is a timeout")
        except TimeoutError:
            ok("silence is a timeout")
    finally:
        page.close()
    t.join(2)


# --------------------------------------------------------------------- browser
def procs_using(path):
    """Process ids whose command line names `path` (Linux)."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if path.encode() in f.read():
                    found.append(int(pid))
        except OSError:
            pass
    return found


class Scratch:
    """Every temp dir the runner makes lands in one directory of ours, so what
    it left behind can be looked at."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.was = tempfile.tempdir
        tempfile.tempdir = self.dir
        return self

    def __exit__(self, *_):
        tempfile.tempdir = self.was
        shutil.rmtree(self.dir, ignore_errors=True)

    def left(self):
        return sorted(os.listdir(self.dir)), procs_using(self.dir) if os.path.isdir("/proc") else []


def suite(files):
    """A tests dir holding `files` ({name: source}), and an empty share."""
    tests, share = tempfile.mkdtemp(), tempfile.mkdtemp()
    for name, src in files.items():
        with open(os.path.join(tests, name), "w", encoding="utf-8") as f:
            f.write(src)
    return tests, share


def run(exe, files, limit=30):
    """(result or Failed, seconds, what was left behind, the scratch directory)."""
    tests, share = suite(files)
    try:
        with Scratch() as s:
            t0 = time.monotonic()
            try:
                got = js.run_units(exe, share, tests, sorted(files), limit)
            except js.Failed as e:
                got = e
            return got, time.monotonic() - t0, s.left(), s.dir
    finally:
        shutil.rmtree(tests, ignore_errors=True)
        shutil.rmtree(share, ignore_errors=True)


# What --dump-dom could never wait for: an answer that comes from a thread the page's
# virtual clock knows nothing of, while a timer keeps the page's idle time running
# on. The page's clock has to pass the old 30 s budget for the wait to prove it.
LATE = """export default [["a background thread's answer, after more virtual time than the old budget", async () => {
  const spin = setInterval(() => {}, 1000);
  const t0 = performance.now();
  const key = await crypto.subtle.importKey("raw", new Uint8Array(16), "PBKDF2", false, ["deriveBits"]);
  await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-512", salt: new Uint8Array(16),
                                   iterations: 1500000 }, key, 256);
  clearInterval(spin);
  const virtual = performance.now() - t0;
  if (virtual < 30000) throw new Error("only " + Math.round(virtual) + " virtual ms passed");
}]];
"""


def browser_tests(exe):
    got, _, left, _ = run(exe, {
        "a.test.js": 'export default [["adds", () => {}], ["fails", () => { throw new Error("boom"); }]];',
        "b.test.js": "export default [ this is not javascript",
    })
    check("a page's results come back: one pass, the failing test named, the broken module named",
          not isinstance(got, js.Failed) and got["passed"] == 1
          and got["failed"][0] == "a.test.js :: fails :: boom"
          and got["failed"][1].startswith("b.test.js :: import :: "))
    check("and it leaves no scratch directory and no Chromium behind", left == ([], []))

    got, secs, left, _ = run(exe, {"late.test.js": LATE})
    check(f"a page that answers late, from a thread virtual time cannot see, is waited for ({secs:.1f} s)",
          not isinstance(got, js.Failed) and got == {"passed": 1, "failed": []})

    got, secs, left, _ = run(exe, {"stuck.test.js": 'export default [["never resolves", () => new Promise(() => {})]];'},
                             limit=8)
    check(f"a page that never finishes fails inside its limit ({secs:.0f} s of 8)",
          isinstance(got, js.Failed) and secs < 8 + 12)
    check("and says which test it is stuck in",
          isinstance(got, js.Failed) and "stuck.test.js :: never resolves" in str(got))
    check("and reports whether the page was visible",
          isinstance(got, js.Failed) and any("visibility is 'visible'" in n for n in got.notes))
    check("and after the timeout, nothing is left: no scratch directory, no Chromium", left == ([], []))

    fake = tempfile.mkdtemp()
    dies = os.path.join(fake, "chromium")
    with open(dies, "w") as f:
        f.write("#!/bin/sh\necho \"TMPDIR=$TMPDIR\" >&2\necho 'a fatal thing' >&2\nexit 3\n")
    os.chmod(dies, 0o755)
    got, secs, left, scratch = run(dies, {"a.test.js": "export default [];"}, limit=30)
    shutil.rmtree(fake, ignore_errors=True)
    check(f"a browser that dies at once is reported at once, with what it said ({secs:.1f} s of 30)",
          isinstance(got, js.Failed) and "status 3" in str(got) and got.notes[1:] == ["a fatal thing"] and secs < 10)
    check("its own temp directory is inside the profile, so removing the profile removes it",
          isinstance(got, js.Failed) and got.notes[0].startswith("TMPDIR=" + scratch + os.sep))
    check("and that leaves nothing behind either", left == ([], []))


def killed(exe):
    """The runner is sent SIGTERM (a CI timeout, a Ctrl-C at a terminal that
    forwards it) with Chromium up: Chromium goes with it."""
    if not os.path.isdir("/proc"):
        return
    tests, share = suite({"stuck.test.js": 'export default [["never resolves", () => new Promise(() => {})]];'})
    with Scratch() as s:
        prog = ("import sys; sys.path.insert(0, %r); import js_test; js_test.TESTS, js_test.SHARE = %r, %r; "
                "sys.exit(js_test.main())" % (os.path.dirname(os.path.abspath(js.__file__)), tests, share))
        p = subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=dict(os.environ, TMPDIR=s.dir))
        try:
            for _ in range(200):                                 # Chromium is up when it names our scratch dir
                if procs_using(s.dir) or p.poll() is not None:
                    break
                time.sleep(0.1)
            time.sleep(1)
            p.send_signal(signal.SIGTERM)
            try:
                code = p.wait(20)
            except subprocess.TimeoutExpired:
                code = None
        finally:
            if p.poll() is None:
                p.kill()
        left = s.left()
    shutil.rmtree(tests, ignore_errors=True)
    shutil.rmtree(share, ignore_errors=True)
    check(f"a runner sent SIGTERM exits (status {code})", code == 143)
    check("and takes its Chromium and its scratch directories with it", left == ([], []))


def main():
    print("\n  The JavaScript runner's own machinery\n")
    frames()
    client()
    exe = js.browser()
    if not exe:
        print("\n    (skipping the browser half: no chromium here. `sudo pacman -S chromium` to\n"
              "     have it mean something.)\n")
    else:
        browser_tests(exe)
        killed(exe)
    print(f"\n  {fails} failed\n" if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
