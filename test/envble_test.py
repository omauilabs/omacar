#!/usr/bin/env python3
"""Ready-made cabin monitors over Bluetooth (lib/envble.py). No radio here.

The Qingping frame is qingping-ble's own captured Air Monitor Lite
advertisement (tests/test_parser.py, AIR_MONITOR), with the values that
library expects for it. The Aranet4 frame is built to Aranet4-Python's
Aranet4Advertisement layout.
"""

import os
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    print(f"    {'ok' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails += 1


QP = "0000fdcd-0000-1000-8000-00805f9b34fb"
AIR_MONITOR = {QP: b"\x88\x0e\xd6\x88\x8f\xe6HT\x01\x04\x03\x01\x16\x02\x12\x04\x02\x00\x02\x00\x13\x02%\x02"}
CLOCK_LITE = {QP: b"\x08\x1e\xf3&T4-X\x01\x046\x01\xcc\x01\x02\x01d"}


def aranet_frame(co2=812, t_c=22.5, hpa=1012.4, rh=41, integrations=True):
    status = (1 << 5) if integrations else 0
    basic = bytes([status, 3, 2, 1])                     # flags, patch, minor, major
    data = struct.pack("<HHHBBBHH", co2, int(round(t_c / 0.05)), int(round(hpa * 10)), rh, 87, 1, 60, 12)
    return basic + b"\x00\x00\x00\x00" + data            # 4 + 4 + 13 = 21 bytes, before the type byte


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-envble-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import envble
    import envnode
    try:
        print("\n  Qingping Air Monitor Lite")
        q = envble.qingping(AIR_MONITOR)
        check(f"qingping-ble's own capture decodes to its expected values ({q})",
              q == {"t_c": 25.9, "rh": 53.4, "pm25": 2, "pm10": 2, "co2": 549})
        check("another Qingping (a clock) is not a cabin monitor", envble.qingping(CLOCK_LITE) is None)
        check("no service data, nothing", envble.qingping({}) is None)
        check("a torn frame stops cleanly", envble.qingping({QP: AIR_MONITOR[QP][:15]}) == {"t_c": 25.9, "rh": 53.4})

        print("\n  Aranet4")
        a = envble.aranet4({0x0702: aranet_frame()}, "Aranet4 1A2B3")
        check(f"CO2, temperature, pressure, humidity ({a})",
              a == {"co2": 812, "t_c": 22.5, "hpa": 1012.4, "rh": 41})
        check("with Smart Home integrations off, nothing",
              envble.aranet4({0x0702: aranet_frame(integrations=False)}, "Aranet4 1A2B3") is None)
        check("a reading taken during calibration is dropped, the rest kept",
              "co2" not in envble.aranet4({0x0702: aranet_frame(co2=0x8000 | 400)}, "Aranet4 1A2B3"))

        print("\n  only the pinned monitor, through the same checks as a USB node")
        check("no pin, nothing believed", envble.pinned({}) is None)
        check("a pin is read, case-folded", envble.pinned({"cabin_ble": {"address": "aa:bb:cc:dd:ee:ff"}}) == "AA:BB:CC:DD:EE:FF")
        rec = envnode.Record("sensor")
        kept = envnode.ble_ingest(rec, "Aranet4", {"co2": 812, "t_c": 22.5, "hpa": 1012.4, "rh": 41}, now=1000.0)
        check(f"the cabin keeps what it tracks; an Aranet4's pressure is dropped ({kept})",
              kept == {"co2": 812, "t_c": 22.5, "rh": 41})
        check("an impossible value is refused like a USB one",
              envnode.ble_ingest(rec, "Qingping", {"co2": 99999, "rh": 140}, now=1001.0) == {})
        check("and the reading lands as the cabin node, labelled with the device",
              rec.nodes["cabin"]["port"] == "Aranet4" and rec.nodes["cabin"]["vals"]["co2"][0] == 812)
        check("`use` refuses something that is not an address", envnode.use("next door") == 2)
        check("and pins one that is", envnode.use("aa:bb:cc:dd:ee:ff") == 0
              and envble.pinned(envnode._config()) == "AA:BB:CC:DD:EE:FF")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
