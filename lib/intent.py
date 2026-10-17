"""What a spoken sentence means to the car -- a grammar, not a model.

PURE. Nothing here touches a file, the clock, the car or the network, so every
phrasing below is a test (test/voice_test.py). lib/voice.py hears and speaks;
this decides.

THE RULE THAT SHAPES IT: NEVER GUESS A WRITE. A transcript is a guess already
(a tiny speech model, a road, a child in the back). The grammar therefore
matches whole shapes of sentence and refuses everything else with "Sorry, I
didn't catch that." It never picks the nearest command, never reads "mute" as
"mute the radar", never reads "fridge on 34" as one of the two things it could
mean. Reads (how's the air, what's the battery) may default; writes may not.

    parse(text, imperial=True, presets=())  ->  an intent
    gate(intent, moving)                    ->  None, or why it is refused
    reply_text(intent, outcome, imperial)   ->  the sentence to speak or show

An intent is {"intent": "fridge.set", "args": {...}, "heard": "<cleaned text>"}.
The kinds: fridge.set / fridge.power / fridge.query, radar.mute, eq.preset /
eq.power, screen, clip.save / clip.mark, battery, env, unknown.

Temperatures are spoken in the unit the owner uses (imperial by default, the
same as share/js/core.js U) and converted here, once, to the Celsius every
write in this tool takes. "Celsius" or "C" spoken after the number overrides it.
"""

import re

SORRY = "Sorry, I didn't catch that."

# What the fridge accepts (lib/cfx.py SET_MIN/SET_MAX), repeated here so this
# module stays import-free. test/voice_test.py pins the two to cfx.
FRIDGE_MIN_C, FRIDGE_MAX_C = -22.0, 10.0

# Settings that are parked-only in the app (fridgecard.js: "moving locks the set
# point, never power"). A one-tap action stays available while driving.
PARKED_ONLY = {"fridge.set"}

# The built-in equaliser presets (lib/eq.py BUILTIN_PRESETS), by what people say.
EQ_ALIASES = {
    "flat": "Flat", "reset": "Flat", "off": None,
    "car cabin": "Car cabin", "cabin": "Car cabin", "car": "Car cabin",
    "bass": "Bass", "bass boost": "Bass", "bassy": "Bass",
    "vocal clarity": "Vocal clarity", "vocal": "Vocal clarity", "vocals": "Vocal clarity",
    "clarity": "Vocal clarity", "voice": "Vocal clarity",
    "loudness": "Loudness", "loud": "Loudness",
    "night": "Night", "night mode": "Night", "quiet": "Night",
}

# Spoken screen names -> the app's view ids (share/js/main.js).
SCREEN_ALIASES = {
    "home": "home", "home screen": "home", "dashboard": "home",
    "fridge": "fridge", "cooler": "fridge",
    "cabin": "cabin", "cabin tab": "cabin",
    "air": "environment", "cabin air": "environment", "air quality": "environment",
    "environment": "environment", "climate": "environment", "the air": "environment",
    "radar": "radar", "radar detector": "radar", "detector": "radar",
    "camera": "cameras", "cameras": "cameras", "cams": "cameras",
    "road cameras": "roadcams", "road cams": "roadcams", "traffic cameras": "roadcams",
    "equalizer": "eq", "equaliser": "eq", "eq": "eq", "sound": "eq", "audio": "eq",
    "map": "navigation", "maps": "navigation", "navigation": "navigation", "nav": "navigation",
    "battery": "ima", "hybrid battery": "ima", "hybrid": "ima",
    "codes": "codes", "trouble codes": "codes", "fault codes": "codes", "faults": "codes",
    "gauges": "drive", "cluster": "live", "live data": "data",
    "music": "music", "service": "service", "agent": "advisor", "assistant": "advisor",
}

_UNITS_WORDS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
                "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}

