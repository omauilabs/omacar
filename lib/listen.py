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


# ------------------------------------------------------------------ the frames
def parse(line, header_digits=3):
    """(can_id, [bytes]) from one monitor line, or None if it is not a frame.

    A monitored line is the arbitration identifier followed by the data, and
    with `ATS1` the adapter puts spaces between the data bytes -- but not
    reliably between the identifier and the first byte, and on a busy bus two
    frames can run together in the output. So the parse is deliberately blunt:
    strip the spaces, take the identifier off the front by its known width, and
    require the rest to be whole hex bytes. Anything else is not a frame, and
    the words the adapter emits in monitor mode -- STOPPED, BUFFER FULL, CAN
    ERROR -- are exactly the anything else this rejects.
    """
    body = (line or "").replace(" ", "").upper()
    if len(body) <= header_digits or not set(body) <= HEXCHARS:
        return None
    ident, rest = body[:header_digits], body[header_digits:]
    if len(rest) % 2:
        return None
    return ident, [int(rest[i:i + 2], 16) for i in range(0, len(rest), 2)]


class Capture:
    """Frames, and the moments a person labelled while they were arriving."""

    def __init__(self, header_digits=3, note=""):
        self.header_digits = header_digits
        self.note = note
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
    def windows(self, settle=0.35):
        """The frames belonging to each mark, as (label, frames).

        A window runs from its mark to the next one, minus a settling period at
        the front. The settle exists because a person presses a switch and THEN
        reaches for the keyboard: without it the first fraction of a second of
        every window still contains the previous state, and a byte that is
        genuinely steady looks like it moved.
        """
        if not self.marks:
            return []
        bounds = [(t, label) for t, label in self.marks]
        out = []
        for i, (t0, label) in enumerate(bounds):
            t1 = bounds[i + 1][0] if i + 1 < len(bounds) else float("inf")
            start = t0 + settle
            out.append((label, [f for f in self.frames if start <= f[0] < t1]))
        return out

    def discriminators(self, settle=0.35):
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
        wins = self.windows(settle)
        if len(wins) < 2:
            return []
        # Per window: {ident: {byte_index: {values}}}
        seen = []
        for label, frames in wins:
            per = {}
            for _t, ident, data in frames:
                slot = per.setdefault(ident, {})
                for i, b in enumerate(data):
                    slot.setdefault(i, set()).add(b)
            seen.append((label, per))

        shared = None
        for _label, per in seen:
            ids = set(per)
            shared = ids if shared is None else (shared & ids)
        out = []
        for ident in sorted(shared or ()):
            width = min(len(per[ident]) for _l, per in seen)
            for i in range(width):
                vals = [(label, per[ident].get(i, set())) for label, per in seen]
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
            "frames": len(self.frames),
            "rejected": self.rejected,
            "marks": [{"at": t, "label": lab} for t, lab in self.marks],
            "census": self.census(),
            "discriminators": self.discriminators(),
        }

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
def listen(seconds=DEFAULT_SECONDS, can_id=None, note="", on_frame=None,
           limit=DEFAULT_LIMIT, cap=None):
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
        digits = 3
        try:
            p = protocols.describe(getattr(el, "protocol", None))
            if p:
                digits = p["header_digits"]
        except Exception:                                     # noqa: BLE001
            pass
        cap = cap or Capture(header_digits=digits, note=note)
        cap.header_digits = digits
        try:
            # Headers ON, because a frame without its identifier is an
            # anonymous eight bytes and useless. Spaces on, because the parse
            # is cheap either way and a human reading a capture wants them.
            el.raw("ATH1")
            el.raw("ATS1")
            if can_id:
                el.raw("ATCRA" + str(can_id).replace(" ", "").upper())
            command = "ATMA"
            el.monitor(command, seconds=seconds, limit=limit,
                       on_line=lambda ln: (cap.add_line(ln),
                                           on_frame(ln) if on_frame else None))
        finally:
            try:
                if can_id:
                    el.raw("ATCRA")           # clear the filter for the daemon
                el.raw("ATH0")
            except Exception:                                 # noqa: BLE001
                pass
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
          f"{DIM}{len(cap.frames)} frames, {len(rows)} identifiers{RESET}\n")
    if not rows:
        print(f"  {DIM}nothing was heard. On a car with the ignition off that is "
              f"the expected answer.{RESET}\n")
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
                    choices=["capture", "marks", "list", "show"])
    ap.add_argument("name", nargs="?", help="for show: which capture")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--id", dest="can_id", help="listen to one identifier only")
    ap.add_argument("--note", default="")
    ap.add_argument("--save", action="store_true", help="keep the capture")
    ap.add_argument("--raw", action="store_true", help="keep every frame too")
    args = ap.parse_args(argv)

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
    if args.save:
        print(f"  saved: {cap.save(raw=args.raw)}\n")
    return 0


def _marks_session(args):
    """Capture while a person presses things and says what they pressed.

    The whole procedure, and it takes under two minutes in a parked car:
    start it, put the switch in one position, press enter and name it, move the
    switch, press enter and name that, finish. What comes back is the list of
    bytes that behaved like that switch.
    """
    import threading

    print(f"\n  {BOLD}Mark what you change{RESET}")
    print(f"  {DIM}Put the control where you want it, then press enter and name "
          f"the position.\n  Hold each one for a few seconds. Empty line to "
          f"finish.{RESET}\n")

    cap = Capture()
    stop = threading.Event()

    def reader():
        try:
            listen(seconds=args.seconds, can_id=args.can_id, note=args.note,
                   cap=cap)
        except Exception as why:                              # noqa: BLE001
            print(f"\n  capture failed: {why}\n")
        finally:
            stop.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(1.0)
    try:
        while not stop.is_set():
            label = input("  position> ").strip()
            if not label:
                break
            cap.mark(label)
            print(f"    {YELLOW}marked{RESET} {label}  "
                  f"{DIM}({len(cap.frames)} frames so far){RESET}")
    except (EOFError, KeyboardInterrupt):
        pass
    stop.set()
    t.join(timeout=args.seconds + 5)
    print()
    _print_census(cap, top=12)
    _print_discriminators(cap)
    if args.save or True:
        print(f"  saved: {cap.save(raw=args.raw)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
