# The meetup demo: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a siloed demo world for Wednesday's Omarchy meetup (6–8 pm, 2026-09-30) that looks and feels like the finished OmaCar, as specified in `doc/design/2026-09-30-meetup-demo.md`.

**Architecture:**
- A demo server (`serve.py --demo`) serves the live app's own `share/`, plus the repo's `demo/` folder and the private demo media, and refuses every route that could reach the car or the live app.
- A demo world process (`lib/demoworld.py`) plays a scripted drive into the demo's own `live.json`.
- The demo page (`demo/demo.html`) is `app.html` with `demo/js/boot.js` loaded first. `boot.js` fills `globalThis.OMACAR_DEMO`, the single door `main.js` and `home.js` read. Everything else on screen is the live app's own code, running on demo data.

**Tech stack:** Python 3 stdlib (server, world, tools); ES modules with no build step (browser); Canvas 2D for maps; WebAudio through the live `audiobus.js`; ffmpeg for the demo camera feed; Piper, in a private venv on the box, for voice lines.

**Where this runs:** Mac worktrees (git, push), with tests on the Omarchy box (`ssh jmyers@omarchy`). The tablet is `ssh jmyers@omarchy 'ssh omacar …'`.

## Global constraints

Every task's requirements include these.

- **The owner's constraint, verbatim:** "the demo mode should not in any way shape or form affect the live running app when in the car, or affect the real data. The demo must be siloed data".
- **The silo rules (spec §2):**
  - demo code lives in `demo/`, never loaded by `share/app.html`;
  - the demo writes only under `~/.local/state/omacar-demo`;
  - it never opens the OBD adapter or `/dev/video*`;
  - it never starts, stops or restarts any `omacar-*` unit;
  - it never runs `wpctl` or `pactl`, and never changes the system volume.
- **Private files are never committed.** Songs, footage, map data, the route, voice clips, the logo and car pictures live under `share/assets/private/` (git-ignored). Tests use small committed fixtures in `test/fixtures/demo/`.
- **The private media layout on the box** (`~/Projects/omacar/share/assets/private/`, the master copy; `omacar assets push` copies it to the tablet):
  - `omarchy-radio/*.mp3` and `omarchy-radio/playlist.json` (present);
  - `omacar-logo.png` (present);
  - `map/raw/osrm-route.json`, `map/raw/osm-roads.json`, `map/raw/osm-water.json`, `map/raw/osm-freeways.json` (present);
  - `demo/drive.json` (Task 1 builds it);
  - `demo/map.json` (Task 3);
  - `demo/voice/*.wav` (Task 6);
  - `demo/clips/{front,rear,cabin,cabin-drowsy}.mp4` (Task 7 reads them; the controller supplies them);
  - `demo/crz-home.png` (Task 8).
- **No new downloads.** Everything approved is already on the box:
  - Piper is at `~/.local/share/omacar-demo-tools/piper/bin/piper`;
  - the voice is at `~/.local/share/omacar-demo-tools/voices/en_US-hfc_female-medium.onnx`;
  - no npm, no CDN, no map library.
- **No sound overnight.** The owner's child is asleep near the tablet. No test or check plays audio on the tablet or the box's speakers before 07:30. Headless Chromium runs with `--mute-audio`, and the tablet's screen stays off (DPMS) unless the owner asks.
- **The car:**
  - never `omacar write`, any clear, services 0x10, 0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x36, 0x37, 0x3E or 0x85, or `ATCSM0`;
  - never probe the tablet's `/dev/video0`–`63`.
- **The look:**
  - the mockups (`doc/design/mockups/3.webp`, `5`, `6`, `7`, `8`, git-ignored; also on the Mac at `/Users/jmyers/omgarchy/omacar/doc/design/mockups/`): dark, slate, cyan accent, rounded cards;
  - use the live app's CSS tokens (`share/css/*.css`, `share/js/tokens.js`) rather than new colours where one exists;
  - landscape 1368×912 and portrait 912×1368 (the tablet), plus 1920×1080 (a projector).
- **Copy:**
  - "Illustrative agent responses" under the Agent chat;
  - "CONCEPT · DEMO SESSIONS" on Work;
  - "DEMO" in the top bar;
  - "© OpenStreetMap contributors" on every map;
  - CarPlay and Android Auto use the names only, never Apple or Google logo artwork;
  - Omarchy Radio's songs are credited "Ryan R. Hughes", exactly as in the station's `playlist.json`.
- **Commits:**
  - one logical change each, in the repo's voice (read `git log -5`);
  - the trailer names the model you actually are: `Co-Authored-By: <your model name> <noreply@anthropic.com>`;
  - never push. The controller merges and pushes from the Mac.
- **Tests:**
  - Python suites are `test/<name>_test.py` (plain scripts with local `ok/bad` helpers and a nonzero exit on failure), each added to `test/all.sh`;
  - JS tests are `test/js/<name>.test.js`, whose default export is `[[name, fn], ...]`, using `eq`/`ok` from `./assert.js`;
  - demo modules are imported as `../demo/js/<file>.js` and live modules as `../js/<file>.js`;
  - a demo module imports live modules as `../../js/<file>.js` (from `demo/js/`) or `../../../js/<file>.js` (from `demo/js/views/`).
- **Running tests on the box.** From your Mac worktree:

      rsync -a --delete --exclude .git --exclude share/assets/private --exclude share/js/vendor/mediapipe ./ jmyers@omarchy:Projects/.omacar-test/meetup-tN/
      ssh jmyers@omarchy 'mkdir -p ~/Projects/.omacar-test/meetup-tN/share/js/vendor && cp -rn ~/Projects/.omacar-test/meetup/share/js/vendor/mediapipe ~/Projects/.omacar-test/meetup-tN/share/js/vendor/'
      ssh jmyers@omarchy 'cd ~/Projects/.omacar-test/meetup-tN && python3 test/<suite>_test.py && timeout 400 python3 test/js_test.py'

  Replace `tN` with your task number. Before reporting DONE, run `./test/all.sh` there and paste its last lines into your report.

## The contracts every task codes against

### `globalThis.OMACAR_DEMO` (the door, already in `share/js/main.js` and `share/js/views/home.js`)

    views:      { [viewId]: mount | { mount, fast } }   replaces a live view's mount; fast = /api/live at 4 Hz
    extraViews: [{ id, label, title, mount, fast }]     the demo's own routable screens (#carplay …)
    tabRoots:   { [tabId]: viewId }                     the screen a tab opens on
    cards:      { [cardId]: () => ({ node, paint(), destroy() }) }   dresses an existing Home card
    afterBar:   (vbarEl) => void                        after every paint of the top bar; must be idempotent
    onKey:      (KeyboardEvent) => boolean              true = the demo used the key

- A view's mount signature is `mount(root, { arg }) -> unmount | null`, as in `share/js/views/*.js`.
- Screens navigate with `location.hash = "#<viewId>"`, which `main.js` routes on `hashchange`.
- Only Task 8 writes `demo/js/boot.js`. Every other task exports a `register(D, deps)` function from its own module, which fills its part of the object. Task 8 calls them in order.

### The demo server's URL map (Task 2 builds it; everyone else uses it)

| URL | Serves |
|---|---|
| `/demo.html` | `demo/demo.html` from the repo (at the root, because the app fetches `data/*.json` relative to the page) |
| `/demo/<path>` | `demo/<path>` from the repo |
| `/demo-media/radio/<file>` | `share/assets/private/omarchy-radio/<file>`, with HTTP Range |
| `/demo-media/logo.png` | `share/assets/private/omacar-logo.png` |
| `/demo-media/<path>` | `share/assets/private/demo/<path>`, with HTTP Range |
| `POST /api/demo/cue` | body `{"cue": "park"\|"drive"\|"drowsy"\|"hard_brake"\|"restart"}` → writes `$OMACAR_STATE/demo-cue.json` as `{"cue": …, "at": <epoch float>}` and answers `{"ok": true}` |
| everything else | the live app's `share/` and `/api/*`, filtered by the §2 allowlist |

