"""Record the bus while the car is driven, and stand aside when it is not.

WHAT THIS IS FOR, AND WHY IT IS A SUPERVISOR RATHER THAN A LONG CAPTURE.

The one thing this project cannot buy is car time. `omacar listen` reads the
traffic a car broadcasts to nobody in particular, which is where a hybrid's
state of charge and a drive-mode switch actually live -- and the only way to
get hours of it is to be recording while somebody drives for hours. Asking a
driver to start it is asking them to remember, before every trip, for months.

So this watches instead. It is not a capture that runs for six hours; it is a
loop that spends almost all of its life reading one small file.

THE PORT IS THE WHOLE DESIGN CONSTRAINT.

There is one serial adapter and the daemon holds it to publish the gauges. A
capture must borrow it, and while it is borrowed the gauges are frozen. A
six-hour borrow is therefore not an option: the driver would watch a dead
dashboard all the way.

Three things follow, and they are the shape of this file:

  IT NEVER HOLDS THE PORT WHILE IDLE. Whether the engine is running is read
  from live.json, which the daemon is already writing. Watching costs nothing,
  so the normal state of this program is to own no hardware at all.

  IT CAPTURES IN LEGS. Twenty minutes, then the port goes back for a minute and
  a half so the daemon can reconnect and the gauges catch up. Over a long drive
  that is most of the traffic and a dashboard that comes back to life every
  twenty minutes, instead of all of the traffic and a dashboard that never does.

  A LEG ENDS WHEN THE ENGINE DOES. Frames stop when the car stops, so a run of
  silence is the ignition going off. A fuel stop ends a leg and hands the port
  back rather than holding it through the sandwich.

WHAT IT WILL NOT DO. It never transmits: the only operation it performs is
ATMA, which does not even acknowledge the frames it reports. It will not take
the port from an actuator test or another capture. It will not run below the
voltage floor. And it will not claim to be recording a car it cannot hear --
a probe that finds no traffic on any setting ends the leg and says so, rather
than filing a capture of a quiet car.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import connect                                                # noqa: E402
import listen as listenlib                                    # noqa: E402
import records                                                # noqa: E402

# The engine is running above this. The same test the watchdog uses, so the two
# cannot disagree about whether the car is on.
RPM_RUNNING = 200

# Below this the battery is doing the work and nothing should be adding to its
# load. Shared with the fault reader rather than restated.
try:
    from dtc import LOW_VOLTS
except Exception:                                             # noqa: BLE001
    LOW_VOLTS = 11.8

IDLE_POLL = 15.0            # how often to read live.json while doing nothing
LEG_MINUTES = 20.0          # how long one borrow of the port lasts
BETWEEN_LEGS = 90.0         # long enough for the daemon to reconnect and settle
QUIET_TIMEOUT = 120.0       # silence that means the engine has stopped
BACKOFF_MAX = 300.0         # the longest wait after something declines
LIVE_STALE = 90.0           # a live.json older than this tells us nothing

STATE = os.path.join(records.STATE, "drivelog.json")
OFFFILE = os.path.join(records.STATE, "drivelog-off")
LOGDIR = os.path.join(records.STATE, "drivelog")


def _live():
    """What the daemon last published, or None if that is too old to trust."""
    try:
        with open(records.LIVE, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - (doc.get("t") or 0) > LIVE_STALE:
        return None
    return doc


def _event(kind, **fields):
    """One line in the trip's log. Append-only, one file a day."""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        path = os.path.join(LOGDIR, time.strftime("trip-%Y%m%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(),
                                "when": time.strftime("%H:%M:%S"),
                                "event": kind, **fields}) + "\n")
    except OSError:
        pass


