#!/usr/bin/env python3
"""Render the meetup demo's spoken lines with Piper, once, on the box.

    python3 tools/demo_voice.py --lines demo/data/voice.json \\
        --out ~/Projects/omacar/share/assets/private/demo/voice

writes OUT/<id>.wav for every line in voice.json ([{id, text, speak?}]).
Piper reads `speak` where a line has one -- "P oh four twenty" for a caption
that says P0420 -- and `text` otherwise. The demo page plays the files at
/demo-media/voice/<id>.wav (demo/js/voice.js say()) and captions them with
`text`.

A wav newer than voice.json is up to date and is skipped, so a second run
renders nothing. Every file's duration is printed either way.

NOTHING HERE PLAYS ANYTHING. Piper is always given -f, so it writes a file
and never reaches a sound device; listening to a line is a separate, deliberate
act. The voice (en_US-hfc_female-medium, CC BY-NC-SA 4.0 training data) and
Piper itself live in a private venv under ~/.local/share/omacar-demo-tools,
installed by hand: this tool downloads nothing.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import wave

TOOLS = os.path.expanduser("~/.local/share/omacar-demo-tools")
PIPER = os.path.join(TOOLS, "piper", "bin", "piper")
MODEL = os.path.join(TOOLS, "voices", "en_US-hfc_female-medium.onnx")
# An id is a file name and a URL path segment both.
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def load_lines(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, list):
        raise ValueError(f"{path}: expected a list of {{id, text}}")
    seen = set()
    for x in doc:
        if not (isinstance(x, dict) and isinstance(x.get("id"), str) and ID.match(x["id"])
                and isinstance(x.get("text"), str) and x["text"].strip()):
            raise ValueError(f"{path}: not a line: {x!r}")
        if x["id"] in seen:
            raise ValueError(f"{path}: {x['id']} twice")
        seen.add(x["id"])
    return doc


def duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def render(piper, model, words, out):
    """One line to `out`, through a partial file, so a Piper that dies leaves
    the last good wav (or nothing) rather than half of one."""
    part = out[:-len(".wav")] + ".part.wav"
    try:
        res = subprocess.run([piper, "-m", model, "-f", part], input=words, text=True,
                             capture_output=True, timeout=300)
        if res.returncode != 0 or not os.path.exists(part):
            raise RuntimeError((res.stderr or res.stdout or f"exit {res.returncode}").strip()[-400:])
        duration(part)                     # a wav that does not parse is not a line
        os.replace(part, out)
    finally:
        if os.path.exists(part):
            os.remove(part)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the demo's voice lines with Piper.")
    ap.add_argument("--lines", required=True, help="demo/data/voice.json")
    ap.add_argument("--out", required=True, help="the directory to write <id>.wav into")
    ap.add_argument("--piper", default=PIPER, help=f"the piper executable (default {PIPER})")
    ap.add_argument("--model", default=MODEL, help=f"the voice (default {MODEL})")
    a = ap.parse_args(argv)

    lines = load_lines(a.lines)
    since = os.stat(a.lines).st_mtime_ns
    os.makedirs(a.out, exist_ok=True)
    made = fresh = failed = 0
    for x in lines:
        out = os.path.join(a.out, x["id"] + ".wav")
        if os.path.exists(out) and os.stat(out).st_mtime_ns > since:
            fresh += 1
            verb = "kept"
        else:
            try:
                render(a.piper, a.model, x.get("speak") or x["text"], out)
            except (OSError, RuntimeError, wave.Error, EOFError, subprocess.TimeoutExpired) as e:
                failed += 1
                print(f"  FAIL  {x['id']}.wav  {e}")
                continue
            made += 1
            verb = "made"
        print(f"  {verb}  {x['id']}.wav  {duration(out):.2f} s")
    print(f"\n  {made} rendered, {fresh} up to date" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
