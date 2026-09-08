#!/usr/bin/env python3
"""A picture actually reaches a canvas.

WHAT THIS PROVES, AND WHY IT IS WORTH A TEST OF ITS OWN.

The phone screen's picture crosses five separate things, and only the first of
them needs hardware:

    the adapter driver   lib/carlink.py, USB in, H.264 out
    the framing          self-delimiting records, so a chunk boundary is not
                         a torn frame
    the HTTP stream      one response, read as it arrives
    the decoder          WebCodecs, which is the browser's hardware decoder
    the canvas           the pixels

Every one of the last four can be wrong in a way that shows up as a black
rectangle, which is the same thing a missing adapter shows up as. So this
sends a recording down the identical path -- same records, same route, same
decoder, same canvas -- and asserts that pixels arrived. The day an adapter is
plugged in, a black screen means the driver and nothing else.

It also asserts the screen SAYS it is a recording, because a stand-in that
does not announce itself is the failure this project spends its time refusing.

SKIPPED LOUDLY without chromium or ffmpeg, like the shader test without a
compiler. Neither is a dependency of this project. A test that cannot run is
not a failure; a test that silently does nothing is.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browser():
    for name in ("chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def python_for_server():
    venv = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                        "venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


# The page under test. It is served from a directory that mirrors share/ by
# symlink, so the module graph is the real one and the repository is not
# written into by a test.
PROBE = """<!doctype html>
<meta charset="utf-8">
<title>phone probe</title>
<canvas id="c" width="800" height="640"></canvas>
<pre id="out">starting</pre>
<script type="module">
import { dongleSource } from "/js/omaplay/source.js";

const events = [];
const canvas = document.getElementById("c");
const out = document.getElementById("out");
let best = { unique: 0, w: 0, h: 0 };
let secondReader = 0;

// Faster than a car would ever play it, on purpose: the recording is paced by
// the server in real time, and this test is about whether the picture crosses
// the layers, not about how fast it does. Delivering it at once also keeps the
// whole run inside the headless browser's clock.
const src = dongleSource({ mode: "replay", loop: false, fps: 20 });
src.on((m) => { events.push(m); report(); });

function sample() {
  try {
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const seen = new Set();
    // Every 997th pixel: a prime stride, so a regular pattern in the picture
    // cannot be sampled in phase with itself and read as one flat colour.
    for (let i = 0; i < d.length; i += 4 * 997) {
      seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
      if (seen.size > 64) break;
    }
    if (seen.size > best.unique) {
      best = { unique: seen.size, w: canvas.width, h: canvas.height };
    }
  } catch (e) { /* the canvas may not be painted yet */ }
}

function report() {
  sample();
  out.textContent = JSON.stringify({
    best,
    stats: src.stats,
    route: (events.filter((e) => e.type === "route").pop() || {}).route || "",
    standinText: document.querySelector(".omaplay")
      ? document.querySelector(".omaplay").dataset.standin || "" : "",
    routes: events.filter((e) => e.type === "route").map((e) => e.route),
    secondReader,
    events: events.map((e) => e.type),
    replay: events.some((e) => e.replay === true),
    // The words the SOURCE said about itself, not the words the route said
    // when it was asked to start. They are different claims from different
    // files and only one of them travels with the picture.
    notes: events.filter((e) => e.note).map((e) => e.note),
    failure: (events.find((e) => e.type === "failure") || {}).error || "",
    hiccup: (events.find((e) => e.type === "hiccup") || {}).error || "",
    standin: document.documentElement.dataset.probeStandin || "",
  });
}

// HOLDING THE HEADLESS BROWSER'S CLOCK STILL.
//
// A headless browser runs on a virtual clock and dumps the page when its
// budget runs out. The budget stops advancing while a fetch is outstanding --
// but a fetch whose body is read as a stream stops counting the moment its
// headers land, so the recording, which is paced in real time by the server,
// was still arriving when the page was dumped. The first version of this test
// saw five frames of thirty-nine and one grey rectangle.
//
// So a second reader is opened on the same stream and its whole body awaited.
// That one does count, so the clock waits for the recording to finish, and the
// page is dumped after the last frame rather than during the first.
//
// It is not only a clock. Two readers on one session is a thing the server
// claims to support, and this is the only place that claim is exercised.
const ticking = setInterval(report, 60);
const running = src.start(canvas);
await new Promise((go) => { const off = src.on(() => { off(); go(); }); });
const second = fetch("/api/phone/video")
  .then((r) => r.arrayBuffer()).then((b) => b.byteLength).catch(() => 0);
const [, held] = await Promise.all([
  Promise.race([running, new Promise((go) => setTimeout(go, 25000))]),
  second,
]);
secondReader = held;

