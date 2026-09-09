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
