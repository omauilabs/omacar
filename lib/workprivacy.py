"""Work's privacy: nothing that names the car or its owner reaches a model or a log.

The same two nets as lib/mcp.py: the vehicle record's identifying strings by
value, and anything shaped like a VIN by shape.
"""
import os
import re
import importlib.util
import sys

# OmaCar's lib/mcp.py is loaded by path under its own name. Registering it as
# "mcp" would shadow the PyPI `mcp` package that claude_agent_sdk imports.
_spec = importlib.util.spec_from_file_location(
    "omacar_mcp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.py"))
_omacar_mcp = importlib.util.module_from_spec(_spec)
sys.modules["omacar_mcp"] = _omacar_mcp
_spec.loader.exec_module(_omacar_mcp)
IDENTIFIER, VEHICLE_SECRET, WITHHELD = (
    _omacar_mcp.IDENTIFIER, _omacar_mcp.VEHICLE_SECRET, _omacar_mcp.WITHHELD)


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
