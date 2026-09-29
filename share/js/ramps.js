// The shapes of every level change, as plans run with linearRampToValueAtTime.
// Pure, and tested.
//
// linearRampToValueAtTime is linear in AMPLITUDE: one long ramp from quiet to
// loud gains most of its decibels in its first instant, which is exactly the
// startle the design forbids. So a plan is a point every 100 ms at most,
// evenly spaced in dB, and no 100 ms of it changes by more than 3 dB. A span
// too big for its time is stretched until it fits, never stepped.
//
//   rampPlan(level, from, to)           the music bus: the Level 1 swell, the
//                                       duck, the return after a release
//   envelopePlan(level, target, hold)   ONE ALERT SOUND'S OWN LEVEL: up from
//                                       silence, held, and back to silence
//   releasePlan(from)                   a sound let go early, down to silence
//   releaseAt(env, now)                 WHEN a sound can be let go without a
//                                       step, on any browser
//   planOnto(events, at, points)        what a bus's automation does once a
//                                       plan is laid onto it, so the next
//                                       plan can start where it really is
//
// SILENCE IS -48 dBFS. Every alert sound starts and ends there: 36 dB under
// the music and below road noise at any speed, and exactly far enough under
// a Level 2 alert (-3 dBFS) that 3 dB per 100 ms reaches it in the spec's
// 1.5 s.

export const RAMP_SECS = {
  0: { up: 3, down: 3 },       // release: music comes back over 3 s
  1: { up: 10, down: 30 },     // Level 1: the radio rises over 10 s, settles over 30 s
  2: { up: 1.5, down: 0.5 },   // Level 2: music ducks in 0.5 s
  3: { up: 3, down: 0.5 },     // Level 3: music ducks in 0.5 s
};
export const MAX_DB_PER_STEP = 3;
export const STEP_SECS = 0.1;
export const SILENCE_DB = -48;
export const RISE_SECS = { 1: 1.5, 2: 1.5, 3: 3 };   // a sound's rise from silence, by level
export const RELEASE_SECS = 3;                        // and its fall back to it

function ramp(from, to, minSecs) {
  if (!Number.isFinite(from) || !Number.isFinite(to)) throw new Error("a ramp needs finite levels in dB");
  const span = Math.abs(to - from);
  const secs = Math.max(minSecs, (span / MAX_DB_PER_STEP) * STEP_SECS);
  const n = Math.max(1, Math.ceil(secs / STEP_SECS - 1e-6));
  const points = [];
  // The last point is `to` itself, not arithmetic that lands a hair beside it.
  for (let i = 0; i <= n; i++) points.push([+((secs * i) / n).toFixed(4), i === n ? to : from + ((to - from) * i) / n]);
  return { secs, points };
}

export function rampPlan(level, from, to) {
  const spec = RAMP_SECS[level];
  if (!spec) throw new Error(`no ramp for level ${level}`);
  return ramp(from, to, to > from ? spec.up : spec.down);
}

export function envelopePlan(level, targetDb, holdSecs) {
  if (!RISE_SECS[level]) throw new Error(`no envelope for level ${level}`);
  const up = ramp(SILENCE_DB, targetDb, RISE_SECS[level]);
  if (holdSecs === Infinity) return { points: up.points, rise: up.secs, secs: Infinity };
  const down = ramp(targetDb, SILENCE_DB, RELEASE_SECS);
  const t1 = +(up.secs + holdSecs).toFixed(4);
  return {
    points: [...up.points, [t1, targetDb], ...down.points.slice(1).map(([t, d]) => [+(t1 + t).toFixed(4), d])],
    rise: up.secs,
    secs: +(t1 + down.secs).toFixed(4),
  };
}

export function releasePlan(fromDb) {
  return ramp(Math.max(SILENCE_DB, fromDb), SILENCE_DB, RELEASE_SECS);
}

// WHEN a sound playing `env` (absolute [t, dB] points) can be let go, at or
// just after `now`, so that its release starts exactly where it is on every
// browser. With cancelAndHoldAtTime any moment would do. Without it (Firefox)
// the only cut that cannot step is one that keeps a point of the envelope and
// drops only what comes after: cancelling a ramp in flight drops its end, and
// the level falls back to where the ramp began. So: `now` itself where the
// envelope is standing still (a hold, or past its last point), and otherwise
// its next point, which is never more than 100 ms away.
export function releaseAt(env, now) {
  if (now <= env[0][0]) return now;
  for (let i = 1; i < env.length; i++) {
    const [, a] = env[i - 1], [t1, b] = env[i];
    if (now <= t1) return a === b ? now : t1;
  }
  return now;
}

// What a bus's automation does once schedule(bus, points, at, append) has
// run: the events it already had ([[t, dB], ...] in context time, at least
// one), cut at `at` and held there as cancelAndHoldAtTime holds them, then
// the plan's points; or, with `append`, the plan simply following on. dbAt()
// of the result is the level the bus will really have at any moment, which is
// where the next plan must start: a plan that starts anywhere else is a step.
export function planOnto(events, at, points, append = false) {
  const kept = append ? events.slice() : [...events.filter(([t]) => t < at), [at, dbAt(events, at)]];
  return [...kept, ...points.map(([t, d]) => [at + t, d])];
}

// Where a plan is at time t, in dB. Each segment is linear in amplitude,
// because that is what linearRampToValueAtTime does.
export function dbAt(points, t) {
  if (t <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    const [t0, a] = points[i - 1], [t1, b] = points[i];
    if (t === t1) return b;   // exactly on a point is exactly its level, not arithmetic beside it
    if (t < t1) {
      const ga = 10 ** (a / 20), gb = 10 ** (b / 20);
      const g = t1 > t0 ? ga + ((gb - ga) * (t - t0)) / (t1 - t0) : gb;
      return 20 * Math.log10(g);
    }
  }
  return points[points.length - 1][1];
}

// The largest change over any 100 ms of a plan, walked in 10 ms steps: the
// spec's rule, measured the way the ear meets it.
export function worstStep(points, until = points[points.length - 1][0]) {
  let worst = 0;
  for (let t = points[0][0]; t + STEP_SECS <= until + 1e-9; t += 0.01) {
    worst = Math.max(worst, Math.abs(dbAt(points, t + STEP_SECS) - dbAt(points, t)));
  }
  return worst;
}
