// Looks — palette and background effect together, as one choice.
//
// These were nearly built as two controls: a theme picker and an effects
// picker. That would have been worse. Nobody wants the matrix rain over the
// normal palette, and nobody driving at night wants to set a red palette and
// then separately remember to turn the animation off. The combinations that
// make sense are few, and each has a name and an occasion.
//
// So: one control, five looks, each coherent.

import { mountEffect } from "./effects.js";

const KEY = "omacar.look";

export const LOOKS = [
  { id: "normal", label: "Normal", effect: "off",
    note: "Default palette, no background." },
  { id: "green", label: "Matrix", effect: "matrix",
    note: "Green palette with the rain behind it." },
  { id: "aurora", label: "Aurora", effect: "aurora",
    note: "Default palette, a slow colour drift behind everything." },
  // The only look that is not dark, and the only one for a condition the
  // desktop theme cannot know about: a glossy panel in direct sun, where every
  // other look here is a mirror.
  { id: "day", label: "Daylight", effect: "off",
    note: "Near-black on white, for direct sunlight on the windscreen." },
  { id: "dim", label: "Night · dim", effect: "off",
    note: "Everything pulled down. For a lit cabin after dark." },
  { id: "red", label: "Night · red", effect: "off",
    note: "Red only, to protect dark adaptation on a long drive." },
];

export function savedLook() {
  try {
    const v = localStorage.getItem(KEY);
    return LOOKS.some((l) => l.id === v) ? v : "normal";
  } catch { return "normal"; }
}

export function saveLook(id) {
  try { localStorage.setItem(KEY, id); } catch { /* private mode */ }
}

export function lookById(id) {
  return LOOKS.find((l) => l.id === id) || LOOKS[0];
}

// The palette is applied to the document root, NOT to a view.
//
// A look has to survive navigation -- the whole point of night red is that it
// stays red while you move between screens -- so it is set once, at the top,
// and never touched by a view mount. Only the background canvas, which is
// genuinely per-view, is mounted and torn down with the hub.
export function applyLook(id) {
  // documentElement, not #app. html and body read --ground from :root, so a
  // look set on #app left the page behind the app unthemed, and anything
  // appended to document.body (the rail's overflow menu) missed it entirely.
  const root = document.documentElement;
  const look = lookById(id);
  if (look.id === "normal") root.removeAttribute("data-look");
  else root.setAttribute("data-look", look.id);
}

export function nextLook(id) {
  const i = LOOKS.findIndex((l) => l.id === id);
  return LOOKS[(i + 1) % LOOKS.length].id;
}

// Effect lifecycle, kept here so the hub does not have to know which looks
// carry an animation and which do not.
export function mountLookEffect(host, id) {
  return mountEffect(host, lookById(id).effect);
}
