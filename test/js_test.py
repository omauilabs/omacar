#!/usr/bin/env python3
"""The app's pure JavaScript, tested in the browser the app runs in.

There is no node on the tablet or on the box, and the house rule is no build
step, so a JavaScript unit test here is a module under test/js/ that a real
Chromium imports. Each module's default export is a list of [name, fn] pairs;
fn throws to fail. The runner page collects the results into document.title and
--dump-dom brings them back -- the same trick app_test.py's geometry probe uses.

Skipped loudly without a browser, like app_test.py: a test that cannot run is
not a failure, and a test that silently does nothing is.
"""

import html
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
TESTS = os.path.join(ROOT, "test", "js")

# The browser finder and the free port come from app_test.py, the suite that
# first ran this app in a browser -- one copy of how a browser is found.
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser, free_port  # noqa: E402

RUNNER = """<!doctype html><meta charset="utf-8"><title>RUNNING</title>
<script type="module">
const files = %s;
const out = { passed: 0, failed: [] };
for (const f of files) {
  let mod;
  try { mod = await import("./_tests/" + f); }
  catch (e) { out.failed.push(f + " :: import :: " + ((e && e.message) || e)); continue; }
  for (const [name, fn] of mod.default) {
    try { await fn(); out.passed++; }
    catch (e) { out.failed.push(f + " :: " + name + " :: " + ((e && e.message) || e)); }
  }
}
document.title = "RESULT " + JSON.stringify(out);
</script>
"""


def main():
    print("\n  JavaScript units, in a real browser\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0
    files = sorted(f for f in os.listdir(TESTS) if f.endswith(".test.js"))
    if not files:
        print("    FAIL  test/js holds no *.test.js files\n")
        return 1
    # A COPY of share/, so nothing here can leave a test page in the repo.
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    shutil.copytree(TESTS, os.path.join(copy, "_tests"))
    with open(os.path.join(copy, "_run.html"), "w", encoding="utf-8") as f:
        f.write(RUNNER % json.dumps(files))
    port = free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                            "--bind", "127.0.0.1"], cwd=copy,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        # --mute-audio: the units build the page's real AudioContext (the
        # audio stage, the alert player), and nothing a test does may ever
        # sound on this machine's speakers. The sound tests render into
        # OfflineAudioContexts, which never reach an output at all; this is
        # the second lock on the same door.
        # THE BUDGET IS VIRTUAL TIME, AND EVERY TEST'S TIMERS SPEND IT. 8 s held
        # one branch's units; the demo build, which puts the cameras and road
        # cameras suites in one page (503 tests), ran out before its last test
        # and reported only "the runner page never finished". A larger budget
        # costs nothing when the page finishes sooner: Chromium fast-forwards
        # idle virtual time, and the real-time timeout below is unchanged.
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--mute-audio",
             f"--user-data-dir={prof}", "--virtual-time-budget=30000",
             "--dump-dom", f"http://127.0.0.1:{port}/_run.html"],
            capture_output=True, text=True, timeout=120)
        m = re.search(r"<title>RESULT (\{.*?\})</title>", r.stdout or "", re.S)
        if not m:
            print("    FAIL  the runner page never finished\n")
            return 1
        res = json.loads(html.unescape(m.group(1)))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    for line in res["failed"]:
        print(f"    FAIL  {line}")
    print(f"\n  {res['passed']} passed, {len(res['failed'])} failed\n")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
