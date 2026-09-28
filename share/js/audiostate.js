// The Surface's audio path as lib/audio.py sees it: the port, the volume, and
// whether `omacar audio on` asked for 100% to be held here. Applied at start
// and every half minute by alertness.js, and read by whatever shows it.
import { postJSON } from "./camapi.js";

export const audio = { last: null };
const fns = new Set();

// pin() (lib/audio.py, fix round 2) is a closed loop that can legitimately
// take a couple of seconds to finish a ramp, and it already refuses to run
// two ramps at once with its own lock. But that lock lives on the SERVER;
// nothing stopped this PAGE from firing a second /api/audio POST while the
// first was still out, and browsers cap concurrent requests per host at 6 --
// so a stuck request (a wedged wpctl, a slow ramp) could pile enough of
// those up to start starving the camera streams and every other /api call.
// This flag is the client-side half of the same rule: never start a second
// apply while one is already in flight.
let inFlight = false;

export function onAudio(fn) { fns.add(fn); fn(audio.last); return () => fns.delete(fn); }

export async function applyAudio() {
  if (inFlight) return audio.last;      // one is already out; do not stack another
  inFlight = true;
  const ctl = new AbortController();
  // pin()'s own hard bound is 8 s; 15 s leaves it room to finish and still
  // guarantees this can never hang forever holding one of the browser's 6
  // connections to this host.
  const bail = setTimeout(() => ctl.abort(), 15000);
  try {
    audio.last = await postJSON("/api/audio", { action: "apply" }, { signal: ctl.signal });
  } catch {
    audio.last = null;
  } finally {
    clearTimeout(bail);
    inFlight = false;
  }
  for (const fn of fns) fn(audio.last);
  return audio.last;
}

// "AUX disconnected" only when the port is known to be the speakers. Unknown
// is not the same as unplugged, and saying so would be a guess.
export function auxLine(a) {
  return a && a.aux === false ? "AUX disconnected — sound is on the tablet's speakers" : "";
}
