// THE DEMO'S VOICE (Task 6 of doc/design/2026-09-30-meetup-demo-plan.md).
//
// Short lines rendered once with Piper on the box (tools/demo_voice.py) and
// kept private under share/assets/private/demo/voice/, served at
// /demo-media/voice/<id>.wav. The words are demo/data/voice.json, so a screen
// can caption what is said, and so can anything listening for
// "omacar-demo:say" on the document.
//
// say(id) lowers the music bus 12 dB over 0.4 s, plays the line, and brings
// the music back over 0.8 s: the audio stage's own ramps (ramps.js), so no
// step is ever audible. A line whose file is missing resolves at once, and its
// caption still goes out: a demo that has lost a wav reads, rather than stops.

import { audioContext, schedule, levelAt, dbToGain } from "../../js/audiobus.js";
import { glidePlan } from "../../js/ramps.js";

export const DUCK_DB = 12;
export const DOWN_SECS = 0.4;
export const UP_SECS = 0.8;
// Under full scale: the voice goes straight to the output, past the stage's
// limiter, and Piper normalises its lines to peak near 0 dBFS.
const VOICE_DB = -3;
const BASE = "/demo-media/voice/";

// What say() reaches outside itself, so a test can stand in for each.
export const voiceIO = {
  fetch: (url) => fetch(url),
  schedule: (bus, points, at) => schedule(bus, points, at),
  levelAt: (bus, t) => levelAt(bus, t),
};

// { id: "text" }, filled from voice.json as soon as it arrives. Not a
// top-level await: boot.js imports this module before js/main.js runs, and
// main.js must not wait on a fetch to find the demo's door open.
export const LINES = {};
export const linesReady = fetch(new URL("../data/voice.json", import.meta.url))
  .then((r) => r.json())
  .then((doc) => { for (const x of doc) LINES[x.id] = x.text; return LINES; })
  .catch(() => LINES);

// Decoded once. Only a line that loaded is kept: one that failed is asked for
// again next time, in case the file has arrived since.
const buffers = new Map();

function load(id) {
  if (!buffers.has(id)) {
    const p = (async () => {
      try {
        const r = await voiceIO.fetch(BASE + encodeURIComponent(id) + ".wav");
        if (!r || !r.ok) return null;
        return await audioContext().decodeAudioData(await r.arrayBuffer());
      } catch { return null; }
    })();
    buffers.set(id, p);
    p.then((b) => { if (!b) buffers.delete(id); });
  }
  return buffers.get(id);
}

// The line speaking now, if any. A new line replaces it rather than talking
// over it, and the music stays down between the two.
let speaking = null;

export async function say(id) {
  await linesReady;
  document.dispatchEvent(new CustomEvent("omacar-demo:say", { detail: { id, text: LINES[id] || "" } }));
  const buf = await load(id);
  if (!buf) return;
  let ctx;
  try { ctx = audioContext(); } catch { return; }   // no audio here at all: the caption was the line
  // Not awaited: a context that has not been allowed to start yet may never
  // settle this, and the line must still resolve (see play()'s timer).
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  const now = ctx.currentTime;
  const prev = speaking;
  const from = voiceIO.levelAt("music", now);
  const base = prev ? prev.base : from;
  const low = base - DUCK_DB;
  // THIS LINE OWNS THE DUCK FROM HERE. The one before hands it over without
  // bringing the music back; and however this one ends -- played, cut short,
  // or failing to build or start at all -- the finally brings it back, unless
  // a newer line has taken it on by then.
  const me = { base, cut: () => {} };
  speaking = me;
  if (prev) prev.cut();
  voiceIO.schedule("music", glidePlan(from, low, DOWN_SECS).points, now);
  try {
    await play(ctx, buf, now + DOWN_SECS, me);
  } catch { /* the line could not play: its caption was the line */ }
  finally {
    if (speaking === me) {
      speaking = null;
      voiceIO.schedule("music", glidePlan(low, base, UP_SECS).points, ctx.currentTime);
    }
  }
}

// One decoded line, from `at`. Settles when it ends, when the next line cuts
// it short (me.cut), or on the clock, because a context that is not running
// never ends a source. Rejects if the graph cannot be built or started, and
// leaves nothing connected either way.
function play(ctx, buf, at, me) {
  return new Promise((resolve, reject) => {
    let gain = null, src = null, over = false, timer = null;
    const done = (err) => {
      if (over) return;
      over = true;
      clearTimeout(timer);
      if (src) src.onended = null;
      for (const n of [src, gain]) { try { if (n) n.disconnect(); } catch { /* already */ } }
      if (err) reject(err); else resolve();
    };
    try {
      gain = ctx.createGain();
      gain.gain.value = dbToGain(VOICE_DB);
      gain.connect(ctx.destination);
      src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(gain);
      me.cut = () => { try { src.stop(); } catch { /* not started */ } done(); };
      src.onended = () => done();
      timer = setTimeout(() => done(), (DOWN_SECS + buf.duration + 0.5) * 1000);
      src.start(at);
    } catch (e) { done(e); }
  });
}
