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
import re
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


# ------------------------------------------------------------------- privacy
#
# NOTHING PRIVATE REACHES THE MODEL, BECAUSE THE MODEL IS A SPEAKER.
#
# install.sh registers this server under `mcp` in vortex.json, which lends its
# tools to the voice assistant: a question asked out loud in a car, answered
# out loud, through whatever is in the room and whatever is in the car park.
# The OTHER route to that same assistant -- share/context.d/60-omacar, the
# block Omarchy Vortex puts in front of every agent and every question -- has
# carried this rule since it was written, and argues it at length: the VIN, the
# plate and the driver's name are all in the vehicle record, all three identify
# a person and one specific car, and no question anybody asks an assistant
# needs any of them. Make, model and year answer "what does my car need" and
# single out nobody.
#
# `car_snapshot` returned records.snapshot() wholesale, vehicle record and all,
# so the two doors into the same model had opposite privacy discipline -- and
# the door with the full VIN behind it was the one an agent would actually
# knock on, because it is the one the tool list advertises.
#
# AN ALLOWLIST, NOT A REMOVAL LIST, for exactly the reason sitrep.CAR_ALLOWED
# is one: a denylist starts publishing whatever field somebody adds to the
# vehicle record next, silently, and an allowlist silently drops it instead.
# That is the failure you want. Widened past sitrep's three only by fields that
# describe a MODEL -- which engine, which gearbox, which protocol, how big the
# tank is -- because an agent needs them to answer anything and there is no car
# in the world they pick out.
#
# The odometer stays, and it is the one field here worth arguing about.
# 60-omacar rounds it to the nearest thousand miles because it is a preamble
# nobody asked for, injected into every prompt, and "about 190,000 miles"
# changes no advice. This tool is called deliberately, and half of what it is
# called for is the service book, whose every countdown is arithmetic on the
# exact reading. Rounding it here would not protect much and would make the
# next service date wrong, which is the one thing this project may not do.
VEHICLE_ALLOWED = (
    "year", "make", "model", "trim", "engine", "drivetrain", "protocol",
    "adapter", "simulated", "displacement_l", "power_kw", "mass_kg", "tank_l",
    "redline", "fuel_price", "odometer_km",
)

# The vehicle fields that name a person or one specific car. Never returned --
# the allowlist above already sees to that -- and gathered here so the scrub at
# the end of _redact() can hunt for them BY VALUE anywhere else in the payload.
# `title` and the top-level `name` are built from the driver's name by
# records.vehicle(), which is how a leak gets out of a field nobody thinks of
# as private.
VEHICLE_SECRET = ("vin", "plate", "driver", "owner", "title", "name", "label")

# What `section` may ask for, and which snapshot keys each one means. Grouped
# rather than mapped one to one because a section is a question -- "the faults"
# means the stored list and the active subset, not one of them.
SECTIONS = {
    "vehicle": ("vehicle",),
    "live": ("live", "connected", "status", "stale", "simulated"),
    "faults": ("faults", "active_faults"),
    "readiness": ("readiness",),
    "mode06": ("mode06",),
    "service": ("service",),
    "perf": ("perf",),
    "trips": ("trips",),
    "modules": ("modules",),
    "signals": ("signals", "actuators"),
}


