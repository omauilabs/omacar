// A sparkline's SVG path from [t, v] points, fitted into w x h.
//
// With a fixed lo/hi (a reading's own scale) the line sits where the value does
// on its dial; without one it fills the box. A flat line is drawn through the
// middle, because a line on the floor reads as "zero".
export function sparkPath(points, w, h, lo = null, hi = null) {
  if (!points || points.length < 2) return "";
  const t0 = points[0][0], t1 = points[points.length - 1][0];
  const vs = points.map((p) => p[1]);
  const min = lo === null ? Math.min(...vs) : lo;
  const max = hi === null ? Math.max(...vs) : hi;
  const flat = max === min;
  const dt = t1 - t0 || 1;
  return points.map(([t, v], i) => {
    const x = ((t - t0) / dt) * w;
    const c = Math.min(max, Math.max(min, v));
    const y = flat ? h / 2 : h - ((c - min) / (max - min)) * h;
    return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
  }).join("");
}

// The same line closed down to the floor, for the colour under it. Empty when
// the line is: a fill with no line over it would read as a reading.
export function sparkArea(line, w, h) {
  return line ? `${line}L${w.toFixed(1)} ${h.toFixed(1)}L0.0 ${h.toFixed(1)}Z` : "";
}

// A sparkline's SVG: a fade of the line's own colour under the line, then the
// line. The colour is `currentColor`, so a tile recolours both by setting
// `color` on the svg (home.css), and the fade needs no per-look rule. Each
// svg owns its gradient, because ids are document-wide and a shared one would
// take the colour of whichever tile defined it first.
const NS = "http://www.w3.org/2000/svg";
let seq = 0;
export function makeSpark(w = 120, h = 28) {
  const el = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const id = `spark-fade-${++seq}`;
  const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none",
                          "aria-hidden": "true", class: "spark" });
  const grad = el("linearGradient", { id, x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.append(el("stop", { offset: "0", class: "spark-stop-top" }),
              el("stop", { offset: "1", class: "spark-stop-bottom" }));
  const defs = el("defs", {});
  defs.appendChild(grad);
  const area = el("path", { class: "spark-area", fill: `url(#${id})` });
  const line = el("path", { class: "spark-line" });
  svg.append(defs, area, line);
  return {
    svg,
    draw(d) {
      line.setAttribute("d", d);
      area.setAttribute("d", sparkArea(d, w, h));
    },
  };
}
