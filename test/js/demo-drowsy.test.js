// The demo's drowsy moment (demo/js/drowsy.js): the chip in the top bar, the
// Level 1 and Level 2 alerts when the scene turns 'drowsy', and the hard-braking
// mark. The demo page does not load the live engine (js/alertness.js), so this
// controller is the only drowsy logic on it.
//
// EVERYTHING OUTSIDE IT IS A FAKE: the clock is a list of timers this file moves,
// the alert player and the voice are recorders, the mark is a counter and the
// page's elements are scratch ones. No test here can make a sound or reach a
// server.
import { eq, ok } from "./assert.js";
import {
  createDemoDrowsy, makeSay, register, chipAfterBar,
  L1_CUES, L2_CUES, L1_REASON, L2_AT, END_AT, SAY_AFTER, SAY_ID, BRAKE_TOAST, POLL_STALE_SECS,
  CHIP_WATCHING, CHIP_PARKED,
} from "../demo/js/drowsy.js";
import { createDrowsy } from "../js/drowsyrun.js";
import { TRIGGER, TONE } from "../js/drowsyui.js";

const kind = (c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind);
const tick = () => new Promise((done) => setTimeout(done, 0));

// ---- the rig ---------------------------------------------------------------
function rig(over = {}) {
  const r = { now: 0, nextId: 1, timers: [], played: [], said: [], marks: [], toasts: [],
              sayFails: false, markFails: false };
  const later = (fn, ms) => { const id = r.nextId++; r.timers.push({ id, at: r.now + ms, fn }); return id; };
  const cancel = (id) => { r.timers = r.timers.filter((t) => t.id !== id); };
  // Move the clock, running every timer that falls due on the way, in order.
  r.advance = (secs) => {
    const end = r.now + secs * 1000;
    for (;;) {
      const due = r.timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      r.timers = r.timers.filter((t) => t !== due);
      r.now = due.at;
      due.fn();
    }
    r.now = end;
  };
  r.secs = () => r.now / 1000;
  r.player = { play(cues, out) { r.played.push([r.secs(), cues.map(kind), out.level]); return Promise.resolve(); } };
  r.app = document.createElement("div");
  r.bar = document.createElement("header");
  r.right = document.createElement("div");
  r.right.className = "tb-right";
  r.bar.append(r.right);
  r.host = document.createElement("div");
  r.host.hidden = true;
  r.ctl = createDemoDrowsy({
    later, cancel, player: () => r.player,
    say: (id) => { r.said.push([r.secs(), id]); return r.sayFails ? Promise.reject(new Error("no voice")) : Promise.resolve(); },
    mark: () => { r.marks.push(r.secs()); return r.markFails ? Promise.reject(new Error("403")) : Promise.resolve({}); },
    toast: (m, tone) => r.toasts.push([m, tone || ""]),
    ...over,
  });
  r.ui = r.ctl.mount({ app: r.app, bar: r.bar, host: r.host });
  const q = (sel) => { const e = r.app.querySelector(sel); return e ? e.textContent : null; };
  r.chip = () => [r.ui.chip.querySelector(".tb-drowsy-t").textContent, r.ui.chip.dataset.tone];
  r.card = () => {
    const layer = r.app.querySelector(".dz-layer");
    return { level: layer.dataset.level, shown: !layer.hidden, title: q(".dz-t"), why: q(".dz-s"),
             kind: layer.firstChild ? layer.firstChild.className : null,
             awake: q(".dz-awake") };
  };
  r.drive = () => r.ctl.feed({ scene: "drive" });
  r.drowsy = () => r.ctl.feed({ scene: "drowsy" });
  r.tap = () => r.app.querySelector(".dz-awake").click();
  return r;
}


// The page as register() sees it: a bar with its right side, a store whose
// events the test fires, an /api/live the test answers and a poll timer it
// fires by hand.
function page(over = {}) {
  const p = { now: 100, asks: 0, sample: { demo: { scene: "drive", event: null } }, polls: [], listeners: {},
              marks: 0, toasts: [] };
  p.app = document.createElement("div");
  p.bar = document.createElement("header");
  const right = document.createElement("div");
  right.className = "tb-right";
  p.bar.append(right);
  p.host = document.createElement("div");
  p.store = { live: null, on(what, fn) { p.listeners[what] = fn; return () => {}; } };
  p.chip = () => { const t = p.bar.querySelector(".tb-drowsy-t"); return t && t.textContent; };
  p.ctl = register({}, {
    store: p.store, api: over.api || { live: async () => { p.asks++; return p.sample; } },
    every: (fn) => { p.polls.push(fn); return 1; }, clock: () => p.now,
    els: { app: p.app, bar: p.bar, host: p.host, dz: null },
    later: () => 0, cancel: () => {}, player: () => ({ play: () => Promise.resolve() }), say: async () => {},
    mark: async () => { p.marks++; return {}; }, toast: (...a) => p.toasts.push(a),
  });
  return p;
}

