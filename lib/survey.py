"""Everything about the car that is not a live reading.

The sample stream is one question asked five times a second. This is the other
half: what the ECU is holding, what it has tested, what it says it is. Slow,
occasional, and the difference between a working scan tool and a gauge.

  Mode 03 / 07 / 0A   stored, pending and permanent trouble codes
  Mode 02             the freeze frame for the stored one
  Mode 01 PID 01      MIL state and the readiness monitors
  Mode 06             on-board monitoring test results
  Mode 09             VIN and calibration

Everything lands in exactly the tables the simulator seeds, so the app cannot
tell the difference between a real car and a simulated one — which is the point
of having built it against a simulator at all.

Run on connect and then every few minutes, because none of it changes fast and
all of it costs bus time the gauge would rather have. The daemon calls
`survey(connection)`; `omacar survey` does one pass from the terminal.
"""

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garage   # noqa: E402
import records  # noqa: E402

# How often the daemon repeats it. Codes and monitors move on the scale of a
# drive cycle, not a second.
EVERY = 300

# Mode 06 is a long list on a modern car and most of it is uninteresting. These
# are the tests that carry a diagnosis: catalyst efficiency, oxygen sensors and
# their heaters, EVAP, and the per-cylinder misfire counters.
MODE06_WANTED = (
    "MONITOR_CATALYST_B1", "MONITOR_CATALYST_B2",
    "MONITOR_O2_B1S1", "MONITOR_O2_B1S2", "MONITOR_O2_B2S1", "MONITOR_O2_B2S2",
    "MONITOR_O2_HEATER_B1S1", "MONITOR_O2_HEATER_B1S2",
    "MONITOR_O2_HEATER_B2S1", "MONITOR_O2_HEATER_B2S2",
    "MONITOR_EVAP_150", "MONITOR_EVAP_090", "MONITOR_EVAP_040",
    "MONITOR_EVAP_020", "MONITOR_PURGE_FLOW",
    "MONITOR_MISFIRE_GENERAL",
    "MONITOR_MISFIRE_CYLINDER_1", "MONITOR_MISFIRE_CYLINDER_2",
    "MONITOR_MISFIRE_CYLINDER_3", "MONITOR_MISFIRE_CYLINDER_4",
    "MONITOR_MISFIRE_CYLINDER_5", "MONITOR_MISFIRE_CYLINDER_6",
    "MONITOR_EGR_B1", "MONITOR_EGR_B2",
    "MONITOR_VVT_B1", "MONITOR_VVT_B2",
)

# python-obd names its monitors after the standard; these are the plain-English
# versions, and the component each one is really about.
# python-obd hangs the monitors off the Status object as attributes suffixed
# `_MONITORING`, each a StatusTest with `available` and `complete`. The names
# and the order are ours: the three continuous monitors first, because that is
# how every emissions report in the world lists them.
MONITOR_NAMES = [
    ("MISFIRE_MONITORING", "Misfire", "continuous"),
    ("FUEL_SYSTEM_MONITORING", "Fuel System", "continuous"),
    ("COMPONENT_MONITORING", "Comprehensive Components", "continuous"),
    ("CATALYST_MONITORING", "Catalyst", "trip"),
    ("HEATED_CATALYST_MONITORING", "Heated Catalyst", "trip"),
    ("EVAPORATIVE_SYSTEM_MONITORING", "Evaporative System", "trip"),
    ("SECONDARY_AIR_SYSTEM_MONITORING", "Secondary Air System", "trip"),
    ("OXYGEN_SENSOR_MONITORING", "Oxygen Sensor", "trip"),
    ("OXYGEN_SENSOR_HEATER_MONITORING", "Oxygen Sensor Heater", "trip"),
    ("EGR_VVT_SYSTEM_MONITORING", "EGR System", "trip"),
    ("NMHC_CATALYST_MONITORING", "NMHC Catalyst", "trip"),
    ("NOX_SCR_AFTERTREATMENT_MONITORING", "NOx Aftertreatment", "trip"),
    ("BOOST_PRESSURE_MONITORING", "Boost Pressure", "trip"),
    ("EXHAUST_GAS_SENSOR_MONITORING", "Exhaust Gas Sensor", "trip"),
    ("PM_FILTER_MONITORING", "Particulate Filter", "trip"),
]


