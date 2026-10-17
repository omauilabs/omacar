# Voice commands ("Hey Jarvis")

Say a wake phrase, then a command; the car answers aloud (Piper) or with a
note. Everything runs on the tablet. It ships **off**: it opens a microphone.

> **Status.** Written and unit-tested on a Mac with stand-ins. **Nothing has
> been run on the tablet**: no microphone read, no wake word heard, no
> transcription, no Piper. See "Verified" and "Unverified" below.

## Install (on the tablet)

```
omacar voice setup                       # pip: openwakeword, faster-whisper; downloads their models (needs network ONCE)
omacar voice mic                         # record 3 s: is the microphone heard?
omacar voice on
systemctl --user enable --now omacar-voice.service
omacar voice say "how's the cabin air"   # the router and the reply, no microphone
```

Settings -> **Voice** has on/off, the wake phrase, quiet hours and spoken
replies. They are greyed while the car is moving. The top bar shows a
**Voice** readout (Listening, Hearing, Thinking, a reply); it says
"Simulated" or "Typed" when the sentence did not come from the microphone.

## The pipeline

| Stage | What | Notes |
|---|---|---|
| Microphone | `parec` (libpulse; same package as the `pactl` lib/audio.py already needs), else `pw-record`, 16 kHz mono s16 | PipeWire's pulse layer. Closed in quiet hours. |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) 0.6.0, ONNX | Code Apache-2.0. Frames of 1280 samples (80 ms). |
| Command | recorded until about 1 s of quiet, 6 s at most | energy endpointer in lib/voice.py (`Endpointer`) |
| Speech to text | faster-whisper `tiny.en`, int8, CPU | Nothing local existed in the repo or its docs (voxtype is only mentioned in `voice/talk.qml`, which types into a window), so this is the venv's own. |
| Meaning | lib/intent.py, a grammar, no ML | pure, tested |
| Doing | the same functions the API calls | `fridge.ask`, `v1.ask`, `eq.write_state`, `screen.ask`, `camstore.mark` |
| Reply | Piper if `piper` is on PATH and a voice is set, else a toast | Piper is a separate program, **never bundled** (GPL-3.0) |

`lib/workrelay.py` ("Work by voice") does not exist in this tree, so nothing
here hands work to it.

## Licences, and what the owner should know

- openWakeWord's **code** is Apache-2.0. Its **pretrained models are CC
  BY-NC-SA 4.0** (non-commercial, share-alike), because of training-data
  licences. Fine for one owner's own car; not for something sold. A model you
  train yourself is yours to licence.
- The pretrained phrases are `alexa`, `hey_mycroft`, `hey_jarvis` (the
  default) and `hey_rhasspy`. There is no pretrained "Hey OmaCar".
  (Source: the project README and `setup.py` at github.com/dscripka/openWakeWord, read 2026-10-17.)
- faster-whisper is MIT; `tiny.en` weights are OpenAI's, MIT.

## A custom "Hey OmaCar"

Train one with the project's Colab notebook (synthetic speech from a TTS, a
few thousand negatives; under an hour), then drop the `.onnx` (or `.tflite`)
file in `~/.config/omacar/voice/models/` as `hey_omacar.onnx`.
`omacar voice phrase hey_omacar` (or the Settings row) selects it. The file
name is the phrase. Not done here: it needs the training run.

## What it understands

Whole sentences only; anything else gets "Sorry, I didn't catch that." and
changes nothing. Numbers may be digits or words ("thirty four", "minus five").

- **Fridge**: "fridge to 34", "set the fridge to 38 degrees", "fridge off/on",
  "how cold is the fridge". Spoken temperatures are the owner's unit (imperial
  by default, like `U` in core.js); "celsius" / "fahrenheit" after the number
  overrides. Outside -22..10 C it says the limits and sends nothing.
- **Radar**: "mute the radar", "unmute the radar". Never settings.
- **Air**: "how's the cabin air", "what's the temperature outside", "what's the CO2".
- **Sound**: "bass preset", "car cabin preset", "equalizer off", "set the
  equalizer to night" (built-in and your own presets).
- **Screens**: "show the fridge", "open cameras", "take me to the map"
  (asked through `/api/screen`; the app names who asked).
- **Clips**: "save that" (the 30 s either side, `camstore.mark("saved")`),
  "mark that".
- **Battery**: "what's my battery" (hybrid pack), "twelve volt battery".

Refusals, by design: "fridge on 34", "fridge off 34", two numbers, a lone
"mute", "make the fridge colder". **The fridge set point is parked-only**, as in
the app (`fridgecard.js`); asked while moving it is refused and the reason is
spoken. Power, mute, screens and clips are one-tap actions and stay available.
Moving is read as the app reads it (`records.status == "driving"` or a hand-off).
A fridge that is not connected is not pretended to: nothing is queued.

## Quiet hours

21:00 to 07:00 by default (Settings, or `omacar voice quiet 22:00 06:30`,
`... quiet off`). In them the microphone is **not opened** and nothing is
spoken, **unless the car is on** (charger present per `lib/ignition.py`, or the
adapter answering). A reply that arrives after the car turns off is shown, not spoken.

## Verified (test/voice_test.py: 189 checks; test/js/voice.test.js: 17)

- the grammar: 100+ phrasings, refusals, units, number words
- parked-only gating, the fridge range pinned to `cfx.py`
- the reply wording, quiet-hour boundaries (21:00 in, 07:00 out, midnight wrap)
- config validation, custom-model discovery, GET/POST `/api/voice`
- the endpointer on synthetic levels; the mic and Piper command lines against stand-ins
- commands acting through the real `fridge.ask` / `v1.ask` / `screen.ask` into scratch state
- the top-bar readout and Settings sheet, drawn in a browser from simulated and typed states

## Unverified (not run anywhere real)

- that `parec` / `pw-record` read the Surface's microphone, and the 16 kHz format
- openWakeWord running (`omacar voice setup` was never run), its score threshold of 0.5 on a real cabin, false wakes from music on the AUX output (no echo cancellation: music plays into the microphone)
- faster-whisper tiny.en accuracy over road noise; CPU time on the i5 (expect a second or two)
- Piper: argv follows `tools/demo_voice.py` (`-m`, `-f`); playback with `pw-play`
- `omacar voice setup` pip resolution on the tablet's Python 3.14 (openwakeword 0.6.0 needs `ai-edge-litert`, `speexdsp-ns`, `onnxruntime`; wheels for 3.14 are not confirmed)
- `camstore.mark` and `/api/cams/lock` from voice with real cameras
