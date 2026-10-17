"""Valentine Research's Extended Serial Protocol (ESP), over Bluetooth LE. No radio here.

What this reads and writes: the framing, the packets OmaCar needs to show a
Valentine One's alerts (respAlertData, infDisplayData, respVersion) and the few
it sends (start/stop alert data, mute on/off, version). Nothing that changes
the detector's settings is encoded anywhere in this file, on purpose: no user
bytes, no sweeps, no volume, no mode.

CREDIT. The constants, identifiers, packet ids, bit layouts and the alert-table
rule below were read from KESPL (https://github.com/DeveloprOfThings/KESPL,
MIT, Copyright (c) 2025 Develpr Of Things): Constants.kt, ESPPacketId.kt,
ESPDevice.kt, bluetooth/EspUUID.kt, packet/ESPPacket.kt, packet/ESPPacketUtils.kt,
packet/data/alert/* and packet/data/displayData/*, on 2026-10-17. The code is
this project's own, in Python; the numbers are the protocol's. The MIT notice
is in ATTRIBUTION.md. The framing was cross-checked, never copied, against
tz1/bluev's v1state.py (GPL-3, read only): SOF 0xAA, destination 0xD0|id,
originator 0xE0|id, a payload length that counts the checksum, EOF 0xAB.

FRAMING (one whole ESP packet per BLE notification or write on the V1connection
LE service, as KESPL's LeConnection treats it; Framer below also tolerates a
packet split across notifications):

    AA | D0+dest | E0+orig | packet id | payload length | payload... | [checksum] | AB

On a bus where the V1 reports itself as 0xEA ("checksum V1") every packet
carries one checksum byte as the last byte of the payload, counted in the
payload length: the 8-bit sum of every byte from SOF to the last payload byte.
A V1 that reports 0xE9 does not use it.

WHAT IS AMBIGUOUS, so not guessed (see doc/radar.md "What is unverified"):
 - KESPL's AlertBand maps Ku to 0x16 and X to 0x08 in one file and 0x16 in the
   unused constants of another; its own bit documentation puts Ku on bit 4
   (0x10). This module reads bits 0..4 as Laser, Ka, K, X, Ku and says so.
 - The alert table's front and rear signal bytes are raw levels. KESPL gives no
   table turning them into the V1's eight bars. bars_from_raw() below is an
   even split, marked as an approximation; the PRIORITY alert's bars come from
   the display data's own bar graph, which is exact.
 - Whether a V1 Gen2 itself speaks this over BLE with no V1connection: KESPL
   only scans for the V1connection LE service.
"""

# --- the LE service (KESPL bluetooth/EspUUID.kt) --------------------------------------
SERVICE = "92a0aff4-9e05-11e2-aa59-f23c91aec05e"
WRITE = "92a0b6d4-9e05-11e2-aa59-f23c91aec05e"     # client out, V1 in
NOTIFY = "92a0b2ce-9e05-11e2-aa59-f23c91aec05e"    # V1 out, client in

SOF, EOF = 0xAA, 0xAB
MAX_PAYLOAD = 48        # the longest ESP payload read here is 17; anything bigger is not a packet
DEST_BASE, ORIG_BASE = 0xD0, 0xE0

# --- device ids (ESPDevice.kt) ----------------------------------------------------------
V1CONNECTION = 0x06          # what OmaCar sends as: the V1connection's own id
BROADCAST = 0x08
V1_NO_CHECKSUM = 0x09
V1_CHECKSUM = 0x0A

# --- packet ids (ESPPacketId.kt): only the ones used here -------------------------------
REQ_VERSION, RESP_VERSION = 0x01, 0x02
INF_DISPLAY_DATA = 0x31
REQ_MUTE_ON, REQ_MUTE_OFF = 0x34, 0x35
REQ_START_ALERT_DATA, REQ_STOP_ALERT_DATA, RESP_ALERT_DATA = 0x41, 0x42, 0x43
RESP_UNSUPPORTED, RESP_NOT_PROCESSED, INF_V1_BUSY, RESP_DATA_ERROR = 0x64, 0x65, 0x66, 0x67

# Everything OmaCar is allowed to send. A request with any other id is refused
# at the door of build_request(), so no settings write can be added by accident.
SENDABLE = {REQ_VERSION, REQ_MUTE_ON, REQ_MUTE_OFF, REQ_START_ALERT_DATA, REQ_STOP_ALERT_DATA}

