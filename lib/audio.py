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
100 ms, measured from the lowest real level the read allows, and every step
up is wpctl's own relative step (`wpctl set-volume -l <target> SINK <step>+`),
so it adds to whatever the level is when wpctl applies it and never passes
the target. It sets a muted sink to a quiet floor before unmuting it, reads
again the moment it has unmuted, and re-mutes if something raised the level
in between. It stops, saying so, when something else raises the level under
it. It is single-flight (a second call returns `busy` and touches nothing),
bounded in iterations and seconds, and it says `pinned: true` only on a read
showing the target. The rules, and the windows it cannot close, are in
_pin_locked()'s docstring.
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
# (0..1 and a bit beyond). wpctl prints two decimals, and a relative step
# lands wherever the level was plus the step, on the grid or not, so a read
# of v means a real level anywhere from v - READ_SLACK up to v + READ_SLACK.
TICK = 0.01
READ_SLACK = TICK / 2
# Each step aims for STEP_DB above the lowest real level the read allows, and
# is never more than STEP_DB_MAX (the plan's ceiling) above it. Near the top
# that makes the largest step 0.09 (from a read of 0.90, 0.91 or 0.92), which
# is what bounds an outside drop landing just before a step (_pin_locked()).
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
    """An absolute set: used only for the floor, while muted or from below
    it, where no level it can land on is loud."""
    rc, _ = _run(["wpctl", "set-volume", SINK, f"{v:.2f}"], timeout=TOOL_TIMEOUT)
    return rc == 0


def _step(delta, target):
    """wpctl's own relative step, as its help gives it: `VOL+` steps the
    volume up by VOL, and `-l` "limits the final volume ... to below this
    value". wpctl reads the level itself as it applies the set, adds
    `delta`, and stops at `target`. `-l` is a set-volume option, so it goes
    after the command (wpctl takes the command from argv[1]), as in
    Hyprland's own `wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+`."""
    rc, _ = _run(["wpctl", "set-volume", "-l", f"{target:.2f}", SINK, f"{delta:.2f}+"],
                 timeout=TOOL_TIMEOUT)
    return rc == 0


def _mute(on):
    rc, _ = _run(["wpctl", "set-mute", SINK, "1" if on else "0"], timeout=TOOL_TIMEOUT)
    return rc == 0


def _heard_db(vol, muted, slack=0.0):
    """The level a driver hears, for spotting a rise: muted, or anywhere
    below the floor, counts as the floor. So pin()'s own unmute and its own
    move up to the floor are not rises, and nothing that stays below the
    floor is either. `slack` moves the read within the real levels it
    allows (±READ_SLACK)."""
    v = vol + slack
    return FLOOR_DB if muted or v < FLOOR_V else 60.0 * math.log10(v)


def _rose(before, after):
    """True when the real level certainly rose more than STEP_DB_MAX between
    two reads: from the highest level `before` allows to the lowest `after`
    allows. Reads round, so read-to-read can overstate a step: a real 0.155
    reads 0.15, and one 0.01 step later a real 0.165 reads 0.17, which looks
    like 3.3 dB for a real 1.6. Judged this way, pin()'s own steps (at most
    STEP_DB from the real level) can never trip it."""
    return _heard_db(*after, -READ_SLACK) - _heard_db(*before, READ_SLACK) > STEP_DB_MAX + 1e-9


