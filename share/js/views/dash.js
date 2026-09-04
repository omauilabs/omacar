// The overview: the one screen that answers "what is going on with this car".
//
// Ordered by what a person actually wants in that order — is anything wrong,
// can I drive it, will it pass a test, what is it costing me. The advisor's
// headline sits near the top when there is one, because a sentence of
// reasoning beats six tiles of numbers, and under it because it is an opinion
// and the numbers are facts.

import { h, store, api, dist, econ, econVal, econDelta, grouped, pct, since, toast,
         lifeTone, sevTone, isoDate, shortDate, U } from "../core.js";
import { sparkline } from "../charts.js";

export default function dash(root) {
  const car = store.car;
  if (!car) { root.appendChild(h("div.card", h("div.skel"))); return; }

  const faults = car.active_faults || [];
  const crit = faults.filter((f) => f.severity === "critical");
  const svc = car.service;
  const ready = car.readiness || {};
  const perf = car.perf;

  // Incomplete monitors count towards the verdict, because a car that cannot
  // be tested is a car you cannot register, and a green card carrying the words
  // "not emissions-ready" is the kind of small contradiction that makes people
  // stop trusting the rest of the screen.
  const notReady = ready.ready === false;
  const verdict = crit.length ? "bad"
    : (faults.length || notReady || (svc && svc.overdue)) ? "warn" : "ok";
  // One line, and the shortest true one. This used to be a headline followed by
  // a paragraph restating it, above four tiles restating both -- the same three
  // facts three to five times inside one viewport. The tiles keep the detail;
  // the headline is only the verdict.
  const smog = notReady ? " · not emissions-ready" : "";
  const headline = crit.length
    ? `${crit.length} fault${crit.length > 1 ? "s" : ""} need attention now`
    : faults.length
      ? `${faults.length} fault${faults.length > 1 ? "s" : ""}${smog}`
      : notReady ? `No faults${smog}` : "All clear";

  // ---- the verdict ----
  root.appendChild(h("section.card.tint-" + verdict,
    h("div.row.wrapline",
      h("div", { style: { minWidth: "0" } },
        h("div.eyebrow", "Vehicle status"),
        h("div.title", { style: { fontSize: "1.5rem", marginTop: "2px" } }, headline)),
      h("div.right.row.wrapline", { style: { gap: "8px" } },
        h("button.btn.primary", { onclick: () => { location.hash = "#scan"; } }, "Full system scan"),
        store.aiOn
          ? h("button.btn.ai", { onclick: () => { location.hash = "#advisor"; } }, "Ask the advisor")
          : null))));

  // ---- the four things that matter ----
  const tiles = h("div.grid.g4");
  tiles.appendChild(tile("Active codes", String(faults.length),
    faults.length ? faults.slice(0, 2).map((f) => f.code).join("  ") : "nothing stored",
    faults.length ? (crit.length ? "bad" : "warn") : "ok",
    () => { location.hash = "#codes"; }));

  tiles.appendChild(tile("Emissions", ready.ready ? "Ready" : "Not ready",
    ready.ready ? "all monitors complete"
      : `${ready.incomplete} monitor${ready.incomplete === 1 ? "" : "s"} incomplete`,
    ready.ready ? "ok" : "warn",
    () => { location.hash = "#health"; }));

  const failing = (car.mode06 || []).filter((m) => m.pass === false).length;
  const marginal = (car.mode06 || []).filter(
    (m) => m.pass !== false && m.headroom !== null && m.headroom > 0.85).length;
  tiles.appendChild(tile("On-board tests", failing ? `${failing} failed` : marginal ? `${marginal} marginal` : "All passing",
    failing ? "past the manufacturer limit"
      : marginal ? "within 15% of the limit" : `${(car.mode06 || []).length} results`,
    failing ? "bad" : marginal ? "warn" : "ok",
    () => { location.hash = "#health"; }));

  if (svc && svc.next) {
    tiles.appendChild(tile("Next service", `${Math.max(0, svc.next.life)}%`,
      `${svc.next.item}${svc.next.km_left !== null && svc.next.km_left !== undefined
        ? " · " + dist(svc.next.km_left) : ""}`,
      lifeTone(svc.next.life),
      () => { location.hash = "#service"; }));
  }
  root.appendChild(tiles);

  // ---- what the watchdog has seen ----
  // Above the advisor, because this is the car telling you something happened
  // and that outranks anything anybody has an opinion about.
  root.appendChild(alertStrip());

  // ---- the advisor's last word ----
  if (store.aiOn) root.appendChild(advisorStrip());

  // ---- driving ----
  if (perf) {
    const winRow = h("div.grid.g4");
    for (const [label, key] of [["Today", "day"], ["Last 7 days", "week"],
                                ["This month", "month"], ["This year", "year"]]) {
      const w = perf[key];
      if (!w) continue;
      const d = w.prev ? econDelta(w.lphk, w.prev.lphk) : null;
      winRow.appendChild(h("div.card",
        h("div.stat-tile",
          h("div.k", label),
          h("div.v", dist(w.km, false), h("small", U.units.dist)),
          h("div.n" + (d && d.tone ? "." + d.tone : ""),
            econ(w.lphk) + (d && d.text ? `   ${d.arrow}${d.text}` : "")))));
    }
    root.appendChild(h("section.sect",
      h("div.head", h("div.eyebrow", "Driving"),
        h("a.muted.right", { href: "#history", style: { textDecoration: "none" } }, "the whole log →")),
      winRow));

    // Sixty days of economy, so a car that has started drinking shows up as a
    // line before it shows up as a bill.
    const days = (perf.days || []).slice(-60);
    if (days.filter((d) => d.lphk).length > 3) {
      const cv = h("canvas");
      const card = h("div.card",
        h("div.row", h("div.eyebrow", "Economy, last 60 days"),
          h("div.muted.right", U.units.econ)),
        h("div", { style: { marginTop: "8px" } }, cv));
      root.appendChild(card);
      requestAnimationFrame(() =>
        sparkline(cv, days.map((d) => econVal(d.lphk)), { height: 54, tint: "#4FA8E8" }));
    }
  }

  const off = store.on("car", () => { /* the vehicle bar and navigation repaint; this view is
    cheap enough to leave until the user navigates back. */ });
  return off;
}

