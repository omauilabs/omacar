"""What the car is actually speaking, and what that changes.

Everything in OmaCar was written against CAN, because every car since 2008 in
the US uses it and the development vehicle is a 2015. But the cars nobody will
spend twelve thousand dollars to diagnose are exactly the older ones, and they
are not on CAN. Supporting them is not a matter of a slower baud rate: four
things change, and getting any of them wrong looks like a car with nothing to
say.

WHAT ACTUALLY DIFFERS.

**Header shape.** `ATSH` takes three hex digits on 11-bit CAN, eight on 29-bit,
and six on J1850 and ISO 9141 -- three bytes of priority, target and source.
Sending a CAN-shaped header on ISO 9141 is not rejected loudly; the adapter
takes it and the car simply never answers.

**Framing.** ISO-TP -- the first-frame/consecutive-frame protocol that
`elm.reassemble` implements -- is a CAN construct. On J1850 and ISO 9141 a long
reply arrives as repeated lines each carrying the full header, and reassembling
them with PCI logic produces confident nonsense.

**Timing.** ISO 9141-2 runs at 10.4 kbaud against CAN's 500, and its
initialisation is a 5-baud address sequence taking two to three seconds. Our
sweep timings were tuned on CAN; applied unchanged they time out before a
healthy slow car has finished speaking.

**What is worth asking.** Service 0x22 barely exists before CAN. Manufacturer
data on these cars lives in older enhanced modes, and sweeping 0x22 across a
1998 vehicle is sixty thousand requests guaranteed to find nothing. Discovery
has to ask what the protocol can answer.

WHAT THIS FILE DOES NOT DO.

It does not claim to have been tested on a pre-CAN car. The development fleet
is a 2015 CR-Z and a 2012 Fit, both CAN. Everything here follows from the
ELM327 datasheet and the relevant ISO documents, and every entry says which
family it belongs to so somebody with a 1999 vehicle can check it rather than
trust it. `verified` is a field here for the same reason it is one in a
profile.
"""

# ELM327 protocol numbers, as reported by ATDPN.
FAMILY_CAN = "can"
FAMILY_J1850 = "j1850"
FAMILY_ISO = "iso9141"
FAMILY_KWP = "kwp2000"
FAMILY_J1939 = "j1939"

PROTOCOLS = {
    "1": {"name": "SAE J1850 PWM", "family": FAMILY_J1850, "baud": 41600,
          "header_digits": 6, "iso_tp": False, "typical": "Ford, 1996-2004",
          "default_header": "61 6A F1", "verified": False},
    "2": {"name": "SAE J1850 VPW", "family": FAMILY_J1850, "baud": 10400,
          "header_digits": 6, "iso_tp": False, "typical": "GM, 1996-2005",
          "default_header": "68 6A F1", "verified": False},
    "3": {"name": "ISO 9141-2", "family": FAMILY_ISO, "baud": 10400,
          "header_digits": 6, "iso_tp": False,
          "typical": "Chrysler, and most European and Asian, 1996-2004",
          "default_header": "68 6A F1", "verified": False},
    "4": {"name": "ISO 14230-4 KWP (5-baud init)", "family": FAMILY_KWP,
          "baud": 10400, "header_digits": 6, "iso_tp": False,
          "typical": "1996-2006", "default_header": "68 6A F1",
          "verified": False},
    "5": {"name": "ISO 14230-4 KWP (fast init)", "family": FAMILY_KWP,
          "baud": 10400, "header_digits": 6, "iso_tp": False,
          "typical": "1996-2006", "default_header": "68 6A F1",
          "verified": False},
    "6": {"name": "ISO 15765-4 CAN 11/500", "family": FAMILY_CAN, "baud": 500000,
          "header_digits": 3, "iso_tp": True, "typical": "most cars since 2008",
          "default_header": "7DF", "verified": True},
    "7": {"name": "ISO 15765-4 CAN 29/500", "family": FAMILY_CAN, "baud": 500000,
          "header_digits": 8, "iso_tp": True, "typical": "Honda, and others",
          "default_header": "18DB33F1", "verified": True},
    "8": {"name": "ISO 15765-4 CAN 11/250", "family": FAMILY_CAN, "baud": 250000,
          "header_digits": 3, "iso_tp": True, "typical": "uncommon on cars",
          "default_header": "7DF", "verified": False},
    "9": {"name": "ISO 15765-4 CAN 29/250", "family": FAMILY_CAN, "baud": 250000,
          "header_digits": 8, "iso_tp": True, "typical": "uncommon on cars",
          "default_header": "18DB33F1", "verified": False},
    "A": {"name": "SAE J1939 CAN 29/250", "family": FAMILY_J1939, "baud": 250000,
          "header_digits": 8, "iso_tp": True,
          "typical": "heavy trucks and buses", "default_header": "18EAFFF9",
          "verified": False},
}


