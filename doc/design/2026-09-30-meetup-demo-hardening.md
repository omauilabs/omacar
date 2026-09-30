# The meetup demo: hardening toward the end state

The meetup of 2026-09-30 was called off on the morning of the day, so there's no freeze. This round takes the built demo (`demo/meetup` at e3f5e49) to the spec's end state:
- every screen works by touch;
- the tour runs three times in a row on the tablet without a fault;
- the silo is proven;
- a backup video is on the tablet and the owner's phone.

Its sources are the final whole-branch review's "should fix" list, the deferred minors in the SDD ledger, and the owner's first run on the tablet at 07:46.

## Box-side work (now, in parallel)

**A. The demo keeps itself up** (bin/omacar, lib/demoguard.py)
1. **A watchdog.** Spec §6 says a watchdog restarts the demo server; today nothing does.
   - The guard, which already runs every 2 s, also checks that the demo server answers `/.mark` and that the world and camera-feed pids are alive.
   - When one is gone, it starts only that part again, with the same environment and command `demo on` uses.
   - Backoff is 2, 4, 8 and 16 s, then at most once a minute. Every restart is logged to `demo-guard.log`.
   - It never restarts anything while the real car is moving, and never after ACTIVE is gone.
2. **`demo off` is quiet.** Before stopping the window, `demo off` POSTs the `quiet` cue (see B.2) and waits 2.5 s, so the music fades rather than cuts. If the server doesn't answer, it goes on at once. The guard's own `demo off` (the car moving, or ACTIVE gone) skips the fade: the window comes down at once. (The 1.2 s first written here was too short: the page starts fading 0.3–0.45 s after the POST and pauses 1.4–1.7 s after it.)
3. **Tests** (demo_silo_test):
   - killing the server, the world or the feed brings it back within 10 s;
   - a car moving meanwhile means nothing is restarted and the demo comes down;
   - `demo off` sends `quiet` first.

**B. The sound is right** (share/js/audiobus.js, demo/js/voice.js, demo/js/radio.js, lib/demoworld.py, lib/serve.py)
1. **Voice through the stage.**
   - audiobus.js gains `voiceIn()`: a gain node into the limiter at unity, beside `musicIn()` and `alertIn()`.
   - It's inert to the live app, which never calls it.
   - voice.js plays its lines into `voiceIn()` instead of straight to the output, so the limiter holds the summed peak. It can't hold −1 dBFS as the stage is set (threshold −1, ratio 20, and Chromium's makeup gain of about +0.57 dB), so the worst case reads −0.19 dBFS and it never clips below about +7.6 dBFS in. The live stage is unchanged; that's for the owner.
   - Keep `VOICE_DB = -7`.
2. **A `quiet` cue.**
   - `POST /api/demo/cue {"cue": "quiet"}` is accepted by the demo server.
   - The world then sets `demo.quiet_at` (epoch).
   - The page, on seeing a fresh `quiet_at`, fades the music bus to silence (1.2 s from the music's level, 0.8 s from a ducked bus, keeping 3 dB per 100 ms) and pauses the radio.
   - The tour's own reset fades the same way before its restart cue, so music never cuts in one step.
3. **Tests:**
   - the limiter holds a voice line summed with music and a Level 2 alert below 0 dBFS, at least 1 dB under the unlimited sum (the OfflineAudioContext pattern the stage tests use);
   - `quiet` fades with no step above 3 dB per 100 ms;
   - the reset fades before restarting.

**C. The tour and screens behave under a presenter** (demo/js/tour.js, menu.js, views/work.js, lib/democheck.py)
1. **The drive and the tour stay together.** On Resume, if the drive's loop time is within 180 s of its closing stop, or the tour was paused more than 5 minutes, the tour sends `restart` and re-enters the current step from its start.
2. **The menu during the reset:** opening the menu while the tour is resetting holds the tour's start until the menu closes.
3. **Resume inside CarPlay or Android Auto:** Resume returns to the step's own projection screen (`openScreen`).
4. **Portrait captions:** one line, with the text shrinking to fit (down to 16 px) and never wrapping.
5. **Work:** only Running sessions tick; Review and Needs-input sessions hold their checklist.
6. **democheck:**
   - a "stay awake" check (the live kiosk holds Omarchy's stay-awake indicator, `~/.local/state/omarchy/indicators/stay-awake`, or the kiosk's hold is active);
   - a "clips play" check (each clip is H.264, which `cams.py demo` already knows how to test).
7. **Tests** for each.

## Tablet-side work (when the tablet is online and parked, with the owner's word for sound)

1. **Performance:** the map's draw time, and the CPU and memory during a tour with the camera feed running, measured on the tablet.
2. **Three whole tours in a row** via `tools/demo_e2e.py --tablet`, headless and muted, with no visible demo window. Then the silo check.
3. **Footage:** the owner's clips or the approved stock, then `demo check` reads ready.
4. **The backup video:** `tools/demo_record.sh` on the tablet at the owner's desk, then copied to the owner's phone.

## The owner's decisions (asked, not assumed)

- The stock footage.
- The `omacar-demo` shortcut.
- Updating the tablet's live checkout to `demo/meetup`. This brings the tab-underline fix and the `card.py` fix into the live app, and makes the bar widget's Demo button and `omacar` on PATH the new demo.
- Opening the pull request once CI runs on the owner's runners (PR #6).
