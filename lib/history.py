"""Turning documents into a service history.

WHAT WAS MISSING.

The service book tracked only `last_km` and `last_at` per item — when the oil
was last changed, overwritten each time. That answers "am I due?" and cannot
answer "what has been done to this car", which is the question a buyer asks, a
warranty claim needs, and anybody deciding whether a 190,000-mile car has been
looked after. So this adds an append-only log alongside the schedule: the
schedule stays the fast answer, the log is the record.

WHY MATCHING IS THE DANGEROUS PART.

A receipt says "TIRE ROTATION" or "Rotate & balance 4 tires" or "R&B tires".
The book says "Tire rotation". Joining those is the whole feature, and getting
it wrong writes a maintenance record that is false — a car that looks serviced
and is not. That is worse than no record at all, because a service history is
exactly the kind of document people later trust absolutely.

So nothing is written automatically on a guess:

  - An exact or synonym match is offered as `confident`.
  - A fuzzy match above the floor is offered as `likely`, with its score.
  - Anything weaker is offered as `unmatched`, showing the receipt's own words
    so a person can decide.

All three are PROPOSALS. Applying them is a separate, deliberate act, and every
entry records the document it came from — so a history entry that looks wrong
can be traced to the page it was read off, and withdrawn.
"""

import difflib
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import records  # noqa: E402

# Confidence floors. Deliberately high: a missed match costs one click, and a
# wrong one costs the truthfulness of the whole record.
LIKELY = 0.72
CONFIDENT = 0.90

# What receipts actually say, mapped to what the book calls it. Written from
# the vocabulary of real invoices rather than from the schedule's own names --
# no garage prints "Engine oil & filter" on a bill.
SYNONYMS = {
    "engine oil": "Engine oil & filter",
    "oil change": "Engine oil & filter",
    "oil and filter": "Engine oil & filter",
    "oil filter": "Engine oil & filter",
    "lube oil filter": "Engine oil & filter",
    "signature service oil change": "Engine oil & filter",
    "full synthetic oil change": "Engine oil & filter",
    "tire rotation": "Tire rotation",
    "tyre rotation": "Tire rotation",
    "rotate tires": "Tire rotation",
    "rotate and balance": "Tire rotation",
    "air filter": "Air filter",
    "engine air filter": "Air filter",
    "cabin filter": "Cabin filter",
    "cabin air filter": "Cabin filter",
    "pollen filter": "Cabin filter",
    "brake fluid": "Brake fluid",
    "brake fluid flush": "Brake fluid",
    "coolant": "Coolant",
    "antifreeze": "Coolant",
    "coolant flush": "Coolant",
    "radiator flush": "Coolant",
    "transmission fluid": "Transmission fluid",
    "atf": "Transmission fluid",
    "cvt fluid": "Transmission fluid",
    "spark plugs": "Spark plugs",
    "plugs": "Spark plugs",
}

NOISE = re.compile(r"\b(replace[d]?|replacement|change[d]?|service|new|install"
                   r"|installed|check|inspect|inspected|qty|ea|each|labor"
                   r"|labour|parts?|and|the|with|for)\b")


def norm(text):
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = NOISE.sub(" ", t)
    return " ".join(t.split())


# The synonym table normalised the same way its inputs are.
#
# It was compared raw against normalised phrases, so "signature service oil
# change" -- a key in the table -- normalised to "signature oil" and matched
# nothing, because norm() strips "service" and "change" as noise. A lookup
# table has to live in the same space as its queries.
NORM_SYNONYMS = None


def _syn():
    global NORM_SYNONYMS
    if NORM_SYNONYMS is None:
        NORM_SYNONYMS = {}
        for k, v in SYNONYMS.items():
            nk = norm(k)
            if nk:
                NORM_SYNONYMS[nk] = v
    return NORM_SYNONYMS


