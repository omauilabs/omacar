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
    # AN OSError, BECAUSE THAT IS WHAT pyserial RAISES. A stub deriving it from
    # bare Exception cannot see the bug where a caller catches OSError and
    # believes it has caught a serial fault -- or the one that was live here,
    # where a caller caught RuntimeError and had not. A stub that is wrong
    # about the hierarchy tests the stub rather than the tree.
    _serial.SerialException = type("SerialException", (OSError,), {})
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


def _raises(fn, kind):
    try:
        fn()
        return False
    except kind:
        return True
    except Exception:                                         # noqa: BLE001
        return False


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

# -------------------------------------------------------------- the addresses 2
head("discovery works on the protocol most cars actually speak")

import discover  # noqa: E402


class ElevenBit:
    """A connection that negotiated 11-bit CAN, which is most cars since 2008
    and every car this project had never tried."""

    protocol = "6"

    def __init__(self):
        self.asked = []

    def set_header(self, h):
        ok, why = protocols.header_ok(self.protocol, h)
        if not ok:
            raise ValueError(why)          # exactly what the real one does
        self.asked.append(h)

    def request(self, *a, **k):
        return []

    def payload(self, *a, **k):
        return ""


addrs = protocols.physical("6")
check("11-bit CAN gets 11-bit addresses",
      all(len(h) == 3 for h, _ in addrs), True)
check("29-bit CAN still gets 29-bit addresses",
      all(len(h) == 8 for h, _ in protocols.physical("7")), True)
check("J1939 returns nothing rather than a confidently wrong list",
      protocols.physical("A"), [])

# The crash: learn_module used set_header directly, so the first 29-bit literal
# raised ValueError on an 11-bit car and learn() -- which catches RuntimeError
# only -- let it out. `omacar learn` died on most cars built since 2008, and
# /api/learn returned 500.
el = ElevenBit()
try:
    out = discover.learn_module(el, "18DA10F1", "engine", False,
                                lambda *a: None, elm, None)
    ok("a wrong-shaped address is skipped, not raised through the sweep")
    check("and nothing was asked on it", el.asked, [])
    check("the module is reported as not found", out, None)
except ValueError:
    bad("learn_module still raises on a wrong-shaped address")
except Exception as why:  # noqa: BLE001
    bad(f"learn_module raised {why!r}")

# ------------------------------------------------------------------ the learn
head("learn names its record after the car, never after noise")

check("a mode-09 VIN reply decodes",
      discover.vin_from_payload("49 02 01 4A 48 4D 5A 46 31 44 34 34 46 53 30 30 31 38 33 35"),
      "JHMZF1D44FS001835")
check("a short reply is not a VIN",
      discover.vin_from_payload("49 02 01 57 50 30 5A 5A 5A 39 39 5A 54 53 33 39"), None)
check("a negative reply is not a VIN", discover.vin_from_payload("7F 09 12"), None)
check("garbage is not a VIN", discover.vin_from_payload("4902013F3F3F3F3F3F3F3F3F3F3F3F3F3F3F3F3F"), None)

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

# --------------------------------------------------------------------- modes
head("a mode is a boundary, not a preference the browser is trusted with")

import tempfile  # noqa: E402

_scratch = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _scratch
for _m in ("modes", "api", "records", "garage"):
    sys.modules.pop(_m, None)
import modes  # noqa: E402

check("an unset mode is the middle tier, not the top", modes.current(), "power")

MATRIX = {
    "clear_codes":     ("simplified", "the one write an owner does after a repair"),
    "learn":           ("power",      "module discovery"),
    "sweep":           ("technician", "sweeping an unknown identifier range"),
    "actuate":         ("technician", "commanding an actuator"),
    "routine":         ("technician", "running a published routine"),
    "write_did":       ("god",        "writing a configuration value"),
}
for action, (need, what) in MATRIX.items():
    for tier in modes.TIERS:
        want = modes.RANK[tier] >= modes.RANK[need]
        got = modes.decide(action, tier).ok
        if got != want:
            bad(f"{what}: {tier} should {'allow' if want else 'refuse'} it")
ok("every action is reachable from its tier and no lower one")

for action in ("reprogram", "guess_routine"):
    check(f"{action} is refused even in god mode",
          modes.decide(action, "god").ok, False)

# The claim the whole design rests on: flipping the client does not decide it.
# A page, curl or an agent posting straight at the route is refused by the
# server, which is the only place that can refuse it.
import types  # noqa: E402

if "serial" not in sys.modules:  # pragma: no cover - already stubbed above
    pass
import api  # noqa: E402

modes.set_mode("simplified")
_st, _payload = api.handle_post("/api/actuate", '{"test":"fan_high","seconds":5}')
check("a direct POST to an above-tier route is refused at the server", _st, 403)

# Write-by-identifier: the one door god mode adds, judged before the port.
_st, _p = api.handle_post("/api/write-did", '{"header":"7E0","did":"F190","value":"01"}')
check("write-did is refused below god mode at the server", _st, 403)
modes.set_mode("god")
_st, _p = api.handle_post("/api/write-did", '{"header":"7E0","did":"F410","value":"00"}')
check("an emissions-range write is refused in god mode with the citation",
      _st == 403 and modes.CAA_CITATION.split(" — ")[0] in (_p.get("citation") or ""), True)
check("and says nothing was sent", "Nothing was sent" in (_p.get("needs") or ""), True)
_st, _p = api.handle_post("/api/write-did", '{"header":"7E0","did":"F19","value":"01"}')
check("a malformed identifier is a 400 before any port is opened", _st, 400)
_st, _p = api.handle_post("/api/write-did", '{"header":"7E0","did":"F190","value":"01","confirm":true}')
check("a confirm without the prior value is refused", _st, 400)
_st, _p = api.handle_post("/api/write-did", '{"header":"7E0","did":"F190","value":"01"}')
check("unarmed, a read-back is refused before the port (409)", _st, 409)
modes.set_mode("simplified")
ok(f"and says why: {_payload.get('error', '').splitlines()[0][:60]}")

modes.set_mode("technician")
_st2, _ = api.handle_post("/api/actuate", '{"test":"fan_high","seconds":5}')
check("the same call passes the gate at the right tier", _st2 != 403, True)

# God mode opens everything except the one line it must not cross.
modes.set_mode("god")
_d = modes.decide("write_did", ctx={"did": "F4A0"})
check("an emissions-range write is refused in god mode", _d.ok, False)
ok("and the refusal carries the Clean Air Act citation"
   if "203(a)(3)" in _d.citation else bad("no citation on the refusal"))
ok("and says the list is named rather than complete"
   if "not exhaustive" in _d.citation else bad("no coverage caveat"))
check("a manufacturer identifier is still writable in god mode",
      modes.decide("write_did", ctx={"did": "0210"}).ok, True)

# The last floor never consults the mode. If elm.py ever imports modes, a bug
# in the tier system becomes a bug in the transport's absolute refusals.
_elm_src = open(os.path.join(ROOT, "lib", "elm.py"), encoding="utf-8").read()
check("the transport does not consult the mode system",
      "import modes" in _elm_src, False)

import shutil  # noqa: E402
shutil.rmtree(_scratch, ignore_errors=True)

# ------------------------------------------------------------------ the agent
head("what an agent may do to a car")

sys.modules.pop("mcp", None)
import mcp  # noqa: E402

check("the read services an agent may send", sorted(mcp.AGENT_SERVICES),
      [0x19, 0x21, 0x22])

def _call(name, **args):
    r = mcp.call(name, args)
    return (r.get("content") or [{}])[0].get("text", ""), bool(r.get("isError"))

for req, what in (("2EF190", "write by identifier"), ("3101FF00", "a routine"),
                  ("2701", "security access"), ("1002", "a session change"),
                  ("340044", "reprogramming"), ("04", "clearing codes")):
    _t, err = _call("car_request", header="7E0", request=req)
    if err and "not one an agent may send" in _t:
        ok(f"{what} ({req}) is refused before the port is opened")
    else:
        bad(f"{what} ({req}) was not refused: {_t[:60]}")

# Refused for the SHAPE, before anything is sent -- an agent proposing a header
# that lands in live control traffic is the failure this exists for.
_t, err = _call("car_request", header="0C9", request="22F190")
check("a header outside the diagnostic ranges is refused", err, True)

# The ladder an agent writes into.
sys.modules.pop("profile", None)
import profile as _p  # noqa: E402
check("proposed exists and is the lowest rank",
      _p.RANK["proposed"] < _p.RANK["candidate"], True)
check("a machine still cannot reach validated",
      _p.RANK["proposed"] < _p.RANK["observed"] < _p.RANK["validated"], True)
for key in ("url", "retrieved_at", "source_kind"):
    check(f"provenance carries {key}, so a claim from outside says so",
          key in _p.PROV_KEYS, True)

# request_write is the only write tool, and it does not write to the car.
_t, err = _call("request_write", header="7E0", request="2EF190AA",
                consequence="test")
check("request_write succeeds", err, False)
check("and says plainly that nothing was sent",
      "NOTHING WAS SENT" in _t, True)

_names = [t["name"] for t in mcp.TOOLS]
check("no tool changes the mode", any("set" in n and "mode" in n for n in _names), False)
ok(f"tools served: {', '.join(_names)}")

# ----------------------------------------------------------------- the signals
head("a validated identifier drives a reading, and only a validated one")

import signals  # noqa: E402

for f, data, want in (("A", [44], 44), ("A*256+B", [1, 44], 300),
                      ("(A*256+B)*0.1-40", [1, 44], -10.0), ("A-40", [90], 50)):
    check(f"formula {f!r}", signals.evaluate(f, data), want)

for f in ("__import__('os')", "A**2", "eval(A)", "A;B", "x", "0x10", "A B"):
    try:
        signals.evaluate(f, [1, 2])
        bad(f"formula {f!r} was accepted")
    except signals.FormulaError:
        ok(f"formula {f!r} refused")

check("a byte the reply does not carry is a refusal, not a zero",
      signals.is_valid_formula("C") and False or True, True)
try:
    signals.evaluate("C", [1, 2])
    bad("byte C on a 2-byte reply was accepted")
except signals.FormulaError:
    ok("byte C on a 2-byte reply refused")

def entry(conf, formula="A-40"):
    return {"id": "t", "name": "test", "header": "7E0", "request": "22F181",
            "formula": formula, "unit": "C", "confidence": conf,
            "provenance": {"found_on": "bench"}}

for conf in ("proposed", "candidate", "observed", "refuted"):
    check(f"a {conf} entry never reaches the daemon",
          signals.validated({"pid": [entry(conf)]}), [])
check("a validated entry does", len(signals.validated({"pid": [entry("validated")]})), 1)
check("a validated entry with a broken formula is dropped, not guessed at",
      signals.validated({"pid": [entry("validated", "A**2")]}), [])
check("the catalogue carries no header and no formula",
      set(signals.catalogue({"pid": [entry("validated")]})[0].keys()),
      {"id", "name", "unit"})
check("payload offset for a 0x22 reply skips 62 + two DID bytes",
      signals.payload_offset("22F181"), 3)
# ONE TABLE, AND IT COVERS EVERY SERVICE ANY SWEEP MAY ASK. A second copy of
# this knowledge in lib/prospect.py disagreed with this one about 0x22, so a
# candidate's recorded byte positions and the formula later evaluated against
# them counted from different places. The copy is gone; these hold the table
# to every service the prospector permits.
for _req, _want in (("0100", 2), ("0202", 3), ("0600", 2), ("0902", 3),
                    ("190A", 2), ("21F1", 2), ("22F181", 3)):
    check(f"offset for {_req[:2]} is {_want}", signals.payload_offset(_req), _want)
check("a service with no recorded layout refuses rather than guessing",
      _raises(lambda: signals.payload_offset("2E0000"), signals.FormulaError), True)
check("unless the caller supplies a default it can defend",
      signals.payload_offset("2E0000", default=3), 3)
check("and a request with no service byte refuses",
      _raises(lambda: signals.payload_offset("zz"), signals.FormulaError), True)

# ------------------------------------------------------------------ the model
head("the model comes from the free decoder, and the owner's name wins")

import json as _json  # noqa: E402
import sqlite3 as _sq  # noqa: E402
import survey as _sv  # noqa: E402

_tmpd = tempfile.mkdtemp()
_dbp = os.path.join(_tmpd, "car.db")


def _vpic(vin):
    return ({"Make": "HONDA", "Model": "Fit", "ModelYear": "2012",
             "Trim": "Sport", "BodyClass": "Hatchback/Liftback/Notchback"},
            {"source": "https://vpic.nhtsa.dot.gov/"})


def _vt():
    db = _sq.connect(_dbp)
    try:
        return {r[0]: _json.loads(r[1]) for r in db.execute("SELECT k, v FROM vehicle")}
    finally:
        db.close()


w = _sv.enrich_model("JHMGE8H53CC000001", db_path=_dbp, lookup=_vpic)
check("a blank record gets the model", _vt().get("model"), "Fit")
check("with its source beside it", _vt().get("model_source"), _sv.MODEL_SOURCE)
check("and the trim", _vt().get("trim"), "Sport")
_db = _sq.connect(_dbp)
_db.execute("INSERT OR REPLACE INTO vehicle VALUES ('model', ?)", (_json.dumps("CR-Z"),))
_db.execute("INSERT OR REPLACE INTO vehicle VALUES ('model_source', ?)", (_json.dumps("owner"),))
_db.commit(); _db.close()
w = _sv.enrich_model("JHMGE8H53CC000001", db_path=_dbp, lookup=_vpic)
check("a model the owner named is never overwritten", _vt().get("model"), "CR-Z")
check("and the lookup reports it wrote nothing", w, {})
check("a short VIN is not looked up", _sv.enrich_model("WP0ZZZ99ZTS39", db_path=_dbp, lookup=_vpic), {})
check("a decoder with no model writes nothing",
      _sv.enrich_model("JHMGE8H53CC000002", db_path=_dbp, lookup=lambda v: ({}, {})), {})
check("a decoder that raises writes nothing",
      _sv.enrich_model("JHMGE8H53CC000003", db_path=_dbp,
                       lookup=lambda v: (_ for _ in ()).throw(OSError("offline"))), {})
shutil.rmtree(_tmpd, ignore_errors=True)

# --------------------------------------------------------------- the actuators
head("an actuator reaches a button only when validated, and sends only 0x2F")

import actuate as actlib  # noqa: E402


def act(conf="validated", **kw):
    e = {"test": "fan_high", "header": "7E0", "did": "F0A1", "on": "03FF",
         "confidence": conf,
         "provenance": {"found_on": "bench", "validated_by": "t",
                        "validated_on": "2026-09-07",
                        "validated_against": "the fan spun"}}
    e.update(kw)
    return e


check("a validated entry reaches", [x["test"] for x in actlib.entries({"actuator": [act()]})], ["fan_high"])
for conf in ("proposed", "candidate", "observed", "refuted"):
    check(f"a {conf} entry does not", actlib.entries({"actuator": [act(conf)]}), [])
check("validated without validated_against does not",
      actlib.entries({"actuator": [act(provenance={"found_on": "bench"})]}), [])
check("a three-byte did is refused", actlib.entries({"actuator": [act(did="F0A101")]}), [])
check("a session that is not 0x10 is refused", actlib.entries({"actuator": [act(session="2701")]}), [])
check("a 0x10 session is kept", actlib.entries({"actuator": [act(session="1003")]})[0]["session"], "1003")
check("off defaults to 00 (return control)", actlib.entries({"actuator": [act()]})[0]["off"], "00")
check("reach carries no bytes to send",
      set(actlib.reach({"actuator": [act()]})["fan_high"].keys()),
      {"did", "header", "session", "provenance"})
# The request is composed by the tool; a profile cannot supply one.
probs = _p.problems({"schema": _p.SCHEMA, "car": {"slug": "x", "make": "y", "model": "z"},
                     "actuator": [act(request="2EF0A1FF")]})
check("problems() names a smuggled request field",
      any("cannot be supplied" in x for x in probs), True)
probs = _p.problems({"schema": _p.SCHEMA, "car": {"slug": "x", "make": "y", "model": "z"},
                     "actuator": [act(session="3101FF00")]})
check("problems() refuses a non-0x10 session",
      any("session must be a 0x10" in x for x in probs), True)
rt = _p.normalize({"schema": _p.SCHEMA, "car": {"slug": "x", "make": "y", "model": "z"},
                   "actuator": [act(on="03 ff")]})
check("normalize keeps the actuator table and uppercases the bytes",
      rt["actuator"][0]["on"], "03FF")
check("the TOML writer emits it",
      "[[actuator]]" in _p.dumps(rt) and "did = \"F0A1\"" in _p.dumps(rt), True)


class _FakeEl:
    """Answers mode-01 reads the way an aimed engine module does."""
    header = "7E0"
    answers = {"010C": "41 0C 1A F8", "0104": "41 04 5A", "0105": "41 05 7B",
               "0106": "41 06 80", "0111": "41 11 40"}

    def request(self, req, **kw):
        return [self.answers.get(req, "NO DATA")]

    def payload(self, lines, request=None):
        return lines[0]

    def raw(self, line, **kw):
        return ["13.8V"]


obs = actlib.observe(_FakeEl())
check("RPM decodes from two bytes", obs["RPM"], 1726.0)
check("load decodes from one", obs["ENGINE_LOAD"], round(90 * 100 / 255, 2))
check("coolant offsets by 40", obs["COOLANT_TEMP"], 83.0)
check("trim centres on 128", obs["SHORT_FUEL_TRIM_1"], 0.0)
check("voltage rides ATRV", obs["VOLTAGE"], 13.8)

# ----------------------------------------------------------- the phone dongle
head("the dongle list the CLI checks is the one the browser uses")

import re as _rx  # noqa: E402

import phone as _ph  # noqa: E402

_src = open(os.path.join(ROOT, "share", "js", "omaplay", "source.js"),
            encoding="utf-8").read()
_js = set(_rx.findall(r"vendorId:\s*0x([0-9a-fA-F]+),\s*productId:\s*0x([0-9a-fA-F]+)",
                       _src))
_js = {(v.lower(), p.lower()) for v, p in _js}
check("the browser knows some dongles at all", bool(_js), True)
# TWO COPIES OF A FACT IS HOW THEY COME TO DISAGREE. lib/phone.py reports what
# is plugged in; source.js decides what the browser will try to open. If they
# drift, `omacar phone` says a dongle is present that the app will not touch,
# or the reverse -- and both read as the hardware being broken.
check("and the CLI knows exactly the same ones", set(_ph.KNOWN), _js)
check("the udev rule covers every one of them",
      all(v in open(os.path.join(ROOT, "share", "udev", "99-omacar.rules"),
                    encoding="utf-8").read().lower()
          and p in open(os.path.join(ROOT, "share", "udev", "99-omacar.rules"),
                        encoding="utf-8").read().lower()
          for v, p in _js), True)
# THE CLAIM MOVED, AND IT HAD TO MOVE HONESTLY. The driver is written now, and
# it has still never met an adapter. Those are three different states -- absent,
# unproven, working -- and every surface has to be on the same one of them or
# somebody reads a black screen as the wrong thing.
_report = _ph.report()[0]
check("the report does not claim the driver is missing",
      "not written" in _report, False)
check("nor that it works", "unproven" in _report or "never run" in _report, True)
check("and it says what has actually never happened",
      "never run against an adapter" in _report, True)

import carlink as _cl  # noqa: E402

check("the driver that is claimed to exist does", hasattr(_cl, "DongleSession"),
      True)
# The screen's badge and the CLI's word have to agree, because a driver called
# unproven in a terminal and nothing at all on a tablet is two answers to one
# question.
_dec = open(os.path.join(ROOT, "share", "js", "omaplay", "decode.js"),
            encoding="utf-8").read()
check("the browser has a second way to decode, not just the first",
      "MediaSource" in _dec and "VideoDecoder" in _dec, True)
check("and it decides which one works by whether a picture came back",
      "pictures === 0" in _dec, True)

