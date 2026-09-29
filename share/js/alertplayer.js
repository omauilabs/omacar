// Every alert OmaCar sounds, and Begin's chime: the ladder's cues (ladder.js)
// turned into sounds (sounds.js), each with its own level envelope, on the
// audio stage (audiobus.js).
//
// EVERY ONSET RAMPS, AT THE SOUND'S OWN GAIN. Every chime note, bark call,
// voice line and alarm -- the first of a drive, every repeat and every
// escalation from Level 1 to 2 to 3 -- rises from silence (-48 dBFS) to its
// level and falls back, never more than 3 dB in any 100 ms (envelopePlan). The
// alert bus is only a gate: opened when a sound starts at silence, closed once
// the last one has faded to silence. So after "I'm awake", the next onset
// starts from silence too. Nothing is ever set in one step.
//
// THE LADDER SENDS NO CANCEL CUES, so the handovers are kept here:
//   - A sound of a higher level lets every lower one go as it rises: Level
//     1's chime and voice when Level 2 starts, Level 2's sound when Level 3's
//     alarm starts. Each falls to silence over 3 s from wherever it is
//     (releaseVoices), so two alerts never add up to a jump.
//   - Every music plan starts from the level the bus really has at that
//     moment (audiobus.js levelAt): a Level 1 swell still rising, or still
//     settling, is cut where it stands and ducked from there. Never stepped.
//   - A Level 2 rotation sound is over, rise to silence, inside its turn
//     (ladder.js slotsOf: 5 s, and 7 s for the voice), so one never sounds
//     over the next.
//   - The held Level 3 alarm steps aside under each "Pull over now", and
//     every plan's peaks add up under the limiter (see VOICE_DB).
//   - "fade" lets every sound go, the held Level 3 alarm included, and brings
//     the music back.
//   - Every sound still live, a held alarm included, is counted in
//     gateCloseAt, so the gate never closes under a sound.
//
// The schedule is pure (voicesFor, releaseVoices, gateCloseAt) and tested;
// createAlertPlayer only hands it to a stage, which is Web Audio unless a
// test gives it another.

import { audioContext, schedule, levelAt, resume, gateAlerts, MUSIC_DB, FLOOR_DB } from "./audiobus.js";
import {
  rampPlan, envelopePlan, releasePlan, releaseAt, glidePlan, dbAt, RISE_SECS, RELEASE_SECS, STEP_SECS,
} from "./ramps.js";
import { CHIME, BARK, playChimeNote, playBark, playAlarm, playVoice, loadClip } from "./sounds.js";
import { slotsOf } from "./ladder.js";
import { asset } from "./assets.js";

