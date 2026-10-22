#!/usr/bin/env python3
"""The posted speed limit under the car, from a map that lives on the tablet.

Everything here works with no network and no GPS receiver today: the position
comes from lib/location.py (gpsd, or its labelled simulator), the roads from a
small SQLite file built ahead of time on a desktop (tools/speedlimits_build.py).

THE DATA IS OPENSTREETMAP'S: (c) OpenStreetMap contributors, under the Open
Database Licence (ODbL) 1.0. The attribution travels with the database (its
`meta` table), through /api/speedlimit, and onto the screen. Nothing of it is
kept in git. See doc/speed-limits.md.

THREE RULES.
  * Say nothing rather than guess. A limit is shown only when the match is
    confident (a road within 15 m, going the way the car is going, with no
    near neighbour that says something different). Otherwise there is no sign.
  * Posted is not default. A way tagged `maxspeed` is "posted". A way with no
    tag may get a deliberately short list of statutory defaults, marked
    "default"; the screen draws it differently and never warns about it.
  * A bad fix never moves the answer. Changing road takes three fixes in a
    row (hysteresis), so a frontage road, an overpass or an intersection does
    not make the sign flicker.

THE DATABASE.
    meta(key, value)
    chunks(id, way_id, mph_f, mph_b, source, oneway, name, hwy, pts)
    chunk_idx            R*Tree over each chunk's bounding box
A chunk is a run of one way's nodes at most ~250 m long (so the bounding boxes
stay small and the R*Tree stays sharp). `pts` is little-endian int32 pairs of
lat, lon in 1e-7 degrees. `mph_f` is the limit along the way's direction,
`mph_b` against it (NULL on a one-way: there is no against).

    omacar speedlimits status            the database and what the matcher says now
    omacar speedlimits fetch [--pbf F]   get or build the database (doc/speed-limits.md)
    omacar speedlimits lookup LAT LON [COURSE]
    omacar speedlimits sim [seconds]     a simulated drive over the sample roads
"""

import json
import math
import os
import sqlite3
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

DATA_DIR = os.path.expanduser(
    os.environ.get("XDG_DATA_HOME", "~/.local/share") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
DB_PATH = os.path.join(DATA_DIR, "speedlimits.db")
CONFIG = os.path.join(CONFIG_DIR, "speedlimits.json")
SAMPLE_OSM = os.path.join(ROOT, "test", "fixtures", "speedlimits.osm")

SCHEMA_VERSION = 1
ATTRIBUTION = ("(c) OpenStreetMap contributors. Open Database Licence (ODbL) 1.0: "
               "https://www.openstreetmap.org/copyright")

M_PER_DEG = 6371008.8 * math.pi / 180.0
KMH_PER_MPH = 1.609344
SCALE = 1e7                      # stored degrees
CHUNK_M = 250.0                  # longest run of a way in one R*Tree entry

# What counts as a road, and the (short, conservative) defaults for the ones
# with no maxspeed tag. California's prima facie limit in a residence district
# is 25 mph (CVC 22352). Nothing else is guessed: a freeway's 65 is a ceiling,
# not what is posted, and a rural road could be anything. Add a class here only
# with a statute behind it.
ROAD_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link", "primary",
                "primary_link", "secondary", "secondary_link", "tertiary",
                "tertiary_link", "unclassified", "residential", "living_street",
                "service", "road"}
DEFAULTS = {"residential": 25}
IMPLIED_ONEWAY = {"motorway", "motorway_link"}

# Matching.
RADIUS_M = 25.0            # candidates within this of the fix
ALIGN_GATE = 60.0          # degrees: a road more across than this is not "the road I'm on"
HEADING_W = 0.3            # metres of distance one degree of misalignment costs
HIGH_DIST_M = 15.0         # confident only when the road is this close...
HIGH_ALIGN = 35.0          # ...and this aligned with the way the car is going
AMBIG_M = 8.0              # a different limit this nearly as good as the winner: unsure
CONFIRM = 3                # fixes in a row to take a new road, or to drop one
MIN_COURSE_MS = 2.0        # slower than this a GPS course is noise


# ---- pure: parsing and packing ---------------------------------------------------------