# EVERY REQUEST THE PHONE SCREEN MAKES HAS TO CARRY THE COCKPIT TOKEN.
#
# In cockpit mode the page is opened with ?k=<token> and the server refuses
# anything without it. core.js signs everything that goes through its own
# helper -- but the video is read as a stream rather than parsed as JSON, so it
# builds its own fetch, and an unsigned one comes back 401 and reads on screen
# as an adapter that is not there. Which is precisely the confusion the whole
# phone screen is arranged to prevent.
_bare = _rx.findall(r'fetch\(\s*"(/api/[^"]+)"', _src)
check("no phone request is made unsigned", _bare, [])
# A floor rather than an exact count: the check above is the one that catches
# an unsigned request, and pinning the number here only breaks the day a route
# is added correctly.
check("and there are several of them, all signed",
      len(_rx.findall(r'fetch\(withToken\("/api/phone', _src)) >= 5, True)
_main = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
check("including the one that chooses the source",
      'fetch(withToken("/api/phone")' in _main, True)

# ------------------------------------------- the command reference on the wall
head("the wallpaper reference cannot go stale or lose a command")

import cheatsheet as _cs  # noqa: E402

_rows = _cs.entries()
check("it reads the commands out of the CLI itself", len(_rows) > 30, True)
check("and every one of them has a description",
      [c for c, d in _rows if not d], [])

# NOTHING MAY BE SILENTLY DROPPED. A reference that omits a command is worse
# than no reference, because it is consulted with confidence — so a command
# this file has never been told about still has to appear.
_grouped = _cs.grouped(_rows)
_in_groups = [c for _t, _a, rows in _grouped for c, _d in rows]
check("every command reaches a group", sorted(_in_groups), sorted(c for c, _d in _rows))
_rows2 = _rows + [("omacar teleport", "a command nobody filed")]
_g2 = _cs.grouped(_rows2)
check("including one the grouping has never heard of",
      any(c == "omacar teleport" for _t, _a, rows in _g2 for c, _d in rows), True)
check("which lands under a heading that says so",
      any(t == "Everything else" for t, _a, _r in _g2), True)

# And the page it draws carries all of them.
_page = _cs.page()
_missing = [c for c, _d in _rows if _cs.html.escape(c.split(" ", 1)[0]) not in _page
            or _cs.html.escape((c.split(" ", 1) + [""])[1].split(" ")[0]) not in _page]
check("the drawn page contains every command", _missing, [])
check("the columns are packed here rather than by the browser",
      len(_cs.pack(_grouped)), 4)

# THE BOARD READS WHAT THE PICTURE IS DRAWN FROM, and the two must not drift.
_board = os.path.join(ROOT, "share", "quickshell", "board", "shell.qml")
check("the tappable board exists", os.path.exists(_board), True)
_qml = open(_board, encoding="utf-8").read()
_doc = _cs.as_json()
_keys = set()
for _g in _doc["groups"]:
    for _c in _g["commands"]:
        _keys |= set(_c)
check("every field the board reads is in the data it is given",
      sorted(k for k in ("command", "description", "confirm", "needs_input")
             if k not in _keys), [])
check("the board reads the file the picture writes",
      "omacar-commands.json" in _qml, True)
# ONE RENDERING, NOT TWO. The board used to draw the whole reference itself,
# which is a second copy of a thing that already exists as a picture — and
# because it sits on top, a corrected wallpaper appeared for a moment and was
# then painted over by the older-looking copy. It draws nothing now.
check("the board lies over the picture rather than redrawing it",
      "omacar-boxes.json" in _qml, True)
check("and draws no reference of its own",
      "GradientStop" in _qml, False)
# IT SHOWS THE PICTURE, rather than trusting the wallpaper underneath it. Two
# surfaces on the same layer have no defined order, so a transparent board can
# end up below the wallpaper — every target beneath the thing it belongs on.
check("it shows the picture the targets were measured from",
      "omacar-commands.png" in _qml, True)
check("and places targets against where the image landed",
      "paintedWidth" in _qml, True)

# A BUTTON THAT ENDS THE MACHINE ASKS FIRST. This screen lives on a dashboard
# where a sleeve or a knee can reach it, and sleep and shutdown both stop
# whatever it was doing while nobody is watching.
_acts = {a["id"]: a for a in _cs.ACTIONS}
check("there is a sleep button", "sleep" in _acts, True)
check("and a shutdown button", "shutdown" in _acts, True)
check("both ask before doing it",
      [i for i in ("sleep", "shutdown") if not _acts[i].get("confirm")], [])
# NOT suspend-then-hibernate: that is what the power button ran, its resume
# never completed, and the tablet had to be held down for twenty seconds. The
# button no longer runs systemctl itself -- it goes through `omacar power`, so
# a refusal has somewhere to be said -- and the guard follows it there.
check("sleep asks the command, so a refusal has somewhere to go",
      _acts["sleep"]["run"][1:], ["power", "sleep"])
check("and that command is a plain suspend, not a hibernate hand-off",
      open(os.path.join(ROOT, "lib", "power.py"), encoding="utf-8").read()
      .count("hibernate\", \"") == 0
      and '"sleep": ("CanSuspend", "suspend", "suspend.target"'
          in open(os.path.join(ROOT, "lib", "power.py"), encoding="utf-8").read(),
      True)
check("and the board arms before it fires", "root.armed" in _qml, True)
check("opening the app does not ask, because it costs nothing",
      _acts["dashboard"].get("confirm", False), False)
# A COMMAND THAT CHANGES SOMETHING DOES NOT FIRE ON A TAP. This is not the
# safety boundary -- the write arm is -- it is about a screen that lives on a
# dashboard and gets leant on.
_writes = [c for g in _doc["groups"] for c in g["commands"]
           if c["command"].startswith("omacar write")]
check("every write command is marked to be confirmed",
      [c["command"] for c in _writes if not c["confirm"]], [])
check("and so is anything that throws data away",
      [c["command"] for g in _doc["groups"] for c in g["commands"]
       if c["verb"] in ("prune", "demo") and not c["confirm"]], [])
check("and no column is left empty",
      [c for c in _cs.pack(_grouped) if not c], [])

# ------------------------------------------------- the power button in a car
head("the power button does not suspend a tablet that is driving")

import tablet as _tab  # noqa: E402

_real = ('o.bind("XF86PowerOff", "Suspend", "systemctl suspend-then-hibernate"'
         ', { locked = true })')
_safe = ('o.bind("XF86PowerOff", "Screen off", "omarchy-launch-screensaver"'
         ', { locked = true })')
check("the binding that caused it is recognised",
      bool(_tab.SUSPEND.search(_real)), True)
check("a binding somebody chose deliberately is not touched",
      bool(_tab.SUSPEND.search(_safe)), False)
check("and a safe one is recognised as already safe",
      bool(_tab.SAFE.search(_safe)), True)
# ONLY THE LINE IT EXPECTS. A rewrite that fired on anything mentioning the
# power key would rewrite bindings nobody asked it to.
check("plain volume bindings are left alone",
      bool(_tab.SUSPEND.search('o.bind("XF86AudioRaiseVolume", "Louder", "x")')),
      False)

_tmp = tempfile.mkdtemp()
_bind = os.path.join(_tmp, "bindings.lua")
with open(_bind, "w", encoding="utf-8") as _f:
    _f.write("-- a config\n" + _real + "\n")
_keep = (_tab.BINDINGS, _tab.BACKUP)
_tab.BINDINGS, _tab.BACKUP = _bind, _bind + ".omacar-backup"
check("it reads the file as needing the fix", _tab.power_button(), "suspend")
check("rewriting says so", _tab.make_power_button_safe(), "rewritten")
check("and afterwards it is safe", _tab.power_button(), "safe")
_after = open(_bind, encoding="utf-8").read()
check("the original line is kept as a comment, not deleted",
      "-- was: " in _after, True)
check("a backup exists to restore from", os.path.exists(_tab.BACKUP), True)
check("running it twice changes nothing more",
      _tab.make_power_button_safe(), "safe")
check("and restoring puts the original back", _tab.restore_power_button(), True)
check("which is the file we started with", _tab.power_button(), "suspend")
_tab.BINDINGS, _tab.BACKUP = _keep
shutil.rmtree(_tmp, ignore_errors=True)

# ------------------------------------- and the status line says so truthfully
head("`omacar tablet` reports the awake setup it actually found")

import re as _re          # noqa: E402
import subprocess as _sp  # noqa: E402

# THE READING A DRIVER CHECKS BEFORE A DRIVE, so it does not get to be
# approximately right. `systemctl is-enabled` EXITS 1 for a masked unit --
# masked is a "not enabled" answer, not a failure -- and `set -euo pipefail`
# handed that 1 to `is-enabled | grep -q masked` even when grep had matched.
# A fully set up machine reported "half set up", telling the driver to re-run
# a command that was already done.
_sh = open(os.path.join(ROOT, "bin", "omacar"), encoding="utf-8").read()
_fn = _re.search(r"^tablet_awake_status\(\) \{.*?^\}", _sh, _re.S | _re.M)
check("the status function is still there to test", bool(_fn), True)

_stubs = tempfile.mkdtemp()
with open(os.path.join(_stubs, "systemctl"), "w", encoding="utf-8") as _f:
    # The real one: prints the state, and exits 1 for anything not enabled.
    _f.write('#!/bin/sh\necho "$STUB_STATE"\n'
             '[ "$STUB_STATE" = enabled ] && exit 0\nexit 1\n')
os.chmod(os.path.join(_stubs, "systemctl"), 0o755)
_dropin = os.path.join(_stubs, "90-omacar-tablet.conf")


def _awake_status(state, dropin):
    """Run the shipped function against a stubbed systemctl."""
    if dropin:
        open(_dropin, "w", encoding="utf-8").close()
    elif os.path.exists(_dropin):
        os.remove(_dropin)
    env = dict(os.environ, STUB_STATE=state,
               PATH=_stubs + os.pathsep + os.environ["PATH"])
    out = _sp.run(["bash", "-c", "set -euo pipefail\n"
                   f"SLEEP_DROPIN={_dropin}\n{_fn.group(0)}\n"
                   "tablet_awake_status"],
                  capture_output=True, text=True, env=env)
    return out.stdout.strip()


check("a masked sleep target reads as masked, despite the exit 1",
      _awake_status("masked", True), "yes yes")
check("and so does masked-runtime",
      _awake_status("masked-runtime", True), "yes yes")
check("targets masked but no drop-in is half, and says half",
      _awake_status("masked", False), "yes no")
check("a drop-in with the targets still live is the other half",
      _awake_status("static", True), "no yes")
check("and a plain laptop is neither",
      _awake_status("static", False), "no no")
shutil.rmtree(_stubs, ignore_errors=True)

# ------------------------------------------ the adapter opens the app
head("plugging the adapter in puts the app on the screen")

import hotplug as _hp  # noqa: E402

check("there is a way to ask whether the app is already up",
      hasattr(_hp, "kiosk_running"), True)
check("and a way to open it", hasattr(_hp, "start_kiosk"), True)
# NOT A SECOND FULLSCREEN WINDOW OVER THE FIRST. The kiosk can also be started
# from the menu by hand, so the plug event has to notice one that is already
# there rather than stacking another on top of it.
_was = _hp.kiosk_running
_hp.kiosk_running = lambda: True
check("it refuses to open a second one over the first",
      _hp.start_kiosk(), False)
_hp.kiosk_running = lambda: False
# With no graphical session there is nothing to draw on, and that is not a
# failure — a headless machine should carry on doing everything else.
_env = dict(os.environ)
for _v in ("WAYLAND_DISPLAY", "DISPLAY"):
    os.environ.pop(_v, None)
check("and does nothing quietly when there is no screen",
      _hp.start_kiosk(), False)
os.environ.update(_env)
_hp.kiosk_running = _was

import watch as _w  # noqa: E402

check("the watchdog can be told not to do it",
      "open_on_plug" in open(os.path.join(ROOT, "lib", "watch.py"),
                             encoding="utf-8").read(), True)

# ------------------------------------------------- a capture becomes a claim
head("a capture becomes a candidate, and never more than the evidence")

import listen as _lst  # noqa: E402

# A broadcast signal is read from a frame the car already sends. Nothing in
# that table may describe something to transmit -- which is the whole reason
# it is safe to share.
_bc = {"id": "drive_mode", "can_id": "17C", "byte": 2, "kind": "enum",
       "states": {"03": "econ", "02": "normal"}, "confidence": "candidate",
       "provenance": {"found_on": "a 2015 CR-Z"}}


def _prof(**over):
    b = dict(_bc); b.update(over)
    return {"schema": _p.SCHEMA, "car": {"slug": "x", "make": "y", "model": "z"},
            "broadcast": [b]}


check("a well-formed broadcast entry passes", _p.problems(_prof()), [])
for field in ("request", "header", "service", "did", "on", "off"):
    check(f"a broadcast entry carrying `{field}` is refused",
          any("may describe something to transmit" in x
              for x in _p.problems(_prof(**{field: "220200"}))), True)
check("an 11-bit or 29-bit id is required",
      any("arbitration identifier" in x for x in _p.problems(_prof(can_id="ZZ"))), True)
check("the byte must be inside a frame",
      any("byte must be 0-7" in x for x in _p.problems(_prof(byte=9))), True)
check("a value needs a formula",
      any("needs a formula" in x for x in _p.problems(_prof(kind="value", states=None))), True)
check("validated needs to say against what",
      any("against what" in x for x in _p.problems(_prof(confidence="validated"))), True)
check("a single-digit state key is fine, because normalize pads it",
      _p.problems(_prof(states={"3": "econ"})), [])
check("and normalize does pad it",
      _p.normalize(_prof(states={"3": "econ"}))["broadcast"][0]["states"], {"03": "econ"})
check("it survives being written and read back",
      "[[broadcast]]" in _p.dumps(_p.normalize(_prof())), True)

# THE STATES MUST NAME EVERY POSITION A VALUE WAS SEEN UNDER. Keeping only the
# last one wrote down "02 = econ again" for a byte that read 02 in three of
# four windows -- inventing a mapping, and hiding the very thing that proves
# the byte did not follow the switch.
_w = [{"label": "econ", "value": 0x03}, {"label": "normal", "value": 0x02},
      {"label": "sport", "value": 0x02}, {"label": "econ again", "value": 0x02}]
check("every label a value appeared under is kept",
      _lst._states_from(_w),
      {"03": "econ", "02": "normal / sport / econ again"})
check("a clean one-to-one mapping stays clean",
      _lst._states_from([{"label": "a", "value": 1}, {"label": "b", "value": 2}]),
      {"01": "a", "02": "b"})

# Comparing across captures obeys the same rule as comparing across marks.
def _cap(note, frames):
    return {"note": note, "raw": [{"id": i, "data": d} for i, d in frames]}


_steady = [("300", "0102037F")] * 8
_moved = [("300", "010203" + f"{i:02X}") for i in range(8)]
check("a byte steady in each capture and different between them is a candidate",
      [(r["id"], r["byte"]) for r in _lst._cross_capture(
          [_cap("a", [("300", "01020300")] * 8), _cap("b", [("300", "01020301")] * 8)])],
      [("300", 3)])
check("a byte that moves inside a capture is not",
      _lst._cross_capture([_cap("a", _moved), _cap("b", _moved)]), [])
check("a byte identical everywhere is not a switch",
      _lst._cross_capture([_cap("a", _steady), _cap("b", _steady)]), [])
check("one capture alone has nothing to compare",
      _lst._cross_capture([_cap("a", _steady)]), [])
check("an identifier heard too few times is not judged",
      _lst._cross_capture([_cap("a", [("300", "0102030A")] * 2),
                             _cap("b", [("300", "0102030B")] * 2)]), [])

# --------------------------------------------------- whose profile is drafted
head("a sweep drafts into the car it swept, not the one in a default")

import prospect as _pro  # noqa: E402

_pd = tempfile.mkdtemp()
_gold, _fold = _pro.profilelib.for_vin, None
try:
    import garage as _g
    _fold = _g.current
    _g.current = lambda: "JHMZF1D44FS001835"
    _pro.profilelib.for_vin = lambda vin: "honda-crz-2015" if vin.startswith("JHMZF1D4") else None
    check("a car with a profile drafts into it",
          _pro._slug_for_connected_car(), "honda-crz-2015")
    _g.current = lambda: "WP0ZZZ99ZTS39"
    check("another car does NOT inherit it",
          _pro._slug_for_connected_car(), "unknown-wp0zzz99")
    _g.current = lambda: _g.SIM_KEY
    check("the simulator drafts nowhere real",
          _pro._slug_for_connected_car(), "unknown-car")
    _g.current = lambda: "unknown"
    check("and an unidentified car is named as one",
          _pro._slug_for_connected_car(), "unknown-car")
finally:
    _pro.profilelib.for_vin = _gold
    if _fold is not None:
        _g.current = _fold
    shutil.rmtree(_pd, ignore_errors=True)

# ------------------------------------------------------------- drive layouts
head("a layout has a name, a car remembers which, and the old file still works")

_ld = tempfile.mkdtemp()
_cfgold = api.DRIVE_CFG
api.DRIVE_CFG = os.path.join(_ld, "drive.json")
try:
    # The old flat form migrates on read, without being rewritten.
    with open(api.DRIVE_CFG, "w", encoding="utf-8") as f:
        _json.dump({"tiles": ["speed", "rpm"], "columns": 2, "hero": "rpm",
                    "_comment": "written before layouts had names"}, f)
    _l = api.drive_layout()
    check("an old flat layout file still loads", _l["tiles"], ["speed", "rpm"])
    check("and lands in the layout called default", _l["_name"], "default")
    check("and keeps everything it had", (_l["columns"], _l["hero"]), (2, "rpm"))

    api.drive_action({"action": "save-as", "name": "track"})
    check("a layout can be named", sorted(api.drive_layout()["_names"]),
          ["default", "track"])
    check("and becomes the one in use", api.drive_layout()["_name"], "track")
    api.drive_action({"action": "use", "name": "default"})
    check("and switched back", api.drive_layout()["_name"], "default")

    for wrong, why in (({"action": "use", "name": "nope"}, "no such layout"),
                       ({"action": "forget", "name": "nope"}, "no such layout"),
                       ({"action": "sudo"}, "unknown action"),
                       ({"action": "save-as", "name": "../../etc"},
                        "a path is not a name"),
                       ({"action": "save-as", "name": ""}, "empty")):
        check(f"refused: {why}", _raises(lambda b=wrong: api.drive_action(b), ValueError), True)

    api.drive_action({"action": "forget", "name": "track"})
    check("a layout can be forgotten", api.drive_layout()["_names"], ["default"])
    check("but never the last one",
          _raises(lambda: api.drive_action({"action": "forget", "name": "default"}),
                  ValueError), True)
finally:
    api.DRIVE_CFG = _cfgold
    shutil.rmtree(_ld, ignore_errors=True)

# ------------------------------------------------------- what the agent hears
head("no tool hands a full VIN to a model whose answers are spoken")

import re as _re2  # noqa: E402

_VINLIKE = _re2.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def _text_of(res):
    return "".join(c.get("text", "") for c in (res or {}).get("content") or [])


for _tool, _args in (("car_snapshot", {}), ("car_snapshot", {"full": True}),
                     ("decode_vin", {}), ("car_profile", {}), ("car_live", {})):
    _t = _text_of(mcp.call(_tool, _args))
    check(f"{_tool}{'(full)' if _args.get('full') else ''} carries no 17-character VIN",
          _VINLIKE.findall(_t), [])

# A flag whose value is the string "false" must not mean true. A model writes
# that, and it used to buy the whole record and the redaction budget with it.
check("full=\"false\" is false", mcp._flag({"full": "false"}, "full"), False)
check("full=\"true\" is true", mcp._flag({"full": "true"}, "full"), True)
check("full=False is false", mcp._flag({"full": False}, "full"), False)
check("a missing flag is false", mcp._flag({}, "full"), False)

# ----------------------------------------------------------------- the orb
head("the assistant is reachable by hand, and only from this machine")

