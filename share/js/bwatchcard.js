// Home's 12 V card: the car's battery while it is parked (lib/batterywatch.py).
//
// The last resting voltage large, the parks behind it as a filled line with the
// floor ruled through it, and one line of what the readings can honestly say.
// WHERE A NUMBER CAME FROM IS ALWAYS ON THE CARD: "Adapter" when the last
// reading is recent, "Last reading 3 days ago" when it is not, "Simulated" from
// `omacar battery-watch sim` -- never blank, so the simulator can never be
// mistaken for the car. When the tablet slept through a park, nothing was
// measured in it and the card says only that: a start and an end.
//
// watchView() and chartModel() are pure (test/js/bwatch.test.js); the rest draws.

import { h, icon, withToken } from "./core.js";
import { sparkPath, makeSpark } from "./spark.js";

const NS = "http://www.w3.org/2000/svg";
export const BAT = ["M2.5 8.5h16v7h-16z", "M20.5 10.5v3", "M6.5 12h4"];

export function ago(s) {
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const fmtV = (v) => (v === null || v === undefined ? "—" : v.toFixed(1));

// What a card says. `st` is /api/battery-watch.
export function watchView(st) {
  if (!st || !st.present) {
    return { state: "absent", title: "12 V parked", source: "", sourceTone: "", big: "—", unit: "V",
             tone: "", line: "No parked reading yet",
             sub: "One is taken as the car is parked and again as it starts.",
             chart: null, floor: st && st.floor_v ? st.floor_v : 12.2, tablet: "", parked: false };
  }
  const sim = st.source === "sim";
  const l = st.latest;
  const age = l ? l.age : null;
  const fresh = age !== null && age < 1800;
  const source = sim ? "Simulated" : fresh ? "Adapter" : age !== null ? `Last reading ${ago(age)}` : "No reading";
  const hl = st.headline || {};
  const t = st.tablet || {};
  return {
    state: sim ? "sim" : fresh ? "live" : "stale",
    title: "12 V parked", source, sourceTone: sim ? "warn" : fresh ? "ok" : "",
    big: l ? fmtV(l.v) : "—", unit: "V",
    tone: hl.tone || "", line: hl.text || "", sub: hl.sub || "",
    projection: st.projection || null,
    chart: st.history && st.history.length ? st : null,
    floor: st.floor_v, critical: st.critical_v,
    tablet: t.pct === null || t.pct === undefined ? "" : `Tablet ${t.pct}%`,
    tabletLine: t.text || "",
    parked: !!st.parked,
  };
}

// The parks as one filled line. Every park gets a segment of its own (width
// grows with the square root of how long it stood, so a three-day park and a
// ten-hour one can both be read), and time runs to scale INSIDE a segment.
// Between a park's start and the first resting reading, and wherever the
// tablet slept, the line is dashed: that stretch was not measured.
//
// Returns null when there is nothing to draw.
export function chartModel(st, W, H, { parksMax = 6, gap = 12, pad = 6 } = {}) {
  if (!st || !st.history || !st.parks || !st.parks.length) return null;
  const parks = st.parks.slice(-parksMax);
  const rows = st.history;
  const floor = st.floor_v;
  const segs = parks.map((p) => {
    const end = p.t1 || Math.max(...rows.map((r) => r.t), p.t0);
    const mine = rows.filter((r) => r.t >= p.t0 && r.t <= end);
    return { p, t0: p.t0, t1: Math.max(end, p.t0 + 60), rows: mine,
             weight: Math.min(5, Math.max(1.6, Math.sqrt(Math.max(p.hours, 0.5)))) };
  }).filter((s) => s.rows.length);
  if (!segs.length) return null;
  const vs = segs.flatMap((s) => s.rows.map((r) => r.v)).concat([floor]);
  const lo = Math.floor((Math.min(...vs) - 0.15) * 10) / 10;
  const hi = Math.ceil((Math.max(...vs) + 0.1) * 10) / 10;
  const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad);
  const usable = W - gap * (segs.length - 1);
  const wsum = segs.reduce((a, s) => a + s.weight, 0);
  let x0 = 0;
  const solid = [], dashed = [], area = [], dots = [], labels = [], bands = [];
  for (const s of segs) {
    const w = (s.weight / wsum) * usable;
    const x = (t) => x0 + ((t - s.t0) / (s.t1 - s.t0)) * w;
    const pts = s.rows.map((r) => ({ x: x(r.t), y: y(r.v), r }));
    // Resting readings carry the line; the "off" reading (still settling after
    // the drive) is a hollow dot joined to it by a dashed stretch.
    let prev = null, run = [];
    const flush = () => {
      if (run.length > 1) {
        solid.push("M" + run.map((q) => `${q.x.toFixed(1)} ${q.y.toFixed(1)}`).join("L"));
        const b = run[run.length - 1], a = run[0];
        area.push(`M${a.x.toFixed(1)} ${H}L` + run.map((q) => `${q.x.toFixed(1)} ${q.y.toFixed(1)}`).join("L")
                  + `L${b.x.toFixed(1)} ${H}Z`);
      }
      run = [];
    };
    for (const q of pts) {
      if (q.r.kind === "off") {
        dots.push({ x: q.x, y: q.y, kind: "start", v: q.r.v });
        prev = q;
        continue;
      }
      if (!q.r.rest) { prev = null; continue; }
      const awake = prev && prev.r.kind !== "off" && (q.r.t - prev.r.t) < 2400;
      if (prev && !awake) {
        flush();
        dashed.push(`M${prev.x.toFixed(1)} ${prev.y.toFixed(1)}L${q.x.toFixed(1)} ${q.y.toFixed(1)}`);
      }
      run.push(q);
      prev = q;
    }
    flush();
    // A park that is only a pair (the tablet slept) shows its end as a dot.
    const rest = pts.filter((q) => q.r.kind !== "off" && q.r.rest);
    if (rest.length && rest.length <= 2) rest.forEach((q) => dots.push({ x: q.x, y: q.y, kind: "end", v: q.r.v }));
    else if (rest.length) dots.push({ x: rest[rest.length - 1].x, y: rest[rest.length - 1].y, kind: "end", v: rest[rest.length - 1].r.v });
    labels.push({ x: x0 + w / 2, t0: s.t0, hours: s.p.hours, open: s.p.open });
    bands.push({ x: x0, w });
    x0 += w + gap;
  }
  return { W, H, lo, hi, floorY: y(floor), critY: st.critical_v > lo ? y(st.critical_v) : null,
           line: solid.join(""), dashed: dashed.join(""), area: area.join(""), dots, labels, bands,
           yLabels: [hi, lo] };
}

