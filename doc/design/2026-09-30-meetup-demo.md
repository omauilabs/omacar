# The meetup demo

OmaCar's first public showing is at the Omarchy meetup in San Francisco, Wednesday 2026-09-30, 6–8 pm. There's no car in the room. The tablet runs a demo world that looks and feels like the finished product. It works on the tablet's own screen or through an HDMI projector, with or without internet (the owner's phone hotspot).

This builds on `demo/2026-09-30` (3ad5c30), Wednesday's build, which is already on the tablet. The owner approved the approach and the three design sections in chat on the night of 2026-09-29, and added Omarchy Radio.

## Success

- Every screen in §4 works by touch. The tour (§6) runs start to finish unattended, three times in a row on the tablet, without a fault.
- Nothing the demo does can change the live app, its data, the car or the tablet's volume (§2), and a test proves each guarantee.
- A backup video of the tour is on the tablet and the owner's phone before he leaves Los Banos.

## 1. The owner's constraint

> "the demo mode should not in any way shape or form affect the live running app when in the car, or affect the real data. The demo must be siloed data"

Today's `omacar demo` does not meet this. It redirects only the state and config folders and serves the live `app.html`. From the demo window:
- the Cameras view reads the real `~/Videos/OmaCar` and the real recorder's run folder;
- `connect.resolve()` finds the real adapter;
- "Begin" (`POST /api/begin`) stops the real `omacar-drivelog.service`;
- `omacar demo cache` writes the real panel cache.

§2 closes each of these.

## 2. The silo: seven guarantees, each pinned by a test