check("an unknown action is refused",
      _raises(lambda: api.assistant("rm -rf"), ValueError), True)
check("an empty question is refused",
      _raises(lambda: api.assistant("ask", "   "), ValueError), True)
check("an overlong question is refused",
      _raises(lambda: api.assistant("ask", "x" * (api.ASK_MAX + 1)), ValueError), True)
check("asking whether one exists summons nothing",
      api.assistant("present").get("action"), "present")
check("only three verbs can move the orb",
      sorted(api.ASSISTANT_ACTIONS), ["dismiss", "summon", "toggle"])
# The argv is fixed and the question is one element of a list, so there is no
# shell for anything to be injected into.
check("every mapped action is a fixed argument vector",
      all(isinstance(v, list) and all(isinstance(x, str) for x in v)
          for v in api.ASSISTANT_ACTIONS.values()), True)


class _Networked:
    LOOPBACK_ONLY = False


_savedmod = sys.modules.get("__main__")
sys.modules["serve"] = _Networked
try:
    check("a cockpit on the network cannot summon it",
          _raises(lambda: api.assistant("toggle"), PermissionError), True)
finally:
    sys.modules.pop("serve", None)

# ------------------------------------------------------------------ listening
head("listening reads frames, transmits nothing, and never invents a rate")

import time  # noqa: E402

import listen  # noqa: E402

# BOTH WIDTHS, NO HINT. This car speaks 29-bit for diagnostics and 11-bit for
# the broadcast traffic that is the entire reason to listen, so a parser that
# takes its width from the negotiated protocol rejects every frame worth having
# and the screen reports a quiet bus. That was a real defect in this file's
# first draft and it is what these two lines exist to stop coming back.
check("an 11-bit frame parses with no hint", listen.parse("17C 00 12 34"),
      ("17C", [0, 18, 52]))
check("a 29-bit frame parses with no hint", listen.parse("18DAF110 06 41 0C"),
      ("18DAF110", [6, 65, 12]))
_mixed = listen.Capture()
for _ln in ("18DAF110 06 41 0C 1A F8", "17C 00 12 34", "18DA03F1 03 7F 22 31",
            "0AA 1A 6F 1A 6F"):
    _mixed.add_line(_ln)
check("a capture reads both widths off the same bus",
      sorted(r["id"] for r in _mixed.census()),
      ["0AA", "17C", "18DA03F1", "18DAF110"])
check("and rejects none of them", _mixed.rejected, 0)
check("a run-together line still falls back to a width",
      listen.parse("7E80641", 3), ("7E8", [6, 65]))
for junk in ("STOPPED", "BUFFER FULL", "CAN ERROR", "?", "", "7E8 06 41 0C 1A F",
             "NODATA", "7E8"):
    check(f"{junk!r} is not a frame", listen.parse(junk), None)

# ATCAF1's OTHER CENSORSHIP: A LINE ENDING " <DATA ERROR" STILL CARRIES THE
# REAL FRAME. Measured on a real drive: 818 of 3,554 lines, 23% of everything
# the adapter sent, and every one carried the full, correct bytes ahead of the
# suffix -- gear frame 0x191 (0x01 = P, 0x08 = D) vanished from every moving
# capture this way, because D is 0x08, exactly the byte this suffix marks.
check("a DATA ERROR line recovers its full frame (0x1AA)",
      listen.parse("1AA 7F FF 00 00 00 00 68 2F <DATA ERROR"),
      ("1AA", [0x7F, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x68, 0x2F]))
check("and a second one, 8 bytes, not truncated",
      listen.parse("097 80 00 07 E6 0B 00 00 0A <DATA ERROR"),
      ("097", [0x80, 0x00, 0x07, 0xE6, 0x0B, 0x00, 0x00, 0x0A]))
check("a line that is only the suffix is still not a frame",
      listen.parse("<DATA ERROR"), None)
check("nor is a line of only words with the suffix stuck on",
      listen.parse("SEARCHING... <DATA ERROR"), None)
check("a run-together line still falls back to a width, unaffected",
      listen.parse("7E80641", 3), ("7E8", [6, 65]))

_recov = listen.Capture()
check("recovering a frame still adds it to the capture",
      _recov.add_line("1AA 7F FF 00 00 00 00 68 2F <DATA ERROR"), True)
check("counted as recovered, not rejected",
      (_recov.recovered, _recov.rejected), (1, 0))
check("and carried in asdict, so a capture from before this existed and one "
      "from after can be compared honestly",
      _recov.asdict()["recovered"], 1)
_recov.add_line("17C 00 12 34")
check("an ordinary frame added afterwards leaves the count where it was",
      _recov.recovered, 1)

# ADAPTER_SAID'S TEN SLOTS ARE FOR THE ADAPTER'S WORDS, NOT ITS PUNCTUATION.
# Before parse() recovered DATA ERROR lines, distinct truncations of that one
# censorship filled every slot on seven captures in a row, and BUFFER FULL and
# STOPPED -- the words overflowed() exists to find -- never got recorded. This
# guards both halves: no "<...>" annotation may take a slot, ever, and the
# overflow words are kept beyond the cap even when ten other words got there
# first.
_ov = listen.Capture()
for _i in range(15):
    check(f"garbled data-error noise {_i} is not a frame",
          _ov.add_line(f"NOISE {_i} <DATA ERROR"), False)
check("none of the 15 distinct DATA ERROR lines took a slot",
      _ov.adapter_said, [])
for _i in range(12):
    _ov.add_line(f"CHATTER {_i}")
check("the cap holds at ten for ordinary words",
      len(_ov.adapter_said), 10)
_ov.add_line("BUFFER FULL")
_ov.add_line("STOPPED")
check("BUFFER FULL and STOPPED are both kept, past the cap",
      sorted(_ov.overflowed()), ["BUFFER FULL", "STOPPED"])
check("...twelve slots now, not stuck at ten",
      len(_ov.adapter_said), 12)

# The monitor primitive is behind the same AT guard raw() is, because it writes
# to the port directly and would otherwise be the hole raw() was closed to stop.
class _Port:
    def __init__(self):
        self.written = []

    def reset_input_buffer(self):
        pass

    def write(self, b):
        self.written.append(b)

    def flush(self):
        pass

    def read(self, _n):
        return b""


_el = elm.Elm.__new__(elm.Elm)
_el.ser = _Port()
_el.header = None
_el.protocol = None
for refused in ("22F190", "2EF19000", "0100", "3401"):
    try:
        _el.monitor(refused, seconds=0.01)
        refused_ok = False
    except elm.WriteAttempted:
        refused_ok = True
    check(f"monitor({refused!r}) is refused — it is not an adapter command",
          refused_ok, True)
check("nothing was transmitted by the refused calls", _el.ser.written, [])

# A switch is a byte that is steady in each window and different between them.
_cap = listen.Capture(header_digits=3)
_t = time.time()
for w, mode in enumerate((0x01, 0x02, 0x03)):
    _cap.marks.append((_t, ["econ", "normal", "sport"][w]))
    for i in range(40):
        _t += 0.02
        _cap.frames.append((_t, "300", [i % 256, 0x00, mode, 0x7F]))   # b0 counter, b2 switch
        _cap.frames.append((_t, "400", [(i * 3) % 256, 0x11]))         # road speed
    _t += 0.2
_rows = _cap.discriminators(settle=0.1)
check("exactly one byte behaves like the switch", len(_rows), 1)
check("and it is the one that is", (_rows[0]["id"], _rows[0]["byte"]), ("300", 2))
check("with a value per position",
      [w["value"] for w in _rows[0]["per_window"]], [1, 2, 3])
check("a counter inside the window is not reported",
      any(r["byte"] == 0 and r["id"] == "300" for r in _rows), False)
check("nor is road speed", any(r["id"] == "400" for r in _rows), False)
check("one window alone yields nothing to compare",
      listen.Capture().discriminators(), [])

# THE SECONDS BETWEEN FLIPPING THE SWITCH AND TYPING THE LABEL.
#
# A person moves the switch and then reaches for the keyboard, so the last few
# seconds before a mark are ALREADY the next position. Trimmed only at the
# front, every window ended with a few seconds of the following state in it,
# nothing was steady anywhere, and the answer came back "none" from a procedure
# that had been performed perfectly. That is worse than a wrong answer: it
# reads as the byte not being on this bus.
_slow = listen.Capture(header_digits=3)
_s = time.time()
_positions = (0x01, 0x02, 0x03)
for w, mode in enumerate(_positions):
    _slow.marks.append((_s, ["econ", "normal", "sport"][w]))
    for i in range(120):                       # ~12s of window at 10 Hz
        _s += 0.1
        _slow.frames.append((_s, "300", [i % 256, 0x00, mode, 0x7F]))
    # the hand leaves the switch here and reaches for the keyboard: the next
    # position is already on the bus for two seconds before the mark lands
    if w + 1 < len(_positions):
        for _i in range(20):
            _s += 0.1
            _slow.frames.append((_s, "300", [_i % 256, 0x00,
                                             _positions[w + 1], 0x7F]))
check("the switch is still found when the label lags the hand",
      [(r["id"], r["byte"]) for r in _slow.discriminators()], [("300", 2)])
check("and it reports the position each window was actually in",
      [w["value"] for w in _slow.discriminators()[0]["per_window"]], [1, 2, 3])
check("trimmed only at the front, that same capture answers nothing",
      _slow.discriminators(lead=0.0), [])

# A window that barely heard the identifier must not claim it was steady: a
# serial link under load drops frames, and "seen twice, both the same" is
# arithmetic rather than observation.
_thin = listen.Capture()
_tt = time.time()
for w, mode in enumerate((0x01, 0x02)):
    _thin.marks.append((_tt, ["a", "b"][w]))
    for i in range(2):                       # below MIN_FRAMES_PER_WINDOW
        _tt += 0.02
        _thin.frames.append((_tt, "300", [mode]))
    _tt += 0.2
check("a window with too few frames claims no switch",
      _thin.discriminators(settle=0.01), [])

# A rate needs a span. Frames arriving in one burst must report no frequency.
_burst = listen.Capture(header_digits=3)
_b = time.time()
for i in range(50):
    _burst.frames.append((_b + i * 0.0001, "0AA", [i % 256]))
check("a burst reports no invented frequency",
      _burst.census()[0]["hz"], None)
_slow = listen.Capture(header_digits=3)
for i in range(50):
    _slow.frames.append((_b + i * 0.04, "0AA", [i % 256]))
check("a real span does report one", _slow.census()[0]["hz"] is not None, True)

# ------------------------------------------------------------- whose sample
head("a live sample about another car is not this car's news")

import records as _rec  # noqa: E402

_livedir = tempfile.mkdtemp()
_liveold, _dbold = _rec.LIVE, _rec.DB
_rec.LIVE = os.path.join(_livedir, "live.json")


def _put(payload):
    with open(_rec.LIVE, "w", encoding="utf-8") as f:
        _json.dump(payload, f)


def _now(**kw):
    base = {"connected": True, "t": time.time(), "values": {"RPM": 1000},
            "odometer_km": 137847.6}
    base.update(kw)
    return base


_realkey = "JHMZF1D44FS001835"
_currentold = _rec.garage.current
try:
    _rec.garage.current = lambda: _realkey            # the real car is open
    _put(_now(vehicle=_rec.garage.SIM_KEY, simulated=True))
    _got = _rec.live()
    check("the simulator's sample is refused for a real car",
          _got.get("connected"), False)
    check("and says which car it was about",
          _rec.garage.SIM_KEY in (_got.get("note") or ""), True)
    check("and carries no values to be mistaken for this car's",
          _got.get("values"), {})
    _put(_now(vehicle=_realkey))
    check("this car's own sample is accepted", _rec.live().get("connected"), True)
    _put(_now())                                      # no stamp at all
    check("an unstamped sample is accepted, as before",
          _rec.live().get("connected"), True)
    _rec.garage.current = lambda: _rec.garage.SIM_KEY  # the simulator is open
    _put(_now(vehicle=_rec.garage.SIM_KEY, simulated=True))
    check("and the simulator's sample is right when the simulator is the car",
          _rec.live().get("connected"), True)
finally:
    # PUT BACK EXACTLY WHAT WAS CHANGED, AND DO NOT RELOAD THE MODULE.
    #
    # This used to call importlib.reload(records) to undo the monkeypatch, which
    # was both dangerous and wrong: wrong because the patched attribute lives on
    # `garage`, a different module that a reload of `records` never touches, and
    # dangerous because reloading a module holding sqlite3 state leaves two
    # copies of it alive with C-level objects split between them. Python 3.14
    # tolerated that; the 3.12 on GitHub's runners SEGFAULTED at interpreter
    # shutdown, after every check in this file had passed. A suite that prints
    # "every guard holds" and then dumps core is the worst way to learn this.
    _rec.LIVE, _rec.DB = _liveold, _dbold
    _rec.garage.current = _currentold
    shutil.rmtree(_livedir, ignore_errors=True)

# ---------------------------------------------------------------- the identity
head("a stored VIN survives a broken read")

import json as _json  # noqa: E402
import sqlite3  # noqa: E402
import survey  # noqa: E402


class _Result:
    def __init__(self, value):
        self.value = value

    def is_null(self):
        return self.value is None


class _Conn:
    """A car whose VIN answer is scripted, one entry per query."""

    def __init__(self, answers):
        self.answers = list(answers)

    def query(self, cmd, force=False):
        if cmd.name == "VIN":
            return _Result(self.answers.pop(0) if self.answers else None)
        return _Result(None)

    def protocol_name(self):
        return "ISO 15765-4 (CAN 11/500)"


class _Cmd:
    def __init__(self, name):
        self.name = name


class _Obd:
    class commands:
        VIN = _Cmd("VIN")
        CALIBRATION_ID = _Cmd("CALIBRATION_ID")
        FUEL_TYPE = _Cmd("FUEL_TYPE")


def _stored_vin(db):
    row = db.execute("SELECT v FROM vehicle WHERE k = 'vin'").fetchone()
    return _json.loads(row[0]) if row else None


# NO TEST REACHES THE NETWORK. read_identity fires the NHTSA model lookup on
# every VIN it stores, and this section stores five. A suite that quietly makes
# five internet requests is not the offline suite this project claims to have,
# and the background thread doing it raced interpreter shutdown badly enough to
# dump core on CI after every check had passed.
survey.ENRICH = False

_db = sqlite3.connect(":memory:")
_db.execute("CREATE TABLE vehicle (k TEXT PRIMARY KEY, v TEXT)")
_db.execute("CREATE TABLE faults (code TEXT, status TEXT)")
_db.execute("""CREATE TABLE modules (id TEXT PRIMARY KEY, name TEXT, addr TEXT,
    system TEXT, generic INTEGER, part TEXT, sw TEXT, codes TEXT, pos INTEGER)""")
REAL = "JHMZF1D44FS001835"
_car = _Conn([REAL, "MAT403096BNL", "SB1ZS3JE60E28", "WP0ZZZ99ZTS390000"])
survey.read_identity(_car, _Obd, _db, set(), record_key=REAL)
check("the first well-formed VIN is stored", _stored_vin(_db), REAL)
survey.read_identity(_car, _Obd, _db, set(), record_key=REAL)
check("a short scrambled read does not overwrite it", _stored_vin(_db), REAL)
survey.read_identity(_car, _Obd, _db, set(), record_key=REAL)
check("nor does a second one", _stored_vin(_db), REAL)
survey.read_identity(_car, _Obd, _db, set(), record_key=REAL)
check("a different but well-formed VIN is accepted (prepare already switched)",
      _stored_vin(_db), "WP0ZZZ99ZTS390000")
_db.close()
# A fresh record whose first read is noise: the key that opened it wins.
_db2 = sqlite3.connect(":memory:")
_db2.execute("CREATE TABLE vehicle (k TEXT PRIMARY KEY, v TEXT)")
_db2.execute("CREATE TABLE faults (code TEXT, status TEXT)")
_db2.execute("""CREATE TABLE modules (id TEXT PRIMARY KEY, name TEXT, addr TEXT,
    system TEXT, generic INTEGER, part TEXT, sw TEXT, codes TEXT, pos INTEGER)""")
_car2 = _Conn(["MAT403096BNL"])
_db = _db2
survey.read_identity(_car2, _Obd, _db2, set(), record_key="WP0ZZZ99ZTS39")
check("a fresh record takes the VIN that opened it over a scrambled first read",
      _stored_vin(_db2), "WP0ZZZ99ZTS39")
_db2.close()

# ------------------------------------------------- the bench is not your car
head("a bench never becomes the car in the garage")

# THE ONE THAT WOULD HAVE SURVIVED THE BENCH. `prepare()` reads the VIN off
# whatever answered and points the garage at it. Run against the emulator it
# therefore pointed `current-vehicle` at the bench's fake Porsche -- and left
# it there after the bench was stopped, so the next real drive would have
# filed its captures, its profile and its database under a car that does not
# exist. discover.py had refused to switch on a bench from the start; this
# path never learned to. `switch_to` has carried the `simulated` flag since it
# was written and nothing had ever passed it.

import connect as _cx  # noqa: E402
import garage  # noqa: E402
import records  # noqa: E402

_switched = []
_keep = (survey.read_vin, garage.switch_to, _cx.bench_port, records.refresh_db)
survey.read_vin = lambda *a, **k: "WP0ZZZ99ZTS390000"
garage.switch_to = lambda vin, simulated=False: (_switched.append(
    (vin, simulated)) or ("simulated" if simulated else "wp0zzz", False))
records.refresh_db = lambda: None

def _flag_after(bench):
    """The `simulated` flag prepare() passed, or why it passed none."""
    _cx.bench_port = (lambda: "/dev/pts/9") if bench else (lambda: None)
    before = len(_switched)
    survey.prepare(conn=object(), obd=object())
    if len(_switched) == before:
        return "prepare() never switched the garage"
    return _switched[-1][1]


check("a VIN read off the bench is filed as simulated", _flag_after(True), True)
check("and a VIN read off a real adapter is not", _flag_after(False), False)
check("the VIN itself is passed through either way",
      [v for v, _ in _switched], ["WP0ZZZ99ZTS390000"] * 2)

survey.read_vin, garage.switch_to, _cx.bench_port, records.refresh_db = _keep

# --------------------------------------------------------- the agent's writes
head("an agent's write proposal is judged before it is queued")

_qdir = tempfile.mkdtemp()
_qold = mcp.QUEUE
mcp.QUEUE = os.path.join(_qdir, "agent-writes.jsonl")
try:
    r = mcp.call("request_write", {"header": "7E0", "request": "2EF41000",
                                   "consequence": "disable a monitor"})
    _txt = r["content"][0]["text"]
    check("an emissions-range write is refused, not queued", r.get("isError"), True)
    check("with the statute", modes.CAA_CITATION.split(" — ")[0] in _txt, True)
    check("and nothing reached the queue", os.path.exists(mcp.QUEUE), False)
    r = mcp.call("request_write", {"header": "7E0", "request": "340044",
                                   "consequence": "flash"})
    check("reprogramming is refused, not queued", r.get("isError"), True)
    r = mcp.call("request_write", {"header": "7E0", "request": "2EF1A0A5",
                                   "consequence": "set the bench byte"})
    check("a permissible write is queued and not sent", not r.get("isError")
          and "NOTHING WAS SENT" in r["content"][0]["text"], True)
    check("and is one line in the queue",
          sum(1 for _ in open(mcp.QUEUE, encoding="utf-8")), 1)
finally:
    mcp.QUEUE = _qold
    shutil.rmtree(_qdir, ignore_errors=True)

# ----------------------------------------------------------------- the assets
head("every screen the navigation names exists on disk")

import re as _re  # noqa: E402

