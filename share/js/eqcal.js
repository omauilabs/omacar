// CALIBRATE: measure the cabin with a sweep, and fit the ten bands to a target.
//
// Pure DSP, no DOM and no audio nodes, so it can be tested on a synthesised room
// (test/js/eqcal.test.js). The view (views/eq.js) plays the sweep and records the
// microphone; everything that turns those two arrays into bands is here.
//
//   sweepSignal()   an exponential (log) sine sweep, 20 Hz - 20 kHz, with fades
//   measure()       the response by spectral division of the recording by the
//                   sweep that was played (delay does not matter to a magnitude),
//                   smoothed to 1/3 octave, with a signal-to-noise figure per
//                   band taken from a noise floor recorded BEFORE the sweep
//   fitBands()      greedy peaking-filter fit of (target - measured)
//   calibrate()     all of it, or a refusal with a plain reason
//
// WHAT THE NUMBERS ARE AND ARE NOT. The measured curve is the whole chain: the
// tablet's output, the car radio and speakers, the cabin, and the MICROPHONE --
// which is not flat either. It is normalised to the mean level between 250 Hz
// and 4 kHz, so only its SHAPE is used. A small laptop mic can be several dB off
// at the ends, which is why the correction is bounded (+6 dB boost, -12 dB cut),
// stays between 40 Hz and 12 kHz, and boosts nothing the microphone could not
// hear above its own noise.

import { bandDb, curveDb, logGrid, defaultBands, FREQ_MIN, FREQ_MAX } from "./eqmath.js";

export const CAL = {
  FS_DEFAULT: 48000,
  SWEEP_SECS: 8,
  LEAD_SECS: 1.0,        // silence recorded before the sweep: the noise floor
  TAIL_SECS: 1.5,        // recorded after it, for the room's decay and the latency
  F1: 20, F2: 20000,
  DBFS: -20,             // the sweep's peak, after a half-second fade in
  FADE_SECS: 0.5,
  FIT_LO: 40, FIT_HI: 12000,
  BOOST_MAX: 6, CUT_MAX: 12,
  BOOST_Q_MAX: 3,        // a boost is never a narrow one
  NULL_DB: 8,            // a dip deeper than this against the target is a null, which EQ cannot fill
  SNR_BAND_DB: 10,       // below this a band's measurement is not trusted for a boost
  SNR_REFUSE_DB: 6,      // median SNR below this: too quiet to measure
  CLIP: 0.98,
  MAX_BANDS: 10,
  MIN_GAIN: 0.5,
  STOP_DB: 0.8,
};

// ---- the signal ---------------------------------------------------------------
export function sweepSignal(fs = CAL.FS_DEFAULT, secs = CAL.SWEEP_SECS, f1 = CAL.F1, f2 = CAL.F2,
                            dbfs = CAL.DBFS, fade = CAL.FADE_SECS) {
  const n = Math.round(fs * secs), L = secs / Math.log(f2 / f1), K = 2 * Math.PI * f1 * L;
  const out = new Float32Array(n), amp = Math.pow(10, dbfs / 20), nf = Math.round(fs * fade);
  for (let i = 0; i < n; i++) {
    const t = i / fs;
    let g = amp;
    if (i < nf) g *= 0.5 - 0.5 * Math.cos(Math.PI * i / nf);              // from silence
    else if (n - i < nf / 4) g *= 0.5 - 0.5 * Math.cos(Math.PI * (n - i) / (nf / 4));
    out[i] = g * Math.sin(K * (Math.exp(t / L) - 1));
  }
  return out;
}

// ---- FFT ----------------------------------------------------------------------
export function nextPow2(n) { let p = 1; while (p < n) p <<= 1; return p; }

// In-place iterative radix-2 complex FFT on Float64Arrays; length a power of two.
export function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang), half = len >> 1;
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < half; k++) {
        const a = i + k, b = a + half;
        const xr = re[b] * cr - im[b] * ci, xi = re[b] * ci + im[b] * cr;
        re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
        const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
      }
    }
  }
}

export function spectrum(x, n) {
  const re = new Float64Array(n), im = new Float64Array(n);
  for (let i = 0; i < Math.min(x.length, n); i++) re[i] = x[i];
  fft(re, im);
  return [re, im];
}

// ---- grids and the target -------------------------------------------------------
// 1/3-octave centres, 20 Hz - 20 kHz.
export function thirdOctaveGrid(lo = 20, hi = 20000) {
  const out = [];
  for (let k = -17; k <= 14; k++) {
    const f = 1000 * Math.pow(2, k / 3);
    if (f >= lo * 0.99 && f <= hi * 1.01) out.push(f);
  }
  return out;
}

