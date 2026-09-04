// Charts drawn as SVG, with d3 doing the arithmetic.
//
// charts.js next door draws on a canvas and stays there: a scope tracing four
// channels at five frames a second is a raster job, and nothing on it has to
// survive a printer. The charts here are the opposite kind — a dozen marks
// that are read slowly, photographed, and handed to somebody on paper — so
// they are vector, they carry their colour as var() rather than as a value,
// and they cost nothing to redraw because they are not redrawn.
//
// What d3 is here for is scales, axes and shape maths. There is no d3 idiom in
// this file beyond that: no data joins against changing data, no transitions,
// no zoom. The bundle at vendor/d3.min.js is a subset and deliberately has no
// d3-fetch and no d3-zoom — see its README before importing anything new.
//
// House rules for every chart below:
//
//   * colour is only ever set with .style("fill" | "stroke", "var(--x)"), so a
//     theme change, a night-red look and the print stylesheet all land without
//     a repaint. .attr("fill", "var(--ok)") silently paints nothing.
//   * geometry is set with .attr(). Never the other way round.
//   * text is sized in rem in the stylesheet below, so it follows the app's
//     type floor rather than pinning itself to whatever it was that day.
//   * an empty or one-row dataset draws an honest empty state and returns.
//     Half the screens in this app have no data until somebody drives the car,
//     and a chart that throws takes the whole view down with it (main.js
//     catches the mount and replaces the screen with an error card).
//
// Every function takes (container, data, options), owns the container's
// contents, and keeps no state anywhere else.

import * as d3 from "./vendor/d3.min.js";
import { paint, tonePaint, rootPx } from "./tokens.js";

// The unpainted part of a track, and the hairline under a bar chart.
//
// NOT --edge. app.css derives --track from the ink and the panel with a
// color-mix, precisely so that a hairline stays visible whichever way round
// the theme is — whereas a theme's own --edge is a border colour and several
// of them sit within a couple of points of --panel, which makes a track that
// is there and cannot be seen. The var() fallback keeps the charts usable in a
// document that has no app.css.
const TRACK = paint("--track", "var(--edge)");

// ------------------------------------------------------------------ the sheet
//
// One stylesheet, injected once, rather than rules in app.css: the charts own
// their own type and their own paper palette, and one lifted into another
// document should still come out right.
//
// The print block is there because app.css's print rules remap the surface
// tokens and deliberately leave the five semantic colours alone. That is right
// for a tinted card and wrong for a mark that IS its colour — a theme's amber
// is chosen to carry on a near-black panel and on white paper it is a smudge.
// So paper gets darker versions of the same five meanings, declared on the
// chart element itself rather than on :root. That placement is the load-bearing
// part: main.js applies the Omarchy palette as INLINE custom properties on the
// root element, an inline declaration outranks any ordinary stylesheet rule
// (print media query or not), and a rule matching the element beats a value
// inherited from an ancestor however that value was set. app.html carries the
// same correction for the rest of the page.
const SHEET_ID = "oc-chart-css";
const SHEET = `
.oc { display: block; width: 100%; height: auto; font-family: var(--mono);
      font-variant-numeric: tabular-nums; }
.oc text { fill: var(--dim); font-size: .78rem; }
.oc .lab  { fill: var(--ink); font-size: .84rem; }
.oc .val  { font-size: .86rem; font-weight: 600; }
.oc .tick { fill: var(--faint); font-size: .74rem; }
.oc .note { fill: var(--faint); font-size: .74rem; letter-spacing: .1em;
            text-transform: uppercase; }
.oc .axis .domain { display: none; }
.oc .axis text { fill: var(--faint); font-size: .74rem; }
@media print {
  .oc { --ink: #111; --dim: #444; --faint: #666; --ghost: #999;
        --edge: #CCC; --panel: #FFF;
        --ok: #1B7A4B; --warn: #8A5A05; --bad: #A32A1B; --info: #17558F;
        --ai: #4B3FA8; }
}
`;

function sheet() {
  if (document.getElementById(SHEET_ID)) return;
  const el = document.createElement("style");
  el.id = SHEET_ID;
  el.textContent = SHEET;
  document.head.appendChild(el);
}

// ------------------------------------------------------------------ plumbing

