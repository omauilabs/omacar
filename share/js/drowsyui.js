// Drowsy mode on screen: the status chip in the top bar, the alert cards, the
// Level 3 banner, the "Test the alerts" card, and the settings sheet
// (Settings -> Drowsy mode, or a tap on the chip).
//
// The chip goes into the top bar from here. main.js builds that bar once and
// never rebuilds it ("the vehicle bar"), so an element added beside its own
// stays put, and main.js carries no line for it.
//
// IT DRAWS; IT PLAYS NOTHING. Every sound comes from the engine
// (drowsyrun.js), which hands its cues to the page's one alert player
// (alertplayer.js). Nothing here touches the player or the audio stage, so
// no path from this file can set a level in one step.
//
// THE CHIP HAS SIX TEXTS, exactly the engine's (drowsyrun.js chipOf):
// "Watching", "Can't see you", "Paused · stopped", "Paused · no car data",
// "Stopped · face tracker error" and "Off" (the spec's four, as amended in
// Task 9). Each has a tone (TONE) and a title that says what it means.
//
// AUX. Drowsy mode's screens say "AUX disconnected" while the port is known
// to be the tablet's speakers (audiostate.js showAux / state.aux): in the
// settings sheet, on the "Test the alerts" card, and in the chip's title. Not
// on the alert cards themselves, which a tired driver should find holding
// nothing but the alert and "I'm awake". Home's card (Task 11) is not needed
// for any of it.
//
// ?dz=1, 2 or 3 draws that level's card with no sound, ?dz=test the "Test
// the alerts" card, and ?dz=sheet opens the settings sheet, for screenshots.

import { h, clear, toast } from "./core.js";
import { postJSON } from "./camapi.js";
import { drowsy } from "./drowsyrun.js";
import { onAudio, showAux } from "./audiostate.js";

export const TRIGGER = {
  perclos: "Your eyes have been closing more and more",
  closed: "Your eyes closed",
  yawns: "You have been yawning",
  nods: "Your head has been nodding",
  "since-stop": "Two hours without a stop",
  night: "It is the small hours",
  "repeat-l2": "A second wake-up within five minutes",
};
// Watching is good news; not seeing the driver, or a tracker that has
// stopped, needs a look; paused and off are neither.
export const TONE = {
  "Watching": "ok",
  "Can't see you": "warn",
  "Paused · stopped": "",
  "Paused · no car data": "",
  "Stopped · face tracker error": "warn",
  "Off": "",
};
export const chipTone = (chip) => TONE[chip] || "";
// What each chip means, for its title. "Watching" is said in chipTitle.
const CHIP_NOTE = {
  "Can't see you": "It cannot see your face, and that alone never raises an alert.",
  "Paused · stopped": "It watches again once the car moves.",
  "Paused · no car data": "It cannot watch without the car's speed.",
  "Stopped · face tracker error": "It is not watching your eyes.",
  "Off": "Tap for its settings.",
};
const SOUND_LABEL = { bark: "The bark", voice: "The voice", alarm: "The two-tone alarm" };
// The ?dz= preview's reason line: a trigger that raises that level.
const PREVIEW_TRIGGER = { 1: "perclos", 2: "closed", 3: "closed" };
export const REST = "Alerts buy you minutes, not safety. The fix is to stop and rest: "
  + "a 20-minute nap, or a coffee (NHTSA, AAA Foundation).";
export const AUX = "Keep the car's radio on AUX. OmaCar's sound reaches the car through that cable, "
  + "and it cannot see which source the radio is on.";

// Sentences, the first with a full stop only when more follow.
function says(first, ...more) {
  const rest = more.filter(Boolean);
  return rest.length ? [first.replace(/\.?$/, "."), ...rest].join(" ") : first;
}
const sentence = (s) => (s ? String(s).replace(/\.+$/, "") + "." : "");

// Pure, for the tests: the lines the chip, the test card and the sheet show.
export function chipTitle(st) {
  const mph = (st.cfg && st.cfg.min_speed_mph) || 30;
  const why = sentence(st.error);
  // Switched off wins: the simulator is the reason only when drowsy mode is on.
  const on = !!(st.cfg && st.cfg.enabled);
  if (st.chip === "Off" && on && st.gate && st.gate.simulated) return says("Drowsy mode: Off", "It ignores simulated driving.", why);
  if (st.chip === "Off" && why) return says("Drowsy mode: Off", why);
  if (st.chip === "Watching" && st.gate && !st.gate.active) return says("Watching", `New alerts start above ${mph} mph.`, why);
  return says(`Drowsy mode: ${st.chip}`, CHIP_NOTE[st.chip], why);
}