// THE CAR HOUSE CURVE (a default the owner can change; doc/eq.md): +4 dB below
// 100 Hz, tapering to 0 at 300 Hz (straight on a log axis), flat to 10 kHz, and
// 2 dB down from 12 kHz up (tapering in from 10 kHz).
export const TARGET = { bassDb: 4, bassKnee: 100, bassEnd: 300, trebleDb: -2, trebleKnee: 10000, trebleEnd: 12000 };
export function targetDb(f, t = TARGET) {
  if (f <= t.bassKnee) return t.bassDb;
  if (f < t.bassEnd) return t.bassDb * (1 - Math.log(f / t.bassKnee) / Math.log(t.bassEnd / t.bassKnee));
  if (f <= t.trebleKnee) return 0;
  if (f < t.trebleEnd) return t.trebleDb * Math.log(f / t.trebleKnee) / Math.log(t.trebleEnd / t.trebleKnee);
  return t.trebleDb;
}

// Power-average |v|^2 (any per-bin quantity) into 1/3-octave bands.
function bandAverage(freqsBin, vals, centres) {
  return centres.map((fc) => {
    const lo = fc / Math.pow(2, 1 / 6), hi = fc * Math.pow(2, 1 / 6);
    let s = 0, n = 0;
    for (let k = 0; k < freqsBin.length; k++) {
      const f = freqsBin[k];
      if (f < lo) continue;
      if (f >= hi) break;
      s += vals[k]; n++;
    }
    return n ? s / n : 0;
  });
}

const db10 = (x) => 10 * Math.log10(Math.max(x, 1e-30));

// ---- measuring -------------------------------------------------------------------
// rec: the Float32Array the microphone gave, starting LEAD_SECS before the sweep
// started playing. Returns the response in dB per 1/3-octave band (absolute and
// arbitrary), the SNR per band, and the recording's peak.
export function measure(rec, fs, sweep, opts = {}) {
  const lead = Math.round((opts.lead ?? CAL.LEAD_SECS) * fs);
  const tail = Math.round((opts.tail ?? CAL.TAIL_SECS) * fs);
  const len = sweep.length + tail;
  if (rec.length < lead + sweep.length) return { error: "The recording was cut short." };
  let peak = 0;
  for (let i = 0; i < rec.length; i++) { const a = Math.abs(rec[i]); if (a > peak) peak = a; }
  const body = rec.subarray(lead, Math.min(rec.length, lead + len));
  const N = nextPow2(len);
  const [yr, yi] = spectrum(body, N);
  const [xr, xi] = spectrum(sweep, N);
  const half = N / 2;
  const fb = new Float64Array(half);
  for (let k = 0; k < half; k++) fb[k] = k * fs / N;
  let xmax = 0;
  const xp = new Float64Array(half);
  for (let k = 0; k < half; k++) { xp[k] = xr[k] * xr[k] + xi[k] * xi[k]; if (xp[k] > xmax) xmax = xp[k]; }
  // |H|^2 = |Y|^2 / |X|^2 for a pure magnitude, regularised where the sweep has no energy.
  const eps = xmax * 1e-9;
  const hp = new Float64Array(half), yp = new Float64Array(half);
  for (let k = 0; k < half; k++) {
    yp[k] = yr[k] * yr[k] + yi[k] * yi[k];
    hp[k] = yp[k] / (xp[k] + eps);
  }
  // The noise floor: sigma^2(f) from the recording's own silence, Welch-averaged.
  const nz = rec.subarray(Math.round(fs * 0.1), Math.max(Math.round(fs * 0.1) + 8192, lead - Math.round(fs * 0.1)));
  const M = 4096, win = new Float64Array(M);
  let sw2 = 0;
  for (let i = 0; i < M; i++) { win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / M); sw2 += win[i] * win[i]; }
  const nper = new Float64Array(M / 2);
  let segs = 0;
  for (let s = 0; s + M <= nz.length; s += M / 2) {
    const re = new Float64Array(M), im = new Float64Array(M);
    for (let i = 0; i < M; i++) re[i] = nz[s + i] * win[i];
    fft(re, im);
    for (let k = 0; k < M / 2; k++) nper[k] += re[k] * re[k] + im[k] * im[k];
    segs++;
  }
  const fn = new Float64Array(M / 2);
  for (let k = 0; k < M / 2; k++) { fn[k] = k * fs / M; nper[k] = segs ? nper[k] / segs / sw2 : 0; }
  // 25 Hz - 16 kHz: the sweep runs 20 Hz - 20 kHz, and the outermost bands would
  // be half outside it (and half noise), so they are not reported.
  const centres = thirdOctaveGrid(25, 16000);
  // Expected |Y|^2 from noise alone over `body.length` samples is body.length * sigma^2(f).
  const noiseBands = bandAverage(fn, nper, centres).map((v) => v * body.length);
  const sigBands = bandAverage(fb, yp, centres);
  const hBands = bandAverage(fb, hp, centres);
  const snr = sigBands.map((s, i) => db10(Math.max(s / Math.max(noiseBands[i], 1e-30) - 1, 1e-6)));
  return { freqs: centres, db: hBands.map(db10), snr, peak, rms: Math.sqrt(rec.reduce((a, v) => a + v * v, 0) / rec.length) };
}

