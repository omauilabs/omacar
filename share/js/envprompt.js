// "Cabin air is stale" -- the one thing the environment sensors ever say
// unasked, and the gentlest way this app has of saying it.
//
// THE RULE (lib/envnode.py keeps the clock): cabin CO2 over 1,500 ppm for five
// minutes while the car is moving. Drowsiness has a second cause besides
// being tired, and a closed cabin full of breath is the cheap one to fix.
//
// HOW IT IS SAID, and why: a toast. It is the app's quietest surface -- a card
// at the corner that fades on its own, no modal, no banner across the road, no
// button to hunt for. The drowsy banner and cards are for a driver who is
// failing; this is for a cabin that is. NO SOUND, EVER: the owner's child
// sleeps near the tablet at night, and the stale-air word is not worth waking
// anybody. A notify-send from watch.py was the other candidate and was
// refused: the desktop's notification daemon decides whether that chimes.
//
// ONE PER 20 MINUTES (kept in localStorage, so a reload does not repeat it),
// and NEVER DURING A DROWSY ALERT: while the engine is at any level above
// zero, showing the banner, or running its test, the prompt waits -- still
// due -- and comes up once the alert has cleared.

import { toast, withToken } from "./core.js";
import { drowsy } from "./drowsyrun.js";
import { shouldPrompt, promptText } from "./envview.js";

const KEY = "omacar.envprompt";
const POLL_MS = 15000;
const STAY_MS = 14000;

const lastShown = () => { try { return Number(localStorage.getItem(KEY)) || 0; } catch { return 0; } };
const remember = (t) => { try { localStorage.setItem(KEY, String(t)); } catch { /* private mode: the in-memory copy below holds */ } };

let memory = 0;
let dz = { level: 0, banner: false, testing: false };
drowsy.on((s) => { dz = s; });

export async function check(now = Date.now(), fetcher = fetch) {
  let st;
  try {
    const r = await fetcher(withToken("/api/env"), { cache: "no-store" });
    if (!r.ok) return false;
    st = await r.json();
  } catch { return false; }
  if (!shouldPrompt(st, dz, now, Math.max(memory, lastShown()))) return false;
  memory = now;
  remember(now);
  toast(promptText(st.prompt.ppm), "calm", STAY_MS);
  return true;
}

setInterval(() => { check(); }, POLL_MS);