def describe(dpn):
    """Whatever ATDPN said, as something we can reason about.

    ATDPN answers with the protocol number, sometimes prefixed with 'A' when
    it was reached by auto-search ('A6' rather than '6'). Stripping that is not
    cosmetic: 'A6' is not a key in the table, and a lookup miss would silently
    fall back to CAN assumptions on a car that is not on CAN.
    """
    if not dpn:
        return None
    s = str(dpn).strip().upper()
    if s.startswith("A") and len(s) > 1:
        s = s[1:]
    return PROTOCOLS.get(s)


def is_can(dpn):
    p = describe(dpn)
    return bool(p and p["family"] in (FAMILY_CAN, FAMILY_J1939))


def uses_iso_tp(dpn):
    p = describe(dpn)
    return bool(p and p["iso_tp"])


def header_ok(dpn, header):
    """Is this header the right shape for this protocol?

    Returns (ok, detail). A wrong-shaped header is the failure that looks most
    like a dead car: the adapter accepts it and nothing ever answers.
    """
    p = describe(dpn)
    if not p:
        return True, ""          # unknown protocol: not our place to refuse
    clean = (header or "").replace(" ", "")
    want = p["header_digits"]
    if len(clean) != want:
        return False, (f"{p['name']} wants a {want}-digit header; "
                       f"{header!r} has {len(clean)}")
    return True, ""


# ---------------------------------------------------------------- addressing

def broadcast(dpn):
    """The address every module on this bus listens to.

    Callers used to write the functional broadcast inline -- `"7DF"` in
    ops.clear_codes and candlog.read_trusted, `"07DF"` in prospect.moving.
    Both are 11-bit CAN literals, and only one of them is even the right
    length for it; on the 29-bit CAN this project's own development car
    speaks, the functional address is `18DB33F1` and neither literal reaches
    anything. The table above already knows the answer for every protocol, so
    ask it rather than assume the wire.

    Spaces are stripped because the pre-CAN entries are written `68 6A F1` to
    be readable as three bytes and ATSH wants the digits.

    An unknown protocol falls back to `7DF` rather than to nothing. That is
    not a guess about the car: it is what every one of these call sites did
    unconditionally before, so a connection that never recorded a protocol --
    the bench emulator, a test double -- behaves exactly as it did.
    """
    p = describe(dpn)
    if not p:
        return "7DF"
    return p["default_header"].replace(" ", "")


# The addresses worth asking one at a time, once the broadcast has told you
# somebody is home.
#
# These are kept as ADDRESSES rather than headers because the header is a
# different shape on every family and the address is the part that carries the
# meaning. physical() below builds the header.
#
# 11-bit CAN is the odd one out: ISO 15765-4 fixes the eight legislated
# request identifiers as 7E0-7E7 and fixes nothing about which module sits
# behind which, beyond 7E0 being the engine. Labelling the rest by function
# would be inventing knowledge, so they are numbered. On the CR-Z the IMA
# motor and battery controllers answer on the neighbours of 7E0, which is
# where the six-address default in `prospect` came from.
MODULES_CAN11 = [
    ("7E0", "engine"),
    ("7E1", "ECU 2"),
    ("7E2", "ECU 3"),
    ("7E3", "ECU 4"),
    ("7E4", "ECU 5"),
    ("7E5", "ECU 6"),
    ("7E6", "ECU 7"),
    ("7E7", "ECU 8"),
]

# 29-bit CAN addresses one module per byte, and those bytes DO carry meaning
# across makes. This list is the one discover.CANDIDATE_HEADERS already sweeps,
# kept here in address form so that file can be pointed at it without changing
# what it asks.
MODULES_29BIT = [
    ("10", "engine"),
    ("18", "transmission"),
    ("28", "ABS / brakes"),
    ("01", "body"),
    ("03", "hybrid / battery"),
    ("04", "hybrid / motor"),
    ("0E", "gateway / other"),
    ("40", "airbag / restraints"),
    ("60", "instrument cluster"),
    ("6A", "climate"),
]

