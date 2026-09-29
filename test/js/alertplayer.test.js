import { eq, ok } from "./assert.js";
import { voicesFor, releaseVoices, gateCloseAt, voicePlays, ALERT_DB } from "../js/alertplayer.js";
import { SILENCE_DB, worstStep, dbAt } from "../js/ramps.js";
import { ALERT_MAX_DB } from "../js/audiobus.js";
import { createAlertPlayer, rotationFor, voiceInstalled, clipNames, LEAD_SECS } from "../js/alertplayer.js";
import { planOnto, RELEASE_SECS } from "../js/ramps.js";
import { MUSIC_DB } from "../js/audiobus.js";
import { DUCK_DB } from "../js/alertplayer.js";
import { createLadder } from "../js/ladder.js";
// Fix round 1's names, read through the modules so that each new test also
// runs against the code before it, and fails there for what it checks rather
// than for a missing import.
import * as AP from "../js/alertplayer.js";
import * as AB from "../js/audiobus.js";
import * as LD from "../js/ladder.js";
import * as SND from "../js/sounds.js";

// ---- a stage with no sound in it ------------------------------------------
// The player's own stage is Web Audio. This one keeps the same books instead:
// the music bus's automation (as audiobus.js keeps it, through planOnto), the
// alert gate's events (as gateAlerts leaves them), and every sound handed to
// it with every release asked of it. Nothing here can reach a speaker.
function fakeStage(clips = {}) {
  const st = {
    t: 0,
    isRunning: true,
    refuseMusic: false,            // a browser without cancelAndHoldAtTime refuses a non-append plan
    music: [[0, MUSIC_DB]],
    musicCalls: [],
    offLevel: [],                  // music plans that did not start where the bus was
    gateEvents: [],
    sounds: [],                    // {v, releases: [{at, points, end}]}
    wake: null,
  };
  st.stage = {
    now: () => st.t,
    running: () => st.isRunning,
    resume: async () => (st.isRunning ? "running" : "suspended"),
    whenRunning: () => new Promise((r) => { st.wake = r; }),
    music(points, at, append = false) {
      st.musicCalls.push({ at, append, from: points[0][1] });
      if (st.refuseMusic && !append) return false;
      const was = dbAt(st.music, at);
      if (!append && Math.abs(points[0][1] - was) > 1e-6) st.offLevel.push({ at, from: points[0][1], was });
      st.music = planOnto(st.music, at, points, append);
      return true;
    },
    musicAt: (t) => dbAt(st.music, t),
    gate(openAt, closeAt) {
      st.gateEvents = st.gateEvents.filter(([t]) => t < openAt);
      st.gateEvents.push([openAt, 1]);
      if (Number.isFinite(closeAt)) st.gateEvents.push([closeAt, 0]);
    },
    render(v, buffer) {
      const s = { v, buffer, releases: [] };
      st.sounds.push(s);
      return { release(at, points, end) { s.releases.push({ at, points, end }); } };
    },
    clip: async (name) => (typeof clips[name] === "function" ? clips[name]() : clips[name] || null),
  };
  return st;
}

// A sound as it actually played: its envelope, cut and re-planned by every
// release the player asked of it, and when it really went silent.
function played(s) {
  let env = s.v.env, end = s.v.end;
  for (const r of s.releases) { env = [...env.filter(([t]) => t < r.at), ...r.points]; end = r.end; }
  return { env, end, start: s.v.start };
}
const gateAt = (st, t) => st.gateEvents.reduce((g, [et, val]) => (et <= t ? val : g), 0);
// Everything the alert bus carries at t, summed through the gate, EACH SOUND
// COUNTED ONLY ABOVE ITS OWN SILENCE (fix round 2): silence (-48 dBFS,
// ramps.js) plus what every sound adds over it. Counted from zero instead,
// two sounds sitting at silence read as -42 dBFS and several releases ending
// together as a jump, though none of it is anything but silence; counted
// this way the bus is silent when everything on it is, and a single sound
// reads exactly as its own envelope.
const SIL = 10 ** (SILENCE_DB / 20);
function heard(st, t) {
  if (!gateAt(st, t)) return SILENCE_DB;
  let over = 0;
  for (const s of st.sounds) {
    const p = played(s);
    if (t >= p.start && t <= p.end) over += Math.max(0, 10 ** (dbAt(p.env, t) / 20) - SIL);
  }
  return 20 * Math.log10(SIL + over);
}
// The words a voice actually says: those begun before it was let go, which
// is all sounds.js lets it say (its own rendered test holds it to that).
function said(s) {
  const r = s.releases.find((x) => Number.isFinite(x.end));
  const says = s.v.says || (s.v.plays || []).map((p) => [s.v.start + p, s.v.start + p + s.buffer.duration]);
  return says.filter(([f]) => !r || f < r.at);
}
// A drive: [t, cues, level] in order, each played at its time.
async function drive(events, clips = CLIPS) {
  const st = fakeStage(clips), p = createAlertPlayer(st.stage);
  for (const [t, cues, level] of events) { st.t = t; await p.play(cues, { level }); }
  return st;
}
// The heard bus and the peak sum on a 10 ms grid from `from` to `to`: the
// worst 100 ms of the one, the loudest point of the other, and where.
function sweepCheck(st, from, to) {
  const n = Math.round((to - from) * 100), h = [];
  let loud = -Infinity, loudAt = from;
  for (let i = 0; i <= n; i++) {
    const t = from + i / 100;
    h.push(heard(st, t));
    const pk = peakSum(st, t);
    if (pk > loud) { loud = pk; loudAt = t; }
  }
  let worst = 0, worstAt = from;
  for (let i = 0; i + 10 < h.length; i++) {
    const d = Math.abs(h[i + 10] - h[i]);
    if (d > worst) { worst = d; worstAt = from + i / 100; }
  }
  return { worst, loud, say: `worst 100 ms ${worst.toFixed(2)} dB at ${worstAt.toFixed(2)} s, peaks ${loud.toFixed(2)} dBFS at ${loudAt.toFixed(2)} s` };
}
const L1 = [{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }];
const L2 = [{ kind: "duck" }, { kind: "bark" }];
const L3 = [{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }];
const FADE = [{ kind: "fade" }];
const steps = (a, b, d) => { const r = []; for (let t = a; t <= b + 1e-9; t = +(t + d).toFixed(6)) r.push(t); return r; };
// THE PEAK BUDGET (fix round 1). Every heard peak at t added up: each sound's
// envelope times the loudest its own content gets (sounds.js PEAK), through
// the gate, plus the music as if the radio itself peaked at full scale. It
// must stay under the limiter's threshold, so the limiter never moves.
const PEAKS = SND.PEAK || { chime: 0.96, alarm: 0.6, bark: 0.7, voice: 10 ** (-2 / 20) };
const LIMIT = AB.LIMIT_DB ?? -1;
function peakSum(st, t) {
  let amp = 0;
  for (const s of st.sounds) {
    const p = played(s);
    if (t >= p.start && t <= p.end) amp += 10 ** (dbAt(p.env, t) / 20) * PEAKS[s.v.kind];
  }
  return 20 * Math.log10(amp * gateAt(st, t) + 10 ** (dbAt(st.music, t) / 20));
}
function loudest(st, from, to) {
  let m = -Infinity, at = from;
  for (let t = from; t <= to; t += 0.01) { const d = peakSum(st, t); if (d > m) { m = d; at = t; } }
  return { db: m, at, say: `peaks add up to ${m.toFixed(2)} dBFS at ${at.toFixed(2)} s` };
}
// The largest change in any 100 ms of a level read at 10 ms steps, and where.
function worstOver(level, from, to) {
  let w = 0, at = from;
  for (let t = from; t + 0.1 <= to + 1e-9; t += 0.01) {
    const d = Math.abs(level(t + 0.1) - level(t));
    if (d > w) { w = d; at = t; }
  }
  return { w, at, say: `worst 100 ms is ${w.toFixed(2)} dB, from ${at.toFixed(2)} s` };
}

