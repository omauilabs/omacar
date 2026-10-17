// The Cabin tab's overview: the things in and around the car that are not the
// car. The fridge is live; Air, Sound and Garage each say plainly what they
// need until their own screens land (doc/environment.md, doc/eq.md,
// doc/design/2026-10-17-garage.md), and never draw a number they do not have.
//
// The fridge gets the hero slot because it is the one with something to say
// right now; a tile is a door to its own screen.

import { h, icon } from "../core.js";
import { ICONS } from "../icons.js";
import { fridgeCard } from "../fridgecard.js";

// As Home does it: the hash is the router, so a view never imports main.js.
const goto = (id) => { location.hash = "#" + id; };

// Each tile: what it is, the one line it can honestly say today, and where a
// tap goes (null: nowhere yet, so it is not a button).
const TILES = [
  { id: "air", icon: ICONS.air, title: "Air",
    line: "Cabin and outside: temperature, humidity, CO₂ and particulates.",
    need: "Needs the environment sensors", go: null },
  { id: "sound", icon: ICONS.sound, title: "Sound",
    line: "An equalizer for the AUX output, with automatic calibration.",
    need: "Arriving with the equalizer", go: null },
  { id: "garage", icon: ICONS.garage, title: "Garage",
    line: "Open and close the garage door from the dash, locally.",
    need: "Needs a ratgdo32 or Konnected blaQ", go: null },
];

function tile(t) {
  const body = [
    h("div.cb-tile-head", h("span.cb-tile-ico", icon(t.icon, 22)), h("span.cb-tile-t", t.title)),
    h("p.cb-tile-line", t.line),
    h("div.cb-tile-need", h("span.cb-lamp", { "aria-hidden": "true" }), t.need),
  ];
  return t.go
    ? h("button.card.cb-tile", { type: "button", data: { tile: t.id }, onclick: () => goto(t.go) }, ...body)
    : h("div.card.cb-tile", { data: { tile: t.id, state: "pending" } }, ...body);
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
  root.appendChild(h("div.cabin", fridgeSlot, h("div.cb-tiles", ...TILES.map(tile))));
  return () => card.destroy();
}
