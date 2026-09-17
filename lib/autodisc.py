"""Discovery that happens on its own, a little at a time.

Today a sweep is a person deciding to run one, sitting with the car for
seventy minutes. That does not scale and it is not how anybody uses a car. This
maps what has not been seen, in short bursts, resuming across weeks -- and it
decides for itself when doing so is safe.

THE SAFETY MODEL, WHICH IS THE WHOLE DESIGN.

Three states, and the difference between them is the alternator:

  MOVING -- never. Flooding the bus with unknown requests at speed is not a
  risk worth taking for data, and this is the one rule with no budget attached.
  Road speed is re-checked before every burst, not once at the start, because a
  car stopped at a light is about to not be.

  STATIONARY, ENGINE RUNNING -- freely. Above about 13 volts the alternator is
  carrying the load, so bus time costs nothing that matters. This is the good
  case, and it is more common than it sounds: every red light on a long drive
  is a few seconds of it.

  IGNITION ON, ENGINE OFF -- on a strict budget. Every request is drawn from a
  battery with nothing replacing it. A 25-minute session in this state once put
  an ABS warning on the dash of the development car, so it gets a few minutes
  at a time and stops well above the point where anything complains.

The gauge is not starved either: bursts are small and the port lease is handed
back between them, so the cluster pauses for a second rather than going dark.
"""

import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import frontier  # noqa: E402

# Above this the alternator is charging and bus time is effectively free.
CHARGING_VOLTS = 13.0
# Engine off: stop well before anything on the car starts complaining.
ENGINE_OFF_FLOOR = 12.2
# How long we are willing to run on battery alone, per key-on.
ENGINE_OFF_BUDGET = 180.0

# Identifiers per burst. Small enough that a car pulling away is noticed within
# a couple of seconds, large enough that the ~1s of lease overhead is not the
# dominant cost.
BURST = 48

DEFAULT_RANGE = (0x0000, 0xA5FF)   # ISO 14229 manufacturer-specific space


class Halt(Exception):
    """A reason to stop that is not an error."""


def road_speed(el, elmlib):
    """km/h, or None if the car will not say.

    None is treated as "moving" by the caller. A discovery pass that cannot
    confirm the car is stationary must not assume it is -- the one-off sweeps
    ask a person to confirm with --parked, and there is no person here.
    """
    try:
        lines = el.request("010D")
        kind, _d, _ = elmlib.classify(lines, 0x01, "010D")
        if kind != "positive":
            return None
        data = el.payload(lines, request="010D")
        if not data or len(data) < 6:
            return None
        return float(int(data[4:6], 16))
    except Exception:                                          # noqa: BLE001
        return None


def engine_running(el, elmlib):
    try:
        lines = el.request("010C")
        kind, _d, _ = elmlib.classify(lines, 0x01, "010C")
        if kind != "positive":
            return False
        data = el.payload(lines, request="010C")
        if not data or len(data) < 8:
            return False
        return (int(data[4:8], 16) / 4.0) > 250
    except Exception:                                          # noqa: BLE001
        return False


def gate(el, elmlib, dtclib, spent_on_battery):
    """May we sweep right now, and under which rule?

    Returns ("charging"|"battery", volts) or raises Halt with a plain reason.
    """
    speed = road_speed(el, elmlib)
    if speed is None:
        raise Halt("road speed could not be read, so I cannot confirm the car "
                   "is stationary")
    if speed > 0:
        raise Halt(f"the car is moving ({speed:.0f} km/h)")

    volts = dtclib.battery_volts(el)
    if volts is None:
        raise Halt("battery voltage could not be read")
    if volts >= CHARGING_VOLTS and engine_running(el, elmlib):
        return "charging", volts
    if volts < ENGINE_OFF_FLOOR:
        raise Halt(f"battery at {volts:.1f} V, below the {ENGINE_OFF_FLOOR} V floor")
    if spent_on_battery >= ENGINE_OFF_BUDGET:
        raise Halt(f"used this key-on's {ENGINE_OFF_BUDGET:.0f}s battery budget; "
                   f"start the engine to continue")
    return "battery", volts


