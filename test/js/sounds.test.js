import { eq, ok } from "./assert.js";
import { CHIME, ALARM, BARK, alarmModulation, barkCalls } from "../js/sounds.js";
import { playChimeNote, playAlarm, playBark, playVoice } from "../js/sounds.js";
import { PEAK, VOICE_PEAK_DB, clipTrim } from "../js/sounds.js";
import { voicesFor, releaseVoices, ALERT_DB, CHIME_NOTE_DB } from "../js/alertplayer.js";
import { SILENCE_DB, dbAt } from "../js/ramps.js";

// ---- rendered, not played ----------------------------------------------------
// Each player is run for real into an OfflineAudioContext and its samples are
// measured. Nothing here has a speaker: an offline context renders into memory.
//
// POLLED, NOT AWAITED. Headless Chromium runs this page on a virtual clock
// (js_test.py's --virtual-time-budget) that fast-forwards whenever nothing is
// pending, and an offline render in flight does not count as pending: an
// awaited startRendering() never came back there, and the page was dumped
// unfinished. So the render is waited on by polling -- with a message to
// ourselves, not a timer: a 1 ms timer spent virtual time at up to fifty
// times the real rate (the page's whole 8 s budget went on four renders),
// where a message spends none, measured on the box.
const channel = new MessageChannel();
let woken = [];
channel.port1.onmessage = () => { const w = woken; woken = []; for (const f of w) f(); };
const yieldOnce = () => new Promise((r) => { woken.push(r); channel.port2.postMessage(0); });
async function render(secs, rate, build, during) {
  const ctx = new OfflineAudioContext(1, Math.ceil(secs * rate), rate);
  build({ ctx, out: ctx.destination });
  if (during) during(ctx);
  let buf = null, err = null;
  ctx.startRendering().then((b) => { buf = b; }, (e) => { err = e; });
  for (let i = 0; !buf && !err && i < 500000; i++) await yieldOnce();
  if (err) throw err;
  if (!buf) throw new Error("the offline render never finished");
  return { data: buf.getChannelData(0), rate };
}
const db = (x) => (x > 0 ? 20 * Math.log10(x) : -Infinity);
// The loudest sample in [from, to) seconds, in dBFS.
function peakDb({ data, rate }, from, to) {
  let m = 0;
  for (let i = Math.max(0, Math.floor(from * rate)); i < Math.min(data.length, Math.ceil(to * rate)); i++) m = Math.max(m, Math.abs(data[i]));
  return db(m);
}
// A constant line, so the rendered voice IS its level node's gain, sample by
// sample, LINE_DB under it. LINE_DB is the loudest a clip may be (sounds.js
// trims anything louder), so this one passes untrimmed.
const LINE_DB = VOICE_PEAK_DB;
function flat(ctx, secs, value = PEAK.voice) {
  const b = ctx.createBuffer(1, Math.ceil(secs * ctx.sampleRate), ctx.sampleRate);
  b.getChannelData(0).fill(value);
  return b;
}
// The rendered level of a flat voice between two times: its worst 100 ms, its
// largest sample-to-sample move, and its greatest distance from the plan.
function measure(r, env, from, to) {
  const lv = (x) => db(x) - LINE_DB;
  const at = (t) => lv(r.data[Math.round(t * r.rate)]);
  let step = 0, jump = 0, off = 0;
  for (let t = from; t + 0.1 <= to; t += 0.01) step = Math.max(step, Math.abs(at(t + 0.1) - at(t)));
  const i0 = Math.ceil(from * r.rate), i1 = Math.floor(to * r.rate);
  for (let i = i0 + 1; i <= i1; i++) jump = Math.max(jump, Math.abs(db(r.data[i]) - db(r.data[i - 1])));
  for (let i = i0; i <= i1; i += 8) off = Math.max(off, Math.abs(lv(r.data[i]) - dbAt(env, i / r.rate)));
  return { step, jump, off };
}
// Take cancelAndHoldAtTime away, as Firefox has none, for the length of fn.
async function withoutHold(fn) {
  const had = Object.getOwnPropertyDescriptor(AudioParam.prototype, "cancelAndHoldAtTime");
  Object.defineProperty(AudioParam.prototype, "cancelAndHoldAtTime", { value: undefined, configurable: true, writable: true });
  try { return await fn(); } finally { Object.defineProperty(AudioParam.prototype, "cancelAndHoldAtTime", had); }
}

// A flat Level 2 line starting at 0.1 s, with one saying as long as its
// envelope, let go at `now` from inside the render, as the player would.
async function releasedVoice(now) {
  const v = Object.assign(voicesFor({ kind: "voice", clip: "l2" }, 2, 0.1, 1.8)[0], { plays: [0] });
  let h = null, r = null;
  const out = await render(5.2, 3200, (st) => { h = playVoice(v, flat(st.ctx, 5), st); }, (ctx) => {
    ctx.suspend(now).then(() => {
      [r] = releaseVoices([v], ctx.currentTime);
      h.release(r.released, r.env.filter(([t]) => t >= r.released), r.end);
      ctx.resume();
    });
  });
  return { v, r, out };
}

