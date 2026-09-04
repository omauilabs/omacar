"""Operations that change something: clearing codes, and service resets.

WHY ROUTINES ARE NEVER DISCOVERED BY SWEEPING.

`prospect` finds unknown data identifiers by asking for all of them and seeing
which answer. That is safe because service 0x22 reads: an identifier that does
not exist replies `requestOutOfRange` and nothing happens.

Service 0x31 is routine control, and the identical technique would be
catastrophic. A routine is not a value you read, it is a procedure the module
runs -- and the routine you just guessed the number of might spin a radiator
fan, cycle an ABS pump, retract a parking brake with a wheel off, or run the
engine to a target speed. There is no `requestOutOfRange` equivalent for
"started the wrong procedure on a car with somebody's hands in it".

So routine definitions come from documentation and from the community, never
from a sweep. This module will not brute-force them and no flag enables it.

WHAT AN HONEST RESET DEFINITION LOOKS LIKE.

Almost every service reset is manufacturer-specific: the same oil-life reset is
a different routine identifier on a Honda and a BMW. So each definition carries
which vehicles it is known to work on and how confident we are, and the UI shows
that before it shows a button. A reset presented with more confidence than it
has earned is how somebody ends up with a parking brake retracted at speed.
"""

import json
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import write as writelib  # noqa: E402

# Community routine definitions live outside the code so they can be shared,
# reviewed and versioned without a release.
DEFS = os.path.join(connect.STATE, "resets.json")
BUNDLED = os.path.join(os.path.dirname(__file__), "..", "share", "data", "resets.json")


class Refused(Exception):
    """A guard said no. The message is meant to be shown verbatim."""


def preflight(el, moving_check=True):
    """Everything that must be true before anything is written.

    Order matters: check the cheapest and most dangerous conditions first, so a
    car that is moving is refused before we spend three seconds reading voltage.
    """
    if not writelib.is_armed():
        raise Refused("write mode is not armed — run: omacar write arm")

    if moving_check:
        try:
            import prospect
            mv = prospect.moving(el)
        except Exception:                                      # noqa: BLE001
            mv = None
        if mv:
            raise Refused("the car reports road speed. Stop the vehicle first.")
        if mv is None:
            raise Refused("road speed could not be read, so I cannot confirm the "
                          "car is stationary. Writes need that confirmation.")

    import dtc as dtclib
    v = dtclib.battery_volts(el)
    if v is not None and v < writelib.WRITE_VOLTS:
        raise Refused(f"battery is at {v:.1f} V. Writing below "
                      f"{writelib.WRITE_VOLTS} V risks leaving a module holding "
                      f"half a change. Start the engine or charge it.")
    return v


def clear_codes(el, headers=None, on_step=None):
    """Clear fault codes, generically and per-module.

    Two passes, because they are two different things. Mode 04 is the
    emissions clear every car understands and is what a parts-shop reader does.
    UDS 0x14 clears a specific module's own codes, including the
    manufacturer-specific ones mode 04 never touches -- the hybrid faults on
    this car live there and would survive a mode-04 clear entirely.
    """
    on_step = on_step or (lambda *a: None)
    import elm as elmlib
    import protocols
    results = {"generic": None, "modules": {}}

    # 1. Generic emissions clear, broadcast.
    #
    # The broadcast address was the literal "7DF" and this line sat OUTSIDE
    # the try below, so on a 29-bit car -- which is what the development
    # vehicle is -- set_header raised straight out of the function and
    # /api/clear answered 500 with a transport exception. Ask protocols for
    # the address, and let a refusal be a recorded result like any other,
    # because "we did not send it" is a fact the caller has to see: api.py
    # decides whether to clear our own stored faults from these kinds, and it
    # must never do that on the strength of a request that never left.
    broadcast = protocols.broadcast(getattr(el, "protocol", None))
    on_step("generic", "mode 04")
    if not elmlib.aim(el, broadcast):
        results["generic"] = {"kind": "skipped",
                              "detail": f"{broadcast} is not a valid address "
                                        f"on this protocol"}
    else:
        try:
            lines = el.request("04")
            kind, detail, _ = elmlib.classify(lines, 0x04, "04")
            results["generic"] = {"kind": kind, "detail": detail}
        except Exception as e:                                 # noqa: BLE001
            results["generic"] = {"kind": "error", "detail": str(e)[:120]}

    # 2. Per-module UDS clear. FFFFFF is "all groups of DTCs".
    for header in (headers or []):
        on_step(header, "0x14")
        if not elmlib.aim(el, header):
            # Skipping is the whole point: with the header unchanged, 14FFFFFF
            # would go to whichever module was addressed last and clear ITS
            # codes under this one's name.
            results["modules"][header] = {
                "kind": "skipped",
                "detail": "not a valid address on this protocol"}
            continue
        try:
            lines = el.request("14FFFFFF")
            kind, detail, _ = elmlib.classify(lines, 0x14, "14FFFFFF")
            results["modules"][header] = {"kind": kind, "detail": detail}
        except Exception as e:                                 # noqa: BLE001
            results["modules"][header] = {"kind": "error", "detail": str(e)[:120]}
    return results


