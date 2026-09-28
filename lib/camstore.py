#!/usr/bin/env python3
"""Where the cameras' clips live, which are kept, and what happened.

    ~/Videos/OmaCar/<role>/YYYYmmdd-HHMMSS.mp4        the loop: one-minute clips
    ~/Videos/OmaCar/locked/<event-id>/<role>/<clip>   kept; the loop never deletes these
    ~/Videos/OmaCar/events.json                       every event, oldest first

Shared by the recorder (lib/cams.py), which runs the loop and watches for hard
braking, and by the server (lib/camroutes.py), which marks events when the
driver taps. Both take one file lock, so a clip is never moved by one while the
other is deleting it.

OMACAR_VIDEOS moves the whole folder, for tests and the end-to-end check.
Stdlib only.
"""

import fcntl
import json
import os
import re
import time
from collections import deque
from contextlib import contextmanager

ROLES = ("front", "rear", "cabin")
CLIP_RE = re.compile(r"^(\d{8})-(\d{6})\.mp4$")
CLIP_SECS = 60
BEFORE = 30          # seconds kept before an event
AFTER = 30           # and after it
DEFAULT_PATTERNS = {
    "front": "Osmo Action 4|DJI",
    "rear": "Insta360|Ace",
    "cabin": "C920|Logitech|ELP",
}
DEFAULT_BUDGET_GB = 40      # of the tablet's 93 GB free
# The best mode at or under 1080p30 front and rear; the cabin at low
# resolution. omacar-cameras.json may lower any of them ("caps"): Tuesday's
# fallback, if the tablet cannot carry two 1080p cameras, is a file edit.
DEFAULT_CAPS = {"front": (1920, 1080, 30), "rear": (1920, 1080, 30), "cabin": (640, 480, 30)}


def videos():
    return os.environ.get("OMACAR_VIDEOS") or os.path.expanduser("~/Videos/OmaCar")


def config_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-cameras.json")


def load_config(path=None):
    """The defaults, with ~/.config/omarchy/omacar-cameras.json laid over them:

        {"patterns": {"rear": "Insta360"},    a role's by-id pattern; "" or null
                                              turns the role off
         "caps": {"rear": [1280, 720, 30]},   a role's largest mode
         "budget_gb": 40}

    Anything for a role that does not exist, a cap that is not three positive
    numbers, or a budget that is not a positive number is ignored rather than
    trusted."""
    cfg = {"patterns": dict(DEFAULT_PATTERNS), "budget_gb": DEFAULT_BUDGET_GB,
           "caps": dict(DEFAULT_CAPS)}
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    if not isinstance(user, dict):
        user = {}
    pats = user.get("patterns")
    if isinstance(pats, dict):
        for role, pat in pats.items():
            if role in ROLES and (pat is None or isinstance(pat, str)):
                cfg["patterns"][role] = pat or ""
    caps = user.get("caps")
    if isinstance(caps, dict):
        for role, cap in caps.items():
            if (role in ROLES and isinstance(cap, list) and len(cap) == 3
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
                            for x in cap)):
                cfg["caps"][role] = (int(cap[0]), int(cap[1]), cap[2])
    gb = user.get("budget_gb")
    if isinstance(gb, (int, float)) and not isinstance(gb, bool) and gb > 0:
        cfg["budget_gb"] = gb
    return cfg


def budget_bytes(cfg=None):
    return int((cfg or load_config())["budget_gb"] * 10**9)


# ---- clips -----------------------------------------------------------------

def clip_start(name):
    """The wall-clock second a clip began, from its name. Local time, which is
    what ffmpeg's -strftime writes."""
    m = CLIP_RE.match(name or "")
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))


def clip_name(t):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + ".mp4"


