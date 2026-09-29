import { eq, ok } from "./assert.js";
import { applyAudio, audio, APPLY_TIMEOUT_MS } from "../js/audiostate.js";

// applyAudio() posts /api/audio through the page's own fetch. Here fetch is a
// stand-in that answers only when the test says so, and rejects the way a
// real one does when its signal aborts, so a pending request can be held
// open and counted. Nothing reaches a server.
function holdFetch() {
  const calls = [];
  const real = window.fetch;
  window.fetch = (url, opts) => new Promise((resolve, reject) => {
    calls.push({ url, opts, resolve });
    const signal = opts && opts.signal;
    if (signal) signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
  return { calls, restore() { window.fetch = real; } };
}

const answer = (body) => new Response(JSON.stringify(body), {
  status: 200, headers: { "Content-Type": "application/json" },
});

// Catches only applyAudio()'s abort timer (the one set for APPLY_TIMEOUT_MS),
// so the test can fire it rather than wait 15 s. Every other timer on the
// page goes to the real setTimeout.
function catchAbortTimer() {
  const caught = [];
  const realSet = window.setTimeout, realClear = window.clearTimeout;
  window.setTimeout = (fn, ms, ...rest) => {
    if (ms !== APPLY_TIMEOUT_MS) return realSet(fn, ms, ...rest);
    caught.push({ fn, cleared: false });
    return -caught.length;
  };
  window.clearTimeout = (id) => {
    if (typeof id === "number" && id < 0 && caught[-id - 1]) caught[-id - 1].cleared = true;
    else realClear(id);
  };
  return { caught, restore() { window.setTimeout = realSet; window.clearTimeout = realClear; } };
}

export default [
  ["a second apply while one is pending sends no second request", async () => {
    const f = holdFetch();
    const t = catchAbortTimer();
    try {
      const first = applyAudio();
      const second = applyAudio();
      // Counted before anything is awaited, so a missing guard fails here
      // rather than leaving the page waiting on a request nobody answers.
      eq(f.calls.length, 1, "requests sent while the first was pending");
      eq(await second, audio.last, "the second call's answer (the last known status)");
      f.calls[0].resolve(answer({ volume: 1, managed: true }));
      eq((await first).volume, 1, "the first call's answer");
      ok(t.caught.length === 1 && t.caught[0].cleared, "the abort timer was cleared once it answered");
      const third = applyAudio();
      eq(f.calls.length, 2, "requests sent once the first had answered");
      f.calls[1].resolve(answer({ volume: 1 }));
      await third;
    } finally {
      t.restore();
      f.restore();
    }
  }],
  ["the abort ends a stuck apply and clears the guard", async () => {
    const f = holdFetch();
    const t = catchAbortTimer();
    try {
      const first = applyAudio();
      eq(t.caught.length, 1, "abort timers set");
      eq(f.calls.length, 1, "requests sent");
      t.caught[0].fn();                     // the 15 s are up
      eq(await first, null, "a stuck apply ends as null");
      eq(audio.last, null, "and says so in audio.last");
      const next = applyAudio();
      eq(f.calls.length, 2, "requests sent after the abort");
      f.calls[1].resolve(answer({ volume: 0.6 }));
      eq((await next).volume, 0.6, "the next apply's answer");
    } finally {
      t.restore();
      f.restore();
    }
  }],
];
