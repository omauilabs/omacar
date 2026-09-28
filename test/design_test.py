#!/usr/bin/env python3
"""The palette keeps its own promise: every text colour clears WCAG AA on the
surface it is drawn on.

The tokens were sampled from the owner's mockups (doc/design/
2026-09-28-foundation.md, "The look"), and a sampled colour is only a start: a
caption that reads beautifully in a mockup can be 3:1 on a real panel in a real
car at noon. This reads the values out of app.css itself, so the test and the
stylesheet cannot drift apart.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "share", "css", "app.css")

# (text token, background token, minimum ratio, what it is)
PAIRS = [
    ("ink", "ground", 7.0, "body text on the page"),
    ("ink", "panel", 7.0, "body text on a card"),
    ("ink-2", "panel", 4.5, "secondary labels on a card"),
    ("dim", "panel", 4.5, "captions on a card"),
    ("faint", "panel", 4.5, "the faintest text that is still text"),
    ("accent", "ground", 4.5, "the active tab's label"),
    ("accent", "panel", 4.5, "accent text on a card"),
    ("ok", "panel", 4.5, "NORMAL and the check marks"),
    ("warn", "panel", 4.5, "warnings"),
    ("bad", "panel", 4.5, "faults"),
    ("rec", "panel", 4.5, "REC"),
    ("on-accent", "accent-fill", 4.5, "text on the primary button"),
]

fails = 0


def check(msg, cond):
    global fails
    if not cond:
        fails += 1
    print(f"    {'ok  ' if cond else 'FAIL'}  {msg}")


def block(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", m.group(1))) if m else {}


def lum(hexv):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def run(name, tokens):
    print(f"\n  {name}\n")
    for fg, bg, need, what in PAIRS:
        if fg not in tokens or bg not in tokens:
            check(f"--{fg} and --{bg} are both declared ({what})", False)
            continue
        r = ratio(tokens[fg], tokens[bg])
        check(f"{what}: --{fg} on --{bg} is {r:.2f}:1 (needs {need})", r >= need)


def main():
    css = open(CSS, encoding="utf-8").read()
    base = block(css, ":root")
    run("The default palette", base)
    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  every pair reads\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
