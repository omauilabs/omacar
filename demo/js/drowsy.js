// The demo's drowsy moment: the chip in the top bar, the two alerts, and the
// hard-braking toast (doc/design/2026-09-30-meetup-demo.md, section 4).
//
// THE DEMO PAGE DOES NOT LOAD js/alertness.js, the live drowsy engine, so this
// controller is the only drowsy logic on it. It watches the demo world's scene
// (store.live.demo.scene) and, when it turns 'drowsy', plays the live app's own
// alerts through the live app's own alert player and cards:
//
//     0 s   Level 1: the chime and voice cues 'Test the alerts' plays, and
//           drowsyui's Level 1 card, "You seem tired. Plan a break soon."
//     9 s   Level 2: the duck and bark cues it plays, drowsyui's full-screen
//           "Are you with me?", and then the voice line 'drowsy-l2'
//     "I'm awake" (a tap) at any moment: the voice is cut (voice.js stop()),
//           the player fades everything, which it does over 3 s, and the
//           alert is over
//     30 s  the same, if nobody tapped
//
// THE CHIP AND THE CARDS ARE DROWSYUI'S. This object is shaped like the live
// engine (state, on(), tap()) and handed to mountDrowsyUI, so the chip, the
// Level 1 and Level 2 cards and their "I'm awake" buttons are the live app's
// own code, looking as it looks. Only the state is the demo's.
//
// A HARD BRAKE (demo.event.kind == "hard_brake") gets its toast here, once per
// event. The mark that locks the clips and puts "Hard braking" on the Cameras
// timeline is lib/cams.py demo's, off the same live.json, as the live
// recorder marks its own: kind "hard-braking", which POST /api/cams/mark
// cannot write.
//
// WHERE THE SAMPLE COMES FROM. The store's live sample, on a screen with the
// fast clock running. The Cameras tab has none (main.js polls /api/live only
// for a screen that shows a live value, and drops the sample on the way out of
// the others), and that is where the tour's hard brake is, so with the store
// quiet this asks /api/live itself.
//
// register(D) wraps D.afterBar to keep the chip in the top bar, and hands the
// wrapper what it found there. chipAfterBar(vbar) does the same job for a
// caller composing its own afterBar (demo/js/boot.js): it is idempotent.

import { store, api, toast } from "../../js/core.js";
import { mountDrowsyUI, TRIGGER } from "../../js/drowsyui.js";
import { alertPlayer } from "../../js/alertplayer.js";

// What 'Test the alerts' plays at Level 1 and at Level 2 (drowsyrun.js
// engine.test(), t = 0 and t = 11 s), which is not exported there. The test
// file plays the live engine's test and holds these to it.
export const L1_CUES = [{ kind: "chime" }, { kind: "voice", clip: "l1" }];
export const L2_CUES = [{ kind: "duck" }, { kind: "bark" }];

// The demo's own reason line on the Level 1 card. drowsyui draws
// TRIGGER[state.trigger], so this is one more key there, and the live ones are
// not touched.
export const L1_REASON = "Eyes closing more often";
const L1_KEY = "demo-eyes";
const L2_KEY = "closed";                    // "Your eyes closed", the live app's own

export const L2_AT = 9;                     // seconds after Level 1
export const END_AT = 30;                   // seconds after Level 1: it ends by itself
export const SAY_AFTER = 3;                 // the voice line follows Level 2's bark by this
export const SAY_ID = "drowsy-l2";
export const BRAKE_TOAST = "Hard braking. The clip is saved.";
export const CHIP_WATCHING = "Watching";
export const CHIP_PARKED = "Paused · stopped";
// With the store's live sample this stale, ask /api/live ourselves.
export const POLL_STALE_SECS = 1.2;
const POLL_EVERY_MS = 500;

// The voice is Task 6's (demo/js/voice.js), and may not be there: a dynamic
// import, made only when there is a line to say, whose absence is no error. A
// failed import is not remembered. stop() reaches voice.js's stop() once the
// module is loaded (before that nothing can be speaking), and a line still
// waiting on the import when it is called is never said.
export function makeVoice(load) {
  let mod = null, epoch = 0;
  return {
    async say(id) {
      const asked = epoch;
      if (!mod) { try { mod = await Promise.resolve().then(load); } catch { mod = null; } }
      if (asked !== epoch) return undefined;
      return mod && typeof mod.say === "function" ? mod.say(id) : undefined;
    },
    stop() {
      epoch++;
      if (mod && typeof mod.stop === "function") mod.stop();
    },
  };
}
export const makeSay = (load) => makeVoice(load).say;
const voice = makeVoice(() => import("./voice.js"));

