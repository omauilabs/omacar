// Home's arrival card (lib/arrival.py, share/js/arrival.js).
//
// Most of the time it is a quiet line: how far home is, and where that came
// from. Within 2 km and closing it becomes the thing to reach for: three keys,
// each one tap, none of them pressed for you. Under them, always, the pickup
// plan when there is one ("Cooling for your pickup at Target Marina — 37 °F by
// 16:20").
//
// Drawn INTO Home's slot when given one, like the fridge and radar cards.

import { h, icon, toast, U } from "./core.js";
import { arrivalFeed, arrivalView, ask, HOME_ICON, PIN_ICON } from "./arrival.js";
import { actionKeys, pickupPanel } from "./arrivalui.js";

export function arrivalCard(host = null) {
  const lamp = h("span.ar-lamp", { "aria-hidden": "true" });
  const src = h("span.ar-src", lamp, h("span.ar-src-t"));
  const title = h("span.ar-name", "Arrival");
  const headline = h("div.ar-headline"), line = h("div.ar-line");
  const keys = actionKeys({ size: 20 });
  const dismiss = h("button.ar-x", { type: "button", "aria-label": "Dismiss" }, "Not now");
  const pkLine = h("div.ar-pk-line"), pkSub = h("div.ar-pk-sub");
  const pkIco = h("span.ar-pk-ico", icon(PIN_ICON, 16));
  const pk = h("div.ar-pk", pkIco, h("div.ar-pk-t", pkLine, pkSub));

  // Saved pickups, one tap each, while no pickup is under way.
  const picks = h("div.ar-picks");
  let picksSig = "";

  const node = host || h("div.card.hc");
  node.classList.add("hc-arrival");
  node.append(
    h("div.ar-top", icon(HOME_ICON, 18), title, src, dismiss),
    h("div.ar-main", h("div.ar-now", headline, line), keys.el),
    pk, picks);

  let last = null;
  dismiss.onclick = () => {
    ask({ do: "dismiss" }).catch(() => {});
  };

  function paint() {
    const v = arrivalView(last, { imperial: U.imperial });
    node.dataset.state = v.state;
    node.dataset.pk = v.pickup && v.pickup.state !== "done" ? "1" : "";
    src.lastChild.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    headline.textContent = v.headline;
    line.textContent = v.line || (v.watching || v.state === "unknown" ? "" : v.watchNote);
    keys.paint(v);
    dismiss.hidden = !v.show;
    node.dataset.show = v.show ? "1" : "";
    if (v.pickup) {
      pkLine.textContent = v.pickup.line;
      pkSub.textContent = v.pickup.note || v.pickup.sub || "";
      pk.hidden = false;
      pk.dataset.state = v.pickup.state;
    } else pk.hidden = true;
    const idle = !v.pickup || v.pickup.state === "done";
    const want = idle && v.pickups.length ? v.pickups.slice(0, 2).map((p) => p.name) : [];
    const sig = want.join("|");
    if (sig !== picksSig) {
      picksSig = sig;
      picks.replaceChildren(...want.map((name) => {
        const b = h("button.pk-go", { type: "button" },
          h("span.pk-go-ico", icon(PIN_ICON, 16)), h("span.pk-go-t", `Heading to ${name}`));
        b.onclick = () => ask({ do: "pickup", name }).catch((e) => toast(e.message, "bad"));
        return b;
      }));
    }
    picks.hidden = !want.length || v.show;
  }
  const off = arrivalFeed.subscribe((st) => { last = st; paint(); });
  paint();
  return { node, paint, destroy() { off(); } };
}
