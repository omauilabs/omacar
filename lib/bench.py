"""The bench: an emulated adapter and car, with a module that answers what a
real one would.

`omacar bench start <scenario>` used to exec the ELM327-emulator's own
console script, which is fine for the scenarios it ships with and useless for
proving anything it does not: the emulator can only be taught a new answer
from its interactive prompt, and the bench runs headless.

So the bench is started from here instead. The emulator is imported, its
message table is extended IN MEMORY with the scenarios below, and it is run
in-process with the same batch behaviour as before: the pseudo-terminal's
name goes to the batch file, the process idles, SIGTERM ends it. Nothing in
the installed package is modified.

THE `actuator` SCENARIO.

The stock `car` plus one thing no shipped scenario has: a module that
implements UDS 0x2F for one identifier, F0A1, the bench's cooling fan.
Commanded on (control option 03, a state byte) it answers positively and the
calculated engine load it reports rises, so a trace taken through the
borrowed link shows the fan's effect. Commanded to 00 it answers positively
and the load drops back. Any other 0x2F identifier is refused with 0x31,
requestOutOfRange, which is what a real module says to an identifier it does
not control. An extended session request is answered positively because the
profile format allows an entry to ask for one first.

This is the whole real-car actuator path exercised end to end on a desk:
the profile entry, the tier, the arm, the motion check, the voltage floor,
the session, the command, the observation, the release and the ledger. What
it cannot prove is which identifier moves which thing on a particular car.
That is a fact about the car, and it is found on the car.
"""

import os
import signal
import sys
import time

# lib/elm.py is OmaCar's own transport module, and this directory is first on
# sys.path when the script is run -- so `import elm` would find it and the
# emulator package would be unreachable. Drop this directory before importing.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]


def actuator_scenario(ObdMessage, helpers):
    """The `car` scenario with a fan a tester can command."""
    PA, NA, FOOTER, DATA_FOOTER = helpers
    sc = dict(ObdMessage["car"])

    def fan_on(self, cmd, pid, uc_val):
        self.omacar_fan = True
        return PA("F0 A1 03 " + cmd[8:10])

    def fan_off(self, cmd, pid, uc_val):
        self.omacar_fan = False
        return PA("F0 A1 00")

    # The emulator echoes the identifier itself for 0x22 and 0x2E (two bytes
    # after the service id, per its uds_sid_pos_answer table), so a positive
    # answer here carries only what follows it.
    def cfg_read(self, cmd, pid, uc_val):
        return PA(getattr(self, "omacar_cfg", "5A"))

    def cfg_write(self, cmd, pid, uc_val):
        self.omacar_cfg = cmd[6:8]
        return PA("")

    def load(self, cmd, pid, uc_val):
        # Fan on: the alternator works harder and the ECU reports more load.
        return PA("5A" if getattr(self, "omacar_fan", False) else "1E")

    sc.update({
        "OMACAR_SESSION_EXT": {
            "Request": "^1003" + FOOTER,
            "Descr": "Diagnostic session control: extended",
            "Priority": 1,
            "Response": PA("03 00 32 01 F4"),
        },
        "OMACAR_FAN_ON": {
            "Request": "^2FF0A103[0-9A-F]{2}" + FOOTER,
            "Descr": "I/O control: bench cooling fan, short-term adjustment",
            "Priority": 1,
            "ResponseFooter": fan_on,
        },
        "OMACAR_FAN_OFF": {
            "Request": "^2FF0A100" + FOOTER,
            "Descr": "I/O control: bench cooling fan, return control to ECU",
            "Priority": 1,
            "ResponseFooter": fan_off,
        },
        "OMACAR_IOCTL_OTHER": {
            "Request": "^2F" + DATA_FOOTER,
            "Descr": "I/O control: any other identifier is out of range",
            "Priority": 9,
            "Response": NA("31"),
        },
        # A writable configuration byte, for proving write-by-identifier
        # end to end: read it, write it, read it back. F1A0 is in the
        # manufacturer range and nowhere near the legislated OBD range the
        # deny-list refuses.
        "OMACAR_CFG_READ": {
            "Request": "^22F1A0" + FOOTER,
            "Descr": "Read bench configuration byte",
            "Priority": 1,
            "ResponseFooter": cfg_read,
        },
        "OMACAR_CFG_WRITE": {
            "Request": "^2EF1A0[0-9A-F]{2}" + FOOTER,
            "Descr": "Write bench configuration byte",
            "Priority": 1,
            "ResponseFooter": cfg_write,
        },
        "OMACAR_WDBI_OTHER": {
            "Request": "^2E" + DATA_FOOTER,
            "Descr": "Write to any other identifier is out of range",
            "Priority": 9,
            "Response": NA("31"),
        },
        # Stationary. The stock car cycles through road speeds, and the
        # motion check would rightly refuse to command anything on it.
        "SPEED": {
            "Request": "^010D" + FOOTER,
            "Descr": "Vehicle speed (the bench does not move)",
            "Response": PA("00"),
        },
        "ENGINE_LOAD": {
            "Request": "^0104" + FOOTER,
            "Descr": "Calculated engine load (rises with the bench fan)",
            "ResponseFooter": load,
        },
    })
    return sc


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print("  usage: bench.py <batch-file> <scenario>")
        return 2
    batch, scenario = argv[0], argv[1]
    try:
        from elm.elm import Elm
        from elm import obd_message as om
    except ImportError as e:
        print(f"omacar: the bench emulator is not installed ({e}); "
              f"run: omacar setup", file=sys.stderr)
        return 1

    om.ObdMessage["actuator"] = actuator_scenario(
        om.ObdMessage, (om.PA, om.NA, om.ELM_FOOTER, om.ELM_DATA_FOOTER))
    if scenario not in om.ObdMessage:
        print(f"omacar: no bench scenario named {scenario!r}. Have: "
              + ", ".join(sorted(k for k in om.ObdMessage
                                 if k not in ("default", "AT"))),
              file=sys.stderr)
        return 1

    emulator = Elm(batch_mode=True)
    emulator.scenario = scenario
    emulator.set_sorted_obd_msg()
    stop = {"now": False}

    def bye(*_a):
        stop["now"] = True

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    with emulator as session:
        pty = None
        for _ in range(60):
            pty = session.get_pty()
            if pty:
                break
            time.sleep(0.1)
        if not pty:
            print("omacar: the bench emulator did not open a port",
                  file=sys.stderr)
            return 1
        tmp = batch + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(pty + "\n")
        os.replace(tmp, batch)
        while not stop["now"]:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