// Draw now, and again if the container's width changes by enough to matter.
//
// The viewBox means a chart is never wrong at another width, only scaled — so
// this is about keeping text at its true size rather than about correctness,
// and a few pixels of drift is not worth a redraw. The observer lets go of
// itself the first time it wakes up detached, because views are torn down by
// clear() and never told about it.
function responsive(container, draw) {
  const width = () => Math.max(280, Math.round(container.clientWidth || 640));
  draw(width());
  if (typeof ResizeObserver === "undefined") return;
  let last = width();
  const ro = new ResizeObserver(() => {
    if (!container.isConnected) { ro.disconnect(); return; }
    const w = width();
    if (Math.abs(w - last) < 12) return;
    last = w;
    draw(w);
  });
  ro.observe(container);
}

function paper(container, w, h, label) {
  sheet();
  container.textContent = "";
  return d3.select(container).append("svg")
    .attr("class", "oc")
    .attr("viewBox", `0 0 ${w} ${h}`)
    .attr("width", w)
    .attr("height", h)
    .attr("role", "img")
    .attr("aria-label", label || "");
}

// No data is a result, and it is worth as much space as a result. A dashed
// outline says "this is where the chart will be" without pretending there is
// something in it.
function empty(container, message) {
  const w = Math.max(280, Math.round(container.clientWidth || 640));
  const h = 88;
  const svg = paper(container, w, h, message);
  svg.append("rect")
    .attr("x", 1).attr("y", 1).attr("width", w - 2).attr("height", h - 2)
    .attr("rx", 10)
    .attr("fill", "none")
    .attr("stroke-dasharray", "4 5")
    .style("stroke", TRACK)
    .attr("stroke-width", 1.5);
  svg.append("text")
    .attr("class", "tick")
    .attr("x", w / 2).attr("y", h / 2 + 4)
    .attr("text-anchor", "middle")
    .text(message);
}

