#!/usr/bin/env python3
"""The cameras, end to end, on a machine with one real camera: the box's C920
as the cabin, and `sim` test pictures for front and rear.

It starts the recorder and the server on scratch folders, then checks:
- recording: three roles, the real one not simulated;
- the live pictures;
- hard braking from a scripted live.json, and Mark event;
- a finished one-minute clip per role that starts on a keyframe;
- playback with Range;
- the locks both events leave behind.
Then it takes screenshots of the Cameras tab and Home through tools/shoot.py.

Every wait is a poll with a deadline, so a slow machine takes longer rather
than failing. About three minutes. Not in test/all.sh, because it needs a
camera.

    python3 tools/cams_e2e.py OUT_DIR
"""

import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "test"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import camstore  # noqa: E402
import shoot  # noqa: E402
from app_test import free_port, python_for_server, wait_for_port  # noqa: E402

ROLES = ("front", "rear", "cabin")
fails = 0


def check(msg, cond):
    global fails
    if not cond:
        fails += 1
    print(f"    {'ok  ' if cond else 'FAIL'}  {msg}")


def until(secs, fn, every=1.0):
    """fn() again and again until it returns something true or `secs` have
    passed. Returns its last value."""
    end = time.time() + secs
    while True:
        v = fn()
        if v or time.time() >= end:
            return v
        time.sleep(every)


