#!/usr/bin/env python3
"""Judge a driveway-check capture, and pick the recorder preset for today.

This is the part of tools/driveway-check.sh that is worth testing without a
car: reading a saved capture and deciding pass/fail against the thresholds in
doc/drive-day.md's "Then, if there's time" and "Telemetry versus capture"
sections, and picking one row of the preset table from the two verdicts.
Every function below is pure -- it takes a capture document (the dict
lib/listen.py's Capture.save() writes: started/note/raw[{t,id,data}], plus
the census/discriminators fields this module ignores) or plain numbers, and
returns a plain value. No port is opened here; nothing here can send
anything to the car.

driveway-check.sh calls this file's CLI (see main() at the bottom) rather
than reimplementing any of this in bash, so the thresholds live in one place
and test/driveway_test.py can pin them with synthetic documents.
"""

import json
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import listen as listenlib  # noqa: E402  (CAPTURES, for load_capture())
import drivelog  # noqa: E402  (OVERFLOW_SETTLE and OMACAR_DRIVELOG_QUIET's own bounds)

# -- capture A: the fast link alone --------------------------------------
#
# doc/drive-day.md, "Then, if there's time": "well over 20,000 frames, the
# raw timestamps span about 20s ... Fail: a hang ... or about 120 frames."
A_MIN_FRAMES = 20000
A_MIN_SPAN = 15.0

# -- capture B: the fast link plus ATCAF0 --------------------------------
#
# "identifiers 097, 1AA, 1CF and 374 all show up, and the 0x161 and 0x164
# frames are the full 8 bytes each -- not the truncated 6 and 5 bytes
# (respectively) ATCAF1 leaves them at." Spelled as raw[].id spells them:
# uppercase, no 0x prefix, at whatever width parse() recorded (3 hex digits
# for an 11-bit id, which every one of these is).
CAF0_REQUIRED_IDS = ("097", "1AA", "1CF", "374")
FULL_WIDTH_IDS = ("161", "164")
FULL_WIDTH_BYTES = 8

# The gear frame. 0x191 byte 0: 0x01 = P. Reported as information only (see
# driveway-check.sh) -- a free cross-check that the car was in P throughout,
# never a pass/fail gate of its own.
GEAR_ID = "191"
GEAR_PARK = "01"


def summarize(doc):
    """Counts, span, identifiers, frame lengths and gear values for one
    saved capture document.

    `doc` is what lib/listen.py's Capture.save(raw=True) writes, or the
    equivalent a test builds by hand: {"raw": [{"t": <float>, "id": <str>,
    "data": <hex str>}, ...], ...}. `t` is relative to the capture's own
    `started`, which is exactly what a span (last - first) needs and does
    not need `started` to compute.
    """
    raw = doc.get("raw") or []
    frames = len(raw)
    times = [r.get("t") or 0.0 for r in raw]
    span = (max(times) - min(times)) if times else 0.0

    ids_seen = set()
    lengths = {}   # id -> the longest frame heard for it, in bytes
    gear = {}      # GEAR_ID's byte 0, as two hex chars -> how many times

    for r in raw:
        ident = str(r.get("id") or "").upper()
        data = str(r.get("data") or "")
        if not ident:
            continue
        ids_seen.add(ident)
        nbytes = len(data) // 2
        if nbytes > lengths.get(ident, 0):
            lengths[ident] = nbytes
        if ident == GEAR_ID and len(data) >= 2:
            byte0 = data[0:2].upper()
            gear[byte0] = gear.get(byte0, 0) + 1

    return {
        "frames": frames,
        "span": round(span, 3),
        "ids_seen": sorted(ids_seen),
        "lengths": lengths,
        "gear": gear,
    }


def verdict_a(summary):
    """(passed, why) for capture A -- the fast link alone.

    A hang is not this function's business: `timeout 90` already turned that
    into a process that exited 124 rather than one that returned a capture
    at all, and driveway-check.sh checks that before it ever calls this.
    """
    frames, span = summary["frames"], summary["span"]
    if frames > A_MIN_FRAMES and span >= A_MIN_SPAN:
        return True, f"{frames} frames over {span:.1f}s"
    return False, f"only {frames} frames over {span:.1f}s"


def verdict_b(summary):
    """(passed, why) for capture B -- the fast link plus ATCAF0."""
    missing = [i for i in CAF0_REQUIRED_IDS if i not in summary["ids_seen"]]
    if missing:
        return False, f"missing identifiers: {', '.join(missing)}"
    short = [i for i in FULL_WIDTH_IDS
             if summary["lengths"].get(i, 0) < FULL_WIDTH_BYTES]
    if short:
        got = ", ".join(f"{i}={summary['lengths'].get(i, 0)}B" for i in short)
        return False, f"still truncated: {got}"
    return True, "097/1AA/1CF/374 all present; 161 and 164 at full width"


