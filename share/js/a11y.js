// Accessibility: OmaCar follows OmaAccess (lib/access.py), and says what moves.
//
// THE SETTINGS LIVE IN OMAACCESS. The server reads `omaaccess status --json`
// and this module puts the answer on <html> as data-a11y-* (share/css/
// access.css), every few seconds, so a change made in OmaAccess's own panel
// reaches the dashboard without a reload. Without OmaAccess, the same settings
// are OmaCar's own (Settings > Accessibility).
//
// SAY. say(text) puts words in the page's live regions (app.html), which a
// screen reader reads; say(text, {urgent: true}) interrupts. When the person
// asked for alerts to be read aloud and no screen reader is running, the
// server speaks too, in OmaAccess's voice, under the house rule: no sound
// overnight with the car off unless it is a safety alert.
//
// EVERY SOUND IS ALSO A SIGHT. alertplayer.js announces each sound
// ("omacar:sound"); here it becomes a caption, and with OmaAccess's "flash for
// sounds" on, an edge glow, at most one every FLASH_GAP ms.

import { h, withToken } from "./core.js";

export const POLL_MS = 5000;
export const FLASH_GAP = 2000;
// The text-size steps the Settings row cycles through (lib/access.py SCALES).
export const SCALE_STEPS = [1, 1.15, 1.3, 1.5, 1.75, 2];
const CAPTION_MS = 4000;

// What each sound is, in words. The voice lines are the drowsy ladder's
// (drowsyui.js shows the same words on its cards).
export const SOUND_WORDS = {
  chime: "Chime", bark: "Dog bark", alarm: "Alarm",
  "voice:l1": "“You seem tired. Plan a break soon.”",
  "voice:l2": "“Time for a break.”",
  "voice:l3": "“Pull over now.”",
};

const DEFAULTS = {
  scale: 1, contrast: false, still: false, dyslexia: false, big: false,
  flash: false, captions: false, reader: false, speak: false, filter: "none", lang: "auto",
};

export const access = { source: "omacar", settings: { ...DEFAULTS }, version: null };
const listeners = new Set();

export function onAccess(fn) { listeners.add(fn); return () => listeners.delete(fn); }

// <html>'s attributes for a set of settings. Pure, so the test can hold it.
export function attrsFor(s) {
  const on = (b) => (b ? "" : null);
  return {
    "data-a11y-contrast": on(s.contrast),
    "data-a11y-still": on(s.still || matchesReducedMotion()),
    "data-a11y-dyslexia": on(s.dyslexia),
    "data-a11y-big": on(s.big),
    "data-a11y-filter": s.filter && s.filter !== "none" ? s.filter : null,
    lang: s.lang === "es" ? "es" : "en",
  };
}

function matchesReducedMotion() {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
}

export function apply(next) {
  const s = { ...DEFAULTS, ...(next && next.settings) };
  const same = JSON.stringify(s) === JSON.stringify(access.settings) && next.source === access.source;
  access.source = (next && next.source) || "omacar";
  access.version = (next && next.version) || null;
  access.settings = s;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(attrsFor(s))) {
    if (v === null) root.removeAttribute(k); else root.setAttribute(k, v);
  }
  if (!same) for (const fn of listeners) { try { fn(access); } catch (e) { console.warn(e); } }
}

// The page scale OmaAccess's text size asks for, on top of OmaCar's own size.
export function accessScale() {
  const v = Number(access.settings.scale);
  return v >= 1 && v <= 2 ? v : 1;
}

async function poll() {
  try {
    const r = await fetch(withToken("/api/access"), { cache: "no-store" });
    if (r.ok) apply(await r.json());
  } catch { /* no server: keep what is applied */ }
}

// ---- saying --------------------------------------------------------------------------

let clearTimer = null;
export function say(text, { urgent = false, aloud = false, urgency = "info" } = {}) {
  const t = String(text || "").trim();
  if (!t) return;
  const el = document.getElementById(urgent ? "say-urgent" : "say-polite");
  if (el) {
    // Emptied first, then filled on the next frame: the same words twice in a
    // row are a new announcement, not "nothing changed".
    el.textContent = "";
    requestAnimationFrame(() => { el.textContent = t; });
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => { el.textContent = ""; }, 8000);
  }
  if (aloud && access.settings.speak && !access.settings.reader) {
    fetch(withToken("/api/access/speak"), {
      method: "POST", body: JSON.stringify({ text: t, urgency }),
    }).catch(() => {});
  }
}

// ---- every sound is also a sight ----------------------------------------------------

