#!/usr/bin/env python3
"""The radar detector on the dash: a Valentine One's alerts, on Home and as a banner.

    omacar v1 status     what v1.json says now
    omacar v1 scan       find V1connection LE adapters nearby (10 s)
    omacar v1 run        hold the connection (omacar-v1.service)
    omacar v1 sim [seconds] [mix|ka|k|laser|loop|idle]
                         a simulated detector, for seeing the screens; every
                         screen says "Simulated" while it runs
    omacar v1 setup      install bleak, the Bluetooth library, in OmaCar's venv

THE DETECTOR SPEAKS, THE TABLET LISTENS. This keeps one Bluetooth LE
connection (the V1connection LE service, lib/esp.py), asks the V1 for its
alert data, and writes what it hears to v1.json, which the server hands to the
app. The one thing the app can ask back is Mute, through v1-ask.json (the same
file-drop the fridge uses), so the server never touches the radio.

THE APP MAKES NO SOUND. The V1 beeps for itself, and a sleeping child rides in
the back. This file has no audio in it and the screens it feeds have none.

NO LOCATION. There is no GPS in this tablet and nobody asked for a log of
where a radar was. The rolling log keeps the band, the frequency, the time and
the peak strength, and nothing else.

WHAT IT WILL NOT DO: change the detector's settings. lib/esp.py cannot even
encode a settings write; the five requests it can send are version, start and
stop alert data, and mute on and off.
"""

import asyncio
import json
import math
import os
import random
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esp  # noqa: E402

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar"), "v1.json")
STATE = os.path.join(STATE_DIR, "v1.json")
ASK = os.path.join(STATE_DIR, "v1-ask.json")

STALE = 10            # s without a write: whoever was writing has stopped
LOG_KEEP = 50         # the rolling log
ENCOUNTER_GAP = 3.0   # s without a matching alert before an encounter is over
SAME_FREQ_MHZ = 30    # an alert within this of a live one, same band, is the same encounter
NO_FACE = 1.5         # s of a dark detector face before alerts the table never cleared are dropped


# ---- the file the app reads ---------------------------------------------------------------

def today():
    return time.strftime("%Y-%m-%d")


def blank(source):
    return {"source": source, "connected": False, "name": None, "address": None,
            "version": None, "gen2": None, "mode": None, "muted": None, "auto_muted": None,
            "alerts": [], "bogeys": 0, "bars": 0, "leds": {"bands": [], "arrows": []},
            "alerts_at": None, "last_alert_at": None,
            "today": {"date": today(), "count": 0}, "log": [],
            "note": None, "framing_errors": 0, "at": time.time()}


def write(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def read_file():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def read_public():
    """For the API. {"present": False} when nothing is or was ever connected."""
    st = read_file()
    if st is None:
        return {"present": False}
    st["present"] = True
    st["age"] = max(0.0, time.time() - float(st.get("at", 0)))
    if (st.get("today") or {}).get("date") != today():
        st["today"] = {"date": today(), "count": 0}
    if st["age"] > STALE:
        # Whoever was writing has stopped. The log and the last alert's time
        # stay (they are history); an alert on screen does not: a banner that
        # outlives the detector that raised it is worse than none.
        st["connected"] = False
        st["alerts"], st["bogeys"], st["bars"] = [], 0, 0
        st["leds"] = {"bands": [], "arrows": []}
        st["stale"] = True
    return st


def take_ask():
    try:
        with open(ASK, encoding="utf-8") as f:
            a = json.load(f)
        os.unlink(ASK)
    except (OSError, ValueError):
        return None
    if not isinstance(a, dict) or time.time() - float(a.get("at", 0)) > 30:
        return None
    return a


def ask(name, value):
    """What the API calls. The only thing the app may ask the detector to do is mute."""
    if name != "mute":
        raise ValueError(f"{name} is not something OmaCar asks of the detector")
    value = bool(value)
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ASK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"name": "mute", "value": value, "at": time.time()}, f)
    os.replace(tmp, ASK)
    return {"asked": "mute", "value": value}


# ---- the encounters: what the log and today's count are made of -------------------------

