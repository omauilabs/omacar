"""Road cameras: Caltrans' stills for Monterey County, and the Imjin Parkway
project's construction cameras.

The owner commutes around Marina, Seaside and Salinas and used to check these
by hand. Caltrans District 5 publishes every camera it runs as one JSON file;
this reads it, keeps the Monterey County cameras that are in service, and hands
the browser a short list in one shape. The stills are proxied rather than
linked, for two reasons. An <img> cannot read the picture's own Last-Modified,
and "how old is this picture" is the whole question on a road camera. And three
screens asking for the same still inside a minute should cost Caltrans one
request, not three.

    GET  /api/roadcams              the list, the pins, and how old the list is
    GET  /api/roadcams/<id>/image   the latest still (served by serve.py)
    POST /api/roadcams/pins         save the pins

NOT AN OPEN PROXY. An id is looked up in the list Caltrans published and is
never turned into a URL; the URL the list gives for it is fetched only over
https from a host in HOSTS, redirects are refused, and every request is over
in TIMEOUT seconds, name lookup included. The Imjin Parkway cameras are never
fetched here at all: the browser frames them, and only from a host the data
file names.

Honest about age. The list is kept for six hours and served from disk when
there is no connection, saying how old it is. A still carries the time
Caltrans stamped on it, never "live".
"""

import email.utils
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "share", "data", "roadcams.json")

FEED_URL = "https://cwwp2.dot.ca.gov/data/d5/cctv/cctvStatusD05.json"
SOURCE = "Caltrans District 5"
# The only hosts this server will ever fetch from.
HOSTS = frozenset({"cwwp2.dot.ca.gov"})
# The only hosts the browser may frame a construction camera from. In code,
# not in the data file beside the URLs it checks, so one edit to that file
# cannot add both a host and a page on it.
EMBED_HOSTS = frozenset({"app.truelook.cloud"})
COUNTY = "Monterey"
ROUTE_ORDER = ("US-101", "SR-1", "SR-68", "SR-156", "SR-183")
EMBED_GROUP = "Imjin Parkway"

FEED_TTL = 6 * 3600       # the list changes a few times a year
FEED_RETRY = 60           # after a failure, serve the old list for this long before asking again
IMAGE_TTL = 60            # several screens, one request to Caltrans a minute
TIMEOUT = 10              # seconds for a whole request, name lookup included
FEED_LIMIT = 4 << 20      # the D5 list is about 560 KB
IMAGE_LIMIT = 2 << 20     # a still is about 10-40 KB
MAX_PINS = 12

# A camera id: Caltrans' own image folder name (sr1imjinparkway), or an embed's
# id from the data file (imjin-1). Nothing else is looked up.
# \Z, not $: a $ also matches before a trailing newline.
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")
_IMAGE_PATH = re.compile(r"^/data/d5/cctv/image/([a-z0-9-]{1,64})/[a-z0-9-]{1,64}\.jpg\Z")


def _xdg(var, default):
    return os.path.expanduser(os.environ.get(var, default))


STATE = os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state"), "omacar", "roadcams")
PINS_CFG = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "omarchy",
                        "omacar-roadcams.json")


class Unreachable(Exception):
    """The network, not Caltrans: no route, no name, no answer in time."""


class Refused(Exception):
    """Caltrans answered, but not with what was asked for."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class NotFound(Exception):
    """No camera by that id, or no saved picture of it."""


# ---- the only door out ------------------------------------------------------

def allowed(url, hosts=HOSTS):
    """True only for https on the default port to a host in `hosts`."""
    try:
        u = urllib.parse.urlsplit(str(url))
        port = u.port
    except ValueError:
        return False
    return (u.scheme == "https" and (u.hostname or "") in hosts
            and port in (None, 443) and not u.username and not u.password)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect could name any host at all. Caltrans has no reason to send
    # one, so one is a failure rather than something to follow.
    def redirect_request(self, *a, **kw):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

# The last time anything here reached the internet, and the last time it did
# not. The screen uses it to decide whether a construction camera is worth
# framing at all.
_NET = {"ok_at": None, "fail_at": None, "error": None}


def _fetch(url, limit):
    """One GET, in the calling thread. (status, headers, body)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "OmaCar/1 (road cameras; one car, one owner)",
        "Accept": "application/json, image/jpeg;q=0.9, */*;q=0.1"})
    deadline = time.time() + TIMEOUT
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            chunks, size = [], 0
            while True:
                if time.time() > deadline:
                    raise Unreachable(f"no complete answer within {TIMEOUT} s")
                b = r.read(65536)
                if not b:
                    break
                size += len(b)
                if size > limit:
                    raise Refused(f"the answer was larger than {limit} bytes")
                chunks.append(b)
            return r.status, {k.lower(): v for k, v in r.headers.items()}, b"".join(chunks)
    except urllib.error.HTTPError as e:
        raise Refused(f"Caltrans answered {e.code} {e.reason}", e.code) from None
    except urllib.error.URLError as e:
        raise Unreachable(str(getattr(e, "reason", e))) from None
    except (TimeoutError, OSError) as e:
        raise Unreachable(str(e) or type(e).__name__) from None


