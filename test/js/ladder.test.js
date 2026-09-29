import { eq } from "./assert.js";
import { createLadder, createStopClock, scaled, isNight, slotsOf, MIN_SLOT_SECS } from "../js/ladder.js";
import { createMeasures } from "../js/drowsy.js";

const CFG = async () => (await fetch("../data/drowsy.json")).json();
const M = (o) => Object.assign({ face: true, calibrated: true, closed: false, closedFor: 0, openFor: 0,
  perclos: 0.02, yawns: 0, nods: 0, faceLost: false }, o);
const go = (o) => Object.assign({ active: true, parked: false, sinceStop: 0, stoppedFor: 0, hour: 14, tap: false }, o);
// Step a ladder through [t, measures, extra] rows; every output comes back.
// m.t defaults to the row's own t (a genuine new frame every row, as in
// every test but P2b/I2, which overrides it to freeze a stale snapshot).
const drive = (lad, rows) => rows.map(([t, m, x]) => lad.step(go(Object.assign({ t, m: M(Object.assign({ t }, m)) }, x))));
const kinds = (out) => out.cues.map((c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind));
// Whole-second (or dt) steps from a to b inclusive, without float drift.
const range = (a, b, dt = 1) => { const r = []; for (let t = a; t <= b + 1e-9; t = +(t + dt).toFixed(6)) r.push(t); return r; };
// Every raise and every release, as [t, level, trigger or "clear"].
const events = (outs) => outs.filter((o) => o.raised || o.cleared)
  .map((o) => [o.t, o.level, o.raised ? o.raised.trigger : "clear"]);
