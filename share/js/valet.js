// Valet mode: what it hides, what it locks, and how its report reads.
//
// THE RULES LIVE HERE AND NOWHERE ELSE, imported by main.js (routing and the
// chip row), core.js (the lock), privacy.js (valet implies privacy) and home.js
// (cards). No imports: the server's /api/valet is the only thing that knows
// whether valet is on, and this module only reflects it, which is also what
// lets a test import it without a DOM or a network.
//
// HIDING IS A COURTESY, NOT A BOUNDARY, exactly as the tiers are (main.js).
// What the PIN guards is turning valet OFF, and that check is lib/valet.py's.
// Somebody who types an address into a browser on a shared network, or opens a
// terminal on the tablet, is past what this feature promises. It stops the
// person in the driver's seat from stumbling into the owner's data, and it says
// so in the docs (doc/valet.md).

// WHAT IS PERSONAL. Screens by id (the registry in main.js is the only list of
// screens; these are names from it).
export const HIDDEN_VIEWS = new Set([
  "work",       // Agent -> Work: the owner's projects
  "garage",     // Profile: every car you own, plates, VINs, names
  "history",    // Log: drive history, trips and records
  "documents",  // Docs: receipts and registrations
  "report",     // Report: the printable page, with the VIN and the owner
  "replay",     // recorded drives
  "roadcams",   // road cameras: the owner's pins say where they drive
]);

// Home cards that carry personal data or open a hidden screen. The Agent card
// shows the advisor's last question and answer; the Navigation card shows the
// owner's first pinned road camera.
export const HIDDEN_CARDS = new Set(["agent", "nav"]);

export const NOT_AVAILABLE = "Not available in Valet mode";
export const LOCKED_VALET = "Locked in Valet mode";
export const LOCKED_MOVING = "Available when you stop";

const state = {
  known: false, on: false, since: null,
  hasPin: false, noPin: false, configured: false,
  lockedFor: 0, triesLeft: 5, recording: false, starting: false,
  report: null, soFar: null,
};
const listeners = new Set();

export const valet = {
  get on() { return state.on; },
  get since() { return state.since; },
  get hasPin() { return state.hasPin; },
  get noPin() { return state.noPin; },
  get configured() { return state.configured; },
  get lockedFor() { return state.lockedFor; },
  get triesLeft() { return state.triesLeft; },
  get recording() { return state.recording; },
  // Recorded, or about to be: the two states that are not a problem.
  get logging() { return state.recording || state.starting; },
  get report() { return state.report; },
  get soFar() { return state.soFar; },
  get known() { return state.known; },
  // Take the server's word (the body of GET /api/valet). Returns whether
  // anything the app draws changed, so a poll that learns nothing repaints nothing.
  apply(d) {
    if (!d || typeof d !== "object") return false;
    const sig = () => JSON.stringify([state.on, state.hasPin, state.noPin, state.lockedFor > 0,
                                      state.recording, state.report && state.report.ended]);
    const before = sig(), wasOn = state.on;
    state.known = true;
    state.on = !!d.on;
    state.since = d.since || null;
    state.hasPin = !!d.has_pin;
    state.noPin = !!d.no_pin;
    state.configured = !!d.configured;
    state.lockedFor = +d.locked_for || 0;
    state.triesLeft = d.tries_left == null ? 5 : +d.tries_left;
    state.recording = !!d.recording;
    state.starting = !!d.starting;
    state.report = d.last_report || null;
    state.soFar = d.so_far || null;
    // Listeners hear about on/off only. The log flickering between recording
    // and not must repaint a lamp, never rebuild the screen under a thumb.
    if (wasOn !== state.on) for (const fn of listeners) { try { fn(state.on); } catch { /* a listener must not stop the rest */ } }
    return before !== sig();
  },
  onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
};

// ---- the decisions (pure; tested in test/js/valet.test.js) ------------------

// Is this screen out of reach in valet mode.
export const hidesView = (id, on = valet.on) => !!on && HIDDEN_VIEWS.has(id);
export const hidesCard = (id, on = valet.on) => !!on && HIDDEN_CARDS.has(id);

// The words on a greyed control: valet outranks moving, because "available when
// you stop" would be a promise valet does not keep.
export function lockReason(valetOn, moving) {
  if (valetOn) return LOCKED_VALET;
  return moving ? LOCKED_MOVING : "";
}

// Where a deep link to `id` actually lands. Home, always: a hidden screen is
// not a place to be redirected to, and Home is the one screen no mode hides.
export function landing(id, on, home = "home") {
  return hidesView(id, on) ? { id: home, notice: NOT_AVAILABLE } : { id, notice: null };
}

// ---- the PIN pad, as a pure reducer -----------------------------------------

// keys: "0".."9", "back", "clear". Four digits and no more.
export function padKey(digits, key) {
  if (key === "back") return digits.slice(0, -1);
  if (key === "clear") return "";
  if (/^\d$/.test(key) && digits.length < 4) return digits + key;
  return digits;
}

export function cooldownText(sec) {
  const s = Math.max(0, Math.ceil(sec));
  const m = Math.floor(s / 60);
  return m ? `${m}:${String(s % 60).padStart(2, "0")}` : `${s} s`;
}

// ---- the report card ---------------------------------------------------------

const KMH_PER_MPH = 1.609344;

export function duration(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 && m < 10 ? `${m} min ${s % 60} s` : `${m} min`;
  const hr = Math.floor(m / 60);
  return m % 60 ? `${hr} h ${m % 60} min` : `${hr} h`;
}

const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

// "While you were away", in rows the card draws. `imperial`: miles and mph.
// A log that was not running is said to be, with the times it does have, and
// never as a column of zeros: an unknown is not a zero.
export function reportView(rep, { imperial = true, time = clock } = {}) {
  if (!rep) return null;
  const away = rep.ended - rep.started;
  const span = `${time(rep.started)} to ${time(rep.ended)}`;
  const rows = [{ key: "away", label: "Away", value: duration(away), sub: span }];
  if (!rep.tracked) {
    return { rows, tracked: false, simulated: false,
             note: "The valet log was not running, so there is nothing to report beyond the times. "
                 + "Check omacar-valet.service." };
  }
  const spd = (kmh) => imperial ? `${Math.round(kmh / KMH_PER_MPH)} mph` : `${Math.round(kmh)} km/h`;
  const dist = rep.distance_km;
  const far = imperial ? dist / KMH_PER_MPH : dist;
  const fast = imperial ? "80 mph" : "129 km/h";
  const braked = rep.hard_brakes;
  rows.push(
    { key: "top", label: "Top speed", value: spd(rep.top_kmh),
      tone: rep.top_kmh > 80 * KMH_PER_MPH ? "warn" : "" },
    { key: "fast", label: `Above ${fast}`, value: rep.fast_s ? duration(rep.fast_s) : "Never",
      tone: rep.fast_s ? "warn" : "ok" },
    { key: "dist", label: "Distance", value: `${far < 10 ? far.toFixed(1) : Math.round(far)} ${imperial ? "mi" : "km"}`,
      sub: rep.distance_basis === "odometer" ? "from the odometer" : "from the speed" },
    { key: "brakes", label: "Hard stops", value: String(braked),
      tone: braked > 2 ? "warn" : braked ? "" : "ok" });
  return { rows, tracked: true, simulated: !!rep.simulated, note: null };
}