// AND THEN LET REAL TIME RUN.
//
// The trick above freezes the browser's virtual clock for the whole of the
// recording, which is what stops the page being dumped early -- and which also
// stops requestAnimationFrame, and with it the fallback decoder's painting.
// So once the stream is done, the clock is let go in short bursts: each of
// these is a real network round trip, and between them virtual time advances
// and animation frames fire. Forty of them is a fraction of a second of real
// time and plenty of frames.
for (let i = 0; i < 120 && best.unique < 8; i++) {
  await fetch("/api/phone", { cache: "no-store" }).catch(() => {});
  await new Promise((go) => requestAnimationFrame(go));
  report();
}
clearInterval(ticking);
report();
</script>
"""


def mirror_share(into):
    """share/, plus one page, without touching the repository."""
    os.makedirs(into, exist_ok=True)
    for name in os.listdir(SHARE):
        os.symlink(os.path.join(SHARE, name), os.path.join(into, name))
    with open(os.path.join(into, "probe.html"), "w", encoding="utf-8") as f:
        f.write(PROBE)
    return into


def make_clip(path):
    ff = shutil.which("ffmpeg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i",
         "testsrc=size=800x640:rate=20:duration=6",
         "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
         "-g", "20", "-f", "h264", path],
        capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(path)


def main():
    print("\n  A picture reaches the canvas\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0
    if not shutil.which("ffmpeg"):
        print("    (skipping: no ffmpeg here to make a recording with.\n"
              "     `sudo pacman -S ffmpeg` to have this test mean something.)\n")
        return 0

    tmp = tempfile.mkdtemp(prefix="omacar-phone-")
    state = os.path.join(tmp, "state", "omacar")
    clip = os.path.join(state, "phone-replay.h264")
    if not make_clip(clip):
        bad("ffmpeg would not make a test recording")
        return 1
    ok(f"a six-second recording, {os.path.getsize(clip)} bytes")

    served = mirror_share(os.path.join(tmp, "share"))
    port = free_port()
    profile = os.path.join(tmp, "profile")
    env = dict(os.environ,
               XDG_STATE_HOME=os.path.join(tmp, "state"),
               XDG_CONFIG_HOME=os.path.join(tmp, "config"))
    server = subprocess.Popen(
        [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
         str(port), served],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, env=env)
    try:
        for _ in range(60):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        else:
            bad("the loopback server never came up")
            return 1
        ok(f"server up on {port}")

        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={profile}", "--virtual-time-budget=30000",
             # The fallback decoder is a <video> element, and a browser that
             # will not start one without a click cannot be asked about it.
             "--autoplay-policy=no-user-gesture-required",
             "--enable-logging=stderr", "--dump-dom",
             f"http://127.0.0.1:{port}/probe.html"],
            capture_output=True, text=True, timeout=180)
        dom, log = r.stdout or "", r.stderr or ""

        m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
        if not m:
            bad("the probe page never reported")
            print("      " + (log.strip().splitlines() or ["(no log)"])[-1][:200])
            return 1
        raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">"))
        try:
            got = json.loads(raw)
        except ValueError:
            bad(f"the probe reported something unreadable: {raw[:120]}")
            return 1

        if os.environ.get("OMACAR_PHONE_DEBUG"):
            print("      " + json.dumps(got)[:900])
        stats = got.get("stats") or {}
        events = got.get("events") or []

        # WHAT COUNTS AS A PASS DEPENDS ON WHETHER THIS MACHINE CAN DECODE
        # H.264 AT ALL, and that is decided by whether a picture came back --
        # never assumed, and never read off a capability check, because the
        # capability check on a headless browser says yes and then decodes
        # nothing.
        decodes = stats.get("pictures", 0) > 0
        if decodes and got.get("failure"):
            bad(f"the stream failed: {got['failure']}")
        if stats.get("refused") or stats.get("rebuilds"):
            print(f"      (the decoder refused {stats.get('refused', 0)} units "
                  f"and was rebuilt {stats.get('rebuilds', 0)} times; the "
                  f"stream survived both, which is the point)")
        # THE FRAMING, END TO END. Every record that went in came out whole,
        # across however many chunks the kernel felt like using.
        check(f"{stats.get('video', 0)} video records crossed HTTP intact",
              stats.get("video", 0) >= 100)
        check("the stream ran to its end and closed", "unplugged" in events)
        check(f"a second reader saw the same stream "
              f"({got.get('secondReader', 0)} bytes)",
              got.get("secondReader", 0) > 10000)
        # AND IT SAID WHAT IT WAS. A stand-in that does not announce itself is
        # worse than no stand-in. This one is sticky, so a reader that arrives
        # after the announcement still gets it -- which is the case that used
        # to slip through.
        check("the source announced itself as a recording",
              got.get("replay") is True)
        check("and said so in words, on the stream itself",
              any("REPLAY" in n.upper() for n in got.get("notes") or []))

        # THE DECODER IS NOT ALWAYS THERE, AND SAYING SO IS THE POINT.
        #
        # A headless Chromium with no GPU accepts an H.264 configuration,
        # takes every chunk without complaint and hands back nothing: there is
        # no software fallback for this codec. isConfigSupported() answers yes.
        # Nothing throws. The canvas stays exactly as black as it would with no
        # adapter plugged in, which is why the app now says which of the two it
        # is -- and why this test asserts it said so rather than pretending the
        # picture was verified.
        if decodes:
            check("the decoder handed back a frame", "picture" in events)
            # THE HONESTY CHECK IN THE OTHER DIRECTION. Saying the picture
            # never arrived and then showing it is as much a lie as the
            # reverse, and it is the easier one to ship: the verdict is
            # decided on a timer and the picture turns up a moment later.
            check("and the app never claimed the picture had not arrived",
                  "undecodable" not in events)
            check(f"pixels reached the canvas ({got['best']['unique']} "
                  f"distinct colours sampled)", got["best"]["unique"] >= 8)
            check(f"the canvas took the recording's size "
                  f"({got['best']['w']}x{got['best']['h']})",
                  got["best"]["w"] == 800 and got["best"]["h"] == 640)
        else:
            print("\n    (this browser decoded none of it: headless Chromium\n"
                  "     has no H.264 decoder. The bytes arrived; the picture\n"
                  "     could not be verified HERE. On a machine with a GPU\n"
                  "     this is the assertion that matters.)\n")
            check("and the app said so rather than showing black",
                  "undecodable" in events)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  the picture crosses every layer but the adapter\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
