"""Ready-made cabin monitors, heard over Bluetooth: no build, no pairing, no app.

Owner, 2026-10-17: "is there a full device I can install instead ready to go
that we can get the data without having to build it?" Two can be listened to
directly, because each broadcasts its current readings in its Bluetooth
advertisements:

  * Qingping Air Monitor Lite (CGDN1): temperature, humidity, PM2.5, PM10,
    CO2. Service data 0xFDCD, a run of (id, size, value) records. Layout ported
    from qingping-ble (Bluetooth-Devices/qingping-ble, src/qingping_ble/
    parser.py, MIT; itself from Ernst79/bleparser), read 2026-10-17.
  * Aranet4: CO2, temperature, humidity, pressure. Manufacturer data 0x0702,
    present only with "Smart Home integrations" on in the Aranet Home app
    (firmware 1.2.0+). Layout and scaling ported from Aranet4-Python
    (Anrijs/Aranet4-Python, aranet4/client.py: Aranet4Advertisement,
    ManufacturerData, CurrentReading._set; MIT), read 2026-10-17.

Their numbers become the "cabin" node through the same Record.ingest() and
clean_value() as a USB node's line, so the card, the screen and the stale-air
prompt need nothing new.

ONLY A PINNED MONITOR IS EVER BELIEVED. Every Aranet4 and Qingping in range
broadcasts the same way -- the car in the next bay, a neighbour's kitchen.
`omacar env scan` lists what it hears, nearest first; `omacar env use
<address>` pins one in ~/.config/omacar/env-nodes.json as
{"cabin_ble": {"address": ...}}.
Without a pin nothing heard over the air is used.
"""

import struct

QINGPING_UUID = "0000fdcd-0000-1000-8000-00805f9b34fb"
ARANET_ID = 0x0702
# qingping-ble's DEVICE_TYPES ids that are the Air Monitor Lite.
QINGPING_AIR = {0x0E: "Qingping Air Monitor Lite", 0x24: "Qingping Air Monitor Lite"}


def qingping(service_data):
    """{t_c, rh, pm25, pm10, co2} (whichever it carried) from an Air Monitor
    Lite's advertisement, or None when it is not one."""
    raw = (service_data or {}).get(QINGPING_UUID)
    if raw is None:
        return None
    b = bytes(raw)
    # qingping-ble pads four zero bytes in front and reads the device id at 5
    # and the first record at 12: in the unpadded bytes, 1 and 8.
    if len(b) < 8 or b[1] not in QINGPING_AIR:
        return None
    out, i = {}, 8
    while i + 2 <= len(b):
        rid, size = b[i], b[i + 1]
        val = b[i + 2:i + 2 + size]
        if len(val) < size:
            break
        if rid == 0x01 and size == 4:
            t, h = struct.unpack("<hH", val)
            out["t_c"], out["rh"] = t / 10, h / 10
        elif rid == 0x12 and size == 4:
            out["pm25"], out["pm10"] = struct.unpack("<HH", val)
        elif rid == 0x13 and size == 2:
            out["co2"] = struct.unpack("<H", val)[0]
        # 0x18 is a fixed capability bitmap, not CO2 (qingping-ble #115).
        i += 2 + size
    return out or None


def aranet4(manufacturer_data, name=None):
    """{co2, t_c, rh, hpa} from an Aranet4's advertisement, or None.

    None also when Smart Home integrations are off (the flag is bit 5 of the
    first status byte), or the device is another Aranet model.
    """
    raw = (manufacturer_data or {}).get(ARANET_ID)
    if raw is None:
        return None
    b = bytearray(raw)
    if len(b) < 5:
        return None
    named = bool(name) and str(name).startswith(("Aranet4", "Aranet2", "Aranet☢", "AranetRn"))
    # Aranet4-Python: an Aranet4 omits the leading type byte the others carry.
    if (named and str(name).startswith("Aranet4")) or (not named and len(b) in (7, 22)):
        b.insert(0, 0)
    if not b[1] & (1 << 5):                     # Smart Home integrations off
        return None
    if b[0] != 0:                               # 0 = Aranet4; others are not cabin monitors
        return None
    fmt = "<xxxxxxxxxHHHBBBHH"
    end = struct.calcsize(fmt)
    if len(b) < end:
        return None
    co2, temp, pres, hum, _batt, _status, _interval, _ago = struct.unpack(fmt, bytes(b[:end]))
    out = {}
    # CurrentReading._set: these bits mark a reading taken during calibration.
    if not co2 >> 15:
        out["co2"] = co2
    if not (temp >> 14) & 1:
        out["t_c"] = round(temp * 0.05, 2)
    if not pres >> 15:
        out["hpa"] = round(pres * 0.1, 1)
    if not hum >> 8:
        out["rh"] = hum
    return out or None


def decode(service_data, manufacturer_data, name=None):
    """(kind, label, values) for a monitor this understands, or None."""
    q = qingping(service_data)
    if q:
        return "qingping", "Qingping Air Monitor Lite", q
    a = aranet4(manufacturer_data, name)
    if a:
        return "aranet4", "Aranet4", a
    return None


# ---- the radio ------------------------------------------------------------------------

def pinned(cfg):
    """The one monitor this car believes, from env.json, or None."""
    c = (cfg or {}).get("cabin_ble")
    if isinstance(c, dict) and isinstance(c.get("address"), str) and c["address"].strip():
        return c["address"].strip().upper()
    return None


def listen(on_reading, address, stop):
    """Hear the pinned monitor until `stop` is set. on_reading(label, values).

    Runs in its own thread with its own asyncio loop, because the service's
    main loop is plain threads.
    """
    import asyncio
    try:
        from bleak import BleakScanner
    except ImportError:
        return "bleak is not installed: omacar fridge setup installs it"

    def heard(device, adv):
        if device.address.upper() != address:
            return
        hit = decode(adv.service_data, adv.manufacturer_data, adv.local_name or device.name)
        if hit:
            on_reading(hit[1], hit[2])

    async def main():
        async with BleakScanner(detection_callback=heard):
            while not stop.is_set():
                await asyncio.sleep(1.0)

    while not stop.is_set():
        try:
            asyncio.run(main())
        except Exception:                                # noqa: BLE001 -- the adapter came and went
            stop.wait(10)
    return None


def scan(seconds=10.0):
    """[(address, label, rssi, values)] for every monitor heard, nearest first."""
    import asyncio
    from bleak import BleakScanner
    found = asyncio.run(BleakScanner.discover(timeout=seconds, return_adv=True))
    out = []
    for dev, adv in found.values():
        hit = decode(adv.service_data, adv.manufacturer_data, adv.local_name or dev.name)
        if hit:
            out.append((dev.address.upper(), hit[1], adv.rssi, hit[2]))
    return sorted(out, key=lambda r: -(r[2] if r[2] is not None else -999))
