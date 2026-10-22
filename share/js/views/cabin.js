// The Cabin tab's overview: the things in and around the car that are not the
// car. The fridge is live; Air, Sound and Garage each say plainly what they
// need until their own screens land (doc/environment.md, doc/eq.md,
// doc/design/2026-10-17-garage.md), and never draw a number they do not have.
//
// The fridge gets the hero slot because it is the one with something to say
// right now; a tile is a door to its own screen.

import { h, icon, temp, withToken } from "../core.js";
import { ICONS } from "../icons.js";
import { fridgeCard } from "../fridgecard.js";
import { BABY } from "../childalert.js";
import { valet } from "../valet.js";
import { turnOn as valetOn, leave as valetLeave } from "../valetui.js";

// As Home does it: the hash is the router, so a view never imports main.js.
const goto = (id) => { location.hash = "#" + id; };

// Each tile: what it is, the one line it can honestly say today, and where a
// tap goes (null: nowhere yet, so it is not a button).
const TILES = [
  { id: "air", icon: ICONS.air, title: "Air",
    line: "Cabin and outside: temperature, humidity, CO₂ and particulates.",
    need: "Open the last 24 hours", go: "environment" },
  { id: "sound", icon: ICONS.sound, title: "Sound",
    line: "The equalizer for the AUX output, and Calibrate.",
    need: "Open the equalizer", go: "eq" },
  { id: "child", icon: BABY, title: "Child seat",
    line: "Alerts you if the car is switched off with a child in the seat.",
    need: "Open the child alert", go: "child-alert" },
  { id: "garage", icon: ICONS.garage, title: "Garage",
    line: "Open and close the garage door from the dash, locally.",
    need: "Needs a ratgdo32 or Konnected blaQ", go: null },
  // Valet mode's one tap. `act` is a tile action rather than a screen; the
  // words come from valet.js's own state, not from a feed.
  { id: "valet", icon: ICONS.valet, title: "Valet",
    line: "Hand the car over: hides your history and documents, locks settings, logs how it is driven.",
    need: "Turn on (parked only)", go: null, act: () => (valet.on ? valetLeave() : valetOn()) },
];

// What a live tile says now, from its feature's own API. Pure, so it is
// tested (test/js/cabin.test.js). null: say the tile's standing line instead.
// `t` formats a Celsius temperature in the user's units.
export function tileStatus(id, d, t = (c) => `${Math.round(c)}°`) {
  if (!d) return null;
  if (id === "air") {
    if (!d.present) return null;
    const cab = d.cabin || {}, out = d.outside || {};
    const cv = cab.values || {}, ov = out.values || {};
    const parts = [];
    if (cab.state !== "absent" && typeof cv.t_c === "number") parts.push(`Cabin ${t(cv.t_c)}`);
    if (cab.state !== "absent" && typeof cv.co2 === "number") parts.push(`CO₂ ${Math.round(cv.co2).toLocaleString("en-US")}`);
    if (out.state !== "absent" && typeof ov.t_c === "number") parts.push(`Outside ${t(ov.t_c)}`);
    if (!parts.length) return null;
    const sim = d.source === "sim";
    const stale = cab.state === "stale" || out.state === "stale";
    return { line: parts.join(" · "), need: sim ? "Simulated" : stale ? "Last reading" : "Live",
             tone: sim ? "warn" : stale ? "" : "ok" };
  }
  if (id === "sound") {
    const st = d.state || {};
    if (!d.present) return { line: st.enabled ? `On · ${st.preset || "Custom"}` : "Bypassed",
                             need: "Not applied here: no PipeWire", tone: "" };
    if (!st.enabled) return { line: "Bypassed", need: "Flat to the AUX output", tone: "" };
    return { line: `On · ${st.preset || "Custom"}`, need: d.applied ? "Shaping the AUX output" : "Starting…",
             tone: d.applied ? "ok" : "" };
  }
  if (id === "child") {
    // Never a reassuring word it cannot back: a loop that is not running says so.
    if (!d.running) return { line: "Not watching", need: "The child-alert service is not running", tone: "warn" };
    if (!d.enabled) return { line: "Off", need: "Switched off in its settings", tone: "" };
    const sim = d.source === "sim";
    if (["alert", "snoozed"].includes(d.phase)) return { line: "Alerting now", need: sim ? "Simulated" : "Car off, seat occupied", tone: "warn" };
    if (d.occupied) return { line: "Occupied", need: sim ? "Simulated" : "Armed: watching for the switch-off", tone: sim ? "warn" : "ok" };
    return { line: "Nothing seen", need: d.config && d.config.camera ? "Watching the seat" : "No camera: Child aboard arms it", tone: "" };
  }
  return null;
}

