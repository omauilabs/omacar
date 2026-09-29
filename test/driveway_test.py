#!/usr/bin/env python3
"""tools/driveway_verdict.py, pinned against synthetic captures and files.

Every threshold the driveway check runs against a real capture is checked
here first against one built by hand, because the box this suite runs on
has no adapter and no car (see test/all.sh). Nothing here opens a port. The
shell script itself is exercised, against a stand-in car, by
test/driveway_e2e_test.py.
"""

import json
import os
import sys
import tempfile
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import driveway_verdict as dv  # noqa: E402

fails = 0


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def head(msg):
    print(f"\n  {msg}\n")


def frames(ident, data, count, dt=0.01, t0=0.0):
    return [{"t": t0 + i * dt, "id": ident, "data": data} for i in range(count)]


# ------------------------------------------------------------- capture A
head("capture A -- the fast link alone")

_a_pass_doc = {"started": 1000.0,
               "raw": frames("161", "0102030405060708", 25000, dt=20.0 / 25000)}
_a_pass = dv.summarize(_a_pass_doc)
check("25,000 frames over 20s: frame count", _a_pass["frames"], 25000)
check("25,000 frames over 20s: span is about 20s", round(_a_pass["span"]), 20)
_passed, _why = dv.verdict_a(_a_pass)
check("25,000 frames over 20s passes", _passed, True)

_a_fail_doc = {"started": 1000.0,
               "raw": frames("161", "0102030405060708", 120, dt=0.01)}
_a_fail = dv.summarize(_a_fail_doc)
check("120 frames: frame count", _a_fail["frames"], 120)
_passed, _why = dv.verdict_a(_a_fail)
check("120 frames fails", _passed, False)

# A boundary case: exactly at the frame floor must not pass -- the doc says
# "well over 20,000", and ">" rather than ">=" is what makes that literal.
_a_boundary_doc = {"started": 1000.0,
                   "raw": frames("161", "00", dv.A_MIN_FRAMES, dt=20.0 / dv.A_MIN_FRAMES)}
_passed, _why = dv.verdict_a(dv.summarize(_a_boundary_doc))
check("exactly 20,000 frames (not 'well over') fails", _passed, False)

# ------------------------------------------------------------- capture B
head("capture B -- the fast link plus ATCAF0, and it must be a real capture (I4)")

_caf0_ids = ("097", "1AA", "1CF", "374")


def _b_doc(overrides=None, drop=(), per_id=4000, span=19.9, mixed=False):
    """A B capture. per_id * 6 frames (four identifiers, 161 and 164).
    `mixed` makes ONE 161 frame full width among truncated ones."""
    raw = []
    dt = span / per_id
    for ident in _caf0_ids:
        if ident in drop:
            continue
        raw += frames(ident, "00000000", per_id, dt=dt)
    lens = {"161": 8, "164": 8}
    if overrides:
        lens.update(overrides)
    raw += frames("161", "00" * lens["161"], per_id, dt=dt)
    raw += frames("164", "00" * lens["164"], per_id, dt=dt)
    if mixed:
        raw.append({"t": 1.0, "id": "161", "data": "00" * 8})
    return {"started": 1000.0, "raw": raw}


_b_pass = dv.summarize(_b_doc())
check("the doc is over 20,000 frames (a real capture)", _b_pass["frames"] > 20000, True)
_passed, _why = dv.verdict_b(_b_pass)
check("all four identifiers, 161/164 at 8 bytes, over 20,000 frames and 15s passes",
      _passed, True)

_b_fail_short = dv.summarize(_b_doc(overrides={"161": 6, "164": 5}))
_passed, _why = dv.verdict_b(_b_fail_short)
check("161 at 6 bytes and 164 at 5 bytes fails", _passed, False)
check("...and says which ones", "161=6B" in _why and "164=5B" in _why, True)

_b_fail_missing = dv.summarize(_b_doc(drop=("374",)))
_passed, _why = dv.verdict_b(_b_fail_missing)
check("a missing identifier (374) fails", _passed, False)
check("...and names it", "374" in _why, True)

_b_thin = dv.summarize(_b_doc(per_id=100))
_passed, _why = dv.verdict_b(_b_thin)
check("everything present but only 600 frames (an overflow after a second) fails",
      _passed, False)
check("...and says how few", "only 600 frames" in _why, True)

_b_brief = dv.summarize(_b_doc(per_id=4000, span=5.0))
_passed, _why = dv.verdict_b(_b_brief)
check("enough frames but a span under 15s fails", _passed, False)

_b_mixed = dv.summarize(_b_doc(overrides={"161": 6}, mixed=True))
check("the LONGEST 161 frame is 8 bytes...", _b_mixed["lengths"]["161"], 8)
check("...but the shortest is 6", _b_mixed["min_lengths"]["161"], 6)
_passed, _why = dv.verdict_b(_b_mixed)
check("one full-width 161 among truncated ones is not a pass", _passed, False)

