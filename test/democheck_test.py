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
each named on the last line; and that the check writes nothing.
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
        json.dump({"version": 1, "layers": {}}, f)
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


def shim(bindir, kiosk):
    """A pgrep that says the kiosk is (or is not) running, and logs its args."""
    os.makedirs(bindir, exist_ok=True)
    path = os.path.join(bindir, "pgrep")
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n"
                f'echo "$@" >> "{bindir}/pgrep.log"\n'
                + ("echo 4242\nexit 0\n" if kiosk else "exit 1\n"))
    os.chmod(path, 0o755)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(work, private, kiosk=True, port=None, extra=None, real_pgrep=False):
    bindir = os.path.join(work, "bin")
    shim(bindir, kiosk)
    path = os.environ.get("PATH", "") if real_pgrep else bindir + os.pathsep + os.environ.get("PATH", "")
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

        # Clips missing.
        for role in ("rear", "cabin-drowsy"):
            os.remove(os.path.join(private, "demo", "clips", role + ".mp4"))
        r, lines = run(work, private)
        check("clips missing: named, and not ready: clips",
              any("stock/owner clips missing: rear, cabin-drowsy" in ln for ln in lines)
              and lines[-1] == "not ready: clips", r.stdout)
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

        # The voice, the map, the car picture, Vehicle's X-ray.
        os.remove(os.path.join(private, "demo", "voice", "drowsy-l2.wav"))
        os.remove(os.path.join(private, "demo", "crz-home.png"))
        os.remove(os.path.join(private, "crz-xray.png"))
        with open(os.path.join(private, "demo", "map.json"), "w", encoding="utf-8") as f:
            f.write('{"version": 1, "layers": ')
        r, lines = run(work, private)
        check("a voice line, a broken map, the car picture and the X-ray: each named",
              lines[-1] == "not ready: map, voice, car picture, vehicle picture", r.stdout)
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
        try:
            time.sleep(0.3)
            r, lines = run(work, private, real_pgrep=True)
            check("a test's stand-in, or a look-alike profile, is not the live kiosk",
                  lines[-1] == "not ready: kiosk", r.stdout)
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
