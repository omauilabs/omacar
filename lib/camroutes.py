#!/usr/bin/env python3
"""The routes the cameras branch adds. lib/api.py tries these first (see the
redesign/cameras block at the end of that file).

    GET  /api/cams                         per role: device, mode, recording, real
                                           fps, clip count, storage, last error
    GET  /api/cams/clips?role=&from=&to=   the clips and events for the timeline
    POST /api/cams/mark                    Mark event: 30 s either side of now
    POST /api/cams/lock                    Save clip: 30 s either side of `t` (the
                                           playhead), or of now
    GET  /api/drowsy, POST /api/drowsy, POST /api/drowsy/event,
    POST /api/drowsy/log                   drowsy mode (lib/drowsycfg.py)

Two more are served by lib/serve.py itself, because neither is JSON:
    GET  /api/cams/<role>/live             multipart MJPEG, 10 fps
    GET  /api/cams/clip/<role>/<file>      the clip, with HTTP Range
"""

import json
import os
import sys
import time
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cams      # noqa: E402
import camstore  # noqa: E402


def _one(q, name):
    v = q.get(name)
    return v[0] if v else None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _body(body):
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _speed():
    try:
        import records
        s = records.live()
    except Exception:                                          # noqa: BLE001
        return None
    # The simulator's speed is not the car's, so it is not recorded as one.
    if not s.get("connected") or s.get("simulated"):
        return None
    v = (s.get("values") or {}).get("SPEED")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def handle_get(path, query):
    if path == "/api/cams":
        return 200, cams.overview()
    if path == "/api/cams/clips":
        q = parse_qs(query or "")
        role = _one(q, "role")
        if role and role not in camstore.ROLES:
            return 400, {"error": f"no camera role called {role!r}"}
        t0, t1 = _num(_one(q, "from")), _num(_one(q, "to"))
        return 200, {"clips": camstore.list_clips(role or None, t0, t1),
                     "events": camstore.list_events(t0, t1), "now": time.time()}
    if path == "/api/audio":
        import audio
        return 200, audio.status()
    if path == "/api/drowsy":
        import drowsycfg
        return 200, drowsycfg.load()
    return None


def handle_post(path, body):
    if path in ("/api/cams/mark", "/api/cams/lock"):
        data = _body(body)
        if data is None:
            return 400, {"error": "the body must be a JSON object"}
        t = data.get("t")
        if t is not None:
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                return 400, {"error": "t is seconds since the epoch"}
            if not time.time() - 7 * 86400 <= t <= time.time() + 5:
                return 400, {"error": "t is not within the last week"}
        kind = "marked" if path.endswith("/mark") else "saved"
        return 200, camstore.mark(kind, t=t, speed_kph=_speed())
    if path == "/api/audio":
        import audio
        data = _body(body)
        if data is None or data.get("action") != "apply":
            return 400, {"error": 'the one action is {"action": "apply"}'}
        return 200, audio.apply()
    if path in ("/api/drowsy", "/api/drowsy/event", "/api/drowsy/log"):
        import drowsycfg
        data = _body(body)
        if data is None:
            return 400, {"error": "the body must be a JSON object"}
        try:
            if path == "/api/drowsy":
                return 200, drowsycfg.save(data)
            if path == "/api/drowsy/event":
                return 200, drowsycfg.log_event(data)
            return 200, drowsycfg.log_measures(data.get("rows"))
        except ValueError as e:
            return 400, {"error": str(e)}
    return None
