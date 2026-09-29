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
  ["new thresholds keep the baseline: no minute blind after a settings change", async () => {
    const m = await calibrated();
    const c = await CFG();
    c.eyes.closed_over_baseline = 0.28;
    m.setConfig(c);
    const s = run(m, 61, 0.1);
    eq([s.calibrated, s.baseline, +s.threshold.toFixed(2)], [true, 0.1, 0.38]);
  }],
  ["a 30 s forward jump right after eyes-closed frames does not inflate closedFor", async () => {
    const m = await calibrated();
    const pre = run(m, 61, 1.2, () => ({ blink: 0.9 }));
    ok(pre.closedFor > 1, `pre-gap closedFor ${pre.closedFor}`);
    const after = m.feed({ t: pre.t + 30, face: true, blink: 0.9, jaw: 0.1, pitch: 0, gated: true });
    eq(after.discontinuity, true);
    ok(after.closedFor <= pre.closedFor, `closedFor after the jump (${after.closedFor}) exceeds the pre-gap value (${pre.closedFor})`);
    ok(after.closedFor <= 1, `closedFor after the jump (${after.closedFor}) exceeds 1 s`);
  }],
  ["a backward jump leaves gatedSecs and the baseline unchanged", async () => {
    const m = createMeasures(await CFG());
    const before = run(m, 0, 58.9);
    eq(before.calibrated, false);
    const jump = m.feed({ t: 30, face: true, blink: 0.1, jaw: 0.1, pitch: 0, gated: true });
    eq([jump.discontinuity, jump.calibrated], [true, false],
       "a backward jump must not itself complete calibration");
    const s = run(m, 30.1, 1.3);
    eq([s.calibrated, s.baseline], [true, 0.1],
       "the real gated time from before the jump still counts, so just over 1 s more finishes it");
  }],
  ["after a gap, measures resume normally", async () => {
    const m = await calibrated();
    const jump = m.feed({ t: 60.9 + 45, face: true, blink: 0.9, jaw: 0.1, pitch: 0, gated: true });
    eq([jump.discontinuity, jump.closedFor], [true, 0],
       "the frame right after a gap starts its own closure fresh");
    const s = run(m, jump.t + 0.1, 1.5, () => ({ blink: 0.9 }));
    ok(near(s.closedFor, 1.5), `closedFor after resuming ${s.closedFor}`);
    eq(s.perclos, null, "PERCLOS's window does not yet hold 30 s since the gap");
  }],
  ["a duplicate frame timestamp is ignored, not a discontinuity", async () => {
    const m = await calibrated();
    let last = null;
    for (let i = 0; i <= 11; i++) {          // t = 61.0 .. 62.1, eyes closed, each fed twice
      const t = +(61 + i / 10).toFixed(3);
      const frame = { t, face: true, blink: 0.9, jaw: 0.1, pitch: 0, gated: true };
      const first = m.feed(frame);
      const dup = m.feed(frame);
      ok(dup === first, `the duplicate at t=${t} did not return the very same, unchanged snapshot`);
      last = first;
    }
    ok(last.closedFor >= 1, `closedFor ${last.closedFor} never reached 1 s despite every frame being fed twice`);
    const jump = m.feed({ t: 40, face: true, blink: 0.1, jaw: 0.1, pitch: 0, gated: true });
    eq(jump.discontinuity, true, "a real backward jump still resets");
  }],
];
