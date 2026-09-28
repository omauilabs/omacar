#!/usr/bin/env python3
"""The roadmap's capability map, and the checks that stop it lying.

Run directly, and from test/all.sh. Stdlib only.

WHAT THIS MUST NEVER DO: call roadmap.render() or roadmap.main() without
--list. Both run test/all.sh to count the checks, and test/all.sh runs this
file, so either call is a loop that ends when the machine does. Everything
below exercises the map, the claims and the git counting on their own.

Most checks hand check_capabilities() a small invented map and an invented set
of tracked files, one failure at a time, because each rule exists for one
specific way a hand-kept list goes wrong and the test should say which.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))

import modes  # noqa: E402
import roadmap as R  # noqa: E402

PASS = FAIL = 0


def head(title):
    print(f"\n  {title}\n")


def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"   ok   {label}")
    else:
        FAIL += 1
        print(f"   FAIL  {label}")


def cap(id, **kw):
    c = {"id": id, "domain": "d", "title": "A thing", "user": "wants it",
         "agent": "does it", "status": "shipped", "tier": "none",
         "needs": [], "depends": [], "evidence": ["lib/x.py"], "notes": ""}
    c.update(kw)
    return c


def world(*caps, domains=("d",)):
    return {"schema": 1,
            "domains": [{"id": d, "title": d.upper()} for d in domains],
            "hardware": {"obd-usb": {"title": "An adapter"}},
            "capabilities": list(caps)}


LIVE = {"lib/x.py", "lib/y.py"}


def problems(doc, live=LIVE):
    return R.check_capabilities(doc, live, modes.ACTIONS)


def says(doc, fragment, live=LIVE):
    return any(fragment in p for p in problems(doc, live))


# ---------------------------------------------------------------------------
head("The capability map in the tree")

doc, why = R.load_capabilities()
ok("doc/capabilities.json loads", doc is not None)
if doc is not None:
    if R.in_checkout():
        found = R.check_capabilities(doc, set(R.tracked()))
        ok("and validates against git and lib/modes.py"
           + ("" if not found else f": {found[:3]}"), not found)
    else:
        print("    (skipping the git half: not a checkout)")
    s = R.capability_summary(doc)
    ok("every domain it names has at least one capability",
       all(sum(r.values()) for r in s["rows"].values()))
    shipped = [c for c in doc["capabilities"] if c["status"] == "shipped"]
    ok("nothing shipped writes to the car without a gate",
       all(c.get("gate") for c in shipped if c["tier"] in ("write", "actuate")))
    ok("no capability is gated by something every mode refuses",
       not any(modes.ACTIONS.get(c.get("gate"), {}).get("tier", "x") is None
               for c in doc["capabilities"] if c.get("gate")))

# ---------------------------------------------------------------------------
head("A shipped capability has to point at something git tracks")

ok("a clean map has no problems", problems(world(cap("a.one"))) == [])
ok("shipped with no evidence is refused",
   says(world(cap("a.one", evidence=[])), "shipped with no evidence"))
ok("building with no evidence is refused too",
   says(world(cap("a.one", status="building", evidence=[])),
        "building with no evidence"))
ok("a planned item may have none",
   problems(world(cap("a.one", status="next", evidence=[]))) == [])
ok("a file git does not track is refused, by name",
   says(world(cap("a.one", evidence=["lib/nope.py"])),
        "lib/nope.py is not tracked"))
ok("a path outside the repository is refused",
   says(world(cap("a.one", evidence=["../etc/passwd"])), "inside the repository"))
nogit = problems(world(cap("a.one"), cap("a.two")), live=None)
ok("without git it says so once, rather than calling every file untracked",
   len(nogit) == 1 and "not a git checkout" in nogit[0])

# ---------------------------------------------------------------------------
head("The fields mean one thing each")

ok("an unknown status is refused",
   says(world(cap("a.one", status="done")), "status 'done'"))
ok("an unknown tier is refused",
   says(world(cap("a.one", tier="dangerous")), "tier 'dangerous'"))
ok("a mistyped field is refused rather than ignored",
   says(world(cap("a.one", evidnce=["lib/x.py"])), "unknown field evidnce"))
ok("a missing field is named",
   says(world({k: v for k, v in cap("a.one").items() if k != "agent"}),
        "missing agent"))
ok("an empty sentence is refused",
   says(world(cap("a.one", user="  ")), "`user` must say something"))
ok("an id used twice is refused",
   says(world(cap("a.one"), cap("a.one")), "id used twice"))
ok("an id that is not lowercase words is refused",
   says(world(cap("A One")), "`id` must be lowercase"))
ok("an unknown domain is refused",
   says(world(cap("a.one", domain="nowhere")), "unknown domain 'nowhere'"))
ok("a domain nothing belongs to is flagged",
   says(world(cap("a.one"), domains=("d", "empty")), "domain empty: has no"))
ok("hardware that is not defined is refused",
   says(world(cap("a.one", needs=["flux-capacitor"])), "'flux-capacitor'"))

# ---------------------------------------------------------------------------
head("A write names the gate that decides it")

ok("a write with no gate is refused",
   says(world(cap("a.one", tier="write")), "must name its `gate`"))
ok("an actuation with no gate is refused",
   says(world(cap("a.one", tier="actuate")), "must name its `gate`"))
ok("a gate lib/modes.py does not know is refused",
   says(world(cap("a.one", tier="write", gate="yolo")),
        "'yolo' is not an action"))
ok("reprogramming is a refusal, not a capability",
   says(world(cap("a.one", tier="write", gate="reprogram")),
        "refused in every mode"))
ok("and so is guessing a routine",
   says(world(cap("a.one", tier="actuate", gate="guess_routine")),
        "refused in every mode"))
ok("something that sends nothing has no gate",
   says(world(cap("a.one", tier="none", gate="read")), "has no gate"))
ok("a real gate is accepted",
   problems(world(cap("a.one", tier="write", gate="clear_codes"))) == [])

# ---------------------------------------------------------------------------
head("Dependencies resolve, and shipped stands on shipped")

ok("a dependency that does not exist is refused",
   says(world(cap("a.one", depends=["a.ghost"])), "'a.ghost', which is not"))
ok("depending on yourself is refused",
   says(world(cap("a.one", depends=["a.one"])), "depends on itself"))
ok("shipped cannot stand on something still planned",
   says(world(cap("a.one", depends=["a.two"]),
              cap("a.two", status="next", evidence=[])),
        "shipped, but depends on a.two, which is next"))
ok("planned can stand on planned",
   problems(world(cap("a.one", status="later", evidence=[], depends=["a.two"]),
                  cap("a.two", status="next", evidence=[]))) == [])
loop = problems(world(cap("a.one", status="next", evidence=[], depends=["a.two"]),
                      cap("a.two", status="next", evidence=[], depends=["a.one"])))
ok("a loop is found and printed once",
   len([p for p in loop if p.startswith("dependency loop")]) == 1)

# ---------------------------------------------------------------------------
head("The block counts what the map says")

fixture = world(cap("a.one"), cap("a.two", evidence=["lib/y.py"]),
                cap("a.three", status="next", evidence=[]),
                cap("b.one", domain="e", status="research", evidence=[]),
                domains=("d", "e"))
lines = []
R.render_capabilities(lines.append, fixture)
text = "\n".join(lines)
ok("the total is the number of entries", "4 capabilities across 2 domains" in text)
ok("each domain's row adds up",
   "| D | 2 | 0 | 1 | 0 | 0 | 3 |" in text and "| E | 0 | 0 | 0 | 0 | 1 | 1 |" in text)
ok("the totals row adds up", "| **All domains** | **2** | **0** | **1** | **0** | **1** | **4** |" in text)
ok("every shipped entry is listed with the file that proves it",
   "A thing (`lib/x.py`) · A thing (`lib/y.py`)" in text)
ok("a domain with nothing shipped says so", "*E* — nothing yet." in text)

if doc is not None:
    real = []
    R.render_capabilities(real.append, doc)
    s = R.capability_summary(doc)
    ok("the real map's table total matches its length",
       f"**{len(doc['capabilities'])}** |" in "\n".join(real)
       and s["count"] == len(doc["capabilities"]))

out = io.StringIO()
with redirect_stdout(out):
    rc = R.main(["--list", "next"])
listed = out.getvalue()
ok("`--list next` answers, and shows what each item stands on",
   rc == 0 and "depends:" in listed and "(shipped)" in listed)
out = io.StringIO()
with redirect_stdout(out):
    rc = R.main(["--list", "eventually"])
ok("`--list` with a status that does not exist is refused", rc == 1)

# ---------------------------------------------------------------------------
head("Claims carry a date, a way to re-check, and where they came from")

good = {"text": "A price.", "asserted": "2026-09-28", "recheck_days": 90,
        "how": "Look again.", "sources": ["https://example.org/price"],
        "verified": True}
ok("a complete claim passes", R.check_claims({"claims": [good]}) == [])
bad = dict(good, asserted="last Tuesday")
ok("a date that is not a date is refused",
   any("YYYY-MM-DD" in p for p in R.check_claims({"claims": [bad]})))
bad = dict(good, how="")
ok("a claim with no way to re-check it is refused",
   any("no `how`" in p for p in R.check_claims({"claims": [bad]})))
bad = dict(good, sources=["http://example.org"])
ok("a source must be an https address",
   any("https://" in p for p in R.check_claims({"claims": [bad]})))
bad = {k: v for k, v in good.items() if k != "verified"}
ok("a sourced claim must say whether the number was seen there",
   any("verified" in p for p in R.check_claims({"claims": [bad]})))
ok("the claims in the tree pass", R.check_claims(R.load_data()) == [])

lines = []
R.render_claims(lines.append, {"claims": [dict(good, verified=False)]},
                now=R.time.mktime(R.time.strptime("2026-10-05", "%Y-%m-%d")))
text = "\n".join(lines)
ok("an unverified claim is marked unverified", "(unverified)" in text)
ok("its sources are printed", "<https://example.org/price>" in text)
ok("its age is not, so the block does not change every midnight",
   "days ago" not in text and "today" not in text)
lines = []
R.render_claims(lines.append, {"claims": [good]},
                now=R.time.mktime(R.time.strptime("2027-06-01", "%Y-%m-%d")))
ok("a claim past its re-check date says so", "due a re-check" in "\n".join(lines))

# ---------------------------------------------------------------------------
head("Regenerating the roadmap is not shipping something")

git = shutil.which("git")
if not git:
    print("    (skipping: no git here)")
else:
    tmp = tempfile.mkdtemp(prefix="omacar-roadmap-test-")
    run = lambda *a: subprocess.run(  # noqa: E731
        ("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
         "commit.gpgsign=false") + a, cwd=tmp, check=True,
        capture_output=True)
    try:
        run("init", "-q")
        os.makedirs(os.path.join(tmp, "doc"))
        with open(os.path.join(tmp, "lib.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(tmp, "doc", "ROADMAP.md"), "w") as f:
            f.write("one\n")
        run("add", "-A")
        run("commit", "-q", "-m", "Real work")
        with open(os.path.join(tmp, "doc", "ROADMAP.md"), "w") as f:
            f.write("two\n")
        run("commit", "-q", "-am", "Roadmap: regenerated")
        saved = R.ROOT
        R.ROOT = tmp
        try:
            s = R.shipped()
            ok("a commit that only rewrites doc/ROADMAP.md is not counted",
               s["count"] == "1")
            ok("and is not listed as shipped",
               [r[1] for r in s["rows"]] == ["Real work"])
            ok("a real checkout is recognised as one", R.in_checkout())
        finally:
            R.ROOT = saved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    nowhere = tempfile.mkdtemp(prefix="omacar-roadmap-nogit-")
    saved = R.ROOT
    R.ROOT = nowhere
    try:
        ok("a directory with no git is recognised as not a checkout",
           not R.in_checkout())
    finally:
        R.ROOT = saved
        shutil.rmtree(nowhere, ignore_errors=True)

# ---------------------------------------------------------------------------
head("A byte that is not UTF-8 cannot stop the count")

fake = tempfile.mkdtemp(prefix="omacar-roadmap-bytes-")
os.makedirs(os.path.join(fake, "test"))
with open(os.path.join(fake, "test", "all.sh"), "wb") as f:
    f.write(b"#!/bin/bash\n"
            b"printf '\\n  A suite with a camera called \\xff\\xfe\\n\\n'\n"
            b"printf '   ok   one\\n   ok   two\\n'\n")
saved = R.ROOT
R.ROOT = fake
try:
    t = R.run_tests()
    ok("run_tests reads a suite whose output is not valid UTF-8",
       t is not None and t["checks"] == 2 and t["ok"])
finally:
    R.ROOT = saved
    shutil.rmtree(fake, ignore_errors=True)

print(f"\n  {PASS} passed, {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