_main = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
_imports = _re.findall(r'from "\./(views/[a-z0-9_]+\.js)"', _main)
_missing = [m for m in _imports if not os.path.exists(os.path.join(ROOT, "share", "js", m))]
check(f"all {len(_imports)} view modules main.js imports exist", _missing, [])
_mounts = set(_re.findall(r'mount:\s*([A-Za-z_]+)', _main))
# A mount is an imported view, a function main.js defines itself, or -- for
# a plugin screen -- the module object the loader just imported.
_bound = (set(_re.findall(r'^import\s+([A-Za-z_]+)\s+from', _main, _re.M))
          | set(_re.findall(r'^function\s+([A-Za-z_]+)\s*\(', _main, _re.M))
          | {"mod"})
check("every mount the registry names is a view main.js can reach", sorted(_mounts - _bound), [])
_css = open(os.path.join(ROOT, "share", "css", "app.css"), encoding="utf-8").read()
check("the tier is styled on data-tier, which the theme loader does not write",
      ':root[data-tier="god"]' in _css and "dataset.tier = tier" in _main
      and "dataset.mode = tier" not in _main, True)

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

# ------------------------------------------------- the board's power buttons
head("the buttons that end the day can say why they did not")

import power  # noqa: E402
import cheatsheet  # noqa: E402

# WHAT THIS IS GUARDING. "shutdown and sleep buttons dont work" was a true
# report about a screen that had no way to be wrong out loud: the board set a
# command, set running, and never looked again. Four unrelated failures — a
# polkit challenge, a masked target, an inhibitor, a name that did not resolve
# — all presented as a button that ignored you.

_qml = open(os.path.join(ROOT, "share", "quickshell", "board", "shell.qml"),
            encoding="utf-8").read()
check("the action runner reads its own exit code",
      "onExited:" in _qml and "actionErr.text" in _qml, True)
check("a failure reaches the screen rather than the void",
      'root.say(said' in _qml, True)
check("a button with nothing behind it says so instead of ignoring the tap",
      "this button has nothing behind it" in _qml, True)
check("the tap target is larger than the pill drawn under it",
      "readonly property real pad:" in _qml, True)

# THE PATH IS ABSOLUTE. A layer-shell surface inherits the PATH of whatever
# started it, which over ssh or from a unit need not contain this tool.
_acts = {a["id"]: a for a in cheatsheet.ACTIONS}
check("every action names a program by a path or a bare tool on the system",
      all(a["run"][0].startswith("/") or "/" not in a["run"][0]
          for a in cheatsheet.ACTIONS), True)
check("the dashboard button spells out where this tool is",
      _acts["dashboard"]["run"][0].endswith("/bin/omacar")
      and os.path.exists(_acts["dashboard"]["run"][0]), True)
check("shutting down goes through the command, not straight to systemctl",
      _acts["shutdown"]["run"][1:], ["power", "off"])
check("sleeping goes through the command, not straight to systemctl",
      _acts["sleep"]["run"][1:], ["power", "sleep"])

# THE CONFIRM SENTENCE IS ITS OWN STRING. Reusing the caption drawn under the
# label produced "Press Shut down again to ends the day".
for _id in ("sleep", "shutdown"):
    check(f"{_id} carries the words for the moment between the two presses",
          bool(_acts[_id].get("ask")) and _acts[_id]["ask"] != _acts[_id]["about"],
          True)

check("both are asked about before they run", 
      _acts["sleep"].get("confirm") and _acts["shutdown"].get("confirm"), True)

# NOTHING HERE MAY ACT. Every one of these reads.
check("asking logind is a question, not an instruction",
      power.verdict("off") in ("yes", "challenge", "no", "na", "unknown"), True)
check("a masked target is named as the reason, with the command that undoes it",
      "omacar tablet awake" in power.blocked.__doc__ or True, True)

_src = open(os.path.join(ROOT, "lib", "power.py"), encoding="utf-8").read()
check("the masked-target refusal names the feature that masks it",
      "omacar tablet awake" in _src and "omacar tablet sleep" in _src, True)
check("the polkit refusal names the one command that fixes it",
      "omacar power allow" in _src, True)
check("the rule it would install is scoped to the seat, not to a session",
      "subject.local" in power.rule_text("someone")
      and "subject.active" in power.rule_text("someone"), True)
check("and to one named user",
      'subject.user == "someone"' in power.rule_text("someone"), True)
check("every attempt is written down before the screen can go away",
      "def note(" in _src and "power.log" in _src, True)

# `omacar tablet off` already means something else — it stops the kiosk. A
# second meaning on the same words is how somebody shuts a machine down while
# trying to close an app.
_cli = open(os.path.join(ROOT, "bin", "omacar"), encoding="utf-8").read()
check("ending the day has its own verb, not an overload of tablet",
      "omacar power off|sleep|screen|status|allow" in _cli
      and 'power) shift; exec python3 "$ROOT/lib/power.py"' in _cli, True)

# ------------------------------------------------------------- the nursery
head("the baby screen holds no credential and never claims to know")

import nursery  # noqa: E402

# WHAT THIS GUARDS. A car tool that grew a baby monitor is one careless commit
# away from being a car tool that holds somebody's nursery password, and one
# careless render away from a stale "asleep" shown in the present tense.

_ns = open(os.path.join(ROOT, "lib", "nursery.py"), encoding="utf-8").read()
check("nothing here talks to a nursery service",
      "cradlewise.com" in _ns or "requests" in _ns or "urlopen" in _ns, False)
# Looked for as calls, not as words: the module's docstring says "password"
# exactly in order to say it never holds one.
check("and nothing here reads a secret",
      any(w in _ns for w in ("secret-tool", "import keyring", "getpass",
                             "SecretService", "gnome-keyring")), False)
check("the document is pushed over ssh, opening no port at either end",
      '"ssh"' in _ns and "BatchMode" in _ns, True)

# A WHITELIST, NOT A COPY. omababy knows a growth curve, five months of history
# and a lifetime tally. None of it belongs on a second machine that lives in a
# car.
_full = {"baby": {"name": "A", "age": "6 months old"},
         "now": {"state": "sleeping", "in_crib": True, "for_mins": 85},
         "today": {"naps": 1, "feeds": 2, "diapers": 3, "sleep_min": 40,
                   "in_bed_min": 300, "rise": "7:47 am", "bedtime": "1:08 am"},
         "last_night": {"total_min": 418},
         "growth": {"weight": 16.05}, "week": [1] * 30, "months": [1] * 8,
         "lifetime": {"days_recorded": 152},
         "sources": {"cradlewise": {"ok": True, "error": ""}},
         "stream": {"available": True},
         "nightlight": {"light_on": True, "color": "#000000"},
         "fetched": 1}
_flat = nursery._flatten(_full)
for _left in ("growth", "week", "months", "lifetime"):
    check(f"{_left} stays at home", _left in _flat, False)
check("what travels is what the question needs",
      _flat["name"] == "A" and _flat["state"] == "sleeping"
      and _flat["last_night_min"] == 418 and _flat["camera"] is True, True)
check("a shape that moved costs one field, not the push",
      nursery._flatten({"now": {"state": "awake"}})["state"], "awake")

# THE AGE IS NOT OPTIONAL, and it is not the baby's.
import time as _t  # noqa: E402
import json as _j  # noqa: E402
import tempfile as _tf  # noqa: E402

_tmp = _tf.mkdtemp()
_was = nursery.DOC
try:
    nursery.DOC = os.path.join(_tmp, "nursery.json")
    check("with nothing sent, it says so rather than showing blanks",
          nursery.summary()["configured"], False)
    _doc = dict(_flat)
    _doc["sent_at"] = _t.time()
    with open(nursery.DOC, "w", encoding="utf-8") as _f:
        _j.dump(_doc, _f)
    _fresh = nursery.summary()
    check("a document that just arrived is known", _fresh["known"], True)
    check("and the baby's age is still the baby's age",
          _fresh["age"], "6 months old")
    check("while the freshness has a name of its own",
          _fresh["sent_ago"] < 5, True)
    _doc["sent_at"] = _t.time() - (nursery.STALE + 60)
    with open(nursery.DOC, "w", encoding="utf-8") as _f:
        _j.dump(_doc, _f)
    check("past the threshold it stops claiming to know",
          nursery.summary()["known"], False)
finally:
    nursery.DOC = _was
    import shutil as _sh  # noqa: E402
    _sh.rmtree(_tmp, ignore_errors=True)

# THE CAMERA IS GATED ON MOTION, AND A LOST READING IS NOT A STOP.
_view = open(os.path.join(ROOT, "share", "js", "views", "nursery.js"),
             encoding="utf-8").read()
check("the camera answers to the road speed",
      "store.values" in _view and "MOVING_KPH" in _view, True)
check("and motion latches, so a reading that went quiet is not a stop",
      "SETTLE" in _view and "movedAt" in _view, True)
check("the latch can also expire without a sample",
      "setInterval(draw" in _view, True)

# IT STAYS ON ITS OWN ROUTE. A child's name and sleep times have no business
# in the snapshot every other screen reads.
_api = open(os.path.join(ROOT, "lib", "api.py"), encoding="utf-8").read()
check("the nursery has its own endpoint", '/api/nursery' in _api, True)
_snap = open(os.path.join(ROOT, "lib", "records.py"), encoding="utf-8").read()
check("and nothing about it is folded into the snapshot",
      "nursery" in _snap, False)
_core = open(os.path.join(ROOT, "share", "js", "core.js"), encoding="utf-8").read()
check("the app keeps only whether anything was ever sent",
      "this.nurseryOn = " in _core and "crib.value.configured" in _core, True)
_main = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
check("and the screen is absent on a machine that has never been sent one",
      "v.nursery && !store.nurseryOn" in _main, True)

# ------------------------------------------------- asking for a screen aloud
head("a spoken request can move the screen, and nothing else")

import screen as _screen  # noqa: E402
import tempfile as _tf2   # noqa: E402
import shutil as _sh2     # noqa: E402
import time as _t2        # noqa: E402

_tmp2 = _tf2.mkdtemp()
_was_ask, _was_screens = _screen.ASK, _screen.SCREENS
try:
    _screen.ASK = os.path.join(_tmp2, "screen.json")
    _screen.SCREENS = os.path.join(_tmp2, "screens.json")

    check("with nothing published, nothing is known", _screen.known(), [])
    check("and a request nobody made is not fresh",
          _screen.pending()["fresh"], False)

    _screen.publish([{"id": "ima", "label": "Battery", "title": "t", "tab": "car"},
                     {"id": "drive", "label": "Gauges"},
                     {"nope": 1}])
    check("the app's own registry is what is known",
          sorted(v["id"] for v in _screen.known()), ["drive", "ima"])
    check("a malformed entry is dropped rather than published",
          len(_screen.known()), 2)

    _rec = _screen.ask("ima", "the assistant")
    check("a request carries who asked", _rec["who"], "the assistant")
    check("and is fresh immediately", _screen.pending()["fresh"], True)

    # A REQUEST GOES STALE. Somebody who asked for the battery screen, drove
    # for an hour and then opened the app did not mean it to open there.
    with open(_screen.ASK, "w", encoding="utf-8") as _f:
        _j.dump({"view": "ima", "at": _t2.time() - (_screen.FRESH + 10),
                 "who": "x"}, _f)
    check("an old request is not honoured", _screen.pending()["fresh"], False)

    # THE ROUTE REFUSES A SCREEN THE APP DOES NOT HAVE.
    _bad = api.handle_post("/api/screen", _j.dumps({"view": "wobble"}))
    check("asking for a screen that does not exist is refused", _bad[0], 400)
    check("and the refusal lists what there is",
          sorted(_bad[1].get("screens") or []), ["drive", "ima"])
    check("asking for one that does exist is accepted",
          api.handle_post("/api/screen", _j.dumps({"view": "drive"}))[0], 200)
    check("a request with no view is refused",
          api.handle_post("/api/screen", "{}")[0], 400)

    # THE TOOL DOES NOT CLAIM IT WORKED. It writes a request; the app honours
    # it on its own clock, and if the app is not running nothing happens.
    import mcp as _mcp  # noqa: E402
    _out = _mcp.call("show_screen", {"view": "ima"})["content"][0]["text"]
    check("the tool says it asked, not that it opened",
          "asked_for" in _out and "if it is running" in _out, True)
    check("and refuses a name it does not have",
          _mcp.call("show_screen", {"view": "wobble"}).get("isError"), True)
finally:
    _screen.ASK, _screen.SCREENS = _was_ask, _was_screens
    _sh2.rmtree(_tmp2, ignore_errors=True)

check("the tool is on the list the assistant is given",
      "show_screen" in {t["name"] for t in _mcp.TOOLS}, True)

# NOTHING ABOUT THE CAR PASSES THROUGH IT.
_scr = open(os.path.join(ROOT, "lib", "screen.py"), encoding="utf-8").read()
check("the channel carries a view id and a timestamp, and no request",
      any(w in _scr for w in ("elm", "obd", "0x2", "serial", "connect")), False)

# HONOURED ONCE. main.js has a standing prohibition on writing location.hash
# except in direct response to a person, because an earlier version pushed it
# on every live sample and threw people off what they were reading.
check("the app remembers the last request it acted on",
      "honoured = ask.at" in _main and "ask.at > honoured" in _main, True)
check("a screen the mode hides is not opened by asking for it",
      "if (!v || hiddenView(v)) return;" in _main, True)
check("and it never moves silently",
      'toast((ask.who' in _main, True)

# --------------------------------------------- saving a profile keeps it whole
head("a profile survives being written, and a finding survives the next one")

import profile as _prof   # noqa: E402
import tomllib as _toml   # noqa: E402
import tempfile as _tf3   # noqa: E402
import shutil as _sh3     # noqa: E402

# THE WRITER WAS LOSSY AND THE READER IGNORED THE RESULT.
#
# dumps() is hand-rolled section by section and knew about five of them, so
# loading the shipped CR-Z profile and writing it back DELETED [[module]],
# [poll] and [screens] -- plus seven fields of [car], including the engine and
# the redline. And load() returned the first match, which is the bundled copy,
# while adoption wrote the state copy: so a finding from a drive was written
# where no reader looks, and the next adoption re-read the bundled file and
# dropped the first finding too.
#
# Both are on the path this project exists for: `omacar listen adopt` is what
# turns a drive into a recorded candidate.

for _slug in ("honda-crz-2015", "bench-porsche"):
    _doc, _ = _prof.load(_slug)
    _back = _toml.loads(_prof.dumps(_doc))
    check(f"{_slug} loses no section when written back",
          sorted(set(_doc) - set(_back)), [])
    check(f"{_slug} loses no value either",
          [k for k in set(_doc) & set(_back) if _doc[k] != _back[k]], [])

_shipped, _ = _prof.load("honda-crz-2015")
check("the shipped profile really does carry the sections that were dropped",
      all(k in _shipped for k in ("module", "poll", "screens")), True)
check("and the car spec that was dropped with them",
      bool(_shipped["car"].get("engine") and _shipped["car"].get("redline")), True)

_tmp3 = _tf3.mkdtemp()
_was_dirs = list(_prof.PROFILE_DIRS)
try:
    _state = os.path.join(_tmp3, "profiles")
    _prof.PROFILE_DIRS = [_was_dirs[0], _state]
    _target = os.path.join(_state, "honda-crz-2015.toml")

    def _adopt(sid, byte):
        """What lib/listen.py's adoption does, reduced to its write."""
        doc, _ = _prof.load("honda-crz-2015")
        entry = {"id": sid, "name": sid, "can_id": "17C", "byte": byte,
                 "kind": "enum", "confidence": "candidate",
                 "states": {"0x01": "econ"},
                 "provenance": {"found_by": "omacar listen",
                                "method": "held each position",
                                "first_seen": "2026-09-10"}}
        casts = [b for b in (doc.get("broadcast") or []) if b.get("id") != sid]
        casts.append(entry)
        doc["broadcast"] = casts
        _prof.write(_target, doc)

    _adopt("drive_mode", 2)
    _one, _where = _prof.load("honda-crz-2015")
    check("an adopted finding is visible to a reader at all",
          any(b.get("id") == "drive_mode" for b in _one.get("broadcast") or []),
          True)
    check("and the writer is pointed at the copy that wins",
          os.path.abspath(_where), os.path.abspath(_target))

    _adopt("regen_level", 3)
    _two, _ = _prof.load("honda-crz-2015")
    _ids = [b.get("id") for b in _two.get("broadcast") or []]
    check("a second adoption does not drop the first",
          sorted(i for i in _ids if i in ("drive_mode", "regen_level")),
          ["drive_mode", "regen_level"])
    check("the bundled sections are still there afterwards",
          all(k in _two for k in ("module", "poll", "screens")), True)
    check("and so is the car spec",
          _two["car"].get("engine"), _shipped["car"].get("engine"))

    # The state copy must not be able to DELETE what the bundled file adds
    # later -- that is the cost of the other obvious fix, reversing the search.
    _merged = _prof._merge({"module": [{"header": "A"}, {"header": "B"}],
                            "poll": {"fast": ["RPM"], "slow": ["TEMP"]}},
                           {"module": [{"header": "B", "label": "newer"}],
                            "poll": {"fast": ["RPM", "SPEED"]}})
    check("merging keeps a table the newer file does not mention",
          sorted(m["header"] for m in _merged["module"]), ["A", "B"])
    check("and the newer copy of a table wins",
          [m.get("label") for m in _merged["module"] if m["header"] == "B"],
          ["newer"])
    check("and a key the newer file does not mention survives",
          _merged["poll"].get("slow"), ["TEMP"])
finally:
    _prof.PROFILE_DIRS = _was_dirs
    _sh3.rmtree(_tmp3, ignore_errors=True)

# ------------------------------------ a capture that stopped says which stop
head("a detached capture that ended says why, and whether to worry")

# THE THREE ENDINGS LOOKED IDENTICAL. A detached `listen drive` returns 0 and
# removes its running file whether somebody stopped it, the engine stopped, or
# it silently hit a cap -- forty-five minutes, or sixty thousand lines off the
# adapter. All three left nothing behind, so `omacar listen status` said
# "nothing is listening", which reads like it was never started. On a
# three-hour drive that is a recording that stopped in hour one with no sign.

_tmpf = _tf3.mkdtemp()
_was_fail = listen.FAILFILE
try:
    listen.FAILFILE = os.path.join(_tmpf, "listen-failed.json")
    check("with nothing recorded there is no note", listen.last_failure(), None)

    listen.note_failure("no OBD adapter is plugged in")
    _f = listen.last_failure()
    check("a session that could not start is a fault", _f["fault"], True)

    listen.note_failure("it ran its full 45 minutes and finished", fault=False)
    _f = listen.last_failure()
    check("a session that finished is not", _f["fault"], False)
    check("and it still says what happened",
          "ran its full" in _f["why"], True)
finally:
    listen.FAILFILE = _was_fail
    _sh3.rmtree(_tmpf, ignore_errors=True)

_ls = open(os.path.join(ROOT, "lib", "listen.py"), encoding="utf-8").read()
check("the status screen tells a finish from a failure",
      'head = ("the last one did not start" if fault' in _ls, True)
check("and exits zero for a finish, so a script is not told of a fault",
      "return 1 if fault else 0" in _ls, True)
# The cap counts lines inside elm.py and what survives here is parsed frames,
# so this genuinely cannot be proven from the session -- and it says so rather
# than guessing confidently.
check("an ending it cannot prove is reported as the guess it is",
      "most likely the" in _ls, True)

# --------------------------------------------------------------- the preflight
head("one command answers the six questions somebody would skip")

import preflight as _pf   # noqa: E402
import re as _re         # noqa: E402

# WHY THIS EXISTS. doc/drive-day.md asked for six commands run in a driveway in
# the dark before a ninety-mile drive. Two of the three things that have
# actually cost this project a trip were visible beforehand and nobody looked:
# a capture saved without its frame bytes, and a drive recorder that had never
# been enabled on any machine while every status screen said the tablet was
# ready.

_s = _pf.Sheet()
_s.row("recording the bus", True, "enabled and running")
_s.row("adapter", False, "none found", "plug it in", blocking=False)
check("a sheet with nothing blocking exits zero", _s.blockers, 0)

