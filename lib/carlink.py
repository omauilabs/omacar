"""The CarPlay dongle, and the stream the phone screen reads.

WHY THIS IS IN PYTHON AND NOT IN THE BROWSER.

The obvious design is WebUSB: the page opens the dongle itself, and
share/js/omaplay/source.js was written expecting exactly that. It would work,
and it costs two things.

WebUSB needs a user gesture and a device-picker dialog for every origin that
has not already been granted the device. On a tablet that boots into a kiosk in
a car, that is a permissions dialog between the driver and their music. And it
puts the driver in the browser, where this project has no bundler and would
have to vendor a compiled dependency to get one -- against the whole point of
an app served as plain ES modules off disk.

The server already owns the serial port for the same reasons. It owns this too.

WHAT GOES DOWN THE WIRE, and why it is framed rather than raw.

One HTTP response, read as a stream, carrying self-delimiting records:

    1 byte   kind    1 = an H.264 access unit, 2 = a JSON event
    4 bytes  length  big-endian
    n bytes  payload

Length-prefixed because H.264 is binary and contains every byte value, so
nothing can be used as a delimiter. Self-delimiting because a chunked HTTP body
splits wherever the network feels like: a reader that assumed one record per
chunk would work perfectly on a desk and tear frames in a car.

The browser decodes with WebCodecs, which hands H.264 to the same hardware
decoder the browser uses for video. On a Surface that is the difference between
a warm tablet and a hot one.

TWO SESSIONS, AND THE FAKE ONE IS NOT A TOY.

`replay` plays a canned H.264 file through the identical path -- same framing,
same route, same decoder. It exists because the plumbing and the protocol are
separate risks, and separating them means the day the dongle arrives the only
untested thing is the dongle. It is also what lets a test assert that a picture
actually reaches a canvas, on a machine with no hardware attached.

It says it is a replay in its own opening event. A fake that does not announce
itself is the failure this project spends its time refusing.
"""

import json
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import records                                                # noqa: E402

REC_VIDEO = 1
REC_EVENT = 2

# A record's length field is four bytes, so this is the ceiling by construction.
# It is also far larger than any access unit a dongle emits, and a length wildly
# above it means the stream has desynchronised rather than that a huge frame
# arrived.
MAX_RECORD = 8 << 20

# The models node-carplay recognises. lib/phone.py reports on the same list and
# a guard holds the two together.
KNOWN_DEVICES = ((0x1314, 0x1520), (0x1314, 0x1521))


def frame(kind, payload):
    """One self-delimiting record."""
    if len(payload) > MAX_RECORD:
        raise ValueError(f"record of {len(payload)} bytes is beyond the "
                         f"{MAX_RECORD} ceiling")
    return bytes([kind]) + struct.pack(">I", len(payload)) + payload


def event(obj):
    return frame(REC_EVENT, json.dumps(obj).encode("utf-8"))


VCL = (1, 5)                          # a coded slice: the picture itself


def nal_starts(data):
    """Every Annex-B start code, as (where it begins, what follows it).

    Three- and four-byte start codes both count. An encoder emits either, and a
    scan that knew only the four-byte form once read a clip as having no key
    frame in it at all.
    """
    nals = []
    i, n = 0, len(data)
    while i + 3 < n:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                nals.append((i, i + 3))
                i += 3
                continue
            if data[i + 2] == 0 and data[i + 3] == 1:
                nals.append((i, i + 4))
                i += 4
                continue
        i += 1
    return nals


def split_annexb(data):
    """An Annex-B buffer, cut into one access unit per picture.

    A file is one long stream and a decoder wants exactly one picture per
    chunk. AnnexBAssembler below is this function with a memory, for the live
    case where the bytes arrive a handful at a time.

    THE BOUNDARY IS THE SECOND SLICE, NOT THE PARAMETER SET. Cutting at each
    SPS looks right and is not: an encoder repeats parameter sets only at key
    frames, so a three-second clip came out as three units of twenty pictures
    each -- which a decoder either rejects or renders as one frame in twenty.
    An access unit ends where the next video slice begins, so the rule is to
    accumulate NALs and close the unit when a second slice would join it. The
    parameter sets and SEI that precede a slice stay with it, which is what
    lets the decoder configure itself from the first unit it is given.
    """
    nals = nal_starts(data)
    n = len(data)
    if not nals:
        return [data] if data else []

    units, cur, seen_slice = [], b"", False
    for idx, (begin, after) in enumerate(nals):
        kind = data[after] & 0x1F if after < n else 0
        end = nals[idx + 1][0] if idx + 1 < len(nals) else n
        chunk = data[begin:end]
        if kind in VCL and seen_slice:
            units.append(cur)
            cur, seen_slice = b"", False
        cur += chunk
        if kind in VCL:
            seen_slice = True
    if cur:
        units.append(cur)
    return units


