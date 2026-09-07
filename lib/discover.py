"""Learn what this particular car is made of.

WHAT "LEARNING A CAR" MEANS HERE.

A generic OBD-II scan tells you the car exists and answers the legally required
questions. It does not tell you how many computers are in there, which of them
will talk to you, what each one monitors, or what it calls itself. That is the
difference between "a 2015 Honda" and "this car, with these six modules, of
which two speak UDS and one holds a catalogue of 49 hybrid fault codes".

This builds that picture incrementally and saves it. Run it again later and it
adds what it learned; it does not start over. A car you have plugged into ten
times should be better understood than one you plugged into once, and nothing
about that should require the owner to know what a DID is.

ALL READ-ONLY.

Discovery never writes, whatever write mode is set to. It asks three kinds of
question: who is there (identification DIDs), what can you set (the 0x19 fault
catalogue), and what do you monitor. None of those change anything.
"""

import json
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import garage  # noqa: E402

# elm and dtc are imported lazily inside learn(), NOT here.
#
# They pull in pyserial, which lives only in the OmaCar venv. The web server
# runs under the system interpreter, so a module-level import here made
# GET /api/learned kill the request handler outright -- reading back what we
# already know about a car needs a JSON file and nothing else, and should not
# depend on a serial library being installed.

# Standard 29-bit diagnostic addresses. Sweeping every possible module address
# would be 255 probes; these are the ones manufacturers actually use, and an
# address that answers nothing here is almost certainly not populated.
CANDIDATE_HEADERS = [
    ("18DA10F1", "engine"),
    ("18DA18F1", "transmission"),
    ("18DA28F1", "ABS / brakes"),
    ("18DA01F1", "body"),
    ("18DA03F1", "hybrid / battery"),
    ("18DA04F1", "hybrid / motor"),
    ("18DA0EF1", "gateway / other"),
    ("18DA40F1", "airbag / restraints"),
    ("18DA60F1", "instrument cluster"),
    ("18DA6AF1", "climate"),
]

# Identification DIDs worth asking every module. Cheap, standardised, and the
# only reliable proof that a module is present and speaking 0x22.
IDENT_DIDS = [
    ("F190", "VIN"),
    ("F18C", "serial number"),
    ("F191", "hardware part number"),
    ("F194", "software version"),
    ("F195", "software part number"),
    ("F181", "application software"),
    ("F110", "ECU part number"),
]


def profile_path(key=None):
    key = key or garage.current()
    d = os.path.join(connect.STATE, "profiles")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{key}.learned.json")


