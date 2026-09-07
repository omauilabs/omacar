"""A functional test that reaches the car.

THE BUTTON USED TO COMMAND A FILE.

The Tests lab wrote a command to command.json and the simulator read it. On a
real car nothing read it: the button ran, the trace stayed flat, and the screen
said so in a warning card -- which was honest, and was also the tool refusing
to do the one thing a technician opens that screen for.

Actuator control is UDS service 0x2F, InputOutputControlByIdentifier: a
request naming the identifier of the thing to move, a control option, and the
state to put it in. The identifiers are manufacturer-specific and nobody
publishes them, which is exactly the shape of problem the profile format
exists for. So an actuator is an entry in a profile, it climbs the same
confidence ladder as any other identifier, and ONLY a validated one reaches a
button. A test the profile has no validated entry for is refused before
anything is opened, with a sentence that says why, and the screen shows that
sentence next to the button instead of a button that does nothing.

WHAT IS FIXED HERE AND CANNOT COME FROM A FILE.

The service byte. This module composes every request it sends as
0x2F + identifier + state, from an entry that carries the identifier and the
state as bytes and never a request string. A profile somebody downloaded
therefore cannot smuggle a 0x2E, a 0x31 or anything else through the actuator
table however it is written: the only service that leaves here is the one the
table is for. The optional session request is constrained the same way to
0x10. Everything else the gates already enforce still applies -- the write
arm, the motion check, the voltage floor and the tier -- because the bytes go
out through Elm.request() like every other write.

RELEASE IS UNCONDITIONAL.

Whatever happens after the command is accepted -- the deadline, a stop from
the screen, an exception, a lost adapter -- the release request goes out in a
finally: block and the port goes back to the daemon. A fan commanded on and
forgotten is a flat battery; an injector held off is a converter full of fuel.
If the release itself fails, that is recorded as loudly as the command was,
and the module's own session timeout ends the override a few seconds later
with no tester talking to it, which is the standard's backstop and ours.

THE TRACE STAYS LIVE.

The daemon lends the port for the test, so it cannot read the car while the
test runs. This module reads instead: a handful of mode-01 values every few
hundred milliseconds through the same connection, published to a sidecar the
daemon overlays onto its live snapshot. The screen shows the fan's effect on
load and current, which is the whole point of commanding it.
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import connect                                                # noqa: E402
import records                                                # noqa: E402

STOP = os.path.join(connect.STATE, "actuate-stop")
BORROWED = os.path.join(connect.STATE, "borrowed.json")

# Fresh means the daemon may overlay it; older than this and the test is over
# or its process is gone, and the daemon's own last readings are the truth.
BORROWED_FRESH = 3.0

HEXCHARS = set("0123456789ABCDEF")

# The mode-01 values worth watching during a test, and how to turn the bytes
# into numbers. These are the SAE J1979 formulas; nothing manufacturer-
# specific is decoded here.
WATCH = (
    ("RPM",              "0C", lambda a, b: (a * 256 + b) / 4.0),
    ("ENGINE_LOAD",      "04", lambda a, b: a * 100.0 / 255.0),
    ("COOLANT_TEMP",     "05", lambda a, b: a - 40.0),
    ("SHORT_FUEL_TRIM_1", "06", lambda a, b: (a - 128.0) * 100.0 / 128.0),
    ("THROTTLE_POS",     "11", lambda a, b: a * 100.0 / 255.0),
)


class Unreachable(Exception):
    """This test has no validated identifier on this car. Nothing was sent."""


# --------------------------------------------------------------- the entries
def _hex(s):
    return str(s or "").replace(" ", "").upper()


def _is_hex(s, length=None):
    """Whole bytes: an even number of hex digits, optionally exactly `length`."""
    s = _hex(s)
    if not s or len(s) % 2 or not set(s) <= HEXCHARS:
        return False
    return length is None or len(s) == length


def _is_header(s):
    """A CAN address is 3 digits (11-bit) or 8 (29-bit); pre-CAN headers are
    6. Not whole bytes, so not _is_hex."""
    s = _hex(s)
    return bool(s) and set(s) <= HEXCHARS and len(s) in (3, 6, 8)


def entries(doc):
    """The actuator entries allowed to drive a button, and nothing else.

    Checked here rather than trusted: confidence must be exactly `validated`
    with something named as what it was validated against; the identifier is
    two bytes; the states are bytes; a session, if any, is a 0x10 request.
    """
    out = []
    for a in (doc or {}).get("actuator") or []:
        if a.get("confidence") != "validated":
            continue
        prov = a.get("provenance") or {}
        if not prov.get("validated_against"):
            continue
        test = str(a.get("test") or "").strip()
        header = _hex(a.get("header"))
        did = _hex(a.get("did"))
        on = _hex(a.get("on"))
        off = _hex(a.get("off") or "00")
        session = _hex(a.get("session"))
        if not (test and _is_header(header) and _is_hex(did, 4)
                and _is_hex(on) and _is_hex(off)):
            continue
        if session and not (session.startswith("10") and _is_hex(session, 4)):
            continue
        out.append({
            "test": test,
            "name": a.get("name") or test,
            "header": header,
            "did": did,
            "on": on,
            "off": off,
            "session": session,
            "provenance": {k: prov.get(k) for k in
                           ("validated_by", "validated_on", "validated_against",
                            "found_on", "url", "source_kind") if prov.get(k)},
        })
    return out


def reach(doc):
    """What the app needs per test: that it reaches, and on whose word.

    No bytes. The browser does not send requests; it shows a sentence.
    """
    out = {}
    for e in entries(doc):
        out[e["test"]] = {
            "did": e["did"],
            "header": e["header"],
            "session": bool(e["session"]),
            "provenance": e["provenance"],
        }
    return out


def profile_doc():
    """The profile for the car in the garage right now, or None.

    The garage key is the VIN that chose the open database, which is the
    same source the daemon uses for its learned readings -- see the note in
    records._signal_catalogue for why the two must not differ.
    """
    import garage
    import profile as profilelib
    key = garage.current()
    if key in (garage.SIM_KEY, "unknown"):
        return None
    slug = profilelib.for_vin(key)
    if not slug:
        return None
    doc, _ = profilelib.load(slug)
    return doc


def for_test(test, doc=None):
    doc = profile_doc() if doc is None else doc
    for e in entries(doc):
        if e["test"] == test:
            return e
    return None


# ------------------------------------------------------------------ running
_ACTIVE = {"thread": None, "test": None, "result": None}
_LOCK = threading.Lock()


def running():
    t = _ACTIVE["thread"]
    return _ACTIVE["test"] if (t is not None and t.is_alive()) else None


def stop():
    """Ask the running test to release now. Safe when nothing is running."""
    try:
        with open(STOP, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _stopped():
    return os.path.exists(STOP)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _publish(test, values, deadline, direct=False):
    """The sidecar the daemon overlays -- and live.json itself when there is
    no daemon to do the overlaying, so a CLI-only machine still sees the
    trace."""
    payload = {"t": time.time(), "status": "test", "test": test,
               "values": values, "until": deadline}
    for path in ((BORROWED, records.LIVE) if direct else (BORROWED,)):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload if path == BORROWED else dict(
                    payload, connected=True, handover=True,
                    note="a functional test is using the adapter"), f)
            os.replace(tmp, path)
        except OSError:
            pass


def observe(el):
    """One reading of the values worth watching, through the borrowed link.

    Reads only, on the module already aimed at. A value that fails to parse
    is None rather than a guess, the same rule the daemon applies.
    """
    import elm as elmlib
    out = {}
    for name, pid, fn in WATCH:
        req = "01" + pid
        try:
            lines = el.request(req)
            kind, _detail, _data = elmlib.classify(lines, 0x01, req)
            if kind != "positive":
                out[name] = None
                continue
            body = _hex(el.payload(lines, req))
            i = body.find("41" + pid)
            if i < 0 or len(body) < i + 6:
                out[name] = None
                continue
            a = int(body[i + 4:i + 6], 16)
            b = int(body[i + 6:i + 8], 16) if len(body) >= i + 8 else 0
            out[name] = round(fn(a, b), 2)
        except Exception:                                     # noqa: BLE001
            out[name] = None
    try:
        import dtc as dtclib
        out["VOLTAGE"] = dtclib.battery_volts(el)
    except Exception:                                         # noqa: BLE001
        out["VOLTAGE"] = None
    return out


def begin(test, seconds, entry, who="the app"):
    """Borrow the port, pass the gates, send the command, and hand the
    observing and the release to a thread. Returns what the module said.

    Everything that can refuse happens HERE, synchronously, so the screen
    gets the refusal as the answer to its request rather than a flat trace:
    the daemon holding the port, the arm, the motion check, the voltage
    floor, an invalid address, a refused session, a refused command.
    """
    import elm as elmlib
    import ops

    with _LOCK:
        if running():
            raise ops.Refused(f"a test is already running: {running()}")
        port, _kind = connect.resolve()
        if not port:
            raise ops.Refused("no adapter")
        if not connect.request_port(port):
            raise ops.Refused("the daemon is holding the port")
        _remove(STOP)
        el = None
        try:
            el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
            el.init()
            volts = ops.preflight(el)
            if not elmlib.aim(el, entry["header"]):
                raise ops.Refused(f"{entry['header']} is not a valid address "
                                  f"on this protocol")
            if entry["session"]:
                lines = el.request(entry["session"])
                kind, detail, _ = elmlib.classify(lines, 0x10, entry["session"])
                if kind != "positive":
                    raise ops.Refused(f"the module refused the diagnostic "
                                      f"session: {detail or kind}")
            req = "2F" + entry["did"] + entry["on"]
            lines = el.request(req)
            kind, detail, data = elmlib.classify(lines, 0x2F, req)
            if kind != "positive":
                raise ops.Refused(f"the module refused the command: "
                                  f"{detail or kind}")
        except BaseException:
            if el is not None:
                try:
                    el.close()
                except Exception:                             # noqa: BLE001
                    pass
            connect.release_port()
            raise

        deadline = time.time() + float(seconds)
        result = {"test": test, "did": entry["did"], "header": entry["header"],
                  "sent": req, "reply": data or detail, "volts": volts,
                  "seconds": seconds, "started": time.time(),
                  "samples": 0, "released": None, "who": who}
        _ACTIVE.update({"test": test, "result": result})
        th = threading.Thread(target=_hold, args=(el, entry, deadline, result),
                              daemon=True, name=f"actuate:{test}")
        _ACTIVE["thread"] = th
        th.start()
        return dict(result)


def _hold(el, entry, deadline, result):
    """Observe until the deadline or a stop, then release, whatever happened."""
    import elm as elmlib
    direct = not connect.daemon_running()
    try:
        while time.time() < deadline and not _stopped():
            values = observe(el)
            result["samples"] += 1
            _publish(result["test"], values, deadline, direct=direct)
            time.sleep(0.35)
    except Exception as e:                                    # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        rel = "2F" + entry["did"] + entry["off"]
        try:
            lines = el.request(rel)
            kind, detail, _ = elmlib.classify(lines, 0x2F, rel)
            result["released"] = kind
            result["release_reply"] = detail
        except Exception as e:                                # noqa: BLE001
            result["released"] = "failed"
            result["release_reply"] = f"{type(e).__name__}: {e}"
        try:
            el.close()
        except Exception:                                     # noqa: BLE001
            pass
        _remove(BORROWED)
        _remove(STOP)
        connect.release_port()
        result["ended"] = time.time()
        try:
            records.write_record(
                "test", f"actuator: {result['test']}",
                {k: result.get(k) for k in
                 ("test", "did", "header", "sent", "reply", "seconds",
                  "samples", "released", "release_reply", "error", "volts")})
        except Exception:                                     # noqa: BLE001
            pass


def last():
    return dict(_ACTIVE["result"]) if _ACTIVE["result"] else None
