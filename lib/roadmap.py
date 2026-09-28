"""omacar roadmap — regenerate the part of the roadmap that cannot lie.

THE FAILURE THIS EXISTS TO PREVENT.

Every roadmap rots the same way. Somebody writes a good one, ships against it
for a month, and then stops updating it. Six months later it still says "1,200
lines, one vehicle, forty tests" and not one of those numbers is true any more.
Nobody notices, because a document that is never regenerated is never checked.

The dangerous part is not that the numbers go stale. It is that the *argument*
around them stays good. A well-made strategy section lends its credibility to
whatever figures sit next to it, so a stale count does not read as out of date
— it reads as fact. That is how a roadmap stops being a plan and becomes a
piece of marketing about a project that no longer exists.

The fix is not discipline. Discipline is exactly what fails here. The fix is to
write down only what a person should write down — the argument, the priorities,
the things we refuse to do — and to DERIVE everything countable from sources
that change when the project changes: git, the test runner, the profile pool,
the discovery frontier, the data files. A number recomputed from the repository
cannot drift away from the repository. There is nowhere for it to drift to.

WHAT IS DERIVED.

Lines of code by area, from `git ls-files` — so a file nobody committed does
not count, which is the correct answer to "has it shipped?".

The test count, by running the suite. Not by reading a number out of a file:
the whole point is that it is measured, and it takes two seconds. The workshop
suite prints its checks under headings, so the same run also answers "which
features actually have tests" — a question a hand-written roadmap can never be
trusted on, because the entry that lacks tests is precisely the entry whose
author did not want to mention it.

Coverage, which is the roadmap's own first success metric: how many vehicle
profiles exist, how many entries each holds, and how many of those entries are
`validated` rather than merely `candidate`. Plus the reset and owner-procedure
definitions by confidence, which is how the honest limit "nothing is verified
yet" stops being a claim somebody has to remember to delete.

The discovery frontier: how much of the manufacturer identifier space has been
asked, per module, on this machine. Empty is a real and useful answer — it
means no automated sweep has run here, and the document should say so rather
than quoting a percentage from a sweep nobody can point at.

The in-flight list, checked against git. Each item names the file it will
create; if git is tracking that file, the item has landed and the block says so
without anybody editing anything. This is the single cheapest cure for the
"still planned" entry that shipped three weeks ago.

The capability map, doc/capabilities.json: everything an owner and an agent
would want to do with a car, each entry with a status and the files that prove
it. Two hundred lines of "what we can do" is exactly the kind of list that rots
in prose, so it lives in data, `--check` validates it, and the block counts it
by domain and status and lists what has shipped. The validation is the point.
An entry marked `shipped` must name files git is tracking, so "shipped" cannot
mean "somebody remembers writing it". A shipped entry may only depend on
shipped entries, so a feature cannot be done while the thing it stands on is
not. A capability that writes to or moves the car must name the gate in
lib/modes.py that decides it, and may not name one no mode allows: reprogramming
and guessing routine identifiers are refusals, not roadmap items.

WHAT IS NOT DERIVED, AND WHY THAT IS DELIBERATE.

The strategy, the phases, the principles, and the honesty sections are written
by a person and are never touched by this command. They are arguments, not
measurements, and generating them would be the same category error as writing a
gauge that displays a candidate PID.

The field log and the claims list live in doc/roadmap.json because nothing in
the repository knows that somebody drove three legs of California with the
logger running, or that a price comparison was indicative rather than quoted.
Those are facts about the world, and the only honest source for them is a human
appending a dated line. A journal does not rot the way a wish list does: when
somebody stops keeping it, it stops — it does not start lying.

Note what roadmap.json deliberately CANNOT hold: a command to run. A claim
carries a human-readable `how` describing the way to re-check it, and this
module never executes it. A JSON file in a public repository that a maintenance
command shells out to is a remote code execution waiting for the first person
who pulls a branch, and there is no version of a documentation generator that
is worth that.

WHY A DELIMITED BLOCK AND NOT A GENERATED FILE.

The obvious design is to generate doc/ROADMAP.md wholesale from data. That
costs the thing worth most in this document: prose that argues a position.
Templating it produces bullet points, and bullet points are what a roadmap
degenerates into anyway. So generation writes between two HTML comments and
touches nothing else, and the hand-written half is safe from it by
construction.

WHY IT DOES NOT STAMP A FRESH DATE ON EVERY RUN.

A generator that rewrites a timestamp every time it runs makes the file dirty
in git every time it runs, and a file that is always dirty is a file whose
diff nobody reads. So the body is rendered first and compared against what is
already there; if nothing measurable changed, the file is left exactly alone
and the old date stands. The date therefore means "when these numbers last
changed", which is more useful than "when somebody last ran the command", and
`omacar roadmap` is safe to run in a loop, a hook, or twice by accident.

Two things used to break that promise, and both were the block describing
itself. A claim printed its age in days, so the body changed every midnight
and `--check` failed on a tree nobody had touched; it now prints the date it
was asserted and flags it only when it is actually due. And the shipped count
included the commit that regenerated this file, so committing a fresh block
made it stale at once; commits that touch nothing but doc/ROADMAP.md are now
left out of that count, and the block says so.
"""