# --- bands and directions (alert/AlertBand.kt, BandArrow.kt, AlertArrow.kt) -------------
BAND_BITS = ((0x01, "Laser"), (0x02, "Ka"), (0x04, "K"), (0x08, "X"), (0x10, "Ku"))
BAND_MASK = 0x1F
ARROW_BITS = ((0x20, "front"), (0x40, "side"), (0x80, "rear"))
PHOTO = {1: "MRCT", 2: "DriveSafe 1", 3: "DriveSafe 2", 4: "Redflex Halo",
         5: "Redflex NK7", 6: "Ekin"}          # alert/PhotoRadar.kt, V4.1037 and later
JUNK_BIT, PRIORITY_BIT = 0x40, 0x80             # alert aux byte (alert/Aux0.kt)

# --- the seven segments (Constants.kt), for the bogey counter ---------------------------
SEVEN_SEG = {0x3F: "0", 0x06: "1", 0x5B: "2", 0x4F: "3", 0x66: "4", 0x6D: "5", 0x7D: "6",
             0x07: "7", 0x7F: "8", 0x6F: "9", 0x77: "A", 0x7C: "b", 0x39: "C", 0x5E: "d",
             0x79: "E", 0x71: "F", 0x49: "#", 0x18: "l", 0x38: "L", 0x1E: "J", 0x58: "c",
             0x3E: "U", 0x1C: "u"}
MODES = {1: "All bogeys", 2: "Logic", 3: "Advanced Logic"}   # displayData/V1Mode.kt
MODE_FROM_COUNTER = {"A": 1, "C": 1, "U": 1, "l": 2, "u": 2, "c": 2, "L": 3}

# Version 4.1018 is the first with the Gen2 platform bits in the display data
# (V1CapabilityInfo.kt lists isGen2 under it). The constant itself was not read.
GEN2_FROM = 4.1018


# ---- framing ------------------------------------------------------------------------------

def checksum(frame):
    """8-bit sum of every byte given: pass SOF..last payload byte."""
    return sum(frame) & 0xFF


def build_packet(dest, orig, pid, payload=b"", with_checksum=True):
    """A whole frame. dest and orig are device ids (0..15), not yet OR-ed with 0xD0/0xE0."""
    body = bytes(payload)
    head = bytes([SOF, DEST_BASE | dest, ORIG_BASE | orig, pid,
                  len(body) + (1 if with_checksum else 0)]) + body
    if with_checksum:
        head += bytes([checksum(head)])
    return head + bytes([EOF])


def build_request(v1_id, pid, payload=b""):
    """OmaCar -> the V1, as the V1connection (KESPL's ESPRequest.toWritablePayload).

    Refuses anything but the five requests this app uses.
    """
    if pid not in SENDABLE:
        raise ValueError(f"packet 0x{pid:02X} is not something OmaCar sends")
    if payload:
        raise ValueError("these requests carry no payload")
    return build_packet(v1_id, V1CONNECTION, pid, payload, with_checksum=(v1_id == V1_CHECKSUM))


class Packet:
    __slots__ = ("dest", "orig", "pid", "payload", "checked")

    def __init__(self, dest, orig, pid, payload, checked):
        self.dest, self.orig, self.pid, self.payload, self.checked = dest, orig, pid, payload, checked

    def __repr__(self):
        return f"Packet(dest={self.dest}, orig={self.orig}, pid=0x{self.pid:02X}, payload={self.payload.hex()})"

    @property
    def from_v1(self):
        return self.orig in (V1_NO_CHECKSUM, V1_CHECKSUM)

    @property
    def for_me(self):
        """Addressed to the V1connection or to everyone (KESPL ByteArray.isForMe)."""
        return self.dest in (V1CONNECTION, BROADCAST)


