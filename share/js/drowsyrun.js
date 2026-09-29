// Drowsy mode, running. The gate (above 30 mph, connected, and never on
// simulated numbers), the cabin watch, the measures, the ladder, the one
// alert player and the logs. It keeps one state object that the screens
// draw (drowsyui.js); it draws nothing itself.
//
// Detection runs only while the car is moving, while an alert sounds, or
// while the settings sheet's preview is open, and only when the recorder has
// a live cabin picture. A parked car and a desk machine never load the
// engine at all.
//
// THE GATE (gateOf). `active` is connected, not simulated, and above
// 48.28 km/h (30 mph). `parked` is connected, not simulated, and 0 km/h:
// never a dropped link and never the stop clock. A dropped link is neither,
// so an adapter hiccup can neither start an alert nor clear one. Nor is a
// poll that failed, hung (given up at 5 s), or has had no answer for 2 s:
// that is no car data, never a stale "active". The stop
// clock (ladder.js createStopClock) is the one source of sinceStop and
// stoppedFor.
//
// FRAMES FEED THE MEASURES whenever the car is rolling: connected, not
// simulated, and moving (over 3 km/h) since its last long stop, at any
// speed, below the 30 mph gate included, and through a stop shorter than
// 10 s. So PERCLOS stays available through stop-and-go traffic. Nothing
// raises below the gate with nothing sounding (ladder.js, I4), and a
// below-gate window is not new evidence at the crossing either: on the step
// the gate opens with nothing sounding, the ladder latches both PERCLOS
// gates (Task 9 fix round 1, I1), so PERCLOS raises only once the window
// holds no frame from the crossing (about 60 s) or has recovered first. A
// closure, a yawn or a nod needs its own rising edge above the gate, as
// before. With an alert already sounding, below-gate evidence may escalate
// it, the owner-approved rule (M8). The measures restart, with a
// discontinuity, only after 10 s stopped (0 km/h, until it moves again),
// after a dropped link, or on a camera gap: the first frame fed after one is
// marked `restart` (drowsy.js), so a PERCLOS reading after moving off is
// never built from a long stop. A poll that failed is unknown, not a dropped
// link: it pauses feeding and restarts nothing by itself. The settings
// preview still shows the raw frame while parked (state.frame).
// (Refined before review, controller 2026-09-29.)
//
// THE CLOCK. Every step of the ladder, and the stop clock, run on one
// clock in seconds: the frame clock facewatch.js stamps each frame with
// (performance.now()). A frame's step is at the frame's own time, m.t. A
// step with no new frame (the half-second poll, a tap, a settings save) is
// at the same clock's now, with the last snapshot, which the ladder knows is
// stale because its m.t has not moved. Steps never run backwards.
//
// ONE CONFIG, ONE PLAYER. The settings are scaled for sensitivity once
// (ladder.js scaled) and the same object goes to the measures, the ladder
// and the player. The Level 2 rotation comes from alertplayer.js's own
// rotationFor and voiceInstalled -- the voice only when the clip the player
// will look for, for this name, is installed -- and goes to createLadder and
// every setConfig. Every cue goes to the page's one alert player,
// alertPlayer(), through drowsy.onCues.
//
// "I'M AWAKE" AND A STOPPED CAR ALWAYS CLEAR EVERYTHING: the ladder's level,
// every sound (a fade through the alert player), and a running "Test the
// alerts". A settings save goes through that same path first, then swaps
// the thresholds under the same ladder and the same baseline.

import { getJSON, postJSON } from "./camapi.js";
import { createMeasures } from "./drowsy.js";
import { createLadder, createStopClock, scaled } from "./ladder.js";
import { loadLandmarker, watchCabin } from "./facewatch.js";
import { alertPlayer, rotationFor, voiceInstalled } from "./alertplayer.js";
import { onAudio, auxLine, showAux } from "./audiostate.js";

// Re-exported, never redefined: the ladder's rotation must be the one the
// player plays, and drowsy mode's screen shows the same AUX warning Begin does.
export { rotationFor, voiceInstalled, showAux };

export const KPH_PER_MPH = 1.609344;

// The gate, from a live sample. The simulator's numbers never open it
// (lib/sim.py marks them `simulated`). A dropped link is neither active nor
// parked: an adapter hiccup must not clear an alert that is sounding.
export function gateOf(sample, cfg) {
  const s = sample || {};
  const simulated = !!s.simulated;
  const live = !!s.connected && !simulated;
  const v = live ? (s.values || {}).SPEED : null;
  const kph = typeof v === "number" && Number.isFinite(v) ? v : null;
  return { connected: live, simulated, kph, moving: kph !== null && kph > 0,
           active: kph !== null && kph > cfg.min_speed_mph * KPH_PER_MPH,
           parked: live && kph === 0 };
}

