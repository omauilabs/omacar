"""The Dometic CFX3's Bluetooth language, encoded and decoded. No radio here.

Ported from andrewbackway/esphome-dometic_cfx_ble (MIT): its protocol.md and
scripts/test.py, read 2026-10-17. A packet is one action byte, then for PUB and
SUB a four-byte topic, then for PUB the value. Values are little-endian. There
is no length and no checksum: BLE's own integrity is all there is.

ONLY AN ALLOW-LIST IS EVER DECODED. Subscribing to the fridge's group makes it
publish everything it knows, and that includes the Wi-Fi network names and
passwords it was given (STATION_SSID_*, STATION_PASSWORD_*,
CFX_DIRECT_PASSWORD_*). Those topics are not in TOPICS below, so decode()
returns None for them and nothing about them is kept, logged or shown. A topic
nobody listed is dropped the same way.
"""

import struct

SERVICE = "537a0300-0995-481f-926c-1604e23fd515"
WRITE = "537a0301-0995-481f-926c-1604e23fd515"
NOTIFY = "537a0302-0995-481f-926c-1604e23fd515"

PUB, SUB, PING, HELLO, ACK, NAK, NOP = range(7)

# The fridge says "no value" with the most negative deci-degree.
NO_VALUE = -32768

# Product type from the advertisement -> the group to subscribe to.
GROUP = {1: (1, 0, 0, 129), 2: (2, 0, 0, 129), 3: (3, 0, 0, 129)}
KIND = {1: "single zone", 2: "single zone + icemaker", 3: "dual zone"}

TEMP, BOOL, I8, U8, VOLT, RANGE = "temp", "bool", "i8", "u8", "volt", "range"

# name: (topic, type). Only what the card shows or sets.
TOPICS = {
    "power":        ((0, 0, 3, 1), BOOL),
    "battery_v":    ((0, 1, 3, 1), VOLT),
    "protection":   ((0, 2, 3, 1), U8),     # 0 low, 1 medium, 2 high
    "source":       ((0, 5, 3, 1), I8),     # 0 AC, 1 DC, 2 solar
    "zones":        ((0, 128, 0, 1), I8),
    "unit":         ((0, 0, 2, 1), I8),     # what the fridge's own panel shows
    "z0_power":     ((0, 0, 1, 1), BOOL),
    "z0_temp":      ((0, 1, 1, 1), TEMP),
    "z0_set":       ((0, 2, 1, 1), TEMP),
    "z0_door":      ((0, 8, 1, 1), BOOL),
    "z0_range":     ((0, 128, 1, 1), RANGE),
    "z1_power":     ((16, 0, 1, 1), BOOL),
    "z1_temp":      ((16, 1, 1, 1), TEMP),
    "z1_set":       ((16, 2, 1, 1), TEMP),
    "z1_door":      ((16, 8, 1, 1), BOOL),
    "z1_range":     ((16, 128, 1, 1), RANGE),
    "alert_door":    ((0, 1, 5, 1), BOOL),
    "alert_voltage": ((0, 2, 5, 1), BOOL),
    "alert_temp":    ((0, 0, 5, 1), BOOL),
    "err_compressor_start": ((0, 51, 4, 1), BOOL),
    "err_compressor_speed": ((0, 52, 4, 1), BOOL),
    "err_fan_voltage":      ((0, 50, 4, 1), BOOL),
    "err_controller_hot":   ((0, 53, 4, 1), BOOL),
    "err_sensor_open":      ((0, 1, 4, 1), BOOL),
    "err_sensor_short":     ((0, 2, 4, 1), BOOL),
}
BY_TOPIC = {t: (name, kind) for name, (t, kind) in TOPICS.items()}
SIZE = {TEMP: 2, BOOL: 1, I8: 1, U8: 1, VOLT: 2, RANGE: 4}

# What may be written, and the bounds the server holds it to whatever the
# fridge's own range says (the CFX3 panel goes -22..+10 C).
WRITABLE = {"power", "z0_power", "z1_power", "z0_set", "z1_set"}
SET_MIN, SET_MAX = -22.0, 10.0


def _temp(raw):
    return None if raw == NO_VALUE else raw / 10.0


def decode(packet):
    """(action, name or None, value or None). name None: not on the allow-list."""
    if not packet:
        return None, None, None
    action = packet[0]
    if action != PUB or len(packet) < 5:
        return action, None, None
    topic = tuple(packet[1:5])
    if topic not in BY_TOPIC:
        return action, None, None     # including every Wi-Fi credential topic
    name, kind = BY_TOPIC[topic]
    body = bytes(packet[5:5 + SIZE[kind]])
    if len(body) < SIZE[kind]:
        return action, name, None
    if kind == TEMP:
        return action, name, _temp(struct.unpack("<h", body)[0])
    if kind == BOOL:
        return action, name, body[0] == 1
    if kind == I8:
        return action, name, struct.unpack("<b", body)[0]
    if kind == U8:
        return action, name, body[0]
    if kind == VOLT:
        return action, name, struct.unpack("<H", body)[0] / 10.0
    lo, hi = struct.unpack("<hh", body)
    return action, name, [_temp(lo), _temp(hi)]


def encode_set(name, value):
    """A PUB that sets one writable value. Raises ValueError for anything else."""
    if name not in WRITABLE:
        raise ValueError(f"{name} is not something OmaCar sets")
    topic, kind = TOPICS[name]
    if kind == BOOL:
        body = bytes([1 if value else 0])
    else:
        v = float(value)
        if not SET_MIN <= v <= SET_MAX:
            raise ValueError(f"{v:g} °C is outside the fridge's {SET_MIN:g}..{SET_MAX:g} °C")
        body = struct.pack("<h", int(round(v * 10)))
    return bytes([PUB, *topic]) + body


def subscribe(product):
    return bytes([SUB, *GROUP.get(product, GROUP[1])])


def ping():
    return bytes([PING])


def ack():
    return bytes([ACK])


def product_of(manufacturer_data):
    """Product type (1 SZ, 2 SZI, 3 DZ) from the advertisement, as the reference does.

    scripts/test.py get_product_type(): bleak's manufacturer_data[0xFFFF], hex
    positions 4-5, i.e. byte 2; single zone when it cannot tell.
    """
    blob = bytes((manufacturer_data or {}).get(0xFFFF, b""))
    return blob[2] if len(blob) > 2 and blob[2] in GROUP else 1


def is_cfx(name):
    """The reference's own filter: the fridge advertises as CFX3_<something>."""
    return bool(name) and str(name).startswith("CFX3_")


# Every one of these from the fridge is answered with an ACK (scripts/test.py).
NEEDS_ACK = {PUB, PING, SUB, NOP, HELLO}
