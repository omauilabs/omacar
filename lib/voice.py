#!/usr/bin/env python3
""""Hey Jarvis" (or your own phrase): offline voice commands for the car.

    omacar voice status         what the service and its config say now
    omacar voice on | off       opt in / out (it ships OFF: it opens a microphone)
    omacar voice phrase NAME    hey_jarvis, alexa, hey_mycroft, hey_rhasspy, or a
                                model you dropped into ~/.config/omacar/voice/models/
    omacar voice quiet 21:00 07:00 | off      no listening, no speech, in between
    omacar voice voice PATH     the Piper voice (.onnx) to speak replies with
    omacar voice models         the phrases available here
    omacar voice mic            record 3 s and report the level: is the mic heard?
    omacar voice say "fridge to 34"   run a typed sentence through the same router
                                (state says "Typed", never "Heard")
    omacar voice sim            a scripted listening cycle, labelled Simulated
    omacar voice run            hold the microphone (omacar-voice.service)
    omacar voice setup          pip-install openwakeword + faster-whisper into the venv

THE PIPELINE, every stage local, no network after setup:
  1. the microphone, through PipeWire's pulse layer (`parec`, from libpulse, the
     same package `pactl` -- already required by lib/audio.py -- comes from; or
     `pw-record` when only that exists), 16 kHz mono 16-bit;
  2. openWakeWord (Apache-2.0 code; its PRETRAINED MODELS are CC BY-NC-SA 4.0:
     fine for one owner's own car, not for selling a product -- doc/voice.md);
  3. the command, recorded until about a second of silence (6 s at most);
  4. faster-whisper `tiny.en`, int8 on the CPU, in the venv;
  5. lib/intent.py -- a grammar, no ML -- and the same functions the API calls
     (fridge.ask, v1.ask, eq.write_state, screen.ask, camstore.mark), so every
     write passes the validation a tap would;
  6. the reply spoken by Piper if `piper` is installed (a SEPARATE PROGRAM,
     never bundled: it is GPL-3.0) and a voice is set; otherwise shown as a
     toast in the app.

QUIET HOURS (21:00 to 07:00 by default): the microphone is not even opened and
nothing is spoken, unless the car is on. A child sleeps near the tablet.
"""

import array
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intent as grammar  # noqa: E402

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONF_DIR = os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar")
CONFIG = os.path.join(CONF_DIR, "voice.json")
MODELS = os.path.join(CONF_DIR, "voice", "models")
STATE = os.path.join(STATE_DIR, "voice.json")

RATE = 16000
CHUNK = 1280                 # samples: 80 ms, openWakeWord's native frame
CHUNK_S = CHUNK / RATE
STALE = 10                   # s without a heartbeat: the service has stopped
PRETRAINED = ["hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy"]
WHISPER_MODEL = "tiny.en"
PROMPT = ("fridge, radar, equalizer, bass preset, cabin air, battery, "
          "show the cameras, save that, degrees")

DEFAULTS = {
    "enabled": False,
    "phrase": "hey_jarvis",
    "threshold": 0.5,
    "speak": True,
    "piper_voice": None,
    "quiet": {"on": True, "start": "21:00", "end": "07:00"},
}


# ---- config ---------------------------------------------------------------------------

def custom_models():
    """{stem: path} for model files dropped into the config folder."""
    out = {}
    try:
        for f in sorted(os.listdir(MODELS)):
            stem, ext = os.path.splitext(f)
            if ext in (".onnx", ".tflite"):
                out[stem] = os.path.join(MODELS, f)
    except OSError:
        pass
    return out


def available_phrases():
    return PRETRAINED + [m for m in custom_models() if m not in PRETRAINED]


def phrase_words(phrase):
    return str(phrase).replace("_", " ")


