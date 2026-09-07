"""A minimal, strictly read-only ELM327 client.

python-obd is the right tool for known PIDs. Prospecting is different: it
needs to set arbitrary headers, send raw service requests, and — crucially —
*see* negative responses. `7F 22 31` (requestOutOfRange) means "that PID does
not exist on this ECU", which is a completely different fact from silence,
which means "wrong header". python-obd collapses both to null.

Read-only is enforced here, at the bottom of the stack, rather than trusted
to callers.
"""
import sys
import time

import serial

# Services this client will ever emit. Everything else — 0x2E write, 0x2F
# input/output control, 0x31 routine control, 0x11 ECU reset, 0x14 clear
# diagnostic information, 0x85 control DTC setting — can change vehicle state
# or erase data, and has no place in a discovery tool.
# 0x19 (ReadDTCInformation) reads only: every subfunction reports stored fault
# data and none of them clear it. Clearing is 0x14, which is deliberately absent
# above. 0x34/0x36/0x37 -- RequestDownload, TransferData, RequestTransferExit --
# are the firmware-flashing trio and are absent for a stronger reason: they
# write program memory, so a transfer that is interrupted or mismatched leaves
# the module without valid firmware. There is nothing to read there anyway.
READ_ONLY_SERVICES = {0x01, 0x02, 0x03, 0x06, 0x07, 0x09, 0x19, 0x21, 0x22}