// The chip's whole title: what it means (chipTitle), then that AUX is out
// while it is. The top bar's chip and Home's Dashcams card both use this, so a
// hover on either says the same thing.
export const chipHint = (st) => says(chipTitle(st), sentence(st.aux));

export function testLine(st) {
  if (!st.testing) return "";
  return "Testing the alerts · " + (st.testLevel ? `Level ${st.testLevel}` : "starting");
}

const n2 = (x) => (x === null || x === undefined ? "–" : x.toFixed(2));

export function measureLine(m) {
  if (!m) return "No picture from the cabin camera yet.";
  if (!m.face) return "No face in view.";
  const pc = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
  const base = m.calibrated
    ? `baseline ${n2(m.baseline)}, closed above ${n2(m.threshold)}`
    : "learning your eyes: the first minute above 30 mph";
  const pitch = m.pitch === null || m.pitch === undefined ? "–" : `${Math.round(m.pitch)}°`;
  return `Closure ${n2(m.blink)} (${base}) · PERCLOS ${pc(m.perclos)} · yawns ${m.yawns} · nods ${m.nods} · pitch ${pitch}`;
}

// THE PREVIEW SAYS ONLY WHAT IS TRUE. The measures are fed only while the car
// rolls (drowsyrun.js), so while it is stopped state.measures is whatever the
// last drive left: a PERCLOS, a yawn count, that nothing is updating. They
// are shown as live only when they were built from the very frame the
// tracker saw last (measures.t === frame.t). Otherwise the line shows that
// frame -- a face or none, and its eye closure -- says it is not measuring,
// and keeps only what does outlast a stop: the open-eye baseline.
export function previewLine(st) {
  if (!st.cfg || !st.cfg.enabled) return "Drowsy mode is off, so nothing watches the cabin camera.";
  const f = st.frame, m = st.measures;
  if (!st.cabinLive || !f) return measureLine(null);
  if (m && m.t === f.t) return measureLine(m);
  return says(f.face ? `Face in view, eye closure ${n2(f.blink)}` : "No face in view",
              "Not measuring: the measures run only while the car is moving.",
              m && m.calibrated ? `Open-eye baseline ${n2(m.baseline)}.` : "");
}

export function audioLine(a) {
  if (!a) return "The server did not say where sound is going.";
  const vol = a.volume === null || a.volume === undefined ? "an unknown volume" : `${Math.round(a.volume * 100)}%`;
  const where = a.aux === true ? "the AUX cable" : a.aux === false
    ? "the tablet's speakers: AUX disconnected" : (a.port_name || "an output OmaCar cannot name");
  return `Sound goes to ${where}, at ${vol}${a.managed ? ", held there" : ""}.`;
}

// ---- the alert cards ---------------------------------------------------------

function awake(engine, big) {
  return h("button.dz-awake" + (big ? ".big" : ""), { type: "button", onclick: () => engine.tap() }, "I'm awake");
}

// Level 1 is a card above the navigation; Levels 2 and 3 fill the screen.
// Every card has "I'm awake" and the rest line.
function paintLayer(engine, layer, level, trigger) {
  clear(layer);
  layer.hidden = !level;
  layer.dataset.level = String(level || 0);
  const why = h("div.dz-s", TRIGGER[trigger] || "");
  if (level === 1) layer.append(h("div.dz-card", h("div.dz-t", "You seem tired. Plan a break soon."), why, h("p.dz-rest", REST), awake(engine, false)));
  if (level === 2) layer.append(h("div.dz-full", h("div.dz-t", "Are you with me?"), why, awake(engine, true), h("p.dz-rest", REST)));
  if (level === 3) layer.append(h("div.dz-full.l3", h("div.dz-t", "Pull over now"), h("div.dz-s", "Stop at the next safe place"), awake(engine, true), h("p.dz-rest", REST)));
}

