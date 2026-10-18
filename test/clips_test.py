#!/usr/bin/env python3
"""Incident clips (lib/clips.py): the trigger maths, the protection from the
recorder's pruning, and the service end to end against a fake recorder folder.

The trigger maths is held to synthetic speed traces: a hard stop, a gentle
stop, noisy samples, a single bad sample, and a hand-off gap where SPEED is
missing, which must never be read as a crash.

THE ONE THAT MATTERS MOST: the janitor (camstore.janitor) deletes the loop's
oldest minute-long clips to stay under budget. An incident's footage must
survive it, byte for byte, and camstore must neither see nor count incidents.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

SCRATCH = tempfile.mkdtemp(prefix="omacar-clips-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
os.environ["OMACAR_VIDEOS"] = os.path.join(SCRATCH, "videos")

import camstore  # noqa: E402
import clips     # noqa: E402

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


G = clips.G


def trace(v0, g, secs_before=3.0, secs_after=5.0, hz=5, rnd=None, quant=True):
    """(t, kph) samples: steady at v0, then slowing at `g` until stopped."""
    out, v, dt = [], float(v0), 1.0 / hz
    for i in range(int(secs_before * hz)):
        out.append((i * dt, v0))
    t = secs_before
    for i in range(int(secs_after * hz)):
        v = max(0.0, v - g * G * dt * 3.6)
        t = secs_before + (i + 1) * dt
        out.append((t, v))
    if rnd is not None:
        out = [(t, v + rnd()) for t, v in out]
    if quant:                      # OBD speed is whole km/h
        out = [(t, float(round(v))) for t, v in out]
    return out


def run_brake(samples, **kw):
    det = clips.BrakeDetector(**kw)
    hits = []
    for t, v in samples:
        r = det.feed(t, v)
        if r:
            hits.append((t, r))
    return hits


def lcg(seed):
    s = [seed]

    def r():
        s[0] = (s[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return (s[0] / 0x7FFFFFFF) - 0.5
    return r


def main():
    print("\n  hard braking")
    hits = run_brake(trace(60, 0.70))
    check("a 0.70 g stop from 60 km/h fires", len(hits) >= 1)
    check("... within 0.8 s of the first slowing sample",
          hits and 3.0 < hits[0][0] <= 3.8)
    check("... reporting a figure over the bar",
          hits and 0.45 <= hits[0][1]["peak_g"] <= 0.9)
    check("... and the speed before it",
          hits and 55 <= hits[0][1]["speed_before"] <= 61)
    check("a 0.55 g stop fires", len(run_brake(trace(80, 0.55))) >= 1)
    check("a 0.30 g firm stop does not", run_brake(trace(60, 0.30)) == [])
    check("a gentle 0.15 g stop does not", run_brake(trace(50, 0.15, secs_after=9)) == [])
    check("a 0.40 g stop, just under, does not", run_brake(trace(70, 0.38)) == [])
    check("a hard stop from 12 km/h is under the floor", run_brake(trace(12, 0.8)) == [])
    check("a hard stop from 40 fires even if it only reaches 10",
          len(run_brake([(i * 0.2, v) for i, v in enumerate([40, 40, 40, 33, 26, 19, 14, 10])])) >= 1)

    print("\n  noise and glitches")
    check("steady 60 km/h with +-1.5 km/h noise never fires",
          run_brake(trace(60, 0.0, secs_before=20, secs_after=0, rnd=lcg(7)) +
                    [(20 + i * 0.2, 60 + 3 * (i % 2 - 0.5)) for i in range(50)]) == [])
    steady = [(i * 0.2, 60.0) for i in range(30)]
    glitch = list(steady)
    glitch[20] = (glitch[20][0], 49.0)       # one sample dips 11 km/h and comes back
    check("one bad sample (an 11 km/h dip) never fires", run_brake(glitch) == [])
    glitch2 = list(steady[:20]) + [(4.0, 44.0)]   # ... and a dip as the LAST sample seen
    check("a dip as the last sample does not fire either", run_brake(glitch2) == [])
    ramp_noise = run_brake(trace(60, 0.55, rnd=lcg(3), quant=True))
    check("a real hard stop with noise on every sample still fires", len(ramp_noise) >= 1)

    print("\n  a hand-off, where SPEED goes missing")
    base = [(i * 0.2, 80.0) for i in range(15)]
    gap = base + [(3.0 + i * 0.2, None) for i in range(15)] + \
        [(6.0 + i * 0.2, 25.0) for i in range(10)]
    check("80 km/h, SPEED missing for 3 s, then 25 km/h: no trigger", run_brake(gap) == [])
    jump = base + [(3.0 + 3.0 + i * 0.2, 25.0) for i in range(10)]
    check("... and a 3 s hole in the timestamps (no None): no trigger", run_brake(jump) == [])
    det = clips.BrakeDetector()
    for t, v in base:
        det.feed(t, v)
    det.feed(3.0, None)
    check("a missing speed clears the history", len(det.win) == 0)
    check("non-numbers are missing too",
          clips.BrakeDetector().feed(1.0, "fast") is None and clips.BrakeDetector().feed(1.0, True) is None
          and clips.BrakeDetector().feed(1.0, float("nan")) is None)
    d2 = clips.BrakeDetector()
    d2.feed(1.0, 60)
    check("a repeated or older timestamp is ignored", d2.feed(1.0, 20) is None and d2.feed(0.5, 20) is None)
    d3 = clips.BrakeDetector()
    for t, v in trace(60, 0.7):
        if d3.feed(t, v):
            break
    check("after it fires the history is clear, so it cannot fire twice on one stop",
          len(d3.win) == 0)

    print("\n  the accelerometer")
    j = clips.JoltDetector()
    flat = [(i * 0.05, (0.0, 0.0, G)) for i in range(40)]
    check("a tablet at rest never fires", all(j.feed(t, xyz) is None for t, xyz in flat))
    j = clips.JoltDetector()
    import math
    tilt = []
    for i in range(80):          # turned slowly through 90 degrees: same magnitude
        a = (i / 79) * math.pi / 2
        tilt.append((i * 0.05, (G * math.sin(a), 0.0, G * math.cos(a))))
    check("tilting it never fires (the magnitude is the same)",
          all(j.feed(t, xyz) is None for t, xyz in tilt))
    j = clips.JoltDetector()
    for t, xyz in flat:
        j.feed(t, xyz)
    got = j.feed(2.05, (0.0, 0.0, G + 2.4 * G))
    check("a 2.4 g spike fires and reports it", got is not None and 2.2 <= got <= 2.6)
    j = clips.JoltDetector()
    for t, xyz in flat:
        j.feed(t, xyz)
    check("a 0.8 g bump does not", j.feed(2.05, (0.0, 0.0, G + 0.8 * G)) is None)
    j = clips.JoltDetector()
    for t, xyz in flat:
        j.feed(t, xyz)
    check("a spike after a 5 s gap is a new baseline, not a trigger",
          j.feed(7.0, (0.0, 0.0, G + 3 * G)) is None)
    check("garbage in is None out", clips.JoltDetector().feed(1.0, ("a", 1, 2)) is None)

    sysfs = os.path.join(SCRATCH, "iio")
    check("no iio directory: no accelerometer", clips.find_accel(sysfs) is None)
    dev = os.path.join(sysfs, "iio:device3")
    os.makedirs(dev)
    open(os.path.join(dev, "in_accel_x_raw"), "w").write("10\n")
    check("an incomplete device is skipped", clips.find_accel(sysfs) is None)
    open(os.path.join(dev, "in_accel_y_raw"), "w").write("-20\n")
    open(os.path.join(dev, "in_accel_z_raw"), "w").write("1000\n")
    open(os.path.join(dev, "in_accel_scale"), "w").write("0.0098\n")
    acc = clips.find_accel(sysfs)
    check("found, with its shared scale", acc is not None and abs(acc["scale"] - 0.0098) < 1e-9)
    x, y, z = clips.read_accel(acc)
    check("raw x scale -> m/s^2", abs(z - 9.8) < 1e-6 and abs(y + 0.196) < 1e-6)
    os.remove(os.path.join(dev, "in_accel_z_raw"))
    check("a file that vanishes reads as None, not an exception", clips.read_accel(acc) is None)

    print("\n  debounce, ring, samples")
    d = clips.Debounce()
    check("first is allowed", d.allow(100.0))
    check("19.9 s later is not", not d.allow(119.9))
    check("20.0 s after the first is", d.allow(120.0))
    r = clips.Ring(span=10)
    for i in range(30):
        r.add(float(i), {"speed": i})
    check("the ring keeps only its span", r.items[0]["t"] >= 19.0 and len(r.items) <= 11)
    check("window() picks by time", [x["t"] for x in r.window(25, 27)] == [25.0, 26.0, 27.0])
    live = {"connected": True, "t": 5.0, "values": {"SPEED": 42, "RPM": 1800.4, "THROTTLE_POS": 20,
                                                    "HYBRID_BATTERY_REMAINING": 61.5}}
    row = clips.sample_of(live, 5.0)
    check("a live sample becomes a telemetry row",
          row == {"speed": 42.0, "rpm": 1800.4, "throttle": 20.0, "soc": 61.5, "ima_amps": None})
    check("the simulator's speed is not the car's", clips.sample_of(dict(live, simulated=True), 5) is None)
    check("no connection, no row", clips.sample_of(dict(live, connected=False), 5) is None)
    check("no speed, no row", clips.sample_of({"connected": True, "values": {"RPM": 900}}, 5) is None)

    print("\n  the loop's pruning cannot touch an incident")
    vroot = os.environ["OMACAR_VIDEOS"]
    T = 1_800_000_000.0                     # a fixed 'now' for the fake footage
    made = {}
    for role in camstore.ROLES:
        os.makedirs(os.path.join(vroot, role), exist_ok=True)
        for k in range(-3, 3):             # six one-minute clips around T
            st = T + k * 60
            name = camstore.clip_name(st)
            p = os.path.join(vroot, role, name)
            body = f"{role}-{k}".encode() * 200
            with open(p, "wb") as f:
                f.write(body)
            made[(role, name)] = body
    ring = [{"t": T - 10 + i * 0.2, "speed": 60.0} for i in range(51)]
    inc = clips.Incident.begin(vroot, "brake", T, 0.62, 60.0, ring)
    linked = sorted(n for n in os.listdir(inc.dir) if clips.MEDIA_RE.match(n))
    # window T-30..T+30: the clips at T-60 (ends T), T, and none after (T+60 starts at t1)
    check("segments covering the window are linked for every camera",
          len(linked) == 3 * 2 and all(any(n.startswith(r + "-") for n in linked) for r in camstore.ROLES))
    first = os.path.join(inc.dir, linked[0])
    check("a link, not a copy: same inode as the loop's file",
          os.stat(first).st_nlink >= 2)
    before_scan = len(camstore.scan(vroot))
    check("camstore.scan does not list incidents", all("incidents" not in c["path"] for c in camstore.scan(vroot)))
    check("camstore.usage does not count them", camstore.usage(vroot)["used"] ==
          sum(len(b) for b in made.values()))
    doomed = camstore.janitor(budget=0, root=vroot)
    check("the janitor really did prune the loop", len(doomed) >= 12)
    survive = True
    for n in linked:
        role = n.split("-")[0]
        seg = n[len(role) + 1:]
        with open(os.path.join(inc.dir, n), "rb") as f:
            survive = survive and f.read() == made[(role, seg)]
    check("every incident file survives the janitor, byte for byte", survive)
    check("the loop's own names are gone (the janitor ran)",
          not os.path.exists(os.path.join(vroot, "front", camstore.clip_name(T - 60))))
    camstore.mark("saved", t=T + 3000, root=vroot, now=T + 3100)
    check("camstore.mark/settle leave incidents alone",
          all(os.path.isfile(os.path.join(inc.dir, n)) for n in linked))
    check("camstore.list_clips still shows no incident", before_scan >= 0 and all(
        "incidents" not in c.get("file", "") for c in camstore.list_clips(root=vroot)))

    print("\n  finishing, joining, telemetry")
    ff = shutil.which("ffmpeg")
    if ff:
        jroot = os.path.join(SCRATCH, "joinroot")
        os.makedirs(os.path.join(jroot, "front"))
        for k in (-1, 0):
            p = os.path.join(jroot, "front", camstore.clip_name(T + k * 60))
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=5:duration=60", "-c:v", "libx264", "-preset",
                            "ultrafast", "-g", "10", "-pix_fmt", "yuv420p",
                            "-movflags", "+frag_keyframe+empty_moov+default_base_moof", p],
                           check=True, capture_output=True, timeout=120)
        rows = [{"t": T - 30 + i * 0.2, "speed": 60.0 if i < 150 else max(0.0, 60 - (i - 150) * 3.0),
                 "rpm": 2000.0} for i in range(0, 301)]
        i2 = clips.Incident.begin(jroot, "brake", T, 0.6, None, rows)
        meta = i2.finish(T + 60)
        check("ffmpeg joined the camera into one file", meta["state"] == "ready"
              and meta["roles"]["front"]["joined"] and os.path.isfile(os.path.join(i2.dir, "front.mp4")))
        part = meta["roles"]["front"]["parts"][0]
        check("... about 60 s long, starting at or just before T-30",
              55 <= part["dur"] <= 66 and T - 33 <= part["start"] <= T - 29)
        check("... and the loose segments were let go of (links removed, loop untouched)",
              not [n for n in os.listdir(i2.dir) if clips.MEDIA_RE.match(n) and n != "front.mp4"]
              and os.path.isfile(os.path.join(jroot, "front", camstore.clip_name(T))))
    else:
        print("    (no ffmpeg here: the join is not exercised)")
    # No ffmpeg at all: the segments stay as parts.
    nroot = os.path.join(SCRATCH, "noffmpeg")
    os.makedirs(os.path.join(nroot, "rear"))
    for k in (-1, 0):
        open(os.path.join(nroot, "rear", camstore.clip_name(T + k * 60)), "wb").write(b"x" * 5000)
    i3 = clips.Incident.begin(nroot, "jolt", T, 2.1, None, [{"t": T - 5, "speed": 30.0}])
    m3 = i3.finish(T + 60, ffmpeg=False)
    check("without ffmpeg the segments are kept as parts, and it says so",
          m3["state"] == "ready" and not m3["roles"]["rear"]["joined"]
          and len(m3["roles"]["rear"]["parts"]) == 2 and "ffmpeg" in (m3["note"] or ""))
    tel = json.load(open(os.path.join(i3.dir, "telemetry.json")))
    check("telemetry is in seconds from the trigger", tel["rows"][0]["dt"] == -5.0)
    check("a 60 s window keeps only 30 s each side",
          all(-30 <= r["dt"] <= 30 for r in clips.telemetry_doc(
              [{"t": T + d, "speed": 1.0} for d in range(-60, 61)],
              clips.make_meta("x", "jolt", T, None, None))["rows"]))

    print("\n  the service, driven by a synthetic drive")
    sroot = os.path.join(SCRATCH, "svcroot")
    for role in ("front", "rear"):
        os.makedirs(os.path.join(sroot, role))
        for k in (-1, 0, 1):
            open(os.path.join(sroot, role, camstore.clip_name(T + k * 60)), "wb").write(b"v" * 3000)
    clock = [T]
    svc = clips.Service(root=sroot, cfg=clips.load_config(os.path.join(SCRATCH, "none.json")),
                        accel=None, clock=lambda: clock[0])
    stop = trace(60, 0.7, secs_before=40, secs_after=40)
    fired = []
    for t, v in stop:
        clock[0] = T - 40 + t
        svc.tick(live={"connected": True, "t": T - 40 + t, "values": {"SPEED": v, "RPM": 1800}})
        if svc.last and svc.last["id"] not in fired:
            fired.append(svc.last["id"])
    check("the stop started exactly one incident", len(fired) == 1 and fired[0].endswith("-brake"))
    check("... and it finished (window closed) with telemetry from both sides",
          clips.get_incident(fired[0], sroot)["incident"]["state"] in ("ready", "no-video"))
    got = clips.get_incident(fired[0], sroot)
    dts = [r["dt"] for r in got["telemetry"]["rows"]]
    check("telemetry spans about -30 s .. +30 s", dts and dts[0] <= -29 and dts[-1] >= 29)
    check("the incident's peak is the stop's real 0.7 g, not the first figure over the bar",
          0.6 <= got["incident"]["peak_g"] <= 0.85)
    check("speed before is the cruising speed, after is 0",
          got["incident"]["speed_before"] == 60.0 and got["incident"]["speed_after"] == 0.0)
    clock[0] = svc.deb.last + 5
    svc.trigger("brake", clock[0])
    check("a second trigger inside 20 s is dropped", len(clips.list_incidents(sroot)) == 1)
    clock[0] += 30
    svc.write()
    check("the service says it is running", clips.read_public()["running"])
    check("asking while it runs works", clips.ask("save") is True)
    svc.tick(live=None)
    check("the ask became a manual incident", any(m["trigger"] == "manual" for m in clips.list_incidents(sroot)))
    hold = []
    for m in clips.list_incidents(sroot):
        hold.append(m["id"])
    check("incident ids look like ids, newest first",
          all(clips.ID_RE.match(i) for i in hold)
          and [m["t"] for m in clips.list_incidents(sroot)] == sorted([m["t"] for m in clips.list_incidents(sroot)], reverse=True))
    st = clips.read_public(now=time.time() + 60)
    check("a state file nobody has written lately is not 'running'", st["running"] is False)
    os.remove(clips.STATE)
    check("with no service, ask() says so", clips.ask("save") is False)

    print("\n  a restart mid-incident")
    rroot = os.path.join(SCRATCH, "rroot")
    os.makedirs(os.path.join(rroot, "front"))
    open(os.path.join(rroot, "front", camstore.clip_name(T)), "wb").write(b"z" * 4000)
    c0 = clips.Incident.begin(rroot, "manual", T, None, None, [{"t": T - 3, "speed": 20.0}])
    svc2 = clips.Service(root=rroot, accel=None, clock=lambda: T + 500)
    m = clips.get_incident(c0.meta["id"], rroot)["incident"]
    check("a new service closes what the old one left open", m["state"] in ("ready", "no-video"))

    print("\n  names, delete, export")
    check("a hostile id is no id", clips.get_incident("../../etc", rroot) is None
          and clips.media_path("../x", "front.mp4", rroot) is None)
    check("a hostile file name is no file", clips.media_path(c0.meta["id"], "../incident.json", rroot) is None
          and clips.media_path(c0.meta["id"], "front.mp4/../../x.mp4", rroot) is None)
    mp = os.path.join(c0.dir, "front-" + camstore.clip_name(T)[:-4] + ".mp4")
    if os.path.isfile(mp):
        check("a real file is found", clips.media_path(c0.meta["id"], os.path.basename(mp), rroot) == mp)
    dest = clips.export_incident(c0.meta["id"], rroot)
    check("export copies into <videos>/exports/<id>", dest.startswith(os.path.join(rroot, "exports"))
          and os.path.isfile(os.path.join(dest, "incident.json")))
    check("... as real copies, not links",
          all(os.stat(os.path.join(dest, n)).st_nlink == 1 for n in os.listdir(dest)))
    check("deleting an unknown id deletes nothing", clips.delete_incident("nope", rroot) is False)
    check("deleting an incident removes it", clips.delete_incident(c0.meta["id"], rroot) is True
          and clips.get_incident(c0.meta["id"], rroot) is None)
    check("... but the loop's file is untouched",
          os.path.isfile(os.path.join(rroot, "front", camstore.clip_name(T))))
    check("... and the export stays", os.path.isfile(os.path.join(dest, "incident.json")))

    print("\n  the simulator")
    sim_root = os.path.join(SCRATCH, "simroot")
    sm = clips.make_sim(sim_root, now=T)
    check("it is labelled Simulated", sm["sim"] and sm["trigger"] == "sim" and sm["label"] == "Simulated")
    check("... with telemetry that is a stop",
          clips.get_incident(sm["id"], sim_root)["telemetry"]["speed_after"] == 0.0)
    sm2 = clips.make_sim(os.path.join(SCRATCH, "simroot2"), now=T, ffmpeg=False)
    check("without ffmpeg it says there is no video, and still has telemetry",
          sm2["state"] == "no-video" and "ffmpeg" in sm2["note"])
    if ff:
        check("with ffmpeg it makes video for all three cameras", sm["state"] == "ready" and len(sm["roles"]) == 3)

    shutil.rmtree(SCRATCH, ignore_errors=True)
    print()
    if fails:
        print(f"  {fails} FAILED")
        return 1
    print("  all clips checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
