#!/usr/bin/env python3
"""Road cameras: the list, the stills, the pins, and the door out.

Against a trimmed copy of Caltrans District 5's own list (test/fixtures/
d5-cctv.json). NOTHING HERE REACHES THE INTERNET: the one function that could
is replaced before anything runs, and the last check is that the real one was
never called. The config and state directories are scratch ones, set before
lib/roadcams.py is imported, because its paths are fixed at import.
"""

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "test", "fixtures", "d5-cctv.json")

SCRATCH = tempfile.mkdtemp(prefix="omacar-roadcams-")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import roadcams  # noqa: E402

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, got, want=True):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg}: got {got!r}, want {want!r}")


def head(msg):
    print(f"\n  {msg}\n")


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return str(e) or True
    except Exception as e:                                    # noqa: BLE001
        return f"wrong exception {type(e).__name__}: {e}"
    return False


# ---- nothing leaves this machine ---------------------------------------------
REAL_CALLS = []


def _no_internet(url, limit):
    REAL_CALLS.append(url)
    raise AssertionError(f"the suite tried to reach the internet: {url}")


REAL_HTTP_GET = roadcams.http_get
REAL_FETCH = roadcams._fetch
roadcams._fetch = _no_internet

FEED = open(FIXTURE, "rb").read()
DOC = json.loads(FEED)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 64 + b"\xff\xd9"
IMJIN_URL = "https://cwwp2.dot.ca.gov/data/d5/cctv/image/sr1imjinparkway/sr1imjinparkway.jpg"


class Net:
    """Stands in for roadcams.http_get: answers from the fixture, or fails."""

    def __init__(self):
        self.calls = []
        self.offline = False
        self.feed = FEED
        self.image = JPEG
        self.ctype = "image/jpeg"
        self.modified = "Tue, 29 Sep 2026 06:47:10 GMT"
        self.date = "Tue, 29 Sep 2026 06:47:59 GMT"

    def __call__(self, url, limit):
        self.calls.append(url)
        if not roadcams.allowed(url):
            raise roadcams.Refused("refused: not https to a known host")
        if self.offline:
            roadcams._NET.update(fail_at=time.time(), error="no route")
            raise roadcams.Unreachable("[Errno 101] Network is unreachable")
        if url == roadcams.FEED_URL:
            return 200, {"content-type": "application/json"}, self.feed
        return 200, {"content-type": self.ctype, "last-modified": self.modified,
                     "date": self.date}, self.image


def reset():
    shutil.rmtree(roadcams.STATE, ignore_errors=True)
    try:
        os.remove(roadcams.PINS_CFG)
    except FileNotFoundError:
        pass
    roadcams._FEED_MEM.update(stamp=None, doc=None)
    roadcams._FEED_FAIL.update(at=None, error=None, offline=False)
    roadcams._IMG_MEM.clear()
    roadcams._INDEX.update(key=None, cams={})
    roadcams._NET.update(ok_at=None, fail_at=None, error=None)
    net = Net()
    roadcams.http_get = net
    return net


