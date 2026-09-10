"""Car profiles: manufacturer-specific PIDs OmaCar has learned.

Read with tomllib (stdlib since 3.11), written by hand-rolled formatting.
No YAML dependency — the schema is small and the file is meant to be edited
by a person after the prospector drafts it.

WHY THE FORMAT IS THIS SHAPE.

A profile is the unit of the whole coverage argument: sweeping a car's
manufacturer identifier range takes about seventy minutes, but only once per
*model* if the result is shared. That only works if a profile someone else
wrote can be trusted, and trust here is not a feeling — it is provenance
recorded per entry.

Three decisions carry most of the weight:

**Confidence is a field with four states, including `refuted`.** Recording that
an identifier was tried and is *wrong* is nearly as valuable as recording a hit,
because it stops the next person spending an hour re-testing it. A format with
only "known" entries throws that away.

**Provenance is per entry, not per file.** One profile accumulates work from
several people across several model years, and "who found this and how" has to
survive being merged. A file-level author field collapses the moment two people
contribute.

**Only a VIN PREFIX is ever stored, never the whole VIN.** The first eight
characters carry the manufacturer, model and model year — exactly what a
profile needs in order to match a car. The remaining nine include the serial
number, which identifies one specific vehicle and, in practice, its owner. A
shared database of full VINs would be a privacy problem shipped as a feature.

ON "SIGNED".

`checksum` is a SHA-256 over the canonical body. It detects corruption and
accidental edits in transit. It is **not** a signature: it proves nothing about
who wrote the file, because anyone changing the content can recompute it.
Real signing needs a key distribution story this project does not yet have, and
calling a checksum a signature would be exactly the kind of overclaim the rest
of this tool refuses to make.
"""
import hashlib
import json
import os
import time
import tomllib

SCHEMA = 1

PROFILE_DIRS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles"),
    os.path.expanduser(
        os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar/profiles"),
]

# Ordered weakest to strongest. `refuted` sits outside that order on purpose:
# it is not "less confident than a candidate", it is a different claim.
# proposed is FIRST because it is lowest. It is the only state that does not
# require the car to have been asked: somebody -- usually an agent -- read a
# published signal set or a standard and wrote down what an identifier ought to
# be. That is genuinely useful, and it is not evidence about THIS car. Keeping
# it below candidate is what stops "I found it on the internet" from being
# recorded at the same weight as "an ECU answered".
CONFIDENCE = ("proposed", "candidate", "observed", "validated", "refuted")

CONFIDENCE_MEANS = {
    "proposed":  "Somebody read this somewhere and thinks it applies. The car "
                 "has not been asked. Provenance says where it came from; "
                 "until an ECU answers it is a lead, not a finding.",
    "candidate": "The ECU answered. Nothing more is known — it may be a "
                 "constant, a part number, or padding.",
    "observed":  "The bytes were seen to change across samples, so it carries "
                 "*something*. What it means is still a guess.",
    "validated": "Checked against something real — a gauge on the dash, a "
                 "second tool, or a physical change somebody made — and it "
                 "matched. Safe to drive a display.",
    "refuted":   "Tried and found wrong. Kept deliberately, so nobody spends "
                 "an hour rediscovering that it does not work.",
}


def _merge(base, over):
    """`over` wins, without deleting what it does not mention.

    Lists of tables are merged by identity rather than replaced, so a profile
    that records one new broadcast entry does not drop the four the bundled
    file already had.
    """
    def ident(row):
        for k in ("id", "name", "header", "test"):
            if row.get(k) not in (None, ""):
                return (k, row[k])
        return ("_", repr(sorted(row.items())))

    out = dict(base)
    for k, v in over.items():
        old = base.get(k)
        if (isinstance(v, list) and isinstance(old, list)
                and all(isinstance(x, dict) for x in v + old)):
            by = {}
            for row in old + v:                 # later wins on a collision
                by[ident(row)] = row
            out[k] = list(by.values())
        elif isinstance(v, dict) and isinstance(old, dict):
            out[k] = {**old, **v}
        else:
            out[k] = v
    return out


def load(slug):
    """The bundled profile, with whatever this machine has learned on top.

    IT USED TO RETURN THE FIRST MATCH AND STOP, and the bundled copy is first.
    So on any car that ships with a profile -- which is the car this was built
    for -- `omacar listen adopt` wrote its finding to the state copy and every
    reader in the app kept loading the bundled one. The adoption was invisible.
    Worse, the next adoption re-read the bundled file and overwrote the state
    copy, so the first finding was gone as well.

    Reading them all and folding them in order fixes both without the other
    obvious answer's cost: simply reversing the search would make a stale state
    copy mask every later improvement to the bundled file. Here the bundled
    file stays the base, the machine's own findings sit on top, and a list of
    tables is merged by identity rather than replaced -- so recording one new
    broadcast entry cannot drop the four that were already there.

    Returns (doc, path) where path is the STRONGEST file found, which is the
    one a writer should be writing to.
    """
    doc, where = None, None
    for d in PROFILE_DIRS:
        for name in (slug + ".toml", slug + ".draft.toml"):
            p = os.path.join(d, name)
            if not os.path.exists(p):
                continue
            with open(p, "rb") as f:
                found = tomllib.load(f)
            doc = found if doc is None else _merge(doc, found)
            where = p
            break                    # one file per directory: .toml beats .draft
    return doc, where


