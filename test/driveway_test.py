#!/usr/bin/env python3
"""tools/driveway_verdict.py, pinned against synthetic captures.

Every threshold the driveway check runs against a real capture is checked
here first against one built by hand, because the box this suite runs on
has no adapter and no car (see test/all.sh). Nothing here opens a port.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
                    "raw": frames("161", "00", A_MIN_FRAMES := dv.A_MIN_FRAMES, dt=20.0 / dv.A_MIN_FRAMES)}
_passed, _why = dv.verdict_a(dv.summarize(_a_boundary_doc))
check("exactly 20,000 frames (not 'well over') fails", _passed, False)

# ------------------------------------------------------------- capture B
head("capture B -- the fast link plus ATCAF0")

_caf0_ids = ("097", "1AA", "1CF", "374")


def _b_doc(overrides=None, drop=()):
    raw = []
    for ident in _caf0_ids:
        if ident in drop:
            continue
        raw += frames(ident, "00000000", 5)
    lens = {"161": 8, "164": 8}
    if overrides:
        lens.update(overrides)
    raw += frames("161", "00" * lens["161"], 5)
    raw += frames("164", "00" * lens["164"], 5)
    return {"started": 1000.0, "raw": raw}


_b_pass = dv.summarize(_b_doc())
_passed, _why = dv.verdict_b(_b_pass)
check("all four identifiers present, 161/164 at 8 bytes passes", _passed, True)

_b_fail_short = dv.summarize(_b_doc(overrides={"161": 6, "164": 5}))
_passed, _why = dv.verdict_b(_b_fail_short)
check("161 at 6 bytes and 164 at 5 bytes fails", _passed, False)
check("...and says which ones", "161=6B" in _why and "164=5B" in _why, True)

_b_fail_missing = dv.summarize(_b_doc(drop=("374",)))
_passed, _why = dv.verdict_b(_b_fail_missing)
check("a missing identifier (374) fails", _passed, False)
check("...and names it", "374" in _why, True)

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
head("the preset table's three rows")

_name, _env = dv.choose_preset(True, True)
check("A pass, B pass -> telemetry-first-caf0", _name, dv.PRESET_TELEMETRY_FIRST_CAF0)
check("...FASTBAUD on", _env.get("OMACAR_FASTBAUD"), "1")
check("...CAF0 on", _env.get("OMACAR_CAF0"), "1")
check("...leg cap 10000", _env.get("OMACAR_DRIVELOG_LEG_LINES"), "10000")
check("...gap 240", _env.get("OMACAR_DRIVELOG_BETWEEN"), "240")

_name, _env = dv.choose_preset(True, False)
check("A pass, B fail -> telemetry-first", _name, dv.PRESET_TELEMETRY_FIRST)
check("...FASTBAUD on", _env.get("OMACAR_FASTBAUD"), "1")
check("...no CAF0 in this row", "OMACAR_CAF0" in _env, False)
check("...leg cap 10000", _env.get("OMACAR_DRIVELOG_LEG_LINES"), "10000")
check("...gap 240", _env.get("OMACAR_DRIVELOG_BETWEEN"), "240")

_name, _env = dv.choose_preset(False, False)
check("A fail -> fallback, regardless of B", _name, dv.PRESET_FALLBACK)
check("...ends on overflow", _env.get("OMACAR_DRIVELOG_END_ON_OVERFLOW"), "1")
check("...quiet timeout is the chosen <q>", _env.get("OMACAR_DRIVELOG_QUIET"),
      f"{dv.fallback_quiet_seconds():g}")
check("...gap 240", _env.get("OMACAR_DRIVELOG_BETWEEN"), "240")

_name, _env = dv.choose_preset(False, True)
check("A fail -> fallback even if B's verdict says pass (B never ran)",
      _name, dv.PRESET_FALLBACK)

check("preset-by-name round-trips telemetry-first-caf0",
      dv.preset_env_by_name(dv.PRESET_TELEMETRY_FIRST_CAF0),
      dv.choose_preset(True, True)[1])
check("preset-by-name round-trips fallback",
      dv.preset_env_by_name(dv.PRESET_FALLBACK),
      dv.choose_preset(False, False)[1])

# -- <q> stays inside lib/drivelog.py's own bounds -------------------------
head("the fallback quiet timeout <q> stays inside lib/drivelog.py's bounds")

import drivelog  # noqa: E402

_lo, _hi = drivelog._QUIET_BOUNDS
_q = dv.fallback_quiet_seconds()
check("<q> is inside (or at) drivelog's own floor/ceiling", _lo <= _q <= _hi, True)
check("<q> is 10s today, since 10 is inside today's bounds", _q, 10.0)

# ---------------------------------------------------------- telemetry share
head("expected telemetry share -- the doc's own leg-hold / cycle method")

_frac, _hold, _cycle, _measured = dv.expected_telemetry(
    dv.PRESET_TELEMETRY_FIRST, dv.choose_preset(True, False)[1])
check("telemetry-first, unmeasured, is close to the doc's own ~98% figure",
      round(_frac * 100), 98)
check("...and says it was not measured this session", _measured, False)

_frac2, _hold2, _cycle2, _measured2 = dv.expected_telemetry(
    dv.PRESET_TELEMETRY_FIRST, dv.choose_preset(True, False)[1], measured_rate=1900.0)
check("...but the same figure with today's own measured rate says measured=True",
      _measured2, True)
check("...and 1,900/s reproduces the doc's own number", round(_frac2 * 100), round(_frac * 100))

_frac3, _hold3, _cycle3, _measured3 = dv.expected_telemetry(
    dv.PRESET_FALLBACK, dv.choose_preset(False, False)[1])
check("fallback telemetry share is high (the whole point of ending on overflow)",
      _frac3 > 0.95, True)
check("fallback is never reported as measured -- nothing at plain baud ran today",
      _measured3, False)

# ------------------------------------------------- one real end-to-end run
head("tools/driveway-check.sh end to end, against a fake omacar")

# There is no way to run this against a real capture without a real adapter,
# and the bench emulator cannot stand in: it answers diagnostic requests but
# has no notion of monitor mode (see test/listen_test.py's own docstring and
# test/all.sh's comment above the line that runs it), so `omacar listen
# capture` against it would just report a quiet bus, never real frames.
#
# So this fakes `omacar` itself instead -- the same technique
# test/guards_test.py already uses for tools/ima-session.sh's --dry-run
# check, extended so its "listen capture" arm writes a synthetic capture
# file (in the shape lib/listen.py's Capture.save() writes) and nudges
# live.json's timestamp, standing in for the daemon reconnecting. This is a
# real, non-dry-run execution of driveway-check.sh's shell logic end to
# end -- confirm/parked, preflight, the stop/restore trap, both captures,
# the reconnect wait, the verdict, the preset choice, and --apply -- against
# canned data whose pass/fail shape this test controls.
#
# Needs the venv (driveway-check.sh's own omacar_need_env gate asks for one
# for any non-dry-run action, whether or not that action actually touches
# python-obd) -- skipped with a note if this box never ran `omacar setup`,
# the same way test/all.sh itself skips test/prospect_test.py.
_VENV_PY = os.path.join(os.environ.get("XDG_DATA_HOME") or
                        os.path.expanduser("~/.local/share"), "omacar", "venv", "bin", "python")

if not os.access(_VENV_PY, os.X_OK):
    ok("skipped -- no venv on this box; run: omacar setup")
else:
    _DW_SH = os.path.join(ROOT, "tools", "driveway-check.sh")

    def _fake_omacar_script(calls_log, drive_status_text="    waiting   for the car"):
        # Written once per run so each scenario gets its own calls log and
        # (if a scenario ever needs one) its own status text.
        return f"""#!/bin/bash