_FILLERS = [
    r"^(hey|ok|okay)\s+(oma\s?car|jarvis|mycroft|rhasspy|alexa|computer)\b",
    r"\b(please|thanks|thank you|for me|right now|now|just|go ahead and|"
    r"can you|could you|would you|will you|i want you to|i would like you to|"
    r"i would like to|i want to|i need you to|let's|lets)\b",
]


# ---- text ---------------------------------------------------------------------------

def words_to_numbers(text):
    """'thirty four' / 'thirty-four' / 'minus five' -> '34' / '-5'. Up to 99."""
    toks = text.split()
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in _TENS:
            v = _TENS[t]
            if i + 1 < len(toks) and toks[i + 1] in _UNITS_WORDS and 1 <= _UNITS_WORDS[toks[i + 1]] <= 9:
                v += _UNITS_WORDS[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif t in _UNITS_WORDS and not (t == "oh" and len(toks) == 1):
            out.append(str(_UNITS_WORDS[t]))
        else:
            out.append(t)
        i += 1
    return " ".join(out)


def clean(text):
    """Lower-case, number words as digits, no punctuation, no politeness."""
    t = str(text or "").lower().replace("\u00b0", " degrees ").replace("\u2019", "'")
    t = re.sub(r"(?<=\d)\s*([fc])\b", r" \1", t)          # "34F" -> "34 f"
    t = re.sub(r"(?<=[a-z])[-\u2010-\u2015](?=[a-z])", " ", t)   # "thirty-four"
    t = re.sub(r"[^a-z0-9.\- ']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = words_to_numbers(t)
    t = re.sub(r"\b(minus|negative)\s+(?=\d)", "-", t)
    for pat in _FILLERS:
        t = re.sub(pat, " ", t).strip()
    t = re.sub(r"\b(the|my|our|a|an)\b", " ", t)
    t = re.sub(r"\bwhat's\b", "what is", t)
    t = re.sub(r"\b(how's|hows)\b", "how is", t)
    t = re.sub(r"\bis the\b", "is", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def _numbers(t):
    return re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])", t)


def _has(t, *words):
    return any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words)


def _intent(kind, heard, **args):
    return {"intent": kind, "args": args, "heard": heard}


def _unknown(heard):
    return _intent("unknown", heard)


# ---- the grammar ----------------------------------------------------------------------

_SHOW = re.compile(r"^(show|open|go to|goto|take me to|switch to|bring up|display|pull up|"
                   r"navigate to)( me)?( up)?\s+(?P<what>.+)$")


def _screen(t):
    m = _SHOW.match(t)
    if not m:
        return None
    what = re.sub(r"\b(screen|page|view|tab|app|please)\b", " ", m.group("what"))
    what = re.sub(r"\s+", " ", what).strip()
    return _intent("screen", t, name=what, view=SCREEN_ALIASES.get(what))


def _clip(t):
    if re.match(r"^(save|keep|clip)( that| this| it)( clip| moment| video| footage| recording)?$", t) \
            or re.match(r"^save( clip| that clip| the last minute| last minute)$", t):
        return _intent("clip.save", t)
    if re.match(r"^(mark|flag|bookmark)( that| this| it)?( moment| event)?$", t):
        return _intent("clip.mark", t)
    return None


def _battery(t):
    if not _has(t, "battery"):
        return None
    if _has(t, "fridge", "radar", "detector", "tablet", "sensor", "node", "phone", "camera"):
        return None
    if not _has(t, "what", "how", "check", "level", "charge", "status", "percent", "tell",
                "much", "left", "remaining", "is", "volt", "volts", "voltage", "12v", "12") and t != "battery":
        return None
    twelve = _has(t, "12", "12v", "voltage", "volts", "volt")
    return _intent("battery", t, which="12v" if twelve else "hybrid")