// ---- fitting ---------------------------------------------------------------------
const lerp = (x, xs, ys) => {
  if (x <= xs[0]) return ys[0];
  for (let i = 1; i < xs.length; i++) if (x <= xs[i]) {
    const t = Math.log(x / xs[i - 1]) / Math.log(xs[i] / xs[i - 1]);
    return ys[i - 1] + (ys[i] - ys[i - 1]) * t;
  }
  return ys[ys.length - 1];
};

const QS = [0.5, 0.7, 1, 1.4, 2, 3, 4.5];

// dev: the correction wanted at `freqs` (dB), snr: the measurement's SNR there.
// Returns up to ten peaking bands. Cuts are weighted fully and boosts at 0.6, a
// boost is limited to +6 dB and Q <= 3, nothing is fitted below 40 Hz or above
// 12 kHz, and a boost is never put where the measurement was not above the noise.
export function fitBands(freqs, dev, snr, o = {}) {
  const C = Object.assign({}, CAL, o);
  const grid = logGrid(Math.round(Math.log2(C.FIT_HI / C.FIT_LO) * 6) + 1, C.FIT_LO, C.FIT_HI);
  const d0 = grid.map((f) => lerp(f, freqs, dev));
  const sn = grid.map((f) => lerp(f, freqs, snr));
  const usable = sn.map((s) => s >= C.SNR_BAND_DB);
  const res = d0.slice();
  // A null (a dip deeper than NULL_DB) and a third of an octave either side of it
  // are left alone: a boost cannot fill one, it only spends headroom.
  const nul = res.map((r) => r > C.NULL_DB);
  const nearNull = (i) => { for (let j = Math.max(0, i - 2); j <= Math.min(nul.length - 1, i + 2); j++) if (nul[j]) return true; return false; };
  const w = res.map((r, i) => (!usable[i] || nearNull(i)) ? 0 : r < 0 ? 1 : 0.6);
  const cost = () => res.reduce((s, r, i) => s + w[i] * r * r, 0);
  const bands = [];
  let cur = cost();
  for (let it = 0; it < C.MAX_BANDS; it++) {
    let bi = -1, bv = C.STOP_DB;
    res.forEach((r, i) => { const v = w[i] * Math.abs(r); if (v > bv) { bv = v; bi = i; } });
    if (bi < 0) break;
    let best = null;
    for (let k = -2; k <= 2; k++) {
      const f0 = grid[bi] * Math.pow(2, k / 6);
      if (f0 < C.FIT_LO || f0 > C.FIT_HI) continue;
      for (const q of QS) {
        const shape = grid.map((f) => bandDb({ type: "peaking", freq: f0, gain: 6, q }, f) / 6);
        let num = 0, den = 0;
        shape.forEach((s, i) => { num += w[i] * s * res[i]; den += w[i] * s * s; });
        if (den <= 0) continue;
        let g = Math.max(-C.CUT_MAX, Math.min(C.BOOST_MAX, num / den));
        if (g > 0 && (q > C.BOOST_Q_MAX || lerp(f0, freqs, snr) < C.SNR_BAND_DB)) continue;
        if (Math.abs(g) < C.MIN_GAIN) continue;
        g = Math.round(g * 10) / 10;
        const b = { type: "peaking", freq: Math.round(f0 * 10) / 10, gain: g, q, on: true };
        const after = res.map((r, i) => r - bandDb(b, grid[i]));
        const c = after.reduce((s, r, i) => s + w[i] * r * r, 0);
        if (!best || c < best.c) best = { b, c, after };
      }
    }
    if (!best || best.c > cur * 0.98) break;
    bands.push(best.b);
    best.after.forEach((r, i) => { res[i] = r; });
    cur = best.c;
  }
  return bands;
}

// The ten slots: the fitted bands, then flat bands at the default layout's
// frequencies (clear of the fitted ones), all in frequency order.
export function toSlots(fitted) {
  const out = fitted.map((b) => ({ ...b }));
  for (const d of defaultBands()) {
    if (out.length >= 10) break;
    if (out.every((b) => Math.abs(Math.log2(b.freq / d.freq)) > 1 / 3)) {
      out.push({ type: "peaking", freq: d.freq, gain: 0, q: 1, on: true });
    }
  }
  const spare = [45, 90, 180, 360, 720, 1400, 2800, 5600, 11000];
  for (const f of spare) { if (out.length < 10) out.push({ type: "peaking", freq: f, gain: 0, q: 1, on: true }); }
  return out.slice(0, 10).sort((a, b) => a.freq - b.freq);
}

