// Home's fridge card: the Dometic CFX3 in the back (lib/fridge.py).
//
// The temperature large, the hour behind it as a filled trail with the set
// point drawn through it, and the two things worth reaching for: the set
// point (parked only, like every other editor) and power (any time, one tap).
//
// WHERE THE NUMBER CAME FROM IS ALWAYS ON THE CARD. "Bluetooth" from the
// fridge itself, "Simulated" from `omacar fridge sim` -- never blank, so the
// simulator can never be mistaken for the food being cold. A reading that has
// gone stale keeps its number, dimmed, with how old it is.
//
// fridgeView() is pure (test/js/fridge.test.js); fridgeCard() draws it.

import { h, icon, store, U, withToken } from "./core.js";
import { sparkPath, makeSpark } from "./spark.js";

const NS = "http://www.w3.org/2000/svg";
const SNOW = ["M12 2.5v19", "M3.8 7.25l16.4 9.5", "M3.8 16.75l16.4-9.5",
              "M9.2 4.2 12 6.5l2.8-2.3", "M9.2 19.8 12 17.5l2.8 2.3"];
const POWER = ["M12 3.5v8", "M7.1 6.6a7 7 0 1 0 9.8 0"];
const STEP_C = 0.5;   // the CFX3 panel's own step

export { SNOW, POWER };
export const fmt = (c, imperial) => {
  if (c === null || c === undefined) return "—";
  const v = imperial ? c * 9 / 5 + 32 : c;
  return imperial ? String(Math.round(v)) : v.toFixed(1).replace(/\.0$/, "");
};

