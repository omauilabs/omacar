"""Which of the three modes the car is in, and whether we can yet tell.

ECON, NORMAL and SPORT change four things on a CR-Z: the drive-by-wire throttle
map, how hard IMA assists, the weight of the electric steering, and the climate
control. Honda's own material describes ECON's throttle curve as logarithmic
from around a quarter pedal where NORMAL is linear, and SPORT as sharper
response with more motor assist.

WHY THIS IS NOT SIMPLY READ OFF THE BUS.

Nobody has published a CAN identifier for the mode selector on this car. The
community work that got furthest wired a CANBus Triple to the internal
F-CAN/IMA-CAN rather than the OBD-II port, and Honda's extended PIDs are
proprietary. The mode may not cross the gateway the port exposes at all. That
question is still open and is worth answering; this module is the other track,
and it works whether or not the bit is ever found.

THE OTHER TRACK. If ECON and SPORT really do remap the pedal, then two drives
at the same speed in different modes put a different ENGINE_LOAD under the same
THROTTLE_POS -- and load and throttle are ordinary polled PIDs that have never
once failed on this car, on the path that kept 4,367 samples across 126 km on
the day every raw capture stalled.

WHAT THE DATA SAID WHEN THIS WAS WRITTEN, AND WHY THE MODULE REFUSES.

There are four labelled windows on this car, from 7 September: econ, normal,
sport, econ again. All four were recorded PARKED, at a closed throttle -- speed
0.0, throttle pinned at exactly 12.9% in every sample of all four, RPM around
750, load around 26%. The modes are statistically identical across them, and
they have to be: at a closed throttle there is no pedal input to remap and no
assist being asked for. The difference between these modes is not a property of
the car, it is a property of the car BEING DRIVEN.

So evidence() reports that, in those words, rather than fitting a boundary to
sixty-five idle samples and returning a confident answer. A classifier trained
on data that cannot contain the signal is not a weak classifier. It is a
number-shaped opinion, and this project has already lost one drive to a status
file that said "capturing" when nothing was.

WHAT IT NEEDS. Labelled samples taken under throttle: hold a mode, drive
normally for a few minutes, mark it, change mode, repeat. mark() is the cheap
half of that and is meant to be reachable from the drive screen with one thumb,
because the reason there is no such data yet is that collecting it previously
meant typing in a moving car.
"""

import json
import os
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garage    # noqa: E402
import records   # noqa: E402

MODES = ("econ", "normal", "sport")
LOG = os.path.join(records.STATE, "drive-modes.json")

# A pedal that has not moved off its stop tells us nothing, and on this car the
# stop reads 12.9% rather than zero -- THROTTLE_POS reports absolute plate
# angle, so "closed" is an offset and not a nought. The floor is computed from
# the data rather than hardcoded, because it is a property of a throttle body
# and this tool is not only ever going to see one car.
REST_MARGIN = 2.0

# Below this there is no point reporting a comparison at all. It is not a
# statistical threshold, it is an honesty one: a mean of four samples put
# beside a mean of six invites a conclusion neither can carry.
MIN_PER_MODE = 40


def _load():
    try:
        with open(LOG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    marks = doc.get("marks") if isinstance(doc, dict) else doc
    return [m for m in (marks or [])
            if isinstance(m, dict) and m.get("mode") in MODES and m.get("at")]


def mark(mode, at=None, vehicle=None):
    """Record that the car is in `mode` from now until the next mark."""
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        return None, f"mode must be one of {', '.join(MODES)}"
    marks = _load()
    entry = {"at": float(at or time.time()), "mode": mode,
             "vehicle": vehicle or garage.current()}
    marks.append(entry)
    marks.sort(key=lambda m: m["at"])
    os.makedirs(records.STATE, exist_ok=True)
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"marks": marks[-2000:]}, f, indent=1)
    os.replace(tmp, LOG)
    return entry, None


