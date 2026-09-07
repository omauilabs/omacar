"""Periodic DTC-status logging, for a long drive.

WHAT THIS IS FOR.

Parked with the engine off, every DTC on this car reports status bit 0x40 --
testNotCompletedThisOperationCycle. The monitor has not run. No amount of
probing in a driveway changes that, because the conditions the monitor waits
for are speed, load and temperature.

A long drive is what makes them run. This samples the status bytes every few
minutes so the transition from "not run" to a real pass or fail is captured as
it happens, which is data no parked session can produce at any price.

WHY IT IS SAFE TO RUN WHILE MOVING, WHEN `prospect` IS NOT.

prospect is gated behind a stationary check because it floods the bus with
thousands of requests to identifiers nobody has confirmed exist. This sends
about eight requests, a few minutes apart, to subfunctions this car has already
answered. The difference in bus load is four orders of magnitude, and the gate
exists for the load, not for the service number.

It still refuses on low voltage. See lib/dtc.py for why that check exists.
"""

import json
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import dtc as dtclib  # noqa: E402
import elm as elmlib  # noqa: E402

LOGDIR = os.path.join(connect.STATE, "dtclog")

# Only the two cheap status subfunctions. 0x0A is the catalogue of every code a
# module can ever set: it is static, it is 199 bytes, and re-reading it every
# five minutes for a whole drive would be a lot of bus time to confirm a
# constant. It is captured once per run instead.
WATCH = [("01FF", "count"), ("02FF", "status")]
CATALOGUE = "0A"


def sample_once(el, headers, want_catalogue):
    """One pass over the modules. Returns a record dict."""
    rec = {"t": time.time(), "volts": dtclib.battery_volts(el), "modules": {}}
    for header in headers:
        el.set_header(header)
        mod = {}
        for sub, key in WATCH:
            req = "19" + sub
            lines = el.request(req, patient=True, timeout=6.0)
            kind, detail, _ = elmlib.classify(lines, dtclib.SERVICE, request=req)
            if kind != "positive":
                mod[key] = {"kind": kind, "detail": detail}
                continue
            data = el.payload(lines, request=req)
            if key == "count":
                try:
                    mod[key] = {"kind": kind, "count": int(data[8:12], 16)}
                except (ValueError, IndexError):
                    mod[key] = {"kind": kind, "raw": data}
            else:
                mod[key] = {"kind": kind, "raw": data,
                            "dtcs": [{"code": c, "status": s,
                                      "flags": dtclib.decode_status(s)}
                                     for c, s in dtclib.parse_dtc_list(data, 2)]}
        if want_catalogue:
            req = "19" + CATALOGUE
            lines = el.request(req, patient=True, timeout=8.0)
            kind, _, _ = elmlib.classify(lines, dtclib.SERVICE, request=req)
            if kind == "positive":
                data = el.payload(lines, request=req)
                mod["catalogue"] = {
                    "raw": data,
                    "dtcs": [c for c, _ in dtclib.parse_dtc_list(data, 2)]}
        rec["modules"][header] = mod
    return rec


# How often to look for the adapter while there is not one, and how often to
# leave a note in the log saying we are still waiting.
IDLE_POLL = 15.0
IDLE_NOTE_EVERY = 1800.0


