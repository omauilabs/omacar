#!/usr/bin/env python3
"""Incident clips: the minute around a hard stop, a jolt or a tap, kept for good.

    omacar clips status      what the service says now
    omacar clips list        the incidents on disk, newest first
    omacar clips save        ask the running service to save a clip now
    omacar clips run         watch for incidents (omacar-clips.service)
    omacar clips sim         make one Simulated incident from synthetic video

WHAT IT IS, AND WHAT IT IS NOT. It is a second process beside the recorder
(lib/cams.py), and it never touches the recorder, its cameras, its loop or its
lock for longer than a directory listing. The recorder keeps writing
one-minute segments into ~/Videos/OmaCar/<role>/ and its janitor keeps deleting
the oldest. This service only ever (a) reads live.json, (b) reads the Surface's
accelerometer if it has one, and (c) on a trigger makes HARD LINKS to the
segments that cover the incident, into ~/Videos/OmaCar/incidents/<id>/. A hard
link is a second name for the same bytes: the janitor can delete the loop's
name and the incident keeps the file. No copy, no re-encode, no space used
until the janitor lets go of its name. camstore.scan() never looks in
incidents/, so nothing the recorder does can see, count or prune them.

    incidents/<id>/incident.json     what happened (see make_meta)
    incidents/<id>/front.mp4 ...     one file per camera, 30 s before to 30 s
                                     after, joined with `ffmpeg -c copy`
    incidents/<id>/telemetry.json    speed, RPM, throttle, charge and IMA current
                                     for the same minute

If ffmpeg is missing or fails, the hard-linked segments stay as they are
(<role>-<segment>.mp4) and incident.json lists them as parts.

TRIGGERS (all pure, tested in test/clips_test.py):
  brake   deceleration of 0.45 g or more, sustained 0.3 s or more, from 15 km/h
          or more, worked out from the car's own speed (live.json SPEED).
  jolt    the Surface's accelerometer (iio), if it has one: read-only. A
          magnitude 1.5 g away from its slow baseline. Absent: skipped, and
          `omacar clips status` says so.
  manual  the Save clip key (a file-drop, like the fridge's: the server never
          runs this process's code).
One incident per 20 s.

Stdlib only. OMACAR_VIDEOS moves the whole folder, for tests.
"""

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camstore  # noqa: E402

G = 9.80665                # m/s^2
BRAKE_G = 0.45             # deceleration that counts
BRAKE_SPAN = 0.3           # s it has to be sustained for
BRAKE_MIN_KPH = 15.0       # and the speed it starts from
BRAKE_LOOK = 1.2           # s of speed history looked back over
MAX_GAP = 0.75             # s between speed samples before the history is void
JOLT_G = 1.5               # g of change from the baseline
JOLT_TAU = 2.0             # s: the baseline follows the tablet's tilt at this pace
DEBOUNCE = 20.0            # s between incidents
BEFORE = camstore.BEFORE   # 30 s before the trigger
AFTER = camstore.AFTER     # and 30 s after
RING_SPAN = BEFORE + 20    # s of samples kept in memory
KEY_SECS = 2               # the recorder forces a keyframe this often (cams.ffmpeg_args)
FINALIZE_AFTER = 4.0       # s past the window's end: fragmented MP4 is readable as it grows
GIVE_UP_AFTER = 120.0      # s past the window's end before an incident is closed regardless
LIVE_STALE = 15
FREE_FLOOR = 1 << 30       # refuse a new incident below 1 GB free: the loop needs room more

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar"), "clips.json")
STATE = os.path.join(STATE_DIR, "clips.json")
ASK = os.path.join(STATE_DIR, "clips-ask.json")

ID_RE = re.compile(r"^\d{8}-\d{6}-(brake|jolt|manual|sim)$")
MEDIA_RE = re.compile(r"^(front|rear|cabin)(-\d{8}-\d{6})?\.mp4$")
TRIGGER_WORDS = {"brake": "Hard braking", "jolt": "Jolt", "manual": "Saved by hand",
                 "sim": "Simulated"}


def incidents_dir(root=None):
    return os.path.join(root or camstore.videos(), "incidents")


def exports_dir(root=None):
    return os.path.join(root or camstore.videos(), "exports")


# ---- the triggers: pure ---------------------------------------------------------

def decel_g(v0_kph, v1_kph, secs):
    """Deceleration in g between two speeds `secs` apart; positive when slowing."""
    return (v0_kph - v1_kph) / 3.6 / secs / G


