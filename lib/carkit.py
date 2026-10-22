#!/usr/bin/env python3
"""The car widget kit: Omarchy apps and plugins on the car screen, declared.

doc/design/2026-10-17-car-widget-kit.md is the design. In short: a plugin
ships a `car.json` describing its content as widgets and its actions as argv
lists; OmaCar draws it with its own car widgets, and THIS FILE is where the
driving rules that can be enforced on a server are enforced -- an action
marked `moving: parked` is refused while the car moves, an action marked
`car_write` is refused while the car moves whatever else it says (rule 5, no
override), and nothing runs through a shell.

    omacar carkit list            every car plugin, valid or not, and what it needs
    omacar carkit check <dir>     validate one car.json
    omacar carkit run <id>        run its data command once and print the JSON
    omacar carkit new <name>      scaffold a plugin (car.json + car-data + test)

A PLUGIN IS CODE YOU ARE RUNNING (rule 10). Its data command and actions run
as you. The kit adds robustness, not a sandbox: timeouts, output caps, no
shell, and a declaration it computes itself rather than trusts.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time

ROOTS = (
    ("omacar", os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omacar", "plugins")),
    ("omarchy", os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omarchy", "plugins")),
)
STATE_DIR = os.path.expanduser(os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
LIVE = os.path.join(STATE_DIR, "live.json")
LOG = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar", "carkit.log")

OUT_MAX = 64 * 1024
WIDGETS = {"metric", "gauge", "row", "grid", "stack", "media", "list", "toggle",
           "button", "chart", "text"}
# `image` is not in schema 1: serving a plugin's own files safely needs its own
# route, and a half-built one is worse than none.
CONTAINERS = {"row", "grid", "stack"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MOVING_KPH = 3


# ---- validation (pure) -------------------------------------------------------------

def _walk(node, path, errs, actions):
    if not isinstance(node, dict):
        errs.append(f"{path}: a widget must be an object")
        return
    t = node.get("type")
    if t not in WIDGETS:
        errs.append(f"{path}: unknown widget type {t!r}")
        return
    if t in CONTAINERS:
        kids = node.get("children")
        if not isinstance(kids, list) or not kids:
            errs.append(f"{path}: a {t} needs children")
            return
        for i, k in enumerate(kids):
            _walk(k, f"{path}.children[{i}]", errs, actions)
    for key in ("action", "play", "next", "prev"):
        a = node.get(key)
        if a is not None and a not in actions:
            errs.append(f"{path}: {key} names action {a!r}, which is not declared")
    if t == "list":
        item = node.get("item") or {}
        if item.get("action") is not None and item["action"] not in actions:
            errs.append(f"{path}.item: action {item['action']!r} is not declared")
    if t == "text" and isinstance(node.get("value"), str) and not node["value"].startswith("$") \
            and len(node["value"]) > 280:
        errs.append(f"{path}: text over 280 characters")


def _argv_ok(argv):
    return isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)


def validate(spec):
    """A list of problems; empty means valid. Never raises."""
    errs = []
    if not isinstance(spec, dict):
        return ["car.json must be a JSON object"]
    if spec.get("schema") != 1:
        errs.append("schema must be 1")
    if not isinstance(spec.get("id"), str) or not ID_RE.match(spec["id"]):
        errs.append("id must be lowercase letters, digits and dashes (max 40)")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        errs.append("name is required")
    actions = spec.get("actions") or {}
    if not isinstance(actions, dict):
        errs.append("actions must be an object")
        actions = {}
    for name, a in actions.items():
        if not isinstance(a, dict) or not _argv_ok(a.get("command")):
            errs.append(f"action {name}: command must be a non-empty list of strings (no shell)")
            continue
        if a.get("moving", "parked") not in ("allowed", "parked"):
            errs.append(f"action {name}: moving is 'allowed' or 'parked'")
        if a.get("car_write") and a.get("moving") == "allowed":
            errs.append(f"action {name}: a car_write can never be moving: allowed (rule 5)")
    data = spec.get("data")
    if data is not None:
        if not isinstance(data, dict) or not _argv_ok(data.get("command")):
            errs.append("data.command must be a non-empty list of strings (no shell)")
    if "card" not in spec and "screen" not in spec:
        errs.append("a car plugin needs a card or a screen")
    for surface in ("card", "screen"):
        if surface in spec:
            _walk(spec[surface], surface, errs, actions)
    return errs


def needs(spec):
    """What the plugin needs, computed -- never taken from its own claims alone."""
    actions = spec.get("actions") or {}
    runs = bool(spec.get("data")) or bool(actions)
    declared = spec.get("needs") or {}
    return {
        "runs_code": runs,
        "network": bool(declared.get("network")),
        "car_data": [str(x) for x in (declared.get("car_data") or [])][:20],
        "writes_car": any(a.get("car_write") for a in actions.values() if isinstance(a, dict)),
        "while_moving": sorted(n for n, a in actions.items() if isinstance(a, dict) and a.get("moving") == "allowed"),
    }


# ---- discovery -------------------------------------------------------------------------

def discover(roots=None):
    """Every car plugin found, as {id, kind, dir, name, spec?, errors, needs?}."""
    out, seen = [], set()
    for kind, root in (roots or ROOTS):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for n in names:
            d = os.path.join(root, n)
            f = os.path.join(d, "car.json")
            if not os.path.isfile(f):
                continue
            entry = {"kind": kind, "dir": d, "id": n, "name": n, "errors": []}
            try:
                with open(f, encoding="utf-8") as fh:
                    spec = json.load(fh)
            except (OSError, ValueError) as e:
                entry["errors"] = [f"car.json is not valid JSON: {e}"]
                out.append(entry)
                continue
            entry["errors"] = validate(spec)
            if isinstance(spec, dict):
                entry["id"] = spec.get("id") or n
                entry["name"] = spec.get("name") or n
            if entry["id"] in seen:
                entry["errors"].append(f"another plugin already uses the id {entry['id']!r}")
            seen.add(entry["id"])
            if not entry["errors"]:
                entry["spec"] = spec
                entry["needs"] = needs(spec)
            out.append(entry)
    return out


def find(pid, roots=None):
    for e in discover(roots):
        if e["id"] == pid and not e["errors"]:
            return e
    return None


# ---- running ---------------------------------------------------------------------------

def _resolve(argv, d):
    """argv[0] './x' is the plugin's own file; anything else is a PATH command."""
    a0 = argv[0]
    if a0.startswith("./"):
        real = os.path.realpath(os.path.join(d, a0))
        if not real.startswith(os.path.realpath(d) + os.sep):
            raise ValueError("a ./ command must stay inside the plugin's directory")
        a0 = real
    return [a0] + argv[1:]