def gear_report(summary):
    """The 0x191 byte-0 values heard, as a list of 'VALUE=count' strings,
    most-heard first, with 0x01 (P) called out by name. Information only --
    see the module docstring."""
    rows = sorted(summary["gear"].items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for value, count in rows:
        label = " (P)" if value == GEAR_PARK else ""
        out.append(f"{value}={count}{label}")
    return out


# -- the preset table -----------------------------------------------------
#
# doc/drive-day.md's own table, row for row. Env values are strings because
# that is what ends up on the right of `Environment=` in a systemd drop-in.
PRESET_TELEMETRY_FIRST_CAF0 = "telemetry-first-caf0"
PRESET_TELEMETRY_FIRST = "telemetry-first"
PRESET_FALLBACK = "fallback"
PRESET_NAMES = (PRESET_TELEMETRY_FIRST_CAF0, PRESET_TELEMETRY_FIRST, PRESET_FALLBACK)

_TELEMETRY_FIRST_ENV = (
    ("OMACAR_FASTBAUD", "1"),
    ("OMACAR_DRIVELOG_LEG_LINES", "10000"),
    ("OMACAR_DRIVELOG_BETWEEN", "240"),
)

# lib/drivelog.py's own bounds for OMACAR_DRIVELOG_QUIET, read rather than
# restated -- if that floor ever moves, the fallback preset's choice below
# moves with it instead of quietly going stale. Not a change to lib/: this
# only reads the tuple it already defines.
_QUIET_FLOOR, _QUIET_CEILING = drivelog._QUIET_BOUNDS


def fallback_quiet_seconds():
    """<q> for the fallback preset's OMACAR_DRIVELOG_QUIET.

    "about 10s if allowed; otherwise the floor" (the brief this module was
    written against). 10s is comfortably inside lib/drivelog.py's bounds
    today (5.0-900.0s) and is short on purpose: the fallback preset's whole
    point is ending a leg fast when the adapter has already given up
    (OMACAR_DRIVELOG_END_ON_OVERFLOW=1), and QUIET is only the backstop for
    the rare case that path is not taken -- a long backstop would silently
    reintroduce the 120s-per-leg problem the fallback preset exists to avoid.
    """
    return 10.0 if _QUIET_FLOOR <= 10.0 <= _QUIET_CEILING else _QUIET_FLOOR


def choose_preset(a_passed, b_passed):
    """(name, env dict) -- the one row of doc/drive-day.md's table this pair
    of verdicts selects. `env` keys are in the order the doc's table lists
    them, so a caller building `Environment=` text does not have to re-sort.

    Capture B's verdict is only consulted when A passed, matching the doc:
    "do not add OMACAR_CAF0 on top of a link that has not proven itself" --
    a caller that never ran B when A failed can safely pass b_passed=False.
    """
    if a_passed and b_passed:
        env = dict(_TELEMETRY_FIRST_ENV)
        env["OMACAR_CAF0"] = "1"
        # Re-insert in the table's own left-to-right order: FASTBAUD, CAF0,
        # LEG_LINES, BETWEEN.
        ordered = {"OMACAR_FASTBAUD": env["OMACAR_FASTBAUD"],
                   "OMACAR_CAF0": env["OMACAR_CAF0"],
                   "OMACAR_DRIVELOG_LEG_LINES": env["OMACAR_DRIVELOG_LEG_LINES"],
                   "OMACAR_DRIVELOG_BETWEEN": env["OMACAR_DRIVELOG_BETWEEN"]}
        return PRESET_TELEMETRY_FIRST_CAF0, ordered
    if a_passed:
        return PRESET_TELEMETRY_FIRST, dict(_TELEMETRY_FIRST_ENV)
    q = fallback_quiet_seconds()
    env = {
        "OMACAR_DRIVELOG_END_ON_OVERFLOW": "1",
        "OMACAR_DRIVELOG_QUIET": f"{q:g}",
        "OMACAR_DRIVELOG_BETWEEN": "240",
    }
    return PRESET_FALLBACK, env


def env_line(env):
    """The one `Environment=` line systemd reads, in the dict's own order."""
    return " ".join(f"{k}={v}" for k, v in env.items())


def preset_env_by_name(name):
    """The env dict for a preset picked by name (`--apply-preset`), not by
    running a test. Raises KeyError for anything not in PRESET_NAMES."""
    if name == PRESET_TELEMETRY_FIRST_CAF0:
        return choose_preset(True, True)[1]
    if name == PRESET_TELEMETRY_FIRST:
        return choose_preset(True, False)[1]
    if name == PRESET_FALLBACK:
        return choose_preset(False, False)[1]
    raise KeyError(name)


# -- telemetry share, the doc's own method: leg hold / cycle --------------
#
# doc/drive-day.md's table computes this the same way for every row: however
# long a leg holds the port, plus the gap between legs, is the cycle: the
# gap's share of the cycle is telemetry -- the daemon has the port back and
# the gauges are alive.
DOC_FASTBAUD_RATE = 1900.0    # frames/s, doc/drive-day.md's own measurement
DOC_BURST_SECONDS = 0.075     # the ~75ms 115200-baud burst, same source


def expected_telemetry(preset_name, env, measured_rate=None):
    """(fraction, leg_hold_s, cycle_s, measured) for the chosen preset.

    For the two fast-link presets, leg hold is `leg_lines / rate`. `rate` is
    today's own capture-A frame rate when the caller has one (measured=True
    -- the honest number, since it is this car and this adapter, today) and
    falls back to doc/drive-day.md's own ~1,900 frames/s otherwise
    (measured=False -- e.g. for --apply-preset, which installs a preset
    without running a capture at all).

    For the fallback preset nothing this script ever runs measures the
    plain-115200-baud burst -- every capture it takes uses
    OMACAR_FASTBAUD=1 -- so its leg hold is always doc/drive-day.md's own
    figure (the ~75ms burst, plus lib/drivelog.py's OVERFLOW_SETTLE, which
    is how much longer a leg actually holds the port after the adapter says
    it has given up) and measured is always False.
    """
    between = float(env.get("OMACAR_DRIVELOG_BETWEEN", drivelog.BETWEEN_LEGS))
    if preset_name == PRESET_FALLBACK:
        leg_hold = DOC_BURST_SECONDS + drivelog.OVERFLOW_SETTLE
        measured = False
    else:
        leg_lines = float(env.get("OMACAR_DRIVELOG_LEG_LINES", listenlib.DEFAULT_LIMIT))
        rate = measured_rate if measured_rate else DOC_FASTBAUD_RATE
        leg_hold = leg_lines / rate
        measured = measured_rate is not None
    cycle = leg_hold + between
    fraction = between / cycle if cycle else 0.0
    return fraction, leg_hold, cycle, measured


# -- loading a saved capture, for the CLI below ----------------------------

def load_capture(name_or_path):
    """A summarize()-able dict, by capture name (as lib/listen.py saves it,
    under listen.CAPTURES) or a path to its .json file directly."""
    path = name_or_path if os.path.isfile(name_or_path) else \
        os.path.join(listenlib.CAPTURES, name_or_path + ".json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------- CLI
#
# driveway-check.sh's own interface to this file: each subcommand prints
# `key=value` lines, one per line, the same shape ima-session.sh already
# parses its own precheck output with (see tools/ima-session.sh's
# PRECHECK_OUT) -- so the shell script never has to parse JSON.

def _print_kv(pairs):
    for k, v in pairs:
        print(f"{k}={v}")


def _cmd_verdict(argv, which):
    if not argv:
        print("usage: driveway_verdict.py verdict-a|verdict-b CAPTURE", file=sys.stderr)
        return 2
    doc = load_capture(argv[0])
    summary = summarize(doc)
    passed, why = (verdict_a if which == "a" else verdict_b)(summary)
    pairs = [
        ("frames", summary["frames"]),
        ("span", f"{summary['span']:.3f}"),
        ("ids", ",".join(summary["ids_seen"])),
        ("passed", 1 if passed else 0),
        ("why", why),
    ]
    for ident in FULL_WIDTH_IDS:
        pairs.append((f"len_{ident}", summary["lengths"].get(ident, 0)))
    gear = gear_report(summary)
    pairs.append(("gear", ";".join(gear) if gear else "none"))
    _print_kv(pairs)
    return 0


def _cmd_preset(argv):
    if len(argv) < 2:
        print("usage: driveway_verdict.py preset A_PASSED B_PASSED", file=sys.stderr)
        return 2
    a_passed, b_passed = argv[0] == "1", argv[1] == "1"
    name, env = choose_preset(a_passed, b_passed)
    _print_kv([("preset", name), ("env", env_line(env))])
    return 0


def _cmd_preset_for(argv):
    if not argv:
        print("usage: driveway_verdict.py preset-for NAME", file=sys.stderr)
        return 2
    try:
        env = preset_env_by_name(argv[0])
    except KeyError:
        print(f"driveway_verdict.py: no such preset: {argv[0]!r}", file=sys.stderr)
        return 1
    _print_kv([("preset", argv[0]), ("env", env_line(env))])
    return 0


def _cmd_telemetry(argv):
    if len(argv) < 2:
        print("usage: driveway_verdict.py telemetry PRESET ENV_LINE [RATE]", file=sys.stderr)
        return 2
    preset_name, line = argv[0], argv[1]
    env = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            env[k] = v
    rate = float(argv[2]) if len(argv) > 2 and argv[2] not in ("", "none") else None
    fraction, leg_hold, cycle, measured = expected_telemetry(preset_name, env, rate)
    _print_kv([
        # One decimal, not zero: a whole-number ".0f" of 99.55% prints "100",
        # which reads as "no capture happens at all" -- a claim this was
        # never measured well enough to make, and not even the one the doc's
        # own math supports.
        ("pct", f"{fraction * 100:.1f}"),
        ("leg_hold", f"{leg_hold:.2f}"),
        ("cycle", f"{cycle:.2f}"),
        ("measured", 1 if measured else 0),
    ])
    return 0


def _capture_block(path):
    """The summary.json fragment for one capture, or None if it never ran."""
    if not path:
        return None
    doc = load_capture(path)
    summary = summarize(doc)
    return {
        "name": os.path.splitext(os.path.basename(path))[0],
        "note": doc.get("note", ""),
        "frames": summary["frames"],
        "span": summary["span"],
        "ids_seen": summary["ids_seen"],
        "gear": summary["gear"],
    }, summary


def _cmd_write_summary(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="driveway_verdict.py write-summary")
    ap.add_argument("--out", required=True, help="session directory")
    ap.add_argument("--stamp", default="")
    ap.add_argument("--mode", default="test")
    ap.add_argument("--dry-run", type=int, default=0)
    ap.add_argument("--parked", type=int, default=0)
    ap.add_argument("--aborted", default="")
    ap.add_argument("--capture-a", default="")
    ap.add_argument("--capture-b", default="")
    ap.add_argument("--reconnect-a", default="")
    ap.add_argument("--reconnect-b", default="")
    ap.add_argument("--preset", default="")
    ap.add_argument("--env", default="")
    ap.add_argument("--applied", type=int, default=0)
    args = ap.parse_args(argv)

    def _num(s):
        try:
            return float(s) if s != "" else None
        except ValueError:
            return None

    cap_a_block = cap_a_summary = None
    if args.capture_a:
        cap_a_block, cap_a_summary = _capture_block(args.capture_a)
        passed_a, why_a = verdict_a(cap_a_summary)
        cap_a_block["passed"] = passed_a
        cap_a_block["why"] = why_a

    cap_b_block = None
    if args.capture_b:
        cap_b_block, cap_b_summary = _capture_block(args.capture_b)
        passed_b, why_b = verdict_b(cap_b_summary)
        cap_b_block["passed"] = passed_b
        cap_b_block["why"] = why_b

    telemetry = None
    if args.preset and args.env:
        env = dict(tok.split("=", 1) for tok in args.env.split() if "=" in tok)
        rate = None
        if cap_a_summary and cap_a_summary["span"] > 0:
            rate = cap_a_summary["frames"] / cap_a_summary["span"]
        frac, leg_hold, cycle, measured = expected_telemetry(args.preset, env, rate)
        telemetry = {"pct": round(frac * 100, 1), "leg_hold_s": round(leg_hold, 2),
                     "cycle_s": round(cycle, 2), "measured": measured}

    doc = {
        "stamp": args.stamp,
        "mode": args.mode,
        "dry_run": bool(args.dry_run),
        "parked_flag": bool(args.parked),
        "aborted_reason": args.aborted or None,
        "capture_a": cap_a_block,
        "capture_b": cap_b_block,
        "reconnect_a_seconds": _num(args.reconnect_a),
        "reconnect_b_seconds": _num(args.reconnect_b),
        "preset": args.preset or None,
        "preset_env": args.env or None,
        "applied": bool(args.applied),
        "telemetry": telemetry,
    }
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "summary.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)
    print(f"summary={path}")
    return 0


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "verdict-a":
        return _cmd_verdict(rest, "a")
    if cmd == "verdict-b":
        return _cmd_verdict(rest, "b")
    if cmd == "preset":
        return _cmd_preset(rest)
    if cmd == "preset-for":
        return _cmd_preset_for(rest)
    if cmd == "telemetry":
        return _cmd_telemetry(rest)
    if cmd == "write-summary":
        return _cmd_write_summary(rest)
    print(f"driveway_verdict.py: unknown command: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