class BrakeDetector:
    """Hard braking from the car's speed, a few samples a second.

    OBD speed is whole km/h at about 5 Hz, so one sample can jitter by a
    km/h, and the daemon hands the adapter to other commands now and then, which
    leaves a hole in the stream. Three rules keep that from being read as a
    crash:

      the window is a real window. A deceleration is only measured across 0.3 s
        or more (BRAKE_SPAN), looked at from several start points, and counts
        only if BOTH halves of the window are braking at least half as hard: a
        single bad sample at the end of an otherwise steady speed fails the
        first half, so a glitch never triggers.
      a missing speed (None) or a gap longer than MAX_GAP voids the history.
        The first sample after a hand-off is compared with nothing.
      below BRAKE_MIN_KPH nothing counts: stopping at a junction is not an
        incident.

    feed(t, kph) returns None, or {"peak_g", "speed_before"} once, at the
    sample that proves it. The history is cleared when it fires.
    """

    def __init__(self, g=BRAKE_G, span=BRAKE_SPAN, min_kph=BRAKE_MIN_KPH):
        self.g, self.span, self.min_kph = g, span, min_kph
        self.win = deque()

    def reset(self):
        self.win.clear()

    @staticmethod
    def _at(win, t):
        """The speed at time t, interpolated between the two samples around it."""
        prev = None
        for ts, v in win:
            if ts >= t:
                if prev is None or ts == t:
                    return v
                (t0, v0) = prev
                return v0 + (v - v0) * (t - t0) / (ts - t0)
            prev = (ts, v)
        return prev[1] if prev else None

    def feed(self, t, kph):
        if (kph is None or isinstance(kph, bool) or not isinstance(kph, (int, float))
                or not math.isfinite(kph) or kph < 0 or t is None):
            self.win.clear()
            return None
        if self.win:
            last = self.win[-1][0]
            if t <= last:
                return None
            if t - last > MAX_GAP:
                self.win.clear()
        self.win.append((t, float(kph)))
        while self.win and t - self.win[0][0] > BRAKE_LOOK:
            self.win.popleft()
        best, v_before = 0.0, None
        half_floor = self.g * 0.5
        for ts, v0 in list(self.win):
            span = t - ts
            if span < self.span or v0 < self.min_kph:
                continue
            a = decel_g(v0, kph, span)
            if a < self.g:
                continue
            vm = self._at(self.win, ts + span / 2)
            if vm is None:
                continue
            a1 = decel_g(v0, vm, span / 2)
            a2 = decel_g(vm, kph, span / 2)
            if a1 < half_floor or a2 < half_floor:
                continue
            if a > best:
                best, v_before = a, v0
        if best <= 0:
            return None
        self.win.clear()
        return {"peak_g": round(best, 2), "speed_before": round(v_before, 1)}


class JoltDetector:
    """A knock from the Surface's accelerometer.

    Takes (x, y, z) in m/s^2. The magnitude is the same however the tablet is
    tilted or turned, so the mount's angle does not matter; what is measured
    is how far the magnitude moves from where it has been resting (an
    exponential baseline with JOLT_TAU), which is 0 on a smooth road and a
    spike on a bump or a hit. Returns the excursion in g when it reaches
    `g`, else None. A gap of over a second restarts the baseline.
    """

    def __init__(self, g=JOLT_G, tau=JOLT_TAU):
        self.g, self.tau = g, tau
        self.base = None
        self.last = None

    def feed(self, t, xyz):
        try:
            m = math.sqrt(sum(float(c) ** 2 for c in xyz))
        except (TypeError, ValueError):
            self.base = self.last = None
            return None
        if not math.isfinite(m):
            self.base = self.last = None
            return None
        if self.base is None or self.last is None or t <= self.last or t - self.last > 1.0:
            self.base, self.last = m, t
            return None
        dt = t - self.last
        excursion = abs(m - self.base) / G
        # The baseline follows slowly, and not at all through a spike, so a
        # jolt does not drag it up and hide its own tail.
        if excursion < self.g * 0.5:
            self.base += (m - self.base) * min(1.0, dt / self.tau)
        self.last = t
        if excursion >= self.g:
            return round(excursion, 2)
        return None


class Debounce:
    """One incident per `quiet` seconds."""

    def __init__(self, quiet=DEBOUNCE):
        self.quiet, self.last = quiet, None

    def allow(self, t):
        if self.last is not None and 0 <= t - self.last < self.quiet:
            return False
        self.last = t
        return True


class Ring:
    """The last RING_SPAN seconds of samples, for the 30 s before a trigger."""

    def __init__(self, span=RING_SPAN):
        self.span, self.items = span, deque()

    def add(self, t, sample):
        if self.items and t <= self.items[-1]["t"]:
            return
        row = dict(sample)
        row["t"] = t
        self.items.append(row)
        while self.items and t - self.items[0]["t"] > self.span:
            self.items.popleft()

    def window(self, t0, t1):
        return [dict(r) for r in self.items if t0 <= r["t"] <= t1]