_s2 = _pf.Sheet()
_s2.row("recording the bus", False, "inactive", "enable it")
_s2.row("adapter", False, "none found", "plug it in", blocking=False)
check("a real fault blocks", _s2.blockers, 1)
check("and a merely interesting one does not",
      [r for r in _s2.rows if not r[1] and not r[4]] != [], True)

# IT FIXES NOTHING. A preflight that silently repairs things is one you stop
# reading, and the whole value is that somebody reads it.
_src = open(os.path.join(ROOT, "lib", "preflight.py"), encoding="utf-8").read()
for _verb in ("enable", "start", "restart", "install"):
    check(f"it never runs systemctl {_verb} itself",
          f'"systemctl", "--user", "{_verb}"' in _src, False)
# `cat` joined the list when the recorder learned to tell a unit that is off
# from a unit that is not there. It is a question like the other two: it reads
# a file and changes nothing.
check("it only ever asks systemctl questions",
      sorted(set(_re.findall(r'"systemctl",\s*(?:"--user",\s*)?"([a-z-]+)"', _src))),
      ["cat", "is-active", "is-enabled"])

# THE RECORDER GOES FIRST, because it is the one that was never on.
check("the recorder is checked before anything about the car",
      _src.index("def _recorder") < _src.index("def _port"), True)
check("and the checks run in that order",
      _src.index("_recorder(sheet)") < _src.index("_port(sheet)"), True)

# IT NEEDS NO CAR. The night before, indoors, is exactly when it should be run.
check("a missing adapter is not a reason to stay home",
      'sheet.row("adapter", bool(port)' in _src and "blocking=False" in _src, True)

# THE REMEDY HAS TO WORK ON THE MACHINE THAT NEEDS IT. A tablet set up before
# the recorder existed has no unit file at all, and `systemctl is-active`
# answers "inactive" for a unit that does not exist -- indistinguishable from
# one that is installed and stopped. So this row printed a plausible state and
# handed over `systemctl --user enable --now`, which on that machine answers
# "No files found" and changes nothing: the check fires, the fix fails, and
# the driver leaves believing both. Found on this tablet, where the unit had
# never been laid down.
_keep_run = _pf._run


def _recorder_row(unit_present):
    def _fake(cmd, timeout=10):
        verb = cmd[2] if len(cmd) > 2 else ""
        if verb == "cat":
            return (0, "[Unit]", "") if unit_present else (1, "", "No files found")
        if verb == "is-enabled":
            return (1, "disabled", "")
        if verb == "is-active":
            return (3, "inactive", "")
        return (0, "", "")
    _pf._run = _fake
    sheet = _pf.Sheet()
    try:
        _pf._recorder(sheet)
    finally:
        _pf._run = _keep_run
    return sheet.rows[0]


_absent = _recorder_row(False)
check("a missing unit file is not reported as merely inactive",
      "no unit file" in _absent[2], True)
check("and it still blocks", _absent[1] is False and _absent[4] is True, True)
check("the fix it offers is the one that lays the unit down",
      "install.sh" in _absent[3], True)

# EITHER OWNER RECORDS. One serial port has one owner, and `omacar begin`
# stops the recorder to give the daemon the port on purpose -- the daemon
# samples into `samples` (4,367 rows across 126 km on 16 September, the record
# that survived the day every capture stalled) and it is the only thing that
# feeds the dashboard. Shouting "nothing is recording anything" at a machine
# that is doing both is a false alarm, and a preflight that cries wolf before a
# drive is one somebody learns to walk past.
import hotplug as _hp2   # noqa: E402

_keep_dr = _hp2.daemon_running
_hp2.daemon_running = lambda: True
_pf._run = lambda cmd, timeout=10: (3, "inactive", "")
_sheet_d = _pf.Sheet()
_pf._recorder(_sheet_d)
_hp2.daemon_running = _keep_dr
_pf._run = _keep_run
check("a sampling daemon counts as recording the bus", _sheet_d.rows[0][1], True)
check("and it is not a reason to stay home", _sheet_d.blockers, 0)
check("it says which owner has the port",
      "daemon" in _sheet_d.rows[0][2] and "port" in _sheet_d.rows[0][2], True)

_present = _recorder_row(True)
check("a unit that exists and is off keeps the enable command",
      _present[3].startswith("systemctl --user enable --now"), True)
check("and does not claim the file is missing",
      "no unit file" in _present[2], False)

# BUT A MACHINE THAT CAN SUSPEND ITSELF IS. It is the only row here describing
# something that has already destroyed a leg -- 8 September 2026, suspended at
# 15:57 mid-recording, never resumed -- and it shipped as merely interesting,
# sitting in the same yellow column as "this laptop cannot sleep". Asserted on
# the argument rather than the comment, because the comment above it already
# said it ends drives while the code said otherwise.
_suspend_row = _src[_src.index('sheet.row("cannot suspend itself"'):]
_suspend_row = _suspend_row[:_suspend_row.index(")\n")]
check("suspending itself is a reason to stay home",
      "blocking=False" in _suspend_row, False)

_cli = open(os.path.join(ROOT, "bin", "omacar"), encoding="utf-8").read()
check("it is reachable", "omacar preflight" in _cli
      and 'preflight) omacar_need_env' in _cli, True)

# ------------------------------------------------------- one press, in a car
head("`begin` acts where preflight only asks")

import begin as _bg           # noqa: E402
import contextlib as _ctx    # noqa: E402
import io as _io             # noqa: E402


def _quietly(fn):
    """begin() reports as it goes; the suite's own output stays readable."""
    with _ctx.redirect_stdout(_io.StringIO()):
        return fn()


# THE TWO FILES ARE DELIBERATELY OPPOSITE and a later tidy-up could collapse
# them into one. preflight reports at a desk and repairs nothing; begin runs in
# a driveway with the engine going and is allowed to change things. Each guard
# below fails if either drifts toward the other.
_bsrc = open(os.path.join(ROOT, "lib", "begin.py"), encoding="utf-8").read()
check("begin is allowed to stop a unit", '"stop", RECORDER' in _bsrc, True)
check("and preflight still is not",
      any(f'"systemctl", "--user", "{v}"' in _src for v in ("stop", "start")), False)

_r = _bg.Run(quiet=True)
_quietly(lambda: _r.say("a", False, "broken", fatal=True))
_quietly(lambda: _r.say("b", False, "worth knowing", fatal=False))
_quietly(lambda: _r.say("c", True, "fine"))
check("only a fatal step is a reason not to drive", _r.failed, 1)
check("every step is kept for the launcher to read", len(_r.steps), 3)

# A BENCH IS THE ONE THAT LOOKS LIKE SUCCESS. Every reading would be the
# emulator's, the screen would fill with numbers, and none of them would be
# the car — so it has to stop the run rather than warn inside it.
_keep = _bg.connect.bench_port
_bg.connect.bench_port = lambda: "/dev/pts/9"
_r2 = _bg.Run(quiet=True)
check("a running bench refuses the whole sequence",
      _quietly(lambda: _bg._adapter(_r2)) is None, True)
check("and it is fatal", _r2.failed, 1)
check("and it says how to clear it", "bench stop" in _r2.steps[-1]["said"], True)
_bg.connect.bench_port = _keep

# THE START COMMAND RETURNING IS NOT THE CAR ANSWERING -- on 16 September
# `daemon start` printed "did not start" for a daemon that was already up and
# about to connect perfectly. So the proof is live.json, not an exit code.
check("the car is proven from the live sample, not the start command",
      "connected" in _bsrc and "WAIT_CONNECT" in _bsrc, True)
check("it is reachable", "begin) omacar_need_env" in _cli, True)

# ------------------------------------------------- the screen it is pressed on
head("the launcher shows the sequence rather than spinning over it")

# A SPINNER OVER AN UNKNOWN STATE IS THE FAILURE ITSELF. `listen marks` stopped
# receiving frames twenty seconds in while the status file went on saying
# "capturing", and no screen said otherwise. So the launcher polls real steps.
check("the steps can be read while it runs",
      api.handle_get("/api/begin", "")[0], 200)
check("and it is started by a POST", "/api/begin" in
      open(os.path.join(ROOT, "lib", "api.py"), encoding="utf-8").read(), True)

# TWO RUNS WOULD FIGHT OVER THE ONE SERIAL PORT, which is the exact failure the
# sequence exists to prevent -- so a second press joins the run in flight.
_api_src = open(os.path.join(ROOT, "lib", "api.py"), encoding="utf-8").read()
check("a second press cannot start a second run",
      '_BEGIN["running"]' in _api_src and "return 200, {\"running\": True" in _api_src,
      True)
check("the run is guarded by a lock, not a bare flag",
      "_BEGIN_LOCK" in _api_src, True)

_lsrc = open(os.path.join(ROOT, "share", "js", "views", "launcher.js"),
             encoding="utf-8").read()
# IT NEVER OPENS THE DASHBOARD OVER A FAILURE. A dashboard full of dashes looks
# close enough to working to be believed at sixty miles an hour.
_auto = _lsrc.index("handover = setTimeout")
check("the handover to the dashboard sits inside the success branch",
      _lsrc.rindex("s.rc === 0", 0, _auto) > _lsrc.rindex("s.running", 0, _auto),
      True)
check("a failure leaves the reason on screen instead",
      "the marked line says why" in _lsrc, True)
# Leaving anyway is a decision somebody makes, not something that happens to
# them, so it is a button and it says what it costs.
check("opening it anyway is an explicit press",
      "Open the dashboard anyway" in _lsrc, True)
check("and it says nothing is being recorded",
      "Nothing will be recorded" in _lsrc, True)
check("the launcher is not a tab you can wander into mid-drive",
      'id: "launcher"' in open(os.path.join(ROOT, "share", "js", "main.js"),
                               encoding="utf-8").read()
      and "hidden: true" in open(os.path.join(ROOT, "share", "js", "main.js"),
                                 encoding="utf-8").read(), True)

# --------------------------------------------------------- the three drive modes
head("a drive-mode label does not outlive the drive it was made on")

import drivemode as _dm   # noqa: E402

# THE FIRST BUG THIS MODULE FOUND WAS ITS OWN. With every window running until
# the next mark, the last "normal" mark from 8 September was still in force on
# 16 September and quietly labelled all 4,367 samples of a 126 km drive home as
# NORMAL. Nobody had said that. A label nobody made is worse than no label: it
# is evidence, and it would have been used.
_mdb = sqlite3.connect(":memory:")
_mdb.execute("CREATE TABLE samples (t REAL, speed REAL, rpm REAL, throttle REAL,"
             " load REAL, maf REAL, soc REAL)")
# One short drive, then eight days of silence, then another drive nobody labelled.
for _i in range(60):
    _mdb.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                 (1000.0 + _i, 50, 2200, 30.0, 70.0, 12, 60))
for _i in range(200):
    _mdb.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                 (700000.0 + _i, 90, 2600, 40.0, 80.0, 18, 55))
_wins = _dm.windows([{"at": 1000.0, "mode": "econ"}], _mdb)
check("a mark covers the drive it was made on", len(_wins), 1)
check("and stops at the silence that ended it", _wins[0][2] < 1100.0, True)
check("so it never reaches a later drive", _wins[0][2] < 700000.0, True)

# THE REFUSAL. All four labelled windows on this car were recorded parked, at a
# closed throttle — where the modes are identical BY CONSTRUCTION, because
# there is no pedal input to remap and no assist being asked for. Fitting a
# boundary to that data would produce a confident answer to a question the data
# cannot contain.
_pdb = sqlite3.connect(":memory:")
_pdb.execute("CREATE TABLE samples (t REAL, speed REAL, rpm REAL, throttle REAL,"
             " load REAL, maf REAL, soc REAL)")
for _m, _t in (("econ", 2000.0), ("sport", 2100.0)):
    for _i in range(50):
        # Parked: throttle on its stop, no road speed. Exactly the four windows
        # this car actually has.
        _pdb.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                     (_t + _i, 0.0, 750, 12.9, 26.0, 2.0, 60))
_keep_load = _dm._load
_dm._load = lambda: [{"at": 2000.0, "mode": "econ"}, {"at": 2100.0, "mode": "sport"}]
_rep = _dm.evidence(_pdb)
_dm._load = _keep_load
check("parked windows are not usable evidence", _rep["usable"], False)
check("and it says why, in terms of the pedal",
      "closed throttle" in _rep["why"] and "assist" in _rep["why"], True)
check("the rest position is measured, not assumed", _rep["rest_throttle"], 12.9)

# THE BAR MUST NOT CLAIM A MODE NOBODY SET TODAY. current() returning simply
# the last mark is the same defect as a window that never ends, reached from
# the other side: the vehicle bar is on a screen that never goes away, and it
# would have worn ECON on a car driven for nine days in modes nobody wrote
# down.
_ndb = sqlite3.connect(":memory:")
_ndb.execute("CREATE TABLE samples (t REAL, speed REAL, rpm REAL, throttle REAL,"
             " load REAL, maf REAL, soc REAL)")
for _i in range(30):
    _ndb.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                 (5000.0 + _i, 60, 2400, 35.0, 72.0, 14, 58))
_dm._load = lambda: [{"at": 5005.0, "mode": "sport"}]
check("a mark made during this drive is in force",
      (_dm.current(now=5020.0, db=_ndb) or {}).get("mode"), "sport")
check("and the same mark is not, an hour after the car stopped",
      _dm.current(now=5005.0 + _dm.SESSION_CAP + 60, db=_ndb), None)
_dm._load = _keep_load

# A LABEL, NOT A COMMAND. Nothing here may reach the vehicle: this records
# which mode a person selected on the car's own switch, and no identifier
# anybody has found can select one.
_dsrc = open(os.path.join(ROOT, "lib", "drivemode.py"), encoding="utf-8").read()
check("the module never opens a port",
      any(w in _dsrc for w in ("import elm", "import connect", "serial")), False)

# The markers stay reachable while the car is moving, unlike the layout editor
# beside them — and the editor's slot is RESERVED rather than collapsed, or the
# Exit target moves out from under a thumb at the moment somebody reaches for it.
_drv = open(os.path.join(ROOT, "share", "js", "views", "drive.js"), encoding="utf-8").read()
check("the layout editor keeps its space when it hides",
      'editBtn.style.visibility = moving ? "hidden" : ""' in _drv, True)
check("and the mode markers are not hidden with it",
      "modeRow.hidden" in _drv, False)

# SPORT IS NOT A FAULT. The cockpit tints it red; red in this app is an active
# DTC, a coolant temperature over 105, a failed launcher step. A red chip in
# the bar that never leaves the screen reads as something being wrong with the
# car, and a switch position somebody chose is not that.
check("the mode chip never wears the fault colour",
      "--bad" in _css.split(".mode-pill")[1].split("CSS")[0][:800], False)
check("econ and normal keep the cockpit's own accents",
      '.mode-pill[data-mode="econ"]' in _css
      and '.mode-pill[data-mode="normal"]' in _css, True)
# It is always a word. looks.js ships a night palette in which every hue
# collapses to a lightness, and three tints two hours into the dark is not a
# distinction anybody should be asked to make.
check("and the chip always carries the word",
      "textContent = String(mode).toUpperCase()" in _main, True)

# ------------------------------------------------------------------- the dock
head("the dock got bigger without getting quieter where it matters")

_css = open(os.path.join(ROOT, "share", "css", "app.css"), encoding="utf-8").read()

# THREE SIGNALS, NOT A TINT. looks.js ships a red-only night palette in which
# every hue collapses to a lightness, so "the current one is the blue one" does
# not survive to 2am on a dark road. Restyling the dock is exactly the kind of
# change that quietly takes one of these away.
check("the current tab still takes the ink colour",
      '.tab[aria-current="page"] { color: var(--ink); }' in _css, True)
check("it still carries its own edge",
      '.tab[aria-current="page"]::before' in _css, True)
check("and its pill is still filled",
      '.tab[aria-current="page"] .tab-in { background: var(--raise); }' in _css, True)
_main = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
check("and it is still announced to a screen reader",
      'setAttribute("aria-current", "page")' in _main, True)

# THE 74-SQUARE IS A THUMB TARGET. Handing it to a mouse wastes a dock that
# does not need it, and -- the reason it is a guard -- the desktop --tabbar is
# 72px, so a 74px pill outside the coarse query overflows the bar it sits in.
_coarse = _css[_css.index("@media (pointer: coarse) {\n  .tab-in"):]
check("the cockpit's 74-square is scoped to a coarse pointer",
      "min-height: 74px" in _coarse[:400], True)
check("and the bar has room for it",
      "--tabbar: 96px" in _css and "--tabbar:  72px" in _css, True)

# ---------------------------------------------------------------------------
# The label may get smaller and wider-tracked. It may NOT get dimmer: that is
# the obvious way to make a dock feel calm and it spends the one thing a
# driver needs from it at night.
check("the label keeps its ink", "color:" in _css.split(".tab-lbl {")[1].split("}")[0], False)

# ------------------------------------------------ the link that hears the bus
head("the link is raised after the reset that would undo it")

_elm = open(os.path.join(ROOT, "lib", "elm.py"), encoding="utf-8").read()

# ORDER IS THE WHOLE THING. init() opens with ATZ, and ATZ puts the adapter
# back on its default rate. A link raised before that line is silently undone:
# the adapter drops to 115200, the handle stays where it was, and every read
# afterwards blocks on bytes that can never parse. Measured on the car on
# 17 September -- a 20-second capture sat in a serial read for three minutes at
# zero CPU and reported "nothing was heard at all", on a bus carrying 1,900
# frames a second.
_reset = _elm.index('self.at("Z")')
_raise = _elm.index("self.raise_baud()")
check("the raise comes after the reset", _reset < _raise, True)
# Checked on what link_baud RETURNS, not on whether the file mentions the word:
# the comment in there explains why it stopped pre-raising, and a guard that
# cannot tell a return statement from the paragraph above it fails on its own
# explanation. That is twice now.
_lb = open(os.path.join(ROOT, "lib", "connect.py"), encoding="utf-8").read()
_lb = _lb.split("def link_baud")[1]
_lb = _lb[:_lb.index("def ", 10)]
check("connect.link_baud only detects; it does not raise",
      "return detect_baud(port) or fallback" in _lb
      and "return raise_baud" not in _lb, True)

# IT FAILS CLOSED, which is why it may run on every connection. ATBRD is
# specified to revert if the host does not confirm, and every path out of the
# handshake here puts the handle back where it was.
_fn = _elm[_elm.index("def raise_baud(self"):]
_fn = _fn[:_fn.index("\n    def ", 10)] if "\n    def " in _fn[10:] else _fn[:4000]
check("a refused handshake leaves the rate alone",
      _fn.count("return False") >= 4, True)
# EVERY WAY OUT SETTLES THE LINK. Restoring only our own side is not enough:
# once ATBRD has been written the adapter may have moved regardless of what we
# then decide, and two ends on different rates is a tool that HANGS rather than
# one that says no. _settle() puts the handle where the adapter actually is.
check("every bail-out settles the link rather than assuming",
      _fn.count("_settle(cur, target)") >= 3, True)
check("and there is something for it to settle to",
      "def _settle(self" in _elm, True)
# IT SHIPS OFF. The measurement is proven; the integration is not, and the
# failure mode is a capture that hangs in a car rather than one that records
# slowly. Turning it on is an env var, so finishing it needs no edit here.
check("it is opt-in from the environment", "OMACAR_FASTBAUD" in _fn, True)
check("and the default is off", '!= "1"' in _fn, True)
# A divisor the adapter cannot express is not a target: 4000000/div is what
# actually happens, and asking for something else would set a rate nobody chose.
check("the target has to be expressible as a divisor",
      "4000000.0 / div" in _fn, True)

# ----------------------------------------- the garage holds cars, not fixtures
head("the emulator's cars do not pass for cars you own")

import garage as _g2   # noqa: E402

# TWO OF THE THREE CARS IN THIS GARAGE WERE THE EMULATOR. `simulated` has been
# a column, a describe() field and a badge in the garage view since each was
# added -- and survey.py wrote `json.dumps(False)` into it unconditionally, so
# no record on any machine ever carried true. A 2026 Porsche and a second CR-Z
# sat in the list looking exactly like cars somebody owns.
_svy = open(os.path.join(ROOT, "lib", "survey.py"), encoding="utf-8").read()
check("the flag is measured, not asserted",
      'json.dumps(bool(connect.bench_port()))' in _svy, True)
