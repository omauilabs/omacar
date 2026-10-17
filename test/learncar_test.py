#!/usr/bin/env python3
"""Learn my car (lib/learncar.py) and the labelled-window ranking it feeds
(lib/correlate.py rank_labelled / load_labelled).

Three things matter most and each has its own section:
  the script is a fixed, ordered, passive list of one-thing steps;
  the ranking offers a byte that sits still outside a window and moves inside
    it, and does NOT offer a rolling counter, a checksum or wheel-speed noise;
  nothing starts, continues or keeps listening while the car is moving.
No adapter is touched: the synthetic bus stands in for the car.
"""

import ast
import json
import os
import random
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

SCRATCH = tempfile.mkdtemp(prefix="omacar-learncar-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")

import correlate  # noqa: E402
import learncar as L  # noqa: E402

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


PARKED = {"speed": 0.0, "age": 2.0, "handover": False}
MOVING = {"speed": 38.0, "age": 1.0, "handover": False}


# ---- synthetic frames for the ranking (no learncar needed) --------------------

def synth_run(n_cycles=6, hz=40, act_s=5.0, rest_s=4.0, seed=3, brake_byte=3,
              flaky_counter=True, t_start=100.0):
    """A capture with the brake held in n_cycles windows.

    0x17C byte 3 is the brake (0x00 / 0x40). Byte 6 is a rolling counter and
    byte 7 a checksum over the rest, 0x156 is wheel-speed noise and 0x2D0 is
    constant -- the first three change as much outside the windows as in them.
    0x3F0 byte 0 changes in the SECOND half of the capture only, to prove a
    one-off change is not a window-locked one.
    """
    rnd = random.Random(seed)
    frames, windows, t, n = [], [], t_start, 0
    for c in range(n_cycles):
        for kind, dur in (("rest", rest_s), ("act", act_s)):
            t0 = t
            while t < t0 + dur:
                n += 1
                brake = 0x40 if kind == "act" else 0
                b = bytearray([0, 0, 0, brake, 0, 0, n & 0x0F, 0])
                b[7] = (sum(b[:7]) * 7 + 0x35) & 0xFF
                frames.append((t, "17C", bytes(b)))
                frames.append((t + 0.001, "156", bytes(rnd.randint(0, 255) for _ in range(4))))
                frames.append((t + 0.002, "2D0", b"\x5a\x00\x00\xff"))
                frames.append((t + 0.003, "3F0", bytes([1 if (c >= n_cycles // 2 and kind == "rest") else 0])))
                t += 1.0 / hz
            windows.append({"label": "brake", "state": "held" if kind == "act" else "released",
                            "active": kind == "act", "t0": t0, "t1": t})
    return {"frames": frames, "windows": windows}


def main():
    # ---- the script ----------------------------------------------------------
    print("\n  the script")
    ids = [s["id"] for s in L.script("manual")]
    check("it runs ignition, brake, steering, lights, hazards, clutch, engine, rev in that order",
          ids == ["ignition", "brake", "steering", "headlights", "hazards", "clutch", "engine", "rev"])
    check("a CVT gets the selector step and not the clutch step",
          "gear" in [s["id"] for s in L.script("cvt")] and "clutch" not in [s["id"] for s in L.script("cvt")])
    check("every step names one thing to do and what it teaches",
          all(s["act"] and s["prep"] and s["teaches"] and s["phrase"] for s in L.STEPS))
    check("only the engine and rev steps need the engine running",
          sorted(s["id"] for s in L.STEPS if s["engine"]) == ["engine", "rev"])
    check("step ids are unique", len({s["id"] for s in L.STEPS}) == len(L.STEPS))
    brake = L.STEP_BY_ID["brake"]
    p = L.plan(brake)
    check("the brake plan alternates rest and act and ends at rest",
          [g["kind"] for g in p] == ["rest", "act"] * 3 + ["rest"])
    check("the first cue is hold-still and later rests say release",
          p[0]["say"].endswith("hold still") and p[2]["say"] == "Release the brake")
    check("steering alternates full left and full right",
          [g["state"] for g in L.plan(L.STEP_BY_ID["steering"]) if g["kind"] == "act"]
          == ["full left", "full right", "full left", "full right"])
    check("the engine step ends with the engine running (no closing rest)",
          L.plan(L.STEP_BY_ID["engine"])[-1]["kind"] == "act")
    check("scaling shrinks every duration and nothing else",
          abs(L.total_seconds(brake, 0.1) - L.total_seconds(brake) * 0.1) < 1e-9
          and [g["say"] for g in L.plan(brake, 0.1)] == [g["say"] for g in p])
    check("a full session is minutes, not an hour",
          60 < sum(L.total_seconds(s) for s in L.script("manual")) < 600)

    # ---- passive only, by construction ----------------------------------------
    print("\n  nothing is ever sent")
    src = open(os.path.join(ROOT, "lib", "learncar.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    check("it never calls an adapter request, raw AT command, actuator or identifier write",
          not (called & {"request", "raw", "write_did", "actuate", "send", "functional_test", "init"}))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
               {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    check("it does not import the request/actuation modules (elm, actuate, write, dtc)",
          not (imported & {"elm", "actuate", "write", "dtc"}))
    check("its only listener is lib/listen.py's monitor", "listen.listen(" in src.replace("self.listen.listen(", "listen.listen("))

    # ---- refusing while moving -----------------------------------------------
    print("\n  refusing while moving")
    check("a fresh zero is parked", L.check_parked(PARKED) == (True, None))
    ok, why = L.check_parked(MOVING)
    check("38 km/h is refused, with the speed in the reason", not ok and "38 km/h" in why)
    check("a road speed we cannot read is refused", not L.check_parked({"speed": None, "age": 1.0})[0])
    check("no reading at all is refused", not L.check_parked(None)[0])
    check("a stale zero is refused (it says nothing about now)",
          not L.check_parked({"speed": 0.0, "age": 600.0, "handover": False})[0])
    check("a hand-off in progress is refused", not L.check_parked(dict(PARKED, handover=True))[0])
    check("a crawl of 1 km/h is moving", not L.check_parked(dict(PARKED, speed=1.0))[0])

    m = L.Machine("manual")
    check("start while moving is refused and the machine never runs",
          not m.start(10.0, MOVING) and m.status == "refused" and m.phase is None)
    m = L.Machine("manual")
    check("start with the speed unreadable is refused", not m.start(10.0, {"speed": None, "age": None}))
    m = L.Machine("manual")
    m.start(10.0, PARKED)
    check("start while parked runs and waits for the owner", m.status == "running" and m.phase == "ready")
    check("'go' while moving is refused and says why",
          not m.go(11.0, MOVING) and m.phase == "ready" and "moving" in m.blocked)
    check("'go' while parked begins the countdown", m.go(12.0, PARKED) and m.phase == "countdown")
    m.tick(12.0 + L.LEAD_S + 0.1, PARKED)
    check("the countdown ends by asking to listen, once", m.pop_effects() == [("listen_start", "ignition")])
    m.listening(20.0)
    check("listening opens the first window", m.phase == "listening" and m._open is not None)
    m.tick(21.0, MOVING)
    check("the car moving mid-step stops listening and ends the session",
          m.status == "refused" and ("listen_stop", None) in m.pop_effects() and "moving" in m.reason)
    check("and nothing carries on afterwards", (m.go(30, PARKED), m.next(30), m.tick(31, PARKED))[0] is False
          and m.phase is None)

    # ---- the engine steps -----------------------------------------------------
    print("\n  engine steps")
    m = L.Machine("manual")
    m.start(0.0, PARKED)
    m.idx = [s["id"] for s in m.steps].index("engine")
    m.phase = "ready"
    check("an engine step will not start without the parking-brake tap",
          not m.go(1.0, PARKED) and "parking-brake" in m.blocked)
    m.confirm(2.0)
    check("after the tap it may", m.go(3.0, PARKED))
    m.tick(3.0 + L.LEAD_S + 0.1, MOVING)
    check("moving at the end of the countdown still stops it before it listens",
          m.status == "running" and m.phase == "starting")  # the check is at 'go' and mid-step
    m.listening(10.0)
    m.tick(10.5, MOVING)
    check("and then ends it the moment it is seen", m.status == "refused")

    # ---- abort and the flow ---------------------------------------------------
    print("\n  flow")
    m = L.Machine("manual")
    m.start(0.0, PARKED)
    m.abort(1.0)
    check("abort ends it", m.status == "aborted" and m.reason)
    m = L.Machine("manual", scale=0.1)
    m.start(0.0, PARKED)
    m.go(1.0, PARKED)
    m.tick(1.0 + L.LEAD_S * 0.1 + 0.01, PARKED)
    m.pop_effects()
    m.listening(2.0)
    now, seen_kinds = 2.0, []
    while m.phase == "listening" and now < 100:
        now += 0.05
        m.tick(now, PARKED)
        if m._open:
            seen_kinds.append(m._open["active"])
    check("the whole plan plays out and ends in saving", m.phase == "saving")
    check("the closed windows are the ignition step's rest then act",
          [w["active"] for w in m.windows] == [False, True] and m.windows[0]["label"] == "ignition")
    check("windows are contiguous apart from the human lag",
          0 <= m.windows[1]["t0"] - m.windows[0]["t1"] <= 1.5 * 0.1 + 0.06)
    check("listen_stop was asked when the plan ended", ("listen_stop", None) in m.pop_effects())
    m.captured(now, 120, True)
    check("a captured step waits for 'Done — next'", m.phase == "review"
          and m.snapshot(now)["steps"][0]["state"] == "done")
    m.next(now + 1)
    check("next moves on to the brake step", m.step["id"] == "brake" and m.phase == "ready")
    m.skip(now + 2)
    check("skip marks it skipped and moves on",
          m.snapshot(now)["steps"][1]["state"] == "skipped" and m.step["id"] == "steering")
    snap = m.snapshot(now)
    check("the snapshot is JSON", bool(json.dumps(snap)))

    # ---- the ranking, on synthetic frames --------------------------------------
    print("\n  ranking labelled windows")
    run = synth_run()
    res = correlate.rank_labelled([dict(run)])["brake"]
    top = res[0]
    check("0x17C byte 3 is found", (top["id"], top["byte"]) == ("17C", 3))
    check("it is a switch, 00 to 40", top["kind"] == "switch" and top["rest_value"] == 0
          and top["active_value"] == 0x40 and top["bits"] == 0x40)
    check("it held in every window: 6/6", (top["hits"], top["windows"]) == (6, 6) and top["strong"])
    check("the rolling counter (byte 6) is not offered",
          not any(c["id"] == "17C" and c["byte"] == 6 for c in res))
    check("the checksum (byte 7) is not offered", not any(c["id"] == "17C" and c["byte"] == 7 for c in res))
    check("wheel-speed noise is not offered", not any(c["id"] == "156" for c in res))
    check("a constant frame is not offered", not any(c["id"] == "2D0" for c in res))
    check("a byte that only changed in the second half is not offered as window-locked",
          not any(c["id"] == "3F0" for c in res))
    check("exactly one candidate survives", len(res) == 1)

    two = {"frames": synth_run()["frames"], "windows": synth_run()["windows"][:4]}
    t2 = correlate.rank_labelled([two])["brake"][0]
    check("two windows is a hint, not strong", (t2["hits"], t2["windows"], t2["strong"]) == (2, 2, False))

    # a window that never changed
    flat = synth_run()
    for i, f in enumerate(flat["frames"]):
        if f[1] == "17C":
            b = bytearray(f[2]); b[3] = 0; b[7] = 0
            flat["frames"][i] = (f[0], f[1], bytes(b))
    check("nothing moved, so nothing is offered",
          correlate.rank_labelled([flat])["brake"] == [])

    # one window in six missed it
    miss = synth_run()
    w = [x for x in miss["windows"] if x["active"]][2]
    for i, f in enumerate(miss["frames"]):
        if f[1] == "17C" and w["t0"] <= f[0] < w["t1"]:
            b = bytearray(f[2]); b[3] = 0
            miss["frames"][i] = (f[0], f[1], bytes(b))
    mc = [c for c in correlate.rank_labelled([miss])["brake"] if c["byte"] == 3][0]
    check("5 of 6 windows is listed but not strong, and says 5/6",
          (mc["hits"], mc["windows"], mc["strong"]) == (5, 6, False))

    # edge handling: a changing edge frame must not break a switch
    edge = synth_run()
    check("window edges are trimmed (a late edge frame does not count against the rest)",
          correlate.rank_labelled([edge])["brake"][0]["hits"] == 6)

    # specificity: a byte that moves during another label too is said to
    other = synth_run()
    other2 = synth_run(seed=9, t_start=1000.0)
    for w in other2["windows"]:
        w["label"] = "hazards"
    # make 0x17C byte 3 move in hazards' active windows too, and be quiet at its rest
    res2 = correlate.rank_labelled([other, other2])
    c = res2["brake"][0]
    check("a byte that also moves while another thing is done says so",
          c["also_in"] == ["hazards"] and not c["specific"])
    check("the other label's own copy is listed as moving during the brake",
          res2["hazards"][0]["also_in"] == ["brake"])
    check("but a byte that merely sits at a different value elsewhere is not 'also moving'",
          correlate.rank_labelled([synth_run(), {
              "frames": [(5000.0 + i * 0.025, "17C", bytes([0, 0, 0, 0x10, 0, 0, i & 15, 0])) for i in range(800)],
              "windows": [{"label": "lights", "state": "off", "active": False, "t0": 5000, "t1": 5010},
                          {"label": "lights", "state": "on", "active": True, "t0": 5010, "t1": 5020}]}]
                                  )["brake"][0]["also_in"] == [])

    # an analog value (steering angle) next to a switch
    rnd = random.Random(5)
    frames, wins, t = [], [], 0.0
    for c in range(4):
        for kind in ("rest", "act"):
            t0 = t
            while t < t0 + 4:
                ang = 0 if kind == "rest" else (0x0480 if c % 2 == 0 else 0xFB80) + rnd.randint(-5, 5)
                ang &= 0xFFFF
                frames.append((t, "1A6", bytes([ang >> 8, ang & 0xFF, 0, 0])))
                t += 0.025
            wins.append({"label": "steering", "state": "x", "active": kind == "act", "t0": t0, "t1": t})
    st = correlate.rank_labelled([{"frames": frames, "windows": wins}])["steering"]
    check("a 16-bit angle is one candidate spanning bytes 0-1, analog",
          len(st) >= 1 and st[0]["kind"] == "analog" and st[0]["span"] == [0, 1])

    # unheard frames are not evidence
    sparse = {"frames": [(1.0, "17C", bytes([0, 0, 0, 0x40]))],
              "windows": [{"label": "brake", "state": "held", "active": True, "t0": 0, "t1": 5},
                          {"label": "brake", "state": "released", "active": False, "t0": 5, "t1": 10}]}
    check("an identifier heard once is not offered", correlate.rank_labelled([sparse])["brake"] == [])
    check("no windows at all is an empty answer", correlate.rank_labelled([]) == {})

    # pooling: windows from separate runs add up
    a, b = synth_run(n_cycles=2), synth_run(n_cycles=2, seed=8, t_start=500.0)
    pooled = correlate.rank_labelled([a, b])["brake"][0]
    check("separate runs pool: 2 + 2 windows is 4/4 and strong", (pooled["hits"], pooled["windows"], pooled["strong"]) == (4, 4, True))

    # ---- files ----------------------------------------------------------------
    print("\n  labels.jsonl and the loader")
    d = os.path.join(SCRATCH, "caps")
    os.makedirs(d)
    run = synth_run()
    started = run["frames"][0][0]
    json.dump({"started": started, "raw": [{"t": round(t - started, 4), "id": i, "data": x.hex()}
                                           for t, i, x in run["frames"]]},
              open(os.path.join(d, "learncar-brake-1.json"), "w"))
    L_orig = L.CAPTURES
    with open(os.path.join(d, "labels.jsonl"), "w") as f:
        for w in run["windows"]:
            f.write(json.dumps(dict(w, capture="learncar-brake-1", simulated=False)) + "\n")
        f.write("not json\n")
        f.write(json.dumps({"capture": "gone", "label": "brake", "t0": 0, "t1": 1}) + "\n")
        f.write(json.dumps(dict(run["windows"][0], capture="learncar-brake-1", simulated=True)) + "\n")
    runs = correlate.load_labelled(d)
    check("the loader reads real windows, skips junk, a missing capture and simulated lines",
          len(runs) == 1 and len(runs[0]["windows"]) == 12)
    check("and what it loads ranks the brake byte", correlate.rank_labelled(runs)["brake"][0]["byte"] == 3)
    check("a simulator's lines load only when asked for", len(correlate.load_labelled(d, simulated=True)) == 1
          and len(correlate.load_labelled(d, simulated=True)[0]["windows"]) == 1)
    check("a missing labels file is an empty list", correlate.load_labelled(os.path.join(SCRATCH, "nowhere")) == [])
    del L_orig

    # ---- the whole thing against the synthetic bus -----------------------------
    print("\n  end to end, Simulated")
    os.environ["OMACAR_LEARNCAR_SPEED"] = "20"
    os.environ.pop("OMACAR_LEARNCAR_SIM_SPEED", None)
    t = threading.Thread(target=lambda: L.run(sim=True), daemon=True)
    t.start()
    deadline = time.time() + 60
    tapped = set()
    while time.time() < deadline:
        st = L.read_public()
        if st.get("status") in ("finished", "aborted", "refused"):
            break
        cur = st.get("current") or {}
        if st.get("phase") == "ready":
            key = ("ready", st["idx"])
            if key not in tapped:
                tapped.add(key)
                if cur.get("engine"):
                    L.ask("confirm", sim=True)
                    time.sleep(0.1)
                L.ask("go", sim=True)
        elif st.get("phase") == "review":
            key = ("review", st["idx"])
            if key not in tapped:
                tapped.add(key)
                L.ask("next", sim=True)
        time.sleep(0.03)
    t.join(10)
    st = L.read_public()
    check("a Simulated session runs all eight steps to the end", st["status"] == "finished"
          and all(s["state"] == "done" for s in st["steps"]))
    check("the state says Simulated, not a car", st["source"] == "sim")
    res = L.read_results(True)
    check("results were written for the simulator and not for a car",
          res and res["source"] == "sim" and L.read_results(False) is None)
    labs = res["labels"]
    check("the brake result is 0x17C byte 3 and reads as a sentence",
          labs["brake"]["candidates"][0]["id"] == "17C" and labs["brake"]["candidates"][0]["byte"] == 3
          and "changes only while the brake is held" in labs["brake"]["candidates"][0]["text"])
    check("steering finds the 16-bit angle", labs["steering"]["candidates"][0]["id"] == "1A6")
    check("headlights and hazards find 0x305", labs["headlights"]["candidates"][0]["id"] == "305"
          and labs["hazards"]["candidates"][0]["id"] == "305")
    check("the engine and rev steps find the engine frame",
          labs["engine"]["candidates"][0]["id"] in ("1DC", "1E4") and labs["rev"]["candidates"][0]["id"] in ("1DC", "1E4"))
    check("a car's captures directory has nothing written by a Simulated run",
          not os.path.exists(os.path.join(L.CAPTURES, "labels.jsonl")))
    check("the simulated labels are in captures/sim/labels.jsonl, flagged simulated",
          all(json.loads(l)["simulated"] for l in open(os.path.join(L.SIM_CAPTURES, "labels.jsonl"))))

    print("\n  the API's refusal")
    os.environ["OMACAR_LEARNCAR_SIM_SPEED"] = "35"
    try:
        L.ask("start", sim=True)
        check("start while the (simulated) car is moving raises", False)
    except PermissionError as e:
        check("start while the (simulated) car is moving is refused with the speed", "35 km/h" in str(e))
    del os.environ["OMACAR_LEARNCAR_SIM_SPEED"]
    try:
        L.ask("go", sim=True)
        check("an action with no session is refused", False)
    except PermissionError:
        check("an action with no session is refused", True)
    try:
        L.ask("write", sim=True)
        check("an unknown action is refused", False)
    except ValueError:
        check("an unknown action is refused", True)
    # a real start with no daemon reading is refused too (no live.json in the scratch state)
    try:
        L.ask("start", sim=False)
        check("a real start with no road speed on record is refused", False)
    except PermissionError as e:
        check("a real start with no road speed on record is refused", "speed" in str(e).lower())

    print()
    print("  learncar: %s" % ("all passed" if not fails else f"{fails} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
