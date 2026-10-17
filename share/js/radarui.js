// The drawn parts the three radar screens share: the eight-segment strength
// bar, the V1's three arrows, the direction compass, the mute key.
//
// Pure drawing; nothing here fetches. Colour comes only from the tone a part is
// given (data-tone -> radar.css), and the tone never stands alone: the band's
// name is written wherever a part is used, because the red-only night look has
// no hues to tell a laser from an X-band bogey.

import { h, icon } from "./core.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

export const RADAR_ICON = ["M12 3a9 9 0 1 0 9 9", "M12 12l5.2-5.2", "M12 7.2a4.8 4.8 0 1 0 4.8 4.8"];
const SPEAKER = ["M4 9.5h3.5L12 6.2v11.6l-4.5-3.3H4z"];
const WAVE = ["M15.4 9.2a4.1 4.1 0 0 1 0 5.6", "M18 7a7.4 7.4 0 0 1 0 10"];
const SLASH = ["M15.6 9.4l5 5.2", "M20.6 9.4l-5 5.2"];

// ---- strength: eight segments, growing -------------------------------------------------
export function strengthBar() {
  const root = h("div.rd-bar", { role: "meter", "aria-valuemin": "0", "aria-valuemax": "8", "aria-valuenow": "0",
                                 "aria-label": "Signal strength 0 of 8" });
  const segs = [];
  for (let i = 0; i < 8; i++) { const s = h("i"); segs.push(s); root.appendChild(s); }
  return {
    el: root,
    set(n, tone = "") {
      n = Math.max(0, Math.min(8, Math.round(n || 0)));
      root.dataset.tone = tone;
      root.dataset.n = String(n);
      root.setAttribute("aria-valuenow", String(n));
      root.setAttribute("aria-label", `Signal strength ${n} of 8`);
      segs.forEach((s, i) => { s.dataset.on = i < n ? "1" : ""; });
    },
  };
}

// ---- the V1's own three arrows: ahead, beside, behind ----------------------------------
const AR = {
  front: ["M24 2 L38 18 H29.5 V25 H18.5 V18 H10 Z"],
  left: ["M3 40 L14 30 V36 H20 V44 H14 V50 Z"],
  right: ["M45 40 L34 30 V36 H28 V44 H34 V50 Z"],
  rear: ["M24 78 L38 62 H29.5 V55 H18.5 V62 H10 Z"],
};
export function arrowGlyph(height = 64) {
  const svg = el("svg", { viewBox: "0 0 48 80", height, width: Math.round(height * 48 / 80), class: "rd-arrows",
                          role: "img", "aria-label": "No direction" });
  const parts = {};
  for (const [k, d] of Object.entries(AR)) {
    const p = el("path", { d: d[0], class: "rd-ar" });
    parts[k] = p;
    svg.appendChild(p);
  }
  return {
    el: svg,
    set(dir, tone = "") {
      svg.dataset.tone = tone;
      parts.front.dataset.on = dir === "front" ? "1" : "";
      parts.left.dataset.on = parts.right.dataset.on = dir === "side" ? "1" : "";
      parts.rear.dataset.on = dir === "rear" ? "1" : "";
      svg.setAttribute("aria-label", dir === "front" ? "Ahead" : dir === "side" ? "Beside" : dir === "rear" ? "Behind" : "No direction");
    },
  };
}