class Framer:
    """Bytes in, whole valid packets out. Keeps no more than one partial packet.

    `bad` counts frames dropped for a wrong EOF or checksum, so a link that is
    talking garbage is visible rather than silent.
    """

    def __init__(self):
        self.buf = bytearray()
        self.checksum_bus = False
        self.bad = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(SOF)
            if i < 0:
                self.buf.clear()
                break
            if i:
                del self.buf[:i]
            # Judge the header as soon as it is there: a stray 0xAA must not
            # make us wait for the length byte of something that is not a packet.
            if len(self.buf) >= 3 and not (self.buf[1] & 0xF0 == DEST_BASE and self.buf[2] & 0xE0 == ORIG_BASE):
                self.bad += 1
                del self.buf[:1]
                continue
            if len(self.buf) < 5:
                break
            pl = self.buf[4]
            if pl > MAX_PAYLOAD:
                self.bad += 1
                del self.buf[:1]
                continue
            total = 6 + pl
            if len(self.buf) < total:
                break
            frame = bytes(self.buf[:total])
            ok = (frame[-1] == EOF and frame[1] & 0xF0 == DEST_BASE and frame[2] & 0xE0 == ORIG_BASE)
            payload = frame[5:5 + pl]
            checked = False
            if ok:
                orig = frame[2] & 0x0F
                if orig == V1_CHECKSUM:
                    self.checksum_bus = True
                elif orig == V1_NO_CHECKSUM:
                    self.checksum_bus = False
                if self.checksum_bus:
                    # A bus in checksum mode: the last payload byte is the sum.
                    if pl == 0 or checksum(frame[:-2]) != frame[-2]:
                        ok = False
                    else:
                        payload, checked = payload[:-1], True
            if ok:
                out.append(Packet(frame[1] & 0x0F, frame[2] & 0x0F, frame[3], bytes(payload), checked))
                del self.buf[:total]
            else:
                self.bad += 1
                del self.buf[:1]          # resync on the next 0xAA
        if len(self.buf) > 64:            # no ESP packet here is anywhere near this long
            self.buf.clear()
        return out


# ---- decoding -------------------------------------------------------------------------------

def band_of(b):
    """Band name from the band/arrow byte: exactly one of bits 0..4 set, else None."""
    low = b & BAND_MASK
    for bit, name in BAND_BITS:
        if low == bit:
            return name
    return None


def arrow_of(b):
    """front / side / rear when exactly one arrow bit is set (KESPL's rule), else None."""
    hi = b & 0xE0
    for bit, name in ARROW_BITS:
        if hi == bit:
            return name
    return None


