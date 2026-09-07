"""The four modes, and the one place that decides what they allow.

WHY THIS EXISTS AT ALL.

OmaCar had five permission gates that did not know about each other: the
service allowlist in elm.py, the arm file in write.py, the three preflight
checks in ops.py, the `--control` flag in serve.py, and per-route checks in
api.py that existed on seven of forty-one routes. Every one of them is correct.
None of them could answer "may this caller do this right now", because no two
of them agreed on what a caller was.

A mode built on top of that as a UI filter is falsifiable in one command:

    curl -X POST http://127.0.0.1:7560/api/clear

with the app sitting in simplified mode and the tablet showing no such button.
So the mode is decided here, on the server, and the screen reflects a decision
it did not make.

WHAT THIS IS NOT.

It is not a replacement for those five gates and it must never become one. It
runs *beside* them: decide() is asked first, and if it says yes the existing
checks still run and can still refuse. A bug in this file therefore fails
closed, to exactly the behaviour the tool had before it existed. elm.py stays
permanently dumb and absolute as the last floor -- it does not import this
module, and there is a test that says so.

THE TIERS.

    simplified   the owner asking "is my car OK, and what do I do?"
    power        the enthusiast who wants the numbers
    technician   the person with the car on a lift
    god          somebody reverse-engineering their own property

Higher tiers are supersets. A tier gates two different things and they are
worth naming separately: what a screen SHOWS, which is a matter of not
frightening somebody with hex they did not ask for, and what the tool SENDS,
which is a matter of what moves on a car with a person leaning over it.

WHAT NO MODE CHANGES.

The motion check, the voltage floor, the absence of reprogramming, the refusal
to guess a routine identifier, the port lease, and the confidence ladder. God
mode is not a way past any of them, and asserting that is most of what
test/guards_test.py does.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")

TIERS = ("simplified", "power", "technician", "god")
RANK = {t: i for i, t in enumerate(TIERS)}
DEFAULT = "power"

MODE_FILE = os.path.join(STATE, "mode")

# God mode decays. A tier that unlocks writing arbitrary configuration into a
# module is not a setting somebody should discover they left on a fortnight
# later, and the arm file already established that a dangerous state with no
# expiry is a dangerous state you forget about.
GOD_SECONDS = 1800


class Refused(Exception):
    """Raised where a caller wants an exception rather than a Decision."""


class Decision:
    """Yes or no, and the sentence to show either way.

    The sentence lives here rather than at each call site because it has four
    consumers -- the chip on the tablet, the CLI's refusal, an agent's tool
    error, and the panel -- and a refusal that is worded differently in each is
    four chances to sound like a different tool having a different problem.
    """

    __slots__ = ("ok", "reason", "citation", "needs", "action")

    def __init__(self, ok, action, reason="", citation="", needs=""):
        self.ok = ok
        self.action = action
        self.reason = reason
        self.citation = citation
        self.needs = needs

    def __bool__(self):
        return self.ok

    def raise_if_refused(self):
        if not self.ok:
            raise Refused(self.text())
        return self

    def text(self):
        out = self.reason
        if self.needs:
            out += f"\n  {self.needs}"
        if self.citation:
            out += f"\n  {self.citation}"
        return out

    def asdict(self):
        """The wire form. One shape for HTTP, stdio, argv and QML.

        This is the whole reason the decision is an object rather than a bool:
        the same dict crosses every boundary, so the greyed chip, the CLI
        refusal and the agent's error are the same sentence from the same line
        of Python rather than three that drift apart.
        """
        return {"ok": self.ok, "action": self.action, "reason": self.reason,
                "citation": self.citation, "needs": self.needs}


# ---------------------------------------------------------------- the actions
#
# `tier` is the lowest mode that may do it. `sends` says whether it puts bytes
# on the bus, which is the difference between a screen being cluttered and a
# fan starting while somebody's hands are near it.
ACTIONS = {
    # Reads. Free at the transport and free here; the tier only decides how
    # much of it a screen puts in front of somebody.
    "read":            {"tier": "simplified", "sends": True,
                        "what": "read a value the car already publishes"},
    "scan":            {"tier": "simplified", "sends": True,
                        "what": "walk every module and read its faults"},

    # Clearing is a write, and it is the one write an owner legitimately does
    # after fixing something themselves.
    "clear_codes":     {"tier": "simplified", "sends": True,
                        "what": "clear stored fault codes"},

    # Sweeps are reads, and they are gated anyway -- not at the transport,
    # where "reads are free" survives verbatim, but here, because a sweep holds
    # the port for minutes and can wake a sleeping module. Say so in the
    # comment or somebody will helpfully "fix" it back.
    "sweep":           {"tier": "technician", "sends": True,
                        "what": "sweep an unknown identifier range"},
    "learn":           {"tier": "power", "sends": True,
                        "what": "discover which modules answer"},

    # Commands the car. Everything below here moves something.
    "session":         {"tier": "technician", "sends": True,
                        "what": "put a module into an extended session"},
    "actuate":         {"tier": "technician", "sends": True,
                        "what": "command an actuator directly"},
    "routine":         {"tier": "technician", "sends": True,
                        "what": "run a built-in routine from a published definition"},
    "security_access": {"tier": "technician", "sends": True,
                        "what": "ask a module for a security seed"},

    # The one that writes configuration, and the only thing god mode adds to
    # the wire.
    "write_did":       {"tier": "god", "sends": True,
                        "what": "write a stored configuration value"},

    # Never, at any tier. Listed so that asking is answered rather than
    # unhandled, and so the refusal has the same shape as every other.
    "reprogram":       {"tier": None, "sends": True,
                        "what": "rewrite a module's firmware"},
    "guess_routine":   {"tier": None, "sends": True,
                        "what": "try a routine identifier nobody has published"},
}

NEVER = {
    "reprogram": (
        "Reprogramming is absent from this tool by construction, not disabled. "
        "It needs a manufacturer-signed firmware image OmaCar cannot produce, "
        "and a transfer that is interrupted leaves a module with no valid "
        "firmware at all. That is a tow truck, not a fault code."),
    "guess_routine": (
        "Routine identifiers are never discovered by sweeping. Unlike a value "
        "you can read, a routine is a procedure the module RUNS -- guessing its "
        "number could spin a radiator fan, cycle an ABS pump or retract a "
        "parking brake with a wheel off. There is no harmless miss, so there is "
        "no sweep, and no mode enables one."),
}


# ------------------------------------------------------ the emissions line
#
# THIS LIST IS NAMED, NOT COMPLETE, AND THE DIFFERENCE IS ON SCREEN.
#
# 37 CFR 201.40(b)(13) and (b)(14) say an owner may diagnose, repair and
# lawfully modify their own vehicle, and may access and share its data. Both
# paragraphs also say, verbatim, that eligibility is not a safe harbor from EPA
# or DOT regulation -- and the Clean Air Act is the separate thing it is not a
# safe harbor from. Section 203(a)(3) prohibits rendering an emission control
# inoperative, and EPA's 2020 enforcement policy states that this covers
# software, naming tuners that reprogram engine function while overriding the
# OBD system.
#
# So god mode opens everything except this. A deny-list cannot be complete --
# identifiers are manufacturer-specific and undocumented, and claiming
# completeness would be exactly the kind of confident invention this project
# refuses everywhere else. It says so where it refuses, and the caveat is part
# of the refusal rather than a footnote somewhere else.
CAA_CITATION = ("Clean Air Act s203(a)(3) — rendering an emission control "
                "inoperative. EPA's 2020 enforcement policy states this covers "
                "software, not only hardware.")

DENY_COVERAGE = ("This list is named, not exhaustive. Identifiers are "
                 "manufacturer-specific and mostly undocumented, so OmaCar "
                 "cannot promise it recognises every emissions-related write. "
                 "It refuses the ones it knows and does not pretend to more.")

# Ranges and identifiers that are emissions-related by the standard rather than
# by guesswork. 0xF400-0xF5FF is the legislated OBD range in ISO 14229 -- the
# J1979 emissions PIDs surfaced through service 0x22 -- so a write into it is
# a write to an emissions control by definition rather than by inference.
DENY_RANGES = [
    (0xF400, 0xF5FF, "the legislated OBD identifier range (ISO 14229)"),
]

DENY_IDS = {
    # Deliberately empty until something is verified on a real car. An invented
    # entry here would be the same failure as an invented fault code: it looks
    # like knowledge, and it is not. The range above is the part that is true
    # from the standard.
}


def emissions_verdict(did):
    """Is writing this identifier an emissions-defeat write?

    Returns (denied, why). `did` is the identifier as hex text, with or without
    spaces. An unparseable identifier is not denied here -- it is refused
    earlier, by the transport, for being malformed.
    """
    clean = (did or "").replace(" ", "").upper()
    try:
        n = int(clean, 16)
    except ValueError:
        return False, ""
    if clean in DENY_IDS:
        return True, DENY_IDS[clean]
    for lo, hi, what in DENY_RANGES:
        if lo <= n <= hi:
            return True, what
    return False, ""


# ------------------------------------------------------------------ the mode
def current():
    """The mode this machine is in. Unset, unreadable or unknown is DEFAULT."""
    try:
        with open(MODE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return DEFAULT
    tier = d.get("tier")
    if tier not in RANK:
        return DEFAULT
    # God mode decays rather than persisting. A reboot re-asks.
    if tier == "god":
        until = d.get("until") or 0
        try:
            if time.time() >= float(until):
                return "technician"
        except (TypeError, ValueError):
            return "technician"
    return tier


def set_mode(tier):
    """Move to a tier. Returns the tier actually in force."""
    if tier not in RANK:
        raise ValueError(f"unknown mode {tier!r}; one of {', '.join(TIERS)}")
    os.makedirs(STATE, exist_ok=True)
    rec = {"tier": tier, "at": time.time()}
    if tier == "god":
        rec["until"] = time.time() + GOD_SECONDS
    tmp = MODE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, MODE_FILE)
    return current()


def allows(tier, action):
    """Does this tier reach this action, ignoring context?"""
    spec = ACTIONS.get(action)
    if spec is None or spec["tier"] is None:
        return False
    return RANK.get(tier, -1) >= RANK[spec["tier"]]


def decide(action, tier=None, ctx=None):
    """May this happen, right now, in this mode?

    ctx carries whatever the action needs to be judged on beyond its name --
    today just `did` for a configuration write. Unknown keys are ignored rather
    than refused, so a caller can pass everything it knows.
    """
    ctx = ctx or {}
    tier = tier or current()
    spec = ACTIONS.get(action)

    if spec is None:
        return Decision(False, action,
                        reason=f"{action!r} is not something this tool does.",
                        needs="If it should be, it needs an entry in "
                              "lib/modes.py ACTIONS with a tier.")

    if spec["tier"] is None:
        return Decision(False, action, reason=NEVER.get(action, "Refused."),
                        needs="No mode enables this.")

    if not allows(tier, action):
        need = spec["tier"]
        return Decision(
            False, action,
            reason=f"{spec['what'].capitalize()} needs {need} mode; "
                   f"this machine is in {tier} mode.",
            needs=f"switch with:  omacar mode {need}")

    if action == "write_did":
        denied, what = emissions_verdict(ctx.get("did"))
        if denied:
            return Decision(
                False, action,
                reason=f"Refusing to write {ctx.get('did')}: it is in {what}.",
                citation=f"{CAA_CITATION}\n  {DENY_COVERAGE}",
                needs="Nothing was sent.")

    return Decision(True, action)


# ------------------------------------------------------------------- the CLI
def main(argv):
    if not argv or argv[0] in ("status", "show"):
        tier = current()
        print(f"\n  mode: {tier}\n")
        for a in sorted(ACTIONS):
            spec = ACTIONS[a]
            d = decide(a, tier)
            mark = "yes" if d.ok else " no"
            print(f"    {mark}  {a:17} {spec['what']}")
        print()
        return 0
    if argv[0] in TIERS:
        now = set_mode(argv[0])
        print(f"  mode: {now}")
        if now == "god":
            print(f"  decays to technician in {GOD_SECONDS // 60} minutes")
        return 0
    print(f"  usage: omacar mode [{'|'.join(TIERS)}]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