def _json_payload(payload):
    """JSON off the wire, whatever it was terminated with.

    One message in this protocol strips a trailing terminator and another does
    not, so neither is trusted: strip and parse, and a payload that is not JSON
    comes back as the text it was rather than as an exception in a read loop.
    """
    text = payload.decode("utf-8", "replace").strip("\0 \t\r\n")
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


class AnnexBAssembler:
    """A stream of H.264 bytes in, whole pictures out.

    The file path can see the whole clip at once; a live one cannot. This is
    split_annexb with a memory: it holds back the picture it is still
    collecting, because the last access unit in the buffer is always the one
    that may still grow, and handing it over early is how a decoder gets half
    a frame.
    """

    # A picture that never closes means the stream has lost its place. Emitting
    # rather than growing without bound turns that into a visible glitch rather
    # than a machine slowly eating its memory.
    LIMIT = 4 << 20

    def __init__(self):
        self.buf = b""

    def feed(self, chunk):
        if chunk:
            self.buf += bytes(chunk)
        return self._cut(final=False)

    def flush(self):
        return self._cut(final=True)

    def _cut(self, final):
        data = self.buf
        nals = nal_starts(data)
        if not nals:
            if final or len(data) > self.LIMIT:
                self.buf = b""
                return [data] if data else []
            return []
        opens, seen = [0], False
        for idx, (_begin, after) in enumerate(nals):
            kind = data[after] & 0x1F if after < len(data) else 0
            if kind in VCL:
                if seen:
                    opens.append(idx)
                seen = True
        cuts = [nals[o][0] for o in opens]
        units = [data[cuts[k]:cuts[k + 1]] for k in range(len(cuts) - 1)]
        tail = data[cuts[-1]:]
        if final or len(tail) > self.LIMIT:
            if tail:
                units.append(tail)
            tail = b""
        self.buf = tail
        return units


class Session:
    """A source of records, with subscribers. One at a time, by design.

    The dongle is a single physical device and the phone screen is a single
    surface, so a second session would be two things fighting over one cable.
    """

    def __init__(self, note=""):
        self.note = note
        self.started = time.time()
        # THE OPENING STATEMENT, KEPT. A session says what it is once, at the
        # start, and a reader that connected a tenth of a second later never
        # heard it -- which for a replay means a recording playing with nothing
        # on screen saying so. The claim is sticky so that arriving late cannot
        # silently turn a stand-in into a phone.
        self._hello = None
        self._subs = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    # -- subscribers -----------------------------------------------------------
    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=240)
        with self._lock:
            if self._hello is not None:
                q.put_nowait(self._hello)
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, rec):
        import queue
        with self._lock:
            subs = list(self._subs)
        return self._fan(rec, subs)

    def hello(self, obj):
        """An event every subscriber gets, whenever it arrives."""
        rec = event(obj)
        with self._lock:
            self._hello = rec
            subs = list(self._subs)
        self._fan(rec, subs)

    def _fan(self, rec, subs):
        import queue
        for q in subs:
            try:
                q.put_nowait(rec)
            except queue.Full:
                # A READER THAT CANNOT KEEP UP LOSES FRAMES, NOT THE SESSION.
                # Blocking here would stall the USB read loop behind a browser
                # tab that went to sleep, and a stalled read loop is a dongle
                # that has to be replugged. Dropping is the right failure for
                # video: the next key frame recovers it.
                try:
                    q.get_nowait()
                    q.put_nowait(rec)
                except Exception:                             # noqa: BLE001
                    pass

    # -- lifecycle -------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_guarded, daemon=True,
                                        name="carlink")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        self._publish(event({"type": "unplugged"}))

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def _run_guarded(self):
        try:
            self._run()
        except Exception as why:                              # noqa: BLE001
            self.error = f"{type(why).__name__}: {why}"
            self._publish(event({"type": "failure", "error": self.error}))

    def _run(self):
        raise NotImplementedError

    def send(self, msg):
        """Input from the screen, going back to the phone."""
        return False


