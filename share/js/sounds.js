// The alert sounds, each played at its own level: an envelope from silence and
// back (ramps.js envelopePlan), scheduled by alertplayer.js. The chime, the
// two-tone alarm and the dog's bark are synthesized here. The voice is the one
// recording: Piper, rendered ahead of time (Task 12), because the tablet has
// no speech engine and nothing in the car should depend on one.
//
// Inside its envelope a sound keeps its character: a bark's syllables start
// fast, a chime's notes ring. They are only ever as loud as the envelope is at
// that moment, and the envelope never moves more than 3 dB in any 100 ms.
//
// THE BARK IS HONEST ABOUT BEING SYNTHESIZED: a pitched harmonic burst with a
// fast downward sweep through two formants, over band-passed noise, two barks
// to a call. Better sounds are another day's work (owner, 2026-09-28).
//
// WHERE A SOUND PLAYS is a stage, {ctx, out}: the page's alert gate unless
// one is given. The tests give an OfflineAudioContext, so every player below
// is rendered and measured sample by sample without a speaker anywhere.

import { withToken } from "./core.js";
import { audioContext, alertIn, dbToGain } from "./audiobus.js";

export const CHIME = { notes: [{ hz: 659.25, at: 0 }, { hz: 880, at: 0.6 }] };
export const ALARM = { tones: [700, 1000], toneSecs: 0.25, peak: 0.6 };
export const BARK = { gap: 0.3, callEvery: 0.8, sweepFrom: 540, sweepTo: 260, secs: 0.16,
                      formants: [1100, 2400], noiseHz: 1700, attack: 0.006, peak: 0.8 };

// A source is stopped this long after its envelope reaches silence.
const TAIL_SECS = 0.05;
// Without cancelAndHoldAtTime, a release cancels only what comes this long
// after its own starting point, so that point itself is kept (see held()).
const KEEP_SECS = 0.001;

// The alarm is one oscillator swung between its two tones by a square wave,
// so it alternates for as long as it sounds, with no schedule to run out.
export function alarmModulation(plan = ALARM) {
  const [lo, hi] = plan.tones;
  return { base: (lo + hi) / 2, depth: (hi - lo) / 2, hz: 1 / (2 * plan.toneSecs) };
}

export function barkCalls(calls, plan = BARK) {
  const out = [];
  for (let c = 0; c < calls; c++) out.push(+(c * plan.callEvery).toFixed(3), +(c * plan.callEvery + plan.gap).toFixed(3));
  return out;
}

function alertStage() { return { ctx: audioContext(), out: alertIn() }; }

// A sound's own level: its envelope, point by point, on a gain node into the
// stage. The first point is silence at the sound's start, and the node is
// silent before then too: it exists a moment before its sound does.
function levelNode(stage, v) {
  const g = stage.ctx.createGain();
  g.gain.value = dbToGain(v.env[0][1]);
  g.gain.setValueAtTime(dbToGain(v.env[0][1]), v.env[0][0]);
  for (const [t, db] of v.env.slice(1)) g.gain.linearRampToValueAtTime(dbToGain(db), t);
  g.connect(stage.out);
  return g;
}

// Start a source with its sound and stop it once the envelope is silent. A
// held sound (end Infinity) runs until it is let go.
function run(src, v, sources) {
  src.start(v.start);
  const stopAt = Number.isFinite(v.end) ? v.end + TAIL_SECS : Infinity;
  if (Number.isFinite(stopAt)) src.stop(stopAt);
  sources.push({ node: src, stopAt });
  return src;
}

// What the player keeps of a sound: a way to let it go, along a release plan
// (absolute [t, dB] points starting at `at`, from alertplayer.js
// releaseVoices), stopping its sources once it is silent.
//
// WITHOUT cancelAndHoldAtTime (Firefox) THIS STILL NEVER STEPS. `at` is
// always a point of the envelope, or inside a stretch where it holds still
// (ramps.js releaseAt), so cancelling only what comes after `at` keeps the
// level exactly where the envelope has it; the release ramps on from there.
// Cancelling in the middle of a ramp would drop the ramp's end and put the
// level back where that ramp began.
function held(g, sources) {
  return {
    release(at, points, end) {
      const p = g.gain;
      if (p.cancelAndHoldAtTime) p.cancelAndHoldAtTime(at);
      else p.cancelScheduledValues(at + KEEP_SECS);
      for (const [t, db] of points) p.linearRampToValueAtTime(dbToGain(db), t);
      const stopAt = end + TAIL_SECS;
      for (const s of sources) {
        // A later stop() replaces an earlier one, so a source already due to
        // stop sooner (a bark's syllable) is left alone.
        if (stopAt >= s.stopAt) continue;
        try { s.node.stop(stopAt); s.stopAt = stopAt; } catch { /* already gone */ }
      }
    },
  };
}

