// The demo's drowsy moment (demo/js/drowsy.js): the chip in the top bar, the
// Level 1 and Level 2 alerts when the scene turns 'drowsy', and the hard-braking
// toast (the mark itself is the camera feed's: lib/cams.py). The demo page does not load the live engine (js/alertness.js), so this
// controller is the only drowsy logic on it.
//
// EVERYTHING OUTSIDE IT IS A FAKE: the clock is a list of timers this file moves,
// the alert player and the voice are recorders (and the last section runs the
// real voice.js against a music bus that is only a list of points) and the
// page's elements are scratch ones. No test here can make a sound or reach a
// server.
import { eq, ok } from "./assert.js";
import {
  createDemoDrowsy, makeVoice, makeSay, register, chipAfterBar,
  L1_CUES, L2_CUES, L1_REASON, L2_AT, END_AT, SAY_AFTER, SAY_ID, BRAKE_TOAST, POLL_STALE_SECS,
  CHIP_WATCHING, CHIP_PARKED, BRAKE_TOAST_NO_CLIP, demoEngine,
} from "../demo/js/drowsy.js";
import { createDrowsy } from "../js/drowsyrun.js";
import { TRIGGER, TONE } from "../js/drowsyui.js";
import { createAlertPlayer, DUCK_DB as ALERT_DUCK_DB } from "../js/alertplayer.js";
import { MUSIC_DB } from "../js/audiobus.js";
import { planOnto, dbAt, worstStep } from "../js/ramps.js";
import { say as realSay, stop as realStop, voiceIO } from "../demo/js/voice.js";

const kind = (c) => (c.clip ? `${c.kind}:${c.clip}` : c.kind);
const tick = () => new Promise((done) => setTimeout(done, 0));