class ReplaySession(Session):
    """A canned H.264 file, through the identical path. Says so."""

    def __init__(self, path, fps=20.0, loop=True, note="replay"):
        super().__init__(note=note)
        self.path = path
        self.fps = max(1.0, float(fps))
        self.loop = loop

    def _run(self):
        with open(self.path, "rb") as f:
            data = f.read()
        units = split_annexb(data)
        if not units:
            raise ValueError(f"{self.path} holds no H.264 access units")
        self.hello({
            "type": "plugged",
            "replay": True,
            "note": f"REPLAY — {os.path.basename(self.path)}, "
                    f"{len(units)} access units. This is a recording, "
                    f"not a phone.",
        })
        period = 1.0 / self.fps
        while not self._stop.is_set():
            for u in units:
                if self._stop.is_set():
                    return
                self._publish(frame(REC_VIDEO, u))
                time.sleep(period)
            if not self.loop:
                return


# ------------------------------------------------------------------ the dongle
#
# THE WIRE FORMAT, and where it came from.
#
# There is no specification for this adapter. What exists is node-carplay,
# which was written by somebody watching the traffic, and this is a reading of
# version 4.1.0 rather than a translation of it: several things that library
# does are wrong in ways that only show up on a bad day, and they are called
# out where they are not copied.
#
# Every message is a 16-byte header and then a payload:
#
#     4 bytes  magic      0x55AA55AA, LITTLE-ENDIAN, so AA 55 AA 55 on the wire
#     4 bytes  length     payload bytes that follow
#     4 bytes  type       see the MSG_ constants
#     4 bytes  typeCheck  ~type, which is the only integrity check there is
#
# Every integer in the protocol is little-endian. There is no checksum over the
# payload and no terminator; length is the whole story.

MAGIC = 0x55AA55AA
MAGIC_BYTES = struct.pack("<I", MAGIC)
USB_HEADER = 16

# The dongle is told to cap its packets at 48 KiB, so a payload wildly beyond
# that is a stream that has lost its place rather than a large frame. Finding
# the next magic is then cheaper and safer than trusting the length.
MAX_USB_PAYLOAD = 1 << 20

MSG_OPEN = 1
MSG_PLUGGED = 2
MSG_PHASE = 3
MSG_UNPLUGGED = 4
MSG_TOUCH = 5
MSG_VIDEO = 6
MSG_AUDIO = 7
MSG_COMMAND = 8
MSG_BLUETOOTH_ADDRESS = 10
MSG_BLUETOOTH_PIN = 12
MSG_BLUETOOTH_NAME = 13
MSG_WIFI_NAME = 14
MSG_MANUFACTURER = 20
MSG_BOX_SETTINGS = 25
MSG_MEDIA = 42
MSG_SEND_FILE = 153
MSG_HEARTBEAT = 170
MSG_SOFTWARE_VERSION = 204

MSG_NAMES = {
    MSG_OPEN: "open", MSG_PLUGGED: "plugged", MSG_PHASE: "phase",
    MSG_UNPLUGGED: "unplugged", MSG_VIDEO: "video", MSG_AUDIO: "audio",
    MSG_COMMAND: "command", MSG_BLUETOOTH_ADDRESS: "bluetooth address",
    MSG_BLUETOOTH_PIN: "bluetooth pin", MSG_BLUETOOTH_NAME: "bluetooth name",
    MSG_WIFI_NAME: "wifi name", MSG_MANUFACTURER: "manufacturer",
    MSG_BOX_SETTINGS: "box settings", MSG_MEDIA: "media",
    MSG_SOFTWARE_VERSION: "software version",
}

