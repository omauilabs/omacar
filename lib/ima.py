"""The IMA half of the car, assembled from what it has actually said.

WHY THIS FILE IS A READER AND NOT A PROBE.

Everything here is built out of files that already exist on disk: the DTC drive
logs, the prospector's saved sweeps, the learned-module profile, the car
profile, the daemon's live sample. It opens no serial port, imports nothing
that needs pyserial, and asks the car nothing. That is deliberate twice over --
the web server runs under the system interpreter where the serial library is
not installed, and an IMA screen that had to talk to the car to draw itself
would be blank every time the car was not plugged in, which is most of the
time somebody wants to look at it.

WHY THE HONEST GAP IS THE FEATURE.

On this car the hybrid controllers answer service 0x19 with a catalogue of
every fault they can set, and answer service 0x22 with part numbers. They have
never once produced a live quantity: no state of charge, no pack voltage, no
pack current, no assist or regen, no pack temperature. Not one reading exists
in any capture.

The tempting thing to build is a dashboard of hybrid gauges reading zero. That
would be a lie with a needle on it, and on a 190,000-mile hybrid it is exactly
the lie that gets acted on. So the register below carries a STATE per quantity
rather than a value per quantity, and the four states are the whole point:

    measured      a real reading, from this car, recently enough to trust
    stale         it was measured, and what is on screen now is history
    candidate     an address answered, and nobody has established what it
                  means -- lib/profile.py's rule is that this must never drive
                  a display, and this file marks it rather than showing it
    undiscovered  nothing has ever answered, and here is the command that
                  would find out

An undiscovered quantity carries the exact sweep that would close it. "SOC is
not discovered on this car -- run this" is a more useful screen than a dial
pointing at nothing, and it is the only version of the screen that is true.

WHY NOTHING HERE RAISES.

Same reason lib/themes.py does not: every input is a file a person can edit,
delete, or half-write while the process is reading it. A malformed drive log
should cost that log's rows and nothing else. Every reader below returns an
empty shape on any failure, and the caller gets a smaller answer rather than a
500.
"""

import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402
import frontier  # noqa: E402
import garage  # noqa: E402
import protocols  # noqa: E402

# The two hybrid controllers, with the labels lib/discover.py already gives
# them. Naming them anything more specific -- "IPU", "MCM", "battery condition
# monitor" -- would be a claim about Honda's own naming that nothing in this
# repo's captures supports, so the labels stay exactly as the module map has
# them and the header does the identifying.
HYBRID_MODULES = {
    "18DA03F1": "hybrid / battery",
    "18DA04F1": "hybrid / motor",
}

# THOSE TWO ARE A HONDA FACT LIVING IN GENERIC CODE.
#
# They are right for the car this was built on and wrong for every other
# hybrid, and a Prius owner reading this file would find two headers their car
# does not have. So they become the FALLBACK, and the real answer comes from
# the vehicle's profile by ROLE -- any module declaring hybrid-battery or
# hybrid-motor fills this screen, whoever made it.
#
# Cached because this is asked nine times while assembling one page and the
# answer cannot change inside a request.
_HYBRID_CACHE = {}


def _current_vin():
    """The VIN of the car being looked at, or "" if there is not one.

    Defined here rather than taken as an argument at every call site: the
    resolver is used ten times while one page is assembled, and threading a
    VIN through all of them would mean ten chances to forget — which is
    exactly what happened on the first attempt, where every call site passed
    nothing and the profile was therefore never consulted at all.
    """
    try:
        return garage.current() or ""
    except Exception:
        return ""


def hybrid_modules(vin=None, slug=None):
    """{header: label} for the hybrid controllers on THIS car."""
    if not vin and not slug:
        vin = _current_vin()
    key = (vin, slug)
    if key in _HYBRID_CACHE:
        return _HYBRID_CACHE[key]
    out = {}
    try:
        import profile as profilelib
        if not slug and vin:
            slug = profilelib.for_vin(vin)
        if slug:
            doc, _p = profilelib.load(slug)
            for role in ("hybrid-battery", "hybrid-motor"):
                out.update(profilelib.modules_by_role(doc, role, default={}))
    except Exception:
        out = {}
    # No profile, or a profile that names no hybrid modules, means this screen
    # has nothing car-specific to go on -- so it behaves exactly as it did
    # before profiles existed rather than going blank.
    result = out or dict(HYBRID_MODULES)
    _HYBRID_CACHE[key] = result
    return result


def has_hybrid(vin=None, slug=None):
    """Whether this car should be offered an IMA screen at all.

    A car whose profile says `screens.ima = false`, or which declares no
    hybrid modules, should not get a tab it can never fill. Defaults to True
    for an unprofiled car, because the alternative is hiding the screen from
    the one car it was built for the moment its profile goes missing.
    """
    if not vin and not slug:
        vin = _current_vin()
    try:
        import profile as profilelib
        if not slug and vin:
            slug = profilelib.for_vin(vin)
        if slug:
            doc, _p = profilelib.load(slug)
            return profilelib.screen(doc, "ima", default=True)
    except Exception:
        pass
    return True

# Asked alongside them because a sweep that skips the gateway learns nothing
# about whether the gateway is where the rest of the hybrid picture lives.
NEIGHBOURS = {
    "18DA10F1": "engine",
    "18DA0EF1": "gateway / other",
}

MEASURED = "measured"
STALE = "stale"
CANDIDATE = "candidate"
UNDISCOVERED = "undiscovered"

