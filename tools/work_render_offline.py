#!/usr/bin/env python3
"""Render the lines the tablet must be able to say with no relay.
Run where Piper is installed; writes share/assets/work/<name>.wav."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workvoice  # noqa: E402

out = os.path.join(ROOT, "share", "assets", "work")
os.makedirs(out, exist_ok=True)
bad = [n for n, text in workvoice.OFFLINE_LINES.items()
       if not workvoice.render(text, os.path.join(out, f"{n}.wav"))]
print("rendered" if not bad else f"Piper missing or failed for: {', '.join(bad)}")
sys.exit(1 if bad else 0)