def _radar(t):
    if not _has(t, "radar", "detector", "v1", "valentine"):
        return None
    if re.search(r"\bun ?mute\b|\bsound (back )?on\b|\bturn (it )?(back )?on\b|\bturn the sound back on\b", t):
        return _intent("radar.mute", t, value=False)
    if _has(t, "mute", "silence", "quiet", "hush", "shut"):
        return _intent("radar.mute", t, value=True)
    return None


def _eq_name(frag, presets):
    frag = re.sub(r"\b(preset|setting|profile|mode)\b", " ", frag)
    frag = re.sub(r"\s+", " ", frag).strip()
    for p in presets:
        if frag == p.lower():
            return p, True
    if frag in EQ_ALIASES and EQ_ALIASES[frag]:
        return EQ_ALIASES[frag], True
    return frag, False


def _eq(t, presets):
    has_eq = _has(t, "equalizer", "equaliser", "eq")
    has_preset = _has(t, "preset", "presets")
    sound = _has(t, "sound")
    if not (has_eq or has_preset or sound):
        return None
    if has_eq or sound:
        if re.search(r"\b(off|disable|bypass|stop)\b", t) and not re.search(r"\bon\b|\benable\b", t):
            return _intent("eq.power", t, value=False)
        if re.search(r"\b(on|enable)\b", t) and not re.search(r"\b(off|disable|bypass)\b", t) \
                and not has_preset and not re.search(r"\bto\b", t):
            return _intent("eq.power", t, value=True)
    # "<name> preset", "preset <name>", "equalizer to <name>", "sound to <name>"
    frag = None
    m = re.match(r"^(?:use|set|switch|change|put)?\s*(?:equalizer|equaliser|eq|sound)?\s*"
                 r"(?:to|on)?\s*(?:preset)?\s*(?P<n>[a-z0-9 ]+?)\s*(?:preset|presets)$", t)
    if m:
        frag = m.group("n")
    if frag is None:
        m = re.match(r"^(?:use|set|switch|change|put|load|select)?\s*(?:preset)\s*(?:to)?\s*(?P<n>[a-z0-9 ]+)$", t)
        if m:
            frag = m.group("n")
    if frag is None and (has_eq or sound):
        m = re.match(r"^(?:set|switch|change|put|use)?\s*(?:equalizer|equaliser|eq|sound)\s*"
                     r"(?:to|on)\s+(?P<n>[a-z0-9 ]+)$", t)
        if m:
            frag = m.group("n")
    if frag is None:
        return None
    frag = re.sub(r"^(use|set|switch|change|put|load|select|to|the)\s+", "", frag).strip()
    name, known = _eq_name(frag, presets)
    if not name:
        return _unknown(t)
    return _intent("eq.preset", t, preset=name, known=known)


def _fridge(t):
    if not _has(t, "fridge", "cooler", "freezer"):
        return None
    nums = _numbers(t)
    off = bool(re.search(r"\b(off|disable|shut|stop|power down)\b", t))
    on = bool(re.search(r"\b(on|enable|start|power up)\b", t))
    if nums:
        if len(nums) != 1 or off or on:
            return _unknown(t)            # two numbers, or on/off AND a number: ask again
        c_word = re.search(r"\b(celsius|centigrade)\b|\d\s*(degrees\s+)?c\b", t)
        f_word = re.search(r"\b(fahrenheit)\b|\d\s*(degrees\s+)?f\b", t)
        if c_word and f_word:
            return _unknown(t)
        unit = "c" if c_word else "f" if f_word else None
        return _intent("fridge.set", t, value=float(nums[0]), unit=unit)
    if off and on:
        return _unknown(t)
    if off:
        return _intent("fridge.power", t, value=False)
    if on and re.search(r"\b(turn|switch|power|start|enable|fridge on|on fridge)\b", t):
        return _intent("fridge.power", t, value=True)
    if re.search(r"\b(cold|temperature|temp|degrees|warm|doing|holding|how)\b", t) \
            and not re.search(r"\b(colder|warmer|cooler|higher|lower|up|down)\b", t):
        return _intent("fridge.query", t)
    return _unknown(t)


