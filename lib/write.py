"""Write operations — clearing codes, functional tests, and settings.

WHY THIS IS A SEPARATE FILE WITH A LOCK ON IT.

Everything else in OmaCar asks the car questions. This file is the only place
that tells it to do something, and the difference is not academic: a functional
test can spin a radiator fan, cycle an ABS pump or command a fuel injector, and
a settings write can leave a module configured in a way its own dashboard has no
way to show you.

That is normal scan-tool capability. Every professional tool does it, and a tool
that cannot clear a code after you have fixed the fault is a viewer, not a
diagnostic. So it is here, it is complete, and it is not hidden behind a
pretence that reading is the only safe thing to do.

It is also armed rather than always-on, because the failure modes are different
in kind from a bad read. A read that goes wrong returns nonsense. A write that
goes wrong at the wrong moment moves something attached to the car you are
sitting in.

WHAT IS DELIBERATELY NOT HERE.

Services 0x34 / 0x36 / 0x37 -- RequestDownload, TransferData,
RequestTransferExit -- reprogram flash memory. They are absent, and not because
writing is frightening: they require a manufacturer-signed firmware image that
this tool does not have and cannot produce, and a transfer that is interrupted
or mismatched leaves a module with no valid firmware at all. That is a tow
truck, not a fault code, and it is a different class of outcome from anything
else in this file.
"""

import json
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import connect  # noqa: E402

ARMED = os.path.join(connect.STATE, "write-armed")

# How long an arm lasts. Long enough to finish a job, short enough that walking
# away from the car does not leave it armed until the next reboot.
ARM_SECONDS = 900

# Services this file may emit once armed. Each entry is the service, a short
# name, and the consequence the user is shown BEFORE it is sent -- not in a
# manual, not in a tooltip, but in the confirmation itself.
WRITE_SERVICES = {
    # Mode 04 is the generic-OBD-II clear, and it is a WRITE despite sitting in
    # the middle of the read-only mode numbers. It is listed here rather than in
    # READ_ONLY_SERVICES for exactly that reason: the numbering invites the
    # mistake, and a service that erases evidence must not be reachable because
    # 0x03 and 0x06 happened to be safe.
    0x04: ("Clear emissions data (generic OBD-II)",
           "The standard mode-04 clear, understood by every car since 1996.\n"
           "  - Erases emissions fault codes and freeze-frame data.\n"
           "  - Resets every readiness monitor to 'not complete', so the car "
           "will FAIL an emissions test for days.\n"
           "  - If the fault is still present the code comes straight back."),
    0x14: ("Clear diagnostic information",
           "Erases stored fault codes AND their freeze-frame data, and resets "
           "every readiness monitor to 'not complete'.\n"
           "  - The evidence of an intermittent fault is gone permanently.\n"
           "  - The car will FAIL an emissions test until the monitors run "
           "again, which typically takes several days of mixed driving.\n"
           "  - If the underlying fault is still present the code returns."),
    0x10: ("Diagnostic session control",
           "Puts a module into an extended or programming session.\n"
           "  - Some modules disable normal functions while in a non-default "
           "session.\n"
           "  - The session ends on its own if the tool stops talking, which is "
           "why it must not be entered while driving."),
    0x27: ("Security access",
           "Unlocks a module's protected functions using a seed/key exchange.\n"
           "  - Repeated failed attempts can lock a module out for a period, "
           "and on some ECUs that lockout survives a power cycle."),
    0x2F: ("Input/output control",
           "Commands an actuator directly, overriding the ECU's own control.\n"
           "  - This physically MOVES things: fans, valves, pumps, injectors, "
           "relays.\n"
           "  - Anything commanded stays commanded until released or the "
           "session ends.\n"
           "  - Never with the vehicle in gear, on a lift, or with anyone near "
           "moving parts."),
    0x31: ("Routine control",
           "Starts a built-in routine, such as a self-test or a calibration.\n"
           "  - Routines can move actuators, run the engine to a target speed, "
           "or apply brakes.\n"
           "  - An interrupted calibration can leave a module needing a "
           "complete re-calibration to work correctly."),
    0x2E: ("Write data by identifier",
           "Changes a stored configuration value inside a module.\n"
           "  - There is no undo. The previous value is gone unless YOU wrote "
           "it down.\n"
           "  - A wrong value can disable a feature, or make a module behave in "
           "a way its own diagnostics will not flag as a fault.\n"
           "  - Always record the value you read before you write over it."),
}

# Below this, refuse. Higher than the read floor on purpose: a read interrupted
# by a brownout returns garbage and you try again, while a write interrupted
# partway through leaves the module holding half a change.
WRITE_VOLTS = 12.2


