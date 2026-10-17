// The Environment screen: the last day of the cabin and the outside, one
// filled chart per metric, and a plain sentence under each saying what the
// number means. Opened by tapping Home's Environment card; it has no tab.
//
// Everything is drawn from /api/env (now) and /api/env/history (a point a
// minute for 24 h). The charts are SVG in the card language of PR #39: a line
// with the colour of its air fading under it, soft rulings, captions over
// values. The cabin's CO2 carries the 800 / 1,200 / 2,000 ppm bands as faint
// washes; the temperature charts carry the dew point as a dashed line.

import { h, clear, U, withToken } from "../core.js";
import { lampOf, fmtTemp, fmtPressure, fmtInt, tempUnit, linePath, niceRange, bandOf,
         bandShares, explain, AQI_TONE, CO2_TONE, CO2_BANDS } from "../envview.js";

const NS = "http://www.w3.org/2000/svg";
const W = 480, HT = 140;
let seq = 0;

const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const toF = (c) => c * 9 / 5 + 32;

// The panels, in reading order: the cabin's four, the outside's four, and the
// cabin's day by CO2 band.
function panels(imperial) {
  const tval = (c) => (imperial ? toF(c) : c);
  const tfmt = (v) => (imperial ? String(Math.round(v)) : v.toFixed(1));
  return [
    { id: "cabin.co2", side: "Cabin", title: "CO₂", series: ["cabin.co2"], unit: "ppm",
      now: (s) => ({ v: fmtInt(s.cabin.values.co2), word: s.cabin.co2_band && s.cabin.co2_band.label,
                     tone: s.cabin.co2_band && CO2_TONE[s.cabin.co2_band.key] }),
      range: { floor: 400, minSpan: 500, include: [800, 1500] }, lab: (v) => fmtInt(v),
      rules: [800, 1200, 2000], washes: [[1200, 2000, "warn"], [2000, 1e9, "bad"]] },
    { id: "cabin.t_c", side: "Cabin", title: "Temperature", series: ["cabin.t_c"], dew: "cabin.dew_c",
      unit: tempUnit(imperial), conv: tval, lab: tfmt,
      now: (s) => ({ v: fmtTemp(s.cabin.values.t_c, imperial), word: s.cabin.dew_c !== null && s.cabin.dew_c !== undefined ? `dew point ${fmtTemp(s.cabin.dew_c, imperial)}°` : "" }),
      range: { minSpan: imperial ? 8 : 4 } },
    { id: "cabin.rh", side: "Cabin", title: "Humidity", series: ["cabin.rh"], unit: "%", lab: (v) => String(Math.round(v)),
      now: (s) => ({ v: s.cabin.values.rh === undefined ? "—" : String(Math.round(s.cabin.values.rh)),
                     word: s.cabin.ah ? `${s.cabin.ah} g/m³` : "" }),
      range: { minSpan: 20, floor: 0, ceil: 100 } },
    { id: "cabin.pm25", side: "Cabin", title: "Particles PM2.5", series: ["cabin.pm25"], unit: "µg/m³",
      lab: (v) => v.toFixed(0),
      now: (s) => { const v = s.cabin.values.pm25; return { v: v === undefined ? "—" : v.toFixed(1),
        word: v === undefined ? "" : v < 9.1 ? "Low" : v < 35.5 ? "Raised" : "High",
        tone: v === undefined ? "" : v < 9.1 ? "ok" : v < 35.5 ? "info" : "warn" }; },
      range: { minSpan: 10, floor: 0 } },
    { id: "outside.t_c", side: "Outside", title: "Temperature", series: ["outdoor.t_c"], dew: "outdoor.dew_c",
      unit: tempUnit(imperial), conv: tval, lab: tfmt,
      now: (s) => ({ v: fmtTemp(s.outside.values.t_c, imperial),
        word: s.outside.t_source === "car" ? "from the car" : s.outside.dew_c !== null && s.outside.dew_c !== undefined ? `dew point ${fmtTemp(s.outside.dew_c, imperial)}°` : "" }),
      range: { minSpan: imperial ? 10 : 5 } },
    { id: "outside.rh", side: "Outside", title: "Humidity", series: ["outdoor.rh"], unit: "%", lab: (v) => String(Math.round(v)),
      now: (s) => ({ v: s.outside.values.rh === undefined ? "—" : String(Math.round(s.outside.values.rh)),
                     word: s.outside.ah ? `${s.outside.ah} g/m³` : "" }),
      range: { minSpan: 20, floor: 0, ceil: 100 } },
    { id: "outside.hpa", side: "Outside", title: "Pressure", series: ["outdoor.hpa"], unit: imperial ? "inHg" : "hPa",
      conv: (v) => (imperial ? v * 0.02953 : v), lab: (v) => (imperial ? v.toFixed(2) : String(Math.round(v))),
      now: (s) => { const p = fmtPressure(s.outside.values.hpa, imperial), tr = s.outside.trend;
        return { v: p.v, word: tr && tr.available ? `${tr.word} ${tr.hpa3h > 0 ? "+" : ""}${imperial ? (tr.hpa3h * 0.02953).toFixed(2) : tr.hpa3h.toFixed(1)} in 3 h`
                                                  : tr ? "trend unavailable" + (tr.reason === "trend unavailable while driving" ? " while driving" : "") : "" }; },
      range: { minSpan: imperial ? 0.15 : 5 } },
    { id: "outside.aqi", side: "Outside", title: "Air quality (PM2.5)", series: ["outdoor.aqi"], unit: "AQI", lab: (v) => String(Math.round(v)),
      now: (s) => ({ v: s.outside.aqi ? String(s.outside.aqi.index) : "—", word: s.outside.aqi ? s.outside.aqi.label : "",
                     tone: s.outside.aqi && AQI_TONE[s.outside.aqi.key] }),
      range: { minSpan: 60, floor: 0, include: [0, 60] }, rules: [50, 100, 150], washes: [[100, 150, "warn"], [150, 1e9, "bad"]] },
    { id: "cabin.band", side: "Cabin", title: "Air through the day", strip: true },
  ];
}

