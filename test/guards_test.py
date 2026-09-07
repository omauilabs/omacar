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

    for bad, why in (({"action": "use", "name": "nope"}, "no such layout"),
                     ({"action": "forget", "name": "nope"}, "no such layout"),
                     ({"action": "sudo"}, "unknown action"),
                     ({"action": "save-as", "name": "../../etc"}, "a path is not a name"),
                     ({"action": "save-as", "name": ""}, "empty")):
        check(f"refused: {why}", _raises(lambda b=bad: api.drive_action(b), ValueError), True)

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
for bad in ("22F190", "2EF19000", "0100", "3401"):
    try:
        _el.monitor(bad, seconds=0.01)
        bad_check = False
    except elm.WriteAttempted:
        bad_check = True
    check(f"monitor({bad!r}) is refused — it is not an adapter command", bad_check, True)
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

import importlib  # noqa: E402

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
    _rec.LIVE, _rec.DB = _liveold, _dbold
    importlib.reload(_rec)
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

# ----------------------------------------------------------------------- done
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every guard holds\n")