def open_db():
    os.makedirs(records.STATE, exist_ok=True)
    db = sqlite3.connect(records.DB, timeout=10.0)
    db.execute("""CREATE TABLE IF NOT EXISTS faults (
        code TEXT PRIMARY KEY, system TEXT, descr TEXT, detail TEXT,
        first_seen REAL, last_seen REAL, count INTEGER,
        status TEXT, severity TEXT, freeze TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS readiness (
        id TEXT PRIMARY KEY, name TEXT, kind TEXT, supported INTEGER,
        complete INTEGER, why TEXT, pos INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mode06 (
        mid TEXT PRIMARY KEY, name TEXT, component TEXT, value REAL,
        lo REAL, hi REAL, unit TEXT, note TEXT, pos INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS modules (
        id TEXT PRIMARY KEY, name TEXT, addr TEXT, system TEXT,
        generic INTEGER, part TEXT, sw TEXT, codes TEXT, pos INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mode06_history (
        mid TEXT, at REAL, value REAL, lo REAL, hi REAL,
        PRIMARY KEY (mid, at))""")
    db.execute("CREATE TABLE IF NOT EXISTS vehicle (k TEXT PRIMARY KEY, v TEXT)")
    return db


def value_of(result):
    if result is None or result.is_null():
        return None
    v = result.value
    return v


def severity_for(code):
    """How loudly to say it. Misfires and anything that will cook a converter
    are the ones worth interrupting somebody for."""
    c = (code or "").upper()
    if c.startswith("P03"):          # misfire family
        return "critical"
    if c.startswith(("P0", "P1", "P2")):
        return "warning"
    return "normal"


SYSTEMS = {
    "0": "Fuel & air metering", "1": "Fuel & air metering",
    "2": "Fuel & air metering", "3": "Ignition", "4": "Emissions",
    "5": "Speed & idle control", "6": "Computer output", "7": "Transmission",
    "8": "Transmission", "9": "Transmission", "A": "Hybrid",
    "B": "Hybrid", "C": "Hybrid",
}


def system_for(code):
    c = (code or "").upper()
    if len(c) < 3:
        return ""
    if c[0] == "P":
        return SYSTEMS.get(c[2], "Powertrain")
    return {"C": "Chassis", "B": "Body", "U": "Network"}.get(c[0], "")


# ---- the passes -------------------------------------------------------------

def read_codes(conn, obd, db, now):
    """Stored, pending and permanent codes, merged into what we already knew.

    A code we have seen before keeps its first-seen date and gains a count.
    One that has gone gets marked cleared rather than deleted, because the
    history of what a car has done is most of what makes the next fault
    diagnosable.
    """
    found = {}
    for cmd, status in (("GET_DTC", "stored"),
                        ("GET_CURRENT_DTC", "pending")):
        c = getattr(obd.commands, cmd, None)
        if c is None:
            continue
        got = value_of(conn.query(c, force=True))
        for entry in got or []:
            code, desc = (entry + ("", ""))[:2] if isinstance(entry, tuple) else (entry, "")
            if code:
                found[code] = {"descr": desc or "", "status": status}

    known = {r[0]: r for r in db.execute(
        "SELECT code, first_seen, count, status FROM faults")}
    fresh = []
    for code, f in found.items():
        prev = known.get(code)
        if not prev:
            fresh.append({"code": code, "status": f["status"],
                          "description": f["descr"],
                          "system": system_for(code),
                          "severity": severity_for(code)})
        db.execute(
            "INSERT INTO faults (code, system, descr, detail, first_seen, "
            "last_seen, count, status, severity, freeze) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET last_seen=excluded.last_seen, "
            "count=faults.count+1, status=excluded.status, "
            "descr=CASE WHEN excluded.descr != '' THEN excluded.descr "
            "ELSE faults.descr END",
            (code, system_for(code), f["descr"], "",
             prev[1] if prev else now, now, 1 if not prev else prev[2] + 1,
             f["status"], severity_for(code), ""))
    # Anything the ECU no longer reports has been cleared, by us or by a
    # battery disconnect or by itself after enough clean drive cycles.
    for code, prev in known.items():
        if code not in found and prev[3] in ("stored", "pending"):
            db.execute("UPDATE faults SET status='cleared', last_seen=? "
                       "WHERE code=?", (now, code))

    # Tell any plugin that a fault it has not seen before has appeared.
    #
    # After the writes, never before: a hook that fires and then the insert
    # fails would have announced something that did not happen. And wrapped,
    # because this runs inside the survey that feeds the gauge -- a plugin
    # must not be able to take that down.
    if fresh:
        try:
            import plugins
            plugins.fire("on-fault", {"faults": fresh, "at": now})
        except Exception:                                     # noqa: BLE001
            pass
    return len(found)