def _ls(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def scan(root=None):
    """Every clip, loop and locked, as {role, file, path, start, end, size,
    locked}, oldest first. A clip ends where the next one of its role starts,
    or a minute after its own start, whichever is sooner."""
    root = root or videos()
    out = []
    for role in ROLES:
        d = os.path.join(root, role)
        for name in _ls(d):
            st = clip_start(name)
            if st is not None:
                p = os.path.join(d, name)
                out.append({"role": role, "file": name, "path": p, "start": st,
                            "size": _size(p), "locked": None})
    ldir = os.path.join(root, "locked")
    for ev in _ls(ldir):
        for role in ROLES:
            d = os.path.join(ldir, ev, role)
            for name in _ls(d):
                st = clip_start(name)
                if st is not None:
                    p = os.path.join(d, name)
                    out.append({"role": role, "file": name, "path": p, "start": st,
                                "size": _size(p), "locked": ev})
    out.sort(key=lambda c: (c["role"], c["start"]))
    for i, c in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        end = c["start"] + CLIP_SECS
        if nxt and nxt["role"] == c["role"]:
            end = min(end, nxt["start"])
        c["end"] = end
    out.sort(key=lambda c: (c["start"], c["role"]))
    return out


def list_clips(role=None, t0=None, t1=None, root=None):
    """What the timeline draws: names and times, never a filesystem path."""
    return [{k: c[k] for k in ("role", "file", "start", "end", "size", "locked")}
            for c in scan(root)
            if (role is None or c["role"] == role)
            and (t0 is None or c["end"] > t0) and (t1 is None or c["start"] < t1)]


def usage(root=None):
    by_role = {r: 0 for r in ROLES}
    count = {r: 0 for r in ROLES}
    for c in scan(root):
        by_role[c["role"]] += c["size"]
        count[c["role"]] += 1
    return {"used": sum(by_role.values()), "by_role": by_role, "clips": count}


def clip_path(role, name, root=None):
    """The file behind /api/cams/clip/<role>/<name>, in the loop or in any
    locked event, or None. The name must look like a clip, so a request can
    never climb out of the folder."""
    if role not in ROLES or not CLIP_RE.match(name or ""):
        return None
    root = root or videos()
    p = os.path.join(root, role, name)
    if os.path.isfile(p):
        return p
    ldir = os.path.join(root, "locked")
    for ev in _ls(ldir):
        p = os.path.join(ldir, ev, role, name)
        if os.path.isfile(p):
            return p
    return None


# ---- the loop ----------------------------------------------------------------

@contextmanager
def _lock(root):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, ".lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def plan_janitor(clips, budget):
    """The loop clips to delete, oldest first, to bring the total under budget.

    Locked clips count against the budget and are never chosen. Neither is
    the newest clip of each role: that is the one ffmpeg is writing."""
    total = sum(c["size"] for c in clips)
    newest = {}
    for c in clips:
        if c["locked"] is None and (c["role"] not in newest
                                    or c["start"] > newest[c["role"]]["start"]):
            newest[c["role"]] = c
    spare = [c for c in clips if c["locked"] is None and c is not newest.get(c["role"])]
    doomed = []
    for c in sorted(spare, key=lambda c: c["start"]):
        if total <= budget:
            break
        doomed.append(c)
        total -= c["size"]
    return doomed


def janitor(budget=None, root=None):
    root = root or videos()
    with _lock(root):
        doomed = plan_janitor(scan(root), budget_bytes() if budget is None else budget)
        for c in doomed:
            try:
                os.remove(c["path"])
            except OSError:
                pass
    return [f"{c['role']}/{c['file']}" for c in doomed]


# ---- hard braking --------------------------------------------------------------

class BrakeWatch:
    """A drop of 16 km/h or more within one second, about 0.45 g, from the
    car's own speed. One event per 30 s: the window it locks is a minute long,
    and a second event inside it would only lock the same clips again."""

    def __init__(self, drop_kph=16.0, within=1.0, quiet=30.0):
        self.drop, self.within, self.quiet = drop_kph, within, quiet
        self.win = deque()
        self.last = None
        self.peak = None

    def feed(self, t, kph):
        if kph is None:
            self.win.clear()
            return False
        self.win.append((t, kph))
        while self.win and t - self.win[0][0] > self.within:
            self.win.popleft()
        top = max(v for _, v in self.win)
        if top - kph >= self.drop and (self.last is None or t - self.last >= self.quiet):
            self.last, self.peak = t, top
            self.win.clear()
            return True
        return False


# ---- events ------------------------------------------------------------------

def _events_file(root):
    return os.path.join(root, "events.json")


def load_events(root=None):
    try:
        with open(_events_file(root or videos()), encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("events"), list):
            return doc
    except (OSError, ValueError):
        pass
    return {"events": []}


def _save_events(root, doc):
    path = _events_file(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)


def list_events(t0=None, t1=None, root=None):
    return [e for e in load_events(root)["events"]
            if (t0 is None or e["t"] >= t0) and (t1 is None or e["t"] <= t1)]


def mark(kind, t=None, speed_kph=None, root=None, now=None):
    """Lock 30 s before to 30 s after `t` (default: now) on every camera.

    Kinds: "hard-braking" (the recorder), "marked" (Mark event) and "saved"
    (Save clip). The clips that exist move now; the ones still to come move
    as settle() finds them."""
    root = root or videos()
    t = time.time() if t is None else float(t)
    eid = time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + "-" + kind
    with _lock(root):
        doc = load_events(root)
        if not any(e["id"] == eid for e in doc["events"]):
            doc["events"].append({"id": eid, "kind": kind, "t": t, "t0": t - BEFORE,
                                  "t1": t + AFTER, "speed_kph": speed_kph,
                                  "state": "pending", "files": []})
            _save_events(root, doc)
    settle(root=root, now=now)
    return next(e for e in load_events(root)["events"] if e["id"] == eid)


def settle(root=None, now=None):
    """Move every loop clip that overlaps a pending event's window into that
    event's folder, and list it there. An event is done once its window has
    closed and the clip covering its last second has started.

    A clip two events overlap stays in the first event's folder, and both
    events list it. Moving the clip ffmpeg is still writing is safe: the
    rename keeps the inode, so ffmpeg finishes the file in its new folder."""
    root = root or videos()
    now = time.time() if now is None else now
    moved = 0
    with _lock(root):
        doc = load_events(root)
        pending = [e for e in doc["events"] if e.get("state") == "pending"]
        if not pending:
            return 0
        clips = scan(root)
        for e in pending:
            for c in clips:
                if c["end"] <= e["t0"] or c["start"] >= e["t1"]:
                    continue
                ref = f"{c['role']}/{c['file']}"
                if c["locked"] is None:
                    dest = os.path.join(root, "locked", e["id"], c["role"])
                    os.makedirs(dest, exist_ok=True)
                    new = os.path.join(dest, c["file"])
                    os.replace(c["path"], new)
                    c["path"], c["locked"] = new, e["id"]
                    moved += 1
                if ref not in e["files"]:
                    e["files"].append(ref)
            if now >= e["t1"] + 5:
                e["state"] = "locked"
        _save_events(root, doc)
    return moved


# ---- HTTP Range --------------------------------------------------------------

def parse_range(header, size):
    """(start, end), inclusive, for a single `bytes=` range; None to send the
    whole file; or "unsatisfiable" for a range that starts past the end.

    One range only: Chromium's <video> never asks for more, and a multi-range
    request gets the whole file, which is a correct answer to it."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if not m or (not m.group(1) and not m.group(2)):
        return None
    a, b = m.group(1), m.group(2)
    if not a:
        n = int(b)
        if n == 0 or not size:
            return "unsatisfiable"
        return max(0, size - n), size - 1
    start = int(a)
    if start >= size:
        return "unsatisfiable"
    end = min(int(b), size - 1) if b else size - 1
    if end < start:
        return None
    return start, end
