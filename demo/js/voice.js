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
  try {
    const ctx = audioContext();
    // Not awaited: a context that has not been allowed to start yet may never
    // settle this, and the line must still resolve (see the timer below).
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const now = ctx.currentTime;
    const prev = speaking;
    const from = voiceIO.levelAt("music", now);
    const base = prev ? prev.base : from;
    const low = base - DUCK_DB;
    if (prev) { speaking = null; prev.cut(); }
    voiceIO.schedule("music", glidePlan(from, low, DOWN_SECS).points, now);

    const gain = ctx.createGain();
    gain.gain.value = dbToGain(VOICE_DB);
    gain.connect(ctx.destination);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(gain);
    src.start(now + DOWN_SECS);

    await new Promise((resolve) => {
      let over = false, timer = null;
      const me = { base, cut: () => { finish(false); try { src.stop(); } catch { /* not started */ } } };
      function finish(restore) {
        if (over) return;
        over = true;
        clearTimeout(timer);
        src.onended = null;
        try { src.disconnect(); gain.disconnect(); } catch { /* already */ }
        if (restore && speaking === me) {
          speaking = null;
          voiceIO.schedule("music", glidePlan(low, base, UP_SECS).points, ctx.currentTime);
        }
        resolve();
      }
      src.onended = () => finish(true);
      // A context that is not running never ends a source, so the line also
      // ends on the clock, and the music comes back all the same.
      timer = setTimeout(() => finish(true), (DOWN_SECS + buf.duration + 0.5) * 1000);
      speaking = me;
    });
  } catch { /* no audio here at all: the caption was the line */ }
}
