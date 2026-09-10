"""What the car says when nobody asks it anything.

THE DOOR THIS OPENS, AND WHY THE OTHER ONES WERE SHUT.

Every capability in this tool until now has been a question. Mode 01 asks for a
PID. Service 0x22 asks for an identifier. Service 0x19 asks for a fault
catalogue. All of them are request-and-answer, and all of them can only reach
data a module has been programmed to answer questions about.

Most of what a car knows is not like that.

The instrument cluster in front of the driver does not poll anything. It listens.
State of charge, assist and regeneration, the drive mode a dashboard switch
selects, gear position, which door is open, whether the brake pedal is down --
these are broadcast continuously on the bus as periodic frames, addressed to
nobody, because the modules that need them are always listening. A diagnostic
identifier for them frequently does not exist at all.

This project has the receipts. A sweep of 8,192 UDS 0x22 identifiers across both
hybrid controllers of a 2015 Honda CR-Z returned NOTHING (doc/SWEEPING.md s5),
and the same car's dashboard was showing a live charge gauge the entire time.
The data was never behind a door. There was no door: it was being shouted into
the room, and this tool was the only thing in the room not listening.

LISTENING IS THE SAFEST THING HERE, NOT THE MOST DANGEROUS.

It reads as though it should be the opposite -- "sniffing the CAN bus" sounds
like the deep end -- so it is worth being precise. An ELM327 in monitor mode is
electrically silent: it does not transmit, and it does not even acknowledge the
frames it reports. Every other read in this tool puts a request frame on a live
powertrain bus. This puts nothing. It is the only operation here that cannot,
even in principle, change what the car does.

What it costs instead is the adapter and a lot of data. A busy bus is thousands
of frames a second, so a capture is bounded by time and by a frame cap, the port
is taken from the daemon under the same lease every other command uses, and the
gauge pauses while it runs.

WHAT THIS MODULE WILL NOT DO.

It will not guess. A frame is an identifier and some bytes; what those bytes
MEAN is not in the data, and no amount of staring at it makes it so. What this
module offers instead is the thing that does work, and works fast: a census of
what is on the bus, and a diff across moments the driver labelled. Press ECON,
mark it. Press SPORT, mark it. The byte that is one value in the first window,
a different value in the second, and steady inside each, is a candidate -- and
it is called a candidate, on the same ladder as everything else, because one
pair of windows can be a coincidence and a person who watched the switch is the
only thing that turns it into a finding.
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import connect                                                # noqa: E402
import records                                                # noqa: E402

CAPTURES = os.path.join(records.STATE, "captures")

HEXCHARS = set("0123456789ABCDEF")

# A capture is bounded twice: by wall-clock seconds, and by frames. The frame
# cap is what stops a busy bus filling a tablet's disk while somebody is driving
# and not watching a terminal.
DEFAULT_SECONDS = 12.0
DEFAULT_LIMIT = 60000

# Below this much elapsed time between an identifier's first and last frame, a
# frequency computed from them is arithmetic rather than measurement.
MIN_RATE_SPAN = 0.5

# An identifier has to be heard this many times inside a window before that
# window is allowed to claim the byte was steady. Without it, a window in which
# an identifier appeared ONCE is trivially constant, and a serial link that
# drops frames -- which is every serial link, under load -- manufactures
# switches that were never there. Five is the smallest number that is not one
# or two, which is the honest justification: below it the claim is arithmetic
# rather than observation.
MIN_FRAMES_PER_WINDOW = 5

# A marks session ends when the person says so. This is only the backstop that
# stops a forgotten terminal holding the adapter all night.
# TWO HOURS, NOT THIRTY MINUTES.
#
# This is the safety net under a marks session, and it used to fire in the
# middle of one. Thirty minutes is shorter than a drive: the reader thread
# would stop, the main thread would stay parked in input() confirming marks
# against a frame count that never moved again, and the session would look
# healthy for the rest of the trip. A backstop exists so a forgotten session
# does not hold the port forever, and two hours does that job without being
# reachable during the procedure it protects.
MARKS_BACKSTOP = 7200.0

# A DRIVE IS LONGER THAN A CONNECTION.
#
# The first capture attempted while actually driving was run over ssh from
# another machine, and the car drove out of range: the session dropped, and the
# capture went with it. A capture that only survives while somebody is watching
# it is not one you can take on a drive, which is the only place half of this
# data exists.
#
# So a detached capture: it outlives its terminal, it writes progressively
# rather than only at the end, and it can be asked how it is doing and told to
# stop. Thirty seconds is often enough of a gap to lose a whole drive to a flat
# battery or a stray ctrl-c, and rewriting the file that often costs nothing
# next to what it protects.
FLUSH_EVERY = 30.0
RUNNING = os.path.join(records.STATE, "listen-running.json")
STOPFILE = os.path.join(records.STATE, "listen-stop")


# ------------------------------------------------------------------ the frames
# An arbitration identifier is 3 hex digits on an 11-bit bus and 8 on a 29-bit
# one. BOTH APPEAR ON THE SAME WIRE. This is the whole reason the width is not
# taken from the negotiated diagnostic protocol: this project's own car speaks
# ISO 15765-4 CAN 29/500 for diagnostics, so asking the protocol gives 8 -- and
# its periodic cluster traffic, which is the entire point of listening, is
# 11-bit. Slicing eight characters off a three-character identifier turns every
# broadcast frame into a reject, and the screen would then say the bus was
# quiet. A false "nothing was heard", on the one capability whose whole purpose
# is to prove the data is there, is the worst possible failure mode for it.
ID_WIDTHS = (3, 8)


def parse(line, header_digits=None):
    """(can_id, [bytes]) from one monitor line, or None if it is not a frame.

    The adapter prints the identifier, then the data bytes, space-separated,
    and that spacing is the reliable structure -- so the split is on whitespace
    and the identifier is whatever the first token is, at whichever legal width
    it happens to be. That handles an 11-bit and a 29-bit frame arriving one
    after the other, which is the normal case on a real car and which no single
    fixed width can read.

    `header_digits` is a FALLBACK, used only for a line with no spaces in it at
    all, which is what a buffer overrun looks like. Anything that is not whole
    hex bytes after a legal identifier is not a frame, and the words the adapter
    emits in monitor mode -- STOPPED, BUFFER FULL, CAN ERROR -- are exactly the
    anything else this rejects.
    """
    text = (line or "").strip().upper()
    if not text:
        return None
    parts = text.split()
    if len(parts) >= 2:
        ident, rest = parts[0], "".join(parts[1:])
        if (len(ident) in ID_WIDTHS and set(ident) <= HEXCHARS
                and rest and len(rest) % 2 == 0 and set(rest) <= HEXCHARS):
            return ident, [int(rest[i:i + 2], 16) for i in range(0, len(rest), 2)]
        return None
    # One run-together token. Fall back to the caller's width hint, and if it
    # has none, try each legal width and accept the one that leaves whole bytes.
    body = parts[0]
    if not set(body) <= HEXCHARS:
        return None
    widths = [header_digits] if header_digits in ID_WIDTHS else list(ID_WIDTHS)
    for w in widths:
        if len(body) > w and (len(body) - w) % 2 == 0:
            rest = body[w:]
            return body[:w], [int(rest[i:i + 2], 16)
                              for i in range(0, len(rest), 2)]
    return None


class Capture:
    """Frames, and the moments a person labelled while they were arriving."""

    def __init__(self, header_digits=None, note=""):
        self.header_digits = header_digits
        self.note = note
        self.protocol = None          # the ATSP setting the frames came from
        # WHICH CAR THIS WAS HEARD ON. Stamped when the capture is made, not
        # worked out when it is read, because the garage can point somewhere
        # else by then -- and a frame filed against the wrong vehicle is the
        # fault this project keeps having to fix. Adoption refuses a capture
        # whose car is not the one in front of you.
        try:
            import profile as _p
            self.vehicle = _p.slug_for_current_car()
        except Exception:                                     # noqa: BLE001
            self.vehicle = "unknown-car"
        self.started = time.time()
        self.frames = []                 # (t, can_id, [bytes])
        self.marks = []                  # (t, label)
        self.rejected = 0

    def add_line(self, line):
        got = parse(line, self.header_digits)
        if got is None:
            self.rejected += 1
            return False
        self.frames.append((time.time(), got[0], got[1]))
        return True

    def mark(self, label):
        self.marks.append((time.time(), str(label or "").strip() or "mark"))

    # -- what is on this bus ---------------------------------------------------
    def census(self):
        """One row per identifier: how often, how long, and which bytes move.

        This is the map. Before anything can be found, it has to be known what
        is out there -- and on a car nobody has mapped, the census alone is new
        information: a count of the identifiers the vehicle broadcasts, which
        no generic scan tool will tell you.
        """
        by_id = {}
        for t, ident, data in self.frames:
            row = by_id.setdefault(ident, {"id": ident, "count": 0, "first": t,
                                           "last": t, "length": len(data),
                                           "values": []})
            row["count"] += 1
            row["last"] = t
            row["length"] = max(row["length"], len(data))
            while len(row["values"]) < len(data):
                row["values"].append(set())
            for i, b in enumerate(data):
                row["values"][i].add(b)
        out = []
        for row in by_id.values():
            span = row["last"] - row["first"]
            varying = [i for i, vals in enumerate(row["values"]) if len(vals) > 1]
            # A RATE NEEDS A SPAN TO BE A RATE. Frames arrive from the adapter
            # in buffered bursts, so a handful seen inside a few milliseconds
            # divides out to tens of thousands of hertz -- a number that is
            # confidently wrong rather than absent. Below a real span, say
            # nothing: an empty cell is honest and a fabricated frequency is
            # the exact failure this project exists to avoid.
            hz = None
            if row["count"] > 1 and span >= MIN_RATE_SPAN:
                hz = round(row["count"] / span, 1)
            out.append({
                "id": row["id"],
                "count": row["count"],
                "length": row["length"],
                "hz": hz,
                "varying_bytes": varying,
                "constant_bytes": [i for i, vals in enumerate(row["values"])
                                   if len(vals) == 1],
            })
        return sorted(out, key=lambda r: (-r["count"], r["id"]))

    # -- windows, and what differs between them --------------------------------
    def windows(self, settle=0.35, lead=3.0):
        """The frames belonging to each mark, as (label, frames).

        A window runs from its mark to the next one, trimmed at BOTH ends.

        The settle at the front exists because a person presses a switch and
        THEN reaches for the keyboard: without it the first fraction of a
        second of every window still holds the previous state.

        THE TRIM AT THE BACK IS THE SAME FACT, AND IT WAS MISSING. The switch
        moves several seconds before the label is typed, so those seconds --
        which are already the NEW state -- were being filed under the OLD
        window. Every window therefore ended with a few seconds of the next
        position in it, no byte was steady anywhere, and discriminators()
        reported nothing at all. The procedure could have been performed
        perfectly and still answered "none", which is the worst possible
        outcome: it looks like the byte is not on this bus.

        The trim is capped at a quarter of the window so it can never eat one.
        A real marks window is a minute or two and loses the full lead; a very
        short one loses a proportion and keeps its shape.
        """
        if not self.marks:
            return []
        bounds = [(t, label) for t, label in self.marks]
        out = []
        for i, (t0, label) in enumerate(bounds):
            start = t0 + settle
            if i + 1 < len(bounds):
                end = bounds[i + 1][0]
                end -= min(lead, max(0.0, (end - start)) * 0.25)
            else:
                end = float("inf")
            out.append((label, [f for f in self.frames if start <= f[0] < end]))
        return out

    def discriminators(self, settle=0.35, lead=3.0):
        """Bytes that are steady within every window and differ between them.

        THE RULE, AND WHY IT IS THIS STRICT. A byte that merely differs across
        two windows proves nothing: road speed differs across any two moments of
        a drive. What is wanted is a byte that behaves like a SWITCH -- one
        value for as long as the switch is in one position, a different value
        for as long as it is in the other. So a candidate has to be constant
        inside each window, present in every window, and take a different value
        in at least two of them.

        Anything that fails is not reported as a weaker result. It is not
        reported, because a list ranked by plausibility is how a person ends up
        believing the fourth item.
        """
        wins = self.windows(settle, lead)
        if len(wins) < 2:
            return []
        # Per window: {ident: {byte_index: {values}}}, and how many times each
        # identifier was actually heard in that window. The count is what stops
        # a dropped-frame window claiming a byte was steady when it was simply
        # absent -- see MIN_FRAMES_PER_WINDOW.
        seen = []
        for label, frames in wins:
            per, heard = {}, {}
            for _t, ident, data in frames:
                heard[ident] = heard.get(ident, 0) + 1
                slot = per.setdefault(ident, {})
                for i, b in enumerate(data):
                    slot.setdefault(i, set()).add(b)
            seen.append((label, per, heard))

        # An identifier only counts if EVERY window heard it enough times.
        shared = None
        for _label, per, heard in seen:
            ids = {i for i in per if heard.get(i, 0) >= MIN_FRAMES_PER_WINDOW}
            shared = ids if shared is None else (shared & ids)
        out = []
        for ident in sorted(shared or ()):
            width = min(len(per[ident]) for _l, per, _h in seen)
            for i in range(width):
                vals = [(label, per[ident].get(i, set())) for label, per, _h in seen]
                if any(len(v) != 1 for _l, v in vals):
                    continue                      # moved inside a window
                singles = [next(iter(v)) for _l, v in vals]
                if len(set(singles)) < 2:
                    continue                      # same everywhere: not a switch
                out.append({
                    "id": ident,
                    "byte": i,
                    "per_window": [{"label": label, "value": value}
                                   for (label, _v), value in zip(vals, singles)],
                    "distinct": len(set(singles)),
                })
        # A byte that takes a different value in EVERY window is the strongest
        # shape: it distinguishes all of them rather than merely two.
        return sorted(out, key=lambda r: (-r["distinct"], r["id"], r["byte"]))

    def asdict(self):
        return {
            "started": self.started,
            "note": self.note,
            "header_digits": self.header_digits,
            "monitor_protocol": self.protocol,
            "vehicle": self.vehicle,
            "frames": len(self.frames),
            "rejected": self.rejected,
            "marks": [{"at": t, "label": lab} for t, lab in self.marks],
            "census": self.census(),
            "discriminators": self.discriminators(),
        }

    def flush(self, name, raw=True):
        """Write what we have so far, under a stable name. Cheap and repeated.

        A capture that only lands on disk when it finishes is a capture that a
        flat battery, a dropped link or an accidental ctrl-c destroys entirely.
        """
        return self.save(name=name, raw=raw)

    def save(self, name=None, raw=False):
        """Keep it. The census and the marks always; the frames only on request.

        A minute of a busy bus is tens of megabytes of raw frames, which on a
        dashboard tablet is a real cost for data whose value has usually been
        extracted already. `raw` is for the case where it has not.
        """
        os.makedirs(CAPTURES, exist_ok=True)
        name = name or time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
        path = os.path.join(CAPTURES, f"{name}.json")
        doc = self.asdict()
        if raw:
            doc["raw"] = [{"t": round(t - self.started, 4), "id": i,
                           "data": "".join(f"{b:02X}" for b in d)}
                          for t, i, d in self.frames]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, path)
        return path


def captures():
    try:
        return sorted(f[:-5] for f in os.listdir(CAPTURES) if f.endswith(".json"))
    except OSError:
        return []


def load(name):
    try:
        with open(os.path.join(CAPTURES, name + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------- the capture
# The CAN protocols an ELM327 can be put into, as ATSP numbers, in the order
# worth trying: 11-bit 500k carries most broadcast traffic, then 29-bit 500k,
# then the 250k pair.
MONITOR_PROTOCOLS = ("6", "7", "8", "9")


class Quiet(Exception):
    """Nothing was heard on any setting.

    Its own type because it is a fact about the car rather than a fault in the
    program, and the two want different words on screen and different decisions
    from a supervisor: a fault is worth reporting, a quiet bus is worth waiting
    out.
    """


def _pick_monitor_protocol(el, probe=2.0):
    """Put the adapter on a setting that actually hears frames, and say which.

    Returns the ATSP number chosen, or None if nothing was heard on any of
    them -- which is a real answer, and a different one from "we monitored on
    the wrong setting and concluded the car was quiet".
    """
    best, best_n = None, 0
    for p in MONITOR_PROTOCOLS:
        try:
            el.raw("ATSP" + p)
            # Echo OFF. The adapter repeats every command back, and inside a
            # monitor that is a wasted line for every frame, on a link that is
            # the bottleneck. init() does not turn it off, and measured on the
            # car it is the difference between four frames a second and fourteen.
            el.raw("ATE0")
            el.raw("ATH1")
            el.raw("ATS1")
        except Exception:                                     # noqa: BLE001
            continue
        seen = []
        try:
            el.monitor("ATMA", seconds=probe,
                       on_line=lambda ln: seen.append(ln))
        except Exception:                                     # noqa: BLE001
            continue
        n = sum(1 for ln in seen if parse(ln) is not None)
        if n > best_n:
            best, best_n = p, n
        # Plenty is plenty: no reason to spend two more seconds each proving
        # the others are worse.
        if n >= 10:
            break
    if best:
        el.raw("ATSP" + best)
        el.raw("ATE0")
        el.raw("ATH1")
        el.raw("ATS1")
    return best


def _restore_protocol(el, was):
    """Put the adapter back on the setting the daemon negotiated.

    Called whether or not a monitoring protocol was found. It used to happen
    only on the way out of a successful capture, so a probe that heard nothing
    left the adapter parked on the last setting it tried -- and handed it back
    to the daemon like that.
    """
    if not was:
        return
    try:
        el.raw("ATSP" + str(was).lstrip("A"))
        el.raw("ATH0")
    except Exception:                                         # noqa: BLE001
        pass


def _publish_progress(name, cap, seconds):
    """What a detached capture is doing, for anything that asks."""
    tmp = RUNNING + ".tmp"
    # WHEN THE LAST FRAME ARRIVED, not just how many there have been.
    #
    # A capture that recorded a hundred frames and then nothing for seven
    # minutes reports the same hundred frames every time it is asked, and reads
    # as healthy. That happened on a real drive: the count was right, the
    # heartbeat was fresh, the protocol was correct, and the capture had been
    # dead for most of the journey. A total cannot show a stall; a timestamp
    # can.
    last = cap.frames[-1][0] if cap.frames else None
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"name": name, "note": cap.note, "started": cap.started,
                   "seconds": seconds, "frames": len(cap.frames),
                   "identifiers": len({i for _t, i, _d in cap.frames}),
                   "rejected": cap.rejected, "protocol": cap.protocol,
                   "last_frame": last,
                   "at": time.time()}, f)
    os.replace(tmp, RUNNING)


def running():
    """The detached capture in progress, or None."""
    try:
        with open(RUNNING, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    # A capture whose deadline passed long ago is finished, not running.
    if time.time() - (doc.get("at") or 0) > FLUSH_EVERY * 3:
        return None
    return doc


# WHY THE LAST DETACHED CAPTURE DID NOT RUN.
#
# A capture that fails after it has detached fails where nobody is looking: the
# terminal that started it has already been told, cheerfully, that it is
# listening. On a long drive that is four hundred miles of believing the car is
# being recorded. This is where the reason goes, so the terminal can read it
# back and `listen status` can say it out loud.
FAILFILE = os.path.join(records.STATE, "listen-failed.json")


def note_failure(why, fault=True):
    """Why the last session is not running.

    `fault` separates the two answers this file has to give. A session that
    could not start is worth driving back for; a session that ran its length
    and finished is not -- but BOTH used to leave nothing behind, so `listen
    status` said "nothing is listening" either way. On a detached capture that
    quietly hit its forty-five minute cap in the first hour of a three-hour
    drive, that sentence is the only thing anybody ever saw.
    """
    try:
        os.makedirs(records.STATE, exist_ok=True)
        with open(FAILFILE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "why": str(why),
                       "fault": bool(fault)}, f)
    except OSError:
        pass


def last_failure():
    try:
        with open(FAILFILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clear_failure():
    try:
        os.remove(FAILFILE)
    except OSError:
        pass


def ask_stop():
    with open(STOPFILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


def _stop_asked():
    return os.path.exists(STOPFILE)


def listen(seconds=DEFAULT_SECONDS, can_id=None, note="", on_frame=None,
           limit=DEFAULT_LIMIT, cap=None, should_stop=None, probe=2.0,
           flush_as=None, on_ready=None, require_traffic=False):
    """Take the port, listen for `seconds`, give it back. Returns a Capture.

    `can_id` narrows the adapter's own filter to one identifier, which is worth
    doing once the interesting one is known: it is the difference between the
    adapter reporting three thousand frames a second and thirty, and on a slow
    serial link that is the difference between seeing every frame and dropping
    most of them.
    """
    import elm as elmlib
    import protocols

    port, _kind = connect.resolve()
    if not port:
        raise RuntimeError("no adapter")
    if not connect.request_port(port):
        raise RuntimeError("the daemon is holding the port")
    try:
        el = elmlib.Elm(port, baudrate=(connect.detect_baud(port) or 38400))
        el.init()
        # The DIAGNOSTIC protocol's header width is recorded as a hint and
        # nothing more. Broadcast traffic on the same wire is routinely a
        # different width -- see the note above parse() -- so the parse decides
        # per line and this only helps a run-together one.
        digits = None
        try:
            p = protocols.describe(getattr(el, "protocol", None))
            if p:
                digits = p["header_digits"]
        except Exception:                                     # noqa: BLE001
            pass
        cap = cap or Capture(header_digits=digits, note=note)
        if cap.header_digits is None:
            cap.header_digits = digits
        # Bound before the try, because the finally below reads it and the
        # first statement inside can raise. It never has, which is the only
        # reason this has not already been a NameError swallowing a real error.
        _restore = getattr(el, "protocol", None)
        try:
            # Headers ON, because a frame without its identifier is an
            # anonymous eight bytes and useless. Spaces on, because the parse
            # is cheap either way and a human reading a capture wants them.
            el.raw("ATE0")
            el.raw("ATH1")
            el.raw("ATS1")
            # THE BUS YOU DIAGNOSE ON IS NOT THE BUS YOU LISTEN TO.
            #
            # An adapter negotiates a protocol for DIAGNOSTICS. On this
            # project's own car that is ISO 15765-4 CAN 29/500 -- and its
            # periodic cluster traffic, the entire reason to listen, is 11-bit
            # at the same 500 kbit/s. Those are two configurations of one CAN
            # controller, and it can only be in one of them: monitoring on the
            # 29-bit setting reports NOTHING while the car is talking
            # continuously. Measured on the car, five seconds each: protocol 7
            # gave 0 lines, protocol 6 gave 147.
            #
            # So the width is probed rather than assumed. Whichever setting
            # actually hears frames is the one the capture runs on, and the
            # original is restored before the port goes back to the daemon,
            # which negotiated it for a reason.
            # CLEAR ANY FILTER FIRST. init() negotiates a diagnostic protocol
            # and the adapter may still be holding a receive-address filter
            # from whatever ran before -- a DTC sweep aims at one module and
            # leaves it aimed. Monitoring through a stale filter shows a
            # handful of frames or none at all, which reads exactly like a
            # quiet bus and is the second way this capability can lie about
            # the car. Reset it, then set our own only if one was asked for.
            el.raw("ATCRA")
            el.raw("ATCF000")
            el.raw("ATCM000")
            if can_id:
                el.raw("ATCRA" + str(can_id).replace(" ", "").upper())
            chosen = _pick_monitor_protocol(el, probe)
            cap.protocol = chosen
            # NOTHING HEARD ON ANY SETTING IS AN ANSWER, AND IT IS NOT THIS ONE.
            #
            # The probe leaves the adapter on whichever setting it tried last,
            # so carrying on here monitors on a setting nothing was heard on
            # and files the result as a capture of a quiet car. That is exactly
            # the confusion the probe exists to prevent -- "we monitored on the
            # wrong setting and concluded the car was quiet" is a sentence in
            # its own docstring -- and it is the difference between a car that
            # is off and a car this tool cannot hear.
            if chosen is None and require_traffic:
                raise Quiet("no setting heard a single frame — the car is "
                            "probably off, or the adapter is not on the bus")
            # THE MOMENT IT IS TRUE TO SAY THIS IS LISTENING, and not before.
            # The port is open, the protocol is chosen and the next call is the
            # monitor itself. Announcing earlier -- which is what the detached
            # capture used to do -- means "it is listening" can be true while
            # the adapter is not even plugged in.
            if on_ready:
                on_ready(cap)
            state = {"last": time.time()}

            def take(ln):
                cap.add_line(ln)
                if on_frame:
                    on_frame(ln)
                # Write through, so what has been heard survives whatever ends
                # the capture -- and publish progress a watcher can read.
                if flush_as and time.time() - state["last"] >= FLUSH_EVERY:
                    state["last"] = time.time()
                    try:
                        cap.flush(flush_as)
                        _publish_progress(flush_as, cap, seconds)
                    except OSError:
                        pass

            el.monitor("ATMA", seconds=seconds, limit=limit,
                       should_stop=should_stop,
                       on_line=take)
        finally:
            try:
                if can_id:
                    el.raw("ATCRA")           # clear the filter for the daemon
            except Exception:                                 # noqa: BLE001
                pass
            _restore_protocol(el, _restore)
            el.close()
    finally:
        connect.release_port()
    return cap


# --------------------------------------------------------------------- the CLI
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW = "\033[32m", "\033[33m"


def _print_census(cap, top=25):
    rows = cap.census()
    print(f"\n  {BOLD}What this bus broadcasts{RESET}  "
          f"{DIM}{len(cap.frames)} frames, {len(rows)} identifiers"
          f"{f', {cap.rejected} lines not frames' if cap.rejected else ''}"
          f"{RESET}\n")
    if not rows:
        # SILENCE AND UNREADABILITY ARE DIFFERENT ANSWERS, and saying the first
        # when the second happened is how somebody concludes their car has
        # nothing to say. If lines arrived and none of them parsed, that is a
        # tool problem and it says so.
        if cap.rejected:
            print(f"  {YELLOW}{cap.rejected} lines arrived and none of them "
                  f"parsed as frames.{RESET} The bus is talking and this could "
                  f"not read it —\n  which is a fault here, not a quiet car. "
                  f"Keep the capture (--save --raw) and send it in.\n")
        else:
            print(f"  {DIM}nothing was heard at all. On a car with the ignition "
                  f"off that is the expected answer.{RESET}\n")
        return
    print(f"    {'id':<9} {'seen':>6} {'Hz':>7}  bytes  moving")
    for r in rows[:top]:
        moving = ",".join(str(i) for i in r["varying_bytes"]) or "-"
        hz = f"{r['hz']:.1f}" if r["hz"] else "-"
        print(f"    {r['id']:<9} {r['count']:>6} {hz:>7}  {r['length']:>5}  {moving}")
    if len(rows) > top:
        print(f"    {DIM}… {len(rows) - top} more{RESET}")
    print()


def _print_discriminators(cap):
    rows = cap.discriminators()
    print(f"  {BOLD}Bytes that behave like the switch you pressed{RESET}\n")
    if not rows:
        print(f"  {DIM}none. Either the state is not on this bus, or it moved "
              f"inside a window, or the marks were too close together. Try "
              f"again, holding each position for a few seconds.{RESET}\n")
        return
    for r in rows[:20]:
        where = "  ".join(f"{w['label']}={w['value']:02X}" for w in r["per_window"])
        print(f"    {GREEN}{r['id']} byte {r['byte']}{RESET}   {where}")
    print(f"\n  {DIM}These are CANDIDATES. One pair of windows can be a "
          f"coincidence; repeat the capture and see if the same byte answers "
          f"the same way.{RESET}\n")


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar listen", add_help=True)
    ap.add_argument("action", nargs="?", default="capture",
                    choices=["capture", "marks", "list", "show", "drive",
                             "status", "stop", "adopt"])
    ap.add_argument("name", nargs="?", help="for show: which capture")
    ap.add_argument("--seconds", type=float, default=None,
                    help=f"capture length (default {DEFAULT_SECONDS:.0f}s; "
                         f"marks runs until you finish)")
    ap.add_argument("--id", dest="can_id", help="listen to one identifier only")
    ap.add_argument("--note", default="")
    ap.add_argument("--save", action="store_true", help="keep the capture")
    # SAVING KEEPS THE FRAMES. THAT IS WHAT SAVING IS FOR.
    #
    # `--raw` used to be opt-in, and the census-only file it left behind can be
    # read once and never compared with anything -- which is the whole point of
    # keeping it. On 8 September a three-mode drive was recorded that way and
    # answered nothing: the bytes were gone. The warning printed below was
    # already there and was not enough, because it is read after the drive.
    #
    # `--raw` still works and now means nothing extra; `--no-raw` is the way
    # to ask for a census-only file, which is a real thing to want on a tablet
    # where a minute of a busy bus is tens of megabytes.
    ap.add_argument("--raw", action="store_true",
                    help="keep every frame (now the default for --save)")
    ap.add_argument("--no-raw", dest="no_raw", action="store_true",
                    help="save the census only, without the frames")
    ap.add_argument("captures", nargs="*", help="for adopt: the captures to compare")
    ap.add_argument("--signal", help="for adopt: which byte, as 17C.2")
    ap.add_argument("--as", dest="as_id", help="for adopt: the id to give it")
    ap.add_argument("--label", help="for adopt: the human name")
    ap.add_argument("--car", help="for adopt: the profile slug to write into")
    # DEFAULTED ON, AND NOT ZERO.
    #
    # This was opt-in, and the drive it was written for was run without it: a
    # capture stalled after one minute and sat there for another seven,
    # reporting itself healthy, because nothing was watching for silence. A
    # capture that has gone quiet should end and let something start a fresh
    # one, whether the cause is the engine stopping or the adapter giving up.
    # Two minutes is long enough to survive a red light and short enough that
    # a stall costs a leg rather than a journey.
    ap.add_argument("--quiet-timeout", dest="quiet_timeout", type=float,
                    default=120.0,
                    help="end a drive capture after this many seconds with no "
                         "frames (0 = never). Both the engine stopping and the "
                         "adapter giving up look like this")
    ap.add_argument("--minutes", type=float, default=45.0,
                    help="for drive: how long to keep listening (default 45)")
    args = ap.parse_args(argv)
    if args.seconds is None:
        args.seconds = MARKS_BACKSTOP if args.action == "marks" else DEFAULT_SECONDS

    if args.action == "status":
        r = running()
        if not r:
            # NOT LISTENING IS TWO DIFFERENT ANSWERS, and they want different
            # things done about them. Nothing was started, or something was
            # started and could not run. The second one is the one worth
            # driving back for, and it used to be indistinguishable.
            failed = last_failure()
            if failed:
                ago = (time.time() - (failed.get("at") or 0)) / 60.0
                fault = failed.get("fault", True)
                head = ("the last one did not start" if fault
                        else "the last one finished")
                print(f"\n  {YELLOW}nothing is listening{RESET} — {head}")
                print(f"    {failed.get('why') or 'no reason recorded'}"
                      f"   {DIM}{ago:.0f} min ago{RESET}\n")
                return 1 if fault else 0
            print("\n  nothing is listening.\n")
            return 1
        mins = (time.time() - r["started"]) / 60.0
        frames = r.get("frames") or 0
        print(f"\n  {BOLD}listening{RESET}  {r['name']}   {r.get('note','')}")
        print(f"    {mins:.1f} min so far · {frames} frames · "
              f"{r['identifiers']} identifiers · protocol {r.get('protocol')}")
        # A CAPTURE THAT HAS STOPPED HEARING LOOKS EXACTLY LIKE ONE GOING WELL,
        # and the difference is the whole drive.
        quiet_for = (time.time() - r["last_frame"]) if r.get("last_frame") else None
        if frames and quiet_for and quiet_for > 45:
            print(f"    {YELLOW}stalled{RESET} — {frames} frames, then nothing "
                  f"for {quiet_for / 60:.1f} min.")
            print(f"    {DIM}The capture is alive and the adapter has stopped "
                  f"delivering. Stopping and{RESET}")
            print(f"    {DIM}starting again recovers it; `omacar doctor` "
                  f"checks the usual causes.{RESET}")
        elif mins > 2 and frames == 0:
            print(f"    {YELLOW}nothing has been heard yet{RESET} — the "
                  f"adapter is open and the bus is silent to it.")
            print(f"    {DIM}On this car the broadcast traffic is 11-bit; if "
                  f"the probe picked wrong,{RESET}")
            print(f"    {DIM}stopping and starting again re-probes it.{RESET}")
        print(f"\n  {DIM}omacar listen stop   to end it and keep what it has{RESET}\n")
        return 0

    if args.action == "stop":
        if not running():
            print("\n  nothing is listening.\n")
            return 1
        ask_stop()
        print("\n  asked it to stop; it saves what it has.\n")
        return 0

    if args.action == "adopt":
        return _adopt(args)

    if args.action == "drive":
        return _drive_session(args)

    if args.action == "list":
        names = captures()
        print()
        for n in names:
            doc = load(n) or {}
            print(f"    {n}   {doc.get('frames', 0)} frames, "
                  f"{len(doc.get('census') or [])} identifiers   {doc.get('note', '')}")
        if not names:
            print(f"    {DIM}no captures yet{RESET}")
        print()
        return 0

    if args.action == "show":
        doc = load(args.name or "")
        if not doc:
            print("  no such capture")
            return 1
        print(f"\n  {BOLD}{args.name}{RESET}  {doc.get('note','')}\n")
        print(f"    {'id':<9} {'seen':>6} {'Hz':>7}  bytes  moving")
        for r in (doc.get("census") or [])[:40]:
            moving = ",".join(str(i) for i in r["varying_bytes"]) or "-"
            hz = f"{r['hz']:.1f}" if r.get("hz") else "-"
            print(f"    {r['id']:<9} {r['count']:>6} {hz:>7}  {r['length']:>5}  {moving}")
        print()
        return 0

    if args.action == "marks":
        return _marks_session(args)

    print(f"\n  {DIM}listening for {args.seconds:.0f}s — the adapter transmits "
          f"nothing while it does{RESET}")
    cap = listen(seconds=args.seconds, can_id=args.can_id, note=args.note)
    _print_census(cap)
    # NOT SAVING IS A CHOICE, NOT A CRASH. This print sat outside the `if`
    # after an edit, so a capture run without --save reached an unbound name
    # and died with a NameError after printing a perfectly good census.
    if not args.save:
        print(f"\n  {DIM}(not saved — add --save to keep it, frames and "
              f"all){RESET}\n")
        return 0
    keep_raw = not args.no_raw
    path = cap.save(raw=keep_raw)
    print(f"  saved: {path}")
    if not keep_raw:
        # SAID WHEN IT HAPPENS, not discovered later. Without the frames this
        # capture can be read but never compared with another, and the whole
        # point of capturing two switch positions is to compare them.
        print(f"  {DIM}(a census only, because --no-raw was passed. This "
              f"file can be read{RESET}")
        print(f"  {DIM} but never compared with another one.){RESET}")
    print()
    return 0


# ------------------------------------------------------------- adoption
#
# A CAPTURE IS EVIDENCE. A PROFILE IS A CLAIM. THIS IS THE STEP BETWEEN.
#
# Everything upstream of here was built and then had nowhere to go: a
# discriminator sat in a JSON file under a timestamp, and the car's profile --
# the thing that is shared, that drives a gauge, that another owner reads --
# knew nothing about it. So the findings of a session survived only as long as
# somebody remembered which file they were in.
#
# What lands is a CANDIDATE, never more. A byte that held one value while a
# switch was in one position and another value while it was in another is
# evidence about the byte; it is not knowledge of what the byte means. The
# person who watched the switch is the only thing that can say that, and they
# say it by naming the signal and its states here. Promotion above candidate
# stays exactly where it was: a human, checking against something real.


def _cross_capture(docs, min_frames=MIN_FRAMES_PER_WINDOW):
    """Bytes steady inside every capture and different between them.

    The same rule Capture.discriminators() applies within one capture, applied
    across several -- which is how a session with the engine running actually
    goes, one capture per switch position, because nobody types at a wheel.
    """
    seen = []
    for doc in docs:
        per, heard = {}, {}
        for f in doc.get("raw") or []:
            try:
                data = bytes.fromhex(f["data"])
            except (ValueError, KeyError, TypeError):
                continue
            ident = f.get("id")
            heard[ident] = heard.get(ident, 0) + 1
            slot = per.setdefault(ident, {})
            for n, b in enumerate(data):
                slot.setdefault(n, set()).add(b)
        seen.append((doc.get("note") or "?", per, heard))
    if len(seen) < 2:
        return []
    shared = None
    for _l, per, heard in seen:
        ids = {i for i in per if heard.get(i, 0) >= min_frames}
        shared = ids if shared is None else (shared & ids)
    out = []
    for ident in sorted(shared or ()):
        width = min(len(per[ident]) for _l, per, _h in seen)
        for n in range(width):
            vals = [(lab, per[ident].get(n, set())) for lab, per, _h in seen]
            if any(len(v) != 1 for _l, v in vals):
                continue
            singles = [next(iter(v)) for _l, v in vals]
            if len(set(singles)) < 2:
                continue
            out.append({"id": ident, "byte": n, "distinct": len(set(singles)),
                        "per_window": [{"label": lab, "value": v}
                                       for (lab, _s), v in zip(vals, singles)]})
    return sorted(out, key=lambda r: (-r["distinct"], r["id"], r["byte"]))


def _states_from(windows):
    """{byte value: every label it was seen under}, in the order seen."""
    order = {}
    for w in windows:
        key = f"{w['value']:02X}"
        labels = order.setdefault(key, [])
        if w["label"] not in labels:
            labels.append(w["label"])
    return {k: " / ".join(v) for k, v in order.items()}


def _adopt(args):
    """Write one discriminator into this car's profile, as a candidate."""
    import profile as profilelib

    # BOTH POSITIONALS, OR THE FIRST CAPTURE IS SILENTLY DROPPED. `name`
    # exists for `show`, and argparse hands it the first bare word here too --
    # so taking only `captures` compared three windows out of four and never
    # said it had. A tool that quietly uses less evidence than it was given is
    # the same failure as one that invents some.
    names = ([args.name] if args.name else []) + list(args.captures or [])
    docs = []
    for n in names:
        d = load(n)
        if not d:
            print(f"\n  no capture called {n!r} — omacar listen list\n")
            return 1
        docs.append((n, d))
    if len(docs) < 2 and not (docs and docs[0][1].get("marks")):
        print("\n  adoption compares positions: give two or more captures, or "
              "one taken with `omacar listen marks`.\n")
        return 1

    # THE CAPTURES MUST ALL BE THE SAME CAR, AND IT MUST BE THIS ONE.
    here = profilelib.slug_for_current_car()
    # THE FRAMES HAVE TO BE THERE, AND THIS USED TO FIND OUT SILENTLY.
    #
    # The comparison reads individual frames: which byte held which value in
    # each position. A capture saved without them keeps a census -- which bytes
    # moved -- and not the values they moved to, so the comparison runs, finds
    # nothing, and reports no candidates. That is indistinguishable from a car
    # with no answer, and it is how a drive across three drive modes, with the
    # switches timed to the second, came home unable to answer the question it
    # was made to answer.
    bare = [n for n, d in docs if not (d.get("raw") or [])]
    if bare:
        print(f"\n  {YELLOW}those captures did not keep their frames{RESET}")
        for n in bare[:6]:
            print(f"    {n}")
        if len(bare) > 6:
            print(f"    … and {len(bare) - 6} more")
        print(f"\n  {DIM}A comparison needs the value each byte held, and a"
              f" capture saved without{RESET}")
        print(f"  {DIM}--raw keeps only a census of which bytes moved. Capture"
              f" again with:{RESET}")
        print(f"    omacar listen capture --seconds 30 --save --raw --note econ")
        print(f"  {DIM}or use `omacar listen marks`, which keeps what it needs "
              f"by itself.{RESET}\n")
        return 1

    cars = {d.get("vehicle") or "unknown-car" for _n, d in docs}
    if len(cars) > 1:
        print(f"\n  those captures are from different cars ({', '.join(sorted(cars))}). "
              f"A profile is a claim about one vehicle.\n")
        return 1
    theirs = cars.pop()
    target = args.car or theirs
    if not args.car and theirs != here and theirs != "unknown-car":
        print(f"\n  those captures were taken on {theirs} and this machine is "
              f"pointing at {here}.\n  Pass --car {theirs} if you meant to file "
              f"them there anyway.\n")
        return 1

    rows = (_cross_capture([d for _n, d in docs]) if len(docs) > 1
            else [{"id": r["id"], "byte": r["byte"], "distinct": r["distinct"],
                   "per_window": r["per_window"]}
                  for r in (docs[0][1].get("discriminators") or [])])
    if not rows:
        print("\n  nothing in those captures behaved like a switch.\n")
        return 1

    if not args.signal:
        print(f"\n  {BOLD}What could be adopted{RESET}  "
              f"{DIM}from {len(docs)} capture(s) of {theirs}{RESET}\n")
        for r in rows[:25]:
            where = "  ".join(f"{w['label']}={w['value']:02X}"
                              for w in r["per_window"])
            print(f"    {GREEN}{r['id']}.{r['byte']}{RESET}   {where}")
        print(f"\n  {DIM}Adopt one with:\n"
              f"    omacar listen adopt {' '.join(names)} \\\n"
              f"      --signal {rows[0]['id']}.{rows[0]['byte']} --as drive_mode "
              f'--label "Drive mode"' + RESET + chr(10))
        return 0

    want = str(args.signal).replace(" ", "").upper()
    if "." not in want:
        print("\n  --signal looks like 17C.2 — an identifier and a byte.\n")
        return 1
    cid, _, bstr = want.partition(".")
    try:
        byte = int(bstr)
    except ValueError:
        print("\n  the byte after the dot must be a number.\n")
        return 1
    row = next((r for r in rows if r["id"] == cid and r["byte"] == byte), None)
    if row is None:
        print(f"\n  {cid}.{byte} is not one of the candidates. Run without "
              f"--signal to see them.\n")
        return 1

    sid = args.as_id or f"{cid.lower()}_{byte}"
    entry = {
        "id": sid,
        "name": args.label or sid.replace("_", " ").capitalize(),
        "can_id": cid,
        "byte": byte,
        "kind": "enum",
        # EVERY LABEL THAT VALUE WAS SEEN UNDER, NOT THE LAST ONE.
        #
        # A dict comprehension over the windows quietly kept whichever label
        # came last, so a byte reading 02 in three of four windows was written
        # down as meaning the fourth. That is the tool inventing a mapping the
        # evidence does not support -- and worse, it HIDES the thing the reader
        # most needs to see: if "econ" and "econ again" ended up on different
        # values, the byte did not follow the switch and is not what you think.
        # Joined, that leaps off the page.
        "states": _states_from(row["per_window"]),
        "confidence": "candidate",
        "provenance": {
            "found_by": "omacar listen",
            "found_on": theirs,
            "method": "held each position and diffed the frames: "
                      + ", ".join(w["label"] for w in row["per_window"]),
            "first_seen": time.strftime("%Y-%m-%d"),
            "note": "captures: " + ", ".join(names),
        },
    }

    path = os.path.join(records.STATE, "profiles", target + ".toml")
    doc, found = profilelib.load(target)
    doc = doc or {"schema": profilelib.SCHEMA,
                  "car": {"slug": target, "make": "", "model": ""}}
    casts = [b for b in (doc.get("broadcast") or []) if b.get("id") != sid]
    casts.append(entry)
    doc["broadcast"] = casts
    probs = profilelib.problems(doc)
    blocking = [x for x in probs if x.startswith("broadcast ")]
    if blocking:
        print("\n  refusing to write it:")
        for x in blocking:
            print("    " + x)
        print()
        return 1
    profilelib.write(path, doc)
    print(f"\n  {GREEN}adopted{RESET} {cid}.{byte} as {BOLD}{sid}{RESET} "
          f"into {target}")
    shared = [k for k, v in entry["states"].items() if " / " in v]
    for k, v in sorted(entry["states"].items()):
        mark = f"  {YELLOW}<- more than one position{RESET}" if " / " in v else ""
        print(f"    {k} = {v}{mark}")
    if shared:
        print(f"\n  {YELLOW}Read that carefully.{RESET} A value that appears "
              f"under more than one position\n  means this byte does not "
              f"distinguish them — and if a position you returned to\n  did not "
              f"come back to its earlier value, the byte was not following the\n"
              f"  switch at all. Repeat it before you believe it.")
    print(f"\n  {DIM}It is a CANDIDATE. It says a byte moved when you said you "
          f"moved a switch,\n  which is evidence and not yet meaning. Hold each "
          f"position again on another\n  drive; if it holds, that is when it "
          f"becomes validated — by you, not by this.{RESET}\n")
    if probs and not blocking:
        print(f"  {DIM}(the profile has other notes: omacar profile check "
              f"{target}){RESET}\n")
    return 0