def available():
    out = []
    for d in PROFILE_DIRS:
        if os.path.isdir(d):
            out += [f[:-5] for f in sorted(os.listdir(d))
                    if f.endswith(".toml") and not f.endswith(".draft.toml")]
    return sorted(set(out))


def vin_prefix(vin):
    """The part of a VIN that describes the MODEL, never the specific car.

    Characters 1-8 are the world manufacturer identifier and the vehicle
    descriptor section: make, model, body, engine, and enough to place the
    model year. Character 9 is a check digit and 10-17 identify one vehicle.

    Truncating here rather than at the point of sharing is deliberate -- if the
    full VIN is never written into a profile, it cannot leak from one later.
    """
    v = (vin or "").strip().upper()
    return v[:8] if len(v) >= 8 else ""


# ---- integrity --------------------------------------------------------------

# The single source of truth for what a profile may contain. normalize() drops
# everything else and dumps() writes exactly these, so the two cannot drift --
# an unknown key used to be hashed but not written, which silently broke the
# checksum of any file carrying one.
CAR_KEYS = ("slug", "make", "model", "description", "protocol", "years",
            "vin_prefix", "engine", "drivetrain", "trim",
            # Physical facts, for anything that has to MODEL the car rather
            # than just read it -- the simulator most of all. They belong here
            # for the same reason the modules do: lib/sim.py currently hard-
            # codes one car's mass, power and redline, which is why its
            # invented "6-speed manual" was read as fact about a real CR-Z
            # and repeated to its owner. A simulator driven by the same
            # profile as the real car cannot describe a different vehicle.
            "mass_kg", "power_kw", "tank_l", "displacement_l", "redline")

# ---- what the car is MADE OF, as opposed to what has been discovered on it --
#
# Everything above this line describes identifiers somebody found. Everything
# below describes the car itself: which modules answer, what is worth polling,
# and which optional screens apply. It lives in the same file and the same
# format on purpose.
#
# WHY NOT A SECOND FILE.
#
# Because a second format is how a framework dies. The moment "what this car
# is" and "what we know about this car" live in different places they drift,
# and somebody has to keep two things in step by hand. A profile is already
# the unit that gets shared between owners of the same model; the shape of
# their car belongs in it.
#
# WHY IT MATTERS AT ALL.
#
# Today lib/telemetry.py hardcodes the PID tiers, lib/ima.py hardcodes two
# Honda hybrid headers, and lib/dtc.py hardcodes four more. Somebody with a
# Golf gets an IMA screen that can never populate and a fault sweep aimed at
# modules their car does not have. None of that is a bug in those files -- it
# is a car-shaped constant sitting in code that ought to be generic.
MODULE_KEYS = ("header", "label", "role", "confidence", "provenance")

# The roles a module can play. Open-ended would be friendlier and much worse:
# a screen that switches on "hybrid-battery" cannot act on a typo, and a typo
# is exactly what an open vocabulary produces.
ROLES = ("engine", "transmission", "hybrid-battery", "hybrid-motor",
         "abs", "body", "gateway", "cluster", "climate", "restraints",
         "other")

# Poll tiers, fastest first. The names match lib/telemetry.py's own constants
# so that a profile reads the way the code already talks.
POLL_TIERS = ("fast", "mid", "slow")

# Optional screens a car may or may not be able to fill. A flag here is a
# claim that the car HAS the thing, not that OmaCar has found it yet -- `ima`
# on a CR-Z is true from the moment you know it is a hybrid, long before any
# state of charge has been read.
SCREENS = ("ima",)
PID_KEYS = ("id", "name", "header", "request", "service", "payload_len",
            "varying_bytes", "formula", "unit", "confidence", "provenance")
# url / retrieved_at / source_kind carry a claim that came from outside this
# machine. Without them a proposal is indistinguishable from a measurement, and
# the whole point of the ladder is that those are different things.
PROV_KEYS = ("found_by", "found_on", "vin_prefix", "method", "first_seen",
             "samples", "validated_by", "validated_on", "validated_against",
             "refuted_by", "refuted_on", "note",
             "url", "retrieved_at", "source_kind", "model")
META_KEYS = ("created", "updated", "contributors", "checksum")

# An actuator entry names WHAT to move, never a request to send. lib/actuate.py
# composes the request itself as 0x2F + did + state, so a shared profile
# cannot smuggle any other service through this table however it is written.
# `session`, if present, is the 0x10 request to make first, and is checked to
# be one.
ACTUATOR_KEYS = ("test", "name", "header", "did", "on", "off", "session",
                 "confidence", "provenance")