# ------------------------------------------------------------------ gear
head("gear values (0x191 byte 0) -- information only")

_gear_doc = {"started": 1000.0,
             "raw": frames("191", "01000000", 42) + frames("191", "08000000", 3)}
_gear_summary = dv.summarize(_gear_doc)
check("0x01 (P) counted", _gear_summary["gear"].get("01"), 42)
check("0x08 (D) counted", _gear_summary["gear"].get("08"), 3)
_report = dv.gear_report(_gear_summary)
check("P is called out by name, most-heard first", _report[0], "01=42 (P)")
check("D is reported too", _report[1], "08=3")

# ---------------------------------------------------------- the preset table
head("the preset table's three rows (I4: both fast rows also end on overflow)")

_CAF0_ROW = ("OMACAR_FASTBAUD=1 OMACAR_CAF0=1 OMACAR_DRIVELOG_LEG_LINES=10000 "
             "OMACAR_DRIVELOG_BETWEEN=240 OMACAR_DRIVELOG_END_ON_OVERFLOW=1 "
             "OMACAR_DRIVELOG_QUIET=10")
_FIRST_ROW = _CAF0_ROW.replace(" OMACAR_CAF0=1", "")
_FALLBACK_ROW = ("OMACAR_DRIVELOG_END_ON_OVERFLOW=1 OMACAR_DRIVELOG_QUIET=10 "
                 "OMACAR_DRIVELOG_BETWEEN=240")

_name, _env = dv.choose_preset(True, True)
check("A pass, B pass -> telemetry-first-caf0", _name, dv.PRESET_TELEMETRY_FIRST_CAF0)
check("...the whole row, in order", dv.env_line(_env), _CAF0_ROW)

_name, _env = dv.choose_preset(True, False)
check("A pass, B fail -> telemetry-first", _name, dv.PRESET_TELEMETRY_FIRST)
check("...the same row without CAF0", dv.env_line(_env), _FIRST_ROW)
check("...no CAF0 in this row", "OMACAR_CAF0" in _env, False)

_name, _env = dv.choose_preset(False, False)
check("A fail -> fallback, regardless of B", _name, dv.PRESET_FALLBACK)
check("...unchanged", dv.env_line(_env), _FALLBACK_ROW)

_name, _env = dv.choose_preset(False, True)
check("A fail -> fallback even if B's verdict says pass (B never ran)",
      _name, dv.PRESET_FALLBACK)

check("preset-by-name round-trips telemetry-first-caf0",
      dv.preset_env_by_name(dv.PRESET_TELEMETRY_FIRST_CAF0),
      dv.choose_preset(True, True)[1])
check("preset-by-name round-trips telemetry-first",
      dv.preset_env_by_name(dv.PRESET_TELEMETRY_FIRST),
      dv.choose_preset(True, False)[1])
check("preset-by-name round-trips fallback",
      dv.preset_env_by_name(dv.PRESET_FALLBACK),
      dv.choose_preset(False, False)[1])

# -- <q> stays inside lib/drivelog.py's bounds -----------------------------
head("QUIET stays inside lib/drivelog.py's bounds")

import drivelog  # noqa: E402

_lo, _hi = drivelog._QUIET_BOUNDS
_q = dv.fallback_quiet_seconds()
check("<q> is inside (or at) drivelog's own floor/ceiling", _lo <= _q <= _hi, True)
check("<q> is 10s today, since 10 is inside today's bounds", _q, 10.0)
for _row in (dv.choose_preset(True, True), dv.choose_preset(True, False),
             dv.choose_preset(False, False)):
    check(f"{_row[0]}: its QUIET is one drivelog would accept",
          _lo <= float(_row[1]["OMACAR_DRIVELOG_QUIET"]) <= _hi, True)

# --------------------------------------------------- what the preset really does
head("every row's numbers are ones lib/drivelog.py's own reader accepts, untouched")

_saved = {k: os.environ.get(k) for k in
          ("OMACAR_DRIVELOG_BETWEEN", "OMACAR_DRIVELOG_LEG_LINES",
           "OMACAR_DRIVELOG_QUIET", "OMACAR_DRIVELOG_END_ON_OVERFLOW")}