function ago(s) {
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

// What the card says. `st` is /api/fridge; `pending` a set point just asked
// for and not yet confirmed; `locked` the app's moving lock.
export function fridgeView(st, { imperial = true, locked = false, pending = null } = {}) {
  const unit = imperial ? "°F" : "°C";
  if (!st || !st.present) {
    return { state: "absent", title: "Fridge", source: "", sourceTone: "",
             temp: "—", unit, line: "No fridge connected yet: omacar fridge scan",
             set: null, canSet: false, canPower: false, on: null, alerts: [], trail: [] };
  }
  const z = (st.zones && st.zones[0]) || {};
  const live = !!st.connected;
  const sim = st.source === "sim";
  const source = sim ? "Simulated" : live ? "Bluetooth" : (st.age !== undefined ? `Last seen ${ago(st.age)}` : "Not connected");
  const sourceTone = sim ? "warn" : live ? "ok" : "";
  const set = pending !== null ? pending : z.set;
  const on = st.power === false || z.power === false ? false : st.power === null && z.power === null ? null : true;
  let line;
  if (on === false) line = "Off";
  else if (z.door) line = "Door open";
  else if (z.temp !== null && z.temp !== undefined && set !== null && set !== undefined) {
    const d = z.temp - set;
    line = d > 1.5 ? "Cooling down" : d < -1.5 ? "Colder than set" : "Holding";
  } else line = st.note || "Waiting for the fridge";
  const meta = [];
  if (st.battery_v) meta.push(`${st.battery_v.toFixed(1)} V`);
  if (st.supply) meta.push(st.supply);
  return {
    state: live ? "live" : "stale", title: (st.name || "Fridge").replace(/ \(simulated\)$/, ""),
    source, sourceTone,
    temp: fmt(z.temp, imperial), unit,
    set: set === null || set === undefined ? null : fmt(set, imperial),
    setC: set === null || set === undefined ? null : set,
    pending: pending !== null,
    line, meta: meta.join(" · "),
    canSet: live && on !== false && !locked && set !== null && set !== undefined,
    canPower: live, on,
    lockedNote: locked ? "Set point changes when you stop" : "",
    alerts: (st.alerts || []).slice(0, 2),
    trail: (st.trail || []).filter((p) => p[1] !== null),
    range: z.range || [-22, 10],
  };
}

// One request each way, shared by the card and the full screen (views/fridge.js)
// so the two can never disagree about what the fridge said.
export function post(name, value) {
  return fetch(withToken("/api/fridge"), {
    method: "POST", cache: "no-store", body: JSON.stringify({ name, value }),
  }).then((r) => r.json());
}

export async function readFridge() {
  const r = await fetch(withToken("/api/fridge"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

// The next set point one step colder or warmer, inside the fridge's range.
export function stepSet(v, dir) {
  const [lo, hi] = v.range;
  return Math.max(lo, Math.min(hi, Math.round((v.setC + dir * STEP_C) * 2) / 2));
}

export function fridgeCard(host = null) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const src = h("span.fr-src", lamp, h("span.fr-src-t"));
  const title = h("span.fr-name");
  const big = h("span.fr-temp.display-num"), unit = h("span.fr-unit");
  const line = h("span.fr-line"), setv = h("span.fr-set");
  const alert = h("div.fr-alert", { role: "status" });

  const spark = makeSpark(240, 56);
  const setLine = document.createElementNS(NS, "line");
  setLine.setAttribute("class", "fr-setline");
  setLine.setAttribute("x1", "0"); setLine.setAttribute("x2", "240");
  spark.svg.appendChild(setLine);

  let pending = null, pendingAt = 0, last = null;
  const down = h("button.fr-step", { type: "button", "aria-label": "Colder" }, "−");
  const up = h("button.fr-step", { type: "button", "aria-label": "Warmer" }, "+");
  const pwr = h("button.fr-pwr", { type: "button", "aria-label": "Fridge power" }, icon(POWER, 20));
  const meta = h("span.fr-meta");
  const setBox = h("div.fr-setbox", down, h("div.fr-setv", h("span.fr-setk", "Set"), setv), up);

  // Drawn INTO Home's slot when given one, so the grid placement Home put on
  // that node (span, data-card) is kept.
  const node = host || h("div.card.hc");
  node.classList.add("hc-fridge");
  node.append(
    h("div.fr-head", icon(SNOW, 18), title, src),
    h("div.fr-body",
      h("div.fr-now", h("div.fr-big", big, unit), line),
      h("div.fr-spark", spark.svg)),
    alert,
    h("div.fr-foot", setBox, meta, pwr));

  const bump = (dir) => {
    const v = last && fridgeView(last, { imperial: U.imperial, pending });
    if (!v || !v.canSet) return;
    const next = stepSet(v, dir);
    pending = next; pendingAt = Date.now();
    paint();
    post("z0_set", next).catch(() => { pending = null; paint(); });
  };
  down.onclick = () => bump(-1);
  up.onclick = () => bump(1);
  pwr.onclick = () => {
    const v = last && fridgeView(last, { imperial: U.imperial });
    if (!v || !v.canPower) return;
    pwr.disabled = true;
    post("power", v.on === false).finally(() => setTimeout(() => { pwr.disabled = false; }, 1500));
  };

  function paint() {
    const z = last && last.zones && last.zones[0];
    // The fridge's own report wins once it agrees, or after 6 s regardless.
    if (pending !== null && ((z && z.set === pending) || Date.now() - pendingAt > 6000)) pending = null;
    const v = fridgeView(last, { imperial: U.imperial, locked: !!store.lockedAsMoving, pending });
    node.dataset.state = v.state;
    node.dataset.on = v.on === false ? "off" : "on";
    title.textContent = v.title;
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.temp;
    unit.textContent = v.unit;
    line.textContent = v.line;
    setv.textContent = v.set === null ? "—" : `${v.set}°`;
    setBox.dataset.pending = v.pending ? "1" : "";
    meta.textContent = v.lockedNote || v.meta || "";
    down.disabled = up.disabled = !v.canSet;
    pwr.disabled = !v.canPower;
    pwr.setAttribute("aria-pressed", v.on === false ? "false" : "true");
    alert.hidden = !v.alerts.length;
    alert.textContent = v.alerts.join(" · ");
    // The trail on a scale that always holds the set point, so the line
    // through it means something: the set point +/- 2 degrees at least.
    const temps = v.trail.map((p) => p[1]);
    const s = v.setC;
    const lo = Math.min(...temps, s !== null ? s - 2 : Infinity);
    const hi = Math.max(...temps, s !== null ? s + 2 : -Infinity);
    spark.draw(v.trail.length > 1 && Number.isFinite(lo) ? sparkPath(v.trail, 240, 56, lo, hi) : "");
    if (s !== null && Number.isFinite(lo) && hi > lo) {
      const y = (56 - ((s - lo) / (hi - lo)) * 56).toFixed(1);
      setLine.setAttribute("y1", y); setLine.setAttribute("y2", y);
      setLine.style.display = "";
    } else setLine.style.display = "none";
  }

  let timer = null, gone = false;
  const tick = async () => {
    try { last = await readFridge(); } catch { /* keep the last reading; its age will show */ }
    if (!gone) { paint(); timer = setTimeout(tick, 2000); }
  };
  tick();
  return { node, paint, destroy() { gone = true; clearTimeout(timer); } };
}
