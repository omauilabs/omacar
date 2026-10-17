#!/usr/bin/env python3
"""The AUX equalizer's backend (lib/eq.py): the maths, the clamps, the state it
accepts, the PipeWire config it writes, and what it does -- and does not -- run.

Nothing here touches a real PipeWire or systemd: eq._run is replaced with a
recorder, and every file goes to a scratch XDG tree.
"""

import json
import math
import os
import re
import shutil
import sys
import tempfile
import time

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


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


scratch = tempfile.mkdtemp(prefix="eq-test-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
import eq  # noqa: E402


def band(typ="peaking", freq=1000, gain=0, q=1, on=True):
    return {"type": typ, "freq": freq, "gain": gain, "q": q, "on": on}


def flat():
    return eq.default_bands()


def near(a, b, tol):
    return abs(a - b) <= tol


print("\n  the maths")
c = eq.biquad("peaking", 1000, 1, 6)
check("peaking +6 dB @1k Q1 is +6.00 dB at 1 kHz", near(eq.mag_db(c, 1000), 6.0, 0.01))
check("...and ~0 at 20 Hz", near(eq.mag_db(c, 20), 0, 0.05))
check("low shelf +6 is +3.00 at its corner",
      near(eq.mag_db(eq.biquad("lowshelf", 1000, 0.707, 6), 1000), 3.0, 0.01))
check("low cut Q0.707 is -3.01 at its corner",
      near(eq.mag_db(eq.biquad("highpass", 1000, 0.707, 0), 1000), -3.0116, 0.01))
check("a cut at Q4 peaks +12.04 at its corner (the web-audio biquad PipeWire runs)",
      near(eq.mag_db(eq.biquad("highpass", 1000, 4, 0), 1000), 12.0412, 0.01))
check("a lowpass at Nyquist is the identity",
      eq.biquad("lowpass", 24000, 1, 0) == (1.0, 0.0, 0.0, 0.0, 0.0))
check("a highpass at 1 Hz is nearly the identity at 1 kHz",
      near(eq.mag_db(eq.biquad("highpass", 1, 0.707, 0), 1000), 0, 0.01))

print("\n  the automatic preamp")
check("flat: 0 dB", eq.auto_preamp(flat()) == 0)
b = flat(); b[5].update(gain=6)
check("+6 dB boost: -6.0 dB", eq.auto_preamp(b) == -6.0)
b = flat(); b[5].update(gain=-9)
check("a cut-only curve: 0 dB (never boosts to make up)", eq.auto_preamp(b) == 0)
vocal = eq.BUILTIN_PRESETS["Vocal clarity"]
check("Vocal clarity: peak 4.11, preamp -4.2",
      near(eq.peak_db(vocal), 4.1099, 0.005) and eq.auto_preamp(vocal) == -4.2)
check("Vocal clarity curve matches the JS test's numbers",
      [round(v, 4) for v in eq.curve_db(vocal, [50, 300, 1500, 3000, 9000])]
      == [-0.4314, -1.6311, 2.4881, 4.1034, 0.8051])
import random  # noqa: E402
rnd = random.Random(7)
worst = -99
for _ in range(60):
    bb = flat()
    for x in bb:
        x.update(gain=round(rnd.uniform(-12, 12) * 2) / 2, q=rnd.uniform(0.3, 8),
                 freq=20 * 1000 ** rnd.random())
    worst = max(worst, eq.peak_db(bb) + eq.auto_preamp(bb))
check(f"60 random 10-band curves: the maximum after the preamp is <= 0 dB (worst {worst:+.4f})",
      worst <= 1e-9)
b = flat(); b[5].update(gain=6)
check("the user may lower it (-9 stays -9) but not raise it (-2 and 0 stay -6.0)",
      eq.effective_preamp(b, -9) == -9 and eq.effective_preamp(b, -2) == -6.0
      and eq.effective_preamp(b, 0) == -6.0 and eq.effective_preamp(b, None) == -6.0)
for name, p in eq.BUILTIN_PRESETS.items():
    after = eq.peak_db(p) + eq.effective_preamp(p)
    check(f"built-in {name}: peak after preamp {after:+.3f} dB <= 0", after <= 1e-9)

print("\n  clamping and validation")
cl = eq.clean_band(band("peaking", 5, 40, 99))
check("freq 5 -> 20, gain 40 -> +12, Q 99 -> 8", (cl["freq"], cl["gain"], cl["q"]) == (20.0, 12.0, 8.0))
cl = eq.clean_band(band("peaking", 99999, -40, 0.01))
check("freq 99999 -> 20000, gain -40 -> -12, Q 0.01 -> 0.3",
      (cl["freq"], cl["gain"], cl["q"]) == (20000.0, -12.0, 0.3))
check("a cut's gain is stored as 0", eq.clean_band(band("highpass", 60, 9, 0.707))["gain"] == 0)
check("an unknown type is refused", raises(lambda: eq.clean_band(band("notch"))))
check("a string for a number is refused", raises(lambda: eq.clean_band(band(freq="1k"))))
check("NaN is refused", raises(lambda: eq.clean_band(band(gain=float("nan")))))
check("a bool is not a number", raises(lambda: eq.clean_band(band(gain=True))))
st = eq.default_state()
st["bands"] = st["bands"][:9]
check("nine bands are refused", raises(lambda: eq.clean_state(st)))
check("not an object is refused", raises(lambda: eq.clean_state([])))
st = eq.default_state()
st["preamp"] = 5
check("a preamp above 0 is held to 0", eq.clean_state(st)["preamp"] == 0.0)
st["preamp"] = -90
check("a preamp below -24 is held to -24", eq.clean_state(st)["preamp"] == -24.0)
st = eq.default_state()
st["user_presets"] = {"Flat": {"bands": flat(), "preamp": None}}
check("a user preset may not take a built-in's name", raises(lambda: eq.clean_state(st)))
st["user_presets"] = {"x" * 40: {"bands": flat(), "preamp": None}}
check("a name over 24 characters is refused", raises(lambda: eq.clean_state(st)))
st["user_presets"] = {f"p{i}": {"bands": flat(), "preamp": None} for i in range(30)}
check("more than 24 user presets are refused", raises(lambda: eq.clean_state(st)))
check("10 default bands: 31 Hz low shelf ... 16 kHz high shelf, all flat",
      len(flat()) == 10 and flat()[0]["type"] == "lowshelf" and flat()[0]["freq"] == 31
      and flat()[9]["type"] == "highshelf" and flat()[9]["freq"] == 16000
      and all(x["gain"] == 0 for x in flat()))
check("Car cabin: -2 dB peaking @125 Q1 and +2 dB high shelf @8 kHz",
      eq.BUILTIN_PRESETS["Car cabin"][2] == {"type": "peaking", "freq": 125.0, "gain": -2.0, "q": 1.0, "on": True}
      and eq.BUILTIN_PRESETS["Car cabin"][8]["type"] == "highshelf"
      and eq.BUILTIN_PRESETS["Car cabin"][8]["gain"] == 2.0)
check("Loudness: low shelf +4 @80, high shelf +3 @10k",
      eq.BUILTIN_PRESETS["Loudness"][0]["gain"] == 4.0 and eq.BUILTIN_PRESETS["Loudness"][0]["freq"] == 80
      and eq.BUILTIN_PRESETS["Loudness"][9]["gain"] == 3.0 and eq.BUILTIN_PRESETS["Loudness"][9]["freq"] == 10000)
check("Night: low cut 60 Hz, -3 dB high shelf @6k",
      eq.BUILTIN_PRESETS["Night"][0]["type"] == "highpass" and eq.BUILTIN_PRESETS["Night"][0]["freq"] == 60
      and eq.BUILTIN_PRESETS["Night"][9]["gain"] == -3.0 and eq.BUILTIN_PRESETS["Night"][9]["freq"] == 6000)
check("six built-ins, each a valid ten-band state",
      list(eq.BUILTIN_PRESETS) == ["Flat", "Car cabin", "Bass", "Vocal clarity", "Loudness", "Night"]
      and all(eq.clean_bands(p) for p in eq.BUILTIN_PRESETS.values()))

print("\n  the generated config")
conf = eq.generate_config(eq.default_state())
check("it is a pipewire config with the filter-chain module",
      "libpipewire-module-filter-chain" in conf and "libpipewire-module-protocol-native" in conf
      and "libpipewire-module-adapter" in conf)
check("the input node is a smart filter named omacar-eq, an Audio/Sink",
      "filter.smart       = true" in conf and 'filter.smart.name  = "omacar-eq"' in conf
      and "media.class        = Audio/Sink" in conf)
check("it has NO filter.smart.target (it attaches in front of the default device)",
      "filter.smart.target" not in re.sub(r"#.*", "", conf))
check("it never claims to be the default sink (low session priority, no default props)",
      "priority.session   = 1" in conf and "default" not in re.sub(r"#.*|DEFAULT", "", conf).lower().replace("default sink", ""))
check("ten band nodes plus the preamp", conf.count("type = builtin") == 11)
check("the labels are PipeWire's: bq_lowshelf, bq_peaking, bq_highshelf",
      "bq_lowshelf" in conf and "bq_peaking" in conf and "bq_highshelf" in conf)
check("links chain preamp -> band 1 -> ... -> band 10", conf.count('input = "') == 10
      and '"preamp:Out" input = "eq_band_1:In"' in conf and '"eq_band_9:Out" input = "eq_band_10:In"' in conf)
check("the preamp is a linear builtin with a Mult", 'label = linear' in conf and '"Mult" = 1.000000' in conf)
st = eq.clean_state(dict(eq.default_state(), bands=eq.BUILTIN_PRESETS["Bass"]))
conf = eq.generate_config(st)
check("Bass: the preamp's Mult is the automatic -5.1 dB",
      f'"Mult" = {10 ** (-5.1 / 20):.6f}' in conf)
night = eq.clean_state(dict(eq.default_state(), bands=eq.BUILTIN_PRESETS["Night"]))
conf = eq.generate_config(night)
check("Night: band 1 is a bq_highpass at 60 Hz", re.search(r"eq_band_1 label = bq_highpass\s+control = \{ \"Freq\" = 60.00", conf))
night["bands"][0]["on"] = False
check("a disabled CUT is left out of the graph (it cannot be made to do nothing)",
      "eq_band_1 " not in eq.generate_config(night) and eq.structure(night)[0] is None)
pk = eq.default_state()
pk["bands"][4].update(on=False, gain=5)
check("a disabled peak stays in the graph at 0 dB (it can be switched live)",
      eq.structure(pk)[4] == "peaking" and eq.band_controls(pk["bands"][4])["Gain"] == 0.0)
a, b2 = eq.default_state(), eq.default_state()
b2["bands"][3]["gain"] = 4
check("moving a gain does not change the structure; changing a type does",
      eq.structure(a) == eq.structure(b2) and eq.structure(a) != eq.structure(
          dict(a, bands=[dict(x, type="highshelf") if i == 3 else x for i, x in enumerate(a["bands"])])))
check("the unit file runs the generated config as its own pipewire",
      "ExecStart=/usr/bin/pipewire -c %h/.local/state/omacar/eq/omacar-eq.conf"
      in open(os.path.join(ROOT, "share", "systemd", "omacar-eq.service")).read())

print("\n  files")
eq.save(night)
p = eq.config_path()
check("saved under XDG_CONFIG_HOME/omacar/eq.json", p == os.path.join(scratch, "config", "omacar", "eq.json") and os.path.exists(p))
check("no temp file left beside it", [f for f in os.listdir(os.path.dirname(p)) if f.startswith(".eq-")] == [])
check("it loads back the same", eq.load() == night)
open(p, "w").write("{broken")
check("a corrupt file loads as the default, not an exception", eq.load() == eq.default_state())
check("the config is written under XDG_STATE_HOME/omacar/eq/omacar-eq.conf",
      eq.write_config(eq.default_state()) is True
      and eq.conf_path() == os.path.join(scratch, "state", "omacar", "eq", "omacar-eq.conf")
      and os.path.exists(eq.conf_path()))
check("writing the same config again says it changed nothing", eq.write_config(eq.default_state()) is False)

print("\n  without PipeWire")
real_run, real_which = eq._run, eq.shutil.which
eq.shutil.which = lambda n: None
d = eq.describe(eq.default_state())
check("describe says present false with a reason", d["present"] is False and "PipeWire" in d["why"])
check("...and still carries the saved state, presets and curve",
      len(d["state"]["bands"]) == 10 and len(d["presets"]["builtin"]) == 6 and len(d["curve"]["db"]) == 128)
check("apply does nothing and says it is unavailable", eq.apply(eq.default_state())["mode"] == "unavailable")
eq.shutil.which = lambda n: "/usr/bin/" + n

calls = []
state = {"active": False}


def fake(args, timeout):
    calls.append(list(args))
    if args[:2] == ["pw-cli", "info"]:
        return 0, ""
    if args[:3] == ["systemctl", "--user", "is-active"]:
        return (0, "active\n") if state["active"] else (3, "inactive\n")
    if args[:3] == ["systemctl", "--user", "restart"] or args[:3] == ["systemctl", "--user", "enable"]:
        state["active"] = True
        return 0, ""
    if args[:3] == ["systemctl", "--user", "disable"]:
        state["active"] = False
        return 0, ""
    if args[0] == "pw-dump":
        return 0, json.dumps([{"id": 77, "type": "PipeWire:Interface:Node",
                               "info": {"props": {"node.name": "omacar-eq"}}}])
    if args[:2] == ["pw-cli", "set-param"]:
        return 0, ""
    return 127, ""


eq._run = fake
on = eq.clean_state(dict(eq.default_state(), enabled=True))
r = eq.apply(on)
check("enabled, not running: it writes the config and starts the unit", r["mode"] == "restarted"
      and ["systemctl", "--user", "enable", "--now", "omacar-eq.service"] in calls)
calls.clear()
r = eq.apply(on)
check("applied again, nothing changed: no restart (idempotent)", r["mode"] == "unchanged"
      and not any(c[2:3] in (["restart"], ["enable"]) for c in calls if c[0] == "systemctl"))
on2 = json.loads(json.dumps(on)); on2["bands"][3]["gain"] = 5
calls.clear()
r = eq.apply(on2)
sets = [c for c in calls if c[:2] == ["pw-cli", "set-param"]]
check("a changed gain is pushed live with pw-cli set-param, no restart",
      r["mode"] == "live" and len(sets) == 1 and not any(c[2:3] == ["restart"] for c in calls)
      and sets[0][3] == "Props" and '"eq_band_4:Gain" 5.000000' in sets[0][4]
      and '"preamp:Mult"' in sets[0][4])
on3 = json.loads(json.dumps(on2)); on3["bands"][3]["type"] = "highshelf"
calls.clear()
r = eq.apply(on3)
check("a changed band TYPE rewrites the config and restarts the unit",
      r["mode"] == "restarted" and ["systemctl", "--user", "restart", "omacar-eq.service"] in calls
      and "bq_highshelf" in open(eq.conf_path()).read())
off = json.loads(json.dumps(on3)); off["enabled"] = False
calls.clear()
r = eq.apply(off)
check("bypass stops and disables the unit", r["mode"] == "bypassed"
      and ["systemctl", "--user", "disable", "--now", "omacar-eq.service"] in calls)

print("\n  live changes")
eq.save(on)
eq.apply(on)
calls.clear()
eq._live_last = 0
r = eq.live({"bands": {"5": {"gain": 20, "freq": 2000, "q": 2}}})
sets = [c for c in calls if c[:2] == ["pw-cli", "set-param"]]
check("a live gain of 20 is clamped to +12 before it reaches PipeWire",
      r["ok"] and '"eq_band_6:Gain" 12.000000' in sets[0][4] and '"eq_band_6:Freq" 2000.000000' in sets[0][4])
check("the preamp follows the new curve (a +12 dB boost -> about -12 dB)", r["preamp"] <= -11.5)
t0 = time.monotonic()
r2 = eq.live({"bands": {"5": {"gain": 1}}})
check("a second change within 50 ms is throttled (<= 20 per second)", r2.get("throttled") is True)
n = 0
eq._live_last = 0
t_end = time.monotonic() + 0.5
while time.monotonic() < t_end:
    r = eq.live({"bands": {"1": {"gain": 1}}})
    n += 0 if r.get("throttled") else 1
check(f"hammering it for half a second applies at most 11 times (got {n})", 1 <= n <= 11)
r = eq.live({"flat": True})
check("A/B flat is never throttled and sends unity preamp", r["ok"] and r["preamp"] == 0.0)
eq._live_last = 0
check("a live change for a band that does not exist is refused", raises(lambda: eq.live({"bands": {"12": {"gain": 1}}})))
calls.clear()
state["active"] = False
eq._live_last = 0
r = eq.live({"bands": {"1": {"gain": 1}}})
check("live while the unit is not running does not try pw-cli", r["ok"] is False
      and not any(c[:2] == ["pw-cli", "set-param"] for c in calls))
recorded_timeouts = []
eq._run = lambda args, timeout: (recorded_timeouts.append((args[:2], timeout)), fake(args, timeout))[1]
state["active"] = True
eq._live_last = 0
eq._node_id = 77
eq.live({"bands": {"1": {"gain": 2}}})
check("pw-cli set-param runs with a 1 s timeout",
      (["pw-cli", "set-param"], 1.0) in recorded_timeouts)
eq._run = real_run
eq.shutil.which = real_which

print("\n  the API")
import api  # noqa: E402
code, body = api.handle_get("/api/eq", "")
check("GET /api/eq answers with state, present and the curve", code == 200 and "present" in body and "state" in body and "curve" in body)
code, body = api.handle_post("/api/eq", "{nope")
check("POST /api/eq with bad JSON is a 400", code == 400)
code, body = api.handle_post("/api/eq", json.dumps({"enabled": False, "bands": [1] * 10}))
check("POST /api/eq with a malformed band is a 400 with a reason", code == 400 and "error" in body)
good = eq.default_state()
good["bands"][2].update(gain=30)
code, body = api.handle_post("/api/eq", json.dumps(good))
check("POST /api/eq clamps and saves (+30 stored as +12)", code == 200 and body["state"]["bands"][2]["gain"] == 12.0
      and eq.load()["bands"][2]["gain"] == 12.0)
code, body = api.handle_post("/api/eq/live", json.dumps({"bands": "x"}))
check("POST /api/eq/live with nonsense is a 400", code == 400)
import serve  # noqa: E402
check("the read-only cockpit does not get EQ writes",
      "/api/eq" not in serve.COCKPIT_WRITES and "/api/eq/live" not in serve.COCKPIT_WRITES)

shutil.rmtree(scratch, ignore_errors=True)
print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
sys.exit(1 if fails else 0)