def clean_config(raw):
    """A config from anything: unknown keys dropped, bad values defaulted.
    Raises ValueError only for a phrase that does not exist here."""
    raw = raw if isinstance(raw, dict) else {}
    cfg = json.loads(json.dumps(DEFAULTS))
    if isinstance(raw.get("enabled"), bool):
        cfg["enabled"] = raw["enabled"]
    if isinstance(raw.get("speak"), bool):
        cfg["speak"] = raw["speak"]
    if "phrase" in raw:
        p = str(raw["phrase"]).strip()
        if p not in available_phrases():
            raise ValueError(f"no wake phrase called {p!r}; have {', '.join(available_phrases())}")
        cfg["phrase"] = p
    t = raw.get("threshold")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t):
        cfg["threshold"] = min(0.95, max(0.2, float(t)))
    v = raw.get("piper_voice")
    if isinstance(v, str) and v.strip():
        cfg["piper_voice"] = v.strip()
    q = raw.get("quiet")
    if isinstance(q, dict):
        if isinstance(q.get("on"), bool):
            cfg["quiet"]["on"] = q["on"]
        for k in ("start", "end"):
            if k in q:
                mins = grammar.parse_hhmm(q[k], None)
                if mins is None:
                    raise ValueError(f"quiet {k} must be HH:MM")
                cfg["quiet"][k] = f"{mins // 60:02d}:{mins % 60:02d}"
    return cfg


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    try:
        return clean_config(raw)
    except ValueError:
        raw.pop("phrase", None)        # a phrase whose model file was removed
        return clean_config(raw)


def save_config(raw):
    """Merge `raw` over the saved config, validate, write. Returns read_public()."""
    merged = load_config()
    raw = raw if isinstance(raw, dict) else {}
    for k, v in raw.items():
        if k == "quiet" and isinstance(v, dict):
            merged["quiet"].update(v)
        elif k in DEFAULTS and k != "piper_voice":      # the voice path is CLI-only
            merged[k] = v
    cfg = clean_config(merged)
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)
    return read_public()


# ---- the state file the app reads --------------------------------------------------------

def write_state(**kw):
    st = _read_state() or {}
    st.update(kw)
    st["at"] = time.time()
    st["pid"] = os.getpid()
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)
    return st


def _read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def have_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def mic_cmd(which=shutil.which):
    """The argv that writes 16 kHz mono s16le to stdout, or None."""
    if which("parec"):
        return ["parec", "--rate=16000", "--channels=1", "--format=s16le",
                "--latency-msec=40"]
    if which("pw-record"):
        return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
    return None


def piper_ready(cfg, which=shutil.which, exists=os.path.exists):
    if not which("piper"):
        return False, "piper is not installed"
    v = cfg.get("piper_voice")
    if not v:
        return False, "no Piper voice set: omacar voice voice PATH"
    if not exists(os.path.expanduser(v)):
        return False, f"Piper voice not found: {v}"
    return True, None


def minute_now(t=None):
    lt = time.localtime(t)
    return lt.tm_hour * 60 + lt.tm_min


def car_is_on():
    """The car is on when the charger is (lib/ignition.py), or the adapter answers."""
    try:
        import ignition
        pub = ignition.read_public()
        if pub.get("running") and pub.get("powered") is True:
            return True
    except Exception:                                            # noqa: BLE001
        pass
    try:
        import records
        live = records.live()
        return bool(live.get("connected")) and not live.get("simulated")
    except Exception:                                            # noqa: BLE001
        return False


def car_is_moving():
    """Same reading as the app's store.lockedAsMoving: driving, or a hand-off."""
    try:
        import records
        live = records.live()
        return bool(live.get("handover")) or records.status(live) == "driving"
    except Exception:                                            # noqa: BLE001
        return False


def read_public(now=None):
    cfg = load_config()
    st = _read_state() or {}
    now = time.time() if now is None else now
    age = now - float(st.get("at", 0)) if st else None
    running = bool(st) and age is not None and age <= STALE
    ok, why = piper_ready(cfg)
    car_on = car_is_on()
    quiet = not grammar.may_listen(cfg, minute_now(now), car_on)
    phase = st.get("phase") if running else "off"
    if cfg["enabled"] and not running:
        phase = "stopped"
    out = {"present": True, "enabled": cfg["enabled"], "running": running,
           "phase": phase, "quiet_now": quiet, "config": cfg,
           "phrase_words": phrase_words(cfg["phrase"]),
           "phrases": available_phrases(),
           "can": {"wake": have_module("openwakeword"),
                   "transcribe": have_module("faster_whisper"),
                   "mic": mic_cmd() is not None,
                   "speak": ok, "speak_why": why}}
    for k in ("source", "transcript", "reply", "spoke", "seq", "error", "intent"):
        if running and k in st:
            out[k] = st[k]
    out["age"] = round(age, 1) if age is not None else None
    return out


