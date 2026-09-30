// CarPlay and Android Auto: the parts both share.
//
// The meetup demo (doc/design/2026-09-30-meetup-demo.md §4, Task 5 of the
// plan) opens either from Home's Phone integration card as a full-screen
// takeover, each drawn fresh in its platform's general style. This file holds
// what the two have in common: the takeover itself, the screen router, the
// clock, the glyphs, the map pane and its turn card, and the binding to the
// one radio player.
//
// NEITHER IS EITHER COMPANY'S ARTWORK. Every glyph below is a path written
// here; an app tile is a colour and one of those glyphs; the album art is the
// station's name set in type. The platforms' names appear only in their own
// status areas. Nothing is fetched: there is no picture here to lose, and none
// that belongs to somebody else.
//
// Everything a screen needs from outside is injected (`deps`): the radio, the
// map and the way back. So this file never reaches for the car, the speakers
// or the network, and the tests run it with fakes.

import { h, clear, store } from "../../js/core.js";

// The class on <body> while a projection is up: projection.css hides the
// app's own bars under it.
export const TAKEOVER = "proj-on";

export function withDeps(deps = {}) {
  return {
    radio: deps.radio || null,
    mountMap: deps.mountMap || null,
    back: deps.back || (() => { location.hash = "#home"; }),
    now: deps.now || (() => Date.now()),
  };
}

// Task 3's turn banner, when it is here, so the distances match the OmaCar
// Navigation screen word for word. It may not be (the tasks were built in
// parallel), so it is imported lazily and everything below has its own.
let nav = null;
import("./nav.js").then((m) => { nav = m; }).catch(() => { /* our own banner, below */ });

// ------------------------------------------------------------------ glyphs
const NS = "http://www.w3.org/2000/svg";
const r2 = (n) => Math.round(n * 100) / 100;

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
const F = (d, extra) => ["path", Object.assign({ d, fill: "currentColor" }, extra)];
const S = (d, w = 2, extra) => ["path", Object.assign({
  d, fill: "none", stroke: "currentColor", "stroke-width": w,
  "stroke-linecap": "round", "stroke-linejoin": "round" }, extra)];
const C = (cx, cy, r, extra) => ["circle", Object.assign({ cx, cy, r, fill: "currentColor" }, extra)];
const R = (x, y, width, height, rx) => ["rect", { x, y, width, height, rx, fill: "currentColor" }];
const E = (cx, cy, rx, ry) => ["ellipse", { cx, cy, rx, ry, fill: "currentColor" }];
const MIRROR = { transform: "matrix(-1 0 0 1 24 0)" };

// A gear, generated rather than traced: `teeth` trapezoids on a ring.
function gear(cx, cy, R0, r0, teeth, hole) {
  const step = (Math.PI * 2) / teeth;
  const at = (rad, a) => `${r2(cx + rad * Math.sin(a))} ${r2(cy - rad * Math.cos(a))}`;
  let d = "";
  for (let i = 0; i < teeth; i++) {
    const a = i * step;
    d += `${i ? "L" : "M"}${at(r0, a - step * 0.28)}L${at(R0, a - step * 0.16)}`
       + `L${at(R0, a + step * 0.16)}L${at(r0, a + step * 0.28)}`
       + `A${r0} ${r0} 0 0 1 ${at(r0, a + step * 0.72)}`;
  }
  return `${d}ZM${cx + hole} ${cy}A${hole} ${hole} 0 1 0 ${cx - hole} ${cy}A${hole} ${hole} 0 1 0 ${cx + hole} ${cy}Z`;
}

// A 3x3 block, of squares or of dots.
const grid3 = (make) => [0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => make(3.2 + c * 6.4, 3.2 + r * 6.4)));