# A SIGNAL NOBODY ASKED FOR.
#
# Every other entry in this format describes a REQUEST: send these bytes to
# this module and read the answer. A broadcast signal is not that. It is a byte
# in a frame the car emits continuously, addressed to nobody, which is how an
# instrument cluster learns the state of charge and which way a dashboard
# switch is set -- and frequently there is no diagnostic identifier for it at
# all. Sweeping 8,192 of them on this project's own car found nothing while the
# dashboard showed the number the whole time.
#
# So it needs its own shape: an arbitration id, a byte position, and either a
# set of named states (a switch) or a formula over the frame's bytes (a value).
# `can_id` is 3 or 8 hex digits because both widths ride the same wire.
#
# It carries no request and cannot be turned into one. Nothing in this table
# can put a frame on the bus, which is the whole reason it is safe to share:
# the worst a wrong entry can do is mislabel a number on a screen.
BROADCAST_KEYS = ("id", "name", "can_id", "byte", "kind", "states", "formula",
                  "unit", "confidence", "provenance")

# A switch has named positions; a value has a formula. Anything else is not
# something this can read.
BROADCAST_KINDS = ("enum", "value")


def normalize(doc):
    """The document as it will actually be written.

    Hashing and writing MUST agree, and they did not: dumps() drops empty
    provenance fields while canonical() hashed them, so a file failed its own
    checksum the moment it was written -- which would have made every shared
    profile look tampered with. Normalising first, then hashing the normalised
    form, makes the round trip lossless by construction rather than by two
    functions happening to stay in step.
    """
    src = json.loads(json.dumps(doc, default=str))
    keep = lambda d, ks: {k: v for k, v in (d or {}).items()
                          if k in ks and v is not None and v != ""}
    out = {"schema": src.get("schema", SCHEMA), "car": keep(src.get("car"), CAR_KEYS)}
    meta = keep(src.get("meta"), META_KEYS)
    if meta:
        out["meta"] = meta
    pids = []
    for p in src.get("pid") or []:
        q = keep(p, PID_KEYS)
        prov = keep(p.get("provenance"), PROV_KEYS)
        if prov:
            q["provenance"] = prov
        else:
            q.pop("provenance", None)
        pids.append(q)
    out["pid"] = pids

    # The capability sections. Each is omitted entirely when absent rather
    # than written empty, so a profile that predates them normalises to
    # exactly what it did before and keeps its existing checksum. A format
    # change that invalidated every shared profile's integrity hash would be
    # a poor way to introduce one.
    mods = []
    for m in src.get("module") or []:
        q = keep(m, MODULE_KEYS)
        if not q.get("header"):
            continue
        q["header"] = str(q["header"]).upper()
        if q.get("role") not in ROLES:
            # An unknown role is kept as "other" rather than dropped: the
            # module still exists and is still worth sweeping for faults, and
            # silently losing it would be worse than not knowing its job.
            q["role"] = "other"
        prov = keep(m.get("provenance"), PROV_KEYS)
        if prov:
            q["provenance"] = prov
        else:
            q.pop("provenance", None)
        mods.append(q)
    if mods:
        out["module"] = mods

    acts = []
    for a in src.get("actuator") or []:
        q = keep(a, ACTUATOR_KEYS)
        for k in ("header", "did", "on", "off", "session"):
            if q.get(k) is not None:
                q[k] = str(q[k]).replace(" ", "").upper()
        prov = keep(a.get("provenance"), PROV_KEYS)
        if prov:
            q["provenance"] = prov
        else:
            q.pop("provenance", None)
        acts.append(q)
    if acts:
        out["actuator"] = acts

    casts = []
    for b in src.get("broadcast") or []:
        q = keep(b, BROADCAST_KEYS)
        if q.get("can_id") is not None:
            q["can_id"] = str(q["can_id"]).replace(" ", "").upper()
        if q.get("byte") is not None:
            try:
                q["byte"] = int(q["byte"])
            except (TypeError, ValueError):
                q.pop("byte", None)
        if isinstance(q.get("states"), dict):
            # Keys are byte values written as two hex digits, so a reader never
            # has to guess whether 10 meant sixteen or ten.
            q["states"] = {str(k).replace(" ", "").upper().rjust(2, "0"): str(v)
                           for k, v in q["states"].items()}
        prov = keep(b.get("provenance"), PROV_KEYS)
        if prov:
            q["provenance"] = prov
        else:
            q.pop("provenance", None)
        casts.append(q)
    if casts:
        out["broadcast"] = casts

    poll = {}
    for tier in POLL_TIERS:
        names = (src.get("poll") or {}).get(tier)
        if isinstance(names, list):
            # De-duplicated, order preserved: the order is a statement about
            # what matters most on a slow serial link.
            seen, keptn = set(), []
            for n in names:
                n = str(n).strip().upper()
                if n and n not in seen:
                    seen.add(n)
                    keptn.append(n)
            if keptn:
                poll[tier] = keptn
    if poll:
        out["poll"] = poll

    screens = {}
    for s in SCREENS:
        v = (src.get("screens") or {}).get(s)
        if v is not None:
            screens[s] = bool(v)
    if screens:
        out["screens"] = screens
    return out


