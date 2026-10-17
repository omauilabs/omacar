#!/usr/bin/env python3
"""OmaCar updates itself, safely: a new release is built beside the old one,
proven, switched to atomically, watched, and undone if it does not come up.

WHAT THE OWNER ASKED FOR (2026-10-17). Updates were manual SSH. They should
happen on the tablet's own say-so -- but never while the car is being driven,
never over a recording, and never in a way that can leave the dashboard dead.

    omacar update status       what runs, what is waiting, what stops it now
    omacar update check        fetch the branch, say what is new (changes nothing)
    omacar update apply        build, test, switch, watch -- only when parked
    omacar update rollback     back to the previous release (one command)
    omacar update history      every check, switch and rollback
    omacar update adopt        one-time: run from a release symlink (doc/update.md)

THE LAYOUT.
    ~/Projects/omacar                the owner's clone. Never reset, never
                                     written to. Only `git fetch` touches it
                                     (refs/remotes), and `git worktree add`.
    ~/Projects/.omacar-releases/<sha> one git worktree per release
    ~/Projects/omacar-current        a SYMLINK to the release that runs

The user units and ~/.local/bin/omacar point at the symlink path, so a switch
is one atomic rename and a restart -- no unit file is rewritten, nothing
dangles, and a rollback is the same rename the other way (doc/update.md).

WHEN IT MAY APPLY (may_apply, pure): parked, no recording or incident clip
being written, no radar/drowsy/child alert, battery >= 30 % or charging, the
live checkout clean. Anything it cannot read counts against updating.
"""

import ast
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")

DEFAULTS = {
    "branch": "main",
    "auto": False,              # never, unless the owner turns it on
    "window": "02:00-05:00",    # auto mode applies only inside this
    "keep": 3,                  # releases kept on disk
    "health_s": 90,             # the new release must be healthy by then
    "min_battery": 30,
    "gate_skip": [],            # gate steps to leave out: "guards", "demoserve", "js"
}

HEALTH_POLL = 2.0
MOVING_WINDOW = 120             # s: speed this recently means moving
ANSWERING_WINDOW = 30           # s: a live sample this recent means ignition on
FRESH_LIVE = 60                 # s: how old "word from the car" may be
ALERT_RECENT = 120              # s: a drowsy alert this recent is still active
CHILD_AWAKE = ("awake", "crying", "fussing", "stirring")


# ---- where things are --------------------------------------------------------

class Paths:
    """Every location, overridable by environment so tests never meet the real ones."""

    def __init__(self, env=None):
        e = os.environ if env is None else env
        home = os.path.expanduser("~")
        self.repo = os.path.expanduser(e.get("OMACAR_UPDATE_REPO", home + "/Projects/omacar"))
        self.releases = os.path.expanduser(
            e.get("OMACAR_UPDATE_RELEASES", home + "/Projects/.omacar-releases"))
        self.link = os.path.expanduser(
            e.get("OMACAR_UPDATE_LINK", home + "/Projects/omacar-current"))
        self.state = os.path.join(STATE_DIR, "update.json")
        self.history = os.path.join(STATE_DIR, "update-history.jsonl")
        self.pending = os.path.join(STATE_DIR, "update-pending.json")
        self.booted = os.path.join(STATE_DIR, "update-booted.json")
        self.lock = os.path.join(STATE_DIR, "update.lock")
        self.config = os.path.join(CONFIG_DIR, "update.json")
        self.units = os.path.expanduser(
            e.get("OMACAR_UPDATE_UNITS", "~/.config/systemd/user"))
        self.bin = os.path.expanduser(e.get("OMACAR_UPDATE_BIN", "~/.local/bin"))
        self.ports = (e.get("OMACAR_UPDATE_PORTS") or "7560 7561 7562 7563 7564").split()


def config(path=None):
    cfg = dict(DEFAULTS)
    try:
        with open(path or Paths().config, encoding="utf-8") as f:
            mine = json.load(f)
        if isinstance(mine, dict):
            for k, dv in DEFAULTS.items():
                v = mine.get(k)
                if k not in mine or not isinstance(v, type(dv)):
                    continue
                if isinstance(v, bool) != isinstance(dv, bool):
                    continue                    # True is not a number of releases
                cfg[k] = v
    except (OSError, ValueError):
        pass
    if parse_window(cfg["window"]) is None:
        cfg["window"] = DEFAULTS["window"]
    cfg["keep"] = max(2, min(10, cfg["keep"]))
    cfg["health_s"] = max(10, min(600, cfg["health_s"]))
    cfg["min_battery"] = max(5, min(95, cfg["min_battery"]))
    if not str(cfg["branch"]).strip():
        cfg["branch"] = DEFAULTS["branch"]
    return cfg


def say(msg):
    print(msg, flush=True)


# ---- the conditions (pure) ---------------------------------------------------

def parse_window(text):
    """"02:00-05:00" -> (start_min, end_min), or None if it is not that."""
    try:
        a, b = str(text).split("-")
        def m(s):
            h, mi = s.strip().split(":")
            h, mi = int(h), int(mi)
            if not (0 <= h < 24 and 0 <= mi < 60):
                raise ValueError
            return h * 60 + mi
        s, e = m(a), m(b)
    except (ValueError, AttributeError):
        return None
    return None if s == e else (s, e)


