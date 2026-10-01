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

## D. The demo with no footage (added 2026-09-30, afternoon)

The owner's cameras wait for parts, and the footage waits for them. The owner will give the go-ahead when it's in, and no stock footage is downloaded until then. Until then the demo must look finished without footage, and switch back by itself once clips are present.

1. **The tour** skips a step whose `needs` it can't meet. `tour.json`'s Cameras step gets `"needs": "clips"`, and the demo page learns whether clips exist from `GET /api/cams` (roles running), or a `demo.clips` flag the server adds. The tour's step numbers, keys and captions stay stable: a skipped step is jumped over, and its key does nothing, with a quiet toast "Cameras aren't connected in this demo".
2. **Home's Dashcams card and the Cameras tab** show a composed empty state when no role is running: a camera glyph, "Front, rear and cabin cameras", and the line "Recorded in one-minute clips, and a hard stop saves the clip". It's styled like the card's normal look, not as an error ("No front camera", "Off"). The demo overrides both through the door (`D.cards.dashcam`, `D.views.cameras`); the live app's own states are unchanged. When roles are running, the live card and view render as today.
3. **The drowsy moment** works with no cabin clip: the chip, both levels and "I'm awake", with no broken cabin tile.
4. **democheck:** missing clips become a note (`--`, "no footage yet: the tour skips Cameras") rather than a FAIL, so `check` can say ready. A clip that is present but won't play stays a FAIL.
5. **Tests** for each, plus an e2e run on the box with no clips: the tour passes with the Cameras step skipped, and no console errors.

**As built (D):**
- **The page's answer** is `demo/js/footage.js`: footage is a role that is recording, from `GET /api/cams`, with no server flag. One detector serves the tour, the card and the screen. It looks when the tour starts (with the reset, capped at 3 s), as each of the tour's steps opens (in the background), and when Home and Cameras mount. While they show the empty state they look again every 5 s, so footage that arrives later is picked up without a reload.
- **The tour** reads `"needs": "clips"` and `"missing"` in `tour.json`. Only a plain `false` skips a step: no answer yet enters it, because the empty state looks finished and a wrongly skipped step is gone. The decision is made as the step opens, so footage that arrives mid-tour is shown.
- **The card and the screen** are `demo/js/cards/dashcam.js` and `demo/js/views/cameras.js`, in `demo/css/nofootage.css`. With a camera recording they hand over to the live card and view (imported and called, not copied).
- **democheck:** no clip at all is a note. Some clips but not all stays a failure, because the tour would then show Cameras with the others empty. A clip that will not play stays `clips play`'s failure.
- **The drowsy moment** needed no change: its chip and both cards draw no picture and ask the cameras for nothing, and a test holds that.
- **The e2e:** `tools/demo_e2e.py --no-clips` expects steps 1-3 and 5-11, taps the Cameras tab, and checks the skipped key's toast. The headless recorder expects the same of a tour with no clips.
