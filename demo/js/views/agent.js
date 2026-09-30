// OMA AGENT, AS THE MEETUP DEMO SHOWS IT (mockup 7; Task 6 of
// doc/design/2026-09-30-meetup-demo-plan.md).
//
// The live screen (share/js/views/advisor.js) drives the real `claude` CLI,
// which the demo never calls and the demo server refuses (/api/ai/*). This one
// answers from a short script, demo/data/agent.json, and says so under the
// input: "Illustrative agent responses". What it DOES do for real is the one
// thing an agent on this dashboard is for: Apply rearranges the demo's Home
// through the same /api/home the Customize editor uses, and sets a night look.
// Only while parked, which is the live app's own rule for changing a layout.
//
// Free text is matched to the script by keywords; the mic "listens" for 1.6 s
// and then asks the next question nobody has asked yet.

import { h, icon, store, api as coreApi, toast, temp, pct, U } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { applyLook as coreApplyLook, lookById, savedLook } from "../../../js/looks.js";
import { say as coreSay, LINES, linesReady } from "../voice.js";

// The pace of a reply: thinking for 700-1200 ms, then about 45 characters a
// second; the mic listens for 1.6 s and types at 40. A test makes these fast.
export const pace = { think: [700, 1200], cps: 45, listen: 1600, typeCps: 40 };

export const FALLBACK = "In this demo I know a few questions. Try one of the suggestions below.";
export const PARK_REASON = "Park to change your layout";
const PLACEHOLDER = "Ask about your car or change your layout…";
const CAR_PIC = "/demo-media/crz-home.png";

// The darkest night look that keeps OmaCar's own hues: "Night · dim",
// "everything pulled down" (share/js/looks.js). Night · red is for
// dark adaptation on a long drive, and recolours every screen after it; the
// line this answers with is "I've dimmed the rest".
export const NIGHT_LOOK = "dim";

// The night arrangement: navigation first and large, the speed dial and the car
// beside it, the four tiles a night drive watches (the pack first), then the
// music, the cameras and the agent (share/data/home-cards.json sizes). It
// TILES Home's grid, in the default's rows: landscape nav 4x3 + dial 3x3 + car
// 5x3, four 3x1 tiles, and three 4x2; portrait the same cards at their portrait
// sizes. (The first one, nav 4x3, dial 4x3 and phone 4x2 before three 3x1
// tiles and the rest where they were, left holes and ran past the screen.) A
// card Home has beyond these keeps its place after them.
export const NIGHT = {
  landscape: [["nav", "m"], ["dial", "m"], ["car", "l"], ["charge", "s"], ["coolant", "s"],
              ["fuel", "s"], ["volts", "s"], ["phone", "m"], ["dashcam", "m"], ["agent", "m"]],
  portrait:  [["nav", "m"], ["dial", "m"], ["car", "l"], ["charge", "s"], ["coolant", "s"],
              ["fuel", "s"], ["volts", "s"], ["phone", "m"], ["dashcam", "m"], ["agent", "m"]],
};

