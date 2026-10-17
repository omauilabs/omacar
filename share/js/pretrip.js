// Ready for a long drive? The judging half of the pre-trip check: pure
// functions from what the app already knows to a lamp and a line per check,
// and from those to one verdict. No DOM, no fetch, no clock it was not given,
// so test/js/pretrip.test.js can pin every combination (views/pretrip.js draws
// it; lib/pretrip.py keeps the trip distance, the ticks and the gaps).
//
// THE RULE THAT MATTERS: an UNKNOWN IS NEVER AN OK. A tyre pressure nobody
// measured, a monitor list the car has not sent, a schedule item nobody has
// ever recorded: each is a lamp of its own ("unknown") and each counts as a
// note in the verdict. "Ready" with no qualifier means every check was looked
// at and passed; it is the one label that cannot be reached by not looking.

export const LAMPS = ["ok", "warn", "bad", "unknown"];

// Service items due "soon" for a trip: within 500 miles or 30 days.
export const DUE_KM = 500 * 1.609344;
export const DUE_DAYS = 30;

// Overdue is "Fix first" only for what keeps a car on the road or stopped:
// brakes and tyres. An overdue cabin filter is a note, not a reason to stay home.
// (This app's judgement, not Honda's.)
export const SAFETY_ITEM = /brake|^tires?$|^tyres?$/i;

// Tyre pressure bands, psi (this app's thresholds; the placard on the door
// pillar is the authority and the screen says so).
export const TYRE_WARN_PSI = 28;
export const TYRE_BAD_PSI = 22;

// 12 V system, volts. The same figures as the "12V system" gauge in readings.js.
const V_RUN_BAD = 12.6, V_RUN_WARN = 13.2, V_OFF_BAD = 11.8, V_OFF_WARN = 12.4;

export const CHECKLIST = [
  { id: "oil", label: "Engine oil level" },
  { id: "coolant", label: "Coolant level" },
  { id: "washer", label: "Washer fluid" },
  { id: "wipers", label: "Wipers" },
  { id: "lights", label: "Lights and indicators" },
  { id: "tyres_look", label: "Tyre condition" },
  { id: "spare", label: "Spare, jack or sealant" },
];

const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const check = (id, title, lamp, line, extra = {}) => ({ id, title, lamp, line, ...extra });

// ---- the overall verdict -----------------------------------------------------

// Any bad lamp: "Fix first". Otherwise warn and unknown lamps are notes, and
// no notes is "Ready". With nothing known to be ok there is nothing to be
// ready on, so that says so instead of reading "Ready, with 7 notes".
export function verdict(checks) {
  const list = Array.isArray(checks) ? checks : [];
  const n = (lamp) => list.filter((c) => c && c.lamp === lamp).length;
  const bad = n("bad"), warn = n("warn"), unknown = list.length - bad - warn - n("ok");
  const ok = n("ok");
  // A lamp outside LAMPS counts as unknown, never as ok (the line above).
  const notes = warn + unknown;
  let level, label;
  if (bad > 0) { level = "fix"; label = "Fix first"; }
  else if (ok === 0) { level = "unknown"; label = "Not enough to say yet"; }
  else if (notes > 0) { level = "notes"; label = `Ready, with ${plural(notes, "note", "notes")}`; }
  else { level = "ready"; label = "Ready"; }
  return { level, label, ok, warn, bad, unknown, notes,
           tone: { fix: "bad", notes: "warn", ready: "ok", unknown: "unknown" }[level] };
}

// ---- the checks ---------------------------------------------------------------

export function codesCheck(faults) {
  if (!Array.isArray(faults)) return check("codes", "Trouble codes", "unknown", "Codes not read yet");
  const active = faults.filter((f) => f && f.active);
  if (!active.length) return check("codes", "Trouble codes", "ok", "No active trouble codes");
  // Pending alone is the car noticing something once; stored or permanent is
  // it having decided.
  const solid = active.filter((f) => f.status !== "pending");
  const names = active.slice(0, 3).map((f) => f.code).join(", ") + (active.length > 3 ? " …" : "");
  return check("codes", "Trouble codes", solid.length ? "bad" : "warn",
    `${plural(active.length, "active code", "active codes")}: ${names}`);
}

export function readinessCheck(readiness) {
  const mons = readiness && Array.isArray(readiness.monitors) ? readiness.monitors : [];
  const supported = mons.filter((m) => m.supported);
  if (!supported.length) return check("readiness", "Readiness monitors", "unknown", "Monitors not read yet");
  const left = supported.filter((m) => !m.complete);
  if (!left.length) return check("readiness", "Readiness monitors", "ok", `All ${supported.length} monitors complete`);
  // An unfinished monitor is no reason not to drive; a long drive is how it finishes.
  return check("readiness", "Readiness monitors", "warn",
    `${left.length} of ${supported.length} not yet run: ${left.slice(0, 2).map((m) => m.name).join(", ")}${left.length > 2 ? " …" : ""}`);
}

