#!/usr/bin/env python3
"""The CFX3's language (lib/cfx.py) and the fridge file (lib/fridge.py).

Byte values are from andrewbackway/esphome-dometic_cfx_ble's protocol.md and
scripts/test.py (little-endian, deci-degrees, deci-volts). No radio here.

THE ONE THAT MATTERS MOST: the fridge publishes the Wi-Fi networks and
passwords it was given, and none of them may come out of decode().
"""

import json
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
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-fridge-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import cfx
    import fridge
    try:
        print("\n  decoding")
        pub = lambda topic, body: bytes([cfx.PUB, *topic]) + body  # noqa: E731
        check("a temperature, in deci-degrees",
              cfx.decode(pub((0, 1, 1, 1), struct.pack("<h", 34))) == (0, "z0_temp", 3.4))
        check("a negative one",
              cfx.decode(pub((0, 1, 1, 1), struct.pack("<h", -185)))[2] == -18.5)
        check("the fridge's 'no value' is None, not -3276.8",
              cfx.decode(pub((0, 2, 1, 1), struct.pack("<h", -32768)))[2] is None)
        check("battery volts, in deci-volts",
              cfx.decode(pub((0, 1, 3, 1), struct.pack("<H", 132)))[2] == 13.2)
        check("a range is [min, max]",
              cfx.decode(pub((0, 128, 1, 1), struct.pack("<hh", -220, 100)))[2] == [-22.0, 10.0])
        check("door open is a boolean", cfx.decode(pub((0, 8, 1, 1), b"\x01"))[2] is True)

        print("\n  what is never decoded")
        secret = b"hunter2-hunter2"
        for topic, what in [((0, 0, 7, 1), "a station SSID"), ((0, 1, 7, 1), "a station password"),
                            ((4, 1, 7, 1), "the last password slot"), ((0, 2, 7, 1), "the CFX direct password"),
                            ((0, 193, 0, 0), "the serial number"), ((9, 9, 9, 9), "an unknown topic")]:
            check(f"{what} decodes to nothing", cfx.decode(pub(topic, secret))[1:] == (None, None))
        check("and no credential topic is on the allow-list at all",
              not any(t[2] == 7 for t, _ in cfx.TOPICS.values()))

        print("\n  encoding what OmaCar sets")
        check("set point 3 C", cfx.encode_set("z0_set", 3).hex() == "00000201011e00")
        check("power off", cfx.encode_set("power", False).hex() == "000000030100")
        for bad, why in [(("z0_set", 15), "above +10 C"), (("z0_set", -30), "below -22 C"),
                         (("battery_v", 12), "not writable"), (("wifi", 1), "unknown")]:
            try:
                cfx.encode_set(*bad)
                check(f"refuses {why}", False)
            except ValueError:
                check(f"refuses {why}", True)
        check("subscribe for a single zone", cfx.subscribe(1).hex() == "0101000081")
        check("product from the advertisement, byte 2 of 0xFFFF",
              cfx.product_of({0xFFFF: bytes([0, 0, 3, 9])}) == 3)
        check("and single zone when it cannot tell", cfx.product_of({}) == 1)

        print("\n  the file the app reads")
        st = fridge.blank("ble")
        flags = {}
        fridge.apply(st, "z0_temp", 4.2, flags)
        fridge.apply(st, "alert_door", True, flags)
        fridge.apply(st, "source", 1, flags)
        check("readings land where the card looks",
              st["zones"][0]["temp"] == 4.2 and st["alerts"] == ["Door open"] and st["supply"] == "DC")
        fridge.apply(st, "alert_door", False, flags)
        check("and an alert clears", st["alerts"] == [])
        fridge.write(st)
        pub_ = fridge.read_public()
        check("a fresh write is present and connected as written",
              pub_["present"] and pub_["age"] < 5)
        with open(fridge.STATE) as f:
            old = json.load(f)
        old["at"] -= 600
        old["connected"] = True
        with open(fridge.STATE, "w") as f:
            json.dump(old, f)
        check("ten minutes stale is not connected, but keeps its number",
              fridge.read_public()["connected"] is False
              and fridge.read_public()["zones"][0]["temp"] == 4.2)

        print("\n  what the screen may ask")
        check("a set point rounds to the panel's half degree",
              fridge.ask("z0_set", 3.3)["value"] == 3.5)
        for bad in [("z0_set", 40), ("z0_set", float("nan")), ("battery_v", 1), ("power; rm", 1)]:
            try:
                fridge.ask(*bad)
                check(f"refuses {bad}", False)
            except (ValueError, TypeError):
                check(f"refuses {bad}", True)

        print("\n  the simulator")
        fridge.sim(seconds=2.5, seed=1)
        s = fridge.read_public()
        check(f"says it is simulated, in the source and the name ({s['source']}, {s['name']})",
              s["source"] == "sim" and "simulated" in s["name"])
        check(f"starts with an hour of trail ({len(s['trail'])} points)", len(s["trail"]) >= 200)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
