// The arrival prompt: a calm strip across the top of the stage, on every
// screen, while the car is nearly home. The radar banner's cousin and its
// opposite: that one is urgent and holds no keys; this one is a quiet offer,
// and keys are all it is.
//
// NEVER MODAL, NEVER AUTOMATIC, NEVER A SOUND. It sits over the stage only,
// lets every tap outside its own keys fall through, and does nothing until one
// of its three keys is pressed. "Not now" puts it away for the rest of the trip.
//
// It steps down below the radar banner when both show, and it hides itself
// where it would only repeat the screen: on Home while Home has an arrival
// card.
//
// Loaded by app.html as its own module, like radarbanner.js.

import { h, U } from "./core.js";
import { arrivalFeed, arrivalView, ask } from "./arrival.js";
import { actionKeys } from "./arrivalui.js";

function start() {
  const stage = document.getElementById("stage");
  if (!stage) return;
  const cap = h("span.ab-cap", "Nearly home");
  const line = h("span.ab-line");
  const keys = actionKeys({ size: 18 });
  const x = h("button.ab-x", { type: "button" }, "Not now");
  const node = h("div.ar-banner", { role: "status", "aria-live": "polite", hidden: true },
    h("span.ab-lamp", { "aria-hidden": "true" }),
    h("div.ab-id", cap, line), keys.el, x);
  document.body.appendChild(node);
  x.onclick = () => { node.hidden = true; ask({ do: "dismiss" }).catch(() => {}); };

  let last = null;
  function place() {
    const r = stage.getBoundingClientRect();
    const radar = document.querySelector(".rd-banner:not([hidden])");
    const below = radar ? radar.getBoundingClientRect().bottom + 8 : r.top + 8;
    node.style.top = `${Math.round(below)}px`;
    node.style.left = `${Math.round(r.left + 26)}px`;
    node.style.right = `${Math.round(innerWidth - r.right + 26)}px`;
  }
  if (globalThis.ResizeObserver) new ResizeObserver(place).observe(stage);
  addEventListener("resize", place);

  function paint() {
    const v = arrivalView(last, { imperial: U.imperial });
    const id = location.hash.replace(/^#/, "").split("/")[0] || "home";
    const homeHasCard = !!document.querySelector('.home-grid [data-card="arrival"]');
    const redundant = (id === "home" || id === "hub") && homeHasCard;
    if (!v.show || redundant) { node.hidden = true; return; }
    place();
    node.hidden = false;
    cap.textContent = v.state === "arrived" ? "You're home" : "Nearly home";
    line.textContent = [v.line, v.source === "Simulated" ? "Simulated" : ""].filter(Boolean).join(" · ");
    keys.paint(v);
  }
  arrivalFeed.subscribe((st) => { last = st; paint(); });
  addEventListener("hashchange", paint);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