// The status chip. Exactly one of six texts (the spec's four, amended by the
// controller on 2026-09-29): "Watching", "Can't see you", "Paused · stopped",
// "Paused · no car data", "Stopped · face tracker error", "Off".
//   - "Off": drowsy mode is off, or the numbers are simulated, which it ignores.
//   - "Stopped · face tracker error" (fix round 1, I3): the tracker failed on
//     three frames in a row and has not come back. Drowsy mode is not
//     watching, and must not say "Can't see you" with a face in view.
//   - "Paused · no car data": a dropped link, an unreadable speed, or no
//     sample at all. The chip says only what is known.
//   - "Paused · stopped" (fix round 2, renamed from "… · parked"): a car that
//     said 0 km/h, or a creep of MOVING_KPH or less that has not ended a stop
//     (rolling false: after a long stop, or at app start), when the watch is
//     deliberately off. Ruling 1 counts such a creep as still stopped, and
//     "Can't see you" would blame the driver. Never "parked": drowsy mode
//     cannot know Park, and a car at a red light is not parked.
export function chipOf({ enabled, gate, measures, cabinLive, trackerFailed = false, rolling = true }) {
  if (!enabled || (gate && gate.simulated)) return "Off";
  if (trackerFailed) return "Stopped · face tracker error";
  if (!gate || !gate.connected || gate.kph === null || gate.kph === undefined) return "Paused · no car data";
  if (!gate.moving) return "Paused · stopped";
  if (!rolling && gate.kph <= MOVING_KPH) return "Paused · stopped";
  if (!cabinLive || !measures || measures.faceLost) return "Can't see you";
  return "Watching";
}

// "Test the alerts" may run, and keeps running, only while the car is
// connected and stopped. Moving off, a dropped link or simulated numbers stop it.
export function testMayRun(gate) {
  return !!(gate && gate.parked);
}

// THE TEST GATE: the seam for safety/parked-confirm. "Test the alerts" may
// run, and keeps running, only while testGate.may(gate, sample) says so.
// Today that is testMayRun: connected and stopped, never simulated. At the
// merge, drowsypark.js replaces it with Park read from the car's gear, and
// its ask() is the tap that asks for the gear. The default never asks.
let testGate = { may: (gate /* , sample */) => testMayRun(gate), ask: async () => false };
export function setTestGate(g) {
  testGate = { ...testGate, ...g };
  return testGate;
}

// The face tracker: loaded the first time a cabin picture is worth
// watching, and kept. A failed load is forgotten, so a later try starts over.
// drop() forgets a tracker that failed on frames (Task 9 fix round 1, I3):
// the next start loads a fresh one, on the CPU, since a tracker that loads
// on the GPU and then fails on every frame (a lost GPU context) would only
// fail again there. `load`, `watch` and `options` are for the tests.
export function createTracker({ load = loadLandmarker, watch = watchCabin, options = {} } = {}) {
  let lm = null, delegate = "GPU";
  return {
    async start({ canvas, onFrame, onError }) {
      if (!lm) lm = load(delegate);
      let l;
      try { l = await lm; } catch (e) { lm = null; throw e; }
      return watch({ ...options, landmarker: l, canvas, onFrame, onError });
    },
    drop() {
      const old = lm;
      lm = null;
      delegate = "CPU";
      if (old) old.then((l) => { if (l && typeof l.close === "function") l.close(); }).catch(() => {});
    },
  };
}
const pageTracker = createTracker();

// After the tracker has stopped on repeated failures, how long before a
// fresh one is loaded.
export const TRACKER_RETRY_SECS = 5;

// What a step reads when no frame has ever been fed.
const NO_SNAPSHOT = Object.freeze({ face: false, faceLost: true });

// Rolling and stopped, for the measures (see the header).
export const MOVING_KPH = 3;
export const STOP_RESTART_SECS = 10;

// The /api/live poll (controller, 2026-09-29). A request is given up after
// LIVE_TIMEOUT_MS, and a gate with no answer for more than LIVE_STALE_SECS
// is no car data: never a stale "active" that could start an alert, never a
// stale "parked" that could clear one. An alert already sounding goes on
// until "I'm awake", as for a dropped link.
export const LIVE_TIMEOUT_MS = 5000;
export const LIVE_STALE_SECS = 2;
// /api/cams misses in a row before the cabin picture counts as gone.
export const CAMS_MISSES = 2;

