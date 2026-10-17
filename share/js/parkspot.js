// Where did I park? What the app says about the last park (lib/parkspot.py).
//
// The data is /api/parking. Everything that decides what is said is PURE and
// tested (test/js/parkspot.test.js): parkView() turns a park into words.
//
// THREE THINGS THIS NEVER DOES.
//   * Invent a place. A park with no GPS fix says "No GPS fix at the time";
//     there is never a coordinate, a map or an address without one.
//   * Present the level as a measurement. "level ≈ P3 (estimate)" is air
//     pressure against the street, good to about a level, and the word
//     "estimate" is always on it.
//   * Hide that it is a rehearsal. A simulated park says "Simulated" first.
//
// The name is parkspot, not parking: share/js/parking.js is the countdown card
// for the tablet's own sleep.

import { withToken } from "./core.js";

export const PARK_ICON = ["M5 4h8a5 5 0 0 1 0 10H9v6", "M9 8.5h4"];   // a P
export const COPY_ICON = ["M9 9h10v11H9z", "M5 15V4h10"];

const pad = (n) => String(n).padStart(2, "0");
export const clock = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

// "14:32" today, "Yesterday 14:32", else "Mon 14:32".
export function whenOf(t, now = Date.now() / 1000) {
  const d = new Date(t * 1000), n = new Date(now * 1000);
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(n) - day(d)) / 86400000);
  if (diff <= 0) return clock(t);
  if (diff === 1) return `Yesterday ${clock(t)}`;
  return `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()]} ${clock(t)}`;
}

export function agoOf(t, now = Date.now() / 1000) {
  const s = Math.max(0, now - t);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const coords = (fix) => `${fix.lat.toFixed(5)}, ${fix.lon.toFixed(5)}`;

// What "Copy" puts on the clipboard: latitude,longitude with no spaces, which
// every maps app takes in its search box.
export const mapsText = (fix) => `${fix.lat.toFixed(6)},${fix.lon.toFixed(6)}`;

function fixView(fix) {
  if (!fix || typeof fix.lat !== "number" || typeof fix.lon !== "number") {
    return { has: false, text: "No GPS fix at the time", copy: null, accuracy: "" };
  }
  const acc = typeof fix.accuracy_m === "number" ? `± ${Math.round(fix.accuracy_m)} m` : "";
  const old = fix.age_s > 5 ? `last fix ${fix.age_s < 90 ? `${fix.age_s} s` : `${Math.round(fix.age_s / 60)} min`} before` : "";
  return { has: true, text: coords(fix), copy: mapsText(fix), accuracy: [acc, old].filter(Boolean).join(" · ") };
}

// The level. Only ever with the word "estimate". null when the park has no
// pressure reading or no street reference to set it against.
function levelView(level) {
  if (!level || typeof level.label !== "string") return null;
  const street = level.label === "street level";
  return { label: level.label, street,
           line: street ? "street level (estimate)" : `level ≈ ${level.label} (estimate)`,
           detail: level.detail || "" };
}

export function parkRow(p, now = Date.now() / 1000) {
  const f = fixView(p.fix), lv = levelView(p.level);
  const photos = p.photos || {};
  return {
    id: p.id, when: whenOf(p.at, now), ago: agoOf(p.at, now), clock: clock(p.at),
    simulated: !!p.simulated,
    fix: f, level: lv,
    stills: ["rear", "front"].map((role) => ({
      role, label: role === "rear" ? "Rear" : "Front", url: photos[role] || null,
      note: photos[role] ? "" : `No ${role} photo`,
    })),
    photoCount: Object.keys(photos).length,
  };
}

// What a screen shows. `pub` is /api/parking; `pick` the id the person chose in
// the history (default: the newest).
export function parkView(pub, { now = Date.now() / 1000, pick = null } = {}) {
  if (!pub || !pub.present || !pub.last) {
    return { state: "none", title: "Where I parked", source: "", sourceTone: "",
             line: "Nothing saved yet",
             sub: "The spot is saved when the car is turned off: the place, a picture front and back, and the level in a garage.",
             current: null, history: [] };
  }
  const rows = (pub.history || [pub.last]).map((p) => parkRow(p, now));
  const cur = rows.find((r) => r.id === pick) || rows[0];
  const sim = cur.simulated;
  return {
    state: "park", title: "Where I parked",
    source: sim ? "Simulated" : cur.fix.has ? "GPS" : "No GPS fix",
    sourceTone: sim ? "warn" : cur.fix.has ? "ok" : "",
    line: cur.when, sub: cur.ago,
    current: cur, history: rows,
  };
}

// ---- the feed ---------------------------------------------------------------

let last = null, timer = null, inflight = false;
const subs = new Set();

async function poll() {
  timer = null; inflight = true;
  try {
    const r = await fetch(withToken("/api/parking"), { cache: "no-store" });
    if (r.ok) last = await r.json();
  } catch { /* keep the last reading */ }
  inflight = false;
  for (const fn of subs) { try { fn(last); } catch { /* the caller's problem */ } }
  if (subs.size) timer = setTimeout(poll, 6000);
}

export const parkFeed = {
  get last() { return last; },
  subscribe(fn) {
    subs.add(fn);
    if (last) { try { fn(last); } catch { /* ignore */ } }
    if (!timer && !inflight) timer = setTimeout(poll, 0);
    return () => {
      subs.delete(fn);
      if (!subs.size && timer) { clearTimeout(timer); timer = null; }
    };
  },
};

// The clipboard, with the old way for a page the browser calls insecure.
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}
