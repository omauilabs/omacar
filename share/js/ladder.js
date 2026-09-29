// The ladder: three alert levels, raised from the measures (drowsy.js) and two
// signals that need no camera, and released by the driver. Pure, with the
// clock injected: step() is handed the time, and nothing here reads Date.now().
//
//   1 · Notice     PERCLOS >= 15%, 3 yawns or 3 nods in 5 min, 2 h since a
//                  stop, or night hours (once an hour)
//   2 · Wake       eyes closed >= 1.0 s, or PERCLOS >= 25%
//   3 · Pull over  eyes closed >= 2.0 s, or two Level 2 alerts within 5 min
//
// EVIDENCE, NOT SPEED -- but never evidence gathered while parked either
// (fix round 2, I4, correcting this header -- NR4). A trigger is watched
// only while the car is active, or an alert is already sounding: that carve
// -out is what lets new evidence escalate an alert below the gate (M8), the
// only sense in which anything still counts "below the gate". Nothing ever
// arms purely from being below the gate with nothing already up -- a doze at
// a rest stop, or yawning at a red light, is never evidence for the drive
// that follows. A stop (5 min or more) or a tap discards armed-but-unused
// evidence -- every trigger, including yawns and nods -- as a second line of
// defense for whatever the gate does let arm while escalating. A
// discontinuity snapshot (m.discontinuity === true) is never read as
// evidence and never advances a release, on its own -- fix round 1, C1/I2,
// after a camera-gap snapshot slipped through as "eyes open".
//
// PERCLOS HYSTERESIS (fix round 1, C1/I1). PERCLOS is a noisy, frame-by-frame
// ratio: a value that sits within about 0.002 of a threshold can cross it and
// re-cross it from one frame to the next. So a PERCLOS trigger (Level 1's 15%
// and Level 2's 25%) arms only once it has read at or above its threshold for
// 3 continuous seconds, and it re-arms only after it has since read below
// (threshold - 0.03) for 10 continuous seconds. Without this, a one-frame
// wobble around 25% could count as two separate Level 2 raises and jump
// straight to Level 3 on the strength of a single episode -- "two Level 2
// ALERTS within 5 min" (repeat-l2) now counts only a Level 2 that is actually
// raised, never a re-arm while one is already sounding.
//
// PERCLOS LATCHES (NR1/NR3, fix round 3; NR5, fix round 4). A gate that
// raises is latched, and so, on any release, is every gate whose reading is
// still at or above its threshold: the reading has not changed just because
// the alert cleared. A latch exists only to stop the SAME 60 s window
// raising twice, so it lasts only as long as that window can: it expires
// once the measures' window holds no frame from the latch's own step (by
// m.t, pruned exactly as drowsy.js prunes it), and at once on a
// discontinuity or a camera restart, which empty that window. A recovery
// (below threshold - 0.03 for 10 s) frees it earlier. After any of these a
// reading at or above threshold is new evidence and raises after the usual
// 3 s hold; so a second Level 2 -- and with it Level 3 -- can come from a
// fresh window, never from the one that already raised.
//
// A SOUNDING ALERT AND THE GATE. With alert_continues_below_gate (the
// default), a raised level carries on below 30 mph, repeats and all. Set to
// false, it falls silent below the gate -- including Level 3's held alarm,
// which gets an explicit fade so nothing keeps sounding with no repeat cue to
// stop it -- and its card stays. New evidence on an already-sounding alert
// (for example a 2 s closure on top of a Level 2) may still escalate it: it
// is the same episode, so M8 lets the escalation through even below the gate,
// while no NEW alert may start there.
//
// RELEASE. Every level clears on "I'm awake" or once the car is stationary.
// Levels 1 and 2 also clear once the eyes have been open, continuously, for
// 5 s with PERCLOS not rising against the value it read when that open run
// began -- or against the first value read once PERCLOS becomes available,
// if the run started before it did (N3, fix round 2): a run is never stuck
// unreleasable just because it began in the 30 s after a discontinuity or
// right after calibration. "Continuously" tolerates a blink: closures under
// 0.5 s do not reset the run, only ones of 0.5 s or more do (M7) -- an
// ordinary blink cadence of one every 2-5 s must not make Levels 1 and 2
// clear only on the tap. Because the measures module resets its own
// m.openFor on every blink (drowsy.js), the ladder tracks the open run
// itself from m.closed/m.closedFor rather than trusting m.openFor -- and
// because that module samples the cabin feed at 10 fps, a real closure's
// reported length undercounts its true one by up to about a frame interval,
// so the 0.5 s cutoff is applied a half frame interval early (N4).
//
// THE SNAPSHOT'S OWN CLOCK (fix round 2, I2). Release timing, and the
// "strictly advancing" check that guards it, run on m.t -- the measures
// snapshot's own timestamp -- never on step()'s own t. The real caller
// steps on a wall clock and can re-feed the same stale snapshot twice a
// second while no new camera frame has arrived; keyed on the step's own t,
// that reads as the eyes having stayed open for however long the wall clock
// ran, with no new frame in evidence. Keyed on m.t, a snapshot whose time
// has not moved since the last step is never evidence for a release, no
// matter how much wall-clock time passed around it. A PERCLOS latch's
// expiry and the yawn and nod windows run on m.t too (NR5/NR7), since they
// mirror the measures' own windows. Everything else -- PERCLOS's hold and
// re-arm timers, repeat and voice scheduling, the Level 2 history -- still
// runs on step()'s own t, since those are the app's own timing, not the
// camera's.
//
// Level 3 has no time cap and does not clear on open eyes: it ends on
// "I'm awake" or a stopped car.

