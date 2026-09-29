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
import time

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, LIB)

import listen as listenlib  # noqa: E402  (CAPTURES, for load_capture())
import drivelog  # noqa: E402  (OVERFLOW_SETTLE and OMACAR_DRIVELOG_QUIET's own bounds)
import connect  # noqa: E402  (YIELD_GRACE, read only)
import records  # noqa: E402  (LIVE, read only)

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
    lengths = {}       # id -> the longest frame heard for it, in bytes
    min_lengths = {}   # id -> the SHORTEST frame heard for it, in bytes
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
        if ident not in min_lengths or nbytes < min_lengths[ident]:
            min_lengths[ident] = nbytes
        if ident == GEAR_ID and len(data) >= 2:
            byte0 = data[0:2].upper()
            gear[byte0] = gear.get(byte0, 0) + 1

    return {
        "frames": frames,
        "span": round(span, 3),
        "ids_seen": sorted(ids_seen),
        "lengths": lengths,
        "min_lengths": min_lengths,
        "gear": gear,
        # Which link it was heard on, as lib/listen.py saves it: the rate
        # the handle was on and what OMACAR_FASTBAUD's raise did. Both None
        # on a capture saved before they were recorded.
        "link_baud": doc.get("link_baud"),
        "fastbaud": doc.get("fastbaud"),
        "overflowed": [w for w in (doc.get("adapter_said") or [])
                       if listenlib.Capture._is_overflow_word(str(w).upper())],
    }


# -- which link a capture was on -------------------------------------------
#
# 29 September, capture A: 142 frames in 0.33s, then BUFFER FULL -- identical
# to 115200 -- and all the verdict could say was "only 142 frames". A link
# that never came up and a link that came up and still could not keep up are
# different faults with different fixes, and the capture now says which.

_STEP_LABELS = {"echo-off": "the echo-off step", "ATBRD OK": "the ATBRD step",
                "final OK": "the final OK step"}


def _answered(fastbaud, step):
    for s in fastbaud.get("steps") or []:
        if s.get("step") == step:
            return s.get("answered")
    return None


def _quoted(text):
    """What the adapter said, on one line: CR and LF shown as \\r and \\n,
    and bytes the handshake already kept as \\x escapes left as they are."""
    if not text:
        return "nothing"
    esc = {"\r": "\\r", "\n": "\\n", "\t": "\\t"}
    return "'" + "".join(ch if " " <= ch <= "~" else esc.get(ch, f"\\x{ord(ch):02x}")
                         for ch in text) + "'"


def _where_raise_stopped(fastbaud):
    """The step the raise gave up at, and what it heard there, in words."""
    if fastbaud.get("outcome") == "not attempted":
        return f"the raise was not attempted: {fastbaud.get('why') or 'no reason recorded'}"
    step = fastbaud.get("failed_at")
    if not step:
        return "the raise said it succeeded"
    target = fastbaud.get("target") or connect.FAST_BAUD
    label = _STEP_LABELS.get(step) or (f"the ident step at {target} baud"
                                       if step == "ident" else f"the {step} step")
    if fastbaud.get("why"):
        text = f"{label} failed: {' '.join(str(fastbaud['why']).split())}"
    else:
        text = f"{label} said {_quoted(_answered(fastbaud, step))}"
    # The echo-off's reply is not a gate -- the handshake goes on to ATBRD
    # whatever it was -- but no OK there is the likeliest reason for what
    # happened next, so it is named beside the step that gave up.
    echo = _answered(fastbaud, "echo-off")
    if step != "echo-off" and echo is not None and "OK" not in echo.upper():
        text += f"; the echo-off before it said {_quoted(echo)}"
    return text


def link_why(summary):
    """Whether the fast link engaged, in words, or "" for a capture saved
    before the link was recorded (which then reads as it always did)."""
    baud, fastbaud = summary.get("link_baud"), summary.get("fastbaud")
    if baud is None and fastbaud is None:
        return ""
    if fastbaud is None:
        return f"the fast link was not asked for (OMACAR_FASTBAUD was not 1; {baud} baud)"
    target = fastbaud.get("target") or connect.FAST_BAUD
    if baud and baud >= target:
        over = summary.get("overflowed") or []
        if over:
            return (f"the fast link engaged ({baud} baud) but the adapter still "
                    f"overflowed ({over[0]})")
        return f"the fast link engaged ({baud} baud)"
    return (f"the fast link did not engage (still {baud or 'an unknown'} baud; "
            f"{_where_raise_stopped(fastbaud)})")


