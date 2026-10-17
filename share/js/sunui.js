// Looks by the sun: the part that touches the browser.
//
// The rules are in sunlook.js (pure, tested). This file owns what cannot be
// pure: the once-a-minute tick, localStorage, the light-sensor and GPS polls,
// the slow crossfade, and the Settings block. Off by default; nothing here
// runs a timer's worth of work until the owner turns it on, and nothing here
// ever changes a look while an alert is on screen.
//
// WHERE THE PLACE COMES FROM, in this order (sunlook.resolveLocation):
//   1. a GPS fix, if the server has a /api/location route that answers with one
//      (another feature's; absent is normal, and checked at run time),
//   2. the home city chosen in Settings,
//   3. the time zone's representative city -- "approximate", and it says so.
//
// WHAT COUNTS AS AN ALERT: the drowsy ladder above level 0, a radar alert on
// the banner, or any other feature that announces itself with
//     document.dispatchEvent(new CustomEvent("omacar:alert", { detail: { source: "child", active: true } }))
// (and active: false when it clears). The child-in-the-car alert should do
// exactly that; until it does, nothing here can know about it.

import { h, store, withToken } from "./core.js";
import { savedLook, saveLook, applyLook, lookById } from "./looks.js";
import { radarFeed } from "./radar.js";
import {
  step, freshState, createAmbientGate, resolveLocation, summaryLine, phaseNow, cleanMapping,
  CITIES, cityById, SUN_LOOKS, PHASES, PHASE_LABEL, dayArc, phaseRuns, upcoming,
  validLatLon,
} from "./sunlook.js";

const CFG_KEY = "omacar.sunlooks";
const STATE_KEY = "omacar.sunlooks.state";
const TICK_MS = 60000;
const AMBIENT_POLL_MS = 5000;
const GPS_POLL_MS = 10 * 60000;

const read = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window */ } };

export function sunConfig() {
  const c = read(CFG_KEY) || {};
  return { enabled: c.enabled === true, mapping: cleanMapping(c.mapping), homeId: cityById(c.homeId) ? c.homeId : "" };
}
function saveConfig(c) { write(CFG_KEY, c); }

// ---- live inputs -----------------------------------------------------------------------------------

let state = freshState();
let gps = null;                              // {lat, lon, at}
let ambient = { present: false, lux: null, known: false };
const gate = createAmbientGate();
const alerts = new Map();                    // source -> true while active
let drowsyOn = false;
let busy = false;                            // true while WE change the look
let timers = [];
let started = false;
const subs = new Set();

const reduceMotion = () => {
  try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
};

function alertActive() {
  if (drowsyOn) return true;
  for (const v of alerts.values()) if (v) return true;
  const r = radarFeed.last;
  return !!(r && r.present && r.connected && r.alerts && r.alerts.length);
}

function place() {
  let tz = "", offsetMin = null;
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* no Intl */ }
  try { offsetMin = -new Date().getTimezoneOffset(); } catch { /* none */ }
  const g = gps && Date.now() - gps.at < 6 * 3600000
    ? { lat: gps.lat, lon: gps.lon, ageS: (Date.now() - gps.at) / 1000 } : null;
  return resolveLocation({ gps: g, homeId: sunConfig().homeId, tz, offsetMin });
}

// A clock time like the dashboard's own clock: 6:41, no AM/PM.
export const fmtClock = (ms) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
  .replace(/\s?[AP]M$/i, "");

// ---- the change itself -------------------------------------------------------------------------