// A drive that escalates all the way and is answered: Level 1 at 0, Level 2
// at 3.9 s (while Level 1's swell is still rising and its voice has just
// begun), a rotation alarm at 8.9 s, Level 3 at 9.5 s (that alarm is still
// rising), "Pull over now" again at 24.5 s over the held alarm, and "I'm
// awake" at 40 s. The ladder's own cues, in its own order.
const CLIPS = { "voice-l1-james": { duration: 2.8 }, "voice-l2-james": { duration: 1.8 }, "voice-l3": { duration: 1.0 } };
async function escalation(stage = fakeStage(CLIPS)) {
  const st = stage, p = createAlertPlayer(st.stage);
  const at = async (t, cues, level) => { st.t = t; await p.play(cues, { level }); };
  await at(0, [{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }], 1);
  await at(3.9, [{ kind: "duck" }, { kind: "bark" }], 2);
  await at(8.9, [{ kind: "alarm" }], 2);
  await at(9.5, [{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], 3);
  await at(24.5, [{ kind: "voice", clip: "l3" }], 3);
  const beforeFade = { voices: p.voices.slice(), gateEvents: st.gateEvents.slice() };
  await at(40, [{ kind: "fade" }], 0);
  return { st, p, beforeFade, kinds: st.sounds.map((s) => `${s.v.kind}${s.v.level}`) };
}
// The ladder itself driving the player: Level 2 raised at 0 and left to
// rotate (bark, voice, alarm, ...) until `until`, one step every 0.1 s (so a
// turn ends when the settings say, not at the next coarse step), then
// answered. The same settings go to both, as drowsy mode gives them.
const LM = (t, o) => Object.assign({ t, face: true, calibrated: true, closed: false, closedFor: 0, openFor: 0,
  perclos: 0.02, yawns: 0, nods: 0, faceLost: false }, o);
async function rotating(cfg, until = 40, clips = CLIPS) {
  const st = fakeStage(clips), p = createAlertPlayer(st.stage);
  if (p.setConfig) p.setConfig(cfg);
  const lad = createLadder(cfg);
  for (let i = 0; i <= until * 10; i++) {
    const t = i / 10;
    st.t = t;
    const o = lad.step({ t, m: LM(t, i ? { face: false } : { closed: true, closedFor: 1.0 }), active: true,
      parked: false, sinceStop: 0, stoppedFor: 0, hour: 14, tap: t === until });
    if (o.cues.length) await p.play(o.cues, o);
  }
  return { st, p };
}
const CFG = async () => (await fetch("../data/drowsy.json")).json();
const soundOf = (st, kind, level) => st.sounds.find((s) => s.v.kind === kind && s.v.level === level);
// The drive above is over by then: no walk along it goes further, even past
// a sound that (wrongly) never ends.
const DRIVE_END = 60;

// What a voice sounds like as heard. The gate is open (unity) from the first
// voice's start until the last voice's end, so the heard level is the
// voice's own envelope.
const first = (v) => dbAt(v.env, v.start);
const worst = (v) => worstStep(v.env, Number.isFinite(v.end) ? v.end : v.env[v.env.length - 1][0] + 1);
const fromSilence = (v) => first(v) <= SILENCE_DB + 1e-9;
const gentle = (v) => worst(v) <= 3 + 1e-6;

export default [
  ["no alert is louder than full scale, and Level 3 uses all of it", () => {
    ok(Object.values(ALERT_DB).every((db) => db <= ALERT_MAX_DB), "every level at or under 0 dBFS");
    eq(ALERT_DB[3], 0);
  }],
  ["the first alert of a drive starts from silence, after Begin's chime", () => {
    const begin = voicesFor({ kind: "chime" }, 1, 0);
    const bark = voicesFor({ kind: "bark" }, 2, 600)[0];
    ok(begin.every(fromSilence) && begin.every(gentle), "Begin's two notes rise from silence and fall back to it");
    ok(gateCloseAt(begin) < 600, "the gate closed after Begin's chime, long before the first alert");
    eq([first(bark), bark.rise, +dbAt(bark.env, 601.5).toFixed(6)], [SILENCE_DB, 1.5, ALERT_DB[2]]);
    ok(gentle(bark), `the bark's worst 100 ms is ${worst(bark).toFixed(2)} dB`);
  }],
  // Changed from the brief (controller, Task 7): no rotation sound may run
  // longer than the ladder's 5 s repeat, so a repeat no longer starts over the
  // one before it -- it starts after that one has reached silence.
  ["a repeat starts from silence too, after the one before it has fallen silent", () => {
    const one = voicesFor({ kind: "bark" }, 2, 0)[0];
    const repeat = voicesFor({ kind: "alarm" }, 2, 5)[0];
    ok(one.end <= 5, `the first bark is silent by the time the repeat begins (it ends at ${one.end})`);
    eq(dbAt(one.env, one.end), SILENCE_DB);
    ok(gateCloseAt([one, repeat]) >= repeat.end, "the gate stays open under both");
    eq(first(repeat), SILENCE_DB);
    ok(gentle(repeat) && gentle(one), "neither moves more than 3 dB in 100 ms");
  }],
  ["an escalation from Level 1 to 2 to 3: every new sound rises from silence", () => {
    const l1 = [...voicesFor({ kind: "chime" }, 1, 0), ...voicesFor({ kind: "voice", clip: "l1" }, 1, 2.5, 2.8)];
    const l2 = voicesFor({ kind: "bark" }, 2, 4);
    const l3 = [...voicesFor({ kind: "alarm", hold: true }, 3, 7), ...voicesFor({ kind: "voice", clip: "l3" }, 3, 8, 1.0)];
    const all = [...l1, ...l2, ...l3];
    ok(all.every(fromSilence), "every first sample is at silence");
    ok(all.every(gentle), "no envelope moves more than 3 dB in any 100 ms");
    eq([l3[0].end, +dbAt(l3[0].env, 10).toFixed(6)], [Infinity, 0]);
  }],
  ["Level 3's alarm has no time cap: it holds until let go, then falls to silence over 3 s", () => {
    const alarm = voicesFor({ kind: "alarm", hold: true }, 3, 0)[0];
    const [r] = releaseVoices([alarm], 3600);
    eq([alarm.end, r.end, dbAt(r.env, 3600), dbAt(r.env, 3603)], [Infinity, 3603, 0, SILENCE_DB]);
    ok(gentle(r), "its release is as gentle as its rise");
  }],
  ["after 'I'm awake' every sound fades to silence, and the gate closes behind them", () => {
    const vs = [...voicesFor({ kind: "bark" }, 2, 0), ...voicesFor({ kind: "alarm", hold: true }, 3, 2)];
    const out = releaseVoices(vs, 4);
    ok(out.every((v) => dbAt(v.env, v.end) === SILENCE_DB), "every voice ends at silence");
    ok(out.every(gentle), "no release steps");
    eq(gateCloseAt(out), 7);
  }],
  ["a sound not yet started when 'I'm awake' comes is never heard", () => {
    const v = voicesFor({ kind: "voice", clip: "l1" }, 1, 10, 2.8)[0];
    eq(releaseVoices([v], 5).map((r) => [r.dropped, r.end]), [[true, 5]]);
  }],
  ["a line is said once more than its rise can hide", () =>
    eq([voicePlays(1.8, 1.5), voicePlays(1.0, 3)], [[0, 2.2], [0, 1.4, 2.8, 4.2]])],

  // ---- Task 7's own rules, beyond the brief -------------------------------
  // Fix round 1: every Level 2 sound fits inside its turn, and the turns come
  // from the settings (ladder.js slotsOf), not a constant.
  ["no Level 2 sound runs longer than its turn, for any turns the settings give, and any clip", () => {
    for (const cfg of [{}, { level2: { repeat_secs: 4.6, voice_slot_secs: 6 } }, { level2: { repeat_secs: 8, voice_slot_secs: 9 } }]) {
      const slots = LD.slotsOf ? LD.slotsOf(cfg) : { repeat: cfg.level2 ? cfg.level2.repeat_secs : 5, voice: 7 };
      const room = (kind) => (kind === "voice" ? slots.voice : slots.repeat) - (AP.SLOT_SPARE_SECS ?? 0.1);
      const vs = [voicesFor({ kind: "bark" }, 2, 0, undefined, slots)[0], voicesFor({ kind: "alarm" }, 2, 0, undefined, slots)[0],
                  ...[0.4, 1.0, 1.8, 2.5, 3.5].map((c) => voicesFor({ kind: "voice", clip: "l2" }, 2, 0, c, slots)[0])];
      for (const v of vs) {
        ok(v.end - v.start <= room(v.kind) + 1e-9, `${JSON.stringify(slots)}: ${v.kind} runs ${(v.end - v.start).toFixed(3)} s`);
        ok(fromSilence(v) && gentle(v), `${v.kind} rises from silence, gently`);
      }
    }
  }],
  ["a longer turn lets the bark and alarm hold longer, up to their own hold", () => {
    const slots = LD.slotsOf ? LD.slotsOf({ level2: { repeat_secs: 8 } }) : null;
    eq([voicesFor({ kind: "bark" }, 2, 0, undefined, slots)[0].end, voicesFor({ kind: "alarm" }, 2, 0, undefined, slots)[0].end],
       [6, 6], "1.5 s up, 1.5 s held, 3 s down");
  }],
  ["at Level 2 the whole line is heard within 3 dB of its level: it starts once its rise is nearly done", () => {
    for (const c of [0.8, 1.0, 1.8, 2.5]) {
      const v = voicesFor({ kind: "voice", clip: "l2" }, 2, 10, c)[0];
      eq(v.kind, "voice", `a ${c} s line fits its turn`);
      const target = AP.VOICE_DB ? AP.VOICE_DB[2] : ALERT_DB[2];
      const from = 10 + v.plays[0];
      let low = Infinity;
      for (let t = from; t <= from + c + 1e-9; t += 0.01) low = Math.min(low, dbAt(v.env, t));
      ok(low >= target - 3 - 1e-6, `a ${c} s line dips to ${low.toFixed(1)} dBFS, under ${target - 3}`);
      eq(v.plays.length, 1, "said once");
    }
  }],
  ["a Level 2 line too long to be heard whole in its turn is not said: the alarm takes the turn", () =>
    eq(voicesFor({ kind: "voice", clip: "l2" }, 2, 0, 3.5).map((v) => v.kind), ["alarm"])],
  ["a bark calls all through its rise and hold", () => eq(voicesFor({ kind: "bark" }, 2, 0)[0].calls, 3)],
  ["a release starts exactly where the sound is, even mid-ramp", () => {
    const v = voicesFor({ kind: "bark" }, 2, 0)[0];
    const [r] = releaseVoices([v], 0.73);
    eq([+r.released.toFixed(6), +dbAt(r.env, r.released).toFixed(6)], [0.8, +dbAt(v.env, 0.8).toFixed(6)]);
    ok(gentle(r), "and falls gently from there");
  }],
  ["without its clips the voice leaves the rotation, and the owner's name picks the clip", async () => {
    eq([rotationFor(["bark", "voice", "alarm"], false), rotationFor(["bark", "voice", "alarm"], true), rotationFor(["voice"], false)],
       [["bark", "alarm"], ["bark", "voice", "alarm"], ["alarm"]]);
    eq(rotationFor(["bark", "voice", "alarm"], await voiceInstalled("James")), ["bark", "alarm"], "none installed here");
    eq([clipNames("l2", "James"), clipNames("l2", ""), clipNames("l3", "James")],
       [["voice-l2-james", "voice-l2"], ["voice-l2"], ["voice-l3"]]);
  }],

  // ---- the player, on a stage with no sound in it --------------------------
  ["the player: every sound it plays rises from silence and never moves more than 3 dB in 100 ms", async () => {
    const { st, kinds } = await escalation();
    eq(kinds, ["chime1", "chime1", "voice1", "bark2", "alarm2", "alarm3", "voice3", "voice3"]);
    for (const s of st.sounds) {
      const p = played(s);
      eq(dbAt(p.env, p.start), SILENCE_DB, `${s.v.kind} ${s.v.level}'s first sample`);
      const w = worstStep(p.env, Number.isFinite(p.end) ? p.end : p.env[p.env.length - 1][0] + 1);
      ok(w <= 3 + 1e-6, `${s.v.kind} ${s.v.level}: worst 100 ms is ${w.toFixed(2)} dB`);
      ok(Number.isFinite(p.end) && dbAt(p.env, p.end) === SILENCE_DB, `${s.v.kind} ${s.v.level} ends at silence`);
    }
  }],
  // THE SUMMED BUS, ACROSS TIMINGS (fix round 2). Counted above silence (see
  // heard), nothing on the alert bus moves more than 3 dB in any 100 ms and
  // nothing adds up past the limiter, on every drive below: Begin's chime
  // (its second note entering is 2.79 dB this way, and needs no exception),
  // Level 1 to 2 at every 0.1 s of Level 1, Level 2 to 3 at every 0.2 s of
  // Level 2's first three turns, "I'm awake" at every 0.5 s of the whole
  // escalation, and a tap at every 0.1 s of the voice's turn. Only the
  // sounds' own envelopes are summed, so it is a bound on what is heard.
  ["and all of them together, through the gate, never jump: Begin's chime", async () => {
    const c = sweepCheck(await drive([[0, [{ kind: "chime" }], 1]]), 0, 8);
    ok(c.worst <= 3 + 1e-6 && c.loud <= LIMIT + 1e-9, c.say);
  }],
  ["nor from Level 1 to 2, whenever Level 2 comes", async () => {
    for (const T of steps(0.3, 6, 0.1)) {
      const st = await drive([[0, L1, 1], [T, L2, 2], [T + 5, [{ kind: "voice", clip: "l2" }], 2], [T + 12, [{ kind: "alarm" }], 2]]);
      const c = sweepCheck(st, Math.max(0, T - 1), T + 14);
      ok(c.worst <= 3 + 1e-6 && c.loud <= LIMIT + 1e-9, `Level 2 at ${T}: ${c.say}`);
    }
  }],
  ["nor from Level 2 to 3, whenever Level 3 comes, voice turn included", async () => {
    const l2 = [[0, L2, 2], [5, [{ kind: "voice", clip: "l2" }], 2], [12, [{ kind: "alarm" }], 2]];
    for (const T of steps(0.2, 16, 0.2)) {
      const st = await drive([...l2.filter(([t]) => t < T), [T, L3, 3], [T + 15, [{ kind: "voice", clip: "l3" }], 3]]);
      const c = sweepCheck(st, Math.max(0, T - 1), T + 8);
      ok(c.worst <= 3 + 1e-6 && c.loud <= LIMIT + 1e-9, `Level 3 at ${T}: ${c.say}`);
    }
  }],
  ["nor when 'I'm awake' comes, anywhere in the escalation", async () => {
    const esc = [[0, L1, 1], [3.9, L2, 2], [8.9, [{ kind: "alarm" }], 2], [9.5, L3, 3], [24.5, [{ kind: "voice", clip: "l3" }], 3]];
    for (const T of steps(0.5, 40, 0.5)) {
      const st = await drive([...esc.filter(([t]) => t < T), [T, FADE, 0]]);
      const c = sweepCheck(st, Math.max(0, T - 1), T + 4);
      ok(c.worst <= 3 + 1e-6 && c.loud <= LIMIT + 1e-9, `'I'm awake' at ${T}: ${c.say}`);
    }
  }],
  ["nor when 'I'm awake' comes during the voice's turn", async () => {
    for (const T of steps(0.1, 7, 0.1)) {
      const c = sweepCheck(await drive([[0, [{ kind: "voice", clip: "l2" }], 2], [T, FADE, 0]]), 0, T + 4);
      ok(c.worst <= 3 + 1e-6 && c.loud <= LIMIT + 1e-9, `'I'm awake' at ${T}: ${c.say}`);
    }
  }],
  ["Level 1 to 2: Level 1's voice is let go as the bark rises, and its chime was already falling", async () => {
    const { st } = await escalation();
    const voice = soundOf(st, "voice", 1), bark = soundOf(st, "bark", 2);
    const p = played(voice);
    eq(voice.releases.length, 1, "the voice was let go once");
    // Fix round 1: it turns to fall first, and the bark enters one step later.
    ok(voice.releases[0].at <= bark.v.start - 0.1 + 1e-9 && voice.releases[0].at >= bark.v.start - 0.2 - 1e-9,
       `let go just before the bark starts (${voice.releases[0].at} vs ${bark.v.start})`);
    ok(p.end <= bark.v.start + RELEASE_SECS + 1e-9, `silent by ${p.end}`);
    ok(st.sounds.filter((s) => s.v.kind === "chime").every((s) => played(s).end <= 6.15 + 1e-9),
       "the chime's notes ended on their own falls");
  }],
  ["Level 1 to 2: the swell is cut where it stands and the music ducks from there, never stepped", async () => {
    const { st } = await escalation();
    eq(st.offLevel, [], "every music plan started at the level the bus really had");
    const w = worstOver((t) => dbAt(st.music, t), 0, 60);
    ok(w.w <= 3 + 1e-6, `music: ${w.say}`);
    ok(dbAt(st.music, 3.95) > MUSIC_DB + 2, "the swell was still rising when Level 2 came");
    eq([5, 39].map((t) => +dbAt(st.music, t).toFixed(6)), [MUSIC_DB - 12, MUSIC_DB - 12],
       "ducked 12 dB, and the swell's settle no longer runs underneath");
    eq(+dbAt(st.music, 44).toFixed(6), MUSIC_DB, "and back to its level after 'I'm awake'");
  }],
  ["Level 2 to 3: the Level 2 sound still rising is let go as the Level 3 alarm rises", async () => {
    const { st } = await escalation();
    const l2 = soundOf(st, "alarm", 2), l3 = soundOf(st, "alarm", 3);
    const p = played(l2);
    eq(l2.releases.length, 1, "the Level 2 alarm was let go");
    ok(p.end <= l3.v.start + 0.1 + RELEASE_SECS + 1e-9, `silent by ${p.end}, not at ${l2.v.end}`);
    let high = -Infinity;
    for (let t = l3.v.start + 0.1; t <= Math.min(p.end, DRIVE_END); t += 0.01) {
      const d = dbAt(p.env, t);
      ok(d <= dbAt(p.env, t - 0.01) + 1e-9, `falling from the moment the alarm rises (at ${t.toFixed(2)})`);
      high = Math.max(high, d);
    }
    ok(high < ALERT_DB[2] - 3, `it never reached its own level (${high.toFixed(1)} dB)`);
  }],
  ["the gate is open under every sound, and under the held alarm for as long as it sounds", async () => {
    const { st, beforeFade } = await escalation();
    for (const s of st.sounds) {
      const p = played(s);
      for (let t = p.start; t < Math.min(p.end, DRIVE_END); t += 0.05) {
        ok(gateAt(st, t) === 1, `${s.v.kind} ${s.v.level} at ${t.toFixed(2)}: gate open`);
      }
    }
    const held = soundOf(st, "alarm", 3).v.start;
    ok(!beforeFade.gateEvents.some(([t, g]) => g === 0 && t >= held), "no close was scheduled after the held alarm began");
    eq(gateCloseAt(beforeFade.voices), Infinity);
  }],
  ["'I'm awake' lets the held Level 3 alarm go, down to silence over 3 s, and the gate closes after it", async () => {
    const { st, p } = await escalation();
    const alarm = soundOf(st, "alarm", 3), a = played(alarm);
    const letGo = alarm.releases.filter((r) => Number.isFinite(r.end));
    eq([letGo.length, alarm.releases.length - letGo.length], [1, 2], "let go once, after stepping aside under two lines");
    eq([+letGo[0].at.toFixed(6), +a.end.toFixed(6), dbAt(a.env, a.end)],
       [40 + LEAD_SECS, 40 + LEAD_SECS + RELEASE_SECS, SILENCE_DB]);
    eq(gateCloseAt(p.voices), a.end, "the player's own books agree");
    eq([gateAt(st, a.end - 0.01), gateAt(st, a.end + 0.01)], [1, 0]);
  }],
  ["with no clips installed the voice cues are skipped, and Level 2's voice slot plays the alarm instead", async () => {
    const st = fakeStage(), p = createAlertPlayer(st.stage);
    await p.play([{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }], { level: 1 });
    st.t = 10;
    await p.play([{ kind: "voice", clip: "l2" }], { level: 2 });
    st.t = 20;
    await p.play([{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], { level: 3 });
    eq(st.sounds.map((s) => `${s.v.kind}${s.v.level}`), ["chime1", "chime1", "alarm2", "alarm3"]);
  }],
  ["a line still loading when 'I'm awake' comes is never said", async () => {
    let arrive;
    const st = fakeStage({ "voice-l1-james": () => new Promise((r) => { arrive = r; }) });
    const p = createAlertPlayer(st.stage);
    const l1 = p.play([{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }], { level: 1 });
    st.t = 1;
    await p.play([{ kind: "fade" }], { level: 0 });
    arrive({ duration: 2.8 });
    await l1;
    eq(st.sounds.map((s) => s.v.kind), ["chime", "chime"]);
  }],

  // ---- fix round 1 ---------------------------------------------------------
  // IMPORTANT 1: THE PEAK BUDGET. In every plan, every heard peak plus the
  // music adds up to no more than the limiter's -1 dBFS.
  ["the peak budget: Begin's chime over the music stays under the limiter", async () => {
    const st = fakeStage(), p = createAlertPlayer(st.stage);
    await p.play([{ kind: "chime" }], { level: 1 });
    const l = loudest(st, 0, 8);
    ok(l.db <= LIMIT + 1e-9, l.say);
  }],
  ["the peak budget: Level 1's chime and voice over the rising radio, even with a long line", async () => {
    for (const secs of [1.5, 2.8, 4.0]) {
      const st = fakeStage({ "voice-l1-james": { duration: secs } }), p = createAlertPlayer(st.stage);
      await p.play([{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }], { level: 1 });
      const l = loudest(st, 0, 45);
      ok(l.db <= LIMIT + 1e-9, `a ${secs} s line: ${l.say}`);
    }
  }],
  ["the peak budget: the whole escalation, Level 1 to 2 to 3, 'Pull over now' twice over the held alarm, and the fade", async () => {
    const { st } = await escalation();
    const l = loudest(st, 0, 45);
    ok(l.db <= LIMIT + 1e-9, l.say);
  }],
  ["the peak budget: Level 2's repeats, the ladder rotating bark, voice and alarm", async () => {
    const { st } = await rotating(await CFG(), 40);
    eq([...new Set(st.sounds.map((s) => s.v.kind))].sort(), ["alarm", "bark", "voice"]);
    const l = loudest(st, 0, 45);
    ok(l.db <= LIMIT + 1e-9, l.say);
  }],
  ["the peak budget: Level 3 on its own, its line repeating every 15 s", async () => {
    const st = fakeStage(CLIPS), p = createAlertPlayer(st.stage);
    await p.play([{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], { level: 3 });
    for (const t of [15, 30]) { st.t = t; await p.play([{ kind: "voice", clip: "l3" }], { level: 3 }); }
    st.t = 50;
    await p.play([{ kind: "fade" }], { level: 0 });
    const l = loudest(st, 0, 55);
    ok(l.db <= LIMIT + 1e-9, l.say);
  }],
  ["under each 'Pull over now' the held alarm steps 9 dB aside, from at least 0.3 s before the line, and comes back after", async () => {
    const { st } = await escalation();
    const alarm = played(soundOf(st, "alarm", 3));
    const lines = st.sounds.filter((s) => s.v.kind === "voice" && s.v.level === 3).map((s) => s.v.line);
    eq(lines.length, 2);
    for (const [from, to] of lines) {
      let high = -Infinity;
      for (let t = from; t <= to; t += 0.01) high = Math.max(high, dbAt(alarm.env, t));
      ok(high <= ALERT_DB[3] - 9 + 1e-6, `the alarm under the line ${from.toFixed(2)}-${to.toFixed(2)} reaches ${high.toFixed(2)} dB`);
    }
    // The repeat, over an alarm already at full: moving by 0.3 s before its line.
    const [from2, to2] = lines[1];
    ok(dbAt(alarm.env, from2 - 0.3) < ALERT_DB[3] - 1, `already stepping aside 0.3 s before (${dbAt(alarm.env, from2 - 0.3).toFixed(2)} dB)`);
    eq(+dbAt(alarm.env, to2 + (AP.RETURN_SECS ?? 1) + 0.01).toFixed(6), ALERT_DB[3], "and back at full after the line");
    const w = worstStep(alarm.env, 40);
    ok(w <= 3 + 1e-6, `the alarm's worst 100 ms, dips and all, is ${w.toFixed(2)} dB`);
    const voice = played(soundOf(st, "voice", 3));
    eq(Math.max(...voice.env.map(([, d]) => d)), -3, "and the line itself speaks at -3 dBFS");
  }],
  ["a dip plan: from a held alarm, or one still rising, never a step and never louder than it was going to be", () => {
    ok(typeof AP.dipPlan === "function", "dipPlan exists");
    const held = voicesFor({ kind: "alarm", hold: true }, 3, 0)[0];
    const a = AP.dipPlan(held, 10, 15.2);
    eq([10, 10.5, 15.2, 16.2].map((t) => +dbAt(a.voice.env, t).toFixed(6)), [0, -9, -9, 0]);
    const b = AP.dipPlan(held, 0.5, 6.2);
    eq([+dbAt(b.voice.env, 6.2).toFixed(6), +dbAt(b.voice.env, 7.2).toFixed(6), b.voice.end], [-9, 0, Infinity]);
    for (const x of [a, b]) {
      ok(worstStep(x.voice.env, 20) <= 3 + 1e-6, "gentle");
      for (let t = 0; t < 20; t += 0.05) ok(dbAt(x.voice.env, t) <= dbAt(held.env, t) + 1e-9, `never over the plain alarm at ${t}`);
    }
  }],

  // IMPORTANT 2: the turns come from the settings, and never overlap.
  ["the ladder's turns and the player's sounds never overlap, whatever turns the settings give", async () => {
    for (const l2 of [{}, { repeat_secs: 6, voice_slot_secs: 8 }, { repeat_secs: 4.6, voice_slot_secs: 6.5 }]) {
      const cfg = await CFG();
      Object.assign(cfg.level2, l2);
      const { st } = await rotating(cfg, 40);
      const vs = st.sounds.map(played).sort((x, y) => x.start - y.start);
      ok(vs.length >= 6, `${vs.length} sounds`);
      for (let i = 1; i < vs.length; i++) {
        ok(vs[i - 1].end <= vs[i].start + 1e-9,
           `${JSON.stringify(l2)}: one ends at ${vs[i - 1].end.toFixed(2)}, after the next starts at ${vs[i].start.toFixed(2)}`);
      }
    }
  }],
  ["and the player takes its turns from the same settings: a longer turn, a longer bark", async () => {
    const cfg = await CFG();
    cfg.level2.repeat_secs = 8;
    const { st } = await rotating(cfg, 12);
    const bark = played(soundOf(st, "bark", 2));
    eq(+(bark.end - bark.start).toFixed(6), 6, "1.5 s up, its whole 1.5 s held, 3 s down");
  }],
  ["and the voice's turn is heard whole: its line from within 3 dB of its level", async () => {
    const { st } = await rotating(await CFG(), 40);
    // Every line said before "I'm awake" at 40 s, which lets the last one go.
    const voices = st.sounds.filter((s) => s.v.kind === "voice" && s.v.line[1] < 40);
    ok(voices.length >= 2, `${voices.length} lines`);
    for (const s of voices) {
      const [from, to] = s.v.line;
      for (let t = from; t <= to; t += 0.01) ok(dbAt(played(s).env, t) >= -6 - 1e-6, `at ${t.toFixed(2)}`);
    }
  }],

  // Minor 3: a late line is still over by the end of its turn.
  ["a Level 2 line whose clip comes up to 1 s late is let go early, and is silent by the end of its turn", async () => {
    for (const late of [0.3, 0.9]) {
      let arrive;
      const st = fakeStage({ "voice-l2-james": () => new Promise((r) => { arrive = r; }) });
      const p = createAlertPlayer(st.stage);
      const done = p.play([{ kind: "voice", clip: "l2" }], { level: 2 });
      st.t = late;
      arrive({ duration: 1.8 });
      await done;
      const turnEnd = LEAD_SECS + (LD.slotsOf ? LD.slotsOf().voice : 5) - (AP.SLOT_SPARE_SECS ?? 0.1);
      eq(st.sounds.length, 1, "it is still said");
      const v = played(st.sounds[0]);
      ok(v.start >= late, `it starts late, at ${v.start.toFixed(2)}`);
      ok(v.end <= turnEnd + 1e-9, `${late} s late: silent at ${v.end.toFixed(2)}, turn over at ${turnEnd.toFixed(2)}`);
      ok(worstStep(v.env, v.end) <= 3 + 1e-6 && dbAt(v.env, v.end) === SILENCE_DB, "falling gently to silence");
    }
  }],
  ["and so is the alarm that stands in for a line with no clip", async () => {
    let arrive;
    const st = fakeStage({ "voice-l2-james": () => new Promise((r) => { arrive = r; }) });
    const p = createAlertPlayer(st.stage);
    const done = p.play([{ kind: "voice", clip: "l2" }], { level: 2 });
    st.t = 0.9;
    arrive(null);
    await done;
    const turnEnd = LEAD_SECS + (LD.slotsOf ? LD.slotsOf().voice : 5) - (AP.SLOT_SPARE_SECS ?? 0.1);
    eq(st.sounds.map((s) => s.v.kind), ["alarm"]);
    ok(played(st.sounds[0]).end <= turnEnd + 1e-9, `silent at ${played(st.sounds[0]).end.toFixed(2)}`);
  }],

  // Minor 4: a stalled clock queues nothing.
  ["while the context is suspended, six repeats queue as one: on resume one sound plays, from where things are", async () => {
    const st = fakeStage();
    st.isRunning = false;
    const p = createAlertPlayer(st.stage);
    const plays = [p.play([{ kind: "duck" }, { kind: "bark" }], { level: 2 })];
    for (const c of ["alarm", "bark", "alarm", "bark", "alarm"]) plays.push(p.play([{ kind: c }], { level: 2 }));
    eq(st.sounds.length, 0, "nothing scheduled on a clock that is not moving");
    st.t = 30;
    st.isRunning = true;
    if (st.wake) st.wake();
    await Promise.all(plays);
    eq(st.sounds.map((s) => [s.v.kind, +s.v.start.toFixed(6)]), [["alarm", 30 + LEAD_SECS]], "one plays, the latest");
    eq([st.offLevel, +dbAt(st.music, 31).toFixed(6)], [[], MUSIC_DB + DUCK_DB], "and the music ducks once, from its level");
  }],
  ["and 'I'm awake' while suspended leaves nothing to play on resume", async () => {
    const st = fakeStage();
    st.isRunning = false;
    const p = createAlertPlayer(st.stage);
    const plays = [p.play([{ kind: "duck" }, { kind: "alarm", hold: true }], { level: 3 }), p.play([{ kind: "fade" }], { level: 0 })];
    st.isRunning = true;
    if (st.wake) st.wake();
    await Promise.all(plays);
    eq(st.sounds.length, 0);
  }],

  // Minor 5: one player per gate.
  ["a player needs a stage: the page's one player is alertPlayer(), and no second one can reach its gate", () => {
    let threw = false;
    try { createAlertPlayer(); } catch { threw = true; }
    ok(threw, "createAlertPlayer() with no stage throws");
  }],

  // Minor 7: a refused swell leaves no settle behind.
  ["a swell the stage refuses (no cancelAndHoldAtTime) appends no settle to a plan that never ran", async () => {
    const st = fakeStage();
    st.refuseMusic = true;
    const p = createAlertPlayer(st.stage);
    await p.play([{ kind: "swell" }], { level: 1 });
    eq(st.musicCalls.map((c) => c.append), [false], "the swell was asked for, refused, and nothing followed it");
    eq(dbAt(st.music, 60), MUSIC_DB);
  }],

  // ---- fix round 2 ---------------------------------------------------------
  // N1: a Level 2 sound that enters a step late, after Level 1 has turned to
  // fall, still ends with its turn.
  ["after Level 1, the first Level 2 sound still ends with its turn, and the turns never overlap", async () => {
    for (const T of steps(0.3, 6, 0.05)) {
      const st = await drive([[0, L1, 1], [T, L2, 2], [T + 5, [{ kind: "voice", clip: "l2" }], 2], [T + 12, [{ kind: "alarm" }], 2]]);
      const l2 = st.sounds.filter((s) => s.v.level === 2).map(played).sort((a, b) => a.start - b.start);
      const ends = [T + LEAD_SECS + 5 - 0.1, T + 5 + LEAD_SECS + 7 - 0.1];
      eq(l2.length, 3, `Level 2 at ${T}`);
      for (let i = 0; i < 2; i++) {
        ok(l2[i].end <= ends[i] + 1e-9, `Level 2 at ${T}: turn ${i + 1} silent at ${l2[i].end.toFixed(3)}, ends at ${ends[i].toFixed(3)}`);
        ok(l2[i].end <= l2[i + 1].start + 1e-9, `Level 2 at ${T}: turn ${i + 1} overlaps the next`);
      }
    }
  }],

  // N2: a sound let go says nothing new.
  ["a voice let go keeps only the sayings already begun: the plan says what sounds.js will play", () => {
    const v = voicesFor({ kind: "voice", clip: "l3" }, 3, 0, 1.0)[0];   // sayings at 0, 1.4, 2.8, 4.2
    const [r] = releaseVoices([v], 2.0);
    eq([v.plays, r.plays, r.says, r.line], [[0, 1.4, 2.8, 4.2], [0, 1.4], [[0, 1], [1.4, 2.4]], [0, 2.4]]);
  }],
  ["'I'm awake' during Level 2's voice turn: no word begins after it", async () => {
    for (const T of steps(0.1, 7, 0.1)) {
      const st = await drive([[0, [{ kind: "voice", clip: "l2" }], 2], [T, FADE, 0]]);
      for (const s of st.sounds) {
        for (const [f] of said(s)) ok(f < T + LEAD_SECS + 0.1, `'I'm awake' at ${T}: a line begins at ${f.toFixed(2)}`);
      }
    }
  }],
  ["nor during Level 1's or Level 3's lines: their next sayings are never said", async () => {
    for (const [events, span] of [[[[0, L1, 1]], [2.6, 12]], [[[0, L3, 3]], [1.2, 9.5]]]) {
      for (const T of steps(span[0], span[1], 0.2)) {
        const st = await drive([...events, [T, FADE, 0]]);
        for (const s of st.sounds) {
          for (const [f] of said(s)) ok(f < T + LEAD_SECS + 0.1, `'I'm awake' at ${T}: a saying begins at ${f.toFixed(2)}`);
        }
      }
    }
  }],
  ["Level 2 to 3 during the voice's turn: 'James, are you with me?' never overlaps 'Pull over now'", async () => {
    for (const T of steps(0.1, 6.5, 0.1)) {
      const st = await drive([[0, [{ kind: "voice", clip: "l2" }], 2], [T, L3, 3]]);
      const l2 = st.sounds.filter((s) => s.v.kind === "voice" && s.v.level === 2).flatMap(said);
      const l3 = st.sounds.filter((s) => s.v.kind === "voice" && s.v.level === 3).flatMap(said);
      ok(l3.length >= 1, `Level 3 at ${T}: 'Pull over now' is said`);
      for (const [, to] of l2) {
        for (const [f] of l3) ok(to <= f + 1e-9, `Level 3 at ${T}: Level 2's line runs to ${to.toFixed(2)}, over 'Pull over now' at ${f.toFixed(2)}`);
      }
    }
  }],
];