const NONE = { level: "0", shown: false, title: null, why: null, kind: null, awake: null };

export default [
  // ---- the two sequences are the live engine's own
  ["the Level 1 and Level 2 cues are exactly what the live engine's 'Test the alerts' plays", async () => {
    const cfg = await (await fetch("../data/drowsy.json")).json();
    const timers = [];
    const played = [];
    let cur = 0;
    const eng = createDrowsy({
      clock: () => 0, wall: () => 0, hour: () => 14,
      getJSON: async (p) => (p === "/api/live" ? { connected: true, values: { SPEED: 0 } }
        : p === "/api/cams" ? { running: true, roles: { cabin: { live: true } } } : JSON.parse(JSON.stringify(cfg))),
      postJSON: async () => ({}),
      player: () => ({ play() { return Promise.resolve(); }, setConfig() {}, setName() {} }),
      voiceInstalled: async () => false,
      onAudio: (fn) => { fn(null); return () => {}; },
      tracker: { start: async () => ({ stop() {} }), drop() {} },
      later: (fn, ms) => { const id = timers.length + 1; timers.push({ id, at: ms, fn }); return id; },
      cancel: (id) => { const k = timers.findIndex((t) => t.id === id); if (k >= 0) timers.splice(k, 1); },
      every: () => 0,
      canvas: null,
    });
    eng.onCues = (cues, out) => played.push({ at: cur, cues, level: out.level });
    await eng.pollLive();
    ok(eng.test(), "the test starts while stopped");
    for (const t of timers.slice().sort((a, b) => a.at - b.at)) { cur = t.at / 1000; t.fn(); }
    const at = (level) => played.find((p) => p.level === level && p.cues.some((c) => c.kind !== "fade"));
    eq([at(1).at, at(1).cues], [0, L1_CUES], "Level 1 at 0 s");
    eq([at(2).at, at(2).cues], [11, L2_CUES], "Level 2 at 11 s");
  }],

  // ---- the chip
  ["its two chip texts are drowsyui's own, with its tones", () => {
    eq([CHIP_WATCHING in TONE, CHIP_PARKED in TONE, TONE[CHIP_WATCHING], TONE[CHIP_PARKED]], [true, true, "ok", ""]);
    eq([CHIP_WATCHING, CHIP_PARKED], ["Watching", "Paused · stopped"]);
  }],
  ["the chip says Watching (ok) while driving, and Paused · stopped when parked", () => {
    const r = rig();
    r.drive();
    eq(r.chip(), ["Watching", "ok"]);
    r.ctl.feed({ scene: "parked" });
    eq(r.chip(), ["Paused · stopped", ""]);
    r.drive();
    eq(r.chip(), ["Watching", "ok"]);
    eq(r.ui.chip.parentNode === r.right, true, "the chip is in the top bar's right side");
  }],
  ["a sample with no demo in it, or none at all, changes nothing", () => {
    const r = rig();
    r.drive();
    for (const s of [null, undefined, {}, "x", 7]) r.ctl.feed(s);
    eq(r.chip(), ["Watching", "ok"]);
    eq(r.card(), NONE);
  }],
  ["the chip's title says what drowsy mode is doing, in drowsyui's own words", () => {
    const r = rig();
    r.drive();
    ok(/Watching/.test(r.ui.chip.title), r.ui.chip.title);
    r.ctl.feed({ scene: "parked" });
    ok(/Paused · stopped/.test(r.ui.chip.title) && /once the car moves/.test(r.ui.chip.title), r.ui.chip.title);
  }],
  ["tapping the chip opens nothing: the live settings sheet is not the demo's to show", () => {
    const r = rig();
    r.drive();
    r.ui.chip.click();
    eq([r.host.hidden, r.host.childNodes.length], [true, 0]);
  }],

  // ---- the drowsy moment
  ["drive to drowsy: Level 1 at once, Level 2 at 9 s, and the voice line after it", () => {
    const r = rig();
    r.drive();
    r.advance(5);
    eq(r.played, [], "nothing plays while driving");
    r.drowsy();
    eq(r.played, [[5, L1_CUES.map(kind), 1]], "Level 1's chime and voice, the moment the scene turns");
    const c1 = r.card();
    eq([c1.level, c1.shown, c1.kind, c1.title, c1.why, c1.awake],
      ["1", true, "dz-card", "You seem tired. Plan a break soon.", "Eyes closing more often", "I'm awake"]);
    r.advance(8.9);
    eq(r.played.length, 1, "still only Level 1 just before 9 s");
    r.advance(0.1);
    eq(r.played[1], [14, L2_CUES.map(kind), 2], "Level 2's duck and bark at 9 s");
    const c2 = r.card();
    eq([c2.level, c2.kind, c2.title, c2.awake], ["2", "dz-full", "Are you with me?", "I'm awake"]);
    eq(r.said, [], "the line waits for the bark to sound");
    r.advance(SAY_AFTER);
    eq(r.said, [[5 + L2_AT + SAY_AFTER, "drowsy-l2"]], "then the voice says the Level 2 line");
  }],
  ["the timings are the brief's: Level 2 at 9 s, the end at 30 s, the line 'drowsy-l2'", () => {
    eq([L2_AT, END_AT, SAY_ID], [9, 30, "drowsy-l2"]);
    eq(L1_REASON, "Eyes closing more often");
  }],
  ["the reason line is the demo's, and the live app's own reasons are left as they were", () => {
    rig();
    eq(TRIGGER.perclos, "Your eyes have been closing more and more");
    eq(TRIGGER.closed, "Your eyes closed");
  }],
  ["the chip warns while an alert is up, and is Watching (ok) again when it ends", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    eq(r.chip(), ["Watching", "warn"]);
    r.advance(9);
    eq(r.chip(), ["Watching", "warn"]);
    r.tap();
    eq(r.chip(), ["Watching", "ok"]);
  }],
  ["'I'm awake' stops the sounds, ends the alert and restores the chip, at Level 1", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(4);
    r.tap();
    eq(r.played[r.played.length - 1], [4, ["fade"], 0], "the player is told to fade: it does that within 3 s");
    eq(r.card(), NONE);
    eq(r.chip(), ["Watching", "ok"]);
    r.advance(40);
    eq(r.played.length, 2, "and nothing else ever plays: Level 2 was cancelled");
    eq(r.said, [], "and the line was never said");
  }],
  ["'I'm awake' at Level 2 stops it, and a line still to come is never said", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(L2_AT + 1);
    eq(r.card().level, "2");
    r.tap();
    eq(r.played[r.played.length - 1], [L2_AT + 1, ["fade"], 0]);
    eq(r.card(), NONE);
    r.advance(40);
    eq([r.said, r.played.length], [[], 3]);
    eq(r.timers, [], "no timer is left running");
  }],
  ["with no tap it ends by itself at 30 s", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(29.9);
    eq(r.card().level, "2", "still up at 29.9 s");
    r.advance(0.1);
    eq(r.card(), NONE);
    eq(r.played[r.played.length - 1], [30, ["fade"], 0]);
    eq(r.chip(), ["Watching", "ok"]);
    eq(r.said.length, 1, "the line was said once, on the way");
    eq(r.timers, [], "no timer is left running");
  }],
  ["a scene that stays 'drowsy' after the alert ended does not start another; a new one does", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(3);
    r.tap();
    for (let i = 0; i < 6; i++) { r.advance(1); r.drowsy(); }
    eq(r.card(), NONE);
    eq(r.played.length, 2, "only the first alert's Level 1 and the fade");
    r.drive();
    r.advance(1);
    r.drowsy();
    eq([r.card().level, r.played.length], ["1", 3], "the next time the scene turns, it plays again");
  }],
  ["seen again on every paint, the scene starts one alert, not one a paint", () => {
    const r = rig();
    r.drive();
    for (let i = 0; i < 5; i++) { r.drowsy(); r.advance(0.25); }
    eq(r.played.length, 1);
  }],
  ["a page that opens in the middle of a drowsy scene does not sound an alert", () => {
    const r = rig();
    r.drowsy();
    eq([r.played, r.card()], [[], NONE]);
  }],
  ["the car parking during an alert ends it, as a stopped car ends the live one", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(2);
    r.ctl.feed({ scene: "parked" });
    eq(r.card(), NONE);
    eq(r.played[r.played.length - 1], [2, ["fade"], 0]);
    eq(r.chip(), ["Paused · stopped", ""]);
  }],
  ["a voice that fails to play changes nothing else", async () => {
    const r = rig();
    r.sayFails = true;
    r.drive();
    r.drowsy();
    r.advance(L2_AT + SAY_AFTER);
    await tick();
    eq([r.said.length, r.card().level], [1, "2"]);
    r.tap();
    eq(r.card(), NONE);
  }],
  ["a player that throws does not stop the card", () => {
    const r = rig({ player: () => ({ play() { throw new Error("no audio context"); } }) });
    r.drive();
    r.drowsy();
    eq(r.card().level, "1");
    r.advance(9);
    eq(r.card().level, "2");
    r.tap();
    eq(r.card(), NONE);
  }],

  // ---- hard braking
  ["a hard_brake event marks once, however many paints see it", async () => {
    const r = rig();
    r.drive();
    r.ctl.feed({ scene: "drive", event: null });
    const ev = { kind: "hard_brake", at: 1759280000.5 };
    for (let i = 0; i < 5; i++) { r.ctl.feed({ scene: "drive", event: ev }); r.advance(0.25); }
    await tick();
    eq(r.marks.length, 1, "one POST to /api/cams/mark");
    eq(r.toasts, [["Hard braking. The clip is saved.", ""]], "one toast");
    eq(BRAKE_TOAST, "Hard braking. The clip is saved.");
  }],
  ["and a second event, at another time, marks again", async () => {
    const r = rig();
    r.drive();
    for (const at of [100, 100, 100, 400, 400]) r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at } });
    await tick();
    eq(r.marks.length, 2);
    eq(r.toasts.length, 2);
  }],
  ["other events, and none, mark nothing", async () => {
    const r = rig();
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "pothole", at: 5 } });
    r.ctl.feed({ scene: "drive", event: null });
    await tick();
    eq([r.marks.length, r.toasts.length], [0, 0]);
  }],
  ["an event already in the first sample the page sees is not marked again", async () => {
    const r = rig();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 77 } });
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 77 } });
    await tick();
    eq(r.marks.length, 0);
  }],
  ["a mark that fails says so, and does not claim the clip is saved", async () => {
    const r = rig();
    r.markFails = true;
    r.drive();
    for (let i = 0; i < 3; i++) r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 9 } });
    await tick();
    eq(r.marks.length, 1, "and it is not retried on every paint");
    eq(r.toasts.length, 1);
    ok(/could not be saved/.test(r.toasts[0][0]) && r.toasts[0][1] === "bad", r.toasts[0].join("|"));
  }],
  ["a hard brake in the middle of a drowsy alert marks it too", async () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.ctl.feed({ scene: "drowsy", event: { kind: "hard_brake", at: 3 } });
    await tick();
    eq([r.marks.length, r.card().level], [1, "1"]);
  }],

  // ---- the say that may not be there
  ["the voice line is imported only when needed, and its absence is no error", async () => {
    let asked = 0;
    const gone = makeSay(async () => { asked++; throw new Error("404"); });
    eq(await gone("drowsy-l2"), undefined);
    eq(await gone("drowsy-l2"), undefined);
    const none = makeSay(async () => ({}));
    eq(await none("drowsy-l2"), undefined, "a module with no say");
    const said = [];
    const here = makeSay(async () => ({ say: (id) => { said.push(id); return Promise.resolve("done"); } }));
    eq([await here("drowsy-l2"), said], ["done", ["drowsy-l2"]]);
    ok(asked >= 1, "the loader was asked");
  }],

  // ---- the page: afterBar, and where the sample comes from
  ["register wraps afterBar, keeps the one it found, and puts the chip in the bar once", async () => {
    const r = rig();
    const calls = [];
    const D = { afterBar: (v) => calls.push(v) };
    const store = { live: null, on() { return () => {}; } };
    const app = document.createElement("div");
    const bar = document.createElement("header");
    const host = document.createElement("div");
    const ctl = register(D, { store, api: { live: async () => ({}) }, every: () => 0, clock: () => 0,
      els: { app, bar, host }, later: () => 0, cancel: () => {}, player: () => r.player, say: async () => {},
      mark: async () => ({}), toast: () => {} });
    await tick();
    ok(ctl && typeof D.afterBar === "function", "afterBar is set");
    D.afterBar(bar);
    eq([calls.length, calls[0] === bar], [1, true], "the one it found is still called");
    eq(bar.querySelectorAll(".tb-drowsy").length, 0, "and with no right side to the bar yet, nothing is added");
    const right = document.createElement("div");
    right.className = "tb-right";
    right.append(document.createElement("button"));
    bar.append(right);
    D.afterBar(bar);
    D.afterBar(bar);
    eq(bar.querySelectorAll(".tb-drowsy").length, 1, "the chip goes in once, however many paints");
    eq(right.firstChild.className, "tb-drowsy", "at the front of the right side");
    const fresh = document.createElement("div");
    fresh.className = "tb-right";
    right.replaceWith(fresh);
    D.afterBar(bar);
    eq(fresh.querySelectorAll(".tb-drowsy").length, 1, "a bar that is rebuilt gets it back");
    right.replaceWith();
    chipAfterBar(bar);
    eq(bar.querySelectorAll(".tb-drowsy").length, 1, "chipAfterBar is the same thing, for a caller composing its own");
  }],
  ["it waits for the app to boot, then starts", async () => {
    const r = rig();
    const app = document.createElement("div");
    app.dataset.booting = "1";
    const bar = document.createElement("header");
    const host = document.createElement("div");
    register({}, { store: { live: null, on() { return () => {}; } }, api: { live: async () => ({}) },
      every: () => 0, clock: () => 0, els: { app, bar, host }, later: () => 0, cancel: () => {},
      player: () => r.player, say: async () => {}, mark: async () => ({}), toast: () => {} });
    await tick();
    eq(app.querySelector(".dz-layer"), null, "nothing is drawn while the boot screen is up");
    app.dataset.booting = "0";
    await tick();
    ok(app.querySelector(".dz-layer"), "and the alert layer is there once the app has booted");
  }],
  ["the store's live sample feeds it; on a screen with no fast clock it asks /api/live itself", async () => {
    const p = page();
    await tick();
    eq(p.polls.length, 1, "one poll timer");
    // The store's own clock is running: it feeds, and the poll stays quiet.
    p.store.live = { demo: { scene: "drive", event: null } };
    p.listeners.live();
    p.store.live = { demo: { scene: "parked", event: null } };
    p.listeners.live();
    eq(p.chip(), "Paused · stopped", "the store's sample reached the chip");
    p.now += 0.4;
    p.polls[0]();
    await tick();
    eq(p.asks, 0, "the poll does not ask while the store is fresh");
    // The store goes quiet (the Cameras tab has no fast clock): the poll takes over.
    p.now += POLL_STALE_SECS + 0.1;
    p.sample = { demo: { scene: "drive", event: { kind: "hard_brake", at: 12 } } };
    p.polls[0]();
    await tick();
    eq(p.asks, 1, "it asks once the store has been quiet for a while");
    eq(p.chip(), "Watching", "and what it hears reaches the chip");
    eq([p.marks, p.toasts], [1, [["Hard braking. The clip is saved."]]], "and a hard brake seen there is marked, once");
    p.polls[0]();
    await tick();
    p.polls[0]();
    await tick();
    eq(p.marks, 1, "still once, however often it asks");
  }],
  ["a poll that fails is ignored, and only one is out at a time", async () => {
    let out = 0, peak = 0, fail = true;
    const p = page({
      api: { live: () => { out++; peak = Math.max(peak, out); return new Promise((res, rej) => setTimeout(() => { out--; fail ? rej(new Error("500")) : res({ demo: { scene: "parked" } }); }, 5)); } },
    });
    await tick();
    p.now = 50;
    p.polls[0](); p.polls[0](); p.polls[0]();
    await new Promise((done) => setTimeout(done, 30));
    eq(peak, 1, "one request at a time");
    eq(p.chip(), "Watching", "a failure changes nothing");
    fail = false;
    p.polls[0]();
    await new Promise((done) => setTimeout(done, 30));
    eq([out, p.chip()], [0, "Paused · stopped"], "and the next answer is used");
  }],
];