def sweep_burst(el, elmlib, header, service, lo, hi):
    """Ask one contiguous chunk. Returns the identifiers that answered."""
    el.set_header(header)
    hits = []
    for pid in range(lo, hi + 1):
        req = f"{service:02X}{pid:04X}"
        try:
            kind, _detail, _ = elmlib.classify(el.request(req), service, req)
        except Exception:                                      # noqa: BLE001
            break
        if kind == "positive":
            hits.append(f"{pid:04X}")
    return hits


def one_pass(headers, service, rng, budget_s, spent_on_battery, on_note):
    """One connected session: sweep until the gate says stop or budget is out.

    Returns (chunks_done, hits, seconds_on_battery, reason_for_stopping).
    """
    import dtc as dtclib
    import elm as elmlib
    import garage

    port, _kind = connect.resolve()
    if not port:
        return 0, [], 0.0, "no adapter"
    if not connect.request_port(port):
        return 0, [], 0.0, "the daemon is holding the port"

    started = time.time()
    battery_s = 0.0
    chunks = 0
    all_hits = []
    reason = "budget spent"
    key = garage.current()
    doc = frontier.load(key)

    try:
        el = elmlib.Elm(port, baudrate=connect.link_baud(port))
        try:
            el.init()
            while time.time() - started < budget_s:
                try:
                    mode, volts = gate(el, elmlib, dtclib,
                                       spent_on_battery + battery_s)
                except Halt as h:
                    reason = str(h)
                    break

                target = None
                for header in headers:
                    svc = (doc.get("services") or {}).get(
                        frontier.key(service, header)) or {}
                    gap = frontier.next_gap(svc.get("swept") or [],
                                            rng[0], rng[1], BURST)
                    if gap:
                        target = (header, gap)
                        break
                if not target:
                    reason = "every module is fully swept over this range"
                    break

                header, (lo, hi) = target
                t0 = time.time()
                hits = sweep_burst(el, elmlib, header, service, lo, hi)
                took = time.time() - t0
                if mode == "battery":
                    battery_s += took

                frontier.record(doc, service, header, lo, hi, hits)
                frontier.save(doc, key)
                chunks += 1
                all_hits += [(header, hexid) for hexid in hits]
                on_note(header, lo, hi, hits, mode, volts)
        finally:
            try:
                el.close()
            except Exception:                                  # noqa: BLE001
                pass
    except Exception as e:                                     # noqa: BLE001
        reason = f"{type(e).__name__}: {e}"
    finally:
        connect.release_port()
    return chunks, all_hits, battery_s, reason


def record_hits(slug, hits, service):
    """File anything found into the profile as a candidate.

    Candidate, never higher. This ran unattended: it can prove an identifier
    answered and nothing else, and the profile format exists precisely so that
    a machine cannot award itself a confidence level only evidence can grant.
    """
    if not hits:
        return 0
    import profile as P
    doc, path = P.load(slug)
    if not doc:
        return 0
    have = {p.get("id") for p in (doc.get("pid") or [])}
    added = 0
    for header, hexid in hits:
        pid_id = f"{header}_{service:02X}{hexid}".lower()
        if pid_id in have:
            continue
        doc.setdefault("pid", []).append({
            "id": pid_id, "header": header,
            "request": f"{service:02X}{hexid}",
            "service": service, "confidence": "candidate",
            "provenance": {
                "found_by": "omacar discover",
                "found_on": (doc.get("car") or {}).get("description")
                            or (doc.get("car") or {}).get("slug") or "",
                "method": f"automatic discovery, service 0x{service:02X}",
                "first_seen": time.strftime("%Y-%m-%d"),
            },
        })
        have.add(pid_id)
        added += 1
    if added:
        P.write(path, doc)
    return added


BOLD, DIM, RESET, GREEN, YELLOW = "\033[1m", "\033[2m", "\033[0m", "\033[32m", "\033[33m"


