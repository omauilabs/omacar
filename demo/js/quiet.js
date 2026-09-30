// The demo going quiet (hardening B, doc/design/2026-09-30-meetup-demo-hardening.md):
// the music fades out rather than cuts, when `omacar demo off` asks, and
// before the tour's reset starts the drive over.
//
// `demo off` POSTs the `quiet` cue, and the world says so in live.json for
// 10 s as `demo.quiet_at` (the cue's own epoch). On seeing a NEW one, the page
// fades the music bus to silence and pauses the radio. The world saying the
// same one for 10 s is still one quiet. Only a quiet_at in the FIRST sample
// the page sees is held against the page's load time: at or before it, it is
// somebody else's (a reload just after an exit). After that the wall clock
// plays no part, so a clock that steps back cannot disable the fade.
//
// THE FADE KEEPS THE STAGE'S RULE: no 100 ms moves the music more than 3 dB
// (ramps.js). It takes at least FADE_MS, and a span too big for that is
// stretched to the rule, never stepped: from MUSIC_DB (-12) to silence
// (-48) is 36 dB, which is 1.2 s; from a bus an alert has ducked (-24), 0.8 s.
// The radio pauses only once the bus is at silence. Anything that brings the
// music back meanwhile (a line voice.js ducked for, ending) is faded again
// first, so the pause is never heard. Then the bus goes back to MUSIC_DB under
// the paused radio, gently too, so the next play is at its level.
//
//   createQuiet({ radio, stage, later, loadedAt }) -> { fadeOut(ms), feed(demo) }
//   register(D, deps)        follows the store's samples (and /api/live when it has none)
//   fadeOut(ms)              the page's own, for the tour's reset (tour.js createReset)
//   quietsBegun()            how many fades have begun on the page, for voice.js

import { store, api } from "../../js/core.js";
import { audioContext, schedule, levelAt, MUSIC_DB } from "../../js/audiobus.js";
import { glidePlan, SILENCE_DB } from "../../js/ramps.js";
import { getRadio } from "./radio.js";

export const FADE_MS = 800;
// Plans go on the context's clock this far ahead, so no first point is
// already in the past when the audio thread reads it (alertplayer.js).
export const LEAD_SECS = 0.05;
// The pause waits this long past the fade's last point.
export const TAIL_MS = 50;
// How many times a fade is laid again because something raised the music
// under it, before the radio is paused anyway.
export const RETRIES = 3;
// With the store's live sample this stale (a screen without the fast clock),
// ask /api/live ourselves, this often. `demo off` waits only a moment.
export const POLL_STALE_SECS = 0.6;
export const POLL_EVERY_MS = 250;
// When this page began (epoch seconds, the world's machine's clock), held only
// against a quiet_at in the page's first sample.
export const LOADED_AT = Date.now() / 1000;

// Fades begun on the page. A line voice.js ducked for, ending while one it did
// not see begin is under way or done, leaves the music to it (voice.js).
let begun = 0;
export function quietsBegun() { return begun; }

// The page's own music bus.
const liveStage = {
  running: () => audioContext().state === "running",
  now: () => audioContext().currentTime,
  levelAt: (bus, t) => levelAt(bus, t),
  schedule: (bus, points, at) => schedule(bus, points, at),
};

export function createQuiet({
  radio = null, stage = liveStage, loadedAt = LOADED_AT, later = (fn, ms) => setTimeout(fn, ms),
} = {}) {
  let seen = false;        // the page's first sample has been seen
  let last = null;         // the last quiet_at seen
  let fading = null;
  const theRadio = () => radio || getRadio();

  // Lay a glide from where the bus will be at `at` to `to`. The time it ends,
  // or null if the stage refused it (a browser that cannot cancel a ramp
  // without a step: see audiobus.js schedule()).
  function glide(to, minSecs) {
    const at = stage.now() + LEAD_SECS;
    const from = stage.levelAt("music", at);
    const start = Number.isFinite(from) ? Math.max(SILENCE_DB, from) : SILENCE_DB;
    const plan = glidePlan(start, to, minSecs);
    return stage.schedule("music", plan.points, at) ? at + plan.secs : null;
  }

  function fadeOut(ms = FADE_MS) {
    if (fading) return fading;
    const r = theRadio();
    if (!r || !r.playing) return Promise.resolve(false);   // nothing sounding: nothing to fade
    // THE PROMISE IS KEPT BEFORE ANYTHING RUNS (fix round 1). A fade can
    // settle at once (a stage not running, a bus already silent); settled
    // inside its own constructor, it was then stored as `fading` for good, and
    // every later quiet returned it and did nothing. It is cleared where it
    // settles, and only if it is still the one in hand.
    let done = null;
    const p = new Promise((d) => { done = d; });
    fading = p;
    let tries = 0;
    const finish = (moved = true) => {
      try { r.pause(); } catch (e) { console.warn("demo quiet: the radio:", e); }
      // The bus back to its level under the paused radio, for the next play.
      if (moved) {
        try { glide(MUSIC_DB, ms / 1000); } catch (e) { console.warn("demo quiet: the music bus:", e); }
      }
      if (fading === p) fading = null;
      done(true);
    };
    const down = () => {
      let end;
      try {
        const lvl = stage.levelAt("music", stage.now() + LEAD_SECS);
        if (!(lvl > SILENCE_DB + 0.01) || tries++ >= RETRIES) { finish(); return; }
        end = glide(SILENCE_DB, ms / 1000);
      } catch (e) { console.warn("demo quiet: the fade:", e); end = null; }
      // A stage that would not move the bus: the pause is the only fade left.
      if (end === null) { finish(); return; }
      later(down, Math.max(0, end - stage.now()) * 1000 + TAIL_MS);
    };
    // A context that is not running (still waiting for its tap) is silent,
    // and its clock stands still, so a fade on it would never arrive.
    let still = false;
    try { still = !!stage.running && !stage.running(); } catch { still = false; }
    if (still) finish(false);
    else { begun++; down(); }
    return p;
  }

  // One sample of the demo world (live.json's `demo`). True if it asked for quiet.
  function feed(demo) {
    if (!demo || typeof demo !== "object") return false;
    const q = typeof demo.quiet_at === "number" && Number.isFinite(demo.quiet_at) ? demo.quiet_at : null;
    const first = !seen;
    seen = true;
    if (q === null || q === last) return false;
    last = q;
    if (first && q <= loadedAt) return false;
    Promise.resolve(fadeOut()).catch((e) => console.warn("demo quiet:", e));
    return true;
  }

  return { fadeOut, feed };
}

// The store's live samples, and /api/live when the store has none (drowsy.js
// follows the world the same way).
function follow(q, { store: s = store, api: a = api, every, clock }) {
  const timer = every || ((fn, ms) => setInterval(fn, ms));
  const now = clock || (() => performance.now() / 1000);
  let fedAt = -Infinity, out = false;
  s.on("live", () => { fedAt = now(); q.feed(s.live && s.live.demo); });
  timer(() => {
    if (out || now() - fedAt < POLL_STALE_SECS) return;
    out = true;
    Promise.resolve().then(() => a.live()).then((x) => q.feed(x && x.demo), () => {})
      .finally(() => { out = false; });
  }, POLL_EVERY_MS);
}

let active = null;

// `D` is the demo's door (boot.js); nothing of it is needed here. `deps` is
// createQuiet's, plus { store, api, every, clock } for the tests.
export function register(D, deps = {}) {
  const q = createQuiet(deps);
  active = q;
  follow(q, deps);
  return q;
}

export function fadeOut(ms = FADE_MS) {
  if (!active) active = createQuiet();
  return active.fadeOut(ms);
}
