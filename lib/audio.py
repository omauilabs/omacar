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

pin() never jumps there. wpctl's volume is a CUBIC control, so one call from
25% to 100% is about +36 dB -- audibly a shout, not a step -- and that call
used to run every 30 seconds, right as the AUX cable went in and the radio
was already playing through the car. It now climbs in steps no larger than
3 dB every 100 ms, the same ceiling the rest of this stage holds itself to
(share/js/audiobus.js), and a muted sink is set to a quiet floor before it is
unmuted rather than becoming audible at whatever level it was left at.
"""

import json
import os
import re
import subprocess
import sys
import time

SINK = "@DEFAULT_AUDIO_SINK@"

# wpctl's volume knob is CUBIC, not linear: dB = 60*log10(v) (v the number
# wpctl takes, 0..1 and a bit beyond). "Nothing sets a level in one step"
# means a step on THAT scale -- 0.25 -> 1.00 in one wpctl call is not a jump
# of 0.75, it is +36 dB, about what the internal speakers actually measured
# at on 2026-09-28. So pin() below climbs in RAMP_DB steps of loudness, at
# least RAMP_SECS apart, converting each dB step back to wpctl's own number.
RAMP_DB = 3.0                              # the plan's own ceiling per step
RAMP_SECS = 0.1                            # ...and its own ceiling per 100 ms
RAMP_RATIO = 10 ** (RAMP_DB / 60.0)        # wpctl-scale multiplier for one step
# Quiet enough that unmuting here is not itself an audible jump -- about
# -48 dB, the one step that cannot be ramped (silence has no "partway").
FLOOR_DB = -48.0
FLOOR_V = 10 ** (FLOOR_DB / 60.0)


def flag_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-audio.json")


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
# audio_test.py spending a real 1-2 s on every call to pin().
def _sleep(secs):
    time.sleep(secs)


def _set_volume(v):
    # Six places, not four: wpctl only needs a handful, but rounding the
    # string throws away a little precision at every step, and losing it
    # twice -- once per endpoint -- can push a nominally-3 dB step a few
    # thousandths over. Six decimals keeps that well under a millibel.
    rc, _ = _run(["wpctl", "set-volume", SINK, f"{v:.6f}"])
    return rc == 0


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


def pin():
    """Raise the sink to 100%, never more than RAMP_DB per RAMP_SECS -- the one
    rule this whole stage exists to keep. A muted sink is set to FLOOR_V WHILE
    STILL MUTED (inaudible: muted is muted, whatever the number underneath
    says) and only unmuted once it is there, so the one step that truly
    cannot be ramped -- off to on -- lands quiet rather than loud, and the
    ramp climbs from that floor like any other start.

    Runs synchronously: on the cubic scale, 25% to 100% is about 13 steps,
    roughly 1.3 s. That is well inside the threaded server's per-request
    budget (lib/serve.py is a ThreadingHTTPServer), so one slow /api/audio
    POST costs only its own request, and it is the price of never letting a
    level jump.
    """
    rc, out = _run(["wpctl", "get-volume", SINK])
    vol, muted = parse_volume(out) if rc == 0 else (None, None)
    if vol is None:
        return status()          # unreadable; status() already says why

    ok = True
    if muted:
        ok = _set_volume(FLOOR_V) and ok
        rc2, _ = _run(["wpctl", "set-mute", SINK, "0"])
        ok = rc2 == 0 and ok
        vol = FLOOR_V
        # Unmuting just made the floor level audible. The very next call is a
        # real dB increase, so it is paced like any other step rather than
        # landing back to back with the one that just went out.
        if vol < 1.0 - 1e-6:
            _sleep(RAMP_SECS)

    while vol < 1.0 - 1e-6:
        vol = min(1.0, vol * RAMP_RATIO)
        ok = _set_volume(vol) and ok
        if vol < 1.0 - 1e-6:
            _sleep(RAMP_SECS)

    st = status()
    if not ok and st.get("error") is None:
        st = dict(st, error="wpctl refused to set the volume")
    return st


def apply():
    """What the app calls at start and every half minute: hold 100% where
    `omacar audio on` asked for it, and only there. Plugging the AUX cable in
    switches to a port that keeps a volume of its own, which is why this is
    asked again rather than once."""
    st = status()
    if st["managed"] and st["volume"] is not None and (st["volume"] < 0.995 or st["muted"]):
        st = pin()
    return st


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "on":
        set_managed(True)
        st = pin()
    elif cmd == "off":
        set_managed(False)
        st = status()
    elif cmd == "pin":
        st = pin()
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
