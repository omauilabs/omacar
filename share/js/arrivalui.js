// The parts of the arrival UI that more than one place draws: the three action
// keys (Home's card and the banner), and the pickup chooser (the Fridge screen
// and Navigation, Places). Pure wording lives in arrival.js.

import { h, icon, toast } from "./core.js";
import { ICONS } from "./icons.js";
import { SNOW } from "./fridgecard.js";
import { HOME_ICON, PIN_ICON, ask } from "./arrival.js";

const KEY_ICON = { garage: ICONS.garage, fridge: SNOW, house: HOME_ICON };

// One tap each, allowed at any speed. A key that cannot be pressed says why in
// its own caption, in place of the sub-caption: it is never just grey.
export function actionKeys({ size = 20, compact = false } = {}) {
  const els = {};
  const nodes = ["garage", "fridge", "house"].map((id) => {
    const label = h("span.ar-key-l"), sub = h("span.ar-key-s");
    const b = h("button.ar-key", { type: "button", "data-id": id },
      h("span.ar-key-ico", icon(KEY_ICON[id], size)),
      h("span.ar-key-t", label, compact ? null : sub));
    b.onclick = () => {
      if (b.disabled || b.dataset.busy) return;
      b.dataset.busy = "1";
      ask({ do: id === "fridge" ? "fridge_eco" : id })
        .then(() => { b.dataset.done = "1"; setTimeout(() => { delete b.dataset.done; }, 2200); })
        .catch((e) => toast(e.message || "That did not work", "bad"))
        .finally(() => { delete b.dataset.busy; });
    };
    els[id] = { b, label, sub };
    return b;
  });
  const el = h("div.ar-keys", ...nodes);
  return {
    el,
    paint(v) {
      for (const k of v.keys) {
        const e = els[k.id];
        e.b.disabled = !k.ok;
        e.b.title = k.ok ? "" : k.why;
        e.label.textContent = e.b.dataset.done ? "Done" : k.label;
        e.sub.textContent = k.ok ? (k.sub || "") : k.why;
        e.b.dataset.ok = k.ok ? "1" : "";
      }
    },
  };
}

// "Heading to a pickup": the saved places as one-tap keys, the status of the
// plan under them, and, when there is no GPS to give an ETA, how long it will
// take (the owner's own estimate), or "Cool now".
export function pickupPanel({ title = "Pickup" } = {}) {
  const head = h("div.pk-head", icon(PIN_ICON, 18), h("span.pk-title", title));
  const line = h("div.pk-line"), sub = h("div.pk-sub");
  const list = h("div.pk-list");
  let curName = "";
  const goTo = (name, eta_min) =>
    ask({ do: "pickup", name, eta_min }).catch((e) => toast(e.message, "bad"));
  const etas = h("div.pk-etas");
  const cancel = h("button.pk-cancel", { type: "button" }, "Stop");
  const empty = h("div.pk-empty", "Save a pickup place in Navigation, Places, then one tap here sends the fridge ahead of you.");
  const el = h("div.pk", head, line, sub, etas, list, empty, cancel);
  for (const [label, m] of [["Cool now", 0], ["In 15 min", 15], ["30 min", 30], ["1 hour", 60]]) {
    const b = h("button.pk-eta", { type: "button" }, label);
    b.onclick = () => goTo(curName, m);
    etas.appendChild(b);
  }
  cancel.onclick = () => ask({ do: "pickup_cancel" }).catch((e) => toast(e.message, "bad"));
  let shown = "";
  return {
    el,
    paint(v) {
      const pk = v.pickup;
      const active = pk && pk.state !== "done";
      line.textContent = pk ? pk.line : "Not heading to a pickup";
      sub.textContent = pk ? (pk.note || pk.sub || "") : "";
      line.dataset.state = pk ? pk.state : "none";
      cancel.hidden = !active;
      etas.hidden = !(pk && pk.needsEta);
      empty.hidden = v.pickups.length > 0;
      el.dataset.auto = pk && pk.auto ? "1" : "";
      const sig = JSON.stringify([v.pickups.map((p) => p.name), active ? pk.name : ""]);
      if (sig !== shown) {
        shown = sig;
        list.replaceChildren(...v.pickups.map((p) => {
          const b = h("button.pk-go", { type: "button" },
            h("span.pk-go-ico", icon(PIN_ICON, 18)), h("span.pk-go-t", `Heading to ${p.name}`));
          b.dataset.on = active && pk.name === p.name ? "1" : "";
          b.onclick = () => goTo(p.name);
          return b;
        }));
      }
      curName = pk ? pk.name : "";
    },
  };
}
