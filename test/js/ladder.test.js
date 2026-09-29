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
    // PERCLOS must hold continuously for 3 s before it arms (C1/I1), below
    // the gate or above it, and the raise fires the moment it is both armed
    // and above 30 mph.
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }],
      [1, { perclos: 0.16 }, { active: false }], [2, { perclos: 0.16 }, { active: false }], [3, { perclos: 0.16 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 1]);
  }],
  ["hovering around 30 mph with the same evidence raises once, never again", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.16 }, { active: false }], [1, { perclos: 0.16 }],
      [2, { perclos: 0.16 }, { active: false }], [3, { perclos: 0.16 }], [4, { perclos: 0.16 }, { tap: true }],
      [5, { perclos: 0.16 }, { active: false }], [6, { perclos: 0.16 }]]);
    eq(out.map((o) => !!o.raised), [false, false, false, true, false, false, false]);
  }],
  ["crossing the gate never builds two Level 2s into Level 3", async () => {
    const out = drive(createLadder(await CFG()), [[0, { perclos: 0.26 }, { active: false }],
      [1, { perclos: 0.26 }, { active: false }], [2, { perclos: 0.26 }, { active: false }], [3, { perclos: 0.26 }],
      [4, { perclos: 0.26 }, { tap: true }], [5, { perclos: 0.26 }, { active: false }], [6, { perclos: 0.26 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 2, 0, 0, 0]);
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
  ["a cleared Level 2 cannot re-raise from the same ~25% PERCLOS until it has re-armed (I1)", async () => {
    const lad = createLadder(await CFG());
    const out = drive(lad, [[0, { perclos: 0.26 }], [1, { perclos: 0.26 }], [2, { perclos: 0.26 }],
      [3, { perclos: 0.26 }], [4, { perclos: 0.26 }], [9, { perclos: 0.26 }], [10, { perclos: 0.26 }]]);
    eq(out.map((o) => o.level), [0, 0, 0, 2, 2, 0, 0]);
    eq(!!out[6].raised, false);
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
    eq(kinds(out[3]), ["alarm"]);
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
