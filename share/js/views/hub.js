// The car hub -- the screen you navigate from while sitting in the driver's seat.
//
// The app used to navigate from a 44px rail of small icons down the left,
// which was right at a desk with a mouse and wrong in a moving car: the targets
// were too small to hit without looking, and looking is the thing you cannot
// spend. That rail is gone now, replaced by the bottom tab bar, and this screen
// is where it lands. This is the same application with the same data and a
// different set of physical assumptions --
// big targets, few of them, high contrast, and every tile carrying the one
// number that tells you whether it is worth opening at all.
//
// It is not a simplified OmaCar. Almost nothing is removed; the secondary row
// still reaches every screen a driver has any business opening from a seat.
// The two exceptions -- Tests and Resets -- are explained where they used to
// be listed. It is the same app laid out for a different hand.
//
//
// HOW THIS SCREEN UPDATES ITSELF, AND WHY IT MUST NOT GO BACK
//
// Read this before "simplifying" the update code below into a redraw.
//
// The hub is flagged fast:true, so it repaints on the 250ms live poll -- about
// four times a second, forever, because it is also the screen the app lands on.
// The version of this file that shipped first rebuilt the entire DOM on every
// one of those ticks. That is not a performance opinion, it is a correctness
// bug on a touchscreen: a finger goes down on a tile, the tick fires, the tile
// is destroyed and replaced by an identical-looking one, the finger comes up on
// the new node, and the browser dispatches `click` on the nearest common
// ancestor of the two -- .hub, not the tile. The tap simply does not happen.
// The same teardown also saved and restored scrollTop four times a second,
// which cancels a fling part-way through.
//
// So: the structure is built exactly ONCE, on mount, and every tick after that
// only writes text and toggles classes and attributes on nodes that were
// already there. Nothing that can be under a finger is ever removed and
// recreated on a timer. Where a list of targets genuinely changes -- a look,
// a plugin, a car with no AI -- it is reconciled by a stable key (syncKeys
// below) so only the entries that actually changed are touched, and the ones
// that did not keep their identity, their focus and their in-flight touch.
//
// The rule, stated so the next person does not have to re-derive it: on this
// screen a repaint may change what a node SAYS; it may never change which node
// it is.

import { h, store, dist, U, readOnly,
         adapterState, connectCar } from "../core.js";
import { ICONS } from "../icons.js";
import { explain } from "../learn.js";
import { radio, radioPlayer } from "../radio.js";
import { LOOKS, savedLook, saveLook, applyLook, nextLook, lookById,
         mountLookEffect } from "../looks.js";

// Sunlight, not taste. A phone in a windscreen mount is competing with the sky,
// and the grey-on-black that reads as elegant indoors is unreadable at noon.
// White on near-black, and nothing dimmer than #C8 anywhere on this screen.
const PRIMARY = [
  { id: "drive",   label: "Drive",  hint: "Gauges" },
  { id: "codes",   label: "Codes",  hint: "Faults" },
  { id: "scan",    label: "Scan",   hint: "Full system" },
  { id: "data",    label: "Data",   hint: "Live values" },
  { id: "health",  label: "Health", hint: "Readiness" },
  { id: "advisor", label: "AI",     hint: "Ask" },
];

// NOTE ON WHAT IS *NOT* HERE: Tests and Resets.
//
// Both used to sit in this row, one tap from the screen the app lands on. Both
// command the car rather than read it -- Tests fires actuators (the catalogue
// includes killing an injector), Resets writes to the ECU. A control that does
// something physical to a vehicle does not belong under a thumb that is
// steadying itself on a bumpy road, and every other entry in this row is a
// read-only destination, so the row teaches the hand that nothing here is
// consequential. That is exactly the habit you do not want when one of the
// twelve is an actuator command.
//
// They are not deleted from the app: they keep their routes and their places
// under Data and Care, two taps away, where the surrounding screen explains
// what they do first. This row is the in-car set; a workbench is where you
// command a car from.
const SECONDARY = [
  { id: "dash", label: "Home" }, { id: "live", label: "Cluster" },
  { id: "concerns", label: "Trends" },
  { id: "service", label: "Service" }, { id: "history", label: "Log" },
  { id: "garage", label: "Garage" }, { id: "learn", label: "Learn" },
  { id: "replay", label: "Replay" },
  { id: "documents", label: "Docs" },
  { id: "report", label: "Report" },
];