def armed_until():
    """Deadline on the current arm, or None. Expired arms are removed."""
    try:
        with open(ARMED, encoding="utf-8") as f:
            until = float(json.load(f)["until"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if time.time() >= until:
        disarm()
        return None
    return until


def is_armed():
    return armed_until() is not None


def arm(seconds=ARM_SECONDS):
    os.makedirs(connect.STATE, exist_ok=True)
    until = time.time() + seconds
    tmp = ARMED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"until": until, "armed_at": time.time()}, f)
    os.replace(tmp, ARMED)
    return until


def disarm():
    try:
        os.remove(ARMED)
    except OSError:
        pass


def describe(service):
    """(name, consequences) for a write service, or None if not one."""
    return WRITE_SERVICES.get(service)


BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
YELLOW, GREEN = "\033[33m", "\033[32m"


def status_text():
    until = armed_until()
    if until is None:
        return f"  write mode: {DIM}disarmed{RESET}  (reads always work)"
    left = int(until - time.time())
    return (f"  write mode: {YELLOW}ARMED{RESET}  "
            f"{DIM}{left // 60}m {left % 60}s remaining{RESET}")



# ------------------------------------------------------------- the agent queue
#
# `omacar mcp` gives an agent request_write, which queues a proposal here and
# sends nothing. That is only honest if a person can actually SEE the queue --
# a write tool that says "the owner can review it" over a file nobody reads is
# a lie with extra steps. So it is listed by the same command that lists what
# writing means, and each entry can be declined by hand. Running one is the
# owner's act, done through the normal armed path, never from here.
QUEUE = os.path.join(connect.STATE, "agent-writes.jsonl")

# ------------------------------------------------------------------ the ledger
#
# Every write that leaves this machine, appended by the gate itself -- see
# Elm.request() -- so there is no path that sends a write service and does not
# leave a line. It records what was sent, to which address, and what answered,
# and it is never rewritten: a declined proposal above is edited in place
# because it is a proposal; a line here is a fact about the past.
SENT = os.path.join(connect.STATE, "writes.jsonl")


