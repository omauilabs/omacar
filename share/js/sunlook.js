// Looks by the sun: the decisions, with no clock, no storage and no DOM.
//
// share/js/sunui.js owns the timers, localStorage and the crossfade and calls
// into this; everything here is a function of its arguments, so the rules the
// owner cares about are tested (test/js/sunlook.test.js) rather than hoped for:
//
//   * a look changes only when the sun crosses a boundary, never mid-phase;
//   * a hand on the toggle wins until the NEXT boundary;
//   * nothing changes while an alert (drowsy, radar, child) is on the screen;
//   * a tunnel or garage can force night after 20 s dark and release it after
//     20 s of light, and a missing light sensor just means "sun only".
//
// Solar maths: share/js/solar.js (NOAA's algorithm).

import { sunTimes, phaseAt, elevationAt } from "./solar.js";

export const PHASES = ["day", "twilight", "night"];
export const PHASE_LABEL = { day: "Day", twilight: "Twilight", night: "Night" };

// The owner's defaults: the Daylight look by day, Night-dim through civil
// twilight, Night-red after dusk. Each is a look id from looks.js.
export const DEFAULT_MAPPING = { day: "day", twilight: "dim", night: "red" };

// Looks worth offering for a phase. Not every look belongs on the sun: the
// Matrix rain and Aurora are for show, not for the hour.
export const SUN_LOOKS = ["normal", "day", "dim", "red", "green", "omarchy"];

export function cleanMapping(m) {
  const out = {};
  for (const p of PHASES) out[p] = SUN_LOOKS.includes(m && m[p]) ? m[p] : DEFAULT_MAPPING[p];
  return out;
}

// ---- where on Earth ----------------------------------------------------------------------

// A short list for a tablet with no keyboard: tap to step through. Cities
// rather than a pair of coordinates because nobody knows their own latitude,
// and the sun differs by well under a minute across a metro area.
export const CITIES = [
  { id: "nyc", name: "New York", lat: 40.7128, lon: -74.0060 },
  { id: "bos", name: "Boston", lat: 42.3601, lon: -71.0589 },
  { id: "dc", name: "Washington", lat: 38.9072, lon: -77.0369 },
  { id: "atl", name: "Atlanta", lat: 33.7490, lon: -84.3880 },
  { id: "mia", name: "Miami", lat: 25.7617, lon: -80.1918 },
  { id: "chi", name: "Chicago", lat: 41.8781, lon: -87.6298 },
  { id: "dal", name: "Dallas", lat: 32.7767, lon: -96.7970 },
  { id: "hou", name: "Houston", lat: 29.7604, lon: -95.3698 },
  { id: "msp", name: "Minneapolis", lat: 44.9778, lon: -93.2650 },
  { id: "den", name: "Denver", lat: 39.7392, lon: -104.9903 },
  { id: "phx", name: "Phoenix", lat: 33.4484, lon: -112.0740 },
  { id: "slc", name: "Salt Lake City", lat: 40.7608, lon: -111.8910 },
  { id: "lax", name: "Los Angeles", lat: 34.0522, lon: -118.2437 },
  { id: "sfo", name: "San Francisco", lat: 37.7749, lon: -122.4194 },
  { id: "sea", name: "Seattle", lat: 47.6062, lon: -122.3321 },
  { id: "anc", name: "Anchorage", lat: 61.2181, lon: -149.9003 },
  { id: "hnl", name: "Honolulu", lat: 21.3069, lon: -157.8583 },
];

export const cityById = (id) => CITIES.find((c) => c.id === id) || null;
export function nextCity(id) {
  const i = CITIES.findIndex((c) => c.id === id);
  return CITIES[(i + 1) % CITIES.length].id;     // an unknown id steps to the first
}

