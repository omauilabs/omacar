#!/usr/bin/env python3
"""The Surface's own volume, held so every alert keeps its headroom.

Sound reaches the car through an AUX cable from the headphone jack. In the page,
music sits 12 dB below full scale and alerts may use all of it
(share/js/audiobus.js), and that promise means nothing if the machine itself is
at 25% -- which is where the internal speakers were on 2026-09-28. So on the
tablet the default sink is held at 100% and unmuted, on whichever port is
active. With the jack unplugged that port is the speakers: Home says "AUX
disconnected", and alerts still play there at full volume.

    omacar audio status   the port, the volume, and whether it is held
    omacar audio on       hold it at 100% from now on (the tablet)
    omacar audio off      stop holding it
    omacar audio pin      ramp to 100% once, now

Held only where `omacar audio on` was run. The app applies this at start and
every half minute, and a desk machine's speakers at 100% -- or the box's,
every time the test suite opens the app -- are not anybody's idea of help.
wpctl sets the volume; pactl names the port.

pin() NEVER TRUSTS ITS OWN COUNTER (fix round 2). The first ramp (fix round 1)
climbed by advancing a local variable and never checking whether a step had
actually landed: a run of failed `wpctl set-volume` calls, or the volume
changing from outside mid-ramp -- the volume key, or the AUX cable restoring
its own port's level -- left that variable pointing nowhere near the real
sink, and the next SUCCESSFUL call jumped the gap in one step. The same
open-loop shape unmuted without checking the floor call had actually landed,
and hung forever formatting "0.00" if the sink was already at zero (0.0 times
any ratio is still 0.0).

So pin() below re-reads the real sink with `wpctl get-volume` at the top of
EVERY iteration and computes the next step from THAT reading, never from
where it last thought the sink was. A failed set, an outside change, or a
sink parked at exactly 0.00 can only ever cost one small step from wherever
the sink actually is -- never a jump, and never a hang. It also takes an
exclusive, non-blocking lock (flock) so two ramps -- a slow poll and a
reload, say -- can never fight over the same sink; the second one returns
`busy` at once and touches nothing.
"""

import fcntl
import json
import math
import os
import re
import subprocess
import sys
import time

SINK = "@DEFAULT_AUDIO_SINK@"

# wpctl's volume knob is CUBIC: dB = 60*log10(v) (v the number wpctl takes,
# 0..1 and a bit beyond). STEP_DB is deliberately 2.5, not the plan's 3.0
# ceiling -- wpctl only ever prints and (here) sets two decimal places, and
# that rounding can cost a few hundredths of a dB either way; 2.5 leaves
# headroom so a step never reads back over 3.0 even after both ends round.
STEP_DB = 2.5
STEP_SECS = 0.1
# The escape valve below (when rounding to 2 decimals means "the next tick"
# is the only way to move at all) is allowed up to the PLAN's own ceiling,
# not STEP_DB -- see pin()'s "doesn't move the value" branch.
STEP_DB_MAX = 3.0
TARGET_TOL_DB = 0.1
# Quiet enough that unmuting here is not itself an audible jump -- silence
# has no "partway", so this is the one step that cannot be ramped.
FLOOR_DB = -60.0
FLOOR_V = 0.1                    # 10**(FLOOR_DB/60), exact at two decimals
# Anything at or below this reads as "the floor" for the purpose of the dB
# math below, so log10 of an actual zero -- reachable with the volume keys,
# never through this module -- is never computed, and the ramp always has a
# real step to take from wherever it is, however low.
FLOOR_READ_V = 0.001
WPCTL_TIMEOUT = 1                # seconds; short, because pin() retries anyway
MAX_ITERATIONS = 40
MAX_SECS = 8.0


def flag_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-audio.json")


