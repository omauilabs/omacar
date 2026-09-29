#!/usr/bin/env python3
"""tools/driveway-check.sh, run for real against a stand-in car.

There is no way to run this against a real capture without a real adapter, and
the bench emulator cannot stand in: it answers diagnostic requests but has no
notion of monitor mode (see test/listen_test.py's own docstring). So
OMACAR_BIN and OMACAR_SYSTEMCTL point at two shell stubs that call
test/driveway_fake.py, which writes the files the real daemon, recorder and
`omacar listen capture` would write -- including the hand-over snapshots the
real daemon publishes while it has lent the adapter out, which is the case the
first version of this test could not see.

Every scenario below is a real, non-dry-run execution of the shell script
against its own throwaway XDG_STATE_HOME and drop-in folder. The systemctl stub
means it can never restart a real recorder unit, even on the tablet.

Needs the venv (driveway-check.sh's own omacar_need_env gate asks for one for
any non-dry-run action) -- skipped with a note if this machine never ran
`omacar setup`, the same way test/all.sh skips test/prospect_test.py.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "tools", "driveway-check.sh")
FAKE = os.path.join(ROOT, "test", "driveway_fake.py")

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


VENV_PY = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                       "omacar", "venv", "bin", "python")
if not os.access(VENV_PY, os.X_OK):
    head("tools/driveway-check.sh end to end, against a fake car")
    ok("skipped -- no venv on this machine; run: omacar setup")
    sys.exit(0)

ANSI = re.compile(r"\x1b\[[0-9;]*m")

OMACAR_STUB = """#!/bin/bash
echo "$*" >> "$FAKE_CALLS"
case "$1 $2" in
  "drive off") : > "$FAKE_OSTATE/drivelog-off"; exec "$FAKE_PY" "$FAKE_HELPER" drive-off ;;
  "drive on") rm -f "$FAKE_OSTATE/drivelog-off"; exit 0 ;;
  "drive status") echo "${FAKE_STATUS_TEXT:-    waiting   for the car}"; exit 0 ;;
  "listen capture") exec "$FAKE_PY" "$FAKE_HELPER" capture "$@" ;;
esac
exit 0
"""

SYSTEMCTL_STUB = """#!/bin/bash
echo "$*" >> "$FAKE_SYSTEMCTL_CALLS"
case "$2" in
  daemon-reload) exit "${FAKE_RELOAD_RC:-0}" ;;
  restart)
    if [[ "${FAKE_RESTART_RC:-0}" != "0" ]]; then exit "$FAKE_RESTART_RC"; fi
    exec "$FAKE_PY" "$FAKE_HELPER" restart ;;
