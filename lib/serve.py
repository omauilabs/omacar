#!/usr/bin/env python3
"""The server behind the OmaCar workshop.

Serves share/ over http (never file:// — a null origin partitions localStorage
and breaks fetch against our own /api) and hands every /api request to api.py.

Deliberately does no OBD work of its own. One process owns the serial
connection, and it is the daemon.

    python3 serve.py <port> <share-dir> [--host H] [--token T] [--control]
    python3 serve.py <port> <share-dir> --demo <demo-dir>    the meetup demo's
                                         server, which only `omacar demo on` starts

Two modes, and the difference between them is the whole security model.

  **Loopback** (the default). Bound to 127.0.0.1, full privileges. The Host
  header is still checked, because a page on any website can point a fetch at
  a hostname that resolves to 127.0.0.1, and this API will happily clear a
  car's trouble codes.

  **Cockpit** (`--host 0.0.0.0`). For putting the gauge on a tablet that
  cannot run Omarchy — a Samsung tablet, an old iPad, a phone. Reachable from
  the local network, and therefore:

    * a token is required on every single request, including the page itself;
    * every write is refused — no clearing codes, no commanding actuators, no
      spending anyone's AI budget — unless `--control` was passed deliberately;
    * the token is not a password. It stops the other devices on a car's
      hotspot from stumbling in. Anyone who can read your Wi-Fi traffic can
      read this, because it is plain HTTP on a LAN, and pretending otherwise
      would be worse than saying so.
"""
import faulthandler
import hmac
import json
import os
import re
import signal
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402

MARK = "omacar-server"
MAX_BODY = 1 << 20

# Set from the command line. Empty token means loopback-only, full privileges.
TOKEN = ""
ALLOW_CONTROL = True
LOOPBACK_ONLY = True

# The only writes a cockpit is ever allowed, even with --control: things that
# change what you are looking at, never what the car is doing.
COCKPIT_WRITES = {"/api/units", "/api/roadcams/pins"}


# ---- the meetup demo (doc/design/2026-09-30-meetup-demo.md, §2 and §3) --------
#
# `serve.py 7580 share --demo demo` is the demo's server, and only `omacar demo
# on` starts it. The owner's constraint, verbatim: "the demo mode should not in
# any way shape or form affect the live running app when in the car, or affect
# the real data. The demo must be siloed data". Three things here hold the
# server to it:
#
#   it refuses to start over anything but the demo's own folders (below);
#   it answers only the /api/ routes in DEMO_ROUTES, and refuses the rest
#     before any handler runs;
#   demo code and media are served by it alone, from inside their own folders.
#
# The live server never passes --demo, and answers 404 for all of it.

DEMO = None          # the demo's code folder (repo/demo) with --demo, else None
DEMO_MEDIA = None    # share/assets/private, beside the share root served

ALLOW = "allow"      # GET and POST: reads or writes the demo's own state, nothing else
READ = "read"        # GET only: reads the demo's own state; a POST is refused
REFUSE = "refuse"    # 403 {"error": "not in the demo"} on GET, POST and HEAD
# A dict is a STUB: that JSON on GET and POST, and the handler never runs. For
# the calls the live page makes in the background, so it stays quiet.

NOT_IN_DEMO = {"error": "not in the demo"}

