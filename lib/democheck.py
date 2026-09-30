#!/usr/bin/env python3
"""`omacar demo check`: is this machine ready to give the meetup demo?

    python3 lib/democheck.py [--private DIR]

One line per check, then "ready" or "not ready: <the checks that failed>",
and exit 0 only when ready. It READS ONLY: nothing is written, started or
stopped, and nothing is played.

    songs        Omarchy Radio's seven songs, each the size demo/data/media-pins.json
                 pins, as the station's playlist.json names them
    drive, map   demo/drive.json and demo/map.json, whole
    map fits drive   the map's origin is the drive's first point, to 1e-5 degrees:
                 a map built from another route draws the car in the wrong place
    voice        a wav for every line in demo/data/voice.json
    clips        the cameras' footage: front, rear, cabin and cabin-drowsy, in
                 demo/clips or where OMACAR_DEMO_CLIPS says, as the camera feed reads
    clips play   each clip present is H.264, by ffprobe (cams.py's own probe): the
                 feed copies the clips as they are and the Cameras tab's <video>
                 plays H.264 everywhere, HEVC only on some hardware
    road cameras saved stills (roadcams/img/*.jpg) in the demo's state or the real
                 one, for a venue with no internet (`omacar-demo on` copies them)
    logo         omacar-logo.png, the top bar's
    car picture  demo/crz-home.png, Home's (tools/demo_carpic.py makes it)
    vehicle picture  crz-xray.png, Vehicle's X-ray (without it, a placeholder)
    volume pin   the LIVE app's `omacar audio on` is off: while it is on, the
                 live page under the demo pushes the speakers to 100% every 30 s
    kiosk        the live kiosk is running underneath, as it should be
    stay awake   the screen will not sleep mid-demo: Omarchy's stay-awake indicator
                 (~/.local/state/omarchy/indicators/stay-awake) is there, or the live
                 kiosk that holds it while it runs (`omacar kiosk launcher`) is
    demo server  when the demo is on, its server answers /demo.html

The media are under share/assets/private/ (git-ignored), or DIR. The volume pin
is read from the REAL settings folder (~/.config/omarchy), never the demo's:
inside `omacar demo`'s environment XDG_CONFIG_HOME points at the demo's own.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "share", "assets", "private")
PINS = os.path.join(ROOT, "demo", "data", "media-pins.json")
VOICE = os.path.join(ROOT, "demo", "data", "voice.json")
CLIP_ROLES = ("front", "rear", "cabin", "cabin-drowsy")
# The live kiosk's Chromium, by its profile (bin/omacar's KIOSK_PROFILE), exactly:
# `--user-data-dir=$HOME/.local/share/omacar/kiosk-profile`, and the argument
# ends there. A looser `user-data-dir=.*/omacar/kiosk-profile` took a test's
# stand-in kiosk left running under /tmp for the live one. The demo's window
# has its own profile, under the demo's folder, and does not match either.
def kiosk_profile():
    """bin/omacar's KIOSK_PROFILE: ${XDG_DATA_HOME:-$HOME/.local/share}/omacar/kiosk-profile.
    (`omacar demo` moves the state and config folders, never the data one.)"""
    data = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return os.path.join(os.path.expanduser(data), "omacar", "kiosk-profile")


def _ere(text):
    """`text` as a literal in pgrep's extended regular expressions."""
    return "".join("\\" + c if c in ".[]()*+?{}|^$\\" else c for c in text)


def kiosk_pattern():
    return "--user-data-dir=" + _ere(kiosk_profile()) + "( |$)"
DEMO_MARK = "omacar-demo"
# Omarchy's stay-awake switch, as bin/omacar's kiosk_idle_hold reads it: under
# $HOME, not the XDG folders (Omarchy's own scripts write it there).
STAY_AWAKE = os.path.join("~", ".local", "state", "omarchy", "indicators", "stay-awake")
# What the live kiosk's command line holds (bin/omacar kiosk), and the kiosk
# holds the indicator while it runs.
KIOSK_LAUNCHER = "omacar kiosk launcher"


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _whole_json(path):
    """None if `path` is a whole JSON object, else what is wrong with it."""
    if not os.path.isfile(path):
        return "missing"
    try:
        doc = _read_json(path)
    except (OSError, ValueError) as e:
        return f"unreadable ({type(e).__name__})"
    return None if isinstance(doc, dict) else "not a JSON object"


# ---- the checks: each returns (ok, what it found) ------------------------------

def songs(private, pins):
    radio = os.path.join(private, "omarchy-radio")
    try:
        tracks = _read_json(os.path.join(radio, "playlist.json")).get("tracks") or []
    except (OSError, ValueError, AttributeError):
        return False, f"no playlist.json in {radio}"
    want = pins.get("radio") or {}
    wrong = []
    for t in tracks:
        name = (t or {}).get("file") or ""
        path = os.path.join(radio, name)
        if not name or not os.path.isfile(path):
            wrong.append(f"{name or '(unnamed)'} missing")
            continue
        size = os.path.getsize(path)
        if name not in want:
            wrong.append(f"{name} is not pinned")
        elif size != want[name]:
            what = "truncated" if size < want[name] else "the wrong size"
            wrong.append(f"{name} {what} ({size} of {want[name]} bytes)")
    listed = {(t or {}).get("file") for t in tracks}
    wrong += [f"{name} missing from playlist.json" for name in want if name not in listed]
    if wrong:
        return False, "; ".join(wrong)
    return True, f"{len(tracks)} of {len(want)}, each the size pinned"