def sample_of(live, now):
    """The telemetry row for one live.json, or None when there is nothing true
    to record (no car, the simulator, a hand-off, or no speed)."""
    if not live or not live.get("connected") or live.get("simulated"):
        return None
    v = live.get("values") or {}
    speed = v.get("SPEED")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed):
        return None
    row = {"speed": round(float(speed), 1)}

    def num(name):
        x = v.get(name)
        return round(float(x), 1) if (isinstance(x, (int, float)) and not isinstance(x, bool)
                                      and math.isfinite(x)) else None
    row["rpm"] = num("RPM")
    row["throttle"] = num("THROTTLE_POS")
    row["soc"] = num("HYBRID_BATTERY_REMAINING")
    amps = None
    for k, x in v.items():
        ku = str(k).upper()
        if "IMA" in ku and ("AMP" in ku or "CURRENT" in ku) and isinstance(x, (int, float)) \
                and not isinstance(x, bool) and math.isfinite(x):
            amps = round(float(x), 1)
            break
    row["ima_amps"] = amps
    return row


# ---- the accelerometer (optional, read-only) ------------------------------------------

def find_accel(sysfs="/sys/bus/iio/devices"):
    """{"dir", "x", "y", "z", "scale", "offset"} for the first iio device with
    in_accel_{x,y,z}_raw, or None. Reads nothing but file names here."""
    for d in sorted(glob.glob(os.path.join(sysfs, "iio:device*"))):
        axes = {a: os.path.join(d, f"in_accel_{a}_raw") for a in "xyz"}
        if not all(os.path.isfile(p) for p in axes.values()):
            continue

        def scale_of(name, default):
            for cand in (f"in_accel_{name}", f"in_accel_x_{name}"):
                p = os.path.join(d, cand)
                try:
                    with open(p, encoding="ascii") as f:
                        return float(f.read().strip())
                except (OSError, ValueError):
                    continue
            return default
        return {"dir": d, **axes, "scale": scale_of("scale", 1.0),
                "offset": scale_of("offset", 0.0)}
    return None


def read_accel(dev):
    """(x, y, z) in m/s^2 from a find_accel() result, or None if unreadable."""
    try:
        out = []
        for a in "xyz":
            with open(dev[a], encoding="ascii") as f:
                raw = float(f.read().strip())
            out.append((raw + dev.get("offset", 0.0)) * dev.get("scale", 1.0))
        return tuple(out)
    except (OSError, ValueError):
        return None


# ---- the files ---------------------------------------------------------------------

def _write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def incident_id(kind, t):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + "-" + kind


def load_config(path=None):
    cfg = {"brake_g": BRAKE_G, "jolt_g": JOLT_G, "jolt": True, "brake": True}
    user = _read_json(path or CONFIG)
    if isinstance(user, dict):
        for k in ("brake_g", "jolt_g"):
            x = user.get(k)
            if isinstance(x, (int, float)) and not isinstance(x, bool) and 0.1 <= x <= 10:
                cfg[k] = float(x)
        for k in ("jolt", "brake"):
            if isinstance(user.get(k), bool):
                cfg[k] = user[k]
    return cfg


def _overlaps(c, t0, t1):
    return c["end"] > t0 and c["start"] < t1


def link_segments(root, inc_dir, t0, t1, have):
    """Hard-link every loop (or locked) segment overlapping [t0, t1] into
    `inc_dir` as <role>-<segment>.mp4, under the recorder's own lock so the
    janitor cannot delete one between the listing and the link. Returns the
    names newly linked. Falls back to a copy when a link is not possible (a
    different filesystem), which costs space but never loses the footage."""
    added = []
    with camstore._lock(root):
        for c in camstore.scan(root):
            if not _overlaps(c, t0, t1):
                continue
            name = f"{c['role']}-{c['file']}"
            if name in have:
                continue
            dest = os.path.join(inc_dir, name)
            try:
                os.link(c["path"], dest)
            except FileExistsError:
                pass
            except OSError:
                try:
                    shutil.copy2(c["path"], dest)
                except OSError:
                    continue
            have.add(name)
            added.append(name)
    return added


def seg_start(name):
    """The epoch second a <role>-<segment>.mp4 began."""
    m = MEDIA_RE.match(name)
    if not m or not m.group(2):
        return None
    return camstore.clip_start(name[len(m.group(1)) + 1:])