# ---- finding the end of a sentence ----------------------------------------------------

def rms(pcm):
    """Root-mean-square of s16le bytes. Pure Python: no numpy needed here."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a))


class Endpointer:
    """Decides when a spoken command is over. Feed it one RMS per chunk.

    Ignores `guard_s` at the start (the tail of the wake phrase), waits up to
    `wait_s` for speech to begin, ends `silence_s` after it stops, and never
    runs past `max_s`. Returns None while going, else why it ended."""

    def __init__(self, chunk_s=CHUNK_S, guard_s=0.5, wait_s=3.0, silence_s=1.0,
                 max_s=6.0, floor=200.0, min_rms=350.0, ratio=3.0):
        self.chunk_s, self.guard_s, self.wait_s = chunk_s, guard_s, wait_s
        self.silence_s, self.max_s = silence_s, max_s
        self.floor, self.min_rms, self.ratio = floor, min_rms, ratio
        self.t = 0.0
        self.heard = False
        self.quiet_for = 0.0

    def feed(self, level):
        self.t += self.chunk_s
        if self.t >= self.max_s:
            return "max"
        if self.t <= self.guard_s:
            return None
        loud = level > max(self.min_rms, self.floor * self.ratio)
        if not loud and level < self.floor * 1.5:
            self.floor = 0.95 * self.floor + 0.05 * level
        if loud:
            self.heard = True
            self.quiet_for = 0.0
            return None
        if self.heard:
            self.quiet_for += self.chunk_s
            return "silence" if self.quiet_for >= self.silence_s else None
        return "nospeech" if self.t - self.guard_s >= self.wait_s else None


# ---- doing it -----------------------------------------------------------------------------

def _ago(secs):
    secs = int(secs)
    return f"{secs} seconds" if secs < 90 else f"{secs // 60} minutes"


def execute(it, ctx):
    """Run one allowed intent. Returns the outcome dict reply_text() reads.
    Everything goes through the module function the API calls, so a voice
    command meets exactly the validation a tap does."""
    k, a = it["intent"], it["args"]
    imperial = ctx.get("imperial", True)
    try:
        if k.startswith("fridge."):
            import fridge
            pub = fridge.read_public()
            if not pub.get("present"):
                return {"ok": False, "why": "No fridge has connected yet."}
            z = (pub.get("zones") or [{}])[0]
            if k == "fridge.query":
                if z.get("temp") is None:
                    return {"ok": False, "why": "The fridge isn't reporting a temperature."}
                return {"ok": True, "temp": z["temp"], "set": z.get("set"),
                        "power": pub.get("power"),
                        "stale": None if pub.get("connected") else _ago(pub.get("age") or 0)}
            if not pub.get("connected"):
                return {"ok": False, "why": "The fridge isn't connected right now."}
            if k == "fridge.set":
                r = fridge.ask("z0_set", a["c"])
                return {"ok": True, "c": r["value"]}
            r = fridge.ask("power", a["value"])
            return {"ok": True}
        if k == "radar.mute":
            import v1
            pub = v1.read_public()
            if not pub.get("present") or not pub.get("connected"):
                return {"ok": False, "why": "No radar detector is connected."}
            v1.ask("mute", a["value"])
            return {"ok": True}
        if k in ("eq.power", "eq.preset"):
            import eq
            if k == "eq.preset":
                names = list(eq.BUILTIN_PRESETS) + list(eq.load()["user_presets"])
                match = next((n for n in names if n.lower() == a["preset"].lower()), None)
                if not match:
                    return {"ok": False, "why": f"I don't have a preset called {a['preset']}."}
                st = eq.set_preset(match)
                st["enabled"] = True
            else:
                st = eq.load()
                st["enabled"] = bool(a["value"])
                match = None
            res = eq.write_state(st).get("result") or {}
            if res.get("ok") is False:
                return {"ok": False, "why": "Saved, but the equalizer can't run here: "
                        + str(res.get("why") or "PipeWire isn't available.")}
            return {"ok": True, "preset": match}
        if k == "screen":
            import screen
            known = screen.known()
            ids = {v["id"]: v for v in known}
            view = a.get("view")
            if view not in ids:
                lab = {v["label"].lower(): v for v in known}
                hit = lab.get(a["name"]) or next(
                    (v for v in known if v["id"] == a["name"]), None)
                if hit:
                    view = hit["id"]
                elif known:
                    return {"ok": False}
            if not view:
                return {"ok": False}
            screen.ask(view, "voice")
            return {"ok": True, "label": (ids.get(view) or {}).get("label", a["name"]).lower()}
        if k in ("clip.save", "clip.mark"):
            import camstore
            import camroutes
            camstore.mark("saved" if k == "clip.save" else "marked", speed_kph=camroutes._speed())
            return {"ok": True}
        if k == "battery":
            import records
            live = records.live()
            if not live.get("connected"):
                return {"ok": False, "why": "I can't read the car right now."}
            vals = live.get("values") or {}
            key = "CONTROL_MODULE_VOLTAGE" if a["which"] == "12v" else "HYBRID_BATTERY_REMAINING"
            v = vals.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return {"ok": False, "why": "The car isn't reporting that battery."}
            return {"ok": True, "volts": float(v), "percent": float(v),
                    "sim": bool(live.get("simulated"))}
        if k == "env":
            import envnode
            pub = envnode.read_public()
            if not pub.get("present"):
                return {"ok": False, "why": "There are no air sensors connected."}
            side = pub["outside"] if a["side"] == "outside" else pub["cabin"]
            if side.get("state") != "live":
                return {"ok": False, "why": "The sensor isn't reporting right now."}
            v = side.get("values") or {}
            band = pub["cabin"].get("co2_band") if a["side"] != "outside" else None
            return {"ok": True, "t_c": v.get("t_c"), "rh": v.get("rh"), "co2": v.get("co2"),
                    "co2_label": (band or {}).get("label"), "pm25": v.get("pm25"),
                    "source": pub.get("source")}
    except (ValueError, TypeError) as e:
        return {"ok": False, "why": f"I couldn't do that: {e}"}
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "why": "That isn't available on this machine."}
    return {"ok": False}


def handle(text, ctx=None):
    """Sentence in, {intent, outcome, reply} out. The only place a transcript
    becomes an action: parse, gate, execute, word the answer."""
    ctx = dict(ctx or {})
    imperial = ctx.get("imperial", True)
    presets = ctx.get("presets") or ()
    it = grammar.parse(text, imperial=imperial, presets=presets)
    if it["intent"] == "unknown":
        return {"intent": it, "outcome": {}, "reply": grammar.SORRY}
    why = grammar.gate(it, bool(ctx.get("moving")))
    if why:
        out = {"refused": why}
    else:
        out = (ctx.get("execute") or execute)(it, ctx)
        if out.get("ok") is False and "why" not in out and it["intent"] == "screen":
            out["why"] = f"I don't have a screen called {it['args']['name']}."
    reply = grammar.reply_text(it, out, imperial)
    if out.get("ok") and out.get("sim") and reply:
        reply += " That's a simulated reading."
    return {"intent": it, "outcome": out, "reply": reply}


def world():
    """What the router needs to know about the car right now."""
    imperial = True
    try:
        import records
        imperial = records.units_for()["system"] == "imperial"
    except Exception:                                            # noqa: BLE001
        pass
    presets = ()
    try:
        import eq
        presets = list(eq.load()["user_presets"])
    except Exception:                                            # noqa: BLE001
        pass
    return {"imperial": imperial, "moving": car_is_moving(), "presets": presets}


# ---- speaking -----------------------------------------------------------------------------

def speak(text, cfg, run=subprocess.run, which=shutil.which, exists=os.path.exists):
    """Piper (its own program) makes a WAV; PipeWire plays it. True if it spoke."""
    ok, _ = piper_ready(cfg, which=which, exists=exists)
    if not (ok and cfg.get("speak") and text):
        return False
    player = next((p for p in (["pw-play"], ["paplay"], ["aplay", "-q"]) if which(p[0])), None)
    if not player:
        return False
    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="omacar-voice-")
    os.close(fd)
    try:
        r = run(["piper", "-m", os.path.expanduser(cfg["piper_voice"]), "-f", wav],
                input=text, text=True, capture_output=True, timeout=30, check=False)
        if r.returncode != 0:
            return False
        run(player + [wav], timeout=30, capture_output=True, check=False,
            stdin=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass


def deliver(text, source, cfg, ctx=None, now=None, speaker=speak):
    """Run a sentence and publish what happened. Quiet hours hold the voice:
    in them (car off) the reply is shown and never spoken."""
    res = handle(text, ctx if ctx is not None else world())
    reply = res["reply"]
    quiet = not grammar.may_listen(cfg, minute_now(now), car_is_on())
    spoke = False if quiet else speaker(reply, cfg)
    st = _read_state() or {}
    write_state(phase="replied", source=source, transcript=text, reply=reply,
                spoke=spoke, intent=res["intent"]["intent"], seq=int(st.get("seq", 0)) + 1)
    return res, spoke


# ---- the microphone and the models --------------------------------------------------------

class Mic:
    def __init__(self):
        argv = mic_cmd()
        if not argv:
            raise RuntimeError("no parec or pw-record: install libpulse / pipewire")
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL)

    def read(self):
        data = b""
        while len(data) < CHUNK * 2:
            more = self.proc.stdout.read(CHUNK * 2 - len(data))
            if not more:
                return None
            data += more
        return data

    def close(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass


class Wake:
    def __init__(self, phrase, threshold):
        from openwakeword.model import Model
        path = custom_models().get(phrase)
        self.model = Model(wakeword_models=[path or phrase], inference_framework="onnx")
        self.threshold = threshold

    def hit(self, chunk):
        import numpy as np
        scores = self.model.predict(np.frombuffer(chunk, dtype=np.int16))
        return max(scores.values()) >= self.threshold if scores else False

    def reset(self):
        if hasattr(self.model, "reset"):
            self.model.reset()


class Ears:
    def __init__(self):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def text(self, pcm):
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1,
                                        initial_prompt=PROMPT, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()


def record_command(mic, on_level=None, end=None):
    """Everything said after the wake phrase, until it stops. Returns PCM bytes."""
    end = end or Endpointer()
    buf, why = [], None
    while why is None:
        chunk = mic.read()
        if chunk is None:
            break
        buf.append(chunk)
        why = end.feed(rms(chunk))
    return b"".join(buf), (why if end.heard else "nospeech")


def run():
    if not (have_module("openwakeword") and have_module("faster_whisper")):
        write_state(phase="error", error="not installed: omacar voice setup")
        print("  voice: openwakeword/faster-whisper missing: omacar voice setup", file=sys.stderr)
        return 1
    mic = wake = ears = None
    wake_for = None
    last_beat = 0.0
    seq_error = 5
    while True:
        cfg = load_config()
        car_on = car_is_on()
        if not cfg["enabled"]:
            mic and mic.close()
            mic = None
            write_state(phase="off", source="mic", error=None)
            time.sleep(2)
            continue
        if not grammar.may_listen(cfg, minute_now(), car_on):
            mic and mic.close()
            mic = None
            write_state(phase="quiet", source="mic", error=None)
            time.sleep(10)
            continue
        try:
            if wake is None or wake_for != (cfg["phrase"], cfg["threshold"]):
                wake, wake_for = Wake(cfg["phrase"], cfg["threshold"]), (cfg["phrase"], cfg["threshold"])
            if mic is None:
                mic = Mic()
        except Exception as e:                                   # noqa: BLE001
            write_state(phase="error", source="mic", error=f"{type(e).__name__}: {e}")
            time.sleep(seq_error)
            seq_error = min(60, seq_error * 2)
            continue
        seq_error = 5
        chunk = mic.read()
        if chunk is None:
            mic.close()
            mic = None
            write_state(phase="error", source="mic", error="the microphone stream ended")
            time.sleep(3)
            continue
        if time.time() - last_beat > 1.0:
            write_state(phase="listening", source="mic", error=None)
            last_beat = time.time()
        if not wake.hit(chunk):
            continue
        write_state(phase="heard", source="mic", transcript="", reply="")
        pcm, why = record_command(mic)
        text = ""
        if why != "nospeech" and pcm:
            write_state(phase="working")
            try:
                ears = ears or Ears()
                text = ears.text(pcm)
            except Exception as e:                               # noqa: BLE001
                write_state(phase="error", error=f"transcription: {type(e).__name__}: {e}")
                ears = None
                time.sleep(3)
                continue
        write_state(transcript=text)
        deliver(text, "mic", cfg)
        wake.reset()
        mic.close()                 # drop what the speaker said into the microphone
        mic = None
        time.sleep(1.0)
        last_beat = 0.0


# ---- the command line ---------------------------------------------------------------------

def sim():
    steps = [("listening", "", ""), ("heard", "", ""), ("heard", "fridge to thirty", ""),
             ("working", "fridge to thirty four", ""),
             ("replied", "fridge to thirty four", "Fridge set to 34 degrees.")]
    n = 0
    while True:
        for phase, transcript, reply in steps:
            n += 1 if phase == "replied" else 0
            write_state(phase=phase, source="sim", transcript=transcript, reply=reply,
                        spoke=False, seq=n, error=None)
            time.sleep(1.6 if phase != "listening" else 3)


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what in ("on", "off"):
        save_config({"enabled": what == "on"})
        print(f"  voice {what}. {'Start it: systemctl --user enable --now omacar-voice.service' if what == 'on' else ''}")
        return 0
    if what == "phrase" and len(argv) > 1:
        try:
            save_config({"phrase": argv[1]})
        except ValueError as e:
            print(f"  omacar voice: {e}", file=sys.stderr)
            return 1
        return 0
    if what == "quiet":
        try:
            if len(argv) == 2 and argv[1] == "off":
                save_config({"quiet": {"on": False}})
            elif len(argv) == 3:
                save_config({"quiet": {"on": True, "start": argv[1], "end": argv[2]}})
            else:
                print("  usage: omacar voice quiet 21:00 07:00 | off", file=sys.stderr)
                return 2
        except ValueError as e:
            print(f"  omacar voice: {e}", file=sys.stderr)
            return 1
        return 0
    if what == "voice" and len(argv) > 1:
        cfg = load_config()
        cfg["piper_voice"] = argv[1]
        os.makedirs(CONF_DIR, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return 0
    if what == "models":
        for p in available_phrases():
            print(f"  {p}{'  (yours)' if p in custom_models() else ''}")
        return 0
    if what == "mic":
        mic = Mic()
        peak, lvls = 0, []
        t0 = time.time()
        while time.time() - t0 < 3:
            c = mic.read()
            if not c:
                break
            lvls.append(rms(c))
        mic.close()
        print(f"  mic command  {' '.join(mic_cmd())}")
        print(f"  {len(lvls)} chunks, rms mean {sum(lvls) / max(1, len(lvls)):.0f}, "
              f"max {max(lvls or [0]):.0f} (speech is usually above 1000; a dead mic reads 0)")
        return 0 if lvls else 1
    if what == "say" and len(argv) > 1:
        cfg = load_config()
        res, spoke = deliver(" ".join(argv[1:]), "typed", cfg)
        print(f"  {res['intent']['intent']}: {res['reply']}{'' if spoke else '  (not spoken)'}")
        return 0
    if what == "sim":
        try:
            sim()
        except KeyboardInterrupt:
            return 0
    if what == "run":
        return run()
    print(f"  omacar voice: no such thing as '{what}'.\n"
          "  status | on | off | phrase | quiet | voice | models | mic | say | sim | run | setup",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