STATE_MEANS = {
    MEASURED: "A real reading from this car, current enough to act on.",
    STALE: "This was measured before. What is shown is history, not now.",
    CANDIDATE: "An address answered but nobody has established what it means. "
               "It must not drive a gauge until it is validated.",
    UNDISCOVERED: "Nothing has ever answered for this. It is not that the "
                  "value is zero -- it is that the question has not been asked.",
}

# How old the live sample may be before a reading drops from measured to
# stale. records.py uses the same fifteen seconds to decide the daemon has
# stopped publishing; matching it means the two screens cannot disagree about
# whether the car is connected.
LIVE_STALE = 15.0

# The generic OBD-II hybrid PID. Mode 01, PID 0x5B, "Hybrid battery pack
# remaining life", percent -- that mapping is python-obd's, in
# obd/commands.py, not ours. It matters that this one is NOT proprietary: the
# car lists it in its own supported bitmap, and no Honda-specific discovery is
# needed to read it. It has simply never been polled.
PACK_REMAINING = "HYBRID_BATTERY_REMAINING"

# The commands that would close each gap. Every one of these is a subcommand
# that exists in bin/omacar with flags that exist in its argparse -- a screen
# that tells somebody to run a command that is not real is worse than a screen
# that tells them nothing.
CMD_0X21 = ("omacar prospect --service 0x21 "
            "--headers 18DA03F1,18DA04F1,18DA10F1,18DA0EF1 "
            "--range 00-FF --parked --rounds 8")
CMD_0X22_DEEP = ("omacar discover --headers 18DA03F1,18DA04F1 --service 0x22 "
                 "--range 1000-A5FF --profile honda-crz-2015 "
                 "--budget 45 --interval 60")
CMD_DTC = "omacar dtc --parked --save"
CMD_DTCLOG = "omacar dtclog"
CMD_LEARN = "omacar learn"
CMD_DOCTOR = "omacar doctor"

# The one safety line that has to appear anywhere a sweep is suggested. It is
# not general advice: a 25-minute key-on-engine-off session on this actual car
# put an ABS warning on the dash, which is why lib/autodisc.py carries a
# 180-second engine-off budget and lib/dtc.py refuses below 11.8 V.
SAFETY = ("Engine running, parked, handbrake on. Check the voltage first -- "
          "a long key-on-engine-off sweep sags the 12 V rail and has already "
          "put an ABS light on this car once.")


# ---- file readers -----------------------------------------------------------
#
# All four of these swallow everything. A file that is missing, truncated,
# hand-edited or written by a future version must cost its own contents and
# nothing else.

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _dtclog_files():
    try:
        return sorted(glob.glob(os.path.join(connect.STATE, "dtclog", "*.jsonl")))
    except OSError:
        return []


def _dtclog_records():
    """Every sample in every drive log, oldest first, as (path, record).

    Read line by line rather than whole-file because dtclog appends while a
    drive is in progress: the last line of a live log is routinely half
    written, and one broken line must not cost the other two hundred.
    """
    for path in _dtclog_files():
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        yield path, rec
        except OSError:
            continue


# prospect writes prospect-YYYYMMDD-HHMMSS.json and puts no timestamp inside
# it, so the filename is the only record of when a sweep happened.
_STAMP = re.compile(r"-(\d{8})-(\d{6})\.json$")


def _stamp_of(path):
    m = _STAMP.search(path)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
    except (ValueError, OverflowError):
        return None


# ---- the fault picture ------------------------------------------------------

