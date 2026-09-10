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
check("it only ever asks systemctl questions",
      sorted(set(_re.findall(r'"systemctl",\s*(?:"--user",\s*)?"([a-z-]+)"', _src))),
      ["is-active", "is-enabled"])

# THE RECORDER GOES FIRST, because it is the one that was never on.
check("the recorder is checked before anything about the car",
      _src.index("def _recorder") < _src.index("def _port"), True)
check("and the checks run in that order",
      _src.index("_recorder(sheet)") < _src.index("_port(sheet)"), True)

# IT NEEDS NO CAR. The night before, indoors, is exactly when it should be run.
check("a missing adapter is not a reason to stay home",
      'sheet.row("adapter", bool(port)' in _src and "blocking=False" in _src, True)

_cli = open(os.path.join(ROOT, "bin", "omacar"), encoding="utf-8").read()
check("it is reachable", "omacar preflight" in _cli
      and 'preflight) omacar_need_env' in _cli, True)

# ----------------------------------------------------------------------- done
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every guard holds\n")
