# Cameras and drowsy mode implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the front, rear and cabin cameras on the tablet, show them in the Cameras tab and on Home, and watch the driver's eyes with alerts that ramp up and down, as specified in `doc/design/2026-09-28-cameras-drowsy.md`. It must be buildable by Tuesday evening, 2026-09-29, for the drive on Wednesday.

**Architecture:**
- **A recorder service** owns every camera. `lib/cams.py` runs `omacar-cams.service`: one ffmpeg per camera, recording one-minute clips on the GPU and writing a 10 fps JPEG live picture to a file. `lib/camstore.py` holds the clips, the loop, the locks and the events.
- **The server reads what the recorder writes.** The JSON routes are in `lib/camroutes.py`, reached through one block at the end of `lib/api.py`. The two streaming routes are in `lib/serve.py`.
- **Drowsy mode runs in the page.** The cabin's live picture goes through MediaPipe's Face Landmarker (vendored), into pure measures (`drowsy.js`) and a pure ladder (`ladder.js`).
- **Everything the app plays goes through one output stage** (`audiobus.js`), with pure ramp plans (`ramps.js`).
- **New files, not edits.** The foundation plan is still running on `redesign/foundation`. This branch touches each file that plan also edits at exactly one delimited insertion point (see "Merging with the foundation branch").

**Tech stack:** vanilla JS (ES modules), Python 3 stdlib, ffmpeg with VA-API (`h264_vaapi`), `v4l2-ctl`, PipeWire (`wpctl`, `pactl`), MediaPipe tasks-vision 1.0.1 (vendored), Web Audio, headless Chromium for tests. Piper TTS runs once, off the tablet, to render the voice clips.

## Global constraints

Every task's requirements include this section. Values are the spec's, verbatim where the spec gives them.

- **No build step.** ES modules load from disk, and Python is stdlib only. The tablet runs nothing else new. Two tools run once, off the tablet: curl (to vendor MediaPipe) and Piper (to render the voice).
- **Nothing is fetched on the road.** `@mediapipe/tasks-vision` 1.0.1 (Apache-2.0) and `face_landmarker.task` live in `share/js/vendor/mediapipe/`.
- **Camera roles and default patterns.** Overridable in `~/.config/omarchy/omacar-cameras.json`. Matched on `/dev/v4l/by-id` names, never on `/dev/videoN` (the IPU6 driver claims `video0`–`video63`).

  | Role | Pattern |
  |---|---|
  | front | `Osmo Action 4\|DJI` |
  | rear | `Insta360\|Ace` |
  | cabin | `C920\|Logitech\|ELP` |

- **Mode.** The recorder takes the best mode at or under 1080p30, preferring MJPEG, then H.264, then YUYV, from `v4l2-ctl --list-formats-ext`. The cabin records at low resolution (640×480).
- **Recording.**
  - Encoding: `-vaapi_device /dev/dri/renderD128`, `format=nv12,hwupload`, `h264_vaapi`.
  - Bitrate: 6 Mbit/s front and rear, 1 Mbit/s cabin.
  - `-fps_mode passthrough`, and keyframes forced at every clip boundary.
  - Clips: one-minute fragmented MP4 in `~/Videos/OmaCar/<role>/YYYYmmdd-HHMMSS.mp4`.
- **Live picture.** 640 px wide, 10 fps, JPEG. Written atomically to `$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg`.
- **Storage.**
  - The budget defaults to 40 GB.
  - The oldest unlocked clips go first.
  - Locked clips are moved to `~/Videos/OmaCar/locked/<event-id>/` and are never deleted by the loop.
- **Events.**
  - Hard braking is a drop of 16 km/h or more within one second (about 0.45 g), in `values.SPEED` (km/h) from live.json.
  - A hard stop locks the clips covering 30 s before to 30 s after, on every recording camera. Mark event and Save clip lock the same window.
  - Events are written to `~/Videos/OmaCar/events.json` with time, kind, speed and the locked files.
- **Routes:**

  | Route | Returns | Served by |
  |---|---|---|
  | `GET /api/cams` | per role: device, mode, recording, real fps, clip count, storage used and budget, last error | `lib/camroutes.py` |
  | `GET /api/cams/<role>/live` | `multipart/x-mixed-replace` MJPEG from the latest-frame file at 10 fps | `lib/serve.py` |
  | `GET /api/cams/clips?role=&from=&to=` | the clip list (and events) for the timeline | `lib/camroutes.py` |
  | `GET /api/cams/clip/<role>/<file>` | the clip, with HTTP Range | `lib/serve.py` |
  | `POST /api/cams/mark` | Mark event | `lib/camroutes.py` |
  | `POST /api/cams/lock` | Save clip | `lib/camroutes.py` |
  | `GET /api/audio`, `POST /api/audio` | the audio path; `{"action": "apply"}` holds 100% where enabled | `lib/camroutes.py` |
  | `GET /api/drowsy`, `POST /api/drowsy`, `POST /api/drowsy/event`, `POST /api/drowsy/log` | drowsy settings, events, measures | `lib/camroutes.py` |

- **Service.** `omacar-cams.service` is a user unit: `Restart=always`, no start limit (`StartLimitIntervalSec=0`), `Nice=10`. The CLI is `omacar cams status|on|off|sim`.
- **Simulation.** `sim` stands up `lavfi testsrc2` for every role with no camera. Anything from `sim` is labelled `SIMULATED`.
- **Cameras tab.**
  - The badge reads `LIVE · 3 cameras`, or `SIMULATED` when any role comes from `sim`.
  - Mute is shown disabled, with "No audio recorded".
  - Parking watch is shown off, "coming later".
- **Drowsy measures:**
  - Eye closure is the mean of `eyeBlinkLeft` and `eyeBlinkRight`.
  - The first 60 s above 30 mph set the open-eye baseline. "Closed" is above the baseline + 0.35, capped at 0.8.
  - PERCLOS is the share of frames in the last 60 s with the eyes closed.
  - A yawn is `jawOpen` > 0.6 held 1.5 s or more.
  - A nod is head pitch more than 15° below the baseline for 0.5 s or more, then recovering.
  - No face for more than 5 s is "Can't see you", which never alerts on its own.
- **Camera-free signals.** A stop is speed 0 for 5 minutes or more. Night hours are 02:00–06:00. These raise Level 1 only.
- **The gate.** Drowsy mode is active only above 30 mph (48.28 km/h), while the car is connected and moving. It is never active while parked.
- **The ladder.** Defaults are in `share/data/drowsy.json`, overrides in `~/.config/omarchy/omacar-drowsy.json`. Sensitive lowers every threshold by 20%.

  | Level | Trigger | What happens |
  |---|---|---|
  | 1 · Notice | PERCLOS ≥ 15%, or 3 yawns in 5 min, or 3 nods in 5 min, or 2 h since a stop, or night hours (once an hour) | a soft two-note chime; the voice says "James, you seem tired. Plan a break soon."; the radio rises +6 dB over 10 s, then settles back over 30 s; a card on screen |
  | 2 · Wake | eyes closed ≥ 1.0 s, or PERCLOS ≥ 25% | music ducks −12 dB over 0.5 s; an alert rises over 1.5 s, rotating bark, voice ("James, are you with me?") and a two-tone alarm (500–1500 Hz); full-screen card with a large "I'm awake" |
  | 3 · Pull over | eyes closed ≥ 2.0 s, or two Level 2 alerts within 5 min | a continuous alarm rises to full over 3 s; the voice says "Pull over now."; a full-screen card stays up |

- **Release.**
  - A level clears on a tap of "I'm awake", or when the eyes stay open for 5 s with PERCLOS falling. Sound fades out over 3 s.
  - After Level 3, a "Stop at the next safe place" banner stays until the car has been stopped for 2 minutes.
  - Every event goes to the records book as `kind=drowsy`, with time, level, trigger, speed and the measures.
- **Audio.**
  - There is one output stage (`share/js/audiobus.js`). Music sits at −12 dBFS and alerts may use 0 dBFS, which leaves 12 dB of headroom.
  - Ramps use `linearRampToValueAtTime`, with no step larger than 3 dB per 100 ms. The shapes come from a pure `rampPlan(level, from, to)`.
  - `lib/audio.py` pins the Surface's volume to 100% (wpctl). It is exposed at `GET /api/audio` and applied at app start. Home shows "AUX disconnected" when the port is the speakers.
- **Voice.**
  - Piper renders the clips once, into `share/assets/private/voice/*.ogg`, and they are listed in `share/assets/manifest.json`.
  - The phrases are "James, you seem tired. Plan a break soon.", "James, are you with me?" and "Pull over now."
  - The owner's name is "James".
- **Chip text.** Exactly `Watching`, `Can't see you`, `Paused · parked`, `Off`.
- **Copy.**
  - Alerts buy minutes; stopping to rest (a 20-minute nap or caffeine) is the fix (NHTSA, AAA Foundation).
  - The car radio must stay on AUX. Begin and drowsy mode's settings say so, and Begin plays a short chime.
- **Tests.**
  - Every new test file goes into `test/all.sh` the same day, inside the `redesign/cameras` block at its end.
  - JavaScript tests are `test/js/*.test.js`: each default-exports `[name, fn]` pairs and imports app modules as `../js/x.js`. `assert.js` has `eq` and `ok`.
  - A test that reads `share/data/*.json` does it with `await fetch("../data/<file>")`.
- **BOXTEST expects `omacar-cams` stopped on the box.** A running recorder makes Home open a stream that never ends, and headless Chromium's virtual time does not advance while a request is open.
- **Commits.**
  - The subject is one plain sentence, in the style of the log (for example "Home can be rearranged by hand, while parked, and a hand-edited file cannot break it").
  - The body says why.
  - The last line is `Co-Authored-By: <model> <noreply@anthropic.com>`, naming the model that actually writes that commit. The blocks below show `Claude Opus 5.5`; an implementer that is another model writes its own name instead.
- **Where work happens.**
  - Code is edited and committed in the worktree `/Users/jmyers/omgarchy/omacar-cameras`, on branch `redesign/cameras`.
  - Tests run on the Omarchy box against the mirror `~/Projects/.omacar-test/cameras`. Never use `~/Projects/.omacar-test/foundation`.
  - GitHub pushes go from the Mac.
  - `SCRATCH` below means your session's scratchpad directory.

**The test command** used by every task (called `BOXTEST` below). `.git` is excluded because a worktree's `.git` is a file pointing at a Mac path, which would make every git command on the box fail:

```bash
rsync -a --delete --exclude .git --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar-cameras/ jmyers@omarchy:Projects/.omacar-test/cameras/ && ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && test/all.sh'
```

A single suite runs as, for example, `ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && python3 test/cams_test.py'` after the same rsync.

## Merging with the foundation branch

`redesign/cameras` branched from `redesign/foundation` at 3835841. Foundation has since landed Task 7 (25b8f1a) and still has Tasks 8–12 to run. This branch touches each file those tasks share at one delimited insertion point, marked `redesign/cameras`:

| Shared file | This branch's one insertion | Foundation's edits there | Expected at merge |
|---|---|---|---|
| `share/js/main.js` | one row in `openSettings()`, just before the "Learn mode" row (Task 11) | Task 8: imports and `TABS`; Task 10: `applyTheme`, `paintDayNight`, `toggleDayNight`, `boot()` | clean |
| `share/app.html` | one block after the last `<script>` (Tasks 3, 5, 8) | Task 8: a `vehicle.css` link after `home.css` | clean |
| `share/js/views/home.js` | the `dashcam:` line in `MAKERS` (Task 4) | Task 7 (landed): imports, loader, editing, cleanup | clean (dry-run checked in Task 12) |
| `lib/api.py` | one block at the end of the file (Task 2) | Task 7 (landed): `/api/home` in the docstring and both handlers | clean |
| `test/guards_test.py` | one changed line: the manifest check names only `crz-` assets (Task 9) | Task 9: a new section appended just after that line, before the done block | **may conflict** (adjacent hunks). Keep both sides. |
| `share/css/*` | none (new `cameras.css` and `drowsy.css` only) | Tasks 8 and 10: `app.css`, `vehicle.css` | clean |
| `share/js/core.js`, `share/js/icons.js`, `test/app_test.py` | none | Tasks 7, 11 | clean |

Task 12 runs `git merge-tree` against the then-current `redesign/foundation` and records the result in the PR.

## File map

| File | Responsibility | Task |
|---|---|---|
| `lib/camstore.py` | clip names, listing, the loop's janitor, hard braking, events and locks, Range parsing, camera config | 1 |
| `lib/cams.py` | roles from by-id names, modes from v4l2-ctl, the ffmpeg command, the recorder, live frames, status, the CLI | 1 |
| `share/systemd/omacar-cams.service` | the recorder as a user unit | 1 |
| `lib/camroutes.py` | every JSON route this branch adds | 2, 5, 10 |
| `lib/serve.py` | the live MJPEG route and the clip route with Range | 2 |
| `share/js/camapi.js` | fetch helpers for the new routes; `?still=1` | 3 |
| `share/js/camlogic.js` | pure: badge, captions, timeline, clip stepping, Home card state | 3, 4 |
| `share/js/views/cameras.js` | the Cameras tab (replaces the placeholder) | 3 |
| `share/css/cameras.css` | the tab and the Home card | 3, 4 |
| `tools/camshot.py` | screenshots with the first-run tour seen and one frame per feed | 3 |
| `share/js/dashcard.js` | Home's Dashcams card | 4, 11 |
| `lib/audio.py` | the Surface's volume and port (wpctl, pactl) | 5 |
| `share/js/audiobus.js` | the one output stage | 5 |
| `share/js/audiostate.js` | the audio path as the page knows it | 5 |
| `share/js/alertness.js` | started beside the app: audio pin, drowsy mode | 5, 11 |
| `share/data/drowsy.json` | drowsy defaults, the spec's numbers | 6 |
| `share/js/drowsy.js` | pure measures, and MediaPipe results into frames | 6 |
| `share/js/ladder.js` | pure level state machine and stop clock | 7 |
| `share/js/ramps.js` | pure `rampPlan` | 8 |
| `share/js/sounds.js` | chime, alarm, bark (synthesized) and voice playback | 8 |
| `share/css/drowsy.css` | drowsy UI, Begin's AUX line | 8, 11 |
| `share/js/vendor/mediapipe/*` | vendored tasks-vision 1.0.1 and the model | 9 |
| `tools/render_voice.py` | renders the voice clips with Piper | 9 |
| `lib/drowsycfg.py` | drowsy settings, event and measure logs | 10 |
| `share/js/mjpeg.js` | pure multipart MJPEG parser | 10 |
| `share/js/facewatch.js` | cabin frames through the Face Landmarker | 10 |
| `share/js/drowsyrun.js` | the running engine: gate, measures, ladder, logs | 10 |
| `tools/drowsy_check.py` | MediaPipe loads and runs on the real cabin camera | 10 |
| `share/js/alertplayer.js` | ladder cues to ramps and sounds | 11 |
| `share/js/drowsyui.js` | chip, alert cards, banner, settings sheet | 11 |
| `tools/cams_e2e.py` | end to end on the box | 12 |

---

### Task 1: The recorder

**Files:**
- Create: `lib/camstore.py`, `lib/cams.py`, `share/systemd/omacar-cams.service`, `test/cams_test.py`
- Modify: `bin/omacar` (usage lines and the `cams)` case), `test/all.sh` (the `redesign/cameras` block)

**Interfaces:**
- Produces:
  - `camstore`: `ROLES`, `CLIP_SECS`, `DEFAULT_PATTERNS`, `videos()`, `config_path()`, `load_config(path=None) → {patterns, budget_gb}`, `budget_bytes(cfg=None)`.
  - `camstore` clips and the loop: `clip_start(name)`, `clip_name(t)`, `scan(root=None) → [{role, file, path, start, end, size, locked}]`, `list_clips(role=None, t0=None, t1=None, root=None)`, `usage(root=None) → {used, by_role, clips}`, `clip_path(role, name, root=None)`, `plan_janitor(clips, budget)`, `janitor(budget=None, root=None)`.
  - `camstore` events: `BrakeWatch().feed(t, kph) → bool` (with `.peak`), `load_events(root=None)`, `list_events(t0=None, t1=None, root=None)`, `mark(kind, t=None, speed_kph=None, root=None, now=None) → event`, `settle(root=None, now=None)`, `parse_range(header, size) → (start, end) | None | "unsatisfiable"`.
  - An event is `{id, kind, t, t0, t1, speed_kph, state: "pending"|"locked", files: ["role/file", …]}`. Kinds are `hard-braking`, `marked` and `saved`.
  - `cams` constants and paths: `ROLES`, `VAAPI`, `CAPS`, `SIM_MODE`, `LIVE_FPS`, `LIVE_GIVE_UP`, `BOUNDARY = "omacarframe"`, `run_dir()`, `live_path(role)`, `status_path()`.
  - `cams` discovery and the command: `match_roles(names, patterns)`, `find_cameras(cfg=None)`, `parse_formats(text)`, `choose_mode(modes, cap)`, `ffmpeg_args(role, mode, clip_dir, device=None, clip_secs=60, duration=None)`, `split_jpegs(buf)`.
  - `cams` runtime: `FpsMeter`, `Recorder(sim).run()`, `overview()`, `stream_live(out, role, frames=None, give_up=LIVE_GIVE_UP, clock=time.time, sleep=time.sleep) → frames sent`.
  - A mode is `{fmt: "MJPG"|"H264"|"YUYV"|"SIM", w, h, fps}`.
  - `OMACAR_VIDEOS` moves `~/Videos/OmaCar`; `XDG_RUNTIME_DIR` and `XDG_CONFIG_HOME` move the rest.

- [ ] **Step 1: Record the baseline**

Run `BOXTEST` before changing anything. Save each suite's pass/fail summary to `SCRATCH/baseline.txt`. A suite that fails already is not this plan's regression: name it in the PR.

- [ ] **Step 2: Write the failing test**

`test/cams_test.py`:

```python
#!/usr/bin/env python3
"""The recorder, without a camera: which camera is which, what mode it records
in, what the loop deletes, what counts as hard braking, and what an event
locks.

The last section runs the real ffmpeg command for a few seconds against a test
picture wherever there is ffmpeg and a VA-API render node (the box and the
tablet both have them), and skips loudly anywhere else. Scratch folders only:
nothing here touches ~/Videos or the real runtime directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

SCRATCH = tempfile.mkdtemp(prefix="omacar-cams-test-")
os.environ["OMACAR_VIDEOS"] = os.path.join(SCRATCH, "videos")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(SCRATCH, "run")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(SCRATCH, "state")

import cams      # noqa: E402
import camstore  # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


# ------------------------------------------------------------ which is which
head("each camera gets its role from its name")

C920 = "usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index0"
DJI = "usb-DJI_Osmo_Action_4_0123456789AB-video-index0"
ACE = "usb-Insta360_Ace_Pro_ABCDEF012345-video-index0"
NAMES = [C920, "usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index1", DJI, ACE,
         "usb-Microsoft_Surface_Camera_Front-video-index0"]
P = camstore.DEFAULT_PATTERNS
check("front, rear and cabin, by name", cams.match_roles(NAMES, P),
      {"front": DJI, "rear": ACE, "cabin": C920})
check("the metadata node (-video-index1) is never a camera",
      cams.match_roles(["usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index1"], P), {})
check("'Ace' is not found inside 'Surface'",
      cams.match_roles(["usb-Microsoft_Surface_Camera_Front-video-index0"], P), {})
check("'Osmo Action 4' matches udev's underscores",
      cams.match_roles(["usb-SZ_Osmo_Action_4_77-video-index0"], {"front": "Osmo Action 4"}),
      {"front": "usb-SZ_Osmo_Action_4_77-video-index0"})
check("an infrared ELP drops in as the cabin with no code change",
      cams.match_roles(["usb-ELP_USB_Camera_IR_2020-video-index0"], P),
      {"cabin": "usb-ELP_USB_Camera_IR_2020-video-index0"})
os.makedirs(os.path.dirname(camstore.config_path()), exist_ok=True)
with open(camstore.config_path(), "w", encoding="utf-8") as f:
    json.dump({"patterns": {"front": "C920", "nope": "x"}, "budget_gb": 12}, f)
_cfg = camstore.load_config()
check("the config file's pattern wins, and an unknown role is ignored",
      (cams.match_roles(NAMES, _cfg["patterns"])["front"], "nope" in _cfg["patterns"]),
      (C920, False))
check("and so does its budget", camstore.budget_bytes(_cfg), 12 * 10**9)
os.remove(camstore.config_path())
check("with no file, the budget is 40 GB", camstore.budget_bytes(), 40 * 10**9)

# ------------------------------------------------------------ the mode
head("the best mode at or under 1080p30, MJPEG first")

# Trimmed from the box's C920, `v4l2-ctl --list-formats-ext`, 2026-09-28.
C920_FORMATS = "\n".join([
    "ioctl: VIDIOC_ENUM_FMT",
    "\tType: Video Capture",
    "",
    "\t[0]: 'YUYV' (YUYV 4:2:2)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\t\tInterval: Discrete 0.067s (15.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.200s (5.000 fps)",
    "\t\tSize: Discrete 2304x1536",
    "\t\t\tInterval: Discrete 0.500s (2.000 fps)",
    "\t[1]: 'H264' (H.264, compressed)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t[2]: 'MJPG' (Motion-JPEG, compressed)",
    "\t\tSize: Discrete 640x480",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1280x720",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\tSize: Discrete 1920x1080",
    "\t\t\tInterval: Discrete 0.033s (30.000 fps)",
    "\t\t\tInterval: Discrete 0.042s (24.000 fps)",
])
_modes = cams.parse_formats(C920_FORMATS)
check("every size of every format is read", len(_modes), 8)
check("with its frame rates", _modes[0], {"fmt": "YUYV", "w": 640, "h": 480, "fps": [30.0, 15.0]})
check("front and rear: MJPEG 1080p30", cams.choose_mode(_modes, cams.CAPS["front"]),
      {"fmt": "MJPG", "w": 1920, "h": 1080, "fps": 30.0})
check("the cabin at low resolution: MJPEG 640x480 at 30",
      cams.choose_mode(_modes, cams.CAPS["cabin"]),
      {"fmt": "MJPG", "w": 640, "h": 480, "fps": 30.0})
check("with no MJPEG, H.264 before YUYV",
      cams.choose_mode([m for m in _modes if m["fmt"] != "MJPG"], cams.CAPS["front"]),
      {"fmt": "H264", "w": 1920, "h": 1080, "fps": 30.0})
check("a 5 fps 1080p loses to 640x480 at 30: a slideshow is not a dashcam",
      cams.choose_mode([m for m in _modes if m["fmt"] == "YUYV"], cams.CAPS["front"]),
      {"fmt": "YUYV", "w": 640, "h": 480, "fps": 30.0})
check("nothing at or under the cap is no mode, not a guess",
      cams.choose_mode([{"fmt": "MJPG", "w": 3840, "h": 2160, "fps": [30.0]}], cams.CAPS["front"]),
      None)
check("a rate over the cap is not used",
      cams.choose_mode([{"fmt": "MJPG", "w": 1280, "h": 720, "fps": [60.0, 30.0]}],
                       cams.CAPS["front"])["fps"], 30.0)

# ------------------------------------------------------------ the command
head("one ffmpeg per camera, as measured on the tablet")

_a = cams.ffmpeg_args("front", {"fmt": "MJPG", "w": 1920, "h": 1080, "fps": 30.0},
                      "/v/front", device="/dev/v4l/by-id/x")
_s = " ".join(_a)
check("the camera is opened in its chosen mode",
      "-f v4l2 -input_format mjpeg -video_size 1920x1080 -framerate 30 -i /dev/v4l/by-id/x" in _s, True)
check("compressed on the GPU",
      ("-vaapi_device /dev/dri/renderD128" in _s and "format=nv12,hwupload" in _s
       and "-c:v h264_vaapi" in _s), True)
check("at 6 Mbit/s front", "-b:v 6M" in _s, True)
check("never padded with duplicates in the dark", "-fps_mode passthrough" in _s, True)
check("a keyframe at every clip boundary", "-force_key_frames expr:gte(t,n_forced*60)" in _s, True)
check("one-minute fragmented MP4, named by when it started",
      ("-segment_time 60" in _s and "frag_keyframe" in _s
       and os.path.join("/v/front", "%Y%m%d-%H%M%S.mp4") in _a), True)
check("and a 640-px 10 fps live picture on stdout",
      ("fps=10,scale=640:-2" in _s and "-c:v mjpeg" in _s and _a[-1] == "pipe:1"), True)
_c = " ".join(cams.ffmpeg_args("cabin", cams.SIM_MODE["cabin"], "/v/cabin"))
check("the cabin at 1 Mbit/s", "-b:v 1M" in _c, True)
check("with no device, a real-time test picture",
      "-re -f lavfi -i testsrc2=size=640x480:rate=30" in _c, True)

# ------------------------------------------------------------ live frames
head("the live picture is cut into whole frames")

A = b"\xff\xd8AAAA\xff\xd9"
B = b"\xff\xd8BB\xff\x00B\xff\xd9"
_f, _rest = cams.split_jpegs(b"junk" + A + B + b"\xff\xd8half")
check("two whole frames, in order", _f, [A, B])
check("and the half frame is kept for the next read", _rest, b"\xff\xd8half")
check("nothing whole is nothing", cams.split_jpegs(b"\xff\xd8abc"), ([], b"\xff\xd8abc"))
_m = cams.FpsMeter()
for _i in range(11):
    _m.add(100 + _i, _i * 10)
check("real fps over the last five seconds", _m.fps(), 10.0)
_m.add(111, 105)
check("and it follows a camera that slows in the dark", _m.fps() < 10.0, True)


class Sink:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    def flush(self):
        pass


os.makedirs(cams.run_dir(), exist_ok=True)
with open(cams.live_path("rear"), "wb") as f:
    f.write(A)
_clock = [0.0]


def _tick(s):
    _clock[0] += s


_sink = Sink()
_n = cams.stream_live(_sink, "rear", clock=lambda: _clock[0], sleep=_tick)
check("a frame goes out as a multipart part with its length",
      (_n, _sink.data.startswith(
          b"--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\n\r\n" + A)),
      (1, True))
check("and silence ends the stream after ten seconds", _clock[0] >= cams.LIVE_GIVE_UP, True)
check("frames=N ends it after N frames",
      cams.stream_live(Sink(), "rear", frames=1, clock=lambda: _clock[0], sleep=_tick), 1)

# ------------------------------------------------------------ the loop
head("the loop deletes the oldest unlocked clips, and nothing else")

V = os.environ["OMACAR_VIDEOS"]
T0 = time.mktime((2026, 9, 30, 9, 0, 0, 0, 0, -1))


def put(role, t, size, event=None):
    d = os.path.join(V, "locked", event, role) if event else os.path.join(V, role)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, camstore.clip_name(t))
    with open(p, "wb") as fh:
        fh.write(b"\0" * size)
    return p


for _i in range(5):
    put("front", T0 + 60 * _i, 100)
put("rear", T0, 100)
put("cabin", T0 + 60, 100, event="20260930-090130-marked")
_clips = camstore.scan()
check("seven clips, loop and locked", len(_clips), 7)
check("a clip ends where the next starts, or a minute after its own start",
      [c["end"] - c["start"] for c in _clips if c["role"] == "front"], [60, 60, 60, 60, 60])
_d = camstore.plan_janitor(_clips, 450)
check("oldest first, only as many as it takes", [c["start"] - T0 for c in _d], [0, 60, 120])
check("never a locked clip, never the clip being written",
      [c for c in camstore.plan_janitor(_clips, 0) if c["locked"] or c["start"] - T0 == 240], [])
check("under budget, nothing goes", camstore.plan_janitor(_clips, 10**9), [])
check("janitor() deletes what the plan says", camstore.janitor(450),
      [f"front/{camstore.clip_name(T0 + s)}" for s in (0, 60, 120)])
check("and the storage count follows", camstore.usage()["used"], 400)

# ------------------------------------------------------------ hard braking
head("hard braking comes from the car's own speed")


def brakes(series):
    w = camstore.BrakeWatch()
    return [t for t, v in series if w.feed(t, v)]


STEADY = [(i / 5, 100.0) for i in range(10)]
check("16 km/h lost in 0.8 s is hard braking",
      brakes(STEADY + [(2.0, 96.0), (2.2, 90.0), (2.4, 86.0), (2.6, 84.0)]), [2.6])
check("the same 16 km/h over three seconds is not",
      brakes(STEADY + [(2.0 + i / 5, 100.0 - 16 * (i + 1) / 15) for i in range(15)]), [])
check("15.9 km/h in half a second is not", brakes(STEADY + [(2.0, 92.0), (2.4, 84.1)]), [])
check("one event per stop, not one per sample",
      len(brakes(STEADY + [(2.0, 80.0), (2.2, 60.0), (2.4, 40.0)])), 1)
check("a lost link is not a stop", brakes(STEADY + [(2.0, None), (2.2, 50.0)]), [])

# ------------------------------------------------------------ events
head("an event locks thirty seconds either side, on every camera")

shutil.rmtree(V)
for _r in camstore.ROLES:
    for _i in range(4):
        put(_r, T0 + 60 * _i, 10)
_t = T0 + 90
_ev = camstore.mark("marked", t=_t, speed_kph=88.0, now=_t)
check("written with its window, kind and speed",
      (_ev["kind"], _ev["t0"] - T0, _ev["t1"] - T0, _ev["speed_kph"], _ev["state"]),
      ("marked", 60, 120, 88.0, "pending"))
check("the clips it touches move at once, on every camera", sorted(_ev["files"]),
      sorted(f"{r}/{camstore.clip_name(T0 + 60)}" for r in camstore.ROLES))
check("into the event's folder",
      os.path.isfile(os.path.join(V, "locked", _ev["id"], "cabin", camstore.clip_name(T0 + 60))),
      True)
check("it stays pending while its window is open",
      camstore.load_events()["events"][0]["state"], "pending")
camstore.settle(now=T0 + 130)
check("and is locked once the window has closed",
      camstore.load_events()["events"][0]["state"], "locked")
check("the loop never deletes a locked clip",
      [c for c in camstore.plan_janitor(camstore.scan(), 0) if c["locked"]], [])
check("a double tap is one event", camstore.mark("marked", t=_t, now=_t)["id"], _ev["id"])
check("and one line in the file", len(camstore.load_events()["events"]), 1)
_ev2 = camstore.mark("hard-braking", t=T0 + 200, speed_kph=97.0, now=T0 + 200)
check("a later event takes the clips it overlaps", len(_ev2["files"]), 6)
put("front", T0 + 220, 10)
camstore.settle(now=T0 + 225)
_e2 = [e for e in camstore.load_events()["events"] if e["kind"] == "hard-braking"][0]
check("a clip that starts inside the window joins it as it appears",
      (f"front/{camstore.clip_name(T0 + 220)}" in _e2["files"], _e2["state"]), (True, "pending"))
camstore.settle(now=T0 + 240)
check("and the event locks when the window closes",
      [e for e in camstore.load_events()["events"] if e["kind"] == "hard-braking"][0]["state"],
      "locked")

# ------------------------------------------------------------ the API's view
head("the recorder's own report, as the API reads it")

check("with no recorder running, it says off", cams.overview()["running"], False)
with open(cams.status_path(), "w", encoding="utf-8") as f:
    json.dump({"pid": os.getpid(), "t": time.time(), "sim": True, "roles": {
        "front": {"device": None, "mode": cams.SIM_MODE["front"], "sim": True,
                  "recording": True, "live": True, "fps": 29.9, "error": None}}}, f)
_ov = cams.overview()
check("with one, it says so, and which role is simulated",
      (_ov["running"], _ov["sim"], _ov["roles"]["front"]["sim"], _ov["roles"]["front"]["fps"]),
      (True, True, True, 29.9))
check("every role is reported, with its clips counted",
      (sorted(_ov["roles"]), _ov["roles"]["rear"]["clips"]), (["cabin", "front", "rear"], 4))
check("storage used, of the budget", _ov["storage"]["budget"], 40 * 10**9)
os.remove(cams.status_path())

# ------------------------------------------------------------ the real command
head("the real command, for seven seconds, against a test picture")

if not (shutil.which("ffmpeg") and shutil.which("ffprobe") and os.path.exists(cams.VAAPI)):
    print("    (skipping: no ffmpeg, ffprobe or VA-API render node here. The box\n"
          "     and the tablet have all three, and that is where it counts.)")
else:
    _dir = os.path.join(SCRATCH, "real")
    os.makedirs(_dir)
    _p = subprocess.run(cams.ffmpeg_args("cabin", {"fmt": "SIM", "w": 640, "h": 480, "fps": 30.0},
                                         _dir, clip_secs=2, duration=7),
                        capture_output=True, timeout=90)
    _frames, _ = cams.split_jpegs(_p.stdout)
    check("ffmpeg ran to the end", _p.returncode, 0)
    check(f"the live picture came out at about 10 fps ({len(_frames)} frames in 7 s)",
          55 <= len(_frames) <= 80, True)
    _out = sorted(os.listdir(_dir))
    check(f"2 s segments came out ({len(_out)} of them)", len(_out) >= 3, True)

    def _probe(name, *q):
        return subprocess.run(["ffprobe", "-v", "error", *q, os.path.join(_dir, name)],
                              capture_output=True, text=True).stdout.strip()

    _durs = [float(_probe(c, "-show_entries", "format=duration", "-of", "csv=p=0"))
             for c in _out[:-1]]
    check(f"every whole segment is 2 s long, not 3 and 1 ({_durs})",
          all(abs(d - 2.0) <= 0.25 for d in _durs), True)
    check("and every one starts on a keyframe",
          all(_probe(c, "-select_streams", "v", "-read_intervals", "%+#1",
                     "-show_entries", "frame=key_frame", "-of", "csv=p=0").startswith("1")
              for c in _out), True)
    check("in H.264", _probe(_out[0], "-select_streams", "v", "-show_entries",
                             "stream=codec_name", "-of", "csv=p=0"), "h264")

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the recorder holds\n")
```

In `test/all.sh`, just before the final `exit $((fails > 0))`, add the block every later task extends:

```bash
# ---- redesign/cameras ---------------------------------------------------------
# The cameras, the audio stage and drowsy mode. Scratch folders only: none of
# these touches ~/Videos, the real runtime directory or the speakers.
python3 "$ROOT/test/cams_test.py" || fails=$((fails + 1))
# ---- end redesign/cameras -----------------------------------------------------
```

- [ ] **Step 3: Run it to see it fail**

Run: `BOXTEST`'s rsync, then `ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && python3 test/cams_test.py'`.

Expected: `ModuleNotFoundError: No module named 'cams'`.

- [ ] **Step 4: Write lib/camstore.py**

```python
#!/usr/bin/env python3
"""Where the cameras' clips live, which are kept, and what happened.

    ~/Videos/OmaCar/<role>/YYYYmmdd-HHMMSS.mp4        the loop: one-minute clips
    ~/Videos/OmaCar/locked/<event-id>/<role>/<clip>   kept; the loop never deletes these
    ~/Videos/OmaCar/events.json                       every event, oldest first

Shared by the recorder (lib/cams.py), which runs the loop and watches for hard
braking, and by the server (lib/camroutes.py), which marks events when the
driver taps. Both take one file lock, so a clip is never moved by one while the
other is deleting it.

OMACAR_VIDEOS moves the whole folder, for tests and the end-to-end check.
Stdlib only.
"""