def main(argv):
    out = argv[1] if len(argv) > 1 else "/tmp/omacar-e2e"
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="omacar-e2e-")
    env = dict(os.environ, OMACAR_VIDEOS=os.path.join(scratch, "videos"),
               XDG_RUNTIME_DIR=os.path.join(scratch, "run"),
               XDG_STATE_HOME=os.path.join(scratch, "state"),
               XDG_CONFIG_HOME=os.path.join(scratch, "config"))
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    live = os.path.join(env["XDG_STATE_HOME"], "omacar", "live.json")
    os.makedirs(os.path.dirname(live))

    def say(kph):
        tmp = live + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"connected": True, "t": time.time(), "values": {"SPEED": kph}}, f)
        os.replace(tmp, live)

    say(0.0)
    log = open(os.path.join(out, "recorder.log"), "w")
    rec = subprocess.Popen([sys.executable, os.path.join(ROOT, "lib", "cams.py"), "sim"],
                           env=env, stdout=log, stderr=subprocess.STDOUT)
    port = free_port()
    srv = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port),
                            os.path.join(ROOT, "share")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def get_json(path):
        try:
            return json.loads(req("GET", path)[1])
        except (OSError, ValueError):
            return None

    def probe(path, *q):
        return subprocess.run(["ffprobe", "-v", "error", *q, path], capture_output=True, text=True).stdout.strip()

    try:
        check("the server is up", wait_for_port(port))
        print("\n  recording")

        def up():
            ov = get_json("/api/cams")
            return ov if ov and ov["running"] and all(ov["roles"][r]["live"] for r in ROLES) else None
        ov = until(60, up)
        check("the recorder is up, with three live roles", bool(ov))
        if not ov:
            return 1
        cab = ov["roles"]["cabin"]
        check(f"the cabin is the C920, not a test picture ({cab['device']}, {cab['mode']})",
              cab["sim"] is False and "C920" in (cab["device"] or ""))
        check("front and rear are simulated, and say so",
              ov["roles"]["front"]["sim"] and ov["roles"]["rear"]["sim"] and ov["sim"])
        for r in ROLES:
            st, body = req("GET", f"/api/cams/{r}/live?frames=3")
            check(f"{r}: three live JPEGs", st == 200 and body.count(b"\xff\xd8") >= 3)

        print("\n  events")
        # Hard braking: 100 km/h for two seconds, then down to 40 at 40 km/h a
        # second. The recorder reads live.json five times a second, so any
        # second of the drop it sees is well past the 16 km/h threshold.
        for _ in range(20):
            say(100.0)
            time.sleep(0.1)
        for k in range(15):
            say(100.0 - 4 * (k + 1))
            time.sleep(0.1)
        braked = until(10, lambda: any(e["kind"] == "hard-braking"
                                       for e in (get_json("/api/cams/clips") or {}).get("events", [])), every=0.5)
        say(0.0)
        check("hard braking was caught from the car's speed", braked)
        st, body = req("POST", "/api/cams/mark", body="{}")
        check("Mark event answered", st == 200 and json.loads(body)["kind"] == "marked")

        print("\n  waiting, up to 150 s, for two clips of every camera and both events locked")

        def settled():
            doc = get_json("/api/cams/clips")
            if not doc:
                return None
            enough = all(sum(1 for c in doc["clips"] if c["role"] == r) >= 2 for r in ROLES)
            kinds = {e["kind"]: e for e in doc["events"]}
            locked = all(kinds.get(k, {}).get("state") == "locked" for k in ("hard-braking", "marked"))
            return doc if enough and locked else None
        doc = until(150, settled, every=5) or get_json("/api/cams/clips") or {"clips": [], "events": []}
        for r in ROLES:
            mine = sorted((c for c in doc["clips"] if c["role"] == r), key=lambda c: c["start"])
            check(f"{r}: at least two clips", len(mine) >= 2)
            if not mine:
                continue
            path = camstore.clip_path(r, mine[0]["file"], root=env["OMACAR_VIDEOS"])
            dur = float(probe(path, "-show_entries", "format=duration", "-of", "csv=p=0") or 0)
            key = probe(path, "-select_streams", "v", "-read_intervals", "%+#1",
                        "-show_entries", "frame=key_frame", "-of", "csv=p=0").startswith("1")
            check(f"{r}: the first clip is a whole minute ({dur:.1f} s) and starts on a keyframe",
                  abs(dur - 60) <= 1.5 and key)
        cabins = sorted((c for c in doc["clips"] if c["role"] == "cabin"), key=lambda c: c["start"])
        if cabins:
            st, body = req("GET", f"/api/cams/clip/cabin/{cabins[0]['file']}", headers={"Range": "bytes=0-1023"})
            check("playback can seek: a Range request is a 206", st == 206 and len(body) == 1024)
        by_kind = {e["kind"]: e for e in doc["events"]}
        for k in ("hard-braking", "marked"):
            e = by_kind.get(k, {})
            check(f"{k}: locked, on every camera",
                  e.get("state") == "locked" and {f.split("/")[0] for f in e.get("files", [])} == set(ROLES))
        # A clip two events overlap lives in the first event's folder and is
        # listed by both, so the check is that every listed clip resolves
        # inside a locked folder, not that each event has a folder of its own.
        paths = [camstore.clip_path(*f.split("/", 1), root=env["OMACAR_VIDEOS"])
                 for e in by_kind.values() for f in e.get("files", [])]
        check("and every locked clip is in a locked folder, out of the loop's reach",
              bool(paths) and all(p and f"{os.sep}locked{os.sep}" in p for p in paths))

        print("\n  on screen")
        os.environ.update(env)      # shoot.py's server reads the same scratch folders
        pngs, doms = shoot.shoot(out, [("cameras-landscape", "?still=1#cameras", (1368, 968)),
                                       ("cameras-portrait", "?still=1#cameras", (912, 1424)),
                                       ("home-landscape", "?still=1#home", (1368, 968))],
                                 doms=["?still=1#cameras"])
        for name, png in pngs.items():
            check(f"screenshot {name}", bool(png))
        dom = doms["?still=1#cameras"]
        check("three feeds, all recording", dom.count('data-rec="1"') == 3)
        check("the badge says SIMULATED", 'cam-badge warn">SIMULATED</span>' in dom)
        check("the timeline shows both events", dom.count('class="cam-marker"') >= 2)
    finally:
        srv.terminate()
        rec.send_signal(signal.SIGINT)
        try:
            rec.wait(timeout=20)
        except subprocess.TimeoutExpired:
            rec.kill()
        log.close()
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'every check held' if not fails else str(fails) + ' failed'}; screenshots in {out}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