// The plan's cabin feed through the real measures (drowsy.js) into the
// ladder: a frame every 0.1 s from t=100, and a ladder step per frame, as
// drowsyrun.js's onFrame does. calibrated at speed first (60 s). frame(i, tr,
// st) returns { closed, active, light, tap } for frame i at tr = t - 100;
// "light" is a stop at a red light: no frames (the watcher is stopped) and a
// 2 Hz pollLive step re-feeding the last, stale snapshot, parked. st carries
// the first raise's time and the tap's. Returns { ev: [[tr, level,
// trigger|"clear"]...], st }.
const cabin = async (n, frame) => {
  const cfg = await CFG();
  const meas = createMeasures(cfg), lad = createLadder(cfg);
  const st = { raisedAt: null, tapAt: null };
  const ev = [];
  let last = null;
  for (let i = 0; i < n; i++) {
    const t = +(100 + i * 0.1).toFixed(3), tr = +(t - 100).toFixed(3);
    const f = frame(i, tr, st);
    let o;
    if (f.light) {
      if (i % 5) continue;
      o = lad.step(go({ t, m: last, active: false, parked: true }));
    } else {
      last = meas.feed({ t, face: true, blink: f.closed ? 0.9 : 0.1, jaw: 0, pitch: 0, gated: f.active !== false });
      if (f.tap) st.tapAt = tr;
      o = lad.step(go({ t, m: last, active: f.active !== false, tap: !!f.tap }));
    }
    if (o.raised && st.raisedAt === null) st.raisedAt = tr;
    if (o.raised || o.cleared) ev.push([tr, o.level, o.raised ? o.raised.trigger : "clear"]);
  }
  return { ev, st };
};
// "I'm awake" on the first frame 30 s or more after the first raise.
const tapAfter30 = (st, tr) => st.raisedAt !== null && st.tapAt === null && tr - st.raisedAt >= 30 - 1e-9;
// N4: drives a Level 2 ladder through an open-eye run, an embedded closure
// given as concrete [t, closed, closedFor] frames (a real 10 fps sequence,
// simulated outside the ladder -- not drowsy.js), and checks release exactly
// 5 s after the run started. Tolerated (a blink) -> cleared by then, since
// the run was never broken; a real closure breaks the run, which restarts
// only once the closure ends, so it is not yet cleared by that same time.
const closureTolerated = async (frames) => {
  const lad = createLadder(await CFG());
  const R = 0.2;
  drive(lad, [[0, { closed: true, closedFor: 1.0 }]]); // raises Level 2
  lad.step(go({ t: R, m: M({ t: R }) }));               // eyes open -- the run starts at R
  const base = R + 1.0;
  for (const [dt, closed, closedFor] of frames) {
    lad.step(go({ t: base + dt, m: M({ t: base + dt, closed, closedFor }) }));
  }
  const checked = lad.step(go({ t: R + 5.1, m: M({ t: R + 5.1 }) })); // just past 5 s since R
  return checked.level === 0;
};

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
  ["evidence below the gate never arms; only genuinely active time counts (I4, fix round 2)", async () => {
    // Round 1 had this raise the moment the car passed 30 mph, crediting
    // hold time gathered below the gate. I4's round 2 ruling reverses that:
    // evidence gathered while not active never arms or counts at all, so
    // the hold cannot even start until the car is genuinely active.
    // Task 9 fix round 1 (I1) goes further: the crossing at t=4 latches
    // both PERCLOS gates, because the window it had was read below the
    // gate. So the hold starts only once the window holds no frame from
    // t=4 (m.t 65), and raises 3 s later -- 68, not 7.
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }],
      [1, { perclos: 0.16 }, { active: false }], [2, { perclos: 0.16 }, { active: false }],
      [3, { perclos: 0.16 }, { active: false }], // 3 s below the gate -- still nothing armed
      ...range(4, 68).map((t) => [t, { perclos: 0.16 }])]);                  // active from t=4: a crossing
    eq(events(out), [[68, 1, "perclos"]]);
  }],
  ["toggling active on and off resets the hold each time; only a sustained run raises (I4)", async () => {
    // Task 9 fix round 1 (I1): the step back into active at t=3 is a
    // crossing with nothing sounding, so both PERCLOS gates latch there
    // (m.t 3). The reading held since before the crossing is not new
    // evidence: the hold can start only once the window holds no frame from
    // t=3 (m.t 64), and it still needs its full 3 s -- 67. Before that
    // ruling this raised at t=6.
    const rows = [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }, { active: false }],                          // inactive -- resets the hold
      ...range(3, 67).map((t) => [t, { perclos: 0.16 }])];                  // active again: a crossing
    const out = drive(createLadder(await CFG()), rows);
    eq(events(out), [[67, 1, "perclos"]]);
  }],
  ["crossing the gate repeatedly with sustained evidence still raises Level 2 only once, and never re-raises through a tap into Level 3 (NR1/NR3)", async () => {
    // Extended past its original tap (fix round 3): under round 2 alone this
    // ran on 3 s longer and reached Level 3 via repeat-l2 at t=10.
    // Task 9 fix round 1 (I1): the crossing at t=1 latches both PERCLOS
    // gates, so the 0.26 read since before it raises only once the window
    // holds no frame from t=1 (m.t 62), after its 3 s hold: Level 2 at 65,
    // not 4. The tap then latches it (NR3), and the next crossing (t=68)
    // latches again: never Level 3.
    const rows = [[0, { perclos: 0.26 }, { active: false }],               // nothing armed
      ...range(1, 65).map((t) => [t, { perclos: 0.26 }]),                   // a crossing, then sustained
      [66, { perclos: 0.26 }, { tap: true }],                                // "I'm awake" clears it
      [67, { perclos: 0.26 }, { active: false }], ...range(68, 75).map((t) => [t, { perclos: 0.26 }])];
    const out = drive(createLadder(await CFG()), rows);
    eq(events(out), [[65, 2, "perclos"], [66, 0, "clear"]]);
    eq(out.every((o) => o.level !== 3), true);
    eq(out.every((o) => !o.raised || o.raised.trigger !== "repeat-l2"), true);
  }],
  ["PERCLOS 15% is Level 1: a chime, the voice, the radio rising", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }], [3, { perclos: 0.16 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 1]);
    eq([out[3].trigger, kinds(out[3])], ["perclos", ["chime", "voice:l1", "swell"]]);
  }],
  ["a condition that stays true raises once", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.17 }],
      [2, { perclos: 0.18 }], [3, { perclos: 0.16 }], [4, { perclos: 0.17 }]]);
    eq(out.map((o) => !!o.raised), [false, false, false, true, false]);
  }],
  ["eyes closed for 1 s is Level 2: the music ducks and the first sound rises", async () => {
    const [o] = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }]]);
    eq([o.level, o.trigger, kinds(o)], [2, "closed", ["duck", "bark"]]);
  }],
  // Task 7 fix round 1: the voice's turn is level2.voice_slot_secs (7 s), so
  // the alarm after it comes at 12 s, not 10.
  ["Level 2 repeats every 5 s, rotating bark, voice, alarm, and gives the voice a 7 s turn", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [2, { face: false }],
      [5, { face: false }], [10, { face: false }], [12, { face: false }], [17, { face: false }]]);
    eq(out.map(kinds), [["duck", "bark"], [], ["voice:l2"], [], ["alarm"], ["bark"]]);
  }],
  ["the turns come from the settings, and none is shorter than a sound's own rise and fall", async () => {
    const c = await CFG();
    eq([slotsOf(c), slotsOf({}), slotsOf({ level2: { repeat_secs: 2, voice_slot_secs: 9 } })],
       [{ repeat: 5, voice: 7 }, { repeat: 5, voice: 7 }, { repeat: MIN_SLOT_SECS, voice: 9 }]);
    c.level2.repeat_secs = 6;
    c.level2.voice_slot_secs = 8;
    const out = drive(createLadder(c), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }], [6, { face: false }],
      [13, { face: false }], [14, { face: false }], [20, { face: false }]]);
    eq(out.map(kinds), [["duck", "bark"], [], ["voice:l2"], [], ["alarm"], ["bark"]]);
  }],
  ["level2.voice false keeps the voice to Levels 1 and 3: one line of settings", async () => {
    const c = await CFG();
    c.level2.voice = false;
    const out = drive(createLadder(c), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }],
      [10, { face: false }], [11, { closed: true, closedFor: 2.0 }]]);
    eq(out.map(kinds), [["duck", "bark"], ["alarm"], ["bark"], ["duck", "alarm", "voice:l3"]]);
    const one = drive(createLadder(c, ["voice"]), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }]]);
    eq(one.map(kinds), [["duck", "alarm"], ["alarm"]], "a rotation of the voice alone falls back to the alarm");
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
      [2, { perclos: 0.16 }], [3, { perclos: 0.16 }], // holds 3 s -> raises Level 1 here
      [4, { perclos: 0.16 }],                          // the open run starts here
      [8, { perclos: 0.15 }],                          // 4 s open, not rising -- not yet
      [9, { perclos: 0.15 }]]);                         // 5 s open, not rising -- clears
    eq(out.map((o) => o.level), [0, 0, 0, 1, 1, 1, 0]);
  }],
  ["open eyes with PERCLOS still rising do not clear it", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }], [3, { perclos: 0.16 }],  // holds 3 s -> raises Level 1 here
      [4, { perclos: 0.16 }],                           // the open run starts here, at 0.16
      [10, { perclos: 0.17 }]]);                        // 6 s open, but PERCLOS has risen
    eq(out.map((o) => o.level), [0, 0, 0, 1, 1, 1]);
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
  // --- Fix round 1 regression tests -------------------------------------
  ["a one-frame PERCLOS wobble does not turn a sounding Level 2 into Level 3 (C1)", async () => {
    const lad = createLadder(await CFG());
    const out = drive(lad, [[0, { perclos: 0.251 }], [1, { perclos: 0.251 }], [2, { perclos: 0.251 }],
      [3, { perclos: 0.251 }], [3.1, { perclos: 0.249 }], [3.2, { perclos: 0.251 }],
      [3.3, { perclos: 0.249 }], [3.4, { perclos: 0.251 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 2, 2, 2, 2, 2]);
    eq(out.some((o) => o.trigger === "repeat-l2"), false);
  }],
  ["a re-crossing while a Level 2 is still sounding is never counted toward repeat-l2 (C1)", async () => {
    // Isolated from PERCLOS hysteresis: a genuine second closed-eye event,
    // not a wobble, while the first Level 2 never cleared.
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }],  // raises Level 2
      [1, { closed: false }],                  // eyes open again, closed2 disarms
      [2, { closed: true, closedFor: 1.0 }]]); // a second closure reaches 1.0 s -- Level 2 never cleared
    eq(out.map((o) => o.level), [2, 2, 2]);
    eq(out[2].trigger, "closed");
  }],
  ["a cleared Level 1 cannot re-raise from the same ~15% PERCLOS until it has re-armed (I1)", async () => {
    const lad = createLadder(await CFG());
    const out = drive(lad, [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }], [2, { perclos: 0.16 }],
      [3, { perclos: 0.16 }],  // holds 3 s -> raises Level 1
      [4, { perclos: 0.16 }],  // open run starts
      [9, { perclos: 0.16 }],  // 5 s open, not rising -> clears
      [10, { perclos: 0.16 }], // same evidence, still above threshold -- must not re-raise
      [20, { perclos: 0.16 }]]); // never recovered below the margin, so still blocked
    eq(out.map((o) => o.level), [0, 0, 0, 1, 1, 0, 0, 0]);
    eq(out.slice(5).some((o) => !!o.raised), false);
  }],
  ["a cleared Level 2 cannot re-raise from the same ~25% PERCLOS until it has re-armed (I1/NR3)", async () => {
    // 0.26 also clears Level 1's own 15% threshold. NR3: on the release at
    // t=9, EVERY gate still reading at or above its own threshold latches,
    // sibling or not -- so Level 1's own gate does not get to fire off the
    // same still-current 0.26 a moment after Level 2's release, any more
    // than Level 2's own gate does.
    const lad = createLadder(await CFG());
    const out = drive(lad, [[0, { perclos: 0.26 }], [1, { perclos: 0.26 }], [2, { perclos: 0.26 }],
      [3, { perclos: 0.26 }], [4, { perclos: 0.26 }], [9, { perclos: 0.26 }], [10, { perclos: 0.26 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 2, 2, 0, 0]);
    eq(out.map((o) => o.raised && o.raised.trigger), [null, null, null, "perclos", null, null, null]);
    eq(out.every((o) => !o.raised || o.raised.level !== 2 || o.t === 3), true);
  }],
  ["a discontinuity snapshot never raises and never counts as evidence on its own", async () => {
    const lad = createLadder(await CFG());
    // A gap reported with alarming numbers -- must raise nothing by itself.
    const gap = drive(lad, [[0, { discontinuity: true, closed: true, closedFor: 5, perclos: 0.9 }]]);
    eq(gap[0].level, 0);
    // And it must not have armed anything for a later step to cash in on.
    const later = drive(lad, [[1, { perclos: 0.02 }]]);
    eq(later[0].level, 0);
  }],
  ["a camera gap is never credited as open-eye time, and does not clear a sounding alert (I2)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }],       // raises Level 2
      [1, {}],                                      // open run starts
      [7, { discontinuity: true, openFor: 0 }],      // a 6 s camera gap -- must not count as open time
      [8, {}],                                       // open run starts again, fresh
      [12, {}],                                       // only 4 s since the gap -- not yet
      [13, {}]]);                                      // 5 s since the gap -- clears
    eq(out.map((o) => o.level), [2, 2, 2, 2, 2, 0]);
  }],
  ["a blink under 0.5 s does not reset the 5 s open-eye release (M7)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }], [1, {}], [3, { closed: true, closedFor: 0.2 }],
      [3.2, {}], [6, {}]]);
    eq(out.map((o) => o.level), [2, 2, 2, 2, 0]);
  }],
  ["a closure of 0.5 s or more resets the open-eye release (M7)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }], [1, {}], [3, { closed: true, closedFor: 0.5 }],
      [3.5, {}], [6, {}], [8.5, {}]]);
    eq(out.map((o) => o.level), [2, 2, 2, 2, 2, 0]);
  }],
  ["a repeated t cannot roll a second, more favorable reading into a release (I2)", async () => {
    const lad = createLadder(await CFG());
    drive(lad, [[0, { closed: true, closedFor: 1.0 }], [1, {}]]);
    const first = lad.step(go({ t: 6, m: M({ perclos: 0.5 }) }));
    const again = lad.step(go({ t: 6, m: M({ perclos: 0.02 }) }));
    const later = lad.step(go({ t: 7, m: M({ perclos: 0.02 }) }));
    eq([first.level, first.cleared], [2, false]);
    eq([again.level, again.cleared], [2, false]);
    eq([later.level, later.cleared], [0, true]);
  }],
  ["a stale snapshot (frozen m.t) never advances a release, however much wall time passes (P2b, I2, fix round 2)", async () => {
    const lad = createLadder(await CFG());
    drive(lad, [[0, { closed: true, closedFor: 1.0 }]]); // raises Level 2 (m.t defaults to 0)
    const open = lad.step(go({ t: 1, m: M({ t: 1 }) }));   // a genuine new frame -- the run starts at m.t=1
    const stale3 = lad.step(go({ t: 3, m: M({ t: 1 }) })); // the SAME frame re-fed on the wall clock
    const stale6 = lad.step(go({ t: 6, m: M({ t: 1 }) })); // still m.t=1 -- 5 s of wall time, 0 s of camera time
    eq([open.level, stale3.level, stale6.level, stale6.cleared], [2, 2, 2, false]);
    // A genuine new frame, however late on the wall clock, with 5 real
    // camera-seconds since the run started (m.t 1 -> 6): now it releases.
    const fresh = lad.step(go({ t: 100, m: M({ t: 6 }) }));
    eq([fresh.level, fresh.cleared], [0, true]);
  }],
  ["N1a: a PERCLOS hold that arms unused (Level 3 wins that step) still raises Level 2 once Level 3 clears", async () => {
    const lad = createLadder(await CFG());
    // closed3 and perclos2's own 3 s hold complete together; closed3 wins,
    // so perclos2's arm is consumed but never latched (C1/N1).
    const out1 = drive(lad, [[0, { perclos: 0.26 }], [1, { perclos: 0.26 }], [2, { perclos: 0.26 }],
      [3, { closed: true, closedFor: 2.0, perclos: 0.26 }]]);
    eq(out1.map((o) => o.level), [0, 0, 0, 3]);
    const out2 = drive(lad, [[4, {}, { tap: true }]]); // "I'm awake" clears Level 3
    eq(out2[0].level, 0);
    // Sustained again: must raise Level 2 within about 3 s, not stay stuck
    // waiting out a 10 s recovery it never actually needed.
    const out3 = drive(lad, [[5, { perclos: 0.27 }], [6, { perclos: 0.27 }], [7, { perclos: 0.27 }],
      [8, { perclos: 0.27 }]]);
    eq(out3.map((o) => o.level), [0, 0, 0, 2]);
  }],
  ["N1b: a single parked step does not discard an unused arm; sustained evidence still raises, once the crossing's window has turned over", async () => {
    const lad = createLadder(await CFG());
    const out1 = drive(lad, [[0, { perclos: 0.26 }], [1, { perclos: 0.26 }], [2, { perclos: 0.26 }],
      [3, { closed: true, closedFor: 2.0, perclos: 0.26 }]]); // Level 3; perclos2 arms unused, as in N1a
    eq(out1[3].level, 3);
    // One short parked step -- clears the level (a stopped car always does),
    // but is not a discard (I4): it is not a 5 min stop, and not a tap.
    const out2 = drive(lad, [[4, {}, { active: false, parked: true }]]);
    eq(out2[0].level, 0);
    // Task 9 fix round 1 (I1): t=5 is a crossing back into active with
    // nothing sounding, so the 0.27 read across it waits for the window to
    // turn over past m.t 5 (66), then its 3 s hold: Level 2 at 69, not 8.
    // The parked step is still not a discard; the crossing latch simply
    // comes after it.
    const out3 = drive(lad, range(5, 69).map((t) => [t, { perclos: 0.27 }]));
    eq(events(out3), [[69, 2, "perclos"]]);
  }],
  ["PERCLOS crossing 25% below the gate with a Level 1 up escalates (M8); 396 s later a fresh 0.28 is new evidence, not the same episode (NR5)", async () => {
    const lad = createLadder(await CFG());
    const out1 = drive(lad, [[0, { yawns: 3 }]]); // raises Level 1
    eq(out1[0].level, 1);
    // A 40 s traffic light: inactive, well under the 5 min discard threshold,
    // so the escalation evidence is not thrown away (M8's carve-out applies
    // to arming too, since Level 1 is already up).
    const out2 = drive(lad, [[1, { perclos: 0.26 }, { active: false }], [2, { perclos: 0.26 }, { active: false }],
      [3, { perclos: 0.26 }, { active: false }], [4, { perclos: 0.26 }, { active: false }]]);
    eq(out2.map((o) => o.level), [1, 1, 1, 2]);
    // perclos2 raised Level 2 at t=4, so it is latched (NR1). But 396 s of
    // camera time pass before the next reading: the 60 s PERCLOS window can
    // hold no frame from before the latch, so the latch has expired (NR5)
    // and 0.28 is new evidence. After its 3 s hold it raises Level 2 -- only
    // Level 2, since the first one is 400 s back, outside repeat-l2's 300 s.
    // (Round 3 wrongly kept the latch and asserted Level 1 here.) The tap row
    // reads M()'s default PERCLOS, 0.02, so its release latches nothing.
    const out3 = drive(lad, [[400, { perclos: 0.02 }, { tap: true }], [401, { perclos: 0.28 }], [402, { perclos: 0.28 }],
      [403, { perclos: 0.28 }], [404, { perclos: 0.28 }]]);
    eq(out3.map((o) => o.level), [0, 0, 0, 0, 2]);
    eq(out3[4].trigger, "perclos");
  }],
  ["N1c (NR5): the plan's red light -- a stale snapshot latches both gates, the camera restarts, and a fresh sustained 0.28 raises Level 2", async () => {
    // Re-review 1's N1c probe, the drowsyrun.js pattern. Level 1 is up; the
    // last frame reads 0.251 as the car stops. Through a 40 s light the
    // watcher is stopped and pollLive re-feeds that stale snapshot, parked:
    // the release latches both gates on it (NR3). Frames resume with a
    // discontinuity, which empties the measures' PERCLOS window -- the
    // latch expires there and then (NR5). PERCLOS reads null for 30 s (its
    // minimum window), then 0.28: after the 3 s hold, Level 2 at t=94.
    // Task 9 fix round 1 (I1): t=62 is a crossing into active with nothing
    // sounding (the frames at t=61 were fed below the gate), so both gates
    // latch at m.t 62 and the 0.28 waits for the window to turn over past it
    // (123), then the hold: Level 2 at 126.
    const rows = [];
    for (const t of range(0, 4)) rows.push([t, { perclos: 0.16 }]);          // Level 1 at t=3
    for (const t of range(5, 20)) rows.push([t, { perclos: 0.24 }]);
    rows.push([20.5, { perclos: 0.251 }]);                                     // the last frame as it slows
    for (const t of range(21, 60, 0.5)) rows.push([t, { t: 20.5, perclos: 0.251 }, { active: false, parked: true }]);
    rows.push([61, { perclos: null, discontinuity: true }, { active: false }]); // the camera restarts
    for (const t of range(62, 90)) rows.push([t, { perclos: null }]);
    for (const t of range(91, 300)) rows.push([t, { perclos: 0.28 }]);
    const ev = events(drive(createLadder(await CFG()), rows));
    eq(ev.slice(0, 3), [[3, 1, "perclos"], [21, 0, "clear"], [126, 2, "perclos"]]);
    // Nothing between the light and t=126, and no Level 3 from the light's
    // own (stale) reading.
    eq(ev.filter(([t]) => t > 21 && t < 126).length, 0);
  }],
  ["NR1a/NR5: a tap on a PERCLOS Level 2 never re-raises from the same 60 s window; once the window is all new frames, a continuing 0.26 raises", async () => {
    // PERCLOS is a 60 s window and stays high well after the tap. Round 2
    // alone let the tap free the gate that had just raised, so the same
    // 0.26 re-raised about 3 s later -- Level 2 became Level 3 (repeat-l2).
    const out = drive(createLadder(await CFG()),
      Array.from({ length: 90 }, (_, t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]));
    eq(out.slice(0, 5).map((o) => o.level), [0, 0, 0, 2, 2]);
    // Latched at the tap (t=5): nothing -- and never Level 3 -- while the
    // window still holds any frame from before it.
    eq(out.slice(5, 69).every((o) => o.level === 0 && !o.raised), true);
    // NR5: at t=66 the window (60 s, pruned as drowsy.js prunes it: older
    // than 60 s) holds only frames from after the tap, so the latch expires;
    // 0.26 is then new evidence and, after the 3 s hold, raises at t=69. A
    // second Level 2 within 5 min of the first is Level 3 (repeat-l2).
    eq(events(out), [[3, 2, "perclos"], [5, 0, "clear"], [69, 3, "repeat-l2"]]);
  }],
  ["NR1b/NR5: a tap on a PERCLOS Level 1 never re-raises from the same 60 s window; after it, only a reading still at 15% or more raises", async () => {
    // The latch's own rule, with the Level 1 notice spacing (row 42) off:
    // with it on, as by default, the notice at t=69 is 66 s after the one
    // at t=3 and is held back (checked at the end).
    const noSpacing = async () => { const c = await CFG(); c.level1.perclos_every_secs = 0; return c; };
    const rows = Array.from({ length: 90 }, (_, t) => [t, { perclos: 0.17 }, t === 5 ? { tap: true } : {}]);
    const out = drive(createLadder(await noSpacing()), rows);
    eq(out.slice(0, 5).map((o) => o.level), [0, 0, 0, 1, 1]);
    eq(out.slice(5, 69).every((o) => o.level === 0 && !o.raised), true);
    // NR5: 0.17 still at or above 15% once the window is all post-tap frames
    // raises a new notice at t=69, which clears on open eyes at t=75.
    eq(events(out), [[3, 1, "perclos"], [5, 0, "clear"], [69, 1, "perclos"], [75, 0, "clear"]]);
    // The same, with PERCLOS falling to 0.13 by then (below 15%, above the
    // 12% re-arm margin): the latch expires, and nothing is raised.
    const fell = drive(createLadder(await noSpacing()), Array.from({ length: 150 },
      (_, t) => [t, { perclos: t < 40 ? 0.17 : 0.13 }, t === 5 ? { tap: true } : {}]));
    eq(events(fell), [[3, 1, "perclos"], [5, 0, "clear"]]);
    // By default the second notice waits for the spacing.
    eq(events(drive(createLadder(await CFG()), rows)), [[3, 1, "perclos"], [5, 0, "clear"]]);
  }],
  ["NR1d (Important): PERCLOS draining from 0.40 with eyes open, tapped every 5 s, never reaches Level 3", async () => {
    // PERCLOS decays linearly from 0.40 (a 60 s window, eyes open from t=5):
    // p(t) = 0.40 * (60-(t-5))/60 after t=5. Round 2 alone answered every
    // tap with Level 3 (repeat-l2) 3.5 s later, four times, then Level 1
    // three times, while PERCLOS stayed at 25% or more.
    // Run on to t=150 (NR5): the latch taken at the first tap expires at
    // t=68, when PERCLOS has drained to 0, so there is still nothing to raise.
    const lad = createLadder(await CFG());
    const outs = [];
    for (let t = 0; t <= 150; t += 0.5) {
      const p = t < 5 ? 0.40 : Math.max(0, 0.40 * (60 - (t - 5)) / 60);
      const tap = t >= 8 && t <= 60 && Math.abs((t - 8) % 5) < 1e-9;
      outs.push(lad.step(go({ t, m: M({ t, perclos: +p.toFixed(4) }), tap })));
    }
    eq(events(outs), [[3, 2, "perclos"], [8, 0, "clear"]]);
  }],
  ["NR1c/NR5 (guard): 'I'm awake' at PERCLOS 0.30 with the eyes open from then on never re-raises, before or after the window turns over", async () => {
    // Through drowsy.js at 10 fps: 0.4 s closures (4 of 13 frames, never a
    // closure trigger) raise Level 2 by PERCLOS; the tap comes 30 s later
    // at about 0.30; the eyes stay open from the tap to tr=300. PERCLOS
    // drains to 0 within the 60 s the latch lasts, so neither the re-arm nor
    // the NR5 expiry finds anything at or above a threshold. The Level 1
    // variant uses 0.2 s closures (2 of 11 frames, about 0.18).
    for (const [level, shut, every] of [[2, 4, 13], [1, 2, 11]]) {
      const { ev, st } = await cabin(3000, (i, tr, s) => ({
        closed: tr >= 65 && s.tapAt === null && i % every < shut, tap: tapAfter30(s, tr) }));
      eq(ev, [[st.raisedAt, level, "perclos"], [st.tapAt, 0, "clear"]]);
    }
  }],
  ["B1 (NR5): a tap at 0.251 latches a gate that never raised; PERCLOS rising to 0.40 raises Level 2 once the window is all new frames", async () => {
    const rows = [];
    for (const t of range(0, 4)) rows.push([t, { perclos: 0.16 }]);                       // Level 1 at t=3
    for (const t of range(5, 20)) rows.push([t, { perclos: +(0.16 + (t - 5) * 0.006).toFixed(4) }]); // to 0.25
    rows.push([21, { perclos: 0.251 }, { tap: true }]);                                    // Level 2's hold 1 s in
    for (const t of range(22, 300)) rows.push([t, { perclos: Math.min(0.40, +(0.251 + (t - 21) * 0.0025).toFixed(4)) }]);
    const ev = events(drive(createLadder(await CFG()), rows));
    // Latched at t=21 (NR3); expired at t=82, when no frame from t=21 or
    // before is left in the window; Level 2 after the 3 s hold. Round 3
    // raised nothing in 279 s of 0.25-0.40. That Level 2 clears on open eyes
    // at t=91 and latches again; 64 s later the still-0.40 window is new
    // again and a second Level 2 within 5 min is Level 3.
    eq(ev, [[3, 1, "perclos"], [21, 0, "clear"], [85, 2, "perclos"], [91, 0, "clear"], [155, 3, "repeat-l2"]]);
  }],
  ["B1y (NR5): Level 1 by yawns, PERCLOS crossing 25% a second before the tap, then 0.30: Level 2 once the window is all new frames", async () => {
    const rows = [[0, { yawns: 3, perclos: 0.10 }], [1, { yawns: 3, perclos: 0.20 }], [2, { yawns: 3, perclos: 0.25 }],
      [3, { yawns: 3, perclos: 0.26 }, { tap: true }], ...range(4, 200).map((t) => [t, { yawns: 3, perclos: 0.30 }])];
    // Round 3: nothing in 197 s of 0.30. Now: latched at t=3, expired at
    // t=64, Level 2 at t=67; then as B1.
    eq(events(drive(createLadder(await CFG()), rows)),
      [[0, 1, "yawns"], [3, 0, "clear"], [67, 2, "perclos"], [73, 0, "clear"], [137, 3, "repeat-l2"]]);
  }],
  ["B2 (NR5): 'I'm awake' at 0.30 and the drowsiness carries on -- raised again 60 s (plus the 3 s hold) after the tap", async () => {
    // Through drowsy.js: 0.4 s closures raise Level 2 by PERCLOS; the tap
    // comes 30 s later; then either the same closures, or worse, 0.8 s
    // closures every 2 s (PERCLOS about 0.40, never a 1 s closure). Round 3
    // raised nothing for the remaining 300 s. Now the tap's latch expires as
    // the window turns over, and the second Level 2 within 5 min is Level 3.
    for (const worse of [false, true]) {
      const { ev, st } = await cabin(4200, (i, tr, s) => ({
        closed: worse && s.tapAt !== null ? i % 20 < 8 : tr >= 65 && i % 13 < 4, tap: tapAfter30(s, tr) }));
      eq(ev.map(([, l, k]) => [l, k]), [[2, "perclos"], [0, "clear"], [3, "repeat-l2"]]);
      eq([ev[0][0], ev[1][0]], [st.raisedAt, st.tapAt]);
      const after = +(ev[2][0] - st.tapAt).toFixed(3);
      eq(after > 60 && after <= 63.2, true);
    }
  }],
  ["C1 (NR5): the plan's red light, through drowsy.js -- the same closures before and after a 40 s stop raise again once the fresh window holds", async () => {
    // A 40 s light (no frames, the stale snapshot re-fed parked at 2 Hz),
    // then 10 s at 20 mph (frames resume: a discontinuity), then the drive.
    // The same 0.4 s closures throughout.
    const light = (L) => (i, tr) => {
      const ph = tr < L ? "drive" : tr < L + 40 ? "light" : tr < L + 50 ? "slow" : "drive";
      return { light: ph === "light", active: ph === "drive", closed: tr >= 65 && i % 13 < 4 };
    };
    // The light while Level 2 sounds (tr=100): the stop clears it and
    // latches both gates on the stale snapshot; the restart at tr=140
    // expires them; PERCLOS is null for its 30 s minimum window, then after
    // the 3 s hold the second Level 2 within 5 min is Level 3 -- 33 s after
    // the restart. Round 3 raised nothing in the 450 s after the light.
    // Task 9 fix round 1 (I1): the 10 s at 20 mph after the light fill the
    // fresh window below the gate, so the crossing at tr=150 latches both
    // gates; Level 3 comes once the window holds no frame from the crossing,
    // after the hold -- about 63 s after the crossing, not 33 s after the
    // restart.
    const a = (await cabin(6000, light(100))).ev;
    eq(a.map(([, l, k]) => [l, k]), [[2, "perclos"], [0, "clear"], [3, "repeat-l2"]]);
    eq(a[1][0], 100);
    const sinceCrossing = +(a[2][0] - 150).toFixed(3);
    eq(sinceCrossing > 60 && sinceCrossing <= 63.2, true);
    // The light early (tr=10, calibration not yet done): Level 2 at last,
    // which clears itself on open eyes (0.4 s closures are blinks) and
    // latches; the window turns over 60 s later and the closures that
    // carried on raise again, as Level 3.
    const b = (await cabin(6000, light(10))).ev;
    eq(b.map(([, l, k]) => [l, k]), [[2, "perclos"], [0, "clear"], [3, "repeat-l2"]]);
    const sinceRelease = +(b[2][0] - b[1][0]).toFixed(3);
    eq(sinceRelease > 60 && sinceRelease <= 63.2, true);
  }],
  // NR6: each of the latch's three ways out, pinned so deleting any one
  // fails here. Every case: Level 2 by PERCLOS at t=3, "I'm awake" at t=5
  // with 0.26 still read, which latches both gates (NR1/NR3).
  ["NR6 re-arm: 10 s below threshold - 0.03 frees a latch early; 9 s does not", async () => {
    const rows = (below) => [...range(0, 20).map((t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]),
      ...range(21, 20 + below).map((t) => [t, { perclos: 0.20 }]),
      ...range(21 + below, 90).map((t) => [t, { perclos: 0.26 }])];
    // 0.20 from t=21 re-arms Level 2's gate at t=31; 0.26 again from t=32
    // is a genuine new rise, held 3 s: Level 3 (repeat-l2) at t=35.
    eq(events(drive(createLadder(await CFG()), rows(11))), [[3, 2, "perclos"], [5, 0, "clear"], [35, 3, "repeat-l2"]]);
    // 9 s is not a recovery: the latch waits for the window to turn over.
    eq(events(drive(createLadder(await CFG()), rows(9))), [[3, 2, "perclos"], [5, 0, "clear"], [69, 3, "repeat-l2"]]);
  }],
  ["NR6 expiry: with no recovery, a latch lasts until the window holds no frame from its own step -- stale re-feeds never age it", async () => {
    // The plan's cadence: a step per 10 fps frame (m.t = the frame's t) and
    // a 2 Hz pollLive step re-feeding the last snapshot (m.t unchanged).
    const lad = createLadder(await CFG());
    const outs = [];
    let snap = null;
    const ticks = [...range(0, 90, 0.1).map((t) => [t, "frame"]), ...range(0, 90, 0.5).map((t) => [+(t + 0.03).toFixed(3), "poll"])]
      .sort((a, b) => a[0] - b[0]);
    for (const [t, kind] of ticks) {
      if (kind === "frame") snap = M({ t, perclos: 0.26 });
      outs.push(lad.step(go({ t, m: snap, tap: kind === "frame" && t === 5 })));
    }
    // At m.t=65.0 the frame from t=5.0 is exactly 60 s old and still in the
    // window (drowsy.js drops frames OLDER than 60 s); at 65.1 it has gone.
    // The polls at 65.03 re-feed m.t=65.0 and change nothing. The hold starts
    // at 65.1, not before: Level 3 at 68.1, and nothing in between.
    eq(events(outs), [[3, 2, "perclos"], [5, 0, "clear"], [68.1, 3, "repeat-l2"]]);
  }],
  ["NR6 discontinuity: a camera gap empties the window, so the latch goes at once; the fresh reading still needs 30 s and the 3 s hold", async () => {
    const rows = [...range(0, 9).map((t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]),
      [10, { perclos: null, discontinuity: true }],             // frames resume after a gap
      ...range(11, 39).map((t) => [t, { perclos: null }]),        // the measures' 30 s minimum window
      ...range(40, 90).map((t) => [t, { perclos: 0.26 }])];
    // Level 3 at t=43: 3 s of a reading made only of frames after the gap.
    // Without the discontinuity expiry it would wait for t=66, then t=69.
    eq(events(drive(createLadder(await CFG()), rows)), [[3, 2, "perclos"], [5, 0, "clear"], [43, 3, "repeat-l2"]]);
  }],
  ["NR6 camera restart: a snapshot clock that goes backwards is a restart, and expires the latch the same way", async () => {
    // A new measures stream on a new clock (m.t 0 at step t=10): its first
    // snapshot carries no discontinuity flag -- nothing came before it in
    // that stream -- and the ladder's "fresh m.t" test alone would never
    // see it as advancing. Its PERCLOS window starts empty all the same.
    const rows = [...range(0, 9).map((t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]),
      [10, { t: 0, perclos: null }],
      ...range(11, 39).map((t) => [t, { t: t - 10, perclos: null }]),
      ...range(40, 90).map((t) => [t, { t: t - 10, perclos: 0.26 }])];
    eq(events(drive(createLadder(await CFG()), rows)), [[3, 2, "perclos"], [5, 0, "clear"], [43, 3, "repeat-l2"]]);
  }],
  ["NR5: fresh frames below the gate turn the window over too; the hold itself still needs 3 s above it", async () => {
    // The latch is about which frames the window holds, and drowsy.js keeps
    // (and drops) frames whatever the speed. 95 s at 20 mph after the tap:
    // nothing is raised below the gate (I4), and the tap's latch has
    // expired, but the window is now all below-gate frames. Task 9 fix round
    // 1 (I1): that is not new evidence at the crossing (t=101), which
    // latches both gates again; the 0.26 raises only once the window holds
    // no frame from the crossing (m.t 162), after its hold -- 165, not 104.
    const rows = [...range(0, 5).map((t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]),
      ...range(6, 100).map((t) => [t, { perclos: 0.26 }, { active: false }]),
      ...range(101, 170).map((t) => [t, { perclos: 0.26 }])];
    eq(events(drive(createLadder(await CFG()), rows)), [[3, 2, "perclos"], [5, 0, "clear"], [165, 3, "repeat-l2"]]);
  }],
  ["NR2 (Important): yawns below the gate never mask a rising edge made of active yawns alone", async () => {
    // Three yawns below the gate reach the raw count of 3; then, while
    // genuinely active, real new yawns keep arriving. Round 2 alone tracked
    // was[] off drowsy.js's raw (below-gate-inclusive) count, so it never
    // saw a fresh rising edge once that count first reached 3.
    const rows = [[0, { yawns: 1 }, { active: false }], [30, { yawns: 2 }, { active: false }],
      [60, { yawns: 3 }, { active: false }], [90, { yawns: 3 }], [120, { yawns: 4 }], [150, { yawns: 5 }],
      [180, { yawns: 6 }]]; // a 3rd active increment (4->5->6)
    const out = drive(createLadder(await CFG()), rows);
    eq(out.every((o) => o.level === 0), false); // it does raise, somewhere in here
    eq(out.map((o) => (o.raised ? o.raised.trigger : null)).some((t) => t === "yawns"), true);
  }],
  ["NR7: a yawn counted on the very frame an old one leaves the measures' 5 min window still counts", async () => {
    // Through drowsy.js at 10 fps. Yawn A is counted below the gate at
    // t=100.0; B, C and D above it at 260.0, 330.0 and 400.1 -- and 400.1 is
    // the frame on which A, 300.1 s old, leaves drowsy.js's own window, so
    // its count reads 3 before and after. Three active yawns within 5 min
    // are Level 1, at the frame D is counted. D half a second later is the
    // control: the count dips to 2 first, then rises.
    for (const [dStart, want] of [[398.6, 400.1], [399.1, 400.6]]) {
      const cfg = await CFG();
      const meas = createMeasures(cfg), lad = createLadder(cfg);
      const starts = [98.5, 258.5, 328.5, dStart];
      const outs = [];
      for (let i = 0; i < 4200; i++) {
        const t = +(i * 0.1).toFixed(3);
        const jaw = starts.some((s) => t >= s && t < s + 1.8) ? 0.8 : 0.1;
        const active = t < 65 || t >= 250;
        const m = meas.feed({ t, face: true, blink: 0.1, jaw, pitch: 0, gated: active });
        outs.push(lad.step(go({ t, m, active })));
      }
      eq(events(outs).filter(([, l]) => l > 0), [[want, 1, "yawns"]]);
    }
  }],
  ["16 yawns above 30 mph over 15 minutes raise Level 1, including after below-gate yawns (NR2)", async () => {
    // m.yawns as drowsy.js reports it: the yawns in the last 300 s (NR7
    // fix round 4: round 3's rows never let a yawn leave that window).
    const at = [0, 0, 0];                                  // 3 below the gate: never counted
    for (let t = 280; t <= 1180; t += 60) at.push(t);      // 16 active yawns
    const count = (t) => at.filter((y) => y <= t && t - y <= 300).length;
    const rows = [[0, { yawns: count(0) }, { active: false }], ...at.slice(3).map((t) => [t, { yawns: count(t) }])];
    const out = drive(createLadder(await CFG()), rows);
    eq(out.some((o) => o.raised && o.raised.trigger === "yawns"), true);
  }],
  ["NR3/NR5: a Level 2 from a closure, released with PERCLOS steady at its own threshold, does not become a new Level 2 from the same 60 s window", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0, perclos: 0.26 }], // raises Level 2 by closure, PERCLOS already high
      [1, { perclos: 0.26 }], [2, { perclos: 0.26 }], [3, { perclos: 0.26 }], [4, { perclos: 0.26 }],
      [5, { perclos: 0.26 }], [6, { perclos: 0.26 }]]); // 5 s open (from t=1), PERCLOS steady -- releases
    eq(out.map((o) => o.level), [2, 2, 2, 2, 2, 2, 0]);
    eq(out.every((o) => o.level !== 3), true);
    // Real new evidence -- a fresh 1 s closure, well outside the 300 s
    // repeat-l2 window so this is a fresh episode -- still raises as normal.
    const out2 = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0, perclos: 0.26 }],
      [1, { perclos: 0.26 }], [2, { perclos: 0.26 }], [3, { perclos: 0.26 }], [4, { perclos: 0.26 }],
      [5, { perclos: 0.26 }], [6, { perclos: 0.26 }], [400, { closed: true, closedFor: 1.0 }]]);
    eq(out2[6].level, 0);
    eq([out2[7].level, out2[7].trigger], [2, "closed"]);
    // NR5, after the latch period: the release latched both gates at t=6.
    // With 0.26 continuing, nothing is raised while the window still holds
    // a frame from t=6 or before; at t=67 it does not, so 0.26 is new
    // evidence -- Level 2 after its 3 s hold, and Level 3 (repeat-l2),
    // since the closure's Level 2 was 70 s earlier.
    const on = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0, perclos: 0.26 }],
      ...range(1, 120).map((t) => [t, { perclos: 0.26 }])]);
    eq(events(on), [[0, 2, "closed"], [6, 0, "clear"], [70, 3, "repeat-l2"]]);
    // Falling to 0.23 by then (under 25%, but not under the 22% re-arm
    // margin): Level 2's gate expires with nothing to raise, and Level 1's,
    // which 0.23 still meets, raises a Level 1 notice.
    const fell = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0, perclos: 0.26 }],
      ...range(1, 120).map((t) => [t, { perclos: t < 20 ? 0.26 : 0.23 }])]);
    // (That notice then clears on 5 s of open eyes at t=76, as Level 1 does.)
    eq(events(fell), [[0, 2, "closed"], [6, 0, "clear"], [70, 1, "perclos"], [76, 0, "clear"]]);
  }],
  ["P13x: PERCLOS 16% that armed as escalation evidence while Level 2 sounded does not survive its tap and raise Level 1 (I4/NR3)", async () => {
    // Re-review 1's actual P13x: Level 2 is already up (from a closure), and
    // a PERCLOS 16% hold spans the tap that clears it. This fails against
    // d9591af, which raises Level 1 at t=5.
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }],
      [2, { perclos: 0.16 }, { active: false }],
      [3, { perclos: 0.16 }, { active: false, tap: true }],
      [4, { perclos: 0.16 }], [5, { perclos: 0.16 }]]);
    eq(out.map((o) => o.level), [2, 2, 0, 0, 0]);
  }],
  ["an open run that began with PERCLOS null can release once PERCLOS appears (N3)", async () => {
    // N3 detail (fix round 3): the step where PERCLOS first appears only
    // takes the baseline -- it cannot also satisfy "not rising" by comparing
    // that same reading with itself, so release is deferred one more step,
    // to a genuine second reading.
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }],   // raises Level 2
      [1, { perclos: null }],                   // eyes open, PERCLOS not available yet -- the run starts here
      [4, { perclos: null }],                   // still no baseline, and under 5 s anyway -- not yet
      [6, { perclos: 0.02 }],                    // PERCLOS appears, 5 s into the run -- takes the baseline, deferred
      [7, { perclos: 0.02 }]]);                   // a genuine second reading, not rising -- clears
    eq(out.map((o) => o.level), [2, 2, 2, 2, 0]);
  }],
  ["a real 0.45 s closure at 10 fps is always tolerated as a blink (N4)", async () => {
    // Concrete 10 fps frame sequences ([t, closed, closedFor]), not random:
    // an aligned run and two phase-shifted, jittered ones.
    const cases = [
      [[0, true, 0], [0.1, true, 0.1], [0.2, true, 0.2], [0.3, true, 0.3], [0.4, true, 0.4], [0.5, false, 0], [0.6, false, 0]],
      [[0.045, true, 0], [0.155, true, 0.11], [0.245, true, 0.2], [0.355, true, 0.31], [0.445, true, 0.4], [0.555, false, 0], [0.645, false, 0]],
      [[0.025, true, 0], [0.125, true, 0.1], [0.225, true, 0.2], [0.325, true, 0.3], [0.425, true, 0.4], [0.525, false, 0], [0.625, false, 0]],
    ];
    for (const frames of cases) eq(await closureTolerated(frames), true);
  }],
  ["a real 0.6 s closure at 10 fps is never tolerated as a blink (N4)", async () => {
    // These three (phase, jitter) combinations land the reported closedFor
    // at 0.49 s -- below the plain 0.5 s cutoff (which would wrongly read
    // this as a blink) but at or above the frame-compensated one.
    const cases = [
      [[0.025, true, 0], [0.115, true, 0.09], [0.225, true, 0.2], [0.315, true, 0.29], [0.425, true, 0.4], [0.515, true, 0.49], [0.625, false, 0], [0.715, false, 0]],
      [[0.055, true, 0], [0.145, true, 0.09], [0.255, true, 0.2], [0.345, true, 0.29], [0.455, true, 0.4], [0.545, true, 0.49], [0.655, false, 0], [0.745, false, 0]],
      [[0.065, true, 0], [0.155, true, 0.09], [0.265, true, 0.2], [0.355, true, 0.29], [0.465, true, 0.4], [0.555, true, 0.49], [0.665, false, 0], [0.755, false, 0]],
    ];
    for (const frames of cases) eq(await closureTolerated(frames), false);
  }],
  ["a camera-free Level 1 notice is quiet for 10 min after a tap; new closure/PERCLOS evidence still raises as normal (N8)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, {}, { hour: 3, tap: true }],  // a tap while nothing is sounding, at a night hour
      [1, {}, { hour: 3 }],              // immediately after -- the night notice must NOT fire here
      [599, {}, { hour: 3 }],            // still under 10 min since the tap
      [601, {}, { hour: 3 }],            // 10 min since the tap -- night can raise again
    ]);
    eq(out.map((o) => o.raised && o.raised.trigger), [null, null, null, "night"]);
    // A real closure right after the tap is not gated by the quiet period.
    const out2 = drive(createLadder(await CFG()), [[0, {}, { tap: true }],
      [1, { closed: true, closedFor: 1.0 }]]);
    eq(out2[1].level, 2);
  }],
  ["setConfig while an alert is sounding keeps the level and the Level 2 history (M9)", async () => {
    const c = await CFG();
    const lad = createLadder(c);
    const out1 = drive(lad, [[0, { closed: true, closedFor: 1.0 }]]); // raises Level 2 (l2Times = [0])
    eq(out1[0].level, 2);
    lad.setConfig(scaled(c, "sensitive")); // a settings change WHILE Level 2 is still sounding
    eq(lad.level, 2); // the level survived the call itself
    const out2 = drive(lad, [[1, {}, { tap: true }], [100, { closed: true, closedFor: 0.2 }],
      [101, { closed: true, closedFor: 0.9 }]]);
    // The Level 2 history survived too: a second Level 2 in the (scaled)
    // window becomes Level 3 via repeat-l2, not just another Level 2.
    eq([out2[2].level, out2[2].trigger], [3, "repeat-l2"]);
  }],
  ["a dropped link freezes sinceStop and does not grow stoppedFor; a reconnect restarts stoppedFor (I3)", async () => {
    const s = createStopClock(await CFG());
    s.feed(0, 110, true);
    s.feed(7000, 110, true);
    const down1 = s.feed(7010, 110, false);
    const down2 = s.feed(7160, 0, false);
    const up = s.feed(7310, 110, true);
    const later = s.feed(7311, 110, true);
    eq([down1.sinceStop, down1.stoppedFor], [7010, 0]);
    eq([down2.sinceStop, down2.stoppedFor], [7010, 0]);
    eq([up.sinceStop, up.stoppedFor], [7010, 0]);
    eq(later.sinceStop, 7011);
  }],
  ["a dropped link keeps Level 3 sounding: it clears only on a tap or a stopped car (I3/M9)", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }],
      [1, {}, { active: false, parked: false }]]);
    eq(out.map((o) => o.level), [3, 3]);
  }],
  ["a doze at a rest stop does not raise at the next 30 mph crossing (I4)", async () => {
    const out = drive(createLadder(await CFG()), [
      [1000, { perclos: 0.9 }, { active: false, parked: true }],
      [1001, { perclos: 0.9 }, { active: false, parked: true }],
      [1002, { perclos: 0.9 }, { active: false, parked: true }],
      [1003, { perclos: 0.9 }, { active: false, parked: true }],  // even a full 3 s hold here is wiped
      [1220, { perclos: 0.6 }, { active: false, parked: false }], // pulls away, still below the gate
      [1240, { perclos: 0.3 }, { active: true, parked: false }]]); // now above the gate
    eq(out.map((o) => o.level), [0, 0, 0, 0, 0, 0]);
  }],
  ["a tap discards armed-but-unused evidence even with nothing currently sounding (I4)", async () => {
    // A plain rising-edge trigger (yawns), not PERCLOS: PERCLOS's own C1/I1
    // hysteresis would mask this fix, since it cannot re-arm from the same
    // reading either way. This isolates I4's own discard.
    const out = drive(createLadder(await CFG()), [
      [0, { yawns: 3 }, { active: false }],              // arms Level 1's yawn trigger, below the gate
      [1, { yawns: 3 }, { active: false, tap: true }],   // "I'm awake" -- nothing sounding, but discards it too
      [2, { yawns: 3 }]]);                                // back above the gate, same reading -- must not raise
    eq(out.map((o) => o.level), [0, 0, 0]);
  }],
  ["an alert already sounding may still escalate below the gate; no NEW alert starts there (M8)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }],                     // raises Level 2, above the gate
      [1, { closed: true, closedFor: 2.0 }, { active: false }]]); // a 2 s closure below the gate -- same episode
    eq(out.map((o) => o.level), [2, 3]);
    eq(out[1].trigger, "closed");
  }],
  ["tapping at Level 0 does nothing", async () => {
    const out = drive(createLadder(await CFG()), [[0, {}, { tap: true }]]);
    eq([out[0].level, out[0].cleared, out[0].cues], [0, false, []]);
  }],
  ["the rotation restarts at the first sound for each new Level 2 episode (M1)", async () => {
    // 400 s apart, well outside the 300 s repeat-l2 window, so this is two
    // unrelated episodes, not evidence toward Level 3.
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }],
      [6, {}, { tap: true }], [400, { closed: true, closedFor: 1.0 }]]);
    eq([kinds(out[0]), kinds(out[3])], [["duck", "bark"], ["duck", "bark"]]);
  }],
  ["Level 3's held alarm falls silent below the gate when alert_continues_below_gate is false, and restarts above it (M2)", async () => {
    const c = await CFG();
    c.alert_continues_below_gate = false;
    const out = drive(createLadder(c), [[0, { closed: true, closedFor: 2.0 }],
      [1, {}, { active: false }], [2, {}, { active: false }], [3, {}]]);
    eq(kinds(out[1]), ["fade"]);
    eq(kinds(out[2]), []);
    // N6: the restart re-sends duck and voice:l3 too, not a bare alarm.
    eq(kinds(out[3]), ["duck", "alarm", "voice:l3"]);
    eq(out.map((o) => o.level), [3, 3, 3, 3]);
  }],
  ["Level 3's held alarm keeps sounding below the gate by default (M2)", async () => {
    const out = drive(createLadder(await CFG()), [[0, { closed: true, closedFor: 2.0 }],
      [1, {}, { active: false }], [15, {}, { active: false }]]);
    eq(kinds(out[1]), []);
    eq(kinds(out[2]), ["voice:l3"]);
  }],
  ["the hourly camera-free budget is spent only when an alert is actually raised (M3)", async () => {
    const out = drive(createLadder(await CFG()), [
      [0, { closed: true, closedFor: 1.0 }, { hour: 3 }], // closed wins; night is eligible but goes unused
      [1, {}, { tap: true, hour: 14 }],                    // clears, in daytime -- no night evidence here
      // Only 1800 s later -- inside the hourly interval. If seeing "night" at
      // t=0 had spent the budget (even though "closed" won that step), this
      // would still be blocked; since it never actually raised, it is not.
      [1800, {}, { hour: 3 }]]);
    eq(out[2].raised && out[2].raised.trigger, "night");
  }],
  ["scaled() is idempotent (M4)", async () => {
    const c = await CFG();
    const once = scaled(c, "sensitive");
    const twice = scaled(once, "sensitive");
    eq(twice, once);
  }],
  ["setConfig without a sounds list keeps the current list, never re-adding voice (M5)", async () => {
    const c = await CFG();
    const lad = createLadder(c, ["alarm", "bark"]);
    lad.setConfig(c); // no sounds argument -- must not fall back to c.sounds (which includes "voice")
    const out = drive(lad, [[0, { closed: true, closedFor: 1.0 }], [5, { face: false }]]);
    eq(out.map(kinds), [["duck", "alarm"], ["bark"]]);
  }],
  ["a ladder built with no sounds list anywhere falls back to alarm and never throws (N5)", async () => {
    const c = await CFG();
    delete c.sounds; // no sounds field at all -- createLadder(cfg) alone
    const lad = createLadder(c);
    const out = drive(lad, [[0, { closed: true, closedFor: 1.0 }]]);
    eq(out.map(kinds), [["duck", "alarm"]]);
  }],
  ["isNight handles a window that wraps past midnight (M6)", async () => {
    const c = await CFG();
    c.level1.night_from_hour = 22;
    c.level1.night_to_hour = 6;
    eq([21, 22, 23, 0, 5, 6].map((h) => isNight(h, c)), [false, true, true, true, true, false]);
  }],
  ["new settings keep the ladder's memory", async () => {
    const c = await CFG();
    const lad = createLadder(c);
    drive(lad, [[0, { closed: true, closedFor: 1.0 }], [1, {}, { tap: true }]]);
    lad.setConfig(scaled(c, "sensitive"));
    const out = drive(lad, [[100, { closed: true, closedFor: 0.2 }], [101, { closed: true, closedFor: 0.9 }]]);
    eq([out[1].level, out[1].trigger], [3, "repeat-l2"]);
  }],
  // Task 9 fix round 1, I1: crossing into active with nothing sounding
  // latches both PERCLOS gates (drowsyrun.js feeds below-gate frames). With
  // an alert already sounding it latches nothing: the owner-approved rule
  // that new evidence may escalate a sounding alert stands.
  ["crossing into active with an alert already sounding latches nothing: PERCLOS still escalates it", async () => {
    const lad = createLadder(await CFG());
    const first = drive(lad, [[0, {}, { hour: 3 }]]);
    eq(events(first), [[0, 1, "night"]]);
    // below the gate, PERCLOS rising but under Level 2's 25% (so Level 1 is not released)
    const below = drive(lad, range(1, 10).map((t) => [t, { perclos: +(0.2 + t / 1000).toFixed(3) }, { active: false, hour: 3 }]));
    const above = drive(lad, range(11, 16).map((t) => [t, { perclos: 0.26 }, { hour: 3 }]));
    eq([events(below), events(above)], [[], [[14, 2, "perclos"]]]);
  }],
  ["crossing into active at level 0 latches both PERCLOS gates at that step's m.t: nothing raises from the window it had", async () => {
    const lad = createLadder(await CFG());
    const below = drive(lad, range(0, 20).map((t) => [t, { perclos: 0.3 }, { active: false }]));
    const above = drive(lad, range(21, 81).map((t) => [t, { perclos: 0.3 }]));
    const later = drive(lad, range(82, 86).map((t) => [t, { perclos: 0.3 }]));
    // Latched at m.t 21: the window holds a frame from t=21 until m.t 81;
    // the hold starts at 82 and raises 3 s later.
    eq([events(below), events(above), events(later)], [[], [], [[85, 2, "perclos"]]]);
  }],
  // Task 9 fix round 3: a no-car-data step (gateKnown false: link down,
  // speed unreadable, answer late) is skipped when looking for a crossing.
  ["active, then no car data, then active is not a crossing: nothing is latched", async () => {
    const lad = createLadder(await CFG());
    const out = drive(lad, [[0, { perclos: 0.02 }], [1, { perclos: 0.02 }, { active: false, gateKnown: false }],
      ...range(2, 6).map((t) => [t, { perclos: 0.3 }])]);
    eq(events(out), [[5, 2, "perclos"]]);   // the 3 s hold from t=2, as if no spell had come
  }],
  ["below the gate, then no car data, then active is a crossing: both gates latch", async () => {
    const lad = createLadder(await CFG());
    const out = drive(lad, [...range(0, 5).map((t) => [t, { perclos: 0.3 }, { active: false }]),
      ...range(6, 8).map((t) => [t, { perclos: 0.3 }, { active: false, gateKnown: false }]),
      ...range(9, 75).map((t) => [t, { perclos: 0.3 }])]);
    eq(events(out), [[73, 2, "perclos"]]);
  }],

  // ---- final review, row 42: a PERCLOS Level 1 notice at most every 5 minutes.
  // PERCLOS sitting between 15% and 25% used to raise Level 1 every ~70 s:
  // each release latched perclos1 (NR3), the latch expired as the window
  // turned over (NR5), and the 3 s hold raised again -- a chime, a line and a
  // 40 s radio swell more than half the time, which teaches a driver to
  // switch drowsy mode off, and with it Levels 2 and 3. Now a PERCLOS Level
  // 1 comes at most once every level1.perclos_every_secs (300 s; 0 is off).
  // An armed perclos1 held back by it is used up without a latch, so it
  // re-arms as usual; nothing else is spaced.
  ["PERCLOS held at 20% raises Level 1 at most once in 5 minutes (row 42)", async () => {
    // Level 1 at t=3, cleared on open eyes at t=9, which latches perclos1.
    // The latch expires at t=70 and the gate re-arms every 4 s from t=73,
    // each one held back and used up, until t=305, 302 s after the first
    // notice; the same again from there to t=607.
    const out = drive(createLadder(await CFG()), range(0, 700).map((t) => [t, { perclos: 0.2 }]));
    const notices = events(out).filter(([, l]) => l === 1).map(([t]) => t);
    eq(events(out), [[3, 1, "perclos"], [9, 0, "clear"], [305, 1, "perclos"], [311, 0, "clear"],
                     [607, 1, "perclos"], [613, 0, "clear"]]);
    eq(notices.slice(1).every((t, i) => t - notices[i] >= 300), true);
  }],
  ["and through drowsy.js, 0.4 s closures every 2 s for 10 minutes: Level 1 at most once in any 5 minutes, and it does come back", async () => {
    // PERCLOS about 20%, every closure a blink to the release (under 0.5 s).
    const { ev } = await cabin(6600, (i, tr) => ({ closed: tr >= 60 && i % 20 < 4 }));
    const notices = ev.filter(([, l, k]) => l === 1 && k === "perclos").map(([t]) => t);
    eq([ev.every(([, l]) => l <= 1), notices.length >= 2, notices.slice(1).every((t, i) => t - notices[i] >= 300)],
       [true, true, true], JSON.stringify(ev));
  }],
  ["a climb to 25% inside those 5 minutes still raises Level 2, and a second Level 2 still makes Level 3 (row 42)", async () => {
    // Level 1 at t=3 and its release at t=9 as above; nothing at t=73 (held
    // back); 0.26 from t=100 raises Level 2 after its own 3 s hold. Its
    // release at t=109 latches both gates; at t=173 the window is new and the
    // second Level 2 within 5 min is Level 3.
    const out = drive(createLadder(await CFG()), range(0, 200).map((t) => [t, { perclos: t < 100 ? 0.2 : 0.26 }]));
    eq(events(out), [[3, 1, "perclos"], [9, 0, "clear"], [103, 2, "perclos"], [109, 0, "clear"], [173, 3, "repeat-l2"]]);
  }],
  ["closure triggers, Level 3 and a Level 1 by yawns are untouched by the PERCLOS notice spacing (row 42)", async () => {
    // Inside the 5 minutes after a PERCLOS notice: a 1 s closure is Level 2
    // and 2 s is Level 3, at once.
    const closure = drive(createLadder(await CFG()), [...range(0, 20).map((t) => [t, { perclos: 0.2 }]),
      [21, { perclos: 0.2, closed: true, closedFor: 1.0 }], [22, { perclos: 0.2, closed: true, closedFor: 2.0 }]]);
    eq(events(closure), [[3, 1, "perclos"], [9, 0, "clear"], [21, 2, "closed"], [22, 3, "closed"]]);
    // And three yawns are a Level 1 notice of their own, spacing or not.
    const yawns = drive(createLadder(await CFG()), [...range(0, 20).map((t) => [t, { perclos: 0.2 }]),
      [21, { perclos: 0.2, yawns: 3 }]]);
    eq(events(yawns), [[3, 1, "perclos"], [9, 0, "clear"], [21, 1, "yawns"]]);
  }],
  ["the notice spacing is 300 s by default, is not a threshold Sensitive lowers, and 0 turns it off (row 42)", async () => {
    const c = await CFG();
    eq([c.level1.perclos_every_secs, scaled(c, "sensitive").level1.perclos_every_secs], [300, 300]);
    c.level1.perclos_every_secs = 0;
    const out = drive(createLadder(c), range(0, 150).map((t) => [t, { perclos: 0.2 }]));
    eq(events(out), [[3, 1, "perclos"], [9, 0, "clear"], [73, 1, "perclos"], [79, 0, "clear"],
                     [143, 1, "perclos"], [149, 0, "clear"]]);
  }],
];
