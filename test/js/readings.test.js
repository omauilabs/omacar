import { eq } from "./assert.js";
import { READINGS, readingState } from "../js/readings.js";
import { makeSignalTile } from "../js/sigtile.js";
import { loadCatalogue } from "../js/homecards.js";

const live = (values, supported) => ({ connected: true, values, supported });

const base = [
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

// ONE SET OF NAMES. Gauges drew the catalogue's own labels ("Battery", "IMA
// pack") while Home and Vehicle said "12V system" and "Hybrid pack" for the
// same readings -- the drift the catalogue was centralised to stop.

const labels = [
  ["the catalogue says 12V system and Hybrid pack", () =>
    eq([READINGS.volts.label, READINGS.charge.label], ["12V system", "Hybrid pack"])],
  ["a tile with no label of its own wears the catalogue's", () =>
    eq(makeSignalTile("volts").node.querySelector(".sig-k").textContent, "12V system")],
  ["and every Home card that shows a reading is named as the catalogue names it", async () => {
    const cat = await loadCatalogue();
    const off = Object.values(cat.cards).filter((c) => c.reading && c.label !== READINGS[c.reading].label)
      .map((c) => `${c.reading}: ${c.label} vs ${READINGS[c.reading].label}`);
    eq(off, [], "mismatched names");
  }],
];

export default [...base, ...labels];
