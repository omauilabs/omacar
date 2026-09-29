# Cameras and drowsy mode

Step 2 of the OmaCar reimagining, pulled forward for the demo drive on Wednesday 2026-09-30 (Marina → Los Banos → San Francisco, ending at the Omarchy meetup). It builds on the foundation branch (`redesign/foundation`). Owner decisions of 2026-09-28 are recorded inline.

## Problem

The owner drives about 1,000 miles a week, often alone and often at night, and sometimes gets drowsy. He wants:
- three wired cameras recorded on the tablet: an Insta360 Ace Pro on the rear, a DJI Osmo Action 4 on the front, and a Logitech webcam on the tablet watching him;
- the cabin camera watching his eyes, waking him with alerts that ramp up and down rather than startle him, because a sudden blast next to someone nodding off can cause the jerk of the wheel it is meant to prevent.

Today OmaCar has no camera support at all: the Cameras tab is a placeholder. Sound reaches the car through an AUX cable from the Surface's headphone jack into the car's existing wiring (owner, 2026-09-28).

## Shape

- A recorder service, `omacar-cams`, owns every camera:
  - it records front and rear (and the cabin at low resolution) in one-minute clips that loop;
  - it locks the clips around a hard stop;
  - it publishes a small live picture per camera for the app.
- The Cameras tab follows mockup 6: a main feed, two small feeds, an event timeline, playback, Save clip and Mark event, and storage and loop settings.
- Home's Dashcams card becomes a live front view with the REC state.
- Drowsy mode runs in the app, the same page as everything else:
  - it reads the cabin camera's live picture through MediaPipe's face tracker, self-hosted;
  - it measures eye closure, PERCLOS, yawns and nods;
  - it adds two signals that need no camera: time since the last stop, and night hours;
  - it escalates through three ramped alert levels.
- One audio output stage for everything OmaCar plays, so alerts always have headroom over music, whatever the car's volume knob says.

## Rejected approaches

