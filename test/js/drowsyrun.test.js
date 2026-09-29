import { eq, ok } from "./assert.js";
import {
  gateOf, chipOf, rotationFor, testMayRun, voiceInstalled, showAux, KPH_PER_MPH,
  createDrowsy, setTestGate, drowsy,
} from "../js/drowsyrun.js";
import * as player from "../js/alertplayer.js";
import * as audiostate from "../js/audiostate.js";

const cfg = { min_speed_mph: 30 };
const moving = { moving: true };

// ---- the rig: drowsy mode's engine with everything outside it handed in.
// No wall clock anywhere: `r.t` is the frame clock, moved only by the test.
const CFG = async () => (await fetch("../data/drowsy.json")).json();
const kind = (c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind);
const r3 = (x) => +x.toFixed(3);

async function rig(over = {}) {
  const r = {
    t: 1000, live: null, cams: { running: true, roles: { cabin: { live: true } } },
    settings: Object.assign(await CFG(), over.settings || {}),
    installed: over.installed || (() => false), asked: [], posts: [], log: [],
    timers: [], nextId: 1, onFrame: null, watches: 0, stops: 0, audio: null,
  };
  const fake = {
    play(cues, out) { r.log.push(["play", cues.map(kind), out.level]); return Promise.resolve(); },
    setConfig(c) { r.log.push(["setConfig", c]); },
    setName(fn) { r.name = fn; },
  };
  r.player = fake;
  r.eng = createDrowsy({
    clock: () => r.t, wall: () => 1.8e9 + r.t, hour: () => over.hour ?? 14,
    getJSON: async (p) => {
      if (p === "/api/live") return r.live;
      if (p === "/api/cams") return r.cams;
      if (p === "/api/drowsy") return JSON.parse(JSON.stringify(r.settings));
      throw new Error("404 " + p);
    },
    postJSON: async (p, body) => { r.posts.push([p, body]); return {}; },
    player: () => fake,
    voiceInstalled: async (name) => { r.asked.push(name); return r.installed(name); },
    onAudio: (fn) => { r.audio = fn; fn(null); return () => {}; },
    watch: async ({ onFrame }) => {
      r.watches++;
      r.onFrame = onFrame;
      return { stop() { r.stops++; r.onFrame = null; } };
    },
    later: (fn, ms) => { const id = r.nextId++; r.timers.push({ id, at: r3(r.t + ms / 1000), fn }); return id; },
    cancel: (id) => { r.timers = r.timers.filter((x) => x.id !== id); },
    every: () => 0,
    canvas: null,
  });
  // The car: a sample from /api/live, then one poll. null is a dropped link.
  r.drive = async (kph, extra) => {
    r.live = kph === null ? { connected: false } : Object.assign({ connected: true, values: { SPEED: kph } }, extra || {});
    await r.eng.pollLive();
    await r.eng.idle();
  };
  // `secs` of cabin frames at 10 fps, each stamped on the frame clock as
  // facewatch.js stamps it; fn(i) overrides a frame's fields.
  r.frames = (secs, fn) => {
    for (let i = 0; i < Math.round(secs * 10); i++) {
      r.t = r3(r.t + 0.1);
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
  r.calibrate = async () => { await r.drive(100); r.frames(61); };
  await r.eng.start();
  await r.eng.idle();
  return r;
}

export default [
  // ---- the brief's
  ["30 mph is 48.28 km/h: below it the gate is shut", () =>
    eq([gateOf({ connected: true, values: { SPEED: 48.2 } }, cfg).active,
        gateOf({ connected: true, values: { SPEED: 48.3 } }, cfg).active], [false, true])],
  ["no car is neither active nor parked: a dropped link must not clear an alert", () =>
    eq([gateOf({ connected: false }, cfg).active, gateOf({ connected: false }, cfg).parked], [false, false])],
  ["stationary and connected is parked", () => eq(gateOf({ connected: true, values: { SPEED: 0 } }, cfg).parked, true)],
  ["the chip says one of four things", () =>
    eq([chipOf({ enabled: false }),
        chipOf({ enabled: true, gate: { moving: false } }),
        chipOf({ enabled: true, gate: moving, cabinLive: false }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: true } }),
        chipOf({ enabled: true, gate: moving, cabinLive: true, measures: { faceLost: false } })],
       ["Off", "Paused · parked", "Can't see you", "Can't see you", "Watching"])],
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
    r.frames(1.2, () => ({ blink: 0.9 }));
    r.frames(12);
    eq(r.plays().slice(0, 3), [["duck", "bark"], ["alarm"], ["bark"]]);
  }],
  ["and a settings reload passes the filtered rotation to setConfig: the voice goes when its clip does", async () => {
    const r = await rig({ settings: { name: "James" }, installed: (n) => n === "James" });
    eq(r.eng.state.rotation, ["bark", "voice", "alarm"]);
    r.settings.name = "";
    await r.eng.reload();
    eq([r.asked, r.eng.state.rotation], [["James", ""], ["bark", "alarm"]]);
    await r.calibrate();
    r.frames(1.2, () => ({ blink: 0.9 }));
    r.frames(12);
    eq(r.plays().slice(0, 3), [["duck", "bark"], ["alarm"], ["bark"]]);
  }],
  ["and comes back with it: the voice is in the rotation once its clip is there", async () => {
    const r = await rig({ settings: { name: "" }, installed: (n) => n === "James" });
    r.settings.name = "James";
    await r.eng.reload();
    await r.calibrate();
    r.frames(1.2, () => ({ blink: 0.9 }));
    r.frames(12);
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
    r.t = 2000;                                   // the clock has moved on
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
    r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    const n = r.plays().length;
    await r.polls(12);                            // no frames for 12 s
    ok(r.plays().length - n >= 2, `repeats during the gap: ${JSON.stringify(r.plays().slice(n))}`);
    eq(r.eng.state.level, 2);
    r.frames(0.1, () => ({ blink: 0.9 }));
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
    r.frames(1.2, () => ({ blink: 0.9 }));
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
    r.frames(61);
    r.frames(3, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures, r.eng.state.level, r.eng.state.chip, r.plays()], [null, 0, "Off", []]);
  }],
  ["after Level 3 the banner stays until 2 minutes connected and stopped, by the stop clock: a dropped link starts that count over", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(2.2, () => ({ blink: 0.9 }));
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

  // ---- 5. no frames feed the measures while parked, or below the gate with nothing sounding
  ["a minute parked with eyes closed feeds nothing, and PERCLOS after moving off is not built from it", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(40);                                 // PERCLOS's window is under way, eyes open
    const fed = r.eng.state.measures;
    ok(fed.perclos !== null && fed.perclos < 0.05, `PERCLOS before the stop ${fed.perclos}`);
    r.eng.preview = true;                         // the settings sheet keeps the camera watched
    await r.drive(0);
    r.frames(60, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures === fed, r.eng.state.frame.blink], [true, 0.9], "parked: the preview's frame, not the measures");
    r.eng.preview = false;
    await r.drive(100);
    r.frames(0.1);
    eq([r.eng.state.measures.discontinuity, r.eng.state.measures.perclos], [true, null], "moving off restarts the window");
    r.frames(35);
    eq([r.eng.state.measures.perclos, r.eng.state.level], [0, 0]);
  }],
  ["below the gate with nothing sounding, frames are not fed; with an alert sounding they are", async () => {
    const r = await rig();
    await r.calibrate();
    await r.drive(40);                            // moving, under 30 mph
    const fed = r.eng.state.measures;
    r.frames(3, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures === fed, r.eng.state.level], [true, 0]);
    await r.drive(100);
    r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    await r.drive(40);
    const t0 = r.eng.state.measures.t;
    r.frames(1);
    eq([r.eng.state.measures.t > t0, r.eng.state.measures.closed], [true, false], "an alert sounding: fed below the gate");
  }],
  ["a pause shorter than a second still restarts the measures: nothing unseen is credited", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(0.6, () => ({ blink: 0.9 }));
    await r.drive(45);                            // under the gate for 0.3 s
    r.frames(0.3, () => ({ blink: 0.9 }));
    await r.drive(100);
    r.frames(0.1, () => ({ blink: 0.9 }));
    eq([r.eng.state.measures.discontinuity, r.eng.state.measures.closedFor], [true, 0]);
    r.frames(0.5, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 0, "0.6 s closed before the pause is not added to 0.6 s after it");
  }],

  // ---- 6. one scaled config, to the measures and the ladder alike
  ["Sensitive reaches the measures and the ladder alike: 0.9 s closed at 0.42 is Level 2", async () => {
    // Baseline 0.1: closed above 0.1 + 0.28 (Sensitive), not 0.45; and Level
    // 2 at 0.8 s, not 1.0. Either one left unscaled and this never raises.
    // Ten frames: closedFor runs from the first closed frame, so 0.9 s.
    const r = await rig({ settings: { sensitivity: "sensitive" } });
    await r.calibrate();
    r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.level, r.eng.state.trigger], [2, "closed"]);
    const c = r.log.find((x) => x[0] === "setConfig")[1];
    eq([c._scaled, c.eyes.closed_over_baseline, c.level2.closed_secs], [true, 0.28, 0.8]);
  }],
  ["and Standard does not: the same closure is not even closed", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.level, r.eng.state.measures.closed], [0, false]);
  }],

  // ---- 7. a settings save while an alert sounds
  ["a save while Level 2 sounds clears it through 'I'm awake' first, then the new settings apply", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(1.2, () => ({ blink: 0.9 }));
    eq(r.eng.state.level, 2);
    const from = r.log.length;
    r.settings.sensitivity = "sensitive";
    await r.eng.reload();
    const after = r.log.slice(from).map((x) => (x[0] === "play" ? "play:" + x[1].join("+") : `${x[0]}:${x[1].sensitivity}`));
    eq([after, r.eng.state.level], [["play:fade", "setConfig:sensitive"], 0]);
    // The same ladder and baseline, with the new thresholds: a closure at
    // 0.42 is closed now, with no new minute of calibration, and 0.9 s of it
    // raises (the second Level 2 in 5 minutes, so Level 3).
    r.frames(5);
    r.frames(1.0, () => ({ blink: 0.42 }));
    eq([r.eng.state.measures.baseline, r.eng.state.measures.threshold, r.eng.state.level, r.eng.state.trigger],
       [0.1, 0.38, 3, "repeat-l2"]);
  }],
  ["the save clears Level 3's held alarm too, and a running Test the alerts", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(2.2, () => ({ blink: 0.9 }));
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
    eq([parked, moving, noPicture, w()], [[0, 0], [1, 0], [1, 1], [2, 2]]);
  }],
  ["drowsy mode off: nothing is watched and nothing alerts", async () => {
    const r = await rig({ settings: { enabled: false } });
    await r.drive(100);
    r.frames(70, () => ({ blink: 0.9 }));
    eq([r.watches, r.eng.state.chip, r.eng.state.level, r.plays()], [0, "Off", 0, []]);
  }],
  ["the chip: Watching with a face, Can't see you after 5 s without one or without frames, Paused when parked", async () => {
    const r = await rig();
    await r.drive(40);
    r.frames(1);
    const a = r.eng.state.chip;
    r.frames(5.2, () => ({ face: false }));
    const b = r.eng.state.chip;
    r.frames(1);
    const c = r.eng.state.chip;
    await r.polls(6);
    const d = r.eng.state.chip;
    await r.drive(0);
    eq([a, b, c, d, r.eng.state.chip], ["Watching", "Can't see you", "Watching", "Can't see you", "Paused · parked"]);
  }],
  ["each alert goes to the records book with its level, trigger, speed and measures", async () => {
    const r = await rig();
    await r.calibrate();
    r.frames(1.2, () => ({ blink: 0.9 }));
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