def ffprobe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def join_role(inc_dir, role, names, t0, t1, ffmpeg="ffmpeg"):
    """One <role>.mp4 from the role's linked segments, cut to the window with
    `-c copy`: no decode, no encode. Returns {"file", "start", "dur"} or None.

    A copy cut can only start on a keyframe and the recorder forces one every
    KEY_SECS from each segment's start, so the cut lands at most KEY_SECS
    before `t0`; `start` is worked out from that, and is good to a frame or
    two. The end is not a keyframe cut and is exact."""
    if not names:
        return None
    names = sorted(names, key=lambda n: seg_start(n) or 0)
    first = seg_start(names[0])
    if first is None:
        return None
    skip = max(0.0, t0 - first)
    cut = math.floor(skip / KEY_SECS) * KEY_SECS      # the keyframe at or before t0
    start = first + cut
    dur = max(1.0, t1 - start)
    lst = os.path.join(inc_dir, f".{role}.concat")
    out = os.path.join(inc_dir, f"{role}.mp4")
    tmp = os.path.join(inc_dir, f".{role}.tmp.mp4")
    try:
        with open(lst, "w", encoding="utf-8") as f:
            for n in names:
                f.write("file '" + os.path.join(inc_dir, n).replace("'", "'\\''") + "'\n")
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                            "-f", "concat", "-safe", "0", "-i", lst,
                            "-ss", f"{cut:.3f}", "-t", f"{dur:.3f}", "-c", "copy",
                            "-movflags", "+faststart", tmp],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 1024:
            return None
        os.replace(tmp, out)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for p in (lst, tmp):
            try:
                os.remove(p)
            except OSError:
                pass
    real = ffprobe_duration(out)
    return {"file": f"{role}.mp4", "start": round(start, 2),
            "dur": round(real if real else dur, 2)}


def parts_of(inc_dir, role):
    """The fallback: a role's linked segments as playable parts."""
    out = []
    for n in sorted(os.listdir(inc_dir)):
        m = MEDIA_RE.match(n)
        if m and m.group(1) == role and m.group(2):
            out.append({"file": n, "start": seg_start(n), "dur": camstore.CLIP_SECS})
    return out


def make_meta(iid, kind, t, peak_g, speed_before, sim=False):
    return {"version": 1, "id": iid, "trigger": kind, "label": TRIGGER_WORDS[kind],
            "t": t, "t0": t - BEFORE, "t1": t + AFTER, "peak_g": peak_g,
            "speed_before": speed_before, "speed_after": None, "state": "recording",
            "sim": bool(sim), "roles": {}, "also": [], "note": None, "created": time.time(),
            "size": 0}


def dir_size(path):
    total = 0
    for n in os.listdir(path):
        try:
            total += os.path.getsize(os.path.join(path, n))
        except OSError:
            pass
    return total


class Incident:
    """One incident while it is being kept. begin() protects the footage at
    once; step() keeps linking segments as the recorder starts new ones; and
    finish() joins them and writes the telemetry."""

    def __init__(self, root, meta, ring_rows):
        self.root, self.meta = root, meta
        self.dir = os.path.join(incidents_dir(root), meta["id"])
        self.have = set()
        self.next_step = 0.0
        self.rows = list(ring_rows)

    @classmethod
    def begin(cls, root, kind, t, peak_g, speed_before, ring_rows, sim=False):
        meta = make_meta(incident_id(kind, t), kind, t, peak_g, speed_before, sim)
        inc = cls(root, meta, ring_rows)
        os.makedirs(inc.dir, exist_ok=True)
        inc.have = set(os.listdir(inc.dir))
        link_segments(root, inc.dir, meta["t0"], meta["t1"], inc.have)
        _write_json(os.path.join(inc.dir, "incident.json"), meta)
        _write_json(os.path.join(inc.dir, "telemetry.part.json"), {"rows": inc.rows})
        return inc

    @classmethod
    def resume(cls, root, iid):
        """An incident a restart interrupted, or None."""
        d = os.path.join(incidents_dir(root), iid)
        meta = _read_json(os.path.join(d, "incident.json"))
        if not isinstance(meta, dict) or meta.get("state") != "recording":
            return None
        part = _read_json(os.path.join(d, "telemetry.part.json")) or {}
        inc = cls(root, meta, part.get("rows") or [])
        inc.have = set(os.listdir(d))
        return inc

    def add_row(self, row):
        if row["t"] <= self.meta["t1"]:
            self.rows.append(row)

    def step(self, now=None, every=2.0):
        """Link any segment the recorder has started since. Throttled: it
        takes the recorder's lock for a directory listing, which is worth
        doing every couple of seconds and not every tick."""
        if now is not None:
            if now < self.next_step:
                return
            self.next_step = now + every
        link_segments(self.root, self.dir, self.meta["t0"], self.meta["t1"], self.have)

    def due(self, now):
        return now >= self.meta["t1"] + FINALIZE_AFTER

    def finish(self, now, ffmpeg=None):
        """Join, write telemetry, close. Never raises."""
        meta = self.meta
        try:
            self.step()
            if ffmpeg is None:
                ffmpeg = shutil.which("ffmpeg")
            roles, notes = {}, []
            for role in camstore.ROLES:
                names = [n for n in self.have if n.startswith(role + "-")]
                if not names:
                    continue
                joined = join_role(self.dir, role, names, meta["t0"], meta["t1"],
                                   ffmpeg) if ffmpeg else None
                if joined:
                    for n in names:
                        try:
                            os.remove(os.path.join(self.dir, n))
                        except OSError:
                            pass
                    roles[role] = {"parts": [joined], "joined": True}
                else:
                    roles[role] = {"parts": parts_of(self.dir, role), "joined": False}
                    notes.append(f"{role}: kept as one-minute segments"
                                 + ("" if ffmpeg else " (ffmpeg not found)"))
            meta["roles"] = roles
            tel = telemetry_doc(self.rows, meta)
            _write_json(os.path.join(self.dir, "telemetry.json"), tel)
            meta["speed_after"] = tel.get("speed_after")
            meta["spark"] = tel.get("spark")
            if meta["trigger"] == "brake":
                measured = peak_decel(self.rows, meta["t"])
                if measured and measured > (meta.get("peak_g") or 0):
                    meta["peak_g"] = measured
            if meta.get("speed_before") is None:
                meta["speed_before"] = tel.get("speed_before")
            meta["note"] = "; ".join(notes) or (None if roles else "no camera was recording")
            meta["state"] = "ready" if roles else "no-video"
        except Exception as e:                                 # noqa: BLE001 -- keep the footage
            meta["state"] = "partial"
            meta["note"] = f"finishing failed: {type(e).__name__}: {e}"[:200]
        meta["size"] = dir_size(self.dir)
        meta["finished"] = now
        _write_json(os.path.join(self.dir, "incident.json"), meta)
        try:
            os.remove(os.path.join(self.dir, "telemetry.part.json"))
        except OSError:
            pass
        return meta