// An IANA zone -> a representative city. Every US zone, then the zones a
// Honda owner abroad is most likely to be in. A zone is a political area, not
// a point, so this is "approximate" and the Settings row says so.
export const TZ_CITY = {
  "America/New_York": [40.7128, -74.0060, "New York"],
  "America/Detroit": [42.3314, -83.0458, "Detroit"],
  "America/Kentucky/Louisville": [38.2527, -85.7585, "Louisville"],
  "America/Indiana/Indianapolis": [39.7684, -86.1581, "Indianapolis"],
  "America/Indianapolis": [39.7684, -86.1581, "Indianapolis"],
  "America/Chicago": [41.8781, -87.6298, "Chicago"],
  "America/Menominee": [45.1077, -87.6143, "Menominee"],
  "America/Indiana/Knox": [41.2958, -86.6250, "Knox"],
  "America/North_Dakota/Center": [47.1164, -101.2996, "Center, ND"],
  "America/Denver": [39.7392, -104.9903, "Denver"],
  "America/Boise": [43.6150, -116.2023, "Boise"],
  "America/Phoenix": [33.4484, -112.0740, "Phoenix"],
  "America/Los_Angeles": [34.0522, -118.2437, "Los Angeles"],
  "America/Anchorage": [61.2181, -149.9003, "Anchorage"],
  "America/Juneau": [58.3019, -134.4197, "Juneau"],
  "America/Nome": [64.5011, -165.4064, "Nome"],
  "America/Adak": [51.8800, -176.6580, "Adak"],
  "Pacific/Honolulu": [21.3069, -157.8583, "Honolulu"],
  "America/Puerto_Rico": [18.4655, -66.1057, "San Juan"],
  "America/Toronto": [43.6532, -79.3832, "Toronto"],
  "America/Winnipeg": [49.8951, -97.1384, "Winnipeg"],
  "America/Edmonton": [53.5461, -113.4938, "Edmonton"],
  "America/Vancouver": [49.2827, -123.1207, "Vancouver"],
  "America/Halifax": [44.6488, -63.5752, "Halifax"],
  "America/Mexico_City": [19.4326, -99.1332, "Mexico City"],
  "Europe/London": [51.5074, -0.1278, "London"],
  "Europe/Dublin": [53.3498, -6.2603, "Dublin"],
  "Europe/Paris": [48.8566, 2.3522, "Paris"],
  "Europe/Berlin": [52.5200, 13.4050, "Berlin"],
  "Europe/Madrid": [40.4168, -3.7038, "Madrid"],
  "Europe/Rome": [41.9028, 12.4964, "Rome"],
  "Australia/Sydney": [-33.8688, 151.2093, "Sydney"],
  "Australia/Melbourne": [-37.8136, 144.9631, "Melbourne"],
  "Pacific/Auckland": [-36.8485, 174.7633, "Auckland"],
  "Asia/Tokyo": [35.6762, 139.6503, "Tokyo"],
};

const okNum = (v) => typeof v === "number" && Number.isFinite(v);
export const validLatLon = (lat, lon) =>
  okNum(lat) && okNum(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;

// The place the sun is worked out for, and where that came from, in the order
// the owner set: a GPS fix when the server has one; else the home city chosen
// in Settings; else the time zone. `approx` is true for the last, and the UI
// says so. With none of the three it is null, and the caller does nothing
// rather than guess a place.
//
//   gps      {lat, lon, ageS} or null          (the gpsd fix, if /api/location answered)
//   homeId   a CITIES id or ""
//   tz       an IANA zone name or ""
//   offsetMin  the clock's offset east of UTC in minutes, the last resort
export const GPS_MAX_AGE_S = 6 * 3600;

export function resolveLocation({ gps = null, homeId = "", tz = "", offsetMin = null } = {}) {
  if (gps && validLatLon(gps.lat, gps.lon) && !(okNum(gps.ageS) && gps.ageS > GPS_MAX_AGE_S)) {
    return { lat: gps.lat, lon: gps.lon, source: "gps", name: "GPS fix", approx: false };
  }
  const home = cityById(homeId);
  if (home) return { lat: home.lat, lon: home.lon, source: "home", name: home.name, approx: false };
  const z = TZ_CITY[tz];
  if (z) return { lat: z[0], lon: z[1], source: "timezone", name: z[2] + " (time zone)", approx: true };
  // An unlisted zone: the longitude is knowable from the clock (15 degrees an
  // hour), the latitude is not. Say so, and use a mid-latitude; dawn and dusk
  // move by minutes across the temperate band, not hours.
  if (okNum(offsetMin) && Math.abs(offsetMin) <= 14 * 60) {
    return { lat: 40, lon: offsetMin / 4, source: "offset",
             name: "the clock's UTC offset (latitude 40 assumed)", approx: true };
  }
  return null;
}

// ---- the phase, today's times -------------------------------------------------------------

export function phaseNow(ms, loc) { return phaseAt(ms, loc.lat, loc.lon); }

// Local calendar date parts of an instant, in the zone `Date` is running in.
export function localYMD(ms) {
  const d = new Date(ms);
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()];
}

