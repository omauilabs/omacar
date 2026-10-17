// Vehicle -> Hybrid -> Coach: the last drive's score, what it is made of, and
// the one thing worth changing (lib/coach.py, share/js/coachview.js).
//
// A SCORE IS ONLY AS GOOD AS ITS HONESTY. Every sub-score says whether it was
// measured, estimated or simulated; one that has too little data shows "—" and
// the reason, never a zero; and the hint is not drawn at all while the car is
// moving. All of that is decided by coachView() (tested), not by this file.

import { h, icon, store, U } from "../core.js";
import { makeSpark, sparkPath } from "../spark.js";
import { coachView } from "../coachview.js";
import { readCoach, LEAF } from "../coachcard.js";

const NS = "http://www.w3.org/2000/svg";
const R = 84, C = 2 * Math.PI * R, SWEEP = 0.75;
const TW = 640, TH = 150;

const svgEl = (tag, attrs) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const CHEV_L = ["M14.5 6 8.5 12l6 6"], CHEV_R = ["M9.5 6l6 6-6 6"];

export default function coachScreen(root) {
  // ---- the hero: a ring, the number, the hint ------------------------------
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.fr-src", lamp, srcT);
  const track = svgEl("circle", { cx: 100, cy: 100, r: R, class: "co-track", fill: "none",
    "stroke-dasharray": `${C * SWEEP} ${C}`, transform: "rotate(135 100 100)" });
  const arc = svgEl("circle", { cx: 100, cy: 100, r: R, class: "co-arc", fill: "none",
    "stroke-dasharray": `0 ${C}`, transform: "rotate(135 100 100)" });
  const ring = svgEl("svg", { viewBox: "0 0 200 200", class: "co-ring", "aria-hidden": "true" });
  ring.append(track, arc);
  const big = h("span.co-big.display-num"), of = h("span.co-of", "/ 100");
  const band = h("div.co-band"), note = h("div.co-note");
  const prev = h("button.co-nav", { type: "button", "aria-label": "Earlier drive" }, icon(CHEV_L, 20));
  const next = h("button.co-nav", { type: "button", "aria-label": "Later drive" }, icon(CHEV_R, 20));
  const whenT = h("span.co-when-t"), whenK = h("span.co-when-k");
  const hintK = h("span.co-cap", "Next time");
  const hintT = h("p.co-hint-t");
  const hint = h("div.co-hint", hintK, hintT);

  const hero = h("section.co-hero",
    h("div.co-head", icon(LEAF, 20), h("h1.co-title", "Hybrid coach"), src),
    h("div.co-pick", prev, h("div.co-when", whenT, whenK), next),
    h("div.co-dial", ring, h("div.co-mid", h("div.co-num", big, of), band)),
    note, hint);

  // ---- the three sub-scores and two facts ------------------------------------
  const rows = ["regen", "smooth", "glide"].map((key) => {
    const label = h("span.co-cap"), tag = h("span.co-tag");
    const num = h("strong.co-n.display-num"), fill = h("span.co-fill");
    const what = h("p.co-what"), val = h("span.co-val"), how = h("span.co-how");
    const node = h("div.co-sub", h("div.co-sub-h", label, tag),
      h("div.co-sub-m", num, h("span.co-bar", fill)), what,
      h("div.co-sub-f", val, how));
    return { key, node, label, tag, num, fill, what, val, how };
  });
  const factRows = ["autostop", "economy"].map((key) => {
    const k = h("span.co-cap"), t = h("span.co-fact-t"), tag = h("span.co-tag");
    return { key, node: h("div.co-fact", h("div.co-sub-h", k, tag), t), k, t, tag };
  });
  const subs = h("section.co-subs", ...rows.map((r) => r.node),
    h("div.co-facts", ...factRows.map((f) => f.node)));

  // ---- the trend: the last twenty drives, filled ---------------------------------
  const spark = makeSpark(TW, TH);
  const marker = h("span.co-mark");
  const plot = h("div.co-plot", h("div.co-y", h("span", "100"), h("span", "50"), h("span", "0")),
    h("div.co-svg", spark.svg, marker));
  const trendK = h("span.co-cap", "The last 20 drives");
  const trendNote = h("span.co-trend-n");
  const trend = h("section.co-trend", h("div.co-trend-h", trendK, trendNote), plot);

  const node = h("div.co", hero, subs, trend);
  root.appendChild(node);

  let data = null, pick = null;

  function paint() {
    const v = coachView(data, { locked: !!store.lockedAsMoving, imperial: U.imperial, pick });
    node.dataset.state = v.state;
    node.dataset.tone = v.headline.tone || "none";
    srcT.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.headline.text;
    of.hidden = v.headline.n === null;
    band.textContent = v.headline.band + (v.headline.partial ? " · partial" : "");
    note.textContent = v.state === "absent" ? v.line : v.headline.note;
    arc.setAttribute("stroke-dasharray",
      `${v.headline.n === null ? 0 : (C * SWEEP * v.headline.n) / 100} ${C}`);
    whenT.textContent = v.when || "";
    whenK.textContent = [v.km, v.count ? `drive ${v.index + 1} of ${v.count}` : ""].filter(Boolean).join(" · ");
    prev.disabled = v.index <= 0;
    next.disabled = v.index < 0 || v.isLast;
    hint.hidden = !v.hint && !v.hintNote;
    hint.dataset.held = v.hint ? "" : "1";
    hintK.textContent = v.hint ? (v.isLast ? "Next time" : "On that drive") : "Hint";
    hintT.textContent = v.hint ? v.hint.text : v.hintNote;

    rows.forEach((r, i) => {
      const s = v.subs[i];
      if (!s) { r.node.hidden = true; return; }
      r.node.hidden = false;
      r.node.dataset.tone = s.tone;
      r.node.dataset.src = s.sourceKind;
      r.label.textContent = s.label;
      r.tag.textContent = s.source;
      r.num.textContent = s.score === null ? "—" : String(s.score);
      r.fill.style.width = s.score === null ? "0%" : `${Math.max(3, s.score)}%`;
      r.what.textContent = s.what;
      r.val.textContent = s.score === null ? s.why : s.value;
      r.how.textContent = s.how;
    });
    factRows.forEach((f) => {
      const x = v.facts.find((q) => q.key === f.key);
      f.node.hidden = !x;
      if (!x) return;
      f.node.dataset.have = x.have ? "1" : "";
      f.k.textContent = x.label;
      f.tag.textContent = x.source;
      f.t.textContent = x.text;
    });

    const pts = v.trend.filter((p) => p.score !== null && p.score !== undefined)
      .map((p) => [p.i, p.score]);
    spark.draw(pts.length > 1 ? sparkPath(pts, TW, TH, 0, 100) : "");
    const at = pts.find((p) => p[0] === v.index);
    if (at && pts.length > 1) {
      const x = (at[0] - pts[0][0]) / ((pts[pts.length - 1][0] - pts[0][0]) || 1);
      marker.style.left = `${(x * 100).toFixed(2)}%`;
      marker.style.top = `${(100 - at[1]).toFixed(1)}%`;
      marker.hidden = false;
    } else marker.hidden = true;
    trendNote.textContent = v.simulated ? "Simulated drives" : v.count ? `${v.count} scored` : "";
    trend.hidden = v.state === "absent";
    subs.hidden = v.state === "absent";
  }

  const step = (d) => {
    const n = (data && data.drives && data.drives.length) || 0;
    const cur = pick === null ? n - 1 : pick;
    const to = Math.max(0, Math.min(n - 1, cur + d));
    pick = to === n - 1 ? null : to;
    paint();
  };
  prev.onclick = () => step(-1);
  next.onclick = () => step(1);
  // Tap the trend to open that drive.
  plot.addEventListener("click", (e) => {
    const n = (data && data.drives && data.drives.length) || 0;
    if (n < 2) return;
    const r = plot.querySelector(".co-svg").getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (n - 1));
    pick = Math.max(0, Math.min(n - 1, i));
    if (pick === n - 1) pick = null;
    paint();
  });

  let timer = null, gone = false;
  const tick = async () => {
    try { data = await readCoach(); } catch { /* keep the last */ }
    if (!gone) { paint(); timer = setTimeout(tick, 8000); }
  };
  const off = store.on("live", paint);
  tick();
  return () => { gone = true; clearTimeout(timer); if (off) off(); };
}
