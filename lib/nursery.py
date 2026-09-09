"""The baby, from the road.

WHAT THIS IS FOR. Long night drives, and a small child asleep at home. The
question is not "show me a nursery app" -- it is "is anything happening"
answered on a screen that is already on the dashboard, without picking up a
phone.

WHAT IT DELIBERATELY IS NOT.

It is not a Cradlewise client. OmaCar holds no account, no token and no
password for anybody's nursery, and it never talks to that service. There is
already a program on the home machine that does -- omababy -- which has the
credentials in a login keyring, merges what two services each half-know, and
writes one document. This reads that document. One credential store, one API
client, one place where a token expires.

It is not a live feed by default, and never while the car is moving. A video
of a sleeping child is the single most attention-capturing thing that could be
put on a windscreen-height screen. The numbers are safe to glance at, the way
a fuel gauge is. Moving pictures are not, and the app already knows the road
speed, so the camera is refused with the reason on it rather than left to
judgement at two in the morning.

HOW THE DOCUMENT GETS HERE, AND WHY IT IS PUSHED.

The tablet lives in a car on a tethered hotspot; the home machine is always on
and always has the credentials. So the home machine pushes -- `omacar nursery
send omacar` over the tailnet, on a timer -- and the tablet only ever reads a
local file. Nothing new listens on any port, no secret is copied, and a tablet
that was in a tunnel comes back to a document that is merely old.

WHICH IS WHY THE AGE IS NOT OPTIONAL. A pushed document is stale by
construction, and "asleep" from forty minutes ago shown as though it were now
is exactly the lie that makes a parent stop looking at the screen. Every answer
here carries how old it is, and past a threshold it stops claiming to know.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import records   # noqa: E402

DOC = os.path.join(records.STATE, "nursery.json")

# Past this the document stops answering "is he asleep" and starts answering
# "nothing has arrived for a while", which is a different and more useful
# thing to say to somebody who is driving.
STALE = 20 * 60

# Where omababy keeps what it merged. Read on the HOME machine only, by
# `send`. The tablet never looks here and does not have it.
SOURCE = os.path.join(os.path.expanduser("~"), ".local", "state", "omababy",
                      "state.json")

# THE ONLY FIELDS THAT CROSS. A whitelist rather than the whole document,
# because the thing being copied to a machine that lives in a car should be
# the answer to the question and not everything another program happens to
# know. Anything omababy adds later stays at home until somebody adds it here
# on purpose.
KEEP = ("name", "age", "state", "in_crib", "for_mins", "last_night_min",
        "naps", "feeds", "diapers", "sleep_min", "in_bed_min", "rise",
        "bedtime", "sources", "fetched", "camera", "nightlight", "from")


def read():
    """The document as it arrived, or None if none ever has."""
    try:
        with open(DOC, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def summary():
    """What to put on a screen, with how old it is attached to it.

    `known` is the load-bearing field: false means the numbers below it are
    history, not status, and whatever draws this must say so rather than
    showing a stale "asleep" in the present tense.
    """
    doc = read()
    if not doc:
        return {"configured": False,
                "why": "nothing has been sent here yet. On the machine at "
                       "home, once: omacar nursery send " + os.uname().nodename
                       + " — then, on a timer: systemctl --user enable --now "
                         "omacar-nursery.timer"}
    sent = doc.get("sent_at") or 0
    # NOT `age`. The document already carries an `age` -- the baby's, "6
    # months old" -- and calling this one that let the whitelist loop below
    # overwrite the freshness with a string, which then divided by sixty.
    ago = max(0.0, time.time() - sent)
    out = {"configured": True, "sent_ago": ago, "known": ago <= STALE,
           "stale_after": STALE}
    for k in KEEP:
        if k in doc:
            out[k] = doc[k]
    return out


def _flatten(state):
    """omababy's cache, reduced to the fields that travel.

    A WHITELIST, NOT A COPY. The document that goes to a machine living in a
    car should be the answer to the question and nothing else -- omababy also
    knows a growth curve, a five-month history and a lifetime tally, none of
    which anybody reads at seventy miles an hour, and all of which would then
    exist on a second device.

    Defensive about shape on purpose: this runs on the home machine against
    another project's file, and that project is free to change. A field that
    moved should cost that one field, not the whole push.
    """
    def dig(*path, default=None):
        cur = state
        for p in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(p)
        return default if cur is None else cur

    light = dig("nightlight", default={}) or {}
    return {
        "name": dig("baby", "name"),
        "age": dig("baby", "age"),
        "state": dig("now", "state"),
        "in_crib": dig("now", "in_crib"),
        "for_mins": dig("now", "for_mins"),
        "last_night_min": dig("last_night", "total_min"),
        "naps": dig("today", "naps"),
        "feeds": dig("today", "feeds"),
        "diapers": dig("today", "diapers"),
        "sleep_min": dig("today", "sleep_min"),
        "in_bed_min": dig("today", "in_bed_min"),
        "rise": dig("today", "rise"),
        "bedtime": dig("today", "bedtime"),
        # Which services are answering. This is the alert the ask actually
        # named: a nursery that has gone quiet because a token expired looks
        # exactly like a nursery where nothing is happening.
        "sources": {k: {"ok": bool(v.get("ok")), "error": v.get("error") or ""}
                    for k, v in (dig("sources", default={}) or {}).items()
                    if isinstance(v, dict)},
        # The camera is a fact about the crib, not a stream. Whether one is
        # available travels; the pictures never do -- see the module docstring.
        "camera": bool(dig("stream", "available")),
        # Three fields of the lamp, because "is the light still on" is a real
        # two-in-the-morning question and the rest of that device is not.
        "nightlight": {"on": bool(light.get("light_on")),
                       "color": light.get("color") or "",
                       "playing": bool(light.get("playing"))} if light else {},
        "fetched": dig("fetched"),
    }


def compose(source=None):
    """The document to send. Runs on the home machine."""
    path = source or SOURCE
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as why:
        return None, f"could not read {path}: {why}"
    doc = _flatten(state)
    doc["sent_at"] = time.time()
    doc["from"] = os.uname().nodename
    return doc, None


def send(host, source=None):
    """Push it to a tablet. Returns (ok, sentence).

    Over ssh and nothing else. No port is opened at either end, the identity
    is the one the tailnet already established, and the failure mode is a
    document that gets old rather than a service that is down.
    """
    doc, why = compose(source)
    if why:
        return False, why
    body = json.dumps(doc)
    remote = ("mkdir -p ~/.local/share/omacar && "
              "cat > ~/.local/share/omacar/nursery.json.tmp && "
              "mv ~/.local/share/omacar/nursery.json.tmp "
              "~/.local/share/omacar/nursery.json")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=15",
                            "-o", "BatchMode=yes", host, remote],
                           input=body, capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as err:
        return False, str(err)
    if r.returncode != 0:
        return False, (r.stderr or "ssh failed").strip().splitlines()[-1]
    return True, (f"sent to {host}\n"
                  "  every five minutes, from here: "
                  "systemctl --user enable --now omacar-nursery.timer\n"
                  "  (set OMACAR_TABLET in the unit if the tablet is not "
                  "called 'omacar')")


def _span(seconds):
    """A length of time. No "ago" -- some sentences need one and some do not."""
    m = int(seconds // 60)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


def _ago(seconds):
    return "just now" if seconds < 60 else _span(seconds) + " ago"


def _mins(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return f"{n // 60}h {n % 60:02d}m" if n >= 60 else f"{n}m"


def status():
    s = summary()
    print()
    if not s["configured"]:
        print("  nothing here yet")
        print(f"  {s['why']}")
        print()
        return 0
    who = s.get("name") or "the baby"
    if s["known"]:
        bits = [str(s.get("state") or "unknown")]
        if _mins(s.get("for_mins")):
            bits.append(_mins(s["for_mins"]))
        if s.get("in_crib") is not None:
            bits.append("in the crib" if s["in_crib"] else "not in the crib")
        print(f"  {who:12} {' · '.join(bits)}")
    else:
        # IT STOPS CLAIMING TO KNOW. Everything below this line is history,
        # and saying so is the difference between a screen worth glancing at
        # and one somebody learns to ignore.
        print(f"  {who:12} nothing has arrived for {_span(s['sent_ago'])}")
        print(f"  {'':12} what follows is the last that did")
    for label, key, fmt in (("last night", "last_night_min", _mins),
                            ("today", "sleep_min", _mins),
                            ("in bed", "in_bed_min", _mins),
                            ("naps", "naps", str),
                            ("feeds", "feeds", str),
                            ("diapers", "diapers", str)):
        v = s.get(key)
        if v not in (None, ""):
            shown = fmt(v)
            if shown:
                print(f"  {label:12} {shown}")
    if s.get("rise") or s.get("bedtime"):
        print(f"  {'clock':12} rise {s.get('rise') or '—'} · "
              f"bed {s.get('bedtime') or '—'}")
    light = s.get("nightlight") or {}
    if light:
        # The colour is only worth printing when it is one. A lamp reporting
        # #000000 while its light is on is the device's way of saying white.
        hue = light.get("color") or ""
        shade = f" {hue}" if hue and hue.lower() not in ("#000000", "#fff") else ""
        print(f"  {'nightlight':12} "
              f"{'on' + shade if light.get('on') else 'off'}"
              f"{' · playing' if light.get('playing') else ''}")
    down = [k for k, v in (s.get("sources") or {}).items() if not v.get("ok")]
    if down:
        print(f"  {'not answering':12} {', '.join(down)}")
    print(f"  {'sent':12} {_ago(s['sent_ago'])} from {s.get('from', 'somewhere')}")
    print()
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "send":
        if len(argv) < 2:
            print("  omacar nursery send HOST   (the tablet, by ssh name)",
                  file=sys.stderr)
            return 2
        ok, msg = send(argv[1], argv[2] if len(argv) > 2 else None)
        print(f"  {msg}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    return status()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
