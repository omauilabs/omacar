// Drowsy mode on screen (share/js/drowsyui.js): the chip, the alert cards,
// the Level 3 banner, the "Test the alerts" card and the settings sheet.
//
// The screens are mounted on a FAKE ENGINE and scratch elements, never the
// page's drowsy mode or its top bar, so nothing here polls a server or
// reaches the alert player: no test in this file can make a sound.
import { eq, ok } from "./assert.js";
import {
  chipTitle, testLine, measureLine, audioLine, previewLine, chipTone, TONE, TRIGGER, REST, AUX,
  mountDrowsyUI, openDrowsySheet,
} from "../js/drowsyui.js";
import { chipOf } from "../js/drowsyrun.js";
import { audio } from "../js/audiostate.js";

// The six chip texts (constraints.md, as amended in Task 9).
const CHIPS = ["Watching", "Can't see you", "Paused · stopped", "Paused · no car data",
               "Stopped · face tracker error", "Off"];
const UNPLUGGED = "AUX disconnected — sound is on the tablet's speakers";

// A stand-in for drowsyrun.js's engine: the same state shape and the calls
// the screens make, counted. `gate` is what canTest()/canAsk() answer.
function fake(patch = {}, gate = {}) {
  const fns = new Set();
  const e = {
    state: { chip: "Off", level: 0, trigger: null, banner: false, measures: null, frame: null, gate: null,
             cfg: { enabled: true, min_speed_mph: 30, sensitivity: "standard", name: "James",
                    sounds: ["bark", "voice", "alarm"] },
             cabinLive: false, testing: false, testLevel: 0, error: null, aux: "", ...patch },
    preview: false, canvas: document.createElement("canvas"),
    taps: 0, stops: 0, tests: 0, asks: 0,
    listeners: () => fns.size,
    on(fn) { fns.add(fn); fn(e.state); return () => fns.delete(fn); },
    set(p) { Object.assign(e.state, p); for (const fn of [...fns]) fn(e.state); },
    tap() { e.taps++; },
    stopTest() { e.stops++; },
    canTest: () => !!gate.canTest,
    canAsk: () => !!gate.canAsk,
    async askTest() { e.asks++; if (gate.parkOnAsk) { gate.canTest = true; gate.canAsk = false; } return !!gate.parkOnAsk; },
    test() { if (!gate.canTest) return false; e.tests++; return true; },
    async reload() { return true; },
  };
  return e;
}

