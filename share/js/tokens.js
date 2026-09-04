// The palette, read off the live document rather than declared here.
//
// Every colour in this application belongs to the Omarchy theme. main.js polls
// the theme and writes it onto :root as custom properties, and a chart that
// held its own hex values would be the one thing on the screen that did not
// change when the desktop did — which is exactly the "guest in its own
// clothes" problem app.css opens by describing.
//
// There are two ways to use a token and they are not interchangeable:
//
//   paint(name)   gives you `var(--ok)`, for anything the browser will resolve
//                 for you. Use it for SVG colour, and set it with .style(),
//                 never with .attr(): var() does not resolve inside an SVG
//                 presentation attribute, so `.attr("fill", "var(--ok)")`
//                 paints nothing at all and fails silently. Colour set this
//                 way follows a theme change, a night-red look and the print
//                 stylesheet with no repaint, because the browser re-resolves
//                 it — which is the whole reason charts here are SVG.
//
//   token(name)   resolves the value NOW, to a string. Only for code that
//                 cannot hand a var() to the browser: canvas (charts.js), or a
//                 measurement. A value read this way is a snapshot and goes
//                 stale the moment the theme moves.
//
// Nothing here caches. getComputedStyle on one element, a handful of times per
// chart, is not a cost worth a cache that would have to be invalidated by a
// theme poll it cannot see.

const root = () => document.documentElement;

// The tokens app.css declares on :root, plus the ones lib/theme.py adds when a
// theme is applied. Anything not in this file's vocabulary is a typo, and a
// typo in a var() name is invisible — the mark simply does not paint.
export const TONES = {
  ok: "--ok", warn: "--warn", bad: "--bad", info: "--info", ai: "--ai",
};

// Ordered for charts that need several distinguishable series at once. Blue
// first because it carries no verdict: a chart of five channels is not saying
// four of them are wrong.
//
// --accent is deliberately absent. lib/theme.py emits it, app.css does not
// declare it, so it exists only while a theme is applied and paints nothing on
// a bare checkout. If you want it, give it a fallback: paint("--accent",
// token("--info")).
export const SERIES = ["--info", "--ok", "--warn", "--ai", "--bad"];

export function token(name, fallback = "") {
  const v = getComputedStyle(root()).getPropertyValue(name).trim();
  return v || fallback;
}

export function paint(name, fallback = "") {
  return fallback ? `var(${name}, ${fallback})` : `var(${name})`;
}

// A tone word — the "ok" / "warn" / "bad" vocabulary lifeTone() and sevTone()
// already speak in core.js — as something you can paint with. An unknown or
// empty tone is not an error: it means "no verdict", which is --dim.
export function tonePaint(word) {
  return paint(TONES[word] || "--dim");
}

// The root font size in pixels, for the one thing rem cannot do: measuring.
// Chart text is sized in rem so it follows the browser's font setting and any
// change to the app's own floor, which means the code that decides whether a
// label fits has to ask how large a rem currently is rather than assume 15 or
// 16.
export function rootPx() {
  return parseFloat(getComputedStyle(root()).fontSize) || 16;
}

// Light or dark, as the theme sees it. Not cosmetic: a 10% amber wash reads on
// a near-black panel and disappears on white, so anything translucent has to
// know which way round it is.
export function mode() {
  return root().dataset.mode === "light" ? "light" : "dark";
}