// ---- the rig ---------------------------------------------------------------
function rig(over = {}) {
  const r = { now: 0, nextId: 1, timers: [], played: [], said: [], stops: [], order: [], toasts: [],
              sayFails: false };
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
  r.player = { play(cues, out) {
    r.played.push([r.secs(), cues.map(kind), out.level]);
    r.order.push(cues.map(kind).join("+"));
    return Promise.resolve();
  } };
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
    stopVoice: () => { r.stops.push(r.secs()); r.order.push("stop"); },
    toast: (m, tone) => r.toasts.push([m, tone || ""]),
    saved: () => true,                 // a camera is recording, unless a test says otherwise
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
              toasts: [] };
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
    stopVoice: () => {}, toast: (...a) => p.toasts.push(a), saved: () => true,
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

  // ---- hard braking: the toast is the page's; the mark is the camera feed's
  ["a hard_brake event toasts once, however many paints see it, and sends nothing to the server", async () => {
    const r = rig();
    const realFetch = globalThis.fetch;
    const calls = [];
    globalThis.fetch = (...a) => { calls.push(a[0]); return realFetch(...a); };
    try {
      r.drive();
      r.ctl.feed({ scene: "drive", event: null });
      const ev = { kind: "hard_brake", at: 1759280000.5 };
      for (let i = 0; i < 5; i++) { r.ctl.feed({ scene: "drive", event: ev }); r.advance(0.25); }
      await tick();
    } finally { globalThis.fetch = realFetch; }
    eq(r.toasts, [["Hard braking. The clip is saved.", ""]], "one toast");
    eq(calls, [], "and no request: lib/cams.py marks it, as the live recorder does");
    eq(BRAKE_TOAST, "Hard braking. The clip is saved.");
  }],
  // ---- ... and "The clip is saved" only while a camera is recording (hardening D) ----------
  ["with no camera recording, a hard brake toasts 'Hard braking.' and no more: there is no clip to say is saved", () => {
    const r = rig({ saved: () => false });
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 9 } });
    eq(r.toasts, [["Hard braking.", ""]], "no mention of a clip");
    eq([BRAKE_TOAST_NO_CLIP, BRAKE_TOAST], ["Hard braking.", "Hard braking. The clip is saved."], "the two texts");
  }],
  ["with a camera recording it keeps 'The clip is saved'", () => {
    const r = rig({ saved: () => true });
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 9 } });
    eq(r.toasts, [[BRAKE_TOAST, ""]], "the full toast");
  }],
  ["the text is chosen as the toast is made: footage that arrives between two brakes changes the second", () => {
    let recording = false;
    const r = rig({ saved: () => recording });
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 1 } });
    recording = true;
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 2 } });
    recording = false;
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 3 } });
    eq(r.toasts.map((t) => t[0]), ["Hard braking.", BRAKE_TOAST, "Hard braking."], "one each, as it stood");
  }],
  ["a question that does not answer true makes no claim: a throw, nothing, or something else", () => {
    for (const saved of [() => { throw new Error("boom"); }, () => undefined, () => null, () => "yes"]) {
      const r = rig({ saved });
      r.drive();
      r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 4 } });
      eq(r.toasts, [["Hard braking.", ""]], "no clip claimed");
    }
  }],
  ["the page's own question is the footage detector's: unknown (not asked yet) is no claim either", () => {
    const toasts = [];
    const ctl = createDemoDrowsy({ later: () => 0, cancel: () => {}, player: () => ({ play: () => Promise.resolve() }),
                                   say: async () => {}, stopVoice: () => {}, toast: (m) => toasts.push(m) });
    ctl.feed({ scene: "drive" });
    ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 5 } });
    eq(toasts, ["Hard braking."], "the page has not looked at the cameras in this test: nothing is saved");
    ctl.destroy();
  }],
  ["and a second event, at another time, toasts again", () => {
    const r = rig();
    r.drive();
    for (const at of [100, 100, 100, 400, 400]) r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at } });
    eq(r.toasts.length, 2);
  }],
  ["other events, and none, toast nothing", () => {
    const r = rig();
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "pothole", at: 5 } });
    r.ctl.feed({ scene: "drive", event: null });
    eq(r.toasts.length, 0);
  }],
  ["an event already in the first sample the page sees is not announced again", () => {
    const r = rig();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 77 } });
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 77 } });
    eq(r.toasts.length, 0);
  }],
  ["a hard brake in the middle of a drowsy alert toasts too, and the alert goes on", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.ctl.feed({ scene: "drowsy", event: { kind: "hard_brake", at: 3 } });
    eq([r.toasts.length, r.card().level], [1, "1"]);
  }],

  // ---- the drowsy moment with no footage (hardening D) -----------------------------------------
  //
  // The cameras wait for parts, and the demo is shown with no clip: the camera feed
  // (lib/cams.py demo) does not run, so the cabin that swaps to the drowsy clip when
  // the scene turns is not there to swap. The moment never needed it. The chip, both
  // levels and "I'm awake" are this controller's and drowsyui's, and not one of them
  // draws a picture or asks for one: nothing for a broken cabin tile to be.
  ["with no camera feed the whole moment runs: Watching, Level 1, Level 2 and I'm awake, asking the cameras for nothing", () => {
    const asked = [];
    const real = globalThis.fetch;
    globalThis.fetch = (u) => { asked.push(String((u && u.url) || u)); return Promise.reject(new Error("no server")); };
    try {
      const r = rig();
      const pictures = () => r.app.querySelectorAll("img, video, canvas, iframe, object, picture").length
                           + r.bar.querySelectorAll("img, video, canvas, iframe, object, picture").length
                           + r.host.querySelectorAll("img, video, canvas, iframe, object, picture").length;
      r.drive();
      eq([r.chip(), pictures()], [["Watching", "ok"], 0], "the chip, no picture");
      r.drowsy();
      eq([r.card().level, r.card().title, pictures()], ["1", "You seem tired. Plan a break soon.", 0], "Level 1");
      r.advance(L2_AT);
      eq([r.card().level, r.card().title, r.card().awake, pictures()], ["2", "Are you with me?", "I'm awake", 0], "Level 2");
      r.advance(SAY_AFTER);
      eq(r.said.length, 1, "its voice line");
      r.tap();
      eq([r.card().shown, r.chip(), pictures()], [false, ["Watching", "ok"], 0], "I'm awake: over, and the chip is back");
      r.drowsy();
      r.advance(END_AT);
      eq([r.card().shown, r.chip()], [false, ["Watching", "ok"]], "and one nobody taps ends by itself");
    } finally { globalThis.fetch = real; }
    eq(asked, [], "no request at all: nothing for the cameras, no cabin stream, no /api/cams");
  }],
  ["and a hard brake toasts as ever with no camera feed: the toast is the page's, the mark is the feed's", () => {
    const r = rig();
    r.drive();
    r.ctl.feed({ scene: "drive", event: { kind: "hard_brake", at: 7 } });
    eq(r.toasts, [[BRAKE_TOAST, ""]], "one toast");
  }],

  // ---- the say that may not be there
  ["the voice is imported only when needed, and its absence is no error", async () => {
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
  ["stop() reaches voice.js's stop once it is loaded, and is nothing before, or without one", async () => {
    const calls = [];
    const v = makeVoice(async () => ({ say: async () => {}, stop: () => calls.push("stop") }));
    v.stop();
    eq(calls, [], "nothing is loaded, so nothing can be speaking");
    await v.say("drowsy-l2");
    v.stop();
    eq(calls, ["stop"]);
    const bare = makeVoice(async () => ({ say: async () => {} }));
    await bare.say("x");
    bare.stop();
    const gone = makeVoice(async () => { throw new Error("404"); });
    await gone.say("x");
    gone.stop();
  }],
  ["a line still being fetched when 'I'm awake' comes is never said", async () => {
    let release;
    const said = [];
    const v = makeVoice(() => new Promise((res) => { release = () => res({ say: (id) => said.push(id), stop() {} }); }));
    const pending = v.say("drowsy-l2");
    await tick();
    v.stop();
    release();
    await pending;
    eq(said, []);
    await v.say("drowsy-l2");
    eq(said, ["drowsy-l2"], "and the next one, after it, is");
  }],
  ["'I'm awake' stops the voice first, then fades the player", () => {
    const r = rig();
    r.drive();
    r.drowsy();
    r.advance(4);
    r.tap();
    eq(r.order, ["chime+voice:l1", "stop", "fade"]);
    r.drowsy();
    r.drive();
    r.drowsy();
    r.advance(30);
    eq(r.stops.length, 2, "and the 30 s end does the same");
    eq(r.order.slice(-2), ["stop", "fade"]);
  }],
  ["a voice that throws on stop changes nothing else", () => {
    const r = rig({ stopVoice: () => { throw new Error("gone"); } });
    r.drive();
    r.drowsy();
    r.tap();
    eq([r.card(), r.played[r.played.length - 1]], [NONE, [0, ["fade"], 0]]);
  }],

  // ---- the page: afterBar, and where the sample comes from
  ["demoEngine() is the controller register() made: the live Dashcams card reads its chip from it", () => {
    const p = page();
    ok(demoEngine() === p.ctl, "the page's controller");
    eq(typeof demoEngine().on, "function", "with the live engine's face");
  }],
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
      stopVoice: () => {}, toast: () => {} });
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
      player: () => r.player, say: async () => {}, stopVoice: () => {}, toast: () => {} });
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
    eq(p.toasts, [["Hard braking. The clip is saved."]], "and a hard brake seen there is announced, once");
    p.polls[0]();
    await tick();
    p.polls[0]();
    await tick();
    eq(p.toasts.length, 1, "still once, however often it asks");
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

  // ---- the voice and the alert player on one music bus ---------------------
  //
  // Level 2 ducks the music to MUSIC_DB + DUCK_DB (-24) and holds it there.
  // voice.js's say() used to duck 12 dB more (-36) and, when the line ended or
  // was cut, glide back to the -24 it had captured: after "I'm awake" the music
  // was left at -24, and the glide cancelled the player's own fade with a step.
  // Here the real say() and the real alert player share one bus that is a list
  // of points, kept as audiobus.js keeps it, and one clock.
  ["Level 2, then its voice line, then 'I'm awake': the music ends at MUSIC_DB, in steps of no more than 3 dB per 100 ms", async () => {
    const s = studio();
    await prime();
    let r;
    r = rig({ player: () => s.player, say: realSay, stopVoice: realStop });
    s.secs = () => r.secs();
    const restore = s.install(() => r.secs());
    try {
      r.drive();
      r.drowsy();
      r.advance(L2_AT);
      ok(Math.abs(dbAt(s.music, r.secs() + 2) - (MUSIC_DB + ALERT_DUCK_DB)) < 1e-6, "Level 2 ducked the music to -24");
      r.advance(SAY_AFTER);
      await tick();
      const before = s.calls.length;
      ok(Math.abs(dbAt(s.music, r.secs()) - (MUSIC_DB + ALERT_DUCK_DB)) < 1e-6, "still -24 while the line is said");
      r.tap();
      await tick();
      await tick();
      eq(s.calls.slice(before).filter((c) => c.who === "voice"), [], "the voice touched the bus no more once the alert had made room");
      ok(Math.abs(dbAt(s.music, r.secs() + 30) - MUSIC_DB) < 1e-6, `the music ends at MUSIC_DB (${dbAt(s.music, r.secs() + 30)})`);
      const worst = worstStep(s.music);
      ok(worst <= 3 + 1e-6, `no step over 3 dB per 100 ms (worst ${worst.toFixed(2)})`);
      eq(s.calls.filter((c) => c.who === "voice"), [], "and the voice never moved the music at all");
    } finally { restore(); }
  }],
  ["the same, with the line running to its end before 'I'm awake'", async () => {
    const s = studio();
    await prime();
    let r;
    r = rig({ player: () => s.player, say: realSay, stopVoice: realStop });
    const restore = s.install(() => r.secs());
    try {
      r.drive();
      r.drowsy();
      r.advance(L2_AT + SAY_AFTER);
      await tick();
      await settle(1500);
      r.advance(5);
      r.tap();
      await tick();
      ok(Math.abs(dbAt(s.music, r.secs() + 30) - MUSIC_DB) < 1e-6, "ends at MUSIC_DB");
      ok(worstStep(s.music) <= 3 + 1e-6, "in steps of no more than 3 dB per 100 ms");
    } finally { restore(); }
  }],
  ["say() on its own still ducks the music 12 dB and brings it back", async () => {
    const s = studio();
    await prime();
    const restore = s.install(() => (s.t += 1.5));
    try {
      s.t = 20;
      await realSay("drowsy-l2");
      const voice = s.calls.filter((c) => c.who === "voice");
      eq(voice.length, 2, "one duck and one restore");
      eq([voice[0].points.at(-1)[1], voice[1].points.at(-1)[1]], [MUSIC_DB - 12, MUSIC_DB]);
      ok(Math.abs(dbAt(s.music, s.t + 5) - MUSIC_DB) < 1e-6, "and the music is back at MUSIC_DB");
      ok(worstStep(s.music) <= 3 + 1e-6, `in steps of no more than 3 dB per 100 ms (${worstStep(s.music).toFixed(2)})`);
    } finally { restore(); }
  }],
  ["stop() cuts a line say() ducked for, and restores the music smoothly, even from mid-duck", async () => {
    for (const cutAfter of [0.2, 1.0]) {
      const s = studio();
      await prime();
      const restore = s.install(() => s.t);
      try {
        s.t = 20;
        const p = realSay("drowsy-l2");
        await tick();
        s.t = 20 + cutAfter;
        realStop();
        await p;
        await tick();
        ok(Math.abs(dbAt(s.music, s.t + 5) - MUSIC_DB) < 1e-6, `cut after ${cutAfter} s: back at MUSIC_DB (${dbAt(s.music, s.t + 5)})`);
        ok(worstStep(s.music) <= 3 + 1e-6, `cut after ${cutAfter} s: no step over 3 dB per 100 ms (${worstStep(s.music).toFixed(2)})`);
      } finally { restore(); }
    }
  }],
  ["stop() with no line speaking does nothing, and a line asked for before it is never played", async () => {
    const s = studio();
    await prime();
    const restore = s.install(() => 50);
    const realFetch = voiceIO.fetch;
    try {
      realStop();
      eq(s.calls, []);
      let release;
      voiceIO.fetch = () => new Promise((res) => { release = () => res(new Response(silentWav(0.05))); });
      const p = realSay("unloaded-line");
      await tick();
      realStop();
      release();
      await p;
      eq(s.calls, [], "the line asked for before stop() left the music alone");
    } finally { voiceIO.fetch = realFetch; restore(); }
  }],
];