class Tracker:
    """Turns a stream of alert tables into encounters.

    An alert is the same encounter as a live one when it has the same band and,
    for the radar bands, a frequency within SAME_FREQ_MHZ (a door opener drifts
    a few MHz; a Ka sweep a little more). That is a heuristic of this app, not
    something the V1 reports. An encounter ends ENCOUNTER_GAP seconds after its
    last matching alert. The log keeps band, frequency, time, the direction at
    the peak and the peak strength: no position.
    """

    def __init__(self, log=None, count=None):
        self.log = list(log or [])[:LOG_KEEP]
        self.day = (count or {}).get("date") or today()
        self.count = int((count or {}).get("count") or 0)
        self.live = []                       # [(entry, last_seen)]

    def _roll(self):
        if self.day != today():
            self.day, self.count = today(), 0

    def update(self, alerts, now):
        self._roll()
        self.prune(now)      # an encounter that went quiet for the gap is over, even if nobody ticked
        used = set()
        for a in alerts:
            hit = None
            for i, (entry, _) in enumerate(self.live):
                if i in used or entry["band"] != a["band"]:
                    continue
                f1, f2 = entry.get("freq_mhz"), a.get("freq_mhz")
                if f1 is None or f2 is None or abs(f1 - f2) <= SAME_FREQ_MHZ:
                    hit = i
                    break
            if hit is None:
                entry = {"at": round(now, 1), "band": a["band"], "freq_mhz": a.get("freq_mhz"),
                         "dir": a.get("dir"), "peak": a["strength"], "secs": 0.0}
                self.live.append((entry, now))
                self.log.insert(0, entry)
                del self.log[LOG_KEEP:]
                self.count += 1
                hit = len(self.live) - 1
            entry = self.live[hit][0]
            if a["strength"] > entry["peak"]:
                entry["peak"], entry["dir"] = a["strength"], a.get("dir")
                if a.get("freq_mhz"):
                    entry["freq_mhz"] = a["freq_mhz"]
            entry["secs"] = round(now - entry["at"], 1)
            self.live[hit] = (entry, now)
            used.add(hit)
        self.prune(now)

    def prune(self, now):
        self.live = [(e, t) for e, t in self.live if now - t <= ENCOUNTER_GAP]


# ---- one detector's worth of state --------------------------------------------------------

class Session:
    """Bytes from the radio (or the simulator) in; the state the app reads out."""

    def __init__(self, source, prior=None):
        self.source = source
        self.framer = esp.Framer()
        self.table = esp.AlertTable()
        prior = prior or {}
        self.tracker = Tracker(prior.get("log"), prior.get("today"))
        self.st = blank(source)
        self.st["last_alert_at"] = prior.get("last_alert_at")
        self.v1_id = None                  # 9 or 10: who to address requests to
        self.time_slicing = False
        self.display_at = 0.0
        self.alert_packet_at = 0.0
        self.face_dark_since = None
        self.face = None
        self.rows = []

    # -- in
    def feed(self, data, now=None):
        now = time.time() if now is None else now
        for p in self.framer.feed(data):
            self._packet(p, now)
        self.st["framing_errors"] = self.framer.bad

    def _packet(self, p, now):
        if not (p.from_v1 and p.for_me):
            return          # an echo of our own request, or somebody else's conversation
        if p.pid == esp.INF_DISPLAY_DATA:
            d = esp.decode_display(p.payload)
            if d:
                self.v1_id, self.time_slicing, self.display_at, self.face = p.orig, d["time_slicing"], now, d
                self.st.update(muted=d["soft"], mode=d["mode"] or self.st["mode"],
                               auto_muted=d["auto_muted"], bars=d["bars"],
                               leds={"bands": d["bands"], "arrows": d["arrows"]})
                lit = bool(d["bands"] or d["arrows"] or d["bars"])
                self.face_dark_since = None if lit else (self.face_dark_since or now)
                if self.rows:
                    self._refresh(now)
        elif p.pid == esp.RESP_ALERT_DATA:
            self.alert_packet_at = now
            table = self.table.feed(esp.decode_alert(p.payload), now)
            if table is not None:
                self._table(table, now)
        elif p.pid == esp.RESP_VERSION:
            v = esp.decode_version(p.payload)
            if v:
                self.st["version"], self.st["gen2"] = v, esp.is_gen2(v)
        elif p.pid in (esp.RESP_UNSUPPORTED, esp.RESP_NOT_PROCESSED, esp.RESP_DATA_ERROR, esp.INF_V1_BUSY):
            self.st["note"] = {esp.RESP_UNSUPPORTED: "the detector does not support a request",
                               esp.RESP_NOT_PROCESSED: "the detector did not act on a request",
                               esp.RESP_DATA_ERROR: "the detector reported a data error",
                               esp.INF_V1_BUSY: "the detector is busy"}[p.pid]

    def _table(self, table, now):
        self.rows = []
        for a in table:
            if a["band"] is None:
                continue
            front, rear = esp.bars_from_raw(a["front_raw"]), esp.bars_from_raw(a["rear_raw"])
            strength = rear if a["dir"] == "rear" else front if a["dir"] == "front" else max(front, rear)
            self.rows.append({"band": a["band"], "photo": a["photo"], "freq_mhz": a["freq_mhz"],
                              "dir": a["dir"], "front": front, "rear": rear, "strength": strength,
                              "exact": False, "priority": a["priority"]})
        self.st["alerts_at"] = now
        self._refresh(now)

    def _refresh(self, now):
        """The alerts as shown: the last table, with the V1's own bars on the priority one.

        Run on every alert table AND every display packet, because the two
        arrive on separate clocks and neither is guaranteed to come first.
        """
        alerts = [dict(a) for a in self.rows]
        if alerts:
            pri = next((a for a in alerts if a["priority"]), None) or max(alerts, key=lambda a: a["strength"])
            for a in alerts:
                a["priority"] = a is pri
            # The priority alert's strength is what the V1's own bar graph
            # shows: exact, and better than any scale of ours.
            if self.face and self.face["bars"] and now - self.display_at < 1.0:
                pri["strength"], pri["exact"] = self.face["bars"], True
            alerts.sort(key=lambda a: (not a["priority"], -a["strength"]))
            # Only a table that is still arriving counts as the encounter going
            # on. A display packet re-drawing an old table (the V1's empty
            # table not yet in) must not start a new encounter. If a V1 only
            # sent tables on change this would end encounters early: unverified.
            if now - (self.st["alerts_at"] or 0) <= ENCOUNTER_GAP:
                self.st["last_alert_at"] = now
                self.tracker.update(alerts, now)
        self.st["alerts"], self.st["bogeys"] = alerts, len(alerts)

    # -- on the clock
    def tick(self, now=None):
        now = time.time() if now is None else now
        # If the V1 never sent an empty table but its face has been dark a
        # moment, the alerts are over: the display data streams on its own.
        if self.st["alerts"] and self.face_dark_since and now - self.face_dark_since > NO_FACE:
            self.rows = []
            self.st["alerts"], self.st["bogeys"] = [], 0
        self.tracker.prune(now)

    def state(self):
        st = dict(self.st)
        st["log"] = self.tracker.log
        st["today"] = {"date": self.tracker.day, "count": self.tracker.count}
        st["source"] = self.source
        return st


