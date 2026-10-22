"""Accessibility: OmaCar follows OmaAccess, and has its own settings when it is absent.

OmaAccess (omauilabs/omaaccess) is the Omarchy accessibility plugin on the same
tablet. It owns the person's settings; OmaCar never keeps a second copy of them.
What it exposes, read from its main branch (0.23.1):

  omaaccess status --json   the EFFECTIVE state, after profiles and defaults:
                            {"settings": {"<key>": {"value": "on", "source": ...}}, ...}
  omaaccess set KEY VALUE   change one setting (the same command its panel runs)
  omaaccess speak TEXT      its voice: Piper if set up, else spd-say, else espeak-ng
  ~/.config/omaaccess/state its overrides file. There is no D-Bus signal; the
                            file's mtime changing is the only "something changed".

So `read()` re-runs `status --json` only when that mtime moves (or after REFRESH
seconds, for profile changes that land elsewhere), and the page polls /api/access.

The colour filters, invert, tint, dim, zoom and the magnifying glass happen in
the Hyprland screen shader, under the page. OmaCar does not redo them; it only
picks its colour-blind chart palette to match the filter (lib/separation.py).

When OmaAccess is not installed (the Mac, a fresh tablet), the same settings live
in ~/.config/omacar/access.json and the Accessibility page writes them there.

Speech obeys the house rule: no sound overnight with the car off, because a
child sleeps near the tablet. Only urgency "safety" (the child alert, rapid tyre
loss) speaks then. And when the screen reader (Orca) is on, OmaCar does not speak
for itself: the page's live regions are read by Orca, and two voices at once
are worse than one.
"""

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONF_DIR = os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
LOCAL = os.path.join(CONF_DIR, "access.json")
OA_STATE = os.path.join(os.path.expanduser(
    os.environ.get("OMAACCESS_CONFIG") or
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omaaccess"), "state")
REFRESH = 30          # s: re-read OmaAccess even if its file has not moved
SPEAK_MAX = 280       # characters: an announcement, not a document

# OmaCar's setting -> (OmaAccess key, kind, OmaCar default). The kinds:
#   bool   OmaAccess "on"/"off"
#   size   OmaAccess textSize in px (its default 12) -> a page scale 1.0..2.0
#   enum   a string from a fixed set
SETTINGS = {
    "scale":    ("textSize", "size", 1.0),        # text and controls bigger
    "contrast": ("contrast", "bool", False),      # solid panels, stronger edges
    "still":    ("still", "bool", False),         # no animation at all
    "dyslexia": ("dyslexiaFont", "bool", False),  # OpenDyslexic for the page
    "big":      ("bigTargets", "bool", False),    # 64 px touch targets
    "flash":    ("alerts", "bool", False),        # every chime also flashes
    "captions": ("captions", "bool", False),      # every spoken line also shown
    "reader":   ("sr", "bool", False),            # Orca is running
    "speak":    ("speakAlerts", "bool", False),   # read alerts aloud
    "filter":   ("colorFilter", "enum", "none"),  # which colour-blind palette
    "lang":     ("language", "enum", "auto"),
}
ENUMS = {
    "filter": ("none", "deutan", "protan", "tritan", "grayscale"),
    "lang": ("auto", "en", "es"),
}
TEXT_PX = 12          # OmaAccess's textSize default: scale 1.0
SCALES = (1.0, 1.15, 1.3, 1.5, 1.75, 2.0)

_cache = {"mtime": None, "at": 0.0, "status": None}


# ---- pure: what the settings are ----------------------------------------------------

def _scale_from_px(px):
    try:
        s = float(px) / TEXT_PX
    except (TypeError, ValueError):
        return 1.0
    return round(min(2.0, max(1.0, s)), 2)


def from_omaaccess(status):
    """OmaAccess's status JSON -> OmaCar's settings, or None if it is not one."""
    if not isinstance(status, dict) or not isinstance(status.get("settings"), dict):
        return None
    src = status["settings"]
    out = {}
    for name, (key, kind, default) in SETTINGS.items():
        cell = src.get(key)
        raw = cell.get("value") if isinstance(cell, dict) else cell
        if kind == "bool":
            out[name] = raw == "on" if isinstance(raw, str) else default
        elif kind == "size":
            sysd = status.get("system") if isinstance(status.get("system"), dict) else {}
            out[name] = _scale_from_px(raw if raw is not None else sysd.get("textSize", TEXT_PX))
        else:
            out[name] = raw if raw in ENUMS[name] else default
    return out


def clean(name, value):
    """A value for one OmaCar setting, or ValueError saying what is allowed."""
    if name not in SETTINGS:
        raise ValueError(f"no setting called {name!r}")
    kind, default = SETTINGS[name][1], SETTINGS[name][2]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if value in ("on", "off"):
            return value == "on"
        raise ValueError(f"{name} is on or off")
    if kind == "size":
        try:
            v = round(float(value), 2)
        except (TypeError, ValueError):
            raise ValueError(f"{name} is a number from 1 to 2") from None
        if not 1.0 <= v <= 2.0:
            raise ValueError(f"{name} is a number from 1 to 2")
        return v
    if value not in ENUMS[name]:
        raise ValueError(f"{name} is one of {', '.join(ENUMS[name])}")
    return value


def from_local(doc):
    out = {k: v[2] for k, v in SETTINGS.items()}
    for k, v in (doc or {}).items():
        try:
            out[k] = clean(k, v)
        except ValueError:
            pass
    return out


def to_omaaccess(name, value):
    """(key, value) for `omaaccess set`, from an OmaCar setting already cleaned."""
    key, kind, _ = SETTINGS[name]
    if kind == "bool":
        return key, "on" if value else "off"
    if kind == "size":
        return key, str(int(round(value * TEXT_PX)))
    return key, value


def speech_gate(urgency, settings, minute_of_day, car_on, quiet_cfg=None):
    """(may speak, why not). The rules, in order:
    - the screen reader is on: Orca reads the page; OmaCar stays quiet;
    - the person did not ask for alerts to be read aloud: nothing is said;
    - overnight with the car off: only a safety alert speaks (a child sleeps here)."""
    import intent
    if settings.get("reader"):
        return False, "the screen reader is reading the page"
    if not settings.get("speak"):
        return False, "reading alerts aloud is off"
    if urgency != "safety" and not intent.may_listen({"quiet": quiet_cfg or {}}, minute_of_day, car_on):
        return False, "quiet hours, and the car is off"
    return True, ""


# ---- impure: where the settings come from --------------------------------------------

def _omaaccess():
    return shutil.which("omaaccess")


def _status(run=subprocess.run, now=None):
    """OmaAccess's status JSON, re-read when its state file moves; None if absent."""
    if not _omaaccess():
        return None
    now = time.time() if now is None else now
    try:
        mtime = os.stat(OA_STATE).st_mtime
    except OSError:
        mtime = None
    if (_cache["status"] is not None and mtime == _cache["mtime"]
            and now - _cache["at"] < REFRESH):
        return _cache["status"]
    try:
        r = run(["omaaccess", "status", "--json"], capture_output=True, text=True,
                timeout=4, check=False)
        status = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        status = None
    _cache.update(mtime=mtime, at=now, status=status)
    return status


def _local():
    try:
        with open(LOCAL, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def read_public():
    status = _status()
    settings = from_omaaccess(status)
    if settings is not None:
        return {"source": "omaaccess", "version": status.get("version"),
                "summary": status.get("summary"), "settings": settings}
    return {"source": "omacar", "settings": from_local(_local())}


def write(name, value, run=subprocess.run):
    """Change one setting where it lives: through OmaAccess, or OmaCar's own file."""
    value = clean(name, value)
    if _omaaccess():
        key, v = to_omaaccess(name, value)
        r = run(["omaaccess", "set", key, v], capture_output=True, text=True,
                timeout=10, check=False)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "omaaccess refused").strip()[:200])
        _cache["status"] = None
        return read_public()
    doc = _local()
    doc[name] = value
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = LOCAL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, LOCAL)
    return read_public()