const PLAY = "M8 5.4v13.2c0 .8.9 1.3 1.6.8l9.8-6.6c.6-.4.6-1.2 0-1.6L9.6 4.6C8.9 4.1 8 4.6 8 5.4z";
const SKIP = "M2.2 6.8v10.4c0 .8.9 1.2 1.5.8l7.3-4.9v4.1c0 .8.9 1.2 1.5.8l8.6-5.4c.5-.4.5-1.1 0-1.4L12.5 5.8c-.6-.4-1.5 0-1.5.8v4.1L3.7 6c-.6-.4-1.5 0-1.5.8z";
const STEP = "M4.5 6.4v11.2c0 .8.9 1.3 1.6.8l8.4-5.6c.6-.4.6-1.2 0-1.6L6.1 5.6c-.7-.5-1.6 0-1.6.8z";
const HANDSET = "M8.2 3.6l1.9 3.8c.3.6.1 1.3-.4 1.7l-1.3 1c1 2.2 3.3 4.5 5.5 5.5l1-1.3c.4-.5 1.1-.7 1.7-.4l3.8 1.9c.6.3.8 1 .6 1.6l-.6 1.7c-.4 1.1-1.5 1.8-2.7 1.6-7-1-13.4-7.4-14.4-14.4-.2-1.2.5-2.3 1.6-2.7l1.7-.6c.6-.2 1.3 0 1.6.6z";
const PIN = "M12 2.5a7 7 0 0 0-7 7c0 5.1 7 12 7 12s7-6.9 7-12a7 7 0 0 0-7-7zm0 4.3a2.7 2.7 0 1 1 0 5.4 2.7 2.7 0 0 1 0-5.4z";

