import { eq, ok } from "./assert.js";
import { rampPlan, envelopePlan, releasePlan, worstStep, RAMP_SECS, SILENCE_DB } from "../js/ramps.js";
import { dbAt, releaseAt, planOnto } from "../js/ramps.js";

const throws = (fn) => { try { fn(); return false; } catch { return true; } };

export default [
  ["the spec's ramp times for the music", () =>
    eq(RAMP_SECS, { 0: { up: 3, down: 3 }, 1: { up: 10, down: 30 }, 2: { up: 1.5, down: 0.5 }, 3: { up: 3, down: 0.5 } })],
  ["Level 1: the radio rises 6 dB over 10 s", () => {
    const p = rampPlan(1, -12, -6);
    eq([p.secs, p.points[0], p.points[p.points.length - 1]], [10, [0, -12], [10, -6]]);
  }],
  ["and settles back over 30 s", () => eq(rampPlan(1, -6, -12).secs, 30)],
  ["Level 2: music ducks 12 dB over half a second", () => eq(rampPlan(2, -12, -24).secs, 0.5)],
  ["no music ramp moves more than 3 dB in any 100 ms, over any span", () => {
    for (const level of [0, 1, 2, 3]) {
      for (const [a, b] of [[-60, 0], [0, -60], [-12, -6], [-6, -12], [-12, -24], [-24, -12], [-120, 0]]) {
        ok(worstStep(rampPlan(level, a, b).points) <= 3 + 1e-6, `level ${level}, ${a} to ${b} dB`);
      }
    }
  }],
  ["every alert sound starts and ends at silence, -48 dBFS", () => {
    const e = envelopePlan(2, -3, 1.5);
    eq([SILENCE_DB, e.points[0], e.points[e.points.length - 1][1]], [-48, [0, -48], -48]);
  }],
  ["it rises over its level's time: 1.5 s at Levels 1 and 2, 3 s at Level 3", () =>
    eq([envelopePlan(1, -6, 0).rise, envelopePlan(2, -3, 0).rise, envelopePlan(3, 0, 0).rise], [1.5, 1.5, 3])],
  ["and falls back over 3 s", () => { const e = envelopePlan(2, -3, 1); eq(+(e.secs - e.rise - 1).toFixed(4), 3); }],
  ["no envelope, at any level, moves more than 3 dB in any 100 ms", () => {
    for (const [lv, db] of [[1, -9], [1, -6], [2, -3], [3, 0]]) {
      ok(worstStep(envelopePlan(lv, db, 1).points) <= 3 + 1e-6, `level ${lv} to ${db} dB`);
    }
  }],
  ["a held envelope has no end until it is let go", () => eq(envelopePlan(3, 0, Infinity).secs, Infinity)],
  ["a release from anywhere reaches silence in 3 s, without a step", () => {
    const r = releasePlan(-3);
    eq([r.secs, r.points[r.points.length - 1][1]], [3, -48]);
    ok(worstStep(r.points) <= 3 + 1e-6, "gentle");
  }],
  ["an unknown level, or silence as a number, is an error rather than a guess", () =>
    eq([throws(() => rampPlan(7, 0, -6)), throws(() => rampPlan(0, -Infinity, 0)), throws(() => envelopePlan(0, -6, 1))],
       [true, true, true])],

  // Task 7 additions: when a sound may be let go, and where a bus really is.
  ["a sound is let go at once where it stands still, and at its next point where it moves", () => {
    const env = envelopePlan(2, -3, 0.4).points.map(([t, d]) => [10 + t, d]);   // rise 10-11.5, hold to 11.9
    eq([releaseAt(env, 9), releaseAt(env, 10.53), releaseAt(env, 10.6), releaseAt(env, 11.7), releaseAt(env, 99)],
       [9, 10.6, 10.6, 11.7, 99]);
  }],
  ["a plan laid onto a bus holds it where it was at that moment, then runs", () => {
    const swell = planOnto([[0, -12]], 1, rampPlan(1, -12, -6).points);
    const settled = planOnto(swell, 11, rampPlan(1, -6, -12).points, true);
    eq([6, 11, 41].map((t) => +dbAt(settled, t).toFixed(6)), [-9, -6, -12]);
    const held = +dbAt(settled, 5).toFixed(6);
    const ducked = planOnto(settled, 5, rampPlan(2, held, -24).points);
    eq([+dbAt(ducked, 5).toFixed(6), dbAt(ducked, 41)], [held, -24]);
    ok(worstStep(ducked, 50) <= 3 + 1e-6, "the swell is cut where it stood and the duck ramps on from there");
  }],
  ["a plan that starts anywhere but the level held is a step, and is measured as one", () => {
    const swell = planOnto([[0, -12]], 0, rampPlan(1, -12, -6).points);
    const wrong = planOnto(swell, 5, rampPlan(2, -12, -24).points);   // from MUSIC_DB, not from the swell
    ok(worstStep(wrong, 10) > 3, `worst ${worstStep(wrong, 10).toFixed(2)} dB`);
  }],
];
