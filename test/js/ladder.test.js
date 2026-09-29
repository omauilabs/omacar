import { eq } from "./assert.js";
import { createLadder, createStopClock, scaled, isNight } from "../js/ladder.js";

const CFG = async () => (await fetch("../data/drowsy.json")).json();
const M = (o) => Object.assign({ face: true, calibrated: true, closed: false, closedFor: 0, openFor: 0,
  perclos: 0.02, yawns: 0, nods: 0, faceLost: false }, o);
const go = (o) => Object.assign({ active: true, parked: false, sinceStop: 0, stoppedFor: 0, hour: 14, tap: false }, o);
// Step a ladder through [t, measures, extra] rows; every output comes back.
const drive = (lad, rows) => rows.map(([t, m, x]) => lad.step(go(Object.assign({ t, m: M(m) }, x))));
const kinds = (out) => out.cues.map((c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind));

export default [
  ["the spec's ladder is the default", async () => {
    const c = await CFG();
    eq([c.level1.perclos, c.level1.yawns, c.level1.nods, c.level1.count_window_secs, c.level1.since_stop_secs,
        c.level1.night_from_hour, c.level1.night_to_hour, c.level2.closed_secs, c.level2.perclos,
        c.level3.closed_secs, c.level3.level2_count, c.level3.level2_window_secs, c.release.open_secs,
        c.banner_stopped_secs, c.stop.still_secs],
       [0.15, 3, 3, 300, 7200, 2, 6, 1.0, 0.25, 2.0, 2, 300, 5, 120, 300]);
  }],
  ["the owner's gate setting defaults to the recommended answer", async () =>
    eq((await CFG()).alert_continues_below_gate, true)],
  ["Sensitive is every trigger threshold 20% lower", async () => {
    const s = scaled(await CFG(), "sensitive");
    eq([s.eyes.closed_over_baseline, s.eyes.closed_cap, s.yawn.jaw_open, s.yawn.hold_secs, s.nod.below_deg,
        s.nod.hold_secs, s.level1.perclos, s.level2.perclos, s.level2.closed_secs, s.level3.closed_secs,
        s.level1.yawns, s.level1.nods, s.level1.since_stop_secs, s.level3.level2_count],
       [0.28, 0.64, 0.48, 1.2, 12, 0.4, 0.12, 0.2, 0.8, 1.6, 2, 2, 5760, 2]);
  }],
  ["Standard changes nothing", async () => { const c = await CFG(); eq(scaled(c, "standard"), c); }],
  ["night is 02:00 to 06:00", async () => { const c = await CFG(); eq([1, 2, 5, 6].map((h) => isNight(h, c)), [false, true, true, false]); }],
  ["five minutes stationary is a stop; four is not", async () => {
    const s = createStopClock(await CFG());
    s.feed(0, 80, true);
    s.feed(1000, 0, true);
    const a = s.feed(1240, 0, true);
    const b = s.feed(1300, 0, true);
    eq([a.sinceStop, b.sinceStop, b.stoppedFor], [1240, 0, 300]);
  }],
  ["nothing is raised below 30 mph, even with eyes closed", async () => {
    eq(drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.5 }, { active: false }]])[0].level, 0);
  }],
  ["a condition that became true below 30 mph raises once the car passes it", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }], [1, { perclos: 0.16 }]]);
    eq(out.map((o) => o.level), [0, 1]);
  }],
  ["hovering around 30 mph with the same evidence raises once, never again", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }, { active: false }], [3, { perclos: 0.16 }], [4, { perclos: 0.16 }, { tap: true }],
      [5, { perclos: 0.16 }, { active: false }], [6, { perclos: 0.16 }]]);
    eq(out.map((o) => !!o.raised), [false, true, false, false, false, false, false]);
  }],
  ["crossing the gate never builds two Level 2s into Level 3", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.26 }, { active: false }], [1, { perclos: 0.26 }],
      [2, { perclos: 0.26 }, { tap: true }], [3, { perclos: 0.26 }, { active: false }], [4, { perclos: 0.26 }]]);
    eq(out.map((o) => o.level), [0, 2, 0, 0, 0]);
  }],
  ["PERCLOS 15% is Level 1: a chime, the voice, the radio rising", async () => {
    const [o] = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }]]);
    eq([o.level, o.trigger, kinds(o)], [1, "perclos", ["chime", "voice:l1", "swell"]]);
  }],
  ["a condition that stays true raises once", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.17 }], [2, { perclos: 0.18 }]]);
    eq(out.map((o) => !!o.raised), [true, false, false]);
  }],
  ["eyes closed for 1 s is Level 2: the music ducks and the first sound rises", async () => {
    const [o] = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }]]);
    eq([o.level, o.trigger, kinds(o)], [2, "closed", ["duck", "bark"]]);
  }],
  ["Level 2 repeats every 5 s, rotating bark, voice, alarm", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [2, { face: false }],
      [5, { face: false }], [10, { face: false }], [15, { face: false }]]);
    eq(out.map(kinds), [["duck", "bark"], [], ["voice:l2"], ["alarm"], ["bark"]]);
  }],
  ["only the sounds the driver chose rotate", async () => {
    const out = drive(createLadder(await CFG(), ["alarm"]), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }]]);
    eq(out.map(kinds), [["duck", "alarm"], ["alarm"]]);
  }],
  ["eyes closed for 2 s is Level 3: a continuous alarm and 'Pull over now'", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, { closed: true, closedFor: 2.0 }]]);
    eq([out[1].level, kinds(out[1]), out[1].banner], [3, ["duck", "alarm", "voice:l3"], true]);
  }],
  ["two Level 2 alerts within 5 min is Level 3", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }],
      [100, { closed: true, closedFor: 0.2 }], [101, { closed: true, closedFor: 1.0 }]]);
    eq([out[0].level, out[1].level, out[3].level, out[3].trigger], [2, 0, 3, "repeat-l2"]);
  }],
  ["six minutes apart, a second Level 2 is only Level 2", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }],
      [400, { closed: true, closedFor: 0.2 }], [401, { closed: true, closedFor: 1.0 }]]);
    eq(out[3].level, 2);
  }],
  ["'I'm awake' clears the level, and the sound fades", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }]]);
    eq([out[1].level, out[1].cleared, kinds(out[1])], [0, true, ["fade"]]);
  }],
  ["eyes open 5 s with PERCLOS not rising clears Level 1; 4 s does not", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [5, { perclos: 0.15 }], [6, { perclos: 0.15 }]]);
    eq(out.map((o) => o.level), [1, 1, 1, 0]);
  }],
  ["open eyes with PERCLOS still rising do not clear it", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }], [6, { perclos: 0.17 }]]);
    eq(out[2].level, 1);
  }],
  ["Level 3 has no time cap: open eyes do not end it, 'I'm awake' does", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }], [1, {}], [10, {}],
      [3600, {}], [3601, {}, { tap: true }]]);
    eq(out.map((o) => o.level), [3, 3, 3, 3, 0]);
  }],
  ["and so does a stopped car", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }], [1, {}, { active: false, parked: true }]]);
    eq([out[1].level, kinds(out[1])], [0, ["fade"]]);
  }],
  ["below the gate a sounding alert keeps repeating, by default", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }, { active: false }]]);
    eq([out[1].level, kinds(out[1])], [2, ["voice:l2"]]);
  }],
  ["with alert_continues_below_gate false it stays raised and falls silent below the gate", async () => {
    const c = await CFG();
    c.alert_continues_below_gate = false;
    const out = drive(createLadder(c), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }, { active: false }],
      [6, { face: false }]]);
    eq([out[1].level, kinds(out[1]), kinds(out[2])], [2, [], ["voice:l2"]]);
  }],
  ["after Level 3 the banner stays until the car has been stopped 2 minutes", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }], [1, {}, { tap: true }],
      [60, {}, { active: false, parked: true, stoppedFor: 30 }], [200, {}, { active: false, parked: true, stoppedFor: 120 }]]);
    eq(out.map((o) => [o.level, o.banner]), [[3, true], [0, true], [0, true], [0, false]]);
  }],
  ["night hours raise Level 1 once an hour, never more", async () => {
    const out = drive(createLadder(await CFG()), [[0, {}, { hour: 3 }], [10, {}, { tap: true, hour: 3 }],
      [1800, {}, { hour: 3 }], [3600, {}, { hour: 4 }]]);
    eq(out.map((o) => o.raised && o.raised.trigger), ["night", null, null, "night"]);
  }],
  ["2 h without a stop raises Level 1, and never escalates on its own", async () => {
    const out = drive(createLadder(await CFG()), [[0, { face: false }, { sinceStop: 7200 }], [30, { face: false }, { sinceStop: 7230 }]]);
    eq([out[0].level, out[0].trigger, out[1].level, kinds(out[1])], [1, "since-stop", 1, []]);
  }],
  ["new settings keep the ladder's memory", async () => {
    const c = await CFG();
    const lad = createLadder(c);
    drive(lad, [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }]]);
    lad.setConfig(scaled(c, "sensitive"));
    const out = drive(lad, [[100, { closed: true, closedFor: 0.2 }], [101, { closed: true, closedFor: 0.9 }]]);
    eq([out[1].level, out[1].trigger], [3, "repeat-l2"]);
  }],
];