export function playChimeNote(v, stage = alertStage()) {
  const { ctx } = stage, g = levelNode(stage, v), sources = [];
  // A bell's first three partials, the upper two quietly, sounding for as
  // long as the note's envelope does.
  for (const [mult, lvl] of [[1, 0.8], [2, 0.12], [3, 0.04]]) {
    const o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.value = v.hz * mult;
    const p = ctx.createGain();
    p.gain.value = lvl;
    o.connect(p);
    p.connect(g);
    run(o, v, sources);
  }
  return held(g, sources);
}

export function playAlarm(v, stage = alertStage()) {
  const { ctx } = stage, g = levelNode(stage, v), m = alarmModulation(), sources = [];
  const o = ctx.createOscillator();
  o.type = "triangle";
  o.frequency.value = m.base;
  const lfo = ctx.createOscillator();
  lfo.type = "square";
  lfo.frequency.value = m.hz;
  const depth = ctx.createGain();
  depth.gain.value = m.depth;
  lfo.connect(depth);
  depth.connect(o.frequency);
  const p = ctx.createGain();
  p.gain.value = ALARM.peak;
  o.connect(p);
  p.connect(g);
  run(o, v, sources);
  run(lfo, v, sources);
  return held(g, sources);
}

// One half second of noise per context, made once.
const noises = new WeakMap();
function noiseBuffer(ctx) {
  if (noises.has(ctx)) return noises.get(ctx);
  const b = ctx.createBuffer(1, Math.floor(ctx.sampleRate / 2), ctx.sampleRate);
  const d = b.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  noises.set(ctx, b);
  return b;
}

export function playBark(v, stage = alertStage()) {
  const { ctx } = stage, g = levelNode(stage, v), sources = [];
  for (const off of barkCalls(v.calls)) {
    const t = v.start + off;
    const syl = ctx.createGain();
    syl.gain.value = 0;
    syl.gain.setValueAtTime(0, t);
    syl.gain.linearRampToValueAtTime(BARK.peak, t + BARK.attack);
    syl.gain.exponentialRampToValueAtTime(0.001, t + BARK.secs);
    syl.connect(g);
    const o = ctx.createOscillator();
    o.type = "sawtooth";
    o.frequency.setValueAtTime(BARK.sweepFrom, t);
    o.frequency.exponentialRampToValueAtTime(BARK.sweepTo, t + BARK.secs * 0.8);
    for (const [hz, q] of [[BARK.formants[0], 5], [BARK.formants[1], 7]]) {
      const bp = ctx.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = hz;
      bp.Q.value = q;
      o.connect(bp);
      bp.connect(syl);
    }
    const n = ctx.createBufferSource();
    n.buffer = noiseBuffer(ctx);
    const nb = ctx.createBiquadFilter();
    nb.type = "bandpass";
    nb.frequency.value = BARK.noiseHz;
    nb.Q.value = 1.2;
    const ng = ctx.createGain();
    ng.gain.value = 0.35;
    n.connect(nb);
    nb.connect(ng);
    ng.connect(syl);
    const stopAt = t + BARK.secs + 0.02;
    for (const s of [o, n]) {
      s.start(t);
      s.stop(stopAt);
      sources.push({ node: s, stopAt });
    }
  }
  return held(g, sources);
}

const decoded = new Map();

export function loadClip(url) {
  const ctx = audioContext();
  if (!decoded.has(url)) {
    decoded.set(url, fetch(withToken(url))
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.arrayBuffer(); })
      .then((b) => ctx.decodeAudioData(b))
      .catch((e) => { decoded.delete(url); throw e; }));
  }
  return decoded.get(url);
}

// The line, said at each of v.plays (seconds after the start), inside the
// voice's one envelope.
export function playVoice(v, buffer, stage = alertStage()) {
  const { ctx } = stage, g = levelNode(stage, v), sources = [];
  const stopAt = Number.isFinite(v.end) ? v.end + TAIL_SECS : Infinity;
  for (const p of v.plays) {
    const s = ctx.createBufferSource();
    s.buffer = buffer;
    s.connect(g);
    s.start(v.start + p);
    if (Number.isFinite(stopAt)) s.stop(stopAt);
    sources.push({ node: s, stopAt });
  }
  return held(g, sources);
}