// The chip, the alert layer, the banner and the test card, drawn from the
// engine's state. `engine`, `app`, `bar`, `host` and `dz` are the page's
// unless a test hands in its own. Returns { chip, layer, banner, test, off }.
export function mountDrowsyUI({
  engine = drowsy,
  app = document.getElementById("app"),
  bar = document.getElementById("vbar"),
  host = document.getElementById("modal-host"),
  dz = new URLSearchParams(location.search).get("dz"),
} = {}) {
  const chipT = h("span.tb-drowsy-t", "Off");
  const chip = h("button.tb-drowsy", { type: "button", onclick: () => openDrowsySheet({ engine, host }) },
    h("span.tb-drowsy-dot", { "aria-hidden": "true" }), chipT);
  const place = () => {
    const right = bar.querySelector(".tb-right");
    if (!right) return false;
    right.insertBefore(chip, right.firstChild);
    return true;
  };
  let mo = null;
  if (!place()) {
    mo = new MutationObserver(() => { if (place()) { mo.disconnect(); mo = null; } });
    mo.observe(bar, { childList: true, subtree: true });
  }
  const layer = h("div.dz-layer", { hidden: true });
  const banner = h("div.dz-banner", { hidden: true, role: "status" }, "Stop at the next safe place");
  // "Test the alerts" runs only while parked, and always shows this card with
  // Stop; the car moving off stops it too (drowsyrun.js). It is where the
  // owner listens for the car's speakers, so it says when they are not in use.
  const testT = h("span.dz-test-t");
  const testAux = h("span.dz-test-aux", { hidden: true }, "AUX disconnected");
  const test = h("div.dz-test", { hidden: true, role: "status" }, testT, testAux,
    h("button.dz-stop", { type: "button", onclick: () => engine.stopTest() }, "Stop"));
  app.append(layer, banner, test);

  // The screenshot previews draw only; they ask the engine for nothing.
  const preview = ["1", "2", "3"].includes(dz) ? Number(dz) : 0;
  let shown = -1;
  const off = engine.on((st) => {
    chipT.textContent = st.chip;
    chip.dataset.tone = chipTone(st.chip);
    chip.title = chipHint(st);
    banner.hidden = !(st.banner && st.level === 0);
    const tl = dz === "test" ? testLine({ testing: true, testLevel: 2 }) : testLine(st);
    test.hidden = !tl;
    testT.textContent = tl;
    testAux.hidden = !st.aux;
    const level = preview || st.level;
    if (level !== shown) { shown = level; paintLayer(engine, layer, level, preview ? PREVIEW_TRIGGER[preview] : st.trigger); }
  });
  if (dz === "sheet") openDrowsySheet({ engine, host });
  return {
    chip, layer, banner, test,
    off() {
      off();
      if (mo) mo.disconnect();
      for (const el of [chip, layer, banner, test]) el.remove();
    },
  };
}

// ---- the settings sheet ------------------------------------------------------

