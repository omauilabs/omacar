#!/usr/bin/env python3
"""Drowsy mode's settings and logs: the spec's defaults, the owner's file laid
over them without letting a typo turn a threshold into a string, what the app
may change, and where events and measures are written. Scratch folders only."""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
SCRATCH = tempfile.mkdtemp(prefix="omacar-drowsy-test-")
for _k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_k] = os.path.join(SCRATCH, _k.lower())

import camroutes  # noqa: E402
import drowsycfg  # noqa: E402
import records    # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


head("the defaults are the spec's")
c = drowsycfg.load()
check("the ladder", (c["level1"]["perclos"], c["level2"]["closed_secs"], c["level2"]["perclos"],
                     c["level3"]["closed_secs"], c["min_speed_mph"]), (0.15, 1.0, 0.25, 2.0, 30))
check("on, standard, James, every sound", (c["enabled"], c["sensitivity"], c["name"], c["sounds"]),
      (True, "standard", "James", ["bark", "voice", "alarm"]))

head("the owner's file is laid over them, carefully")
os.makedirs(os.path.dirname(drowsycfg.user_path()), exist_ok=True)
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"level2": {"perclos": 0.3}, "eyes": {"closed_cap": "high"}, "surprise": 1}, f)
c = drowsycfg.load()
check("a threshold tuned by hand is used", c["level2"]["perclos"], 0.3)
check("a typo cannot turn a threshold into a string", c["eyes"]["closed_cap"], 0.8)
check("and a key nobody knows is dropped", "surprise" in c, False)

head("the app may change four things, and only to what exists")
c = drowsycfg.save({"sensitivity": "sensitive", "name": "", "sounds": ["alarm", "alarm", "bark"]})
check("saved", (c["sensitivity"], c["name"], c["sounds"]), ("sensitive", "", ["alarm", "bark"]))
check("and the hand-tuned threshold survives the save", c["level2"]["perclos"], 0.3)
check("a name there is no voice clip for is refused", raises(lambda: drowsycfg.save({"name": "Bob"})), True)
check("so is an empty rotation", raises(lambda: drowsycfg.save({"sounds": []})), True)
check("and a threshold from the app", raises(lambda: drowsycfg.save({"level2": {"perclos": 0.1}})), True)
check("through the route, a refusal is a 400",
      camroutes.handle_post("/api/drowsy", json.dumps({"enabled": "yes"}))[0], 400)
check("and GET /api/drowsy answers with the merged settings",
      camroutes.handle_get("/api/drowsy", "")[1]["sensitivity"], "sensitive")

head("events go to the records book; measures to a daily log")
out = drowsycfg.log_event({"t": 1000.0, "level": 2, "trigger": "closed", "speed_kph": 104.6,
                           "measures": {"perclos": 0.18, "closedFor": 1.2}})
row = sqlite3.connect(records.DB).execute(
    "SELECT kind, label, payload FROM records WHERE id = ?", (out["id"],)).fetchone()
check("kind=drowsy, with level, trigger, speed and the measures",
      (row[0], row[1], json.loads(row[2])["speed_kph"], json.loads(row[2])["measures"]["perclos"]),
      ("drowsy", "Drowsy · Level 2 · closed", 104.6, 0.18))
check("a level that does not exist is refused",
      raises(lambda: drowsycfg.log_event({"level": 4, "trigger": "x"})), True)
w = drowsycfg.log_measures([{"t": 1, "perclos": 0.1}, {"t": 2, "perclos": 0.12}])
lines = open(w["file"], encoding="utf-8").read().splitlines()
check("measures append as one JSON line each", [json.loads(x)["t"] for x in lines], [1, 2])