# The command word is one uint32. The names are node-carplay's; the numbers are
# the protocol's.
COMMANDS = {
    "invalid": 0, "startRecordAudio": 1, "stopRecordAudio": 2,
    "requestHostUI": 3, "siri": 5, "mic": 7, "frame": 12, "boxMic": 15,
    "enableNightMode": 16, "disableNightMode": 17, "audioTransferOn": 22,
    "audioTransferOff": 23, "wifi24g": 24, "wifi5g": 25,
    "left": 100, "right": 101, "selectDown": 104, "selectUp": 105,
    "back": 106, "down": 114, "home": 200, "play": 201, "pause": 202,
    "next": 204, "prev": 205,
    "requestVideoFocus": 500, "releaseVideoFocus": 501,
    "wifiEnable": 1000, "autoConnetEnable": 1001, "wifiConnect": 1002,
    "scanningDevice": 1003, "deviceFound": 1004, "deviceNotFound": 1005,
    "connectDeviceFailed": 1006, "btConnected": 1007, "btDisconnected": 1008,
    "wifiConnected": 1009, "wifiDisconnected": 1010, "btPairStart": 1011,
    "wifiPair": 1012,
}
COMMAND_NAMES = {v: k for k, v in COMMANDS.items()}

# A touch is three states and a pair of coordinates. THE COORDINATES ARE
# FRACTIONS OF THE SCREEN, not pixels: the wire wants 0..10000, and feeding it
# pixels pins every touch to the bottom-right corner, which is the kind of bug
# that looks like a broken touchscreen.
TOUCH_ACTIONS = {"down": 14, "move": 15, "up": 16}

PHONE_TYPES = {1: "Android mirror", 3: "CarPlay", 4: "iPhone mirror",
               5: "Android Auto", 6: "HiCar"}

# Sound is not in the picture path and is not decoded here. The commands that
# come alongside it are, because "Siri started" is a thing the screen shows.
AUDIO_COMMANDS = {
    1: "output start", 2: "output stop", 3: "input config",
    4: "phone call start", 5: "phone call stop", 6: "navigation start",
    7: "navigation stop", 8: "siri start", 9: "siri stop",
    10: "media start", 11: "media stop", 12: "alert start", 13: "alert stop",
}

# Settings the adapter keeps as files on its own filesystem, written by sending
# it a file rather than a setting.
FILE_DPI = "/tmp/screen_dpi"
FILE_NIGHT_MODE = "/tmp/night_mode"
FILE_HAND_DRIVE = "/tmp/hand_drive_mode"
FILE_CHARGE_MODE = "/tmp/charge_mode"
FILE_BOX_NAME = "/etc/box_name"


def usb_header(msg_type, length):
    """The 16 bytes in front of everything."""
    return struct.pack("<IIII", MAGIC, length, msg_type,
                       (~msg_type) & 0xFFFFFFFF)


def usb_message(msg_type, payload=b""):
    return usb_header(msg_type, len(payload)) + payload


def file_payload(name, content):
    """A write to a path on the adapter's own filesystem.

    The name length COUNTS THE TERMINATOR, which is the sort of detail that
    costs an afternoon when it is off by one.
    """
    nm = name.encode("ascii") + b"\0"
    return (struct.pack("<I", len(nm)) + nm
            + struct.pack("<I", len(content)) + content)


def file_number(name, value):
    return file_payload(name, struct.pack("<I", int(value)))