def open_db():
    db = sqlite3.connect(records.DB, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS service_log (
        id INTEGER PRIMARY KEY,
        item TEXT, km REAL, at REAL, note TEXT,
        source_doc INTEGER, source_text TEXT, confidence TEXT,
        added REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS service_log_at ON service_log(at)")
    db.commit()
    return db


def book_items():
    import book
    db = book.open_db()
    try:
        book.ensure_schedule(db)
        return [r[0] for r in db.execute("SELECT item FROM service ORDER BY item")]
    finally:
        db.close()


def match(phrase, items=None):
    """(item, score, how) for one line off a receipt.

    `how` is part of the answer, not decoration: "synonym" and "fuzzy 0.78" are
    different claims, and the UI shows which one it is so somebody can weigh a
    proposal rather than just accept it.
    """
    items = items or book_items()
    n = norm(phrase)
    if not n:
        return None, 0.0, "empty"

    # Exact, on the normalised form.
    for it in items:
        if norm(it) == n:
            return it, 1.0, "exact"

    # Known invoice vocabulary, in normalised space.
    syn = _syn()
    if n in syn and syn[n] in items:
        return syn[n], 0.97, "synonym"
    # Longest key first, so "cabin air filter" wins over "air filter" on a
    # phrase containing both.
    for key in sorted(syn, key=len, reverse=True):
        target = syn[key]
        if target in items and (key in n or n in key):
            return target, 0.93, f"synonym ({key})"

    # Fuzzy, as a last resort and reported as such.
    best, score = None, 0.0
    for it in items:
        r = difflib.SequenceMatcher(None, n, norm(it)).ratio()
        if r > score:
            best, score = it, r
    return best, round(score, 3), f"fuzzy {score:.2f}"


def propose(doc):
    """What a parsed document says was done, as proposals.

    Returns [] when the document has not been read yet -- proposals come from
    extraction, and extraction is a deliberate act.
    """
    ex = doc.get("extracted") or {}
    if not ex or ex.get("unreadable"):
        return []
    items = book_items()

    # Prefer the advisor's normalised list; fall back to line items, which are
    # messier but sometimes carry work the summary missed.
    phrases = list(ex.get("service_items") or [])
    if not phrases:
        phrases = [i.get("description", "") for i in (ex.get("items") or [])]

    when = None
    if doc.get("doc_date"):
        try:
            when = time.mktime(time.strptime(doc["doc_date"][:10], "%Y-%m-%d"))
        except ValueError:
            when = None
    km = doc.get("odometer")

    out, seen = [], set()
    for p in phrases:
        item, score, how = match(p, items)
        if item and item in seen:
            continue
        band = ("confident" if score >= CONFIDENT
                else "likely" if score >= LIKELY else "unmatched")
        if band != "unmatched":
            seen.add(item)
        out.append({
            "source_text": p,
            "item": item if band != "unmatched" else None,
            "score": score, "how": how, "band": band,
            "km": km, "at": when,
            "doc_id": doc.get("id"), "doc_title": doc.get("title"),
        })
    return out


def already_logged(doc_id):
    db = open_db()
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM service_log WHERE source_doc=?", (int(doc_id),))]
    finally:
        db.close()


def apply(entries):
    """Write proposals into the history AND update the schedule.

    Both, deliberately. The log is the record of what happened; the schedule is
    the fast answer to "am I due". Writing only the log would leave the service
    screen still counting from nothing, which is the bug this feature exists to
    fix.
    """
    import book
    db = open_db()
    written = []
    try:
        for e in entries:
            if not e.get("item"):
                continue
            # A document applied twice must not double-count. The pair
            # (document, item) is the natural key: one receipt records one oil
            # change, however many times somebody presses the button.
            if e.get("doc_id") is not None:
                dup = db.execute(
                    "SELECT id FROM service_log WHERE source_doc=? AND item=?",
                    (int(e["doc_id"]), e["item"])).fetchone()
                if dup:
                    continue
            db.execute(
                "INSERT INTO service_log (item, km, at, note, source_doc,"
                " source_text, confidence, added) VALUES (?,?,?,?,?,?,?,?)",
                (e["item"], e.get("km"), e.get("at") or time.time(),
                 e.get("note") or "", e.get("doc_id"), e.get("source_text"),
                 e.get("band"), time.time()))
            written.append(e["item"])
        db.commit()
    finally:
        db.close()

    # Update the schedule from the NEWEST entry per item, which is not
    # necessarily the one just added -- somebody filing an old receipt should
    # not make the car look freshly serviced.
    for item in set(written):
        row = newest(item)
        if row:
            book.log_service(item, km=row.get("km"), when=row.get("at"),
                             note=f"from document #{row.get('source_doc')}")
    return written


def newest(item):
    db = open_db()
    try:
        r = db.execute("SELECT * FROM service_log WHERE item=? "
                       "ORDER BY at DESC LIMIT 1", (item,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def timeline(n=200):
    db = open_db()
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM service_log ORDER BY at DESC, id DESC LIMIT ?", (n,))]
    finally:
        db.close()


def withdraw(entry_id):
    """Remove one history entry. See the note about traceability."""
    db = open_db()
    try:
        db.execute("DELETE FROM service_log WHERE id=?", (int(entry_id),))
        db.commit()
        return True
    finally:
        db.close()