# ---- reading the capability sections ---------------------------------------
#
# Every one of these takes the caller's own default and returns it untouched
# when the profile says nothing. That is what makes this safe to adopt one
# call site at a time: a car with no profile, or a profile written before
# these sections existed, behaves exactly as it did before.

def _doc(profile):
    """A profile document, from a document or a slug.

    load() returns (doc, path) rather than a doc, so the slug path unpacks.
    Anything that fails to load is an empty document and every reader below
    then falls back to the caller's default -- a car with no profile must
    behave exactly as it did before profiles existed.
    """
    if isinstance(profile, dict):
        return profile
    if not profile:
        return {}
    try:
        doc, _path = load(profile)
        return doc or {}
    except Exception:
        return {}


def modules(profile, default=None):
    """[(header, label)] for this car, or the caller's default."""
    doc = _doc(profile)
    mods = doc.get("module") or []
    if not mods:
        return default if default is not None else []
    return [(m["header"], m.get("label") or m.get("role") or m["header"])
            for m in mods if m.get("header")]


def modules_by_role(profile, role, default=None):
    """{header: label} for every module playing one role."""
    doc = _doc(profile)
    mods = [m for m in (doc.get("module") or []) if m.get("role") == role]
    if not mods:
        return default if default is not None else {}
    return {m["header"]: (m.get("label") or role) for m in mods}


def poll(profile, tier, default=None):
    """The PID names for one tier, or the caller's default."""
    doc = _doc(profile)
    names = (doc.get("poll") or {}).get(tier)
    return list(names) if names else (list(default) if default else [])


def screen(profile, name, default=False):
    """Whether this car can fill an optional screen at all."""
    doc = _doc(profile)
    v = (doc.get("screens") or {}).get(name)
    return default if v is None else bool(v)


def slug_for_current_car():
    """The profile slug for the vehicle the garage is pointing at.

    ONE COPY OF THIS, DELIBERATELY. lib/prospect.py grew its own answer to
    "which car is this about" -- a hardcoded default -- and filed a Porsche's
    measurements under a Honda. The lesson from that, and from the payload
    offsets before it, is that a second copy of a piece of knowledge is a
    second chance to disagree. Everything that writes a per-car file asks here.

    A car with a matching profile gets its slug. One without gets a name built
    from its VIN prefix, which is honest and unmistakable rather than
    inheriting somebody else's. Nothing gets a guess.
    """
    import garage
    try:
        key = garage.current()
    except Exception:                                         # noqa: BLE001
        return "unknown-car"
    if not key or key in (getattr(garage, "SIM_KEY", "simulated"), "unknown"):
        return "unknown-car"
    slug = for_vin(key)
    if slug:
        return slug
    prefix = vin_prefix(key)
    return ("unknown-" + prefix.lower()) if prefix else "unknown-car"


def for_vin(vin):
    """The profile slug whose vin_prefix matches this car, or None.

    Matching on the model half of the VIN and never the whole thing is the
    same rule vin_prefix() exists for: a profile is about a MODEL, and the
    moment it can be tied to one specific car it has become personal data
    that other owners of that model should not be inheriting.

    Returns a slug rather than a document so callers can cache the cheap
    thing. None means "no profile for this car", which every reader above
    already handles by returning the caller's own default.
    """
    want = vin_prefix(vin)
    if not want:
        return None
    for slug in available():
        doc, _path = load(slug)
        if not doc:
            continue
        got = str((doc.get("car") or {}).get("vin_prefix") or "").upper()
        if got and want.startswith(got):
            return slug
    return None


def canonical(doc):
    """A stable byte representation of a profile's meaning.

    JSON with sorted keys rather than the TOML text, so that reformatting,
    reordering entries or changing comments does not change the checksum --
    only a change to the actual content does. `meta.checksum` is excluded, for
    the obvious reason.
    """
    doc = normalize(doc)
    body = {k: v for k, v in doc.items() if k != "meta"}
    meta = {k: v for k, v in (doc.get("meta") or {}).items() if k != "checksum"}
    if meta:
        body["meta"] = meta
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      default=str).encode()


def checksum(doc):
    return "sha256:" + hashlib.sha256(canonical(doc)).hexdigest()


def verify(doc):
    """(ok, detail). A profile with no checksum is unverified, not corrupt."""
    have = (doc.get("meta") or {}).get("checksum")
    if not have:
        return None, "no checksum recorded"
    want = checksum(doc)
    if have == want:
        return True, ""
    return False, f"checksum mismatch: file says {have[:19]}…, content is {want[:19]}…"


# ---- validation -------------------------------------------------------------

# THE ONLY SERVICES A SHARED PROFILE MAY ASK FOR.
#
# A profile describes things to READ off a car, and it arrives from somebody
# else's machine. Nothing in the format ever needed to carry a write, and until
# this list existed nothing stopped it: `omacar profile fetch` pulls a TOML from
# a URL and candlog sent its `request` field at an ECU. The transport refuses
# writes unless armed -- but a technician arms the tool to do their own work,
# and a pooled profile does not get to ride along on that.
#
# Deliberately narrower than the transport's read set. 0x19 is a fault
# catalogue and 0x21/0x22 are manufacturer reads; those are what a profile is
# for. A profile has no business sending 0x10, 0x27, or anything that changes
# the car.
SAFE_SERVICES = {0x01, 0x09, 0x19, 0x21, 0x22}