Every other server (`serve.py` without `--demo`) answers 404 for `/demo.html`, `/demo/*` and `/demo-media/*`.

### `live.json` in the demo (Task 1 writes it; the pages read `store.live`)

The same payload `lib/sim.py run` publishes (`connected`, `simulated: true`, `values`, `t`, `odometer_km`, `economy_lphk`, and the rest; reuse `sim.publish`), with the hybrid pack in `values.HYBRID_BATTERY_REMAINING` (percent). Home's Hybrid tile derives charging and assist from how that moves (`share/js/readings.js:157-180`). One added key:

    "demo": {
      "t": 123.4,                 seconds into the loop
      "loop_secs": 900,
      "lat": 36.70, "lon": -121.80,
      "heading": 12.5,            degrees, 0 = north, clockwise
      "street": "CA-1 N",
      "route_m": 5234.0,          metres from the route's start
      "remaining_m": 182000.0,
      "eta": 1759290000.0,        epoch seconds
      "next": { "type": "turn", "modifier": "right", "street": "Reservation Rd",
                "instruction": "Turn right onto Reservation Rd", "in_m": 420.0 } | null,
      "parked": false,
      "scene": "drive" | "parked" | "drowsy",
      "event": { "kind": "hard_brake", "at": 1759280000.0 } | null,
      "ima": { "state": "assist" | "charge" | "idle" | "stop", "kw": 6.2 }
    }

### `drive.json` (Task 1 builds it from `map/raw/osrm-route.json`; Tasks 1 and 3 read it)

    { "version": 1,
      "name": "Marina to San Francisco on Highway 1",
      "destination": { "label": "Omarchy Meetup, San Francisco", "lat": 37.7793, "lon": -122.4193 },
      "route_total_m": 187640.0,
      "loop_secs": 900,
      "route": [[lat, lon], ...],                                    the whole route, simplified to about 5 m
      "points": [[t, lat, lon, kph, heading, route_m, remaining_s], ...],   1 Hz, t = 0 … loop_secs
      "maneuvers": [{ "route_m", "type", "modifier", "street", "instruction" }, ...],   the whole route
      "streets": [[route_m_start, "name"], ...],
      "scenes": [{ "t": 780, "kind": "parked", "secs": 120 }],
      "events": [{ "t": 300, "kind": "hard_brake" }] }

### `map.json` (Task 3 builds it)

    { "version": 1,
      "origin": [lat0, lon0],                x = metres east, y = metres north of the origin
      "bbox": [xmin, ymin, xmax, ymax],
      "layers": { "ocean": [poly...], "water": [poly...], "rivers": [line...],
                  "roads": { "motorway": [line...], "trunk": [...], "primary": [...],
                             "secondary": [...], "tertiary": [...], "minor": [...] } },
      "labels": [{ "x", "y", "text", "class" }],
      "route": [[x, y], ...] }

- Points are `[x, y]` rounded to 0.5 m.
- The projection is `x = (lon - lon0) * cos(lat0) * 111320`, `y = (lat - lat0) * 110540`.

### Omarchy Radio (Task 4 exports it; Tasks 5, 6, 7 and 8 use it)

    // demo/js/radio.js
    export function createRadio({ base = "/demo-media/radio/", makeAudio, connect }) -> Radio
    export function getRadio() -> Radio          // the page's one player, made on first call
    Radio: { load(): Promise, tracks: [{title, artist, file}], index, playing,
             play(i?), pause(), toggle(), next(), prev(), seek(sec),
             position(), duration(), subscribe(fn) -> unsubscribe }

### Voice (Task 6 exports it; Tasks 7 and 8 use it)

    // demo/js/voice.js
    export function say(id) -> Promise<void>   // plays /demo-media/voice/<id>.wav, lowering the music 12 dB while it speaks
    export const LINES = { [id]: "text" }      // from demo/data/voice.json, for the captions

### The map for other screens (Task 3 exports it; Tasks 5 and 8 use it)

    // demo/js/views/navigation.js
    export default navigationView                                   // the Navigation tab's Maps screen
    export function mountMap(el, { style: "omacar" | "carplay" | "aa", follow: true }) -> { destroy() }

## Order

- **Task 0** (done by the controller: f1ad0fe and 3793bd1): the door, `demo/demo.html`, the empty `boot.js`, and the JS runner copying `demo/`.
- **Wave A, in parallel:** Tasks 1, 2, 3, 4, 5, 6 and 7, each in its own worktree `~/omgarchy/omacar-meetup-tN` on branch `demo/meetup-tN`, cut from `demo/meetup`.
  - Task 2's last step (the end-to-end silo test) waits for Task 1 to be merged. Rebase then.
- **Wave B:** Task 8, once Tasks 2–7 are merged.
- **Wave C:** Task 9.

---

### Task 1: The demo world

**Files:**
- Create: `lib/demoworld.py`, `tools/demo_route.py`, `test/demoworld_test.py`, `test/fixtures/demo/osrm-mini.json`, `test/fixtures/demo/drive-mini.json`
- Modify: `test/all.sh`: add `python3 "$ROOT/test/demoworld_test.py" || fails=$((fails + 1))` in a `# ---- demo/meetup` block before `exit`

