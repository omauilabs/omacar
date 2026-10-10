// The hybrid system, now: which way the IMA motor is working, and the pack.
//
// ONE PICTURE FOR EVERY SCREEN. The drive screen's "Hybrid flow" tile, Home's
// Hybrid pack card, the Live section of Vehicle → Hybrid and the old "IMA"
// reading all draw from hybridNow(), so no two of them can disagree about
// whether the motor is helping (doc/design/2026-10-10-hybrid-gauges.md).
//
// EVERY NUMBER SAYS WHERE IT CAME FROM. `source` is one of:
//   measured       a validated profile reading (lib/signals.py) for that
//                  quantity, or the charge PID 0x5B
//   simulated      the demo world, and only the demo world
//   estimated      the flow, worked out from speed, throttle and charge,
//                  because no pack-current reading has been found on this car
//                  yet. Drawn hatched with "EST" -- never stored, never sent
//   not found yet  nothing on this car has been found to give it
//
// Flow runs -1 (full regen, the pack charging) to +1 (full assist).

// Thresholds. ASSIST_MPS2 is the demo world's (lib/demoworld.py), so a demo
// and the estimate on a real car call the same moments assist.
export const ASSIST_MPS2 = 0.4;
const STOP_KPH = 3;
const REGEN_THROTTLE = 5;        // %: a closed throttle
const ASSIST_THROTTLE = 30;      // %: a foot well into it
const FULL_REGEN_MPS2 = 2.0;     // braking this hard reads as full regen
const ACCEL_SPAN_MS = 2000;      // acceleration over the last two seconds
const SOC_SPAN_MS = 12000;       // the charge trend, as readings.js's IMA tile
const SOC_MOVED = 0.4;           // a resting pack jitters less than this
// The pack's assist current, as a full bar. A starting figure until the
// parked IMA session measures the real one.
export const PACK_AMPS_MAX = 100;
const MOTOR_KW = 10;             // lib/demoworld.py MOTOR_KW

const NOT_FOUND = Object.freeze({ value: null, source: "not found yet" });
const QUANTITY = { volts: "ima.volts", amps: "ima.amps", temp: "ima.temp" };

const speeds = [];   // {t, kph}
const socs = [];     // {t, soc}
let last = null;
// One computation per sample: a screen asks get() and read() of the same
// sample, and the history must take that sample once, not twice.
let seen = null;

// Module-wide history, as readings.js's IMA window: tests start from here.
export function resetHybrid() {
  speeds.length = 0;
  socs.length = 0;
  last = null;
  seen = null;
}

const num = (x) => (x === null || x === undefined || Number.isNaN(Number(x)) ? null : Number(x));
const clamp = (x) => Math.max(-1, Math.min(1, x));

function trim(list, now, span) {
  while (list.length && now - list[0].t > span) list.shift();
}

// The validated reading for a pack quantity, if this car has one with a value.
function measuredFor(quantity, values, car) {
  for (const sig of (car && car.signals) || []) {
    if (sig && sig.quantity === quantity) {
      const v = num(values[sig.id]);
      if (v !== null) return { value: v, source: "measured", unit: sig.unit || "" };
    }
  }
  return null;
}

function labelFor(value) {
  if (value === null) return "—";
  const pct = Math.round(Math.abs(value) * 100);
  if (pct < 5) return "Steady";
  return value > 0 ? `Assist ${pct}%` : `Charging ${pct}%`;
}

function flowOf(value, source, state) {
  return { value, source, state, label: state === "stop" && value === 0 ? "Stopped" : labelFor(value) };
}