def problems(doc):
    """Everything wrong with a profile, as a list of human sentences.

    Returns [] for a good one. Written to be shown to a contributor before
    they share a file, not to be caught as an exception.
    """
    out = []
    if doc.get("schema") != SCHEMA:
        out.append(f"schema is {doc.get('schema')!r}, expected {SCHEMA}")
    car = doc.get("car") or {}
    for field in ("slug", "make", "model"):
        if not car.get(field):
            out.append(f"car.{field} is missing")
    years = car.get("years")
    if years and (not isinstance(years, list) or len(years) != 2):
        out.append("car.years should be [from, to]")

    seen = set()
    for i, p in enumerate(doc.get("pid") or []):
        where = p.get("id") or p.get("name") or f"entry {i + 1}"
        if not p.get("id"):
            out.append(f"{where}: no id, so it cannot be merged or superseded")
        elif p["id"] in seen:
            out.append(f"{where}: duplicate id")
        else:
            seen.add(p["id"])
        for field in ("header", "request"):
            if not p.get(field):
                out.append(f"{where}: {field} is missing")
        req = (p.get("request") or "").replace(" ", "")
        if req:
            try:
                service = int(req[:2], 16)
            except ValueError:
                out.append(f"{where}: request {req!r} does not begin with a "
                           f"service byte")
            else:
                if service not in SAFE_SERVICES:
                    allowed = ", ".join("0x%02X" % x for x in sorted(SAFE_SERVICES))
                    out.append(
                        f"{where}: request 0x{service:02X} is not a read. A "
                        f"shared profile may only carry {allowed} — it describes "
                        f"what to read off a car, and this one would send "
                        f"something else to somebody's ECU.")
        conf = p.get("confidence")
        if conf not in CONFIDENCE:
            out.append(f"{where}: confidence {conf!r} is not one of "
                       f"{', '.join(CONFIDENCE)}")
        prov = p.get("provenance") or {}
        if not prov.get("found_on"):
            out.append(f"{where}: provenance.found_on is missing — a reader "
                       f"cannot tell which car this came from")
        if conf == "validated" and not prov.get("validated_against"):
            out.append(f"{where}: claims validated but does not say against "
                       f"what. That is the difference between evidence and "
                       f"assertion.")
        # A full VIN must never appear anywhere in a profile.
        for k, v in prov.items():
            if isinstance(v, str) and len(v.strip()) == 17 and v.strip().isalnum():
                out.append(f"{where}: provenance.{k} looks like a full VIN. "
                           f"Store only a prefix (see vin_prefix).")

    hexchars = set("0123456789ABCDEF")

    def is_hex(v, length=None):
        v = str(v or "").replace(" ", "").upper()
        return (bool(v) and len(v) % 2 == 0 and set(v) <= hexchars
                and (length is None or len(v) == length))

    seen_tests = set()
    for i, a in enumerate(doc.get("actuator") or []):
        where = f"actuator {a.get('test') or i + 1}"
        if not a.get("test"):
            out.append(f"{where}: no test, so no button can use it")
        elif a["test"] in seen_tests:
            out.append(f"{where}: duplicate test")
        else:
            seen_tests.add(a["test"])
        hdr = str(a.get("header") or "").replace(" ", "").upper()
        if not (hdr and set(hdr) <= hexchars and len(hdr) in (3, 6, 8)):
            out.append(f"{where}: header must be a 3-, 6- or 8-digit hex address")
        if not is_hex(a.get("did"), 4):
            out.append(f"{where}: did must be exactly two bytes (four hex digits)")
        if not is_hex(a.get("on")):
            out.append(f"{where}: on must be the control option and state, as hex")
        if a.get("off") not in (None, "") and not is_hex(a.get("off")):
            out.append(f"{where}: off must be hex (default 00, return control)")
        sess = str(a.get("session") or "").replace(" ", "").upper()
        if sess and not (sess.startswith("10") and is_hex(sess, 4)):
            out.append(f"{where}: session must be a 0x10 request such as 1003; "
                       f"nothing else may be sent before the command")
        for k in ("request", "service", "payload"):
            if k in a:
                out.append(f"{where}: carries `{k}`. An actuator names an "
                           f"identifier and a state; the request is composed "
                           f"by the tool as 0x2F + did + state and cannot be "
                           f"supplied.")
        conf = a.get("confidence")
        if conf not in CONFIDENCE:
            out.append(f"{where}: confidence {conf!r} is not one of "
                       f"{', '.join(CONFIDENCE)}")
        prov = a.get("provenance") or {}
        if not prov.get("found_on"):
            out.append(f"{where}: provenance.found_on is missing")
        if conf == "validated" and not prov.get("validated_against"):
            out.append(f"{where}: claims validated but does not say against "
                       f"what -- for an actuator that means what physically "
                       f"moved, seen by whom.")

    seen_bcast = set()
    for i, b in enumerate(doc.get("broadcast") or []):
        where = f"broadcast {b.get('id') or i + 1}"
        if not b.get("id"):
            out.append(f"{where}: no id, so it cannot be merged or superseded")
        elif b["id"] in seen_bcast:
            out.append(f"{where}: duplicate id")
        else:
            seen_bcast.add(b["id"])
        cid = str(b.get("can_id") or "").replace(" ", "").upper()
        if not (cid and set(cid) <= hexchars and len(cid) in (3, 8)):
            out.append(f"{where}: can_id must be a 3- or 8-digit hex "
                       f"arbitration identifier")
        byte = b.get("byte")
        if not isinstance(byte, int) or not 0 <= byte <= 7:
            out.append(f"{where}: byte must be 0-7, the position in the frame")
        kind = b.get("kind")
        if kind not in BROADCAST_KINDS:
            out.append(f"{where}: kind {kind!r} is not one of "
                       f"{', '.join(BROADCAST_KINDS)}")
        elif kind == "enum":
            states = b.get("states")
            if not isinstance(states, dict) or not states:
                out.append(f"{where}: an enum needs `states`, mapping byte "
                           f"values to what they mean")
            else:
                for k in states:
                    # One byte, written as hex. normalize() pads a single
                    # digit, so complaining about "3" would be complaining
                    # about something this file itself fixes.
                    kk = str(k).replace(" ", "").upper()
                    if len(kk) not in (1, 2) or not set(kk) <= hexchars:
                        out.append(f"{where}: state key {k!r} should be one "
                                   f"byte, as one or two hex digits")
        elif kind == "value" and not str(b.get("formula") or "").strip():
            out.append(f"{where}: a value needs a formula over the frame's "
                       f"bytes (A is byte 0)")
        # A BROADCAST ENTRY CANNOT BE TURNED INTO A REQUEST, and the format
        # will not carry the fields that would let somebody try. This is the
        # reason the section is safe to share at all: the worst a wrong entry
        # can do is mislabel a number on a screen.
        for k in ("request", "header", "service", "did", "on", "off"):
            if k in b:
                out.append(f"{where}: carries `{k}`. A broadcast signal is read "
                           f"from a frame the car already sends; nothing in "
                           f"this table may describe something to transmit.")
        conf = b.get("confidence")
        if conf not in CONFIDENCE:
            out.append(f"{where}: confidence {conf!r} is not one of "
                       f"{', '.join(CONFIDENCE)}")
        prov = b.get("provenance") or {}
        if not prov.get("found_on"):
            out.append(f"{where}: provenance.found_on is missing")
        if conf == "validated" and not prov.get("validated_against"):
            out.append(f"{where}: claims validated but does not say against "
                       f"what. For a broadcast byte that means the thing you "
                       f"watched change while it did.")
    return out