import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "doc", "ROADMAP.md")
DATA = os.path.join(ROOT, "doc", "roadmap.json")
CAPS = os.path.join(ROOT, "doc", "capabilities.json")
DOC_REL = "doc/ROADMAP.md"

BEGIN_RE = re.compile(r"^<!--\s*omacar:status begin\b.*$", re.M)
END_RE = re.compile(r"^<!--\s*omacar:status end\b.*$", re.M)

BEGIN = ("<!-- omacar:status begin — generated by `omacar roadmap`. "
         "Everything between these two comments is rewritten; edit the prose "
         "outside them, and the facts in doc/roadmap.json. -->")
END = "<!-- omacar:status end -->"

DATE_PREFIX = "*Numbers last changed"

# The ISO 14229 manufacturer-specific identifier space, quoted from the sweeper
# rather than retyped, so the denominator of every percentage below is the same
# number the sweeper actually uses.
DID_SPACE = (0x0000, 0xA5FF)


# ---- shelling out -----------------------------------------------------------

def git(*args):
    """Run git in the repo, or return '' if this is not a checkout.

    Returning empty rather than raising matters: the roadmap should still
    regenerate inside a release tarball, from which git metadata is stripped.
    Losing the shipped-history section there is a small, obvious gap; refusing
    to run at all would be a broken command in the only place a newcomer is
    likely to try it first.
    """
    try:
        out = subprocess.run(("git",) + args, cwd=ROOT, check=True,
                             capture_output=True, text=True)
        return out.stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def tracked(paths=()):
    out = git("ls-files", "-z", *paths)
    return [p for p in out.split("\0") if p]


def in_checkout():
    """Is there a working git here at all?

    tracked() answers [] both for "nothing is tracked" and for "there is no
    git", and those must not be confused by anything that FAILS on an
    untracked file: a tarball would report every shipped capability as
    unproven, and a check that cries wolf in the one place a newcomer runs it
    first is a check people learn to skip.
    """
    return git("rev-parse", "--is-inside-work-tree").strip() == "true"


def line_count(paths):
    n = 0
    for rel in paths:
        p = os.path.join(ROOT, rel)
        try:
            with open(p, "rb") as f:
                n += f.read().count(b"\n")
        except OSError:
            pass                      # tracked but deleted; it counts as gone
    return n


# ---- the measurements -------------------------------------------------------

# Label, the paths git is asked about, and an optional extension filter.
#
# The filter is here because lib/ holds two shell files alongside forty Python
# ones. Without it the first area to claim a directory claims everything in it,
# and the table cheerfully reported two shell scripts as Python -- a small lie,
# but exactly the kind this whole command exists to stop, and one nobody would
# ever have caught by reading the output.
AREAS = [
    ("Python — the whole diagnostic side", ["lib"], (".py",)),
    ("JavaScript — the app", ["share/js"], (".js",)),
    ("CSS", ["share/css"], (".css",)),
    ("QML — the Quickshell plugin", ["plugin"], None),
    ("Shell — the CLI and the installer",
     ["bin", "install.sh", "uninstall.sh", "lib/env.sh", "lib/omarchy-app.sh"],
     None),
    ("Tests", ["test"], None),
    ("Documentation", ["doc", "README.md"], None),
    ("Data — codes, resets, procedures, profiles",
     ["share/data", "profiles"], None),
]


def code_size():
    """Lines by area, counted from what git is tracking.

    Counting the working tree instead would be easier and wrong: it would
    include whatever is half-written this afternoon, and the roadmap's job is
    to describe what exists, not what somebody is in the middle of. An
    uncommitted file has not shipped.
    """
    rows, seen, total = [], set(), 0
    for label, paths, exts in AREAS:
        files = [f for f in tracked(paths)
                 if f not in seen and (not exts or f.endswith(exts))]
        seen.update(files)
        n = line_count(files)
        total += n
        if files:
            rows.append((label, len(files), n))
    rest = [f for f in tracked() if f not in seen]
    return rows, total, line_count(rest) + total


HEADING_RE = re.compile(r"^  (\S.*?)\s*$")
OK_RE = re.compile(r"^\s{2,}ok\b")
SUMMARY_RE = re.compile(r"^\s*(all good|\d+ passed|\(skipping)")

# A VIN-shaped token: seventeen characters from the VIN alphabet, which
# excludes I, O and Q precisely so they cannot be confused with 1 and 0.
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def plural(n, one, many=None):
    return f"{fmt(n)} {one if n == 1 else (many or one + 's')}"


def redact(text):
    """Strip anything VIN-shaped out of text on its way into a published file.

    The workshop suite names some of its groups after the fixture vehicle it
    built, VIN and all. Those are invented cars, but this document is pushed to
    a public repository and the rule that keeps a VIN out of shared data has to
    hold at every exit, not only at the ones where the VIN is real. The moment
    it is conditional on somebody correctly deciding which VIN is a fixture, it
    is a rule that will eventually be got wrong.
    """
    return VIN_RE.sub("<VIN>", text)


