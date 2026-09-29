import { eq, ok } from "./assert.js";
import {
  MUSIC_DB, ALERT_MAX_DB, dbToGain, gainToDb, headroomDb, musicIn, alertIn,
  audioContext, schedule,
} from "../js/audiobus.js";
import { auxLine } from "../js/audiostate.js";
import { levelAt } from "../js/audiobus.js";
import { rampPlan } from "../js/ramps.js";

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
];
