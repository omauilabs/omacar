"""Service 0x19 -- ReadDTCInformation.

WHY THIS IS NOT PART OF `prospect`.

prospect sweeps a range of DIDs: thousands of requests differing only in a
two-byte identifier. 0x19 has no DID. It takes a subfunction -- about twenty of
them, each with its own argument shape and its own reply format -- so the useful
probe is a couple of dozen hand-written requests, not a range.

That difference is also why 4096 empty DIDs on 0x22 said nothing about this
service. They are different questions to the ECU.

READ ONLY, AND THE OMISSION IS THE POINT.

Every subfunction here reports. None clear. Clearing DTCs is service 0x14 and
it is not in this file, not in READ_ONLY_SERVICES, and not reachable from the
transport -- a diagnostic that quietly erases the evidence of an intermittent
fault is worse than no diagnostic.

THE PER-CODE RECORDS ARE OPT-IN, AND THAT IS ABOUT BUS TIME, NOT SAFETY.

Two of the subfunctions -- 0x04, the freeze frames, and 0x06, the extended
data -- cannot be asked blind. Each takes one specific DTC as its argument, so
it is one request per code, and this car's catalogue is 241 codes long on a
single module. Asked for every code on every module that is thousands of
requests and many minutes at ignition II, which is exactly how a read-only
tool ends up flattening a battery. So they sit behind --snapshots, bounded by
--limit, and are pointed at the codes the module actually reported before the
codes it merely knows the names of.

They are worth asking for. A swept identifier that moves is a number with no
provenance; a snapshot record is the module's OWN quantities -- on a hybrid
controller typically pack voltage, current, temperature and state of charge --
frozen at the instant a named fault set, in Honda's own layout, with the fault
name attached to it. That is the difference between a reading and evidence.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import elm as elmlib  # noqa: E402

SERVICE = 0x19

# Below this, stop. 12.0V is a rested battery at roughly half charge; ABS and
# other chassis modules start reporting undervoltage well before the car fails
# to crank, so the warning light arrives long before a no-start.
LOW_VOLTS = 11.8

# How often the long per-code pass re-reads that voltage. ATRV is a question to
# the adapter and not to the car, so it costs no bus time -- but it does cost a
# round trip, and a 12V rail does not sag measurably in the couple of seconds
# one request takes. Fifteen seconds notices the drop long before a chassis
# module does.
FLOOR_EVERY = 15.0


def battery_volts(el):
    """Adapter-measured voltage at the OBD connector, via ELM `ATRV`."""
    try:
        for ln in el.raw("ATRV", timeout=2.0):
            t = ln.strip().upper().rstrip("V")
            if t and t[0].isdigit():
                return float(t)
    except (ValueError, OSError):
        pass
    return None

# (subfunction hex, name, what a positive reply means).
#
# Ordered cheapest-and-most-informative first. 0x0A is the discovery prize: it
# asks the module to enumerate every DTC it can ever set, which is a
# manufacturer-specific catalogue of what that ECU actually monitors -- exactly
# the Honda-internal detail a generic emissions scan never shows.
SUBFUNCTIONS = [
    ("01FF", "reportNumberOfDTCByStatusMask", "count of DTCs matching any status"),
    ("02FF", "reportDTCByStatusMask",         "every DTC with any status bit set"),
    ("0A",   "reportSupportedDTC",            "every DTC this ECU can ever set"),
    ("15",   "reportDTCWithPermanentStatus",  "emissions DTCs that resist clearing"),
    ("14",   "reportDTCFaultDetectionCounter", "live counters for maturing faults"),
    ("03",   "reportDTCSnapshotIdentification", "which freeze-frames are stored"),
    ("42FF", "reportWWHOBDDTCByMaskRecord",   "world-wide harmonised OBD view"),
]

# WHY 0x04 AND 0x06 ARE NOT IN THAT TABLE.
#
# The table is the blind sweep, and every request in it is complete on its own:
# it can be sent to a module nobody has ever spoken to, because it names no
# fault. Subfunctions 0x04 (reportDTCSnapshotRecordByDTCNumber) and 0x06
# (reportDTCExtendedDataRecordByDTCNumber) are not that shape. Each one takes a
# DTC as its argument -- three bytes naming the fault, then one byte naming
# which record of it is wanted -- so there is nothing to ask until a catalogue
# has come back and said which faults this module has names for.
#
# 0x04 was in the table anyway, as the literal request '0416'. That put
#
#     19 04 16
#
# on the bus: the service, the subfunction, and then a single byte standing
# where ISO 14229-1 defines four -- a three-byte DTCMaskRecord followed by a
# one-byte record number. 0x16 was being read by the module as the first byte
# of a DTC it was never given the rest of, and the record number was not there
# at all. A module has two lawful answers to a request that short: a negative
# response carrying NRC 0x13, incorrectMessageLength, or nothing. It was
# nothing, from both hybrid controllers -- and this file wrote that silence
# down as "the subfunction is not supported".
#
# That is the part that mattered. Silence in answer to a question that was
# never properly asked is not evidence about the module; it is evidence about
# the question. Two of the three modules on this car were recorded as not
# answering snapshots on the strength of it. The per-DTC path below asks the
# question in the shape the standard defines, so that whatever comes back --
# records, or a negative response, or silence -- is finally about the car.

RECORD_ALL = 0xFF

# The only two subfunctions that take a DTC, and an allowlist rather than a
# note: record_request() refuses to build anything outside this dict, so the
# per-code path cannot be talked into emitting another subfunction, and cannot
# emit another service at all -- it writes SERVICE itself into byte zero.
PER_DTC_SUBFUNCTIONS = {
    0x04: ("reportDTCSnapshotRecordByDTCNumber",
           "the module's own values, frozen when that fault set"),
    0x06: ("reportDTCExtendedDataRecordByDTCNumber",
           "the counters the module keeps beside that fault"),
}

# How many codes --snapshots asks about per module unless told otherwise.
# Five codes is ten requests per module, roughly a dozen seconds -- small
# enough that nobody has to think about the battery before typing it. The
# number is deliberately not "all of them": see the module docstring.
DEFAULT_RECORD_LIMIT = 5

STATUS_BITS = [
    (0x01, "failed now"),
    (0x02, "failed this cycle"),
    (0x04, "pending"),
    (0x08, "confirmed"),
    (0x10, "not run since clear"),
    (0x20, "failed since clear"),
    (0x40, "not run this cycle"),
    (0x80, "warning lamp"),
]


def decode_dtc(b0, b1, b2):
    """Three DTC bytes -> 'P0420-1C'.

    The top two bits pick the system letter and the next two the first digit;
    this is the ISO 14229 / SAE J2012 layout, the same one behind the codes a
    parts-shop reader prints. The third byte is the failure type, which is the
    part a generic reader throws away -- P0420-1C and P0420-64 are different
    faults and only the suffix distinguishes them.
    """
    letter = "PCBU"[(b0 >> 6) & 0x03]
    return "%s%X%X%02X-%02X" % (letter, (b0 >> 4) & 0x03, b0 & 0x0F, b1, b2)


def decode_status(byte):
    on = [name for bit, name in STATUS_BITS if byte & bit]
    return ", ".join(on) if on else "clean"


def parse_dtc_list(data, skip):
    """Positive-response payload -> [(code, status_byte)].

    `skip` is how many bytes follow the 0x59 echo before the records start:
    the subfunction, plus a status-availability mask for the list-returning
    subfunctions. Records are then four bytes each.
    """
    body = data[2 + skip * 2:]
    out = []
    for i in range(0, len(body) - 7, 8):
        try:
            b = [int(body[i + j:i + j + 2], 16) for j in range(0, 8, 2)]
        except ValueError:
            break
        if b[0] == 0 and b[1] == 0 and b[2] == 0:
            continue
        out.append((decode_dtc(b[0], b[1], b[2]), b[3]))
    return out


def encode_dtc(code):
    """'P0420-1C' -> (0x04, 0x20, 0x1C). The exact inverse of decode_dtc.

    A per-DTC request has to carry the three bytes the module knows the fault
    by, and what the rest of this tool carries around -- in profiles, in saved
    JSON, on the screen, in what the owner types -- is the printed form. The
    printed form is not lossy: decode_dtc spends one character on the system
    letter, one on the top two bits of the first byte and then plain hex, so
    the three bytes come back exactly. Doing that conversion here, once, is the
    difference between one inverse and three call sites each with its own
    off-by-one in the nibble packing.

    A bare six-digit hex string is also accepted and taken as the three bytes
    themselves, for a caller holding raw bytes out of a saved payload. There is
    no ambiguity between the two forms: the printed form is eight characters
    with a dash in the middle, so 'B12345' can only be the bytes B1 23 45 (whose
    printed form, for the record, is B3123-45).

    IT REFUSES A CODE WITH NO FAILURE-TYPE SUFFIX, and the refusal is the point
    of the function rather than a rough edge on it. 'P0420' is five characters
    describing two bytes. The third byte -- the failure type, the part that
    separates P0420-1C from P0420-64 -- is simply not in it. Defaulting the
    missing byte to 00 would send a perfectly well-formed request about a fault
    the module may never have had, and the empty answer would come back and be
    filed against the code the caller believes they asked about. Inventing the
    byte and then reporting on the answer is this tool telling somebody a thing
    it does not know.
    """
    text = str(code).strip().upper().replace(" ", "")
    hexdigits = "0123456789ABCDEF"
    if len(text) == 6 and all(c in hexdigits for c in text):
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    if len(text) != 8 or text[5] != "-":
        raise ValueError(
            "%r is not a DTC this can send. A per-DTC request needs all three "
            "bytes, which means the printed form with its failure type -- "
            "'P0420-1C', not 'P0420' -- or the six hex digits themselves."
            % (code,))
    system = "PCBU".find(text[0])
    if system < 0:
        raise ValueError("%r does not start with a system letter (P, C, B or U)."
                         % (code,))
    if any(c not in hexdigits for c in text[1:5] + text[6:8]):
        raise ValueError("%r has a non-hex digit in it." % (code,))
    first = int(text[1], 16)
    if first > 3:
        # Two bits carry this digit, so 0-3 is the whole range the wire has.
        # A '4' here means the string was not produced by decode_dtc and
        # guessing which byte it was meant to be is not this function's job.
        raise ValueError(
            "%r cannot be a DTC: the digit after the system letter is carried "
            "in two bits, so it is 0-3." % (code,))
    return ((system << 6) | (first << 4) | int(text[2], 16),
            int(text[3:5], 16),
            int(text[6:8], 16))


def record_request(sub, code, record=RECORD_ALL):
    """The hex for one per-DTC request: 19 <sub> <b0> <b1> <b2> <record>.

    Six bytes, in the order ISO 14229-1 defines for subfunctions 0x04 and
    0x06: the service, the subfunction, the three-byte DTCMaskRecord, and the
    one-byte record number. 0xFF in that last byte means "every record you
    have for this fault", which is what a first look wants; a specific record
    number asks for one of them.

    Building it here rather than at the call site is what makes the shape
    checkable without a car. All three things that were wrong before -- the
    total length, where the record number sits, and the subfunction being sent
    with no DTC at all -- are now decided in one function a test can call a
    thousand times on a desk.

    The service byte is written from SERVICE rather than passed in, and the
    subfunction is checked against PER_DTC_SUBFUNCTIONS, so this builder emits
    a read or it raises. It cannot be talked into 0x14 in either sense of that
    number: service 0x14 is clearDiagnosticInformation, which erases the
    evidence and is absent from this whole tool, and subfunction 0x14 is the
    fault-detection counter, which takes no DTC and would be malformed with
    one -- which is the bug this function exists to make impossible.
    """
    if isinstance(sub, str):
        sub = int(sub, 16)
    sub = int(sub)
    if sub not in PER_DTC_SUBFUNCTIONS:
        raise ValueError(
            "0x%02X is not a per-DTC subfunction. Only 0x04 (snapshots) and "
            "0x06 (extended data) take a DTC number, and only those two may be "
            "built here." % (sub,))
    record = int(record)
    if not 0 <= record <= 0xFF:
        raise ValueError("a record number is one byte; %d is not." % (record,))
    b0, b1, b2 = encode_dtc(code)
    return "%02X%02X%02X%02X%02X%02X" % (SERVICE, sub, b0, b1, b2, record)


def parse_record_reply(data, sub, asked=None):
    """A 0x04 or 0x06 positive response -> the part of it that can be justified.

    The reply opens with a fixed header the standard pins down exactly: the
    0x59 marker, the subfunction echoed back, the three-byte DTC the records
    belong to, and that DTC's status byte. Then come the records: for 0x04 a
    record number, a count of identifiers, and then DID/value pairs; for 0x06 a
    record number and then manufacturer-defined data.

    AND THAT IS WHERE THIS STOPS, DELIBERATELY.

    Neither record body carries the length of its own values. A snapshot record
    is DID, then as many bytes as that DID is defined to be -- a definition
    this tool does not have for a Honda hybrid controller. Without it there is
    no way to find where the first value ends and the second DID begins, so
    every value after the first would be split at a guessed offset. Guessed
    offsets produce numbers that look like readings, and a wrong pack voltage
    printed next to a real fault name is worse than no pack voltage at all.

    So: the header is decoded because it is fixed, the first record number is
    read because it is the byte right after the header, and everything past
    that is handed back as raw hex for a decoder that has the DID table. The
    saved JSON keeps the raw bytes precisely so that decoder can be written
    later without another trip to the car.

    Returns None when the reply is not the shape this claims to parse, and sets
    'mismatch' when the module echoed a different DTC than `asked` -- a record
    attributed to the wrong fault is a lie with a fault name on it.
    """
    body = str(data or "").upper().replace(" ", "")
    if len(body) < 12 or body[0:2] != "59":
        return None
    try:
        echoed_sub = int(body[2:4], 16)
        b = [int(body[i:i + 2], 16) for i in range(4, 12, 2)]
    except ValueError:
        return None
    if echoed_sub != int(sub):
        # The module answered about a different question. Reading its bytes
        # against this one's layout is how a parser invents a fault.
        return None
    out = {
        "code": decode_dtc(b[0], b[1], b[2]),
        "status": b[3],
        "status_text": decode_status(b[3]),
        "record": None,
        "identifiers": None,
        "records_raw": body[12:],
        "mismatch": False,
    }
    if asked is not None:
        try:
            out["mismatch"] = tuple(b[:3]) != encode_dtc(asked)
        except ValueError:
            out["mismatch"] = True
    rest = body[12:]
    if len(rest) >= 2:
        out["record"] = int(rest[0:2], 16)
    if int(sub) == 0x04 and len(rest) >= 4:
        out["identifiers"] = int(rest[2:4], 16)
    return out


def candidate_codes(rows, limit=DEFAULT_RECORD_LIMIT):
    """Which codes are worth one request each, best first.

    Rows are what probe() returned for one module. Three tiers, in this order:

      1. codes the module reported with a status bit set -- something actually
         happened to these, so they are the ones with records behind them;
      2. codes it listed with a clean status, which still means it named them
         in a list of ITS faults rather than of every fault it knows;
      3. the 0x0A catalogue, which is every code the ECU can ever set. This is
         the discovery prize and it is also 241 entries of mostly-never-set,
         so it goes last: asking a catalogue entry for a snapshot is a fair
         question with a very likely answer of "no such record".

    Deduplicated across tiers, so a code that is both stored and catalogued is
    asked about once, in its stored position.

    The limit is applied here rather than trusted to the caller because this is
    the function that knows the list is 241 long.
    """
    flagged, listed, catalogue, seen = [], [], [], set()
    for row in rows or []:
        if row.get("kind") != "positive":
            continue
        from_catalogue = str(row.get("sub", ""))[:2] == "0A"
        for code, status in row.get("dtcs") or []:
            if code in seen:
                continue
            seen.add(code)
            if from_catalogue:
                catalogue.append(code)
            elif status:
                flagged.append(code)
            else:
                listed.append(code)
    out = flagged + listed + catalogue
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


def per_dtc(el, header, codes, subs=(0x04, 0x06), record=RECORD_ALL,
            on_line=None, should_stop=None):
    """Ask each code about its own records. Returns rows shaped like probe()'s.

    One request per code per subfunction, and no more: the bound is the length
    of `codes`, which candidate_codes() has already cut down. Nothing here
    loops over a range or retries.

    `should_stop` is checked between codes and is how the caller stops early
    without this function needing to know why -- in practice a battery that has
    sagged during the pass, which is the failure this whole file worries about.
    """
    el.set_header(header)
    on_line = on_line or (lambda row: None)
    out = []
    for code in codes:
        if should_stop is not None and should_stop():
            break
        for sub in subs:
            sub = int(sub, 16) if isinstance(sub, str) else int(sub)
            # Built first, so an unsupported subfunction is refused by the
            # builder's sentence rather than by a KeyError on the line below.
            req = record_request(sub, code, record)
            name, meaning = PER_DTC_SUBFUNCTIONS[sub]
            # patient=True for the same reason 0x0A needs it: a snapshot with
            # a handful of identifiers in it is several frames, and the
            # impatient read would return the first one and call it the reply.
            lines = el.request(req, patient=True, timeout=6.0)
            kind, detail, _first = elmlib.classify(lines, SERVICE, request=req)
            data = el.payload(lines, request=req) if kind == "positive" else ""
            row = {"header": header, "sub": "%02X" % sub, "name": name,
                   "meaning": meaning, "kind": kind, "detail": detail,
                   "data": data, "dtcs": [], "code": code, "request": req,
                   "record_asked": record}
            if kind == "positive":
                row["record_data"] = parse_record_reply(data, sub, asked=code)
            out.append(row)
            on_line(row)
    return out


def probe(el, header, on_line):
    """Every blind subfunction against one ECU. Returns a list of result dicts.

    Blind: the ones that need no argument. The two that take a DTC are in
    per_dtc(), which runs after this and only when asked.
    """
    el.set_header(header)
    results = []
    for sub, name, meaning in SUBFUNCTIONS:
        req = "19" + sub
        # patient=True: a supported 0x0A on a real ECU returns a multi-frame
        # ISO-TP reply of hundreds of bytes. The impatient read returns at the
        # first gap in output and would truncate it to the first frame.
        lines = el.request(req, patient=True, timeout=6.0)
        kind, detail, _first = elmlib.classify(lines, SERVICE, request=req)
        # classify() decides supported-or-not from any single frame, which it
        # does correctly. The payload has to be rebuilt across frames.
        data = el.payload(lines, request=req) if kind == "positive" else ""
        row = {"header": header, "sub": sub, "name": name, "meaning": meaning,
               "kind": kind, "detail": detail, "data": data, "dtcs": []}
        if kind == "positive":
            skip = 2 if sub[:2] in ("01", "02", "0A", "15", "42") else 1
            if sub[:2] == "01":
                # A count, not a list: mask, format id, then a 16-bit total.
                try:
                    row["count"] = int(data[8:12], 16)
                except (ValueError, IndexError):
                    pass
            else:
                row["dtcs"] = parse_dtc_list(data, skip)
        results.append(row)
        on_line(row)
    return results


BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW = "\033[32m", "\033[33m"

# The same modules prospect sweeps: engine, then the two hybrid controllers
# that answered nothing across 4096 DIDs on 0x22.
# 0E is here because it answered service 0x22 identification DIDs in an earlier
# sweep and then never got asked about 0x19 -- an easy module to miss, since the
# 0x19 probe was written against the two ECUs we were already chasing.
DEFAULT_HEADERS = ["18DA10F1", "18DA03F1", "18DA04F1", "18DA0EF1"]


def headers_for(vin=None, slug=None):
    """The modules worth sweeping for faults on THIS car.

    Four Honda addresses are the right answer for the car this was written on
    and a waste of four round trips on anything else. The profile's module
    list is the real answer; these stay as the fallback so a car nobody has
    profiled still sweeps something sensible rather than nothing.

    Unlike discover.py's CANDIDATE_HEADERS -- which is a genuinely generic
    list of addresses manufacturers use, and stays hardcoded on purpose --
    this is a claim about which modules a PARTICULAR car actually has.
    """
    if not vin and not slug:
        try:
            import garage
            vin = garage.current() or ""
        except Exception:
            vin = ""
    try:
        import profile as profilelib
        if not slug and vin:
            slug = profilelib.for_vin(vin)
        if slug:
            doc, _p = profilelib.load(slug)
            # Refuted modules are skipped. `refuted` means somebody swept it
            # and it answered nothing -- keeping it in the sweep spends a round
            # trip per subfunction to re-learn a silence that is already
            # recorded. The entry stays in the profile precisely so it does not
            # have to be rediscovered; honouring it here is the payoff.
            live = [m for m in (doc.get("module") or [])
                    if m.get("header") and m.get("confidence") != "refuted"]
            if live:
                return [m["header"] for m in live]
            mods = profilelib.modules(doc, default=None)
            if mods:
                return [h for h, _label in mods]
    except Exception:
        pass
    return list(DEFAULT_HEADERS)


def main(argv):
    import argparse
    import atexit
    import time
    ap = argparse.ArgumentParser(prog="omacar dtc", add_help=True)
    ap.add_argument("--headers", default=",".join(headers_for()))
    ap.add_argument("--parked", action="store_true",
                    help="confirm the car is parked when road speed cannot be read")
    ap.add_argument("--save", action="store_true",
                    help="write the full result to ~/.local/state/omacar/")
    ap.add_argument("--snapshots", action="store_true",
                    help="after the sweep, ask each code found for its own "
                         "freeze frame (19 04) and extended data (19 06)")
    ap.add_argument("--limit", type=int, default=DEFAULT_RECORD_LIMIT,
                    help="how many codes per module --snapshots may ask about "
                         f"(default {DEFAULT_RECORD_LIMIT}; two requests each)")
    ap.add_argument("--record", default="FF",
                    help="which record to ask for, in hex; FF means all of "
                         "them (default FF)")
    ap.add_argument("--codes", default="",
                    help="ask --snapshots about these codes instead of the "
                         "ones the sweep finds, e.g. P0420-1C,P1456-68")
    args = ap.parse_args(argv)

    # BOTH OF THESE ARE CHECKED BEFORE THE PORT IS OPENED, not at the moment
    # they are first used. A typo in --record that surfaces halfway through the
    # sweep costs the sweep; the same typo caught here costs a retype.
    try:
        want_record = int(args.record, 16)
        if not 0 <= want_record <= 0xFF:
            raise ValueError
    except ValueError:
        sys.exit("omacar: --record takes one byte in hex, 00 to FF.")
    if args.limit < 0:
        sys.exit("omacar: --limit cannot be negative.")
    if args.snapshots and args.limit == 0 and not args.codes:
        sys.exit("omacar: --limit 0 asks about no codes at all. Drop "
                 "--snapshots, or give it a limit.")

    # The per-code pass is one request per code per subfunction, and this car
    # has 241 codes on one module. Somebody who has typed a large --limit is
    # entitled to it -- it is their car and their battery -- but not entitled
    # to be surprised by it, so the size of what they asked for is said out
    # loud before anything is sent rather than discovered from the clock.
    ask_codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    if args.snapshots and ask_codes:
        for c in ask_codes:
            try:
                encode_dtc(c)
            except ValueError as why:
                sys.exit("omacar: %s" % why)

    port, kind = connect.resolve()
    if not port:
        sys.exit("omacar: no adapter and no bench emulator.")
    warn = connect.serial_group_warning(port)
    if warn:
        sys.exit("omacar: " + warn)
    if not connect.request_port(port):
        sys.exit("omacar: the daemon is holding " + port + " and did not let go.\n"
                 "  stop it with: omacar daemon stop")
    atexit.register(connect.release_port)

    headers = [h.strip().upper() for h in args.headers.split(",") if h.strip()]
    el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
    print(f"\n  {BOLD}OmaCar fault reader{RESET}  {DIM}service 0x19 · {port} ({kind}){RESET}")
    el.init()

    # Same motion gate prospect uses. This service only reads, but it still
    # puts unfamiliar requests on a live bus.
    if kind != "bench":
        import prospect
        mv = prospect.moving(el)
        if mv:
            el.close()
            sys.exit("\n  refusing to probe: the car reports road speed. Park it.\n")
        if mv is None and not args.parked:
            el.close()
            sys.exit("\n  refusing to probe: road speed could not be read.\n"
                     "  If it is parked, say so:  omacar dtc --parked\n")

    print(f"  {DIM}read-only subfunctions; clearing (0x14) is not implemented{RESET}")

    # BATTERY VOLTAGE, CHECKED BEFORE AND DURING.
    #
    # Added after a long key-on-engine-off session ended with an ABS warning on
    # the dash. Read-only requests cannot set an ABS code, but half an hour at
    # ignition II with no alternator can sag the 12V rail far enough that the
    # ABS module complains -- and nothing here was watching for it. A probe that
    # can flatten the car's battery should be the thing that notices first.
    v = battery_volts(el)
    if v is not None:
        colour = GREEN if v >= 12.2 else YELLOW
        print(f"  battery {colour}{v:.1f} V{RESET}")
        if v < LOW_VOLTS:
            el.close()
            sys.exit(f"\n  refusing to probe: {v:.1f} V is too low for a key-on\n"
                     f"  session. Start the engine, or charge the battery.\n")

    if args.snapshots:
        each = len(ask_codes) if ask_codes else args.limit
        total = each * len(PER_DTC_SUBFUNCTIONS) * len(headers)
        print(f"  {DIM}per-code records: up to {each} code(s) x 2 subfunctions"
              f" x {len(headers)} module(s) = {total} extra request(s){RESET}")

    # THE VOLTAGE FLOOR, CHECKED DURING AND NOT ONLY BEFORE.
    #
    # The check above runs once, before anything is sent. That is the right
    # place for it and it is not enough for this pass. --snapshots is the
    # longest thing this file can be asked to do, and the failure the floor
    # exists for -- a key-on session with no alternator sagging the 12V rail
    # until a chassis module sets a code of its own -- takes many minutes to
    # arrive, which is to say it arrives in the middle of the pass and not
    # before it.
    #
    # Falling below the floor stops the per-code pass and lets the run finish
    # normally. It does not exit: the sweep's answers are already in `captured`,
    # --save has not run yet, and quitting here would throw away the evidence in
    # order to protect the battery that was collected at the battery's expense.
    floor_hit = None
    floor_checked = 0.0

    def sagging():
        nonlocal floor_hit, floor_checked
        if floor_hit is not None:
            return True
        now = time.time()
        if now - floor_checked < FLOOR_EVERY:
            return False
        floor_checked = now
        volts = battery_volts(el)
        if volts is not None and volts < LOW_VOLTS:
            floor_hit = volts
            print(f"    {YELLOW}stopping the per-code pass: {volts:.1f} V{RESET}")
            return True
        return False

    supported = 0
    answered = 0
    captured = []
    for header in headers:
        print(f"\n  {BOLD}{header}{RESET}")

        def show(row):
            nonlocal supported
            if row["kind"] == "positive":
                supported += 1
                mark = f"{GREEN}yes{RESET}"
            elif row["kind"] == "negative":
                mark = f"{YELLOW}{row['detail']}{RESET}"
            else:
                mark = f"{DIM}{row['detail'] or 'silent'}{RESET}"
            print(f"    19{row['sub']:<5} {row['name']:<33} {mark}")
            if "count" in row:
                print(f"      {DIM}-> {row['count']} stored{RESET}")
            for code, status in row["dtcs"]:
                print(f"      {BOLD}{code}{RESET}  {DIM}{decode_status(status)}{RESET}")

        def show_record(row):
            nonlocal answered
            if row["kind"] == "positive":
                answered += 1
                mark = f"{GREEN}yes{RESET}"
            elif row["kind"] == "negative":
                mark = f"{YELLOW}{row['detail']}{RESET}"
            else:
                mark = f"{DIM}{row['detail'] or 'silent'}{RESET}"
            print(f"    19 {row['sub']}  {row['code']:<10} "
                  f"{row['name']:<38} {mark}")
            got = row.get("record_data")
            if not got:
                return
            if got["mismatch"]:
                # The module answered about a different fault than the one it
                # was asked about. Printing its records under the asked-for
                # name would be this tool attaching real bytes to the wrong
                # code, which is the most convincing kind of wrong.
                print(f"      {YELLOW}answered about {got['code']}, not "
                      f"{row['code']} -- not attributed{RESET}")
                return
            head_line = f"      {DIM}{got['code']} · {got['status_text']}"
            if got["record"] is not None:
                head_line += f" · record {got['record']:02X}"
            if got["identifiers"] is not None:
                head_line += f" · {got['identifiers']} identifier(s)"
            print(head_line + RESET)
            if got["records_raw"]:
                # Raw, and only raw. Splitting these bytes into values needs
                # the module's DID lengths, which this tool does not have --
                # see parse_record_reply(). They are printed because they are
                # what the module said, and saved because a decoder written
                # later should not cost another trip to the car.
                shown = got["records_raw"]
                tail = "" if len(shown) <= 96 else " ..."
                print(f"      {DIM}{shown[:96]}{tail}{RESET}")

        rows = []
        try:
            rows = probe(el, header, show)
        except Exception as e:  # a module that stops answering must not end the run
            print(f"    {DIM}aborted: {e}{RESET}")
        captured.extend(rows)

        if not args.snapshots or floor_hit is not None:
            continue
        # The codes the sweep actually found, unless the caller named their
        # own. candidate_codes() applies the limit, so the bound on this pass
        # is decided before a single per-code request is built.
        codes = ask_codes or candidate_codes(rows, args.limit)
        if not codes:
            print(f"    {DIM}no codes came back from this module, so there is"
                  f" nothing to ask records about{RESET}")
            continue
        try:
            captured.extend(per_dtc(el, header, codes, record=want_record,
                                    on_line=show_record, should_stop=sagging))
        except Exception as e:
            print(f"    {DIM}aborted: {e}{RESET}")

    el.close()

    if args.save:
        import datetime
        import json
        import os
        d = os.path.expanduser("~/.local/state/omacar")
        os.makedirs(d, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(d, f"dtc-{stamp}.json")
        # The raw payload is kept alongside the decode. A decoder bug should
        # cost a re-parse, not another trip to the car -- which is exactly what
        # the truncated multi-frame read cost the first time round.
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"captured_at": stamp, "service": "0x19",
                       "vehicle": "honda-crz-2015", "results": captured},
                      f, indent=2)
        print(f"\n  saved  {DIM}{path}{RESET}")

    print(f"\n  {supported} supported subfunction(s) across {len(headers)} module(s).")
    if args.snapshots:
        # Counted separately on purpose. These are per-code requests, not
        # subfunctions, and folding them into the number above would let one
        # module with five stored codes read as ten supported subfunctions.
        print(f"  {answered} per-code record request(s) answered.")
        if floor_hit is not None:
            print(f"  {DIM}the per-code pass stopped early at "
                  f"{floor_hit:.1f} V{RESET}")
    if not supported:
        print(f"  {DIM}Service 0x19 is not answered by these modules in the default"
              f" session.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
