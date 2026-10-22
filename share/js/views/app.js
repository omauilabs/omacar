// One car plugin's screen, at #app/<id> (doc/design/2026-10-17-car-widget-kit.md).
//
// Its `screen` (or its `card`, if it declared no screen), drawn by the kit in
// the active look and filling the stage. Redrawn when its data changes and when
// the car starts or stops moving, so glance mode (share/js/carkit.js glance())
// applies the moment the wheels turn, without waiting for the next poll.

import { h, store, withToken } from "../core.js";
import { render, appIcon } from "../carkit.js";
import { ctxFor } from "./apps.js";

export default function app(root, { arg } = {}) {
  const pid = String(arg || "");
  const title = h("span.ck-app-t");
  const src = h("span.ck-app-src");
  const body = h("div.ck-app-body");
  const err = h("div.ck-tile-err", { hidden: true });
  const head = h("div.ck-app-head", h("span.ck-app-ico"), title, src);
  root.appendChild(h("div.ck-app", head, err, body));

  let last = null, lastKey = "", gone = false, timer = null;
  const paint = () => {
    if (!last) return;
    const moving = !!store.lockedAsMoving;
    const key = JSON.stringify([last.data, moving]);
    if (key === lastKey) return;
    lastKey = key;
    const spec = last.spec;
    head.firstChild.replaceChildren(appIcon(spec.icon));
    title.textContent = last.name;
    src.textContent = (last.kind === "omarchy" ? "Omarchy app" : "OmaCar plugin") + (moving ? " · glance mode" : "");
    body.replaceChildren(render(spec.screen || spec.card, last.data || {}, spec, ctxFor(pid)));
    err.hidden = !last.error;
    err.textContent = last.error ? `${last.error}${last.age !== null ? ` · last good ${Math.round(last.age)} s ago` : ""}` : "";
  };
  const tick = async () => {
    try {
      const r = await fetch(withToken(`/api/carkit/${encodeURIComponent(pid)}`), { cache: "no-store" });
      if (r.ok) last = await r.json();
      else { title.textContent = "Not found"; body.replaceChildren(h("div.ck-empty", `No car app called "${pid}"`)); }
    } catch { /* keep what is drawn */ }
    if (!gone) { paint(); timer = setTimeout(tick, Math.max(1000, ((last && last.spec.data && last.spec.data.every_s) || 3) * 1000)); }
  };
  const off = store.on ? store.on("live", paint) : null;
  tick();
  return () => { gone = true; clearTimeout(timer); if (typeof off === "function") off(); };
}