def _step_up(vol, target):
    """The relative step Δ for the next move from an unmuted read of `vol`
    (at or above the floor), sent by _step() as `Δ+` under `-l target`.

    Sized from the lowest real level that reads as `vol` (vol - READ_SLACK):
    the rise that is STEP_DB above it, rounded DOWN to two decimals, so
    rounding can only hold a step back; at least one tick, when that tick is
    within STEP_DB_MAX of it; and never more than it takes to reach the
    target from it (rounded up to the tick, which -l then stops at the
    target). From any real level the read allows, the landing is at most
    STEP_DB above it, because a higher start makes the same Δ a smaller
    step. From a read above the target Δ is 0, and -l alone brings the
    level down to the target. None when no step is safe, which the floor
    makes impossible from 0.13 up."""
    low = vol - READ_SLACK
    need = target - low
    if need <= 0:
        return 0.0
    d = math.floor(low * (10 ** (STEP_DB / 60.0) - 1) * 100 + 1e-9) / 100.0
    if d < TICK - 1e-9:
        d = TICK
        if 60.0 * math.log10((low + d) / low) > STEP_DB_MAX + 1e-9:
            return None
    return min(d, math.ceil(need * 100 - 1e-9) / 100.0)


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
    2. Immediately before every set-volume and unmute, the sink is read
       again. If that read differs at all from the one the move was decided
       from, nothing is set, and the move is decided again from the new
       read. (The ruling allowed one 0.01 tick of slack. There is none,
       because only an outside actor changes the sink between two reads
       milliseconds apart, and a step is sized for the lowest level its
       read allows, so from a lower level the same step is a bigger one.)
    3. Every read is checked against the one before it (_rose()). A rise of
       more than STEP_DB_MAX, in what a driver hears, judged from the
       highest real level the earlier read allows to the lowest the later
       one allows, is not pin()'s doing, because its own steps are at most
       STEP_DB from the real level. So pin() takes no further step, and
       returns pinned: false saying an outside change interfered. It does
       not ramp on from a level it did not set, and it does not undo the
       rise: the level stays where the outside change put it.
    4. Muted, the only move is to the floor, inaudibly. The unmute happens
       only when two reads in a row show muted at or below the floor.
       Unmuted and below the floor, one absolute set goes straight to it:
       nothing down there is loud enough to startle anybody. From the floor
       up, each step is wpctl's own relative step, _step(): Δ from
       _step_up(), at most STEP_DB above the lowest real level the read
       allows, never past the target, and STEP_SECS after the last move up.
    5. Bounded: MAX_ITERATIONS, no iteration starts after LAST_START_SECS,
       no call starts after MAX_SECS, and every call has TOOL_TIMEOUT. An
       unmute starts only while there is room for the two calls (7) may
       make after it, so those also start before MAX_SECS.
    6. pinned: true comes only from a read showing the target, unmuted.
    7. The moment pin() has unmuted, it reads again. If the level is more
       than STEP_DB_MAX above the floor, a rise landed between the read in
       (2) and the unmute. If the read fails, nothing shows that it did
       not. Either way pin() mutes again at once, rests, and goes round
       from (1), which sets the floor again before the next unmute. If
       that mute fails, pin() stops, not pinned, and says so.

    THE WINDOWS THIS CANNOT CLOSE. wpctl has no compare-and-set. Every set
    and every unmute is its own wpctl process, which starts, connects to
    PipeWire and acts some milliseconds after the read in (2). That is
    about one wpctl call: 9 ms median, 39.6 ms at most on the box, not
    measured on the tablet. An outside change can land in that gap -- a
    volume key, or the headphone route restoring its own level -- and (2)
    cannot see it. (2) keeps each gap to that one call. No decision, sleep
    or other call sits between the last read and the move.

    Before a step up, the danger is a drop. A plain set would overwrite
    the drop with a level sized for the read before it: a drop to 0.25
    just before a set to 1.00 is +36.1 dB. A relative step is added to the
    level wpctl finds instead, so the sink lands at most Δ above where the
    drop left it, and never above the target. Δ is at most 0.09. So a drop
    to the floor costs at most 0.13 -> 0.22, +13.7 dB (-53.2 to -39.5 dBFS),
    and a drop to 0.25 costs 0.25 -> 0.34, +8.0 dB. Below the floor the
    ratio is larger, but nothing lands louder than 0.22.
    - What remains is inside wpctl itself. It learns the level when it
      connects, and adds Δ to that. A drop between then and PipeWire
      applying the set is still overwritten, by at most the target. That
      window is a fraction of one call and has not been measured.
    - pin() does not see any of this. The sink lands lower than the step
      would have left it without the drop, so (3) does not fire.

    Before the unmute, the danger is a rise on the still-muted sink. The
    unmute exposes it. (7) detects it and cuts it short; it does not stop
    it being heard. From the unmute landing to the re-mute landing is
    about two wpctl calls: the read in (7), then the re-mute's own
    start-up. That is ~18 ms at the box's median. For that time the level
    is whatever the outside change set, up to full scale.

    (3), likewise, only detects: a rise it sees between two reads has
    already been heard.
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
        return last is not None and _rose(last, reading)

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
            move = ("unmute", None) if vol <= FLOOR_V + 1e-9 else ("floor", FLOOR_V)
        elif vol < FLOOR_V - 1e-9:
            move = ("floor", FLOOR_V)
        elif abs(60.0 * math.log10(vol) - target_db) <= TARGET_TOL_DB:
            return result(True, None)                            # (6)
        else:
            delta = _step_up(vol, target)
            if delta is None:
                return result(False, f"stuck at {vol * 100:.0f}%: no step within "
                                     f"{STEP_DB_MAX:g} dB is available")
            move = ("step", delta)

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
        if check != (vol, muted):
            carried = check
            continue

        # (5) An unmute needs room for the read and the mute (7) may follow it with.
        if elapsed() >= MAX_SECS - (2 * TOOL_TIMEOUT if move[0] == "unmute" else 0):
            return short("ran out of time")
        if move[0] == "unmute":
            _mute(False)
            after = _read()                                      # (7)
            if after[0] is None or _rose((FLOOR_V, True), after):
                if not _mute(True):
                    return dict(result(False, (
                        "could not read the level straight after unmuting" if after[0] is None
                        else f"an outside change interfered: the volume rose to "
                             f"{after[0] * 100:.0f}% in the moment before pin() unmuted it")
                        + ", and muting again failed, so the sink is unmuted there"),
                        volume=after[0])
                rest()
                continue
            last = after
        elif move[0] == "floor":
            _set_volume(move[1])
            if muted:
                continue          # inaudible while muted: re-read and confirm at once
        else:
            _step(move[1], target)
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
