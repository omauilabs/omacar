// The equalizer's arithmetic, kept out of the view so it can be tested alone.
//
// These are the biquads PipeWire's bq_* builtins run (spa/plugins/audioconvert/
// biquad.c: the RBJ cookbook with alpha = sin(w0) / 2Q, Gain in dB, A = 10^(dB/40),
// w0 = 2 pi f / rate), and lib/eq.py has the same ones in Python. The screen draws
// the curve from them, and the server uses them to set the preamp that keeps the
// whole curve at or below 0 dB -- so the line on the screen IS the line the
// preamp was computed from.

export const RATE = 48000;
export const FREQ_MIN = 20, FREQ_MAX = 20000;
export const GAIN_MAX = 12;
export const Q_MIN = 0.3, Q_MAX = 8;
export const NBANDS = 10;
export const TYPES = ["lowshelf", "peaking", "highshelf", "highpass", "lowpass"];
export const TYPE_NAMES = { lowshelf: "Low shelf", peaking: "Peaking", highshelf: "High shelf",
                            highpass: "Low cut", lowpass: "High cut" };
export const isCut = (t) => t === "highpass" || t === "lowpass";

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function biquad(type, freq, q, gainDb, rate = RATE) {
  const w0 = Math.PI * clamp(freq / (rate / 2), 0, 1);
  if (type === "lowpass" || type === "highpass") {
    if (w0 <= 0 || w0 >= Math.PI) {
      const pass = (w0 >= Math.PI) === (type === "lowpass");
      return [pass ? 1 : 0, 0, 0, 0, 0];
    }
    const alpha = Math.sin(w0) / (2 * q), k = Math.cos(w0);
    const s = type === "lowpass" ? 1 - k : 1 + k;
    const a0 = 1 + alpha;
    return [s / 2 / a0, (type === "lowpass" ? s : -s) / a0, s / 2 / a0, -2 * k / a0, (1 - alpha) / a0];
  }
  const A = Math.pow(10, gainDb / 40);
  const alpha = Math.sin(w0) / (2 * q), k = Math.cos(w0);
  let b0, b1, b2, a0, a1, a2;
  if (type === "peaking") {
    b0 = 1 + alpha * A; b1 = -2 * k; b2 = 1 - alpha * A;
    a0 = 1 + alpha / A; a1 = -2 * k; a2 = 1 - alpha / A;
  } else {
    const k2 = 2 * Math.sqrt(A) * alpha, ap = A + 1, am = A - 1;
    if (type === "lowshelf") {
      b0 = A * (ap - am * k + k2); b1 = 2 * A * (am - ap * k); b2 = A * (ap - am * k - k2);
      a0 = ap + am * k + k2; a1 = -2 * (am + ap * k); a2 = ap + am * k - k2;
    } else {
      b0 = A * (ap + am * k + k2); b1 = -2 * A * (am + ap * k); b2 = A * (ap + am * k - k2);
      a0 = ap - am * k + k2; a1 = 2 * (am - ap * k); a2 = ap - am * k - k2;
    }
  }
  return [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0];
}

export function magDb([b0, b1, b2, a1, a2], f, rate = RATE) {
  const w = 2 * Math.PI * f / rate;
  const c1 = Math.cos(w), s1 = Math.sin(w), c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
  const nr = b0 + b1 * c1 + b2 * c2, ni = -(b1 * s1 + b2 * s2);
  const dr = 1 + a1 * c1 + a2 * c2, di = -(a1 * s1 + a2 * s2);
  const den = dr * dr + di * di;
  return den > 0 ? 10 * Math.log10(Math.max(nr * nr + ni * ni, 1e-30) / den) : 0;
}

export function bandCoef(b) {
  return biquad(b.type, b.freq, b.q, isCut(b.type) ? 0 : b.gain);
}

export function bandDb(b, f) { return magDb(bandCoef(b), f); }

export function logGrid(n, lo = FREQ_MIN, hi = FREQ_MAX) {
  const r = Math.log(hi / lo);
  return Array.from({ length: n }, (_, i) => lo * Math.exp(r * i / (n - 1)));
}

// The combined response of the enabled bands, before the preamp.
export function curveDb(bands, freqs) {
  const co = bands.filter((b) => b.on).map(bandCoef);
  return freqs.map((f) => co.reduce((s, c) => s + magDb(c, f), 0));
}

export function peakDb(bands) {
  const fs = logGrid(1024);
  for (const b of bands) if (b.on) for (const m of [0.85, 0.95, 1, 1.05, 1.18]) {
    const f = b.freq * m;
    if (f >= FREQ_MIN && f <= FREQ_MAX) fs.push(f);
  }
  return Math.max(...curveDb(bands, fs));
}

// -max(0, peak), rounded down to 0.1 dB so the maximum lands at or below 0.
export function autoPreamp(bands) {
  const p = Math.max(0, peakDb(bands));
  return p > 0 ? -Math.ceil(p * 10 - 1e-9) / 10 : 0;
}

