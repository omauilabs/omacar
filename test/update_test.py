#!/usr/bin/env python3
"""The updater only changes what runs when it is safe, and can always go back.

lib/update.py against REAL git in a scratch directory: a local bare repository
is "origin", a clone of it is the owner's checkout, and nothing touches the
network, the real ~/Projects, a real unit or a real systemctl (its stand-in
writes down every call). The rules (doc/update.md):

  * the conditions are a pure function: parked, no recording, no alert,
    battery >= 30 % or charging; anything unknown counts against;
  * a dirty live checkout is refused, and its files are left exactly as found;
  * a failed gate keeps the old release running and the bad commit is not retried;
  * a passing gate switches one symlink and restarts the units, the kiosk last;
  * a release that does not come up healthy is rolled back by itself, with why;
  * `rollback` is one command; only the last three releases stay on disk;
  * auto mode never fires outside its window, and is off by default.
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

SCRATCH = tempfile.mkdtemp(prefix="omacar-update-test-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
os.environ["OMACAR_VIDEOS"] = os.path.join(SCRATCH, "videos")
os.environ["OMACAR_POWER_SUPPLY_DIR"] = os.path.join(SCRATCH, "power")
os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "t"
os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "t@example.invalid"
SYSLOG = os.path.join(SCRATCH, "systemctl.log")
STUB = os.path.join(SCRATCH, "systemctl-stub")
with open(STUB, "w") as f:
    f.write(f'#!/bin/sh\necho "$@" >> "{SYSLOG}"\nexit 0\n')
os.chmod(STUB, 0o755)
os.environ["OMACAR_UPDATE_SYSTEMCTL"] = STUB

import update  # noqa: E402

fails = 0


def check(msg, cond, detail=""):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg} {detail}")


def head(t):
    print(f"\n  {t}")


def sh(cwd, *cmd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"{cmd}: {r.stderr}")
    return r.stdout.strip()


PARKED = {"moving": False, "answering": False, "charger": False, "live_fresh": True,
          "recording": False, "alerts": [], "battery": 80, "dirty": False}


def ctx(**kw):
    return {**PARKED, **kw}


# ---- pure ------------------------------------------------------------------------

head("the conditions (pure)")
ok, why = update.may_apply(PARKED)
check("a parked car, nothing recording, battery 80 %: may update", ok and not why)
check("moving: no", update.may_apply(ctx(moving=True))[0] is False)
check("the car answering (ignition on), even at 0 km/h: no",
      update.may_apply(ctx(answering=True))[0] is False)
check("charger on and no word from the car: no (cannot confirm parked)",
      update.may_apply(ctx(charger=True, live_fresh=False))[0] is False)
check("charger on but the car's daemon reports it is not answering: yes",
      update.may_apply(ctx(charger=True, live_fresh=True, battery=50))[0] is True)
check("charger unreadable and no live word: no",
      update.may_apply(ctx(charger=None, live_fresh=False))[0] is False)
check("a recording or incident clip being written: no",
      update.may_apply(ctx(recording=True))[0] is False)
for a in ("radar", "drowsy", "child"):
    ok, why = update.may_apply(ctx(alerts=[a]))
    check(f"a {a} alert: no, and it says so", not ok and any(a in w for w in why))
check("battery 29 % on battery: no", update.may_apply(ctx(battery=29))[0] is False)
check("battery 30 % on battery: yes", update.may_apply(ctx(battery=30))[0] is True)
check("battery 10 % but charging: yes",
      update.may_apply(ctx(battery=10, charger=True, live_fresh=True))[0] is True)
check("battery unknown and not charging: no", update.may_apply(ctx(battery=None))[0] is False)
check("a dirty live checkout: no", update.may_apply(ctx(dirty=True))[0] is False)
ok, why = update.may_apply(ctx(moving=True, recording=True, battery=5))
check("every reason is listed, not just the first", len(why) == 3, str(why))

head("the window")
check("02:00-05:00 holds 03:30", update.in_window(3 * 60 + 30, "02:00-05:00"))
check("and not 05:00 itself", not update.in_window(5 * 60, "02:00-05:00"))
check("and not 13:00", not update.in_window(13 * 60, "02:00-05:00"))
check("a window may wrap midnight", update.in_window(23 * 60, "22:00-02:00")
      and update.in_window(60, "22:00-02:00") and not update.in_window(12 * 60, "22:00-02:00"))
check("nonsense is no window at all", not update.in_window(120, "whenever")
      and update.parse_window("25:00-26:00") is None and update.parse_window("03:00-03:00") is None)

head("health (pure)")
good = {"snapshot": True, "root": "/r/new", "kiosk": True, "booted_at": 100.0}
check("snapshot answers, right release, page booted since the switch",
      update.healthy(good, 90.0, "/r/new")[0])
check("no snapshot: not healthy", not update.healthy({**good, "snapshot": False}, 90.0, "/r/new")[0])
check("still the old release: not healthy", not update.healthy({**good, "root": "/r/old"}, 90.0, "/r/new")[0])
check("the page booted BEFORE the switch does not count",
      not update.healthy({**good, "booted_at": 80.0}, 90.0, "/r/new")[0])
check("with no kiosk running, the server alone is enough",
      update.healthy({**good, "kiosk": False, "booted_at": None}, 90.0, "/r/new")[0])

head("the config")
cfgp = os.path.join(SCRATCH, "cfg.json")
check("nothing on disk: branch main, auto OFF, 02:00-05:00",
      (lambda c: c["branch"] == "main" and c["auto"] is False and c["window"] == "02:00-05:00")(
          update.config(cfgp)))
with open(cfgp, "w") as f:
    json.dump({"branch": "stable", "auto": True, "window": "bad", "keep": True}, f)
c = update.config(cfgp)
check("a set branch and auto are honoured; a bad window and a bool 'keep' are not",
      c["branch"] == "stable" and c["auto"] is True and c["window"] == "02:00-05:00"
      and c["keep"] == 3, str(c))
os.unlink(cfgp)

# ---- real git --------------------------------------------------------------------

ORIGIN = os.path.join(SCRATCH, "origin.git")
DEV = os.path.join(SCRATCH, "dev")
REPO = os.path.join(SCRATCH, "Projects", "omacar")
env = {"OMACAR_UPDATE_REPO": REPO,
       "OMACAR_UPDATE_RELEASES": os.path.join(SCRATCH, "Projects", ".omacar-releases"),
       "OMACAR_UPDATE_LINK": os.path.join(SCRATCH, "Projects", "omacar-current"),
       "OMACAR_UPDATE_UNITS": os.path.join(SCRATCH, "units"),
       "OMACAR_UPDATE_BIN": os.path.join(SCRATCH, "bin")}
P = update.Paths(env)
CFG = update.config(os.path.join(SCRATCH, "none.json"))

os.makedirs(os.path.dirname(REPO), exist_ok=True)
sh(SCRATCH, "git", "init", "--bare", "-b", "main", ORIGIN)
sh(SCRATCH, "git", "clone", "-q", ORIGIN, DEV)
sh(DEV, "git", "checkout", "-q", "-b", "main")


def commit(name, body="x", msg=None):
    path = os.path.join(DEV, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    sh(DEV, "git", "add", "-A")
    sh(DEV, "git", "commit", "-q", "-m", msg or f"change {name}")
    sh(DEV, "git", "push", "-q", "origin", "main")
    return sh(DEV, "git", "rev-parse", "HEAD")


os.makedirs(os.path.join(DEV, "share", "systemd"), exist_ok=True)
os.makedirs(os.path.join(DEV, "bin"), exist_ok=True)
for n, body in (("omacar-daemon.service", "[Service]\nExecStart=/bin/sh -c '. __ROOT__/lib/env.sh'\n"),
                ("omacar-kiosk.service", "[Service]\nExecStart=%h/.local/bin/omacar kiosk\n"),
                ("omacar-update.service", "[Service]\nExecStart=/usr/bin/python3 __ROOT__/lib/update.py tick\n"),
                ("omacar-update.timer", "[Timer]\nOnBootSec=3min\n"),
                ("omacar-fridge.service", "[Service]\nExecStart=__ROOT__/lib/fridge.py\n")):
    with open(os.path.join(DEV, "share", "systemd", n), "w") as f:
        f.write(body)
with open(os.path.join(DEV, "bin", "omacar"), "w") as f:
    f.write("#!/bin/sh\n")
with open(os.path.join(DEV, ".gitignore"), "w") as f:
    f.write("share/js/vendor/mediapipe/\n__pycache__/\n")
sha1 = commit("lib/app.py", "VALUE = 1\n", "first")
sh(SCRATCH, "git", "clone", "-q", "-b", "main", ORIGIN, REPO)
# What the real checkout has that git does not: the fetched vendor bundle.
os.makedirs(os.path.join(REPO, "share", "js", "vendor", "mediapipe"))
with open(os.path.join(REPO, "share", "js", "vendor", "mediapipe", "vision_bundle.mjs"), "w") as f:
    f.write("bundle")

# Units the owner has installed: the daemon and the kiosk (not the fridge).
os.makedirs(env["OMACAR_UPDATE_UNITS"])
with open(os.path.join(env["OMACAR_UPDATE_UNITS"], "omacar-daemon.service"), "w") as f:
    f.write(f"[Service]\nExecStart=/bin/sh -c '. {REPO}/lib/env.sh'\n")
with open(os.path.join(env["OMACAR_UPDATE_UNITS"], "omacar-kiosk.service"), "w") as f:
    f.write("[Service]\nExecStart=%h/.local/bin/omacar kiosk\n")
os.makedirs(env["OMACAR_UPDATE_BIN"])
os.symlink(os.path.join(REPO, "bin", "omacar"), os.path.join(env["OMACAR_UPDATE_BIN"], "omacar"))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def healthy_probe():
    return {"snapshot": True, "root": os.path.realpath(P.link), "kiosk": False, "booted_at": None}


def old_probe_factory(old_dir):
    # The server never switches: the page says old release.
    return lambda: {"snapshot": True, "root": os.path.realpath(old_dir), "kiosk": False, "booted_at": None}


def gate_ok(d, cfg=None):
    return {"ok": True, "steps": [{"name": "stub", "ok": True, "secs": 0, "note": ""}]}


def gate_bad(d, cfg=None):
    return {"ok": False, "steps": [{"name": "guards_test", "ok": False, "secs": 1,
                                    "note": "FAIL a guard"}]}


def restarts():
    try:
        with open(SYSLOG) as f:
            return [ln.split()[1] for ln in f if ln.startswith("restart")]
    except OSError:
        return []


def link_sha():
    return sh(os.path.realpath(P.link), "git", "rev-parse", "HEAD")


head("not set up")
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx())
check("apply before adopt is refused with the instruction", not r["ok"] and "adopt" in r["reason"], str(r))

head("adopt: run from the symlink")
res = update.adopt(P)
check("adopt succeeds", res["ok"], str(res))
check("the symlink names a worktree at the clone's HEAD", link_sha() == sha1)
check("the ignored vendor bundle came along (git does not carry it)",
      os.path.exists(os.path.join(os.path.realpath(P.link), "share", "js", "vendor", "mediapipe",
                                  "vision_bundle.mjs")))
dtext = open(os.path.join(env["OMACAR_UPDATE_UNITS"], "omacar-daemon.service")).read()
check("an installed unit now runs from the symlink path, not the clone",
      P.link in dtext and (REPO + "/") not in dtext, dtext)
check("a unit the owner never installed is not installed (the fridge)",
      not os.path.exists(os.path.join(env["OMACAR_UPDATE_UNITS"], "omacar-fridge.service")))
check("the update timer is installed",
      os.path.exists(os.path.join(env["OMACAR_UPDATE_UNITS"], "omacar-update.timer")))
check("~/.local/bin/omacar now points through the symlink",
      os.readlink(os.path.join(env["OMACAR_UPDATE_BIN"], "omacar")) == os.path.join(P.link, "bin", "omacar"))
check("the clone's own checkout is untouched (still clean on main)",
      not update.is_dirty(REPO) and sh(REPO, "git", "rev-parse", "HEAD") == sha1)
check("adopting twice does nothing", update.adopt(P)["reason"] == "already set up")

old_dir = os.path.realpath(P.link)

head("check: what is waiting")
sh(DEV, "git", "checkout", "-q", "-b", "feat")
fsha = commit("lib/feat.py", "Y = 2\n", "feature commit")
sh(DEV, "git", "checkout", "-q", "main")
sh(DEV, "git", "merge", "-q", "--no-ff", "feat", "-m", "Merge pull request #7 from o/feat",
   "-m", "Add the thing the owner wanted")
sh(DEV, "git", "push", "-q", "origin", "main")
sha2 = sh(DEV, "git", "rev-parse", "HEAD")
st = update.check(P, CFG)
av = st["available"]
check("a newer commit on origin/main is found", av and av["sha"] == sha2, str(av))
check("with the PR title from the merge body, not 'Merge pull request'",
      av["changes"] == ["Add the thing the owner wanted"], str(av and av["changes"]))
check("and the clone's working branch did not move (fetch only)",
      sh(REPO, "git", "rev-parse", "HEAD") == sha1)
pub = update.read_public(P, CFG)
check("read_public shows current, available and the live verdict",
      pub["current"]["sha"] == sha1 and pub["available"]["sha"] == sha2 and "can_apply" in pub)

head("a dirty live checkout is refused, and left alone")
dirty_file = os.path.join(REPO, "lib", "app.py")
with open(dirty_file, "w") as f:
    f.write("VALUE = 'owner is editing'\n")
untracked = os.path.join(REPO, "notes.txt")
with open(untracked, "w") as f:
    f.write("mine")
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(),
                 probe=healthy_probe, restart=lambda p: [])
check("apply refuses and says why", not r["ok"] and "uncommitted" in r["reason"], str(r))
check("the edited file is byte-for-byte what the owner left",
      open(dirty_file).read() == "VALUE = 'owner is editing'\n" and open(untracked).read() == "mine")
check("nothing was switched", link_sha() == sha1)
sh(REPO, "git", "checkout", "--", "lib/app.py")
os.unlink(untracked)

head("not parked: nothing is built, nothing switches")
n_before = len(update.releases(P))
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(moving=True),
                 probe=healthy_probe, restart=lambda p: [])
check("refused while moving, with the reason", not r["ok"] and "moving" in r["reason"], str(r))
check("no worktree was even built", len(update.releases(P)) == n_before)
check("and the symlink did not move", link_sha() == sha1)

head("a failed gate keeps the old release")
r = update.apply(P, CFG, gate=gate_bad, ctx_fn=lambda d=False: ctx(),
                 probe=healthy_probe, restart=lambda p: [])
check("apply fails at the gate and names the step", not r["ok"] and "guards_test" in r["reason"], str(r))
check("the old release is still the one running", link_sha() == sha1)
check("the failed release's worktree was removed", len(update.releases(P)) == n_before)
check("the gate output is in the saved result",
      "guards_test" in json.dumps(update.load_state(P)["last"]["gate"]))
check("the bad commit is remembered as rejected",
      sha2 in update.load_state(P)["rejected"])
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(),
                 probe=healthy_probe, restart=lambda p: [])
check("the same commit is not tried again", not r["ok"] and "rejected" in r["reason"], str(r))

head("the car starts while the gate runs")
sha3 = commit("lib/more.py", "Z = 3\n", "after the failed one")
calls = {"n": 0}


def flaky(dirty=False):
    calls["n"] += 1
    return ctx() if calls["n"] == 1 else ctx(moving=True)


r = update.apply(P, CFG, gate=gate_ok, ctx_fn=flaky, probe=healthy_probe, restart=lambda p: [])
check("conditions are checked again just before the switch",
      not r["ok"] and "conditions changed" in r["reason"], str(r))
check("so nothing switched and the worktree was cleaned up",
      link_sha() == sha1 and len(update.releases(P)) == n_before)

head("a good update: gate, switch, restart, healthy")
clock = Clock()
open(SYSLOG, "w").close()
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(), probe=healthy_probe,
                 sleep=clock.sleep, clock=clock)
check("it reports success", r["ok"] and r["reason"] == "updated and healthy", str(r))
check("the symlink now names the new commit", link_sha() == sha3)
check("the previous release is remembered", update.load_state(P)["previous"] == sha1[:12])
rs = restarts()
check("the units that run from the checkout were restarted, the kiosk LAST",
      rs and rs[-1] == "omacar-kiosk.service" and "omacar-daemon.service" in rs, str(rs))
check("the fridge (not installed) was never touched", "omacar-fridge.service" not in rs)
check("the vendor bundle was carried to the new release too",
      os.path.exists(os.path.join(os.path.realpath(P.link), "share", "js", "vendor", "mediapipe",
                                  "vision_bundle.mjs")))
check("the owner's clone is still on its own commit, clean",
      sh(REPO, "git", "rev-parse", "HEAD") == sha1 and not update.is_dirty(REPO))
check("no pending marker is left", not os.path.exists(P.pending))
check("the history recorded it",
      any(h.get("kind") == "apply" and h.get("ok") for h in update.read_history(P)))
new_dir = os.path.realpath(P.link)

head("one command back")
r = update.rollback(P, CFG, ctx_fn=lambda d=False: ctx(moving=True), restart=lambda p: [])
check("a rollback is not done while moving either", not r["ok"] and "moving" in r["reason"], str(r))
check("and nothing changed", link_sha() == sha3)
clock = Clock()
r = update.rollback(P, CFG, ctx_fn=lambda d=False: ctx(), probe=lambda: {
    "snapshot": True, "root": os.path.realpath(P.link), "kiosk": False, "booted_at": None},
    restart=lambda p: [], sleep=clock.sleep, clock=clock)
check("rollback returns to the previous release", r["ok"] and link_sha() == sha1, str(r))
check("and marks the one it left, so auto mode does not reinstall it",
      sha3 in update.load_state(P)["rejected"])

head("a release that does not come up is undone by itself")
sha4 = commit("lib/four.py", "W = 4\n", "four")
cur_dir = os.path.realpath(P.link)
clock = Clock()
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(),
                 probe=old_probe_factory(cur_dir), sleep=clock.sleep, clock=clock)
check("apply reports a rollback, with the reason",
      not r["ok"] and r["kind"] == "rollback" and "old release" in r["reason"], str(r))
check("the symlink is back on the release that worked", os.path.realpath(P.link) == cur_dir)
check("it waited the configured 90 s before giving up",
      clock.t - 1000.0 >= CFG["health_s"], str(clock.t))
check("the bad commit is recorded as rejected, with why",
      "did not come up" in update.load_state(P)["rejected"][sha4]["reason"])
check("no pending marker left behind", not os.path.exists(P.pending))
snap_down = lambda: {"snapshot": False, "root": None, "kiosk": False, "booted_at": None}
sha5 = commit("lib/five.py", "V = 5\n", "five")
clock = Clock()
r = update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(), probe=snap_down,
                 sleep=clock.sleep, clock=clock)
check("a server that never answers /api/snapshot is rolled back too",
      not r["ok"] and "not answering" in r["reason"] and os.path.realpath(P.link) == cur_dir, str(r))

head("an update that died between the switch and its verdict")
sha6 = commit("lib/six.py", "U = 6\n", "six")
update.fetch(REPO, "main")
d6 = update.add_release(P, sha6, cur_dir)
update.switch_link(P.link, d6)
with open(P.pending, "w") as f:
    json.dump({"from": sha1, "from_dir": cur_dir,
               "to": sha6, "to_dir": d6, "switched_at": 10.0, "deadline": 50.0}, f)
check("before its deadline it is left alone",
      update.resume_pending(P, CFG, restart=lambda p: [], probe=snap_down, clock=lambda: 20.0) is None)
r = update.resume_pending(P, CFG, restart=lambda p: [], probe=snap_down, clock=lambda: 500.0)
check("after it, an unhealthy new release is undone",
      r and not r["ok"] and os.path.realpath(P.link) == cur_dir, str(r))

head("only the last three releases stay")
clock = Clock()
for i in range(5):
    commit(f"lib/gen{i}.py", f"G = {i}\n", f"gen {i}")
    update.apply(P, CFG, gate=gate_ok, ctx_fn=lambda d=False: ctx(), probe=healthy_probe,
                 sleep=clock.sleep, clock=clock, restart=lambda p: [])
rel = update.releases(P)
check("three releases on disk", len(rel) == 3, str([r["short"] for r in rel]))
check("the running one is among them and is the newest",
      os.path.realpath(P.link) == os.path.realpath(rel[0]["dir"]))
check("git forgot the pruned worktrees",
      sh(REPO, "git", "worktree", "list").count("\n") == len(rel))

head("auto mode")
updates = []


def fake_apply(*a, **k):
    updates.append(1)
    return {"ok": True}


real_apply = update.apply
update.apply = fake_apply
commit("lib/auto.py", "A = 1\n", "waiting for auto")
at = lambda h, m: time.mktime((2026, 10, 17, h, m, 0, 0, 0, -1))
update.tick(P, CFG, now=at(3, 0))
check("auto is off by default: a waiting update inside the window is not applied", not updates)
auto = dict(CFG, auto=True)
update.tick(P, auto, now=at(13, 0))
check("auto on, outside the window: not applied", not updates)
update.tick(P, auto, now=at(3, 30))
check("auto on, inside the window: applied", len(updates) == 1)
update.apply = real_apply

head("recording lock")
import camstore  # noqa: E402
os.makedirs(os.environ["OMACAR_VIDEOS"])
check("idle: not busy", update.recording_busy() is False)
with camstore._lock(os.environ["OMACAR_VIDEOS"]):
    # camstore holds its flock in THIS process; a second open file description
    # (what the updater makes) still sees it held.
    check("camstore's lock held: busy", update.recording_busy() is True)
camstore.mark("marked", t=time.time(), root=os.environ["OMACAR_VIDEOS"])
check("an incident clip still pending: busy", update.recording_busy() is True)

head("the gate itself")
bad_rel = os.path.join(SCRATCH, "badrel")
os.makedirs(os.path.join(bad_rel, "lib"))
with open(os.path.join(bad_rel, "lib", "broken.py"), "w") as f:
    f.write("def (:\n")
g = update.default_gate(bad_rel, CFG)
check("a syntax error in lib/ fails the gate before anything runs",
      not g["ok"] and len(g["steps"]) == 1 and "broken.py" in g["steps"][0]["note"], str(g))
good_rel = os.path.join(SCRATCH, "goodrel")
os.makedirs(os.path.join(good_rel, "lib"))
with open(os.path.join(good_rel, "lib", "fine.py"), "w") as f:
    f.write("X = 1\n")
os.makedirs(os.path.join(good_rel, "test"))
with open(os.path.join(good_rel, "test", "guards_test.py"), "w") as f:
    f.write("import sys\nprint('FAIL one')\nsys.exit(1)\n")
g = update.default_gate(good_rel, dict(CFG, gate_skip=["demoserve", "js"]))
check("a failing test file fails the gate, naming its first FAIL line",
      not g["ok"] and "FAIL one" in g["steps"][-1]["note"], str(g))
with open(os.path.join(good_rel, "test", "guards_test.py"), "w") as f:
    f.write("print('every guard holds')\n")
g = update.default_gate(good_rel, dict(CFG, gate_skip=["demoserve", "js"]))
check("and a passing one passes", g["ok"], str(g))

head("the app's door")
try:
    update.request("format-disk", P)
    check("an unknown action is refused", False)
except ValueError:
    check("an unknown action is refused", True)

shutil.rmtree(SCRATCH, ignore_errors=True)
print("\n  a child alert holds an update (lib/childalert.py's state, read directly)")
import json as _j, tempfile as _t
_d = _t.mkdtemp(prefix="omacar-upd-child-")
_saved = update.STATE_DIR
update.STATE_DIR = _d
try:
    import sys as _s
    _s.modules["childalert"] = None          # as on a release without the module
    with open(os.path.join(_d, "child-alert.json"), "w") as _f:
        _j.dump({"pub": {"hold_sleep": True, "source": "live"}}, _f)
    check("an active child alert is an alert", "child" in update.active_alerts(1e9))
    with open(os.path.join(_d, "child-alert.json"), "w") as _f:
        _j.dump({"pub": {"hold_sleep": True, "source": "sim"}}, _f)
    check("a simulated one is not", "child" not in update.active_alerts(1e9))
finally:
    update.STATE_DIR = _saved
    _s.modules.pop("childalert", None)

print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
sys.exit(1 if fails else 0)
