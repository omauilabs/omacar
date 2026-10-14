#!/usr/bin/env python3
"""Intent: a transcript becomes one checked thing to do, or a question back.
The model is a fake here; nothing reaches Claude."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workcfg, workintent as wi, workprivacy as wp  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

P = workcfg.Project
CFG = workcfg.Config(projects={
    "omacar": P("omacar", ("omacar", "car", "the car app"), "/p/omacar", "t"),
    "omalens": P("omalens", ("omalens", "lens"), "/p/omalens", "t"),
    "omasaber": P("omasaber", ("omasaber",), "/p/omasaber", "t")})

def fake(reply):
    calls = []
    def ask(prompt): calls.append(prompt); return reply
    ask.calls = calls
    return ask

print("\n  confirmation: only a spoken yes starts anything")
for said, kind in [("yes", "yes"), ("Yeah, start it.", "yes"), ("no", "no"),
                   ("cancel that", "no"), ("", "unclear"), ("um", "unclear"),
                   ("what about omalens", "unclear")]:
    m = fake({})
    check(f"{said!r} -> {kind}", wi.parse(said, CFG, pending="confirm", ask_model=m).kind, kind)
    check(f"{said!r} asked no model", m.calls, [])

print("\n  a pending question takes the words as the answer")
i = wi.parse("make it configurable", CFG, pending="question", ask_model=fake({}))
check("answer", (i.kind, i.answer), ("answer", "make it configurable"))

print("\n  new tasks are checked against the allowlist")
i = wi.parse("in omacar fix the timeout", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omacar", "instruction": "fix the timeout"}))
check("known project", (i.kind, i.project, i.instruction), ("new_task", "omacar", "fix the timeout"))
i = wi.parse("in omakar fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omakar", "instruction": "fix it"}))
check("near-miss resolves", (i.kind, i.project), ("new_task", "omacar"))
i = wi.parse("in the lab fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omalab", "instruction": "fix it"}))
check("unlisted project", i.kind, "unclear")
i = wi.parse("in oma fix it", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "oma", "instruction": "fix it"}))
check("ambiguous asks either/or", (i.kind, len(i.choices) >= 2), ("choose", True))
i = wi.parse("in omacar", CFG, pending=None,
             ask_model=fake({"kind": "new_task", "project": "omacar", "instruction": ""}))
check("no instruction", i.kind, "unclear")
i = wi.parse("x", CFG, pending=None, ask_model=fake({"kind": "launch_missiles"}))
check("unknown kind", i.kind, "unclear")
i = wi.parse("x", CFG, pending=None, ask_model=fake("not a dict"))
check("garbage from the model", i.kind, "unclear")

print("\n  scrub")
sec = wp.secrets_from({"vin": "JHMZF1D60FS000000", "plate": "8ABC123", "driver": "Jim"})
check("short names are not hunted", "Jim" in sec, False)
check("vin and plate hunted", ("JHMZF1D60FS000000" in sec, "8ABC123" in sec), (True, True))
out = wp.scrub("plate 8ABC123 and JHMZF1D60FS000000 and 1HGCM82633A004352", sec)
check("by value and by shape", ("8ABC123" in out, "JHMZF1D60FS000000" in out, "1HGCM82633A004352" in out),
      (False, False, False))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
