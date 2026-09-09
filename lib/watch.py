"""The watchdog: the part that matters when you are not looking at the app.

A diagnostic tool you have to open is a tool you use after something has gone
wrong. The point of leaving an adapter in the car is to be told *while* it is
going wrong — a coolant temperature climbing on a hill, a charging system that
stopped charging forty miles from home, a code that set once and cleared itself
before you ever saw the light.

So this runs alongside the daemon, watches the sample stream and the fault
list, and raises an Omarchy notification when a rule fires. Nothing else in
OmaCar polls the car; this reads the same `live.json` everything else reads.

Design rules, all learned the hard way by anyone who has built an alerter:

  Hysteresis, not thresholds. A value sitting on a limit must not produce a
  notification a second. Every rule arms above one level and only re-arms below
  a lower one.

  Sustain before shouting. A single sample over a limit is noise on a bus; a
  rule has to hold for a few seconds before it counts.

  Quiet by default. Anything that fires more than a few times a day is
  something people learn to ignore, which makes the useful ones useless too.

  Every alert is filed, whether or not a notification reached anyone. The
  timeline in the app is the record; the notification is a courtesy.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card      # noqa: E402
import concerns  # noqa: E402
import hotplug  # noqa: E402
import prune    # noqa: E402
import records  # noqa: E402

PIDFILE = os.path.join(records.STATE, "watch.pid")
STATEFILE = os.path.join(records.STATE, "watch-state.json")
# A small file for anything that cannot open a database — the Omarchy bar
# widget reads this with one `cat`, the same way every other card on this
# desktop reads its data.
FEED = os.path.join(records.STATE, "alerts.json")
FEED_KEEP = 20
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")),
    "omarchy", "omacar-watch.json")

POLL = 2.0
# How often the bar panel's rollup is rebuilt. See Watch.card() for why it is
# a minute rather than every pass.
CARD_EVERY = 60.0
# A trip ends when the engine has been off this long. Long enough to survive a
# stall at a light and a fuel stop, short enough that the summary arrives while
# you are still standing next to the car.
TRIP_END_GAP = 180
TRIP_MIN_KM = 0.4


def cfg():
    try:
        with open(CONFIG) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


# ---- rules ------------------------------------------------------------------
#
# `on` and `off` are the arming and re-arming levels; `hold` is how long the
# condition has to persist before it counts. `only_running` keeps a rule from
# firing about a car that is simply parked.

RULES = [
    {
        "id": "coolant_hot",
        "title": "Engine running hot",
        "urgency": "critical",
        "value": lambda v, s: v.get("COOLANT_TEMP"),
        "on": lambda x: x is not None and x >= 105,
        "off": lambda x: x is None or x <= 99,
        "hold": 6,
        "only_running": True,
        "say": lambda x, u: f"Coolant at {records.to_temp(x, u):.0f}{u['temp']}. "
                            "Stop somewhere safe before it boils.",
    },
    {
        "id": "coolant_warm",
        "title": "Coolant above normal",
        "urgency": "normal",
        "value": lambda v, s: v.get("COOLANT_TEMP"),
        "on": lambda x: x is not None and x >= 100,
        "off": lambda x: x is None or x <= 95,
        "hold": 20,
        "only_running": True,
        # Two rules watch the same value at two levels, so the second must not
        # add a "getting warm" notification to a car that is already boiling.
        "suppressed_by": "coolant_hot",
        "say": lambda x, u: f"Coolant at {records.to_temp(x, u):.0f}{u['temp']}, "
                            "above the thermostat's working range.",
    },
    {
        "id": "not_charging",
        "title": "Not charging",
        "urgency": "critical",
        "value": lambda v, s: v.get("CONTROL_MODULE_VOLTAGE"),
        "on": lambda x: x is not None and x < 12.6,
        "off": lambda x: x is None or x >= 13.2,
        "hold": 25,
        "only_running": True,
        "say": lambda x, u: f"System voltage {x:.1f} V with the engine running. "
                            "The car is on its battery and will stop when that is flat.",
    },
    {
        "id": "fuel_low",
        "title": "Fuel low",
        "urgency": "normal",
        "value": lambda v, s: v.get("FUEL_LEVEL"),
        "on": lambda x: x is not None and x <= 12,
        "off": lambda x: x is None or x >= 22,
        "hold": 30,
        "only_running": True,
        "say": lambda x, u: f"Fuel at {x:.0f}%.",
    },
    {
        "id": "trim_high",
        "title": "Fuel trim drifting",
        "urgency": "low",
        "value": lambda v, s: v.get("LONG_FUEL_TRIM_1"),
        "on": lambda x: x is not None and abs(x) >= 15,
        "off": lambda x: x is None or abs(x) <= 11,
        # Long trim is a slow adaptation, so a long hold: a minute of it is a
        # real condition and a few seconds of it is the ECU thinking.
        "hold": 120,
        "only_running": True,
        "say": lambda x, u: f"Long-term fuel trim at {x:+.1f}%. "
                            + ("Running lean — look for a vacuum leak."
                               if x > 0 else "Running rich."),
    },
    {
        "id": "overrev",
        "title": "Engine over its redline",
        "urgency": "normal",
        "value": lambda v, s: v.get("RPM"),
        "on": lambda x: x is not None and x >= 6500,
        "off": lambda x: x is None or x <= 5200,
        "hold": 1,
        "only_running": True,
        "say": lambda x, u: f"{x:.0f} rpm.",
    },
]


def notify(title, body, urgency="normal", icon="omacar"):
    """One desktop notification, and never a reason to crash the watchdog."""
    level = {"low": "low", "normal": "normal", "critical": "critical"}.get(urgency, "normal")
    try:
        subprocess.run(
            ["notify-send", "-a", "OmaCar", "-u", level, "-i", icon,
             title, body],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def load_state():
    try:
        with open(STATEFILE) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(records.STATE, exist_ok=True)
    tmp = STATEFILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATEFILE)


def file_alert(kind, title, body, urgency, extra=None):
    payload = {"kind": kind, "title": title, "body": body, "urgency": urgency}
    if extra:
        payload.update(extra)
    try:
        records.write_record("alert", f"{title} — {body}"[:180], payload)
    except Exception:                                    # noqa: BLE001
        pass
    write_feed(dict(payload, at=int(time.time())))


def write_feed(entry):
    """Prepend to the little file the bar widget reads."""
    try:
        with open(FEED) as f:
            feed = json.load(f) or {}
    except (OSError, ValueError):
        feed = {}
    items = [entry] + [x for x in (feed.get("alerts") or [])][:FEED_KEEP - 1]
    out = {
        "at": int(time.time()),
        "alerts": items,
        # A day's worth, counted by severity, which is all a badge needs.
        "recent": {
            level: sum(1 for x in items
                       if x.get("urgency") == level
                       and time.time() - x.get("at", 0) < 86400)
            for level in ("critical", "normal", "low")
        },
    }
    try:
        os.makedirs(records.STATE, exist_ok=True)
        tmp = FEED + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, FEED)
    except OSError:
        pass


class Watch:
    def __init__(self, quiet=False, persist=True, watch_faults=True, sink=None):
        self.quiet = quiet
        self.persist_state = persist
        self.watch_faults = watch_faults
        # Where alerts go. The default files and notifies; a test collects.
        self.sink = sink
        self.state = load_state() if persist else {}
        self.armed = self.state.get("armed", {})
        self.since = {}
        self.known_codes = set(self.state.get("codes", []))
        # Which car those codes belong to. Swapping vehicles — or switching
        # between the simulator and a real adapter — replaces the whole fault
        # list at once, and announcing that as "five codes cleared" is a lie
        # about a repair that never happened.
        self.known_vin = self.state.get("vin")
        self.last_connected = None
        # A link change that has not been believed yet: (state, first_seen).
        self.link_pending = None
        self.trip = None
        # Compaction is a housekeeping job, not a rule. It lives here because
        # the watchdog is already the one process that is always awake, and a
        # second timer is a second thing to discover is not running.
        self.last_compact = self.state.get("compacted", 0)
        # The bar panel's rollup, on the same argument as compaction. Not
        # persisted, unlike `compacted`: a watchdog that has just started is
        # exactly when a card most wants rebuilding, and it costs a second.
        self.last_card = 0.0
        self.card_thread = None
        # Hotplug without root: notice a serial port that was not there a
        # moment ago and ask the daemon to take it. The udev rule does this
        # properly and instantly; this is the version that needs nothing from
        # anybody, and having both is harmless.
        self.autostart = cfg().get("autostart", True)
        # On by default on a machine set up as a tablet, and harmless anywhere
        # else: with no graphical session there is nothing to open.
        self.open_on_plug = cfg().get("open_on_plug", True)
        self.plug_view = str(cfg().get("plug_view", "hub") or "hub")
        self.saw_adapter = None
        self.last_hotplug = 0.0
        self.last_moving = 0.0
        self.units = records.units_for()

    # -- one pass ------------------------------------------------------------
    def tick(self, snap=None, now=None):
        """One evaluation. `snap` and `now` are injectable so the rules can be
        tested against a scripted car rather than against whatever is parked
        outside — which is the only way to test a rule about overheating."""
        snap = records.live() if snap is None else snap
        values = snap.get("values") or {}
        connected = bool(snap.get("connected"))
        running = (values.get("RPM") or 0) > 200
        now = time.time() if now is None else now

        self.link(connected, snap, now)
        if connected:
            self.rules(values, running, now)
            self.trips(values, running, now)
        # Faults are read from the database rather than the sample stream, so a
        # code that sets and self-clears between polls is still noticed.
        if self.watch_faults:
            self.faults(now)
        if self.persist_state:
            self.hotplug(now)
            self.card(now)
            self.housekeep(now)
            self.persist()

    # -- the adapter ---------------------------------------------------------
    #
    # How long a link change has to hold before anyone is told about it.
    #
    # Comfortably longer than a hand-off (a few seconds) and than the pause
    # while the daemon re-opens the port (~1s), and short enough that a real
    # unplug is reported while you are still standing next to the car.
    LINK_SETTLE = 25.0

    def link(self, connected, snap, now=None):
        """File adapter come-and-go, but only when it is real.

        TWO THINGS MADE THIS FLAP FOR A WHOLE DRIVE.

        The daemon publishes connected=False with status="yielded" every time
        it lends the adapter to a command -- the DTC sweep every five minutes,
        a scan, a reset. That is a deliberate hand-off lasting seconds, and it
        was filed as "The adapter is no longer answering", then "OmaCar is
        watching" twenty seconds later, on repeat. Ten of those pairs in one
        afternoon, none of them a real event.

        And nothing waited to see whether a drop stuck. Even a genuine one-tick
        blip -- a connector nudged over a bump -- became two notifications.

        So: a hand-off is not a disconnection, and no change is believed until
        it has held for LINK_SETTLE.
        """
        now = time.time() if now is None else now

        # Lent, not lost. Not even recorded as a state change.
        if snap.get("status") == "yielded" or snap.get("handover"):
            self.link_pending = None
            return

        if self.last_connected is None:
            self.last_connected = connected
            self.link_pending = None
            return
        if connected == self.last_connected:
            # Back to where it was before the timer ran out: nothing happened.
            self.link_pending = None
            return

        if self.link_pending is None or self.link_pending[0] != connected:
            self.link_pending = (connected, now)
            return
        if now - self.link_pending[1] < self.LINK_SETTLE:
            return

        self.last_connected = connected
        self.link_pending = None
        if connected:
            name = snap.get("kind") or "adapter"
            self.raise_alert("link", "OmaCar is watching",
                             f"{name} connected on {snap.get('port', '')}".strip(),
                             "low")
        else:
            self.raise_alert("link", "OmaCar stopped watching",
                             "The adapter is no longer answering.", "low")

    # -- threshold rules -----------------------------------------------------
    def rules(self, values, running, now):
        for rule in RULES:
            if rule.get("only_running") and not running:
                self.armed.pop(rule["id"], None)
                self.since.pop(rule["id"], None)
                continue
            if rule.get("suppressed_by") and self.armed.get(rule["suppressed_by"]):
                # The louder rule about the same value already has it.
                self.since.pop(rule["id"], None)
                continue
            x = rule["value"](values, self)
            armed = self.armed.get(rule["id"])
            if armed:
                # Only ever re-arm on the lower level, so a value hovering on
                # the limit cannot produce a notification a second.
                if rule["off"](x):
                    self.armed.pop(rule["id"], None)
                    self.since.pop(rule["id"], None)
                continue
            if not rule["on"](x):
                self.since.pop(rule["id"], None)
                continue
            first = self.since.setdefault(rule["id"], now)
            if now - first < rule.get("hold", 0):
                continue
            self.armed[rule["id"]] = now
            self.since.pop(rule["id"], None)
            self.raise_alert(rule["id"], rule["title"],
                             rule["say"](x, self.units), rule["urgency"])

    # -- new trouble codes ---------------------------------------------------
    def faults(self, now):
        db = records.connect()
        if db is None:
            return
        try:
            active = {f["code"]: f for f in records.faults(db) if f["active"]}
            vin = (records.vehicle(db) or {}).get("vin")
        finally:
            db.close()

        if vin != self.known_vin:
            # A different car. Adopt its codes without comment; the previous
            # car's list is not news about this one.
            self.known_vin = vin
            self.known_codes = set(active)
            return
        fresh = [c for c in active if c not in self.known_codes]
        gone = [c for c in self.known_codes if c not in active]
        if not self.known_codes and active:
            # First run: adopt what is already there rather than announcing a
            # car's entire history the moment the watchdog starts.
            self.known_codes = set(active)
            return
        for code in fresh:
            f = active[code]
            self.raise_alert(
                "code:" + code,
                f"{code} — {f.get('descr', 'new fault')}",
                (f.get("module") or {}).get("name", "") or f.get("system", ""),
                "critical" if f.get("severity") == "critical" else "normal",
                extra={"code": code})
        if gone:
            self.raise_alert("codes_cleared", "Trouble codes cleared",
                             ", ".join(sorted(gone)), "low")
        self.known_codes = set(active)

    # -- trips ---------------------------------------------------------------
    def trips(self, values, running, now):
        speed = values.get("SPEED") or 0
        if speed > records.MOVING_KPH:
            self.last_moving = now
            if self.trip is None:
                self.trip = {"t0": now, "km": 0.0, "top": 0.0, "hot": 0.0,
                             "litres": 0.0, "moving_s": 0.0, "idle_s": 0.0}
            self.trip["km"] += speed * POLL / 3600.0
            self.trip["moving_s"] += POLL
            self.trip["top"] = max(self.trip["top"], speed)
        if self.trip is not None:
            self.trip["hot"] = max(self.trip["hot"], values.get("COOLANT_TEMP") or 0)
            if speed <= records.MOVING_KPH:
                self.trip["idle_s"] += POLL
            # Fuel the same way everything else in OmaCar does it: mass air
            # flow over the stoichiometric ratio, integrated. A trip without a
            # fuel figure is half a trip.
            maf = values.get("MAF")
            if maf and maf > 0:
                self.trip["litres"] += ((maf / records.AFR_GASOLINE) * POLL
                                        / records.FUEL_DENSITY_G_PER_L)
        if self.trip is None:
            return
        if running or now - self.last_moving < TRIP_END_GAP:
            return
        trip, self.trip = self.trip, None
        if trip["km"] < TRIP_MIN_KM:
            return
        # File it in the record as well as announcing it. The simulator seeds
        # a `trips` table and the real daemon never did, so on an actual car
        # the drive log had nothing in it.
        self.file_trip(trip)
        u = self.units
        mins = max(1, int((self.last_moving - trip["t0"]) / 60))
        # The trip summary is the one alert that is not a warning. It is the
        # reason to leave the thing plugged in on a car that is behaving.
        self.raise_alert(
            "trip", "Trip finished",
            f"{records.to_dist(trip['km'], u):.1f} {u['dist']} in {mins} min  ·  "
            f"top {records.to_dist(trip['top'], u):.0f} {u['speed']}",
            "low", extra={"km": round(trip["km"], 2), "minutes": mins},
            always=True)

    def file_trip(self, trip):
        try:
            db = sqlite3.connect(records.DB, timeout=5.0)
        except sqlite3.Error:
            return
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS trips (
                t0 REAL PRIMARY KEY, t1 REAL, km REAL, litres REAL, lphk REAL,
                moving_s INTEGER, idle_s INTEGER, top_kph REAL,
                kind TEXT, label TEXT)""")
            km, litres = trip["km"], trip.get("litres", 0.0)
            db.execute("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (trip["t0"], self.last_moving, round(km, 2),
                        round(litres, 3),
                        round(litres / km * 100.0, 2) if km > 0.5 and litres else None,
                        int(trip.get("moving_s", 0)), int(trip.get("idle_s", 0)),
                        round(trip["top"], 1), "drive",
                        time.strftime("%H:%M", time.localtime(trip["t0"]))))
            db.commit()
        except sqlite3.Error:
            pass
        finally:
            db.close()

    # -- raising -------------------------------------------------------------
    def raise_alert(self, key, title, body, urgency, extra=None, always=False):
        # Freeze the state that produced this. A coolant spike on a climb is
        # gone by the time anybody opens the app, and an alert without the
        # evidence behind it is a rumour.
        if self.sink is None and urgency in ("critical", "normal") \
                and not key.startswith(("link", "trip", "hotplug")):
            try:
                shot = concerns.capture(reason=key.split(":")[0], label=title)
                extra = dict(extra or {}, snapshot=shot.get("id"))
            except Exception:                             # noqa: BLE001
                pass
        if self.sink is not None:
            self.sink.append({"key": key, "title": title, "body": body,
                              "urgency": urgency, "extra": extra})
            return
        file_alert(key.split(":")[0], title, body, urgency, extra)
        if not self.quiet:
            notify(title, body, urgency)
        print(f"  {time.strftime('%H:%M:%S')}  [{urgency:8}] {title} — {body}",
              flush=True)

    def hotplug(self, now):
        if not self.autostart or now - self.last_hotplug < 5:
            return
        self.last_hotplug = now
        try:
            port = hotplug.adapter_present()
        except Exception:                                 # noqa: BLE001
            return
        if port == self.saw_adapter:
            return
        appeared = port and not self.saw_adapter
        self.saw_adapter = port
        if not appeared:
            return
        if hotplug.daemon_running() or hotplug.sim_running():
            return
        self.raise_alert("hotplug", "Adapter plugged in",
                         f"Starting the daemon on {port}.", "low")
        hotplug.start_daemon()
        # AND PUT THE APP ON THE SCREEN.
        #
        # Plugging the adapter in is the clearest statement anybody makes to
        # this program: a car is about to be driven. Until now it started the
        # daemon and left the tablet showing whatever it had been showing, so
        # the driver still had to find and open the app before setting off —
        # which is a thing to do with a phone in one hand at the kerb, and
        # exactly the friction that decides whether a tool gets used.
        if self.open_on_plug and hotplug.start_kiosk(self.plug_view):
            self.raise_alert("kiosk", "Opened the app",
                             f"The adapter appeared, so the {self.plug_view} "
                             f"screen is up.", "low")

    def card(self, now):
        """Keep the bar panel's rollup fresh.

        Same argument as housekeep() below: this is the one OmaCar process
        that is always awake, so it is where a job that has to happen whether
        or not anybody has the app open belongs. A second timer would be a
        second thing to discover has not been running.

        A MINUTE, NOT EVERY PASS. The watchdog wakes twice a second and the
        card costs about a second to build — a full pass over the daily
        rollups, the fault list, the service book and the trend engine. Doing
        that on every tick would spend most of a core to republish a file
        whose figures move on the scale of a trip, and the panel only rereads
        it when the kernel says it changed. A minute keeps today's distance
        honest while you are actually driving, which is the fastest anything
        on that card genuinely moves, and it is the cadence the panel already
        expects: its own belt-and-braces reload timer runs at the same rate.

        IN A THREAD, because the watchdog's job is to notice a car overheating
        within a few seconds and it must not be a second late for a cosmetic
        reason. The build is entirely read-only — one SQLite connection opened
        and closed inside the thread — so nothing here can collide with the
        rules running alongside it. At most one at a time: if a build is
        somehow still going a minute later, the next one is skipped rather
        than queued behind it. The thread is a daemon so a hung read can never
        keep the watchdog from exiting when it is asked to stop.
        """
        if now - self.last_card < CARD_EVERY:
            return
        if self.card_thread is not None and self.card_thread.is_alive():
            return
        self.last_card = now
        self.card_thread = threading.Thread(target=self.build_card, daemon=True)
        self.card_thread.start()

    def build_card(self):
        try:
            card.write()
        except Exception as e:                            # noqa: BLE001
            # The panel showing a stale card is a small problem. A watchdog
            # that died writing one is the problem it exists to not be.
            print(f"  card refresh failed: {e}", file=sys.stderr, flush=True)

    def housekeep(self, now):
        """Once a day, and never while the car is being driven — a VACUUM
        holds a write lock, and the daemon is trying to write samples."""
        if now - self.last_compact < 86400:
            return
        if self.trip is not None or now - self.last_moving < 600:
            return
        self.last_compact = now
        try:
            out = prune.compact(verbose=False)
            if out.get("deleted"):
                print(f"  compacted {out['rolled']} day(s), "
                      f"dropped {out['deleted']:,} raw samples", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"  compaction failed: {e}", file=sys.stderr, flush=True)

    def persist(self):
        save_state({"armed": self.armed, "codes": sorted(self.known_codes),
                    "vin": self.known_vin, "compacted": self.last_compact,
                    "at": int(time.time())})


def run(quiet=False):
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    w = Watch(quiet=quiet)
    print(f"  watching {records.LIVE}", flush=True)
    try:
        while True:
            try:
                w.tick()
            except Exception as e:                        # noqa: BLE001
                # A watchdog that dies on a bad sample is worse than no
                # watchdog: it is a watchdog you think is running.
                print(f"  tick failed: {e}", file=sys.stderr, flush=True)
            time.sleep(POLL)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
    return 0


def alerts(n=50):
    db = records.connect()
    out = records.records(db, kind="alert", n=n) if db else []
    if db:
        db.close()
    return out


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv and argv[0] == "list":
        u = records.units_for()
        for a in alerts(30):
            p = a.get("payload") or {}
            print(f"  {time.strftime('%d %b %H:%M', time.localtime(a['at']))}  "
                  f"[{p.get('urgency', '?'):8}] {p.get('title', a.get('label'))}")
            if p.get("body"):
                print(f"                       {p['body']}")
        return 0
    return run(quiet="--quiet" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