def http_get(url, limit):
    """GET `url` from an allowed host, whole, in at most TIMEOUT seconds.

    The socket timeout does not cover the name lookup, and in a car with no
    signal that is exactly the part that hangs. So the request runs in a
    thread and this waits for it for TIMEOUT seconds, no longer; a lookup
    that is still stuck after that is abandoned, not waited on.

    Tests replace this function. Nothing in the suite reaches the internet.
    """
    if not allowed(url):
        raise Refused("refused: not https to a known host")
    box = {}

    def run():
        try:
            box["ok"] = _fetch(url, limit)
        except Exception as e:                                # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(TIMEOUT)
    now = time.time()
    if "ok" in box:
        _NET.update(ok_at=now)
        return box["ok"]
    err = box.get("err") or Unreachable(f"no answer within {TIMEOUT} s")
    if isinstance(err, Refused):
        _NET.update(ok_at=now)       # something answered: the network is up
        raise err
    if not isinstance(err, Unreachable):
        err = Unreachable(str(err) or type(err).__name__)
    _NET.update(fail_at=now, error=str(err))
    raise err


def _write(path, data):
    """Atomically or not at all: a failed write leaves no .tmp file behind."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---- the list ---------------------------------------------------------------
#
# TWO LOCKS, AND NEITHER IS HELD WHILE ANOTHER REQUEST WAITS ON THE NETWORK.
# _STATE_LOCK guards the copies and the failure note and is only ever held for
# a moment. _REFRESH_LOCK lets one download of the list run at a time; a caller
# who finds one under way takes the list already in hand rather than queueing
# behind it, and a still never waits for the list at all while any copy of it
# exists (see _index()).
#
# THE LAST GOOD LIST IS KEPT IN MEMORY AS WELL AS ON DISK. A full or read-only
# disk costs the copy that survives a restart, not the list: it is served from
# memory for its six hours, with a warning, instead of being downloaded again
# on every request.

def _feed_file():
    return os.path.join(STATE, "d5-cctv.json")


_STATE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_FEED_MEM = {"stamp": None, "doc": None}      # the copy on disk, as last read
_FEED_GOOD = {"doc": None}                    # the last good list this process fetched
_FEED_FAIL = {"at": None, "error": None, "offline": False}
_FEED_WARN = {"text": None}                   # why the list is not on disk


def _read_cache():
    path = _feed_file()
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return None
    if _FEED_MEM["stamp"] == stamp:
        return _FEED_MEM["doc"]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if not (isinstance(doc, dict) and isinstance(doc.get("feed"), dict)
                and isinstance(doc.get("fetched_at"), (int, float))):
            return None
    except (OSError, ValueError):
        return None
    _FEED_MEM.update(stamp=stamp, doc=doc)
    return doc


def _current():
    """The newest good list at hand, from memory or from disk, or None."""
    disk = _read_cache()
    mem = _FEED_GOOD["doc"]
    if mem and (not disk or mem["fetched_at"] >= disk["fetched_at"]):
        return mem
    return disk


def _fresh(doc, now):
    return bool(doc) and 0 <= now - doc["fetched_at"] < FEED_TTL


def _served(doc, now):
    """What is at hand, with whatever is known to be wrong with it."""
    if not doc:
        return None, _nothing(_FEED_FAIL["error"], _FEED_FAIL["offline"])
    fresh = _fresh(doc, now)
    return doc["feed"], _meta(doc, now, from_cache=True,
                              error=None if fresh else _FEED_FAIL["error"],
                              offline=False if fresh else _FEED_FAIL["offline"])


def feed(now=None, force=False, stale_ok=False):
    """(the feed as Caltrans published it, or None; what to say about it).

    `stale_ok`: any copy at hand will do, however old, and only a caller with
    nothing at all waits for a download. The stills ask this way: which URL a
    camera has does not change from one hour to the next.
    """
    now = time.time() if now is None else now
    with _STATE_LOCK:
        cached = _current()
        # A failure a moment ago is not asked again on every request: with no
        # signal each ask costs the full TIMEOUT, and the screen would wait
        # ten seconds for a list it already has.
        failed = _FEED_FAIL["at"]
        backoff = failed is not None and 0 <= now - failed < FEED_RETRY
        if not force and (_fresh(cached, now) or backoff or (stale_ok and cached)):
            return _served(cached, now)
    wait = cached is None
    if not _REFRESH_LOCK.acquire(blocking=wait, timeout=TIMEOUT + 1 if wait else -1):
        return _served(cached, now)
    try:
        with _STATE_LOCK:
            cached = _current()
        if not force and _fresh(cached, now):
            return _served(cached, now)          # somebody else just fetched it
        return _download(now, cached)
    finally:
        _REFRESH_LOCK.release()


def _download(now, cached):
    try:
        _status, _h, body = http_get(FEED_URL, FEED_LIMIT)
        try:
            doc = json.loads(body.decode("utf-8"))
        except ValueError:
            raise Refused("Caltrans sent a web page, not the list") from None
        if not (isinstance(doc, dict) and isinstance(doc.get("data"), list)):
            raise Refused("the list is not in the shape Caltrans uses")
        # A LIST THAT PARSES IS NOT YET A GOOD LIST. One with no Monterey
        # cameras, or with less than half as many as the last good one, is a
        # maintenance page or a changed format, and replacing the good copy
        # with it would say "fresh" for six hours with every pin missing.
        got = len(cameras(doc))
        had = len(cameras(cached["feed"])) if cached else 0
        if got == 0:
            raise Refused("it listed no Monterey County cameras")
        if had and got * 2 < had:
            raise Refused(f"it listed {got} Monterey County cameras, down from {had}")
    except (Unreachable, Refused) as e:
        offline = isinstance(e, Unreachable)
        why = (f"No connection ({e})" if offline
               else f"Caltrans' camera list didn't load ({e})")
        with _STATE_LOCK:
            _FEED_FAIL.update(at=now, error=why, offline=offline)
        if cached:
            return cached["feed"], _meta(cached, now, from_cache=True,
                                         error=why, offline=offline)
        return None, _nothing(why, offline)
    wrapped = {"fetched_at": now, "url": FEED_URL, "feed": doc}
    with _STATE_LOCK:
        _FEED_FAIL.update(at=None, error=None, offline=False)
        _FEED_GOOD["doc"] = wrapped
    try:
        _write(_feed_file(), json.dumps(wrapped).encode("utf-8"))
        _FEED_WARN["text"] = None
    except OSError as e:
        _FEED_WARN["text"] = (f"The camera list could not be kept on disk "
                              f"({e.strerror or e}), so it is held in memory "
                              f"until OmaCar restarts.")
    _FEED_MEM.update(stamp=None, doc=None)
    return doc, _meta(wrapped, now, from_cache=False, error=None, offline=False)


def _nothing(error, offline):
    return {"source": SOURCE, "url": FEED_URL, "fetched_at": None, "age": None,
            "from_cache": False, "error": error, "offline": offline,
            "warning": _FEED_WARN["text"]}


def _meta(doc, now, from_cache, error, offline):
    return {"source": SOURCE, "url": FEED_URL, "fetched_at": doc["fetched_at"],
            "age": max(0, int(now - doc["fetched_at"])), "from_cache": from_cache,
            "error": error, "offline": offline, "warning": _FEED_WARN["text"]}


def _route(raw):
    r = str(raw or "").strip().upper().replace(" ", "")
    m = re.match(r"^(US|SR|I)-?(\d+)$", r)
    return f"{m.group(1)}-{m.group(2)}" if m else (str(raw or "").strip() or "Other")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def normalise(item):
    """One camera from the D5 list in the app's shape, or None to leave it out.

    Out: another county, out of service, or an image URL that is not https on
    Caltrans' own host (then there is nothing this server may fetch for it).
    """
    c = (item or {}).get("cctv") if isinstance(item, dict) else None
    if not isinstance(c, dict):
        return None
    loc = c.get("location") or {}
    if str(loc.get("county") or "").strip() != COUNTY:
        return None
    if str(c.get("inService") or "").strip().lower() != "true":
        return None
    still = ((c.get("imageData") or {}).get("static") or {})
    url = str(still.get("currentImageURL") or "")
    if not allowed(url):
        return None
    m = _IMAGE_PATH.match(urllib.parse.urlsplit(url).path)
    if not m or not ID.match(m.group(1)):
        return None
    freq = _num(still.get("currentImageUpdateFrequency"))
    freq = int(min(60, max(1, freq))) if freq else 2
    stream = str((c.get("imageData") or {}).get("streamingVideoURL") or "")
    return {
        "id": m.group(1),
        "kind": "still",
        "name": str(loc.get("locationName") or m.group(1)).strip(),
        "route": _route(loc.get("route")),
        "direction": str(loc.get("direction") or "").strip() or None,
        "place": str(loc.get("nearbyPlace") or "").strip() or None,
        "lat": _num(loc.get("latitude")),
        "lon": _num(loc.get("longitude")),
        "image_url": url,
        "stream_url": stream if stream.startswith("https://") else None,
        "updated_minutes": freq,
        "source": SOURCE,
        # Kept for ordering along the road, then dropped: see cameras().
        "_pm": _num(loc.get("postmile")),
    }


def cameras(doc):
    """Every Monterey County camera in service, in route order, then along
    the road by postmile (south to north, west to east)."""
    out, seen = [], set()
    for item in (doc or {}).get("data") or []:
        cam = normalise(item)
        if cam and cam["id"] not in seen:
            seen.add(cam["id"])
            out.append(cam)
    rank = {r: i for i, r in enumerate(ROUTE_ORDER)}
    out.sort(key=lambda c: (rank.get(c["route"], len(rank)), c["route"],
                            c["_pm"] if c["_pm"] is not None else 1e9, c["name"]))
    for c in out:
        c.pop("_pm", None)
    return out


# ---- the embeds and the defaults ---------------------------------------------

def data_file(path=None):
    try:
        with open(path or DATA, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def embeds(data=None):
    """The construction cameras, each checked: a host not in EMBED_HOSTS is dropped."""
    data = data_file() if data is None else data
    hosts = EMBED_HOSTS
    out, seen = [], set()
    for e in data.get("embeds") or []:
        if not isinstance(e, dict):
            continue
        eid, url = str(e.get("id") or ""), str(e.get("url") or "")
        if not ID.match(eid) or eid in seen or not allowed(url, hosts):
            continue
        seen.add(eid)
        out.append({"id": eid, "kind": "embed",
                    "name": str(e.get("name") or eid), "route": EMBED_GROUP,
                    "url": url, "source": str(e.get("source") or ""),
                    "note": str(e.get("note") or "")})
    return out


# ---- the pins -----------------------------------------------------------------

def _read_pins():
    try:
        with open(PINS_CFG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("pins"), list):
        return None
    return doc


def defaults(cams, embs, data=None):
    """(the default pins as ids, the ones that could not be resolved).

    The data file names Caltrans cameras as the feed spells them, so a
    default is resolved against the list rather than hard-coding an id.
    """
    known = {c["id"] for c in list(cams) + list(embs)}
    by_name = {c["name"]: c["id"] for c in cams}
    data = data_file() if data is None else data
    pins, missing = [], []
    for entry in data.get("default_pins") or []:
        entry = str(entry)
        cid = entry if entry in known else by_name.get(entry)
        if cid and cid not in pins:
            pins.append(cid)
        elif not cid:
            missing.append({"id": None, "name": entry})
    return pins, missing


def pins_for(cams, embs, data=None):
    """(pins as ids, whether they are the defaults, pins that are not listed).

    A pin that is not in the list stays pinned, and says so: a camera that
    went out of service is something the owner should hear about, not a tile
    that quietly vanished from the commute.
    """
    known = {c["id"]: c["name"] for c in list(cams) + list(embs)}
    doc = _read_pins()
    if doc is None:
        pins, missing = defaults(cams, embs, data)
        return pins, True, missing
    pins, missing, seen = [], [], set()
    labels = doc.get("labels") if isinstance(doc.get("labels"), dict) else {}
    default = False
    for cid in doc["pins"][:MAX_PINS]:
        if not isinstance(cid, str) or not ID.match(cid) or cid in seen:
            continue
        seen.add(cid)
        pins.append(cid)
        if cid not in known:
            missing.append({"id": cid, "name": str(labels.get(cid) or cid)})
    return pins, default, missing


def save_pins(data):
    """Validate and write the pins. Raises ValueError with the reason."""
    if not isinstance(data, dict):
        raise ValueError("pins are an object: {\"pins\": [camera ids]}")
    if data.get("action") == "reset":
        try:
            os.remove(PINS_CFG)
        except FileNotFoundError:
            pass
        return listing()
    pins = data.get("pins")
    if not isinstance(pins, list):
        raise ValueError("pins must be a list of camera ids")
    if len(pins) > MAX_PINS:
        raise ValueError(f"at most {MAX_PINS} cameras can be pinned")
    doc, _meta_ = feed(stale_ok=True)
    cams = cameras(doc) if doc else []
    embs = embeds()
    known = {c["id"]: c["name"] for c in cams + embs}
    # A PIN ALREADY SAVED IS NEVER A REASON TO REFUSE A SAVE. A pinned camera
    # that has since left Caltrans' list stays pinned, under the name it had,
    # until the owner unpins it; refusing every later save because of it made
    # a reorder fail with a reason that did not say why. Only a NEW pin has to
    # be in the list.
    was = _read_pins() or {}
    before = {p for p in was.get("pins") or [] if isinstance(p, str) and ID.match(p)}
    old = was.get("labels") if isinstance(was.get("labels"), dict) else {}
    out = []
    for cid in pins:
        if not isinstance(cid, str) or not ID.match(cid):
            raise ValueError(f"{cid!r} is not a camera id")
        if cid in out:
            continue
        if cid not in known and cid not in before:
            if doc is None:
                raise ValueError(f"the Caltrans camera list is not available, so "
                                 f"{cid} cannot be checked. Try again with a connection.")
            raise ValueError(f"no camera called {cid} in the list")
        out.append(cid)
    body = {"_comment": "Road cameras pinned in OmaCar's Navigation tab, in the "
                        "order shown. Edit them in the app: Navigation, Road "
                        "cameras, Edit pins. Delete this file for the defaults.",
            "pins": out,
            "labels": {cid: str(known.get(cid) or old.get(cid) or cid) for cid in out}}
    _write(PINS_CFG, (json.dumps(body, indent=2) + "\n").encode("utf-8"))
    return listing()


# ---- the answer to GET /api/roadcams -------------------------------------------

def listing(now=None):
    """The whole screen's worth, and never an exception for a network fault."""
    doc, meta = feed(now=now)
    cams = cameras(doc) if doc else []
    embs = embeds()
    meta["count"] = len(cams)
    groups = []
    for c in cams:
        if not groups or groups[-1]["id"] != c["route"]:
            groups.append({"id": c["route"], "label": c["route"], "ids": []})
        groups[-1]["ids"].append(c["id"])
    if embs:
        groups.append({"id": "imjin", "label": EMBED_GROUP, "ids": [e["id"] for e in embs]})
    pins, default, missing = pins_for(cams, embs)
    return {"cameras": cams + embs, "groups": groups, "pins": pins,
            "pins_default": default, "pins_missing": missing,
            "default_pins": defaults(cams, embs)[0], "feed": meta,
            "net": dict(_NET), "image_ttl": IMAGE_TTL, "max_pins": MAX_PINS}