def modules():
    """What each hybrid controller has told us about its own faults.

    Two different things live here and conflating them is the mistake this
    whole screen exists to avoid.

    The CATALOGUE (0x19 subfunction 0x0A) is every code the module is capable
    of setting. It is a map of what Honda built that ECU to measure, it is
    static, and it says nothing whatever about the health of this car.

    The STORED list (0x19 subfunctions 0x01FF and 0x02FF) is what the module is
    flagging right now -- except that on this car every single observation ever
    captured carries status byte 0x40, which lib/dtc.py decodes as "not run
    this cycle". So the flagged list is a list of monitors that have not
    completed, not a list of faults. `all_not_run` below is what lets the view
    say that as a headline instead of a footnote.
    """
    out = {}
    for header, label in hybrid_modules().items():
        out[header] = {
            "header": header, "label": label,
            "catalogue": [], "catalogue_seen": 0,
            "catalogue_first": None, "catalogue_last": None,
            "flagged": [], "observations": 0, "flag_states": {},
            "all_not_run": None,
            "counts": [], "count_seen": [],
            "first_seen": None, "last_seen": None, "sources": 0,
        }

    catalogue = {h: set() for h in hybrid_modules()}
    flagged = {h: {} for h in hybrid_modules()}
    files = {h: set() for h in hybrid_modules()}

    for path, rec in _dtclog_records():
        t = rec.get("t")
        mods = rec.get("modules")
        if not isinstance(mods, dict):
            continue
        for header, mod in mods.items():
            slot = out.get(str(header).upper())
            if slot is None or not isinstance(mod, dict):
                continue
            files[slot["header"]].add(path)
            if isinstance(t, (int, float)):
                slot["first_seen"] = min(slot["first_seen"] or t, t)
                slot["last_seen"] = max(slot["last_seen"] or t, t)

            cat = mod.get("catalogue")
            if isinstance(cat, dict) and isinstance(cat.get("dtcs"), list):
                catalogue[slot["header"]].update(
                    str(c) for c in cat["dtcs"] if isinstance(c, str))
                slot["catalogue_seen"] += 1
                if isinstance(t, (int, float)):
                    slot["catalogue_first"] = min(slot["catalogue_first"] or t, t)
                    slot["catalogue_last"] = max(slot["catalogue_last"] or t, t)

            status = mod.get("status")
            if isinstance(status, dict) and isinstance(status.get("dtcs"), list):
                for d in status["dtcs"]:
                    if not isinstance(d, dict):
                        continue
                    code = str(d.get("code") or "")
                    if not code:
                        continue
                    # The decoded flag string is taken from the log rather than
                    # re-derived here. lib/dtc.py owns that decode and needs
                    # pyserial to import; copying its bit table into this file
                    # would be a second copy free to drift from the first.
                    flags = str(d.get("flags") or "")
                    raw = d.get("status")
                    entry = flagged[slot["header"]].setdefault(code, {
                        "code": code, "seen": 0, "first": None, "last": None,
                        "flags": {}, "status_bytes": [],
                    })
                    entry["seen"] += 1
                    if isinstance(t, (int, float)):
                        entry["first"] = min(entry["first"] or t, t)
                        entry["last"] = max(entry["last"] or t, t)
                    if flags:
                        entry["flags"][flags] = entry["flags"].get(flags, 0) + 1
                        slot["flag_states"][flags] = \
                            slot["flag_states"].get(flags, 0) + 1
                    if isinstance(raw, int) and raw not in entry["status_bytes"]:
                        entry["status_bytes"].append(raw)
                    slot["observations"] += 1

            count = mod.get("count")
            if isinstance(count, dict) and isinstance(count.get("count"), int):
                slot["counts"].append(count["count"])
                if isinstance(t, (int, float)):
                    slot["count_seen"].append(
                        {"t": t, "n": count["count"], "volts": rec.get("volts")})

    for header, slot in out.items():
        slot["catalogue"] = sorted(catalogue[header])
        slot["flagged"] = sorted(flagged[header].values(),
                                 key=lambda e: e["code"])
        slot["sources"] = len(files[header])
        slot["count_seen"].sort(key=lambda r: r["t"])
        # True only when EVERY observation said the monitor had not run. One
        # real pass or fail anywhere in the record has to flip this, because
        # the moment it does the flagged list stops being a list of monitors
        # and starts being a list of faults.
        if slot["observations"]:
            states = slot["flag_states"]
            slot["all_not_run"] = (
                len(states) == 1 and "not run this cycle" in states)
        slot["distinct_counts"] = sorted(set(slot["counts"]))
    return out


# ---- the quantity register --------------------------------------------------

def _live():
    """The daemon's last published sample, and how old it is."""
    doc = _load_json(os.path.join(connect.STATE, "live.json")) or {}
    age = None
    t = doc.get("t")
    if isinstance(t, (int, float)):
        age = max(0.0, time.time() - t)
    return doc, age


def _supported():
    """Every PID name this car has claimed to support, from its own bitmaps.

    Two sources because they are written by different commands at different
    times: status.json by `omacar doctor`, live.json by the daemon. A name in
    either is a claim the car made.
    """
    names = set()
    for fn in ("status.json", "live.json"):
        doc = _load_json(os.path.join(connect.STATE, fn)) or {}
        sup = doc.get("supported")
        if isinstance(sup, list):
            names.update(str(s) for s in sup)
    return names


def _candidates(slug="honda-crz-2015"):
    """Profile entries that point at a hybrid module and are not validated.

    lib/profile.py's own rule, written into the file it generates: an entry
    below `validated` must not drive a gauge. This surfaces them so the screen
    can say one exists rather than quietly showing its bytes as a reading.
    """
    try:
        import profile as profilelib
        doc, path = profilelib.load(slug)
    except Exception:                                          # noqa: BLE001
        return []
    if not isinstance(doc, dict):
        return []
    out = []
    for pid in doc.get("pid") or []:
        if not isinstance(pid, dict):
            continue
        header = str(pid.get("header") or "").upper()
        if header not in hybrid_modules():
            continue
        conf = pid.get("confidence") or "candidate"
        varying = pid.get("varying_bytes") or []
        out.append({
            "id": pid.get("id"), "header": header,
            "label": hybrid_modules()[header],
            "name": pid.get("name"),
            "request": pid.get("request"),
            "confidence": conf,
            "varying_bytes": varying,
            "unit": pid.get("unit"),
            "source": path,
            # Only `validated` may drive a display. That is not this file's
            # opinion -- it is written at the top of every profile lib/profile.py
            # generates, and repeating the rule here rather than re-deciding it
            # keeps the app and the format agreeing about what is safe to show.
            "state": MEASURED if conf == "validated" else CANDIDATE,
            "displayable": conf == "validated",
            "why": ("Nothing moved across resamples, so whatever this holds is "
                    "constant -- a part number or padding, not a reading."
                    if not varying else
                    "Bytes were seen to change, so it carries something. What "
                    "it means is still a guess."),
        })
    return out


# The quantities the owner actually asked for, in the order a person thinks
# about them. Naming a quantity we do not have is not the same as inventing
# data about it: each entry says plainly which of them has ever produced a
# reading on this car, and every one that has not carries the command that
# would settle it.
# WHAT A HYBRID IS, AS THREE THINGS RATHER THAN ONE.
#
# The pack, the motor, and the braking that puts energy back. They fail
# differently, they are diagnosed differently, and an owner asking "how is my
# battery" is asking about the first one and not the other two. Grouping them
# also stops the register reading as a flat list of things we do not have.
BATTERY, MOTOR, REGEN = "battery", "motor", "regen"