class UsbStream:
    """Bytes off the endpoint, whole messages out.

    WHY THIS EXISTS RATHER THAN TWO READS PER MESSAGE. node-carplay reads
    exactly sixteen bytes, then exactly the length it just read. That assumes
    every USB read returns precisely what was asked for, which libusb does not
    promise: a short read there raises, a failed payload read silently ends the
    read loop, and a sixteen-byte request against a device that packed a header
    and its payload into one packet is an overflow error.

    Accumulating instead makes all three impossible, and gives somewhere to
    put the fourth problem: a stream that has lost its place can be found again
    by looking for the magic, rather than by shutting the adapter down.
    """

    def __init__(self):
        self.buf = b""
        self.desyncs = 0

    def feed(self, chunk):
        if chunk:
            self.buf += bytes(chunk)
        out, i, buf, n = [], 0, self.buf, len(self.buf)
        while True:
            if n - i < USB_HEADER:
                break
            if buf[i:i + 4] != MAGIC_BYTES:
                i = self._resync(buf, i)
                if i is None:
                    i = max(0, n - 3)
                    break
                continue
            length, mtype, check = struct.unpack("<III", buf[i + 4:i + 16])
            if check != ((~mtype) & 0xFFFFFFFF) or length > MAX_USB_PAYLOAD:
                nxt = self._resync(buf, i)
                if nxt is None:
                    i = max(0, n - 3)
                    break
                i = nxt
                continue
            if n - i - USB_HEADER < length:
                break
            out.append((mtype, buf[i + USB_HEADER:i + USB_HEADER + length]))
            i += USB_HEADER + length
        self.buf = buf[i:]
        return out

    def _resync(self, buf, i):
        j = buf.find(MAGIC_BYTES, i + 1)
        if j < 0:
            return None
        self.desyncs += 1
        return j


