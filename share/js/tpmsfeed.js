// One feed of /api/tpms for Home's callout, the banner and the Tires screen.
// Polls while somebody listens and stops when nobody does; slow while there is
// no radio to hear, quicker while a wheel is losing air.

import { withToken } from "./core.js";

const subs = new Set();
let last = null, timer = null, inflight = false;

function nextDelay(st) {
  if (!st || !st.present) return 30000;
  if (st.running && (st.alerts || []).some((a) => a.kind === "rapid")) return 1500;
  return st.running ? 4000 : 15000;
}

async function poll() {
  timer = null;
  if (inflight || !subs.size) return;
  inflight = true;
  try {
    const r = await fetch(withToken("/api/tpms"), { cache: "no-store" });
    if (r.ok) last = await r.json();
  } catch { /* keep the last answer; its age is on the screen */ }
  inflight = false;
  for (const f of [...subs]) { try { f(last); } catch (e) { console.warn("tpms feed:", e); } }
  if (subs.size) timer = setTimeout(poll, nextDelay(last));
}

export const tpmsFeed = {
  get last() { return last; },
  subscribe(fn) {
    subs.add(fn);
    if (last) { try { fn(last); } catch { /* the caller's problem */ } }
    if (!timer && !inflight) timer = setTimeout(poll, 0);
    return () => {
      subs.delete(fn);
      if (!subs.size && timer) { clearTimeout(timer); timer = null; }
    };
  },
  refresh() { if (timer) clearTimeout(timer); timer = setTimeout(poll, 0); },
  async heard() {
    const r = await fetch(withToken("/api/tpms/heard"), { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  },
  async assign(body) {
    const r = await fetch(withToken("/api/tpms/assign"), { method: "POST", cache: "no-store", body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || String(r.status));
    return d;
  },
};