class Supervisor:
    def __init__(self, leg_minutes=LEG_MINUTES, quiet=QUIET_TIMEOUT,
                 between=BETWEEN_LEGS, poll=IDLE_POLL, once=False):
        self.leg_minutes = leg_minutes
        self.quiet = quiet
        self.between = between
        self.poll = poll
        self.once = once
        self.state = "starting"
        self.detail = ""
        self.legs = 0
        self.frames = 0
        self.started = time.time()
        self.last_leg = None
        self.backoff = 0.0
        self._said = None            # the last idle reason, so it is said once

    # -- what anything asking can read ----------------------------------------
    def publish(self, cap=None):
        doc = {
            "at": time.time(), "started": self.started,
            "state": self.state, "detail": self.detail,
            "legs": self.legs, "frames": self.frames,
            "last_leg": self.last_leg,
            "leg_minutes": self.leg_minutes,
        }
        if cap is not None:
            doc["leg"] = {"frames": len(cap.frames),
                          "rejected": cap.rejected,
                          "protocol": cap.protocol,
                          "since": cap.started}
        try:
            os.makedirs(records.STATE, exist_ok=True)
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            os.replace(tmp, STATE)
        except OSError:
            pass

    def say(self, state, detail="", **fields):
        """Set the state, and log it only when it has actually changed.

        A supervisor that logs every fifteen seconds that it is still waiting
        writes a megabyte a day of nothing. One line per change is the whole
        story and fits on a screen.
        """
        self.state, self.detail = state, detail
        key = (state, detail)
        if key != self._said:
            self._said = key
            _event(state, detail=detail, **fields)
        self.publish()

    # -- the gates -------------------------------------------------------------
    def stood_down(self):
        return os.path.exists(OFFFILE)

    def someone_else_has_it(self):
        """Another capture, or a borrowed port. Neither is ours to interrupt."""
        if listenlib.running():
            return "another capture is running"
        try:
            import actuate
            with open(actuate.BORROWED, encoding="utf-8") as f:
                doc = json.load(f)
            if time.time() - (doc.get("at") or 0) < actuate.BORROWED_FRESH * 4:
                return "a test has borrowed the adapter"
        except Exception:                                     # noqa: BLE001
            pass
        return None

    def should_capture(self):
        """Whether to take the port right now, and why not if not."""
        held = self.someone_else_has_it()
        if held:
            return False, held
        live = _live()
        if live is None:
            # NO DAEMON, OR ONE THAT IS NOT PUBLISHING. Nothing can be inferred
            # about the car from here, so the honest move is to try: a leg on a
            # parked car ends itself on the quiet timeout, which is bounded and
            # self-correcting, where refusing forever would mean this feature
            # simply does not work without the daemon.
            return True, "no live reading — trying blind"
        if not live.get("connected"):
            return False, "the adapter is not connected"
        if live.get("simulated"):
            return False, "this is the simulator, not a car"
        values = live.get("values") or {}
        volts = values.get("CONTROL_MODULE_VOLTAGE")
        if volts is not None and volts < LOW_VOLTS:
            return False, f"{volts:.1f} V is below the {LOW_VOLTS} V floor"
        rpm = values.get("RPM") or 0
        if rpm <= RPM_RUNNING:
            return False, "the engine is not running"
        return True, f"{rpm:.0f} rpm"

    # -- one leg ---------------------------------------------------------------
    def leg(self):
        name = time.strftime("%Y%m%d-%H%M%S") + "-drive"
        cap = listenlib.Capture(note="drive")
        seconds = self.leg_minutes * 60
        last = {"at": time.time()}
        beat = {"at": 0.0}
        inner = cap.add_line

        def saw(ln):
            last["at"] = time.time()
            return inner(ln)
        cap.add_line = saw

        def done():
            # THE HEARTBEAT LIVES HERE, and that is the point of putting it in
            # the stop test rather than beside the frames.
            #
            # publish() had exactly one caller -- say() -- which runs once when
            # a leg begins. So for the next twenty minutes the status file's
            # timestamp did not move, while `omacar drive status` computes
            # `alive = age < 120` and prints a red "nothing has been written
            # for N min - it is not running" underneath a green "capturing".
            # It is wrong for about ninety per cent of every leg, and it is
            # the ONLY staleness alarm there is: one drive of that teaches a
            # driver to ignore it, and after that a genuinely dead supervisor
            # reads exactly the same.
            #
            # This runs on every read iteration whether or not frames arrive,
            # which is what makes it a heartbeat rather than a frame counter --
            # a silent bus must still prove the process is alive. One small
            # atomic write every fifteen seconds.
            now = time.time()
            if now - beat["at"] >= 15.0:
                beat["at"] = now
                self.publish(cap)
            if self.stood_down():
                return True
            return now - last["at"] > self.quiet

        def began(c):
            self.legs += 1
            self.say("capturing", f"leg {self.legs} on protocol {c.protocol}",
                     leg=self.legs, protocol=c.protocol)

        try:
            listenlib.listen(seconds=seconds, cap=cap, note="drive",
                             flush_as=name, should_stop=done, on_ready=began,
                             require_traffic=True)
        except listenlib.Quiet as why:
            # Not a fault: the car is off, or its broadcast traffic is not on
            # any setting we know. Either way there is nothing to record.
            self.say("waiting", str(why))
            return False
        except RuntimeError as why:
            self.say("declined", str(why))
            return False
        finally:
            try:
                if cap.frames:
                    cap.flush(name)
            except OSError:
                pass

        self.frames += len(cap.frames)
        self.last_leg = {"name": name, "frames": len(cap.frames),
                         "protocol": cap.protocol, "at": time.time()}
        _event("leg ended", name=name, frames=len(cap.frames),
               rejected=cap.rejected, protocol=cap.protocol,
               minutes=round((time.time() - cap.started) / 60.0, 1))
        return True

    # -- the loop --------------------------------------------------------------
    def run(self):
        self.say("waiting", "for the car")
        while True:
            if self.stood_down():
                self.say("stood down", "omacar drive on to start again")
                if self.once:
                    return 0
                time.sleep(self.poll)
                continue

            go, why = self.should_capture()
            if not go:
                self.say("waiting", why)
                if self.once:
                    return 0
                time.sleep(self.poll)
                continue

            self.say("starting a leg", why)
            worked = self.leg()
            if self.once:
                return 0
            if worked:
                self.backoff = 0.0
                # THE PORT GOES BACK BETWEEN LEGS. The daemon reconnects and
                # the gauges catch up; without this the dashboard would be
                # frozen for the whole drive and the driver would rightly
                # conclude the tool had crashed.
                self.say("between legs", f"{self.between:.0f}s for the gauges")
                time.sleep(self.between)
            else:
                self.backoff = min(BACKOFF_MAX,
                                   (self.backoff * 2) or self.poll)
                time.sleep(self.backoff)