// Wear a look, crossfaded. The View Transitions API cross-dissolves the whole
// page between two renderings, gradients and all (a CSS colour transition
// cannot, and would snap every lit surface at once). Reduced motion, or a
// browser without it, simply changes.
function wear(id) {
  busy = true;
  // Stored at once, so a tick in the next few milliseconds sees the look we
  // chose and not a stale one (which it would take for a hand on the toggle).
  saveLook(id);
  const swap = () => applyLook(id);
  const done = () => { busy = false; };
  if (!reduceMotion() && typeof document.startViewTransition === "function") {
    const root = document.documentElement;
    root.classList.add("look-xfade");               // app.css slows the dissolve to four seconds
    const vt = document.startViewTransition(swap);
    vt.updateCallbackDone.catch(() => {}).finally(done);
    vt.finished.catch(() => {}).finally(() => root.classList.remove("look-xfade"));
  } else {
    try { swap(); } finally { done(); }
  }
}

// One decision. `why` is only for the log line.
export function tick(why = "tick", now = Date.now()) {
  const cfg = sunConfig();
  if (!cfg.enabled) { if (state.applied !== null) { state = freshState(); write(STATE_KEY, state); } notify(); return; }
  const loc = place();
  if (!loc) { notify(); return; }
  const dark = gate.feed(ambient.present ? ambient.lux : null, now);
  const r = step(state, {
    enabled: true, phase: phaseNow(now, loc), ambientDark: dark, alert: alertActive(),
    current: savedLook(), mapping: cfg.mapping,
  });
  state = r.state;
  write(STATE_KEY, state);
  if (r.apply && r.apply !== savedLook()) wear(r.apply);
  notify();
}

function notify() { for (const f of [...subs]) { try { f(); } catch { /* a closed sheet */ } } }
export function onSunChange(f) { subs.add(f); return () => subs.delete(f); }

// ---- polls -----------------------------------------------------------------------------------------

async function pollAmbient() {
  if (!sunConfig().enabled) return;
  const was = gate.dark;
  try {
    const r = await fetch(withToken("/api/ambient"), { cache: "no-store" });
    const j = r.ok ? await r.json() : null;
    ambient = j && j.present
      ? { present: true, lux: typeof j.lux === "number" ? j.lux : null, known: true }
      : { present: false, lux: null, known: true };
  } catch { ambient = { present: false, lux: null, known: false }; }
  tick("ambient");
  if (gate.dark !== was) notify();
}

// Another feature's route, and optional: a 404, a non-JSON answer, a fix that is
// not a fix -- all just mean "no GPS".
async function pollGps() {
  if (!sunConfig().enabled) return;
  try {
    const r = await fetch(withToken("/api/location"), { cache: "no-store" });
    if (!r.ok) { gps = null; return; }
    const j = await r.json();
    const f = (j && (j.fix || j)) || {};
    const lat = f.lat ?? f.latitude, lon = f.lon ?? f.lng ?? f.longitude;
    gps = validLatLon(lat, lon) && j.present !== false ? { lat, lon, at: Date.now() } : null;
  } catch { gps = null; }
}

// ---- start / stop --------------------------------------------------------------------------------------

function arm() {
  disarm();
  if (!sunConfig().enabled) return;
  pollGps().then(() => tick("start"));
  pollAmbient();
  timers = [
    setInterval(() => tick("minute"), TICK_MS),
    setInterval(pollAmbient, AMBIENT_POLL_MS),
    setInterval(pollGps, GPS_POLL_MS),
  ];
}
function disarm() { timers.forEach(clearInterval); timers = []; gate.reset(); }

export function startSunLooks() {
  if (started) return;
  started = true;
  const saved = read(STATE_KEY);
  if (saved && typeof saved === "object") state = { ...freshState(), ...saved };
  // A look chosen by hand: tick() notices that the worn look is not the one it
  // applied and holds off until the next boundary. Our own change is ignored.
  document.addEventListener("omacar:look", () => { if (!busy) tick("look"); });
  document.addEventListener("omacar:alert", (e) => {
    const d = (e && e.detail) || {};
    if (d.source) alerts.set(String(d.source), !!d.active);
  });
  import("./drowsyrun.js").then((m) => {
    if (m && m.drowsy && m.drowsy.on) m.drowsy.on((s) => { drowsyOn = !!(s && (s.level > 0 || s.banner || s.testing)); });
  }).catch(() => { /* drowsy mode is not here; nothing to wait for */ });
  arm();
}

