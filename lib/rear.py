#!/usr/bin/env python3
"""A second screen for the back seat: what a child may see, and nothing else.

An old phone or tablet joins the car's hotspot (or Tailscale), scans a QR code
and gets share/rear.html: a few big friendly cards -- how long until we get
there, the music, calming visuals, how warm it is in here, and a button that
quietly asks the driver for a break. It is for a passenger who cannot read a
gauge and must not be able to find one.

    omacar rear pair         print the URL (and a QR code) with a rear-only token
    omacar rear revoke       forget the token; every paired screen stops at once
    omacar rear serve        the rear server (what omacar-rear.service runs)
    omacar rear music on|off let the rear screen skip tracks (default: off)
    omacar rear status       what is paired, what is allowed
    omacar rear sim          a Simulated song that can skip, for a look

THE SECURITY MODEL IS A SEPARATE DOOR, NOT A SMALLER KEY.

The rear screen is served by `serve.py --rear`, a different process on a
different port from the cockpit, and in that mode lib/api.py is never imported
at all: a request is answered from this file or refused. Three layers:

  * the token. Its own file (rear-token), not the cockpit's. The cockpit's
    token does not open this door and this token does not open that one.
  * an allow-list of exact (method, path) pairs, REAR_GET / REAR_POST below.
    Anything else is 403. There is no wildcard and no prefix match, so a route
    added to the app later is out of the rear screen until someone writes it
    in here. test/rear_test.py walks every route the app has and holds this.
  * what the allowed routes return. /api/rear/state is built here, field by
    field, from four sources. No camera, no diagnostics, no Work, no settings,
    no location, no history, no names: the rear page cannot be sent what the
    rear server never reads.

The music buttons are a second, separate switch (config rear.json,
"music_control", default false). While it is off the music route is not in the
allow-list at all, so it 403s like any other.

HONESTY. "How long?" is an ETA only when navigation has published one in the
last two minutes (state navigation.json); with no navigation and no location it
is the time the car has been driving, and says so. It is never a guess. A
cabin thermometer that is absent is a card that is absent, not a 0. A simulator
labels itself on the screen.
"""

import hmac
import json
import math
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")


def _p(name, config=False):
    return os.path.join(CONFIG_DIR if config else STATE_DIR, name)


PORT = 7571
COOKIE = "omacar_rear"

# ---- the allow-list ----------------------------------------------------------
#
# Exact paths. A GET is a page or the state document; a POST is something a
# child may do. The music route is only reachable while the owner allows it.