export default [
  ["the chime is two notes, the second higher and later", () => {
    eq(CHIME.notes.length, 2);
    ok(CHIME.notes[1].hz > CHIME.notes[0].hz && CHIME.notes[1].at > CHIME.notes[0].at, "rising, one after the other");
  }],
  ["the alarm's two tones sit inside ISO 7731's 500-1500 Hz", () =>
    ok(ALARM.tones.length === 2 && ALARM.tones.every((hz) => hz >= 500 && hz <= 1500), String(ALARM.tones))],
  ["and it swings between them every quarter second, for as long as it sounds", () => {
    const m = alarmModulation();
    eq([m.base - m.depth, m.base + m.depth, 1 / (2 * m.hz)], [700, 1000, 0.25]);
  }],
  ["a bark call is two barks", () => eq(barkCalls(1), [0, BARK.gap])],
  ["three calls carry a bark through a Level 2 rise", () => eq(barkCalls(3).length, 6)],
  ["each bark falls in pitch, fast", () => ok(BARK.sweepTo < BARK.sweepFrom && BARK.secs <= 0.2, "a fast downward sweep")],
  ["nothing is louder than full scale", () => ok([ALARM.peak, BARK.peak].every((p) => p > 0 && p <= 1), "every peak in (0, 1]")],

  // ---- the first sample of every sound, rendered ---------------------------
  ["a chime note, rendered, is silent until it starts and starts at silence", async () => {
    const [v] = voicesFor({ kind: "chime" }, 1, 0.1);
    const r = await render(2, 8000, (st) => playChimeNote(v, st));
    const onset = peakDb(r, 0.1, 0.11), full = peakDb(r, 1.7, 1.8);
    eq(peakDb(r, 0, 0.1), -Infinity, "nothing before it starts");
    ok(onset - full <= SILENCE_DB - CHIME_NOTE_DB + 1.5, `its first 10 ms are ${(full - onset).toFixed(1)} dB under its held level`);
    ok(full <= CHIME_NOTE_DB + 0.01 && full > CHIME_NOTE_DB - 4, `and it holds at ${full.toFixed(1)} dBFS`);
  }],
  ["the alarm, rendered, starts at silence and holds at full scale with no cap", async () => {
    const [v] = voicesFor({ kind: "alarm", hold: true }, 3, 0.1);
    const r = await render(3.6, 8000, (st) => playAlarm(v, st));
    const onset = peakDb(r, 0.1, 0.12), full = peakDb(r, 3.2, 3.6);
    eq(peakDb(r, 0, 0.1), -Infinity, "nothing before it starts");
    ok(onset - full <= SILENCE_DB - ALERT_DB[3] + 1.5, `its first 20 ms are ${(full - onset).toFixed(1)} dB under full`);
    // Just under ALARM.peak: at this rate the band-limited triangle loses its
    // upper harmonics, and with them a decibel or so of its peak.
    ok(full <= db(ALARM.peak) + 0.01 && full > db(ALARM.peak) - 2, `and it holds at ${full.toFixed(1)} dBFS`);
  }],
  ["a bark, rendered, starts at silence: its first call is far under the calls at its level", async () => {
    const [v] = voicesFor({ kind: "bark" }, 2, 0.1);
    const r = await render(2.3, 16000, (st) => playBark(v, st));
    const firstCall = peakDb(r, 0.1, 0.3), heldCall = peakDb(r, 1.7, 1.9);
    eq(peakDb(r, 0, 0.1), -Infinity, "nothing before it starts");
    ok(firstCall - heldCall <= SILENCE_DB + 6 - ALERT_DB[2], `${(heldCall - firstCall).toFixed(1)} dB under`);
  }],
  ["a voice line, rendered flat, follows its envelope from silence to silence, sample by sample", async () => {
    const v = Object.assign(voicesFor({ kind: "voice", clip: "l1" }, 1, 0.1, 2.8)[0], { plays: [0] });
    const r = await render(9.2, 3200, (st) => playVoice(v, flat(st.ctx, 9.2), st));
    eq(+(db(r.data[Math.round(0.1 * 3200)]) - LINE_DB).toFixed(2), SILENCE_DB, "its first sample");
    const m = measure(r, v.env, 0.1, v.end - 0.01);
    ok(m.step <= 3.05 && m.jump <= 0.1 && m.off <= 0.1, JSON.stringify(m));
  }],

  // ---- letting go, rendered ------------------------------------------------
  ["let go mid-rise, a line falls to silence from exactly where it was", async () => {
    const { v, r, out } = await releasedVoice(0.84);
    ok(r.released > 0.84 && r.released <= 0.94 + 1e-9, `from the envelope's next point, ${r.released}`);
    const m = measure(out, r.env, 0.1, r.end - 0.01);
    ok(m.step <= 3.05 && m.jump <= 0.1 && m.off <= 0.1, JSON.stringify(m));
    ok(r.end < v.end, "and is over sooner than it would have been");
    eq(peakDb(out, r.end + 0.06, 5.2), -Infinity, "its sources stop once it is silent");
  }],
  ["and just the same without cancelAndHoldAtTime, as in Firefox: no step", async () => {
    const { r, out } = await withoutHold(() => releasedVoice(0.84));
    const m = measure(out, r.env, 0.1, r.end - 0.01);
    ok(m.step <= 3.05 && m.jump <= 0.1 && m.off <= 0.1, JSON.stringify(m));
  }],
  ["let go while it holds, without cancelAndHoldAtTime: no step either", async () => {
    const { r, out } = await withoutHold(() => releasedVoice(1.8));
    eq(+r.released.toFixed(6), 1.8, "let go at once, since it was standing still");
    const m = measure(out, r.env, 0.1, r.end - 0.01);
    ok(m.step <= 3.05 && m.jump <= 0.1 && m.off <= 0.1, JSON.stringify(m));
  }],
  ["the held Level 3 alarm, let go, falls to silence over 3 s and stops", async () => {
    for (const hold of [true, false]) {
      const [v] = voicesFor({ kind: "alarm", hold: true }, 3, 0.1);
      let h = null, rel = null;
      const go = () => render(7.2, 8000, (st) => { h = playAlarm(v, st); }, (ctx) => {
        ctx.suspend(3.6).then(() => {
          [rel] = releaseVoices([v], ctx.currentTime);
          h.release(rel.released, rel.env.filter(([t]) => t >= rel.released), rel.end);
          ctx.resume();
        });
      });
      const r = hold ? await go() : await withoutHold(go);
      const full = peakDb(r, 3.3, 3.6), tail = peakDb(r, rel.end - 0.1, rel.end);
      eq(+rel.end.toFixed(6), 6.6, "3 s after it was let go");
      ok(tail - full <= SILENCE_DB + 3, `${hold ? "" : "without cancelAndHoldAtTime: "}its last 100 ms are ${(full - tail).toFixed(1)} dB under full`);
      ok(peakDb(r, rel.end - 1.6, rel.end - 1.5) - full < -20, "falling all the way, not held and then cut");
      eq(peakDb(r, rel.end + 0.06, 7.2), -Infinity, "and then nothing: its oscillators have stopped");
    }
  }],

  // ---- fix round 1: each sound's content peak is what the budget says ------
  ["each sound, rendered at its level, peaks no higher than its budget says", async () => {
    const check = (kind, r, from, to, env) => {
      const p = peakDb(r, from, to), bound = env + db(PEAK[kind]);
      ok(p <= bound + 0.01, `${kind}: ${p.toFixed(2)} dBFS against a budget of ${bound.toFixed(2)}`);
    };
    const [c] = voicesFor({ kind: "chime" }, 1, 0.1);
    check("chime", await render(3, 8000, (st) => playChimeNote(c, st)), 1.6, 2.6, CHIME_NOTE_DB);
    const [a] = voicesFor({ kind: "alarm", hold: true }, 3, 0.1);
    check("alarm", await render(4, 16000, (st) => playAlarm(a, st)), 3.1, 4, ALERT_DB[3]);
    for (let i = 0; i < 4; i++) {
      const [b] = voicesFor({ kind: "bark" }, 2, 0.1);
      check("bark", await render(2.3, 16000, (st) => playBark(b, st)), 0.1, 2.3, ALERT_DB[2]);
    }
  }],
  ["a clip louder than the voice's peak is trimmed to it, once; a quieter one is left as it is", async () => {
    const ctx = new OfflineAudioContext(1, 800, 8000);
    const loud = flat(ctx, 0.1, 1), quiet = flat(ctx, 0.1, 0.5);
    eq([+db(clipTrim(loud)).toFixed(6), clipTrim(quiet), clipTrim(loud) === clipTrim(loud)], [VOICE_PEAK_DB, 1, true]);
    const v = Object.assign(voicesFor({ kind: "voice", clip: "l1" }, 1, 0.1, 2.8)[0], { plays: [0] });
    const r = await render(6, 3200, (st) => playVoice(v, flat(st.ctx, 6, 1), st));
    const held = v.start + v.rise + 0.5;   // on its hold, at its level
    eq([+dbAt(v.env, held).toFixed(6), +peakDb(r, held, held + 0.2).toFixed(2)], [-7, -7 + VOICE_PEAK_DB],
       "a full-scale line plays at its level less 2 dB");
  }],
];
