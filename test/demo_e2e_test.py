#!/usr/bin/env python3
"""tools/demo_e2e.py's no-footage logic (hardening D), against stand-ins.

The walk itself is six minutes of real time per size and is run by hand (and
not here), but what it EXPECTS of a demo with no clips is held here, so a tool
that quietly expected the wrong thing cannot pass a demo that is wrong:

  which steps the tour must jump over: those whose `needs` is clips, when the
    demo has none, read from the real demo/data/tour.json (only Cameras);
  whether a folder holds footage (an empty file is none), and whether the demo's
    own GET /api/cams says a camera is recording (a loopback server of this test's
    own: nothing is started and no browser is used);
  follow_tour, on a scripted page: a tour that jumps over Cameras passes when no
    footage is expected, and is a fault when footage is; a tour that SHOWS Cameras
    with no footage expected is a fault too.
"""

import contextlib
import http.server
import io
import json
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "test"))
import demo_e2e as E  # noqa: E402

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
        bad(f"{msg}\n          got  {got!r}\n          want {want!r}")


with open(os.path.join(ROOT, "demo", "data", "tour.json"), encoding="utf-8") as f:
    STEPS = json.load(f)["steps"]

head("which steps are jumped over")
check("no footage: only Cameras (step 4)", E.skipped_steps(STEPS, False), {3})
check("footage: none", E.skipped_steps(STEPS, True), set())
check("steps that need nothing are never jumped over",
      E.skipped_steps([{"id": "a"}, {"id": "b", "needs": None}], False), set())
check("a step that needs something else is not this tool's to expect",
      E.skipped_steps([{"id": "a", "needs": "wifi"}], False), set())
check("the Cameras step is the one that says so",
      [(s["id"], s.get("needs")) for s in STEPS if s.get("needs")], [("cameras", "clips")])

head("whether a folder holds footage")
with tempfile.TemporaryDirectory() as d:
    check("an empty folder: none", E.footage_in(d), False)
    open(os.path.join(d, "front.mp4"), "wb").close()
    check("an empty file is none (the camera feed reads it as missing)", E.footage_in(d), False)
    with open(os.path.join(d, "front.mp4"), "wb") as f:
        f.write(b"\0" * 16)
    check("a clip with something in it: footage", E.footage_in(d), True)
    check("a folder that is not there: none", E.footage_in(os.path.join(d, "nope")), False)
    with open(os.path.join(d, "notes.txt"), "w") as f:
        f.write("x")
    os.remove(os.path.join(d, "front.mp4"))
    check("other files are not clips", E.footage_in(d), False)

head("whether the demo's own GET /api/cams says a camera is recording")
ANSWER = {"body": b"{}", "status": 200}


class Cams(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(ANSWER["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(ANSWER["body"])))
        self.end_headers()
        self.wfile.write(ANSWER["body"])

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Cams)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
off = {"recording": False}
on = {"recording": True}
for what, doc, want in (
    ("nothing running", {"running": False, "roles": {"front": off, "rear": off, "cabin": off}}, False),
    ("running, no camera recording", {"running": True, "roles": {"front": off, "rear": off, "cabin": off}}, False),
    ("a camera that says recording while the recorder is not running", {"running": False, "roles": {"front": on}}, False),
    ("one camera recording", {"running": True, "roles": {"front": on, "rear": off, "cabin": off}}, True),
    ("all three", {"running": True, "roles": {"front": on, "rear": on, "cabin": on}}, True),
    ("an answer with no roles", {"running": True}, False),
):
    ANSWER["body"] = json.dumps(doc).encode()
    check(f"{what}: {want}", E.footage_up(port), want)
ANSWER["body"] = b"not json"
check("an answer that is not JSON: none", E.footage_up(port), False)
srv.shutdown()
srv.server_close()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
check("a server that is not there: none", E.footage_up(closed), False)

head("follow_tour on a scripted page")


class Page:
    """A page whose tour enters `order` one step at a time, each for its own length,
    and then ends on Home. value() answers as demo_e2e's STATE_JS and the rest do."""

    def __init__(self, steps, order):
        self.look = 0
        self.script = []
        t = 1_000_000
        for i in order:
            self.script.append(["running", i, 0.5, steps[i]["go"], t + 500])
            t += int(float(steps[i]["secs"]) * 1000)
        self.script.append(["idle", -1, 0, "#home", t + 500])

    def value(self, expr, timeout=15, wait=False):
        if "tour.start()" in expr:
            return 1_000_000
        if "location.hash" in expr and "tour" not in expr:
            return "#home"
        n = min(self.look, len(self.script) - 1)
        self.look += 1
        return self.script[n]


def walk(order, skip):
    watch = E.Watch()
    with contextlib.redirect_stdout(io.StringIO()):          # follow_tour says each step as it opens
        entries, problems = E.follow_tour(Page(STEPS, order), STEPS, watch, grace=5, skip=skip)
    return [e[0] for e in entries], problems


everything = list(range(len(STEPS)))
without_cameras = [i for i in everything if i != 3]
got, problems = walk(without_cameras, {3})
check("no footage, and the tour jumps over Cameras: the steps run as 1-3 and 5-11, and no problem", (got, problems),
      (without_cameras, []))
got, problems = walk(without_cameras, set())
check("footage expected but Cameras jumped over: a fault, naming the steps",
      (got, len(problems), "the steps ran as" in problems[0] and "not [1, 2, 3, 4, 5" in problems[0]),
      (without_cameras, 1, True))
got, problems = walk(everything, {3})
check("no footage expected but Cameras shown: a fault, saying it should have been jumped over",
      (got, len(problems), "jumped over: no footage" in problems[0]), (everything, 1, True))
got, problems = walk(everything, set())
check("footage, and every step shown: no problem (as it always was)", (got, problems), (everything, []))

print()
print("  all passed" if not fails else f"  {fails} FAILED")
sys.exit(1 if fails else 0)
