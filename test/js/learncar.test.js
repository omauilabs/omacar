import { eq } from "./assert.js";
import { learnView, candidateView, resultsView, where, secs } from "../js/learncar.js";

// What Learn my car says (share/js/learncar.js): the script, the guided step,
// the results, and that parked-only greys controls rather than hiding them.
const steps = (over = {}) => [
  { id: "ignition", title: "Ignition to II, engine off", kicker: "IMA readiness", engine: false, seconds: 20, state: "pending", frames: 0, windows: 0 },
  { id: "brake", title: "Brake pedal", kicker: "Brake switch", engine: false, seconds: 31, state: "pending", frames: 0, windows: 0 },
  { id: "engine", title: "Start the engine, idle 20 s", kicker: "IMA assist and charge", engine: true, seconds: 60, state: "pending", frames: 0, windows: 0 },
].map((s, i) => Object.assign(s, over[i] || {}));

const idle = (over = {}) => Object.assign({ present: false, status: "idle", steps: steps(), idx: 0, current: null,
  results: null, results_sim: null }, over);

const cur = (over = {}) => Object.assign({ n: 2, of: 3, id: "brake", title: "Brake pedal", kicker: "Brake switch",
  prep: "Ignition ON (II)", engine: false, confirmed: false, frames: 4210, segments: [
    { kind: "rest", state: "released", seconds: 4 }, { kind: "act", state: "held", seconds: 5 }],
  countdown: null, seg: null, say: null, kind: null, remaining: null, windows: 0 }, over);

const running = (phase, c, over = {}) => Object.assign({ present: true, status: "running", source: "sim", phase, idx: 1,
  steps: steps([{ state: "done", frames: 900, windows: 2 }]), current: cur(c), blocked: null }, over);

const cand = (over = {}) => Object.assign({ id: "17C", byte: 3, kind: "switch", rest_value: 0, active_value: 0x40,
  hits: 18, windows: 18, strong: true, also_in: [], span: [3, 3],
  text: "0x17C byte 3 changes only while the brake is held — 18/18 windows" }, over);