def in_window(minute_of_day, window):
    """Is this minute inside the window? It may wrap midnight (22:00-02:00)."""
    w = parse_window(window)
    if w is None:
        return False
    s, e = w
    return s <= minute_of_day < e if s < e else (minute_of_day >= s or minute_of_day < e)


def may_apply(ctx, min_battery=30):
    """(ok, [reasons]) from what is known. Pure; ctx is gather_context()'s shape:

      moving       True if the car is, or was within two minutes, moving
      answering    True if the car's computers answered in the last 30 s (ignition on)
      charger      True / False / None (unreadable)
      live_fresh   True if there is a recent word from the car's daemon
      recording    True if a recording or incident clip is being written
      alerts       names of alerts active now: "radar", "drowsy", "child"
      battery      percent or None
      dirty        True if the owner's checkout has uncommitted changes (apply only)

    Every unknown counts against updating, the way the ignition loop's do."""
    why = []
    if ctx.get("moving"):
        why.append("the car is moving")
    elif ctx.get("answering"):
        why.append("the car is on (its computers are answering)")
    elif ctx.get("charger") is not False and not ctx.get("live_fresh"):
        why.append("cannot confirm the car is parked (charger on, no word from the car)")
    if ctx.get("recording"):
        why.append("a recording or camera incident is being written")
    for a in ctx.get("alerts") or []:
        why.append(f"a {a} alert is active")
    bat = ctx.get("battery")
    if ctx.get("charger") is not True:
        if bat is None:
            why.append("the battery level is unknown")
        elif bat < min_battery:
            why.append(f"battery {bat}% is under {min_battery}% and not charging")
    if ctx.get("dirty"):
        why.append("the live checkout has uncommitted changes")
    return (not why), why


def healthy(probe, switched_at, expect_root=None):
    """(ok, why) from one probe of the running app. Pure.

    probe: {"snapshot": bool, "root": str|None, "kiosk": bool, "booted_at": float|None}
    The server must answer /api/snapshot (and, when asked, from the new
    release); and if a kiosk is running its page must have reported boot
    complete since the switch."""
    if not probe.get("snapshot"):
        return False, "the server is not answering /api/snapshot"
    if expect_root and probe.get("root") not in (None, expect_root):
        return False, "the server is still running the old release"
    if expect_root and probe.get("root") is None:
        return False, "the server does not report which release it runs"
    if probe.get("kiosk") and not ((probe.get("booted_at") or 0) >= switched_at):
        return False, "the kiosk page has not reported boot complete"
    return True, "healthy"


# ---- git ---------------------------------------------------------------------