# ---- the stills -----------------------------------------------------------------

_IMG_LOCK = threading.Lock()
_IMG_LOCKS = {}
_IMG_MEM = {}
_INDEX = {"key": None, "cams": {}}


def _index():
    doc, meta = feed(stale_ok=True)
    if doc is None:
        raise Unreachable(meta.get("error") or "the camera list is not available")
    key = meta["fetched_at"]
    if _INDEX["key"] != key:
        _INDEX.update(key=key, cams={c["id"]: c for c in cameras(doc)})
    return _INDEX["cams"]


def _img_paths(cid):
    base = os.path.join(STATE, "img", cid)
    return base + ".jpg", base + ".json"


def _http_time(value):
    try:
        dt = email.utils.parsedate_to_datetime(value) if value else None
        return dt.timestamp() if dt else None
    except (TypeError, ValueError, IndexError):
        return None


def _is_picture(ctype, body):
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype == "image/jpeg":
        return body[:3] == b"\xff\xd8\xff"
    if ctype == "image/png":
        return body[:8] == b"\x89PNG\r\n\x1a\n"
    return False


def _aged(rec, now, how):
    out = dict(rec)
    out["source"] = how
    at_fetch = rec.get("age_at_fetch")
    out["age"] = None if at_fetch is None else max(0, int(at_fetch + now - rec["fetched_at"]))
    return out


