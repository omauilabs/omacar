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

pin() NEVER JUMPS THE LEVEL, because the jack drives the car's speakers and a
jump startles the driver. It is a closed loop: every move is decided from a
fresh read of the real sink, never from where pin() thinks it left it, and is
re-read again immediately before it is made. It climbs at most 3 dB per
100 ms, measured from the lowest real level the read allows. It sets a muted
sink to a quiet floor before unmuting it, and it stops, saying so, when
something else raises the level under it. It is single-flight (a second call
returns `busy` and touches nothing), bounded in iterations and seconds, and
it says `pinned: true` only on a read showing the target. The rules and the
one window it cannot close are in _pin_locked()'s docstring.
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

# wpctl's volume knob is CUBIC: dB = 60*log10(v), v the number wpctl takes
# (0..1 and a bit beyond). wpctl prints two decimals and pin() sets two, so a
# read of v means a real level anywhere from v - READ_SLACK up.
TICK = 0.01
READ_SLACK = TICK / 2
# Each step aims for STEP_DB above the lowest real level the read allows, and
# is never more than STEP_DB_MAX (the plan's ceiling) above it.
STEP_DB = 2.5
STEP_DB_MAX = 3.0
STEP_SECS = 0.1
TARGET_TOL_DB = 0.1
# The floor: quiet enough that the two steps which cannot be ramped -- the
# unmute (silence has no "partway") and the move up from anything below it --
# land where nobody is startled. 0.13 (-53.2 dB), not lower, because steps are
# sized from read - READ_SLACK: from a read of 0.12 the next tick is 3.2 dB
# above the lowest real level that reads 0.12 (0.13 over 0.115), while from
# 0.13 up there is always a tick within 3 dB (0.14 over 0.125 is 2.95 dB).
FLOOR_V = 0.13
FLOOR_DB = 60.0 * math.log10(FLOOR_V)
# Every wpctl and pactl call in this file passes this timeout, so no call can
# wedge a request: pin() retries anyway, and status() just reports what it got.
TOOL_TIMEOUT = 1
MAX_ITERATIONS = 40
# No iteration starts at or after LAST_START_SECS, and no wpctl call starts at
# or after MAX_SECS, so pin() ends by MAX_SECS + TOOL_TIMEOUT at the latest.
LAST_START_SECS = 7.0
MAX_SECS = 8.0
# apply()'s worst case, for the page's abort (share/js/audiostate.js,
# APPLY_TIMEOUT_MS): status()'s three calls, pin() running out its time with
# one call in flight, and the one read after it.
APPLY_WORST_SECS = 3 * TOOL_TIMEOUT + (MAX_SECS + TOOL_TIMEOUT) + TOOL_TIMEOUT


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


# The three hooks tests replace: _run with a fake sink, _sleep and _now with
# its fake clock, so nothing in test/audio_test.py touches a real device or
# spends real seconds.
def _run(args, timeout):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _sleep(secs):
    time.sleep(secs)


def _now():
    return time.monotonic()


def _read():
    """(volume, muted) from one `wpctl get-volume`, or (None, None)."""
    rc, out = _run(["wpctl", "get-volume", SINK], timeout=TOOL_TIMEOUT)
    return parse_volume(out) if rc == 0 else (None, None)


def _set_volume(v):
    rc, _ = _run(["wpctl", "set-volume", SINK, f"{v:.2f}"], timeout=TOOL_TIMEOUT)
    return rc == 0


def _heard_db(vol, muted):
    """The level a driver hears, for spotting a rise: muted, or anywhere
    below the floor, counts as the floor. So pin()'s own unmute and its own
    move up to the floor are not rises, and nothing that stays below the
    floor is either."""
    return FLOOR_DB if muted or vol < FLOOR_V else 60.0 * math.log10(vol)