// The next boundary at or after `ms`, searching today and tomorrow. kind is
// one of dawn | sunrise | sunset | dusk; the phase the sky is in AFTER it is
// `to`. null if the sun crosses nothing in two days (polar).
const AFTER = { dawn: "twilight", sunrise: "day", sunset: "twilight", dusk: "night" };
export function nextBoundary(ms, loc, ymd = localYMD(ms)) {
  const out = [];
  for (let k = -1; k <= 1; k++) {
    const t = sunTimes(ymd[0], ymd[1], ymd[2] + k, loc.lat, loc.lon);
    for (const kind of ["dawn", "sunrise", "sunset", "dusk"]) {
      if (t[kind] !== null && t[kind] > ms) out.push({ kind, at: t[kind], to: AFTER[kind] });
    }
  }
  out.sort((a, b) => a.at - b.at);
  return out[0] || null;
}

export function upcoming(ms, loc, n = 4) {
  const out = [];
  const ymd = localYMD(ms);
  for (let k = -1; k <= 2; k++) {
    const t = sunTimes(ymd[0], ymd[1], ymd[2] + k, loc.lat, loc.lon);
    for (const kind of ["dawn", "sunrise", "sunset", "dusk"]) {
      if (t[kind] !== null && t[kind] > ms) out.push({ kind, at: t[kind], to: AFTER[kind] });
    }
  }
  out.sort((a, b) => a.at - b.at);
  return out.slice(0, n);
}

// The Settings line: "Sunset 6:41 · Night look from 7:08". `fmt` prints an
// epoch ms as a clock time in the caller's zone. Always names the next sunrise
// or sunset and the next time a look changes that the owner would notice.
export function summaryLine(ms, loc, fmt) {
  const ev = upcoming(ms, loc, 6);
  if (!ev.length) return "The sun does not cross the horizon for the next two days here.";
  const first = (kinds) => ev.find((e) => kinds.includes(e.kind));
  const sun = first(["sunrise", "sunset"]);
  const ph = phaseNow(ms, loc);
  const sunPart = sun ? `${sun.kind === "sunset" ? "Sunset" : "Sunrise"} ${fmt(sun.at)}` : "";
  // The look change to name: the next time the phase becomes "night" or "day"
  // (the two ends), falling back to twilight when that is all there is.
  const want = ph === "night" ? "day" : ph === "day" ? "night" : (ev[0].to === "night" ? "night" : "day");
  const change = ev.find((e) => e.to === want) || ev[0];
  const changePart = `${PHASE_LABEL[change.to]} look from ${fmt(change.at)}`;
  return [sunPart, changePart].filter(Boolean).join(" · ");
}

// ---- ambient light --------------------------------------------------------------------------