export function wordsFor(cues) {
  const out = [];
  for (const c of cues || []) {
    const key = c.kind === "voice" ? `voice:${c.clip}` : c.kind;
    const w = SOUND_WORDS[key];
    if (w && !out.includes(w)) out.push(w);
  }
  return out;
}

let lastFlash = -Infinity, capEl = null, capTimer = null;

export function flashAllowed(now, last = lastFlash) { return now - last >= FLASH_GAP; }

function showSound(words, level) {
  const s = access.settings;
  if (s.flash && flashAllowed(performance.now())) {
    lastFlash = performance.now();
    const f = h("div.a11y-flash", { "aria-hidden": "true", data: { level: String(level) } });
    document.body.appendChild(f);
    setTimeout(() => f.remove(), 1000);
  }
  if (!words.length) return;
  const line = words.join(" · ");
  if (s.captions || s.flash) {
    if (!capEl) { capEl = h("div.a11y-caption", { "aria-hidden": "true" }); document.body.appendChild(capEl); }
    capEl.textContent = line;
    capEl.dataset.level = String(level);
    capEl.hidden = false;
    clearTimeout(capTimer);
    capTimer = setTimeout(() => { if (capEl) capEl.hidden = true; }, CAPTION_MS);
  }
  // The screen reader hears it once, in the live region, whether or not the
  // caption is drawn: a blind driver needs to know the alarm is OmaCar's.
  say(line, { urgent: level >= 3 });
}


// ---- dialogs ---------------------------------------------------------------------------
//
// Every aria-modal dialog in the app (a dozen of them, in a dozen files) gets
// the same three things here, so none can forget one:
//   - a name: the dialog is labelled by its own title, so a screen reader says
//     "Enter god mode?, dialog" rather than "dialog" (WCAG 4.1.2);
//   - Tab stays inside it while it is open (WCAG 2.4.3, APG's modal pattern);
//   - when it closes, focus goes back to whatever opened it, not to <body>.
const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), "
  + "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
const openers = new WeakMap();
let titled = 0;

export function nameDialog(box) {
  if (box.hasAttribute("aria-label") || box.hasAttribute("aria-labelledby")) return;
  const t = box.querySelector("h1, h2, h3, .title, .sheet-title, [class$='-t']");
  if (!t) return;
  if (!t.id) t.id = "dlg-title-" + (++titled);
  box.setAttribute("aria-labelledby", t.id);
}

function watchDialogs() {
  const seen = new Set();
  const sync = () => {
    const now = new Set(document.querySelectorAll("[aria-modal='true']"));
    for (const box of now) {
      if (seen.has(box)) continue;
      seen.add(box);
      const from = document.activeElement;
      if (from && from !== document.body && !box.contains(from)) openers.set(box, from);
      nameDialog(box);
    }
    for (const box of [...seen]) {
      if (now.has(box) && box.isConnected && !box.closest("[hidden]")) continue;
      seen.delete(box);
      const back = openers.get(box);
      if (back && back.isConnected && !document.querySelector("[aria-modal='true']")) {
        try { back.focus({ preventScroll: true }); } catch { /* gone */ }
      }
    }
  };
  new MutationObserver(sync).observe(document.body, {
    childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "aria-modal"] });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const boxes = [...document.querySelectorAll("[aria-modal='true']")].filter((b) => !b.closest("[hidden]"));
    const box = boxes[boxes.length - 1];
    if (!box) return;
    const items = [...box.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

export function startAccess() {
  watchDialogs();
  // Space presses a custom button, as it does a real one (ARIA's button
  // pattern). Each such element already answers Enter itself.
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.key === " " && t instanceof Element && t.matches('[role="button"]:not(button), [role="switch"]:not(button)')) {
      e.preventDefault();
      t.click();
    }
  });
  poll();
  setInterval(poll, POLL_MS);
  try {
    window.matchMedia("(prefers-reduced-motion: reduce)")
      .addEventListener("change", () => apply({ ...access, settings: { ...access.settings } }));
  } catch { /* an old engine: the CSS media queries still hold */ }
  document.addEventListener("omacar:sound", (e) => {
    const d = e.detail || {};
    showSound(wordsFor(d.cues), d.level || 0);
  });
  // The child alert and other safety layers (childalert.js) already draw
  // themselves; here they are also announced, at once.
  document.addEventListener("omacar:alert", (e) => {
    const d = e.detail || {};
    if (d.active && d.source === "child") {
      say("Child alert. Check the back seat.", { urgent: true, aloud: true, urgency: "safety" });
      showSound([], "safety");
    }
  });
}

export async function setAccess(name, value) {
  const r = await fetch(withToken("/api/access"), { method: "POST", body: JSON.stringify({ name, value }) });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || String(r.status));
  apply(body);
  return body;
}
