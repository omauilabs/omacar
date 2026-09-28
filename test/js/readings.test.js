import { eq } from "./assert.js";
import { READINGS, readingState } from "../js/readings.js";

const live = (values, supported) => ({ connected: true, values, supported });

export default [
  ["a supported reading with a value is live", () =>
    eq(readingState(READINGS.coolant, live({ COOLANT_TEMP: 88 }, ["COOLANT_TEMP"])), "live")],
  ["a reading the car does not support is absent", () =>
    eq(readingState(READINGS.charge, live({ COOLANT_TEMP: 88 }, ["COOLANT_TEMP"])), "absent")],
  ["a supported reading with no value yet is waiting", () =>
    eq(readingState(READINGS.coolant, live({}, ["COOLANT_TEMP"])), "waiting")],
  ["with no car it is waiting, never absent", () =>
    eq(readingState(READINGS.coolant, { connected: false, values: {} }), "waiting")],
  ["an unknown supported list never claims absent", () =>
    eq(readingState(READINGS.charge, live({}, undefined)), "waiting")],
  ["zero is a value, not a gap", () =>
    eq(readingState(READINGS.fuel, live({ FUEL_LEVEL: 0 }, ["FUEL_LEVEL"])), "live")],
  ["a derived reading has no pid and draws its own dash", () =>
    eq(readingState(READINGS.odometer, { connected: false }), "live")],
  ["the hybrid pack reads the generic remaining-life PID", () =>
    eq(READINGS.charge.pid, "HYBRID_BATTERY_REMAINING")],
  ["the catalogue is frozen", () => eq(Object.isFrozen(READINGS), true)],
];
