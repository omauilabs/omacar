// The fridge, full screen: Cabin → Fridge.
//
// The same reading as the Home card (fridgeView, readFridge, post in
// fridgecard.js), given room: the temperature at a size you can read from the
// back seat, the hour as a proper chart with its scale and the set point ruled
// through it, the controls at thumb size, and every fact the fridge reports.

import { h, icon, store, U } from "../core.js";
import { sparkPath, makeSpark } from "../spark.js";
import { fridgeView, readFridge, post, stepSet, fmt, SNOW, POWER } from "../fridgecard.js";

const NS = "http://www.w3.org/2000/svg";
const W = 600, H = 220;

const fact = (k) => {
  const v = h("span.fz-fv");
  return { node: h("div.fz-fact", h("span.fz-fk", k), v), v };
};

export default function fridgeScreen(root) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.fr-src", lamp, srcT);
  const name = h("h1.fz-name");
  const big = h("span.fz-temp.display-num"), unit = h("span.fz-unit");
  const line = h("div.fz-line"), alert = h("div.fz-alert", { role: "status" });
  const setv = h("span.fz-set.display-num"), setk = h("span.fz-setk", "Set point");
  const down = h("button.fz-step", { type: "button", "aria-label": "Colder" }, "−");
  const up = h("button.fz-step", { type: "button", "aria-label": "Warmer" }, "+");
  const pwr = h("button.fz-pwr", { type: "button" }, icon(POWER, 22), h("span.fz-pwr-t"));
  const note = h("div.fz-note");

  const spark = makeSpark(W, H);
  const setLine = document.createElementNS(NS, "line");
  setLine.setAttribute("class", "fr-setline");
  setLine.setAttribute("x1", "0"); setLine.setAttribute("x2", String(W));
  spark.svg.appendChild(setLine);
  const yHi = h("span"), yLo = h("span"), xL = h("span", "1 h ago"), xR = h("span", "now");

  const facts = {
    battery: fact("Fridge battery"), supply: fact("Running on"),
    protection: fact("Battery protection"), door: fact("Lid"),
    kind: fact("Model"), updated: fact("Reading"),
  };

  const node = h("div.fz",
    h("section.fz-hero",
      h("div.fz-head", icon(SNOW, 22), name, src),
      h("div.fz-big", big, unit),
      line, alert,
      h("div.fz-ctl",
        h("div.fz-setbox", down, h("div.fz-setv", setk, setv), up),
        pwr),
      note),
    h("section.fz-chart",
      h("div.fz-chart-k", "The last hour"),
      h("div.fz-plot", h("div.fz-y", yHi, yLo), h("div.fz-svg", spark.svg)),
      h("div.fz-x", xL, xR)),
    h("section.fz-facts", ...Object.values(facts).map((f) => f.node)));
  root.appendChild(node);

  let last = null, pending = null, pendingAt = 0;

  function paint() {
    const z = last && last.zones && last.zones[0];
    if (pending !== null && ((z && z.set === pending) || Date.now() - pendingAt > 6000)) pending = null;
    const v = fridgeView(last, { imperial: U.imperial, locked: !!store.lockedAsMoving, pending });
    node.dataset.state = v.state;
    node.dataset.on = v.on === false ? "off" : "on";
    name.textContent = v.title;
    srcT.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.temp;
    unit.textContent = v.unit;
    line.textContent = v.line;
    alert.hidden = !v.alerts.length;
    alert.textContent = v.alerts.join(" · ");
    setv.textContent = v.set === null ? "—" : `${v.set}°`;
    setv.dataset.pending = v.pending ? "1" : "";
    down.disabled = up.disabled = !v.canSet;
    pwr.disabled = !v.canPower;
    pwr.lastChild.textContent = v.on === false ? "Turn on" : "Turn off";
    pwr.dataset.on = v.on === false ? "off" : "on";
    note.textContent = v.lockedNote;

    const temps = v.trail.map((p) => p[1]);
    const s = v.setC;
    const lo = Math.floor(Math.min(...temps, s !== null ? s - 2 : Infinity));
    const hi = Math.ceil(Math.max(...temps, s !== null ? s + 2 : -Infinity));
    const ok = v.trail.length > 1 && Number.isFinite(lo) && hi > lo;
    spark.draw(ok ? sparkPath(v.trail, W, H, lo, hi) : "");
    yHi.textContent = ok ? `${fmt(hi, U.imperial)}°` : "";
    yLo.textContent = ok ? `${fmt(lo, U.imperial)}°` : "";
    if (ok && s !== null) {
      const y = (H - ((s - lo) / (hi - lo)) * H).toFixed(1);
      setLine.setAttribute("y1", y); setLine.setAttribute("y2", y);
      setLine.style.display = "";
    } else setLine.style.display = "none";

    const st = last || {};
    facts.battery.v.textContent = st.battery_v ? `${st.battery_v.toFixed(1)} V` : "—";
    facts.supply.v.textContent = st.supply || "—";
    facts.protection.v.textContent = st.protection || "—";
    facts.door.v.textContent = z && z.door !== null && z.door !== undefined ? (z.door ? "Open" : "Closed") : "—";
    facts.kind.v.textContent = st.kind ? `CFX3 · ${st.kind}` : "—";
    facts.updated.v.textContent = v.source || "—";
  }

  const bump = (dir) => {
    const v = fridgeView(last, { imperial: U.imperial, locked: !!store.lockedAsMoving, pending });
    if (!v.canSet) return;
    pending = stepSet(v, dir); pendingAt = Date.now();
    paint();
    post("z0_set", pending).catch(() => { pending = null; paint(); });
  };
  down.onclick = () => bump(-1);
  up.onclick = () => bump(1);
  pwr.onclick = () => {
    const v = fridgeView(last, { imperial: U.imperial });
    if (!v.canPower) return;
    pwr.disabled = true;
    post("power", v.on === false).finally(() => setTimeout(() => { pwr.disabled = false; }, 1500));
  };

  let timer = null, gone = false;
  const tick = async () => {
    try { last = await readFridge(); } catch { /* keep the last; its age shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 1500); }
  };
  tick();
  return () => { gone = true; clearTimeout(timer); };
}