def parse_maxspeed(value):
    """An OSM maxspeed value as whole mph, or None when it is not a plain number.

    "35 mph" -> 35. "50" and "50 km/h" -> 31 (OSM's bare number is km/h).
    "signals", "none", "walk", "US:urban", "25;35" and anything else that is
    not one number -> None, because a wrong limit is worse than none.
    """
    if value is None:
        return None
    s = str(value).strip().lower().replace(" ", "")
    if not s or ";" in s:
        return None
    mult = 1.0
    if s.endswith("mph"):
        s = s[:-3]
    elif s.endswith("km/h"):
        s = s[:-4]
        mult = 1 / KMH_PER_MPH
    elif s.endswith("kmh"):
        s = s[:-3]
        mult = 1 / KMH_PER_MPH
    elif s.replace(".", "", 1).isdigit():
        mult = 1 / KMH_PER_MPH
    try:
        n = float(s) * mult
    except ValueError:
        return None
    n = int(round(n))
    return n if 5 <= n <= 90 else None


def pack_pts(points):
    flat = []
    for lat, lon in points:
        flat += [int(round(lat * SCALE)), int(round(lon * SCALE))]
    return struct.pack("<%di" % len(flat), *flat)


def unpack_pts(blob):
    n = len(blob) // 4
    flat = struct.unpack("<%di" % n, blob)
    return [(flat[i] / SCALE, flat[i + 1] / SCALE) for i in range(0, n, 2)]


def _len_m(a, b):
    dy = (b[0] - a[0]) * M_PER_DEG
    dx = (b[1] - a[1]) * M_PER_DEG * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def split_chunks(points, max_m=CHUNK_M):
    """Cut a polyline into runs of at most max_m (sharing end points)."""
    out, cur, run = [], [points[0]], 0.0
    for p in points[1:]:
        d = _len_m(cur[-1], p)
        if len(cur) > 1 and run + d > max_m:
            out.append(cur)
            cur, run = [cur[-1]], 0.0
        cur.append(p)
        run += d
    if len(cur) >= 2:
        out.append(cur)
    return out


def rows_for_way(way_id, tags, points):
    """The chunk rows one OSM way becomes: [] when it is not a road we know
    anything about. `points` is [(lat, lon), ...] in the way's own direction.

    A row is a dict: way_id, mph_f, mph_b, source, oneway, name, hwy, points.
    """
    hwy = tags.get("highway")
    if hwy not in ROAD_CLASSES or len(points) < 2:
        return []
    ow = str(tags.get("oneway", "")).lower()
    reverse = ow == "-1"
    oneway = ow in ("yes", "true", "1", "-1")
    if not oneway and ow not in ("no", "false", "0"):
        oneway = hwy in IMPLIED_ONEWAY or tags.get("junction") in ("roundabout", "circular")
    base = parse_maxspeed(tags.get("maxspeed"))
    fwd = parse_maxspeed(tags.get("maxspeed:forward"))
    bwd = parse_maxspeed(tags.get("maxspeed:backward"))
    f = fwd if fwd is not None else base
    b = bwd if bwd is not None else base
    pts = list(points)
    if reverse:                     # traffic runs against the way: make "forward" true
        pts.reverse()
        f, b = b, f
    if f is None and b is None:
        d = DEFAULTS.get(hwy)
        if d is None:
            return []
        f = b = d
        source = "default"
    else:
        source = "posted"
        f = f if f is not None else b
        b = b if b is not None else f
    if oneway:
        b = None
    name = (tags.get("name") or tags.get("ref") or "")[:60]
    return [{"way_id": int(way_id), "mph_f": f, "mph_b": b, "source": source,
             "oneway": 1 if oneway else 0, "name": name, "hwy": hwy, "points": c}
            for c in split_chunks(pts)]


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, way_id INTEGER NOT NULL, mph_f INTEGER, mph_b INTEGER,
  source TEXT NOT NULL CHECK (source IN ('posted','default')), oneway INTEGER NOT NULL,
  name TEXT, hwy TEXT, pts BLOB NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_idx USING rtree(id, min_lat, max_lat, min_lon, max_lon);
