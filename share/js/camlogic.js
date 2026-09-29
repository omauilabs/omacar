// The Cameras tab's arithmetic, apart from the DOM so it can be tested: what
// the badge says, what each feed's caption says, and where each clip and
// event sits on the last hour's timeline.

export const ROLES = ["front", "rear", "cabin"];
export const ROLE_LABEL = { front: "Front camera", rear: "Rear camera", cabin: "Cabin camera" };
export const EVENT_LABEL = { "hard-braking": "Hard braking", marked: "Marked", saved: "Saved clip" };

const pad = (n) => String(n).padStart(2, "0");
export function hhmm(t) { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
export function hhmmss(t) { return `${hhmm(t)}:${pad(new Date(t * 1000).getSeconds())}`; }

// "1080p" from the recorder's mode; nothing when there is no mode.
export function resLabel(mode) { return mode && mode.h ? `${mode.h}p` : ""; }

// LIVE · 3 cameras, or SIMULATED when any recording role is a test picture:
// the same rule as the car's own badge.
export function camBadge(ov) {
  if (!ov) return { text: "NO SERVER", tone: "bad" };
  const rec = ROLES.filter((r) => ov.running && ov.roles && ov.roles[r] && ov.roles[r].recording);
  if (!rec.length) return { text: "NOT RECORDING", tone: "" };
  if (rec.some((r) => ov.roles[r].sim)) return { text: "SIMULATED", tone: "warn" };
  return { text: `LIVE · ${rec.length} camera${rec.length === 1 ? "" : "s"}`, tone: "ok" };
}

// A feed's caption, and, when it has no picture, why not. `r.recording` is
// only true once the recorder has that camera's first live frame (lib/cams.py
// Camera.status()); before that — starting, or restarting after a stall — and
// for a stalled, refused or mistyped-pattern role, `r.error` already carries
// the reason to show, and this never claims REC for it.
export function feedState(ov, role) {
  const r = (ov && ov.roles && ov.roles[role]) || {};
  const title = [ROLE_LABEL[role], resLabel(r.mode)].filter(Boolean).join(" · ");
  const off = (why) => ({ title, rec: false, sim: false, live: false, why });
  if (!ov) return off("OmaCar cannot reach its server");
  if (!ov.running) return off(r.device ? "Recorder off — omacar cams on" : "No camera");
  if (!r.recording) return off(r.error && r.error !== "no camera" ? r.error : "No camera");
  return { title, rec: true, sim: !!r.sim, live: !!r.live, why: r.live ? null : "Waiting for the picture" };
}

// Home's Dashcams card: the front picture, or in words why there is none.
// Only the front role matters here; a card that showed the rear's trouble
// would blame the wrong camera. `why` is a reason to show in place of the
// picture, and is also what the card says while it waits for its first frame.
export function dashState(ov) {
  const off = (why) => ({ live: false, rec: false, sim: false, why });
  if (!ov) return off("OmaCar cannot reach its server");
  const r = (ov.roles && ov.roles.front) || {};
  if (!ov.running) return off(r.device ? "Recorder off" : "No front camera");
  if (!r.recording) return off(!r.error || r.error === "no camera" ? "No front camera" : r.error);
  return { live: !!r.live, rec: true, sim: !!r.sim, why: r.live ? null : "Waiting for the picture" };
}

// "12.3 of 40 GB".
export function storageLine(s) {
  if (!s) return "";
  return `${(s.used / 1e9).toFixed(1)} of ${Math.round(s.budget / 1e9)} GB`;
}

// The last hour, laid out: the main camera's clips as spans, every event as a
// marker, and a label every five minutes. x and w are fractions of the width.
export function timelineModel(clips, events, role, now, span = 3600) {
  const t0 = now - span;
  const x = (t) => Math.min(1, Math.max(0, (t - t0) / span));
  const ticks = clips
    .filter((c) => c.role === role && c.end > t0 && c.start < now)
    .map((c) => ({ file: c.file, x: x(c.start), w: x(c.end) - x(c.start), locked: !!c.locked }));
  const markers = events
    .filter((e) => e.t >= t0 && e.t <= now)
    .map((e) => ({ id: e.id, kind: e.kind, t: e.t, x: x(e.t), label: `${EVENT_LABEL[e.kind] || e.kind} · ${hhmm(e.t)}` }));
  const labels = [];
  for (let t = Math.ceil(t0 / 300) * 300; t <= now; t += 300) labels.push({ x: x(t), text: hhmm(t) });
  return { t0, t1: now, ticks, markers, labels };
}

// The clip of `role` that covers time t, and how far into it t is.
export function clipAt(clips, role, t) {
  const c = clips.find((k) => k.role === role && k.start <= t && t < k.end);
  return c ? { file: c.file, pos: t - c.start } : null;
}

// Ten seconds back or forward, across a clip boundary when it has to, and over
// a gap to the next clip. Past the newest clip is null: back to live.
export function stepAcross(clips, role, file, pos, delta) {
  const mine = clips.filter((c) => c.role === role).sort((a, b) => a.start - b.start);
  const cur = mine.find((c) => c.file === file);
  if (!cur) return null;
  const t = cur.start + pos + delta;
  const hit = mine.find((c) => c.start <= t && t < c.end);
  if (hit) return { file: hit.file, pos: t - hit.start };
  const next = mine.find((c) => c.start > t);
  return next ? { file: next.file, pos: 0 } : null;
}