def git(repo, *args, timeout=60, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def is_dirty(repo):
    """Anything a human could lose: modified, staged or untracked (not ignored)."""
    return bool(git(repo, "status", "--porcelain", "--untracked-files=normal"))


def describe(repo, sha):
    out = git(repo, "log", "-1", "--format=%H%x1f%cI%x1f%s", sha)
    full, date, subject = out.split("\x1f", 2)
    return {"sha": full, "short": full[:12], "date": date, "subject": subject}


def changes_between(repo, old, new, limit=12):
    """Titles of what landed from old to new: a merge commit's PR title (the
    first line of its body) or a squash commit's subject."""
    if not old or old == new:
        return []
    raw = git(repo, "log", "--first-parent", f"--format=%s%x1f%b%x1e", f"{old}..{new}",
              check=False)
    out = []
    for rec in raw.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        subj, _, body = rec.partition("\x1f")
        title = subj
        if subj.startswith("Merge pull request") or subj.startswith("Merge branch"):
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            title = first or subj
        out.append(title)
    return out[:limit]


def count_between(repo, old, new):
    try:
        return int(git(repo, "rev-list", "--first-parent", "--count", f"{old}..{new}"))
    except (RuntimeError, ValueError):
        return 0


def fetch(repo, branch):
    """Only refs/remotes/origin/<branch> changes. The working tree and HEAD are untouched."""
    git(repo, "fetch", "--quiet", "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=90)
    return git(repo, "rev-parse", f"refs/remotes/origin/{branch}")


# ---- releases ----------------------------------------------------------------

def release_dir(P, sha):
    return os.path.join(P.releases, sha[:12])


def current(P):
    """{"dir", "sha"} of the release the symlink names, or None if not adopted."""
    if not os.path.islink(P.link):
        return None
    d = os.path.realpath(P.link)
    if not os.path.isdir(d):
        return None
    try:
        return {"dir": d, "sha": git(d, "rev-parse", "HEAD")}
    except (RuntimeError, OSError):
        return None


def releases(P):
    """Release directories on disk, newest-switched-to first."""
    try:
        names = [n for n in os.listdir(P.releases)
                 if os.path.isdir(os.path.join(P.releases, n))]
    except OSError:
        return []
    out = []
    for n in names:
        d = os.path.join(P.releases, n)
        try:
            out.append({"dir": d, "short": n, "at": os.stat(d).st_mtime})
        except OSError:
            pass
    return sorted(out, key=lambda r: -r["at"])


def _ignored_paths(src):
    """Top-level git-ignored files/dirs worth carrying into a new release:
    the fetched MediaPipe bundle, the private assets, the models -- things the
    app needs that are, by design, not in git."""
    out = git(src, "ls-files", "--others", "--ignored", "--exclude-standard",
              "--directory", check=False)
    keep = []
    for p in out.splitlines():
        base = p.rstrip("/")
        if "__pycache__" in base or base.endswith((".pyc", ".bak")) or ".bak." in base:
            continue
        if base.endswith(".log") or base.startswith(("launch", "doc/film", "doc/design/mockups")):
            continue
        keep.append(base)
    return keep


def _carry(src, dst, rel):
    s, d = os.path.join(src, rel), os.path.join(dst, rel)
    if not os.path.lexists(s) or os.path.lexists(d):
        return
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if os.path.isdir(s) and not os.path.islink(s):
        shutil.copytree(s, d, symlinks=True,
                        copy_function=lambda a, b, **kw: _link_or_copy(a, b))
    else:
        _link_or_copy(s, d)


def _link_or_copy(a, b):
    try:
        os.link(a, b)
    except OSError:
        shutil.copy2(a, b, follow_symlinks=False)


def add_release(P, sha, carry_from):
    """A worktree at sha under P.releases, with the ignored runtime files carried in."""
    d = release_dir(P, sha)
    if os.path.exists(d):
        remove_release(P, d)
    os.makedirs(P.releases, exist_ok=True)
    git(P.repo, "worktree", "add", "--detach", d, sha, timeout=120)
    if carry_from and os.path.isdir(carry_from):
        for rel in _ignored_paths(carry_from):
            try:
                _carry(carry_from, d, rel)
            except OSError as e:
                say(f"  (could not carry {rel}: {e})")
    return d


def remove_release(P, d):
    r = subprocess.run(["git", "-C", P.repo, "worktree", "remove", "--force", d],
                       capture_output=True, text=True)
    if r.returncode != 0 or os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)
    subprocess.run(["git", "-C", P.repo, "worktree", "prune"], capture_output=True)


def prune(P, keep, protect):
    """Keep the newest `keep` releases and anything in `protect` (current, previous)."""
    prot = {os.path.realpath(p) for p in protect if p}
    rel = releases(P)
    kept = 0
    for r in rel:
        if os.path.realpath(r["dir"]) in prot or kept < keep:
            kept += 1
            continue
        remove_release(P, r["dir"])


def switch_link(link, target):
    """Atomic: a new symlink beside the old, then one rename over it."""
    tmp = f"{link}.new.{os.getpid()}"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, link)
    try:
        os.utime(target)          # releases() orders by when they last ran
    except OSError:
        pass


# ---- the gate ----------------------------------------------------------------

def find_chromium():
    exe = os.environ.get("OMACAR_CHROME")
    if exe and os.path.exists(exe):
        return exe
    for n in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(n)
        if p:
            return p
    return None


def _syntax_step(d):
    t0 = time.time()
    bad = []
    n = 0
    lib = os.path.join(d, "lib")
    for name in sorted(os.listdir(lib)):
        if not name.endswith(".py"):
            continue
        n += 1
        try:
            with open(os.path.join(lib, name), "rb") as f:
                ast.parse(f.read(), name)
        except (SyntaxError, ValueError) as e:
            bad.append(f"{name}: {e}")
    return {"name": "python syntax of lib/", "ok": not bad, "secs": round(time.time() - t0, 1),
            "note": (f"{n} files parse" if not bad else "; ".join(bad)[:300])}