# ---- merging ----------------------------------------------------------------

RANK = {"refuted": 4, "validated": 3, "observed": 2, "candidate": 1, "proposed": 0}


def merge(base, incoming):
    """Combine two profiles for the same car. Returns (doc, notes).

    THE RULE: better evidence wins, and `refuted` beats everything.

    A validated entry replaces a candidate, because somebody checked it against
    something real. A refutation replaces even a validated entry, because the
    cost of acting on a wrong identifier is higher than the cost of losing a
    right one -- if the refutation is itself wrong, the fix is to re-validate
    and say so, which leaves a record either way.

    Equal confidence keeps the incumbent, so merging is idempotent and does not
    depend on the order files are combined in.
    """
    out = json.loads(json.dumps(base, default=str))
    notes = []
    by_id = {p.get("id"): i for i, p in enumerate(out.get("pid") or [])}
    out.setdefault("pid", [])

    for p in incoming.get("pid") or []:
        pid = p.get("id")
        if not pid:
            notes.append("skipped an entry with no id")
            continue
        if pid not in by_id:
            out["pid"].append(p)
            by_id[pid] = len(out["pid"]) - 1
            notes.append(f"added {pid} ({p.get('confidence')})")
            continue
        cur = out["pid"][by_id[pid]]
        if RANK.get(p.get("confidence"), -1) > RANK.get(cur.get("confidence"), -1):
            out["pid"][by_id[pid]] = p
            notes.append(f"{pid}: {cur.get('confidence')} -> {p.get('confidence')}")
        else:
            notes.append(f"{pid}: kept {cur.get('confidence')}")

    meta = out.setdefault("meta", {})
    who = (incoming.get("meta") or {}).get("contributors") or []
    meta["contributors"] = sorted(set((meta.get("contributors") or []) + who))
    meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["checksum"] = checksum(out)
    return out, notes


# ---- writing ----------------------------------------------------------------

def _q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _arr(xs):
    return "[" + ", ".join(str(x) for x in xs) + "]"


