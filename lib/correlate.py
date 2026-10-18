"""Validate a candidate PID by watching what it tracks.

THE IDEA.

A sweep can prove that an identifier answers and that its bytes move. It cannot
say what they mean. Traditionally somebody works that out by staring at a gauge
and wiggling the throttle -- which does not scale, and is why manufacturer
coverage costs what it costs.

But we already record channels we trust: road speed, engine RPM, coolant
temperature, load, intake temperature, both fuel trims. If an unknown byte
moves in lockstep with one of those across several separate drives, that is
evidence about what it is, gathered without anybody looking at anything.

WHY CORRELATION ALONE IS NOT ENOUGH, AND WHAT THIS DOES INSTEAD.

Almost everything in a running engine correlates with almost everything else.
Coolant temperature and intake temperature both climb from cold. RPM and road
speed track each other in any fixed gear. A byte that scores r = 0.97 against
coolant will often score 0.96 against intake, and calling it "coolant, proven"
would be exactly the confident-sounding wrong answer this project exists to
avoid.

So the rule here is deliberately not "highest correlation wins":

  1. The best correlate must be STRONG in absolute terms.
  2. It must beat the runner-up by a MARGIN. Two channels that fit almost
     equally well mean the data cannot distinguish them, and the honest output
     is "ambiguous, here are both" rather than a coin toss with a decimal point.
  3. It must hold across SEVERAL SEPARATE DRIVES. One drive can produce a
     coincidence -- a byte that happens to ramp while the engine warms up looks
     like a temperature until you see a drive where it does not.
  4. The residual after fitting scale and offset must be small, because a
     linear relationship is the claim being made.

Failing any of those is a result, not an error. `ambiguous` is a legitimate
outcome and gets reported as one.
"""

import math

# Channels we already trust, because they are standard OBD-II PIDs with defined
# units. These are the yardsticks; nothing here validates one against another.
TRUSTED = {
    "speed":    "road speed (km/h)",
    "rpm":      "engine speed",
    "coolant":  "coolant temperature (°C)",
    "intake":   "intake air temperature (°C)",
    "load":     "calculated engine load (%)",
    "throttle": "throttle position (%)",
    "maf":      "mass air flow (g/s)",
    "stft":     "short-term fuel trim (%)",
    "ltft":     "long-term fuel trim (%)",
}

# Thresholds. Deliberately strict: a wrong validation propagates to everybody
# who pulls the profile, while a missed one costs only another drive.
MIN_R = 0.90          # absolute strength of the best fit
MIN_MARGIN = 0.06     # how far it must beat the runner-up
MIN_DRIVES = 3        # separate drives it must hold across
MIN_POINTS = 120      # samples per drive, below which noise dominates


# ---- reading a payload as a number ------------------------------------------

def decodings(payload_hex):
    """Every plausible numeric reading of a response payload.

    A candidate's meaning is not just which identifier answered but how its
    bytes are packed, and there is no way to know in advance. So every
    reasonable packing is offered as a separate hypothesis and the correlation
    decides between them -- which is the same discipline as the rest of this
    file: propose, then let evidence choose.
    """
    try:
        raw = bytes.fromhex(payload_hex)
    except (ValueError, TypeError):
        return {}
    out = {}
    for i, b in enumerate(raw):
        out[f"byte{i}"] = float(b)
        # Signed, for anything centred on zero (fuel trims, timing).
        out[f"byte{i}_signed"] = float(b - 256 if b > 127 else b)
    for i in range(len(raw) - 1):
        hi, lo = raw[i], raw[i + 1]
        out[f"word{i}"] = float(hi * 256 + lo)
        v = hi * 256 + lo
        out[f"word{i}_signed"] = float(v - 65536 if v > 32767 else v)
    return out


# ---- statistics -------------------------------------------------------------