def read_freeze(conn, obd, db):
    """The freeze frame, attached to the code the ECU says it belongs to."""
    c = getattr(obd.commands, "FREEZE_DTC", None)
    if c is None:
        return 0
    got = value_of(conn.query(c, force=True))
    if not got:
        return 0
    code = got[0] if isinstance(got, tuple) else got
    if not code:
        return 0
    frame = {}
    # Mode 02 mirrors Mode 01, one PID at a time, for the frozen moment.
    for name, key in (("DTC_RPM", "rpm"), ("DTC_SPEED", "speed"),
                      ("DTC_COOLANT_TEMP", "coolant"),
                      ("DTC_ENGINE_LOAD", "load"),
                      ("DTC_LONG_FUEL_TRIM_1", "ltft"),
                      ("DTC_SHORT_FUEL_TRIM_1", "stft")):
        cmd = getattr(obd.commands, name, None)
        if cmd is None:
            continue
        v = value_of(conn.query(cmd, force=True))
        if v is None:
            continue
        frame[key] = round(float(v.magnitude if hasattr(v, "magnitude") else v), 1)
    if frame:
        db.execute("UPDATE faults SET freeze=? WHERE code=?",
                   (json.dumps(frame), code))
    return 1 if frame else 0


def read_readiness(conn, obd, db):
    """MIL state and the monitors, in the order the app expects them."""
    status = value_of(conn.query(obd.commands.STATUS))
    if status is None:
        return 0
    pos, n = 0, 0
    for attr, label, kind in MONITOR_NAMES:
        mon = getattr(status, attr, None)
        if mon is None:
            continue
        db.execute(
            "INSERT INTO readiness (id, name, kind, supported, complete, why, pos)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "supported=excluded.supported, complete=excluded.complete, "
            "pos=excluded.pos",
            (attr.lower(), label, kind,
             1 if getattr(mon, "available", False) else 0,
             # python-obd reports `complete` as the monitor having FINISHED.
             1 if getattr(mon, "complete", False) else 0, "", pos))
        pos += 1
        n += 1
    db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
               ("mil", json.dumps(bool(getattr(status, "MIL", False)))))
    db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
               ("dtc_count", json.dumps(int(getattr(status, "DTC_count", 0)))))
    return n


def read_mode06(conn, obd, db):
    """On-board test results — the ECU showing its working."""
    pos, n = 0, 0
    for name in MODE06_WANTED:
        cmd = getattr(obd.commands, name, None)
        if cmd is None:
            continue
        # Mode 06 is not in `supported_commands` — that list comes from the
        # Mode 01 support bitmaps and says nothing about the other modes — so
        # every query here has to be forced or python-obd refuses it.
        res = conn.query(cmd, force=True)
        tests = value_of(res)
        if not tests:
            continue
        for test in getattr(tests, "tests", []) or []:
            if getattr(test, "is_null", None) and test.is_null():
                continue
            def mag(x):
                if x is None:
                    return None
                return float(x.magnitude if hasattr(x, "magnitude") else x)
            value, lo, hi = mag(test.value), mag(test.min), mag(test.max)
            if value is None:
                continue
            unit = ""
            if test.value is not None and hasattr(test.value, "units"):
                unit = f"{test.value.units:~}"
            mid = f"{name}:{getattr(test, 'tid', pos)}"
            label = getattr(test, "name", "") or ""
            if label in ("", "Unknown"):
                # The emulator, and plenty of real ECUs, return a TID the
                # standard does not name. The component plus the TID is more
                # use than the word "Unknown" repeated eleven times.
                label = f"{pretty(name)} test {getattr(test, 'tid', pos)}"
            db.execute(
                "INSERT INTO mode06 (mid, name, component, value, lo, hi, unit, "
                "note, pos) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(mid) DO UPDATE SET value=excluded.value, "
                "lo=excluded.lo, hi=excluded.hi, pos=excluded.pos",
                (mid, label, pretty(name), value, lo, hi,
                 unit, "", pos))
            # And a dated copy. The current value answers "is it passing";
            # the history answers "for how much longer", which is the more
            # useful question and the one no consumer tool asks.
            db.execute(
                "INSERT OR REPLACE INTO mode06_history VALUES (?,?,?,?,?)",
                (mid, round(time.time() / 3600) * 3600.0, value, lo, hi))
            pos += 1
            n += 1
    return n


