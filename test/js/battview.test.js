import { eq } from "./assert.js";
import { batteryView, tempText, healthTone, yRange, warnText, ago } from "../js/battview.js";

const live = (over = {}, r = {}) => Object.assign({
  present: true, source: "sysfs", stale: false, age: 5, warning: null, limit_pct: 50,
  reading: Object.assign({ capacity: 63, status: "Charging", charging: true, unit: "Wh", full: 38.7,
    design: 43.0, health: 90.0, cycles: 88, temp_c: 41.2 }, r),
  limit: { state: "unknown", why: "Needs 3 days" }, projection: { state: "learning", text: "Needs about three weeks" },
  days: [{ day: "2026-03-01", health: 91, peak_cap: 100, charge_samples: 200 },
         { day: "2026-03-02", health: null, peak_cap: 50, charge_samples: 10 },
         { day: "2026-03-03", health: 90, peak_cap: 50, charge_samples: 200 }],
}, over);

export default [
  ["no data says how to start, never a zero", () => {
    const v = batteryView({ present: false });
    eq([v.state, v.healthText, v.cycles, v.temp], ["absent", "—", "Not reported", "Not reported"]);
    eq(v.line.includes("omacar tabletbatt run"), true);
  }],
  ["the demo says it has no battery", () => eq(batteryView({ present: false, demo: true }).line, "The demo has no tablet battery.")],
  ["a sensor reading names its source and shows health", () => {
    const v = batteryView(live());
    eq([v.source, v.sourceTone, v.healthText, v.full, v.design], ["Battery sensor", "ok", "90.0", "38.7 Wh", "43.0 Wh"]);
  }],
  ["the simulator is always labelled Simulated", () => {
    const v = batteryView(live({ source: "sim" }));
    eq([v.source, v.sourceTone], ["Simulated", "warn"]);
  }],
  ["a stale sample says how old", () => eq(batteryView(live({ stale: true, age: 900 })).source, "Last seen 15 min ago")],
  ["missing attributes read Not reported, not 0", () => {
    const v = batteryView(live({}, { cycles: null, temp_c: null, unit: null, full: null, design: null, health: null }));
    eq([v.cycles, v.temp, v.full, v.healthText], ["Not reported", "Not reported", "Not reported", "—"]);
    eq(v.health, null);
  }],
  ["°F and °C", () => eq([tempText(41.2, true), tempText(41.2, false), tempText(null, true)], ["106°F", "41.2°C", "Not reported"])],
  ["health bands", () => eq([healthTone(92), healthTone(82), healthTone(79), healthTone(null)], ["ok", "warn", "bad", ""])],
  ["limit labels", () => {
    eq(batteryView(live({ limit: { state: "on", why: "x" } })).limit.label, "Likely on");
    eq(batteryView(live({ limit: { state: "off", why: "x" } })).limit.label, "Not active");
    eq(batteryView(live()).limit.label, "Can't tell yet");
  }],
  ["the trend skips days with no health; peaks skip short charges", () => {
    const v = batteryView(live());
    eq(v.trend.length, 2);
    eq(v.peaks.map((p) => p[1]), [100, 50]);
  }],
  ["warning only while fresh", () => {
    eq(warnText(live({ warning: "Hot" })), "Hot");
    eq(warnText(live({ warning: "Hot", stale: true })), null);
    eq(warnText({ present: false }), null);
  }],
  ["y range never flatter than four points", () => {
    const [lo, hi] = yRange([[0, 95.2], [1, 95.4]]);
    eq(hi - lo >= 4, true);
  }],
  ["ago", () => eq([ago(30), ago(7200), ago(3 * 86400)], ["30 s ago", "2 h ago", "3 days ago"])],
];