def pearson(xs, ys):
    """Correlation coefficient, or None when it is undefined.

    Returns None rather than 0 for a constant series. Zero would mean "no
    relationship", which is a claim; a constant supports no claim at all.
    """
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def fit(xs, ys):
    """Least-squares y = a*x + b. Returns (a, b, rmse) or None."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    rmse = math.sqrt(sum((a * x + b - y) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, rmse


def spread(vs):
    """How much a channel actually moved. A yardstick that sat still tells us
    nothing, however well something correlates with it."""
    if not vs:
        return 0.0
    return max(vs) - min(vs)


# ---- the decision -----------------------------------------------------------

def assess(drives, decoding_name=None):
    """Decide what an unknown series tracks.

    `drives` is a list of per-drive dicts:
        {"candidate": [floats], "channels": {"speed": [...], ...}}
    aligned index for index. Returns a verdict dict.
    """
    usable = [d for d in drives
              if len(d.get("candidate") or []) >= MIN_POINTS]
    if len(usable) < MIN_DRIVES:
        return {"verdict": "insufficient",
                "why": f"{len(usable)} usable drive(s), need {MIN_DRIVES} of "
                       f"at least {MIN_POINTS} samples",
                "decoding": decoding_name}

    # Score every channel on every drive separately, then take the WEAKEST
    # per channel. A channel is only a real explanation if it holds on every
    # drive -- averaging would let one excellent drive carry two poor ones.
    per_channel = {}
    for ch in TRUSTED:
        worst = None
        for d in usable:
            ys = (d.get("channels") or {}).get(ch)
            xs = d.get("candidate")
            if not ys or len(ys) != len(xs):
                worst = None
                break
            if spread(ys) <= 0:
                worst = None          # yardstick never moved on this drive
                break
            r = pearson(xs, ys)
            if r is None:
                worst = None
                break
            r = abs(r)
            worst = r if worst is None else min(worst, r)
        if worst is not None:
            per_channel[ch] = worst

    if not per_channel:
        return {"verdict": "no-signal",
                "why": "the candidate did not vary, or no trusted channel did",
                "decoding": decoding_name}

    ranked = sorted(per_channel.items(), key=lambda kv: kv[1], reverse=True)
    best, best_r = ranked[0]
    second, second_r = (ranked[1] if len(ranked) > 1 else (None, 0.0))
    margin = best_r - second_r

    # Fit over everything pooled, for the formula.
    xs = [v for d in usable for v in d["candidate"]]
    ys = [v for d in usable for v in d["channels"][best]]
    f = fit(xs, ys)

    out = {
        "decoding": decoding_name,
        "best": best, "r": round(best_r, 4),
        "runner_up": second, "runner_up_r": round(second_r, 4),
        "margin": round(margin, 4),
        "drives": len(usable), "points": len(xs),
        "ranked": [(k, round(v, 4)) for k, v in ranked[:5]],
    }
    if f:
        a, b, rmse = f
        out["scale"] = round(a, 6)
        out["offset"] = round(b, 4)
        out["rmse"] = round(rmse, 4)
        out["formula"] = _formula(decoding_name, a, b)

    if best_r < MIN_R:
        out["verdict"] = "weak"
        out["why"] = f"best correlation {best_r:.3f} is below {MIN_R}"
    elif margin < MIN_MARGIN:
        out["verdict"] = "ambiguous"
        out["why"] = (f"{best} ({best_r:.3f}) and {second} ({second_r:.3f}) fit "
                      f"almost equally well. The data cannot tell them apart; "
                      f"a drive that separates them would.")
    else:
        out["verdict"] = "tracks"
        out["why"] = (f"tracks {best} at r={best_r:.3f} across {len(usable)} "
                      f"drives, beating {second} by {margin:.3f}")
    return out


LETTERS = "ABCDEFGHIJKLMNOP"


def _letter(i):
    """Byte index to the letter OBD formulas conventionally use.

    All indices, not just the first four. An earlier version mapped 0-3 to
    A-D and fell back to "byte4" beyond that, so a two-byte value spanning
    that boundary rendered as `(D*256+byte4)` -- half convention, half debug
    output, and not a formula anything could evaluate.
    """
    return LETTERS[i] if i < len(LETTERS) else f"b{i}"


def _formula(decoding, a, b):
    """Turn a decoding name and a fit into the profile's formula syntax."""
    if not decoding:
        return ""
    name = decoding[:-7] if decoding.endswith("_signed") else decoding
    if name.startswith("word"):
        i = int(name[4:])
        term = f"({_letter(i)}*256+{_letter(i + 1)})"
    elif name.startswith("byte"):
        term = _letter(int(name[4:]))
    else:
        term = name
    sign = "+" if b >= 0 else "-"
    if abs(a - 1.0) < 1e-9 and abs(b) < 1e-9:
        return term
    if abs(b) < 1e-9:
        return f"{term}*{a:g}"
    return f"{term}*{a:g} {sign} {abs(b):g}"