# Every /api/ path the live page or the demo page can call (share/js/core.js's
# `api` object, `grep -rn '"/api/' share/js demo`) and every one the server
# answers, each with its verdict and why. A path ending in /* covers everything
# under it; an exact entry wins over one. Anything not here is refused, so a
# route added to the app later is out of the demo until somebody decides it is
# safe. test/demoserve_test.py holds the table to that, and names the routes
# that must never be let through whatever this says.
DEMO_ROUTES = {
    # --- ALLOW: the demo's own state and config, written by the demo's screens
    # The cue the menu, the keys and the tour send: demo-cue.json in the demo's
    # state, which only lib/demoworld.py reads. Served here, not by api.py.
    "/api/demo/cue": ALLOW,
    # Home's layout, in the demo's config. The Agent's Apply writes it.
    "/api/home": ALLOW,
    # Which screen to show, in the demo's state. `omacar demo tour` asks here,
    # and the page publishes its list of screens here.
    "/api/screen": ALLOW,
    # The top bar's day/night button. The demo's own theme store; the desktop
    # theme itself is only ever read.
    "/api/themes": ALLOW,
    # The Cameras view and Home's dashcam card: the demo's clips (OMACAR_VIDEOS)
    # and the demo feed's pictures (its own XDG_RUNTIME_DIR). The overview lists
    # /dev/v4l/by-id and never opens a device; mark and lock copy the demo's own
    # clips into the demo's own locked/.
    "/api/cams": ALLOW,
    "/api/cams/*": ALLOW,
    # Drowsy mode: its settings in the demo's config, its events and measures in
    # the demo's state.
    "/api/drowsy": ALLOW,
    "/api/drowsy/event": ALLOW,
    "/api/drowsy/log": ALLOW,
    # Road cameras: Caltrans' public list and stills, cached in the demo's
    # state (seeded from the real cache by `demo on`), and the pins in the
    # demo's config.
    "/api/roadcams": ALLOW,
    "/api/roadcams/*": ALLOW,

    # --- READ: the demo's own state, read; a write to the same path is refused
    # live.json, which the demo world writes at 5 Hz.
    "/api/live": READ,
    # The seeded garage: the summary, the history, trips and the records. A
    # POST to /api/snapshot freezes a capture, which the demo has no use for.
    "/api/snapshot": READ,
    "/api/history": READ,
    "/api/trips": READ,
    "/api/records": READ,
    "/api/concerns": READ,
    "/api/snapshots": READ,
    "/api/vehicles": READ,
    "/api/service-history": READ,
    "/api/ima": READ,
    # Pictures, documents, procedures and what was learned, from the demo's
    # state (empty or seeded), and which private pictures exist (share/assets).
    "/api/photos": READ,
    "/api/documents": READ,
    "/api/procedures": READ,
    "/api/resets": READ,
    "/api/learned": READ,
    "/api/assets": READ,
    # The demo's own nursery.json, which never exists: the real one is in the
    # real state, which this server cannot see.
    "/api/nursery": READ,
    # The drive-mode label, the drive layout and the tier, read at boot.
    # Marking a mode, saving a layout or changing the tier is refused.
    "/api/drivemode": READ,
    "/api/drive": READ,
    "/api/mode": READ,
    # The palette: the desktop theme, read-only, so the demo looks like this
    # desktop. Fonts restamp the panel cache, which is in the demo's state.
    "/api/theme": READ,
    "/api/fonts": READ,
    # Plugins installed in the demo's config: none.
    "/api/plugins": READ,

    # --- STUB: background calls, answered without running anything
    # The live page's volume pin (§2.6): it would hold the tablet's speakers at
    # 100% every 30 s. The demo never touches the volume.
    "/api/audio": {"managed": False, "demo": True},
    # The advisor runs the real claude CLI. Boot asks whether it is there, and
    # Home and Vehicle ask for its last answer.
    "/api/ai/available": {"available": False},
    "/api/ai/history": {"records": []},
    # The top bar asks on every load whether the voice assistant exists. "No"
    # keeps its button hidden, so nothing can start it.
    "/api/assistant": {"ok": False, "present": False},

    # --- REFUSE: the car, the adapter, devices, services, the real claude CLI
    # Stops the real omacar-drivelog.service, then drives the adapter.
    "/api/begin": REFUSE,
    # Starts and stops the polling daemon, and looks for its adapter.
    "/api/daemon": REFUSE,
    "/api/adapter": REFUSE,
    # Talk to the car, or arm writes to it.
    "/api/scan": REFUSE,
    "/api/learn": REFUSE,
    "/api/clear": REFUSE,
    "/api/reset": REFUSE,
    "/api/write-mode": REFUSE,
    "/api/write-did": REFUSE,
    "/api/actuate": REFUSE,
    # Saves a stretch of the bus log.
    "/api/record": REFUSE,
    # OCR in a subprocess, then the advisor.
    "/api/document": REFUSE,
    # Photographs, the car's record, the odometer, the service book and the
    # units: settings and records the demo has no reason to change.
    "/api/photo": REFUSE,
    "/api/vehicle": REFUSE,
    "/api/odometer": REFUSE,
    "/api/service": REFUSE,
    "/api/units": REFUSE,
    # The advisor: a job runs the real claude CLI.
    "/api/ai": REFUSE,
    "/api/ai/*": REFUSE,
    # The CarPlay adapter, over USB, and its video. The demo's CarPlay and
    # Android Auto are drawn by the demo.
    "/api/phone": REFUSE,
    "/api/phone/*": REFUSE,
}

# The cues the demo world acts on (lib/demoworld.py). `quiet` is `demo off`'s:
# the world says when, and the page fades its music out.
DEMO_CUES = ("park", "drive", "drowsy", "hard_brake", "restart", "quiet")

# What /demo-media/ serves, by extension.
DEMO_MEDIA_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4",
                    ".json": "application/json", ".png": "image/png",
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def demo_entry(path):
    """The DEMO_ROUTES key that decides `path`, or None when none does."""
    if path in DEMO_ROUTES:
        return path
    best = None
    for key in DEMO_ROUTES:
        if key.endswith("/*") and path.startswith(key[:-1]) and len(path) > len(key) - 1:
            if best is None or len(key) > len(best):
                best = key
    return best