def run_tests():
    """Run the whole suite and parse its own output back.

    Parsing the runner's printed output rather than importing it is the point.
    The runner is what a person runs; if it stops working, or a suite silently
    stops being invoked, this notices for the same reason a person would --
    the checks are not there any more. A count imported from a module would
    keep reporting a healthy number for a suite that no longer executes.

    Headings have their parenthetical stripped because the smoke test prints
    the scratch HOME it built, which is a fresh temp path on every run. Left in,
    it would make the generated block differ from itself every single time and
    the whole no-op-when-nothing-changed design would collapse.
    """
    sh = os.path.join(ROOT, "test", "all.sh")
    if not os.path.exists(sh):
        return None
    try:
        out = subprocess.run(["bash", sh], cwd=ROOT, capture_output=True,
                             text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    groups, cur = [], None
    for line in out.stdout.splitlines():
        if OK_RE.match(line):
            if cur:
                cur[1] += 1
            continue
        if SUMMARY_RE.match(line):
            continue
        m = HEADING_RE.match(line)
        if m:
            name = re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip()
            cur = [redact(name), 0]
            groups.append(cur)
    # Redaction can make two headings identical -- two fixture cars become two
    # rows called the same thing -- so fold them together rather than printing
    # a table with a repeated row and two different numbers in it.
    merged, seen = [], {}
    for name, n in groups:
        if not n:
            continue
        if name in seen:
            seen[name][1] += n
        else:
            seen[name] = [name, n]
            merged.append(seen[name])
    groups = merged
    failed = sum(int(m.group(1)) for m in
                 re.finditer(r"(\d+) failed", out.stdout))
    return {"groups": groups, "checks": sum(g[1] for g in groups),
            "failed": failed, "ok": out.returncode == 0}


def profiles():
    """Every vehicle profile in the pool, with its confidence breakdown.

    Parsed with a small regex rather than tomllib because this module has to
    run under the system interpreter, which on an older machine is not
    guaranteed to have it, and because the only things wanted here are the slug
    and a tally of one repeated key. Reaching for lib/profile.py would drag in
    the whole normalisation and checksum path to count four strings.
    """
    out = []
    d = os.path.join(ROOT, "profiles")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".toml") or name.endswith(".draft.toml"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        body = re.sub(r"^\s*#.*$", "", text, flags=re.M)
        tally = {}
        for c in re.findall(r'^\s*confidence\s*=\s*"([a-z]+)"', body, re.M):
            tally[c] = tally.get(c, 0) + 1
        out.append({"slug": name[:-5],
                    "entries": body.count("[[pid]]"),
                    "confidence": tally})
    return out


def definitions():
    """Reset and owner-procedure definitions, tallied by confidence.

    This is the honest limit "nothing is verified yet" turned into something
    that stops being said the moment it stops being true. Written by hand, that
    line survives long past the first verified reset, because the person who
    verifies it is thinking about a parking brake, not about a sentence in a
    document three directories away.
    """
    out = {}
    for name, key in (("resets", "resets"), ("procedures", "procedures")):
        try:
            with open(os.path.join(ROOT, "share", "data", name + ".json"),
                      encoding="utf-8") as f:
                items = json.load(f).get(key) or []
        except (OSError, ValueError):
            continue
        tally = {}
        for it in items:
            c = (it or {}).get("confidence") or "unlabelled"
            tally[c] = tally.get(c, 0) + 1
        out[name] = {"count": len(items), "confidence": tally}
    return out


def state_dir():
    return os.path.expanduser(
        os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")


def frontier():
    """How much of the identifier space has been asked, on this machine.

    Deliberately reports counts and module addresses and never a vehicle key,
    because the frontier files are named by VIN and this document is published.
    The repo already truncates a VIN to eight characters before it reaches a
    shared profile; the same rule has to hold here, and the cheapest way to
    hold it is to never read the name.
    """
    d = os.path.join(state_dir(), "frontier")
    files = sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []
    lo, hi = DID_SPACE
    span = hi - lo + 1
    rows = []
    for name in files:
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        for k, svc in sorted((doc.get("services") or {}).items()):
            asked = 0
            for s in svc.get("swept") or []:
                a, b = max(s[0], lo), min(s[1], hi)
                if b >= a:
                    asked += b - a + 1
            rows.append({"key": k, "asked": asked,
                         "pct": 100.0 * asked / span,
                         "found": len(svc.get("found") or [])})
    return {"vehicles": len(files), "rows": rows, "span": span}


def local_records():
    """Counts of what this machine has actually captured. Counts only.

    Not distances, not dates of travel, not a VIN. The field log below says in
    the owner's own words that he drove three legs of California; that is his
    sentence to write. A generator quietly harvesting a trip database into a
    published file is a different thing entirely, and the rule that keeps the
    two apart is that this function may count files and may not open them.
    """
    s = state_dir()

    def count(sub, suffix=""):
        p = os.path.join(s, sub)
        try:
            return sum(1 for f in os.listdir(p) if f.endswith(suffix))
        except OSError:
            return 0

    return {
        "vehicles": count("vehicles", ".db"),
        "drive_logs": count("dtclog", ".jsonl"),
        "candidate_logs": count("candidates"),
        "learned": sum(1 for f in os.listdir(s)
                       if f.endswith(".learned.json")) if os.path.isdir(s) else 0,
    }


# Commits that change nothing but this document are not work that shipped, and
# counting them made the block stale the moment it was committed: the commit
# that records "N commits" is commit N+1. Leaving them out makes a regenerated
# block a fixed point, so `--check` can pass on the commit that carries it.
NOT_THIS_FILE = ("--", ".", f":(exclude){DOC_REL}")


def shipped(n=8):
    count = (git("rev-list", "--count", "HEAD", *NOT_THIS_FILE) or "").strip()
    log = git("log", "-n", str(n), "--date=short", "--format=%ad\t%s",
              "HEAD", *NOT_THIS_FILE)
    rows = [l.split("\t", 1) for l in log.splitlines() if "\t" in l]
    branch = (git("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    return {"count": count, "rows": rows, "branch": branch}


def load_data():
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def in_flight(cfg):
    """In-flight items, marked landed or not by asking git, not by asking us.

    An item declares the file it will create when it lands. The moment that
    file is committed, this block says so on the next run and nobody has to
    remember to move a line. Items whose work lands inside an existing file
    cannot be checked this way and say so plainly -- a wrong automatic answer
    would be far worse than an honest 'confirm by hand', because the whole
    value of a derived status is that you can stop reading it sceptically.
    """
    live = set(tracked())
    out = []
    for it in cfg.get("inflight") or []:
        ev = it.get("evidence") or []
        landed = bool(ev) and all(p in live for p in ev)
        out.append({"title": it.get("title", "?"), "note": it.get("note", ""),
                    "evidence": ev, "branch": it.get("branch", ""),
                    "state": "landed" if landed
                    else ("in flight" if ev else "in flight — no new file to check")})
    return out


def claims(cfg, now=None):
    now = now or time.time()
    out = []
    for c in cfg.get("claims") or []:
        asserted = c.get("asserted") or ""
        age = None
        try:
            t = time.mktime(time.strptime(asserted, "%Y-%m-%d"))
            age = int((now - t) // 86400)
        except (ValueError, OverflowError):
            pass
        every = int(c.get("recheck_days") or 180)
        out.append({"text": c.get("text", ""), "how": c.get("how", ""),
                    "asserted": asserted, "age": age,
                    "stale": age is not None and age > every,
                    "every": every,
                    "sources": list(c.get("sources") or []),
                    "verified": c.get("verified")})
    return out


def check_claims(cfg):
    """What is wrong with the claims list, as sentences. Empty when nothing is.

    A claim is the one place prose may state a number the repository cannot
    derive -- a price, a count on somebody else's website -- so the least it
    owes the reader is a real date, a way to re-check it, and, where it came
    from outside, the address it came from. `verified` false is allowed and
    is rendered as such: an unverified claim marked unverified is honest; one
    that carries no marking either way is not.
    """
    out = []
    for i, c in enumerate(cfg.get("claims") or []):
        where = f"claim {i + 1}"
        if not isinstance(c, dict):
            out.append(f"{where}: not an object")
            continue
        if not (c.get("text") or "").strip():
            out.append(f"{where}: no text")
        try:
            time.strptime(c.get("asserted") or "", "%Y-%m-%d")
        except ValueError:
            out.append(f"{where}: `asserted` is not a YYYY-MM-DD date")
        if not (c.get("how") or "").strip():
            out.append(f"{where}: no `how` -- say how to check it again")
        every = c.get("recheck_days", 180)
        if not isinstance(every, int) or every <= 0:
            out.append(f"{where}: `recheck_days` must be a positive whole number")
        src = c.get("sources", [])
        if not isinstance(src, list) or not all(
                isinstance(s, str) and s.startswith("https://") for s in src):
            out.append(f"{where}: `sources` must be a list of https:// addresses")
        if "verified" in c and not isinstance(c["verified"], bool):
            out.append(f"{where}: `verified` must be true or false")
        if src and "verified" not in c:
            out.append(f"{where}: cites a source but does not say whether the "
                       "number was seen there (`verified`)")
    return out


# ---- the capability map ------------------------------------------------------
#
# doc/capabilities.json. Its _about explains the fields; this is the part that
# refuses a bad entry. Every rule below exists because the failure it stops is
# one a hand-kept list makes silently.

CAP_STATUS = ("shipped", "building", "next", "later", "research")
CAP_TIER = ("none", "read", "write", "actuate")
CAP_FIELDS = ("id", "domain", "title", "user", "agent", "status", "tier",
              "needs", "depends", "evidence", "notes")
CAP_OPTIONAL = ("gate",)
CAP_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def load_capabilities(path=None):
    """The map, or None and the reason it could not be read."""
    try:
        with open(path or CAPS, encoding="utf-8") as f:
            return json.load(f), ""
    except OSError as exc:
        return None, f"cannot read {os.path.relpath(path or CAPS, ROOT)}: {exc.strerror}"
    except ValueError as exc:
        return None, f"{os.path.relpath(path or CAPS, ROOT)} is not valid JSON: {exc}"


def mode_actions():
    """lib/modes.py's ACTIONS, the one place a gate's name means something.

    Imported rather than copied: a second list of gate names here would be the
    same drift this whole module exists to prevent, and modes.py is stdlib
    only, so importing it costs nothing under the system interpreter.
    """
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import modes                                              # noqa: E402
    return modes.ACTIONS


def check_capabilities(doc, live=None, actions=None):
    """Every problem with the map, as a sentence naming the entry. [] is clean.

    `live` is the set of tracked paths, or None when there is no git to ask;
    `actions` is modes.ACTIONS. Both are parameters so the tests can hand in a
    world without shelling out.
    """
    if actions is None:
        actions = mode_actions()
    out = []
    if not isinstance(doc, dict):
        return ["the map is not a JSON object"]
    if doc.get("schema") != 1:
        out.append("`schema` must be 1")

    domains = doc.get("domains")
    known_domains = []
    if not isinstance(domains, list) or not domains:
        out.append("`domains` must be a non-empty list")
        domains = []
    for d in domains:
        did = d.get("id") if isinstance(d, dict) else None
        if not isinstance(did, str) or not CAP_ID_RE.match(did):
            out.append(f"domain {did!r}: needs a lowercase id")
            continue
        if did in known_domains:
            out.append(f"domain {did}: listed twice")
        if not (d.get("title") or "").strip():
            out.append(f"domain {did}: no title")
        known_domains.append(did)

    hardware = doc.get("hardware")
    if not isinstance(hardware, dict):
        out.append("`hardware` must be an object of id -> {title, notes}")
        hardware = {}
    for hid, h in hardware.items():
        if not CAP_ID_RE.match(hid):
            out.append(f"hardware {hid!r}: needs a lowercase id")
        if not isinstance(h, dict) or not (h.get("title") or "").strip():
            out.append(f"hardware {hid}: no title")

    caps = doc.get("capabilities")
    if not isinstance(caps, list) or not caps:
        return out + ["`capabilities` must be a non-empty list"]

    if live is None:
        out.append("not a git checkout, so no evidence can be checked -- "
                   "run this from a clone")
    by_id = {}
    for i, c in enumerate(caps):
        if not isinstance(c, dict):
            out.append(f"capability {i + 1}: not an object")
            continue
        cid = c.get("id")
        name = cid if isinstance(cid, str) and cid else f"capability {i + 1}"
        if not isinstance(cid, str) or not CAP_ID_RE.match(cid):
            out.append(f"{name}: `id` must be lowercase words joined by . or -")
        elif cid in by_id:
            out.append(f"{name}: id used twice")
        else:
            by_id[cid] = c

        missing = [k for k in CAP_FIELDS if k not in c]
        if missing:
            out.append(f"{name}: missing {', '.join(missing)}")
        extra = [k for k in c if k not in CAP_FIELDS + CAP_OPTIONAL]
        if extra:
            out.append(f"{name}: unknown field {', '.join(extra)}")

        for k in ("title", "user", "agent"):
            if k in c and not (isinstance(c[k], str) and c[k].strip()):
                out.append(f"{name}: `{k}` must say something")
        if "notes" in c and not isinstance(c["notes"], str):
            out.append(f"{name}: `notes` must be a string (empty is fine)")

        if "domain" in c and c["domain"] not in known_domains:
            out.append(f"{name}: unknown domain {c['domain']!r}")
        status = c.get("status")
        if "status" in c and status not in CAP_STATUS:
            out.append(f"{name}: status {status!r} is not one of "
                       f"{', '.join(CAP_STATUS)}")
        tier = c.get("tier")
        if "tier" in c and tier not in CAP_TIER:
            out.append(f"{name}: tier {tier!r} is not one of {', '.join(CAP_TIER)}")

        for k in ("needs", "depends", "evidence"):
            if k in c and not (isinstance(c[k], list)
                               and all(isinstance(x, str) and x for x in c[k])):
                out.append(f"{name}: `{k}` must be a list of strings")
        for h in c.get("needs") or []:
            if isinstance(h, str) and h not in hardware:
                out.append(f"{name}: needs {h!r}, which `hardware` does not define")

        # The gate. A write or an actuation that cannot say which rule in
        # lib/modes.py decides it is a capability nobody has thought through;
        # one gated by an action no mode allows is not a capability at all.
        gate = c.get("gate")
        if tier in ("write", "actuate") and not gate:
            out.append(f"{name}: a {tier} capability must name its `gate`, "
                       "the lib/modes.py action that decides it")
        if gate is not None:
            spec = actions.get(gate) if isinstance(gate, str) else None
            if tier == "none":
                out.append(f"{name}: sends nothing to the car, so it has no gate")
            elif spec is None:
                out.append(f"{name}: gate {gate!r} is not an action in "
                           "lib/modes.py")
            elif spec.get("tier") is None:
                out.append(f"{name}: gate {gate!r} is refused in every mode; "
                           "that is a refusal, not a capability")

        # Evidence. Shipped and building must point at something real, and
        # everything named must be tracked -- an untracked file has not shipped
        # and a mistyped one is the invented citation this project refuses.
        ev = c.get("evidence") if isinstance(c.get("evidence"), list) else []
        if status in ("shipped", "building") and not ev:
            out.append(f"{name}: {status} with no evidence -- name the files "
                       "that implement it")
        for p in ev:
            if not isinstance(p, str):
                continue
            if p.startswith("/") or ".." in p.split("/"):
                out.append(f"{name}: evidence {p!r} must be a path inside the "
                           "repository")
            elif live is not None and p not in live:
                out.append(f"{name}: evidence {p} is not tracked by git")

    # Dependencies, once every id is known.
    for cid, c in by_id.items():
        deps = c.get("depends") if isinstance(c.get("depends"), list) else []
        for d in deps:
            if d == cid:
                out.append(f"{cid}: depends on itself")
            elif d not in by_id:
                out.append(f"{cid}: depends on {d!r}, which is not in the map")
            elif c.get("status") == "shipped" and \
                    by_id[d].get("status") != "shipped":
                out.append(f"{cid}: shipped, but depends on {d}, which is "
                           f"{by_id[d].get('status')}")

    # A cycle means neither can come first, which is a plan nobody can follow.
    state = {}

    def visit(n, path):
        if state.get(n) == 1:
            loop = path[path.index(n):] + [n]
            out.append("dependency loop: " + " -> ".join(loop))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in by_id[n].get("depends") or []:
            if d in by_id and d != n:
                visit(d, path + [n])
        state[n] = 2

    for cid in by_id:
        visit(cid, [])

    used = {c.get("domain") for c in by_id.values()}
    for d in known_domains:
        if d not in used:
            out.append(f"domain {d}: has no capabilities")
    return out


def capability_summary(doc):
    """Counts by domain and status, and the shipped entries, in map order."""
    titles = {d["id"]: d.get("title", d["id"]) for d in doc.get("domains") or []
              if isinstance(d, dict) and "id" in d}
    rows = {d: {s: 0 for s in CAP_STATUS} for d in titles}
    shipped_by = {d: [] for d in titles}
    for c in doc.get("capabilities") or []:
        d, s = c.get("domain"), c.get("status")
        if d in rows and s in CAP_STATUS:
            rows[d][s] += 1
            if s == "shipped":
                shipped_by[d].append(c)
    totals = {s: sum(r[s] for r in rows.values()) for s in CAP_STATUS}
    return {"titles": titles, "rows": rows, "totals": totals,
            "shipped": shipped_by,
            "count": sum(totals.values()), "domains": len(titles)}


def listing(doc, status, domain=None):
    """The entries at one status, with what each stands on. For people and agents.

    This is the answer to "what should I pick up next": an entry, what it
    needs to be true first and whether those things are, and what hardware it
    wants. Printed, not rendered into the document, because it is a question
    somebody asks at a terminal rather than a fact the roadmap states.
    """
    by_id = {c.get("id"): c for c in doc.get("capabilities") or []}
    lines = []
    for c in doc.get("capabilities") or []:
        if c.get("status") != status or (domain and c.get("domain") != domain):
            continue
        lines.append(f"  {c['id']}  [{c.get('domain')}, {c.get('tier')}]  "
                     f"{c.get('title')}")
        deps = [f"{d} ({(by_id.get(d) or {}).get('status', '?')})"
                for d in c.get("depends") or []]
        if deps:
            lines.append(f"      depends: {', '.join(deps)}")
        if c.get("needs"):
            lines.append(f"      needs:   {', '.join(c['needs'])}")
        if c.get("gate"):
            lines.append(f"      gate:    {c['gate']}")
        if c.get("notes"):
            lines.append(f"      notes:   {c['notes']}")
    return lines


# ---- rendering --------------------------------------------------------------

def fmt(n):
    return f"{n:,}"


def render_capabilities(add, doc=None):
    """The map, counted. Written through `add` so the tests can render it alone.

    The table is domain by status because that is the question a reader
    actually has -- "how much of the car does this cover, and how much is
    real" -- and a single percentage would hide exactly the domains that are
    all plans. The shipped list follows so every count in the first column
    can be traced to an entry and the file it names.
    """
    if doc is None:
        doc, why = load_capabilities()
        if doc is None:
            add(f"**The capability map** — {why}.")
            add("")
            return
    s = capability_summary(doc)
    add(f"**The capability map, counted** — `doc/capabilities.json`: "
        f"{plural(s['count'], 'capability', 'capabilities')} across "
        f"{plural(s['domains'], 'domain')}.")
    add("")
    add("| Domain | Shipped | Building | Next | Later | Research | All |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for d, title in s["titles"].items():
        r = s["rows"][d]
        add(f"| {title} | " + " | ".join(str(r[k]) for k in CAP_STATUS)
            + f" | {sum(r.values())} |")
    t = s["totals"]
    add("| **All domains** | " + " | ".join(f"**{t[k]}**" for k in CAP_STATUS)
        + f" | **{s['count']}** |")
    add("")
    add("*Shipped* means `omacar roadmap --check` found every file the entry")
    add("names tracked by git, and every entry it stands on shipped too. It does")
    add("not mean it works on every car: each entry's notes say which cars and")
    add("adapters it has actually met.")
    add("")
    add("**What has shipped, by domain** — each with the first file that "
        "proves it:")
    add("")
    for d, title in s["titles"].items():
        items = s["shipped"][d]
        if not items:
            add(f"- *{title}* — nothing yet.")
            continue
        add(f"- *{title}* — " + " · ".join(
            f"{c['title']} (`{(c.get('evidence') or ['?'])[0]}`)" for c in items)
            + ".")
    add("")


def render(cfg):
    L = []
    add = L.append

    add("### The numbers, generated")
    add("")
    add("Everything in this section is recomputed by `omacar roadmap` from git,")
    add("the test runner, the profile pool and the discovery frontier. None of it")
    add("is typed by hand, which is the only reason it can be trusted six months")
    add("from now. If a figure here disagrees with the prose above, the prose is")
    add("the one that is wrong.")
    add("")

    # --- code
    rows, counted, total = code_size()
    add("**How much there is**")
    add("")
    add("| | Files | Lines |")
    add("|---|---:|---:|")
    for label, files, n in rows:
        add(f"| {label} | {files} | {fmt(n)} |")
    add(f"| **Tracked in git, all of it** | | **{fmt(total)}** |")
    add("")

    # --- tests
    t = run_tests()
    if t is None:
        add("**Tests** — the suite could not be run from here, so no count is")
        add("recorded. A number is not carried over from a previous run: a stale")
        add("test count is exactly the kind of comfortable lie this section exists")
        add("to prevent.")
        add("")
    else:
        verdict = "all passing" if t["ok"] and not t["failed"] else \
            f"**{t['failed']} failing**"
        add(f"**Tests** — {plural(t['checks'], 'check')}, {verdict}. Run with "
            "`test/all.sh`; none of them needs a car.")
        add("")
        add("What is actually covered, straight out of the runner's own headings:")
        add("")
        add("| Area | Checks |")
        add("|---|---:|")
        for name, n in t["groups"]:
            add(f"| {name} | {n} |")
        add("")

    # --- coverage
    add("**Coverage — the first success metric on this page, counted**")
    add("")
    ps = profiles()
    if not ps:
        add("No vehicle profiles in the pool.")
    else:
        add("| Profile | Entries | Validated | Observed | Candidate |")
        add("|---|---:|---:|---:|---:|")
        for p in ps:
            c = p["confidence"]
            add(f"| `{p['slug']}` | {p['entries']} | {c.get('validated', 0)} "
                f"| {c.get('observed', 0)} | {c.get('candidate', 0)} |")
        add("")
        add("*A profile entry below `validated` may not drive a gauge. That rule")
        add("is enforced in code, not by convention, which is why the validated")
        add("column is the only one that means anything yet.*")
    add("")

    d = definitions()
    if d:
        bits = []
        for name, info in sorted(d.items()):
            tally = ", ".join(f"{n} {k}" for k, n in
                              sorted(info["confidence"].items()))
            bits.append(f"**{name}**: {info['count']} defined ({tally})")
        add("Workshop definitions — " + "; ".join(bits) + ".")
        add("")
        add("`verified` means somebody ran it on a real car and it did what it")
        add("said. Until a definition carries that word it is `reported`, and the")
        add("app shows it as reported.")
        add("")

    f = frontier()
    add("**Identifier space explored** — the manufacturer range is "
        f"`0x{DID_SPACE[0]:04X}`–`0x{DID_SPACE[1]:04X}`, {fmt(f['span'])} "
        "identifiers per module per service.")
    add("")
    if not f["rows"]:
        add("No frontier record on the machine that generated this. That is not")
        add("the same as nothing having been swept — early sweeps on the CR-Z ran")
        add("before the resumable frontier existed and left no record it can read.")
        add("It does mean the tool cannot currently *prove* a coverage percentage")
        add("to you, and so it declines to quote one.")
    else:
        add("| Module / service | Asked | Of the space | Responders |")
        add("|---|---:|---:|---:|")
        for r in f["rows"]:
            add(f"| `{r['key']}` | {fmt(r['asked'])} | {r['pct']:.1f}% "
                f"| {r['found']} |")
    add("")

    lr = local_records()
    add("On this machine: "
        + plural(lr["vehicles"], "vehicle database") + ", "
        + plural(lr["drive_logs"], "drive fault-log session") + ", "
        + plural(lr["candidate_logs"], "candidate correlation log") + ", "
        + plural(lr["learned"], "learned module map")
        + ". Counts only — the field log below is the place for what those "
          "drives actually were.")
    add("")

    # --- the capability map
    render_capabilities(add)

    # --- shipped
    s = shipped()
    if s["count"]:
        add(f"**Shipped** — {s['count']} commits on `{s['branch']}`, not "
            "counting the ones that only regenerate this file. The most "
            "recent, unedited:")
        add("")
        for date, subject in s["rows"]:
            add(f"- `{date}` {subject}")
        add("")

    # --- in flight
    fl = in_flight(cfg)
    if fl:
        add("**In flight** — being built right now, and not to be counted as")
        add("shipped. Each names the file that proves it landed; git answers,")
        add("not us.")
        add("")
        for it in fl:
            ev = (" — " + ", ".join(f"`{p}`" for p in it["evidence"])) \
                if it["evidence"] else ""
            br = f" (branch `{it['branch']}`)" if it.get("branch") else ""
            note = f" {it['note']}" if it.get("note") else ""
            add(f"- **{it['title']}**{br} — {it['state']}{ev}.{note}")
        add("")

    # --- claims
    render_claims(add, cfg)

    # --- field log
    log = cfg.get("field") or []
    if log:
        add("**Field log** — the car, and the world, not the repository")
        add("")
        add("Nothing in a checkout knows a car was driven or a demo was given, so")
        add("this half is hand-appended to `doc/roadmap.json` and rendered here. A")
        add("journal is the one form of hand-maintained document that does not rot:")
        add("when somebody stops keeping it, it stops. It does not start lying.")
        add("")
        for e in log:
            add(f"- **{e.get('date', '?')}** — {redact(e.get('what', ''))}")
        add("")

    while L and not L[-1].strip():
        L.pop()
    return "\n".join(L)


def render_claims(add, cfg, now=None):
    """The claims, with their sources and dates and never their age.

    The age in days used to be printed here, which made the block different
    every midnight. The date says the same thing and does not move; the flag
    says when it matters.
    """
    cl = claims(cfg, now)
    if cl:
        add("**Assertions with nothing in the repository to check them against**")
        add("")
        add("Every claim below is made by a person and cannot be derived. Each")
        add("carries the date it was last checked, where it came from, and how")
        add("to check it again. The point is not the date — it is that an")
        add("unverifiable claim is visibly marked as one instead of sitting in")
        add("the prose looking like a measurement.")
        add("")
        for c in cl:
            flag = " **— due a re-check**" if c["stale"] else ""
            mark = " **(unverified)**" if c.get("verified") is False else ""
            add(f"- {c['text']}{mark}")
            if c["sources"]:
                add("  <br>Sources: " + ", ".join(f"<{u}>" for u in c["sources"]))
            add(f"  <br>*Asserted {c['asserted']}{flag}. To re-check: "
                f"{redact(c['how'])}*")
        add("")


def build_block(cfg, previous_date=None):
    body = render(cfg)
    return body


def splice(text, body, today):
    """Put the body between the markers, keeping the old date if unchanged."""
    b, e = BEGIN_RE.search(text), END_RE.search(text)
    if not b or not e or e.start() < b.end():
        return None, False
    old = text[b.end():e.start()]
    old_body = "\n".join(l for l in old.splitlines()
                         if not l.startswith(DATE_PREFIX)).strip()
    changed = old_body != body.strip()
    date = today
    if not changed:
        for l in old.splitlines():
            if l.startswith(DATE_PREFIX):
                m = re.search(r"(\d{4}-\d{2}-\d{2})", l)
                if m:
                    date = m.group(1)
    stamp = (f"{DATE_PREFIX}: {date}. Re-run `omacar roadmap` any time — it "
             "rewrites this file only when a number actually moved.*")
    block = f"{BEGIN}\n\n{stamp}\n\n{body}\n\n{END}"
    return text[:b.start()] + block + text[e.end():], changed


def problems(cfg):
    """Everything --check refuses, before a single number is measured."""
    out = check_claims(cfg)
    doc, why = load_capabilities()
    if doc is None:
        return out + [why]
    live = set(tracked()) if in_checkout() else None
    return out + check_capabilities(doc, live)


USAGE = """  usage: omacar roadmap [--check | --print | --list STATUS [DOMAIN]]

    (nothing)        rewrite the generated block of doc/ROADMAP.md, if a number moved
    --check          validate doc/capabilities.json and doc/roadmap.json, and fail
                     if the block is out of date
    --print          print the block instead of writing it
    --list STATUS    the capabilities at one status (shipped, building, next,
                     later, research), with what each depends on and needs"""


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        return 0

    if "--list" in argv:
        rest = argv[argv.index("--list") + 1:]
        if not rest or rest[0] not in CAP_STATUS:
            print(USAGE)
            return 1
        doc, why = load_capabilities()
        if doc is None:
            print(f"  {why}")
            return 1
        lines = listing(doc, rest[0], rest[1] if len(rest) > 1 else None)
        print("\n".join(lines) if lines else f"  nothing is {rest[0]}")
        return 0

    cfg = load_data()

    # The map and the claims are checked BEFORE anything is measured, and a
    # bad one stops the write: rendering counts out of a map that does not
    # validate would publish numbers about entries that are not true, which
    # is the failure this file exists to prevent.
    bad = problems(cfg)
    if bad and "--print" not in argv:
        print(f"  {plural(len(bad), 'problem')} in doc/capabilities.json or "
              "doc/roadmap.json:")
        for p in bad:
            print(f"    - {p}")
        if "--check" not in argv:
            print("  nothing written; fix these and run it again")
            return 1

    body = build_block(cfg)
    today = time.strftime("%Y-%m-%d")

    if "--print" in argv:
        print(body)
        return 0

    try:
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print(f"  cannot read {DOC}: {exc}")
        return 1

    out, changed = splice(text, body, today)
    if out is None:
        print("  doc/ROADMAP.md has no generated block. Add these two lines "
              "where the status belongs, and run this again:")
        print(f"\n{BEGIN}\n{END}\n")
        return 1

    if "--check" in argv:
        print("  roadmap is out of date — run `omacar roadmap`" if changed
              else "  roadmap is current")
        if not bad:
            print("  doc/capabilities.json and doc/roadmap.json validate")
        return 1 if (changed or bad) else 0

    if not changed and out == text:
        print("  roadmap is current — nothing moved, file untouched")
        return 0

    tmp = DOC + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, DOC)
    print(f"  wrote {os.path.relpath(DOC, ROOT)}"
          + ("" if changed else " (markers only)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
