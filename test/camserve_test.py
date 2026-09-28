#!/usr/bin/env python3
"""The recorder's API through the real server: the JSON routes, the live
MJPEG, and clips with HTTP Range -- which lib/serve.py never had, and which a
<video> needs before it can seek. Scratch folders only; no camera, no
recorder."""

import http.client
import json
import os
import shutil
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
