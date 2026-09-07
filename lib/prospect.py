"""omacar prospect — find the manufacturer PIDs nobody documents.

Generic OBD-II will not tell you a CR-Z's IMA state of charge, assist/regen
current, or battery temperature. Those live behind Honda-specific services,
and no off-the-shelf tool ships a Honda custom-PID set. So: ask the ECU
directly, and record what answers.

Method
------
1. Sweep candidate headers x PIDs with a read-only service (0x21 or 0x22).
2. Anything that answers positively is a responder.
3. Re-sample every responder several times with the engine running, and diff
   the payloads. **Bytes that never change are almost certainly not the
   reading you want.** Variance is the signal.
4. Draft a profile of candidates for a human to name and validate.

Nothing here is authoritative. A responder is evidence that an address
exists, not knowledge of what it means — the draft profile says so, and the
cluster refuses to display an unvalidated candidate.
"""
import atexit
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connect  # noqa: E402
import elm as elmlib  # noqa: E402
import profile as profilelib  # noqa: E402
# signals.py owns the answer to "how much of a positive reply is echo". This
# module used to know it too, and the two answers differed -- see the long note
# in sweep(). Importing it costs nothing: signals pulls in os, re and sys, and
# defers python-obd to the one function that needs it.
import signals as signalslib  # noqa: E402

# WHY THERE IS NO LONGER A LITERAL LIST HERE.
#
# This was ["07E0" ... "07E5"] -- Honda's 11-bit diagnostic addresses, with a
# leading zero, written when the only car in the room was known to be on
# 11-bit CAN. It is wrong twice over. Four hex digits is not a valid ATSH
# header on EITHER CAN protocol: 11-bit wants three and 29-bit wants eight, so
# after 0b1fc1f every one of these is refused, and before it every one was
# accepted by the adapter and then never answered by the car. And the
# development vehicle is a CR-Z, which is 29-bit -- so the default list could
# not reach the very car it was written for.
#
# The addresses now come from protocols.physical() once the adapter has told
# us what it negotiated, which is the only moment the right shape is knowable.
# `--headers` still overrides, for the person who knows better than the table.

DIM, BOLD, GREEN, YELLOW, RESET = "\033[2m", "\033[1m", "\033[32m", "\033[33m", "\033[0m"


def parse_range(spec, width):
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return range(int(lo, 16), int(hi, 16) + 1)
    return range(0, 1 << (4 * width))


def moving(el):
    """True if the car reports any road speed. Refuse to sweep if so.

    The broadcast address is asked of protocols, not written here. "07DF" is
    the 11-bit CAN functional address and it is the wrong shape on a 29-bit
    car -- which is every Honda this project has touched -- so set_header()
    rightly refused it, and that refusal broke two things at once: `omacar dtc`
    crashed before it could read anything, and ops.preflight caught the
    ValueError in its bare except, came back with mv None, and refused every
    clear on every CAN car with "road speed could not be read". A guard failing
    closed for a reason that had nothing to do with the car.

    There is deliberately no fallback to "07DF" here. Falling back to the
    malformed header is falling back to the bug, and set_header would raise
    again from inside the handler that was meant to contain it.
    """
    import protocols
    el.set_header(protocols.broadcast(getattr(el, "protocol", None)))
    kind, _, data = elmlib.classify(el.request("010D"), 0x01, "010D")
    # The literal `data[4:6]` that used to be here was a third copy of the fact
    # the sweep got wrong for 0x22 -- how much of a positive reply is echo
    # rather than data. It happened to be right, because mode 01 answers 41 +
    # one PID byte, but "happened to be right" is what the other two copies
    # were too, until a service with a two-byte identifier turned up. One
    # source, everywhere, so the next service to arrive can only be wrong once.
    echo = 2 * signalslib.payload_offset("010D")
    if kind != "positive" or len(data) < echo + 2:
        return None                      # cannot tell — caller decides
    try:
        return int(data[echo:echo + 2], 16) > 0
    except ValueError:
        return None


