// The Surface's audio path as lib/audio.py sees it: the port, the volume, and
// whether `omacar audio on` asked for 100% to be held here. Applied at start
// and every half minute by alertness.js, and read by whatever shows it.
import { postJSON } from "./camapi.js";

export const audio = { last: null };
const fns = new Set();

export function onAudio(fn) { fns.add(fn); fn(audio.last); return () => fns.delete(fn); }

export async function applyAudio() {
  try { audio.last = await postJSON("/api/audio", { action: "apply" }); } catch { audio.last = null; }
  for (const fn of fns) fn(audio.last);
  return audio.last;
}

// "AUX disconnected" only when the port is known to be the speakers. Unknown
// is not the same as unplugged, and saying so would be a guess.
export function auxLine(a) {
  return a && a.aux === false ? "AUX disconnected — sound is on the tablet's speakers" : "";
}
