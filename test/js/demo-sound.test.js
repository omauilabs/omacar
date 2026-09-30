// The meetup demo's sound, hardening B (doc/design/2026-09-30-meetup-demo-hardening.md):
// the voice through the audio stage (demo/js/voice.js into share/js/audiobus.js
// voiceIn()).
//
// NOTHING HERE HAS A SPEAKER. The stage is rendered into OfflineAudioContexts
// (a second copy of audiobus.js, built on one; see offlineStage()), and the
// page's own context is only ever spied on.
import { eq, ok } from "./assert.js";
import { MUSIC_DB, LIMIT_DB, audioContext, dbToGain, gainToDb } from "../js/audiobus.js";
import * as AB from "../js/audiobus.js";
import { rampPlan } from "../js/ramps.js";
import { voicesFor, DUCK_DB } from "../js/alertplayer.js";
import { playAlarm, playBark } from "../js/sounds.js";
import { SAY_AFTER } from "../demo/js/drowsy.js";
import * as VOICE from "../demo/js/voice.js";

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
