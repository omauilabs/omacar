import { eq } from "./assert.js";
import { signView, OverTracker, speedMph, CREDIT } from "../js/speedlimit.js";

// What the speed-limit sign says and when the speed number warns
// (share/js/speedlimit.js). Never a guess, never a default passed off as
// posted, and the simulator is always named.
const body = (over = {}) => Object.assign({
  present: true, limit: 35, source: "posted", confidence: "high", road: "Main St",
  location_source: "gpsd", fix: true, gps_mph: 30,
}, over);

// A run of one update a second, returning whether the last one is "over".
const run = (tr, speeds, limit = 35, source = "posted", t0 = 0) =>
  speeds.map((sp, i) => tr.update(sp, limit, source, t0 + i * 1000)).pop();

export default [
  ["a confident posted limit is a sign with the number and 'Limit'", () => {
    const v = signView(body());
    eq([v.show, v.text, v.unit, v.kind, v.caption], [true, "35", "mph", "posted", "Limit"]);
  }],
  ["a default is hollow, says Default, and is never 'posted'", () => {
    const v = signView(body({ limit: 25, source: "default" }));
    eq([v.show, v.kind, v.caption, /not posted/.test(v.title)], [true, "default", "Default", true]);
  }],
  ["low confidence, no limit and no database show nothing", () => eq(
    [signView(body({ confidence: "low", limit: null })).show, signView(body({ limit: null })).show,
     signView({ present: false }).show, signView(null).show, signView(body({ confidence: "none" })).show],
    [false, false, false, false, false])],
  ["a limit with a missing confidence is not trusted", () => eq(signView(body({ confidence: undefined })).show, false)],
  ["the simulator is always written under the sign", () => {
    const v = signView(body({ location_source: "sim" }));
    eq([v.sim, v.caption, /simulated/i.test(v.title)], [true, "Simulated", true]);
  }],
  ["the OpenStreetMap credit is in the tooltip every time", () => eq(
    [signView(body()).title.includes(CREDIT), signView(body({ source: "default" })).title.includes("OpenStreetMap")], [true, true])],
  ["metric shows km/h, rounded", () => {
    const v = signView(body({ limit: 35 }), { imperial: false });
    eq([v.text, v.unit], ["56", "km/h"]);
  }],
  ["the speed: the car when live, else the receiver, else nothing", () => eq(
    [Math.round(speedMph({ carKmh: 96.56, carLive: true, gpsMph: 10 })), speedMph({ carKmh: 96.5, carLive: false, gpsMph: 42 }),
     speedMph({ carKmh: NaN, carLive: true, gpsMph: null }), speedMph({ carKmh: 0, carLive: true, gpsMph: 9 })],
    [60, 42, null, 0])],
  ["over by 6 for 6 seconds warns; the sixth second is not yet 'more than 5 s'", () => {
    const tr = new OverTracker();
    const early = tr.update(41, 35, "posted", 0);
    const at5 = tr.update(41, 35, "posted", 1000) || tr.update(41, 35, "posted", 2000) || tr.update(41, 35, "posted", 3000)
      || tr.update(41, 35, "posted", 4000) || tr.update(41, 35, "posted", 5000);
    eq([early, at5, tr.update(41, 35, "posted", 5001), tr.update(41, 35, "posted", 6000)], [false, false, true, true]);
  }],
  ["exactly 5 over never warns, however long", () => eq(run(new OverTracker(), Array(30).fill(40)), false)],
  ["over by 5.1 does, after the hold", () => eq(run(new OverTracker(), Array(8).fill(40.1)), true)],
  ["dipping back under resets the clock", () => {
    const tr = new OverTracker();
    run(tr, [42, 42, 42, 42, 42]);                       // 4 s of over
    tr.update(36, 35, "posted", 5000);                   // back within 5
    eq([tr.update(42, 35, "posted", 6000), tr.update(42, 35, "posted", 10000)], [false, false]);
  }],
  ["a default limit never warns, even 30 over for a minute", () =>
    eq(run(new OverTracker(), Array(60).fill(60), 25, "default"), false)],
  ["no limit or no speed never warns", () => {
    const tr = new OverTracker();
    eq([run(tr, Array(10).fill(90), null, "posted"), run(new OverTracker(), Array(10).fill(null), 35, "posted")], [false, false]);
  }],
  ["losing the limit mid-run clears it at once", () => {
    const tr = new OverTracker();
    run(tr, Array(8).fill(50));
    eq(tr.update(50, null, null, 8000), false);
  }],
  ["a long silence is a new run, not a continuation", () => {
    const tr = new OverTracker();
    tr.update(50, 35, "posted", 0);
    const after = tr.update(50, 35, "posted", 60000);            // 60 s later: one second into a new run
    const steps = [1, 2, 3, 4, 5].map((i) => tr.update(50, 35, "posted", 60000 + i * 1000));
    eq([after, steps, tr.update(50, 35, "posted", 66000)], [false, [false, false, false, false, false], true]);
  }],
];
