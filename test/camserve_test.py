#!/usr/bin/env python3
"""The recorder's API through the real server: the JSON routes, the live
MJPEG, and clips with HTTP Range -- which lib/serve.py never had, and which a
<video> needs before it can seek. Scratch folders only; no camera, no
recorder."""

import http.client
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import free_port, python_for_server, wait_for_port  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="omacar-camserve-")
ENV = dict(os.environ,
           OMACAR_VIDEOS=os.path.join(SCRATCH, "videos"),
           XDG_RUNTIME_DIR=os.path.join(SCRATCH, "run"),
           XDG_CONFIG_HOME=os.path.join(SCRATCH, "config"),
           XDG_STATE_HOME=os.path.join(SCRATCH, "state"),
           XDG_DATA_HOME=os.path.join(SCRATCH, "data"))
os.environ.update(ENV)

import cams      # noqa: E402
import camstore  # noqa: E402

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


PORT = free_port()
SRV = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
                        str(PORT), os.path.join(ROOT, "share")],
                       env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not wait_for_port(PORT):
    SRV.kill()
    sys.exit("  FAIL  the server never came up")


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def server_threads():
    """The server subprocess's own thread count (Linux's /proc), or None
    where /proc does not exist. ThreadingHTTPServer gives every connection
    its own thread, so this is the most direct proof there is that a
    connection's handler actually ended, rather than an abandoned client
    merely going quiet while a write loop somewhere spins on."""
    try:
        with open(f"/proc/{SRV.pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Thread"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


try:
    head("a clip, whole and in ranges")
    T = time.time() - 120
    NAME = camstore.clip_name(T)
    DATA = bytes(range(256)) * 4 + b"x" * 1000          # 2024 bytes
    os.makedirs(os.path.join(ENV["OMACAR_VIDEOS"], "front"))
    with open(os.path.join(ENV["OMACAR_VIDEOS"], "front", NAME), "wb") as f:
        f.write(DATA)
    URL = f"/api/cams/clip/front/{NAME}"
    st, h, body = req("GET", URL)
    check("the whole clip, and it says ranges are welcome",
          (st, len(body), h.get("accept-ranges"), h.get("content-type")),
          (200, 2024, "bytes", "video/mp4"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=0-99"})
    check("the first hundred bytes", (st, body, h.get("content-range")),
          (206, DATA[:100], "bytes 0-99/2024"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=2000-"})
    check("from an offset to the end", (st, body, h.get("content-range")),
          (206, DATA[2000:], "bytes 2000-2023/2024"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=-24"})
    check("the last 24 bytes", (st, body), (206, DATA[-24:]))
    check("an end past the size is clipped to it",
          req("GET", URL, headers={"Range": "bytes=2020-9999"})[2], DATA[2020:])
    st, h, _ = req("GET", URL, headers={"Range": "bytes=5000-"})
    check("a start past the end is 416, with the size", (st, h.get("content-range")),
          (416, "bytes */2024"))
    check("a multi-range request gets the whole file",
          camstore.parse_range("bytes=0-1,5-6", 100), None)
    check("a path that climbs out of the folder is refused",
          req("GET", "/api/cams/clip/front/..%2F..%2Fevents.json")[0], 404)
    check("a role that does not exist is refused", req("GET", f"/api/cams/clip/boot/{NAME}")[0], 404)

    head("a clip name can never be a path")
    # An UNENCODED '..': the route regex only matches a single path segment
    # of digits, dashes and one '.mp4', so a literal '..' segment never
    # reaches camstore.clip_path() at all -- this is the same defense as the
    # encoded case above, checked against the raw string a lazier client
    # might send.
    check("a bare .. is refused, not just an encoded one",
          req("GET", "/api/cams/clip/front/../../events.json")[0] in (400, 404), True)
    # An ABSOLUTE PATH in the name position, with its leading '/' escaped so
    # it arrives as literal text rather than starting a new path segment.
    check("an absolute path in the name position is refused",
          req("GET", "/api/cams/clip/front/%2Fetc%2Fpasswd")[0] in (400, 404), True)
    # clip_path() itself, directly: the route regex already stops a name
    # like this from reaching it over HTTP, but clip_path() is meant to
    # refuse it on its own account too, not merely be lucky that nothing
    # upstream ever passes it one.
    check("clip_path() refuses .. in the name on its own",
          camstore.clip_path("front", "../../etc/passwd"), None)
    check("clip_path() refuses an absolute path in the name on its own",
          camstore.clip_path("front", "/etc/passwd"), None)

    head("a symlink is refused, whichever way it points")
    FRONT_DIR = os.path.join(ENV["OMACAR_VIDEOS"], "front")
    OUTSIDE = os.path.join(SCRATCH, "outside-secret.mp4")
    with open(OUTSIDE, "wb") as f:
        f.write(b"not a clip; not yours")
    OUT_NAME = camstore.clip_name(T - 200)
    OUT_LINK = os.path.join(FRONT_DIR, OUT_NAME)
    os.symlink(OUTSIDE, OUT_LINK)
    try:
        check("a symlink leading out of the clip store is refused",
              req("GET", f"/api/cams/clip/front/{OUT_NAME}")[0], 404)
        check("and clip_path() itself never hands the link back",
              camstore.clip_path("front", OUT_NAME), None)
    finally:
        os.remove(OUT_LINK)
    IN_NAME = camstore.clip_name(T - 300)
    IN_LINK = os.path.join(FRONT_DIR, IN_NAME)
    os.symlink(os.path.join(FRONT_DIR, NAME), IN_LINK)          # points INSIDE the store
    try:
        check("a symlink to a clip inside the store is refused too -- "
              "a clip is never a link at all",
              req("GET", f"/api/cams/clip/front/{IN_NAME}")[0], 404)
        check("and clip_path() refuses that one too",
              camstore.clip_path("front", IN_NAME), None)
    finally:
        os.remove(IN_LINK)
    check("a normal clip still streams with Range, after all of that",
          req("GET", URL, headers={"Range": "bytes=0-9"})[2], DATA[:10])

    head("the JSON routes")
    st, _, body = req("GET", "/api/cams")
    ov = json.loads(body)
    check("GET /api/cams answers for every role", (st, sorted(ov["roles"])),
          (200, ["cabin", "front", "rear"]))
    check("the recorder is off, and it says so", ov["running"], False)
    check("storage counts the clip", ov["storage"]["used"], 2024)
    st, _, body = req("GET", "/api/cams/clips?role=front")
    clips = json.loads(body)["clips"]
    check("the timeline gets the clip by name, never by path",
          (st, [c["file"] for c in clips], "path" in clips[0]), (200, [NAME], False))
    check("an unknown role is a 400", req("GET", "/api/cams/clips?role=boot")[0], 400)
    st, _, body = req("POST", "/api/cams/lock", body=json.dumps({"t": T + 10}),
                      headers={"Content-Type": "application/json"})
    ev = json.loads(body)
    check("Save clip locks the window around the playhead",
          (st, ev["kind"], ev["files"]), (200, "saved", [f"front/{NAME}"]))
    check("and the clip still plays from its new folder",
          req("GET", URL, headers={"Range": "bytes=0-9"})[2], DATA[:10])
    st, _, body = req("POST", "/api/cams/mark", body="{}")
    check("Mark event marks now", (st, json.loads(body)["kind"]), (200, "marked"))
    check("a time that is not a number is refused",
          req("POST", "/api/cams/lock", body=json.dumps({"t": "soon"}))[0], 400)
    check("and so is one from last month",
          req("POST", "/api/cams/lock", body=json.dumps({"t": time.time() - 40 * 86400}))[0], 400)
    st, _, body = req("GET", "/api/cams/clips")
    check("the timeline sees both events",
          sorted(e["kind"] for e in json.loads(body)["events"]), ["marked", "saved"])

    head("the live picture, as MJPEG")
    os.makedirs(cams.run_dir(), exist_ok=True)
    J1, J2 = b"\xff\xd8one\xff\xd9", b"\xff\xd8second\xff\xd9"
    with open(cams.live_path("cabin"), "wb") as f:
        f.write(J1)
    # The second picture is written only once the first has been read back,
    # so the order cannot depend on how quickly the server gets going.
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request("GET", "/api/cams/cabin/live?frames=2")
    r = c.getresponse()
    st, h = r.status, {k.lower(): v for k, v in r.getheaders()}
    body = b""
    while J1 not in body:
        chunk = r.read1(256)
        if not chunk:
            break
        body += chunk
    with open(cams.live_path("cabin") + ".tmp", "wb") as fh:
        fh.write(J2)
    os.replace(cams.live_path("cabin") + ".tmp", cams.live_path("cabin"))
    body += r.read()
    c.close()
    check("multipart, the way an <img> reads it", (st, h.get("content-type")),
          (200, "multipart/x-mixed-replace; boundary=omacarframe"))
    check("two whole frames, each with its length",
          (body.count(b"--omacarframe\r\n"), body.count(b"Content-Length: ")), (2, 2))
    check("in the order they were written", body.index(J1) < body.index(J2), True)
    check("a camera that does not exist is a 404", req("GET", "/api/cams/boot/live")[0], 404)

    head("the live route ends when the client disconnects")
    # A dropped tab or a phone that lost signal never sends a clean close --
    # it just stops reading, and its OS eventually tears the connection down
    # from underneath. cams.stream_live() only finds that out the next time
    # it tries to WRITE a frame, so this feeds it fresh ones after the drop
    # and asks whether the handler actually went away, rather than trusting
    # that a quiet client means a quiet server.
    #
    # A raw socket, not http.client: this needs SO_LINGER(0) for an ABORTIVE
    # close (a real RST, the way a lost link or a killed app looks, not a
    # polite FIN), and http.client gives no way to set that. A plain close()
    # was tried first and is exactly the flaky half-measure this avoids: on
    # loopback the write immediately after a graceful FIN often still
    # succeeds silently (the RST from the far side has not arrived yet), so
    # a test that writes exactly one frame and closes politely can pass or
    # fail depending on scheduling, not on whether the server actually
    # noticed. SO_LINGER(0) plus feeding several frames while polling closes
    # both gaps at once.
    with open(cams.live_path("rear"), "wb") as f:
        f.write(J1)
    before = server_threads()
    s = socket.create_connection(("127.0.0.1", PORT), timeout=20)
    s.sendall(b"GET /api/cams/rear/live HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    buf = b""
    while J1 not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    during = server_threads()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()

    def _new_frame(n):
        with open(cams.live_path("rear") + ".tmp", "wb") as f:
            f.write(J1 if n % 2 else J2)
        os.replace(cams.live_path("rear") + ".tmp", cams.live_path("rear"))

    if before is not None and during is not None:
        check("the connection got its own thread while it was live",
              during > before, True)
        deadline = time.time() + 5           # well under cams.LIVE_GIVE_UP (10 s):
        ended = False                        # this has to be the WRITE failing,
        n = 0                                # not the give-up timer catching up
        while time.time() < deadline:
            if server_threads() <= before:
                ended = True
                break
            _new_frame(n)
            n += 1
            time.sleep(0.3)
        check(f"and its thread exits within a few seconds of the drop, well "
              f"before the {cams.LIVE_GIVE_UP} s give-up timer would "
              f"(threads: before {before}, live {during})", ended, True)
    else:
        ok("no /proc here to count threads (not Linux); falling back to a "
           "responsiveness check below")
        for n in range(3):
            _new_frame(n)
            time.sleep(0.3)
    st3, _, _ = req("GET", "/api/cams")
    check("and the server answers everyone else right away, nothing wedged",
          st3, 200)
finally:
    SRV.terminate()
    try:
        SRV.wait(timeout=5)
    except subprocess.TimeoutExpired:
        SRV.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the recorder's API holds\n")