esac
exit 0
"""

ENV_LINE_CAF0 = ("OMACAR_FASTBAUD=1 OMACAR_CAF0=1 OMACAR_DRIVELOG_LEG_LINES=10000 "
                 "OMACAR_DRIVELOG_BETWEEN=240 OMACAR_DRIVELOG_END_ON_OVERFLOW=1 "
                 "OMACAR_DRIVELOG_QUIET=10")
ENV_LINE_FIRST = ENV_LINE_CAF0.replace(" OMACAR_CAF0=1", "")
ENV_LINE_FALLBACK = ("OMACAR_DRIVELOG_END_ON_OVERFLOW=1 OMACAR_DRIVELOG_QUIET=10 "
                     "OMACAR_DRIVELOG_BETWEEN=240")


class Case:
    """One scenario: its own state dir, drop-in dir, stubs and fake car."""

    def __init__(self, name, fake=None, steady="connected", marker=False,
                 dropin=None, script_env=None):
        self.name = name
        self.tmp = tempfile.mkdtemp(prefix=f"dw-{name}-")
        self.state = os.path.join(self.tmp, "state")
        self.ostate = os.path.join(self.state, "omacar")
        self.dropin = os.path.join(self.tmp, "dropin")
        self.bin = os.path.join(self.tmp, "bin")
        for d in (self.ostate, self.dropin, self.bin):
            os.makedirs(d)
        self.calls_log = os.path.join(self.tmp, "omacar-calls.log")
        self.sysctl_log = os.path.join(self.tmp, "systemctl-calls.log")
        self.omacar = os.path.join(self.bin, "omacar")
        self.systemctl = os.path.join(self.bin, "systemctl")
        for path, body in ((self.omacar, OMACAR_STUB), (self.systemctl, SYSTEMCTL_STUB)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            os.chmod(path, 0o755)
        for f in (self.calls_log, self.sysctl_log):
            open(f, "w").close()
        for fname, body in (dropin or {}).items():
            with open(os.path.join(self.dropin, fname), "w", encoding="utf-8") as f:
                f.write(body)
        if marker:
            open(os.path.join(self.ostate, "drivelog-off"), "w").close()

        self.env = dict(os.environ)
        self.env.update({
            "OMACAR_BIN": self.omacar, "OMACAR_SYSTEMCTL": self.systemctl,
            "OMACAR_DROPIN_DIR": self.dropin, "XDG_STATE_HOME": self.state,
            "FAKE_PY": sys.executable, "FAKE_HELPER": FAKE,
            "FAKE_OSTATE": self.ostate, "FAKE_CALLS": self.calls_log,
            "FAKE_SYSTEMCTL_CALLS": self.sysctl_log,
            "FAKE_BACK": "1.0",
            "OMACAR_DRIVEWAY_WAIT_SECS": "8", "OMACAR_DRIVEWAY_GRACE_SECS": "1",
            "OMACAR_DRIVEWAY_PROMPT_SECS": "2", "OMACAR_DRIVEWAY_VERIFY_SECS": "3",
        })
        self.env.update({k: str(v) for k, v in (fake or {}).items()})
        self.env.update({k: str(v) for k, v in (script_env or {}).items()})
        # The daemon, alive and publishing: a steady stream of samples with a
        # fresh `t`, the way lib/daemon.py does it. "yielded" starts the case
        # in the middle of a recorder leg.
        if steady is not None:
            back = "0" if steady == "connected" else "never"
            subprocess.run([sys.executable, FAKE, "spawn", back, "90"], env=self.env)
        time.sleep(0.5)
        self.proc = None
        self.stdout = ""
        self.t = 0.0

    # ---------------------------------------------------------------- running
    def run(self, args, timeout=150):
        t0 = time.time()
        p = subprocess.run(["bash", SH, *args], env=self.env, capture_output=True,
                           text=True, timeout=timeout, input="")
        self.t = time.time() - t0
        self.rc = p.returncode
        self.stdout = ANSI.sub("", p.stdout + p.stderr)
        return self

    def start(self, args, stdin=None):
        """Start it and return at once (for the prompt and signal cases)."""
        self.out_path = os.path.join(self.tmp, "stdout.txt")
        self.out_f = open(self.out_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(["bash", SH, *args], env=self.env, stdout=self.out_f,
                                     stderr=subprocess.STDOUT, stdin=stdin)
        return self.proc

    def finish(self, timeout=90):
        self.timed_out = False
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            self.proc.kill()
            self.proc.wait()
        self.out_f.close()
        self.rc = self.proc.returncode
        with open(self.out_path, encoding="utf-8") as f:
            self.stdout = ANSI.sub("", f.read())

    # ---------------------------------------------------------------- reading
    def calls(self):
        with open(self.calls_log, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def systemctl_calls(self):
        with open(self.sysctl_log, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def conf(self, name="driveway-preset.conf"):
        p = os.path.join(self.dropin, name)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()

    def dropin_files(self):
        return sorted(os.listdir(self.dropin))

    def marker(self):
        return os.path.exists(os.path.join(self.ostate, "drivelog-off"))

    def summary(self):
        base = os.path.join(self.ostate, "driveway-checks")
        docs = []
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                p = os.path.join(base, name, "summary.json")
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as f:
                        docs.append(json.load(f))
        return docs[-1] if docs else None

    def close(self):
        pid_file = os.path.join(self.ostate, "finisher.pid")
        try:
            with open(pid_file, encoding="utf-8") as f:
                os.kill(int(f.read().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def listens(calls):
    return [c for c in calls if c.startswith("listen capture")]


def scenario_the_whole_run_both_captures_pass_parked_():
    head("the whole run, both captures pass (--parked --apply)")
    c = Case("happy")
    c.run(["--parked", "--apply"])
    check("exits 0", c.rc, 0)
    check("capture A passes", "capture A passed" in c.stdout, True)
    check("capture B passes", "capture B passed" in c.stdout, True)
    check("it says 'gauges back in N s' for each capture",
          len(re.findall(r"gauges back in [0-9.]+ s", c.stdout)), 2)
    check("the strongest preset is chosen", "preset:    telemetry-first-caf0" in c.stdout, True)
    check("the drop-in carries the whole ruled table row",
          c.conf(), f"[Service]\nEnvironment={ENV_LINE_CAF0}\n")
    check("stand down, capture twice, then start again",
          [x.split()[0] + " " + x.split()[1] for x in c.calls()],
          ["drive off", "listen capture", "listen capture", "drive on"])
    check("reload then restart, in that order",
          c.systemctl_calls(), ["--user daemon-reload", "--user restart omacar-drivelog"])
    s = c.summary() or {}
    check("summary.json: mode test, not aborted", (s.get("mode"), s.get("aborted_reason")),
          ("test", None))
    check("summary.json: preset and applied", (s.get("preset"), s.get("applied")),
          ("telemetry-first-caf0", True))
    check("summary.json: both captures passed",
          ((s.get("capture_a") or {}).get("passed"), (s.get("capture_b") or {}).get("passed")),
          (True, True))
    back_a = s.get("gauges_back_a_seconds")
    check("summary.json: the measured gauges-back seconds are real (about 1s, not 0)",
          back_a is not None and 0.5 <= back_a <= 6.0, True)
    check("summary.json: the install was checked against drivelog.json and matched",
          (s.get("install") or {}).get("verified"), True)
    check("it says plainly that the fast link and CAF0 cannot be seen from status",
          "cannot be seen" in c.stdout, True)
    check("the figure is labelled an estimate, in whole percent, with the caveat",
          bool(re.search(r"about \d+% \(estimated from today's frame rate", c.stdout))
          and "hand-over" in c.stdout, True)
    c.close()


def scenario_s5_the_daemon_keeps_publishing_hand_over():
    head("S5: the daemon keeps publishing hand-over snapshots and never reconnects (C1)")
    c = Case("handover", fake={"FAKE_BACK": "never"},
             script_env={"OMACAR_DRIVEWAY_WAIT_SECS": "3"},
             dropin={"telemetry-first.conf": f"[Service]\nEnvironment={ENV_LINE_FIRST}\n"})
    c.run(["--parked", "--apply"])
    check("it does NOT say the gauges are back", "gauges back in" in c.stdout, False)
    check("it stops and says the gauges did not come back",
          "did not come back" in c.stdout, True)
    check("it stops with a non-zero exit", c.rc != 0, True)
    check("capture B never runs", len(listens(c.calls())), 1)
    check("no preset is chosen or installed",
          (c.conf(), c.systemctl_calls()), (None, []))
    check("it names the fast-link drop-in that is currently installed",
          "telemetry-first.conf" in c.stdout, True)
    check("and suggests the fallback preset", "--apply-preset fallback" in c.stdout, True)
    check("the recorder is still given back", c.calls()[-1], "drive on")
    s = c.summary() or {}
    check("summary.json says why it stopped", "gauges" in (s.get("aborted_reason") or ""), True)
    check("summary.json: nothing applied", (s.get("preset"), s.get("applied")), (None, False))
    c.close()


def scenario_a_hung_capture_exit_124_gets_the_lease_s():
    head("a hung capture: exit 124 gets the lease's grace period before judging (C1)")
    c = Case("hang", fake={"FAKE_A": "hang", "FAKE_HANG_BACK": "5"},
             script_env={"OMACAR_DRIVEWAY_TIMEOUT_SECS": "2", "OMACAR_DRIVEWAY_WAIT_SECS": "2",
                         "OMACAR_DRIVEWAY_GRACE_SECS": "6"})
    c.run(["--parked", "--apply"])
    check("it says the capture hung", "hung" in c.stdout, True)
    check("capture A did not pass", "capture A passed" in c.stdout, False)
    check("the daemon came back inside grace + wait, and it says so",
          bool(re.search(r"gauges back in [0-9.]+ s", c.stdout)), True)
    check("A failed, so B is skipped and the fallback preset is chosen",
          (len(listens(c.calls())), "preset:    fallback" in c.stdout), (1, True))
    check("summary.json records the hang (exit 124)",
          ((c.summary() or {}).get("capture_a_exit")), 124.0)
    c.close()


def scenario_a_fails_so_the_fallback_the_most_likely_():
    head("A fails, so the fallback (the most likely outcome today)")
    c = Case("afail", fake={"FAKE_A": "fail"},
             dropin={"fullbus.conf": "[Service]\nEnvironment=OMACAR_FASTBAUD=1 OMACAR_CAF0=1\n",
                     "balanced.conf": "[Service]\nEnvironment=OMACAR_FASTBAUD=1\n",
                     "telemetry-first.conf": f"[Service]\nEnvironment={ENV_LINE_FIRST}\n",
                     "telemetry-first.conf.off": "OLD EARLIER .off\n",
                     "hand-written.conf": "[Service]\nEnvironment=SOMETHING=1\n"})
    c.run(["--parked", "--apply"])
    check("exits 0", c.rc, 0)
    check("capture A is judged a failure", "capture A failed" in c.stdout, True)
    check("capture B is skipped", (len(listens(c.calls())), "capture B: not run" in c.stdout),
          (1, True))
    check("the fallback drop-in is written, QUIET=10 included",
          c.conf(), f"[Service]\nEnvironment={ENV_LINE_FALLBACK}\n")
    files = c.dropin_files()
    check("the three legacy confs are renamed, not deleted",
          all(f + ".off" in files or f + ".off.1" in files
              for f in ("fullbus.conf", "balanced.conf", "telemetry-first.conf")), True)
    check("and the originals are gone",
          any(f in files for f in ("fullbus.conf", "balanced.conf", "telemetry-first.conf")), False)
    check("an earlier .off file is never overwritten (M3)",
          c.conf("telemetry-first.conf.off"), "OLD EARLIER .off\n")
    check("the renamed one is kept beside it", c.conf("telemetry-first.conf.off.1") is not None, True)
    check("a hand-written .conf stays active, and the run warns about it (M4)",
          ("hand-written.conf" in files,
           "another drop-in is active: hand-written.conf" in c.stdout), (True, True))
    s = c.summary() or {}
    check("summary.json: A failed, B never ran, fallback applied",
          ((s.get("capture_a") or {}).get("passed"), s.get("capture_b"), s.get("preset"),
           s.get("applied")), (False, None, "fallback", True))
    check("summary.json: the install matched drivelog.json",
          (s.get("install") or {}).get("verified"), True)
    check("summary.json lists the other active conf", s.get("other_active_confs"),
          ["hand-written.conf"])
    c.close()


def scenario_b_is_judged_on_more_than_identifiers_i4_():
    head("B is judged on more than identifiers (I4): too few frames fails it")
    c = Case("bthin", fake={"FAKE_B": "thin"})
    c.run(["--parked", "--apply"])
    check("capture B fails on its frame count", "capture B failed" in c.stdout, True)
    check("so telemetry-first (no CAF0) is chosen", "preset:    telemetry-first" in c.stdout
          and "telemetry-first-caf0" not in c.stdout.split("Verdict")[-1], True)
    check("the drop-in has no CAF0", "OMACAR_CAF0" in (c.conf() or ""), False)
    c.close()


def scenario_a_capture_that_fails_without_hanging_is_():
    head("a capture that fails without hanging is never judged from an older file (I2)")
    c = Case("exit1", fake={"FAKE_A": "exit1"})
    old_dir = os.path.join(c.ostate, "captures")
    os.makedirs(old_dir)
    old = os.path.join(old_dir, "20200101-000000.json")
    with open(old, "w", encoding="utf-8") as f:
        json.dump({"started": 1577836800.0, "note": "fastbaud-test",
                   "raw": [{"t": i * 20.0 / 25413, "id": "161", "data": "0102030405060708"}
                           for i in range(25413)]}, f)
    os.utime(old, (1577836800, 1577836800))
    c.run(["--parked", "--apply"])
    check("it says the capture did not run, with its exit code",
          "did not run (exit 1)" in c.stdout, True)
    check("it does not pass capture A on the strength of the old file",
          "capture A passed" in c.stdout, False)
    check("the fallback is chosen", "preset:    fallback" in c.stdout, True)
    check("summary.json records the exit code", (c.summary() or {}).get("capture_a_exit"), 1.0)
    c.close()


def scenario_the_verdict_helper_failing_stops_the_run():
    head("the verdict helper failing stops the run (M2)")
    c = Case("badraw", fake={"FAKE_A": "badraw"})
    c.run(["--parked", "--apply"])
    check("it says it could not judge capture A", "could not judge capture A" in c.stdout, True)
    check("it stops non-zero and picks nothing", (c.rc != 0, c.conf()), (True, None))
    check("the recorder is still given back", c.calls()[-1], "drive on")
    c.close()


def scenario_was_the_recorder_already_off_the_marker_():
    head("was the recorder already off? the marker file says, not the status text (C2)")
    c = Case("alreadyoff", marker=True)
    c.run(["--parked", "--apply"])
    check("exits 0", c.rc, 0)
    check("it says it will leave the recorder off", "already stood down" in c.stdout, True)
    check("`drive on` is never called", "drive on" in c.calls(), False)
    check("the marker is still there", c.marker(), True)
    c.close()

    c = Case("stalestatus", fake={"FAKE_STATUS_TEXT": "    stood down   omacar drive on"})
    c.run(["--parked", "--apply"])
    check("a stale 'stood down' in the status text does not fool it", c.rc, 0)
    check("it does not claim the recorder was already off",
          "already stood down" in c.stdout, False)
    check("`drive on` IS called", c.calls()[-1], "drive on")
    check("the recorder is running at the end (no marker)", c.marker(), False)
    c.close()


def scenario_the_install_prompt_never_keeps_the_recor():
    head("the install prompt never keeps the recorder paused for good (C3)")
    c = Case("prompt")
    p = c.start(["--parked"], stdin=subprocess.PIPE)      # stdin stays open and silent
    t0 = time.time()
    c.finish(timeout=45)
    p.stdin.close()
    check("it does not wait for an answer forever (took %.0fs)" % (time.time() - t0),
          c.timed_out, False)
    check("it says the recorder is paused until you answer",
          "the recorder is paused until you answer" in c.stdout, True)
    check("no answer means no", (c.conf(), c.systemctl_calls()), (None, []))
    check("it says so", "no answer" in c.stdout, True)
    check("and the recorder is given back", c.calls()[-1], "drive on")
    check("exit 0", c.rc, 0)
    check("summary.json: nothing applied", (c.summary() or {}).get("applied"), False)
    c.close()


def scenario_installing_only_claimed_if_it_worked_ver():
    head("installing: only claimed if it worked, verified from drivelog.json (I3, M5)")
    c = Case("restartfails", fake={"FAKE_RESTART_RC": "5"})
    c.run(["--parked", "--apply"])
    check("a failed restart is not called installed",
          (bool(re.search(r"(?<!NOT )installed —", c.stdout)), "NOT installed" in c.stdout),
          (False, True))
    s = c.summary() or {}
    check("summary.json: applied is false, the install failed",
          (s.get("applied"), (s.get("install") or {}).get("state")), (False, "failed"))
    c.close()

    c = Case("reloadfails", fake={"FAKE_RELOAD_RC": "1"})
    c.run(["--parked", "--apply"])
    check("restart is not run if daemon-reload failed",
          c.systemctl_calls(), ["--user daemon-reload"])
    check("and it is not called installed", "NOT installed" in c.stdout, True)
    c.close()

    c = Case("restartstale", fake={"FAKE_RESTART_APPLY": "stale"})
    with open(os.path.join(c.ostate, "drivelog.json"), "w", encoding="utf-8") as f:
        json.dump({"at": time.time() - 500, "started": time.time() - 500, "between": 90.0,
                   "leg_lines": 60000, "quiet": 120.0, "end_on_overflow": False}, f)
    c.run(["--parked", "--apply"])
    check("an old supervisor's status is not taken as the new one",
          "could not confirm" in c.stdout, True)
    s = c.summary() or {}
    check("summary.json: restarted, but not verified",
          (s.get("applied"), (s.get("install") or {}).get("verified")), (True, False))
    c.close()


def scenario_the_port_is_not_free_at_the_start_i1():
    head("the port is not free at the start (I1)")
    c = Case("midleg", steady="yielded", fake={"FAKE_START_YIELDED": "1", "FAKE_OFF_BACK": "1.0"})
    c.run(["--parked", "--apply"])
    check("a recorder leg in progress is waited out, not refused", c.rc, 0)
    check("it does not tell the owner to plug the adapter in", "plug" in c.stdout, False)
    check("it says the adapter is free", "the adapter is free" in c.stdout, True)
    c.close()

    c = Case("stuckleg", steady="yielded",
             fake={"FAKE_START_YIELDED": "1", "FAKE_OFF_BACK": "never"},
             script_env={"OMACAR_DRIVEWAY_WAIT_SECS": "3"})
    c.run(["--parked", "--apply"])
    check("if something else keeps the port it stops, non-zero", c.rc != 0, True)
    check("it says the adapter is still in use, and does not say to plug it in",
          ("still being used" in c.stdout, "plug" in c.stdout), (True, False))
    check("no capture was attempted", listens(c.calls()), [])
    check("the recorder is given back", c.calls()[-1], "drive on")
    c.close()


def scenario_no_live_data_is_refused_plainly():
    head("no live data from the daemon: refused, and the recorder is not touched")
    c = Case("nolive", steady=None)
    c.run(["--parked", "--apply"])
    check("it refuses, non-zero", c.rc != 0, True)
    check("in plain words", "no live data from the daemon" in c.stdout, True)
    check("and touches nothing", c.calls(), [])
    check("the refusal is recorded in summary.json",
          "no live data" in ((c.summary() or {}).get("aborted_reason") or ""), True)
    c.close()


def scenario_apply_preset_and_remove_preset_stand_the():
    head("--apply-preset and --remove-preset stand the recorder down first (M6)")
    c = Case("applypreset")
    c.run(["--apply-preset", "telemetry-first-caf0"])
    check("exits 0", c.rc, 0)
    check("drive off, then (no capture) drive on", c.calls(), ["drive off", "drive on"])
    check("the drop-in is the whole row", c.conf(), f"[Service]\nEnvironment={ENV_LINE_CAF0}\n")
    check("reload then restart", c.systemctl_calls(),
          ["--user daemon-reload", "--user restart omacar-drivelog"])
    s = c.summary() or {}
    check("summary.json: apply-preset, applied, verified",
          (s.get("mode"), s.get("applied"), (s.get("install") or {}).get("verified")),
          ("apply-preset", True, True))
    c.close()

    c = Case("applypreset-off", marker=True)
    c.run(["--apply-preset", "fallback"])
    check("a recorder that was already off stays off",
          (c.calls(), c.marker()), (["drive off"], True))
    c.close()

    c = Case("removepreset", dropin={"driveway-preset.conf": f"[Service]\nEnvironment={ENV_LINE_FALLBACK}\n",
                                     "driveway-preset.conf.off": "OLD\n"})
    c.run(["--remove-preset"])
    check("exits 0", c.rc, 0)
    check("the preset is renamed, and an earlier .off is not overwritten",
          (c.conf(), c.conf("driveway-preset.conf.off"),
           c.conf("driveway-preset.conf.off.1") is not None),
          (None, "OLD\n", True))
    check("reload then restart", c.systemctl_calls(),
          ["--user daemon-reload", "--user restart omacar-drivelog"])
    check("drive off, then drive on", c.calls(), ["drive off", "drive on"])
    check("it verified the recorder is back at its defaults",
          (c.summary() or {}).get("install", {}).get("verified"), True)
    c.close()

    c = Case("removepreset-none")
    c.run(["--remove-preset"])
    check("nothing to remove: it says so and restarts nothing",
          ("nothing to remove" in c.stdout, c.systemctl_calls()), (True, []))
    c.close()


def scenario_two_runs_at_once_m8():
    head("two runs at once (M8)")
    if shutil.which("flock"):
        import fcntl
        c = Case("lock")
        lock = open(os.path.join(c.ostate, "driveway-check.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        c.run(["--parked", "--apply"])
        check("a second run is refused", (c.rc != 0, "another driveway check" in c.stdout), (True, True))
        check("and touches nothing", c.calls(), [])
        lock.close()
        c.close()
    else:
        ok("skipped -- no flock on this machine")


def scenario_ctrl_c_kill_during_a_capture_m1():
    head("Ctrl-C / hang-up / kill during a capture (M1)")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        c = Case("signal", fake={"FAKE_CAPTURE_SECS": "6"})
        p = c.start(["--parked", "--apply"])
        running = os.path.join(c.ostate, "capture-running")
        for _ in range(200):
            if os.path.exists(running):
                break
            time.sleep(0.1)
        time.sleep(0.5)
        p.send_signal(sig)
        c.finish(timeout=90)
        name = signal.Signals(sig).name
        check(f"{name}: it says it is finishing the capture and giving the recorder back",
              "finishing the capture, then giving the recorder back" in c.stdout, True)
        check(f"{name}: the recorder is given back", c.calls()[-1], "drive on")
        check(f"{name}: the interruption is recorded",
              "interrupted" in ((c.summary() or {}).get("aborted_reason") or ""), True)
        check(f"{name}: nothing was installed", (c.conf(), c.systemctl_calls()), (None, []))
        c.close()


SCENARIOS = [
    ("the_whole_run_both_captures_pass_parked_", scenario_the_whole_run_both_captures_pass_parked_),
    ("s5_the_daemon_keeps_publishing_hand_over", scenario_s5_the_daemon_keeps_publishing_hand_over),
    ("a_hung_capture_exit_124_gets_the_lease_s", scenario_a_hung_capture_exit_124_gets_the_lease_s),
    ("a_fails_so_the_fallback_the_most_likely_", scenario_a_fails_so_the_fallback_the_most_likely_),
    ("b_is_judged_on_more_than_identifiers_i4_", scenario_b_is_judged_on_more_than_identifiers_i4_),
    ("a_capture_that_fails_without_hanging_is_", scenario_a_capture_that_fails_without_hanging_is_),
    ("the_verdict_helper_failing_stops_the_run", scenario_the_verdict_helper_failing_stops_the_run),
    ("was_the_recorder_already_off_the_marker_", scenario_was_the_recorder_already_off_the_marker_),
    ("the_install_prompt_never_keeps_the_recor", scenario_the_install_prompt_never_keeps_the_recor),
    ("installing_only_claimed_if_it_worked_ver", scenario_installing_only_claimed_if_it_worked_ver),
    ("the_port_is_not_free_at_the_start_i1", scenario_the_port_is_not_free_at_the_start_i1),
    ("no_live_data_is_refused_plainly", scenario_no_live_data_is_refused_plainly),
    ("apply_preset_and_remove_preset_stand_the", scenario_apply_preset_and_remove_preset_stand_the),
    ("two_runs_at_once_m8", scenario_two_runs_at_once_m8),
    ("ctrl_c_kill_during_a_capture_m1", scenario_ctrl_c_kill_during_a_capture_m1),
]


def main():
    only = [x for x in os.environ.get("DW_ONLY", "").split(",") if x]
    for name, fn in SCENARIOS:
        if only and not any(o in name for o in only):
            continue
        try:
            fn()
        except Exception as e:                                # noqa: BLE001
            # One scenario blowing up (a timeout, a missing file) is a
            # failure of that scenario, not a reason to hide the others.
            bad(f"{name}: the scenario itself failed: {type(e).__name__}: {e}")


main()

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  every end-to-end driveway check holds\n")
