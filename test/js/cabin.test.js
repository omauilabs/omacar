import { eq } from "./assert.js";
import { tileStatus } from "../js/views/cabin.js";

// What the Cabin tab's Air and Sound tiles say (share/js/views/cabin.js).
const t = (c) => `${Math.round(c)}°`;
const env = (over = {}) => Object.assign({
  present: true, source: "sensor",
  cabin: { state: "live", values: { t_c: 22.2, co2: 812 } },
  outside: { state: "live", values: { t_c: 18.6 } },
}, over);

export default [
  ["nothing yet: the tile keeps its standing words", () =>
    eq([tileStatus("air", null), tileStatus("air", { present: false })], [null, null])],
  ["live sensors: cabin, CO₂ and outside, lamp ok", () =>
    eq(tileStatus("air", env(), t), { line: "Cabin 22° · CO₂ 812 · Outside 19°", need: "Live", tone: "ok" })],
  ["the simulator says Simulated, in the warn tone", () =>
    eq(tileStatus("air", env({ source: "sim" }), t).need, "Simulated")],
  ["a cabin with no node is left out, not zeroed", () =>
    eq(tileStatus("air", env({ cabin: { state: "absent", values: {} } }), t).line, "Outside 19°")],
  ["a stale reading says so", () =>
    eq(tileStatus("air", env({ cabin: { state: "stale", values: { t_c: 20 } } }), t).need, "Last reading")],
  ["EQ on and applied", () =>
    eq(tileStatus("sound", { present: true, applied: true, state: { enabled: true, preset: "Car cabin" } }),
       { line: "On · Car cabin", need: "Shaping the AUX output", tone: "ok" })],
  ["EQ bypassed", () =>
    eq(tileStatus("sound", { present: true, state: { enabled: false } }).line, "Bypassed")],
  ["no PipeWire says so rather than pretending", () =>
    eq(tileStatus("sound", { present: false, state: { enabled: true, preset: "Bass" } }).need,
       "Not applied here: no PipeWire")],
];
