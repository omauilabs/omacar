// Cabin → Apps: every car plugin (doc/design/2026-10-17-car-widget-kit.md).
//
// Each tile is the plugin's own `card`, drawn by the kit (share/js/carkit.js)
// in the active look, with what it needs said underneath before anybody
// relies on it (rule 7). A broken car.json is a tile too, saying why -- one bad
// plugin never takes the screen down. Tapping a tile opens its screen.

import { h, store, withToken, confirmDialog } from "../core.js";
import { render, appIcon } from "../carkit.js";

const go = (id) => { location.hash = "#" + id; };

export function ctxFor(pid) {
  return {
    moving: !!store.lockedAsMoving,
    run: async (name, arg) => {
      const r = await fetch(withToken(`/api/carkit/${pid}/action/${name}`), {
        method: "POST", cache: "no-store", body: JSON.stringify(arg === undefined ? {} : { arg }) });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.error || String(r.status));
      }
    },
    confirm: (label) => confirmDialog({ title: label, body: "Run this now?", confirm: label, tone: "normal" }),
  };
}

function needsLine(n) {
  const bits = [];
  if (n.runs_code) bits.push("Runs code on this tablet");
  if (n.network) bits.push("uses the network");
  if (n.writes_car) bits.push("can write to the car (never while moving)");
  if (n.while_moving && n.while_moving.length) bits.push(`${n.while_moving.length} action${n.while_moving.length > 1 ? "s" : ""} allowed while moving`);
  return bits.join(" · ");
}

export default function apps(root) {
  const grid = h("div.ck-apps-grid");
  const empty = h("div.ck-apps-empty", { hidden: true },
    h("div.ck-apps-empty-t", "No car apps yet"),
    h("p", "An Omarchy app or plugin appears here when it ships a car.json. "
      + "`omacar carkit new my-app` starts one; doc/design/2026-10-17-car-widget-kit.md explains the format."));
  root.appendChild(h("div.ck-apps", grid, empty));

  let gone = false, timer = null;
  const tick = async () => {
    let list = [];
    try {
      const r = await fetch(withToken("/api/carkit"), { cache: "no-store" });
      list = r.ok ? (await r.json()).plugins || [] : [];
    } catch { /* keep the grid as it was */ }
    const tiles = await Promise.all(list.map(async (p) => {
      if (p.errors && p.errors.length) {
        return h("div.card.ck-tile", { data: { state: "broken" } },
          h("div.ck-tile-head", appIcon("data"), h("span.ck-tile-t", p.name), h("span.ck-tile-kind", p.kind)),
          h("div.ck-tile-err", "Not loaded: " + p.errors[0]),
          p.errors.length > 1 ? h("div.ck-tile-err", `and ${p.errors.length - 1} more`) : null);
      }
      let d = null;
      try {
        const r = await fetch(withToken(`/api/carkit/${p.id}`), { cache: "no-store" });
        d = r.ok ? await r.json() : null;
      } catch { /* drawn as unavailable below */ }
      const spec = p.spec;
      const body = d ? render(spec.card || spec.screen, d.data || {}, spec, ctxFor(p.id))
        : h("div.ck-empty", "Not answering");
      const tile = h("div.card.ck-tile", { role: "button", tabindex: "0", data: { state: d && d.error ? "stale" : "ok" } },
        h("div.ck-tile-head", appIcon(spec.icon), h("span.ck-tile-t", p.name),
          h("span.ck-tile-kind", p.kind === "omarchy" ? "Omarchy" : "OmaCar")),
        h("div.ck-tile-body", body),
        d && d.error ? h("div.ck-tile-err", d.error + (d.age !== null ? ` · last good ${Math.round(d.age)} s ago` : "")) : null,
        h("div.ck-tile-needs", needsLine(p.needs || {})));
      tile.addEventListener("click", (e) => { if (!e.target.closest("button")) go("app/" + p.id); });
      return tile;
    }));
    if (gone) return;
    grid.replaceChildren(...tiles);
    empty.hidden = list.length > 0;
    timer = setTimeout(tick, 4000);
  };
  tick();
  return () => { gone = true; clearTimeout(timer); };
}