def _step_up(vol, target):
    """The next level from an unmuted read of `vol` (at or above the floor):
    STEP_DB above the lowest real level that reads as `vol`, capped at the
    target and rounded DOWN to two decimals, so rounding can only hold a step
    back. If that rounds back onto `vol`, the next tick instead, when it is
    within STEP_DB_MAX. None when no step is safe, which the floor above
    makes impossible from 0.13 up."""
    low_db = 60.0 * math.log10(vol - READ_SLACK)
    raw = min(target, 10 ** ((low_db + STEP_DB) / 60.0))
    v = math.floor(raw * 100 + 1e-9) / 100.0
    if v <= round(vol, 2) + 1e-9:
        v = min(target, round(vol + TICK, 2))
        if 60.0 * math.log10(v) - low_db > STEP_DB_MAX + 1e-9:
            return None
    return v


def status():
    vol, muted = _read()
    _, default = _run(["pactl", "get-default-sink"], timeout=TOOL_TIMEOUT)
    rc2, js = _run(["pactl", "--format=json", "list", "sinks"], timeout=TOOL_TIMEOUT)
    try:
        sinks = json.loads(js) if rc2 == 0 and js.strip() else []
    except ValueError:
        sinks = []
    kind, desc = port_of(sinks, (default or "").strip())
    return {"volume": vol, "muted": muted, "port": kind, "port_name": desc,
            "aux": {"aux": True, "speakers": False}.get(kind), "managed": managed(),
            "error": None if vol is not None else "wpctl could not read the volume"}