def pretty(name):
    return (name.replace("MONITOR_", "").replace("_", " ").title()
            .replace("B1s1", "B1S1").replace("B1s2", "B1S2")
            .replace("B2s1", "B2S1").replace("B2s2", "B2S2"))


def read_identity(conn, obd, db, supported):
    """VIN and what the powertrain module can actually answer.

    The `modules` table is what the scan report walks. A generic adapter
    reaches exactly one module, so that is exactly what goes in it — the
    report is then telling the truth about a real car rather than implying it
    read an airbag unit it cannot address.
    """
    out = {}
    for name, key in (("VIN", "vin"), ("CALIBRATION_ID", "calibration"),
                      ("FUEL_TYPE", "fuel")):
        cmd = getattr(obd.commands, name, None)
        if cmd is None:
            continue
        v = value_of(conn.query(cmd, force=True))
        if not v:
            continue
        # Mode 09 answers in bytes, not text.
        if isinstance(v, (bytes, bytearray)):
            v = bytes(v).decode("ascii", "replace")
        text = str(v).strip().strip("\x00")
        # A STORED VIN IS NOT OVERWRITTEN BY A BROKEN READ. This runs every
        # survey, and a multi-frame read on a flaky link can come back short
        # or scrambled. A VIN is seventeen characters on every car this tool
        # can talk to, so a different answer that is not seventeen long is
        # noise, not a new identity -- and writing it would put noise in the
        # vehicle table over the real car. (Found on the bench emulator, which
        # cycles three different answers to the same question.) A different
        # answer that IS well-formed is accepted: prepare() already switched
        # records for it.
        if key == "vin":
            row = db.execute("SELECT v FROM vehicle WHERE k = 'vin'").fetchone()
            stored = json.loads(row[0]) if row else ""
            if stored and text != stored and len(text) != 17:
                continue
        if text:
            out[key] = text
    # What the VIN itself will tell us. The model is not in there and no
    # amount of wanting puts it there, so it is left for the owner to name.
    if out.get("vin"):
        out.update(decode_vin(out["vin"]))
    for k, v in out.items():
        db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
                   (k, json.dumps(v)))
    db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
               ("protocol", json.dumps(conn.protocol_name())))
    db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
               ("simulated", json.dumps(False)))

    codes = [r[0] for r in db.execute(
        "SELECT code FROM faults WHERE status IN ('stored','pending','permanent')")]
    db.execute(
        "INSERT INTO modules (id, name, addr, system, generic, part, sw, codes, pos)"
        " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "codes=excluded.codes, sw=excluded.sw",
        ("PGM-FI", "Engine (powertrain)", "0x7E0", "Powertrain", 1,
         out.get("calibration", ""), conn.protocol_name(),
         json.dumps(codes), 0))
    return out


# ---- reading the VIN --------------------------------------------------------
#
# OBD-II will not tell you what car it is in. It reports a VIN and nothing
# else — no make, no model, no year — so a scan tool that shows "2015 Honda
# CR-Z" either asked a paid database or worked it out. The first three
# characters are the world manufacturer identifier, and the tenth is the model
# year on a thirty-year cycle. Both are in the standard and free.
#
# The model is genuinely not derivable without a licensed database, so this
# does not pretend: it gives you the year and the manufacturer, and leaves the
# model for you to fill in.