try:
    for _row in (dv.choose_preset(True, True), dv.choose_preset(False, False)):
        for _k in _saved:
            os.environ.pop(_k, None)
        os.environ.update({k: v for k, v in _row[1].items() if k.startswith("OMACAR_DRIVELOG")})
        _ov = drivelog.read_overrides()
        check(f"{_row[0]}: nothing fell back to a default (no notes)", _ov["notes"] != []
              and all("outside" not in n and "not a number" not in n for n in _ov["notes"]),
              True)
        _want = dv.expected_running(_row[1])
        check(f"{_row[0]}: expected_running() agrees with drivelog.read_overrides()",
              (_ov["between"], _ov["leg_lines"], _ov["quiet"], _ov["end_on_overflow"]),
              (_want["between"], _want["leg_lines"], _want["quiet"], _want["end_on_overflow"]))
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# ---------------------------------------------------------- telemetry share
head("expected telemetry share -- the doc's own leg-hold / cycle method (I5)")

_frac, _hold, _cycle, _measured = dv.expected_telemetry(
    dv.PRESET_TELEMETRY_FIRST, dv.choose_preset(True, False)[1])
check("telemetry-first, unmeasured, is close to the doc's own ~98% figure",
      round(_frac * 100), 98)
check("...and says the frame rate was not measured", _measured, False)

_frac2, _hold2, _cycle2, _measured2 = dv.expected_telemetry(
    dv.PRESET_TELEMETRY_FIRST, dv.choose_preset(True, False)[1], measured_rate=1900.0)
check("...but the same figure with today's own frame rate says measured=True",
      _measured2, True)
check("...and 1,900/s reproduces the doc's own number", round(_frac2 * 100), round(_frac * 100))

_frac_rc, _h_rc, _c_rc, _ = dv.expected_telemetry(
    dv.PRESET_TELEMETRY_FIRST, dv.choose_preset(True, False)[1],
    measured_rate=1900.0, reconnect_seconds=10.0)
check("a measured reconnect time lowers the share",
      _frac_rc < _frac2, True)
check("...by the seconds over the cycle (the gauges are down that much longer)",
      round(_frac2 - _frac_rc, 4), round(10.0 / _c_rc, 4))
check("...and the cycle itself is still leg + gap", round(_c_rc, 3), round(_cycle2, 3))

_frac3, _hold3, _cycle3, _measured3 = dv.expected_telemetry(
    dv.PRESET_FALLBACK, dv.choose_preset(False, False)[1])
check("fallback telemetry share is high (the whole point of ending on overflow)",
      _frac3 > 0.95, True)
check("fallback is never reported as measured -- nothing at plain baud ran today",
      _measured3, False)
_frac3b = dv.expected_telemetry(dv.PRESET_FALLBACK, dv.choose_preset(False, False)[1],
                                measured_rate=1900.0, reconnect_seconds=30.0)[0]
check("a reconnect time taken on the fast link is not applied to the fallback",
      _frac3b, _frac3)

check("a share is shown as a whole percentage", dv.whole_percent(0.9683), 97)
check("...and is never 100 (an estimate cannot promise that)", dv.whole_percent(0.9996), 99)

# ------------------------------------------------- the gauges are back (C1)
head("'gauges back' needs connected, not handed over, and a `t` after the capture (C1)")

_SINCE = 1000.0
_handover = {"connected": False, "status": "yielded", "handover": True, "t": 1005.0}
check("a hand-over snapshot with a fresh `t` is NOT the gauges being back",
      dv.gauges_back(_handover, _SINCE), False)
check("connected but `t` from before the capture is not back either",
      dv.gauges_back({"connected": True, "t": 999.0}, _SINCE), False)
check("connected, and a status of yielded, is not back",
      dv.gauges_back({"connected": True, "status": "yielded", "t": 1005.0}, _SINCE), False)
check("connected with a `t` after the capture is back",
      dv.gauges_back({"connected": True, "t": 1005.0}, _SINCE), True)
check("connected is exactly True (a truthy string is not)",
      dv.gauges_back({"connected": "yes", "t": 1005.0}, _SINCE), False)
check("no live.json is not back", dv.gauges_back(None, _SINCE), False)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


_clk = Clock()
_docs = []


def _reader():
    # The daemon: hand-over snapshots (fresh `t` every poll) until 6s after the
    # capture returned, then connected samples.
    if _clk.t - _SINCE < 6.0:
        return {"connected": False, "status": "yielded", "t": _clk.t}
    return {"connected": True, "t": _clk.t}


_ok, _secs, _doc = dv.wait_live("connected", _SINCE, 30, read=_reader,
                                clock=_clk.now, sleep=_clk.sleep)
check("waiting through hand-over snapshots ends when it is really connected", _ok, True)
check("...and reports how long that took, from the capture returning (about 6s)",
      6.0 <= _secs < 7.0, True)

_clk = Clock()
_ok, _secs, _doc = dv.wait_live(
    "connected", _SINCE, 3, read=lambda: {"connected": False, "status": "yielded", "t": _clk.t},
    clock=_clk.now, sleep=_clk.sleep)
check("hand-over snapshots forever (S5) time out instead of passing", _ok, False)
check("...and the last thing it saw is returned for the message",
      (_doc or {}).get("status"), "yielded")

