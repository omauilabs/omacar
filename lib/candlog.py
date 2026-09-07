"""Log candidate PIDs against trusted channels, during a drive.

WHY THIS CANNOT JUST READ live.json.

Correlation needs the candidate and the yardstick sampled at the same moment.
The gauge daemon already writes trusted channels to live.json continuously, and
pairing a candidate reading with whatever live.json happened to hold would be
easy -- and wrong. Taking the port off the daemon, reading a candidate, giving
it back and then reading live.json puts two to three seconds between the two
numbers. Coolant temperature would survive that; road speed would not, and road
speed is one of the better yardsticks precisely because it moves fast.

So this reads BOTH from the same connection inside one burst: the candidate
identifiers, then the standard PIDs, back to back on the adapter it is already
holding.

WHY IT SAMPLES IN BURSTS RATHER THAN CONTINUOUSLY.

Holding the port for a whole drive would leave the gauge dead for two hours.
A burst every ten seconds costs the gauge about a second and yields 700-odd
aligned points on a two-hour trip -- comfortably more than the correlator's
floor, for a signal that is being asked to hold across whole drives rather than
between consecutive seconds.
"""

import json
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402

LOGDIR = os.path.join(connect.STATE, "candidates")

# Standard OBD-II requests for the channels the correlator trusts, so they come
# off the same connection in the same burst as the candidates.
TRUSTED_PIDS = [
    ("0105", "coolant",  lambda d: d[0] - 40 if d else None),
    ("010C", "rpm",      lambda d: (d[0] * 256 + d[1]) / 4 if len(d) > 1 else None),
    ("010D", "speed",    lambda d: float(d[0]) if d else None),
    ("0104", "load",     lambda d: d[0] * 100 / 255 if d else None),
    ("0111", "throttle", lambda d: d[0] * 100 / 255 if d else None),
    ("010F", "intake",   lambda d: d[0] - 40 if d else None),
    ("0110", "maf",      lambda d: (d[0] * 256 + d[1]) / 100 if len(d) > 1 else None),
    ("0106", "stft",     lambda d: (d[0] - 128) * 100 / 128 if d else None),
    ("0107", "ltft",     lambda d: (d[0] - 128) * 100 / 128 if d else None),
]


def _payload(el, elmlib, lines, service, req):
    kind, _detail, _ = elmlib.classify(lines, service, request=req)
    if kind != "positive":
        return None
    return el.payload(lines, request=req) or None


def read_trusted(el, elmlib):
    """The standard channels, from this connection, right now.

    The broadcast address comes from the protocol rather than from the "7DF"
    literal that used to be here. 7DF is 11-bit CAN's functional address and
    nothing else's, so on the 29-bit car this tool was written for the header
    was refused, the exception left read_trusted before its loop, and the
    burst recorded candidates with no yardstick beside them -- which is a row
    the correlator cannot use and cannot tell from a quiet drive.
    """
    import protocols
    out = {}
    if not elmlib.aim(el, protocols.broadcast(getattr(el, "protocol", None))):
        return out
    for req, name, decode in TRUSTED_PIDS:
        try:
            data = _payload(el, elmlib, el.request(req), 0x01, req)
        except Exception:                                     # noqa: BLE001
            data = None
        if not data:
            continue
        # Skip "41" + the PID echo, then decode the value bytes.
        body = data[4:] if len(data) > 4 else ""
        try:
            raw = bytes.fromhex(body)
            out[name] = decode(raw)
        except (ValueError, IndexError, TypeError):
            pass
    return out


def read_candidates(el, elmlib, cands):
    """Every candidate identifier, as its raw payload hex."""
    out = {}
    for c in cands:
        # A candidate whose header this protocol cannot use is left out of the
        # row entirely. It must not be read on the previous candidate's
        # address: two identifiers sharing one module's answers is exactly the
        # false correlation this whole file exists to avoid.
        if not elmlib.aim(el, c["header"]):
            continue
        try:
            lines = el.request(c["request"], patient=True, timeout=4.0)
            data = _payload(el, elmlib, lines, int(c["request"][:2], 16), c["request"])
        except Exception:                                     # noqa: BLE001
            data = None
        if data:
            out[c["id"]] = data
    return out


def burst(cands):
    """One aligned sample of everything. Returns a row, or None."""
    import dtc as dtclib
    import elm as elmlib

    port, _kind = connect.resolve()
    if not port:
        return {"t": time.time(), "skipped": "no adapter"}
    if not connect.request_port(port):
        return {"t": time.time(), "skipped": "daemon would not yield"}
    try:
        el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
        try:
            el.init()
            v = dtclib.battery_volts(el)
            if v is not None and v < dtclib.LOW_VOLTS:
                return {"t": time.time(), "volts": v,
                        "skipped": "battery too low"}
            row = {"t": time.time(), "volts": v}
            row["candidates"] = read_candidates(el, elmlib, cands)
            row["channels"] = read_trusted(el, elmlib)
            return row
        finally:
            try:
                el.close()
            except Exception:                                 # noqa: BLE001
                pass
    except Exception as e:                                    # noqa: BLE001
        return {"t": time.time(), "error": f"{type(e).__name__}: {e}"}
    finally:
        connect.release_port()


def candidates_from_profile(slug):
    """Entries worth watching: anything not already settled.

    `validated` needs no further evidence and `refuted` is a closed question,
    so both are skipped -- there is no point spending bus time re-deciding
    something somebody has already decided.
    """
    import profile as P
    doc, _path = P.load(slug)
    if not doc:
        return []
    return [{"id": p["id"], "header": p["header"], "request": p["request"]}
            for p in (doc.get("pid") or [])
            if p.get("confidence") in ("candidate", "observed")
            and p.get("header") and p.get("request")]


def run(slug, interval, logpath, once=False):
    os.makedirs(os.path.dirname(logpath), exist_ok=True)
    cands = candidates_from_profile(slug)
    if not cands:
        print("  nothing to watch: no candidate or observed entries in "
              f"profile {slug!r}")
        return 1
    print(f"\n  watching {len(cands)} candidate(s) every {interval:g}s")
    print(f"  {logpath}\n")
    n = 0
    while True:
        started = time.time()
        row = burst(cands)
        try:
            with open(logpath, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            print(f"  could not write: {e}", file=sys.stderr, flush=True)
        n += 1
        ts = time.strftime("%H:%M:%S", time.localtime(row.get("t", time.time())))
        if row.get("skipped") or row.get("error"):
            print(f"  {ts}  #{n}  {row.get('skipped') or row.get('error')}", flush=True)
        else:
            got = len(row.get("candidates") or {})
            ch = len(row.get("channels") or {})
            sp = (row.get("channels") or {}).get("speed")
            print(f"  {ts}  #{n}  {got}/{len(cands)} candidates, {ch} channels"
                  f"{'' if sp is None else f', {sp:.0f} km/h'}", flush=True)
        if once:
            return 0
        time.sleep(max(2.0, interval - (time.time() - started)))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar candlog", add_help=True)
    ap.add_argument("--profile", default="honda-crz-2015")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--log", default="")
    args = ap.parse_args(argv)
    logpath = args.log or os.path.join(
        LOGDIR, time.strftime(f"{args.profile}-%Y%m%d-%H%M%S.jsonl"))
    try:
        return run(args.profile, args.interval, logpath, once=args.once)
    except KeyboardInterrupt:
        print("\n  stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
