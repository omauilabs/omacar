// The equalizer's arithmetic (share/js/eqmath.js): the response curve against
// the known RBJ values, the preamp that keeps the curve at or below 0 dB, and the
// mapping between a finger on the graph and a band's frequency and gain. The
// reference numbers below were also computed from lib/eq.py's Python copy of the
// same formulas (test/eq_test.py holds the other half), so the two cannot drift.
import { eq, ok } from "./assert.js";
import * as m from "../js/eqmath.js";

const near = (got, want, tol, what) => {
  if (Math.abs(got - want) > tol) throw new Error(`${what}: got ${got}, want ${want} +- ${tol}`);
};
const flat = () => [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000].map((f, i) => ({
  type: i === 0 ? "lowshelf" : i === 9 ? "highshelf" : "peaking", freq: f, gain: 0, q: 1, on: true }));
const with1 = (i, patch) => { const b = flat(); Object.assign(b[i], patch); return b; };

const VOCAL = (() => {
  const b = flat();
  Object.assign(b[2], { gain: -2 });
  Object.assign(b[3], { gain: -1.5 });
  Object.assign(b[5], { gain: 1.5, freq: 1500 });
  Object.assign(b[6], { gain: 3, freq: 3000 });
  Object.assign(b[7], { gain: 1.5, freq: 5000 });
  return b;
})();

