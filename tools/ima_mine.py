#!/usr/bin/env python3
"""Mine saved bus captures for the IMA pack's charge, current and voltage.

Nobody has found where the hybrid pack's figures live on this car's bus. This
looks. For every frame ID, every byte and every adjacent byte pair (big-endian,
a 16-bit value) it builds a series over time, lines that up against what the
dashboard recorded in the same seconds, and ranks the series by how closely
they move together. A high score is a lead for a person to check against the
car, never a finding: every result is written with state "candidate", and the
verdict never reads one.

Read-only. It reads capture files and the samples database, and nothing here
opens an adapter or can send anything to the car.

  python3 tools/ima_mine.py [--captures DIR] [--out DIR] [--top N] [--db FILE]

The three targets are proxies, and say so:
  charge   the samples' `soc` (PID 0x5B, percent)
  current  the discrete derivative of `soc` over 5 s
  voltage  `throttle` with the sign inverted, as a stand-in for sag under load

Method: 1 s bins over the overlap of a capture and the samples. In a bin, the
last frame value, and the sample nearest within 2 s. A series needs at least 60
paired bins and 3 distinct values or it is skipped. Score is |Pearson r|.

The recorder cannot poll PIDs while it listens to the bus, so a drive's
captures usually fall in gaps between samples. Such a capture is bracketed
instead: the last charge reading before it and the first after it, each within
30 s, are joined by a straight line across its bins. Charge drifts slowly, so
that holds for charge only; current and voltage still need real overlap. Each
candidate says how many of its bins were bracketed.

A bracketed capture is one straight line, so however many seconds it spans it
is ONE independent point against charge, not one per second. A candidate
resting on bracketing alone therefore needs at least 20 captures, and every
candidate says how many captures it rests on and whether its value ever moved
inside one: a value constant within every capture that tracks charge across
them is as likely to be anything else that drifts over a drive.
"""

import argparse
import glob
import json
import bisect
import math
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import records  # noqa: E402  (the database's path and its read-only handle)

MIN_BINS = 60
MIN_DISTINCT = 3
NEAREST = 2.0       # seconds: how far a sample may be from a bin and still count
DERIV_SPAN = 5      # seconds, for the current proxy
BRACKET_MAX = 30.0  # seconds: the furthest a bracketing charge reading may be
MIN_BRACKETED_CAPTURES = 20   # independent captures behind a bracketing-only lead
TARGETS = ("charge", "current", "voltage")


def default_captures():
    return os.path.join(records.STATE, "captures")


