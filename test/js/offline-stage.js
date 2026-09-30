// A stage of its own, rendered offline: the helper audiobus.test.js and
// demo-sound.test.js share. Not a test file itself (no .test.js).
//
// audiobus.js builds ONE context for the page, on first use. The same file
// under another URL is another module, and a copy of it built while
// AudioContext hands out an OfflineAudioContext makes that the stage's
// context: the real graph -- the music bus, the alert gate, voiceIn(), the
// limiter -- rendered into memory. Nothing here has a speaker.
//
// POLLED, NOT AWAITED, as sounds.test.js explains: headless Chromium's virtual
// clock does not count an offline render as pending, so the render is waited
// on with messages to ourselves, which spend no virtual time.
import { dbToGain, gainToDb } from "../js/audiobus.js";

const channel = new MessageChannel();
let woken = [];
channel.port1.onmessage = () => { const w = woken; woken = []; for (const f of w) f(); };
const yieldOnce = () => new Promise((r) => { woken.push(r); channel.port2.postMessage(0); });
let stages = 0;

// { stage: the copy of audiobus.js, ctx: its OfflineAudioContext, rate }
export async function offlineStage(secs, rate = 16000) {
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

// The render's samples (channel 0).
export async function rendered(ctx) {
  let buf = null, err = null;
  ctx.startRendering().then((b) => { buf = b; }, (e) => { err = e; });
  for (let i = 0; !buf && !err && i < 500000; i++) await yieldOnce();
  if (err) throw err;
  if (!buf) throw new Error("the offline render never finished");
  return buf.getChannelData(0);
}

// A constant signal at `db` dBFS into `node`, from `from` until `to`.
export function flatInto(ctx, node, db, from = 0, to = Infinity) {
  const s = ctx.createConstantSource();
  s.offset.value = dbToGain(db);
  s.connect(node);
  s.start(from);
  if (Number.isFinite(to)) s.stop(to);
  return s;
}

// The loudest sample in [from, to) seconds, in dBFS.
export function peakOf(data, rate, from, to) {
  let m = 0;
  for (let i = Math.floor(from * rate); i < Math.min(data.length, Math.ceil(to * rate)); i++) m = Math.max(m, Math.abs(data[i]));
  return gainToDb(m);
}