def demo_route(path):
    """ALLOW, READ, REFUSE or "stub" for an /api/ path; REFUSE when unlisted."""
    key = demo_entry(path)
    if key is None:
        return REFUSE
    verdict = DEMO_ROUTES[key]
    return "stub" if isinstance(verdict, dict) else verdict


def is_demo_path(path):
    """A URL only the demo server serves."""
    from urllib.parse import unquote
    p = unquote(path)
    return p == "/demo.html" or p.startswith(("/demo/", "/demo-media/")) \
        or p in ("/demo", "/demo-media")


def demo_file(path):
    """(real path, content type) for a demo URL, or None for a 404.

    The URL is decoded first, so %2e%2e is the .. it spells, and the file must
    resolve (os.path.realpath, which follows every link) to somewhere inside
    the one folder its prefix maps to. A link inside the private tree that
    points out of it is refused like any other way out."""
    from urllib.parse import unquote
    p = unquote(path)
    media = DEMO_MEDIA or ""
    if p == "/demo.html":
        base, name = DEMO, "demo.html"
    elif p.startswith("/demo/"):
        base, name = DEMO, p[len("/demo/"):]
    elif p.startswith("/demo-media/radio/"):
        base, name = os.path.join(media, "omarchy-radio"), p[len("/demo-media/radio/"):]
    elif p == "/demo-media/logo.png":
        base, name = media, "omacar-logo.png"
    elif p.startswith("/demo-media/"):
        base, name = os.path.join(media, "demo"), p[len("/demo-media/"):]
    else:
        return None
    if not base or not name or "\x00" in name:
        return None
    try:
        root = os.path.realpath(base)
        real = os.path.realpath(os.path.join(root, name))
    except (OSError, ValueError):
        return None
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        return None
    ext = os.path.splitext(real)[1].lower()
    if p.startswith("/demo-media/"):
        ctype = DEMO_MEDIA_TYPES.get(ext, "application/octet-stream")
    else:
        import mimetypes
        ctype = {".js": "text/javascript", ".mjs": "text/javascript",
                 ".html": "text/html; charset=utf-8", ".css": "text/css",
                 ".json": "application/json"}.get(ext) \
            or mimetypes.guess_type(real)[0] or "application/octet-stream"
    return real, ctype


# The folders the demo server's handlers read and write through. Each has to
# be the demo's: a path under a folder called omacar-demo (bin/omacar's
# DEMO_ROOT). Unset would mean the real one, because every module falls back to
# the user's own ~/.local/state, ~/.config, ~/Videos/OmaCar or /run/user/UID.
DEMO_ENV_DIRS = ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
                 "OMACAR_STATE", "OMACAR_VIDEOS")


