// Home's coach card: the last drive's score and its one hint (lib/coach.py).
//
// PARKED ONLY. While the car is moving the card says "Drive in progress" and
// nothing else: no score to chase, no hint to read at speed. The decision is
// cardView()'s (coachview.js, tested), not this file's.
//
// WHERE THE NUMBER CAME FROM is on the card: "From the drive log", or
// "Simulated" in the warn tone when it is `omacar coach sim`.

import { h, icon, store, U, withToken } from "./core.js";
import { cardView } from "./coachview.js";

export const LEAF = ["M5 19c0-9 6-14 14-14 0 8-5 14-14 14z", "M5 19l8-8"];

export async function readCoach() {
  const r = await fetch(withToken("/api/coach"), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export function coachCard(host = null) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const src = h("span.fr-src", lamp, h("span.fr-src-t"));
  const big = h("span.ch-big.display-num"), of = h("span.ch-of", "/100");
  const band = h("span.ch-band"), when = h("span.ch-when");
  const hint = h("p.ch-hint"), idle = h("div.ch-idle");
  const node = host || h("div.card.hc");
  node.classList.add("hc-coach");
  node.append(
    h("div.fr-head", icon(LEAF, 18), h("span.fr-name", "Hybrid coach"), src),
    h("div.ch-body",
      h("div.ch-score", h("div.ch-num", big, of), band, when),
      hint),
    idle);

  let last = null;
  function paint() {
    const v = cardView(last, { locked: !!store.lockedAsMoving, imperial: U.imperial });
    node.dataset.state = v.state;
    node.dataset.tone = v.tone;
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.big;
    of.hidden = v.state !== "ready" || v.big === "—";
    band.textContent = v.state === "ready" ? v.line : "";
    when.textContent = v.when || "";
    hint.textContent = v.hint || "";
    hint.hidden = !v.hint;
    idle.textContent = v.state === "ready" ? "" : v.line;
    idle.hidden = v.state === "ready";
  }

  let timer = null, gone = false, wasMoving = !!store.lockedAsMoving;
  const tick = async () => {
    try { last = await readCoach(); } catch { /* keep the last */ }
    if (!gone) { paint(); timer = setTimeout(tick, 20000); }
  };
  // A drive just ended: ask again soon, the log is scored a moment after.
  const off = store.on("live", () => {
    const m = !!store.lockedAsMoving;
    if (wasMoving && !m) { clearTimeout(timer); timer = setTimeout(tick, 4000); }
    wasMoving = m;
    paint();
  });
  tick();
  return { node, paint, destroy() { gone = true; clearTimeout(timer); if (off) off(); } };
}