def _drive_session(args):
    """A capture that outlives the terminal that started it.

    For the case the whole feature exists for: a long drive, where the useful
    frames are the ones recorded while the car is moving and nobody can be
    typing. Start it before setting off, drive, and stop it when back. It
    writes through every half minute, so a flat battery or a lost connection
    costs the last thirty seconds rather than the whole drive.

    Mode changes are not marked here on purpose. Reaching for a keyboard at
    speed to label a window is exactly the thing this tool refuses to ask of a
    driver; every frame carries a timestamp, so the windows can be cut
    afterwards from a note on paper.
    """
    import subprocess

    if running():
        print("\n  something is already listening — omacar listen status\n")
        return 1

    if os.environ.get("OMACAR_LISTEN_CHILD") != "1":
        # Re-launch detached, so closing the terminal, losing ssh or driving
        # out of range does not take the capture with it.
        env = dict(os.environ, OMACAR_LISTEN_CHILD="1")
        argv = [sys.executable, os.path.abspath(__file__), "drive",
                "--minutes", str(args.minutes), "--note", args.note or "drive"]
        if args.can_id:
            argv += ["--id", args.can_id]
        log = os.path.join(records.STATE, "listen-drive.log")
        os.makedirs(records.STATE, exist_ok=True)
        with open(log, "ab") as f:
            subprocess.Popen(argv, stdout=f, stderr=f,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             env=env)
        # WAIT FOR IT TO BE LISTENING, OR FOR IT TO HAVE FAILED.
        #
        # This used to wait a while and then say "listening in the background"
        # whatever had happened, with a mild parenthesis if it had not reported
        # yet -- and exit zero. A missing adapter therefore read as success, and
        # the whole point of a detached capture is that nobody looks at it
        # again until the drive is over.
        for _ in range(40):
            time.sleep(0.5)
            if running() or last_failure():
                break
        r = running()
        if not r:
            why = (last_failure() or {}).get("why") or _log_tail(log)
            print(f"\n  {YELLOW}it did not start{RESET}"
                  + (f"   {why}" if why else ""))
            print(f"  {DIM}Nothing is being recorded. The full log is at{RESET}")
            print(f"  {DIM}{log}{RESET}\n")
            return 1

        minutes = ("%g" % round(args.minutes, 2))
        print(f"\n  {BOLD}listening in the background{RESET}   "
              f"up to {minutes} min"
              + (f", on protocol {r.get('protocol')}" if r.get("protocol") else ""))
        print(f"  {DIM}It survives this terminal closing, ssh dropping and the car\n"
              f"  driving out of range. Nothing is transmitted to the vehicle.{RESET}\n")
        print(f"    omacar listen status     how it is going")
        print(f"    omacar listen stop       end it and keep what it has\n")
        return 0

    # The child.
    try:
        os.remove(STOPFILE)
    except OSError:
        pass
    name = time.strftime("%Y%m%d-%H%M%S") + "-drive"
    cap = Capture(note=args.note or "drive")
    clear_failure()
    quiet_for = float(getattr(args, "quiet_timeout", 0) or 0)
    # PUBLISHED WHEN THE PORT IS OPEN, NOT WHEN THE PROCESS STARTS. Announcing
    # it here used to make `running()` true a fraction of a second before the
    # adapter turned out to be missing.
    ready = {"yes": False}

    # A HEARTBEAT ON A TIMER, NOT ON TRAFFIC.
    #
    # Progress was republished only when a frame arrived, and `running()` calls
    # a capture dead if its file is more than ninety seconds old. So a capture
    # that was working perfectly on a bus that happened to be quiet -- a parked
    # car at a fuel stop, a car whose broadcast traffic we have not found the
    # right setting for -- reported "nothing is listening" while it was
    # listening. Which is the same lie as the one above, told the other way
    # round, and it would have hidden every quiet-bus finding this feature
    # exists to make.
    beat = threading.Event()

    def heartbeat():
        while not beat.wait(FLUSH_EVERY):
            try:
                _publish_progress(name, cap, args.minutes * 60)
            except OSError:
                pass

    def began(c):
        ready["yes"] = True
        _publish_progress(name, c, args.minutes * 60)
        threading.Thread(target=heartbeat, daemon=True,
                         name="listen-heartbeat").start()

    # THE ENGINE STOPPING IS WHAT ENDS A LEG. Frames stop when the car does, so
    # a run of silence is the ignition going off -- and on a trip that is a
    # fuel stop, not the end of the recording. Ending the leg hands the port
    # back to the daemon and lets the next one start clean.
    last_line = {"at": time.time()}
    started_at = time.time()
    # `_stop_asked()` reads a file that the finally block deletes, so by the
    # time the reason is worked out the answer has been thrown away. Remember
    # it while it is still true.
    _stop_asked_ever = {"yes": False}
    _inner_take = cap.add_line

    def seen(ln):
        last_line["at"] = time.time()
        return _inner_take(ln)
    cap.add_line = seen

    def done_here():
        if _stop_asked():
            _stop_asked_ever["yes"] = True
            return True
        if quiet_for and time.time() - last_line["at"] > quiet_for:
            return True
        return False

    try:
        listen(seconds=args.minutes * 60, can_id=args.can_id,
               note=args.note or "drive", cap=cap, flush_as=name,
               should_stop=done_here, on_ready=began,
               require_traffic=bool(quiet_for))
    except Quiet as why:
        # Not a fault. The car is off, or we cannot hear its bus, and either
        # way there is nothing to record and nothing to fix by retrying fast.
        note_failure(str(why))
        print(f"listen drive heard nothing: {why}", flush=True)
        return 2
    except Exception as why:                                  # noqa: BLE001
        # A SENTENCE, NOT A TRACEBACK. Nobody reads this file at a desk; it is
        # read on a phone at a fuel stop, or over a slow link from another
        # country, by somebody who wants to know whether to bother turning
        # round. The traceback still goes to the log underneath.
        plain = {
            "no adapter": "no OBD adapter is plugged in",
            "the daemon is holding the port":
                "the daemon has the port — stop it, or use `omacar stop`",
        }.get(str(why), f"{type(why).__name__}: {why}")
        note_failure(plain)
        print(f"listen drive stopped before it began: {plain}", flush=True)
        # The traceback still goes to the log underneath, for whoever wants it.
        # RETURNED RATHER THAN RE-RAISED, so the exit code says what happened
        # and so this is callable by something other than a subprocess.
        import traceback
        traceback.print_exc()
        return 1
    finally:
        try:
            # A CAPTURE IS ONLY WRITTEN IF THERE WAS A CAPTURE. A session that
            # never opened the port used to leave a file recording zero frames
            # from `unknown-car`, which is a false record of a drive that never
            # happened -- and it would sit in the list looking like evidence.
            if ready["yes"]:
                cap.flush(name)
                _publish_progress(name, cap, args.minutes * 60)
        except OSError:
            pass
        beat.set()
        try:
            os.remove(RUNNING)
        except OSError:
            pass
        try:
            os.remove(STOPFILE)
        except OSError:
            pass
    # WHY IT ENDED, WRITTEN DOWN, because every reason looked identical.
    #
    # This path returns 0 whether somebody asked it to stop, the bus went
    # quiet, or it silently hit a cap -- forty-five minutes by default, or
    # sixty thousand lines. All three removed the running file and left
    # nothing behind, so `omacar listen status` said "nothing is listening" in
    # every case. On a three-hour drive that means the recording stopped in
    # the first hour and the only evidence was a sentence that reads like it
    # was never started.
    #
    # Not a fault: this is a session that did its job and finished. The note
    # says which, and the status screen prints it as a finish rather than a
    # failure.
    ran = time.time() - started_at
    asked = args.minutes * 60.0
    kept = f"{len(cap.frames)} frames kept"
    if _stop_asked_ever["yes"]:
        why = f"you asked it to stop after {ran / 60:.0f} min. {kept}"
    elif quiet_for and time.time() - last_line["at"] > quiet_for:
        why = (f"the bus went quiet for {quiet_for:.0f}s — the engine "
               f"stopped. {kept}")
    elif ran >= asked * 0.98:
        why = (f"it ran its full {args.minutes:.0f} minutes and finished. "
               f"{kept} — start another, or use `omacar drive`, which "
               f"re-legs by itself")
    else:
        # THE ONE THAT CANNOT BE TOLD APART FROM HERE, said as exactly that.
        # The cap that ends it counts LINES off the adapter, inside elm.py,
        # and what survives here is parsed frames -- so this cannot prove it
        # was the limit. Guessing confidently would be worse than saying which
        # two things it might be.
        why = (f"it stopped after {ran / 60:.0f} of its {args.minutes:.0f} "
               f"minutes, most likely the {DEFAULT_LIMIT}-line cap. {kept} — "
               f"`omacar drive` re-legs instead of stopping")
    note_failure(why, fault=False)
    return 0