NRC = {
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


class WriteAttempted(Exception):
    """Raised when something asks for a service that could change state."""


class Elm:
    def __init__(self, port, baudrate=38400, timeout=0.05):
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self.header = None
        self.protocol = None
        time.sleep(0.2)

    # -- transport ---------------------------------------------------------

    def raw(self, line, wait=0.0, patient=False, timeout=5.0):
        """Send an adapter command -- AT or ST -- and return the reply lines.

        THIS IS NOT A WAY TO REACH THE CAR, AND IT USED TO BE.

        The service allowlist, the write arm and the absolute refusal of the
        reprogramming trio all live in request(). raw() went straight to the
        wire, so any caller that wrote hex here bypassed every one of them.
        Six call sites did, and one of them -- candlog, reading candidates out
        of a downloaded community profile -- was sending bytes this tool had
        never seen to an ECU, on the strength of a file off the internet.

        The gate is here rather than in a comment because a comment is not a
        gate. request() gained `patient` and `timeout` at the same time, since
        the reason those callers reached past it was that it could not do a
        multi-frame read, which is a bad reason to have a hole.
        """
        head = (line or "").strip()[:2].upper()
        if head not in ("AT", "ST"):
            raise WriteAttempted(
                f"raw() sends adapter commands (AT/ST), not vehicle requests. "
                f"{line!r} carries a service byte and must go through "
                f"request(), which is where the allowlist and the write arm "
                f"are enforced.")
        return self._send(line, wait=wait, patient=patient, timeout=timeout)

    def _send(self, line, wait=0.0, patient=False, timeout=5.0):
        """Send one line, read to the ELM prompt, return the reply lines.

        `patient` waits for the ">" prompt instead of returning at the first
        gap in output. The gap-break is right for ordinary requests -- it is
        what keeps a sweep fast -- and wrong for anything where the adapter
        thinks between writing: an auto-searching ELM prints "SEARCHING..."
        and then goes quiet for seconds before the real reply, so the
        impatient read returns the search notice and never sees the answer.

        THE SERIAL TIMEOUT IS THE SWEEP'S SPEED LIMIT, NOT ATST.
        ---------------------------------------------------------
        This loop always performs one more read() after the reply has landed,
        and that read blocks for the whole pyserial timeout before the
        gap-break fires. With the old default of 1.0s every request cost a
        flat ~1.003s whether the DID answered or not -- measured -- which is
        why a 768-request sweep took thirteen minutes. ATST changes nothing
        here; it governs how long the ELM waits for the CAR, and the ELM was
        already answering promptly with NO DATA.

        The constructor default is now 50ms. That is safe because the loop's
        real terminator is the ">" prompt, which the ELM sends at the end of
        every reply: a short timeout only decides how quickly we notice
        silence, never whether a reply is seen. The 5s deadline below still
        bounds anything genuinely slow.
        """
        self.ser.reset_input_buffer()
        self.ser.write((line + "\r").encode())
        self.ser.flush()
        buf, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
            elif buf and not patient:
                break
        if wait:
            time.sleep(wait)
        text = buf.decode("ascii", "replace").replace("\r", "\n")
        return [ln.strip() for ln in text.split("\n") if ln.strip() and ln.strip() != ">"]

    def monitor(self, command="ATMA", seconds=8.0, on_line=None, limit=200000,
                should_stop=None):
        """Listen to the bus. Transmit nothing.

        THE ONLY OPERATION IN THIS TOOL THAT PUTS NOTHING ON THE WIRE.

        Every other capability here is a question: it sends a request and reads
        an answer, which is safe but is not nothing. An ELM327 in monitor mode
        is silent -- it does not even acknowledge the frames it reports -- so
        this is strictly less intrusive than a read, and it is the one thing
        that can see data the car never answers questions about.

        That matters more than it sounds. A hybrid's state of charge, the drive
        mode a dashboard switch selects, the gear a transmission is in: these
        feed the instrument cluster over periodic broadcast frames and are
        frequently exposed by NO diagnostic identifier at all. Sweeping 8,192
        of them on this project's own car found exactly nothing, and the data
        was on the wire the whole time.

        WHY THIS CANNOT USE _send(). Every other command here terminates at the
        ELM's ">" prompt. A monitor has no prompt: it streams until something is
        sent to stop it, so the read loop is bounded by a deadline and the stop
        is an explicit write of a single byte. Sending that byte is not a
        vehicle request -- it goes to the adapter, to end its own mode -- and
        the AT guard below is the same one raw() applies, kept because this
        method writes to the port directly and would otherwise be the hole
        raw() was closed to prevent.

        Returns the number of lines seen. Lines go to `on_line` as they arrive
        rather than being accumulated here, because a busy bus produces
        thousands a second and the caller decides what is worth keeping.

        `should_stop` is checked every read. A capture driven by a person -- put
        the switch here, name it; move it, name that -- has no duration anybody
        can state in advance, and a deadline is the wrong shape for it: too
        short and the procedure cannot be run at all, too long and finishing
        means waiting out a timer. So the deadline stays as the backstop and the
        person is the terminator.
        """
        head = (command or "").strip()[:2].upper()
        if head != "AT":
            raise WriteAttempted(
                f"monitor() runs an adapter monitor command (AT...), not a "
                f"vehicle request. {command!r} carries a service byte; a "
                f"request goes through request(), where the allowlist and the "
                f"write arm are enforced.")
        self.ser.reset_input_buffer()
        self.ser.write((command.strip() + "\r").encode())
        self.ser.flush()
        seen, buf, deadline = 0, b"", time.time() + float(seconds)
        try:
            while time.time() < deadline and seen < limit:
                if should_stop is not None and should_stop():
                    break
                chunk = self.ser.read(512)
                if not chunk:
                    continue
                buf += chunk
                # Split on either terminator: adapters differ, and a monitor is
                # the one place the stream is long enough for it to matter.
                buf = buf.replace(b"\r", b"\n")
                *lines, buf = buf.split(b"\n")
                for raw_line in lines:
                    text = raw_line.decode("ascii", "replace").strip()
                    if not text or text == ">":
                        continue
                    seen += 1
                    if on_line is not None:
                        on_line(text)
        finally:
            # STOP IT, WHATEVER HAPPENED. A monitor left running owns the
            # adapter forever and every later command reads its frames instead
            # of an answer. One byte ends it; then drain to the prompt so the
            # next caller starts clean.
            try:
                self.ser.write(b"\r")
                self.ser.flush()
                end = time.time() + 2.0
                while time.time() < end:
                    chunk = self.ser.read(512)
                    if not chunk:
                        break
                    if b">" in chunk:
                        break
            except Exception:                                 # noqa: BLE001
                pass
        return seen

    def at(self, cmd):
        return self.raw("AT" + cmd)

    def init(self, protocol=None):
        """Bring the adapter up on the protocol THIS car actually speaks.

        WAS protocol="6", HARDCODED. Protocol 6 is ISO 15765-4 CAN 11-bit,
        which is most cars and is not all of them: a 2015 CR-Z answers on
        protocol 7, CAN 29-bit. Forced onto 6 the adapter cannot reach the car
        at all, and every request returns NO DATA -- which reads exactly like a
        car that has nothing to say rather than a tool asking on the wrong
        wire. `omacar prospect` reported "0 responders" against a perfectly
        healthy vehicle for precisely this reason.

        Default is now auto-search (ATSP0). The ELM cannot complete a search
        without a real request to search WITH, so the 0100 below is not merely
        a warm-up -- it is the thing that resolves the protocol. Afterwards
        ATDPN reports what it settled on, which is recorded so callers can see
        it and so a caller that already knows can skip the search by passing it.
        """
        self.at("Z")
        time.sleep(0.5)
        self.at("E0")     # no echo
        self.at("L0")     # no linefeeds
        self.at("S0")     # no spaces
        self.at("H1")     # headers on — we need to know who answered
        self.at("SP" + (protocol if protocol is not None else "0"))

        # THE FIRST REQUEST IS THE SEARCH; THE SECOND IS THE ANSWER.
        #
        # raw() returns as soon as output is followed by a gap, and an ELM
        # mid-auto-search emits "SEARCHING..." and then pauses -- so the first
        # 0100 comes back with the search in progress rather than a reply.
        # Asking again once the protocol has settled is the standard way round
        # this; three attempts covers a slow bus without hanging on a car that
        # simply is not answering.
        out = []
        for _ in range(3):
            # Patient, and with room for a slow search.
            out = self.request("0100", patient=True, timeout=12.0)
            joined = " ".join(out).upper()
            if out and "SEARCH" not in joined and "STOPPED" not in joined:
                break
            time.sleep(1.0)

        # Auto-search leaves DPN as the protocol it found; a forced one echoes
        # back what was asked for. Either way this is the truth afterwards.
        try:
            dpn = self.at("DPN")
            self.protocol = (dpn[-1] if isinstance(dpn, list) and dpn else str(dpn)).strip()
        except Exception:                                     # noqa: BLE001
            self.protocol = None
        return out

    def set_timeout(self, ms):
        """ATST -- how long the ELM waits for the car before saying NO DATA.

        The value is in units of 4ms, so ATST19 is 100ms. This is the single
        biggest lever on sweep time: a request that gets an answer costs what
        the answer costs, but a request to a DID that does not exist costs the
        WHOLE timeout, and on a sweep the misses are nearly all of the work.
        Measured here at the adapter default: 1.0s per request either way, so
        a 768-request sweep took thirteen minutes almost entirely spent waiting
        for silence.

        The trade is false negatives. Set it below what a real ECU needs to
        reply and a DID that exists gets recorded as silent -- which is worse
        than a slow sweep, because you cannot tell from the results that it
        happened. Hence `restore()` and the verify pass in prospect: sweep
        fast, then re-ask anything interesting at a patient timeout.
        """
        units = max(1, min(255, int(round(ms / 4.0))))
        self.at("ST%02X" % units)
        return units * 4

    def restore_timeout(self):
        """Back to the adapter's own default (ATST is reset by ATZ, but this
        is cheaper than a full re-init and does not disturb the protocol)."""
        self.at("ST00")          # 00 means "use the default"

    def payload(self, lines, request=None):
        """Reassemble a reply using THIS connection's protocol.

        Callers used to call the module-level reassemble() directly, which
        defaults to ISO-TP -- correct on CAN and quietly wrong everywhere else.
        There were eight such call sites, and every one of them would have had
        to remember. Asking the connection, which already knows what it
        negotiated, removes the chance to forget.
        """
        iso_tp, digits = True, None
        if self.protocol:
            try:
                import protocols
                p = protocols.describe(self.protocol)
                if p:
                    iso_tp = p["iso_tp"]
                    digits = p["header_digits"]
            except ImportError:
                pass
        return reassemble(lines, request=request, iso_tp=iso_tp,
                          header_digits=digits)

    def set_header(self, header):
        """Set the target address, refusing a shape this protocol cannot use.

        A header of the wrong width is accepted by the adapter and then simply
        never answered by the car -- indistinguishable from a module that is
        not there. Catching it here turns hours of "0 responders" into one
        clear sentence.

        THIS STILL RAISES, DELIBERATELY. A caller with a list of addresses to
        get through wants to skip the bad one and carry on, and aim() below is
        that caller's form. But the loops that RECORD what they asked --
        discover's module map, autodisc's frontier -- must not be able to skip
        an address by accident, and a return value they forgot to check would
        let them: the header would stay pointed at the previous module, the
        request would go out anyway, and one ECU's answer would be filed under
        ten addresses. An exception cannot be forgotten.
        """
        if header == self.header:
            return
        if self.protocol:
            try:
                import protocols
                ok, why = protocols.header_ok(self.protocol, header)
                if not ok:
                    raise ValueError("omacar: " + why)
            except ImportError:
                pass
        self.at("SH" + header)
        self.header = header

    # -- requests ----------------------------------------------------------

    def request(self, payload_hex, patient=False, timeout=None):
        """Send a raw service request.

        Reads always pass. Writes pass only while write mode is armed, and
        services that are not implemented at all never pass however armed the
        tool is -- the flashing trio is refused here even if something upstream
        decides it wants them.

        The import is lazy so that elm.py stays a transport module that can be
        imported and tested with nothing else present.
        """
        service = int(payload_hex[:2], 16)
        kw = {"patient": patient}
        if timeout is not None:
            kw["timeout"] = timeout
        if service in READ_ONLY_SERVICES:
            return self._send(payload_hex, **kw)

        try:
            import write as writelib
        except ImportError:
            writelib = None

        described = writelib.describe(service) if writelib else None
        if described is None:
            raise WriteAttempted(
                f"service 0x{service:02X} is not implemented by this tool and "
                f"cannot be sent. Reprogramming services (0x34/0x36/0x37) are "
                f"deliberately absent: they need a manufacturer-signed firmware "
                f"image, and a partial transfer leaves a module unable to boot.")
        if not writelib.is_armed():
            raise WriteAttempted(
                f"service 0x{service:02X} ({described[0]}) writes to the car and "
                f"write mode is not armed.\n"
                f"  arm it with:  omacar write arm")
        # THE LEDGER LINE IS WRITTEN BY THE GATE. Every write leaves through
        # this method, so every write leaves a line, and no caller can forget
        # to. The reply is classified here for the same reason: "sent" alone
        # would record the attempt and lose what the module said about it.
        lines = self._send(payload_hex, **kw)
        try:
            kind, detail, _data = classify(lines, service, payload_hex)
            writelib.sent(service, getattr(self, "header", ""), payload_hex,
                          kind, detail)
        except Exception:                                     # noqa: BLE001
            pass
        return lines

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# Headers already refused once, so a sweep of two hundred identifiers against
# a wrong-shaped address says so once rather than two hundred times.
_REFUSED = set()


def aim(el, header):
    """Point a connection at one address. True if it is now pointed there.

    This is set_header for a caller that has other addresses to try. It exists
    because the alternative -- widening header_ok until nothing is refused --
    would put us back where 0b1fc1f found us: the adapter accepts a
    wrong-width header, the car never answers, and the sweep reports the
    module as absent. Refusing is right; crashing the whole sweep over one
    address is not.

    False means WE DID NOT ASK, and it is not the same fact as silence.
    Callers must skip the address rather than send the request anyway: with
    the header unchanged the request would go to whatever module was
    addressed last, and its answer would be recorded against this one.

    A free function rather than a method so that anything holding a connection
    can use it -- ops and candlog are handed one, and the test doubles that
    stand in for an ECU implement set_header and nothing else.
    """
    try:
        el.set_header(header)
        return True
    except ValueError as why:
        key = (getattr(el, "protocol", None), header)
        if key not in _REFUSED:
            _REFUSED.add(key)
            # set_header prefixes its own message; this line already names the
            # tool by being on its stderr, so drop the second "omacar:".
            said = str(why)
            said = said[8:] if said.startswith("omacar: ") else said
            print(f"  skipping {header}: {said}", file=sys.stderr, flush=True)
        return False


def classify(lines, service, request=None):
    """(kind, detail, data_hex) for a reply.

    kind is 'positive', 'negative', 'silent' or 'error'.

    `request` is the hex we sent, and skipping it is not optional.
    ---------------------------------------------------------------
    The adapter echoes the request back as the first line even with ATE0, and
    the positive-response marker for a service is that service + 0x40 -- 0x62
    for UDS 0x22. So the request "220062" CONTAINS "62", and searching the
    echo for the marker reports a positive response from a DID that actually
    answered 7F 22 31 (requestOutOfRange). Every DID ending in the marker byte
    false-positived on every ECU; a sweep of 0000-00FF duly "found" 220062 on
    all three modules of this car and nothing else, which is a scan tool
    inventing hardware.
    """
    if not lines:
        return "silent", "", ""
    if request:
        want = request.replace(" ", "").upper()
        lines = [ln for ln in lines if ln.replace(" ", "").upper() != want]
        if not lines:
            return "silent", "", ""
    joined = "".join(lines).upper()
    if "NODATA" in joined.replace(" ", ""):
        return "silent", "NO DATA", ""
    if any(w in joined for w in ("BUSINIT", "CANERROR", "BUFFERFULL",
                                 "UNABLETOCONNECT", "STOPPED", "ERROR")):
        return "error", joined[:40], ""

    positive = f"{service + 0x40:02X}"

    # A refusal anywhere in the reply settles it, so look for one across every
    # line before considering any line positive. Interleaved per-line checks
    # let a positive match on an earlier line pre-empt an explicit 7F on a
    # later one.
    for ln in lines:
        body = ln.replace(" ", "").upper()
        i = body.find("7F")
        if i >= 0 and len(body) >= i + 6 and body[i + 2:i + 4] == f"{service:02X}":
            code = int(body[i + 4:i + 6], 16)
            return "negative", NRC.get(code, f"NRC 0x{code:02X}"), ""

    for ln in lines:
        body = ln.replace(" ", "").upper()
        j = body.find(positive)
        if j >= 0:
            return "positive", ln, body[j:]
    return "silent", lines[0][:40], ""


def reassemble(lines, request=None, iso_tp=True, header_digits=None):
    """ELM327 lines -> one continuous payload hex string.

    `iso_tp` MUST be False on anything that is not CAN. ISO-TP's
    first-frame/consecutive-frame numbering is a CAN construct; on J1850 or
    ISO 9141 a long reply arrives as repeated lines each carrying the full
    header, and the leading data byte of each would be read as a PCI byte.
    That does not fail loudly -- it produces a plausible-looking payload with
    bytes missing and invented, which is worse than an error.

    WHY classify() IS NOT ENOUGH.

    classify() returns the first line carrying the positive-response marker,
    which is correct for a reply that fits in one CAN frame and silently wrong
    for anything longer. Service 0x19 subfunction 0x0A on this car returns 199
    bytes across 29 frames; read one frame at a time it looks like a positive
    response containing a single truncated record, so the DTC list came back
    empty while every subfunction reported "supported". A parser that finds
    nothing is easy to mistake for a car that has nothing.

    ISO 15765-2 framing, after the header is stripped:
        0L              single frame, L data bytes
        1LLL            first frame, 12-bit total length, then 6 data bytes
        2S              consecutive frame, sequence S (wraps 0-F), 7 data bytes
    Trailing 0x55 padding in the final frame is cut by the declared length.
    """
    if not iso_tp:
        # Flat concatenation: strip each line's header and join. There are no
        # sequence numbers to honour and none to misread.
        flat = ""
        for ln in lines:
            body = ln.replace(" ", "").upper()
            if not body or (request and body == request.replace(" ", "").upper()):
                continue
            if not all(c in "0123456789ABCDEF" for c in body):
                continue
            n = header_digits if header_digits else 6
            flat += body[n:] if len(body) > n else ""
        return flat

    out, total, seen_ff = "", None, False
    for ln in lines:
        body = ln.replace(" ", "").upper()
        if not body or (request and body == request.replace(" ", "").upper()):
            continue
        if not all(c in "0123456789ABCDEF" for c in body):
            continue
        # Strip the reply header: 8 hex chars for 29-bit (18DAF1xx), 3 for the
        # 11-bit 7Ex replies. Anything else is left alone and probably is not
        # a frame at all.
        if len(body) > 8 and body.startswith("18DA"):
            body = body[8:]
        elif len(body) > 3 and body[0] == "7" and len(body) % 2 == 1:
            body = body[3:]
        if len(body) < 2:
            continue
        pci = body[0]
        if pci == "0":
            n = int(body[1], 16)
            return body[2:2 + n * 2]
        if pci == "1":
            total = int(body[1:4], 16)
            out += body[4:]
            seen_ff = True
        elif pci == "2" and seen_ff:
            out += body[2:]
        elif not seen_ff:
            # No PCI we recognise: a reply the adapter printed without framing.
            out += body
    if total is not None:
        out = out[:total * 2]
    return out
