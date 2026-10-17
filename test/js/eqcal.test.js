// Calibrate's DSP (share/js/eqcal.js), on a SYNTHESISED room: a known magnitude
// response applied to the real sweep, with a delay and noise. What this proves is
// that the pipeline recovers a known response and fits sensible bands to it. It
// cannot prove anything about a real microphone, speakers or cabin -- only the
// tablet, in the car, can (doc/eq.md).
import { eq, ok } from "./assert.js";
import * as C from "../js/eqcal.js";
import { bandDb } from "../js/eqmath.js";

const FS = 48000;
const near = (got, want, tol, what) => {
  if (Math.abs(got - want) > tol) throw new Error(`${what}: got ${got}, want ${want} +- ${tol}`);
};
const SWEEP = C.sweepSignal(FS);
const LEAD = Math.round(C.CAL.LEAD_SECS * FS), TAIL = Math.round(C.CAL.TAIL_SECS * FS);

// The synthetic cabin: +8 dB at 120 Hz (Q 1.5) and -6 dB at 2.5 kHz (Q 2), flat elsewhere.
const room = (f) => bandDb({ type: "peaking", freq: 120, gain: 8, q: 1.5 }, f)
                  + bandDb({ type: "peaking", freq: 2500, gain: -6, q: 2 }, f);

function record(resp, { gain = 1, noise = 0.0003, delay = 3000, seed = 3 } = {}) {
  const full = new Float32Array(LEAD + SWEEP.length + TAIL);
  full.set(SWEEP, LEAD);
  const out = C.simulate(full, FS, resp, { delaySamples: delay, noise, seed });
  if (gain !== 1) for (let i = 0; i < out.length; i++) out[i] *= gain;
  return out;
}
const at = (r, f) => {
  let bi = 0;
  r.freqs.forEach((x, i) => { if (Math.abs(Math.log(x / f)) < Math.abs(Math.log(r.freqs[bi] / f))) bi = i; });
  return bi;
};

