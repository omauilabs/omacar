#!/usr/bin/env python3
"""The JavaScript runner's own machinery, held to what it promises.

test/js_test.py reads its result out of a real Chromium over the DevTools
protocol, on a pipe, with a client small enough to live in that file. That
client, and the waiting and cleaning up around it, are code that can be wrong in
ways the 557 tests it carries would only ever show as "the runner page never
finished" -- on the one machine where it happened, a car tablet, which is the
worst place to find out. So:

  the DevTools client          against a scripted Chromium on two pipes: an event
                               and an answer split across writes, a page that
                               opens late, a protocol error, a crash, a Chromium
                               that hangs up, silence, a message bigger than a pipe
  a page that answers late     on the real clock, from a thread the page's
                               virtual time cannot see, after more virtual time
                               than --dump-dom's budget
  a page that never answers    is a FAIL that names its test and says whether the
                               page was visible and focused, inside the limit
  a browser that dies at once  is reported at once, with what it said, and its
                               TMPDIR is its profile
  what it leaves behind        no Chromium, no scratch directory, no server
                               thread -- after a timeout, a browser that dies at
                               once, and the runner being sent SIGTERM, SIGHUP
                               (an ssh session dropping) and SIGINT
  a runner that is killed      SIGKILL cannot be caught, so Chromium has to
                               leave by itself when its pipe closes

SKIPPED LOUDLY without chromium (the client needs none), like the suite it tests.
The checks that look for Chromium's processes read /proc, so off Linux they say
they are skipped rather than pass without looking.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import js_test as js  # noqa: E402

fails = 0
PROC = os.path.isdir("/proc")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


# ------------------------------------------------------------------ the client
def frame(obj):
    return json.dumps(obj).encode() + b"\0"


def scripted():
    """A scripted Chromium, on the two pipes a Devtools speaks over. Returns
    (the Devtools, what the script has been sent, a function to stop it)."""
    cmd_r, cmd_w = os.pipe()
    ans_r, ans_w = os.pipe()
    sent, state, closed = [], {"polls": 0}, set()

    def close(fd):
        if fd not in closed:                    # a descriptor number, closed twice, may be someone else's by then
            closed.add(fd)
            os.close(fd)

    def out(data):
        try:
            os.write(ans_w, data)
        except OSError:
            pass

    def script(msg):
        method = msg["method"]
        if method == "Target.getTargets":
            state["polls"] += 1
            targets = [] if state["polls"] == 1 else [{"targetId": "ui", "type": "browser_ui"},
                                                       {"targetId": "pg", "type": "page"}]
            out(frame({"id": msg["id"], "result": {"targetInfos": targets}}))
        elif method == "Target.attachToTarget":
            out(frame({"id": msg["id"], "result": {"sessionId": "S1"}}))
        elif method == "Echo":
            event = frame({"method": "Runtime.consoleAPICalled", "sessionId": "S1",
                           "params": {"type": "error", "args": [{"value": "oops"}]}})
            both = event + frame({"id": msg["id"], "result": {"echo": msg["params"]}})
            at = 0
            for cut in (5, len(event) - 1, len(event) + 3):                # mid-event, before the NUL, mid-answer
                out(both[at:cut])
                at = cut
                time.sleep(0.01)
            out(both[at:])
        elif method == "Boom":
            out(frame({"id": msg["id"], "error": {"message": "no such thing"}}))
        elif method == "Crash":
            out(frame({"method": "Inspector.targetCrashed", "params": {}}))
        elif method == "Bye":
            close(ans_w)

    def serve():
        buf = b""
        while True:
            try:
                more = os.read(cmd_r, 65536)
            except OSError:
                return
            if not more:
                return
            buf += more
            while b"\0" in buf:
                head, _, buf = buf.partition(b"\0")
                sent.append(json.loads(head))
                script(sent[-1])

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    page = js.Devtools(ans_r, cmd_w)

    def stop():
        page.close()                            # its end of the commands pipe: the script reads EOF and stops
        t.join(2)
        close(cmd_r)
        close(ans_w)
    return page, sent, stop


def client():
    page, sent, stop = scripted()
    try:
        page.attach(time.monotonic() + 5)
        check("it waits for the page to open, and attaches to the page and not the browser's own UI",
              page.session == "S1" and sent[-1]["params"] == {"targetId": "pg", "flatten": True}
              and sum(m["method"] == "Target.getTargets" for m in sent) == 2)
        check("commands go to the page's session once attached, and not before",
              "sessionId" not in sent[0] and page.call("Echo", {"a": 1}) == {"echo": {"a": 1}}
              and sent[-1]["sessionId"] == "S1")
        check("an event and an answer split across writes, mid-message and either side of the NUL, are read whole",
              list(page.heard) == ["console.error: oops"])
        big = "x" * 300000
        check("a message bigger than a pipe goes and comes back",
              page.call("Echo", {"big": big}, timeout=10) == {"echo": {"big": big}})
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
        t0 = time.monotonic()
        try:
            page.call("Silence", timeout=0.3)
            bad("silence is a timeout")
        except TimeoutError:
            check("silence is a timeout, on time", 0.25 < time.monotonic() - t0 < 2)
        t0 = time.monotonic()
        try:
            page.call("Bye", timeout=10)
            bad("a Chromium that hangs up is a connection error")
        except ConnectionError:
            check("a Chromium that hangs up is a connection error at once, not a timeout",
                  time.monotonic() - t0 < 2)
    finally:
        stop()


# --------------------------------------------------------------------- browser
def procs_using(path):
    """Process ids whose command line names `path` (Linux only)."""
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

    def entries(self):
        return sorted(os.listdir(self.dir))

    def procs(self):
        return procs_using(self.dir) if PROC else []


def suite(files):
    """A tests dir holding `files` ({name: source}), and an empty share."""
    tests, share = tempfile.mkdtemp(), tempfile.mkdtemp()
    for name, src in files.items():
        with open(os.path.join(tests, name), "w", encoding="utf-8") as f:
            f.write(src)
    return tests, share


def run(exe, files, limit=30):
    """(result or Failed, seconds, what was left behind, the scratch directory):
    the left-behind being (the scratch directory's entries, the processes naming
    it, the threads this process gained)."""
    tests, share = suite(files)
    try:
        with Scratch() as s:
            threads = threading.active_count()
            t0 = time.monotonic()
            try:
                got = js.run_units(exe, share, tests, sorted(files), limit)
            except js.Failed as e:
                got = e
            secs = time.monotonic() - t0
            return got, secs, (s.entries(), s.procs(), threading.active_count() - threads), s.dir
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

STUCK = 'export default [["never resolves", () => new Promise(() => {})]];'
CLEAN = ([], [], 0)


def browser_tests(exe):
    got, _, left, _ = run(exe, {
        "a.test.js": 'export default [["adds", () => {}], ["fails", () => { throw new Error("boom"); }]];',
        "b.test.js": "export default [ this is not javascript",
    })
    check("a page's results come back: one pass, the failing test named, the broken module named",
          not isinstance(got, js.Failed) and got["passed"] == 1
          and got["failed"][0] == "a.test.js :: fails :: boom"
          and got["failed"][1].startswith("b.test.js :: import :: "))
    check("and it leaves no scratch directory, no Chromium and no server thread behind", left == CLEAN)

    got, secs, left, _ = run(exe, {"late.test.js": LATE})
    check(f"a page that answers late, from a thread virtual time cannot see, is waited for ({secs:.1f} s)",
          not isinstance(got, js.Failed) and got == {"passed": 1, "failed": []})

    got, secs, left, _ = run(exe, {"stuck.test.js": STUCK}, limit=8)
    check(f"a page that never finishes fails inside its limit ({secs:.0f} s of 8)",
          isinstance(got, js.Failed) and secs < 8 + 12)
    check("and says which test it is stuck in",
          isinstance(got, js.Failed) and "stuck.test.js :: never resolves" in str(got))
    check("and says the page was visible and focused: what the tablet's fix is for",
          isinstance(got, js.Failed) and "the page's visibility is 'visible', and it is focused" in got.notes)
    check("and after the timeout, nothing is left: no scratch directory, no Chromium, no server thread", left == CLEAN)

    fake = tempfile.mkdtemp()
    dies = os.path.join(fake, "chromium")
    with open(dies, "w") as f:
        f.write("#!/bin/sh\n"
                'for a in "$@"; do case $a in --user-data-dir=*) echo "PROFILE=${a#*=}" >&2;; esac; done\n'
                'echo "TMPDIR=$TMPDIR" >&2\necho "a fatal thing" >&2\nexit 3\n')
    os.chmod(dies, 0o755)
    got, secs, left, scratch = run(dies, {"a.test.js": "export default [];"}, limit=30)
    shutil.rmtree(fake, ignore_errors=True)
    said = dict(n.split("=", 1) for n in getattr(got, "notes", []) if "=" in n)
    check(f"a browser that dies at once is reported at once, with its status and what it said ({secs:.1f} s of 30)",
          isinstance(got, js.Failed) and "status 3" in str(got) and "a fatal thing" in got.notes and secs < 10)
    check("its TMPDIR is its own profile, so removing the profile removes what it keeps there",
          said.get("TMPDIR") and said.get("TMPDIR") == said.get("PROFILE")
          and said["PROFILE"].startswith(scratch + os.sep))
    check("and that leaves nothing behind either", left == CLEAN)


def signalled(exe, sig, name, exits):
    """The runner is sent `sig` with Chromium up and its page stuck. Chromium
    must have been up (a test that signals a runner that never got that far
    proves nothing), and must be gone afterwards, and -- for a signal the runner
    can hear -- so must every scratch directory. SIGKILL cannot be heard, so it
    is Chromium's own exit on its closed pipe that is being looked for."""
    tests, share = suite({"stuck.test.js": STUCK})
    with Scratch() as s:
        prog = ("import sys; sys.path.insert(0, %r); import js_test; js_test.TESTS, js_test.SHARE = %r, %r; "
                "sys.exit(js_test.main())" % (os.path.dirname(os.path.abspath(js.__file__)), tests, share))
        p = subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=dict(os.environ, TMPDIR=s.dir))
        seen = []
        try:
            for _ in range(400):                                 # Chromium is up when it names our scratch dir
                seen = s.procs()
                if seen or p.poll() is not None:
                    break
                time.sleep(0.1)
            if seen:
                time.sleep(1)                                    # past its start-up, into the page
                p.send_signal(sig)
            try:
                code = p.wait(30)
            except subprocess.TimeoutExpired:
                code = None
            t0 = time.monotonic()
            while s.procs() and time.monotonic() - t0 < 10:      # Chromium's own exit takes a moment
                time.sleep(0.1)
            gone = time.monotonic() - t0
            procs, entries = s.procs(), s.entries()
        finally:
            if p.poll() is None:
                p.kill()
    shutil.rmtree(tests, ignore_errors=True)
    shutil.rmtree(share, ignore_errors=True)
    check(f"{name}: Chromium was up when the runner was signalled (else there is nothing to check)", bool(seen))
    check(f"{name}: the runner exits (status {code})", code in exits)
    check(f"{name}: no Chromium is left ({gone:.1f} s after the runner went)", not procs)
    if sig != signal.SIGKILL:
        check(f"{name}: and no scratch directory", entries == [])


def main():
    print("\n  The JavaScript runner's own machinery\n")
    client()
    exe = js.browser()
    if not exe:
        print("\n    (skipping the browser half: no chromium here. `sudo pacman -S chromium` to\n"
              "     have it mean something.)\n")
    else:
        if not PROC:
            print("\n    (no /proc here, so the checks below cannot look for a Chromium left running,\n"
                  "     and the signal checks are skipped: they would pass without looking.)\n")
        browser_tests(exe)
        if PROC:
            for sig, name, exits in ((signal.SIGTERM, "SIGTERM", (143,)),
                                     (signal.SIGHUP, "SIGHUP, as when an ssh session drops", (129,)),
                                     (signal.SIGINT, "SIGINT", (130, -signal.SIGINT)),
                                     (signal.SIGKILL, "SIGKILL", (-signal.SIGKILL,))):
                signalled(exe, sig, name, exits)
    print(f"\n  {fails} failed\n" if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
