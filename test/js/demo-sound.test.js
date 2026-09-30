// The meetup demo's sound, hardening B (doc/design/2026-09-30-meetup-demo-hardening.md):
// the voice through the audio stage (demo/js/voice.js into share/js/audiobus.js
// voiceIn()), the `quiet` cue's fade (demo/js/quiet.js), and the tour's reset
// fading before its restart (demo/js/tour.js createReset).
//
// NOTHING HERE HAS A SPEAKER. The stage is rendered into OfflineAudioContexts
// (a second copy of audiobus.js, built on one; see offlineStage()), the page's
// own context is only ever spied on, and the radio, the clock and the store
// are fakes this file moves.
import { eq, ok } from "./assert.js";
import { MUSIC_DB, LIMIT_DB, audioContext, gainToDb, voiceIn } from "../js/audiobus.js";
import { SILENCE_DB, STEP_SECS, MAX_DB_PER_STEP, dbAt, planOnto, worstStep, rampPlan, glidePlan } from "../js/ramps.js";
import { voicesFor, DUCK_DB } from "../js/alertplayer.js";
import { playAlarm, playBark } from "../js/sounds.js";
import { SAY_AFTER } from "../demo/js/drowsy.js";
import { say, voiceIO, VOICE_DB, ROOM_DB, DOWN_SECS, DUCK_DB as LINE_DUCK_DB } from "../demo/js/voice.js";
import { createReset, RESET_FADE_MS } from "../demo/js/tour.js";
import { createQuiet, register, fadeOut } from "../demo/js/quiet.js";
import { offlineStage, rendered, flatInto, peakOf } from "./offline-stage.js";

const tick = () => new Promise((done) => setTimeout(done, 0));
const wait = (ms) => new Promise((done) => setTimeout(done, ms));

// The worst part of the stage's worst case: music at MUSIC_DB with the radio
// at full scale, a Level 2 alarm on its hold at ALERT_DB[2], and a demo line
// at voice.js's level whose words are at full scale too (Piper normalises
// near 0 dBFS, and voice.js trims nothing). `voiceTo` is where the line goes.
async function worstCase(voiceTo) {
  const { stage, ctx, rate } = await offlineStage(2.4, 48000);
  flatInto(ctx, stage.musicIn(), 0);
  const [alarm] = voicesFor({ kind: "alarm" }, 2, 0.1);
  stage.gateAlerts(alarm.start, alarm.end);
  playAlarm(alarm, { ctx, out: stage.alertIn() });
  const line = voiceTo === "stage" ? stage.voiceIn() : ctx.destination;
  flatInto(ctx, line, VOICE_DB, 0.5);
  const out = await rendered(ctx);
  // The alarm holds at its level from its rise's end for HOLD (0.4 s).
  const held = [alarm.start + alarm.rise + 0.02, alarm.start + alarm.rise + 0.38];
  return { out, rate, held, peak: peakOf(out, rate, held[0], held[1]) };
}

