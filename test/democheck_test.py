#!/usr/bin/env python3
"""lib/democheck.py, `omacar demo check`, against a scratch tree.

The venue check reads the demo's private media, the REAL settings folder (for
the live volume pin), the process list (for the live kiosk) and, when the demo
is on, the demo's server. Here every one of those is a stand-in: a scratch
private tree whose songs are sparse files of the pinned sizes, a scratch HOME,
a `pgrep` on PATH that answers as told, and a loopback server of this test's
own. Nothing here reads the real machine's files or plays anything.

Checks that everything present is "ready"; that a truncated song, missing
clips, the volume pin, no kiosk and a demo whose server does not answer are
each named on the last line; that the road cameras have saved stills (in the
demo's state or the real one) and the map was built from the drive; that every
clip present is H.264 (an `ffprobe` on PATH that answers as told), so the
Cameras tab's player can play it; that the screen will not sleep mid-demo
(Omarchy's stay-awake indicator, or the live kiosk that holds it); and that the
check writes nothing.
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "lib", "democheck.py")
PINS = os.path.join(ROOT, "demo", "data", "media-pins.json")
VOICE = os.path.join(ROOT, "demo", "data", "voice.json")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond, detail=""):
    if cond:
        ok(msg)
    else:
        bad(msg + (f"\n          {detail}" if detail else ""))


def build(private):
    """A private tree with everything the demo needs, the songs as sparse files
    of exactly their pinned sizes."""
    with open(PINS, encoding="utf-8") as f:
        pins = json.load(f)
    radio = os.path.join(private, "omarchy-radio")
    os.makedirs(radio)
    tracks = []
    for name, size in pins["radio"].items():
        with open(os.path.join(radio, name), "wb") as f:
            f.truncate(size)
        tracks.append({"title": name, "artist": "Ryan R. Hughes", "file": name})
    with open(os.path.join(radio, "playlist.json"), "w", encoding="utf-8") as f:
        json.dump({"station": "omarchy", "tracks": tracks}, f)
    demo = os.path.join(private, "demo")
    os.makedirs(os.path.join(demo, "voice"))
    os.makedirs(os.path.join(demo, "clips"))
    with open(os.path.join(demo, "drive.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "points": [[0, 36.7, -121.8, 0, 0, 0, 0]]}, f)
    with open(os.path.join(demo, "map.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "origin": [36.7, -121.8], "layers": {}}, f)
    with open(VOICE, encoding="utf-8") as f:
        for line in json.load(f):
            with open(os.path.join(demo, "voice", line["id"] + ".wav"), "wb") as w:
                w.write(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\0" * 64)
    for role in ("front", "rear", "cabin", "cabin-drowsy"):
        with open(os.path.join(demo, "clips", role + ".mp4"), "wb") as f:
            f.write(b"\0\0\0\x18ftypmp42" + b"\0" * 100)
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    for p in (os.path.join(private, "omacar-logo.png"), os.path.join(demo, "crz-home.png"),
              os.path.join(private, "crz-xray.png")):
        with open(p, "wb") as f:
            f.write(png)
    return pins


def shim(bindir, kiosk, launcher=True, pgrep=True, ffprobe=True):
    """A pgrep that says the kiosk's Chromium is (or is not) running, and
    separately that `omacar kiosk launcher` is (or is not), and logs its args;
    and an ffprobe that calls a clip H.264 unless the file says otherwise
    (a stand-in: the test's clips are a few bytes, not video). The file's text
    says what the real one would have found: HEVC, or BROKEN for one ffprobe
    cannot read. `pgrep=False` leaves the real pgrep in charge, `ffprobe=False`
    leaves none at all (on a PATH that has no other)."""
    os.makedirs(bindir, exist_ok=True)
    path = os.path.join(bindir, "pgrep")
    if pgrep:
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n"
                    f'echo "$@" >> "{bindir}/pgrep.log"\n'
                    'case "$*" in\n'
                    + "  *'kiosk launcher'*) " + ("echo 4343; exit 0" if launcher else "exit 1") + " ;;\n"
                    + "  *) " + ("echo 4242; exit 0" if kiosk else "exit 1") + " ;;\n"
                    "esac\n")
        os.chmod(path, 0o755)
    elif os.path.exists(path):
        os.remove(path)
    probe = os.path.join(bindir, "ffprobe")
    if not ffprobe:
        if os.path.exists(probe):
            os.remove(probe)
        return
    with open(probe, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n"
                'for last; do :; done\n'
                'if grep -q BROKEN "$last" 2>/dev/null; then exit 1; fi\n'
                'codec=h264\n'
                'if grep -q HEVC "$last" 2>/dev/null; then codec=hevc; fi\n'
                'echo \'{"streams":[{"codec_name":"\'$codec\'","width":1280,"height":720,"avg_frame_rate":"30/1"}]}\'\n')
    os.chmod(probe, 0o755)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(work, private, kiosk=True, port=None, extra=None, real_pgrep=False, launcher=True, path=None,
        ffprobe=True):
    bindir = os.path.join(work, "bin")
    shim(bindir, kiosk, launcher=launcher, pgrep=not real_pgrep, ffprobe=ffprobe)
    # The real pgrep is found later on the PATH than the shims' folder; `path`
    # is the whole PATH, for a run with no ffprobe at all.
    path = path or bindir + os.pathsep + os.environ.get("PATH", "")
    env = {"PATH": path,
           "HOME": os.path.join(work, "home"), "LANG": "C.UTF-8",
           "OMACAR_DEMO_PORT": str(port or free_port()), **(extra or {})}
    r = subprocess.run([sys.executable, TOOL, "--private", private],
                       capture_output=True, text=True, env=env, timeout=60)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return r, lines


def listing(top):
    out = {}
    for d, _, files in os.walk(top):
        for n in files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            out[os.path.relpath(p, top)] = (st.st_size, st.st_mtime_ns)
    return out


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/demo.html":
            body = b'<!doctype html><script type="module" src="demo/js/boot.js"></script>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


def main():
    print("\n  omacar demo check (lib/democheck.py), against a scratch tree\n")
    with open(PINS, encoding="utf-8") as f:
        pins = json.load(f)
    check("the pins name the station's seven songs", len(pins.get("radio", {})) == 7, str(pins.get("radio")))

    work = tempfile.mkdtemp(prefix="democheck-")
    try:
        private = os.path.join(work, "private")
        build(private)
        home = os.path.join(work, "home")
        os.makedirs(os.path.join(home, ".config", "omarchy"))
        # A road camera's saved still (beside its .json) in the REAL state.
        real_img = os.path.join(home, ".local", "state", "omacar", "roadcams", "img")
        os.makedirs(real_img)
        for name in ("d5-a.jpg", "d5-a.json"):
            with open(os.path.join(real_img, name), "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\0" * 64)
        before = listing(work)

        r, lines = run(work, private)
        check("everything present: ready, exit 0", r.returncode == 0 and lines and lines[-1] == "ready",
              r.stdout + r.stderr)
        check("one line per check, before the verdict", len(lines) >= 9, "\n".join(lines))
        log = os.path.join(work, "bin", "pgrep.log")
        with open(log, encoding="utf-8") as f:
            asked = f.read()
        profile = os.path.join(home, ".local", "share", "omacar", "kiosk-profile")
        check("the kiosk is looked for by its own profile, exactly",
              "--user-data-dir=" + profile.replace(".", "\\.") + "( |$)" in asked, asked)
        check("the demo off: its server is not asked for", any("off" in ln for ln in lines if "server" in ln),
              "\n".join(lines))
        check("clips play: every clip is H.264",
              any(ln.split()[:3] == ["ok", "clips", "play"] for ln in lines), "\n".join(lines))
        check("stay awake: the live kiosk's launcher holds it, and pgrep is asked for it by its command line",
              any(ln.split()[:3] == ["ok", "stay", "awake"] for ln in lines)
              and "-f -- omacar kiosk launcher" in asked, "\n".join(lines) + "\n" + asked)
        check("road cameras: the real state's saved still is found",
              any(ln.split()[:3] == ["ok", "road", "cameras"] for ln in lines), "\n".join(lines))
        check("map fits drive: the map's origin is the drive's first point",
              any(ln.split()[:4] == ["ok", "map", "fits", "drive"] for ln in lines), "\n".join(lines))

        # Road cameras: the saved stills, in the demo's own state (where `demo
        # on` copied them) or in the real one. The real folder is put aside
        # whole and back, so the files keep their times.
        aside = real_img + ".aside"
        os.rename(real_img, aside)
        try:
            r, lines = run(work, private)
            check("no saved road-camera stills: not ready: road cameras",
                  r.returncode == 1 and lines[-1] == "not ready: road cameras", r.stdout + r.stderr)
            check("and it says what to do",
                  any("no saved road-camera stills: run omacar-demo on once while online" in ln
                      for ln in lines), r.stdout)
            demo_state = os.path.join(home, ".local", "state", "omacar-demo", "state")
            demo_img = os.path.join(demo_state, "omacar", "roadcams", "img")
            os.makedirs(demo_img)
            with open(os.path.join(demo_img, "d5-b.json"), "wb") as f:
                f.write(b"{}")
            r, lines = run(work, private)
            check("a camera's .json is not a still: not ready: road cameras",
                  lines[-1] == "not ready: road cameras", r.stdout)
            with open(os.path.join(demo_img, "d5-b.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\0" * 64)
            r, lines = run(work, private)
            check("a still in the demo's own state: ready", lines[-1] == "ready", r.stdout)
            # From inside the demo's environment, where XDG_STATE_HOME is the demo's.
            r, lines = run(work, private, extra={"XDG_STATE_HOME": demo_state})
            check("and it is found from inside the demo's environment too",
                  lines[-1] == "ready", r.stdout)
            os.remove(os.path.join(demo_img, "d5-b.jpg"))
            os.rename(aside, real_img)
            aside = None
            r, lines = run(work, private, extra={"XDG_STATE_HOME": demo_state})
            check("from there the real state's still counts, not the demo's folder as the real one",
                  lines[-1] == "ready", r.stdout)
        finally:
            if aside:
                os.rename(aside, real_img)
            shutil.rmtree(os.path.join(home, ".local", "state", "omacar-demo"), ignore_errors=True)

        # Map fits drive: the map's origin is the drive's first point, within 1e-5.
        map_path = os.path.join(private, "demo", "map.json")

        def with_origin(origin):
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "origin": origin, "layers": {}}, f)
            return run(work, private)

        r, lines = with_origin([36.700004, -121.800004])
        check("an origin 4e-6 off the drive's first point: ready", lines[-1] == "ready", r.stdout)
        for what, origin in (("latitude", [36.7001, -121.8]), ("longitude", [36.7, -121.8001])):
            r, lines = with_origin(origin)
            check(f"a map from another drive ({what} 1e-4 off): not ready: map fits drive",
                  r.returncode == 1 and lines[-1] == "not ready: map fits drive", r.stdout)
            check("and it says how to rebuild it",
                  any("map.json was built from another drive: rebuild with tools/demo_map.py" in ln
                      for ln in lines), r.stdout)
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "layers": {}}, f)
        r, lines = run(work, private)
        check("a map with no origin at all: not ready: map fits drive",
              lines[-1] == "not ready: map fits drive", r.stdout)
        with_origin([36.7, -121.8])

        # A song cut short.
        song = sorted(pins["radio"])[0]
        path = os.path.join(private, "omarchy-radio", song)
        with open(path, "r+b") as f:
            f.truncate(pins["radio"][song] - 1)
        r, lines = run(work, private)
        check("a truncated song: not ready: songs", r.returncode == 1 and lines[-1] == "not ready: songs",
              r.stdout + r.stderr)
        check("and which", any(song in ln for ln in lines[:-1]), "\n".join(lines))
        with open(path, "r+b") as f:
            f.truncate(pins["radio"][song])
        os.remove(os.path.join(private, "omarchy-radio", sorted(pins["radio"])[1]))
        r, lines = run(work, private)
        check("a missing song: not ready: songs", lines[-1] == "not ready: songs", r.stdout)
        with open(os.path.join(private, "omarchy-radio", sorted(pins["radio"])[1]), "wb") as f:
            f.truncate(pins["radio"][sorted(pins["radio"])[1]])

        # Clips play: the Cameras tab's <video> plays H.264, and the camera feed
        # copies the clips as they are (cams.py demo_warnings, which this reuses).
        clip = os.path.join(private, "demo", "clips", "cabin.mp4")
        with open(clip, "rb") as f:
            good = f.read()
        with open(clip, "wb") as f:
            f.write(good + b"HEVC")
        r, lines = run(work, private)
        check("an HEVC clip: not ready: clips play", r.returncode == 1 and lines[-1] == "not ready: clips play",
              r.stdout + r.stderr)
        check("and it names the clip, its codec and the fix",
              any("cabin.mp4 is HEVC, not H264" in ln and "ffmpeg -i cabin.mp4 -c:v libx264" in ln for ln in lines),
              r.stdout)
        check("the other three are not named",
              not any(n + ".mp4" in ln for ln in lines for n in ("front", "rear", "cabin-drowsy")
                      if "play" in ln), r.stdout)
        with open(clip, "wb") as f:
            f.write(good + b"BROKEN")
        r, lines = run(work, private)
        check("a clip ffprobe cannot read: named, and not ready: clips play",
              any("cabin.mp4 could not be read by ffprobe" in ln for ln in lines)
              and lines[-1] == "not ready: clips play", r.stdout)
        with open(clip, "wb") as f:
            f.write(good)
        bindir = os.path.join(work, "bin")
        r, lines = run(work, private, ffprobe=False, path=bindir)
        check("no ffprobe on the machine: every clip is named, not ready: clips play",
              lines[-1] == "not ready: clips play"
              and sum("could not be read by ffprobe" in ln for ln in lines) == 1
              and all(n + ".mp4 could not be read" in " ".join(lines) for n in ("front", "rear", "cabin", "cabin-drowsy")),
              r.stdout)
        r, lines = run(work, private)
        check("the clip whole again: ready", lines[-1] == "ready", r.stdout)

        # Clips missing.
        for role in ("rear", "cabin-drowsy"):
            os.remove(os.path.join(private, "demo", "clips", role + ".mp4"))
        r, lines = run(work, private)
        check("clips missing: named, and not ready: clips",
              any("stock/owner clips missing: rear, cabin-drowsy" in ln for ln in lines)
              and lines[-1] == "not ready: clips", r.stdout)
        check("the two that are there are still read: clips play is ok",
              any(ln.split()[:3] == ["ok", "clips", "play"] for ln in lines), r.stdout)
        with open(os.path.join(private, "demo", "clips", "front.mp4"), "ab") as f:
            f.write(b"HEVC")
        r, lines = run(work, private)
        check("a present clip in the wrong codec is named beside the missing ones",
              lines[-1] == "not ready: clips, clips play" and any("front.mp4 is HEVC" in ln for ln in lines), r.stdout)
        with open(os.path.join(private, "demo", "clips", "front.mp4"), "wb") as f:
            f.write(good)
        held = {}
        for role in ("front", "cabin"):
            path = os.path.join(private, "demo", "clips", role + ".mp4")
            with open(path, "rb") as f:
                held[path] = f.read()
            os.remove(path)
        r, lines = run(work, private)
        check("none at all: clips fails, and clips play has nothing to ask (it is not a failure)",
              lines[-1] == "not ready: clips" and any(ln.split()[:3] == ["--", "clips", "play"] for ln in lines),
              r.stdout)
        for path, data in held.items():
            with open(path, "wb") as f:
                f.write(data)
        # The launcher's OMACAR_DEMO_CLIPS (another folder of footage) is where
        # the camera feed reads, so it is where the check looks.
        other = os.path.join(work, "clips")
        os.makedirs(other)
        for role in ("front", "rear", "cabin", "cabin-drowsy"):
            with open(os.path.join(other, role + ".mp4"), "wb") as f:
                f.write(b"\0" * 64)
        r, lines = run(work, private, extra={"OMACAR_DEMO_CLIPS": other})
        check("clips in OMACAR_DEMO_CLIPS, as the camera feed is given: ready",
              lines[-1] == "ready" and any(other in ln for ln in lines), r.stdout)
        for role in ("rear", "cabin-drowsy"):
            with open(os.path.join(private, "demo", "clips", role + ".mp4"), "wb") as f:
                f.write(b"\0" * 64)

        # The real ffprobe, on clips the real ffmpeg makes: the question the camera
        # feed asks (cams.py probe_clip), with no stand-in between. Skipped, and
        # said, where there is no ffmpeg (or none that writes H.264).
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        real = os.path.join(work, "realclips")
        os.makedirs(real)

        def make(role, codec):
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "testsrc=size=160x120:rate=10", "-t", "1", "-c:v", codec,
                                "-pix_fmt", "yuv420p", os.path.join(real, role + ".mp4")],
                               capture_output=True, timeout=60)
            return r.returncode == 0
        if ffmpeg and ffprobe and make("front", "libx264") and make("rear", "mpeg4"):
            r, lines = run(work, private, extra={"OMACAR_DEMO_CLIPS": real}, path=os.environ.get("PATH", ""))
            play = [ln for ln in lines if ln.split()[:3] in (["FAIL", "clips", "play"], ["ok", "clips", "play"])]
            check("real ffprobe: an MPEG-4 clip is named, and the H.264 one beside it is not",
                  len(play) == 1 and play[0].split()[0] == "FAIL" and "rear.mp4 is MPEG4, not H264" in play[0]
                  and "front.mp4" not in play[0], "\n".join(lines))
            os.remove(os.path.join(real, "rear.mp4"))
            r, lines = run(work, private, extra={"OMACAR_DEMO_CLIPS": real}, path=os.environ.get("PATH", ""))
            check("real ffprobe: an H.264 clip plays",
                  any(ln.split()[:3] == ["ok", "clips", "play"] for ln in lines), "\n".join(lines))
        else:
            print("    (skipping the real-ffprobe check: no ffmpeg that writes H.264 here)")
        shutil.rmtree(real)

        # The voice, the map, the car picture, Vehicle's X-ray.
        os.remove(os.path.join(private, "demo", "voice", "drowsy-l2.wav"))
        os.remove(os.path.join(private, "demo", "crz-home.png"))
        os.remove(os.path.join(private, "crz-xray.png"))
        with open(os.path.join(private, "demo", "map.json"), "w", encoding="utf-8") as f:
            f.write('{"version": 1, "layers": ')
        r, lines = run(work, private)
        check("a voice line, a broken map, the car picture and the X-ray: each named",
              lines[-1] == "not ready: map, voice, car picture, vehicle picture", r.stdout)
        check("a broken map is `map`'s to name: the comparison is skipped, not failed",
              any(ln.split()[:4] == ["--", "map", "fits", "drive"] for ln in lines), r.stdout)
        check("the X-ray says how it reaches the tablet",
              any("crz-xray.png" in ln and "omacar assets push" in ln for ln in lines), r.stdout)
        check("the voice line by name", any("drowsy-l2" in ln for ln in lines), r.stdout)
        build_again = os.path.join(work, "private2")
        build(build_again)
        private = build_again

        # The live volume pin, in the REAL settings folder.
        pin = os.path.join(home, ".config", "omarchy", "omacar-audio.json")
        with open(pin, "w", encoding="utf-8") as f:
            json.dump({"pin": True}, f)
        r, lines = run(work, private)
        check("the volume pin on: not ready: volume pin", lines[-1] == "not ready: volume pin", r.stdout)
        check("and it says how to turn it off", any("omacar audio off" in ln for ln in lines), r.stdout)
        with open(pin, "w", encoding="utf-8") as f:
            json.dump({"pin": False}, f)
        r, lines = run(work, private)
        check("the pin written but off: ready", lines[-1] == "ready", r.stdout)

        # No live kiosk.
        r, lines = run(work, private, kiosk=False)
        check("no live kiosk: not ready: kiosk", lines[-1] == "not ready: kiosk", r.stdout)

        # Stay awake: the screen must not sleep mid-demo. The live kiosk holds
        # Omarchy's indicator while it runs (bin/omacar kiosk_idle_hold), so either
        # the indicator is there or the kiosk's launcher is running.
        ind_dir = os.path.join(home, ".local", "state", "omarchy", "indicators")
        ind = os.path.join(ind_dir, "stay-awake")
        r, lines = run(work, private, launcher=False)
        check("no launcher and no indicator: not ready: stay awake",
              r.returncode == 1 and lines[-1] == "not ready: stay awake", r.stdout + r.stderr)
        check("and it says what to do",
              any("the screen may sleep during the demo: start the live kiosk, or turn on Omarchy's stay-awake" in ln
                  for ln in lines), r.stdout)
        os.makedirs(ind_dir)
        with open(ind, "w", encoding="utf-8"):
            pass
        try:
            r, lines = run(work, private, launcher=False)
            check("Omarchy's stay-awake indicator alone: ready", lines[-1] == "ready", r.stdout)
            check("and the line says which",
                  any(ln.split()[:3] == ["ok", "stay", "awake"] and "stay-awake" in ln for ln in lines), r.stdout)
            r, lines = run(work, private, launcher=False, kiosk=False)
            check("with no kiosk either, only the kiosk is not ready", lines[-1] == "not ready: kiosk", r.stdout)
            # From inside the demo's environment, whose XDG_STATE_HOME is the demo's:
            # the machine's own indicator is the one that counts.
            demo_state = os.path.join(home, ".local", "state", "omacar-demo", "state")
            r, lines = run(work, private, launcher=False, extra={"XDG_STATE_HOME": demo_state})
            check("and found from inside the demo's environment", lines[-1] == "ready", r.stdout)
        finally:
            os.remove(ind)
            os.rmdir(ind_dir)
            os.rmdir(os.path.dirname(ind_dir))
        r, lines = run(work, private, launcher=True, kiosk=False)
        check("the launcher's process alone holds it: only the kiosk is not ready",
              lines[-1] == "not ready: kiosk", r.stdout)

        # XDG_DATA_HOME moves the kiosk's profile, as it does in bin/omacar.
        data = os.path.join(work, "data-home")
        open(os.path.join(work, "bin", "pgrep.log"), "w").close()
        run(work, private, extra={"XDG_DATA_HOME": data})
        with open(os.path.join(work, "bin", "pgrep.log"), encoding="utf-8") as f:
            asked = f.read()
        check("with XDG_DATA_HOME, the kiosk's profile is under it",
              "--user-data-dir=" + os.path.join(data, "omacar", "kiosk-profile").replace(".", "\\.") in asked, asked)

        # The real pgrep, against stand-ins: a test's fake kiosk under /tmp (the
        # box had one left running, and it passed for the live kiosk) and a
        # profile that only starts with the real one's name do not count; a
        # process on the real profile does.
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        decoys = [subprocess.Popen(sleeper + [f"--user-data-dir={os.path.join(work, 'tmp', 'omacar', 'kiosk-profile')}"]),
                  subprocess.Popen(sleeper + [f"--user-data-dir={profile}-old", "--app=x"])]
        # (and a stand-in for the kiosk's launcher, so that the screen is held awake)
        try:
            time.sleep(0.3)
            holder = subprocess.Popen(sleeper + ["omacar", "kiosk", "launcher"])
            decoys.append(holder)
            time.sleep(0.3)
            r, lines = run(work, private, real_pgrep=True)
            check("a test's stand-in, or a look-alike profile, is not the live kiosk",
                  lines[-1] == "not ready: kiosk", r.stdout)
            check("and a process whose command line holds `omacar kiosk launcher` holds the screen awake",
                  any(ln.split()[:3] == ["ok", "stay", "awake"] for ln in lines), r.stdout)
            live = subprocess.Popen(sleeper + [f"--user-data-dir={profile}", "--app=http://127.0.0.1/app.html"])
            try:
                time.sleep(0.3)
                r, lines = run(work, private, real_pgrep=True)
                check("a process on the real kiosk profile is", lines[-1] == "ready", r.stdout)
            finally:
                live.kill()
                live.wait()
        finally:
            for d in decoys:
                d.kill()
                d.wait()

        # The demo on: its server must answer /demo.html.
        active = os.path.join(home, ".local", "state", "omacar-demo")
        os.makedirs(active)
        with open(os.path.join(active, "ACTIVE"), "w", encoding="utf-8"):
            pass
        port = free_port()
        r, lines = run(work, private, port=port)
        check("the demo on and no server: not ready: demo server",
              lines[-1] == "not ready: demo server", r.stdout)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Page)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            r, lines = run(work, private, port=port)
            check("the demo on and its server answering: ready", lines[-1] == "ready", r.stdout)
        finally:
            srv.shutdown()
            srv.server_close()

        # Read only: nothing it looked at was written, and nothing was added.
        os.remove(os.path.join(active, "ACTIVE"))
        os.rmdir(active)
        after = listing(work)
        ignore = lambda d: {k: v for k, v in d.items()
                            if not k.startswith(("bin" + os.sep, "private" + os.sep, "private2" + os.sep,
                                                     "clips" + os.sep))
                            and k != os.path.join("home", ".config", "omarchy", "omacar-audio.json")}
        check("it wrote nothing in HOME", ignore(after) == ignore(before),
              str(set(ignore(after)) ^ set(ignore(before))))
        still = listing(private)
        r, lines = run(work, private)
        check("nor in the private tree", listing(private) == still)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n  {'all passed' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