const meanBetween = (freqs, vals, lo, hi) => {
  let s = 0, n = 0;
  freqs.forEach((f, i) => { if (f >= lo && f <= hi) { s += vals[i]; n++; } });
  return n ? s / n : 0;
};
const median = (a) => { const s = a.slice().sort((x, y) => x - y); return s.length ? s[s.length >> 1] : 0; };
const quantile = (a, p) => { const s = a.slice().sort((x, y) => x - y); return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0; };

// Everything: a recording and the sweep that was played -> bands, or a reason.
export function calibrate(rec, fs, sweep, opts = {}) {
  const m = measure(rec, fs, sweep, opts);
  if (m.error) return { ok: false, error: m.error };
  if (m.peak >= CAL.CLIP) {
    return { ok: false, error: "The microphone clipped: turn the radio down a little and try again." };
  }
  const inBand = m.freqs.map((f, i) => (f >= 100 && f <= 8000) ? m.snr[i] : null).filter((v) => v !== null);
  const snrMed = median(inBand);
  if (snrMed < CAL.SNR_REFUSE_DB) {
    return { ok: false, snrDb: snrMed,
      error: "Too quiet to measure: turn the radio up a little, or move closer to the microphone, and keep the cabin quiet." };
  }
  const ref = meanBetween(m.freqs, m.db, 250, 4000);
  const measured = m.db.map((v) => v - ref);
  const target = m.freqs.map((f) => targetDb(f));
  const want = m.freqs.map((f, i) => target[i] - measured[i]);
  // Where the correction is judged (the band it may touch), and its baseline: a
  // high percentile, so what is left to do is mostly CUTS and the dips are filled
  // only gently (fitBands weights a boost less than a cut).
  const usableIdx = m.freqs.map((f, i) => i).filter((i) => m.freqs[i] >= CAL.FIT_LO && m.freqs[i] <= CAL.FIT_HI && m.snr[i] >= CAL.SNR_BAND_DB);
  const base = quantile(usableIdx.map((i) => want[i]), 0.7);
  const dev = want.map((v) => v - base);
  const bands = toSlots(fitBands(m.freqs, dev, m.snr));
  const corr = curveDb(bands, m.freqs);
  const predicted0 = measured.map((v, i) => v + corr[i]);
  const shift = meanBetween(m.freqs, predicted0, 250, 4000);
  const predicted = predicted0.map((v) => v - shift);
  const fitted = bands.filter((b) => Math.abs(b.gain) > 0);
  // Confidence: how far above the noise the useful part of the measurement was.
  const conf = Math.max(0, Math.min(1, (snrMed - CAL.SNR_REFUSE_DB) / 24));
  const label = conf > 0.66 ? "High" : conf > 0.33 ? "Medium" : "Low";
  return { ok: true, freqs: m.freqs, measured, target, predicted, bands, fitted: fitted.length,
           snrDb: snrMed, confidence: conf, label, baseline: base, peak: m.peak };
}

// What the room does to a signal, for tests: a magnitude response applied to a
// signal in the frequency domain, with a delay and added noise.
export function simulate(signal, fs, respDb, { delaySamples = 0, total, noise = 0, seed = 1 } = {}) {
  const n = total ?? signal.length + delaySamples;
  const N = nextPow2(n);
  const [re, im] = spectrum(signal, N);
  for (let k = 0; k <= N / 2; k++) {
    const f = k * fs / N;
    const g = f < 1 ? 1 : Math.pow(10, respDb(f) / 20);
    const ph = -2 * Math.PI * f * delaySamples / fs;
    const c = Math.cos(ph) * g, s = Math.sin(ph) * g;
    const r = re[k], i = im[k];
    re[k] = r * c - i * s; im[k] = r * s + i * c;
    if (k > 0 && k < N / 2) { re[N - k] = re[k]; im[N - k] = -im[k]; }
  }
  im[0] = 0; im[N / 2] = 0;
  // inverse via conjugate trick
  for (let k = 0; k < N; k++) im[k] = -im[k];
  fft(re, im);
  const out = new Float32Array(n);
  let s = seed >>> 0;
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  for (let i = 0; i < n; i++) out[i] = re[i] / N + (noise ? noise * (rnd() + rnd() + rnd() + rnd() - 2) * 1.7 : 0);
  return out;
}

export { curveDb, FREQ_MIN, FREQ_MAX };
