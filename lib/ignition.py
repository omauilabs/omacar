#!/usr/bin/env python3
"""The tablet follows the car: awake while it runs, asleep 30 s after it stops.

WHAT THE OWNER ASKED FOR (2026-10-17). "Power on when the car starts up and
power down 30 seconds after the car is parked. Currently it runs all the time
and drains the battery too much." The tablet's Surface Connect charger is
plugged into an inverter that only has power while the car is on, so the
charger IS the ignition signal: mains online means the car is on, mains gone
means it is off.

WHY THIS IS CAREFUL, AND NOT `systemctl suspend` ON A TIMER. On 8 September
2026 the power button suspended this tablet mid-drive and the boot never came
back (lib/tablet.py has the whole story), which is why `omacar tablet awake`
masks sleep.target to this day. A feature that puts the tablet to sleep has to
prove the car is parked, not merely that the charger went quiet -- an inverter
fuse, a cable knocked out of the socket or a cranking dip all look exactly like
"car off" from the charger's side. So the charger starts the countdown and the
car itself can veto it:

  * a SPEED above zero in the last two minutes: the car was moving, so this is
    a charger fault, not a parking. Held, and the screen says so.
  * the car's computers ANSWERING in the last 30 s: with the ignition off they
    go quiet within a second or two, so a live sample means the ignition is
    still on. Held.
  * the person at the screen tapping STAY ON: held until the charger comes
    back -- unless the battery falls to the floor, which ends it anyway (still
    never while moving).
  * a CHILD ALERT running (lib/childalert.py): the car seat looked occupied
    when the car was switched off, and nobody has said "I have my child" yet.
    Held, and neither Sleep now nor a low battery ends it -- a tablet asleep
    cannot tell anybody anything.

Anything unknown counts against sleeping: a charger this cannot read never
starts a countdown.

WHAT SLEEP DOES. The recorders (cameras, bus logger) are stopped first, so
their files are closed rather than cut, then logind is asked to suspend
(power.py's checks and log). On resume, if the charger is back, the recorders
that were running come back too. If it woke without the charger (a stray
wakeup), it counts down again and goes back to sleep.

WAKING WHEN THE CAR STARTS IS THE PART ONLY THE TABLET CAN ANSWER. It needs
the Surface to treat the charger coming back as a wakeup from suspend, and
whether a Surface Pro 7+ on stock Arch does that has not been measured.
`omacar ignition probe` reports what the machine offers, and every cycle is
logged with the time it slept, the time it woke and what woke it, so the first
driveway test answers it (doc/ignition.md).

    omacar ignition status     what it sees now, and what it would do
    omacar ignition probe      can this machine do it? wake sources, sleep modes
    omacar ignition log        the last sleep/wake cycles
    omacar ignition run        the loop (what omacar-ignition.service runs)
    omacar ignition stay       hold it awake until the charger comes back
    omacar ignition sleep-now  skip the rest of the countdown
"""

import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
STATE = os.path.join(STATE_DIR, "ignition.json")
ASK = os.path.join(STATE_DIR, "ignition-ask.json")
CYCLES = os.path.join(STATE_DIR, "ignition-cycles.jsonl")
LIVE = os.path.join(STATE_DIR, "live.json")
CONFIG = os.path.join(CONFIG_DIR, "ignition.json")
SUPPLIES = os.environ.get("OMACAR_POWER_SUPPLY_DIR", "/sys/class/power_supply")

DEFAULTS = {
    "enabled": True,
    "grace_s": 30,           # the owner's number
    "action": "suspend",     # or "hibernate", "suspend-then-hibernate", "poweroff"
    "battery_floor": 10,     # percent: ends a stay-on, never a drive
    "moving_window_s": 120,  # any speed this recently vetoes
    "answering_window_s": 30,  # any live sample this recently vetoes
    "rewake_grace_s": 30,    # woke without the charger: back to sleep after this
}
ACTIONS = ("suspend", "hibernate", "suspend-then-hibernate", "poweroff")

