# Speed limits, offline

A small round sign beside the speed on Home's dial card and on Gauges says what
the posted limit is on the road you are on. It works with no network. It says
nothing when it is not sure.

**Map data: © OpenStreetMap contributors, under the Open Database Licence
(ODbL) 1.0** (<https://www.openstreetmap.org/copyright>). That is a licence
condition, not a courtesy, so it is in four places: the database's `meta` table,
`GET /api/speedlimit`, the sign's caption area (tap the sign for the full line),
and here. The database is a derived database under the ODbL: share it under the
ODbL if you share it. **No map data is in git**, and `test/fixtures/speedlimits.osm`
is a made-up neighbourhood, not OpenStreetMap data.

**There is no GPS receiver on the tablet yet.** Today everything runs on the
simulator in `lib/location.py`, and the sign says "Simulated" whenever it does.

## What you see

- **The sign.** Posted limit: a solid ring and the number, captioned "Limit".
  A **default** (the road has no `maxspeed` tag): a hollow, dashed ring captioned
  "Default", because it is a statutory guess, not something painted on a pole.
  Nothing at all when the match is unsure: no placeholder, no zero.
- **Over the limit.** When the car has been more than 5 mph over a **posted**
  limit for more than 5 seconds, the speed number takes the warn tone. That is
  all: no sound (a child sleeps near the tablet), nothing modal, nothing
  moves. A default never triggers it.
- **With the radar.** While the radar banner has an alert and you are more than
  5 mph over a posted limit, it adds "Over the limit". It only reads the speed
  state; `radarbanner.js` gained a few lines for this and nothing else changed.
- Metric: the number is shown in km/h (rounded); the rule always compares mph.

## How it decides

`lib/speedlimit.py`, pure and tested (`test/speedlimit_test.py`).

1. **Candidates.** The R*Tree finds road chunks whose bounding box touches a
   25 m square around the fix; each is measured to the real segment.
2. **Heading.** With a usable course (above 2 m/s), a road more than 60 degrees
   across is not a candidate, a one-way road is only a candidate going its way,
   and the score is distance + 0.3 m per degree of misalignment. A two-way road
   with `maxspeed:forward` / `maxspeed:backward` gives the limit for the
   direction of travel. A stopped car has no heading and is never moved to a
   different road.
3. **Hysteresis.** The matcher locks onto a road after the same one wins 3 fixes
   in a row, and changes road only when another wins 3 in a row. Frontage roads,
   overpasses and intersections therefore do not flicker.
4. **Confidence.** High only if the road is within 15 m, lined up within 35
   degrees, and no neighbour within 8 points of the winner says a different
   number. Anything else is "low" and shows nothing.

The matcher advances when `/api/speedlimit` is read (the app reads it once a
second while a sign or banner is on screen), so it costs nothing when no one is
looking. It keeps its state in the server process, not a file.

## Defaults, and why they are short

Only `highway=residential` with no `maxspeed` gets a default: **25 mph**, the
California prima facie limit in a residence district (CVC 22352), labelled
"default, not posted". Nothing else is guessed. A freeway's 65 is a ceiling, not
a posting, and a rural road could be anything. Add a class to `DEFAULTS` in
`lib/speedlimit.py` only with a statute behind it. A `maxspeed` that is
`signals`, `none`, `walk`, a list, or a country code is ignored, never turned
into a number.

## Getting the data (on a desktop, not the tablet)

```
python3 -m venv ~/osmvenv && ~/osmvenv/bin/pip install osmium     # BSD-2-Clause
curl -LO https://download.geofabrik.de/north-america/us/california-latest.osm.pbf
~/osmvenv/bin/python tools/speedlimits_build.py california-latest.osm.pbf --out speedlimits.db
```

Then copy `speedlimits.db` to `~/.local/share/omacar/` on the tablet, or host it
and put `{"db_url": "https://...", "sha256": "..."}` in
`~/.config/omacar/speedlimits.json` and run `omacar speedlimits fetch` (it checks
the hash and that the file is a database this version reads, and installs
nothing otherwise). `omacar speedlimits fetch --pbf FILE` runs the build tool.
`omacar speedlimits sample` builds the tiny sample database from the fixture.
With no pyosmium, the tool reads a small `.osm` / `.osm.gz` with the standard
library (a city, not a state: it holds every node in memory).

Other commands: `omacar speedlimits status`, `lookup LAT LON [COURSE]`,
`sim [seconds]` (a labelled simulated drive east along the sample's Main St, 30
to 45 mph).

## What is verified, and what is not

Verified (tests and the sandbox):

- Parsing of `maxspeed`, posted-vs-default labelling, one-way and
  forward/backward handling, chunking, the R*Tree lookup (including that SQLite
  plans it through the index, and 30,000 chunks in a few milliseconds).
- Heading matching on opposite carriageways 15 m apart, an intersection, a
  frontage road 20 m away, hysteresis, confidence, and the over-limit rule (JS).
- pyosmium 4.3.1 and the standard-library reader build databases with identical rows
  from the fixture.
- The UI, in a sandbox against the simulator, at 1368x912 and 912x1368.

**Not verified:** a real California extract was never downloaded or built (so its
size, build time and memory are unmeasured; expect gigabytes of RAM and minutes
on a desktop); how complete `maxspeed` is in OpenStreetMap on the owner's roads;
anything with a real GPS receiver (course noise at low speed, urban canyons,
the tablet's CPU cost of a lookup); the tablet's SQLite having R*Tree compiled
in (Arch's does; `omacar speedlimits sample` proves it on the box in one command).
