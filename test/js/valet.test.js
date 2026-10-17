import { eq, ok } from "./assert.js";
import { privacy } from "../js/privacy.js";
import { valet, HIDDEN_VIEWS, HIDDEN_CARDS, hidesView, hidesCard, landing, lockReason, padKey,
         cooldownText, duration, reportView, NOT_AVAILABLE } from "../js/valet.js";

// Valet mode's rules (share/js/valet.js): what hides, where a deep link lands,
// the PIN pad, and how the "While you were away" card reads.

const rep = (o = {}) => Object.assign({
  started: 1000, ended: 1000 + 4320, tracked: true, simulated: false,
  top_kmh: 148.0, fast_s: 250, distance_km: 52.4, distance_basis: "odometer", hard_brakes: 3,
}, o);
const at = (t) => "t" + t;

export default [
  ["off: nothing is hidden, nothing is redirected", () => {
    for (const id of HIDDEN_VIEWS) eq(hidesView(id, false), false, id);
    eq(landing("garage", false), { id: "garage", notice: null });
  }],
  ["on: every personal screen is hidden", () => {
    for (const id of ["work", "garage", "history", "documents", "report", "replay", "roadcams"]) {
      ok(hidesView(id, true), id + " should hide");
    }
  }],
  ["on: the car's own screens, the fridge and the air stay", () => {
    for (const id of ["home", "vehicle", "codes", "drive", "live", "data", "cabin", "fridge", "environment",
                      "eq", "advisor", "cameras", "radar", "navigation", "ima", "service"]) {
      ok(!hidesView(id, true), id + " should stay");
    }
  }],
  ["a hidden screen is not routable: it lands on Home with a quiet notice", () =>
    eq(landing("work", true), { id: "home", notice: "Not available in Valet mode" })],
  ["the notice is the one string the toast shows", () => eq(NOT_AVAILABLE, "Not available in Valet mode")],
  ["a visible screen routes as asked", () => eq(landing("fridge", true), { id: "fridge", notice: null })],
  ["Home cards: the Agent and the pinned road camera go, the fridge and air stay", () => {
    ok(hidesCard("agent", true) && hidesCard("nav", true));
    for (const id of ["fridge", "env", "dial", "car", "coolant"]) ok(!hidesCard(id, true), id);
    ok(HIDDEN_CARDS.has("agent"));
  }],
  ["lock words: valet outranks moving, and nothing is nothing", () => {
    eq(lockReason(true, true), "Locked in Valet mode");
    eq(lockReason(true, false), "Locked in Valet mode");
    eq(lockReason(false, true), "Available when you stop");
    eq(lockReason(false, false), "");
  }],
  ["apply() reflects the server and tells listeners about on/off only", () => {
    const heard = [];
    const off = valet.onChange((on) => heard.push(on));
    valet.apply({ on: true, has_pin: true, configured: true, recording: true });
    valet.apply({ on: true, has_pin: true, configured: true, recording: false });  // a flicker
    ok(valet.on && valet.hasPin && !valet.recording);
    valet.apply({ on: false, has_pin: true, configured: true, last_report: rep() });
    eq(heard, [true, false]);
    eq(valet.report.top_kmh, 148.0);
    off();
    ok(!valet.apply(null), "garbage changes nothing");
  }],
  ["valet implies privacy, and the owner's own setting comes back after", () => {
    privacy.on = false;
    valet.apply({ on: false });
    ok(!privacy.on, "off and unset");
    valet.apply({ on: true });
    ok(privacy.on, "valet on forces privacy");
    valet.apply({ on: false });
    ok(!privacy.on, "and releases it");
  }],
  ["the PIN pad: four digits and no more", () => {
    let d = "";
    for (const k of ["1", "2", "3", "4", "5"]) d = padKey(d, k);
    eq(d, "1234");
  }],
  ["the PIN pad: delete, clear, and nothing that is not a digit", () => {
    eq(padKey("123", "back"), "12");
    eq(padKey("", "back"), "");
    eq(padKey("123", "clear"), "");
    eq(padKey("12", "x"), "12");
    eq(padKey("12", "10"), "12");
  }],
  ["the cooldown reads as m:ss, then seconds", () => {
    eq(cooldownText(300), "5:00");
    eq(cooldownText(61), "1:01");
    eq(cooldownText(42), "42 s");
    eq(cooldownText(-3), "0 s");
  }],
  ["durations", () => {
    eq([duration(40), duration(60), duration(250), duration(4320), duration(3600), duration(5400)],
       ["40 s", "1 min", "4 min 10 s", "1 h 12 min", "1 h", "1 h 30 min"]);
  }],
  ["the report, in miles: top speed, time over 80 mph, distance, hard stops", () => {
    const v = reportView(rep(), { imperial: true, time: at });
    eq(v.rows.map((r) => [r.key, r.value]),
       [["away", "1 h 12 min"], ["top", "92 mph"], ["fast", "4 min 10 s"], ["dist", "33 mi"], ["brakes", "3"]]);
    eq(v.rows[0].sub, "t1000 to t5320");
    eq(v.rows[2].label, "Above 80 mph");
    ok(v.tracked && !v.simulated);
  }],
  ["the report, in kilometres", () => {
    const v = reportView(rep(), { imperial: false, time: at });
    eq(v.rows[1].value, "148 km/h");
    eq(v.rows[2].label, "Above 129 km/h");
    eq(v.rows[3].value, "52 km");
  }],
  ["a quiet drive reads as good news, not as a blank", () => {
    const v = reportView(rep({ top_kmh: 60, fast_s: 0, hard_brakes: 0, distance_km: 3.2 }), { time: at });
    eq(v.rows[2].value, "Never");
    eq(v.rows[2].tone, "ok");
    eq(v.rows[4].tone, "ok");
    eq(v.rows[3].value, "2.0 mi");
  }],
  ["no log: the times, and a sentence saying so, never zeros", () => {
    const v = reportView({ started: 1000, ended: 2000, tracked: false }, { time: at });
    eq(v.rows.length, 1);
    ok(!v.tracked && /not running/.test(v.note));
  }],
  ["a simulated trace says so", () =>
    ok(reportView(rep({ simulated: true }), { time: at }).simulated === true)],
  ["no report, no card", () => eq(reportView(null), null)],
  ["the rows hold no place", () => {
    const j = JSON.stringify(reportView(rep(), { time: at }));
    ok(!/lat|lon|gps|route|address/i.test(j.replace(/simulated/g, "")), j);
  }],
];