export default [
  ["a byte or a 16-bit pair is named as the screen says it", () => {
    eq(where({ id: "17C", byte: 3, span: [3, 3] }), { id: "0x17C", bytes: "byte 3" });
    eq(where({ id: "1A6", byte: 0, span: [0, 1] }), { id: "0x1A6", bytes: "bytes 0–1" });
  }],
  ["a switch candidate reads as 18/18 with its values and a full meter", () => {
    const v = candidateView(cand());
    eq([v.id, v.bytes, v.what, v.frac, v.pct, v.strong, v.tone, v.value],
      ["0x17C", "byte 3", "changes only while the brake is held", "18/18 windows", 100, true, "ok", "00 → 40"]);
  }],
  ["a hint is a warn tone and says so, singular for one window", () => {
    const v = candidateView(cand({ hits: 1, windows: 1, strong: false }));
    eq([v.frac, v.tone, v.pct], ["1/1 window", "warn", 100]);
  }],
  ["an analog value has no from-to, and where else it moves is named", () => {
    const v = candidateView(cand({ kind: "analog", rest_value: 0, active_value: null, also_in: ["hazards", "rev"] }));
    eq([v.value, v.also], ["analog", "Also moves during hazards, rev"]);
  }],
  ["results keep the top three and count the rest", () => {
    const l = { label: "brake", title: "Brake pedal", kicker: "Brake switch", windows: 3, phrase: "x",
      candidates: [cand(), cand({ byte: 4 }), cand({ byte: 5 }), cand({ byte: 6 })] };
    const r = resultsView({ captures: 4, labels: { brake: l } });
    eq([r.blocks[0].candidates.length, r.blocks[0].more, r.captures, r.empty], [3, 1, 4, false]);
  }],
  ["nothing standing out is a result with a reason, not a blank", () => {
    const r = resultsView({ captures: 1, labels: { brake: { label: "brake", title: "Brake pedal", kicker: "k", windows: 3, candidates: [] } } });
    eq([r.blocks[0].none, r.blocks[0].noneWhy.length > 20], [true, true]);
  }],
  ["no results at all is empty", () => eq(resultsView(null).empty, true)],
  ["seconds round up and never go below zero", () => eq([secs(4.2), secs(0.01), secs(-3), secs(5)], ["5", "1", "0", "5"])],

  ["before any session: the routine, Car as the source, Begin enabled", () => {
    const v = learnView(idle());
    eq([v.screen, v.source.text, v.canStart, v.steps.length, v.steps[0].lamp], ["start", "Car", true, 3, "pending"]);
  }],
  ["choosing Simulated is said before it starts", () =>
    eq(learnView(idle(), { prefs: { sim: true } }).source, { text: "Simulated", tone: "warn" })],
  ["moving greys Begin and says why; it is never hidden", () => {
    const v = learnView(idle(), { locked: true });
    eq([v.screen, v.canStart, v.startNote], ["start", false, "Parked only: stop before starting"]);
  }],
  ["a refusal shows its reason in the bad tone", () =>
    eq(learnView(idle({ status: "refused", reason: "The car is moving (38 km/h)" })).notice,
      { tone: "bad", text: "The car is moving (38 km/h)" })],
  ["an abort says nothing was written to the car", () =>
    eq(learnView(idle({ status: "aborted" })).notice.text, "Session stopped. Nothing was written to the car.")],
  ["a runner that went quiet is stalled, in the warn tone", () =>
    eq(learnView(idle({ status: "stalled", reason: "The session stopped responding" })).notice.tone, "warn")],

  ["the ready phase: the title is the headline and Start is enabled when parked", () => {
    const v = learnView(running("ready"));
    const g = v.guide;
    eq([v.screen, g.phase, g.say, g.primary, g.primaryOn, g.skip, g.lampText],
      ["guide", "ready", "Brake pedal", "Start this step", true, true, "Not listening yet"]);
    eq(v.steps.map((s) => s.lamp), ["done", "now", "pending"]);
  }],
  ["an engine step needs the parking-brake tap before Start lights", () => {
    const st = running("ready", { id: "engine", engine: true, n: 3, title: "Start the engine, idle 20 s" }, { idx: 2 });
    const a = learnView(st).guide;
    eq([a.primaryOn, a.note], [false, "Confirm the parking brake first"]);
    st.current.confirmed = true;
    eq(learnView(st).guide.primaryOn, true);
  }],
  ["moving greys Start this step, with the reason", () => {
    const g = learnView(running("ready"), { locked: true }).guide;
    eq([g.primaryOn, g.note], [false, "Parked only: stop before continuing"]);
  }],
  ["the countdown shows whole seconds, and runs between polls", () => {
    const st = running("countdown", { countdown: 4.6 });
    eq(learnView(st).guide.big, "5");
    eq(learnView(st, { elapsed: 2 }).guide.big, "3");
  }],
  ["listening: the cue is the headline, with Now for an action and Hold still for rest", () => {
    const act = learnView(running("listening", { seg: 1, say: "Press and HOLD the brake", kind: "act", remaining: 3.2 })).guide;
    eq([act.say, act.big, act.bigUnit, act.lamp, act.kind], ["Press and HOLD the brake", "4", "Now", "on", "act"]);
    const rest = learnView(running("listening", { seg: 0, say: "Release the brake", kind: "rest", remaining: 2 })).guide;
    eq([rest.bigUnit, rest.kind], ["Hold still", "rest"]);
  }],
  ["the listening lamp counts frames", () =>
    eq(learnView(running("listening", { seg: 0, say: "x", kind: "rest", remaining: 2, frames: 4210 })).guide.lampText,
      "Listening · 4,210 frames")],
  ["the timeline marks the current and the past segments", () => {
    const g = learnView(running("listening", { seg: 1, say: "x", kind: "act", remaining: 2 })).guide;
    eq(g.segments.map((s) => [s.cur, s.past]), [[false, true], [true, false]]);
    eq(g.segProgress > 0.5 && g.segProgress < 0.7, true);
  }],
  ["review: Done — next, Do it again, and what it heard", () => {
    const st = running("review", {}, { steps: steps([{}, { state: "done", frames: 4210, windows: 7 }]) });
    const g = learnView(st).guide;
    eq([g.say, g.primary, g.retry, g.lampText], ["Got it", "Done — next", true, "Heard 4,210 frames in 7 windows"]);
  }],
  ["a failed step says it did not take, with the reason", () => {
    const st = running("review", {}, { steps: steps([{}, { state: "failed", why: "The bus was silent" }]) });
    const g = learnView(st).guide;
    eq([g.say, g.lampText], ["That one didn't take", "The bus was silent"]);
  }],
  ["the last step's button says it leads to results", () => {
    const st = running("review", {}, { idx: 2, steps: steps([{}, {}, { state: "done", frames: 9, windows: 2 }]) });
    eq(learnView(st).guide.primary, "Done — see results");
  }],

  ["finished with results shows them, labelled Simulated", () => {
    const res = { captures: 2, labels: { brake: { label: "brake", title: "Brake pedal", kicker: "Brake switch", windows: 3, candidates: [Object.assign(cand(), {})] } } };
    const v = learnView(idle({ status: "finished", source: "sim", results_sim: res }));
    eq([v.screen, v.source.text, v.results.blocks.length], ["results", "Simulated", 1]);
  }],
  ["a car's results are not shown under a simulator's source", () => {
    const res = { captures: 2, labels: { brake: { label: "brake", title: "t", kicker: "k", windows: 3, candidates: [cand()] } } };
    const v = learnView(idle({ status: "finished", source: "car", results: res, results_sim: null }));
    eq([v.source.text, v.results.blocks.length], ["Listening to the car", 1]);
    eq(learnView(idle({ results: res }), { prefs: { sim: true }, viewing: true }).screen, "start");
  }],
  ["asking to see old results opens them from the start screen", () => {
    const res = { captures: 1, labels: { brake: { label: "brake", title: "t", kicker: "k", windows: 3, candidates: [cand()] } } };
    const st = idle({ results: res });
    eq([learnView(st).screen, learnView(st).hasResults, learnView(st, { viewing: true }).screen], ["start", true, "results"]);
  }],
];
