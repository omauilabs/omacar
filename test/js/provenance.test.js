import { eq } from "./assert.js";
import { badge, footerLine, sourceKey } from "../js/provenance.js";

export default [
  ["no server says so, not 'no car'", () => eq(badge(null, null, "404").text, "NO SERVER")],
  ["before the first snapshot it is starting", () => eq(badge(null, null, null).text, "STARTING")],
  ["the simulator is named before its numbers", () =>
    eq(badge({ simulated: true }, { connected: true }).text, "SIMULATED")],
  ["the bench is named", () => eq(badge({}, { connected: true, kind: "bench" }).text, "BENCH")],
  ["a real car is live", () => eq(badge({}, { connected: true, kind: "OBDLink SX" }).text, "LIVE · OBD-II")],
  ["no link is no car", () => eq(badge({}, { connected: false }).text, "NO CAR")],
  ["the footer says simulated", () =>
    eq(footerLine({ simulated: true, signals: [] }, {}), "Vehicle data: OBD-II · simulated")],
  ["the footer names enhanced signals only when there are some", () =>
    eq(footerLine({ signals: [{ id: "x" }] }, { connected: true }), "Vehicle data: OBD-II + Honda enhanced · live")],
  ["source keys", () => eq([
    sourceKey({ simulated: true }, {}), sourceKey({}, { kind: "bench" }),
    sourceKey({}, { connected: true }), sourceKey({}, { connected: false })],
    ["sim", "bench", "obd", "recorded"])],
];
