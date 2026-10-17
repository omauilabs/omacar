// Vehicle → Care → 12 V: the car's battery while parked, and the tablet's own.
//
// The same reading as Home's card (watchView, chartModel in bwatchcard.js),
// given room: the parks as a filled line with the floor ruled through it, what
// the readings can honestly project, the tablet's battery, and a line per park.
//
// WHEN THE TABLET SLEEPS NOTHING IS MEASURED, AND THE SCREEN SAYS SO. A long
// park is two points joined by a dashed line; a rate is only claimed across
// several parks, or from hours of awake readings, and never from one reading.

import { h, icon } from "../core.js";
import { watchView, chartModel, drawChart, readWatch, BAT, fmtV, ago } from "../bwatchcard.js";

const NS = "http://www.w3.org/2000/svg";

const fact = (k) => {
  const v = h("span.bw-fv");
  return { node: h("div.bw-fact", h("span.bw-fk", k), v), v };
};

const day = (t) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const stood = (hours) => (hours < 36 ? `${Math.round(hours)} h` : `${(hours / 24).toFixed(hours < 240 ? 1 : 0).replace(/\.0$/, "")} d`);

export default function bwatchScreen(root) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.fr-src", lamp, srcT);
  const big = h("span.bw-big.display-num"), unit = h("span.bw-unit", "V");
  const line = h("div.bw-line"), sub = h("div.bw-sub");
  const projK = h("span.bw-k", "What the readings say");
  const proj = h("div.bw-proj"), also = h("div.bw-also");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "bw-svg bw-svg-big");
  const yHi = h("span"), yLo = h("span");
  const xl = h("div.bw-xl");
  const floorTag = h("span.bw-floortag");
  const note = h("div.bw-note");

  const pctBig = h("span.bw-pct.display-num"), pctBar = h("span.bw-bar-i");
  const tline = h("div.bw-tline");
  const facts = { floor: fact("Floor"), crit: fact("Critical"), settle: fact("Settling ignored"),
                  step: fact("Adapter reads in"), readings: fact("This park") };
  const parksList = h("div.bw-parks");

  const svgbox = h("div.bw-svgbox", svg, floorTag);
  const node = h("div.bw",
    h("div.bw-l",
    h("section.bw-hero",
      h("div.bw-head", icon(BAT, 22), h("h1.bw-name", "12 V battery, parked"), src),
      h("div.bw-bigrow", big, unit),
      line, sub,
      h("div.bw-projbox", projK, proj, also)),
    h("section.bw-chart",
      h("div.bw-chart-k", "Resting voltage, park by park"),
      h("div.bw-plot", h("div.bw-y", yHi, yLo), svgbox),
      xl, note)),
    h("div.bw-r",
    h("section.bw-tab",
      h("div.bw-tabk", icon(["M5 4.5h14v15H5z", "M9.5 17h5"], 18), "Tablet battery"),
      h("div.bw-tabrow", pctBig, h("span.bw-pctu", "%")),
      h("span.bw-bar", pctBar),
      tline),
    h("section.bw-side",
      h("div.bw-facts", ...Object.values(facts).map((f) => f.node)),
      h("div.bw-pk", h("span", "Recent parks"), h("span", "start → end")),
      parksList)));
  root.appendChild(node);

  let last = null;

  function paint() {
    const v = watchView(last);
    node.dataset.state = v.state;
    node.dataset.tone = v.tone;
    srcT.textContent = v.source || "No reading";
    src.dataset.tone = v.sourceTone;
    big.textContent = v.big;
    line.textContent = v.line;
    const p = v.projection;
    sub.textContent = p && v.sub === p.text ? "" : v.sub;
    proj.textContent = p ? p.text : "No readings yet.";
    proj.dataset.state = p ? p.state : "";
    also.textContent = p && p.also ? p.also : "";
    projK.textContent = p && p.basis ? `What the readings say · ${p.n} ${p.unit}, ${p.basis}` : "What the readings say";

    const W = Math.max(300, Math.round(svgbox.clientWidth)), H = Math.max(100, Math.round(svgbox.clientHeight));
    const m = v.chart ? chartModel(last, W, H, { parksMax: 6, gap: 22, pad: 10 }) : null;
    drawChart(svg, m);
    yHi.textContent = m ? `${m.hi.toFixed(1)} V` : "";
    yLo.textContent = m ? `${m.lo.toFixed(1)} V` : "";
    xl.textContent = "";
    xl.style.marginLeft = `${yHi.parentNode.offsetWidth + 10}px`;
    if (m) {
      for (const l of m.labels) {
        const c = h("div.bw-xt", { style: { left: `${(l.x / m.W) * 100}%` } },
          h("span", day(l.t0)), h("span.bw-xd", l.open ? `${stood(l.hours)} so far` : stood(l.hours)));
        xl.appendChild(c);
      }
      floorTag.style.top = `${(m.floorY / m.H) * 100}%`;
      floorTag.textContent = `${v.floor.toFixed(1)} V floor`;
      floorTag.hidden = false;
    } else floorTag.hidden = true;
    note.textContent = m
      ? "Dashed: not measured. The tablet sleeps 30 s after the car is parked, so a long park is a start and an end. Hollow dot: the voltage as the car stopped, still settling."
      : "Nothing to draw yet. One reading is taken as the car is parked and another as it starts.";

    const t = (last && last.tablet) || {};
    pctBig.textContent = t.pct === null || t.pct === undefined ? "—" : String(t.pct);
    pctBar.style.width = `${t.pct || 0}%`;
    pctBar.parentNode.dataset.low = t.pct !== null && t.pct !== undefined && t.pct <= 20 ? "1" : "";
    tline.textContent = t.text || "";

    const st = last || {};
    facts.floor.v.textContent = st.floor_v ? `${st.floor_v.toFixed(1)} V` : "—";
    facts.crit.v.textContent = st.critical_v ? `${st.critical_v.toFixed(1)} V` : "—";
    facts.settle.v.textContent = st.settle_h !== undefined ? `first ${st.settle_h} h` : "—";
    facts.step.v.textContent = st.step_v ? `${st.step_v} V steps` : "—";
    facts.readings.v.textContent = st.park ? `${st.park.readings} resting` : st.parked === false ? "driving" : "—";

    parksList.textContent = "";
    for (const q of (st.parks || []).slice().reverse().slice(0, 5)) {
      parksList.appendChild(h("div.bw-prow",
        h("span.bw-pd", day(q.t0)),
        h("span.bw-pl", q.open ? `${stood(q.hours)} so far` : stood(q.hours)),
        h("span.bw-pv", `${q.off_v === null || q.off_v === undefined ? "—" : fmtV(q.off_v)} → ${q.on_v === null || q.on_v === undefined ? (q.open ? "…" : "—") : fmtV(q.on_v)} V`)));
    }
    if (!(st.parks || []).length) parksList.appendChild(h("div.bw-prow.bw-none", "No parks recorded yet"));
  }

  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (last) paint(); }) : null;
  if (ro) ro.observe(svgbox);
  let timer = null, gone = false;
  const tick = async () => {
    try { last = await readWatch(); } catch { /* keep the last; its age shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 15000); }
  };
  tick();
  return () => { gone = true; clearTimeout(timer); if (ro) ro.disconnect(); };
}
