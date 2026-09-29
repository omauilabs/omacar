import { eq } from "./assert.js";
import { createLadder, createStopClock, scaled, isNight } from "../js/ladder.js";

const CFG = async () => (await fetch("../data/drowsy.json")).json();
const M = (o) => Object.assign({ face: true, calibrated: true, closed: false, closedFor: 0, openFor: 0,
  perclos: 0.02, yawns: 0, nods: 0, faceLost: false }, o);
const go = (o) => Object.assign({ active: true, parked: false, sinceStop: 0, stoppedFor: 0, hour: 14, tap: false }, o);
// Step a ladder through [t, measures, extra] rows; every output comes back.
// m.t defaults to the row's own t (a genuine new frame every row, as in
// every test but P2b/I2, which overrides it to freeze a stale snapshot).
const drive = (lad, rows) => rows.map(([t, m, x]) => lad.step(go(Object.assign({ t, m: M(Object.assign({ t }, m)) }, x))));
const kinds = (out) => out.cues.map((c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind));
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
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }],
      [1, { perclos: 0.16 }, { active: false }], [2, { perclos: 0.16 }, { active: false }],
      [3, { perclos: 0.16 }, { active: false }], // 3 s below the gate -- still nothing armed
      [4, { perclos: 0.16 }], [5, { perclos: 0.16 }], [6, { perclos: 0.16 }], // active -- hold starts fresh at t=4
      [7, { perclos: 0.16 }]]);                                                // 3 s genuinely active -- raises now
    eq(out.map((o) => o.level), [0, 0, 0, 0, 0, 0, 0, 1]);
  }],
  ["toggling active on and off resets the hold each time; only a sustained run raises (I4)", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }, { active: false }],                          // inactive -- resets the hold
      [3, { perclos: 0.16 }], [4, { perclos: 0.16 }], [5, { perclos: 0.16 }], // active again -- hold restarts at t=3
      [6, { perclos: 0.16 }]]);                                             // 3 s since the restart -- raises
    eq(out.map((o) => !!o.raised), [false, false, false, false, false, false, true]);
  }],
  ["crossing the gate repeatedly with sustained evidence still raises Level 2 only once, and never re-raises through a tap into Level 3 (NR1/NR3)", async () => {
    // Extended past its original tap (fix round 3): under round 2 alone this
    // ran on 3 s longer and reached Level 3 via repeat-l2 at t=10.
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.26 }, { active: false }], // nothing armed
      [1, { perclos: 0.26 }], [2, { perclos: 0.26 }], [3, { perclos: 0.26 }],
      [4, { perclos: 0.26 }],                          // 3 s active from t=1 -- raises Level 2
      [5, { perclos: 0.26 }, { tap: true }],            // "I'm awake" clears it -- and latches the gate (NR3)
      [6, { perclos: 0.26 }, { active: false }], [7, { perclos: 0.26 }],
      [8, { perclos: 0.26 }], [9, { perclos: 0.26 }], [10, { perclos: 0.26 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0]);
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
  ["N1b: a single parked step does not discard an unused arm; sustained evidence still raises promptly", async () => {
    const lad = createLadder(await CFG());
    const out1 = drive(lad, [[0, { perclos: 0.26 }], [1, { perclos: 0.26 }], [2, { perclos: 0.26 }],
      [3, { closed: true, closedFor: 2.0, perclos: 0.26 }]]); // Level 3; perclos2 arms unused, as in N1a
    eq(out1[3].level, 3);
    // One short parked step -- clears the level (a stopped car always does),
    // but is not a discard (I4): it is not a 5 min stop, and not a tap.
    const out2 = drive(lad, [[4, {}, { active: false, parked: true }]]);
    eq(out2[0].level, 0);
    const out3 = drive(lad, [[5, { perclos: 0.27 }], [6, { perclos: 0.27 }], [7, { perclos: 0.27 }],
      [8, { perclos: 0.27 }]]);
    eq(out3.map((o) => o.level), [0, 0, 0, 2]);
  }],
  ["N1c: PERCLOS crossing 25% during a short (under 5 min) stop, with a Level 1 already up, escalates rather than getting lost", async () => {
    const lad = createLadder(await CFG());
    const out1 = drive(lad, [[0, { yawns: 3 }]]); // raises Level 1
    eq(out1[0].level, 1);
    // A 40 s traffic light: inactive, well under the 5 min discard threshold,
    // so the escalation evidence is not thrown away (M8's carve-out applies
    // to arming too, since Level 1 is already up).
    const out2 = drive(lad, [[1, { perclos: 0.26 }, { active: false }], [2, { perclos: 0.26 }, { active: false }],
      [3, { perclos: 0.26 }, { active: false }], [4, { perclos: 0.26 }, { active: false }]]);
    eq(out2.map((o) => o.level), [1, 1, 1, 2]);
    // perclos2 actually raised Level 2 at t=4, so it is latched (NR1) and a
    // tap cannot free it -- correctly: PERCLOS never actually recovered, so
    // Level 2 must not be reachable again from the same sustained episode.
    // perclos1 was consumed unused at t=4 (perclos2 won that step) and was
    // never latched, so the tap's discard does reset it: sustained PERCLOS
    // above the gate reaches Level 1 again within about 3 s, not stuck.
    const out3 = drive(lad, [[400, {}, { tap: true }], [401, { perclos: 0.28 }], [402, { perclos: 0.28 }],
      [403, { perclos: 0.28 }], [404, { perclos: 0.28 }]]);
    eq(out3.map((o) => o.level), [0, 0, 0, 0, 1]);
  }],
  ["NR1a (Important): a tap on a PERCLOS Level 2 never re-raises from the same continuing 0.26", async () => {
    // PERCLOS is a 60 s window and stays high well after the tap. Round 2
    // alone let the tap free the gate that had just raised, so the same
    // 0.26 re-raised about 3 s later -- Level 2 became Level 3 (repeat-l2).
    const out = drive(createLadder(await CFG()),
      Array.from({ length: 20 }, (_, t) => [t, { perclos: 0.26 }, t === 5 ? { tap: true } : {}]));
    eq(out.slice(0, 5).map((o) => o.level), [0, 0, 0, 2, 2]);
    eq(out.slice(5).every((o) => o.level === 0), true);
    eq(out.every((o) => o.level !== 3), true);
  }],
  ["NR1b (Important): a tap on a PERCLOS Level 1 never re-raises from the same continuing 0.17", async () => {
    const out = drive(createLadder(await CFG()),
      Array.from({ length: 20 }, (_, t) => [t, { perclos: 0.17 }, t === 5 ? { tap: true } : {}]));
    eq(out.slice(0, 5).map((o) => o.level), [0, 0, 0, 1, 1]);
    eq(out.slice(5).every((o) => o.level === 0), true);
  }],
  ["NR1d (Important): PERCLOS draining from 0.40 with eyes open, tapped every 5 s, never reaches Level 3", async () => {
    // PERCLOS decays linearly from 0.40 (a 60 s window, eyes open from t=5):
    // p(t) = 0.40 * (60-(t-5))/60 after t=5. Round 2 alone answered every
    // tap with Level 3 (repeat-l2) 3.5 s later, four times, then Level 1
    // three times, while PERCLOS stayed at 25% or more.
    const lad = createLadder(await CFG());
    const events = [];
    for (let t = 0; t <= 60; t += 0.5) {
      const p = t < 5 ? 0.40 : Math.max(0, 0.40 * (60 - (t - 5)) / 60);
      const tap = t >= 8 && Math.abs((t - 8) % 5) < 1e-9;
      const o = lad.step(go({ t, m: M({ t, perclos: +p.toFixed(4) }), tap }));
      if (o.raised || o.cleared) events.push([t, o.level, o.raised ? o.raised.trigger : "clear"]);
    }
    eq(events, [[3, 2, "perclos"], [8, 0, "clear"]]);
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
  ["16 yawns above 30 mph over 15 minutes raise Level 1, including after below-gate yawns (NR2)", async () => {
    const rows = [[0, { yawns: 3 }, { active: false }]]; // 3 below the gate: never counted
    for (let t = 280, n = 0; t <= 1180; t += 60) { n++; rows.push([t, { yawns: 3 + n }]); } // 15 active yawns
    const out = drive(createLadder(await CFG()), rows);
    eq(out.some((o) => o.raised && o.raised.trigger === "yawns"), true);
  }],
  ["NR3 (product call): a Level 2 from a closure, released with PERCLOS steady at its own threshold, does not become a new Level 2", async () => {
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
];