def _near(rows, t):
    best = None
    for r in rows:
        if r.get("speed") is None:
            continue
        if best is None or abs(r["t"] - t) < abs(best["t"] - t):
            best = r
    return best


def peak_decel(rows, t, before=1.0, after=6.0):
    """The hardest braking in the seconds around the trigger, in g, measured
    over 0.6-1.0 s windows (long enough that whole-km/h rounding is small).
    The trigger fires as soon as the average clears the bar, early in the
    stop; this is the figure the list shows."""
    pts = [(r["t"], r["speed"]) for r in rows
           if r.get("speed") is not None and t - before <= r["t"] <= t + after]
    best = 0.0
    for i, (t0, v0) in enumerate(pts):
        for t1, v1 in pts[i + 1:]:
            span = t1 - t0
            if span > 1.0:
                break
            if span >= 0.6:
                best = max(best, decel_g(v0, v1, span))
    return round(best, 2) if best > 0 else None


def spark_of(rows, t, n=40):
    """n speeds across the window, for the list's little line; None where
    there is no sample within 1.5 s (never a zero for 'unknown')."""
    out = []
    for i in range(n):
        centre = t - BEFORE + (i + 0.5) * (BEFORE + AFTER) / n
        near = _near(rows, centre)
        out.append(near["speed"] if near and abs(near["t"] - centre) <= 1.5 else None)
    return out


def telemetry_doc(rows, meta):
    """telemetry.json: the rows from 30 s before to 30 s after, times as
    seconds from the trigger so the file does not depend on the clock."""
    t = meta["t"]
    rows = [r for r in rows if meta["t0"] <= r["t"] <= meta["t1"]]
    rows.sort(key=lambda r: r["t"])
    out = []
    for r in rows:
        row = {"dt": round(r["t"] - t, 2)}
        for k in ("speed", "rpm", "throttle", "soc", "ima_amps"):
            if r.get(k) is not None:
                row[k] = r[k]
        out.append(row)
    before = _near([r for r in rows if t - 4.0 <= r["t"] <= t - 1.0], t - 1.5)
    after = _near([r for r in rows if r["t"] >= t + 3.0], t + 5.0)
    return {"version": 1, "t": t, "window": [-BEFORE, AFTER], "sim": bool(meta.get("sim")),
            "rows": out, "spark": spark_of(rows, t), "speed_before": before["speed"] if before else None,
            "speed_after": after["speed"] if after else None}


# ---- reading and keeping them ----------------------------------------------------------

