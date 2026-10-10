#!/usr/bin/env python3
"""`demoworld.py tidy`: the meetup demo's car, all systems normal.

The demo's car comes from `sim.py seed`, which is written for a different job:
a year of a real CR-Z's life, with an IMA pack on its way out, an O2 heater
code, a blower resistor, the deflation warning after a rotation, and the
readiness monitors those hold back. In front of a room that read as a broken
car: Vehicle said "4 systems need attention", its tab carried a red 5, Home's
car said "Tyres Check", and a minute later the demo's own scan said "All
systems normal · 0 codes" (doc/design/2026-09-30-meetup-demo.md §4: "All
systems normal … no trouble codes").

`tidy` makes the seeded car the demo's: every code cleared (the rows kept, as
history), an all-clear last scan from earlier today, the monitors complete,
the on-board tests inside their limits and nothing in the service book due.
The year of trips is not touched.

Checks, in a scratch HOME whose state is inside an omacar-demo folder, as
`omacar demo on` makes it: the seeded car is not clean before (so the checks
below mean something); after tidy, the snapshot the Vehicle overview and the
tab badge read has no active code, no module holding a code, readiness ready,
every Mode 06 result passing and no service due, with the history intact; a
second tidy changes nothing; tidy refuses a state folder that is not the
demo's, and one beside a running daemon; and, in headless Chromium against a
real server, Vehicle says "All systems normal", its tab has no badge and
Home's car callout does not say Check. Nothing here plays anything.
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
LIB = os.path.join(ROOT, "lib")
SHARE = os.path.join(ROOT, "share")
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser  # noqa: E402

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


def env_for(home, demo=True):
    """The environment `omacar demo on` gives its children (demo_env), in a
    scratch HOME. demo=False: the same shape, but not inside omacar-demo."""
    root = os.path.join(home, ".local", "state", "omacar-demo" if demo else "not-the-demo")
    state = os.path.join(root, "state")
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "HOME": home,
           "XDG_STATE_HOME": state, "XDG_CONFIG_HOME": os.path.join(root, "config"),
           "OMACAR_STATE": os.path.join(state, "omacar"),
           "OMACAR_PORT": os.path.join(root, "no-adapter"),
           "XDG_RUNTIME_DIR": os.path.join(root, "run")}
    os.makedirs(env["OMACAR_STATE"], exist_ok=True)
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
    os.makedirs(os.path.join(env["XDG_CONFIG_HOME"], "omarchy"), exist_ok=True)
    return env


def py(env, *args, timeout=180):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          env=env, timeout=timeout)


# What the overview, the tab badge and Home's callout are built from, read the
# way the server reads it (lib/records.py snapshot()).
PROBE = r"""
import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
import records
s = records.snapshot()
db = sqlite3.connect(records.DB)
print(json.dumps({
  "active": [f["code"] for f in s["active_faults"]],
  "faults": sorted((f["code"], f["status"]) for f in s["faults"]),
  "module_codes": {m["id"]: m["codes"] for m in s["modules"]},
  "ready": s["readiness"]["ready"], "incomplete": s["readiness"]["incomplete"],
  "mode06_fail": [m["mid"] for m in s["mode06"] if m.get("pass") is False],
  "service_due": (s["service"] or {}).get("due"),
  "surveyed_at": (s["vehicle"] or {}).get("surveyed_at"),
  "trips": db.execute("SELECT COUNT(*) FROM trips").fetchone()[0],
  "days": db.execute("SELECT COUNT(*) FROM days").fetchone()[0],
  "samples": db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
  "db": records.DB,
}))
"""


def snapshot(env):
    r = py(env, "-c", PROBE, LIB)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


# ---- headless: the screens themselves -----------------------------------------

SCREENS = r"""
<script type="module">
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const out = {};
await wait(2500);
location.hash = "#vehicle";
await wait(2500);
const st = document.querySelector(".vh-status");
out.vehicle = st ? st.textContent.replace(/\s+/g, " ").trim() : null;
const tabs = [...document.querySelectorAll("#tabbar .tab")];
const vt = tabs.find((t) => /Vehicle/.test(t.textContent));
const pip = vt && vt.querySelector(".pip");
out.pip = pip ? (pip.hidden ? "" : (pip.textContent || "dot")) : null;
out.systems = [...document.querySelectorAll(".vh-systems")].map((n) => n.textContent.replace(/\s+/g, " ").trim()).join(" ");
location.hash = "#home";
await wait(2500);
const co = document.querySelector(".hc-car .callout");
out.callout = co ? co.textContent.replace(/\s+/g, " ").trim() : null;
document.title = "TIDY " + JSON.stringify(out);
</script>
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def screens(exe, env):
    work = tempfile.mkdtemp(prefix="demotidy-")
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy, ignore=lambda d, names:
                    ["private"] if os.path.basename(d) == "assets" else [])
    with open(os.path.join(copy, "app.html"), "a", encoding="utf-8") as f:
        f.write(SCREENS)
    with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
        f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
    port = free_port()
    srv = subprocess.Popen([sys.executable, os.path.join(LIB, "serve.py"), str(port), copy],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    prof = os.path.join(work, "prof")
    base = [exe, "--headless=new", "--password-store=basic", "--disable-gpu", "--no-sandbox", "--mute-audio",
            f"--user-data-dir={prof}", "--window-size=1368,912"]
    try:
        for _ in range(60):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom",
                               f"http://127.0.0.1:{port}/_seed.html"],
                       capture_output=True, timeout=120, env=dict(os.environ, TMPDIR=work))
        r = subprocess.run(base + ["--virtual-time-budget=12000", "--dump-dom",
                                   f"http://127.0.0.1:{port}/app.html"],
                           capture_output=True, text=True, timeout=180, env=dict(os.environ, TMPDIR=work))
        m = re.search(r"<title>TIDY (\{.*?\})</title>", r.stdout or "", re.S)
        return json.loads(m.group(1).replace("&quot;", '"')) if m else None
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(work, ignore_errors=True)


