// The fuel log's shared parts: what the screens say (pure, test/js/fuel.test.js),
// the fill-up card with its thumb-size steppers, and Home's "Fuel log" card.
// The server half is lib/fuel.py; doc/fuel-log.md has the whole story.
//
// THE CAR SUPPLIES WHEN, THE OWNER SUPPLIES HOW MUCH. A level that jumps while
// the car is parked is noticed by lib/fuel.py; the amount and the price are
// asked for here, on a card that only appears once the car is parked, with a
// stepper for each and no keyboard to open.
//
// WHERE A NUMBER CAME FROM IS ALWAYS ON THE CARD: "Watching" when the
// watcher is running, "Last seen N ago" when it is not, "Simulated" for
// `omacar fuel sim`. An economy that has not been measured is a dash, never
// a zero.

import { h, icon, store, toast, withToken } from "./core.js";
import { makeSpark, sparkPath } from "./spark.js";

export const PUMP = ["M5 20.5V5.5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v15", "M3.5 20.5h13",
                     "M7.5 8.5h5", "M15.5 8.5h1.4a2 2 0 0 1 2 2v6.1a1.6 1.6 0 0 0 3.2 0V8.7l-2.9-2.9"];

const GAL = 3.785411784;          // litres in a US gallon
const KM_MI = 1.609344;
const MPG_K = 235.214583;

// ---- units: the log is metric, the screens are not --------------------------------------

export const isImp = (u) => !u || u.system === "imperial";
export const volOf = (litres, imp) => (imp ? litres / GAL : litres);
export const litresOf = (v, imp) => (imp ? v * GAL : v);
export const priceOf = (perLitre, imp) => (imp ? perLitre * GAL : perLitre);
export const econOf = (l100, imp) => (l100 && l100 > 0 ? (imp ? MPG_K / l100 : l100) : null);

export const volUnit = (imp) => (imp ? "gal" : "L");
export const econUnit = (imp) => (imp ? "mpg" : "L/100km");
export const distUnit = (imp) => (imp ? "mi" : "km");
export const distOf = (km, imp) => (imp ? km / KM_MI : km);

export const money = (x, dp = 2) => (x === null || x === undefined ? "—" : "$" + Number(x).toFixed(dp));
export const fmt1 = (x) => (x === null || x === undefined ? "—" : Number(x).toFixed(1));
const group = (n) => Math.round(n).toLocaleString("en-US");

// Dollars per mile or per km, from the log's dollars per km.
export const costPerDist = (cpk, imp) => (cpk === null || cpk === undefined ? null : imp ? cpk * KM_MI : cpk);