// The engine. The page has one, `drowsy`, below; a test builds its own with
// every outside thing handed in: the clock, the server, the player, the
// watcher, the timers. Nothing in here reads a clock of its own.
export function createDrowsy(opts = {}) {
  const d = {
    clock: () => performance.now() / 1000,     // the frame clock (facewatch.js)
    wall: () => Date.now() / 1000,              // for the records, never for the ladder
    hour: () => new Date().getHours(),
    getJSON, postJSON,
    player: alertPlayer,                        // the page's one alert player
    voiceInstalled,
    onAudio,
    tracker: pageTracker,
    later: (fn, ms) => setTimeout(fn, ms),
    cancel: (id) => clearTimeout(id),
    every: (fn, ms) => setInterval(fn, ms),
    ...opts,
  };

  const listeners = new Set();
  let cfg = null, measures = null, ladder = null, stops = null;
  // The stop clock reads stop.still_secs through this object, so a hand
  // edit that reaches the page with a reload is used without a new clock.
  const stopCfg = { stop: { still_secs: 300 } };
  let gate = null, lastSample = null, gateAt = null, cabinLive = false, aux = "";
  let watcher = null, watchGen = 0, starting = null, retryAt = -Infinity, watchSince = null;
  let lastT = -Infinity, lastSnap = null, frame = null, lastFaceT = null;
  // rolling: moving (over MOVING_KPH) since the last long stop or dropped
  // link. stopSince: when the car last read 0 km/h, until it moves again.
  // restartOwed: the next frame fed starts the measures over.
  let rolling = false, stopSince = null, restartOwed = true;
  let logBuf = [], lastLogT = -Infinity;
  let testing = null;
  let started = false, cfgRetryAt = -Infinity, liveBusy = false, camsBusy = false, camsMisses = 0;
  // st.error is the tracker's trouble, else the settings'. A load failure
  // clears once a tracker starts; a frame failure once a frame goes through.
  let settingsError = null, trackerError = null, trackerLoadFailed = false, trackerFailed = false;

  const st = {
    chip: "Off", level: 0, trigger: null, banner: false, t: null,
    measures: null, frame: null, gate: null, cfg: null, rotation: null,
    cabinLive: false, testing: false, testLevel: 0, error: null, aux: "",
  };

  function faceLost() {
    if (!watcher || !cfg) return true;
    const since = lastFaceT !== null ? lastFaceT : watchSince;
    return d.clock() - since > (cfg.face_lost_secs ?? 5);
  }

  function publish() {
    st.gate = gate;
    st.cabinLive = cabinLive;
    st.measures = lastSnap;
    st.frame = frame;
    st.testing = !!testing;
    st.testLevel = testing ? testing.level : 0;
    st.aux = aux;
    st.error = trackerError || settingsError;
    st.chip = chipOf({ enabled: !!(cfg && cfg.enabled), gate, cabinLive, measures: { faceLost: faceLost() },
                       trackerFailed, rolling: rolling && !stoppedLong() });
    for (const fn of listeners) { try { fn(st); } catch (e) { console.error(e); } }
  }

  // Every cue, the ladder's and the test's, goes out here: to engine.onCues,
  // which is the page's one alert player unless a caller replaces it.
  function deliver(cues, out) {
    if (!cues.length || !engine.onCues) return;
    try { engine.onCues(cues, out); } catch (e) { console.warn("drowsy sound:", e); }
  }
  function play(cues, out) {
    const p = d.player().play(cues, out);
    if (p && typeof p.catch === "function") p.catch((e) => console.warn("drowsy sound:", e));
  }

  async function fetchConfig() {
    const next = await d.getJSON("/api/drowsy");
    if (!next || typeof next !== "object") throw new Error("no settings");
    let installed = false;
    try { installed = !!(await d.voiceInstalled(next.name)); } catch { installed = false; }
    return { next, installed };
  }

  // New settings: scaled once, and that one object goes to the measures,
  // the ladder and the player. The measures and the ladder keep their
  // memory (setConfig, never a new instance): the baseline, the windows,
  // the level, the evidence.
  function apply({ next, installed }) {
    const c = scaled(next, next.sensitivity);
    const rota = rotationFor(next.sounds, installed);
    cfg = next;
    if (measures) measures.setConfig(c); else measures = createMeasures(c);
    if (ladder) ladder.setConfig(c, rota); else ladder = createLadder(c, rota);
    stopCfg.stop = c.stop;
    if (!stops) stops = createStopClock(stopCfg);
    const p = d.player();
    p.setConfig(c);
    p.setName(() => (cfg && cfg.name) || "");
    gate = gateOf(lastSample, cfg);
    st.cfg = next;
    st.rotation = rota;
    settingsError = null;
    syncWatch();
    publish();
  }

  async function loadConfig() {
    try { apply(await fetchConfig()); } catch { settingsError = "No settings from the server"; publish(); }
  }

  function logRow(out, m) {
    const s = m || {};
    return { t: Math.round(d.wall() * 10) / 10, mt: s.t ?? null, kph: gate ? gate.kph : null,
             active: !!(gate && gate.active), fed: feeding(), face: !!s.face, blink: s.blink ?? null,
             closed: !!s.closed, closedFor: s.closedFor ?? 0, perclos: s.perclos ?? null, yawns: s.yawns ?? 0,
             nods: s.nods ?? 0, pitch: s.pitch ?? null, baseline: s.baseline ?? null, level: out.level };
  }

  function logEvent(out, m) {
    const s = m || {};
    const body = {
      t: d.wall(), level: out.raised.level, trigger: out.raised.trigger, speed_kph: gate ? gate.kph : null,
      measures: { perclos: s.perclos ?? null, closedFor: s.closedFor ?? 0, yawns: s.yawns ?? 0, nods: s.nods ?? 0,
                  blink: s.blink ?? null, baseline: s.baseline ?? null, faceLost: !!s.faceLost },
    };
    // The alert still sounded; a lost record is the only loss.
    Promise.resolve().then(() => d.postJSON("/api/drowsy/event", body)).catch(() => {});
  }

  // One step of the ladder, at `t` on the frame clock, with snapshot `m`.
  // A gate is only as good as its last answer: with none for more than
  // LIVE_STALE_SECS (a poll hung, or failing), it is no car data. Rolling is
  // left alone: not knowing is not a dropped link, and restarts nothing.
  function freshen() {
    if (!cfg || gateAt === null || d.clock() - gateAt <= LIVE_STALE_SECS) return;
    if (gate && !gate.connected && !gate.simulated && lastSample === null) return;
    lastSample = null;
    gate = gateOf(null, cfg);
    if (testing && !testGate.may(gate, null)) stopTest();
  }

  function step(t, m, tap = false) {
    if (!cfg || !ladder) return null;
    freshen();
    t = Math.max(lastT, t);
    lastT = t;
    const sc = stops.feed(t, gate ? gate.kph : null, !!(gate && gate.connected));
    const snap = m || NO_SNAPSHOT;
    const out = ladder.step({
      t, m: snap, active: !!(cfg.enabled && gate && gate.active), parked: !!(gate && gate.parked),
      sinceStop: sc.sinceStop, stoppedFor: sc.stoppedFor, hour: d.hour(), tap,
    });
    st.t = t;
    st.level = out.level;
    st.trigger = out.trigger;
    st.banner = out.banner;
    if (out.raised) logEvent(out, snap);
    deliver(out.cues, out);
    if (gate && gate.moving && t - lastLogT >= 1) { lastLogT = t; logBuf.push(logRow(out, m)); }
    publish();
    return out;
  }

  // A step with no new frame: the clock's now, the last snapshot.
  const tick = (tap = false) => step(d.clock(), lastSnap, tap);

  // Rolling and stopped, from each answered poll (see the header). A known
  // "no car" -- the link down, or the simulator -- ends rolling and owes a
  // restart. A poll that failed changes nothing: it is not known to be either.
  function track(answered) {
    if (!gate || !gate.connected) {
      if (answered) { rolling = false; stopSince = null; restartOwed = true; }
      return;
    }
    if (gate.kph === null) return;
    if (gate.kph > MOVING_KPH) { rolling = true; stopSince = null; return; }
    if (gate.kph === 0 && stopSince === null) stopSince = d.clock();
    if (rolling && stoppedLong()) { rolling = false; restartOwed = true; }
  }
  function stoppedLong() {
    return stopSince !== null && d.clock() - stopSince >= STOP_RESTART_SECS;
  }

  // Whether a frame may feed the measures now (see the header).
  function feeding() {
    return !!(cfg && cfg.enabled && ladder && gate && gate.connected && gate.kph !== null
              && rolling && !stoppedLong());
  }

  function onFrame(f) {
    if (!cfg || !ladder || !f) return;
    freshen();
    if (trackerError && !trackerLoadFailed) { trackerError = null; trackerFailed = false; }
    frame = f;
    if (f.face) lastFaceT = f.t;
    if (!feeding()) { publish(); return; }
    const prev = measures.snapshot;
    const snap = measures.feed({ t: f.t, face: !!f.face, blink: f.blink, jaw: f.jaw, pitch: f.pitch,
                                 gated: !!gate.active, restart: restartOwed });
    // A duplicate: the measures ignored it, and so does the ladder. A
    // restart still owed stays owed.
    if (snap === prev) { publish(); return; }
    restartOwed = false;
    lastSnap = snap;
    step(snap.t, snap);
  }

  async function flushLog() {
    if (!logBuf.length) return;
    const rows = logBuf;
    logBuf = [];
    try { await d.postJSON("/api/drowsy/log", { rows }); } catch { /* a lost ten seconds of measures */ }
  }

  // The cabin watch runs while there is something to watch for: moving, an
  // alert sounding, or the preview open; drowsy mode on; a live cabin picture.
  function wanted() {
    return !!(cfg && cfg.enabled && cabinLive
              && ((rolling && !stoppedLong()) || engine.preview || (ladder && ladder.level > 0)));
  }
  function stopWatch() {
    watchGen++;
    if (watcher) watcher.stop();
    watcher = null;
    restartOwed = true;               // a camera restart: a gap, whatever its length
    frame = null;
    lastFaceT = null;
  }
  // A frame the tracker (or the measures or ladder behind onFrame) threw on
  // (Task 9 fix round 1, I3). Each one is shown; the third in a row has
  // already stopped the watch (facewatch.js), so it is forgotten here, the
  // tracker is dropped, and a fresh one is loaded TRACKER_RETRY_SECS later.
  // Until a frame goes through again the chip says drowsy mode has stopped.
  function trackerTrouble(e, info) {
    const why = (e && e.message) || String(e);
    if (info && info.fatal) {
      trackerFailed = true;
      trackerError = `The face tracker stopped after ${info.consecutive} failed frames (${why}); loading it again`;
      watchGen++;
      watcher = null;
      restartOwed = true;
      frame = null;
      lastFaceT = null;
      d.tracker.drop();
      retryAt = d.clock() + TRACKER_RETRY_SECS;
    } else {
      trackerError = "The face tracker failed on a frame: " + why;
    }
    publish();
  }

  function syncWatch() {
    const want = wanted();
    if (want && !watcher && !starting && d.clock() >= retryAt) {
      const gen = ++watchGen;
      starting = (async () => {
        try {
          const w = await d.tracker.start({
            canvas: engine.canvas,
            onFrame: (f) => { if (gen === watchGen) onFrame(f); },
            onError: (e, info) => { if (gen === watchGen) trackerTrouble(e, info); },
          });
          if (gen !== watchGen || !wanted()) { w.stop(); return; }
          watcher = w;
          watchSince = d.clock();
          lastFaceT = null;
          if (trackerLoadFailed) { trackerLoadFailed = false; trackerError = null; }
        } catch (e) {
          // A minute before trying again: a failed load is megabytes, and
          // this is asked twice a second.
          retryAt = d.clock() + 60;
          trackerLoadFailed = true;
          trackerError = "The face tracker did not load: " + ((e && e.message) || e);
        } finally { starting = null; publish(); }
      })();
    }
    if (!want && (watcher || starting)) stopWatch();
  }

  function stopTest() {
    if (!testing) return;
    for (const id of testing.timers) d.cancel(id);
    testing = null;
    deliver([{ kind: "fade" }], { level: 0 });
    publish();
  }

  // "I'm awake": a running test stops, and the ladder is stepped with the
  // tap, which clears its level and fades every sound.
  function awake() {
    stopTest();
    tick(true);
  }

  const engine = {
    state: st,
    canvas: opts.canvas !== undefined ? opts.canvas
      : (typeof document !== "undefined" ? document.createElement("canvas") : null),
    preview: false,
    // Where every cue goes: the page's one alert player. Replacing it must
    // still reach that one player (alertness.js); never a second one.
    onCues: play,
    player: () => d.player(),
    on(fn) { listeners.add(fn); fn(st); return () => listeners.delete(fn); },
    tap() { awake(); },
    // New settings from the server (after a save). Nothing sounding
    // survives a settings change: it is cleared through the same path as
    // "I'm awake" first, with the settings it was raised under, and only
    // then do the measures and the ladder take the new ones.
    async reload() {
      let got = null;
      try { got = await fetchConfig(); } catch { settingsError = "No settings from the server"; }
      stopTest();
      if (ladder && ladder.level > 0) tick(true);
      if (got) apply(got); else publish();
      return !!got;
    },
    // "Test the alerts": parked only (the test gate), shown as a card with
    // Stop (drowsyui.js: state.testing, state.testLevel, stopTest), through
    // the one player, and stopped by itself if the gate says no any more.
    canTest() { return !!(cfg && testGate.may(gate, lastSample)) && !testing; },
    canAsk() { return !!(gate && gate.parked) && !engine.canTest() && !testing; },
    async askTest() { try { return !!(await testGate.ask()); } catch { return false; } },
    test() {
      if (!engine.canTest()) return false;
      const run = { timers: [], level: 0 };
      testing = run;
      const say = (cues, level, secs) => run.timers.push(d.later(() => {
        if (testing !== run) return;
        run.level = level;
        deliver(cues, { level });
        publish();
      }, secs * 1000));
      say([{ kind: "chime" }, { kind: "voice", clip: "l1" }], 1, 0);
      say([{ kind: "fade" }], 0, 8);
      say([{ kind: "duck" }, { kind: "bark" }], 2, 11);
      say([{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], 3, 19);
      say([{ kind: "fade" }], 0, 27);
      run.timers.push(d.later(() => { if (testing === run) { testing = null; publish(); } }, 31000));
      publish();
      return true;
    },
    stopTest() { stopTest(); },
    async pollLive() {
      // One poll out at a time. While one is out the clock still steps (the
      // ladder's repeats) and the gate goes stale; the request itself is
      // given up after LIVE_TIMEOUT_MS.
      if (liveBusy) { tick(false); return; }
      liveBusy = true;
      try {
        let sample = null, answered = false;
        const ctl = new AbortController();
        const bail = d.later(() => ctl.abort(), LIVE_TIMEOUT_MS);
        try { sample = await d.getJSON("/api/live", { signal: ctl.signal }); answered = true; }
        catch { /* no answer, or given up: unknown */ } finally { d.cancel(bail); }
        lastSample = sample;
        gateAt = d.clock();
        if (!cfg && d.clock() >= cfgRetryAt) { cfgRetryAt = d.clock() + 10; await loadConfig(); }
        if (cfg) { gate = gateOf(sample, cfg); track(answered); }
        if (testing && !testGate.may(gate, sample)) stopTest();
        syncWatch();
        tick(false);
      } finally { liveBusy = false; }
    },
    // The recorder's overview, every 5 s. An answer is acted on at once. A
    // request that fails, or hangs past LIVE_TIMEOUT_MS, is a miss, and it
    // takes CAMS_MISSES in a row to call the cabin picture gone: one server
    // hiccup must not stop the watch, which restarts the measures and
    // empties PERCLOS (Task 9 fix round 1).
    async pollCams() {
      if (camsBusy) return;
      camsBusy = true;
      const ctl = new AbortController();
      const bail = d.later(() => ctl.abort(), LIVE_TIMEOUT_MS);
      try {
        const ov = await d.getJSON("/api/cams", { signal: ctl.signal });
        camsMisses = 0;
        cabinLive = !!(ov && ov.running && ov.roles && ov.roles.cabin && ov.roles.cabin.live);
      } catch {
        camsMisses++;
        if (camsMisses >= CAMS_MISSES) cabinLive = false;
      } finally { d.cancel(bail); camsBusy = false; }
      syncWatch();
      publish();
    },
    flushLog,
    // Settles once a cabin watch that is starting has started (tests).
    idle() { return starting || Promise.resolve(); },
    async start() {
      if (started) return;
      started = true;
      await loadConfig();
      await Promise.all([engine.pollLive(), engine.pollCams()]);
      d.every(() => engine.pollLive(), 500);
      d.every(() => engine.pollCams(), 5000);
      d.every(() => flushLog(), 10000);
    },
  };

  d.onAudio((a) => {
    const s = auxLine(a);
    if (s !== aux) { aux = s; publish(); }
  });
  return engine;
}

// The page's drowsy mode, and its start (alertness.js).
export const drowsy = createDrowsy();
export function startDrowsy() { return drowsy.start(); }
