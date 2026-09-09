"""Asking the app to put a screen up, from somewhere that is not the app.

WHY THIS EXISTS. The tablet is used for twenty-five hours a week in a moving
car, and the only interaction that is safe there is the one that does not
involve looking at or touching anything. That means the voice assistant, and
an assistant that can only read the car is half a thing: "how is the battery"
gets an answer, "show me the battery" does not.

So this is one small channel from outside the browser into it -- a file saying
which screen was last asked for and when. The app reads it on the clock it
already has and moves if the request is newer than the last one it honoured.

WHAT IT CANNOT DO, WHICH IS MOST OF IT.

It cannot touch the car. Not a byte reaches the bus through here; the app's
own gates -- the write arm, the tier, the motion and voltage checks -- are all
downstream of a screen being visible and none of them are weakened by which
screen that is. The worst an attacker with this channel could do is change
which page somebody is looking at.

It cannot yank the screen repeatedly. Requests carry a timestamp and the app
honours each one exactly once. This is the rule that matters: main.js has a
standing prohibition on writing location.hash except in direct response to a
person, because an earlier version pushed the hash on every live sample and
threw people off whatever they were reading four times a second. A voice
request IS a person -- but only the first time it is seen.

And it never happens silently. A screen that changed by itself with nothing to
say why is indistinguishable from a bug, so the app names who asked.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records   # noqa: E402

ASK = os.path.join(records.STATE, "screen.json")

# A request older than this is not honoured at all. Somebody who asked for the
# battery screen, drove for an hour and then opened the app did not mean for
# it to open on the battery screen.
FRESH = 90.0


def ask(view, who="somebody"):
    """Record that a screen was asked for. Returns the record."""
    rec = {"view": str(view), "at": time.time(), "who": str(who)}
    os.makedirs(os.path.dirname(ASK), exist_ok=True)
    tmp = ASK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, ASK)
    return rec


def pending():
    """The current request, or an empty one. Never raises."""
    try:
        with open(ASK, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return {"view": None, "at": 0, "who": None, "fresh": False}
    age = time.time() - (rec.get("at") or 0)
    rec["fresh"] = age <= FRESH
    return rec


# WHAT SCREENS THERE ARE, PUBLISHED BY THE APP RATHER THAN LISTED HERE.
#
# The registry lives in share/js/main.js and includes whatever plugin screens
# somebody dropped in. A second copy in Python would be a second copy of the
# truth, and the one that drifts is always the one nobody is looking at. So the
# running app posts its own list at boot, and anything asking "what can I ask
# for" gets the answer from the app that would have to honour it.
SCREENS = os.path.join(records.STATE, "screens.json")


def publish(views):
    """Called by the app at boot with [{id, label, title, tab}, ...]."""
    clean = []
    for v in views or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        clean.append({"id": str(v["id"])[:40],
                      "label": str(v.get("label") or v["id"])[:40],
                      "title": str(v.get("title") or "")[:120],
                      "tab": str(v.get("tab") or "")[:40]})
    rec = {"at": time.time(), "views": clean[:80]}
    os.makedirs(os.path.dirname(SCREENS), exist_ok=True)
    tmp = SCREENS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, SCREENS)
    return rec


def known():
    """What the app last said it had. Empty when it has never run here."""
    try:
        with open(SCREENS, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return []
    return rec.get("views") or []


def clear():
    try:
        os.unlink(ASK)
    except OSError:
        pass