// A top bar (with main.js's right-hand group, holding one thing already) and
// an app root of the test's own, never the page's.
function stage({ right = true } = {}) {
  const app = document.createElement("div");
  const bar = document.createElement("header");
  app.appendChild(bar);
  let r = null;
  if (right) {
    r = document.createElement("div");
    r.className = "tb-right";
    r.appendChild(document.createElement("span"));
    bar.appendChild(r);
  }
  document.body.appendChild(app);
  return { app, bar, right: r, done: () => app.remove() };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const rowNamed = (host, label) =>
  [...host.querySelectorAll(".sheet-row")].find((b) => b.querySelector(".sheet-lab").textContent === label);

export default [
  ["the measures line: no picture, no face, and learning the driver's eyes", () =>
    eq([measureLine(null), measureLine({ face: false }),
        measureLine({ face: true, calibrated: false, blink: 0.31, perclos: 0.04, yawns: 0, nods: 1, pitch: -4.4 })],
       ["No picture from the cabin camera yet.", "No face in view.",
        "Closure 0.31 (learning your eyes: the first minute above 30 mph) · PERCLOS 4% · yawns 0 · nods 1 · pitch -4°"])],
  ["and once it has a baseline", () =>
    eq(measureLine({ face: true, calibrated: true, baseline: 0.3, threshold: 0.21, blink: 0.29, perclos: 0.12, yawns: 1, nods: 0, pitch: null }),
       "Closure 0.29 (baseline 0.30, closed above 0.21) · PERCLOS 12% · yawns 1 · nods 0 · pitch –")],
  ["where the sound goes, and only 'disconnected' when the port is known", () =>
    eq([audioLine(null), audioLine({ aux: true, volume: 0.8, managed: true }), audioLine({ aux: false, volume: 0.5 }),
        audioLine({ aux: null, volume: null, port_name: "Speaker" })],
       ["The server did not say where sound is going.", "Sound goes to the AUX cable, at 80%, held there.",
        "Sound goes to the tablet's speakers: AUX disconnected, at 50%.", "Sound goes to Speaker, at an unknown volume."])],
  ["the test card names the level sounding, and is empty with no test", () =>
    eq([testLine({ testing: false }), testLine({ testing: true, testLevel: 0 }), testLine({ testing: true, testLevel: 2 })],
       ["", "Testing the alerts · starting", "Testing the alerts · Level 2"])],
  ["the chip's title: new alerts start above the gate, and simulated driving is ignored", () =>
    eq([chipTitle({ chip: "Watching", gate: { active: false } }),
        chipTitle({ chip: "Watching", gate: { active: false }, cfg: { min_speed_mph: 25 } }),
        chipTitle({ chip: "Watching", gate: { active: true } }),
        chipTitle({ chip: "Off", gate: { simulated: true }, cfg: { enabled: true } })],
       ["Watching. New alerts start above 30 mph.", "Watching. New alerts start above 25 mph.",
        "Drowsy mode: Watching", "Drowsy mode: Off. It ignores simulated driving."])],

  // ---- the six chip texts (Task 9's amended list), each with a tone and a title
  ["the chip has a tone for exactly the six texts the engine can show, and no others", () => {
    const seen = new Set();
    const gates = [null, { simulated: true, connected: false, kph: null },
                   { connected: false, kph: null }, { connected: true, kph: null },
                   { connected: true, kph: 0, moving: false }, { connected: true, kph: 2, moving: true },
                   { connected: true, kph: 60, moving: true, active: true }];
    for (const enabled of [true, false]) for (const gate of gates) for (const cabinLive of [true, false])
      for (const faceLost of [true, false]) for (const trackerFailed of [true, false]) for (const rolling of [true, false])
        seen.add(chipOf({ enabled, gate, measures: { faceLost }, cabinLive, trackerFailed, rolling }));
    eq([[...seen].sort(), Object.keys(TONE).sort()], [[...CHIPS].sort(), [...CHIPS].sort()]);
  }],
  ["watching is ok; can't see you and a face tracker error are warnings; paused and off are neither", () =>
    eq([CHIPS.map(chipTone), chipTone("Paused · parked"), chipTone(undefined)],
       [["ok", "warn", "", "", "warn", ""], "", ""])],
  ["the chip's title says what each of the six means, and gives the reason it has stopped", () => {
    const stopped = { connected: true, kph: 0, moving: false, active: false };
    eq([chipTitle({ chip: "Can't see you", gate: { active: true } }),
        chipTitle({ chip: "Paused · stopped", gate: stopped }),
        chipTitle({ chip: "Paused · no car data", gate: null }),
        chipTitle({ chip: "Stopped · face tracker error", gate: stopped,
                    error: "The face tracker stopped after 3 failed frames (context lost); loading it again" }),
        chipTitle({ chip: "Off", gate: stopped, cfg: { enabled: false } }),
        chipTitle({ chip: "Off", gate: null, cfg: null, error: "No settings from the server" }),
        chipTitle({ chip: "Can't see you", gate: { active: true }, error: "The face tracker did not load: 404." }),
        chipTitle({ chip: "Watching", gate: { active: true }, error: "The face tracker failed on a frame: busy" })],
       ["Drowsy mode: Can't see you. It cannot see your face, and that alone never raises an alert.",
        "Drowsy mode: Paused · stopped. It watches again once the car moves.",
        "Drowsy mode: Paused · no car data. It cannot watch without the car's speed.",
        "Drowsy mode: Stopped · face tracker error. It is not watching your eyes. "
          + "The face tracker stopped after 3 failed frames (context lost); loading it again.",
        "Drowsy mode: Off. Tap for its settings.",
        "Drowsy mode: Off. No settings from the server.",
        "Drowsy mode: Can't see you. It cannot see your face, and that alone never raises an alert. "
          + "The face tracker did not load: 404.",
        "Drowsy mode: Watching. The face tracker failed on a frame: busy."]);
  }],
  // Review minor 2: switched off wins over the simulator. "It ignores
  // simulated driving" is the reason only when drowsy mode is on.
  ["switched off, the title says so even with the simulator running", () =>
    eq([chipTitle({ chip: "Off", gate: { simulated: true }, cfg: { enabled: false } }),
        chipTitle({ chip: "Off", gate: { simulated: true }, cfg: null, error: "No settings from the server" }),
        chipTitle({ chip: "Off", gate: { simulated: true }, cfg: { enabled: true } }),
        chipTitle({ chip: "Off", gate: { simulated: true }, cfg: { enabled: true }, error: "The face tracker failed on a frame: busy" })],
       ["Drowsy mode: Off. Tap for its settings.",
        "Drowsy mode: Off. No settings from the server.",
        "Drowsy mode: Off. It ignores simulated driving.",
        "Drowsy mode: Off. It ignores simulated driving. The face tracker failed on a frame: busy."])],

  // ---- the preview says only what is true (Task 9's carry-forward)
  // The measures are fed only while the car rolls, so a snapshot left from the
  // drive is not a reading. The line shows it as live only when it was built
  // from the very frame on screen (measures.t === frame.t).
  ["the preview line: off, no picture, and live measures only when they come from this frame", () => {
    const on = { enabled: true };
    const frame = { t: 10, face: true, blink: 0.31, jaw: 0.1, pitch: -3 };
    const m = { t: 10, face: true, calibrated: true, baseline: 0.3, threshold: 0.65, blink: 0.31,
                perclos: 0.04, yawns: 0, nods: 0, pitch: -3 };
    eq([previewLine({ cfg: { enabled: false }, cabinLive: true, frame, measures: m }),
        previewLine({ cfg: null }),
        previewLine({ cfg: on, cabinLive: false, frame: null, measures: m }),
        previewLine({ cfg: on, cabinLive: true, frame: null, measures: null }),
        previewLine({ cfg: on, cabinLive: true, frame, measures: m })],
       ["Drowsy mode is off, so nothing watches the cabin camera.",
        "Drowsy mode is off, so nothing watches the cabin camera.",
        "No picture from the cabin camera yet.",
        "No picture from the cabin camera yet.",
        measureLine(m)]);
  }],
  ["stopped, it shows the camera's picture and says it is not measuring, never a stale reading", () => {
    const on = { enabled: true };
    const stale = { t: 10, face: true, calibrated: true, baseline: 0.3, threshold: 0.65, blink: 0.9,
                    perclos: 0.3, yawns: 2, nods: 1, pitch: -3 };
    const learning = { t: 10, face: true, calibrated: false, baseline: null, blink: 0.9, perclos: null,
                       yawns: 0, nods: 0, pitch: -3 };
    const got = [previewLine({ cfg: on, cabinLive: true, frame: { t: 12, face: true, blink: 0.31 }, measures: null }),
                 previewLine({ cfg: on, cabinLive: true, frame: { t: 12, face: false }, measures: stale }),
                 previewLine({ cfg: on, cabinLive: true, frame: { t: 12, face: true, blink: 0.312 }, measures: learning })];
    eq(got, ["Face in view, eye closure 0.31. Not measuring: the measures run only while the car is moving.",
             "No face in view. Not measuring: the measures run only while the car is moving. Open-eye baseline 0.30.",
             "Face in view, eye closure 0.31. Not measuring: the measures run only while the car is moving."]);
    ok(!got.some((s) => /PERCLOS|yawns|nods/.test(s)), "a stale PERCLOS, yawn or nod count is shown as live");
  }],

  // ---- the chip, in the top bar main.js builds
  ["the chip goes first in the top bar's right side, with the engine's text, tone and title", () => {
    const s = stage(), e = fake({ chip: "Can't see you", gate: { active: true } });
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    try {
      const c = s.right.firstChild;
      const first = [c.className, c.type, c.textContent, c.dataset.tone, c.title];
      e.set({ chip: "Watching", gate: { active: false } });
      const then = [c.textContent, c.dataset.tone, c.title];
      e.set({ chip: "Paused · no car data", gate: null });
      eq([first, then, [c.textContent, c.dataset.tone]],
         [["tb-drowsy", "button", "Can't see you", "warn",
           "Drowsy mode: Can't see you. It cannot see your face, and that alone never raises an alert."],
          ["Watching", "ok", "Watching. New alerts start above 30 mph."],
          ["Paused · no car data", ""]]);
    } finally { ui.off(); s.done(); }
  }],
  ["the chip's title also says AUX is disconnected, while it is", () => {
    const s = stage(), e = fake({ chip: "Watching", gate: { active: true }, aux: UNPLUGGED });
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    try {
      const c = s.bar.querySelector(".tb-drowsy");
      const was = c.title;
      e.set({ aux: "" });
      eq([was, c.title], [`Drowsy mode: Watching. ${UNPLUGGED}.`, "Drowsy mode: Watching"]);
    } finally { ui.off(); s.done(); }
  }],
  ["the chip waits for the top bar when main.js has not built it yet", async () => {
    const s = stage({ right: false }), e = fake({ chip: "Paused · no car data" });
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    try {
      const before = s.bar.querySelector(".tb-drowsy");
      const r = document.createElement("div");
      r.className = "tb-right";
      r.appendChild(document.createElement("span"));
      s.bar.appendChild(r);
      await tick();
      eq([before, r.firstChild.className, r.firstChild.textContent, r.children.length],
         [null, "tb-drowsy", "Paused · no car data", 2]);
    } finally { ui.off(); s.done(); }
  }],
  ["tapping the chip opens drowsy mode's settings", () => {
    const s = stage(), e = fake({ chip: "Off" });
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, host, dz: null });
    try {
      s.bar.querySelector(".tb-drowsy").click();
      const sheet = host.querySelector(".sheet");
      eq([host.hidden, sheet && sheet.getAttribute("aria-label"), e.preview], [false, "Drowsy mode", true]);
      host.querySelector(".sheet-head .btn").click();
    } finally { ui.off(); s.done(); host.remove(); }
  }],

  // ---- the alert cards
  ["Level 1 is a card; Levels 2 and 3 fill the screen; each has I'm awake and the rest line", () => {
    const s = stage(), e = fake();
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    const L = ui.layer;
    const text = (sel) => (L.querySelector(sel) ? L.querySelector(sel).textContent : null);
    const look = () => [L.hidden, L.dataset.level, L.firstChild ? L.firstChild.className : null,
                        text(".dz-t"), text(".dz-s"), text(".dz-awake"), !!L.querySelector(".dz-awake.big"),
                        text(".dz-rest")];
    try {
      const seen = [look()];
      e.set({ level: 1, trigger: "perclos" }); seen.push(look());
      L.querySelector(".dz-awake").click();
      e.set({ level: 2, trigger: "closed" }); seen.push(look());
      e.set({ level: 3, trigger: "repeat-l2" }); seen.push(look());
      L.querySelector(".dz-awake").click();
      e.set({ level: 0, trigger: null }); seen.push(look());
      eq([seen, e.taps, ui.layer.parentNode === s.app], [[
        [true, "0", null, null, null, null, false, null],
        [false, "1", "dz-card", "You seem tired. Plan a break soon.", TRIGGER.perclos, "I'm awake", false, REST],
        [false, "2", "dz-full", "Are you with me?", TRIGGER.closed, "I'm awake", true, REST],
        [false, "3", "dz-full l3", "Pull over now", "Stop at the next safe place", "I'm awake", true, REST],
        [true, "0", null, null, null, null, false, null]], 2, true]);
    } finally { ui.off(); s.done(); }
  }],
  ["after Level 3 the banner stays once the card has gone, until the engine lets it go", () => {
    const s = stage(), e = fake();
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    try {
      const seen = [ui.banner.hidden];
      e.set({ level: 3, banner: true }); seen.push(ui.banner.hidden);
      e.set({ level: 0, banner: true }); seen.push(ui.banner.hidden);
      e.set({ banner: false }); seen.push(ui.banner.hidden);
      eq([seen, ui.banner.textContent, ui.banner.getAttribute("role")],
         [[true, true, false, true], "Stop at the next safe place", "status"]);
    } finally { ui.off(); s.done(); }
  }],

  // ---- "Test the alerts"
  ["the test card names the level sounding, Stop stops the test, and it goes with the test", () => {
    const s = stage(), e = fake();
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    const T = ui.test;
    const look = () => [T.hidden, T.querySelector(".dz-test-t").textContent];
    try {
      const seen = [look()];
      e.set({ testing: true, testLevel: 0 }); seen.push(look());
      e.set({ testLevel: 2 }); seen.push(look());
      T.querySelector(".dz-stop").click();
      e.set({ testing: false, testLevel: 0 }); seen.push(look());
      eq([seen, e.stops, T.querySelector(".dz-stop").textContent],
         [[[true, ""], [false, "Testing the alerts · starting"], [false, "Testing the alerts · Level 2"], [true, ""]],
          1, "Stop"]);
    } finally { ui.off(); s.done(); }
  }],
  ["the test card says AUX is disconnected while it is, since the test is where you listen for it", () => {
    const s = stage(), e = fake({ testing: true, testLevel: 1, aux: UNPLUGGED });
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    try {
      const a = ui.test.querySelector(".dz-test-aux");
      const was = [a.hidden, a.textContent];
      e.set({ aux: "" });
      eq([was, a.hidden], [[false, "AUX disconnected"], true]);
    } finally { ui.off(); s.done(); }
  }],

  // ---- ?dz=, for screenshots: drawn, with nothing asked of the engine
  ["?dz=2 draws Level 2's card, ?dz=test the test card, and neither asks the engine for anything", () => {
    const got = [];
    for (const dz of ["1", "2", "3", "test", "9"]) {
      const s = stage(), e = fake({ chip: "Paused · no car data" });
      const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz });
      got.push([dz, ui.layer.hidden, ui.layer.dataset.level, ui.test.hidden,
                ui.test.querySelector(".dz-test-t").textContent, e.taps + e.stops + e.tests + e.asks]);
      ui.off(); s.done();
    }
    eq(got, [["1", false, "1", true, "", 0], ["2", false, "2", true, "", 0], ["3", false, "3", true, "", 0],
             ["test", true, "0", false, "Testing the alerts · Level 2", 0], ["9", true, "0", true, "", 0]]);
  }],
  ["off() takes the chip, the layer, the banner and the test card away, and stops listening", () => {
    const s = stage(), e = fake();
    const ui = mountDrowsyUI({ engine: e, app: s.app, bar: s.bar, dz: null });
    ui.off();
    try {
      eq([e.listeners(), !!s.app.querySelector(".tb-drowsy, .dz-layer, .dz-banner, .dz-test")], [0, false]);
    } finally { s.done(); }
  }],

  // ---- the settings sheet
  ["the sheet warns while sound is on the tablet's speakers, and says where sound goes", () => {
    const was = audio.last;
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const seen = [];
    try {
      for (const a of [{ aux: false, volume: 0.5 }, { aux: true, volume: 1, managed: true }, null]) {
        audio.last = a;
        const close = openDrowsySheet({ engine: fake(), host });
        const w = host.querySelector(".aux-warn");
        seen.push([w.hidden, w.textContent, host.querySelector(".dz-aux").textContent]);
        close();
      }
      eq(seen, [
        [false, UNPLUGGED, "Sound goes to the tablet's speakers: AUX disconnected, at 50%."],
        [true, "", "Sound goes to the AUX cable, at 100%, held there."],
        [true, "", "The server did not say where sound is going."]]);
    } finally { audio.last = was; host.remove(); }
  }],
  ["the sheet carries the rest line and the AUX note, and Done turns the preview off and stops listening", () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const e = fake({ chip: "Paused · stopped" });
    try {
      openDrowsySheet({ engine: e, host });
      const open = [host.hidden, e.preview, e.listeners(), host.querySelector(".dz-preview canvas") === e.canvas,
                    [...host.querySelectorAll(".dz-rest")].map((p) => p.textContent)];
      host.querySelector(".sheet-head .btn").click();
      eq([open, [host.hidden, e.preview, e.listeners(), host.childElementCount]],
         [[false, true, 1, true, [REST, AUX]], [true, false, 0, 0]]);
    } finally { host.remove(); }
  }],
  ["while stopped, the sheet's preview shows the picture and says it is not measuring", () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const stale = { t: 10, face: true, calibrated: true, baseline: 0.3, threshold: 0.65, blink: 0.9,
                    perclos: 0.3, yawns: 2, nods: 0, pitch: -3 };
    const e = fake({ chip: "Paused · stopped", cabinLive: true, frame: { t: 12, face: true, blink: 0.31 }, measures: stale });
    try {
      const close = openDrowsySheet({ engine: e, host });
      const seen = [host.querySelector(".dz-status").textContent, host.querySelector(".dz-measures").textContent];
      e.set({ chip: "Watching", gate: { active: true }, frame: { t: 13, face: true, blink: 0.2 },
              measures: { ...stale, t: 13, blink: 0.2, perclos: 0.05 } });
      seen.push(host.querySelector(".dz-status").textContent, host.querySelector(".dz-measures").textContent);
      close();
      eq(seen, ["Drowsy mode: Paused · stopped. It watches again once the car moves.",
                "Face in view, eye closure 0.31. Not measuring: the measures run only while the car is moving. "
                  + "Open-eye baseline 0.30.",
                "Drowsy mode: Watching",
                "Closure 0.20 (baseline 0.30, closed above 0.65) · PERCLOS 5% · yawns 2 · nods 0 · pitch -3°"]);
    } finally { host.remove(); }
  }],
  // Found in the ?dz=sheet screenshot: a sheet opened before the settings had
  // loaded showed "Off" for everything, and stayed so. Tapping a sound there
  // would have saved a rotation of that one sound, dropping the other two.
  ["the rows follow settings that arrive after the sheet opened, and cannot be changed while unknown", () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const e = fake({ chip: "Off", cfg: null });
    const LABELS = ["Drowsy mode", "Sensitivity", "The name the voice uses", "The bark", "The voice", "The two-tone alarm"];
    const look = () => LABELS.map((l) => { const r = rowNamed(host, l); return [r.disabled, r.querySelector(".sheet-v").textContent]; });
    try {
      const close = openDrowsySheet({ engine: e, host });
      const before = look();
      e.set({ chip: "Paused · no car data",
              cfg: { enabled: true, sensitivity: "sensitive", name: "James", sounds: ["bark", "alarm"], min_speed_mph: 30 } });
      const after = look();
      close();
      eq([before, after], [
        [[true, "–"], [true, "–"], [true, "–"], [true, "–"], [true, "–"], [true, "–"]],
        [[false, "On"], [false, "Sensitive"], [false, "James"], [false, "On"], [false, "Off"], [false, "On"]]]);
    } finally { host.remove(); }
  }],
  ["Test the alerts is greyed out unless the car is stopped, and follows the car while the sheet is open", () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const gate = { canTest: false };
    const e = fake({ chip: "Paused · no car data" }, gate);
    try {
      const close = openDrowsySheet({ engine: e, host });
      const look = () => {
        const r = rowNamed(host, "Test the alerts");
        return [r.disabled, r.querySelector(".sheet-note").textContent, r.querySelector(".sheet-v").textContent];
      };
      const seen = [look()];
      gate.canTest = true;
      e.set({ chip: "Paused · stopped" });
      seen.push(look());
      rowNamed(host, "Test the alerts").click();
      eq([seen, e.tests, host.hidden], [[
        [true, "Only while stopped", "Play"],
        [false, "Level 1, 2 and 3 in turn, about half a minute. Stops if the car moves", "Play"]], 1, true]);
      close();
    } finally { host.remove(); }
  }],
  ["where the test gate wants Park, the row asks for the gear first, then plays", async () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const gate = { canTest: false, canAsk: true, parkOnAsk: true };
    const e = fake({ chip: "Paused · stopped" }, gate);
    try {
      openDrowsySheet({ engine: e, host });
      const r = rowNamed(host, "Test the alerts");
      const before = [r.disabled, r.querySelector(".sheet-note").textContent];
      r.click();
      await tick();
      eq([before, e.asks, e.tests, host.hidden], [[false, "Only in Park. Tap to check the gear"], 1, 1, true]);
    } finally { host.remove(); }
  }],
  // Review minor 1: with a test running, canTest() is false, and the row used
  // to show "Only while parked", greyed out. It shows the test instead.
  ["while a test is running, the row says so and stops it, rather than a greyed-out rule", () => {
    const host = document.createElement("div");
    host.hidden = true;
    document.body.appendChild(host);
    const e = fake({ chip: "Paused · stopped", testing: true, testLevel: 1 }, { canTest: false });
    try {
      const close = openDrowsySheet({ engine: e, host });
      const r = rowNamed(host, "Test the alerts");
      const got = [r.disabled, r.querySelector(".sheet-note").textContent, r.querySelector(".sheet-v").textContent];
      r.click();
      const stops = e.stops;
      e.set({ testing: false, testLevel: 0 });
      const r2 = rowNamed(host, "Test the alerts");
      eq([got, stops, [r2.disabled, r2.querySelector(".sheet-note").textContent]],
         [[false, "Testing now", "Stop"], 1, [true, "Only while stopped"]]);
      close();
    } finally { host.remove(); }
  }],
  // Review minor 3: each sound row used to toggle the list it was drawn with,
  // so a second tap before the first save came back posted a stale list and
  // undid the first. Saves now go one at a time, each from the settings the
  // one before left: the engine's, reloaded, or else the server's answer.
  ["two quick taps on the sound rows both land, whether or not the reload after each save works", async () => {
    const seen = [];
    for (const reloads of [true, false]) {
      const host = document.createElement("div");
      host.hidden = true;
      document.body.appendChild(host);
      const server = { cfg: { enabled: true, sensitivity: "standard", name: "James", min_speed_mph: 30,
                              sounds: ["bark", "voice", "alarm"] } };
      const posted = [];
      const post = async (path, change) => {
        await tick();
        posted.push([path, change]);
        server.cfg = { ...server.cfg, ...change };
        return { ...server.cfg };
      };
      const e = fake({ chip: "Paused · stopped", cfg: { ...server.cfg } });
      let reloaded = 0;
      e.reload = async () => {
        await tick();
        reloaded++;
        if (reloads) e.set({ cfg: { ...server.cfg } });
        return reloads;
      };
      try {
        const close = openDrowsySheet({ engine: e, host, post });
        rowNamed(host, "The bark").click();
        rowNamed(host, "The voice").click();
        for (let i = 0; i < 200 && reloaded < 2; i++) await tick();
        await tick();
        const shown = ["The bark", "The voice", "The two-tone alarm"]
          .map((l) => rowNamed(host, l).querySelector(".sheet-v").textContent);
        close();
        seen.push([reloads, posted, server.cfg.sounds, reloads ? shown : null]);
      } finally { host.remove(); }
    }
    const both = [["/api/drowsy", { sounds: ["voice", "alarm"] }], ["/api/drowsy", { sounds: ["alarm"] }]];
    eq(seen, [[true, both, ["alarm"], ["Off", "Off", "On"]], [false, both, ["alarm"], null]]);
  }],

  // ---- the wiring: alertness.js and main.js, read as text
  // alertness.js cannot be imported here: it starts the page's drowsy mode.
  ["alertness.js keeps its one 30 s audio poll, mounts the screens before starting, and plays nothing itself", async () => {
    const src = (await (await fetch("../js/alertness.js", { cache: "no-store" })).text()).replace(/^\s*\/\/.*$/gm, "");
    const at = (s) => src.indexOf(s);
    eq([(src.match(/setInterval\(/g) || []).length, src.includes("setInterval(applyAudio, 30000)"),
        /onCues|setName|alertPlayer|\.play\(/.test(src),
        at("mountDrowsyUI()") >= 0 && at("startDrowsy()") > at("mountDrowsyUI()")],
       [1, true, false, true]);
  }],
  ["Settings has a Drowsy mode row that opens this sheet, and writes no hash", async () => {
    const main = await (await fetch("../js/main.js", { cache: "no-store" })).text();
    const hunk = main.split("// ---- redesign/cameras: drowsy mode's own sheet")[1] || "";
    const body = hunk.split("// ---- end redesign/cameras")[0];
    eq([body.includes('row("Drowsy mode"'), body.includes('import("./drowsyui.js").then((m) => m.openDrowsySheet())'),
        /location\.hash/.test(body), main.indexOf("// ---- redesign/cameras: drowsy mode's own sheet")
          < main.indexOf('rows.appendChild(row("Learn mode"')],
       [true, true, false, true]);
  }],
];
