import { eq, ok } from "./assert.js";
import { badge, footerLine, sourceKey } from "../js/provenance.js";

// records.live() mid-hand-off, re-stamped `ago` seconds from now.
const handover = (ago = 0) => ({ connected: false, status: "yielded", handover: true,
                                 t: Date.now() / 1000 + ago, kind: "wired" });

export default [
  ["no server says so, not 'no car'", () => eq(badge(null, null, "404").text, "NO SERVER")],
  // The server went away after the first snapshot. The snapshot is still in
  // hand and still says connected; it is not current any more.
  ["losing the server after boot is still no server", () =>
    eq(badge({ live: { connected: true } }, null, true).text, "NO SERVER")],
  ["before the first snapshot it is starting", () => eq(badge(null, null, null).text, "STARTING")],
  ["the simulator is named before its numbers", () =>
    eq(badge({ simulated: true }, { connected: true }).text, "SIMULATED")],
  ["the bench is named", () => eq(badge({}, { connected: true, kind: "bench" }).text, "BENCH")],
  ["a real car is live", () => eq(badge({}, { connected: true, kind: "OBDLink SX" }).text, "LIVE · OBD-II")],
  ["no link is no car", () => eq(badge({}, { connected: false }).text, "NO CAR")],
  // A HAND-OFF IS NOT A DISCONNECTION, and the badge on every screen must not
  // say it is while the screens under it say "Paused · adapter in use".
  ["a hand-off is paused, not no car, and says who has the adapter", () => {
    const b = badge({}, handover());
    eq(b.text, "PAUSED");
    ok(!/has not answered/.test(b.title) && /adapter in use/.test(b.title)
       && /recorder/.test(b.title) && /sweep/.test(b.title),
       `the title: ${b.title}`);
  }],
  ["a real disconnection is still no car", () =>
    eq([badge({}, { connected: false, status: "lost" }).text,
        badge({}, { connected: false, status: "no daemon", stale_for: 40, values: {} }).text],
       ["NO CAR", "NO CAR"])],
  ["and so is a hand-off that has stopped being re-stamped", () =>
    eq(badge({}, handover(-40)).text, "NO CAR")],
  ["the simulator is still named first, hand-off or not", () =>
    eq(badge({ simulated: true }, handover()).text, "SIMULATED")],
  ["the footer says simulated", () =>
    eq(footerLine({ simulated: true, signals: [] }, {}), "Vehicle data: OBD-II · simulated")],
  ["the footer names enhanced signals only when there are some", () =>
    eq(footerLine({ signals: [{ id: "x" }] }, { connected: true }), "Vehicle data: OBD-II + Honda enhanced · live")],
  ["source keys", () => eq([
    sourceKey({ simulated: true }, {}), sourceKey({}, { kind: "bench" }),
    sourceKey({}, { connected: true }), sourceKey({}, { connected: false })],
    ["sim", "bench", "obd", "recorded"])],
];