export default [
  ["a peaking +6 dB at 1 kHz, Q 1 is +6.0 dB at 1 kHz", () =>
    near(m.bandDb({ type: "peaking", freq: 1000, gain: 6, q: 1 }, 1000), 6.0, 0.01, "dB at 1k")],
  ["...and about nothing at 20 Hz and at 20 kHz", () => {
    const b = { type: "peaking", freq: 1000, gain: 6, q: 1 };
    near(m.bandDb(b, 20), 0, 0.05, "20 Hz");
    near(m.bandDb(b, 20000), 0, 0.05, "20 kHz");
  }],
  ["a peaking cut of 6 dB is -6.0 dB at its centre", () =>
    near(m.bandDb({ type: "peaking", freq: 1000, gain: -6, q: 2 }, 1000), -6.0, 0.01, "dB")],
  ["a shelf is half its gain at its corner and the whole of it well past", () => {
    const lo = { type: "lowshelf", freq: 1000, gain: 6, q: 0.707 };
    near(m.bandDb(lo, 1000), 3.0, 0.01, "corner");
    near(m.bandDb(lo, 20), 6.0, 0.05, "plateau");
    near(m.bandDb(lo, 20000), 0, 0.05, "far side");
    const hi = { type: "highshelf", freq: 1000, gain: -6, q: 0.707 };
    near(m.bandDb(hi, 20000), -6.0, 0.1, "high plateau");
  }],
  ["a low cut at Q 0.707 is -3.01 dB at its corner and falls below it", () => {
    const c = { type: "highpass", freq: 1000, gain: 0, q: 0.707 };
    near(m.bandDb(c, 1000), -3.0116, 0.01, "corner");
    ok(m.bandDb(c, 100) < -30, "100 Hz is more than 30 dB down");
    near(m.bandDb(c, 20000), 0, 0.1, "passband");
  }],
  ["a high cut passes the lows and falls above its corner", () => {
    const c = { type: "lowpass", freq: 1000, gain: 0, q: 0.707 };
    near(m.bandDb(c, 50), 0, 0.01, "passband");
    ok(m.bandDb(c, 10000) < -30, "10 kHz is more than 30 dB down");
  }],
  ["a cut at Q 4 peaks at its corner, which is why the preamp looks at the whole curve", () =>
    near(m.bandDb({ type: "highpass", freq: 1000, gain: 0, q: 4 }, 1000), 12.0412, 0.01, "resonance")],
  ["same numbers as lib/eq.py: the Vocal clarity curve", () => {
    const want = [-0.4314, -1.6311, 2.4881, 4.1034, 0.8051];
    [50, 300, 1500, 3000, 9000].forEach((f, i) =>
      near(m.curveDb(VOCAL, [f])[0], want[i], 0.002, `${f} Hz`));
  }],
  ["a disabled band contributes nothing", () => {
    const b = with1(5, { gain: 9, on: false });
    eq(m.curveDb(b, [1000]).map((v) => Math.round(v * 1000)), [0]);
  }],
  ["flat needs no preamp", () => eq(m.autoPreamp(flat()), 0)],
  ["a +6 dB boost is met by a -6.0 dB preamp", () => eq(m.autoPreamp(with1(5, { gain: 6 })), -6)],
  ["a cut-only curve needs no preamp", () => eq(m.autoPreamp(with1(5, { gain: -9 })), 0)],
  ["Vocal clarity: peak 4.11 dB, preamp -4.2 (rounded away from zero)", () => {
    near(m.peakDb(VOCAL), 4.1099, 0.005, "peak");
    eq(m.autoPreamp(VOCAL), -4.2);
  }],
  ["the preamp always brings the maximum to 0 dB or below, for any gains", () => {
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let n = 0; n < 40; n++) {
      const b = flat().map((x) => Object.assign(x, {
        gain: Math.round((rnd() * 24 - 12) * 2) / 2, q: 0.3 + rnd() * 7.7,
        freq: 20 * Math.pow(1000, rnd()) }));
      const after = m.peakDb(b) + m.autoPreamp(b);
      ok(after <= 1e-9, `case ${n}: peak after the preamp is ${after}`);
    }
  }],
  ["the user can lower the preamp but never raise it above the automatic one", () => {
    const b = with1(5, { gain: 6 });
    eq(m.effectivePreamp(b, -9), -9);
    eq(m.effectivePreamp(b, -2), -6);
    eq(m.effectivePreamp(b, 0), -6);
    eq(m.effectivePreamp(b, null), -6);
  }],
  ["frequency maps to x on a log axis and back", () => {
    eq(Math.round(m.freqToX(20, 1000)), 0);
    eq(Math.round(m.freqToX(20000, 1000)), 1000);
    near(m.freqToX(632.456, 1000), 500, 0.5, "geometric mean is half way");
    for (const f of [20, 100, 1234, 20000]) near(m.xToFreq(m.freqToX(f, 800), 800), f, f * 1e-9, "round trip");
  }],
  ["gain maps to y: +12 at the top, 0 in the middle, -12 at the bottom", () => {
    eq(m.gainToY(12, 400), 0); eq(m.gainToY(0, 400), 200); eq(m.gainToY(-12, 400), 400);
    eq(m.yToGain(100, 400), 6);
  }],
  ["dragging a peaking handle sets frequency and gain from the finger", () => {
    const b = { type: "peaking", freq: 1000, gain: 0, q: 1, on: true };
    const p = m.dragBand(b, 500, 100, 1000, 400);
    near(p.freq, 632.5, 0.5, "freq"); eq(p.gain, 6);
  }],
  ["a shelf's handle sits at half its gain, so a finger there sets the whole", () => {
    const b = { type: "lowshelf", freq: 100, gain: 0, q: 0.707, on: true };
    eq(m.dragBand(b, 300, 100, 1000, 400).gain, 12);
    const at = { ...b, gain: 8 };
    near(m.handleAt(at, 1000, 400)[1], m.gainToY(4, 400), 0.01, "handle y");
  }],
  ["a cut's handle moves in frequency only", () => {
    const p = m.dragBand({ type: "highpass", freq: 60, gain: 0, q: 0.707 }, 400, 0, 1000, 400);
    eq("gain" in p, false); ok(p.freq > 100, "moved right");
  }],
  ["a drag past the edge is held to 20 Hz - 20 kHz and +-12 dB", () => {
    const b = { type: "peaking", freq: 1000, gain: 0, q: 1 };
    const p = m.dragBand(b, 5000, -900, 1000, 400);
    eq(p.freq, 20000); eq(p.gain, 12);
    const q = m.dragBand(b, -5000, 9000, 1000, 400);
    eq(q.freq, 20); eq(q.gain, -12);
  }],
  ["the nearest handle within reach is picked, and none when the finger is far", () => {
    const b = flat();
    const [x, y] = m.handleAt(b[5], 1000, 400);
    eq(m.nearestHandle(b, x + 5, y - 4, 1000, 400), 5);
    eq(m.nearestHandle(b, x, y + 200, 1000, 400), -1);
  }],
  ["spreading two fingers widens the band (lower Q), pinching narrows it; held to 0.3 - 8", () => {
    eq(m.pinchQ(1, 200, 100), 2); eq(m.pinchQ(1, 100, 200), 0.5);   // together: narrower; apart: wider
    eq(m.pinchQ(1, 1000, 1), 8); eq(m.pinchQ(1, 1, 1000), 0.3);
  }],
  ["the sliders are log: the ends are the limits and the middle is the geometric mean", () => {
    eq(Math.round(m.sliderToFreq(0)), 20); eq(Math.round(m.sliderToFreq(1000)), 20000);
    near(m.sliderToQ(500), Math.sqrt(0.3 * 8), 0.001, "Q middle");
    eq(m.qToSlider(8), 1000); eq(m.freqToSlider(20), 0);
  }],
  ["frequencies read the way the keys label them", () => {
    eq(m.fmtFreqShort(31), "31"); eq(m.fmtFreqShort(125), "125"); eq(m.fmtFreqShort(1000), "1k");
    eq(m.fmtFreqShort(1500), "1.5k"); eq(m.fmtFreqShort(16000), "16k");
    eq(m.fmtFreq(125), "125 Hz"); eq(m.fmtFreq(16000), "16 kHz"); eq(m.fmtFreq(2500), "2.5 kHz");
    eq(m.fmtDb(-3.2), "−3.2"); eq(m.fmtDb(0), "0.0"); eq(m.fmtDb(2), "+2.0");
  }],
  ["the curve path has one point per frequency and the area closes to the floor", () => {
    const fs = m.logGrid(5), p = m.linePath(fs, [0, 0, 0, 0, 0], 100, 40);
    eq(p.split("L").length, 5); ok(p.startsWith("M0.0 20.0"), p);
    ok(m.areaPath(p, 100, 40).endsWith("L100 40L0 40Z"), "area");
  }],
];