GROUPS = [
    (BATTERY, "The pack",
     "The IMA battery itself: how full, how healthy, how warm, and whether it "
     "is ageing evenly."),
    (MOTOR, "The motor",
     "The electric machine between the engine and the gearbox. It both drives "
     "the car and generates."),
    (REGEN, "Regenerative braking",
     "What the car recovers instead of turning into heat at the discs. It is "
     "the quantity a hybrid is bought for and the one no scan tool shows."),
]

_WANTED = [
    # -- the pack ------------------------------------------------------------
    ("soc", BATTERY, "State of charge", "%",
     "How full the IMA pack is. The dash bars are the driver's version of it."),
    ("pack_voltage", BATTERY, "Pack voltage", "V",
     "Terminal voltage of the whole IMA pack."),
    ("pack_current", BATTERY, "Pack current", "A",
     "Charge and discharge current. The sign is what separates assist from regen."),
    ("pack_temp", BATTERY, "Pack temperature", "degC",
     "The IMA pack's own temperature, which is what its cooling fan reacts to."),
    ("cell_balance", BATTERY, "Cell block spread", "V",
     "The gap between the strongest and weakest block. It is what separates "
     "one tired block from a uniformly tired pack, and those are very "
     "different bills."),
    ("pack_health", BATTERY, "State of health", "%",
     "Capacity now against capacity when it was new. On a 190,000-mile car "
     "this is the number that decides whether the pack is worth keeping."),
    # -- the motor -----------------------------------------------------------
    ("motor_rpm", MOTOR, "Motor speed", "rpm",
     "The IMA motor's own speed, as distinct from engine RPM."),
    ("assist_regen", MOTOR, "Assist / regen", "kW",
     "What the motor is doing right now, in power rather than in bars. "
     "Positive is helping the engine; negative is generating."),
    ("motor_temp", MOTOR, "Motor temperature", "degC",
     "Heat is what limits how long a hybrid can assist. A motor that backs "
     "off on a long climb is usually a thermal limit, not a fault."),
    ("motor_torque", MOTOR, "Motor torque", "Nm",
     "What the motor is actually contributing at the crank."),
    # -- regenerative braking ------------------------------------------------
    ("regen_power", REGEN, "Regen power", "kW",
     "The rate energy is being recovered under braking, right now."),
    ("regen_total", REGEN, "Energy recovered", "Wh",
     "Accumulated over a drive, this is the only honest measure of whether "
     "the hybrid half is earning its weight."),
    ("brake_blend", REGEN, "Brake blending", "%",
     "How much of a given brake application is the motor and how much is the "
     "friction brakes. It is what wears the pads, or does not."),
]


