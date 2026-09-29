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
//   - A Level 2 rotation sound is over, rise to silence, inside the ladder's
//     5 s repeat (ROTATION_SECS), so one repeat never sounds over the last.
//   - "fade" lets every sound go, the held Level 3 alarm included, and brings
//     the music back.
//   - Every sound still live, a held alarm included, is counted in
//     gateCloseAt, so the gate never closes under a sound.
//
// The schedule is pure (voicesFor, releaseVoices, gateCloseAt) and tested;
// createAlertPlayer only hands it to a stage, which is Web Audio unless a
// test gives it another.

import { audioContext, schedule, levelAt, resume, gateAlerts, MUSIC_DB, FLOOR_DB } from "./audiobus.js";
import { rampPlan, envelopePlan, releasePlan, releaseAt, dbAt, RISE_SECS, RELEASE_SECS } from "./ramps.js";
import { CHIME, BARK, playChimeNote, playBark, playAlarm, playVoice, loadClip } from "./sounds.js";
import { asset } from "./assets.js";

// Where each level's sound peaks, in dBFS. Level 3 uses the full scale.
export const ALERT_DB = { 1: -6, 2: -3, 3: 0 };
export const CHIME_NOTE_DB = -9;       // each of the chime's two notes; together about -6
// How long a sound holds its level between its rise and its fall. Level 2's
// bark and alarm hold 0.4 s: 1.5 s up, 0.4 s held and 3 s down is 4.9 s.
export const HOLD_SECS = { chime: 1.0, bark: 0.4, alarm: 0.4 };
export const VOICE_GAP = 0.4;
// When the voice starts after its cue: after Level 1's chime, and once Level
// 3's alarm has begun. Level 2's voice is a rotation of its own.
export const VOICE_AFTER = { 1: 2.5, 3: 1.0 };
export const SWELL_DB = 6;             // Level 1: the radio rises +6 dB
export const DUCK_DB = -12;            // Levels 2 and 3: music ducks -12 dB
// The longest a Level 2 rotation sound runs, from its first sample to
// silence: inside drowsy.json's level2.repeat_secs (5 s), with 0.1 s spare
// for the two clocks, so a repeat never starts over the one before it.
export const ROTATION_SECS = 4.9;
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
export function voicesFor(cue, level, at, clipSecs = 2) {
  const lv = Math.min(3, Math.max(1, level || 1));
  const voice = (kind, start, env, extra) => Object.assign({
    kind, level: lv, start, rise: env.rise, end: start + env.secs,
    env: env.points.map(([t, d]) => [start + t, d]),
  }, extra || {});
  // Level 2's sounds are its rotation: each holds only as long as still fits.
  const hold = (want) => (lv === 2 ? Math.min(want, ROTATION_SECS - RISE_SECS[2] - RELEASE_SECS) : want);
  if (cue.kind === "chime") {
    return CHIME.notes.map((n) => voice("chime", at + n.at, envelopePlan(1, CHIME_NOTE_DB, HOLD_SECS.chime), { hz: n.hz }));
  }
  if (cue.kind === "bark") {
    const h = hold(HOLD_SECS.bark);
    const env = envelopePlan(lv, ALERT_DB[lv], h);
    // Calls all through the rise and the hold: three at Level 2.
    return [voice("bark", at, env, { calls: Math.max(1, Math.ceil((env.rise + h) / BARK.callEvery)) })];
  }
  if (cue.kind === "alarm") {
    return [voice("alarm", at, envelopePlan(lv, ALERT_DB[lv], cue.hold ? Infinity : hold(HOLD_SECS.alarm)))];
  }
  if (cue.kind === "voice") {
    const rise = envelopePlan(lv, ALERT_DB[lv], 0).rise;
    const all = voicePlays(clipSecs, rise);
    const h = hold(Math.max(0, all[all.length - 1] + clipSecs - rise));
    // At Level 2 the rotation's 5 s leaves no room for a second saying: one
    // that would begin in the fall is not said at all.
    const plays = all.filter((p) => p < rise + h);
    return [voice("voice", at, envelopePlan(lv, ALERT_DB[lv], h), { plays, clip: cue.clip })];
  }
  return [];
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
    resume: () => resume(),
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

export function createAlertPlayer(stage = liveStage()) {
  let voices = [];                 // what is sounding, or due to, as planned now
  const handles = new Map();       // voice id -> sounds.js's handle on it
  let nextId = 0;
  let epoch = 0;                   // moves on every fade
  let peak = 0;                    // the highest level sounded since the last fade
  let name = () => "James";

  function prune(now) {
    voices = voices.filter((v) => v.end > now);
    for (const id of [...handles.keys()]) if (!voices.some((v) => v.id === id)) handles.delete(id);
  }

  // Let `which` go at `at`: each falls to silence from wherever it is, one
  // already falling keeps its own fall, and one not yet started is never heard.
  function letGo(which, at) {
    const out = new Map(releaseVoices(which, at).map((r) => [r.id, r]));
    for (const v of which) {
      const r = out.get(v.id);
      if (!r || r === v) continue;           // over by then, or already falling on its own
      const h = handles.get(r.id);
      if (h) h.release(r.released, r.env.filter(([t]) => t >= r.released), r.end);
    }
    const gone = new Set(which.map((v) => v.id));
    voices = voices.flatMap((v) => {
      if (!gone.has(v.id)) return [v];
      const r = out.get(v.id);
      return r && !r.dropped ? [r] : [];
    });
  }

  function sound(vs, buffer) {
    if (!vs.length) return;
    const start = Math.min(...vs.map((v) => v.start));
    const level = Math.max(...vs.map((v) => v.level));
    prune(stage.now());
    // A higher level takes over: every lower sound lets go as this one rises.
    const lower = voices.filter((v) => v.level < level);
    if (lower.length) letGo(lower, start);
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

  // A line is said once its clip is loaded, unless "I'm awake" or a higher
  // level came meanwhile, or it would now start too late to mean anything.
  async function say(cue, level, at) {
    const ep = epoch;
    let buffer = null;
    try { buffer = await clipFor(cue.clip); } catch { buffer = null; }
    const lv = Math.min(3, Math.max(1, level || 1));
    if (ep !== epoch || peak > lv) return;
    const now = stage.now();
    if (now + LEAD_SECS > at + STALE_SECS) return;
    const start = Math.max(at, now + LEAD_SECS);
    if (buffer) sound(voicesFor(cue, lv, start, buffer.duration), buffer);
    // No clip at all: Level 2's rotation keeps its slot with the alarm
    // rather than going quiet for 5 s. Levels 1 and 3 have their other sounds.
    else if (lv === 2) sound(voicesFor({ kind: "alarm" }, 2, start));
  }

  function play(cues, out) {
    Promise.resolve().then(() => stage.resume()).catch(() => {});
    const at = stage.now() + LEAD_SECS;
    const level = Math.min(3, Math.max(0, (out && out.level) || 0));
    const later = [];
    for (const cue of cues || []) {
      if (cue.kind === "fade") fade(at);
      else if (cue.kind === "swell") swell(at);
      else if (cue.kind === "duck") duck(at, level);
      else if (cue.kind === "voice") later.push(say(cue, level, at + (VOICE_AFTER[level] || 0)));
      else sound(voicesFor(cue, level, at));
    }
    return Promise.all(later).then(() => {});
  }

  return {
    setName(fn) { name = fn; },
    get voices() { return voices; },
    play,
  };
}

// One player for the page, so Begin's chime, drowsy mode and "Test the
// alerts" share one gate and one list of what is sounding.
let the = null;
export function alertPlayer() { return the || (the = createAlertPlayer()); }

// Begin's chime: the same envelopes as any alert, from silence and back.
export function beginChime() {
  return alertPlayer().play([{ kind: "chime" }], { level: 1 });
}
