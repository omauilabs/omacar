#!/usr/bin/env python3
"""`omacar demo check`: is this machine ready to give the meetup demo?

    python3 lib/democheck.py [--private DIR]

One line per check, then "ready" or "not ready: <the checks that failed>",
and exit 0 only when ready. It READS ONLY: nothing is written, started or
stopped, and nothing is played.

    songs        Omarchy Radio's seven songs, each the size demo/data/media-pins.json
                 pins, as the station's playlist.json names them
    drive, map   demo/drive.json and demo/map.json, whole
    voice        a wav for every line in demo/data/voice.json
    clips        the cameras' footage: front, rear, cabin and cabin-drowsy, in
                 demo/clips or where OMACAR_DEMO_CLIPS says, as the camera feed reads
    logo         omacar-logo.png, the top bar's
    car picture  demo/crz-home.png, Home's (tools/demo_carpic.py makes it)
    volume pin   the LIVE app's `omacar audio on` is off: while it is on, the
                 live page under the demo pushes the speakers to 100% every 30 s
    kiosk        the live kiosk is running underneath, as it should be
    demo server  when the demo is on, its server answers /demo.html

The media are under share/assets/private/ (git-ignored), or DIR. The volume pin
is read from the REAL settings folder (~/.config/omarchy), never the demo's:
inside `omacar demo`'s environment XDG_CONFIG_HOME points at the demo's own.
"""

import argparse
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
# The live kiosk's Chromium, by its profile (bin/omacar's KIOSK_PROFILE). The
# demo's window has its own profile, under the demo's folder, and does not match.
KIOSK = "user-data-dir=.*/omacar/kiosk-profile"
DEMO_MARK = "omacar-demo"


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


def clips(private):
    # Where the camera feed reads them: bin/omacar passes OMACAR_DEMO_CLIPS to
    # `cams.py demo --from` when it is set, and the private tree's otherwise.
    here = os.environ.get("OMACAR_DEMO_CLIPS") or os.path.join(private, "demo", "clips")
    missing = [r for r in CLIP_ROLES
               if not (os.path.isfile(os.path.join(here, r + ".mp4"))
                       and os.path.getsize(os.path.join(here, r + ".mp4")) > 0)]
    if missing:
        return False, "stock/owner clips missing: " + ", ".join(missing) + f" (in {here})"
    return True, ", ".join(CLIP_ROLES) + f" (in {here})"


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
        r = subprocess.run(["pgrep", "-f", KIOSK], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not ask pgrep: {e}"
    if r.returncode == 0 and r.stdout.strip():
        return True, "running (pid " + ", ".join(r.stdout.split()) + ")"
    return False, "not running: omacar kiosk"


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
        ("voice", *voice(private)),
        ("clips", *clips(private)),
        ("logo", *picture(private, "omacar-logo.png")),
        ("car picture", *picture(private, os.path.join("demo", "crz-home.png"),
                                 " (tools/demo_carpic.py makes it)")),
        ("volume pin", *volume_pin()),
        ("kiosk", *kiosk()),
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
        print(f"  {mark}  {name:<12} {what}")
    failed = [name for name, ok, _ in results if ok is False]
    print()
    print("not ready: " + ", ".join(failed) if failed else "ready")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
