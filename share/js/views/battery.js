// The tablet's own battery, full screen: Settings → Tablet battery.
//
// Health (what it holds now against what it held new), the trend with an honest
// projection, heat while charging, and whether Surface UEFI's Battery Limit
// looks active. Linux CANNOT set that limit on this tablet -- the screen says
// so and says where it is set (doc/tablet-battery.md). Nothing here writes.
//
// The numbers come from battview.js (pure, tested); lib/tabletbatt.py is the
// source. Read-only, so the moving lock changes nothing here.

import { h, icon, U, withToken } from "../core.js";
import { sparkPath, makeSpark } from "../spark.js";
import { batteryView, yRange, DAY_MS } from "../battview.js";

const NS = "http://www.w3.org/2000/svg";
const W = 600, H = 220;
const PK_LO = 30;   // the daily-peak chart runs 30-100 %: the 50 % rule sits mid-plot
const BATT = ["M4 8h13a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z", "M21 11v2", "M6 11v2"];

const fact = (k) => {
  const v = h("span.bt-fv");
  return { node: h("div.bt-fact", h("span.bt-fk", k), v), v };
};
const dateText = (ord) => new Date(ord * DAY_MS).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export default function batteryScreen(root) {
  const lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const srcT = h("span");
  const src = h("span.fr-src", lamp, srcT);
  const big = h("span.bt-big.display-num"), unit = h("span.bt-unit", "%");
  const line = h("div.bt-line"), heat = h("div.bt-heat", { role: "status" });
  const proj = h("div.bt-proj");
  const nowT = h("span"), newT = h("span");

  const spark = makeSpark(W, H);
  const yHi = h("span"), yLo = h("span"), xL = h("span"), xR = h("span");

  const lamp2 = h("span.bt-llamp", { "aria-hidden": "true" });
  const lstate = h("span.bt-lstate");
  const lwhy = h("div.bt-lwhy");

  const pk = makeSpark(W, 120);
  const rule = document.createElementNS(NS, "line");
  rule.setAttribute("class", "bt-rule");
  rule.setAttribute("x1", "0"); rule.setAttribute("x2", String(W));
  pk.svg.appendChild(rule);
  const pkEmpty = h("div.bt-empty");

  const facts = { cycles: fact("Charge cycles"), temp: fact("Battery temperature"),
                  charge: fact("Charge now"), status: fact("Status") };

  const node = h("div.bt",
    h("section.bt-hero",
      h("div.bt-head", icon(BATT, 22), h("h1.bt-name", "Tablet battery"), src),
      h("div.bt-bigrow", big, unit, h("span.bt-cap", "health")),
      line,
      h("div.bt-caps", h("div", h("span.bt-ck", "Now, when full"), nowT), h("div", h("span.bt-ck", "New"), newT)),
      heat, proj),
    h("section.bt-chart",
      h("div.bt-chart-k", "Health, day by day"),
      h("div.bt-plot", h("div.bt-y", yHi, yLo), h("div.bt-svg", spark.svg)),
      h("div.bt-x", xL, xR)),
    h("section.bt-limit",
      h("div.bt-lhead", h("span.bt-chart-k", "Battery limit"), h("span.bt-lbadge", lamp2, lstate)),
      lwhy,
      h("div.bt-how",
        h("p", "Linux cannot limit charging on this tablet. The limit lives in the Surface firmware, ",
          "and stops charging at 50 %, which is much kinder to a battery that sits hot in a car."),
        h("div.bt-steps",
          ...["Shut the tablet down. Hold Volume Up and press Power.",
              "In Surface UEFI choose Boot configuration, then Advanced Options.",
              "Turn Enable Battery Limit on. Save and exit."].map((t, i) =>
            h("div.bt-step", h("span.bt-n", String(i + 1)), h("span", t))))),
      h("div.bt-fine", "OmaCar cannot see the switch. It watches how high the charge goes while the car charges the tablet.")),
    h("section.bt-side",
      h("div.bt-facts", ...Object.values(facts).map((f) => f.node)),
      h("div.bt-chart-k", "Highest charge each day"),
      h("div.bt-plot.bt-plot-s", h("div.bt-y", h("span", "100%"), h("span", `${PK_LO}%`)), h("div.bt-svg", pk.svg, pkEmpty))));
  root.appendChild(node);

  let last = null;

  function paint() {
    const v = batteryView(last, { imperial: U.imperial });
    node.dataset.state = v.state;
    node.dataset.tone = v.healthTone || "";
    srcT.textContent = v.source || (v.state === "absent" ? "No readings" : "");
    src.dataset.tone = v.sourceTone;
    big.textContent = v.healthText;
    line.textContent = v.line;
    nowT.textContent = v.full || "—"; newT.textContent = v.design || "—";
    heat.hidden = !v.warning;
    heat.textContent = v.warning || "";
    proj.textContent = v.projection;
    proj.hidden = !v.projection;

    const ok = v.trend.length > 1;
    if (ok) {
      const [lo, hi] = yRange(v.trend);
      spark.draw(sparkPath(v.trend, W, H, lo, hi));
      yHi.textContent = `${hi}%`; yLo.textContent = `${lo}%`;
      xL.textContent = dateText(v.trend[0][0]); xR.textContent = dateText(v.trend[v.trend.length - 1][0]);
    } else {
      spark.draw(""); yHi.textContent = yLo.textContent = ""; xL.textContent = "One reading a day builds this line"; xR.textContent = "";
    }

    node.dataset.limit = v.limit.state;
    lstate.textContent = v.limit.label;
    lamp2.dataset.tone = v.limit.tone;
    lwhy.textContent = v.limit.why || (v.state === "absent" ? "" : "Judged from how high the charge goes on long charging days.");

    const pok = v.peaks.length > 1;
    pk.draw(pok ? sparkPath(v.peaks, W, 120, PK_LO, 100) : "");
    pkEmpty.textContent = pok ? "" : "Needs a few long charges.";
    if (pok) {
      const y = (120 - ((v.limitPct - PK_LO) / (100 - PK_LO)) * 120).toFixed(1);
      rule.setAttribute("y1", y); rule.setAttribute("y2", y); rule.style.display = "";
    } else rule.style.display = "none";

    facts.cycles.v.textContent = v.cycles;
    facts.temp.v.textContent = v.temp;
    facts.temp.node.dataset.hot = v.tempHot ? "1" : "";
    facts.charge.v.textContent = v.charge;
    facts.status.v.textContent = v.status || "—";
  }

  let timer = null, gone = false;
  const tick = async () => {
    try {
      const r = await fetch(withToken("/api/tabletbatt"), { cache: "no-store" });
      if (r.ok) last = await r.json();
    } catch { /* keep the last; its age shows */ }
    if (!gone) { paint(); timer = setTimeout(tick, 5000); }
  };
  tick();
  return () => { gone = true; clearTimeout(timer); };
}
