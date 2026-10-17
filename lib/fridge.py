#!/usr/bin/env python3
"""The Dometic CFX3 in the back, on Home: its temperature, and two controls.

Owner, 2026-10-17: a Dometic CFX3 45 (single zone). It speaks a pub/sub
protocol over Bluetooth LE (lib/cfx.py); this keeps one connection to it and
writes what it hears to fridge.json, which the server hands to the app. The
app's two taps (power, set temperature) come back through fridge-ask.json,
the same file-drop the ignition loop uses, so the server never touches the
radio.

    omacar fridge status     what fridge.json says now
    omacar fridge scan       find CFX3_* fridges nearby (10 s)
    omacar fridge run        hold the connection (omacar-fridge.service)
    omacar fridge sim        a simulated fridge, for seeing the card; it
                             says "Simulated" on the card, every time

The fridge's Bluetooth mode has to be PAIR (its own menu) for the first
connection. bleak, the Bluetooth library, lives in OmaCar's venv:
`omacar fridge setup` installs it.

WHAT IS NEVER KEPT. The fridge also publishes the Wi-Fi networks and passwords
it was given. lib/cfx.py decodes only an allow-list, so those are dropped
before anything here sees them.
"""

import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfx  # noqa: E402

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar"), "fridge.json")
STATE = os.path.join(STATE_DIR, "fridge.json")
ASK = os.path.join(STATE_DIR, "fridge-ask.json")

TRAIL_EVERY = 15          # s between trail points
TRAIL_KEEP = 60 * 60      # an hour of them
STALE = 20                # s without a write: whoever was writing has stopped


# ---- the file the app reads ---------------------------------------------------------

def blank(source):
    return {"source": source, "connected": False, "name": None, "kind": None,
            "power": None, "battery_v": None, "protection": None, "supply": None,
            "zones": [{"temp": None, "set": None, "door": None, "power": None,
                       "range": None}],
            "alerts": [], "trail": [], "note": None, "at": time.time()}