def current(now=None, db=None):
    """The mode in force RIGHT NOW, or None.

    Not simply the last mark. A mark expires the same way it does for
    evidence() — at a gap in sampling that says the car was switched off, or
    after SESSION_CAP — and the vehicle bar puts this on a screen that never
    goes away. Returning the last thing anybody ever typed would have that bar
    claiming ECON on a car that has since been driven for nine days in modes
    nobody wrote down, which is the same defect as a window that never ends,
    reached from the other side.
    """
    marks = _load()
    if not marks:
        return None
    now = float(now or time.time())
    past = [m for m in marks if m["at"] <= now]
    if not past:
        return None
    last = past[-1]
    for mode, t0, t1 in windows([last], db):
        if t0 <= now <= t1:
            return last
    return None


# A MARK DOES NOT LAST FOREVER, and letting it was the first bug this module
# found -- in itself. With each window running until the next mark, the final
# "normal" mark from 8 September was still in force on 16 September, and it
# quietly labelled all 4,367 samples of a 126 km drive home as NORMAL. Nobody
# had said that. The car was driven in whatever it was driven in.
#
# So a window ends at the next mark, at a gap in sampling that says the car was
# switched off, or after SESSION_CAP -- whichever comes first. Whether a CR-Z
# remembers its mode across an ignition cycle is not something this file knows,
# and a label is not the place to assume it.
SESSION_GAP = 600.0      # ten minutes without a sample: that drive ended
SESSION_CAP = 5400.0     # and no single mark speaks for more than 90 minutes


def windows(marks=None, db=None):
    """[(mode, from, to)] -- each mark runs until it stops being true."""
    marks = marks if marks is not None else _load()
    out = []
    for i, m in enumerate(marks):
        hard = marks[i + 1]["at"] if i + 1 < len(marks) else time.time()
        end = min(hard, m["at"] + SESSION_CAP)
        rows = _samples(m["at"], end, db)
        # Walk forward to the first silence long enough to mean the ignition
        # went off, and end the window there.
        last = m["at"]
        for r in rows:
            if r["t"] - last > SESSION_GAP:
                end = last
                break
            last = r["t"]
        else:
            if rows and end - last > SESSION_GAP:
                end = last
        if end > m["at"]:
            out.append((m["mode"], m["at"], end))
    return out


def _samples(t0, t1, db=None):
    close = db is None
    db = db or sqlite3.connect(garage.db_path())
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(
            "SELECT t, speed, rpm, throttle, load, maf, soc FROM samples "
            "WHERE t BETWEEN ? AND ? ORDER BY t", (t0, t1))]
    except sqlite3.Error:
        return []
    finally:
        if close:
            db.close()


def evidence(db=None):
    """Can the labelled data tell these modes apart? Usually: not yet.

    Returns a report rather than a verdict, because the interesting part is
    almost always WHY there is no verdict.
    """
    marks = _load()
    out = {"marks": len(marks), "modes": {}, "usable": False, "why": ""}
    if not marks:
        out["why"] = ("nothing has been marked. Hold a mode, drive for a few "
                      "minutes, and mark it: omacar drivemode econ")
        return out

    per = {}
    for mode, t0, t1 in windows(marks, db):
        rows = _samples(t0, t1, db)
        per.setdefault(mode, []).extend(rows)

    # The rest position, measured rather than assumed.
    everything = [r["throttle"] for rs in per.values() for r in rs
                  if r.get("throttle") is not None]
    rest = min(everything) if everything else None
    out["rest_throttle"] = rest

    for mode, rows in sorted(per.items()):
        moving = [r for r in rows
                  if (r.get("throttle") or 0) > (rest or 0) + REST_MARGIN
                  and (r.get("speed") or 0) > 3]
        rec = {"samples": len(rows), "under_throttle": len(moving)}
        if moving:
            ratios = [r["load"] / r["throttle"] for r in moving
                      if r.get("load") and r.get("throttle")]
            if ratios:
                rec["load_per_throttle"] = round(statistics.mean(ratios), 3)
                if len(ratios) > 1:
                    rec["spread"] = round(statistics.pstdev(ratios), 3)
        out["modes"][mode] = rec

    thin = [m for m, r in out["modes"].items()
            if r["under_throttle"] < MIN_PER_MODE]
    if len(out["modes"]) < 2:
        out["why"] = "only one mode has ever been marked; there is nothing to compare"
    elif thin:
        # THE ONE THAT MATTERS, and the state this car is actually in.
        out["why"] = (
            "every marked window is at a closed throttle. These modes change "
            "how the pedal is mapped and how hard IMA assists — parked, there "
            "is no pedal input to map and no assist being asked for, so they "
            "are identical by construction. Marking needs to happen while "
            "driving: " + ", ".join(f"{m} has {out['modes'][m]['under_throttle']}"
                                    for m in thin) + f" samples under throttle, "
            f"and {MIN_PER_MODE} is the floor for saying anything at all")
    else:
        out["usable"] = True
        out["why"] = "there is enough to compare"
    return out