# ---- the simulator ---------------------------------------------------------------------------
#
# Believable encounters, run through the very same bytes the radio would send
# (esp.alert_payload / esp.display_payload -> Framer -> Session), so the codec
# is exercised end to end. It labels itself Simulated in the source, in the
# name, and the screens say so.

def _raw(bars):
    return 0 if bars <= 0 else 32 * bars - 15           # inverts esp.bars_from_raw


def _tri(t, up, hold, down, peak):
    """Bars 0..peak: up over `up` s, flat for `hold`, down over `down`."""
    if t < 0:
        return 0
    if t < up:
        return max(1, round(peak * t / up))
    if t < up + hold:
        return peak
    if t < up + hold + down:
        return max(0, round(peak * (1 - (t - up - hold) / down)))
    return 0


def enc_k(rnd):
    """A door opener / speed sign: K band, front, drifting, in and out in ~9 s."""
    peak, base, dur = rnd.choice([3, 4, 5]), 24150 + rnd.randint(-12, 12), rnd.uniform(7, 11)
    def at(t):
        if t > dur:
            return None
        return [{"band": "K", "freq": base + int(4 * math.sin(t * 1.7)), "dir": "front",
                 "front": _tri(t, dur * .4, dur * .1, dur * .5, peak), "rear": 0}]
    return dur, at


def enc_ka(rnd):
    """A Ka ramp from the front: building to full, the side as it passes, then the rear fading."""
    base = 34700 + rnd.choice([-95, -40, 0, 30, 70])
    def at(t):
        if t < 0 or t > 25:
            return None
        if t < 11:
            return [{"band": "Ka", "freq": base + int(2 * math.sin(t)), "dir": "front",
                     "front": _tri(t, 10, 1, 0.01, 8), "rear": 0}]
        if t < 13:
            return [{"band": "Ka", "freq": base, "dir": "side", "front": 6, "rear": 6}]
        return [{"band": "Ka", "freq": base, "dir": "rear", "front": 0,
                 "rear": _tri(t - 13, 0.01, 0, 12, 7)}]
    return 25, at


def enc_laser(rnd):
    """A laser burst: a second or two at full, sometimes twice."""
    gap = rnd.choice([None, 4.5])
    def at(t):
        if 0 <= t <= 1.8 or (gap and gap <= t <= gap + 1.4):
            return [{"band": "Laser", "freq": None, "dir": "front", "front": 8, "rear": 0}]
        return None
    return (gap + 1.4 if gap else 1.8), at