export async function readWatch() {
  const r = await fetch(withToken("/api/battery-watch"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

function el(tag, attrs) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  return n;
}

// Paints a chart model into an svg the caller made (card and screen share it).
export function drawChart(svg, m, { dots = true } = {}) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!m) return;
  svg.setAttribute("viewBox", `0 0 ${m.W} ${m.H}`);
  const id = "bw-fade-" + Math.random().toString(36).slice(2, 8);
  const defs = el("defs");
  const g = el("linearGradient", { id, x1: "0", y1: "0", x2: "0", y2: "1" });
  g.append(el("stop", { offset: "0", class: "bw-stop-top" }), el("stop", { offset: "1", class: "bw-stop-bot" }));
  defs.appendChild(g);
  svg.appendChild(defs);
  for (const b of m.bands) svg.appendChild(el("rect", { x: b.x.toFixed(1), y: 0, width: b.w.toFixed(1), height: m.H, class: "bw-band" }));
  if (m.critY !== null) svg.appendChild(el("line", { x1: 0, x2: m.W, y1: m.critY.toFixed(1), y2: m.critY.toFixed(1), class: "bw-crit" }));
  svg.appendChild(el("line", { x1: 0, x2: m.W, y1: m.floorY.toFixed(1), y2: m.floorY.toFixed(1), class: "bw-floor" }));
  if (m.area) svg.appendChild(el("path", { d: m.area, fill: `url(#${id})`, class: "bw-area" }));
  if (m.dashed) svg.appendChild(el("path", { d: m.dashed, class: "bw-dash" }));
  if (m.line) svg.appendChild(el("path", { d: m.line, class: "bw-line" }));
  if (dots) for (const d of m.dots) svg.appendChild(el("circle", { cx: d.x.toFixed(1), cy: d.y.toFixed(1), r: d.kind === "start" ? 3.2 : 4, class: "bw-dot bw-dot-" + d.kind }));
}

export function watchCard(host = null) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const src = h("span.fr-src", lamp, h("span.fr-src-t"));
  const big = h("span.bwc-big.display-num"), unit = h("span.bwc-unit", "V");
  const line = h("span.bwc-line"), sub = h("span.bwc-sub");
  const plot = el("svg", { "aria-hidden": "true", class: "bw-svg" });
  const plotBox = h("div.bwc-plot", plot);
  const tab = h("span.bwc-tab");
  const node = host || h("div.card.hc");
  node.classList.add("hc-bwatch");
  node.append(
    h("div.fr-head", icon(BAT, 18), h("span.fr-name", "12 V parked"), src),
    h("div.bwc-body",
      h("div.bwc-now", h("div.bwc-bigrow", big, unit), line, sub),
      plotBox),
    h("div.bwc-bottom", tab));
  node.setAttribute("role", "button");
  node.tabIndex = 0;
  const open = () => { if (!node.closest(".editing")) location.hash = "#bwatch"; };
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });

  let last = null;
  function paint() {
    const v = watchView(last);
    node.dataset.state = v.state;
    node.dataset.tone = v.tone;
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.big;
    line.textContent = v.line;
    // The long projection sentence belongs to the screen; the card keeps a short one.
    sub.textContent = v.sub && v.sub.length <= 56 ? v.sub : "";
    tab.textContent = v.tablet;
    const W = Math.max(120, Math.round(plotBox.clientWidth)), H = Math.max(40, Math.round(plotBox.clientHeight));
    drawChart(plot, v.chart ? chartModel(last, W, H, { parksMax: 6, gap: 8, pad: 4 }) : null, { dots: false });
    node.dataset.chart = v.chart ? "1" : "";
  }
  let timer = null, gone = false;
  const tick = async () => {
    try { last = await readWatch(); } catch { /* keep the last; its age shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 20000); }
  };
  tick();
  return { node, paint, destroy() { gone = true; clearTimeout(timer); } };
}