def quantities():
    """Every IMA quantity worth having, and its state on THIS car.

    The register is generated rather than written down, so it cannot go stale
    against the data: `soc` becomes measured the day something publishes a
    state of charge, without anybody editing this list.
    """
    live, age = _live()
    values = live.get("values") if isinstance(live.get("values"), dict) else {}
    supported = _supported()
    cands = _candidates()

    # A candidate whose bytes were seen to MOVE could be a live quantity; one
    # whose payload never changed is a part number or padding, and lifting a
    # whole register out of "undiscovered" on the strength of a constant would
    # be the same overclaim in a different costume.
    moving = [c for c in cands if c.get("varying_bytes")]
    # A candidate somebody has NAMED belongs to the quantity it names and to no
    # other. An unnamed one cannot be attributed at all, so it lifts every
    # quantity to candidate and says so -- "something on this module moves and
    # nobody knows which of these it is" is the true statement, and pinning it
    # to state of charge because that is the one we want most is not.
    named = {}
    for c in moving:
        n = str(c.get("name") or "").strip().lower()
        if n:
            named[n] = c
    unattributed = [c for c in moving
                    if not str(c.get("name") or "").strip()]

    out = []
    for qid, group, label, unit, why in _WANTED:
        row = {"id": qid, "group": group, "label": label, "unit": unit,
               "about": why,
               "state": UNDISCOVERED, "value": None, "at": None,
               "source": None, "note": None, "next": None,
               "command": CMD_0X21, "safety": SAFETY}
        mine = named.get(qid) or named.get(label.lower())
        if mine or unattributed:
            row["state"] = CANDIDATE
            row["source"] = (mine or unattributed[0]).get("request")
            row["note"] = (
                ("%s on %s was recorded as this quantity, and it has not been "
                 "validated against anything real. An unvalidated candidate "
                 "must not drive a gauge."
                 % (mine["request"], mine["header"])) if mine else
                ("An unvalidated responder on the hybrid modules has bytes "
                 "that move, and nothing has established which quantity it "
                 "carries -- possibly none of these. An unvalidated candidate "
                 "must not drive a gauge."))
            row["next"] = ("Correlate it against the dash while driving: "
                           "omacar candlog --profile honda-crz-2015 --interval 10")
        else:
            # Two DIFFERENT sweeps, and conflating them turns a clean negative
            # result into a false positive. README:483 records that the
            # 0x0000-0x0FFF data sweep returned NOTHING -- 8192 identifiers,
            # zero responders, on modules that answered 22F181 normally
            # immediately afterwards. The part numbers came from a separate
            # F1xx identification sweep, entirely outside that range. The empty
            # sweep is a real finding and it must not be reported as a find.
            row["note"] = ("No live IMA quantity of any kind has ever been "
                           "captured on this car. Service 0x22 was swept over "
                           "0x0000-0x0FFF and returned nothing at all; it "
                           "answers only in the F100-F19F identification "
                           "block, with static part numbers. Service 0x21, "
                           "which is where a 2015 Honda would most likely keep "
                           "live data, has never been asked with a 29-bit "
                           "header.")
            row["next"] = "Ask service 0x21, which has never been tried here."
        out.append(row)

    # Pack remaining life is the odd one out and deserves its own entry: it is
    # generic OBD-II, not Honda-proprietary, and this car lists it in its own
    # supported bitmap. That makes it the cheapest thing on this page by a
    # wide margin -- and for a long time it had still never been asked, because
    # it was in no poll tier and there was no column to put it in. Both are now
    # fixed (telemetry.SLOW, samples.soc), so the first drive with the adapter
    # plugged in either fills this row or proves the bitmap was lying.
    row = {"id": "pack_remaining", "group": BATTERY,
           "label": "Hybrid pack remaining life", "unit": "%",
           "about": "Generic OBD-II mode 01 PID 0x5B. Not manufacturer data: "
                    "a standard reading this car says it supports.",
           "state": UNDISCOVERED, "value": None, "at": None, "source": None,
           "note": None, "next": None, "command": CMD_DOCTOR, "safety": SAFETY}
    val = values.get(PACK_REMAINING)
    if isinstance(val, (int, float)):
        row["value"] = float(val)
        row["at"] = live.get("t")
        row["source"] = "live.json"
        row["state"] = MEASURED if (age is not None and age <= LIVE_STALE) else STALE
        if row["state"] == STALE:
            row["note"] = ("Last published %s ago. The daemon has stopped "
                           "publishing, so this is history."
                           % _ago(age))
    elif PACK_REMAINING in supported:
        row["note"] = ("The car lists this PID as supported in its own bitmap, "
                       "and nothing has ever asked for it. A support bitmap "
                       "can be wrong, so this is a claim about the bitmap, not "
                       "a reading.")
        # This used to read "it is in no poll tier and there is no samples
        # column to store it in". Both were true when written and neither is
        # now: HYBRID_BATTERY_REMAINING is in telemetry.SLOW and `samples` has
        # a `soc` column. Leaving that text would have told the owner to go and
        # add something that is already there.
        row["next"] = ("It is polled in lib/telemetry.py's SLOW tier and "
                       "stored in the samples `soc` column, so the next drive "
                       "with the adapter plugged in will answer this. If it "
                       "stays empty after a drive, the bitmap was wrong and "
                       "the ECU does not really serve it.")
    else:
        row["note"] = ("This car has not listed the PID as supported in any "
                       "bitmap we have on file.")
        row["next"] = "Re-read the supported list: " + CMD_DOCTOR
    out.append(row)
    return out


def _ago(secs):
    if secs is None:
        return "an unknown time"
    if secs < 90:
        return "%d seconds" % int(secs)
    if secs < 5400:
        return "%d minutes" % int(secs / 60)
    if secs < 172800:
        return "%d hours" % int(secs / 3600)
    return "%d days" % int(secs / 86400)


# ---- pack health over time --------------------------------------------------

def pack_health():
    """The longitudinal frame, and an honest account of how full it is.

    A 190,000-mile hybrid's pack degradation is the single most valuable thing
    this section could track, and there is currently nothing to plot: the only
    quantity that would show degradation is PID 0x5B, which has never been
    polled and has no column in the samples table.

    So this returns the frame with the real series that DO exist in it -- the
    per-module flagged count over time, and the battery voltage the DTC logger
    reads before every sample -- and states plainly that the pack-capacity
    series is empty and why. Building the frame now means it fills as he
    drives rather than starting from zero on the day the PID is added.
    """
    mods = modules()
    series = []
    for header, slot in sorted(mods.items()):
        pts = slot.get("count_seen") or []
        if not pts:
            continue
        series.append({
            "id": header, "label": slot["label"],
            "what": "monitors flagged",
            "points": [{"t": p["t"], "v": p["n"]} for p in pts],
            "n": len(pts),
            "from": pts[0]["t"], "to": pts[-1]["t"],
            "min": min(p["n"] for p in pts), "max": max(p["n"] for p in pts),
            # The count swinging between 1 and 18 between samples is the
            # signature of a not-run list rather than a fault list, and saying
            # so beside the line is the difference between a scary chart and
            # an explained one.
            "note": ("This is how many monitors the module was flagging, not "
                     "how many faults it had. Every status byte captured on "
                     "this car so far says the monitor has not run."),
        })

    volts = []
    for _path, rec in _dtclog_records():
        v, t = rec.get("volts"), rec.get("t")
        if isinstance(v, (int, float)) and isinstance(t, (int, float)):
            volts.append({"t": t, "v": float(v)})
    volts.sort(key=lambda p: p["t"])
    if volts:
        series.append({
            "id": "volts", "label": "Bus voltage at the connector",
            "what": "volts",
            "points": volts, "n": len(volts),
            "from": volts[0]["t"], "to": volts[-1]["t"],
            "min": min(p["v"] for p in volts), "max": max(p["v"] for p in volts),
            "note": ("ATRV, read before every DTC sample. It is the 12 V "
                     "system, not the IMA pack -- but it is the number that "
                     "says whether a sweep is safe to start."),
        })

    return {
        "series": series,
        "capacity": {
            "have": False,
            "series": [],
            "why": ("Pack capacity would come from mode 01 PID 0x5B, which "
                    "this car lists as supported and which nothing has ever "
                    "polled. There is no column for it in the samples table "
                    "and it is in no poll tier, so there is no history to "
                    "draw -- not a flat line, an absent one."),
            "fills_when": ("Add HYBRID_BATTERY_REMAINING to lib/telemetry.py's "
                           "SLOW tier and give the samples table a column, and "
                           "every drive from then on contributes a point."),
        },
        "span": _span(series),
    }


