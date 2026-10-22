// Home's "parking" card: where the car was left, at a glance.
//
// The rear still small on the left, the time large, the level when there is
// one ("P3 ≈", always with the word estimate nearby), and where the answer
// came from. Tapping it opens the full screen (Navigation -> Parked).
// Drawn INTO Home's slot when given one, like the fridge and arrival cards.
//
// While the car is moving it says so and dims: the saved spot is the one from
// the last stop, and the card must not look like it is about this trip.

import { h, icon, store } from "./core.js";
import { parkFeed, parkView, PARK_ICON } from "./parkspot.js";

export function parkspotCard(host = null) {
  const lamp = h("span.ps-lamp", { "aria-hidden": "true" });
  const src = h("span.ps-src", lamp, h("span.ps-src-t"));
  const img = h("img.psc-img", { alt: "", hidden: true, draggable: "false" });
  const noimg = h("div.psc-noimg", icon(PARK_ICON, 30));
  const big = h("div.psc-big.display-num"), line = h("div.psc-line"), lv = h("div.psc-lv");
  const node = host || h("div.card.hc");
  node.classList.add("hc-parkspot");
  node.append(
    h("div.psc-head", icon(PARK_ICON, 18), h("span", "Parked"), src),
    h("div.psc-body", h("div.psc-pic", img, noimg), h("div.psc-now", big, line, lv)));

  let last = null;
  function paint() {
    const v = parkView(last);
    const t = src.lastChild;
    node.dataset.state = v.state;
    node.dataset.moving = store.lockedAsMoving ? "1" : "";
    src.dataset.tone = v.sourceTone;
    t.textContent = v.source;
    if (v.state === "none") {
      big.textContent = "—"; line.textContent = "Saved when you park"; lv.textContent = "";
      img.hidden = true; noimg.hidden = false;
      return;
    }
    const c = v.current;
    big.textContent = c.when.replace(/^Yesterday /, "Yest. ");
    line.textContent = c.fix.has ? `${c.ago} · place saved` : `${c.ago} · no GPS fix`;
    lv.textContent = c.level ? c.level.line : "";
    const still = c.stills.find((s) => s.url);
    if (still && img.getAttribute("src") !== still.url) img.src = still.url;
    img.hidden = !still; noimg.hidden = !!still;
  }
  const off = parkFeed.subscribe((st) => { last = st; paint(); });
  paint();
  return { node, paint, destroy() { off(); } };
}
