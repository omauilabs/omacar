// What the tablet-battery screen says (views/battery.js), as pure data.
//
// `st` is /api/tabletbatt (lib/tabletbatt.py). WHERE EACH NUMBER CAME FROM is
// always in the view: "Simulated" from `omacar tabletbatt sim`, "Battery sensor"
// from sysfs, "Last seen N ago" when the sampler stopped. A figure the battery
// does not report is null here and "Not reported" on screen -- never a zero.
//
// Linux CANNOT limit this tablet's charge (doc/tablet-battery.md); the limit
// row reports whether Surface UEFI's Battery Limit LOOKS active and how to set it.

export const DAY_MS = 86400000;

export function ago(s) {
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const tempText = (c, imperial) =>
  c === null || c === undefined ? "Not reported"
    : imperial ? `${Math.round(c * 9 / 5 + 32)}°F` : `${c.toFixed(1).replace(/\.0$/, "")}°C`;

// The app's own bands, not the maker's: 85 and up is fine, 80 is the usual
// "worn" line.
export const healthTone = (h) => h === null || h === undefined ? "" : h >= 85 ? "ok" : h >= 80 ? "warn" : "bad";

const LIMIT = {
  on:      { label: "Likely on",           tone: "ok" },
  off:     { label: "Not active",          tone: "info" },
  unclear: { label: "Unclear",             tone: "" },
  unknown: { label: "Can't tell yet",      tone: "" },
};

const fixed = (v, n) => v === null || v === undefined ? null : v.toFixed(n);

export function batteryView(st, { imperial = true } = {}) {
  if (!st || !st.present) {
    return { state: "absent", source: "", sourceTone: "", health: null, healthText: "—",
             line: st && st.demo ? "The demo has no tablet battery."
               : "No battery readings yet. Run: omacar tabletbatt run",
             warning: null, trend: [], peaks: [], limit: Object.assign({ state: "unknown", why: "" }, LIMIT.unknown),
             cycles: "Not reported", temp: "Not reported", charge: "—", projection: "" };
  }
  const r = st.reading || {};
  const sim = st.source === "sim";
  const live = !st.stale;
  const source = sim ? "Simulated" : live ? "Battery sensor" : `Last seen ${ago(st.age || 0)}`;
  const sourceTone = sim ? "warn" : live ? "ok" : "";
  const lim = st.limit || { state: "unknown", why: "" };
  const L = LIMIT[lim.state] || LIMIT.unknown;
  const unit = r.unit || "";
  const days = st.days || [];
  const trend = days.filter((d) => d.health !== null && d.health !== undefined)
    .map((d) => [Date.parse(d.day) / DAY_MS, d.health]);
  const peaks = days.filter((d) => d.peak_cap !== null && d.peak_cap !== undefined && (d.charge_samples || 0) >= 90)
    .map((d) => [Date.parse(d.day) / DAY_MS, d.peak_cap]);
  const capNow = r.capacity === null || r.capacity === undefined ? null : r.capacity;
  return {
    state: live ? "live" : "stale", source, sourceTone,
    health: r.health === undefined ? null : r.health,
    healthText: r.health === null || r.health === undefined ? "—" : fixed(r.health, 1),
    healthTone: healthTone(r.health),
    full: unit && r.full !== null ? `${fixed(r.full, 1)} ${unit}` : "Not reported",
    design: unit && r.design !== null ? `${fixed(r.design, 1)} ${unit}` : "Not reported",
    line: r.health === null || r.health === undefined
      ? "This battery does not report its full capacity."
      : `Holds ${fixed(r.health, 0)} % of what it held new`,
    cycles: r.cycles === null || r.cycles === undefined ? "Not reported" : String(r.cycles),
    temp: tempText(r.temp_c, imperial),
    tempHot: !!st.warning,
    charge: capNow === null ? "—" : `${capNow} %`,
    status: r.status || "—",
    warning: st.warning || null,
    limit: { state: lim.state, label: L.label, tone: L.tone, why: lim.why || "" },
    projection: (st.projection && st.projection.text) || "",
    rough: !!(st.projection && st.projection.rough),
    trend, peaks, limitPct: st.limit_pct || 50,
  };
}

// The chart's vertical range: a little air round the data, never flatter than
// four points so wobble does not look like collapse.
export function yRange(points, floorSpan = 4) {
  const vs = points.map((p) => p[1]);
  let lo = Math.floor(Math.min(...vs) - 1), hi = Math.ceil(Math.max(...vs) + 1);
  if (hi - lo < floorSpan) { const m = (hi + lo) / 2; lo = Math.floor(m - floorSpan / 2); hi = lo + floorSpan; }
  return [lo, Math.min(hi, 101)];
}

// The calm word for Home/toasts: only when there is something to say.
export function warnText(st) {
  return st && st.present && !st.stale && st.warning ? st.warning : null;
}