def enc_x(rnd):
    """A weak X-band bogey, behind: the sort that rides along with something else."""
    dur = rnd.uniform(6, 9)
    def at(t):
        if 0 <= t <= dur:
            return [{"band": "X", "freq": 10525 + rnd.randint(-2, 2), "dir": "rear",
                     "front": 0, "rear": _tri(t, dur * .5, 0, dur * .5, 2)}]
        return None
    return dur, at


ENCOUNTERS = {"k": enc_k, "ka": enc_ka, "laser": enc_laser}


def _frames(alerts, soft, mode=2):
    """The bytes a checksum V1 would send for one instant: an alert table and the display."""
    out = b""
    n = len(alerts)
    if n:
        order = sorted(range(n), key=lambda i: (alerts[i]["band"] != "Laser",
                                                alerts[i]["band"] != "Ka", -max(alerts[i]["front"], alerts[i]["rear"])))
        for k, i in enumerate(order, 1):
            a = alerts[i]
            out += esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA,
                                    esp.alert_payload(k, n, a["band"], a["freq"] or 0, _raw(a["front"]),
                                                      _raw(a["rear"]), a["dir"], priority=(k == 1)))
        top = alerts[order[0]]
        bars = top["rear"] if top["dir"] == "rear" else top["front"] if top["dir"] == "front" else max(top["front"], top["rear"])
        face = esp.display_payload(str(min(n, 9)), bars, {a["band"] for a in alerts} - {"Ku", "Photo"},
                                   {top["dir"]} if top["dir"] else (), soft, mode, mute_led=soft)
    else:
        out += esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA, b"\x00")
        face = esp.display_payload({1: "A", 2: "l", 3: "L"}[mode], 0, (), (), soft, mode, mute_led=soft)
    return out, esp.build_packet(esp.BROADCAST, esp.V1_CHECKSUM, esp.INF_DISPLAY_DATA, face)


def sim(seconds=None, seed=None, scenario="mix", step=0.25, lead=None):
    """A believable detector. `scenario` picks what happens: mix (the default:
    one encounter after ten seconds, then one every 30-90), ka, k, laser (that
    one, over and over), loop (all of them, back to back), idle (nothing)."""
    rnd = random.Random(seed)
    sess = Session("sim")
    sess.st.update(connected=True, name="Valentine One (simulated)", version="V4.1037", gen2=True,
                   mode="Logic", muted=False)
    start = time.time()
    running = []                       # [(start_t, duration, at)]
    nxt = start + (lead if lead is not None else 10 if scenario == "mix" else 2)
    queue = ["k", "ka", "laser"]
    soft, mode, last_table = False, 2, 0
    while seconds is None or time.time() - start < seconds:
        now = time.time()
        a = take_ask()
        if a and a["name"] == "mute":
            soft = bool(a["value"])
        if scenario != "idle" and now >= nxt:
            if scenario == "loop":
                kind = queue[0]
                queue = queue[1:] + queue[:1]
            elif scenario == "mix":
                kind = rnd.choice(["k", "k", "ka", "laser", "ka"])
            else:
                kind = scenario
            dur, at = ENCOUNTERS[kind](rnd)
            running.append((now, dur, at))
            if kind == "ka" and rnd.random() < .4:
                d2, at2 = enc_x(rnd)
                running.append((now + 2, d2, at2))
            nxt = now + dur + (4 if scenario in ("loop", "ka", "k", "laser") else rnd.uniform(30, 90))
        alerts = []
        for t0, dur, at in running:
            alerts += at(now - t0) or []
        running = [r for r in running if now - r[0] <= r[1] + 1]
        table, face = _frames(alerts, soft, mode)
        sess.feed(face, now)
        if alerts or last_table or now - sess.alert_packet_at > 5:
            sess.feed(table, now)
        last_table = len(alerts)
        sess.st["muted"] = soft
        sess.tick(now)
        write(sess.state())
        time.sleep(step)


# ---- the radio -----------------------------------------------------------------------------

def configured_address():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return (json.load(f) or {}).get("address")
    except (OSError, ValueError):
        return None


async def _find(bleak, timeout=10.0):
    found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        if esp.SERVICE in [u.lower() for u in (adv.service_uuids or [])]:
            out.append((dev.address, adv.local_name or dev.name or "V1connection", adv.rssi))
    return sorted(out, key=lambda x: -(x[2] or -999))


