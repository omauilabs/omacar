// Incident clips (lib/clips.py): the arithmetic of the Clips view and the
// "Clip saved" toast, apart from the DOM so it can be tested.

export const ROLES = ["front", "rear", "cabin"];
export const ROLE_LABEL = { front: "Front", rear: "Rear", cabin: "Cabin" };
export const BEFORE = 30, AFTER = 30;

// Tone is the app's lamp vocabulary (--bad, --warn, --info).
export const TRIGGERS = {
  brake: { word: "Hard braking", tone: "bad" },
  jolt: { word: "Jolt", tone: "warn" },
  manual: { word: "Saved by hand", tone: "info" },
  sim: { word: "Simulated", tone: "warn" },
};
export function triggerInfo(inc) {
  return TRIGGERS[inc && inc.trigger] || { word: (inc && inc.label) || "Incident", tone: "info" };
}

const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "Today", "Yesterday", or "Mon 12 Oct", and the time apart, so the list can
// set the time big and the day as a caption.
export function whenParts(t, nowMs = Date.now()) {
  const d = new Date(t * 1000), now = new Date(nowMs);
  const day0 = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day0(now) - day0(d)) / 86400000);
  const day = diff === 0 ? "Today" : diff === 1 ? "Yesterday"
    : `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return { day, time: `${pad(d.getHours())}:${pad(d.getMinutes())}`, secs: pad(d.getSeconds()) };
}

// The peak, or nothing: a manual save has no peak, and "0.00 g" would be a
// reading it never took.
export function peakText(inc) {
  const g = inc && inc.peak_g;
  return typeof g === "number" && g > 0 ? `${g.toFixed(2)} g` : null;
}

// "62 → 0 km/h", "62 km/h before", or null. `conv` converts km/h to the
// user's unit; `unit` names it.
export function speedLine(inc, conv = (k) => k, unit = "km/h") {
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? Math.round(conv(v)) : null);
  const a = num(inc && inc.speed_before), b = num(inc && inc.speed_after);
  if (a === null && b === null) return null;
  if (a !== null && b !== null) return `${a} → ${b} ${unit}`;
  return a !== null ? `${a} ${unit} before` : `${b} ${unit} after`;
}

export function bytesText(n) {
  if (!Number.isFinite(n) || n <= 0) return "0 MB";
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
}

// The service's line: what the list's header says about whether this can work.
export function serviceState(list) {
  if (!list) return { tone: "bad", text: "OmaCar cannot reach its server" };
  if (list.demo) return { tone: "info", text: "Not recorded in the demo" };
  if (!list.running) return { tone: "warn", text: "Clip service off · omacar clips on" };
  const acc = list.accel;
  if (acc && acc.present === false) return { tone: "ok", text: "Watching speed · no accelerometer" };
  return { tone: "ok", text: "Watching speed and bumps" };
}

// ---- the telemetry ----------------------------------------------------------

// [[dt, value]] for one key, skipping rows that did not carry it.
export function series(tel, key) {
  const rows = (tel && tel.rows) || [];
  const out = [];
  for (const r of rows) if (typeof r[key] === "number" && Number.isFinite(r[key])) out.push([r.dt, r[key]]);
  return out;
}

// An SVG path for a series on a FIXED time axis (x0..x1 seconds from the
// trigger), so the line lines up with the needle whatever part of the window
// it covers. The line is broken, not bridged, across a hole of more than
// `gap` seconds: a stretch with no reading is not a straight line.
export function stripPath(pts, w, h, lo, hi, x0 = -BEFORE, x1 = AFTER, gap = 2.5) {
  if (!pts || pts.length < 2) return "";
  let d = "", prev = null;
  for (const [t, v] of pts) {
    const x = ((t - x0) / (x1 - x0)) * w;
    const c = Math.min(hi, Math.max(lo, v));
    const y = hi === lo ? h / 2 : h - ((c - lo) / (hi - lo)) * h;
    d += (prev === null || t - prev > gap ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
    prev = t;
  }
  return d;
}

// The value of a key nearest `dt`, within `within` seconds, else null (a
// reading that was not taken is never drawn as zero).
export function valueAt(tel, key, dt, within = 1.0) {
  let best = null, bd = Infinity;
  for (const r of (tel && tel.rows) || []) {
    if (typeof r[key] !== "number") continue;
    const d = Math.abs(r.dt - dt);
    if (d < bd) { bd = d; best = r[key]; }
  }
  return bd <= within ? best : null;
}

// A top for the speed axis: the next 20 above the peak, at least 40.
export function speedTop(tel) {
  const s = series(tel, "speed");
  const max = s.length ? Math.max(...s.map((p) => p[1])) : 0;
  return Math.max(40, Math.ceil((max + 1) / 20) * 20);
}

// Which keys the readouts can show for this incident.
export function readoutKeys(tel) {
  return ["speed", "rpm", "throttle", "soc", "ima_amps"].filter((k) => series(tel, k).length > 1);
}

// ---- the cameras, in step -----------------------------------------------------

// The part of a role's footage that covers incident time `dt`, with the video
// time inside it; null when the role has nothing there. Time is carried as
// seconds from the trigger, so the cameras share one clock whatever their
// own segment starts were.
export function partAt(inc, role, dt) {
  const r = inc && inc.roles && inc.roles[role];
  if (!r || !r.parts) return null;
  const abs = inc.t + dt;
  for (const p of r.parts) {
    if (typeof p.start === "number" && abs >= p.start && abs < p.start + p.dur) {
      return { file: p.file, at: abs - p.start, part: p };
    }
  }
  return null;
}

// The span of dt every camera together can show, for the scrubber: from the
// earliest part start to the latest part end, clamped to the window.
export function span(inc) {
  let lo = Infinity, hi = -Infinity;
  for (const role of ROLES) {
    const r = inc && inc.roles && inc.roles[role];
    if (!r) continue;
    for (const p of r.parts || []) { lo = Math.min(lo, p.start - inc.t); hi = Math.max(hi, p.start + p.dur - inc.t); }
  }
  if (!Number.isFinite(lo)) return [-BEFORE, AFTER];
  return [Math.max(-BEFORE, lo), Math.min(AFTER, hi)];
}

// A follower is moved only when it has drifted: setting currentTime on every
// tick would stutter the picture.
export function needsSeek(have, want, tol = 0.3) {
  return Math.abs(have - want) > tol;
}

export function clampDt(dt, [lo, hi]) { return Math.min(hi - 0.05, Math.max(lo, dt)); }

export function dtText(dt) {
  const s = Math.abs(dt) < 0.05 ? 0 : dt;
  return `${s > 0 ? "+" : s < 0 ? "−" : ""}${Math.abs(s).toFixed(1)} s`;
}

// ---- the toast ----------------------------------------------------------------

// What the poller says on seeing `last` after `prevId`. prevId undefined is
// "not asked yet": the first answer only sets the baseline, so opening the
// app never toasts a clip from an hour ago.
export function toastFor(prevId, last, running) {
  if (prevId === undefined) return { id: last ? last.id : null, say: null };
  if (!last || !running) return { id: prevId, say: null };
  if (last.id === prevId) return { id: prevId, say: null };
  return { id: last.id, say: "Clip saved" };
}