import fcntl
import json
import os
import re
import time
from collections import deque
from contextlib import contextmanager

ROLES = ("front", "rear", "cabin")
CLIP_RE = re.compile(r"^(\d{8})-(\d{6})\.mp4$")
CLIP_SECS = 60
BEFORE = 30          # seconds kept before an event
AFTER = 30           # and after it
DEFAULT_PATTERNS = {
    "front": "Osmo Action 4|DJI",
    "rear": "Insta360|Ace",
    "cabin": "C920|Logitech|ELP",
}
DEFAULT_BUDGET_GB = 40      # of the tablet's 93 GB free


def videos():
    return os.environ.get("OMACAR_VIDEOS") or os.path.expanduser("~/Videos/OmaCar")


def config_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-cameras.json")


def load_config(path=None):
    """The defaults, with ~/.config/omarchy/omacar-cameras.json laid over them.
    A pattern for a role that does not exist, or a budget that is not a
    positive number, is ignored rather than trusted."""
    cfg = {"patterns": dict(DEFAULT_PATTERNS), "budget_gb": DEFAULT_BUDGET_GB}
    try:
        with open(path or config_path(), encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    if not isinstance(user, dict):
        user = {}
    pats = user.get("patterns")
    if isinstance(pats, dict):
        for role, pat in pats.items():
            if role in ROLES and isinstance(pat, str) and pat:
                cfg["patterns"][role] = pat
    gb = user.get("budget_gb")
    if isinstance(gb, (int, float)) and not isinstance(gb, bool) and gb > 0:
        cfg["budget_gb"] = gb
    return cfg


def budget_bytes(cfg=None):
    return int((cfg or load_config())["budget_gb"] * 10**9)


# ---- clips -----------------------------------------------------------------

def clip_start(name):
    """The wall-clock second a clip began, from its name. Local time, which is
    what ffmpeg's -strftime writes."""
    m = CLIP_RE.match(name or "")
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))


def clip_name(t):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + ".mp4"


def _ls(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def scan(root=None):
    """Every clip, loop and locked, as {role, file, path, start, end, size,
    locked}, oldest first. A clip ends where the next one of its role starts,
    or a minute after its own start, whichever is sooner."""
    root = root or videos()
    out = []
    for role in ROLES:
        d = os.path.join(root, role)
        for name in _ls(d):
            st = clip_start(name)
            if st is not None:
                p = os.path.join(d, name)
                out.append({"role": role, "file": name, "path": p, "start": st,
                            "size": _size(p), "locked": None})
    ldir = os.path.join(root, "locked")
    for ev in _ls(ldir):
        for role in ROLES:
            d = os.path.join(ldir, ev, role)
            for name in _ls(d):
                st = clip_start(name)
                if st is not None:
                    p = os.path.join(d, name)
                    out.append({"role": role, "file": name, "path": p, "start": st,
                                "size": _size(p), "locked": ev})
    out.sort(key=lambda c: (c["role"], c["start"]))
    for i, c in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        end = c["start"] + CLIP_SECS
        if nxt and nxt["role"] == c["role"]:
            end = min(end, nxt["start"])
        c["end"] = end
    out.sort(key=lambda c: (c["start"], c["role"]))
    return out


def list_clips(role=None, t0=None, t1=None, root=None):
    """What the timeline draws: names and times, never a filesystem path."""
    return [{k: c[k] for k in ("role", "file", "start", "end", "size", "locked")}
            for c in scan(root)
            if (role is None or c["role"] == role)
            and (t0 is None or c["end"] > t0) and (t1 is None or c["start"] < t1)]


def usage(root=None):
    by_role = {r: 0 for r in ROLES}
    count = {r: 0 for r in ROLES}
    for c in scan(root):
        by_role[c["role"]] += c["size"]
        count[c["role"]] += 1
    return {"used": sum(by_role.values()), "by_role": by_role, "clips": count}


def clip_path(role, name, root=None):
    """The file behind /api/cams/clip/<role>/<name>, in the loop or in any
    locked event, or None. The name must look like a clip, so a request can
    never climb out of the folder."""
    if role not in ROLES or not CLIP_RE.match(name or ""):
        return None
    root = root or videos()
    p = os.path.join(root, role, name)
    if os.path.isfile(p):
        return p
    ldir = os.path.join(root, "locked")
    for ev in _ls(ldir):
        p = os.path.join(ldir, ev, role, name)
        if os.path.isfile(p):
            return p
    return None


# ---- the loop ----------------------------------------------------------------

@contextmanager
def _lock(root):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, ".lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def plan_janitor(clips, budget):
    """The loop clips to delete, oldest first, to bring the total under budget.

    Locked clips count against the budget and are never chosen. Neither is
    the newest clip of each role: that is the one ffmpeg is writing."""
    total = sum(c["size"] for c in clips)
    newest = {}
    for c in clips:
        if c["locked"] is None and (c["role"] not in newest
                                    or c["start"] > newest[c["role"]]["start"]):
            newest[c["role"]] = c
    spare = [c for c in clips if c["locked"] is None and c is not newest.get(c["role"])]
    doomed = []
    for c in sorted(spare, key=lambda c: c["start"]):
        if total <= budget:
            break
        doomed.append(c)
        total -= c["size"]
    return doomed


def janitor(budget=None, root=None):
    root = root or videos()
    with _lock(root):
        doomed = plan_janitor(scan(root), budget_bytes() if budget is None else budget)
        for c in doomed:
            try:
                os.remove(c["path"])
            except OSError:
                pass
    return [f"{c['role']}/{c['file']}" for c in doomed]


# ---- hard braking --------------------------------------------------------------

class BrakeWatch:
    """A drop of 16 km/h or more within one second, about 0.45 g, from the
    car's own speed. One event per 30 s: the window it locks is a minute long,
    and a second event inside it would only lock the same clips again."""

    def __init__(self, drop_kph=16.0, within=1.0, quiet=30.0):
        self.drop, self.within, self.quiet = drop_kph, within, quiet
        self.win = deque()
        self.last = None
        self.peak = None

    def feed(self, t, kph):
        if kph is None:
            self.win.clear()
            return False
        self.win.append((t, kph))
        while self.win and t - self.win[0][0] > self.within:
            self.win.popleft()
        top = max(v for _, v in self.win)
        if top - kph >= self.drop and (self.last is None or t - self.last >= self.quiet):
            self.last, self.peak = t, top
            self.win.clear()
            return True
        return False


# ---- events ------------------------------------------------------------------

def _events_file(root):
    return os.path.join(root, "events.json")


def load_events(root=None):
    try:
        with open(_events_file(root or videos()), encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("events"), list):
            return doc
    except (OSError, ValueError):
        pass
    return {"events": []}


def _save_events(root, doc):
    path = _events_file(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)


def list_events(t0=None, t1=None, root=None):
    return [e for e in load_events(root)["events"]
            if (t0 is None or e["t"] >= t0) and (t1 is None or e["t"] <= t1)]


def mark(kind, t=None, speed_kph=None, root=None, now=None):
    """Lock 30 s before to 30 s after `t` (default: now) on every camera.

    Kinds: "hard-braking" (the recorder), "marked" (Mark event) and "saved"
    (Save clip). The clips that exist move now; the ones still to come move
    as settle() finds them."""
    root = root or videos()
    t = time.time() if t is None else float(t)
    eid = time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + "-" + kind
    with _lock(root):
        doc = load_events(root)
        if not any(e["id"] == eid for e in doc["events"]):
            doc["events"].append({"id": eid, "kind": kind, "t": t, "t0": t - BEFORE,
                                  "t1": t + AFTER, "speed_kph": speed_kph,
                                  "state": "pending", "files": []})
            _save_events(root, doc)
    settle(root=root, now=now)
    return next(e for e in load_events(root)["events"] if e["id"] == eid)


def settle(root=None, now=None):
    """Move every loop clip that overlaps a pending event's window into that
    event's folder, and list it there. An event is done once its window has
    closed and the clip covering its last second has started.

    A clip two events overlap stays in the first event's folder, and both
    events list it. Moving the clip ffmpeg is still writing is safe: the
    rename keeps the inode, so ffmpeg finishes the file in its new folder."""
    root = root or videos()
    now = time.time() if now is None else now
    moved = 0
    with _lock(root):
        doc = load_events(root)
        pending = [e for e in doc["events"] if e.get("state") == "pending"]
        if not pending:
            return 0
        clips = scan(root)
        for e in pending:
            for c in clips:
                if c["end"] <= e["t0"] or c["start"] >= e["t1"]:
                    continue
                ref = f"{c['role']}/{c['file']}"
                if c["locked"] is None:
                    dest = os.path.join(root, "locked", e["id"], c["role"])
                    os.makedirs(dest, exist_ok=True)
                    new = os.path.join(dest, c["file"])
                    os.replace(c["path"], new)
                    c["path"], c["locked"] = new, e["id"]
                    moved += 1
                if ref not in e["files"]:
                    e["files"].append(ref)
            if now >= e["t1"] + 5:
                e["state"] = "locked"
        _save_events(root, doc)
    return moved


# ---- HTTP Range --------------------------------------------------------------

def parse_range(header, size):
    """(start, end), inclusive, for a single `bytes=` range; None to send the
    whole file; or "unsatisfiable" for a range that starts past the end.

    One range only: Chromium's <video> never asks for more, and a multi-range
    request gets the whole file, which is a correct answer to it."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if not m or (not m.group(1) and not m.group(2)):
        return None
    a, b = m.group(1), m.group(2)
    if not a:
        n = int(b)
        if n == 0 or not size:
            return "unsatisfiable"
        return max(0, size - n), size - 1
    start = int(a)
    if start >= size:
        return "unsatisfiable"
    end = min(int(b), size - 1) if b else size - 1
    if end < start:
        return None
    return start, end
```

- [ ] **Step 5: Write lib/cams.py**

```python
#!/usr/bin/env python3
"""The cameras: which is which, what each can do, and the recorder that owns them.

    omacar cams status      what is plugged in, and what is recording
    omacar cams on | off    enable and start, or stop and disable, omacar-cams.service
    omacar cams sim         the recorder, here, with lavfi test pictures for
                            every role that has no camera (SIMULATED in the app)
    omacar cams run [--sim] the recorder itself; what the unit runs

ONE FFMPEG PER CAMERA. The input is decoded once and split two ways: the
recording, compressed on the GPU into one-minute fragmented-MP4 clips, and a
640-px, 10 fps JPEG live picture, which this process writes atomically to
$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg. Files, not sockets, the way the
daemon and live.json already work.

CAMERAS ARE FOUND BY NAME, never by /dev/videoN: the tablet's IPU6 driver
claims video0-video63, so USB cameras land at video64 and up, in any order.

Stdlib only; ffmpeg and v4l2-ctl do the work.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camstore  # noqa: E402

ROLES = camstore.ROLES
BY_ID = "/dev/v4l/by-id"
VAAPI = "/dev/dri/renderD128"
# The best mode at or under 1080p30; the cabin at low resolution.
CAPS = {"front": (1920, 1080, 30), "rear": (1920, 1080, 30), "cabin": (640, 480, 30)}
BITRATE = {"front": "6M", "rear": "6M", "cabin": "1M"}
FORMAT_ORDER = ("MJPG", "H264", "YUYV")
INPUT_FORMAT = {"MJPG": "mjpeg", "H264": "h264", "YUYV": "yuyv422"}
# What `sim` stands up for a role with no camera. 720p rather than 1080p, so
# three test pictures cost the box no more than real cameras would.
SIM_MODE = {"front": {"fmt": "SIM", "w": 1280, "h": 720, "fps": 30.0},
            "rear": {"fmt": "SIM", "w": 1280, "h": 720, "fps": 30.0},
            "cabin": {"fmt": "SIM", "w": 640, "h": 480, "fps": 30.0}}
LIVE_WIDTH = 640
LIVE_FPS = 10
LIVE_GIVE_UP = 10       # seconds without a new frame before a live stream ends
BOUNDARY = "omacarframe"


def run_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "omacar-cams")


def live_path(role):
    return os.path.join(run_dir(), f"{role}.jpg")


def status_path():
    return os.path.join(run_dir(), "status.json")


# ---- which camera is which -------------------------------------------------

def match_roles(names, patterns):
    """{role: by-id name} for the cameras whose names match each role's pattern.

    Only capture nodes count: a UVC camera also exposes -video-index1, which
    carries metadata, not pictures. Case-sensitive, so the Ace Pro's "Ace" is
    not found inside "Surface"; and tried with underscores read as spaces as
    well, because udev writes "Osmo Action 4" as Osmo_Action_4."""
    out, taken = {}, set()
    capture = sorted(n for n in names if n.endswith("-video-index0"))
    for role in ROLES:
        pat = patterns.get(role)
        if not pat:
            continue
        rx = re.compile(pat)
        for n in capture:
            if n not in taken and (rx.search(n) or rx.search(n.replace("_", " "))):
                out[role] = n
                taken.add(n)
                break
    return out


def find_cameras(cfg=None):
    """{role: /dev/v4l/by-id/...} for what is plugged in now."""
    cfg = cfg or camstore.load_config()
    try:
        names = os.listdir(BY_ID)
    except OSError:
        names = []
    return {r: os.path.join(BY_ID, n) for r, n in match_roles(names, cfg["patterns"]).items()}


# ---- what each camera can do -------------------------------------------------

_FMT = re.compile(r"\[\d+\]:\s*'(\w+)'")
_SIZE = re.compile(r"Size:\s*Discrete\s+(\d+)x(\d+)")
_FPS = re.compile(r"\(([\d.]+)\s*fps\)")


def parse_formats(text):
    """`v4l2-ctl --list-formats-ext` as [{fmt, w, h, fps: [...]}]."""
    modes, fmt, cur = [], None, None
    for line in (text or "").splitlines():
        m = _FMT.search(line)
        if m:
            fmt, cur = m.group(1), None
            continue
        m = _SIZE.search(line)
        if m and fmt:
            cur = {"fmt": fmt, "w": int(m.group(1)), "h": int(m.group(2)), "fps": []}
            modes.append(cur)
            continue
        m = _FPS.search(line)
        if m and cur is not None:
            cur["fps"].append(float(m.group(1)))
    return modes


def choose_mode(modes, cap):
    """The best mode at or under `cap` (w, h, fps), preferring MJPEG, then
    H.264, then YUYV. Within a format, a mode that manages 15 fps beats one
    that does not, then the bigger picture, then the higher rate: a C920's
    YUYV 1080p runs at 5 fps, which is a slideshow, not a dashcam."""
    cw, ch, cf = cap
    for fmt in FORMAT_ORDER:
        best = None
        for m in modes:
            if m["fmt"] != fmt or m["w"] > cw or m["h"] > ch:
                continue
            fps = max((f for f in m["fps"] if f <= cf + 0.01), default=None)
            if fps is None:
                continue
            key = (fps >= 15, m["w"] * m["h"], fps)
            if best is None or key > best[0]:
                best = (key, {"fmt": fmt, "w": m["w"], "h": m["h"], "fps": fps})
        if best:
            return best[1]
    return None


def probe_modes(device):
    try:
        r = subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_formats(r.stdout)


# ---- the command ------------------------------------------------------------

def ffmpeg_args(role, mode, clip_dir, device=None, clip_secs=camstore.CLIP_SECS,
                duration=None, vaapi=VAAPI):
    """One ffmpeg for one camera: decode once, split two ways.

      recording  hwupload -> h264_vaapi. -fps_mode passthrough, so a camera
                 that slows in the dark is not padded with duplicates (on
                 2026-09-27 a dim room gave 10 real fps and 407 of 600 frames
                 were padding). Keyframes forced at every clip boundary,
                 because without them 5 s segments came out 6 s and 4 s long.
                 A keyframe every 2 s besides, so the fragmented MP4 has
                 fragments to seek to.
      live       10 fps, 640 px wide, MJPEG on stdout, for this process to cut
                 into files.

    With `device` None, the input is a test picture (lavfi testsrc2) in the
    role's mode, at real time. `duration` stops it (tests only).
    """
    w, h, fps = mode["w"], mode["h"], mode["fps"]
    rate = f"{fps:g}"
    src = ["-t", str(duration)] if duration else []
    if device is None:
        src += ["-re", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={rate}"]
    else:
        src += ["-f", "v4l2", "-input_format", INPUT_FORMAT[mode["fmt"]],
                "-video_size", f"{w}x{h}", "-framerate", rate, "-i", device]
    gop = str(max(1, int(round(fps * 2))))
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats",
        "-progress", "pipe:2", "-vaapi_device", vaapi, *src,
        "-filter_complex",
        f"[0:v]split=2[rec][live];[rec]format=nv12,hwupload[recv];"
        f"[live]fps={LIVE_FPS},scale={LIVE_WIDTH}:-2[livev]",
        "-map", "[recv]", "-c:v", "h264_vaapi", "-b:v", BITRATE[role],
        "-maxrate", BITRATE[role], "-g", gop, "-fps_mode", "passthrough",
        "-force_key_frames", f"expr:gte(t,n_forced*{clip_secs})",
        "-f", "segment", "-segment_time", str(clip_secs), "-segment_format", "mp4",
        "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        "-reset_timestamps", "1", "-strftime", "1",
        os.path.join(clip_dir, "%Y%m%d-%H%M%S.mp4"),
        "-map", "[livev]", "-c:v", "mjpeg", "-q:v", "6", "-f", "mjpeg", "pipe:1",
    ]


def split_jpegs(buf):
    """(whole JPEGs, the unfinished rest) from concatenated JPEGs. ffmpeg's
    MJPEG never carries a thumbnail, and 0xFF inside entropy-coded data is
    always stuffed, so FFD9 inside a frame is its end."""
    frames = []
    while True:
        s = buf.find(b"\xff\xd8")
        if s < 0:
            return frames, b""
        e = buf.find(b"\xff\xd9", s + 2)
        if e < 0:
            return frames, buf[s:]
        frames.append(buf[s:e + 2])
        buf = buf[e + 2:]


class FpsMeter:
    """Real frames per second over the last few seconds, from ffmpeg's running
    frame count. ffmpeg's own fps= averages since it started, which hides a
    camera that slowed down when the road went dark ten minutes ago."""

    def __init__(self, window=5.0):
        self.window = window
        self.points = deque()

    def add(self, t, frames):
        self.points.append((t, frames))
        while len(self.points) > 2 and t - self.points[0][0] > self.window:
            self.points.popleft()

    def fps(self):
        if len(self.points) < 2:
            return None
        (t0, f0), (t1, f1) = self.points[0], self.points[-1]
        return round((f1 - f0) / (t1 - t0), 1) if t1 > t0 else None


# ---- one camera ---------------------------------------------------------------

