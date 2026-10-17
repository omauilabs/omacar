import { eq } from "./assert.js";
import { parkView, parkRow, whenOf, agoOf, coords, mapsText } from "../js/parkspot.js";

// What the Parked screen and Home's parking card say (share/js/parkspot.js).
// A park with no fix never shows a place; the level is always an estimate; a
// rehearsal always says Simulated.
const NOW = new Date(2026, 9, 17, 15, 0, 0).getTime() / 1000;
const at = (h, m, daysAgo = 0) => new Date(2026, 9, 17 - daysAgo, h, m, 0).getTime() / 1000;
const park = (over = {}) => Object.assign({
  id: 7, at: at(14, 32), simulated: false,
  fix: { lat: 37.7842, lon: -122.4079, accuracy_m: 6, age_s: 0, source: "gpsd" },
  level: { estimate: true, label: "P3", levels: -3, detail: "about 3 levels below the street" },
  photos: { front: "/api/parking/photo/7/front", rear: "/api/parking/photo/7/rear" },
}, over);
const pub = (...ps) => ({ present: true, last: ps[0], history: ps });

export default [
  ["nothing saved yet says when it will be", () => {
    const v = parkView({ present: false, last: null, history: [] }, { now: NOW });
    eq([v.state, v.line, v.current], ["none", "Nothing saved yet", null]);
  }],
  ["a park shows its time, how long ago, and where the GPS fix came from", () => {
    const v = parkView(pub(park()), { now: NOW });
    eq([v.state, v.line, v.sub, v.source, v.sourceTone], ["park", "14:32", "28 min ago", "GPS", "ok"]);
  }],
  ["coordinates to five places, and the Copy text to six with no space", () => {
    const f = park().fix;
    eq([coords(f), mapsText(f)], ["37.78420, -122.40790", "37.784200,-122.407900"]);
  }],
  ["no fix: it says so, and there is nothing to copy or draw", () => {
    const c = parkView(pub(park({ fix: null })), { now: NOW }).current;
    eq([c.fix.has, c.fix.text, c.fix.copy], [false, "No GPS fix at the time", null]);
  }],
  ["no fix is not a made-up zero", () => {
    const c = parkView(pub(park({ fix: { lat: null, lon: null } })), { now: NOW }).current;
    eq([c.fix.has, c.fix.copy], [false, null]);
  }],
  ["a fix from some seconds before says how old it was", () => {
    const c = parkRow(park({ fix: { lat: 1, lon: 2, accuracy_m: 9, age_s: 40 } }), NOW);
    eq(c.fix.accuracy, "± 9 m · last fix 40 s before");
  }],
  ["the level is always called an estimate", () => {
    const l = parkRow(park(), NOW).level;
    eq([l.label, l.line], ["P3", "level ≈ P3 (estimate)"]);
  }],
  ["street level is also an estimate", () =>
    eq(parkRow(park({ level: { estimate: true, label: "street level", detail: "" } }), NOW).level.line, "street level (estimate)")],
  ["no pressure data: no level at all, not a zero", () => eq(parkRow(park({ level: null }), NOW).level, null)],
  ["a simulated park says Simulated, in the warn tone, before anything else", () => {
    const v = parkView(pub(park({ simulated: true })), { now: NOW });
    eq([v.source, v.sourceTone], ["Simulated", "warn"]);
  }],
  ["a park with no fix and not simulated says No GPS fix", () =>
    eq(parkView(pub(park({ fix: null })), { now: NOW }).source, "No GPS fix")],
  ["the rear still comes first, then the front; a missing one says so", () => {
    const c = parkRow(park({ photos: { front: "/f" } }), NOW);
    eq(c.stills.map((s) => [s.role, s.url, s.note]), [["rear", null, "No rear photo"], ["front", "/f", ""]]);
  }],
  ["the history picks the park you tap, defaulting to the newest", () => {
    const p = pub(park({ id: 9, at: at(16, 0) }), park({ id: 8, at: at(9, 5, 1) }));
    eq([parkView(p, { now: NOW }).current.id, parkView(p, { now: NOW, pick: 8 }).current.id,
        parkView(p, { now: NOW, pick: 99 }).current.id], [9, 8, 9]);
  }],
  ["yesterday and older days are named", () =>
    eq([whenOf(at(9, 5, 1), NOW), whenOf(at(9, 5, 3), NOW), whenOf(at(14, 32), NOW)],
       ["Yesterday 09:05", "Wed 09:05", "14:32"])],
  ["how long ago, in words", () =>
    eq([agoOf(NOW - 20, NOW), agoOf(NOW - 1500, NOW), agoOf(NOW - 7200, NOW), agoOf(NOW - 3 * 86400, NOW)],
       ["just now", "25 min ago", "2 h ago", "3 days ago"])],
];