// The estimate, from what every car sends. Returns a flow.
function estimate(values, now) {
  const kph = num(values.SPEED);
  const thr = num(values.THROTTLE_POS);
  const rpm = num(values.RPM);
  const soc = num(values.HYBRID_BATTERY_REMAINING);
  if (kph !== null) { speeds.push({ t: now, kph }); trim(speeds, now, ACCEL_SPAN_MS); }
  if (soc !== null) { socs.push({ t: now, soc }); trim(socs, now, SOC_SPAN_MS); }
  if (kph === null) return flowOf(null, "estimated", "unknown");
  if (kph < STOP_KPH) {
    const f = flowOf(0, "estimated", "stop");
    if (rpm === 0) f.label = "Auto stop";
    return f;
  }
  let accel = 0;
  if (speeds.length >= 2) {
    const a = speeds[0], b = speeds[speeds.length - 1];
    const secs = (b.t - a.t) / 1000;
    if (secs > 0) accel = ((b.kph - a.kph) / 3.6) / secs;
  }
  if (thr !== null && thr < REGEN_THROTTLE && accel < -ASSIST_MPS2) {
    return flowOf(-Math.min(1, -accel / FULL_REGEN_MPS2), "estimated", "regen");
  }
  if (thr !== null && thr > ASSIST_THROTTLE && accel > ASSIST_MPS2) {
    return flowOf(Math.max(0.1, Math.min(1, (thr - ASSIST_THROTTLE) / 70)), "estimated", "assist");
  }
  const drift = socs.length >= 3 ? socs[socs.length - 1].soc - socs[0].soc : 0;
  return flowOf(drift > SOC_MOVED ? -0.1 : 0, "estimated", drift > SOC_MOVED ? "regen" : "cruise");
}

// The demo world's own motor, which it publishes as demo.ima {state, kw}.
function simulated(sample) {
  const ima = sample.demo && sample.demo.ima;
  if (!sample.simulated || !ima) return null;
  const kw = num(ima.kw) || 0;
  const sign = ima.state === "assist" ? 1 : ima.state === "charge" ? -1 : 0;
  const state = ima.state === "charge" ? "regen" : ima.state === "idle" ? "cruise" : ima.state;
  const value = clamp(sign * kw / MOTOR_KW);
  const pack = {
    // A NiMH pack of this size sits near 144 V nominal; these are a demo's
    // numbers and say so, never the car's.
    volts: { value: Math.round((144 - value * 12) * 10) / 10, source: "simulated", unit: "V" },
    amps: { value: Math.round(value * PACK_AMPS_MAX), source: "simulated", unit: "A" },
    temp: { value: 31, source: "simulated", unit: "C" },
  };
  return { flow: flowOf(value, "simulated", state), pack };
}

export function hybridNow(sample, car, now = Date.now()) {
  const s = sample || {};
  if (seen && seen.sample === s && seen.car === car && sample) return seen.out;
  const out = compute(s, car, now);
  seen = { sample: s, car, out };
  return out;
}

function compute(s, car, now) {
  if (s.handover) {
    // A hand-off: the daemon republishes old values. Start the history again
    // from fresh readings only, and hand back what was last said, paused.
    speeds.length = 0;
    socs.length = 0;
    return last ? { ...last, paused: true } : null;
  }
  const values = s.values || {};
  const soc = num(values.HYBRID_BATTERY_REMAINING);
  const charge = soc === null ? NOT_FOUND : { value: soc, source: "measured", unit: "%" };

  const sim = simulated(s);
  const amps = measuredFor(QUANTITY.amps, values, car);
  let flow;
  if (amps) {
    // Positive current is assist (the pack discharging) by the profile's sign.
    const value = clamp(amps.value / PACK_AMPS_MAX);
    flow = flowOf(value, "measured", value > 0.05 ? "assist" : value < -0.05 ? "regen" : "cruise");
    estimate(values, now);   // keep the history warm should the reading drop
  } else if (sim) {
    flow = sim.flow;
  } else {
    flow = estimate(values, now);
  }

  const pick = (q) => measuredFor(QUANTITY[q], values, car) || (sim && sim.pack[q]) || NOT_FOUND;
  last = { flow, charge, volts: pick("volts"), amps: amps || pick("amps"), temp: pick("temp") };
  return last;
}
