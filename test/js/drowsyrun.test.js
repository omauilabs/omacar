import { eq, ok } from "./assert.js";
import {
  gateOf, chipOf, rotationFor, testMayRun, voiceInstalled, showAux, KPH_PER_MPH,
  createDrowsy, createTracker, setTestGate, drowsy, TRACKER_RETRY_SECS,
} from "../js/drowsyrun.js";
import * as player from "../js/alertplayer.js";
import * as audiostate from "../js/audiostate.js";

const cfg = { min_speed_mph: 30 };
// A real gate, not the brief's { moving: true }: the chip now asks whether a car is there.
const moving = gateOf({ connected: true, values: { SPEED: 50 } }, cfg);

// ---- the rig: drowsy mode's engine with everything outside it handed in.
// No wall clock anywhere: `r.t` is the frame clock, moved only by the test.
const CFG = async () => (await fetch("../data/drowsy.json")).json();
const kind = (c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind);
const r3 = (x) => +x.toFixed(3);
const yieldTask = () => new Promise((done) => setTimeout(done, 0));

async function rig(over = {}) {
  const r = {
    t: 1000, live: null, cams: { running: true, roles: { cabin: { live: true } } },
    settings: Object.assign(await CFG(), over.settings || {}),
    installed: over.installed || (() => false), asked: [], posts: [], log: [],
    timers: [], nextId: 1, onFrame: null, watches: 0, stops: 0, audio: null,
    liveCalls: 0, hang: false, fail: false, drops: 0,
  };
  const fake = {
    play(cues, out) { r.log.push(["play", cues.map(kind), out.level]); return Promise.resolve(); },
    setConfig(c) { r.log.push(["setConfig", c]); },
    setName(fn) { r.name = fn; },
  };
  r.player = fake;
  r.eng = createDrowsy({
    clock: () => r.t, wall: () => 1.8e9 + r.t, hour: () => over.hour ?? 14,
    getJSON: async (p, opts) => {
      if (p === "/api/live") {
        r.liveCalls++;
        if (r.fail) throw new Error("500");
        // A hung request: it ends only if the caller aborts it.
        if (r.hang) {
          return new Promise((_, no) => {
            if (opts && opts.signal) opts.signal.addEventListener("abort", () => no(new Error("aborted")));
          });
        }
        return r.live;
      }
      if (p === "/api/cams") return r.cams;
      if (p === "/api/drowsy") return JSON.parse(JSON.stringify(r.settings));
      throw new Error("404 " + p);
    },
    postJSON: async (p, body) => { r.posts.push([p, body]); return {}; },
    player: () => fake,
    voiceInstalled: async (name) => { r.asked.push(name); return r.installed(name); },
    onAudio: (fn) => { r.audio = fn; fn(null); return () => {}; },
    tracker: over.tracker || {
      start: async ({ onFrame }) => {
        r.watches++;
        r.onFrame = onFrame;
        return { stop() { r.stops++; r.onFrame = null; } };
      },
      drop() { r.drops++; },
    },
    later: (fn, ms) => { const id = r.nextId++; r.timers.push({ id, at: r3(r.t + ms / 1000), fn }); return id; },
    cancel: (id) => { r.timers = r.timers.filter((x) => x.id !== id); },
    every: () => 0,
    canvas: over.canvas || null,
  });
  // The car: a sample from /api/live, then one poll. null is a dropped link.
  r.drive = async (kph, extra) => {
    r.live = kph === null ? { connected: false } : Object.assign({ connected: true, values: { SPEED: kph } }, extra || {});
    await r.eng.pollLive();
    await r.eng.idle();
  };
  // One /api/live poll, as the page's timer fires it. While the server hangs
  // it is not waited for: only a timer the rig fires later can end it.
  r.poll = async () => {
    const p = r.eng.pollLive();
    if (!r.hang) { await p; await r.eng.idle(); }
  };
  // `secs` of cabin frames at 10 fps, each stamped on the frame clock as
  // facewatch.js stamps it, with the page's half-second /api/live poll
  // between every fifth, and any timers that fall due; fn(i) overrides a
  // frame's fields.
  r.frames = async (secs, fn) => {
    for (let i = 0; i < Math.round(secs * 10); i++) {
      r.advance(0.1);
      if (i % 5 === 4) await r.poll();
      if (r.onFrame) r.onFrame(Object.assign({ t: r.t, face: true, blink: 0.1, jaw: 0.1, pitch: 0 }, fn ? fn(i) : {}));
    }
  };
  // `secs` of polls with no frames, every half second unless `every` says
  // otherwise, and any test timers due.
  r.polls = async (secs, every = 0.5) => {
    for (let i = 0; i < Math.round(secs / every); i++) { r.advance(every); await r.eng.pollLive(); await r.eng.idle(); }
  };
  r.advance = (secs) => {
    const end = r3(r.t + secs);
    for (;;) {
      const due = r.timers.filter((x) => x.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      r.timers = r.timers.filter((x) => x !== due);
      r.t = Math.max(r.t, due.at);
      due.fn();
    }
    r.t = end;
  };
  r.plays = () => r.log.filter((x) => x[0] === "play").map((x) => x[1]);
  r.calibrate = async () => { await r.drive(100); await r.frames(61); };
  await r.eng.start();
  await r.eng.idle();
  return r;
}

// The review's lead-in (Task 9 review, I1): calibrated at speed, then 60 s at
// 40 km/h with a 0.7 s closure every 2 s -- PERCLOS about 0.35, no closure
// long enough for Level 2, and nothing raised below the gate -- then above
// it. Returns the crossing's time.
async function perclosLead(r) {
  await r.calibrate();
  await r.drive(40);
  await r.frames(60, (i) => ({ blink: i % 20 < 7 ? 0.9 : 0.1 }));
  const m = r.eng.state.measures;
  ok(m.perclos > 0.3 && r.eng.state.level === 0, `below the gate: PERCLOS ${m.perclos}, level ${r.eng.state.level}`);
  await r.drive(100);
  return r.t;
}
// Every change of level from now on, as [seconds after `from`, level, trigger].
function watchRaises(r, from) {
  const out = [];
  let was = r.eng.state.level;
  r.eng.on((st) => { if (st.level !== was) { was = st.level; out.push([r3(st.t - from), st.level, st.trigger]); } });
  return out;
}

// A live MJPEG stream of `n` pictures (fix round 1, I3), for the real
// watchCabin loop: one picture per read, then open until aborted.
const enc = (x) => new TextEncoder().encode(x);
const mjpegPart = (n) => {
  const jpg = new Uint8Array([0xff, 0xd8, n & 0xff, 0xff, 0xd9]);
  const head = enc(`--omacarframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpg.length}\r\n\r\n`);
  const out = new Uint8Array(head.length + jpg.length + 2);
  out.set(head);
  out.set(jpg, head.length);
  out.set(enc("\r\n"), head.length + jpg.length);
  return out;
};
const liveStream = (n) => (signal) => {
  let i = 0;
  return Promise.resolve({ ok: true, body: { getReader: () => ({
    read: () => (i < n ? Promise.resolve({ value: mjpegPart(i++), done: false })
      : new Promise((done) => signal.addEventListener("abort", () => done({ value: undefined, done: true })))),
  }) } });
};
const turns = async (done = () => false) => {
  for (let i = 0; i < 300 && !done(); i++) await new Promise((res) => setTimeout(res, 0));
};

export default [
  // ---- the brief's
  ["30 mph is 48.28 km/h: below it the gate is shut", () =>
    eq([gateOf({ connected: true, values: { SPEED: 48.2 } }, cfg).active,
        gateOf({ connected: true, values: { SPEED: 48.3 } }, cfg).active], [false, true])],
  ["no car is neither active nor parked: a dropped link must not clear an alert", () =>
    eq([gateOf({ connected: false }, cfg).active, gateOf({ connected: false }, cfg).parked], [false, false])],
  ["stationary and connected is parked", () => eq(gateOf({ connected: true, values: { SPEED: 0 } }, cfg).parked, true)],
  // Amended before review (controller, 2026-09-29): a fifth chip text, for
  // no car data. The brief's partial gate ({ moving: false }) is now a real
  // connected-at-0 gate, since "parked" needs a car that said 0 km/h.
  ["the chip says one of five things", () =>
    eq([chipOf({ enabled: false }),
        chipOf({ enabled: true, gate: gateOf({ connected: true, values: { SPEED: 0 } }, cfg) }),
        chipOf({ enabled: true, gate: moving, cabinLive: false }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: true } }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: false } }),
        chipOf({ enabled: true, gate: gateOf({ connected: false }, cfg) })],
       ["Off", "Paused · parked", "Can't see you", "Can't see you", "Watching", "Paused · no car data"])],
  ["no car data is not parked: a dropped link, an unreadable speed, or no sample at all", () => {
    const chip = (s) => chipOf({ enabled: true, gate: s === undefined ? null : gateOf(s, cfg), cabinLive: true,
                                 measures: { faceLost: false } });
    eq([chip({ connected: false }), chip({ connected: true, values: {} }), chip({ connected: true, values: { SPEED: "x" } }),
        chip(null), chip(undefined), chip({ connected: true, values: { SPEED: 0 } })],
       ["Paused · no car data", "Paused · no car data", "Paused · no car data", "Paused · no car data",
        "Paused · no car data", "Paused · parked"]);
  }],
  ["the simulator's numbers never open the gate, and the chip says Off", () => {
    const g = gateOf({ connected: true, simulated: true, values: { SPEED: 100 } }, cfg);
    eq([g.active, g.moving, g.parked, chipOf({ enabled: true, gate: g, cabinLive: true, measures: { faceLost: false } })],
       [false, false, false, "Off"]);
  }],
  ["Test the alerts runs only while connected and stopped, and stops when that ends", () =>
    eq([{ connected: true, values: { SPEED: 0 } }, { connected: true, values: { SPEED: 8 } }, { connected: false },
        { connected: true, simulated: true, values: { SPEED: 0 } }].map((s) => testMayRun(gateOf(s, cfg))),
       [true, false, false, false])],
  ["the voice leaves the rotation when its clips are not installed", () =>
    eq([rotationFor(["bark", "voice", "alarm"], false), rotationFor(["bark", "voice", "alarm"], true), rotationFor(["voice"], false)],
       [["bark", "alarm"], ["bark", "voice", "alarm"], ["alarm"]])],

  // ---- 1. the rotation is alertplayer.js's, and it reaches the ladder
  ["rotationFor and voiceInstalled are alertplayer.js's own, not copies", () =>
    eq([rotationFor === player.rotationFor, voiceInstalled === player.voiceInstalled], [true, true])],
  ["with no clip for the name, a Level 2 never plays the voice: the ladder was built with the filtered rotation", async () => {
    // Only the named clip is installed; the owner's name is "" (no name), so
    // the clip the player would look for, voice-l2, is missing.
    const r = await rig({ settings: { name: "" }, installed: (n) => n === "James" });
    eq([r.asked, r.eng.state.rotation], [[""], ["bark", "alarm"]]);
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    await r.frames(12);
    eq(r.plays().slice(0, 3), [["duck", "bark"], ["alarm"], ["bark"]]);
  }],
  ["and a settings reload passes the filtered rotation to setConfig: the voice goes when its clip does", async () => {
    const r = await rig({ settings: { name: "James" }, installed: (n) => n === "James" });
    eq(r.eng.state.rotation, ["bark", "voice", "alarm"]);
    r.settings.name = "";
    await r.eng.reload();
    eq([r.asked, r.eng.state.rotation], [["James", ""], ["bark", "alarm"]]);
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    await r.frames(12);
    eq(r.plays().slice(0, 3), [["duck", "bark"], ["alarm"], ["bark"]]);
  }],
  ["and comes back with it: the voice is in the rotation once its clip is there", async () => {
    const r = await rig({ settings: { name: "" }, installed: (n) => n === "James" });
    r.settings.name = "James";
    await r.eng.reload();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    await r.frames(12);
    eq(r.plays().slice(0, 3), [["duck", "bark"], ["voice:l2"], ["alarm"]]);
  }],

  // ---- 2. the page's one alert player
  ["drowsy mode plays through the page's one alert player, and never builds another", () =>
    eq([drowsy.player() === player.alertPlayer(), drowsy.player() === drowsy.player()], [true, true])],
  ["the player gets the same settings as the ladder, and the name from them", async () => {
    const r = await rig({ settings: { name: "" } });
    const sets = r.log.filter((x) => x[0] === "setConfig");
    eq([sets.length, sets[0][1]._scaled === undefined, sets[0][1].level2.voice_slot_secs, r.name()], [1, true, 7, ""]);
  }],

  // ---- 3. one clock: the frame's own time, and steps that never run back
  ["a frame's step is at the frame's own time, m.t, not the clock's now", async () => {
    const r = await rig();
    await r.calibrate();
    r.t = 1999.9;
    await r.poll();                               // the gate was answered a moment ago
    r.t = 2000;                                   // and the clock has moved on since this frame
    r.onFrame({ t: 1999.95, face: true, blink: 0.1, jaw: 0.1, pitch: 0 });
    eq([r.eng.state.t, r.eng.state.measures.t], [1999.95, 1999.95]);
  }],
  ["a step between frames is on the same clock, and never runs backwards", async () => {
    const r = await rig();
    await r.calibrate();
    const at = r.t;
    r.t = r3(at - 0.05);                          // a poll stamped just before the last frame
    await r.eng.pollLive();
    eq(r.eng.state.t, at);
    r.t = r3(at + 0.4);
    await r.eng.pollLive();
    eq(r.eng.state.t, r3(at + 0.4));
  }],
  ["a duplicate frame is ignored: no step, and a closure still reaches Level 2 with every frame fed twice", async () => {
    const r = await rig();
    await r.calibrate();
    const before = [r.eng.state.t, r.eng.state.measures];
    r.onFrame({ t: r.t, face: true, blink: 0.9, jaw: 0.1, pitch: 0 });
    eq([r.eng.state.t, r.eng.state.measures === before[1]], [before[0], true]);
    for (let i = 0; i < 12; i++) {
      r.t = r3(r.t + 0.1);
      const f = { t: r.t, face: true, blink: 0.9, jaw: 0.1, pitch: 0 };
      r.onFrame(f);
      r.onFrame({ ...f });
    }
    eq([r.eng.state.level, r.eng.state.trigger], [2, "closed"]);
  }],
  ["a camera gap is a discontinuity, and a sounding alert repeats through it on the clock", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    const n = r.plays().length;
    await r.polls(12);                            // no frames for 12 s
    ok(r.plays().length - n >= 2, `repeats during the gap: ${JSON.stringify(r.plays().slice(n))}`);
    eq(r.eng.state.level, 2);
    await r.frames(0.1, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures.discontinuity, r.eng.state.measures.closedFor], [true, 0]);
  }],

  // ---- 4. the gate: active, parked, simulated, and the stop clock
  ["active is above 48.28 km/h, not at it", () => {
    const at = 30 * KPH_PER_MPH;
    eq([gateOf({ connected: true, values: { SPEED: at } }, cfg).active,
        gateOf({ connected: true, values: { SPEED: at + 0.001 } }, cfg).active], [false, true]);
  }],
  ["a dropped link, however long, neither clears an alert nor stops it; connected at 0 km/h does", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    await r.drive(null);
    const n = r.plays().length;
    await r.polls(400);                           // longer than a 5-minute stop
    const since = r.plays().slice(n);
    ok(since.length >= 60 && !since.some((c) => c.includes("fade")), `through the drop: ${JSON.stringify(since.slice(0, 4))}...`);
    eq([r.eng.state.level, r.eng.state.gate.parked], [2, false]);
    await r.drive(0);
    eq([r.eng.state.level, r.plays().at(-1)], [0, ["fade"]]);
  }],
  ["simulated numbers never make drowsy mode active: nothing is fed, nothing alerts, the chip says Off", async () => {
    const r = await rig();
    r.eng.preview = true;                         // so the camera is watched at all
    await r.drive(100, { simulated: true });
    await r.frames(61);
    await r.frames(3, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures, r.eng.state.level, r.eng.state.chip, r.plays()], [null, 0, "Off", []]);
  }],
  ["after Level 3 the banner stays until 2 minutes connected and stopped, by the stop clock: a dropped link starts that count over", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(2.2, () => ({ blink: 0.9 }));
    eq([r.eng.state.level, r.eng.state.banner], [3, true]);
    await r.drive(0);
    eq([r.eng.state.level, r.eng.state.banner], [0, true]);
    await r.polls(90);
    await r.drive(null);
    await r.polls(60);
    await r.drive(0);
    await r.polls(100);
    eq(r.eng.state.banner, true, "90 s stopped, a dropped link, then 100 s stopped");
    await r.polls(25);
    eq(r.eng.state.banner, false, "125 s stopped since the link came back");
  }],
  ["nor does a dropped link count toward the 2 hours since a stop: that notice comes after 2 h connected", async () => {
    const r = await rig();
    await r.drive(100);
    await r.polls(100 * 60, 10);
    await r.drive(null);
    await r.polls(30 * 60, 10);
    await r.drive(100);
    await r.polls(19 * 60, 10);
    const early = r.eng.state.level;
    await r.polls(2 * 60, 10);
    eq([early, r.eng.state.level, r.eng.state.trigger], [0, 1, "since-stop"]);
  }],

  // ---- the /api/live poll: a timeout, and never a stale "active" (controller, 2026-09-29)
  ["a hung /api/live never leaves the gate at a stale 'active': no car data within 2 s, given up at 5 s", async () => {
    const r = await rig();
    await r.calibrate();
    r.hang = true;
    let finished = false;
    r.eng.pollLive().then(() => { finished = true; });
    await r.frames(2.5);
    const g = r.eng.state.gate;
    const stale = [g.connected, g.active, r.eng.state.chip];
    await r.frames(1.2, () => ({ blink: 0.9 }));  // a closure with no speed known
    const level = r.eng.state.level;
    r.advance(2);                                 // 5.7 s since that poll went out
    await yieldTask();
    const gaveUp = finished;
    r.hang = false;
    await r.drive(100);                           // an answer again: the gate opens again
    await r.frames(2);
    await r.frames(1.2, () => ({ blink: 0.9 }));
    eq([stale, level, gaveUp, r.eng.state.level], [[false, false, "Paused · no car data"], 0, true, 2]);
  }],
  ["an alert already sounding goes on through a hung poll, and 'I'm awake' still clears it", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    const n = r.plays().length;
    r.hang = true;
    r.eng.pollLive();
    await r.frames(12);
    const since = r.plays().slice(n);
    ok(since.length >= 2 && !since.some((c) => c.includes("fade")), `through the hang: ${JSON.stringify(since)}`);
    eq([r.eng.state.level, r.eng.state.gate.connected], [2, false]);
    r.eng.tap();
    eq([r.eng.state.level, r.plays().at(-1)], [0, ["fade"]]);
    r.hang = false;
    r.advance(5);
    await yieldTask();
  }],
  ["with no frames at all, the half-second poll still steps while a request is out: the gate goes stale on the clock", async () => {
    const r = await rig();
    await r.drive(100);
    r.hang = true;
    const got = [];
    for (let i = 0; i < 6; i++) { r.advance(0.5); r.eng.pollLive(); got.push(r.eng.state.gate.connected); }
    eq(got, [true, true, true, true, false, false]);
    r.hang = false;
    r.advance(5);
    await yieldTask();
  }],
  ["a failed poll is no car data at once, but not a dropped link: it restarts nothing", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(40);
    r.fail = true;
    await r.eng.pollLive();
    const unknown = [r.eng.state.gate.active, r.eng.state.chip];
    r.fail = false;
    await r.drive(100);
    await r.frames(0.1);
    eq([unknown, r.eng.state.measures.t, r.eng.state.measures.discontinuity], [[false, "Paused · no car data"], r.t, false]);
  }],

  // ---- 5. which frames feed the measures (refined before review, controller 2026-09-29):
  // every frame while the car rolls, below the gate included; a restart only
  // after 10 s stopped, a dropped link, or a camera gap.
  ["stop-and-go at 25-35 mph keeps PERCLOS available after its first 30 s, with no restart", async () => {
    const r = await rig();
    await r.calibrate();
    const from = r.t, seen = [];
    r.eng.on((st) => { const m = st.measures; if (m && m.t > from && seen.at(-1) !== m) seen.push(m); });
    for (let i = 0; i < 24; i++) {                // 2 minutes, 5 s at each speed
      await r.drive(i % 2 ? 56 : 40);
      await r.frames(5);
    }
    const late = seen.filter((m) => m.t > from + 31);
    eq([seen.some((m) => m.discontinuity), late.length > 800, late.every((m) => m.perclos !== null)], [false, true, true]);
  }],
  ["a stop shorter than 10 s is not a restart: PERCLOS carries on", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(40);
    await r.drive(0);
    await r.frames(6);
    await r.drive(40);
    await r.frames(0.1);
    const m = r.eng.state.measures;
    eq([m.t, m.discontinuity, m.perclos !== null], [r.t, false, true], "the frame just fed, after the stop");
  }],
  ["a stop of 10 s restarts the measures: only its first 10 s were fed, and PERCLOS after moving off starts over", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(40);
    const fed = r.eng.state.measures;
    ok(fed.perclos !== null && fed.perclos < 0.05, `PERCLOS before the stop ${fed.perclos}`);
    r.eng.preview = true;                         // the settings sheet keeps the camera watched
    await r.drive(0);
    const stop = r.t;
    await r.frames(60, () => ({ blink: 0.9 }));
    const last = r.eng.state.measures;
    ok(last.t <= stop + 10.05, `a frame ${r3(last.t - stop)} s into the stop was fed`);
    eq(r.eng.state.frame.blink, 0.9, "parked: the preview still gets the frame");
    r.eng.preview = false;
    await r.drive(100);
    await r.frames(0.1);
    eq([r.eng.state.measures.discontinuity, r.eng.state.measures.perclos], [true, null], "moving off restarts the window");
    await r.frames(35);
    eq([r.eng.state.measures.perclos, r.eng.state.level], [0, 0]);
  }],
  ["creeping at 3 km/h or less is still stopped: after a long stop it feeds nothing until the car moves", async () => {
    const r = await rig();
    await r.calibrate();
    await r.drive(0);
    await r.polls(11);
    r.eng.preview = true;
    await r.drive(3);
    const held = r.eng.state.measures;
    await r.frames(2);
    const creeping = r.eng.state.measures === held;
    await r.drive(4);
    await r.frames(0.1);
    eq([creeping, r.eng.state.measures.t, r.eng.state.measures.discontinuity], [true, r.t, true]);
  }],
  ["a dropped link restarts the measures too, however short, with the camera still watched", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(40);
    r.eng.preview = true;                         // the watcher keeps running through the drop
    await r.drive(null);
    await r.frames(0.5);
    await r.drive(100);
    await r.frames(0.1);
    eq([r.watches, r.eng.state.measures.t, r.eng.state.measures.discontinuity, r.eng.state.measures.perclos],
       [1, r.t, true, null]);
  }],
  ["the cabin picture going and coming back is a camera gap, however short", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(40);
    r.cams = { running: true, roles: { cabin: { live: false } } };
    await r.eng.pollCams();
    r.cams = { running: true, roles: { cabin: { live: true } } };
    await r.eng.pollCams();
    await r.eng.idle();
    await r.frames(0.1);
    eq([r.watches, r.eng.state.measures.t, r.eng.state.measures.discontinuity], [2, r.t, true]);
  }],
  ["below the gate frames are fed, but nothing below the gate raises an alert", async () => {
    const r = await rig();
    await r.calibrate();
    await r.drive(40);                            // moving, under 30 mph
    const t0 = r.eng.state.measures.t;
    await r.frames(3, () => ({ blink: 0.9 }));
    const m = r.eng.state.measures;
    eq([m.t > t0, m.closedFor >= 2.5, r.eng.state.level], [true, true, 0]);
    await r.drive(100);                           // the same closure goes on above the gate: no new evidence
    await r.frames(1, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 0);
    await r.frames(2);
    await r.frames(1.2, () => ({ blink: 0.9 }));  // a new closure, above the gate
    eq([r.eng.state.level, r.eng.state.trigger], [2, "closed"]);
  }],
  ["a dip below the gate is not a pause: a closure that spans it is one closure", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(0.6, () => ({ blink: 0.9 }));
    await r.drive(45);                            // under the gate for 0.3 s
    await r.frames(0.3, () => ({ blink: 0.9 }));
    await r.drive(100);
    await r.frames(0.2, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures.discontinuity, r.eng.state.level, r.eng.state.trigger], [false, 2, "closed"]);
  }],

  // ---- fix round 1, I1: a PERCLOS window built below the gate is not new
  // evidence when the car crosses 30 mph (controller: option b).
  ["a PERCLOS window built below the gate raises nothing at the crossing while the eyes stay open", async () => {
    const r = await rig();
    const cross = await perclosLead(r);
    const raises = watchRaises(r, cross);
    await r.frames(65);
    eq(raises, []);
  }],
  ["with the closures going on above the gate, PERCLOS raises once the window has turned over", async () => {
    const r = await rig();
    const cross = await perclosLead(r);
    const raises = watchRaises(r, cross);
    await r.frames(70, (i) => ({ blink: i % 20 < 7 ? 0.9 : 0.1 }));
    const [at, level, trigger] = raises[0] || [];
    ok(at >= 60 && at <= 60 + 3 + 0.6, `first raise at +${at} s after the crossing (window 60 s, hold 3 s)`);
    eq([level, trigger], [2, "perclos"]);
  }],
  ["and one long closure after the crossing raises at once: the closure triggers are unchanged", async () => {
    const r = await rig();
    const cross = await perclosLead(r);
    const raises = watchRaises(r, cross);
    await r.frames(2.5);
    await r.frames(1.2, () => ({ blink: 0.9 }));
    const [at, level, trigger] = raises[0] || [];
    ok(raises.length === 1 && at > 2.5 && at < 4, `raises ${JSON.stringify(raises)}`);
    eq([level, trigger], [2, "closed"]);
  }],

  // ---- fix round 1, I3: a tracker failing on every frame is shown, stopped and reloaded
  ["a tracker that throws on every frame: the error is shown, the chip says drowsy mode has stopped, and a fresh one is loaded on the CPU", async () => {
    const loads = [];
    const FACE = { faceBlendshapes: [{ categories: [{ categoryName: "eyeBlinkLeft", score: 0.1 },
      { categoryName: "eyeBlinkRight", score: 0.1 }] }] };
    const tracker = createTracker({
      load: async (delegate) => {
        loads.push(delegate);
        return loads.length === 1
          ? { detectForVideo() { throw new Error("GPU context lost"); }, close() { loads.push("closed"); } }
          : { detectForVideo: () => FACE };
      },
      options: { fetchLive: liveStream(1000), decode: async () => ({ width: 4, height: 3, close() {} }),
                 fps: Infinity, retryMs: 0 },
    });
    const r = await rig({ tracker, canvas: { width: 0, height: 0, getContext: () => ({ drawImage() {} }) } });
    await r.drive(100);
    await turns(() => r.eng.state.chip === "Stopped · face tracker error");
    await turns(() => loads.includes("closed"));
    const failed = [r.eng.state.chip, /stopped after 3 failed frames/.test(r.eng.state.error || ""), [...loads]];
    r.advance(TRACKER_RETRY_SECS);
    await r.poll();
    await turns(() => r.eng.state.error === null);
    eq([failed, loads, r.eng.state.error, r.eng.state.chip !== "Stopped · face tracker error"],
       [["Stopped · face tracker error", true, ["GPU", "closed"]], ["GPU", "closed", "CPU"], null, true]);
  }],

  ["the chip's sixth text: a stopped tracker is 'Stopped · face tracker error', never 'Can't see you'", () =>
    eq([chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: true }, trackerFailed: true }),
        chipOf({ enabled: true, gate: gateOf({ connected: false }, cfg), trackerFailed: true }),
        chipOf({ enabled: false, gate: moving, trackerFailed: true })],
       ["Stopped · face tracker error", "Stopped · face tracker error", "Off"])],
  ["a tracker that failed to load and then starts clears its error (minor)", async () => {
    let n = 0;
    const r = await rig({ tracker: { start: async () => { if (n++ === 0) throw new Error("no wasm"); return { stop() {} }; },
                                     drop() {} } });
    await r.drive(100);
    const failed = r.eng.state.error;
    await r.polls(61);
    eq([/did not load: no wasm/.test(failed || ""), n, r.eng.state.error], [true, 2, null]);
  }],

  // ---- 6. one scaled config, to the measures and the ladder alike
  ["Sensitive reaches the measures and the ladder alike: 0.9 s closed at 0.42 is Level 2", async () => {
    // Baseline 0.1: closed above 0.1 + 0.28 (Sensitive), not 0.45; and Level
    // 2 at 0.8 s, not 1.0. Either one left unscaled and this never raises.
    // Ten frames: closedFor runs from the first closed frame, so 0.9 s.
    const r = await rig({ settings: { sensitivity: "sensitive" } });
    await r.calibrate();
    await r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.level, r.eng.state.trigger], [2, "closed"]);
    const c = r.log.find((x) => x[0] === "setConfig")[1];
    eq([c._scaled, c.eyes.closed_over_baseline, c.level2.closed_secs], [true, 0.28, 0.8]);
  }],
  ["and Standard does not: the same closure is not even closed", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.level, r.eng.state.measures.closed], [0, false]);
  }],

  // ---- 7. a settings save while an alert sounds
  ["a save while Level 2 sounds clears it through 'I'm awake' first, then the new settings apply", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    const from = r.log.length;
    r.settings.sensitivity = "sensitive";
    await r.eng.reload();
    const after = r.log.slice(from).map((x) => (x[0] === "play" ? "play:" + x[1].join("+") : `${x[0]}:${x[1].sensitivity}`));
    eq([after, r.eng.state.level], [["play:fade", "setConfig:sensitive"], 0]);
    // The same ladder and baseline, with the new thresholds: a closure at
    // 0.42 is closed now, with no new minute of calibration, and 0.9 s of it
    // raises (the second Level 2 in 5 minutes, so Level 3).
    await r.frames(5);
    await r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.measures.baseline, r.eng.state.measures.threshold, r.eng.state.level, r.eng.state.trigger],
       [0.1, 0.38, 3, "repeat-l2"]);
  }],
  ["the save clears Level 3's held alarm too, and a running Test the alerts", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(2.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 3);
    await r.eng.reload();
    eq([r.eng.state.level, r.plays().at(-1)], [0, ["fade"]]);
    await r.drive(0);
    ok(r.eng.test(), "the test starts");
    r.advance(12);
    await r.eng.reload();
    r.advance(30);
    eq([r.eng.state.testing, r.plays().slice(-2)], [false, [["duck", "bark"], ["fade"]]]);
  }],
  ["a save with nothing sounding is not 'I'm awake': nothing plays, and the night notice is not hushed", async () => {
    const r = await rig({ hour: 3 });
    await r.drive(0);
    await r.eng.reload();
    const after = r.plays().length;
    await r.drive(100);
    eq([after, r.eng.state.level, r.eng.state.trigger], [0, 1, "night"]);
  }],

  // ---- 8. the test gate: today's rule by default, replaceable at the merge
  ["the default test gate is today's rule, and never asks", async () => {
    const r = await rig();
    await r.drive(0);
    const parked = [r.eng.canTest(), r.eng.canAsk(), await r.eng.askTest()];
    await r.drive(8);
    eq([parked, [r.eng.canTest(), r.eng.canAsk()]], [[true, false, false], [false, false]]);
  }],
  ["a replaced gate is honoured: it decides, with the sample, whether the test may start and whether it goes on", async () => {
    const was = setTestGate({});
    let asked = 0;
    try {
      setTestGate({ may: (gate, sample) => testMayRun(gate) && !!(sample && sample.inPark),
                    ask: async () => { asked++; return true; } });
      const r = await rig();
      await r.drive(0);                           // stopped, but not in Park
      eq([r.eng.canTest(), r.eng.canAsk(), r.eng.test()], [false, true, false]);
      eq([await r.eng.askTest(), asked], [true, 1]);
      await r.drive(0, { inPark: true });
      eq([r.eng.canTest(), r.eng.canAsk()], [true, false]);
      ok(r.eng.test(), "the test starts in Park");
      r.advance(3);
      await r.drive(0);                           // out of Park at 0 km/h: a red light
      eq([r.eng.state.testing, r.plays().at(-1)], [false, ["fade"]]);
    } finally { setTestGate({ may: was.may, ask: was.ask }); }
  }],

  // ---- 9. Test the alerts: parked only, a card with Stop, the one player
  ["Test the alerts plays Levels 1, 2 and 3 in turn through the one player, then ends by itself", async () => {
    const r = await rig();
    await r.drive(0);
    ok(r.eng.test(), "it starts while parked");
    eq([r.eng.state.testing, r.eng.state.testLevel, r.eng.canTest(), r.eng.test()], [true, 0, false, false],
       "shown at once, and not twice");
    const seen = [];
    let was = 0;
    for (const at of [0, 8, 11, 19, 27, 31]) { r.advance(at - was); was = at; seen.push(r.eng.state.testLevel); }
    eq(r.plays(), [["chime", "voice:l1"], ["fade"], ["duck", "bark"], ["duck", "alarm", "voice:l3"], ["fade"]]);
    eq([seen, r.eng.state.testing], [[1, 0, 2, 3, 0, 0], false]);
  }],
  ["it stops with a fade the moment the car moves, and nothing more is played", async () => {
    const r = await rig();
    await r.drive(0);
    r.eng.test();
    r.advance(12);
    await r.drive(5);
    r.advance(30);
    eq([r.eng.state.testing, r.plays()], [false, [["chime", "voice:l1"], ["fade"], ["duck", "bark"], ["fade"]]]);
  }],
  ["and when the link drops, on 'I'm awake', and on Stop", async () => {
    const out = [];
    for (const how of ["link", "tap", "stop"]) {
      const r = await rig();
      await r.drive(0);
      r.eng.test();
      r.advance(1);
      if (how === "link") await r.drive(null);
      if (how === "tap") r.eng.tap();
      if (how === "stop") r.eng.stopTest();
      r.advance(40);
      out.push([r.eng.state.testing, r.plays().length, r.plays().at(-1)]);
    }
    eq(out, [[false, 2, ["fade"]], [false, 2, ["fade"]], [false, 2, ["fade"]]]);
  }],
  ["it will not start while moving, on simulated numbers, or with no link", async () => {
    const r = await rig();
    const got = [];
    for (const s of [[8], [0, { simulated: true }], [null]]) { await r.drive(...s); got.push(r.eng.test()); }
    eq([got, r.plays()], [[false, false, false], []]);
  }],

  // ---- 10. the AUX warning, for drowsy mode's own screen
  ["drowsy mode's state carries the AUX warning while sound is on the tablet's speakers", async () => {
    const r = await rig();
    const got = [r.eng.state.aux];
    r.audio({ aux: false });
    got.push(r.eng.state.aux);
    r.audio({ aux: true });
    got.push(r.eng.state.aux);
    eq([got, showAux === audiostate.showAux], [["", audiostate.auxLine({ aux: false }), ""], true]);
  }],

  // ---- the watch, the chip and the records
  ["the face tracker is started only when there is something to watch, and stopped after", async () => {
    const r = await rig();
    const w = () => [r.watches, r.stops];
    await r.drive(0);
    const parked = w();
    await r.drive(20);
    const moving = w();
    r.cams = { running: true, roles: { cabin: { live: false } } };
    await r.eng.pollCams();
    const noPicture = w();
    r.cams = { running: true, roles: { cabin: { live: true } } };
    await r.eng.pollCams();
    await r.eng.idle();
    await r.drive(0);
    const shortStop = w();
    await r.polls(11);
    eq([parked, moving, noPicture, shortStop, w()], [[0, 0], [1, 0], [1, 1], [2, 1], [2, 2]]);
  }],
  ["drowsy mode off: nothing is watched and nothing alerts", async () => {
    const r = await rig({ settings: { enabled: false } });
    await r.drive(100);
    await r.frames(70, () => ({ blink: 0.9 }));
    eq([r.watches, r.eng.state.chip, r.eng.state.level, r.plays()], [0, "Off", 0, []]);
  }],
  ["the chip: Watching with a face, Can't see you after 5 s without one or without frames, Paused when parked", async () => {
    const r = await rig();
    await r.drive(40);
    await r.frames(1);
    const a = r.eng.state.chip;
    await r.frames(5.2, () => ({ face: false }));
    const b = r.eng.state.chip;
    await r.frames(1);
    const c = r.eng.state.chip;
    await r.polls(6);
    const d = r.eng.state.chip;
    await r.drive(0);
    eq([a, b, c, d, r.eng.state.chip], ["Watching", "Can't see you", "Watching", "Can't see you", "Paused · parked"]);
  }],
  ["the link dropping while driving reads 'Paused · no car data', never 'Paused · parked'", async () => {
    const r = await rig();
    await r.drive(90);
    const driving = r.eng.state.chip;
    await r.drive(null);
    const dropped = r.eng.state.chip;
    await r.drive(0);
    eq([driving, dropped, r.eng.state.chip], ["Watching", "Paused · no car data", "Paused · parked"]);
  }],
  ["each alert goes to the records book with its level, trigger, speed and measures", async () => {
    const r = await rig();
    await r.calibrate();
    await r.frames(1.2, () => ({ blink: 0.9 }));
    await Promise.resolve();
    await Promise.resolve();
    const ev = r.posts.filter((p) => p[0] === "/api/drowsy/event").map((p) => p[1]);
    eq(ev.map((e) => [e.level, e.trigger, e.speed_kph, e.measures.closedFor >= 1]), [[2, "closed", 100, true]]);
  }],
  ["measures are logged once a second while moving, and flushed together", async () => {
    const r = await rig();
    await r.calibrate();
    await r.eng.flushLog();
    const rows = r.posts.filter((p) => p[0] === "/api/drowsy/log").flatMap((p) => p[1].rows);
    ok(rows.length >= 60 && rows.length <= 62, `${rows.length} rows for 61 s`);
    eq([rows.at(-1).kph, rows.at(-1).active, rows.at(-1).fed, typeof rows.at(-1).mt], [100, true, true, "number"]);
  }],
];