def sent(service, header, request, outcome, detail="", who="", why=""):
    os.makedirs(connect.STATE, exist_ok=True)
    row = {"at": time.time(), "service": f"0x{int(service):02X}",
           "header": header or "", "request": request,
           "outcome": outcome, "detail": (detail or "")[:160],
           "who": who or "", "why": why or ""}
    with open(SENT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def ledger(limit=50):
    """The last `limit` lines, oldest first."""
    out = []
    try:
        with open(SENT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out[-limit:]


def _print_ledger():
    rows = ledger()
    if not rows:
        print(f"  {DIM}nothing has been written to a car from this machine{RESET}\n")
        return
    print(f"  {BOLD}Writes that left this machine{RESET}  {DIM}append-only{RESET}\n")
    for r in rows:
        when = time.strftime("%d %b %H:%M:%S", time.localtime(r.get("at") or 0))
        tone = GREEN if r.get("outcome") == "positive" else YELLOW
        print(f"  {when}  {r.get('header', ''):9} {BOLD}{r.get('request')}{RESET}"
              f"  {tone}{r.get('outcome')}{RESET}  {DIM}{r.get('detail', '')}{RESET}")
    print()


def queued():
    """Every proposal an agent has left, newest last."""
    out = []
    try:
        with open(QUEUE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def decline(index, why=""):
    """Mark one proposal declined, by its position in `queued()`."""
    rows = queued()
    if not (0 <= index < len(rows)):
        return None
    rows[index]["status"] = "declined"
    rows[index]["declined_at"] = time.time()
    if why:
        rows[index]["declined_why"] = why
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, QUEUE)
    return rows[index]

def _print_queue():
    rows = queued()
    if not rows:
        return
    print(f"  {BOLD}Writes an agent has proposed{RESET}  {DIM}none of these has been sent{RESET}\n")
    for i, r in enumerate(rows, 1):
        state = r.get("status", "queued")
        mark = f"{DIM}declined{RESET}" if state == "declined" else f"{YELLOW}queued{RESET}"
        when = time.strftime("%d %b %H:%M", time.localtime(r.get("at") or 0))
        print(f"  {i:2}. {mark}  {when}  {BOLD}{r.get('header')}  {r.get('request')}{RESET}")
        for line in (r.get("consequence") or "").split("\n"):
            print(f"      {line}")
        if r.get("reasoning"):
            print(f"      {DIM}{r['reasoning'][:160]}{RESET}")
        print()
    print(f"  {DIM}To run one: arm write mode and send it yourself. To drop one: "
          f"omacar write decline <n>{RESET}\n")


def _cli_did(header=None, did=None, value=None):
    """`omacar write did <header> <did> [<value>]` -- read back, then ask.

    The same two calls the screen makes, with the same server-side checks:
    the tier and the deny-list first, then the arm, the motion check and the
    voltage floor, then a read, and only then -- on a typed yes -- the write.
    """
    if not header or not did:
        print("  usage: omacar write did <header> <did> [<value hex>]\n"
              "         read the identifier back; with a value, offer to write it")
        return 2
    import api
    import modes
    import ops
    d = modes.decide("write_did", ctx={"did": did})
    if not d.ok:
        print("\n  " + d.text().replace("\n", "\n  ") + "\n")
        return 1
    try:
        first = api.write_did(header, did, value, confirm=False, who="the terminal")
    except (api.BadRequest, ops.Refused) as e:
        print(f"\n  refused: {e}\n")
        return 1
    print(f"\n  {BOLD}{did} on {header}{RESET}  holds  {BOLD}{first['prior'] or '(empty)'}{RESET}")
    if not value:
        print()
        return 0
    print(f"\n  {YELLOW}{first['what']}{RESET}")
    for line in first["consequence"].split("\n"):
        print(f"    {DIM}{line}{RESET}")
    print(f"\n  would send  {BOLD}{first['would_send']}{RESET}   "
          f"({first['prior'] or '(empty)'} -> {value.replace(' ', '').upper()})")
    try:
        answer = input("\n  Write it? Type yes to proceed: ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        print("  nothing sent\n")
        return 0
    try:
        done = api.write_did(header, did, value, confirm=True, prior=first["prior"],
                             who="the terminal")
    except (api.BadRequest, ops.Refused) as e:
        print(f"\n  refused: {e}\n")
        return 1
    print(f"\n  {GREEN}written{RESET}  {did}: {done.get('prior') or '(empty)'} -> "
          f"{done.get('after') or '(unreadable)'}   reply {done.get('reply')}\n")
    return 0


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="omacar write", add_help=True)
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "arm", "disarm", "list", "queue", "decline",
                             "log", "did"])
    ap.add_argument("which", nargs="?", help="for decline: the proposal number; "
                                             "for did: the module address")
    ap.add_argument("rest", nargs="*", help="for did: <identifier> [<value hex>]")
    ap.add_argument("--minutes", type=float, default=ARM_SECONDS / 60.0)
    args = ap.parse_args(argv)

    if args.action == "status":
        print()
        print(status_text())
        print()
        return 0

    if args.action == "list":
        print(f"\n  {BOLD}What write mode allows{RESET}\n")
        for svc in sorted(WRITE_SERVICES):
            name, why = WRITE_SERVICES[svc]
            print(f"  {BOLD}0x{svc:02X}  {name}{RESET}")
            for line in why.split("\n"):
                print(f"    {DIM}{line}{RESET}")
            print()
        print(f"  {DIM}Reprogramming (0x34/0x36/0x37) is not implemented at all.{RESET}\n")
        _print_queue()
        return 0

    if args.action == "log":
        _print_ledger()
        return 0
    if args.action == "did":
        return _cli_did(args.which, *(args.rest or []))
    if args.action == "queue":
        print()
        _print_queue()
        return 0

    if args.action == "decline":
        try:
            n = int(args.which or "") - 1
        except ValueError:
            print("  usage: omacar write decline <number>   (see: omacar write queue)")
            return 1
        row = decline(n)
        if row is None:
            print(f"  no proposal number {n + 1}")
            return 1
        print(f"\n  declined: {row.get('header')} {row.get('request')}\n")
        return 0

    if args.action == "disarm":
        disarm()
        print(f"\n  {GREEN}disarmed{RESET}. Writes are refused again.\n")
        return 0

    # arm
    until = arm(args.minutes * 60.0)
    print(f"\n  {YELLOW}Write mode armed{RESET} for {args.minutes:g} minutes "
          f"(until {time.strftime('%H:%M:%S', time.localtime(until))}).\n")
    print(f"  OmaCar can now clear codes, run functional tests and change")
    print(f"  settings on this vehicle. Each operation still states what it")
    print(f"  does before it sends anything.\n")
    print(f"  {DIM}Writes still refuse while the car is moving, and below "
          f"{WRITE_VOLTS} V.{RESET}")
    print(f"  {DIM}Disarm early with: omacar write disarm{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
