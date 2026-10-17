#!/usr/bin/env python3
"""Build OmaCar's offline speed-limit database from an OpenStreetMap extract.

RUN THIS ON A DESKTOP, NOT THE TABLET. A state is a lot of ways: California is
a ~1.3 GB .osm.pbf and the build wants several GB of RAM and a few minutes.
Copy the resulting speedlimits.db to the tablet (omacar speedlimits fetch
--help), or host it and set db_url.

    python3 -m venv ~/osmvenv && ~/osmvenv/bin/pip install osmium
    curl -LO https://download.geofabrik.de/north-america/us/california-latest.osm.pbf
    ~/osmvenv/bin/python tools/speedlimits_build.py california-latest.osm.pbf \\
            --out speedlimits.db

READERS.
  *.osm.pbf, *.osm.bz2, *.osm   via pyosmium (osmium on PyPI; BSD-2-Clause
                                licence, wraps libosmium). Node locations come
                                from a flex_mem index, so ways need no second pass.
  *.osm, *.osm.gz               with no pyosmium installed, the standard library
                                reads small extracts (a city) into memory.
Force one with --reader osmium|xml.

WHAT IS KEPT. Ways whose `highway` is a road class and which either have a
parseable `maxspeed` ("posted") or are residential with none (a 25 mph
California default, labelled "default"). Everything else is dropped on
purpose; see lib/speedlimit.py DEFAULTS before adding a class.

LICENCE. The output is a Produced Work / derived database of OpenStreetMap
data: (c) OpenStreetMap contributors, ODbL 1.0. Keep the attribution in the
`meta` table (this tool writes it), show it in the app (it does), and share
the database under the ODbL if you share it. Never commit it to git.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import speedlimit as S  # noqa: E402


def build_osmium(path, out, index, **meta):
    """Stream the extract through pyosmium straight into the database, so a
    state's worth of ways is never held in memory at once (only the node index)."""
    import osmium
    db = S.create_db(out)

    class Roads(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.batch, self.n = [], 0

        def way(self, w):
            tags = w.tags
            if tags.get("highway") not in S.ROAD_CLASSES:
                return
            try:
                pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
            except osmium.InvalidLocationError:
                return
            keep = {k: tags.get(k) for k in ("highway", "name", "ref", "maxspeed", "maxspeed:forward",
                                             "maxspeed:backward", "oneway", "junction") if tags.get(k)}
            self.batch += S.rows_for_way(w.id, keep, pts)
            if len(self.batch) >= 20000:
                self.flush()

        def flush(self):
            self.n += S.add_rows(db, self.batch)
            self.batch = []

    h = Roads()
    h.apply_file(path, locations=True, idx=index)
    h.flush()
    S.finish_db(db, **meta)
    db.close()
    return h.n


def pick_reader(path, want):
    if want == "xml":
        return "xml"
    try:
        import osmium  # noqa: F401
        return "osmium"
    except ImportError:
        if want == "osmium":
            sys.exit("  pyosmium is not installed: pip install osmium (BSD-2-Clause)")
        if path.endswith((".pbf", ".bz2")):
            sys.exit("  A .pbf needs pyosmium: python3 -m venv v && v/bin/pip install osmium")
        return "xml"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("extract", help=".osm.pbf / .osm / .osm.gz")
    ap.add_argument("--out", default="speedlimits.db")
    ap.add_argument("--reader", choices=["osmium", "xml"])
    ap.add_argument("--index", default="flex_mem", help="pyosmium node-location index")
    a = ap.parse_args(argv)
    reader = pick_reader(a.extract, a.reader)
    meta = {"source": os.path.basename(a.extract), "reader": reader}
    if reader == "osmium":
        n = build_osmium(a.extract, a.out, a.index, **meta)
    else:
        n = S.build_db(S.read_osm_xml(a.extract), a.out, **meta)
    print(f"  {n} road chunks -> {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB, reader: {reader})")
    print("  Map data: " + S.ATTRIBUTION)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