// ------------------------------------------------------------------ icons
// Drawn, like share/js/icons.js, so the demo has no artwork to lose.
export const GLYPH = {
  person: ["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"],
  mic: ["M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z", "M19 11a7 7 0 0 1-14 0", "M12 18v3"],
  send: ["M21.5 2.5 10.5 13.5", "M21.5 2.5 14.5 21.5l-4-8-8-4z"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  bubble: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  bars: ["M6 20v-5", "M12 20V9", "M18 20V4"],
  obd: ["M3.5 8h17l-2 9h-13z", "M8 11.5v2", "M12 11.5v2", "M16 11.5v2"],
  chip: ["M7 7h10v10H7z", "M10 3v4", "M14 3v4", "M10 17v4", "M14 17v4", "M3 10h4", "M3 14h4", "M17 10h4", "M17 14h4"],
  play: ["M7 4.5v15l12-7.5z"],
  pause: ["M7 5h3v14H7z", "M14 5h3v14h-3z"],
  prev: ["M18 5v14L8 12z", "M6 5v14"],
  next: ["M6 5v14l10-7z", "M18 5v14"],
  info: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 16v-4", "M12 8h.01"],
  right: ["M8 20v-8a3 3 0 0 1 3-3h8", "M15.5 5.5 19 9l-3.5 3.5"],
  left: ["M16 20v-8a3 3 0 0 0-3-3H5", "M8.5 5.5 5 9l3.5 3.5"],
  straight: ["M12 20V4", "M7.5 8.5 12 4l4.5 4.5"],
};
const CHIP_ICON = { warning: GLYPH.bubble, drive: GLYPH.bars, health: ICONS.health,
                    night: ICONS.moon, radio: ICONS.music };

// ------------------------------------------------------------------ helpers
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const between = ([lo, hi]) => lo + Math.random() * (hi - lo);

export function clock12(d = new Date()) {
  const hh = d.getHours();
  return `${hh % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")} ${hh >= 12 ? "PM" : "AM"}`;
}

// A reply appearing as it is "written": onText gets each longer prefix, about
// 30 times a second, and last of all the whole text.
export async function stream(text, onText, { cps = pace.cps, wait = sleep, alive = () => true } = {}) {
  const step = Math.max(1, Math.round(cps / 30));
  for (let i = step; i < text.length; i += step) {
    if (!alive()) return;
    onText(text.slice(0, i));
    await wait((step * 1000) / cps);
  }
  if (alive()) onText(text);
}

// The level bars: "listening", "speaking", or still.
export function waveform(n = 11, cls = "") {
  const node = h("div.dm-wave" + (cls ? "." + cls : ""), { "aria-hidden": "true", data: { state: "idle" } });
  for (let i = 0; i < n; i++) {
    const mid = 1 - Math.abs(i - (n - 1) / 2) / ((n + 1) / 2);
    // setProperty: h()'s style object goes through Object.assign, which
    // drops custom properties without a word.
    const bar = h("i");
    bar.style.setProperty("--h", (0.2 + 0.8 * mid * (0.75 + 0.25 * Math.abs(Math.sin(i * 1.7)))).toFixed(2));
    bar.style.setProperty("--d", `${(i * 97) % 600}ms`);
    node.appendChild(bar);
  }
  return { node, set: (state) => { node.dataset.state = state; } };
}

export function meMsg(words) {
  return h("div.ag-msg.me",
    h("span.ag-av", icon(GLYPH.person, 18)),
    h("div.ag-bubble", h("div.ag-text", words), h("span.ag-time", clock12())));
}

// An answer: three dots until thinking() is done, then the words.
export function botMsg() {
  const dots = h("span.ag-dots", { "aria-label": "Thinking" }, h("i"), h("i"), h("i"));
  const text = h("div.ag-text");
  const time = h("span.ag-time");
  const bubble = h("div.ag-bubble", dots, text, time);
  const node = h("div.ag-msg.bot", h("span.ag-av.ring"), bubble);
  return { node, bubble, text, time, dots };
}

// ------------------------------------------------------------------ the script
let script = null, scriptNow = null;

export function loadScript() {
  if (!script) {
    script = fetch(new URL("../../data/agent.json", import.meta.url), { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`agent.json: ${r.status}`); return r.json(); })
      .then((s) => { scriptNow = s; return s; })
      .catch((e) => { script = null; console.error(e); return []; });
  }
  return script;
}