def _env(t):
    out = _has(t, "outside", "outdoor", "outdoors")
    cab = _has(t, "cabin", "inside", "here", "car", "air")
    thing = _has(t, "air", "co2", "carbon", "humidity", "humid", "temperature", "temp",
                 "climate", "stuffy", "fresh", "quality")
    if not thing:
        return None
    if not re.match(r"^(how|what|is|are|check|tell|read|give)\b", t):
        return None
    if not (out or cab or _has(t, "temperature", "temp", "humidity", "co2", "carbon")):
        return None
    focus = ("co2" if _has(t, "co2", "carbon", "stuffy", "fresh") else
             "temp" if _has(t, "temperature", "temp") else
             "humidity" if _has(t, "humidity", "humid") else "air")
    return _intent("env", t, side="outside" if out else "cabin", focus=focus)


def parse(text, imperial=True, presets=()):
    """The intent for a sentence. Never raises; unknown is a real answer."""
    t = clean(text)
    if not t:
        return _unknown(t)
    # Order matters: "show the fridge" is a screen, not a fridge command, and
    # "fridge temperature" must be the fridge before it is the cabin's.
    for fn in (_screen, _clip, _radar, lambda x: _eq(x, presets), _fridge, _battery, _env):
        got = fn(t)
        if got is not None:
            if got["intent"] == "fridge.set":
                got["args"]["c"] = to_celsius(got["args"]["value"], got["args"]["unit"], imperial)
            return got
    return _unknown(t)


def to_celsius(value, unit, imperial=True):
    f = (unit == "f") or (unit is None and imperial)
    return round((value - 32.0) * 5.0 / 9.0, 2) if f else float(value)


# ---- allowed? -------------------------------------------------------------------------

def gate(intent, moving):
    """None when the intent may run; otherwise a reason code for reply_text()."""
    k = intent["intent"]
    if k == "fridge.set":
        c = intent["args"]["c"]
        if not (FRIDGE_MIN_C <= c <= FRIDGE_MAX_C):
            return "range"
    if k in PARKED_ONLY and moving:
        return "moving"
    return None


# ---- what is said back ------------------------------------------------------------------

def _deg(c, imperial):
    """A temperature as people say it: whole degrees F, or half-degree C."""
    if c is None:
        return None
    if imperial:
        return f"{round(c * 9.0 / 5.0 + 32.0)} degrees"
    v = round(c * 2) / 2
    return f"{v:g} degrees"