head("beyond the brief: the keys Task 7 added, the routes, and a bool is not a level")
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"level2": {"voice": "no", "voice_slot_secs": "long"}}, f)
c = drowsycfg.load()
check("level2.voice and voice_slot_secs are type-checked like any threshold",
      (c["level2"]["voice"], c["level2"]["voice_slot_secs"]), (True, 7))
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"level2": {"voice": False, "voice_slot_secs": 8}}, f)
c = drowsycfg.load()
check("and the owner's own values for them are used", (c["level2"]["voice"], c["level2"]["voice_slot_secs"]),
      (False, 8))
check("True is not Level 1", raises(lambda: drowsycfg.log_event({"level": True, "trigger": "closed"})), True)
check("an event through the route is written",
      "id" in camroutes.handle_post("/api/drowsy/event", json.dumps({"level": 1, "trigger": "night"}))[1], True)
check("a log through the route is written",
      camroutes.handle_post("/api/drowsy/log", json.dumps({"rows": [{"t": 3}]}))[1]["written"], 1)
check("rows that are not a list are a 400",
      camroutes.handle_post("/api/drowsy/log", json.dumps({"rows": "x"}))[0], 400)
check("a body that is not an object is a 400", camroutes.handle_post("/api/drowsy", "[1]")[0], 400)

head("fix round 1, I2: a save never rewrites an owner file that does not parse")
for bad_text in ("{bad", '{"level2": {"perclos": 0.3},}\n', "[1, 2]", ""):
    with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
        f.write(bad_text)
    before = open(drowsycfg.user_path(), "rb").read()
    try:
        drowsycfg.save({"sensitivity": "standard"})
        why = None
    except ValueError as e:
        why = str(e)
    after = open(drowsycfg.user_path(), "rb").read()
    check(f"{bad_text[:14]!r}: the save is refused, naming the file, and the file is byte-for-byte unchanged",
          (why is not None and drowsycfg.user_path() in why, after == before), (True, True))
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    f.write('{"level2": {"perclos": 0.3},\n "eyes": {"closed_cap": 0.7,}}\n')
before = open(drowsycfg.user_path(), "rb").read()
code, body = camroutes.handle_post("/api/drowsy", json.dumps({"enabled": False}))
check("through the route it is a 400 whose message gives the line", (code, "line 2" in body.get("error", "")), (400, True))
check("and the file is still the owner's", open(drowsycfg.user_path(), "rb").read(), before)
os.remove(drowsycfg.user_path())
check("with no file at all a save still works", drowsycfg.save({"enabled": True})["enabled"], True)

head("fix round 1 minors: an event's speed and measures, and the owner's sounds")
for wrong in ({"speed_kph": "fast"}, {"speed_kph": True}, {"speed_kph": -5}, {"speed_kph": 1000},
              {"speed_kph": float("nan")}, {"measures": "x"}, {"measures": [0.2]}):
    check(f"an event with {wrong!r} is refused",
          raises(lambda w=wrong: drowsycfg.log_event(dict({"level": 2, "trigger": "closed"}, **w))), True)
ok_ids = [drowsycfg.log_event({"level": 2, "trigger": "closed", "speed_kph": v, "measures": m})["id"]
          for v, m in ((None, None), (0, {}), (104.6, {"perclos": 0.2}))]
check("an unknown speed (None), 0 and 104.6 km/h, and an object or no measures, are written", len(ok_ids), 3)
import contextlib  # noqa: E402
import io  # noqa: E402
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"sounds": ["bark", "horn", 5, "alarm", "bark"]}, f)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    c = drowsycfg.load()
check("unknown sounds in the owner's file are dropped", c["sounds"], ["bark", "alarm"])
check("with a warning that names them", ("horn" in err.getvalue(), "5" in err.getvalue()), (True, True))
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"sounds": ["horn"]}, f)
with contextlib.redirect_stderr(io.StringIO()):
    c = drowsycfg.load()
check("and a rotation with none known left is the spec's", c["sounds"], ["bark", "voice", "alarm"])
os.remove(drowsycfg.user_path())

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  drowsy mode's settings hold\n")