def _span(series):
    lo = [s["from"] for s in series if s.get("from")]
    hi = [s["to"] for s in series if s.get("to")]
    if not lo or not hi:
        return None
    return {"from": min(lo), "to": max(hi), "days": (max(hi) - min(lo)) / 86400.0}


# ---- what has been asked ----------------------------------------------------

def _prospect_sweeps():
    """Every saved prospector run that touched a hybrid module."""
    out = []
    try:
        paths = sorted(glob.glob(os.path.join(connect.STATE, "prospect-*.json")))
    except OSError:
        paths = []
    for p in paths:
        doc = _load_json(p)
        if not doc:
            continue
        headers = [str(x).upper() for x in (doc.get("headers") or [])]
        found = doc.get("found") if isinstance(doc.get("found"), list) else []
        svc = doc.get("service")
        out.append({
            "at": _stamp_of(p),
            "file": os.path.basename(p),
            "service": ("0x%02X" % svc) if isinstance(svc, int) else str(svc),
            "headers": headers,
            "hybrid": [h for h in headers if h in hybrid_modules()],
            "responders": len(found),
            # A responder list where every payload is static is a list of part
            # numbers, and the prospector already records which bytes moved.
            "varying": sum(1 for f in found
                           if isinstance(f, dict) and f.get("varying")),
            # A "responder" carrying zero payload bytes answered with nothing.
            # That is how the 220062 sweep produced three hits on three modules
            # at once: the request string contains the positive-response marker
            # 62 and the adapter echoes the request, so the echo classified as
            # a reply. lib/elm.py's classify() catches it now. Counting empty
            # payloads separately means the screen can show the hit and say
            # why it is not one, rather than either hiding it or believing it.
            "empty": sum(1 for f in found
                         if isinstance(f, dict) and not f.get("payload_len")),
        })
    return out


def _probe_files():
    """Saved `omacar dtc` runs: which 0x19 subfunctions each module answered.

    Two shapes are accepted because two exist. The current one is what
    lib/dtc.py --save writes; the older one was transcribed by hand from
    console output before --save existed and says so in its own provenance
    field. The old one is the only record that the engine module answered
    nothing at all on 0x19, which is worth keeping.
    """
    out = []
    try:
        paths = sorted(glob.glob(os.path.join(connect.STATE, "dtc-*.json")))
    except OSError:
        paths = []
    for p in paths:
        doc = _load_json(p)
        if not doc:
            continue
        rec = {"file": os.path.basename(p), "at": _stamp_of(p),
               "provenance": doc.get("provenance"), "modules": {}}
        results = doc.get("results")
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                header = str(row.get("header") or "").upper()
                if not header:
                    continue
                slot = rec["modules"].setdefault(header, {"answered": [],
                                                          "refused": []})
                sub = str(row.get("sub") or "")
                if row.get("kind") == "positive":
                    slot["answered"].append(sub)
                elif sub:
                    slot["refused"].append(sub)
        mods = doc.get("modules")
        if isinstance(mods, dict):
            for header, body in mods.items():
                if not isinstance(body, dict):
                    continue
                slot = rec["modules"].setdefault(str(header).upper(),
                                                 {"answered": [], "refused": []})
                subs = body.get("subfunctions")
                if isinstance(subs, list):
                    slot["answered"] += [str(s) for s in subs]
                # The note is carried whatever the answer was. "never probed
                # for 0x19" is as much a finding as "NO DATA on every
                # subfunction", and dropping it turns an unasked question into
                # a silent one.
                if body.get("note"):
                    slot["note"] = body["note"]
                elif body.get("supports_0x19") is False:
                    slot["note"] = "no answer on 0x19"
        if rec["modules"]:
            out.append(rec)
    return out