def load_profile(key=None):
    try:
        with open(profile_path(key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"modules": {}, "learned_at": None, "passes": 0}


def save_profile(prof, key=None):
    prof["learned_at"] = time.time()
    p = profile_path(key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prof, f, indent=2)
    os.replace(tmp, p)
    return p


def merge_module(prof, header, found):
    """Add what we just learned without discarding what we knew.

    Union rather than replace: a module that was asleep on this pass should not
    erase what it told us on the last one. The only field that always takes the
    newer value is `seen`.
    """
    mod = prof["modules"].setdefault(header, {})
    mod["seen"] = time.time()
    for k, v in found.items():
        if k == "dtc_catalogue" and isinstance(v, list):
            merged = set(mod.get(k) or []) | set(v)
            mod[k] = sorted(merged)
        elif isinstance(v, dict):
            d = dict(mod.get(k) or {})
            d.update(v)
            mod[k] = d
        elif v not in (None, "", []):
            mod[k] = v
    return mod


def learn_module(el, header, label, deep, on_step, elmlib, dtclib):
    """Everything cheap we can find out about one module."""
    found = {"label": label, "ident": {}, "services": []}
    el.set_header(header)

    # 1. Is anyone home? Identification DIDs, service 0x22.
    for did, what in IDENT_DIDS:
        req = "22" + did
        kind, _, _ = elmlib.classify(el.request(req), 0x22, req)
        if kind == "positive":
            data = el.payload(el.request(req, patient=True, timeout=4.0), request=req)
            found["ident"][what] = decode_ascii(data[6:]) if data else ""
            if "0x22" not in found["services"]:
                found["services"].append("0x22")
        on_step(header, "ident " + did)
        # A module that refuses the first two identification DIDs outright is
        # not going to answer the rest; stop paying for the whole list.
        if len(found["ident"]) == 0 and did == "F191":
            break

    if not found["ident"]:
        return None       # nothing at this address

    # 2. Does it hold a fault catalogue? Service 0x19.
    kind, _, _ = elmlib.classify(el.request("1901FF"), 0x19, "1901FF")
    if kind == "positive":
        found["services"].append("0x19")
        on_step(header, "fault catalogue")
        lines = el.request("190A", patient=True, timeout=8.0)
        k2, _, _ = elmlib.classify(lines, 0x19, "190A")
        if k2 == "positive":
            data = el.payload(lines, request="190A")
            found["dtc_catalogue"] = [c for c, _ in dtclib.parse_dtc_list(data, 2)]

    # 3. Optional deeper pass: a coarse DID probe to see whether this module
    # has any manufacturer data at all before anyone commits an hour to it.
    if deep and "0x22" in found["services"]:
        hits = []
        for pid in range(0x0000, 0x10000, 0x400):
            req = "22%04X" % pid
            k3, _, _ = elmlib.classify(el.request(req), 0x22, req)
            if k3 == "positive":
                hits.append("%04X" % pid)
            on_step(header, "probe %04X" % pid)
        found["coarse_hits"] = hits
    return found


def decode_ascii(hexstr):
    """Hex payload to text, when it plausibly IS text.

    Identification DIDs usually hold ASCII part numbers, but not always, and a
    binary field rendered as mojibake looks like a bug in the tool rather than
    a field that was never text.
    """
    try:
        raw = bytes.fromhex(hexstr)
    except ValueError:
        return hexstr
    printable = sum(1 for b in raw if 32 <= b < 127)
    if raw and printable / len(raw) >= 0.7:
        return raw.decode("ascii", "replace").strip("\x00 ").strip()
    return hexstr


def learn(deep=False, on_step=None, on_module=None):
    """One learning pass. Returns the updated profile."""
    try:
        import dtc as dtclib
        import elm as elmlib
    except ImportError as e:
        raise RuntimeError(
            "the serial library is missing from this interpreter (%s). "
            "Learning needs the OmaCar venv." % e) from e
    on_step = on_step or (lambda *a: None)
    on_module = on_module or (lambda *a: None)

    port, kind = connect.resolve()
    if not port:
        raise RuntimeError("no adapter")
    if not connect.request_port(port):
        raise RuntimeError("the daemon is holding the port")
    try:
        el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
        el.init()
        v = dtclib.battery_volts(el)
        if v is not None and v < dtclib.LOW_VOLTS:
            raise RuntimeError("battery at %.1fV is too low to probe" % v)

        prof = load_profile()
        prof["passes"] = prof.get("passes", 0) + 1
        prof["volts"] = v
        try:
            for header, label in CANDIDATE_HEADERS:
                on_step(header, "probing")
                found = learn_module(el, header, label, deep, on_step, elmlib, dtclib)
                if found:
                    merge_module(prof, header, found)
                    on_module(header, found)
        finally:
            el.close()
        save_profile(prof)
        return prof
    finally:
        connect.release_port()


def summary(prof=None):
    """A plain-language account of what is known, for the UI."""
    prof = prof or load_profile()
    mods = prof.get("modules") or {}
    codes = set()
    for m in mods.values():
        codes.update(m.get("dtc_catalogue") or [])
    return {
        "passes": prof.get("passes", 0),
        "learned_at": prof.get("learned_at"),
        "modules": len(mods),
        "uds_modules": sum(1 for m in mods.values() if "0x22" in (m.get("services") or [])),
        "dtc_modules": sum(1 for m in mods.values() if "0x19" in (m.get("services") or [])),
        "catalogue_size": len(codes),
        "detail": [
            {"header": h, "label": m.get("label"), "services": m.get("services") or [],
             "ident": m.get("ident") or {},
             "catalogue": len(m.get("dtc_catalogue") or []),
             "coarse_hits": m.get("coarse_hits") or []}
            for h, m in sorted(mods.items())
        ],
    }


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar learn", add_help=True)
    ap.add_argument("--deep", action="store_true",
                    help="also probe coarsely for manufacturer data (slower)")
    ap.add_argument("--show", action="store_true",
                    help="print what is already known and exit")
    args = ap.parse_args(argv)

    BOLD, DIM, RESET, GREEN = "\033[1m", "\033[2m", "\033[0m", "\033[32m"

    if args.show:
        s = summary()
        print(f"\n  {BOLD}What OmaCar knows about this car{RESET}")
        print(f"  {s['modules']} module(s) · {s['uds_modules']} speak UDS · "
              f"{s['catalogue_size']} fault codes catalogued · {s['passes']} pass(es)\n")
        for d in s["detail"]:
            print(f"  {BOLD}{d['header']}{RESET}  {d['label'] or ''}")
            print(f"    services  {', '.join(d['services']) or '—'}")
            for k, v in (d["ident"] or {}).items():
                print(f"    {k:<22} {v}")
            if d["catalogue"]:
                print(f"    fault catalogue        {d['catalogue']} codes")
            if d["coarse_hits"]:
                print(f"    coarse hits            {', '.join(d['coarse_hits'])}")
            print()
        return 0

    print(f"\n  {BOLD}Learning this car{RESET}  {DIM}read-only{RESET}\n")
    last = [""]

    def step(header, what):
        if header != last[0]:
            print(f"\n  {header}", end="", flush=True)
            last[0] = header
        print(".", end="", flush=True)

    def module(header, found):
        svc = ", ".join(found.get("services") or [])
        print(f"  {GREEN}found{RESET} {found.get('label') or ''} [{svc}]", end="", flush=True)

    try:
        learn(deep=args.deep, on_step=step, on_module=module)
    except RuntimeError as e:
        print(f"\n\n  cannot learn: {e}\n")
        return 1
    s = summary()
    print(f"\n\n  {s['modules']} module(s) found · {s['catalogue_size']} fault "
          f"codes catalogued.")
    print(f"  {DIM}saved to {profile_path()}{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