const GLYPHS = {
  play: [F(PLAY)],
  pause: [R(6.2, 5, 4, 14, 1.2), R(13.8, 5, 4, 14, 1.2)],
  "skip-fwd": [F(SKIP)],
  "skip-back": [F(SKIP, MIRROR)],
  next: [F(STEP), R(16.5, 5.5, 2.6, 13, 1.3)],
  prev: [F(STEP, MIRROR), R(4.9, 5.5, 2.6, 13, 1.3)],
  note: [F("M8.2 6.4l10.6-2.7v3.1L8.2 9.5z"), R(8.2, 6.6, 1.9, 11, .6), R(16.9, 4, 1.9, 11, .6),
         E(6.6, 17.6, 2.8, 2.2), E(15.3, 15, 2.8, 2.2)],
  bars: [R(3.5, 10, 3.2, 10, 1.6), R(8.3, 4.5, 3.2, 15.5, 1.6), R(13.1, 8, 3.2, 12, 1.6), R(17.9, 12.5, 3.2, 7.5, 1.6)],
  handset: [F(HANDSET)],
  bubble: [F("M12 3.6c-5 0-9 3.3-9 7.4 0 2.4 1.4 4.5 3.5 5.8-.1 1.3-.7 2.6-1.8 3.6 2 0 3.8-.7 5.1-1.9.7.1 1.4.2 2.2.2 5 0 9-3.3 9-7.4S17 3.6 12 3.6z")],
  pin: [F(PIN, { "fill-rule": "evenodd" })],
  arrow: [F("M12 2.8l7.3 17.6c.2.5-.3 1-.8.7L12 17.4l-6.5 3.7c-.5.3-1-.2-.8-.7z")],
  podcast: [S("M8.4 15.3a5.1 5.1 0 1 1 7.2 0", 2.1), S("M5.7 18.1a8.9 8.9 0 1 1 12.6 0", 2.1),
            C(12, 11.1, 2.3), S("M12 14.4v6.6", 2.6)],
  calendar: [S("M5.5 5h13a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z"),
             S("M3.5 10h17"), S("M8 3v4"), S("M16 3v4"), R(7, 13, 3, 3, .6)],
  gear: [F(gear(12, 12, 10, 7.4, 8, 3.1), { "fill-rule": "evenodd" })],
  grid: grid3((x, y) => R(x - 2.3, y - 2.3, 4.6, 4.6, 1.2)),
  dots: grid3((x, y) => C(x, y, 2.1)),
  dash: [R(3, 4, 10.5, 16, 2.2), R(15, 4, 6, 7.2, 2), R(15, 12.8, 6, 7.2, 2)],
  back: [S("M15.5 4.5L8 12l7.5 7.5", 2.6)],
  chev: [S("M9 5l7 7-7 7", 2.2)],
  list: [S("M9 6.5h11M9 12h11M9 17.5h11"), C(4.6, 6.5, 1.4), C(4.6, 12, 1.4), C(4.6, 17.5, 1.4)],
  bell: [S("M6 17v-6a6 6 0 0 1 12 0v6l1.5 1.5h-15z"), S("M10 21h4")],
  mic: [F("M12 2.8a3.2 3.2 0 0 1 3.2 3.2v5a3.2 3.2 0 0 1-6.4 0V6A3.2 3.2 0 0 1 12 2.8z"),
        S("M5.8 11a6.2 6.2 0 0 0 12.4 0"), S("M12 17.4v3.6")],
  plus: [S("M12 5v14M5 12h14", 2.4)],
  minus: [S("M5 12h14", 2.4)],
  speaker: [F("M3.5 9.2h3.3L11.5 5v14l-4.7-4.2H3.5z"), S("M15 9a4.2 4.2 0 0 1 0 6"), S("M17.8 6.3a8 8 0 0 1 0 11.4")],
  close: [S("M6.5 6.5l11 11M17.5 6.5l-11 11", 2.4)],
  search: [S("M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z", 2.2), S("M15.5 15.5L20 20", 2.4)],
  route: [S("M6 19a2 2 0 1 0 0-.01M18 5a2 2 0 1 0 0-.01"), S("M8 19h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16")],
  compose: [S("M11 4.5H6.5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V13"),
            S("M17.3 3.7a1.9 1.9 0 0 1 2.7 2.7L12.5 14l-3.5.9.9-3.5z")],
  info: [S("M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z", 1.8), S("M12 11v5.5", 2.2), C(12, 7.8, 1.3)],
  house: [F("M12 3.5l8.5 7.3c.4.3.1 1-.4 1h-1.6V19a1 1 0 0 1-1 1h-3.3v-5H9.8v5H6.5a1 1 0 0 1-1-1v-7.2H3.9c-.5 0-.8-.7-.4-1z")],
  case: [F("M9 4.5h6A1.5 1.5 0 0 1 16.5 6v1H20a1.5 1.5 0 0 1 1.5 1.5V18a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 18V8.5A1.5 1.5 0 0 1 4 7h3.5V6A1.5 1.5 0 0 1 9 4.5zm.5 1.7V7h5v-.8z", { "fill-rule": "evenodd" })],
  reply: [S("M10 6L4 12l6 6"), S("M4 12h9a7 7 0 0 1 7 7")],
  signal: [R(2, 15, 3.4, 5, 1), R(7, 12, 3.4, 8, 1), R(12, 8.5, 3.4, 11.5, 1), R(17, 5, 3.4, 15, 1)],
  battery: [S("M4 7.5h13a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 15V9A1.5 1.5 0 0 1 4 7.5z", 1.3),
            R(4.2, 9.2, 10.6, 5.6, .8), R(19.7, 10.3, 1.8, 3.4, .9)],
  // OmaCar's own mark, the ring and hub of its boot screen.
  omacar: [["circle", { cx: 12, cy: 12, r: 6.6, fill: "none", stroke: "currentColor", "stroke-width": 2.6 }],
           C(12, 12, 1.9)],
};

// The maneuver arrows, heavier and in a 48-unit box. Each is a shaft and a
// head; the "-left" ones are the "-right" ones in a mirror.
function head(tx, ty, dx, dy, len = 13, half = 10) {
  const n = Math.hypot(dx, dy);
  dx /= n; dy /= n;
  const bx = tx - dx * len, by = ty - dy * len;
  return `M${r2(tx)} ${r2(ty)}L${r2(bx - dy * half)} ${r2(by + dx * half)}L${r2(bx + dy * half)} ${r2(by - dx * half)}Z`;
}
const shaft = (d, dim) => ["path", { d, fill: "none", stroke: "currentColor", "stroke-width": 6,
                                     "stroke-linecap": "round", "stroke-linejoin": "round",
                                     opacity: dim ? 0.4 : 1 }];
const tip = (d) => ["path", { d, fill: "currentColor", stroke: "currentColor", "stroke-width": 2,
                              "stroke-linejoin": "round" }];