const FEEDS = { air: "/api/env", sound: "/api/eq", child: "/api/child-alert" };

function tile(t) {
  const line = h("p.cb-tile-line", t.line);
  const needT = h("span", t.need);
  const need = h("div.cb-tile-need", h("span.cb-lamp", { "aria-hidden": "true" }), needT);
  const body = [
    h("div.cb-tile-head", h("span.cb-tile-ico", icon(t.icon, 22)), h("span.cb-tile-t", t.title),
      t.go ? h("span.cb-chev", { "aria-hidden": "true" }, icon(ICONS.chevron, 18)) : null),
    line, need,
  ];
  const node = (t.go || t.act)
    ? h("button.card.cb-tile", { type: "button", data: { tile: t.id }, onclick: () => (t.act ? t.act() : goto(t.go)) }, ...body)
    : h("div.card.cb-tile", { data: { tile: t.id, state: "pending" } }, ...body);
  const paint = (d) => {
    if (t.id === "valet") {
      const on = valet.on;
      line.textContent = on ? "On. Your history, documents and settings are out of sight, and the drive is being logged."
                            : t.line;
      needT.textContent = on ? (valet.logging ? "Tap to turn off (PIN)" : "On, but the log is not running")
                             : t.need;
      node.dataset.tone = on ? (valet.logging ? "ok" : "warn") : "";
      return;
    }
    const s = tileStatus(t.id, d, (c) => temp(c));
    line.textContent = s ? s.line : t.line;
    needT.textContent = s ? s.need : t.need;
    node.dataset.tone = s ? s.tone : "";
  };
  return { node, feed: FEEDS[t.id], paint };
}

export default function cabin(root) {
  const fridgeSlot = h("div.card.hc.cb-fridge", {
    role: "button", tabindex: "0", "aria-label": "Open the fridge",
  });
  const card = fridgeCard(fridgeSlot);
  // The card's own controls take their taps; anywhere else on it opens the
  // full screen.
  fridgeSlot.addEventListener("click", (e) => {
    if (!e.target.closest("button")) goto("fridge");
  });
  fridgeSlot.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target === fridgeSlot) { e.preventDefault(); goto("fridge"); }
  });
  // Hidden in valet mode: the garage door is the owner's, and so is its tile.
  const tiles = TILES.filter((t) => !(valet.on && t.id === "garage")).map(tile);
  root.appendChild(h("div.cabin", fridgeSlot, h("div.cb-tiles", ...tiles.map((x) => x.node))));
  // Each live tile reads its own feature's API every few seconds; a feed that
  // fails keeps the tile's standing words rather than showing an error here.
  let timer = null, gone = false;
  const vt = tiles.find((x) => x.node.dataset.tile === "valet");
  if (vt) vt.paint(null);
  const tick = async () => {
    if (vt) vt.paint(null);
    await Promise.all(tiles.filter((x) => x.feed).map(async (x) => {
      try {
        const r = await fetch(withToken(x.feed), { cache: "no-store" });
        x.paint(r.ok ? await r.json() : null);
      } catch { x.paint(null); }
    }));
    if (!gone) timer = setTimeout(tick, 5000);
  };
  tick();
  return () => { gone = true; clearTimeout(timer); card.destroy(); };
}