def _pin_locked(target):
    """The ramp, run only once pin() holds the lock.

    1. Every move is decided from a fresh read of the real sink, never from
       where the last move meant to leave it. A failed read, or a failed
       set, sets nothing; the next iteration reads again.
    2. Immediately before every set-volume and set-mute, the sink is read
       again. If that read differs at all from the one the move was decided
       from, nothing is set, and the move is decided again from the new
       read. (The ruling allowed one 0.01 tick of slack. There is none,
       because only an outside actor changes the sink between two reads
       milliseconds apart, and a step sized for the higher read can be over
       3 dB from the lower one: 0.21 over a real 0.1851, which reads 0.19,
       is 3.3 dB.)
    3. Every read is checked against the one before it. A rise of more than
       STEP_DB_MAX, in what a driver hears, is not pin()'s doing, because its
       own steps are smaller. So pin() takes no further step, and returns
       pinned: false saying an outside change interfered. It does not ramp
       on from a level it did not set.
    4. Muted, the only move is to the floor, inaudibly. The unmute happens
       only when two reads in a row show muted at or below the floor.
       Unmuted and below the floor, one step goes straight to it: nothing
       down there is loud enough to startle anybody. From the floor up,
       each step is _step_up(): at most STEP_DB_MAX above the lowest real
       level the read allows, and STEP_SECS after the last audible move.
    5. Bounded: MAX_ITERATIONS, no iteration starts after LAST_START_SECS,
       no call starts after MAX_SECS, and every call has TOOL_TIMEOUT.
    6. pinned: true comes only from a read showing the target, unmuted.

    THE WINDOW THIS CANNOT CLOSE. A window of milliseconds remains between
    the read in (2) and the set that follows it: the reading wpctl
    exiting, then the setting wpctl starting and connecting to PipeWire.
    That is about one wpctl call, 9 ms median on the box; it has not been
    measured on the tablet. An outside change landing exactly there -- a
    volume key, or the headphone route restoring its own level -- is
    overwritten by a set sized for the level before it. If that change
    was a drop, the set is a jump. Closing the window would take a set
    that applies only if the level is still what was read, a
    compare-and-set, and wpctl has none: it takes a volume as a plain
    value, so any read-then-set leaves this gap. (2) keeps the gap at
    that minimum. No decision, sleep or other call ever sits between the
    last read and the set. But (2) cannot make the gap shorter than one
    call, and a single read followed at once by the set was already that
    short. (3) catches any rise the gap lets through, at the next read.
    """
    start = _now()
    iterations = 0
    last = None          # (volume, muted) of the most recent successful read
    carried = None       # a pre-set read that did not match, to decide from next
    target_db = 60.0 * math.log10(target)

    def elapsed():
        return _now() - start

    def result(pinned, error):
        return {"pinned": pinned, "busy": False, "volume": last[0] if last else None,
                "iterations": iterations, "error": error}

    def short(why):
        where = ("without a readable volume" if last is None else
                 f"at {last[0] * 100:.0f}%{' muted' if last[1] else ''}, short of the target")
        return result(False, f"{why} after {iterations} iterations {where}")

    def rest():
        # The gap between audible moves. Skipped once no further iteration
        # may start, so a sleep never adds to the time bound's overrun.
        if elapsed() < LAST_START_SECS:
            _sleep(STEP_SECS)

    def outside_rise(reading):
        return last is not None and _heard_db(*reading) - _heard_db(*last) > STEP_DB_MAX + 1e-9

    def interfered(reading):
        return dict(result(False, (
            f"an outside change interfered: the volume rose from "
            f"{last[0] * 100:.0f}%{' muted' if last[1] else ''} to "
            f"{reading[0] * 100:.0f}%{' muted' if reading[1] else ''} between two "
            "reads, so pin() stopped rather than ramp on from a level it did not set")),
            volume=reading[0])

    while True:
        if iterations >= MAX_ITERATIONS:
            return short("gave up")
        if elapsed() >= LAST_START_SECS:
            return short("ran out of time")
        iterations += 1

        # (1) The read this iteration's move is decided from.
        if carried is not None:
            reading, carried = carried, None
        else:
            reading = _read()
            if reading[0] is None:
                rest()
                continue
            if outside_rise(reading):                            # (3)
                return interfered(reading)
            last = reading
        vol, muted = reading

        # (4) Decide the move.
        if muted:
            move = ("unmute", None) if vol <= FLOOR_V + 1e-9 else ("set", FLOOR_V)
        elif vol < FLOOR_V - 1e-9:
            move = ("set", FLOOR_V)
        elif abs(60.0 * math.log10(vol) - target_db) <= TARGET_TOL_DB:
            return result(True, None)                            # (6)
        else:
            v_next = _step_up(vol, target)
            if v_next is None:
                return result(False, f"stuck at {vol * 100:.0f}%: no step within "
                                     f"{STEP_DB_MAX:g} dB is available")
            move = ("set", v_next)

        # (2) Read again, immediately before acting on it.
        if elapsed() >= MAX_SECS:
            return short("ran out of time")
        check = _read()
        if check[0] is None:
            rest()
            continue
        if outside_rise(check):                                  # (3)
            return interfered(check)
        last = check
        if (round(check[0], 2), check[1]) != (round(vol, 2), muted):
            carried = check
            continue

        if elapsed() >= MAX_SECS:
            return short("ran out of time")
        if move[0] == "unmute":
            _run(["wpctl", "set-mute", SINK, "0"], timeout=TOOL_TIMEOUT)
        else:
            _set_volume(move[1])
            if muted:
                continue          # inaudible while muted: re-read and confirm at once
        rest()


def pin(target=1.0):
    """Ramp the sink to `target` (an amplitude, 1.0 = 0 dBFS = full scale),
    never more than 3 dB per 100 ms measured from the real sink, and return
    {pinned, busy, volume, iterations, error}. Single-flight: a second call
    while one is running touches nothing and returns `busy` at once, because
    two ramps racing the same sink is its own way of jumping the level."""
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

    Returns the same shape as status(), with `busy` and pin()'s own `error`
    layered on top when a ramp ran. After a ramp only the volume is read
    again; the port is the one read before it (the next poll reads it anew).
    That keeps the worst case at APPLY_WORST_SECS: 3 s for status(), 9 s for
    pin(), 1 s for the read after, 13 s in all, inside the page's 15 s abort."""
    st = status()
    if st["managed"] and st["volume"] is not None and (st["volume"] < 0.995 or st["muted"]):
        result = pin()
        vol, muted = _read()
        st = dict(st, volume=vol, muted=muted,
                  error=None if vol is not None else "wpctl could not read the volume")
        if result.get("busy"):
            st["busy"] = True
        if result.get("error") and not st["error"]:
            st["error"] = result["error"]
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