def status_doc():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"


def report():
    """One screen, for somebody on a slow link who cannot stop it to look."""
    doc = status_doc()
    lines = ["", f"  {BOLD}Recording the drive{RESET}", ""]
    if os.path.exists(OFFFILE):
        lines.append(f"    {YELLOW}stood down{RESET}   omacar drive on")
        lines.append("")
        return "\n".join(lines), 1
    if not doc:
        lines.append(f"    {DIM}not running. omacar drive on, or "
                     f"systemctl --user start omacar-drivelog{RESET}")
        lines.append("")
        return "\n".join(lines), 1

    age = time.time() - (doc.get("at") or 0)
    alive = age < 120
    colour = GREEN if alive else RED
    lines.append(f"    {colour}{doc.get('state', '?')}{RESET}   "
                 f"{doc.get('detail') or ''}")
    if not alive:
        # A SUPERVISOR THAT HAS STOPPED LOOKS EXACTLY LIKE ONE THAT IS WAITING,
        # and the whole point of it is that nobody is watching.
        lines.append(f"    {RED}nothing has been written for "
                     f"{age / 60:.0f} min — it is not running{RESET}")

    leg = doc.get("leg")
    if leg and doc.get("state") == "capturing":
        mins = (time.time() - (leg.get("since") or time.time())) / 60.0
        lines.append(f"    {mins:.1f} min · {leg.get('frames', 0)} frames · "
                     f"protocol {leg.get('protocol')}")
        if mins > 2 and not leg.get("frames"):
            lines.append(f"    {YELLOW}nothing heard yet{RESET} — the adapter "
                         f"is open and the bus is silent to it")
    last = doc.get("last_leg")
    if last:
        lines.append(f"    {DIM}last leg {last.get('frames', 0)} frames on "
                     f"protocol {last.get('protocol')}{RESET}")
    lines.append(f"    {DIM}{doc.get('legs', 0)} leg(s), "
                 f"{doc.get('frames', 0)} frames this run{RESET}")
    lines.append("")
    lines.append(f"  {DIM}omacar listen list      what has been captured{RESET}")
    lines.append(f"  {DIM}omacar drive off        stand it down{RESET}")
    lines.append("")
    return "\n".join(lines), 0 if alive else 1


def main(argv):
    action = argv[0] if argv else "run"
    if action == "status":
        text, rc = report()
        print(text)
        return rc
    if action == "off":
        os.makedirs(records.STATE, exist_ok=True)
        with open(OFFFILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        _event("stood down", detail="asked by hand")
        print("\n  stood down. It will not record until `omacar drive on`.\n")
        return 0
    if action == "on":
        try:
            os.remove(OFFFILE)
        except OSError:
            pass
        _event("stood up", detail="asked by hand")
        # IT ONLY REMOVES THE MARKER. IT DOES NOT START ANYTHING.
        #
        # "it will record again when the engine is running" is true when a
        # supervisor exists and a flat lie when one does not -- and on a
        # machine where the unit was never enabled, which is every machine
        # this has ever been installed on, that is the sentence somebody reads
        # before driving seventy miles.
        doc = status_doc()
        alive = doc and (time.time() - (doc.get("at") or 0)) < 120
        if alive:
            print("\n  it will record again when the engine is running.\n")
            return 0
        print("\n  the marker is cleared, but nothing is supervising, so "
              "nothing will record.")
        print("  start it with:  systemctl --user enable --now "
              "omacar-drivelog\n")
        return 1
    if action in ("-h", "--help", "help"):
        print("\n  omacar drive           record the bus while the car is "
              "driven\n"
              "  omacar drive status    what it is doing\n"
              "  omacar drive on|off    let it record, or stand it down\n")
        return 0

    once = "--once" in argv
    sup = Supervisor(once=once)
    try:
        return sup.run()
    except KeyboardInterrupt:
        sup.say("stopped", "interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