# WHAT dumps() ITSELF DOES NOT KNOW ABOUT.
#
# The writer below is hand-rolled, section by section, and it knew about five:
# car, meta, pid, broadcast and actuator. The shipped CR-Z profile also carries
# [[module]], [poll] and [screens] -- so loading a profile and writing it back
# DELETED all three, silently, and the only writer of profiles is the step that
# records what a drive discovered. Adoption would have thrown away the module
# map, the polling tiers and the screen switches in the act of saving a
# finding.
#
# This carries anything the named sections did not handle through verbatim, so
# a section added tomorrow survives a writer that has never heard of it. Being
# generic it is plainer than the hand-written parts above -- which is the right
# trade: unfamiliar data should be preserved exactly, not prettily.
_KNOWN_SECTIONS = ("schema", "car", "meta", "pid", "broadcast", "actuator")


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return _q(str(v))


def _dump_unknown(doc):
    """Every top-level section dumps() has no hand-written case for."""
    out = []
    for key in doc:
        if key in _KNOWN_SECTIONS:
            continue
        value = doc[key]
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            for row in value:
                out.append("")
                out.append(f"[[{key}]]")
                for k, v in row.items():
                    if isinstance(v, dict):
                        continue          # handled below, as a nested table
                        
                    out.append(f"{k} = {_toml_value(v)}")
                for k, v in row.items():
                    if isinstance(v, dict):
                        out.append("")
                        out.append(f"  [{key}.{k}]")
                        for kk, vv in v.items():
                            out.append(f"  {kk} = {_toml_value(vv)}")
        elif isinstance(value, dict):
            out.append("")
            out.append(f"[{key}]")
            for k, v in value.items():
                if isinstance(v, dict):
                    continue
                out.append(f"{k} = {_toml_value(v)}")
            for k, v in value.items():
                if isinstance(v, dict):
                    out.append("")
                    out.append(f"  [{key}.{k}]")
                    for kk, vv in v.items():
                        out.append(f"  {kk} = {_toml_value(vv)}")
        else:
            out.append("")
            out.append(f"{key} = {_toml_value(value)}")
    return out