function chartSvg(p, st, hist, imperial) {
  const id = ++seq;
  const t1 = hist.t1, t0 = hist.t0;
  const conv = p.conv || ((v) => v);
  const pts = ((hist.series[p.series[0]]) || []).map(([t, v]) => [t, conv(v)]);
  const wrap = h("div.ep-chart");
  if (pts.length < 2) {
    wrap.appendChild(h("div.ep-empty", pts.length ? "Just started. The chart fills as minutes pass." : "Nothing recorded in the last day."));
    return wrap;
  }
  const dew = p.dew ? ((hist.series[p.dew]) || []).map(([t, v]) => [t, conv(v)]) : [];
  const { lo, hi } = niceRange(pts.map((x) => x[1]).concat(dew.map((x) => x[1])), p.range || {});
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${HT}`, preserveAspectRatio: "none", "aria-hidden": "true" });
  const gid = `ep-fade-${id}`;
  const grad = svgEl("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.append(svgEl("stop", { offset: "0", class: "ep-stop-top" }), svgEl("stop", { offset: "1", class: "ep-stop-bot" }));
  const defs = svgEl("defs"); defs.appendChild(grad);
  svg.appendChild(defs);
  const y = (v) => HT - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * HT;
  for (const [a, b, tone] of p.washes || []) {
    const ya = y(Math.min(b, hi)), yb = y(Math.max(a, lo));
    if (a < hi && b > lo) svg.appendChild(svgEl("rect", { x: 0, y: ya.toFixed(1), width: W, height: Math.max(0, yb - ya).toFixed(1), class: `ep-bandfill-${tone}` }));
  }
  for (const r of p.rules || []) if (r > lo && r < hi) {
    svg.appendChild(svgEl("line", { x1: 0, x2: W, y1: y(r).toFixed(1), y2: y(r).toFixed(1), class: "ep-rule" }));
  }
  const { line, area } = linePath(pts, t0, t1, W, HT, lo, hi);
  svg.appendChild(svgEl("path", { d: area, fill: `url(#${gid})` }));
  if (dew.length > 1) svg.appendChild(svgEl("path", { d: linePath(dew, t0, t1, W, HT, lo, hi).line, class: "ep-dew" }));
  svg.appendChild(svgEl("path", { d: line, class: "ep-line" }));
  wrap.appendChild(svg);
  wrap.append(h("span.ep-lab.hi", p.lab(hi)), h("span.ep-lab.lo", p.lab(lo)));
  return wrap;
}

function stripSvg(hist) {
  const pts = (hist.series["cabin.co2"] || []);
  const wrap = h("div.ep-chart");
  if (pts.length < 2) { wrap.appendChild(h("div.ep-empty", "No cabin CO₂ recorded in the last day.")); return { wrap, shares: null }; }
  const span = hist.t1 - hist.t0;
  const svg = svgEl("svg", { viewBox: "0 0 1000 40", preserveAspectRatio: "none", class: "ep-strip", "aria-hidden": "true" });
  // Runs of one band merged, so a day is dozens of rects and not 1,440.
  let run = null;
  const flush = () => { if (run) svg.appendChild(svgEl("rect", { x: run.x.toFixed(1), y: 0, width: Math.max(1.2, run.w).toFixed(1), height: 40, class: `b-${run.b}` })); };
  for (const [t, v] of pts) {
    const x = ((t - 30 - hist.t0) / span) * 1000, w = (60 / span) * 1000, b = bandOf(v);
    if (run && run.b === b && x - (run.x + run.w) < 1) run.w = x + w - run.x;
    else { flush(); run = { x, w, b }; }
  }
  flush();
  wrap.appendChild(svg);
  return { wrap, shares: bandShares(pts) };
}

export default function environment(root) {
  let alive = true, st = null, hist = null, timer = null, histAt = 0;
  const imperial = () => U.imperial;
  const grid = h("div.env-grid");
  const srcCabin = h("span.en-src", h("span.en-lamp"), h("span.en-src-t")), srcOut = h("span.en-src", h("span.en-lamp"), h("span.en-src-t"));
  const head = h("div.env-head", h("span.env-cap", "Last 24 hours"),
    h("div.env-sources", h("span", h("span.env-cap", "Cabin  "), srcCabin), h("span", h("span.env-cap", "Outside  "), srcOut)));
  root.appendChild(h("div.env", head, grid));

  function lamp(el, side) {
    const l = lampOf(side);
    el.dataset.tone = l.tone;
    el.lastChild.textContent = l.text;
  }

  function draw() {
    if (!alive) return;
    if (!st || !st.present) {
      clear(grid);
      grid.appendChild(h("div.env-none", h("div", h("div", "No environment sensors yet."),
        h("div", "Plug the cabin and outdoor nodes into USB, then run omacar env scan."))));
      lamp(srcCabin, null); lamp(srcOut, null);
      return;
    }
    lamp(srcCabin, st.cabin); lamp(srcOut, st.outside);
    const hs = hist && hist.present ? hist : { t0: Date.now() / 1000 - 86400, t1: Date.now() / 1000, series: {} };
    clear(grid);
    for (const p of panels(imperial())) {
      const card = h("div.card.ep");
      if (p.strip) {
        const { wrap, shares } = stripSvg(hs);
        card.append(
          h("div.ep-top", h("div.ep-name", h("span.ep-side", p.side), h("span.ep-title", p.title))),
          wrap,
          shares ? h("div.ep-shares", ...CO2_BANDS.map((b) => h(`span.${b.key}`, `${b.label} ${Math.round(100 * shares[b.key] / shares.n)}%`))) : null,
          h("p.ep-mean", explain("cabin.band", st)));
        grid.appendChild(card);
        continue;
      }
      const n = p.now(st);
      if (n.tone) card.dataset.tone = n.tone;
      const unitEl = n.v === "—" ? "" : p.unit;
      card.append(
        h("div.ep-top", h("div.ep-name", h("span.ep-side", p.side), h("span.ep-title", p.title)),
          h("div.ep-now", h("span.ep-v", n.v), h("span.ep-u", unitEl), n.word ? h("span.ep-word", n.word) : null)),
        chartSvg(p, st, hs, imperial()),
        h("p.ep-mean", explain(p.id, st, { imperial: imperial() })),
      );
      grid.appendChild(card);
    }
  }

  async function load() {
    try {
      const r = await fetch(withToken("/api/env"), { cache: "no-store" });
      if (r.ok) st = await r.json();
      if (Date.now() - histAt > 55000) {
        const hr = await fetch(withToken("/api/env/history?hours=24"), { cache: "no-store" });
        if (hr.ok) { hist = await hr.json(); histAt = Date.now(); }
      }
    } catch { /* keep what is drawn */ }
    draw();
    if (alive) timer = setTimeout(load, 10000);
  }
  load();
  return () => { alive = false; clearTimeout(timer); };
}