function words(s) {
  return " " + String(s || "").toLowerCase().replace(/[’‘]/g, "'")
    .replace(/[^a-z0-9'°]+/g, " ").trim() + " ";
}

// The script entry a question means, or null. Each keyword found as whole
// words scores its length in words, so "check engine" outweighs "check";
// the highest score wins, and a tie goes to the earlier entry.
export function matchScript(text, entries) {
  const t = words(text);
  if (!t.trim()) return null;
  let best = null, top = 0;
  for (const e of entries || []) {
    let score = 0;
    for (const k of e.keywords || []) {
      const kw = words(k).trim();
      if (kw && t.includes(" " + kw + " ")) score += kw.split(" ").length;
    }
    if (score > top) { best = e; top = score; }
  }
  return best;
}

const question = (e) => (/[.?!]$/.test(e.chip) ? e.chip : e.chip + ".");

// ------------------------------------------------------------------ parked
// Whether the demo car is parked: the demo world says so outright
// (live.json's demo.parked, Task 1). Without it, the live app's own lock.
export function parkedNow() {
  const d = store.sample && store.sample.demo;
  if (d && typeof d.parked === "boolean") return d.parked;
  return !store.lockedAsMoving;
}

// ------------------------------------------------------------------ the layout
export function nightLayout(current) {
  const out = {};
  for (const o of ["landscape", "portrait"]) {
    const first = NIGHT[o].map((x) => x.slice());
    const ids = new Set(first.map(([c]) => c));
    const cur = current && current[o] ? current[o] : {};
    const rest = (Array.isArray(cur.cards) ? cur.cards : [])
      .filter((it) => Array.isArray(it) && it.length === 2 && !ids.has(it[0]))
      .map(([c, s]) => [c, s]);
    const hidden = (Array.isArray(cur.hidden) ? cur.hidden : []).filter((c) => !ids.has(c));
    out[o] = { cards: [...first, ...rest], hidden };
  }
  return out;
}

// What Apply replaced, kept until restoreHome() puts it back: the layout from
// before the FIRST Apply, not the night one a second Apply would find.
let kept = null, keptLook = null;

const withIO = (io = {}) => ({ api: io.api || coreApi, applyLook: io.applyLook || coreApplyLook,
                               say: io.say || coreSay });
const lookNow = () => document.documentElement.getAttribute("data-look") || "normal";

export async function applyNightLayout(io = {}) {
  const parked = io.parked !== undefined ? io.parked : parkedNow();
  if (!parked) return { ok: false, why: PARK_REASON };
  const x = withIO(io);
  const current = await x.api.home();
  if (!kept) kept = current;
  await x.api.saveHome(nightLayout(current));
  if (keptLook === null) keptLook = lookNow();
  x.applyLook(lookById(NIGHT_LOOK).id);
  const spoken = Promise.resolve(x.say("layout-applied")).catch(() => {});
  return { ok: true, spoken };
}

// The demo's restart: Home and the look as they were before the first Apply.
// After a reload nothing is kept, and the night layout may still be on the
// server, so Home goes back to its default (homelayout.py's reset) and the
// look to the one this page boots with.
export async function restoreHome(io = {}) {
  const x = withIO(io);
  const k = kept, l = keptLook;
  kept = null;
  keptLook = null;
  await x.api.saveHome(k || { action: "reset" });
  x.applyLook(l !== null ? l : savedLook());
}

// ------------------------------------------------------------------ the preview
function miles(m) {
  const mi = m / 1609.344;
  if (mi < 0.1) return `${Math.max(50, Math.round((m * 3.28084) / 50) * 50)} ft`;
  return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
}

function left(secs) {
  const m = Math.max(1, Math.round(secs / 60));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

// A sketch of the coast for the preview's navigation card, when the real map
// (Task 3's mountMap) is not there to draw it.
function sketchMap() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 150 100");
  svg.setAttribute("preserveAspectRatio", "xMidYMid slice");
  svg.setAttribute("class", "nl-sketch");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = `
    <rect width="150" height="100" class="sea"/>
    <path class="land" d="M58 0 C52 14 63 24 56 36 C49 48 59 58 52 70 C47 80 55 90 49 100 L150 100 L150 0Z"/>
    <path class="road" d="M66 100 C80 82 92 74 120 66 L150 60"/>
    <path class="road" d="M95 0 C98 20 108 30 150 34"/>
    <path class="road" d="M68 52 C88 54 118 46 150 48"/>
    <path class="road" d="M112 100 C114 84 124 76 150 74"/>
    <path class="route-glow" d="M76 100 C74 88 70 78 72 64 C74 52 67 42 70 30 C72 20 68 10 70 0"/>
    <path class="route" d="M76 100 C74 88 70 78 72 64 C74 52 67 42 70 30 C72 20 68 10 70 0"/>
    <path class="me" d="M72.5 57 l-5 11 5 -3 5 3z"/>`;
  return svg;
}

// Speed as an arc of 270 degrees, 0 to 100 mph.
function dialSvg() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 120 120");
  svg.setAttribute("class", "nl-arc");
  svg.setAttribute("aria-hidden", "true");
  const arc = "M31.7 88.3 A40 40 0 1 1 88.3 88.3";
  svg.innerHTML = `<path class="track" d="${arc}"/><path class="val" d="${arc}" pathLength="100"/>`;
  return svg;
}

// The night layout, drawn small in the reply and full size in the overlay:
// nav, the dial, the music and three tiles, with the car's own numbers.
function nightPicture({ radio, mountMap, big = false }) {
  const map = h("div.nl-map");
  let mapHandle = null;
  if (typeof mountMap === "function") {
    try { mapHandle = mountMap(map, { style: "omacar", follow: true }); } catch { mapHandle = null; }
  }
  if (!mapHandle) map.appendChild(sketchMap());
  const turn = h("span.nl-turn"), turnIn = h("b"), street = h("span.nl-street"), eta = h("div.nl-eta");
  const nav = h("div.nl-nav", map,
    h("div.nl-man", turn, h("div", turnIn, street)), eta);

  const spd = h("b"), unit = h("small");
  const arc = dialSvg();
  const carPic = h("img.nl-car", { alt: "", hidden: true, draggable: "false" });
  carPic.onload = () => { carPic.hidden = false; };
  carPic.src = CAR_PIC;
  const dial = h("div.nl-dial", h("div.nl-gauge", arc, h("div.nl-speed", spd, unit), h("span.nl-gear", "D")),
    carPic, h("span.nl-ready", "READY"));

  const title = h("b.nl-song"), artist = h("span.nl-artist");
  const music = h("div.nl-music",
    h("div.nl-np", h("span.nl-or", "OR"), h("div.nl-np-w", title, artist)),
    h("div.nl-prog", h("i")),
    h("div.nl-ctl", icon(GLYPH.prev, 16), h("span.nl-pp", icon(GLYPH.pause, 18)), icon(GLYPH.next, 16)));

  const tile = (ico, cls, label) => {
    const v = h("b");
    return { v, node: h("div.nl-tile." + cls, h("span.nl-ti", icon(ico, 16)), v, h("small", label)) };
  };
  const tHybrid = tile(ICONS.battery, "hy", "Hybrid battery");
  const tCool = tile(ICONS.thermo, "co", "Engine temp");
  const tFuel = tile(ICONS.fuel, "fu", "Fuel");
  const tiles = h("div.nl-tiles", tHybrid.node, tCool.node, tFuel.node);

  const node = h("div.nl-wrap", h("div.nl" + (big ? ".big" : ""), nav, dial, h("div.nl-right", music, tiles)));

  function paint() {
    const v = store.values || {};
    const d = (store.sample && store.sample.demo) || null;
    spd.textContent = String(Math.round((v.SPEED ?? 77) * U.units.km));
    unit.textContent = U.units.speed;
    // The gap longer than the arc, so a zero-length dash is one cap, not two.
    arc.querySelector(".val").style.strokeDasharray = `${Math.min(100, ((v.SPEED ?? 77) * U.units.km))} 1000`;
    tHybrid.v.textContent = pct(v.HYBRID_BATTERY_REMAINING ?? 52);
    tCool.v.textContent = temp(v.COOLANT_TEMP ?? 88, false) + U.units.temp;
    tFuel.v.textContent = pct(v.FUEL_LEVEL ?? 64);
    const nx = d && d.next;
    turn.replaceChildren(icon(GLYPH[nx && /left/.test(nx.modifier || "") ? "left"
                                    : nx && /straight|continue/.test(nx.modifier || nx.type || "") ? "straight" : "right"], 22));
    turnIn.textContent = nx && Number.isFinite(nx.in_m) ? miles(nx.in_m) : "1.2 mi";
    street.textContent = (nx && nx.street) || (d && d.street) || "Pacific Coast Hwy";
    if (d && Number.isFinite(d.eta) && Number.isFinite(d.remaining_m)) {
      eta.textContent = `ETA ${clock12(new Date(d.eta * 1000))} · ${left(d.eta - Date.now() / 1000)} · ${miles(d.remaining_m)}`;
    } else {
      eta.textContent = "ETA 10:14 PM · 32 min · 22 mi";
    }
    const tr = radio && radio.tracks && radio.tracks[radio.index || 0];
    title.textContent = tr ? tr.title : "Omarchy Radio";
    artist.textContent = tr ? tr.artist : "Ryan R. Hughes";
  }
  paint();
  return { node, paint, destroy() { if (mapHandle && mapHandle.destroy) try { mapHandle.destroy(); } catch { /* gone */ } } };
}

// ------------------------------------------------------------------ the screen
// The mounted screen, for the tour (Task 8), which calls these rather than
// clicking: agentActions.ask("night"), .apply(), .ask("radio").
let active = null;
export const agentActions = {
  ask: (id) => (active ? active.ask(id) : Promise.resolve(false)),
  apply: () => (active ? active.apply() : applyNightLayout()),
  preview: () => { if (active) active.preview(); },
  restore: (io) => restoreHome(io),
};

export function agentView(deps = {}) {
  const radio = deps.radio || null;
  const io = withIO(deps);

  return function mount(root) {
    let alive = true, queue = Promise.resolve();
    let listening = false, applying = false, applied = false;
    const asked = new Set();
    const pictures = new Set(), applyBtns = new Set(), cleanups = [];
    let overlay = null;

    // ---- the car and its context (landscape: the left panel)
    const pic = h("img.ag-carpic", { alt: "", hidden: true, draggable: "false" });
    pic.onload = () => { pic.hidden = false; };
    pic.src = CAR_PIC;
    const go = (hash) => () => { location.hash = hash; };
    const ctxRow = (ico, title, sub, hash) => h("button.ag-ctx-row", { type: "button", onclick: go(hash) },
      h("span.ag-ctx-i", icon(ico, 20)), h("span.ag-ctx-t", h("b", title), h("small", sub)),
      h("span.ag-chev", icon(ICONS.chevron, 18)));
    const car = h("div.ag-car",
      h("div.ag-car-h", h("div.ag-car-t", "Your CR-Z"), h("div.ag-car-s", "2015 · Sport Hybrid")),
      h("div.ag-picbox", pic),
      h("span.ag-chev.ag-car-go", icon(ICONS.chevron, 18)));
    const context = h("div.ag-ctx", h("div.ag-ctx-h", "Vehicle context"),
      ctxRow(GLYPH.obd, "OBD-II · Demo", "Live data connected", "#vehicle"),
      ctxRow(GLYPH.chip, "Honda enhanced · Demo", "OEM data & systems", "#vehicle"),
      ctxRow(ICONS.camera, "Cameras · Demo", "3 cameras available", "#cameras"));
    const helloWave = waveform(9, "ag-hello-wave");
    const hello = h("div.ag-hello", helloWave.node,
      h("div", h("div.ag-hello-h", "What would you like to do?"),
        h("p", "I can help with your car, your data, or create a custom dashboard layout.")));
    const side = h("aside.ag-side", car, context, hello);

    // ---- the chat
    const log = h("div.ag-log", { role: "log", "aria-live": "polite" },
      h("div.ag-empty", h("span.ag-av.ring"),
        h("div.ag-empty-h", "Hi James."),
        h("p", "Ask about your CR-Z, or ask me to change your dashboard.")));
    const chips = h("div.ag-chips");
    const input = h("input", { type: "text", placeholder: PLACEHOLDER, "aria-label": "Ask Oma Agent",
                               autocomplete: "off", enterkeyhint: "send" });
    const heard = h("span.ag-heard");
    const level = waveform(15, "ag-level");
    const mic = h("button.ag-mic", { type: "button", "aria-label": "Speak", onclick: () => listen() },
      icon(GLYPH.mic, 22));
    const sendBtn = h("button.ag-send", { type: "submit", "aria-label": "Send" }, icon(GLYPH.send, 18));
    const form = h("form.ag-input", { onsubmit: (e) => { e.preventDefault(); submit(); } },
      mic, h("div.ag-field", input, heard, level.node), sendBtn);
    const main = h("section.ag-main", log, chips, form, h("div.ag-note", "Illustrative agent responses"));
    root.appendChild(h("div.ag", side, main));

    const add = (node) => {
      const empty = log.querySelector(".ag-empty");
      if (empty) empty.remove();
      log.appendChild(node);
      scroll();
      return node;
    };
    const scroll = () => { log.scrollTop = log.scrollHeight; };

    // At once when the script is in hand, so a screen opened a second time
    // never draws without its chips.
    async function paintChips() {
      const s = scriptNow || await loadScript();
      if (!alive) return;
      if (s.length && s.every((e) => asked.has(e.id))) asked.clear();
      chips.replaceChildren(...s.filter((e) => !asked.has(e.id)).map((e) =>
        h("button.ag-chip", { type: "button", onclick: () => ask(e.id) },
          icon(CHIP_ICON[e.id] || GLYPH.bubble, 18), h("span", e.chip))));
    }

    function paintApply() {
      const parked = parkedNow();
      for (const { btn, why } of applyBtns) {
        btn.disabled = applying || applied || !parked;
        btn.title = parked ? "" : PARK_REASON;
        btn.querySelector("span").textContent = applied ? "Applied" : applying ? "Applying…" : "Apply layout";
        if (why) why.hidden = parked || applied;
      }
    }
    const paintLive = () => { paintApply(); for (const p of pictures) p.paint(); };
    cleanups.push(store.on("live", paintLive), store.on("car", paintLive));

    function applyButton(cls = "") {
      const btn = h("button.ag-btn.primary.ag-apply" + cls, { type: "button", onclick: () => apply() },
        icon(ICONS.check, 18), h("span", "Apply layout"));
      return btn;
    }

    function previewCard() {
      const pictured = nightPicture({ radio, mountMap: deps.mountMap });
      pictures.add(pictured);
      const btn = applyButton();
      const why = h("div.ag-why", { hidden: true }, icon(GLYPH.info, 15), h("span", PARK_REASON));
      applyBtns.add({ btn, why });
      const card = h("div.ag-preview",
        h("div.ag-pv-h", h("div", h("div.ag-pv-t", "Night drive"), h("div.ag-pv-s", "Landscape + portrait")),
          h("span.ag-pill", "For your CR-Z")),
        pictured.node,
        h("div.ag-pv-b",
          h("button.ag-btn.ag-previewbtn", { type: "button", onclick: () => preview() },
            icon(GLYPH.eye, 18), h("span", "Preview layout")),
          btn),
        why);
      paintApply();
      return card;
    }

    function preview() {
      closePreview();
      const pictured = nightPicture({ radio, mountMap: deps.mountMap, big: true });
      pictures.add(pictured);
      const btn = applyButton(".ag-ov-apply");
      const why = h("div.ag-why", { hidden: true }, icon(GLYPH.info, 15), h("span", PARK_REASON));
      const entry = { btn, why };
      applyBtns.add(entry);
      const close = () => closePreview();
      overlay = h("div.ag-overlay", { role: "dialog", "aria-modal": "true", "aria-label": "Night drive preview",
                                      onclick: (e) => { if (e.target === overlay) close(); } },
        h("div.ag-ov-card",
          h("div.ag-ov-h",
            h("div", h("div.ag-pv-t", "Night drive"), h("div.ag-pv-s", "Preview · as Home will look")),
            h("div.ag-ov-btns", btn,
              h("button.ag-btn.ag-close", { type: "button", onclick: close }, icon(ICONS.x, 18), h("span", "Close")))),
          pictured.node,
          why));
      overlay._done = () => { pictures.delete(pictured); pictured.destroy(); applyBtns.delete(entry); };
      document.body.appendChild(overlay);
      paintApply();
    }
    function closePreview() {
      if (!overlay) return;
      overlay._done();
      overlay.remove();
      overlay = null;
    }
    const onKey = (e) => { if (e.key === "Escape" && overlay) { e.preventDefault(); closePreview(); } };
    document.addEventListener("keydown", onKey);

    async function apply() {
      if (applying) return { ok: false, why: "busy" };
      if (!parkedNow()) { paintApply(); return { ok: false, why: PARK_REASON }; }
      applying = true;
      paintApply();
      try {
        const r = await applyNightLayout(io);
        if (r.ok && alive) {
          applied = true;
          closePreview();
          await linesReady;
          await answer(LINES["layout-applied"] || "Night drive is on.", null);
        }
        return r;
      } catch (e) {
        toast(`Couldn't change the layout: ${(e && e.message) || e}`, "bad");
        return { ok: false, why: String((e && e.message) || e) };
      } finally {
        applying = false;
        if (alive) paintApply();
      }
    }

    function nowPlaying() {
      const t = h("span.ag-np-t");
      const btn = h("button.ag-np-b", { type: "button", onclick: () => radio && radio.toggle() });
      const node = h("div.ag-np", h("span.ag-np-or", "OR"), h("span.ag-np-w", t, h("small", "Omarchy Radio")), btn);
      const show = (s) => {
        t.textContent = `${s.title} · ${s.artist}`;
        btn.replaceChildren(icon(s.playing ? GLYPH.pause : GLYPH.play, 16));
        btn.setAttribute("aria-label", s.playing ? "Pause" : "Play");
      };
      const tr = radio.tracks && radio.tracks[radio.index || 0];
      if (tr) show({ title: tr.title, artist: tr.artist, playing: !!radio.playing });
      if (radio.subscribe) cleanups.push(radio.subscribe(show));
      return node;
    }

    // One answer: dots, then the words at the reply's pace, then its time.
    async function answer(text, entry, spoken) {
      const b = botMsg();
      add(b.node);
      await sleep(between(pace.think));
      if (!alive) return;
      b.dots.remove();
      if (entry && entry.voice && !spoken) Promise.resolve(io.say(entry.voice)).catch(() => {});
      await stream(text, (t) => { b.text.textContent = t; scroll(); }, { alive: () => alive });
      b.time.textContent = clock12();
      return b;
    }

    async function run(entry, words) {
      if (!alive) return;
      if (entry) asked.add(entry.id);
      paintChips();
      add(meMsg(words));
      if (!entry) { await answer(FALLBACK, null); return; }
      if (entry.action === "radio" && radio) {
        if (!(radio.tracks && radio.tracks.length) && radio.load) {
          try { await Promise.race([radio.load(), sleep(3000)]); } catch { /* the reply says less */ }
        }
        const tr = radio.tracks && radio.tracks[0];
        const text = tr ? `Playing Omarchy Radio: ${tr.title}, by ${tr.artist || "Ryan R. Hughes"}.` : entry.reply;
        // The line first, then the music, as a presenter would; played even if
        // the screen has gone by then, because it was asked for. A play() the
        // browser refuses (no gesture yet) is the radio's to report, not this.
        Promise.resolve(io.say(entry.voice || "radio")).catch(() => {})
          .then(() => radio.play(0)).catch(() => {});
        const b = await answer(text, entry, true);
        if (b && alive) { b.bubble.appendChild(nowPlaying()); scroll(); }
        return;
      }
      await answer(entry.reply, entry);
      if (entry.action === "night-layout" && alive) add(previewCard());
    }

    function enqueue(entry, words) {
      queue = queue.then(() => run(entry, words)).catch((e) => console.error(e));
      return queue;
    }

    async function ask(id) {
      const s = await loadScript();
      const e = s.find((x) => x.id === id);
      return e ? enqueue(e, question(e)) : false;
    }

    async function submit() {
      const q = input.value.trim();
      if (!q) return;
      input.value = "";
      const s = await loadScript();
      return enqueue(matchScript(q, s), q);
    }

    async function listen() {
      if (listening) return;
      listening = true;
      form.classList.add("listening");
      heard.textContent = "Listening…";
      level.set("listen");
      const s = await loadScript();
      await sleep(pace.listen);
      form.classList.remove("listening");
      heard.textContent = "";
      level.set("idle");
      if (!alive) return;
      const next = s.find((e) => !asked.has(e.id)) || s[0];
      if (next) {
        await stream(question(next), (t) => { input.value = t; }, { cps: pace.typeCps, alive: () => alive });
        listening = false;
        if (alive) await submit();
      }
      listening = false;
    }

    const ctl = { ask, apply, preview };
    active = ctl;
    paintChips();

    return () => {
      alive = false;
      if (active === ctl) active = null;
      closePreview();
      document.removeEventListener("keydown", onKey);
      for (const p of pictures) p.destroy();
      for (const c of cleanups) { try { c(); } catch { /* gone */ } }
    };
  };
}

export default agentView;

// Task 8's boot.js calls this with deps = { radio, mountMap, back }. Fast, so
// the parked lock on Apply follows the demo car four times a second rather
// than the twenty-second snapshot.
export function register(D, deps = {}) {
  D.views.advisor = { mount: agentView(deps), fast: true };
}
