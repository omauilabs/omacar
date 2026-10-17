#!/usr/bin/env python3
"""The car widget kit's server half (lib/carkit.py): the driving rules that a
server can enforce, enforced. Real subprocesses in a scratch plugin folder;
nothing here touches the car."""
import json, os, shutil, stat, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
fails = 0
def check(msg, cond):
    global fails
    print(f"    {'ok' if cond else 'FAIL'}  {msg}")
    fails += 0 if cond else 1

def plugin(root, pid, spec, data_py=None):
    d = os.path.join(root, pid); os.makedirs(d)
    with open(os.path.join(d, "car.json"), "w") as f: json.dump(spec, f)
    if data_py is not None:
        p = os.path.join(d, "car-data")
        with open(p, "w") as f: f.write("#!" + sys.executable + "\n" + data_py)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return d

def main():
    scratch = tempfile.mkdtemp(prefix="omacar-carkit-")
    os.environ["HOME"] = scratch
    import carkit
    roots = (("omacar", os.path.join(scratch, "p")),)
    live = os.path.join(scratch, "live.json")
    def set_speed(kph, handover=False):
        with open(live, "w") as f:
            json.dump({"connected": True, "t": time.time(), "values": {"SPEED": kph}, "handover": handover}, f)
    try:
        print("\n  validation")
        good = {"schema": 1, "id": "demo", "name": "Demo", "card": {"type": "metric", "value": "$.v"}}
        check("a minimal plugin is valid", carkit.validate(good) == [])
        check("an unknown widget is named", any("unknown widget" in e for e in carkit.validate(dict(good, card={"type": "iframe"}))))
        check("a shell string is not a command",
              any("no shell" in e for e in carkit.validate(dict(good, actions={"x": {"command": "rm -rf /"}}))))
        check("a car_write can never be moving: allowed (rule 5)",
              any("rule 5" in e for e in carkit.validate(dict(good, actions={"x": {"command": ["true"], "car_write": True, "moving": "allowed"}}))))
        check("a button naming an undeclared action is caught",
              any("not declared" in e for e in carkit.validate(dict(good, card={"type": "button", "action": "ghost"}))))
        check("needs are computed, not taken on trust",
              carkit.needs(dict(good, data={"command": ["./x"]}, needs={"runs_code": False}))["runs_code"] is True)

        print("\n  the moving rule (pure)")
        D = carkit.decide_action
        check("parked: a parked-only action runs", D({"moving": "parked"}, False)[0])
        check("moving: a parked-only action is refused, with the reason", D({}, True) == (False, "Available when you stop"))
        check("moving: an allowed action runs", D({"moving": "allowed"}, True)[0])
        check("moving: a car write is refused whatever else it says",
              D({"moving": "allowed", "car_write": True}, True)[0] is False)

        print("\n  running, against real processes")
        plugin(roots[0][1], "demo", dict(good, data={"command": ["./car-data"], "every_s": 1},
               actions={"hi": {"command": ["./car-data"], "moving": "parked"},
                        "go": {"command": ["./car-data"], "moving": "allowed"},
                        "say": {"command": ["./car-data"], "arg": True, "moving": "parked"},
                        "w": {"command": ["./car-data"], "car_write": True, "moving": "parked"}}),
               "import json,sys; print(json.dumps({'v': 7, 'argv': sys.argv[1:]}))")
        e = carkit.find("demo", roots)
        d, err = carkit.run_data(e)
        check(f"a data command's JSON comes back ({d})", err is None and d["v"] == 7)
        set_speed(0)
        check("parked: the action runs", carkit.run_action("demo", "hi", roots, live)[0] == 200)
        set_speed(60)
        check("moving: the parked-only action is refused (409)", carkit.run_action("demo", "hi", roots, live)[0] == 409)
        check("moving: the allowed one runs", carkit.run_action("demo", "go", roots, live)[0] == 200)
        set_speed(0, handover=True)
        check("a hand-off counts as moving, as the page's lock does",
              carkit.run_action("demo", "hi", roots, live)[0] == 409)
        set_speed(60)
        check("moving: the car write is refused", carkit.run_action("demo", "w", roots, live)[0] == 409)
        set_speed(0)
        check("an argument is one argv element", carkit.run_action("demo", "say", roots, live, arg="a b; rm -rf ~")[0] == 200)
        check("an action that takes none refuses one", carkit.run_action("demo", "hi", roots, live, arg="x")[0] == 400)
        check("a control character is refused", carkit.run_action("demo", "say", roots, live, arg="a\nb")[0] == 400)
        check("an unknown action is 404", carkit.run_action("demo", "nope", roots, live)[0] == 404)

        print("\n  what a plugin cannot do")
        plugin(roots[0][1], "escape", dict(good, id="escape", data={"command": ["./../demo/car-data"]}))
        d, err = carkit.run_data(carkit.find("escape", roots))
        check(f"a ./ command cannot leave its folder ({err})", d is None and "inside" in (err or ""))
        plugin(roots[0][1], "loud", dict(good, id="loud", data={"command": ["./car-data"]}),
               "print('x' * 70000)")
        check("more than 64 KB of output is refused", "KB" in (carkit.run_data(carkit.find("loud", roots))[1] or ""))
        plugin(roots[0][1], "slow", dict(good, id="slow", data={"command": ["./car-data"], "timeout_s": 0.5}),
               "import time; time.sleep(3)")
        check("a slow data command times out", "longer" in (carkit.run_data(carkit.find("slow", roots))[1] or ""))
        plugin(roots[0][1], "prose", dict(good, id="prose", data={"command": ["./car-data"]}), "print('hello')")
        check("output that is not JSON is an error, not a crash", "JSON" in (carkit.run_data(carkit.find("prose", roots))[1] or ""))
        os.makedirs(os.path.join(roots[0][1], "broken"))
        with open(os.path.join(roots[0][1], "broken", "car.json"), "w") as f: f.write("{nope")
        listed = {p["id"]: p for p in carkit.public_list(roots)["plugins"]}
        check("a broken car.json is listed with its reason, and the others still load",
              listed["broken"]["errors"] and not listed["demo"]["errors"])
        check("the plugin's directory is not exposed to the page", all("dir" not in p for p in listed.values()))

        print("\n  the shipped examples")
        ex = (("omacar", os.path.join(ROOT, "examples", "carkit")),)
        for p in carkit.discover(ex):
            check(f"{p['id']} is valid", not p["errors"])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