def run_data(entry, timeout=None):
    """(data dict, error or None). The command runs with no shell, a timeout and a cap."""
    spec = entry["spec"]
    data = spec.get("data")
    if not data:
        return {}, None
    t = min(10.0, float(data.get("timeout_s", 3)))
    try:
        argv = _resolve(data["command"], entry["dir"])
        r = subprocess.run(argv, cwd=entry["dir"], capture_output=True, timeout=timeout or t)
    except subprocess.TimeoutExpired:
        return None, f"its data command took longer than {t:g} s"
    except (OSError, ValueError) as e:
        return None, f"its data command did not start: {e}"
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return None, f"its data command exited {r.returncode} {tail[0][:120]}".strip()
    if len(r.stdout) > OUT_MAX:
        return None, f"its data command printed more than {OUT_MAX // 1024} KB"
    try:
        obj = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None, "its data command did not print JSON"
    if not isinstance(obj, dict):
        return None, "its data command must print one JSON object"
    return obj, None


_cache = {}                      # id -> {"data", "error", "at"}
_lock = threading.Lock()


def data_for(pid, now=None, roots=None):
    """What the page polls: the last good data, refreshed when older than every_s."""
    now = time.time() if now is None else now
    e = find(pid, roots)
    if not e:
        return None
    every = max(1.0, float((e["spec"].get("data") or {}).get("every_s", 5)))
    with _lock:
        c = _cache.get(pid)
        fresh = c and now - c["at"] < every
    if not fresh:
        d, err = run_data(e)
        with _lock:
            prev = _cache.get(pid) or {}
            _cache[pid] = {"data": d if d is not None else prev.get("data"),
                           "error": err, "at": now,
                           "good_at": now if d is not None else prev.get("good_at")}
    with _lock:
        c = dict(_cache[pid])
    age = None if c.get("good_at") is None else round(now - c["good_at"], 1)
    return {"id": pid, "name": e["name"], "kind": e["kind"], "spec": e["spec"], "needs": e["needs"],
            "data": c.get("data") or {}, "error": c.get("error"), "age": age}