let active = null;                          // the controller register() made last

export function createDemoDrowsy(opts = {}) {
  const d = {
    player: () => alertPlayer(),            // the page's one alert player
    say: voice.say,
    stopVoice: voice.stop,
    toast,
    later: (fn, ms) => setTimeout(fn, ms),
    cancel: (id) => clearTimeout(id),
    ...opts,
  };
  TRIGGER[L1_KEY] = L1_REASON;

  const listeners = new Set();
  // The state drowsyui draws from (drowsyrun.js): what the chip says, the
  // level showing and why. `gate` and `cfg` are what its chip title reads.
  const st = {
    chip: CHIP_WATCHING, level: 0, trigger: null, banner: false, t: null,
    measures: null, frame: null, rotation: null, cabinLive: true, testing: false, testLevel: 0,
    error: null, aux: "",
    cfg: { enabled: true, min_speed_mph: 30, sensitivity: "standard", name: "James", sounds: ["bark", "voice", "alarm"] },
    gate: { connected: true, simulated: false, kph: 60, moving: true, active: true, parked: false },
  };
  let ui = null, ep = null, lastScene = null, seen = false;
  const announced = new Set();

  function publish() {
    for (const fn of listeners) { try { fn(st); } catch (e) { console.error(e); } }
  }

  function setChip(parked) {
    st.chip = parked ? CHIP_PARKED : CHIP_WATCHING;
    st.gate = { connected: true, simulated: false, kph: parked ? 0 : 60, moving: !parked, active: !parked, parked };
    publish();
  }

  function level(n, trigger) {
    st.level = n;
    st.trigger = trigger;
    publish();
  }

  function play(cues, lv) {
    try {
      const p = d.player().play(cues.map((c) => ({ ...c })), { level: lv });
      if (p && typeof p.catch === "function") p.catch((e) => console.warn("demo drowsy sound:", e));
    } catch (e) { console.warn("demo drowsy sound:", e); }
  }

  function speak() {
    try { Promise.resolve(d.say(SAY_ID)).catch((e) => console.warn("demo drowsy voice:", e)); }
    catch (e) { console.warn("demo drowsy voice:", e); }
  }

  function begin() {
    ep = { timers: [] };
    level(1, L1_KEY);
    play(L1_CUES, 1);
    ep.timers.push(d.later(level2, L2_AT * 1000), d.later(end, END_AT * 1000));
  }

  function level2() {
    if (!ep) return;
    level(2, L2_KEY);
    play(L2_CUES, 2);
    ep.timers.push(d.later(() => { if (ep) speak(); }, SAY_AFTER * 1000));
  }

  // "I'm awake", the car parking, or the end of the 30 s: every timer stops,
  // so a line still to come is never said; a line being said is cut (and
  // voice.js gives the music back only if it was voice.js that ducked it); and
  // the player is asked to fade everything, which it does over 3 s.
  function end() {
    if (!ep) return;
    for (const id of ep.timers) d.cancel(id);
    ep = null;
    level(0, null);
    try { d.stopVoice(); } catch (e) { console.warn("demo drowsy voice:", e); }
    play([{ kind: "fade" }], 0);
  }

  const evKey = (ev) => `${ev.kind}@${ev.at ?? ""}`;

  // One sample of the demo world (live.json's `demo`), as often as it is seen.
  function feed(demo) {
    if (!demo || typeof demo !== "object") return;
    const first = !seen;
    seen = true;
    const scene = typeof demo.scene === "string" ? demo.scene : null;
    if (scene !== null || demo.parked === true) {
      const parked = scene === "parked" || (scene === null && demo.parked === true);
      if (parked !== (st.chip === CHIP_PARKED)) setChip(parked);
      if (parked) end();
    }
    // The scene TURNING drowsy starts an alert: not the first sample the page
    // ever sees (a reload mid-scene is not a reason to sound one), and not a
    // scene that simply goes on after the alert has ended.
    if (scene === "drowsy" && lastScene !== null && lastScene !== "drowsy" && !ep) begin();
    if (scene !== null) lastScene = scene;
    // A hard brake is announced once, however many samples carry it. One
    // already in the first sample is from before this page.
    const ev = demo.event;
    if (ev && typeof ev === "object" && ev.kind) {
      const key = evKey(ev);
      if (!announced.has(key)) {
        announced.add(key);
        if (announced.size > 64) announced.delete(announced.values().next().value);
        if (!first && ev.kind === "hard_brake") d.toast(BRAKE_TOAST);
      }
    }
  }

  const ctl = {
    state: st,
    // The engine's face, for mountDrowsyUI and, if it is ever opened, drowsyui's settings sheet.
    canvas: typeof document !== "undefined" ? document.createElement("canvas") : null,
    preview: false,
    on(fn) { listeners.add(fn); fn(st); return () => listeners.delete(fn); },
    tap() { end(); },
    stopTest() {},
    canTest: () => false,
    canAsk: () => false,
    askTest: async () => false,
    test: () => false,
    reload: async () => false,
    feed,
    // Draw the chip and the cards with drowsyui, on the page's own elements.
    mount(els = {}) {
      const app = els.app || document.getElementById("app");
      const bar = els.bar || document.getElementById("vbar");
      const host = els.host || document.getElementById("modal-host");
      ui = mountDrowsyUI({ engine: ctl, app, bar, host, ...("dz" in els ? { dz: els.dz } : {}) });
      // The chip is a readout here, not the door to the live app's settings
      // sheet, which needs the live engine. drowsyui adds its click listener
      // with addEventListener, so it cannot be taken off; this one goes first
      // (a capturing listener on the target runs before the others) and ends the event.
      ui.chip.addEventListener("click", (e) => e.stopImmediatePropagation(), true);
      ui.chip.tabIndex = -1;
      // While an alert is up the chip says so in its colour. Its text stays
      // one of drowsyui's six: the demo shows nothing the live app would not.
      ctl.on((s) => { if (s.level > 0) ui.chip.dataset.tone = "warn"; });
      return ui;
    },
    // The chip belongs in the top bar's right side; main.js paints the bar and
    // calls this after every paint (the door's afterBar), so put it back if a
    // rebuilt bar lost it. Where it already is, nothing moves.
    afterBar(vbar) {
      if (!ui || !vbar) return;
      const right = vbar.querySelector(".tb-right");
      if (right && ui.chip.parentNode !== right) right.insertBefore(ui.chip, right.firstChild);
    },
    destroy() {
      if (ep) { for (const id of ep.timers) d.cancel(id); ep = null; }
      if (ui) { ui.off(); ui = null; }
      listeners.clear();
    },
  };
  return ctl;
}