// The last day of alerts, or nothing at all. A quiet car should produce a
// quiet screen — an empty "no alerts" panel on every load teaches people that
// this area is always empty and they stop reading it.
function alertStrip() {
  const box = h("div");
  api.alerts(12).then(({ records }) => {
    const day = Date.now() / 1000 - 86400;
    const recent = (records || []).filter((r) => r.at >= day);
    if (!recent.length) return;
    const worst = recent.some((r) => (r.payload || {}).urgency === "critical") ? "bad"
      : recent.some((r) => (r.payload || {}).urgency === "normal") ? "warn" : "";
    const card = h("div.card" + (worst ? ".tint-" + worst : ""),
      h("div.row",
        h("div.eyebrow", "While you were away"),
        h("a.muted.right", { href: "#history", style: { textDecoration: "none" } },
          "the whole timeline →")));
    const list = h("div", { style: { marginTop: "10px", display: "grid", gap: "8px" } });
    for (const a of recent.slice(0, 5)) {
      const p = a.payload || {};
      list.appendChild(h("div.row.wrapline",
        h("span.dot" + (p.urgency === "critical" ? ".bad"
          : p.urgency === "normal" ? ".warn" : ".info")),
        h("span", { style: { fontWeight: "600" } }, p.title || a.label),
        h("span.muted", { style: { flex: "1", minWidth: "0" } }, p.body || ""),
        h("span.muted.right", since(Date.now() / 1000 - a.at))));
    }
    card.appendChild(list);
    box.appendChild(card);
  }).catch(() => { /* the watchdog may not be running; that is not an error */ });
  return box;
}

function tile(k, v, note, tone, onclick) {
  const card = h("div.card", { style: { cursor: onclick ? "pointer" : "default" },
                               onclick, role: onclick ? "button" : null,
                               tabindex: onclick ? "0" : null,
                               onkeydown: onclick ? (e) => { if (e.key === "Enter") onclick(); } : null },
    h("div.stat-tile",
      h("div.k", k),
      h("div.v" + (tone ? "." + tone : ""), v),
      h("div.n", note)));
  return card;
}

// The advisor's most recent headline, if it has one. Cached answers cost
// nothing to show, and a diagnosis you have already paid for should not be
// hidden behind a click.
function advisorStrip() {
  const box = h("div.card.tint-ai",
    h("div.row",
      h("span.pill.ai", "AI ADVISOR"),
      h("span.muted.right", "runs on your own Claude — no key, no fee")),
    h("div", { style: { marginTop: "10px" } }, h("div.skel", { style: { width: "70%" } })));

  api.aiHistory().then(({ records }) => {
    const last = (records || [])[0];
    const body = box.lastChild;
    body.replaceChildren();
    if (!last) {
      body.appendChild(h("p.lede",
        "Nothing asked yet. The advisor reads this car's codes, freeze frames, "
        + "Mode 06 margins, fuel trims and service history together and tells "
        + "you what is actually wrong and what to do first."));
      body.appendChild(h("div.row", { style: { marginTop: "12px" } },
        h("button.btn.ai", { onclick: () => { location.hash = "#advisor/triage"; } },
          "Diagnose this vehicle")));
      return;
    }
    body.appendChild(h("p", { style: { fontSize: ".92rem" } },
      (last.payload && last.payload.headline) || last.label));
    body.appendChild(h("div.row", { style: { marginTop: "10px" } },
      h("span.muted", since(Date.now() / 1000 - last.at)),
      h("button.btn.ai.sm.right", { onclick: () => { location.hash = "#advisor"; } }, "Open advisor")));
  }).catch(() => {
    box.lastChild.replaceChildren(h("p.muted", "Advisor history unavailable."));
  });
  return box;
}