# Stopped before sleeping so their files are closed, and started again on a
# wake with the charger back. Only the ones that were running.
RECORDERS = ("omacar-cams.service", "omacar-drivelog.service")


# ---- reading the world ---------------------------------------------------------

def config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            mine = json.load(f)
        if isinstance(mine, dict):
            for k in DEFAULTS:
                if k in mine and isinstance(mine[k], type(DEFAULTS[k])):
                    cfg[k] = mine[k]
    except (OSError, ValueError):
        pass
    if cfg["action"] not in ACTIONS:
        cfg["action"] = DEFAULTS["action"]
    cfg["grace_s"] = max(5, min(600, cfg["grace_s"]))
    return cfg


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def supplies(root=None):
    """{"powered": True/False/None, "battery": pct or None, "seen": [...]}.

    Powered when any mains or USB supply reports online=1; False only when at
    least one such supply was read and none is online; None when there is
    nothing to read, which never starts a countdown.
    """
    root = root or SUPPLIES
    seen, online, battery = [], [], None
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        kind = _read(os.path.join(d, "type"))
        name = os.path.basename(d)
        if kind == "Battery":
            cap = _read(os.path.join(d, "capacity"))
            if cap and cap.isdigit():
                battery = int(cap) if battery is None else min(battery, int(cap))
            continue
        if kind not in ("Mains", "USB", "USB_C", "USB_PD"):
            continue
        on = _read(os.path.join(d, "online"))
        if on not in ("0", "1"):
            continue
        seen.append({"name": name, "type": kind, "online": on == "1"})
        online.append(on == "1")
    powered = any(online) if online else None
    return {"powered": powered, "battery": battery, "seen": seen}


def car(path=None, now=None):
    """{"answering": bool, "speed": float or None, "age": s or None} from live.json."""
    path = path or LIVE
    now = time.time() if now is None else now
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return {"answering": False, "speed": None, "age": None}
    t = snap.get("t") if isinstance(snap.get("t"), (int, float)) else st.st_mtime
    age = max(0.0, now - t)
    vals = snap.get("values") or {}
    speed = vals.get("SPEED")
    speed = float(speed) if isinstance(speed, (int, float)) else None
    # A HAND-OFF IS THE CAR STILL ANSWERING. While the drive recorder or a scan
    # borrows the adapter (up to ~140 s for a capture leg), the daemon writes
    # connected=False, handover=True, a current `t` and its last readings. Read
    # as "quiet", a charger that failed mid-drive during a leg would see the
    # moving veto run out with the car still rolling.
    handover = bool(snap.get("handover")) or snap.get("status") == "yielded"
    return {"answering": (bool(snap.get("connected")) and bool(vals)) or handover,
            "speed": speed, "age": age}


# ---- deciding (pure) -------------------------------------------------------------

def fresh_state():
    return {"phase": "on", "since": None, "deadline": None, "held": None,
            "stay": False, "last_moving": None, "last_answering": None}