def dumps(doc):
    """A profile as TOML, written for a person to edit afterwards.

    Hand-rolled rather than via a library because the schema is small, the
    output is meant to be read, and the comments explaining each confidence
    level are part of the format's job -- a contributor should not have to find
    the documentation to know what `observed` commits them to.
    """
    car = doc.get("car") or {}
    meta = doc.get("meta") or {}
    L = []
    L.append("# OmaCar vehicle profile.")
    L.append("#")
    L.append("# confidence:")
    for k in CONFIDENCE:
        first, *rest = CONFIDENCE_MEANS[k].split(". ")
        L.append(f"#   {k:<10} {first}.")
        for r in rest:
            if r.strip():
                L.append(f"#   {'':<10} {r.rstrip('.')}.")
    L.append("#")
    L.append("# An entry below `validated` must not drive a gauge.")
    L.append("")
    L.append(f"schema = {SCHEMA}")
    L.append("")
    L.append("[car]")
    for k in ("slug", "make", "model", "description", "protocol"):
        if car.get(k):
            L.append(f"{k} = {_q(car[k])}")
    if car.get("years"):
        L.append(f"years = {_arr(car['years'])}")
    if car.get("vin_prefix"):
        L.append(f"vin_prefix = {_q(car['vin_prefix'])}   "
                 f"# model/year only -- never a whole VIN")
    # AND EVERYTHING ELSE THE CAR SECTION HOLDS. The list above is what this
    # writer was taught; the shipped CR-Z profile also carries the engine, the
    # displacement, the redline, the kerb mass, the tank and the drivetrain,
    # and writing the profile back deleted all seven. A writer that keeps only
    # the fields it recognises is a writer that quietly narrows the format
    # every time anybody saves.
    for k in car:
        if k in ("slug", "make", "model", "description", "protocol", "years",
                 "vin_prefix"):
            continue
        L.append(f"{k} = {_toml_value(car[k])}")
    L.append("")
    L.append("[meta]")
    for k in ("created", "updated"):
        if meta.get(k):
            L.append(f"{k} = {_q(meta[k])}")
    if meta.get("contributors"):
        L.append("contributors = [" + ", ".join(_q(c) for c in meta["contributors"]) + "]")
    if meta.get("checksum"):
        L.append(f"checksum = {_q(meta['checksum'])}   # integrity, NOT a signature")
    for k in meta:
        if k in ("created", "updated", "contributors", "checksum"):
            continue
        L.append(f"{k} = {_toml_value(meta[k])}")

    for p in doc.get("pid") or []:
        L.append("")
        L.append("[[pid]]")
        for k in ("id", "name", "header", "request", "formula", "unit"):
            if p.get(k) is not None:
                L.append(f"{k} = {_q(p[k])}")
        if p.get("service") is not None:
            L.append(f"service = 0x{int(p['service']):02X}")
        if p.get("payload_len") is not None:
            L.append(f"payload_len = {int(p['payload_len'])}")
        if p.get("varying_bytes") is not None:
            L.append(f"varying_bytes = {_arr(p['varying_bytes'])}")
        L.append(f"confidence = {_q(p.get('confidence', 'candidate'))}")
        prov = p.get("provenance") or {}
        if prov:
            L.append("")
            L.append("  [pid.provenance]")
            for k in ("found_by", "found_on", "vin_prefix", "method",
                      "first_seen", "samples", "validated_by", "validated_on",
                      "validated_against", "refuted_by", "refuted_on", "note"):
                v = prov.get(k)
                if v is None or v == "":
                    continue
                L.append(f"  {k} = " + (str(v) if isinstance(v, int) else _q(v)))

    for b in doc.get("broadcast") or []:
        L.append("")
        L.append("[[broadcast]]")
        L.append("# Read from a frame the car already sends. Nothing here is "
                 "transmitted.")
        for k in ("id", "name", "can_id"):
            if b.get(k) not in (None, ""):
                L.append(f"{k} = {_q(b[k])}")
        if b.get("byte") is not None:
            L.append(f"byte = {int(b['byte'])}")
        if b.get("kind"):
            L.append(f"kind = {_q(b['kind'])}")
        for k in ("formula", "unit"):
            if b.get(k) not in (None, ""):
                L.append(f"{k} = {_q(b[k])}")
        L.append(f"confidence = {_q(b.get('confidence', 'candidate'))}")
        states = b.get("states") or {}
        if states:
            L.append("")
            L.append("  [broadcast.states]")
            for k in sorted(states):
                L.append(f"  {_q(str(k))} = {_q(str(states[k]))}")
        prov = b.get("provenance") or {}
        if prov:
            L.append("")
            L.append("  [broadcast.provenance]")
            for k in ("found_by", "found_on", "vin_prefix", "method",
                      "first_seen", "validated_by", "validated_on",
                      "validated_against", "refuted_by", "refuted_on", "note",
                      "url", "retrieved_at", "source_kind"):
                v = prov.get(k)
                if v is None or v == "":
                    continue
                L.append(f"  {k} = " + (str(v) if isinstance(v, int) else _q(v)))

    for a in doc.get("actuator") or []:
        L.append("")
        L.append("[[actuator]]")
        L.append("# Sent as 0x2F + did + state. Only `validated` reaches a button.")
        for k in ("test", "name", "header", "did", "on", "off", "session"):
            if a.get(k) not in (None, ""):
                L.append(f"{k} = {_q(a[k])}")
        L.append(f"confidence = {_q(a.get('confidence', 'candidate'))}")
        prov = a.get("provenance") or {}
        if prov:
            L.append("")
            L.append("  [actuator.provenance]")
            for k in ("found_by", "found_on", "vin_prefix", "method",
                      "first_seen", "validated_by", "validated_on",
                      "validated_against", "refuted_by", "refuted_on", "note",
                      "url", "retrieved_at", "source_kind"):
                v = prov.get(k)
                if v is None or v == "":
                    continue
                L.append(f"  {k} = " + (str(v) if isinstance(v, int) else _q(v)))

    L += _dump_unknown(doc)
    return "\n".join(L) + "\n"


def write(path, doc):
    # Normalise BEFORE hashing, and write the normalised form, so the file on
    # disk is exactly what was hashed.
    doc = normalize(doc)
    doc.setdefault("schema", SCHEMA)
    meta = doc.setdefault("meta", {})
    meta.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["checksum"] = checksum(doc)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(doc))
    os.replace(tmp, path)
    return path


def write_draft(path, car, findings, who=None, vin=None):
    """Draft a profile from prospector findings.

    Every entry lands as `candidate` or `observed` and never higher, whatever
    the sweep saw. A prospector can prove that an identifier answers and that
    its bytes move; it cannot know what they mean, and the format must not let
    a machine award itself the confidence level that only a person checking
    against a real gauge can grant.
    """
    stamp = time.strftime("%Y-%m-%d")
    pids = []
    for f in findings:
        varying = f.get("varying") or f.get("varying_bytes") or []
        pids.append({
            "id": f"{f['header']}_{f['request']}".lower(),
            "name": f.get("name") or "",
            "header": f["header"],
            "request": f["request"],
            "service": f.get("service"),
            "payload_len": f.get("payload_len"),
            "varying_bytes": varying,
            "formula": "",
            "unit": "",
            # `observed` only when bytes actually moved. Otherwise it answered
            # and that is all we know.
            "confidence": "observed" if varying else "candidate",
            "provenance": {
                "found_by": who or os.environ.get("USER") or "unknown",
                "found_on": car.get("description") or car.get("slug") or "",
                "vin_prefix": vin_prefix(vin) if vin else "",
                "method": f"omacar prospect, service 0x{(f.get('service') or 0):02X}",
                "first_seen": stamp,
                "samples": len(f.get("samples") or []),
            },
        })
    doc = {
        "schema": SCHEMA,
        "car": {k: v for k, v in car.items() if k != "discovered"},
        "meta": {"contributors": [who or os.environ.get("USER") or "unknown"]},
        "pid": pids,
    }
    return write(path, doc)
