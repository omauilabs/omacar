// The Live section of Vehicle → Hybrid: the flow bar, then charge, voltage,
// current and temperature, each with the last minute as a line.
//
// Built once and painted on every live sample, outside the IMA view's draw():
// that view rebuilds its page from /api/ima every six seconds at most, and a
// gauge that moved only when a drive log was re-parsed would not be live.
//
// A quantity nothing on this car has been found to give is drawn dimmed, with
// no number and no line, and says which session would find it. A zero is
// never a stand-in (the rule sigtile.js keeps for every other reading).

import { h } from "./core.js";
import { READINGS } from "./readings.js";
import { makeGauge } from "./gauges.js";
import { hybridNow } from "./hybrid.js";
import { sparkPath } from "./spark.js";

const NS = "http://www.w3.org/2000/svg";
const KEEP_MS = 60000;
const WHERE = "Not found yet — the parked IMA session (tools/ima-session.sh) will look for it.";
const CELLS = [
  { key: "charge", label: "Charge", unit: "%", digits: 0 },
  { key: "volts", label: "Pack voltage", unit: "V", digits: 1 },
  { key: "amps", label: "Pack current", unit: "A", digits: 0 },
  { key: "temp", label: "Pack temperature", unit: "°C", digits: 0 },
];
const SOURCE_WORD = { measured: "measured", simulated: "simulated", estimated: "estimated" };

export function makeHybridLive() {
  const def = { ...READINGS.flow, scale: READINGS.flow.scale() };
  const bar = makeGauge("bar", def);
  const cells = CELLS.map((c) => {
    const v = h("strong.hl-v", "—");
    const src = h("span.hl-src");
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 120 28");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(NS, "path");
    svg.appendChild(path);
    const note = h("p.hl-note.muted");
    const node = h("div.hl-cell", h("span.hl-k", c.label), h("div.hl-val", v, src), svg, note);
    return { ...c, node, v, src, path, note, trail: [] };
  });
  const node = h("section.sect.hybrid-live",
    h("div.title", { style: { fontSize: "1.05rem" } }, "Live"),
    h("div.hl-flow", bar.el),
    h("div.hl-grid", ...cells.map((c) => c.node)));

  return {
    node,
    paint(car, sample, now = Date.now()) {
      const s = sample || {};
      const hy = hybridNow(s, car, now);
      node.dataset.state = s.handover ? "paused" : "live";
      const flowOut = READINGS.flow.get(s.values || {}, s, car);
      bar.update(flowOut, READINGS.flow.read(s.values || {}, s, car));
      for (const c of cells) {
        const q = hy ? hy[c.key] : null;
        const found = q && q.value !== null && q.source !== "not found yet";
        c.node.dataset.found = found ? "yes" : "no";
        if (!found) {
          c.v.textContent = "—";
          c.src.textContent = "";
          c.path.setAttribute("d", "");
          c.note.textContent = c.key === "charge" ? "Waiting for the car" : WHERE;
          c.trail.length = 0;
          continue;
        }
        c.v.textContent = `${Number(q.value).toFixed(c.digits)} ${c.unit}`;
        c.src.textContent = SOURCE_WORD[q.source] || "";
        c.note.textContent = "";
        if (!s.handover) c.trail.push([now, Number(q.value)]);
        while (c.trail.length && now - c.trail[0][0] > KEEP_MS) c.trail.shift();
        c.path.setAttribute("d", sparkPath(c.trail, 120, 28, null, null));
      }
    },
  };
}