def _log_tail(path, lines=1):
    """The last thing a detached child said, for a parent that has to explain."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            got = [ln.strip() for ln in f.readlines() if ln.strip()]
        return " / ".join(got[-lines:])[:200] if got else ""
    except OSError:
        return ""


def _marks_rescue(cap, name):
    """A failed session still keeps what it heard.

    Both error paths out of a marks session used to `return 1` and drop the
    capture on the floor. The frames are already flushed under `name` while it
    runs, so this is mostly about saying where they went -- but a session that
    failed in the first second has nothing flushed yet, and this is what makes
    that case honest rather than silent.
    """
    if not cap.frames and not cap.marks:
        print(f"  {DIM}(nothing was heard, so there is nothing to keep){RESET}\n")
        return 1
    try:
        path = cap.save(name, raw=True)
        print(f"  kept what it heard: {path}")
        print(f"  {DIM}({len(cap.frames)} frames, {len(cap.marks)} mark(s)) — "
              f"a short session may still be worth comparing{RESET}\n")
    except OSError as why:                                    # noqa: BLE001
        print(f"  {DIM}(could not keep it: {why}){RESET}\n")
    return 1


def _marks_session(args):
    """Capture while a person presses things and says what they pressed.

    The whole procedure, and it takes under two minutes in a parked car: start
    it, put the switch in one position, press enter and name it, move the
    switch, press enter and name that, empty line to finish. What comes back is
    the list of bytes that behaved like that switch.

    IT ENDS WHEN THE PERSON ENDS IT. This used to run for `--seconds` and no
    longer -- twelve by default -- which made the procedure it exists for
    literally impossible: reaching the ECON switch, holding it, typing a label,
    reaching for SPORT and holding that is minutes, not seconds. The deadline
    is now a backstop measured in tens of minutes, and the empty line is the
    terminator.
    """
    import threading

    print(f"\n  {BOLD}Mark what you change{RESET}")
    print(f"  {DIM}Put the control where you want it, then press enter and name "
          f"the position.\n  Hold each one for a few seconds — a position with "
          f"fewer than {MIN_FRAMES_PER_WINDOW} frames\n  is not counted, "
          f"because a byte seen twice was not observed to be steady.\n  "
          f"Empty line to finish.{RESET}\n")

    cap = Capture()
    done = threading.Event()
    failed = {}
    # NAMED BEFORE IT STARTS, so the frames have somewhere to go while it runs.
    # This session used to write nothing at all until it ended, which meant an
    # adapter that dropped, a tablet that was shut, or either of the two error
    # returns below threw away a procedure somebody had performed in a car.
    marks_name = time.strftime("%Y%m%d-%H%M%S") + "-marks"

    def reader():
        try:
            listen(seconds=args.seconds, can_id=args.can_id, note=args.note,
                   cap=cap, should_stop=done.is_set, flush_as=marks_name)
        except Exception as why:                              # noqa: BLE001
            failed["why"] = why
        finally:
            done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(1.2)
    if failed:
        print(f"\n  capture failed: {failed['why']}\n")
        return _marks_rescue(cap, marks_name)
    try:
        while not done.is_set():
            label = input("  position> ").strip()
            if not label:
                break
            # THE READER MAY HAVE STOPPED WHILE THIS THREAD SAT IN input().
            # It used to confirm regardless, against a frame count that had
            # frozen, which reads as a healthy session for as long as somebody
            # keeps typing into a dead one.
            if done.is_set():
                why = failed.get("why") or "it reached its limit"
                print(f"    {YELLOW}not marked{RESET} — the capture has "
                      f"stopped. {DIM}{why}{RESET}")
                break
            cap.mark(label)
            print(f"    {YELLOW}marked{RESET} {label}  "
                  f"{DIM}({len(cap.frames)} frames so far){RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
    done.set()
    t.join(timeout=6)
    if failed:
        print(f"\n  capture failed: {failed['why']}\n")
        return _marks_rescue(cap, marks_name)
    print()
    _print_census(cap, top=12)
    _print_discriminators(cap)
    # A MARKS SESSION KEEPS ITS FRAMES WHETHER OR NOT ANYBODY ASKED FOR THEM.
    #
    # This is a procedure somebody performs in a parked car, holding a switch
    # in each position and naming it, and it costs two minutes of their life
    # that cannot be got back. Discarding the frames because a flag was not
    # passed means the answer can be read once, on the day, and never checked
    # against anything afterwards. That is exactly what happened to a whole
    # drive across three drive modes on 8 September.
    path = cap.save(raw=True)
    print(f"  saved: {path}")
    print(f"  {DIM}(with every frame, so this session can be compared with "
          f"another one later){RESET}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