def best_decoding(drives_by_decoding):
    """Pick the packing that explains the data best.

    `drives_by_decoding` maps a decoding name to the drives list for it.
    Returns the winning assessment, and every assessment for inspection --
    because "we tried nine packings and this one won" is itself something a
    reader should be able to check.
    """
    results = []
    for name, drives in drives_by_decoding.items():
        results.append(assess(drives, decoding_name=name))
    order = {"tracks": 0, "ambiguous": 1, "weak": 2, "no-signal": 3,
             "insufficient": 4}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), -(r.get("r") or 0)))
    return results[0], results


# ---- reading the logs -------------------------------------------------------

# A gap longer than this ends a drive. Bursts are ten seconds apart by default,
# so five minutes of silence means the car was off, not that a sample was
# missed -- and the whole point of counting drives separately is that a
# coincidence within one warm-up does not survive being switched off and on.
DRIVE_GAP = 300.0


def load_log(paths):
    """Rows from candidate logs, oldest first, skipping non-samples."""
    import json
    rows = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("candidates") and r.get("channels"):
                        rows.append(r)
        except OSError:
            continue
    rows.sort(key=lambda r: r.get("t", 0))
    return rows


def split_drives(rows):
    out, cur, last = [], [], None
    for r in rows:
        t = r.get("t", 0)
        if last is not None and t - last > DRIVE_GAP:
            if cur:
                out.append(cur)
            cur = []
        cur.append(r)
        last = t
    if cur:
        out.append(cur)
    return out


def series_for(drives_rows, cand_id):
    """Per-decoding drive lists for one candidate.

    A row where the candidate did not answer is dropped ALONG WITH its
    channels, keeping the two series index-aligned. Filling a gap with the
    previous value would manufacture correlation out of nothing.
    """
    by_decoding = {}
    for rows in drives_rows:
        names = None
        per = {}
        chans = {ch: [] for ch in TRUSTED}
        for r in rows:
            payload = (r.get("candidates") or {}).get(cand_id)
            if not payload:
                continue
            d = decodings(payload)
            if not d:
                continue
            if names is None:
                names = list(d)
                per = {n: [] for n in names}
            if list(d) != names:
                continue          # payload changed length; not the same thing
            ch = r.get("channels") or {}
            if any(ch.get(c) is None for c in TRUSTED):
                # Only keep rows where every yardstick answered, so all
                # channels are compared over identical samples.
                continue
            for n in names:
                per[n].append(d[n])
            for c in TRUSTED:
                chans[c].append(float(ch[c]))
        if not names:
            continue
        for n in names:
            by_decoding.setdefault(n, []).append(
                {"candidate": per[n], "channels": chans})
    return by_decoding


def analyse(drives_rows, cand_id):
    by_decoding = series_for(drives_rows, cand_id)
    if not by_decoding:
        return {"verdict": "insufficient", "why": "no aligned samples logged"}, []
    return best_decoding(by_decoding)


# ---- labelled windows: what changes ONLY while a thing is being done ---------
#
# Everything above asks "what tracks a channel we already trust". Passive CAN
# capture has no such channel for most of what the owner cares about (is the
# brake down, which way is the wheel turned, is the IMA ready): nobody has said
# which byte means it. "Learn my car" (lib/learncar.py) supplies the missing
# yardstick as a person: it asks for ONE thing at a marked moment, listens, and
# writes down when. This ranks, for each label, the identifier bytes that sit
# still while nothing is being done and move when it is.
#
# THE RULE, AND WHY IT IS THIS STRICT.
#   * Quiet outside. The byte takes at most LABEL_MAX_QUIET distinct values in
#     the label's own rest windows. A rolling counter (16 values), a checksum
#     (it follows the counter) and a wheel-speed jitter all fail here, which is
#     exactly why they are not offered: they "change inside the window" too,
#     and they change outside it just as much.
#   * Novel inside. In an active window, at least LABEL_HIT_FRACTION of the
#     frames carry a value the rest windows never showed. Not "a different
#     average": a different value.
#   * Every window, or nearly. Hits/windows is the confidence, printed as the
#     fraction it is (18/18). Under LABEL_MIN_WINDOWS windows it is a hint, and
#     says so. A byte that also goes novel while the person does OTHER things
#     is reported with where else it moved, because a byte that moves whenever
#     anything happens is "the car is busy", not "the brake".
# Frames inside LABEL_SETTLE of a window's edge are dropped: a person presses a
# pedal slightly after the marker, and a window that still holds the previous
# state is how a clean switch gets reported as noise.