1. **Own folders.** Everything the demo writes lives under `DEMO_ROOT` (`~/.local/state/omacar-demo`). The demo server runs with `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `XDG_RUNTIME_DIR`, `OMACAR_STATE` and `OMACAR_VIDEOS` all inside it. The demo browser's profile is `DEMO_ROOT/browser`, deliberately not under `~/.local/share/omacar/kiosk*`: the kiosk watchdog's prefix match would take that for the live kiosk.
2. **No hardware.** The demo server runs with `OMACAR_PORT` set to a path that does not exist, so `connect.resolve()` can never reach an adapter. The demo's camera feeds come from files (§3) and never open `/dev/video*`.
3. **An allowlist of routes.** `serve.py --demo` answers only the routes the demo page uses. Every other `/api/` route answers 403 "not in the demo". That closes:
   - `/api/begin`, which stops the real recorder;
   - `/api/learn`, `/api/clear`, `/api/reset` and `/api/write-*`;
   - `/api/phone*`, the voice assistant, and `/api/ai/*`, which runs the real `claude` CLI;
   - `/api/audio`, the volume pin;
   - daemon control.
4. **No live services touched.**
   - `omacar demo` never starts, stops or restarts `omacar-daemon`, `-drivelog`, `-watch`, `-kiosk`, `-cams`, `-dtclog` or the simulator's unit, and never runs `wpctl` or `pactl`.
   - It never writes `~/.local/state/omacar`, `~/.config/omarchy`, `~/Videos/OmaCar` or `~/.local/state/omarchy/liquid-glass-car.json`.
   - `omacar demo cache` is fixed to write the demo's own panel cache.
5. **The live app never loads demo code.**
   - Demo code lives in `demo/` at the repo root, outside `share/`. Only a server started with `--demo` serves it, under `/demo/`. The live server answers 404 there.
   - The live page's code changes by one inert seam only: `overrideView` in `main.js`, which the live page never calls.
   - The server and the camera recorder gain demo-only modes (`serve.py --demo`, `cams.py demo`). The live units never pass them, and a test checks their unit files.
6. **Volume.** The demo never changes the system volume or output. The owner sets loudness by hand before the show. `omacar demo check` warns if the live app's volume pin (`omacar audio on`) is set: the live page would then push the speakers to 100% every 30 s during the demo.
7. **Safety.**
   - `omacar demo on` refuses while the live app reports the car moving (fresh `live.json` with speed above 0).
   - The demo window closes itself if the live state starts reporting movement. It reads `live.json` read-only, every 2 s.
   - At the venue there's no adapter and so no fresh live data, which counts as parked.

**The silo test** runs `demo on`, the whole tour, then `demo off`, in a scratch `HOME` seeded with a fake live state. `PATH` shims record every call to `systemctl`, `wpctl` and `pactl`. The test asserts that every file outside `DEMO_ROOT` is byte-identical and that none of those commands ran.

## 3. Architecture

- **Commands.** `omacar demo on | off | check | tour | trash`, with `start` and `stop` kept as aliases.
  - `on` seeds the history if needed, then starts the demo world, the demo camera feed, the demo server and the demo window.
  - Every verb stays confirm-gated, as `guards_test.py` requires.
- **The demo world, `lib/demoworld.py run`.** It replaces `sim.py run` in the demo.
  - It is paced by elapsed real time, so it has none of the sim's fivefold drift.
  - It plays `demo/drive.json` and writes `live.json` at 5 Hz. The values are the sim's, plus:
    - hybrid: IMA assist and charge in kW, the pack's percentage, and auto-stop;
    - position: latitude, longitude and heading;
    - navigation: the next manoeuvre, its distance, the street and the ETA;
    - scene flags: parked, drowsy, hard braking.
  - It takes cues from `demo-cue.json`, which `POST /api/demo/cue` writes: park, drive, drowsy, hard-brake and restart.
  - History, trips and codes come from `sim.py seed`, as today.
- **The demo server.** `serve.py 7580 share --demo demo` serves:
  - `share/` as today;
  - `demo/` under `/demo/`;
  - the private demo media (songs, clips, map, voice) under `/demo-media/`, with HTTP Range, which static files lack;
  - the §2 allowlist.
- **The demo page, `/demo/demo.html`.** It is the live `app.html`'s shell plus `demo/boot.js`.
  - `boot.js` installs the demo's views through `overrideView`, and adds the DEMO label, the long-press menu, the tour and the cue keys.
  - Everything else is the live app's own code running on demo data: Home's cards, Vehicle, Cameras, the drowsy screens, the alert player and the audio stage. That is what makes it real.
- **The demo window.** It opens as `chromium --user-data-dir=DEMO_ROOT/browser --app=http://127.0.0.1:7580/demo/demo.html`, with the live kiosk's other flags, including `--kiosk` and `--autoplay-policy=no-user-gesture-required`.
  - Its path gives it a different Wayland app id from the live kiosk's.
  - Whether it opens over the fullscreen kiosk depends on Hyprland's settings, so the plan checks that on the tablet and doesn't assume it.
- **Cameras, `cams.py demo --from <clips>`.**
  - It copies the clips into `DEMO_ROOT/videos/<role>/`, named with times in the last hour, because the timeline shows only the last hour.
  - It decodes the same clips in a loop to `<role>.jpg` at 10 fps and writes `status.json`, so the Cameras view's own checks pass.
  - It never opens a device.

## 4. Screens

**One scripted drive** runs everything. It goes from Marina to San Francisco, with the destination shown as "Omarchy Meetup, San Francisco".
- The loop plays about 15 minutes of that trip:
  - it leaves from a public place in Marina, never the owner's home;
  - a few town turns and traffic lights;
  - then Highway 1 north along Monterey Bay;
  - then it fades back to the start.
- It includes one stop where the car pulls over. The demo keeps the live app's rules, so while the demo car moves, the same things lock as in the real car. The agent builds its layout at the stop, like mockup 7's "PARKED".

| Screen | In the demo |
|---|---|
| **Home** (mockup 3) | The CR-Z picture from the mockups, as a private asset. The speed dial and tiles follow the drive. Navigation shows the next turn, Dashcams shows footage, and there are Phone integration, Now Playing and Oma Agent cards. |
| **Navigation** | Drawn by the demo's own canvas renderer, with no map library, from OpenStreetMap data for the route. Roads are drawn by class, with the coastline and water, in the mockups' look: dark, a slate road network, a cyan route and an arrow for the car. The map is heading-up and follows the car, with a turn banner, the distance and the ETA. It is credited "© OpenStreetMap contributors", as the ODbL requires. Road cameras are the live app's tab: live Caltrans stills over the hotspot, or saved stills (seeded into the demo state, with their real age) without it. |
| **Cameras** (mockup 6) | Front, rear and cabin loops, the timeline, one flagged hard-braking event, and working playback. |
| **Vehicle** (mockup 5) | "All systems normal", live signals, no trouble codes, and a scripted **Scan vehicle**. |
| **Agent** (mockup 7) | The chat with the mockup's chips and a "Play Omarchy Radio" chip. Scripted replies stream in. "Build me a focused night-drive layout" gives the preview, and **Apply** really changes the demo's Home layout. The mic shows "Listening…", then fills in the next scripted question. Labelled "Illustrative agent responses". |
| **Work** (mockup 8) | Claude 01–03 and Codex sessions whose steps tick along. "Give me an update" is answered by voice. The driving view shows "Your agents are working". Labelled "CONCEPT · DEMO SESSIONS". |
| **CarPlay and Android Auto** | From the Phone integration card, a full-screen takeover in each one's style. Each has a home screen, Maps (the same route and renderer, restyled) and Now Playing, plus the button back to OmaCar. They are drawn fresh, using the names only, with no Apple or Google logo artwork. |
| **Omarchy Radio** | Ryan R. Hughes's seven songs, from the station's own files. |
| **Drowsy moment** | On cue, the cabin clip shows eyes closing and the chip changes. Level 1 and then Level 2 play through the live app's alert player, and "I'm awake" ends it. It plays at whatever volume the tablet is set to. |

**How Omarchy Radio plays**
- One player sits on the demo page and feeds the audio stage's music bus (`musicIn()`, −12 dBFS) in the demo window's own AudioContext.
- Home's Now Playing card, the Now Playing screen, and the Now Playing screens in CarPlay and Android Auto all show that one player, so a song carries on across screens.
- The stage's own ducking lowers it under drowsy alerts.
- The card is the station name set in type, with no invented artwork.
- The live app's `radio.js`, which plays the station's stream, is not touched.

**Voice.** The agent's and Work's spoken replies, and the drowsy Level 2 line, are short clips pre-rendered once with Piper on the box and kept private. Captions show the words.

**Labels.** "DEMO" in the top bar, as the mockups mark "CONCEPT · DEMO DATA".

## 5. Assets and downloads

Private files are never committed. They live under `share/assets/private/`, which is git-ignored, and reach the tablet through `omacar assets push`.

| Folder or file | What | State |
|---|---|---|
| `omarchy-radio/` | 7 songs and `playlist.json`, checked against github.com/omacom/radio.omarchy.org at 023a8f5c62ab | on the tablet and the box, 48 MB |
| `map/` | The route's OpenStreetMap data, compacted, plus the route and its turns | needs download |
| `clips/` | Front, rear and cabin loops: stock first, then the owner's footage | stock needs download |
| `voice/` | The pre-rendered voice lines | needs Piper |
| `crz-home.png` | The Home car picture, cut from the owner's mockups | to make |
| `crz-xray.png` | Vehicle's X-ray picture | on the box, not yet on the tablet |

**Downloads, each needing the owner's OK:**

| What | From | Size |
|---|---|---|
| OpenStreetMap roads, coast and water for the route | overpass-api.de | est. 10–25 MB; the download stops at 40 MB |
| The route and its turns | router.project-osrm.org | under 1 MB |
| `piper-tts` 1.8.0 and `onnxruntime` 1.30.0, into a private venv on the box (no sudo) | PyPI | 34 MB + 24 MB |
| The voice `en_US-hfc_female-medium.onnx` and its `.json` | huggingface.co/rhasspy/piper-voices | 63 MB. Its training data is CC BY-NC-SA 4.0: non-commercial use, which a community meetup is |
| Stock footage | Pexels (free licence) | listed with names and sizes before downloading |

## 6. Running it on stage

**Timeline**
1. Built and run on the box overnight, and on the tablet by morning.
2. A 10-minute rehearsal at the owner's Los Banos desk, with fixes by early afternoon.
3. **Freeze at 2 pm.** Anything not solid by then drops out of the tour, and its screen shows the live app's own placeholder. Nothing half-working goes on stage.
4. The backup video is recorded from the frozen build and put on the tablet and the owner's phone before he leaves Los Banos.

**At the venue**
- A 5-minute card in the owner's notes: power, brightness, and the sound output and level set by hand.
- `omacar demo check` prints "ready" or what's missing. It checks that:
  - the songs, clips, map, voice lines and saved stills are there and match their pins;
  - every clip is H.264, so the Cameras tab's player can play it;
  - the demo window opens;
  - the live volume pin is off;
  - the live kiosk is running underneath, as usual;
  - the screen will not sleep mid-demo: Omarchy's stay-awake is on, or the live kiosk that holds it is running.

**The tour**, about 6 minutes:
1. Home
2. Navigation
3. Cameras, with the hard-braking clip
4. The drowsy moment
5. The Vehicle scan
6. Agent: the night-drive layout, then Omarchy Radio
7. Work
8. CarPlay
9. Android Auto, then back to Home

It shows one short caption per step. Any touch pauses it, and Resume carries on.

**Cues**
- A long press on the OmaCar logo opens the menu: Tour, Resume, Drowsy, Hard braking, Park or Drive, Backup video, Exit demo.
- Type Cover keys: 1–9 jump to a step, D drowsy, B hard braking, P park or drive, Space pause or resume, Esc the menu.

**If something breaks**
- The server's watchdog restarts it, and the page reconnects where it was.
- A clip that fails shows its still.
- The backup video plays full-screen with `mpv` from the menu.

**Ending.** "Exit demo", or `omacar demo off`, closes the demo window, and the untouched live app is underneath.

## 7. Testing

- **The silo test**, as in §2.
- **The server:** each refused route answers 403 under `--demo`, and the live server answers 404 for `/demo/`.
- **The demo world:**
  - after N seconds of wall time, the drive is N seconds in;
  - its values stay within the car's real ranges;
  - each cue does what it says.
- **JS**, in `test/js/demo-*.test.js`:
  - the map's projection and the turn banner;
  - the tour's order and pause;
  - the agent's script matching;
  - one radio player shared by every screen;
  - the CarPlay and Android Auto screens mounting.

  Demo media stays outside `share/`, because the JS runner copies all of `share/` on every run.
- **The box, end to end.** `tools/demo_e2e.py` runs the tour headless at 1368×912, 912×1368 and 1920×1080 (the projector). It screenshots every step and requires a clean console.
- **The tablet:** three whole tours in a row with no fault, then the silo check, then the rehearsal.

## 8. Not in this

- Real CarPlay or Android Auto projection.
- A real agent, or real Work sessions.
- GPS, or real routing.
- Changes to the live app's radio stream.
- The camera session that was planned for the evening of 2026-09-29.

## 9. Shape of the plan

The tasks are cut so they touch different files, and after Task 1 they run in parallel worktrees.

1. **The silo and its plumbing** (first; the rest build on its interfaces):
   - the `bin/omacar` demo verbs;
   - `serve.py --demo` and its allowlist;
   - the `overrideView` seam;
   - the panel cache fix;
   - the silo test.
2. **The demo world:** `lib/demoworld.py`, plus the builder that turns the route into `demo/drive.json`.
3. **The map:** its data, the renderer and the Navigation view.
4. **Omarchy Radio:** the player, the Now Playing screen and Home's card.
5. **CarPlay and Android Auto.**
6. **Agent and Work:** the agent's script, the Work sessions and the voice lines.
7. **Cameras and the drowsy moment:** `cams.py demo` and the drowsy cue.
8. **Running the show:** the tour, the cues and menu, the DEMO label and `omacar demo check`.
9. **Finishing:** the box end-to-end run and the backup video.