def decide(st, power, carnow, now, cfg, ask=None, child=None):
    """(new state, action) from what is known now. Never touches anything.

    action is None, "sleep" or "wake" (the charger is back after a sleep;
    restart what was stopped). `ask` is the screen's request: "stay" or "now".
    `child` is lib/childalert.py's ignition_hold(): a reason, or None.
    """
    st = dict(st)
    # What the car said, remembered: the vetoes are about the recent past, and
    # the sample the daemon left on disk is only as recent as its age says.
    if carnow.get("age") is not None:
        seen_at = now - carnow["age"]
        if carnow.get("answering") and carnow["age"] < 15:
            st["last_answering"] = max(st.get("last_answering") or 0, seen_at)
            if (carnow.get("speed") or 0) > 0:
                st["last_moving"] = max(st.get("last_moving") or 0, seen_at)

    if power["powered"] is not False:
        # The car is on (or the charger cannot be read: unknown never sleeps).
        was = st["phase"]
        st.update(phase="on", since=None, deadline=None, held=None, stay=False)
        if power["powered"] is None:
            st["held"] = "cannot read the charger"
        return st, ("wake" if was == "asleep" else None)

    # The charger is off.
    if st["phase"] == "on":
        st.update(phase="counting", since=now, deadline=now + cfg["grace_s"], held=None)
    if ask == "stay":
        st["stay"] = True
    elif ask == "now":
        st["stay"] = False
    moving = st.get("last_moving") is not None and now - st["last_moving"] < cfg["moving_window_s"]
    answering = (st.get("last_answering") is not None
                 and now - st["last_answering"] < cfg["answering_window_s"])
    low = power.get("battery") is not None and power["battery"] <= cfg["battery_floor"]

    held = None
    if moving:
        held = "moving"            # a charger fault on the road: never sleep
    elif answering:
        held = "car answering"     # the ignition is still on
    elif child:
        held = "child alert"       # never asleep with a child maybe inside
    elif st["stay"] and not low:
        held = "stay on"
    if held:
        # The countdown starts over once the hold lifts: 30 s of a quiet,
        # parked car, not the remainder of a countdown begun while moving.
        st.update(phase="held", held=held, deadline=None)
        return st, None
    if st["phase"] == "held":
        st.update(phase="counting", since=now, deadline=now + cfg["grace_s"], held=None)
    if st["phase"] == "asleep":
        # Awake without the charger: a stray wakeup. Count again.
        st.update(phase="counting", since=now, deadline=now + cfg["rewake_grace_s"], held=None)
    st["held"] = None
    if ask == "now" or now >= st["deadline"]:
        st["phase"] = "asleep"
        return st, "sleep"
    return st, None


# ---- acting ----------------------------------------------------------------------

def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as why:
        return 127, "", str(why)


def _active(unit):
    return _run(["systemctl", "--user", "is-active", unit], 5)[1] == "active"


def stop_recorders():
    stopped = []
    for unit in RECORDERS:
        if _active(unit):
            code, _o, err = _run(["systemctl", "--user", "stop", unit], 30)
            if code == 0:
                stopped.append(unit)
            else:
                _log(f"could not stop {unit}: {err}")
    return stopped


def start_recorders(units):
    for unit in units:
        code, _o, err = _run(["systemctl", "--user", "start", unit], 30)
        if code != 0:
            _log(f"could not restart {unit}: {err}")


def screen_on():
    import power
    for cmd in (["hyprctl", "dispatch", 'hl.dsp.dpms({ action = "enable" })'],
                ["hyprctl", "dispatch", "dpms", "on"]):
        if power._refused(*_run(cmd, 5)) is None:
            return True
    return False


def go_to_sleep(cfg):
    """Ask logind, through power.py's checks. Returns (ok, sentence)."""
    import power
    if cfg["action"] == "suspend":
        return power.do("sleep")
    why = power.blocked("sleep" if cfg["action"] != "poweroff" else "off")
    if why:
        power.note(f"ignition {cfg['action']}: refused before trying — {why}")
        return False, why
    code, out, err = _run(["systemctl", cfg["action"]], 30)
    power.note(f"ignition {cfg['action']}: exit {code} {err or out}")
    return code == 0, (f"{cfg['action']} accepted" if code == 0 else (err or out))


def wake_reason():
    """What the kernel says woke it, when it says (needs no root)."""
    irq = _read("/sys/power/pm_wakeup_irq")
    return f"irq {irq}" if irq else None


