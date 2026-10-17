import { eq, ok } from "./assert.js";
import { stepValue, specs, repeatPlan, defaults, answerBody, total, sourceOf, fuelView,
         chartModel, columnView, shouldAsk, econOf, volOf, litresOf, priceOf, gaugeLine,
         costPerDist, monthName, ago } from "../js/fuelcard.js";

// What the fuel log says (share/js/fuelcard.js). The log is metric; the screen
// is not. The steppers move in the steps the owner asked for, the economy is a
// reciprocal, and where a number came from is never blank.
const G = 3.785411784;
const IMP = { system: "imperial" }, MET = { system: "metric" };

const iv = (to, at, l100, extra = {}) => Object.assign({
  from_id: to - 1, to_id: to, from_at: at - 86400, at, km: 480, litres: 480 * l100 / 100,
  cost: 36, l100, mpg: 235.214583 / l100, cost_per_km: 0.075, car_l100: null, car_error_pct: null,
}, extra);
const fill = (id, at, over = {}) => Object.assign({
  id, at, litres: 36, cost: 36, full: true, status: "logged", odo_km: 1000 * id,
  level_before: 20, level_after: 90, manual: false, simulated: false }, over);
const st = (over = {}) => Object.assign({
  present: true, source: "real", units: IMP, tank_l: 40, watcher: { watching: true, age: 3 },
  pending: null, fills: [fill(3, 3000), fill(2, 2000), fill(1, 1000)],
  intervals: [iv(2, 2000, 6.7), iv(3, 3000, 7.0, { car_l100: 6.5, car_error_pct: 7.1 })],
  last: iv(3, 3000, 7.0, { car_l100: 6.5, car_error_pct: 7.1 }), average_l100: 6.85, cost_per_km: 0.075,
  month: { month: "2026-10", spend: 72, fills: 2, unpriced: 0 }, months: [], last_price_per_l: 1.0,
}, over);