function svg(paths, size) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("width", size); el.setAttribute("height", size);
  el.setAttribute("aria-hidden", "true");
  for (const d of paths || []) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d); p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor"); p.setAttribute("stroke-width", "1.7");
    p.setAttribute("stroke-linecap", "round"); p.setAttribute("stroke-linejoin", "round");
    el.appendChild(p);
  }
  return el;
}

// The badge is the reason this is a dashboard and not a menu. A tile that says
// only "Codes" makes you open Codes to find out whether it matters; a tile that
// says "Codes / 2 active" has already answered the question from a glance.
function badgeFor(id, car) {
  if (!car) return null;
  const faults = car.active_faults || [];
  switch (id) {
    case "codes":
      return faults.length
        ? { text: `${faults.length} active`, tone: faults.some((f) => f.severity === "critical") ? "bad" : "warn" }
        : { text: "clear", tone: "ok" };
    case "health": {
      const r = car.readiness || {};
      const names = Object.keys(r);
      const done = names.filter((k) => r[k] === true || r[k] === "complete").length;
      return names.length ? { text: `${done}/${names.length} ready`, tone: done === names.length ? "ok" : "warn" } : null;
    }
    case "drive": {
      const sp = store.values && store.values.SPEED;
      return { text: store.connected ? (sp ? `${Math.round(sp)}` : "linked") : "no link",
               tone: store.connected ? "ok" : "warn" };
    }
    case "scan":
      return car.scanned_at ? { text: "ready", tone: "ok" } : null;
    case "advisor":
      return store.aiOn ? { text: "ready", tone: "ok" } : { text: "off", tone: "warn" };
    case "data": {
      const n = (store.sample.supported || car.supported || []).length;
      return n ? { text: `${n} PIDs`, tone: "ok" } : null;
    }
    default: return null;
  }
}

// Four numbers, chosen because they are the ones a driver actually glances at,
// and sized so they can be read without leaning in. A table rather than four
// hand-built cells, because the cells are now built once and only re-read on
// each tick: the reader below has to be a function of the store, not a snapshot
// of it taken at build time.
const VITALS = [
  { key: "speed", label: "Speed",
    value: (v) => (v.SPEED != null ? String(Math.round(v.SPEED * U.units.km)) : "--"),
    unit: () => U.units.speed },
  { key: "rpm", label: "RPM",
    value: (v) => (v.RPM != null ? String(Math.round(v.RPM)) : "--"),
    unit: () => "" },
  { key: "coolant", label: "Coolant",
    value: (v) => (v.COOLANT_TEMP != null ? String(Math.round(v.COOLANT_TEMP)) : "--"),
    unit: () => "°" },
  { key: "trip", label: "Trip",
    value: (v, car) => (car.trip_km != null ? dist(car.trip_km, false) : "--"),
    unit: () => U.units.dist },
];

function effectLabel() { return lookById(savedLook()).label; }

// The effect canvas lives OUTSIDE the redraw cycle.
//
// The hub repaints on every live sample -- several times a second while
// driving. Rebuilding the canvas each time would restart the animation
// constantly and leak a requestAnimationFrame loop per repaint, which on this
// machine is exactly the kind of background cost that crashed the compositor
// once already. So it is mounted once, and only torn down when the effect
// changes or the view goes away.
let fxHost = null;
let fxStop = null;
let fxMode = null;

function redrawFx() {
  if (!fxHost) return;
  const want = savedLook();
  applyLook(want);
  if (want === fxMode) return;
  if (fxStop) { fxStop(); fxStop = null; }
  fxMode = want;
  fxStop = mountLookEffect(fxHost, want);
  const btn = document.querySelector(".hub-look");
  if (btn) btn.textContent = effectLabel();
}

// The Connect state lives out here for the same reason the effect canvas does:
// it describes an attempt in progress rather than the contents of the screen,
// and it has to survive both a repaint and -- for the port the adapter probe
// found -- the wait for an answer that may arrive after the first paint.
let adapter = null;        // what /api/adapter reported, null until it answers
let connecting = false;
let connectNote = "";      // the one calm sentence a failed attempt leaves behind
let paintHub = null;       // set while the view is mounted, so the button can repaint

async function startConnect() {
  if (connecting) return;
  connecting = true;
  connectNote = "";
  if (paintHub) paintHub();
  const r = await connectCar();
  connecting = false;
  adapter = r.adapter || adapter;
  connectNote = r.ok ? "" : r.message;
  if (paintHub) paintHub();
}