// Hysteresis for the tunnel and the garage. The sun can say "day" while the
// cabin is dark; this turns the lux reading into one steady yes/no.
//   dark   when lux has been below darkLux for holdMs without a break
//   light  when it has been at or above lightLux for holdMs without a break
// darkLux < lightLux leaves a band in between where nothing changes, so a
// reading hovering at one value cannot flicker the look. A missing reading
// (no sensor, or a failed read) clears the timers and reports "not dark":
// no sensor means the sun alone decides, never a frozen dark.
export const AMBIENT = { darkLux: 15, lightLux: 60, holdMs: 20000 };

export function createAmbientGate(opts = {}) {
  const c = { ...AMBIENT, ...opts };
  let dark = false, since = null, side = null;      // side: "dark" | "light" | null (the band)
  return {
    feed(lux, nowMs) {
      if (!okNum(lux) || lux < 0) { dark = false; since = null; side = null; return dark; }
      const s = lux < c.darkLux ? "dark" : lux >= c.lightLux ? "light" : null;
      if (s !== side) { side = s; since = s ? nowMs : null; }
      if (s && since !== null && nowMs - since >= c.holdMs) dark = s === "dark";
      return dark;
    },
    get dark() { return dark; },
    reset() { dark = false; since = null; side = null; },
  };
}

// ---- the decision ---------------------------------------------------------------------------

export function freshState() { return { applied: null, override: null, deferred: false }; }

// One step of auto mode. Pure: returns the next state and the look to apply
// (or null for "leave it").
//
//   state    {applied, override, deferred}   what this step last did (persist it)
//   in.enabled      Settings toggle
//   in.phase        "day" | "twilight" | "night", from the sun
//   in.ambientDark  the gate's verdict
//   in.alert        true while a drowsy, radar or child alert is showing
//   in.current      the look actually worn right now
//   in.mapping      {day, twilight, night} -> look ids
//
// "Only at the boundary" falls out of two facts. We apply only when the WANTED
// look differs from the worn one, and the wanted look is a function of the
// phase (and of the ambient gate), so it can change only when one of those
// does. And a look that is not what we last applied was put there by a hand:
// that is an override, held for as long as the phase it was set in lasts.
export function step(state, inp) {
  const s = { ...state };
  if (!inp.enabled) return { state: freshState(), apply: null };

  // A hand on the toggle (or the Look row): the worn look is not the one we put there.
  if (s.applied !== null && inp.current !== s.applied) {
    s.override = { phase: inp.phase };
    s.applied = inp.current;
  }
  // The next boundary clears the override. Ambient changes do not: a manual
  // choice stands until the sky itself moves on.
  if (s.override && s.override.phase !== inp.phase) s.override = null;
  if (s.override) return { state: s, apply: null };

  const wanted = inp.ambientDark && inp.phase !== "night" ? inp.mapping.night : inp.mapping[inp.phase];
  if (inp.current === wanted) { s.applied = wanted; s.deferred = false; return { state: s, apply: null }; }
  if (inp.alert) { s.deferred = true; return { state: s, apply: null }; }
  s.applied = wanted; s.deferred = false;
  return { state: s, apply: wanted };
}

// ---- the day's arc, for the drawing -----------------------------------------------------------

// The sun's height across [startMs, endMs), n+1 samples, each with its phase.
// Pure so the drawing can be tested without a DOM; the bands the owner sees
// are runs of equal phase.
export function dayArc(startMs, endMs, loc, n = 144) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = startMs + ((endMs - startMs) * i) / n;
    const el = elevationAt(t, loc.lat, loc.lon);
    out.push({ t, el, phase: el >= -0.833 ? "day" : el >= -6 ? "twilight" : "night" });
  }
  return out;
}

// Runs of equal phase over the samples: [{phase, from, to}] as fractions 0..1.
export function phaseRuns(arc) {
  const runs = [];
  const n = arc.length - 1;
  arc.forEach((p, i) => {
    const last = runs[runs.length - 1];
    if (last && last.phase === p.phase) last.to = i / n;
    else runs.push({ phase: p.phase, from: runs.length ? runs[runs.length - 1].to : 0, to: i / n });
  });
  return runs;
}