// Where each level's sound peaks, in dBFS. Level 3 uses the full scale.
export const ALERT_DB = { 1: -6, 2: -3, 3: 0 };
// THE HEADROOM IS SHARED (fix round 1). Every heard peak -- each sound's
// envelope times its content's peak (sounds.js PEAK), plus the music -- adds
// up to no more than the limiter's -1 dBFS in any plan, so the limiter never
// pumps a level in normal use. Three levels move for that:
//   - each chime note is -12, not -9: two notes overlapping at -9 over the
//     rising radio peaked at -0.2 dBFS. At -12 the pair's peaks add to -6.3.
//   - the voice speaks at VOICE_DB, not ALERT_DB: -3 at Level 3 (the
//     controller's ruling), -3 at Level 2, and -7 at Level 1, where the radio
//     has risen to -6 dBFS beneath it.
//   - the held Level 3 alarm steps 9 dB aside under each line (dipPlan).
export const CHIME_NOTE_DB = -12;
export const VOICE_DB = { 1: -7, 2: -3, 3: -3 };
// How long a sound would hold its level between its rise and its fall. A
// Level 2 sound holds only as long as its turn allows (turnHold): 0.4 s in the
// default 5 s turn, since 1.5 s up and 3 s down leave 0.5 s, less 0.1 s spare.
export const HOLD_SECS = { chime: 1.0, bark: 1.5, alarm: 1.5 };
export const VOICE_GAP = 0.4;
// When the voice starts after its cue: after Level 1's chime, and once Level
// 3's alarm has begun. Level 2's voice is a rotation of its own.
export const VOICE_AFTER = { 1: 2.5, 3: 1.0 };
export const SWELL_DB = 6;             // Level 1: the radio rises +6 dB
export const DUCK_DB = -12;            // Levels 2 and 3: music ducks -12 dB
// Every Level 2 sound is silent again this long before its turn ends
// (ladder.js slotsOf), so the next one never starts over it.
export const SLOT_SPARE_SECS = 0.1;
// A Level 2 line starts only once its envelope is within this much of its
// level, so the name at its start is heard at full level (fix round 1). The
// envelope still rises from silence; the line itself begins at -6 dBFS, as a
// voice does, rather than rising out of nothing under the rise.
export const LINE_WITHIN_DB = 3;
// The held Level 3 alarm steps aside under each line: 9 dB down, reached
// DIP_SECS after it starts moving, which is DIP_LEAD_SECS before the line;
// and back up over RETURN_SECS once the line is over.
export const ALARM_DIP_DB = 9;
export const DIP_LEAD_SECS = 0.5;
export const DIP_SECS = 0.5;
export const RETURN_SECS = 1.0;
// Cues go on the context's clock this far ahead, so no first point is already
// in the past when the audio thread reads it.
export const LEAD_SECS = 0.05;
// A line whose clip took longer than this to load is stale, and is not said.
const STALE_SECS = 1;

// How many times a line is said: enough that one saying falls wholly after the
// rise, at full level. Start offsets from the voice's start.
export function voicePlays(clipSecs, riseSecs, gap = VOICE_GAP) {
  const n = 1 + Math.ceil(riseSecs / (clipSecs + gap));
  return Array.from({ length: n }, (_, i) => +(i * (clipSecs + gap)).toFixed(3));
}

// Pure: the voices a cue sounds, each with its envelope in absolute time.
// `slots` are Level 2's turns (ladder.js slotsOf); every Level 2 sound is
// silent SLOT_SPARE_SECS before its turn ends.
export function voicesFor(cue, level, at, clipSecs = 2, slots = slotsOf()) {
  const lv = Math.min(3, Math.max(1, level || 1));
  const voice = (kind, start, env, extra) => Object.assign({
    kind, level: lv, start, rise: env.rise, end: start + env.secs,
    env: env.points.map(([t, d]) => [start + t, d]),
  }, extra || {});
  // A Level 2 sound holds only as long as its turn allows.
  const turnHold = (want, slot) =>
    (lv === 2 ? Math.max(0, Math.min(want, slot - SLOT_SPARE_SECS - RISE_SECS[2] - RELEASE_SECS)) : want);
  if (cue.kind === "chime") {
    return CHIME.notes.map((n) => voice("chime", at + n.at, envelopePlan(1, CHIME_NOTE_DB, HOLD_SECS.chime), { hz: n.hz }));
  }
  if (cue.kind === "bark") {
    const h = turnHold(HOLD_SECS.bark, slots.repeat);
    const env = envelopePlan(lv, ALERT_DB[lv], h);
    // Calls all through the rise and the hold: three in a 5 s turn.
    return [voice("bark", at, env, { calls: Math.max(1, Math.ceil((env.rise + h) / BARK.callEvery)) })];
  }
  if (cue.kind === "alarm") {
    return [voice("alarm", at, envelopePlan(lv, ALERT_DB[lv], cue.hold ? Infinity : turnHold(HOLD_SECS.alarm, slots.repeat)))];
  }
  if (cue.kind === "voice") {
    const target = VOICE_DB[lv];
    const rise = envelopePlan(lv, target, 0);
    if (lv === 2) {
      // Level 2: said once, from the moment its envelope is within 3 dB of
      // its level, and held until the line is over. A line too long to be
      // heard whole in the voice's turn is not said: the alarm takes the turn.
      const lineAt = rise.points.find(([, d]) => d >= target - LINE_WITHIN_DB)[0];
      const env = envelopePlan(2, target, Math.max(0, lineAt + clipSecs - rise.rise));
      if (env.secs > slots.voice - SLOT_SPARE_SECS) return voicesFor({ kind: "alarm" }, 2, at, clipSecs, slots);
      return [voice("voice", at, env, { plays: [lineAt], clip: cue.clip, line: [at + lineAt, at + lineAt + clipSecs] })];
    }
    // Levels 1 and 3: said again until one saying falls wholly after the rise.
    const plays = voicePlays(clipSecs, rise.rise);
    const env = envelopePlan(lv, target, Math.max(0, plays[plays.length - 1] + clipSecs - rise.rise));
    return [voice("voice", at, env, { plays, clip: cue.clip, line: [at, at + plays[plays.length - 1] + clipSecs] })];
  }
  return [];
}

