#!/usr/bin/env python3
"""The car, as tools an agent can call.

    omacar mcp

Speaks MCP over stdin/stdout, which is what Claude Code, Codex and Cursor all
already know how to launch. No dependency and no build step: the protocol is
JSON objects on a pipe, and hand-rolling it is thirty lines. The shape is
copied from Omarchy Vortex's pilot server rather than invented.

WHAT AN AGENT GETS, AND WHY THAT LIST IS SHORT.

Reads of what the tool already knows, one constrained read of the car itself,
and a way to write down what it worked out. That is the whole surface.

The interesting one is `car_request`. An agent can put a request on the bus --
which is the point, because a model that can read a manufacturer identifier and
then check it against a live car is doing something no static database does.
It goes through elm.Elm.request(), the same single gate every other caller uses,
so the service allowlist and the write arm apply to an agent exactly as they
apply to a person. It is further narrowed here to the three services a
discovery tool needs, because an agent has no business sending a session
control even when a technician legitimately does.

WHAT AN AGENT CANNOT DO, BY CONSTRUCTION.

Raise its own privilege. The mode lives on disk and the tools read it; nothing
here sets it. Be honest about the size of that claim, though: an agent with a
shell on this machine can edit that file, and every harness named above has a
shell. The true statement is narrower and still worth making -- OmaCar's own
interfaces will not let an agent escalate itself, so a confused-deputy call
through this server cannot do what the person at the keyboard has not allowed.

Promote its own guess. `propose_did` writes at confidence `proposed`, below
`candidate`, and lib/profile.py already refuses to let a machine award above
`observed`. An agent can find something, check it, and record what it saw. Only
a person comparing it against something real can call it validated. That
ceiling is the reason this project's data is worth anything.

Write to the car. `request_write` queues a proposal for a human and sends
nothing, ever. It is a tool so that an agent has somewhere to put a conclusion
it has genuinely reached, rather than either doing it or losing it.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import records                                                # noqa: E402

PROTOCOL = "2024-11-05"
NAME = "omacar"

# The services an AGENT may put on the bus. Narrower than the transport's read
# set, which also passes 0x01/0x02/0x03/0x06/0x07/0x09 -- those are the daemon's
# job and an agent asking for them is asking for something snapshot() already
# has, more slowly and over the port the gauge is using.
#
#   0x19  ReadDTCInformation -- the fault catalogue, and where the manufacturer
#         content on this project's one real car actually turned out to be
#   0x21  manufacturer-specific read, pre-UDS
#   0x22  ReadDataByIdentifier -- the range every published signal set uses
AGENT_SERVICES = {0x19, 0x21, 0x22}

QUEUE = os.path.join(records.STATE, "agent-writes.jsonl")
PROPOSED = os.path.join(records.STATE, "proposed.jsonl")


# ------------------------------------------------------------------ the tools
TOOLS = [
    {
        "name": "car_snapshot",
        "description":
            "Everything OmaCar knows about the current vehicle without touching "
            "the bus: identity, odometer, stored faults with their first-seen "
            "dates, readiness monitors, Mode 06 on-board test results with the "
            "limit each was judged against, the service book, and recent trips. "
            "Start here. It is free, it is instant, and it answers most "
            "questions without waking a module.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "car_live",
        "description":
            "The most recent live sample, if anything is publishing one. Says "
            "how old it is, because a sample nobody is publishing any more is "
            "not a current reading and this tool will not pretend otherwise.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "car_profile",
        "description":
            "What has been learned about this model: every identifier anyone "
            "has recorded, with its confidence -- proposed, candidate, "
            "observed, validated or refuted -- and where it came from. Read "
            "this before proposing anything, so you add to it rather than "
            "rediscover it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "car_request",
        "description":
            "Send one diagnostic request to a module and return what came back. "
            "Read services only: 0x19 (fault catalogue), 0x21 and 0x22 "
            "(manufacturer reads). This is how you check whether an identifier "
            "you found published somewhere is actually present on THIS car. "
            "Requires the car to be connected and a mode of at least power.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "header": {"type": "string",
                           "description": "Module address, e.g. 18DA10F1 or 7E0."},
                "request": {"type": "string",
                            "description": "Hex request, e.g. 22F190. Service "
                                           "byte must be 19, 21 or 22."},
            },
            "required": ["header", "request"],
        },
    },
    {
        "name": "propose_did",
        "description":
            "Record what you think an identifier means, with where you got it. "
            "Written at confidence 'proposed', which is below 'candidate' and "
            "cannot drive a gauge. You cannot raise it: only a person checking "
            "the value against something real -- a gauge on the dash, a second "
            "tool, a physical change they made -- awards 'validated'. Say where "
            "it came from in source_url; an entry with no provenance is worth "
            "less than no entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "header": {"type": "string"},
                "request": {"type": "string"},
                "name": {"type": "string",
                         "description": "What you think it is, e.g. 'coolant temperature'."},
                "formula": {"type": "string",
                            "description": "How to turn the bytes into a value, if you know."},
                "unit": {"type": "string"},
                "source_url": {"type": "string",
                               "description": "Where this came from. A published "
                                              "signal set, a forum post, a standard."},
                "reasoning": {"type": "string",
                              "description": "Why you believe it, including what "
                                             "you checked against the live car."},
            },
            "required": ["header", "request", "name", "source_url"],
        },
    },
    {
        "name": "request_write",
        "description":
            "Propose a write for a human to review. NOTHING IS SENT. It is "
            "queued with your stated consequence so the owner can read it, "
            "decide, and run it themselves. Use this when you have genuinely "
            "concluded something should be written -- it is better than doing "
            "nothing with the conclusion, and it is the only thing you may do "
            "with it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "header": {"type": "string"},
                "request": {"type": "string"},
                "consequence": {"type": "string",
                                "description": "What this does to the car, in the "
                                               "owner's words. Be specific and be "
                                               "honest about what you are unsure of."},
                "reasoning": {"type": "string"},
            },
            "required": ["header", "request", "consequence"],
        },
    },
    {
        "name": "car_mode",
        "description":
            "Which mode this machine is in and what it currently permits. You "
            "cannot change it -- that is the owner's decision, made at the "
            "keyboard. Read it so you can explain a refusal rather than "
            "retrying into one.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ----------------------------------------------------------------- the bodies
def text(s):
    return {"content": [{"type": "text", "text": s}]}


def failed(s):
    return {"content": [{"type": "text", "text": s}], "isError": True}


def as_json(obj):
    return text(json.dumps(obj, indent=1, default=str))


def _mode():
    import modes
    return modes.current()


def t_snapshot(_):
    return as_json(records.snapshot())


def t_live(_):
    live = records.live() or {}
    if not live:
        return text("Nothing is publishing a live sample. Start the daemon with "
                    "`omacar daemon start`, or the simulator with `omacar sim "
                    "start`. car_snapshot works regardless.")
    age = time.time() - (live.get("t") or 0)
    live = dict(live, age_seconds=round(age, 1))
    if age > 15:
        live["stale"] = ("Nothing has published for over fifteen seconds. Treat "
                         "these as the last known values, not current ones.")
    return as_json(live)


def t_profile(_):
    import profile as profilelib
    vin = ((records.snapshot().get("vehicle") or {}).get("vin") or "")
    try:
        doc = profilelib.for_vin(vin) if vin else None
    except Exception:                                         # noqa: BLE001
        doc = None
    if not doc:
        return text("No profile for this vehicle yet. That is a real answer: "
                    "nothing has been learned about this model on this machine. "
                    "car_request can start changing it.")
    return as_json(doc)


def t_mode(_):
    import modes
    tier = modes.current()
    return as_json({
        "tier": tier,
        "tiers": list(modes.TIERS),
        "permits": {a: modes.decide(a, tier).asdict() for a in sorted(modes.ACTIONS)},
        "note": "You cannot change this. It is the owner's decision, made at "
                "the keyboard.",
    })


def t_request(args):
    header = (args.get("header") or "").strip()
    req = (args.get("request") or "").replace(" ", "").strip().upper()
    if not header or not req:
        return failed("header and request are both required.")

    try:
        service = int(req[:2], 16)
    except ValueError:
        return failed(f"{req!r} does not begin with a service byte.")
    if service not in AGENT_SERVICES:
        allowed = ", ".join(f"0x{s:02X}" for s in sorted(AGENT_SERVICES))
        return failed(
            f"service 0x{service:02X} is not one an agent may send. Allowed: "
            f"{allowed} — the read services a discovery tool needs. Writing is "
            f"request_write, which queues for a human and sends nothing.")

    import modes
    d = modes.decide("read")
    if not d.ok:
        return failed(d.text())

    import connect
    import elm as elmlib
    import protocols

    # The RAW ELM client, not python-obd's OBD object: this sends a UDS request
    # to one module by header, which is not something python-obd models. Same
    # sequence dtc.py and prospect.py use, including the port lease -- an agent
    # asking for one identifier must not be able to take the adapter away from
    # the gauge and keep it.
    port, kind_of = connect.resolve()
    if not port:
        return failed("no adapter and no bench emulator. Plug one in, or run "
                      "`omacar bench start` to answer from a simulated ECU.")
    warn = connect.serial_group_warning(port)
    if warn:
        return failed(warn)
    if not connect.request_port(port):
        return failed(f"the daemon is holding {port} and did not let go. Stop "
                      f"it with `omacar daemon stop`, or wait for its lease.")

    try:
        conn = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
        conn.init()
    except Exception as why:                                  # noqa: BLE001
        try:
            connect.release_port()
        except Exception:                                     # noqa: BLE001
            pass
        return failed(f"could not open {port}: {why}")

    try:
        # The motion gate every other raw-UDS path takes. This only reads, but
        # it still puts an unfamiliar request on a live bus, and an agent is the
        # last caller that should be exempt from a rule a person obeys.
        if kind_of != "bench":
            import prospect
            mv = prospect.moving(conn)
            if mv:
                return failed("the car reports road speed. Nothing was sent: "
                              "unfamiliar requests go on a stationary bus.")
            if mv is None:
                return failed("road speed could not be read, so I cannot "
                              "confirm the car is stationary. Nothing was sent.")
        ok, why = protocols.header_ok(getattr(conn, "protocol", None), header)
        if not ok:
            return failed(f"{header!r} will not be sent: {why}")
        if not elmlib.aim(conn, header):
            return failed(
                f"{header!r} is not a valid address on the protocol this car "
                f"negotiated, so nothing was sent. Ask car_profile which "
                f"modules answer here.")
        lines = conn.request(req, patient=True, timeout=5.0)
        kind, detail, data = elmlib.classify(lines, service, req)
        return as_json({"header": header, "request": req, "kind": kind,
                        "detail": detail, "data": data,
                        "note": {"silent": "The module did not answer. That is a "
                                           "real result: record it as refuted "
                                           "rather than retrying.",
                                 "negative": "The module refused. `detail` says "
                                             "why.",
                                 }.get(kind, "")})
    finally:
        try:
            conn.close()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            connect.release_port()
        except Exception:                                     # noqa: BLE001
            pass                       # connect() may already have released it


def t_propose(args):
    import profile as profilelib
    entry = {
        "id": f"{args.get('header','').lower()}_{args.get('request','').lower()}",
        "header": args.get("header"),
        "request": (args.get("request") or "").replace(" ", "").upper(),
        "name": args.get("name"),
        "formula": args.get("formula") or "",
        "unit": args.get("unit") or "",
        # BELOW candidate. `candidate` means an ECU answered; this has not
        # necessarily been asked at all.
        "confidence": "proposed",
        "provenance": {
            "found_by": "agent",
            "found_on": (records.snapshot().get("vehicle") or {}).get("model")
                        or "unknown vehicle",
            "method": "proposed by an agent from a published source",
            "url": args.get("source_url"),
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_kind": "agent",
            "note": args.get("reasoning") or "",
        },
    }
    problems = profilelib.problems({"schema": profilelib.SCHEMA,
                                    "car": {"slug": "x", "make": "x", "model": "x"},
                                    "pid": [entry]})
    real = [p for p in problems if "car." not in p]
    if real:
        return failed("Refused:\n  " + "\n  ".join(real))
    # Its own file, not the shared profile. A proposal has not earned a line in
    # the document other people fetch: `omacar profile` is where entries an ECU
    # has actually answered live, and mixing the two would mean a downloaded
    # profile could carry a model's guesses as though a car had confirmed them.
    os.makedirs(records.STATE, exist_ok=True)
    with open(PROPOSED, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return text(
        f"Recorded {entry['id']} as PROPOSED, which is below candidate and "
        f"cannot drive a gauge. Check it with car_request; if a module answers, "
        f"say so in a follow-up proposal. Only a person comparing it against "
        f"something real can call it validated, and that is deliberate.")


def t_request_write(args):
    rec = {
        "at": time.time(),
        "header": args.get("header"),
        "request": (args.get("request") or "").replace(" ", "").upper(),
        "consequence": args.get("consequence"),
        "reasoning": args.get("reasoning") or "",
        "by": "agent",
        "status": "queued",
    }
    os.makedirs(records.STATE, exist_ok=True)
    with open(QUEUE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return text(
        "Queued for a human. NOTHING WAS SENT.\n\n"
        f"  {rec['header']}  {rec['request']}\n"
        f"  {rec['consequence']}\n\n"
        "The owner can read it with `omacar write list`, and run it themselves "
        "if they agree. That is the only thing this tool does with a write, and "
        "it is not a limitation to work around.")


HANDLERS = {
    "car_snapshot": t_snapshot,
    "car_live": t_live,
    "car_profile": t_profile,
    "car_mode": t_mode,
    "car_request": t_request,
    "propose_did": t_propose,
    "request_write": t_request_write,
}


def call(name, args):
    fn = HANDLERS.get(name)
    if fn is None:
        return failed(f"no such tool: {name}")
    return fn(args or {})


# ------------------------------------------------------------ the JSON-RPC
def reply(request_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": request_id}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main(argv=None):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method, request_id = message.get("method"), message.get("id")
        # A notification carries no id and must never be answered; replying to
        # one is a protocol error some clients treat as fatal.
        if request_id is None:
            continue

        if method == "initialize":
            reply(request_id, {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": NAME, "version": "1"},
            })
        elif method == "tools/list":
            reply(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params") or {}
            try:
                reply(request_id, call(params.get("name"),
                                       params.get("arguments") or {}))
            except Exception as e:                            # noqa: BLE001
                # A crash here silently ends the agent's turn, so everything
                # becomes a tool error the model can read and recover from.
                reply(request_id, failed(f"{type(e).__name__}: {e}"))
        elif method == "ping":
            reply(request_id, {})
        else:
            reply(request_id, error={"code": -32601,
                                     "message": f"unknown method: {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
