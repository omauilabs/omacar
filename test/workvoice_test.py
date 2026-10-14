#!/usr/bin/env python3
"""Spoken lines: short, fixed, and never a diff. Piper is a fake here."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workvoice as v  # noqa: E402

fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

check("started", v.line({"kind": "started", "project": "omacar"}), "omacar session started.")
check("pr", v.line({"kind": "pr_opened", "project": "omacar", "pr": 41}),
      "omacar: gate passed, draft PR 41 opened.")
long_q = "Should it be 0.8 s? Or configurable? Here is the diff: --- a/x +++ b/x"
said = v.line({"kind": "question", "project": "omacar", "question": long_q})
check("question cut to its first sentence", said, "omacar has a question: Should it be 0.8 s?")
check("at most two sentences, ever",
      v.line({"kind": "refused", "project": "p", "why": "One. Two. Three."}).count(". ") <= 1, True)
check("unknown kind says nothing wrong", v.line({"kind": "nope"}), "")
check("quiet at 23:00 parked", v.quiet_now((21, 7), 23, True), True)
check("not quiet at 23:00 moving", v.quiet_now((21, 7), 23, False), False)
check("not quiet at noon", v.quiet_now((21, 7), 12, True), False)
check("quiet at 06:00", v.quiet_now((21, 7), 6, True), True)
ran = []
def fake_run(cmd, **kw):
    ran.append(cmd); open(cmd[cmd.index("--output_file") + 1], "wb").write(b"RIFF")
    class R: returncode = 0
    return R()
import tempfile; out = os.path.join(tempfile.mkdtemp(), "a.wav")
check("render calls piper with the line on stdin", (v.render("Hi.", out, run=fake_run), os.path.exists(out)), (True, True))
check("missing piper is False, not a crash", v.render("Hi.", out, run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())), False)

check("confirm cuts the instruction", v.line({"kind": "confirm", "project": "omacar",
      "instruction": "Fix the retry bug. Then update the docs"}), "omacar: Fix the retry bug. Start it?")
check("long instruction still asks", v.line({"kind": "confirm", "project": "omacar",
      "instruction": "word " * 80}).endswith("Start it?"), True)
check("trailing period not doubled", v.line({"kind": "confirm", "project": "omacar",
      "instruction": "fix it."}), "omacar: fix it. Start it?")
def deny(*a, **k): raise PermissionError()
check("render OSError is False", v.render("Hi.", out, run=deny), False)

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
