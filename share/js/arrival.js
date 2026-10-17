// Arriving home, and the fridge for a pickup: what the app says about them.
//
// The data is /api/arrival (lib/arrival.py). Everything here that decides what
// is said is PURE and tested (test/js/arrival.test.js): arrivalView() turns
// that reading into the words, the keys and which of them may be pressed.
//
// THREE THINGS THIS NEVER DOES.
//   * Act by itself. Every key is one tap by a person; nothing opens, cools or
//     runs on arrival (the pickup pre-cool is the one automatic thing, and only
//     after the owner chose a pickup -- or enabled proximity -- and it says so).
//   * Invent a place. With no GPS receiver the card says so; the distance and
//     ETA are never drawn from a guess.
//   * Hide where the position came from. "Simulated" is written every time the
//     simulator is the source.

import { withToken, clockOf } from "./core.js";

export const HOME_ICON = ["M3 12.5 12 4l9 8.5", "M5.5 10.6V20h13v-9.4"];
export const PIN_ICON = ["M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0 1 13 0c0 4.9-6.5 11-6.5 11z",
                         "M12 7.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z"];

const M_PER_MI = 1609.344, FT_PER_M = 3.28084;

export function fmtDist(m, imperial = true) {
  if (m === null || m === undefined) return "—";
  if (imperial) {
    if (m < 160) return `${Math.round(m * FT_PER_M / 10) * 10} ft`;
    const mi = m / M_PER_MI;
    return `${mi < 10 ? mi.toFixed(1) : Math.round(mi)} mi`;
  }
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  const km = m / 1000;
  return `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

export function fmtEta(s) {
  if (s === null || s === undefined) return "";
  if (s < 45) return "under a minute";
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")} min`;
}

export const fmtTemp = (c, imperial = true) =>
  c === null || c === undefined ? "—" : imperial ? `${Math.round(c * 9 / 5 + 32)} °F` : `${(Math.round(c * 2) / 2).toString()} °C`;

// Where the position came from. Tone follows the radar and the fridge: ok =
// the real thing, warn = simulated, none = nothing to report.
export function sourceOf(loc) {
  const l = loc || {};
  if (l.source === "sim") return { text: "Simulated", tone: "warn" };
  if (l.source === "gpsd" && l.fix) return { text: "GPS", tone: "ok" };
  if (l.source === "gpsd") return { text: "GPS, no fix", tone: "" };
  return { text: "No GPS yet", tone: "" };
}

// The pickup line: the words on the card. `st.pickup` is lib/arrival.py's view.
export function pickupView(st, { imperial = true, clock = clockOf } = {}) {
  const pk = st && st.pickup;
  if (!pk) {
    const last = st && st.last_pickup;
    if (last) {
      const back = last.restored_to !== null && last.restored_to !== undefined
        ? `, fridge back to ${fmtTemp(last.restored_to, imperial)}` : "";
      return { state: "done", line: `Pickup at ${last.name} done${back}`, sub: "", name: last.name };
    }
    return null;
  }
  const t = fmtTemp(pk.target_c, imperial);
  const by = pk.by ? clock(pk.by) : "";
  const auto = pk.mode === "auto" ? "Started on its own: you are close. Stop cancels it" : "";
  const base = { name: pk.name, mode: pk.mode, auto: pk.mode === "auto", note: pk.note || "" };
  if (pk.arrived) {
    return { ...base, state: "arrived", line: `At ${pk.name}`,
             sub: pk.base_c !== null && pk.base_c !== undefined
               ? `Fridge goes back to ${fmtTemp(pk.base_c, imperial)} when you leave` : "" };
  }
  if (pk.cooling) {
    return { ...base, state: "cooling",
             line: `Cooling for your pickup at ${pk.name}` + (by ? ` — ${t} by ${by}` : ` — ${t}`),
             sub: pk.base_c !== null && pk.base_c !== undefined
               ? `Back to ${fmtTemp(pk.base_c, imperial)} after the pickup` : auto };
  }
  if (pk.eta_s !== null && pk.eta_s !== undefined) {
    const lead = pk.starts_in_s;
    const when = lead > 0 ? `cooling starts in ${fmtEta(lead)}` : "cooling now";
    return { ...base, state: "heading", line: `Heading to ${pk.name} — ${when}`,
             sub: t !== "—" && by ? `${t} by ${by}` : auto };
  }
  return { ...base, state: "no-eta", line: `Heading to ${pk.name}`,
           sub: "No GPS fix, so no ETA: say how long it will take", needsEta: true };
}

