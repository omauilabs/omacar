// A child left in the car: what the screen shows of lib/childalert.py.
//
// The loop decides; this draws what it decided and carries the taps back:
//   - the full-screen card, "Is your child still in the car?", with "I have my
//     child" and "Remind me in 2 min";
//   - while snoozed, a card above the dock counting down to the reminder;
//   - the silent "Child seat" lamp in the top bar's console during a drive
//     with the seat occupied;
//   - the chime.
//
// THE CHIME IS THE ONE EXCEPTION TO OMACAR'S NO-SOUND RULE. Nothing else here
// plays a sound unless a brief explicitly allows it, because the owner's child
// sleeps near the tablet at night. This brief allows it: a child possibly left
// in a parked car is the case the rule bends for. It starts only when the loop
// says so (60 s after the switch-off, unanswered, and only if the owner has
// not switched the chime off), and stops the moment anybody answers.
//
// alertView(), lampView() and seatView() are pure (test/js/childalert.test.js);
// mountChildAlert() draws them.

import { h, icon, withToken } from "./core.js";

// Lucide "baby" (ISC; see ATTRIBUTION.md). Paths only.
export const BABY = [
  "M10 16c.5.3 1.2.5 2 .5s1.5-.2 2-.5", "M15 12h.01", "M9 12h.01",
  "M19.38 6.813A9 9 0 0 1 20.8 10.2a2 2 0 0 1 0 3.6 9 9 0 0 1-17.6 0 2 2 0 0 1 0-3.6A9 9 0 0 1 12 3c2 0 3.5 1.1 3.5 2.5s-.9 2.5-2 2.5c-.8 0-1.5-.4-1.5-1",
];

export const CHIME_EVERY_MS = 20000;      // the chime again, while unanswered
export const CHIME_EVERY_HOT_MS = 10000;  // and twice as often in a hot car

export const CAMERA_WORD = {
  watching: "Watching", dark: "Too dark to see", "no-picture": "No picture",
  "no-engine": "Face detection not installed", error: "Detector failed",
  "no-reports": "Not reporting", none: "No camera chosen",
};
export const ROLE_WORD = { front: "Front camera", rear: "Rear camera", cabin: "Cabin camera" };

export function mmss(s) {
  const n = Math.max(0, Math.round(Number(s) || 0));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
}

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const deg = (c) => `${Math.round(c)}°`;

// The camera, in words. Unknown is never "empty": the sentence says the alert
// does not depend on it.
export function cameraLine(pub) {
  const c = (pub && pub.camera) || {};
  const word = CAMERA_WORD[c.state] || "Unknown";
  if (!c.role) return { k: "Camera", v: "None chosen", tone: "" };
  const ok = c.state === "watching";
  return { k: ROLE_WORD[c.role] || "Camera", v: word, tone: ok ? "ok" : "warn" };
}

// The cabin's heat, honestly: a number with what it is doing, or a plain
// "no sensor" -- never a zero for unknown.
export function heatFact(pub, t = deg) {
  if (pub.heat && typeof pub.heat.t_c === "number") {
    const sim = pub.cabin_source === "sim" ? " (simulated)" : "";
    return { k: "Cabin", v: `${t(pub.heat.t_c)} · ${pub.heat.why || (pub.heat.rising ? "rising" : "steady")}${sim}`,
             tone: pub.heat.hot ? "bad" : "" };
  }
  return { k: "Cabin", v: pub.cabin_sensor ? "No recent reading" : "No temperature sensor", tone: "" };
}

// The card. null: nothing on screen.
export function alertView(pub, t = deg) {
  if (!pub || !pub.running || !pub.alert || !["alert", "snoozed"].includes(pub.phase)) return null;
  const a = pub.alert;
  const hot = !!(pub.heat && pub.heat.hot);
  const sim = pub.source === "sim";
  if (pub.phase === "snoozed") {
    return { mode: "snoozed", sim, tone: hot ? "bad" : "warn",
             title: `Reminding you in ${mmss(a.snoozed_left)}`,
             sub: "Is your child still in the car? The tablet stays awake until you answer.",
             actions: ["have"], chime: false };
  }
  const phone = a.notified
    ? { k: "Your phone", v: "Alert queued · phone delivery not connected yet", tone: "warn" }
    : { k: "Your phone", v: `Alert queued in ${a.notify_in} s`, tone: "" };
  return {
    mode: "card", sim, tone: hot ? "bad" : "warn",
    title: "Is your child still in the car?",
    urgent: hot ? `It is ${t(pub.heat.t_c)} in the car${/fast/.test(pub.heat.why || "") ? " and rising fast"
      : pub.heat.rising ? " and rising" : ""}. Check now.` : null,
    why: `The car is off. ${cap(pub.why || "a child was seen during the drive")}.`,
    facts: [{ k: "Car off", v: mmss(a.off_for), tone: "" }, phone, heatFact(pub, t), cameraLine(pub)],
    actions: ["have", "remind"],
    noSensor: !pub.cabin_sensor,
    chime: !!pub.chime,
    chimeEvery: hot ? CHIME_EVERY_HOT_MS : CHIME_EVERY_MS,
    next: (pub.chime ? `Chiming every ${(hot ? CHIME_EVERY_HOT_MS : CHIME_EVERY_MS) / 1000} s` :
           pub.config && pub.config.chime === false ? "The chime is switched off" :
           `A chime starts at 1:00`) + " · OmaCar stays awake until you answer",
  };
}