export function setSunEnabled(on) {
  const c = sunConfig();
  saveConfig({ ...c, enabled: !!on });
  state = freshState();                        // switching on is a fresh start: wear the right look now
  write(STATE_KEY, state);
  if (on) { started || startSunLooks(); arm(); } else { disarm(); }
  notify();
}
export function setSunMapping(phase, look) {
  const c = sunConfig();
  saveConfig({ ...c, mapping: { ...c.mapping, [phase]: look } });
  if (c.enabled) tick("mapping");
  notify();
}
export function setSunHome(id) {
  const c = sunConfig();
  saveConfig({ ...c, homeId: id });
  if (c.enabled) tick("home");
  notify();
}

// ---- what Settings shows ---------------------------------------------------------------------------------

export function sunStatus(now = Date.now()) {
  const cfg = sunConfig();
  const loc = place();
  const out = { cfg, loc, ambient: { ...ambient, dark: gate.dark }, deferred: !!state.deferred };
  if (!loc) return { ...out, line: "No place to work the sun out for.", phase: null };
  out.phase = phaseNow(now, loc);
  out.line = summaryLine(now, loc, fmtClock);
  out.held = !!state.override;
  return out;
}

const SRC_NOTE = {
  gps: "from the GPS fix",
  home: "the home city you chose",
  timezone: "approximate, from the time zone",
  offset: "approximate, from the clock's UTC offset",
};

