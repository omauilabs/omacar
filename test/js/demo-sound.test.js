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
import { MUSIC_DB, LIMIT_DB, audioContext, dbToGain, gainToDb } from "../js/audiobus.js";
import * as AB from "../js/audiobus.js";
import { SILENCE_DB, STEP_SECS, MAX_DB_PER_STEP, dbAt, planOnto, worstStep, rampPlan, glidePlan } from "../js/ramps.js";
import { voicesFor, DUCK_DB } from "../js/alertplayer.js";
import { playAlarm, playBark } from "../js/sounds.js";
import { SAY_AFTER } from "../demo/js/drowsy.js";
import * as VOICE from "../demo/js/voice.js";
import { createReset } from "../demo/js/tour.js";
// Read through the module, so these tests run (and fail for what they check)
// against a tree without it.
const QUIET = await import("../demo/js/quiet.js").catch(() => ({}));

const tick = () => new Promise((done) => setTimeout(done, 0));
const wait = (ms) => new Promise((done) => setTimeout(done, ms));

// ---- a stage of its own, rendered offline -----------------------------------------
// The same file under another URL is another module: this copy of audiobus.js
// builds its own stage, and building it while AudioContext hands out an
// OfflineAudioContext makes that the stage's context. The real graph -- the
// music bus, the alert gate, voiceIn(), the limiter -- rendered into memory.
// Polled, not awaited, as sounds.test.js explains.
const channel = new MessageChannel();
let woken = [];
channel.port1.onmessage = () => { const w = woken; woken = []; for (const f of w) f(); };
const yieldOnce = () => new Promise((r) => { woken.push(r); channel.port2.postMessage(0); });
let stages = 0;
async function offlineStage(secs, rate = 16000) {
  const ctx = new OfflineAudioContext(1, Math.ceil(secs * rate), rate);
  const real = window.AudioContext;
  window.AudioContext = function OfflineStage() { return ctx; };
  try {
    const stage = await import(`../js/audiobus.js?demo-sound-${++stages}`);
    stage.musicIn();
    if (stage.audioContext() !== ctx) throw new Error("the copy did not build on the offline context");
    return { stage, ctx, rate };
  } finally { window.AudioContext = real; }
}
async function rendered(ctx) {
  let buf = null, err = null;
  ctx.startRendering().then((b) => { buf = b; }, (e) => { err = e; });
  for (let i = 0; !buf && !err && i < 500000; i++) await yieldOnce();
  if (err) throw err;
  if (!buf) throw new Error("the offline render never finished");
  return buf.getChannelData(0);
}
function flatInto(ctx, node, db, from = 0, to = Infinity) {
  const s = ctx.createConstantSource();
  s.offset.value = dbToGain(db);
  s.connect(node);
  s.start(from);
  if (Number.isFinite(to)) s.stop(to);
  return s;
}
function peakOf(data, rate, from, to) {
  let m = 0;
  for (let i = Math.floor(from * rate); i < Math.min(data.length, Math.ceil(to * rate)); i++) m = Math.max(m, Math.abs(data[i]));
  return gainToDb(m);
}

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
  flatInto(ctx, line, VOICE.VOICE_DB, 0.5);
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
  r.quiet = QUIET.createQuiet({ radio: r.radio, stage: r.stage, later: r.later, loadedAt });
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
    ok(typeof AB.voiceIn === "function", "audiobus.js exports voiceIn()");
    const realFetch = VOICE.voiceIO.fetch, realSchedule = VOICE.voiceIO.schedule, realLevel = VOICE.voiceIO.levelAt;
    const ctx = audioContext();
    const proto = Object.getPrototypeOf(ctx);
    const to = [];
    try {
      VOICE.voiceIO.fetch = async () => new Response(silentWav(0.05));
      VOICE.voiceIO.schedule = () => true;
      VOICE.voiceIO.levelAt = () => MUSIC_DB;
      ctx.createGain = () => {
        const g = proto.createGain.call(ctx);
        const connect = g.connect.bind(g);
        g.connect = (node, ...rest) => { to.push(node); return connect(node, ...rest); };
        return g;
      };
      // An id of its own: voice.js keeps what it decodes, by id, for the page.
      await VOICE.say("hardening-b-routing");
    } finally {
      delete ctx.createGain;
      VOICE.voiceIO.fetch = realFetch; VOICE.voiceIO.schedule = realSchedule; VOICE.voiceIO.levelAt = realLevel;
    }
    eq(to.length, 1, "the line's one gain node, connected once");
    ok(to[0] === AB.voiceIn(), "into voiceIn()");
    ok(to[0] !== ctx.destination, "and not past the limiter");
  }],
  ["the voice keeps its level: -7 dB, the alert player's quietest line", () => eq(VOICE.VOICE_DB, -7)],
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
    ok(MUSIC_DB + DUCK_DB <= VOICE.ROOM_DB, "a bus the alert has ducked is one voice.js leaves alone");
    const t0 = 0.1, lineAt = t0 + SAY_AFTER + VOICE.DOWN_SECS;
    const { stage, ctx, rate } = await offlineStage(lineAt + 2.3);
    flatInto(ctx, stage.musicIn(), 0);
    stage.schedule("music", rampPlan(2, MUSIC_DB, MUSIC_DB + DUCK_DB).points, t0);
    const [bark] = voicesFor({ kind: "bark" }, 2, t0);
    stage.gateAlerts(bark.start, bark.end);
    playBark(bark, { ctx, out: stage.alertIn() });
    flatInto(ctx, stage.voiceIn(), VOICE.VOICE_DB, lineAt, lineAt + 2);
    const out = await rendered(ctx);
    const pk = peakOf(out, rate, 0, lineAt + 2.3);
    ok(pk <= LIMIT_DB, `the loudest sample is ${pk.toFixed(2)} dBFS`);
    ok(peakOf(out, rate, lineAt + 0.1, lineAt + 1.9) > -9, "and the line is in it");
  }],

  // ---- the quiet cue's fade ------------------------------------------------------------
  ["quiet: the music falls to silence with no 100 ms moving more than 3 dB, and then the radio pauses", async () => {
    ok(typeof QUIET.createQuiet === "function", "demo/js/quiet.js exports createQuiet()");
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
    const q = QUIET.createQuiet({
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
  ["quiet: two asks during one fade are one fade", async () => {
    const r = rig();
    const a = r.quiet.fadeOut(), b = r.quiet.fadeOut();
    ok(a === b, "the same fade");
    eq(r.plans.length, 1);
    r.advance(3);
    eq(r.log.length, 1);
  }],

  // ---- the quiet cue, seen in live.json ------------------------------------------------
  ["quiet_at: one from before the page loaded is not acted on; a fresh one is, once; a newer one again", () => {
    const r = rig({ loadedAt: 1000 });
    eq([r.quiet.feed({ quiet_at: 999.5 }), r.quiet.feed({ quiet_at: 1000 }), r.plans.length], [false, false, 0],
       "a quiet from before the page (a reload just after `demo off` failed) is somebody else's");
    eq([r.quiet.feed({}), r.quiet.feed(null), r.quiet.feed({ quiet_at: null }), r.quiet.feed({ quiet_at: "1001" })],
       [false, false, false, false], "no quiet_at, or not a number: nothing");
    eq(r.quiet.feed({ quiet_at: 1001 }), true, "a fresh one fades");
    eq(r.plans.length, 1);
    eq(r.quiet.feed({ quiet_at: 1001 }), false, "the world says it for 10 s; it is acted on once");
    r.advance(3);
    eq(r.log.length, 1, "paused");
    r.radio.playing = true;
    eq(r.quiet.feed({ quiet_at: 1005 }), true, "a newer one is another quiet");
    r.advance(3);
    eq(r.log.length, 2);
  }],
  ["register(D) follows the store's samples, and asks /api/live itself when the store has none", async () => {
    ok(typeof QUIET.register === "function", "demo/js/quiet.js exports register()");
    const r = rig({ loadedAt: 1000 });
    const store = new EventTarget();
    store.on = (what, fn) => { store.addEventListener(what, fn); return () => store.removeEventListener(what, fn); };
    store.live = null;
    let polls = 0, clockNow = 0, polled = null;
    const every = (fn) => { polled = fn; return 1; };
    const api = { live: async () => { polls++; return { demo: { quiet_at: 1002 } }; } };
    const q = QUIET.register({ views: {} }, {
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
  ["fadeOut() is exported, for the tour's reset", () => ok(typeof QUIET.fadeOut === "function", "demo/js/quiet.js exports fadeOut(ms)")],

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
