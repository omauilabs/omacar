// Home's Navigation card in the demo, as mockup 3 draws it: the next turn and
// its distance, a small map following the car, and the ETA row. Tapping it
// opens the Navigation screen.
//
//   navCard() -> { node, paint(), destroy() }    home.js's card shape
//   register(D)                                  D.cards.nav = navCard
//
// It dresses the catalogue's existing `nav` card (share/data/home-cards.json),
// which the live app draws as a Road cameras placeholder; home.js lets the
// demo dress a card, never add one.

import { h } from "../../../js/core.js";
import { bannerOf, etaOf, iconSvg, streetLine } from "../nav.js";
import { mountMap, demoOf, compassTurner, CREDIT } from "../views/navigation.js";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
const go = () => { location.hash = "#navigation"; };

export function navCard() {
  const turnIcon = h("div.dnc-icon", { "aria-hidden": "true" });
  const dist = h("div.dnc-dist");
  const what = h("div.dnc-what");
  const needle = h("span.dnc-needle", h("i"), h("b", "N"));
  const turn = compassTurner(needle);
  const mapEl = h("div.dnc-map", h("div.dnc-credit", CREDIT));
  const etaTime = h("b.dnc-time");
  const left = h("span.dnc-left");
  const wait = h("div.dnc-wait", { hidden: true }, "Waiting for the demo drive");
  const node = h("div.card.hc.hc-dnav",
    h("div.dnc-banner", turnIcon, h("div.dnc-words", dist, what), h("div.dnc-compass", needle)),
    mapEl,
    h("div.dnc-eta", h("span.dnc-k", "ETA"), etaTime, left),
    wait);
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", "Navigation");
  node.tabIndex = 0;
  node.addEventListener("click", () => { if (!node.closest(".editing")) go(); });
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });

  // About 6 m a pixel: the next few streets, as the mockup's card shows them.
  const map = mountMap(mapEl, { style: "omacar", follow: true, mpp: 6 });

  return {
    node,
    paint() {
      const demo = demoOf();
      const has = !!(demo && Number.isFinite(demo.lat));
      node.dataset.state = has ? "drive" : "waiting";
      wait.hidden = has;
      if (!has) return;
      const b = demo.next ? bannerOf(demo.next) : { icon: "arrive", distance: "", text: "Arrived", street: "" };
      if (turnIcon.dataset.icon !== b.icon) { turnIcon.innerHTML = iconSvg(b.icon); turnIcon.dataset.icon = b.icon; }
      text(dist, b.distance);
      text(what, [b.text, streetLine(b)].filter(Boolean).join(" "));
      const e = etaOf(demo);
      text(etaTime, e.time);
      text(left, `${e.distance} · ${e.remaining}`);
      turn(demo.heading);
    },
    destroy() { map.destroy(); },
  };
}

export function register(D) {
  D.cards.nav = navCard;
}
