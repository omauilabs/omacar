import { eq, ok } from "./assert.js";
import workView, { resetWork } from "../js/views/work.js";
import { store, api } from "../js/core.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const SESSION = { id: "s1", project: "omacar", state: "done", pr: 41, repo: "omauilabs/omacar" };

async function withWork(opts, fn) {
  const was = { workStatus: api.workStatus, workEvents: api.workEvents, workClip: api.workClip };
  api.workStatus = async () => ({ online: true, sessions: opts.sessions || [SESSION],
    ...("asking" in opts ? { asking: opts.asking } : {}) });
  api.workEvents = async () => ({ online: opts.online !== false, events: opts.events || [] });
  api.workClip = async () => ({ wav_b64: "" });
  const clock = { t: 1e12 };
  const wasClock = store.clock;
  store.clock = () => clock.t;
  store.motionLatched = false; store.stillSince = null; store.lastMoving = false;
  store.live = { connected: true, values: { SPEED: opts.speed || 0, RPM: 0 } };
  store.emit("live");
  resetWork();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const unmount = workView(root);
  try {
    for (let i = 0; i < 40 && !root.querySelector(".work-row"); i++) await wait(25);
    await fn(root);
  } finally {
    unmount(); root.remove(); Object.assign(api, was);
    store.clock = wasClock; store.live = null; store.lastMoving = false;
    store.motionLatched = false; store.stillSince = null;
  }
}

export default [
  ["moving: no pull request on screen", () => withWork({ speed: 60 }, (root) =>
    eq(root.querySelector(".work-pr"), null))],
  ["parked: the draft PR, linked", () => withWork({}, (root) => {
    const a = root.querySelector(".work-pr");
    ok(a && a.textContent.includes("41"), "PR 41 shown");
    eq(a.getAttribute("href"), "https://github.com/omauilabs/omacar/pull/41");
    eq([a.getAttribute("target"), a.getAttribute("rel")], ["_blank", "noopener noreferrer"]);
  })],
  ["a pending question is shown large, with its own talk button", () => withWork({
    sessions: [{ ...SESSION, state: "question" }], events: [
    { id: 1, at: 1, kind: "question", session: "s1", line: "omacar has a question: 0.8 s?" }] },
    (root) => {
      eq(root.querySelector(".work-question-text").textContent, "omacar has a question: 0.8 s?");
      ok(root.querySelector(".work-question .work-talk"), "the card repeats the talk button");
    })],
  ["F3: two questions waiting, the relay asking the first: the card shows the first", () => withWork({
    sessions: [{ ...SESSION, state: "question" }, { ...SESSION, id: "s2", project: "omalens", state: "question" }],
    asking: "s1", events: [
      { id: 1, at: 1, kind: "question", session: "s1", line: "omacar has a question: A?" },
      { id: 2, at: 2, kind: "question", session: "s2", line: "omalens has a question: B?" }] },
    (root) => eq(root.querySelector(".work-question-text").textContent, "omacar has a question: A?"))],
  ["F3: the relay asking the second: the card shows the second", () => withWork({
    sessions: [{ ...SESSION, state: "question" }, { ...SESSION, id: "s2", project: "omalens", state: "question" }],
    asking: "s2", events: [
      { id: 1, at: 1, kind: "question", session: "s1", line: "omacar has a question: A?" },
      { id: 2, at: 2, kind: "question", session: "s2", line: "omalens has a question: B?" }] },
    (root) => eq(root.querySelector(".work-question-text").textContent, "omalens has a question: B?"))],
  ["the talk button is thumb-sized", () => withWork({}, (root) => {
    const r = root.querySelector(".work-talk").getBoundingClientRect();
    ok(r.width >= 160 && r.height >= 160, `talk button ${r.width}x${r.height}`);
  })],
];
