import { eq, ok } from "./assert.js";
import {
  MUSIC_DB, ALERT_MAX_DB, dbToGain, gainToDb, headroomDb, musicIn, alertIn,
  audioContext, schedule,
} from "../js/audiobus.js";
import { auxLine } from "../js/audiostate.js";
import { levelAt } from "../js/audiobus.js";
import { rampPlan } from "../js/ramps.js";
// Read through the module, so the voice's tests below run against a stage
// without voiceIn() too, and fail there for what they check.
import * as AB from "../js/audiobus.js";

// ---- a stage of its own, rendered offline -----------------------------------
// audiobus.js builds ONE context for the page, on first use. A second copy of
// the module (the same file under another URL is another module) builds its
// own, and building it while AudioContext hands out an OfflineAudioContext
// makes that the stage's context: the real graph, limiter and all, rendered
// into memory. Nothing here has a speaker. The render is polled rather than
// awaited, for the reason sounds.test.js gives.
const channel = new MessageChannel();
let woken = [];
channel.port1.onmessage = () => { const w = woken; woken = []; for (const f of w) f(); };
const yieldOnce = () => new Promise((r) => { woken.push(r); channel.port2.postMessage(0); });
let stages = 0;
async function offlineStage(secs, rate = 16000) {
  const ctx = new OfflineAudioContext(1, Math.ceil(secs * rate), rate);
  const real = window.AudioContext;
  window.AudioContext = function OfflineStage() { return ctx; };
  try {
    const stage = await import(`../js/audiobus.js?offline-stage-${++stages}`);
    stage.musicIn();
    if (stage.audioContext() !== ctx) throw new Error("the copy did not build on the offline context");
    return { stage, ctx, rate };
  } finally { window.AudioContext = real; }
}
async function rendered(ctx) {
  let buf = null, err = null;
  ctx.startRendering().then((b) => { buf = b; }, (e) => { err = e; });
  for (let i = 0; !buf && !err && i < 500000; i++) await yieldOnce();
  if (err) throw err;
  if (!buf) throw new Error("the offline render never finished");
  return buf.getChannelData(0);
}
// A constant signal at `db` dBFS into `node`, from `at`.
function flatInto(ctx, node, db, at = 0) {
  const s = ctx.createConstantSource();
  s.offset.value = dbToGain(db);
  s.connect(node);
  s.start(at);
  return s;
}
const peakOf = (data, rate, from, to) => {
  let m = 0;
  for (let i = Math.floor(from * rate); i < Math.min(data.length, Math.ceil(to * rate)); i++) m = Math.max(m, Math.abs(data[i]));
  return gainToDb(m);
};