- **Detection in a Python service instead of the page.** More robust in principle: it keeps running if the page dies. But MediaPipe and ONNX wheels for the tablet's Python 3.14.7 are unverified, and the recorder would have to share frames with a second process. For Wednesday, the page is the fast, GPU-backed route. Moving it to a service is the first follow-up.
- **Opening the cabin camera twice (recorder plus browser).** A V4L2 device has one owner. The recorder owns it; the page reads the recorder's live picture.
- **Raising the music as the main alert.** Loud music masks the alert that follows it. Rising music is kept as the gentle first-level stimulus (the owner's idea, supported by a 2026 study on gradually increasing volume); at Level 2 and above, music ducks and the alert takes over.
- **Instant full-volume alarms.** They cause startle and swerve. Every alert has a shaped rise (Patterson's aviation warning guidance; a 2023 driving study found an abrupt 105 dB sound measurably affected drivers' reactions).
- **A recording of a real dog.** The owner has none. The bark is synthesized in the app for now; better sounds are a later exploration (owner, 2026-09-28).

## Design

### Cameras

**Devices and roles.**
- `lib/cams.py` finds cameras by their `/dev/v4l/by-id` names and gives each a role. They are never matched by `/dev/videoN` number: the tablet's IPU6 driver claims `video0`–`video63`, so USB cameras land at `video64` and up, in any order.
- Default patterns, overridable in `~/.config/omarchy/omacar-cameras.json`:

  | Role | Pattern |
  |---|---|
  | front | `Osmo Action 4\|DJI` |
  | rear | `Insta360\|Ace` |
  | cabin | `C920\|Logitech\|ELP` |

- An infrared cabin camera (recommended for night: an ELP 1080p with IR LEDs and an IR-cut filter, 3.6 mm lens, not fisheye) drops in as `cabin` with no code change.
- Each camera's modes come from `v4l2-ctl --list-formats-ext`. The recorder picks the best mode at or under 1080p30, preferring MJPEG, then H.264, then YUYV.
- The action cameras' webcam modes are documented only for Windows and macOS. What they actually offer on Linux, and how long they run on USB power, is measured on the tablet on Tuesday evening, not assumed.

**One ffmpeg per camera.** The input is decoded once and split two ways:
- **Recording.**
  - Compressed on the GPU: `hwupload` → `h264_vaapi`, measured working on this tablet at 4–10% of one core for 640×480.
  - Bitrate: 6 Mbit/s front and rear, 1 Mbit/s cabin at 640×480.
  - `-fps_mode passthrough` so a camera that slows in the dark does not pad with duplicates. On 2026-09-27 a dim room gave 10 real fps, and 407 of 600 frames were padding.
  - Keyframes forced at each clip boundary, so every clip starts on one.
  - One-minute fragmented-MP4 clips in `~/Videos/OmaCar/<role>/YYYYmmdd-HHMMSS.mp4`, which Chromium plays directly.
- **Live picture.** Scaled to 640 px wide, 10 fps, JPEG. `lib/cams.py` writes the latest frame atomically to `$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg`. Files, not sockets, as the daemon and live.json already work.

**Storage.**
- Loop recording keeps the total under a budget: default 40 GB of the tablet's 93 GB free.
- The oldest unlocked clips go first.
- Locked clips are moved to `~/Videos/OmaCar/locked/<event-id>/` and never deleted by the loop.

**Events.**
- **Hard braking** comes from the car's own speed in live.json: a drop of 16 km/h or more within one second, about 0.45 g, locks the clips covering 30 seconds before to 30 seconds after, on every recording camera.
- **Mark event** and **Save clip** from the app lock the same window around the tap.
- Events go to `~/Videos/OmaCar/events.json` with time, kind, speed and the locked files.

**Server routes** (in `lib/api.py`, backed by `lib/cams.py`):

| Route | Returns |
|---|---|
| `GET /api/cams` | Per role: device, mode, recording, real fps, clip count, storage used and budget, last error |
| `GET /api/cams/<role>/live` | `multipart/x-mixed-replace` MJPEG from the latest-frame file at 10 fps (an `<img>` shows it with no decoder) |
| `GET /api/cams/clips?role=&from=&to=` | The clip list for the timeline |
| `GET /api/cams/clip/<role>/<file>` | The clip itself, with HTTP Range support, which `serve.py` lacks today and video seeking needs |
| `POST /api/cams/mark` | Mark an event |
| `POST /api/cams/lock` | Lock a clip window (Save clip) |

**Service and simulation.**
- `omacar-cams.service`, a user unit: `Restart=always`, no start limit (the lesson of the drive-day fixes), `Nice=10`.
- `omacar cams status|on|off|sim`.
- `sim` stands up `lavfi testsrc2` inputs for roles with no camera. Anything from `sim` is labelled SIMULATED in the app, the same rule as car data.

**The Cameras tab** (`share/js/views/cameras.js`, replacing the placeholder), following mockup 6:
- A main feed and two small feeds (tap one to swap it into the main slot), each with its role, resolution, REC state and clock.
- An event timeline for the last hour: clip ticks and event markers, where tapping a point plays that moment.
- Playback: back 10 s, play and pause, forward 10 s.
- Buttons: Save clip and Mark event. Mute is shown disabled with "No audio recorded", because the recorder takes no audio on Wednesday.
- Camera selection.
- Storage (used of budget), Loop recording (on), and Parking watch, shown off and "coming later".
- The source badge reads `LIVE · 3 cameras`, or `SIMULATED` when any role comes from `sim`.

**Home's Dashcams card** shows the front live picture with a REC dot when the recorder is running, and says why when it is not: no camera, recorder off, or simulated.

### Drowsy mode

**Pipeline.**
- The page draws the cabin live picture into a canvas at 10–15 fps.
- It runs `@mediapipe/tasks-vision` 1.0.1 Face Landmarker (Apache-2.0) in `VIDEO` mode, with blendshapes and the facial transformation matrix switched on.
- It is self-hosted in `share/js/vendor/mediapipe/`: `vision_bundle.mjs` (152 KB), the `wasm/` engine files (about 11.5 MB), and `face_landmarker.task` (3.6 MB). Nothing is fetched on the road.
- The owner approved the download on 2026-09-28.

**Measures,** each computed in a pure module (`share/js/drowsy.js`) from timestamped frames so it can be unit-tested:

| Measure | How it is computed |
|---|---|
| Eye closure | The mean of `eyeBlinkLeft` and `eyeBlinkRight`. The first 60 s above 30 mph set the driver's open-eye baseline; "closed" is above the baseline + 0.35, capped at 0.8. |
| Closure duration | How long the eyes have been continuously closed. |
| PERCLOS | The share of frames in the last 60 s with eyes closed (P80 sense). |
| Yawn | `jawOpen` > 0.6 held for 1.5 s or more. |
| Nod | Head pitch more than 15° below baseline for 0.5 s or more, then recovering. |
| Face lost | No face for more than 5 s: status "Can't see you". It never alerts on its own. |

**Camera-free signals.**
- Time since the last stop: speed 0 for 5 minutes or more counts as a stop, read from live.json.
- Night hours: 02:00–06:00.
- These raise Level 1 only. They never escalate on their own.

**Active only above 30 mph** (Euro NCAP tests from 50 km/h), and only while the car is connected and moving. Never while parked.

**The ladder.** Thresholds live in `~/.config/omarchy/omacar-drowsy.json`, with these defaults:

| Level | Trigger | What happens |
|---|---|---|
| 1 · Notice | PERCLOS ≥ 15%, or 3 yawns in 5 min, or 3 nods in 5 min, or 2 h since a stop, or night hours (once an hour) | A soft two-note chime. The voice says "James, you seem tired. Plan a break soon." The radio rises +6 dB over 10 s, then settles back over 30 s. A card appears on screen. |
| 2 · Wake | Eyes closed ≥ 1.0 s, or PERCLOS ≥ 25% | Music ducks −12 dB over 0.5 s. An alert rises over 1.5 s to its target, rotating between the synthesized bark, the voice ("James, are you with me?") and a two-tone alarm (500–1500 Hz, per ISO 7731's range). A full-screen card appears with a large "I'm awake" button. |
| 3 · Pull over | Eyes closed ≥ 2.0 s, or two Level 2 alerts within 5 min | A continuous alarm rises to full over 3 s. The voice says "Pull over now." A full-screen card stays up. |

**Release and follow-up.**
- A level clears when the driver taps "I'm awake", or their eyes stay open for 5 s with PERCLOS falling.
- Sound fades out over 3 s.
- After Level 3, a "Stop at the next safe place" banner stays until the car has been stopped for 2 minutes.
- Every event is logged to the records book (`kind=drowsy`) with time, level, trigger, speed and the measures. Vehicle and the report can show them later.

**Audio.**
- **One shared output stage** (`share/js/audiobus.js`). Every source OmaCar plays joins it: the radio's `<audio>`, Music, and the chime, bark, alarm and voice clips. Music sits 12 dB below full scale; alerts may use the full 0 dBFS. So an alert always has 12 dB of headroom over music, whatever the car's knob is set to.
- **The Surface's own volume is pinned** to 100% on the headphone (AUX) port by `lib/audio.py` (`wpctl`), exposed at `GET /api/audio` and applied at app start.
  - Today it sits at 25% on the internal speakers, which would make every alert quiet.
  - If the jack is unplugged, the active port becomes the speakers: Home shows "AUX disconnected", and alerts still play there at full volume.
- **The car radio must stay on AUX.** The app cannot see the car's radio source. Begin and drowsy mode's settings say so, and Begin plays a short chime so the driver hears that the path works.
- **Ramps** are `linearRampToValueAtTime` on gain nodes: no step is ever audible. The shapes are pure functions (`rampPlan(level, from, to)`) and are unit-tested.
- **The sounds:**
  - **Chime and alarm:** synthesized with oscillators.
  - **Bark:** synthesized from a pitched harmonic burst with a fast downward sweep, band-passed noise and a short formant envelope, two barks per call. It is honest about being synthesized.
  - **Voice:** Piper TTS rendered ahead of time. The phrases are pre-rendered once on the box with Piper and a permissively licensed US English voice, into `share/assets/private/voice/*.ogg`: private because they carry the owner's name. They are listed in the assets manifest. The tablet has no speech engine installed, so nothing depends on one in the car.

**Screens.**
- **Home and the top bar** carry a small drowsy status chip: `Watching`, `Can't see you`, `Paused · stopped`, `Paused · no car data`, `Stopped · face tracker error`, `Off`. (Amended by the controller, 2026-09-29: "no car data" for a dropped link or an unreadable speed; "face tracker error" when the tracker has stopped after repeated failures; and "stopped", not "parked", because drowsy mode cannot know Park and a car at a red light is not parked.)
- **Settings → Drowsy mode:**
  - on or off;
  - sensitivity (Standard, or Sensitive with thresholds −20%);
  - the name used by the voice;
  - which sounds rotate;
  - "Test the alerts", available only while parked.
- **The copy says plainly** that alerts buy minutes, and that stopping to rest (a 20-minute nap or caffeine) is the fix (NHTSA, AAA Foundation).

## Testing

- **JS units, in the browser harness:**
  - the closure baseline and "closed" decision;
  - closure duration;
  - the PERCLOS window;
  - yawn and nod detectors on synthetic series;
  - the level state machine with injected clocks (triggers, rotation, release, the Level 3 banner);
  - `rampPlan` shapes (no step larger than 3 dB per 100 ms at any level);
  - the audiobus headroom arithmetic.
- **Python:**
  - role matching from by-id names;
  - mode choice from `v4l2-ctl` output fixtures;
  - the clip janitor against a budget;
  - hard-braking detection on speed series;
  - Range responses.
- **On the box:** the C920 plus two `sim` roles, end to end: recording, live, playback, locking.
- **On the tablet, Tuesday evening:** the DJI, the Insta360 and the C920 plugged in; the measured modes recorded in `doc/cameras.md`.
- **On the drive, Wednesday:** drowsy mode logs its measures. During the Los Banos office session, thresholds are checked against the recorded cabin clips.

## Rollout

1. Branch `redesign/cameras`, off `redesign/foundation`. It is merged back after the foundation plan completes.
2. **Tuesday, during the data drives:** build and test the recorder, the tab and drowsy mode on the box.
3. **Tuesday evening:** real cameras on the tablet, then the demo build installed.
4. **Wednesday:** the demo drive, then tuning in the Los Banos office working directly on the tablet, then the meetup.

## Open questions

1. What the DJI and Insta360 offer as webcams on Linux: formats, how long they run on USB power, and heat in a hot car. Measured Tuesday evening.
2. Whether the recorder can run all three cameras on one powered USB 3 hub without dropped frames. Measured Tuesday evening.
3. Night driving needs an infrared cabin camera. The recommendation stands (ELP with IR LEDs, 3.6 mm lens); the purchase is the owner's.
4. Better alert sounds and other ways to get the driver's attention are for another day (owner, 2026-09-28).
