// Home's Environment card: the cabin and the outside, side by side.
//
// Each side is its temperature large, the humidity under it, and ONE headline
// for the air -- CO2 inside (the number that predicts a sleepy driver), the
// EPA PM2.5 category outside -- on a soft raised track with a status lamp,
// and a filled trail of that air metric behind it for the last ten minutes.
// The card is the way into the Environment screen (the day's charts).
//
// envView() (share/js/envview.js) is pure and tested; this file only draws it.

import { h, U, withToken } from "./core.js";
import { sparkPath, makeSpark } from "./spark.js";
import { envView } from "./envview.js";

const W = 200, HT = 34;

function side(name, cap) {
  const lamp = h("span.en-lamp", { "aria-hidden": "true" });
  const src = h("span.en-src", lamp, h("span.en-src-t"));
  const temp = h("span.en-temp.display-num"), unit = h("span.en-unit");
  const rh = h("span.en-rhv"), rhU = h("span.en-u", " %"), note = h("span.en-note");
  const airK = h("span.en-airk"), airV = h("span.en-airv"), airU = h("span.en-airu"), airL = h("span.en-airl");
  const spark = makeSpark(W, HT);
  const line = h("div.en-line");
  const node = h("div.en-side", { "data-side": name },
    h("div.en-cap", h("span.en-name", cap), src),
    h("div.en-main",
      h("div.en-big", temp, unit),
      h("div.en-rh", h("span.en-k", "Humidity"), h("span", rh, rhU))),
    note,
    h("div.en-air",
      h("span.en-halo", { "aria-hidden": "true" }),
      h("div.en-airt", airK, h("span.en-airvv", airV, airU)),
      airL),
    h("div.en-spark", spark.svg),
    line);
  return { node, src, temp, unit, rh, rhU, note, airK, airV, airU, airL, spark, line };
}

function paintSide(s, v) {
  s.node.dataset.state = v.state;
  s.node.dataset.tone = v.air.tone || "";
  s.src.dataset.tone = v.lamp.tone;
  s.src.lastChild.textContent = v.lamp.text;
  s.temp.textContent = v.temp;
  s.unit.textContent = v.temp === "—" ? "" : v.unit;
  s.rh.textContent = v.rh;
  s.rhU.hidden = v.rh === "—";
  s.note.textContent = v.tempNote;
  s.airK.textContent = v.air.k;
  s.airV.textContent = v.air.v;
  s.airU.textContent = v.air.u ? " " + v.air.u : "";
  s.airL.textContent = v.air.label;
  s.line.textContent = v.line;
  const t = v.trail;
  let d = "";
  if (t.length > 1) {
    const vs = t.map((p) => p[1]);
    // Ten minutes, scaled to ITSELF (at least 150 ppm / 3 ug/m3 tall) so the
    // shape of the last few minutes reads; the number above says how high.
    const span = v.air.k === "CO₂" ? 150 : 3;
    const mn = Math.min(...vs), mx = Math.max(...vs);
    const mid = (mn + mx) / 2, half = Math.max((mx - mn) / 2, span / 2) * 1.1;
    d = sparkPath(t, W, HT, mid - half, mid + half);
  }
  s.spark.draw(d);
}

export function envCard(host = null) {
  const cab = side("cabin", "Cabin"), out = side("outside", "Outside");
  const more = h("div.en-more");
  const node = host || h("div.card.hc");
  node.classList.add("hc-env");
  node.append(
    h("div.en-sides", cab.node, h("div.en-rule", { "aria-hidden": "true" }), out.node),
    more);

  let last = null;
  function paint() {
    const v = envView(last, { imperial: U.imperial });
    node.dataset.present = v.present ? "1" : "0";
    paintSide(cab, v.cabin);
    paintSide(out, v.outside);
    more.textContent = v.more;
    more.hidden = !v.more;
  }

  let timer = null, gone = false;
  const tick = async () => {
    try {
      const r = await fetch(withToken("/api/env"), { cache: "no-store" });
      if (r.ok) last = await r.json();
    } catch { /* keep the last reading; its age will show */ }
    if (!gone) { paint(); timer = setTimeout(tick, 2000); }
  };
  paint();
  tick();
  return { node, paint, destroy() { gone = true; clearTimeout(timer); } };
}
