// ONE OUTPUT STAGE FOR EVERYTHING OMACAR PLAYS.
//
// Sound reaches the car through an AUX cable into the car's own radio, whose
// volume knob this app cannot see. So the only promise that holds whatever the
// knob says is a relative one: music sits 12 dB below full scale, alerts may
// use all of it, and an alert always has 12 dB of headroom over music. The
// knob scales both alike. lib/audio.py holds the Surface itself at 100%.
//
//   radio <audio> -> its analyser -> music gain (-12 dB) --+
//   chime, bark, alarm, voice ----> alert gate (closed) ---+-> limiter -> out
//
// The music bus moves by ramps (linearRampToValueAtTime) shaped by ramps.js,
// so no step is ever audible. The alert bus is a GATE, NEVER A FADER: every
// alert sound carries its own envelope from silence and back
// (alertplayer.js), the gate opens only when the next sound starts at
// silence, and it closes only after the last sound has faded to silence.

export const MUSIC_DB = -12;
export const ALERT_MAX_DB = 0;
export const FLOOR_DB = -60;        // quiet enough to count as silence

export function dbToGain(db) { return db <= -120 ? 0 : Math.pow(10, db / 20); }
export function gainToDb(g) { return g > 0 ? 20 * Math.log10(g) : -Infinity; }
export function headroomDb(musicDb = MUSIC_DB, alertDb = ALERT_MAX_DB) { return alertDb - musicDb; }

let ctx = null;
const node = {};

function ensure() {
  if (ctx) return;
  const AC = window.AudioContext || window.webkitAudioContext;
  ctx = new AC();
  node.music = ctx.createGain();
  node.music.gain.value = dbToGain(MUSIC_DB);
  node.alert = ctx.createGain();
  node.alert.gain.value = 0;
  // Music plus a full-scale alert can pass 0 dBFS by a decibel or two; the
  // limiter takes that off rather than letting the output clip.
  node.limit = ctx.createDynamicsCompressor();
  node.limit.threshold.value = -1;
  node.limit.knee.value = 0;
  node.limit.ratio.value = 20;
  node.limit.attack.value = 0.003;
  node.limit.release.value = 0.25;
  node.meter = ctx.createAnalyser();
  node.meter.fftSize = 2048;
  node.music.connect(node.limit);
  node.alert.connect(node.limit);
  node.limit.connect(node.meter);
  node.meter.connect(ctx.destination);
}

export function audioContext() { ensure(); return ctx; }
export function musicIn() { ensure(); return node.music; }
export function alertIn() { ensure(); return node.alert; }

// A context made without a tap starts suspended; the kiosk's autoplay flag
// lets it run, and Begin's tap resumes it everywhere else.
export async function resume() {
  ensure();
  if (ctx.state === "suspended") {
    try { await ctx.resume(); } catch { /* it needs a tap first */ }
  }
  return ctx.state;
}

// Where a bus is now, in dB.
export function currentDb(bus) { ensure(); return gainToDb(node[bus].gain.value); }

// Run a ramp plan's points ([seconds, dB] pairs from ramps.js) on a bus from
// `at` (the context's clock). `append` continues after what is already
// scheduled instead of replacing it.
export function schedule(bus, points, at, append = false) {
  ensure();
  const g = node[bus].gain;
  if (!append) {
    if (g.cancelAndHoldAtTime) {
      g.cancelAndHoldAtTime(at);
    } else {
      // NO FALLBACK. cancelAndHoldAtTime is the only way to ask "what would
      // this automation's value have been at `at`", and without it there is
      // no correct way to cancel a ramp that is already in flight:
      // cancelScheduledValues(at) drops the ramp's end event, so the value
      // falls back to whatever the PREVIOUS event set until `at`, and
      // setValueAtTime(g.value, at) then writes g.value read NOW -- not the
      // value the automation would actually have at `at`. Doing that is two
      // steps of a few dB each, exactly the kind of jump this stage exists
      // to prevent, and it gets WORSE the further `at` is in the future.
      // So a browser without cancelAndHoldAtTime does not reschedule at
      // all: this call is skipped and the bus is left exactly where its
      // last scheduled plan already has it, mid-ramp or not, rather than
      // stepping the level to "fix" it.
      return;
    }
  }
  for (const [t, db] of points) g.linearRampToValueAtTime(dbToGain(db), at + t);
}

// Open the alert gate at `openAt`, when the sound starting then is at silence,
// and close it at `closeAt`, when the last sound will have faded to silence
// (Infinity: not yet known). A close already scheduled after `openAt` is
// cancelled, so a sound that starts during another's release keeps the gate
// open under both.
export function gateAlerts(openAt, closeAt) {
  ensure();
  const g = node.alert.gain;
  g.cancelScheduledValues(openAt);
  g.setValueAtTime(1, openAt);
  if (Number.isFinite(closeAt)) g.setValueAtTime(0, closeAt);
}

// The output's level in dBFS, to check the stage is carrying anything.
export function outputDb() {
  ensure();
  const buf = new Float32Array(node.meter.fftSize);
  node.meter.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  return gainToDb(Math.sqrt(sum / buf.length));
}