export default [
  ["music sits 12 dB below full scale", () => eq(MUSIC_DB, -12)],
  ["alerts may use all of it", () => eq(ALERT_MAX_DB, 0)],
  ["so an alert always has 12 dB over music", () => eq(headroomDb(), 12)],
  // The real graph, not arithmetic: this builds the page's own AudioContext.
  ["the stage is built that way: the music bus at -12 dB", () => eq(musicIn().gain.value.toFixed(4), "0.2512")],
  ["and the alert gate closed, so nothing plays on it until an alert opens it", () => eq(alertIn().gain.value, 0)],
  ["-12 dB is a quarter of the amplitude", () => eq(dbToGain(-12).toFixed(4), "0.2512")],
  ["gain and dB round-trip", () => ok(Math.abs(gainToDb(dbToGain(-7.5)) + 7.5) < 1e-9, "round trip")],
  ["silence is zero gain, not a tiny number", () => eq([dbToGain(-Infinity), dbToGain(-200)], [0, 0])],
  ["a Level 1 swell leaves 6 dB, a Level 2 duck 24", () => eq([headroomDb(-6), headroomDb(-24)], [6, 24])],
  ["AUX disconnected only when the port is known to be the speakers", () =>
    eq([auxLine({ aux: false }), auxLine({ aux: true }), auxLine({ aux: null }), auxLine(null)],
       ["AUX disconnected — sound is on the tablet's speakers", "", "", ""])],
  // Fix round 1: without cancelAndHoldAtTime (Firefox has no such method),
  // schedule()'s old fallback cancelled the in-flight ramp and rewrote the
  // gain to its value read right now -- a step of a few dB, on the one bus
  // this whole file exists to keep stepless. Forcing the fallback here (by
  // deleting cancelAndHoldAtTime off the real gain param, not a mock) and
  // spying on every method that could touch the value proves the fix: none
  // of them run at all, so the bus is left exactly where it was.
  ["without cancelAndHoldAtTime, schedule() refuses to reschedule rather than stepping the bus", () => {
    const g = musicIn().gain;
    const realHold = g.cancelAndHoldAtTime;
    const realCancel = g.cancelScheduledValues.bind(g);
    const realSetAt = g.setValueAtTime.bind(g);
    const realRamp = g.linearRampToValueAtTime.bind(g);
    let cancelled = false, stepped = false, ramped = false;
    g.cancelAndHoldAtTime = undefined;   // force the no-cancelAndHoldAtTime path
    g.cancelScheduledValues = (...a) => { cancelled = true; return realCancel(...a); };
    g.setValueAtTime = (...a) => { stepped = true; return realSetAt(...a); };
    g.linearRampToValueAtTime = (...a) => { ramped = true; return realRamp(...a); };
    try {
      schedule("music", [[1, -6]], audioContext().currentTime, false);
    } finally {
      g.cancelAndHoldAtTime = realHold;
      g.cancelScheduledValues = realCancel;
      g.setValueAtTime = realSetAt;
      g.linearRampToValueAtTime = realRamp;
    }
    ok(!cancelled && !stepped && !ramped,
       `schedule() without cancelAndHoldAtTime must not touch the gain at all `
       + `(cancelScheduledValues=${cancelled} setValueAtTime=${stepped} linearRampToValueAtTime=${ramped})`);
  }],
  // Task 7: every plan starts where the bus will really be. The real music
  // bus, a quarter of an hour ahead on its own clock, so nothing moves now.
  ["the stage knows where its own plans have a bus, mid-ramp, so the next plan starts there", () => {
    const t0 = audioContext().currentTime + 900;
    ok(schedule("music", rampPlan(1, MUSIC_DB, MUSIC_DB + 6).points, t0), "a swell, scheduled");
    ok(schedule("music", rampPlan(1, MUSIC_DB + 6, MUSIC_DB).points, t0 + 10, true), "and its settle after it");
    const mid = levelAt("music", t0 + 5);
    eq(+mid.toFixed(6), MUSIC_DB + 3, "halfway up the swell");
    ok(schedule("music", rampPlan(2, mid, MUSIC_DB - 12).points, t0 + 5), "a duck from exactly there");
    eq([t0 + 5, t0 + 6, t0 + 60].map((t) => +levelAt("music", t).toFixed(6)), [MUSIC_DB + 3, MUSIC_DB - 12, MUSIC_DB - 12],
       "held where the swell was, ducked, and the settle cancelled");
    ok(schedule("music", rampPlan(0, levelAt("music", t0 + 7), MUSIC_DB).points, t0 + 7), "and back");
    eq(+levelAt("music", t0 + 20).toFixed(6), MUSIC_DB);
  }],

  // ---- hardening B: the demo's voice joins the stage ---------------------------------
  // voiceIn() is the demo's (demo/js/voice.js). The live app never calls it,
  // and the page's stage is built exactly as before: the tests above hold that.
  ["voiceIn() is one gain node for the page, at unity", () => {
    ok(typeof AB.voiceIn === "function", "audiobus.js exports voiceIn()");
    const v = AB.voiceIn();
    ok(v === AB.voiceIn(), "the same node every time");
    eq(v.gain.value, 1, "unity: a line plays at its own level");
    ok(v.context === audioContext(), "on the page's own context");
  }],
  ["a line into voiceIn() reaches the output through the limiter, at its own level", async () => {
    const { stage, ctx, rate } = await offlineStage(1.2);
    ok(typeof stage.voiceIn === "function", "audiobus.js exports voiceIn()");
    // -20 dBFS is far under the threshold: it comes out at its own level (and
    // the limiter's constant makeup, measured beside it, see below). 0 dBFS is
    // over it: the limiter takes it down.
    flatInto(ctx, stage.voiceIn(), -20, 0.1).stop(0.6);
    flatInto(ctx, stage.voiceIn(), 0, 0.6);
    const out = await rendered(ctx);
    const quiet = peakOf(out, rate, 0.3, 0.6), loud = peakOf(out, rate, 0.9, 1.2);
    ok(Math.abs(quiet - -20) < 1, `-20 dBFS in, ${quiet.toFixed(2)} dBFS out: connected, at unity`);
    ok(loud < -0.2, `0 dBFS in, ${loud.toFixed(2)} dBFS out: limited`);
  }],
];