def list_incidents(root=None):
    """Summaries, newest first. Never raises on a bad folder."""
    d = incidents_dir(root)
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for iid in names:
        if not ID_RE.match(iid):
            continue
        meta = _read_json(os.path.join(d, iid, "incident.json"))
        if isinstance(meta, dict) and meta.get("id") == iid:
            out.append(meta)
    out.sort(key=lambda m: m.get("t", 0), reverse=True)
    return out


def get_incident(iid, root=None):
    if not ID_RE.match(iid or ""):
        return None
    d = os.path.join(incidents_dir(root), iid)
    meta = _read_json(os.path.join(d, "incident.json"))
    if not isinstance(meta, dict):
        return None
    tel = _read_json(os.path.join(d, "telemetry.json")) or \
        {"rows": [{"dt": round(r["t"] - meta["t"], 2),
                   **{k: r[k] for k in ("speed", "rpm", "throttle", "soc", "ima_amps")
                      if r.get(k) is not None}}
                  for r in (_read_json(os.path.join(d, "telemetry.part.json")) or {}).get("rows", [])],
         "partial": True}
    return {"incident": meta, "telemetry": tel}


def media_path(iid, name, root=None):
    """The file behind /api/clips/media/<id>/<name>, or None. Both parts are
    matched against a pattern that cannot carry a separator or '..'."""
    if not ID_RE.match(iid or "") or not MEDIA_RE.match(name or ""):
        return None
    p = os.path.join(incidents_dir(root), iid, name)
    if os.path.islink(p) or not os.path.isfile(p):
        return None
    return p


def delete_incident(iid, root=None):
    """The one place an incident is ever removed, and only because a person
    asked: nothing automatic calls this."""
    if not ID_RE.match(iid or ""):
        return False
    d = os.path.join(incidents_dir(root), iid)
    if not os.path.isdir(d) or os.path.islink(d):
        return False
    shutil.rmtree(d)
    return True


def export_incident(iid, root=None):
    """Copy an incident to <videos>/exports/<id>/ (a real copy, not links), so
    deleting the incident later keeps the export. Returns the folder."""
    if not ID_RE.match(iid or ""):
        raise ValueError("no such incident")
    src = os.path.join(incidents_dir(root), iid)
    if not os.path.isdir(src):
        raise ValueError("no such incident")
    dest = os.path.join(exports_dir(root), iid)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(exports_dir(root), f"{iid}-{n}")
    os.makedirs(dest)
    for name in sorted(os.listdir(src)):
        if name.startswith(".") or name.endswith(".part.json"):
            continue
        p = os.path.join(src, name)
        if os.path.isfile(p) and not os.path.islink(p):
            shutil.copy2(p, os.path.join(dest, name))
    return dest


def totals(root=None):
    used, n = 0, 0
    d = incidents_dir(root)
    try:
        for iid in os.listdir(d):
            p = os.path.join(d, iid)
            if ID_RE.match(iid) and os.path.isdir(p):
                used += dir_size(p)
                n += 1
    except OSError:
        pass
    return {"count": n, "bytes": used}


# ---- the service's own state ------------------------------------------------------------

def write_state(doc):
    os.makedirs(STATE_DIR, exist_ok=True)
    doc["at"] = time.time()
    _write_json(STATE, doc)


def read_public(now=None):
    """For the API. `running` is false when nothing has written lately."""
    now = time.time() if now is None else now
    st = _read_json(STATE)
    if not isinstance(st, dict):
        return {"present": False, "running": False}
    st["present"] = True
    st["age"] = max(0.0, now - float(st.get("at", 0)))
    st["running"] = st["age"] <= 5
    return st


def ask(action="save"):
    """What the API calls for a tap: a file-drop the service picks up within a
    fraction of a second. Returns False when no service has written lately,
    so the caller can say so instead of waiting for nothing."""
    if action != "save":
        raise ValueError("the one thing to ask is save")
    if not read_public()["running"]:
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    _write_json(ASK, {"action": action, "at": time.time()})
    return True


def take_ask():
    a = _read_json(ASK)
    if a is None:
        return None
    try:
        os.unlink(ASK)
    except OSError:
        pass
    if not isinstance(a, dict) or time.time() - float(a.get("at", 0)) > 30:
        return None
    return a if a.get("action") == "save" else None


# ---- the service ----------------------------------------------------------------------------