// A music bus and an alert player on it: one list of points, kept as
// audiobus.js keeps its own (planOnto/dbAt), and the log of who moved it.
function studio() {
  const s = { music: [[0, MUSIC_DB]], calls: [], t: 0, secs: () => 0 };
  const put = (who) => (points, at, append = false) => {
    s.calls.push({ who, points, at, append });
    s.music = planOnto(s.music, at, points, append);
    return true;
  };
  s.player = createAlertPlayer({
    now: () => s.secs(), running: () => true, resume: async () => "running", whenRunning: () => Promise.resolve(),
    music: put("player"), musicAt: (t) => dbAt(s.music, t), gate() {}, render() { return { release() {} }; },
    clip: async () => null,
  });
  // voice.js reaches the bus, the clock and the network through voiceIO.
  s.install = (now) => {
    const was = { ...voiceIO };
    voiceIO.schedule = (bus, points, at) => (bus === "music" ? put("voice")(points, at) : true);
    voiceIO.levelAt = (bus, t) => dbAt(s.music, t);
    voiceIO.now = now;
    voiceIO.fetch = async () => new Response(silentWav(0.05));
    return () => Object.assign(voiceIO, was);
  };
  return s;
}

// Decode and cache the line once, on a bus nobody looks at, so the scenarios
// do not wait on a decode the page runs on virtual time does not wait for.
async function prime() {
  const was = { ...voiceIO };
  voiceIO.fetch = async () => new Response(silentWav(0.05));
  voiceIO.schedule = () => true;
  voiceIO.levelAt = () => MUSIC_DB;
  voiceIO.now = () => 5;
  try { await realSay("drowsy-l2"); } finally { Object.assign(voiceIO, was); }
}
const settle = (ms) => new Promise((done) => setTimeout(done, ms));

// A mono 16-bit wav of silence, as Piper writes them.
function silentWav(secs, rate = 22050) {
  const n = Math.round(secs * rate), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  return buf;
}