def moving(path=None, now=None):
    """The server's own motion check, the same rule as the page's lockedAsMoving:
    a fresh sample with speed, or a hand-off (adapter lent out), is moving.
    Unknown is NOT moving here only because a car that is off has no sample;
    the page's lock still applies on top."""
    now = time.time() if now is None else now
    try:
        st = os.stat(path or LIVE)
        with open(path or LIVE, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return False
    t = snap.get("t") if isinstance(snap.get("t"), (int, float)) else st.st_mtime
    if now - t > 15:
        return False
    if snap.get("handover") or snap.get("status") == "yielded":
        return True
    sp = (snap.get("values") or {}).get("SPEED")
    return isinstance(sp, (int, float)) and sp > MOVING_KPH


def decide_action(action, is_moving):
    """(allowed, reason). Pure: the whole moving rule in one place, tested."""
    if action.get("car_write") and is_moving:
        return False, "Writes to the car are never made while it is moving"
    if action.get("moving", "parked") != "allowed" and is_moving:
        return False, "Available when you stop"
    return True, None


def clean_arg(arg):
    """A list item's value for an action that declares `"arg": true`: one argv
    element, never a shell word -- at most 100 characters, no control
    characters. None when absent; ValueError when unacceptable."""
    if arg is None:
        return None
    if not isinstance(arg, (str, int, float)) or isinstance(arg, bool):
        raise ValueError("arg must be a string or a number")
    a = str(arg)
    if not a or len(a) > 100 or any(ord(c) < 32 for c in a):
        raise ValueError("arg must be 1 to 100 printable characters")
    # NEVER A FLAG. The value is appended to the plugin's argv, so "--force"
    # or "-rf" would reach its command as an option, not a value (security
    # review, 2026-10-17). A list item's value has no business starting with
    # '-'; `--` is not used instead because not every command honours it.
    if a.startswith("-"):
        raise ValueError("arg must not start with '-'")
    return a


def run_action(pid, name, roots=None, live_path=None, arg=None):
    """(status, payload) for POST /api/carkit/<id>/action/<name>."""
    e = find(pid, roots)
    if not e:
        return 404, {"error": f"no car plugin called {pid!r}"}
    a = (e["spec"].get("actions") or {}).get(name)
    if not a:
        return 404, {"error": f"{pid} has no action {name!r}"}
    try:
        arg = clean_arg(arg)
    except ValueError as e:
        return 400, {"error": str(e)}
    if arg is not None and not a.get("arg"):
        return 400, {"error": f"{name} takes no argument"}
    if arg is None and a.get("arg"):
        return 400, {"error": f"{name} needs an argument"}
    ok, why = decide_action(a, moving(live_path))
    if not ok:
        _log(f"{pid}/{name}: refused -- {why}")
        return 409, {"error": why}
    t = min(15.0, float(a.get("timeout_s", 5)))
    try:
        argv = _resolve(a["command"], e["dir"]) + ([arg] if arg is not None else [])
        r = subprocess.run(argv, cwd=e["dir"], capture_output=True, timeout=t)
    except subprocess.TimeoutExpired:
        _log(f"{pid}/{name}: timed out after {t:g} s")
        return 504, {"error": f"{a.get('label', name)} took longer than {t:g} s"}
    except (OSError, ValueError) as ex:
        _log(f"{pid}/{name}: did not start: {ex}")
        return 500, {"error": f"{a.get('label', name)} did not start: {ex}"}
    _log(f"{pid}/{name}: exit {r.returncode}")
    with _lock:
        _cache.pop(pid, None)            # the next read shows the effect
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return 500, {"error": f"{a.get('label', name)} failed: {tail[0][:160]}"}
    return 200, {"ok": True}


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


def public_list(roots=None):
    """GET /api/carkit: every plugin, its card spec and needs, and any errors."""
    return {"plugins": [{k: v for k, v in e.items() if k != "dir"} for e in discover(roots)]}


# ---- the CLI ---------------------------------------------------------------------------

SCAFFOLD_CAR = {
    "schema": 1, "id": "", "name": "", "icon": "data",
    "needs": {"network": False, "car_data": []},
    "data": {"command": ["./car-data"], "every_s": 5, "timeout_s": 3},
    "card": {"type": "metric", "label": "Example", "value": "$.value", "unit": "", "source": "$.source"},
    "screen": {"type": "stack", "children": [
        {"type": "metric", "label": "Example", "value": "$.value", "source": "$.source"},
        {"type": "button", "label": "Say hello", "action": "hello"}]},
    "actions": {"hello": {"label": "Say hello", "command": ["notify-send", "Hello from the car"], "moving": "parked"}},
}
SCAFFOLD_DATA = '''#!/usr/bin/env python3
"""Print one JSON object: what the car screen shows. Keep it fast (< 3 s)."""
import json, time
print(json.dumps({"value": time.strftime("%H:%M:%S"), "source": "this plugin"}))
'''


def main(argv):
    what = argv[0] if argv else "list"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "list":
        for e in discover():
            mark = "ok " if not e["errors"] else "BAD"
            n = e.get("needs") or {}
            extra = (f"  runs code{', writes the car' if n.get('writes_car') else ''}"
                     f"{', while moving: ' + ','.join(n['while_moving']) if n.get('while_moving') else ''}") if n else ""
            print(f"  {mark} {e['id']:24} {e['kind']:8} {e['name']}{extra}")
            for err in e["errors"]:
                print(f"        {err}")
        return 0
    if what == "check" and len(argv) > 1:
        try:
            with open(os.path.join(argv[1], "car.json"), encoding="utf-8") as f:
                errs = validate(json.load(f))
        except (OSError, ValueError) as e:
            errs = [str(e)]
        print("  valid" if not errs else "\n".join("  " + e for e in errs))
        return 0 if not errs else 1
    if what == "run" and len(argv) > 1:
        e = find(argv[1])
        if not e:
            print(f"  no valid car plugin called {argv[1]!r}", file=sys.stderr)
            return 1
        d, err = run_data(e)
        print(json.dumps(d, indent=2) if d is not None else f"  {err}")
        return 0 if d is not None else 1
    if what == "new" and len(argv) > 1:
        name = argv[1]
        if not ID_RE.match(name):
            print("  a plugin name is lowercase letters, digits and dashes", file=sys.stderr)
            return 2
        d = os.path.join(ROOTS[0][1], name)
        if os.path.exists(d):
            print(f"  {d} already exists", file=sys.stderr)
            return 1
        os.makedirs(d)
        car = dict(SCAFFOLD_CAR, id=name, name=name.replace("-", " ").capitalize())
        with open(os.path.join(d, "car.json"), "w", encoding="utf-8") as f:
            json.dump(car, f, indent=2)
        p = os.path.join(d, "car-data")
        with open(p, "w", encoding="utf-8") as f:
            f.write(SCAFFOLD_DATA)
        os.chmod(p, 0o755)
        print(f"  made {d}: edit car.json and car-data, then `omacar carkit check {d}`")
        return 0
    print("  omacar carkit: list | check <dir> | run <id> | new <name>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
