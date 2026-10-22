// The parking countdown: the car is off, and the tablet is about to sleep.
//
// lib/ignition.py decides; this only shows what it decided and carries the two
// taps back. It is silent on purpose -- the owner's child sleeps near the
// tablet -- and it never offers a button while the car might be moving: the
// one held state that can happen on the road ("charger lost while moving")
// says what is wrong and asks for nothing.
//
// parkingView() is pure (test/js/parking.test.js); mountParking() draws it.

import { h, withToken } from "./core.js";

// What the card says for each thing the loop can report. null: no card.
export function parkingView(pub) {
  if (!pub || !pub.running) return null;
  if (pub.phase === "counting") {
    const left = Math.max(0, Number(pub.left) || 0);
    return {
      tone: "", title: "Car is off",
      sub: `OmaCar sleeps in ${left} s to save the battery.`,
      ring: { left, grace: Math.max(1, Number(pub.grace) || 30) },
      actions: ["stay", "now"],
    };
  }
  if (pub.phase !== "held" || !pub.held) return null;
  if (pub.held === "moving") {
    return { tone: "warn", title: "Charger lost while moving",
             sub: "Staying awake. Check the charger cable and the inverter.",
             ring: null, actions: [] };
  }
  if (pub.held === "car answering") {
    return { tone: "", title: "Waiting for the car to switch off",
             sub: "Its computers are still answering, so OmaCar stays awake until they stop.",
             ring: null, actions: [] };
  }
  if (pub.held === "child alert") {
    // lib/childalert.py is holding it: a child may be in the car. Its own card
    // (childalert.js) says so in full and this one steps aside for it; this
    // is what shows if that card cannot, and it offers no Sleep now.
    return { tone: "bad", kind: "child", title: "Staying awake: child alert",
             sub: "Until you say the car seat is empty (Cabin → Child seat).",
             ring: null, actions: [] };
  }
  if (pub.held === "stay on") {
    const floor = pub.battery !== null && pub.battery !== undefined ? ` Battery ${pub.battery}%.` : "";
    return { tone: "", title: "Staying on",
             sub: `Until the car starts, or the battery runs low.${floor}`,
             ring: null, actions: ["now"] };
  }
  if (String(pub.held).startsWith("cannot sleep")) {
    return { tone: "bad", title: "OmaCar couldn't sleep",
             sub: String(pub.held).replace(/^cannot sleep:\s*/, ""),
             ring: null, actions: [] };
  }
  return null;
}

const LABEL = { stay: "Stay on", now: "Sleep now" };
const NS = "http://www.w3.org/2000/svg";
const R = 30, C = 2 * Math.PI * R;

export function mountParking({ host = document.getElementById("app"), poll = 1000 } = {}) {
  const num = h("span.pk-num.display-num");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 72 72");
  svg.setAttribute("aria-hidden", "true");
  const track = document.createElementNS(NS, "circle");
  const arc = document.createElementNS(NS, "circle");
  for (const c of [track, arc]) {
    c.setAttribute("cx", "36"); c.setAttribute("cy", "36"); c.setAttribute("r", String(R));
  }
  track.setAttribute("class", "pk-track");
  arc.setAttribute("class", "pk-arc");
  arc.setAttribute("stroke-dasharray", C.toFixed(2));
  svg.append(track, arc);
  const ring = h("div.pk-ring", svg, num);
  const title = h("div.pk-title"), sub = h("div.pk-sub");
  const acts = h("div.pk-acts");
  const card = h("div.pk-card", { role: "status", "aria-live": "polite", hidden: true },
    ring, h("div.pk-words", title, sub), acts);
  host.appendChild(card);

  let shownActs = "";
  const send = async (ask) => {
    for (const b of acts.querySelectorAll("button")) b.disabled = true;
    try {
      await fetch(withToken("/api/ignition"), { method: "POST", cache: "no-store",
        body: JSON.stringify({ ask }) });
    } catch { /* the next poll shows whatever the loop did */ }
  };

  const paint = (v) => {
    card.hidden = !v;
    if (!v) { shownActs = ""; return; }
    card.dataset.tone = v.tone;
    card.dataset.kind = v.kind || "";
    if (title.textContent !== v.title) title.textContent = v.title;
    if (sub.textContent !== v.sub) sub.textContent = v.sub;
    ring.hidden = !v.ring;
    if (v.ring) {
      num.textContent = String(v.ring.left);
      arc.style.strokeDashoffset = (C * (1 - v.ring.left / v.ring.grace)).toFixed(2);
    }
    const key = v.actions.join(",");
    if (key !== shownActs) {
      shownActs = key;
      acts.replaceChildren(...v.actions.map((a) =>
        h("button.pk-btn" + (a === "stay" ? ".pk-key" : ""), { type: "button", onclick: () => send(a) }, LABEL[a])));
    }
  };

  let timer = null;
  const tick = async () => {
    try {
      const r = await fetch(withToken("/api/ignition"), { cache: "no-store" });
      paint(r.ok ? parkingView(await r.json()) : null);
    } catch { paint(null); }
    timer = setTimeout(tick, poll);
  };
  tick();
  return { card, off() { clearTimeout(timer); card.remove(); } };
}
