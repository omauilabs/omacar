#!/usr/bin/env python3
"""Screenshots of Home, Vehicle and Gauges at the tablet's two orientations,
for setting beside the owner's mockups. Not a test: it passes and fails
nothing. Taken with a touch pointer emulated, as the tablet has, because the
coarse-pointer block in app.css makes the tab bar and chip rows taller there.

    tools/shoot.py [OUT_DIR]        default /tmp/omacar-shots

Uses the same server and the same onboarding seed as test/app_test.py.

THE WINDOW IS THE VIEWPORT HERE. --screenshot renders at exactly
--window-size, unlike --dump-dom, whose inner height comes back short of it
(test/app_test.py's TABLET table). This used to add 56 px for an allowance
--screenshot never takes, so every shot was 56 px taller than the tablet --
room in which a screen that overflows on the tablet looked as if it fitted.
The PNGs are twice these sizes (device scale 2).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
SIZES = {"landscape": (1368, 912), "portrait": (912, 1368)}
VIEWS = ("home", "vehicle", "drive")

# One copy of how a browser, a port and the server's python are found:
# test/app_test.py's.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import COARSE, browser, free_port, python_for_server  # noqa: E402


def main(argv):
    out = argv[1] if len(argv) > 1 else "/tmp/omacar-shots"
    os.makedirs(out, exist_ok=True)
    exe = browser()
    if not exe:
        print("no chromium here")
        return 1
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
        f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
    port = free_port()
    srv = subprocess.Popen([python_for_server(),
                            os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    try:
        time.sleep(1.5)
        base = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                f"--user-data-dir={prof}", "--hide-scrollbars",
                "--force-device-scale-factor=2", COARSE]
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom",
                               f"http://127.0.0.1:{port}/_seed.html"],
                       capture_output=True, timeout=120)
        for orient, (w, hgt) in SIZES.items():
            for view in VIEWS:
                path = os.path.join(out, f"{view}-{orient}.png")
                subprocess.run(base + [f"--window-size={w},{hgt}", "--virtual-time-budget=9000",
                                       f"--screenshot={path}",
                                       f"http://127.0.0.1:{port}/app.html#{view}"],
                               capture_output=True, timeout=180)
                print(("  wrote " if os.path.exists(path) else "  FAILED ") + path)
    finally:
        srv.terminate()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