def discovery():
    """What has been swept, what answered, and what to ask next.

    The "what next" half is deliberately delegated to lib/frontier.py rather
    than recomputed. frontier already models exactly this -- which identifier
    ranges have been asked of which module on which service, merged into
    intervals so the next question is a subtraction. A second implementation
    here would be a second answer to the same question, and the two would
    disagree the first time a sweep was interrupted.
    """
    key = _vehicle_key()
    doc = frontier.load(key)

    ranges = []
    for header in list(hybrid_modules()) + list(NEIGHBOURS):
        for service in (0x22, 0x21):
            # THE TWO SERVICES DO NOT HAVE THE SAME SIZE OF SPACE, and saying
            # they do would be a made-up number in the one place this screen
            # is meant to be counting honestly. 0x22 takes a two-byte DID and
            # runs to 0xA5FF; 0x21 takes a ONE-byte local identifier and there
            # are 256 of them, total. lib/protocols.py owns that rule -- it is
            # the same call prospect makes to build the request -- so this asks
            # rather than deciding for itself.
            width = protocols.id_width(None, service)
            if width <= 2:
                lo, hi, chunk = 0x00, 0xFF, 0x100
            else:
                # lib/autodisc.py DEFAULT_RANGE, in the blocks it sweeps in.
                lo, hi, chunk = 0x0000, 0xA5FF, 0x1000
            svc = (doc.get("services") or {}).get(frontier.key(service, header))
            spans = frontier.merge((svc or {}).get("swept") or [])
            inside, total, percent = frontier.progress(doc, service, header, lo, hi)
            nxt = frontier.next_gap(spans, lo, hi, chunk)
            ranges.append({
                "header": header,
                "label": hybrid_modules().get(header) or NEIGHBOURS.get(header),
                "hybrid": header in hybrid_modules(),
                "service": "0x%02X" % service,
                "swept": inside, "total": total, "percent": round(percent, 2),
                "found": len((svc or {}).get("found") or []),
                "last": (svc or {}).get("last"),
                # Printed at the identifier's own width. "0000-00FF" for a
                # one-byte service reads as a range four times the size of the
                # one that exists.
                "next": (("%0*X-%0*X" % (width, nxt[0], width, nxt[1]))
                         if nxt else None),
            })

    mods = modules()
    answered = []
    for header, slot in sorted(mods.items()):
        if slot["catalogue"]:
            answered.append({
                "header": header, "label": slot["label"],
                "service": "0x19", "sub": "0A",
                "what": "%d codes this module can set" % len(slot["catalogue"]),
                "at": slot["catalogue_last"],
            })
        if slot["observations"]:
            answered.append({
                "header": header, "label": slot["label"],
                "service": "0x19", "sub": "01FF / 02FF",
                "what": "%d distinct codes flagged across %d observations"
                        % (len(slot["flagged"]), slot["observations"]),
                "at": slot["last_seen"],
            })

    return {
        "frontier_file": frontier.path_for(key),
        "frontier_empty": not (doc.get("services") or {}),
        "ranges": ranges,
        "answered": answered,
        "sweeps": _prospect_sweeps(),
        "probes": _probe_files(),
        "next": next_steps(),
    }


def next_steps():
    """The ordered list of things worth doing, cheapest and most-untried first.

    Ordered by what is unknown rather than by what is easy: the top of this
    list is the question nobody has asked, not the sweep that takes longest.
    """
    steps = []
    live, _age = _live()
    supported = _supported()

    steps.append({
        "id": "service21",
        "title": "Ask service 0x21 on the hybrid modules",
        "why": ("The only 0x21 sweep on record used an 11-bit header (07E0) on "
                "a car running 29-bit CAN, so it proved nothing. 0x21 is a "
                "read-only service, it is already whitelisted in lib/elm.py, "
                "and it is the most likely remaining home for live IMA data "
                "on a car of this age. About two minutes of bus time."),
        "command": CMD_0X21,
        "safety": SAFETY,
        "cost": "1,024 requests across four modules",
    })

    if PACK_REMAINING in supported:
        steps.append({
            "id": "pid5b",
            "title": "Poll mode 01 PID 0x5B once",
            "why": ("The car lists hybrid pack remaining life as supported and "
                    "nothing has ever asked. One request settles whether the "
                    "bitmap is telling the truth, and if it is, it is a "
                    "trendable pack-health percentage that needs no Honda "
                    "discovery at all."),
            "command": CMD_DOCTOR,
            "safety": SAFETY,
            "cost": "one request",
            "note": ("No command polls it into storage yet: it is in no tier in "
                     "lib/telemetry.py and the samples table has no column. "
                     "`omacar doctor` re-reads the supported bitmap; storing a "
                     "reading needs that one-line change first."),
        })

    mods = modules()
    if any(not m["catalogue"] for m in mods.values()) or not mods:
        steps.append({
            "id": "dtc",
            "title": "Re-read the fault catalogues with the payloads kept",
            "why": ("The catalogue is the map of what each hybrid controller "
                    "measures. --save keeps the raw payload, so a decoder bug "
                    "costs a re-parse rather than another trip to the car."),
            "command": CMD_DTC, "safety": SAFETY, "cost": "about 24 requests",
        })

    steps.append({
        "id": "did-extend",
        "title": "Extend the 0x22 sweep past 0x0FFF, in short bursts",
        # Counted, not estimated: 0x1000 identifiers asked of the 0xA5FF+1 that
        # lib/autodisc.py DEFAULT_RANGE calls the manufacturer space. An
        # earlier draft said "about four per cent", which is not a figure any
        # denominator in this repo produces.
        "why": ("Only 0x0000-0x0FFF has been asked -- 4,096 identifiers of the "
                "42,496 in the ISO 14229 manufacturer space, so about nine "
                "tenths of it has never been tried. `omacar discover` re-checks "
                "road speed before every 48-identifier burst and resumes across "
                "weeks from the frontier file, so this can simply live on the "
                "car instead of being a seventy-minute sitting."),
        "command": CMD_0X22_DEEP,
        "safety": SAFETY,
        "cost": "spread over many short bursts; resumable",
    })

    steps.append({
        "id": "drive",
        "title": "Log DTC status across a long drive",
        "why": ("Every status byte ever captured on this car says the monitor "
                "has not run this cycle. Either the drives logged were too "
                "short, or these monitors only clear under conditions not yet "
                "met -- and the only way to tell is a long drive with the "
                "logger running. This is safe while moving: it sends about "
                "eight requests every few minutes."),
        "command": CMD_DTCLOG,
        "safety": "Safe while driving. It still refuses below 11.8 V.",
        "cost": "eight requests every few minutes",
    })

    if not _learned():
        steps.append({
            "id": "learn",
            "title": "Run a learning pass so the app has a module map",
            "why": ("`omacar learn` has never been run on this car, so the "
                    "learned-module profile is empty and the Learn screen has "
                    "nothing to show. Fifteen seconds with the engine idling "
                    "fills it."),
            "command": CMD_LEARN, "safety": SAFETY, "cost": "about a minute",
        })
    return steps