const has = (v) => v !== null && v !== undefined;

const BLINK_SECS = 0.5;          // M7: closures shorter than this are a blink,
                                  // not a break in the open-eye release run.
const CABIN_FPS = 10;            // N4: the plan's cabin feed rate.
const FRAME_SECS = 1 / CABIN_FPS;
// N4: drowsy.js times a closure from its first closed frame to whenever it
// is next read while still closed, so at CABIN_FPS its reported closedFor
// undercounts a real closure's true length by up to about a frame interval.
// Comparing against BLINK_SECS itself would let some real closures at or
// above it read under 0.5 s and be tolerated as a blink; a half frame
// interval early still tolerates a real ~0.45 s blink.
const EFFECTIVE_BLINK_SECS = BLINK_SECS - FRAME_SECS / 2;
const PERCLOS_HOLD_SECS = 3;     // C1/I1: continuous time at/above threshold
const PERCLOS_MARGIN = 0.03;     // ...to arm, and below (threshold - margin)
const PERCLOS_REARM_SECS = 10;   // ...for this long, to re-arm.
const TAP_QUIET_SECS = 600;      // N8: camera-free Level 1 notices are quiet
                                  // for this long after "I'm awake".

// A PERCLOS trigger arms once the current step is eligible (evidenceOK: the
// car is active, or an alert is already sounding -- I4, fix round 2) and a
// continuous run at or above threshold reaches PERCLOS_HOLD_SECS (C1/I1); a
// run while ineligible never counts toward that hold at all, so it cannot
// be "finished" by evidence gathered below the gate or while parked.
// Latching -- the PERCLOS_REARM_SECS recovery this trigger must then wait
// out -- happens only when the caller confirms the arm was actually used
// for a raise, via perclosLatch (N1, fix round 2): an episode that arms and
// is then discarded unused must return to fresh, not get stuck blocked
// forever with no way to read below its own threshold again.
//
// A latched gate has three ways out (NR5, fix round 4). A latch exists only
// to stop the SAME 60 s window raising twice, so:
//   - it expires once the window holds no frame from the latch's own step:
//     a fresh snapshot (m.t advanced, not a discontinuity) whose m.t is more
//     than perclos.window_secs past the latch's m.t -- the very rule
//     drowsy.js prunes its window by. Stale re-feeds do not move m.t, so
//     they never age a latch;
//   - it expires at once on a discontinuity or a camera restart
//     (perclosReset, from step()), since the measures then empty their
//     window: the reading that follows still needs drowsy.js's 30 s
//     minimum window, then the 3 s hold;
//   - and, earlier, it re-arms once PERCLOS has read below (threshold -
//     PERCLOS_MARGIN) for PERCLOS_REARM_SECS.
// A latched gate runs no hold, so whichever way it goes, a reading at or
// above threshold must then be held a full PERCLOS_HOLD_SECS from that step.
const freshGate = () => ({ holdSince: null, belowSince: null, blocked: false, latchMT: null });
const gateOf = (perc, key) => perc[key] || (perc[key] = freshGate());

