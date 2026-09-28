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
