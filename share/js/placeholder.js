// A screen that is coming, saying what it will do and which step builds it.
// No sample map, no fake footage, no pretend sessions: an empty screen that
// tells the truth is better than a full one that does not.
import { h, icon } from "./core.js";

export function placeholder({ icon: ico, title, step, lines }) {
  return h("div.card.ph-card",
    h("div.ph-icon", icon(ico, 40)),
    h("div.ph-title", title),
    h("div.ph-step", step),
    ...lines.map((l) => h("p.ph-line", l)));
}
