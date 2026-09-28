#!/usr/bin/env python3
"""The app actually starts in a browser.

THE BUG THIS WOULD HAVE CAUGHT, AND DID NOT.

`share/js/main.js` called maskVin() and imported it under a different name.
Nothing in this suite noticed, because nothing in this suite had ever RUN the
application -- the tests check that every file an import names exists, that the
stylesheet declares its tokens, that the shaders compile. Every one of those
passed while the app was dead on arrival: a red card reading "OmaCar could not
start · maskVin is not defined", found by plugging a tablet into a car.

Existing checks are static and this class of fault is not. A name that is used
but never bound is perfectly good syntax; `node --check` passes it, an asset
test passes it, and it fails the moment the line executes -- which, for that
line, meant the moment a real vehicle appeared and the bar painted.

So this one starts the server, loads the page in a headless browser, and asks
two questions a person would ask: did anything throw, and is the application
on the screen.

SKIPPED LOUDLY WITHOUT A BROWSER, like the shader test without a compiler.
Chromium is not a dependency of this project -- the app is meant to run in
whatever the owner has -- so a machine without one says so and passes. A test
that cannot run is not a failure; a test that silently does nothing is.
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

# Appended to a COPY of app.html. It drives the store directly rather than
# faking a feed, because what is being measured is layout under a speed value,
# not the network.
# A RAW STRING: the probe is JavaScript, and `\d` in a plain Python string is
# an invalid escape that only survives by accident. Python has warned about it
# for years and has said it will stop accepting it.
GEOMETRY_PROBE = r"""
<script type="module">
import { store } from "./js/core.js";
window.__st = store;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rect = () => {
    const el = document.querySelector(".drive-exit");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.x * 10) / 10, Math.round(r.y * 10) / 10,
            Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10];
  };
  await wait(3500);
  // A READING TO COMPARE, BECAUSE THE DEFECT IS A DISAGREEMENT AND NOT A GAP.
  //
  // What this pair of checks was written to catch is one screen showing raw
  // Celsius while another shows Fahrenheit for the same instant. With no car
  // plugged in both screens correctly show a dash, `agree` requires digits,
  // and the check failed on every machine that was not sitting in a car --
  // which is every machine the suite normally runs on. A test that can only
  // pass with a vehicle attached is a test nobody can act on, so the probe
  // supplies the instant itself.
  //
  // The poller is silenced FIRST for the same reason it is silenced below: it
  // overwrites store.live every 250 ms, and an injected value that is gone
  // before it is read reports a fault that is not there.
  window.__st.refreshLive = async () => {};
  window.__st.live = Object.assign({}, window.__st.live || {}, {
    t: Date.now() / 1000, connected: true,
    values: Object.assign({}, (window.__st.live && window.__st.live.values) || {},
                          { COOLANT_TEMP: 89, SPEED: 0, RPM: 0 }) });
  window.__st.emit("live");
  await wait(400);
  // THE SCREEN THE TABLET PAINTS WHEN SOMEBODY GETS IN. Read before anything
  // navigates away from it: HOME is the arrival screen, and the coolant number
  // here used to be raw Celsius under a bare degree sign while two other
  // screens showed the same instant in Fahrenheit.
  // HOME'S COOLANT TILE. Value and unit are separate elements, for the reason
  // above: textContent of the whole tile would satisfy a unit regex by
  // accident.
  const cool = document.querySelector('.sig[data-reading="coolant"]');
  const vitalV = cool ? (cool.querySelector(".sig-v") || {}).textContent || "" : "";
  const vitalU = cool ? (cool.querySelector(".sig-u") || {}).textContent || "" : "";
  const vital = vitalV;
  location.hash = "#drive";
  await wait(2500);
  const tile = [...document.querySelectorAll(".drive-tile")]
    .map((el) => el.textContent.trim())
    .find((t) => t.includes("Coolant")) || "";
  const digits = (t) => (t.match(/-?\d+/) || [""])[0];
  const s = window.__st;
  // SILENCE THE FAST POLLER FIRST. It calls refreshLive() every 250 ms and
  // overwrites store.live with whatever the server says, so an injected speed
  // survived for less time than it took to measure it -- and the probe
  // reported "no movement" for a screen that was moving plenty. A test that
  // cannot fail is worse than no test, so this one was checked by putting the
  // old stylesheet back and watching it go red.
  s.refreshLive = async () => {};   // already silenced above; kept so this
                                    // block still reads as self-contained
  const at = async (kph) => {
    s.live = Object.assign({}, s.live || {}, {
      t: Date.now() / 1000, connected: true,
      values: Object.assign({}, (s.live && s.live.values) || {},
                            { SPEED: kph, RPM: 1800 }) });
    s.emit("live");
    await wait(220);
    return rect();
  };
  const out = { viewport: [innerWidth, innerHeight],
                homeCoolant: vital, homeUnit: vitalU, driveCoolant: tile,
                agree: !!digits(vital) && digits(vital) === digits(tile),
                unit: vitalU === "\u00b0F" || vitalU === "\u00b0C" };
  out.stopped = await at(0);
  out.rolling = await at(70);
  out.creep = [];
  for (const v of [3, 4, 3, 4, 3, 4]) out.creep.push(await at(v));
  document.title = "GEOM " + JSON.stringify(out);
})();
</script>
"""

# Appended to a second COPY of app.html, in its own run. This suite's own
# opening check loads app.html with no hash, and main.js's arrival screen is
# Home -- so until this probe existed, nothing in this file (or any other
# suite) had ever mounted share/js/views/vehicle.js. A renamed export, a
# selector that no longer matches a class the CSS still carries, any of the
# ordinary ways a view goes wrong on its way to the screen would have passed
# every check above: main.js's own try/catch around view.mount() turns a
# throwing view into a "That view failed to draw" card, not a blank page, and
# nothing here had ever looked. So this navigates to #vehicle for real and
# asks for content only a correctly-mounted screen produces -- the headline
# and one row per system, named -- not just that a container exists.
VEHICLE_PROBE = r"""
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  await wait(3500);
  location.hash = "#vehicle";
  await wait(2000);
  const titleEl = document.querySelector(".vh-title");
  const rows = [...document.querySelectorAll(".vh-sys-row")];
  const out = {
    headline: titleEl ? titleEl.textContent : null,
    rowCount: rows.length,
    rows: rows.map((r) => r.textContent.replace(/\s+/g, " ").trim()),
  };
  document.title = "VEHICLE " + JSON.stringify(out);
})();
</script>
"""


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
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def python_for_server():
    venv = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                        "venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def main():
    print("\n  The app starts in a browser\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0

    port = free_port()
    profile = tempfile.mkdtemp()
    server = subprocess.Popen(
        [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
         str(port), SHARE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/app.html"
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
             f"--user-data-dir={profile}", "--virtual-time-budget=9000",
             "--enable-logging=stderr", "--dump-dom", url],
            capture_output=True, text=True, timeout=120)
        dom, log = r.stdout or "", r.stderr or ""

        check("the page returned a document", len(dom) > 2000)

        # 1. Nothing threw. The browser prints an uncaught error to stderr and
        #    the app draws its own card, so both are worth asking about.
        throws = re.findall(r"(?:Reference|Type|Syntax|Range)Error[^\n]{0,120}",
                            log + "\n" + dom)
        if throws:
            for t in throws[:4]:
                bad(f"the page threw: {t.strip()}")
        else:
            ok("nothing threw while the app started")

        card = "OmaCar could not start" in dom
        if card:
            why = re.search(r"OmaCar could not start.{0,200}", dom, re.S)
            bad("the app drew its own failure card: "
                + re.sub(r"<[^>]+>", " ", why.group(0) if why else "").strip()[:160])
        else:
            ok("the app did not draw a failure card")

        # 2. It is actually on the screen, not merely silent.
        for what, needle in (("the vehicle bar", "vbar"),
                             ("the navigation", "<nav"),
                             ("a mounted view", "data-view")):
            check(f"{what} rendered", needle in dom)

        # 3. THE BOOT SCREEN LET GO. This is the check the three above cannot
        #    make: a splash stuck at full opacity over a perfectly healthy app
        #    leaves every one of them passing, because the DOM is all there --
        #    it is simply behind a sheet nobody can tap through. So the test
        #    asks for the one thing that means it finished, which is that the
        #    element removed itself.
        check("the boot screen is drawn before anything can load it",
              'id="boot"' in open(os.path.join(SHARE, "app.html"),
                                  encoding="utf-8").read())
        check("and it let go of the screen once the app was up",
              'id="boot"' not in dom)
        # ---- and again with no server behind it at all -------------------
        #
        # THE FAILURE PATH IS THE ONE THAT LIED. With the OmaCar server down,
        # every /api/ call 404s -- and the app said "connecting..." in the bar
        # and "Not connected" with a Connect button on the hub, which is what
        # it says when the CAR has not answered. Those two have completely
        # different fixes: one is a cable in a footwell, the other is a daemon
        # that is not running, and sending somebody to the wrong one in a car
        # park at night is exactly the kind of small lie this tool exists not
        # to tell.
        static = free_port()
        plain = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(static),
             "--bind", "127.0.0.1"], cwd=SHARE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        prof2 = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", static), 0.25):
                        break
                except OSError:
                    continue
            r2 = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={prof2}", "--virtual-time-budget=12000",
                 "--dump-dom", f"http://127.0.0.1:{static}/app.html"],
                capture_output=True, text=True, timeout=120)
            dead = r2.stdout or ""
            check("with no server, the boot screen still lets go",
                  'id="boot"' not in dead)
            check("and the app names the server rather than the car",
                  "cannot reach the OmaCar server" in dead
                  and "cannot reach its own server" in dead)
            check("and does not send anybody to the OBD cable",
                  "Not connected" not in dead)
        finally:
            plain.terminate()
            try:
                plain.wait(timeout=5)
            except subprocess.TimeoutExpired:
                plain.kill()
            shutil.rmtree(prof2, ignore_errors=True)
        # ---- and the geometry a thumb has to hit ---------------------------
        #
        # MEASURED, NOT READ. The drive screen's exit button used to grow from
        # 552 to 1125 CSS pixels the instant the car crept past 1.9 mph,
        # sliding its label 286 pixels -- 54 mm -- sideways and covering the
        # spot the other button had occupied. Dithering the speed across the
        # threshold flipped it 4.2 times a second, so one fixed point on the
        # glass meant two different actions at that rate. No amount of reading
        # the stylesheet finds that; it is a rendered number.
        #
        # The page is measured in a COPY of share/, so nothing here can leave
        # a probe behind in the repository.
        probe = tempfile.mkdtemp()
        copy = os.path.join(probe, "share")
        shutil.copytree(SHARE, copy)
        with open(os.path.join(copy, "app.html"), "a", encoding="utf-8") as f:
            f.write(GEOMETRY_PROBE)
        with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
            f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
        gport = free_port()
        gsrv = subprocess.Popen(
            [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
             str(gport), copy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gprof = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", gport), 0.25):
                        break
                except OSError:
                    continue
            subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={gprof}", "--virtual-time-budget=2000",
                 "--dump-dom", f"http://127.0.0.1:{gport}/_seed.html"],
                capture_output=True, timeout=120)
            # 1368x912 is the INNER viewport of a Surface Pro 7 at scale 2, and
            # --window-size sets the OUTER one: asking for 1368,912 gives an
            # inner height of 769 and every number measured in it is wrong.
            #
            # WHAT IS NOT KNOWN, and it is worth writing down rather than
            # leaving the next person to rediscover it. Asking for this exact
            # size, on this exact profile, the seed page comes back with an
            # inner height of 912 and app.html comes back with 968 — same
            # flags, same run, same browser. So the 56px is something the app's
            # own page does, not the browser chrome, and a calibration measured
            # on the seed page measures the wrong page. Chromium 151, Sep 2026.
            rg = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={gprof}", "--hide-scrollbars",
                 "--force-device-scale-factor=1", "--window-size=1368,1055",
                 "--virtual-time-budget=20000", "--dump-dom",
                 # No hash: the probe reads the arrival screen first,
                 # then navigates. Loading straight into #drive would skip the
                 # screen the tablet actually paints when somebody gets in.
                 f"http://127.0.0.1:{gport}/app.html"],
                capture_output=True, text=True, timeout=180)
            m = re.search(r"<title>GEOM (\{.*?\})</title>", rg.stdout, re.S)
            if not m:
                bad("the geometry probe returned nothing")
            else:
                g = json.loads(m.group(1).replace("&quot;", '"'))
                vp = g.get("viewport") or []
                check(f"the viewport really is the tablet's (got {vp[:2]})",
                      vp[:2] == [1368, 912])
                check(f"the exit button does not move when the car starts "
                      f"rolling (stopped {g.get('stopped')}, "
                      f"rolling {g.get('rolling')})",
                      g.get("stopped") == g.get("rolling")
                      and g.get("stopped") is not None)
                check(f"Home and the drive screen agree about the coolant "
                      f"({g.get('homeCoolant')!r} vs {g.get('driveCoolant')!r})",
                      bool(g.get("agree")))
                check(f"and Home says which scale it is in "
                      f"(unit element reads {g.get('homeUnit')!r})",
                      bool(g.get("unit")))
                widths = sorted({r[2] for r in (g.get("creep") or []) if r})
                check(f"nor flicker across the threshold in creeping traffic "
                      f"(widths seen: {widths})", len(widths) == 1)
        finally:
            gsrv.terminate()
            try:
                gsrv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gsrv.kill()
            shutil.rmtree(gprof, ignore_errors=True)
            shutil.rmtree(probe, ignore_errors=True)
        # ---- and the Vehicle screen actually mounts -------------------
        vprobe = tempfile.mkdtemp()
        vcopy = os.path.join(vprobe, "share")
        shutil.copytree(SHARE, vcopy)
        with open(os.path.join(vcopy, "app.html"), "a", encoding="utf-8") as f:
            f.write(VEHICLE_PROBE)
        vport = free_port()
        vsrv = subprocess.Popen(
            [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
             str(vport), vcopy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        vprof = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", vport), 0.25):
                        break
                except OSError:
                    continue
            rv = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={vprof}", "--virtual-time-budget=9000",
                 "--dump-dom", f"http://127.0.0.1:{vport}/app.html"],
                capture_output=True, text=True, timeout=120)
            mv = re.search(r"<title>VEHICLE (\{.*?\})</title>", rv.stdout, re.S)
            if not mv:
                bad("the vehicle probe returned nothing")
            else:
                v = json.loads(mv.group(1).replace("&quot;", '"'))
                check(f"the Vehicle screen draws a headline (got {v.get('headline')!r})",
                      bool((v.get("headline") or "").strip()))
                check(f"and one row per system (got {v.get('rowCount')})",
                      v.get("rowCount") == 5)
                rows_text = " | ".join(v.get("rows") or [])
                for label in ("Engine", "Hybrid system", "Brakes", "Electrical",
                              "All other systems"):
                    check(f"the systems list names {label!r}", label in rows_text)
        finally:
            vsrv.terminate()
            try:
                vsrv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vsrv.kill()
            shutil.rmtree(vprof, ignore_errors=True)
            shutil.rmtree(vprobe, ignore_errors=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  the app starts\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
