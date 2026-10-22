#!/usr/bin/env python3
"""The routes incident clips add (lib/clips.py). lib/api.py tries these first.

    GET  /api/clips              the service's state and every incident, newest first
    GET  /api/clips/last         the newest incident's id and time: what the toast polls
    GET  /api/clips/item?id=     one incident and its telemetry
    POST /api/clips/save         Save clip: {} -- asks the service; falls back to the
                                 recorder's own lock when the service is not running
    POST /api/clips/delete       {id} -- parked only, here as well as in the app
    POST /api/clips/export       {id} -- copy to ~/Videos/OmaCar/exports/<id>/

The video itself is served by lib/serve.py, which has Range:
    GET  /api/clips/media/<id>/<file>.mp4
"""

import json
import os
import sys
import time
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camstore  # noqa: E402
import clips     # noqa: E402

PARKED_BELOW_KPH = 3.0     # delete is refused at or above this


def _body(body):
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _speed():
    """The car's speed now, or None when unknown. The simulator counts: if the
    screen says the car is moving, the lock holds."""
    try:
        import records
        s = records.live()
    except Exception:                                          # noqa: BLE001
        return None
    v = (s.get("values") or {}).get("SPEED") if isinstance(s, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _summary(m):
    keep = ("id", "trigger", "label", "t", "peak_g", "speed_before", "speed_after",
            "state", "sim", "roles", "also", "note", "size", "spark")
    return {k: m.get(k) for k in keep}


def handle_get(path, query):
    if path == "/api/clips":
        st = clips.read_public()
        return 200, {"running": st["running"], "accel": st.get("accel"),
                     "triggers": st.get("triggers"), "free": st.get("free"),
                     "note": st.get("note"), "totals": clips.totals(),
                     "incidents": [_summary(m) for m in clips.list_incidents()],
                     "now": time.time()}
    if path == "/api/clips/last":
        st = clips.read_public()
        return 200, {"running": st["running"], "last": st.get("last")}
    if path == "/api/clips/item":
        iid = (parse_qs(query or "").get("id") or [""])[0]
        got = clips.get_incident(iid)
        if not got:
            return 404, {"error": "no such incident"}
        return 200, {"incident": _summary(got["incident"]), "telemetry": got["telemetry"]}
    return None


def handle_post(path, body):
    if path not in ("/api/clips/save", "/api/clips/delete", "/api/clips/export"):
        return None
    data = _body(body)
    if data is None:
        return 400, {"error": "the body must be a JSON object"}
    if path == "/api/clips/save":
        try:
            if clips.ask("save"):
                return 200, {"ok": True, "via": "clips"}
        except OSError as e:
            return 503, {"error": f"could not ask the clip service: {e}"}
        # The service is not running. A tap must still keep the footage, so the
        # recorder's own lock (lib/camstore.py) does it: the same minute on
        # every camera, without the telemetry.
        ev = camstore.mark("saved", speed_kph=_speed())
        return 200, {"ok": True, "via": "loop", "id": ev.get("id"),
                     "note": "The clip service is not running, so this was saved "
                             "as a locked loop clip, without telemetry."}
    iid = data.get("id")
    if not isinstance(iid, str) or not clips.ID_RE.match(iid):
        return 400, {"error": "id is an incident id"}
    if path == "/api/clips/delete":
        v = _speed()
        if v is not None and v >= PARKED_BELOW_KPH:
            return 409, {"error": "Parked only: delete is off while the car is moving."}
        if not clips.delete_incident(iid):
            return 404, {"error": "no such incident"}
        return 200, {"ok": True}
    try:
        dest = clips.export_incident(iid)
    except ValueError:
        return 404, {"error": "no such incident"}
    except OSError as e:
        return 500, {"error": f"could not copy: {e}"}
    return 200, {"ok": True, "path": dest, "display": dest.replace(os.path.expanduser("~"), "~", 1)}
