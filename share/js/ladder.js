// The ladder: three alert levels, raised from the measures (drowsy.js) and two
// signals that need no camera, and released by the driver. Pure, with the
// clock injected: step() is handed the time, and nothing here reads Date.now().
//
//   1 · Notice     PERCLOS >= 15%, 3 yawns or 3 nods in 5 min, 2 h since a
//                  stop, or night hours (once an hour)
//   2 · Wake       eyes closed >= 1.0 s, or PERCLOS >= 25%
//   3 · Pull over  eyes closed >= 2.0 s, or two Level 2 alerts within 5 min
//
// EVIDENCE, NOT SPEED. Every trigger is watched on every step, below the gate
// or above it, and a condition that becomes true is armed until a raise uses
// it. The 30 mph gate decides only whether an armed trigger may raise now.
// So a condition that became true at 25 mph raises once the car passes 30, and
// hovering around 30 mph with the same evidence never raises it again, nor
// builds two Level 2s into a Level 3. A stop (5 min or more) or a tap discards
// all armed-but-unused evidence -- every trigger, including yawns and nods --
// so a doze at a rest stop must not raise the moment the car pulls back onto
// the road (I4). A discontinuity snapshot (m.discontinuity === true) is never
// read as evidence and never advances a release, on its own -- fix round 1,
// C1/I2, after a camera-gap snapshot slipped through as "eyes open".
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
// began. "Continuously" tolerates a blink: closures under 0.5 s do not reset
// the run, only ones of 0.5 s or more do (M7) -- an ordinary blink cadence of
// one every 2-5 s must not make Levels 1 and 2 clear only on the tap. Because
// the measures module resets its own m.openFor on every blink (drowsy.js),
// the ladder tracks the open run itself from m.closed/m.closedFor rather than
// trusting m.openFor. Release also requires a non-null PERCLOS, no
// m.discontinuity on the step, and the step's own t strictly after the last
// one seen: a repeated or stale snapshot is never evidence for a release,
// any more than it is for a raise. Level 3 has no time cap and does not
// clear on open eyes: it ends on "I'm awake" or a stopped car.

const has = (v) => v !== null && v !== undefined;

const BLINK_SECS = 0.5;          // M7: closures shorter than this are a blink,
                                  // not a break in the open-eye release run.
const PERCLOS_HOLD_SECS = 3;     // C1/I1: continuous time at/above threshold
const PERCLOS_MARGIN = 0.03;     // ...to arm, and below (threshold - margin)
const PERCLOS_REARM_SECS = 10;   // ...for this long, to re-arm.

