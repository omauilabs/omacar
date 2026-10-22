// The car widget kit's renderer (doc/design/2026-10-17-car-widget-kit.md).
//
// A plugin describes its content as a tree of widgets; this draws it with
// OmaCar's own car widgets in the active look. The driving rules that live in
// the page are enforced HERE, in two pure functions the tests pin down:
//
//   glance(tree, moving)   -> the tree as it may be shown right now: lists cut
//                             to 3, long text and pictures gone, at most 4
//                             widgets, nothing that needs reading while moving
//   bind(value, data)      -> "$.a.b" read from the plugin's data, or the literal
//
// Actions are never run from here directly: render() is handed a run(name)
// that posts to the server, which re-checks motion and refuses car writes
// while moving (lib/carkit.py decide_action). A button whose action is
// parked-only is drawn greyed, with the reason, while moving -- never hidden
// (the rule every OmaCar screen follows).

import { h, icon } from "./core.js";
import { ICONS } from "./icons.js";
import { sparkPath, makeSpark } from "./spark.js";

export const GLANCE_MAX = 4;
const LIST_MOVING = 3;
const TEXT_MOVING = 80;

// ---- pure --------------------------------------------------------------------------

export function bind(v, data) {
  if (typeof v !== "string" || !v.startsWith("$.")) return v;
  let cur = data;
  for (const k of v.slice(2).split(".")) {
    if (cur === null || cur === undefined || typeof cur !== "object") return undefined;
    cur = cur[k];
  }
  return cur;
}

const CONTAINERS = new Set(["row", "grid", "stack"]);

// The tree as it may be shown. `moving` false: unchanged. `moving` true: the
// glance transform -- leaves are counted in declaration order and the first
// GLANCE_MAX kept; text over 80 characters is dropped (after
// binding, so a bound note is judged by what it would say).
export function glance(tree, moving, data = {}) {
  if (!tree) return null;
  if (!moving) return tree;
  let budget = GLANCE_MAX;
  const walk = (n) => {
    if (!n || budget <= 0) return null;
    if (CONTAINERS.has(n.type)) {
      const kids = (n.children || []).map(walk).filter(Boolean);
      return kids.length ? Object.assign({}, n, { children: kids }) : null;
    }
    if (n.type === "text") {
      const s = String(bind(n.value, data) ?? "");
      if (s.length > TEXT_MOVING) return null;
    }
    budget -= 1;
    if (n.type === "list") return Object.assign({}, n, { max: LIST_MOVING });
    return n;
  };
  return walk(tree);
}

// Whether an action's key is live right now, and the words when it is not.
export function actionState(spec, name, moving) {
  const a = (spec.actions || {})[name];
  if (!a) return { ok: false, why: "Not available" };
  if (a.car_write && moving) return { ok: false, why: "Never while moving" };
  if ((a.moving || "parked") !== "allowed" && moving) return { ok: false, why: "Available when you stop" };
  return { ok: true, why: "", label: a.label || name, confirm: !!a.confirm };
}

// What a metric's value reads as: words, never a zero, for nothing.
export function shown(v) {
  if (v === null || v === undefined || v === "" || (typeof v === "number" && !Number.isFinite(v))) return "—";
  return String(v);
}

// ---- drawing -----------------------------------------------------------------------

const NS = "http://www.w3.org/2000/svg";

function sourceLamp(src) {
  const unl = src === undefined || src === null || src === "";
  return h("span.ck-src", { data: { tone: unl ? "warn" : "ok" } },
    h("span.ck-lamp", { "aria-hidden": "true" }), unl ? "Unlabelled source" : String(src));
}

function key(spec, name, label, ctx, extra = "") {
  const st = actionState(spec, name, ctx.moving);
  const b = h("button.ck-key" + extra, { type: "button", disabled: !st.ok, title: st.why || "" },
    label || st.label || name);
  b.addEventListener("click", async () => {
    if (!st.ok) return;
    if (st.confirm && !(await ctx.confirm(st.label))) return;
    b.disabled = true;
    try { await ctx.run(name); } finally { setTimeout(() => { b.disabled = false; }, 600); }
  });
  return st.ok ? b : h("div.ck-keywrap", b, h("span.ck-why", st.why));
}