def raise_outcome(summary):
    """One short word for the raise: raised, failed at <step>, not
    attempted, not asked -- or "" for a capture from before it was kept."""
    fastbaud = summary.get("fastbaud")
    if fastbaud is None:
        return "not asked" if summary.get("link_baud") is not None else ""
    if fastbaud.get("outcome") == "failed":
        return f"failed at {fastbaud.get('failed_at')}"
    return fastbaud.get("outcome") or ""


def _too_few(summary):
    """"only N frames over Ss", led by what the link says about why."""
    base = f"only {summary['frames']} frames over {summary['span']:.1f}s"
    link = link_why(summary)
    return f"{link}: {base}" if link else base


def verdict_a(summary):
    """(passed, why) for capture A -- the fast link alone.

    A hang is not this function's business: `timeout 90` already turned that
    into a process that exited 124 rather than one that returned a capture
    at all, and driveway-check.sh checks that before it ever calls this.
    """
    frames, span = summary["frames"], summary["span"]
    if frames > A_MIN_FRAMES and span >= A_MIN_SPAN:
        return True, f"{frames} frames over {span:.1f}s"
    return False, _too_few(summary)


def verdict_b(summary):
    """(passed, why) for capture B -- the fast link plus ATCAF0.

    Three things have to hold together, and every one that does not is named:

      - it is a capture of the fast link at all -- A's own thresholds (over
        20,000 frames, a span of at least 15s). ATCAF0 sends every frame at
        full length on a link that is already close to full, so a capture
        that overflows after a second can still hold all four identifiers
        and full-width 161/164 and mean nothing about a 20-second leg;
      - 097, 1AA, 1CF and 374 all show up;
      - the SHORTEST 161 and 164 frame is the full 8 bytes, not the longest.
        ATCAF1 truncates only some frames (any whose first byte is 0x01-0x07),
        so one full-width frame among truncated ones is not a pass.
    """
    problems = []
    frames, span = summary["frames"], summary["span"]
    if not (frames > A_MIN_FRAMES and span >= A_MIN_SPAN):
        problems.append(_too_few(summary))
    missing = [i for i in CAF0_REQUIRED_IDS if i not in summary["ids_seen"]]
    if missing:
        problems.append(f"missing identifiers: {', '.join(missing)}")
    mins = summary.get("min_lengths") or {}
    short = [i for i in FULL_WIDTH_IDS if mins.get(i, 0) < FULL_WIDTH_BYTES]
    if short:
        got = ", ".join(f"{i}={mins.get(i, 0)}B" for i in short)
        problems.append(f"still truncated: {got}")
    if problems:
        return False, "; ".join(problems)
    return True, (f"{frames} frames over {span:.1f}s; 097/1AA/1CF/374 all "
                  f"present; 161 and 164 at full width")


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
    """(name, env dict) -- the one row of the preset table this pair of
    verdicts selects. `env` keys are in a fixed left-to-right order, so a
    caller building `Environment=` text does not have to re-sort.

    Both fast-link rows also carry OMACAR_DRIVELOG_END_ON_OVERFLOW=1 and a
    short OMACAR_DRIVELOG_QUIET. That is the controller's ruling on top of
    doc/drive-day.md's own table: it costs nothing when nothing overflows,
    and if a leg does overflow it ends after ~1s instead of holding the port
    for the default 120s quiet timeout (which would take telemetry from
    ~98% to ~66%). QUIET comes from fallback_quiet_seconds(), inside
    lib/drivelog.py's own bounds.

    Capture B's verdict is only consulted when A passed, matching the doc:
    "do not add OMACAR_CAF0 on top of a link that has not proven itself" --
    a caller that never ran B when A failed can safely pass b_passed=False.
    """
    q = f"{fallback_quiet_seconds():g}"
    if a_passed:
        env = {"OMACAR_FASTBAUD": "1"}
        if b_passed:
            env["OMACAR_CAF0"] = "1"
        env["OMACAR_DRIVELOG_LEG_LINES"] = "10000"
        env["OMACAR_DRIVELOG_BETWEEN"] = "240"
        env["OMACAR_DRIVELOG_END_ON_OVERFLOW"] = "1"
        env["OMACAR_DRIVELOG_QUIET"] = q
        return (PRESET_TELEMETRY_FIRST_CAF0 if b_passed else PRESET_TELEMETRY_FIRST), env
    env = {
        "OMACAR_DRIVELOG_END_ON_OVERFLOW": "1",
        "OMACAR_DRIVELOG_QUIET": q,
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


def expected_telemetry(preset_name, env, measured_rate=None, reconnect_seconds=None):
    """(fraction, leg_hold_s, cycle_s, rate_measured) for the chosen preset.

    An ESTIMATE, never a measurement: only a frame rate (and, once
    driveway-check.sh has watched one, a reconnect time) is measured; the
    rest is the doc's own arithmetic.

    For the two fast-link presets, leg hold is `leg_lines / rate`. `rate` is
    today's own capture-A frame rate when the caller has one (rate_measured=
    True) and falls back to doc/drive-day.md's own ~1,900 frames/s otherwise
    (False -- e.g. for --apply-preset, which installs a preset without
    running a capture at all).

    `reconnect_seconds` is how long the gauges took to come back after
    today's capture (see wait-live below). The recorder's cycle is a leg
    plus a fixed gap, and the daemon reconnects INSIDE that gap, so those
    seconds are not extra cycle -- they are more time the gauges are down on
    top of the leg itself. They come off the telemetry share; the cycle
    stays leg + gap. None means nothing was measured, and the estimate then
    leaves out the few seconds of hand-over every leg costs.

    For the fallback preset nothing this script ever runs measures the
    plain-115200-baud burst -- every capture it takes uses
    OMACAR_FASTBAUD=1 -- so its leg hold is always doc/drive-day.md's own
    figure (the ~75ms burst, plus lib/drivelog.py's OVERFLOW_SETTLE, which
    is how much longer a leg actually holds the port after the adapter says
    it has given up), rate_measured is always False, and a reconnect time
    taken on the fast link is not applied to it.
    """
    between = float(env.get("OMACAR_DRIVELOG_BETWEEN", drivelog.BETWEEN_LEGS))
    if preset_name == PRESET_FALLBACK:
        leg_hold = DOC_BURST_SECONDS + drivelog.OVERFLOW_SETTLE
        measured = False
        reconnect_seconds = None
    else:
        leg_lines = float(env.get("OMACAR_DRIVELOG_LEG_LINES", listenlib.DEFAULT_LIMIT))
        rate = measured_rate if measured_rate else DOC_FASTBAUD_RATE
        leg_hold = leg_lines / rate
        measured = measured_rate is not None
    cycle = leg_hold + between
    down = leg_hold + (reconnect_seconds or 0.0)
    fraction = max(0.0, (cycle - down) / cycle) if cycle else 0.0
    return fraction, leg_hold, cycle, measured


def whole_percent(fraction):
    """A share as a whole number, never 0 or 100: this is an estimate, and
    "100%" would claim no time is ever spent capturing."""
    return max(1, min(99, int(round(fraction * 100))))


# -- loading a saved capture, for the CLI below ----------------------------

def load_capture(name_or_path):
    """A summarize()-able dict, by capture name (as lib/listen.py saves it,
    under listen.CAPTURES) or a path to its .json file directly."""
    path = name_or_path if os.path.isfile(name_or_path) else \
        os.path.join(listenlib.CAPTURES, name_or_path + ".json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# -- reading what the daemon and the recorder publish ---------------------
#
# Everything here reads files the daemon and the recorder already write --
# live.json, drivelog.json, the captures folder. Nothing opens the adapter.

WAIT_INTERVAL = 0.5


def read_live_raw():
    """live.json as the daemon last wrote it, however old, or None."""
    try:
        with open(records.LIVE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def gauges_back(doc, since):
    """Are the gauges alive again: the daemon says it is CONNECTED, is not
    mid hand-over, and said so after `since`.

    A fresh `t` on its own proves nothing. lib/daemon.py stamps a new `t` on
    every snapshot it publishes while it has lent the adapter out, on
    purpose (a hand-off that goes stale looks like a dead daemon), so "t has
    moved" is true the whole time the port is still somebody else's.
    """
    if not isinstance(doc, dict):
        return False
    return (doc.get("connected") is True
            and doc.get("status") != "yielded"
            and (doc.get("t") or 0) > since)


def port_released(doc, since, now):
    """Is nothing holding the port right now -- for a caller that only needs
    no leg in flight, not a car that is on and answering (the recorder is
    being restarted; the car may be off in the garage).

    No live.json, or one with no `t` or a stale one, means no daemon is
    publishing, so nothing is lending anything out. A fresh one has to be
    newer than `since` and not "yielded".
    """
    if not isinstance(doc, dict):
        return True
    t = doc.get("t")
    if not t or now - t > drivelog.LIVE_STALE:
        return True
    return doc.get("status") != "yielded" and t > since


def wait_live(mode, since, timeout, read=read_live_raw, clock=time.time,
              sleep=time.sleep, interval=WAIT_INTERVAL):
    """(ok, seconds_since, last_doc): poll until the gauges are back
    (mode "connected") or the port is released (mode "released"), up to
    `timeout` seconds. `seconds_since` is measured from `since`, the moment
    the thing that took the port away returned."""
    started = clock()
    while True:
        doc = read()
        now = clock()
        good = (gauges_back(doc, since) if mode == "connected"
                else port_released(doc, since, now))
        if good:
            return True, now - since, doc
        if now - started >= timeout:
            return False, now - since, doc
        sleep(interval)


def yield_grace():
    """How long a hung capture's lease can outlive it, read from lib/connect.py
    rather than restated. (The waits are overridable by env in the shell
    script, for the tests only.)"""
    return float(connect.YIELD_GRACE)


def find_capture(note, since, directory=None):
    """The newest saved capture with this `note` written at or after `since`
    (a time.time() value), as a path, or "" if this run saved none.

    "At or after `since`" is the point: a capture that failed without
    hanging leaves no file, and looking up the newest one with that note
    would find yesterday's, or the doc's manual run's, and judge that.
    Files older than `since` are not even opened, and a drive leg (several
    MB) is never opened at all.
    """
    directory = directory or listenlib.CAPTURES
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return ""
    best = ""
    for name in names:
        if not name.endswith(".json") or name.endswith("-drive.json"):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < since:
                continue
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("note") == note:
            best = path
    return best


def expected_running(env):
    """What a supervisor started with this `Environment=` line should
    publish in drivelog.json: between, leg_lines, quiet, end_on_overflow.
    Unset means the recorder's own default, read from lib/."""
    def num(key, default, cast=float):
        raw = env.get(key)
        if raw in (None, ""):
            return default
        try:
            return cast(raw)
        except ValueError:
            return default
    return {
        "between": num("OMACAR_DRIVELOG_BETWEEN", drivelog.BETWEEN_LEGS),
        "leg_lines": num("OMACAR_DRIVELOG_LEG_LINES", listenlib.DEFAULT_LIMIT, int),
        "quiet": num("OMACAR_DRIVELOG_QUIET", drivelog.QUIET_TIMEOUT),
        "end_on_overflow": env.get("OMACAR_DRIVELOG_END_ON_OVERFLOW") == "1",
    }


def check_running(doc, expected, since):
    """(ok, problems) for a drivelog.json document against a preset.

    `started` has to be later than `since` (the moment the restart was
    asked for) or this is the OLD supervisor's status, and the four numbers
    have to be the preset's. It cannot see OMACAR_FASTBAUD or OMACAR_CAF0:
    the supervisor does not publish those."""
    if not isinstance(doc, dict):
        return False, ["the recorder has published no status yet"]
    problems = []
    if (doc.get("started") or 0) <= since:
        problems.append("still the old supervisor's status (not restarted yet)")
    for key in ("between", "leg_lines", "quiet"):
        got, want = doc.get(key), expected[key]
        if got is None or abs(float(got) - float(want)) > 1e-6:
            problems.append(f"{key} is {got}, wanted {want:g}")
    if bool(doc.get("end_on_overflow")) != expected["end_on_overflow"]:
        problems.append(f"end_on_overflow is {bool(doc.get('end_on_overflow'))}, "
                        f"wanted {expected['end_on_overflow']}")
    return not problems, problems


def wait_running(env, since, timeout, read=drivelog.status_doc, clock=time.time,
                 sleep=time.sleep, interval=WAIT_INTERVAL):
    """(ok, problems, doc): poll drivelog.json until it matches the preset."""
    expected = expected_running(env)
    started = clock()
    while True:
        doc = read()
        ok, problems = check_running(doc, expected, since)
        if ok or clock() - started >= timeout:
            return ok, problems, doc
        sleep(interval)


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
        ("link_baud", "" if summary["link_baud"] is None else summary["link_baud"]),
        ("fastbaud", raise_outcome(summary)),
    ]
    for ident in FULL_WIDTH_IDS:
        pairs.append((f"len_{ident}", (summary.get("min_lengths") or {}).get(ident, 0)))
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


def _parse_env_line(line):
    env = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            env[k] = v
    return env


def _opt_float(text):
    try:
        return float(text) if text not in (None, "", "none") else None
    except ValueError:
        return None


def _cmd_telemetry(argv):
    if len(argv) < 2:
        print("usage: driveway_verdict.py telemetry PRESET ENV_LINE [RATE [RECONNECT_S]]",
              file=sys.stderr)
        return 2
    preset_name = argv[0]
    env = _parse_env_line(argv[1])
    rate = _opt_float(argv[2]) if len(argv) > 2 else None
    reconnect = _opt_float(argv[3]) if len(argv) > 3 else None
    fraction, leg_hold, cycle, measured = expected_telemetry(
        preset_name, env, rate, reconnect)
    counted = reconnect is not None and preset_name != PRESET_FALLBACK
    _print_kv([
        # A whole number, capped at 99 -- see whole_percent().
        ("pct", whole_percent(fraction)),
        ("leg_hold", f"{leg_hold:.1f}"),
        ("cycle", f"{cycle:.0f}"),
        ("rate_measured", 1 if measured else 0),
        ("reconnect_counted", 1 if counted else 0),
    ])
    return 0


def _capture_block(path):
    """(summary.json fragment, summarize() result) for one capture, or
    (None, None) if it never ran. A capture this module cannot read at all
    is recorded as such rather than losing the whole summary.json."""
    if not path:
        return None, None
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        doc = load_capture(path)
        summary = summarize(doc)
    except Exception as e:                                    # noqa: BLE001
        return {"name": name, "error": f"{type(e).__name__}: {e}",
                "passed": False}, None
    return {
        "name": name,
        "note": doc.get("note", ""),
        "frames": summary["frames"],
        "span": summary["span"],
        "ids_seen": summary["ids_seen"],
        "gear": summary["gear"],
        "link_baud": summary["link_baud"],
        "fastbaud": summary["fastbaud"],
    }, summary


def _num(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


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
    ap.add_argument("--exit-a", default="")
    ap.add_argument("--exit-b", default="")
    ap.add_argument("--result-a", default="")
    ap.add_argument("--result-b", default="")
    ap.add_argument("--reconnect-a", default="")
    ap.add_argument("--reconnect-b", default="")
    ap.add_argument("--preset", default="")
    ap.add_argument("--env", default="")
    ap.add_argument("--applied", type=int, default=0)
    ap.add_argument("--install-state", default="")
    ap.add_argument("--install-verified", default="")
    ap.add_argument("--install-detail", default="")
    ap.add_argument("--other-confs", default="")
    args = ap.parse_args(argv)

    cap_a_block, cap_a_summary = _capture_block(args.capture_a)
    if cap_a_summary is not None:
        passed_a, why_a = verdict_a(cap_a_summary)
        cap_a_block["passed"] = passed_a
        cap_a_block["why"] = why_a

    cap_b_block, cap_b_summary = _capture_block(args.capture_b)
    if cap_b_summary is not None:
        passed_b, why_b = verdict_b(cap_b_summary)
        cap_b_block["passed"] = passed_b
        cap_b_block["why"] = why_b

    # capture_a_exit / capture_a_result (below) exist because a capture that
    # exited non-zero saved no file: "capture_a": null alone would read as
    # "it never happened".

    telemetry = None
    if args.preset and args.env:
        env = _parse_env_line(args.env)
        rate = None
        if cap_a_summary and cap_a_summary["span"] > 0:
            rate = cap_a_summary["frames"] / cap_a_summary["span"]
        # The fast-link preset with CAF0 is judged on capture B's link, the
        # other on capture A's.
        reconnect = _num(args.reconnect_b if args.preset == PRESET_TELEMETRY_FIRST_CAF0
                         and args.reconnect_b != "" else args.reconnect_a)
        frac, leg_hold, cycle, measured = expected_telemetry(
            args.preset, env, rate, reconnect)
        telemetry = {"pct": whole_percent(frac), "leg_hold_s": round(leg_hold, 1),
                     "cycle_s": round(cycle), "rate_measured": measured,
                     "reconnect_counted": (reconnect is not None
                                           and args.preset != PRESET_FALLBACK),
                     "label": "estimated"}

    install = None
    if args.install_state:
        verified = {"1": True, "0": False}.get(args.install_verified)
        install = {"state": args.install_state, "verified": verified,
                   "detail": args.install_detail or None}

    doc = {
        "stamp": args.stamp,
        "mode": args.mode,
        "dry_run": bool(args.dry_run),
        "parked_flag": bool(args.parked),
        "aborted_reason": args.aborted or None,
        "capture_a": cap_a_block,
        "capture_b": cap_b_block,
        "capture_a_exit": _num(args.exit_a),
        "capture_b_exit": _num(args.exit_b),
        "capture_a_result": args.result_a or None,
        "capture_b_result": args.result_b or None,
        "gauges_back_a_seconds": _num(args.reconnect_a),
        "gauges_back_b_seconds": _num(args.reconnect_b),
        "preset": args.preset or None,
        "preset_env": args.env or None,
        "applied": bool(args.applied),
        "install": install,
        "other_active_confs": [c for c in args.other_confs.split(",") if c],
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


def _cmd_now(argv):
    print(f"{time.time():.6f}")
    return 0


def _cmd_yield_grace(argv):
    print(f"{yield_grace():g}")
    return 0


def _cmd_live_state(argv):
    """alive: the daemon published something recently (drivelog's own
    _live(), the same test the recorder uses). A "yielded" snapshot counts:
    the daemon is alive and has only lent the adapter out."""
    live = drivelog._live()
    doc = live if live is not None else {}
    values = doc.get("values") or {}
    rpm = values.get("RPM")
    _print_kv([
        ("alive", 1 if live is not None else 0),
        ("connected", 1 if doc.get("connected") is True else 0),
        ("status", doc.get("status") or ""),
        ("rpm", "" if rpm is None else rpm),
    ])
    return 0


def _cmd_wait_live(argv):
    if len(argv) < 3 or argv[0] not in ("connected", "released"):
        print("usage: driveway_verdict.py wait-live connected|released SINCE TIMEOUT",
              file=sys.stderr)
        return 2
    ok, seconds, doc = wait_live(argv[0], float(argv[1]), float(argv[2]))
    doc = doc if isinstance(doc, dict) else {}
    _print_kv([
        ("ok", 1 if ok else 0),
        ("seconds", f"{max(0.0, seconds):.1f}"),
        ("connected", 1 if doc.get("connected") is True else 0),
        ("status", doc.get("status") or ""),
    ])
    return 0


def _cmd_latest_capture(argv):
    if len(argv) < 2:
        print("usage: driveway_verdict.py latest-capture NOTE SINCE", file=sys.stderr)
        return 2
    _print_kv([("path", find_capture(argv[0], float(argv[1])))])
    return 0


def _cmd_verify_running(argv):
    if len(argv) < 3:
        print("usage: driveway_verdict.py verify-running ENV_LINE SINCE TIMEOUT",
              file=sys.stderr)
        return 2
    ok, problems, doc = wait_running(_parse_env_line(argv[0]), float(argv[1]),
                                     float(argv[2]))
    _print_kv([("ok", 1 if ok else 0), ("detail", "; ".join(problems))])
    if ok and isinstance(doc, dict):
        _print_kv([("between", f"{doc.get('between'):g}"),
                   ("leg_lines", doc.get("leg_lines")),
                   ("quiet", f"{doc.get('quiet'):g}"),
                   ("end_on_overflow", 1 if doc.get("end_on_overflow") else 0)])
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
    for name, fn in (("now", _cmd_now), ("yield-grace", _cmd_yield_grace),
                     ("live-state", _cmd_live_state), ("wait-live", _cmd_wait_live),
                     ("latest-capture", _cmd_latest_capture),
                     ("verify-running", _cmd_verify_running)):
        if cmd == name:
            return fn(rest)
    print(f"driveway_verdict.py: unknown command: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