class Service:
    """The loop's state, with a clock and a source of samples injected, so a
    test can drive it with a synthetic drive and a fake recorder directory.
    tick() does one pass and never raises."""

    def __init__(self, root=None, cfg=None, accel=None, clock=time.time):
        self.root = root or camstore.videos()
        self.cfg = cfg or load_config()
        self.accel = accel
        self.clock = clock
        self.brake = BrakeDetector(g=self.cfg["brake_g"])
        self.jolt = JoltDetector(g=self.cfg["jolt_g"])
        self.deb = Debounce()
        self.ring = Ring()
        self.active = []
        self.last = None
        self.last_live_t = None
        self.note = None
        self.tot, self.tot_at = None, 0.0
        self.recover()

    def recover(self):
        """Close what a crash or a reboot left open: finish with the footage
        and telemetry there is."""
        try:
            for m in list_incidents(self.root):
                if m.get("state") == "recording":
                    inc = Incident.resume(self.root, m["id"])
                    if inc:
                        inc.finish(self.clock())
        except Exception as e:                                 # noqa: BLE001
            self.note = f"recovery failed: {type(e).__name__}: {e}"[:200]

    def trigger(self, kind, t, peak_g=None, speed_before=None):
        if self.deb.last is not None and 0 <= t - self.deb.last < self.deb.quiet:
            if kind == "manual" and self.active:
                inc = self.active[-1]
                if "manual" not in inc.meta["also"]:
                    inc.meta["also"].append("manual")
                    _write_json(os.path.join(inc.dir, "incident.json"), inc.meta)
            return None
        try:
            free = shutil.disk_usage(self.root if os.path.isdir(self.root) else "/").free
        except OSError:
            free = FREE_FLOOR
        if free < FREE_FLOOR:
            self.note = "disk nearly full: no incident kept"
            return None
        self.deb.allow(t)
        if speed_before is None:
            row = _near([r for r in self.ring.items if t - r["t"] <= 4.0 and r["t"] <= t - 0.5], t)
            speed_before = row["speed"] if row else None
        inc = Incident.begin(self.root, kind, t, peak_g, speed_before,
                             self.ring.window(t - BEFORE, t))
        self.active.append(inc)
        self.last = {"id": inc.meta["id"], "t": t, "trigger": kind}
        self.write()
        return inc

    def feed_live(self, live):
        """One live.json. Returns the incident it started, if any."""
        t = live.get("t") if isinstance(live, dict) else None
        row = sample_of(live, self.clock())
        if row is None or t is None:
            self.brake.reset()
            return None
        if t == self.last_live_t:
            return None
        self.last_live_t = t
        self.ring.add(t, row)
        for inc in self.active:
            inc.add_row(dict(row, t=t))
        if not self.cfg["brake"]:
            return None
        hit = self.brake.feed(t, row["speed"])
        if hit:
            return self.trigger("brake", t, hit["peak_g"], hit["speed_before"])
        return None

    def feed_accel(self, t, xyz):
        if not self.cfg["jolt"]:
            return None
        g = self.jolt.feed(t, xyz)
        if g:
            return self.trigger("jolt", t, g)
        return None

    def tick(self, live=None, xyz=None):
        now = self.clock()
        try:
            if live is not None:
                self.feed_live(live)
            if xyz is not None:
                self.feed_accel(now, xyz)
            a = take_ask()
            if a:
                self.trigger("manual", now)
            for inc in list(self.active):
                if inc.due(now):
                    inc.finish(now)
                    self.active.remove(inc)
                else:
                    inc.step(now)
        except Exception as e:                                 # noqa: BLE001 -- never end the watch
            self.note = f"{type(e).__name__}: {e}"[:200]

    def write(self):
        try:
            free = shutil.disk_usage(self.root if os.path.isdir(self.root) else "/").free
        except OSError:
            free = None
        if self.clock() - self.tot_at > 10 or self.tot is None:
            self.tot, self.tot_at = totals(self.root), self.clock()
        tot = self.tot
        write_state({"pid": os.getpid(), "running": True,
                     "accel": {"present": bool(self.accel),
                               "why": None if self.accel else
                               "no accelerometer found (/sys/bus/iio/devices): jolt trigger off"},
                     "triggers": {"brake": self.cfg["brake"], "jolt": bool(self.accel) and self.cfg["jolt"],
                                  "brake_g": self.cfg["brake_g"], "jolt_g": self.cfg["jolt_g"]},
                     "last": self.last, "active": [i.meta["id"] for i in self.active],
                     "incidents": tot["count"], "bytes": tot["bytes"], "free": free,
                     "note": self.note})


def run():
    import signal
    stop = {"now": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.update(now=True))
    import records
    svc = Service(accel=find_accel())
    svc.write()
    next_live = next_write = 0.0
    while not stop["now"]:
        now = time.time()
        live = None
        if now >= next_live:
            try:
                live = records.live()
            except Exception:                                  # noqa: BLE001
                live = None
            next_live = now + 0.1
        xyz = read_accel(svc.accel) if svc.accel else None
        svc.tick(live=live, xyz=xyz)
        if now >= next_write:
            svc.write()
            next_write = now + 1.0
        time.sleep(0.05)
    try:
        os.remove(STATE)
    except OSError:
        pass
    return 0


