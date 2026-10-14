"""Work's privacy: nothing that names the car or its owner reaches a model or a log.

The same two nets as lib/mcp.py: the vehicle record's identifying strings by
value, and anything shaped like a VIN by shape.
"""
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp import IDENTIFIER, VEHICLE_SECRET, WITHHELD  # noqa: E402


def secrets_from(vehicle):
    out = []
    for key in VEHICLE_SECRET:
        s = str((vehicle or {}).get(key) or "").strip()
        if len(s) >= 4:          # mcp.py's floor: shorter strings hit ordinary words
            out.append(s)
    return out


def scrub(text, secrets):
    text = str(text or "")
    for s in secrets:
        text = re.sub(re.escape(s), "this car", text, flags=re.I)
    return IDENTIFIER.sub(WITHHELD, text)