LABEL_SETTLE = 0.4            # s trimmed from each window edge
LABEL_MIN_FRAMES = 5          # frames an id needs in a window before it counts
LABEL_MAX_QUIET = 3           # distinct rest values a byte may take
LABEL_HIT_FRACTION = 0.4      # share of a window's frames that must be novel (a
                              # flashing lamp is novel about half the time)
LABEL_MIN_REST_FRAMES = 10    # rest frames needed before "quiet" means anything
LABEL_MIN_WINDOWS = 3         # active windows heard before it is more than a hint
LABEL_PASS = 0.75             # hits/windows needed to be listed at all


def _trim(w, settle):
    """The span of a window that is safely in its state. Capped at a quarter of
    the window per side so a short window keeps its shape."""
    span = w["t1"] - w["t0"]
    cut = min(settle, max(0.0, span) * 0.25)
    return w["t0"] + cut, w["t1"] - cut


def _window_values(run, w, settle):
    """{id: [bytes, ...]} for the frames inside one window of one run."""
    import bisect
    times = run.get("_times")
    if times is None:
        run["frames"] = sorted(run["frames"], key=lambda f: f[0])
        times = run["_times"] = [f[0] for f in run["frames"]]
    t0, t1 = _trim(w, settle)
    lo, hi = bisect.bisect_left(times, t0), bisect.bisect_left(times, t1)
    out = {}
    for _t, fid, data in run["frames"][lo:hi]:
        out.setdefault(fid, []).append(bytes(data))
    return out


def _bit_mask(a, b):
    return a ^ b


def rank_labelled(runs, settle=LABEL_SETTLE):
    """Rank candidate signals per label.

    `runs` is a list of {"frames": [(t, can_id, bytes)], "windows": [{"label",
    "state", "active", "t0", "t1"}]}, with frames and windows on one time base
    per run. Several runs (a capture per step, several sessions) pool: a label's
    windows from every run count together, which is how confidence grows with
    each session. Returns {label: [candidate, ...]}, best first; a label with
    nothing to report maps to []. Pure: no files, no clock.
    """
    # Per label: the rest windows' values, and every active window's values.
    labels = {}
    for run in runs:
        for w in run.get("windows") or []:
            vals = _window_values(run, w, settle)
            labels.setdefault(w["label"], {"rest": [], "act": []})[
                "act" if w.get("active") else "rest"].append((w, vals))

    # Per label: each byte's rest values (when it was quiet enough to mean
    # something), and the frames behind them.
    for label, L in labels.items():
        rest = {}                      # (id, i) -> [values]
        rest_frames = {}               # id -> frame count
        for _w, vals in L["rest"]:
            for fid, datas in vals.items():
                rest_frames[fid] = rest_frames.get(fid, 0) + len(datas)
                for d in datas:
                    for i, b in enumerate(d):
                        rest.setdefault((fid, i), []).append(b)
        quiet = {}                     # (id, i) -> frozenset of rest values
        for key, vs in rest.items():
            if rest_frames.get(key[0], 0) < LABEL_MIN_REST_FRAMES:
                continue
            s = frozenset(vs)
            if len(s) <= LABEL_MAX_QUIET:
                quiet[key] = s
        L["rest_vals"], L["rest_frames"], L["quiet"] = rest, rest_frames, quiet

    result = {}
    for label, L in labels.items():
        rest, rest_frames, quiet = L["rest_vals"], L["rest_frames"], L["quiet"]

        cands = {}
        for w, vals in L["act"]:
            for fid, datas in vals.items():
                if len(datas) < LABEL_MIN_FRAMES:
                    continue              # not heard enough to say anything
                width = min(len(d) for d in datas)
                for i in range(width):
                    key = (fid, i)
                    if key not in quiet:
                        continue
                    seen = [d[i] for d in datas]
                    novel = [b for b in seen if b not in quiet[key]]
                    c = cands.setdefault(key, {"heard": 0, "hits": 0, "modal": [],
                                               "frames": 0, "states": set()})
                    c["heard"] += 1
                    c["frames"] += len(seen)
                    if len(novel) / len(seen) >= LABEL_HIT_FRACTION:
                        c["hits"] += 1
                        c["modal"].append(max(set(novel), key=novel.count))
                        c["states"].add(w.get("state") or "")
        listed = []
        for (fid, i), c in cands.items():
            if c["heard"] < 1 or c["hits"] / c["heard"] < LABEL_PASS:
                continue
            rest_modal = max(set(rest[(fid, i)]), key=rest[(fid, i)].count)
            switch = len(set(c["modal"])) == 1
            listed.append({
                "id": fid, "byte": i,
                "kind": "switch" if switch else "analog",
                "rest_value": rest_modal,
                "active_value": c["modal"][0] if switch else None,
                "active_values": sorted(set(c["modal"])),
                "bits": _bit_mask(rest_modal, c["modal"][0]) if switch else None,
                "hits": c["hits"], "windows": c["heard"],
                "frames_active": c["frames"],
                "frames_rest": rest_frames.get(fid, 0),
                "states": sorted(s for s in c["states"] if s),
                "strong": c["heard"] >= LABEL_MIN_WINDOWS and c["hits"] == c["heard"],
                "also_in": [], "specific": True,
            })

        # Where ELSE does this byte move? Judged against that other label's own
        # rest windows: a byte that merely sits at a different value in another
        # context (engine off vs engine on) has not moved there. A byte that
        # leaves its own rest values while some other thing is being done is
        # a general "something is happening" byte, not this label's signal.
        for cand in listed:
            key = (cand["id"], cand["byte"])
            for other, O in labels.items():
                if other == label or key not in O["quiet"]:
                    continue
                oq = O["quiet"][key]
                moved = 0
                for _w, vals in O["act"]:
                    datas = vals.get(cand["id"]) or []
                    seen = [d[cand["byte"]] for d in datas if len(d) > cand["byte"]]
                    if len(seen) >= LABEL_MIN_FRAMES and sum(
                            1 for b in seen if b not in oq) / len(seen) >= LABEL_HIT_FRACTION:
                        moved += 1
                if moved:
                    cand["also_in"].append(other)
            cand["specific"] = not cand["also_in"]
        listed.sort(key=lambda c: (len(c["also_in"]), not c["strong"],
                                   -(c["hits"] / c["windows"]), -c["windows"],
                                   c["kind"] != "switch", c["id"], c["byte"]))
        result[label] = _join_adjacent(listed)
    return result


