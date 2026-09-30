#!/usr/bin/env python3
"""Build the meetup demo's map, map.json, from OpenStreetMap and the route.

    python3 tools/demo_map.py --roads R --water W --freeways F --drive D --out O
                              [--detail S,W,N,E]

R, W and F are Overpass answers (`out geom`, so every way carries its own
`geometry` and `tags`): the roads of the detail box, the coast and water, and
the motorways and trunk roads of the whole route. D is the demo's drive.json
(`route`: [[lat, lon], ...]) or OSRM's answer (`routes[0].geometry`:
[[lon, lat], ...]); `--route` is the same flag. The origin is the route's
first point.

The output is the file demo/js/map.js draws and nothing else reads:

    { "version": 1, "origin": [lat0, lon0], "bbox": [xmin, ymin, xmax, ymax],
      "layers": { "ocean": [poly], "coast": [line], "water": [poly], "rivers": [line],
                  "roads": { "motorway": [line], "trunk", "primary",
                             "secondary", "tertiary", "minor" } },
      "labels": [{ "x", "y", "text", "class" }], "route": [[x, y], ...] }

x is metres east of the origin and y metres north, x = (lon - lon0) *
cos(lat0) * 111320 and y = (lat - lat0) * 110540, rounded to 0.5 m: an
equirectangular projection, which map.js repeats exactly for the car, so map
and car always agree. Its own distortion is east-west against north-south, by
cos(lat) / cos(lat0): under 0.3% across the detail box, 1.4% by San Francisco.

It is a private asset, like the data it is made from: it goes to
share/assets/private/demo/map.json and is never committed. The same inputs give
the same bytes.

The map data is (c) OpenStreetMap contributors, under the ODbL; every screen
that draws it says so.
"""

import argparse
import json
import math
import os
import sys

K_LON = 111320.0
K_LAT = 110540.0

# The detail box the roads were downloaded for (S, W, N, E). Roads in it are
# drawn by class; outside it the map has only the freeways.
DETAIL = (36.62, -121.88, 36.93, -121.70)

# The map reaches this far past the detail box and the route, then stops.
MARGIN_M = 2000.0

DETAIL_EPS = 2.0        # Douglas-Peucker tolerance, metres, for the detail box
FREEWAY_EPS = 25.0      # and for the freeways beyond it
LABEL_EVERY_M = 3000.0  # one label per name per this many metres
MAX_BYTES = 8_000_000

# highway=* -> the class it is drawn as. Links go with their road, so a slip
# road is drawn as wide as what it joins. Anything else (service roads,
# footways, tracks) is not drawn.
CLASSES = {
    "motorway": "motorway", "motorway_link": "motorway",
    "trunk": "trunk", "trunk_link": "trunk",
    "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "tertiary", "tertiary_link": "tertiary",
    "unclassified": "minor", "residential": "minor", "living_street": "minor",
}
# waterway=* drawn as a line. Drains and ditches are not: at 3 m a pixel they
# are hatching across the fields, not water anybody would look for.
RIVERS = ("river", "stream", "canal")
ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary", "minor"]
LABELLED = ("primary", "trunk")


# ---------------------------------------------------------------- geometry
def half(v):
    """Rounded to 0.5 m, and an int when it is whole, so the file says 12 not 12.0."""
    r = round(v * 2) / 2
    return int(r) if r == int(r) else r


