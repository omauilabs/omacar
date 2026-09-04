"""The rollup the bar panel and the dock card read.

OmaCar's daemon writes one row a second into the vehicle's database and the
current sample into `live.json`. That is the right granularity for a gauge
cluster and the wrong one for something you glance at: nobody opens a menu bar
to watch this second's RPM. What those surfaces want is the shape of it — how
far today, how thirsty this month against last, what the ECU is holding, what
is due next — and they want it in one small file they can read in one go,
without opening a database on the shell's own thread.

So this reduces everything OmaCar knows to that one file:

    ~/.local/state/omarchy/liquid-glass-car.json

WHY THIS IS AN OMACAR COMMAND AND NOT A SCRIPT SOMEWHERE ELSE.

That file was written until now by a standalone script in ~/.local/bin, which
read a single `telemetry.db`. OmaCar moved away from that path when it grew a
garage: one database per vehicle, with a pointer saying which car is in front
of us. The script went on reading the old name, found nothing there, and
published a card whose live figures were right — it still read live.json — and
whose every stored figure was empty. No name, no vehicle, no service book, no
trips, no codes. The panel rendered exactly that, and its hero read "No car"
over a car with a hundred and thirty-seven thousand kilometres of history
behind it.

The lesson is not that the script had a bug. It is that a cache of OmaCar's
data was being produced by something that was not OmaCar, and so could drift
away from it silently. Everything below comes from records.snapshot(), the
same reader the app, the loopback API and the AI layer use, resolved through
lib/garage.py like every other reader — so the panel can no longer disagree
with the screens it is a summary of.

The maths that snapshot() does not do is inherited from that script, and it is
worth keeping in the same words, because each line of it is a decision:

  distance   speed integrated over time, never a trip odometer PID — most
             cars do not expose one, and the ECU's own trip meter resets on
             its own schedule rather than on ours
  fuel       mass air flow over the stoichiometric ratio, integrated the same
             way, the same maths the cluster's ring uses
  economy    total fuel over total distance for the whole window, and NOT the
             mean of the per-second figures: that average is dominated by
             crawling in traffic, where the instantaneous number is enormous
             and the fuel actually burned is small

Two sources, one answer. A year of one-second rows is not a thing to keep, so
OmaCar rolls each day up into a `days` row and prunes the samples behind it.
Where those rollups exist they are the record; where they do not, the same
figures are integrated from the raw samples on the fly. records.days() merges
the two and this file reports which it got in `source`, so a reader can say
"a fortnight" rather than "a year" when that is all there is.

Read-only, always. This never opens the serial port — the daemon owns that —
and it never writes to the database.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import concerns  # noqa: E402
import records   # noqa: E402

# Hardcoded under ~ rather than under XDG_STATE_HOME, and deliberately: the
# two readers of this file — plugin/Panel.qml and the dock card — both build
# the path from $HOME themselves, so honouring an XDG override here would only
# mean writing somewhere nothing is looking. OmaCar's own state still follows
# XDG through records.STATE; this one file lives where Omarchy keeps the
# desktop's caches, because that is what it is.
CACHE = os.path.expanduser("~/.local/state/omarchy/liquid-glass-car.json")

# How much of the recent past the `history` block carries. Fourteen days is
# what the dock card's sparkline draws, and long enough that a week-on-week
# comparison has both weeks in it.
DAYS = 14

# How many trips are worth listing. The panel shows five and the card one, so
# this is headroom rather than a limit anybody sees.
TRIPS = 12


# ---- the live sample --------------------------------------------------------

def flat_live(snap):
    """The current sample, flattened to the names the panel binds to.

    The daemon publishes OBD-II's own PID names — `values.RPM` — and the panel
    reads `live.rpm`. The panel does that flattening itself for the sample it
    reads directly from live.json, and falls back to this copy for the moment
    before its first read and for whenever the daemon is not running. The two
    therefore have to agree key for key; see `readonly property var live` in
    plugin/Panel.qml, which is the list below in QML.

    records.live() has already decided whether the sample is fresh enough to
    believe. A stale one comes back with its values emptied, so everything
    here goes to None rather than freezing last Tuesday's road speed on a
    panel whose whole contract is that the number is true now.
    """
    v = snap.get("values") or {}
    return {
        "rpm": v.get("RPM"),
        "speed": v.get("SPEED"),
        "coolant": v.get("COOLANT_TEMP"),
        "intake": v.get("INTAKE_TEMP"),
        "ambient": v.get("AMBIANT_AIR_TEMP"),
        "fuel_pct": v.get("FUEL_LEVEL"),
        "volts": v.get("CONTROL_MODULE_VOLTAGE"),
        "load": v.get("ENGINE_LOAD"),
        "throttle": v.get("THROTTLE_POS"),
        "stft": v.get("SHORT_FUEL_TRIM_1"),
        "ltft": v.get("LONG_FUEL_TRIM_1"),
        "timing": v.get("TIMING_ADVANCE"),
        "run_time": v.get("RUN_TIME"),
        "lphk": snap.get("economy_lphk"),
        "lph": snap.get("fuel_lph"),
        "efficiency": snap.get("efficiency"),
        "basis": snap.get("efficiency_basis"),
        "protocol": snap.get("protocol"),
        "adapter": snap.get("kind"),
        "port": snap.get("port"),
        "trip": snap.get("trip"),
    }


# ---- the fortnight ----------------------------------------------------------

def blank_day(day):
    """A day the car did not move, in the shape records.days() gives one.

    The days series only contains days something happened, and a sparkline
    with the quiet days missing is a sparkline that draws a fortnight as a
    week. Note `lphk` stays None rather than becoming nought: a car that
    burned nothing has no economy figure at all, and plotting nought would
    draw a saw-tooth of imaginary perfect days.
    """
    return {"day": day, "km": 0.0, "litres": 0.0, "lphk": None,
            "moving_s": 0, "engine_s": 0, "idle_s": 0, "top_kph": 0.0,
            "trips": 0, "cost": None, "odo": None,
            "ltft_mean": None, "coolant_max": None, "rpm_max": None}


def history(series, trips, samples):
    """The last fortnight, day by day, plus the window as a whole.

    The old script integrated this straight from the raw samples, which was
    the only thing it could do and is now the wrong thing to do: once the
    compactor has been through, the raw samples do not reach back fourteen
    days at all, and a card built from them would report a fortnight that got
    shorter every night. records.days() already merges the stored rollups with
    whatever the compactor has not reached yet, so the series arrives complete
    and this only has to pad the gaps and add up the total.
    """
    by_day = {d["day"]: d for d in series}
    today = datetime.now().date()
    days = []
    for i in range(DAYS - 1, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        days.append(by_day.get(key) or blank_day(key))

    first = days[0]["day"]
    return {
        "days": days,
        "today": days[-1],
        # Fuel over distance for the fortnight as one window; window_of() is
        # the same summation the Drive tab's figures go through.
        "window": records.window_of(days, n=DAYS),
        # Real trips out of the book, rather than the runs of movement the old
        # script inferred from the sample stream. The watchdog files a trip
        # when the engine has been off long enough for one to have ended,
        # which is a better judge of it than a gap in the rows.
        "trips": [t for t in trips
                  if datetime.fromtimestamp(t.get("t0") or 0).strftime(
                      "%Y-%m-%d") >= first][:8],
        "last_trip": trips[0] if trips else None,
        "samples": samples,
    }


def sample_count(db, since):
    """How many raw rows are left in the window, for `have_history` to lean on
    when there are no daily rollups yet — a car driven for the first time this
    morning has samples and no rolled-up day to show for them."""
    got = records.rows(db, "SELECT count(*) AS n FROM samples WHERE t >= ?",
                       (since,), table="samples")
    return int(got[0]["n"]) if got else 0


# ---- what the numbers say ---------------------------------------------------

def noticed(db, faults):
    """Things worth flagging that no trouble code has been set for.

    A code is the car noticing. This is the panel noticing first — a fuel trim
    that has crept all month, a coolant peak climbing, a self-test margin
    closing — and it is what fills the Health tab's "noticed in the data"
    section under the codes.

    The old script did its own small version of this with two SQL queries.
    concerns.assess() is OmaCar's own trend engine and is strictly better at
    it, so this defers to it and does two things to the result.

    It drops the `series` each concern carries. Those are ninety to a hundred
    and twenty points apiece for a graph the panel does not draw, and they
    would be most of the bytes in this file.

    And it drops any concern about a code that is already in the fault list
    above it. "P0135 keeps coming back" is a true and useful observation in
    the app, where it sits in its own view; three rows under the very code it
    is about, it reads as the panel saying the same thing twice.
    """
    try:
        found = concerns.assess(db)
    except Exception:                                     # noqa: BLE001
        # A trend engine that cannot fit a line must not be able to stop the
        # panel getting a card. Everything else in here is still true.
        return []
    known = {f.get("code") for f in faults}
    out = []
    for c in found:
        if c.get("code") and c["code"] in known:
            continue
        out.append({k: v for k, v in c.items() if k != "series"})
    return out


# ---- privacy ----------------------------------------------------------------

def masked_vin(vin):
    """A VIN with everything that identifies one car taken out of it.

    THE FULL VIN DOES NOT BELONG IN THIS FILE.

    A VIN identifies one vehicle and, through any number of public and
    commercial databases, its owner and its history. share/js/privacy.js says
    all this at length about the web app, and gives the app a privacy mode you
    can switch on before photographing the screen.

    The bar panel has no such switch, and its Now tab shows the VIN whenever
    anybody opens the panel — over a shoulder, in a screenshot, in a video of
    the desktop. A privacy mode that has to be remembered is a privacy mode
    that is off in the one frame that gets published, so this cache simply
    never carries the identifying part.

    The first three characters stay, which is the same rule privacy.js uses:
    they are the World Manufacturer Identifier, shared by hundreds of
    thousands of cars, and they keep the field recognisable as a VIN. Nothing
    from the fourth character on is kept anywhere in this file.

    Note that the value is REPLACED rather than hidden. A masked string is
    genuinely not there to be read; a shortened one that the reader is asked
    to render politely is a leak waiting for a consumer that does not.

    The whole VIN is not lost, and is one command away for anybody who wants
    it: `omacar vehicle` reads it out of the database, where it belongs.
    """
    v = str(vin or "").strip()
    if not v:
        return v
    if len(v) < 6:
        return "•" * len(v)
    return v[:3] + "•" * (len(v) - 3)


# ---- the card ---------------------------------------------------------------

def build():
    """Everything the panel and the dock card read, as one dict."""
    # Re-read which car is current before anything else. This runs inside the
    # watchdog, which stays up for weeks, and records.DB is resolved once at
    # import — so without this a vehicle switch would leave the card reporting
    # the car that was plugged in last month.
    records.refresh_db()

    snap = records.snapshot()
    db = records.connect()
    try:
        # The daily series the snapshot already built, rather than a second
        # call to records.days(). Merging the stored rollups with the samples
        # the compactor has not reached is the expensive half of this whole
        # command, and doing it twice would also give the card two chances to
        # disagree with itself. `perf.days` is the last sixty days of exactly
        # that series, which is more than the fortnight below needs.
        series = (snap.get("perf") or {}).get("days") or []
        trips = snap.get("trips") or []
        window_start = time.time() - DAYS * 86400
        samples = sample_count(db, window_start)
        watch = noticed(db, snap.get("faults") or [])
        # Whether the daily rollups exist at all, which is what decides
        # whether the year view is a year or is however far the raw samples
        # happen to reach. records.days() merges the two, so this asks the
        # table directly rather than inferring it from the merged series.
        stored = records.rows(db, "SELECT count(*) AS n FROM days",
                              table="days")
    finally:
        if db is not None:
            db.close()

    vehicle = dict(snap.get("vehicle") or {})
    if vehicle.get("vin"):
        vehicle["vin"] = masked_vin(vehicle["vin"])

    faults = snap.get("faults") or []
    active = snap.get("active_faults") or []

    return {
        # -- straight from the snapshot, so the panel and the app agree ------
        "checked": snap.get("checked") or int(time.time()),
        "units": snap.get("units") or records.units_for(),
        "connected": bool(snap.get("connected")),
        "simulated": bool(snap.get("simulated")),
        "status": snap.get("status") or "offline",
        "stale": snap.get("stale"),
        "vehicle": vehicle,
        "name": snap.get("name") or "",
        "odometer": snap.get("odometer"),
        "perf": snap.get("perf"),
        "service": snap.get("service"),
        "trips": trips[:TRIPS],
        "faults": faults,
        # The list, not a count. The panel prefers it over `issues` precisely
        # because a length cannot disagree with the rows underneath it.
        "active_faults": active,
        "have_history": bool(snap.get("have_history")) or samples > 0,

        # -- composed here ---------------------------------------------------
        "live": flat_live(snap.get("live") or {}),
        "history": history(series, trips, samples),
        "watch": watch,
        # BOTH KEYS, AND THEY MUST AGREE.
        #
        # The panel reads `active_faults.length` where it has one and falls
        # back to `issues` where it does not; the dock card only ever reads
        # `issues`. So this is the length of that same list and nothing else.
        # The old script added the derived observations into the count, which
        # would now make the two disagree — a hero pill reading "6 issues"
        # over a Health tab listing five codes.
        "issues": len(active),
        "source": "rollups" if (stored and stored[0]["n"]) else "samples",
    }


def write(out=None, path=CACHE):
    """Publish the card. Atomically, because the panel is watching this path.

    plugin/Panel.qml holds a FileView on it with watchChanges, so the kernel
    tells the shell the moment the file is touched. Writing in place would
    give it a fair chance of reading a half-finished card and rendering the
    half — a hero with no name over a Drive tab with no numbers. A temp file
    beside it and one rename means the panel sees the old card or the new one
    and never anything in between.

    The temp file goes in the SAME directory, since os.replace is only atomic
    within one filesystem.
    """
    out = build() if out is None else out
    folder = os.path.dirname(path)
    # Guarded: write(path="card.json") with a bare filename gives dirname "",
    # and makedirs("") raises rather than meaning "here".
    if folder:
        os.makedirs(folder, exist_ok=True)

    # THE TEMP NAME CARRIES THE PID, and that is not fussiness.
    #
    # There are three producers now -- the watchdog's minute thread, the
    # panel's Refresh button and anyone typing `omacar card` -- where the old
    # script was one oneshot on a timer. With a single shared "<path>.tmp",
    # two of them overlapping means the second truncates the file the first is
    # still filling and the first then renames the second's half-written bytes
    # into place. That was reproduced, not theorised: four concurrent writers
    # produced 52 corrupt reads of the published file.
    #
    # The blast radius is wider than this panel. The dock's Service.qml reads
    # this same file every five seconds through `jq`, and its `|| echo {}`
    # fallback catches a MISSING file, not a malformed one -- so one torn read
    # stops every dock card, not just the car. The panel fails more quietly and
    # just as badly: readCache() returns silently on a parse error and goes on
    # showing the last good card forever.
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            # allow_nan=False on purpose. Python would otherwise emit the bare
            # tokens NaN and Infinity, which are not JSON and which QML's
            # JSON.parse rejects -- producing exactly the silent freeze above.
            # Nothing generates one today, but live.lphk and friends are copied
            # straight out of live.json, so the chain exists. Failing loudly
            # here beats publishing a file the panel cannot read; watch.py
            # already catches and prints, so the watchdog survives either way.
            json.dump(out, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # A build that raised must not leave litter beside a file the shell is
        # watching with inotify.
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return out


# ---- the command ------------------------------------------------------------

def report(out):
    """What `omacar card` says when nobody asked it to be quiet: the card, in
    the terminal, so you can see what the panel is about to show."""
    u = out["units"]

    def dist(km):
        v = records.to_dist(km, u)
        return f"{v:,.1f} {u['dist']}" if v is not None else "—"

    def econ(w):
        v = records.to_econ((w or {}).get("lphk"), u)
        return f"{v:.1f} {u['econ']}" if v else "—"

    print(f"car        {out['name'] or 'unknown'}"
          + ("   (simulated)" if out["simulated"] else ""))
    print(f"status     {out['status']}"
          + (f"   {records.to_dist(out['odometer'], u):,.0f} {u['dist']}"
             if out["odometer"] else ""))
    perf = out["perf"]
    if not out["have_history"]:
        print("history    none yet — run `omacar daemon start` with the car awake")
    elif perf:
        for label, key in (("today", "day"), ("this week", "week"),
                           ("this month", "month"), ("this year", "year")):
            w = perf[key]
            prev = w.get("prev") or {}
            delta = ""
            if prev.get("lphk") and w.get("lphk"):
                d = (records.to_econ(w["lphk"], u) or 0) \
                    - (records.to_econ(prev["lphk"], u) or 0)
                delta = f"   {d:+.1f} vs previous"
            print(f"{label:<11}{dist(w['km']):>12}   {econ(w):<12}"
                  f"{records.to_vol(w['litres'], u):>7,.1f} {u['vol']}{delta}")
    if out["service"]:
        s = out["service"]["next"]
        print(f"next due   {s['item']}"
              + (f" ({s['code']})" if s.get("code") else "")
              + f"   {s['life']}% life"
              + (f", {records.to_dist(s['km_left'], u):,.0f} {u['dist']}"
                 if s.get("km_left") is not None else "")
              + (f", {s['due_on']}" if s.get("due_on") else ""))
    for f in out["faults"]:
        if not f.get("active"):
            continue
        when = (datetime.fromtimestamp(f["last_seen"]).strftime("%d %b")
                if f.get("last_seen") else "")
        print(f"{f['status']:<11}{f['code']}  {f['descr']}   last {when}")
    for w in out["watch"]:
        print(f"noticed    {w['title']}")
    print(f"written    {CACHE}   ({out['source']})")


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    out = write()
    if "--quiet" in argv:
        return 0
    if "--json" in argv:
        print(json.dumps(out, indent=2))
        return 0
    report(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