def _learned():
    """The learned-module profile, if a learning pass has ever written one."""
    try:
        path = os.path.join(connect.STATE, "profiles",
                            "%s.learned.json" % _vehicle_key())
    except Exception:                                          # noqa: BLE001
        return None
    doc = _load_json(path)
    if not doc or not isinstance(doc.get("modules"), dict) or not doc["modules"]:
        return None
    return doc


def _vehicle_key():
    try:
        return garage.current()
    except Exception:                                          # noqa: BLE001
        return "unknown"


def _vehicle():
    """Enough of the car record to label the screen and own up to a simulator."""
    try:
        return garage.describe(_vehicle_key())
    except Exception:                                          # noqa: BLE001
        return {"key": "unknown", "simulated": False, "vin": None, "name": None}


# ---- the whole picture ------------------------------------------------------

def summary():
    """Everything the IMA screen needs, in one call that cannot fail.

    One endpoint rather than five because every panel on that screen is a
    different view of the same question -- what does this car's hybrid half
    actually tell us -- and five requests would let them disagree with each
    other about it mid-render.
    """
    try:
        car = _vehicle()
        mods = modules()
        qs = quantities()
        return {
            "vehicle": {
                "key": car.get("key"), "vin": car.get("vin"),
                "name": car.get("name"),
                # The rest of the app says so on every screen when the record
                # is the simulator, and an IMA section that quietly did not
                # would be the one screen where made-up hybrid numbers looked
                # real.
                "simulated": bool(car.get("simulated")),
            },
            "states": STATE_MEANS,
            # The three things a hybrid is, so the screen can be organised the
            # way an owner asks about it -- how is my battery, what is the
            # motor doing, is the regen working -- rather than as one flat list
            # of quantities we mostly do not have.
            "groups": [{"id": g, "label": lab, "about": why}
                       for g, lab, why in GROUPS],
            "quantities": qs,
            "measured": sum(1 for q in qs if q["state"] == MEASURED),
            "undiscovered": sum(1 for q in qs if q["state"] == UNDISCOVERED),
            "modules": [mods[h] for h in sorted(mods)],
            "candidates": _candidates(),
            "health": pack_health(),
            "discovery": discovery(),
            "sources": {
                "dtclog": len(_dtclog_files()),
                "learned": bool(_learned()),
                "state_dir": connect.STATE,
            },
            "generated": time.time(),
        }
    except Exception as e:                                     # noqa: BLE001
        # The contract is that this never raises. A screen that says it could
        # not assemble itself is recoverable; a 500 from the API is a blank
        # page with nothing explaining why.
        return {"error": "%s: %s" % (type(e).__name__, e),
                "vehicle": {"simulated": False}, "quantities": [],
                "modules": [], "candidates": [], "states": STATE_MEANS,
                "health": {"series": [], "capacity": {"have": False}},
                "discovery": {"ranges": [], "answered": [], "sweeps": [],
                              "probes": [], "next": []},
                "generated": time.time()}


def main(argv):
    """`python lib/ima.py` -- the same picture, on a terminal.

    Not a courtesy: this is how the summary gets checked against the files
    without a browser, and it is how the test suite's expectations were read
    off the real captures in the first place.
    """
    BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
    GREEN, YELLOW = "\033[32m", "\033[33m"
    s = summary()
    if s.get("error"):
        print("\n  could not assemble: " + s["error"] + "\n")
        return 1
    v = s["vehicle"]
    print(f"\n  {BOLD}IMA{RESET}  {DIM}{v.get('name') or v.get('key')}"
          f"{' · SIMULATED' if v.get('simulated') else ''}{RESET}\n")

    for m in s["modules"]:
        print(f"  {BOLD}{m['header']}{RESET}  {DIM}{m['label']}{RESET}")
        print(f"    catalogue      {len(m['catalogue'])} codes"
              f"  {DIM}({m['catalogue_seen']} captures){RESET}")
        print(f"    flagged        {len(m['flagged'])} distinct"
              f"  {DIM}({m['observations']} observations){RESET}")
        if m["all_not_run"]:
            print(f"    {YELLOW}every observation says the monitor has not run"
                  f"{RESET}")
        if m["distinct_counts"]:
            print(f"    counts seen    "
                  f"{', '.join(str(c) for c in m['distinct_counts'])}")
        print()

    print(f"  {BOLD}Quantities{RESET}")
    for q in s["quantities"]:
        tone = GREEN if q["state"] == MEASURED else YELLOW if q["state"] == STALE else DIM
        val = ("%.1f %s" % (q["value"], q["unit"])) if q["value"] is not None else "—"
        print(f"    {q['label']:<28} {tone}{q['state']:<13}{RESET} {val}")
    print()

    print(f"  {BOLD}Next{RESET}")
    for st in s["discovery"]["next"]:
        print(f"    {st['title']}")
        print(f"      {DIM}{st['command']}{RESET}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
