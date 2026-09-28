// Every reading the app can show, in one place.
//
// This was drive mode's private catalogue (views/drive.js), and Home and
// Vehicle need the same numbers formatted the same way: the whole reason
// app_test.py checks that the hub and the drive screen agree about coolant is
// that two copies of a formatter WILL drift. So there is one copy, here.
//
// Each entry now also names the PID it reads, so a tile can tell the two kinds
// of empty apart: "the car does not report this" and "the car has not said yet".

import { U, temp, econ, dist, vol, grouped } from "./core.js";

// The IMA tile's memory. A pack's direction cannot be read from one sample, so
// the last few are kept here -- in the view, not the store, because nothing
// else needs them and a tile that is not on screen should cost nothing.
const SOC_WINDOW_MS = 12000;   // a few live samples, not a trend line
const SOC_MOVED = 0.4;         // a resting pack jitters less than this between reads
const socTrail = [];

// ---------------------------------------------------------------- the catalogue
//
// Everything drive mode can show. `get` returns { v, n, tone } — the number,
// the note under it, and whether it wants colour. Adding a readout is adding
// one entry here; nothing else in the file knows what any of them are.
//
// This object is the BASE catalogue and it never changes after this file is
// read. What a particular car has been taught is merged onto a copy of it per
// mount, down in learnedFor(); the freeze under the closing brace is what
// keeps that promise enforceable rather than merely intended.
const TILES = {
  speed: {
    pid: "SPEED",
    label: "Speed", hero: true,
    get: (v) => ({ v: num(v.SPEED, (x) => Math.round(x * U.units.km)), n: U.units.speed }),
    read: (v) => (raw(v.SPEED) === null ? null : raw(v.SPEED) * U.units.km),
    scale: () => ({ min: 0, max: U.imperial ? 140 : 220, step: U.imperial ? 20 : 40 }),
  },
  rpm: {
    pid: "RPM",
    label: "Engine speed", hero: true,
    get: (v) => ({ v: num(v.RPM, (x) => grouped(x)), n: "rpm",
                   tone: v.RPM > 6200 ? "bad" : v.RPM > 5500 ? "warn" : "" }),
    read: (v) => raw(v.RPM),
    // Labelled in thousands, as every tachometer is.
    scale: () => ({ min: 0, max: 7000, step: 1000,
                    tick: (x) => String(Math.round(x / 1000)),
                    bands: [{ from: 5500, to: 6200, tone: "warn" },
                            { from: 6200, tone: "bad" }] }),
  },
  econ_now: {
    label: "Economy",
    get: (v, s, car) => {
      const moving = (v.SPEED || 0) > 3;
      if (moving && s.economy_lphk) return { v: econ(s.economy_lphk, false), n: U.units.econ };
      const day = car && car.perf && car.perf.day;
      return { v: day ? econ(day.lphk, false) : "—", n: U.units.econ + " today" };
    },
  },
  econ_trip: {
    label: "Economy, trip",
    get: (v, s, car) => {
      const day = car && car.perf && car.perf.day;
      return { v: day ? econ(day.lphk, false) : "—", n: U.units.econ + " today" };
    },
  },
  coolant: {
    pid: "COOLANT_TEMP",
    label: "Coolant",
    get: (v) => ({ v: num(v.COOLANT_TEMP, (x) => temp(x, false)), n: U.units.temp,
                   tone: v.COOLANT_TEMP > 105 ? "bad" : v.COOLANT_TEMP > 100 ? "warn" : "" }),
    read: (v) => asTemp(v.COOLANT_TEMP),
    scale: () => ({ min: asTemp(40), max: asTemp(120), step: U.imperial ? 40 : 20,
                    bands: [{ from: asTemp(100), to: asTemp(105), tone: "warn" },
                            { from: asTemp(105), tone: "bad" }] }),
  },
  intake: {
    pid: "INTAKE_TEMP",
    label: "Intake air",
    get: (v) => ({ v: num(v.INTAKE_TEMP, (x) => temp(x, false)), n: U.units.temp }),
    read: (v) => asTemp(v.INTAKE_TEMP),
    scale: () => ({ min: asTemp(-10), max: asTemp(70), step: U.imperial ? 40 : 20 }),
  },
  ambient: {
    pid: "AMBIANT_AIR_TEMP",
    label: "Outside",
    get: (v) => ({ v: num(v.AMBIANT_AIR_TEMP, (x) => temp(x, false)), n: U.units.temp }),
    read: (v) => asTemp(v.AMBIANT_AIR_TEMP),
    scale: () => ({ min: asTemp(-20), max: asTemp(50), step: U.imperial ? 30 : 20 }),
  },
  volts: {
    pid: "CONTROL_MODULE_VOLTAGE",
    label: "12V system",
    get: (v) => {
      const running = (v.RPM || 0) > 200;
      return { v: num(v.CONTROL_MODULE_VOLTAGE, (x) => x.toFixed(1)), n: "V",
               tone: running && v.CONTROL_MODULE_VOLTAGE < 12.6 ? "bad"
                   : running && v.CONTROL_MODULE_VOLTAGE < 13.2 ? "warn" : "" };
    },
    read: (v) => raw(v.CONTROL_MODULE_VOLTAGE),
    // The bands are the figures that are low WHETHER OR NOT the engine is
    // running. The tone above is the stricter, running-aware judgement; a
    // painted band cannot know, and a face that shouts at a healthy parked
    // battery is a face you learn to ignore.
    // Step 2, not 1: seven labels round a face this small ran "12 13 14"
    // together into one smear.
    scale: () => ({ min: 10, max: 16, step: 2, tick: (x) => String(Math.round(x)),
                    bands: [{ to: 11.8, tone: "bad" },
                            { from: 11.8, to: 12.4, tone: "warn" }] }),
  },
  // ---- the hybrid ---------------------------------------------------------
  //
  // THE PACK, WHICH IS NOT THE `volts` TILE ABOVE. `volts` is
  // CONTROL_MODULE_VOLTAGE -- the 12V system the adapter itself sits on -- and
  // reading it as the traction battery is the single easiest mistake to make
  // on a car like this. They are labelled so the difference is on the screen:
  // "12V system" against "Hybrid pack", on every screen that shows either,
  // because every screen takes its names from here.
  //
  // The daemon has stored HYBRID_BATTERY_REMAINING into samples.soc since the
  // column was added, and on 16 September it filled 4,330 rows across 126 km.
  // Nothing has ever drawn it.
  charge: {
    pid: "HYBRID_BATTERY_REMAINING",
    // PID 0x5B: the pack's remaining life, which is not the manufacturer's
    // state of charge -- see the note on the dial in views/ima.js.
    label: "Hybrid pack",
    get: (v) => {
      const soc = v.HYBRID_BATTERY_REMAINING;
      return { v: num(soc, (x) => Math.round(x)), n: "%",
               tone: soc === null || soc === undefined ? ""
                   : soc < 20 ? "bad" : soc < 35 ? "warn" : "" };
    },
    read: (v) => raw(v.HYBRID_BATTERY_REMAINING),
    // A pack is not a fuel tank: the ends of this range are where the car
    // stops letting you use it, not where it is empty or full. The observed
    // working band on this car over a 126 km drive was 43.9% to 76.9%.
    scale: () => ({ min: 0, max: 100, step: 25,
                    bands: [{ to: 20, tone: "bad" }, { from: 20, to: 35, tone: "warn" }] }),
  },
  ima: {
    pid: "HYBRID_BATTERY_REMAINING",
    label: "IMA",
    // CHARGING / ASSIST / STEADY, FROM THE CHARGE ITSELF.
    //
    // The honest caveat: this car answers no motor-power identifier we have
    // found, so the direction here is the pack's own movement over the last
    // few samples and nothing more. It is not signed motor power and it does
    // not pretend to be -- which is why it says "charging" rather than a
    // number of kilowatts it cannot know.
    get: (v) => {
      const soc = v.HYBRID_BATTERY_REMAINING;
      if (soc === null || soc === undefined) return { v: "—", n: "no pack reading" };
      socTrail.push({ t: Date.now(), soc });
      while (socTrail.length && Date.now() - socTrail[0].t > SOC_WINDOW_MS) socTrail.shift();
      if (socTrail.length < 3) return { v: "…", n: "settling" };
      const drift = soc - socTrail[0].soc;
      // A pack at rest still jitters a fraction of a percent between reads, so
      // below this it is called steady rather than manufacturing a direction.
      if (drift > SOC_MOVED) return { v: "Charging", n: "regen", tone: "good" };
      if (drift < -SOC_MOVED) return { v: "Assist", n: "motor helping" };
      return { v: "Steady", n: "at rest" };
    },
  },
  fuel: {
    pid: "FUEL_LEVEL",
    label: "Fuel",
    get: (v, s, car) => {
      const tank = car && car.vehicle && car.vehicle.tank_l;
      return { v: num(v.FUEL_LEVEL, (x) => Math.round(x)), n:
        tank && v.FUEL_LEVEL ? `%  ·  ${vol(tank * v.FUEL_LEVEL / 100)}` : "%",
        tone: v.FUEL_LEVEL < 10 ? "bad" : v.FUEL_LEVEL < 18 ? "warn" : "" };
    },
    read: (v) => raw(v.FUEL_LEVEL),
    scale: () => ({ ...pct(), bands: [{ to: 10, tone: "bad" },
                                      { from: 10, to: 18, tone: "warn" }] }),
  },
  range: {
    label: "Range",
    get: (v, s, car) => {
      // Distance to empty, from what is in the tank and how the car has
      // actually been driven — not a number the ECU reports.
      const tank = car && car.vehicle && car.vehicle.tank_l;
      const day = car && car.perf && car.perf.year;
      if (!tank || !v.FUEL_LEVEL || !day || !day.lphk) return { v: "—", n: U.units.dist };
      const litres = tank * v.FUEL_LEVEL / 100;
      return { v: dist(litres / day.lphk * 100, false), n: U.units.dist + " left",
               tone: litres / day.lphk * 100 < 60 ? "warn" : "" };
    },
  },
  load: {
    pid: "ENGINE_LOAD",
    label: "Engine load",
    get: (v) => ({ v: num(v.ENGINE_LOAD, (x) => Math.round(x)), n: "%" }),
    read: (v) => raw(v.ENGINE_LOAD),
    scale: () => ({ ...pct(), bands: [{ from: 85, tone: "warn" }] }),
  },
  throttle: {
    pid: "THROTTLE_POS",
    label: "Throttle",
    get: (v) => ({ v: num(v.THROTTLE_POS, (x) => Math.round(x)), n: "%" }),
    read: (v) => raw(v.THROTTLE_POS),
    scale: () => pct(),
  },
  timing: {
    pid: "TIMING_ADVANCE",
    label: "Timing",
    get: (v) => ({ v: num(v.TIMING_ADVANCE, (x) => x.toFixed(0)), n: "°" }),
    read: (v) => raw(v.TIMING_ADVANCE),
    scale: () => ({ min: -10, max: 50, step: 10 }),
  },
  stft: {
    pid: "SHORT_FUEL_TRIM_1",
    label: "Short trim",
    get: (v) => ({ v: num(v.SHORT_FUEL_TRIM_1, (x) => (x > 0 ? "+" : "") + x.toFixed(1)), n: "%",
                   tone: Math.abs(v.SHORT_FUEL_TRIM_1) > 15 ? "warn" : "" }),
    read: (v) => raw(v.SHORT_FUEL_TRIM_1),
    scale: () => ({ min: -25, max: 25, step: 10,
                    tick: (x) => (x > 0 ? "+" : "") + Math.round(x),
                    bands: [{ to: -15, tone: "bad" }, { from: -15, to: -10, tone: "warn" },
                            { from: 10, to: 15, tone: "warn" }, { from: 15, tone: "bad" }] }),
  },
  ltft: {
    pid: "LONG_FUEL_TRIM_1",
    label: "Long trim",
    get: (v) => ({ v: num(v.LONG_FUEL_TRIM_1, (x) => (x > 0 ? "+" : "") + x.toFixed(1)), n: "%",
                   tone: Math.abs(v.LONG_FUEL_TRIM_1) > 15 ? "warn" : "" }),
    read: (v) => raw(v.LONG_FUEL_TRIM_1),
    scale: () => ({ min: -25, max: 25, step: 10,
                    tick: (x) => (x > 0 ? "+" : "") + Math.round(x),
                    bands: [{ to: -15, tone: "bad" }, { from: -15, to: -10, tone: "warn" },
                            { from: 10, to: 15, tone: "warn" }, { from: 15, tone: "bad" }] }),
  },
  maf: {
    pid: "MAF",
    label: "Air flow",
    get: (v) => ({ v: num(v.MAF, (x) => x.toFixed(1)), n: "g/s" }),
    read: (v) => raw(v.MAF),
    scale: () => ({ min: 0, max: 120, step: 30 }),
  },
  today: {
    label: "Today",
    get: (v, s, car) => ({ v: car && car.perf && car.perf.day
      ? dist(car.perf.day.km, false) : "—", n: U.units.dist }),
  },
  odometer: {
    label: "Odometer",
    get: (v, s, car) => ({ v: car && car.odometer ? grouped(car.odometer * U.units.km) : "—",
                           n: U.units.dist }),
  },
  codes: {
    label: "Faults",
    get: (v, s, car) => {
      const n = car && car.active_faults ? car.active_faults.length : 0;
      return { v: String(n), n: n === 1 ? "stored" : "stored", tone: n ? "warn" : "" };
    },
  },
  service: {
    label: "Next service",
    get: (v, s, car) => {
      const nx = car && car.service && car.service.next;
      if (!nx) return { v: "—", n: "" };
      return { v: Math.max(0, nx.life) + "%", n: nx.short || nx.item,
               tone: nx.life <= 0 ? "bad" : nx.life <= 15 ? "warn" : "" };
    },
    read: (v, s, car) => {
      const nx = car && car.service && car.service.next;
      return nx ? Math.max(0, nx.life) : null;
    },
    scale: () => ({ ...pct(), bands: [{ to: 15, tone: "warn" }] }),
  },
};