def main():
    print("\n  The demo's car, tidied (lib/demoworld.py tidy)\n")
    home = tempfile.mkdtemp(prefix="demotidy-home-")
    try:
        env = env_for(home)
        r = py(env, os.path.join(LIB, "sim.py"), "seed")
        check("sim.py seed makes the demo car", r.returncode == 0, r.stderr[-400:])
        before = snapshot(env)
        check("seeded, it is not a clean car (so what follows means something)",
              before["active"] and any(before["module_codes"].values()) and not before["ready"],
              json.dumps(before)[:400])

        t0 = time.time()
        r = py(env, os.path.join(LIB, "demoworld.py"), "tidy")
        check("tidy exits 0", r.returncode == 0, r.stdout + r.stderr)
        after = snapshot(env)
        check("no active code (the Vehicle tab's count, Codes, the agent's 'no warnings')",
              after["active"] == [], after["active"])
        check("P1449, P0A7F, B1225, C1B00 and P0135 kept, as cleared history",
              {c for c, s in after["faults"] if s == "cleared"}
              >= {"P1449", "P0A7F", "B1225", "C1B00", "P0135"}
              and len(after["faults"]) == len(before["faults"]), after["faults"])
        check("the last scan found no code in any module (every system OK, Tyres OK)",
              all(c == [] for c in after["module_codes"].values())
              and len(after["module_codes"]) == len(before["module_codes"]), after["module_codes"])
        check("readiness complete (the tab's '!')", after["ready"] and after["incomplete"] == 0,
              (after["ready"], after["incomplete"]))
        check("every on-board test inside its limits", after["mode06_fail"] == [], after["mode06_fail"])
        check("nothing in the service book due (the tab's warn count)", after["service_due"] == 0,
              after["service_due"])
        lt = time.localtime(after["surveyed_at"] or 0)
        today = time.localtime(t0)
        check("the last scan is from earlier today",
              (lt.tm_year, lt.tm_yday) == (today.tm_year, today.tm_yday)
              and after["surveyed_at"] <= time.time(), after["surveyed_at"])
        check("the history is as seeded: trips, days and samples",
              (after["trips"], after["days"], after["samples"])
              == (before["trips"], before["days"], before["samples"]),
              (before["trips"], after["trips"], before["samples"], after["samples"]))

        r = py(env, os.path.join(LIB, "demoworld.py"), "tidy")
        again = snapshot(env)
        check("a second tidy changes nothing", r.returncode == 0 and again == after,
              json.dumps(again)[:300])

        # Refused: a state folder that is not the demo's ...
        other = env_for(home, demo=False)
        r = py(other, os.path.join(LIB, "demoworld.py"), "tidy")
        check("tidy refuses a state folder outside omacar-demo (exit 2)",
              r.returncode == 2 and "omacar-demo" in r.stderr, (r.returncode, r.stderr))
        dbs = [n for _, _, fs in os.walk(other["XDG_STATE_HOME"]) for n in fs if n.endswith(".db")]
        check("and makes no database there", dbs == [], dbs)
        # ... and one beside a running daemon.
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            with open(os.path.join(env["OMACAR_STATE"], "daemon.pid"), "w") as f:
                f.write(str(sleeper.pid))
            r = py(env, os.path.join(LIB, "demoworld.py"), "tidy")
            check("tidy refuses beside a running daemon (exit 2)",
                  r.returncode == 2 and "daemon" in r.stderr, r.stderr)
        finally:
            sleeper.kill()
            sleeper.wait()
            os.remove(os.path.join(env["OMACAR_STATE"], "daemon.pid"))

        exe = browser()
        if not exe:
            print("    (skipping the screens: no chromium here)")
        else:
            got = screens(exe, env)
            check("the screens answered", got is not None)
            got = got or {}
            check("Vehicle says All systems normal", "All systems normal" in (got.get("vehicle") or ""),
                  got.get("vehicle"))
            check("its tab carries no badge", got.get("pip") == "", got.get("pip"))
            check("no system says Check", "Check" not in (got.get("systems") or "x Check"),
                  got.get("systems"))
            check("Home's car callout does not say Check",
                  got.get("callout") and "Check" not in got["callout"], got.get("callout"))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print(f"\n  {'all passed' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