# ---------------------------------------------------------------- resets

def load_resets():
    """Bundled definitions, overlaid with anything the user has added."""
    out = {}
    paths = [BUNDLED, DEFS]
    try:
        import plugins
        paths[1:1] = plugins.data_files("resets")
    except Exception:                                         # noqa: BLE001
        pass
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for r in data.get("resets", []):
            if r.get("id"):
                out[r["id"]] = r
    return out


PROCEDURES = os.path.join(os.path.dirname(__file__), "..", "share", "data",
                          "procedures.json")
USER_PROCEDURES = os.path.join(connect.STATE, "procedures.json")


def load_procedures(make=None):
    """Owner-performed procedures, filtered to this make.

    Nothing here is sent to the car, so this carries none of the guards the
    reset path does -- these are instructions a person follows with the
    ignition key and the buttons on their own dashboard. A wrong entry wastes
    somebody's afternoon; it does not command an actuator.

    Universal entries (empty `makes`) always show, because the diagnostic
    sequences are not make-specific.
    """
    out = []
    paths = [PROCEDURES, USER_PROCEDURES]
    try:
        import plugins
        paths[1:1] = plugins.data_files("procedures")
    except Exception:                                         # noqa: BLE001
        pass
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for p in data.get("procedures", []):
            makes = [m.lower() for m in (p.get("makes") or [])]
            if not makes or (make and make.lower() in makes):
                out.append(p)
    return sorted(out, key=lambda p: (p.get("category") or "", p.get("name") or ""))


def applicable(resets, vin=None, make=None):
    """Which definitions plausibly apply to this car.

    A definition with no `makes` is universal. One that names makes only shows
    for those, because offering a Honda oil reset on a BMW is how somebody
    sends a routine to a module that does something else entirely with that ID.
    """
    out = []
    for r in resets.values():
        makes = [m.lower() for m in (r.get("makes") or [])]
        if not makes or (make and make.lower() in makes):
            out.append(r)
    return sorted(out, key=lambda r: (r.get("category") or "", r.get("name") or ""))


def run_reset(el, spec, on_step=None):
    """Send one reset definition. Every guard applies."""
    on_step = on_step or (lambda *a: None)
    import elm as elmlib

    header = spec.get("header")
    if not header:
        raise Refused("this definition has no module address")
    requests = spec.get("requests") or []
    if not requests:
        raise Refused("this definition has no requests")

    # A reset definition names its own module address, and an address of the
    # wrong shape for this car's protocol is a fact about the definition, not
    # a transport failure. Refused is what the UI knows how to show, and it
    # says which vehicle the definition was written for.
    try:
        el.set_header(header)
    except ValueError as why:
        raise Refused(f"this definition addresses {header}, which is not a "
                      f"valid address on the protocol this car negotiated. "
                      f"({why})")
    out = []
    for i, req in enumerate(requests):
        req = req.replace(" ", "").upper()
        service = int(req[:2], 16)
        if service not in writelib.WRITE_SERVICES and service not in elmlib.READ_ONLY_SERVICES:
            raise Refused(f"definition uses service 0x{service:02X}, which this "
                          f"tool does not implement")
        on_step(header, req)
        lines = el.request(req)
        kind, detail, data = elmlib.classify(lines, service, req)
        out.append({"request": req, "kind": kind, "detail": detail, "data": data})
        if kind == "negative":
            # Stop on the first refusal. Continuing a multi-step routine after
            # the module has rejected a step is how a half-applied calibration
            # happens.
            break
        # Routines often need a moment between steps; the definition says how
        # long rather than this guessing.
        delay = spec.get("delay_between") or 0.2
        if i < len(requests) - 1:
            time.sleep(float(delay))
    return out