function perclosGate(perc, key, threshold, value, t, evidenceOK, mt, windowSecs) {
  const st = gateOf(perc, key);
  if (st.blocked && mt - st.latchMT > windowSecs) st.blocked = false;
  const above = evidenceOK && has(value) && value >= threshold;
  const belowMargin = has(value) && value < threshold - PERCLOS_MARGIN;
  st.belowSince = belowMargin ? (st.belowSince === null ? t : st.belowSince) : null;
  if (st.blocked && st.belowSince !== null && t - st.belowSince >= PERCLOS_REARM_SECS) st.blocked = false;
  st.holdSince = above && !st.blocked ? (st.holdSince === null ? t : st.holdSince) : null;
  if (st.holdSince !== null && t - st.holdSince >= PERCLOS_HOLD_SECS) {
    st.holdSince = null;
    return true;
  }
  return false;
}

// N1/NR3: block a gate, creating its entry if this is the first time it is
// touched -- a sibling gate latched by NR3 may never have armed at all. The
// latch is stamped with the snapshot's own m.t (NR5); latching a gate that
// is already latched restamps it, since the newer reading's window is the
// one that must now turn over. A hold in progress is dropped.
function perclosLatch(perc, key, mt) {
  const st = gateOf(perc, key);
  st.blocked = true;
  st.latchMT = mt;
  st.holdSince = null;
}

// I4: a stop of 5 min or more returns a gate fully to fresh -- unarmed and
// unblocked -- not merely clearing the outer armed flag. This is the strong
// discard: even a gate that raised is cleared, since a real stop is rest.
// NR5: a discontinuity or a camera restart does the same, since the
// measures' PERCLOS window no longer holds any frame the latch was about.
function perclosReset(perc, key) {
  perc[key] = freshGate();
}

// NR1: a tap is the weak discard. It still clears an armed-but-UNUSED hold
// (round 2's N1), but it must never un-latch a gate that already raised --
// "I'm awake" answers the alert, it does not make PERCLOS's own 60 s window
// read as fresh evidence again. Only the normal re-arm rule (below margin
// for PERCLOS_REARM_SECS) frees a gate that actually raised something.
function perclosTapDiscard(perc, key) {
  const st = perc[key];
  if (st && st.blocked) return;
  perc[key] = freshGate();
}