// ---- the quiet cue's fade, on books ---------------------------------------------------
// A clock whose timers run only when it is moved, and a stage that keeps the
// music bus's automation the way audiobus.js keeps it (planOnto), so dbAt()
// of it is the level the bus really has at any moment.
function rig({ playing = true, loadedAt = 1000 } = {}) {
  const r = { t: 10, timers: [], nextId: 1, music: [[0, MUSIC_DB]], plans: [], log: [] };
  r.later = (fn, ms) => { const id = r.nextId++; r.timers.push({ id, at: r.t + Math.max(0, ms) / 1000, fn }); return id; };
  r.advance = (secs) => {
    const end = r.t + secs;
    for (;;) {
      const due = r.timers.filter((x) => x.at <= end + 1e-9).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      r.timers = r.timers.filter((x) => x !== due);
      r.t = due.at;
      due.fn();
    }
    r.t = end;
  };
  r.stage = {
    now: () => r.t,
    levelAt: (bus, t) => dbAt(r.music, t),
    schedule(bus, points, at) {
      r.plans.push({ bus, at, points });
      r.music = planOnto(r.music, at, points);
      return true;
    },
  };
  r.radio = {
    playing,
    pause() { r.log.push(["pause", r.t, dbAt(r.music, r.t)]); this.playing = false; },
  };
  r.quiet = createQuiet({ radio: r.radio, stage: r.stage, later: r.later, loadedAt });
  r.level = (t) => dbAt(r.music, t);
  return r;
}
const worstOver = (level, from, to) => {
  let w = 0, at = from;
  for (let t = from; t + STEP_SECS <= to + 1e-9; t += 0.01) {
    const d = Math.abs(level(t + STEP_SECS) - level(t));
    if (d > w) { w = d; at = t; }
  }
  return { w, at, say: `worst 100 ms is ${w.toFixed(2)} dB, from ${at.toFixed(2)} s` };
};