def from_captures():
    """Adopt the marks that were written as capture notes.

    Before this module existed the only way to say which mode the car was in
    was to type it into a capture's --note, and 44 captures carry one. Those
    are real labels with real timestamps and there is no reason to make
    somebody re-record them -- but they are adopted rather than trusted
    silently: what comes back says how many, and evidence() will still refuse
    them if they cannot answer the question, which for this car's four is
    exactly what happens.
    """
    import glob
    found = []
    for path in sorted(glob.glob(os.path.join(records.STATE, "captures", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        note = str(doc.get("note") or "").strip().lower()
        hit = [m for m in MODES if m in note]
        # "econ again" is one mode; "econ then sport" is a note this cannot
        # read, and guessing which half came first would invent a label.
        if len(hit) != 1 or not doc.get("started"):
            continue
        found.append({"at": float(doc["started"]), "mode": hit[0],
                      "vehicle": doc.get("vehicle") or garage.current(),
                      "from": os.path.basename(path)[:-5]})
    existing = {(round(m["at"], 1), m["mode"]) for m in _load()}
    fresh = [m for m in found if (round(m["at"], 1), m["mode"]) not in existing]
    if fresh:
        marks = _load() + fresh
        marks.sort(key=lambda m: m["at"])
        os.makedirs(records.STATE, exist_ok=True)
        tmp = LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"marks": marks[-2000:]}, f, indent=1)
        os.replace(tmp, LOG)
    return len(fresh), len(found)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv[0] if argv else "status"

    if verb in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    if verb in MODES:
        entry, err = mark(verb)
        if err:
            print(f"  {err}", file=sys.stderr)
            return 2
        print(f"  marked {entry['mode']} at "
              f"{time.strftime('%H:%M:%S', time.localtime(entry['at']))}")
        return 0

    if verb == "from-captures":
        fresh, total = from_captures()
        print(f"  {fresh} new mark(s) adopted from {total} capture note(s)")
        return 0

    if verb == "evidence":
        rep = evidence()
        print()
        print(f"  {rep['marks']} mark(s)")
        for mode, r in rep["modes"].items():
            extra = (f", load/throttle {r['load_per_throttle']}"
                     if r.get("load_per_throttle") else "")
            print(f"    {mode:<8} {r['samples']:5} samples, "
                  f"{r['under_throttle']:4} under throttle{extra}")
        if rep.get("rest_throttle") is not None:
            print(f"\n  a closed throttle on this car reads "
                  f"{rep['rest_throttle']}%")
        print(f"\n  {'ENOUGH' if rep['usable'] else 'NOT YET'}: {rep['why']}\n")
        return 0 if rep["usable"] else 1

    cur = current()
    if cur:
        print(f"  {cur['mode']} since "
              f"{time.strftime('%d %b %H:%M', time.localtime(cur['at']))}")
    else:
        print("  nobody has said which mode the car is in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