def saved(cid):
    """The last still kept on disk for `cid`, or None. Never fetches."""
    if not ID.match(str(cid)):
        return None
    jpg, meta = _img_paths(cid)
    try:
        with open(meta, encoding="utf-8") as f:
            rec = json.load(f)
        with open(jpg, "rb") as f:
            rec["body"] = f.read()
        return rec
    except (OSError, ValueError):
        return None


def image(cid, cached_only=False, now=None):
    """The latest still for camera `cid`.

    A dict: body, content_type, last_modified (the picture's own header, as
    sent), modified (the same, in epoch seconds), age (seconds old now, on
    Caltrans' clock where it sent one), fetched_at, and source -- "caltrans",
    "cache" (fetched under IMAGE_TTL ago) or "saved" (the copy on disk, asked
    for with cached_only). Raises NotFound, Unreachable or Refused.
    """
    clock = now is None
    now = time.time() if clock else now
    if not isinstance(cid, str) or not ID.match(cid):
        raise NotFound("that is not a camera id")
    if cached_only:
        rec = _IMG_MEM.get(cid) or saved(cid)
        if not rec:
            raise NotFound("no saved picture of this camera yet")
        return _aged(rec, now, "saved")
    cam = _index().get(cid)
    if cam is None:
        raise NotFound(f"no Caltrans camera called {cid} in Monterey County")
    with _IMG_LOCK:
        lock = _IMG_LOCKS.setdefault(cid, threading.Lock())
    with lock:
        rec = _IMG_MEM.get(cid)
        if rec and 0 <= now - rec["fetched_at"] < IMAGE_TTL:
            return _aged(rec, now, "cache")
        url = cam["image_url"]
        if not allowed(url):                  # checked in normalise(); twice is cheap
            raise Refused("refused: not https to a known host")
        _status, hdrs, body = http_get(url, IMAGE_LIMIT)
        ctype = (hdrs.get("content-type") or "").split(";")[0].strip().lower()
        if not _is_picture(ctype, body):
            raise Refused("Caltrans sent something that is not a picture")
        fetched = time.time() if clock else now
        last_mod = hdrs.get("last-modified")
        modified = _http_time(last_mod)
        # The picture's age on Caltrans' own clock when it sent one: the
        # tablet's clock can be minutes out after a night with no signal,
        # and a still is only ever a couple of minutes old when it is good.
        said = _http_time(hdrs.get("date"))
        at_fetch = None if modified is None else max(0.0, (said or fetched) - modified)
        rec = {"body": body, "content_type": ctype, "last_modified": last_mod,
               "modified": modified, "age_at_fetch": at_fetch, "fetched_at": fetched,
               "id": cid}
        _IMG_MEM[cid] = rec
        jpg, meta = _img_paths(cid)
        try:
            _write(jpg, body)
            _write(meta, json.dumps({k: v for k, v in rec.items() if k != "body"}).encode())
        except OSError:
            pass                    # a full disk costs the offline copy, not the picture
        return _aged(rec, fetched, "caltrans")