// Sensitive lowers every trigger threshold by 20%: the closed-eye margin and
// its cap, PERCLOS, the yawn and nod measures and counts, the closure times,
// the time since a stop, and the Level 2 count, which cannot go below two.
// Windows, the baseline minute, the release time and the hourly limit are not
// thresholds and stay as they are.
// Idempotent by marker (M4): a config already scaled is returned as a clone,
// unchanged, so a caller that scales once at load and again after a settings
// round-trip cannot compound the 20% cut into 36%.
export function scaled(cfg, sensitivity = cfg.sensitivity) {
  const c = JSON.parse(JSON.stringify(cfg));
  if (sensitivity !== "sensitive") return c;
  if (c._scaled) return c;
  const f = 0.8;
  const r = (x, d = 4) => +(x * f).toFixed(d);
  const n = (x) => Math.max(1, Math.round(x * f));
  c.eyes.closed_over_baseline = r(c.eyes.closed_over_baseline);
  c.eyes.closed_cap = r(c.eyes.closed_cap);
  c.yawn.jaw_open = r(c.yawn.jaw_open);
  c.yawn.hold_secs = r(c.yawn.hold_secs, 3);
  c.nod.below_deg = r(c.nod.below_deg, 2);
  c.nod.hold_secs = r(c.nod.hold_secs, 3);
  c.level1.perclos = r(c.level1.perclos);
  c.level1.yawns = n(c.level1.yawns);
  c.level1.nods = n(c.level1.nods);
  c.level1.since_stop_secs = Math.round(c.level1.since_stop_secs * f);
  c.level2.closed_secs = r(c.level2.closed_secs, 3);
  c.level2.perclos = r(c.level2.perclos);
  c.level3.closed_secs = r(c.level3.closed_secs, 3);
  c.level3.level2_count = Math.max(2, n(c.level3.level2_count));
  c._scaled = true;
  return c;
}

// Handles a window that wraps past midnight (M6): from <= to is the ordinary
// same-day window; from > to (for example 22 -> 6) is night whenever the hour
// is at or after from, or before to.
export function isNight(hour, cfg) {
  const { night_from_hour: from, night_to_hour: to } = cfg.level1;
  return from <= to ? hour >= from && hour < to : hour >= from || hour < to;
}

// Time since the last stop: connected and speed 0 for 5 minutes or more
// counts. A drive starts when the app does.
//
// A dropped link is not a stop (I3, fix round 1): while disconnected, both
// clocks freeze rather than run. sinceStop holds at whatever it read the
// instant the link dropped -- so a long connected drive does not lose its
// 2-hour notice to a 5-minute dead zone -- and stoppedFor reads 0 throughout,
// because a disconnected box cannot tell whether the car kept moving. On
// reconnect, stoppedFor restarts from 0: the disconnected span is never
// credited as stationary time, so the post-Level-3 banner clears only after
// 2 minutes actually observed connected and stopped.
export function createStopClock(cfg) {
  let still = null, lastStop = null, frozenSinceStop = null, wasConnected = null;
  return {
    feed(t, kph, connected) {
      if (lastStop === null) lastStop = t;
      if (!connected) {
        if (frozenSinceStop === null) frozenSinceStop = t - lastStop;
        still = null;
        wasConnected = false;
        return { sinceStop: frozenSinceStop, stoppedFor: 0 };
      }
      if (wasConnected === false) {
        // Reconnecting: resume sinceStop from where it was frozen, as if the
        // disconnected span had not passed, and start stoppedFor fresh.
        lastStop = t - frozenSinceStop;
        still = null;
      }
      wasConnected = true;
      frozenSinceStop = null;
      if (kph === 0) {
        if (still === null) still = t;
        if (t - still >= cfg.stop.still_secs) lastStop = t;
      } else still = null;
      return { sinceStop: t - lastStop, stoppedFor: still === null ? 0 : t - still };
    },
  };
}

// LEVEL 2'S TURNS (Task 7 fix round 1). Each Level 2 rotation sound gets a
// turn of level2.repeat_secs (5 s by default) before the next cue, and the
// voice a longer turn of its own, level2.voice_slot_secs (7 s), so that its
// line can start once its own rise is nearly done and still be heard whole
// before the next sound (alertplayer.js holds every sound inside its turn).
// A sound's own rise and fall alone take 4.5 s, so no turn is shorter than
// MIN_SLOT_SECS. level2.voice: false leaves the voice out of the rotation
// altogether -- the owner's one-line choice of "voice only at Levels 1 and 3".
export const MIN_SLOT_SECS = 4.6;
export function slotsOf(cfg) {
  const L2 = (cfg && cfg.level2) || {};
  const s = (x, d) => Math.max(MIN_SLOT_SECS, Number.isFinite(x) ? x : d);
  return { repeat: s(L2.repeat_secs, 5), voice: s(L2.voice_slot_secs, 7) };
}