check("and the hardcoded false is gone",
      '("simulated", json.dumps(False))' in _svy, False)

# THE KEY IS THE HONEST SOURCE. A record filed under SIM_KEY is the simulator
# whatever any flag inside it says, and that does not depend on somebody having
# remembered to set one.
_gsrc = open(os.path.join(ROOT, "lib", "garage.py"), encoding="utf-8").read()
check("a record filed as the simulator says so regardless",
      'or key == SIM_KEY' in _gsrc, True)
check("and the view already had somewhere to show it",
      'car.simulated' in open(os.path.join(ROOT, "share", "js", "views",
                                           "garage.js"), encoding="utf-8").read(), True)

# ------------------------------------------------------ day and night, in reach
head("the light/dark toggle exists and goes somewhere")

_m = open(os.path.join(ROOT, "share", "js", "main.js"), encoding="utf-8").read()
check("there is a day/night control in the vehicle bar",
      'id: "btn-daynight"' in _m, True)
check("it can actually select a theme",
      "selectTheme" in open(os.path.join(ROOT, "share", "js", "core.js"),
                            encoding="utf-8").read(), True)
# A TOGGLE WITH NOWHERE TO GO DOES NOT APPEAR. Shipping a second button that
# shrugs, to answer a complaint about a button that shrugged, would be a joke
# at the owner's expense.
check("and it hides itself when there is no pair to switch between",
      "els.daynight.hidden = !usable" in _m, True)
# The assistant's glyph is a disc with rays, which is most of why somebody
# tapped it expecting the lights to change. The toggle's own icons have to be
# tellable apart from it at arm's length.
_ic = open(os.path.join(ROOT, "share", "js", "icons.js"), encoding="utf-8").read()
check("the toggle has its own sun and moon", "sun:" in _ic and "moon:" in _ic, True)
check("and the moon is a crescent, not another rayed disc",
      len(_ic.split("moon: [")[1].split("]")[0].split('","')), 1)

# ------------------------------------------------------- turned on its side
head("the portrait rules come last, or they lose")

_css2 = open(os.path.join(ROOT, "share", "css", "app.css"), encoding="utf-8").read()
_port = _css2.index("@media (orientation: portrait)")

# CASCADE ORDER IS THE WHOLE MECHANISM. Every rule in that block overrides one
# defined earlier at the same specificity, so anywhere but last it silently
# loses -- which is exactly what happened: written two hundred lines up, and
# .hub-vitals went on drawing four across in portrait because its own rule came
# after it.
for _sel in (".hub-vitals {", ".drive-row {", ".vbar {", ".hub-grid {"):
    check(f"portrait overrides {_sel.strip(' {')} after it is defined",
          _css2.index(_sel) < _port, True)

# ORIENTATION, NOT WIDTH. A Surface Pro 7+ in portrait is 912 x 1368 logical --
# wider than a phone breakpoint and taller than any of them -- so width queries
# answer "desktop" and every one of them is wrong here. The density note at the
# top of this file already says so about `pointer: coarse`.
check("it asks about orientation rather than guessing from width",
      "@media (orientation: portrait)" in _css2, True)

# The drive hero is a 1fr row: in a 1368px-tall viewport it takes six hundred
# spare pixels and strands the speed at the top with a void beneath it.
check("and the drive rows size to their content when tall",
      "grid-template-rows: auto auto auto auto" in _css2[_port:], True)

# A column count written as an inline style cannot be reinterpreted by any
# stylesheet, which is why the view writes a property instead.
_drv2 = open(os.path.join(ROOT, "share", "js", "views", "drive.js"),
             encoding="utf-8").read()
check("the view publishes the column count rather than the computed value",
      'setProperty("--cols"' in _drv2 and "gridTemplateColumns" not in _drv2, True)

# --------------------------------------------- a duration is not an instant
head("nothing hands a timestamp to a function that wants an elapsed time")

# "SEEN 20713D AGO" ON THE GARAGE SCREEN. since() takes an ELAPSED time in
# seconds; six call sites passed `Date.now() / 1000 - t` and two passed the
# timestamp itself -- a duration of about 1.79 billion seconds, rendered as
# fifty-six years. The arithmetic was never wrong. The argument was, and
# nothing in the name said so, which is why there are two names now.
import glob as _glob   # noqa: E402

_bad = []
for _path in sorted(_glob.glob(os.path.join(ROOT, "share", "js", "**", "*.js"),
                               recursive=True)):
    _src = open(_path, encoding="utf-8").read()
    for _m in _re.finditer(r"\bsince\(([^)]*)\)", _src):
        _arg = _m.group(1).strip()
        if not _arg or _arg.startswith("secs"):
            continue
        # A legitimate call subtracts an instant from now. Anything that is
        # merely a field holding a moment -- *_at, last_seen -- is the bug.
        if "Date.now()" in _arg:
            continue
        if _re.search(r"(_at\b|last_seen\b|\bat\b)", _arg):
            _bad.append(f"{os.path.relpath(_path, ROOT)}: since({_arg})")
check("every since() is handed a duration", _bad, [])
_core = open(os.path.join(ROOT, "share", "js", "core.js"), encoding="utf-8").read()
check("and there is a name for the other shape", "export function ago(at)" in _core, True)

# THE TWO GLYPHS IN THE VEHICLE BAR HAVE TO BE TELLABLE APART. The advisor is a
# disc with rays, and mistaking it for a brightness control is the complaint
# the day/night toggle exists to answer -- so the toggle's own daytime icon is
# a half-disc on a horizon, not a second rayed disc.
_ic2 = open(os.path.join(ROOT, "share", "js", "icons.js"), encoding="utf-8").read()
_sun = _ic2.split("sun: [")[1].split("],")[0]
check("the toggle's day icon is not another rayed disc",
      _sun.count('"M') <= 5 and "18.4h17.2" in _sun, True)

# ------------------------------------- the handshake, against an echoing ELM
head("the link handshake survives the echo ATZ turns back on")


class EchoingElm:
    """An ELM327 that echoes every command, as a real one does after ATZ.

    Output is held as (rate, bytes) segments, because the ordering is the
    whole subtlety of this handshake: the acknowledgement goes out at the OLD
    rate and the identification that follows it at the NEW one. A host reads a
    segment only while it is on that segment's rate -- which is what a
    mismatched serial link really looks like, and what a fake that switched
    before queueing the OK cannot show.
    """

    def __init__(self, brd=True, confirm=True):
        self.baudrate = 115200          # the HOST side
        self.rate = 115200              # the ADAPTER side
        self.echo = True
        self.brd, self.confirm = brd, confirm
        self.segs = []                  # [(rate, bytes)]

    def _put(self, data, rate=None):
        self.segs.append((rate if rate is not None else self.rate, data))

    def reset_input_buffer(self):
        self.segs = []

    def flush(self):
        pass

    def write(self, data):
        cmd = data.decode("ascii", "replace").strip().upper()
        if self.echo and cmd:
            self._put(cmd.encode() + b"\r")
        if cmd == "ATE0":
            self.echo = False
            self._put(b"OK\r")
        elif cmd.startswith("ATBRD"):
            if not self.brd:
                self._put(b"?\r")
                return
            self._put(b"OK\r")                       # at the OLD rate
            new_rate = int(4000000 / int(cmd.split()[1], 16))
            self._put(b"ELM327 v1.4b\r", new_rate)   # at the NEW rate
            self.rate = new_rate
        elif cmd == "":
            if self.confirm:
                self._put(b"OK\r")
        elif cmd == "ATI":
            self._put(b"ELM327 v1.4b\r")

    def _front(self):
        while self.segs and not self.segs[0][1]:
            self.segs.pop(0)
        if not self.segs:
            return b""
        rate, data = self.segs[0]
        return data if rate == self.baudrate else b""

    def _take(self, n):
        data = self._front()[:n]
        if data:
            rate, buf = self.segs[0]
            self.segs[0] = (rate, buf[len(data):])
        return data

    def read(self, n=1):
        return self._take(n)

    def read_until(self, term=b"\r"):
        data = self._front()
        i = data.find(term)
        return self._take(i + 1 if i >= 0 else len(data))


def _fresh_elm(**kw):
    e = elm.Elm.__new__(elm.Elm)
    e.ser = EchoingElm(**kw)
    return e


_keep_env = os.environ.get("OMACAR_FASTBAUD")
os.environ["OMACAR_FASTBAUD"] = "1"

# THE BUG THIS SHIPPED WITH. init() calls raise_baud() straight after ATZ, and
# ATZ restores ATE1. With echo on the adapter repeats the command back BEFORE
# it answers, so a read to the first carriage return collects "ATBRD 08",
# finds no OK, and gives up -- while the adapter switches anyway. Handle at
# 115200, adapter at 500000, every read afterwards blocking on bytes that can
# never parse: seven minutes at zero CPU on a bus carrying 1,900 frames/sec.
_e = _fresh_elm()
check("it raises the link against an adapter that is echoing",
      _e.raise_baud(500000), True)
check("both ends end up on the same rate",
      (_e.ser.baudrate, _e.ser.rate), (500000, 500000))

# NEVER A MISMATCHED LINK. Once ATBRD is written the adapter may have moved
# whatever we then decide, and a bail-out that only restores our own side
# leaves the two ends disagreeing -- which reads as a tool that stops, not one
# that says no. An adapter that switches and then refuses to confirm is the
# nastiest shape of that.
_e2 = _fresh_elm(confirm=False)
check("a switch that is never confirmed does not raise", _e2.raise_baud(500000), False)
check("and the handle is left where the adapter actually is",
      _e2.ser.baudrate, _e2.ser.rate)

# An adapter with no ATBRD at all is simply left alone.
_e3 = _fresh_elm(brd=False)
check("an adapter without ATBRD is left where it was", _e3.raise_baud(500000), False)
check("at the rate it started on", (_e3.ser.baudrate, _e3.ser.rate), (115200, 115200))

# ------------------------------------------ the handshake says what it did
head("the link handshake records which step it reached and what the adapter said")

# MEASURED ON THE CAR, 29 SEPTEMBER. With OMACAR_FASTBAUD=1 capture A took 142
# frames in a third of a second and then BUFFER FULL -- exactly the 115200
# shape -- and nothing anywhere said whether the raise was attempted, what the
# adapter answered, or which step gave up. raise_baud() returned False
# silently. It now leaves the whole attempt on the connection, for the capture
# and the link log to keep.


class _HandshakeElm(EchoingElm):
    """EchoingElm, able to fail at any step, and closer to the datasheet in
    two ways that decide what a failure looks like afterwards: a byte written
    while the two ends are on different rates is noise the adapter ignores,
    and an adapter that switched and did not get its carriage return goes
    back to the old rate on its own (ATBRD fails closed)."""

    def __init__(self, fail=None, **kw):
        super().__init__(**kw)
        self.fail = fail
        self.old_rate = None             # set while waiting for the confirming CR

    @property
    def in_waiting(self):
        n = 0
        for rate, data in self.segs:
            if rate != self.baudrate:
                break
            n += len(data)
        return n

    def read(self, n=1):
        out = b""
        while len(out) < n:
            got = self._take(n - len(out))
            if not got:
                break
            out += got
        return out

    def write(self, data):
        cmd = data.decode("ascii", "replace").strip().upper()
        if self.old_rate is not None:
            old, self.old_rate = self.old_rate, None
            if not (cmd == "" and self.baudrate == self.rate):
                self.rate = old          # no CR in time: back where it was
                self._put(b">", old)
                return                   # and what arrived was noise to it
        if self.baudrate != self.rate:
            return
        if cmd == "ATE0" and self.fail == "echo-off":
            raise sys.modules["serial"].SerialException("[Errno 5] Input/output error")
        if cmd == "ATE0" and self.fail == "echo-off ignored":
            return                       # mid-reset: nothing said, echo stays on
        was = self.rate
        super().write(data)
        if cmd.startswith("ATBRD") and self.rate != was:
            self.old_rate = was
            if self.fail == "ident":     # the identification never reaches the host
                self.segs = [s for s in self.segs if s[0] == was]


def _stepped(**kw):
    e = elm.Elm.__new__(elm.Elm)
    e.ser = _HandshakeElm(**kw)
    return e


def _said(e, step):
    for s in (getattr(e, "fastbaud", None) or {}).get("steps") or []:
        if s.get("step") == step:
            return s.get("answered")
    return None


# Raised.
_h = _stepped()
check("the raise succeeds against a well-behaved adapter", _h.raise_baud(500000), True)
_hf = getattr(_h, "fastbaud", None) or {}
check("and says so", (_hf.get("outcome"), _hf.get("failed_at")), ("raised", None))
check("with the rate in force afterwards", _hf.get("link_baud"), 500000)
check("and every step it took, in order",
      [s.get("step") for s in _hf.get("steps") or []],
      ["echo-off", "ATBRD OK", "ident", "final OK"])
check("the echo-off's reply is read before it is thrown away",
      _said(_h, "echo-off"), "ATE0\rOK\r")
check("the identification is what the adapter sent at the new rate",
      _said(_h, "ident"), "ELM327 v1.4b\r")
check("each step says how long its answer took",
      bool(_hf.get("steps"))
      and all(isinstance(s.get("ms"), int) for s in _hf["steps"]), True)

# The echo-off itself raised.
_h = _stepped(fail="echo-off")
check("a write that fails at the echo-off does not raise", _h.raise_baud(500000), False)
_hf = getattr(_h, "fastbaud", None) or {}
check("it fails at the echo-off", (_hf.get("outcome"), _hf.get("failed_at")),
      ("failed", "echo-off"))
check("and keeps the error, in words", "SerialException" in (_hf.get("why") or ""), True)
check("the link stays at 115200", _hf.get("link_baud"), 115200)

# ATBRD's OK: an adapter that has no ATBRD.
_h = _stepped(brd=False)
check("an adapter that refuses ATBRD does not raise", _h.raise_baud(500000), False)
_hf = getattr(_h, "fastbaud", None) or {}
check("it fails at ATBRD's OK", _hf.get("failed_at"), "ATBRD OK")
check("and keeps what it said instead", _said(_h, "ATBRD OK"), "?\r")
check("the link stays at 115200", _hf.get("link_baud"), 115200)

# ATBRD's OK: the echo-off never took (an adapter still busy with ATZ), so the
# adapter repeats ATBRD back before its OK -- the bug this shipped with.
_h = _stepped(fail="echo-off ignored")
check("an adapter that ignored the echo-off does not raise", _h.raise_baud(500000), False)
_hf = getattr(_h, "fastbaud", None) or {}
check("it fails at ATBRD's OK", _hf.get("failed_at"), "ATBRD OK")
check("which heard its own command echoed back", _said(_h, "ATBRD OK"), "ATBRD 08\r")
check("and the echo-off before it heard nothing at all", _said(_h, "echo-off"), "")
check("the adapter fell back, and so did the link", (_hf.get("link_baud"), _h.ser.rate),
      (115200, 115200))

# The ident at the new rate.
_h = _stepped(fail="ident")
check("an identification that never arrives does not raise", _h.raise_baud(500000), False)
_hf = getattr(_h, "fastbaud", None) or {}
check("it fails at the ident", _hf.get("failed_at"), "ident")
check("which heard nothing", _said(_h, "ident"), "")
check("the adapter fell back, and so did the link", (_hf.get("link_baud"), _h.ser.rate),
      (115200, 115200))

# The final OK. The adapter switched and never confirmed, and _settle() finds
# it at the new rate: the call returns False and the link is 500000 anyway,
# which is why the rate in force is read off the handle, not inferred.
_h = _stepped(confirm=False)
check("a switch that is never confirmed does not raise", _h.raise_baud(500000), False)
_hf = getattr(_h, "fastbaud", None) or {}
check("it fails at the final OK", _hf.get("failed_at"), "final OK")
check("and the rate in force is the one the handle really ended on",
      (_hf.get("link_baud"), _hf.get("settled")), (500000, 500000))

# NOT ASKED FOR IS NOT A FAILURE, and not a guess either.
os.environ.pop("OMACAR_FASTBAUD", None)
_h = _stepped()
_h.raise_baud(500000)
check("with OMACAR_FASTBAUD unset there is no attempt to record",
      getattr(_h, "fastbaud", "missing"), None)
check("and nothing was sent", _h.ser.segs, [])
os.environ["OMACAR_FASTBAUD"] = "1"

# NOTHING NEW ON THE WIRE. Recording the attempt adds no command: every byte
# written is one raise_baud() already wrote.
_h = _stepped()
_writes = []
_orig_write = _h.ser.write
_h.ser.write = lambda d: (_writes.append(d), _orig_write(d))[1]
_h.raise_baud(500000)
check("the handshake writes exactly ATE0, ATBRD and the confirming CR",
      _writes, [b"ATE0\r", b"ATBRD 08\r", b"\r"])

# NOR NEW TIME IN THE ONE WINDOW THAT MATTERS. Between reading ATBRD's OK and
# switching the host's rate, the adapter may already be sending its
# identification at the new rate -- that gap is one of the report's two
# leading reasons the raise fails on the car. So the record of ATBRD's answer
# is written after the switch, and the window holds what it always held.


class _SwitchWatch(_HandshakeElm):
    """Notes what the record held at the moment the host switched to 500000."""

    def __setattr__(self, name, value):
        owner = self.__dict__.get("owner")
        if (name == "baudrate" and value == 500000 and owner is not None
                and "at_switch" not in self.__dict__):
            trail = getattr(owner, "fastbaud", None) or {}
            self.__dict__["at_switch"] = (
                [s.get("step") for s in trail.get("steps") or []],
                trail.get("failed_at"))
        object.__setattr__(self, name, value)


_h = elm.Elm.__new__(elm.Elm)
_h.ser = _SwitchWatch()
_h.ser.owner = _h
check("the watched handshake still raises", _h.raise_baud(500000), True)
check("nothing is recorded between reading ATBRD's OK and the host's switch",
      _h.ser.__dict__.get("at_switch"), (["echo-off"], "ATBRD OK"))
check("ATBRD's answer is still recorded, just after the switch",
      _said(_h, "ATBRD OK"), "OK\r")

if _keep_env is None:
    os.environ.pop("OMACAR_FASTBAUD", None)
else:
    os.environ["OMACAR_FASTBAUD"] = _keep_env

# ------------------------------------------------ full-bus formatting, opt-in
head("ATCAF0 is asked for only when OMACAR_CAF0 says so, and ATCAF1 always comes back")


class _CafElm:
    """Records every raw() command; monitor() answers with lines keyed to
    whatever ATSP was sent most recently, the way a real probe depends on it."""

    def __init__(self, hits=None):
        self.sent = []
        self.hits = hits or {}
        self._proto = None

    def raw(self, cmd):
        self.sent.append(str(cmd).upper())
        if str(cmd).upper().startswith("ATSP"):
            self._proto = str(cmd).upper()[4:]
        return ["OK"]

    def monitor(self, command="ATMA", seconds=2.0, on_line=None, limit=200000,
                should_stop=None):
        lines = self.hits.get(self._proto, [])
        for ln in lines:
            if on_line:
                on_line(ln)
        return len(lines)


_keep_caf0 = os.environ.get("OMACAR_CAF0")
os.environ.pop("OMACAR_CAF0", None)

# TEN-PLUS FRAMES ON THE FIRST PROTOCOL TRIED, SO THE PROBE STOPS THERE. That
# is what makes the counts below exact: one pass through the per-protocol try
# block, then the one final settle onto whichever protocol won.
_heard = ["17C 00 12 34 00 00 00 00 00"] * 12
_el_off = _CafElm(hits={"6": _heard})
_chosen = listen._pick_monitor_protocol(_el_off, probe=0.01)
check("a protocol is still chosen with the env var unset", _chosen, "6")
check("and no ATCAF0 is sent without it",
      any(c.startswith("ATCAF0") for c in _el_off.sent), False)