def _cmd_step(name, cmd, d, timeout, scratch):
    t0 = time.time()
    env = dict(os.environ)
    # Scratch state: a gate must never write into the car's own state.
    for k, sub in (("XDG_STATE_HOME", "state"), ("XDG_CONFIG_HOME", "config"),
                   ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        env[k] = os.path.join(scratch, sub)
    env["OMACAR_PORTS"] = "1"
    try:
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout, env=env)
        ok = r.returncode == 0
        tail = [ln for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
        note = tail[-1].strip() if tail else ""
        if not ok:
            fails = [ln.strip() for ln in tail if "FAIL" in ln]
            note = (fails[0] if fails else note)[:240]
    except subprocess.TimeoutExpired:
        ok, note = False, f"timed out after {timeout} s"
    except OSError as e:
        ok, note = False, str(e)
    return {"name": name, "ok": ok, "secs": round(time.time() - t0, 1), "note": note[:300]}


def default_gate(d, cfg=None):
    """The fast subset: syntax, guards, the demo server's route table, and the
    JS units where a Chromium exists. Stops at the first failure."""
    cfg = cfg or DEFAULTS
    skip = set(cfg.get("gate_skip") or [])
    py = sys.executable or "python3"
    steps = [_syntax_step(d)]
    ok = steps[0]["ok"]
    if not ok:
        return {"ok": False, "steps": steps}
    scratch = tempfile.mkdtemp(prefix="omacar-gate-")
    try:
        plan = []
        if "guards" not in skip:
            plan.append(("guards_test", [py, "test/guards_test.py"], 180))
        if "demoserve" not in skip:
            plan.append(("demoserve_test", [py, "test/demoserve_test.py"], 240))
        if "js" not in skip:
            if find_chromium():
                plan.append(("JS units", [py, "test/js_test.py"], 300))
            else:
                steps.append({"name": "JS units", "ok": True, "secs": 0,
                              "note": "skipped: no chromium here"})
        for name, cmd, to in plan:
            if not ok:
                break
            if not os.path.exists(os.path.join(d, cmd[1])):
                steps.append({"name": name, "ok": True, "secs": 0, "note": "skipped: not in this release"})
                continue
            s = _cmd_step(name, cmd, d, to, scratch)
            steps.append(s)
            ok = s["ok"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"ok": ok and all(s["ok"] for s in steps), "steps": steps}


def gate_summary(g):
    return "; ".join(f"{s['name']}: {'ok' if s['ok'] else 'FAILED'}"
                     + (f" ({s['note']})" if s.get("note") and not s["ok"] else "")
                     for s in g["steps"])


# ---- reading the world ---------------------------------------------------------

def gather_context(P=None, now=None, repo_dirty=False):
    """The inputs of may_apply, read from the files the other services write."""
    now = time.time() if now is None else now
    ctx = {"moving": False, "answering": False, "charger": None, "live_fresh": False,
           "recording": False, "alerts": [], "battery": None, "dirty": repo_dirty}
    try:
        import ignition
        pw = ignition.supplies()
        ctx["charger"], ctx["battery"] = pw["powered"], pw["battery"]
        car = ignition.car(now=now)
        age = car.get("age")
        ctx["live_fresh"] = age is not None and age < FRESH_LIVE
        ctx["answering"] = bool(car.get("answering")) and age is not None and age < ANSWERING_WINDOW
        if (car.get("speed") or 0) > 0 and age is not None and age < MOVING_WINDOW:
            ctx["moving"] = True
        pub = ignition.read_public()
        if pub.get("running") and pub.get("held") == "moving":
            ctx["moving"] = True
    except Exception:
        ctx["live_fresh"], ctx["answering"] = False, False
    ctx["recording"] = recording_busy()
    ctx["alerts"] = active_alerts(now)
    return ctx


def recording_busy():
    """The camera store's lock is held, or an incident clip is still pending."""
    try:
        import camstore
        root = camstore.videos()
    except Exception:
        return False
    lockf = os.path.join(root, ".lock")
    if os.path.exists(lockf):
        try:
            with open(lockf, "a+") as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    return True
        except OSError:
            pass
    try:
        return any(e.get("state") == "pending" for e in camstore.load_events(root)["events"])
    except Exception:
        return False


def active_alerts(now):
    out = []
    try:
        import v1
        if (v1.read_public() or {}).get("alerts"):
            out.append("radar")
    except Exception:
        pass
    try:
        import nursery
        s = nursery.read() or {}
        if str(s.get("state") or "").lower() in CHILD_AWAKE:
            sent = float(s.get("fetched") or 0)
            if sent and now - sent < nursery.STALE:
                out.append("child")
    except Exception:
        pass
    try:
        import records
        db = records.connect()
        recs = records.records(db, "drowsy", 1)
        if recs and now - float(recs[0].get("at") or 0) < ALERT_RECENT:
            out.append("drowsy")
    except Exception:
        pass
    return out


# ---- state files -------------------------------------------------------------

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def load_state(P):
    st = _read_json(P.state, {})
    st.setdefault("rejected", {})
    return st


def save_state(P, st):
    _write_json(P.state, st)


def log(P, kind, ok, **fields):
    rec = {"at": time.time(), "kind": kind, "ok": ok, **fields}
    os.makedirs(os.path.dirname(P.history), exist_ok=True)
    with open(P.history, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read_history(P, n=20):
    out = []
    try:
        with open(P.history, encoding="utf-8") as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return out[-n:]


class Busy(Exception):
    pass


class run_lock:
    """One update at a time. Held for the whole of apply/rollback."""

    def __init__(self, P):
        self.P = P
        self.f = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.P.lock), exist_ok=True)
        self.f = open(self.P.lock, "a+")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise Busy("another update is running")
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()


