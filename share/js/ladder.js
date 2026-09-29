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
// builds two Level 2s into a Level 3.
//
// A SOUNDING ALERT AND THE GATE. With alert_continues_below_gate (the
// default), a raised level carries on below 30 mph, repeats and all. Set to
// false, it falls silent below the gate and its card stays.
//
// RELEASE. Every level clears on "I'm awake" or once the car is stationary.
// Levels 1 and 2 also clear when the eyes stay open 5 s after the raise with
// PERCLOS no higher than when they opened: PERCLOS is a 60 s window, so it
// cannot fall within 5 s of a closure, and "not rising" is what can be seen.
// Level 3 has no time cap and does not clear on open eyes: it ends on
// "I'm awake" or a stopped car.

const has = (v) => v !== null && v !== undefined;

// Sensitive lowers every trigger threshold by 20%: the closed-eye margin and
// its cap, PERCLOS, the yawn and nod measures and counts, the closure times,
// the time since a stop, and the Level 2 count, which cannot go below two.
// Windows, the baseline minute, the release time and the hourly limit are not
// thresholds and stay as they are.
export function scaled(cfg, sensitivity = cfg.sensitivity) {
  const c = JSON.parse(JSON.stringify(cfg));
  if (sensitivity !== "sensitive") return c;
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
  return c;
}

export function isNight(hour, cfg) {
  return hour >= cfg.level1.night_from_hour && hour < cfg.level1.night_to_hour;
}

// Time since the last stop: speed 0 for 5 minutes or more counts, and so does
// the car being off that long. A drive starts when the app does.
export function createStopClock(cfg) {
  let still = null, lastStop = null;
  return {
    feed(t, kph, connected) {
      if (lastStop === null) lastStop = t;
      if (!connected || kph === 0) {
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
  // the hourly limits and the armed evidence. Only the thresholds and the
  // rotation change.
  function setConfig(c, sounds = c.sounds) {
    cfg = c;
    L1 = c.level1;
    L2 = c.level2;
    L3 = c.level3;
    rota = sounds && sounds.length ? [...sounds] : ["alarm"];
  }
  setConfig(cfg0, sounds0);

  let level = 0, trigger = null, banner = false;
  let rot = 0, nextRepeat = null, nextVoice = null;
  let openStart = null, perclosAtOpen = null;
  const was = {}, armed = {};
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
    openStart = null;
    out.raised = { level: to, trigger: why };
    if (to === 1) out.cues.push({ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" });
    if (to === 2) { out.cues.push({ kind: "duck" }, rotation()); nextRepeat = t + L2.repeat_secs; }
    if (to === 3) {
      banner = true;
      nextRepeat = null;
      out.cues.push({ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" });
      nextVoice = t + L3.voice_repeat_secs;
    }
  }

  function clear(out) {
    level = 0;
    trigger = null;
    nextRepeat = nextVoice = null;
    openStart = null;
    out.cleared = true;
    out.cues.push({ kind: "fade" });
  }

  function step(inp) {
    const t = inp.t, m = inp.m || {};
    const out = { t, level, trigger, raised: null, cleared: false, cues: [], banner };

    // Release first: "I'm awake", a stationary car, or (Levels 1 and 2)
    // eyes that have stayed open.
    if (level > 0) {
      if (level < 3 && m.face && m.calibrated && !m.closed) {
        if (openStart === null) { openStart = t; perclosAtOpen = has(m.perclos) ? m.perclos : null; }
      } else openStart = null;
      const steady = level < 3 && openStart !== null && t - openStart >= cfg.release.open_secs
        && (!has(m.perclos) || perclosAtOpen === null || m.perclos <= perclosAtOpen);
      if (inp.tap || steady || inp.parked) clear(out);
    }
    if (banner && inp.stoppedFor >= cfg.banner_stopped_secs) banner = false;

    // Evidence, on every step: armed on its rising edge, disarmed when it
    // goes false, used up by a raise.
    const see = (k, v) => {
      if (v && !was[k]) armed[k] = true;
      if (!v) armed[k] = false;
      was[k] = !!v;
    };
    see("closed3", m.closedFor >= L3.closed_secs);
    see("closed2", m.closedFor >= L2.closed_secs);
    see("perclos2", has(m.perclos) && m.perclos >= L2.perclos);
    see("perclos1", has(m.perclos) && m.perclos >= L1.perclos);
    see("yawns", m.yawns >= L1.yawns);
    see("nods", m.nods >= L1.nods);

    if (inp.active) {
      const use = (k) => { const a = !!armed[k]; armed[k] = false; return a; };
      const c3 = use("closed3"), c2 = use("closed2"), p2 = use("perclos2");
      const p1 = use("perclos1"), y1 = use("yawns"), n1 = use("nods");
      const free = (k, v) => {
        if (!v || (has(lastFree[k]) && t - lastFree[k] < L1.camera_free_every_secs)) return false;
        lastFree[k] = t;
        return true;
      };
      const s1 = free("since-stop", inp.sinceStop >= L1.since_stop_secs);
      const nt = free("night", isNight(inp.hour, cfg));

      let to = 0, why = null;
      if (c3) { to = 3; why = "closed"; }
      else if (c2 || p2) { to = 2; why = c2 ? "closed" : "perclos"; }
      else if (p1) { to = 1; why = "perclos"; }
      else if (y1) { to = 1; why = "yawns"; }
      else if (n1) { to = 1; why = "nods"; }
      else if (s1) { to = 1; why = "since-stop"; }
      else if (nt) { to = 1; why = "night"; }
      if (to === 2) {
        while (l2Times.length && t - l2Times[0] > L3.level2_window_secs) l2Times.shift();
        l2Times.push(t);
        if (l2Times.length >= L3.level2_count) { to = 3; why = "repeat-l2"; }
      }
      if (to > level) raise(to, why, t, out);
    }

    const mayRepeat = inp.active || cfg.alert_continues_below_gate !== false;
    if (mayRepeat && level === 2 && nextRepeat !== null && t >= nextRepeat && !out.raised) {
      out.cues.push(rotation());
      nextRepeat = t + L2.repeat_secs;
    }
    if (mayRepeat && level === 3 && nextVoice !== null && t >= nextVoice && !out.raised) {
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
