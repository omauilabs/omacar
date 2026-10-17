import { eq, ok } from "./assert.js";
import { tpmsView, wheelView, homeTyre, mergeTyre, learnRows, bannerView, shouldSound, fmtP, agoText, PSI } from "../js/tpmsview.js";

// What the tyre screens say (share/js/tpmsview.js). Where a number came from is
// never blank; an unheard wheel is a dash, never a zero; strangers are only
// ever in the learn list.
const P = (psi) => psi * PSI;
const wheel = (k, psi, over = {}) => Object.assign({
  wheel: k, name: k, id: "id" + k, assigned: true, kpa: P(psi), temp_c: 20, p20_kpa: P(psi),
  placard_kpa: P(36), level: "ok", battery: "ok", rssi: -10, at: 100, age: 5, fresh: true,
  trend: [], slope_kpa_day: -0.1, leak: false, trend_note: "Holding", trend_n: 9, rapid: null,
}, over);
const st = (ws, over = {}) => Object.assign({
  present: true, source: "rtl_433", running: true, age: 2, alerts: [], radio: "ok",
  wheels: ws || [wheel("FL", 36), wheel("FR", 35), wheel("RL", 36), wheel("RR", 36)],
}, over);

export default [
  ["psi to one decimal, bar to two", () => eq([fmtP(P(35.04), true), fmtP(241.5, false)], [{ v: "35.0", u: "psi" }, { v: "2.42", u: "bar" }])],
  ["unknown pressure is a dash", () => eq([fmtP(null).v, fmtP(undefined, false).v], ["—", "—"])],
  ["ages read as words", () => eq([agoText(30), agoText(600), agoText(7200), agoText(300000)], ["30 s ago", "10 min ago", "2 h ago", "3 d ago"])],

  ["no sensor file yet: absent, with the way to start", () => {
    const v = tpmsView({ present: false });
    ok(v.state === "absent" && v.wheels.length === 4 && /learn/.test(v.note), v.note);
    eq(v.wheels.map((w) => w.pressure), ["—", "—", "—", "—"]);
  }],
  ["live from the radio says so, in the ok tone", () => {
    const v = tpmsView(st());
    eq([v.source, v.sourceTone, v.wheels[0].source, v.wheels[0].state], ["RTL-SDR · rtl_433", "ok", "Live · radio", "live"]);
  }],
  ["the simulator always says Simulated, on the screen and on every wheel", () => {
    const v = tpmsView(st(null, { source: "sim" }));
    eq([v.source, v.sourceTone, v.sim, v.wheels.map((w) => w.source)], ["Simulated", "warn", true, ["Simulated", "Simulated", "Simulated", "Simulated"]]);
  }],
  ["a wheel gone quiet keeps its number and says how long ago", () => {
    const w = wheelView(wheel("RL", 34, { fresh: false, age: 5400 }), st());
    eq([w.pressure, w.source, w.state, w.dim], ["34.0", "Last seen 2 h ago", "stale", true]);
  }],
  ["a service that stopped makes every wheel 'last seen'", () => {
    const v = tpmsView(st(null, { running: false, age: 900 }));
    ok(v.wheels.every((w) => /^Last seen/.test(w.source)), v.wheels[0].source);
  }],
  ["an unassigned wheel is a dash and an invitation, not 0", () => {
    const w = wheelView({ wheel: "RR", name: "Rear right", assigned: false, kpa: null }, st());
    eq([w.pressure, w.state, w.source, w.heard], ["—", "unassigned", "No sensor assigned", false]);
  }],
  ["an assigned wheel not yet heard says so", () => {
    const w = wheelView(wheel("FR", 0, { kpa: null, p20_kpa: null, level: null, at: null, age: null, fresh: false }), st());
    eq([w.pressure, w.source, w.state], ["—", "Not heard yet", "quiet"]);
  }],
  ["nothing assigned says how to assign", () => {
    const v = tpmsView(st(WHEELS_NONE()));
    ok(/Assign your sensors/.test(v.note) && !v.anyAssigned, v.note);
  }],
  ["assigned but nothing heard says to wait", () => {
    const quiet = ["FL", "FR", "RL", "RR"].map((k) => wheel(k, 0, { kpa: null, p20_kpa: null, level: null, at: null, fresh: false }));
    ok(/Waiting to hear/.test(tpmsView(st(quiet)).note), "note");
  }],
  ["the radio's complaint is the note", () => {
    const v = tpmsView(st(null, { radio: "error", note: "No RTL-SDR found: is it plugged in?", running: false }));
    eq(v.note, "No RTL-SDR found: is it plugged in?");
  }],
  ["levels become words and tones", () => {
    const f = (psi, level) => wheelView(wheel("FL", psi, { level }), st());
    eq([f(36, "ok").word, f(31, "warn").word, f(26, "bad").word, f(26, "bad").tone], ["Good", "Low", "Very low", "bad"]);
  }],
  ["a high tyre is 'High', not 'Low'", () => eq(wheelView(wheel("FL", 46, { level: "warn" }), st()).word, "High")],
  ["rapid loss wins over the level", () => {
    const w = wheelView(wheel("FL", 20, { level: "bad", rapid: { from: 250, to: 130, frac: 0.4, at: 99 } }), st());
    eq([w.word, w.tone], ["Losing air fast", "bad"]);
  }],
  ["...but only while the service is running", () => {
    const w = wheelView(wheel("FL", 20, { level: "bad", rapid: { from: 250, to: 130, frac: 0.4, at: 99 } }), st(null, { running: false }));
    ok(w.word !== "Losing air fast", w.word);
  }],
  ["against the placard, in words", () => {
    const f = (psi) => wheelView(wheel("FL", psi, { p20_kpa: P(psi) }), st()).vs;
    eq([f(36), f(32.4), f(45)], ["", "10 % low", "25 % high"]);
  }],
  ["a tyre in metric is bar and Celsius", () => {
    const w = wheelView(wheel("FL", 36, { temp_c: 21 }), st(), { imperial: false });
    eq([w.pressure, w.unit, w.tempUnit, w.temp], ["2.48", "bar", "°C", "21.0"]);
  }],
  ["°F in imperial", () => eq(wheelView(wheel("FL", 36, { temp_c: 21 }), st()).temp, "70")],
  ["a low sensor battery is said and flagged", () => {
    const w = wheelView(wheel("FL", 36, { battery: "low" }), st());
    eq([w.battery, w.batteryLow], ["Sensor battery low", true]);
  }],
  ["a leak says how fast, in the owner's units", () => {
    const w = wheelView(wheel("RL", 31, { leak: true, slope_kpa_day: -P(0.4) }), st());
    eq([w.trend.text, w.trend.tone], ["Losing 0.4 psi a day", "warn"]);
  }],
  ["no verdict yet says so rather than 'fine'", () => {
    const w = wheelView(wheel("RL", 36, { slope_kpa_day: null, trend_n: 2 }), st());
    eq(w.trend.text, "Trend needs a few more mornings");
  }],
  ["the banner picks a rapid alert over a leak", () => {
    const v = tpmsView(st(null, { alerts: [{ wheel: "RL", kind: "leak", at: 1 }, { wheel: "FR", kind: "rapid", at: 2 }] }));
    eq(v.banner.kind, "rapid");
  }],

  ["Home: all four fine is a range, in psi", () => {
    const h = homeTyre(st());
    eq([h.text, h.tone], ["35–36 psi", "ok"]);
  }],
  ["Home: one low tyre is named, with its number", () => {
    const h = homeTyre(st([wheel("FL", 36), wheel("FR", 36), wheel("RL", 31, { level: "warn" }), wheel("RR", 36)]));
    eq([h.text, h.tone], ["RL 31.0 psi · low", "warn"]);
  }],
  ["Home: very low", () => {
    const h = homeTyre(st([wheel("FL", 36), wheel("FR", 36), wheel("RL", 26, { level: "bad" }), wheel("RR", 36)]));
    eq([h.text, h.tone], ["RL 26.0 psi · very low", "bad"]);
  }],
  ["Home: rapid loss is the whole message", () => {
    const h = homeTyre(st([wheel("FL", 36), wheel("FR", 20, { level: "bad", rapid: { at: 1, frac: 0.3 } }), wheel("RL", 36), wheel("RR", 36)]));
    eq([h.text, h.tone], ["FR losing air fast", "bad"]);
  }],
  ["Home: a slow leak on tyres that are still in range", () => {
    const h = homeTyre(st(null, { alerts: [{ wheel: "RL", kind: "leak", at: 1 }] }));
    eq([h.text, h.tone], ["RL slow leak", "warn"]);
  }],
  ["Home: the simulator is named on the callout", () => {
    const h = homeTyre(st(null, { source: "sim" }));
    ok(/Simulated$/.test(h.text), h.text);
  }],
  ["Home: only two heard says 'of 4'", () => {
    const h = homeTyre(st([wheel("FL", 36), wheel("FR", 35), wheel("RL", 0, { kpa: null, p20_kpa: null, level: null }), wheel("RR", 0, { kpa: null, p20_kpa: null, level: null })]));
    eq(h.text, "35–36 psi · 2 of 4");
  }],
  ["Home: stale readings are dim and dated", () => {
    const h = homeTyre(st(null, { running: false }));
    ok(/ago$/.test(h.text) && h.tone === "", h.text + "|" + h.tone);
  }],
  ["Home: nothing heard leaves the car's own state alone", () => {
    eq([homeTyre({ present: false }), homeTyre(null), homeTyre(st(WHEELS_NONE()))], [null, null, null]);
  }],
  ["Home: the car's worse warning beats a fine radio reading, the stale one never hides the car's", () => {
    const radio = { text: "35–36 psi", tone: "ok" };
    eq(mergeTyre(radio, { text: "Check", tone: "warn" }).text, "Check");
    eq(mergeTyre(radio, { text: "OK", tone: "ok" }).text, "35–36 psi");
    eq(mergeTyre({ text: "35 psi · 3 h ago", tone: "" }, { text: "OK", tone: "ok" }).text, "35 psi · 3 h ago");
    eq(mergeTyre(null, { text: "Not read", tone: "" }).text, "Not read");
    eq(mergeTyre({ text: "RL 26 psi · very low", tone: "bad" }, { text: "Check", tone: "warn" }).tone, "bad");
  }],

  ["learn: the sensor that lost air floats to the top", () => {
    const heard = [{ id: "aa", kpa: P(36), rssi: -8, model: "J", wheel: null, age: 3 },
                   { id: "bb", kpa: P(33), rssi: -14, model: "J", wheel: null, age: 3 },
                   { id: "cc", kpa: P(35), rssi: -20, model: "J", wheel: "FL", age: 3 }];
    const rows = learnRows(heard, { aa: P(36), bb: P(36), cc: P(35) });
    eq(rows.map((r) => r.id), ["bb", "aa", "cc"]);
    eq([rows[0].delta, rows[0].dropped, rows[1].delta, rows[1].dropped], ["−3.0", true, "±0", false]);
    eq(rows[2].wheel, "FL");
  }],
  ["learn: a new sensor has no change yet, and a stranger is just a row", () => {
    const rows = learnRows([{ id: "zz", kpa: P(30), rssi: null, model: "?", wheel: null, age: 1 }], {});
    eq([rows[0].delta, rows[0].rssi, rows[0].dropped], ["", "—", false]);
  }],
  ["learn: metric deltas are bar", () => {
    const rows = learnRows([{ id: "aa", kpa: 200, rssi: -9, model: "J", wheel: null, age: 1 }], { aa: 240 }, { imperial: false });
    eq([rows[0].delta, rows[0].dropped, rows[0].unit], ["−0.40", true, "bar"]);
  }],

  ["banner: a rapid loss while running", () => {
    const b = bannerView(st([wheel("FR", 20), wheel("FL", 36), wheel("RL", 36), wheel("RR", 36)],
      { alerts: [{ wheel: "FR", kind: "rapid", at: 1234.4, frac: 0.31, tone: "bad" }] }));
    eq([b.wheel, b.title, b.pct, b.key], ["FR", "Front right tire is losing air fast", 31, "FR:1234"]);
  }],
  ["banner: never for a slow leak", () => eq(bannerView(st(null, { alerts: [{ wheel: "RL", kind: "leak", at: 1 }] })), null)],
  ["banner: never from a service that stopped", () =>
    eq(bannerView(st(null, { running: false, alerts: [{ wheel: "RL", kind: "rapid", at: 1, frac: 0.3 }] })), null)],
  ["sound: once per event, and only while moving", () => {
    const b = { key: "FR:1" };
    eq([shouldSound(b, { moving: false }), shouldSound(b, { moving: true }),
        shouldSound(b, { moving: true, seen: new Set(["FR:1"]) }), shouldSound(null, { moving: true })], [false, true, false, false]);
  }],
];

function WHEELS_NONE() {
  return ["FL", "FR", "RL", "RR"].map((k) => ({ wheel: k, name: k, assigned: false, kpa: null, level: null }));
}
