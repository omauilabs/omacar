#!/usr/bin/env python3
"""Where did I park (lib/parkspot.py): the capture decision, the level estimate
from pressure, the storage, and the privacy of what reaches the phone.

Pure decisions on a fake clock, then the files in a scratch state dir. No
camera, no GPS, no sensor, no real clock.
"""

import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-parkspot-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import parkspot as P
    import notify
    try:
        ON, OFF, UNK = {"powered": True}, {"powered": False}, {"powered": None}
        quiet = {"age": None, "speed": None, "answering": False}

        def drive(st, steps):
            """steps: [(t, power, car)] -> list of (t, action)."""
            out = []
            for t, pw, car in steps:
                st, act = P.decide_capture(st, pw, car, t)
                if act:
                    out.append((t, act))
            return st, out

        print("\n  the capture decision")
        st, got = drive(P.fresh_state(), [(0, ON, quiet), (10, OFF, quiet), (12, OFF, quiet), (14.5, OFF, quiet)])
        check("charger off, car quiet: one capture, 4 s in", got == [(14.5, "capture")])
        st, got = drive(st, [(16, OFF, quiet), (20, OFF, quiet)])
        check("and not again while the charger stays off", got == [])
        st, got = drive(P.fresh_state(), [(0, OFF, quiet), (10, OFF, quiet)])
        check("a charger never seen on (boot with it off) is not a parking", got == [])
        st, got = drive(P.fresh_state(), [(0, UNK, quiet), (10, UNK, quiet)])
        check("a charger that cannot be read never captures", got == [])
        moving = {"age": 1.0, "speed": 40.0, "answering": True}
        st, got = drive(P.fresh_state(), [(0, ON, quiet), (10, OFF, moving), (14.5, OFF, moving)])
        check("speed after the charger went off: a cable fault, not a park", got == [])
        st, got = drive(P.fresh_state(), [(0, ON, moving), (8, ON, moving), (10, OFF, quiet), (14.5, OFF, quiet)])
        check("speed BEFORE it went off (driving, stop, switch off) is a park", got == [(14.5, "capture")])
        st, got = drive(P.fresh_state(), [(0, ON, quiet), (10, OFF, quiet), (40, OFF, quiet)])
        check("seen too late (tablet about to sleep): skipped, not taken at 30 s", got == [])
        st, got = drive(P.fresh_state(), [(0, ON, quiet), (10, OFF, quiet), (15, OFF, quiet),
                                          (20, ON, quiet), (30, OFF, quiet), (35, OFF, quiet)])
        check("a second off within five minutes is not a second park", got == [(15, "capture")])
        st, got = drive(P.fresh_state(), [(0, ON, quiet), (10, OFF, quiet), (15, OFF, quiet),
                                          (20, ON, quiet), (400, OFF, quiet), (405, OFF, quiet)])
        check("after five minutes it is", got == [(15, "capture"), (405, "capture")])

        print("\n  the level, from pressure")
        d = P.height_delta_m(1013.25, 899.0)
        check(f"1013.25 hPa to 899 hPa is about 1 km up ({d:.0f} m)", 980 < d < 1010)
        check("lower pressure is higher, higher pressure is lower",
              P.height_delta_m(1000, 990) > 0 > P.height_delta_m(1000, 1010))
        # 3 m is about 0.36 hPa near sea level.
        lv = P.level_estimate(1012.40, 1012.40 + 0.36 * 3)
        check(f"3 levels down reads P3 ({lv['label']}, {lv['delta_m']} m)", lv["label"] == "P3" and lv["levels"] == -3)
        check("and says below the street", "below the street" in lv["detail"])
        up = P.level_estimate(1012.40, 1012.40 - 0.36 * 2)
        check("2 levels up reads P2, above the street", up["label"] == "P2" and "above" in up["detail"])
        check("a 1 m difference is street level, not a level",
              P.level_estimate(1012.4, 1012.4 - 0.12)["label"] == "street level")
        check("every estimate is marked an estimate",
              all(P.level_estimate(1012.4, p)["estimate"] is True for p in (1012.4, 1013.5, 1010.0)))
        check("no reference, no estimate", P.level_estimate(None, 1000) is None and P.level_estimate(1000, None) is None)
        check("an impossible pressure is no estimate", P.level_estimate(1000, 12.0) is None and P.level_estimate(0, 1000) is None)
        now = 10000.0
        ramp = [(now - 300 + i * 10, 1012.4) for i in range(10)] + [(now - 60, 1011.0), (now - 30, 1010.9)]
        check("the street reference ignores the last minute and a half (the ramp)",
              abs(P.street_reference(ramp, now) - 1012.4) < 1e-9)
        check("too few samples: no reference", P.street_reference([(now - 200, 1012.4)], now) is None)
        s = P.note_ref([], 100.0, 1012.0, 40.0)
        check("a street sample is kept only while moving", P.note_ref(s, 130.0, 1012.0, 2.0) == s and len(s) == 1)
        check("and not more than one per 10 s", P.note_ref(s, 104.0, 1012.0, 40.0) == s)
        check("and old ones fall away", P.note_ref(s, 100.0 + P.REF_KEEP_S + 5, None, None) == [])

        print("\n  which fix is saved")
        t = 5000.0
        check("a live fix is saved as it is", P.pick_fix({"fix": True, "lat": 37.7842, "lon": -122.4079, "source": "gpsd"}, t)["lat"] == 37.7842)
        f = P.pick_fix({"fix": False, "lat": None, "recent": [[t - 40, 37.78, -122.40]], "source": "gpsd"}, t)
        check("no fix now, one 40 s ago: that one, with its age", f and f["age_s"] == 40)
        check("a point older than two minutes is not where I parked",
              P.pick_fix({"fix": False, "recent": [[t - 400, 37.78, -122.40]]}, t) is None)
        check("no GPS at all is no fix, never a made-up point",
              P.pick_fix({"present": False, "fix": False, "lat": None, "lon": None, "recent": []}, t) is None
              and P.pick_fix(None, t) is None)

        print("\n  the phone hears nothing about where")
        sent = []
        loc = {"fix": True, "lat": 37.784213, "lon": -122.407955, "source": "gpsd"}
        rec = P.capture(1_700_000_000.0, send=lambda *a, **k: sent.append((a, k)), location=loc, pressure=(1013.48, "sensor"),
                        samples=[(1_700_000_000.0 - 300 + i * 10, 1012.4) for i in range(10)],
                        stills=lambda role, dest, now: (open(dest, "wb").write(b"\xff\xd8" + b"x" * 300 + b"\xff\xd9"),
                                                        {"ok": True, "how": "test"})[1])
        (args, kw) = sent[0]
        text = " ".join(str(a) for a in args) + " " + json.dumps(kw)
        check("one notification, kind parked", args[0] == "parked" and len(sent) == 1)
        check("it carries the time and 'photo saved'", re.search(r"Parked at \d\d:\d\d · photo saved", args[2]) is not None)
        check("no coordinate in it (neither digits of the lat nor the lon)",
              "37.78" not in text and "122.40" not in text and not re.search(r"\d+\.\d{3,}", text))
        check("no address-like words either", not re.search(r"(street|avenue|road| st\b| ave\b)", text, re.I))
        check("the key is per park", kw["key"] == f"parked-{rec['id']}")
        check("and the park keeps the coordinates, on the tablet", rec["fix"]["lat"] == 37.784213)
        check("the level came out of the pressures", rec["level"] and rec["level"]["label"] == "P3" and rec["level"]["estimate"])
        none_sent = []
        P.capture(1_700_001_000.0, send=lambda *a, **k: none_sent.append(a), location={"fix": False}, pressure=(None, None),
                  samples=[], stills=lambda role, dest, now: {"ok": False, "how": "no live picture"})
        check("no photo: the text says only the time", re.fullmatch(r"Parked at \d\d:\d\d", none_sent[0][2]) is not None)
        r2 = P.load_parks()[-1]
        check("no fix, no pressure: stored as nothing, not zero",
              r2["fix"] is None and r2["pressure_hpa"] is None and r2["level"] is None and not r2["front"])
        # The real outbox, through notify.send.
        P.capture(1_700_002_000.0, location=loc, pressure=(None, None), samples=[],
                  stills=lambda role, dest, now: {"ok": False, "how": "x"})
        box = notify.since(0)
        check("through the real outbox: one event per park, none with a coordinate",
              len(box) == 1 and "37.78" not in json.dumps(box) and "-122" not in json.dumps(box))

        print("\n  keeping ten")
        for i in range(14):
            P.capture(1_700_100_000.0 + i * 1000, send=lambda *a, **k: None, location={"fix": False}, pressure=(None, None),
                      samples=[], stills=lambda role, dest, now: (open(dest, "wb").write(b"\xff\xd8" + b"y" * 300 + b"\xff\xd9"),
                                                                  {"ok": True, "how": "t"})[1])
        rows = P.load_parks()
        check("only the last ten parks are kept", len(rows) == 10 and rows[-1]["id"] == 3 + 14)
        files = sorted(os.listdir(P.paths()["dir"]))
        check("and only their photographs (2 each)", len(files) == 20)
        pub = P.read_public()
        check("newest first, with photo URLs", pub["last"]["id"] == rows[-1]["id"]
              and pub["last"]["photos"]["rear"].startswith("/api/parking/photo/"))
        check("a photo path resolves for a recorded id only",
              P.photo_path(rows[-1]["id"], "rear") and P.photo_path(1, "rear") is None
              and P.photo_path("../../etc/passwd", "rear") is None and P.photo_path(rows[-1]["id"], "cabin") is None)

        print("\n  the stills read the recorder, never disturb it")
        live = os.path.join(scratch, "rear.jpg")
        open(live, "wb").write(b"\xff\xd8" + b"z" * 400 + b"\xff\xd9")
        os.utime(live, (900.0, 900.0))
        dest = os.path.join(scratch, "out.jpg")
        check("a fresh live picture is copied", P.still_from_live("rear", dest, 905.0, live_path=live)["ok"]
              and os.path.getsize(dest) == 404)
        check("a stale one is refused (and then the clip is tried)", not P.still_from_live("rear", dest, 990.0, live_path=live)["ok"])
        open(live, "wb").write(b"not a jpeg at all" * 30)
        os.utime(live, (900.0, 900.0))
        check("a half-written one is refused", not P.still_from_live("rear", dest, 905.0, live_path=live)["ok"])
        src = open(os.path.join(ROOT, "lib", "parkspot.py")).read()
        check("the module never stops, signals or restarts anything",
              not re.search(r"systemctl|\.kill\(|\.terminate\(|os\.kill|send_signal", src))

        print("\n  the simulator labels itself")
        P.forget()
        last = P.sim(3, base=1_700_200_000.0)
        pub = P.read_public()
        check("three simulated parks, all labelled", len(pub["history"]) == 3 and all(p["simulated"] for p in pub["history"]))
        check("the newest has a level estimate, the oldest a fix but no level",
              pub["last"]["level"]["label"] == "P3" and pub["history"][-1]["level"] is None)
        P.forget()
        check("forget deletes parks and photographs", P.load_parks() == [] and not os.path.isdir(P.paths()["dir"]))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print()
    if fails:
        print(f"  {fails} FAILED")
        return 1
    print("  every parkspot check holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