// The label column is a fixed width, so a long name has to be cut rather than
// allowed to run under the plot. JetBrains Mono advances at almost exactly
// 0.6em, and rootPx() asks the document how large a rem is now rather than
// assuming — the app's type floor has moved once already.
function fit(text, px, rem = 0.84) {
  const s = String(text == null ? "" : text);
  const n = Math.max(4, Math.floor(px / (0.6 * rem * rootPx())));
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

function labelWidth(w) {
  return Math.round(Math.max(110, Math.min(250, w * 0.32)));
}

// A dashed rule with a word over it: "here is the line the value is measured
// against". Shared by the two row charts so a limit and a service threshold
// read as the same kind of statement.
function rule(g, x, top, bottom, colour, caption) {
  g.append("line")
    .attr("x1", x).attr("x2", x).attr("y1", top).attr("y2", bottom)
    .attr("stroke-dasharray", "5 4")
    .attr("stroke-width", 1.75)
    .style("stroke", colour);
  if (caption) {
    g.append("text")
      .attr("class", "note")
      .attr("x", x).attr("y", top - 8)
      .attr("text-anchor", "middle")
      .style("fill", colour)
      .text(caption);
  }
}

function axis(g, scale, y, ticks, format) {
  g.append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${y})`)
    .call(d3.axisBottom(scale).tickValues(ticks).tickFormat(format)
      .tickSize(0).tickPadding(12));
}

// ---------------------------------------------------------------- lollipop
//
// One row per test: a track running from nothing to the limit, and a dot where
// the measurement actually landed. It answers "how much room is left" for a
// dozen tests in one glance, which a table of values against limits cannot do
// however carefully it is laid out — the reader has to divide every row.
//
//   rows  [{ label, value, tone, right, title }]  value as a fraction of the
//         limit, so 1 is at the limit and 1.2 is past it
//   opts  { limit, band: [lo, hi], caption, empty, aria }
export function lollipop(container, rows, opts = {}) {
  const data = (rows || []).filter((r) => Number.isFinite(r.value));
  if (!data.length) { empty(container, opts.empty || "Nothing measured yet"); return; }

  const limit = opts.limit === undefined ? 1 : opts.limit;
  const band = opts.band || [0.85, 1];
  const rowH = 34;
  const top = 30;
  const bottom = 36;

  responsive(container, (w) => {
    const left = labelWidth(w);
    const right = 86;
    const iw = Math.max(120, w - left - right);
    const h = top + data.length * rowH + bottom;
    const svg = paper(container, w, h, opts.aria || "Measured values against their limits");
    const g = svg.append("g").attr("transform", `translate(${left},${top})`);

    const hi = Math.max(limit * 1.18, (d3.max(data, (r) => r.value) || 0) * 1.06);
    const x = d3.scaleLinear().domain([0, hi]).range([0, iw]);
    const y = (i) => i * rowH + rowH / 2;
    const plot = data.length * rowH;

    // The band is the interesting part of the chart: everything inside it has
    // passed and is also about to stop passing.
    g.append("rect")
      .attr("x", x(band[0])).attr("y", -6)
      .attr("width", Math.max(0, x(band[1]) - x(band[0])))
      .attr("height", plot + 10)
      .style("fill", "var(--warn)")
      .attr("opacity", 0.1);

    rule(g, x(limit), -6, plot + 4, "var(--bad)", opts.caption || "limit");

    const row = g.selectAll("g.row").data(data).join("g")
      .attr("class", "row")
      .attr("transform", (d, i) => `translate(0,${y(i)})`);

    // Only where there is something to say: an empty <title> is a tooltip that
    // opens onto nothing.
    row.filter((d) => d.title).append("title").text((d) => d.title);

    row.append("line")
      .attr("x1", 0).attr("x2", iw).attr("y1", 0).attr("y2", 0)
      .attr("stroke-width", 5).attr("stroke-linecap", "round")
      .style("stroke", TRACK);

    row.append("line")
      .attr("x1", 0).attr("y1", 0).attr("y2", 0)
      .attr("x2", (d) => x(Math.max(0, d.value)))
      .attr("stroke-width", 5).attr("stroke-linecap", "round")
      .style("stroke", (d) => tonePaint(d.tone));

    // The dot carries a ring of the panel colour so it stays a dot where two
    // rows would otherwise merge into the band behind them.
    row.append("circle")
      .attr("cx", (d) => x(Math.max(0, d.value))).attr("cy", 0).attr("r", 6.5)
      .attr("stroke-width", 2)
      .style("fill", (d) => tonePaint(d.tone))
      .style("stroke", "var(--panel)");

    row.append("text")
      .attr("class", "lab")
      .attr("x", -14).attr("y", 5)
      .attr("text-anchor", "end")
      .text((d) => fit(d.label, left - 14));

    row.append("text")
      .attr("class", "val")
      .attr("x", iw + 14).attr("y", 5)
      .style("fill", (d) => tonePaint(d.tone))
      .text((d) => d.right || "");

    const ticks = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5].filter((t) => t <= hi);
    axis(g, x, plot + 8, ticks, d3.format(".0%"));
  });
}

// -------------------------------------------------------------------- bars
//
// The same grammar as the lollipop, for a percentage that stands on its own
// rather than against a limit: one bar per item, 0 to 100, worst first.
//
//   rows  [{ label, value, tone, right, title }]  value in percent
//   opts  { max, mark, caption, empty, aria }
export function bars(container, rows, opts = {}) {
  const data = (rows || []).filter((r) => Number.isFinite(r.value));
  if (!data.length) { empty(container, opts.empty || "Nothing to show yet"); return; }

  const max = opts.max || 100;
  const rowH = 30;
  const top = opts.mark === undefined ? 8 : 30;
  const bottom = 36;

  responsive(container, (w) => {
    const left = labelWidth(w);
    const right = 76;
    const iw = Math.max(120, w - left - right);
    const h = top + data.length * rowH + bottom;
    const svg = paper(container, w, h, opts.aria || "");
    const g = svg.append("g").attr("transform", `translate(${left},${top})`);

    const x = d3.scaleLinear().domain([0, max]).range([0, iw]);
    const plot = data.length * rowH;
    const barH = 10;

    if (opts.mark !== undefined) rule(g, x(opts.mark), -6, plot + 4, "var(--warn)", opts.caption);

    const row = g.selectAll("g.row").data(data).join("g")
      .attr("class", "row")
      .attr("transform", (d, i) => `translate(0,${i * rowH + rowH / 2})`);

    row.filter((d) => d.title).append("title").text((d) => d.title);

    row.append("rect")
      .attr("x", 0).attr("y", -barH / 2).attr("width", iw).attr("height", barH)
      .attr("rx", barH / 2)
      .style("fill", TRACK);

    row.append("rect")
      .attr("x", 0).attr("y", -barH / 2).attr("height", barH)
      .attr("width", (d) => Math.max(barH, x(Math.max(0, Math.min(max, d.value)))))
      .attr("rx", barH / 2)
      .style("fill", (d) => tonePaint(d.tone));

    row.append("text")
      .attr("class", "lab")
      .attr("x", -14).attr("y", 5)
      .attr("text-anchor", "end")
      .text((d) => fit(d.label, left - 14));

    row.append("text")
      .attr("class", "val")
      .attr("x", iw + 14).attr("y", 5)
      .style("fill", (d) => tonePaint(d.tone))
      .text((d) => d.right || "");

    axis(g, x, plot + 8, [0, 25, 50, 75, 100].filter((t) => t <= max),
      (t) => t + "%");
  });
}

// --------------------------------------------------------------- barsLine
//
// Distance as bars, economy as a line over them — the SVG twin of
// charts.js:barsAndLine, which stays where it is for the screens that repaint.
// Two measures on one chart because the question is always both at once: did I
// drive more, and did it cost more per mile.
//
//   rows  [{ label, bar, line }]  line may be null for a month with no fuel
//   opts  { height, barTone, lineTone, lineUnit, empty, aria }
export function barsLine(container, rows, opts = {}) {
  const data = (rows || []).filter((r) => Number.isFinite(r.bar));
  if (data.length < 2) { empty(container, opts.empty || "Not enough history yet"); return; }

  const height = opts.height || 210;
  const top = 16;
  const bottom = 30;

  responsive(container, (w) => {
    const left = 52;
    const right = 62;
    const iw = Math.max(160, w - left - right);
    const ih = height - top - bottom;
    const svg = paper(container, w, height, opts.aria || "");
    const g = svg.append("g").attr("transform", `translate(${left},${top})`);

    const x = d3.scaleBand()
      .domain(data.map((d, i) => i))
      .range([0, iw])
      .padding(0.3);
    const yBar = d3.scaleLinear()
      .domain([0, (d3.max(data, (d) => d.bar) || 1) * 1.06])
      .range([ih, 0]);

    // The line is scaled to its own range, not to zero: a year of economy is a
    // spread of five, and anchoring at the origin flattens it into a straight
    // line that says nothing. It is also held to the upper two thirds so it
    // rides over the bars instead of through them.
    const vals = data.map((d) => d.line).filter((v) => Number.isFinite(v));
    const lo = d3.min(vals), hiV = d3.max(vals);
    const spread = (hiV - lo) || 1;
    const yLine = d3.scaleLinear()
      .domain([lo - spread * 0.35, hiV + spread * 0.35])
      .range([ih * 0.72, ih * 0.06]);

    g.append("g")
      .attr("class", "axis")
      .call(d3.axisLeft(yBar).ticks(3).tickSize(0).tickPadding(10)
        .tickFormat(d3.format(",")));

    const bw = x.bandwidth();
    const r = Math.min(4, bw / 2);
    g.selectAll("path.bar").data(data).join("path")
      .attr("class", "bar")
      .attr("d", (d, i) => {
        const bx = x(i), by = yBar(d.bar);
        return `M${bx},${ih} L${bx},${by + r} Q${bx},${by} ${bx + r},${by}`
          + ` L${bx + bw - r},${by} Q${bx + bw},${by} ${bx + bw},${by + r}`
          + ` L${bx + bw},${ih} Z`;
      })
      .style("fill", tonePaint(opts.barTone || "info"))
      .attr("opacity", 0.85);

    g.append("line")
      .attr("x1", 0).attr("x2", iw).attr("y1", ih).attr("y2", ih)
      .attr("stroke-width", 1.25)
      .style("stroke", TRACK);

    if (vals.length > 1) {
      const line = d3.line()
        .defined((d) => Number.isFinite(d.line))
        .x((d, i) => x(i) + bw / 2)
        .y((d) => yLine(d.line))
        .curve(d3.curveMonotoneX);
      g.append("path")
        .attr("d", line(data))
        .attr("fill", "none")
        .attr("stroke-width", 2.5)
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("stroke", tonePaint(opts.lineTone || "warn"));
      const pts = data.map((d, i) => ({ i, v: d.line }))
        .filter((p) => Number.isFinite(p.v));
      g.selectAll("circle.pt").data(pts).join("circle")
        .attr("class", "pt")
        .attr("cx", (p) => x(p.i) + bw / 2)
        .attr("cy", (p) => yLine(p.v))
        .attr("r", 3.2)
        .attr("stroke-width", 1.5)
        .style("fill", tonePaint(opts.lineTone || "warn"))
        .style("stroke", "var(--panel)");

      // Only the last point is labelled. A number over every month is twelve
      // numbers nobody reads; the one at the end is where the eye lands.
      const last = pts[pts.length - 1];
      g.append("text")
        .attr("class", "val")
        .attr("x", x(last.i) + bw + 10)
        .attr("y", yLine(last.v) + 4)
        .style("fill", tonePaint(opts.lineTone || "warn"))
        .text(Math.round(last.v) + (opts.lineUnit ? " " + opts.lineUnit : ""));
    }

    g.selectAll("text.mo").data(data).join("text")
      .attr("class", "mo tick")
      .attr("x", (d, i) => x(i) + bw / 2)
      .attr("y", ih + 20)
      .attr("text-anchor", "middle")
      .text((d) => d.label);
  });
}