def whole(private, rel):
    why = _whole_json(os.path.join(private, rel))
    return (True, rel) if why is None else (False, f"{rel} {why}")


def voice(private):
    try:
        ids = [x["id"] for x in _read_json(VOICE)]
    except (OSError, ValueError, KeyError, TypeError):
        return False, f"cannot read {os.path.relpath(VOICE, ROOT)}"
    bad = []
    for i in ids:
        path = os.path.join(private, "demo", "voice", i + ".wav")
        try:
            with open(path, "rb") as f:
                head = f.read(12)
        except OSError:
            bad.append(f"{i} missing")
            continue
        if not (head[:4] == b"RIFF" and head[8:12] == b"WAVE"):
            bad.append(f"{i} is not a wav")
    if bad:
        return False, "; ".join(bad) + " (tools/demo_voice.py makes them)"
    return True, f"{len(ids)} lines"


def clip_files(private):
    """(the folder, {role: path} of the clips there). Where the camera feed
    reads them: bin/omacar passes OMACAR_DEMO_CLIPS to `cams.py demo --from`
    when it is set, and the private tree's otherwise. Only clips with
    something in them count as there."""
    here = os.environ.get("OMACAR_DEMO_CLIPS") or os.path.join(private, "demo", "clips")
    found = {}
    for role in CLIP_ROLES:
        path = os.path.join(here, role + ".mp4")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            found[role] = path
    return here, found


def clips(private):
    here, found = clip_files(private)
    missing = [r for r in CLIP_ROLES if r not in found]
    if missing:
        return False, "stock/owner clips missing: " + ", ".join(missing) + f" (in {here})"
    return True, ", ".join(CLIP_ROLES) + f" (in {here})"


def clips_play(private):
    """Every clip that is there is H.264: the camera feed copies them as they
    are (never re-encoded) and the Cameras tab's <video> plays H.264 on any
    machine, HEVC on some. cams.py's demo_warnings asks ffprobe, exactly as
    `cams.py demo` does when it starts, and names each clip that is not, or
    that ffprobe cannot read. None when there is nothing to read: `clips`
    says so."""
    _, found = clip_files(private)
    if not found:
        return None, "no clip to read (see clips)"
    try:
        import cams
    except ImportError as e:
        return False, f"cannot load lib/cams.py's probe ({e})"
    wrong = cams.demo_warnings(found)
    if wrong:
        return False, "; ".join(wrong)
    return True, ", ".join(found) + ": H.264"


def picture(private, rel, note=""):
    path = os.path.join(private, rel)
    try:
        with open(path, "rb") as f:
            png = f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False, f"{rel} missing{note}"
    return (True, rel) if png else (False, f"{rel} is not a PNG")


def real_config():
    """The machine's own settings folder, even from inside the demo's
    environment, whose XDG_CONFIG_HOME is the demo's."""
    x = os.environ.get("XDG_CONFIG_HOME")
    if x and DEMO_MARK not in os.path.abspath(os.path.expanduser(x)).split(os.sep):
        return os.path.expanduser(x)
    return os.path.expanduser("~/.config")


def real_state():
    """The machine's own state folder, even from inside the demo's environment,
    whose XDG_STATE_HOME is the demo's."""
    x = os.environ.get("XDG_STATE_HOME")
    if x and DEMO_MARK not in os.path.abspath(os.path.expanduser(x)).split(os.sep):
        return os.path.expanduser(x)
    return os.path.expanduser("~/.local/state")


def roadcams():
    """Saved road-camera stills, in the demo's own state or the real one: with
    no internet at the venue they are all the Road cameras screen has, and
    `omacar-demo on` copies the real ones into the demo's the first time."""
    for state in (os.path.join(demo_root(), "state"), real_state()):
        stills = glob.glob(os.path.join(state, "omacar", "roadcams", "img", "*.jpg"))
        if stills:
            return True, f"{len(stills)} saved stills in {os.path.dirname(stills[0])}"
    return False, "no saved road-camera stills: run omacar-demo on once while online"