export default [
  // ---- the voice, through the stage --------------------------------------------------
  ["a demo line plays into the stage's voiceIn(), not straight to the output", async () => {
    const realFetch = voiceIO.fetch, realSchedule = voiceIO.schedule, realLevel = voiceIO.levelAt;
    const ctx = audioContext();
    const proto = Object.getPrototypeOf(ctx);
    const to = [];
    try {
      voiceIO.fetch = async () => new Response(silentWav(0.05));
      voiceIO.schedule = () => true;
      voiceIO.levelAt = () => MUSIC_DB;
      ctx.createGain = () => {
        const g = proto.createGain.call(ctx);
        const connect = g.connect.bind(g);
        g.connect = (node, ...rest) => { to.push(node); return connect(node, ...rest); };
        return g;
      };
      // An id of its own: voice.js keeps what it decodes, by id, for the page.
      await say("hardening-b-routing");
    } finally {
      delete ctx.createGain;
      voiceIO.fetch = realFetch; voiceIO.schedule = realSchedule; voiceIO.levelAt = realLevel;
    }
    eq(to.length, 1, "the line's one gain node, connected once");
    ok(to[0] === voiceIn(), "into voiceIn()");
    ok(to[0] !== ctx.destination, "and not past the limiter");
  }],
  ["the voice keeps its level: -7 dB, the alert player's quietest line", () => eq(VOICE_DB, -7)],
  ["the worst case, rendered: a line over music at MUSIC_DB and a Level 2 alarm comes out under full scale", async () => {
    // What reaches the stage adds up to over full scale here: the radio's
    // peak at -12, the alarm's at -3 less its own 4.4 dB (sounds.js PEAK) and
    // the line's at -7. Past the limiter, as voice.js used to play it, the
    // line clips the output.
    const past = await worstCase("destination");
    ok(past.peak > 0, `a line past the limiter: ${past.peak.toFixed(2)} dBFS, clipped`);
    const through = await worstCase("stage");
    // THE LIMITER TAKES IT UNDER FULL SCALE, BUT NOT TO -1 dBFS. Chromium's
    // DynamicsCompressorNode adds makeup gain to everything it passes (+0.57
    // dB at this threshold and ratio, measured on the box: -12 dBFS in,
    // -11.43 out; a steady +1 dBFS in, -0.33 out), and its 3 ms attack lets
    // the alarm's peaks through a little. So an over like this one comes out
    // just under full scale: the output never clips, which is the limiter's
    // job, but -1 dBFS is not where it holds. The live stage is not changed
    // here; see hardening-B-report.md.
    ok(through.peak < 0, `the same line through voiceIn(): ${through.peak.toFixed(2)} dBFS, under full scale`);
    ok(through.peak < past.peak - 1, `and the limiter is what took it there (${(past.peak - through.peak).toFixed(2)} dB)`);
  }],
  ["as the demo plays it, a Level 2 moment and its line stay under -1 dBFS", async () => {
    // drowsy.js: the duck and the bark, and SAY_AFTER later its line, which
    // voice.js starts DOWN_SECS after it is asked. The alert player has
    // ducked the music by then, and voice.js leaves a bus it finds ducked
    // alone (ROOM_DB). Radio and words at full scale again.
    ok(MUSIC_DB + DUCK_DB <= ROOM_DB, "a bus the alert has ducked is one voice.js leaves alone");
    const t0 = 0.1, lineAt = t0 + SAY_AFTER + DOWN_SECS;
    const { stage, ctx, rate } = await offlineStage(lineAt + 2.3);
    flatInto(ctx, stage.musicIn(), 0);
    stage.schedule("music", rampPlan(2, MUSIC_DB, MUSIC_DB + DUCK_DB).points, t0);
    const [bark] = voicesFor({ kind: "bark" }, 2, t0);
    stage.gateAlerts(bark.start, bark.end);
    playBark(bark, { ctx, out: stage.alertIn() });
    flatInto(ctx, stage.voiceIn(), VOICE_DB, lineAt, lineAt + 2);
    const out = await rendered(ctx);
    const pk = peakOf(out, rate, 0, lineAt + 2.3);
    ok(pk <= LIMIT_DB, `the loudest sample is ${pk.toFixed(2)} dBFS`);
    ok(peakOf(out, rate, lineAt + 0.1, lineAt + 1.9) > -9, "and the line is in it");
  }],

  // ---- the quiet cue's fade ------------------------------------------------------------
  ["quiet: the music falls to silence with no 100 ms moving more than 3 dB, and then the radio pauses", async () => {
    const r = rig();
    const done = r.quiet.fadeOut();
    eq(r.plans.length, 1, "one plan, laid at once");
    const [p] = r.plans;
    eq([p.bus, p.points[0][1], p.points.at(-1)[1]], ["music", MUSIC_DB, SILENCE_DB], "the music bus, from where it is, to silence");
    const w = worstOver(r.level, 10, 13);
    ok(w.w <= MAX_DB_PER_STEP + 1e-6, w.say);
    ok(worstStep(p.points) <= MAX_DB_PER_STEP + 1e-6, "and the plan itself");
    const end = p.at + p.points.at(-1)[0];
    // 36 dB at 3 dB per 100 ms is 1.2 s: 0.8 s is the least a fade takes, and
    // a longer span is stretched to the rule, never stepped (ramps.js).
    eq(+(end - p.at).toFixed(4), 1.2, "from MUSIC_DB, 1.2 s");
    r.advance(end - r.t - 0.06);
    eq(r.log, [], "the radio plays on while the music falls");
    r.advance(0.2);
    eq(r.log.length, 1, "then it pauses, once");
    const [[, at, db]] = r.log;
    ok(at >= end && db <= SILENCE_DB + 1e-6, `at ${at.toFixed(2)} s, at ${db.toFixed(1)} dB: at silence, not above it`);
    eq(await done, true, "fadeOut() resolves once the radio is paused");
    // Back at MUSIC_DB for the next play, gently, with the radio paused.
    eq(+r.level(r.t + 10).toFixed(6), MUSIC_DB, "the bus is back at MUSIC_DB afterwards");
    const back = worstOver(r.level, at, r.t + 5);
    ok(back.w <= MAX_DB_PER_STEP + 1e-6, `and comes back without a step: ${back.say}`);
    ok(r.plans.at(-1).at >= at, "only once the radio has paused");
  }],
  ["quiet: from a bus the alerts have ducked (-24), the fade takes 0.8 s", () => {
    const r = rig();
    r.music = [[0, MUSIC_DB - 12]];
    r.quiet.fadeOut();
    const [p] = r.plans;
    eq([p.points[0][1], +p.points.at(-1)[0].toFixed(4)], [MUSIC_DB - 12, 0.8]);
  }],
  ["quiet: the same fade rendered through the stage, sample by sample", async () => {
    const r = rig();
    const { stage, ctx, rate } = await offlineStage(3.6);
    flatInto(ctx, stage.musicIn(), 0);
    r.t = 0.5;
    const q = createQuiet({
      radio: r.radio, later: r.later, loadedAt: 0,
      stage: { now: () => r.t, levelAt: stage.levelAt, schedule: stage.schedule },
    });
    const done = q.fadeOut();
    r.advance(3);
    eq(await done, true, "done");
    const out = await rendered(ctx);
    const db = (t) => Math.max(SILENCE_DB, gainToDb(Math.abs(out[Math.round(t * rate)])));
    const makeup = db(0.4) - MUSIC_DB;          // the limiter's own, see above
    const w = worstOver(db, 0.3, 3.5);
    ok(w.w <= MAX_DB_PER_STEP + 0.05, `rendered: ${w.say}`);
    const [[, pausedAt]] = r.log;
    ok(db(pausedAt) <= SILENCE_DB + makeup + 0.05, `at the pause (${pausedAt.toFixed(2)} s) the music is at ${db(pausedAt).toFixed(1)} dBFS`);
    ok(pausedAt - 0.5 <= 1.35, `paused ${(pausedAt - 0.5).toFixed(2)} s after the cue was seen`);
  }],
  ["quiet: a plan that brings the music back mid-fade (a line ending) is faded again: never paused above silence", async () => {
    const r = rig();
    const done = r.quiet.fadeOut();
    r.advance(0.5);
    // voice.js giving back a duck it made before the quiet: up to MUSIC_DB over 0.8 s.
    r.stage.schedule("music", glidePlan(r.level(r.t + 0.05), MUSIC_DB, 0.8).points, r.t + 0.05);
    r.advance(4);
    eq(r.log.length, 1, "paused once");
    const [[, at, db]] = r.log;
    ok(db <= SILENCE_DB + 1e-6, `at ${db.toFixed(1)} dB (${at.toFixed(2)} s)`);
    const w = worstOver(r.level, 10, at);
    ok(w.w <= MAX_DB_PER_STEP + 1e-6, w.say);
    eq(await done, true);
  }],
  ["quiet: with the radio already quiet, fadeOut() resolves at once and moves nothing", async () => {
    const r = rig({ playing: false });
    eq(await r.quiet.fadeOut(), false);
    eq([r.plans.length, r.log.length, r.timers.length], [0, 0, 0]);
  }],
  ["quiet: a stage that is not running is silent already, so the radio pauses at once", async () => {
    const r = rig();
    r.stage.running = () => false;             // a context still waiting for its tap: its clock stands still
    eq(await Promise.race([r.quiet.fadeOut(), wait(200).then(() => "waiting")]), true, "done at once");
    eq([r.log.length, r.timers.length], [1, 0], "paused, with nothing left to wait for");
    eq(+r.level(r.t + 10).toFixed(6), MUSIC_DB, "and the bus left at its level");
  }],
  // FIX ROUND 1: a fade that settled at once (a stage not running, a bus
  // already silent) used to be kept as `fading` for good, so every later
  // quiet returned it and did nothing.
  ["quiet: a fade that settles at once does not latch: the next quiet still fades and pauses", async () => {
    const r = rig();
    r.stage.running = () => false;
    eq(await r.quiet.fadeOut(), true, "not running: paused at once");
    r.stage.running = () => true;
    r.radio.playing = true;
    const again = r.quiet.fadeOut();
    eq(r.plans.length, 1, "running, with the radio playing: a fade is laid");
    r.advance(3);
    eq(r.log.length, 2, "and the radio paused again");
    eq(await again, true);
    // A bus already at silence settles at once too.
    r.radio.playing = true;
    r.music = [[0, SILENCE_DB]];
    eq(await r.quiet.fadeOut(), true, "silent already: paused at once");
    r.radio.playing = true;
    r.music = [[0, MUSIC_DB]];
    const third = r.quiet.fadeOut();
    ok(r.plans.at(-1).points.at(-1)[1] === SILENCE_DB, "and the next quiet fades from MUSIC_DB again");
    r.advance(3);
    eq([r.log.length, await third], [4, true]);
  }],
  ["quiet: two asks during one fade are one fade", async () => {
    const r = rig();
    const a = r.quiet.fadeOut(), b = r.quiet.fadeOut();
    ok(a === b, "the same fade");
    eq(r.plans.length, 1);
    r.advance(3);
    eq(r.log.length, 1);
  }],

  // FIX ROUND 1: voice.js's own plan and quiet.js's own plan on one bus. A
  // line that ducked the music and ends while a quiet fade is taking it down
  // used to glide back up from its duck (-24) wherever the fade had got to: a
  // step up in the middle of the fade, and a second fade to follow.
  ["a line that ducked and ends during a quiet fade leaves the music to the fade: no glide up from its duck", async () => {
    const r = rig();
    const was = { fetch: voiceIO.fetch, schedule: voiceIO.schedule, levelAt: voiceIO.levelAt, now: voiceIO.now };
    try {
      voiceIO.fetch = async () => new Response(silentWav(0.3));
      voiceIO.schedule = (bus, points, at) => r.stage.schedule(bus, points, at);
      voiceIO.levelAt = (bus, t) => r.stage.levelAt(bus, t);
      voiceIO.now = () => r.t;
      const line = say("hardening-b-quiet-line");
      for (let i = 0; i < 2000 && !r.plans.length; i++) await tick();
      eq(r.plans.length, 1, "the line ducked the music");
      eq(r.plans[0].points.at(-1)[1], MUSIC_DB - LINE_DUCK_DB, "12 dB");
      r.t = 10.6;                       // the line is being said, the music down
      const quiet = r.quiet.fadeOut();  // `demo off`
      r.t = 10.9;                       // and the line ends, mid-fade
      await line;
      eq(r.plans.length, 2, "the line laid nothing when it ended: the fade has the music");
      r.advance(3);
      eq(await quiet, true);
      const [[, at, db]] = r.log;
      ok(db <= SILENCE_DB + 1e-6, `paused at ${db.toFixed(1)} dB`);
      const w = worstOver(r.level, 10, at);
      ok(w.w <= MAX_DB_PER_STEP + 1e-6, w.say);
      eq(r.plans.length, 3, "the duck, one fade, and the bus given back under the paused radio");
      eq(+r.level(r.t + 10).toFixed(6), MUSIC_DB, "at MUSIC_DB for the next play");
    } finally { Object.assign(voiceIO, was); }
  }],
  ["a line that ducks after a quiet has come and gone gives the music back as ever", async () => {
    const r = rig();
    r.quiet.fadeOut();
    r.advance(4);                       // faded, paused, and the bus given back
    r.radio.playing = true;
    const n = r.plans.length;
    const was = { fetch: voiceIO.fetch, schedule: voiceIO.schedule, levelAt: voiceIO.levelAt, now: voiceIO.now };
    try {
      voiceIO.fetch = async () => new Response(silentWav(0.3));
      voiceIO.schedule = (bus, points, at) => r.stage.schedule(bus, points, at);
      voiceIO.levelAt = (bus, t) => r.stage.levelAt(bus, t);
      voiceIO.now = () => r.t;
      const line = say("hardening-b-after-quiet");
      for (let i = 0; i < 2000 && r.plans.length === n; i++) await tick();
      r.t += 1;
      await line;
      eq(r.plans.length, n + 2, "ducked, and brought back");
      eq(+r.level(r.t + 10).toFixed(6), MUSIC_DB);
    } finally { Object.assign(voiceIO, was); }
  }],

  // ---- the quiet cue, seen in live.json ------------------------------------------------
  // FIX ROUND 1: the guard is the first sample the page sees, not the wall
  // clock. Only a quiet_at in that first sample is held against the page's
  // load time; after it, any quiet_at the page has not seen is a quiet.
  ["quiet_at: one in the page's first sample, no newer than the page, is from before it; after that each new one is a quiet, once", () => {
    const r = rig({ loadedAt: 1000 });
    eq([r.quiet.feed({ quiet_at: 999.5 }), r.quiet.feed({ quiet_at: 999.5 }), r.plans.length], [false, false, 0],
       "a quiet from before the page (a reload just after an exit), for the 10 s the world says it");
    eq([r.quiet.feed(null), r.quiet.feed({ quiet_at: null }), r.quiet.feed({ quiet_at: "1001" })],
       [false, false, false], "no quiet_at, or not a number: nothing");
    eq(r.quiet.feed({ quiet_at: 1001 }), true, "a new one fades");
    eq(r.plans.length, 1);
    eq(r.quiet.feed({ quiet_at: 1001 }), false, "the world says it for 10 s; it is acted on once");
    r.advance(3);
    eq(r.log.length, 1, "paused");
    r.radio.playing = true;
    eq(r.quiet.feed({ quiet_at: 1005 }), true, "a newer one is another quiet");
    r.advance(3);
    eq(r.log.length, 2);
  }],
  ["quiet_at: a wall clock that steps back cannot disable the fade", () => {
    const r = rig({ loadedAt: 1000 });
    eq(r.quiet.feed({ quiet_at: null }), false, "the page's first sample: no quiet");
    eq(r.quiet.feed({ quiet_at: 400 }), true, "a quiet stamped ten minutes before the page (the clock stepped back) still fades");
    r.advance(3);
    eq(r.log.length, 1);
    const s2 = rig({ loadedAt: 1000 });
    eq(s2.quiet.feed({ quiet_at: 999 }), false, "a stale one in the first sample");
    eq(s2.quiet.feed({ quiet_at: 990 }), true, "and then a different one, older still: a new cue all the same");
  }],
  ["quiet_at: one in the first sample that is newer than the page is a quiet", () => {
    const r = rig({ loadedAt: 1000 });
    eq(r.quiet.feed({ quiet_at: 1000.2 }), true);
    eq(r.plans.length, 1);
  }],
  ["quiet_at: a sample with no demo block is not the page's first sample", () => {
    const r = rig({ loadedAt: 1000 });
    eq([r.quiet.feed(null), r.quiet.feed(undefined), r.quiet.feed("demo")], [false, false, false]);
    eq(r.quiet.feed({ quiet_at: 999 }), false, "still the first sample: from before the page");
  }],
  ["register(D) follows the store's samples, and asks /api/live itself when the store has none", async () => {
    const r = rig({ loadedAt: 1000 });
    const store = new EventTarget();
    store.on = (what, fn) => { store.addEventListener(what, fn); return () => store.removeEventListener(what, fn); };
    store.live = null;
    let polls = 0, clockNow = 0, polled = null;
    const every = (fn) => { polled = fn; return 1; };
    const api = { live: async () => { polls++; return { demo: { quiet_at: 1002 } }; } };
    const q = register({ views: {} }, {
      radio: r.radio, stage: r.stage, later: r.later, loadedAt: 1000,
      store, api, every, clock: () => clockNow,
    });
    ok(q && typeof q.fadeOut === "function", "it hands back its controller");
    store.live = { demo: { quiet_at: 1001 } };
    store.dispatchEvent(new Event("live"));
    eq(r.plans.length, 1, "a sample from the store with a fresh quiet_at: fading");
    r.advance(3);
    eq(r.log.length, 1);
    // The store goes quiet (a screen without the fast clock): after a while
    // with nothing from it, the page asks for itself.
    r.radio.playing = true;
    polled();
    eq(polls, 0, "not while the store is current");
    clockNow = 5;
    polled();
    await tick(); await tick();
    eq(polls, 1, "asked");
    eq(r.plans.length, 3, "and its fresh quiet_at fades the music again");
    r.advance(3);
    eq(r.log.length, 2);
  }],
  ["fadeOut(), the tour's reset's, fades the controller register() made", async () => {
    const r = rig();
    const store = new EventTarget();
    store.on = (what, fn) => { store.addEventListener(what, fn); return () => store.removeEventListener(what, fn); };
    register({}, { radio: r.radio, stage: r.stage, later: r.later, store, api: { live: async () => null }, every: () => 1 });
    const done = fadeOut();
    eq(r.plans.length, 1, "on that controller's radio and stage");
    r.advance(3);
    eq([r.log.length, await done], [1, true]);
  }],

  // ---- the tour's reset ----------------------------------------------------------------
  ["the tour's reset fades the music out first, and only then pauses the radio and sends restart", async () => {
    const did = [];
    let faded = null;
    const reset = createReset({
      fade: () => { did.push("fade"); return new Promise((d) => { faded = d; }); },
      radio: { pause: () => did.push("radio.pause") },
      cue: (name) => { did.push(`cue:${name}`); return Promise.resolve(); },
      resetWork: () => { did.push("resetWork"); },
      restoreHome: () => { did.push("restoreHome"); return Promise.resolve(); },
    });
    const done = reset();
    await tick(); await tick();
    eq(did, ["fade"], "fading, and nothing else yet: the music does not cut, and the drive has not jumped");
    faded(true);
    await done;
    eq(did, ["fade", "radio.pause", "cue:restart", "resetWork", "restoreHome"], "then the rest, in order");
  }],
  ["a fade that never finishes holds the reset 2 s at most", async () => {
    const c = { now: 0, id: 0, timers: [] };
    const later = (fn, ms) => { const id = ++c.id; c.timers.push({ id, at: c.now + ms, fn }); return id; };
    const cancel = (id) => { c.timers = c.timers.filter((x) => x.id !== id); };
    const advance = (ms) => {
      c.now += ms;
      for (const x of c.timers.filter((y) => y.at <= c.now)) { cancel(x.id); x.fn(); }
    };
    const did = [];
    const reset = createReset({
      fade: () => { did.push("fade"); return new Promise(() => {}); },
      radio: { pause: () => did.push("radio.pause") },
      cue: (name) => { did.push(`cue:${name}`); return Promise.resolve(); },
      later, cancel,
    });
    const done = reset();
    await tick();
    advance(RESET_FADE_MS - 100);
    await tick(); await tick();
    eq(did, ["fade"], "waiting on the fade");
    advance(100);
    await done;
    eq(did, ["fade", "radio.pause", "cue:restart"], "and on after 2 s");
    eq([RESET_FADE_MS, c.timers.length], [2000, 0], "2 s, and no timer left behind");
  }],
  ["a fade that fails is logged, and the reset goes on", async () => {
    const did = [];
    const warn = console.warn;
    console.warn = () => {};
    try {
      for (const fade of [() => { throw new Error("no stage"); }, () => Promise.reject(new Error("no stage"))]) {
        did.length = 0;
        const reset = createReset({
          fade,
          radio: { pause: () => did.push("radio.pause") },
          cue: (name) => { did.push(`cue:${name}`); return Promise.resolve(); },
        });
        await Promise.race([reset(), wait(1000)]);
        eq(did, ["radio.pause", "cue:restart"]);
      }
    } finally { console.warn = warn; }
  }],
];

// A mono 16-bit wav of silence, as Piper writes them.
function silentWav(secs, rate = 22050) {
  const n = Math.round(secs * rate), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const s = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  s(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); s(8, "WAVE"); s(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); s(36, "data"); v.setUint32(40, n * 2, true);
  return buf;
}
