// The tyre banner: a strip over the stage, on every screen, while a tyre is
// losing air fast (lib/tpms.py rapid_loss: a quarter of its pressure in ten
// minutes, confirmed by two readings in a row).
//
// CALM, NOT MODAL. It takes no focus, lets every tap outside its two keys fall
// through, and says what to do in plain words. A slow leak never gets a
// banner: it is on the Tires screen, Home's callout and the owner's phone, and
// a banner for a tyre that lost a psi this week would teach the driver to look
// past banners.
//
// SOUND, ONCE, AND ONLY MOVING. A child sleeps near the tablet, so nothing
// here makes a noise in a parked car. A tyre that has lost a quarter of its
// air in ten minutes at speed is the one tyre event worth looking up from the
// road for, so then, and only then, the alert stage plays one soft chime per
// event (not per poll, not per repeat). The banner is the rest of the alert.
//
// Loaded by app.html as its own module (like radarbanner.js).

import { h, store, U } from "./core.js";
import { tpmsFeed } from "./tpmsfeed.js";
import { bannerView, shouldSound, fmtP } from "./tpmsview.js";

function start() {
  const stage = document.getElementById("stage");
  if (!stage) return;
  const cap = h("span.tb2-cap", "Tire pressure");
  const head = h("span.tb2-head");
  const sub = h("span.tb2-sub");
  const open = h("button.tb2-key", { type: "button" }, "Look");
  const dismiss = h("button.tb2-key.tb2-quiet", { type: "button" }, "Got it");
  const node = h("div.tp-banner", { role: "alert", "aria-live": "polite", hidden: true },
    h("span.tb2-lamp", { "aria-hidden": "true" }),
    h("div.tb2-id", cap, head, sub), open, dismiss);
  document.body.appendChild(node);

  const dismissed = new Set(), sounded = new Set();
  let current = null;

  function place() {
    const r = stage.getBoundingClientRect();
    const radar = document.querySelector(".rd-banner:not([hidden])");
    const below = radar ? radar.getBoundingClientRect().height + 8 : 0;
    node.style.top = `${Math.round(r.top + 8 + below)}px`;
    node.style.left = `${Math.round(r.left + 26)}px`;
    node.style.right = `${Math.round(innerWidth - r.right + 26)}px`;
  }
  if (globalThis.ResizeObserver) new ResizeObserver(place).observe(stage);
  addEventListener("resize", place);

  function paint(st) {
    const b = bannerView(st);
    const onScreen = /^#tpms(\/|$)/.test(location.hash);
    current = b;
    if (!b || dismissed.has(b.key) || onScreen) { node.hidden = true; return; }
    const p = fmtP(b.kpa, U.imperial);
    cap.textContent = b.sim ? "Tire pressure · Simulated" : "Tire pressure";
    head.textContent = b.title;
    sub.textContent = `Down ${b.pct} % in the last few minutes${b.kpa === null ? "" : ` · ${p.v} ${p.u}`}. Slow down and stop somewhere safe when you can.`;
    place();
    node.hidden = false;
    if (shouldSound(b, { moving: store.state === "driving", seen: sounded })) {
      sounded.add(b.key);
      import("./alertplayer.js").then((m) => m.alertPlayer().play([{ kind: "chime" }], { level: 2 }))
        .catch((e) => console.warn("tyre tone:", e));
    }
  }

  open.onclick = () => { location.hash = "#tpms"; };
  dismiss.onclick = () => { if (current) dismissed.add(current.key); node.hidden = true; };
  tpmsFeed.subscribe(paint);
  addEventListener("hashchange", () => paint(tpmsFeed.last));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
