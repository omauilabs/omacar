#!/usr/bin/env python3
"""The guards, as an executable specification.

Every assertion here is a defect that was live in this tree on 2026-09-07 and
is fixed. They are collected in one file because they share a shape: each one
was a check that existed, looked right, and did not hold — a floor that passed
when it could not measure, a validator that approved what it could not parse, a
gate with a door beside it. A comment saying "this is enforced" is not a test,
and every one of these had a comment.

Needs no car, no adapter and no network. Runs under any python3.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

# pyserial lives in the venv, and none of these assertions open a port. Stub it
# so the guards can be checked on any machine, including CI with no venv built:
# a safety test that only runs where the environment is perfect is a safety test
# that stops running.
if "serial" not in sys.modules:
    import types

    _serial = types.ModuleType("serial")

    class _Serial:                                   # pragma: no cover
        def __init__(self, *a, **k):
            raise RuntimeError("the guard tests never open a port")

    _serial.Serial = _Serial
    _serial.SerialException = type("SerialException", (Exception,), {})
    _tools = types.ModuleType("serial.tools")
    _lp = types.ModuleType("serial.tools.list_ports")
    _lp.comports = lambda: []
    _tools.list_ports = _lp
    _serial.tools = _tools
    sys.modules["serial"] = _serial
    sys.modules["serial.tools"] = _tools
    sys.modules["serial.tools.list_ports"] = _lp

fails = 0
section = ""


def head(t):
    global section
    section = t
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


# --------------------------------------------------------------- the transport
head("raw() is not a way to reach the car")

import elm  # noqa: E402


class FakePort:
    """Records what would have gone on the wire."""

    def __init__(self):
        self.sent = []

    def write(self, b):
        self.sent.append(b)

    def read(self, *a, **k):
        return b">"

    def reset_input_buffer(self):
        pass

    @property
    def in_waiting(self):
        return 0


def fake_elm():
    e = elm.Elm.__new__(elm.Elm)
    e.ser = FakePort()
    e.protocol = "7"
    e.log = None
    return e


e = fake_elm()
sent = []
e._send = lambda line, **kw: sent.append(line) or []

for line in ("ATRV", "ATZ", "STI", "sti"):
    sent.clear()
    try:
        e.raw(line)
        ok(f"raw({line!r}) is allowed — it is an adapter command")
    except Exception as why:  # noqa: BLE001
        bad(f"raw({line!r}) should be allowed: {why}")

for line in ("22F190", "190A", "2EF190", "3401"):
    sent.clear()
    try:
        e.raw(line)
        bad(f"raw({line!r}) reached the wire — the gate is bypassable")
    except elm.WriteAttempted:
        ok(f"raw({line!r}) refused — a service byte must go through request()")

check("nothing was transmitted by the refused calls", sent, [])

head("request() can do what made callers bypass it")
got = {}
e._send = lambda line, **kw: got.update(line=line, **kw) or []
e.request("190A", patient=True, timeout=8.0)
check("patient is forwarded", got.get("patient"), True)
check("timeout is forwarded", got.get("timeout"), 8.0)

head("the reprogramming trio is refused however armed")
for svc in ("34", "36", "37"):
    try:
        e.request(svc + "0044")
        bad(f"service 0x{svc} was accepted")
    except elm.WriteAttempted:
        ok(f"service 0x{svc} refused by construction")

# ------------------------------------------------------------------ the floors
head("the voltage floor fails closed")

import ops  # noqa: E402
import write as writelib  # noqa: E402


class NoVolts:
    """An adapter whose ATRV says nothing — the common case on a car and an
    adapter this project has never met, which is every car but one."""

    protocol = "7"

    def raw(self, *a, **k):
        return []

    def request(self, *a, **k):
        return []

    def set_header(self, *a, **k):
        pass


import dtc as dtclib  # noqa: E402

_real_volts = dtclib.battery_volts
_real_moving = None
try:
    import prospect

    _real_moving = prospect.moving
    prospect.moving = lambda el: False          # stationary, so speed is not the refusal
except Exception:  # noqa: BLE001
    pass

dtclib.battery_volts = lambda el: None
writelib.arm(60)
try:
    ops.preflight(NoVolts())
    bad("an unreadable voltage passed the floor")
except ops.Refused as why:
    if "voltage" in str(why).lower():
        ok("an unreadable voltage is refused, like an unreadable road speed")
    else:
        bad(f"refused, but for the wrong reason: {why}")
except Exception as why:  # noqa: BLE001
    bad(f"raised something other than Refused: {why!r}")

dtclib.battery_volts = lambda el: 12.8
try:
    ops.preflight(NoVolts())
    ok("a healthy 12.8 V still passes")
except Exception as why:  # noqa: BLE001
    bad(f"12.8 V was refused: {why}")

dtclib.battery_volts = _real_volts
writelib.disarm()
if _real_moving is not None:
    prospect.moving = _real_moving

# --------------------------------------------------------------- the addresses
head("a header must name a tester, or it is somebody's live traffic")

import protocols  # noqa: E402

TABLE = next(v for v in vars(protocols).values()
             if isinstance(v, dict) and v
             and all(isinstance(x, dict) and "name" in x for x in v.values()))

for dpn, entry in sorted(TABLE.items()):
    hdr = entry.get("default_header", "")
    passed, why = protocols.header_ok(dpn, hdr)
    if passed:
        ok(f"{entry['name']} accepts its own header {hdr!r}")
    else:
        bad(f"{entry['name']} refuses its own header {hdr!r}: {why}")
    passed, why = protocols.header_ok(dpn, protocols.broadcast(dpn))
    if not passed:
        bad(f"{entry['name']} refuses its own broadcast address: {why}")

for dpn, hdr, what in (
    ("6", "123", "an 11-bit engine-control id"),
    ("7", "18FF1234", "a 29-bit id naming no tester"),
    ("3", "686A22", "a pre-CAN header with no tester byte"),
    (None, "0C9", "a live id on an unknown protocol"),
    ("6", "ZZZ", "not hex at all"),
):
    passed, _ = protocols.header_ok(dpn, hdr)
    check(f"{what} is refused", passed, False)

# ---------------------------------------------------------------- the profiles
head("a downloaded profile may only ask for reads")

import profile as profilelib  # noqa: E402


def one(request):
    return {"schema": profilelib.SCHEMA,
            "car": {"slug": "x", "make": "Honda", "model": "CR-Z"},
            "pid": [{"id": "e", "header": "18DA10F1", "request": request,
                     "confidence": "candidate",
                     "provenance": {"found_on": "a 2015 CR-Z"}}]}


def refuses(request):
    return any("not a read" in p for p in profilelib.problems(one(request)))


for req, what in (("22F190", "a data read"), ("190A", "a fault catalogue"),
                  ("0100", "a mode 01 PID")):
    check(f"{what} ({req}) is allowed", refuses(req), False)

for req, what in (("2EF190", "write by identifier"), ("3101FF00", "a routine"),
                  ("2701", "security access"), ("340044", "reprogramming"),
                  ("04", "clearing codes")):
    check(f"{what} ({req}) is refused", refuses(req), True)

# ------------------------------------------------------------------ the server
head("a page you did not open cannot clear your codes")

import serve  # noqa: E402


class FakeReq:
    """Only what _same_origin reads."""

    def __init__(self, headers):
        self.headers = headers


def same_origin(headers):
    return serve.Handler._same_origin(FakeReq(headers))


for headers, want, what in (
    ({"Sec-Fetch-Site": "cross-site"}, False, "a cross-site POST"),
    ({"Sec-Fetch-Site": "same-origin"}, True, "the app's own POST"),
    ({"Sec-Fetch-Site": "none"}, True, "a POST from the address bar"),
    ({"Origin": "https://example.com"}, False, "an old browser, foreign origin"),
    ({"Origin": "http://127.0.0.1:7560"}, True, "an old browser, loopback origin"),
    ({}, True, "curl and the CLI, which are not browsers"),
):
    check(f"{what}", same_origin(headers), want)

# ------------------------------------------------------------------ the advisor
head("the advisor names the model that actually answered")

import ai  # noqa: E402

envelope = {"modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 12},
                           "claude-sonnet-5": {"outputTokens": 1840}}}
check("the model asked for wins over the background title model",
      ai._answering_model(envelope, "claude-sonnet-5"), "claude-sonnet-5")
check("with nothing asked for, the model that did the work wins",
      ai._answering_model(envelope, None), "claude-sonnet-5")
check("an empty envelope falls back to what was asked for",
      ai._answering_model({}, "claude-sonnet-5"), "claude-sonnet-5")

# ----------------------------------------------------------------------- done
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every guard holds\n")