# J1850, ISO 9141-2 and KWP2000 all use a three-byte header of format, target
# and source. The targets below are the ISO 14230 conventional ones. UNTESTED
# BY THIS PROJECT -- see the module docstring; they are here so a sweep on a
# pre-CAN car asks something plausible instead of nothing, not because anyone
# has watched a 1999 car answer them.
MODULES_PRECAN = [
    ("10", "engine"),
    ("18", "transmission"),
    ("28", "ABS / brakes"),
]


def physical(dpn):
    """[(header, label)] -- the modules worth addressing directly, shaped for
    this protocol.

    Every caller that wants "the list of modules" has so far written its own,
    and every one of them wrote it for a single protocol: prospect's is 11-bit
    CAN with a leading zero that fits neither, dtc's and discover's are 29-bit
    only. A car on the other protocol gets a list it cannot use, which is not
    a wrong answer from the module -- it is a question that never left the
    adapter.

    An unknown protocol returns the 11-bit list, for the same reason
    broadcast() falls back to 7DF: it is what the callers assumed before, so
    nothing that works today starts failing.

    J1939 returns nothing at all. Heavy trucks address by PGN and source
    address rather than by an ISO 15765 diagnostic pair, so a list of
    18DAxxF1 headers would be confidently wrong, and a caller that gets an
    empty list can say "I do not know how to sweep this bus" -- which is true.
    """
    p = describe(dpn)
    if not p:
        return [(h, lbl) for h, lbl in MODULES_CAN11]
    if p["family"] == FAMILY_J1939:
        return []
    if p["family"] == FAMILY_CAN:
        if p["header_digits"] == 3:
            return [(h, lbl) for h, lbl in MODULES_CAN11]
        return [("18DA%sF1" % addr, lbl) for addr, lbl in MODULES_29BIT]
    # Three-byte header: keep this protocol's own format and source bytes and
    # vary only the target, so the shape stays whatever the table says works.
    base = p["default_header"].replace(" ", "")
    fmt, src = base[:2], base[4:6]
    return [(fmt + addr + src, lbl) for addr, lbl in MODULES_PRECAN]


# Sweep pacing. CAN can be hammered; a 10.4 kbaud line cannot, and the ELM's
# own timeout has to be long enough for a slow car to finish a reply.
def pacing(dpn):
    p = describe(dpn)
    if not p:
        return {"delay": 0.06, "timeout": 5.0, "atst": None}
    if p["family"] == FAMILY_CAN:
        return {"delay": 0.02, "timeout": 5.0, "atst": "20"}
    if p["baud"] >= 41000:            # J1850 PWM
        return {"delay": 0.08, "timeout": 8.0, "atst": "40"}
    # The slow lines: ISO 9141-2, KWP, J1850 VPW.
    return {"delay": 0.15, "timeout": 12.0, "atst": "80"}


def discovery_services(dpn):
    """Which services are worth sweeping on this protocol, and why.

    Sweeping UDS 0x22 across a 1998 car is sixty thousand requests that cannot
    succeed, and a tool that does it looks broken rather than thorough.
    """
    p = describe(dpn)
    if not p:
        return [(0x22, "UDS read-by-identifier")]
    if p["family"] in (FAMILY_CAN, FAMILY_J1939):
        return [(0x22, "UDS read-by-identifier"),
                (0x21, "older manufacturer read")]
    if p["family"] == FAMILY_KWP:
        # KWP2000 has readDataByLocalIdentifier, a single-byte id.
        return [(0x21, "KWP read-by-local-identifier"),
                (0x22, "KWP read-by-common-identifier")]
    # J1850 and ISO 9141: enhanced data is mode 0x22 on a few makes and
    # manufacturer-defined elsewhere. 0x21 is the common one.
    return [(0x21, "manufacturer enhanced read")]


def id_width(dpn, service):
    """How many hex digits the identifier takes for this service.

    0x21 on the older protocols is a ONE-byte local identifier, not the
    two-byte DID that 0x22 uses. Sweeping it with four digits asks 65536
    questions where 256 exist, and every one of them is malformed.
    """
    if service == 0x22:
        return 4
    if service == 0x21:
        return 2
    return 4


def summary(dpn):
    p = describe(dpn)
    if not p:
        return f"unknown protocol {dpn!r}"
    seal = "" if p["verified"] else "  (untested by this project)"
    return (f"{p['name']} — {p['typical']}, {p['baud']} baud, "
            f"{p['header_digits']}-digit headers, "
            f"{'ISO-TP' if p['iso_tp'] else 'no ISO-TP'}{seal}")