def _join_adjacent(cands):
    """Two analog bytes side by side in one identifier are one 16-bit value (a
    steering angle is not two findings). The pair keeps the better byte's
    numbers and says which bytes it spans."""
    out, used = [], set()
    by = {(c["id"], c["byte"]): c for c in cands}
    for c in cands:
        key = (c["id"], c["byte"])
        if key in used:
            continue
        c = dict(c, span=[c["byte"], c["byte"]])
        nxt = by.get((c["id"], c["byte"] + 1))
        if c["kind"] == "analog" and nxt and nxt["kind"] == "analog" \
                and (c["id"], c["byte"] + 1) not in used:
            used.add((c["id"], c["byte"] + 1))
            c["span"] = [c["byte"], c["byte"] + 1]
        out.append(c)
    return out


def load_labelled(captures_dir, labels_path=None, simulated=False):
    """Runs for rank_labelled(), from what lib/learncar.py wrote.

    `labels_path` defaults to labels.jsonl inside `captures_dir`. Each line
    names a capture file (without .json) and one window in absolute time; the
    capture's raw frames carry times relative to its `started`. Lines for a
    capture that is missing or has no raw frames are skipped, and so is any
    line whose `simulated` flag is not the one asked for -- a simulator's
    frames must never rank against a real car's.
    """
    import json
    import os
    path = labels_path or os.path.join(captures_dir, "labels.jsonl")
    by_cap = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    float(r["t0"]), float(r["t1"]), r["label"], r["capture"]
                except (ValueError, KeyError, TypeError):
                    continue
                if bool(r.get("simulated")) != bool(simulated):
                    continue
                by_cap.setdefault(r["capture"], []).append(r)
    except OSError:
        return []
    runs = []
    for name, wins in sorted(by_cap.items()):
        try:
            with open(os.path.join(captures_dir, name + ".json"), encoding="utf-8") as f:
                doc = json.load(f)
            started = float(doc["started"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        frames = []
        for fr in doc.get("raw") or []:
            try:
                frames.append((started + float(fr["t"]), str(fr["id"]).upper(),
                               bytes.fromhex(fr["data"])))
            except (ValueError, KeyError, TypeError):
                continue
        if not frames:
            continue
        runs.append({"capture": name, "frames": frames, "windows": [
            {"label": r["label"], "state": r.get("state") or "",
             "active": bool(r.get("active")), "t0": float(r["t0"]),
             "t1": float(r["t1"])} for r in wins]})
    return runs