def bars_from_raw(raw):
    """An APPROXIMATION of the V1's eight bars from a raw level 0..255.

    KESPL exposes the level and no table; the V1's own bars are not an even
    split. Use bar_count() on the display data wherever the priority alert is
    concerned.
    """
    if raw <= 0:
        return 0
    return min(8, 1 + (int(raw) - 1) * 8 // 256)


def bar_count(byte):
    """Lit bars in the display data's bar graph byte (b0 is the left-most)."""
    return bin(byte & 0xFF).count("1")


def decode_alert(payload):
    """One respAlertData payload (7 bytes) -> a dict, or {"empty": True} for the empty table.

    Layout, from KESPL AlertData.kt: [index<<4 | count][freq MHz hi][freq lo]
    [front level][rear level][band+arrow bits][aux: bit7 priority, bit6 junk,
    low nibble the photo-radar type]. None for a payload too short to be one.
    """
    if not payload:
        return None
    if payload[0] == 0:
        return {"empty": True}
    if len(payload) < 7:
        return None
    index, count = payload[0] >> 4, payload[0] & 0x0F
    if not 1 <= index <= count:
        return None
    ba, aux = payload[5], payload[6]
    band = band_of(ba)
    photo = PHOTO.get(aux & 0x0F) if band == "K" else None
    if photo:
        band = "Photo"
    freq = (payload[1] << 8) | payload[2]
    return {
        "index": index, "count": count, "band": band, "dir": arrow_of(ba), "photo": photo,
        # A laser alert has no carrier frequency; whatever the field holds then is not one.
        "freq_mhz": freq if band in ("Ka", "K", "X", "Ku") and freq else None,
        "front_raw": payload[3], "rear_raw": payload[4],
        "priority": bool(aux & PRIORITY_BIT), "junk": bool(aux & JUNK_BIT),
    }


class AlertTable:
    """Assembles "k of N" packets into a complete table; only a complete one comes out.

    feed() returns None while a table is partial, [] for the empty table, and
    the list of alerts (junk ones dropped, as the V1 says to) once all N have
    arrived. A partial table older than `hold` seconds is thrown away, and a
    packet that belongs to a table of a different size starts a new one.
    """

    def __init__(self, hold=1.5):
        self.hold = hold
        self.rows = {}
        self.count = 0
        self.since = 0.0

    def feed(self, alert, now):
        if alert is None:
            return None
        if alert.get("empty"):
            self.rows, self.count = {}, 0
            return []
        if self.rows and (alert["count"] != self.count or now - self.since > self.hold):
            self.rows = {}
        if not self.rows:
            self.since = now
        self.count = alert["count"]
        self.rows[alert["index"]] = alert
        if len(self.rows) < self.count:
            return None
        table = [self.rows[i] for i in range(1, self.count + 1) if i in self.rows]
        self.rows, self.count = {}, 0
        return [a for a in table if not a["junk"]]


def decode_display(payload):
    """infDisplayData payload (8 bytes) -> what the V1 is showing on its face.

    Layout, from KESPL DisplayData.kt: bogey counter image 1 and 2, signal bar
    graph, band/arrow image 1 and 2, aux0, aux1, aux2. Image 2 is the other
    half of a blink, so "lit" is image 1 OR image 2.
    """
    if len(payload) < 8:
        return None
    b1, b2, bars, ba1, ba2, aux0, aux1, aux2 = payload[:8]
    lit = ba1 | ba2
    glyph = SEVEN_SEG.get(b1 & 0x7F)
    mode = (aux1 >> 2) & 0x03 or MODE_FROM_COUNTER.get(glyph, 0)
    return {
        "counter": glyph, "dp": bool(b1 & 0x80),
        "bars": bar_count(bars),
        "bands": [n for bit, n in BAND_BITS[:4] if lit & bit],
        "arrows": [n for bit, n in ARROW_BITS if lit & bit],
        "mute_led": bool(lit & 0x10),
        "soft": bool(aux0 & 0x01),             # audio muted
        "time_slicing": not (aux0 & 0x02),     # accessories may talk (KESPL: !tsHoldOff)
        "searching": bool(aux0 & 0x04),
        "display_on": bool(aux0 & 0x08),
        "euro": bool(aux0 & 0x10),
        "legacy": bool(aux0 & 0x40),
        "mode": MODES.get(mode),
        "auto_muted": bool(aux1 & 0x10),
    }


def decode_version(payload):
    """respVersion payload -> "V4.1037" or None (seven ASCII characters)."""
    s = bytes(payload[:7]).decode("ascii", "replace")
    return s if len(s) == 7 and s[0] in "Vv" else None


def is_gen2(version):
    try:
        return float(version[1:]) >= GEN2_FROM
    except (TypeError, ValueError):
        return None


# ---- encoding what a V1 would say (the simulator and the tests) ----------------------------

def alert_payload(index, count, band="Ka", freq_mhz=34700, front=0, rear=0, arrow="front",
                  priority=False, junk=False, photo=0):
    """The 7 bytes of one respAlertData row: the inverse of decode_alert()."""
    bit = {n: b for b, n in BAND_BITS}[band if band != "Photo" else "K"]
    arr = {n: b for b, n in ARROW_BITS}[arrow] if arrow else 0
    aux = (PRIORITY_BIT if priority else 0) | (JUNK_BIT if junk else 0) | (photo & 0x0F)
    f = int(freq_mhz or 0)
    return bytes([(index << 4) | count, f >> 8, f & 0xFF, front & 0xFF, rear & 0xFF, bit | arr, aux])


def display_payload(digit="0", bars=0, bands=(), arrows=(), soft=False, mode=2, time_slicing=True,
                    mute_led=False, auto_muted=False, blink=False):
    """The 8 bytes of one infDisplayData: the inverse of decode_display()."""
    seg = {v: k for k, v in SEVEN_SEG.items()}
    ba = 0
    for bit, n in BAND_BITS[:4]:
        if n in bands:
            ba |= bit
    for bit, n in ARROW_BITS:
        if n in arrows:
            ba |= bit
    if mute_led:
        ba |= 0x10
    bar_byte = (1 << min(8, bars)) - 1
    aux0 = 0x08 | 0x04 | (0x01 if soft else 0) | (0 if time_slicing else 0x02)
    aux1 = ((mode & 3) << 2) | (0x10 if auto_muted else 0)
    return bytes([seg.get(digit, 0), seg.get(digit, 0) if not blink else 0, bar_byte,
                  ba, ba, aux0, aux1, 0x00])
