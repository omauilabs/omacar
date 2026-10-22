#!/usr/bin/env python3
"""Things the car needs to tell the owner when the owner is not at the screen.

One outbox, shared by every feature that has to reach a phone: the child left
in the car, the 12 V battery running down while parked, the car arriving home.
A feature calls send(); the event is written once to an outbox file (one JSON
object per line, capped), and any paired client reads it with
`GET /api/notify?since=<id>` over Tailscale.

WHAT IS NOT HERE YET, SAID PLAINLY. omarchy-mobile (frogbark/omarchy-mobile)
is the phone side. Its `omarchy-link` daemon runs on an Omarchy computer, and
the phone is its client; its event stream carries only theme changes today, and
its /v1/notify goes the other way (phone -> desktop). So nothing pushes these
to a phone until omarchy-mobile gains a "car" subscriber that reads this route
(or omarchy-link gains a spool its /v1/events relays). Until then they show on
the tablet, are kept here, and `omacar notify list` prints them.

    omacar notify list           the last 20
    omacar notify test           one harmless test event
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
OUTBOX = os.path.join(STATE_DIR, "notify-outbox.jsonl")
KEEP = 200
URGENCY = ("info", "warn", "critical")


def _read():
    try:
        with open(OUTBOX, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def send(kind, title, body, urgency="info", key=None, now=None):
    """Record one event for the owner. Returns the event, or None when `key`
    names an event already sent in the last `quiet` window (see below).

    kind: short machine name ("child-left", "battery-low", "arrival").
    key:  dedupe key -- the same key is not sent twice within 10 minutes,
          so a condition that holds for an hour is one message, not sixty.
    """
    if urgency not in URGENCY:
        raise ValueError(f"urgency is one of {URGENCY}")
    now = time.time() if now is None else now
    rows = _read()
    if key:
        for r in reversed(rows):
            if r.get("key") == key and now - r.get("at", 0) < 600:
                return None
    ev = {"id": (rows[-1]["id"] + 1) if rows else 1, "at": round(now, 1), "kind": str(kind)[:40],
          "title": str(title)[:120], "body": str(body)[:500], "urgency": urgency, "key": key}
    rows.append(ev)
    rows = rows[-KEEP:]
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = OUTBOX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, OUTBOX)
    return ev


def since(after_id=0, limit=50):
    """Events newer than `after_id`, oldest first: what a client polls."""
    return [r for r in _read() if r.get("id", 0) > after_id][:limit]


def main(argv):
    what = argv[0] if argv else "list"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "list":
        for r in _read()[-20:]:
            t = time.strftime("%m-%d %H:%M", time.localtime(r["at"]))
            print(f"  {r['id']:>4}  {t}  {r['urgency']:8}  {r['title']} — {r['body']}")
        return 0
    if what == "test":
        ev = send("test", "OmaCar test", "If your phone shows this, notifications reach it.", "info")
        print(f"  queued #{ev['id']}")
        return 0
    print("  omacar notify: list | test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