// Pure: sounds cut so that each is silent by `deadline`: one that would run
// past it lets go early enough to reach silence by then, and one that could
// not even start before that is not played at all. A Level 2 line that
// loaded late must still be over when its turn is.
export function fitTurn(vs, deadline) {
  const out = [];
  for (const v of vs) {
    if (v.end <= deadline) { out.push(v); continue; }
    // releaseAt may move the cut up to one step later, so it is one step earlier.
    const [r] = releaseVoices([v], deadline - RELEASE_SECS - STEP_SECS);
    if (r && !r.dropped) out.push(Object.assign(r, { released: undefined }));
  }
  return out;
}

// Pure: the held Level 3 alarm `v`, stepping ALARM_DIP_DB aside from `from`
// (DIP_LEAD_SECS before a line) until `until` (the line's end), then back.
// Still rising, it simply stops rising there. Returns the re-planned voice and
// the moment its new plan takes over (releaseAt: never a step, on any browser).
export function dipPlan(v, from, until) {
  const at = releaseAt(v.env, from);
  const level = dbAt(v.env, at);
  const full = v.env[v.env.length - 1][1];
  const low = full - ALARM_DIP_DB;
  let down;
  if (level > low) {
    // Down at the dip's own pace.
    down = glidePlan(level, low, (DIP_SECS * (level - low)) / ALARM_DIP_DB).points.map(([t, d]) => [at + t, d]);
  } else {
    // Still rising: on along its own rise, point for point, and no further.
    const riseRate = (full - v.env[0][1]) / RISE_SECS[3];
    const rest = v.env.filter(([t, d]) => t > at && d < low);
    const [lt, ld] = rest.length ? rest[rest.length - 1] : [at, level];
    down = [[at, level], ...rest, [lt + (low - ld) / riseRate, low]];
  }
  const held = Math.max(until, down[down.length - 1][0]);
  const back = glidePlan(low, full, RETURN_SECS);
  const env = [...v.env.filter(([t]) => t < at), ...down, ...back.points.map(([t, d]) => [held + t, d])];
  return { voice: Object.assign({}, v, { env }), at };
}

// Pure: every voice still sounding at `now`, let go from wherever its envelope
// is, down to silence over 3 s. A voice already on its way down keeps its own
// fall. A voice not yet started is dropped, never heard. A held alarm has no
// end until this. The release starts at releaseAt(): `now`, or the
// envelope's next point where it is moving, so it cannot step on any browser.
export function releaseVoices(voices, now) {
  const out = [];
  for (const v of voices) {
    if (v.end <= now) continue;
    if (v.start >= now) {
      out.push(Object.assign({}, v, { env: [[now, v.env[0][1]]], end: now, released: now, dropped: true }));
      continue;
    }
    if (Number.isFinite(v.end) && now >= v.end - RELEASE_SECS) { out.push(v); continue; }
    const at = releaseAt(v.env, now);
    const r = releasePlan(dbAt(v.env, at));
    out.push(Object.assign({}, v, {
      env: [...v.env.filter(([t]) => t < at), ...r.points.map(([t, d]) => [at + t, d])],
      end: at + r.secs,
      released: at,
    }));
  }
  return out;
}

// Pure: when the alert gate may close, once the last voice has faded to
// silence. Infinity while a held alarm has not been let go.
export function gateCloseAt(voices) {
  return voices.reduce((m, v) => Math.max(m, v.end), 0);
}