def speak(text, urgency="info", run=subprocess.Popen, now=None):
    """Say one line in OmaAccess's voice if the gate allows. Returns {spoken, why}."""
    text = " ".join(str(text or "").split())[:SPEAK_MAX]
    if not text:
        return {"spoken": False, "why": "nothing to say"}
    import voice
    settings = read_public()["settings"]
    quiet = {}
    try:
        quiet = voice.load_config().get("quiet") or {}
    except Exception:                                            # noqa: BLE001
        pass
    ok, why = speech_gate(urgency, settings, voice.minute_now(now), voice.car_is_on(), quiet)
    if not ok:
        return {"spoken": False, "why": why}
    argv = (["omaaccess", "speak", text] if _omaaccess() else
            ["spd-say", "-P", "message", text] if shutil.which("spd-say") else None)
    if not argv:
        return {"spoken": False, "why": "no voice installed (OmaAccess or speech-dispatcher)"}
    try:
        run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return {"spoken": False, "why": str(e)}
    return {"spoken": True, "why": ""}


def main(argv):
    if len(argv) < 2 or argv[1] == "status":
        print(json.dumps(read_public(), indent=1))
        return 0
    if argv[1] == "set" and len(argv) == 4:
        try:
            print(json.dumps(write(argv[2], argv[3]), indent=1))
            return 0
        except (ValueError, RuntimeError) as e:
            print(f"omacar access: {e}", file=sys.stderr)
            return 2
    print("usage: omacar access [status | set NAME VALUE]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
