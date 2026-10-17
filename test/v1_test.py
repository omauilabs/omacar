#!/usr/bin/env python3
"""The Valentine One's language (lib/esp.py) and the radar file (lib/v1.py).

Byte layouts are KESPL's (MIT, Develpr Of Things) and framing was cross-checked
against bluev's v1state.py; see lib/esp.py. NO RADIO here: every packet below is
built by hand from the documented layout, and the one checksum is added up by
hand in a comment so the test does not just agree with the code.
"""

import json
import os
import shutil
import sys
import tempfile
import time

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


def raises(fn, *a):
    try:
        fn(*a)
    except (ValueError, TypeError):
        return True
    return False


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-v1-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import esp
    import v1
    try:
        print("\n  framing and checksums")
        # Start alert data to a checksum V1 (0xDA), from the V1connection (0xE6):
        # AA+DA+E6+41+01 = 0x2AC, so the checksum byte is AC.
        check("start alert data, checksum V1: AA DA E6 41 01 AC AB",
              esp.build_request(esp.V1_CHECKSUM, esp.REQ_START_ALERT_DATA).hex(" ") == "aa da e6 41 01 ac ab")
        check("and to a V1 without checksums there is none: AA D9 E6 41 00 AB",
              esp.build_request(esp.V1_NO_CHECKSUM, esp.REQ_START_ALERT_DATA).hex(" ") == "aa d9 e6 41 00 ab")
        # Mute on: AA+DA+E6+34+01 = 0x29F -> 9F
        check("mute on: AA DA E6 34 01 9F AB",
              esp.build_request(esp.V1_CHECKSUM, esp.REQ_MUTE_ON).hex(" ") == "aa da e6 34 01 9f ab")
        for pid, why in [(0x13, "write user bytes"), (0x15, "write a sweep"), (0x39, "write volume"),
                         (0x36, "change mode"), (0x14, "factory default")]:
            check(f"refuses to send {why} (0x{pid:02X})", raises(esp.build_request, esp.V1_CHECKSUM, pid))
        check("refuses a payload on a request that has none",
              raises(esp.build_request, esp.V1_CHECKSUM, esp.REQ_MUTE_ON, b"\x01"))

        good = esp.build_packet(esp.BROADCAST, esp.V1_CHECKSUM, esp.INF_DISPLAY_DATA, bytes(8))
        f = esp.Framer()
        out = f.feed(good[:5]) + f.feed(good[5:9]) + f.feed(good[9:])
        check("a packet split across three notifications arrives whole", len(out) == 1 and out[0].pid == 0x31)
        check("with the checksum byte taken off the payload", len(out[0].payload) == 8 and out[0].checked)
        bent = bytearray(good)
        bent[8] ^= 0x04
        f = esp.Framer()
        check("a bad checksum is dropped and counted", f.feed(bytes(bent)) == [] and f.bad >= 1)
        f = esp.Framer()
        out = f.feed(b"\x00\x13" + good + b"\xAA\x01" + good)
        check("junk between packets is skipped, both packets survive", len(out) == 2)
        nochk = esp.build_packet(esp.BROADCAST, esp.V1_NO_CHECKSUM, esp.INF_DISPLAY_DATA, bytes(8), with_checksum=False)
        out = esp.Framer().feed(nochk)
        check("a V1 without checksums needs none", len(out) == 1 and not out[0].checked and len(out[0].payload) == 8)

        print("\n  one alert")
        row = bytes([0x11, 0x87, 0x8C, 0xD1, 0x00, 0x22, 0x80])   # 1 of 1, 34700 MHz, front, Ka, priority
        a = esp.decode_alert(row)
        check("Ka at 34.700 GHz from the front, priority",
              (a["band"], a["freq_mhz"], a["dir"], a["priority"]) == ("Ka", 34700, "front", True))
        check("the raw levels are kept", (a["front_raw"], a["rear_raw"]) == (0xD1, 0))
        check("band bits: laser, Ka, K, X, Ku",
              [esp.band_of(b) for b in (1, 2, 4, 8, 0x10)] == ["Laser", "Ka", "K", "X", "Ku"])
        check("two band bits at once is not a band", esp.band_of(0x06) is None)
        check("arrows: front, side, rear, and two at once is none",
              [esp.arrow_of(b) for b in (0x20, 0x40, 0x80, 0x60)] == ["front", "side", "rear", None])
        las = esp.decode_alert(bytes([0x11, 0x12, 0x34, 0xFF, 0, 0x21, 0x80]))
        check("a laser alert has no frequency, whatever the field holds", las["band"] == "Laser" and las["freq_mhz"] is None)
        ph = esp.decode_alert(bytes([0x11, 0x5E, 0x6E, 0x40, 0, 0x24, 0x03]))
        check("K with a photo-radar type is Photo", ph["band"] == "Photo" and ph["photo"] == "DriveSafe 2")
        check("an empty table is its own answer", esp.decode_alert(b"\x00" * 7) == {"empty": True})
        check("a short payload is nothing", esp.decode_alert(b"\x11\x00") is None)
        check("an index past its count is nothing", esp.decode_alert(bytes([0x31, 0, 0, 0, 0, 0x22, 0])) is None)

        print("\n  the alert table")
        def r(i, n, **kw):
            return esp.decode_alert(esp.alert_payload(i, n, **kw))
        t = esp.AlertTable()
        check("a table is not out until the last row arrives",
              t.feed(r(2, 3, band="K", freq_mhz=24150, front=60), 0) is None
              and t.feed(r(1, 3, band="Ka", freq_mhz=34700, front=200, priority=True), 0.1) is None)
        full = t.feed(r(3, 3, band="X", freq_mhz=10525, rear=20, arrow="rear"), 0.2)
        check("then all three, in index order", [x["band"] for x in full] == ["Ka", "K", "X"])
        check("a table of another size starts over",
              t.feed(r(1, 2), 1.0) is None and t.feed(r(1, 3), 1.1) is None
              and t.feed(r(2, 3), 1.1) is None and len(t.feed(r(3, 3), 1.2)) == 3)
        t = esp.AlertTable(hold=1.5)
        t.feed(r(1, 2), 0.0)
        check("a half table that sat too long is not completed by a late row", t.feed(r(2, 2), 5.0) is None)
        check("the empty table clears", esp.AlertTable().feed({"empty": True}, 0) == [])
        t = esp.AlertTable()
        t.feed(r(1, 2, junk=True), 0)
        check("a row the V1 calls junk is dropped from what is shown",
              [x["band"] for x in t.feed(r(2, 2, band="K", freq_mhz=24150), 0)] == ["K"])
        t = esp.AlertTable()
        t.feed(r(1, 2), 0)
        check("the same row twice is not a complete table", t.feed(r(1, 2), 0.05) is None)

        print("\n  the V1's face")
        face = esp.decode_display(bytes([0x6F, 0x6F, 0b00001111, 0x22, 0x22, 0b00001101, 0b00001001 << 0, 0x00]))
        check("the bogey counter reads 9 off its seven segments", face["counter"] == "9")
        check("four bars lit left to right count as 4", face["bars"] == 4)
        check("Ka and the front arrow", face["bands"] == ["Ka"] and face["arrows"] == ["front"])
        check("aux0 bit 0 is mute (soft), bit 3 is display on, bit 2 searching",
              face["soft"] and face["display_on"] and face["searching"])
        check("mode from aux1 bits 2-3: 2 is Logic", face["mode"] == "Logic")
        check("a blinking arrow counts as lit, from image 2 alone",
              esp.decode_display(bytes([0, 0, 0, 0x00, 0x40, 0, 0, 0]))["arrows"] == ["side"])
        check("time slicing is the opposite of the holdoff bit",
              esp.decode_display(bytes([0, 0, 0, 0, 0, 0x02, 0, 0]))["time_slicing"] is False
              and esp.decode_display(bytes(8))["time_slicing"] is True)
        check("mode falls back to the bogey counter's letter for an older V1",
              esp.decode_display(bytes([0x38, 0, 0, 0, 0, 0, 0, 0]))["mode"] == "Advanced Logic")
        check("a short display payload is nothing", esp.decode_display(b"\x00" * 4) is None)
        check("version string", esp.decode_version(b"V4.1037") == "V4.1037" and esp.is_gen2("V4.1037") is True
              and esp.is_gen2("V3.8920") is False)
        check("round trip: display_payload decodes to what it was given",
              esp.decode_display(esp.display_payload("3", 5, {"Ka", "K"}, {"rear"}, soft=True, mode=3))
              == {**esp.decode_display(esp.display_payload("3", 5, {"Ka", "K"}, {"rear"}, soft=True, mode=3)),
                  "counter": "3", "bars": 5, "bands": ["Ka", "K"], "arrows": ["rear"], "soft": True, "mode": "Advanced Logic"})
        check("bars_from_raw is monotone and spans 0..8",
              [esp.bars_from_raw(x) for x in (0, 1, 255)] == [0, 1, 8]
              and all(esp.bars_from_raw(x) <= esp.bars_from_raw(x + 1) for x in range(255)))

        print("\n  a session: bytes in, state out")
        s = v1.Session("ble")
        t0 = 1000.0

        def feed(alerts, soft=False, now=t0, digit="2", bars=6):
            n = len(alerts)
            data = b""
            for k, al in enumerate(alerts, 1):
                data += esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA,
                                         esp.alert_payload(k, n, **al))
            if not alerts:
                data += esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA, b"\x00")
            data += esp.build_packet(esp.BROADCAST, esp.V1_CHECKSUM, esp.INF_DISPLAY_DATA,
                                     esp.display_payload(digit, bars if alerts else 0, {a["band"] for a in alerts},
                                                         {alerts[0]["arrow"]} if alerts else (), soft))
            s.feed(data, now)
        feed([], now=t0)
        check("idle: connected to a checksum V1 (id 10), nothing alerting",
              s.v1_id == 10 and s.st["bogeys"] == 0 and s.state()["today"]["count"] == 0)
        feed([dict(band="K", freq_mhz=24150, front=100, arrow="front"),
              dict(band="Ka", freq_mhz=34700, front=200, arrow="front", priority=True)], now=t0 + 1, bars=6)
        st = s.state()
        check("two bogeys, the priority one first", st["bogeys"] == 2 and st["alerts"][0]["band"] == "Ka")
        check("the priority alert's strength is the V1's own bars (6), marked exact",
              st["alerts"][0]["strength"] == 6 and st["alerts"][0]["exact"] is True)
        check("the other is the approximation and says so", st["alerts"][1]["exact"] is False)
        check("two encounters today, newest first", st["today"]["count"] == 2 and st["log"][0]["band"] in ("K", "Ka"))
        feed([dict(band="K", freq_mhz=24158, front=100, arrow="front"),
              dict(band="Ka", freq_mhz=34703, front=240, arrow="front", priority=True)], now=t0 + 2, bars=8)
        check("the same two a second later are the same two encounters", s.state()["today"]["count"] == 2)
        peak = [e for e in s.state()["log"] if e["band"] == "Ka"][0]
        check("and the Ka one's peak is 8", peak["peak"] == 8)
        feed([], now=t0 + 3)
        check("an empty table clears the alerts at once", s.state()["alerts"] == [])
        feed([dict(band="Laser", freq_mhz=0, front=255, arrow="front", priority=True)], now=t0 + 20, bars=8)
        check("a laser burst 19 s later is a third encounter", s.state()["today"]["count"] == 3)
        # An old table redrawn by a display packet long after is not a new encounter.
        n = s.state()["today"]["count"]
        s.feed(esp.build_packet(esp.BROADCAST, esp.V1_CHECKSUM, esp.INF_DISPLAY_DATA, esp.display_payload("1", 8, {"Laser"}, {"front"})), t0 + 40)
        check("a stale table redrawn by the display starts no new encounter", s.state()["today"]["count"] == n)
        check("the log records no position of any kind",
              all(set(e) <= {"at", "band", "freq_mhz", "dir", "peak", "secs"} for e in s.state()["log"]))
        echo = esp.build_packet(esp.V1_CHECKSUM, esp.V1CONNECTION, esp.REQ_START_ALERT_DATA)
        before = json.dumps(s.state(), sort_keys=True)
        s.feed(echo, t0 + 21)
        check("an echo of our own request changes nothing", json.dumps(s.state(), sort_keys=True) == before)
        s2 = v1.Session("ble")
        s2.feed(esp.build_packet(0x03, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA,
                                 esp.alert_payload(1, 1, "Ka", 34700, 200, 0, "front")), t0)
        check("an alert table answering another device is not ours", s2.st["alerts"] == [])
        s.feed(esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_VERSION, b"V4.1037"), t0 + 22)
        check("the version and Gen2 arrive", s.st["version"] == "V4.1037" and s.st["gen2"] is True)
        # A V1 that never sends the empty table, but whose face goes dark.
        s3 = v1.Session("ble")
        s3.feed(esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA,
                                 esp.alert_payload(1, 1, "K", 24150, 100, 0, "front")), 5.0)
        s3.feed(esp.build_packet(esp.BROADCAST, esp.V1_CHECKSUM, esp.INF_DISPLAY_DATA, esp.display_payload("l", 0)), 5.1)
        s3.tick(5.2)
        still = len(s3.st["alerts"])
        s3.tick(7.0)
        check("a dark face clears alerts the table never cleared, but not instantly",
              still == 1 and s3.st["alerts"] == [])
        big = v1.Session("ble")
        for i in range(70):
            big.feed(esp.build_packet(esp.V1CONNECTION, esp.V1_CHECKSUM, esp.RESP_ALERT_DATA,
                                      esp.alert_payload(1, 1, "K", 24150, 100, 0, "front")), 100.0 + i * 10)
        check("the log keeps the last 50", len(big.state()["log"]) == 50 and big.state()["today"]["count"] == 70)

        print("\n  the file the app reads")
        st = v1.blank("ble")
        st["connected"] = True
        st["alerts"] = [{"band": "Ka", "strength": 5}]
        st["bogeys"], st["bars"] = 1, 5
        st["log"] = [{"at": 1, "band": "Ka", "freq_mhz": 34700, "dir": "front", "peak": 5, "secs": 4}]
        st["last_alert_at"] = time.time()
        v1.write(st)
        pub = v1.read_public()
        check("fresh: present, connected, the alert shown", pub["present"] and pub["connected"] and pub["bogeys"] == 1)
        with open(v1.STATE) as f:
            old = json.load(f)
        old["at"] -= 600
        with open(v1.STATE, "w") as f:
            json.dump(old, f)
        pub = v1.read_public()
        check("ten minutes stale: not connected, and NO alert on screen",
              pub["connected"] is False and pub["alerts"] == [] and pub["bogeys"] == 0 and pub["stale"] is True)
        check("but the log and the last alert's time are history and stay",
              len(pub["log"]) == 1 and pub["last_alert_at"] is not None)
        check("nothing at all is {present: false}", (os.unlink(v1.STATE), v1.read_public())[1] == {"present": False})

        print("\n  what the screen may ask")
        check("mute on, mute off", v1.ask("mute", True)["value"] is True and v1.ask("mute", 0)["value"] is False)
        for bad in [("volume", 5), ("mode", 2), ("user_bytes", 1), ("mute; rm", True), ("", None)]:
            check(f"refuses {bad[0]!r}", raises(v1.ask, *bad))
        a = v1.take_ask()
        check("the ask is read once", a and a["name"] == "mute" and v1.take_ask() is None)

        print("\n  the simulator")
        v1.sim(seconds=1.2, seed=1, scenario="ka", step=0.05, lead=0.05)
        s_ = v1.read_public()
        check(f"says it is simulated, in the source and the name ({s_['source']}, {s_['name']})",
              s_["source"] == "sim" and "simulated" in s_["name"])
        check("and it saw a Ka encounter come in through the codec",
              s_["today"]["count"] >= 1 and s_["log"][-1]["band"] in ("Ka", "X"))
        check("the sim's file never claims to be the Bluetooth link", s_["source"] != "ble")
        prior = v1.Session("ble", s_)
        check("a real link does not inherit the simulator's log",
              v1.Session("ble", None).state()["log"] == [] and prior.state()["source"] == "ble")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