// The Level 2 rotation the ladder may use: "voice" only when its clip is
// installed (Task 12 is deferrable), so a missing voice is never a silent
// slot in the rotation. Drowsy mode passes this to the ladder's setConfig.
export function rotationFor(sounds, installed) {
  const r = (sounds || []).filter((s) => s !== "voice" || installed);
  return r.length ? r : ["alarm"];
}

// The asset names a line is looked for under, in order. Only an owner named
// James hears his name; a missing named clip falls back to the plain one.
export function clipNames(clip, name) {
  return clip !== "l3" && name === "James" ? [`voice-${clip}-james`, `voice-${clip}`] : [`voice-${clip}`];
}

// Whether Level 2's line is installed for this name (Task 12).
export async function voiceInstalled(name = "James") {
  for (const n of clipNames("l2", name)) {
    const a = await asset(n).catch(() => null);
    if (a && a.url) return true;
  }
  return false;
}

// The real stage: Web Audio, the page's music bus and alert gate. A test
// hands createAlertPlayer a stage of its own with these same parts.
function liveStage() {
  return {
    now: () => audioContext().currentTime,
    running: () => audioContext().state === "running",
    resume: () => resume(),
    // Resolves once the context runs: at once if it already does.
    whenRunning() {
      const ctx = audioContext();
      return new Promise((done) => {
        if (ctx.state === "running") { done(); return; }
        const on = () => { if (ctx.state === "running") { ctx.removeEventListener("statechange", on); done(); } };
        ctx.addEventListener("statechange", on);
      });
    },
    music: (points, at, append) => schedule("music", points, at, append),
    musicAt: (t) => levelAt("music", t),
    gate: (openAt, closeAt) => gateAlerts(openAt, closeAt),
    render(v, buffer) {
      if (v.kind === "chime") return playChimeNote(v);
      if (v.kind === "bark") return playBark(v);
      if (v.kind === "alarm") return playAlarm(v);
      return playVoice(v, buffer);
    },
    async clip(assetName) {
      const a = await asset(assetName).catch(() => null);
      if (!a || !a.url) return null;
      try { return await loadClip(a.url); } catch { return null; }
    },
  };
}

