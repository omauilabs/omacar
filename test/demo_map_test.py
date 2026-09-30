#!/usr/bin/env python3
"""The meetup demo's map builder, tools/demo_map.py, against a hand-made map.

The builder turns three OpenStreetMap downloads and the demo's route into the
one file the demo's Canvas renderer draws (demo/js/map.js). Everything it gets
wrong would be drawn, not raised: a road in the wrong class is a residential
street drawn as a freeway, an ocean closed on the wrong side floods the town,
a way that runs 45 km out of the map is a line to nowhere, and a builder that
is not deterministic changes a private asset every time somebody runs it.

test/fixtures/demo/osm-mini.json stands in for all three downloads at once, as
Overpass gives them (`out geom`): every road class, a coastline in two ways
listed out of order with an islet beside it, a lake, a river, a stream, and two
freeways outside the detail box, one of which runs far out of the map. The
route is written here, once as the demo's drive.json and once as OSRM answers.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "demo_map.py")
MINI = os.path.join(ROOT, "test", "fixtures", "demo", "osm-mini.json")
DETAIL = "36.70,-121.82,36.74,-121.78"      # S, W, N, E: the fixture's detail box

PASS, FAIL = [], []


def ok(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   {'ok ' if cond else 'FAIL'}  {name}")


def bad(name, why):
    FAIL.append(name)
    print(f"   FAIL  {name}: {why}")


def head(name):
    print(f"\n  {name}\n")


# The route: north from Marina's latitude, out of the detail box, like the
# real drive does. Its first point is the map's origin.
ROUTE = [[36.715, -121.800], [36.725, -121.797], [36.74, -121.795], [36.78, -121.795]]
DRIVE = {"version": 1, "name": "test", "route": ROUTE,
         "destination": {"label": "Somewhere", "lat": 36.78, "lon": -121.795}}
OSRM = {"code": "Ok", "routes": [{"geometry": {"type": "LineString",
                                               "coordinates": [[lon, lat] for lat, lon in ROUTE]}}]}

tmp = tempfile.mkdtemp(prefix="omacar-demo-map-test-")


def build(route_doc, name, flag="--drive"):
    rpath = os.path.join(tmp, name + "-route.json")
    with open(rpath, "w") as f:
        json.dump(route_doc, f)
    out = os.path.join(tmp, name + ".json")
    p = subprocess.run([sys.executable, TOOL, "--roads", MINI, "--water", MINI, "--freeways", MINI,
                        flag, rpath, "--detail", DETAIL, "--out", out],
                       capture_output=True, text=True, timeout=60)
    return p, out


def proj(lat, lon, origin):
    lat0, lon0 = origin
    return ((lon - lon0) * math.cos(math.radians(lat0)) * 111320, (lat - lat0) * 110540)


def inside(pt, poly):
    """Even-odd point in polygon."""
    x, y = pt
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


head("It builds")
if not os.path.exists(TOOL):
    bad("tools/demo_map.py exists", "missing")
    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
    sys.exit(1)

p, out = build(DRIVE, "a")
ok("the builder exits 0", p.returncode == 0)
if p.returncode != 0:
    print(p.stdout, p.stderr)
    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
    sys.exit(1)
size = os.path.getsize(out)
ok("it prints the file's size", str(size) in p.stdout or f"{size / 1e6:.2f} MB" in p.stdout
   or f"{size / 1e3:.1f} kB" in p.stdout)
ok("the file is under 8 MB", size < 8e6)
with open(out) as f:
    m = json.load(f)

head("The shape")
ok("version 1", m.get("version") == 1)
ok("the origin is the drive's first point", m.get("origin") == ROUTE[0])
ok("bbox is [xmin, ymin, xmax, ymax]", len(m.get("bbox", [])) == 4
   and m["bbox"][0] < m["bbox"][2] and m["bbox"][1] < m["bbox"][3])
L = m.get("layers", {})
for k in ("ocean", "water", "rivers", "roads"):
    ok(f"layers.{k} is there", k in L)
roads = L.get("roads", {})
for cls in ("motorway", "trunk", "primary", "secondary", "tertiary", "minor"):
    ok(f"roads.{cls} has lines", len(roads.get(cls, [])) > 0)
ok("nothing else is a road class", set(roads) <= {"motorway", "trunk", "primary", "secondary", "tertiary", "minor"})

origin = m["origin"]
P = lambda lat, lon: proj(lat, lon, origin)  # noqa: E731


def has_line_near(lines, pt, tol=3.0):
    return any(math.hypot(x - pt[0], y - pt[1]) <= tol for ln in lines for x, y in ln)


head("Roads, by class")
mway = roads.get("motorway", [])
ok("a motorway link is a motorway", has_line_near(mway, P(36.721, -121.793)))
ok("a trunk link is a trunk", has_line_near(roads.get("trunk", []), P(36.731, -121.789)))
ok("residential, unclassified and living streets are minor",
   all(has_line_near(roads.get("minor", []), P(*pt)) for pt in
       [(36.715, -121.79), (36.735, -121.79), (36.705, -121.797)]))
ok("a footway is not drawn",
   not any(has_line_near(v, P(36.716, -121.795), 1.0) for v in roads.values()))
ok("a freeway outside the detail box joins motorway", has_line_near(mway, P(36.775, -121.79)))
ok("a freeway in both downloads is drawn once",
   sum(1 for ln in mway if has_line_near([ln], P(36.69, -121.795), 1.0) or has_line_near([ln], P(36.75, -121.795), 1.0)) == 1)
reind = [ln for ln in roads.get("minor", []) if has_line_near([ln], P(36.718, -121.799))]
ok("a straight street of 400 points is simplified to its two ends", len(reind) == 1 and len(reind[0]) == 2)
# A 200-point curve with a 10 m wiggle: 25 m flattens it, 2 m would not.
fw = [ln for ln in mway if has_line_near([ln], P(36.775, -121.79))]
ok("a freeway outside the detail box is simplified at 25 m", len(fw) == 1 and len(fw[0]) == 2)

head("Points")
allpts = []
for layer in ("ocean", "water", "rivers"):
    for g in L.get(layer, []):
        allpts += g
for v in roads.values():
    for g in v:
        allpts += g
allpts += m.get("route", [])
allpts += [[lb["x"], lb["y"]] for lb in m.get("labels", [])]
ok("every point is rounded to 0.5 m", all((c * 2) == int(c * 2) for pt in allpts for c in pt))
x0, y0, x1, y1 = m["bbox"]
far = [pt for pt in allpts if pt[0] < x0 - 1000 or pt[0] > x1 + 1000 or pt[1] < y0 - 1000 or pt[1] > y1 + 1000]
ok("no coordinate is outside the bbox by more than 1 km", not far)
far_road = [ln for ln in roads.get("trunk", []) if has_line_near([ln], P(36.77, -121.785))]
ok("a way running 45 km out of the map is cut at its edge, not dropped",
   len(far_road) == 1 and abs(max(y for _, y in far_road[0]) - y1) <= 0.5)

head("The ocean")
ocean = L.get("ocean", [])
ok("there is one ocean polygon", len(ocean) == 1)
if ocean:
    poly = ocean[0]
    ok("it is closed", poly[0] == poly[-1] and len(poly) >= 4)
    # The coastline, projected: x as a function of y along it.
    coast = [P(*pt) for pt in [(36.75, -121.812), (36.735, -121.809), (36.72, -121.811),
                               (36.705, -121.808), (36.69, -121.810)]]

    def coast_x(y):
        for (xa, ya), (xb, yb) in zip(coast, coast[1:]):
            if yb <= y <= ya:
                return xa + (xb - xa) * (y - ya) / (yb - ya)
        return None
    east = [pt for pt in poly if coast_x(pt[1]) is not None and pt[0] > coast_x(pt[1]) + 1]
    ok("every point of it lies on or west of the coastline", not east)
    ring = [tuple(pt) for pt in poly[:-1]]
    ok("the sea west of the coast is inside it", inside(P(36.72, -121.815), ring))
    ok("the land east of the coast is not", not inside(P(36.72, -121.80), ring))
    ok("the islet's ring did not become the ocean", inside(P(36.738, -121.819), ring)
       and inside(P(36.702, -121.819), ring))
    ok("it covers the box's western edge", any(abs(pt[0] - P(36.72, -121.82)[0]) < 1 for pt in poly))

head("Water")
ok("a lake is a closed polygon", any(len(g) >= 4 and g[0] == g[-1] and has_line_near([g], P(36.708, -121.790))
                                     for g in L.get("water", [])))
ok("a river and a stream are lines", len(L.get("rivers", [])) == 2)

head("Labels")
labels = m.get("labels", [])
names = {(lb["text"], lb["class"]) for lb in labels}
ok("a named primary is labelled", ("Reservation Road", "primary") in names)
ok("a named trunk is labelled", ("Salinas Road", "trunk") in names)
ok("secondary and minor roads are not", not any(lb["text"] in ("Del Monte Boulevard", "Carmel Avenue") for lb in labels))
same = [(a, b) for i, a in enumerate(labels) for b in labels[i + 1:]
        if a["text"] == b["text"] and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < 3000]
ok("one label per name per 3 km", not same)

head("The route")
route = m.get("route", [])
ok("it is the drive's route, projected",
   len(route) == len(ROUTE) and all(math.hypot(a[0] - b[0], a[1] - b[1]) <= 0.5
                                    for a, b in zip(route, [P(*q) for q in ROUTE])))
ok("it starts at the origin", route and route[0] == [0, 0])

head("Determinism, and OSRM's route")
p2, out2 = build(DRIVE, "b")
with open(out, "rb") as f1, open(out2, "rb") as f2:
    ok("the same inputs give the same bytes", p2.returncode == 0 and f1.read() == f2.read())
p3, out3 = build(OSRM, "c", flag="--route")
with open(out, "rb") as f1, open(out3, "rb") as f3:
    ok("OSRM's answer, given as --route, gives the same map", p3.returncode == 0 and f1.read() == f3.read())

subprocess.run(["rm", "-rf", tmp])
print(f"\n  {len(PASS)} passed, {len(FAIL)} failed\n")
sys.exit(1 if FAIL else 0)