const DRAW = {
  metric(n, d, spec, ctx) {
    const trail = bind(n.trail, d);
    const sp = makeSpark(120, 32);
    if (Array.isArray(trail) && trail.length > 1) {
      sp.draw(sparkPath(trail.map((v, i) => [i, Number(v)]).filter((p) => Number.isFinite(p[1])), 120, 32));
    }
    return h("div.ck-metric", { data: { tone: bind(n.tone, d) || "" } },
      h("div.ck-cap", String(bind(n.label, d) ?? "")),
      h("div.ck-val", h("span.ck-num.display-num", shown(bind(n.value, d))),
        h("span.ck-unit", String(bind(n.unit, d) ?? ""))),
      Array.isArray(trail) && trail.length > 1 ? h("div.ck-trail", sp.svg) : null,
      sourceLamp(bind(n.source, d)));
  },
  gauge(n, d) {
    const v = Number(bind(n.value, d)), lo = Number(n.min ?? 0), hi = Number(n.max ?? 100);
    const f = Number.isFinite(v) && hi > lo ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) : null;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 120 70");
    const arc = (frac, cls) => {
      const a0 = Math.PI, a1 = Math.PI - Math.PI * frac;
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", `M${60 + 50 * Math.cos(a0)} ${64 - 50 * Math.sin(a0)} A50 50 0 0 1 ${(60 + 50 * Math.cos(a1)).toFixed(2)} ${(64 - 50 * Math.sin(a1)).toFixed(2)}`);
      p.setAttribute("class", cls);
      return p;
    };
    svg.appendChild(arc(1, "ck-arc-track"));
    if (f !== null && f > 0) svg.appendChild(arc(f, "ck-arc"));
    return h("div.ck-gauge", h("div.ck-cap", String(bind(n.label, d) ?? "")), svg,
      h("div.ck-gauge-v", h("span.ck-num.display-num", shown(bind(n.value, d))), h("span.ck-unit", String(bind(n.unit, d) ?? ""))),
      sourceLamp(bind(n.source, d)));
  },
  media(n, d, spec, ctx) {
    const playing = !!bind(n.playing, d);
    const keys = [];
    if (n.prev) keys.push(key(spec, n.prev, "⏮", ctx, ".ck-round"));
    if (n.play) keys.push(key(spec, n.play, playing ? "⏸" : "▶", ctx, ".ck-round.ck-lit"));
    if (n.next) keys.push(key(spec, n.next, "⏭", ctx, ".ck-round"));
    return h("div.ck-media",
      h("div.ck-media-words",
        h("div.ck-media-t", shown(bind(n.title, d))),
        h("div.ck-media-a", String(bind(n.artist, d) ?? ""))),
      h("div.ck-media-keys", ...keys));
  },
  list(n, d, spec, ctx) {
    const items = bind(n.items, d);
    const arr = Array.isArray(items) ? items : [];
    const max = n.max || arr.length;
    const it = n.item || {};
    const rows = arr.slice(0, max).map((x) => {
      const title = shown(bind(it.title || "$.title", { $: x, ...x }) ?? x.title);
      const row = h("div.ck-li",
        h("div.ck-li-w", h("div.ck-li-t", String(x[(it.title || "$.title").slice(2)] ?? title)),
          it.sub ? h("div.ck-li-s", String(x[it.sub.slice(2)] ?? "")) : null),
        it.trailing ? h("div.ck-li-r", String(x[it.trailing.slice(2)] ?? "")) : null);
      if (!it.action) return row;
      const st = actionState(spec, it.action, ctx.moving);
      const b = h("button.ck-li-b", { type: "button", disabled: !st.ok, title: st.why }, row);
      b.addEventListener("click", () => st.ok && ctx.run(it.action, x[(it.arg || "$.id").slice(2)]));
      return b;
    });
    const more = arr.length - rows.length;
    return h("div.ck-list", ...rows, more > 0 ? h("div.ck-more", ctx.moving ? `${more} more when you stop` : `${more} more`) : null,
      arr.length ? null : h("div.ck-empty", String(n.empty || "Nothing here")));
  },
  toggle(n, d, spec, ctx) {
    const on = !!bind(n.value, d);
    const st = actionState(spec, n.action, ctx.moving);
    const b = h("button.ck-toggle", { type: "button", "aria-pressed": String(on), disabled: !st.ok, title: st.why },
      h("span.ck-cap", String(bind(n.label, d) ?? "")), h("span.ck-switch", h("span.ck-knob")));
    b.addEventListener("click", () => st.ok && ctx.run(n.action));
    return st.ok ? b : h("div.ck-keywrap", b, h("span.ck-why", st.why));
  },
  button(n, d, spec, ctx) {
    return key(spec, n.action, String(bind(n.label, d) ?? ""), ctx, n.lit ? ".ck-lit" : "");
  },
  chart(n, d) {
    const series = bind(n.series, d);
    const sp = makeSpark(300, 90);
    const pts = (Array.isArray(series) ? series : []).map((v, i) => [i, Number(v)]).filter((p) => Number.isFinite(p[1]));
    sp.draw(pts.length > 1 ? sparkPath(pts, 300, 90) : "");
    return h("div.ck-chart", h("div.ck-cap", String(bind(n.label, d) ?? "")), sp.svg, sourceLamp(bind(n.source, d)));
  },
  text(n, d) {
    return h("p.ck-text", String(bind(n.value, d) ?? "").slice(0, 280));
  },
};

export function render(tree, data, spec, ctx) {
  const t = glance(tree, ctx.moving, data);
  const draw = (n) => {
    if (!n) return null;
    if (CONTAINERS.has(n.type)) {
      return h(`div.ck-${n.type}`, { data: { cols: String(n.cols || "") } }, ...(n.children || []).map(draw).filter(Boolean));
    }
    const f = DRAW[n.type];
    return f ? f(n, data, spec, ctx) : null;
  };
  const out = draw(t) || h("div.ck-empty", "Nothing to show");
  // Rule 4, nothing new in motion: the kit's own transitions stop while moving.
  if (ctx.moving) out.classList.add("ck-moving");
  return out;
}

export function appIcon(name) {
  return icon(ICONS[name] || ICONS.data, 22);
}