export function createLadder(cfg0, sounds0 = cfg0.sounds) {
  let cfg, L1, L2, L3, rota;
  // New settings keep the ladder's memory: its level, the Level 2 history,
  // the hourly limits and the armed evidence. Only the thresholds change,
  // and the rotation only when sounds is actually passed (M5): setConfig(c)
  // alone must keep the current list rather than falling back to c.sounds
  // and re-adding "voice" after a caller filtered it out for missing clips.
  // N5: the very first call must still leave a usable rotation -- ["alarm"]
  // -- even when the config carries no sounds list at all, so a ladder built
  // from a bare config does not throw on its first Level 2.
  function setConfig(c, sounds) {
    cfg = c;
    L1 = c.level1;
    L2 = c.level2;
    L3 = c.level3;
    if (sounds !== undefined) rota = sounds && sounds.length ? [...sounds] : ["alarm"];
    else if (rota === undefined) rota = ["alarm"];
  }
  setConfig(cfg0, sounds0);

  let level = 0, trigger = null, banner = false;
  let rot = 0, nextRepeat = null, nextVoice = null, alarmSilenced = false;
  let openStreakStart = null, perclosAtOpenStreak = null;
  let lastMT = null, lastTapT = null;
  const was = {}, armed = {};
  const perc = {};               // PERCLOS hysteresis state, per trigger key
  // NR2/NR7: a mirror of drowsy.js's own yawn and nod windows -- each event
  // it has counted, as [m.t first seen, seen on an evidenceOK step] -- so a
  // new event is told apart from an old one leaving, even on one frame.
  const seenYawns = [], seenNods = [];
  const l2Times = [];
  const lastFree = {};

  function rotation() {
    let list = L2.voice === false ? rota.filter((x) => x !== "voice") : rota;
    if (!list.length) list = ["alarm"];
    const s = list[rot++ % list.length];
    if (s === "voice") return { kind: "voice", clip: "l2" };
    if (s === "alarm") return { kind: "alarm" };
    return { kind: "bark" };
  }
  // The next Level 2 cue comes once this one's turn is over.
  function turn(cue, t) {
    const slots = slotsOf(cfg);
    nextRepeat = t + (cue.kind === "voice" ? slots.voice : slots.repeat);
    return cue;
  }

  function raise(to, why, t, out) {
    level = to;
    trigger = why;
    openStreakStart = null;
    out.raised = { level: to, trigger: why };
    if (to === 1) out.cues.push({ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" });
    if (to === 2) {
      rot = 0; // M1: each new Level 2 episode opens on the driver's first choice.
      out.cues.push({ kind: "duck" }, turn(rotation(), t));
    }
    if (to === 3) {
      banner = true;
      nextRepeat = null;
      alarmSilenced = false;
      out.cues.push({ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
  }

  function clear(out, m, mt) {
    level = 0;
    trigger = null;
    nextRepeat = nextVoice = null;
    openStreakStart = null;
    alarmSilenced = false;
    out.cleared = true;
    out.cues.push({ kind: "fade" });
    // NR3: on ANY release -- a tap, open eyes, or a stop -- a PERCLOS gate
    // still reading at or above its own threshold right now is latched too,
    // sibling or not. The reading did not change just because the level
    // cleared, so it is not new evidence and must not immediately re-arm
    // and raise again a moment later (a fresh Level 2, or Level 1 on Level
    // 2's own release). It is freed as any latch is (NR5): a recovery, the
    // window turning over past this snapshot's m.t, or a camera gap.
    if (m && has(m.perclos)) {
      if (m.perclos >= L2.perclos) perclosLatch(perc, "perclos2", mt);
      if (m.perclos >= L1.perclos) perclosLatch(perc, "perclos1", mt);
    }
  }

  function step(inp) {
    const t = inp.t, m = inp.m || {};
    const out = { t, level, trigger, raised: null, cleared: false, cues: [], banner };

    // I2 (fix round 2): release timing runs on the snapshot's OWN clock,
    // m.t, not step()'s t -- see the header. Falls back to t only if a
    // caller's snapshot genuinely carries no t of its own.
    const mt = has(m.t) ? m.t : t;
    const prevMT = lastMT;
    const advanced = prevMT === null || mt > prevMT;
    lastMT = mt;

    // NR5 (fix round 4): a discontinuity -- drowsy.js's own flag, on a
    // snapshot that is not a re-feed of the last one -- or a camera restart,
    // a snapshot clock that went backwards (drowsy.js flags that too, but
    // it is not "advanced" here), empties the measures' PERCLOS window. No
    // frame a latch was taken on remains, so every latch expires at once,
    // and no hold may span the gap. Done before the release below, so a
    // release on this very step latches on its own (post-gap) reading.
    if (prevMT !== null && mt !== prevMT && (m.discontinuity || mt < prevMT)) {
      perclosReset(perc, "perclos1");
      perclosReset(perc, "perclos2");
    }

    // Release first: "I'm awake", a stationary car, or (Levels 1 and 2) eyes
    // that have stayed open. A blink under BLINK_SECS (effectively half a
    // frame interval early -- N4) does not break the open run (M7);
    // m.discontinuity always does (I2) -- a camera gap is never credited as
    // time the driver's eyes were seen open.
    if (level > 0) {
      // N3 detail (fix round 3): captured BEFORE this step may set the
      // baseline below, so the step where PERCLOS first appears cannot
      // satisfy "not rising" by comparing a reading with itself -- release
      // on fresh PERCLOS is deferred one step, to a genuine comparison.
      const hadBaseline = perclosAtOpenStreak !== null;
      if (level < 3) {
        if (m.discontinuity || !m.face || !m.calibrated) {
          openStreakStart = null;
          perclosAtOpenStreak = null;
        } else if (m.closed) {
          if (m.closedFor >= EFFECTIVE_BLINK_SECS) { openStreakStart = null; perclosAtOpenStreak = null; }
        } else if (openStreakStart === null) {
          openStreakStart = mt;
          perclosAtOpenStreak = has(m.perclos) ? m.perclos : null;
        } else if (perclosAtOpenStreak === null && has(m.perclos)) {
          // N3: a run that began before PERCLOS was available is kept, not
          // discarded -- the baseline is simply taken once a value appears.
          perclosAtOpenStreak = m.perclos;
        }
      }
      const steady = level < 3 && !m.discontinuity && advanced && openStreakStart !== null && hadBaseline
        && mt - openStreakStart >= cfg.release.open_secs
        && has(m.perclos) && perclosAtOpenStreak !== null && m.perclos <= perclosAtOpenStreak;
      if (inp.tap || steady || inp.parked) clear(out, m, mt);
    }
    if (banner && inp.stoppedFor >= cfg.banner_stopped_secs) banner = false;

    // I4 (fix round 2): evidence only arms or counts while the car is
    // active, or an alert is already sounding (the same "escalation below
    // the gate is fine" carve-out M8 needs) -- never merely while parked or
    // below the gate with nothing up. Computed once, after release may have
    // just cleared the level, and reused for both arming and consumption.
    const evidenceOK = inp.active || level > 0;

    // N8: the tap's own step already falls inside its own quiet period.
    if (inp.tap) lastTapT = t;

    // Evidence, on every step: armed on its rising edge, disarmed when it
    // goes false, used up by a raise. was[] always tracks the real reading,
    // evidenceOK or not, so a value that turns true while ineligible and
    // simply stays true never produces a pent-up rising edge the moment the
    // car goes active -- that would be evidence gathered while not active,
    // arming late instead of never. A discontinuity snapshot never counts as
    // evidence on its own (I2) -- skipped outright, not merely trusted to
    // read as "false" because Task 5 already reset its numbers.
    if (!m.discontinuity && advanced) {
      const see = (k, v) => {
        if (v && !was[k] && evidenceOK) armed[k] = true;
        if (!v) armed[k] = false;
        was[k] = !!v;
      };
      see("closed3", m.closedFor >= L3.closed_secs);
      see("closed2", m.closedFor >= L2.closed_secs);

      // NR2: a yawn or nod counts only if drowsy.js's own running count
      // (m.yawns/m.nods) increments on a step where evidenceOK is true -- a
      // below-gate yawn is never counted at all, so it can never mask a
      // later rising edge made entirely of active evidence. Kept in the
      // ladder's own window (L1.count_window_secs), independent of
      // drowsy.js's own count, which still includes below-gate events.
      // NR7 (fix round 4): drowsy.js's count is itself windowed, so an old
      // event leaving on the same frame a new one arrives leaves it
      // unchanged; diffing it lost the new one. The mirror is pruned by
      // drowsy.js's own rule (older than count_window_secs, by m.t) and then
      // to its count, oldest first, should it lag; whatever the count still
      // holds beyond the mirror is new since the last fresh snapshot.
      const countActive = (seen, raw) => {
        while (seen.length && mt - seen[0][0] > L1.count_window_secs) seen.shift();
        if (has(raw)) {
          while (seen.length > raw) seen.shift();
          while (seen.length < raw) seen.push([mt, evidenceOK]); // none before: taken whole
        }
        return seen.filter(([, active]) => active).length;
      };
      const activeYawnCount = countActive(seenYawns, m.yawns);
      const activeNodCount = countActive(seenNods, m.nods);
      see("yawns", activeYawnCount >= L1.yawns);
      see("nods", activeNodCount >= L1.nods);
      // PERCLOS triggers use their own hold/re-arm hysteresis (C1/I1), not a
      // plain rising edge; evidenceOK gates whether time counts toward the
      // hold at all, not just whether the result is kept. Only a fresh,
      // non-discontinuity snapshot gets here, so only such a one can age a
      // latch out (NR5) -- whatever the speed, since drowsy.js keeps and
      // drops frames whatever the speed: the window is what the latch is
      // about. A hold still runs only while evidenceOK.
      const W = cfg.perclos.window_secs;
      if (perclosGate(perc, "perclos2", L2.perclos, m.perclos, t, evidenceOK, mt, W)) armed.perclos2 = true;
      if (perclosGate(perc, "perclos1", L1.perclos, m.perclos, t, evidenceOK, mt, W)) armed.perclos1 = true;
    }

    // M8: no NEW alert starts below the gate, but new evidence on one already
    // sounding may still escalate it -- it is the same episode. evidenceOK
    // is the same flag arming used above.
    if (evidenceOK) {
      const use = (k) => { const a = !!armed[k]; armed[k] = false; return a; };
      const c3 = use("closed3"), c2 = use("closed2"), p2 = use("perclos2");
      const p1 = use("perclos1"), y1 = use("yawns"), n1 = use("nods");
      // N8: a camera-free Level 1 notice (night or since-stop) is quiet for
      // TAP_QUIET_SECS after "I'm awake" -- new closure or PERCLOS evidence
      // still raises as normal, since it is not gated by this at all.
      const quiet = lastTapT !== null && t - lastTapT < TAP_QUIET_SECS;
      const eligible = (v, k) => v && !quiet && (!has(lastFree[k]) || t - lastFree[k] >= L1.camera_free_every_secs);
      const s1 = eligible(inp.sinceStop >= L1.since_stop_secs, "since-stop");
      const nt = eligible(isNight(inp.hour, cfg), "night");

      let to = 0, why = null, whyKey = null;
      if (c3) { to = 3; why = "closed"; }
      else if (c2 || p2) { to = 2; why = c2 ? "closed" : "perclos"; if (p2 && !c2) whyKey = "perclos2"; }
      else if (p1) { to = 1; why = "perclos"; whyKey = "perclos1"; }
      else if (y1) { to = 1; why = "yawns"; }
      else if (n1) { to = 1; why = "nods"; }
      else if (s1) { to = 1; why = "since-stop"; }
      else if (nt) { to = 1; why = "night"; }
      // repeat-l2 (C1): count only a Level 2 actually raised here, never a
      // re-arm while one is already sounding.
      if (to === 2 && to > level) {
        while (l2Times.length && t - l2Times[0] > L3.level2_window_secs) l2Times.shift();
        l2Times.push(t);
        if (l2Times.length >= L3.level2_count) { to = 3; why = "repeat-l2"; }
      }
      if (to > level) {
        // M3: the hourly camera-free budget is spent only by the trigger
        // that actually raises, not one merely seen while another won.
        if (why === "since-stop") lastFree["since-stop"] = t;
        if (why === "night") lastFree.night = t;
        // N1: only a trigger that actually raised something latches its gate.
        if (whyKey) perclosLatch(perc, whyKey, mt);
        raise(to, why, t, out);
      }
    }

    // I4 (fix round 2): a discard fires on a tap, or a stop of 5 min or
    // more -- never merely on a parked step, which by itself now arms
    // nothing anyway. Round 2's N1 still holds: an armed-but-unused hold is
    // cleared by either. NR1 (fix round 3) draws the line the reset must
    // not cross: a tap uses the weak discard, which never un-latches a gate
    // that actually raised (perclosTapDiscard); only a real 5+ min stop uses
    // the strong one, resetting every gate regardless (perclosReset).
    if (inp.tap) {
      for (const k in armed) armed[k] = false;
      perclosTapDiscard(perc, "perclos1");
      perclosTapDiscard(perc, "perclos2");
    }
    if (inp.stoppedFor >= cfg.stop.still_secs) {
      for (const k in armed) armed[k] = false;
      perclosReset(perc, "perclos1");
      perclosReset(perc, "perclos2");
    }

    const mayRepeat = inp.active || cfg.alert_continues_below_gate !== false;
    // M2: below the gate with alert_continues_below_gate false, Level 3's
    // held alarm actually falls silent (an explicit fade, since nothing else
    // would stop a held sound), and restarts if the alert is still up once
    // the car is active again or the setting allows it.
    if (level === 3 && !out.raised) {
      if (!mayRepeat && !alarmSilenced) {
        out.cues.push({ kind: "fade" });
        alarmSilenced = true;
      } else if (mayRepeat && alarmSilenced) {
        // N6: the restart re-sends duck and voice:l3 too, exactly as raise()
        // does -- Task 7's own fade (crossing back above the gate) already
        // ramps the music back up, so a bare alarm(hold) would return at
        // full volume over un-ducked music, with "Pull over now" not due
        // again for another voice_repeat_secs.
        out.cues.push({ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" });
        nextVoice = t + L3.voice_repeat_secs;
        alarmSilenced = false;
      }
    }
    if (mayRepeat && level === 2 && nextRepeat !== null && t >= nextRepeat && !out.raised) {
      out.cues.push(turn(rotation(), t));
    }
    if (mayRepeat && level === 3 && nextVoice !== null && t >= nextVoice && !out.raised && !alarmSilenced) {
      out.cues.push({ kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
    out.level = level;
    out.trigger = trigger;
    out.banner = banner;
    return out;
  }

  return { step, setConfig, get level() { return level; } };
}