def _log(line):
    import power
    power.note("ignition: " + line)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _cycle(rec):
    os.makedirs(os.path.dirname(CYCLES), exist_ok=True)
    with open(CYCLES, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def child_hold(now=None):
    """lib/childalert.py's word on sleeping.

    FAIL SAFE IF THE MODULE ITSELF IS BROKEN. If childalert.py will not import
    (a bad merge, a syntax error), its loop is not running either -- but the
    state file it last wrote is still on disk, and a hold it recorded there
    must still hold. So the fallback reads that file directly: an alert that
    asked to hold sleep keeps the tablet awake until the file says otherwise.
    Only no file at all, or a simulated one, means no hold.
    """
    try:
        import childalert
        return childalert.ignition_hold(now)
    except Exception as why:   # noqa: BLE001
        _log(f"child alert module unreadable, reading its state file: {why}")
        try:
            with open(os.path.join(STATE_DIR, "child-alert.json"), encoding="utf-8") as f:
                pub = (json.load(f) or {}).get("pub") or {}
        except (OSError, ValueError):
            return None
        if pub.get("hold_sleep") and pub.get("source") != "sim":
            return "child alert"
        return None


def take_ask():
    """The screen's last request, consumed once: "stay", "now" or None."""
    try:
        with open(ASK, encoding="utf-8") as f:
            a = json.load(f)
        os.unlink(ASK)
    except (OSError, ValueError):
        return None
    if time.time() - float(a.get("at", 0)) > 60:
        return None       # a stale tap from before the last sleep is not a request
    return a.get("ask") if a.get("ask") in ("stay", "now") else None


def ask(what):
    """What the API and the CLI call. Returns the state the screen will see."""
    if what not in ("stay", "now"):
        raise ValueError("ask is 'stay' or 'now'")
    _write_json(ASK, {"ask": what, "at": time.time()})
    return {"asked": what}


def public(st, power, cfg, now=None):
    """What the screen needs, and nothing it does not."""
    now = time.time() if now is None else now
    left = None
    if st["phase"] == "counting" and st.get("deadline"):
        left = max(0, round(st["deadline"] - now))
    return {"phase": st["phase"], "held": st.get("held"), "left": left,
            "grace": cfg["grace_s"], "powered": power["powered"],
            "battery": power["battery"], "at": now, "enabled": cfg["enabled"]}


def read_public():
    """For the API: the loop's last word, or that there is no loop."""
    try:
        with open(STATE, encoding="utf-8") as f:
            pub = json.load(f)
    except (OSError, ValueError):
        return {"phase": "off", "running": False}
    # The loop writes every second; older than ten and nobody is deciding.
    pub["running"] = time.time() - float(pub.get("at", 0)) < 10
    if not pub["running"]:
        pub["phase"] = "off"
    return pub


def run(tick=1.0):
    st = fresh_state()
    stopped = []
    slept_at = None
    clocks = lambda: (time.clock_gettime(time.CLOCK_BOOTTIME), time.monotonic())  # noqa: E731
    last_boot, last_mono = clocks()
    _log("loop started")
    while True:
        cfg = config()
        now = time.time()
        boot, mono = clocks()
        # CLOCK_MONOTONIC stops while suspended and CLOCK_BOOTTIME does not:
        # the gap between them growing is the machine having been asleep.
        if (boot - last_boot) - (mono - last_mono) > 2 and slept_at is not None:
            pw = supplies()
            _cycle({"slept": slept_at, "woke": now, "reason": wake_reason(),
                    "charger": pw["powered"], "battery": pw["battery"]})
            _log(f"woke after {round(now - slept_at)} s, charger "
                 f"{'on' if pw['powered'] else 'off'}, {wake_reason() or 'reason unknown'}")
            slept_at = None
            screen_on()
        last_boot, last_mono = boot, mono

        pw = supplies()
        if not cfg["enabled"]:
            st = fresh_state()
            _write_json(STATE, public(st, pw, cfg, now))
            time.sleep(tick)
            continue
        was = st["phase"]
        st, action = decide(st, pw, car(now=now), now, cfg, take_ask(), child_hold(now))
        if st["phase"] != was:
            _log(f"{was} -> {st['phase']}" + (f" ({st['held']})" if st.get("held") else ""))
        _write_json(STATE, public(st, pw, cfg, now))
        if action == "wake":
            start_recorders(stopped)
            stopped = []
        elif action == "sleep":
            stopped = stop_recorders() or stopped
            slept_at = time.time()
            ok, said = go_to_sleep(cfg)
            if not ok:
                # Could not sleep: say so on the screen and stop trying every
                # second -- hold until the charger comes and goes again.
                _log(f"could not sleep: {said}")
                st.update(phase="held", held="cannot sleep: " + said, deadline=None)
                slept_at = None
                start_recorders(stopped)
                stopped = []
                _write_json(STATE, public(st, pw, cfg, time.time()))
        time.sleep(tick)


# ---- telling ----------------------------------------------------------------------

def probe():
    """Can this machine do it? Read-only."""
    import power
    pw = supplies()
    print()
    if not pw["seen"]:
        print("  charger    none found under", SUPPLIES, "-- nothing to follow")
    for s in pw["seen"]:
        wake = _read(os.path.join(SUPPLIES, s["name"], "device", "power", "wakeup"))
        print(f"  charger    {s['name']} ({s['type']}) online={int(s['online'])}"
              f"  wakeup={wake or 'n/a'}")
    print(f"  battery    {pw['battery']}%" if pw["battery"] is not None else "  battery    unknown")
    print(f"  mem_sleep  {_read('/sys/power/mem_sleep') or 'unknown'}"
          "   ([brackets] = the one suspend uses)")
    print(f"  states     {_read('/sys/power/state') or 'unknown'}")
    acpi = _read("/proc/acpi/wakeup") or ""
    on = [l.split()[0] for l in acpi.splitlines()[1:] if "*enabled" in l]
    print(f"  acpi wake  {' '.join(on) or 'none enabled'}")
    why = power.blocked("sleep")
    print(f"  suspend    {'blocked: ' + why if why else 'allowed'}")
    swap = _read("/proc/swaps") or ""
    print(f"  swap       {'yes' if len(swap.splitlines()) > 1 else 'none (hibernate impossible)'}")
    cfg = config()
    print(f"  config     {CONFIG}: grace {cfg['grace_s']} s, action {cfg['action']}")
    print()
    print("  Whether the charger coming back wakes it is measured, not read:")
    print("  see doc/ignition.md, 'The driveway test'.")
    print()
    return 0


def show_status():
    pub = read_public()
    pw = supplies()
    print()
    print(f"  loop       {'running' if pub.get('running') else 'not running'}")
    print(f"  phase      {pub.get('phase')}" + (f" ({pub['held']})" if pub.get("held") else "")
          + (f", {pub['left']} s left" if pub.get("left") is not None else ""))
    print(f"  charger    {'on' if pw['powered'] else 'off' if pw['powered'] is False else 'unknown'}")
    c = car()
    print(f"  car        {'answering' if c['answering'] and (c['age'] or 99) < 15 else 'quiet'}"
          + (f", {c['speed']:g} km/h" if c["speed"] is not None else ""))
    print()
    return 0


def show_log(n=10):
    try:
        with open(CYCLES, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()][-n:]
    except (OSError, ValueError):
        rows = []
    if not rows:
        print("  no sleep/wake cycles recorded yet")
        return 0
    for r in rows:
        t = lambda x: time.strftime("%m-%d %H:%M:%S", time.localtime(x))  # noqa: E731
        print(f"  slept {t(r['slept'])}  woke {t(r['woke'])}  "
              f"after {round((r['woke'] - r['slept']) / 60)} min  charger "
              f"{'on' if r.get('charger') else 'off'}  {r.get('reason') or ''}")
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        return show_status()
    if what == "probe":
        return probe()
    if what == "log":
        return show_log()
    if what == "run":
        run()
        return 0
    if what == "stay":
        ask("stay")
        print("  staying on until the charger comes back")
        return 0
    if what == "sleep-now":
        ask("now")
        print("  sleeping now, if the car is parked")
        return 0
    print(f"  omacar ignition: no such thing as '{what}'.\n"
          "  status | probe | log | run | stay | sleep-now", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
