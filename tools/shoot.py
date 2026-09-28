#!/usr/bin/env python3
"""Screenshots of Home and Vehicle at the tablet's two orientations, for
setting beside the owner's mockups. Not a test: it passes and fails nothing.

    tools/shoot.py [OUT_DIR]        default /tmp/omacar-shots

Uses the same server and the same onboarding seed as test/app_test.py, and the
same outer-window allowance: app.html's inner height comes back 56 px short of
--window-size, so 912 of inner height needs 968 of window.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
SIZES = {"landscape": (1368, 968), "portrait": (912, 1424)}
VIEWS = ("home", "vehicle")

# One copy of how a browser, a port and the server's python are found:
# test/app_test.py's.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser, free_port, python_for_server  # noqa: E402


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
                "--force-device-scale-factor=2"]
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