def sweep(el, headers, service, pids, delay, on_progress):
    found, tried = [], 0
    total = len(headers) * len(pids)
    for header in headers:
        # A header this protocol cannot use is skipped whole, not swept with
        # whatever address was set last. Sweeping it anyway would file one
        # module's answers under another module's name, which is the same
        # class of untruth as a scan tool inventing hardware.
        if not elmlib.aim(el, header):
            tried += len(pids)
            on_progress(tried, total, header, "", "skip")
            continue
        dead = 0
        for pid in pids:
            req = f"{service:02X}{pid:0{4 if service == 0x22 else 2}X}"
            kind, detail, data = elmlib.classify(el.request(req), service, req)
            tried += 1
            on_progress(tried, total, header, req, kind)
            if kind == "positive":
                # HOW MUCH OF THE REPLY IS ECHO IS ASKED FOR, NOT ASSUMED.
                #
                # This line read `(len(data) - 4) // 2`: four hex digits, two
                # bytes of echo, for every service there is. That is right for
                # 0x21, which answers 61 + one PID byte, and right for mode 01,
                # which answers 41 + one PID byte. It is wrong for 0x22, whose
                # positive reply is 62 followed by TWO identifier bytes. Every
                # 0x22 candidate this tool has ever recorded therefore counted
                # the identifier's low byte as the first byte of the payload.
                #
                # An off-by-one in a length would be a cosmetic bug. This one
                # is not, because the byte positions travel. payload_len and
                # varying_bytes go into the draft profile; a person reads
                # "byte 0 moves", writes the formula "A", and the daemon hands
                # that formula to lib/signals.py -- which counts from ITS
                # offset, the correct 3. So "A" named one byte on the way in
                # and a different byte on the way out. On the fixture that
                # reproduced this, the prospector reported the moving byte as
                # B and B was the byte that never moved: the candidate would
                # have been marked `validated` against a constant. A number
                # that is wrong while looking exactly like a reading is the one
                # thing this project promises never to show.
                #
                # So the knowledge lives in one place now and is asked for
                # here. A second copy that happens to agree today is the same
                # bug waiting for the next service to be added, which is how
                # the first one arrived.
                echo = 2 * signalslib.payload_offset(req)
                found.append({"header": header, "service": service,
                              "pid": f"{pid:X}", "request": req, "sample": data,
                              "payload_len": max(0, (len(data) - echo) // 2)})
                dead = 0
            elif kind == "silent":
                dead += 1
                # A header that has answered nothing at all is not there.
                if dead >= 24 and not any(f["header"] == header for f in found):
                    on_progress(tried, total, header, req, "skip")
                    break
            time.sleep(delay)
    return found


def resample(el, found, rounds, delay, on_progress):
    """Re-read every responder and mark which payload bytes actually move.

    The offsets recorded here are *payload* offsets -- counted from the first
    byte after the service and identifier echo -- because that is the only
    thing a formula can name. They are measured from the same boundary
    signals.payload_offset() uses, for the reason spelled out in sweep(): a
    byte index means nothing except relative to an agreed first byte, and when
    the two modules disagreed about where that was, "byte 0 moves" and the
    formula "A" pointed at different bytes of the same reply.
    """
    series = {id(f): [f["sample"]] for f in found}
    for r in range(rounds):
        for f in found:
            # It answered during the sweep, so this cannot normally fail --
            # but if it does, an empty sample is the honest record. Re-asking
            # on the previous responder's header would produce a byte that
            # "never moves" and quietly bury a real candidate.
            if not elmlib.aim(el, f["header"]):
                series[id(f)].append("")
                continue
            kind, _, data = elmlib.classify(el.request(f["request"]), f["service"], f["request"])
            series[id(f)].append(data if kind == "positive" else "")
            time.sleep(delay)
        on_progress(r + 1, rounds, "", "", "resample")
    for f in found:
        samples = [s for s in series[id(f)] if s]
        varying = []
        if len(samples) > 1:
            n = min(len(s) for s in samples)
            echo = 2 * signalslib.payload_offset(f["request"])
            for i in range(echo, n, 2):   # skip the service+identifier echo
                if len({s[i:i + 2] for s in samples}) > 1:
                    varying.append((i - echo) // 2)
        f["varying"] = varying
        f["samples"] = samples[:8]
    return found


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar prospect", add_help=True)
    ap.add_argument("--service", default="0x21",
                    help="read-only service to sweep: 0x21 (Honda) or 0x22 (UDS)")
    ap.add_argument("--headers", default="",
                    help="module addresses to sweep; default: the ones this "
                         "protocol uses")
    ap.add_argument("--range", dest="rng", default="",
                    help="PID range in hex, e.g. 00-FF or 0000-01FF")
    ap.add_argument("--delay", type=float, default=0.06)
    ap.add_argument("--rounds", type=int, default=6, help="resamples per responder")
    ap.add_argument("--car", default="honda-crz-2015")
    ap.add_argument("--parked", action="store_true",
                    help="confirm the car is parked when road speed cannot be read")
    args = ap.parse_args(argv)

    service = int(args.service, 16)
    if service not in elmlib.READ_ONLY_SERVICES:
        sys.exit(f"omacar: service {args.service} is not read-only; refusing.")

    port, kind = connect.resolve()
    if not port:
        sys.exit("omacar: no adapter and no bench emulator.")
    warn = connect.serial_group_warning(port)
    if warn:
        sys.exit("omacar: " + warn)
    # Take the port lease rather than refusing outright.
    #
    # prospect drives elm.py directly instead of going through
    # connect.connect(), so it did not inherit the handoff that doctor, live
    # and survey get -- it just told you to stop the daemon. Same lease, same
    # deadline, released on the way out however this exits: a sweep that dies
    # halfway must not leave the gauge paused.
    if not connect.request_port(port):
        sys.exit("omacar: the daemon is holding " + port + " and did not let go.\n"
                 "  stop it with: omacar daemon stop")
    atexit.register(connect.release_port)

    width = 2 if service == 0x22 else 1
    pids = parse_range(args.rng, width) if args.rng else parse_range("00-FF", 1) \
        if service != 0x22 else parse_range("0000-00FF", 2)

    # The OBDLink EX runs at 115200; elm.py defaults to 38400, which is the SX.
    # Asked at the wrong rate an EX returns line noise rather than silence, so
    # a sweep would record "no responder" for every PID on the car.
    el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
    print(f"\n  {BOLD}OmaCar prospector{RESET}  {DIM}{port} ({kind}){RESET}")
    el.init()

    # Pace the sweep for the wire it is actually on.
    #
    # These timings were tuned on 500 kbaud CAN. ISO 9141-2 runs at 10.4 kbaud
    # and needs an order of magnitude longer before silence means anything --
    # applied unchanged, a healthy slow car times out on every request and
    # reports as having nothing to say.
    import protocols
    prof = protocols.describe(el.protocol)
    pace = protocols.pacing(el.protocol)
    if prof:
        print(f"  {DIM}{protocols.summary(el.protocol)}{RESET}")
        if not prof["verified"]:
            print(f"  {YELLOW}This protocol has not been tested by this "
                  f"project.{RESET} Results are worth reporting either way.")
    if args.delay == 0.06:            # untouched default: follow the protocol
        args.delay = pace["delay"]
    if pace.get("atst"):
        try:
            el.set_timeout(int(pace["atst"], 16) * 4)
        except (ValueError, AttributeError, TypeError):
            pass

    # WHICH ADDRESSES TO ASK, AND WHY NOT BEFORE NOW.
    #
    # A header's shape is a property of the wire, not of the car's make, so
    # the list cannot be assembled until the adapter has said what it
    # negotiated -- which is why this sits after init() rather than next to
    # the argument parser where a default belongs. --headers still wins: the
    # table is a starting point for a sweep, not an authority on somebody
    # else's vehicle.
    if args.headers.strip():
        headers = [h.strip().upper() for h in args.headers.split(",") if h.strip()]
    else:
        headers = [h for h, _label in protocols.physical(el.protocol)]
        if not headers:
            el.close()
            sys.exit(f"\n  {protocols.summary(el.protocol)}\n"
                     "  I do not know how this bus addresses its modules, so I\n"
                     "  will not guess at addresses to flood it with.\n\n"
                     "  If you know them:  omacar prospect --headers ...\n")
        print(f"  {DIM}addresses: {', '.join(headers)}{RESET}")

    # A RANGE WIDER THAN THE IDENTIFIER THE SERVICE TAKES.
    #
    # `--service 0x21 --range F100-F1FF` builds "21F100" -- a two-byte
    # identifier on a service whose identifier is one byte. The adapter sends
    # it happily, the module cannot parse it, and 256 malformed questions come
    # back as 256 silences, which reads as "nothing on this car" rather than
    # "you asked wrong". F1xx is a 0x22 range; this catches the confusion.
    #
    # The digit count is the same expression sweep() formats with rather than
    # protocols.id_width(), deliberately: id_width falls back to four digits
    # for anything that is not 0x21, which is right for UDS and wrong for mode
    # 09, and a guard that disagrees with the request it is guarding is
    # theatre. When the pre-CAN services land and the width starts varying by
    # protocol, both should move to id_width together.
    digits = 4 if service == 0x22 else 2
    biggest = pids[-1] if len(pids) else 0
    if biggest >= (1 << (4 * digits)):
        el.close()
        sys.exit(f"\n  service 0x{service:02X} takes a {digits}-digit "
                 f"identifier, and {biggest:X} does not fit in one.\n"
                 f"  Two-byte ranges like F190-F19F belong to 0x22:\n\n"
                 f"      omacar prospect --service 0x22 --range {args.rng}\n")

    # The safety gate. A sweep floods the bus with unknown requests; doing
    # that while the car is moving is not a risk worth taking for data.
    if kind == "bench":
        print(f"  {DIM}bench emulator — vehicle-motion gate does not apply{RESET}")
    else:
        mv = moving(el)
        if mv:
            el.close()
            sys.exit("\n  refusing to sweep: the car reports road speed.\n"
                     "  Park it, leave the engine running, and try again.\n")
        if mv is None and not args.parked:
            el.close()
            sys.exit("\n  refusing to sweep: road speed could not be read, so I\n"
                     "  cannot tell whether the car is moving.\n\n"
                     "  If it is parked with the engine running, say so:\n"
                     "      omacar prospect --parked\n")

    # The sweep is the long one, so it is the one that can flatten the battery.
    # See lib/dtc.py for why this exists.
    import dtc as dtclib
    v = dtclib.battery_volts(el)
    if v is not None:
        print(f"  battery {v:.1f} V")
        if v < dtclib.LOW_VOLTS:
            el.close()
            sys.exit(f"\n  refusing to sweep: {v:.1f} V is too low for a key-on\n"
                     f"  session. Start the engine, or charge the battery.\n")

    print(f"  service 0x{service:02X} · {len(headers)} headers · "
          f"{len(pids)} pids · {len(headers) * len(pids)} requests")
    print(f"  {DIM}read-only services only; writes are refused at the transport{RESET}\n")

    last = [0.0]
    started = [time.time()]
    hits = [0]

    def _dur(secs):
        secs = int(max(0, secs))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m{secs % 60:02d}s"
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"

    def progress(i, total, header, req, kind_):
        now = time.time()
        if kind_ == "positive":
            hits[0] += 1
        if kind_ in ("positive", "skip") or now - last[0] > 0.5:
            last[0] = now
            pct = 100.0 * i / max(1, total)
            mark = {"positive": GREEN + "hit " + RESET, "skip": DIM + "skip" + RESET,
                    "resample": DIM + "diff" + RESET}.get(kind_, "    ")

            # AN ETA, BECAUSE THESE RUNS ARE LONG.
            #
            # A bare percentage is fine for something that takes ten seconds.
            # A full 16-bit range on one ECU is over two hours, and without a
            # remaining-time figure there is no way to tell a slow sweep from a
            # stuck one -- or to decide whether it is worth starting before
            # you need the car back. Measured from actual elapsed time rather
            # than a per-request constant, so a bus that slows down is
            # reflected rather than hidden.
            elapsed = now - started[0]
            rate = i / elapsed if elapsed > 0.5 else 0
            eta = f" · {_dur((total - i) / rate)} left" if rate > 0 else ""
            found_s = f" · {hits[0]} hit{'' if hits[0] == 1 else 's'}" if hits[0] else ""

            sys.stdout.write(
                f"\r  {pct:5.1f}%  {header:<9} {req:<7} {mark}"
                f"{DIM}{_dur(elapsed)} elapsed{eta}{found_s}{RESET}\033[K")
            sys.stdout.flush()

    found = sweep(el, headers, service, list(pids), args.delay, progress)
    print(f"\r\033[K  {len(found)} responder(s)\n")

    if found:
        print(f"  resampling {len(found)} responder(s) x{args.rounds} to find "
              f"which bytes move…")
        resample(el, found, args.rounds, args.delay, progress)
        print("\r\033[K", end="")
    el.close()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(connect.STATE, f"prospect-{stamp}.json")
    os.makedirs(connect.STATE, exist_ok=True)
    with open(raw, "w", encoding="utf-8") as f:
        json.dump({"port": port, "service": service, "headers": headers,
                   "found": found}, f, indent=2)

    live = [f for f in found if f.get("varying")]
    print(f"  {BOLD}Results{RESET}\n")
    for f in found:
        v = f.get("varying") or []
        tag = f"{GREEN}{len(v)} byte(s) move{RESET}" if v else f"{DIM}static{RESET}"
        print(f"    {f['header']}  {f['request']:<6} len={f['payload_len']:<3} {tag}")
    print()

    draft = profilelib.write_draft(
        os.path.join(connect.STATE, "profiles", args.car + ".draft.toml"),
        # The protocol recorded here was hardcoded to CAN 11/500 -- the one
        # this project happened to be written against. A profile is a claim
        # about a specific vehicle, and naming the wrong bus in it makes every
        # candidate underneath unverifiable by anyone else.
        {"slug": args.car, "description": "drafted by omacar prospect",
         "protocol": (prof["name"] if prof else f"unknown ({el.protocol})"),
         "discovered": stamp},
        live or found)

    print(f"  raw log   {raw}")
    print(f"  draft     {draft}")
    print(f"\n  {YELLOW}Every entry is a candidate.{RESET} Name it, write its formula,")
    print("  check it against the dash, then set confidence = \"validated\".\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