// Run `fn` once the app has booted (main.js flips #app's data-booting to "0"),
// or now if there is nothing to wait for.
function whenBooted(app, fn) {
  if (!app || app.dataset.booting !== "1") { fn(); return; }
  const mo = new MutationObserver(() => {
    if (app.dataset.booting !== "1") { mo.disconnect(); fn(); }
  });
  mo.observe(app, { attributes: true, attributeFilter: ["data-booting"] });
}

// The store's live samples, and /api/live when the store has none.
function follow(ctl, { store: s = store, api: a = api, every, clock }) {
  const timer = every || ((fn, ms) => setInterval(fn, ms));
  const now = clock || (() => performance.now() / 1000);
  let fedAt = -Infinity, out = false;
  s.on("live", () => { fedAt = now(); ctl.feed(s.live && s.live.demo); });
  timer(() => {
    if (out || now() - fedAt < POLL_STALE_SECS) return;
    out = true;
    Promise.resolve().then(() => a.live()).then((x) => ctl.feed(x && x.demo), () => {})
      .finally(() => { out = false; });
  }, POLL_EVERY_MS);
}

export function chipAfterBar(vbar) { if (active) active.afterBar(vbar); }

// `deps` is for the tests: the controller's own (player, say, stopVoice, toast,
// later, cancel) and { store, api, every, clock, els }.
export function register(D, deps = {}) {
  const ctl = createDemoDrowsy(deps);
  active = ctl;
  const prev = D.afterBar;
  D.afterBar = (vbar) => {
    if (typeof prev === "function") prev(vbar);
    ctl.afterBar(vbar);
  };
  const els = deps.els || {
    app: document.getElementById("app"), bar: document.getElementById("vbar"),
    host: document.getElementById("modal-host"),
  };
  whenBooted(els.app, () => {
    if (!els.app || !els.bar) return;
    ctl.mount(els);
    ctl.afterBar(els.bar);
    follow(ctl, deps);
  });
  return ctl;
}