// FROZEN, AND THE FREEZE IS THE POINT.
//
// The tile a car earns by having a signal validated used to be written INTO
// this object at mount. It is module scope: it outlives the view, so the next
// car inherited the last car's readouts. Freezing turns that mistake from a
// silent leak into a TypeError the first time anyone tries it again — module
// code is strict mode, so an assignment here throws rather than being quietly
// dropped. Per-car entries live in the derived catalogue instead; see below.
Object.freeze(TILES);

function num(x, fmt) {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : String(fmt(x));
}

// ---------------------------------------------------------------- the scales
//
// `get` formats a reading for reading; `read` returns the same reading as a
// NUMBER, and `scale` says what face to draw it on. A dial whose ticks say °F
// has to be handed Fahrenheit, so read() converts exactly as get() does.
//
// scale is a function rather than an object because the units toggle at
// runtime: a face built once at import would keep its mph ticks after you
// switched to km/h. Readouts with no scale — the odometer, a fault count —
// simply have none, and the editor then offers them only as numbers.
const raw = (x) => (x === null || x === undefined || Number.isNaN(x) ? null : Number(x));
const asTemp = (c) => (raw(c) === null ? null : U.imperial ? raw(c) * 9 / 5 + 32 : raw(c));
const pct = () => ({ min: 0, max: 100, step: 25 });