async def _link(bleak, stop):
    """One connection, held until it fails or `stop` is set. Returns a sentence saying why it ended."""
    want = configured_address()
    found = await _find(bleak, 8.0)
    if want:
        found = [f for f in found if f[0].lower() == want.lower()] or [(want, None, None)]
    if not found:
        return "no V1connection LE in range (is the V1 on, and its Bluetooth free?)"
    address, name, _ = found[0]
    prior = read_file()
    sess = Session("ble", prior if prior and prior.get("source") == "ble" else None)
    sess.st.update(name=name or "V1connection", address=address)
    started = [False]
    last_start = [0.0]
    last_json = [None, 0.0]

    def flush(force=False):
        s = sess.state()
        s["at"] = 0
        j = json.dumps(s, sort_keys=True)
        if force or j != last_json[0] or time.time() - last_json[1] > 2.0:
            last_json[0], last_json[1] = j, time.time()
            write(sess.state())

    async with bleak.BleakClient(address, timeout=20.0) as client:
        async def send(pid):
            await client.write_gatt_char(esp.WRITE, esp.build_request(sess.v1_id, pid))

        def heard(_sender, data):
            sess.feed(bytes(data))

        await client.start_notify(esp.NOTIFY, heard)
        sess.st["connected"] = True
        sent_version = False
        try:
            while client.is_connected and not stop.is_set():
                now = time.time()
                sess.tick(now)
                if now - sess.display_at > 8 and sess.display_at:
                    sess.st["note"] = "the detector has gone quiet"
                if sess.v1_id and sess.time_slicing:
                    if not sent_version:
                        await send(esp.REQ_VERSION)
                        sent_version = True
                    # Start alert data once, and again if the face shows an
                    # alert but no alert packet has come (a V1 that restarted).
                    face_alerting = bool(sess.face and (sess.face["bands"] or sess.face["bars"]))
                    if (not started[0]) or (face_alerting and now - sess.alert_packet_at > 3
                                            and now - last_start[0] > 5):
                        await send(esp.REQ_START_ALERT_DATA)
                        started[0], last_start[0] = True, now
                    a = take_ask()
                    if a and a["name"] == "mute":
                        await send(esp.REQ_MUTE_ON if a["value"] else esp.REQ_MUTE_OFF)
                flush()
                await asyncio.sleep(0.25)
        finally:
            if started[0] and client.is_connected and sess.v1_id:
                try:
                    await send(esp.REQ_STOP_ALERT_DATA)     # leave the V1 as we found it
                except Exception:                            # noqa: BLE001 -- going away anyway
                    pass
    sess.st["connected"] = False
    sess.st["alerts"], sess.st["bogeys"] = [], 0
    flush(True)
    return "stopped" if stop.is_set() else "the connection dropped"


async def _run(bleak):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    wait = 5
    while not stop.is_set():
        try:
            why = await _link(bleak, stop)
        except Exception as e:                       # noqa: BLE001 -- the radio says many things
            why = f"{type(e).__name__}: {e}"
        prev = read_file() or blank("ble")
        prev.update(connected=False, note=why, alerts=[], bogeys=0, bars=0)
        write(prev)
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), wait)
        except asyncio.TimeoutError:
            pass
        wait = min(60, wait * 2) if "no V1connection" in why else 5


def run():
    try:
        import bleak
    except ImportError:
        st = blank("ble")
        st["note"] = "Bluetooth library not installed: run omacar v1 setup"
        write(st)
        print("  bleak is not installed in this interpreter: omacar v1 setup", file=sys.stderr)
        return 1
    asyncio.run(_run(bleak))
    return 0


def scan():
    try:
        import bleak
    except ImportError:
        print("  bleak is not installed: omacar v1 setup", file=sys.stderr)
        return 1
    found = asyncio.run(_find(bleak))
    if not found:
        print("  no V1connection LE in range. Is the V1 powered, and is nothing else (a phone) holding it?")
        return 1
    for address, name, rssi in found:
        print(f"  {address}  {name}  {rssi} dBm")
    print(f"\n  To pin one: echo '{{\"address\": \"{found[0][0]}\"}}' > {CONFIG}")
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "scan":
        return scan()
    if what == "run":
        return run()
    if what == "sim":
        secs, scen = None, "mix"
        for a in argv[1:]:
            if a in ("mix", "ka", "k", "laser", "loop", "idle"):
                scen = a
            else:
                secs = float(a)
        sim(secs, scenario=scen)
        return 0
    print(f"  omacar v1: no such thing as '{what}'.\n"
          "  status | scan | run | sim | setup", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
