"""A validated identifier becomes a live reading.

THE DEAD END THIS CLOSES.

lib/profile.py has a four-state confidence ladder and a rule that nothing
below `validated` may drive a gauge. lib/prospect.py finds candidates,
lib/correlate.py promotes them to observed, and a person with a second tool or
a dash gauge promotes one to validated. And then nothing happened: no code in
the tree consumed a validated entry. The daemon polled python-obd's named
commands and nothing else, so an identifier the community had spent seventy
minutes and a real car establishing was a line in a TOML file and not a number
on a screen. Every step of the coverage strategy worked except the last one,
which is the one people see.

HOW IT RIDES THE EXISTING LINK.

python-obd's OBDCommand takes a per-command header, and OBD.query() sets it
before sending. So a validated entry -- header, request, formula -- becomes an
OBDCommand the daemon queries on the slow tier through the connection it
already holds. No second serial client, no fight over the port, no change to
the lease.

THE FORMULA IS EVALUATED, NEVER eval()'d.

A profile is a file somebody downloaded. Its formula is a string somebody else
wrote. The evaluator below understands numbers, the byte names A through H,
the four arithmetic operators, parentheses and unary minus, and nothing else --
which is the whole of what a scan-tool formula ever needs (Torque, OBDb and
every published PID list use this convention) and none of what an attacker
would want. An unknown token is a refusal, not a guess.

ONLY VALIDATED, AND THAT IS CHECKED HERE TOO.

This module re-checks the confidence field rather than trusting a caller to
have filtered. A profile that says `candidate` does not reach the daemon
however it arrived, because the rule "below validated must not drive a gauge"
is only a rule if every consumer enforces it.

29-BIT IS UNTESTED ON HARDWARE, and says so. python-obd sends the header with
ATSH, which the ELM327 accepts in eight-digit form for 29-bit CAN, and that is
what this project's one real car speaks. It has been verified on the bench
emulator, which is 11-bit. Until the first real 29-bit drive with a validated
entry, that line stays here.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BYTES = "ABCDEFGH"


# --------------------------------------------------------------- the formula
class FormulaError(ValueError):
    pass


_TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Ha-h])|([-+*/()]))")


def tokens(formula):
    out, pos = [], 0
    text = formula or ""
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            rest = text[pos:].strip()
            if not rest:
                break
            raise FormulaError(f"unexpected {rest[:12]!r} in formula {formula!r}")
        num, byte, op = m.groups()
        if num is not None:
            out.append(("num", float(num)))
        elif byte is not None:
            out.append(("byte", byte.upper()))
        else:
            out.append(("op", op))
        pos = m.end()
    return out


def evaluate(formula, data):
    """The number a formula gives for these payload bytes.

    `data` is the payload AFTER the service echo and the identifier -- for a
    22F181 reply of 62 F1 81 01 2C, it is [0x01, 0x2C], so A=1 and B=44.
    A formula naming a byte the reply does not have is a refusal.
    """
    toks = tokens(formula)
    if not toks:
        raise FormulaError("empty formula")
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def take():
        t = toks[pos[0]]
        pos[0] += 1
        return t

    def atom():
        kind, val = peek()
        if kind == "num":
            take()
            return val
        if kind == "byte":
            take()
            i = BYTES.index(val)
            if i >= len(data):
                raise FormulaError(
                    f"formula uses byte {val} but the reply carries only "
                    f"{len(data)} byte(s)")
            return float(data[i])
        if kind == "op" and val == "(":
            take()
            v = expr()
            k, o = peek()
            if k != "op" or o != ")":
                raise FormulaError("missing )")
            take()
            return v
        if kind == "op" and val == "-":
            take()
            return -atom()
        raise FormulaError(f"unexpected {val!r}")

    def term():
        v = atom()
        while True:
            k, o = peek()
            if k == "op" and o in "*/":
                take()
                r = atom()
                if o == "/":
                    if r == 0:
                        raise FormulaError("division by zero")
                    v = v / r
                else:
                    v = v * r
            else:
                return v

    def expr():
        v = term()
        while True:
            k, o = peek()
            if k == "op" and o in "+-":
                take()
                r = term()
                v = v + r if o == "+" else v - r
            else:
                return v

    v = expr()
    if pos[0] != len(toks):
        raise FormulaError("trailing tokens")
    return v


def is_valid_formula(formula):
    try:
        evaluate(formula, [0] * 8)
        return True
    except FormulaError:
        return False


# --------------------------------------------------------------- the entries
def validated(doc):
    """The profile entries allowed to drive a reading, and nothing else.

    Checked here rather than trusted: confidence must be exactly `validated`,
    the formula must parse, and the header and request must both be present.
    """
    out = []
    for p in (doc or {}).get("pid") or []:
        if p.get("confidence") != "validated":
            continue
        formula = (p.get("formula") or "").strip()
        header = (p.get("header") or "").replace(" ", "").upper()
        request = (p.get("request") or "").replace(" ", "").upper()
        if not (formula and header and request) or not is_valid_formula(formula):
            continue
        out.append({
            "id": p.get("id") or f"{header.lower()}_{request.lower()}",
            "name": p.get("name") or p.get("id") or request,
            "unit": p.get("unit") or "",
            "header": header,
            "request": request,
            "formula": formula,
        })
    return out


def payload_offset(request):
    """How many bytes of a positive reply are echo rather than data.

    0x22 replies 62 + the two identifier bytes; 0x21 replies 61 + one byte;
    a mode 01 PID replies 41 + one byte.
    """
    try:
        service = int(request[:2], 16)
    except ValueError:
        return 0
    return {0x22: 3, 0x21: 2, 0x01: 2}.get(service, 1)


def commands(doc):
    """python-obd commands for every validated entry, ready for the daemon.

    Imported lazily so that this module stays importable -- and testable --
    where pyserial is not installed, which is everywhere except the venv.
    """
    import obd
    out = []
    for e in validated(doc):
        skip = payload_offset(e["request"])
        formula = e["formula"]

        def decoder(messages, _skip=skip, _formula=formula, _name=e["name"]):
            for m in messages:
                data = list(bytes(m.data))[_skip:]
                if not data:
                    continue
                try:
                    return evaluate(_formula, data)
                except FormulaError:
                    return None
            return None

        out.append((e["id"], obd.OBDCommand(
            e["id"], e["name"], e["request"].encode(), 0, decoder,
            header=e["header"].encode())))
    return out


def catalogue(doc):
    """What the app needs to draw a tile: id, name, unit. No formula, no
    header -- the browser does not send requests, it shows numbers."""
    return [{"id": e["id"], "name": e["name"], "unit": e["unit"]}
            for e in validated(doc)]
