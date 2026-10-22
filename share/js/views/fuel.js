// Fuel: Vehicle -> Care -> Fuel.
//
// What the pump says the car gets, beside what the car says it gets. The left
// is the answer (real economy on the last tank, cost per mile, this month's
// spend); the right is the evidence: one column per fill-up, each with the
// tank it filled drawn as a gauge, and the economy it ended drawn over them as
// a filled line, with the car's own estimate dashed through it.
//
// Pure parts, and the fill-up card itself, are in fuelcard.js
// (test/js/fuel.test.js); the log is lib/fuel.py.

import { h, icon, store, toast, confirmDialog } from "../core.js";
import { PUMP, fuelView, chartModel, columnView, readFuel, postFuel, fillSheet,
         fmt1, econUnit } from "../fuelcard.js";

const W = 1000, H = 400;

const fact = (k, note) => {
  const v = h("span.fu-fv"), n = h("span.fu-fn", note || "");
  return { node: h("div.fu-fact", h("span.fu-fk", k), v, n), v, n };
};

export default function fuelScreen(root) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.fr-src", lamp, srcT);
  const big = h("span.fu-big.display-num"), unit = h("span.fu-unit");
  const line = h("div.fu-line");
  const facts = {
    cost: fact("Cost per mile"), month: fact("This month"),
    avg: fact("Average"), car: fact("Car's estimate"),
  };
  const detail = h("div.fu-detail");
  const addBtn = h("button.fu-add", { type: "button" }, icon(PUMP, 22), h("span", "Log a fill-up"));
  const lockNote = h("div.fu-lock", "Logging when you stop");
  const sheetHost = h("div.fu-modal", { hidden: true });

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const plot = h("div.fu-plot", svg);
  const cols = h("div.fu-cols");
  const legend = h("div.fu-legend");
  const empty = h("div.fu-empty");

  const node = h("div.fu",
    h("section.fu-hero",
      h("div.fu-head", icon(PUMP, 22), h("h1.fu-name", "Fuel"), src),
      h("div.fu-cap", "Real economy, last tank"),
      h("div.fu-bigrow", big, unit),
      line,
      h("div.fu-facts", ...Object.values(facts).map((f) => f.node)),
      h("div.fu-actions", addBtn, lockNote),
      detail),
    h("section.fu-chart",
      h("div.fu-chart-head", h("span.fu-chart-k", "Economy per tank"), legend),
      h("div.fu-stage", plot, empty),
      cols),
    sheetHost);
  root.appendChild(node);

  let st = null, selected = null, sheet = null;

  function closeSheet() {
    sheet = null;
    sheetHost.hidden = true;
    sheetHost.replaceChildren();
  }
  function openSheet() {
    if (!st || store.lockedAsMoving) return;
    sheet = fillSheet({ st, pending: null,
      onDone: (next) => { st = next; closeSheet(); changed(); paint(); },
      onCancel: closeSheet });
    sheetHost.replaceChildren(sheet.node);
    sheetHost.hidden = false;
  }
  sheetHost.addEventListener("click", (e) => { if (e.target === sheetHost) closeSheet(); });
  addBtn.onclick = openSheet;

  const changed = () => window.dispatchEvent(new Event("omacar:fuel-changed"));

  function paint() {
    const v = fuelView(st);
    const imp = v.imp;
    node.dataset.state = v.state;
    srcT.textContent = v.source;
    src.dataset.tone = v.sourceTone;
    big.textContent = v.econ;
    unit.textContent = v.state === "ok" ? v.unit : "";
    line.textContent = v.line;

    facts.cost.v.textContent = v.cost === "—" ? "—" : `${v.cost}${v.perUnit}`;
    facts.cost.node.firstChild.textContent = imp ? "Cost per mile" : "Cost per km";
    facts.month.v.textContent = v.month;
    facts.month.n.textContent = v.monthFills
      ? `${v.monthName} · ${v.monthFills} fill-up${v.monthFills === 1 ? "" : "s"}` + (v.monthUnpriced ? " · some unpriced" : "")
      : v.monthName || "";
    facts.avg.v.textContent = v.avg && v.avg !== "—" ? `${v.avg} ${v.unit}` : "—";
    facts.avg.n.textContent = v.tanks ? `${v.tanks} tank${v.tanks === 1 ? "" : "s"} measured` : "";
    facts.car.v.textContent = v.carEcon ? `${v.carEcon} ${v.unit}` : "—";
    facts.car.n.textContent = v.carEcon && v.carError !== null
      ? (Math.abs(v.carError) < 1 ? "matches the pump"
        : v.carError > 0 ? `${Math.abs(v.carError).toFixed(0)}% kinder than the pump`
          : `${Math.abs(v.carError).toFixed(0)}% harsher than the pump`)
      : "last tank, from the car";

    const locked = !!store.lockedAsMoving;
    addBtn.disabled = locked || !st;
    lockNote.hidden = !locked;
    if (sheet) sheet.paint();

    // ---- the chart and the columns under it
    const m = chartModel(st, 10);
    const hasMeasure = m.real.length > 0;
    plot.hidden = !m.cols.length;
    empty.hidden = !!m.cols.length;
    empty.textContent = st && st.present ? "" : "Nothing logged yet. The first fill-up is noticed on its own, or log one by hand.";
    legend.replaceChildren(
      h("span.fu-lg.real", h("i"), "Pump"),
      h("span.fu-lg.car", h("i"), "Car's estimate"));
    legend.hidden = !hasMeasure;

    plot.querySelectorAll(".fu-dot, .fu-tick, .fu-pend").forEach((n) => n.remove());
    svg.replaceChildren();
    if (m.cols.length) {
      const NS = "http://www.w3.org/2000/svg";
      const mk = (tag, attrs) => {
        const n = document.createElementNS(NS, tag);
        for (const [k, val] of Object.entries(attrs)) n.setAttribute(k, val);
        return n;
      };
      const defs = mk("defs", {});
      const grad = mk("linearGradient", { id: "fu-fade", x1: "0", y1: "0", x2: "0", y2: "1" });
      grad.append(mk("stop", { offset: "0", class: "fu-stop-top" }), mk("stop", { offset: "1", class: "fu-stop-bottom" }));
      defs.appendChild(grad);
      svg.appendChild(defs);
      const X = (x) => (x * W).toFixed(1), Y = (y) => ((1 - y) * H).toFixed(1);
      if (hasMeasure) {
        for (const t of m.ticks) {
          const y = (t - m.lo) / (m.hi - m.lo);
          svg.appendChild(mk("line", { class: "fu-rule", x1: 0, x2: W, y1: Y(y), y2: Y(y) }));
          plot.appendChild(h("span.fu-tick", { style: { bottom: `${y * 100}%` } }, fmt1(t).replace(/\.0$/, "")));
        }
        const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${X(p.x)} ${Y(p.y)}`).join("");
        if (m.car.length > 1) svg.appendChild(mk("path", { class: "fu-car", d: path(m.car) }));
        if (m.real.length > 1) {
          const d = path(m.real);
          const last = m.real[m.real.length - 1], first = m.real[0];
          svg.appendChild(mk("path", { class: "fu-area", fill: "url(#fu-fade)",
            d: `${d}L${X(last.x)} ${H}L${X(first.x)} ${H}Z` }));
          svg.appendChild(mk("path", { class: "fu-line-p", d }));
        }
        for (const p of m.car) {
          plot.appendChild(h("span.fu-dot.car", { style: { left: `${p.x * 100}%`, bottom: `${p.y * 100}%` } }));
        }
        for (const p of m.real) {
          plot.appendChild(h("span.fu-dot", { style: { left: `${p.x * 100}%`, bottom: `${p.y * 100}%` } },
            h("b", fmt1(p.v))));
        }
      }
    }
    cols.style.gridTemplateColumns = `repeat(${Math.max(m.cols.length, 1)}, minmax(0, 1fr))`;
    cols.replaceChildren(...m.cols.map((c) => {
      const cv = columnView(c, m.imp);
      const lo = cv.before ?? null, hi = cv.after ?? null;
      const tank = h("span.fu-tank", { "aria-hidden": "true" },
        lo !== null && hi !== null
          ? h("i", { style: { bottom: `${lo}%`, height: `${Math.max(2, hi - lo)}%` } }) : null);
      return h("button.fu-col", { type: "button", "data-kind": cv.kind,
        "aria-pressed": String(selected === c.id),
        onclick: () => { selected = selected === c.id ? null : c.id; paint(); } },
        tank,
        h("span.fu-when", cv.when),
        h("span.fu-vol", cv.vol),
        h("span.fu-cost", cv.cost),
        h("span.fu-kind", cv.kind === "full" ? "" : cv.kind));
    }));

    // ---- the selected fill-up, with the only destructive control on the screen
    const sel = m.cols.find((c) => c.id === selected);
    detail.hidden = !sel;
    if (sel) {
      const cv = columnView(sel, m.imp);
      const bits = [cv.when, cv.vol, cv.cost, cv.price ? `at ${cv.price}/${m.imp ? "gal" : "L"}` : null, cv.odo,
                    cv.econ ? `${cv.econ} ${econUnit(m.imp)} on that tank` : null].filter((x) => x && x !== "—");
      const rm = h("button.fu-remove", { type: "button", disabled: locked }, "Remove");
      rm.onclick = async () => {
        const yes = await confirmDialog({ title: "Remove this fill-up?",
          body: "It leaves the log, and the economy is worked out again without it.",
          confirm: "Remove" });
        if (!yes) return;
        try { st = await postFuel({ op: "remove", id: sel.id }); selected = null; changed(); paint(); }
        catch (e) { toast(String(e.message || e), "bad"); }
      };
      detail.replaceChildren(h("span.fu-detail-t", bits.join("  ·  ")), rm);
    }
  }

  let timer = null, gone = false;
  const tick = async () => {
    try { st = await readFuel(); } catch { /* keep the last; its source shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 2500); }
  };
  paint();
  tick();
  // A change made by the calm card elsewhere refreshes this screen at once.
  const refresh = () => { clearTimeout(timer); tick(); };
  window.addEventListener("omacar:fuel-changed", refresh);
  return () => { gone = true; clearTimeout(timer); window.removeEventListener("omacar:fuel-changed", refresh); };
}
