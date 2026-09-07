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
    ("0416", "reportDTCSnapshotRecordByDTC",  "freeze frame, all records"),
    ("42FF", "reportWWHOBDDTCByMaskRecord",   "world-wide harmonised OBD view"),
]

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


def probe(el, header, on_line):
    """Every subfunction against one ECU. Returns a list of result dicts."""
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
    ap = argparse.ArgumentParser(prog="omacar dtc", add_help=True)
    ap.add_argument("--headers", default=",".join(headers_for()))
    ap.add_argument("--parked", action="store_true",
                    help="confirm the car is parked when road speed cannot be read")
    ap.add_argument("--save", action="store_true",
                    help="write the full result to ~/.local/state/omacar/")
    args = ap.parse_args(argv)

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

    supported = 0
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

        try:
            captured.extend(probe(el, header, show))
        except Exception as e:  # a module that stops answering must not end the run
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
    if not supported:
        print(f"  {DIM}Service 0x19 is not answered by these modules in the default"
              f" session.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