def pearson(xs, ys):
    """Pearson r, or None when either side does not vary."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def load_captures(directory):
    """(name, started, frames) for every readable capture; frames is [] when it
    had no raw. Unreadable files are skipped, never raised."""
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            started = float(doc["started"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        frames = []
        for fr in doc.get("raw") or []:
            try:
                data = bytes.fromhex(fr["data"])
                frames.append((started + float(fr["t"]), str(fr["id"]).upper(), data))
            except (ValueError, KeyError, TypeError):
                continue
        out.append((os.path.basename(path), started, frames))
    return out


def load_samples(db_path):
    """[(t, soc, throttle)] sorted by t, or [] if there is nothing to read."""
    try:
        if db_path:
            if not os.path.exists(db_path):
                return []
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        else:
            db = records.connect()
            if db is None:
                return []
        rows = db.execute("SELECT t, soc, throttle FROM samples ORDER BY t").fetchall()
        db.close()
    except sqlite3.Error:
        return []
    return [(float(r[0]), r[1], r[2]) for r in rows if r[0] is not None]


def nearest_sample(times, t):
    """Index of the sample nearest t within NEAREST seconds, or None."""
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    best = None
    for i in (lo - 1, lo):
        if 0 <= i < len(times) and abs(times[i] - t) <= NEAREST:
            if best is None or abs(times[i] - t) < abs(times[best] - t):
                best = i
    return best


def target_bins(samples, t0, t1):
    """{bin: {"charge", "current", "voltage"}} for the bins t0..t1-1."""
    times = [s[0] for s in samples]
    soc = {}
    thr = {}
    for b in range(int(t0), int(t1)):
        i = nearest_sample(times, b + 0.5)
        if i is None:
            continue
        if samples[i][1] is not None:
            soc[b] = float(samples[i][1])
        if samples[i][2] is not None:
            thr[b] = float(samples[i][2])
    out = {}
    for b in range(int(t0), int(t1)):
        row = {}
        if b in soc:
            row["charge"] = soc[b]
            if b - DERIV_SPAN in soc:
                row["current"] = (soc[b] - soc[b - DERIV_SPAN]) / DERIV_SPAN
        if b in thr:
            row["voltage"] = -thr[b]
        if row:
            out[b] = row
    return out


def bracket_bins(socs, t0, t1):
    """{bin: {"charge"}} for a capture that falls wholly between two charge
    readings, each within BRACKET_MAX of it, interpolated in a straight line.
    socs is [(t, soc)] sorted, soc never None. Empty when not bracketed."""
    times = [s[0] for s in socs]
    i = bisect.bisect_left(times, t0) - 1
    j = bisect.bisect_right(times, t1)
    if i < 0 or j >= len(socs) or j != i + 1:
        return {}
    (ta, ca), (tb, cb) = socs[i], socs[j]
    if t0 - ta > BRACKET_MAX or tb - t1 > BRACKET_MAX:
        return {}
    return {b: {"charge": ca + (cb - ca) * (b + 0.5 - ta) / (tb - ta)}
            for b in range(int(t0), int(t1))}


def series_for_capture(frames, t0, t1):
    """{(id, (i,) or (i, i+1)): {bin: value}}, last frame in each bin wins."""
    series = {}
    for t, fid, data in frames:
        b = int(t)
        if b < int(t0) or b >= int(t1):
            continue
        for i in range(len(data)):
            series.setdefault((fid, (i,)), {})[b] = data[i]
            if i + 1 < len(data):
                series.setdefault((fid, (i, i + 1)), {})[b] = data[i] * 256 + data[i + 1]
    return series


def mine(captures, samples):
    """Returns (candidates, stats). candidates is every scored series."""
    stats = {"captures": len(captures), "with_raw": 0, "raw_frames": 0,
             "bus_seconds": 0.0, "overlap_captures": 0, "overlap_bins": 0,
             "bracketed_captures": 0, "bracketed_bins": 0}
    socs = [(s[0], float(s[1])) for s in samples if s[1] is not None]
    # (id, bytes, target) -> ([x...], [y...], bracketed count) across captures
    pairs = {}
    for cap_name, _started, frames in captures:
        if not frames:
            continue
        stats["with_raw"] += 1
        stats["raw_frames"] += len(frames)
        ft0 = min(f[0] for f in frames)
        ft1 = max(f[0] for f in frames)
        stats["bus_seconds"] += ft1 - ft0
        if not samples:
            continue
        t0 = max(ft0, samples[0][0])
        t1 = min(ft1, samples[-1][0])
        tgt = target_bins(samples, t0, t1) if t1 > t0 else {}
        bracketed = False
        if tgt:
            stats["overlap_captures"] += 1
            stats["overlap_bins"] += len(tgt)
        else:
            t0, t1 = ft0, ft1
            tgt = bracket_bins(socs, t0, t1)
            if not tgt:
                continue
            bracketed = True
            stats["bracketed_captures"] += 1
            stats["bracketed_bins"] += len(tgt)
        for (fid, idx), vals in series_for_capture(frames, t0, t1).items():
            for b, v in vals.items():
                row = tgt.get(b)
                if not row:
                    continue
                for name, y in row.items():
                    acc = pairs.setdefault((fid, idx, name), [[], [], 0, {}])
                    acc[0].append(v)
                    acc[1].append(y)
                    acc[2] += bracketed
                    acc[3].setdefault(cap_name, set()).add(v)
    cands = []
    for (fid, idx, name), (xs, ys, nbr, per_cap) in pairs.items():
        if len(xs) < MIN_BINS or len(set(xs)) < MIN_DISTINCT:
            continue
        if nbr == len(xs) and len(per_cap) < MIN_BRACKETED_CAPTURES:
            continue
        r = pearson(xs, ys)
        if r is None:
            continue
        cands.append({"id": fid, "bytes": list(idx), "target": name,
                      "r": round(abs(r), 6), "sign": 1 if r >= 0 else -1,
                      "bins": len(xs), "bracketed": nbr, "captures": len(per_cap),
                      "moves_within": any(len(vs) > 1 for vs in per_cap.values()),
                      "state": "candidate"})
    return cands, stats


def top_per_target(cands, n):
    out = []
    for name in TARGETS:
        rows = [c for c in cands if c["target"] == name]
        # Ties go to the single byte, then the earlier one: a constant
        # neighbour makes a pair score exactly what its live byte does.
        rows.sort(key=lambda c: (-c["r"], len(c["bytes"]), c["bytes"], c["id"]))
        out.extend(rows[:n])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--captures", default=default_captures())
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--db", default=None, help="samples database (default: the car's)")
    a = ap.parse_args(argv)

    captures = load_captures(a.captures)
    samples = load_samples(a.db)
    cands, st = mine(captures, samples)
    result = top_per_target(cands, a.top)

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)

    print(f"{st['captures']} captures, {st['with_raw']} had raw frames "
          f"({st['raw_frames']} frames, {st['bus_seconds']:.0f} s of bus); "
          f"{st['overlap_captures']} overlap the samples ({st['overlap_bins']} bins), "
          f"{st['bracketed_captures']} are bracketed by charge readings "
          f"({st['bracketed_bins']} bins, charge only).")
    if not st["overlap_bins"] and not st["bracketed_bins"]:
        print(f"no overlap between the raw frames and the samples: {st['raw_frames']} "
              f"frames exist, over {st['bus_seconds']:.0f} s of bus, and nothing to line "
              "them up against. More drives with the adapter in will feed this.")
        return 0
    if not result:
        print("frames lined up, but no series reached 60 bins and 3 distinct values"
              + (f", or {MIN_BRACKETED_CAPTURES} bracketed captures"
                 if st["bracketed_captures"] else "") + ". "
              "More captures, and longer ones (a faster adapter link), will feed this.")
        return 0
    for name in TARGETS:
        rows = [c for c in result if c["target"] == name]
        print(f"\n{name}:")
        for c in rows:
            b = "+".join(str(i) for i in c["bytes"])
            print(f"  {c['id']:>8} byte {b:<5} r={c['r']:.3f} "
                  f"({'+' if c['sign'] > 0 else '-'}) bins={c['bins']} "
                  f"bracketed={c['bracketed']} captures={c['captures']}"
                  f"{'' if c['moves_within'] else ' flat-within'}  candidate")
    print("\nCandidates only: check each against the car before trusting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
