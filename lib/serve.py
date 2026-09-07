#!/usr/bin/env python3
"""The server behind the OmaCar workshop.

Serves share/ over http (never file:// — a null origin partitions localStorage
and breaks fetch against our own /api) and hands every /api request to api.py.

Deliberately does no OBD work of its own. One process owns the serial
connection, and it is the daemon.

    python3 serve.py <port> <share-dir> [--host H] [--token T] [--control]

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
import hmac
import json
import os
import sys
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
COCKPIT_WRITES = {"/api/units"}


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
        if path.startswith("/api/"):
            if not self._local():
                return self._json({"error": "loopback only"}, 403)
            if not self._authorised():
                return self._json({"error": "a token is required"}, 401)
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
        if path.startswith("/api/"):
            out = api.handle_get(path, query)
            if out is None:
                return self._json({"error": "no such endpoint"}, 404)
            return self._json(out[1], out[0])
        return SimpleHTTPRequestHandler.do_GET(self)

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


if __name__ == "__main__":
    port, root, host, TOKEN, ALLOW_CONTROL = parse_args(sys.argv[1:])
    LOOPBACK_ONLY = host in ("127.0.0.1", "localhost", "::1")
    if not LOOPBACK_ONLY and not TOKEN:
        sys.exit("serve.py: refusing to bind to the network without --token")
    os.chdir(root)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