def run(interval, headers, logpath, once=False, verbose=True):
    """Sample forever. Designed to survive a whole drive unattended.

    NOTHING IN HERE MAY RAISE PAST THE LOOP.

    This runs in a car, on a laptop nobody is looking at, while the person who
    started it is driving. A crash at minute four of a two-hour trip loses the
    entire point of the trip, and there is no one to notice and restart it. So
    every failure -- adapter unplugged, engine off between legs, a module that
    stops answering mid-request -- is caught, logged and retried.
    """
    os.makedirs(os.path.dirname(logpath), exist_ok=True)
    first = True
    n = 0
    # What we are currently skipping for, and when we last wrote it down.
    waiting_for = None
    waiting_logged = 0.0
    while True:
        started = time.time()
        rec = None
        port = None
        leased = False
        try:
            port, kind = connect.resolve()
            if not port:
                rec = {"t": time.time(), "skipped": "no adapter"}
            else:
                # Take the lease so the gauge daemon steps aside, and give it
                # back in the finally below however this turns out.
                leased = connect.request_port(port)
                if not leased:
                    rec = {"t": time.time(), "skipped": "daemon would not yield"}
                else:
                    el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
                    try:
                        el.init()
                        v = dtclib.battery_volts(el)
                        if v is not None and v < dtclib.LOW_VOLTS:
                            rec = {"t": time.time(), "volts": v,
                                   "skipped": "battery below %.1fV" % dtclib.LOW_VOLTS}
                        else:
                            rec = sample_once(el, headers, want_catalogue=first)
                            first = False
                    finally:
                        try:
                            el.close()
                        except Exception:                     # noqa: BLE001
                            pass
        except Exception as e:                                # noqa: BLE001
            rec = {"t": time.time(), "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            if leased:
                try:
                    connect.release_port()
                except Exception:                             # noqa: BLE001
                    pass

        # WAITING IS NOT AN EVENT, AND IT IS NOT A REASON TO STOP.
        #
        # Unplug the adapter at the end of a leg and this used to append
        # {"skipped": "no adapter"} every five minutes, for as long as the
        # laptop stayed on -- an unbounded file of a car saying nothing, and no
        # way to see at a glance where the driving stopped.
        #
        # Exiting instead would be worse: a journey is legs with meals and
        # hotels between them, and a logger that gave up during lunch would
        # have to be remembered and restarted before leg two, which is exactly
        # the kind of thing nobody remembers. So it keeps waiting -- quietly,
        # and paying attention.
        skip = rec.get("skipped") if isinstance(rec, dict) else None
        note = True
        if skip:
            # One line when the waiting starts, then a heartbeat, so the log
            # still records that the tool was awake and watching.
            if skip == waiting_for and (started - waiting_logged) < IDLE_NOTE_EVERY:
                note = False
            else:
                waiting_for, waiting_logged = skip, started
        else:
            waiting_for, waiting_logged = None, 0.0

        if note:
            try:
                with open(logpath, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except OSError as e:
                print("dtclog: could not write log: %s" % e,
                      file=sys.stderr, flush=True)

        n += 1
        if verbose and note:
            summarise(rec, n)
        if once:
            return rec
        # Interval measured from the start of the sample, so a slow sample does
        # not push every later one later still.
        #
        # While there is no adapter, look again far more often than the sample
        # interval: plugging in at the start of leg two should start logging in
        # seconds, not up to five minutes later.
        wait = IDLE_POLL if skip == "no adapter" else interval
        time.sleep(max(2.0, wait - (time.time() - started)))


def summarise(rec, n):
    if not rec:
        return
    ts = time.strftime("%H:%M:%S", time.localtime(rec.get("t", time.time())))
    if "skipped" in rec:
        print("  %s  #%d  skipped: %s" % (ts, n, rec["skipped"]), flush=True)
        return
    if "error" in rec:
        print("  %s  #%d  error: %s" % (ts, n, rec["error"]), flush=True)
        return
    v = rec.get("volts")
    bits = ["%s  #%d" % (ts, n), "%.1fV" % v if v else "  -  "]
    for header, mod in rec.get("modules", {}).items():
        st = mod.get("status", {})
        dtcs = st.get("dtcs", [])
        # "pending" is the interesting count: a monitor that has RUN and failed
        # once. A drive that turns 0x40 into a pending code is the whole reason
        # this program exists.
        notrun = sum(1 for d in dtcs if d["status"] & 0x40)
        real = sum(1 for d in dtcs if d["status"] & 0x0F)
        bits.append("%s %d dtc/%d notrun/%d active" %
                    (header[4:6], len(dtcs), notrun, real))
    print("  " + "  ".join(bits), flush=True)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar dtclog", add_help=True)
    ap.add_argument("--interval", type=float, default=300.0,
                    help="seconds between samples (default 300)")
    ap.add_argument("--headers", default="18DA03F1,18DA04F1",
                    help="modules to watch; only ones supporting 0x19 are useful")
    ap.add_argument("--once", action="store_true", help="one sample, then exit")
    ap.add_argument("--log", default="", help="path to the JSONL log")
    args = ap.parse_args(argv)

    headers = [h.strip().upper() for h in args.headers.split(",") if h.strip()]
    logpath = args.log or os.path.join(
        LOGDIR, time.strftime("drive-%Y%m%d-%H%M%S.jsonl"))
    print("\n  OmaCar DTC log")
    print("  every %gs · %s" % (args.interval, ", ".join(headers)))
    print("  %s\n" % logpath)
    try:
        run(args.interval, headers, logpath, once=args.once)
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
