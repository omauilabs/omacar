// The hybrid picture every screen draws from (share/js/hybrid.js).
import { eq, ok } from "./assert.js";
import { hybridNow, resetHybrid, PACK_AMPS_MAX } from "../js/hybrid.js";

const car = (signals = []) => ({ signals });
const live = (values, extra = {}) => ({ connected: true, values, ...extra });

// Two samples a second apart, so the estimate has an acceleration to read.
function twoSamples(a, b, c = car()) {
  resetHybrid();
  hybridNow(live(a), c, 1000);
  return hybridNow(live(b), c, 2000);
}

export default [
  ["braking with the throttle closed is estimated regen", () => {
    const h = twoSamples({ SPEED: 60, THROTTLE_POS: 0, RPM: 1200 },
                         { SPEED: 54, THROTTLE_POS: 0, RPM: 1100 });
    eq(h.flow.source, "estimated");
    eq(h.flow.state, "regen");
    ok(h.flow.value < 0, "negative is charging: " + h.flow.value);
    ok(h.flow.label.startsWith("Charging"), h.flow.label);
  }],
  ["hard throttle while speeding up is estimated assist", () => {
    const h = twoSamples({ SPEED: 40, THROTTLE_POS: 80 }, { SPEED: 46, THROTTLE_POS: 80 });
    eq(h.flow.state, "assist");
    ok(h.flow.value > 0.5, String(h.flow.value));
    ok(h.flow.label.startsWith("Assist"), h.flow.label);
  }],
  ["a steady cruise is steady", () => {
    const h = twoSamples({ SPEED: 90, THROTTLE_POS: 20 }, { SPEED: 90, THROTTLE_POS: 20 });
    eq(h.flow.value, 0);
    eq(h.flow.label, "Steady");
  }],
  ["stopped is zero, and an engine at rest is auto stop", () => {
    const h = twoSamples({ SPEED: 0, RPM: 0 }, { SPEED: 0, RPM: 0 });
    eq(h.flow.value, 0);
    eq(h.flow.label, "Auto stop");
  }],
  ["a hand-off clears the history and hands back the last picture, paused", () => {
    resetHybrid();
    hybridNow(live({ SPEED: 60, THROTTLE_POS: 0 }), car(), 1000);
    const before = hybridNow(live({ SPEED: 54, THROTTLE_POS: 0 }), car(), 2000);
    eq(before.flow.state, "regen");
    const paused = hybridNow({ handover: true, values: { SPEED: 30 } }, car(), 3000);
    ok(paused.paused, "paused");
    eq(paused.flow.value, before.flow.value);
    // The first reading after it has nothing to compare with: without the
    // reset, 54 -> 30 km/h across the hand-off would read as hard regen.
    const after = hybridNow(live({ SPEED: 30, THROTTLE_POS: 0 }), car(), 4000);
    eq(after.flow.value, 0);
  }],
  ["the demo's motor is labelled simulated, never measured", () => {
    resetHybrid();
    const h = hybridNow(live({ SPEED: 50, HYBRID_BATTERY_REMAINING: 60 },
                             { simulated: true, demo: { ima: { state: "assist", kw: 5 } } }),
                        car(), 1000);
    eq(h.flow.source, "simulated");
    eq(h.flow.value, 0.5);
    eq(h.volts.source, "simulated");
    eq(h.amps.source, "simulated");
    eq(h.charge.source, "measured");
  }],
  ["a validated pack-current reading outranks the estimate", () => {
    resetHybrid();
    const c = car([{ id: "ima_amps", name: "Pack current", unit: "A", quantity: "ima.amps" }]);
    const h = hybridNow(live({ SPEED: 60, THROTTLE_POS: 0, ima_amps: PACK_AMPS_MAX / 2 }), c, 1000);
    eq(h.flow.source, "measured");
    eq(h.flow.value, 0.5);
    eq(h.amps.value, PACK_AMPS_MAX / 2);
  }],
  ["a reading with no quantity is not taken for pack current", () => {
    resetHybrid();
    const c = car([{ id: "mystery", name: "Something", unit: "A" }]);
    const h = hybridNow(live({ SPEED: 60, mystery: 40 }), c, 1000);
    eq(h.flow.source, "estimated");
    eq(h.amps.source, "not found yet");
    eq(h.volts.source, "not found yet");
    eq(h.temp.source, "not found yet");
  }],
  ["no charge reading is not found, not zero", () => {
    resetHybrid();
    const h = hybridNow(live({ SPEED: 10 }), car(), 1000);
    eq(h.charge.value, null);
    eq(h.charge.source, "not found yet");
  }],
  ["with no car the flow reading waits, in the words every reading uses", async () => {
    const { READINGS } = await import("../js/readings.js");
    resetHybrid();
    eq(READINGS.flow.get({}, { connected: false, values: {} }, car()).n, "Waiting for the car");
    resetHybrid();
    eq(READINGS.flow.get({}, { connected: true, values: {} }, car()).n, "no speed reading");
  }],
];