// The day's arc, drawn: the sun's height from midnight to midnight, the three
// phases as bands behind it, now as a line, the boundaries labelled. Colours
// are tokens, so it follows whatever look is worn.
function arcSvg(loc, now) {
  const d0 = new Date(now); d0.setHours(0, 0, 0, 0);
  const d1 = new Date(d0); d1.setDate(d1.getDate() + 1);
  const arc = dayArc(d0.getTime(), d1.getTime(), loc, 144);
  const W = 560, H = 104, top = 8, base = H - 20;
  const maxEl = Math.max(20, ...arc.map((p) => p.el));
  const minEl = Math.min(-25, ...arc.map((p) => p.el));
  const X = (f) => (f * W).toFixed(1);
  const Y = (el) => (top + (1 - (el - minEl) / (maxEl - minEl)) * (base - top)).toFixed(1);
  const tone = { day: "var(--warn)", twilight: "var(--info)", night: "var(--faint)" };
  const bands = phaseRuns(arc).map((r) =>
    `<rect x="${X(r.from)}" y="0" width="${(((r.to - r.from) * W) + .5).toFixed(1)}" height="${base}" fill="${tone[r.phase]}" opacity="${r.phase === "night" ? .09 : r.phase === "twilight" ? .2 : .17}"/>`).join("");
  const line = arc.map((p, i) => `${i ? "L" : "M"}${X(i / (arc.length - 1))} ${Y(p.el)}`).join("");
  const f = (now - d0.getTime()) / (d1.getTime() - d0.getTime());
  const nowEl = arc[Math.round(f * (arc.length - 1))].el;
  const marks = upcoming(d0.getTime() - 1, loc, 5).filter((e) => e.at < d1.getTime() && (e.kind === "sunrise" || e.kind === "sunset")).map((e) => {
    const x = (e.at - d0.getTime()) / (d1.getTime() - d0.getTime());
    const anchor = x > .92 ? "end" : x < .08 ? "start" : "middle";
    return `<line x1="${X(x)}" y1="${base}" x2="${X(x)}" y2="${base + 4}" stroke="var(--dim)" stroke-width="1"/>`
         + `<text x="${X(x)}" y="${H - 3}" text-anchor="${anchor}" fill="var(--dim)" font-size="12">${fmtClock(e.at)}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="sun-arc" role="img" aria-label="The sun's height today, with day, twilight and night shaded">`
    + bands
    + `<line x1="0" y1="${Y(0)}" x2="${W}" y2="${Y(0)}" stroke="var(--faint)" stroke-width="1" stroke-dasharray="3 4"/>`
    + `<path d="${line}" fill="none" stroke="var(--ink-2)" stroke-width="2" stroke-linejoin="round"/>`
    + `<line x1="${X(f)}" y1="0" x2="${X(f)}" y2="${base}" stroke="var(--accent)" stroke-width="1.5"/>`
    + `<circle cx="${X(f)}" cy="${Y(nowEl)}" r="4.5" fill="var(--accent)" stroke="var(--panel)" stroke-width="2"/>`
    + marks + `</svg>`;
}

// The block that goes into Settings. `row` is Settings' own whole-width row
// builder, so this looks like its neighbours; `redraw` repaints the sheet.
export function sunSettingsBlock({ row, redraw, toast }) {
  const parked = !store.lockedAsMoving;
  const s = sunStatus();
  const block = h("div.sun-block");
  const on = s.cfg.enabled;

  const lead = row("Looks by the sun",
    on ? s.line + (s.held ? " · held by your choice until the next change" : "")
       : "Day look by day, a dimmer one in twilight, red after dusk. Off until you turn it on.",
    on ? "On" : "Off", () => {
      if (!parked) return;
      setSunEnabled(!on);
      redraw();
      if (!on) toast && toast("Looks by the sun is on. It changes only as the sun crosses a boundary.");
    }, on);
  if (!parked) { lead.disabled = true; lead.title = "Parked only"; }
  block.appendChild(lead);

  if (on || s.loc) {
    const card = h("div.sun-card");
    if (s.loc) {
      const holder = h("div.sun-arc-wrap");
      holder.innerHTML = arcSvg(s.loc, Date.now());   // numbers and fixed strings only
      card.appendChild(holder);
    }
    const cells = h("div.sun-map");
    for (const ph of PHASES) {
      const cur = s.cfg.mapping[ph];
      const b = h("button.sun-cell", {
        type: "button", disabled: !parked || undefined, "data-phase": ph,
        title: "Tap to choose which look this part of the day wears",
        onclick: () => {
          const i = SUN_LOOKS.indexOf(cur);
          setSunMapping(ph, SUN_LOOKS[(i + 1) % SUN_LOOKS.length]);
          redraw();
        },
      }, h("span.sun-cap", PHASE_LABEL[ph]), h("span.sun-val", lookById(cur).label));
      cells.appendChild(b);
    }
    card.appendChild(cells);
    block.appendChild(card);
  }

  // Where. Tapping steps through the cities, then back to "from the time zone".
  const ids = ["", ...CITIES.map((c) => c.id)];
  const locName = s.loc ? s.loc.name : "unknown";
  const where = row("Where the sun is worked out for",
    s.loc ? `${locName} · ${SRC_NOTE[s.loc.source]}` : "No GPS, no home city, and the time zone is not one this knows. Choose a city.",
    s.cfg.homeId ? cityById(s.cfg.homeId).name : (s.loc && s.loc.source === "gps" ? "GPS" : "Time zone"),
    () => {
      if (!parked) return;
      setSunHome(ids[(ids.indexOf(s.cfg.homeId) + 1) % ids.length]);
      redraw();
    });
  if (!parked) { where.disabled = true; where.title = "Parked only"; }
  block.appendChild(where);

  if (on) {
    const a = s.ambient;
    const note = !a.known ? "Light sensor: checking…"
      : !a.present ? "No light sensor found, so the sun alone decides."
      : a.lux === null ? "Light sensor found but not readable this second."
      : `Light sensor: ${a.lux < 10 ? a.lux.toFixed(1) : Math.round(a.lux)} lux${a.dark ? " · dark, wearing the night look" : ""}`;
    block.appendChild(h("div.sun-ambient", note));
  }
  return block;
}
