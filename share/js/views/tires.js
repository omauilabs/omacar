// Tires: Vehicle → Care → Tires. Four tyre sensors heard by an RTL-SDR
// (lib/tpms.py), drawn on a car from above.
//
// WHAT IS ON THIS SCREEN IS THE OWNER'S, AND ONLY THE OWNER'S. A sensor shows
// up here only after the owner has said which wheel it is on. Everything else
// that was heard (other cars' sensors, in a car park) appears in one place, the
// learn panel, and nowhere else.
//
// THE LEARN PANEL IS A SETTING: parked only (store.lockedAsMoving), greyed with
// its reason and never hidden. To find which sensor is which, let about 3 psi
// out of one tyre while it is open: that sensor's change goes negative and its
// row rises to the top.

import { h, icon, store, U } from "../core.js";
import { sparkPath, makeSpark } from "../spark.js";
import { tpmsFeed } from "../tpmsfeed.js";
import { tpmsView, learnRows, WHEELS, NAMES } from "../tpmsview.js";

const NS = "http://www.w3.org/2000/svg";
const TIRE = ["M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z", "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z",
              "M12 3.5v5", "M12 15.5v5", "M3.5 12h5", "M15.5 12h5"];
const W = 240, SH = 46;

const svg = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  n.append(...kids);
  return n;
};

// The car, from above, front at the top. Wheels carry the tone of their
// tyre; the body is a quiet raised surface so the wheels are what is lit.
function carSvg() {
  const wheel = (k, x, y) => svg("g", { class: "tr-wh", "data-wheel": k },
    svg("rect", { x, y, width: 30, height: 68, rx: 9 }),
    svg("rect", { class: "tr-tread", x: x + 8, y: y + 8, width: 14, height: 52, rx: 5 }));
  const s = svg("svg", { class: "tr-car", viewBox: "0 0 260 500", "aria-hidden": "true" },
    svg("defs", {},
      svg("linearGradient", { id: "tr-body", x1: "0", y1: "0", x2: "0", y2: "1" },
        svg("stop", { offset: "0", class: "tr-b0" }), svg("stop", { offset: "1", class: "tr-b1" })),
      svg("linearGradient", { id: "tr-glass", x1: "0", y1: "0", x2: "0", y2: "1" },
        svg("stop", { offset: "0", class: "tr-g0" }), svg("stop", { offset: "1", class: "tr-g1" }))),
    // axle lines first, so the wheels sit on them
    svg("line", { class: "tr-axle", x1: 60, y1: 128, x2: 200, y2: 128 }),
    svg("line", { class: "tr-axle", x1: 60, y1: 372, x2: 200, y2: 372 }),
    wheel("FL", 30, 94), wheel("FR", 200, 94), wheel("RL", 30, 338), wheel("RR", 200, 338),
    svg("path", { class: "tr-body", d: "M130 14c40 0 66 22 71 60l7 78c3 40 3 112 0 160l-6 78c-5 38-33 62-72 62s-67-24-72-62l-6-78c-3-48-3-120 0-160l7-78c5-38 31-60 71-60z" }),
    svg("path", { class: "tr-glass", d: "M78 172c0-12 8-30 20-38 8-5 24-8 32-8s24 3 32 8c12 8 20 26 20 38l-4 30c-12 4-30 6-48 6s-36-2-48-6z" }),
    svg("path", { class: "tr-glass", d: "M84 332c12 4 28 6 46 6s34-2 46-6l4 24c-10 10-30 16-50 16s-40-6-50-16z" }),
    svg("path", { class: "tr-roof", d: "M92 214c12 4 26 6 38 6s26-2 38-6l2 104c-12 6-26 8-40 8s-28-2-40-8z" }),
    svg("path", { class: "tr-line", d: "M104 52c8-4 18-6 26-6s18 2 26 6" }),
    svg("path", { class: "tr-hl", d: "M72 56c6-8 14-12 22-14l-2 14z" }),
    svg("path", { class: "tr-hl", d: "M188 56c-6-8-14-12-22-14l2 14z" }),
    svg("ellipse", { class: "tr-mirror", cx: 62, cy: 164, rx: 8, ry: 5 }),
    svg("ellipse", { class: "tr-mirror", cx: 198, cy: 164, rx: 8, ry: 5 }));
  return s;
}