// -------------------------------------------------- what THIS car was taught
//
// VALIDATED IDENTIFIERS BECOME TILES — FOR THE CAR IN FRONT OF YOU, AND NO OTHER.
//
// This is where the coverage strategy finally reaches a screen. A profile
// entry that a person has checked against something real carries an id, a name
// and a unit in the snapshot, and the daemon publishes its value under that
// id. Everything else — the header, the request, the formula — stays on the
// server, because the browser draws numbers and does not send requests. The
// result is merged into the same object the picker enumerates, so a validated
// entry is choosable the moment it exists, with no list to keep in step.
//
// It is DERIVED from a car rather than accumulated into the catalogue, and the
// difference is the whole point of the function. The first version added these
// entries to TILES itself. TILES is module scope; it outlives the view. Switch
// from the car that had been taught an oil-temperature signal to one that has
// not and the second car's dashboard still carried the first car's tile,
// reading whatever the live sample happened to hold under that id — a number
// presented as this vehicle's when nothing on this vehicle produced it. That
// is the same defect as the live sample belonging to another car, fixed today
// in lib/records.py live() under "whose sample is this?", and it is exactly
// the thing this tool exists not to do.
//
// No scale: a signal the tool has only just been taught has no sensible bands
// yet, so it renders as a digital readout rather than a dial pointing at a
// range nobody chose.
function learnedFor(car) {
  const out = {};
  for (const sig of (car && car.signals) || []) {
    if (!sig || !sig.id || TILES[sig.id]) continue;
    const key = sig.id, unit = sig.unit || "";
    out[key] = {
      pid: key,
      label: sig.name || key,
      get: (v) => ({ v: num(v[key], (x) => Math.round(x * 10) / 10), n: unit }),
      read: (v) => raw(v[key]),
      scale: null,
      learned: true,
    };
  }
  return out;
}

