import { eq, ok } from "./assert.js";
import { envView, lampOf, shouldPrompt, promptText, linePath, niceRange, bandShares, bandOf,
         explain, fmtTemp, fmtPressure, PROMPT_GAP_MS } from "../js/envview.js";

// What the Environment card says (share/js/envview.js). Where a number came
// from is never blank; nothing plugged in is a line of words, never a zero.
const cabin = (over = {}) => Object.assign({
  state: "live", source: "sensor", age: 1, values: { t_c: 22.4, rh: 41.2, co2: 812 },
  co2_band: { key: "ok", label: "Fine" }, dew_c: 8.9, trail: { co2: [[0, 700], [5, 812]] } }, over);
const outside = (over = {}) => Object.assign({
  state: "live", source: "sensor", age: 1, values: { t_c: 17.9, rh: 63, hpa: 1012.6, pm25: 6.1 },
  aqi: { index: 25, key: "good", label: "Good" }, car_t_c: 19.5, dew_c: 10.8,
  trail: { pm25: [[0, 5], [5, 6.1]] } }, over);
const st = (over = {}) => Object.assign({ present: true, source: "sensor", moving: false,
  cabin: cabin(), outside: outside(), prompt: { due: false, ppm: 812 } }, over);

export default [
  ["°F rounds, °C keeps a decimal", () => eq([fmtTemp(22.4, true), fmtTemp(22.4, false), fmtTemp(null, true)], ["72", "22.4", "—"])],
  ["pressure is inHg for imperial, hPa for metric", () =>
    eq([fmtPressure(1013.25, true), fmtPressure(1013.25, false)], [{ v: "29.92", u: "inHg" }, { v: "1013", u: "hPa" }])],
  ["no sensors yet says how to find them", () => {
    const v = envView({ present: false });
    eq([v.present, v.cabin.line, v.outside.line, v.cabin.temp, v.hint],
       [false, "Plug in the cabin sensor", "Plug in the outdoor sensor", "—", "No sensors yet: omacar env scan"]);
  }],
  ["a live sensor says Sensor, in the ok tone, and CO2 carries its band", () => {
    const v = envView(st());
    eq([v.cabin.lamp, v.cabin.air.v, v.cabin.air.label, v.cabin.air.tone, v.cabin.temp, v.cabin.rh],
       [{ text: "Sensor", tone: "ok" }, "812", "Fine", "info", "72", "41"]);
  }],
  ["outside headline is the PM2.5 AQI category", () => {
    const v = envView(st());
    eq([v.outside.air.k, v.outside.air.v, v.outside.air.label, v.outside.air.tone], ["AQI", "25", "Good", "ok"]);
  }],
  ["the simulator always says Simulated", () =>
    eq(lampOf(cabin({ source: "sim" })), { text: "Simulated", tone: "warn" })],
  ["the car's thermometer is labelled From the car, and has no air sensor to quote", () => {
    const v = envView(st({ outside: outside({ source: "car", t_source: "car", values: { t_c: 12 }, aqi: null }) }));
    eq([v.outside.lamp.text, v.outside.temp, v.outside.air.v, v.outside.air.label],
       ["From the car", "54", "—", "No air sensor outside"]);
  }],
  ["a node without a thermometer borrows the car's, and says so", () => {
    const v = envView(st({ outside: outside({ t_source: "car", values: { t_c: 12, pm25: 6.1 } }) }));
    eq([v.outside.lamp.text, v.outside.tempNote], ["Sensor", "Temperature from the car"]);
  }],
  ["a stale side keeps its number and says how old", () => {
    const v = envView(st({ cabin: cabin({ state: "stale", age: 420 }) }));
    eq([v.cabin.lamp, v.cabin.temp], [{ text: "Last seen 7 min ago", tone: "" }, "72"]);
  }],
  ["a cabin node still warming up has no CO2 yet, not a zero", () => {
    const v = envView(st({ cabin: cabin({ values: { t_c: 22, rh: 40 }, co2_band: null }) }));
    eq([v.cabin.air.v, v.cabin.air.label], ["—", "No CO₂ reading yet"]);
  }],
  ["the footer line carries dew point and pressure trend", () => {
    const v = envView(st({ outside: outside({ trend: { available: true, word: "falling" } }) }), { imperial: false });
    eq(v.more, "Dew point 10.8° · 1013 hPa falling");
  }],

  // the calm prompt
  ["the prompt text is the owner's sentence, with the ppm grouped", () =>
    eq(promptText(1640), "Cabin air is stale (CO₂ 1,640 ppm). Switch to fresh air or crack a window.")],
  ["not due, no prompt", () => ok(!shouldPrompt(st(), { level: 0 }, 1e9, 0), "due is false")],
  ["due and moving, it speaks", () => ok(shouldPrompt(st({ moving: true, prompt: { due: true, ppm: 1640 } }), { level: 0 }, 1e9, 0), "should")],
  ["never during a drowsy alert, a banner or a test", () => {
    const s = st({ moving: true, prompt: { due: true, ppm: 1640 } });
    for (const d of [{ level: 1 }, { level: 3 }, { level: 0, banner: true }, { level: 0, testing: true }])
      ok(!shouldPrompt(s, d, 1e9, 0), JSON.stringify(d));
  }],
  ["once per 20 minutes", () => {
    const s = st({ moving: true, prompt: { due: true, ppm: 1640 } });
    ok(!shouldPrompt(s, { level: 0 }, 1000 + PROMPT_GAP_MS - 1, 1000), "19m59s");
    ok(shouldPrompt(s, { level: 0 }, 1000 + PROMPT_GAP_MS, 1000), "20m");
  }],
  ["parked (not moving) never prompts, even if the server says due", () =>
    ok(!shouldPrompt(st({ moving: false, prompt: { due: true, ppm: 1700 } }), { level: 0 }, 1e9, 0), "parked")],

  // charts
  ["a line fills a fixed window and breaks across a gap instead of sloping over it", () => {
    const { line, area } = linePath([[0, 1], [60, 2], [120, 3], [2000, 3], [2060, 4]], 0, 2100, 100, 50, 0, 5);
    eq((line.match(/M/g) || []).length, 2);
    ok(area.endsWith("Z"), "area closed");
    ok(line.startsWith("M0.0 40.0"), "first point at the window's left edge, at value 1 of 0..5");
  }],
  ["a flat line is drawn through the middle, not on the floor", () =>
    ok(linePath([[0, 5], [60, 5]], 0, 60, 10, 20, 5, 5).line.includes("10.0"), "mid")],
  ["niceRange never exaggerates a flat line", () => {
    const r = niceRange([21.9, 22.0, 22.1], { minSpan: 4 });
    ok(r.hi - r.lo >= 4, "span");
  }],
  ["CO2 bands at the owner's edges", () =>
    eq([799, 800, 1199, 1200, 2000, 2001].map(bandOf), ["good", "ok", "ok", "stuffy", "stuffy", "high"])],
  ["band shares sum to the minutes", () => {
    const s = bandShares([[0, 500], [1, 900], [2, 1300], [3, 2500], [4, 600]]);
    eq([s.good, s.ok, s.stuffy, s.high, s.n], [2, 1, 1, 1, 5]);
  }],

  // what it means
  ["pressure trend unavailable while driving says why", () => {
    const m = explain("outside.hpa", st({ outside: outside({ trend: { available: false, reason: "trend unavailable while driving" } }) }));
    ok(m.startsWith("Trend unavailable while driving"), m);
  }],
  ["every metric has a plain sentence", () => {
    for (const k of ["cabin.co2", "cabin.t_c", "cabin.rh", "cabin.pm25", "outside.t_c", "outside.rh", "outside.hpa", "outside.aqi", "cabin.band"])
      ok(explain(k, st({ outside: outside({ trend: { available: true, word: "steady" } }) })).length > 20, k);
  }],
  ["the car's reading is quoted beside the sensor's, labelled as the car's", () =>
    ok(explain("outside.t_c", st(), { imperial: false }).includes("The car says 19.5"), "car")],
];