export default [
  ["the sweep is 8 s, starts from silence and peaks at -20 dBFS", () => {
    eq(SWEEP.length, 8 * FS);
    ok(Math.abs(SWEEP[0]) < 1e-6, "starts at zero");
    let pk = 0; for (const v of SWEEP) pk = Math.max(pk, Math.abs(v));
    near(20 * Math.log10(pk), -20, 0.1, "peak dBFS");
  }],
  ["the FFT is right: a sine lands in its bin, and Parseval holds", () => {
    const n = 1024, re = new Float64Array(n), im = new Float64Array(n);
    for (let i = 0; i < n; i++) re[i] = Math.sin(2 * Math.PI * 64 * i / n);
    fft: { const e = re.reduce((s, v) => s + v * v, 0); C.fft(re, im);
      const p = re.map((v, i) => v * v + im[i] * im[i]);
      eq(p.indexOf(Math.max(...p)) === 64 || p.indexOf(Math.max(...p)) === 960, true);
      near(p.reduce((s, v) => s + v, 0) / n, e, 1e-6, "Parseval"); }
  }],
  ["the target curve: +4 dB below 100 Hz, 0 at 300 Hz and flat, -2 dB from 12 kHz", () => {
    eq(C.targetDb(50), 4); eq(C.targetDb(100), 4); eq(C.targetDb(300), 0); eq(C.targetDb(1000), 0);
    eq(C.targetDb(10000), 0); eq(C.targetDb(16000), -2);
    near(C.targetDb(173.2), 2, 0.05, "half way down the taper");
  }],
  ["measure() recovers the room's response (shape, to within 1.5 dB) through a delay and noise", () => {
    const m = C.measure(record(room), FS, SWEEP);
    const flat = (f) => m.db[at(m, f)];
    const ref = [250, 500, 1000, 4000].reduce((s, f) => s + flat(f), 0) / 4;
    const truth = (f) => room(f) - [250, 500, 1000, 4000].reduce((a, x) => a + room(x), 0) / 4;
    for (const f of [60, 120, 2500, 8000]) near(flat(f) - ref, truth(f), 1.5, `${f} Hz`);
    near(flat(120) - ref, 8, 1.5, "the 120 Hz peak");
    near(flat(2500) - ref, -6, 1.5, "the 2.5 kHz dip");
  }],
  ["measure() reports the noise: SNR is high at a loud sweep and near 0 dB with no signal", () => {
    const loud = C.measure(record(room), FS, SWEEP);
    ok(loud.snr[at(loud, 1000)] > 25, "1 kHz SNR " + loud.snr[at(loud, 1000)]);
    const quiet = C.measure(record((f) => room(f) - 70), FS, SWEEP);
    ok(quiet.snr[at(quiet, 1000)] < 6, "a mute sweep: " + quiet.snr[at(quiet, 1000)]);
  }],
  ["calibrate() on the +8 dB @120 / -6 dB @2.5k room: a cut near 120 Hz, a bounded boost near 2.5 kHz", () => {
    const r = C.calibrate(record(room), FS, SWEEP);
    ok(r.ok, r.error);
    const cuts = r.bands.filter((b) => b.gain < -2 && Math.abs(Math.log2(b.freq / 120)) < 1 / 3);
    ok(cuts.length >= 1, "a cut within a third of an octave of 120 Hz: " + JSON.stringify(r.bands));
    const boosts = r.bands.filter((b) => b.gain > 1 && Math.abs(Math.log2(b.freq / 2500)) < 1 / 3);
    ok(boosts.length >= 1, "a boost near 2.5 kHz: " + JSON.stringify(r.bands));
    for (const b of r.bands) ok(b.gain <= 6 && b.gain >= -12, "gain bounds " + b.gain);
    for (const b of boosts) ok(b.q <= 3, "a boost is not narrow: Q " + b.q);
  }],
  ["...it makes the corrected prediction closer to the target than the measurement was", () => {
    const r = C.calibrate(record(room), FS, SWEEP);
    const err = (curve) => {
      let s = 0, n = 0;
      r.freqs.forEach((f, i) => { if (f >= 40 && f <= 12000) { const d = curve[i] - r.target[i]; s += d * d; n++; } });
      return Math.sqrt(s / n);
    };
    ok(err(r.predicted) < err(r.measured) * 0.6, `rms to target ${err(r.measured).toFixed(2)} -> ${err(r.predicted).toFixed(2)}`);
  }],
  ["...ten bands come back, in frequency order, none below 40 Hz or above 12 kHz", () => {
    const r = C.calibrate(record(room), FS, SWEEP);
    eq(r.bands.length, 10);
    for (let i = 1; i < 10; i++) ok(r.bands[i].freq >= r.bands[i - 1].freq, "sorted");
    for (const b of r.bands.filter((x) => x.gain !== 0)) ok(b.freq >= 40 && b.freq <= 12000, "range " + b.freq);
    ok(r.bands.every((b) => b.type === "peaking"), "peaking filters");
  }],
  ["...and the result still obeys the headroom rule once the app's preamp is applied", async () => {
    const { autoPreamp, peakDb } = await import("../js/eqmath.js");
    const r = C.calibrate(record(room), FS, SWEEP);
    ok(peakDb(r.bands) + autoPreamp(r.bands) <= 1e-9, "peak after preamp is at or below 0 dB");
  }],
  ["a flat room needs (almost) nothing but the house curve's low end", () => {
    const r = C.calibrate(record(() => 0), FS, SWEEP);
    ok(r.ok, r.error);
    for (const b of r.bands) ok(Math.abs(b.gain) <= 6, "bounded");
    ok(r.bands.filter((b) => Math.abs(b.gain) > 0).length <= 4, "few bands: " + JSON.stringify(r.bands.filter((b) => b.gain)));
  }],
  ["a recording too quiet to measure is refused with a plain reason", () => {
    const r = C.calibrate(record((f) => room(f) - 70), FS, SWEEP);
    eq(r.ok, false);
    ok(/too quiet/i.test(r.error) && /turn the radio up/i.test(r.error), r.error);
  }],
  ["a clipped recording is refused", () => {
    const rec = record(room, { gain: 400 });
    for (let i = 0; i < rec.length; i++) rec[i] = Math.max(-1, Math.min(1, rec[i]));
    const r = C.calibrate(rec, FS, SWEEP);
    eq(r.ok, false); ok(/clipped/i.test(r.error), r.error);
  }],
  ["a recording that is shorter than the sweep is refused, not misread", () => {
    eq(C.calibrate(new Float32Array(FS), FS, SWEEP).ok, false);
  }],
  ["a deep null (-16 dB at 5 kHz) is left alone: EQ cannot fill it", () => {
    const nulled = (f) => room(f) + bandDb({ type: "peaking", freq: 5000, gain: -16, q: 1.5 }, f);
    const r = C.calibrate(record(nulled), FS, SWEEP);
    ok(r.ok, r.error);
    const b = r.bands.filter((x) => x.gain > 0 && Math.abs(Math.log2(x.freq / 5000)) < 1 / 6);
    eq(b.length, 0);
  }],
  ["fitBands never boosts where the measurement is under the noise", () => {
    const fs = C.thirdOctaveGrid();
    const dev = fs.map((f) => (f > 4000 && f < 9000) ? 6 : 0);          // a dip to fill ...
    const snr = fs.map((f) => (f > 2900 && f < 11000) ? 3 : 30);          // ... in the noise
    eq(C.fitBands(fs, dev, snr).filter((b) => b.gain > 0).length, 0);
    const good = fs.map(() => 30);
    ok(C.fitBands(fs, dev, good).some((b) => b.gain > 0), "the same dip, measured well, is filled");
  }],
  ["fitBands makes no correction outside 40 Hz - 12 kHz", () => {
    const fs = C.thirdOctaveGrid();
    const dev = fs.map((f) => (f < 35 || f > 14000) ? -10 : 0);
    eq(C.fitBands(fs, dev, fs.map(() => 30)).length, 0);
  }],
  ["fitBands finds a -8 dB hump and holds a +10 dB dip to +6", () => {
    const fs = C.thirdOctaveGrid();
    const hump = fs.map((f) => -8 * Math.exp(-Math.pow(Math.log2(f / 200) / 0.4, 2)));
    const a = C.fitBands(fs, hump, fs.map(() => 30));
    ok(a.length >= 1 && a[0].gain < -5 && Math.abs(Math.log2(a[0].freq / 200)) < 0.3, JSON.stringify(a));
    const dip = fs.map((f) => 7 * Math.exp(-Math.pow(Math.log2(f / 1000) / 0.8, 2)));
    const b = C.fitBands(fs, dip, fs.map(() => 30));
    ok(b.every((x) => x.gain <= 6), JSON.stringify(b));
  }],
];