const RIGHT = {
  straight: [shaft("M24 43V17"), tip(head(24, 5, 0, -1))],
  "turn-right": [shaft("M15 43V27a9 9 0 0 1 9-9h7"), tip(head(43, 18, 1, 0))],
  "slight-right": [shaft("M17 43V29l12-12"), tip(head(38, 8, 1, -1))],
  "fork-right": [shaft("M24 31L14 21", true), shaft("M24 43V31l8-8"), tip(head(41, 14, 1, -1))],
  "ramp-right": [shaft("M17 43V8", true), shaft("M17 43V30l11-11"), tip(head(37, 10, 1, -1))],
  uturn: [shaft("M32 43V19a9 9 0 0 0-18 0v9"), tip(head(14, 41, 0, 1))],
  merge: [shaft("M24 43V20", true), shaft("M11 43v-7c0-6 13-8 13-17"), tip(head(24, 5, 0, -1))],
  arrive: [F("M24 5a11 11 0 0 0-11 11c0 8.3 11 21 11 21s11-12.7 11-21A11 11 0 0 0 24 5zm0 6.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9z",
             { "fill-rule": "evenodd" }), shaft("M15 43h18")],
};
const MIRROR48 = { transform: "matrix(-1 0 0 1 48 0)" };
const mirrored = (parts) => parts.map(([tag, a]) => [tag, Object.assign({}, a, MIRROR48)]);
export const TURNS = Object.assign({}, RIGHT, {
  "turn-left": mirrored(RIGHT["turn-right"]),
  "slight-left": mirrored(RIGHT["slight-right"]),
  "fork-left": mirrored(RIGHT["fork-right"]),
  "ramp-left": mirrored(RIGHT["ramp-right"]),
});

function build(parts, box, cls) {
  const svg = el("svg", { viewBox: box, "aria-hidden": "true", class: cls });
  for (const [tag, a] of parts) svg.appendChild(el(tag, a));
  return svg;
}
export const glyph = (name) => build(GLYPHS[name] || [], "0 0 24 24", `g g-${name}`);
export const turnGlyph = (id) => build(TURNS[id] || TURNS.straight, "0 0 48 48", `g g-turn g-${id}`);

// A made-up map for a Maps tile: a park, some water, two roads and a location
// arrow. Coloured by projection.css through its classes.
export function mapArt() {
  return build([
    ["path", { d: "M0 16c4-1.5 7.5.5 10.5 4.5V24H0z", class: "mp-park" }],
    ["path", { d: "M16 0h8v10c-3.5-.5-6.5-4-8-10z", class: "mp-water" }],
    ["path", { d: "M-1 8C7 8.5 14 12 25 20.5", class: "mp-road" }],
    ["path", { d: "M8.5-1C9.5 8 12.5 15 11 25", class: "mp-hwy" }],
    ["circle", { cx: 16, cy: 15.5, r: 4.4, class: "mp-dot" }],
    ["path", { d: "M16 12.6l2.2 5.4-2.2-1.2-2.2 1.2z", class: "mp-arrow" }],
  ], "0 0 24 24", "g g-mapart");
}

// ------------------------------------------------------------------ text
const pad2 = (n) => String(n).padStart(2, "0");
export function clockText(ms) {
  const d = new Date(ms);
  return `${d.getHours() % 12 || 12}:${pad2(d.getMinutes())}`;
}
export const ampm = (ms) => (new Date(ms).getHours() < 12 ? "AM" : "PM");

export function mss(sec) {
  const s = Number.isFinite(sec) && sec > 0 ? Math.floor(sec) : 0;
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
}

const M_PER_MI = 1609.344;

// Imperial, by the same rule as the Navigation screen (Task 3): feet to the
// nearest 50 under a tenth of a mile, one decimal under ten, whole above.
function distText(m) {
  const mi = m / M_PER_MI;
  if (mi < 0.1) return `${Math.round((m * 3.28084) / 50) * 50} ft`;
  if (mi < 10) return `${mi.toFixed(1)} mi`;
  return `${Math.round(mi)} mi`;
}