class Camera:
    """One role's ffmpeg, its live picture, and the last thing it said."""

    def __init__(self, role, device, mode, sim=False):
        self.role, self.device, self.mode, self.sim = role, device, mode, sim
        self.proc = None
        self.error = None
        self.started = None
        self.last_frame = None
        self.meter = FpsMeter()

    def start(self):
        clip_dir = os.path.join(camstore.videos(), self.role)
        os.makedirs(clip_dir, exist_ok=True)
        os.makedirs(run_dir(), exist_ok=True)
        args = ffmpeg_args(self.role, self.mode, clip_dir,
                           device=None if self.sim else self.device)
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.started = time.time()
        threading.Thread(target=self._frames, daemon=True).start()
        threading.Thread(target=self._progress, daemon=True).start()

    def _frames(self):
        out = live_path(self.role)
        tmp = out + ".tmp"
        buf = b""
        while True:
            chunk = self.proc.stdout.read1(1 << 16)
            if not chunk:
                return
            frames, buf = split_jpegs(buf + chunk)
            if frames:
                with open(tmp, "wb") as f:
                    f.write(frames[-1])
                os.replace(tmp, out)
                self.last_frame = time.time()
            if len(buf) > 8 << 20:
                buf = b""      # eight megabytes with no end marker is not a picture

    def _progress(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            key, sep, val = line.partition("=")
            if sep and re.fullmatch(r"[a-z0-9_]+", key):
                if key == "frame" and val.isdigit():
                    self.meter.add(time.time(), int(val))
                continue
            if line:
                self.error = line[:300]

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            # SIGINT, not SIGKILL: ffmpeg closes the clip it is writing, so the
            # last minute is a playable file rather than a truncated one.
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def status(self):
        alive = self.alive()
        fresh = self.last_frame is not None and time.time() - self.last_frame < 3
        return {"device": self.device, "mode": self.mode, "sim": self.sim,
                "recording": alive, "live": alive and fresh,
                "fps": self.meter.fps() if alive else None,
                "since": self.started, "error": self.error}


# ---- the recorder --------------------------------------------------------------

def _live():
    try:
        import records
        return records.live()
    except Exception:                                          # noqa: BLE001
        return None


class Recorder:
    """Every role's camera, the loop, and the watch for hard braking."""

    def __init__(self, sim=False):
        self.sim = sim
        self.cams = {}
        self.notes = {}
        self.retry_at = {}
        self.brake = camstore.BrakeWatch()
        self.last_t = None

    def discover(self):
        found = find_cameras()
        now = time.time()
        for role in ROLES:
            cam = self.cams.get(role)
            if cam and cam.alive():
                continue
            if cam and cam.error:
                self.notes[role] = cam.error
            if now < self.retry_at.get(role, 0):
                continue
            self.retry_at[role] = now + 5          # a camera that dies is retried, not spun
            dev = found.get(role)
            if dev:
                mode = choose_mode(probe_modes(dev), CAPS[role])
                if not mode:
                    self.notes[role] = "no mode at or under 1080p30"
                    self.cams.pop(role, None)
                    continue
                new = Camera(role, dev, mode)
            elif self.sim:
                new = Camera(role, None, SIM_MODE[role], sim=True)
            else:
                self.notes[role] = "no camera"
                self.cams.pop(role, None)
                continue
            new.start()
            self.cams[role] = new

    def watch_braking(self):
        s = _live()
        if not s or not s.get("connected"):
            self.brake.feed(time.time(), None)
            return
        t = s.get("t")
        kph = (s.get("values") or {}).get("SPEED")
        if t is None or t == self.last_t or not isinstance(kph, (int, float)):
            return
        self.last_t = t
        if self.brake.feed(t, kph):
            camstore.mark("hard-braking", t=t, speed_kph=self.brake.peak)

    def write_status(self):
        doc = {"pid": os.getpid(), "t": time.time(), "sim": self.sim, "roles": {}}
        for role in ROLES:
            cam = self.cams.get(role)
            doc["roles"][role] = cam.status() if cam else {
                "device": None, "mode": None, "sim": False, "recording": False,
                "live": False, "fps": None, "since": None, "error": self.notes.get(role)}
        os.makedirs(run_dir(), exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, status_path())

    def run(self):
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        next_find = next_settle = next_janitor = 0.0
        while not stop.is_set():
            now = time.time()
            if now >= next_find:
                self.discover()
                next_find = now + 2
            # live.json is written five times a second; one second of braking
            # needs every sample of it.
            self.watch_braking()
            if now >= next_settle:
                camstore.settle()
                self.write_status()
                next_settle = now + 1
            if now >= next_janitor:
                camstore.janitor()
                next_janitor = now + 30
            stop.wait(0.2)
        for cam in self.cams.values():
            cam.stop()
        try:
            os.remove(status_path())
        except OSError:
            pass


# ---- what the API and the CLI read ---------------------------------------------

def _read_status():
    try:
        with open(status_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def overview():
    """GET /api/cams. Per role: device, mode, recording, real fps, clip count,
    storage used, and the last error; and storage used of the budget."""
    cfg = camstore.load_config()
    st = _read_status()
    running = bool(st) and time.time() - st.get("t", 0) < 5 and _alive(st.get("pid"))
    found = find_cameras(cfg)
    use = camstore.usage()
    roles = {}
    for role in ROLES:
        r = (st or {}).get("roles", {}).get(role, {}) if running else {}
        why = r.get("error") if running else ("recorder off" if role in found else "no camera")
        roles[role] = {
            "device": r.get("device") or found.get(role),
            "mode": r.get("mode"), "sim": bool(r.get("sim")),
            "recording": bool(r.get("recording")), "live": bool(r.get("live")),
            "fps": r.get("fps"), "clips": use["clips"][role], "used": use["by_role"][role],
            "error": why,
        }
    return {"running": running, "sim": bool(running and st.get("sim")),
            "storage": {"used": use["used"], "budget": camstore.budget_bytes(cfg)},
            "loop": True, "roles": roles, "now": time.time()}


def stream_live(out, role, frames=None, give_up=LIVE_GIVE_UP, clock=time.time,
                sleep=time.sleep):
    """Write the role's live picture to `out` as multipart MJPEG, each part
    carrying its own length, until `frames` parts have gone or no new frame
    has arrived for `give_up` seconds. Returns the number sent."""
    path = live_path(role)
    last, sent, quiet_since = None, 0, clock()
    while True:
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp is not None and stamp != last:
            try:
                with open(path, "rb") as f:
                    jpg = f.read()
            except OSError:
                jpg = b""
            last = stamp
            if jpg:
                out.write(b"--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                          % (BOUNDARY.encode(), len(jpg)) + jpg + b"\r\n")
                out.flush()
                sent += 1
                quiet_since = clock()
                if frames and sent >= frames:
                    return sent
        elif clock() - quiet_since > give_up:
            return sent
        sleep(1.0 / LIVE_FPS)


def _print_status():
    ov = overview()
    state = ("running" + (" (sim)" if ov["sim"] else "")) if ov["running"] else "off"
    s = ov["storage"]
    print(f"\n  recorder  {state}")
    print(f"  storage   {s['used'] / 1e9:.1f} of {s['budget'] / 1e9:.0f} GB\n")
    for role, r in ov["roles"].items():
        m = r["mode"]
        mode = f"{m['fmt']} {m['w']}x{m['h']}@{m['fps']:g}" if m else "-"
        fps = f"{r['fps']:.1f} fps" if r["fps"] else ""
        print(f"  {role:<6} {'REC' if r['recording'] else '---'}  {mode:<22} {fps:<9} "
              f"{r['clips']:>4} clips  {os.path.basename(r['device'] or '') or '(none)'}")
        if r["error"]:
            print(f"         {r['error']}")
    print()
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        return _print_status()
    if cmd in ("on", "off"):
        how = ["enable", "--now"] if cmd == "on" else ["disable", "--now"]
        return subprocess.run(["systemctl", "--user", *how, "omacar-cams.service"]).returncode
    if cmd in ("run", "sim"):
        st = _read_status()
        if st and _alive(st.get("pid")) and st.get("pid") != os.getpid():
            print(f"omacar: the recorder is already running (pid {st['pid']}): "
                  f"omacar cams off first", file=sys.stderr)
            return 1
        Recorder(sim=(cmd == "sim" or "--sim" in argv[2:])).run()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 6: Run the test to see it pass**

Run: rsync, then `python3 test/cams_test.py` on the box.

Expected: every check ok. The last section runs for about 8 s on the box, with its VA-API.

If "every whole segment is 2 s long" fails, print `_durs`, compare against the tablet facts in Global constraints, and fix `ffmpeg_args`. Do not loosen the tolerance.

- [ ] **Step 7: The unit and the CLI**

`share/systemd/omacar-cams.service`:

```ini
[Unit]
Description=OmaCar cameras — front, rear and cabin, in one-minute clips that loop
Documentation=file:__ROOT__/lib/cams.py
After=graphical-session.target
# NO START LIMIT: the lesson of the drive-day fixes. systemd's default gives up
# after five restarts in ten seconds and parks the unit in `failed`, which turns
# a camera that browned out at a set of lights into a dashcam that records
# nothing for the rest of the day. In [Unit], where systemd reads it.
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
Type=simple
# Stdlib only, so the system python would do; env.sh is sourced like every
# sibling unit, so OMACAR_PY resolves the same way everywhere.
ExecStart=/bin/sh -c '. __ROOT__/lib/env.sh; exec "$OMACAR_PY" __ROOT__/lib/cams.py run'
Restart=always
RestartSec=5
# The cameras are not urgent work: the compositor and the page come first.
Nice=10

[Install]
WantedBy=default.target
```

In `bin/omacar`:
- In the usage comment at the top, after the two `omacar kiosk` / `omacar cockpit` entries (just before `#   omacar tablet [setup]`), add:

```bash
#   omacar cams status|on|off|sim
#                             the dashcams: front, rear and cabin, in clips that loop
```

- In the `case`, after the `assets)` line, add:

```bash
  cams) shift; exec python3 "$ROOT/lib/cams.py" "$@" ;;
```

Check: `ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && bin/omacar cams status'` (after rsync). Expected: `recorder  off`, and a `cabin` line naming `usb-046d_HD_Pro_Webcam_C920_F65398FF-video-index0` with `recorder off` under it.

- [ ] **Step 8: Run everything**

Run `BOXTEST`. Expected: `cams_test.py` green; the guards' cheatsheet section still passes (it files `omacar cams` under "Everything else"); nothing regresses against the baseline.

- [ ] **Step 9: Commit**

```bash
cd /Users/jmyers/omgarchy/omacar-cameras
git add lib/camstore.py lib/cams.py share/systemd/omacar-cams.service bin/omacar test/cams_test.py test/all.sh
git commit -m "The tablet records its cameras in one-minute clips that loop, and locks the minute around a hard stop" -m "One ffmpeg per camera decodes once and splits two ways: h264_vaapi clips with passthrough frame timing and keyframes forced at each boundary, as measured on the tablet, and a 10 fps live picture this process writes atomically. Cameras are found by their by-id names, because the IPU6 driver owns video0-63. Hard braking is read from the car's own speed; locked clips leave the loop's reach.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The recorder's API

The JSON routes go through `lib/api.py` like every other route. The live stream and the clip cannot, because `handle_get` answers with a JSON tuple. They are served in `lib/serve.py`, the way `/api/phone/video` already is.

**Files:**
- Create: `lib/camroutes.py`, `test/camserve_test.py`
- Modify: `lib/api.py` (one block at the end), `lib/serve.py` (`import re`, two routes in `do_GET`, two methods), `test/all.sh` (the cameras block)

**Interfaces:**
- Consumes: `cams.overview()`, `cams.stream_live()`, `cams.ROLES`, `cams.BOUNDARY`, `camstore.list_clips()`, `list_events()`, `mark()`, `clip_path()` and `parse_range()` (all Task 1).
- Produces:
  - `camroutes.handle_get(path, query)` and `camroutes.handle_post(path, body)`, each returning `(status, payload)` or `None`. Tasks 5 and 10 add their routes here.
  - `GET /api/cams/clips` → `{clips: [{role, file, start, end, size, locked}], events: [event], now}`.
  - `POST /api/cams/mark` and `POST /api/cams/lock` accept `{t?}` (epoch seconds, within the last week) and return the event.
  - `GET /api/cams/<role>/live[?frames=N]` and `GET /api/cams/clip/<role>/<file>` (Range: 200, 206 or 416).

- [ ] **Step 1: Write the failing test**

`test/camserve_test.py`:

```python
#!/usr/bin/env python3
"""The recorder's API through the real server: the JSON routes, the live
MJPEG, and clips with HTTP Range -- which lib/serve.py never had, and which a
<video> needs before it can seek. Scratch folders only; no camera, no
recorder."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import free_port, python_for_server  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="omacar-camserve-")
ENV = dict(os.environ,
           OMACAR_VIDEOS=os.path.join(SCRATCH, "videos"),
           XDG_RUNTIME_DIR=os.path.join(SCRATCH, "run"),
           XDG_CONFIG_HOME=os.path.join(SCRATCH, "config"),
           XDG_STATE_HOME=os.path.join(SCRATCH, "state"),
           XDG_DATA_HOME=os.path.join(SCRATCH, "data"))
os.environ.update(ENV)

import cams      # noqa: E402
import camstore  # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


PORT = free_port()
SRV = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
                        str(PORT), os.path.join(ROOT, "share")],
                       env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(80):
    time.sleep(0.25)
    try:
        with socket.create_connection(("127.0.0.1", PORT), 0.25):
            break
    except OSError:
        continue


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


try:
    head("a clip, whole and in ranges")
    T = time.time() - 120
    NAME = camstore.clip_name(T)
    DATA = bytes(range(256)) * 4 + b"x" * 1000          # 2024 bytes
    os.makedirs(os.path.join(ENV["OMACAR_VIDEOS"], "front"))
    with open(os.path.join(ENV["OMACAR_VIDEOS"], "front", NAME), "wb") as f:
        f.write(DATA)
    URL = f"/api/cams/clip/front/{NAME}"
    st, h, body = req("GET", URL)
    check("the whole clip, and it says ranges are welcome",
          (st, len(body), h.get("accept-ranges"), h.get("content-type")),
          (200, 2024, "bytes", "video/mp4"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=0-99"})
    check("the first hundred bytes", (st, body, h.get("content-range")),
          (206, DATA[:100], "bytes 0-99/2024"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=2000-"})
    check("from an offset to the end", (st, body, h.get("content-range")),
          (206, DATA[2000:], "bytes 2000-2023/2024"))
    st, h, body = req("GET", URL, headers={"Range": "bytes=-24"})
    check("the last 24 bytes", (st, body), (206, DATA[-24:]))
    check("an end past the size is clipped to it",
          req("GET", URL, headers={"Range": "bytes=2020-9999"})[2], DATA[2020:])
    st, h, _ = req("GET", URL, headers={"Range": "bytes=5000-"})
    check("a start past the end is 416, with the size", (st, h.get("content-range")),
          (416, "bytes */2024"))
    check("a multi-range request gets the whole file",
          camstore.parse_range("bytes=0-1,5-6", 100), None)
    check("a path that climbs out of the folder is refused",
          req("GET", "/api/cams/clip/front/..%2F..%2Fevents.json")[0], 404)
    check("a role that does not exist is refused", req("GET", f"/api/cams/clip/boot/{NAME}")[0], 404)

    head("the JSON routes")
    st, _, body = req("GET", "/api/cams")
    ov = json.loads(body)
    check("GET /api/cams answers for every role", (st, sorted(ov["roles"])),
          (200, ["cabin", "front", "rear"]))
    check("the recorder is off, and it says so", ov["running"], False)
    check("storage counts the clip", ov["storage"]["used"], 2024)
    st, _, body = req("GET", "/api/cams/clips?role=front")
    clips = json.loads(body)["clips"]
    check("the timeline gets the clip by name, never by path",
          (st, [c["file"] for c in clips], "path" in clips[0]), (200, [NAME], False))
    check("an unknown role is a 400", req("GET", "/api/cams/clips?role=boot")[0], 400)
    st, _, body = req("POST", "/api/cams/lock", body=json.dumps({"t": T + 10}),
                      headers={"Content-Type": "application/json"})
    ev = json.loads(body)
    check("Save clip locks the window around the playhead",
          (st, ev["kind"], ev["files"]), (200, "saved", [f"front/{NAME}"]))
    check("and the clip still plays from its new folder",
          req("GET", URL, headers={"Range": "bytes=0-9"})[2], DATA[:10])
    st, _, body = req("POST", "/api/cams/mark", body="{}")
    check("Mark event marks now", (st, json.loads(body)["kind"]), (200, "marked"))
    check("a time that is not a number is refused",
          req("POST", "/api/cams/lock", body=json.dumps({"t": "soon"}))[0], 400)
    check("and so is one from last month",
          req("POST", "/api/cams/lock", body=json.dumps({"t": time.time() - 40 * 86400}))[0], 400)
    st, _, body = req("GET", "/api/cams/clips")
    check("the timeline sees both events",
          sorted(e["kind"] for e in json.loads(body)["events"]), ["marked", "saved"])

    head("the live picture, as MJPEG")
    os.makedirs(cams.run_dir(), exist_ok=True)
    J1, J2 = b"\xff\xd8one\xff\xd9", b"\xff\xd8second\xff\xd9"
    with open(cams.live_path("cabin"), "wb") as f:
        f.write(J1)

    def swap():
        time.sleep(0.5)
        with open(cams.live_path("cabin") + ".tmp", "wb") as fh:
            fh.write(J2)
        os.replace(cams.live_path("cabin") + ".tmp", cams.live_path("cabin"))

    threading.Thread(target=swap).start()
    st, h, body = req("GET", "/api/cams/cabin/live?frames=2")
    check("multipart, the way an <img> reads it", (st, h.get("content-type")),
          (200, "multipart/x-mixed-replace; boundary=omacarframe"))
    check("two whole frames, each with its length",
          (body.count(b"--omacarframe\r\n"), body.count(b"Content-Length: ")), (2, 2))
    check("in the order they were written", body.index(J1) < body.index(J2), True)
    check("a camera that does not exist is a 404", req("GET", "/api/cams/boot/live")[0], 404)
finally:
    SRV.terminate()
    try:
        SRV.wait(timeout=5)
    except subprocess.TimeoutExpired:
        SRV.kill()
    shutil.rmtree(SCRATCH, ignore_errors=True)

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the recorder's API holds\n")
```

Add it to the cameras block in `test/all.sh`, after the `cams_test.py` line:

```bash
python3 "$ROOT/test/camserve_test.py" || fails=$((fails + 1))
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/camserve_test.py` on the box.

Expected: the clip requests come back 404 (`no such endpoint`), and `GET /api/cams` fails to parse, because it is also a 404.

- [ ] **Step 3: Write lib/camroutes.py**

```python
#!/usr/bin/env python3
"""The routes the cameras branch adds. lib/api.py tries these first (see the
redesign/cameras block at the end of that file).

    GET  /api/cams                         per role: device, mode, recording, real
                                           fps, clip count, storage, last error
    GET  /api/cams/clips?role=&from=&to=   the clips and events for the timeline
    POST /api/cams/mark                    Mark event: 30 s either side of now
    POST /api/cams/lock                    Save clip: 30 s either side of `t` (the
                                           playhead), or of now

Two more are served by lib/serve.py itself, because neither is JSON:
    GET  /api/cams/<role>/live             multipart MJPEG, 10 fps
    GET  /api/cams/clip/<role>/<file>      the clip, with HTTP Range
"""

import json
import os
import sys
import time
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cams      # noqa: E402
import camstore  # noqa: E402


def _one(q, name):
    v = q.get(name)
    return v[0] if v else None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _body(body):
    try:
        data = json.loads(body or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _speed():
    try:
        import records
        s = records.live()
    except Exception:                                          # noqa: BLE001
        return None
    v = (s.get("values") or {}).get("SPEED") if s.get("connected") else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def handle_get(path, query):
    if path == "/api/cams":
        return 200, cams.overview()
    if path == "/api/cams/clips":
        q = parse_qs(query or "")
        role = _one(q, "role")
        if role and role not in camstore.ROLES:
            return 400, {"error": f"no camera role called {role!r}"}
        t0, t1 = _num(_one(q, "from")), _num(_one(q, "to"))
        return 200, {"clips": camstore.list_clips(role or None, t0, t1),
                     "events": camstore.list_events(t0, t1), "now": time.time()}
    return None


def handle_post(path, body):
    if path in ("/api/cams/mark", "/api/cams/lock"):
        data = _body(body)
        if data is None:
            return 400, {"error": "the body must be a JSON object"}
        t = data.get("t")
        if t is not None:
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                return 400, {"error": "t is seconds since the epoch"}
            if not time.time() - 7 * 86400 <= t <= time.time() + 5:
                return 400, {"error": "t is not within the last week"}
        kind = "marked" if path.endswith("/mark") else "saved"
        return 200, camstore.mark(kind, t=t, speed_kph=_speed())
    return None
```

- [ ] **Step 4: One block at the end of lib/api.py**

Append to `lib/api.py`, after the last line of `handle_post`:

```python


# ---- redesign/cameras ---------------------------------------------------------
# Cameras, the audio stage and drowsy mode route through lib/camroutes.py,
# tried before everything above. One block at the end of this file, so the
# branch that added them meets the foundation branch's edits here in one place
# (doc/design/2026-09-28-cameras-drowsy-plan.md, "Merging with the foundation
# branch").
_base_get, _base_post = handle_get, handle_post


def handle_get(path, query):  # noqa: F811
    import camroutes
    out = camroutes.handle_get(path, query)
    return out if out is not None else _base_get(path, query)


def handle_post(path, body):  # noqa: F811
    import camroutes
    out = camroutes.handle_post(path, body)
    return out if out is not None else _base_post(path, body)
# ---- end redesign/cameras -----------------------------------------------------
```

- [ ] **Step 5: The two streaming routes in lib/serve.py**

Add `import re` after `import os` in the imports.

In `do_GET`, just before the line `if path.startswith("/api/"):`, add:

```python
        if path.startswith("/api/cams/"):
            # THE TWO CAMERA ROUTES THAT ARE NOT JSON. The others go through
            # api.handle_get like everything else (lib/camroutes.py).
            m = re.fullmatch(r"/api/cams/([a-z]+)/live", path)
            if m:
                import cams
                if m.group(1) not in cams.ROLES:
                    return self._json({"error": "no such camera"}, 404)
                return self._mjpeg(m.group(1), query)
            m = re.fullmatch(r"/api/cams/clip/([a-z]+)/([0-9-]+\.mp4)", path)
            if m:
                import camstore
                real = camstore.clip_path(m.group(1), m.group(2))
                if real is None:
                    return self._json({"error": "no such clip"}, 404)
                return self._ranged(real, "video/mp4")
```

Add these two methods to `Handler`, after `_json`:

```python
    def _ranged(self, real, ctype):
        """A file with HTTP Range, which a <video> needs before it can seek.

        SimpleHTTPRequestHandler has none: it answers every request with the
        whole file and a 200. Chromium plays that but cannot seek in it, so
        back 10 s and a tap on the timeline would do nothing."""
        import camstore
        size = os.path.getsize(real)
        rng = camstore.parse_range(self.headers.get("Range"), size)
        if rng == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng if rng else (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(real, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 18, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _mjpeg(self, role, query):
        """A camera's live picture, as MJPEG an <img> shows with no decoder.

        It ends by itself after cams.LIVE_GIVE_UP seconds with no new frame,
        and after `frames=N` frames when asked (screenshots and tests).
        Connection: close is the terminator, as for /api/phone/video."""
        import cams
        from urllib.parse import parse_qs
        try:
            frames = int((parse_qs(query).get("frames") or ["0"])[0]) or None
        except ValueError:
            frames = None
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + cams.BOUNDARY)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            cams.stream_live(self.wfile, role, frames=frames)
        except (BrokenPipeError, ConnectionResetError):
            pass
```

- [ ] **Step 6: Run it to see it pass**

Run: rsync, then `python3 test/camserve_test.py`. Expected: every check ok.

Then run `BOXTEST`. Expected: nothing regresses. `app_test.py` still starts the app, and `workshop_test.py` still reaches `/api/home` and every other route through the wrapper.

- [ ] **Step 7: Commit**

```bash
git add lib/camroutes.py lib/api.py lib/serve.py test/camserve_test.py test/all.sh
git commit -m "The server hands out the recorder's live pictures, its clips with Range, and a way to keep a minute" -m "Mark event and Save clip lock thirty seconds either side of the tap, or of the playhead, on every camera. Clips are served with HTTP Range because a <video> cannot seek without it and serve.py had never needed it. The live picture is MJPEG so an <img> shows it with no decoder, and it ends itself when the pictures stop.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The Cameras tab

**Files:**
- Create: `share/js/camapi.js`, `share/js/camlogic.js`, `share/css/cameras.css`, `tools/camshot.py`, `test/js/camlogic.test.js`
- Replace: `share/js/views/cameras.js` (the placeholder)
- Modify: `share/app.html` (the `redesign/cameras` block, at the end)

**Interfaces:**
- Consumes: the Task 2 routes; `h`, `clear`, `icon`, `toast`, `withToken` from `core.js`; `ICONS.camera` and `ICONS.vehicle`.
- Produces:
  - `camapi.js`: `STILL` (true when the URL has `?still=1`), `getJSON(path)`, `postJSON(path, data)`, `liveUrl(role)`, `clipUrl(role, file)`.
  - `camlogic.js`: `ROLES`, `ROLE_LABEL`, `EVENT_LABEL`, `hhmm(t)`, `hhmmss(t)`, `resLabel(mode)`, `camBadge(ov) → {text, tone}`, `feedState(ov, role) → {title, rec, sim, live, why}`, `storageLine(storage)`, `timelineModel(clips, events, role, now, span=3600) → {t0, t1, ticks, markers, labels}`, `clipAt(clips, role, t) → {file, pos}|null`, `stepAcross(clips, role, file, pos, delta) → {file, pos}|null`.
  - The DOM the end-to-end check reads: each feed is `.cam-feed[data-rec="1"|"0"]`; the badge is `.cam-badge`; event markers are `.cam-marker`.
  - `tools/camshot.py`: `run(out, shots, doms=()) → ({name: png}, {target: dom})` and a CLI, `camshot.py OUT NAME=TARGET@W,H …`. Tasks 4, 11 and 12 use it.

- [ ] **Step 1: Write the failing test**

`test/js/camlogic.test.js`:

```js
import { eq } from "./assert.js";
import { camBadge, feedState, storageLine, timelineModel, clipAt, stepAcross } from "../js/camlogic.js";

const at = (h, m, s = 0) => new Date(2026, 8, 30, h, m, s).getTime() / 1000;
const role = (o) => Object.assign({ device: "/dev/v4l/by-id/x", mode: { fmt: "MJPG", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const ov = (roles, running = true) => ({ running, storage: { used: 12.34e9, budget: 40e9 },
  roles: Object.assign({ front: role({}), rear: role({}), cabin: role({ mode: { fmt: "MJPG", w: 640, h: 480, fps: 30 } }) }, roles) });

export default [
  ["three real cameras read LIVE · 3 cameras", () => eq(camBadge(ov({})).text, "LIVE · 3 cameras")],
  ["one simulated role makes the whole badge SIMULATED", () =>
    eq(camBadge(ov({ rear: role({ sim: true }) })).text, "SIMULATED")],
  ["one camera is singular", () =>
    eq(camBadge(ov({ rear: role({ recording: false }), cabin: role({ recording: false }) })).text, "LIVE · 1 camera")],
  ["no recorder is not recording", () => eq(camBadge(ov({}, false)).text, "NOT RECORDING")],
  ["no server says so", () => eq(camBadge(null).text, "NO SERVER")],
  ["a feed names its role and resolution", () => eq(feedState(ov({}), "front").title, "Front camera · 1080p")],
  ["the cabin at 480p", () => eq(feedState(ov({}), "cabin").title, "Cabin camera · 480p")],
  ["a recording feed is REC, with nothing to explain", () =>
    eq([feedState(ov({}), "rear").rec, feedState(ov({}), "rear").why], [true, null])],
  ["recorder off, with a camera plugged in, says how to start it", () =>
    eq(feedState(ov({}, false), "front").why, "Recorder off — omacar cams on")],
  ["recorder off with nothing plugged in says no camera", () =>
    eq(feedState(ov({ front: role({ device: null }) }, false), "front").why, "No camera")],
  ["a camera that failed says what ffmpeg said", () =>
    eq(feedState(ov({ rear: role({ recording: false, error: "VIDIOC_STREAMON: No space left on device" }) }), "rear").why,
       "VIDIOC_STREAMON: No space left on device")],
  ["storage reads used of budget", () => eq(storageLine({ used: 12.34e9, budget: 40e9 }), "12.3 of 40 GB")],
  ["the timeline puts the main camera's clips and every event on the last hour", () => {
    const clips = [{ role: "front", file: "a", start: at(9, 30), end: at(9, 31), locked: null },
                   { role: "front", file: "b", start: at(8, 30), end: at(8, 31), locked: null },
                   { role: "rear", file: "c", start: at(9, 30), end: at(9, 31), locked: null }];
    const m = timelineModel(clips, [{ id: "e", kind: "hard-braking", t: at(9, 38) }], "front", at(10, 0));
    eq(m.ticks.map((t) => [t.file, t.x.toFixed(3)]), [["a", "0.500"]]);
    eq(m.markers.map((k) => [k.label, k.x.toFixed(3)]), [["Hard braking · 09:38", "0.633"]]);
    eq([m.labels.length, m.labels[0].text, m.labels[12].text], [13, "09:00", "10:00"]);
  }],
  ["a tap on the timeline finds the clip and the second", () => {
    const clips = [{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "b", start: 1060, end: 1120 }];
    eq([clipAt(clips, "front", 1075), clipAt(clips, "front", 999), clipAt(clips, "rear", 1075)],
       [{ file: "b", pos: 15 }, null, null]);
  }],
  ["back ten seconds crosses into the previous clip", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "b", start: 1060, end: 1120 }],
                  "front", "b", 4, -10), { file: "a", pos: 54 })],
  ["forward past a gap lands on the next clip's start", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }, { role: "front", file: "c", start: 1300, end: 1360 }],
                  "front", "a", 55, 10), { file: "c", pos: 0 })],
  ["forward past the newest clip is back to live", () =>
    eq(stepAcross([{ role: "front", file: "a", start: 1000, end: 1060 }], "front", "a", 55, 10), null)],
];
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/js_test.py`. Expected: `camlogic.test.js :: import :: …`.

- [ ] **Step 3: Write camapi.js and camlogic.js**

`share/js/camapi.js`:

```js
// JSON to and from the routes the cameras branch adds (lib/camroutes.py), and
// the URLs of its two streams. core.js keeps its own list of routes; these
// live apart so this branch adds a file rather than lines to one the
// foundation branch is also editing.
import { withToken } from "./core.js";

// ?still=1: every live feed asks for one frame and stops, so a headless
// screenshot is not held open by a stream that never ends.
export const STILL = new URLSearchParams(location.search).has("still");

async function call(path, opts) {
  const r = await fetch(withToken(path), Object.assign({ cache: "no-store" }, opts || {}));
  let body = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((body && body.error) || String(r.status));
  return body;
}

export const getJSON = (path) => call(path);
export const postJSON = (path, data) => call(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}),
});
export const liveUrl = (role) => withToken(`/api/cams/${role}/live${STILL ? "?frames=1" : ""}`);
export const clipUrl = (role, file) => withToken(`/api/cams/clip/${role}/${file}`);
```

`share/js/camlogic.js`:

```js
// The Cameras tab's arithmetic, apart from the DOM so it can be tested: what
// the badge says, what each feed's caption says, and where each clip and
// event sits on the last hour's timeline.

export const ROLES = ["front", "rear", "cabin"];
export const ROLE_LABEL = { front: "Front camera", rear: "Rear camera", cabin: "Cabin camera" };
export const EVENT_LABEL = { "hard-braking": "Hard braking", marked: "Marked", saved: "Saved clip" };

const pad = (n) => String(n).padStart(2, "0");
export function hhmm(t) { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
export function hhmmss(t) { return `${hhmm(t)}:${pad(new Date(t * 1000).getSeconds())}`; }

// "1080p" from the recorder's mode; nothing when there is no mode.
export function resLabel(mode) { return mode && mode.h ? `${mode.h}p` : ""; }

// LIVE · 3 cameras, or SIMULATED when any recording role is a test picture:
// the same rule as the car's own badge.
export function camBadge(ov) {
  if (!ov) return { text: "NO SERVER", tone: "bad" };
  const rec = ROLES.filter((r) => ov.running && ov.roles && ov.roles[r] && ov.roles[r].recording);
  if (!rec.length) return { text: "NOT RECORDING", tone: "" };
  if (rec.some((r) => ov.roles[r].sim)) return { text: "SIMULATED", tone: "warn" };
  return { text: `LIVE · ${rec.length} camera${rec.length === 1 ? "" : "s"}`, tone: "ok" };
}

// A feed's caption, and, when it has no picture, why not.
export function feedState(ov, role) {
  const r = (ov && ov.roles && ov.roles[role]) || {};
  const title = [ROLE_LABEL[role], resLabel(r.mode)].filter(Boolean).join(" · ");
  const off = (why) => ({ title, rec: false, sim: false, live: false, why });
  if (!ov) return off("OmaCar cannot reach its server");
  if (!ov.running) return off(r.device ? "Recorder off — omacar cams on" : "No camera");
  if (!r.recording) return off(r.error && r.error !== "no camera" ? r.error : "No camera");
  return { title, rec: true, sim: !!r.sim, live: !!r.live, why: r.live ? null : "Waiting for the picture" };
}

// "12.3 of 40 GB".
export function storageLine(s) {
  if (!s) return "";
  return `${(s.used / 1e9).toFixed(1)} of ${Math.round(s.budget / 1e9)} GB`;
}

// The last hour, laid out: the main camera's clips as spans, every event as a
// marker, and a label every five minutes. x and w are fractions of the width.
export function timelineModel(clips, events, role, now, span = 3600) {
  const t0 = now - span;
  const x = (t) => Math.min(1, Math.max(0, (t - t0) / span));
  const ticks = clips
    .filter((c) => c.role === role && c.end > t0 && c.start < now)
    .map((c) => ({ file: c.file, x: x(c.start), w: x(c.end) - x(c.start), locked: !!c.locked }));
  const markers = events
    .filter((e) => e.t >= t0 && e.t <= now)
    .map((e) => ({ id: e.id, kind: e.kind, t: e.t, x: x(e.t), label: `${EVENT_LABEL[e.kind] || e.kind} · ${hhmm(e.t)}` }));
  const labels = [];
  for (let t = Math.ceil(t0 / 300) * 300; t <= now; t += 300) labels.push({ x: x(t), text: hhmm(t) });
  return { t0, t1: now, ticks, markers, labels };
}

// The clip of `role` that covers time t, and how far into it t is.
export function clipAt(clips, role, t) {
  const c = clips.find((k) => k.role === role && k.start <= t && t < k.end);
  return c ? { file: c.file, pos: t - c.start } : null;
}

// Ten seconds back or forward, across a clip boundary when it has to, and over
// a gap to the next clip. Past the newest clip is null: back to live.
export function stepAcross(clips, role, file, pos, delta) {
  const mine = clips.filter((c) => c.role === role).sort((a, b) => a.start - b.start);
  const cur = mine.find((c) => c.file === file);
  if (!cur) return null;
  const t = cur.start + pos + delta;
  const hit = mine.find((c) => c.start <= t && t < c.end);
  if (hit) return { file: hit.file, pos: t - hit.start };
  const next = mine.find((c) => c.start > t);
  return next ? { file: next.file, pos: 0 } : null;
}
```

- [ ] **Step 4: Run the test to see it pass**

Run: rsync, then `python3 test/js_test.py`. Expected: all green.

- [ ] **Step 5: The view**

Replace `share/js/views/cameras.js` with:

```js
// Cameras, after mockup 6: a main feed and two small feeds (tap one to swap it
// into the main slot), the last hour as a timeline of clips and events,
// playback, Save clip and Mark event, camera selection, and storage.
//
// NOTHING HERE OPENS A CAMERA. A V4L2 device has one owner and it is the
// recorder (lib/cams.py). The feeds are its live pictures as MJPEG, which an
// <img> shows with no decoder; playback is its clips through a <video>, which
// can seek because the server answers Range requests. Three feeds and drowsy
// mode's cabin stream hold four of the six connections Chromium allows to one
// host, which leaves two for everything else: do not add a fifth stream.

import { h, clear, icon, toast } from "../core.js";
import { ICONS } from "../icons.js";
import { getJSON, postJSON, liveUrl, clipUrl } from "../camapi.js";
import { ROLES, ROLE_LABEL, camBadge, feedState, storageLine, timelineModel, clipAt,
         stepAcross, hhmm, hhmmss } from "../camlogic.js";

// Transport and settings glyphs in the style of the Lucide set the app already
// uses (share/js/icons.js), kept here so this branch adds a file rather than
// lines to icons.js.
const G = {
  play: ["M6 3 20 12 6 21z"],
  pause: ["M14 4h4v16h-4z", "M6 4h4v16H6z"],
  back: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  fwd: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  save: ["M9 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0z", "M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
         "M20 4 8.12 15.88", "M14.47 14.48 20 20", "M8.12 8.12 12 12"],
  mark: ["m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"],
  mute: ["M11 5 6 9H2v6h4l5 4z", "m22 9-6 6", "m16 9 6 6"],
  disk: ["M22 12H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"],
  loop: ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5",
         "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"],
};

const pct = (x) => (x * 100).toFixed(3) + "%";

export default function camerasView(root) {
  let alive = true;
  let ov = null;
  let clips = [], events = [];
  let model = null;
  let main = "front";
  let playing = null;              // { role, file, start } while a clip plays in the main slot
  const streaming = {};            // role -> its <img> holds a live stream

  // ---- the feeds ------------------------------------------------------------
  const feeds = {};
  for (const role of ROLES) {
    const img = h("img.cam-img", { alt: `${ROLE_LABEL[role]}, live`, draggable: "false" });
    img.addEventListener("error", () => { streaming[role] = false; });
    const f = {
      img,
      title: h("span.cam-title"),
      rec: h("span.cam-rec", { hidden: true }, h("span.cam-dot"), "REC"),
      sim: h("span.cam-sim", { hidden: true }, "SIMULATED"),
      clock: h("span.cam-clock"),
      why: h("div.cam-why", { hidden: true }),
    };
    f.node = h("div.cam-feed", { data: { role, rec: "0" }, title: `Show the ${ROLE_LABEL[role].toLowerCase()} large` },
      img, f.why, h("div.cam-cap", f.rec, f.title, f.sim, f.clock));
    f.node.addEventListener("click", () => { if (role !== main) choose(role); });
    feeds[role] = f;
  }
  const video = h("video.cam-video", { playsinline: true, muted: true, preload: "auto" });
  const mainSlot = h("div.cam-main");
  const sideSlot = h("div.cam-side");
  const badge = h("span.tb-src.cam-badge");

  // ---- the timeline -----------------------------------------------------------
  const tlEvent = h("span.cam-tl-event");
  const tlSpan = h("span.cam-tl-span");
  const track = h("div.cam-track", { title: "Tap a moment to play it" });
  track.addEventListener("click", (e) => {
    if (!model) return;
    const r = track.getBoundingClientRect();
    const t = model.t0 + ((e.clientX - r.left) / r.width) * (model.t1 - model.t0);
    const hit = clipAt(clips, main, t);
    if (hit) play(main, hit.file, hit.pos); else toast("Nothing was recorded then.");
  });

  // ---- playback ---------------------------------------------------------------
  const when = h("span.cam-when", "LIVE");
  const pos = h("input.cam-scrub", { type: "range", min: "0", max: "60", step: "0.1", value: "0",
    "aria-label": "Position in the clip", disabled: true });
  pos.addEventListener("input", () => { if (playing) video.currentTime = Number(pos.value); });
  const playBtn = h("button.cam-btn.play", { type: "button", "aria-label": "Play", onclick: () => toggle() }, icon(G.play, 28));
  const liveBtn = h("button.cam-live", { type: "button", hidden: true, onclick: () => goLive() }, "Live");
  const tbtn = (g, label, fn) => h("button.cam-btn", { type: "button", "aria-label": label, title: label, onclick: fn }, icon(g, 26));
  const act = (g, label, fn) => h("button.cam-act", { type: "button", onclick: fn }, icon(g, 22), h("span", label));
  const muteBtn = h("button.cam-act", { type: "button", disabled: true, title: "No audio recorded" },
    icon(G.mute, 22), h("span", "Mute"), h("span.cam-act-note", "No audio recorded"));

  // ---- selection and storage --------------------------------------------------
  const selBtns = ROLES.map((r) => h("button.cam-pick", { type: "button", data: { role: r }, "aria-pressed": "false",
    onclick: () => choose(r) }, icon(ICONS.camera, 22), h("span", r[0].toUpperCase() + r.slice(1))));
  const meter = h("span.cam-meter-fill");
  const storage = h("span.cam-store-v");

  root.appendChild(h("div.cams",
    h("div.cams-feeds", mainSlot, sideSlot),
    h("div.card.cam-timeline", h("div.cam-tl-head", h("span.cam-tl-title", "Event timeline"), tlEvent, badge, tlSpan), track),
    h("div.card.cam-playbar",
      h("div.cam-scrubrow", when, pos),
      h("div.cam-transport", tbtn(G.back, "Back 10 seconds", () => step(-10)), playBtn,
        tbtn(G.fwd, "Forward 10 seconds", () => step(10)), liveBtn),
      h("div.cam-actions", act(G.save, "Save clip", saveClip), act(G.mark, "Mark event", markEvent), muteBtn)),
    h("div.cams-foot",
      h("div.card.cam-select", h("div.cam-card-t", "Camera selection"), h("div.cam-picks", ...selBtns)),
      h("div.card.cam-settings", h("div.cam-card-t", "Settings & storage"),
        h("div.cam-row", icon(G.disk, 20), h("span.cam-row-l", "Storage"), h("span.cam-meter", meter), storage),
        h("div.cam-row", icon(G.loop, 20), h("span.cam-row-l", "Loop recording"), h("span.cam-row-v.on", "On")),
        h("div.cam-row.is-off", icon(ICONS.vehicle, 20), h("span.cam-row-l", "Parking watch"),
          h("span.cam-row-v", "Off · coming later"))))));

  function choose(role) {
    if (playing) goLive();
    main = role;
    place();
  }

  // Placed by moving nodes, never rebuilding them: a feed keeps its stream.
  function place() {
    mainSlot.appendChild(feeds[main].node);
    for (const r of ROLES) if (r !== main) sideSlot.appendChild(feeds[r].node);
    for (const r of ROLES) feeds[r].node.classList.toggle("is-main", r === main);
    for (const b of selBtns) b.setAttribute("aria-pressed", String(b.dataset.role === main));
    drawTimeline();
  }

  function paint() {
    const b = camBadge(ov);
    badge.textContent = b.text;
    badge.className = "tb-src cam-badge" + (b.tone ? " " + b.tone : "");
    for (const role of ROLES) {
      const s = feedState(ov, role), f = feeds[role];
      f.title.textContent = s.title;
      f.rec.hidden = !s.rec;
      f.sim.hidden = !s.sim;
      f.why.hidden = !s.why;
      f.why.textContent = s.why || "";
      f.node.dataset.rec = s.rec ? "1" : "0";
      if (s.live && !streaming[role]) { f.img.src = liveUrl(role); streaming[role] = true; }
      if (!s.live && streaming[role]) { f.img.removeAttribute("src"); streaming[role] = false; }
    }
    const st = ov && ov.storage;
    storage.textContent = storageLine(st);
    meter.style.width = st && st.budget ? Math.min(100, (100 * st.used) / st.budget) + "%" : "0";
  }

  function paintPlay() {
    const going = !!playing && !video.paused;
    clear(playBtn);
    playBtn.appendChild(icon(going ? G.pause : G.play, 28));
    playBtn.setAttribute("aria-label", going ? "Pause" : "Play");
    liveBtn.hidden = !playing;
    pos.disabled = !playing;
    when.textContent = playing ? hhmmss(playing.start + video.currentTime) : "LIVE";
  }

  function drawTimeline() {
    model = timelineModel(clips, events, main, Date.now() / 1000);
    clear(track);
    for (const k of model.ticks) {
      track.appendChild(h("span.cam-tick" + (k.locked ? ".locked" : ""),
        { style: { left: pct(k.x), width: pct(Math.max(k.w, 0.002)) } }));
    }
    for (const l of model.labels) track.appendChild(h("span.cam-label", { style: { left: pct(l.x) } }, l.text));
    for (const e of model.markers) {
      track.appendChild(h("span.cam-marker", { data: { kind: e.kind }, title: e.label, style: { left: pct(e.x) } }));
    }
    if (playing) {
      const x = (playing.start + video.currentTime - model.t0) / (model.t1 - model.t0);
      track.appendChild(h("span.cam-head", { style: { left: pct(Math.min(1, Math.max(0, x))) } }));
    }
    const last = model.markers[model.markers.length - 1];
    tlEvent.textContent = last ? last.label : "No events in the last hour";
    tlEvent.classList.toggle("none", !last);
    tlSpan.textContent = `${hhmm(model.t0)} – ${hhmm(model.t1)}`;
  }

  function tickClock() {
    const now = Date.now() / 1000;
    for (const role of ROLES) {
      const s = playing && playing.role === role ? hhmmss(playing.start + video.currentTime) : hhmmss(now);
      if (feeds[role].clock.textContent !== s) feeds[role].clock.textContent = s;
    }
    if (playing) {
      pos.value = String(video.currentTime);
      when.textContent = hhmmss(playing.start + video.currentTime);
    }
  }

  async function refresh() {
    try { ov = await getJSON("/api/cams"); } catch { ov = null; }
    if (alive) paint();
  }

  async function refreshClips() {
    const from = Math.floor(Date.now() / 1000) - 3600;
    try {
      const d = await getJSON(`/api/cams/clips?from=${from}`);
      clips = d.clips;
      events = d.events;
    } catch { /* keep what was there */ }
    if (alive) drawTimeline();
  }

  function play(role, file, at) {
    const c = clips.find((k) => k.role === role && k.file === file);
    if (!c) return;
    playing = { role, file, start: c.start };
    const f = feeds[role];
    f.node.appendChild(video);
    f.node.classList.add("is-playing");
    video.src = clipUrl(role, file);
    video.addEventListener("loadedmetadata", () => {
      video.currentTime = Math.max(0, Math.min(at, (video.duration || at) - 0.1));
      video.play().catch(() => {});
    }, { once: true });
    pos.max = String(Math.max(1, c.end - c.start));
    paintPlay();
    drawTimeline();
  }

  function goLive() {
    if (!playing) return;
    const f = feeds[playing.role];
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.remove();
    f.node.classList.remove("is-playing");
    playing = null;
    paintPlay();
    drawTimeline();
  }

  // Play, from live, replays the last ten seconds: the one thing a dashcam's
  // play button can mean while it is already showing the present.
  function toggle() {
    if (!playing) { step(-10); return; }
    if (video.paused) video.play().catch(() => {}); else video.pause();
  }

  async function step(delta) {
    if (!playing) {
      await refreshClips();
      const hit = clipAt(clips, main, Date.now() / 1000 + delta);
      if (hit) play(main, hit.file, hit.pos); else toast("Nothing recorded to go back to yet.");
      return;
    }
    const next = stepAcross(clips, playing.role, playing.file, video.currentTime, delta);
    if (!next) { goLive(); return; }
    if (next.file === playing.file) video.currentTime = next.pos;
    else play(playing.role, next.file, next.pos);
  }

  video.addEventListener("play", paintPlay);
  video.addEventListener("pause", paintPlay);
  // On to the next minute, or back to live after the newest.
  video.addEventListener("ended", () => {
    if (!playing) return;
    const later = clips.filter((c) => c.role === playing.role && c.start > playing.start)
      .sort((a, b) => a.start - b.start);
    if (later.length) play(playing.role, later[0].file, 0); else goLive();
  });

  async function saveClip() {
    const t = playing ? playing.start + video.currentTime : Date.now() / 1000;
    try {
      await postJSON("/api/cams/lock", { t });
      toast(`Saved: the minute around ${hhmmss(t)} is kept, on every camera.`);
      refreshClips();
    } catch (e) { toast("Could not save the clip: " + e.message, "bad"); }
  }

  async function markEvent() {
    try {
      await postJSON("/api/cams/mark", {});
      toast("Marked. The minute around now is kept, on every camera.");
      refreshClips();
    } catch (e) { toast("Could not mark the event: " + e.message, "bad"); }
  }

  place();
  paintPlay();
  refresh();
  refreshClips();
  const timers = [setInterval(refresh, 2000), setInterval(refreshClips, 10000),
                  setInterval(tickClock, 250), setInterval(drawTimeline, 5000)];

  return () => {
    alive = false;
    for (const t of timers) clearInterval(t);
    for (const r of ROLES) feeds[r].img.removeAttribute("src");
    video.pause();
    video.removeAttribute("src");
    video.load();
  };
}
```

- [ ] **Step 6: The stylesheet, and the one block in app.html**

`share/css/cameras.css`:

```css
/* The Cameras tab (views/cameras.js) and Home's Dashcams card (dashcard.js),
   after mockup 6. Linked from the redesign/cameras block at the end of
   app.html; every selector here belongs to something this branch adds. */

.cams [hidden], .hc-cam [hidden] { display: none !important; }
.wrap:has(> .cams) { max-width: none; }
.cams { display: grid; gap: 12px; }

/* Landscape: the main feed takes two thirds and the other two stack in the
   last third, all within 40% of the screen's height, as mockup 6 has it.
   Pictures fill their boxes (object-fit: cover), so the 4:3 cabin crops
   rather than letterboxes. */
.cams-feeds { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; height: 40vh; min-height: 280px; }
.cam-main, .cam-side { display: grid; gap: 12px; min-width: 0; min-height: 0; }
.cam-side { grid-template-rows: 1fr 1fr; }
.cam-feed {
  position: relative; overflow: hidden; min-width: 0; min-height: 0;
  border-radius: var(--r); border: 1px solid var(--hair); background: #000; cursor: pointer;
}
.cam-feed.is-main { cursor: default; }
.cam-img, .cam-video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.cam-img:not([src]) { visibility: hidden; }
.cam-video { z-index: 1; background: #000; }
/* Captions sit on the picture, so they are light on a dark scrim in every look. */
.cam-cap {
  position: absolute; inset: 0 0 auto 0; z-index: 2; display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; font-size: .8rem; color: #F6FCFF;
  background: linear-gradient(180deg, rgba(0, 0, 0, .6), transparent);
}
.cam-title { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.cam-rec { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px; border-radius: 999px;
           background: rgba(0, 0, 0, .55); font-weight: 600; letter-spacing: .06em; }
.cam-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--rec); }
.cam-sim { padding: 3px 8px; border-radius: 999px; background: rgba(0, 0, 0, .55); color: var(--warn);
           font-size: .68rem; letter-spacing: .12em; }
.cam-clock { font-variant-numeric: tabular-nums; }
.cam-why { position: absolute; inset: 0; display: grid; place-items: center; padding: 16px;
           text-align: center; color: var(--dim); font-size: .9rem; background: var(--panel); }
.cam-feed.is-playing .cam-rec { display: none; }

/* The last hour. */
.cam-timeline { display: grid; gap: 10px; padding: 14px 16px; }
.cam-tl-head { display: flex; align-items: center; gap: 12px; font-size: .85rem; color: var(--ink-2); }
.cam-tl-title { color: var(--ink); }
.cam-tl-event { flex: 1; color: var(--rec); }
.cam-tl-event.none { color: var(--dim); }
.cam-tl-span { color: var(--dim); font-variant-numeric: tabular-nums; }
.cam-track { position: relative; height: 52px; border-radius: 10px; background: var(--panel-2); cursor: pointer; }
.cam-tick { position: absolute; top: 12px; height: 20px; border-left: 1px solid var(--panel-2);
            background: color-mix(in srgb, var(--accent) 35%, transparent); }
.cam-tick.locked { background: color-mix(in srgb, var(--rec) 45%, transparent); }
.cam-marker { position: absolute; top: 4px; width: 12px; height: 12px; margin-left: -6px;
              border-radius: 50%; background: var(--rec); }
.cam-label { position: absolute; bottom: 4px; transform: translateX(-50%); font-size: .68rem;
             color: var(--dim); font-variant-numeric: tabular-nums; }
.cam-head { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--accent); }

/* Playback. */
.cam-playbar { display: grid; gap: 10px; padding: 14px 16px; grid-template-columns: 1fr auto; align-items: center; }
.cam-scrubrow { grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; font-size: .85rem; }
.cam-scrub { flex: 1; min-height: var(--tap); accent-color: var(--accent); }
.cam-when { min-width: 8ch; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.cam-transport, .cam-actions { display: flex; align-items: center; gap: 10px; }
.cam-btn, .cam-live, .cam-act, .cam-pick {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-width: var(--tap-lg); min-height: var(--tap-lg); padding: 0 14px;
  border: 1px solid var(--edge); border-radius: var(--r); background: var(--panel-2); color: var(--ink); font: inherit;
}
.cam-btn.play { width: 64px; height: 64px; padding: 0; border-radius: 50%; }
.cam-act { flex-direction: column; min-width: 96px; padding: 8px 14px; font-size: .8rem; }
.cam-act:disabled { opacity: .5; cursor: default; }
.cam-act-note { font-size: .66rem; color: var(--dim); }

/* Selection and storage. */
.cams-foot { display: grid; grid-template-columns: 1fr 2fr; gap: 12px; }
.cam-select, .cam-settings { display: grid; gap: 10px; align-content: start; padding: 14px 16px; }
.cam-card-t { font-size: .9rem; color: var(--ink-2); }
.cam-picks { display: flex; gap: 10px; }
.cam-pick { flex: 1; flex-direction: column; padding: 10px; font-size: .8rem; }
.cam-pick[aria-pressed="true"] { background: var(--accent-fill); color: var(--on-accent); border-color: transparent; }
/* Display rows, not buttons: nothing here is tapped, so they need not be 48 px. */
.cam-row { display: flex; align-items: center; gap: 12px; min-height: 36px; font-size: .88rem; }
.cam-row-v { margin-left: auto; color: var(--dim); }
.cam-row-v.on { color: var(--ok); }
.cam-row.is-off { color: var(--dim); }
.cam-meter { flex: 1; height: 6px; overflow: hidden; border-radius: 3px; background: var(--track); }
.cam-meter-fill { display: block; width: 0; height: 100%; background: var(--accent); }
.cam-store-v { color: var(--ink-2); font-variant-numeric: tabular-nums; }

@media (orientation: portrait) {
  .cams-feeds { grid-template-columns: 1fr; height: auto; }
  .cam-feed { aspect-ratio: 16 / 9; }
  .cam-side { grid-template-rows: none; grid-template-columns: 1fr 1fr; }
  .cams-foot, .cam-playbar { grid-template-columns: 1fr; }
  .cam-actions .cam-act { flex: 1; }
}
```

In `share/app.html`, after the last line (`<script type="module" src="js/bootscreen.js"></script>`), add the block that Tasks 5 and 8 extend:

```html
<!-- ---- redesign/cameras ------------------------------------------------------
     The Cameras tab, Home's Dashcams card, the audio stage and drowsy mode
     (doc/design/2026-09-28-cameras-drowsy.md). One block at the end of the
     page, so this branch meets the foundation branch's edits here in one
     place. A stylesheet link is valid in the body; these style only what this
     branch adds. -->
<link rel="stylesheet" href="css/cameras.css">
<!-- ---- end redesign/cameras ------------------------------------------------ -->
```

- [ ] **Step 7: Run everything, and look at it with the C920 and two test pictures**

Run `BOXTEST`. Expected: all green. `app_test.py` still boots to Home.

Screenshots need two things a bare headless Chromium does not have: the first-run tour already seen, and live feeds that stop after one frame. `tools/camshot.py` does both. It marks the tour seen the way foundation's `tools/shoot.py` does, with a same-origin seed page in a copy of `share/`.

```python
#!/usr/bin/env python3
"""Screenshots of the app for review beside the owner's mockups: served from a
copy of share/ with the first-run tour already seen, in headless Chromium.
Not a test; it passes and fails nothing.

    python3 tools/camshot.py OUT_DIR NAME=TARGET@W,H ...

TARGET is what follows app.html?, for example `still=1#cameras`. `still=1`
asks each live feed for one frame, so a stream that never ends cannot hold the
page open. The server inherits this process's environment, so OMACAR_VIDEOS
and XDG_RUNTIME_DIR point it at a scratch recorder.

The inner height comes back 56 px short of --window-size (test/app_test.py),
so 912 of inner height needs 968 of window. The tour is marked seen the way
foundation's tools/shoot.py does it: a same-origin seed page in the copy.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "test"))
from app_test import browser, free_port, python_for_server  # noqa: E402


def run(out, shots, doms=()):
    """shots: [(name, target, "W,H")]; doms: [target]. Returns
    ({name: png path or None}, {target: DOM text})."""
    os.makedirs(out, exist_ok=True)
    exe = browser()
    if not exe:
        raise SystemExit("no chromium here")
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(os.path.join(ROOT, "share"), copy)
    with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
        f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
    port = free_port()
    srv = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    url = f"http://127.0.0.1:{port}"
    pngs, dom = {}, {}
    try:
        for _ in range(80):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        base = [exe, "--headless=new", "--no-sandbox", "--hide-scrollbars", f"--user-data-dir={prof}"]
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom", url + "/_seed.html"],
                       capture_output=True, timeout=120)
        for name, target, size in shots:
            png = os.path.join(out, name + ".png")
            subprocess.run(base + [f"--window-size={size}", "--virtual-time-budget=8000",
                                   f"--screenshot={png}", f"{url}/app.html?{target}"],
                           capture_output=True, timeout=180)
            pngs[name] = png if os.path.exists(png) else None
        for target in doms:
            dom[target] = subprocess.run(base + ["--virtual-time-budget=8000", "--dump-dom",
                                                 f"{url}/app.html?{target}"],
                                         capture_output=True, text=True, timeout=180).stdout
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return pngs, dom


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    shots = []
    for arg in argv[2:]:
        name, _, rest = arg.partition("=")
        target, _, size = rest.rpartition("@")
        shots.append((name, target, size))
    pngs, _ = run(argv[1], shots)
    for name, png in pngs.items():
        print(("  wrote  " + png) if png else ("  FAILED " + name))
    return 0 if all(pngs.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Then screenshot the tab with the recorder running on scratch folders:

```bash
rsync -a --delete --exclude .git --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar-cameras/ jmyers@omarchy:Projects/.omacar-test/cameras/
ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && S=/tmp/omacar-shot && rm -rf $S && mkdir -p $S/videos && mkdir -p -m 700 $S/run && export OMACAR_VIDEOS=$S/videos XDG_RUNTIME_DIR=$S/run && (setsid -f python3 lib/cams.py sim >$S/cams.log 2>&1) && sleep 75 && python3 tools/camshot.py $S/out cameras-landscape=still=1#cameras@1368,968 cameras-portrait=still=1#cameras@912,1424; pkill -INT -f "[l]ib/cams.py sim"; sleep 3; ls $S/out'
scp 'jmyers@omarchy:/tmp/omacar-shot/out/*.png' "$SCRATCH/"
```

The `[l]` in the `pkill` pattern keeps it from matching the ssh shell's own command line, which contains the same words.

Open both next to `/Users/jmyers/omgarchy/omacar/doc/design/mockups/6.webp`. Check:
- the main feed and two small feeds (the cabin is the real C920; front and rear are test pictures, each labelled SIMULATED);
- REC, role, resolution and clock on each;
- the badge reads `SIMULATED`;
- the timeline has a minute of clip for the main camera;
- the transport, with Save clip, Mark event and a disabled Mute ("No audio recorded");
- camera selection;
- storage, loop recording On, parking watch "Off · coming later".

Fix spacing in `cameras.css` before committing. Then remove the scratch: `ssh jmyers@omarchy 'rm -rf /tmp/omacar-shot'`.

- [ ] **Step 8: Commit**

```bash
git add share/js/camapi.js share/js/camlogic.js share/js/views/cameras.js share/css/cameras.css share/app.html tools/camshot.py test/js/camlogic.test.js
git commit -m "The Cameras tab shows three live feeds, the last hour, and plays back any minute of it" -m "Mockup 6: a main feed with two small ones to swap in, a timeline of clips and events where a tap plays that moment, back and forward ten seconds across clip boundaries, Save clip and Mark event. The badge says SIMULATED when any feed is a test picture. Nothing here opens a camera; the recorder owns them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Home's Dashcams card

**Files:**
- Create: `share/js/dashcard.js`
- Modify: `share/js/camlogic.js` (add `dashState`), `test/js/camlogic.test.js`, `share/css/cameras.css` (append), `share/js/views/home.js` (one hunk: the `dashcam:` line in `MAKERS`)

**Interfaces:**
- Consumes: `getJSON`, `liveUrl` (Task 3); `feedState` (Task 3); `tappable`, `h` in `home.js`.
- Produces:
  - `dashState(ov) → {live, rec, sim, why}` in `camlogic.js`.
  - `dashcamCard(node) → {top, paint(), destroy()}` in `dashcard.js`. `top` is the caption row, which Task 11 adds the drowsy chip to.

- [ ] **Step 1: Write the failing test**

Add to `test/js/camlogic.test.js`: put `dashState` in the import list, and add these entries to the default export's array:

```js
  ["Home's card shows the front picture with REC while recording", () =>
    eq(dashState(ov({})), { live: true, rec: true, sim: false, why: null })],
  ["and says SIMULATED over a test picture", () => eq(dashState(ov({ front: role({ sim: true }) })).sim, true)],
  ["recorder off, with the front camera plugged in", () => eq(dashState(ov({}, false)).why, "Recorder off")],
  ["no front camera at all", () =>
    eq(dashState(ov({ front: role({ device: null, recording: false, error: "no camera" }) })).why, "No front camera")],
  ["and no server at all", () => eq(dashState(null).why, "OmaCar cannot reach its server")],
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/js_test.py`. Expected: `camlogic.test.js :: import :: … dashState`.

- [ ] **Step 3: dashState, the card, and its styles**

Append to `share/js/camlogic.js`:

```js
// Home's Dashcams card: the front picture, or in words why there is none.
export function dashState(ov) {
  const off = (why) => ({ live: false, rec: false, sim: false, why });
  if (!ov) return off("OmaCar cannot reach its server");
  const r = (ov.roles && ov.roles.front) || {};
  if (!ov.running) return off(r.device ? "Recorder off" : "No front camera");
  if (!r.recording) return off(!r.error || r.error === "no camera" ? "No front camera" : r.error);
  return { live: !!r.live, rec: true, sim: !!r.sim, why: r.live ? null : "Waiting for the picture" };
}
```

`share/js/dashcard.js`:

```js
// Home's Dashcams card: the front camera's live picture with a REC dot while
// the recorder runs, and in words why not when it does not -- no camera, or
// recorder off -- so a black rectangle never stands in for a reason. A test
// picture says SIMULATED, the same rule as the car's numbers.
//
// home.js makes the card's node (a tap opens Cameras) and imports this module
// where the card is made: see the redesign/cameras block in its MAKERS.
import { h } from "./core.js";
import { getJSON, liveUrl } from "./camapi.js";
import { dashState } from "./camlogic.js";

export function dashcamCard(node) {
  const img = h("img.dc-img", { alt: "Front camera, live", draggable: "false", hidden: true });
  const why = h("div.dc-why");
  const rec = h("span.dc-rec", { hidden: true }, h("span.dc-dot"), "REC");
  const sim = h("span.dc-sim", { hidden: true }, "SIMULATED");
  const top = h("div.dc-top", h("span.dc-title", "Dashcams"), sim, rec);
  node.append(h("div.dc-stage", img, why), top);
  let streaming = false, dead = false;
  img.addEventListener("error", () => { streaming = false; });

  async function poll() {
    let ov = null;
    try { ov = await getJSON("/api/cams"); } catch { /* dashState says so */ }
    if (dead) return;
    const s = dashState(ov);
    rec.hidden = !s.rec;
    sim.hidden = !s.sim;
    why.hidden = !s.why;
    why.textContent = s.why || "";
    if (s.live && !streaming) { img.src = liveUrl("front"); img.hidden = false; streaming = true; }
    if (!s.live && streaming) { img.removeAttribute("src"); img.hidden = true; streaming = false; }
  }
  poll();
  const timer = setInterval(poll, 3000);

  return {
    top,
    paint() {},
    destroy() { dead = true; clearInterval(timer); img.removeAttribute("src"); },
  };
}
```

Append to `share/css/cameras.css`:

```css
/* Home's Dashcams card. */
.hc-cam { padding: 0; background: #000; }
.dc-stage { position: absolute; inset: 0; }
.dc-img { width: 100%; height: 100%; object-fit: cover; }
.dc-why { position: absolute; inset: 0; display: grid; place-items: center; padding: 40px 16px 16px;
          text-align: center; color: var(--dim); font-size: .9rem; background: var(--panel); }
.dc-top { position: absolute; inset: 0 0 auto 0; display: flex; align-items: center; gap: 8px;
          padding: 12px 14px; color: #F6FCFF; background: linear-gradient(180deg, rgba(0, 0, 0, .6), transparent); }
.dc-title { flex: 1; font-size: .9rem; }
.dc-rec { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px; border-radius: 999px;
          background: rgba(0, 0, 0, .55); font-size: .75rem; font-weight: 600; letter-spacing: .06em; }
.dc-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--rec); }
.dc-sim { padding: 3px 8px; border-radius: 999px; background: rgba(0, 0, 0, .55); color: var(--warn);
          font-size: .66rem; letter-spacing: .12em; }
```

- [ ] **Step 4: The one hunk in home.js**

In `share/js/views/home.js`, replace the single line in `MAKERS` that begins `  dashcam: () => soonCard(ICONS.camera, "Dashcams",` with:

```js
  // ---- redesign/cameras: the live front view (share/js/dashcard.js) -----------
  // Imported where the card is made, so the cameras branch meets this file in
  // one hunk (doc/design/2026-09-28-cameras-drowsy-plan.md).
  dashcam: () => {
    const node = tappable(h("div.card.hc.hc-cam"), "cameras");
    let card = null, gone = false;
    import("../dashcard.js").then((m) => { if (!gone) card = m.dashcamCard(node); });
    return { node, paint: () => { if (card) card.paint(); },
             destroy: () => { gone = true; if (card) card.destroy(); } };
  },
  // ---- end redesign/cameras ---------------------------------------------------
```

`soonCard` stays: the Navigation card still uses it.

- [ ] **Step 5: Run everything, and look at it**

Run `BOXTEST`. Expected:
- `js_test.py` is green.
- `app_test.py` still boots to Home. The box's only camera is the C920, which is the cabin, so the card reads "No front camera".

Then repeat Task 3's screenshot command with `home-landscape=still=1#home@1368,968` as the only shot, and check that the card shows the SIMULATED front picture with REC.

- [ ] **Step 6: Commit**

```bash
git add share/js/dashcard.js share/js/camlogic.js share/css/cameras.css share/js/views/home.js test/js/camlogic.test.js
git commit -m "Home's Dashcams card shows the front camera live, or says in words why it cannot" -m "No front camera, recorder off, or a simulated picture labelled as one: a black rectangle never stands in for a reason. The card is loaded where it is made so this branch meets home.js in one hunk while the foundation branch is still editing it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The audio stage

**Files:**
- Create: `lib/audio.py`, `share/js/audiobus.js`, `share/js/audiostate.js`, `share/js/alertness.js`, `test/audio_test.py`, `test/js/audiobus.test.js`
- Modify: `lib/camroutes.py` (`/api/audio`), `bin/omacar` (usage and the `audio)` case), `share/js/radio.js` (joins the stage), `share/app.html` (inside the cameras block), `test/all.sh` (the cameras block)

**Interfaces:**
- Consumes: `postJSON` (Task 3), `camroutes` (Task 2).
- Produces:
  - `audio.py`: `status() → {volume, muted, port: "aux"|"speakers"|"other"|"unknown", port_name, aux: true|false|null, managed, error}`, `pin()`, `apply()`, `managed()`, `set_managed(on)`, `parse_volume(text)`, `port_of(sinks, default)`. CLI `omacar audio status|on|off|pin`.
  - `GET /api/audio` → `status()`; `POST /api/audio {"action": "apply"}` → `apply()`.
  - `audiobus.js` levels: `MUSIC_DB = -12`, `ALERT_MAX_DB = 0`, `FLOOR_DB = -60`, `dbToGain(db)`, `gainToDb(g)`, `headroomDb(musicDb = -12, alertDb = 0)`.
  - `audiobus.js` graph: `audioContext()`, `musicIn()`, `alertIn()`, `resume()`, `currentDb(bus)`, `schedule(bus, points, at, append=false)`, `setLevelNow(bus, db)`, `outputDb()`. A bus is `"music"` or `"alert"`, and points are `[seconds, dB]` pairs.
  - `audiostate.js`: `audio.last`, `onAudio(fn) → off()`, `applyAudio()`, `auxLine(status)`.

- [ ] **Step 1: Write the failing tests**

`test/audio_test.py`:

```python
#!/usr/bin/env python3
"""The Surface's volume, held at 100% where it was asked for and nowhere else:
reading wpctl and pactl, naming the port, and apply() deciding whether to
touch anything. wpctl never runs for real here -- _run is replaced -- so this
suite cannot turn anybody's speakers up."""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
SCRATCH = tempfile.mkdtemp(prefix="omacar-audio-test-")
os.environ["XDG_CONFIG_HOME"] = SCRATCH

import audio  # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


head("reading wpctl")
check("a plain volume", audio.parse_volume("Volume: 0.25\n"), (0.25, False))
check("muted", audio.parse_volume("Volume: 1.00 [MUTED]\n"), (1.0, True))
check("nothing readable is nothing", audio.parse_volume("error"), (None, None))

head("naming the port")
SINK = "alsa_output.pci-0000_00_1f.3.analog-stereo"
SINKS = [{"name": SINK, "active_port": "analog-output-headphones",
          "ports": [{"name": "analog-output-speaker", "description": "Speakers",
                     "availability": "availability unknown"},
                    {"name": "analog-output-headphones", "description": "Headphones",
                     "availability": "available"}]}]
check("headphones are the AUX cable", audio.port_of(SINKS, SINK), ("aux", "Headphones"))
check("the speakers are the speakers",
      audio.port_of([dict(SINKS[0], active_port="analog-output-speaker")], SINK),
      ("speakers", "Speakers"))
check("so is a UCM-style port name",
      audio.port_of([{"name": "x", "active_port": "[Out] Headphones",
                      "ports": [{"name": "[Out] Headphones", "description": "Headphones"}]}], "x"),
      ("aux", "Headphones"))
check("an unknown default sink is unknown, never AUX", audio.port_of(SINKS, "other"), ("unknown", ""))

head("apply() touches the volume only where it was asked to")
VOL = ["Volume: 0.25\n"]
CALLS = []


def fake(args, timeout=5):
    CALLS.append(args)
    if args[:2] == ["wpctl", "get-volume"]:
        return 0, VOL[0]
    if args[:2] == ["pactl", "get-default-sink"]:
        return 0, SINK + "\n"
    if args[:3] == ["pactl", "--format=json", "list"]:
        return 0, json.dumps(SINKS)
    return 0, ""


audio._run = fake
SETS = lambda: [c for c in CALLS if len(c) > 1 and c[1] in ("set-mute", "set-volume")]  # noqa: E731
_st = audio.apply()
check("not asked: nothing is set", (SETS(), _st["managed"], _st["aux"], _st["volume"]), ([], False, True, 0.25))
audio.set_managed(True)
CALLS.clear()
audio.apply()
check("asked: unmuted and set to 100%", SETS(),
      [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
       ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"]])
VOL[0] = "Volume: 1.00\n"
CALLS.clear()
audio.apply()
check("already at 100%: nothing is set again", SETS(), [])
audio.set_managed(False)
check("and off again", audio.managed(), False)

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  the audio path holds\n")
```

Add to the cameras block in `test/all.sh`:

```bash
python3 "$ROOT/test/audio_test.py" || fails=$((fails + 1))
```

`test/js/audiobus.test.js`:

```js
import { eq, ok } from "./assert.js";
import { MUSIC_DB, ALERT_MAX_DB, dbToGain, gainToDb, headroomDb } from "../js/audiobus.js";
import { auxLine } from "../js/audiostate.js";

export default [
  ["music sits 12 dB below full scale", () => eq(MUSIC_DB, -12)],
  ["alerts may use all of it", () => eq(ALERT_MAX_DB, 0)],
  ["so an alert always has 12 dB over music", () => eq(headroomDb(), 12)],
  ["whatever the car's knob says: the knob scales both alike", () => {
    for (const knob of [0.05, 0.3, 1]) {
      eq(Math.round(gainToDb(dbToGain(ALERT_MAX_DB) * knob) - gainToDb(dbToGain(MUSIC_DB) * knob)), 12, `knob ${knob}`);
    }
  }],
  ["-12 dB is a quarter of the amplitude", () => eq(dbToGain(-12).toFixed(4), "0.2512")],
  ["gain and dB round-trip", () => ok(Math.abs(gainToDb(dbToGain(-7.5)) + 7.5) < 1e-9, "round trip")],
  ["silence is zero gain, not a tiny number", () => eq([dbToGain(-Infinity), dbToGain(-200)], [0, 0])],
  ["a Level 1 swell leaves 6 dB, a Level 2 duck 24", () => eq([headroomDb(-6), headroomDb(-24)], [6, 24])],
  ["AUX disconnected only when the port is known to be the speakers", () =>
    eq([auxLine({ aux: false }), auxLine({ aux: true }), auxLine({ aux: null }), auxLine(null)],
       ["AUX disconnected — sound is on the tablet's speakers", "", "", ""])],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/audio_test.py` (expected: `No module named 'audio'`) and `python3 test/js_test.py` (expected: `audiobus.test.js :: import`).

- [ ] **Step 3: Write lib/audio.py**

```python
#!/usr/bin/env python3
"""The Surface's own volume, held so every alert keeps its headroom.

Sound reaches the car through an AUX cable from the headphone jack. In the page,
music sits 12 dB below full scale and alerts may use all of it
(share/js/audiobus.js), and that promise means nothing if the machine itself is
at 25% -- which is where the internal speakers were on 2026-09-28. So on the
tablet the default sink is held at 100% and unmuted, on whichever port is
active. With the jack unplugged that port is the speakers: Home says "AUX
disconnected", and alerts still play there at full volume.

    omacar audio status   the port, the volume, and whether it is held
    omacar audio on       hold it at 100% from now on (the tablet)
    omacar audio off      stop holding it
    omacar audio pin      set 100% once, now

Held only where `omacar audio on` was run. The app applies this at start and
every half minute, and a desk machine's speakers at 100% -- or the box's,
every time the test suite opens the app -- are not anybody's idea of help.
wpctl sets the volume; pactl names the port.
"""

import json
import os
import re
import subprocess
import sys

SINK = "@DEFAULT_AUDIO_SINK@"


def flag_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-audio.json")


def managed():
    try:
        with open(flag_path(), encoding="utf-8") as f:
            return bool(json.load(f).get("pin"))
    except (OSError, ValueError, AttributeError):
        return False


def set_managed(on):
    os.makedirs(os.path.dirname(flag_path()), exist_ok=True)
    tmp = flag_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pin": bool(on)}, f)
    os.replace(tmp, flag_path())


def parse_volume(text):
    m = re.search(r"Volume:\s*([0-9.]+)", text or "")
    if not m:
        return None, None
    return float(m.group(1)), "[MUTED]" in text


def port_of(sinks, default):
    """(kind, description) of the default sink's active port: "aux" for
    anything headphone-shaped, "speakers" for the built-in speakers, "other"
    or "unknown" otherwise."""
    for s in sinks or []:
        if s.get("name") != default:
            continue
        name = s.get("active_port") or ""
        port = next((p for p in s.get("ports") or [] if p.get("name") == name), {})
        desc = port.get("description") or name
        both = f"{name} {desc}"
        if re.search(r"head", both, re.I):
            return "aux", desc
        if re.search(r"speak", both, re.I):
            return "speakers", desc
        return ("other" if name else "unknown"), desc
    return "unknown", ""


def _run(args, timeout=5):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def status():
    rc, out = _run(["wpctl", "get-volume", SINK])
    vol, muted = parse_volume(out) if rc == 0 else (None, None)
    _, default = _run(["pactl", "get-default-sink"])
    rc2, js = _run(["pactl", "--format=json", "list", "sinks"])
    try:
        sinks = json.loads(js) if rc2 == 0 and js.strip() else []
    except ValueError:
        sinks = []
    kind, desc = port_of(sinks, (default or "").strip())
    return {"volume": vol, "muted": muted, "port": kind, "port_name": desc,
            "aux": {"aux": True, "speakers": False}.get(kind), "managed": managed(),
            "error": None if vol is not None else "wpctl could not read the volume"}


def pin():
    _run(["wpctl", "set-mute", SINK, "0"])
    _run(["wpctl", "set-volume", SINK, "1.0"])
    return status()


def apply():
    """What the app calls at start and every half minute: hold 100% where
    `omacar audio on` asked for it, and only there. Plugging the AUX cable in
    switches to a port that keeps a volume of its own, which is why this is
    asked again rather than once."""
    st = status()
    if st["managed"] and st["volume"] is not None and (st["volume"] < 0.995 or st["muted"]):
        st = pin()
    return st


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "on":
        set_managed(True)
        st = pin()
    elif cmd == "off":
        set_managed(False)
        st = status()
    elif cmd == "pin":
        st = pin()
    elif cmd == "status":
        st = status()
    else:
        print(__doc__)
        return 2
    vol = "?" if st["volume"] is None else f"{st['volume'] * 100:.0f}%"
    where = {"aux": "AUX (headphone jack)", "speakers": "the speakers: AUX disconnected"}.get(
        st["port"], st["port_name"] or "unknown")
    print(f"  output  {where}")
    print(f"  volume  {vol}{' muted' if st['muted'] else ''}"
          f"{' (held at 100%)' if st['managed'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

In `lib/camroutes.py`:
- in `handle_get`, before `return None`:

```python
    if path == "/api/audio":
        import audio
        return 200, audio.status()
```

- in `handle_post`, before `return None`:

```python
    if path == "/api/audio":
        import audio
        data = _body(body)
        if data is None or data.get("action") != "apply":
            return 400, {"error": 'the one action is {"action": "apply"}'}
        return 200, audio.apply()
```

In `bin/omacar`:
- under the `omacar cams` usage lines, add:

```bash
#   omacar audio status|on|off|pin
#                             hold the tablet at 100% so alerts keep their headroom
```

- after the `cams)` case line, add:

```bash
  audio) shift; exec python3 "$ROOT/lib/audio.py" "$@" ;;
```

- [ ] **Step 4: Write audiobus.js and audiostate.js, and join the radio to the stage**

`share/js/audiobus.js`:

```js
// ONE OUTPUT STAGE FOR EVERYTHING OMACAR PLAYS.
//
// Sound reaches the car through an AUX cable into the car's own radio, whose
// volume knob this app cannot see. So the only promise that holds whatever the
// knob says is a relative one: music sits 12 dB below full scale, alerts may
// use all of it, and an alert always has 12 dB of headroom over music. The
// knob scales both alike. lib/audio.py holds the Surface itself at 100%.
//
//   radio <audio> -> its analyser -> music gain (-12 dB) --+
//   chime, bark, alarm, voice ----> alert gain (silent) ---+-> limiter -> out
//
// Level changes are ramps (linearRampToValueAtTime) shaped by ramps.js, so no
// step is ever audible. The alert bus starts silent: every alert begins from a
// ramp, or from a level set while nothing was playing on it.

export const MUSIC_DB = -12;
export const ALERT_MAX_DB = 0;
export const FLOOR_DB = -60;        // quiet enough to count as silence

export function dbToGain(db) { return db <= -120 ? 0 : Math.pow(10, db / 20); }
export function gainToDb(g) { return g > 0 ? 20 * Math.log10(g) : -Infinity; }
export function headroomDb(musicDb = MUSIC_DB, alertDb = ALERT_MAX_DB) { return alertDb - musicDb; }

let ctx = null;
const node = {};

function ensure() {
  if (ctx) return;
  const AC = window.AudioContext || window.webkitAudioContext;
  ctx = new AC();
  node.music = ctx.createGain();
  node.music.gain.value = dbToGain(MUSIC_DB);
  node.alert = ctx.createGain();
  node.alert.gain.value = 0;
  // Music plus a full-scale alert can pass 0 dBFS by a decibel or two; the
  // limiter takes that off rather than letting the output clip.
  node.limit = ctx.createDynamicsCompressor();
  node.limit.threshold.value = -1;
  node.limit.knee.value = 0;
  node.limit.ratio.value = 20;
  node.limit.attack.value = 0.003;
  node.limit.release.value = 0.25;
  node.meter = ctx.createAnalyser();
  node.meter.fftSize = 2048;
  node.music.connect(node.limit);
  node.alert.connect(node.limit);
  node.limit.connect(node.meter);
  node.meter.connect(ctx.destination);
}

export function audioContext() { ensure(); return ctx; }
export function musicIn() { ensure(); return node.music; }
export function alertIn() { ensure(); return node.alert; }

// A context made without a tap starts suspended; the kiosk's autoplay flag
// lets it run, and Begin's tap resumes it everywhere else.
export async function resume() {
  ensure();
  if (ctx.state === "suspended") {
    try { await ctx.resume(); } catch { /* it needs a tap first */ }
  }
  return ctx.state;
}

// Where a bus is now, in dB.
export function currentDb(bus) { ensure(); return gainToDb(node[bus].gain.value); }

// Run a ramp plan's points ([seconds, dB] pairs from ramps.js) on a bus from
// `at` (the context's clock). `append` continues after what is already
// scheduled instead of replacing it.
export function schedule(bus, points, at, append = false) {
  ensure();
  const g = node[bus].gain;
  if (!append) {
    if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(at);
    else { g.cancelScheduledValues(at); g.setValueAtTime(g.value, at); }
  }
  for (const [t, db] of points) g.linearRampToValueAtTime(dbToGain(db), at + t);
}

// Set a bus that has nothing playing on it. No ramp is needed where there is
// nothing to hear.
export function setLevelNow(bus, db) {
  ensure();
  const g = node[bus].gain;
  g.cancelScheduledValues(ctx.currentTime);
  g.setValueAtTime(dbToGain(db), ctx.currentTime);
}

// The output's level in dBFS, to check the stage is carrying anything.
export function outputDb() {
  ensure();
  const buf = new Float32Array(node.meter.fftSize);
  node.meter.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  return gainToDb(Math.sqrt(sum / buf.length));
}
```

`share/js/audiostate.js`:

```js
// The Surface's audio path as lib/audio.py sees it: the port, the volume, and
// whether `omacar audio on` asked for 100% to be held here. Applied at start
// and every half minute by alertness.js, and read by whatever shows it.
import { postJSON } from "./camapi.js";

export const audio = { last: null };
const fns = new Set();

export function onAudio(fn) { fns.add(fn); fn(audio.last); return () => fns.delete(fn); }

export async function applyAudio() {
  try { audio.last = await postJSON("/api/audio", { action: "apply" }); } catch { audio.last = null; }
  for (const fn of fns) fn(audio.last);
  return audio.last;
}

// "AUX disconnected" only when the port is known to be the speakers. Unknown
// is not the same as unplugged, and saying so would be a guess.
export function auxLine(a) {
  return a && a.aux === false ? "AUX disconnected — sound is on the tablet's speakers" : "";
}
```

`share/js/alertness.js`:

```js
// Started beside the app (share/app.html), like awake.js, because it is not
// part of any view. It holds the Surface's volume where `omacar audio on`
// asked for it (lib/audio.py), at start and every half minute: plugging the
// AUX cable in switches to a port that keeps a volume of its own.
import { applyAudio } from "./audiostate.js";

applyAudio();
setInterval(applyAudio, 30000);
```

In `share/app.html`, inside the `redesign/cameras` block, after the `cameras.css` link, add:

```html
<script type="module" src="js/alertness.js"></script>
```

In `share/js/radio.js`:
- add below `import { h } from "./core.js";`:

```js
// The radio joins the one output stage (audiobus.js): its analyser feeds the
// music bus, 12 dB below full scale, so every alert has headroom over it.
import { audioContext, musicIn } from "./audiobus.js";
```

- in `analyserFor(a)`, replace `actx = new AC();` with `actx = audioContext();`.
- in `analyserFor(a)`, replace `analyser.connect(actx.destination);` with `analyser.connect(musicIn());`.
- the comment above that line currently warns about connecting the far end to the destination. Change its last sentence to: "Forget to connect the far end to the stage and the visualiser works beautifully while the radio goes silent."

- [ ] **Step 5: Run everything**

Run `BOXTEST`. Expected:
- `audio_test.py` and `js_test.py` are green.
- `app_test.py` still boots with alertness.js loaded. It posts `apply`, and on the box that is not managed, so no volume changes.

Check the CLI on the box without changing anything: `ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && bin/omacar audio status'`. Expected: an output port and a volume, with no "(held at 100%)".

- [ ] **Step 6: Commit**

```bash
git add lib/audio.py lib/camroutes.py bin/omacar share/js/audiobus.js share/js/audiostate.js share/js/alertness.js share/js/radio.js share/app.html test/audio_test.py test/js/audiobus.test.js test/all.sh
git commit -m "Everything OmaCar plays goes through one stage, so an alert always has 12 dB over the music" -m "Music sits 12 dB below full scale and alerts may use all of it, which holds whatever the car's volume knob says. The radio's analyser now feeds that stage. The Surface itself is held at 100% by lib/audio.py, only where omacar audio on asked, so the test suite can never turn the box's speakers up.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The drowsy measures (pure)

**Files:**
- Create: `share/data/drowsy.json`, `share/js/drowsy.js`, `test/js/drowsy.test.js`

**Interfaces:**
- Produces:
  - `share/data/drowsy.json`: the defaults every later drowsy task reads. It has `enabled`, `sensitivity`, `name`, `sounds`, `min_speed_mph`, `eyes`, `perclos`, `yawn`, `nod`, `face_lost_secs`, `stop`, `level1`, `level2`, `level3`, `release`, and `banner_stopped_secs`.
  - `drowsy.js`: `PITCH_SIGN`, `pitchDeg(matrixData) → degrees` (negative is chin down), and `frameFrom(result, t) → {t, face, blink, jaw, pitch}`, which reads a MediaPipe FaceLandmarkerResult.
  - `drowsy.js`: `createMeasures(cfg) → {feed(frame) → snapshot, snapshot}`. A frame is `{t, face, blink, jaw, pitch, gated}`, with t in seconds.
  - A snapshot is `{t, face, calibrated, baseline, threshold, blink, closed, closedFor, openFor, perclos, yawns, nods, faceLost, pitch, pitchBaseline}`. `perclos` is null until the window holds 30 s; `yawns` and `nods` are counts in the last 5 min.

- [ ] **Step 1: The defaults, and the failing test**

`share/data/drowsy.json`:

```json
{
  "_comment": "Drowsy mode's numbers, from doc/design/2026-09-28-cameras-drowsy.md. Read through GET /api/drowsy, which lays ~/.config/omarchy/omacar-drowsy.json over these (lib/drowsycfg.py). Seconds, degrees, and PERCLOS as a fraction. Sensitive lowers every threshold by 20% (share/js/ladder.js, scaled()).",
  "enabled": true,
  "sensitivity": "standard",
  "name": "James",
  "sounds": ["bark", "voice", "alarm"],
  "min_speed_mph": 30,
  "eyes": { "baseline_secs": 60, "closed_over_baseline": 0.35, "closed_cap": 0.8 },
  "perclos": { "window_secs": 60, "min_secs": 30 },
  "yawn": { "jaw_open": 0.6, "hold_secs": 1.5 },
  "nod": { "below_deg": 15, "hold_secs": 0.5 },
  "face_lost_secs": 5,
  "stop": { "still_secs": 300 },
  "level1": { "perclos": 0.15, "yawns": 3, "nods": 3, "count_window_secs": 300,
              "since_stop_secs": 7200, "night_from_hour": 2, "night_to_hour": 6,
              "camera_free_every_secs": 3600 },
  "level2": { "closed_secs": 1.0, "perclos": 0.25, "repeat_secs": 5 },
  "level3": { "closed_secs": 2.0, "level2_count": 2, "level2_window_secs": 300, "voice_repeat_secs": 15 },
  "release": { "open_secs": 5 },
  "banner_stopped_secs": 120
}
```

`test/js/drowsy.test.js`:

```js
import { eq, ok } from "./assert.js";
import { createMeasures, frameFrom, pitchDeg } from "../js/drowsy.js";

const CFG = async () => (await fetch("../data/drowsy.json")).json();
const near = (a, b, d = 0.051) => typeof a === "number" && Math.abs(a - b) <= d;

// Feed `secs` of frames at 10 fps from `from`; fn(t) overrides a frame's fields.
function run(m, from, secs, fn) {
  let s = null;
  for (let i = 0; i < Math.round(secs * 10); i++) {
    const t = +(from + i / 10).toFixed(3);
    s = m.feed(Object.assign({ t, face: true, blink: 0.1, jaw: 0.1, pitch: 0, gated: true }, fn ? fn(t) : {}));
  }
  return s;
}
// A driver calibrated on 60 s of open eyes at 0.1, blinking every 4 s.
async function calibrated() {
  const m = createMeasures(await CFG());
  run(m, 0, 61, (t) => ({ blink: Math.round(t * 10) % 40 === 0 ? 0.9 : 0.1 }));
  return m;
}

export default [
  ["the spec's numbers are the defaults", async () => {
    const c = await CFG();
    eq([c.eyes.baseline_secs, c.eyes.closed_over_baseline, c.eyes.closed_cap, c.perclos.window_secs,
        c.yawn.jaw_open, c.yawn.hold_secs, c.nod.below_deg, c.nod.hold_secs, c.face_lost_secs, c.min_speed_mph],
       [60, 0.35, 0.8, 60, 0.6, 1.5, 15, 0.5, 5, 30]);
  }],
  ["eye closure is the mean of both blink scores", () => {
    const f = frameFrom({
      faceBlendshapes: [{ categories: [{ categoryName: "eyeBlinkLeft", score: 0.2 },
        { categoryName: "eyeBlinkRight", score: 0.4 }, { categoryName: "jawOpen", score: 0.7 }] }],
      facialTransformationMatrixes: [{ rows: 4, columns: 4, data: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] }],
    }, 5);
    ok(near(f.blink, 0.3, 1e-9), `blink ${f.blink}`);
    eq([f.t, f.face, f.jaw, f.pitch], [5, true, 0.7, 0]);
  }],
  ["no face is a frame without a face", () => eq(frameFrom({ faceBlendshapes: [] }, 1), { t: 1, face: false })],
  ["a nod down is a negative pitch", () => {
    const a = 20 * Math.PI / 180;   // 20 degrees about x: the face's forward axis tips down
    const d = [1, 0, 0, 0, 0, Math.cos(a), Math.sin(a), 0, 0, -Math.sin(a), Math.cos(a), 0, 0, 0, 0, 1];
    ok(near(pitchDeg(d), -20, 1e-6), `pitch ${pitchDeg(d)}`);
  }],
  ["no baseline before 60 s above 30 mph", async () => {
    const s = run(createMeasures(await CFG()), 0, 59);
    eq([s.calibrated, s.closed, s.perclos], [false, false, null]);
  }],
  ["time below 30 mph does not count toward it", async () => {
    const m = createMeasures(await CFG());
    run(m, 0, 30);
    run(m, 30, 60, () => ({ gated: false }));
    eq(run(m, 90, 20).calibrated, false);
  }],
  ["the baseline is the open-eye median, so blinks do not raise it", async () => {
    const s = (await calibrated()).snapshot;
    eq([s.calibrated, s.baseline, +s.threshold.toFixed(2)], [true, 0.1, 0.45]);
  }],
  ["closed is capped at 0.8 for eyes that rest half shut", async () => {
    eq(+run(createMeasures(await CFG()), 0, 61, () => ({ blink: 0.6 })).threshold.toFixed(2), 0.8);
  }],
  ["closure duration is continuous closed time", async () => {
    const m = await calibrated();
    const s = run(m, 61, 1.2, () => ({ blink: 0.9 }));
    ok(near(s.closedFor, 1.1), `closedFor ${s.closedFor}`);
    eq(run(m, 62.2, 0.1).closedFor, 0);
  }],
  ["PERCLOS is the closed share of the last 60 s", async () => {
    const m = await calibrated();
    run(m, 61, 9, () => ({ blink: 0.9 }));
    const s = run(m, 70, 51);
    ok(near(s.perclos, 0.15, 0.01), `perclos ${s.perclos}`);
  }],
  ["PERCLOS waits for 30 s of window rather than guessing", async () => {
    const m = createMeasures(await CFG());
    run(m, 0, 61);
    eq(run(m, 61, 25).perclos, null);
    ok(run(m, 86, 5).perclos !== null, "a number after 30 s");
  }],
  ["a yawn is the jaw past 0.6 for 1.5 s", async () => {
    const m = await calibrated();
    run(m, 61, 1.6, () => ({ jaw: 0.7 }));
    run(m, 62.6, 2);
    run(m, 64.6, 1.2, () => ({ jaw: 0.7 }));
    eq(run(m, 65.8, 2).yawns, 1);
  }],
  ["a nod is 15 degrees down for half a second, then back", async () => {
    const m = await calibrated();
    run(m, 61, 0.6, () => ({ pitch: -20 }));
    run(m, 61.6, 1);
    run(m, 62.6, 0.3, () => ({ pitch: -20 }));
    run(m, 62.9, 1);
    run(m, 63.9, 2, () => ({ pitch: -10 }));
    eq(run(m, 65.9, 1).nods, 1);
  }],
  ["no face for more than 5 s is 'Can't see you', and nothing more", async () => {
    const m = await calibrated();
    const a = run(m, 61, 4.9, () => ({ face: false }));
    const b = run(m, 65.9, 1.2, () => ({ face: false }));
    eq([a.faceLost, b.faceLost, b.closed, b.closedFor], [false, true, false, 0]);
  }],
];
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/js_test.py`. Expected: `drowsy.test.js :: import`.

- [ ] **Step 3: Write drowsy.js**

```js
// Drowsy mode's measures, from timestamped frames. Pure: no camera, no clock,
// no DOM. A frame is { t (seconds), face, blink, jaw, pitch, gated }, and every
// measure is a function of the frames fed so far, so each can be tested on a
// synthetic series.
//
//   eye closure  the mean of eyeBlinkLeft and eyeBlinkRight
//   baseline     the first 60 s above 30 mph (gated) with a face in view set
//                the driver's open-eye baseline -- the median, so blinks do
//                not raise it; "closed" is above baseline + 0.35, capped at 0.8
//   closure      how long the eyes have been continuously closed
//   PERCLOS      the share of frames in the last 60 s with the eyes closed
//                (P80), once the window holds 30 s
//   yawn         jawOpen > 0.6 held for 1.5 s or more
//   nod          head pitch more than 15 degrees below its baseline for 0.5 s
//                or more, then recovering
//   face lost    no face for more than 5 s: "Can't see you". Never an alert.

// Which way is chin-down. The facial transformation matrix is column-major
// in MediaPipe's y-up camera space, so a nod tips the face's forward axis to
// negative y and this reads negative. The owner confirms it on the tablet
// (Task 12); if nodding reads positive there, this becomes -1.
export const PITCH_SIGN = 1;

export function pitchDeg(data) {
  if (!data || data.length < 11) return null;
  const y = Math.max(-1, Math.min(1, data[9]));
  return PITCH_SIGN * Math.asin(y) * 180 / Math.PI;
}

// A MediaPipe FaceLandmarkerResult as a measures frame.
export function frameFrom(result, t) {
  const bs = result && result.faceBlendshapes && result.faceBlendshapes[0];
  if (!bs || !bs.categories || !bs.categories.length) return { t, face: false };
  const s = {};
  for (const c of bs.categories) s[c.categoryName] = c.score;
  const m = result.facialTransformationMatrixes && result.facialTransformationMatrixes[0];
  return { t, face: true, blink: ((s.eyeBlinkLeft || 0) + (s.eyeBlinkRight || 0)) / 2,
           jaw: s.jawOpen || 0, pitch: m ? pitchDeg(m.data) : null };
}

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

export function createMeasures(cfg) {
  const E = cfg.eyes, W = cfg.perclos, Y = cfg.yawn, N = cfg.nod, L1 = cfg.level1;
  let gatedSecs = 0, prevGatedT = null;
  const blinks = [], pitches = [];
  let baseline = null, pitch0 = null;
  let closedSince = null, openSince = null;
  const win = [];                      // [t, closed], with a face, after the baseline
  let jawSince = null, yawnCounted = false;
  const yawns = [];
  let downSince = null, downLast = null;
  const nods = [];
  let firstT = null, lastFace = null;
  let snap = null;

  function feed(f) {
    const t = f.t;
    if (firstT === null) firstT = t;
    if (f.face) lastFace = t;

    // The baseline: gated time with a face in view, in unbroken runs.
    if (baseline === null) {
      if (f.gated && f.face) {
        if (prevGatedT !== null && t - prevGatedT < 1) gatedSecs += t - prevGatedT;
        prevGatedT = t;
        blinks.push(f.blink);
        if (typeof f.pitch === "number") pitches.push(f.pitch);
        if (gatedSecs >= E.baseline_secs) { baseline = median(blinks); pitch0 = median(pitches); }
      } else prevGatedT = null;
    }

    const threshold = baseline === null ? null : Math.min(baseline + E.closed_over_baseline, E.closed_cap);
    const closed = !!f.face && threshold !== null && f.blink > threshold;
    if (closed) { if (closedSince === null) closedSince = t; } else closedSince = null;
    const open = !!f.face && threshold !== null && !closed;
    if (open) { if (openSince === null) openSince = t; } else openSince = null;

    if (f.face && threshold !== null) win.push([t, closed]);
    while (win.length && t - win[0][0] > W.window_secs) win.shift();
    const span = win.length ? t - win[0][0] : 0;
    const perclos = span >= W.min_secs ? win.filter((w) => w[1]).length / win.length : null;

    if (f.face && f.jaw > Y.jaw_open) {
      if (jawSince === null) jawSince = t;
      if (!yawnCounted && t - jawSince >= Y.hold_secs) { yawns.push(t); yawnCounted = true; }
    } else { jawSince = null; yawnCounted = false; }

    if (pitch0 !== null && f.face && typeof f.pitch === "number") {
      if (pitch0 - f.pitch > N.below_deg) {
        if (downSince === null) downSince = t;
        downLast = t;
      } else {
        if (downSince !== null && downLast - downSince >= N.hold_secs) nods.push(t);
        downSince = null;
        downLast = null;
      }
    }

    const recent = (xs) => { while (xs.length && t - xs[0] > L1.count_window_secs) xs.shift(); return xs.length; };
    snap = {
      t, face: !!f.face, calibrated: baseline !== null, baseline, threshold,
      blink: f.face ? f.blink : null, closed,
      closedFor: closedSince === null ? 0 : t - closedSince,
      openFor: openSince === null ? 0 : t - openSince,
      perclos, yawns: recent(yawns), nods: recent(nods),
      faceLost: t - (lastFace === null ? firstT : lastFace) > cfg.face_lost_secs,
      pitch: f.face && typeof f.pitch === "number" ? f.pitch : null, pitchBaseline: pitch0,
    };
    return snap;
  }

  return { feed, get snapshot() { return snap; } };
}
```

- [ ] **Step 4: Run it to see it pass**

Run: rsync, then `python3 test/js_test.py`. Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add share/data/drowsy.json share/js/drowsy.js test/js/drowsy.test.js
git commit -m "Eye closure, PERCLOS, yawns and nods are measured from frames, against the driver's own open-eye baseline" -m "Pure, so each measure is tested on a synthetic series. The first minute above 30 mph sets the baseline from its median, so blinks do not raise it; closed is 0.35 above that, capped at 0.8. A lost face says so and never alerts on its own. The spec's numbers live in share/data/drowsy.json.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The ladder (pure)

**Files:**
- Create: `share/js/ladder.js`, `test/js/ladder.test.js`

**Interfaces:**
- Consumes: `drowsy.json` (Task 6) and the measures snapshot shape (Task 6).
- Produces:
  - `scaled(cfg, sensitivity) → cfg` (Sensitive is −20%) and `isNight(hour, cfg)`.
  - `createStopClock(cfg) → {feed(t, kph, connected) → {sinceStop, stoppedFor}}`.
  - `createLadder(cfg, sounds = cfg.sounds) → {step(input) → out, level}`.
  - `input` is `{t, active, parked, m, sinceStop, stoppedFor, hour, tap}`.
  - `out` is `{t, level, trigger, raised: null|{level, trigger}, cleared, cues, banner}`.
  - Triggers are `perclos`, `closed`, `yawns`, `nods`, `since-stop`, `night` and `repeat-l2`.
  - Cues are `{kind: "chime"}`, `{kind: "voice", clip: "l1"|"l2"|"l3"}`, `{kind: "swell"}`, `{kind: "duck"}`, `{kind: "bark"}`, `{kind: "alarm", secs?, hold?}` and `{kind: "fade"}`.

- [ ] **Step 1: Write the failing test**

`test/js/ladder.test.js`:

```js
import { eq } from "./assert.js";
import { createLadder, createStopClock, scaled, isNight } from "../js/ladder.js";

const CFG = async () => (await fetch("../data/drowsy.json")).json();
const M = (o) => Object.assign({ face: true, calibrated: true, closed: false, closedFor: 0, openFor: 0,
  perclos: 0.02, yawns: 0, nods: 0, faceLost: false }, o);
const go = (o) => Object.assign({ active: true, parked: false, sinceStop: 0, stoppedFor: 0, hour: 14, tap: false }, o);
// Step a ladder through [t, measures, extra] rows; every output comes back.
const drive = (lad, rows) => rows.map(([t, m, x]) => lad.step(go(Object.assign({ t, m: M(m) }, x))));
const kinds = (out) => out.cues.map((c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind));

export default [
  ["the spec's ladder is the default", async () => {
    const c = await CFG();
    eq([c.level1.perclos, c.level1.yawns, c.level1.nods, c.level1.count_window_secs, c.level1.since_stop_secs,
        c.level1.night_from_hour, c.level1.night_to_hour, c.level2.closed_secs, c.level2.perclos,
        c.level3.closed_secs, c.level3.level2_count, c.level3.level2_window_secs, c.release.open_secs,
        c.banner_stopped_secs, c.stop.still_secs],
       [0.15, 3, 3, 300, 7200, 2, 6, 1.0, 0.25, 2.0, 2, 300, 5, 120, 300]);
  }],
  ["Sensitive is every threshold 20% lower", async () => {
    const s = scaled(await CFG(), "sensitive");
    eq([s.level1.perclos, s.level2.perclos, s.level2.closed_secs, s.level3.closed_secs, s.level1.yawns,
        s.level1.nods, s.level1.since_stop_secs], [0.12, 0.2, 0.8, 1.6, 2, 2, 5760]);
  }],
  ["Standard changes nothing", async () => { const c = await CFG(); eq(scaled(c, "standard"), c); }],
  ["night is 02:00 to 06:00", async () => { const c = await CFG(); eq([1, 2, 5, 6].map((h) => isNight(h, c)), [false, true, true, false]); }],
  ["five minutes stationary is a stop; four is not", async () => {
    const s = createStopClock(await CFG());
    s.feed(0, 80, true);
    s.feed(1000, 0, true);
    const a = s.feed(1240, 0, true);
    const b = s.feed(1300, 0, true);
    eq([a.sinceStop, b.sinceStop, b.stoppedFor], [1240, 0, 300]);
  }],
  ["nothing is raised below 30 mph, even with eyes closed", async () => {
    eq(drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.5 }, { active: false }]])[0].level, 0);
  }],
  ["but a condition still true when the car passes 30 mph raises then", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }], [1, { perclos: 0.16 }]]);
    eq(out.map((o) => o.level), [0, 1]);
  }],
  ["PERCLOS 15% is Level 1: a chime, the voice, the radio rising", async () => {
    const [o] = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }]]);
    eq([o.level, o.trigger, kinds(o)], [1, "perclos", ["chime", "voice:l1", "swell"]]);
  }],
  ["a condition that stays true raises once", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.17 }], [2, { perclos: 0.18 }]]);
    eq(out.map((o) => !!o.raised), [true, false, false]);
  }],
  ["eyes closed for 1 s is Level 2: the music ducks and the first sound rises", async () => {
    const [o] = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }]]);
    eq([o.level, o.trigger, kinds(o)], [2, "closed", ["duck", "bark"]]);
  }],
  ["Level 2 repeats every 5 s, rotating bark, voice, alarm", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [2, { face: false }],
      [5, { face: false }], [10, { face: false }], [15, { face: false }]]);
    eq(out.map(kinds), [["duck", "bark"], [], ["voice:l2"], ["alarm"], ["bark"]]);
  }],
  ["only the sounds the driver chose rotate", async () => {
    const out = drive(createLadder(await CFG(), ["alarm"]), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }]]);
    eq(out.map(kinds), [["duck", "alarm"], ["alarm"]]);
  }],
  ["eyes closed for 2 s is Level 3: a continuous alarm and 'Pull over now'", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, { closed: true, closedFor: 2.0 }]]);
    eq([out[1].level, kinds(out[1]), out[1].banner], [3, ["duck", "alarm", "voice:l3"], true]);
  }],
  ["two Level 2 alerts within 5 min is Level 3", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }],
      [100, { closed: true, closedFor: 0.2 }], [101, { closed: true, closedFor: 1.0 }]]);
    eq([out[0].level, out[1].level, out[3].level, out[3].trigger], [2, 0, 3, "repeat-l2"]);
  }],
  ["six minutes apart, a second Level 2 is only Level 2", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }],
      [400, { closed: true, closedFor: 0.2 }], [401, { closed: true, closedFor: 1.0 }]]);
    eq(out[3].level, 2);
  }],
  ["'I'm awake' clears the level, and the sound fades", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }]]);
    eq([out[1].level, out[1].cleared, kinds(out[1])], [0, true, ["fade"]]);
  }],
  ["eyes open 5 s with PERCLOS not rising clears it; 4 s does not", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [5, { perclos: 0.15 }], [6, { perclos: 0.15 }]]);
    eq(out.map((o) => o.level), [1, 1, 1, 0]);
  }],
  ["open eyes with PERCLOS still rising do not clear it", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }], [6, { perclos: 0.17 }]]);
    eq(out[2].level, 1);
  }],
  ["a stationary car clears the level", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, {}, { active: false, parked: true }]]);
    eq(out[1].level, 0);
  }],
  ["after Level 3 the banner stays until the car has been stopped 2 minutes", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }], [1, {}, { tap: true }],
      [60, {}, { active: false, parked: true, stoppedFor: 30 }], [200, {}, { active: false, parked: true, stoppedFor: 120 }]]);
    eq(out.map((o) => [o.level, o.banner]), [[3, true], [0, true], [0, true], [0, false]]);
  }],
  ["night hours raise Level 1 once an hour, never more", async () => {
    const out = drive(createLadder(await CFG()), [[0, {}, { hour: 3 }], [10, {}, { tap: true, hour: 3 }],
      [1800, {}, { hour: 3 }], [3600, {}, { hour: 4 }]]);
    eq(out.map((o) => o.raised && o.raised.trigger), ["night", null, null, "night"]);
  }],
  ["2 h without a stop raises Level 1, and never escalates on its own", async () => {
    const out = drive(createLadder(await CFG()), [[0, { face: false }, { sinceStop: 7200 }], [30, { face: false }, { sinceStop: 7230 }]]);
    eq([out[0].level, out[0].trigger, out[1].level, kinds(out[1])], [1, "since-stop", 1, []]);
  }],
];
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/js_test.py`. Expected: `ladder.test.js :: import`.

- [ ] **Step 3: Write ladder.js**

```js
// The ladder: three alert levels, raised from the measures (drowsy.js) and two
// signals that need no camera, and released by a tap or by eyes that stay
// open. Pure, with the clock injected: step() is handed the time, and nothing
// here reads Date.now().
//
//   1 · Notice     PERCLOS >= 15%, 3 yawns or 3 nods in 5 min, 2 h since a
//                  stop, or night hours (once an hour)
//   2 · Wake       eyes closed >= 1.0 s, or PERCLOS >= 25%
//   3 · Pull over  eyes closed >= 2.0 s, or two Level 2 alerts within 5 min
//
// RAISED ON AN EDGE, ONLY WHILE THE GATE IS OPEN (above 30 mph, connected,
// moving). A condition that stays true raises once, not on every frame; one
// that was already true when the car passed 30 mph raises then. A raised level
// STAYS if the car slows -- eyes closed at 25 mph are no safer -- and clears
// on a tap of "I'm awake", when the eyes stay open for 5 s after it was raised
// with PERCLOS no higher than when they opened (PERCLOS is a 60 s window, so
// it cannot fall within 5 s of a closure; "not rising" is what can be seen),
// or when the car is stationary.