# ------------------------------------------------------------------ the tools
TOOLS = [
    {
        "name": "car_snapshot",
        "description":
            "What OmaCar knows about the current vehicle without touching the "
            "bus: identity, odometer, stored faults with their first-seen "
            "dates, readiness monitors, Mode 06 on-board test results with the "
            "limit each was judged against, the service book, and recent trips. "
            "Start here. It is free, it is instant, and it answers most "
            "questions without waking a module.\n"
            "Returns a BRIEF by default: every section is present, the long "
            "lists are cut to the entries that answer a question, and the "
            "payload's `omitted` says per section what was left out and the "
            "argument that returns it. Nothing is hidden and nothing is "
            "silently shortened. Pass full=true for the whole record — about four "
            "times the brief on this project's own car, and it grows with the "
            "drive log — or section=\"faults\" for one section whole.\n"
            "The VIN, the registration plate and the driver's name are in none "
            "of these. `vehicle.vin_prefix` is the first 8 characters of the "
            "VIN, which describe the MODEL and no particular car; it is not a "
            "VIN and must not be reported as one. decode_vin takes a full VIN "
            "if you are given one.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "full": {"type": "boolean",
                         "description": "Return every section whole. Large. Ask "
                                        "for it when you genuinely need "
                                        "everything, not by default."},
                "section": {"type": "string",
                            "enum": sorted(SECTIONS),
                            "description": "Return one section whole, which is "
                                           "usually what `omitted` is telling "
                                           "you to do."},
            },
        },
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
    {
        "name": "lookup_signals",
        "description":
            "What other people have already published about this model: the "
            "OBDb community signal sets, as headers and requests you can send "
            "straight back with car_request. This is the shortcut past a "
            "seventy-minute sweep. An empty result is a real and common answer "
            "-- most models have not been mapped by anybody -- and it is not a "
            "failure. Everything here is a LEAD, not a finding: model years and "
            "markets differ, so check one against the car before believing it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "make": {"type": "string", "description": "e.g. Honda"},
                "model": {"type": "string", "description": "e.g. CR-Z"},
            },
            "required": ["make", "model"],
        },
    },
    {
        "name": "decode_vin",
        "description":
            "Ask NHTSA what a VIN is. OBD-II reports a VIN and nothing else: "
            "the make and year are derivable from the standard, but the MODEL "
            "genuinely is not, which is why OmaCar leaves it blank rather than "
            "guessing. This fills it in, free and with a citation, and gives "
            "you the make and model that lookup_signals needs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vin": {"type": "string",
                        "description": "17 characters. Omit to use the current vehicle's."},
            },
        },
    },
    {
        "name": "standard_dids",
        "description":
            "The ISO 14229 identification identifiers, F180 to F199, as "
            "requests. The cheapest useful thing to ask a module nobody has "
            "mapped: about a dozen reads, and the answers tell you what the "
            "module calls itself -- part number, software version, serial, "
            "manufacturing date.",
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


def _scrub(node, secrets):
    """Replace the car's identifying strings wherever they appear, by value.

    The last line of defence, and the same trick sitrep.redact() plays on the
    prose it mails out. The allowlist above governs the vehicle block, which is
    where these strings live today; this governs everywhere else, so a section
    added next year that happens to quote the plate -- an advisory, a note, a
    module's answer to F190, which IS the VIN -- cannot leak it just because
    nobody thought to add that section to a list here.

    Short strings are not hunted: see the caller.
    """
    if isinstance(node, dict):
        return {k: _scrub(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(v, secrets) for v in node]
    if isinstance(node, str):
        for bad in secrets:
            if bad in node:
                node = node.replace(bad, "this car")
        return node
    return node


# A VIN BY ITS SHAPE, NOT BY THE FIELD IT ARRIVED IN.
#
# Seventeen alphanumeric characters in a row, with nothing alphanumeric either
# side. In a car's records that is a VIN or it is a module serial number, and
# both of them name one vehicle. The allowlist above only knows about
# vehicle.vin; this knows about the one an agent will actually meet first --
# F190 is the VIN, F18C is a serial, `standard_dids` tells an agent to go and
# read them, and a survey that stores an answer in a module row would put one
# into the snapshot through a field nobody has added to any list here.
#
# It is a real cut and it says so where it cuts, because a masked token is a
# fact about this tool and a silently dropped one is a lie about the car. If
# what it caught was genuinely not an identifier, the record is still whole on
# disk and `omacar` at the keyboard still shows it to the person; only the
# thing wired to a speaker is squinting.
IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{17}(?![A-Za-z0-9])")
WITHHELD = "[17-character identifier withheld]"


def _plain(node):
    """Nothing VIN-shaped, and no seventeen-digit tails on the numbers.

    THE SECOND HALF OF THAT SENTENCE IS NOT COSMETIC. Python prints the double
    nearest 1/3.785411784 as 0.26417205235814845, and `units` carries exactly
    that as the litres-to-gallons factor. Serialised, its digits are a
    seventeen-character run: indistinguishable, to anything scanning this
    payload for a leaked VIN, from a leaked VIN -- so a guard that means "no
    identifiers left here" cannot be written while it is present, and a guard
    that cannot be written is a guard nobody has.

    Twelve significant figures is the cut. It is more precision than exists
    anywhere in this payload -- the car reports a fuel level of 34.5% and a
    coolant temperature to a tenth of a degree -- so no number changes by
    anything a person or a model could act on, and the last five digits of a
    binary float were never a measurement in the first place.
    """
    if isinstance(node, dict):
        return {k: _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    if isinstance(node, str):
        return IDENTIFIER.sub(WITHHELD, node)
    # Floats only. bool and int are left exactly as they are -- an odometer is
    # not improved by being reformatted, and a timestamp is not a measurement
    # anybody reads aloud.
    if isinstance(node, float):
        try:
            return float(f"{node:.12g}")
        except (ValueError, OverflowError):                   # noqa: BLE001
            return node
    return node


def _redact(snap):
    """A snapshot with nothing in it that says whose car this is.

    See the privacy note above VEHICLE_ALLOWED for why this exists at all. The
    mechanics:

    The vehicle block is REBUILT from the allowlist rather than edited, so a
    field nobody has thought about yet is absent by default instead of present
    by default.

    The VIN becomes its 8-character prefix. Characters 1-8 are the world
    manufacturer identifier and the vehicle descriptor section -- make, model,
    body, engine, and enough to place the model year -- and 9-17 are the check
    digit and the serial that names one car out of the millions built. That cut
    is profile.vin_prefix()'s, and it is asked for rather than sliced here so
    the two cannot drift apart later. It is enough to look a profile up and not
    enough to report a car stolen. The payload SAYS it is a prefix, because a
    model handed eight characters called `vin` would reasonably read them out
    as the VIN, and being wrong out loud is the failure mode of this whole
    surface.

    `title` and the top-level `name` go entirely: records.vehicle() builds them
    from the driver's name ("James' 2015 Honda CR-Z"), so the field that looks
    like a caption is the field that carries the person.
    """
    import profile as profilelib
    out = dict(snap)
    car = snap.get("vehicle") or {}

    keep = {k: car[k] for k in VEHICLE_ALLOWED if car.get(k) is not None}
    vin = str(car.get("vin") or "").strip()
    if vin:
        keep["vin_prefix"] = profilelib.vin_prefix(vin)
    out["vehicle"] = keep
    out.pop("title", None)
    out.pop("name", None)
    out["privacy"] = (
        "The VIN, the plate and the driver's name were removed from this "
        "answer by value: the exact strings the vehicle record holds were "
        "searched for and replaced with " + WITHHELD + ", along with any other "
        "17-character identifier. That is a scrub, not a proof — a fragment of "
        "one written some other way, or typed into a free-text note, can "
        "survive it, so treat anything that looks like a VIN as one and do not "
        "read it aloud. `vin_prefix` is the first 8 VIN characters: it "
        "describes the model, not this car, and it is not a VIN — do not read "
        "it out as one or send it to a VIN decoder as one.")

    # Hunt the removed strings anywhere else in the payload -- but not through
    # the vehicle block, which is already exactly what it is allowed to be, and
    # never for a string the allowlist deliberately kept. A car labelled "CR-Z"
    # by its owner would otherwise have every mention of the MODEL "CR-Z", in
    # every fault description, replaced with "this car".
    kept = {str(v).strip().lower() for v in keep.values()}
    secrets = []
    for key in VEHICLE_SECRET:
        s = str(car.get(key) or "").strip()
        # Four characters is sitrep's floor and it is the right one: shorter
        # strings ("Jim", a two-letter plate fragment) match inside ordinary
        # words and would corrupt the text rather than protect anybody. Those
        # fields are dropped outright above; this net is for copies of them.
        if len(s) >= 4 and s.lower() not in kept:
            secrets.append(s)
    if secrets:
        vehicle = out["vehicle"]
        out = _scrub({k: v for k, v in out.items() if k != "vehicle"}, secrets)
        out["vehicle"] = vehicle
    # And then by shape, over everything including the vehicle block, for the
    # identifier that arrived in a field no list here knows about.
    return _plain(out)


# ---------------------------------------------------------------------- size
#
# WHY THE DEFAULT IS A BRIEF, AND WHY THAT IS NOT A TRUNCATION.
#
# The whole record was 43 KB of JSON on this project's own car the day this was
# written, and it grows with the drive log: sixty days of rollups, every service
# item including the ten in good order, eleven Mode 06 tests of which two
# failed, twelve trips with their idle seconds. Every byte of it is true and
# almost none of it is the answer to the question that was asked. The brief is
# 11 KB of the same 46 KB record today, and every kilobyte it drops is named.
#
# The loop this feeds treats exactly that as its headline cost bug. converse.py
# spends a flag and a paragraph on --strict-mcp-config because without it a
# session silently gains a few hundred tool definitions and rewrites the prompt
# cache: $0.2457 for the word "pong" against $0.0030 with the flag, measured
# twice. A 43 KB tool result is the same bug wearing a different hat, and worse
# in one way -- it is paid for on the turn that fetched it and again on every
# turn after it, because it stays in the conversation.
#
# WHAT THIS IS NOT ALLOWED TO BE is a quiet shortening. A tool that returns
# part of an answer while looking like it returned all of it is the exact
# failure this project exists to not have, and it is worse than the size bug it
# would be fixing. So: every cut section carries its own COUNT, so the model
# can see there is more; `omitted` names each cut in words; and both arguments
# that fetch the rest are named in the payload, not just in the tool schema
# thirty tool definitions away. Nothing here is unreachable. It is merely no
# longer free by default.
def _brief(snap):
    """The snapshot cut to what a question needs, saying what it cut.

    The rule for each section is the same: keep the entries that ARE the
    answer, count the ones that are context. A fault that is still active is
    the answer to "what is wrong with my car", including the prose OmaCar
    wrote about it; a fault last seen in March is a code and a date. A Mode 06
    test that failed, or that is closest to failing, is the answer; the ones
    passing comfortably are a number. Sixty daily rollups are never the answer
    to anything asked out loud, and `perf` already carries the totals they add
    up to.
    """
    out = {k: snap[k] for k in
           ("checked", "units", "connected", "simulated", "status", "stale",
            "vehicle", "odometer", "live", "signals", "actuators",
            "have_history", "privacy") if k in snap}
    omitted = {}

    # The modules, as the map an agent actually uses: who is on the bus, at
    # what address, holding which codes. That is what car_request needs to be
    # aimed. The part number and the software version are what a mechanic
    # needs, they are per-module strings nobody asks a voice assistant for,
    # and they are a third of this list's bytes.
    modules = snap.get("modules") or []
    out["modules"] = [{k: m.get(k) for k in ("id", "name", "addr", "system", "codes")}
                      for m in modules]
    if modules:
        omitted["modules"] = (
            'the part number, software version and pos of each of the '
            f'{len(modules)} module(s). car_snapshot section="modules" has them.')

    # THE ACTIVE FAULTS KEEP THEIR PROSE, and that is the one thing in this
    # function worth defending. `detail` is OmaCar's own explanation of what
    # sets a code on THIS car -- checked, written down, attributable. Cut it
    # and a model asked "what is P1449" answers from memory instead, which is
    # how a hybrid battery code becomes a confident sentence about a starter
    # motor. What goes is what is derived or restated: `ago` and `since` are
    # `last_seen` and `first_seen` in seconds, `status` and `active` say the
    # same thing twice here, and the freeze frame is a diagnosis's evidence
    # rather than a question's answer.
    faults = snap.get("faults") or []
    active = snap.get("active_faults") or []
    past = [f for f in faults if not f.get("active")]

    def _fault(f):
        out_f = {k: f.get(k) for k in
                 ("code", "system", "descr", "detail", "severity",
                  "first_seen", "last_seen", "count")}
        out_f["module"] = (f.get("module") or {}).get("id")
        return out_f

    out["faults"] = {
        "stored": len(faults),
        "active": len(active),
        "active_detail": [_fault(f) for f in active],
        "inactive": [{"code": f.get("code"), "descr": f.get("descr"),
                      "last_seen": f.get("last_seen")} for f in past],
    }
    if faults:
        omitted["faults"] = (
            "per active fault: the freeze frame, and the derived `ago`/`since`/"
            "`status` fields. " +
            (f"The {len(past)} no longer active are code, description and date "
             f"only. " if past else "") +
            f'car_snapshot section="faults" returns all {len(faults)} whole.')

    m06 = snap.get("mode06") or []
    failing = [m for m in m06 if m.get("pass") is False]
    unrun = [m for m in m06 if m.get("pass") is None]
    # Headroom is value-over-limit: a test sitting at 0.95 has passed and is
    # about to stop passing, which is the only kind of passing test worth the
    # bytes. Named as the number it is, so nothing here reads as a failure.
    closest = sorted((m for m in m06 if m.get("pass") and m.get("headroom") is not None),
                     key=lambda m: m["headroom"], reverse=True)[:2]
    out["mode06"] = {
        "tests": len(m06), "failing": failing,
        "not_run": [m.get("mid") for m in unrun],
        "closest_passing": [{k: m.get(k) for k in
                             ("mid", "name", "value", "lo", "hi", "unit",
                              "headroom")} for m in closest],
    }
    quiet = len(m06) - len(failing) - len(unrun)
    if quiet > 0:
        omitted["mode06"] = (
            f"{quiet} test(s) passed; the {len(closest)} closest to their limit "
            f"are above, without their explanatory note. car_snapshot "
            f'section="mode06" returns all {len(m06)} whole, each with the '
            f"limit it was judged against.")

    rd = snap.get("readiness") or {}
    monitors = rd.get("monitors") or []
    unfinished = [m for m in monitors if m.get("supported") and not m.get("complete")]
    out["readiness"] = {k: v for k, v in rd.items() if k != "monitors"}
    out["readiness"]["monitors_total"] = len(monitors)
    out["readiness"]["monitors_incomplete"] = unfinished
    if len(monitors) > len(unfinished):
        omitted["readiness"] = (
            f"{len(monitors) - len(unfinished)} monitor(s) that are complete or "
            f'unsupported on this car. car_snapshot section="readiness" returns '
            f"all {len(monitors)}.")

    # The book cut to what is not fine, plus whatever `next` and `oil` point
    # at if they are fine -- "when is my next service" has to be answerable
    # from a car with nothing overdue. Named rather than repeated: records
    # hands back `next` and `oil` as whole copies of items that are usually
    # also in the attention list, and the same service item serialised three
    # times is a kilobyte of nothing.
    svc = snap.get("service") or {}
    items = svc.get("items") or []
    shown = [i for i in items if (i.get("state") or "ok") != "ok"]
    for pointer in (svc.get("next"), svc.get("oil")):
        if pointer and pointer not in shown:
            shown.append(pointer)
    out["service"] = {
        "due": svc.get("due"), "overdue": svc.get("overdue"),
        "items_total": len(items),
        "next": (svc.get("next") or {}).get("item"),
        "oil": (svc.get("oil") or {}).get("item"),
        "items": shown,
    }
    if len(items) > len(shown):
        omitted["service"] = (
            f"{len(items) - len(shown)} service item(s) in good order, with "
            f"their intervals and last-done dates. `next` and `oil` name items "
            f'in `items` above. car_snapshot section="service" is the whole book.')

    perf = snap.get("perf") or {}
    out["perf"] = {k: v for k, v in perf.items() if k not in ("days", "months")}
    days, months = perf.get("days") or [], perf.get("months") or []
    if days or months:
        omitted["perf"] = (
            f"the per-day rollups ({len(days)} day(s)) and the per-month totals "
            f"({len(months)} month(s)) — the raw series the day/week/month/year "
            f'figures above are computed from. car_snapshot section="perf" '
            f"returns them.")

    trips = snap.get("trips") or []
    out["trips"] = {"in_snapshot": len(trips), "recent": trips[:3]}
    if len(trips) > 3:
        omitted["trips"] = (
            f"{len(trips) - 3} older trip(s), of the {len(trips)} most recent "
            f'this snapshot carries. car_snapshot section="trips" returns those '
            f"{len(trips)}; the database holds every trip ever recorded and "
            f"`omacar trips` is how a person reads them.")

    out["omitted"] = omitted
    out["note"] = (
        "This is the brief, which is what car_snapshot returns by default: a "
        "long tool result is paid for on every turn of the conversation after "
        "the one that fetched it. Every section is here and every list here is "
        "counted, so nothing has been shortened behind your back — `omitted` "
        "says what was left out, section by section. Call car_snapshot again "
        "with section=\"<name>\" for one section whole (" +
        ", ".join(sorted(SECTIONS)) + "), or full=true for the entire record.")
    return out


def _flag(args, name):
    """A JSON boolean, or a word that plainly means one. Nothing else is true.

    `if args.get("full")` treated the STRING "false" as true, and a model that
    writes {"full": "false"} -- which they do -- got the whole record and the
    redaction budget with it. A flag that means the opposite of what was typed
    is worse than no flag.
    """
    v = args.get(name)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "on")
    return bool(v) if v is not None else False


def t_snapshot(args):
    snap = _redact(records.snapshot())

    section = (args.get("section") or "").strip()
    if section:
        keys = SECTIONS.get(section)
        if not keys:
            return failed(f"no such section: {section!r}. There are "
                          f"{len(SECTIONS)}: {', '.join(sorted(SECTIONS))}.")
        out = {"section": section, "checked": snap.get("checked"),
               "privacy": snap.get("privacy")}
        # Both arguments at once is a caller asking two things. Answer the
        # narrower one and SAY the other was not done, rather than letting it
        # look as though full=true had been honoured.
        if _flag(args, "full"):
            out["note"] = (f'full=true was not applied: section="{section}" '
                           f"names one section and this is that section, whole. "
                           f"Call again without `section` for the entire record.")
        out.update({k: snap.get(k) for k in keys})
        return as_json(out)

    if _flag(args, "full"):
        return as_json(snap)
    return as_json(_brief(snap))


def t_live(_):
    live = records.live() or {}

    # WHAT records.live() ACTUALLY HANDS BACK WHEN THERE IS NO NEWS, which is
    # never the empty dict this used to test for.
    #
    # A missing or unreadable live.json comes back as {"connected": False,
    # "status": "no daemon"}. A sample stamped with a different vehicle comes
    # back as a note saying so. A sample whose publisher has stopped comes back
    # with its moving values dropped and `stale_for` in their place. All three
    # are truthy, so the "nothing is publishing" branch below was unreachable;
    # and all three are missing `t`, so `time.time() - (live.get("t") or 0)`
    # measured the age of the UNIX EPOCH and reported it as the age of the
    # reading: 1,788,821,000 seconds, fifty-six years, to one decimal place.
    #
    # In the tool whose entire job is to say how old a reading is. A model
    # reading that either repeats a nonsense number out loud or -- far worse,
    # and more likely -- treats it as a glitch and answers from the values next
    # to it, which is precisely the "last Tuesday's road speed as the current
    # one" failure records.live() drops those values to prevent.
    #
    # So the age comes from a real timestamp or it is not reported at all.
    # `stale_for` is records.live()'s own measurement, taken from the `t` it
    # then dropped, and it is the honest age of a stale sample.
    stamp = live.get("t")
    stale_for = live.get("stale_for")
    if stamp:
        age = time.time() - stamp
    elif stale_for is not None:
        age = float(stale_for)
    else:
        age = None

    if age is None:
        # Nothing timestamped at all: nobody has ever published here, the file
        # is unreadable, or what is on disk is about another car. Whichever it
        # is, records.live() said so in words and those words are the answer;
        # inventing an age for a reading that does not exist is not.
        said = []
        if live.get("status"):
            said.append(f"records.live() reports: {live['status']}.")
        if live.get("note"):
            said.append(str(live["note"]))
        # Through the same shape guard as everything else, because the words
        # records.live() chose include the OTHER car's garage key -- and a
        # garage key is a VIN. See IDENTIFIER above.
        return text(IDENTIFIER.sub(WITHHELD, (
            "Nothing is publishing a live sample for this vehicle, so there is "
            "no reading and no age to report. " + " ".join(said)).strip()) +
            " Start the daemon with `omacar daemon start`, or the simulator "
            "with `omacar sim start`. car_snapshot works regardless.")

    out = dict(live, age_seconds=round(age, 1))
    # THE PUBLISHER'S STAMP IS NOT NEWS, AND IT IS A VIN. A daemon stamps each
    # sample with the garage key it is publishing for, so that records.live()
    # can refuse a sample about another car -- which it has already done by the
    # time we are here. The key is the vehicle's VIN, so passing the stamp on
    # would hand the full VIN to the speaker through the one tool that was not
    # being audited for it. What it was there to decide has been decided.
    out.pop("vehicle", None)
    if age > records.LIVE_STALE:
        # records.LIVE_STALE rather than a number retyped here: the daemon and
        # the simulator both publish five times a second, and the one place
        # that decides when silence means "stopped" should stay one place.
        out["stale"] = (
            f"Nothing has published for {int(age)} seconds, past the "
            f"{records.LIVE_STALE}-second window. Whoever was writing has "
            f"stopped. Treat anything here as the last known values, not "
            f"current ones, and say so if you repeat them.")
    return as_json(_plain(out))


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


def write_verdict(request):
    """The same refusal the screen and the CLI give, before anything is queued.

    A proposal the owner could never run is not worth their reading: a
    reprogramming service is refused by construction, and a configuration
    write into the emissions range is refused with the statute even in god
    mode. Judged at the highest tier on purpose -- the question here is not
    "may the owner do this now" but "could anybody, ever" -- so the answer
    does not change with whatever mode the machine happens to be in.
    Returns None when the proposal may be queued, else the refusal text.
    """
    import modes
    req = (request or "").replace(" ", "").upper()
    try:
        service = int(req[:2], 16)
    except ValueError:
        return "a request begins with a service byte, as two hex digits"
    if service in (0x34, 0x36, 0x37):
        return modes.decide("reprogram", "god").text()
    if service == 0x2E:
        if len(req) < 6:
            return "a write by identifier names a two-byte identifier after 2E"
        d = modes.decide("write_did", "god", ctx={"did": req[2:6]})
        if not d.ok:
            return d.text()
    return None


def t_request_write(args):
    refused = write_verdict(args.get("request"))
    if refused:
        return failed("Refused, and not queued. NOTHING WAS SENT.\n\n" + refused
                      + "\n\nThis is the same refusal the owner would get at the "
                        "keyboard, in any mode; there is nothing to wait for.")
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


def t_lookup(args):
    import knowledge
    make = args.get("make") or ""
    model = args.get("model") or ""
    if not make or not model:
        v = (records.snapshot().get("vehicle") or {})
        make = make or v.get("make") or ""
        model = model or v.get("model") or v.get("name") or ""
    if not make or not model:
        return failed("make and model are needed, and this vehicle's record "
                      "does not carry them. decode_vin will tell you both.")
    try:
        entries, meta = knowledge.signals(make, model)
    except Exception as why:                                  # noqa: BLE001
        return failed(f"could not reach OBDb: {why}")
    return as_json({"make": make, "model": model, "count": len(entries),
                    "signals": entries[:200], "source": meta})


def t_decode_vin(args):
    import knowledge
    asked = str(args.get("vin") or "").strip()
    vin = asked or (records.snapshot().get("vehicle") or {}).get("vin") or ""
    try:
        out, meta = knowledge.decode_vin(vin)
    except Exception as why:                                  # noqa: BLE001
        return failed(f"could not reach NHTSA: {why}")
    if meta.get("error"):
        return failed(meta["error"])
    # WHAT COMES BACK IS THE ANSWER, NOT THE QUESTION. car_snapshot goes to
    # great trouble to keep this car's VIN away from a model whose replies are
    # spoken out loud, and echoing it here would hand it straight back through
    # the next tool in the same list. The decoded make, model and year are what
    # was asked for; the VIN adds nothing to them.
    #
    # A VIN the CALLER supplied is different -- they already have it, and
    # confirming which one was decoded is the difference between an answer and
    # a guess -- so that one is echoed as its prefix, which is all that
    # identifies the model anyway.
    import profile as profilelib
    body = {"decoded": out, "source": meta}
    body["vin_prefix"] = profilelib.vin_prefix(vin)
    body["note"] = ("`vin_prefix` describes the model, not the car, and is not "
                    "a VIN. The full VIN is deliberately not in this answer: "
                    "this tool's replies may be spoken aloud."
                    if not asked else
                    "Decoded the VIN you supplied. Its prefix is echoed back "
                    "rather than the whole of it, which identifies one car.")
    return as_json(body)


def t_standard_dids(_):
    import knowledge
    return as_json({
        "dids": knowledge.ident_dids(),
        "note": "All reads, all service 0x22. Send them with car_request "
                "against a header car_profile lists, or against the engine.",
    })


HANDLERS = {
    "car_snapshot": t_snapshot,
    "car_live": t_live,
    "car_profile": t_profile,
    "car_mode": t_mode,
    "car_request": t_request,
    "propose_did": t_propose,
    "request_write": t_request_write,
    "lookup_signals": t_lookup,
    "decode_vin": t_decode_vin,
    "standard_dids": t_standard_dids,
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