class DongleSession(Session):
    """A Carlinkit adapter, opened and read.

    WRITTEN FROM A READING OF THE PROTOCOL, NOT FROM A WORKING DONGLE. Every
    offset here is sourced; none of it has met hardware. What has been proven
    is everything on either side of it -- the framing, the route, the decoder
    and the screen -- through ReplaySession, which travels the identical path.
    So the honest statement is that the untested part is exactly one file, and
    it says so on the screen until a real adapter proves otherwise.
    """

    # The adapter's own defaults. They are magic numbers with no derivation
    # anywhere, so they are copied rather than reasoned about.
    FORMAT = 5
    IBOX_VERSION = 2
    PHONE_WORK_MODE = 2
    PACKET_MAX = 49152

    def __init__(self, width=800, height=640, fps=20, dpi=160, night=False,
                 hand=0, box_name="OmaCar", wifi="5ghz", mic="os",
                 audio_transfer=False, note="dongle"):
        super().__init__(note=note)
        self.width, self.height, self.fps = int(width), int(height), int(fps)
        self.dpi, self.night, self.hand = int(dpi), bool(night), int(hand)
        self.box_name = (box_name or "OmaCar")[:16]
        self.wifi, self.mic = wifi, mic
        self.audio_transfer = bool(audio_transfer)

        self._dev = None
        self._in = None
        self._out = None
        self._intf = None
        self._wire = threading.Lock()
        self._ticker_thread = None
        self._annexb = AnnexBAssembler()

        self.frames = 0
        self.units = 0
        self.last_frame_at = 0.0
        self.geometry = None
        self.phone = None
        self.heard = False
        self.frame_interval = None

    # -- opening ---------------------------------------------------------------
    def _pyusb(self):
        try:
            import usb.core
            import usb.util
        except ImportError:
            raise RuntimeError(
                "pyusb is not installed in this interpreter. The daemon's "
                "virtual environment has it: ~/.local/share/omacar/venv.")
        return usb.core, usb.util

    def _find(self, core):
        for vid, pid in KNOWN_DEVICES:
            dev = core.find(idVendor=vid, idProduct=pid)
            if dev is not None:
                return dev
        return None

    def _open(self):
        core, util = self._pyusb()
        dev = self._find(core)
        if dev is None:
            raise RuntimeError("no Carlinkit adapter is plugged in. "
                               "`omacar phone` says what it can see.")

        # THE RESET, AND WHY IT IS FOLLOWED BY A WAIT AND A SECOND SEARCH.
        # The adapter comes up in a state that will not stream until it has
        # been port-reset, and the reset makes it vanish from the bus for one
        # to three seconds and come back as a different device number. The
        # handle from before the reset is dead. This is the single thing most
        # likely to be got wrong, because skipping it works right up until it
        # does not.
        try:
            dev.reset()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            util.dispose_resources(dev)
        except Exception:                                     # noqa: BLE001
            pass
        self._stop.wait(3.0)
        dev, deadline = None, time.time() + 10.0
        while time.time() < deadline and not self._stop.is_set():
            dev = self._find(core)
            if dev is not None:
                break
            self._stop.wait(0.25)
        if dev is None:
            raise RuntimeError("the adapter did not come back after its reset")

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:                                     # noqa: BLE001
            # Not every kernel claims it, and not every backend can say.
            pass
        dev.set_configuration(1)
        intf = dev.get_active_configuration()[(0, 0)]

        # DISCOVERED, NOT HARDCODED. The endpoint addresses are nowhere in any
        # source; taking them from the descriptors is one line and removes a
        # whole class of first-morning failure.
        def direction(want):
            return util.find_descriptor(
                intf, custom_match=lambda e:
                util.endpoint_direction(e.bEndpointAddress) == want)

        self._in = direction(util.ENDPOINT_IN)
        self._out = direction(util.ENDPOINT_OUT)
        if self._in is None or self._out is None:
            raise RuntimeError("the adapter has no bulk endpoints in one or "
                               "both directions, which is not a dongle we know")
        util.claim_interface(dev, intf.bInterfaceNumber)
        self._dev, self._intf = dev, intf
        return dev

    def _close(self):
        dev, intf = self._dev, self._intf
        self._dev = self._in = self._out = self._intf = None
        if dev is None:
            return
        try:
            import usb.util
            if intf is not None:
                usb.util.release_interface(dev, intf.bInterfaceNumber)
            usb.util.dispose_resources(dev)
        except Exception:                                     # noqa: BLE001
            pass

    # -- writing ---------------------------------------------------------------
    def _write(self, msg_type, payload=b""):
        out = self._out
        if out is None:
            return False
        data = usb_message(msg_type, payload)
        with self._wire:
            try:
                out.write(data, 3000)
                return True
            except Exception as why:                          # noqa: BLE001
                # SAID OUT LOUD, because this runs on the heartbeat thread as
                # well as the read thread, and an exception raised there goes
                # nowhere. A session that dies silently looks exactly like an
                # adapter that is fine and simply has nothing to show.
                self.error = f"{type(why).__name__}: {why}"
                self._publish(event({"type": "failure", "error": self.error}))
                self._stop.set()
                return False

    def _command(self, name):
        return self._write(MSG_COMMAND, struct.pack("<I", COMMANDS[name]))

    def _handshake(self):
        """The opening sequence, in order and one at a time.

        node-carplay fires these eleven concurrently and relies on libusb
        queueing them in submission order. That happens to hold and there is no
        reason to depend on it.
        """
        cfg = struct.pack("<IIIIIII", self.width, self.height, self.fps,
                          self.FORMAT, self.PACKET_MAX, self.IBOX_VERSION,
                          self.PHONE_WORK_MODE)
        settings = json.dumps({
            "mediaDelay": 300,
            # SECONDS, despite the name of the function this was read from.
            # Milliseconds here puts media sync out by a factor of a thousand.
            "syncTime": int(round(time.time())),
            "androidAutoSizeW": self.width,
            "androidAutoSizeH": self.height,
        }, separators=(",", ":")).encode("ascii")

        for msg_type, payload in (
            (MSG_SEND_FILE, file_number(FILE_DPI, self.dpi)),
            (MSG_OPEN, cfg),
            (MSG_SEND_FILE, file_number(FILE_NIGHT_MODE, 1 if self.night else 0)),
            (MSG_SEND_FILE, file_number(FILE_HAND_DRIVE, self.hand)),
            (MSG_SEND_FILE, file_number(FILE_CHARGE_MODE, 1)),
            (MSG_SEND_FILE, file_payload(FILE_BOX_NAME,
                                         self.box_name.encode("ascii"))),
            (MSG_BOX_SETTINGS, settings),
            (MSG_COMMAND, struct.pack("<I", COMMANDS["wifiEnable"])),
            (MSG_COMMAND, struct.pack("<I", COMMANDS[
                "wifi5g" if self.wifi == "5ghz" else "wifi24g"])),
            (MSG_COMMAND, struct.pack("<I", COMMANDS[
                "boxMic" if self.mic == "box" else "mic"])),
            (MSG_COMMAND, struct.pack("<I", COMMANDS[
                "audioTransferOn" if self.audio_transfer
                else "audioTransferOff"])),
        ):
            if self._stop.is_set() or not self._write(msg_type, payload):
                return False
        return True

    def _ticker(self):
        """Heartbeat, the pairing prod, and the keyframe request.

        The adapter stops streaming if it stops hearing from us, so this runs
        for as long as the session does.
        """
        began = time.time()
        last_beat = last_frame = 0.0
        connected = paired = False
        while not self._stop.is_set():
            now = time.time()
            if now - last_beat >= 2.0:
                last_beat = now
                self._write(MSG_HEARTBEAT)
            if not connected and now - began >= 1.0:
                connected = True
                self._command("wifiConnect")
            if not paired and not self.heard and now - began >= 15.0:
                paired = True
                self._command("wifiPair")
            if self.frame_interval and now - last_frame >= self.frame_interval:
                last_frame = now
                self._command("frame")
            self._stop.wait(0.2)

    # -- reading ---------------------------------------------------------------
    def _run(self):
        self._open()
        self.hello({
            "type": "opening",
            "note": f"Carlinkit adapter open at {self.width}x{self.height}, "
                    f"{self.fps} fps. Waiting for a phone.",
        })
        if not self._handshake():
            raise RuntimeError(self.error or "the adapter would not take the "
                                             "opening sequence")
        self._ticker_thread = threading.Thread(target=self._ticker, daemon=True,
                                               name="carlink-ticker")
        self._ticker_thread.start()

        stream = UsbStream()
        # A read large enough for the biggest payload the adapter was told it
        # may send, and a multiple of the bulk packet size so a full transfer
        # never ends mid-packet.
        size = self.PACKET_MAX + (1 << 16)
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._in.read(size, 1000)
                except Exception as why:                      # noqa: BLE001
                    name = type(why).__name__
                    if "Timeout" in name:
                        continue
                    if getattr(why, "errno", None) in (19, 5):   # ENODEV, EIO
                        self._publish(event({
                            "type": "unplugged",
                            "note": "the adapter left the bus"}))
                        return
                    raise
                for msg_type, payload in stream.feed(chunk):
                    self._handle(msg_type, bytes(payload))
        finally:
            self._close()

    def _handle(self, msg_type, payload):
        self.heard = True
        if msg_type == MSG_VIDEO:
            self._video(payload)
            return
        if msg_type == MSG_PLUGGED:
            kind = struct.unpack("<I", payload[0:4])[0] if len(payload) >= 4 else 0
            self.phone = PHONE_TYPES.get(kind, f"type {kind}")
            # Only CarPlay wants the periodic keyframe prod; Android Auto is
            # explicitly given none.
            self.frame_interval = 5.0 if kind == 3 else None
            self._publish(event({
                "type": "plugged", "phone": self.phone,
                "wifi": bool(struct.unpack("<I", payload[4:8])[0])
                        if len(payload) >= 8 else None,
            }))
            return
        if msg_type == MSG_UNPLUGGED:
            self.phone = None
            self.frame_interval = None
            self._annexb = AnnexBAssembler()
            self._publish(event({"type": "unplugged"}))
            return
        if msg_type == MSG_MEDIA:
            self._media(payload)
            return
        if msg_type == MSG_AUDIO:
            if len(payload) - 12 == 1:
                code = struct.unpack("<b", payload[12:13])[0]
                self._publish(event({
                    "type": "audio", "command": code,
                    "what": AUDIO_COMMANDS.get(code, f"audio command {code}")}))
            return                       # sound itself is not carried yet
        if msg_type == MSG_COMMAND and len(payload) >= 4:
            value = struct.unpack("<I", payload[0:4])[0]
            self._publish(event({"type": "command", "value": value,
                                 "name": COMMAND_NAMES.get(value)}))
            return
        if msg_type == MSG_BOX_SETTINGS:
            self._publish(event({"type": "box",
                                 "settings": _json_payload(payload)}))
            return
        if msg_type == MSG_SOFTWARE_VERSION:
            self._publish(event({
                "type": "version",
                "version": payload.decode("ascii", "replace").strip("\0 ")}))
            return
        if msg_type == MSG_PHASE and len(payload) >= 4:
            # The source this was read from carries a literal note that nobody
            # knows what the values mean, so neither do we.
            self._publish(event({
                "type": "phase",
                "phase": struct.unpack("<I", payload[0:4])[0]}))
            return
        if msg_type in (MSG_BLUETOOTH_ADDRESS, MSG_BLUETOOTH_PIN,
                        MSG_BLUETOOTH_NAME, MSG_WIFI_NAME):
            self._publish(event({
                "type": MSG_NAMES.get(msg_type, "text"),
                "text": payload.decode("ascii", "replace").strip("\0 ")}))
            return

    def _video(self, payload):
        """One video message: twenty bytes of geometry, then H.264."""
        if len(payload) < 20:
            return
        width, height, flags, declared, unknown = struct.unpack(
            "<IIIII", payload[0:20])
        bytes_here = payload[20:]
        if self.geometry != (width, height):
            self.geometry = (width, height)
            self._publish(event({
                "type": "geometry", "width": width, "height": height,
                # Three fields in this sub-header are parsed by every
                # implementation and read by none. They are reported once so
                # that the first real adapter says what they actually hold,
                # rather than being trusted now on a guess.
                "flags": flags, "declared": declared, "unknown": unknown,
                "carried": len(bytes_here),
            }))
        self.frames += 1
        self.last_frame_at = time.time()

        # WHOLE PICTURES, NOT WHATEVER THE ADAPTER SENT. A video message is
        # not promised to be one access unit, and a decoder handed half a
        # picture either drops it or renders a smear. Cutting the stream at
        # picture boundaries is the same rule the replay path uses, which is
        # why the replay path is worth anything.
        for unit in self._annexb.feed(bytes_here):
            self.units += 1
            self._publish(frame(REC_VIDEO, unit))

    def _media(self, payload):
        if len(payload) < 4:
            return
        kind = struct.unpack("<I", payload[0:4])[0]
        if kind == 1:
            self._publish(event({
                "type": "media",
                "message": {"payload": {"type": 1,
                                        "media": _json_payload(payload[4:])}},
            }))

    # -- input -----------------------------------------------------------------
    def send(self, msg):
        """A touch or a button, going back to the phone."""
        if not isinstance(msg, dict) or not self.running():
            return False
        what = str(msg.get("type") or "").lower()
        if what == "touch":
            action = TOUCH_ACTIONS.get(
                str(msg.get("action") or msg.get("action_type") or "").lower())
            if action is None:
                return False
            # Fractions in, 0..10000 out, truncated rather than rounded so the
            # value matches every other implementation exactly.
            def scaled(v):
                try:
                    return int(max(0.0, min(10000.0, 10000.0 * float(v))))
                except (TypeError, ValueError):
                    return 0
            return self._write(MSG_TOUCH, struct.pack(
                "<IIII", action, scaled(msg.get("x")), scaled(msg.get("y")), 0))
        if what == "command":
            name = str(msg.get("name") or "")
            if name not in COMMANDS:
                return False
            return self._command(name)
        return False

    def stop(self):
        self._stop.set()
        super().stop()


# --------------------------------------------------------------- the registry
_CURRENT = {"session": None}
_REG_LOCK = threading.Lock()


def current():
    s = _CURRENT["session"]
    return s if (s and s.running()) else None


def start_replay(path=None, fps=20.0, loop=True):
    path = path or os.path.join(records.STATE, "phone-replay.h264")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"no replay stream at {path}. Make one with:\n"
            f"  ffmpeg -f lavfi -i testsrc=size=800x640:rate=20:duration=5 "
            f"-c:v libx264 -profile:v baseline -pix_fmt yuv420p -f h264 {path}")
    with _REG_LOCK:
        stop()
        s = ReplaySession(path, fps=fps, loop=loop).start()
        _CURRENT["session"] = s
        return s


def start_dongle(**opts):
    """Open the adapter for real. One at a time, like the replay."""
    with _REG_LOCK:
        stop()
        s = DongleSession(**opts).start()
        _CURRENT["session"] = s
        return s


def stop():
    s = _CURRENT["session"]
    if s:
        try:
            s.stop()
        except Exception:                                     # noqa: BLE001
            pass
    _CURRENT["session"] = None


def dongles_present():
    """The known adapters plugged in, without needing pyusb."""
    import phone
    return phone.dongles()