// Items whose due date means something: recorded ones only. `unrecorded` is
// the list of names lib/pretrip.py calls gaps (their "last done" is a
// placeholder, so any countdown from it would be a guess).
export function dueItems(service, unrecorded = []) {
  const items = service && Array.isArray(service.items) ? service.items : [];
  const skip = new Set((unrecorded || []).map((n) => String(n).toLowerCase()));
  const out = [];
  for (const it of items) {
    if (skip.has(String(it.item).toLowerCase())) continue;
    const kmLeft = isNum(it.km_left) ? it.km_left : null;
    const dLeft = isNum(it.days_left) ? it.days_left : null;
    const overdue = (isNum(it.life) && it.life <= 0) || (kmLeft !== null && kmLeft < 0) || (dLeft !== null && dLeft < 0);
    const soon = (kmLeft !== null && kmLeft <= DUE_KM) || (dLeft !== null && dLeft <= DUE_DAYS);
    if (overdue || soon) out.push({ item: it.item, overdue, kmLeft, daysLeft: dLeft });
  }
  return out;
}

export function serviceCheck(service, unrecorded = []) {
  if (!service || !Array.isArray(service.items) || !service.items.length) {
    return check("service", "Service due", "unknown", "No service schedule yet");
  }
  const due = dueItems(service, unrecorded);
  if (!due.length) return check("service", "Service due", "ok", "Nothing due in the next 500 miles or 30 days");
  const over = due.filter((d) => d.overdue);
  const hard = over.filter((d) => SAFETY_ITEM.test(d.item));
  const names = (xs) => xs.slice(0, 2).map((d) => d.item).join(", ") + (xs.length > 2 ? " …" : "");
  const line = over.length
    ? `${plural(over.length, "overdue", "overdue")}: ${names(over)}${due.length > over.length ? `, ${due.length - over.length} more due soon` : ""}`
    : `${plural(due.length, "item", "items")} due soon: ${names(due)}`;
  return check("service", "Service due", hard.length ? "bad" : "warn", line, { due });
}

// Items the book knows and nobody has recorded. Unknown, never ok: the due
// dates for these are placeholders.
export function gapsCheck(gaps) {
  if (!Array.isArray(gaps)) return check("gaps", "Service record", "unknown", "Service record not read yet");
  if (!gaps.length) return check("gaps", "Service record", "ok", "Every scheduled item has been recorded once");
  return check("gaps", "Service record", "unknown",
    `${plural(gaps.length, "item", "items")} never recorded: ${gaps.slice(0, 2).map((g) => g.item).join(", ")}${gaps.length > 2 ? " …" : ""}`,
    { gaps });
}

// The 12 V battery, from the system voltage, read the way the gauge reads it:
// running judges the charging system, parked judges the battery.
export function batteryCheck({ volts, rpm, connected } = {}) {
  if (!connected || !isNum(volts)) return check("battery12", "12 V battery", "unknown", "Not reading the car right now");
  const running = isNum(rpm) && rpm > 200;
  const [bad, warn] = running ? [V_RUN_BAD, V_RUN_WARN] : [V_OFF_BAD, V_OFF_WARN];
  const lamp = volts < bad ? "bad" : volts < warn ? "warn" : "ok";
  return check("battery12", "12 V battery", lamp,
    `${volts.toFixed(1)} V ${running ? "with the engine running" : "parked"}${lamp === "ok" ? "" : lamp === "bad" ? ": low" : ": a little low"}`);
}

// The hybrid pack, from lib/battery_health.py's verdict. "Not enough data yet"
// and an error are unknown, not healthy.
export function imaCheck(doc) {
  const v = doc && !doc.error ? doc.verdict : null;
  const lamp = { Healthy: "ok", "Watch it": "warn", Failing: "bad" }[v];
  if (!lamp) return check("ima", "Hybrid battery", "unknown", doc && doc.verdict && !doc.error ? `${doc.verdict}` : "No pack history yet");
  const why = Array.isArray(doc.reasons) && doc.reasons.length ? ": " + doc.reasons[0] : "";
  return check("ima", "Hybrid battery", lamp, v + why);
}

// Tyre pressures from lib/tpms.py if it exists. Accepts a list or an object of
// wheels, each a number or an object with psi / kpa / bar. Nothing parseable is
// "Not measured", never a pressure.
export function tyrePressures(t) {
  if (!t || typeof t !== "object") return [];
  const raw = Array.isArray(t.tyres) ? t.tyres : Array.isArray(t.wheels) ? t.wheels
    : t.tyres && typeof t.tyres === "object" ? Object.values(t.tyres)
    : t.wheels && typeof t.wheels === "object" ? Object.values(t.wheels) : [];
  const psi = [];
  for (const w of raw) {
    const o = isNum(w) ? { psi: w } : w || {};
    if (isNum(o.psi)) psi.push(o.psi);
    else if (isNum(o.kpa)) psi.push(o.kpa * 0.145038);
    else if (isNum(o.pressure_kpa)) psi.push(o.pressure_kpa * 0.145038);
    else if (isNum(o.bar)) psi.push(o.bar * 14.5038);
  }
  return psi;
}