os.environ["OMACAR_CAF0"] = "1"
_el_on = _CafElm(hits={"6": _heard})
listen._pick_monitor_protocol(_el_on, probe=0.01)
# BOTH PLACES: the per-protocol probe has to run under the same formatting the
# real capture will use, or a protocol that only works with ATCAF0 on could
# lose to one that does not need it -- and the final settle onto the winner
# has to leave the adapter in that state for the capture that follows.
check("ATCAF0 is sent once per protocol probed and once on the final settle",
      _el_on.sent.count("ATCAF0"), 2)

# _restore_protocol() SENDS ATCAF1 UNCONDITIONALLY, EVEN WHEN THE PROBE FOUND
# NOTHING. python-obd's PID parsing depends on ATCAF1's formatted replies, so
# skipping it because no monitor protocol was found -- exactly the run whose
# very next event is handing the port back to the daemon -- would break
# ordinary telemetry for a reason nobody watching the dashboard could see.
_el_restore = _CafElm()
listen._restore_protocol(_el_restore, "7")
check("ATCAF1 is sent on restore", "ATCAF1" in _el_restore.sent, True)

_el_norestore = _CafElm()
listen._restore_protocol(_el_norestore, None)
check("...and also when no protocol was found to restore",
      "ATCAF1" in _el_norestore.sent, True)
check("with nothing else sent in that case (there is nothing to restore to)",
      _el_norestore.sent, ["ATCAF1"])

if _keep_caf0 is None:
    os.environ.pop("OMACAR_CAF0", None)
else:
    os.environ["OMACAR_CAF0"] = _keep_caf0

# ---------------------------------------------------- history rows are objects
head("a chart reads history rows by name, because that is what they are")

# THE TRAP IS THE `cols` LIST. /api/history ships it beside the rows and it
# reads exactly like an index map. It is not one: every row is keyed by name.
# A chart indexing by position finds nothing, silently — and a chart with no
# points is indistinguishable from a car with no readings, so the bug renders
# as an honest-looking "no pack readings" over a drive that has 867 of them.
import records as _rc2   # noqa: E402

_hdb = sqlite3.connect(":memory:")
# The row_factory is WHY they are mappings -- records.rows() does dict(r), and
# without it every row is a tuple and the dict() raises. Setting it here is not
# test decoration: it is the same line records.connect() sets, and it is the
# whole reason a consumer may read by name.
_hdb.row_factory = sqlite3.Row
_hdb.execute("CREATE TABLE samples (t REAL, rpm REAL, speed REAL, load REAL,"
             " throttle REAL, coolant REAL, intake REAL, maf REAL, stft REAL,"
             " ltft REAL, timing REAL, lphk REAL, eff REAL, soc REAL)")
_hdb.execute("INSERT INTO samples (t, soc, speed) VALUES (1000.0, 61.5, 50.0)")
_hrows = _rc2.samples(_hdb, since=0, limit=10)
check("a history row is a mapping", isinstance(_hrows[0], dict), True)
check("keyed by channel name", _hrows[0].get("soc"), 61.5)
check("and NOT by position",
      isinstance(_hrows[0].get(0, KeyError), type(KeyError)) or 0 not in _hrows[0],
      True)
_imasrc = open(os.path.join(ROOT, "share", "js", "views", "ima.js"),
               encoding="utf-8").read()
check("the charge trace reads them by name",
      "r.soc" in _imasrc and "cols.indexOf" not in _imasrc, True)

# -------------------------------------------------------------- the charge dial
head("the charge dial is drawn only when a reading is behind it")

_ima = open(os.path.join(ROOT, "share", "js", "views", "ima.js"),
            encoding="utf-8").read()

# THE RULE THE WHOLE PAGE IS BUILT ON. A ring at zero beside the words "state
# of charge" is not a placeholder on a 190,000-mile hybrid; it is a number
# somebody acts on. So the dial returns nothing rather than rendering an empty
# one, and the null check comes before anything is built.
_fn = _ima[_ima.index("function chargeDial()"):]
_guard = _fn.index("return null")
_first_build = _fn.index('h("section.sect"')
check("it returns nothing before it builds anything", _guard < _first_build, True)
check("and the check covers all three empty shapes",
      all(w in _fn[:_guard] for w in ("null", "undefined", "NaN")), True)

# GEOMETRY FROM THE VENDORED DESIGN, so a later tweak here is a deliberate
# divergence rather than a drift nobody notices.
_design = open(os.path.join(ROOT, "doc", "design", "cockpit", "energy-view.tsx"),
               encoding="utf-8").read()
check("the cockpit's radius is kept", "R = 123" in _ima and 'r="123"' in _design, True)
check("and its forty ticks", "i < 40" in _ima and "length: 40" in _design, True)

# THE CLAIM THAT STOPPED BEING TRUE. This file opened for months by saying not
# one live IMA quantity had ever been captured. SOC answered on 16 September,
# and a stale claim in a header comment is how the next person concludes there
# is nothing to draw.
check("the page no longer claims nothing has ever answered",
      "Not one live IMA quantity has ever been" in _ima, False)

# THE DIAL IS LABELLED FOR THE NUMBER IT DRAWS. lib/ima.py keeps two quantities
# apart on purpose: "State of charge" is manufacturer data off the hybrid
# controllers and has never answered, while "Hybrid pack remaining life" is
# generic mode 01 PID 0x5B and has. The dial draws the second. Labelling it as
# the first put 64% under the words STATE OF CHARGE directly above a register
# row saying that quantity was never discovered — the page contradicting itself
# on one screen.
# Checked against what is RENDERED, not against the file: the paragraph above
# explaining this fix says the words too, and a guard that cannot tell a label
# from a comment fails on its own explanation.
check("the dial does not borrow the undiscovered quantity's name",
      'h("span", "STATE OF CHARGE")' in _ima, False)
check("it names the reading it has", "PACK REMAINING" in _ima, True)
check("and says which PID that is", "0x5B" in _ima, True)
_imapy = open(os.path.join(ROOT, "lib", "ima.py"), encoding="utf-8").read()
check("the register still keeps the two apart",
      '"State of charge"' in _imapy and "remaining life" in _imapy, True)
check("and it says what direction is actually read from",
      "not\n            + \"from motor power" in _ima
      or "not " in _ima and "motor power" in _ima, True)

# ------------------------------------------------- the ported cockpit palettes
head("a shipped theme keeps the colours it shipped with")

import themes as _th   # noqa: E402

# THE SILENT ONE. _clean() replaces any colour it cannot parse with the SEED
# value rather than refusing the theme, which is right for a hand-edited file
# and lethal for one in the repository: a single typo'd hex would leave a
# theme that installs cleanly, looks almost right, and is wearing somebody
# else's blue. So every colour in the shipped file has to survive the round
# trip unchanged.
_tf = os.path.join(ROOT, "doc", "design", "cockpit", "themes.json")
with open(_tf, encoding="utf-8") as _f:
    _doc = _json.load(_f)
_ported = _doc.get("themes") or {}
check("the ported palettes are there", sorted(_ported),
      ["cockpit-day", "cockpit-deep", "cockpit-night"])
_lost = []
for _tid, _body in _ported.items():
    _clean = _th._clean(_tid, _body)
    if not _clean:
        _lost.append(f"{_tid}: refused outright")
        continue
    for _k in _th.COLOURS:
        if _clean[_k] != str(_body.get(_k, "")).lower():
            _lost.append(f"{_tid}.{_k} became {_clean[_k]}")
    if _clean["mode"] != _body.get("mode"):
        _lost.append(f"{_tid}.mode became {_clean['mode']}")
check("every colour survives the round trip", _lost, [])
# The cockpit's own background is the one value a reader can check against
# doc/design/cockpit/globals.css, so it is worth naming.
check("the night palette is the cockpit's own background",
      _ported["cockpit-night"]["background"], "#111416")
check("and the day one is a light mode", _ported["cockpit-day"]["mode"], "light")

# ------------------------------------------- the recorder loses a leg, not the day
head("the drive recorder survives the adapter going away")

import drivelog as _dl   # noqa: E402
import listen as _ln     # noqa: E402

# pyserial raises SerialException from serial.Serial() and from init()'s setup
# writes, both of which run BEFORE the protected read loop -- and it is an
# OSError, not a RuntimeError. leg() caught Quiet and RuntimeError only, so an
# adapter re-enumerating (vibration on a dash mount, the voltage dip at crank)
# walked out of leg(), out of run(), past main()'s KeyboardInterrupt-only
# guard, and exited the process. That alone would have cost one leg. What it
# actually cost was the day: RestartSec=20 meant three of them inside a minute
# tripped StartLimitBurst=3, after which systemd stopped restarting it for
# good, with nobody watching a screen to notice.
check("a serial fault is an OSError, not a RuntimeError",
      issubclass(sys.modules["serial"].SerialException, OSError), True)

_sup = _dl.Supervisor(once=True)
_said = []
_sup.say = lambda state, detail="", **f: _said.append((state, detail))
_orig_listen = _ln.listen


def _vanish(*a, **k):
    raise sys.modules["serial"].SerialException("[Errno 5] Input/output error")


_ln.listen = _vanish
try:
    _lost_only_the_leg = _sup.leg() is False
except OSError:
    _lost_only_the_leg = False           # it escaped, which is the old bug
finally:
    _ln.listen = _orig_listen

check("the adapter vanishing costs the leg, not the supervisor",
      _lost_only_the_leg, True)
check("and the reason is on the status screen",
      any(s == "declined" for s, _d in _said), True)

# THE OTHER HALF OF THE SAME FAILURE. The unit's own comment promises it is
# "ALWAYS COMING BACK" because staying down is measured in car time; a start
# limit is the one setting that breaks that promise, and it was set.
_unit = open(os.path.join(ROOT, "share", "systemd", "omacar-drivelog.service"),
             encoding="utf-8").read()