"""


def create_db(path):
    if os.path.exists(path):
        os.remove(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def add_rows(db, rows):
    """Insert chunk rows and their R*Tree boxes. Returns the number added."""
    n = 0
    for r in rows:
        cur = db.execute(
            "INSERT INTO chunks (way_id, mph_f, mph_b, source, oneway, name, hwy, pts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (r["way_id"], r["mph_f"], r["mph_b"], r["source"], r["oneway"], r["name"],
             r["hwy"], pack_pts(r["points"])))
        lats = [p[0] for p in r["points"]]
        lons = [p[1] for p in r["points"]]
        db.execute("INSERT INTO chunk_idx VALUES (?,?,?,?,?)",
                   (cur.lastrowid, min(lats), max(lats), min(lons), max(lons)))
        n += 1
    return n


def finish_db(db, **meta):
    meta.setdefault("schema_version", SCHEMA_VERSION)
    meta.setdefault("attribution", ATTRIBUTION)
    meta.setdefault("defaults", json.dumps(DEFAULTS))
    meta["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    meta["chunks"] = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    db.commit()


# ---- pure: geometry and choosing --------------------------------------------------------

def angdiff(a, b):
    """Smallest angle between two bearings, 0..180."""
    d = abs((a - b) % 360.0)
    return 360.0 - d if d > 180.0 else d


def seg_bearing(a, b):
    dy = (b[0] - a[0])
    dx = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def point_seg_m(lat, lon, a, b):
    """Metres from (lat, lon) to the segment a-b, on a local flat projection."""
    k = math.cos(math.radians(lat))
    ax, ay = (a[1] - lon) * M_PER_DEG * k, (a[0] - lat) * M_PER_DEG
    bx, by = (b[1] - lon) * M_PER_DEG * k, (b[0] - lat) * M_PER_DEG
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    return math.hypot(ax + t * dx, ay + t * dy)


class Cand:
    """One way near the fix, judged against the way the car is going."""
    __slots__ = ("way_id", "name", "hwy", "source", "mph", "dist", "diff", "score", "oneway")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self):
        return {"way_id": self.way_id, "name": self.name, "mph": self.mph, "source": self.source,
                "dist_m": round(self.dist, 1), "off_deg": None if self.diff is None else round(self.diff)}


def judge(row, lat, lon, course):
    """How good a match one chunk row is. `course` is None when the car is
    too slow for a heading to mean anything. Returns a Cand or None when the
    chunk is not within reach or runs the wrong way."""
    pts = row["points"]
    best, bd = None, 1e18
    for i in range(len(pts) - 1):
        d = point_seg_m(lat, lon, pts[i], pts[i + 1])
        if d < bd:
            bd, best = d, (pts[i], pts[i + 1])
    if bd > RADIUS_M:
        return None
    brg = seg_bearing(*best)
    mph, diff = row["mph_f"], None
    if course is not None:
        df, db_ = angdiff(course, brg), angdiff(course, (brg + 180.0) % 360.0)
        if row["oneway"]:
            diff = df
        else:
            diff = min(df, db_)
            if db_ < df and row["mph_b"] is not None:
                mph = row["mph_b"]
        if diff > ALIGN_GATE:
            return None
    score = bd + (HEADING_W * diff if diff is not None else 0.0)
    return Cand(way_id=row["way_id"], name=row["name"], hwy=row["hwy"], source=row["source"],
                mph=mph, dist=bd, diff=diff, score=score, oneway=row["oneway"])


# ---- the database --------------------------------------------------------------------

class Db:
    """A read-only handle on speedlimits.db."""

    def __init__(self, path):
        self.path = path
        self.con = sqlite3.connect("file:" + path + "?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.meta = dict(self.con.execute("SELECT key, value FROM meta").fetchall())

    def close(self):
        self.con.close()

    def rows_near(self, lat, lon, radius_m=RADIUS_M):
        """Chunk rows whose R*Tree box touches the square around the point."""
        dlat = radius_m / M_PER_DEG
        dlon = radius_m / (M_PER_DEG * max(0.01, math.cos(math.radians(lat))))
        with self.lock:
            got = self.con.execute(
                "SELECT c.way_id, c.mph_f, c.mph_b, c.source, c.oneway, c.name, c.hwy, c.pts"
                " FROM chunk_idx i JOIN chunks c ON c.id = i.id"
                " WHERE i.max_lat >= ? AND i.min_lat <= ? AND i.max_lon >= ? AND i.min_lon <= ?",
                (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
        return [{"way_id": r["way_id"], "mph_f": r["mph_f"], "mph_b": r["mph_b"],
                 "source": r["source"], "oneway": r["oneway"], "name": r["name"],
                 "hwy": r["hwy"], "points": unpack_pts(r["pts"])} for r in got]

    def candidates(self, lat, lon, course=None):
        """Ways within RADIUS_M of the point, best first. One entry per way
        (its closest chunk)."""
        best = {}
        for row in self.rows_near(lat, lon):
            c = judge(row, lat, lon, course)
            if c and (c.way_id not in best or c.score < best[c.way_id].score):
                best[c.way_id] = c
        return sorted(best.values(), key=lambda c: c.score)


# ---- the matcher: map-matching lite with hysteresis --------------------------------------

class Matcher:
    """Follows the car along the roads. Feed it one fix at a time.

    It LOCKS onto a way only after the same way has been the best match for
    CONFIRM fixes in a row, and lets go of it only when a different way has
    been best CONFIRM fixes in a row (or it has been out of reach that long).
    While the car is too slow for its heading to mean anything it HOLDS: it
    never changes road on a stopped car's wobble.
    """

    def __init__(self, db):
        self.db = db
        self.reset()

    def reset(self):
        self.locked = None       # way_id
        self.chal = None         # way_id challenging the lock
        self.chal_n = 0
        self.out_n = 0           # fixes the locked way has been out of reach

    def _challenge(self, way_id):
        if way_id == self.chal:
            self.chal_n += 1
        else:
            self.chal, self.chal_n = way_id, 1
        return self.chal_n >= CONFIRM

    def feed(self, lat, lon, course=None, speed_ms=None):
        moving = (course is not None and speed_ms is not None and speed_ms >= MIN_COURSE_MS)
        cands = self.db.candidates(lat, lon, course if moving else None)
        by = {c.way_id: c for c in cands}
        best = cands[0] if cands else None
        if self.locked is None:
            if best is not None and self._challenge(best.way_id):
                self.locked, self.chal, self.chal_n, self.out_n = best.way_id, None, 0, 0
            elif best is None:
                self.chal, self.chal_n = None, 0
        elif self.locked in by:
            self.out_n = 0
            if best.way_id != self.locked and moving:
                if self._challenge(best.way_id):
                    self.locked, self.chal, self.chal_n = best.way_id, None, 0
            else:
                self.chal, self.chal_n = None, 0
        else:
            self.out_n += 1
            if best is not None and self._challenge(best.way_id):
                self.locked, self.chal, self.chal_n, self.out_n = best.way_id, None, 0, 0
            elif self.out_n >= CONFIRM and best is None:
                self.locked, self.chal, self.chal_n = None, None, 0
        return self.verdict(by, moving)

    def verdict(self, by, moving):
        """What the sign may say. Anything short of confident has limit None."""
        cur = by.get(self.locked) if self.locked is not None else None
        out = {"limit": None, "source": None, "road": None, "confidence": "none",
               "why": None, "candidates": [c.as_dict() for c in sorted(by.values(), key=lambda c: c.score)[:4]]}
        if cur is None:
            out["why"] = "no road in reach" if not by else "not on a road yet"
            if by:
                out["confidence"] = "low"
            return out
        out["road"] = cur.name or None
        out["confidence"] = "low"
        if cur.dist > HIGH_DIST_M:
            out["why"] = "road is %d m away" % round(cur.dist)
        elif moving and cur.diff is not None and cur.diff > HIGH_ALIGN:
            out["why"] = "not lined up with the road"
        elif any(o.way_id != cur.way_id and o.mph != cur.mph and o.score - cur.score < AMBIG_M
                 for o in by.values()):
            out["why"] = "a nearby road says something different"
        else:
            out.update(confidence="high", limit=cur.mph, source=cur.source, why=None)
        return out


# ---- what the app reads ----------------------------------------------------------------

_lock = threading.Lock()
_db = None
_matcher = None
_last_key = None
_last_verdict = None


def db_path():
    return os.environ.get("OMACAR_SPEEDLIMITS_DB") or DB_PATH


def open_db():
    """The database, or None. Reopened when the file is replaced by a fetch."""
    global _db, _matcher
    path = db_path()
    if _db is not None and _db.path != path:
        _db.close()
        _db = _matcher = None
    if _db is None:
        if not os.path.exists(path):
            return None
        try:
            _db = Db(path)
        except sqlite3.Error:
            return None
        _matcher = Matcher(_db)
    return _db


def meta_public(db):
    m = db.meta
    return {"built_at": m.get("built_at"), "source": m.get("source"),
            "chunks": int(m.get("chunks") or 0), "attribution": m.get("attribution", ATTRIBUTION),
            "sample": m.get("sample") == "1"}


def read_public(now=None):
    """The /api/speedlimit body. `limit` is None unless the match is confident;
    `source` says "posted" or "default"; `location_source` says whether the
    position was gpsd or the simulator, and the app writes that on screen."""
    global _last_key, _last_verdict
    import location
    loc = location.read_public(now)
    base = {"present": False, "attribution": ATTRIBUTION, "limit": None, "source": None,
            "confidence": "none", "location_source": loc.get("source"), "fix": bool(loc.get("fix")),
            "gps_mph": None}
    with _lock:
        db = open_db()
        if db is None:
            base["why"] = "No speed-limit data on this tablet (omacar speedlimits fetch)"
            return base
        base.update(present=True, data=meta_public(db))
        if not loc.get("fix") or loc.get("lat") is None:
            _matcher.reset()
            _last_key = _last_verdict = None
            base["why"] = "No GPS fix"
            return base
        key = (loc.get("source"), loc.get("at"))
        if key != _last_key:
            _last_verdict = _matcher.feed(loc["lat"], loc["lon"], loc.get("track"), loc.get("speed_ms"))
            _last_key = key
        sp = loc.get("speed_ms")
        base["gps_mph"] = None if sp is None else round(sp * 3600 / 1609.344, 1)
        base.update({k: _last_verdict[k] for k in ("limit", "source", "road", "confidence", "why")})
        return base


def reset():
    """For tests: forget the database and the matcher."""
    global _db, _matcher, _last_key, _last_verdict
    with _lock:
        if _db is not None:
            _db.close()
        _db = _matcher = _last_key = _last_verdict = None


# ---- the CLI -------------------------------------------------------------------------

def read_osm_xml(path):
    """Ways with their coordinates from a small .osm / .osm.gz (standard
    library only). Yields (way_id, tags, [(lat, lon), ...]). Holds every node
    in memory: for a city, not for a state (use pyosmium for California)."""
    import gzip
    import xml.etree.ElementTree as ET
    opener = gzip.open if path.endswith(".gz") else open
    nodes, ways = {}, []
    with opener(path, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "node":
                nodes[el.get("id")] = (float(el.get("lat")), float(el.get("lon")))
            elif el.tag == "way":
                ways.append((el.get("id"), {t.get("k"): t.get("v") for t in el.findall("tag")},
                             [n.get("ref") for n in el.findall("nd")]))
            if el.tag in ("node", "way"):
                el.clear()
    for wid, tags, refs in ways:
        pts = [nodes[r] for r in refs if r in nodes]
        if len(pts) >= 2:
            yield int(wid), tags, pts


def build_db(ways, path, **meta):
    """Write a database from an iterable of (way_id, tags, points). Returns
    the number of chunks. Shared by the desktop tool and the sample."""
    db = create_db(path)
    try:
        n = 0
        batch = []
        for wid, tags, pts in ways:
            batch += rows_for_way(wid, tags, pts)
            if len(batch) >= 20000:
                n += add_rows(db, batch)
                batch = []
        n += add_rows(db, batch)
        finish_db(db, **meta)
    finally:
        db.close()
    return n


def build_sample(path=None):
    """The tiny sample database from test/fixtures/speedlimits.osm (a made-up
    neighbourhood, so it carries no ODbL data). Marked `sample`."""
    return build_db(read_osm_xml(SAMPLE_OSM), path or db_path(),
                    source="sample: test/fixtures/speedlimits.osm (made up, not OpenStreetMap)",
                    sample=1)


def fetch(argv):
    """Get a database onto this tablet. Never downloads map data unasked.

      omacar speedlimits fetch              download from `db_url` in speedlimits.json
      omacar speedlimits fetch --url URL    download from URL (https:// or file://)
      omacar speedlimits fetch --pbf FILE   build from a Geofabrik .osm.pbf (needs pyosmium;
                                            run on a desktop, then copy the .db over)
    """
    import hashlib
    import shutil
    import urllib.request
    url, pbf, sha = None, None, None
    it = iter(argv)
    for a in it:
        if a == "--url":
            url = next(it, None)
        elif a == "--pbf":
            pbf = next(it, None)
        elif a == "--sha256":
            sha = next(it, None)
    if pbf:
        import subprocess
        tool = os.path.join(ROOT, "tools", "speedlimits_build.py")
        return subprocess.call([sys.executable, tool, pbf, "--out", db_path()])
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    url = url or cfg.get("db_url")
    sha = sha or cfg.get("sha256")
    if not url:
        print("  No database source is configured. Either:\n"
              "    * build one on a desktop (doc/speed-limits.md) and copy it to\n"
              "      %s\n"
              "    * or put {\"db_url\": \"https://...\", \"sha256\": \"...\"} in\n"
              "      %s and run this again.\n"
              "  Map data is OpenStreetMap's (ODbL) and is never shipped in git."
              % (db_path(), CONFIG), file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(db_path()), exist_ok=True)
    tmp = db_path() + ".part"
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
        shutil.copyfileobj(r, out)
    if sha:
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        if h.hexdigest() != sha.lower():
            os.remove(tmp)
            print("  The download's SHA-256 does not match; nothing was installed.", file=sys.stderr)
            return 1
    try:
        probe = Db(tmp)
        ok = int(probe.meta.get("schema_version", 0)) == SCHEMA_VERSION
        probe.close()
    except (sqlite3.Error, ValueError):
        ok = False
    if not ok:
        os.remove(tmp)
        print("  That is not a speed-limit database this version can read.", file=sys.stderr)
        return 1
    reset()
    os.replace(tmp, db_path())
    print("  speed limits installed: " + db_path())
    print("  Map data: " + ATTRIBUTION)
    return 0


def sim(seconds=None, tick=1.0):
    """A labelled simulated drive east along the sample's Main St, past Oak Ave,
    speeding up from 30 to 45 mph. Writes location.json like gpsd would."""
    import location
    lat = 37.77000
    lon0, lon1 = -122.4298, -122.4202
    leg = 0.0
    begin = time.time()
    st = location.blank("sim")
    mph = 30.0
    while seconds is None or time.time() - begin < seconds:
        el = time.time() - begin
        mph = 30.0 if el < 25 else min(45.0, 30.0 + (el - 25) * 1.5)
        v = mph * 1609.344 / 3600.0
        leg += v * tick
        lon = lon0 + leg / (M_PER_DEG * math.cos(math.radians(lat)))
        if lon > lon1:
            leg, lon = 0.0, lon0
        st.update(fix=True, lat=lat, lon=lon, speed_ms=round(v, 2), track=90.0, mode=3,
                  note="Simulated drive on the sample roads")
        location.push_recent(st, time.time())
        location.write(st)
        time.sleep(tick)
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        db = open_db()
        out = {"db": db_path(), "present": db is not None}
        if db:
            out["data"] = meta_public(db)
        out["now"] = read_public()
        print(json.dumps(out, indent=2))
        return 0
    if what == "lookup" and len(argv) >= 3:
        db = open_db()
        if db is None:
            print("  no database: omacar speedlimits fetch", file=sys.stderr)
            return 2
        course = float(argv[3]) if len(argv) > 3 else None
        cs = db.candidates(float(argv[1]), float(argv[2]), course)
        print(json.dumps([c.as_dict() for c in cs], indent=2))
        return 0
    if what == "sim":
        return sim(float(argv[1]) if len(argv) > 1 else None)
    if what == "fetch":
        return fetch(argv[1:])
    if what == "sample":
        print("  sample database: %d chunks -> %s" % (build_sample(), db_path()))
        return 0
    print("  omacar speedlimits: status | fetch | sample | lookup LAT LON [COURSE] | sim [seconds]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