export default [
  ["gallons move in tenths, price in cents (imperial)", () => {
    const sp = specs(true);
    eq([stepValue(9.4, 1, 1, sp.vol), stepValue(9.4, -1, 1, sp.vol), stepValue(3.79, 1, 1, sp.price),
        stepValue(3.79, -1, 1, sp.price)], [9.5, 9.3, 3.8, 3.78]);
  }],
  ["metric: litres in tenths, price per litre in tenths of a cent", () => {
    const sp = specs(false);
    eq([stepValue(35, 1, 1, sp.vol), stepValue(1.459, 1, 1, sp.price)], [35.1, 1.46]);
  }],
  ["steps never leave float dust (0.1 + 0.2 style)", () => {
    const sp = specs(true);
    let v = 0.5;
    for (let i = 0; i < 7; i++) v = stepValue(v, 1, 1, sp.vol);
    eq(v, 1.2);
    let p = 3.5;
    for (let i = 0; i < 29; i++) p = stepValue(p, 1, 1, sp.price);
    eq(p, 3.79);
  }],
  ["steppers stop at their ends", () => {
    const sp = specs(true);
    eq([stepValue(0.5, -1, 1, sp.vol), stepValue(60, 1, 10, sp.vol), stepValue(0.5, -1, 10, sp.price)], [0.5, 60, 0.5]);
  }],
  ["holding a key: slow, then fast, then in strides", () => {
    eq([repeatPlan(1), repeatPlan(10), repeatPlan(30), repeatPlan(80)],
       [{ delay: 150, mult: 1 }, { delay: 80, mult: 1 }, { delay: 45, mult: 5 }, { delay: 45, mult: 10 }]);
  }],
  ["a whole tank from the floor is reachable by holding (under 40 s)", () => {
    let v = 0.5, ms = 420, n = 0;
    while (v < 10.6) { n++; const p = repeatPlan(n); v = stepValue(v, 1, p.mult, specs(true).vol); ms += p.delay; }
    ok(ms < 40000, `${ms} ms`);
  }],
  ["the total is dollars and cents", () => eq(total(9.4, 3.79), 35.63)],
  ["economy is a reciprocal: lower L/100km is higher mpg", () => {
    ok(econOf(6.0, true) > econOf(8.0, true));
    eq(econOf(0, true), null);
    eq(econOf(null, true), null);
    eq(econOf(7.84, false), 7.84);
  }],
  ["units convert both ways", () => {
    ok(Math.abs(litresOf(volOf(40, true), true) - 40) < 1e-9);
    ok(Math.abs(priceOf(1, true) - G) < 1e-9);
  }],
  ["the card opens on the gauge's estimate and the last price", () => {
    const pend = { id: 5, est_litres: 30.0, level_before: 21, level_after: 96 };
    const d = defaults(pend, st());
    eq([d.vol, d.price, d.full], [7.9, 3.79, true]);
    const m = defaults(pend, st({ units: MET }));
    eq([m.vol, m.price], [30, 1]);
  }],
  ["... from the tank size when the watcher gave no litres", () => {
    const d = defaults({ id: 5, level_before: 20, level_after: 70 }, st());
    eq(d.vol, 5.3);
  }],
  ["... and a sane start when nothing is known", () => {
    const d = defaults(null, st({ last_price_per_l: null, tank_l: null }));
    eq([d.vol, d.price], [8, 3.5]);
  }],
  ["the answer goes out in litres and total dollars", () => {
    const b = answerBody({ id: 7 }, 9.4, 3.79, false, true);
    eq([b.op, b.id, b.cost, b.full], ["answer", 7, 35.63, false]);
    ok(Math.abs(b.litres - 9.4 * G) < 0.01);
  }],
  ["the gauge line says what the tank did", () => {
    eq(gaugeLine({ level_before: 21.2, level_after: 95.8, est_litres: 29.8 }, st()),
       "Tank 21% to 96% · about 7.9 gal by the gauge");
    eq(gaugeLine(null, st()), "");
  }],
  ["the source is never blank: watching, last seen, simulated, not watching", () => {
    eq(sourceOf(st()), { text: "Watching the tank", tone: "ok" });
    eq(sourceOf(st({ watcher: { watching: false, age: 300 } })), { text: "Last seen 5 min ago", tone: "" });
    eq(sourceOf(st({ source: "sim" })), { text: "Simulated", tone: "warn" });
    eq(sourceOf(st({ watcher: { watching: false, age: null } })).text, "Not watching");
  }],
  ["nothing logged says how fills get noticed, and draws dashes", () => {
    const v = fuelView({ present: false });
    eq([v.state, v.econ, v.cost, v.month], ["absent", "—", "—", "—"]);
    ok(v.line.includes("noticed"));
  }],
  ["imperial: mpg, dollars per mile, this month", () => {
    const v = fuelView(st());
    eq([v.state, v.unit, v.perUnit], ["ok", "mpg", "/mi"]);
    eq(v.econ, "33.6");
    eq(v.cost, "$0.121");
    eq([v.month, v.monthName, v.monthFills], ["$72.00", "October", 2]);
    eq(v.carEcon, "36.2");
    eq(v.carError, 7.1);
  }],
  ["metric: L/100km, dollars per km", () => {
    const v = fuelView(st({ units: MET }));
    eq([v.unit, v.perUnit, v.econ, v.cost], ["L/100km", "/km", "7.0", "$0.075"]);
  }],
  ["a log with fills but no measured tank has a dash, not a zero", () => {
    const v = fuelView(st({ last: null, intervals: [], average_l100: null }));
    eq([v.state, v.econ, v.cost], ["empty", "—", "—"]);
  }],
  ["the trend is per tank, oldest first", () => {
    const v = fuelView(st());
    eq(v.trend.length, 2);
    ok(v.trend[0][1] > v.trend[1][1]);
  }],
  ["cost per distance follows the unit", () => {
    ok(Math.abs(costPerDist(0.1, true) - 0.1609344) < 1e-9);
    eq(costPerDist(0.1, false), 0.1);
    eq(costPerDist(null, true), null);
  }],
  ["chart columns are the fill-ups, oldest left, points over the fill that ended the tank", () => {
    const m = chartModel(st(), 10);
    eq(m.cols.map((c) => c.id), [1, 2, 3]);
    eq(m.real.map((p) => p.id), [2, 3]);
    eq(m.real.map((p) => p.x), [(1 + 0.5) / 3, (2 + 0.5) / 3]);
    eq(m.car.map((p) => p.id), [3]);
  }],
  ["the chart scale holds every point with a margin, and y is 0..1", () => {
    const m = chartModel(st(), 10);
    const all = m.real.concat(m.car);
    ok(all.every((p) => p.y > 0 && p.y < 1));
    ok(m.lo < Math.min(...all.map((p) => p.v)) && m.hi > Math.max(...all.map((p) => p.v)));
  }],
  ["only the last n fill-ups are drawn", () => {
    const fills = [];
    for (let i = 14; i >= 1; i--) fills.push(fill(i, i * 1000));
    eq(chartModel(st({ fills, intervals: [] }), 10).cols.length, 10);
    eq(chartModel(st({ fills, intervals: [] }), 10).cols[9].id, 14);
  }],
  ["no measured tank: no scale to invent", () => {
    const m = chartModel(st({ intervals: [] }), 10);
    eq([m.real.length, m.ticks.length], [0, 0]);
  }],
  ["a column says full, top-up, no amount, or waiting", () => {
    const c = (over) => columnView({ f: fill(1, 1000, over), iv: null }, true).kind;
    eq([c({}), c({ full: false }), c({ status: "skipped", litres: null, cost: null }),
        c({ status: "pending", litres: null, cost: null })], ["full", "top-up", "no amount", "waiting"]);
  }],
  ["a column shows the amount, the price and the odometer in the unit on screen", () => {
    const v = columnView({ f: fill(1, 1000, { litres: 9.4 * G, cost: 35.63, odo_km: 160.9344 }), iv: null }, true);
    eq([v.vol, v.cost, v.price, v.odo], ["9.4 gal", "$35.63", "$3.79", "100 mi"]);
  }],
  ["a skipped fill-up has dashes for what nobody said", () => {
    const v = columnView({ f: fill(1, 1000, { status: "skipped", litres: null, cost: null }), iv: null }, true);
    eq([v.vol, v.cost, v.price], ["—", "—", ""]);
  }],
  ["the question is asked only when parked and quiet", () => {
    const s = st({ pending: { id: 9 } });
    eq(shouldAsk(s, {}), { id: 9 });
    eq(shouldAsk(s, { locked: true }), null);
    eq(shouldAsk(s, { alert: true }), null);
    eq(shouldAsk(s, { dismissed: 9 }), null);
    eq(shouldAsk(s, { dismissed: 8 }), { id: 9 });
    eq(shouldAsk(st(), {}), null);
    eq(shouldAsk(null, {}), null);
  }],
  ["month and age words", () => {
    eq(monthName("2026-10"), "October");
    eq(monthName(""), "");
    eq([ago(30), ago(300), ago(7200), ago(5 * 86400)], ["30 s ago", "5 min ago", "2 h ago", "5 d ago"]);
  }],
];