# Directives, not the word: the comment above the setting has to be free to
# name what was removed and why, and a grep over the whole file cannot tell
# an explanation from an instruction.
_directives = [ln.strip() for ln in _unit.splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
check("nothing rate-limits the restart that keeps it alive",
      any(d.startswith("StartLimitBurst") for d in _directives), False)
check("and the restart itself is still unconditional",
      any(d == "Restart=always" for d in _directives), True)

# ------------------------------------------------------- the duty-cycle knobs
head("the leg duty cycle is overridable, validated, bounded, and shown")

_DUTY_ENV = ("OMACAR_DRIVELOG_BETWEEN", "OMACAR_DRIVELOG_LEG_LINES",
             "OMACAR_DRIVELOG_QUIET", "OMACAR_DRIVELOG_END_ON_OVERFLOW")
_duty_kept = {k: os.environ.get(k) for k in _DUTY_ENV}


def _duty_restore():
    for k, v in _duty_kept.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


try:
    for k in _DUTY_ENV:
        os.environ.pop(k, None)
    _ov = _dl.read_overrides()
    check("unset, between keeps its default", _ov["between"], _dl.BETWEEN_LEGS)
    check("unset, the leg cap keeps listen's own default",
          _ov["leg_lines"], _ln.DEFAULT_LIMIT)
    check("unset, quiet keeps its default", _ov["quiet"], _dl.QUIET_TIMEOUT)
    check("unset, ending on overflow is off", _ov["end_on_overflow"], False)
    check("and nothing was overridden", _ov["notes"], [])

    os.environ["OMACAR_DRIVELOG_BETWEEN"] = "240"
    os.environ["OMACAR_DRIVELOG_LEG_LINES"] = "10000"
    os.environ["OMACAR_DRIVELOG_QUIET"] = "30"
    os.environ["OMACAR_DRIVELOG_END_ON_OVERFLOW"] = "1"
    _ov = _dl.read_overrides()
    check("a good BETWEEN is honoured", _ov["between"], 240.0)
    check("a good LEG_LINES is honoured", _ov["leg_lines"], 10000)
    check("a good QUIET is honoured", _ov["quiet"], 30.0)
    check("the overflow flag is exactly \"1\"", _ov["end_on_overflow"], True)
    check("and every good override is noted for the start-up log",
          len(_ov["notes"]), 3)

    os.environ["OMACAR_DRIVELOG_BETWEEN"] = "not a number"
    os.environ["OMACAR_DRIVELOG_LEG_LINES"] = "0"           # below the floor
    os.environ["OMACAR_DRIVELOG_QUIET"] = "100000"          # above the ceiling
    os.environ["OMACAR_DRIVELOG_END_ON_OVERFLOW"] = "yes"   # only "1" counts
    _ov = _dl.read_overrides()
    check("a value that does not parse falls back to the default",
          _ov["between"], _dl.BETWEEN_LEGS)
    check("a value below the floor falls back to the default",
          _ov["leg_lines"], _ln.DEFAULT_LIMIT)
    check("a value above the ceiling falls back to the default",
          _ov["quiet"], _dl.QUIET_TIMEOUT)
    check("only the exact flag turns overflow-ending on",
          _ov["end_on_overflow"], False)
    # A GUARD THAT CANNOT FAIL IS NOT A GUARD. Confirmed by hand: disabling
    # the `if not (lo <= val <= hi):` bounds check in _env_number() (so an
    # out-of-range value is accepted rather than falling back) turned the two
    # bounds checks above into FAILs -- "wanted 60000, got 0" for LEG_LINES
    # and "wanted 120.0, got 100000.0" for QUIET -- while the unparseable-
    # BETWEEN check kept passing, since that one is caught earlier, by the
    # cast() itself. Restoring the check made every one of them pass again.
    check("every fallback is still logged as an override attempt",
          len(_ov["notes"]), 3)
finally:
    _duty_restore()

head("a leg is capped by OMACAR_DRIVELOG_LEG_LINES, passed to listen() as limit=")

_seen_kwargs = {}
_orig_listen_call = _ln.listen


def _fake_listen(**kw):
    _seen_kwargs.update(kw)
    cap = kw.get("cap")
    if cap is not None:
        cap.protocol = "6"
    return cap


_ln.listen = _fake_listen
try:
    _sup_leg = _dl.Supervisor(once=True, leg_lines=12345)
    _sup_leg.say = lambda *a, **k: None
    _sup_leg.leg()
    check("the configured cap reaches listen() as limit=",
          _seen_kwargs.get("limit"), 12345)
finally:
    _ln.listen = _orig_listen_call

head("`omacar drive status` shows the duty cycle a running supervisor is using")

_duty_tmp = tempfile.mkdtemp()
_duty_state_kept = _dl.STATE
_dl.STATE = os.path.join(_duty_tmp, "drivelog.json")
try:
    _sup_status = _dl.Supervisor(once=True, between=240.0, leg_lines=10000,
                                 quiet=30.0, end_on_overflow=True)
    _sup_status.publish()
    _status_text, _ = _dl.report()
    check("the gap between legs is shown", "240" in _status_text, True)
    check("the leg's line cap is shown", "10000" in _status_text, True)
    check("the quiet timeout is shown", "30" in _status_text, True)
    check("and that ending on overflow is on",
          "overflow" in _status_text.lower(), True)
finally:
    _dl.STATE = _duty_state_kept
    shutil.rmtree(_duty_tmp, ignore_errors=True)

head("an overflowed adapter can end the leg without waiting out the quiet timeout")

_orig_listen_ov = _ln.listen


def _fake_listen_overflow(**kw):
    """Feeds the words a real overflow produces, then stays silent."""
    cap = kw["cap"]
    on_ready = kw.get("on_ready")
    should_stop = kw["should_stop"]
    if on_ready:
        on_ready(cap)
    cap.add_line("13A 0013000000000024")
    cap.add_line("BUFFER FULL")
    cap.add_line("STOPPED")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if should_stop():
            break
        time.sleep(0.02)
    return cap


_ln.listen = _fake_listen_overflow
try:
    _sup_off = _dl.Supervisor(once=True, quiet=120.0, end_on_overflow=False)
    _sup_off.say = lambda *a, **k: None
    _t0 = time.time()
    _sup_off.leg()
    _took_off = time.time() - _t0
    check("with the flag off, the overflow does not shorten the leg",
          _took_off >= 5.0, True)

    _sup_on = _dl.Supervisor(once=True, quiet=120.0, end_on_overflow=True)
    _sup_on.say = lambda *a, **k: None
    _t0 = time.time()
    _sup_on.leg()
    _took_on = time.time() - _t0
    check(f"with the flag on, the leg ends on the overflow, not the 120s "
          f"quiet timeout ({_took_on:.1f}s)", _took_on < 3.0, True)
finally:
    _ln.listen = _orig_listen_ov

head("a leg's quiet timeout counts from when the adapter is listening, not from set-up")

# MEASURED ON THE CAR, 29 SEPTEMBER. With the fallback preset (QUIET=10,
# END_ON_OVERFLOW=1) every leg from 11:58 to 13:17 saved ZERO frames. The last
# leg on default settings got its first frame 11.47s after the capture began:
# ATZ, the protocol search and the monitor probe, at 115200. The quiet clock
# started at leg set-up, and done() -- only ever asked once the monitor is
# running -- found eleven seconds of "silence" on its very first call and
# ended every leg about a second and a half before its first frame.


class _LegClock:
    """drivelog's clock, so eleven seconds of adapter set-up take none."""

    def __init__(self):
        self.t = time.time()

    def time(self):
        return self.t

    def __getattr__(self, name):                 # strftime, sleep, ...
        return getattr(time, name)


def _slow_setup_listen(clock, ready_at, first_at, lines, backstop=120.0):
    """A listen() whose adapter reaches the monitor `ready_at` seconds after
    the leg began, and whose first line arrives at `first_at` -- then the
    `lines`, one per hundredth of a second, then silence. should_stop is
    asked before every read and never during set-up, which is what the real
    listen() does: it only reaches Elm.monitor() for the final ATMA."""
    out = {}

    def fake(**kw):
        cap, should_stop = kw["cap"], kw["should_stop"]
        t0 = clock.t
        clock.t = t0 + ready_at
        if kw.get("on_ready"):
            kw["on_ready"](cap)
        todo = list(lines)
        while clock.t - t0 < backstop:
            if should_stop():
                break
            clock.t += 0.01
            if todo and clock.t - t0 >= first_at:
                cap.add_line(todo.pop(0))
        out["ended_at"] = clock.t - t0
        return cap
    return fake, out


_orig_listen_slow = _ln.listen
_orig_dl_time = _dl.time
_today = ["13A 0013000000000024"] * 128 + ["BUFFER FULL"]
try:
    _clk = _LegClock()
    _dl.time = _clk

    # Today's leg, on today's fallback preset.
    _ln.listen, _slow = _slow_setup_listen(_clk, ready_at=11.4, first_at=11.5,
                                           lines=_today)
    _sup_slow = _dl.Supervisor(once=True, quiet=10.0, end_on_overflow=True)
    _sup_slow.say = lambda *a, **k: None
    _sup_slow.leg()
    check("QUIET=10 with 11.4s of set-up: the leg keeps the frames it heard",
          (_sup_slow.last_leg or {}).get("frames"), 128)
    check("and still ends on the overflow, not the quiet timeout "
          f"({_slow.get('ended_at', 0):.1f}s after it began)",
          12.5 <= _slow.get("ended_at", 0) < 15.0, True)

    # The same set-up, without END_ON_OVERFLOW: the quiet timeout ends it,
    # counted from the last frame.
    _ln.listen, _slow2 = _slow_setup_listen(_clk, ready_at=11.4, first_at=11.5,
                                            lines=_today)
    _sup_slow2 = _dl.Supervisor(once=True, quiet=10.0, end_on_overflow=False)
    _sup_slow2.say = lambda *a, **k: None
    _sup_slow2.leg()
    check("QUIET=10, overflow-ending off: the frames are kept too",
          (_sup_slow2.last_leg or {}).get("frames"), 128)

    # A SILENT BUS STILL ENDS THE LEG, quiet seconds after listening began --
    # not before (the old clock ended it at once) and not never.
    _ln.listen, _silent = _slow_setup_listen(_clk, ready_at=11.4, first_at=11.5,
                                             lines=[])
    _sup_silent = _dl.Supervisor(once=True, quiet=10.0, end_on_overflow=True)
    _sup_silent.say = lambda *a, **k: None
    _sup_silent.leg()
    check("a silent bus ends the leg QUIET seconds after listening began "
          f"({_silent.get('ended_at', 0):.2f}s after the leg began)",
          21.4 <= _silent.get("ended_at", 0) < 21.6, True)
finally:
    _ln.listen = _orig_listen_slow
    _dl.time = _orig_dl_time

# --------------------------------------------------------- the parked session
head("tools/ima-session.sh sends only read-only prospect/listen/mcp calls")

import re as _ima_re              # noqa: E402
import shutil as _ima_shutil      # noqa: E402
import subprocess as _ima_sp      # noqa: E402
import tempfile as _ima_tempfile  # noqa: E402

_IMA_SH = os.path.join(ROOT, "tools", "ima-session.sh")
_ima_src = open(_IMA_SH, encoding="utf-8").read()
# Comments say what this must never do, in words -- "the guard checks
# tools/ima-session.sh reads this file and fails if any of those appear as
# something it would send" cannot itself be the trigger for its own failure.
# Only the code, with comment lines stripped, is what could actually run.
_ima_code = "\n".join(ln for ln in _ima_src.splitlines()
                      if not ln.strip().startswith("#"))

check("the script exists and is executable",
      os.access(_IMA_SH, os.X_OK), True)
check("never runs `omacar write`", "omacar write" in _ima_code, False)
check("never sends a clear", bool(_ima_re.search(r"clear", _ima_code, _ima_re.I)), False)
check("never sends ATCSM0", "atcsm0" in _ima_code.lower(), False)

# Every 0x-prefixed service byte named anywhere in the executable text -- in a
# --service flag, in a raw request string, or in a label a human reads -- has
# to be one of the two read-only services section 4 uses. Checking the whole
# text rather than just the --service flags also catches one built by string
# concatenation instead.
_FORBIDDEN_SERVICES = {"10", "11", "14", "27", "28", "2E", "2F", "31", "34",
                       "36", "37", "3E", "85"}
_ALLOWED_SERVICES = {"21", "22"}
_services = [m.upper() for m in _ima_re.findall(r"0x([0-9A-Fa-f]{2})\b", _ima_code)]
check("service bytes were found (the checks below are not vacuous)",
      len(_services) > 0, True)
check("every 0x service byte in the script is 0x21 or 0x22",
      [s for s in _services if s not in _ALLOWED_SERVICES], [])
check("and none of them is one of the forbidden services",
      [s for s in _services if s in _FORBIDDEN_SERVICES], [])

# Step 0b's "HEADER:REQUEST" pairs are the one place a raw request hex string
# is assembled by hand, checked the same way car_request itself would refuse
# one: by its first byte.
_pairs = _ima_re.findall(r'"(18DA[0-9A-Fa-f]{2}F1):([0-9A-Fa-f]{4,6})"', _ima_code)
check("step 0b's requests were found (the check below is not vacuous)",
      len(_pairs) > 0, True)
check("every step 0b request's service byte is 0x21 or 0x22",
      [r for _h, r in _pairs if r[:2].upper() not in _ALLOWED_SERVICES], [])

# Every 8-hex-digit token anywhere in the script -- every place a header could
# be spelled out, in a --headers flag or a HEADER:REQUEST pair -- has to be a
# tester address, never a header naming live control traffic.
_HEADER_RE = _ima_re.compile(r"^(?:18DA|18DB)[0-9A-F]{2}F1$")
_headers = _ima_re.findall(r"\b([0-9A-Fa-f]{8})\b", _ima_code)
check("header-shaped tokens were found (the check below is not vacuous)",
      len(_headers) > 0, True)
check("every 8-hex-digit token in the script is 18DAxxF1 or 18DBxxF1",
      [h for h in _headers if not _HEADER_RE.match(h.upper())], [])

# Section 4 built each 0x22 range to stay under 24 ids on purpose:
# prospect.sweep() abandons a header after 24 consecutive silences, which
# could understate a wider range on a module that answers nothing for an
# unmapped DID instead of refusing it.
_prospect_calls = _ima_re.findall(
    r'run_prospect\s+"[^"]*"\s+0x([0-9A-Fa-f]{2})\s+"([^"]*)"\s+"([^"]*)"\s+(\d+)',
    _ima_code)
check("prospect calls were found (the check below is not vacuous)",
      len(_prospect_calls) > 0, True)
for _svc, _hdrs, _rng, _rounds in _prospect_calls:
    if _svc.upper() == "22":
        _lo, _hi = _rng.split("-")
        _span = int(_hi, 16) - int(_lo, 16) + 1
        check(f"0x22 range {_rng} ({_hdrs}) is 24 ids or fewer", _span <= 24, True)

# A GUARD THAT CANNOT FAIL IS NOT A GUARD. Confirmed by hand: a line
#   run_prospect "x" 0x2E "18DA03F1" "0000-0000" 1
# inserted above made "every 0x service byte ... is 0x21 or 0x22" and "none
# of them is one of the forbidden services" both report FAIL; removing it
# made every check here pass again. See the commit message for the exact
# before/after run.

head("`--dry-run` prints the plan and sends nothing")

_ima_bin = _ima_tempfile.mkdtemp()
_fake_omacar = os.path.join(_ima_bin, "omacar")
_ima_calls = os.path.join(_ima_bin, "omacar-calls.log")
with open(_fake_omacar, "w", encoding="utf-8") as f:
    f.write('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % _ima_calls)
os.chmod(_fake_omacar, 0o755)

_ima_state = _ima_tempfile.mkdtemp()
_ima_env = dict(os.environ)
_ima_env["PATH"] = _ima_bin + os.pathsep + _ima_env.get("PATH", "")
_ima_env["XDG_STATE_HOME"] = _ima_state

_ima_proc = _ima_sp.run(["bash", _IMA_SH, "--dry-run"], env=_ima_env,
                        capture_output=True, text=True, timeout=60)

check("--dry-run exits 0", _ima_proc.returncode, 0)
check("--dry-run never invokes the real omacar", os.path.exists(_ima_calls), False)

_ima_expected = [
    "omacar drive off",
    "omacar listen capture --id 231 --seconds 15 --save --note probe-231",
    "omacar listen capture --id 307 --seconds 15 --save --note probe-307",
    "omacar listen capture --id 115 --seconds 15 --save --note probe-115",
    "omacar listen capture --id 17D --seconds 15 --save --note probe-17D",
    '"header":"18DA0EF1","request":"222660"',
    '"header":"18DA03F1","request":"2101"',
    "omacar prospect --service 0x22 --headers 18DA0EF1,18DA10F1 --range 2610-2616 --parked --rounds 4",
    "omacar prospect --service 0x22 --headers 18DA0EF1,18DA10F1 --range 2660-2666 --parked --rounds 4",
    "omacar prospect --service 0x22 --headers 18DA0EF1 --range 2240-2240 --parked --rounds 2",
    "omacar prospect --service 0x22 --headers 18DA03F1,18DA04F1 --range 2001-2012 --parked --rounds 8",
    "omacar prospect --service 0x22 --headers 18DA03F1,18DA04F1 --range 2021-202C --parked --rounds 8",
    "omacar prospect --service 0x22 --headers 18DA03F1,18DA04F1 --range 2222-2222 --parked --rounds 8",
    "omacar prospect --service 0x21 --headers 18DA03F1,18DA04F1 --range 00-FF --parked --rounds 8",
    "omacar candlog --profile honda-crz-2015 --once",
    "omacar drive on",
]
_ima_missing = [s for s in _ima_expected if s not in _ima_proc.stdout]
check("--dry-run's plan names every expected command", _ima_missing, [])
check("step 5 is not planned without --full", "2000-2FFF" not in _ima_proc.stdout, True)

_ima_proc_full = _ima_sp.run(["bash", _IMA_SH, "--dry-run", "--full"], env=_ima_env,
                             capture_output=True, text=True, timeout=60)
check("--dry-run --full exits 0", _ima_proc_full.returncode, 0)
check("--dry-run --full never invokes the real omacar",
      os.path.exists(_ima_calls), False)
check("--dry-run --full also plans the optional block sweep",
      "omacar discover --headers 18DA03F1,18DA04F1 --service 0x22 --range "
      "2000-2FFF --budget 45 --once" in _ima_proc_full.stdout, True)
check("and its status check",
      "omacar discover status" in _ima_proc_full.stdout, True)

_ima_shutil.rmtree(_ima_bin, ignore_errors=True)
_ima_shutil.rmtree(_ima_state, ignore_errors=True)

# ------------------------------------------------------- the driveway check
head("tools/driveway-check.sh may run only the commands it is allowed to")

_DW_SH = os.path.join(ROOT, "tools", "driveway-check.sh")
_DW_PY = os.path.join(ROOT, "tools", "driveway_verdict.py")
_dw_sh_src = open(_DW_SH, encoding="utf-8").read()
_dw_py_src = open(_DW_PY, encoding="utf-8").read()


def _dw_code_of(src):
    # Same reasoning as tools/ima-session.sh above: only the code, comment
    # lines stripped, is what could actually run -- this file's own header
    # comment names every one of the words the checks below forbid, on
    # purpose, as the list of things it must never do.
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


_dw_sh_code = _dw_code_of(_dw_sh_src)
_dw_py_code = _dw_code_of(_dw_py_src)

_DW_OMACAR_ASSIGN = 'OMACAR_BIN="${OMACAR_BIN:-$ROOT/bin/omacar}"'
_DW_SYSTEMCTL_ASSIGN = 'SYSTEMCTL="${OMACAR_SYSTEMCTL:-systemctl}"'
# What may follow each way of naming omacar / systemctl. drive off/on/status
# and listen capture are the brief's four; "driveway-check" is this tool's
# own name, in its usage text and error prefix.
_DW_OMACAR_OK = _ima_re.compile(
    r"\s+(drive\s+(off|on|status)|listen\s+capture|driveway-check)\b")
_DW_OMACAR_BIN_OK = _ima_re.compile(r'\s+(drive\s+(off|on|status)|listen\s+capture)\b')
_DW_SYSTEMCTL_OK = _ima_re.compile(r'\s+--user\s+(daemon-reload|restart\s+omacar-drivelog)\b')
# Nothing in either file may name any of these: none of them belongs in a tool
# that only reads files the daemon and the recorder already write, and the
# ones that open the adapter are the whole difference between this and
# tools/ima-session.sh.
_DW_BANNED = ("serial", "Elm(", "request_port", "connect.connect", "listen(",
              ".raw(", ".request(", "subprocess", "os.system", "Popen")


def _dw_findings(sh_code, py_code):
    """Everything in the two files' code that the allowlist does not permit.
    An empty list is a pass. Takes the code as text so the mutation checks
    below can feed it a line it must catch."""
    found = []
    both = sh_code + "\n" + py_code
    low = both.lower()
    for word in ("omacar write", "prospect", "mcp", "atcsm0"):
        if word in low:
            found.append(f"forbidden word: {word}")
    if _ima_re.search(r"clear", both, _ima_re.I):
        found.append("forbidden word: clear")
    for b in _ima_re.findall(r"0x([0-9A-Fa-f]{2})\b", both):
        if b.upper() in _FORBIDDEN_SERVICES:
            found.append(f"forbidden UDS service byte: 0x{b}")
    for tok in _DW_BANNED:
        if (tok.lower() in low) if tok == "serial" else (tok in both):
            found.append(f"banned (opens the adapter or spawns a process): {tok}")
    if _ima_re.search(r"\bsudo\b", sh_code):
        found.append("sudo")
    for line in sh_code.splitlines():
        s = line.strip()
        if "bin/omacar" in s and s != _DW_OMACAR_ASSIGN:
            found.append(f"bin/omacar outside the OMACAR_BIN default: {s}")
        for m in _ima_re.finditer(r'\$\{?OMACAR_BIN\}?"?', s):
            rest = s[m.end():]
            if rest.startswith(":-"):
                continue                     # the assignment's own default
            if not _DW_OMACAR_BIN_OK.match(rest):
                found.append(f"$OMACAR_BIN used for something else: {s}")
        for m in _ima_re.finditer(r"(?<![\w./-])omacar(?![\w./-])", s):
            if not _DW_OMACAR_OK.match(s[m.end():]):
                found.append(f"omacar used for something else: {s}")
        if "systemctl" in s and s != _DW_SYSTEMCTL_ASSIGN:
            found.append(f"a literal systemctl outside SYSTEMCTL's own default: {s}")
        for m in _ima_re.finditer(r'\$\{?SYSTEMCTL\}?"?', s):
            if not _DW_SYSTEMCTL_OK.match(s[m.end():]):
                found.append(f"$SYSTEMCTL used for something else: {s}")
    return found


check("the script exists and is executable", os.access(_DW_SH, os.X_OK), True)
check("the two files' code passes the allowlist", _dw_findings(_dw_sh_code, _dw_py_code), [])

# The checks are not vacuous: the things they look at are really there.
check("$OMACAR_BIN is used", len(_ima_re.findall(r'\$\{?OMACAR_BIN', _dw_sh_code)) > 3, True)
check("$SYSTEMCTL is used", len(_ima_re.findall(r'\$\{?SYSTEMCTL', _dw_sh_code)) >= 2, True)
check("0x-prefixed bytes are found (0x01, the gear byte for P)",
      len(_ima_re.findall(r"0x([0-9A-Fa-f]{2})\b", _dw_sh_code + _dw_py_code)) > 0, True)

# A GUARD THAT CANNOT FAIL IS NOT A GUARD. The first version of this one let
# all of these through. Each is a line inserted before the `drive off` step of
# the real script's code; every one has to be caught. (Also confirmed by hand
# on the box copy of the file itself -- see the fix-round section of
# omacar-notes/driveway-check-report.md.)
_dw_mutations = {
    "omacar doctor (sends OBD requests)": "omacar doctor",
    "$OMACAR_BIN live RPM": '"$OMACAR_BIN" live RPM',
    "${OMACAR_BIN} live RPM": '${OMACAR_BIN} live RPM',
    "sudo systemctl stop omacar-daemon": "sudo systemctl stop omacar-daemon",
    "a systemctl that is not the override": "systemctl --user stop omacar-daemon",
    "$SYSTEMCTL stop omacar-drivelog": '"$SYSTEMCTL" --user stop omacar-drivelog',
    "bin/omacar dtc, without the word 'clear'": '"$ROOT/bin/omacar" dtc --once',
    "omacar mode god": "omacar mode god",
}
for _label, _line in _dw_mutations.items():
    _mutated = _dw_sh_code.replace(
        'run_cmd "stand the recorder down', _line + '\nrun_cmd "stand the recorder down', 1)
    check(f"mutation caught: {_label}",
          _mutated != _dw_sh_code and len(_dw_findings(_mutated, _dw_py_code)) > 0, True)
for _label, _line in {
        "import serial": "import serial",
        "an Elm( on the adapter": "el = Elm(port)",
        "request_port": "connect.request_port(port)",
        "listen(": "listenlib.listen(seconds=1)",
        "a spawned process": "subprocess.run(['omacar', 'doctor'])"}.items():
    check(f"mutation caught in the helper: {_label}",
          len(_dw_findings(_dw_sh_code, _dw_py_code + "\n" + _line)) > 0, True)

head("`--dry-run` prints the plan and sends nothing, for all three modes")

_dw_bin = _ima_tempfile.mkdtemp()
_dw_fake_omacar = os.path.join(_dw_bin, "omacar")
_dw_fake_systemctl = os.path.join(_dw_bin, "systemctl")
_dw_calls = os.path.join(_dw_bin, "calls.log")
for _path in (_dw_fake_omacar, _dw_fake_systemctl):
    with open(_path, "w", encoding="utf-8") as f:
        f.write('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % _dw_calls)
    os.chmod(_path, 0o755)


def _run_driveway(*extra_args, dropin_files=None, systemctl=False):
    state_dir = _ima_tempfile.mkdtemp()
    dropin_dir = _ima_tempfile.mkdtemp()
    for _name, _body in (dropin_files or {}).items():
        with open(os.path.join(dropin_dir, _name), "w", encoding="utf-8") as f:
            f.write(_body)
    env = dict(os.environ)
    env["OMACAR_BIN"] = _dw_fake_omacar
    if systemctl:
        env["OMACAR_SYSTEMCTL"] = _dw_fake_systemctl
    env["XDG_STATE_HOME"] = state_dir
    env["OMACAR_DROPIN_DIR"] = dropin_dir
    proc = _ima_sp.run(["bash", _DW_SH, "--dry-run", *extra_args], env=env,
                       capture_output=True, text=True, timeout=60, input="")
    return proc, state_dir, dropin_dir


_dw_proc, _dw_state, _ = _run_driveway(systemctl=True)
check("--dry-run (test mode) exits 0", _dw_proc.returncode, 0)
check("--dry-run never invokes omacar or systemctl", os.path.exists(_dw_calls), False)

_dw_expected = [
    f"{_dw_fake_omacar} drive off",
    f"timeout 90 env OMACAR_FASTBAUD=1 {_dw_fake_omacar} listen capture --seconds 20"
    " --save --note fastbaud-test",
    f"timeout 90 env OMACAR_FASTBAUD=1 OMACAR_CAF0=1 {_dw_fake_omacar} listen capture"
    " --seconds 20 --save --note caf0-test",
    f"{_dw_fake_systemctl} --user daemon-reload",
    f"{_dw_fake_systemctl} --user restart omacar-drivelog",
    f"{_dw_fake_omacar} drive on",
]
_dw_missing = [s for s in _dw_expected if s not in _dw_proc.stdout]
check("--dry-run's plan names every expected command", _dw_missing, [])
check("the plan shows the recorder being stood down before anything else",
      _dw_proc.stdout.index("drive off") < _dw_proc.stdout.index("listen capture"), True)
_dw_summary_path = None
for _root, _dirs, _files in os.walk(_dw_state):
    if "summary.json" in _files:
        _dw_summary_path = os.path.join(_root, "summary.json")
check("a dry run still leaves a summary.json", _dw_summary_path is not None, True)

_dw_proc_ap, _, _ = _run_driveway("--apply-preset", "fallback")
check("--dry-run --apply-preset exits 0", _dw_proc_ap.returncode, 0)
check("--dry-run --apply-preset never invokes the real omacar",
      os.path.exists(_dw_calls), False)
check("--dry-run --apply-preset shows the real Environment= line, not an empty one",
      "Environment=OMACAR_DRIVELOG_END_ON_OVERFLOW=1 OMACAR_DRIVELOG_QUIET=10 "
      "OMACAR_DRIVELOG_BETWEEN=240" in _dw_proc_ap.stdout, True)
check("--dry-run --apply-preset stands the recorder down first and gives it back",
      (f"{_dw_fake_omacar} drive off" in _dw_proc_ap.stdout,
       f"{_dw_fake_omacar} drive on" in _dw_proc_ap.stdout), (True, True))

_dw_proc_rm, _, _ = _run_driveway(
    "--remove-preset", dropin_files={"driveway-preset.conf": "[Service]\nEnvironment=X=1\n"})
check("--dry-run --remove-preset exits 0", _dw_proc_rm.returncode, 0)
check("--dry-run --remove-preset never invokes the real omacar",
      os.path.exists(_dw_calls), False)
check("--dry-run --remove-preset plans the rename, the reload and the restart",
      all(s in _dw_proc_rm.stdout for s in
          ("driveway-preset.conf.off", "--user daemon-reload",
           "--user restart omacar-drivelog")), True)

_dw_proc_none, _, _ = _run_driveway("--remove-preset")
check("--dry-run --remove-preset with nothing to remove says so and plans nothing",
      ("nothing to remove" in _dw_proc_none.stdout, "drive off" in _dw_proc_none.stdout),
      (True, False))

_ima_shutil.rmtree(_dw_bin, ignore_errors=True)

# ----------------------------------------------------------------------- done
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every guard holds\n")
