#!/usr/bin/env python3
"""Hands-free calls (lib/calls.py, share/js/calls.js, share/js/callsui.js).

No phone, no oFono, no bus: the call state machine is fed D-Bus-shaped events
and a fake clock, and the oFono/busctl/PipeWire reads are parsed from the
shapes those tools print. WHAT THIS DOES NOT PROVE is that a real iPhone's
oFono events look like these: doc/calls.md says so.
"""

import json
import os
import re
import sys
import tempfile
import threading
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


def ev(member, path=None, **kw):
    return dict(member=member, path=path, **kw)


def prop(path, name, value, iface="org.ofono.VoiceCall"):
    return dict(member="PropertyChanged", interface=iface, path=path, name=name, value=value)


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-calls-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import calls as c
    P = "/hfp/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    V1, V2 = P + "/voicecall01", P + "/voicecall02"

    def fresh():
        m = c.blank_machine()
        c.apply_event(m, ev("ModemAdded", P, props={"Name": "James's iPhone", "Online": True}), 0)
        return m

    print("\n  an incoming call, answered, ended")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "incoming", "LineIdentification": "+14155550142", "Name": ""}), 100)
    s = c.summarize(m, 101)
    check("incoming: phase and the number as the phone sent it",
          s["phase"] == "incoming" and s["call"]["number"] == "+14155550142")
    check("no name: the number is formatted, never invented", s["call"]["who"] == "+1 (415) 555-0142")
    c.apply_event(m, prop(V1, "State", "active"), 110)
    s = c.summarize(m, 165)
    check("answered: active, and the duration counts from the answer", s["phase"] == "active" and s["call"]["secs"] == 55)
    c.apply_event(m, ev("CallRemoved", V1), 200)
    s = c.summarize(m, 201)
    check("ended: lingers as 'ended' with the talk time", s["phase"] == "ended" and s["ended"]["secs"] == 90)
    check("and goes quiet after the linger", c.summarize(m, 200 + c.ENDED_LINGER + 1)["phase"] == "idle")
    check("it is in the recent list as answered, 90 s",
          m["recent"][0]["outcome"] == "answered" and m["recent"][0]["secs"] == 90)

    print("\n  names, missing numbers, outcomes")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "incoming", "LineIdentification": "", "Name": ""}), 1)
    check("no number and no name is 'Unknown caller'", c.summarize(m, 2)["call"]["who"] == "Unknown caller")
    c.apply_event(m, prop(V1, "Name", "Dana"), 2)
    check("a name that arrives later replaces it", c.summarize(m, 3)["call"]["who"] == "Dana")
    c.apply_event(m, ev("CallRemoved", V1), 9)
    check("unanswered and removed is a missed call, and no 'ended' bar",
          m["recent"][0]["outcome"] == "missed" and c.summarize(m, 10)["phase"] == "idle")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "incoming", "LineIdentification": "5551234"}), 1)
    c.plan_ask(m, {"action": "reject"})
    c.apply_event(m, ev("CallRemoved", V1), 3)
    check("rejected from the screen is 'declined', not 'missed'", m["recent"][0]["outcome"] == "declined")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "dialing", "LineIdentification": "5551234"}), 1)
    check("dialing is an outgoing call", c.summarize(m, 2)["phase"] == "dialing" and m["calls"][V1]["dir"] == "out")
    c.apply_event(m, prop(V1, "State", "alerting"), 2)
    c.apply_event(m, prop(V1, "State", "active"), 4)
    c.apply_event(m, prop(V1, "State", "disconnected"), 14)
    check("'disconnected' ends the call as outgoing, 10 s",
          m["recent"][0]["outcome"] == "outgoing" and m["recent"][0]["secs"] == 10 and not m["calls"])

    print("\n  held, waiting, priority")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "active", "LineIdentification": "111"}), 1)
    c.apply_event(m, ev("CallAdded", V2, props={"State": "waiting", "LineIdentification": "222"}), 5)
    s = c.summarize(m, 6)
    check("a call-waiting arrives beside the active call", s["phase"] == "active" and s["waiting"]["number"] == "222")
    check("answering it holds the first", c.plan_ask(m, {"action": "answer"})[0][:4] == ("call", P, c.IF_MGR, "HoldAndAnswer"))
    c.apply_event(m, prop(V1, "State", "held"), 7)
    c.apply_event(m, prop(V2, "State", "active"), 7)
    check("held is not the foreground while another is active", c.summarize(m, 8)["call"]["number"] == "222")
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "held", "LineIdentification": "1"}), 1)
    c.apply_event(m, ev("CallAdded", V2, props={"State": "incoming", "LineIdentification": "2"}), 2)
    check("incoming outranks held", c.summarize(m, 3)["phase"] == "incoming")

    print("\n  snapshots (what polling oFono produces) and the link")
    m = fresh()
    c.apply_event(m, ev("Snapshot", calls={V1: {"State": "incoming", "LineIdentification": "9"}}), 1)
    check("a snapshot adds a call", c.summarize(m, 2)["phase"] == "incoming")
    c.apply_event(m, ev("Snapshot", calls={V1: {"State": "active"}}), 3)
    check("and moves it to active without losing the number", m["calls"][V1]["state"] == "active" and m["calls"][V1]["number"] == "9")
    c.apply_event(m, ev("Snapshot", calls={}), 9)
    check("a snapshot without it ends it", not m["calls"] and m["recent"][0]["outcome"] == "answered")
    m = fresh()
    c.apply_event(m, ev("Snapshot", calls={V1: {"State": "active"}}), 1)
    c.apply_event(m, ev("ModemRemoved", P), 2)
    check("the phone leaving ends every call and clears the link", not m["calls"] and m["link"]["modem"] is None)
    c.apply_event(m, prop(P, "Online", False, c.IF_MODEM), 3)
    c.apply_event(m, prop("/x", "SpeakerVolume", 70, c.IF_VOL), 3)
    check("call volume is tracked", m["volume"]["speaker"] == 70)
    check("an event it does not know changes nothing", c.apply_event(m, ev("Nonsense", "/q"), 4) is m)
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "disconnected"}), 1)
    check("a call added already disconnected is ignored", not m["calls"])

    print("\n  what an ask means")
    m = fresh()
    check("nothing to answer is nothing done", c.plan_ask(m, {"action": "answer"}) == [])
    c.apply_event(m, ev("CallAdded", V1, props={"State": "incoming", "LineIdentification": "7"}), 1)
    check("answer is VoiceCall.Answer on that call", c.plan_ask(m, {"action": "answer"}) == [("call", V1, c.IF_CALL, "Answer", [])])
    check("hang up on an unanswered ring is a decline", c.plan_ask(m, {"action": "hangup"})[0][3] == "Hangup" and V1 in m["rejected"])
    check("mute and volume pass through", c.plan_ask(m, {"action": "mute", "value": 1}) == [("mute", True)]
          and c.plan_ask(m, {"action": "volume", "value": 40}) == [("volume", 40)])
    c.apply_event(m, ev("CallRemoved", V1), 2)
    rid = m["recent"][0]["id"]
    check("call back dials the NUMBER IN THE LIST by id", c.plan_ask(m, {"action": "dial", "id": rid}) == [("dial", "7")])
    check("never while moving", c.plan_ask(m, {"action": "dial", "id": rid}, moving=True)[0][0] == "refuse")
    check("an id that is not in the list dials nothing", c.plan_ask(m, {"action": "dial", "id": 999})[0][0] == "refuse")
    m2 = c.blank_machine([{"id": 1, "number": "5", "name": "", "dir": "in", "outcome": "missed", "at": 0, "secs": 0}])
    check("and not with no phone connected", c.plan_ask(m2, {"action": "dial", "id": 1})[0][0] == "refuse")
    check("answer, decline, hang up, mute, volume are NOT gated by moving",
          all(c.plan_ask(m, {"action": a, "value": 1}, moving=True) is not None and
              not any(op[0] == "refuse" for op in c.plan_ask(m, {"action": a, "value": 1}, moving=True))
              for a in ("answer", "reject", "hangup", "mute", "volume")))

    print("\n  the ask file")
    check("answer is accepted", c.ask("answer") == {"asked": "answer"} and c.take_ask()["action"] == "answer")
    for bad, why in [(("volume", 101), "volume above 100"), (("volume", -1), "volume below 0"),
                     (("dial-by-number", None), "an unknown action")]:
        try:
            c.ask(*bad)
            check(why + " is refused", False)
        except ValueError:
            check(why + " is refused", True)
    try:
        c.ask("dial")
        check("dial with no id is refused", False)
    except (TypeError, ValueError):
        check("dial with no id is refused", True)
    os.makedirs(c.STATE_DIR, exist_ok=True)
    with open(c.LIVE, "w") as f:
        json.dump({"t": time.time(), "connected": True, "values": {"SPEED": 60}}, f)
    try:
        c.ask("dial", call_id=1)
        check("dial is refused when the car's speed is above zero", False)
    except PermissionError:
        check("dial is refused when the car's speed is above zero", True)
    check("but answering is not", c.ask("answer") == {"asked": "answer"})
    c.take_ask()
    with open(c.LIVE, "w") as f:
        json.dump({"t": time.time() - 600, "connected": True, "values": {"SPEED": 60}}, f)
    check("an old speed sample does not stop a call back", c.moving() is False)
    with open(c.LIVE, "w") as f:
        json.dump({"t": time.time(), "handover": True, "values": {}}, f)
    check("a hand-off counts as moving", c.moving() is True)
    os.unlink(c.LIVE)
    c.ask("answer")
    with open(c.ASK) as f:
        a = json.load(f)
    a["at"] -= 100
    with open(c.ASK, "w") as f:
        json.dump(a, f)
    check("a request that sat for a minute is dropped, not carried out late", c.take_ask() is None)

    print("\n  the file the app reads")
    check("nothing written is not present", c.read_public() == {"present": False})
    m = fresh()
    c.apply_event(m, ev("CallAdded", V1, props={"State": "active", "LineIdentification": "3"}), 1000)
    st = c.compose(m, "ofono", 1000)
    st["at"] = 1000.0
    os.makedirs(c.STATE_DIR, exist_ok=True)
    with open(c.STATE, "w") as f:
        json.dump(st, f)
    pub = c.read_public(now=1003)
    check("a fresh file shows the call, and the age", pub["phase"] == "active" and not pub["stale"])
    check("the duration is the server's clock at read time", pub["call"]["secs"] == 3)
    pub = c.read_public(now=1000 + c.STALE + 1)
    check("a STALE file shows no call at all (a ringing from a minute ago is not ringing)",
          pub["phase"] == "idle" and pub["call"] is None and pub["stale"] and not pub["link"]["connected"])
    ring = fresh()
    c.apply_event(ring, ev("CallAdded", V1, props={"State": "incoming", "LineIdentification": "3"}), 1000)
    st = c.compose(ring, "ofono", 1000)
    st["at"] = 1000.0
    with open(c.STATE, "w") as f:
        json.dump(st, f)
    check("a stale ring is gone", c.read_public(now=1100)["call"] is None)
    check("compose says where it came from", c.compose(m, "sim", 1)["source"] == "sim")

    print("\n  what busctl and pw-dump print")
    modems = {"type": "a(oa{sv})", "data": [[
        ["/ril_0", {"Type": {"type": "s", "data": "hardware"}}],
        [P, {"Type": {"type": "s", "data": "hfp"}, "Name": {"type": "s", "data": "James's iPhone"},
             "Online": {"type": "b", "data": True}}]]]}
    pm = c.parse_modems(modems)
    check("only the hands-free modems, variants unwrapped",
          len(pm) == 1 and pm[0][0] == P and pm[0][1]["Name"] == "James's iPhone" and pm[0][1]["Online"] is True)
    calls = {"type": "a(oa{sv})", "data": [[[V1, {"State": {"type": "s", "data": "incoming"},
                                                  "LineIdentification": {"type": "s", "data": "+14155550142"}}]]]}
    check("GetCalls becomes a snapshot's calls", c.parse_calls(calls)[V1]["State"] == "incoming")
    check("an empty reply is no calls and no error", c.parse_calls({"type": "a(oa{sv})", "data": [[]]}) == {} and c.parse_calls(None) == {})
    check("the volume reply", c.parse_volume({"type": "a{sv}", "data": [{"SpeakerVolume": {"type": "y", "data": 55}}]})["SpeakerVolume"] == 55)
    check("the phone's MAC from the modem path", c.mac_of(P) == "AA_BB_CC_DD_EE_FF" and c.mac_of("/ril_0") is None)
    dump = [{"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "bluez_input.AA_BB_CC_DD_EE_FF.0", "media.class": "Audio/Source"}}},
            {"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "bluez_output.AA_BB_CC_DD_EE_FF.0", "media.class": "Audio/Sink"}}},
            {"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_output.pci", "media.class": "Audio/Sink"}}}]
    check("the phone's two call-audio nodes are found by MAC", c.audio_nodes(dump, "AA_BB_CC_DD_EE_FF") ==
          ("bluez_input.AA_BB_CC_DD_EE_FF.0", "bluez_output.AA_BB_CC_DD_EE_FF.0"))
    check("another phone's nodes are not taken", c.audio_nodes(dump, "11_22_33_44_55_66") is None)
    voice, talk = c.loopback_cmds("SRC", "SNK", None)
    check("the caller's voice goes to the DEFAULT sink (so through the AUX equalizer)", "-P" not in voice and voice[-2:] == ["-C", "SRC"])
    check("the microphone goes to the phone, from the default source", talk[-2:] == ["-P", "SNK"] and "-C" not in talk)
    check("a chosen USB microphone is the capture", c.loopback_cmds("S", "K", "usb-mic")[1][-4:] == ["-C", "usb-mic", "-P", "K"])
    check("audio runs for active, held and dialing, and not otherwise",
          [c.routing_wanted(p) for p in ("active", "held", "dialing", "incoming", "idle", "ended")] ==
          [True, True, True, False, False, False])

    print("\n  the simulator")
    out = []

    def run_sim():
        c.sim(seconds=4.0, first_s=0.0, ring_s=30, talk_s=30, rest_s=1)
    t = threading.Thread(target=run_sim)
    t.start()
    time.sleep(0.9)
    pub = c.read_public()
    check("it rings, and says Simulated", pub["phase"] == "incoming" and pub["source"] == "sim")
    check("with a caller that says it is simulated", "simulated" in pub["call"]["who"].lower())
    c.ask("answer")
    time.sleep(0.9)
    pub = c.read_public()
    check("a tap answers it", pub["phase"] == "active")
    c.ask("mute", True)
    time.sleep(0.6)
    check("mute shows", c.read_public()["muted"] is True)
    c.ask("hangup")
    time.sleep(0.6)
    pub = c.read_public()
    check("hang up ends it, and the bar says so for a few seconds", pub["phase"] == "ended")
    check("the recent calls are the simulator's, and the real log is untouched", not os.path.exists(c.RECENT))
    t.join()

    print("\n  precedence (stylesheets and scripts)")
    css = lambda n: open(os.path.join(ROOT, "share", "css", n)).read()  # noqa: E731

    def z(sheet, selector):
        mt = re.search(re.escape(selector) + r"\s*\{[^}]*?z-index:\s*(\d+)", css(sheet))
        return int(mt.group(1)) if mt else None
    zc, zb = z("calls.css", ".cl-card"), z("calls.css", ".cl-bar")
    zr, zdb, zdl, zpk = z("radar.css", ".rd-banner"), z("drowsy.css", ".dz-banner"), z("drowsy.css", ".dz-layer"), z("parking.css", ".pk-card")
    check("the call card and bar share one layer", zc == zb and zc is not None)
    check("it sits above the radar banner and below drowsy's banner, parking and drowsy's layer",
          zr < zc < zdb < zpk < zdl)
    js = open(os.path.join(ROOT, "share", "js", "calls.js")).read()
    mt = re.search(r"Z = \{ radar: (\d+), call: (\d+), drowsyBanner: (\d+), parking: (\d+), drowsyLayer: (\d+) \}", js)
    check("calls.js Z table matches the stylesheets", mt and [int(x) for x in mt.groups()] == [zr, zc, zdb, zpk, zdl])
    ui = open(os.path.join(ROOT, "share", "js", "callsui.js")).read()
    check("the call UI plays no sound of its own (a child sleeps near the tablet)",
          not re.search(r"new Audio\b|createOscillator|\.play\(|AudioContext", ui + js))
    app = open(os.path.join(ROOT, "share", "app.html")).read()
    check("app.html loads the call UI and its stylesheet", "js/callsui.js" in app and "css/calls.css" in app)

    print()
    if fails:
        print(f"  {fails} FAILED")
        return 1
    print("  every check holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