export function scaled(cfg, sensitivity = cfg.sensitivity) {
  const c = JSON.parse(JSON.stringify(cfg));
  if (sensitivity !== "sensitive") return c;
  const f = 0.8;
  const n = (x) => Math.max(1, Math.round(x * f));
  const r = (x, d) => +(x * f).toFixed(d);
  c.level1.perclos = r(c.level1.perclos, 4);
  c.level1.yawns = n(c.level1.yawns);
  c.level1.nods = n(c.level1.nods);
  c.level1.since_stop_secs = Math.round(c.level1.since_stop_secs * f);
  c.level2.closed_secs = r(c.level2.closed_secs, 3);
  c.level2.perclos = r(c.level2.perclos, 4);
  c.level3.closed_secs = r(c.level3.closed_secs, 3);
  return c;
}

export function isNight(hour, cfg) {
  return hour >= cfg.level1.night_from_hour && hour < cfg.level1.night_to_hour;
}

// Time since the last stop: speed 0 for 5 minutes or more counts, and so does
// the car being off that long. A drive starts when the app does.
export function createStopClock(cfg) {
  let still = null, lastStop = null;
  return {
    feed(t, kph, connected) {
      if (lastStop === null) lastStop = t;
      if (!connected || kph === 0) {
        if (still === null) still = t;
        if (t - still >= cfg.stop.still_secs) lastStop = t;
      } else still = null;
      return { sinceStop: t - lastStop, stoppedFor: still === null ? 0 : t - still };
    },
  };
}