// The top bar's lamp: only while a drive is under way with the seat occupied.
// Silent, and steady -- it is reassurance, not a request.
export function lampView(pub) {
  if (!pub || !pub.running || !pub.enabled || pub.phase !== "armed") return null;
  if (pub.car === "unknown") {
    return { text: "Occupied", tone: "warn",
             hint: "Child seat occupied, but OmaCar cannot read the charger, so it cannot tell when the car is switched off." };
  }
  return { text: "Occupied", tone: "on",
           hint: `Child seat: ${pub.why || "occupied"}. If the car is switched off with the seat occupied, OmaCar will ask.` };
}

// The Cabin → Child seat screen. `locked`: the car is moving (or handing off),
// so settings and "Car seat is empty" are greyed, never hidden.
const CAR_WORD = { on: "On", off: "Off", moving: "Moving", unknown: "Charger unreadable" };
const BY_WORD = { camera: "The camera", you: "Your tap" };

function agoWord(s) {
  if (s === null || s === undefined) return "None yet";
  if (s < 10) return "Just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

export function seatView(pub, { locked = false } = {}) {
  const cfg = (pub && pub.config) || {};
  const live = !!(pub && pub.running);
  const readouts = [
    { k: "Last face seen", v: live ? agoWord(pub.seen_ago) : "—" },
    { k: "Armed by", v: live && pub.occupied ? (BY_WORD[pub.latched_by] || "The drive") : "—" },
    { k: "Car", v: live ? (CAR_WORD[pub.car] || "Unknown") : "—" },
  ];
  const base = { locked, config: cfg, cameras: (pub && pub.cameras) || ["front", "rear", "cabin"], readouts,
                 camera: cameraLine(pub || {}), canAboard: false, canClear: false, canSet: !locked };
  if (!pub) return { ...base, state: "unknown", word: "Unknown", tone: "", source: "No answer", sourceTone: "",
                     line: "The server did not answer." };
  const sim = pub.source === "sim";
  const source = !pub.running ? "Not running" : sim ? "Simulated" : "Live";
  const sourceTone = !pub.running ? "bad" : sim ? "warn" : "ok";
  if (!pub.running) {
    return { ...base, state: "off", word: "Not watching", tone: "bad", source, sourceTone,
             line: "The child-alert service is not running, so nothing is watching the seat. " +
                   "Start it with: omacar child-alert run (omacar tablet setup enables it)." };
  }
  if (!pub.enabled) {
    return { ...base, state: "off", word: "Off", tone: "", source, sourceTone,
             line: "Switched off in the settings. Nothing is watching the seat." };
  }
  if (pub.occupied) {
    return { ...base, state: "occupied", word: "Occupied", tone: "on", source, sourceTone,
             line: `${cap(pub.why)}. Only you can clear it.`, canClear: !locked };
  }
  return { ...base, state: "clear", word: "Nothing seen", tone: "", source, sourceTone, canAboard: true,
           line: cfg.camera
             ? "No child seen since the seat was last cleared. A face on the camera, or Child aboard, arms the alert."
             : "No camera faces the seat yet. Tap Child aboard when you put a child in, and the alert is armed." };
}

// ---- drawing ----------------------------------------------------------------

export async function ask(what) {
  try {
    await fetch(withToken("/api/child-alert"), { method: "POST", cache: "no-store",
      body: JSON.stringify({ ask: what }) });
  } catch { /* the next poll shows whatever the loop did */ }
}

const LABEL = { have: "I have my child", remind: "Remind me in 2 min" };

// The chime: the alert player's chime (the same envelopes as every alert,
// rising from silence), twice in a row, so it is not mistaken for Begin's.
async function chime() {
  try {
    const { alertPlayer } = await import("./alertplayer.js");
    const p = alertPlayer();
    p.play([{ kind: "chime" }], { level: 1 });
    setTimeout(() => p.play([{ kind: "chime" }], { level: 1 }), 1400);
  } catch (e) { console.warn("child alert: the chime would not play:", e); }
}

export function mountChildAlert({
  app = document.getElementById("app"),
  bar = document.getElementById("vbar"),
  poll = 1000, playChime = chime, t = deg,
} = {}) {
  // The full-screen card.
  const title = h("h1.ca-title"), urgent = h("p.ca-urgent", { hidden: true }), why = h("p.ca-why");
  const facts = h("div.ca-facts");
  const next = h("p.ca-next");
  const acts = h("div.ca-acts");
  const sim = h("span.ca-sim", { hidden: true }, "Simulated");
  const card = h("section.ca-card", { role: "alertdialog", "aria-modal": "true", "aria-labelledby": "ca-title" },
    h("div.ca-head", h("span.ca-ico", icon(BABY, 30)), h("span.ca-kicker", "Child alert"), sim),
    h("div.ca-q", title, urgent, why), facts, acts, next);
  title.id = "ca-title";
  const layer = h("div.ca-layer", { hidden: true }, card);
  // The snoozed card, above the dock.
  const snT = h("div.ca-sn-title"), snS = h("div.ca-sn-sub");
  const snActs = h("div.ca-sn-acts");
  const snooze = h("div.ca-snooze", { hidden: true, role: "status" },
    h("span.ca-sn-lamp", { "aria-hidden": "true" }), h("div.ca-sn-words", snT, snS), snActs);
  app.append(layer, snooze);

  // The console lamp, beside the drowsy watch.
  const lampT = h("span.tb-child-t", "Occupied");
  const lamp = h("button.tb-child", { type: "button", hidden: true,
    onclick: () => { location.hash = "#child-alert"; } },
    h("span.tb-child-dot", { "aria-hidden": "true" }), lampT);
  const place = () => {
    const right = bar && bar.querySelector(".tb-right");
    if (!right) return false;
    const drowsy = right.querySelector(".tb-drowsy");
    right.insertBefore(lamp, drowsy ? drowsy.nextSibling : right.firstChild);
    return true;
  };
  let mo = null;
  if (bar && !place()) {
    mo = new MutationObserver(() => { if (place()) { mo.disconnect(); mo = null; } });
    mo.observe(bar, { childList: true, subtree: true });
  }

  let hiddenUntil = 0, shownActs = "", lastChime = 0, lastPub = null;
  const tap = (what) => {
    // Answered: off the screen now; the next polls bring it back if the loop
    // did not hear it.
    hiddenUntil = Date.now() + 3000;
    paint(lastPub);
    ask(what === "have" ? "clear" : "snooze");
  };
  const button = (a) => h("button.ca-btn" + (a === "have" ? ".ca-key" : ""), { type: "button", onclick: () => tap(a) }, LABEL[a]);

  function paint(pub) {
    lastPub = pub;
    const v = Date.now() < hiddenUntil ? null : alertView(pub, t);
    const full = !!v && v.mode === "card";
    layer.hidden = !full;
    snooze.hidden = !(v && v.mode === "snoozed");
    if (v) {
      sim.hidden = !v.sim;
      (full ? card : snooze).dataset.tone = v.tone;
    }
    if (full) {
      title.textContent = v.title;
      urgent.hidden = !v.urgent;
      urgent.textContent = v.urgent || "";
      why.textContent = v.why;
      next.textContent = v.next;
      facts.replaceChildren(...v.facts.map((f) =>
        h("div.ca-fact", { data: { tone: f.tone || "" } }, h("span.ca-fk", f.k), h("span.ca-fv", f.v))));
      const key = v.actions.join(",");
      if (key !== shownActs) { shownActs = key; acts.replaceChildren(...v.actions.map(button)); }
      if (v.chime && Date.now() - lastChime >= v.chimeEvery) { lastChime = Date.now(); playChime(); }
    } else {
      // Off the full card: the next time it shows chiming, it chimes at once.
      shownActs = "";
      lastChime = 0;
    }
    if (v && v.mode === "snoozed") {
      snT.textContent = v.title;
      snS.textContent = (v.sim ? "Simulated. " : "") + v.sub;
      if (!snActs.firstChild) snActs.append(button("have"));
    }
    const l = lampView(pub);
    lamp.hidden = !l;
    if (l) { lampT.textContent = l.text; lamp.dataset.tone = l.tone; lamp.title = l.hint; }
  }

  let timer = null;
  const tick = async () => {
    try {
      const r = await fetch(withToken("/api/child-alert"), { cache: "no-store" });
      // A server that hiccups does not take a running alert off the screen:
      // only the loop's own word does.
      if (r.ok) paint(await r.json()); else paint(lastPub);
    } catch { paint(lastPub); }
    timer = setTimeout(tick, poll);
  };
  tick();
  return { layer, snooze, lamp, paint, off() { clearTimeout(timer); if (mo) mo.disconnect(); layer.remove(); snooze.remove(); lamp.remove(); } };
}