// The identity of a car's teaching, so the view can ask "is this still the
// same set?" without rebuilding the screen to find out. Id, name and unit all
// count: a renamed signal is a different label on a tile, and a tile whose
// label is stale is a tile that lies about what it is showing. Joined on
// escaped separators — never a control character typed literally into the
// source, which is invisible to every reader who comes after you.
function learnedKey(car) {
  return ((car && car.signals) || [])
    .filter((s) => s && s.id && !TILES[s.id])
    .map((s) => [s.id, s.name || "", s.unit || ""].join("\u001f"))
    .join("\n");
}

export { TILES as READINGS };
export { num, raw, asTemp, pct, learnedFor, learnedKey };

// LIVE, ABSENT OR WAITING. Never a zero standing in for a missing value.
//
//   live     the value is in the sample: draw get()'s output
//   absent   the car answered the supported-PIDs question and this was not in
//            the answer: say "Not on this car"
//   waiting  no car, or a supported reading that has not arrived yet: say
//            "Waiting for the car"
//
// A reading with no pid is derived (economy, odometer, a fault count) and its
// get() already draws a dash when it has nothing, so it is always "live".
export function readingState(def, sample) {
  if (!def || !def.pid) return "live";
  const s = sample || {};
  if (!s.connected) return "waiting";
  const v = (s.values || {})[def.pid];
  if (v !== null && v !== undefined && !Number.isNaN(v)) return "live";
  const sup = s.supported;
  if (Array.isArray(sup) && sup.length && !sup.includes(def.pid)) return "absent";
  return "waiting";
}