export function createLadder(cfg, sounds = cfg.sounds) {
  const L1 = cfg.level1, L2 = cfg.level2, L3 = cfg.level3;
  const rota = sounds && sounds.length ? sounds : ["alarm"];
  let level = 0, trigger = null, banner = false;
  let rot = 0, nextRepeat = null, nextVoice = null;
  let openStart = null, perclosAtOpen = null;
  const was = {};
  const l2Times = [];
  const lastFree = {};

  const has = (v) => v !== null && v !== undefined;
  function rotation() {
    const s = rota[rot++ % rota.length];
    if (s === "voice") return { kind: "voice", clip: "l2" };
    if (s === "alarm") return { kind: "alarm", secs: 3 };
    return { kind: "bark" };
  }

  function raise(to, why, t, out) {
    level = to;
    trigger = why;
    openStart = null;
    out.raised = { level: to, trigger: why };
    if (to === 1) out.cues.push({ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" });
    if (to === 2) { out.cues.push({ kind: "duck" }, rotation()); nextRepeat = t + L2.repeat_secs; }
    if (to === 3) {
      banner = true;
      nextRepeat = null;
      out.cues.push({ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
  }

  function clear(out) {
    level = 0;
    trigger = null;
    nextRepeat = nextVoice = null;
    openStart = null;
    out.cleared = true;
    out.cues.push({ kind: "fade" });
  }

  function step(inp) {
    const t = inp.t, m = inp.m || {};
    const out = { t, level, trigger, raised: null, cleared: false, cues: [], banner };

    // Release first: a tap, open eyes, or a stationary car.
    if (level > 0) {
      if (m.face && m.calibrated && !m.closed) {
        if (openStart === null) { openStart = t; perclosAtOpen = has(m.perclos) ? m.perclos : null; }
      } else openStart = null;
      const steady = openStart !== null && t - openStart >= cfg.release.open_secs
        && (!has(m.perclos) || perclosAtOpen === null || m.perclos <= perclosAtOpen);
      if (inp.tap || steady || inp.parked) clear(out);
    }
    if (banner && inp.stoppedFor >= cfg.banner_stopped_secs) banner = false;

    if (!inp.active) {
      for (const k of Object.keys(was)) was[k] = false;
    } else {
      const edge = (k, v) => { const rose = !!v && !was[k]; was[k] = !!v; return rose; };
      const c3 = edge("closed3", m.closedFor >= L3.closed_secs);
      const c2 = edge("closed2", m.closedFor >= L2.closed_secs);
      const p2 = edge("perclos2", has(m.perclos) && m.perclos >= L2.perclos);
      const p1 = edge("perclos1", has(m.perclos) && m.perclos >= L1.perclos);
      const y1 = edge("yawns", m.yawns >= L1.yawns);
      const n1 = edge("nods", m.nods >= L1.nods);
      const free = (k, v) => {
        if (!v || (has(lastFree[k]) && t - lastFree[k] < L1.camera_free_every_secs)) return false;
        lastFree[k] = t;
        return true;
      };
      const s1 = free("since-stop", inp.sinceStop >= L1.since_stop_secs);
      const nt = free("night", isNight(inp.hour, cfg));

      let to = 0, why = null;
      if (c3) { to = 3; why = "closed"; }
      else if (c2 || p2) { to = 2; why = c2 ? "closed" : "perclos"; }
      else if (p1) { to = 1; why = "perclos"; }
      else if (y1) { to = 1; why = "yawns"; }
      else if (n1) { to = 1; why = "nods"; }
      else if (s1) { to = 1; why = "since-stop"; }
      else if (nt) { to = 1; why = "night"; }
      if (to === 2) {
        while (l2Times.length && t - l2Times[0] > L3.level2_window_secs) l2Times.shift();
        l2Times.push(t);
        if (l2Times.length >= L3.level2_count) { to = 3; why = "repeat-l2"; }
      }
      if (to > level) raise(to, why, t, out);
    }

    if (level === 2 && nextRepeat !== null && t >= nextRepeat && !out.raised) {
      out.cues.push(rotation());
      nextRepeat = t + L2.repeat_secs;
    }
    if (level === 3 && nextVoice !== null && t >= nextVoice && !out.raised) {
      out.cues.push({ kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
    out.level = level;
    out.trigger = trigger;
    out.banner = banner;
    return out;
  }

  return { step, get level() { return level; } };
}
```

- [ ] **Step 4: Run it to see it pass**

Run: rsync, then `python3 test/js_test.py`. Expected: all green.

If "Level 2 repeats every 5 s" fails at t=15 with no cue, check that `nextRepeat` is `t + repeat_secs` from the cue's own step, not from the raise.

- [ ] **Step 5: Commit**

```bash
git add share/js/ladder.js test/js/ladder.test.js
git commit -m "Three alert levels rise from the measures, repeat and rotate, and clear when the driver answers" -m "A pure state machine with the clock handed in, so triggers, rotation, release and the Level 3 banner are tested step by step. Levels rise on an edge and only above 30 mph, and stay raised if the car slows. Night hours and two hours without a stop only ever raise Level 1, once an hour. Sensitive lowers every threshold by 20%.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Ramp plans, the sounds, and Begin's chime

**Files:**
- Create: `share/js/ramps.js`, `share/js/sounds.js`, `share/css/drowsy.css`, `test/js/ramps.test.js`, `test/js/sounds.test.js`
- Modify: `share/js/views/launcher.js` (the chime and the AUX line), `share/app.html` (inside the cameras block)

**Interfaces:**
- Consumes: `audioContext`, `alertIn`, `resume`, `setLevelNow` (Task 5); `withToken` (core.js).
- Produces:
  - `ramps.js`: `RAMP_SECS`, `MAX_DB_PER_STEP = 3`, `STEP_SECS = 0.1`, `rampPlan(level, from, to) → {secs, points: [[t, dB], …]}`. It throws on an unknown level or a non-finite level.
  - `sounds.js` plans: `CHIME`, `ALARM`, `BARK`, `alarmSteps(secs) → [[t, hz]]`, `barkCalls(calls) → [t]`.
  - `sounds.js` players: `playChime(at)`, `playAlarm(at, secs) → {stop(when)}`, `playBark(at, calls = 1)`, `playVoice(url, at) → Promise<source|null>`, `beginChime()`.

- [ ] **Step 1: Write the failing tests**

`test/js/ramps.test.js`:

```js
import { eq, ok } from "./assert.js";
import { rampPlan, RAMP_SECS } from "../js/ramps.js";

// The largest step between consecutive points, after checking they are never
// more than 100 ms apart.
const worst = (p) => {
  let w = 0;
  for (let i = 1; i < p.points.length; i++) {
    const [t0, a] = p.points[i - 1], [t1, b] = p.points[i];
    ok(t1 - t0 <= 0.1 + 1e-9, `points ${t0} and ${t1} are more than 100 ms apart`);
    w = Math.max(w, Math.abs(b - a));
  }
  return w;
};
const throws = (fn) => { try { fn(); return false; } catch { return true; } };

export default [
  ["the spec's ramp times", () =>
    eq(RAMP_SECS, { 0: { up: 3, down: 3 }, 1: { up: 10, down: 30 }, 2: { up: 1.5, down: 0.5 }, 3: { up: 3, down: 0.5 } })],
  ["Level 1: the radio rises 6 dB over 10 s", () => {
    const p = rampPlan(1, -12, -6);
    eq([p.secs, p.points[0], p.points[p.points.length - 1]], [10, [0, -12], [10, -6]]);
  }],
  ["and settles back over 30 s", () => eq(rampPlan(1, -6, -12).secs, 30)],
  ["Level 2: music ducks 12 dB over half a second", () => eq(rampPlan(2, -12, -24).secs, 0.5)],
  ["and the alert rises over 1.5 s", () => eq(rampPlan(2, -40, -3).secs, 1.5)],
  ["Level 3: the alarm reaches full over 3 s", () => eq(rampPlan(3, -40, 0).secs, 3)],
  ["release: the sound fades out over 3 s", () => eq(rampPlan(0, 0, -60).secs, 3)],
  ["no step larger than 3 dB per 100 ms, at any level, over any span", () => {
    for (const level of [0, 1, 2, 3]) {
      for (const [a, b] of [[-60, 0], [0, -60], [-12, -6], [-6, -12], [-12, -24], [-24, -12], [-40, -3], [-40, 0], [-120, 0]]) {
        ok(worst(rampPlan(level, a, b)) <= 3 + 1e-9, `level ${level}, ${a} to ${b} dB`);
      }
    }
  }],
  ["a span too big for the level's time is stretched, never stepped", () =>
    ok(rampPlan(2, -60, 0).secs >= 2, "60 dB needs 2 s at 3 dB per 100 ms")],
  ["an unknown level, or silence as a number, is an error rather than a guess", () =>
    eq([throws(() => rampPlan(7, 0, -6)), throws(() => rampPlan(0, -Infinity, 0))], [true, true])],
];
```

`test/js/sounds.test.js`:

```js
import { eq, ok } from "./assert.js";
import { CHIME, ALARM, BARK, alarmSteps, barkCalls } from "../js/sounds.js";

export default [
  ["the chime is two soft notes, rising", () => {
    eq(CHIME.notes.length, 2);
    ok(CHIME.notes[1].hz > CHIME.notes[0].hz, "the second note is higher");
    ok(CHIME.peak <= 0.5, "soft");
  }],
  ["the alarm's two tones sit inside ISO 7731's 500-1500 Hz", () =>
    ok(ALARM.tones.length === 2 && ALARM.tones.every((hz) => hz >= 500 && hz <= 1500), String(ALARM.tones))],
  ["and alternate every quarter second", () => eq(alarmSteps(1), [[0, 700], [0.25, 1000], [0.5, 700], [0.75, 1000]])],
  ["a bark call is two barks", () => eq(barkCalls(1), [0, BARK.gap])],
  ["three calls carry a bark through a Level 2 rise", () => eq(barkCalls(3).length, 6)],
  ["each bark falls in pitch, fast", () => ok(BARK.sweepTo < BARK.sweepFrom && BARK.secs <= 0.2, "a fast downward sweep")],
  ["nothing is louder than full scale", () =>
    ok([CHIME.peak, ALARM.peak, BARK.peak].every((p) => p > 0 && p <= 1), "every peak in (0, 1]")],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/js_test.py`. Expected: two `:: import` failures.

- [ ] **Step 3: Write ramps.js and sounds.js**

`share/js/ramps.js`:

```js
// The shapes of every level change, as plans the audio stage runs with
// linearRampToValueAtTime (audiobus.js schedule()). Pure, and tested.
//
// linearRampToValueAtTime is linear in AMPLITUDE: one long ramp from quiet to
// loud gains most of its decibels in its first instant, which is exactly the
// startle the design forbids. So a plan is a point every 100 ms, evenly spaced
// in dB, and no step between points is larger than 3 dB. A span too big for
// its level's time is stretched until it fits, never stepped.

export const RAMP_SECS = {
  0: { up: 3, down: 3 },       // release: sound fades out over 3 s, music comes back
  1: { up: 10, down: 30 },     // Level 1: the radio rises over 10 s, settles over 30 s
  2: { up: 1.5, down: 0.5 },   // Level 2: the alert rises over 1.5 s; music ducks in 0.5 s
  3: { up: 3, down: 0.5 },     // Level 3: the alarm rises to full over 3 s
};
export const MAX_DB_PER_STEP = 3;
export const STEP_SECS = 0.1;

export function rampPlan(level, from, to) {
  const spec = RAMP_SECS[level];
  if (!spec) throw new Error(`no ramp for level ${level}`);
  if (!Number.isFinite(from) || !Number.isFinite(to)) throw new Error("a ramp needs finite levels in dB");
  const span = Math.abs(to - from);
  const secs = Math.max(to > from ? spec.up : spec.down, (span / MAX_DB_PER_STEP) * STEP_SECS);
  const n = Math.max(1, Math.ceil(secs / STEP_SECS - 1e-6));
  const points = [];
  for (let i = 0; i <= n; i++) points.push([+((secs * i) / n).toFixed(4), from + ((to - from) * i) / n]);
  return { secs, points };
}
```

`share/js/sounds.js`:

```js
// The alert sounds. The chime, the two-tone alarm and the dog's bark are
// synthesized here, each from a plan (plain numbers, tested) played on the
// audio stage's alert input. The voice is the one recording: Piper, rendered
// ahead of time (tools/render_voice.py), because the tablet has no speech
// engine and nothing in the car should depend on one.
//
// THE BARK IS HONEST ABOUT BEING SYNTHESIZED: a pitched harmonic burst with a
// fast downward sweep through two formants, over band-passed noise, two
// barks to a call. Better sounds are another day's work (owner, 2026-09-28).

import { withToken } from "./core.js";
import { audioContext, alertIn, resume, setLevelNow } from "./audiobus.js";

export const CHIME = { notes: [{ hz: 659.25, at: 0, secs: 1.0 }, { hz: 880, at: 0.3, secs: 1.3 }],
                       attack: 0.04, peak: 0.4 };
export const ALARM = { tones: [700, 1000], toneSecs: 0.25, peak: 0.6 };
export const BARK = { gap: 0.3, callEvery: 0.8, sweepFrom: 540, sweepTo: 260, secs: 0.16,
                      formants: [1100, 2400], noiseHz: 1700, attack: 0.006, peak: 0.8 };

export function alarmSteps(secs, plan = ALARM) {
  const out = [];
  for (let i = 0; i * plan.toneSecs < secs - 1e-9; i++) {
    out.push([+(i * plan.toneSecs).toFixed(3), plan.tones[i % plan.tones.length]]);
  }
  return out;
}

export function barkCalls(calls, plan = BARK) {
  const out = [];
  for (let c = 0; c < calls; c++) out.push(+(c * plan.callEvery).toFixed(3), +(c * plan.callEvery + plan.gap).toFixed(3));
  return out;
}

export function playChime(at) {
  const ctx = audioContext(), out = alertIn();
  for (const n of CHIME.notes) {
    const t = at + n.at;
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, t);
    env.gain.linearRampToValueAtTime(CHIME.peak, t + CHIME.attack);
    env.gain.exponentialRampToValueAtTime(0.0001, t + n.secs);
    env.connect(out);
    // A bell's first three partials, the upper two quietly.
    for (const [mult, lvl] of [[1, 1], [2, 0.18], [3, 0.06]]) {
      const o = ctx.createOscillator();
      o.type = "sine";
      o.frequency.value = n.hz * mult;
      const g = ctx.createGain();
      g.gain.value = lvl;
      o.connect(g);
      g.connect(env);
      o.start(t);
      o.stop(t + n.secs + 0.05);
    }
  }
}

export function playAlarm(at, secs) {
  const ctx = audioContext();
  const o = ctx.createOscillator();
  o.type = "triangle";
  for (const [t, hz] of alarmSteps(secs)) o.frequency.setValueAtTime(hz, at + t);
  const g = ctx.createGain();
  g.gain.value = ALARM.peak;
  o.connect(g);
  g.connect(alertIn());
  o.start(at);
  o.stop(at + secs);
  return { stop(when) { try { o.stop(when); } catch { /* already stopped */ } } };
}

let noise = null;
function noiseBuffer(ctx) {
  if (noise) return noise;
  noise = ctx.createBuffer(1, Math.floor(ctx.sampleRate / 2), ctx.sampleRate);
  const d = noise.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  return noise;
}

export function playBark(at, calls = 1) {
  const ctx = audioContext(), out = alertIn();
  for (const off of barkCalls(calls)) {
    const t = at + off;
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, t);
    env.gain.linearRampToValueAtTime(BARK.peak, t + BARK.attack);
    env.gain.exponentialRampToValueAtTime(0.001, t + BARK.secs);
    env.connect(out);
    const o = ctx.createOscillator();
    o.type = "sawtooth";
    o.frequency.setValueAtTime(BARK.sweepFrom, t);
    o.frequency.exponentialRampToValueAtTime(BARK.sweepTo, t + BARK.secs * 0.8);
    for (const [hz, q] of [[BARK.formants[0], 5], [BARK.formants[1], 7]]) {
      const bp = ctx.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = hz;
      bp.Q.value = q;
      o.connect(bp);
      bp.connect(env);
    }
    const n = ctx.createBufferSource();
    n.buffer = noiseBuffer(ctx);
    const nb = ctx.createBiquadFilter();
    nb.type = "bandpass";
    nb.frequency.value = BARK.noiseHz;
    nb.Q.value = 1.2;
    const ng = ctx.createGain();
    ng.gain.value = 0.35;
    n.connect(nb);
    nb.connect(ng);
    ng.connect(env);
    o.start(t);
    o.stop(t + BARK.secs + 0.02);
    n.start(t);
    n.stop(t + BARK.secs + 0.02);
  }
}

const decoded = new Map();

export async function playVoice(url, at) {
  const ctx = audioContext();
  if (!decoded.has(url)) {
    decoded.set(url, fetch(withToken(url))
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.arrayBuffer(); })
      .then((b) => ctx.decodeAudioData(b)));
  }
  let buf;
  try { buf = await decoded.get(url); } catch { decoded.delete(url); return null; }
  const s = ctx.createBufferSource();
  s.buffer = buf;
  s.connect(alertIn());
  s.start(Math.max(at, ctx.currentTime));
  return s;
}

// Begin's chime: the one chance to hear that sound reaches the car before
// anything depends on it. Called from the Begin tap, which is the gesture a
// browser wants before it lets a page make a sound.
export async function beginChime() {
  await resume();
  setLevelNow("alert", -6);
  playChime(audioContext().currentTime + 0.05);
}
```

- [ ] **Step 4: Run them to see them pass**

Run: rsync, then `python3 test/js_test.py`. Expected: all green.

- [ ] **Step 5: Begin plays the chime and says to keep the radio on AUX**

In `share/js/views/launcher.js`:
- after `const foot = h("div.launch-foot");`, add:

```js
  // THE CAR CANNOT BE ASKED WHICH SOURCE ITS RADIO IS ON, so this says it.
  const aux = h("div.launch-aux", {},
    "Sound reaches the car through the AUX cable. Keep the car's radio on AUX: "
    + "OmaCar cannot see which source it is on, and the chime you hear on Begin is the check.");
```

- change `root.appendChild(h("div.launch", {}, title, sub, btn, steps, foot));` to `root.appendChild(h("div.launch", {}, title, sub, btn, aux, steps, foot));`.
- in `press()`, after `started = true;`, add:

```js
    // THE CHIME SAYS THE PATH WORKS: from the tablet, down the AUX cable, out
    // of the car's speakers. No sound is not a failed start, so it cannot
    // stop the sequence.
    import("../sounds.js").then((s) => s.beginChime()).catch(() => {});
```

`share/css/drowsy.css`:

```css
/* Drowsy mode on screen (drowsyui.js), its line on Home's Dashcams card, and
   Begin's note about AUX (views/launcher.js). Linked from the redesign/cameras
   block at the end of app.html. */

.launch-aux { max-width: 34rem; margin: 4px auto 0; text-align: center; font-size: .85rem;
              line-height: 1.45; color: var(--dim); }
```

In `share/app.html`, inside the `redesign/cameras` block, after the `cameras.css` link, add:

```html
<link rel="stylesheet" href="css/drowsy.css">
```

- [ ] **Step 6: Run everything, and listen**

Run `BOXTEST`. Expected: all green, including the launcher guards ("the marked line says why", "Open the dashboard anyway" and "Nothing will be recorded" are untouched).

The box has no car speakers. The chime is heard on the tablet in Task 12.

- [ ] **Step 7: Commit**

```bash
git add share/js/ramps.js share/js/sounds.js share/css/drowsy.css share/js/views/launcher.js share/app.html test/js/ramps.test.js test/js/sounds.test.js
git commit -m "Alerts rise and fall in steps nobody can hear, and Begin's chime proves the sound reaches the car" -m "Every level change is a plan of points 100 ms apart and at most 3 dB apart, because a single linear ramp gains most of its decibels in its first instant. The chime, the two-tone alarm (700 and 1000 Hz, inside ISO 7731's range) and a synthesized bark are plans too. Begin says to keep the car's radio on AUX, which the app cannot see.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Vendoring: MediaPipe, and the voice

The owner approved both downloads on 2026-09-28.

**The voice** is Piper's `en_US-ljspeech-high`, trained on the LJ Speech dataset, which is in the public domain. Step 4 checks its model card before anything is rendered. If the card does not say public domain, the fallback is `en_US-libritts_r-medium` (LibriTTS-R, CC BY 4.0, speaker 0), with the attribution changed to match.

**Files:**
- Create:
  - the MediaPipe files: `share/js/vendor/mediapipe/{vision_bundle.mjs, face_landmarker.task, LICENSE, SHA256SUMS, README.md}` and `share/js/vendor/mediapipe/wasm/{vision_wasm_internal.js, vision_wasm_internal.wasm, vision_wasm_nosimd_internal.js, vision_wasm_nosimd_internal.wasm}`;
  - the voice tool: `tools/render_voice.py`;
  - the tests: `test/vendor_test.py`, `test/js/vendor.test.js`.
- Create, not committed: `share/assets/private/voice/{l1-james, l1, l2-james, l2, l3}.ogg`, in the Mac worktree and in the box's canonical `~/Projects/omacar/share/assets/private/voice/`.
- Modify: `share/assets/manifest.json` (five entries), `test/guards_test.py` (one line), `ATTRIBUTION.md` (append), `test/all.sh` (the cameras block).

**Interfaces:**
- Produces:
  - `import("./vendor/mediapipe/vision_bundle.mjs")`, which exports `FilesetResolver` and `FaceLandmarker`, with the wasm under `vendor/mediapipe/wasm/` and the model at `vendor/mediapipe/face_landmarker.task`.
  - Asset names `voice-l1-james`, `voice-l1`, `voice-l2-james`, `voice-l2` and `voice-l3`. Each is served as `assets/private/voice/<clip>.ogg` through `asset(name)` (`share/js/assets.js`).
  - Clip `l1` says "You seem tired. Plan a break soon.", `l2` "Are you with me?" and `l3` "Pull over now.". The `-james` pair begin with "James, ".
  - The name setting (Task 11) can only choose among names that have clips: "James" or none.

- [ ] **Step 1: Write the failing tests**

`test/vendor_test.py`:

```python
#!/usr/bin/env python3
"""What drowsy mode needs and must never fetch on the road. MediaPipe's
tasks-vision 1.0.1 and the Face Landmarker model are checked against the
sizes the owner approved and the hashes pinned in SHA256SUMS. The voice
clips are private, so they are checked in the manifest, and against their
pins wherever they are installed."""

import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import assets  # noqa: E402

V = os.path.join(ROOT, "share", "js", "vendor", "mediapipe")
SIZES = {"vision_bundle.mjs": 155439, "wasm/vision_wasm_internal.js": 323377,
         "wasm/vision_wasm_internal.wasm": 11756954, "face_landmarker.task": 3758596}
NOSIMD = ["wasm/vision_wasm_nosimd_internal.js", "wasm/vision_wasm_nosimd_internal.wasm"]
VOICE = ["voice-l1-james", "voice-l1", "voice-l2-james", "voice-l2", "voice-l3"]

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


head("the vendored face tracker")
for rel, size in SIZES.items():
    p = os.path.join(V, rel)
    check(f"{rel} is the approved {size:,} bytes", os.path.getsize(p) if os.path.isfile(p) else None, size)
for rel in NOSIMD:
    check(f"{rel} is here", os.path.isfile(os.path.join(V, rel)), True)
sums = {}
if os.path.isfile(os.path.join(V, "SHA256SUMS")):
    for line in open(os.path.join(V, "SHA256SUMS"), encoding="utf-8"):
        if line.strip():
            digest, rel = line.split(None, 1)
            sums[rel.strip()] = digest
check("every file is pinned", sorted(sums), sorted(list(SIZES) + NOSIMD))
for rel, want in sums.items():
    p = os.path.join(V, rel)
    check(f"{rel} matches its pin", sha(p) if os.path.isfile(p) else None, want)
lic = os.path.join(V, "LICENSE")
text = open(lic, encoding="utf-8").read() if os.path.isfile(lic) else ""
check("the licence is Apache 2.0", "Apache License" in text and "Version 2.0" in text, True)

head("the voice clips")
m = assets.load_manifest()["assets"]
check("all five phrases are in the manifest", [n for n in VOICE if n not in m], [])
check("each in the private voice folder, as Ogg",
      [n for n in VOICE if n in m and m[n].get("file") != f"voice/{n[len('voice-'):]}.ogg"], [])
check("and every one is pinned", [n for n in VOICE if n in m and not m[n].get("sha256")], [])
st = assets.status()
here = [n for n in VOICE if n in st and st[n]["present"]]
if here:
    check("the ones installed here match their pins", [n for n in here if not st[n]["ok"]], [])
else:
    ok("(no voice clips installed here: the box's test mirror never has them)")

print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  nothing will be fetched on the road\n")
```

Add to the cameras block in `test/all.sh`:

```bash
python3 "$ROOT/test/vendor_test.py" || fails=$((fails + 1))
```

`test/js/vendor.test.js`:

```js
import { ok } from "./assert.js";

export default [
  ["the vendored bundle loads, with the two things drowsy mode uses", async () => {
    const m = await import("../js/vendor/mediapipe/vision_bundle.mjs");
    ok(typeof m.FilesetResolver === "function" && typeof m.FaceLandmarker === "function",
       "FilesetResolver and FaceLandmarker are exported");
  }],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/vendor_test.py` and `python3 test/js_test.py`. Expected: the size checks fail with `None`, and `vendor.test.js :: import` fails.

- [ ] **Step 3: Vendor MediaPipe**

On the Mac:

```bash
cd /Users/jmyers/omgarchy/omacar-cameras
V=share/js/vendor/mediapipe; B=https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1
mkdir -p $V/wasm
curl -fsSL -o $V/vision_bundle.mjs $B/vision_bundle.mjs
for f in vision_wasm_internal.js vision_wasm_internal.wasm vision_wasm_nosimd_internal.js vision_wasm_nosimd_internal.wasm; do
  curl -fsSL -o $V/wasm/$f $B/wasm/$f
done
curl -fsSL -o $V/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
curl -fsSL $B/package.json | python3 -c 'import json,sys; print("licence:", json.load(sys.stdin)["license"])'
curl -fsSL -o $V/LICENSE https://www.apache.org/licenses/LICENSE-2.0.txt
wc -c $V/vision_bundle.mjs $V/wasm/* $V/face_landmarker.task
(cd $V && shasum -a 256 vision_bundle.mjs wasm/vision_wasm_internal.js wasm/vision_wasm_internal.wasm wasm/vision_wasm_nosimd_internal.js wasm/vision_wasm_nosimd_internal.wasm face_landmarker.task > SHA256SUMS)
```

Expected:
- `licence: Apache-2.0`.
- The sizes 155439, 323377, 11756954 and 3758596. A different size means a different file: stop and report it.
- Record the two no-SIMD sizes for the README.

Read the Face Landmarker model card, linked from https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker under "Models". If it says Apache 2.0, the README below stands. If it says anything else, stop and report it to the controller before committing: the owner approved the download as Apache-2.0.

`share/js/vendor/mediapipe/README.md`:

```markdown
# Vendored MediaPipe

Drowsy mode's face tracker, checked in so nothing is fetched on the road.

| File | From | Bytes |
|---|---|---|
| `vision_bundle.mjs` | npm `@mediapipe/tasks-vision@1.0.1` | 155,439 |
| `wasm/vision_wasm_internal.js` | the same | 323,377 |
| `wasm/vision_wasm_internal.wasm` | the same | 11,756,954 |
| `wasm/vision_wasm_nosimd_internal.js` | the same | (its `wc -c`) |
| `wasm/vision_wasm_nosimd_internal.wasm` | the same | (its `wc -c`) |
| `face_landmarker.task` | MediaPipe's model catalogue, float16 v1 | 3,758,596 |

Fetched from https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/ and
https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
for the drive of 2026-09-30. `SHA256SUMS` pins every file and
`test/vendor_test.py` checks them.

The no-SIMD pair is what `FilesetResolver` falls back to in a browser without
WebAssembly SIMD. Chromium on the tablet has SIMD and never loads it.

tasks-vision is Apache License 2.0 (`LICENSE`), as its package.json declares.
The model is Google's, distributed with MediaPipe under Apache 2.0 per its
model card.
```

The two "(its `wc -c`)" cells take the sizes `wc -c` just printed, with thousands separators like the rows above them: they are the one thing in this file that cannot be known before the download.

- [ ] **Step 4: Render the voice clips with Piper**

`tools/render_voice.py`:

```python
#!/usr/bin/env python3
"""Render drowsy mode's spoken phrases with Piper, once, off the tablet.

The tablet has no speech engine, and nothing in the car depends on one. These
are rendered here, turned into Ogg by ffmpeg, and shipped as private assets:
they carry the owner's name, so they stay out of the public repository.

    python tools/render_voice.py MODEL.onnx OUT_DIR

Needs piper-tts in the running Python, and ffmpeg on PATH. The phrases are the
spec's (doc/design/2026-09-28-cameras-drowsy.md), each with and without the
owner's name, because the voice's name setting can only choose between clips
that exist.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import wave

NAME = "James"
PHRASES = {
    "l1-james": f"{NAME}, you seem tired. Plan a break soon.",
    "l1": "You seem tired. Plan a break soon.",
    "l2-james": f"{NAME}, are you with me?",
    "l2": "Are you with me?",
    "l3": "Pull over now.",
}


def synth(voice, text, path):
    with wave.open(path, "wb") as w:
        if hasattr(voice, "synthesize_wav"):     # piper-tts 1.3
            voice.synthesize_wav(text, w)
        else:                                    # piper-tts 1.2
            voice.synthesize(text, w)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    from piper import PiperVoice
    voice = PiperVoice.load(argv[1])
    out = argv[2]
    os.makedirs(out, exist_ok=True)
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    codec = ["-c:a", "libopus", "-b:a", "64k"] if "libopus" in enc else ["-c:a", "libvorbis", "-q:a", "5"]
    tmp = tempfile.mkdtemp()
    try:
        for name, text in PHRASES.items():
            wav = os.path.join(tmp, name + ".wav")
            synth(voice, text, wav)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav,
                            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", *codec,
                            os.path.join(out, name + ".ogg")], check=True)
            print(f"  {name}.ogg  {text}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

First, try Piper on the box (Arch, Python 3.14.7):

```bash
rsync -a --delete --exclude .git --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar-cameras/ jmyers@omarchy:Projects/.omacar-test/cameras/
ssh jmyers@omarchy 'python3 --version && rm -rf /tmp/piper-venv && python3 -m venv /tmp/piper-venv && /tmp/piper-venv/bin/pip install -q piper-tts && /tmp/piper-venv/bin/python -c "import piper; print(\"piper ok\")"'
```

If that printed `Python 3.14.7` and `piper ok`, render on the box:

```bash
ssh jmyers@omarchy 'set -e; W=/tmp/piper-voice; rm -rf $W; mkdir -p $W; cd $W
B=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high
curl -fsSLO $B/en_US-ljspeech-high.onnx; curl -fsSLO $B/en_US-ljspeech-high.onnx.json; curl -fsSL -o MODEL_CARD $B/MODEL_CARD
cat MODEL_CARD; grep -qi "public domain" MODEL_CARD
/tmp/piper-venv/bin/python ~/Projects/.omacar-test/cameras/tools/render_voice.py $W/en_US-ljspeech-high.onnx $W/out
mkdir -p ~/Projects/omacar/share/assets/private/voice && cp $W/out/*.ogg ~/Projects/omacar/share/assets/private/voice/'
mkdir -p share/assets/private/voice && scp 'jmyers@omarchy:/tmp/piper-voice/out/*.ogg' share/assets/private/voice/
```

If `pip install piper-tts` failed on the box (usually because onnxruntime has no wheel for Python 3.14), fall back to the Mac. Use `python3`, and if that fails too, `python3.12`, which is also installed:

```bash
P=$SCRATCH/piper; rm -rf $P; mkdir -p $P
python3 -m venv $P/venv && $P/venv/bin/pip install -q piper-tts \
  || { rm -rf $P/venv; python3.12 -m venv $P/venv && $P/venv/bin/pip install -q piper-tts; }
B=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high
(cd $P && curl -fsSLO $B/en_US-ljspeech-high.onnx && curl -fsSLO $B/en_US-ljspeech-high.onnx.json && curl -fsSL -o MODEL_CARD $B/MODEL_CARD)
cat $P/MODEL_CARD; grep -qi "public domain" $P/MODEL_CARD
$P/venv/bin/python tools/render_voice.py $P/en_US-ljspeech-high.onnx share/assets/private/voice
ssh jmyers@omarchy 'mkdir -p ~/Projects/omacar/share/assets/private/voice'
scp share/assets/private/voice/*.ogg jmyers@omarchy:Projects/omacar/share/assets/private/voice/
```

Either way, the expected output is five lines, `l1-james.ogg` through `l3.ogg`, each with its phrase.

If `grep -qi "public domain"` fails, use the fallback voice instead. Its base is `https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium`, and its files are `en_US-libritts_r-medium.onnx` and `.onnx.json`. Check that its MODEL_CARD names CC BY 4.0, and change the ATTRIBUTION line below to match.

Listen to all five on the Mac (`afplay share/assets/private/voice/l1-james.ogg` plays it if Core Audio decodes it; otherwise `ffplay -nodisp -autoexit`). Check that "James" is said as a name, and that nothing is clipped.

- [ ] **Step 5: The manifest, the pins, the guard and the credits**

In `share/assets/manifest.json`, add these five entries inside `"assets"`, after `"crz-home"`:

```json
    "voice-l1-james": { "file": "voice/l1-james.ogg", "use": "Drowsy mode, Level 1: \"James, you seem tired. Plan a break soon.\" Piper en_US-ljspeech-high, rendered by tools/render_voice.py", "sha256": null },
    "voice-l1": { "file": "voice/l1.ogg", "use": "Drowsy mode, Level 1, with no name: \"You seem tired. Plan a break soon.\"", "sha256": null },
    "voice-l2-james": { "file": "voice/l2-james.ogg", "use": "Drowsy mode, Level 2: \"James, are you with me?\"", "sha256": null },
    "voice-l2": { "file": "voice/l2.ogg", "use": "Drowsy mode, Level 2, with no name: \"Are you with me?\"", "sha256": null },
    "voice-l3": { "file": "voice/l3.ogg", "use": "Drowsy mode, Level 3: \"Pull over now.\"", "sha256": null }
```

Then pin them, on the Mac where the files are:

```bash
for n in voice-l1-james voice-l1 voice-l2-james voice-l2 voice-l3; do python3 lib/assets.py pin $n; done
python3 lib/assets.py status
```

Expected: `ok` for all five voice entries and `crz-xray`, and `--` for `crz-home` if it is still not installed.

In `test/guards_test.py`, the private-assets check lists exactly the car pictures. Change its last line from:

```python
      sorted(_as.load_manifest()["assets"]), ["crz-home", "crz-xray"])
```

to:

```python
      sorted(n for n in _as.load_manifest()["assets"] if n.startswith("crz-")), ["crz-home", "crz-xray"])
```

Append to the "Bundled with the app" section at the end of `ATTRIBUTION.md`:

```markdown
- **MediaPipe tasks-vision 1.0.1** (`share/js/vendor/mediapipe/`: `vision_bundle.mjs`
  and the `wasm/` engine) and the **Face Landmarker** model (`face_landmarker.task`),
  by Google — Apache License 2.0, full text in `share/js/vendor/mediapipe/LICENSE`.
  Drowsy mode reads the cabin camera with them, in the page, with nothing fetched
  on the road.

Not bundled, and credited because the app plays it: **the voice clips**
(`share/assets/private/voice/`, private because they say the owner's name) were
rendered once with Piper TTS and its `en_US-ljspeech-high` voice, trained on the
LJ Speech dataset (public domain). Piper itself does not ship; it ran on another
machine and only the audio came back. `tools/render_voice.py` is how.
```

- [ ] **Step 6: Run everything**

Run `BOXTEST`. Expected:
- `vendor_test.py` passes. On the box it says no voice clips are installed, because the mirror never has them.
- `vendor.test.js` imports the bundle.
- The guards pass with the new manifest.
- The since() guard, which reads every `.js` under `share/js`, still passes with the vendored `wasm/*.js` in scope. If it flags a vendored file, exclude `share/js/vendor/` from that one glob in `guards_test.py` and say so in the commit body.

Then run `python3 test/vendor_test.py` on the Mac as well, where the voice clips are installed. Expected: "the ones installed here match their pins".

- [ ] **Step 7: Commit**

```bash
git add share/js/vendor/mediapipe tools/render_voice.py share/assets/manifest.json test/guards_test.py ATTRIBUTION.md test/vendor_test.py test/js/vendor.test.js test/all.sh
git status --short share/assets   # must list manifest.json only
git commit -m "The face tracker and the voice ship with the app, so drowsy mode fetches nothing on the road" -m "MediaPipe tasks-vision 1.0.1 and the Face Landmarker model are vendored at the sizes the owner approved and pinned by hash. The spoken phrases were rendered once with Piper's public-domain LJ Speech voice and ship as private assets, because they say the owner's name; each also exists without it, which is what the name setting chooses between.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Detection wiring

**Files:**
- Create: `lib/drowsycfg.py`, `share/js/mjpeg.js`, `share/js/facewatch.js`, `share/js/drowsyrun.js`, `tools/drowsy_check.py`, `test/drowsy_test.py`, `test/js/mjpeg.test.js`, `test/js/drowsyrun.test.js`
- Modify: `lib/camroutes.py` (the drowsy routes), `test/all.sh` (the cameras block)

**Interfaces:**
- Consumes:
  - `drowsy.json` and `createMeasures`/`frameFrom` (Task 6);
  - `createLadder`, `createStopClock`, `scaled` (Task 7);
  - the vendored bundle (Task 9);
  - `getJSON`/`postJSON` (Task 3);
  - `/api/live` (records.live) and `/api/cams` (Task 2);
  - `records.write_record(kind, label, payload)`.
- Produces:
  - `drowsycfg`: `load()`, `save(changes)` (raises ValueError), `log_event(data)`, `log_measures(rows)`, `NAMES = ("James", "")`, `SOUNDS`.
  - Routes: `GET /api/drowsy`; `POST /api/drowsy` with `{enabled?, sensitivity?, name?, sounds?}`; `POST /api/drowsy/event` with `{t, level, trigger, speed_kph, measures}`; `POST /api/drowsy/log` with `{rows: [...]}`.
  - `mjpeg.js`: `createMjpegParser() → {push(Uint8Array) → [Uint8Array jpeg]}`.
  - `facewatch.js`: `loadLandmarker(delegate="GPU") → FaceLandmarker` (it falls back to CPU), and `watchCabin({landmarker, canvas, onFrame, fps=12}) → {stop()}`.
  - `drowsyrun.js` pure helpers: `KPH_PER_MPH`, `gateOf(sample, cfg) → {connected, kph, moving, active, parked}`, `chipOf({enabled, gate, measures, cabinLive}) → chip text`.
  - `drowsyrun.js` engine: `drowsy`, with `.state {chip, level, trigger, banner, measures, gate, cfg, cabinLive, error}`, `.canvas`, `.preview`, `.onCues`, `.on(fn)`, `.tap()`, `.reload()`, `.canTest()` and `.test()`; and `startDrowsy()`.

- [ ] **Step 1: Write the failing tests**

`test/drowsy_test.py`:

```python
#!/usr/bin/env python3
"""Drowsy mode's settings and logs: the spec's defaults, the owner's file laid
over them without letting a typo turn a threshold into a string, what the app
may change, and where events and measures are written. Scratch folders only."""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
SCRATCH = tempfile.mkdtemp(prefix="omacar-drowsy-test-")
for _k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_k] = os.path.join(SCRATCH, _k.lower())

import camroutes  # noqa: E402
import drowsycfg  # noqa: E402
import records    # noqa: E402

fails = 0


def head(t):
    print(f"\n  {t}")


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL  {msg}")


def check(msg, got, want):
    if got == want:
        ok(msg)
    else:
        bad(f"{msg} (wanted {want!r}, got {got!r})")


def raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


head("the defaults are the spec's")
c = drowsycfg.load()
check("the ladder", (c["level1"]["perclos"], c["level2"]["closed_secs"], c["level2"]["perclos"],
                     c["level3"]["closed_secs"], c["min_speed_mph"]), (0.15, 1.0, 0.25, 2.0, 30))
check("on, standard, James, every sound", (c["enabled"], c["sensitivity"], c["name"], c["sounds"]),
      (True, "standard", "James", ["bark", "voice", "alarm"]))

head("the owner's file is laid over them, carefully")
os.makedirs(os.path.dirname(drowsycfg.user_path()), exist_ok=True)
with open(drowsycfg.user_path(), "w", encoding="utf-8") as f:
    json.dump({"level2": {"perclos": 0.3}, "eyes": {"closed_cap": "high"}, "surprise": 1}, f)
c = drowsycfg.load()
check("a threshold tuned by hand is used", c["level2"]["perclos"], 0.3)
check("a typo cannot turn a threshold into a string", c["eyes"]["closed_cap"], 0.8)
check("and a key nobody knows is dropped", "surprise" in c, False)

head("the app may change four things, and only to what exists")
c = drowsycfg.save({"sensitivity": "sensitive", "name": "", "sounds": ["alarm", "alarm", "bark"]})
check("saved", (c["sensitivity"], c["name"], c["sounds"]), ("sensitive", "", ["alarm", "bark"]))
check("and the hand-tuned threshold survives the save", c["level2"]["perclos"], 0.3)
check("a name there is no voice clip for is refused", raises(lambda: drowsycfg.save({"name": "Bob"})), True)
check("so is an empty rotation", raises(lambda: drowsycfg.save({"sounds": []})), True)
check("and a threshold from the app", raises(lambda: drowsycfg.save({"level2": {"perclos": 0.1}})), True)
check("through the route, a refusal is a 400",
      camroutes.handle_post("/api/drowsy", json.dumps({"enabled": "yes"}))[0], 400)
check("and GET /api/drowsy answers with the merged settings",
      camroutes.handle_get("/api/drowsy", "")[1]["sensitivity"], "sensitive")

head("events go to the records book; measures to a daily log")
out = drowsycfg.log_event({"t": 1000.0, "level": 2, "trigger": "closed", "speed_kph": 104.6,
                           "measures": {"perclos": 0.18, "closedFor": 1.2}})
row = sqlite3.connect(records.DB).execute(
    "SELECT kind, label, payload FROM records WHERE id = ?", (out["id"],)).fetchone()
check("kind=drowsy, with level, trigger, speed and the measures",
      (row[0], row[1], json.loads(row[2])["speed_kph"], json.loads(row[2])["measures"]["perclos"]),
      ("drowsy", "Drowsy · Level 2 · closed", 104.6, 0.18))
check("a level that does not exist is refused",
      raises(lambda: drowsycfg.log_event({"level": 4, "trigger": "x"})), True)
w = drowsycfg.log_measures([{"t": 1, "perclos": 0.1}, {"t": 2, "perclos": 0.12}])
lines = open(w["file"], encoding="utf-8").read().splitlines()
check("measures append as one JSON line each", [json.loads(x)["t"] for x in lines], [1, 2])

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if fails:
    print(f"  {fails} failed\n")
    sys.exit(1)
print("  drowsy mode's settings hold\n")
```

Add to the cameras block in `test/all.sh`:

```bash
python3 "$ROOT/test/drowsy_test.py" || fails=$((fails + 1))
```

`test/js/mjpeg.test.js`:

```js
import { eq } from "./assert.js";
import { createMjpegParser } from "../js/mjpeg.js";

const enc = (s) => new TextEncoder().encode(s);
const part = (bytes) => {
  const head = enc(`--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${bytes.length}\r\n\r\n`);
  const out = new Uint8Array(head.length + bytes.length + 2);
  out.set(head);
  out.set(bytes, head.length);
  out.set(enc("\r\n"), head.length + bytes.length);
  return out;
};
const J1 = new Uint8Array([0xff, 0xd8, 1, 2, 3, 0xff, 0xd9]);
// A picture with a blank line inside it: the parser must take it by length.
const J2 = new Uint8Array([0xff, 0xd8, 0x0d, 0x0a, 0x0d, 0x0a, 9, 0xff, 0xd9]);
const both = () => { const a = part(J1), b = part(J2); const o = new Uint8Array(a.length + b.length); o.set(a); o.set(b, a.length); return o; };
const arr = (fs) => fs.map((f) => [...f]);

export default [
  ["two parts in one read are two pictures", () => eq(arr(createMjpegParser().push(both())), [[...J1], [...J2]])],
  ["split anywhere, they come out whole", () => {
    const all = both();
    for (let cut = 1; cut < all.length; cut++) {
      const p = createMjpegParser();
      eq(arr([...p.push(all.slice(0, cut)), ...p.push(all.slice(cut))]), [[...J1], [...J2]], `cut at ${cut}`);
    }
  }],
  ["a picture is taken by its length, not by looking for a blank line", () =>
    eq(createMjpegParser().push(part(J2)).map((f) => f.length), [J2.length])],
];
```

`test/js/drowsyrun.test.js`:

```js
import { eq } from "./assert.js";
import { gateOf, chipOf } from "../js/drowsyrun.js";

const cfg = { min_speed_mph: 30 };
const moving = { moving: true };

export default [
  ["30 mph is 48.28 km/h: below it the gate is shut", () =>
    eq([gateOf({ connected: true, values: { SPEED: 48.2 } }, cfg).active,
        gateOf({ connected: true, values: { SPEED: 48.3 } }, cfg).active], [false, true])],
  ["no car is neither active nor parked: a dropped link must not clear an alert", () =>
    eq([gateOf({ connected: false }, cfg).active, gateOf({ connected: false }, cfg).parked], [false, false])],
  ["stationary and connected is parked", () => eq(gateOf({ connected: true, values: { SPEED: 0 } }, cfg).parked, true)],
  ["the chip says one of four things", () =>
    eq([chipOf({ enabled: false }),
        chipOf({ enabled: true, gate: { moving: false } }),
        chipOf({ enabled: true, gate: moving, cabinLive: false }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: true } }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: false } })],
       ["Off", "Paused · parked", "Can't see you", "Can't see you", "Watching"])],
];
```

- [ ] **Step 2: Run them to see them fail**

Run: rsync, then `python3 test/drowsy_test.py` (expected: `No module named 'drowsycfg'`) and `python3 test/js_test.py` (expected: two `:: import` failures).

- [ ] **Step 3: Write lib/drowsycfg.py and its routes**

`lib/drowsycfg.py`:

```python
#!/usr/bin/env python3
"""Drowsy mode's settings, and where its events and measures are written.

    GET  /api/drowsy          share/data/drowsy.json with the owner's
                              ~/.config/omarchy/omacar-drowsy.json laid over it
    POST /api/drowsy          what the app may change: on or off, sensitivity,
                              the name the voice uses, which sounds rotate
    POST /api/drowsy/event    one alert, into the records book as kind=drowsy
    POST /api/drowsy/log      per-second measures, appended to
                              $XDG_STATE_HOME/omacar/drowsy/YYYY-MM-DD.jsonl

Thresholds are not changed from the app. They are changed by hand, in the
file, which is where Wednesday's tuning in the Los Banos office happens: the
measures log beside the recorded cabin clips.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = os.path.join(ROOT, "share", "data", "drowsy.json")
SENSITIVITIES = ("standard", "sensitive")
# The names the voice can say are the ones there are clips for
# (tools/render_voice.py). "" is the clips with no name.
NAMES = ("James", "")
SOUNDS = ("bark", "voice", "alarm")


def user_path():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config")),
                        "omarchy", "omacar-drowsy.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _overlay(base, over):
    """`over` laid on `base` key by key, keeping only keys `base` has and values
    of the same kind, so a hand-edited typo cannot turn a threshold into a
    string."""
    out = {}
    for k, v in base.items():
        o = over.get(k, v)
        if k.startswith("_"):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = _overlay(v, o if isinstance(o, dict) else {})
        elif isinstance(v, bool):
            out[k] = o if isinstance(o, bool) else v
        elif isinstance(v, (int, float)):
            out[k] = o if isinstance(o, (int, float)) and not isinstance(o, bool) else v
        elif isinstance(v, str):
            out[k] = o if isinstance(o, str) else v
        elif isinstance(v, list):
            out[k] = o if isinstance(o, list) else v
        else:
            out[k] = v
    return out


def load():
    return _overlay(_read(DEFAULTS), _read(user_path()))


def save(changes):
    if not isinstance(changes, dict):
        raise ValueError("the settings must be an object")
    user = _read(user_path())
    for k, v in changes.items():
        if k == "enabled" and isinstance(v, bool):
            user[k] = v
        elif k == "sensitivity" and v in SENSITIVITIES:
            user[k] = v
        elif k == "name" and v in NAMES:
            user[k] = v
        elif k == "sounds" and isinstance(v, list) and v and all(s in SOUNDS for s in v):
            user[k] = list(dict.fromkeys(v))
        else:
            raise ValueError(f"{k!r} cannot be set to {v!r} from the app")
    os.makedirs(os.path.dirname(user_path()), exist_ok=True)
    tmp = user_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(user, f, indent=2)
    os.replace(tmp, user_path())
    return load()


def log_event(data):
    import records
    level, trigger = data.get("level"), data.get("trigger")
    if level not in (1, 2, 3) or not isinstance(trigger, str):
        raise ValueError("an event needs a level of 1, 2 or 3 and a trigger")
    payload = {"t": data.get("t") or time.time(), "level": level, "trigger": trigger,
               "speed_kph": data.get("speed_kph"), "measures": data.get("measures") or {}}
    return {"id": records.write_record("drowsy", f"Drowsy · Level {level} · {trigger}", payload)}


def log_dir():
    return os.path.join(os.path.expanduser(os.environ.get("XDG_STATE_HOME", "~/.local/state")),
                        "omacar", "drowsy")


def log_measures(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    os.makedirs(log_dir(), exist_ok=True)
    path = os.path.join(log_dir(), time.strftime("%Y-%m-%d") + ".jsonl")
    kept = [r for r in rows[:600] if isinstance(r, dict)]
    with open(path, "a", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return {"written": len(kept), "file": path}
```

In `lib/camroutes.py`:
- add to the docstring's route list:

```
    GET  /api/drowsy, POST /api/drowsy, POST /api/drowsy/event,
    POST /api/drowsy/log                   drowsy mode (lib/drowsycfg.py)
```

- in `handle_get`, before `return None`:

```python
    if path == "/api/drowsy":
        import drowsycfg
        return 200, drowsycfg.load()
```

- in `handle_post`, before `return None`:

```python
    if path in ("/api/drowsy", "/api/drowsy/event", "/api/drowsy/log"):
        import drowsycfg
        data = _body(body)
        if data is None:
            return 400, {"error": "the body must be a JSON object"}
        try:
            if path == "/api/drowsy":
                return 200, drowsycfg.save(data)
            if path == "/api/drowsy/event":
                return 200, drowsycfg.log_event(data)
            return 200, drowsycfg.log_measures(data.get("rows"))
        except ValueError as e:
            return 400, {"error": str(e)}
```

- [ ] **Step 4: Write mjpeg.js, facewatch.js and drowsyrun.js**

`share/js/mjpeg.js`:

```js
// Frames out of the recorder's live stream (lib/cams.py, stream_live): a
// multipart body where every part carries its own Content-Length. A part is
// taken by its length, never by searching for the boundary or a blank line,
// which a JPEG is free to contain.

const CRLF2 = [13, 10, 13, 10];

function find(buf, seq) {
  outer: for (let i = 0; i + seq.length <= buf.length; i++) {
    for (let j = 0; j < seq.length; j++) if (buf[i + j] !== seq[j]) continue outer;
    return i;
  }
  return -1;
}

export function createMjpegParser() {
  let buf = new Uint8Array(0);
  const dec = new TextDecoder();
  return {
    push(chunk) {
      const joined = new Uint8Array(buf.length + chunk.length);
      joined.set(buf);
      joined.set(chunk, buf.length);
      buf = joined;
      const out = [];
      for (;;) {
        const head = find(buf, CRLF2);
        if (head < 0) break;
        const m = /content-length:\s*(\d+)/i.exec(dec.decode(buf.subarray(0, head)));
        if (!m) { buf = buf.slice(head + 4); continue; }
        const start = head + 4, n = Number(m[1]);
        if (buf.length < start + n) break;
        out.push(buf.slice(start, start + n));
        buf = buf.slice(start + n);
      }
      return out;
    },
  };
}
```

`share/js/facewatch.js`:

```js
// The cabin camera, watched: the recorder's live picture, through MediaPipe's
// Face Landmarker (vendored in share/js/vendor/mediapipe), into the measures'
// frame shape (drowsy.js, frameFrom). The recorder owns the camera -- a V4L2
// device has one owner -- so this reads its JPEGs rather than opening the
// device a second time.
//
// The page draws each picture into a canvas and hands the canvas to the
// landmarker in VIDEO mode, with blendshapes and the facial transformation
// matrix switched on.

import { withToken } from "./core.js";
import { createMjpegParser } from "./mjpeg.js";
import { frameFrom } from "./drowsy.js";

const VENDOR = new URL("./vendor/mediapipe/", import.meta.url);

// The 12 MB engine loads here and only here, the first time a cabin picture
// is worth watching. GPU first; the CPU if the GPU will not start.
export async function loadLandmarker(delegate = "GPU") {
  const { FilesetResolver, FaceLandmarker } = await import("./vendor/mediapipe/vision_bundle.mjs");
  const fileset = await FilesetResolver.forVisionTasks(new URL("wasm", VENDOR).href);
  const make = (d) => FaceLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: new URL("face_landmarker.task", VENDOR).href, delegate: d },
    runningMode: "VIDEO",
    numFaces: 1,
    outputFaceBlendshapes: true,
    outputFacialTransformationMatrixes: true,
  });
  try { return await make(delegate); } catch (e) { if (delegate !== "CPU") return make("CPU"); throw e; }
}

export function watchCabin({ landmarker, canvas, onFrame, fps = 12 }) {
  let stopped = false, ctrl = null, lastMs = 0, busy = false;
  const g = canvas.getContext("2d");
  (async () => {
    while (!stopped) {
      try {
        ctrl = new AbortController();
        const r = await fetch(withToken("/api/cams/cabin/live"), { signal: ctrl.signal, cache: "no-store" });
        if (!r.ok || !r.body) throw new Error(String(r.status));
        const reader = r.body.getReader();
        const parser = createMjpegParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done || stopped) break;
          const jpgs = parser.push(value);
          const now = performance.now();
          if (!jpgs.length || busy || now - lastMs < 1000 / fps) continue;
          lastMs = now;
          busy = true;
          try {
            const bmp = await createImageBitmap(new Blob([jpgs[jpgs.length - 1]], { type: "image/jpeg" }));
            if (canvas.width !== bmp.width) { canvas.width = bmp.width; canvas.height = bmp.height; }
            g.drawImage(bmp, 0, 0);
            bmp.close();
            onFrame(frameFrom(landmarker.detectForVideo(canvas, now), now / 1000));
          } finally { busy = false; }
        }
      } catch { /* the stream ended or never started: try again shortly */ }
      if (!stopped) await new Promise((res) => setTimeout(res, 3000));
    }
  })();
  return { stop() { stopped = true; if (ctrl) ctrl.abort(); } };
}
```

`share/js/drowsyrun.js`:

```js
// Drowsy mode, running. The gate (above 30 mph, connected, moving), the cabin
// watch, the measures, the ladder and the logs. It keeps one state object that
// the screens draw (drowsyui.js), and hands the ladder's cues to whoever sounds
// them (alertplayer.js); it draws nothing itself.
//
// Detection runs only while the car is moving, or while the settings sheet's
// preview is open, and only when the recorder has a live cabin picture. A
// parked car and a desk machine never load the engine at all.

import { getJSON, postJSON } from "./camapi.js";
import { createMeasures } from "./drowsy.js";
import { createLadder, createStopClock, scaled } from "./ladder.js";
import { loadLandmarker, watchCabin } from "./facewatch.js";

export const KPH_PER_MPH = 1.609344;

// The gate, from a live sample. A dropped link is neither active nor parked:
// an adapter hiccup must not clear an alert that is sounding.
export function gateOf(sample, cfg) {
  const s = sample || {};
  const v = s.connected ? (s.values || {}).SPEED : null;
  const kph = typeof v === "number" ? v : null;
  return { connected: !!s.connected, kph, moving: kph !== null && kph > 0,
           active: kph !== null && kph >= cfg.min_speed_mph * KPH_PER_MPH,
           parked: !!s.connected && kph === 0 };
}

// The status chip, in the spec's four words.
export function chipOf({ enabled, gate, measures, cabinLive }) {
  if (!enabled) return "Off";
  if (!gate || !gate.moving) return "Paused · parked";
  if (!cabinLive || !measures || measures.faceLost) return "Can't see you";
  return "Watching";
}

const listeners = new Set();
let cfg = null, measures = null, ladder = null, stops = null;
let gate = null, cabinLive = false;
let landmarker = null, loading = null, watcher = null, retryAt = 0;
let tapPending = false, lastSnap = null, logBuf = [], lastLogT = 0;

const now = () => performance.now() / 1000;

export const drowsy = {
  state: { chip: "Off", level: 0, trigger: null, banner: false, measures: null,
           gate: null, cfg: null, cabinLive: false, error: null },
  canvas: document.createElement("canvas"),
  preview: false,
  onCues: null,
  on(fn) { listeners.add(fn); fn(this.state); return () => listeners.delete(fn); },
  tap() { tapPending = true; tick(); },
  async reload() { await loadConfig(); },
  canTest() { return !(gate && gate.moving); },
  // "Test the alerts", only while parked: each level in turn, as the ladder
  // would sound it, then a fade.
  test() {
    if (!this.canTest() || !this.onCues) return false;
    const say = (cues, level, at) => setTimeout(() => this.onCues(cues, { level }), at * 1000);
    say([{ kind: "chime" }, { kind: "voice", clip: "l1" }], 1, 0);
    say([{ kind: "fade" }], 0, 5);
    say([{ kind: "duck" }, { kind: "bark" }], 2, 8);
    say([{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], 3, 14);
    say([{ kind: "fade" }], 0, 20);
    return true;
  },
};

async function loadConfig() {
  try { cfg = await getJSON("/api/drowsy"); } catch { drowsy.state.error = "No settings from the server"; publish(); return; }
  const c = scaled(cfg, cfg.sensitivity);
  measures = createMeasures(c);
  ladder = createLadder(c, cfg.sounds);
  stops = stops || createStopClock(c);
  drowsy.state.cfg = cfg;
  drowsy.state.error = null;
  publish();
}

function publish() {
  const st = drowsy.state;
  st.gate = gate;
  st.cabinLive = cabinLive;
  st.measures = lastSnap;
  st.chip = chipOf({ enabled: !!(cfg && cfg.enabled), gate, measures: lastSnap, cabinLive });
  for (const fn of listeners) { try { fn(st); } catch (e) { console.error(e); } }
}

function logRow(out) {
  const m = lastSnap || {};
  return { t: Math.round(Date.now() / 100) / 10, kph: gate ? gate.kph : null, active: !!(gate && gate.active),
           face: !!m.face, blink: m.blink ?? null, closed: !!m.closed, closedFor: m.closedFor ?? 0,
           perclos: m.perclos ?? null, yawns: m.yawns ?? 0, nods: m.nods ?? 0, pitch: m.pitch ?? null,
           baseline: m.baseline ?? null, level: out.level };
}

function logEvent(out) {
  const m = lastSnap || {};
  postJSON("/api/drowsy/event", {
    t: Date.now() / 1000, level: out.raised.level, trigger: out.raised.trigger, speed_kph: gate ? gate.kph : null,
    measures: { perclos: m.perclos ?? null, closedFor: m.closedFor ?? 0, yawns: m.yawns ?? 0, nods: m.nods ?? 0,
                blink: m.blink ?? null, baseline: m.baseline ?? null, faceLost: !!m.faceLost },
  }).catch(() => { /* the alert still sounded; the record is the loss */ });
}

async function flushLog() {
  if (!logBuf.length) return;
  const rows = logBuf;
  logBuf = [];
  try { await postJSON("/api/drowsy/log", { rows }); } catch { /* a lost ten seconds of measures */ }
}

function tick(snap) {
  if (!cfg || !ladder) return;
  if (snap) lastSnap = snap;
  const t = now();
  const sc = stops.feed(t, gate ? gate.kph : null, !!(gate && gate.connected));
  const out = ladder.step({
    t, active: !!(cfg.enabled && gate && gate.active), parked: !!(gate && gate.parked),
    m: lastSnap || { face: false, faceLost: true }, sinceStop: sc.sinceStop, stoppedFor: sc.stoppedFor,
    hour: new Date().getHours(), tap: tapPending,
  });
  tapPending = false;
  drowsy.state.level = out.level;
  drowsy.state.trigger = out.trigger;
  drowsy.state.banner = out.banner;
  if (out.raised) logEvent(out);
  if (out.cues.length && drowsy.onCues) drowsy.onCues(out.cues, out);
  if (gate && gate.moving && t - lastLogT >= 1) { lastLogT = t; logBuf.push(logRow(out)); }
  publish();
}

async function startWatch() {
  if (loading) return;
  try {
    if (!landmarker) { loading = loadLandmarker("GPU"); landmarker = await loading; }
    if (!watcher) {
      watcher = watchCabin({ landmarker, canvas: drowsy.canvas,
        onFrame: (f) => { f.gated = !!(gate && gate.active); tick(measures.feed(f)); } });
    }
  } catch (e) {
    // A minute before trying again: a failed load is 12 MB, and this is asked
    // twice a second.
    retryAt = now() + 60;
    drowsy.state.error = "The face tracker did not load: " + ((e && e.message) || e);
  } finally { loading = null; }
}

function syncWatch() {
  const want = !!(cfg && cfg.enabled && cabinLive && ((gate && gate.moving) || drowsy.preview));
  if (want && !watcher && now() >= retryAt) startWatch();
  if (!want && watcher) { watcher.stop(); watcher = null; }
}

async function pollLive() {
  let sample = null;
  try { sample = await getJSON("/api/live"); } catch { /* no server: no gate */ }
  if (cfg) gate = gateOf(sample, cfg);
  syncWatch();
  tick();
}

async function pollCams() {
  try {
    const ov = await getJSON("/api/cams");
    cabinLive = !!(ov.running && ov.roles.cabin && ov.roles.cabin.live);
  } catch { cabinLive = false; }
  syncWatch();
}

export async function startDrowsy() {
  await loadConfig();
  pollLive();
  pollCams();
  setInterval(pollLive, 500);
  setInterval(pollCams, 5000);
  setInterval(flushLog, 10000);
}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: rsync, then `python3 test/drowsy_test.py` and `python3 test/js_test.py`. Expected: all green.

- [ ] **Step 6: The face tracker on the real cabin camera**

`tools/drowsy_check.py`:

```python
#!/usr/bin/env python3
"""Does drowsy mode's face tracker load and run here, on the recorder's real
cabin picture, fetching nothing from anywhere but this machine?

Needs the recorder running with a live cabin camera (`omacar cams sim` on the
box, where the C920 is the cabin). This serves share/ and the cabin's live
MJPEG itself, opens headless Chromium on a page that loads the vendored
MediaPipe, runs the Face Landmarker on 20 frames, and has the page post back
what happened. It is a step in the plan rather than a line in test/all.sh,
because it needs a camera.

    python3 tools/drowsy_check.py
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "test"))
import cams  # noqa: E402
from app_test import browser, free_port  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><title>drowsy check</title><canvas id="c"></canvas>
<script type="module">
import { loadLandmarker, watchCabin } from "./js/facewatch.js";
const out = { loaded: false, loadMs: null, frames: 0, faces: 0, secs: null, error: null, resources: [] };
try {
  const t0 = performance.now();
  const lm = await loadLandmarker("CPU");
  out.loaded = true;
  out.loadMs = Math.round(performance.now() - t0);
  const t1 = performance.now();
  await new Promise((done) => {
    const w = watchCabin({ landmarker: lm, canvas: document.getElementById("c"),
      onFrame: (f) => { out.frames++; if (f.face) out.faces++; if (out.frames >= 20) { w.stop(); done(); } } });
    setTimeout(() => { w.stop(); done(); }, 60000);
  });
  out.secs = (performance.now() - t1) / 1000;
} catch (e) { out.error = String((e && e.message) || e); }
out.resources = performance.getEntriesByType("resource").map((r) => r.name);
await fetch("/_result", { method: "POST", body: JSON.stringify(out) });
</script>
"""

RESULT = {}
DONE = threading.Event()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=SHARE, **k)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.partition("?")[0]
        if path == "/_check.html":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/cams/cabin/live":
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + cams.BOUNDARY)
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                cams.stream_live(self.wfile, "cabin")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        RESULT.update(json.loads(self.rfile.read(n) or b"{}"))
        self.send_response(204)
        self.end_headers()
        DONE.set()


def main():
    exe = browser()
    if not exe:
        print("  no chromium here")
        return 2
    if not os.path.exists(cams.live_path("cabin")):
        print(f"  no cabin picture at {cams.live_path('cabin')}: start the recorder first")
        return 2
    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp()
    ch = subprocess.Popen([exe, "--headless=new", "--no-sandbox", f"--user-data-dir={prof}",
                           f"http://127.0.0.1:{port}/_check.html"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        DONE.wait(120)
    finally:
        ch.terminate()
        try:
            ch.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ch.kill()
        srv.shutdown()
        shutil.rmtree(prof, ignore_errors=True)
    r = RESULT
    if not r:
        print("  FAIL  the page never reported back")
        return 1
    foreign = [u for u in r["resources"] if not u.startswith(f"http://127.0.0.1:{port}/")]
    print(f"  loaded   {r['loaded']} in {r['loadMs']} ms (CPU delegate)")
    print(f"  frames   {r['frames']} in {r['secs'] and round(r['secs'], 1)} s, {r['faces']} with a face")
    print(f"  error    {r['error']}")
    print(f"  fetched  {len(r['resources'])} resources, {len(foreign)} from anywhere else")
    for u in foreign:
        print(f"    FOREIGN  {u}")
    good = r["loaded"] and r["frames"] >= 20 and not foreign and not r["error"]
    print("\n  " + ("ok" if good else "FAIL") + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
```

Run it on the box, against the C920 as the cabin, on scratch folders:

```bash
rsync -a --delete --exclude .git --exclude share/assets/private/ /Users/jmyers/omgarchy/omacar-cameras/ jmyers@omarchy:Projects/.omacar-test/cameras/
ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && S=/tmp/omacar-check && rm -rf $S && mkdir -p $S/videos && mkdir -p -m 700 $S/run && export OMACAR_VIDEOS=$S/videos XDG_RUNTIME_DIR=$S/run && (setsid -f python3 lib/cams.py sim >$S/cams.log 2>&1) && sleep 15 && python3 tools/drowsy_check.py; pkill -INT -f "[l]ib/cams.py sim"; sleep 3; rm -rf $S'
```

Expected:
- `loaded True`.
- `frames 20`, at about 10 a second (the recorder's live rate). The count of frames with a face is whatever is in front of the C920.
- `0 from anywhere else`, then `ok`.

If `loaded` is False, read `error`. A MIME error means the server sent the `.mjs` or `.wasm` with the wrong type: Python 3.14's `mimetypes` gives `text/javascript` and `application/wasm`, so check which Python ran the tool.

- [ ] **Step 7: Run everything**

Run `BOXTEST`. Expected: all green. `app_test.py` does not load drowsy mode yet; that is Task 11.

- [ ] **Step 8: Commit**

```bash
git add lib/drowsycfg.py lib/camroutes.py share/js/mjpeg.js share/js/facewatch.js share/js/drowsyrun.js tools/drowsy_check.py test/drowsy_test.py test/js/mjpeg.test.js test/js/drowsyrun.test.js test/all.sh
git commit -m "Drowsy mode reads the cabin camera's live picture through the vendored face tracker, and logs what it measures" -m "The recorder owns the camera, so the page reads its MJPEG, draws each frame to a canvas and runs the Face Landmarker only while the car is moving, with the gate at 30 mph deciding what may alert. A dropped link is not parked, so it cannot clear an alert. Settings, events (kind=drowsy) and a per-second measures log for Wednesday's tuning go through lib/drowsycfg.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Drowsy mode on screen, and in the car's speakers

**Files:**
- Create: `share/js/alertplayer.js`, `share/js/drowsyui.js`, `test/js/alertplayer.test.js`
- Modify:
  - the page: `share/js/alertness.js` (start drowsy mode), `share/js/dashcard.js` (the chip and the AUX line), `share/css/drowsy.css` (append);
  - `share/js/main.js`: one hunk, the Settings row.

**Interfaces:**
- Consumes:
  - `drowsy`, `startDrowsy` (Task 10);
  - `rampPlan` (Task 8);
  - `playChime`, `playAlarm`, `playBark`, `playVoice` (Task 8);
  - `audioContext`, `schedule`, `setLevelNow`, `currentDb`, `resume`, `MUSIC_DB`, `FLOOR_DB` (Task 5);
  - `asset(name)` (`share/js/assets.js`);
  - `onAudio`, `auxLine` (Task 5);
  - `dashcamCard(...).top` (Task 4).
- Produces:
  - `alertplayer.js`: `ALERT_DB = {1: -6, 2: -3, 3: 0}`, `ALERT_FLOOR_DB = -40`, `SWELL_DB = 6`, `DUCK_DB = -12`, `VOICE_AFTER`, `rampsFor(cue, level, cur) → [{bus, plan, at, append}]`, and `createAlertPlayer({name}) → {play(cues, out)}`.
  - `drowsyui.js`: `mountDrowsyUI()` and `openDrowsySheet()`. With `?dz=1|2|3` it draws that level's card for a screenshot, with no sound.
  - The DOM: the chip `.tb-drowsy` in `#vbar .tb-right`; the alert layer `.dz-layer[data-level]`; the banner `.dz-banner`.

- [ ] **Step 1: Write the failing test**

`test/js/alertplayer.test.js`:

```js
import { eq, ok } from "./assert.js";
import { rampsFor, ALERT_DB } from "../js/alertplayer.js";
import { ALERT_MAX_DB } from "../js/audiobus.js";

const quiet = { music: -12, alert: -Infinity };
const ends = (r) => r.map((x) => [x.bus, x.plan.points[0][1], x.plan.points[x.plan.points.length - 1][1], x.plan.secs, x.at]);

export default [
  ["no alert is louder than full scale, and Level 3 uses all of it", () => {
    ok(Object.values(ALERT_DB).every((db) => db <= ALERT_MAX_DB), "every level at or under 0 dBFS");
    eq(ALERT_DB[3], 0);
  }],
  ["Level 1: the radio rises 6 dB over 10 s, then settles back over 30 s", () =>
    eq(ends(rampsFor({ kind: "swell" }, 1, quiet)), [["music", -12, -6, 10, 0], ["music", -6, -12, 30, 10]])],
  ["Level 1's soft sounds need no ramp", () => eq(rampsFor({ kind: "chime" }, 1, quiet), [])],
  ["Level 2: music ducks 12 dB in half a second", () =>
    eq(ends(rampsFor({ kind: "duck" }, 2, quiet)), [["music", -12, -24, 0.5, 0]])],
  ["and the first sound rises from -40 dB to its target over 1.5 s, from silence", () =>
    eq(ends(rampsFor({ kind: "bark" }, 2, quiet)), [["alert", -40, -3, 1.5, 0]])],
  ["a repeat with the bus already up does not ramp again", () =>
    eq(rampsFor({ kind: "bark" }, 2, { music: -24, alert: -3 }), [])],
  ["Level 3's alarm reaches full scale over 3 s", () =>
    eq(ends(rampsFor({ kind: "alarm", hold: true }, 3, quiet)), [["alert", -40, 0, 3, 0]])],
  ["from Level 2 it rises the last 3 dB, still over 3 s", () =>
    eq(ends(rampsFor({ kind: "alarm", hold: true }, 3, { music: -24, alert: -3 })), [["alert", -3, 0, 3, 0]])],
  ["release: the alert fades out over 3 s and the music comes back", () =>
    eq(ends(rampsFor({ kind: "fade" }, 0, { music: -24, alert: 0 })), [["alert", 0, -60, 3, 0], ["music", -24, -12, 3, 0]])],
  ["a fade from silence is still a finite plan", () =>
    eq(ends(rampsFor({ kind: "fade" }, 0, quiet)), [["alert", -60, -60, 3, 0], ["music", -12, -12, 3, 0]])],
];
```

- [ ] **Step 2: Run it to see it fail**

Run: rsync, then `python3 test/js_test.py`. Expected: `alertplayer.test.js :: import`.

- [ ] **Step 3: Write alertplayer.js**

```js
// Drowsy mode's cues, sounded. Each cue the ladder hands over (ladder.js)
// becomes ramps on the audio stage (audiobus.js, ramps.js) and a sound
// (sounds.js). Every level change is a ramp. The only thing ever set in one
// step is a bus with nothing playing on it.

import { audioContext, schedule, setLevelNow, currentDb, resume, MUSIC_DB, FLOOR_DB } from "./audiobus.js";
import { rampPlan } from "./ramps.js";
import { playChime, playAlarm, playBark, playVoice } from "./sounds.js";
import { asset } from "./assets.js";

// Where each level's alert sits, in dBFS. Level 3 uses the full scale.
export const ALERT_DB = { 1: -6, 2: -3, 3: 0 };
export const ALERT_FLOOR_DB = -40;   // an alert's rise starts here, never from nothing
export const SWELL_DB = 6;           // Level 1: the radio rises +6 dB
export const DUCK_DB = -12;          // Levels 2 and 3: music ducks -12 dB
// When the voice starts after its cue: after Level 1's chime, part-way up
// Level 2's rise, and once Level 3's alarm has begun.
export const VOICE_AFTER = { 1: 1.4, 2: 0.5, 3: 1.0 };
const SOUNDS = new Set(["chime", "bark", "alarm", "voice"]);

// Pure: the ramps a cue asks for, from where the two buses are now.
export function rampsFor(cue, level, cur) {
  const c = { music: Math.max(FLOOR_DB, cur.music), alert: Math.max(FLOOR_DB, cur.alert) };
  const r = (bus, plan, at = 0, append = false) => ({ bus, plan, at, append });
  if (cue.kind === "swell") {
    const up = rampPlan(1, c.music, MUSIC_DB + SWELL_DB);
    return [r("music", up), r("music", rampPlan(1, MUSIC_DB + SWELL_DB, MUSIC_DB), up.secs, true)];
  }
  if (cue.kind === "duck") return [r("music", rampPlan(level, c.music, MUSIC_DB + DUCK_DB))];
  if (cue.kind === "fade") return [r("alert", rampPlan(0, c.alert, FLOOR_DB)), r("music", rampPlan(0, c.music, MUSIC_DB))];
  if (SOUNDS.has(cue.kind) && level >= 2 && c.alert < ALERT_DB[level] - 0.5) {
    return [r("alert", rampPlan(level, Math.max(ALERT_FLOOR_DB, c.alert), ALERT_DB[level]))];
  }
  return [];
}

export function createAlertPlayer({ name = () => "James" } = {}) {
  let level = 0;
  let held = [];                     // what the next fade must stop

  async function voiceUrl(clip) {
    const withName = clip !== "l3" && name() === "James";
    const a = await asset(`voice-${clip}${withName ? "-james" : ""}`);
    return a && a.url ? a.url : null;
  }

  async function sound(cue, at) {
    if (cue.kind === "chime") playChime(at);
    else if (cue.kind === "bark") playBark(at, level >= 2 ? 3 : 1);
    else if (cue.kind === "alarm") held.push(playAlarm(at, cue.hold ? 600 : (cue.secs || 3)));
    else if (cue.kind === "voice") {
      const url = await voiceUrl(cue.clip);
      const s = url ? await playVoice(url, at + (VOICE_AFTER[level] || 0)) : null;
      if (s) held.push(s);
    }
  }

  return {
    async play(cues, out) {
      await resume();
      const ctx = audioContext();
      for (const cue of cues) {
        const at = ctx.currentTime + 0.05;
        const cur = { music: currentDb("music"), alert: currentDb("alert") };
        if (cue.kind === "fade") {
          for (const x of rampsFor(cue, 0, cur)) schedule(x.bus, x.plan.points, at + x.at, x.append);
          for (const s of held) { try { s.stop(at + 3.05); } catch { /* already stopped */ } }
          held = [];
          level = 0;
          continue;
        }
        level = (out && out.level) || level;
        if (level === 1 && SOUNDS.has(cue.kind)) setLevelNow("alert", ALERT_DB[1]);
        for (const x of rampsFor(cue, level, cur)) schedule(x.bus, x.plan.points, at + x.at, x.append);
        if (SOUNDS.has(cue.kind)) await sound(cue, at);
      }
    },
  };
}
```

- [ ] **Step 4: Run it to see it pass**

Run: rsync, then `python3 test/js_test.py`. Expected: all green.

- [ ] **Step 5: Write drowsyui.js**

```js
// Drowsy mode on screen: the status chip in the top bar, the alert cards, the
// Level 3 banner, and the settings sheet (Settings -> Drowsy mode).
//
// The chip goes into the top bar from here. main.js builds that bar once and
// never rebuilds it ("the vehicle bar"), so an element added beside its own
// stays put, and main.js carries no line for it.
//
// ?dz=1, 2 or 3 draws that level's card with no sound, for screenshots.

import { h, clear, toast } from "./core.js";
import { postJSON } from "./camapi.js";
import { drowsy } from "./drowsyrun.js";
import { onAudio } from "./audiostate.js";

const TRIGGER = {
  perclos: "Your eyes have been closing more and more",
  closed: "Your eyes closed",
  yawns: "You have been yawning",
  nods: "Your head has been nodding",
  "since-stop": "Two hours without a stop",
  night: "It is the small hours",
  "repeat-l2": "A second wake-up within five minutes",
};
const TONE = { "Watching": "ok", "Can't see you": "warn", "Paused · parked": "", "Off": "" };
const SOUND_LABEL = { bark: "The bark", voice: "The voice", alarm: "The two-tone alarm" };
export const REST = "Alerts buy you minutes, not safety. The fix is to stop and rest: "
  + "a 20-minute nap, or a coffee (NHTSA, AAA Foundation).";
export const AUX = "Keep the car's radio on AUX. OmaCar's sound reaches the car through that cable, "
  + "and it cannot see which source the radio is on.";

function awake(big) {
  return h("button.dz-awake" + (big ? ".big" : ""), { type: "button", onclick: () => drowsy.tap() }, "I'm awake");
}

function paintLayer(layer, level, trigger) {
  clear(layer);
  layer.hidden = !level;
  layer.dataset.level = String(level || 0);
  const why = h("div.dz-s", TRIGGER[trigger] || "");
  if (level === 1) layer.append(h("div.dz-card", h("div.dz-t", "You seem tired. Plan a break soon."), why, h("p.dz-rest", REST), awake(false)));
  if (level === 2) layer.append(h("div.dz-full", h("div.dz-t", "Are you with me?"), why, awake(true), h("p.dz-rest", REST)));
  if (level === 3) layer.append(h("div.dz-full.l3", h("div.dz-t", "Pull over now"), h("div.dz-s", "Stop at the next safe place"), awake(true), h("p.dz-rest", REST)));
}

export function mountDrowsyUI() {
  const chip = h("button.tb-drowsy", { type: "button", onclick: () => openDrowsySheet() },
    h("span.tb-drowsy-dot"), h("span.tb-drowsy-t", "Off"));
  const place = () => {
    const right = document.querySelector("#vbar .tb-right");
    if (!right) return false;
    right.insertBefore(chip, right.firstChild);
    return true;
  };
  if (!place()) {
    const mo = new MutationObserver(() => { if (place()) mo.disconnect(); });
    mo.observe(document.getElementById("vbar"), { childList: true, subtree: true });
  }
  const layer = h("div.dz-layer", { hidden: true });
  const banner = h("div.dz-banner", { hidden: true, role: "status" }, "Stop at the next safe place");
  document.getElementById("app").append(layer, banner);

  const preview = Number(new URLSearchParams(location.search).get("dz")) || 0;
  let shown = -1;
  drowsy.on((st) => {
    chip.querySelector(".tb-drowsy-t").textContent = st.chip;
    chip.dataset.tone = TONE[st.chip] || "";
    chip.title = st.chip === "Watching" && st.gate && !st.gate.active
      ? "Watching. Alerts start above 30 mph." : `Drowsy mode: ${st.chip}`;
    banner.hidden = !(st.banner && st.level === 0);
    const level = preview || st.level;
    if (level !== shown) { shown = level; paintLayer(layer, level, st.trigger || (preview ? "closed" : null)); }
  });
}

function measureLine(m) {
  if (!m) return "No picture from the cabin camera yet.";
  if (!m.face) return "No face in view.";
  const pc = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
  const n2 = (x) => (x === null || x === undefined ? "–" : x.toFixed(2));
  const base = m.calibrated
    ? `baseline ${n2(m.baseline)}, closed above ${n2(m.threshold)}`
    : "learning your eyes: the first minute above 30 mph";
  const pitch = m.pitch === null || m.pitch === undefined ? "–" : `${Math.round(m.pitch)}°`;
  return `Closure ${n2(m.blink)} (${base}) · PERCLOS ${pc(m.perclos)} · yawns ${m.yawns} · nods ${m.nods} · pitch ${pitch}`;
}

function audioLine(a) {
  if (!a) return "The server did not say where sound is going.";
  const vol = a.volume === null || a.volume === undefined ? "an unknown volume" : `${Math.round(a.volume * 100)}%`;
  const where = a.aux === true ? "the AUX cable" : a.aux === false
    ? "the tablet's speakers: AUX disconnected" : (a.port_name || "an output OmaCar cannot name");
  return `Sound goes to ${where}, at ${vol}${a.managed ? ", held there" : ""}.`;
}

export function openDrowsySheet() {
  const host = document.getElementById("modal-host");
  const offs = [];
  const close = () => {
    drowsy.preview = false;
    for (const off of offs) off();
    host.hidden = true;
    clear(host);
    host.onclick = null;
  };
  const row = (label, note, value, onclick, disabled) =>
    h("button.sheet-row", { type: "button", onclick, disabled: !!disabled },
      h("span.sheet-l", h("span.sheet-lab", label), note ? h("span.sheet-note", note) : null),
      h("span.sheet-v", value));
  const rows = h("div.sheet-rows");
  const status = h("div.dz-status");
  const measures = h("div.dz-measures");
  const audioNote = h("div.dz-aux");

  async function save(change) {
    try { await postJSON("/api/drowsy", change); await drowsy.reload(); redraw(); }
    catch (e) { toast("Could not save: " + e.message, "bad"); }
  }

  function redraw() {
    const c = drowsy.state.cfg || {};
    const sounds = c.sounds || [];
    clear(rows);
    rows.append(
      row("Drowsy mode", "Watches your eyes above 30 mph and wakes you gently", c.enabled ? "On" : "Off",
        () => save({ enabled: !c.enabled })),
      row("Sensitivity", "Sensitive lowers every threshold by 20%", c.sensitivity === "sensitive" ? "Sensitive" : "Standard",
        () => save({ sensitivity: c.sensitivity === "sensitive" ? "standard" : "sensitive" })),
      row("The name the voice uses", "Only a name the voice was recorded with", c.name ? c.name : "No name",
        () => save({ name: c.name ? "" : "James" })),
      ...["bark", "voice", "alarm"].map((s) => row(SOUND_LABEL[s], "In the Level 2 rotation", sounds.includes(s) ? "On" : "Off", () => {
        const next = sounds.includes(s) ? sounds.filter((x) => x !== s) : [...sounds, s];
        if (!next.length) { toast("At least one sound has to stay in the rotation."); return; }
        save({ sounds: next });
      })),
      row("Test the alerts", drowsy.canTest() ? "Level 1, 2 and 3 in turn, over twenty seconds" : "Only while parked",
        "Play", () => { if (!drowsy.test()) toast("Only while parked."); }, !drowsy.canTest()));
  }

  const sheet = h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Drowsy mode" },
    h("div.sheet-head", h("div.title", "Drowsy mode"), h("button.btn.right", { type: "button", onclick: close }, "Done")),
    h("div.dz-preview", drowsy.canvas, h("div", status, measures)),
    rows, audioNote, h("p.dz-rest", REST), h("p.dz-rest", AUX));
  clear(host);
  host.appendChild(sheet);
  host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  drowsy.preview = true;
  offs.push(drowsy.on((st) => {
    status.textContent = st.chip + (st.error ? ` · ${st.error}` : "");
    measures.textContent = measureLine(st.measures);
  }));
  offs.push(onAudio((a) => { audioNote.textContent = audioLine(a); }));
  redraw();
}
```

- [ ] **Step 6: Start it beside the app, and put it on Home**

Replace `share/js/alertness.js` with:

```js
// Started beside the app (share/app.html), like awake.js, because it is not
// part of any view.
//
// It holds the Surface's volume where `omacar audio on` asked for it
// (lib/audio.py), at start and every half minute: plugging the AUX cable in
// switches to a port that keeps a volume of its own.
//
// It also runs drowsy mode, whose chip goes into the top bar main.js builds,
// and whose cues become sound on the one output stage.
import { applyAudio } from "./audiostate.js";
import { drowsy, startDrowsy } from "./drowsyrun.js";
import { createAlertPlayer } from "./alertplayer.js";
import { mountDrowsyUI } from "./drowsyui.js";

applyAudio();
setInterval(applyAudio, 30000);

const player = createAlertPlayer({ name: () => (drowsy.state.cfg && drowsy.state.cfg.name) || "" });
drowsy.onCues = (cues, out) => { player.play(cues, out).catch((e) => console.warn("drowsy sound:", e)); };
mountDrowsyUI();
startDrowsy().catch((e) => console.warn("drowsy mode did not start:", e));
```

In `share/js/dashcard.js`:
- add to the imports:

```js
import { drowsy } from "./drowsyrun.js";
import { onAudio, auxLine } from "./audiostate.js";
```

- after `node.append(h("div.dc-stage", img, why), top);`, add:

```js
  // Home carries drowsy mode's chip, and says when the AUX cable is out.
  const dz = h("span.dc-drowsy");
  const aux = h("div.dc-aux", { hidden: true });
  top.appendChild(dz);
  node.appendChild(aux);
  const offDz = drowsy.on((st) => { dz.textContent = st.chip; dz.dataset.tone = st.chip === "Watching" ? "ok" : st.chip === "Can't see you" ? "warn" : ""; });
  const offAux = onAudio((a) => { const s = auxLine(a); aux.hidden = !s; aux.textContent = s; });
```

- change `destroy()` to:

```js
    destroy() { dead = true; clearInterval(timer); img.removeAttribute("src"); offDz(); offAux(); },
```

Append to `share/css/drowsy.css`:

```css
.dz-layer[hidden], .dz-banner[hidden], .dc-aux[hidden] { display: none; }

/* The chip in the top bar. */
.tb-drowsy { display: inline-flex; align-items: center; gap: 6px; min-height: 32px; padding: 0 10px; white-space: nowrap;
             border: 1px solid var(--edge); border-radius: 999px; background: none; color: var(--dim);
             font: inherit; font-size: .72rem; letter-spacing: .04em; cursor: pointer; }
.tb-drowsy-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--faint); }
.tb-drowsy[data-tone="ok"] { color: var(--ok); }
.tb-drowsy[data-tone="ok"] .tb-drowsy-dot { background: var(--ok); }
.tb-drowsy[data-tone="warn"] { color: var(--warn); }
.tb-drowsy[data-tone="warn"] .tb-drowsy-dot { background: var(--warn); }
@media (pointer: coarse) { .tb-drowsy { min-height: var(--tap); } }

/* The alerts. Level 1 is a card above the navigation; 2 and 3 fill the screen. */
.dz-layer { position: fixed; inset: 0; z-index: 300; display: grid; pointer-events: none; }
.dz-layer[data-level="1"] { place-items: end center; padding: 0 16px calc(var(--nav) + 16px); }
.dz-card { pointer-events: auto; width: 100%; max-width: 560px; display: grid; gap: 10px; padding: 18px 20px;
           border-radius: 18px; background: var(--panel);
           border: 1px solid color-mix(in srgb, var(--warn) 50%, transparent);
           box-shadow: 0 12px 40px rgba(0, 0, 0, .5); }
.dz-full { pointer-events: auto; display: grid; place-content: center; justify-items: center; gap: 22px;
           padding: 32px; text-align: center; background: color-mix(in srgb, var(--ground) 92%, var(--warn)); }
.dz-full.l3 { background: color-mix(in srgb, var(--ground) 86%, var(--rec)); }
.dz-t { font-size: 1.5rem; font-weight: 600; color: var(--ink); }
.dz-full .dz-t { font-size: clamp(2.4rem, 6vw, 4rem); }
.dz-s { color: var(--ink-2); }
.dz-rest { margin: 0; max-width: 40rem; color: var(--dim); font-size: .9rem; line-height: 1.45; }
.dz-awake { min-height: var(--tap-lg); padding: 0 24px; border: 0; border-radius: var(--r);
            background: var(--accent-fill); color: var(--on-accent); font: inherit; font-weight: 600; }
.dz-awake.big { min-width: min(80vw, 520px); min-height: 120px; border-radius: 28px; font-size: 2rem; }
.dz-banner { position: fixed; top: 64px; left: 50%; z-index: 250; transform: translateX(-50%);
             padding: 10px 18px; border-radius: 999px; background: var(--rec); color: #FFFFFF; font-weight: 600; }

/* The settings sheet's preview and lines. */
.dz-preview { display: grid; grid-template-columns: 160px 1fr; gap: 12px; align-items: center; }
.dz-preview canvas { width: 160px; height: 120px; border-radius: 10px; background: #000; }
.dz-status { font-weight: 600; }
.dz-measures, .dz-aux { font-size: .82rem; color: var(--dim); font-variant-numeric: tabular-nums; }

/* Home's Dashcams card: the chip, and the AUX line along the bottom. */
.dc-drowsy { padding: 3px 8px; border-radius: 999px; background: rgba(0, 0, 0, .55); font-size: .7rem; }
.dc-drowsy[data-tone="ok"] { color: var(--ok); }
.dc-drowsy[data-tone="warn"] { color: var(--warn); }
.dc-aux { position: absolute; right: 0; bottom: 0; left: 0; padding: 8px 14px;
          background: rgba(0, 0, 0, .7); color: var(--warn); font-size: .8rem; }
```

- [ ] **Step 7: The one hunk in main.js: Settings → Drowsy mode**

In `share/js/main.js`, in `openSettings()`, just before the line `rows.appendChild(row("Learn mode", "Explains the terms in place, and hides nothing",`, add:

```js
    // ---- redesign/cameras: drowsy mode's own sheet (share/js/drowsyui.js) ----
    rows.appendChild(row("Drowsy mode", "Watches your eyes above 30 mph and wakes you gently",
      "Open", () => { close(); import("./drowsyui.js").then((m) => m.openDrowsySheet()); }));
    // ---- end redesign/cameras -------------------------------------------------
```

This is the only line this branch adds to main.js. It writes no `location.hash`, so the guard's count of hash writers stays at one.

- [ ] **Step 8: Run everything, and look at it**

Run `BOXTEST`. Expected:
- `js_test.py` is green.
- `app_test.py` boots with drowsy mode running: nothing threw, and no failure card. The box has no recorder running, so no stream opens.
- The guards pass: "every mount the registry names…" and "nothing but a tap writes the hash".

Then take screenshots with `tools/camshot.py` (Task 3), without the recorder. `?dz=N` draws a level's card with no sound:

```bash
ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/cameras && S=/tmp/omacar-shot && rm -rf $S && mkdir -p -m 700 $S/run && export XDG_RUNTIME_DIR=$S/run && python3 tools/camshot.py $S/out home=still=1#home@1368,968 "dz1=still=1&dz=1#home@1368,968" "dz2=still=1&dz=2#home@1368,968" "dz3=still=1&dz=3#home@1368,968"; ls $S/out'
scp 'jmyers@omarchy:/tmp/omacar-shot/out/*.png' "$SCRATCH/"
ssh jmyers@omarchy 'rm -rf /tmp/omacar-shot'
```

Check:
- the chip reads `Paused · parked` in the top bar and on the Dashcams card;
- Level 1 is a card above the navigation, with "I'm awake";
- Level 2 fills the screen with a large "I'm awake";
- Level 3 says "Pull over now" and "Stop at the next safe place";
- every card carries the rest line.

Open Settings by hand in a headed window if you can, to check the Drowsy mode row opens the sheet.

- [ ] **Step 9: Commit**

```bash
git add share/js/alertplayer.js share/js/drowsyui.js share/js/alertness.js share/js/dashcard.js share/css/drowsy.css share/js/main.js test/js/alertplayer.test.js
git commit -m "Drowsy mode wakes the driver in rising steps, shows its state in the top bar and on Home, and has its own settings" -m "Level 1 is a chime, the voice and the radio rising 6 dB; Level 2 ducks the music and rotates a bark, the voice and a two-tone alarm up over a second and a half; Level 3 holds a full-scale alarm and says pull over. Every change is a ramp. Settings -> Drowsy mode is the one line this branch adds to main.js; the chip goes into the top bar from outside it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: End to end on the box, then the tablet, then a draft PR

**Files:**
- Create: `tools/cams_e2e.py`
- Modify: `doc/cameras.md` (append the section measured on the tablet)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the end-to-end check**

`tools/cams_e2e.py`:

```python
#!/usr/bin/env python3
"""The cameras, end to end, on a machine with one real camera: the box's C920
as the cabin, and `sim` test pictures for front and rear.

It starts the recorder and the server on scratch folders, then checks:
- recording: three roles, the real one not simulated;
- the live pictures;
- a finished one-minute clip per role that starts on a keyframe;
- playback with Range;
- Mark event, and hard braking from a scripted live.json;
- the locks and events both leave behind.
Then it takes screenshots of the Cameras tab and Home.

Takes about three minutes. Not in test/all.sh, because it needs a camera.

    python3 tools/cams_e2e.py OUT_DIR
"""

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "test"))
import camstore  # noqa: E402
from app_test import free_port, python_for_server  # noqa: E402

ROLES = ("front", "rear", "cabin")
fails = 0


def check(msg, cond):
    global fails
    if not cond:
        fails += 1
    print(f"    {'ok  ' if cond else 'FAIL'}  {msg}")


def main(argv):
    out = argv[1] if len(argv) > 1 else "/tmp/omacar-e2e"
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="omacar-e2e-")
    env = dict(os.environ, OMACAR_VIDEOS=os.path.join(scratch, "videos"),
               XDG_RUNTIME_DIR=os.path.join(scratch, "run"),
               XDG_STATE_HOME=os.path.join(scratch, "state"),
               XDG_CONFIG_HOME=os.path.join(scratch, "config"))
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    live = os.path.join(env["XDG_STATE_HOME"], "omacar", "live.json")
    os.makedirs(os.path.dirname(live))

    def say(kph):
        tmp = live + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"connected": True, "t": time.time(), "values": {"SPEED": kph}}, f)
        os.replace(tmp, live)

    say(0.0)
    log = open(os.path.join(out, "recorder.log"), "w")
    rec = subprocess.Popen([sys.executable, os.path.join(ROOT, "lib", "cams.py"), "sim"],
                           env=env, stdout=log, stderr=subprocess.STDOUT)
    port = free_port()
    srv = subprocess.Popen([python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port),
                            os.path.join(ROOT, "share")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def probe(path, *q):
        return subprocess.run(["ffprobe", "-v", "error", *q, path], capture_output=True, text=True).stdout.strip()

    try:
        for _ in range(80):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        print("\n  recording")
        ov = None
        for _ in range(40):
            try:
                ov = json.loads(req("GET", "/api/cams")[1])
            except (OSError, ValueError):
                ov = None
            if ov and ov["running"] and all(ov["roles"][r]["live"] for r in ROLES):
                break
            time.sleep(1)
        check("the recorder is up, with three live roles",
              bool(ov and ov["running"] and all(ov["roles"][r]["live"] for r in ROLES)))
        if not ov:
            return 1
        cab = ov["roles"]["cabin"]
        check(f"the cabin is the C920, not a test picture ({cab['device']}, {cab['mode']})",
              cab["sim"] is False and "C920" in (cab["device"] or ""))
        check("front and rear are simulated, and say so",
              ov["roles"]["front"]["sim"] and ov["roles"]["rear"]["sim"] and ov["sim"])
        for r in ROLES:
            st, body = req("GET", f"/api/cams/{r}/live?frames=3")
            check(f"{r}: three live JPEGs", st == 200 and body.count(b"\xff\xd8") >= 3)

        print("\n  events")
        for _ in range(10):
            say(100.0)
            time.sleep(0.2)
        say(90.0)
        time.sleep(0.25)
        say(80.0)
        time.sleep(3)
        say(0.0)
        evs = json.loads(req("GET", "/api/cams/clips")[1])["events"]
        check("hard braking was caught from the car's speed", any(e["kind"] == "hard-braking" for e in evs))
        st, body = req("POST", "/api/cams/mark", body="{}")
        check("Mark event answered", st == 200 and json.loads(body)["kind"] == "marked")

        print("\n  waiting 80 s for a whole minute of every camera and the locks to settle")
        time.sleep(80)
        doc = json.loads(req("GET", "/api/cams/clips")[1])
        for r in ROLES:
            mine = sorted((c for c in doc["clips"] if c["role"] == r), key=lambda c: c["start"])
            check(f"{r}: at least two clips", len(mine) >= 2)
            if not mine:
                continue
            path = camstore.clip_path(r, mine[0]["file"], root=env["OMACAR_VIDEOS"])
            dur = float(probe(path, "-show_entries", "format=duration", "-of", "csv=p=0") or 0)
            key = probe(path, "-select_streams", "v", "-read_intervals", "%+#1",
                        "-show_entries", "frame=key_frame", "-of", "csv=p=0").startswith("1")
            check(f"{r}: the first clip is a whole minute ({dur:.1f} s) and starts on a keyframe",
                  abs(dur - 60) <= 1.5 and key)
        cabin = sorted((c for c in doc["clips"] if c["role"] == "cabin"), key=lambda c: c["start"])[0]
        st, body = req("GET", f"/api/cams/clip/cabin/{cabin['file']}", headers={"Range": "bytes=0-1023"})
        check("playback can seek: a Range request is a 206", st == 206 and len(body) == 1024)
        by_kind = {e["kind"]: e for e in doc["events"]}
        for k in ("hard-braking", "marked"):
            e = by_kind.get(k, {})
            check(f"{k}: locked, on every camera",
                  e.get("state") == "locked" and {f.split("/")[0] for f in e.get("files", [])} == set(ROLES))
        # A clip two events overlap lives in the first event's folder and is
        # listed by both, so the check is that every listed clip resolves
        # inside a locked folder, not that each event has a folder of its own.
        paths = [camstore.clip_path(*f.split("/", 1), root=env["OMACAR_VIDEOS"])
                 for e in by_kind.values() for f in e.get("files", [])]
        check("and every locked clip is in a locked folder, out of the loop's reach",
              bool(paths) and all(p and f"{os.sep}locked{os.sep}" in p for p in paths))

        print("\n  on screen")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import camshot
        os.environ.update(env)
        pngs, doms = camshot.run(out, [("cameras-landscape", "still=1#cameras", "1368,968"),
                                       ("cameras-portrait", "still=1#cameras", "912,1424"),
                                       ("home-landscape", "still=1#home", "1368,968")],
                                 doms=["still=1#cameras"])
        for name, png in pngs.items():
            check(f"screenshot {name}", bool(png))
        dom = doms["still=1#cameras"]
        check("three feeds, all recording", dom.count('data-rec="1"') == 3)
        check("the badge says SIMULATED", 'cam-badge warn">SIMULATED</span>' in dom)
        check("the timeline shows both events", dom.count('class="cam-marker"') >= 2)
    finally:
        srv.terminate()
        rec.send_signal(signal.SIGINT)
        try:
            rec.wait(timeout=20)
        except subprocess.TimeoutExpired:
            rec.kill()
        log.close()
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'every check held' if not fails else str(fails) + ' failed'}; screenshots in {out}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 2: Run the whole suite, then the end-to-end check on the box**

Run `BOXTEST`. Expected: every suite green, or failing only where it already failed at baseline (Task 1, Step 1).

Then:

```bash
ssh jmyers@omarchy 'systemctl --user is-active omacar-cams.service; cd ~/Projects/.omacar-test/cameras && python3 tools/cams_e2e.py /tmp/omacar-e2e'
scp 'jmyers@omarchy:/tmp/omacar-e2e/*.png' "$SCRATCH/"
```

Expected:
- `inactive` or `unknown`. If the unit is active on the box, stop it first with `systemctl --user stop omacar-cams`: two recorders cannot share the C920.
- Every check `ok`, and three screenshots.

Compare `cameras-landscape.png` and `cameras-portrait.png` with `/Users/jmyers/omgarchy/omacar/doc/design/mockups/6.webp`, and `home-landscape.png` with `3.webp`. Fix what differs in `cameras.css` and rerun.

Then rerun the face tracker check from Task 10, Step 6. Expected: `ok`.

- [ ] **Step 3: Check the merge with the foundation branch**

```bash
cd /Users/jmyers/omgarchy/omacar-cameras
git merge-tree --write-tree --name-only redesign/foundation redesign/cameras
```

Expected: a tree id alone (no conflicts), or conflicts only in `test/guards_test.py`. Record the output for the PR body. Any other conflicting file means a foundation task edited a line this branch changed: resolve it now, on this branch, rather than leave it for the merge.

- [ ] **Step 4: Commit**

```bash
git add tools/cams_e2e.py
git commit -m "One command proves the cameras end to end on the box: recording, live, playback and locks" -m "The C920 records as the cabin and test pictures stand in for front and rear. It checks every role, whole one-minute clips that start on a keyframe, a Range request, Mark event, and hard braking from a scripted speed, and it takes the screenshots to set beside mockup 6.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Push, and open a draft PR**

```bash
git push -u origin redesign/cameras
git ls-remote --heads origin redesign/foundation
```

If `redesign/foundation` is on origin, open the PR against it. If not, open it against `omacar-launch`, and make the body's first line "Stacks on redesign/foundation; merge that first."

```bash
gh pr create --draft --base redesign/foundation --head redesign/cameras \
  --title "Cameras and drowsy mode" --body-file "$SCRATCH/pr-body.md"
```

`$SCRATCH/pr-body.md` must contain:
- a line per task's commit;
- the merge-tree result from Step 3;
- any suite that already failed at baseline;
- the end-to-end output and the three screenshots, attached through the PR's web UI;
- the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Then run `get_status` from the ccd_pr tools, and bind the PR if it is not reported.

- [ ] **Step 6: Ask which build the demo drives**

`redesign/foundation` may have moved on (Tasks 8–12). Ask the controller whether Wednesday's build should include it. If yes:
- run `git merge redesign/foundation` on this branch, and resolve `test/guards_test.py` by keeping both sides;
- run `BOXTEST`;
- push.

If no, the demo drives `redesign/cameras` as it stands.

- [ ] **Step 7: Tuesday evening, on the tablet, with the owner**

These run from the Mac through the box: `ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "…"'`. `T` below means `~/Projects/.omacar-wt/cameras` on the tablet.

1. **Put the branch on the tablet** without touching the kiosk's checkout, bring the private pictures and voice clips across, and run the suite there:

```bash
ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "cd ~/Projects/omacar && git fetch origin && git worktree add ~/Projects/.omacar-wt/cameras origin/redesign/cameras"'
ssh jmyers@omarchy 'rsync -a ~/Projects/omacar/share/assets/private/ omacar:Projects/.omacar-wt/cameras/share/assets/private/'
ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "cd ~/Projects/.omacar-wt/cameras && test/all.sh; python3 lib/assets.py status"'
```

Expected: the suite as on the box, and `ok` for every voice clip.

2. **Install the recorder's unit, pointed at the worktree:**

```bash
ssh jmyers@omarchy 'ssh -o BatchMode=yes omacar "T=\$HOME/Projects/.omacar-wt/cameras; sed \"s|__ROOT__|\$T|g\" \$T/share/systemd/omacar-cams.service > ~/.config/systemd/user/omacar-cams.service && systemctl --user daemon-reload"'
```

3. **With the owner, plug in the cameras.** The DJI Osmo Action 4 goes on the front, the Insta360 Ace Pro on the rear, and the C920 on the cabin, all through the powered USB 3 hub. Then run `ls -l /dev/v4l/by-id`, `v4l2-ctl -d <each video-index0> --list-formats-ext > ~/cams-<role>.txt`, and `python3 T/lib/cams.py status`. If a camera's name does not match its default pattern, write `~/.config/omarchy/omacar-cameras.json` with `{"patterns": {"<role>": "<a word from its by-id name>"}}` and check again.

4. **Record.** `python3 T/lib/cams.py on`, wait 70 s, then `python3 T/lib/cams.py status`.
   - For ten minutes, `python3 T/lib/cams.py status` once a minute. Each role's fps must stay at or above 90% of its mode's rate. This is open question 2: one hub, three cameras, no dropped frames. An error line such as `VIDIOC_STREAMON: No space left on device` means the bus is full: move the rear camera to the tablet's other USB port, run the ten minutes again, and record which arrangement held.
   - CPU: `top -b -n 3 -d 5 | grep -E "ffmpeg|python"`.
   - Ask the owner how long the DJI and the Insta360 run on USB power, and how hot they get. This is open question 1.

5. **Sound.**
   - Run `python3 T/lib/audio.py on`, then `status`. Expected: output `AUX (headphone jack)`, volume 100% (held at 100%).
   - Unplug the jack and run `status` again. Expected: `the speakers: AUX disconnected`. Plug it back in.

6. **The demo build, in the kiosk** (these commands run on the tablet). Record where `~/.local/bin/omacar` points (`readlink ~/.local/bin/omacar`) so it can be put back. Then:

```bash
systemctl --user stop omacar-kiosk; ~/.local/bin/omacar server stop
ln -sfn ~/Projects/.omacar-wt/cameras/bin/omacar ~/.local/bin/omacar
systemctl --user start omacar-kiosk
```

   If `readlink` printed nothing, `~/.local/bin/omacar` is a file rather than a link: copy it to `~/.local/bin/omacar.before-cameras` before the `ln`, and put that copy back after the drive instead.

   Press Begin: the owner hears the chime through the car, with the radio on AUX.

7. **Drowsy mode, parked, with the owner in the driver's seat.**
   - Open Settings → Drowsy mode. The preview shows the cabin picture and a face.
   - Have the owner look straight ahead, then nod down. The measures line's pitch must fall (more negative) on the nod. If it rises instead, set `PITCH_SIGN = -1` in `share/js/drowsy.js` on the Mac, and commit ("Chin-down reads negative on the tablet's camera, as the nod detector expects"). Then push, and `git -C ~/Projects/.omacar-wt/cameras fetch && git -C ~/Projects/.omacar-wt/cameras checkout --detach origin/redesign/cameras` on the tablet.
   - Run "Test the alerts" with the radio playing through the car. You should hear the chime and the voice, the radio duck and the bark rise, then the alarm rise to full and "Pull over now", and then the fade. Set the car's knob so the radio is comfortable. The alerts must then be clearly louder, and none of them startling.

8. **Write down what was measured.** Append to `doc/cameras.md` on the Mac:

```markdown
## What the tablet records, measured 2026-09-29

The Wednesday build records USB cameras on the tablet itself, through
`omacar-cams` (lib/cams.py; doc/design/2026-09-28-cameras-drowsy.md). The box
and PoE cameras above remain the later plan; this section is what the tablet
did with three USB cameras on one powered USB 3 hub.

| Role | Camera (by-id name) | Mode chosen | Real fps | CPU (ffmpeg) | Notes |
|---|---|---|---|---|---|
| front | … | … | … | … | … |
| rear | … | … | … | … | … |
| cabin | … | … | … | … | … |

- Ten minutes with all three: …
- USB power and heat (DJI, Insta360): …
- Audio: AUX at 100%, held by `omacar audio on`; unplugged reads "AUX disconnected".
- Drowsy mode: the nod sign …; Test the alerts, heard through the car: ….

For the Los Banos session: the per-second measures are in
`~/.local/state/omacar/drowsy/2026-09-30.jsonl`, events are in the records
book as `kind=drowsy`, and the cabin clips are in `~/Videos/OmaCar/cabin/`.
Thresholds are tuned in `~/.config/omarchy/omacar-drowsy.json`.
```

   Fill every `…` with what steps 3–7 measured: the by-id names, `cams.py status` modes and fps, the `top` figures, and the owner's answers. Then commit ("The tablet's cameras, measured: modes, frame rates and CPU with all three on one hub"), and push.

9. **Leave it running.** `omacar cams on` has already enabled the unit, so the recorder starts at login. The kiosk now serves the worktree. Tell the owner the one line that puts the kiosk back after the drive: `ln -sfn <the readlink from step 6> ~/.local/bin/omacar && systemctl --user restart omacar-kiosk`.

---

## Self-review

**1. Spec coverage.** Every spec requirement, and the task that builds it:

| Spec | Task |
|---|---|
| Roles by `/dev/v4l/by-id` patterns, config override, infrared ELP drops in | 1 |
| Modes from `v4l2-ctl`, best at or under 1080p30, MJPEG > H.264 > YUYV; cabin low resolution | 1 |
| One ffmpeg per camera, decoded once, split; `h264_vaapi`; 6/6/1 Mbit/s; passthrough; keyframes at boundaries; one-minute fMP4 at the named path | 1 |
| Live picture 640 px, 10 fps, JPEG, atomic, `$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg` | 1 |
| Loop budget 40 GB, oldest unlocked first, locked moved and never deleted | 1 |
| Hard braking (16 km/h in 1 s) locks −30 s…+30 s on every camera; events.json | 1 |
| The six server routes, Range support | 2 (`live` and `clip` in serve.py) |
| `omacar-cams.service` (Restart=always, no start limit, Nice=10); `omacar cams status\|on\|off\|sim`; SIMULATED | 1, 3, 4 |
| Cameras tab: main and two small feeds with role, resolution, REC, clock; timeline; playback ±10 s; Save clip, Mark event; Mute disabled; selection; storage, loop, parking watch; the badge | 3 |
| Home's Dashcams card: live front, REC, and why not | 4 |
| MediaPipe 1.0.1 Face Landmarker, VIDEO mode, blendshapes and matrix, self-hosted | 9, 10 |
| Page draws the cabin picture to a canvas at 10–15 fps | 10 |
| Measures: closure, baseline, closed, duration, PERCLOS, yawn, nod, face lost | 6 |
| Camera-free signals; Level 1 only | 7, 10 |
| Active only above 30 mph, connected and moving | 7, 10 |
| The ladder, thresholds file, sensitivity −20% | 6, 7, 10 |
| Release, fade 3 s, Level 3 banner, `kind=drowsy` records | 7, 10, 11 |
| One output stage; music −12 dBFS; 12 dB headroom | 5 |
| Volume pinned to 100% (wpctl), `GET /api/audio`, applied at start; AUX disconnected on Home | 5, 11 |
| Radio stays on AUX: Begin and settings say so; Begin chimes | 8, 11 |
| Ramps via `linearRampToValueAtTime`; `rampPlan(level, from, to)`; ≤ 3 dB per 100 ms | 8 |
| Chime and alarm synthesized; bark synthesized; Piper voice, private, in the manifest | 8, 9 |
| Status chip on Home and the top bar; Settings → Drowsy mode (on/off, sensitivity, name, sounds, test while parked); the rest copy | 11 |
| Tests: JS units, Python units, the box end to end, the tablet on Tuesday | 1–12 |
| Measured modes in `doc/cameras.md` | 12 |

Not built by this plan, by the spec's own design:
- Wednesday's drive and the Los Banos tuning session. The plan gives them the measures log and the thresholds file.
- Vehicle and report views of drowsy events ("later").
- Parking watch ("coming later").
- Better alert sounds ("another day").

**2. Placeholder scan.** No step says TBD or "handle edge cases", and none says "similar to Task N". Two places deliberately take values that only exist at execution time:
- Task 9, the no-SIMD file sizes, printed by `wc -c` in the same step;
- Task 12, Step 7, the tablet's measured modes and the owner's answers. The table's shape and the commands that produce each value are given.

**3. Type and name consistency.**
- **The ladder's names.** The trigger names (`perclos`, `closed`, `yawns`, `nods`, `since-stop`, `night`, `repeat-l2`) match between `ladder.js`, its tests and `drowsyui.js`'s `TRIGGER`. The cue kinds match between `ladder.js`, `alertplayer.js` and `drowsyrun.js`'s `test()`.
- **Voice names.** `voice-<clip>[-james]` is used by the manifest (Task 9), `vendor_test.py` and `alertplayer.js`.
- **Recorder names.** `cams.BOUNDARY` is `omacarframe` in `cams.py`, both HTTP tests and `mjpeg.test.js`. `camstore.clip_path(role, name, root=None)` has that signature in every caller.
- **The measures snapshot.** Its keys (`face`, `calibrated`, `closed`, `closedFor`, `perclos`, `yawns`, `nods`, `faceLost`) are the ones `ladder.js`, `chipOf` and `measureLine` read.
- **The config keys.** `nod.below_deg` and `level1.count_window_secs` are named the same in `drowsy.json`, `drowsy.js` and both tests.

**4. Checked before it was committed (2026-09-28).** This plan's new files and hunks were built, from its own text, into a scratch copy of the tree at 5f9356f, and its suites run on the box:
- `cams_test`, `camserve_test`, `audio_test`, `drowsy_test` and `guards_test` passed.
- `app_test` booted with drowsy mode loaded and nothing threw.
- `js_test` passed 149 and failed 0.
- The real `h264_vaapi` command gave 2.0 s segments, each starting on a keyframe, and 70 live frames in 7 s.
- `cams_e2e.py` ran with all three roles simulated (the C920 held out on purpose). It found whole 60.0 s first clips, hard braking, both locks, a 206, and the screenshots. That run is what corrected its lock check and led to `tools/camshot.py`.

Not run: anything with the real camera or the car. That is Task 10's check, Task 12's end-to-end run, and Tuesday evening.
