import { eq, ok } from "./assert.js";
import { watchView, chartModel, ago, fmtV } from "../js/bwatchcard.js";

// What the 12 V card says (share/js/bwatchcard.js). Where the number came from
// is never blank, an empty record says how a reading is taken, and the chart
// never joins what was not measured with a solid line.
const H = 3600, D = 86400, NOW = 1_800_000_000;
const row = (t, v, kind, rest = true) => ({ t, v, kind, rest });
const st = (over = {}) => Object.assign({
  present: true, source: "adapter", floor_v: 12.2, critical_v: 11.9, parked: true,
  latest: { t: NOW - 60, v: 12.4, kind: "parked", age: 60 },
  headline: { tone: "ok", text: "12.4 V just now", sub: "Above the 12.2 V floor." },
  projection: { state: "steady", text: "x" }, tablet: { pct: 61, text: "Losing about 0.6 % an hour" },
  history: [], parks: [],
}, over);

export default [
  ["nothing recorded says how a reading is taken", () => {
    const v = watchView({ present: false, floor_v: 12.2 });
    eq([v.state, v.big, v.line], ["absent", "—", "No parked reading yet"]);
    ok(/parked.*starts/.test(v.sub), "the subtitle names when readings are taken");
  }],
  ["a recent adapter reading says Adapter, in the ok tone", () => {
    const v = watchView(st());
    eq([v.source, v.sourceTone, v.big, v.tablet], ["Adapter", "ok", "12.4", "Tablet 61%"]);
  }],
  ["an old reading says how old, with no tone", () => {
    const v = watchView(st({ latest: { t: NOW - 3 * D, v: 12.1, kind: "on", age: 3 * D } }));
    eq([v.state, v.source, v.sourceTone, v.big], ["stale", "Last reading 3 days ago", "", "12.1"]);
  }],
  ["the simulator always says Simulated", () => {
    const v = watchView(st({ source: "sim" }));
    eq([v.state, v.source, v.sourceTone], ["sim", "Simulated", "warn"]);
  }],
  ["the headline's tone reaches the card", () =>
    eq(watchView(st({ headline: { tone: "warn", text: "t", sub: "s" } })).tone, "warn")],
  ["no tablet battery gives no tablet text", () =>
    eq(watchView(st({ tablet: { pct: null, text: "" } })).tablet, "")],
  ["ages are said the way a person would", () =>
    eq([ago(30), ago(600), ago(7200), ago(3 * D)], ["just now", "10 min ago", "2 h ago", "3 days ago"])],
  ["a voltage is one decimal, and unknown is a dash", () => eq([fmtV(12.449), fmtV(null)], ["12.4", "—"])],

  ["no parks, no chart", () => eq(chartModel(st(), 600, 200), null)],
  ["a pair of readings is two dots and a dashed stretch, never a solid line", () => {
    const s = st({
      parks: [{ t0: NOW - 3 * D, t1: NOW - 60, hours: 72, open: false }],
      history: [row(NOW - 3 * D, 13.0, "off", false), row(NOW - 60, 12.4, "on")],
    });
    const m = chartModel(s, 600, 200);
    eq([m.line, m.dots.map((d) => d.kind)], ["", ["start", "end"]]);
    ok(m.dashed.length > 0, "the unmeasured stretch is dashed");
  }],
  ["awake readings ten minutes apart are one solid line with a fill", () => {
    const hist = [row(NOW - 4 * H, 13.0, "off", false)];
    for (let i = 1; i <= 20; i++) hist.push(row(NOW - 4 * H + i * 600, 12.6 - i * 0.002, "parked"));
    const m = chartModel(st({ parks: [{ t0: NOW - 4 * H, t1: null, hours: 4, open: true }], history: hist }), 600, 200);
    ok(m.line.startsWith("M") && m.area.length > m.line.length * 0.9, "solid line and area");
    eq(m.dots.filter((d) => d.kind === "end").length, 1);
  }],
  ["the floor is always drawn, inside the plot, even when every reading is above it", () => {
    const s = st({
      parks: [{ t0: NOW - D, t1: null, hours: 24, open: true }],
      history: [row(NOW - D, 13.0, "off", false), row(NOW - 12 * H, 12.7, "parked"), row(NOW - 11 * H, 12.7, "parked")],
    });
    const m = chartModel(s, 600, 200);
    ok(m.floorY > 0 && m.floorY < 200, "floor inside the plot");
    ok(m.lo <= 12.2 && m.hi >= 13.0, "scale holds the floor and the readings");
  }],
  ["a reading under the floor draws below the floor line", () => {
    const s = st({
      parks: [{ t0: NOW - 3 * D, t1: NOW - 60, hours: 72, open: false }],
      history: [row(NOW - 3 * D, 13.0, "off", false), row(NOW - 60, 12.0, "on")],
    });
    const m = chartModel(s, 600, 200);
    const end = m.dots.find((d) => d.kind === "end");
    ok(end.y > m.floorY, "lower on screen than the floor");
  }],
  ["each park gets a segment of its own, in order", () => {
    const s = st({
      parks: [{ t0: NOW - 9 * D, t1: NOW - 8 * D, hours: 24, open: false },
              { t0: NOW - 3 * D, t1: NOW - 2 * D, hours: 24, open: false }],
      history: [row(NOW - 9 * D, 13, "off", false), row(NOW - 8 * D, 12.5, "on"),
                row(NOW - 3 * D, 13, "off", false), row(NOW - 2 * D, 12.4, "on")],
    });
    const m = chartModel(s, 600, 200);
    eq(m.labels.length, 2);
    ok(m.labels[0].x < m.labels[1].x && m.bands[0].x + m.bands[0].w < m.bands[1].x, "separate, left to right");
  }],
  ["a running voltage at ignition-on is not drawn as a resting reading", () => {
    const s = st({
      parks: [{ t0: NOW - D, t1: NOW - 60, hours: 24, open: false }],
      history: [row(NOW - D, 13, "off", false), row(NOW - 60, 14.1, "on", false)],
    });
    const m = chartModel(s, 600, 200);
    eq(m.dots.filter((d) => d.kind === "end").length, 0);
  }],
];
