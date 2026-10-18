// "Hot: charging is hard on the battery right now" -- the one thing the tablet's
// battery ever says unasked. lib/tabletbatt.py decides (over 45 C while
// charging); this only says it, as a toast: the app's quietest surface, no
// banner, no sound (the owner's child sleeps near the tablet).
//
// ONCE PER HOUR, kept in localStorage so a reload does not repeat it. Home shows
// nothing new otherwise; the full story is Settings → Tablet battery.

import { toast, withToken } from "./core.js";
import { warnText } from "./battview.js";

const KEY = "omacar.battwarn";
const POLL_MS = 60000, EVERY_MS = 60 * 60 * 1000, STAY_MS = 12000;
const lastShown = () => { try { return Number(localStorage.getItem(KEY)) || 0; } catch { return 0; } };
let memory = 0;

export async function check(now = Date.now(), fetcher = fetch) {
  let st;
  try {
    const r = await fetcher(withToken("/api/tabletbatt"), { cache: "no-store" });
    if (!r.ok) return false;
    st = await r.json();
  } catch { return false; }
  const words = warnText(st);
  if (!words || st.source === "sim" || now - Math.max(memory, lastShown()) < EVERY_MS) return false;
  memory = now;
  try { localStorage.setItem(KEY, String(now)); } catch { /* the in-memory copy holds */ }
  toast(words, "calm", STAY_MS);
  return true;
}

setInterval(() => { check(); }, POLL_MS);