def reply_text(intent, outcome=None, imperial=True):
    """The sentence for this intent and what happened. outcome is a dict:
    {"refused": "moving"|"range"|..., "ok": bool, "why": str, ...kind data}."""
    k = intent["intent"]
    a = intent.get("args") or {}
    o = outcome or {}
    if k == "unknown":
        return SORRY
    r = o.get("refused")
    if r == "moving":
        return "I can't change that while the car is moving. Ask me again when you're parked."
    if r == "range":
        return (f"The fridge only goes from {_deg(FRIDGE_MIN_C, imperial)} "
                f"to {_deg(FRIDGE_MAX_C, imperial)}.")
    if r == "unavailable":
        return o.get("why") or "That isn't available."
    if r == "quiet":
        return ""
    if k == "fridge.set":
        if o.get("ok"):
            return f"Fridge set to {_deg(o['c'], imperial)}."
        return o.get("why") or "I couldn't set the fridge."
    if k == "fridge.power":
        if o.get("ok"):
            return "Fridge on." if a["value"] else "Fridge off."
        return o.get("why") or "I couldn't reach the fridge."
    if k == "fridge.query":
        if not o.get("ok"):
            return o.get("why") or "I can't read the fridge."
        s = f"The fridge is at {_deg(o['temp'], imperial)}"
        if o.get("set") is not None:
            s += f", set to {_deg(o['set'], imperial)}"
        if o.get("power") is False:
            s = "The fridge is off"
        return s + ("." if not o.get("stale") else f", as of {o['stale']} ago.")
    if k == "radar.mute":
        if o.get("ok"):
            return "Radar muted." if a["value"] else "Radar sound back on."
        return o.get("why") or "No radar detector is connected."
    if k == "eq.power":
        return ("Equalizer on." if a["value"] else "Equalizer off.") if o.get("ok") \
            else (o.get("why") or "I couldn't change the equalizer.")
    if k == "eq.preset":
        if o.get("ok"):
            return f"Equalizer on, {o.get('preset', a['preset'])} preset."
        return o.get("why") or f"I don't have a preset called {a['preset']}."
    if k == "screen":
        if o.get("ok"):
            return f"Showing {o.get('label') or a['name']}."
        return o.get("why") or f"I don't have a screen called {a['name']}."
    if k == "clip.save":
        return "Saving that clip. It finishes in about thirty seconds." if o.get("ok") \
            else (o.get("why") or "Saving clips isn't available.")
    if k == "clip.mark":
        return "Marked." if o.get("ok") else (o.get("why") or "Marking isn't available.")
    if k == "battery":
        if not o.get("ok"):
            return o.get("why") or "I can't read the car right now."
        if a["which"] == "12v":
            return f"The twelve volt battery is at {o['volts']:.1f} volts."
        return f"The hybrid battery is at {round(o['percent'])} percent."
    if k == "env":
        return env_sentence(a, o, imperial) if o.get("ok") else \
            (o.get("why") or "I can't read the air sensors right now.")
    return SORRY


def env_sentence(a, o, imperial=True):
    """'The cabin is 72 degrees, the air is fresh, humidity 44 percent.'"""
    who = "outside" if a["side"] == "outside" else "in the cabin"
    parts = []
    t = _deg(o.get("t_c"), imperial)
    co2, band, rh = o.get("co2"), o.get("co2_label"), o.get("rh")
    focus = a["focus"]
    if focus in ("air", "temp") and t:
        parts.append(f"It is {t} {who}")
    if focus in ("air", "co2") and co2 is not None:
        word = (band or "").lower()
        parts.append(f"the air is {word}" if word else "")
        if focus == "co2":
            parts.append(f"carbon dioxide is {round(co2)} parts per million")
    if focus in ("air", "humidity") and rh is not None:
        parts.append(f"humidity {round(rh)} percent")
    if focus == "air" and o.get("pm25") is not None and a["side"] == "outside":
        parts.append(f"fine particles {round(o['pm25'])}")
    parts = [p for p in parts if p]
    if not parts:
        word = {"temp": "temperature", "co2": "carbon dioxide"}.get(focus, focus)
        return f"I have no {word} reading {who} right now."
    head = parts[0][0].upper() + parts[0][1:]
    tail = ", ".join(parts[1:])
    s = head + (", " + tail if tail else "")
    if o.get("source") == "sim":
        s += ". That's a simulated reading"
    return s + "."


# ---- when it may listen or speak ----------------------------------------------------------

def parse_hhmm(s, default):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m:
        return default
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else default


def in_quiet_hours(minute_of_day, start="21:00", end="07:00"):
    """True from start up to (not including) end, across midnight if need be."""
    s, e = parse_hhmm(start, 21 * 60), parse_hhmm(end, 7 * 60)
    if s == e:
        return False
    return s <= minute_of_day < e if s < e else (minute_of_day >= s or minute_of_day < e)


def may_listen(cfg, minute_of_day, car_on):
    """No wake listening and no speech in quiet hours unless the car is on."""
    q = cfg.get("quiet") or {}
    if not q.get("on", True):
        return True
    return bool(car_on) or not in_quiet_hours(minute_of_day, q.get("start", "21:00"),
                                              q.get("end", "07:00"))