WMI = {
    "1FA": "Ford", "1FB": "Ford", "1FC": "Ford", "1FD": "Ford", "1FM": "Ford",
    "1FT": "Ford", "2FA": "Ford", "3FA": "Ford", "WF0": "Ford",
    "1G1": "Chevrolet", "1G6": "Cadillac", "1GC": "Chevrolet",
    "1GK": "GMC", "2G1": "Chevrolet", "3GC": "Chevrolet", "KL1": "Chevrolet",
    "1C3": "Chrysler", "1C4": "Chrysler", "1C6": "Ram", "2C3": "Chrysler",
    "3C4": "Chrysler", "ZFA": "Fiat",
    "1HG": "Honda", "2HG": "Honda", "3HG": "Honda", "JHM": "Honda",
    "JHL": "Honda", "5FN": "Honda", "5J6": "Honda", "SHH": "Honda",
    "19U": "Acura", "19X": "Honda", "2HK": "Honda",
    "JT2": "Toyota", "JTD": "Toyota", "JTE": "Toyota", "JTM": "Toyota",
    "4T1": "Toyota", "5TD": "Toyota", "2T1": "Toyota", "NMT": "Toyota",
    "SB1": "Toyota", "JTH": "Lexus", "JTJ": "Lexus",
    "JN1": "Nissan", "JN8": "Nissan", "1N4": "Nissan", "3N1": "Nissan",
    "5N1": "Nissan", "JNK": "Infiniti", "VSK": "Nissan",
    "JM1": "Mazda", "JM3": "Mazda", "4F2": "Mazda", "3MZ": "Mazda",
    "JF1": "Subaru", "JF2": "Subaru", "4S3": "Subaru", "4S4": "Subaru",
    "JA3": "Mitsubishi", "JA4": "Mitsubishi", "4A3": "Mitsubishi",
    "KMH": "Hyundai", "KM8": "Hyundai", "5NP": "Hyundai", "TMA": "Hyundai",
    "KNA": "Kia", "KND": "Kia", "5XY": "Kia", "U5Y": "Kia",
    "WVW": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen",
    "3VW": "Volkswagen", "1VW": "Volkswagen", "9BW": "Volkswagen",
    "TRU": "Audi", "WAU": "Audi", "WA1": "Audi",
    "WBA": "BMW", "WBS": "BMW", "WBY": "BMW", "4US": "BMW", "5UX": "BMW",
    "WDB": "Mercedes-Benz", "WDC": "Mercedes-Benz", "WDD": "Mercedes-Benz",
    "4JG": "Mercedes-Benz", "W1K": "Mercedes-Benz", "W1N": "Mercedes-Benz",
    "WP0": "Porsche", "WP1": "Porsche",
    "YV1": "Volvo", "YV4": "Volvo", "LVS": "Volvo",
    "SAL": "Land Rover", "SAJ": "Jaguar", "SCC": "Lotus", "SCF": "Aston Martin",
    "VF1": "Renault", "VF3": "Peugeot", "VF7": "Citroën", "VF6": "Renault",
    "ZAR": "Alfa Romeo", "ZAM": "Maserati", "ZFF": "Ferrari",
    "MAT": "Tata", "MA3": "Suzuki", "JS2": "Suzuki", "JS3": "Suzuki",
    "5YJ": "Tesla", "7SA": "Tesla", "LRW": "Tesla",
    "KPT": "SsangYong", "LSG": "Chevrolet", "LFV": "Volkswagen",
}