# ---- the simulator -----------------------------------------------------------------------------

def _synthetic_video(path, role, secs, ffmpeg):
    """A short test-pattern clip, a different colour per camera. None if it
    cannot be made."""
    size = {"front": "640x360", "rear": "640x360", "cabin": "480x360"}[role]
    hue = {"front": 0, "rear": 120, "cabin": 240}[role]
    for codec in (["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
                  ["-c:v", "mpeg4"]):
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15:duration={secs}",
                            "-vf", f"hue=h={hue}", *codec, "-g", "30", "-movflags", "+faststart",
                            path], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.isfile(path):
            return True
    return False


def sim_profile(t, secs_before=BEFORE, secs_after=AFTER):
    """A believable hard stop: cruising at 62 km/h, then 0.62 g to a halt."""
    rows, v, a_ms2 = [], 62.0, 0.62 * G
    for i in range(int((secs_before + secs_after) * 5) + 1):
        dt = -secs_before + i * 0.2
        if dt >= 0 and v > 0:
            v = max(0.0, v - a_ms2 * 0.2 * 3.6)
        wob = math.sin(i / 7.0) * 0.8 if dt < 0 else 0.0
        speed = round(max(0.0, v + wob if dt < 0 else v), 1)
        rpm = 0.0 if speed == 0 and dt > 3 else 1900 + speed * 9
        rows.append({"t": t + dt, "speed": speed, "rpm": round(rpm, 0),
                     "throttle": 18.0 + 4 * math.sin(i / 5.0) if dt < 0 else 0.0,
                     "soc": round(62.0 + 3.0 * min(1.0, max(0.0, dt) / 8.0), 1),
                     "ima_amps": -45.0 if 0 <= dt < 5 else 6.0})
    return rows


def make_sim(root=None, now=None, ffmpeg=None):
    """One incident labelled Simulated, from synthetic video, without touching
    the recorder or the loop. Returns the meta."""
    root = root or camstore.videos()
    now = time.time() if now is None else now
    t = now - 5
    iid = incident_id("sim", t)
    d = os.path.join(incidents_dir(root), iid)
    os.makedirs(d, exist_ok=True)
    meta = make_meta(iid, "sim", t, 0.62, 62.0, sim=True)
    if ffmpeg is None:
        ffmpeg = shutil.which("ffmpeg")
    roles = {}
    if ffmpeg:
        for role in camstore.ROLES:
            if _synthetic_video(os.path.join(d, f"{role}.mp4"), role, BEFORE + AFTER, ffmpeg):
                dur = ffprobe_duration(os.path.join(d, f"{role}.mp4")) or BEFORE + AFTER
                roles[role] = {"parts": [{"file": f"{role}.mp4", "start": t - BEFORE, "dur": dur}],
                               "joined": True}
    meta["roles"] = roles
    meta["state"] = "ready" if roles else "no-video"
    meta["note"] = "Simulated: test-pattern video" if roles else \
        "Simulated: ffmpeg not found, so there is no video, only the telemetry"
    rows = sim_profile(t)
    tel = telemetry_doc(rows, meta)
    _write_json(os.path.join(d, "telemetry.json"), tel)
    meta["speed_after"] = tel["speed_after"]
    meta["spark"] = tel["spark"]
    meta["size"] = dir_size(d)
    _write_json(os.path.join(d, "incident.json"), meta)
    return meta


# ---- the command ---------------------------------------------------------------------------------

def _fmt(m):
    sa = m.get("speed_after")
    return (f"  {m['id']}  {m.get('label', '?'):<14} "
            f"{('%.2f g' % m['peak_g']) if m.get('peak_g') else '—':<7} "
            f"{m.get('speed_before') if m.get('speed_before') is not None else '—'} -> "
            f"{sa if sa is not None else '—'} km/h  {m.get('state')}"
            + ("  [Simulated]" if m.get("sim") else ""))


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        acc = find_accel()
        print("  accelerometer:", acc["dir"] if acc else "none found: the jolt trigger is off")
        return 0
    if what == "list":
        ms = list_incidents()
        for m in ms:
            print(_fmt(m))
        if not ms:
            print("  no incidents")
        return 0
    if what == "save":
        if ask("save"):
            print("  asked the service to save a clip")
            return 0
        print("  the clip service is not running: omacar clips run", file=sys.stderr)
        return 1
    if what == "run":
        return run()
    if what == "sim":
        m = make_sim()
        print(_fmt(m))
        print("  " + (m.get("note") or ""))
        return 0
    print(f"  omacar clips: no such thing as '{what}'.\n"
          "  status | list | save | run | sim", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