def is_running(P):
    """Is an apply/rollback holding the lock right now?"""
    if not os.path.exists(P.lock):
        return False
    try:
        with open(P.lock, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except OSError:
        return False


# ---- units and probes ----------------------------------------------------------

def _sysctl(*args, timeout=30):
    # OMACAR_UPDATE_SYSTEMCTL is for the tests: a stand-in that writes down
    # its calls, so no test can enable or restart a real unit. Empty: none.
    alt = os.environ.get("OMACAR_UPDATE_SYSTEMCTL")
    if alt == "":
        return 1, ""
    cmd = [alt, *args] if alt else ["systemctl", "--user", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def running_units(P):
    """Installed omacar units that run from the release symlink and are active now."""
    out = []
    try:
        names = sorted(os.listdir(P.units))
    except OSError:
        return out
    for n in names:
        if not (n.startswith("omacar-") and n.endswith(".service")):
            continue
        try:
            with open(os.path.join(P.units, n), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        uses_link = P.link in text
        if n == "omacar-kiosk.service":
            uses_link = True            # it runs ~/.local/bin/omacar, which adopt points at the link
        if uses_link and _sysctl("is-active", "--quiet", n)[0] == 0:
            out.append(n)
    return out


def restart_units(P):
    """Restart what runs from the checkout; the kiosk last, so the page comes
    up against a server that is already the new one."""
    units = running_units(P)
    ordered = [u for u in units if u != "omacar-kiosk.service"]
    if "omacar-kiosk.service" in units:
        ordered.append("omacar-kiosk.service")
    for u in ordered:
        _sysctl("restart", u, timeout=60)
    return ordered


def kiosk_active():
    return _sysctl("is-active", "--quiet", "omacar-kiosk.service")[0] == 0


def probe_app(P):
    """One look at the running app. Never raises."""
    out = {"snapshot": False, "root": None, "kiosk": False, "booted_at": None}
    for port in P.ports:
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/api/snapshot", timeout=4) as r:
                if r.status == 200:
                    json.loads(r.read().decode("utf-8", "replace") or "null")
                    out["snapshot"] = True
        except Exception:
            continue
        try:
            with urllib.request.urlopen(base + "/api/update", timeout=4) as r:
                out["root"] = (json.loads(r.read().decode()) or {}).get("root")
        except Exception:
            pass
        break
    out["kiosk"] = kiosk_active()
    out["booted_at"] = _read_json(P.booted, {}).get("at")
    return out


def wait_healthy(P, switched_at, timeout, expect_root=None, probe=None,
                 sleep=time.sleep, clock=time.time):
    probe = probe or (lambda: probe_app(P))
    end = clock() + timeout
    why = "no probe ran"
    while True:
        ok, why = healthy(probe(), switched_at, expect_root)
        if ok:
            return True, why
        if clock() >= end:
            return False, why
        sleep(HEALTH_POLL)


def record_boot(P, root):
    """The page's report that it finished booting (POST /api/update/booted)."""
    _write_json(P.booted, {"at": time.time(), "root": root})
    return {"ok": True}


# ---- status ------------------------------------------------------------------

def check(P, cfg=None, fetcher=fetch):
    """Fetch the branch and record what is waiting. Changes nothing that runs."""
    cfg = cfg or config(P.config)
    st = load_state(P)
    cur = current(P)
    st["branch"] = cfg["branch"]
    st["checked_at"] = time.time()
    try:
        target = fetcher(P.repo, cfg["branch"])
    except Exception as e:
        st["check_error"] = str(e)[:300]
        save_state(P, st)
        log(P, "check", False, reason=st["check_error"])
        return st
    st.pop("check_error", None)
    base = cur["sha"] if cur else git(P.repo, "rev-parse", "HEAD", check=False) or None
    if base and target != base:
        info = describe(P.repo, target)
        info["behind"] = count_between(P.repo, base, target)
        info["changes"] = changes_between(P.repo, base, target)
        st["available"] = info
    else:
        st["available"] = None
    save_state(P, st)
    log(P, "check", True, available=(st["available"] or {}).get("short"))
    return st


def read_public(P=None, cfg=None, now=None):
    """For the API and `status`: the saved state with the live verdict on top."""
    P = P or Paths()
    cfg = cfg or config(P.config)
    now = time.time() if now is None else now
    st = load_state(P)
    cur = current(P)
    out = {"present": True, "adopted": cur is not None, "branch": cfg["branch"],
           "auto": cfg["auto"], "window": cfg["window"],
           "checked_at": st.get("checked_at"), "check_error": st.get("check_error"),
           "available": st.get("available"), "last": st.get("last"),
           "rejected": st.get("rejected") or {}, "running": is_running(P),
           "root": os.path.realpath(ROOT)}
    if cur:
        try:
            out["current"] = describe(cur["dir"], cur["sha"])
        except RuntimeError:
            out["current"] = {"sha": cur["sha"], "short": cur["sha"][:12]}
        out["previous"] = st.get("previous")
    else:
        try:
            out["current"] = describe(P.repo, "HEAD")
        except Exception:
            out["current"] = None
    ok, why = may_apply(gather_context(P, now), cfg["min_battery"])
    out["can_apply"] = {"ok": ok, "reasons": why}
    out["releases"] = [r["short"] for r in releases(P)]
    return out


# ---- apply / rollback ----------------------------------------------------------

def _result(P, st, kind, ok, **fields):
    rec = {"kind": kind, "ok": ok, "at": time.time(), **fields}
    st["last"] = rec
    save_state(P, st)
    log(P, kind, ok, **{k: v for k, v in fields.items() if k != "gate"},
        **({"gate": gate_summary(fields["gate"])} if fields.get("gate") else {}))
    return rec


def apply(P=None, cfg=None, force_sha=None, fetcher=fetch, gate=default_gate,
          restart=restart_units, probe=None, ctx_fn=None, sleep=time.sleep,
          clock=time.time, auto=False):
    """Build, prove, switch, watch. Returns the result record (also saved as
    state["last"]). Never raises for an expected refusal."""
    P = P or Paths()
    cfg = cfg or config(P.config)
    ctx_fn = ctx_fn or (lambda dirty=False: gather_context(P, repo_dirty=dirty))
    try:
        lock = run_lock(P)
        lock.__enter__()
    except Busy as e:
        return {"ok": False, "kind": "apply", "reason": str(e)}
    try:
        st = load_state(P)
        cur = current(P)
        if not cur:
            return _result(P, st, "apply", False,
                           reason="not set up: run `omacar update adopt` once first")
        if is_dirty(P.repo):
            return _result(P, st, "apply", False, **{"from": cur["sha"][:12]},
                           reason="the live checkout has uncommitted changes; "
                                  "commit or stash them (OmaCar never touches them)")
        try:
            target = force_sha or fetcher(P.repo, cfg["branch"])
            target = git(P.repo, "rev-parse", target)
        except Exception as e:
            return _result(P, st, "apply", False, reason=f"could not fetch: {str(e)[:200]}")
        if target == cur["sha"]:
            return _result(P, st, "apply", True, **{"from": cur["sha"][:12]},
                           to=target[:12], reason="already up to date", noop=True)
        rej = st["rejected"].get(target)
        if rej and not force_sha:
            return _result(P, st, "apply", False, to=target[:12],
                           reason=f"this version was rejected before ({rej['reason']}); "
                                  "a newer commit clears it")
        ok, why = may_apply(ctx_fn(False), cfg["min_battery"])
        if not ok:
            return _result(P, st, "apply", False, to=target[:12], waiting=True,
                           reason="not now: " + "; ".join(why))

        say(f"  building {target[:12]} beside {cur['sha'][:12]} ...")
        try:
            d = add_release(P, target, cur["dir"])
        except Exception as e:
            return _result(P, st, "apply", False, to=target[:12],
                           reason=f"could not build the release: {str(e)[:200]}")
        say("  running the gate ...")
        g = gate(d, cfg)
        if not g["ok"]:
            bad = next((s for s in g["steps"] if not s["ok"]), {"name": "?", "note": ""})
            remove_release(P, d)
            st["rejected"][target] = {"reason": f"gate failed at {bad['name']}", "at": time.time()}
            return _result(P, st, "apply", False, **{"from": cur["sha"][:12]}, to=target[:12],
                           gate=g, reason=f"the gate failed at {bad['name']}: {bad.get('note', '')}; "
                                          "the old release is still running")
        # The gate took minutes: the car may have started meanwhile.
        ok, why = may_apply(ctx_fn(False), cfg["min_battery"])
        if not ok:
            remove_release(P, d)
            return _result(P, st, "apply", False, to=target[:12], gate=g, waiting=True,
                           reason="the gate passed but conditions changed: " + "; ".join(why))

        switched_at = clock()
        _write_json(P.pending, {"from": cur["sha"], "from_dir": cur["dir"], "to": target,
                                "to_dir": d, "switched_at": switched_at,
                                "deadline": switched_at + cfg["health_s"] + 30})
        switch_link(P.link, d)
        say(f"  switched to {target[:12]}; restarting ...")
        restarted = restart(P)
        good, hwhy = wait_healthy(P, switched_at, cfg["health_s"],
                                  expect_root=os.path.realpath(d), probe=probe,
                                  sleep=sleep, clock=clock)
        if good:
            st = load_state(P)
            st["previous"] = cur["sha"][:12]
            st["available"] = None
            _drop(P.pending)
            prune(P, cfg["keep"], [d, cur["dir"]])
            return _result(P, st, "apply", True, **{"from": cur["sha"][:12]}, to=target[:12],
                           gate=g, restarted=restarted, auto=auto,
                           reason="updated and healthy")
        # NOT HEALTHY: undo it, and say why.
        say(f"  not healthy ({hwhy}); rolling back ...")
        switch_link(P.link, cur["dir"])
        restart(P)
        _drop(P.pending)
        st = load_state(P)
        st["rejected"][target] = {"reason": f"did not come up: {hwhy}", "at": time.time()}
        return _result(P, st, "rollback", False, **{"from": target[:12]}, to=cur["sha"][:12],
                       gate=g, auto=True,
                       reason=f"rolled back automatically: {hwhy}")
    finally:
        lock.__exit__()


def _drop(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def rollback(P=None, cfg=None, ctx_fn=None, restart=restart_units, probe=None,
             sleep=time.sleep, clock=time.time):
    """One command back to the previous release (same parked conditions)."""
    P = P or Paths()
    cfg = cfg or config(P.config)
    ctx_fn = ctx_fn or (lambda dirty=False: gather_context(P, repo_dirty=dirty))
    try:
        lock = run_lock(P)
        lock.__enter__()
    except Busy as e:
        return {"ok": False, "kind": "rollback", "reason": str(e)}
    try:
        st = load_state(P)
        cur = current(P)
        if not cur:
            return _result(P, st, "rollback", False, reason="not set up: nothing to roll back")
        prev = None
        for r in releases(P):
            if os.path.realpath(r["dir"]) == os.path.realpath(cur["dir"]):
                continue
            if st.get("previous") and not r["short"].startswith(st["previous"][:12]):
                continue
            prev = r
            break
        if not prev:
            prev = next((r for r in releases(P)
                         if os.path.realpath(r["dir"]) != os.path.realpath(cur["dir"])), None)
        if not prev:
            return _result(P, st, "rollback", False, reason="there is no earlier release on disk")
        ok, why = may_apply(ctx_fn(False), cfg["min_battery"])
        if not ok:
            return _result(P, st, "rollback", False, waiting=True,
                           reason="not now: " + "; ".join(why))
        old_sha = git(prev["dir"], "rev-parse", "HEAD")
        switched_at = clock()
        switch_link(P.link, prev["dir"])
        restart(P)
        # An older release may not have /api/update, so the root is not demanded.
        good, hwhy = wait_healthy(P, switched_at, cfg["health_s"], None, probe, sleep, clock)
        if not good:
            switch_link(P.link, cur["dir"])
            restart(P)
            return _result(P, st, "rollback", False, **{"from": cur["sha"][:12]},
                           to=old_sha[:12],
                           reason=f"the earlier release did not come up ({hwhy}); put back")
        st = load_state(P)
        st["previous"] = cur["sha"][:12]
        st["rejected"][cur["sha"]] = {"reason": "rolled back by hand", "at": time.time()}
        return _result(P, st, "rollback", True, **{"from": cur["sha"][:12]}, to=old_sha[:12],
                       reason="rolled back and healthy")
    finally:
        lock.__exit__()


def resume_pending(P=None, cfg=None, restart=restart_units, probe=None, clock=time.time):
    """An apply that died between the switch and its verdict (power cut, the
    process killed with the kiosk it restarted): judge it now, and undo it if
    the new release is not healthy."""
    P = P or Paths()
    cfg = cfg or config(P.config)
    pend = _read_json(P.pending, {})
    if not pend or is_running(P):
        return None
    if clock() < pend.get("deadline", 0):
        return None
    cur = current(P)
    if not cur or os.path.realpath(cur["dir"]) != os.path.realpath(pend.get("to_dir", "")):
        _drop(P.pending)
        return None
    good, why = healthy((probe or (lambda: probe_app(P)))(), pend["switched_at"],
                        os.path.realpath(pend["to_dir"]))
    st = load_state(P)
    if good:
        st["previous"] = pend["from"][:12]
        _drop(P.pending)
        return _result(P, st, "apply", True, **{"from": pend["from"][:12]},
                       to=pend["to"][:12], reason="updated and healthy (verified after a restart)")
    if os.path.isdir(pend["from_dir"]):
        switch_link(P.link, pend["from_dir"])
        restart(P)
    _drop(P.pending)
    st["rejected"][pend["to"]] = {"reason": f"did not come up: {why}", "at": time.time()}
    return _result(P, st, "rollback", False, **{"from": pend["to"][:12]}, to=pend["from"][:12],
                   auto=True, reason=f"rolled back automatically: {why}")


def tick(P=None, cfg=None, now=None, **kw):
    """What the timer runs: finish an interrupted update, look for a new one,
    and -- only if auto is on, inside the window, and everything allows -- apply."""
    P = P or Paths()
    cfg = cfg or config(P.config)
    resume_pending(P, cfg)
    st = check(P, cfg)
    if not (cfg["auto"] and st.get("available")):
        return st
    t = time.localtime(time.time() if now is None else now)
    if not in_window(t.tm_hour * 60 + t.tm_min, cfg["window"]):
        return st
    return apply(P, cfg, auto=True, **kw)


# ---- adopt ---------------------------------------------------------------------

def adopt(P=None):
    """One-time: run from the release symlink instead of the live checkout.

    Builds a release at the clone's committed HEAD, points the symlink at it,
    re-points the units already installed (and ~/.local/bin/omacar) at the
    symlink path, and installs the update timer. The clone itself is not
    modified, and nothing is restarted -- the running app keeps running until
    the next update or reboot."""
    P = P or Paths()
    if current(P):
        return {"ok": True, "reason": "already set up"}
    if is_dirty(P.repo):
        return {"ok": False, "reason": "the live checkout has uncommitted changes; "
                                       "adopting would run without them. Commit or stash first."}
    sha = git(P.repo, "rev-parse", "HEAD")
    d = add_release(P, sha, P.repo)
    switch_link(P.link, d)
    n = 0
    src = os.path.join(d, "share", "systemd")
    os.makedirs(P.units, exist_ok=True)
    for name in sorted(os.listdir(src) if os.path.isdir(src) else []):
        if not name.endswith((".service", ".timer")):
            continue
        dst = os.path.join(P.units, name)
        fresh = name.startswith("omacar-update")
        if not (fresh or os.path.exists(dst)):
            continue                     # never install units the owner had not
        with open(os.path.join(src, name), encoding="utf-8") as f:
            text = f.read().replace("__ROOT__", P.link)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        n += 1
    os.makedirs(P.bin, exist_ok=True)
    bindir = os.path.join(d, "bin")
    for name in (os.listdir(bindir) if os.path.isdir(bindir) else []):
        dst = os.path.join(P.bin, name)
        if os.path.islink(dst) and os.path.realpath(dst).startswith(os.path.realpath(P.repo) + os.sep):
            tmp = dst + ".new"
            os.symlink(os.path.join(P.link, "bin", name), tmp)
            os.replace(tmp, dst)
    _sysctl("daemon-reload")
    _sysctl("enable", "--now", "omacar-update.timer")
    return {"ok": True, "release": sha[:12], "units": n,
            "reason": f"running from {P.link} -> {d}"}


# ---- the app's door ------------------------------------------------------------

def request(action, P=None):
    """POST /api/update. Starts the work detached and returns at once; the
    screen reads the outcome from GET /api/update."""
    P = P or Paths()
    if action not in ("check", "apply", "rollback"):
        raise ValueError(f"{action!r} is not something the app may ask of the updater")
    if is_running(P):
        return 409, {"error": "an update is already running"}
    if action in ("apply", "rollback"):
        cur = current(P)
        if not cur:
            return 409, {"error": "not set up: run `omacar update adopt` once"}
        ok, why = may_apply(gather_context(P, repo_dirty=False), config(P.config)["min_battery"])
        if not ok:
            return 409, {"error": "not now", "reasons": why}
    cmd = [sys.executable or "python3", os.path.abspath(__file__), action]
    # Apply restarts the kiosk, whose cgroup this server may live in: run it
    # in its own transient unit so the restart does not kill the update.
    if shutil.which("systemd-run") and not os.environ.get("OMACAR_UPDATE_NO_SYSTEMD"):
        cmd = ["systemd-run", "--user", "--collect", "--quiet",
               f"--unit=omacar-update-{action}-{int(time.time())}", *cmd]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return 200, {"started": action}


# ---- command line ----------------------------------------------------------------

def show_status():
    pub = read_public()
    cur = pub.get("current") or {}
    say("")
    say(f"  running      {cur.get('short', '?')}  {cur.get('date', '')[:10]}  {cur.get('subject', '')}")
    if not pub["adopted"]:
        say("  not set up   run `omacar update adopt` once (doc/update.md)")
    av = pub.get("available")
    if av:
        say(f"  waiting      {av['short']}  {av['behind']} commit(s) on {pub['branch']}")
        for c in av.get("changes", []):
            say(f"                 - {c}")
    else:
        say(f"  waiting      nothing on {pub['branch']}" + (
            "" if pub.get("checked_at") else "  (never checked)"))
    ca = pub["can_apply"]
    say("  right now    " + ("may update" if ca["ok"] else "will not update: " + "; ".join(ca["reasons"])))
    say(f"  auto         {'on, ' + pub['window'] if pub['auto'] else 'off'}")
    last = pub.get("last")
    if last:
        say(f"  last         {'ok' if last.get('ok') else 'no'}: {last.get('reason', '')}")
    if pub.get("check_error"):
        say(f"  check failed {pub['check_error']}")
    say(f"  releases     {', '.join(pub['releases']) or 'none'}")
    say("")
    return 0


def show_history(n=15):
    for r in read_history(Paths(), n):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("at", 0)))
        bits = [r.get("kind", "?"), "ok" if r.get("ok") else "FAILED"]
        if r.get("from") or r.get("to"):
            bits.append(f"{r.get('from', '')} -> {r.get('to', '')}")
        if r.get("reason"):
            bits.append(r["reason"])
        say(f"  {when}  " + "  ".join(bits))
    return 0


def main(argv):
    cmd = argv[0] if argv else "status"
    P = Paths()
    if cmd == "status":
        return show_status()
    if cmd == "history":
        return show_history()
    if cmd == "check":
        st = check(P)
        if st.get("check_error"):
            say(f"  could not check: {st['check_error']}")
            return 1
        av = st.get("available")
        say(f"  {av['short']} is waiting ({av['behind']} commit(s))" if av else "  up to date")
        for c in (av or {}).get("changes", []):
            say(f"    - {c}")
        return 0
    if cmd in ("apply", "rollback"):
        res = apply(P) if cmd == "apply" else rollback(P)
        say(f"  {'ok' if res.get('ok') else 'no'}: {res.get('reason', '')}")
        for s in (res.get("gate") or {}).get("steps", []):
            say(f"    {'ok  ' if s['ok'] else 'FAIL'} {s['name']}  {s.get('note', '')}")
        return 0 if res.get("ok") else 1
    if cmd == "tick":
        tick(P)
        return 0
    if cmd == "adopt":
        res = adopt(P)
        say(f"  {res['reason']}")
        return 0 if res.get("ok") else 1
    say("usage: omacar update status|check|apply|rollback|history|adopt")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