// OSRM's maneuver type and modifier, to the icon ids Task 3's nav.js uses.
export function iconOf(next) {
  const type = (next && next.type) || "";
  const mod = (next && next.modifier) || "";
  const left = mod.includes("left");
  if (type === "arrive") return "arrive";
  if (mod === "uturn") return "uturn";
  if (type === "merge") return "merge";
  if (type === "fork") return left ? "fork-left" : "fork-right";
  if (type === "on ramp" || type === "off ramp") return left ? "ramp-left" : "ramp-right";
  if (mod === "slight left" || mod === "slight right") return left ? "slight-left" : "slight-right";
  if (left || mod.includes("right")) return left ? "turn-left" : "turn-right";
  return "straight";
}
const VERBS = {
  straight: "Continue", "turn-right": "Turn right", "turn-left": "Turn left",
  "slight-right": "Bear right", "slight-left": "Bear left", "fork-right": "Keep right",
  "fork-left": "Keep left", "ramp-right": "Take the ramp", "ramp-left": "Take the ramp",
  merge: "Merge", uturn: "Make a U-turn", arrive: "Arrive",
};

function ownBanner(next) {
  const icon = iconOf(next);
  return { icon, distance: distText(Math.max(0, Number(next && next.in_m) || 0)),
           text: VERBS[icon], street: (next && next.street) || "" };
}

// { icon, distance, text, street }: Task 3's when it is loaded and answers in
// that shape, ours otherwise. The icon is always one we can draw.
export function bannerOf(next) {
  const own = ownBanner(next);
  if (nav && typeof nav.bannerOf === "function") {
    try {
      const b = nav.bannerOf(next);
      if (b && typeof b.distance === "string" && b.distance) {
        return { icon: TURNS[b.icon] ? b.icon : own.icon, distance: b.distance,
                 text: typeof b.text === "string" && b.text ? b.text : own.text,
                 street: typeof b.street === "string" ? b.street : own.street };
      }
    } catch { /* ours, then */ }
  }
  return own;
}

// What a turn card shows for a live sample: nothing yet, carry on along the
// street, or the next maneuver.
export function turnOf(sample) {
  const d = sample && sample.demo;
  if (!d) return { wait: true };
  if (!d.next) return { icon: "straight", distance: "", text: "Continue", street: d.street || "" };
  return bannerOf(d.next);
}

// The trip, split the way both platforms lay it out.
export function tripOf(demo, nowMs) {
  if (!demo || !Number.isFinite(demo.eta)) return null;
  const mins = Math.round(Math.max(0, demo.eta - nowMs / 1000) / 60);
  const hr = Math.floor(mins / 60), min = mins % 60;
  const mi = Math.max(0, Number(demo.remaining_m) || 0) / M_PER_MI;
  return {
    arrive: clockText(demo.eta * 1000), ampm: ampm(demo.eta * 1000),
    left: hr ? { n: `${hr}:${pad2(min)}`, u: "hrs" } : { n: String(min), u: "min" },
    long: hr ? `${hr} hr ${min} min` : `${min} min`,
    miles: mi < 10 ? mi.toFixed(1) : String(Math.round(mi)),
  };
}

// Sets text only when it changed: the turn card repaints four times a second.
export function setText(node, s) {
  if (node && node.textContent !== s) node.textContent = s;
}

// A turn card built by either view, filled in from turnOf(). The parts it
// looks for: .proj-turn-ic, .proj-turn-dist, .proj-turn-street and, if the
// view has one, .proj-turn-text.
export function paintTurn(card, t) {
  const q = (s) => card.querySelector(s);
  const ic = q(".proj-turn-ic"), dist = q(".proj-turn-dist"), street = q(".proj-turn-street");
  const text = q(".proj-turn-text");
  card.classList.toggle("wait", !!t.wait);
  if (t.wait) {
    // Forget what was drawn, not only the drawing: a drive that comes back at
    // the same distance must draw it again.
    if (ic) { clear(ic); delete ic.dataset.icon; }
    if (dist) delete dist.dataset.v;
    setText(dist, "");
    setText(street, "Waiting for the demo drive");
    setText(text, "");
    return;
  }
  if (ic && ic.dataset.icon !== t.icon) {
    clear(ic);
    ic.appendChild(turnGlyph(t.icon));
    ic.dataset.icon = t.icon;
  }
  if (dist && dist.dataset.v !== (t.distance || t.text)) {
    dist.dataset.v = t.distance || t.text;
    clear(dist);
    const m = /^(\S+)\s+(.+)$/.exec(t.distance || "");
    if (m) dist.append(h("span.n", m[1]), " ", h("span.u", m[2]));
    else dist.textContent = t.text;
  }
  setText(street, t.street || t.text);
  setText(text, t.street ? t.text : "");
}