def _lock_path():
    """Where pin()'s single-flight lock file lives: $XDG_RUNTIME_DIR (a
    tmpfs, gone at logout, exactly where a lock file belongs) or, failing
    that, the same state directory the rest of OmaCar uses."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser(os.environ.get("XDG_STATE_HOME", "~/.local/state")), "omacar")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "omacar-audio-pin.lock")


def managed():
    try:
        with open(flag_path(), encoding="utf-8") as f:
            return bool(json.load(f).get("pin"))
    except (OSError, ValueError, AttributeError):
        return False


def set_managed(on):
    os.makedirs(os.path.dirname(flag_path()), exist_ok=True)
    tmp = flag_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pin": bool(on)}, f)
    os.replace(tmp, flag_path())


def parse_volume(text):
    m = re.search(r"Volume:\s*([0-9.]+)", text or "")
    if not m:
        return None, None
    return float(m.group(1)), "[MUTED]" in text


def port_of(sinks, default):
    """(kind, description) of the default sink's active port: "aux" for
    anything headphone-shaped, "speakers" for the built-in speakers, "other"
    or "unknown" otherwise."""
    for s in sinks or []:
        if s.get("name") != default:
            continue
        name = s.get("active_port") or ""
        port = next((p for p in s.get("ports") or [] if p.get("name") == name), {})
        desc = port.get("description") or name
        both = f"{name} {desc}"
        if re.search(r"head", both, re.I):
            return "aux", desc
        if re.search(r"speak", both, re.I):
            return "speakers", desc
        return ("other" if name else "unknown"), desc
    return "unknown", ""


def _run(args, timeout=5):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


# A swappable hook, the same idea as _run: tests replace this with something
# that does not actually wait, so the ramp's timing can be checked without
# audio_test.py spending real wall-clock time on every call to pin().
def _sleep(secs):
    time.sleep(secs)


def _set_volume(v):
    # v is already rounded to 2 decimals by the caller (pin()'s whole point
    # is that the rounding happens BEFORE the number is sent, never after),
    # so this just says exactly that back to wpctl.
    rc, _ = _run(["wpctl", "set-volume", SINK, f"{v:.2f}"], timeout=WPCTL_TIMEOUT)
    return rc == 0


def _db(v):
    """dB on wpctl's cubic scale, with the floor standing in for anything at
    or effectively at zero -- so this never calls log10(0) and a ramp
    starting from "Volume: 0.00" always has a real, finite step to take."""
    return FLOOR_DB if v is None or v <= FLOOR_READ_V else 60.0 * math.log10(v)


def status():
    rc, out = _run(["wpctl", "get-volume", SINK])
    vol, muted = parse_volume(out) if rc == 0 else (None, None)
    _, default = _run(["pactl", "get-default-sink"])
    rc2, js = _run(["pactl", "--format=json", "list", "sinks"])
    try:
        sinks = json.loads(js) if rc2 == 0 and js.strip() else []
    except ValueError:
        sinks = []
    kind, desc = port_of(sinks, (default or "").strip())
    return {"volume": vol, "muted": muted, "port": kind, "port_name": desc,
            "aux": {"aux": True, "speakers": False}.get(kind), "managed": managed(),
            "error": None if vol is not None else "wpctl could not read the volume"}


def _pin_locked(target):
    """The ramp itself, run only once the caller holds the lock. Every
    iteration re-reads the real sink and decides its next move from THAT
    reading -- never from a count of its own past calls -- which is what
    makes a failed set, an outside change or a sink already at 0.00 cost at
    most one small step rather than becoming a later jump or a hang.

    BELOW THE FLOOR, THE 3 dB RULE DOES NOT APPLY (fix round 3): nothing
    down there is audible enough to startle anybody, so a sink the volume
    keys left at, say, 5% or 1% is set straight to FLOOR_V in one step --
    the one large step this function is allowed to take -- and the ordinary
    step-by-step ramp takes over once a read confirms it is there. Round 2's
    version instead tried to ramp UP FROM that low reading directly: at
    those levels a single hundredth-of-a-percent wpctl tick is many dB, so
    its "the next tick is too big, call it close enough" escape valve fired
    immediately and reported `pinned: true` while the sink was still nearly
    silent -- a dishonest status as well as a safety failure. That escape
    valve cannot legitimately fire above the floor at all (a tick is ~2.5 dB
    at v=0.1 and only gets smaller above it), so if it somehow does, this
    now reports a truthful failure instead of a false success."""
    start = time.monotonic()
    iterations = 0
    last_vol = None
    target_db = _db(target)

    while True:
        if iterations >= MAX_ITERATIONS or (time.monotonic() - start) >= MAX_SECS:
            return {"pinned": False, "busy": False, "volume": last_vol,
                    "iterations": iterations,
                    "error": (f"gave up after {iterations} iterations without a "
                              "readable volume" if last_vol is None else
                              f"gave up after {iterations} iterations at "
                              f"{last_vol * 100:.0f}%, short of the target")}
        iterations += 1

        rc, out = _run(["wpctl", "get-volume", SINK], timeout=WPCTL_TIMEOUT)
        vol, muted = parse_volume(out) if rc == 0 else (None, None)
        if vol is None:
            _sleep(STEP_SECS)
            continue
        last_vol = vol

        if muted:
            if vol > FLOOR_V + 1e-9:
                _set_volume(FLOOR_V)
                continue                       # loop: re-read and confirm
            # A fresh read just confirmed muted AND at or below the floor --
            # the only condition under which this unmutes.
            _run(["wpctl", "set-mute", SINK, "0"], timeout=WPCTL_TIMEOUT)
            _sleep(STEP_SECS)
            continue

        if vol < FLOOR_V - 1e-9:
            # Below the floor: not loud enough to ramp, and not loud enough
            # to need to. One step straight to the floor -- see this
            # function's own docstring -- then loop to re-read and confirm
            # it landed before the ordinary ramp below ever runs on it.
            _set_volume(FLOOR_V)
            _sleep(STEP_SECS)
            continue

        cur_db = _db(vol)
        if abs(cur_db - target_db) <= TARGET_TOL_DB:
            return {"pinned": True, "busy": False, "volume": vol,
                    "iterations": iterations, "error": None}

        raw_next = min(target, 10 ** ((cur_db + STEP_DB) / 60.0))
        # Rounded DOWN, so the rounding itself can never push a step over the
        # limit -- only ever hold it back a little.
        v_next = math.floor(raw_next * 100 + 1e-9) / 100.0
        vol2 = round(vol, 2)
        if v_next <= vol2 + 1e-9:
            # Rounding down landed back on the level that was just read: at
            # this level, 2.5 dB isn't even one hundredth. Try the next tick
            # up instead (capped at the target, same as the normal step),
            # but only if that tick is still within the PLAN's own ceiling.
            v_alt = min(target, round(vol2 + 0.01, 2))
            if _db(v_alt) - cur_db <= STEP_DB_MAX + 1e-9:
                v_next = v_alt
            else:
                # UNREACHABLE ABOVE THE FLOOR IN PRACTICE (fix round 3): a
                # two-decimal tick is only ~2.5 dB at v=0.1 and smaller from
                # there up, so this branch should never fire here. If it
                # somehow does, that is a bug -- report it as the failure it
                # is rather than claiming the level was pinned.
                return {"pinned": False, "busy": False, "volume": vol,
                        "iterations": iterations,
                        "error": (f"stuck at {vol * 100:.0f}%: no safe step "
                                  "available above the floor")}

        _set_volume(v_next)
        _sleep(STEP_SECS)


def pin(target=1.0):
    """Ramp the sink to `target` (an amplitude, 1.0 = 0 dBFS = full scale),
    never more than ~3 dB per 100 ms measured against the REAL sink, not a
    plan. Single-flight: a second call while one is already running touches
    nothing and returns `busy` at once, because two ramps racing the same
    sink is its own way of jumping the level (fix round 2, Critical 1)."""
    try:
        fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        return {"pinned": False, "busy": False, "volume": None, "iterations": 0,
                "error": f"could not open the audio lock: {e}"}
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return {"pinned": False, "busy": True, "volume": None, "iterations": 0, "error": None}
    try:
        return _pin_locked(target)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def apply():
    """What the app calls at start and every half minute: hold 100% where
    `omacar audio on` asked for it, and only there. Plugging the AUX cable in
    switches to a port that keeps a volume of its own, which is why this is
    asked again rather than once.

    Returns the same shape as status() -- callers (the routes, audiostate.js,
    this file's own CLI) all read volume/muted/port/aux/managed/error from
    it -- with `busy` and pin()'s own `error` layered on top when a ramp
    actually ran."""
    st = status()
    if st["managed"] and st["volume"] is not None and (st["volume"] < 0.995 or st["muted"]):
        result = pin()
        st = status()
        if result.get("busy"):
            st = dict(st, busy=True)
        if result.get("error") and not st.get("error"):
            st = dict(st, error=result["error"])
    return st


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    note = None
    if cmd == "on":
        set_managed(True)
        result = pin()
        st = status()
        if result.get("busy"):
            note = "another pin was already running; nothing changed here"
        elif result.get("error"):
            note = result["error"]
    elif cmd == "off":
        set_managed(False)
        st = status()
    elif cmd == "pin":
        result = pin()
        st = status()
        if result.get("busy"):
            note = "another pin is already running; nothing changed"
        elif result.get("error"):
            note = result["error"]
    elif cmd == "status":
        st = status()
    else:
        print(__doc__)
        return 2
    vol = "?" if st["volume"] is None else f"{st['volume'] * 100:.0f}%"
    where = {"aux": "AUX (headphone jack)", "speakers": "the speakers: AUX disconnected"}.get(
        st["port"], st["port_name"] or "unknown")
    print(f"  output  {where}")
    print(f"  volume  {vol}{' muted' if st['muted'] else ''}"
          f"{' (held at 100%)' if st['managed'] else ''}")
    if note:
        print(f"  note    {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