export function tyresCheck(tpms) {
  const psi = tyrePressures(tpms);
  if (!psi.length) return check("tyres", "Tyre pressures", "unknown", "Not measured. Check them with a gauge against the door-pillar placard");
  const low = Math.min(...psi);
  const lamp = low < TYRE_BAD_PSI ? "bad" : low < TYRE_WARN_PSI ? "warn" : "ok";
  const n = psi.length;
  return check("tyres", "Tyre pressures", lamp,
    `${n} tyre${n === 1 ? "" : "s"} read, lowest ${Math.round(low)} psi${lamp === "ok" ? "" : ": low"}`,
    { simulated: !!(tpms && (tpms.simulated || tpms.source === "sim")) });
}

// Fuel against the trip. Without a trip distance there is nothing to compare
// the tank with, so that is unknown, not ok. Range is the app's own estimate
// (litres in the tank over this car's year average), never the ECU's.
export function rangeKm({ fuelPct, tankL, lphk }) {
  if (![fuelPct, tankL, lphk].every(isNum) || fuelPct <= 0 || tankL <= 0 || lphk <= 0) return null;
  return (tankL * fuelPct / 100) / lphk * 100;
}

export function fuelCheck({ tripKm, fuelPct, tankL, lphk, fmtDist = (km) => `${Math.round(km)} km` } = {}) {
  if (!isNum(fuelPct)) return check("fuel", "Fuel for the trip", "unknown", "Fuel level not reading");
  const range = rangeKm({ fuelPct, tankL, lphk });
  const level = `${Math.round(fuelPct)}% in the tank`;
  if (!isNum(tripKm) || tripKm <= 0) {
    return check("fuel", "Fuel for the trip", "unknown",
      `${level}${range ? `, about ${fmtDist(range)} of range` : ""}. Enter the trip distance to compare`, { range });
  }
  if (range === null) return check("fuel", "Fuel for the trip", "unknown", `${level}. No range estimate yet (needs driving history)`, { range });
  if (range >= tripKm * 1.25) return check("fuel", "Fuel for the trip", "ok", `${fmtDist(range)} of range for a ${fmtDist(tripKm)} trip`, { range });
  if (range >= tripKm) return check("fuel", "Fuel for the trip", "warn", `${fmtDist(range)} of range for a ${fmtDist(tripKm)} trip: tight, plan a fill-up`, { range });
  return check("fuel", "Fuel for the trip", "warn", `${fmtDist(range)} of range for a ${fmtDist(tripKm)} trip: fill up on the way`, { range });
}

// The owner's own walk-round. Unticked is unknown, not a fault: nobody has
// looked yet.
export function manualCheck(ticks, list = CHECKLIST) {
  const t = ticks && typeof ticks === "object" ? ticks : {};
  const left = list.filter((c) => t[c.id] !== true);
  if (!left.length) return check("manual", "Walk-round", "ok", `All ${list.length} checked by you`);
  return check("manual", "Walk-round", "unknown",
    `${list.length - left.length} of ${list.length} ticked: ${left.slice(0, 2).map((c) => c.label).join(", ")}${left.length > 2 ? " …" : ""} still to check`);
}

// Every check, in the order the screen lists them.
export function buildChecks(i = {}) {
  const unrecorded = (i.gaps || []).map((g) => g.item);
  return [
    codesCheck(i.faults),
    readinessCheck(i.readiness),
    serviceCheck(i.service, unrecorded),
    gapsCheck(i.gaps),
    batteryCheck(i.battery12 || {}),
    imaCheck(i.ima),
    tyresCheck(i.tpms),
    fuelCheck(i.fuel || {}),
    manualCheck(i.ticks, i.checklist && i.checklist.length ? i.checklist : CHECKLIST),
  ];
}

// The trip-distance stepper: ±25 in the unit on screen, snapped to a multiple
// of 25, never below zero or above the server's cap. `kmPerUnit` is the
// size of one displayed unit in km (1.609344 for miles, 1 for km).
export const STEP = 25;
export const MAX_TRIP_KM = 8000;

export function stepTrip(km, dir, kmPerUnit = 1.609344) {
  const cur = isNum(km) ? Math.round(km / kmPerUnit / STEP) * STEP : 0;
  const next = Math.max(0, cur + (dir < 0 ? -STEP : STEP));
  if (next === 0) return null;
  return Math.min(MAX_TRIP_KM, next * kmPerUnit);
}
