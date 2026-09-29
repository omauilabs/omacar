import { eq, ok } from "./assert.js";
import { voicesFor, releaseVoices, gateCloseAt, voicePlays, ALERT_DB } from "../js/alertplayer.js";
import { SILENCE_DB, worstStep, dbAt } from "../js/ramps.js";
import { ALERT_MAX_DB } from "../js/audiobus.js";
import {
  createAlertPlayer, rotationFor, voiceInstalled, clipNames, ROTATION_SECS, LEAD_SECS,
} from "../js/alertplayer.js";
import { planOnto, RELEASE_SECS } from "../js/ramps.js";
import { MUSIC_DB } from "../js/audiobus.js";
import { DUCK_DB } from "../js/alertplayer.js";

// ---- a stage with no sound in it ------------------------------------------
// The player's own stage is Web Audio. This one keeps the same books instead:
// the music bus's automation (as audiobus.js keeps it, through planOnto), the
// alert gate's events (as gateAlerts leaves them), and every sound handed to
// it with every release asked of it. Nothing here can reach a speaker.
function fakeStage(clips = {}) {
  const st = {
    t: 0,
    music: [[0, MUSIC_DB]],
    offLevel: [],                  // music plans that did not start where the bus was
    gateEvents: [],
    sounds: [],                    // {v, releases: [{at, points, end}]}
  };
  st.stage = {
    now: () => st.t,
    resume: async () => "running",
    music(points, at, append = false) {
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
// Everything the alert bus carries at t, summed in amplitude, as heard through
// the gate, from the quietest the music ever sits under an alert (ducked, -24
// dBFS) up. Below that, each sound's own envelope is the check (above): there
// a dB reading of a sum measures nothing anyone hears -- two sounds at
// silence sum to 6 dB over silence, and one entering at silence under
// another near silence moves the sum by a decibel or two of nothing.
const AUDIBLE_DB = MUSIC_DB + DUCK_DB;
function heard(st, t) {
  let amp = 0;
  for (const s of st.sounds) {
    const p = played(s);
    if (t >= p.start && t <= p.end) amp += 10 ** (dbAt(p.env, t) / 20);
  }
  return Math.max(AUDIBLE_DB, 20 * Math.log10(amp * gateAt(st, t) || 1e-9));
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
// rising) and "I'm awake" at 40 s. The ladder's own cues, in its own order.
async function escalation(stage = fakeStage({ "voice-l1-james": { duration: 2.8 }, "voice-l3": { duration: 1.0 } })) {
  const st = stage, p = createAlertPlayer(st.stage);
  const at = async (t, cues, level) => { st.t = t; await p.play(cues, { level }); };
  await at(0, [{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }], 1);
  await at(3.9, [{ kind: "duck" }, { kind: "bark" }], 2);
  await at(8.9, [{ kind: "alarm" }], 2);
  await at(9.5, [{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }], 3);
  const beforeFade = { voices: p.voices.slice(), gateEvents: st.gateEvents.slice() };
  await at(40, [{ kind: "fade" }], 0);
  return { st, p, beforeFade, kinds: st.sounds.map((s) => `${s.v.kind}${s.v.level}`) };
}
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
  ["a Level 2 rotation sound's longest run fits inside the ladder's repeat", async () => {
    const cfg = await fetch("../data/drowsy.json").then((r) => r.json());
    ok(ROTATION_SECS < cfg.level2.repeat_secs, `${ROTATION_SECS} s fits inside ${cfg.level2.repeat_secs} s`);
  }],
  ["and no Level 2 rotation sound runs longer than that, whatever its clip", () => {
    const vs = [voicesFor({ kind: "bark" }, 2, 0)[0], voicesFor({ kind: "alarm" }, 2, 0)[0],
                ...[0.4, 1.0, 1.8, 3.0].map((c) => voicesFor({ kind: "voice", clip: "l2" }, 2, 0, c)[0])];
    for (const v of vs) {
      ok(v.end - v.start <= ROTATION_SECS + 1e-9, `${v.kind} runs ${(v.end - v.start).toFixed(3)} s`);
      ok(fromSilence(v) && gentle(v), `${v.kind} rises from silence, gently`);
    }
  }],
  ["at Level 2 a line is said only where it fits: once, from the start of its rise", () =>
    eq([1.0, 1.8].map((c) => voicesFor({ kind: "voice", clip: "l2" }, 2, 0, c)[0].plays), [[0, 1.4], [0]])],
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
    eq(kinds, ["chime1", "chime1", "voice1", "bark2", "alarm2", "alarm3", "voice3"]);
    for (const s of st.sounds) {
      const p = played(s);
      eq(dbAt(p.env, p.start), SILENCE_DB, `${s.v.kind} ${s.v.level}'s first sample`);
      const w = worstStep(p.env, Number.isFinite(p.end) ? p.end : p.env[p.env.length - 1][0] + 1);
      ok(w <= 3 + 1e-6, `${s.v.kind} ${s.v.level}: worst 100 ms is ${w.toFixed(2)} dB`);
      ok(Number.isFinite(p.end) && dbAt(p.env, p.end) === SILENCE_DB, `${s.v.kind} ${s.v.level} ends at silence`);
    }
  }],
  ["and all of them together, through the gate, never jump where they can be heard", async () => {
    const { st } = await escalation();
    const w = worstOver((t) => heard(st, t), 0, 50);
    ok(w.w <= 3 + 1e-6, `the alert bus, all sounds summed through the gate: ${w.say}`);
  }],
  ["Level 1 to 2: Level 1's voice is let go as the bark rises, and its chime was already falling", async () => {
    const { st } = await escalation();
    const voice = soundOf(st, "voice", 1), bark = soundOf(st, "bark", 2);
    const p = played(voice);
    eq(voice.releases.length, 1, "the voice was let go once");
    ok(voice.releases[0].at >= bark.v.start && voice.releases[0].at <= bark.v.start + 0.1 + 1e-9,
       `let go as the bark starts (${voice.releases[0].at} vs ${bark.v.start})`);
    ok(p.end <= bark.v.start + 0.1 + RELEASE_SECS + 1e-9, `silent by ${p.end}`);
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
    eq(alarm.releases.length, 1, "the held alarm was let go");
    eq([+alarm.releases[0].at.toFixed(6), +a.end.toFixed(6), dbAt(a.env, a.end)],
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
];