def write(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["at"] = time.time()
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def read_public():
    """For the API. {"present": False} when nothing is or was ever connected."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"present": False}
    st["present"] = True
    st["age"] = max(0.0, time.time() - float(st.get("at", 0)))
    if st["age"] > STALE:
        # The last reading stays (it is the last thing known about the food),
        # but it is not "connected" any more, and the card says how old it is.
        st["connected"] = False
    return st


ALERT_WORDS = {
    "alert_door": "Door open",
    "alert_voltage": "Battery low",
    "alert_temp": "Not holding temperature",
    "err_compressor_start": "Compressor would not start",
    "err_compressor_speed": "Compressor speed fault",
    "err_fan_voltage": "Fan over-voltage",
    "err_controller_hot": "Controller too hot",
    "err_sensor_open": "Temperature sensor open",
    "err_sensor_short": "Temperature sensor shorted",
}
SUPPLY = {0: "AC", 1: "DC", 2: "Solar"}
PROTECTION = {0: "Low", 1: "Medium", 2: "High"}


def apply(state, name, value, flags):
    """One decoded PUB folded into the state the app reads."""
    z = state["zones"]
    if name.startswith(("z0_", "z1_")):
        i = int(name[1])
        while len(z) <= i:
            z.append({"temp": None, "set": None, "door": None, "power": None, "range": None})
        z[i][name[3:]] = value
    elif name == "battery_v":
        state["battery_v"] = value
    elif name == "protection":
        state["protection"] = PROTECTION.get(value)
    elif name == "source":
        state["supply"] = SUPPLY.get(value)
    elif name in ("power", "zones", "unit"):
        state[name] = value
    elif name in ALERT_WORDS:
        flags[name] = bool(value)
        state["alerts"] = [ALERT_WORDS[k] for k, on in flags.items() if on]


def add_trail(state, now):
    t = state["trail"]
    temp = state["zones"][0].get("temp")
    if temp is None or (t and now - t[-1][0] < TRAIL_EVERY):
        return
    t.append([round(now, 1), temp])
    while t and now - t[0][0] > TRAIL_KEEP:
        t.pop(0)


def take_ask():
    try:
        with open(ASK, encoding="utf-8") as f:
            a = json.load(f)
        os.unlink(ASK)
    except (OSError, ValueError):
        return None
    if time.time() - float(a.get("at", 0)) > 30:
        return None
    return a


def ask(name, value):
    """What the API calls: validated here, sent by whichever loop is running."""
    if name not in cfx.WRITABLE:
        raise ValueError(f"{name} is not something OmaCar sets")
    if name.endswith("_set"):
        v = float(value)
        if not math.isfinite(v):
            raise ValueError("not a temperature")
        cfx.encode_set(name, v)          # raises outside -22..+10 C
        value = round(v * 2) / 2         # the panel's own half-degree steps
    else:
        value = bool(value)
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ASK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"name": name, "value": value, "at": time.time()}, f)
    os.replace(tmp, ASK)
    return {"asked": name, "value": value}


# ---- the simulator ------------------------------------------------------------------

def sim(seconds=None, seed=None):
    """A believable CFX3 45: a compressor cycling about its set point, a lid
    opened now and then, a battery on the car's 12 V. Source "sim", always."""
    rnd = random.Random(seed)
    st = blank("sim")
    st.update(connected=True, name="CFX3 45 (simulated)", kind="single zone",
              power=True, battery_v=13.4, protection="Medium", supply="DC")
    z = st["zones"][0]
    z.update(temp=4.6, set=3.0, door=False, power=True, range=[-22.0, 10.0])
    compressor, door_until, door_since, start = True, 0.0, None, time.time()
    # An hour of history, so the trail is not empty the moment the card opens.
    for k in range(TRAIL_KEEP // TRAIL_EVERY, 0, -1):
        t = start - k * TRAIL_EVERY
        st["trail"].append([round(t, 1), round(3.0 + 0.9 * math.sin(k / 9.0)
                                                 + 0.25 * math.sin(k / 2.3), 1)])
    while seconds is None or time.time() - start < seconds:
        now = time.time()
        a = take_ask()
        if a:
            if a["name"] == "power":
                st["power"] = z["power"] = bool(a["value"])
            elif a["name"] == "z0_set":
                z["set"] = float(a["value"])
        door = now < door_until
        if not door and rnd.random() < 0.004:
            door_until = now + rnd.uniform(6, 20)
            door = True
        door_since = (door_since or now) if door else None
        z["door"] = door
        if st["power"]:
            if z["temp"] >= z["set"] + 1.0:
                compressor = True
            elif z["temp"] <= z["set"] - 0.5:
                compressor = False
            z["temp"] += (-0.05 if compressor else 0.012) + (0.06 if door else 0.0)
        else:
            z["temp"] += (21.0 - z["temp"]) * 0.002     # warming toward the cabin
        z["temp"] = round(z["temp"], 2)
        st["battery_v"] = round(13.4 + (-0.25 if compressor and st["power"] else 0.1)
                                + rnd.uniform(-0.03, 0.03), 2)
        st["alerts"] = ["Door open"] if door_since and now - door_since >= 10 else []
        st["compressor"] = bool(compressor and st["power"])
        add_trail(st, now)
        write(st)
        time.sleep(1.0)


# ---- the radio ------------------------------------------------------------------------

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
        name = adv.local_name or dev.name
        if cfx.is_cfx(name):
            out.append((dev.address, name, cfx.product_of(adv.manufacturer_data)))
    return out


async def _link(bleak):
    """One connection, held until it fails. Returns a sentence saying why it ended."""
    import asyncio
    want = configured_address()
    found = await _find(bleak)
    if want:
        found = [f for f in found if f[0].lower() == want.lower()] or [(want, None, 1)]
    if not found:
        return "no CFX3 in range (is its Bluetooth mode set to PAIR?)"
    address, name, product = found[0]
    st = blank("ble")
    st.update(name=(name or "CFX3").replace("_", " "), kind=cfx.KIND.get(product))
    flags, acked = {}, asyncio.Event()
    last_sent = [time.time()]

    async with bleak.BleakClient(address, timeout=20.0) as client:
        async def send(packet):
            last_sent[0] = time.time()
            await client.write_gatt_char(cfx.WRITE, packet, response=True)

        def heard(_sender, data):
            action, nm, value = cfx.decode(bytes(data))
            if action == cfx.ACK:
                acked.set()
            if nm is not None and value is not None:
                apply(st, nm, value, flags)
            if action in cfx.NEEDS_ACK:
                asyncio.ensure_future(send(cfx.ack()))

        await client.start_notify(cfx.NOTIFY, heard)
        await send(cfx.ping())
        try:
            await asyncio.wait_for(acked.wait(), 6)
        except asyncio.TimeoutError:
            return "the fridge did not answer its first ping"
        await send(cfx.subscribe(product))
        st["connected"] = True
        while client.is_connected:
            a = take_ask()
            if a:
                try:
                    await send(cfx.encode_set(a["name"], a["value"]))
                except ValueError as why:
                    st["note"] = str(why)
            if time.time() - last_sent[0] > 3:
                await send(cfx.ping())
            add_trail(st, time.time())
            write(st)
            await asyncio.sleep(1.0)
    st["connected"] = False
    write(st)
    return "the connection dropped"


def run():
    import asyncio
    try:
        import bleak
    except ImportError:
        st = blank("ble")
        st["note"] = "Bluetooth library not installed: run omacar fridge setup"
        write(st)
        print("  bleak is not installed in this interpreter: omacar fridge setup",
              file=sys.stderr)
        return 1
    wait = 5
    while True:
        try:
            why = asyncio.run(_link(bleak))
        except Exception as e:                       # noqa: BLE001 -- the radio says many things
            why = f"{type(e).__name__}: {e}"
        prev = read_public()
        st = prev if prev.get("present") else blank("ble")
        st.pop("present", None)
        st.pop("age", None)
        st.update(connected=False, note=why)
        write(st)
        time.sleep(wait)
        wait = min(60, wait * 2) if "no CFX3" in why else 5


def scan():
    import asyncio
    try:
        import bleak
    except ImportError:
        print("  bleak is not installed: omacar fridge setup", file=sys.stderr)
        return 1
    found = asyncio.run(_find(bleak))
    if not found:
        print("  no CFX3 in range. Set the fridge's Bluetooth mode to PAIR and try again.")
        return 1
    for address, name, product in found:
        print(f"  {address}  {name}  {cfx.KIND.get(product, '?')}")
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
        sim(float(argv[1]) if len(argv) > 1 else None)
        return 0
    print(f"  omacar fridge: no such thing as '{what}'.\n"
          "  status | scan | run | sim | setup", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