// What the sub-line under the car's name says when there is no car talking.
// The port goes here rather than on the button because in car mode the button
// is a target for a thumb at arm's length, and "Connect" is the whole of what
// it needs to say; the evidence that OmaCar has already found the adapter
// belongs in the line of prose next to it.
function offlineLine() {
  if (connecting) return "Connecting…";
  if (connectNote) return connectNote;
  if (adapter && adapter.warning) return adapter.warning;
  if (adapter && adapter.port) return "Not connected · " + adapter.port;
  if (adapter && adapter.known) return "Not connected — no adapter found";
  return "Not connected";
}

// ------------------------------------------------------------ in-place DOM

// Assign only on a real change. Writing the same string back to textContent is
// not free -- it replaces the text node, which is enough to drop a selection
// and to dirty layout for the whole tile -- and at four writes a second across
// forty nodes that adds up to a screen that never settles.
function text(el, s) { if (el && el.textContent !== s) el.textContent = s; }

// A touchscreen returns no tactile confirmation, so a tap that produces no
// visible change gets tapped again -- which on a navigation tile means arriving
// somewhere and immediately being taken there a second time. app.css does have
// a :active rule, but it is a 1.5% scale: at arm's length in a moving car that
// is invisible. Colour and border survive that distance, so the press is drawn
// here, in JS, stepping the surface one level exactly the way the stylesheet's
// own hover does. It is inline because app.css belongs to another pass tonight;
// when the density token layer lands this should move into a :active rule and
// this function should shrink to the touch-action lines.
//
// touch-action: manipulation is the other half of "immediate": without it
// Chromium holds every tap for ~300ms in case a second one arrives and means
// double-tap-to-zoom, which reads as the app being slow to respond.
function pressable(el) {
  const down = () => {
    el.style.background = "var(--raise)";
    el.style.borderColor = "var(--bright-2)";
  };
  const up = () => { el.style.background = ""; el.style.borderColor = ""; };
  el.addEventListener("pointerdown", down);
  for (const e of ["pointerup", "pointercancel", "pointerleave"]) el.addEventListener(e, up);
  el.style.touchAction = "manipulation";
  el.style.webkitTapHighlightColor = "transparent";
  return el;
}

// Reconcile a keyed row of targets in place.
//
// Today both rows below are constant, so this runs once at build and then does
// nothing on every tick, which is the point: it costs a Map and a walk, and in
// exchange no future conditional entry (a plugin screen, an AI tile that hides
// itself when no model is configured) can reintroduce the teardown bug by
// accident. Nodes that are still wanted are moved, never rebuilt, so a tile
// that keeps its key keeps its identity.
function syncKeys(host, keys, make) {
  const have = new Map();
  for (const n of Array.from(host.children)) have.set(n.dataset.key, n);
  let i = 0;
  for (const k of keys) {
    let n = have.get(k);
    if (n) have.delete(k);
    else { n = make(k); n.dataset.key = k; }
    if (host.children[i] !== n) host.insertBefore(n, host.children[i] || null);
    i += 1;
  }
  for (const n of have.values()) n.remove();
}

// ------------------------------------------------------------------ mount

export default function hub(root) {
  fxHost = document.createElement("div");
  fxHost.className = "fx-host";
  root.appendChild(fxHost);
  fxMode = null;
  redrawFx();

  const paint = build(root);
  paintHub = paint;

  // Ask which port is there once per mount. Nothing depends on the answer --
  // the button works without it -- so a failure is simply a quieter sub-line.
  adapterState().then((a) => { adapter = a; if (paintHub && !store.connected) paintHub(); });

  // Subscribe ONCE, outside the paint. The first version of this re-entered
  // hub() from inside its own listener, so every repaint added another listener
  // to the store and the old ones were never removed -- by the end of a drive
  // that is hundreds of handlers all redrawing the same screen. It is worth
  // saying again now that the paint is cheap: cheap is not the same as free,
  // and a leaked subscription is unbounded.
  const offLive = store.on("live", paint);
  const offCar = store.on("car", paint);
  const offRadio = radio.on(paint);
  paint();
  return () => {
    offLive(); offCar(); offRadio();
    paintHub = null;
    if (fxStop) { fxStop(); fxStop = null; }
    if (fxHost) { fxHost.remove(); fxHost = null; }
    fxMode = null;
  };
}