// THE STAGE IS REQUIRED. There is one alert gate on the page, and two players
// on it would each schedule its closing under the other's sounds: the page's
// one player is alertPlayer(). A test passes a stage with no sound in it.
export function createAlertPlayer(stage) {
  if (!stage) throw new Error("createAlertPlayer needs a stage; the page's one player is alertPlayer()");
  let voices = [];                 // what is sounding, or due to, as planned now
  const handles = new Map();       // voice id -> sounds.js's handle on it
  let nextId = 0;
  let epoch = 0;                   // moves on every fade
  let peak = 0;                    // the highest level sounded since the last fade
  let name = () => "James";
  let slots = slotsOf();           // Level 2's turns, from the settings
  let held = null;                 // while the context is not running: the latest cue state
  let waiting = false;

  function prune(now) {
    voices = voices.filter((v) => v.end > now);
    for (const id of [...handles.keys()]) if (!voices.some((v) => v.id === id)) handles.delete(id);
  }

  // Let `which` go at `at`: each falls to silence from wherever it is, one
  // already falling keeps its own fall, and one not yet started is never heard.
  // Returns the latest moment one of them turned to fall (-Infinity if none
  // had to).
  function letGo(which, at) {
    const out = new Map(releaseVoices(which, at).map((r) => [r.id, r]));
    let turned = -Infinity;
    for (const v of which) {
      const r = out.get(v.id);
      if (!r || r === v || r.dropped) continue;   // over, already falling on its own, or never started
      const h = handles.get(r.id);
      if (h) h.release(r.released, r.env.filter(([t]) => t >= r.released), r.end);
      turned = Math.max(turned, r.released);
    }
    for (const r of out.values()) {
      if (!r.dropped) continue;
      const h = handles.get(r.id);
      if (h) h.release(r.released, r.env, r.end);
    }
    const gone = new Set(which.map((v) => v.id));
    voices = voices.flatMap((v) => {
      if (!gone.has(v.id)) return [v];
      const r = out.get(v.id);
      return r && !r.dropped ? [r] : [];
    });
    return turned;
  }

  // The same sounds, dt later.
  function later(vs, dt) {
    const at = (t) => t + dt;
    return vs.map((v) => Object.assign({}, v, {
      start: at(v.start), end: at(v.end), env: v.env.map(([t, d]) => [at(t), d]),
    }, v.line ? { line: v.line.map(at) } : {}));
  }

  function sound(vs, buffer) {
    if (!vs.length) return;
    let start = Math.min(...vs.map((v) => v.start));
    const level = Math.max(...vs.map((v) => v.level));
    prune(stage.now());
    // A higher level takes over: every lower sound lets go, and the new one
    // enters one step after the last of them has turned to fall. Entering at
    // -48 dBFS while a lower sound still rises near -30 would move the sum by
    // more than 3 dB in that 100 ms (fix round 1).
    const lower = voices.filter((v) => v.level < level);
    if (lower.length) {
      const turned = letGo(lower, start);
      if (turned > -Infinity && turned + STEP_SECS > start) {
        vs = later(vs, turned + STEP_SECS - start);
        start = turned + STEP_SECS;
      }
    }
    for (const v of vs) { v.id = ++nextId; voices.push(v); }
    stage.gate(start, gateCloseAt(voices));
    for (const v of vs) {
      try { handles.set(v.id, stage.render(v, buffer)); } catch (e) { console.warn("alert sound:", e); }
    }
    peak = Math.max(peak, level);
  }

  // Where the music is at `at`, as a plan must start from it.
  function musicFrom(at) {
    const db = stage.musicAt(at);
    return Math.max(FLOOR_DB, Number.isFinite(db) ? db : FLOOR_DB);
  }

  function swell(at) {
    const up = rampPlan(1, musicFrom(at), MUSIC_DB + SWELL_DB);
    if (stage.music(up.points, at)) stage.music(rampPlan(1, MUSIC_DB + SWELL_DB, MUSIC_DB).points, at + up.secs, true);
  }

  function duck(at, level) {
    stage.music(rampPlan(Math.min(3, Math.max(2, level)), musicFrom(at), MUSIC_DB + DUCK_DB).points, at);
  }

  function fade(at) {
    epoch++;
    peak = 0;
    prune(stage.now());
    if (voices.length) {
      letGo(voices.slice(), at);
      // The gate closes once the last of them is silent. With none left
      // sounding it closes now, and an opening scheduled for a sound that has
      // just been dropped goes with it.
      stage.gate(at, Math.max(at, gateCloseAt(voices)));
    }
    stage.music(rampPlan(0, musicFrom(at), MUSIC_DB).points, at);
  }

  async function clipFor(clip) {
    let who = "";
    try { who = name() || ""; } catch { /* no settings yet: no name */ }
    for (const n of clipNames(clip, who)) {
      const b = await stage.clip(n);
      if (b) return b;
    }
    return null;
  }

  // The held Level 3 alarm steps aside under a line: each live one is
  // re-planned from DIP_LEAD_SECS before the line until the line is over.
  function stepAside(line) {
    for (const v of voices.slice()) {
      if (v.kind !== "alarm" || v.level !== 3 || Number.isFinite(v.end)) continue;
      const r = dipPlan(v, line[0] - DIP_LEAD_SECS, line[1]);
      const h = handles.get(v.id);
      // A re-plan that ends nothing: the alarm is still held.
      if (h) h.release(r.at, r.voice.env.filter(([t]) => t >= r.at), Infinity);
      voices = voices.map((x) => (x.id === v.id ? r.voice : x));
    }
  }

  // A line is said once its clip is loaded, unless "I'm awake" or a higher
  // level came meanwhile, or it would now start too late to mean anything.
  // A Level 2 line, or the alarm standing in for it, is still over by the end
  // of the voice's turn, however late it starts.
  async function say(cue, level, at) {
    const ep = epoch;
    let buffer = null;
    try { buffer = await clipFor(cue.clip); } catch { buffer = null; }
    const lv = Math.min(3, Math.max(1, level || 1));
    if (ep !== epoch || peak > lv) return;
    const now = stage.now();
    if (now + LEAD_SECS > at + STALE_SECS) return;
    // At Level 3 the alarm needs DIP_LEAD_SECS to step aside first.
    const start = Math.max(at, now + LEAD_SECS + (lv === 3 ? DIP_LEAD_SECS : 0));
    let vs = [];
    if (buffer) vs = voicesFor(cue, lv, start, buffer.duration, slots);
    // No clip at all: Level 2's rotation keeps its turn with the alarm
    // rather than going quiet. Levels 1 and 3 have their other sounds.
    else if (lv === 2) vs = voicesFor({ kind: "alarm" }, 2, start, undefined, slots);
    if (lv === 2) vs = fitTurn(vs, at + slots.voice - SLOT_SPARE_SECS);
    const line = vs.length && vs[0].kind === "voice" ? vs[0].line : null;
    if (lv === 3 && line) stepAside(line);
    sound(vs, buffer);
  }

  function now(cues, out) {
    const at = stage.now() + LEAD_SECS;
    const level = Math.min(3, Math.max(0, (out && out.level) || 0));
    const later = [];
    for (const cue of cues || []) {
      if (cue.kind === "fade") fade(at);
      else if (cue.kind === "swell") swell(at);
      else if (cue.kind === "duck") duck(at, level);
      else if (cue.kind === "voice") later.push(say(cue, level, at + (VOICE_AFTER[level] || 0)));
      else sound(voicesFor(cue, level, at, undefined, slots));
    }
    return Promise.all(later).then(() => {});
  }

  // WHILE THE CONTEXT IS NOT RUNNING its clock stands still, and every cue
  // scheduled against it would start together once it runs: six Level 2
  // repeats rising at once, far over Level 2. So only the latest state is
  // kept -- the music's last move, a held alarm, the last sounds and the
  // level -- and it is played once, from where things are, when the context
  // runs. "I'm awake" empties it, and still lets go of anything sounding.
  function hold(cues, out) {
    for (const cue of cues || []) {
      if (cue.kind === "fade") { held = null; fade(stage.now() + LEAD_SECS); continue; }
      held = held || { music: null, alarm: null, sounds: [], level: 0 };
      if (cue.kind === "swell" || cue.kind === "duck") held.music = cue;
      else if (cue.kind === "alarm" && cue.hold) held.alarm = cue;
    }
    const sounds = (cues || []).filter((c) => !["fade", "swell", "duck"].includes(c.kind) && !(c.kind === "alarm" && c.hold));
    if (held && sounds.length) held.sounds = sounds;
    if (held) held.level = Math.min(3, Math.max(0, (out && out.level) || 0));
  }

  function play(cues, out) {
    Promise.resolve().then(() => stage.resume()).catch(() => {});
    if (stage.running()) return now(cues, out);
    hold(cues, out);
    if (!held || waiting) return Promise.resolve();
    waiting = true;
    return Promise.resolve(stage.whenRunning()).then(() => {
      waiting = false;
      const h = held;
      held = null;
      if (!h) return undefined;
      return now([h.music, h.alarm, ...h.sounds].filter(Boolean), { level: h.level });
    });
  }

  return {
    setName(fn) { name = fn; },
    // Drowsy mode's settings: Level 2's turns (ladder.js slotsOf), the same
    // ones its ladder keeps.
    setConfig(cfg) { slots = slotsOf(cfg); },
    get voices() { return voices; },
    play,
  };
}

// One player for the page, so Begin's chime, drowsy mode and "Test the
// alerts" share one gate and one list of what is sounding.
let the = null;
export function alertPlayer() { return the || (the = createAlertPlayer(liveStage())); }

// Begin's chime: the same envelopes as any alert, from silence and back.
export function beginChime() {
  return alertPlayer().play([{ kind: "chime" }], { level: 1 });
}