function wheelCard(k) {
  const lamp = h("span.tr-lamp", { "aria-hidden": "true" });
  const src = h("span.tr-src", lamp, h("span.tr-src-t"));
  const num = h("span.tr-num.display-num"), unit = h("span.tr-unit");
  const word = h("span.tr-word"), vs = h("span.tr-vs");
  const temp = h("span.tr-fact-v"), batt = h("span.tr-fact-v");
  const spark = makeSpark(W, SH);
  const rule = svg("line", { class: "tr-rule", x1: 0, x2: W });
  spark.svg.appendChild(rule);
  const trend = h("span.tr-trend");
  const node = h("section.tr-w", { "data-wheel": k },
    h("div.tr-w-head", h("span.tr-cap", NAMES[k]), src),
    h("div.tr-w-main", h("div.tr-big", num, unit), h("div.tr-side", word, vs)),
    h("div.tr-facts",
      h("div.tr-fact", h("span.tr-fact-k", "Temperature"), temp),
      h("div.tr-fact", h("span.tr-fact-k", "Sensor"), batt)),
    h("div.tr-spark", spark.svg), trend);
  return { node, lamp, src, srcT: src.lastChild, num, unit, word, vs, temp, batt, spark, rule, trend };
}

export default function tiresScreen(root) {
  const cards = Object.fromEntries(WHEELS.map((k) => [k, wheelCard(k)]));
  const car = carSvg();
  const srcLamp = h("span.tr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.tr-src.tr-src-top", srcLamp, srcT);
  const note = h("div.tr-note", { role: "status" });
  const learnKey = h("button.tr-key", { type: "button" }, icon(TIRE, 20), h("span", "Learn sensors"));
  const lockNote = h("span.tr-lock");
  const placard = h("span.tr-placard");
  const panel = h("section.tr-learn", { hidden: true });
  const list = h("div.tr-rows");
  const close = h("button.tr-key.tr-key-quiet", { type: "button" }, "Done");
  const hint = h("p.tr-hint");
  const learnEmpty = h("div.tr-empty");
  panel.append(
    h("div.tr-l-head", h("h2.tr-l-title", "Learn sensors"), close),
    hint, list, learnEmpty);

  const node = h("div.tr",
    h("header.tr-head", icon(TIRE, 22), h("h1.tr-title", "Tires"), src),
    cards.FL.node, h("div.tr-carbox", car), cards.FR.node,
    cards.RL.node, cards.RR.node,
    h("footer.tr-foot", learnKey, lockNote, note, placard),
    panel);
  root.appendChild(node);

  let st = null, heard = null, open = false, baseline = {}, asked = 0;

  const bodyOf = (k) => car.querySelector(`.tr-wh[data-wheel="${k}"]`);

  function paintCard(k, v, w) {
    const c = cards[k];
    c.node.dataset.state = v.state;
    c.node.dataset.tone = v.tone;
    c.node.dataset.dim = v.dim ? "1" : "";
    c.srcT.textContent = v.source;
    c.src.dataset.tone = v.sourceTone;
    c.num.textContent = v.heard ? v.pressure : "—";
    c.unit.textContent = v.unit;
    c.word.textContent = v.assigned ? (v.heard ? v.word : "Waiting") : "Not set up";
    c.vs.textContent = v.heard ? [v.vs || "At placard", `${v.placard.v} ${v.placard.u}`].join(" · ") : v.assigned ? "" : "Learn sensors, then assign";
    c.temp.textContent = v.heard ? `${v.temp} ${v.tempUnit}` : "—";
    c.batt.textContent = v.heard ? (v.battery ? v.battery.replace("Sensor battery ", "") : "—") : "—";
    c.batt.dataset.low = v.batteryLow ? "1" : "";
    c.trend.textContent = v.heard || v.trendPoints.length ? v.trend.text + (v.trendPoints.length > 1 ? " · cold readings, 30 days" : "") : "";
    c.trend.dataset.tone = v.trend.tone;
    // The trend on a scale that always holds the placard: a flat tyre is a
    // flat line, a leak is a visible slope, and noise is not made to look like one.
    const pts = v.trendPoints;
    const pl = w && w.placard_kpa;
    if (pts.length > 1 && pl) {
      const lo = pl * 0.72, hi = pl * 1.06;
      c.spark.draw(sparkPath(pts, W, SH, lo, hi));
      const y = (SH - ((pl - lo) / (hi - lo)) * SH).toFixed(1);
      c.rule.setAttribute("y1", y); c.rule.setAttribute("y2", y);
      c.rule.style.display = "";
    } else { c.spark.draw(""); c.rule.style.display = "none"; }
    const wh = bodyOf(k);
    wh.dataset.tone = v.tone;
    wh.dataset.state = v.state;
  }

  function paint() {
    const imperial = U.imperial;
    const view = tpmsView(st, { imperial });
    node.dataset.state = view.state;
    node.dataset.sim = view.sim ? "1" : "";
    srcT.textContent = view.source || "No radio yet";
    src.dataset.tone = view.sourceTone;
    note.textContent = view.note;
    view.wheels.forEach((v, i) => paintCard(v.wheel, v, st && st.wheels ? st.wheels[i] : null));
    const pl = st && st.placard_psi ? st.placard_psi : null;
    placard.textContent = pl ? `Placard ${imperial ? pl.front + " / " + pl.rear + " psi" : (pl.front * 0.0689476).toFixed(2) + " / " + (pl.rear * 0.0689476).toFixed(2) + " bar"} front / rear · cold` : "";
    const locked = !!store.lockedAsMoving;
    learnKey.disabled = locked;
    lockNote.textContent = locked ? "Learning sensors needs the car stopped" : "";
    if (locked && open) setOpen(false);
  }

  function paintLearn() {
    const imperial = U.imperial;
    const rows = learnRows(heard && heard.heard, baseline, { imperial });
    hint.textContent = imperial
      ? "Let about 3 psi out of one tyre and watch which sensor drops: that is the one on that wheel. Then tap the wheel it is on."
      : "Let about 0.2 bar out of one tyre and watch which sensor drops: that is the one on that wheel. Then tap the wheel it is on.";
    list.textContent = "";
    for (const r of rows) {
      const wheels = h("div.tr-seg", { role: "group", "aria-label": `Wheel for ${r.id}` },
        ...WHEELS.map((k) => {
          const b = h("button", { type: "button", "aria-pressed": r.wheel === k ? "true" : "false", "aria-label": NAMES[k] }, k);
          b.onclick = async () => {
            b.disabled = true;
            try {
              await tpmsFeed.assign(r.wheel === k ? { unassign: k } : { id: r.id, wheel: k });
              tpmsFeed.refresh();
              heard = await tpmsFeed.heard();
            } catch (e) { learnEmpty.textContent = String(e.message || e); }
            paintLearn();
          };
          return b;
        }));
      list.append(h("div.tr-row", { "data-dropped": r.dropped ? "1" : "", "data-mine": r.wheel ? "1" : "" },
        h("div.tr-r-id", h("span.tr-r-idv", r.id), h("span.tr-r-model", r.model)),
        h("div.tr-r-sig", h("span.tr-r-k", "Signal"), h("span.tr-r-v", `${r.rssi} dB`)),
        h("div.tr-r-p", h("span.tr-r-k", "Pressure"), h("span.tr-r-v", `${r.pressure} ${r.unit}`)),
        h("div.tr-r-d", h("span.tr-r-k", "Change"), h("span.tr-r-v", r.delta || "—")),
        wheels));
    }
    const running = heard && heard.running;
    learnEmpty.textContent = rows.length ? "" : !heard || !heard.present
      ? "The radio has not started. Is the RTL-SDR plugged in and omacar-tpms running?"
      : running ? "Listening. Sensors in a parked car may only speak every few minutes; driving a block makes them all talk."
      : "The radio service is not running.";
  }

  async function pullHeard() {
    try { heard = await tpmsFeed.heard(); } catch { /* keep the last list */ }
    for (const r of (heard && heard.heard) || []) if (baseline[r.id] === undefined && r.kpa !== null) baseline[r.id] = r.kpa;
    if (open) paintLearn();
  }

  function setOpen(on) {
    open = on;
    panel.hidden = !on;
    node.dataset.learning = on ? "1" : "";
    if (on) { baseline = {}; heard = null; paintLearn(); pullHeard(); }
  }
  learnKey.onclick = () => { if (!store.lockedAsMoving) setOpen(true); };
  close.onclick = () => setOpen(false);

  const off = tpmsFeed.subscribe((s) => { st = s; paint(); });
  const timer = setInterval(() => {
    paint();
    if (open && Date.now() - asked > 2000) { asked = Date.now(); pullHeard(); }
  }, 1000);
  paint();
  return () => { off(); clearInterval(timer); };
}