def cmd_status(service, rng):
    import garage
    key = garage.current()
    doc = frontier.load(key)
    rows = frontier.summary(doc)
    print(f"\n  {BOLD}Discovery frontier{RESET}  {DIM}{key}{RESET}")
    if not rows:
        print("  nothing swept yet\n")
        return 0
    total_span = rng[1] - rng[0] + 1
    for r in rows:
        svc_hex, header = r["key"].split("/")
        try:
            _i, _t, pct = frontier.progress(doc, int(svc_hex, 16), header,
                                            rng[0], rng[1])
        except ValueError:
            pct = 0.0
        last = (time.strftime("%d %b %H:%M", time.localtime(r["last"]))
                if r.get("last") else "—")
        print(f"    {r['key']:<20} {pct:5.1f}%  {r['swept']:>6} asked  "
              f"{r['found']} found   {DIM}{last}{RESET}")
    print(f"\n  {DIM}range {rng[0]:#06x}-{rng[1]:#06x} ({total_span} identifiers "
          f"per module){RESET}\n")
    return 0


def run(headers, service, rng, slug, interval, budget, once=False):
    print(f"\n  {BOLD}OmaCar automatic discovery{RESET}")
    print(f"  {len(headers)} module(s) · service 0x{service:02X} · "
          f"{rng[0]:#06x}-{rng[1]:#06x}")
    print(f"  {DIM}sweeps only while stationary; freely when charging, "
          f"{ENGINE_OFF_BUDGET:.0f}s per key-on on battery{RESET}\n")

    battery_spent = 0.0
    while True:
        started = time.time()

        def note(header, lo, hi, hits, mode, volts):
            mark = f"{GREEN}{len(hits)} hit(s){RESET}" if hits else f"{DIM}—{RESET}"
            tone = GREEN if mode == "charging" else YELLOW
            print(f"  {time.strftime('%H:%M:%S')}  {header} "
                  f"{lo:#06x}-{hi:#06x}  {mark}  {tone}{mode} {volts:.1f}V{RESET}",
                  flush=True)

        chunks, hits, on_battery, reason = one_pass(
            headers, service, rng, budget, battery_spent, note)
        battery_spent += on_battery
        # Charging resets the budget: the alternator has replaced what was used.
        if on_battery == 0 and chunks:
            battery_spent = 0.0

        added = record_hits(slug, hits, service)
        if chunks:
            print(f"  {DIM}{chunks} chunk(s), {len(hits)} hit(s), "
                  f"{added} new profile entr(ies) — {reason}{RESET}", flush=True)
        else:
            print(f"  {time.strftime('%H:%M:%S')}  {DIM}idle: {reason}{RESET}",
                  flush=True)
        if once:
            return 0
        time.sleep(max(20.0, interval - (time.time() - started)))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar discover", add_help=True)
    ap.add_argument("action", nargs="?", default="run",
                    choices=["run", "status", "reset"])
    ap.add_argument("--headers", default="18DA10F1,18DA03F1,18DA04F1")
    ap.add_argument("--service", default="0x22")
    ap.add_argument("--range", dest="rng", default="0000-A5FF")
    ap.add_argument("--profile", default="honda-crz-2015")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--budget", type=float, default=45.0,
                    help="seconds of sweeping per attempt")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)

    service = int(args.service, 16)
    lo, _, hi = args.rng.partition("-")
    rng = (int(lo, 16), int(hi or lo, 16))
    headers = [h.strip().upper() for h in args.headers.split(",") if h.strip()]

    if args.action == "status":
        return cmd_status(service, rng)
    if args.action == "reset":
        import garage
        key = garage.current()
        frontier.save({"vehicle": key, "services": {}}, key)
        print(f"  frontier cleared for {key}")
        return 0
    try:
        return run(headers, service, rng, args.profile, args.interval,
                   args.budget, once=args.once)
    except KeyboardInterrupt:
        print("\n  stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