// ---- the compass: a ring round the car, four lamps on it ------------------------------
function arcPath(cx, cy, r, a0, a1) {
  const p = (a) => [cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
export function compass(size = 250) {
  const svg = el("svg", { viewBox: "0 0 240 240", width: size, height: size, class: "rd-compass", role: "img",
                          "aria-label": "Direction: no alerts" });
  svg.appendChild(el("circle", { cx: 120, cy: 120, r: 100, class: "rd-ring" }));
  svg.appendChild(el("circle", { cx: 120, cy: 120, r: 66, class: "rd-ring rd-ring-in" }));
  // The car, nose up: a body and a windscreen, drawn as furniture.
  svg.appendChild(el("rect", { x: 103, y: 84, width: 34, height: 72, rx: 13, class: "rd-car" }));
  svg.appendChild(el("path", { d: "M108 106h24l-2.5-11h-19z", class: "rd-glass" }));
  const wedge = {
    front: el("path", { d: arcPath(120, 120, 100, -128, -52), class: "rd-wedge" }),
    right: el("path", { d: arcPath(120, 120, 100, -38, 38), class: "rd-wedge" }),
    rear: el("path", { d: arcPath(120, 120, 100, 52, 128), class: "rd-wedge" }),
    left: el("path", { d: arcPath(120, 120, 100, 142, 218), class: "rd-wedge" }),
  };
  for (const w of Object.values(wedge)) svg.appendChild(w);
  const count = el("text", { x: 120, y: 142, class: "rd-count", "text-anchor": "middle" });
  count.textContent = "0";
  svg.appendChild(count);
  return {
    el: svg,
    // rows: alertRows(); each lights its own wedge, brighter with strength.
    set(rows) {
      const lit = { front: null, right: null, rear: null, left: null };
      for (const r of rows) {
        const strongest = (k) => (!lit[k] || r.strength > lit[k].strength ? r : lit[k]);
        if (r.dir === "front") lit.front = strongest("front");
        else if (r.dir === "rear") lit.rear = strongest("rear");
        else if (r.dir === "side") { lit.left = strongest("left"); lit.right = strongest("right"); }
      }
      for (const [k, w] of Object.entries(wedge)) {
        const r = lit[k];
        w.dataset.tone = r ? r.tone : "";
        w.dataset.on = r ? "1" : "";
        w.style.opacity = r ? (0.4 + 0.6 * r.strength / 8).toFixed(2) : "";
      }
      count.textContent = String(rows.length);
      count.dataset.on = rows.length ? "1" : "";
      const first = rows[0];
      svg.setAttribute("aria-label", first ? `Direction: ${first.head}, ${first.dirWord.toLowerCase()}` : "Direction: no alerts");
    },
  };
}

// ---- Mute: the one control, a lit key like the dock's --------------------------------------
export function muteKey(onTap) {
  const ico = h("span.rd-key-ico");
  const txt = h("span.rd-key-t", "Mute");
  const b = h("button.rd-key", { type: "button", "aria-pressed": "false" }, ico, txt);
  const draw = (muted) => {
    ico.replaceChildren(icon(muted ? [...SPEAKER, ...SLASH] : [...SPEAKER, ...WAVE], 20));
  };
  draw(false);
  b.addEventListener("click", (e) => { e.stopPropagation(); onTap(); });
  return {
    el: b,
    set(muted, can) {
      b.disabled = !can;
      b.dataset.on = muted ? "1" : "";
      b.setAttribute("aria-pressed", muted ? "true" : "false");
      txt.textContent = muted ? "Muted" : "Mute";
      if (b.dataset.drawn !== String(!!muted)) { draw(muted); b.dataset.drawn = String(!!muted); }
    },
  };
}

// One mute handler for all three screens: an optimistic flip that the detector
// confirms (the display data's own mute bit) or that gives way after 4 s.
export function muteController(feed, getLast, repaint) {
  let pending = null, at = 0;
  return {
    get pending() {
      if (pending !== null && Date.now() - at > 4000) pending = null;
      return pending;
    },
    settle(st) { if (pending !== null && st && !!st.muted === pending) pending = null; },
    tap() {
      const st = getLast();
      if (!st || !st.connected || st.muted === null || st.muted === undefined) return;
      const next = !(pending !== null ? pending : !!st.muted);
      pending = next; at = Date.now();
      repaint();
      feed.mute(next).then(() => feed.refresh()).catch(() => { pending = null; repaint(); });
    },
  };
}