// A PERCLOS trigger arms exactly once, on the step where a continuous run at
// or above threshold reaches PERCLOS_HOLD_SECS (C1/I1). It then stays
// blocked -- a single episode cannot arm twice -- until PERCLOS has read
// below (threshold - PERCLOS_MARGIN) continuously for PERCLOS_REARM_SECS,
// after which it must hold above threshold again before it can arm.
function perclosGate(perc, key, threshold, value, t) {
  const st = perc[key] || (perc[key] = { holdSince: null, belowSince: null, blocked: false });
  const above = has(value) && value >= threshold;
  const belowMargin = has(value) && value < threshold - PERCLOS_MARGIN;
  st.holdSince = above ? (st.holdSince === null ? t : st.holdSince) : null;
  st.belowSince = belowMargin ? (st.belowSince === null ? t : st.belowSince) : null;
  if (st.blocked && st.belowSince !== null && t - st.belowSince >= PERCLOS_REARM_SECS) st.blocked = false;
  if (!st.blocked && above && st.holdSince !== null && t - st.holdSince >= PERCLOS_HOLD_SECS) {
    st.blocked = true;
    st.holdSince = null;
    return true;
  }
  return false;
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

export function createLadder(cfg0, sounds0 = cfg0.sounds) {
  let cfg, L1, L2, L3, rota;
  // New settings keep the ladder's memory: its level, the Level 2 history,
  // the hourly limits and the armed evidence. Only the thresholds change,
  // and the rotation only when sounds is actually passed (M5): setConfig(c)
  // alone must keep the current list rather than falling back to c.sounds
  // and re-adding "voice" after a caller filtered it out for missing clips.
  function setConfig(c, sounds) {
    cfg = c;
    L1 = c.level1;
    L2 = c.level2;
    L3 = c.level3;
    if (sounds !== undefined) rota = sounds && sounds.length ? [...sounds] : ["alarm"];
  }
  setConfig(cfg0, sounds0);

  let level = 0, trigger = null, banner = false;
  let rot = 0, nextRepeat = null, nextVoice = null, alarmSilenced = false;
  let openStreakStart = null, perclosAtOpenStreak = null;
  let lastStepT = null;
  const was = {}, armed = {};
  const perc = {};               // PERCLOS hysteresis state, per trigger key
  const l2Times = [];
  const lastFree = {};

  function rotation() {
    const s = rota[rot++ % rota.length];
    if (s === "voice") return { kind: "voice", clip: "l2" };
    if (s === "alarm") return { kind: "alarm" };
    return { kind: "bark" };
  }

  function raise(to, why, t, out) {
    level = to;
    trigger = why;
    openStreakStart = null;
    out.raised = { level: to, trigger: why };
    if (to === 1) out.cues.push({ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" });
    if (to === 2) {
      rot = 0; // M1: each new Level 2 episode opens on the driver's first choice.
      out.cues.push({ kind: "duck" }, rotation());
      nextRepeat = t + L2.repeat_secs;
    }
    if (to === 3) {
      banner = true;
      nextRepeat = null;
      alarmSilenced = false;
      out.cues.push({ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
  }

  function clear(out) {
    level = 0;
    trigger = null;
    nextRepeat = nextVoice = null;
    openStreakStart = null;
    alarmSilenced = false;
    out.cleared = true;
    out.cues.push({ kind: "fade" });
  }

  function step(inp) {
    const t = inp.t, m = inp.m || {};
    const out = { t, level, trigger, raised: null, cleared: false, cues: [], banner };

    // I2's last clause, scoped to release: t strictly after the last step, or
    // a repeated/stale call is not evidence that the eyes have stayed open.
    const advanced = lastStepT === null || t > lastStepT;
    lastStepT = t;

    // Release first: "I'm awake", a stationary car, or (Levels 1 and 2) eyes
    // that have stayed open. A blink under BLINK_SECS does not break the open
    // run (M7); m.discontinuity always does (I2) -- a camera gap is never
    // credited as time the driver's eyes were seen open.
    if (level > 0) {
      if (level < 3) {
        if (m.discontinuity || !m.face || !m.calibrated) {
          openStreakStart = null;
          perclosAtOpenStreak = null;
        } else if (m.closed) {
          if (m.closedFor >= BLINK_SECS) { openStreakStart = null; perclosAtOpenStreak = null; }
        } else if (openStreakStart === null) {
          openStreakStart = t;
          perclosAtOpenStreak = has(m.perclos) ? m.perclos : null;
        }
      }
      const steady = level < 3 && !m.discontinuity && advanced && openStreakStart !== null
        && t - openStreakStart >= cfg.release.open_secs
        && has(m.perclos) && perclosAtOpenStreak !== null && m.perclos <= perclosAtOpenStreak;
      if (inp.tap || steady || inp.parked) clear(out);
    }
    if (banner && inp.stoppedFor >= cfg.banner_stopped_secs) banner = false;

    // Evidence, on every step: armed on its rising edge, disarmed when it
    // goes false, used up by a raise. A discontinuity snapshot never counts
    // as evidence on its own (I2) -- skipped outright, not merely trusted to
    // read as "false" because Task 5 already reset its numbers.
    if (!m.discontinuity) {
      const see = (k, v) => {
        if (v && !was[k]) armed[k] = true;
        if (!v) armed[k] = false;
        was[k] = !!v;
      };
      see("closed3", m.closedFor >= L3.closed_secs);
      see("closed2", m.closedFor >= L2.closed_secs);
      see("yawns", m.yawns >= L1.yawns);
      see("nods", m.nods >= L1.nods);
      // PERCLOS triggers use their own hold/re-arm hysteresis (C1/I1),
      // not a plain rising edge.
      if (perclosGate(perc, "perclos2", L2.perclos, m.perclos, t)) armed.perclos2 = true;
      if (perclosGate(perc, "perclos1", L1.perclos, m.perclos, t)) armed.perclos1 = true;
    }

    // M8: no NEW alert starts below the gate, but new evidence on one already
    // sounding may still escalate it -- it is the same episode. So evidence
    // is consumed here whenever the car is active, or an alert is already up.
    if (inp.active || level > 0) {
      const use = (k) => { const a = !!armed[k]; armed[k] = false; return a; };
      const c3 = use("closed3"), c2 = use("closed2"), p2 = use("perclos2");
      const p1 = use("perclos1"), y1 = use("yawns"), n1 = use("nods");
      const eligible = (v, k) => v && (!has(lastFree[k]) || t - lastFree[k] >= L1.camera_free_every_secs);
      const s1 = eligible(inp.sinceStop >= L1.since_stop_secs, "since-stop");
      const nt = eligible(isNight(inp.hour, cfg), "night");

      let to = 0, why = null;
      if (c3) { to = 3; why = "closed"; }
      else if (c2 || p2) { to = 2; why = c2 ? "closed" : "perclos"; }
      else if (p1) { to = 1; why = "perclos"; }
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
        raise(to, why, t, out);
      }
    }

    // I4: a stop or a tap discards any evidence still armed and unused. This
    // runs after arming and after a possible raise, on purpose: evidence
    // that completes its hold WHILE parked (a doze at a rest stop) must not
    // survive, unconsumed, into the drive that follows -- only evidence a
    // step actually used to raise something is safe from this.
    if (inp.tap || inp.parked) for (const k in armed) armed[k] = false;

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
        out.cues.push({ kind: "alarm", hold: true });
        nextVoice = t + L3.voice_repeat_secs;
        alarmSilenced = false;
      }
    }
    if (mayRepeat && level === 2 && nextRepeat !== null && t >= nextRepeat && !out.raised) {
      out.cues.push(rotation());
      nextRepeat = t + L2.repeat_secs;
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
