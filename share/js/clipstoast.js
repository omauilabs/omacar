// "Clip saved", silent: the small toast that says an incident was kept.
//
// The clip service (lib/clips.py) writes the newest incident's id into its
// state file; this asks /api/clips/last every few seconds, one request at a
// time and given up after LIVE_TIMEOUT_MS, and toasts when the id changes. The
// first answer after the page opens only sets the baseline, so opening the app
// never toasts a clip from an hour ago. It plays no sound: the owner's child
// sleeps near the tablet at night.
//
// ONE SHORT REQUEST. Chromium lets a host six connections and the Cameras
// tab's streams, drowsy mode and the phone layer already hold several of them
// (views/cameras.js); this adds one that ends in a few milliseconds.
import { toast } from "./core.js";
import { getJSON } from "./camapi.js";
import { LIVE_TIMEOUT_MS } from "./drowsyrun.js";
import { toastFor } from "./clipslogic.js";

export const POLL_MS = 3000;

let timer = null, busy = false, prev, started = false;

async function poll(get = getJSON) {
  if (busy) return;
  busy = true;
  const ctl = new AbortController();
  const bail = setTimeout(() => ctl.abort(), LIVE_TIMEOUT_MS);
  try {
    const d = await get("/api/clips/last", { signal: ctl.signal });
    const r = toastFor(prev, d && d.last, !!(d && d.running));
    prev = r.id;
    if (r.say) toast(r.say, "ok", 2600);
  } catch { /* a miss: the next tick asks again */ }
  finally { clearTimeout(bail); busy = false; }
}

export function startClipToast() {
  if (started) return;
  started = true;
  poll();
  timer = setInterval(poll, POLL_MS);
}

// After a tap on Save clip: ask again soon, a few times, so the toast follows
// the tap within a moment rather than within three seconds.
export function pokeClipToast() {
  let n = 0;
  const id = setInterval(() => { poll(); if (++n >= 6) clearInterval(id); }, 500);
}

export function stopClipToast() { clearInterval(timer); started = false; prev = undefined; }