export function ago(s) {
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 2 * 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function dayLabel(at) {
  return new Date(at * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function monthName(key) {
  const [y, m] = String(key || "").split("-").map(Number);
  if (!y || !m) return "";
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long" });
}

// ---- the steppers -----------------------------------------------------------------------

// Gallons in tenths, dollars per gallon in cents. In metric: litres in tenths,
// dollars per litre in tenths of a cent (a litre costs a third of a gallon, so
// a cent a step would be a hundredth of the price).
export function specs(imp) {
  return {
    vol:   { step: 0.1, min: 0.5, max: imp ? 60 : 200, dp: 1 },
    price: { step: imp ? 0.01 : 0.001, min: imp ? 0.5 : 0.1, max: imp ? 20 : 5, dp: imp ? 2 : 3 },
  };
}

export function stepValue(v, dir, mult, spec) {
  const n = Math.round((v + dir * spec.step * mult) / spec.step);
  const x = Math.min(spec.max, Math.max(spec.min, n * spec.step));
  return Number(x.toFixed(spec.dp));
}

// Holding a key: one step at once, then a pause, then faster, then in bigger
// strides, so a whole tank (a hundred tenths) is a few seconds and a single
// tap is still exactly one tenth. `n` counts repeats since the first.
export function repeatPlan(n) {
  return { delay: n < 6 ? 150 : n < 20 ? 80 : 45, mult: n < 24 ? 1 : n < 56 ? 5 : 10 };
}

export const total = (vol, price) => Math.round(vol * price * 100) / 100;

// What the sheet opens with: the gauge's own estimate of what went in, to the
// nearest tenth, and the last price paid.
export function defaults(pending, st) {
  const imp = isImp(st && st.units);
  const sp = specs(imp);
  let est = pending && pending.est_litres;
  if (!est && pending && st && st.tank_l && pending.level_before !== null && pending.level_after !== null) {
    est = (pending.level_after - pending.level_before) / 100 * st.tank_l;
  }
  const vol = est ? stepValue(volOf(est, imp), 0, 1, sp.vol) : imp ? 8 : 30;
  const lp = st && st.last_price_per_l;
  const price = lp ? stepValue(priceOf(lp, imp), 0, 1, sp.price) : imp ? 3.5 : 0.95;
  return { vol, price, full: true };
}

export function gaugeLine(pending, st) {
  if (!pending || pending.level_before === null || pending.level_after === null
      || pending.level_before === undefined) return "";
  const imp = isImp(st && st.units);
  const bits = [`Tank ${Math.round(pending.level_before)}% to ${Math.round(pending.level_after)}%`];
  const est = pending.est_litres;
  if (est) bits.push(`about ${fmt1(volOf(est, imp))} ${volUnit(imp)} by the gauge`);
  return bits.join(" · ");
}

export function answerBody(pending, vol, price, full, imp) {
  return { op: "answer", id: pending.id, litres: Number(litresOf(vol, imp).toFixed(2)),
           cost: total(vol, price), full: !!full };
}

// ---- what the screens say ---------------------------------------------------------------

// The source lamp: where the log's numbers came from, never blank.
export function sourceOf(st) {
  if (!st || !st.present) return { text: "", tone: "" };
  if (st.source === "sim") return { text: "Simulated", tone: "warn" };
  const w = st.watcher || {};
  if (w.watching) return { text: "Watching the tank", tone: "ok" };
  if (w.age !== null && w.age !== undefined) return { text: `Last seen ${ago(w.age)}`, tone: "" };
  return { text: "Not watching", tone: "" };
}

export function fuelView(st) {
  const imp = isImp(st && st.units);
  const src = sourceOf(st);
  const base = { imp, unit: econUnit(imp), perUnit: imp ? "/mi" : "/km", source: src.text,
                 sourceTone: src.tone, trend: [], pending: !!(st && st.pending) };
  if (!st || !st.present) {
    return Object.assign(base, {
      state: "absent", econ: "—", cost: "—", month: "—", monthName: "",
      line: "No fill-ups yet. They are noticed when the tank jumps while you are parked.",
      fills: 0 });
  }
  const last = st.last;
  const m = st.month || {};
  const iv = st.intervals || [];
  const fills = (st.fills || []).filter((f) => f.status === "logged").length;
  const cpd = last ? costPerDist(last.cost_per_km, imp) : null;
  const avg = st.average_l100 ? econOf(st.average_l100, imp) : null;
  const car = last && last.car_l100 ? econOf(last.car_l100, imp) : null;
  return Object.assign(base, {
    state: last ? "ok" : "empty",
    econ: last ? fmt1(econOf(last.l100, imp)) : "—",
    cost: cpd === null ? "—" : money(cpd, 3),
    avg: avg === null ? "—" : fmt1(avg),
    carEcon: car === null ? null : fmt1(car),
    carError: last && last.car_error_pct !== null && last.car_error_pct !== undefined ? last.car_error_pct : null,
    km: last ? `${group(distOf(last.km, imp))} ${distUnit(imp)}` : "",
    when: last ? dayLabel(last.at) : "",
    month: m.fills ? money(m.spend) : "—",
    monthName: monthName(m.month),
    monthFills: m.fills || 0,
    monthUnpriced: m.unpriced || 0,
    tanks: iv.length,
    fills,
    trend: iv.map((i) => [i.at, econOf(i.l100, imp)]),
    line: last ? `${group(distOf(last.km, imp))} ${distUnit(imp)} on the last tank`
      : "Fill up to full twice, and the pump's own figure appears here.",
  });
}

// The timeline and the chart share one set of columns: the last `n` fill-ups,
// oldest left. A point on the chart sits over the column of the fill-up that
// ended its measurement, so the chart and the log read as one thing.
export function chartModel(st, n = 10) {
  const imp = isImp(st && st.units);
  const rows = ((st && st.fills) || []).slice().reverse().slice(-n);   // oldest first
  const by = new Map((st && st.intervals || []).map((i) => [i.to_id, i]));
  const cols = rows.map((f, k) => ({
    id: f.id, f, iv: by.get(f.id) || null, x: (k + 0.5) / Math.max(rows.length, 1),
  }));
  const real = cols.filter((c) => c.iv).map((c) => ({ x: c.x, v: econOf(c.iv.l100, imp), id: c.id }));
  const car = cols.filter((c) => c.iv && c.iv.car_l100)
    .map((c) => ({ x: c.x, v: econOf(c.iv.car_l100, imp), id: c.id }));
  const all = real.concat(car).map((p) => p.v);
  if (!all.length) return { imp, cols, real, car, lo: 0, hi: 1, ticks: [] };
  const step = imp ? 2 : 0.5;
  const lo = Math.floor((Math.min(...all) - step) / step) * step;
  const hi = Math.ceil((Math.max(...all) + step) / step) * step;
  const span = hi - lo;
  const ticks = [lo, lo + span / 2, hi];
  const y = (v) => (v - lo) / span;
  real.forEach((p) => { p.y = y(p.v); });
  car.forEach((p) => { p.y = y(p.v); });
  return { imp, cols, real, car, lo, hi, ticks };
}

export function columnView(c, imp) {
  const f = c.f;
  const kind = f.status === "pending" ? "waiting" : f.status === "skipped" ? "no amount"
    : f.full ? "full" : "top-up";
  const logged = f.status === "logged";
  return {
    id: f.id, kind, when: dayLabel(f.at),
    vol: logged ? `${fmt1(volOf(f.litres, imp))} ${volUnit(imp)}` : "—",
    cost: logged && f.cost !== null ? money(f.cost) : "—",
    price: logged && f.cost !== null && f.litres ? money(priceOf(f.cost / f.litres, imp)) : "",
    odo: f.odo_km ? `${group(distOf(f.odo_km, imp))} ${distUnit(imp)}` : "",
    before: f.level_before, after: f.level_after,
    econ: c.iv ? fmt1(econOf(c.iv.l100, imp)) : "",
  };
}

// Whether the calm card should be up: a question is waiting, the car is not
// moving, and no drowsiness alert is using the screen.
export function shouldAsk(st, { locked = false, alert = false, dismissed = null } = {}) {
  const p = st && st.pending;
  if (!p || locked || alert) return null;
  if (dismissed !== null && dismissed === p.id) return null;
  return p;
}

// ---- requests ---------------------------------------------------------------------------

export async function readFuel() {
  const r = await fetch(withToken("/api/fuel"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export async function postFuel(body) {
  const r = await fetch(withToken("/api/fuel"), {
    method: "POST", cache: "no-store", body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || String(r.status));
  return j;
}

// ---- a key you can hold -----------------------------------------------------------------

function holdKey(btn, fire) {
  let t = null, n = 0, down = false;
  const stop = () => { down = false; clearTimeout(t); };
  const tick = () => {
    if (!down) return;
    n++;
    const p = repeatPlan(n);
    fire(p.mult);
    t = setTimeout(tick, p.delay);
  };
  btn.addEventListener("pointerdown", (e) => {
    if (btn.disabled) return;
    e.preventDefault();
    try { btn.setPointerCapture(e.pointerId); } catch { /* a synthetic event has no pointer */ }
    down = true; n = 0;
    fire(1);
    clearTimeout(t);
    t = setTimeout(tick, 420);
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) btn.addEventListener(ev, stop);
  // From the keyboard (or a screen reader) a click is one step.
  btn.addEventListener("click", (e) => { if (e.detail === 0 && !btn.disabled) fire(1); });
  return stop;
}

function stepper(caption, spec, unitText, onchange) {
  let value = spec.min;
  const val = h("span.fl-sv.display-num");
  const cap = h("span.fl-sk", caption);
  const unit = h("span.fl-su", unitText);
  const minus = h("button.fl-key", { type: "button", "aria-label": `Less ${caption.toLowerCase()}` }, "−");
  const plus = h("button.fl-key", { type: "button", "aria-label": `More ${caption.toLowerCase()}` }, "+");
  const node = h("div.fl-step", { role: "group", "aria-label": caption },
    cap, h("div.fl-track", minus, h("div.fl-read", val, unit), plus));
  const paint = () => { val.textContent = value.toFixed(spec.dp); };
  const move = (dir) => (mult) => { value = stepValue(value, dir, mult, spec); paint(); onchange(value); };
  holdKey(minus, move(-1));
  holdKey(plus, move(1));
  node.addEventListener("keydown", (e) => {
    const k = e.key;
    if (k === "ArrowUp" || k === "+" || k === "=") { move(1)(1); e.preventDefault(); }
    if (k === "ArrowDown" || k === "-") { move(-1)(1); e.preventDefault(); }
  });
  node.tabIndex = 0;
  return {
    node,
    get: () => value,
    set(v, u) { value = v; if (u) unit.textContent = u; paint(); },
    lock(on) { minus.disabled = plus.disabled = on; node.dataset.locked = on ? "1" : ""; },
  };
}

// ---- "Fill-up: how much?" ---------------------------------------------------------------

// The card itself. `pending` is the question the watcher left in the log; with
// none it is the manual form ("Log a fill-up"). Parked only: while the car
// moves every key is dimmed and the card says when it will work.
export function fillSheet({ st, pending = null, onDone = () => {}, onCancel = () => {} }) {
  const imp = isImp(st && st.units);
  const sp = specs(imp);
  const d = defaults(pending, st);
  let full = true, busy = false;

  const totalEl = h("span.fl-total.display-num");
  const vol = stepper(imp ? "Gallons" : "Litres", sp.vol, volUnit(imp), () => paintTotal());
  const price = stepper(imp ? "Price per gallon" : "Price per litre", sp.price, imp ? "$ / gal" : "$ / L", () => paintTotal());
  vol.set(d.vol);
  price.set(d.price);

  const fullKey = h("button.fl-full", { type: "button", role: "switch", "aria-checked": "true" },
    h("span.fl-full-dot", { "aria-hidden": "true" }), h("span.fl-full-t", "Filled to full"));
  fullKey.onclick = () => {
    full = !full;
    fullKey.setAttribute("aria-checked", String(full));
    fullKey.classList.toggle("off", !full);
    fullKey.lastChild.textContent = full ? "Filled to full" : "Top-up, not to full";
    note.textContent = full ? "" : "A top-up is counted, but cannot end an economy measurement.";
  };
  const note = h("div.fl-note");
  const lockNote = h("div.fl-lock", "Fill-up details when you stop");
  const skipBtn = h("button.fl-quiet", { type: "button" }, pending ? "Skip" : "Cancel");
  const saveBtn = h("button.fl-save", { type: "button" }, "Save");
  const src = pending && pending.simulated
    ? h("span.fr-src", { "data-tone": "warn" }, h("span.fr-lamp"), h("span", "Simulated")) : null;

  const node = h("div.fl-sheet", { role: "dialog", "aria-label": pending ? "Fill-up: how much?" : "Log a fill-up" },
    h("div.fl-head", icon(PUMP, 18), h("span.fl-eyebrow", pending ? "Fill-up" : "Log a fill-up"), src),
    h("h2.fl-title", pending ? "How much?" : "How much did it take?"),
    h("div.fl-gauge", gaugeLine(pending, st)),
    h("div.fl-steps", vol.node, price.node),
    h("div.fl-mid", fullKey, h("div.fl-sum", h("span.fl-sumk", "Total"), totalEl)),
    note, lockNote,
    h("div.fl-foot", skipBtn, saveBtn));

  function paintTotal() { totalEl.textContent = money(total(vol.get(), price.get())); }
  paintTotal();

  function paint() {
    const locked = !!store.lockedAsMoving;
    vol.lock(locked); price.lock(locked);
    fullKey.disabled = locked;
    saveBtn.disabled = locked || busy;
    lockNote.hidden = !locked;
  }
  paint();

  skipBtn.onclick = async () => {
    if (busy) return;
    busy = true; paint();
    try {
      if (pending) await postFuel({ op: "skip", id: pending.id });
      onCancel();
    } catch (e) { toast(String(e.message || e), "bad"); } finally { busy = false; paint(); }
  };
  saveBtn.onclick = async () => {
    if (busy || store.lockedAsMoving) return;
    busy = true; paint();
    try {
      const v = vol.get(), p = price.get();
      const body = pending ? answerBody(pending, v, p, full, imp)
        : { op: "add", litres: Number(litresOf(v, imp).toFixed(2)), cost: total(v, p), full };
      const next = await postFuel(body);
      toast(`Logged ${v.toFixed(1)} ${volUnit(imp)} at ${money(p, sp.price.dp)}`);
      onDone(next);
    } catch (e) { toast(String(e.message || e), "bad"); } finally { busy = false; paint(); }
  };
  return { node, paint };
}

// ---- Home's card ------------------------------------------------------------------------

export function fuelLogCard(host = null) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const src = h("span.fr-src", lamp, h("span.fr-src-t"));
  const big = h("span.fl-big.display-num"), unit = h("span.fl-unit");
  const cost = h("span.fl-cost.display-num"), costU = h("span.fl-costu");
  const line = h("span.fl-line");
  const month = h("span.fl-month"), ask = h("span.fl-ask");
  const spark = makeSpark(220, 60);

  const node = host || h("div.card.hc");
  node.classList.add("hc-fuellog");
  node.append(
    h("div.fr-head", icon(PUMP, 18), h("span.fr-name", "Fuel log"), src),
    h("div.fl-body",
      h("div.fl-now", h("span.fl-cap", "Last tank"), h("div.fl-bigrow", big, unit), line),
      h("div.fl-now.fl-cpm", h("span.fl-cap", "Cost"), h("div.fl-bigrow", cost, costU)),
      h("div.fl-spark", spark.svg)),
    h("div.fl-foot2", month, ask));

  let last = null;
  function paint() {
    const v = fuelView(last);
    node.dataset.state = v.state;
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.econ;
    unit.textContent = v.state === "ok" ? v.unit : "";
    cost.textContent = v.cost;
    costU.textContent = v.cost === "—" ? "" : v.perUnit;
    line.textContent = v.line;
    month.textContent = v.month === "—" ? "" : `${v.monthName} ${v.month} · ${v.monthFills} fill-up${v.monthFills === 1 ? "" : "s"}`;
    ask.textContent = v.pending ? "Fill-up waiting: how much?" : "";
    const pts = v.trend.filter((p) => p[1] !== null);
    if (pts.length > 1) {
      // Fill-ups are not evenly spaced in time, but a tank is a tank: the
      // trend is drawn by tank, one step each.
      const idx = pts.map((p, i) => [i, p[1]]);
      const vs = pts.map((p) => p[1]);
      const pad = (Math.max(...vs) - Math.min(...vs)) * 0.25 || 1;
      spark.draw(sparkPath(idx, 220, 60, Math.min(...vs) - pad, Math.max(...vs) + pad));
    } else spark.draw("");
  }
  paint();

  let timer = null, gone = false;
  const tick = async () => {
    try { last = await readFuel(); } catch { /* keep the last; its source shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 15000); }
  };
  tick();
  const onSaved = () => { clearTimeout(timer); tick(); };
  window.addEventListener("omacar:fuel-changed", onSaved);
  return { node, paint,
           destroy() { gone = true; clearTimeout(timer); window.removeEventListener("omacar:fuel-changed", onSaved); } };
}