_now = 2000.0
check("no live.json: nothing holds the port", dv.port_released(None, _SINCE, _now), True)
check("a stale live.json (no daemon publishing): nothing holds the port",
      dv.port_released({"connected": False, "t": _now - 500, "status": "yielded"},
                       _SINCE, _now), True)
check("a fresh 'yielded' snapshot: a leg holds the port",
      dv.port_released({"connected": False, "t": _now - 1, "status": "yielded"},
                       _SINCE, _now), False)
check("a fresh, connected snapshot after `since`: released",
      dv.port_released({"connected": True, "t": _now - 1}, _SINCE, _now), True)
check("a car that is off ('waiting', no `t`) is not a leg in flight",
      dv.port_released({"connected": False, "status": "waiting for the car"}, _SINCE, _now),
      True)

check("the hung-capture grace is read from lib/connect.py, not hard-coded",
      dv.yield_grace(), float(dv.connect.YIELD_GRACE))

# ---------------------------------------- only the capture THIS run saved (I2)
head("a capture from before this run is never picked up (I2)")

_tmp = tempfile.mkdtemp(prefix="dw-caps-")
try:
    def _save(name, note, age):
        path = os.path.join(_tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"started": time.time() - age, "note": note, "raw": []}, f)
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    _old = _save("20260928-100000.json", "fastbaud-test", 86400)
    _other = _save("20260929-100000.json", "caf0-test", 5)
    _leg = _save("20260929-235959-drive.json", "fastbaud-test", 3)
    _t0 = time.time() - 30
    check("only an older capture with that note exists: none is found",
          dv.find_capture("fastbaud-test", _t0, _tmp), "")
    _new = _save("20260929-100200.json", "fastbaud-test", 1)
    check("this run's own capture is found", dv.find_capture("fastbaud-test", _t0, _tmp), _new)
    check("an unreadable file is skipped, not a crash",
          (open(os.path.join(_tmp, "20260929-100300.json"), "w").write("{nope"),
           dv.find_capture("fastbaud-test", _t0, _tmp))[1], _new)
    check("a capture with another note is not returned",
          dv.find_capture("caf0-test", _t0, _tmp), _other)
    check("a drive leg is never opened or returned",
          _leg in [dv.find_capture("fastbaud-test", _t0, _tmp)], False)
    check("a missing folder is just 'none'", dv.find_capture("x", 0, _tmp + "-nope"), "")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# ------------------------------------- what the recorder says it is running (I3)
head("checking a restart from the recorder's own status file (I3)")

_env = dv.choose_preset(True, True)[1]
_want = dv.expected_running(_env)
check("the numbers expected from the preset",
      (_want["between"], _want["leg_lines"], _want["quiet"], _want["end_on_overflow"]),
      (240.0, 10000, 10.0, True))
check("an unset variable expects the recorder's own default (remove-preset)",
      dv.expected_running({}),
      {"between": drivelog.BETWEEN_LEGS, "leg_lines": dv.listenlib.DEFAULT_LIMIT,
       "quiet": drivelog.QUIET_TIMEOUT, "end_on_overflow": False})

_good = {"started": 2001.0, "between": 240.0, "leg_lines": 10000, "quiet": 10.0,
         "end_on_overflow": True}
check("a status from a supervisor started after the restart, with the numbers, matches",
      dv.check_running(_good, _want, 2000.0), (True, []))
_old_sup = dict(_good, started=1990.0)
_r = dv.check_running(_old_sup, _want, 2000.0)
check("the same numbers from the OLD supervisor do not", _r[0], False)
check("...and it says why", "old supervisor" in _r[1][0], True)
_r = dv.check_running(dict(_good, between=90.0), _want, 2000.0)
check("a wrong number does not match, and is named",
      (_r[0], any("between" in p for p in _r[1])), (False, True))
_r = dv.check_running(dict(_good, end_on_overflow=False), _want, 2000.0)
check("end_on_overflow off does not match", _r[0], False)
check("no status file yet does not match", dv.check_running(None, _want, 2000.0)[0], False)

_clk = Clock()
_reads = iter([None, dict(_good, started=1990.0), _good])
_okw, _problems, _d = dv.wait_running(_env, 2000.0, 10, read=lambda: next(_reads),
                                      clock=_clk.now, sleep=_clk.sleep, interval=1.0)
check("polling waits for the new supervisor's file", (_okw, _d), (True, _good))

# ---------------------------------------------------------------- the module
head("the module reads files and nothing else")

_src = open(os.path.join(ROOT, "tools", "driveway_verdict.py"), encoding="utf-8").read()
check("it never spawns a process", any(w in _src for w in ("subprocess", "os.system", "Popen")),
      False)

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every driveway_verdict check holds\n")
