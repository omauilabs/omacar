#!/usr/bin/env python3
"""tools/demo_voice.py, against a stand-in Piper.

The meetup demo's spoken lines are rendered once, on the box, by Piper
(tools/demo_voice.py), and kept private. Here Piper is a fake that writes one
second of silence and logs what it was asked to say, so this runs anywhere,
needs no voice model, and can make no sound: nothing here plays anything.

Checks that every line in demo/data/voice.json is rendered, that a second run
renders none, that a newer voice.json renders them all again, that a line's
`speak` is what reaches Piper when it has one, and that each file's duration
is printed.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "demo_voice.py")
LINES = os.path.join(ROOT, "demo", "data", "voice.json")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


# Piper's own interface, as far as the tool uses it: `piper -m MODEL -f OUT`,
# the text on stdin. It writes a second of 22.05 kHz mono silence, as Piper's
# medium voices do, and appends what it heard to $FAKE_PIPER_LOG.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, wave
args = sys.argv[1:]
model = args[args.index("-m") + 1]
out = args[args.index("-f") + 1]
text = sys.stdin.read()
with open(os.environ["FAKE_PIPER_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"model": model, "out": out, "text": text}) + "\n")
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"\0\0" * 22050)
'''


def run(work, lines, out, log):
    env = dict(os.environ, FAKE_PIPER_LOG=log)
    return subprocess.run(
        [sys.executable, TOOL, "--lines", lines, "--out", out,
         "--piper", os.path.join(work, "piper"), "--model", os.path.join(work, "voice.onnx")],
        capture_output=True, text=True, env=env, timeout=60)


def heard(log):
    try:
        with open(log, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except FileNotFoundError:
        return []


def main():
    print("\n  The demo's voice lines, rendered by a stand-in Piper\n")
    with open(LINES, encoding="utf-8") as f:
        doc = json.load(f)
    ids = [x["id"] for x in doc]
    check("voice.json is a list of {id, text}", isinstance(doc, list) and all(
        isinstance(x.get("id"), str) and isinstance(x.get("text"), str) for x in doc))
    check("its ids are unique", len(ids) == len(set(ids)))

    with tempfile.TemporaryDirectory() as work:
        piper = os.path.join(work, "piper")
        with open(piper, "w", encoding="utf-8") as f:
            f.write(FAKE)
        os.chmod(piper, 0o755)
        lines = os.path.join(work, "voice.json")
        with open(lines, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        # An hour old, so a wav written now is plainly newer on any filesystem.
        past = time.time() - 3600
        os.utime(lines, (past, past))
        out = os.path.join(work, "voice")
        log = os.path.join(work, "piper.log")

        first = run(work, lines, out, log)
        check("the first run succeeds", first.returncode == 0)
        if first.returncode:
            print(first.stdout + first.stderr)
        made = sorted(f[:-4] for f in os.listdir(out) if f.endswith(".wav")) if os.path.isdir(out) else []
        check("every line is rendered", made == sorted(ids))
        check("Piper is run once per line", len(heard(log)) == len(ids))
        check("with the model it was given",
              all(h["model"] == os.path.join(work, "voice.onnx") for h in heard(log)))
        check("each file's duration is printed",
              all(f"{i}.wav" in first.stdout for i in ids) and first.stdout.count("1.00 s") >= len(ids))
        by_out = {os.path.basename(h["out"]).split(".")[0]: h["text"] for h in heard(log)}
        speaks = [x for x in doc if x.get("speak")]
        check("a line's `speak` is what Piper reads, when it has one",
              all(by_out.get(x["id"], "").strip() == x["speak"] for x in speaks))
        check("and its `text` otherwise",
              all(by_out.get(x["id"], "").strip() == x["text"] for x in doc if not x.get("speak")))

        before = len(heard(log))
        second = run(work, lines, out, log)
        check("a second run succeeds", second.returncode == 0)
        check("and renders none", len(heard(log)) == before)
        check("but still lists every duration", all(f"{i}.wav" in second.stdout for i in ids))

        os.utime(lines, None)          # voice.json edited: now newer than every wav
        time.sleep(0.01)
        third = run(work, lines, out, log)
        check("a newer voice.json renders every line again",
              third.returncode == 0 and len(heard(log)) == before + len(ids))

        broken = os.path.join(work, "broken")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 3\n")
        os.chmod(broken, 0o755)
        env = dict(os.environ, FAKE_PIPER_LOG=log)
        fail = subprocess.run(
            [sys.executable, TOOL, "--lines", lines, "--out", os.path.join(work, "v2"),
             "--piper", broken, "--model", "x.onnx"],
            capture_output=True, text=True, env=env, timeout=60)
        check("a Piper that fails fails the run", fail.returncode != 0)
        check("and leaves no half-written wav behind",
              not any(f.endswith(".wav") for f in os.listdir(os.path.join(work, "v2")))
              if os.path.isdir(os.path.join(work, "v2")) else True)

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  the voice lines render\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