// Build the whole screen once and return the function that updates it. Every
// node the update touches is captured in this closure, so the update never has
// to search the document for something it might not find.
function build(root) {
  const title = h("div.hub-title");
  const sub = h("div.hub-sub");

  // The Connect button is built whether or not the car is connected, and hidden
  // rather than removed, with its box reserved wide enough for the longest label
  // it can carry. An ELM327 that re-links at a traffic light used to insert this
  // button into the row and shove the two beside it ~140px sideways -- so a
  // thumb already travelling towards "Night · red" landed on "Workshop" and left
  // car mode. Nothing in this header may appear, disappear or resize; the state
  // changes, the geometry does not.
  //
  // A cockpit is read-only by design, so it gets the state and no button at all.
  // That is decided once, at load, from a constant, so it cannot move anything
  // later either.
  const connect = readOnly ? null : pressable(h("button.hub-exit", {
    onclick: startConnect,
    style: { minWidth: "10.5rem" },
    title: "Start talking to the car",
  }, "Connect"));

  const look = pressable(h("button.hub-exit.hub-look", {
    onclick: () => { saveLook(nextLook(savedLook())); redrawFx(); },
    style: { minWidth: "9.5rem" },
    title: lookById(savedLook()).note + "  (tap to change)",
  }, effectLabel()));

  const exit = pressable(h("button.hub-exit", {
    onclick: () => { location.hash = "#dash"; },
    title: "Leave car mode",
  }, "Workshop"));

  // 12px is the floor between two targets that do different things; 8px was
  // close enough that a near-miss on Connect reached the look picker.
  const head = h("div.hub-head",
    h("div", title, sub),
    h("div.row", { style: { gap: "12px" } }, connect, look, exit));

  // ---- vitals ----
  const vitalCells = VITALS.map((v) => {
    const value = h("span");
    const unit = h("span.hub-vital-u");
    return {
      spec: v, value, unit,
      node: h("div.hub-vital",
        h("div.hub-vital-v", value, unit),
        h("div.hub-vital-k", { style: { fontSize: "1rem" } }, v.label)),
    };
  });
  const vitals = h("div.hub-vitals", ...vitalCells.map((c) => c.node));

  // ---- primary tiles ----
  //
  // Sizing, until the density tokens land in app.css.
  //
  // The old floor was 158px, which fitted all six tiles on ONE row inside the
  // 1180px reading column: a strip of small landscape tiles at the exact
  // resolution this screen exists for, which is the desktop layout it was
  // meant to replace. 300px is chosen against the arithmetic of that column --
  // three tracks plus two 16px gaps fit in 1176px and four do not -- so the
  // reference tablet gets the 3x2 slab of thumb targets, and it does so
  // without a width breakpoint, which on a 1368px-wide finger-driven machine
  // would answer "desktop" and undo itself.
  //
  // The floor is min(50% - 8px, 300px) rather than a flat 300px so a narrow
  // window still gets two columns instead of a single tall stack: the 50% term
  // only ever wins below ~600px, where 300px could not fit two tracks anyway.
  const tiles = new Map();
  const grid = h("div.hub-grid", {
    style: { gridTemplateColumns: "repeat(auto-fit, minmax(min(50% - 8px, 300px), 1fr))", gap: "16px" },
  });

  const makeTile = (id) => {
    const t = PRIMARY.find((p) => p.id === id);
    const label = h("div.hub-label", { style: { fontSize: "1.42rem" } }, t.label);
    const hint = h("div.hub-hint", { style: { fontSize: "1.02rem" } }, t.hint);
    const badge = h("div.hub-badge", { hidden: true });
    const node = pressable(h("button.hub-tile", {
      onclick: () => { location.hash = "#" + t.id; },
      // clamp rather than a flat height: 17vh is the slab on the 912px-tall
      // tablet this is for, and the floor keeps it usable on a laptop lid where
      // the same 17vh would be a strip. The floor is stated as two tap targets
      // rather than as a number so it follows the density switch instead of
      // having to be found and changed a second time.
      style: { minHeight: "clamp(calc(var(--tap-lg) * 2), 17vh, 168px)" },
    }));
    node.appendChild(h("div.hub-ico"));
    node.firstChild.appendChild(svg(ICONS[t.id], 38));
    node.appendChild(label);
    node.appendChild(hint);
    node.appendChild(badge);
    tiles.set(id, { spec: t, node, badge });
    return node;
  };

  // ---- secondary chips ----
  const more = h("div.hub-more", { style: { gap: "12px" } });
  const makeChip = (id) => {
    const t = SECONDARY.find((s) => s.id === id);
    // --tap-lg rather than the stylesheet's flat 52px: it is above the 48px
    // floor with room for an unsteady hand, and it grows with the density
    // switch when the machine says it is being driven by a finger.
    const node = pressable(h("button.hub-chip", {
      onclick: () => { location.hash = "#" + t.id; },
      style: { minHeight: "var(--tap-lg)", fontSize: "1.08rem" },
    }, t.label));
    return node;
  };

  // ---- the radio ----
  //
  // radio.js owns this markup; the hub only writes text into it. It is built
  // once for the same reason everything else here is, and more sharply: the
  // volume control is a RANGE INPUT, so a drag on it sets radio.volume, which
  // emits, which used to rebuild the player and destroy the slider under the
  // finger that was dragging it. Reaching in by class name is a coupling, and
  // it is the smaller of the two evils; the alternative is a player that cannot
  // be adjusted while it is playing.
  const player = radioPlayer();
  const rPlay = player.querySelector(".radio-play");
  const rName = player.querySelector(".radio-name");
  const rStatus = player.querySelector(".radio-status");
  const rVol = player.querySelector(".radio-vol");
  const rLine = document.createTextNode("");
  const rCount = h("span.radio-count");
  if (rStatus) {
    while (rStatus.firstChild) rStatus.removeChild(rStatus.firstChild);
    rStatus.appendChild(rLine);
    rStatus.appendChild(rCount);
  }
  if (rPlay) pressable(rPlay);
  if (rVol) rVol.style.touchAction = "pan-y";

  root.appendChild(h("div.hub",
    head,
    vitals,
    // Learn mode is a per-browser setting reached through its own screen, so it
    // cannot change while this view is mounted; it is safe to resolve once.
    explain(h, "hub"),
    player,
    grid,
    more));

  function paint() {
    const car = store.car;
    // A note left by a failed attempt describes a moment, not the car. Once
    // something is answering it has stopped being true, so it does not survive
    // to be shown again after the next drop-out.
    if (store.connected) connectNote = "";

    text(title, car && car.name ? car.name : "OmaCar");
    text(sub, store.connected ? (store.sample.protocol || "connected") : offlineLine());

    if (connect) {
      // Hidden, not removed: see the note where it is built.
      connect.style.visibility = store.connected ? "hidden" : "visible";
      connect.disabled = connecting || store.connected;
      text(connect, connecting ? "Connecting…" : "Connect");
    }
    text(look, effectLabel());
    const note = lookById(savedLook()).note + "  (tap to change)";
    if (look.title !== note) look.title = note;

    const values = store.values || {};
    const c = car || {};
    for (const cell of vitalCells) {
      text(cell.value, cell.spec.value(values, c));
      const u = cell.spec.unit();
      text(cell.unit, u);
      cell.unit.hidden = !u;
    }

    syncKeys(grid, PRIMARY.map((t) => t.id), makeTile);
    // Driven from PRIMARY rather than from the tiles map, so a tile that ever
    // stops being wanted is not still being written to after it has left the
    // document.
    for (const t of PRIMARY) {
      const { spec, node, badge } = tiles.get(t.id);
      const b = badgeFor(spec.id, car);
      if (b) {
        badge.hidden = false;
        badge.className = "hub-badge tone-" + b.tone;
        text(badge, b.text);
      } else {
        badge.hidden = true;
      }
      // The badge is the half of the tile a screen reader cannot see, so the
      // label carries it too -- and only when it has actually changed, because
      // rewriting aria-label four times a second makes a screen reader announce
      // the tile over and over.
      const al = spec.label + (b ? ", " + b.text : "");
      if (node.getAttribute("aria-label") !== al) node.setAttribute("aria-label", al);
    }

    syncKeys(more, SECONDARY.map((t) => t.id), makeChip);

    // ---- the radio, in place ----
    if (rPlay) {
      text(rPlay, radio.playing ? "❚❚" : "▶");
      rPlay.setAttribute("aria-label", radio.playing ? "Pause radio" : "Play radio");
    }
    const np = radio.now;
    text(rName, np.title || "Omarchy Radio");
    const status = radio.failed ? "offline"
      : radio.loading ? "connecting…"
      : radio.playing ? "live" : "paused";
    const line = np.artist ? np.artist : status;
    if (rLine.data !== line) rLine.data = line;
    if (rStatus) rStatus.classList.toggle("bad", radio.failed);
    text(rCount, np.listeners != null ? `${np.listeners} listening` : "");
    rCount.hidden = np.listeners == null;
    // Never write over a slider somebody has hold of: the value it would be
    // given is the value they just set, and the write moves the thumb out from
    // under the finger mid-drag.
    if (rVol && document.activeElement !== rVol) rVol.value = String(Math.round(radio.volume * 100));
  }

  return paint;
}
