#!/usr/bin/env python3
"""Voice commands: the grammar (lib/intent.py), the rules around it
(quiet hours, parked-only, what is spoken) and the service's pure parts
(lib/voice.py). No microphone, no models, no Piper: those are stand-ins here
and are named as unverified in doc/voice.md.
"""

import json
import os
import shutil
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
    scratch = tempfile.mkdtemp(prefix="omacar-voice-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    import cfx
    import intent as g
    import voice
    import fridge
    import v1
    import screen
    try:
        run(g, voice, cfx, fridge, v1, screen, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'voice: all checks hold' if not fails else f'voice: {fails} FAILED'}")
    return 1 if fails else 0


def kind(g, text, **kw):
    return g.parse(text, **kw)["intent"]


def run(g, voice, cfx, fridge, v1, screen, scratch):
    print("\n  the fridge")
    p = lambda t, **kw: g.parse(t, **kw)  # noqa: E731
    check("pins the fridge range to cfx", (g.FRIDGE_MIN_C, g.FRIDGE_MAX_C) == (cfx.SET_MIN, cfx.SET_MAX))
    r = p("fridge to 34")
    check("'fridge to 34' is a set, in Fahrenheit by default",
          r["intent"] == "fridge.set" and abs(r["args"]["c"] - 1.11) < 0.01)
    check("the same in metric when the owner is metric", p("fridge to 4", imperial=False)["args"]["c"] == 4.0)
    check("'celsius' spoken overrides imperial", p("fridge to 4 celsius")["args"]["c"] == 4.0)
    check("'fahrenheit' spoken overrides metric", abs(p("fridge to 34 fahrenheit", imperial=False)["args"]["c"] - 1.11) < 0.01)
    check("'34 degrees F' with the degree sign", p("Set the fridge to 34°F")["intent"] == "fridge.set")
    check("'34F' glued", p("fridge 34F")["args"]["value"] == 34.0)
    check("number words: thirty four", p("fridge to thirty four")["args"]["value"] == 34.0)
    check("number words with a hyphen", p("set the fridge to thirty-six degrees")["args"]["value"] == 36.0)
    check("minus five", p("fridge to minus five")["args"]["value"] == -5.0)
    check("negative 5", p("make the fridge negative 5")["args"]["value"] == -5.0)
    check("polite, with the wake echo",
          kind(g, "Hey Jarvis, could you please set the fridge to 38 degrees?") == "fridge.set")
    check("'fridge temperature to 34'", kind(g, "fridge temperature to 34") == "fridge.set")
    check("'turn the fridge off'", p("turn the fridge off")["args"] == {"value": False})
    check("'fridge off'", p("fridge off")["intent"] == "fridge.power")
    check("'shut off the fridge'", p("shut off the fridge")["args"] == {"value": False})
    check("'turn the fridge on'", p("turn the fridge on")["args"] == {"value": True})
    check("'switch the fridge on'", p("switch the fridge on")["args"] == {"value": True})
    check("'how cold is the fridge' is a read", kind(g, "how cold is the fridge") == "fridge.query")
    check("'what's the fridge temperature' is a read", kind(g, "what's the fridge temperature") == "fridge.query")
    check("'fridge on 34' is NOT guessed", kind(g, "fridge on 34") == "unknown")
    check("'fridge off 34' is NOT guessed", kind(g, "fridge off 34") == "unknown")
    check("two numbers are NOT guessed", kind(g, "fridge to 34 or 36") == "unknown")
    check("'celsius and fahrenheit' together is NOT guessed", kind(g, "fridge to 34 fahrenheit celsius") == "unknown")
    check("'make the fridge colder' is NOT guessed", kind(g, "make the fridge colder") == "unknown")
    check("a bare 'fridge' is NOT guessed", kind(g, "fridge") == "unknown")

    print("\n  radar, equalizer, screens, clips, battery, air")
    check("'mute the radar'", p("mute the radar")["args"] == {"value": True})
    check("'radar mute'", p("radar mute")["args"] == {"value": True})
    check("'silence the radar detector'", p("silence the radar detector")["args"] == {"value": True})
    check("'unmute the radar'", p("unmute the radar")["args"] == {"value": False})
    check("'turn the radar sound back on'", p("turn the radar sound back on")["args"] == {"value": False})
    check("'mute' alone is NOT the radar", kind(g, "mute") == "unknown")
    check("'bass preset'", p("bass preset")["args"]["preset"] == "Bass")
    check("'use the vocal preset'", p("use the vocal preset")["args"]["preset"] == "Vocal clarity")
    check("'car cabin preset'", p("car cabin preset")["args"]["preset"] == "Car cabin")
    check("'set the equalizer to night'", p("set the equalizer to night")["args"]["preset"] == "Night")
    check("'equalizer to flat'", p("equalizer to flat")["args"]["preset"] == "Flat")
    check("'preset loudness'", p("preset loudness")["args"]["preset"] == "Loudness")
    check("'equalizer off'", p("equalizer off")["args"] == {"value": False})
    check("'turn the equalizer on'", p("turn the equalizer on")["args"] == {"value": True})
    check("'disable the eq'", p("disable the eq")["args"] == {"value": False})
    check("a user preset is found by name", p("road trip preset", presets=["Road trip"])["args"]["preset"] == "Road trip")
    check("an unknown preset is carried, flagged unknown", not p("banana preset")["args"]["known"])
    check("'show the fridge'", p("show the fridge")["args"]["view"] == "fridge")
    check("'open cameras'", p("open cameras")["args"]["view"] == "cameras")
    check("'go to the radar screen'", p("go to the radar screen")["args"]["view"] == "radar")
    check("'take me to the map'", p("take me to the map")["args"]["view"] == "navigation")
    check("'show me the cabin air'", p("show me the cabin air")["args"]["view"] == "environment")
    check("'open the equalizer'", p("open the equalizer")["args"]["view"] == "eq")
    check("'show the weather' is a screen the app may not have", p("show the weather")["args"]["view"] is None)
    check("'save that'", kind(g, "save that") == "clip.save")
    check("'save that clip'", kind(g, "save that clip") == "clip.save")
    check("'mark that'", kind(g, "mark that") == "clip.mark")
    check("'what's my battery'", p("what's my battery")["args"] == {"which": "hybrid"})
    check("'battery level'", kind(g, "battery level") == "battery")
    check("'how's the twelve volt battery'", p("how's the twelve volt battery")["args"] == {"which": "12v"})
    check("'the fridge battery' is not the car's", kind(g, "what is the fridge battery") != "battery")
    check("'how's the cabin air'", p("how's the cabin air")["args"] == {"side": "cabin", "focus": "air"})
    check("'how is the air quality outside'", p("how is the air quality outside")["args"]["side"] == "outside")
    check("'what's the temperature outside'", p("what's the temperature outside")["args"] == {"side": "outside", "focus": "temp"})
    check("'what's the cabin temperature'", p("what's the cabin temperature")["args"] == {"side": "cabin", "focus": "temp"})
    check("'what's the co2 in here'", p("what's the CO2 in here")["args"]["focus"] == "co2")
    check("'what's the humidity'", p("what's the humidity")["args"]["focus"] == "humidity")

    print("\n  what is NOT understood is not acted on")
    for t in ["", "   ", "hello", "what time is it", "play some music", "call mom", "turn it up",
              "fridge to", "set the temperature", "open", "show", "take me home",
              "delete everything", "disable the brakes", "clear the codes", "unlock the doors",
              "bass", "preset", "save", "turn it off", "hey omacar", "the", "34", "yes", "no", "stop"]:
        check(f"unknown: {t!r}", kind(g, t) == "unknown")
    check("the sorry sentence", g.reply_text(p("blah"), {}, True) == "Sorry, I didn't catch that.")

    print("\n  refusals: understood, then declined")
    it = p("fridge to 34")
    check("fridge set while moving is refused", g.gate(it, True) == "moving")
    check("fridge set while parked is allowed", g.gate(it, False) is None)
    check("fridge power is a one-tap action: allowed moving", g.gate(p("fridge off"), True) is None)
    check("radar mute is allowed moving", g.gate(p("mute the radar"), True) is None)
    check("fridge to 70 is out of range", g.gate(p("fridge to 70"), False) == "range")
    check("fridge to minus 20 F is in range (-29 C is not)", g.gate(p("fridge to minus 20"), False) == "range")
    check("fridge to 0 F is in range", g.gate(p("fridge to 0"), False) is None)
    check("the moving refusal says why",
          "moving" in g.reply_text(it, {"refused": "moving"}) and "parked" in g.reply_text(it, {"refused": "moving"}))
    check("the range refusal names the limits",
          g.reply_text(p("fridge to 70"), {"refused": "range"}, True) == "The fridge only goes from -8 degrees to 50 degrees.")

    print("\n  the words said back")
    R = lambda t, o, imp=True: g.reply_text(p(t), o, imp)  # noqa: E731
    check("fridge set", R("fridge to 34", {"ok": True, "c": 1.0}) == "Fridge set to 34 degrees.")
    check("fridge set, metric", R("fridge to 4", {"ok": True, "c": 4.0}, False) == "Fridge set to 4 degrees.")
    check("fridge set, half a degree", R("fridge to 3.5", {"ok": True, "c": 3.5}, False) == "Fridge set to 3.5 degrees.")
    check("fridge off", R("fridge off", {"ok": True}) == "Fridge off.")
    check("fridge not connected", R("fridge off", {"ok": False, "why": "The fridge isn't connected right now."}) == "The fridge isn't connected right now.")
    check("fridge read", R("how cold is the fridge", {"ok": True, "temp": 3.4, "set": 3.0, "power": True}) == "The fridge is at 38 degrees, set to 37 degrees.")
    check("fridge read, stale", "as of 5 minutes ago" in R("how cold is the fridge", {"ok": True, "temp": 3.4, "set": None, "stale": "5 minutes"}))
    check("radar muted", R("mute the radar", {"ok": True}) == "Radar muted.")
    check("radar unmuted", R("unmute the radar", {"ok": True}) == "Radar sound back on.")
    check("radar absent", R("mute the radar", {"ok": False}) == "No radar detector is connected.")
    check("eq preset", R("bass preset", {"ok": True, "preset": "Bass"}) == "Equalizer on, Bass preset.")
    check("eq off", R("equalizer off", {"ok": True}) == "Equalizer off.")
    check("eq unknown preset", R("banana preset", {"ok": False}) == "I don't have a preset called banana.")
    check("screen", R("show the fridge", {"ok": True, "label": "fridge"}) == "Showing fridge.")
    check("screen missing", R("show the weather", {"ok": False}) == "I don't have a screen called weather.")
    check("clip", "thirty seconds" in R("save that", {"ok": True}))
    check("clip unavailable", R("save that", {"ok": False}) == "Saving clips isn't available.")
    check("battery", R("what's my battery", {"ok": True, "percent": 61.6}) == "The hybrid battery is at 62 percent.")
    check("12 volt", R("twelve volt battery", {"ok": True, "volts": 13.84}) == "The twelve volt battery is at 13.8 volts.")
    check("no car", R("what's my battery", {"ok": False, "why": "I can't read the car right now."}) == "I can't read the car right now.")
    env = {"ok": True, "t_c": 22.2, "rh": 44.0, "co2": 640, "co2_label": "Fresh"}
    check("air summary", R("how's the cabin air", env) == "It is 72 degrees in the cabin, the air is fresh, humidity 44 percent.")
    check("temp only", R("what's the cabin temperature", env) == "It is 72 degrees in the cabin.")
    check("outside temp", R("what's the temperature outside", env) == "It is 72 degrees outside.")
    check("co2", "640 parts per million" in R("what's the CO2", env))
    check("a simulated air reading says so", "simulated" in R("how's the cabin air", dict(env, source="sim")))
    check("no sensor value", "no temperature reading in the cabin" in R("what's the cabin temperature", {"ok": True}))

    print("\n  quiet hours")
    H = lambda h, m=0: h * 60 + m  # noqa: E731
    check("22:00 is quiet", g.in_quiet_hours(H(22)))
    check("03:00 is quiet", g.in_quiet_hours(H(3)))
    check("21:00 sharp is quiet", g.in_quiet_hours(H(21)))
    check("20:59 is not", not g.in_quiet_hours(H(20, 59)))
    check("07:00 sharp is not", not g.in_quiet_hours(H(7)))
    check("06:59 is quiet", g.in_quiet_hours(H(6, 59)))
    check("noon is not", not g.in_quiet_hours(H(12)))
    check("a daytime window works (13:00-15:00)", g.in_quiet_hours(H(14), "13:00", "15:00") and not g.in_quiet_hours(H(16), "13:00", "15:00"))
    check("equal start and end means never", not g.in_quiet_hours(H(3), "07:00", "07:00"))
    check("a bad time falls back to the default", g.in_quiet_hours(H(23), "nine", "pm"))
    cfg = voice.clean_config({})
    check("quiet and car off: no listening", not g.may_listen(cfg, H(23), False))
    check("quiet but car on: listening", g.may_listen(cfg, H(23), True))
    check("daytime: listening", g.may_listen(cfg, H(12), False))
    off = voice.clean_config({"quiet": {"on": False}})
    check("quiet hours switched off: always listening", g.may_listen(off, H(3), False))
    check("it ships OFF", cfg["enabled"] is False)

    print("\n  config")
    check("defaults", cfg["phrase"] == "hey_jarvis" and cfg["quiet"] == {"on": True, "start": "21:00", "end": "07:00"})
    try:
        voice.clean_config({"phrase": "hey_nobody"})
        check("an unknown phrase is refused", False)
    except ValueError:
        check("an unknown phrase is refused", True)
    try:
        voice.clean_config({"quiet": {"start": "25:00"}})
        check("a bad quiet time is refused", False)
    except ValueError:
        check("a bad quiet time is refused", True)
    check("threshold is clamped", voice.clean_config({"threshold": 5})["threshold"] == 0.95)
    os.makedirs(voice.MODELS)
    open(os.path.join(voice.MODELS, "hey_omacar.onnx"), "wb").close()
    check("a dropped-in model becomes a phrase", "hey_omacar" in voice.available_phrases())
    pub = voice.save_config({"enabled": True, "phrase": "hey_omacar", "quiet": {"start": "22:30"}})
    check("saved and read back", pub["enabled"] and pub["config"]["phrase"] == "hey_omacar"
          and pub["config"]["quiet"]["start"] == "22:30" and pub["config"]["quiet"]["end"] == "07:00")
    check("phrase words for display", pub["phrase_words"] == "hey omacar")
    check("the API cannot set the Piper voice path", voice.save_config({"piper_voice": "/etc/passwd"})["config"]["piper_voice"] is None)
    check("not running with no heartbeat", pub["running"] is False and pub["phase"] == "stopped")
    import api
    code, body = api.handle_get("/api/voice", "")
    check("GET /api/voice", code == 200 and body["present"])
    code, body = api.handle_post("/api/voice", json.dumps({"phrase": "alexa"}))
    check("POST /api/voice", code == 200 and body["config"]["phrase"] == "alexa")
    code, body = api.handle_post("/api/voice", json.dumps({"phrase": "nobody"}))
    check("POST /api/voice with a bad phrase is a 400", code == 400)
    os.unlink(voice.CONFIG)

    print("\n  the microphone, the end of a sentence, Piper")
    check("parec is preferred", voice.mic_cmd(lambda n: "/usr/bin/" + n)[0] == "parec")
    check("pw-record is the fallback", voice.mic_cmd(lambda n: "/x" if n == "pw-record" else None)[0] == "pw-record")
    check("neither: None", voice.mic_cmd(lambda n: None) is None)
    check("both ask for 16 kHz mono s16", all("16000" in " ".join(voice.mic_cmd(lambda n, w=w: "/x" if n == w else None)) for w in ("parec", "pw-record")))
    import struct
    loud = struct.pack("<1280h", *([3000, -3000] * 640))
    still = struct.pack("<1280h", *([0] * 1280))
    check("rms of silence is 0", voice.rms(still) == 0)
    check("rms of a square wave", abs(voice.rms(loud) - 3000) < 1)
    e = voice.Endpointer()
    seq = [e.feed(0) for _ in range(5)]
    check("quiet at the start: still waiting", all(x is None for x in seq))
    e = voice.Endpointer()
    out = None
    for _ in range(10):
        e.feed(0)                      # guard
    for _ in range(12):
        e.feed(3000)                   # speaking
    for _ in range(30):
        out = e.feed(0)
        if out:
            break
    check("speech then a second of quiet ends it", out == "silence" and e.heard)
    e = voice.Endpointer()
    out = [x for x in (e.feed(0) for _ in range(80)) if x]
    check("nobody speaks: gives up", out and out[0] == "nospeech" and not e.heard)
    e = voice.Endpointer()
    out = None
    for _ in range(200):
        out = e.feed(3000)
        if out:
            break
    check("someone who never stops is cut at 6 s", out == "max" and 5.9 <= e.t <= 6.1)
    e = voice.Endpointer()
    for _ in range(6):
        e.feed(3000)                   # the wake phrase's own tail, inside the guard
    check("the wake phrase's tail (inside the guard) does not count as the command", not e.heard)
    c = {"piper_voice": "/v.onnx", "speak": True}
    check("no piper: not ready", not voice.piper_ready(c, which=lambda n: None)[0])
    check("no voice set: not ready", not voice.piper_ready({"piper_voice": None}, which=lambda n: "/x")[0])
    check("missing voice file: not ready", not voice.piper_ready(c, which=lambda n: "/x", exists=lambda p: False)[0])
    check("piper and voice: ready", voice.piper_ready(c, which=lambda n: "/x", exists=lambda p: True)[0])
    calls = []

    class Done:
        returncode = 0

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        return Done()
    ok = voice.speak("Radar muted.", c, run=fake_run, which=lambda n: "/x", exists=lambda p: True)
    check("speaking runs piper as its own program, then a player",
          ok and calls[0][0][:3] == ["piper", "-m", "/v.onnx"] and calls[0][0][3] == "-f"
          and calls[0][1] == "Radar muted." and calls[1][0][0] == "pw-play")
    check("speak off: silent", not voice.speak("hi", dict(c, speak=False), run=fake_run, which=lambda n: "/x"))
    check("piper is never imported", "piper" not in sys.modules)

    print("\n  doing it: the same functions the taps use")
    write_json = lambda path, obj: (os.makedirs(os.path.dirname(path), exist_ok=True), json.dump(obj, open(path, "w")))  # noqa: E731
    fr = fridge.blank("ble")
    fr.update(connected=True, power=True, zones=[{"temp": 3.4, "set": 3.0, "door": False, "power": True, "range": [-22, 10]}])
    fridge.write(fr)
    ctx = {"imperial": True, "moving": False, "presets": []}
    res = voice.handle("set the fridge to 34", dict(ctx))
    ask = json.load(open(fridge.ASK))
    check("the fridge ask was queued through fridge.ask (half-degree steps)", ask["name"] == "z0_set" and ask["value"] == 1.0)
    check("and it was told back", res["reply"] == "Fridge set to 34 degrees.")
    os.unlink(fridge.ASK)
    res = voice.handle("set the fridge to 34", dict(ctx, moving=True))
    check("moving: nothing queued", not os.path.exists(fridge.ASK) and "moving" in res["reply"])
    res = voice.handle("fridge off", dict(ctx, moving=True))
    check("moving: power is still one tap", json.load(open(fridge.ASK))["value"] is False and res["reply"] == "Fridge off.")
    os.unlink(fridge.ASK)
    res = voice.handle("fridge to 90", dict(ctx))
    check("out of range: nothing queued", not os.path.exists(fridge.ASK) and "only goes" in res["reply"])
    res = voice.handle("how cold is the fridge", dict(ctx))
    check("the fridge is read from fridge.json", res["reply"] == "The fridge is at 38 degrees, set to 37 degrees.")
    fr["connected"] = False
    fridge.write(fr)
    import time
    st = json.load(open(fridge.STATE))
    st["at"] = time.time() - 600
    json.dump(st, open(fridge.STATE, "w"))
    res = voice.handle("fridge to 34", dict(ctx))
    check("a fridge that is not connected is not pretended to", not os.path.exists(fridge.ASK) and "isn't connected" in res["reply"])
    res = voice.handle("mute the radar", dict(ctx))
    check("no detector: said so, nothing queued", res["reply"] == "No radar detector is connected." and not os.path.exists(v1.ASK))
    rad = v1.blank("ble")
    rad["connected"] = True
    v1.write(rad)
    res = voice.handle("mute the radar", dict(ctx))
    check("the detector is muted through v1.ask", json.load(open(v1.ASK)) == {"name": "mute", "value": True, "at": json.load(open(v1.ASK))["at"]} and res["reply"] == "Radar muted.")
    screen.publish([{"id": "fridge", "label": "Fridge", "tab": "cabin"}, {"id": "cameras", "label": "Cameras", "tab": "cameras"}])
    res = voice.handle("show the fridge", dict(ctx))
    check("a screen is asked for as 'voice'", screen.pending()["view"] == "fridge" and screen.pending()["who"] == "voice"
          and res["reply"] == "Showing fridge.")
    res = voice.handle("show the weather", dict(ctx))
    check("a screen the app does not have is said so", res["reply"] == "I don't have a screen called weather.")
    res = voice.handle("show the radar", dict(ctx))
    check("a known alias the app does not publish is not forced", "don't have a screen" in res["reply"])
    res = voice.handle("what's the cabin temperature", dict(ctx))
    check("no sensors: said so", res["reply"] == "There are no air sensors connected.")
    res = voice.handle("what is my battery", dict(ctx))
    check("no car data: said so", res["reply"] == "I can't read the car right now.")
    res = voice.handle("banana", dict(ctx))
    check("gibberish writes nothing", res["reply"] == "Sorry, I didn't catch that.")

    print("\n  deliver(): quiet hours hold the voice")
    spoken = []
    voice.car_is_on = lambda: False
    cfgq = voice.clean_config({"quiet": {"on": True}})
    t_night = __import__("time").mktime((2026, 10, 17, 23, 30, 0, 0, 0, -1))
    t_day = __import__("time").mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
    sp = lambda text, cfg: spoken.append(text) or True  # noqa: E731
    voice.deliver("banana", "typed", cfgq, ctx=dict(ctx), now=t_night, speaker=sp)
    check("23:30 car off: shown, not spoken", spoken == [])
    st = json.load(open(voice.STATE))
    check("state says typed, and not spoken", st["source"] == "typed" and st["spoke"] is False)
    voice.deliver("banana", "typed", cfgq, ctx=dict(ctx), now=t_day, speaker=sp)
    check("noon: spoken", spoken == ["Sorry, I didn't catch that."])
    voice.car_is_on = lambda: True
    voice.deliver("banana", "typed", cfgq, ctx=dict(ctx), now=t_night, speaker=sp)
    check("23:30 with the car on: spoken", len(spoken) == 2)


if __name__ == "__main__":
    sys.exit(main())
