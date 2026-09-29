#!/usr/bin/env python3
"""Screenshots of the app for setting beside the owner's mockups. Not a test:
it passes and fails nothing. Taken with a touch pointer emulated, as the
tablet has, because the coarse-pointer block in app.css makes the tab bar and
chip rows taller there.

    tools/shoot.py [OUT_DIR]                    Home, Vehicle and Gauges, both orientations
    tools/shoot.py OUT_DIR NAME=TARGET@W,H ...  any page; TARGET is what follows
                                                app.html, e.g. '?still=1#cameras'

Uses the same server and the same onboarding seed as test/app_test.py.

THE WINDOW IS THE VIEWPORT HERE. --screenshot renders at exactly
--window-size, unlike --dump-dom, whose inner height comes back short of it
(test/app_test.py's TABLET table). The PNGs are twice these sizes (device
scale 2).

The server inherits this process's environment, so OMACAR_VIDEOS,
XDG_RUNTIME_DIR, XDG_STATE_HOME and XDG_CONFIG_HOME point it at scratch
folders (tools/sim-cams.sh). `?still=1` asks every live camera feed for one
frame (share/js/camapi.js), so a stream that never ends cannot hold a
headless page open. `served()` is also how tools/drowsy_check.py puts its
page in front of the real server.
"""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
SIZES = {"landscape": (1368, 912), "portrait": (912, 1368)}
VIEWS = ("home", "vehicle", "drive")

# One copy of how a browser, a port and the server's python are found, and of
# how to wait for the server: test/app_test.py's.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import COARSE, browser, free_port, python_for_server, wait_for_port  # noqa: E402

SEED = '<script>localStorage.setItem("omacar.onboarded","1")</script>ok'


@contextlib.contextmanager
def served(extra=None):
    """(url, the browser's argv so far) for a copy of share/ served by
    lib/serve.py, with the first-run tour marked seen in a fresh profile.
    `extra` adds files to the copy: {path inside share/: text}."""
    exe = browser()
    if not exe:
        raise SystemExit("no chromium here")
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    for rel, text in dict({"_seed.html": SEED}, **(extra or {})).items():
        with open(os.path.join(copy, rel), "w", encoding="utf-8") as f:
            f.write(text)
    port = free_port()
    srv = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    url = f"http://127.0.0.1:{port}"
    base = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--user-data-dir={prof}", "--hide-scrollbars",
            "--force-device-scale-factor=2", COARSE]
    try:
        if not wait_for_port(port):
            raise SystemExit("the server never came up")
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom", url + "/_seed.html"],
                       capture_output=True, timeout=120)
        yield url, base
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def shoot(out, shots, doms=()):
    """shots: [(name, target, (w, h))]; doms: [target]. Returns
    ({name: png path, or None if it failed}, {target: the DOM})."""
    os.makedirs(out, exist_ok=True)
    pngs, dom = {}, {}
    with served() as (url, base):
        for name, target, (w, hgt) in shots:
            path = os.path.join(out, name + ".png")
            subprocess.run(base + [f"--window-size={w},{hgt}", "--virtual-time-budget=9000",
                                   f"--screenshot={path}", f"{url}/app.html{target}"],
                           capture_output=True, timeout=180)
            pngs[name] = path if os.path.exists(path) else None
        for target in doms:
            dom[target] = subprocess.run(base + ["--virtual-time-budget=9000", "--dump-dom",
                                                 f"{url}/app.html{target}"],
                                         capture_output=True, text=True, timeout=180).stdout
    return pngs, dom


def main(argv):
    out = argv[1] if len(argv) > 1 else "/tmp/omacar-shots"
    if len(argv) > 2:
        shots = []
        for arg in argv[2:]:
            name, _, rest = arg.partition("=")
            target, _, size = rest.rpartition("@")
            w, hgt = size.split(",")
            shots.append((name, target, (int(w), int(hgt))))
    else:
        shots = [(f"{view}-{orient}", f"#{view}", size) for orient, size in SIZES.items() for view in VIEWS]
    pngs, _ = shoot(out, shots)
    for name, path in pngs.items():
        print(("  wrote " + path) if path else ("  FAILED " + name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