// The preamp that is applied: the automatic one, or the user's if it is lower.
export function effectivePreamp(bands, user = null) {
  const a = autoPreamp(bands);
  return user === null || user === undefined ? a : Math.round(Math.min(a, user) * 10) / 10;
}

// ---- the graph's geometry -----------------------------------------------------
const L0 = Math.log(FREQ_MIN), L1 = Math.log(FREQ_MAX);
export const freqToX = (f, w) => (Math.log(clamp(f, FREQ_MIN, FREQ_MAX)) - L0) / (L1 - L0) * w;
export const xToFreq = (x, w) => Math.exp(L0 + clamp(x / w, 0, 1) * (L1 - L0));
export const gainToY = (g, h, range = GAIN_MAX) => (0.5 - clamp(g, -range, range) / (2 * range)) * h;
export const yToGain = (y, h, range = GAIN_MAX) => clamp((0.5 - y / h) * 2 * range, -range, range);

const r1 = (v) => Math.round(v * 10) / 10;

// Where a band's handle sits: on THAT band's own curve at its corner -- a
// peak's gain, half a shelf's gain (the shelf is half-way up at its corner), a
// cut's -3 dB or its resonance.
export function handleAt(b, w, h) {
  return [freqToX(b.freq, w), gainToY(bandDb(b, b.freq), h)];
}

// A finger at (x, y) on the graph (w by h) -> the band fields it sets. The
// x axis is frequency; the y axis is gain, except for a cut, which has none, and
// for a shelf, whose handle sits at half its gain.
export function dragBand(b, x, y, w, h) {
  const out = { freq: Math.round(xToFreq(x, w) * 10) / 10 };
  if (!isCut(b.type)) {
    const g = yToGain(y, h) * (b.type === "peaking" ? 1 : 2);
    out.gain = clamp(r1(g), -GAIN_MAX, GAIN_MAX);
  }
  out.freq = clamp(out.freq, FREQ_MIN, FREQ_MAX);
  return out;
}

// The nearest handle to a finger, within `reach` px (a 44 px target is 22 either
// way, so 28 is generous); the selected band wins a tie.
export function nearestHandle(bands, x, y, w, h, selected = -1, reach = 28) {
  let best = -1, bd = reach * reach;
  bands.forEach((b, i) => {
    const [hx, hy] = handleAt(b, w, h);
    const d = (hx - x) ** 2 + (hy - y) ** 2 - (i === selected ? 40 : 0);
    if (d < bd) { bd = d; best = i; }
  });
  return best;
}

// Two fingers on a band: spreading them widens the band (a lower Q), pinching
// them together narrows it, as a zoom does.
export function pinchQ(startQ, startDist, dist) {
  return clamp(Math.round(startQ * startDist / Math.max(1, dist) * 100) / 100, Q_MIN, Q_MAX);
}

// Slider <-> value, on the log scales frequency and Q live on.
export const sliderToFreq = (s) => xToFreq(s, 1000);
export const freqToSlider = (f) => Math.round(freqToX(f, 1000));
export const sliderToQ = (s) => Math.exp(Math.log(Q_MIN) + clamp(s / 1000, 0, 1) * (Math.log(Q_MAX) - Math.log(Q_MIN)));
export const qToSlider = (q) => Math.round((Math.log(clamp(q, Q_MIN, Q_MAX)) - Math.log(Q_MIN)) / (Math.log(Q_MAX) - Math.log(Q_MIN)) * 1000);

export function fmtFreq(f) {
  return f >= 1000 ? (f >= 10000 ? (f / 1000).toFixed(1).replace(/\.0$/, "") : (f / 1000).toFixed(2).replace(/0$/, "").replace(/\.0$/, "")) + " kHz"
                   : Math.round(f) + " Hz";
}
export function fmtFreqShort(f) {
  return f >= 1000 ? (f / 1000).toFixed(f >= 10000 || Math.abs(f / 1000 - Math.round(f / 1000)) < 0.05 ? 0 : 1).replace(/\.0$/, "") + "k"
                   : String(Math.round(f));
}
export const fmtDb = (g) => (g > 0 ? "+" : g < 0 ? "−" : "") + Math.abs(g).toFixed(1);

// SVG path for a curve: db[i] at freqs[i] into a w x h graph.
export function linePath(freqs, db, w, h) {
  return freqs.map((f, i) => (i ? "L" : "M") + freqToX(f, w).toFixed(1) + " " + gainToY(db[i], h).toFixed(1)).join("");
}
export function areaPath(line, w, h) { return line ? `${line}L${w} ${h}L0 ${h}Z` : ""; }

// The default layout: ten bands from a 31 Hz low shelf to a 16 kHz high shelf, flat.
export const DEFAULT_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
export function defaultBands() {
  return DEFAULT_FREQS.map((freq, i) => ({
    type: i === 0 ? "lowshelf" : i === NBANDS - 1 ? "highshelf" : "peaking",
    freq, gain: 0, q: i === 0 || i === NBANDS - 1 ? 0.707 : 1, on: true }));
}