def map_fits_drive(private):
    """The car is drawn on the map from the drive's own coordinates, so the map
    must have been built from that drive: its origin is the drive's first point.
    None when either file cannot be read: `drive` and `map` say so."""
    try:
        drive = _read_json(os.path.join(private, "demo", "drive.json"))
        mp = _read_json(os.path.join(private, "demo", "map.json"))
    except (OSError, ValueError):
        return None, "not compared: drive.json or map.json cannot be read (see above)"
    try:
        lat, lon = drive["points"][0][1:3]
        lat0, lon0 = mp["origin"]
        fits = abs(lat0 - lat) <= 1e-5 and abs(lon0 - lon) <= 1e-5
    except (KeyError, IndexError, TypeError, ValueError):
        return False, ("map.json has no origin, or drive.json no first point: "
                       "rebuild with tools/demo_map.py")
    if not fits:
        return False, "map.json was built from another drive: rebuild with tools/demo_map.py"
    return True, "origin is the drive's first point"


def volume_pin():
    path = os.path.join(real_config(), "omarchy", "omacar-audio.json")
    try:
        on = bool(_read_json(path).get("pin"))
    except FileNotFoundError:
        return True, "off (never set)"
    except (OSError, ValueError, AttributeError):
        return True, "off (unreadable, which the live app reads as off)"
    if on:
        return False, ("ON: the live page would push the speakers to 100% every 30 s "
                       "during the demo. Turn it off: omacar audio off")
    return True, "off"


def kiosk():
    try:
        r = subprocess.run(["pgrep", "-f", "--", kiosk_pattern()],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not ask pgrep: {e}"
    if r.returncode == 0 and r.stdout.strip():
        return True, "running (pid " + ", ".join(r.stdout.split()) + ")"
    return False, "not running: omacar kiosk"


def stay_awake():
    """The screen must not sleep in front of the room. Omarchy's stay-awake
    indicator is a file, written while the switch is on, and the live kiosk
    turns the switch on for as long as it runs (bin/omacar kiosk_idle_hold), so
    either the file is there or the kiosk that holds it is."""
    path = os.path.expanduser(STAY_AWAKE)
    if os.path.exists(path):
        return True, f"Omarchy's stay-awake is on ({STAY_AWAKE})"
    fix = "start the live kiosk, or turn on Omarchy's stay-awake"
    try:
        r = subprocess.run(["pgrep", "-f", "--", KIOSK_LAUNCHER],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"the screen may sleep during the demo: could not ask pgrep ({e}); {fix}"
    if r.returncode == 0 and r.stdout.strip():
        return True, "the live kiosk holds it (pid " + ", ".join(r.stdout.split()) + ")"
    return False, f"the screen may sleep during the demo: {fix}"


def demo_root():
    base = os.path.abspath(os.path.expanduser(os.environ.get("XDG_STATE_HOME") or "~/.local/state"))
    parts = base.split(os.sep)
    if DEMO_MARK in parts:                  # run from inside the demo's environment
        return os.sep.join(parts[:parts.index(DEMO_MARK) + 1])
    return os.path.join(base, DEMO_MARK)


def demo_server():
    """None when the demo is off (nothing to ask), else (ok, what)."""
    if not os.path.exists(os.path.join(demo_root(), "ACTIVE")):
        return None
    port = os.environ.get("OMACAR_DEMO_PORT") or "7580"
    url = f"http://127.0.0.1:{port}/demo.html"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            body = r.read(1 << 20)
            status = r.status
    except (urllib.error.URLError, OSError) as e:
        return False, f"{url} did not answer ({getattr(e, 'reason', e)})"
    if status == 200 and b"demo/js/boot.js" in body:
        return True, f"{url} answers"
    return False, f"{url} answered {status}, and not with the demo page"


# ---- the report ------------------------------------------------------------------

def run(private):
    try:
        pins = _read_json(PINS)
    except (OSError, ValueError):
        pins = {}
    results = [
        ("songs", *songs(private, pins)),
        ("drive", *whole(private, os.path.join("demo", "drive.json"))),
        ("map", *whole(private, os.path.join("demo", "map.json"))),
        ("map fits drive", *map_fits_drive(private)),
        ("voice", *voice(private)),
        ("clips", *clips(private)),
        ("clips play", *clips_play(private)),
        ("logo", *picture(private, "omacar-logo.png")),
        ("car picture", *picture(private, os.path.join("demo", "crz-home.png"),
                                 " (tools/demo_carpic.py makes it)")),
        ("vehicle picture", *picture(private, "crz-xray.png", " (omacar assets push copies it to the tablet)")),
        ("road cameras", *roadcams()),
        ("volume pin", *volume_pin()),
        ("kiosk", *kiosk()),
        ("stay awake", *stay_awake()),
    ]
    server = demo_server()
    results.append(("demo server", *(server if server is not None
                                     else (None, "off: not asked (omacar demo on starts it)"))))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Is this machine ready to give the meetup demo?")
    ap.add_argument("--private", default=PRIVATE, help="the private media (default: share/assets/private)")
    a = ap.parse_args(argv)
    results = run(os.path.abspath(a.private))
    for name, ok, what in results:
        mark = "--  " if ok is None else ("ok  " if ok else "FAIL")
        print(f"  {mark}  {name:<15} {what}")
    failed = [name for name, ok, _ in results if ok is False]
    print()
    print("not ready: " + ", ".join(failed) if failed else "ready")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