# Position ten. The letters skip I, O, Q, U and Z, and the digits run 1-9;
# thirty codes, so the cycle repeats every thirty years.
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def decode_vin(vin, now=None):
    """Year and manufacturer from a VIN, or as much of them as it will give.

    Deliberately conservative: a VIN that fails its own check digit is
    reported as unverified rather than silently trusted, and the model is
    never guessed."""
    out = {}
    v = (vin or "").strip().upper()
    if len(v) < 11:
        return out
    out["wmi"] = v[:3]
    maker = WMI.get(v[:3]) or WMI.get(v[:2] + "?")
    if maker:
        out["make"] = maker

    code = v[9]
    if code in YEAR_CODES:
        idx = YEAR_CODES.index(code)
        # The cycle is thirty years long, so a bare code is ambiguous. Resolve
        # it to the most recent year that is not in the future.
        year = 1980 + idx
        current = (now or time.gmtime().tm_year)
        while year + 30 <= current + 1:
            year += 30
        out["year"] = year

    # North American VINs carry a check digit at position nine. Where it is
    # present and wrong, say so rather than quietly building a car record on a
    # misread.
    if len(v) == 17 and v[0] in "1234578":
        out["vin_valid"] = vin_check_digit(v)
    return out


VIN_TRANSLIT = {**{c: i for i, c in enumerate("0123456789")},
                **{c: v for c, v in zip("ABCDEFGH", range(1, 9))},
                **{c: v for c, v in zip("JKLMN", range(1, 6))},
                "P": 7, "R": 9,
                **{c: v for c, v in zip("STUVWXYZ", range(2, 10))}}
VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(v):
    try:
        total = sum(VIN_TRANSLIT[c] * w for c, w in zip(v, VIN_WEIGHTS))
    except KeyError:
        return False
    want = total % 11
    return v[8] == ("X" if want == 10 else str(want))


# ---- one pass ---------------------------------------------------------------

def read_vin(conn, obd):
    """The VIN, before anything is written. This is what decides which car's
    record we are about to open."""
    cmd = getattr(obd.commands, "VIN", None)
    if cmd is None:
        return None
    try:
        v = value_of(conn.query(cmd, force=True))
    except Exception:                                     # noqa: BLE001
        return None
    if not v:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = bytes(v).decode("ascii", "replace")
    return str(v).strip().strip("\x00") or None


def prepare(conn=None, obd=None):
    """Point the tool at the car that is actually plugged in.

    Call this BEFORE anything opens the database — the daemon opens its sample
    connection at startup and keeps it for the life of the process. Switching
    the file under a live SQLite handle does not fail loudly: SQLite follows
    the inode and then refuses the next write with "attempt to write a
    readonly database", which is a mystifying way to be told you moved a file.

    Returns (key, is_new) or None when there is nothing to go on — an adapter
    that will not give up a VIN keeps whatever record was current, which is
    the best guess available and is at least stable.
    """
    if conn is None:
        return None
    if obd is None:
        import obd as obd_mod
        obd = obd_mod
    vin = read_vin(conn, obd)
    if not vin:
        return None
    key, is_new = garage.switch_to(vin)
    records.refresh_db()
    if is_new:
        print(f"  a car we have not seen before — VIN {vin}, "
              f"starting its own record", flush=True)
    return key, is_new


def survey(conn, obd=None, verbose=False):
    """One full slow pass. Returns a small summary; never raises at the caller."""
    if obd is None:
        import obd as obd_mod
        obd = obd_mod
    now = time.time()
    db = open_db()
    out = {"codes": 0, "monitors": 0, "tests": 0, "identity": {}}
    try:
        supported = {c.name for c in conn.supported_commands}
        out["identity"] = read_identity(conn, obd, db, supported)
        out["codes"] = read_codes(conn, obd, db, now)
        read_freeze(conn, obd, db)
        out["monitors"] = read_readiness(conn, obd, db)
        out["tests"] = read_mode06(conn, obd, db)
        db.execute("INSERT OR REPLACE INTO vehicle VALUES (?,?)",
                   ("surveyed_at", json.dumps(int(now))))
        db.commit()
    finally:
        db.close()
    if verbose:
        print(f"  {out['codes']} code(s), {out['monitors']} monitor(s), "
              f"{out['tests']} on-board test result(s)"
              + (f", VIN {out['identity'].get('vin')}"
                 if out["identity"].get("vin") else ""))
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import connect
    conn, port, kind = connect.connect()
    import obd
    if conn.status() != obd.OBDStatus.CAR_CONNECTED:
        print(f"omacar survey: not connected ({conn.status()})", file=sys.stderr)
        return 1
    print(f"  surveying {port} ({kind}) over {conn.protocol_name()}")
    prepare(conn, obd)
    survey(conn, obd, verbose=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
