// The last few minutes of every numeric reading, for sparklines.
//
// Filled from the live poller (main.js subscribes once) and read by tiles.
// Kept in module scope on purpose: a sparkline that starts empty every time you
// come back to Home is a sparkline that is never there when you look.

const WINDOW_MS = 5 * 60 * 1000;
const MAX_POINTS = 600;          // 5 minutes at 2 Hz, with room to spare
const series = new Map();        // pid -> [[ms, value], ...]
let lastT = 0;

export function record(sample, now = Date.now()) {
  if (!sample || !sample.connected || !sample.values) return;
  const t = sample.t ? sample.t * 1000 : now;
  // The poller asks four times a second and the daemon writes about five: the
  // same sample is often read twice, and twice is still one measurement.
  if (t <= lastT) return;
  lastT = t;
  for (const [k, v] of Object.entries(sample.values)) {
    if (typeof v !== "number" || Number.isNaN(v)) continue;
    let s = series.get(k);
    if (!s) { s = []; series.set(k, s); }
    s.push([t, v]);
    while (s.length && (t - s[0][0] > WINDOW_MS || s.length > MAX_POINTS)) s.shift();
  }
}

export function trail(pid) {
  return (series.get(pid) || []).slice();
}

export function reset() {
  series.clear();
  lastT = 0;
}