REAR_STATIC = {
    "/rear.html": ("rear.html", "text/html; charset=utf-8"),
    "/css/rear.css": ("css/rear.css", "text/css; charset=utf-8"),
    "/js/rear.js": ("js/rear.js", "text/javascript; charset=utf-8"),
    "/js/rearlogic.js": ("js/rearlogic.js", "text/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
REAR_GET = frozenset(REAR_STATIC) | {"/api/rear/state"}
REAR_POST = frozenset({"/api/rear/ask"})
REAR_POST_MUSIC = frozenset({"/api/rear/music"})   # only while music_control is on


def route_allowed(method, path, cfg=None):
    """True only for an exact (method, path) the rear screen is given."""
    cfg = config() if cfg is None else cfg
    if method in ("GET", "HEAD"):
        return path in REAR_GET
    if method == "POST":
        if path in REAR_POST:
            return True
        return bool(cfg.get("music_control")) and path in REAR_POST_MUSIC
    return False


# ---- config ------------------------------------------------------------------

DEFAULTS = {"music_control": False}


def config():
    try:
        with open(_p("rear.json", True), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict) and raw.get("music_control") is True:
        cfg["music_control"] = True      # only a real `true` turns it on
    return cfg


def set_music_control(on):
    path = _p("rear.json", True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = config()
    cfg["music_control"] = bool(on)
    _write(path, cfg)
    return cfg


# ---- the token ---------------------------------------------------------------

def _token_file():
    return _p("rear-token")


def token():
    """The current rear token, or None when nothing is paired."""
    try:
        with open(_token_file(), encoding="utf-8") as f:
            t = f.read().strip()
    except OSError:
        return None
    return t or None


def pair(new=False):
    """The rear token: the existing one, or a fresh one (always fresh with new)."""
    cur = None if new else token()
    if cur:
        return cur
    t = secrets.token_urlsafe(18)
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(_token_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t + "\n")
    return t


def revoke():
    """Forget the token. True if there was one."""
    try:
        os.unlink(_token_file())
        return True
    except OSError:
        return False


def valid(given):
    cur = token()
    if not cur or not given:
        return False
    return hmac.compare_digest(str(given), cur)


def token_from(authorization, query, cookie):
    """Whatever token the request carries: Bearer, ?k=, or the cookie. Each is
    tried; the first that is valid wins, so a stale cookie cannot lock out a
    fresh QR."""
    from urllib.parse import parse_qs
    tries = []
    if authorization and authorization.startswith("Bearer "):
        tries.append(authorization[7:])
    k = (parse_qs(query or "").get("k") or [""])[0]
    if k:
        tries.append(k)
    for part in (cookie or "").split(";"):
        name, _, val = part.strip().partition("=")
        if name == COOKIE and val:
            tries.append(val)
    for t in tries:
        if valid(t):
            return t
    return None


# ---- small files -------------------------------------------------------------

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


# ---- how long ----------------------------------------------------------------

NAV_FRESH = 120          # s: navigation's word on the ETA goes stale after this
DRIVE_GAP = 180          # s without a sample: the car stopped being driven


def drive_start(times_desc, now, gap=DRIVE_GAP):
    """When this drive began, from sample times newest-first; None when the car
    is not being driven now (no sample inside `gap`). A hole longer than `gap`
    ends the drive: a lunch stop is the start of another one."""
    if not times_desc:
        return None
    newest = _num(times_desc[0])
    if newest is None or now - newest > gap:
        return None
    start = newest
    for t in times_desc[1:]:
        t = _num(t)
        if t is None:
            continue
        if start - t > gap:
            break
        start = t
    return start


def how_long(now, nav, started, simulated=False):
    """The "How long?" card. Only ever one of three honest things:

      eta      navigation published an arrival time in the last NAV_FRESH s
      elapsed  no navigation: how long the car has been driving
      unknown  neither -- and it says so rather than showing a number
    """
    eta_at = _num((nav or {}).get("eta_at"))
    nav_at = _num((nav or {}).get("at"))
    if eta_at is not None and nav_at is not None and 0 <= now - nav_at <= NAV_FRESH:
        left = eta_at - now
        if left < 24 * 3600:
            return {"kind": "eta", "minutes": max(0, int(math.ceil(left / 60.0))),
                    "simulated": bool(simulated)}
    if started is not None:
        return {"kind": "elapsed", "minutes": max(0, int((now - started) // 60)),
                "simulated": bool(simulated)}
    return {"kind": "unknown", "simulated": False}


# ---- the music ---------------------------------------------------------------
#
# The player lives in the front screen's browser (share/js/radio.js), so the
# front page publishes what is playing (POST /api/rear/nowplaying, loopback
# only) and the rear server reads the file. A skip goes the other way, as a
# file the front page collects. Nothing here touches audio.

MUSIC_FRESH = 45         # s: a front page that has stopped publishing is not playing
CMD_FRESH = 20           # s: a skip nobody collected is dropped, not played late
CMD_GAP = 1.0            # s between skips


def _clip(s, n):
    return " ".join(str(s or "").split())[:n]


def publish_music(data, now=None):
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        raise ValueError("now-playing must be an object")
    doc = {"at": now, "playing": data.get("playing") is True,
           "title": _clip(data.get("title"), 90), "artist": _clip(data.get("artist"), 90),
           "can_skip": data.get("can_skip") is True,
           "source": "Simulated" if data.get("source") == "Simulated" else "live"}
    _write(_p("rear-music.json"), doc)
    return doc


def music(now, cfg):
    doc = _read(_p("rear-music.json"))
    at = _num((doc or {}).get("at"))
    if not doc or at is None or now - at > MUSIC_FRESH or not doc.get("title") and not doc.get("playing"):
        return {"state": "off", "can_skip": False}
    can = bool(cfg.get("music_control")) and doc.get("can_skip") is True
    return {"state": "playing" if doc.get("playing") else "paused",
            "title": doc.get("title") or "", "artist": doc.get("artist") or "",
            "can_skip": can, "simulated": doc.get("source") == "Simulated"}


def music_command(cmd, now=None):
    """A skip from the rear screen. (status, payload)."""
    now = time.time() if now is None else now
    cfg = config()
    if not cfg.get("music_control"):
        return 403, {"error": "the driver has not turned this on"}
    if cmd not in ("next", "previous"):
        return 400, {"error": "next or previous"}
    m = music(now, cfg)
    if m["state"] == "off" or not m["can_skip"]:
        return 409, {"error": "this music cannot skip"}
    last = _read(_p("rear-music-cmd.json")) or {}
    if now - (_num(last.get("at")) or 0) < CMD_GAP:
        return 429, {"error": "one at a time"}
    doc = {"id": int(last.get("id") or 0) + 1, "cmd": cmd, "at": now, "done": False}
    _write(_p("rear-music-cmd.json"), doc)
    return 200, {"ok": True}


def pending_music_command(now=None):
    now = time.time() if now is None else now
    doc = _read(_p("rear-music-cmd.json"))
    if (not doc or doc.get("done") or doc.get("cmd") not in ("next", "previous")
            or now - (_num(doc.get("at")) or 0) > CMD_FRESH):
        return None
    if not config().get("music_control"):
        return None
    return {"id": doc["id"], "cmd": doc["cmd"]}


def finish_music_command(cid):
    doc = _read(_p("rear-music-cmd.json"))
    if doc and doc.get("id") == cid:
        doc["done"] = True
        _write(_p("rear-music-cmd.json"), doc)
        return True
    return False


# ---- ask the driver ----------------------------------------------------------

ASK_EVERY = 120          # s between notes, across every rear screen
NOTE_LIFE = 300          # s a note waits on the front screen before it lapses
WANTS = {"break": "a break", "snack": "a snack", "drink": "a drink",
         "bathroom": "the bathroom"}


def alert_active(now=None, sources=None):
    """Is anything on the front screen that a note must not sit on top of?

    The radar detector's alert, a drowsy-mode alert in the last minute, or a
    watchdog alert in the last minute. A source that exists but cannot be read
    counts as an alert: unknown is not clear."""
    now = time.time() if now is None else now
    src = sources or _Sources()
    try:
        return bool(src.alert(now))
    except Exception:                                      # noqa: BLE001
        return True


def ask_status(now, sources=None):
    last = _read(_p("rear-ask.json")) or {}
    wait = max(0, int(math.ceil(ASK_EVERY - (now - (_num(last.get("at")) or 0)))))
    busy = alert_active(now, sources)
    return {"ok": wait == 0 and not busy, "wait": wait, "busy": busy,
            "choices": [{"id": k, "label": v} for k, v in WANTS.items()]}


def ask(what, now=None, sources=None):
    """A child asked for something. (status, payload). Silent by construction:
    this writes a file; the front screen draws a card and makes no sound."""
    now = time.time() if now is None else now
    if not isinstance(what, str) or what not in WANTS:
        return 400, {"error": "pick one of the buttons"}
    st = ask_status(now, sources)
    if st["wait"]:
        return 429, {"error": "already asked", "wait": st["wait"]}
    if st["busy"]:
        # Does not start the clock: a note that was not shown was not sent.
        return 409, {"error": "the driver is busy", "busy": True}
    _write(_p("rear-ask.json"), {"at": now})
    prev = _read(_p("rear-note.json")) or {}
    _write(_p("rear-note.json"), {"id": int(prev.get("id") or 0) + 1, "what": what,
                                  "label": WANTS[what], "at": now, "dismissed": False})
    return 200, {"ok": True, "wait": ASK_EVERY}


def note_for_front(now=None):
    """The note the front screen should draw, or None."""
    now = time.time() if now is None else now
    n = _read(_p("rear-note.json"))
    if not n or n.get("dismissed") or now - (_num(n.get("at")) or 0) > NOTE_LIFE:
        return None
    if n.get("what") not in WANTS:
        return None
    return {"id": n["id"], "what": n["what"], "label": WANTS[n["what"]],
            "age": int(now - n["at"])}


def dismiss_note(nid):
    n = _read(_p("rear-note.json"))
    if n and n.get("id") == nid:
        n["dismissed"] = True
        _write(_p("rear-note.json"), n)
        return True
    return False


# ---- where the numbers come from ---------------------------------------------

class _Sources:
    """The four things the rear screen reads, each in its own method so a test
    can swap one. Every one of them returns None for "not there"."""

    def nav(self):
        return _read(_p("navigation.json"))

    def drive(self, now):
        """(start, simulated): when this drive began, from the sample book."""
        import records
        simulated = False
        try:
            simulated = bool(records.live().get("simulated"))
        except Exception:                                  # noqa: BLE001
            pass
        db = records.connect()
        if db is None:
            return None, simulated
        try:
            cur = db.execute("SELECT t FROM samples WHERE t >= ? ORDER BY t DESC LIMIT 40000",
                             (now - 12 * 3600,))
            times = [r[0] for r in cur]
        except Exception:                                  # noqa: BLE001
            return None, simulated
        finally:
            db.close()
        return drive_start(times, now), simulated

    def cabin(self):
        """(celsius, simulated) of the cabin thermometer, or None."""
        import envnode
        e = envnode.read_public()
        c = e.get("cabin") if isinstance(e, dict) else None
        if not c or c.get("state") != "live":
            return None
        t = _num((c.get("values") or {}).get("t_c"))
        return None if t is None else (t, c.get("source") == "sim")

    def unit(self):
        import records
        return records.units_for()["system"]

    def alert(self, now):
        import v1
        st = v1.read_public()
        if st.get("alerts") and not st.get("stale"):
            return True
        for kind in ("drowsy", "alert"):
            if self._recent_record(kind, now, 60):
                return True
        return False

    @staticmethod
    def _recent_record(kind, now, secs):
        import records
        db = records.connect()
        if db is None:
            return False
        try:
            if not records.has(db, "records"):
                return False
            row = db.execute("SELECT MAX(at) FROM records WHERE kind = ?", (kind,)).fetchone()
        finally:
            db.close()
        at = _num(row[0]) if row else None
        return at is not None and 0 <= now - at <= secs


def comfort(c):
    """A word a child can read for a temperature in Celsius."""
    if c is None:
        return None
    if c < 16:
        return "chilly"
    if c < 20:
        return "cool"
    if c <= 25:
        return "just right"
    if c <= 29:
        return "warm"
    return "hot"


def state_public(now=None, sources=None, cfg=None):
    """Everything /api/rear/state says. Built field by field: if a thing is not
    written here, the rear page cannot see it."""
    now = time.time() if now is None else now
    src = sources or _Sources()
    cfg = config() if cfg is None else cfg
    try:
        started, sim = src.drive(now)
    except Exception:                                      # noqa: BLE001
        started, sim = None, False
    try:
        nav = src.nav()
    except Exception:                                      # noqa: BLE001
        nav = None
    out = {"at": now, "how_long": how_long(now, nav, started, sim),
           "music": music(now, cfg), "control": {"music": bool(cfg.get("music_control"))},
           "cabin": None, "ask": ask_status(now, src)}
    try:
        cab = src.cabin()
    except Exception:                                      # noqa: BLE001
        cab = None
    if cab:
        c, csim = cab
        imperial = True
        try:
            imperial = src.unit() == "imperial"
        except Exception:                                  # noqa: BLE001
            pass
        out["cabin"] = {"degrees": int(round(c * 9 / 5 + 32)) if imperial else int(round(c)),
                        "unit": "F" if imperial else "C", "word": comfort(c), "simulated": csim}
    # No name, no place, no VIN: the rear screen is told none of them.
    return out


# ---- the requests the rear server answers -------------------------------------

def static_file(share_dir, path):
    """(bytes, content-type) for an allow-listed static path, else None."""
    ent = REAR_STATIC.get(path)
    if not ent:
        return None
    try:
        with open(os.path.join(share_dir, ent[0]), "rb") as f:
            return f.read(), ent[1]
    except OSError:
        return None


def handle(method, path, body, now=None, sources=None):
    """(status, payload) for an allow-listed API route; the caller has already
    checked the token and route_allowed()."""
    now = time.time() if now is None else now
    if method in ("GET", "HEAD") and path == "/api/rear/state":
        return 200, state_public(now, sources)
    if method == "POST":
        try:
            data = json.loads(body or "{}")
        except ValueError:
            return 400, {"error": "bad request"}
        if not isinstance(data, dict):
            return 400, {"error": "bad request"}
        if path == "/api/rear/ask":
            return ask(data.get("what"), now, sources)
        if path == "/api/rear/music":
            return music_command(data.get("cmd"), now)
    return 404, {"error": "not here"}


# ---- the front screen's side (mounted in lib/api.py, loopback only) -----------

def front_get(now=None):
    now = time.time() if now is None else now
    return {"note": note_for_front(now), "alert": alert_active(now),
            "music_cmd": pending_music_command(now), "paired": token() is not None}


def front_post(path, data, now=None):
    """(status, payload) for the front page's own posts."""
    if path == "/api/rear/dismiss":
        return 200, {"ok": dismiss_note(data.get("id"))}
    if path == "/api/rear/nowplaying":
        try:
            publish_music(data, now)
        except ValueError as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True}
    if path == "/api/rear/music/done":
        return 200, {"ok": finish_music_command(data.get("id"))}
    return 404, {"error": "no such endpoint"}


# ---- command line ------------------------------------------------------------

def _ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def url(host=None, new=False):
    return f"http://{host or _ip()}:{PORT}/rear.html?k={pair(new)}"


def _qr(text):
    import shutil
    import subprocess
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "UTF8", "-m", "2", text], check=False)
        return True
    return False


SIM_SONGS = ("Wheels on the Bus", "Twinkle Twinkle", "Baby Shark")


def sim(seconds=None):
    """A song that can skip, published as the front screen would. It says
    Simulated on the rear screen, every time. (The drive and the cabin
    thermometer have their own simulators: `omacar sim`, `omacar env sim`.)"""
    start, i = time.time(), 0
    print("  rear sim: publishing a Simulated song until Ctrl-C")
    while seconds is None or time.time() - start < seconds:
        publish_music({"playing": True, "can_skip": True, "source": "Simulated",
                       "title": SIM_SONGS[i % len(SIM_SONGS)], "artist": "Simulated"})
        cmd = pending_music_command()
        if cmd:
            i += 1 if cmd["cmd"] == "next" else -1
            finish_music_command(cmd["id"])
        time.sleep(1)


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "pair":
        new = "--new" in argv
        u = url(new=new)
        print()
        print("  Open this on the back-seat phone or tablet (same hotspot or Tailscale):")
        print()
        print(f"    {u}")
        print()
        if not _qr(u):
            print("  (install qrencode for a scannable code)")
        print("  It shows only the cards in doc/rear-screen.md. It cannot see the car,")
        print("  the cameras or anything personal. `omacar rear revoke` ends it.")
        print("  The rear server must be running: `omacar rear serve`")
        print()
        return 0
    if what == "revoke":
        print("  rear token revoked; paired screens stop at their next request."
              if revoke() else "  nothing was paired.")
        return 0
    if what == "serve":
        os.execv(sys.executable, [sys.executable,
                                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "serve.py"),
                                  str(PORT), os.path.join(os.path.dirname(os.path.dirname(
                                      os.path.abspath(__file__))), "share"),
                                  "--host", "0.0.0.0", "--rear"])
    if what == "music":
        if len(argv) < 2 or argv[1] not in ("on", "off"):
            print("  omacar rear music on|off", file=sys.stderr)
            return 2
        cfg = set_music_control(argv[1] == "on")
        print(f"  rear screen may skip tracks: {'yes' if cfg['music_control'] else 'no'}")
        return 0
    if what == "status":
        print(f"  paired:        {'yes' if token() else 'no'}")
        print(f"  skip tracks:   {'allowed' if config()['music_control'] else 'off (default)'}")
        print(f"  rear port:     {PORT}")
        print(json.dumps(state_public(), indent=2))
        return 0
    if what == "sim":
        try:
            sim(float(argv[1]) if len(argv) > 1 else None)
        except KeyboardInterrupt:
            pass
        return 0
    print(f"  omacar rear: no such thing as '{what}'.\n"
          "  pair | revoke | serve | music on|off | status | sim", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