**Interfaces:**
- Consumes: `lib/sim.py` (`publish`, `VEHICLE`, the values' names and shape), `records` (`STATE`)
- Produces: `python3 lib/demoworld.py run [--drive PATH]`
  - the default PATH is `$ROOT/share/assets/private/demo/drive.json`, falling back to `$ROOT/test/fixtures/demo/drive-mini.json` with a warning on stderr;
  - it writes `$OMACAR_STATE/live.json` at 5 Hz and `$OMACAR_STATE/sim.pid`, so `demo status/stop` and the daemon's "sim is running" refusal still work;
  - `demoworld.state_at(drive, t, cue_state) -> dict` is the pure core (the `live.json` payload without `t`, `uptime` or `now`-dependent fields).
- Produces: `python3 tools/demo_route.py --osrm IN --out OUT [--loop-secs 900]`, which writes `drive.json`.

**Requirements:**
1. **`tools/demo_route.py`:**
   - It reads OSRM's `routes[0]`: `geometry.coordinates` are `[lon, lat]`, and `legs[].steps[]` carry `maneuver.type`/`modifier`/`location` and `name`/`ref`. It also uses `legs[].annotation.speed`.
   - It builds a speed profile along the route: annotation speeds, capped at 105 km/h, and a stop of 25 s at the first traffic-light-like manoeuvre (the first `turn` or `end of road` step) to show auto-stop. The profile accelerates at no more than 2.0 m/s² and brakes at no more than 2.5 m/s².
   - It samples at 1 Hz for `loop_secs - 120` seconds of driving, then slows to 0 and holds for 120 s. That hold is `scenes: [{t: loop_secs-120, kind: "parked", secs: 120}]`.
   - It adds `events: [{t: 300, kind: "hard_brake"}]`.
   - Headings come from consecutive points. `route_m` is cumulative metres along the full geometry. `remaining_s` is OSRM's duration for the rest of the route, scaled by the ratio of distance left.
   - `route` is the whole geometry simplified with Douglas–Peucker at 5 m.
   - `maneuvers` are all the steps: `route_m` at each step's location, `street` = the step's `ref` or else its `name`, and `instruction` in plain English (for example, "Turn right onto Reservation Rd", "Merge onto CA-1 N", "Keep left to stay on CA-1 N", "Arrive at Omarchy Meetup").
   - `streets` are step starts with their names.
2. **`demoworld.state_at`:**
   - It interpolates `points` at `t % loop_secs`.
   - `SPEED` is km/h, rounded to 0.1.
   - `RPM` is 0 when stopped (auto-stop), else 750 + speed × 28 in 6th gear above 60 km/h, with a lower gear under that (a plausible CR-Z curve).
   - `ENGINE_LOAD` and `THROTTLE_POS` follow acceleration.
   - `COOLANT_TEMP` is 88–91 °C, `CONTROL_MODULE_VOLTAGE` 14.1–14.3 driving and 12.5 stopped, and `FUEL_LEVEL` 62 falling slowly.
   - `HYBRID_BATTERY_REMAINING` starts at 58: −0.05 %/s under assist (acceleration > 0.4 m/s²), +0.06 %/s under regen (deceleration > 0.4 m/s²), +0.005 %/s cruising, clamped to 40–78.
   - `demo.ima` follows the same thresholds: `stop` when the speed is 0.
   - `demo.next` is the first manoeuvre with `route_m` beyond the car's, and `in_m` is the difference.
   - `demo.eta` is now + `remaining_s`.
3. **Cues** (`$OMACAR_STATE/demo-cue.json`, each `at` acted on once, like `main.js`'s `honourAsk`):
   - `park` brakes to 0 at 2.5 m/s² where the car is and holds; the loop clock pauses and `scene` becomes `parked`.
   - `drive` resumes from the paused point.
   - `drowsy` sets `scene` to `drowsy` for 45 s (driving continues).
   - `hard_brake` brakes at 8 m/s² for 2 s, sets `demo.event` for 10 s, then recovers.
   - `restart` jumps to `t = 0`.
   - The scripted `events` at their times behave exactly like the `hard_brake` cue.
4. **Pacing** comes from `time.monotonic()` elapsed, not loop counts. The simulator's fivefold drift (`sim.py:1076`) must not be copied.
5. It never opens a serial port and never imports `connect`, `elm` or `daemon`.

- [ ] **Step 1:** Write `test/fixtures/demo/osrm-mini.json`: a two-step, three-km route (hand-written, the same shape as `map/raw/osrm-route.json` on the box; read that file's first step with `ssh jmyers@omarchy 'python3 -c "import json;d=json.load(open(\"Projects/omacar/share/assets/private/map/raw/osrm-route.json\"));print(json.dumps(d[\"routes\"][0][\"legs\"][0][\"steps\"][0],indent=1)[:1500])"'`).
- [ ] **Step 2:** Write `test/demoworld_test.py` with these cases. Run it; it fails on the missing modules.
  - `demo_route` on the fixture gives `points` at 1 Hz from `t=0` to `loop_secs`, speeds never above 105, and acceleration never above 2.0 / braking never above 2.5 m/s².
  - The last 120 s are parked.
  - `maneuvers[0].instruction` is non-empty.
  - `state_at(drive, 0)` has `SPEED == 0` and `RPM == 0` (auto-stop at the start).
  - A driving second has `RPM > 0`.
  - `HYBRID_BATTERY_REMAINING` stays within 40–78 over a whole loop, stepped at 0.2 s.
  - `next.in_m` falls monotonically between manoeuvres.
  - The `park` cue ends at `SPEED == 0` within 20 s of simulated time and `scene == "parked"`; `drive` resumes.
  - `drowsy` gives `scene == "drowsy"` for 45 s.
  - `restart` returns `t` to 0.
  - **Pacing:** with an injected clock advanced 10.0 s in 50 steps, `demo.t` advances 10.0 ± 0.05.
  - The module source contains none of `serial`, `import connect`, `import elm` or `import daemon`.
- [ ] **Step 3:** Implement `tools/demo_route.py`, then `lib/demoworld.py` (`state_at` pure; `DemoWorld(drive, clock=time.monotonic).step()`; `run()` loops at 0.2 s, publishing through `sim.publish` and writing `sim.pid`).
- [ ] **Step 4:** Run the suite on the box until it passes.
- [ ] **Step 5:** On the box, build the real drive:

      cd ~/Projects/.omacar-test/meetup-t1 && python3 tools/demo_route.py --osrm ~/Projects/omacar/share/assets/private/map/raw/osrm-route.json --out ~/Projects/omacar/share/assets/private/demo/drive.json

  Then print the first 5 manoeuvres, the loop's max speed, and the t of the first stop. Paste them into your report.
- [ ] **Step 6:** Commit, then run `./test/all.sh` on the box and report its tail.

### Task 2: The silo and the demo server

**Files:**
- Modify: `lib/serve.py`: `--demo DIR` (a new `demo_arg(argv) -> str | None`; leave `parse_args` and its 5-tuple alone), the URL map, the allowlist and Range for `/demo-media/`.
- Modify: `bin/omacar`: `demo on|off|check|tour|trash`, with `start`/`stop` as aliases and `status` kept, plus the help text at lines 69–71.
- Modify: `lib/card.py`: honour `XDG_STATE_HOME` for its cache path.
- Create: `lib/demoguard.py`, `test/demoserve_test.py`, `test/demo_silo_test.py`
- Modify: `test/workshop_test.py` and `test/guards_test.py` where they pin the demo verbs' strings (keep their intent, update the strings); `test/all.sh`

**Interfaces:**
- Consumes: `lib/demoworld.py run` (Task 1). Until Task 1 merges, the start function calls it and the silo test is the last step.
- Produces:
  - the URL map and `POST /api/demo/cue` (above);
  - `omacar demo on` / `off` / `check` / `tour`. `check` runs `"$OMACAR_PY" "$ROOT/lib/democheck.py"`; `tour` runs `demo on` and then POSTs `{"view": "demo-tour", "who": "omacar demo tour"}` to `/api/screen` on 7580. Task 8 creates `democheck.py` and the `demo-tour` screen; until then `check` prints "the check arrives with Task 8" and exits 0.

**Requirements:**
1. **`demo_env`** also exports:
   - `OMACAR_VIDEOS="$DEMO_ROOT/videos"`;
   - `OMACAR_PORT="$DEMO_ROOT/no-adapter"` (a path that never exists; `connect.resolve()` honours `OMACAR_PORT` first, per `lib/connect.py:246-257`).
   - The server and the camera feed alone get `XDG_RUNTIME_DIR="$DEMO_ROOT/run"`, passed on their command line (`env XDG_RUNTIME_DIR=… …`), never exported to the browser, which needs the real one for Wayland.
   - Keep everything `test/workshop_test.py:1153-1222` pins, and add to it.
2. **`demo on`:**
   - Refuse, with a message, if the real `live.json` (read before `demo_env` changes `OMACAR_STATE`) has `t` within 5 s of now and `values.SPEED > 3`.
   - Seed (`sim.py seed`) if new.
   - Start `lib/demoworld.py run`, `lib/cams.py demo` (Task 7; start it only if `lib/cams.py demo --help` exits 0, so this task doesn't depend on Task 7), `serve.py 7580 "$ROOT/share" --demo "$ROOT/demo"` and `lib/demoguard.py`.
   - Open the window:

         chromium --user-data-dir="$DEMO_ROOT/browser" --app=http://127.0.0.1:7580/demo.html --kiosk --start-fullscreen --noerrdialogs --disable-infobars --disable-session-crashed-bubble --disable-features=TranslateUI,Translate --no-first-run --password-store=basic --autoplay-policy=no-user-gesture-required --ozone-platform=wayland

     Use the same flags as the live kiosk (`bin/omacar:585-598`). Add `--mute-audio` when `OMACAR_DEMO_MUTE=1` (tests, and the night).
   - Write `ACTIVE` as today.
   - **Road cameras offline:** if the demo state has no `roadcams/` yet, copy the real `~/.local/state/omacar/roadcams/` (the saved feed and stills) and the real `~/.config/omarchy/omacar-roadcams.json` (the pinned commute) into the demo's state and config. This only reads the real side, and the saved stills keep their real ages.
3. **`demo off`** stops the world, the camera feed, the server, the guard and the demo browser (found by `pgrep -f "user-data-dir=$DEMO_ROOT/browser"`), removes `ACTIVE`, and prints what it stopped. It never touches another process.
4. **`lib/demoguard.py`** is started by `demo on` with the real `live.json` path as its argument. Every 2 s it reads that file read-only. When it's fresh (`t` within 5 s) and `SPEED > 3`, it runs `omacar demo off` and exits. It also exits when `ACTIVE` is gone.
5. **Serving under `--demo DIR`:**
   - Answer the URL map. Resolve paths safely: `os.path.realpath` must stay inside the mapped root, or answer 404.
   - `/demo-media/*` uses `_ranged` (`serve.py:147`) with the right content type (mp3, wav, mp4, json, png).
   - Without `--demo`, `/demo.html`, `/demo/*` and `/demo-media/*` answer 404.
6. **The allowlist under `--demo`:**
   - List every `/api/` path the live page can call: `share/js/core.js` `api` object, plus `grep -rn '"/api/' share/js`.
   - Classify each as **ALLOW** (reads only demo state, or writes only demo state: `/api/home`, `/api/screen`, `/api/cams*` including mark and lock, `/api/drowsy*`, `/api/roadcams*`), **STUB** (background calls answered with inert JSON so the page stays quiet: `/api/audio` → `{"managed": false, "demo": true}`; `/api/ai/available` → `{"available": false}`), or **REFUSE** (everything else, especially `/api/begin`, `/api/learn`, `/api/clear`, `/api/reset`, `/api/write*`, `/api/phone*`, the assistant, `/api/ai/*`, daemon control), answered with 403 `{"error": "not in the demo"}`.
   - Keep the table as a dict in `serve.py` (`DEMO_ROUTES`), with a comment giving the reason for each entry.
   - An unknown `/api/` path is refused.
7. **`lib/card.py`** writes its cache under `$XDG_STATE_HOME/omarchy/`, so `omacar demo cache` writes the demo's own.
8. **Tests:**
   - `test/demoserve_test.py` starts `serve.py` on a free port, in a scratch HOME with `--demo`, and asserts:
     - every REFUSE route answers 403 on GET and POST;
     - `/api/audio` gives the stub;
     - `/demo.html` answers 200 and contains `demo/js/boot.js`;
     - `/demo-media/../../etc/passwd` and the encoded `%2e%2e` form answer 404;
     - a Range request on a fixture wav answers 206 with the right bytes;
     - `POST /api/demo/cue {"cue":"park"}` writes the cue file.
   - Then, without `--demo`, it asserts 404 for `/demo.html`, `/demo/js/boot.js` and `/demo-media/logo.png`.
   - Also, `share/app.html` contains no `demo/`.
   - `test/demo_silo_test.py` makes a scratch HOME and fills it with fake real state: `~/.local/state/omacar/live.json` (parked), `~/.config/omarchy/omacar-audio.json`, `~/Videos/OmaCar/front/x.mp4` and `~/.local/state/omarchy/liquid-glass-car.json`. It puts `PATH` shims for `systemctl`, `wpctl`, `pactl`, `hyprctl` and `chromium` that append their argv to a log, then runs `OMACAR_DEMO_MUTE=1 bin/omacar demo on`, POSTs a `park` cue, waits 3 s, and runs `demo off`. It asserts:
     - every file outside `DEMO_ROOT` is byte-identical;
     - the shim log has no `systemctl`, `wpctl` or `pactl` line;
     - `chromium` was called with `--user-data-dir=…/omacar-demo/browser` and `--app=http://127.0.0.1:7580/demo.html`;
     - no process is left holding port 7580.
   - A second case writes a fresh moving real `live.json` (`SPEED` 50) and asserts `demo on` refuses without starting anything.
- [ ] **Step 1:** Write `test/demoserve_test.py`; it fails.
- [ ] **Step 2:** Implement `serve.py`'s demo mode and `DEMO_ROUTES`, then pass it.
- [ ] **Step 3:** Write `test/demo_silo_test.py`'s moving-car refusal case; implement the `bin/omacar` verbs, `demoguard.py` and the `card.py` fix; pass it.
- [ ] **Step 4:** Update `workshop_test.py` and `guards_test.py`, and run both.
- [ ] **Step 5:** After Task 1 is merged into `demo/meetup` (the controller tells you), rebase, then write and pass the end-to-end silo case.
- [ ] **Step 6:** On the tablet, check only whether a second fullscreen Chromium window covers the kiosk, keeping the screen off:
  1. `OMACAR_DEMO_MUTE=1 omacar demo on` from the branch's box mirror, copied to `~/Projects/.omacar-wt/meetup` on the tablet, never the live checkout.
  2. `hyprctl clients -j`: is the demo window fullscreen and focused, above the kiosk?
  3. `omacar demo off`.
  4. Put the answer in your report. If it doesn't cover the kiosk, report the Hyprland option that decides it (`hyprctl getoption misc:new_window_takes_over_fullscreen` / `misc:on_focus_under_fullscreen`) and stop; the controller decides.
- [ ] **Step 7:** Commit, then run `./test/all.sh` on the box and report its tail.

### Task 3: The map

**Files:**
- Create: `tools/demo_map.py`, `demo/js/map.js`, `demo/js/nav.js`, `demo/js/views/navigation.js`, `demo/js/cards/navcard.js`, `demo/css/nav.css`, `test/fixtures/demo/osm-mini.json`, `test/demo_map_test.py`, `test/js/demo-map.test.js`
- Modify: `test/all.sh`

**Interfaces:**
- Consumes: `drive.json` (its `route`), and `store` from `share/js/core.js` (`store.live.demo`, `store.live.values.SPEED`)
- Produces:
  - `map.json` (above);
  - `map.js`: `project(lat, lon, origin) -> [x, y]`, `createMap(canvas, { data, style }) -> { setView({ x, y, heading, mpp }), resize(), draw(), destroy() }`, `STYLES = { omacar, carplay, aa }`, `loadMapData(url = "/demo-media/map.json") -> Promise<data>` (cached);
  - `nav.js`: `bannerOf(next) -> { icon, distance, text, street }` and `etaOf(demo, now = Date.now()) -> { time, remaining, distance }`;
  - `views/navigation.js`: `navigationView` (default export) and `mountMap(el, { style, follow })`, plus `register(D)`, which sets `D.views.navigation = { mount: navigationView, fast: true }` and `D.tabRoots.navigation = "navigation"`;
  - `cards/navcard.js`: `navCard()` and `register(D)`, which sets `D.cards.nav = navCard`.

**Requirements:**
1. **`tools/demo_map.py --roads R --water W --freeways F --drive D --out O`:**
   - The origin is the drive's first point.
   - Detail roads are classed:
     - `motorway` (with its links);
     - `trunk` (with its links);
     - `primary`;
     - `secondary`;
     - `tertiary`;
     - `minor` (unclassified, residential, living_street).
   - Freeways outside the detail box join `motorway`/`trunk`, simplified at 25 m. Detail lines are simplified at 2 m (Douglas–Peucker).
   - Water: `natural=water` closed ways become polygons, and `waterway` becomes `rivers` lines.
   - **The ocean:** join coastline ways end to start (OSM coastlines have the land on the left and water on the right). Take the chain that crosses the detail box, and close it along the box's western edge into one polygon. The Pacific and Monterey Bay are west.
   - Labels come from named `primary`/`trunk` ways, one per name per 3 km.
   - `route` is the drive's route, projected.
   - The output is under 8 MB. Print its size.
2. **Drawing (`map.js`, Canvas 2D):**
   - Honour `devicePixelRatio`.
   - Heading-up: rotate so `heading` points up. The car sits at 50% across and 70% down.
   - Layers in order:
     1. land background;
     2. the ocean and water;
     3. rivers;
     4. roads minor → motorway, with widths growing by class;
     5. the route, cyan, with a soft glow and a lighter casing;
     6. labels;
     7. the car arrow, whose glyph matches mockup 3.
   - Cull by the view box.
   - The draw budget is 16 ms on the tablet (measure in Step 5).
   - **Styles:**
     - `omacar`: mockup 3/7, a near-black land, a deep blue-slate sea, slate roads and a cyan route.
     - `carplay`: a lighter night grey with a blue route.
     - `aa`: a dark teal land with a blue route.
   - The credit "© OpenStreetMap contributors" is drawn in the corner by the view, not the canvas.
3. **`nav.js`:**
   - Distances are imperial:
     - under 0.1 mi: feet, rounded to 50 ft;
     - under 10 mi: one decimal;
     - otherwise whole miles.
   - Icons are the ids `turn-left`, `turn-right`, `slight-left`, `slight-right`, `straight`, `merge`, `fork-left`, `fork-right`, `ramp-right`, `ramp-left`, `arrive` and `uturn`, mapped from OSRM's type and modifier. Draw them as inline SVG in `nav.js` (`iconSvg(id)`).
   - `etaOf` gives the time as `h:mm AM/PM`, and the remaining time as `N min` or `H h M min`.
4. **The Navigation screen** (spec §4): a full-bleed map, following the car at about 3 m/px driving and 1.5 m/px when stopped, eased.
   - A turn banner at the top, as in mockup 3's nav card: an icon, the distance, the text and the street.
   - A bottom bar with the ETA, the distance and the remaining time, and the destination label.
   - A "Road cameras" chip going to `#roadcams`.
   - The credit.
   - It reads `store.live.demo` on each paint. With no `demo` block, it shows "Waiting for the demo drive".
5. **Home's nav card:** mockup 3's card (the banner, a mini map at about 6 m/px, and the ETA row), tappable to `#navigation`.
6. **Tests:**
   - `test/demo_map_test.py` builds from `osm-mini.json` and a mini drive, and asserts:
     - classes are present;
     - the ocean polygon is closed and lies west of the coastline;
     - no coordinate is outside the bbox by more than 1 km;
     - the file is deterministic.
   - `test/js/demo-map.test.js`:
     - `project` round-trip within 0.5 m at 50 km;
     - `bannerOf` for 1300 m → "0.8 mi", 150 m → "500 ft", 20 km → "12 mi";
     - the type/modifier → icon table;
     - `etaOf` formatting;
     - `createMap` on an OffscreenCanvas-backed stub draws without throwing, and `setView` rotates, checked by projecting a point north of the car when heading is 90 (it must land left of centre).
- [ ] **Step 1:** Write the fixtures and both tests; they fail.
- [ ] **Step 2:** Implement `demo_map.py` and pass its test.
- [ ] **Step 3:** Implement `map.js` and `nav.js`, and pass the JS tests.
- [ ] **Step 4:** Implement the view and the card, plus `register(D)` in each.
- [ ] **Step 5:** On the box, build the real map:

      python3 tools/demo_map.py --roads ~/Projects/omacar/share/assets/private/map/raw/osm-roads.json --water ~/Projects/omacar/share/assets/private/map/raw/osm-water.json --freeways ~/Projects/omacar/share/assets/private/map/raw/osm-freeways.json --drive ~/Projects/omacar/share/assets/private/demo/drive.json --out ~/Projects/omacar/share/assets/private/demo/map.json

  If Task 1 hasn't built `drive.json` yet, build it first with Task 1's command, from your branch after merging it in. Render two screenshots, headless at 1368×912: the view at t=60 s and at t=400 s. Make a scratch page in your mirror that sets `OMACAR_DEMO` from your `register` and feeds a fixed `store.live`. Record the draw time. Put the PNGs in `~/Projects/.omacar-test/meetup-t3/shots/` and name them in your report.
- [ ] **Step 6:** Commit, then run `./test/all.sh` and report its tail.

### Task 4: Omarchy Radio

**Files:**
- Create: `demo/js/radio.js`, `demo/js/views/nowplaying.js`, `demo/js/cards/phonecard.js`, `demo/css/radio.css`, `test/js/demo-radio.test.js`

**Interfaces:**
- Consumes: `share/js/audiobus.js` (`audioContext()`, `musicIn()`, `resume()`), and `/demo-media/radio/playlist.json` (the station's format: `{station, name, source, tracks: [{title, artist, file}]}`)
- Produces:
  - the Radio contract (above);
  - `views/nowplaying.js`: `nowPlayingView` and `register(D)`, which pushes `{ id: "nowplaying", label: "Now Playing", title: "Omarchy Radio", mount, fast: false }` onto `D.extraViews`;
  - `cards/phonecard.js`: `phoneCard({ onCarPlay, onAndroidAuto })` and `register(D)`, which sets `D.cards.phone`.

**Requirements:**
1. **`createRadio`:**
   - `makeAudio()` defaults to `() => new Audio()`; the element gets `preload = "auto"` and `crossOrigin = "anonymous"`.
   - `connect(el)` defaults to `audioContext().createMediaElementSource(el).connect(musicIn())`, done once per element and guarded.
   - Tracks play in playlist order and wrap. `ended` goes to `next`.
   - `subscribe(fn)` calls `fn(state)` on every change and every 500 ms while playing. The state is `{index, title, artist, playing, position, duration}`.
   - `getRadio()` returns one instance per page.
   - Nothing autoplays: playing starts only from `play()`.
2. **The Now Playing screen:**
   - The station card is set in type ("OMARCHY RADIO" over "every song a pull request"), with no artwork.
   - The title is large, with the artist below it.
   - A scrubbable progress bar showing `m:ss` / `m:ss`.
   - Previous / play-pause / next, at 64 px or more, like the mockup's music tile.
   - The list of seven, with the playing one marked.
   - A quiet line "radio.omarchy.org".
3. **The phone card** is mockup 3's Phone integration card, with three rows:
   1. "Apple CarPlay" (→ `onCarPlay`, default `location.hash = "#carplay"`);
   2. "Android Auto" (→ `#androidauto`);
   3. a Now Playing row: a small type-set "OR" tile, the title · artist, and a play/pause button. The row itself goes to `#nowplaying`.

   The CarPlay and Android Auto rows use neutral glyphs (a rounded square with a play triangle; a stylised "A" wedge), never the companies' marks.
4. **Tests** (with a fake audio element whose `play()` resolves, and a fake `connect`):
   - `load` reads the seven tracks;
   - `play(2)` sets index 2 and `playing`;
   - `next` at 6 wraps to 0, and `prev` at 0 goes to 6;
   - `ended` advances;
   - `connect` is called once even after three plays;
   - `getRadio() === getRadio()`;
   - subscribers get a state after `play`;
   - the `m:ss` formatter gives 0 → "0:00", 61.4 → "1:01" and 3600 → "60:00";
   - the source contains no `wpctl` and no `/api/audio`.
- [ ] **Step 1:** Write the tests; they fail.
- [ ] **Step 2:** Implement `radio.js`; pass them.
- [ ] **Step 3:** Implement the screen and the card.
- [ ] **Step 4:** Take one headless screenshot of each on a scratch page (muted), as in Task 3 Step 5, and name them in your report.
- [ ] **Step 5:** Commit, then run `./test/all.sh` and report its tail.

### Task 5: CarPlay and Android Auto

**Files:**
- Create: `demo/js/views/carplay.js`, `demo/js/views/androidauto.js`, `demo/js/projection.js` (the parts both share: the status bar, the clock, the app grid, the screen router), `demo/css/projection.css`, `test/js/demo-projection.test.js`

**Interfaces:**
- Consumes (injected, so this task doesn't wait for Tasks 3 and 4):
  - `deps.radio` (the Radio contract);
  - `deps.mountMap(el, { style }) -> { destroy() }`;
  - `deps.back()` (default `location.hash = "#home"`).
- Produces:
  - `carplayView(deps) -> mount` and `androidAutoView(deps) -> mount`;
  - `register(D, deps)` in each, pushing `{ id: "carplay", label: "CarPlay", title: "Apple CarPlay", mount: carplayView(deps), fast: true }` and `{ id: "androidauto", label: "Android Auto", title: "Android Auto", mount: androidAutoView(deps), fast: true }`.

**Requirements:**
1. **A full-screen takeover.** Hide the app's bars while mounted, with a class on `document.body` removed on unmount. Each has its own chrome, drawn fresh in that platform's general style:
   - **CarPlay:**
     - a left dock with the time, signal and the three recent apps;
     - a home grid of rounded-square app tiles: Maps, Now Playing, Phone, Messages, Podcasts, Calendar and Settings, plus an "OmaCar" tile that calls `back()`;
     - SF-like system type: use the app's own font stack.
   - **Android Auto:**
     - a bottom rail with the launcher, the time and the recent apps;
     - a split "dashboard": the map on the left, and media plus a suggestion card on the right;
     - an app launcher grid;
     - an "OmaCar" exit.
   - Glyphs are simple inline SVGs (map pin, music note, phone handset, speech bubble and so on), never Apple's or Google's artwork or wordmarks. The word "CarPlay" or "Android Auto" appears only in the status area.
2. **Maps** in each: `deps.mountMap(el, { style: "carplay" | "aa" })` fills the map area, with a turn card in that style from `store.live.demo.next`. Re-implement a small banner, or import `bannerOf` from `../nav.js` when it exists; guard the import so the test runs without it.
3. **Now Playing** in each is bound to `deps.radio`: the title, artist, progress and controls. The same song continues when you switch screens, because it is one Radio.
4. **Phone and Messages** are static, believable screens with made-up names: favourites "Mom", "Home", "Office", and one message thread "Leaving Los Banos now, see you at the meetup 🎉". There is no real data.
5. **Tests** (with fake deps):
   - mounting each view adds the takeover class and unmounting removes it;
   - the home grid has an "OmaCar" tile that calls `back`;
   - opening Maps calls `mountMap` once with the right style, and leaving it calls `destroy`;
   - the Now Playing screen reflects a radio state pushed through `subscribe`;
   - the source has no `apple.com`, `google.com` or `.svg` URLs, and no image files.
- [ ] **Step 1:** Write the tests; they fail.
- [ ] **Step 2:** Implement `projection.js`, then the two views; pass them.
- [ ] **Step 3:** Take screenshots at 1368×912 and 1920×1080 (the home and Now Playing screens of each) with a fake map (a gradient) on a scratch page, and name them in your report.
- [ ] **Step 4:** Commit, then run `./test/all.sh` and report its tail.

### Task 6: Agent, Work and the voice

**Files:**
- Create:
  - `demo/js/views/agent.js`, `demo/js/views/work.js`, `demo/js/voice.js`;
  - `demo/data/agent.json`, `demo/data/work.json`, `demo/data/voice.json`;
  - `tools/demo_voice.py`;
  - `demo/css/agent.css`, `demo/css/work.css`;
  - `test/js/demo-agent.test.js`, `test/demo_voice_test.py`
- Modify: `test/all.sh`

**Interfaces:**
- Consumes:
  - `deps.radio` (the Radio contract);
  - `api.home()` and `api.saveHome(body)` from `share/js/core.js` (the demo server allows `/api/home`);
  - `applyLook` and `lookById` from `share/js/looks.js`;
  - `share/js/audiobus.js` (`audioContext()`, `schedule()`, `currentDb()`, `MUSIC_DB`).
- Produces:
  - `agentView(deps) -> mount`, with `register(D, deps)` setting `D.views.advisor = agentView(deps)`;
  - `workView(deps) -> mount`, with `register(D, deps)` setting `D.views.work = { mount: workView(deps), fast: true }`;
  - `voice.js`: `say(id)` and `LINES`;
  - `python3 tools/demo_voice.py --lines demo/data/voice.json --out DIR [--piper P --model M]` writes `DIR/<id>.wav` for every line.

**Requirements:**
1. **The Agent screen** is mockup 7 (`doc/design/mockups/7.webp`).
   - **The left panel:** "Your CR-Z" with the car picture (`/demo-media/crz-home.png` if it loads, else nothing), "2015 · Sport Hybrid", and "Vehicle context" with three rows: "OBD-II · Demo, Live data connected"; "Honda enhanced · Demo, OEM data & systems"; "Cameras · Demo, 3 cameras available".
   - **The chat:**
     - the user's line in a bubble with its time;
     - "thinking" as three pulsing dots for 700–1200 ms;
     - then the reply streamed at about 45 characters/s.
   - **Chips:** "Explain a warning", "Review my drive", "Check vehicle health", "Build me a focused night-drive layout" and "Play Omarchy Radio".
   - **The input line** is "Ask about your car or change your layout…", with a mic.
     - The mic shows "Listening…" with a level animation for 1.6 s, then types the next unasked chip's question and sends it.
     - Free text is matched to the script by keywords. With no match, the reply is: "In this demo I know a few questions. Try one of the suggestions below."
   - **"Illustrative agent responses"** sits under the input.
   - **The night-drive reply** includes the preview card "Night drive · Landscape + portrait", drawn as a small version of the layout: nav, dial, music and three tiles. It has **Preview layout** and **Apply layout** buttons.
     - **Preview** shows it full size in an overlay.
     - **Apply**:
       1. `GET /api/home` and keep it (to put back on `restart`);
       2. `POST /api/home` with the night arrangement (`nav`, `dial`, `phone`, `charge`, `coolant`, `fuel` placed first; read `share/data/home-cards.json` and `lib/homelayout.py` for the body's shape);
       3. `applyLook(lookById(<the darkest night look in share/js/looks.js>))`;
       4. say `layout-applied`.
     - **Only while parked:** while `store.live.demo.parked` is false, Apply is disabled with the reason "Park to change your layout". The same rule as the live app, and a talking point.
   - **"Play Omarchy Radio"** replies "Playing Omarchy Radio: <title>, by Ryan R. Hughes.", calls `deps.radio.play(0)`, and shows a small now-playing chip in the reply.
   - The script lives in `demo/data/agent.json` as `[{id, chip, keywords[], reply, action?: "night-layout" | "radio" | null, voice?: id}]`.
     - Replies are grounded in the demo's own numbers where they name any. For example, Review my drive: "Your last 30 minutes: 38.2 mpg, 64% of braking recovered by the IMA, one hard stop on CA-1 near Castroville."
     - Explain a warning: "No warnings right now. The last one was P0420 in March, cleared after the cat was replaced."
     - Vehicle health: "All systems normal: the IMA pack is balanced, coolant 190°F, 12V at 14.2 V."
2. **The Work screen** is mockup 8 (`doc/design/mockups/8.webp`).
   - **Session tiles:** Claude 01, 02 and 03 (Max 20x) and Codex (Subscription), each with a usage ring.
   - **Four sessions from `work.json`:**
     - OmaCar: "Implementing voice controls", Running;
     - Bulletin Builder: "Tests passed, ready for review";
     - OmaSaber: "Needs input";
     - OmaCar mobile (Codex): Running.

     Each has a checklist whose steps tick along on timers: a step every 20–40 s, looping.
   - **The session detail:** the checklist, a voice chat, and **Speak** / **Pause session** / **Open review**, with "Ready for review, 6 files changed".
   - **"Give me an update"** says `work-update` and shows the words.
   - **While `store.live.values.SPEED > 3`:** the driving view, "Your agents are working" (2 running, 1 ready, 1 needs you), with "Listening…" and **Send instruction**, as in the mockup.
   - **"CONCEPT · DEMO SESSIONS"** in the header.
3. **`voice.json`:** `[{id, text}]`, including:
   - `work-update`: "Two agents are running. Bulletin Builder passed its tests and is ready for your review. OmaSaber needs your input on the saber colour.";
   - `layout-applied`: "Night drive is on. Navigation stays large, and I've dimmed the rest.";
   - `radio`: "Playing Omarchy Radio.";
   - `drowsy-l2`: "James, are you with me?";
   - `health`: "All systems normal.";
   - one line for every agent reply that has `voice`.
4. **`say(id)`:**
   - It fetches and decodes `/demo-media/voice/<id>.wav` once (cached).
   - It ramps the music bus down 12 dB over 0.4 s with `schedule("music", …)`, plays the buffer through a gain to `audioContext().destination`, then ramps back over 0.8 s.
   - It resolves when done. With the file missing, it resolves at once, and the captions still show.
5. **`tools/demo_voice.py`** runs Piper per line (`piper -m MODEL -f OUT.wav`, text on stdin). The defaults are the paths in the Global constraints. It skips lines whose wav is newer than `voice.json`, and prints each file's duration.
6. **Tests:**
   - `test/js/demo-agent.test.js`:
     - keyword matching (for example "how's my car doing" → health; "night" → night layout; "radio" → radio; nonsense → fallback);
     - streaming reveals the reply in order and completely;
     - Apply is disabled with the reason when not parked;
     - the Work driving view appears when SPEED > 3;
     - `say` resolves when the fetch fails.
   - `test/demo_voice_test.py`: with a fake `--piper` script that writes a 1 s silent wav, every line is rendered, and a second run renders none.
- [ ] **Step 1:** Write the tests; they fail.
- [ ] **Step 2:** Implement `voice.js` and `demo_voice.py`; pass their tests.
- [ ] **Step 3:** Implement the Agent screen and the script; pass the tests.
- [ ] **Step 4:** Implement the Work screen; pass the tests.
- [ ] **Step 5:** On the box, render the real lines to `~/Projects/omacar/share/assets/private/demo/voice/` and list the durations. Do not play them.
- [ ] **Step 6:** Take headless screenshots of Agent (after the night-layout reply) and Work (parked and driving) at 1368×912, and name them in your report.
- [ ] **Step 7:** Commit, then run `./test/all.sh` and report its tail.

### Task 7: The demo cameras and the drowsy moment

**Files:**
- Modify: `lib/cams.py`: a `demo` subcommand, and `_ours()` accepting `demo` in argv
- Create: `demo/js/drowsy.js`, `demo/css/drowsy-demo.css`, `test/js/demo-drowsy.test.js`
- Modify: `test/cams_test.py` (new cases)

**Interfaces:**
- Consumes:
  - the clips at `$ROOT/share/assets/private/demo/clips/{front,rear,cabin,cabin-drowsy}.mp4` (override with `--from DIR`);
  - `OMACAR_VIDEOS` and `XDG_RUNTIME_DIR` set by Task 2;
  - `store.live.demo.scene` and `.event`;
  - `share/js/drowsyui.js` (read its exports: the chip and alert-card renderers and `?dz=` handling);
  - `alertPlayer()` from `share/js/alertplayer.js`;
  - `say("drowsy-l2")` from Task 6 (guard the import).
- Produces:
  - `python3 lib/cams.py demo [--from DIR]`;
  - `demo/js/drowsy.js`: `register(D, deps)`, which starts the demo drowsy controller once the app has booted and wraps `afterBar` to add the chip. Keep the chip in its own element; Task 8 composes `afterBar`s.

**Requirements:**
1. **`cams.py demo`:**
   - **Clips:** it copies each role's clip into `$OMACAR_VIDEOS/<role>/` as one-minute pieces named `YYYYmmdd-HHMMSS.mp4` for the last 50 minutes. Use `ffmpeg -c copy -f segment -segment_time 60 -reset_timestamps 1` with fragmented-MP4 flags, as the recorder writes them (`cams.py:302-343`), looping the source as needed. They're regular files, not symlinks (`camstore._safe_clip` refuses symlinks).
   - **Live frames:** it writes `$XDG_RUNTIME_DIR/omacar-cams/<role>.jpg` at 10 fps and 640 px wide, from one `ffmpeg -stream_loop -1 -re -i clip -vf fps=10,scale=640:-2 -q:v 5 -update 1 <role>.jpg` per role.
   - **Status:** it writes `status.json` every second in the shape `write_status` uses (`cams.py:617`). `_ours()` must accept a `cams.py … demo` process, so `overview()` reports the recorder running.
   - **The drowsy cabin:** when `$OMACAR_STATE/live.json`'s `demo.scene` is `drowsy`, the cabin's frame source switches to `cabin-drowsy.mp4`. Restart that one ffmpeg, and switch back when the scene ends.
   - It never opens `/dev/video*`, never imports the device-probing code paths, and refuses to run unless `OMACAR_VIDEOS` is set and contains `omacar-demo`.
   - Children are killed on SIGTERM.
2. **The drowsy moment (`demo/js/drowsy.js`, on the demo page only; the live engine isn't loaded there):**
   - **The chip:** "Watching" (tone ok) while driving, and "Paused · stopped" when parked, using drowsyui's own chip renderer so it looks identical.
   - **On `scene` changing to `drowsy`:**
     1. t = 0: the Level 1 card ("You seem tired. Plan a break soon.", reason "Eyes closing more often") and `alertPlayer().play(<Level 1 sequence>)`. Reuse the exact sequence `drowsyrun.js` `engine.test()` plays for Level 1.
     2. t = 9 s: Level 2, the full-screen "Are you with me?", with the Level 2 sequence and then `say("drowsy-l2")`.
     3. "I'm awake" (a tap) ends it at any point: the sounds stop, fading within 3 s as the player already does.
     4. The chip returns to "Watching".
     5. With no tap, it ends by itself at 30 s.
   - **On `demo.event.kind === "hard_brake"`:** `POST /api/cams/mark` once per event (the Cameras timeline shows a locked event), and a toast "Hard braking. The clip is saved."
3. **Tests:**
   - `test/cams_test.py` (demo cases, scratch folders, ffmpeg `testsrc2` clips of 3 s):
     - `demo` creates correctly named regular files for the last 50 minutes;
     - `status.json` makes `overview()` report the roles as running;
     - it refuses when `OMACAR_VIDEOS` lacks `omacar-demo`;
     - it never touches `/dev` (monkeypatch `os.open`/`open` to fail on `/dev/`).
   - `test/js/demo-drowsy.test.js`, with a fake clock, a fake player and a fake `say`:
     - drive → drowsy gives Level 1 at 0 and Level 2 at 9 s;
     - "I'm awake" stops the player and restores the chip;
     - it times out at 30 s;
     - a `hard_brake` event marks once even when seen on 5 paints.
- [ ] **Step 1:** Write the tests; they fail.
- [ ] **Step 2:** Implement `cams.py demo`; pass its tests.
- [ ] **Step 3:** Implement `drowsy.js`; pass its tests.
- [ ] **Step 4:** Commit, then run `./test/all.sh` and report its tail.

### Task 8: Running the show

**Files:**
- Modify: `demo/js/boot.js` (the wiring)
- Create:
  - `demo/js/tour.js`, `demo/js/menu.js`, `demo/js/bar.js`, `demo/data/tour.json`;
  - `demo/css/demo.css` (the whole demo's polish: the DEMO pill, the logo, the menu, the captions, and hiding the "SIMULATED" source badge `.tb-src.warn` in the demo only);
  - `demo/js/views/scan.js` (the scripted Scan vehicle);
  - `lib/democheck.py`, `tools/demo_carpic.py`;
  - `test/js/demo-tour.test.js`, `test/democheck_test.py`
- Modify: `test/all.sh`

**Interfaces:**
- Consumes: every `register` above, `getRadio`, `mountMap`, `say`, and `POST /api/demo/cue`
- Produces: the finished demo page; `omacar demo check`; and the `demo-tour` screen that starts the tour, used by `omacar demo tour`.

**Requirements:**
1. **`boot.js`** imports every part and calls, in order:
   1. `register(D)` for the map, the nav card and the drowsy controller;
   2. `register(D, deps)` with `deps = { radio: getRadio(), mountMap, back: () => location.hash = "#home" }` for Now Playing, the phone card, CarPlay, Android Auto, Agent and Work.

   It also sets the Home car card to `/demo-media/crz-home.png` via `D.cards.car`, and composes `afterBar` from `bar.js` and the drowsy chip. `onKey` is the tour's keys. `getRadio().load()` runs at boot.
2. **`bar.js`** (afterBar, idempotent):
   - The top bar's "OmaCar" word (`.tb-mark`) becomes `<img src="/demo-media/logo.png" alt="OmaCar">`, 28 px high in landscape and 24 px in portrait.
   - A small "DEMO" pill sits on the right, as the mockups place "CONCEPT · DEMO DATA".
   - A 700 ms press on the logo opens the menu.
3. **The menu (`menu.js`):**
   - Start tour / Resume tour;
   - Drowsy moment;
   - Hard braking;
   - Park / Drive (by state);
   - Restart the drive;
   - Play backup video, which POSTs a cue the server doesn't know. Instead, show the command `omacar demo video` as text. Task 9 adds that verb.
   - Exit demo, which shows "Run `omacar demo off`, or press Super+W" (the page can't close its own kiosk window safely).

   Cues POST to `/api/demo/cue`.
4. **The tour (`tour.js`, steps in `tour.json`):** about 6 minutes, following spec §6.

   | # | Step | Duration and actions | Caption |
   |---|---|---|---|
   | 1 | Home | 40 s; plays the radio at 25 s | "Your car, live: speed, the hybrid pack and every sensor, from the car's own computers." |
   | 2 | Navigation | 40 s | "Turn-by-turn on the tablet, working offline." |
   | 3 | Road cameras | 15 s | "Caltrans cameras along the route." |
   | 4 | Cameras | 35 s; `hard_brake` cue at 5 s | "Three cameras, recorded in one-minute clips. A hard stop saves the clip." |
   | 5 | The drowsy moment | 40 s; stays on Home, `drowsy` cue at 3 s | "Drowsy mode watches the driver's eyes, entirely on the tablet, and wakes you gently." |
   | 6 | Vehicle | 30 s; opens `#scan` at 15 s | "Diagnostics for every module, in plain English." |
   | 7 | Agent | 60 s; `park` cue at 0, night layout at 8 s, Apply at 30 s, Play Omarchy Radio at 45 s | "Oma Agent knows this car, and changes the dashboard for you." |
   | 8 | Work | 40 s; `drive` cue at 0, "Give me an update" at 10 s | "Your coding agents, by voice, while you drive." |
   | 9 | CarPlay | 35 s; home → Maps → Now Playing | "Your phone, when you want it." |
   | 10 | Android Auto | 30 s | (none) |
   | 11 | Home | 10 s | "OmaCar: open source, on Omarchy." |

   - Captions are one line at the bottom, 22 px, fading between steps.
   - Any touch or key pauses the tour and shows "Tour paused · tap Resume" for 3 s. Resume is in the menu or Space.
   - **Keys:** 1–9 jump to that step; D drowsy; B hard braking; P park/drive; Space pause/resume; Esc menu.
   - The `demo-tour` extra view starts the tour and routes to step 1.
   - The tour's actions call the modules' functions (not simulated clicks) where they exist. For Apply, it calls the agent view's exported action. Coordinate with the modules' exports as merged.
5. **`lib/democheck.py`** prints one line per check and exits 0 only if all pass:
   - the songs are present and their sizes match `playlist.json`'s files;
   - `drive.json`, `map.json` and the voice wavs are present;
   - the clips are present, or reports "stock/owner clips missing: <roles>";
   - `omacar-logo.png` and `crz-home.png` are present;
   - the live volume pin is off (`omacar-audio.json` absent or not managed, in the REAL `~/.config/omarchy`);
   - the live kiosk is running (`pgrep -f 'user-data-dir=.*/omacar/kiosk-profile'`);
   - the demo server answers `/demo.html` if the demo is on.

   Its last line is "ready" or "not ready: <list>". It reads only.
6. **The scripted scan (`views/scan.js`, `D.views.scan`).** The live Scan screen calls routes the demo server refuses, so the demo draws its own in the live screen's look (`share/js/views/scan.js`).
   - It lists the car's modules: PGM-FI, IMA, ABS/VSA, SRS, EPS, Body, Meter, A/C.
   - They go from "Scanning…" to "No codes" one after another over 12 s, with a progress bar.
   - It ends on "All systems normal · 8 modules · 0 codes".
7. **`tools/demo_carpic.py --mock M --out O`** cuts the car from mockup 3 (landscape: the car region around x 300–760, y 190–470 of the 1536×1024 image; confirm by looking) with an elliptical feathered alpha, so it sits on dark cards. It needs Pillow, which is on the box. Run it on the box against `doc/design/mockups/3.webp` (copied from the Mac) and write `share/assets/private/demo/crz-home.png`.
8. **Tests:**
   - `test/js/demo-tour.test.js` (fake clock, fake cue sender):
     - the steps run in order with their actions at the right offsets;
     - a touch pauses and Space resumes from the same step;
     - `1` jumps to step 1;
     - the tour ends back on Home.
   - `test/democheck_test.py`: a scratch tree with everything present gives "ready"; with a song truncated, it gives "not ready: songs".
- [ ] **Step 1:** Write the tests; they fail.
- [ ] **Step 2:** Implement `tour.js`, `menu.js`, `bar.js`, `boot.js` and `demo.css`; pass them.
- [ ] **Step 3:** Implement `democheck.py` and `demo_carpic.py`; pass them, and make the car picture on the box.
- [ ] **Step 4:** On the box, run the whole demo headless and muted:
  1. `OMACAR_DEMO_MUTE=1 bin/omacar demo on` from the mirror, with a scratch `HOME` that has the private tree linked in.
  2. Walk the tour via CDP.
  3. Screenshot every step at 1368×912 into `shots/`.
  4. Fix what looks wrong against the mockups.
  5. Name the shots in your report.
- [ ] **Step 5:** Commit, then run `./test/all.sh` and report its tail.

### Task 9: End to end, the tablet, and the backup video

**Files:**
- Create: `tools/demo_e2e.py`, `tools/demo_record.sh`
- Modify: `bin/omacar`: add `demo video` (plays `$HOME/Videos/omacar-demo-backup.mp4` full-screen with `mpv --fs`)

**Requirements:**
1. **`tools/demo_e2e.py`:**
   - It starts the demo (muted) and drives the whole tour through DevTools, as `test/js_test.py` does, at 1368×912, 912×1368 and 1920×1080.
   - It screenshots every step and fails on any console error or any failed request except the known 403s the page never makes.
   - It checks the silo afterwards: the real state tree's hashes before and after.
   - It is a tool, not in `all.sh`.
2. **`tools/demo_record.sh`** records the tour with sound on the box without anyone hearing or seeing it:
   1. a PipeWire null sink `omacar-demo-rec`;
   2. a Hyprland headless output (`hyprctl output create headless`) at 1920×1080;
   3. the demo browser on that output with `PULSE_SINK=omacar-demo-rec`, unmuted;
   4. `wf-recorder -o <headless output> --audio=omacar-demo-rec.monitor -f ~/Videos/omacar-demo-backup.mp4`;
   5. the tour started, stopped at its end;
   6. then everything created is removed.

   Check each tool exists first (`wf-recorder`, `pw-cli` or `pactl`), and say which is missing rather than improvising. It never changes the default sink or its volume.
3. **On the tablet** (after 07:30, with the owner's go-ahead for sound):
   1. `git -C ~/Projects/omacar fetch && git checkout` of the merged `demo/meetup` (the controller runs this: the live checkout is the owner's car app, so it must stay on a build containing `demo/2026-09-30` plus only this branch);
   2. `omacar assets push` from the box;
   3. `omacar demo check`;
   4. three muted tours via `demo_e2e.py` on the tablet (headless);
   5. then the silo check.
- [ ] **Step 1:** Write `demo_e2e.py`; run it on the box; fix or report failures.
- [ ] **Step 2:** Write `demo_record.sh`; record on the box; report the file's duration and size, and extract three stills to look at.
- [ ] **Step 3:** Commit and report. The tablet steps are the controller's.
