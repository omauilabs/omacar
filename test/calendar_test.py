#!/usr/bin/env python3
"""The launch calendar, checked offline.

launch/calendar.json is a schedule somebody acts on by hand, one slot at a
time, for a month. A slot that is wrong -- a tweet over the limit, two posts
at the same minute, a fifth post on a day that was meant to have four, a VIN
in a thread -- is found at the moment of posting, which is the worst moment.
So the shape is checked here, and deliberately breaking an entry fails the
suite naming it.

Runs with the interpreter alone. No network, no car.
"""

import collections
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Checked where it is, not where it used to be: the environment variable lets
# the owner keep it anywhere, and the in-tree path is the fallback for anyone
# who does have one.
CALENDAR = os.environ.get("OMACAR_CALENDAR") or os.path.join(ROOT, "launch",
                                                             "calendar.json")

TWEET_MAX = 280
PER_DAY_MAX = 4
FIRST, LAST = datetime.date(2026, 9, 7), datetime.date(2026, 10, 7)
REVEAL = datetime.date(2026, 9, 30)
VINLIKE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
IDENT = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def problems(doc):
    """Every defect in a calendar, as sentences naming the entry."""
    out = []
    if doc.get("schema") != 1:
        out.append(f"schema is {doc.get('schema')!r}, expected 1")
    pillars = {p.get("key") for p in doc.get("pillars") or [] if p.get("key")}
    if not pillars:
        out.append("no pillars")
    # The reveal thread itself is not a pillar post; it is the day.
    pillars.add("reveal")
    try:
        reveal = datetime.date.fromisoformat(doc.get("reveal") or "")
        if reveal != REVEAL:
            out.append(f"reveal is {reveal}, expected {REVEAL}")
    except ValueError:
        out.append("reveal is not a date")
    posts = doc.get("posts")
    if not isinstance(posts, list) or not posts:
        return out + ["no posts"]
    ids, slots, per_day = set(), set(), collections.Counter()
    for i, e in enumerate(posts):
        who = e.get("id") or f"post {i + 1}"
        if not e.get("id") or not IDENT.match(e["id"]):
            out.append(f"{who}: id missing or not a slug")
        elif e["id"] in ids:
            out.append(f"{who}: duplicate id")
        ids.add(e.get("id"))
        try:
            day = datetime.date.fromisoformat(e.get("date") or "")
            if not FIRST <= day <= LAST:
                out.append(f"{who}: {day} is outside {FIRST}..{LAST}")
            per_day[day] += 1
        except ValueError:
            out.append(f"{who}: date {e.get('date')!r} is not a date")
            day = None
        if not TIME.match(str(e.get("time") or "")):
            out.append(f"{who}: time {e.get('time')!r} is not HH:MM")
        if (e.get("date"), e.get("time")) in slots:
            out.append(f"{who}: another post has the same date and time")
        slots.add((e.get("date"), e.get("time")))
        if e.get("pillar") not in pillars:
            out.append(f"{who}: pillar {e.get('pillar')!r} is not one of "
                       f"{', '.join(sorted(pillars))}")
        thread = e.get("thread")
        if not isinstance(thread, list) or not thread:
            out.append(f"{who}: thread is empty")
            thread = []
        for n, t in enumerate(thread, 1):
            if not isinstance(t, str) or not t.strip():
                out.append(f"{who}: tweet {n} is empty")
                continue
            if len(t) > TWEET_MAX:
                out.append(f"{who}: tweet {n} is {len(t)} characters, over {TWEET_MAX}")
            if VINLIKE.search(t):
                out.append(f"{who}: tweet {n} contains a 17-character VIN-shaped token")
        if e.get("media") and not e.get("alt"):
            out.append(f"{who}: has media but no alt text")
        tags = e.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.split()
        for t in tags:
            if not isinstance(t, str) or not t.startswith("#") or len(t) < 2:
                out.append(f"{who}: hashtag {t!r} does not start with #")
    for day, n in sorted(per_day.items()):
        if n > PER_DAY_MAX:
            out.append(f"{day}: {n} posts, more than {PER_DAY_MAX}")
    return out


def main():
    print("\n  The launch calendar\n")
    # THE CALENDAR IS NOT IN THE PUBLIC TREE. It is a month of unpublished
    # posts and the notes behind them, which is the owner's business and not
    # part of the tool -- so it lives outside the repository and this checks it
    # wherever it is found. A checkout without one is not a failure; a check
    # that silently passes on a missing file would be.
    if not os.path.exists(CALENDAR):
        print(f"    (no calendar at {os.path.relpath(CALENDAR, ROOT)} — skipping.\n"
              f"     This is expected in a public checkout: the launch calendar\n"
              f"     is kept outside the repository.)\n")
        return 0
    with open(CALENDAR, encoding="utf-8") as f:
        doc = json.load(f)
    probs = problems(doc)
    for p in probs:
        print(f"      {p}")
    check("launch/calendar.json has no problems", not probs)
    posts = doc["posts"]
    ok(f"{len(posts)} slots, {sum(len(e['thread']) for e in posts)} posts, "
       f"{len({e['date'] for e in posts})} days")
    check("the reveal day has a thread first",
          any(e["date"] == str(REVEAL) and len(e["thread"]) > 1
              for e in posts))

    # Deliberately break one, three ways, and expect it named each time.
    broken = json.loads(json.dumps(doc))
    broken["posts"][0]["thread"][0] = "x" * (TWEET_MAX + 1)
    check("an over-long tweet fails naming the entry",
          any(broken["posts"][0]["id"] in p and "over 280" in p for p in problems(broken)))
    broken = json.loads(json.dumps(doc))
    broken["posts"][1]["thread"].append("this car is JHMZF1D44FS001835 by the way")
    check("a VIN in a tweet fails naming the entry",
          any(broken["posts"][1]["id"] in p and "VIN" in p for p in problems(broken)))
    broken = json.loads(json.dumps(doc))
    day = broken["posts"][0]["date"]
    for k in range(PER_DAY_MAX + 1):
        broken["posts"].append({"id": f"too-many-{k}", "date": day, "time": f"0{k}:07",
                                "pillar": broken["posts"][0]["pillar"],
                                "thread": ["one more"]})
    check("a fifth post on a day fails naming the day",
          any(day in p and "more than" in p for p in problems(broken)))

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  the calendar holds\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