// ------------------------------------------------------------------ the takeover
let mounted = null;

// Mounts a projection into `root`. `make(el)` builds it and returns
// { go(screenId), stop() }. Unmounting stops it and gives the app its bars back.
export function takeover(root, kind, make) {
  document.body.classList.add(TAKEOVER);
  const el = h(`div.proj.proj-${kind}`);
  root.appendChild(el);
  let inst = null;
  try { inst = make(el); } catch (e) {
    el.remove();
    document.body.classList.remove(TAKEOVER);
    throw e;
  }
  mounted = inst;
  return () => {
    if (mounted === inst) mounted = null;
    try { inst.stop(); } finally {
      el.remove();
      document.body.classList.remove(TAKEOVER);
    }
  };
}

// Moves whichever projection is mounted to one of its screens: for the tour,
// which calls functions rather than simulating taps. False when none is up.
export function openScreen(id) {
  if (!mounted) return false;
  mounted.go(id);
  return true;
}

// One screen at a time in `stage`. A screen is `(host) -> cleanup`; an
// unknown id goes to `fallback`.
export function createRouter(stage, screens, { fallback, onChange } = {}) {
  let cur = null, off = null;
  const leave = () => {
    if (off) { try { off(); } catch (e) { console.error(e); } }
    off = null;
    clear(stage);
  };
  return {
    get current() { return cur; },
    go(id) {
      if (!Object.prototype.hasOwnProperty.call(screens, id)) id = fallback;
      if (id === cur) return;
      leave();
      cur = id;
      if (onChange) onChange(id);
      const host = h("div.proj-scr", { data: { screen: id } });
      stage.appendChild(host);
      off = screens[id](host) || null;
    },
    stop() { leave(); cur = null; },
  };
}

// The clock in every .proj-time under `root`, h:mm, kept current.
export function runClock(root, now) {
  const tick = () => {
    const t = clockText(now());
    for (const n of root.querySelectorAll(".proj-time")) setText(n, t);
  };
  tick();
  const id = setInterval(tick, 1000);
  return () => clearInterval(id);
}

// `fn(store.live)` now and on every live sample (4 Hz: both views are fast).
export function onLive(fn) {
  fn(store.live);
  return store.on("live", () => fn(store.live));
}

// The injected map in a pane of its own, with the credit every map carries.
// mountMap draws into .proj-map once the pane is on the page (start); stop
// lets it go. If the map draws its own credit, ours steps aside (paint).
export function mapPane(deps, style) {
  const map = h("div.proj-map");
  const credit = h("div.proj-credit", "© OpenStreetMap contributors");
  const pane = h("div.proj-mappane", map, credit);
  let handle = null;
  return {
    pane, map,
    start() {
      if (!deps.mountMap) return;
      try { handle = deps.mountMap(map, { style, follow: true }); } catch (e) { console.error(e); handle = null; }
    },
    paint() { credit.hidden = map.textContent.includes("OpenStreetMap"); },
    stop() {
      const h0 = handle;
      handle = null;
      if (h0 && typeof h0.destroy === "function") {
        try { h0.destroy(); } catch (e) { console.error(e); }
      }
    },
  };
}

// ------------------------------------------------------------------ the radio
// What to show for the one radio: a state it pushed, filled in from the player
// itself for anything the push left out. Mounting reads it straight from the
// player, so a song already playing is on screen before the next push.
export function stateOf(radio, pushed) {
  const p = pushed || {};
  const call = (f) => { try { return typeof f === "function" ? Number(f.call(radio)) : NaN; } catch { return NaN; } };
  const index = Number.isInteger(p.index) ? p.index : (radio && Number.isInteger(radio.index) ? radio.index : 0);
  const t = ((radio && radio.tracks) || [])[index] || {};
  return {
    index,
    title: p.title || t.title || "Omarchy Radio",
    artist: p.artist || t.artist || "",
    playing: "playing" in p ? !!p.playing : !!(radio && radio.playing),
    position: Number.isFinite(p.position) ? p.position : call(radio && radio.position),
    duration: Number.isFinite(p.duration) ? p.duration : call(radio && radio.duration),
  };
}