def main():
    head("The scratch directories are the ones in use")
    check("state is under the scratch HOME", roadcams.STATE.startswith(SCRATCH))
    check("and so is the pins file", roadcams.PINS_CFG.startswith(SCRATCH))
    check("which is where the owner's config goes, by name",
          roadcams.PINS_CFG.endswith(os.path.join("omarchy", "omacar-roadcams.json")))

    # ---------------------------------------------------------------- parsing
    head("Caltrans' list, read and filtered")
    cams = roadcams.cameras(DOC)
    check("the fixture's eight Monterey County cameras are kept", len(cams), 8)
    check("and the Santa Cruz one on the same route is not",
          any(c["id"] == "sr1eastofmorrisseyblvd" for c in cams), False)
    check("every camera has the app's shape",
          all(set(c) == {"id", "kind", "name", "route", "direction", "place", "lat",
                         "lon", "image_url", "stream_url", "updated_minutes", "source"}
              for c in cams))
    imjin = next(c for c in cams if c["id"] == "sr1imjinparkway")
    check("the id is Caltrans' own image folder name", imjin["id"], "sr1imjinparkway")
    check("the name is the feed's, as spelt", imjin["name"], "SR-1 : Imjin Parkway")
    check("with its route, direction and nearby place",
          (imjin["route"], imjin["direction"], imjin["place"]), ("SR-1", "North", "Marina"))
    check("its position as numbers",
          (round(imjin["lat"], 3), round(imjin["lon"], 3)) if imjin["lat"] else None,
          (36.667, -121.814))
    check("its still is Caltrans' JPEG", imjin["image_url"], IMJIN_URL)
    check("its video is the HLS stream, for later",
          (imjin["stream_url"] or "").endswith(".m3u8"), True)
    check("and it updates every two minutes", imjin["updated_minutes"], 2)
    check("the routes come in the owner's order",
          [c["route"] for c in cams],
          ["US-101", "US-101", "SR-1", "SR-1", "SR-68", "SR-68", "SR-156", "SR-183"])
    check("and along each road by postmile",
          [c["id"] for c in cams if c["route"] == "SR-1"],
          ["sr1lightfighterdrive", "sr1imjinparkway"])

    def item(**over):
        it = json.loads(json.dumps(DOC["data"][3]))       # sr1imjinparkway
        for k, v in over.items():
            if k == "url":
                it["cctv"]["imageData"]["static"]["currentImageURL"] = v
            elif k == "inService":
                it["cctv"]["inService"] = v
            elif k == "county":
                it["cctv"]["location"]["county"] = v
        return it

    check("a camera out of service is left out", roadcams.normalise(item(inService="false")), None)
    check("so is one in another county", roadcams.normalise(item(county="San Benito")), None)
    for why, url in (("plain http", IMJIN_URL.replace("https:", "http:")),
                     ("another host", "https://example.com/data/d5/cctv/image/x/x.jpg"),
                     ("a look-alike host", "https://cwwp2.dot.ca.gov.example.com/data/d5/cctv/image/x/x.jpg"),
                     ("another port", "https://cwwp2.dot.ca.gov:8443/data/d5/cctv/image/x/x.jpg"),
                     ("a user in the URL", "https://me@cwwp2.dot.ca.gov/data/d5/cctv/image/x/x.jpg"),
                     ("a path outside the camera folders", "https://cwwp2.dot.ca.gov/other/x.jpg")):
        check(f"and one whose still is {why}", roadcams.normalise(item(url=url)), None)

    # ------------------------------------------------------------ the door out
    head("An id is looked up, never turned into a URL")
    net = reset()
    got = roadcams.image("sr1imjinparkway")
    check("a camera's still is fetched from the URL Caltrans listed for it",
          net.calls[-1], IMJIN_URL)
    check("and comes back as the picture", got["body"], JPEG)
    for cid in ("../../etc/passwd", "https://example.com/x.jpg", "SR1IMJIN", "", "a" * 65,
                "sr1imjinparkway/../x"):
        before = len(net.calls)
        check(f"{cid[:30]!r} is not a camera, and nothing is fetched for it",
              bool(raises(roadcams.NotFound, roadcams.image, cid))
              and len(net.calls) == before, True)
    before = len(net.calls)
    check("a construction camera is framed by the browser, never fetched here",
          bool(raises(roadcams.NotFound, roadcams.image, "imjin-1")) and len(net.calls) == before,
          True)
    check("an id in no list at all is not found",
          bool(raises(roadcams.NotFound, roadcams.image, "sr1nowhere")), True)

    # A list somebody tampered with names another host. The camera is dropped
    # rather than fetched, so its id is simply not there.
    net = reset()
    evil = json.loads(FEED)
    evil["data"][3]["cctv"]["imageData"]["static"]["currentImageURL"] = \
        "https://example.com/data/d5/cctv/image/sr1imjinparkway/sr1imjinparkway.jpg"
    net.feed = json.dumps(evil).encode()
    check("a listed still on another host is never fetched",
          bool(raises(roadcams.NotFound, roadcams.image, "sr1imjinparkway"))
          and not any("example.com" in u for u in net.calls), True)

    head("The only door out")
    for why, url, want in (("https to Caltrans", IMJIN_URL, True),
                           ("http to Caltrans", IMJIN_URL.replace("https:", "http:"), False),
                           ("another host", "https://example.com/", False),
                           ("a look-alike", "https://cwwp2.dot.ca.gov.evil.example/", False),
                           ("a port", "https://cwwp2.dot.ca.gov:444/x", False),
                           ("a user", "https://a:b@cwwp2.dot.ca.gov/x", False),
                           ("a file", "file:///etc/passwd", False),
                           ("nonsense", "https://[::1/", False)):
        check(f"{why} is {'allowed' if want else 'refused'}", roadcams.allowed(url), want)

    real_http_get = REAL_HTTP_GET
    seen = []
    roadcams._fetch = lambda url, limit: seen.append(url) or (200, {}, b"")
    check("http_get refuses a URL off the list before anything is opened",
          bool(raises(roadcams.Refused, real_http_get, "https://example.com/", 10))
          and seen == [], True)
    roadcams._fetch = _no_internet
    check("a redirect is not followed, whatever it names",
          roadcams._NoRedirect().redirect_request(None, None, 302, "Found", {},
                                                  "https://example.com/"), None)

    # Every request is over in TIMEOUT, name lookup included: a request that
    # hangs is abandoned, and says it was the network.
    was = roadcams.TIMEOUT
    roadcams.TIMEOUT = 0.2
    roadcams._fetch = lambda url, limit: time.sleep(2)
    t0 = time.time()
    why = raises(roadcams.Unreachable, real_http_get, IMJIN_URL, 10)
    took = time.time() - t0
    roadcams._fetch = _no_internet
    roadcams.TIMEOUT = was
    check(f"a request that hangs is given up on in time ({took:.2f} s)",
          bool(why) and took < 1.5, True)
    check("and the timeout is at most ten seconds", roadcams.TIMEOUT <= 10, True)

    # The reading loop itself, against a fake opener: too big is refused.
    class Resp:
        status = 200
        headers = {"Content-Type": "image/jpeg"}

        def __init__(self, n):
            self.left = n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, k):
            k = min(k, self.left)
            self.left -= k
            return b"x" * k

    class Opener:
        def __init__(self, n):
            self.n = n

        def open(self, req, timeout=None):
            return Resp(self.n)

    was_opener = roadcams._OPENER
    try:
        roadcams._OPENER = Opener(5000)
        st, hd, body = REAL_FETCH(IMJIN_URL, 10000)
        check("a whole answer is read", (st, len(body), hd.get("content-type")), (200, 5000, "image/jpeg"))
        roadcams._OPENER = Opener(20000)
        check("one larger than its limit is refused",
              bool(raises(roadcams.Refused, REAL_FETCH, IMJIN_URL, 10000)), True)
    finally:
        roadcams._OPENER = was_opener

    # ------------------------------------------------------- the list's cache
    head("The list is kept six hours, and served from disk with no connection")
    net = reset()
    t0 = time.time()
    first = roadcams.listing(now=t0)
    check("the first ask fetches Caltrans' list", net.calls, [roadcams.FEED_URL])
    check("and says so", (first["feed"]["from_cache"], first["feed"]["error"]), (False, None))
    check("it counts the Monterey cameras", first["feed"]["count"], 8)
    check("the list is kept on disk",
          os.path.exists(os.path.join(roadcams.STATE, "d5-cctv.json")), True)
    again = roadcams.listing(now=t0 + 3600)
    check("an hour later the kept copy is used", (len(net.calls), again["feed"]["from_cache"]),
          (1, True))
    check("and its age is said", again["feed"]["age"], 3600)
    net.offline = True
    late = roadcams.listing(now=t0 + 6 * 3600 + 5)
    check("after six hours it asks again", len(net.calls), 2)
    check("and with no connection serves the old list",
          (len(late["cameras"]) >= 8, late["feed"]["from_cache"], late["feed"]["offline"]),
          (True, True, True))
    check("saying there is no connection, and how old the list is",
          ((late["feed"]["error"] or "").startswith("No connection"), late["feed"]["age"]),
          (True, 6 * 3600 + 5))
    roadcams.listing(now=t0 + 6 * 3600 + 20)
    check("a failure a moment ago is not asked again at once", len(net.calls), 2)
    net.offline = False
    back = roadcams.listing(now=t0 + 6 * 3600 + 5 + roadcams.FEED_RETRY + 1)
    check("and once the connection is back the list is fresh again",
          (len(net.calls), back["feed"]["from_cache"], back["feed"]["error"]), (3, False, None))

    net = reset()
    net.offline = True
    none = roadcams.listing()
    check("with no list ever kept and no connection: no Caltrans cameras",
          [c["id"] for c in none["cameras"] if c["kind"] == "still"], [])
    check("but the reason", (none["feed"]["error"] or "").startswith("No connection"), True)
    check("and the construction cameras are still listed",
          [c["id"] for c in none["cameras"] if c["kind"] == "embed"],
          ["imjin-1", "imjin-2", "imjin-3"])

    net = reset()
    net.feed = b"<html>maintenance</html>"
    check("a list that is not Caltrans' JSON is not kept",
          (roadcams.listing()["feed"]["error"] is not None,
           os.path.exists(os.path.join(roadcams.STATE, "d5-cctv.json"))), (True, False))

    head("The groups, in the owner's order")
    net = reset()
    got = roadcams.listing()
    check("US-101, SR-1, SR-68, SR-156, SR-183, then Imjin Parkway",
          [g["label"] for g in got["groups"]],
          ["US-101", "SR-1", "SR-68", "SR-156", "SR-183", "Imjin Parkway"])
    check("each group names its cameras",
          next(g for g in got["groups"] if g["id"] == "SR-68")["ids"],
          ["sr68yorkroad", "sr68reservationroadriverroad"])
    emb = [c for c in got["cameras"] if c["kind"] == "embed"]
    check("the Imjin Parkway embeds carry the project's name and the note",
          [(e["name"], e["source"], bool(e["note"])) for e in emb],
          [(f"Imjin Parkway — camera {n}", "Imjin Parkway Widening project (TrueLook)", True)
           for n in (1, 2, 3)])
    check("and are https on TrueLook's host",
          all(e["url"].startswith("https://app.truelook.cloud/?code=") for e in emb), True)
    data = roadcams.data_file()
    data = json.loads(json.dumps(data))
    data["embeds"].append({"id": "sneaky", "name": "x", "url": "https://example.com/"})
    data["embeds"].append({"id": "plain", "name": "x", "url": "http://app.truelook.cloud/?code=1"})
    check("an embed on another host, or over http, is dropped",
          [e["id"] for e in roadcams.embeds(data)], ["imjin-1", "imjin-2", "imjin-3"])

    # ------------------------------------------------------------- the stills
    head("A still is kept a minute, and carries its own age")
    net = reset()
    t0 = time.time()
    a = roadcams.image("sr1imjinparkway", now=t0)
    b = roadcams.image("sr1imjinparkway", now=t0 + 30)
    check("two asks inside a minute cost Caltrans one request",
          sum(1 for u in net.calls if u == IMJIN_URL), 1)
    check("the second says it came from the cache", (a["source"], b["source"]), ("caltrans", "cache"))
    check("the picture's own Last-Modified is kept as sent", a["last_modified"], net.modified)
    check("its age is counted on Caltrans' clock (Date minus Last-Modified)", a["age"], 49)
    check("and grows with the time since", b["age"], 79)
    roadcams.image("sr1imjinparkway", now=t0 + roadcams.IMAGE_TTL + 1)
    check("after a minute it is fetched again",
          sum(1 for u in net.calls if u == IMJIN_URL), 2)

    net.offline = True
    check("with no connection a fresh ask says it is the network",
          bool(raises(roadcams.Unreachable, roadcams.image, "sr1imjinparkway",
                      now=t0 + 3 * roadcams.IMAGE_TTL)), True)
    roadcams._IMG_MEM.clear()
    s = roadcams.image("sr1imjinparkway", cached_only=True, now=t0 + 600)
    check("the last picture is on disk, and says it is the saved one",
          (s["body"], s["source"]), (JPEG, "saved"))
    check("with its real age", s["age"], 49 + 600 - (roadcams.IMAGE_TTL + 1))
    check("a camera never fetched has no saved picture",
          bool(raises(roadcams.NotFound, roadcams.image, "sr68yorkroad", cached_only=True)), True)
    net.offline = False
    net.ctype, net.image = "text/html", b"<html>oops</html>"
    check("an answer that is not a picture is refused, not passed on",
          bool(raises(roadcams.Refused, roadcams.image, "us101airportblvd")), True)
    net.ctype, net.image = "image/jpeg", b"<html>oops</html>"
    check("even when it claims to be one",
          bool(raises(roadcams.Refused, roadcams.image, "us101airportblvd")), True)

    # ---------------------------------------------------------------- the pins
    head("The pins: the commute by default, validated when saved")
    net = reset()
    got = roadcams.listing()
    check("the default is the commute, then the Imjin Parkway cameras", got["pins"],
          ["sr1imjinparkway", "sr1lightfighterdrive", "sr68reservationroadriverroad",
           "imjin-1", "imjin-2", "imjin-3"])
    check("and says it is the default", (got["pins_default"], got["pins_missing"]), (True, []))
    saved = roadcams.save_pins({"pins": ["sr68yorkroad", "imjin-2", "sr68yorkroad"]})
    check("saving writes them, once each, in order", saved["pins"], ["sr68yorkroad", "imjin-2"])
    check("to the owner's config file",
          json.load(open(roadcams.PINS_CFG))["pins"], ["sr68yorkroad", "imjin-2"])
    check("and they are no longer the default", roadcams.listing()["pins_default"], False)
    check("the listing still carries the defaults, for the editor's 'Back to the commute'",
          roadcams.listing()["default_pins"],
          ["sr1imjinparkway", "sr1lightfighterdrive", "sr68reservationroadriverroad",
           "imjin-1", "imjin-2", "imjin-3"])
    for why, body in (("not an object", ["sr68yorkroad"]),
                      ("no list", {"pins": "sr68yorkroad"}),
                      ("something that is not a string", {"pins": [7]}),
                      ("a path", {"pins": ["../../etc/passwd"]}),
                      ("a URL", {"pins": ["https://example.com/x.jpg"]}),
                      ("a camera that is not in the list", {"pins": ["sr1nowhere"]}),
                      ("too many", {"pins": [f"cam{i}" for i in range(roadcams.MAX_PINS + 1)]})):
        check(f"a body with {why} is refused",
              bool(raises(ValueError, roadcams.save_pins, body)), True)
    check("and a refusal leaves the saved pins alone",
          json.load(open(roadcams.PINS_CFG))["pins"], ["sr68yorkroad", "imjin-2"])

    gone = json.loads(FEED)
    gone["data"] = [x for x in gone["data"] if "sr68yorkroad" not in json.dumps(x)]
    net.feed = json.dumps(gone).encode()
    roadcams.feed(force=True)
    left = roadcams.listing()
    check("a pinned camera that leaves Caltrans' list stays pinned", left["pins"],
          ["sr68yorkroad", "imjin-2"])
    check("and is named as missing, by the name it had",
          left["pins_missing"], [{"id": "sr68yorkroad", "name": "SR-68 : York Road"}])

    check("reset goes back to the commute",
          roadcams.save_pins({"action": "reset"})["pins_default"], True)
    check("and removes the file", os.path.exists(roadcams.PINS_CFG), False)

    net = reset()
    net.offline = True
    why = raises(ValueError, roadcams.save_pins, {"pins": ["sr1imjinparkway"]})
    check("with no list at all a Caltrans camera cannot be checked, and says so",
          "cannot be checked" in str(why), True)
    check("but the construction cameras can still be pinned",
          roadcams.save_pins({"pins": ["imjin-3"]})["pins"], ["imjin-3"])

    head("Through the API")
    import api  # noqa: E402
    net = reset()
    st, body = api.handle_get("/api/roadcams", "")
    check("GET /api/roadcams answers the listing",
          (st, [g["id"] for g in body["groups"]][:2]), (200, ["US-101", "SR-1"]))
    st, body = api.handle_post("/api/roadcams/pins", '{"pins": ["us101airportblvd"]}')
    check("POST /api/roadcams/pins saves", (st, body["pins"]), (200, ["us101airportblvd"]))
    st, body = api.handle_post("/api/roadcams/pins", '{"pins": ["nope/../x"]}')
    check("and refuses a bad one with the reason", (st, "camera id" in body.get("error", "")),
          (400, True))

    head("Through the server")
    import serve  # noqa: E402
    net = reset()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    try:
        st, hd, body = get("/api/roadcams/sr1imjinparkway/image")
        check("a still is served as a picture",
              (st, hd.get("Content-Type"), body), (200, "image/jpeg", JPEG))
        check("with the picture's own Last-Modified", hd.get("Last-Modified"), net.modified)
        check("and its age in seconds, never 'live'", hd.get("X-Roadcam-Age", "").isdigit(), True)
        check("which is not cached by the browser", hd.get("Cache-Control"), "no-store")
        for path in ("/api/roadcams/sr1nowhere/image", "/api/roadcams/..%2F..%2Fetc/image",
                     "/api/roadcams/imjin-1/image",
                     "/api/roadcams/https:%2F%2Fexample.com%2Fx.jpg/image"):
            st, hd, body = get(path)
            check(f"{path} is a 404 with a reason in JSON",
                  (st, "error" in json.loads(body or b"{}")), (404, True))
        net.offline = True
        roadcams._IMG_MEM.clear()
        st, hd, body = get("/api/roadcams/sr1imjinparkway/image")
        err = json.loads(body or b"{}")
        check("with no connection the answer is a clear error, not a broken picture",
              (st, err.get("offline"), err.get("saved"), err.get("error", "")[:13]),
              (504, True, True, "No connection"))
        st, hd, body = get("/api/roadcams/sr1imjinparkway/image?cached=1")
        check("and the saved copy is there for the asking",
              (st, hd.get("X-Roadcam-Source"), body), (200, "saved", JPEG))
        st, hd, body = get("/api/roadcams")
        check("the listing is still a 200 with no connection", st, 200)
    finally:
        srv.shutdown()
    serve.ALLOW_CONTROL = False
    h = serve.Handler.__new__(serve.Handler)
    check("a read-only cockpit may still choose which cameras it shows",
          serve.Handler._may_write(h, "/api/roadcams/pins"), True)
    serve.ALLOW_CONTROL = True

    head("Nothing reached the internet")
    check("the real request was never made", REAL_CALLS, [])

    shutil.rmtree(SCRATCH, ignore_errors=True)
    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  road cameras hold\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