// The whole of it. `st` is /api/arrival (or null before the first read).
export function arrivalView(st, { imperial = true, clock = clockOf } = {}) {
  if (!st || !st.arrival) {
    return { state: "unknown", source: "", sourceTone: "", headline: "Arrival", line: "Waiting for the app",
             show: false, keys: [], pickup: null, watching: false, hasHome: false, pickups: [] };
  }
  const a = st.arrival, src = sourceOf(st.location);
  const hasHome = !!(st.places && st.places.home);
  const pickups = (st.places && st.places.pickups) || [];
  const act = st.actions || {};
  const eco = act.fridge || {};
  const keys = [
    { id: "garage", label: "Open garage", ok: !!(act.garage && act.garage.available),
      why: (act.garage && act.garage.why) || "" },
    { id: "fridge", label: "Fridge to eco",
      sub: eco.eco_c !== undefined ? fmtTemp(eco.eco_c, imperial) : "",
      ok: !!eco.available, why: eco.why || "" },
    { id: "house", label: "House", ok: !!(act.house && act.house.available),
      why: (act.house && act.house.why) || "" },
  ];
  const near = a.phase === "approaching" || a.phase === "arrived";
  const show = near && !a.dismissed;
  let headline, line, state;
  if (!hasHome) {
    state = "no-home"; headline = "Home"; line = "Set your home in Navigation, Places";
  } else if (a.phase === "no_fix" || a.phase === "no_home") {
    state = "no-fix"; headline = "Distance to home";
    line = st.location && st.location.source === "gpsd" ? "Waiting for a GPS fix" : "No GPS receiver yet";
  } else if (a.phase === "arrived") {
    state = "arrived"; headline = "You're home"; line = "";
  } else if (a.phase === "approaching") {
    state = "near"; headline = "Nearly home";
    line = [fmtDist(a.dist_m, imperial), a.eta_s !== null && a.eta_s !== undefined ? `about ${fmtEta(a.eta_s)}` : ""]
      .filter(Boolean).join(" · ");
  } else {
    state = "away"; headline = "Home is";
    line = fmtDist(a.dist_m, imperial) + (a.eta_s !== null && a.eta_s !== undefined ? ` · ${fmtEta(a.eta_s)}` : "");
  }
  return {
    state, source: src.text, sourceTone: src.tone, headline, line, show, near,
    distText: fmtDist(a.dist_m, imperial),
    keys, hasHome, pickups,
    watching: !!st.watching,
    watchNote: st.watching ? "" : "The arrival service is not running (omacar-arrival)",
    pickup: pickupView(st, { imperial, clock }),
  };
}

// ---- one feed, shared by the card, the banner and the screens ------------------------------

const subs = new Set();
let last = null, timer = null, inflight = false;

async function poll() {
  timer = null;
  if (inflight) return;
  inflight = true;
  try {
    const r = await fetch(withToken("/api/arrival"), { cache: "no-store" });
    if (r.ok) last = await r.json();
  } catch { /* keep the last reading */ }
  inflight = false;
  for (const fn of subs) { try { fn(last); } catch { /* the caller's problem */ } }
  if (subs.size) timer = setTimeout(poll, 2000);
}

export const arrivalFeed = {
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
  refresh() { if (timer) clearTimeout(timer); timer = setTimeout(poll, 0); },
};

export function ask(body) {
  return fetch(withToken("/api/arrival"), {
    method: "POST", cache: "no-store", body: JSON.stringify(body),
  }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `error ${r.status}`);
    arrivalFeed.refresh();
    return j;
  });
}

export function postPlaces(body) {
  return fetch(withToken("/api/places"), {
    method: "POST", cache: "no-store", body: JSON.stringify(body),
  }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `error ${r.status}`);
    arrivalFeed.refresh();
    return j;
  });
}