function press(radio, what) {
  if (!radio) return;
  if (what === "toggle") {
    if (typeof radio.toggle === "function") radio.toggle();
    else if (radio.playing) radio.pause();
    else radio.play();
  } else if (typeof radio[what] === "function") radio[what]();
}

// Previous, play/pause and next, as buttons wired to the radio. `skin` names
// the glyphs: CarPlay's are double triangles, Android Auto's a bar and one.
export function mediaButtons(radio, { prev = "prev", next = "next" } = {}) {
  return [
    h("button.pj-prev", { "aria-label": "Previous", onclick: () => press(radio, "prev") }, glyph(prev)),
    h("button.pj-play", { "aria-label": "Play", onclick: () => press(radio, "toggle") }, glyph("play")),
    h("button.pj-next", { "aria-label": "Next", onclick: () => press(radio, "next") }, glyph(next)),
  ];
}

// Keeps every .pj-* part under `box` showing the radio, and lets a tap on the
// bar seek. Returns the unsubscribe.
export function bindMedia(box, radio) {
  let dur = 0;
  const all = (s) => box.querySelectorAll(s);
  const paint = (st) => {
    dur = Number.isFinite(st.duration) && st.duration > 0 ? st.duration : 0;
    const pos = Math.max(0, Math.min(Number.isFinite(st.position) ? st.position : 0, dur || Infinity));
    for (const e of all(".pj-title")) setText(e, st.title);
    for (const e of all(".pj-artist")) setText(e, st.artist);
    for (const e of all(".pj-elapsed")) setText(e, mss(pos));
    for (const e of all(".pj-remain")) setText(e, "-" + mss(dur - pos));
    for (const e of all(".pj-dur")) setText(e, mss(dur));
    for (const e of all(".pj-fill")) e.style.width = `${dur ? ((pos / dur) * 100).toFixed(2) : 0}%`;
    for (const e of all(".pj-play")) {
      const want = st.playing ? "Pause" : "Play";
      if (e.getAttribute("aria-label") !== want) {
        e.setAttribute("aria-label", want);
        clear(e);
        e.appendChild(glyph(st.playing ? "pause" : "play"));
      }
    }
    for (const e of all(".pj-row")) {
      e.classList.toggle("on", Number(e.dataset.i) === st.index);
      e.classList.toggle("playing", Number(e.dataset.i) === st.index && st.playing);
    }
    box.classList.toggle("pj-playing", !!st.playing);
  };
  for (const bar of all(".pj-bar")) {
    bar.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const r = bar.getBoundingClientRect();
      if (!radio || !dur || !r.width || typeof radio.seek !== "function") return;
      radio.seek(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * dur);
    });
  }
  paint(stateOf(radio));
  const off = radio && typeof radio.subscribe === "function"
    ? radio.subscribe((st) => paint(stateOf(radio, st))) : null;
  return () => { if (typeof off === "function") off(); };
}

// The station's card art: its name in type, never an invented cover.
export function stationArt(cls = "") {
  return h(`div.proj-art${cls}`, h("span", "OMARCHY"), h("span", "RADIO"));
}

// ------------------------------------------------------------------ made up
// Phone and Messages are static and invented. Nobody here is real, and
// nothing is read from a phone.
export const FAVOURITES = [
  { name: "Mom", kind: "mobile", initial: "M", glyph: null, tint: "#E8710A" },
  { name: "Home", kind: "home", initial: "H", glyph: "house", tint: "#1E8E3E" },
  { name: "Office", kind: "work", initial: "O", glyph: "case", tint: "#7B61FF" },
];
export const THREAD = { from: "Jordan", initial: "J", tint: "#0B8BD6", minsAgo: 4,
                        text: "Leaving Los Banos now, see you at the meetup 🎉" };
export const EVENT = { title: "Omarchy Meetup", when: "6:00 – 8:00 PM", where: "San Francisco" };

export const whenText = (ms) => `${clockText(ms)} ${ampm(ms)}`;
