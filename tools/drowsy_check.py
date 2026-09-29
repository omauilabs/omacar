#!/usr/bin/env python3
"""Does drowsy mode's face tracker load and run here, on the recorder's real
cabin picture, fetching nothing from anywhere but this machine?

Run it beside a recorder: `tools/sim-cams.sh python3 tools/drowsy_check.py`.
On the box the C920 is the cabin. The check page is served by lib/serve.py
itself, through tools/shoot.py's served(), so the cabin's live MJPEG comes
from the real handler. The page loads the fetched MediaPipe, runs the Face
Landmarker on 20 frames, and reports on the browser's console, which this
reads. A step in the plan rather than a line in test/all.sh, because it needs
a camera.

    python3 tools/drowsy_check.py
"""

import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cams  # noqa: E402
from shoot import served  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><title>drowsy check</title><canvas id="c"></canvas>
<script type="module">
import { loadLandmarker, watchCabin } from "./js/facewatch.js";
const out = { loaded: false, loadMs: null, frames: 0, faces: 0, secs: null, error: null, resources: [] };
try {
  const t0 = performance.now();
  const lm = await loadLandmarker("CPU");
  out.loaded = true;
  out.loadMs = Math.round(performance.now() - t0);
  const t1 = performance.now();
  await new Promise((done) => {
    const w = watchCabin({ landmarker: lm, canvas: document.getElementById("c"),
      onFrame: (f) => { out.frames++; if (f.face) out.faces++; if (out.frames >= 20) { w.stop(); done(); } } });
    setTimeout(() => { w.stop(); done(); }, 60000);
  });
  out.secs = (performance.now() - t1) / 1000;
} catch (e) { out.error = String((e && e.message) || e); }
out.resources = performance.getEntriesByType("resource").map((r) => r.name);
console.log("DROWSYCHECK " + btoa(JSON.stringify(out)));
</script>
"""


def main():
    if not os.path.exists(cams.live_path("cabin")):
        print(f"  no cabin picture at {cams.live_path('cabin')}: run this beside a recorder (tools/sim-cams.sh)")
        return 2
    report = None
    with served({"_check.html": PAGE}) as (url, base):
        ch = subprocess.Popen(base + ["--enable-logging=stderr", "--v=0", url + "/_check.html"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(ln) for ln in ch.stderr], daemon=True).start()
        deadline = time.time() + 120
        try:
            while report is None and time.time() < deadline:
                try:
                    line = lines.get(timeout=1)
                except queue.Empty:
                    continue
                m = re.search(r"DROWSYCHECK ([A-Za-z0-9+/=]+)", line)
                if m:
                    report = json.loads(base64.b64decode(m.group(1)))
        finally:
            ch.terminate()
            try:
                ch.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ch.kill()
    if not report:
        print("  FAIL  the page never reported back")
        return 1
    foreign = [u for u in report["resources"] if not u.startswith(url + "/")]
    secs = report["secs"]
    fps = report["frames"] / secs if secs else 0
    print(f"  loaded   {report['loaded']} in {report['loadMs']} ms (CPU delegate)")
    print(f"  frames   {report['frames']} in {secs and round(secs, 1)} s ({fps:.1f} fps), "
          f"{report['faces']} with a face")
    print(f"  error    {report['error']}")
    print(f"  fetched  {len(report['resources'])} resources, {len(foreign)} from anywhere else")
    for u in foreign:
        print(f"    FOREIGN  {u}")
    good = report["loaded"] and report["frames"] >= 20 and not foreign and not report["error"]
    print("\n  " + ("ok" if good else "FAIL") + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