// Drowsy mode's own sheet: the cabin picture and what is known about it, the
// settings, "Test the alerts", where sound goes, and the two notes the spec
// asks for (rest is the fix; keep the radio on AUX). Opening it turns the
// engine's preview on, so the cabin camera is watched while it is open;
// closing it turns the preview off. Returns close().
export function openDrowsySheet({ engine = drowsy, host = document.getElementById("modal-host"), post = postJSON } = {}) {
  const offs = [];
  let open = true;
  const close = () => {
    if (!open) return;
    open = false;
    engine.preview = false;
    for (const off of offs) off();
    host.hidden = true;
    clear(host);
    host.onclick = null;
  };
  const row = (label, note, value, onclick, disabled) =>
    h("button.sheet-row", { type: "button", onclick, disabled: !!disabled },
      h("span.sheet-l", h("span.sheet-lab", label), note ? h("span.sheet-note", note) : null),
      h("span.sheet-v", value));
  const rows = h("div.sheet-rows");
  const status = h("div.dz-status");
  const measures = h("div.dz-measures");
  const unplugged = h("div.aux-warn", { hidden: true });
  const audioNote = h("div.dz-aux");

  // SAVES GO ONE AT A TIME, EACH FROM THE SETTINGS THE ONE BEFORE LEFT. A row
  // hands save() a function of the settings, not a finished change, and it is
  // worked out only when its turn comes: from the engine's settings once a
  // reload has brought them in, or else from the server's answer to the last
  // save (POST /api/drowsy answers with the whole merged file). So two quick
  // taps on two sounds both land; neither posts the list the rows were drawn
  // with and undoes the other.
  let saving = Promise.resolve();
  let answered = null;
  function save(make) {
    saving = saving.then(async () => {
      const c = answered || engine.state.cfg;
      const change = c ? make(c) : null;
      if (!change) return;
      try {
        const saved = await post("/api/drowsy", change);
        answered = (await engine.reload()) ? null : saved;
        redraw();
      } catch (e) { toast("Could not save: " + e.message, "bad"); }
    });
    return saving;
  }

  // "Test the alerts" follows the test gate (drowsyrun.js): it plays while the
  // car is stopped; where the gate wants Park and can ask the gear, it asks
  // first (the parked-confirm plan's row); otherwise it is greyed out. The
  // gate is "connected, 0 km/h" until that plan merges, so the rule says
  // stopped, as the chip does. While a test runs the row says so, and stops it.
  function testRow() {
    if (engine.state.testing) return row("Test the alerts", "Testing now", "Stop", () => engine.stopTest());
    const may = engine.canTest(), ask = !may && engine.canAsk();
    return row("Test the alerts",
      may ? "Level 1, 2 and 3 in turn, about half a minute. Stops if the car moves"
        : ask ? "Only in Park. Tap to check the gear" : "Only while stopped",
      "Play", async () => {
        if (!engine.canTest() && engine.canAsk()) await engine.askTest();
        if (engine.test()) close(); else toast("Only while stopped.");
      }, !may && !ask);
  }

  // With no settings from the server yet, every setting reads "–" and cannot
  // be changed: a toggle from an unknown value would save a guess (tapping a
  // sound would save a rotation of that one sound, dropping the others).
  function redraw() {
    const known = !!engine.state.cfg;
    const c = engine.state.cfg || {};
    const sounds = c.sounds || [];
    const val = (s) => (known ? s : "–");
    const setting = (label, note, value, onclick) => row(label, note, val(value), onclick, !known);
    clear(rows);
    // Each toggle is of the settings save() hands it (`now`), never of `c`,
    // the settings these rows were drawn with (see save()).
    rows.append(
      setting("Drowsy mode", "Watches your eyes above 30 mph and wakes you gently", c.enabled ? "On" : "Off",
        () => save((now) => ({ enabled: !now.enabled }))),
      setting("Sensitivity", "Sensitive lowers every threshold by 20%", c.sensitivity === "sensitive" ? "Sensitive" : "Standard",
        () => save((now) => ({ sensitivity: now.sensitivity === "sensitive" ? "standard" : "sensitive" }))),
      setting("The name the voice uses", "Only a name the voice was recorded with", c.name ? c.name : "No name",
        () => save((now) => ({ name: now.name ? "" : "James" }))),
      ...["bark", "voice", "alarm"].map((s) => setting(SOUND_LABEL[s], "In the Level 2 rotation", sounds.includes(s) ? "On" : "Off",
        () => save((now) => {
          const had = now.sounds || [];
          const next = had.includes(s) ? had.filter((x) => x !== s) : [...had, s];
          if (!next.length) { toast("At least one sound has to stay in the rotation."); return null; }
          return { sounds: next };
        }))),
      testRow());
  }

  const sheet = h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Drowsy mode" },
    h("div.sheet-head", h("div.title", "Drowsy mode"), h("button.btn.right", { type: "button", onclick: close }, "Done")),
    h("div.dz-preview", engine.canvas, h("div", status, measures)),
    unplugged, rows, audioNote, h("p.dz-rest", REST), h("p.dz-rest", AUX));
  clear(host);
  host.appendChild(sheet);
  host.hidden = false;
  host.onclick = (e) => { if (e.target === host) close(); };
  engine.preview = true;
  // The rows are redrawn only when the settings arrive or change, or when
  // what the Test row says would change; not on every frame: a sheet rebuilt
  // ten times a second loses the finger on it.
  let testKey = null, cfgSeen;
  offs.push(engine.on((st) => {
    status.textContent = chipTitle(st);
    measures.textContent = previewLine(st);
    const key = `${engine.canTest()}|${engine.canAsk()}|${!!st.testing}`;
    if (key !== testKey || st.cfg !== cfgSeen) { testKey = key; cfgSeen = st.cfg; redraw(); }
  }));
  offs.push(showAux(unplugged));
  offs.push(onAudio((a) => { audioNote.textContent = audioLine(a); }));
  return close;
}