echo "$@" >> {calls_log!r}
case "$1 $2" in
  "drive status") echo {drive_status_text!r}; exit 0 ;;
  "drive off") exit 0 ;;
  "drive on") exit 0 ;;
  "listen capture")
    note=""
    args=("$@")
    for ((i=0; i<${{#args[@]}}; i++)); do
      if [[ "${{args[$i]}}" == "--note" ]]; then note="${{args[$((i+1))]}}"; fi
    done
    "{_VENV_PY}" - "$note" "$OMACAR_STATE_FOR_TEST" <<'PYEOF'
import json, os, sys, time
note, state_dir = sys.argv[1], sys.argv[2]
captures_dir = os.path.join(state_dir, "captures")
os.makedirs(captures_dir, exist_ok=True)
raw = []
if note == "fastbaud-test":
    n, span = 25413, 20.05
    for i in range(n):
        raw.append({{"t": round(i * span / n, 4), "id": "161", "data": "0102030405060708"}})
elif note == "caf0-test":
    for i in range(200):
        t = round(i * 0.1, 3)
        for ident in ("097", "1AA", "1CF", "374"):
            raw.append({{"t": t, "id": ident, "data": "0011223344556677"}})
        raw.append({{"t": t, "id": "161", "data": "05BF05BF20081122"}})
        raw.append({{"t": t, "id": "164", "data": "0400003C72AABBCC"}})
doc = {{"started": time.time(), "note": note, "raw": raw}}
name = time.strftime("%Y%m%d-%H%M%S") + "-" + note + "-" + str(int(time.time() * 1000) % 1000)
with open(os.path.join(captures_dir, name + ".json"), "w", encoding="utf-8") as f:
    json.dump(doc, f)
live_path = os.path.join(state_dir, "live.json")
try:
    with open(live_path, encoding="utf-8") as f:
        live = json.load(f)
except (OSError, ValueError):
    live = {{}}
live["t"] = time.time()
live["connected"] = True
live["simulated"] = False
live.setdefault("values", {{}})["RPM"] = 850
with open(live_path, "w", encoding="utf-8") as f:
    json.dump(live, f)
PYEOF
    exit 0
    ;;
esac
exit 0
"""

    _tmp = tempfile.mkdtemp(prefix="omacar-driveway-e2e-")
    try:
        _bin_dir = os.path.join(_tmp, "bin")
        _state_dir = os.path.join(_tmp, "state")
        _dropin_dir = os.path.join(_tmp, "dropin")
        os.makedirs(_bin_dir)
        os.makedirs(os.path.join(_state_dir, "omacar"))
        os.makedirs(_dropin_dir)

        _calls_log = os.path.join(_tmp, "omacar-calls.log")
        _fake_omacar = os.path.join(_bin_dir, "omacar")
        with open(_fake_omacar, "w", encoding="utf-8") as f:
            f.write(_fake_omacar_script(_calls_log))
        os.chmod(_fake_omacar, 0o755)

        # Seed live.json fresh, so the preflight step passes.
        with open(os.path.join(_state_dir, "omacar", "live.json"), "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "connected": True, "simulated": False,
                       "values": {"RPM": 850}}, f)

        env = dict(os.environ)
        env["OMACAR_BIN"] = _fake_omacar
        env["XDG_STATE_HOME"] = _state_dir
        env["OMACAR_DROPIN_DIR"] = _dropin_dir
        env["OMACAR_STATE_FOR_TEST"] = os.path.join(_state_dir, "omacar")

        proc = subprocess.run(["bash", _DW_SH, "--parked", "--apply"], env=env,
                              capture_output=True, text=True, timeout=90, input="")
        check("a real (non-dry-run) run exits 0", proc.returncode, 0)
        check("capture A is judged to pass", "capture A passed" in proc.stdout, True)
        check("capture B is judged to pass", "capture B passed" in proc.stdout, True)
        # "reconnected" and "in Ns" are two ANSI-coloured spans on the same
        # printed line (see wait_reconnect()'s caller in driveway-check.sh),
        # so the escape code between them means the contiguous phrase
        # "reconnected in" never appears literally -- counting the word on
        # its own, once per capture, is what actually holds.
        check("both captures reconnect well inside 30s (printed twice, once each)",
              proc.stdout.count("reconnected") >= 2, True)
        check("the strongest preset is chosen (both captures passed)",
              f"preset:    {dv.PRESET_TELEMETRY_FIRST_CAF0}" in proc.stdout, True)
        check("it reports the preset installed",
              "installed — the recorder is running with it now." in proc.stdout, True)

        conf_path = os.path.join(_dropin_dir, "driveway-preset.conf")
        check("driveway-preset.conf was written", os.path.isfile(conf_path), True)
        if os.path.isfile(conf_path):
            conf_text = open(conf_path, encoding="utf-8").read()
            check("...with OMACAR_FASTBAUD=1", "OMACAR_FASTBAUD=1" in conf_text, True)
            check("...with OMACAR_CAF0=1", "OMACAR_CAF0=1" in conf_text, True)

        calls_text = open(_calls_log, encoding="utf-8").read() if os.path.exists(_calls_log) else ""
        check("omacar was asked to stand down, capture twice, then start again",
              all(s in calls_text for s in
                  ("drive off", "listen capture --seconds 20 --save --note fastbaud-test",
                   "listen capture --seconds 20 --save --note caf0-test", "drive on")),
              True)
        check("driveway-check.sh never called anything but drive/listen on $OMACAR_BIN",
              all(ln.split()[0] in ("drive", "listen") for ln in calls_text.splitlines() if ln),
              True)

        summaries = []
        checks_dir = os.path.join(_state_dir, "omacar", "driveway-checks")
        if os.path.isdir(checks_dir):
            for name in os.listdir(checks_dir):
                p = os.path.join(checks_dir, name, "summary.json")
                if os.path.isfile(p):
                    summaries.append(json.load(open(p, encoding="utf-8")))
        check("exactly one session's summary.json was written", len(summaries), 1)
        if summaries:
            s = summaries[0]
            check("summary.json: mode is test", s.get("mode"), "test")
            check("summary.json: not aborted", s.get("aborted_reason"), None)
            check("summary.json: preset matches", s.get("preset"), dv.PRESET_TELEMETRY_FIRST_CAF0)
            check("summary.json: applied is true", s.get("applied"), True)
            check("summary.json: capture A recorded a pass",
                  (s.get("capture_a") or {}).get("passed"), True)
            check("summary.json: capture B recorded a pass",
                  (s.get("capture_b") or {}).get("passed"), True)
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every driveway_verdict check holds\n")