def rnd(pts):
    out = []
    for x, y in pts:
        p = [half(x), half(y)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def simplify(pts, eps):
    """Douglas-Peucker, iteratively: a coastline has thousands of points."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        worst, at = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / L
            if d > worst:
                worst, at = d, i
        if at >= 0 and worst > eps:
            keep[at] = True
            stack.append((a, at))
            stack.append((at, b))
    return [p for p, k in zip(pts, keep) if k]


def clip_segment(p, q, box):
    """Liang-Barsky: the part of segment pq inside box, or None."""
    x0, y0, x1, y1 = box
    (px, py), (qx, qy) = p, q
    dx, dy = qx - px, qy - py
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        if pp == 0:
            if qq < 0:
                return None
            continue
        t = qq / pp
        if pp < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    # The ends themselves when they are inside, not recomputed: p + (q - p)
    # is not always q in floating point, and clip_line compares them.
    a = p if t0 == 0.0 else (px + t0 * dx, py + t0 * dy)
    b = q if t1 == 1.0 else (px + t1 * dx, py + t1 * dy)
    return a, b


def clip_line(pts, box):
    """A polyline cut to the box: the runs of it inside, each ending on the edge."""
    runs, cur = [], []
    for p, q in zip(pts, pts[1:]):
        s = clip_segment(p, q, box)
        if s is None:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
            continue
        a, b = s
        if cur and (abs(cur[-1][0] - a[0]) > 1e-9 or abs(cur[-1][1] - a[1]) > 1e-9):
            if len(cur) > 1:
                runs.append(cur)
            cur = []
        if not cur:
            cur = [a]
        cur.append(b)
        if b != q:  # it left the box here
            runs.append(cur)
            cur = []
    if len(cur) > 1:
        runs.append(cur)
    return runs


def clip_poly(pts, box):
    """Sutherland-Hodgman against an axis-aligned box. Returns a closed ring or []."""
    x0, y0, x1, y1 = box
    ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else list(pts)
    edges = (
        (lambda p: p[0] >= x0, lambda p, q: (x0, p[1] + (q[1] - p[1]) * (x0 - p[0]) / (q[0] - p[0]))),
        (lambda p: p[0] <= x1, lambda p, q: (x1, p[1] + (q[1] - p[1]) * (x1 - p[0]) / (q[0] - p[0]))),
        (lambda p: p[1] >= y0, lambda p, q: (p[0] + (q[0] - p[0]) * (y0 - p[1]) / (q[1] - p[1]), y0)),
        (lambda p: p[1] <= y1, lambda p, q: (p[0] + (q[0] - p[0]) * (y1 - p[1]) / (q[1] - p[1]), y1)),
    )
    for inside, cross in edges:
        if not ring:
            return []
        out = []
        prev = ring[-1]
        for cur in ring:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
        ring = out
    if len(ring) < 3:
        return []
    return ring + [ring[0]]


def length(pts):
    return sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))


def at_distance(pts, d):
    """The point d metres along a polyline."""
    for p, q in zip(pts, pts[1:]):
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        if d <= L and L > 0:
            t = d / L
            return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        d -= L
    return pts[-1]


# ---------------------------------------------------------------- inputs
def ways(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    out = [e for e in doc.get("elements", [])
           if e.get("type") == "way" and len(e.get("geometry") or []) >= 2]
    out.sort(key=lambda e: e["id"])
    return out


def route_of(path):
    """[[lat, lon], ...] from drive.json's `route`, or from OSRM's answer."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if isinstance(doc.get("route"), list) and doc["route"]:
        pts = [(float(a), float(b)) for a, b in doc["route"]]
    elif doc.get("routes"):
        pts = [(float(lat), float(lon)) for lon, lat in doc["routes"][0]["geometry"]["coordinates"]]
    else:
        raise SystemExit(f"{path}: neither a drive.json `route` nor an OSRM `routes[0].geometry`")
    if len(pts) < 2:
        raise SystemExit(f"{path}: the route has fewer than two points")
    return pts


class Projection:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.cos(math.radians(lat0)) * K_LON

    def __call__(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * K_LAT)

    def way(self, w):
        return [self(g["lat"], g["lon"]) for g in w["geometry"]]

    def box(self, s, w, n, e):
        (x0, y0), (x1, y1) = self(s, w), self(n, e)
        return (x0, y0, x1, y1)


# ---------------------------------------------------------------- the ocean
def join_chains(coast):
    """Coastline ways joined end to start, as OSM draws them: land on the left.
    Returns (points, closed) pairs, points as (lat, lon)."""
    segs = [[(g["lat"], g["lon"]) for g in w["geometry"]] for w in coast]
    starts = {}
    for i, s in enumerate(segs):
        starts.setdefault(s[0], []).append(i)
    ends = {}
    for i, s in enumerate(segs):
        ends.setdefault(s[-1], []).append(i)
    used = [False] * len(segs)
    chains = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        chain = list(segs[i])
        while chain[0] != chain[-1]:            # forward
            nxt = next((j for j in starts.get(chain[-1], []) if not used[j]), None)
            if nxt is None:
                break
            used[nxt] = True
            chain += segs[nxt][1:]
        while chain[0] != chain[-1]:            # and back
            prv = next((j for j in ends.get(chain[0], []) if not used[j]), None)
            if prv is None:
                break
            used[prv] = True
            chain = segs[prv][:-1] + chain
        chains.append((chain, chain[0] == chain[-1]))
    return chains


def ocean(coast, proj, box):
    """The sea inside `box`, as one closed polygon, and the shore as lines.

    The coastline chain that crosses the box -- open, so not an island, and the
    one with the most points inside -- runs with the land on its left, so the
    water is on its right. It is closed around the outside of the box, turning
    the way that keeps the water on the right (clockwise), which for this coast
    means round the western edge; then the whole ring is clipped to the box."""
    x0, y0, x1, y1 = box

    def within(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    best, score = None, 0
    for chain, closed in join_chains(coast):
        if closed:
            continue
        pts = [proj(*p) for p in chain]
        n = sum(1 for p in pts if within(p))
        if n > score:
            best, score = pts, n
    if not best:
        return [], []
    ins = [i for i, p in enumerate(best) if within(p)]
    seg = best[max(ins[0] - 1, 0): ins[-1] + 2]
    seg = simplify(seg, DETAIL_EPS)

    # A ring around everything, well outside the box and the segment.
    xs = [p[0] for p in seg] + [x0, x1]
    ys = [p[1] for p in seg] + [y0, y1]
    pad = 1000.0
    R = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    W, H = R[2] - R[0], R[3] - R[1]

    def to_ring(p):
        """Straight out from the box to the ring, and where that is on it."""
        x, y = p
        if y < y0:
            q = (x, R[1])
        elif y > y1:
            q = (x, R[3])
        elif x < x0:
            q = (R[0], y)
        elif x > x1:
            q = (R[2], y)
        else:  # a chain that ends inside the box: out through the nearest side
            d = min((x - x0, "w"), (x1 - x, "e"), (y - y0, "s"), (y1 - y, "n"))[1]
            q = {"w": (R[0], y), "e": (R[2], y), "s": (x, R[1]), "n": (x, R[3])}[d]
        return q

    def perim(q):
        """Clockwise distance round the ring from its north-west corner."""
        x, y = q
        if y == R[3]:
            return x - R[0]
        if x == R[2]:
            return W + (R[3] - y)
        if y == R[1]:
            return W + H + (R[2] - x)
        return 2 * W + H + (y - R[1])

    corners = [(0.0, (R[0], R[3])), (W, (R[2], R[3])), (W + H, (R[2], R[1])), (2 * W + H, (R[0], R[1]))]
    e_out, s_in = to_ring(seg[-1]), to_ring(seg[0])
    a, b = perim(e_out), perim(s_in)
    if b <= a:
        b += 2 * (W + H)
    way = [e_out]
    for lap in (0.0, 2 * (W + H)):
        for d, c in corners:
            if a < d + lap < b:
                way.append(c)
    way.append(s_in)
    ring = seg + way + [seg[0]]
    poly = clip_poly(ring, box)
    # The shore itself, for the renderer's surf line: the chain, cut to the box.
    shore = [r for r in (rnd(run) for run in clip_line(seg, box)) if len(r) >= 2]
    return ([rnd(poly)] if poly else []), shore


# ---------------------------------------------------------------- the map
def labels(named):
    """One label per name per 3 km, along named primary and trunk roads."""
    out = []
    for cls, name, pts in named:
        L = length(pts)
        if L < 200:
            continue
        spots = [L / 2] if L < LABEL_EVERY_M else \
            [LABEL_EVERY_M / 2 + k * LABEL_EVERY_M for k in range(int((L - LABEL_EVERY_M / 2) // LABEL_EVERY_M) + 1)]
        for d in spots:
            x, y = at_distance(pts, d)
            if any(lb["text"] == name and math.hypot(lb["x"] - x, lb["y"] - y) < LABEL_EVERY_M for lb in out):
                continue
            out.append({"x": x, "y": y, "text": name, "class": cls})
    for lb in out:
        lb["x"], lb["y"] = half(lb["x"]), half(lb["y"])
    return out


def shield_text(ref):
    """'CA 1' -> '1', 'US 101' -> '101', 'I 280;CA 85' -> '280'."""
    first = ref.split(";")[0].strip()
    return first.split()[-1] if first else ""


def build(roads_path, water_path, freeways_path, route_path, detail=DETAIL):
    route_ll = route_of(route_path)
    lat0, lon0 = route_ll[0]
    proj = Projection(lat0, lon0)
    dbox = proj.box(*detail)

    route = simplify([proj(a, b) for a, b in route_ll], 1.0)
    rx = [p[0] for p in route] + [dbox[0], dbox[2]]
    ry = [p[1] for p in route] + [dbox[1], dbox[3]]
    bbox = (min(rx) - MARGIN_M, min(ry) - MARGIN_M, max(rx) + MARGIN_M, max(ry) + MARGIN_M)

    lines = {c: [] for c in ORDER}
    named = []
    shields = []
    seen = set()

    def add(w):
        """A road in the detail box, by class at 2 m; beyond it, only the
        motorways and trunk roads, at 25 m. A way in both downloads once."""
        cls = CLASSES.get((w.get("tags") or {}).get("highway"))
        if not cls or w["id"] in seen:
            return
        seen.add(w["id"])
        pts = proj.way(w)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        touches = not (max(xs) < dbox[0] or min(xs) > dbox[2] or max(ys) < dbox[1] or min(ys) > dbox[3])
        if not touches and cls not in ("motorway", "trunk"):
            return
        tags = w.get("tags") or {}
        for run in clip_line(simplify(pts, DETAIL_EPS if touches else FREEWAY_EPS), bbox):
            r = rnd(run)
            if len(r) >= 2:
                lines[cls].append(r)
                if cls in LABELLED and tags.get("name"):
                    named.append((cls, tags["name"], run))
                if cls == "motorway" and tags.get("ref") and not (tags.get("highway") or "").endswith("_link"):
                    shields.append(("shield", shield_text(tags["ref"]), run))

    for w in ways(roads_path):
        add(w)
    for w in ways(freeways_path):
        add(w)

    water, rivers, coast = [], [], []
    for w in ways(water_path):
        t = w.get("tags") or {}
        if t.get("natural") == "coastline":
            coast.append(w)
        elif t.get("natural") == "water" and w["geometry"][0] == w["geometry"][-1]:
            poly = clip_poly(simplify(proj.way(w), DETAIL_EPS), bbox)
            if len(poly) >= 4:
                water.append(rnd(poly))
        elif t.get("waterway") in RIVERS:
            for run in clip_line(simplify(proj.way(w), DETAIL_EPS), bbox):
                r = rnd(run)
                if len(r) >= 2:
                    rivers.append(r)

    sea, shore = ocean(coast, proj, dbox)
    return {
        "version": 1,
        "origin": [lat0, lon0],
        "bbox": [half(v) for v in bbox],
        "layers": {
            "ocean": sea,
            "coast": shore,
            "water": water,
            "rivers": rivers,
            "roads": lines,
        },
        "labels": labels(named) + labels(shields),
        "route": rnd(route),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--roads", required=True)
    ap.add_argument("--water", required=True)
    ap.add_argument("--freeways", required=True)
    ap.add_argument("--drive", "--route", dest="route", required=True,
                    help="drive.json, or OSRM's answer")
    ap.add_argument("--out", required=True)
    ap.add_argument("--detail", default=",".join(str(v) for v in DETAIL),
                    help="the roads' download box, S,W,N,E (default %(default)s)")
    a = ap.parse_args(argv)
    detail = tuple(float(v) for v in a.detail.split(","))
    if len(detail) != 4:
        ap.error("--detail is S,W,N,E")
    m = build(a.roads, a.water, a.freeways, a.route, detail)
    body = json.dumps(m, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    if len(body) >= MAX_BYTES:
        print(f"map.json would be {len(body)} bytes, over the {MAX_BYTES}-byte budget; not written",
              file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, a.out)
    L = m["layers"]
    counts = ", ".join(f"{k} {len(v)}" for k, v in L["roads"].items())
    print(f"{a.out}: {len(body)} bytes ({len(body) / 1e6:.2f} MB)")
    print(f"  roads: {counts}")
    print(f"  ocean {len(L['ocean'])} ({sum(len(p) for p in L['ocean'])} points), water {len(L['water'])}, "
          f"rivers {len(L['rivers'])}, labels {len(m['labels'])}, route {len(m['route'])} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