def demo_env_problems(env=None):
    """Why this environment is not the demo's, as a list; empty when it is.

    Checked before the demo server binds, so a `serve.py --demo` run by hand
    from an ordinary shell does not serve the demo page over the real garage,
    the real clips and the real settings, where its allowed writes would land.
    It also needs OMACAR_PORT set to a path that does not exist, which is what
    stops connect.resolve() globbing /dev/ttyUSB* for the real adapter."""
    env = os.environ if env is None else env
    out = []
    for name in DEMO_ENV_DIRS:
        val = env.get(name) or ""
        if not val:
            out.append(f"{name} is not set")
        elif "omacar-demo" not in os.path.abspath(val).split(os.sep):
            out.append(f"{name}={val} is not inside the demo's folder")
    port = env.get("OMACAR_PORT") or ""
    if not port:
        out.append("OMACAR_PORT is not set, so the adapter would be looked for")
    elif os.path.exists(port):
        out.append(f"OMACAR_PORT={port} exists")
    return out


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def end_headers(self):
        """Security headers on EVERYTHING, including static files.

        The API set these; the static handler inherited from
        SimpleHTTPRequestHandler set none, so app.html itself could be framed
        by any page. The Host check does not help there -- a remote page
        framing http://127.0.0.1:<port>/app.html makes the browser send
        Host: 127.0.0.1, which passes. That is clickjacking on an application
        that can clear fault codes and command actuators.
        """
        if not self.headers_already_secured:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.headers_already_secured = True
        # STATIC FILES HAD NO Cache-Control AT ALL.
        #
        # The API sends no-store; SimpleHTTPRequestHandler sends only
        # Last-Modified, and with no explicit policy a browser is free to
        # HEURISTICALLY cache -- typically a tenth of the file's age. So an
        # updated OmaCar served the OLD js modules to a tab that already had
        # them, with no error and nothing to see: the app simply carried on
        # running last week's code. That is a miserable thing to debug, and it
        # cost an hour here before it was recognised as a caching problem
        # rather than a broken change.
        #
        # no-cache, NOT no-store: the file may still be held, it just has to be
        # revalidated. Every unchanged asset is a 304 with no body, so this is
        # a correctness fix rather than a bandwidth cost.
        if not self.cache_policy_sent:
            self.send_header("Cache-Control", "no-cache")
            self.cache_policy_sent = True
        super().end_headers()

    headers_already_secured = False
    cache_policy_sent = False

    def send_header(self, keyword, value):
        if keyword.lower() == "cache-control":
            self.cache_policy_sent = True
        super().send_header(keyword, value)

    def send_response(self, *a, **kw):
        # Reset per response, since the handler instance is reused on a
        # keep-alive connection.
        self.headers_already_secured = False
        self.cache_policy_sent = False
        super().send_response(*a, **kw)

    def do_HEAD(self):
        """HEAD on an API path returned a 404 from the static handler.

        Harmless in itself, and actively misleading: a header check against
        /api/... read the 404's headers rather than the API's, which is exactly
        how a security audit of this file briefly reached the wrong conclusion.
        """
        path = self.path.partition("?")[0]
        if is_demo_path(path):
            # As its GET would answer, without the body: 404 on the live server.
            found = demo_file(path) if DEMO is not None else None
            if found is None:
                return self._bare(404)
            self.send_response(200)
            self.send_header("Content-Type", found[1])
            self.send_header("Content-Length", str(os.path.getsize(found[0])))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path.startswith("/api/"):
            if not self._local():
                return self._json({"error": "loopback only"}, 403)
            if not self._authorised():
                return self._json({"error": "a token is required"}, 401)
            if DEMO is not None and demo_route(path) == REFUSE:
                return self._bare(403)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        return super().do_HEAD()

    def _send(self, body, ctype="application/json", status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # X-Frame-Options and nosniff are set for EVERY response in
        # end_headers() now, including static files, so setting them here as
        # well only produced duplicates.
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(json.dumps(payload, default=str).encode(), status=status)

    def _bare(self, status):
        """A status and no body, for HEAD."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _demo_get(self, path):
        """True once the demo server has answered a GET itself; False to carry on.

        Before every other route in do_GET, including the ones served here
        rather than by api.py (the phone's video, the cameras' pictures), so a
        refused route never reaches any handler at all."""
        if is_demo_path(path):
            import camstore
            found = demo_file(path)
            f = camstore.open_clip(found[0]) if found else None
            if f is None:
                self._json({"error": "not found"}, 404)
            else:
                self._ranged(f, found[1])
            return True
        if path.startswith("/api/"):
            verdict = demo_route(path)
            if verdict == "stub":
                self._json(DEMO_ROUTES[demo_entry(path)])
                return True
            if verdict == REFUSE:
                self._json(NOT_IN_DEMO, 403)
                return True
        return False

    def _demo_cue(self, body):
        """POST /api/demo/cue: {"cue": ...} becomes $OMACAR_STATE/demo-cue.json,
        {"cue": ..., "at": <epoch>}, for the demo world to act on once.

        OMACAR_STATE is the demo's: the server would not have started
        otherwise (demo_env_problems). Written whole and renamed into place, so
        the world never reads half a cue."""
        import tempfile
        try:
            data = json.loads(body or "{}")
        except ValueError:
            data = None
        cue = data.get("cue") if isinstance(data, dict) else None
        if not isinstance(cue, str) or cue not in DEMO_CUES:
            return self._json({"error": "cue must be one of " + ", ".join(DEMO_CUES)}, 400)
        state = os.environ["OMACAR_STATE"]
        os.makedirs(state, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=state, prefix=".demo-cue.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"cue": cue, "at": time.time()}, f)
        os.replace(tmp, os.path.join(state, "demo-cue.json"))
        return self._json({"ok": True})

    def _ranged(self, f, ctype):
        """A file with HTTP Range, which a <video> needs before it can seek.

        SimpleHTTPRequestHandler has none: it answers every request with the
        whole file and a 200. Chromium plays that but cannot seek in it, so
        back 10 s and a tap on the timeline would do nothing.

        `f` arrives already open (camstore.open_clip): opened with O_NOFOLLOW
        and fstat-checked as a regular file, so nothing swapped into the path
        between clip_path()'s check and this call is ever read from -- the
        close of that TOCTOU window lives there, not here."""
        import camstore
        try:
            size = os.fstat(f.fileno()).st_size
            rng = camstore.parse_range(self.headers.get("Range"), size)
            if rng == "unsatisfiable":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = rng if rng else (0, size - 1)
            length = max(0, end - start + 1)
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 18, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            f.close()

    def _mjpeg(self, role, query):
        """A camera's live picture, as MJPEG an <img> shows with no decoder.

        It ends by itself after cams.LIVE_GIVE_UP seconds with no new frame,
        and after `frames=N` frames when asked (screenshots and tests).
        Connection: close is the terminator, as for /api/phone/video."""
        import cams
        from urllib.parse import parse_qs
        try:
            frames = int((parse_qs(query).get("frames") or ["0"])[0]) or None
        except ValueError:
            frames = None
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + cams.BOUNDARY)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            cams.stream_live(self.wfile, role, frames=frames)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _local(self):
        """Only this machine, and only under a loopback name.

        A page on any site can point a fetch at a hostname that resolves to
        127.0.0.1. Checking the Host header stops that reaching an API which
        will happily clear the car's trouble codes.
        """
        if not LOOPBACK_ONLY:
            return True
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1", "")

    def _authorised(self):
        """In cockpit mode nothing at all is served without the token."""
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], TOKEN):
            return True
        qs = parse_qs(self.path.partition("?")[2])
        given = (qs.get("k") or [""])[0]
        return hmac.compare_digest(given, TOKEN)

    def _same_origin(self):
        """Refuse a POST that a web page on another site sent here.

        THE HOST CHECK WAS NEVER ENOUGH.

        _local() checks the Host header, which stops a remote page pointing a
        fetch at a hostname that resolves to 127.0.0.1. It does not stop a page
        on any website simply POSTing to http://127.0.0.1:7560/api/clear -- the
        browser sends Host: 127.0.0.1 there because that IS the host, the check
        passes, and the car's codes are cleared along with every readiness
        monitor, which fails an emissions test for days. No token is needed
        because loopback has never required one.

        Sec-Fetch-Site is sent by every current browser and is not forgeable by
        page script. Origin is the fallback for anything older. A request with
        neither -- curl, the CLI, a test -- is not a browser and is allowed:
        the threat here is a page the user did not open on purpose, not a
        person with a shell, who already has the CLI.
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if site:
            return site in ("same-origin", "same-site", "none")
        origin = self.headers.get("Origin")
        if not origin:
            return True                      # not a browser
        from urllib.parse import urlparse
        host = urlparse(origin).hostname or ""
        return host in ("127.0.0.1", "localhost", "::1")

    def _may_write(self, path):
        if ALLOW_CONTROL:
            return True
        return path in COCKPIT_WRITES

    def do_GET(self):
        if not self._local():
            return self._json({"error": "loopback only"}, 403)
        if not self._authorised():
            return self._json({"error": "a token is required"}, 401)
        path, _, query = self.path.partition("?")
        if path == "/.mark":
            return self._send(MARK.encode(), "text/plain")
        if DEMO is not None:
            if self._demo_get(path):
                return
        elif is_demo_path(path):
            # The live server has no demo: not its code, not its media.
            return self._json({"error": "not found"}, 404)
        if path == "/report.html":
            # The same document `omacar share` writes, handed to the browser as
            # a download. Self-contained, so what lands in somebody's inbox
            # opens without this server or any other.
            import share
            from urllib.parse import parse_qs, unquote_plus
            q = parse_qs(query)
            note = unquote_plus((q.get("note") or [""])[0])
            doc = share.build(note=note or None,
                              include_photos=(q.get("photos") or ["1"])[0] != "0")
            body = doc.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             'attachment; filename="omacar-report.html"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/export.csv":
            # Raw samples, out of the tool and into whatever you actually use.
            #
            # Streamed row by row rather than built in memory: a long drive is
            # tens of thousands of rows, and this runs on a 2-core machine that
            # is also polling a serial port. It is also UNDECIMATED, unlike
            # /api/history -- that thins to fit a graph, which is right for a
            # chart and wrong for an export somebody is going to analyse.
            import csv
            import io
            import time
            import records
            from urllib.parse import parse_qs
            q = parse_qs(query)
            def num(name):
                try:
                    return float((q.get(name) or [""])[0])
                except ValueError:
                    return None
            t0, t1 = num("from"), num("to")
            db = records.connect()
            rows = records.samples(db, since=t0, until=t1, limit=1000000) if db else []
            # Connection: close is REQUIRED here, not tidiness.
            #
            # This streams, so it cannot send a Content-Length. Under HTTP/1.1
            # keep-alive a body with neither Content-Length nor chunked
            # encoding has no defined end, so the client waits for more bytes
            # forever -- curl hung indefinitely on a export that had in fact
            # been written in full. Closing the connection IS the terminator.
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="omacar-samples.csv"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["iso"] + records.SAMPLE_COLS)
            for r in rows:
                vals = [r.get(c) if isinstance(r, dict) else r[i]
                        for i, c in enumerate(records.SAMPLE_COLS)]
                stamp = vals[0]
                iso = ""
                try:
                    iso = time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(float(stamp)))
                except (TypeError, ValueError):
                    pass
                w.writerow([iso] + vals)
                if buf.tell() > 32768:
                    self.wfile.write(buf.getvalue().encode())
                    buf.seek(0); buf.truncate(0)
            self.wfile.write(buf.getvalue().encode())
            if db:
                db.close()
            return
        if path == "/api/phone/video":
            # THE PHONE SCREEN'S VIDEO, AS A STREAM.
            #
            # Same shape as the CSV export above and for the same reason: no
            # Content-Length is possible, so `Connection: close` is the
            # terminator rather than tidiness. The records are self-delimiting
            # (see lib/carlink.py), so it does not matter where the chunking
            # falls -- which is the whole point, because it falls wherever the
            # kernel likes and a reader that assumed otherwise would tear
            # frames only under load.
            import carlink
            session = carlink.current()
            if session is None:
                # A session that died between the start and this request left
                # its reason behind. Repeating it beats replacing it with a
                # generic one -- the specific message is the whole point of
                # having written it.
                gone = carlink.last()
                why = getattr(gone, "error", None) if gone else None
                self._json({"error": why or "nothing is streaming — "
                                            "POST /api/phone/start first"}, 409)
                return
            q = session.subscribe()
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            import queue as _q
            # Two different clocks. The wait is short so a session that ends is
            # noticed at once rather than holding a browser open for another
            # five seconds; the keep-alive is slow because it means nothing and
            # exists only so a stalled adapter looks like a stalled adapter
            # rather than a dead network.
            quiet = 0
            try:
                while True:
                    try:
                        rec = q.get(timeout=0.5)
                        quiet = 0
                    except _q.Empty:
                        if not session.running():
                            break
                        quiet += 1
                        if quiet < 10:
                            continue
                        quiet = 0
                        rec = carlink.event({"type": "idle"})
                    self.wfile.write(rec)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass                      # the tab went away; entirely normal
            finally:
                session.unsubscribe(q)
            return

        if path.startswith("/api/roadcams/") and path.endswith("/image"):
            return self._roadcam_image(path[len("/api/roadcams/"):-len("/image")], query)

        if path.startswith("/plugin/"):
            # A plugin's own view module. Resolved through plugins.view_path,
            # which refuses anything outside that plugin's directory and
            # anything that is not a .js file.
            import plugins
            parts = path[len("/plugin/"):].split("/", 1)
            real = plugins.view_path(parts[0], parts[1] if len(parts) > 1 else "")
            if real is None:
                return self._json({"error": "no such plugin view"}, 404)
            with open(real, "rb") as f:
                body = f.read()
            self._send(body, "text/javascript; charset=utf-8")
            return
        if path == "/boot-film":
            # THE INTRO FILM, WHICH DOES NOT LIVE IN THIS REPOSITORY.
            #
            # A beautiful intro video is tens of megabytes, and this is a
            # public repository that anybody may clone to read their own car.
            # Making every one of them carry a film is the kind of weight that
            # turns "no build step, no lockfile" into a slogan rather than a
            # fact. So the boot screen draws its own mark, and if the owner has
            # put a film here it plays that instead. Absent is the normal case
            # and is not an error.
            import records as _r
            for name in ("boot.webm", "boot.mp4"):
                real = os.path.join(_r.STATE, name)
                if os.path.exists(real):
                    ctype = ("video/webm" if name.endswith(".webm")
                             else "video/mp4")
                    with open(real, "rb") as f:
                        body = f.read()
                    self._send(body, ctype)
                    return
            return self._json({"error": "no boot film"}, 404)
        if path.startswith("/doc/"):
            # Served through docs.path_of, which refuses anything climbing out
            # of the folder. Same rule as /photo/, for the same reason.
            import docs
            real = docs.path_of(path[len("/doc/"):])
            if real is None:
                return self._json({"error": "no such document"}, 404)
            import mimetypes
            ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
            with open(real, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # A stored PDF or image is shown, not executed. CSP because these
            # are files somebody else's phone produced.
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; img-src 'self'; object-src 'self'")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/photo/"):
            # Resolved through photos.path_of, which refuses anything that
            # climbs out of the folder. Never join a request path directly.
            import photos
            real = photos.path_of(path[len("/photo/"):])
            if real is None:
                return self._json({"error": "no such photograph"}, 404)
            try:
                with open(real, "rb") as f:
                    blob = f.read()
            except OSError:
                return self._json({"error": "unreadable"}, 404)
            kind = {"jpg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp"}.get(real.rsplit(".", 1)[-1], "application/octet-stream")
            return self._send(blob, kind)
        if path.startswith("/api/cams/"):
            # THE TWO CAMERA ROUTES THAT ARE NOT JSON. The others go through
            # api.handle_get like everything else (lib/camroutes.py).
            m = re.fullmatch(r"/api/cams/([a-z]+)/live", path)
            if m:
                import cams
                if m.group(1) not in cams.ROLES:
                    return self._json({"error": "no such camera"}, 404)
                return self._mjpeg(m.group(1), query)
            m = re.fullmatch(r"/api/cams/clip/([a-z]+)/([0-9-]+\.mp4)", path)
            if m:
                import camstore
                real = camstore.clip_path(m.group(1), m.group(2))
                f = camstore.open_clip(real) if real is not None else None
                if f is None:
                    return self._json({"error": "no such clip"}, 404)
                return self._ranged(f, "video/mp4")
        if path.startswith("/api/"):
            out = api.handle_get(path, query)
            if out is None:
                return self._json({"error": "no such endpoint"}, 404)
            return self._json(out[1], out[0])
        return SimpleHTTPRequestHandler.do_GET(self)

    def _roadcam_image(self, cid, query):
        """A road camera's latest still, with the picture's own age on it.

        Binary, so it is served here rather than through api.py. The id is
        looked up in Caltrans' list by lib/roadcams.py and never becomes a URL.
        `?cached=1` is the copy on disk, for a screen that has no picture at all
        and no connection to get one: it never reaches the network.
        """
        import email.utils
        import roadcams
        cached = (parse_qs(query).get("cached") or [""])[0] == "1"

        # Whether a last good picture is on disk, said with every failure, so
        # the screen can put it up -- marked as the last good one -- whatever
        # went wrong: no connection, a refusal, or a camera that has just left
        # the list.
        def kept():
            keep = roadcams.saved(cid) if not cached else None
            return {"saved": bool(keep),
                    "saved_modified": keep.get("modified") if keep else None}
        try:
            got = roadcams.image(cid, cached_only=cached)
        except roadcams.NotFound as e:
            return self._json(dict({"error": str(e), "offline": False}, **kept()), 404)
        except roadcams.Unreachable as e:
            return self._json(dict({"error": f"No connection: {e}", "offline": True},
                                   **kept()), 504)
        except roadcams.Refused as e:
            return self._json(dict({"error": str(e), "offline": False,
                                    "status": e.status}, **kept()), 502)
        except Exception as e:                                # noqa: BLE001
            return self._json({"error": f"road cameras: {type(e).__name__}: {e}",
                               "offline": False}, 500)
        body = got["body"]
        self.send_response(200)
        self.send_header("Content-Type", got.get("content_type") or "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The picture's own Last-Modified, written out again from the time it
        # parsed to rather than passed through as sent, and the same instant as
        # an age the screen can count on from: the tablet's clock is not
        # trusted to agree with Caltrans'.
        if got.get("modified") is not None:
            self.send_header("Last-Modified",
                             email.utils.formatdate(got["modified"], usegmt=True))
        for name, key in (("X-Roadcam-Age", "age"), ("X-Roadcam-Modified", "modified"),
                          ("X-Roadcam-Fetched", "fetched_at")):
            if got.get(key) is not None:
                self.send_header(name, str(int(got[key])))
        self.send_header("X-Roadcam-Source", got.get("source") or "")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._local():
            return self._json({"error": "loopback only"}, 403)
        if not self._authorised():
            return self._json({"error": "a token is required"}, 401)
        path = self.path.split("?", 1)[0]
        if not self._same_origin():
            return self._json(
                {"error": "cross-site requests are refused. This API can clear "
                          "fault codes and command actuators; a page you did not "
                          "open is not allowed to do that."}, 403)
        if not path.startswith("/api/"):
            return self._json({"error": "no such endpoint"}, 404)
        if not self._may_write(path):
            # A read-only cockpit says what it is rather than failing oddly.
            return self._json(
                {"error": "this display is read-only. Start the server with "
                          "--control to allow it to command the car."}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            # Rejected, not truncated. Truncating leaves the rest of the body in
            # the socket, where the next request on a keep-alive connection reads
            # it as a request line -- so an oversized POST used to corrupt the
            # request after it rather than failing.
            self.close_connection = True
            return self._json({"error": f"body larger than {MAX_BODY} bytes"}, 413)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        if DEMO is not None:
            # After the body is read, so a refusal leaves nothing in the socket
            # for the next request on a keep-alive connection to trip over.
            verdict = demo_route(path)
            if verdict == "stub":
                return self._json(DEMO_ROUTES[demo_entry(path)])
            if verdict != ALLOW:
                return self._json(NOT_IN_DEMO, 403)
            if path == "/api/demo/cue":
                return self._demo_cue(body)
        try:
            out = api.handle_post(path, body)
        except Exception as e:                       # noqa: BLE001
            return self._json({"error": str(e)[:500]}, 500)
        if out is None:
            return self._json({"error": "no such endpoint"}, 404)
        return self._json(out[1], out[0])


def parse_args(argv):
    port, root = int(argv[0]), argv[1]
    host, token, control = "127.0.0.1", "", None
    rest = argv[2:]
    for i, a in enumerate(rest):
        if a == "--host" and i + 1 < len(rest):
            host = rest[i + 1]
        elif a == "--token" and i + 1 < len(rest):
            token = rest[i + 1]
        elif a == "--control":
            control = True
    if control is None:
        # Loopback keeps every power it has always had; anything reachable
        # from the network has to be told to be dangerous.
        control = host in ("127.0.0.1", "localhost", "::1")
    return port, root, host, token, control


def demo_arg(argv):
    """The folder after --demo: None without the flag, "" with no folder after
    it. Separate from parse_args so its five-tuple stays as it is."""
    rest = argv[2:]
    for i, a in enumerate(rest):
        if a == "--demo":
            return rest[i + 1] if i + 1 < len(rest) else ""
    return None


def _said_so(signum, _frame):
    """Say which signal is ending the process, then let it end it.

    On 29 September this server died in the middle of a drive and left nothing
    to say why. Nothing in its request handling can end the process -- a fault
    in a request only ends that request's thread -- so what is left is being
    ended from outside, and a signal it does not handle ends a Python process
    without a word. Now SIGTERM and SIGHUP, which is what stopping a service or
    a plain kill sends, leave a line saying which and when.

    The signal is not swallowed and shutdown is not changed. The default action
    is put back and the same signal is sent to ourselves, so the process dies of
    it exactly as it did before there was a handler: no unwinding, no atexit
    handlers, no daemon threads torn down live, and a status that says "killed
    by SIGTERM" rather than an exit code that a service manager would call a
    failure.

    The line is best effort, and the signal does not wait on it. A stderr that
    cannot be written (the disk that is full is the likeliest reason a server
    is dying at all) would otherwise raise out of here, the signal would never
    be re-sent, and the process would go on, or leave by some other route with
    the wrong status.

    A death with NO line is an answer too: SIGKILL, which is what the kernel's
    out-of-memory killer sends, or another signal that is not handled here
    (SIGQUIT, SIGUSR1, SIGALRM), and no process can announce SIGKILL.
    """
    try:
        print(f"{time.strftime('%F %T')} serve.py: {signal.Signals(signum).name} "
              f"received; exiting", file=sys.stderr, flush=True)
    except Exception:                                # noqa: BLE001
        pass
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


if __name__ == "__main__":
    # A crash from inside the interpreter, which is not an exception and so is
    # not caught by anything above, leaves its stack in the log.
    faulthandler.enable()
    for _sig in (signal.SIGTERM, signal.SIGHUP):
        # Not where the signal was already being ignored when this started,
        # which is what `nohup` does to SIGHUP: that is a decision somebody
        # made about this process, and installing a handler would undo it.
        if signal.getsignal(_sig) is not signal.SIG_IGN:
            signal.signal(_sig, _said_so)
    port, root, host, TOKEN, ALLOW_CONTROL = parse_args(sys.argv[1:])
    LOOPBACK_ONLY = host in ("127.0.0.1", "localhost", "::1")
    if not LOOPBACK_ONLY and not TOKEN:
        sys.exit("serve.py: refusing to bind to the network without --token")
    _demo = demo_arg(sys.argv[1:])
    if _demo is not None:
        if not LOOPBACK_ONLY:
            sys.exit("serve.py: the demo is served on loopback only")
        if not _demo or not os.path.isdir(_demo):
            sys.exit("serve.py: --demo needs the demo's folder (the repo's demo/)")
        _why = demo_env_problems()
        if _why:
            sys.exit("serve.py: refusing to serve the demo outside its own folders: "
                     + "; ".join(_why) + ". `omacar demo on` sets them.")
        DEMO = os.path.abspath(_demo)
        DEMO_MEDIA = os.path.join(os.path.abspath(root), "assets", "private")
    os.chdir(root)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
