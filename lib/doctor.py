"""omacar doctor — what are we talking to, and what will it tell us?

Writes a status cache the bar widget reads, so the widget never has to open
a serial connection of its own (which takes seconds and would block the bar).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import connect  # noqa: E402

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"

# The readings worth showing first: what the ambient meter will be driven from.
CORE = ["RPM", "SPEED", "ENGINE_LOAD", "COOLANT_TEMP", "INTAKE_TEMP",
        "THROTTLE_POS", "TIMING_ADVANCE", "SHORT_FUEL_TRIM_1",
        "LONG_FUEL_TRIM_1", "MAF", "FUEL_LEVEL", "RUN_TIME"]


def main():
    conn, port, kind = connect.connect()
    import obd

    status = conn.status()
    connected = status == obd.OBDStatus.CAR_CONNECTED
    proto = f"{conn.protocol_id()} — {conn.protocol_name()}" if connected else "—"
    supported = sorted(c.name for c in conn.supported_commands)

    print()
    print(f"  {BOLD}OmaCar doctor{RESET}")
    print()
    print(f"    source     {port}  {DIM}({kind}){RESET}")
    print(f"    status     {GREEN if connected else RED}{status}{RESET}")
    print(f"    protocol   {proto}")
    # Say what the protocol MEANS, not just its number. "6" tells somebody
    # nothing; "CAN 11/500, most cars since 2008" tells them whether the tool
    # is even on the right kind of wire for their vehicle.
    try:
        import protocols as _p
        _num = str(conn.protocol_id()) if connected else ""
        _d = _p.describe(_num)
        if _d:
            print(f"               {_p.summary(_num)}")
    except Exception:                                         # noqa: BLE001
        pass
    print(f"    supported  {len(supported)} commands")
    # Which car this is, and what the tree already knows about it -- the two
    # facts a car session needs before deciding what to try. The VIN is read
    # the same way the daemon reads it; the profile is the one the garage
    # would match. Nothing here opens a database.
    if connected:
        try:
            import survey
            import profile as profilelib
            import signals
            import actuate
            vin = survey.read_vin(conn, obd)
            if vin:
                print(f"    vin        {vin[:8]}{'…' if len(vin) > 8 else ''}")
                slug = profilelib.for_vin(vin)
                if slug:
                    doc, _ = profilelib.load(slug)
                    tiles = len(signals.validated(doc or {}))
                    buttons = list(actuate.reach(doc or {}))
                    print(f"    profile    {slug}  {DIM}{tiles} validated reading(s), "
                          f"{len(buttons)} actuator(s){(': ' + ', '.join(buttons)) if buttons else ''}{RESET}")
                else:
                    print(f"    profile    {DIM}none for this model yet — omacar learn, "
                          f"then omacar profile{RESET}")
            else:
                print(f"    vin        {DIM}not answered{RESET}")
        except Exception as why:                                # noqa: BLE001
            print(f"    profile    {DIM}not checked ({type(why).__name__}){RESET}")
    print()

    if connected:
        print(f"  {BOLD}Live readings{RESET}")
        print()
        for name in CORE:
            cmd = getattr(obd.commands, name, None)
            if cmd is None or cmd.name not in supported:
                print(f"    {name:<20} {DIM}not supported by this ECU{RESET}")
                continue
            r = conn.query(cmd)
            print(f"    {name:<20} {DIM}—{RESET}" if r.is_null() else f"    {name:<20} {r.value}")
        print()

        dtcs = conn.query(obd.commands.GET_DTC)
        codes = dtcs.value or []
        if codes:
            print(f"  {BOLD}Stored fault codes{RESET}")
            print()
            for code, desc in codes:
                print(f"    {RED}{code}{RESET}  {desc}")
        else:
            print(f"  {DIM}no stored fault codes{RESET}")
        print()

    os.makedirs(connect.STATE, exist_ok=True)
    with open(os.path.join(connect.STATE, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"port": port, "kind": kind, "status": str(status),
                   "connected": connected, "protocol": proto,
                   "supported": supported}, f)

    conn.close()
    return 0 if connected else 1


if __name__ == "__main__":
    sys.exit(main())
