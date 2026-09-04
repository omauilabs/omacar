// The car hub -- the screen you navigate from while sitting in the driver's seat.
//
// The workshop layout (a 44px rail of small icons down the left) is right at a
// desk with a mouse and wrong in a moving car: the targets are too small to hit
// without looking, and looking is the thing you cannot spend. This is the same
// application with the same data and a different set of physical assumptions --
// big targets, few of them, high contrast, and every tile carrying the one
// number that tells you whether it is worth opening at all.
//
// It is not a simplified OmaCar. Nothing is removed; the secondary row reaches
// every view the rail does. It is the same app laid out for a different hand.

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

const SECONDARY = [
  { id: "dash", label: "Home" }, { id: "live", label: "Cluster" },
  { id: "tests", label: "Tests" }, { id: "concerns", label: "Trends" },
  { id: "service", label: "Service" }, { id: "history", label: "Log" },
  { id: "garage", label: "Garage" }, { id: "learn", label: "Learn" },
  { id: "resets", label: "Resets" }, { id: "replay", label: "Replay" },
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
// and sized so they can be read without leaning in.
function vitals() {
  const v = store.values || {};
  const car = store.car || {};
  const items = [
    ["Speed", v.SPEED != null ? Math.round(v.SPEED * U.units.km) : "--", U.units.speed],
    ["RPM", v.RPM != null ? Math.round(v.RPM) : "--", ""],
    ["Coolant", v.COOLANT_TEMP != null ? Math.round(v.COOLANT_TEMP) : "--", "°"],
    ["Trip", car.trip_km != null ? dist(car.trip_km, false) : "--", U.units.dist],
  ];
  return h("div.hub-vitals",
    ...items.map(([label, val, unit]) =>
      h("div.hub-vital",
        h("div.hub-vital-v", String(val), unit ? h("span.hub-vital-u", unit) : null),
        h("div.hub-vital-k", label))));
}

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
// the hub rebuilds itself on every live sample, so anything held inside draw()
// would be thrown away several times a second -- including, while somebody is
// looking at it, the word "Connecting".
let adapter = null;        // what /api/adapter reported, null until it answers
let connecting = false;
let connectNote = "";      // the one calm sentence a failed attempt leaves behind
let redrawHub = null;      // set while the view is mounted, so the button can repaint

async function startConnect() {
  if (connecting) return;
  connecting = true;
  connectNote = "";
  if (redrawHub) redrawHub();
  const r = await connectCar();
  connecting = false;
  adapter = r.adapter || adapter;
  connectNote = r.ok ? "" : r.message;
  if (redrawHub) redrawHub();
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

export default function hub(root) {
  // Subscribe ONCE, outside draw(). The first version of this re-entered hub()
  // from inside its own listener, so every repaint added another listener to
  // the store and the old ones were never removed -- by the end of a drive
  // that is hundreds of handlers all redrawing the same screen.
  const redraw = () => {
    const top = root.scrollTop;
    // Everything EXCEPT the effect canvas. The previous version cleared every
    // child, and since the hub repaints on each live sample the canvas was
    // destroyed within a second of being created -- the effect appeared to do
    // nothing at all, because it was being deleted rather than never starting.
    for (const n of Array.from(root.children)) if (n !== fxHost) n.remove();
    draw(root);
    root.scrollTop = top;
  };
  redrawHub = redraw;
  // Ask which port is there once per mount. Nothing depends on the answer --
  // the button works without it -- so a failure is simply a quieter sub-line.
  adapterState().then((a) => { adapter = a; if (redrawHub && !store.connected) redrawHub(); });
  fxHost = document.createElement("div");
  fxHost.className = "fx-host";
  root.appendChild(fxHost);
  fxMode = null;
  redrawFx();

  const offLive = store.on("live", redraw);
  const offCar = store.on("car", redraw);
  const offRadio = radio.on(redraw);
  draw(root);
  return () => {
    offLive(); offCar(); offRadio();
    redrawHub = null;
    if (fxStop) { fxStop(); fxStop = null; }
    if (fxHost) { fxHost.remove(); fxHost = null; }
    fxMode = null;
  };
}

function draw(root) {
  const car = store.car;
  // A note left by a failed attempt describes a moment, not the car. Once
  // something is answering it has stopped being true, so it does not survive
  // to be shown again after the next drop-out.
  if (store.connected) connectNote = "";

  root.appendChild(h("div.hub",
    h("div.hub-head",
      h("div",
        h("div.hub-title", car && car.name ? car.name : "OmaCar"),
        h("div.hub-sub", store.connected
          ? (store.sample.protocol || "connected")
          : offlineLine())),
      h("div.row", { style: { gap: "8px" } },
        // A cockpit is read-only by design, so it gets the state and no button.
        store.connected || readOnly ? null : h("button.hub-exit", {
          onclick: startConnect,
          disabled: connecting,
          title: "Start talking to the car",
        }, connecting ? "Connecting…" : "Connect"),
        h("button.hub-exit.hub-look", {
          onclick: () => { saveLook(nextLook(savedLook())); redrawFx(); },
          title: lookById(savedLook()).note + "  (tap to change)",
        }, effectLabel()),
        h("button.hub-exit", {
          onclick: () => { location.hash = "#dash"; },
          title: "Leave car mode",
        }, "Workshop"))),

    vitals(),

    explain(h, "hub"),

    radioPlayer(),

    h("div.hub-grid",
      ...PRIMARY.map((t) => {
        const b = badgeFor(t.id, car);
        const tile = h("button.hub-tile", {
          onclick: () => { location.hash = "#" + t.id; },
          "aria-label": t.label + (b ? ", " + b.text : ""),
        });
        tile.appendChild(h("div.hub-ico"));
        tile.firstChild.appendChild(svg(ICONS[t.id], 34));
        tile.appendChild(h("div.hub-label", t.label));
        tile.appendChild(h("div.hub-hint", t.hint));
        if (b) tile.appendChild(h("div.hub-badge.tone-" + b.tone, b.text));
        return tile;
      })),

    h("div.hub-more",
      ...SECONDARY.map((t) =>
        h("button.hub-chip", { onclick: () => { location.hash = "#" + t.id; } }, t.label)))));
}
